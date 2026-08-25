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

import re
from difflib import SequenceMatcher
from pathlib import Path
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
from .rerank import Reranker as RerankerProtocol
from .rerank import RerankError, build_reranker
from .retrieval import Hit, reciprocal_rank_fusion
from .store.models import SEARCHABLE_STATUSES

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
        freshness_window: float = 0.12,
        dense_pool_factor: int = 5,
        terms_path: str | Path | None = None,
    ):
        self.repos = repos
        self.embedder = embedder
        self.reranker = reranker
        self.candidates = max(1, int(candidates))
        self.embed_model = embed_model
        self.rerank_top_n = max(1, int(rerank_top_n))
        self.rerank_max_chars = max(100, int(rerank_max_chars))
        self.rrf_k = int(rrf_k)
        #: Ширина «полки» релевантности, внутри которой решает год издания.
        #: 0.12 — примерно восьмая часть разброса оценок в выдаче: тексты,
        #: отличающиеся слабее, для инженера равнозначны.
        self.freshness_window = float(freshness_window)
        #: На сколько лет документ должен быть новее, чтобы это считалось
        #: переизданием, а не просто соседним по времени источником.
        self.freshness_min_gap = 3
        self.dense_pool_factor = max(1, int(dense_pool_factor))
        #: Двуязычный словарь: половина библиотеки английская, а спрашивают
        #: по-русски. Расширяется только лексический запрос — плотный поиск
        #: bge-m3 язык переступает сам.
        self.terms_path = terms_path
        #: Что добавилось к последнему запросу — показывается инженеру.
        self.last_expansion: List[str] = []
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
        domains: Iterable[str] | None = None,
    ) -> List[Hit]:
        """Найти до ``top_k`` фрагментов. Ранги проставлены, лучший — первый.

        ``domains`` ограничивает поиск направлением (спутник, релейка,
        протоколы …) — по нему отсекается лексический канал прямо в SQL,
        а плотный фильтруется по метаданным чанка.
        """
        self.last_warning = None
        text = (query or "").strip()
        if not text or top_k <= 0:
            return []
        allowed = set(doc_types) if doc_types else None
        areas = {d for d in (domains or []) if d} or None

        # Мусор чистится ДО слияния, а не только на выходе. Иначе фрагмент,
        # случайно попавший в оба канала, копит вклад дважды и обгоняет точный
        # результат, который нашёл только один канал: русские методички,
        # совпавшие по слову «поля», вытесняли английский RFC, найденный по
        # смыслу. Слияние RRF так и задумано — согласие каналов есть довод, —
        # но согласие с нулевым весом доводом не является.
        lexical = _drop_worthless(self._lexical(text, allowed, meta_filter, areas))
        dense = _drop_worthless(
            self._dense(text, allowed, meta_filter, areas, set(SEARCHABLE_STATUSES))
        )

        if lexical and dense:
            merged = reciprocal_rank_fusion(
                [lexical, dense], k=self.rrf_k, top_k=self.candidates
            )
        else:
            merged = list(lexical or dense)[: self.candidates]

        merged = self._rerank(text, merged)
        merged = self._prefer_fresh(merged)
        merged = _drop_worthless(merged)
        hits = merged[:top_k]
        for rank, hit in enumerate(hits, start=1):
            hit.rank = rank
        return hits

    def _prefer_fresh(self, hits: List[Hit]) -> List[Hit]:
        """При почти одинаковой релевантности — свежая редакция вперёд.

        В библиотеке рядом лежат ГОСТ 2009 года и он же 2024-го, методичка и
        её переиздание. Формулировки в них почти совпадают, поэтому поиск
        ставит их рядом, и какая окажется первой — дело случая. Для отчёта
        заказчику это не случайность: ссылаться надо на действующую редакцию.

        Правило намеренно слабое — один проход обменов соседей. Оно чинит
        ровно тот случай, ради которого сделано (две редакции подряд), и не
        может перетасовать выдачу: свежая, но менее подходящая книга не
        поднимется выше точной старой больше чем на одну позицию за проход.

        И применяется оно только к РЕДАКЦИЯМ ОДНОГО документа. Иначе правило
        систематически топит первоисточники: RFC 7230 — 2014 года, RFC 791 —
        1981-го, а методичка отдела — 2021-го, и точный английский документ
        уходил с первого места под русский, который просто новее. Это разные
        документы, а не две редакции, и сравнивать их по свежести незачем.
        """
        if len(hits) < 2 or self.freshness_window <= 0:
            return hits

        def year_of(hit: Hit) -> int:
            raw = (hit.chunk.meta or {}).get("year")
            try:
                return int(raw) if raw else 0
            except (TypeError, ValueError):
                return 0

        years = [year_of(hit) for hit in hits]
        if len({year for year in years if year}) < 2:
            return hits

        scores = [hit.score for hit in hits]
        span = (max(scores) - min(scores)) or 0.0
        if span <= 0:
            return hits
        window = span * self.freshness_window

        order = list(hits)
        for index in range(len(order) - 1):
            current, following = order[index], order[index + 1]
            this_year, next_year = year_of(current), year_of(following)
            if not this_year or not next_year:
                continue
            # Разница меньше трёх лет — это, скорее всего, не переиздание,
            # а просто соседние по времени документы: не трогаем.
            if next_year - this_year < self.freshness_min_gap:
                continue
            if abs(current.score - following.score) > window:
                continue
            if not _same_document(current, following):
                continue
            order[index], order[index + 1] = following, current
        return order

    # -- каналы -------------------------------------------------------------

    def _lexical(
        self,
        query: str,
        allowed: set[str] | None,
        meta_filter: Dict[str, str] | None,
        domains: set[str] | None = None,
    ) -> List[Hit]:
        """Первый канал: FTS5 по стеммированному тексту.

        Запрос перед поиском расширяется английскими эквивалентами. Без этого
        «какие поля в заголовке» не находит в RFC ровно ничего: BM25 ищет
        буквальные слова, а RFC написан по-английски. Плотный поиск такой
        запрос вытягивает, но он работает только когда построены векторы, —
        а лексический канал как раз тот, который точно попадает в название
        поля, то самое, что инженер и ищет.
        """
        from .terms import expand_query  # noqa: PLC0415 — словарь не нужен при импорте

        query, self.last_expansion = expand_query(query, self.terms_path)
        # Фильтр по meta накладывается уже после выборки, поэтому при нём
        # берём запас кандидатов — иначе отсев съест половину списка.
        limit = self.candidates * (self.dense_pool_factor if meta_filter else 1)
        pairs = self.repos.chunks.search_lexical(
            query, limit=limit, doc_types=sorted(allowed) if allowed else None,
            domains=sorted(domains) if domains else None,
        )
        if not pairs:
            return []
        chunks = self._chunks_by_uid([uid for uid, _ in pairs])
        hits: List[Hit] = []
        for uid, score in pairs:
            chunk = chunks.get(uid)
            if chunk is None or not _matches(chunk, allowed, meta_filter, domains):
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
        domains: set[str] | None = None,
        statuses: set[str] | None = None,
    ) -> List[Hit]:
        """Второй канал: косинус между вектором запроса и векторами чанков.

        Направление отсекается здесь же, а не после возврата: иначе по редкому
        направлению (сорок документов по релейкам против тысячи по спутнику)
        плотный канал возвращал бы одни спутниковые фрагменты, они отсеивались
        бы позже, и слияние молча вырождалось в чистый лексический поиск.
        """
        if self.embedder is None:
            # Молчать нельзя: без плотного канала английская половина
            # библиотеки находится только по двуязычному словарю, а всё, что
            # мимо словаря, не находится вовсе. Инженер видит пустую выдачу и
            # думает, что документа нет.
            self.last_warning = (
                "смысловой поиск выключен — английские документы находятся "
                "только по словарю терминов"
            )
            return []
        uids, matrix = self._vectors()
        if not uids:
            total = self.repos.vectors.count()
            if total:
                # Строки в таблице есть, а под нынешнее имя модели их ноль.
                # Библиотеку проиндексировали под «BAAI/bge-m3», а в настройках
                # стоит «bge-m3» (или наоборот) — и поиск тихо становится
                # чисто лексическим.
                self.last_warning = (
                    f"векторы построены другой моделью, не «{self.embed_model}» — "
                    "смысловой поиск не работает; постройте их заново "
                    "(«reportgen embed --force»)"
                )
            else:
                self.last_warning = (
                    "векторы не построены — смысловой поиск не работает; "
                    "выполните «reportgen embed»"
                )
            return []
        try:
            query_vec = self.embedder.embed_one(query)
        except EmbeddingError as error:
            self.last_warning = f"плотный поиск отключён: {error}"
            return []
        if not query_vec:
            return []

        pool = self.candidates
        if allowed or meta_filter or domains:
            pool *= self.dense_pool_factor
        scored = top_cosine(query_vec, matrix, uids, k=pool)
        if not scored:
            return []
        chunks = self._chunks_by_uid([uid for uid, _ in scored])
        hits: List[Hit] = []
        for uid, score in scored:
            chunk = chunks.get(uid)
            if chunk is None or not _matches(chunk, allowed, meta_filter, domains, statuses):
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


