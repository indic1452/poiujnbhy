"""Беды службы эмбеддингов: назвать причину и не бросить библиотеку.

На машине отдела в библиотеке 13 600 документов и 562 000 фрагментов. При
такой библиотеке сервер эмбеддингов отвечает не «нет», а «500» — и раньше
человек видел на экране ровно это: «сервер эмбеддингов недоступен
(http://127.0.0.1:8001/v1): HTTP Error 500: Internal Server Error». Служба
при этом работала. Настоящая причина лежала в теле ответа, которое никто не
читал, а лечилась она размером пачки, о котором нигде не было сказано.
"""

import json
import unittest
import urllib.error
from io import BytesIO
from typing import List, Sequence

import _bootstrap  # noqa: F401

from reportgen import _http
from reportgen.corpus import Chunk
from reportgen.embeddings import (
    GIVE_UP_AFTER,
    MIN_TEXT_CHARS,
    EmbeddingClient,
    EmbeddingError,
    advice,
    index_embeddings,
)
from reportgen.store.db import Database
from reportgen.store.repo import Repositories


def http_error(code: int = 500, body: object = None) -> urllib.error.HTTPError:
    """Ответ сервера с кодом ошибки — такой же, как приходит от llama.cpp."""
    raw = json.dumps(body if body is not None else {
        "error": {"code": 500, "message": "input is too large to process. "
                                          "increase the physical batch size",
                  "type": "server_error"}}).encode("utf-8")
    return urllib.error.HTTPError("http://127.0.0.1:8001/v1/embeddings", code,
                                  "Internal Server Error", {}, BytesIO(raw))


class ExplainTests(unittest.TestCase):
    """«Internal Server Error» — это не причина, а её отсутствие."""

    def test_the_server_says_why_and_we_repeat_it(self):
        text = _http.explain(http_error())
        self.assertIn("500", text)
        self.assertIn("input is too large", text)

    def test_a_plain_error_is_left_alone(self):
        self.assertEqual("нет связи", _http.explain(RuntimeError("нет связи")))

    def test_a_body_that_is_not_json_still_helps(self):
        error = urllib.error.HTTPError(
            "http://127.0.0.1:8001/v1/embeddings", 500, "Internal Server Error",
            {}, BytesIO(b"model not loaded with --embeddings"))
        self.assertIn("--embeddings", _http.explain(error))

    def test_an_unreadable_body_does_not_break_the_message(self):
        class Broken(urllib.error.HTTPError):
            def read(self):                     # noqa: D102
                raise OSError("поток уже закрыт")

        error = Broken("u", 500, "boom", {}, BytesIO(b""))
        self.assertIn("500", _http.explain(error))


class AdviceTests(unittest.TestCase):
    """Человек сидит за той же машиной: ему нужна команда, а не диагноз."""

    def test_a_stopped_service_is_told_what_to_launch(self):
        text = advice("refused", "http://127.0.0.1:8001/v1")
        self.assertIn("start-embed.ps1", text)
        self.assertIn("bge-m3", text)

    def test_an_answering_service_is_told_about_the_batch(self):
        text = advice("http", "http://127.0.0.1:8001/v1")
        self.assertIn("пачк", text)
        self.assertIn("--embeddings", text)
        self.assertIn("8002", text)

    def test_a_slow_service_is_told_to_wait(self):
        self.assertIn("видеопамять", advice("timeout", "", 120))

    def test_an_unknown_trouble_points_at_the_log(self):
        self.assertIn("embed.log", advice("other"))


