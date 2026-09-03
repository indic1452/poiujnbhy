"""Тесты гибридного поиска поверх SQLite.

База поднимается в памяти и наполняется настоящими чанками из
``examples/corpus`` — через репозитории напрямую, без слоя приёма документов.
Сеть в тестах не используется: ``urlopen`` подменяется, а вместо сервера
эмбеддингов работает :class:`reportgen.embeddings.StubEmbedder`.
"""

from __future__ import annotations

import hashlib
import json
import math
import unittest
import urllib.error
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Sequence
from unittest import mock

import _bootstrap  # noqa: F401
from reportgen import embeddings as embeddings_module
from reportgen.config import Settings
from reportgen.corpus import load_corpus
from reportgen.embeddings import (
    EmbeddingClient,
    EmbeddingError,
    StubEmbedder,
    cosine,
    index_embeddings,
    l2_normalize,
    top_cosine,
)
from reportgen.rerank import (
    CrossEncoderReranker,
    LLMReranker,
    NoopReranker,
    RerankError,
    build_reranker,
)
from reportgen.search import DatabaseRetriever, build_retriever
from reportgen.store.db import Database
from reportgen.store.repo import Repositories

CORPUS_DIR = Path(__file__).resolve().parents[1] / "examples" / "corpus"


# ------------------------------------------------------------ оснастка ----

def build_repos() -> Repositories:
    """База в памяти с библиотекой из examples/corpus."""
    repos = Repositories(Database(":memory:"))
    groups: "OrderedDict[str, list]" = OrderedDict()
    for chunk in load_corpus(CORPUS_DIR):
        groups.setdefault(chunk.doc_id, []).append(chunk)
    for doc_id, chunks in groups.items():
        first = chunks[0]
        document = repos.documents.upsert(
            doc_id=doc_id,
            doc_type=first.doc_type,
            title=str(first.meta.get("title", doc_id)),
            source_path=str(first.meta.get("path", "")),
            sha256=hashlib.sha256(doc_id.encode("utf-8")).hexdigest(),
            meta=first.meta,
        )
        repos.chunks.replace_for_document(document, chunks)
    return repos


class FakeEmbedder:
    """Эмбеддер с заранее известным вектором запроса."""

    def __init__(self, vector: Sequence[float] = (1.0, 0.0)):
        self.name = "fake-embedder"
        self.vector = [float(value) for value in vector]
        self.calls: List[str] = []

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> List[float]:
        self.calls.append(text)
        return list(self.vector)


class BrokenEmbedder:
    """Сервер эмбеддингов недоступен."""

    name = "broken-embedder"

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        raise EmbeddingError("соединение отвергнуто")

    def embed_one(self, text: str) -> List[float]:
        raise EmbeddingError("соединение отвергнуто")


class FakeLLM:
    """Модель, отвечающая заранее заданным текстом."""

    def __init__(self, answer: str):
        self.name = "fake-llm"
        self.answer = answer
        self.prompts: List[str] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2) -> str:
        self.prompts.append(user)
        return self.answer


class FakeResponse:
    """Ответ HTTP, пригодный для ``with urlopen(...) as response``."""

    def __init__(self, payload: Any):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class RecordingURLOpen:
    """Подмена ``urllib.request.urlopen``: пишет запросы, отдаёт заготовки."""

    def __init__(self, responder):
        self.responder = responder
        self.payloads: List[Dict[str, Any]] = []

    def __call__(self, request, timeout=None):
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        return self.responder(self.payloads[-1])

    @property
    def calls(self) -> int:
        return len(self.payloads)


def put_ordered_vectors(repos: Repositories, order: Sequence[str], model: str = "fake") -> None:
    """Раскладывает векторы так, чтобы косинус с [1, 0] убывал по списку."""
    total = len(order)
    vectors: Dict[str, List[float]] = {}
    for position, uid in enumerate(order):
        weight = 1.0 - position / (total + 1)
        vectors[uid] = [weight, math.sqrt(max(0.0, 1.0 - weight * weight))]
    repos.vectors.put_many(model, vectors)


OBW_QUERY = "занимаемая полоса частот метод 99 процентов мощности"
EVM_QUERY = "модуль вектора ошибки EVM качество модуляции"


# ------------------------------------------------- лексический поиск ------

class LexicalSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repos = build_repos()

    def setUp(self):
        self.retriever = DatabaseRetriever(self.repos)

    def test_corpus_is_loaded(self):
        self.assertEqual(len(self.retriever), self.repos.chunks.count())
        self.assertGreaterEqual(len(self.retriever), 20)

    def test_finds_relevant_chunk_first(self):
        hits = self.retriever.search(OBW_QUERY, top_k=3)
        self.assertEqual(hits[0].chunk.chunk_id, "literature/spectrum-measurement#0001")
        self.assertIn("99", hits[0].chunk.text)

    def test_ranks_are_sequential_and_top_k_respected(self):
        hits = self.retriever.search("спектр измерение полоса отчёт", top_k=4)
        self.assertEqual(len(hits), 4)
        self.assertEqual([hit.rank for hit in hits], [1, 2, 3, 4])
        self.assertGreater(hits[0].score, 0.0)

    def test_filters_by_doc_types(self):
        hits = self.retriever.search("спектр измерение полоса", top_k=5, doc_types=["reports"])
        self.assertTrue(hits)
        self.assertEqual({hit.chunk.doc_type for hit in hits}, {"reports"})

    def test_meta_filter_keeps_only_matching_documents(self):
        hits = self.retriever.search("измерение параметров", top_k=5,
                                     meta_filter={"doc_kind": "методика"})
        self.assertTrue(hits)
        self.assertEqual({hit.chunk.meta["doc_kind"] for hit in hits}, {"методика"})

    def test_meta_filter_without_matches_returns_empty(self):
        hits = self.retriever.search("измерение параметров", top_k=5,
                                     meta_filter={"doc_kind": "инструкция по эксплуатации"})
        self.assertEqual(hits, [])

    def test_empty_query_returns_empty(self):
        self.assertEqual(self.retriever.search("   "), [])
        self.assertEqual(self.retriever.search(OBW_QUERY, top_k=0), [])

    def test_unknown_terms_return_empty(self):
        self.assertEqual(self.retriever.search("квазимодо тарабарщина"), [])


# --------------------------------------------------- гибридный поиск ------

class HybridSearchTests(unittest.TestCase):
    def setUp(self):
        self.repos = build_repos()

    def test_stub_embedder_does_not_break_top_hit(self):
        lexical = DatabaseRetriever(self.repos).search(OBW_QUERY, top_k=3)
        index_embeddings(self.repos, StubEmbedder(dim=128))
        hybrid = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=128))
        hits = hybrid.search(OBW_QUERY, top_k=3)
        self.assertEqual(hits[0].chunk.chunk_id, lexical[0].chunk.chunk_id)
        self.assertEqual([hit.rank for hit in hits], [1, 2, 3])

    def test_stub_embedder_pulls_in_topically_close_chunks(self):
        index_embeddings(self.repos, StubEmbedder(dim=128))
        hybrid = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=128))
        found = {hit.chunk.doc_id for hit in hybrid.search(EVM_QUERY, top_k=3)}
        self.assertIn("literature/modulation-quality", found)

    def test_dense_channel_promotes_chunk_missed_by_lexical(self):
        lexical_only = DatabaseRetriever(self.repos)
        lexical_ids = [hit.chunk.chunk_id for hit in lexical_only.search(EVM_QUERY, top_k=10)]
        target = "regulations/report-rules#0001"
        self.assertNotIn(target, lexical_ids)

        # Плотный канал ставит фрагмент первым, лексический о нём не знает.
        others = [uid for uid in self._all_uids() if uid not in lexical_ids and uid != target]
        put_ordered_vectors(self.repos, [target, *others, *lexical_ids])
        embedder = FakeEmbedder()
        # candidates=3: каждый канал отдаёт по три кандидата, как на реальном
        # корпусе, где плотный поиск возвращает далеко не весь индекс.
        hybrid = DatabaseRetriever(self.repos, embedder=embedder, candidates=3)
        hits = [hit.chunk.chunk_id for hit in hybrid.search(EVM_QUERY, top_k=3)]
        self.assertIn(target, hits)
        self.assertIn(lexical_ids[0], hits)
        self.assertEqual(embedder.calls, [EVM_QUERY])

    def test_rrf_prefers_consensus_between_channels(self):
        lexical = DatabaseRetriever(self.repos).search(EVM_QUERY, top_k=10)
        best_lexical = lexical[0].chunk.chunk_id
        second_lexical = lexical[1].chunk.chunk_id

        # Второй лексический становится первым плотным, первый — последним:
        # согласие двух каналов обязано перевесить одно первое место.
        rest = [uid for uid in self._all_uids() if uid not in (best_lexical, second_lexical)]
        put_ordered_vectors(self.repos, [second_lexical, *rest, best_lexical])
        hybrid = DatabaseRetriever(self.repos, embedder=FakeEmbedder())
        fused = [hit.chunk.chunk_id for hit in hybrid.search(EVM_QUERY, top_k=25)]
        self.assertEqual(fused[0], second_lexical)
        self.assertIn(best_lexical, fused)
        self.assertGreater(fused.index(best_lexical), fused.index(second_lexical))

    def test_dense_respects_doc_types_filter(self):
        put_ordered_vectors(self.repos, self._all_uids())
        hybrid = DatabaseRetriever(self.repos, embedder=FakeEmbedder())
        hits = hybrid.search(EVM_QUERY, top_k=5, doc_types=["standards"])
        self.assertTrue(hits)
        self.assertEqual({hit.chunk.doc_type for hit in hits}, {"standards"})

    def test_embedder_idle_while_there_are_no_vectors(self):
        embedder = FakeEmbedder()
        hits = DatabaseRetriever(self.repos, embedder=embedder).search(OBW_QUERY, top_k=3)
        self.assertTrue(hits)
        self.assertEqual(embedder.calls, [])

    def test_broken_embedder_degrades_to_lexical(self):
        put_ordered_vectors(self.repos, self._all_uids())
        retriever = DatabaseRetriever(self.repos, embedder=BrokenEmbedder())
        hits = retriever.search(OBW_QUERY, top_k=3)
        self.assertEqual(hits[0].chunk.chunk_id, "literature/spectrum-measurement#0001")
        self.assertIsNotNone(retriever.last_warning)
        self.assertIn("плотный поиск", str(retriever.last_warning))

    def test_warning_belongs_to_the_thread_that_searched(self):
        """Поисковик на сервер один, а спрашивают одновременно.

        Пояснения к поиску лежали в общем поле экземпляра: расширение запроса
        и предупреждение о деградации показывались не тому, кто спрашивал, а
        тому, кто спросил последним. Хуже: чужой поиск обнулял предупреждение
        между поиском и его чтением, и своё инженер не видел вовсе.
        """
        import threading

        put_ordered_vectors(self.repos, self._all_uids())
        retriever = DatabaseRetriever(self.repos, embedder=BrokenEmbedder())
        retriever.search(OBW_QUERY, top_k=3)
        self.assertIn("плотный поиск", str(retriever.last_warning))

        # Соседний поток ищет исправным путём — своего предупреждения у него
        # нет, и чужое он не видит.
        seen = {}

        def other():
            clean = DatabaseRetriever(self.repos, embedder=FakeEmbedder())
            clean.search(OBW_QUERY, top_k=3)
            seen["clean"] = clean.last_warning
            seen["shared"] = retriever.last_warning

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()

        self.assertIsNone(seen["clean"])
        self.assertIsNone(seen["shared"], "чужое предупреждение видно из другого потока")
        # А в своём потоке предупреждение никуда не делось.
        self.assertIn("плотный поиск", str(retriever.last_warning))

    def _all_uids(self) -> List[str]:
        return [chunk.chunk_id for chunk in self.repos.chunks.all_chunks()]


