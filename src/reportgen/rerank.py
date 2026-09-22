"""Реранкеры: второй, дорогой проход по коротком списку кандидатов.

Первый проход (BM25 + плотные векторы) обязан быть быстрым и работает с
запросом и фрагментом по отдельности. Реранкер видит пару «запрос —
фрагмент» целиком и потому заметно точнее; платой за это является время,
поэтому его пускают только на верхушку выдачи (20–50 фрагментов).

Три реализации под три ситуации установки:

* :class:`CrossEncoderReranker` — отдельный сервис ``/rerank``
  (bge-reranker-v2-m3 под TEI, Infinity, vLLM). Лучшее качество;
* :class:`LLMReranker` — оценка той же чат-моделью, что пишет отчёт.
  Медленно, но не требует второй модели в памяти GPU;
* :class:`NoopReranker` — заглушка, сохраняющая порядок первого прохода.

Контракт у всех один: :meth:`score` возвращает по числу на каждый фрагмент,
больше — релевантнее. Абсолютная шкала не важна и между реализациями не
совпадает: наверх слой поиска берёт только порядок.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import _http
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Sequence

from .llm import LLM

__all__ = [
    "RerankError",
    "Reranker",
    "CrossEncoderReranker",
    "LLMReranker",
    "NoopReranker",
    "build_reranker",
]


class RerankError(RuntimeError):
    """Ошибка обращения к реранкеру."""


class Reranker(Protocol):
    """Интерфейс реранкера: оценка релевантности фрагментов запросу."""

    name: str

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        ...


class _TooLarge(RuntimeError):
    """Запрос не влез в сервер. Лечится не повтором, а меньшей порцией."""


#: Короче этого резать фрагмент бессмысленно: по обрывку в полторы строки
#: кросс-энкодер уже не отличит нужное от похожего, и второй проход теряет
#: смысл. Дошли до этой границы — честно отказываемся и говорим про -ub.
MIN_RERANK_CHARS = 240

#: Коды, при которых повтор осмыслен: сервер жив, но занят или перезапускается.
#: Всё остальное (400-е, 500) — отказ по существу: тот же запрос пройдёт так же.
RETRY_CODES = frozenset({408, 429, 502, 503, 504})

#: По чему опознаём тесноту. Слова взяты из ответов llama.cpp, TEI и vLLM.
_TIGHT_WORDS = ("too large", "too long", "exceed", "batch size",
                "context", "maximum context length", "out of memory")


def _стоит_повторить(error: BaseException) -> bool:
    """Есть ли смысл повторять запрос. Без кода — считаем, что сеть моргнула."""
    код = getattr(error, "code", None)
    if код is None:
        return True
    return int(код) in RETRY_CODES


def _тесно(error: BaseException, пояснение: str) -> bool:
    """Сервер отказал из-за размера запроса, а не по другой причине.

    Опознаём двумя способами сразу: по коду 413 и по словам сервера. На
    413 полагаться нельзя — llama.cpp отвечает на тесноту обычной 500,
    а на слова нельзя полагаться тем более: у каждой реализации свои.
    """
    код = getattr(error, "code", None)
    if код is not None and int(код) == 413:
        return True
    if код is not None and int(код) == 500:
        низ = пояснение.lower()
        return any(слово in низ for слово in _TIGHT_WORDS)
    return False


def _descending(count: int) -> List[float]:
    """Оценки, сохраняющие исходный порядок фрагментов."""
    return [float(count - index) for index in range(count)]


# ------------------------------------------------------- кросс-энкодер ----

@dataclass
class CrossEncoderReranker:
    """Клиент к сервису ``POST {base_url}/rerank`` (формат jina/bge/cohere).

    Запрос: ``{"model": ..., "query": ..., "documents": [...]}``.
    Ответ: ``{"results": [{"index": 0, "relevance_score": 0.87}, ...]}``.

    Разбор намеренно снисходителен: реализации расходятся в мелочах — поле
    называют ``relevance_score`` или ``score``, список кладут в ``results``
    или в ``data``, ``index`` иногда не присылают вовсе. Если оценки нет, но
    порядок есть, порядок и используется: место в ответе — тоже информация.
    Терять из-за этого весь второй проход незачем.
    """

    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = "bge-reranker-v2-m3"
    api_key: str = "not-needed"
    timeout: float = 120.0
    retries: int = 3
    #: Сколько фрагментов уходит на сервер за один раз. Ноль — «ещё не
    #: выяснено, шлём всё разом»; дальше подбирается по ответам сервера и
    #: остаётся жить в клиенте: поисковик держит его между вопросами.
    batch: int = 0
    #: До скольких знаков режем каждый фрагмент. Ноль — «не режем». Как и
    #: batch, подбирается по ответам сервера: бывает, что и ОДИН фрагмент
    #: не влезает в физический батч, и делить тогда нечего.
    max_chars: int = 0

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        """Оценки для всех фрагментов, порциями по силам сервера.

        Верхушку выдачи (20 фрагментов по 1200 знаков) отправляли одним
        запросом — около 24 000 знаков. llama.cpp с обычным физическим
        батчем на такое отвечает 500 и пишет в тело «input is too large to
        process. increase the physical batch size». Ответ на это был
        негодный: три одинаковых повтора того же неподъёмного запроса,
        три секунды впустую и молчаливый отказ от реранка — а на вопрос
        приходится до пяти обращений, по одному на каждый заход разбора.

        Кросс-энкодер оценивает пару «запрос — фрагмент» независимо от
        остальных, поэтому дробить список безопасно: оценки из разных
        порций сравнимы между собой. Этим и пользуемся — при отказе по
        размеру порция делится пополам, пока не пройдёт.
        """
        documents = [str(text) for text in texts]
        if not documents:
            return []

        размер = self.batch if self.batch > 0 else len(documents)
        оценки: List[float] = []
        начало = 0
        while начало < len(documents):
            порция = documents[начало:начало + размер]
            try:
                оценки.extend(self._request(query, порция))
            except _TooLarge as тесно:
                # Сперва делим по числу, потом — по длине. Порядок важен:
                # укорачивать фрагменты, когда их просто много, значит зря
                # терять текст, по которому и считается оценка.
                if len(порция) > 1:
                    размер = max(1, len(порция) // 2)
                    self.batch = размер
                    continue
                короче = self._ужать(query, порция[0], str(тесно))
                if короче is None:
                    raise RerankError(
                        f"реранкеру ({self.base_url}) не по силам даже один "
                        f"фрагмент в {MIN_RERANK_CHARS} знаков: {тесно}. "
                        f"Поднимите физический батч llama-server (-ub), "
                        f"например -ub 2048"
                    ) from тесно
                self.max_chars = короче
                # Число фрагментов подбиралось под ПРЕЖНЮЮ длину и успело
                # ужаться до одного. С укороченными их снова влезает больше,
                # поэтому счёт начинаем заново: иначе верхушка уходит на
                # сервер по одному фрагменту — двадцать запросов вместо двух.
                # Зацикливания нет: длина на каждом шаге строго убывает.
                self.batch = 0
                размер = len(documents) - начало
                continue
            начало += len(порция)
        return оценки

    def _ужать(self, query: str, document: str, жалоба: str) -> int | None:
        """Новая длина фрагмента, которая должна пройти. ``None`` — некуда.

        Сервер в жалобе называет числа: «input (574 tokens) is too large to
        process. increase the physical batch size (current batch size: 512)».
        По ним видно, во сколько раз ужиматься, — и одного повтора хватает
        вместо пяти делений пополам вслепую. Если чисел нет, режем вдвое.

        Запас в 15 % нужен потому, что токены считает сервер, а знаки — мы:
        на кириллице один токен бывает и в один знак, и в четыре.
        """
        было = len(document if self.max_chars <= 0 else document[: self.max_chars])
        if было <= MIN_RERANK_CHARS:
            return None
        токенов = re.search(r"input\s*\((\d+)\s*tokens?\)", жалоба, re.I)
        батч = re.search(r"batch size:\s*(\d+)", жалоба, re.I)
        if токенов and батч and int(токенов.group(1)) > 0:
            доля = int(батч.group(1)) / int(токенов.group(1))
            стало = int(было * доля * 0.85)
        else:
            стало = было // 2
        стало = max(MIN_RERANK_CHARS, min(стало, было - 1))
        return стало if стало < было else None

    def _request(self, query: str, documents: Sequence[str]) -> List[float]:
        """Один запрос к сервису. Повторяем только то, что может пройти."""
        куски = ([str(d)[: self.max_chars] for d in documents]
                 if self.max_chars > 0 else [str(d) for d in documents])
        payload: Dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": куски,
            "top_n": len(куски),
        }
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/rerank",
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
                return self._parse(body, len(documents))
            except (urllib.error.URLError, TimeoutError, OSError,
                    json.JSONDecodeError) as error:
                # Тело ответа читается один раз, а нужно оно и здесь, и в
                # тексте ошибки, — поэтому разбираем сразу.
                пояснение = _http.explain(error)
                last_error = error
                if _тесно(error, пояснение):
                    raise _TooLarge(пояснение) from error
                # Сервер не поднят или ответил отказом по существу — ждать и
                # пробовать снова бессмысленно: паузы добавлялись к каждому
                # вопросу, не давая ни одного шанса на успех.
                if _http.refused(error) or not _стоит_повторить(error):
                    raise RerankError(self._беда(error, пояснение)) from error
                if attempt < max(1, self.retries) - 1:
                    time.sleep(2 ** attempt)
        raise RerankError(self._беда(last_error, _http.explain(last_error)))

    def _беда(self, error: BaseException | None, пояснение: str) -> str:
        """Почему не вышло — словами сервера, а не только кодом.

        «HTTP Error 500: Internal Server Error» — тупик: сервер работает, а
        что ему не нравится, узнать неоткуда. Настоящая причина лежит в теле
        ответа, и показать надо именно её.
        """
        код = getattr(error, "code", None)
        if _http.refused(error) if error is not None else False:
            return f"сервис реранка не поднят ({self.base_url}): {пояснение}"
        if код is not None:
            return (f"сервис реранка ({self.base_url}) ответил ошибкой "
                    f"{код}: {пояснение}")
        return f"сервис реранка недоступен ({self.base_url}): {пояснение}"

    def check(self) -> Dict[str, Any]:
        """Отвечает ли реранкер — одним коротким запросом.

        Узнать это можно было единственным способом: задать вопрос
        помощнику и разглядеть в блоке источников строчку «реранк
        пропущен». Причём строчка эта говорила «недоступен» и код 500 —
        то есть ровно то, по чему ничего не понять.

        Здесь спрашиваем малым: один короткий фрагмент. Если и он не
        проходит, дело не в размере запроса, а в самой службе, и об этом
        надо сказать прямо, вместе с тем, что делать.
        """
        try:
            оценки = self._request("проверка связи", ["короткий фрагмент"])
        except _TooLarge as тесно:
            return {"ok": False, "kind": "tight", "model": self.model,
                    "error": f"серверу не по силам даже один короткий фрагмент: {тесно}",
                    "advice": "поднимите физический батч llama-server "
                              "(ключ -ub, например -ub 2048) или уменьшите "
                              "rerank_max_chars в settings.json"}
        except RerankError as беда:
            низ = str(беда).lower()
            if "not support" in низ or "rerank" in низ and "support" in низ:
                совет = ("на этом порту поднята модель, которая не умеет "
                         "реранк. Реранкеру нужна своя модель "
                         "(bge-reranker-v2-m3) и свой llama-server с ключом "
                         "--reranking")
            elif "не поднят" in низ:
                совет = ("запустите llama-server с моделью реранкера и ключом "
                         "--reranking на порту из rerank_base_url "
                         "(обычно 8002) — это ОТДЕЛЬНАЯ служба, не та, что "
                         "считает векторы")
            else:
                совет = ("проверьте, что по адресу rerank_base_url отвечает "
                         "служба реранка с ключом --reranking, а не модель "
                         "эмбеддингов или чат-модель")
            return {"ok": False, "kind": "other", "model": self.model,
                    "error": str(беда), "advice": совет}
        if not оценки:
            return {"ok": False, "kind": "empty", "model": self.model,
                    "error": "служба ответила, но не прислала ни одной оценки",
                    "advice": "похоже, по этому адресу не реранкер: сверьте "
                              "rerank_model и ключ --reranking у llama-server"}
        # Подобранная длина — не мелочь для отчёта: если сервер заставил
        # резать фрагменты, реранк работает, но видит куски вместо абзацев,
        # и качество второго прохода падает. Про это надо сказать.
        совет = ""
        if self.max_chars > 0:
            совет = (f"фрагменты приходится резать до {self.max_chars} знаков — "
                     f"физический батч llama-server мал. Поднимите -ub "
                     f"(например -ub 2048): реранк станет точнее, потому что "
                     f"будет видеть абзац целиком, а не его начало")
        return {"ok": True, "kind": "", "error": "", "advice": совет,
                "model": self.model, "batch": self.batch,
                "max_chars": self.max_chars}

    @staticmethod
    def _parse(body: Any, count: int) -> List[float]:
        results: Any = None
        if isinstance(body, dict):
            for key in ("results", "data", "scores"):
                value = body.get(key)
                if isinstance(value, list) and value:
                    results = value
                    break
        elif isinstance(body, list):
            results = body
        if not results:
            # Ответ есть, но разобрать нечего — порядок первого прохода
            # сохраняем, вместо того чтобы всё обнулять.
            return _descending(count)

        scores = [0.0] * count
        for position, item in enumerate(results):
            index = position
            raw: Any = item
            if isinstance(item, dict):
                if "index" in item:
                    try:
                        index = int(item["index"])
                    except (TypeError, ValueError):
                        index = position
                raw = None
                for key in ("relevance_score", "score", "relevance"):
                    if isinstance(item.get(key), (int, float)):
                        raw = item[key]
                        break
            if not 0 <= index < count:
                continue
            if isinstance(raw, (int, float)):
                scores[index] = float(raw)
            else:
                # Поля с оценкой нет: место в ответе — единственный сигнал.
                scores[index] = 1.0 / (1.0 + position)
        return scores


# ------------------------------------------------------ реранк моделью ----

@dataclass
class LLMReranker:
    """Реранк чат-моделью: когда отдельного сервиса реранка в контуре нет.

    Модель просят вернуть только числа, но она всё равно иногда пишет
    пояснения — разбор это переживает: сначала ищем строки вида «3: 7», затем,
    если не нашли, просто выбираем первые ``n`` чисел из ответа. Фрагменты
    оцениваются пачками по ``batch_size``: длинный список модель начинает
    оценивать поверхностно, а пачками ещё и параллелится.
    """

    llm: LLM
    batch_size: int = 8
    max_chars: int = 700
    temperature: float = 0.0
    name: str = "llm-reranker"

    #: «/no_think» в конце — переключатель Qwen3. Словесной просьбы «не
    #: рассуждай вслух» ему мало: размышление у гибридной модели включается
    #: не подсказкой, а шаблоном, и блок <think> съедал весь потолок оценок.
    #: Разбор не находил ни одной строки «номер: оценка», реранк молча
    #: скатывался в порядок выдачи поиска — то есть не работал вовсе.
    SYSTEM = (
        "Ты — модуль ранжирования фрагментов технической документации. "
        "Ты оцениваешь, насколько фрагмент помогает ответить на запрос инженера. "
        "Ты не пишешь пояснений и не рассуждаешь вслух: только требуемые числа."
        " /no_think"
    )

    TEMPLATE = (
        "### ЗАПРОС\n{query}\n\n"
        "### ФРАГМЕНТЫ\n{documents}\n\n"
        "### ЗАДАНИЕ\n"
        "Оцени полезность каждого фрагмента для ответа на запрос целым числом "
        "от 0 до 10, где 0 — фрагмент не относится к запросу, 10 — фрагмент "
        "прямо отвечает на него. Верни ровно {count} строк вида «номер: оценка», "
        "по одной на фрагмент, в том же порядке. Никакого другого текста."
    )

    _LINE_RE = re.compile(r"^\s*\[?(\d{1,3})\]?\s*[:.)\-]\s*(-?\d+(?:[.,]\d+)?)", re.MULTILINE)
    _NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        documents = [str(text) for text in texts]
        if not documents:
            return []
        size = max(1, int(self.batch_size))
        scores: List[float] = []
        for start in range(0, len(documents), size):
            piece = documents[start:start + size]
            scores.extend(self._score_batch(query, piece))
        return scores

    def _score_batch(self, query: str, documents: Sequence[str]) -> List[float]:
        listing = "\n\n".join(
            f"[{index}] {self._shorten(text)}"
            for index, text in enumerate(documents, start=1)
        )
        user = self.TEMPLATE.format(query=query, documents=listing, count=len(documents))
        try:
            answer = self.llm.complete(
                self.SYSTEM,
                user,
                # Запас поверх «номер: оценка» на случай, если переключатель
                # не услышан и модель всё-таки начала с рассуждения.
                max_tokens=max(256, 12 * len(documents)),
                temperature=self.temperature,
            )
        except Exception as error:  # noqa: BLE001 — тип зависит от клиента модели
            raise RerankError(f"реранк моделью не удался: {error}") from error
        return self._parse(answer, len(documents))

    def _shorten(self, text: str) -> str:
        flat = " ".join(text.split())
        if len(flat) <= self.max_chars:
            return flat
        return flat[: self.max_chars].rstrip() + "…"

    @classmethod
    def _parse(cls, answer: str, count: int) -> List[float]:
        scores = [0.0] * count
        found = False
        for match in cls._LINE_RE.finditer(answer or ""):
            index = int(match.group(1)) - 1
            if 0 <= index < count:
                scores[index] = cls._clamp(match.group(2))
                found = True
        if found:
            return scores
        numbers = cls._NUMBER_RE.findall(answer or "")
        if not numbers:
            # Модель не выдала ни одного числа — порядок первого прохода
            # надёжнее случайного (док. 01, инвариант 5: деградация безопасна).
            return _descending(count)
        for index, raw in enumerate(numbers[:count]):
            scores[index] = cls._clamp(raw)
        return scores

    @staticmethod
    def _clamp(raw: str) -> float:
        try:
            value = float(str(raw).replace(",", "."))
        except ValueError:
            return 0.0
        return max(0.0, min(10.0, value))


# -------------------------------------------------------------- заглушка --

@dataclass
class NoopReranker:
    """Ничего не меняет: отдаёт убывающие оценки в исходном порядке.

    Нужна, чтобы конвейер можно было собрать и прогнать целиком там, где
    реранкера нет, не разводя в вызывающем коде веток ``if reranker is None``.
    """

    name: str = "noop-reranker"

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        return _descending(len(texts))


def build_reranker(settings: Any, llm: LLM | None = None) -> Reranker | None:
    """Реранкер по настройкам: сервис, модель или ничего.

    Возвращает ``None``, если реранк выключен, — вызывающий код обязан
    считать это штатной ситуацией, а не ошибкой конфигурации.
    """
    if not getattr(settings, "rerank_enabled", False):
        return None
    base_url = str(getattr(settings, "rerank_base_url", "") or "")
    if base_url:
        return CrossEncoderReranker(
            base_url=base_url,
            model=str(getattr(settings, "rerank_model", "bge-reranker-v2-m3")),
            api_key=str(getattr(settings, "rerank_api_key", "not-needed")),
            timeout=float(getattr(settings, "rerank_timeout", 120.0)),
        )
    if llm is not None:
        return LLMReranker(llm=llm)
    return None
