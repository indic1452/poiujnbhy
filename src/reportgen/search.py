"""Гибридный поиск поверх SQLite: лексика + плотные векторы + реранк.

Слой 3 архитектуры (док. 01) в том виде, в каком он работает на установке с
базой: :class:`reportgen.retrieval.BM25Index` держит корпус в оперативной
памяти и хорош для CLI и тестов, а здесь тот же интерфейс поиска реализован
поверх хранилища — FTS5 для лексики, таблица ``embeddings`` для векторов.

Конвейеру (:mod:`reportgen.pipeline`) безразлично, что именно ему передали:
:class:`DatabaseRetriever` повторяет сигнатуру
:meth:`reportgen.retrieval.Retriever.search` и возвращает те же
:class:`reportgen.retrieval.Hit`.

Порядок работы одного запроса::

    FTS5 (candidates)  ─┐
                        ├─ RRF ─→ реранк топ-N ─→ top_k
    косинус по векторам ┘

Оба канала опциональны сверху вниз: нет векторов или эмбеддера — остаётся
чистая лексика; нет реранкера — остаётся слияние. Поиск обязан отвечать
всегда, пусть и хуже: инвариант 5 из док. 01.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Protocol,
    Sequence,
    Tuple,
)

from .corpus import Chunk
from .embeddings import Embedder, EmbeddingClient, EmbeddingError, top_cosine
from .rerank import RerankError, build_reranker
from .rerank import Reranker as RerankerProtocol
from .retrieval import Hit, reciprocal_rank_fusion

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from .config import Settings
    from .store.repo import Repositories

__all__ = ["Retriever", "DatabaseRetriever", "build_retriever"]

# Больше этого числа параметров SQLite в один запрос не отдаём.
_SQL_SLICE = 400


class Retriever(Protocol):
    """Интерфейс поиска, которого ждёт конвейер.

    Ему удовлетворяют и :class:`reportgen.retrieval.Retriever` (индекс в
    памяти), и :class:`DatabaseRetriever` (индекс в SQLite).
    """

    def search(
        self,
        query: str,
        top_k: int = 6,
        *,
        doc_types: Iterable[str] | None = None,
        meta_filter: Dict[str, str] | None = None,
    ) -> List[Hit]:
        ...


class DatabaseRetriever:
    """Гибридный поиск по библиотеке, лежащей в базе.

    :param repos: репозитории хранилища (:class:`reportgen.store.repo.Repositories`);
    :param embedder: источник векторов запроса; ``None`` — только лексика;
    :param reranker: объект с методом ``score(query, texts)`` либо вызываемый
        объект ``(query, chunks) -> оценки`` в стиле
        :mod:`reportgen.retrieval`; ``None`` — без второго прохода;
    :param candidates: сколько кандидатов даёт каждый канал до слияния.

    Матрица векторов кэшируется в памяти: читать сотни тысяч BLOB из SQLite
    на каждый запрос бессмысленно, они меняются только при индексации.
    Признак устаревания — число строк в ``embeddings``; счёт строк стоит
    доли миллисекунды, а дообавление документа его гарантированно меняет.
    """

    def __init__(
        self,
        repos: "Repositories",
        embedder: Embedder | None = None,
        reranker: Any | None = None,
        candidates: int = 50,
        *,
        embed_model: str | None = None,
        rerank_top_n: int = 20,
        rerank_max_chars: int = 1200,
        rrf_k: int = 60,
        dense_pool_factor: int = 5,
    ):
        self.repos = repos
        self.embedder = embedder
        self.reranker = reranker
        self.candidates = max(1, int(candidates))
        self.embed_model = embed_model
        self.rerank_top_n = max(1, int(rerank_top_n))
        self.rerank_max_chars = max(100, int(rerank_max_chars))
        self.rrf_k = int(rrf_k)
        self.dense_pool_factor = max(1, int(dense_pool_factor))
        # Последняя нефатальная неприятность (недоступен сервис эмбеддингов
        # или реранка). Поиск при этом отработал в деградированном режиме.
        self.last_warning: str | None = None
        self._vector_cache: Tuple[List[str], List[List[float]]] | None = None
        self._cached_rows: int = -1

    # -- служебное ----------------------------------------------------------

    def __len__(self) -> int:
        return self.repos.chunks.count()

    @property
    def cached_vector_count(self) -> int:
        """Сколько векторов сейчас лежит в кэше (0 — кэш пуст)."""
        return len(self._vector_cache[0]) if self._vector_cache else 0

    def invalidate_cache(self) -> None:
        """Сбросить кэш векторов вручную (после массовой переиндексации)."""
        self._vector_cache = None
        self._cached_rows = -1

    # -- основной вход ------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 6,
        *,
        doc_types: Iterable[str] | None = None,
        meta_filter: Dict[str, str] | None = None,
    ) -> List[Hit]:
        """Найти до ``top_k`` фрагментов. Ранги проставлены, лучший — первый."""
        self.last_warning = None
        text = (query or "").strip()
        if not text or top_k <= 0:
            return []
        allowed = set(doc_types) if doc_types else None

        lexical = self._lexical(text, allowed, meta_filter)
        dense = self._dense(text, allowed, meta_filter)

        if lexical and dense:
            merged = reciprocal_rank_fusion(
                [lexical, dense], k=self.rrf_k, top_k=self.candidates
            )
        else:
            merged = list(lexical or dense)[: self.candidates]

        merged = self._rerank(text, merged)
        hits = merged[:top_k]
        for rank, hit in enumerate(hits, start=1):
            hit.rank = rank
        return hits

    # -- каналы -------------------------------------------------------------

    def _lexical(
        self,
        query: str,
        allowed: set[str] | None,
        meta_filter: Dict[str, str] | None,
    ) -> List[Hit]:
        """Первый канал: FTS5 по стеммированному тексту."""
        # Фильтр по meta накладывается уже после выборки, поэтому при нём
        # берём запас кандидатов — иначе отсев съест половину списка.
        limit = self.candidates * (self.dense_pool_factor if meta_filter else 1)
        pairs = self.repos.chunks.search_lexical(
            query, limit=limit, doc_types=sorted(allowed) if allowed else None
        )
        if not pairs:
            return []
        chunks = self._chunks_by_uid([uid for uid, _ in pairs])
        hits: List[Hit] = []
        for uid, score in pairs:
            chunk = chunks.get(uid)
            if chunk is None or not _matches(chunk, allowed, meta_filter):
                continue
            hits.append(Hit(chunk=chunk, score=float(score)))
            if len(hits) >= self.candidates:
                break
        return hits

    def _dense(
        self,
        query: str,
        allowed: set[str] | None,
        meta_filter: Dict[str, str] | None,
    ) -> List[Hit]:
        """Второй канал: косинус между вектором запроса и векторами чанков."""
        if self.embedder is None:
            return []
        uids, matrix = self._vectors()
        if not uids:
            return []
        try:
            query_vec = self.embedder.embed_one(query)
        except EmbeddingError as error:
            self.last_warning = f"плотный поиск отключён: {error}"
            return []
        if not query_vec:
            return []

        pool = self.candidates
        if allowed or meta_filter:
            pool *= self.dense_pool_factor
        scored = top_cosine(query_vec, matrix, uids, k=pool)
        if not scored:
            return []
        chunks = self._chunks_by_uid([uid for uid, _ in scored])
        hits: List[Hit] = []
        for uid, score in scored:
            chunk = chunks.get(uid)
            if chunk is None or not _matches(chunk, allowed, meta_filter):
                continue
            hits.append(Hit(chunk=chunk, score=float(score)))
            if len(hits) >= self.candidates:
                break
        return hits

    def _rerank(self, query: str, merged: Sequence[Hit]) -> List[Hit]:
        """Третий проход: пересортировка верхушки списка кандидатов."""
        hits = list(merged)
        if self.reranker is None or not hits:
            return hits
        head = hits[: self.rerank_top_n]
        tail = hits[self.rerank_top_n:]
        try:
            scores = self._call_reranker(query, head)
        except RerankError as error:
            self.last_warning = f"реранк пропущен: {error}"
            return hits
        if len(scores) < len(head):
            # Недостающие оценки не должны выкидывать фрагменты в конец
            # списка: сохраняем за ними порядок первого прохода.
            scores = list(scores) + [float(-index) for index in range(len(head) - len(scores))]
        rescored = [
            Hit(chunk=hit.chunk, score=float(score))
            for hit, score in zip(head, scores)
        ]
        # sorted стабильна: равные оценки оставляют порядок первого прохода.
        rescored.sort(key=lambda hit: -hit.score)
        return rescored + tail

    def _call_reranker(self, query: str, hits: Sequence[Hit]) -> List[float]:
        scorer = getattr(self.reranker, "score", None)
        if callable(scorer):
            texts = [self._rerank_text(hit.chunk) for hit in hits]
            return [float(score) for score in scorer(query, texts)]
        if callable(self.reranker):
            # Совместимость с реранкерами в стиле reportgen.retrieval:
            # вызываемый объект (query, chunks) -> оценки.
            return [float(score) for score in self.reranker(query, [hit.chunk for hit in hits])]
        raise RerankError("реранкер не умеет ни score(query, texts), ни вызов")

    def _rerank_text(self, chunk: Chunk) -> str:
        return chunk.indexed_text[: self.rerank_max_chars]

    # -- доступ к хранилищу -------------------------------------------------

    def _vectors(self) -> Tuple[List[str], List[List[float]]]:
        rows = self.repos.vectors.count()
        if self._vector_cache is None or rows != self._cached_rows:
            self._vector_cache = self.repos.vectors.all_vectors(self.embed_model)
            self._cached_rows = rows
        return self._vector_cache

    def _chunks_by_uid(self, uids: Sequence[str]) -> Dict[str, Chunk]:
        found: Dict[str, Chunk] = {}
        for start in range(0, len(uids), _SQL_SLICE):
            piece = uids[start:start + _SQL_SLICE]
            for chunk in self.repos.chunks.get_many(piece):
                found[chunk.chunk_id] = chunk
        return found


def _matches(
    chunk: Chunk,
    allowed: set[str] | None,
    meta_filter: Dict[str, str] | None,
) -> bool:
    """Те же правила отбора, что в :meth:`reportgen.retrieval.BM25Index.search`."""
    if allowed and chunk.doc_type not in allowed:
        return False
    if meta_filter and any(
        str(chunk.meta.get(key, "")).lower() != str(value).lower()
        for key, value in meta_filter.items()
    ):
        return False
    return True


def build_retriever(
    repos: "Repositories",
    settings: "Settings",
    *,
    llm: Any | None = None,
) -> Retriever:
    """Собирает поиск по настройкам установки.

    Эмбеддер и реранкер подключаются, только если они явно включены
    (``embed_enabled`` / ``rerank_enabled``). Значение по умолчанию — обе
    выключены: свежая установка обязана искать сразу, без второго сервера в
    контуре, а качество наращивается по мере появления железа (док. 06).

    ``llm`` используется как запасной реранкер (:class:`~reportgen.rerank.LLMReranker`),
    когда реранк включён, но адрес отдельного сервиса не задан.
    """
    embedder: Embedder | None = None
    if getattr(settings, "embed_enabled", False):
        embedder = EmbeddingClient(
            base_url=settings.embed_base_url,
            model=settings.embed_model,
            api_key=settings.embed_api_key,
            timeout=settings.embed_timeout,
            batch=settings.embed_batch,
        )
    reranker: RerankerProtocol | None = build_reranker(settings, llm)
    return DatabaseRetriever(
        repos,
        embedder=embedder,
        reranker=reranker,
        candidates=getattr(settings, "retrieval_candidates", 50),
        embed_model=settings.embed_model if embedder is not None else None,
    )
