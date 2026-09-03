"""Плотные векторные представления фрагментов библиотеки.

Второй канал поиска рядом с BM25 (док. 01, слой 3): лексика находит точные
термины и обозначения, плотные векторы — переформулировки («модуль вектора
ошибки» ↔ «качество модуляции»). По отдельности оба канала промахиваются на
разных запросах, поэтому в :mod:`reportgen.search` их результаты сливаются.

Модуль намеренно обходится стандартной библиотекой:

* :class:`EmbeddingClient` — обычный ``urllib`` к OpenAI-совместимому
  ``/embeddings`` локального сервера (bge-m3 под vLLM, llama.cpp, TEI);
* :class:`StubEmbedder` — детерминированные векторы из хешей стеммированных
  токенов; работает без сервера и без GPU, годится для тестов, для холодного
  старта установки и для изолированного контура;
* ``numpy`` используется, если он есть, но не является обязательным —
  импорт ленивый, с честным запасным путём на чистом Python.

Все векторы, которые модуль отдаёт наружу, L2-нормированы: так косинусная
близость сводится к скалярному произведению, а хранилище (таблица
``embeddings``) получает ровно тот формат, который обещает схема.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import socket
import time
import urllib.error
import urllib.request

from . import _http
from collections import Counter
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Protocol,
    Sequence,
    Tuple,
)

from .retrieval import tokenize

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from .store.repo import Repositories

logger = logging.getLogger("reportgen.embeddings")

__all__ = [
    "EmbeddingError",
    "advice",
    "Embedder",
    "EmbeddingClient",
    "StubEmbedder",
    "l2_normalize",
    "cosine",
    "top_cosine",
    "VectorIndex",
    "build_index",
    "normalize_rows",
    "index_embeddings",
]

# Флаг для отладки и тестов: выключает numpy и переводит вычисления на чистый
# Python. Результаты обоих путей совпадают с точностью до округления.
USE_NUMPY = True

# Ниже этого значения норму считаем нулевой: нормировать такой вектор
# бессмысленно, он остаётся нулевым.
_EPS = 1e-12

_numpy_module: Any = None
_numpy_checked = False


def _numpy() -> Any:
    """Ленивый импорт numpy. Возвращает ``None``, если пакета нет."""
    global _numpy_module, _numpy_checked
    if not USE_NUMPY:
        return None
    if not _numpy_checked:
        try:
            import numpy as _np  # noqa: PLC0415 — импорт намеренно ленивый
        except ImportError:
            _np = None
        _numpy_module = _np
        _numpy_checked = True
    return _numpy_module


class EmbeddingError(RuntimeError):
    """Ошибка получения векторов (сеть, формат ответа, пустой ответ).

    ``kind`` называет РОД беды одним словом. Без него наружу уходила только
    строка вида «сервер эмбеддингов недоступен (http://127.0.0.1:8001/v1):
    <Errno 111> Connection refused», и человек в отделе, прочитав её на
    экране библиотеки, узнавал ровно одно: что-то не работает. А беды тут
    разные, и делать при них надо разное: службу не запустили — запустить;
    служба грузит модель в видеопамять — подождать; на порту чужая служба —
    поправить настройки. Род беды и позволяет сказать это словами.
    """

    def __init__(self, message: str, *, kind: str = "other"):
        super().__init__(message)
        #: refused | timeout | http | format | other
        self.kind = kind


#: Что делать при каждом роде беды. Текст пишется для инженера отдела: он
#: сидит за той же машиной, где всё и стоит, и ему нужна команда, а не
#: диагноз. Названия скриптов — те, что лежат в scripts\windows.
def advice(kind: str, base_url: str = "", timeout: float = 0.0) -> str:
    """Одна строка: что сделать человеку, чтобы беды не стало."""
    where = base_url or "http://127.0.0.1:8001/v1"
    if kind == "refused":
        return ("служба не запущена: по адресу " + where + " никто не отвечает. "
                "Запустите scripts\\windows\\start-embed.ps1 — должно открыться "
                "свёрнутое окно llama-server. Если оно сразу закрывается, "
                "в каталоге моделей нет файла bge-m3*.gguf")
    if kind == "timeout":
        seconds = f" за {int(timeout)} с" if timeout else ""
        return ("служба не ответила" + seconds + ": скорее всего, модель ещё "
                "загружается в видеопамять. Подождите минуту и повторите; "
                "если ждать приходится всякий раз — уменьшите embed_batch "
                "в settings.json")
    if kind == "http":
        return ("служба ответила ошибкой. Если в скобках сказано про размер "
                "пачки (input is too large, batch size) — система уменьшит "
                "пачку сама, повторите построение; если ошибка осталась при "
                "одном фрагменте, сервер запущен без ключа --embeddings или "
                "по адресу " + where + " отвечает не эмбеддер, а другая "
                "служба (у эмбеддингов порт 8001, у реранкера — 8002)")
    if kind == "format":
        return ("ответ не похож на ответ сервера эмбеддингов — вероятно, на "
                "этом порту другая служба. Проверьте embed_base_url в "
                "settings.json и запустите сервер с ключом --embeddings")
    return ("посмотрите окно llama-server и файл logs\\embed.log — там "
            "написана причина")


def _kind_of(error: BaseException | None) -> str:
    """Род беды по исключению транспорта.

    Разбираем по типу, а не по тексту: тексты у Python разных версий и
    сборок разные, а «служба не запущена» и «служба думает» — это разные
    действия человека, и путать их нельзя.
    """
    if error is None:
        return "other"
    if _http.refused(error):
        return "refused"
    if isinstance(error, urllib.error.HTTPError):
        return "http"
    if isinstance(error, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return "format"
    if isinstance(error, TimeoutError):
        return "timeout"
    reason = getattr(error, "reason", None)
    if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
        return "timeout"
    return "other"


class Embedder(Protocol):
    """Минимальный интерфейс источника векторов."""

    name: str

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...

    def embed_one(self, text: str) -> List[float]:
        ...


# ------------------------------------------------------------- векторы ----

def l2_normalize(vector: Sequence[float]) -> List[float]:
    """Приводит вектор к единичной длине. Нулевой вектор остаётся нулевым."""
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if norm < _EPS:
        return values
    return [value / norm for value in values]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Косинусная близость двух векторов.

    Векторы разной длины считаются несравнимыми — близость 0.0, исключения
    не бросаем: в базе могут одновременно лежать векторы двух моделей, и
    поиск не должен от этого падать.
    """
    if len(a) != len(b) or not a:
        return 0.0
    np = _numpy()
    if np is not None:
        left = np.asarray(a, dtype="float64")
        right = np.asarray(b, dtype="float64")
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator < _EPS:
            return 0.0
        return float(np.dot(left, right) / denominator)
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(a, b):
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value
    denominator = math.sqrt(left_norm) * math.sqrt(right_norm)
    if denominator < _EPS:
        return 0.0
    return dot / denominator


class VectorIndex:
    """Матрица векторов корпуса, пригодная для настоящей библиотеки.

    Раньше матрица жила списками Python: ``List[List[float]]``. На корпусе
    отдела — 562 000 фрагментов по 1024 числа — это около восемнадцати
    гигабайт объектов, а каждый поиск делал с них ещё и копию в float64,
    то есть четыре с половиной гигабайта сверху. На такой библиотеке
    смысловой поиск не «работал медленно», он не работал вовсе.

    Здесь то же самое хранится одной матрицей float32: 2,3 ГБ на весь
    корпус, поиск — одно умножение матрицы на вектор. Строки нормируются
    один раз при загрузке, поэтому косинус — это скалярное произведение, и
    на каждый запрос не считаются заново длины полумиллиона векторов.

    Без numpy остаётся прежний путь на списках: он медленный, но при
    небольшой библиотеке разницы не видно, а ронять поиск из-за
    отсутствующей библиотеки нельзя.
    """

    def __init__(self, uids: Sequence[str], matrix: Any, dim: int = 0):
        self.uids = list(uids)
        self.matrix = matrix
        self.dim = int(dim)

    def __len__(self) -> int:
        return len(self.uids)

    def search(self, query_vec: Sequence[float], k: int = 10) -> List[Tuple[str, float]]:
        if not len(self.uids) or k <= 0 or not query_vec:
            return []
        np = _numpy()
        if np is None or not hasattr(self.matrix, "shape"):
            return top_cosine(query_vec, self.matrix, self.uids, k=k)
        if len(query_vec) != self.dim:
            # Вектор запроса другой размерности — это чужая модель. Молча
            # выдавать бессмысленные оценки нельзя.
            return []
        query = np.asarray(query_vec, dtype="float32")
        norm = float(np.linalg.norm(query))
        if norm < _EPS:
            return []
        scores = self.matrix @ (query / norm)
        count = min(int(k), scores.shape[0])
        # argpartition вместо полной сортировки: на полумиллионе строк
        # сортировка занимает больше, чем само умножение.
        top = np.argpartition(-scores, count - 1)[:count]
        top = top[np.argsort(-scores[top], kind="stable")]
        return [(self.uids[int(i)], float(scores[int(i)])) for i in top]


def build_index(uids: Sequence[str], vectors: Sequence[Sequence[float]]) -> VectorIndex:
    """Собирает индекс из готовых списков — путь для небольших корпусов."""
    np = _numpy()
    dim = len(vectors[0]) if len(vectors) else 0
    if np is None or not dim:
        return VectorIndex(uids, list(vectors), dim)
    keep = [i for i, vector in enumerate(vectors) if len(vector) == dim]
    matrix = np.asarray([vectors[i] for i in keep], dtype="float32")
    normalize_rows(matrix)
    return VectorIndex([uids[i] for i in keep], matrix, dim)


def normalize_rows(matrix: Any) -> None:
    """Делит строки на их длину — на месте, без второй матрицы в памяти."""
    np = _numpy()
    if np is None or not hasattr(matrix, "shape") or not matrix.size:
        return
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms < _EPS] = 1.0
    matrix /= norms[:, None]


