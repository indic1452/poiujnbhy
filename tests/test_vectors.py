"""Векторы библиотеки строятся сами, а состояние поиска видно.

Смысловой поиск работает только по фрагментам, у которых есть векторы.
Строила их одна команда из консоли, к которой на изолированной машине никто
не подходит: книгу клали через «Библиотеку», и она молча оставалась
невидимой для поиска по смыслу.
"""

import threading
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.embeddings import EmbeddingError
from reportgen.store.db import Database
from reportgen.store.repo import Repositories
from reportgen.web.vectors import VectorIndexer, _hint

ROOT = Path(__file__).resolve().parents[1]


class StubEmbedder:
    """Служба эмбеддингов, которой на самом деле нет: считает по буквам."""

    def __init__(self, model="bge-m3", fail=False, dim=4):
        self.model = model
        self.fail = fail
        self.dim = dim
        self.calls = 0
        self.texts = []

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise EmbeddingError("служба эмбеддингов не отвечает (127.0.0.1:8001)")
        self.texts.extend(texts)
        return [[float(len(text) % 7 + i) for i in range(self.dim)] for text in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


class SlowEmbedder(StubEmbedder):
    """Считает медленно — чтобы застать работу в разгаре."""

    def __init__(self, gate: threading.Event, **kwargs):
        super().__init__(**kwargs)
        self.gate = gate

    def embed(self, texts):
        self.gate.wait(5)
        return super().embed(texts)


def build_repos(chunk_count=5):
    repos = Repositories(Database(":memory:"))
    stored = repos.documents.upsert(
        doc_id="kniga", doc_type="literature", title="Том по релейным линиям",
        source_path="kniga.md", sha256="a" * 64, domain="microwave",
    )
    chunks = [
        Chunk(
            chunk_id=f"kniga#{index:04d}", doc_id="kniga",
            doc_type="literature", title_path=["Глава 1"],
            text=f"Фрагмент номер {index} про радиорелейные линии связи.",
        )
        for index in range(chunk_count)
    ]
    repos.chunks.replace_for_document(stored, chunks)
    return repos


def make_settings(**changes):
    values = dict(embed_enabled=True, embed_model="bge-m3", embed_batch=2)
    values.update(changes)
    return Settings(**values)


class StatusTests(unittest.TestCase):
    def test_a_fresh_library_reports_every_chunk_as_missing(self):
        indexer = VectorIndexer(build_repos(5), make_settings())
        state = indexer.status()
        self.assertEqual(5, state["chunks"])
        self.assertEqual(0, state["vectors"])
        self.assertEqual(5, state["missing"])
        self.assertFalse(state["ready"])
        self.assertIn("5", state["hint"])

    def test_a_disabled_search_says_so_plainly(self):
        indexer = VectorIndexer(build_repos(2), make_settings(embed_enabled=False))
        state = indexer.status()
        self.assertFalse(state["enabled"])
        self.assertFalse(state["ready"])
        self.assertIn("выключен", state["hint"])

    def test_vectors_of_another_model_are_counted_apart(self):
        """Векторы чужой модели — не «есть» и не «нет», а отдельная беда.

        Косинус между разными моделями ничего не значит: библиотека
        проиндексирована, а поиск при этом слеп целиком.
        """
        repos = build_repos(3)
        repos.vectors.put_many("e5-large", {
            f"kniga#{index:04d}": [0.1, 0.2, 0.3, 0.4] for index in range(3)})
        state = VectorIndexer(repos, make_settings()).status()
        self.assertEqual(0, state["vectors"])
        self.assertEqual(3, state["missing"])
        self.assertEqual(3, state["stale"])
        self.assertIn("другой моделью", state["hint"])

    def test_an_indexed_library_is_ready(self):
        repos = build_repos(3)
        repos.vectors.put_many("bge-m3", {
            f"kniga#{index:04d}": [0.1, 0.2, 0.3, 0.4] for index in range(3)})
        state = VectorIndexer(repos, make_settings()).status()
        self.assertEqual(3, state["vectors"])
        self.assertEqual(0, state["missing"])
        self.assertTrue(state["ready"])
        self.assertIn("работает", state["hint"])

    def test_an_empty_library_is_not_a_complaint(self):
        repos = Repositories(Database(":memory:"))
        state = VectorIndexer(repos, make_settings()).status()
        self.assertFalse(state["ready"])
        self.assertIn("пуста", state["hint"])


class BuildTests(unittest.TestCase):
    def test_vectors_are_built_for_every_chunk(self):
        repos = build_repos(5)
        embedder = StubEmbedder()
        indexer = VectorIndexer(repos, make_settings(), lambda: embedder)
        indexer.start()
        indexer.wait(10)
        state = indexer.status(fresh=True)
        self.assertEqual(5, state["vectors"])
        self.assertEqual(0, state["missing"])
        self.assertTrue(state["ready"])
        self.assertEqual(5, len(embedder.texts))

    def test_only_the_missing_ones_are_rebuilt(self):
        """Книга легла в библиотеку — считать заново всю нельзя.

        На большой библиотеке это часы работы видеокарты, и каждая
        загруженная страница запускала бы их снова.
        """
        repos = build_repos(4)
        repos.vectors.put_many("bge-m3", {
            "kniga#0000": [0.1, 0.2, 0.3, 0.4],
            "kniga#0001": [0.1, 0.2, 0.3, 0.4],
        })
        embedder = StubEmbedder()
        indexer = VectorIndexer(repos, make_settings(), lambda: embedder)
        indexer.start()
        indexer.wait(10)
        self.assertEqual(2, len(embedder.texts))
        self.assertEqual(0, indexer.status(fresh=True)["missing"])

    def test_force_rebuilds_everything(self):
        repos = build_repos(4)
        repos.vectors.put_many("bge-m3", {
            f"kniga#{index:04d}": [0.1, 0.2, 0.3, 0.4] for index in range(4)})
        embedder = StubEmbedder()
        indexer = VectorIndexer(repos, make_settings(), lambda: embedder)
        indexer.start(force=True)
        indexer.wait(10)
        self.assertEqual(4, len(embedder.texts))

    def test_a_dead_service_does_not_take_the_application_down(self):
        repos = build_repos(3)
        indexer = VectorIndexer(repos, make_settings(), lambda: StubEmbedder(fail=True))
        indexer.start()
        indexer.wait(10)
        state = indexer.status(fresh=True)
        self.assertIn("не отвечает", state["error"])
        self.assertIn("не строятся", state["hint"])
        # Библиотека цела, поиск словами работает: векторов просто нет.
        self.assertEqual(3, state["missing"])

    def test_a_broken_client_factory_is_reported_not_raised(self):
        def factory():
            raise RuntimeError("адрес службы не разобран")

        indexer = VectorIndexer(build_repos(2), make_settings(), factory)
        indexer.start()
        indexer.wait(10)
        self.assertIn("адрес службы не разобран", indexer.status(fresh=True)["error"])

    def test_two_starts_do_not_make_two_workers(self):
        """Видеокарта одна: две постройки поделили бы её и шли бы вдвое дольше."""
        gate = threading.Event()
        embedder = SlowEmbedder(gate)
        indexer = VectorIndexer(build_repos(6), make_settings(), lambda: embedder)
        indexer.start()
        second = indexer.start()
        self.assertTrue(second["running"])
        gate.set()
        indexer.wait(10)
        self.assertEqual(6, len(embedder.texts))

    def test_progress_is_visible_while_it_works(self):
        gate = threading.Event()
        indexer = VectorIndexer(build_repos(6), make_settings(),
                                lambda: SlowEmbedder(gate))
        state = indexer.start()
        self.assertTrue(state["running"])
        self.assertIn("строятся векторы", state["hint"])
        gate.set()
        indexer.wait(10)

    def test_nothing_runs_when_the_search_is_switched_off(self):
        embedder = StubEmbedder()
        indexer = VectorIndexer(build_repos(3), make_settings(embed_enabled=False),
                                lambda: embedder)
        indexer.start()
        indexer.wait(5)
        self.assertEqual(0, len(embedder.texts))

    def test_a_complete_library_is_not_rebuilt_on_every_upload(self):
        repos = build_repos(3)
        repos.vectors.put_many("bge-m3", {
            f"kniga#{index:04d}": [0.1, 0.2, 0.3, 0.4] for index in range(3)})
        embedder = StubEmbedder()
        asked = []

        def factory():
            asked.append(1)
            return embedder

        indexer = VectorIndexer(repos, make_settings(), factory)
        state = indexer.start_if_needed()
        self.assertFalse(state["running"])
        indexer.wait(5)
        # Ни потока, ни обращения к службе: всё построено, будить нечего.
        self.assertEqual([], asked)
        self.assertEqual(0, embedder.calls)

    def test_vectors_of_deleted_documents_do_not_inflate_the_count(self):
        """В таблице векторов остаются строки снесённых документов.

        Считали строки таблицы — выходило «векторов больше, чем фрагментов»,
        и человек видел тревогу на пустом месте.
        """
        repos = build_repos(2)
        repos.vectors.put_many("bge-m3", {
            "kniga#0000": [0.1, 0.2, 0.3, 0.4],
            "kniga#0001": [0.1, 0.2, 0.3, 0.4],
            "snesli#0000": [0.1, 0.2, 0.3, 0.4],
        })
        state = VectorIndexer(repos, make_settings()).status()
        self.assertEqual(2, state["chunks"])
        self.assertEqual(2, state["vectors"])
        self.assertEqual(0, state["missing"])
        self.assertEqual(0, state["stale"])
        self.assertTrue(state["ready"])


class HintTests(unittest.TestCase):
    """Строка о состоянии — единственное, что читает человек."""

    BASE = {
        "enabled": True, "error": "", "running": False, "chunks": 100,
        "vectors": 100, "missing": 0, "stale": 0, "model": "bge-m3",
        "done": 0, "total": 0,
    }

    def hint(self, **changes):
        state = dict(self.BASE)
        state.update(changes)
        return _hint(state)

    def test_a_just_started_job_does_not_say_zero_of_zero(self):
        # «строятся векторы: 0 из 0 фрагментов» выглядит поломкой, хотя
        # это просто первые доли секунды: сколько работы — ещё не сочли.
        self.assertEqual("строятся векторы",
                         self.hint(running=True, done=0, total=0))

    def test_the_error_wins_over_the_counts(self):
        self.assertIn("не строятся", self.hint(error="нет связи", missing=40))

    def test_both_troubles_at_once_are_both_named(self):
        text = self.hint(missing=40, stale=60, vectors=60)
        self.assertIn("40", text)
        self.assertIn("60", text)


if __name__ == "__main__":
    unittest.main()
