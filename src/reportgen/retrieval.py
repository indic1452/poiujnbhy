"""Поиск по корпусу: BM25 на чистом Python плюс точка расширения до гибрида.

В продуктиве BM25 остаётся, но к нему добавляются плотные векторы (bge-m3 и
подобные) и реранкер — см. док. 02. Интерфейс :class:`Retriever` рассчитан
на это: достаточно передать ``dense_scorer`` и ``reranker``, код конвейера
не меняется.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence

from .corpus import Chunk

K1 = 1.2
B = 0.75

# Номера пунктов стандартов («5.3.2», «п. 4.1») должны оставаться одним токеном:
# для библиотеки нормативных документов это самый частый способ сослаться на
# требование, а разбитый на «5», «3», «2» номер не находится.
_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+(?:\.[0-9]+)+|[a-zA-Zа-яА-ЯёЁ0-9]+")

# Служебные слова, которые только зашумляют BM25 на русском.
STOPWORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "мне", "было", "вот", "от", "меня", "еще",
    "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли",
    "если", "уже", "или", "ни", "быть", "был", "него", "до", "вас", "нибудь",
    "для", "при", "этом", "этого", "эта", "эти", "тот", "the", "a", "an",
    "of", "and", "to", "in", "is", "for", "on", "with", "as", "by", "be",
}

# Грубое усечение окончаний. Полноценная лемматизация (pymorphy/spacy) даст
# больше, но тянет зависимости; для BM25 в паре с плотным поиском хватает.
_SUFFIXES = (
    "ования", "ованию", "ирования", "ениями", "ениям", "ением", "ения", "ению",
    "ости", "остью", "ами", "ями", "ого", "ему", "ому", "ыми", "ими", "ей",
    "ах", "ях", "ов", "ев", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие",
    "ой", "ою", "ую", "юю", "их", "ых", "ье", "ья",
    "ам", "ям", "ом", "ем", "ы", "и", "а", "я", "о", "е", "у", "ю",
)


def stem(word: str) -> str:
    if len(word) <= 4 or word.isdigit():
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> List[str]:
    tokens = (token.lower() for token in _TOKEN_RE.findall(text))
    return [stem(token) for token in tokens if token not in STOPWORDS]


@dataclass
class Hit:
    chunk: Chunk
    score: float
    rank: int = 0


class BM25Index:
    """Классический BM25 Okapi. Держит корпус в памяти."""

    def __init__(self, chunks: Sequence[Chunk]):
        self.chunks: List[Chunk] = list(chunks)
        self._tokens: List[Counter] = []
        self._lengths: List[int] = []
        self._df: Counter = Counter()
        for chunk in self.chunks:
            tokens = tokenize(chunk.indexed_text)
            counts = Counter(tokens)
            self._tokens.append(counts)
            self._lengths.append(len(tokens))
            self._df.update(counts.keys())
        self._avg_len = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0

    def __len__(self) -> int:
        return len(self.chunks)

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        doc_types: Iterable[str] | None = None,
        meta_filter: Dict[str, str] | None = None,
        domains: Iterable[str] | None = None,
    ) -> List[Hit]:
        terms = tokenize(query)
        if not terms:
            return []
        allowed_types = set(doc_types) if doc_types else None
        allowed_domains = {d for d in (domains or []) if d} or None
        scores: List[tuple[float, int]] = []
        for index, chunk in enumerate(self.chunks):
            if allowed_types and chunk.doc_type not in allowed_types:
                continue
            if allowed_domains and chunk.meta.get("domain", "") not in allowed_domains:
                continue
            if meta_filter and any(
                str(chunk.meta.get(key, "")).lower() != str(value).lower()
                for key, value in meta_filter.items()
            ):
                continue
            counts = self._tokens[index]
            length = self._lengths[index] or 1
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                denominator = tf + K1 * (1 - B + B * length / (self._avg_len or 1))
                score += self._idf(term) * tf * (K1 + 1) / denominator
            if score > 0:
                scores.append((score, index))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [
            Hit(chunk=self.chunks[index], score=score, rank=rank)
            for rank, (score, index) in enumerate(scores[:top_k], start=1)
        ]

    # -- сохранение ---------------------------------------------------------

    def save(self, path: str | Path) -> None:
        payload = {
            "version": 1,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str | Path) -> "BM25Index":
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls([Chunk.from_dict(item) for item in payload["chunks"]])


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hit]], k: int = 60, top_k: int = 10
) -> List[Hit]:
    """Слияние нескольких ранжирований (RRF).

    Устойчивее взвешенной суммы: не требует калибровки шкал BM25 и косинусной
    близости между собой.
    """
    fused: Dict[str, float] = {}
    seen: Dict[str, Chunk] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            fused[hit.chunk.chunk_id] = fused.get(hit.chunk.chunk_id, 0.0) + 1.0 / (k + rank)
            seen[hit.chunk.chunk_id] = hit.chunk
    ordered = sorted(fused.items(), key=lambda item: -item[1])[:top_k]
    return [
        Hit(chunk=seen[chunk_id], score=score, rank=rank)
        for rank, (chunk_id, score) in enumerate(ordered, start=1)
    ]


DenseScorer = Callable[[str, Sequence[Chunk]], Sequence[float]]
Reranker = Callable[[str, Sequence[Chunk]], Sequence[float]]


class Retriever:
    """Гибридный поиск: BM25 (+ плотный поиск) → реранкер → top-k.

    ``dense_scorer`` и ``reranker`` не входят в каркас: они требуют моделей и
    GPU. Подключаются снаружи одной строкой, интерфейс конвейера при этом
    остаётся прежним.
    """

    def __init__(
        self,
        index: BM25Index,
        dense_scorer: DenseScorer | None = None,
        reranker: Reranker | None = None,
        candidates: int = 50,
        terms_path: Any = None,
    ):
        self.index = index
        self.dense_scorer = dense_scorer
        self.reranker = reranker
        self.candidates = candidates
        #: Двуязычный словарь. Этим поиском пользуются CLI («reportgen search»,
        #: «generate --index») и запасной путь веб-сервиса; без словаря там не
        #: было межъязыкового механизма вообще, и русский вопрос по английскому
        #: RFC не находил ничего.
        self.terms_path = terms_path
        # Своё у каждого потока: поисковик на сервер один, а запросы идут
        # одновременно — общее поле показывало чужое расширение запроса.
        self._state = threading.local()

    @property
    def last_expansion(self) -> List[str]:
        """Что добавилось к последнему запросу этого потока."""
        return getattr(self._state, "expansion", [])

    @last_expansion.setter
    def last_expansion(self, value: Sequence[str] | None) -> None:
        self._state.expansion = list(value or [])

    def search(
        self,
        query: str,
        top_k: int = 6,
        *,
        doc_types: Iterable[str] | None = None,
        meta_filter: Dict[str, str] | None = None,
        domains: Iterable[str] | None = None,
    ) -> List[Hit]:
        from .terms import expand_query  # noqa: PLC0415 — словарь не нужен при импорте

        expanded, self.last_expansion = expand_query(query, self.terms_path)
        lexical = self.index.search(
            expanded, top_k=self.candidates, doc_types=doc_types,
            meta_filter=meta_filter, domains=domains,
        )
        rankings = [lexical]
        if self.dense_scorer is not None and lexical:
            chunks = [hit.chunk for hit in lexical]
            scores = self.dense_scorer(query, chunks)
            dense = sorted(
                (Hit(chunk=c, score=s) for c, s in zip(chunks, scores)),
                key=lambda hit: -hit.score,
            )
            rankings.append(dense)
        merged = reciprocal_rank_fusion(rankings, top_k=self.candidates) if len(rankings) > 1 else lexical

        if self.reranker is not None and merged:
            chunks = [hit.chunk for hit in merged]
            scores = self.reranker(query, chunks)
            merged = sorted(
                (Hit(chunk=c, score=s) for c, s in zip(chunks, scores)),
                key=lambda hit: -hit.score,
            )
        for rank, hit in enumerate(merged[:top_k], start=1):
            hit.rank = rank
        return merged[:top_k]