class KindTests(unittest.TestCase):
    """Род беды разбираем по типу исключения, а не по тексту."""

    def kind_of(self, error: BaseException) -> str:
        from reportgen.embeddings import _kind_of

        return _kind_of(error)

    def test_kinds(self):
        cases = [
            (ConnectionRefusedError(111, "Connection refused"), "refused"),
            (http_error(), "http"),
            (TimeoutError("вышло время"), "timeout"),
            (json.JSONDecodeError("не json", "", 0), "format"),
            (OSError("что-то ещё"), "other"),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(expected, self.kind_of(error))

    def test_the_reason_inside_urlerror_is_looked_at(self):
        wrapped = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
        self.assertEqual("refused", self.kind_of(wrapped))


class FakeServer:
    """Сервер, который берёт не больше ``limit`` текстов и не длиннее ``chars``."""

    def __init__(self, limit: int = 4, chars: int = 10 ** 9):
        self.limit = limit
        self.chars = chars
        self.calls: List[int] = []

    def __call__(self, texts: Sequence[str]) -> List[List[float]]:
        self.calls.append(len(texts))
        if len(texts) > self.limit:
            raise EmbeddingError("сервер эмбеддингов недоступен: "
                                 "input is too large to process", kind="http")
        if any(len(text) > self.chars for text in texts):
            raise EmbeddingError("сервер эмбеддингов недоступен: "
                                 "input is too large to process", kind="http")
        return [[float(len(text) % 5), 1.0] for text in texts]


def client_with(server: FakeServer, **changes) -> EmbeddingClient:
    client = EmbeddingClient(**changes)
    client._request = server                     # noqa: SLF001 — подмена транспорта
    return client


class BatchTests(unittest.TestCase):
    """Предел сервера считается в токенах, а мы шлём штуки — его нащупывают."""

    def test_a_batch_that_is_too_big_is_halved_until_it_goes(self):
        server = FakeServer(limit=4)
        client = client_with(server, batch=16)
        vectors = client.embed([f"текст {i}" for i in range(16)])
        self.assertEqual(16, len(vectors))
        self.assertEqual(4, client.safe_batch)
        # Крупными были только пробные заходы — 16 и 8; дальше всё по четыре.
        self.assertEqual([16, 8], server.calls[:2])
        self.assertTrue(all(size <= 4 for size in server.calls[2:]), server.calls)

    def test_the_found_size_is_kept_for_the_next_calls(self):
        """Иначе на полумиллионе фрагментов ошибка повторялась бы вечно."""
        server = FakeServer(limit=4)
        client = client_with(server, batch=16)
        client.embed([f"а {i}" for i in range(8)])
        server.calls.clear()
        client.embed([f"б {i}" for i in range(8)])
        self.assertTrue(all(size <= 4 for size in server.calls), server.calls)

    def test_a_dead_service_is_not_halved(self):
        # Дроблением не лечится: пусть беда дойдёт до человека как есть.
        def dead(texts):
            raise EmbeddingError("служба не запущена", kind="refused")

        client = EmbeddingClient(batch=8)
        client._request = dead                   # noqa: SLF001
        with self.assertRaises(EmbeddingError) as caught:
            client.embed(["а", "б", "в", "г"])
        self.assertEqual("refused", caught.exception.kind)


class ShorteningTests(unittest.TestCase):
    """Один фрагмент, который не лезет целиком: таблица на сто страниц."""

    def test_a_single_long_fragment_is_shortened_and_counted(self):
        server = FakeServer(limit=8, chars=1000)
        client = client_with(server, batch=4)
        vectors = client.embed(["я" * 4000])
        self.assertEqual(1, len(vectors))
        # Укороченный текст даёт вектор хуже полного — об этом надо знать.
        self.assertEqual(1, client.shortened)

    def test_shortening_stops_at_a_meaningful_length(self):
        # От текста в двести знаков вектор перестаёт что-либо значить.
        server = FakeServer(limit=8, chars=10)
        client = client_with(server, batch=2)
        with self.assertRaises(EmbeddingError):
            client.embed(["я" * 4000])
        self.assertEqual(0, client.shortened)
        self.assertGreaterEqual(MIN_TEXT_CHARS, 400)


def build_repos(count: int = 6) -> Repositories:
    repos = Repositories(Database(":memory:"))
    document = repos.documents.upsert(
        doc_id="kniga", doc_type="literature", title="Том",
        source_path="kniga.md", sha256="a" * 64, domain="satellite")
    repos.chunks.replace_for_document(document, [
        Chunk(chunk_id=f"kniga#{i:04d}", doc_id="kniga", doc_type="literature",
              title_path=["Глава 1"], text=f"Фрагмент номер {i}.")
        for i in range(count)
    ])
    return repos


class Poison:
    """Сервер, который не берёт один определённый текст — никогда."""

    model = "bge-m3"

    def __init__(self, bad: str):
        self.bad = bad
        self.seen = 0

    def embed(self, texts):
        self.seen += len(texts)
        if any(self.bad in text for text in texts):
            raise EmbeddingError("сервер не принял фрагмент", kind="http")
        return [[1.0, 0.0] for _ in texts]

    def embed_one(self, text):
        return self.embed([text])[0]


class IndexTests(unittest.TestCase):
    def test_one_impossible_fragment_does_not_stop_the_library(self):
        """Остальные полмиллиона фрагментов ни в чём не виноваты."""
        repos = build_repos(6)
        written = index_embeddings(repos, Poison("номер 3"), batch=4)
        self.assertEqual(5, written)
        self.assertEqual(5, repos.vectors.count())

    def test_a_dead_service_stops_the_build_instead_of_pretending(self):
        """Иначе построение «успешно» кончается нулём векторов и молчанием."""
        class Dead:
            model = "bge-m3"

            def embed(self, texts):
                raise EmbeddingError("служба не запущена", kind="refused")

        with self.assertRaises(EmbeddingError):
            index_embeddings(build_repos(4), Dead(), batch=2)

    def test_a_server_that_refuses_everything_gives_up_quickly(self):
        """Перебирать полмиллиона фрагментов из-за сломанной службы незачем."""
        class AllBad:
            model = "bge-m3"

            def __init__(self):
                self.seen = 0

            def embed(self, texts):
                self.seen += len(texts)
                raise EmbeddingError("сервер не принял", kind="http")

        server = AllBad()
        with self.assertRaises(EmbeddingError):
            index_embeddings(build_repos(400), server, batch=4)
        # Сдаёмся около полусотни, а не на четырёхстах.
        self.assertLess(server.seen, GIVE_UP_AFTER * 4)

    def test_the_texts_are_read_by_the_batch_and_not_all_at_once(self):
        """Полмиллиона фрагментов — это гигабайты; в память они не помещаются."""
        repos = build_repos(6)
        calls = []
        original = repos.chunks.get_many
        repos.chunks.get_many = lambda uids: (calls.append(len(uids)) or original(uids))
        repos.chunks.all_chunks = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("вся библиотека поднята в память"))
        index_embeddings(repos, Poison("такого нет"), batch=2)
        self.assertTrue(calls)
        self.assertTrue(all(size <= 2 for size in calls), calls)


if __name__ == "__main__":
    unittest.main()