# --------------------------------------------------------- кэш векторов ---

class VectorCacheTests(unittest.TestCase):
    def setUp(self):
        self.repos = build_repos()

    def test_only_one_thread_loads_the_matrix(self):
        """Двадцать одновременных вопросов — не двадцать матриц.

        На библиотеке отдела матрица весит 2,14 ГиБ. Без замка каждый из
        потоков, пришедших с пустым кэшем, заводил свою: сорок гигабайт на
        машине с шестьюдесятью четырьмя — это своп и стоящее приложение.
        """
        import threading
        import time as _time

        retriever = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=64))
        loads = []
        original = self.repos.vectors.load_index
        start = threading.Barrier(8)

        def slow(model=None, batch=2000):
            loads.append(model)
            _time.sleep(0.05)          # загрузка на корпусе отдела — секунды
            return original(model, batch)

        self.repos.vectors.load_index = slow

        def ask():
            start.wait(5)
            retriever._vectors()  # noqa: SLF001 — проверяем именно его

        threads = [threading.Thread(target=ask) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(1, len(loads), f"матрицу загрузили {len(loads)} раз")

    def test_everyone_gets_the_same_matrix(self):
        """Ждавшие берут готовую, а не свою копию."""
        import threading

        retriever = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=64))
        seen = []
        start = threading.Barrier(6)

        def ask():
            start.wait(5)
            seen.append(id(retriever._vectors()))  # noqa: SLF001

        threads = [threading.Thread(target=ask) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(1, len(set(seen)), "матриц вышло несколько")

    def test_cache_is_invalidated_after_indexing(self):
        retriever = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=64))
        retriever.search(OBW_QUERY, top_k=3)
        self.assertEqual(retriever.cached_vector_count, 0)

        written = index_embeddings(self.repos, StubEmbedder(dim=64))
        retriever.search(OBW_QUERY, top_k=3)
        self.assertEqual(retriever.cached_vector_count, written)
        self.assertEqual(written, self.repos.chunks.count())

    def test_cache_is_reused_between_searches(self):
        index_embeddings(self.repos, StubEmbedder(dim=64))
        retriever = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=64))
        with mock.patch.object(
            self.repos.vectors, "load_index", wraps=self.repos.vectors.load_index
        ) as loader:
            retriever.search(OBW_QUERY, top_k=3)
            retriever.search(EVM_QUERY, top_k=3)
            self.assertEqual(loader.call_count, 1)
            retriever.invalidate_cache()
            retriever.search(OBW_QUERY, top_k=3)
            self.assertEqual(loader.call_count, 2)