#: Доля от лучшего результата, ниже которой фрагмент в промпт не идёт.
#: Два процента — порог намеренно щадящий: у слияния RRF разброс оценок в
#: выдаче меньше чем вдвое, и там он не отсекает ничего. Он рассчитан на
#: другой случай — когда лучший результат весит 6,95, а остальные 0,0000019.
MIN_SCORE_SHARE = 0.02


def _drop_worthless(hits: List[Hit]) -> List[Hit]:
    """Убирает фрагменты, попавшие в выдачу по чистой случайности.

    Запрос «какие поля в заголовке» находил нужное место в RFC с весом 6,95 —
    и вместе с ним шесть методичек с весом 0,0000019: они совпали по слову
    «поля», которое есть в каждом документе, то есть не несут никакой
    информации. Все семь уходили в промпт как [S1]…[S7], и модель получала
    шесть источников мусора на один по делу. Хуже того, ссылки в ответе
    становились непроверяемыми: инженер открывает [S4] и не понимает, при чём
    тут методичка по измерениям.

    Два правила, оба узкие:

    * есть и положительные оценки, и неположительные — остаются положительные.
      Это шкала реранкера: у bge-reranker минус означает «не по теме»;
    * все оценки положительные — остаются те, что весят хотя бы два процента
      от лучшей. Разброс в нормальной выдаче куда меньше, так что правило
      молчит; срабатывает оно на разрыве в тысячи раз.

    Первый результат не выбрасывается никогда: пустая выдача хуже сомнительной.
    """
    if len(hits) < 2:
        return hits
    positive = [hit for hit in hits if hit.score > 0]
    if not positive:
        # Все оценки неположительные — это шкала реранкера целиком, сравнивать
        # долями нечего.
        return hits
    if len(positive) < len(hits):
        return positive

    best = max(hit.score for hit in hits)
    if best <= 0:
        return hits
    kept = [hit for hit in hits if hit.score >= best * MIN_SCORE_SHARE]
    return kept or hits[:1]


