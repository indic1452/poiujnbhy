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

    @property
    def name(self) -> str:
        return f"{self.model} @ {self.base_url}"

    def score(self, query: str, texts: Sequence[str]) -> List[float]:
        documents = [str(text) for text in texts]
        if not documents:
            return []
        payload: Dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
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
                last_error = error
                # Сервер не поднят — ждать и пробовать снова бессмысленно:
                # три секунды пауз добавлялись к каждому запросу, не давая
                # ни одного шанса на успех.
                if _http.refused(error):
                    break
                if attempt < max(1, self.retries) - 1:
                    time.sleep(2 ** attempt)
        raise RerankError(f"сервис реранка недоступен ({self.base_url}): {last_error}")

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

    SYSTEM = (
        "Ты — модуль ранжирования фрагментов технической документации. "
        "Ты оцениваешь, насколько фрагмент помогает ответить на запрос инженера. "
        "Ты не пишешь пояснений и не рассуждаешь вслух: только требуемые числа."
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
                max_tokens=max(64, 12 * len(documents)),
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