class VectorCacheDuringBuildTests(unittest.TestCase):
    """Пока строятся векторы, матрицу перечитываем реже — но не позже конца.

    Число векторов меняется после каждой пачки, а разбор вопроса делает
    несколько поисков подряд: без задержки библиотека распаковывалась бы из
    BLOB по разу на заход. Задержка допустима ТОЛЬКО во время постройки:
    вне её достроенный вектор обязан находиться сразу.
    """

    def setUp(self):
        self.repos = build_repos()
        index_embeddings(self.repos, StubEmbedder(dim=64))
        self.retriever = DatabaseRetriever(self.repos, embedder=StubEmbedder(dim=64))

    def more_vectors(self):
        """Дописать вектор ещё одного фрагмента — как делает пачка."""
        self.repos.vectors.put_many("bge-m3", {"pridumannyy#0000": [0.5] * 64})

    def test_while_building_the_matrix_is_not_reloaded_on_every_search(self):
        self.retriever.vectors_building = lambda: True
        with mock.patch.object(
            self.repos.vectors, "load_index", wraps=self.repos.vectors.load_index
        ) as loader:
            self.retriever.search(OBW_QUERY, top_k=3)
            self.more_vectors()
            self.retriever.search(EVM_QUERY, top_k=3)
            self.assertEqual(loader.call_count, 1)

    def test_when_nothing_is_building_a_new_vector_is_seen_at_once(self):
        # Ради экономии слепнуть нельзя: построенный вектор должен
        # находиться сразу, а не «через несколько секунд».
        self.retriever.vectors_building = lambda: False
        with mock.patch.object(
            self.repos.vectors, "load_index", wraps=self.repos.vectors.load_index
        ) as loader:
            self.retriever.search(OBW_QUERY, top_k=3)
            self.more_vectors()
            self.retriever.search(EVM_QUERY, top_k=3)
            self.assertEqual(loader.call_count, 2)

    def test_the_end_of_the_build_is_not_waited_out(self):
        """Постройка кончилась — следующий же поиск видит всю библиотеку."""
        building = [True]
        self.retriever.vectors_building = lambda: building[0]
        self.retriever.search(OBW_QUERY, top_k=3)
        before = self.retriever.cached_vector_count
        self.more_vectors()
        building[0] = False
        self.retriever.search(EVM_QUERY, top_k=3)
        self.assertEqual(before + 1, self.retriever.cached_vector_count)

    def test_a_broken_probe_does_not_break_the_search(self):
        # Подсказка о постройке — удобство, а не условие работы поиска.
        def explode():
            raise RuntimeError("строитель уехал")

        # Первый поиск наполняет кэш — подсказку спрашивают со второго.
        self.retriever.search(OBW_QUERY, top_k=3)
        self.retriever.vectors_building = explode
        self.assertTrue(self.retriever.search(EVM_QUERY, top_k=3))


# ----------------------------------------------------------- реранкеры ----

class RerankInSearchTests(unittest.TestCase):
    def setUp(self):
        self.repos = build_repos()

    def test_reranker_reorders_results(self):
        class ReportsFirst:
            name = "reports-first"

            def score(self, query: str, texts: Sequence[str]) -> List[float]:
                return [10.0 if "SUP-2023-041" in text else 0.0 for text in texts]

        plain = DatabaseRetriever(self.repos).search("спектр полоса измерение", top_k=5)
        self.assertNotEqual(plain[0].chunk.doc_type, "reports")

        retriever = DatabaseRetriever(self.repos, reranker=ReportsFirst())
        hits = retriever.search("спектр полоса измерение", top_k=5)
        self.assertEqual(hits[0].chunk.doc_type, "reports")
        self.assertEqual(hits[0].rank, 1)

    def test_noop_reranker_keeps_order(self):
        plain = DatabaseRetriever(self.repos).search(OBW_QUERY, top_k=5)
        with_noop = DatabaseRetriever(self.repos, reranker=NoopReranker()).search(OBW_QUERY, top_k=5)
        self.assertEqual(
            [hit.chunk.chunk_id for hit in plain],
            [hit.chunk.chunk_id for hit in with_noop],
        )

    def test_callable_reranker_is_supported(self):
        seen: List[str] = []

        def reranker(query, chunks):
            seen.append(query)
            return [1.0 if chunk.doc_type == "regulations" else 0.0 for chunk in chunks]

        retriever = DatabaseRetriever(self.repos, reranker=reranker)
        hits = retriever.search("отчёт оформление измерение", top_k=5)
        self.assertEqual(seen, ["отчёт оформление измерение"])
        self.assertEqual(hits[0].chunk.doc_type, "regulations")

    def test_failing_reranker_does_not_break_search(self):
        class Broken:
            name = "broken"

            def score(self, query: str, texts: Sequence[str]) -> List[float]:
                raise RerankError("сервис реранка недоступен")

        retriever = DatabaseRetriever(self.repos, reranker=Broken())
        hits = retriever.search(OBW_QUERY, top_k=3)
        self.assertEqual(hits[0].chunk.chunk_id, "literature/spectrum-measurement#0001")
        self.assertIn("реранк пропущен", str(retriever.last_warning))