def top_cosine(
    query_vec: Sequence[float],
    matrix: Sequence[Sequence[float]],
    uids: Sequence[str],
    k: int = 10,
) -> List[Tuple[str, float]]:
    """Топ-k ближайших к запросу строк матрицы.

    Возвращает пары ``(chunk_uid, косинус)``, лучшие первыми. Строки с
    размерностью, отличной от размерности запроса, пропускаются (см.
    :func:`cosine`). Порядок при равных оценках стабилен — он определяется
    исходным порядком матрицы, поэтому выдача воспроизводима (инвариант 3
    из док. 01).
    """
    if not query_vec or not len(matrix) or not len(uids) or k <= 0:
        return []
    dim = len(query_vec)
    rows = [
        (uid, vector)
        for uid, vector in zip(uids, matrix)
        if len(vector) == dim
    ]
    if not rows:
        return []

    np = _numpy()
    if np is not None:
        data = np.asarray([vector for _, vector in rows], dtype="float64")
        query = np.asarray(query_vec, dtype="float64")
        query_norm = float(np.linalg.norm(query))
        if query_norm < _EPS:
            return []
        norms = np.linalg.norm(data, axis=1)
        norms[norms < _EPS] = 1.0
        scores = (data @ query) / (norms * query_norm)
        order = np.argsort(-scores, kind="stable")[:k]
        return [(rows[int(i)][0], float(scores[int(i)])) for i in order]

    scored = [(uid, cosine(query_vec, vector)) for uid, vector in rows]
    order = sorted(range(len(scored)), key=lambda i: -scored[i][1])[:k]
    return [scored[i] for i in order]


