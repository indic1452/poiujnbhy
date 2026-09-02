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
import math
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

__all__ = [
    "EmbeddingError",
    "Embedder",
    "EmbeddingClient",
    "StubEmbedder",
    "l2_normalize",
    "cosine",
    "top_cosine",
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
    """Ошибка получения векторов (сеть, формат ответа, пустой ответ)."""


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
        vectors: List[List[float]] = []
        for start in range(0, len(items), size):
            piece = items[start:start + size]
            vectors.extend(self._request(piece))
        if len(vectors) != len(items):
            raise EmbeddingError(
                f"сервер вернул {len(vectors)} векторов вместо {len(items)}"
            )
        return vectors

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
            f"сервер эмбеддингов недоступен ({self.base_url}): {last_error}"
        ) from last_error

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
    chunks = repos.chunks.all_chunks()
    by_uid = {chunk.chunk_id: chunk for chunk in chunks}
    uids = list(by_uid)
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
    for start in range(0, total, size):
        piece = uids[start:start + size]
        vectors = client.embed([by_uid[uid].indexed_text for uid in piece])
        if len(vectors) != len(piece):
            raise EmbeddingError(
                f"на {len(piece)} фрагментов получено {len(vectors)} векторов"
            )
        repos.vectors.put_many(model, dict(zip(piece, vectors)))
        written += len(piece)
        if progress is not None:
            progress(written, total)
    return written


def _missing_uids(repos: "Repositories", uids: Sequence[str], slice_size: int = 400) -> List[str]:
    """Чанки без вектора. Запрос режется на части: у SQLite лимит на число ?."""
    missing: List[str] = []
    for start in range(0, len(uids), slice_size):
        missing.extend(repos.vectors.missing(uids[start:start + slice_size]))
    return missing