class CrossEncoderRerankerTests(unittest.TestCase):
    def test_parses_relevance_scores(self):
        opener = RecordingURLOpen(lambda payload: FakeResponse({
            "results": [
                {"index": 1, "relevance_score": 0.91},
                {"index": 0, "relevance_score": 0.12},
            ]
        }))
        with mock.patch("reportgen._http.urlopen", opener):
            scores = CrossEncoderReranker().score("полоса", ["первый", "второй"])
        self.assertEqual(scores, [0.12, 0.91])
        self.assertEqual(opener.payloads[0]["documents"], ["первый", "второй"])
        self.assertEqual(opener.payloads[0]["query"], "полоса")

    def test_missing_score_field_falls_back_to_order(self):
        opener = RecordingURLOpen(lambda payload: FakeResponse({
            "results": [{"index": 1}, {"index": 0}]
        }))
        with mock.patch("reportgen._http.urlopen", opener):
            scores = CrossEncoderReranker().score("полоса", ["первый", "второй"])
        self.assertGreater(scores[1], scores[0])

    def test_empty_answer_keeps_input_order(self):
        opener = RecordingURLOpen(lambda payload: FakeResponse({"results": []}))
        with mock.patch("reportgen._http.urlopen", opener):
            scores = CrossEncoderReranker().score("полоса", ["a", "b", "c"])
        self.assertEqual(scores, [3.0, 2.0, 1.0])

    def test_network_error_raises_rerank_error(self):
        def boom(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        with mock.patch("reportgen._http.urlopen", boom):
            with self.assertRaises(RerankError):
                CrossEncoderReranker(retries=1).score("полоса", ["a"])


class LLMRerankerTests(unittest.TestCase):
    def test_parses_numbered_scores(self):
        llm = FakeLLM("1: 2\n2: 9\n3: 0")
        scores = LLMReranker(llm=llm).score("полоса", ["a", "b", "c"])
        self.assertEqual(scores, [2.0, 9.0, 0.0])
        self.assertIn("### ЗАПРОС", llm.prompts[0])

    def test_survives_chatty_answer(self):
        llm = FakeLLM("Конечно! Вот оценки:\n[1] - 7\n[2] - 3\nГотово.")
        self.assertEqual(LLMReranker(llm=llm).score("полоса", ["a", "b"]), [7.0, 3.0])

    def test_falls_back_to_plain_numbers(self):
        llm = FakeLLM("8 4 1")
        self.assertEqual(LLMReranker(llm=llm).score("полоса", ["a", "b", "c"]), [8.0, 4.0, 1.0])

    def test_answer_without_numbers_keeps_order(self):
        llm = FakeLLM("не могу оценить")
        self.assertEqual(LLMReranker(llm=llm).score("полоса", ["a", "b"]), [2.0, 1.0])

    def test_batches_long_lists(self):
        llm = FakeLLM("1: 5\n2: 5")
        LLMReranker(llm=llm, batch_size=2).score("полоса", ["a", "b", "c", "d"])
        self.assertEqual(len(llm.prompts), 2)

    def test_failing_model_becomes_rerank_error(self):
        class Boom:
            name = "boom"

            def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
                raise RuntimeError("модель не отвечает")

        with self.assertRaises(RerankError):
            LLMReranker(llm=Boom()).score("полоса", ["a"])


# ------------------------------------------------ клиент эмбеддингов -----

class EmbeddingClientTests(unittest.TestCase):
    def test_splits_texts_into_batches(self):
        def responder(payload: Dict[str, Any]) -> FakeResponse:
            return FakeResponse({
                "data": [
                    {"index": index, "embedding": [float(index + 1), 0.0, 0.0]}
                    for index in range(len(payload["input"]))
                ]
            })

        opener = RecordingURLOpen(responder)
        with mock.patch("reportgen._http.urlopen", opener):
            vectors = EmbeddingClient(batch=2).embed(["a", "b", "c", "d", "e"])

        self.assertEqual(opener.calls, 3)
        self.assertEqual([len(payload["input"]) for payload in opener.payloads], [2, 2, 1])
        self.assertEqual(len(vectors), 5)
        self.assertEqual(opener.payloads[0]["model"], "bge-m3")

    def test_result_is_l2_normalized(self):
        opener = RecordingURLOpen(
            lambda payload: FakeResponse({"data": [{"index": 0, "embedding": [3.0, 4.0]}]})
        )
        with mock.patch("reportgen._http.urlopen", opener):
            client = EmbeddingClient()
            vector = client.embed_one("занимаемая полоса")
        self.assertAlmostEqual(vector[0], 0.6, places=6)
        self.assertAlmostEqual(vector[1], 0.8, places=6)
        self.assertEqual(client.dim, 2)

    def test_out_of_order_answer_is_sorted_by_index(self):
        opener = RecordingURLOpen(lambda payload: FakeResponse({
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        }))
        with mock.patch("reportgen._http.urlopen", opener):
            vectors = EmbeddingClient().embed(["первый", "второй"])
        self.assertEqual(vectors[0], [1.0, 0.0])
        self.assertEqual(vectors[1], [0.0, 1.0])

    def test_network_error_is_retried_and_reported_in_russian(self):
        attempts: List[int] = []

        def boom(request, timeout=None):
            attempts.append(1)
            raise urllib.error.URLError("connection refused")

        with mock.patch("reportgen._http.urlopen", boom), \
                mock.patch("reportgen.embeddings.time.sleep") as sleeper:
            with self.assertRaises(EmbeddingError) as context:
                EmbeddingClient(retries=3).embed(["a"])
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleeper.call_count, 2)
        self.assertIn("сервер эмбеддингов недоступен", str(context.exception))

    def test_malformed_answer_is_reported(self):
        opener = RecordingURLOpen(lambda payload: FakeResponse({"data": []}))
        with mock.patch("reportgen._http.urlopen", opener):
            with self.assertRaises(EmbeddingError):
                EmbeddingClient(retries=1).embed(["a"])