#: Ошибки, при которых имеет смысл дробить пачку: сервер ответил, но не
#: справился. «Служба не запущена» дроблением не лечится.
_SPLIT_ON = ("http", "timeout")

#: Короче этого укорачивать фрагмент бессмысленно: от текста ничего не
#: остаётся, и вектор перестаёт что-либо значить.
MIN_TEXT_CHARS = 400

#: Отказы, после которых построение продолжают: сервер ответил, но этот
#: текст не взял. «Служба не отвечает» сюда не входит — там продолжать нечего.
_SKIPPABLE = ("http", "format")

#: Столько подряд отвергнутых фрагментов — и мы сдаёмся. Это уже не трудные
#: тексты, а сломанная служба, и перебирать из-за неё полмиллиона фрагментов
#: значит занять видеокарту на час впустую.
GIVE_UP_AFTER = 50


# ------------------------------------------------- клиент к серверу -------

@dataclass
class EmbeddingClient:
    """Клиент к OpenAI-совместимому ``POST {base_url}/embeddings``.

    Так отвечают vLLM, llama.cpp server, Ollama и text-embeddings-inference,
    поэтому смена движка — это смена ``base_url``, а не кода. Наружу контура
    ничего не уходит: запрос идёт через :mod:`reportgen._http`, где системный
    прокси отключён явно — одного локального адреса для этого мало.

    Повторные попытки с экспоненциальной паузой повторяют поведение
    :class:`reportgen.llm.OpenAICompatLLM`: локальный сервер может быть занят
    загрузкой модели в память, и первая попытка часто приходится на этот момент.
    """

    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = "bge-m3"
    api_key: str = "not-needed"
    timeout: float = 120.0
    retries: int = 3
    batch: int = 16
    dim: int = 0  # становится известной после первого успешного ответа

    def __post_init__(self) -> None:
        #: Предел пачки, нащупанный на этом сервере. Ноль — ещё не нащупан.
        self.safe_batch = 0
        #: Сколько фрагментов пришлось укоротить, чтобы сервер их принял.
        #: Число нужно назвать вслух: укороченный текст даёт худший вектор.
        self.shortened = 0

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

    # -- публичный интерфейс -----------------------------------------------

    def embed(self, texts: Sequence[str], *, batch: int | None = None) -> List[List[float]]:
        """Векторизует список текстов, разбивая его на батчи.

        Батч ограничен и по числу текстов: локальный сервер эмбеддингов
        держит все тексты батча в памяти GPU одновременно, и слишком большой
        запрос кончается OOM, а не ошибкой формата.
        """
        items = list(texts)
        if not items:
            return []
        size = max(1, int(batch or self.batch))
        if self.safe_batch:
            size = min(size, self.safe_batch)
        vectors: List[List[float]] = []
        index = 0
        while index < len(items):
            piece = items[index:index + size]
            try:
                vectors.extend(self._request(piece))
            except EmbeddingError as error:
                if len(piece) > 1 and error.kind in _SPLIT_ON:
                    # Сервер не осилил пачку целиком. Пределы у llama.cpp
                    # считаются в ТОКЕНАХ (-ub), а мы отправляем ШТУКИ, и
                    # сколько штук влезет — зависит от длины фрагментов,
                    # то есть от библиотеки. Значит, предел не угадывают в
                    # настройках, а нащупывают: делим пачку пополам и дальше
                    # держимся найденного размера. Иначе на библиотеке в
                    # полмиллиона фрагментов человек получал 500 от сервера
                    # и ни одного вектора.
                    size = max(1, len(piece) // 2)
                    self.safe_batch = size
                    continue
                if len(piece) == 1 and error.kind in _SPLIT_ON:
                    vectors.append(self._shorten_and_retry(piece[0], error))
                    index += 1
                    continue
                raise
            index += len(piece)
        if len(vectors) != len(items):
            raise EmbeddingError(
                f"сервер вернул {len(vectors)} векторов вместо {len(items)}"
            )
        return vectors

    def _shorten_and_retry(self, text: str, error: EmbeddingError) -> List[float]:
        """Один фрагмент, который сервер не принял целиком.

        Делить дальше нечего — остаётся укоротить сам текст: у модели предел
        по числу токенов, а в библиотеке отдела попадаются таблицы и листинги
        в десятки тысяч знаков. Укороченный текст даёт вектор хуже полного,
        поэтому такие фрагменты считаем и говорим о них вслух: молча выдавать
        половину текста за целый нельзя.
        """
        cut = len(text)
        while cut > MIN_TEXT_CHARS:
            cut = max(MIN_TEXT_CHARS, cut // 2)
            try:
                vectors = self._request([text[:cut]])
            except EmbeddingError:
                continue
            self.shortened += 1
            return vectors[0]
        raise error

    def embed_one(self, text: str) -> List[float]:
        vectors = self.embed([text])
        if not vectors:
            raise EmbeddingError("сервер эмбеддингов вернул пустой ответ")
        return vectors[0]

    # -- транспорт ----------------------------------------------------------

    def _request(self, texts: Sequence[str]) -> List[List[float]]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": list(texts),
            "encoding_format": "float",
        }
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/embeddings",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(max(1, self.retries)):
            try:
                with _http.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                return self._parse(body, len(texts))
            except (urllib.error.URLError, TimeoutError, OSError,
                    KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                if _http.refused(error):
                    break
                if attempt < max(1, self.retries) - 1:
                    time.sleep(2 ** attempt)
        raise EmbeddingError(
            f"сервер эмбеддингов недоступен ({self.base_url}): "
            f"{_http.explain(last_error)}",
            kind=_kind_of(last_error),
        ) from last_error

    def check(self) -> Dict[str, Any]:
        """Один короткий запрос: отвечает ли служба и чем именно.

        Нужна человеку, а не программе: он нажимает «Проверить связь» и
        хочет узнать ответ сейчас, а не через полчаса построения. Ошибку
        не поднимаем — её описание и есть ответ.
        """
        started = time.monotonic()
        try:
            vector = self.embed_one("проверка связи")
        except EmbeddingError as error:
            return {
                "ok": False,
                "error": str(error),
                "advice": advice(error.kind, self.base_url, self.timeout),
                "kind": error.kind,
                "base_url": self.base_url,
                "model": self.model,
            }
        return {
            "ok": True,
            "error": "",
            "advice": "",
            "kind": "",
            "base_url": self.base_url,
            "model": self.model,
            "dim": len(vector),
            "ms": int((time.monotonic() - started) * 1000),
        }

    def _parse(self, body: Any, expected: int) -> List[List[float]]:
        if not isinstance(body, dict):
            raise ValueError("ответ не является объектом JSON")
        data = body.get("data")
        if not isinstance(data, list) or not data:
            raise ValueError("в ответе нет поля data с векторами")
        ordered: List[Any] = list(data)
        if all(isinstance(item, dict) and "index" in item for item in ordered):
            ordered.sort(key=lambda item: int(item["index"]))
        vectors: List[List[float]] = []
        for item in ordered:
            raw = item.get("embedding") if isinstance(item, dict) else item
            if not isinstance(raw, (list, tuple)) or not raw:
                raise ValueError("в элементе ответа нет непустого поля embedding")
            vectors.append(l2_normalize([float(value) for value in raw]))
        if len(vectors) != expected:
            raise ValueError(
                f"на {expected} текстов получено {len(vectors)} векторов"
            )
        self.dim = len(vectors[0])
        return vectors


# ------------------------------------------------------------ заглушка ----

@dataclass
class StubEmbedder:
    """Детерминированные векторы без всякой модели («hashing trick»).

    Мешок стеммированных токенов (:func:`reportgen.retrieval.tokenize`)
    раскладывается по фиксированному числу измерений: каждый токен даёт свою
    координату и свой знак, оба выведены из устойчивого хеша blake2b (обычный
    ``hash()`` для строк рандомизирован между запусками и для индекса не
    годится). Вес токена сублинейный, ``1 + ln(tf)``: длинный фрагмент, где
    термин повторён двадцать раз, не должен подавлять короткий и точный.

    Что заглушка даёт и чего не даёт:

    * тексты с общими терминами получают заметно больший косинус, чем тексты
      без общих терминов, — этого хватает, чтобы отладить весь тракт поиска,
      слияние рангов и хранение векторов без сервера и GPU;
    * настоящей семантики (синонимы, переформулировки) здесь нет — за ней
      нужен :class:`EmbeddingClient` с bge-m3 или аналогом.
    """

    dim: int = 256
    seed: int = 0
    name: str = "stub-embedder"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> List[float]:
        vector = [0.0] * self.dim
        counts = Counter(tokenize(text or ""))
        for token, tf in counts.items():
            index, sign = self._slot(token)
            vector[index] += sign * (1.0 + math.log(tf))
        return l2_normalize(vector)

    def _slot(self, token: str) -> Tuple[int, float]:
        digest = hashlib.blake2b(
            f"{self.seed}:{token}".encode("utf-8"), digest_size=8
        ).digest()
        value = int.from_bytes(digest, "big")
        index = (value >> 1) % self.dim
        sign = 1.0 if value & 1 else -1.0
        return index, sign


# --------------------------------------------------------- индексация ----

def index_embeddings(
    repos: "Repositories",
    client: Embedder,
    *,
    batch: int = 32,
    only_missing: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Проставляет векторы всем чанкам библиотеки. Возвращает число записанных.

    ``only_missing=True`` — обычный режим: после добавления одного документа
    пересчитывать весь корпус не нужно, это часы работы GPU. Полный пересчёт
    (``only_missing=False``) обязателен при смене модели эмбеддингов: векторы
    разных моделей несравнимы между собой.

    ``progress(готово, всего)`` вызывается после каждого батча — веб-интерфейс
    показывает по нему полосу выполнения.
    """
    # Опознаватели, а не тексты: библиотека отдела — полмиллиона фрагментов,
    # и поднимать её в память целиком ради пачек по шестнадцать незачем.
    uids = repos.chunks.all_uids()
    if only_missing:
        uids = _missing_uids(repos, uids)
    total = len(uids)
    if progress is not None:
        progress(0, total)
    if not total:
        return 0

    model = str(getattr(client, "model", "") or getattr(client, "name", "embedder"))
    size = max(1, int(batch))
    written = 0
    skipped: List[str] = []
    in_a_row = 0
    for start in range(0, total, size):
        piece = uids[start:start + size]
        texts = {chunk.chunk_id: chunk.indexed_text
                 for chunk in repos.chunks.get_many(piece)}
        piece = [uid for uid in piece if uid in texts]
        if not piece:
            continue
        try:
            vectors = client.embed([texts[uid] for uid in piece])
        except EmbeddingError as error:
            # Отличаем беду СЛУЖБЫ от беды ФРАГМЕНТА. Служба не запущена или
            # не отвечает — построение обречено целиком, и делать вид, что
            # мы просто «пропускаем фрагменты», нечестно: человек должен
            # увидеть, что смысловой поиск не работает. А вот отказ на
            # конкретном тексте (сервер ответил, но этот не взял) ронять
            # построение библиотеки не должен: остальные полмиллиона
            # фрагментов ни в чём не виноваты.
            if getattr(error, "kind", "other") not in _SKIPPABLE:
                raise
            before = len(skipped)
            for uid in piece:
                written += index_embeddings_one(repos, client, model, uid,
                                                texts[uid], skipped)
                if progress is not None:
                    progress(written + len(skipped), total)
            if len(skipped) - before == len(piece):
                # Не пошёл ни один фрагмент пачки. Это уже не «трудный
                # текст», а сломанная служба: считаем подряд идущие неудачи
                # и сдаёмся, не перебирая впустую полмиллиона фрагментов.
                in_a_row += len(piece)
                if in_a_row >= GIVE_UP_AFTER:
                    raise EmbeddingError(
                        f"сервер эмбеддингов отверг подряд {in_a_row} "
                        f"фрагментов — построение остановлено: {error}",
                        kind=getattr(error, "kind", "other")) from error
            else:
                in_a_row = 0
            continue
        in_a_row = 0
        if len(vectors) != len(piece):
            raise EmbeddingError(
                f"на {len(piece)} фрагментов получено {len(vectors)} векторов"
            )
        repos.vectors.put_many(model, dict(zip(piece, vectors)))
        written += len(piece)
        if progress is not None:
            progress(written + len(skipped), total)
    if skipped:
        _log_skipped(skipped)
    return written


def index_embeddings_one(repos: "Repositories", client: Embedder, model: str,
                         uid: str, text: str, skipped: List[str]) -> int:
    """Один фрагмент: записан — 1, не принят сервером — 0 и запись в список."""
    try:
        vectors = client.embed([text])
    except EmbeddingError:
        skipped.append(uid)
        return 0
    if not vectors:
        skipped.append(uid)
        return 0
    repos.vectors.put_many(model, {uid: vectors[0]})
    return 1


def _log_skipped(skipped: Sequence[str]) -> None:
    """Пропущенные фрагменты — в журнал, с примерами, а не одним числом."""
    examples = ", ".join(skipped[:5])
    logger.warning(
        "сервер эмбеддингов не принял %d фрагментов — они останутся без "
        "векторов и будут находиться только словами. Например: %s",
        len(skipped), examples)


def _missing_uids(repos: "Repositories", uids: Sequence[str], slice_size: int = 400) -> List[str]:
    """Чанки без вектора. Запрос режется на части: у SQLite лимит на число ?."""
    missing: List[str] = []
    for start in range(0, len(uids), slice_size):
        missing.extend(repos.vectors.missing(uids[start:start + slice_size]))
    return missing
