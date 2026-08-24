import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.corpus import Chunk
from reportgen.retrieval import BM25Index, Hit, Retriever, reciprocal_rank_fusion, tokenize

CHUNKS = [
    Chunk("lit#1", "lit", "literature", ["Конспект", "EVM"],
          "Модуль вектора ошибки характеризует качество модуляции QPSK."),
    Chunk("lit#2", "lit", "literature", ["Конспект", "Полоса"],
          "Занимаемая полоса частот определяется методом 99 процентов мощности."),
    Chunk("rep#1", "rep", "reports", ["Отчёт 2023"],
          "Паразитная составляющая в спектре обнаружена не была."),
]


class TokenizeTests(unittest.TestCase):
    def test_drops_stopwords_and_stems(self):
        self.assertEqual(tokenize("Измерение занимаемой полосы"), ["измерен", "занимаем", "полос"])

    def test_word_forms_collapse(self):
        self.assertEqual(tokenize("занимаемая полоса"), tokenize("занимаемой полосы"))


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.index = BM25Index(CHUNKS)

    def test_ranks_relevant_first(self):
        hits = self.index.search("занимаемая полоса частот")
        self.assertEqual(hits[0].chunk.chunk_id, "lit#2")

    def test_filters_by_doc_type(self):
        hits = self.index.search("спектр", doc_types=["reports"])
        self.assertEqual({hit.chunk.doc_type for hit in hits}, {"reports"})

    def test_no_match_returns_empty(self):
        self.assertEqual(self.index.search("совершенно посторонний запрос ковид"), [])

    def test_breadcrumbs_are_searchable(self):
        # Слова из заголовка нет в теле чанка, но найтись он обязан.
        hits = self.index.search("Отчёт 2023")
        self.assertEqual(hits[0].chunk.chunk_id, "rep#1")

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.json"
            self.index.save(path)
            restored = BM25Index.load(path)
        self.assertEqual(len(restored), len(self.index))
        self.assertEqual(
            restored.search("модуляция QPSK")[0].chunk.chunk_id,
            self.index.search("модуляция QPSK")[0].chunk.chunk_id,
        )


class FusionTests(unittest.TestCase):
    def test_rrf_prefers_consensus(self):
        first = [Hit(CHUNKS[0], 1.0), Hit(CHUNKS[1], 0.9)]
        second = [Hit(CHUNKS[1], 5.0), Hit(CHUNKS[2], 4.0)]
        fused = reciprocal_rank_fusion([first, second])
        self.assertEqual(fused[0].chunk.chunk_id, "lit#2")


class RetrieverTests(unittest.TestCase):
    def test_reranker_reorders(self):
        index = BM25Index(CHUNKS)

        def reranker(query, chunks):
            # Искусственный реранкер: поднимает отчёты наверх.
            return [1.0 if chunk.doc_type == "reports" else 0.0 for chunk in chunks]

        retriever = Retriever(index, reranker=reranker)
        hits = retriever.search("спектр модуляция полоса")
        self.assertEqual(hits[0].chunk.doc_type, "reports")
        self.assertEqual(hits[0].rank, 1)

    def test_dense_scorer_participates(self):
        index = BM25Index(CHUNKS)
        calls = []

        def dense(query, chunks):
            calls.append(query)
            return [float(len(chunks) - i) for i in range(len(chunks))]

        Retriever(index, dense_scorer=dense).search("полоса частот")
        self.assertEqual(calls, ["полоса частот"])


if __name__ == "__main__":
    unittest.main()