# --------------------------------------------------------- заглушка ------

class StubEmbedderTests(unittest.TestCase):
    def test_vectors_are_deterministic_and_normalized(self):
        first = StubEmbedder(dim=64).embed_one("занимаемая полоса частот")
        second = StubEmbedder(dim=64).embed_one("занимаемая полоса частот")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in first)), 1.0, places=6)

    def test_similar_texts_are_closer_than_unrelated(self):
        embedder = StubEmbedder(dim=256)
        query = embedder.embed_one("занимаемая полоса частот по методу 99 % мощности")
        near = embedder.embed_one("занимаемой полосы частот, метод 99 процентов мощности")
        far = embedder.embed_one("подпись инженера и регистрация отчёта в реестре")
        self.assertGreater(cosine(query, near), 0.5)
        self.assertGreater(cosine(query, near), cosine(query, far) + 0.3)

    def test_word_forms_collapse(self):
        embedder = StubEmbedder(dim=64)
        self.assertEqual(
            embedder.embed_one("занимаемая полоса"), embedder.embed_one("занимаемой полосы")
        )

    def test_empty_text_gives_zero_vector(self):
        self.assertEqual(StubEmbedder(dim=8).embed_one(""), [0.0] * 8)


class VectorMathTests(unittest.TestCase):
    def test_cosine_matches_between_numpy_and_pure_python(self):
        a = l2_normalize([1.0, 2.0, 3.0])
        b = l2_normalize([3.0, 2.0, 1.0])
        with_numpy = cosine(a, b)
        with mock.patch.object(embeddings_module, "USE_NUMPY", False):
            without_numpy = cosine(a, b)
        self.assertAlmostEqual(with_numpy, without_numpy, places=9)
        self.assertAlmostEqual(cosine(a, a), 1.0, places=9)

    def test_cosine_of_different_dimensions_is_zero(self):
        self.assertEqual(cosine([1.0, 0.0], [1.0, 0.0, 0.0]), 0.0)

    def test_top_cosine_orders_and_limits(self):
        matrix = [[1.0, 0.0], [0.7071, 0.7071], [0.0, 1.0]]
        uids = ["a", "b", "c"]
        result = top_cosine([1.0, 0.0], matrix, uids, k=2)
        self.assertEqual([uid for uid, _ in result], ["a", "b"])
        with mock.patch.object(embeddings_module, "USE_NUMPY", False):
            pure = top_cosine([1.0, 0.0], matrix, uids, k=2)
        self.assertEqual([uid for uid, _ in pure], ["a", "b"])
        self.assertAlmostEqual(result[0][1], pure[0][1], places=6)

    def test_top_cosine_skips_rows_of_other_dimension(self):
        result = top_cosine([1.0, 0.0], [[1.0, 0.0, 0.0], [0.5, 0.5]], ["a", "b"], k=5)
        self.assertEqual([uid for uid, _ in result], ["b"])


# ------------------------------------------------------- индексация ------