#: Насколько должны совпасть названия, чтобы считать документы редакциями
#: одного и того же. 0.85 разводит «ГОСТ Р 53363-2009» и «ГОСТ Р 53363-2024»
#: (одно) с «RFC 7230» и «Методика контроля излучения» (разное).
_SAME_DOCUMENT_RATIO = 0.85

_EDITION_NOISE = re.compile(r"\b(19|20)\d{2}\b|[^0-9a-zа-яё]+")


def _document_title(hit: Hit) -> str:
    meta = hit.chunk.meta or {}
    title = str(meta.get("title") or "")
    if not title and hit.chunk.title_path:
        title = str(hit.chunk.title_path[0])
    return title or hit.chunk.doc_id


def _edition_key(hit: Hit) -> str:
    """Название документа без года и знаков препинания."""
    return _EDITION_NOISE.sub(" ", _document_title(hit).lower()).strip()


def _same_document(first: Hit, second: Hit) -> bool:
    """Похоже ли, что это две редакции одного документа."""
    left, right = _edition_key(first), _edition_key(second)
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= _SAME_DOCUMENT_RATIO


def _matches(
    chunk: Chunk,
    allowed: set[str] | None,
    meta_filter: Dict[str, str] | None,
    domains: set[str] | None = None,
    statuses: set[str] | None = None,
) -> bool:
    """Те же правила отбора, что в :meth:`reportgen.retrieval.BM25Index.search`."""
    if allowed and chunk.doc_type not in allowed:
        return False
    if domains and chunk.meta.get("domain", "") not in domains:
        return False
    if statuses and chunk.meta.get("status", "current") not in statuses:
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
        terms_path=getattr(settings, "terms_path", None),
    )