class IndexEmbeddingsTests(unittest.TestCase):
    def setUp(self):
        self.repos = build_repos()
        self.total = self.repos.chunks.count()

    def test_fills_every_chunk_and_reports_progress(self):
        steps: List[tuple] = []
        written = index_embeddings(
            self.repos, StubEmbedder(dim=32), batch=8,
            progress=lambda done, total: steps.append((done, total)),
        )
        self.assertEqual(written, self.total)
        self.assertEqual(self.repos.vectors.count(), self.total)
        self.assertEqual(steps[0], (0, self.total))
        self.assertEqual(steps[-1], (self.total, self.total))
        uids, vectors = self.repos.vectors.all_vectors()
        self.assertEqual(len(uids), self.total)
        self.assertTrue(all(len(vector) == 32 for vector in vectors))

    def test_second_run_writes_nothing_when_only_missing(self):
        index_embeddings(self.repos, StubEmbedder(dim=32))
        again = index_embeddings(self.repos, StubEmbedder(dim=32))
        self.assertEqual(again, 0)
        self.assertEqual(self.repos.vectors.count(), self.total)

    def test_only_missing_does_not_overwrite_existing_vectors(self):
        uid = self.repos.chunks.all_chunks()[0].chunk_id
        self.repos.vectors.put_many("прежняя-модель", {uid: [1.0, 0.0, 0.0, 0.0]})

        written = index_embeddings(self.repos, StubEmbedder(dim=32), only_missing=True)
        self.assertEqual(written, self.total - 1)
        kept = self.repos.vectors.get_many([uid])[uid]
        self.assertEqual(kept, [1.0, 0.0, 0.0, 0.0])

    def test_full_reindex_overwrites_everything(self):
        uid = self.repos.chunks.all_chunks()[0].chunk_id
        self.repos.vectors.put_many("прежняя-модель", {uid: [1.0, 0.0, 0.0, 0.0]})

        written = index_embeddings(self.repos, StubEmbedder(dim=32), only_missing=False)
        self.assertEqual(written, self.total)
        self.assertEqual(len(self.repos.vectors.get_many([uid])[uid]), 32)

    def test_model_name_is_stored(self):
        index_embeddings(self.repos, EmbeddingClientStub())
        row = self.repos.db.query_one("SELECT model FROM embeddings LIMIT 1")
        self.assertEqual(row["model"], "тест-модель")


class EmbeddingClientStub(StubEmbedder):
    """Заглушка с полем model — так индексатор именует модель в базе."""

    model = "тест-модель"


# ------------------------------------------------------------- фабрика ---

class BuildRetrieverTests(unittest.TestCase):
    def setUp(self):
        self.repos = build_repos()

    def test_plain_settings_give_lexical_only(self):
        retriever = build_retriever(self.repos, Settings())
        self.assertIsInstance(retriever, DatabaseRetriever)
        self.assertIsNone(retriever.embedder)
        self.assertIsNone(retriever.reranker)
        self.assertTrue(retriever.search(OBW_QUERY, top_k=2))

    def test_embeddings_and_rerank_are_wired_when_enabled(self):
        settings = Settings(
            embed_enabled=True,
            embed_base_url="http://127.0.0.1:9001/v1",
            embed_model="bge-m3",
            rerank_enabled=True,
            rerank_base_url="http://127.0.0.1:9001/v1",
            retrieval_candidates=17,
        )
        retriever = build_retriever(self.repos, settings)
        self.assertIsInstance(retriever.embedder, EmbeddingClient)
        self.assertEqual(retriever.embedder.base_url, "http://127.0.0.1:9001/v1")
        self.assertIsInstance(retriever.reranker, CrossEncoderReranker)
        self.assertEqual(retriever.candidates, 17)
        self.assertEqual(retriever.embed_model, "bge-m3")

    def test_llm_reranker_is_used_without_rerank_service(self):
        settings = Settings(rerank_enabled=True, rerank_base_url="")
        self.assertIsNone(build_reranker(settings))
        retriever = build_retriever(self.repos, settings, llm=FakeLLM("1: 1"))
        self.assertIsInstance(retriever.reranker, LLMReranker)

    def test_a_dead_service_is_not_retried_with_pauses(self):
        """Сервер не поднят — три секунды пауз ни к чему не приведут.

        «Connection refused» приходит мгновенно; повторы с паузами 1 и 2
        секунды добавлялись к КАЖДОМУ запросу, то есть к каждому вопросу
        помощника, не давая ни одного шанса на успех.
        """
        import time

        from reportgen.embeddings import EmbeddingClient, EmbeddingError

        client = EmbeddingClient(base_url="http://127.0.0.1:9/v1", timeout=2.0)
        started = time.monotonic()
        with self.assertRaises(EmbeddingError):
            client.embed(["проба"])
        self.assertLess(time.monotonic() - started, 1.0)

    def test_a_slow_service_is_still_retried(self):
        """Занятый сервер — другое дело: там повтор помогает."""
        from reportgen import _http

        self.assertTrue(_http.refused(ConnectionRefusedError(111, "refused")))
        self.assertFalse(_http.refused(TimeoutError()))

    def test_the_reranker_does_not_share_a_port_with_the_embedder(self):
        """Реранкер — отдельный сервер на своём порту (док. 11, start-embed.ps1).

        По умолчанию здесь стоял тот же 8001, что у эмбеддингов: установка,
        включившая реранк без явного адреса, спрашивала оценки у эмбеддера.
        Тот отвечал ошибкой, реранк молча пропускался — и никто не замечал,
        потому что поиск при этом продолжал работать.
        """
        settings = Settings()
        self.assertNotEqual(settings.embed_base_url, settings.rerank_base_url)
        self.assertIn("8002", settings.rerank_base_url)


if __name__ == "__main__":
    unittest.main()


class ScoreCutoffTests(unittest.TestCase):
    """Фрагменты, попавшие в выдачу по чистой случайности, в промпт не идут.

    Запрос «какие поля в заголовке» находил нужное место в RFC с весом 6,95 —
    и вместе с ним шесть методичек с весом 0,0000019: они совпали по слову
    «поля», которое есть в каждом документе. Все семь уходили в промпт как
    [S1]…[S7]: шесть источников мусора на один по делу, и ссылки в ответе
    становились непроверяемыми.
    """

    def rule(self, scores):
        from reportgen.corpus import Chunk
        from reportgen.retrieval import Hit
        from reportgen.search import _drop_worthless

        hits = [
            Hit(chunk=Chunk(chunk_id=f"c{index}", doc_id=f"d{index}",
                            doc_type="standards", title_path=["Д"], text="т"),
                score=score)
            for index, score in enumerate(scores)
        ]
        return [hit.score for hit in _drop_worthless(hits)]

    def test_huge_gap_is_cut(self):
        self.assertEqual([6.95], self.rule([6.95, 1.9e-06, 1.9e-06, 1.9e-06]))

    def test_normal_spread_is_untouched(self):
        # У слияния RRF разброс в выдаче меньше чем вдвое.
        scores = [0.0164, 0.0161, 0.0155, 0.0121, 0.0091]
        self.assertEqual(scores, self.rule(scores))

    def test_equal_scores_are_all_kept(self):
        self.assertEqual([1.0, 1.0, 1.0], self.rule([1.0, 1.0, 1.0]))

    def test_reranker_negatives_are_dropped_when_positives_exist(self):
        # У bge-reranker минус означает «не по теме».
        self.assertEqual([5.2, 1.1], self.rule([5.2, 1.1, -2.3, -7.0]))

    def test_all_negative_scale_is_left_alone(self):
        # Это шкала реранкера целиком, сравнивать долями нечего.
        scores = [-1.0, -2.0, -3.0]
        self.assertEqual(scores, self.rule(scores))

    def test_single_hit_survives(self):
        self.assertEqual([1.9e-06], self.rule([1.9e-06]))

    def test_first_hit_is_never_dropped(self):
        # Пустая выдача хуже сомнительной.
        self.assertTrue(self.rule([0.001, 0.0005]))


class FusionNoiseTests(unittest.TestCase):
    """Мусор чистится до слияния каналов, а не только на выходе.

    Фрагмент, случайно попавший в оба канала, копит вклад RRF дважды и
    обгоняет точный результат, который нашёл только один канал: русские
    методички, совпавшие по слову «поля», вытесняли английский RFC,
    найденный по смыслу. Согласие каналов — довод, но согласие с нулевым
    весом доводом не является.
    """

    RFC = (
        "Internet Engineering Task Force (IETF)                  R. Fielding\n"
        "Request for Comments: 7230                                    Adobe\n"
        "Category: Standards Track                                 June 2014\n\n"
        "  Hypertext Transfer Protocol (HTTP/1.1): Message Syntax and Routing\n\n"
        "3.2.  Header Fields\n\n"
        "   Each header field consists of a case-insensitive field name followed\n"
        "   by a colon, optional leading whitespace, the field value.\n"
    )
    NOISE = "Поля таблицы заполняются по образцу. Поля формы обязательны. " * 8

    def setUp(self):
        import tempfile

        from reportgen.ingest.pipeline import ingest_directory
        from reportgen.store.db import Database
        from reportgen.store.repo import Repositories

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "standards" / "rfc").mkdir(parents=True)
        (root / "standards" / "rfc" / "rfc7230.txt").write_text(self.RFC, encoding="utf-8")
        (root / "literature").mkdir()
        for index in range(6):
            (root / "literature" / f"м{index}.md").write_text(
                f"# Методичка {index}\n\n{self.NOISE}", encoding="utf-8")
        database = Database(":memory:")
        database.migrate()
        self.repos = Repositories(database)
        ingest_directory(self.repos, root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_russian_question_returns_only_the_rfc(self):
        from reportgen.search import DatabaseRetriever

        glossary = Path(__file__).resolve().parents[1] / "templates" / "terms.json"
        hits = DatabaseRetriever(self.repos, terms_path=glossary).search(
            "какие поля в заголовке и что в них лежит", top_k=8)
        self.assertEqual(1, len(hits), [hit.chunk.doc_id for hit in hits])
        self.assertEqual("standards/rfc/rfc7230", hits[0].chunk.doc_id)

    def test_query_that_suits_everything_keeps_everything(self):
        # Правило обязано молчать там, где все документы вправду подходят.
        from reportgen.search import DatabaseRetriever

        hits = DatabaseRetriever(self.repos).search("поля", top_k=8)
        self.assertGreaterEqual(len(hits), 6)
