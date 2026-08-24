"""Тесты помощника: изоляция чатов, ответы по библиотеке, поток, кабинет."""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from fastapi.testclient import TestClient

from reportgen.config import Settings
from reportgen.corpus import load_corpus
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.app import create_app
from reportgen.web.assistant import HISTORY_DEPTH, MAX_QUESTION, AssistantService
from reportgen.web.service import ReportService, ServiceError

ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads((ROOT / "examples" / "cases" / "case-2024-118.json").read_text(encoding="utf-8"))


def fill_library(repos, *, domain: str = "modulation"):
    by_doc: dict[str, list] = {}
    for chunk in load_corpus(ROOT / "examples" / "corpus"):
        by_doc.setdefault(chunk.doc_id, []).append(chunk)
    for doc_id, chunks in by_doc.items():
        document = repos.documents.upsert(
            doc_id, chunks[0].doc_type, chunks[0].meta.get("title", doc_id),
            "", "sha-" + doc_id[:10], meta=chunks[0].meta, domain=domain,
        )
        repos.chunks.replace_for_document(document, chunks)


class AssistantTestCase(unittest.TestCase):
    library_domain = "modulation"
    with_library = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"),
        )
        self.repos = Repositories(Database(":memory:"))
        if self.with_library:
            fill_library(self.repos, domain=self.library_domain)
        self.reports = ReportService(repos=self.repos, settings=self.settings, llm=StubLLM())
        self.assistant = AssistantService(reports=self.reports)
        self.ivanov = self.repos.users.create("ivanov", "пароль123", "Иванов", "engineer")
        self.petrov = self.repos.users.create("petrov", "пароль123", "Петров", "engineer")

    def tearDown(self):
        self._tmp.cleanup()


class ChatIsolationTests(AssistantTestCase):
    """Чужой разговор недоступен никому, включая администратора."""

    def setUp(self):
        super().setUp()
        self.admin = self.repos.users.create("admin", "пароль123", "Админ", "admin")
        self.chat = self.assistant.create_chat(self.ivanov, title="Личный вопрос")

    def _expect_404(self, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.status, 404)

    def test_other_user_cannot_read(self):
        self._expect_404(lambda: self.assistant.get_chat(self.petrov, self.chat.id))

    def test_admin_cannot_read(self):
        self._expect_404(lambda: self.assistant.get_chat(self.admin, self.chat.id))

    def test_other_user_cannot_rename(self):
        self._expect_404(lambda: self.assistant.rename(self.petrov, self.chat.id, "чужое"))

    def test_other_user_cannot_delete(self):
        self._expect_404(lambda: self.assistant.delete(self.petrov, self.chat.id))

    def test_other_user_cannot_ask(self):
        self._expect_404(lambda: self.assistant.ask(self.petrov, self.chat.id, "вопрос"))

    def test_missing_and_foreign_chat_are_indistinguishable(self):
        with self.assertRaises(ServiceError) as foreign:
            self.assistant.get_chat(self.petrov, self.chat.id)
        with self.assertRaises(ServiceError) as absent:
            self.assistant.get_chat(self.petrov, 99999)
        self.assertEqual(str(foreign.exception), str(absent.exception))

    def test_list_shows_only_own_chats(self):
        self.assistant.create_chat(self.petrov, title="Свой")
        titles = [chat.title for chat in self.assistant.list_chats(self.petrov)]
        self.assertEqual(titles, ["Свой"])


class ChatLifecycleTests(AssistantTestCase):
    def test_auto_title_from_first_question(self):
        chat = self.assistant.create_chat(self.ivanov)
        self.assertEqual(chat.title, "Новый разговор")
        self.assistant.ask(self.ivanov, chat.id, "Какой предел EVM для QPSK?")
        self.assertEqual(
            self.assistant.get_chat(self.ivanov, chat.id).title, "Какой предел EVM для QPSK?"
        )

    def test_manual_title_is_not_overwritten(self):
        chat = self.assistant.create_chat(self.ivanov, title="Разбор кейса")
        self.assistant.ask(self.ivanov, chat.id, "Вопрос про полосу")
        self.assertEqual(self.assistant.get_chat(self.ivanov, chat.id).title, "Разбор кейса")

    def test_long_question_makes_short_title(self):
        chat = self.assistant.create_chat(self.ivanov)
        self.assistant.ask(self.ivanov, chat.id, "полоса " * 40)
        title = self.assistant.get_chat(self.ivanov, chat.id).title
        self.assertLessEqual(len(title), 62)

    def test_archive_hides_from_default_list(self):
        chat = self.assistant.create_chat(self.ivanov)
        self.assistant.update(self.ivanov, chat.id, archived=True)
        self.assertEqual(self.assistant.list_chats(self.ivanov), [])
        self.assertEqual(len(self.assistant.list_chats(self.ivanov, archived=True)), 1)

    def test_delete_removes_messages(self):
        chat = self.assistant.create_chat(self.ivanov)
        self.assistant.ask(self.ivanov, chat.id, "вопрос")
        self.assistant.delete(self.ivanov, chat.id)
        self.assertEqual(self.repos.chats.stats()["messages"], 0)

    def test_unknown_case_reference_is_rejected(self):
        with self.assertRaises(ServiceError) as caught:
            self.assistant.create_chat(self.ivanov, case_ref=4242)
        self.assertEqual(caught.exception.status, 404)


class AnswerTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)

    def test_both_messages_are_stored(self):
        result = self.assistant.ask(self.ivanov, self.chat.id, "Какой предел EVM для QPSK?")
        messages = self.assistant.messages(self.ivanov, self.chat.id)
        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        self.assertEqual(result["answer"]["role"], "assistant")

    def test_answer_carries_sources_and_counters(self):
        result = self.assistant.ask(self.ivanov, self.chat.id, "предел EVM для QPSK")
        self.assertTrue(result["answer"]["sources"])
        self.assertIn("found", result["answer"]["meta"])
        self.assertIn("cited", result["answer"]["meta"])

    def test_only_cited_sources_are_kept(self):
        class OneCitation(StubLLM):
            def complete(self, system, user, **kwargs):
                return "Ответ опирается только на [S2]."

        self.reports.llm = OneCitation()
        result = self.assistant.ask(self.ivanov, self.chat.id, "предел EVM")
        labels = [item["label"] for item in result["answer"]["sources"]]
        self.assertEqual(labels, ["S2"])

    def test_empty_question_is_rejected(self):
        with self.assertRaises(ServiceError) as caught:
            self.assistant.ask(self.ivanov, self.chat.id, "   ")
        self.assertEqual(caught.exception.status, 400)

    def test_too_long_question_is_rejected(self):
        with self.assertRaises(ServiceError) as caught:
            self.assistant.ask(self.ivanov, self.chat.id, "а" * (MAX_QUESTION + 1))
        self.assertEqual(caught.exception.status, 400)

    def test_history_is_bounded(self):
        seen: list[list] = []

        class Spy(StubLLM):
            def complete(self, system, user, *, history=None, **kwargs):
                seen.append(list(history or []))
                return super().complete(system, user, **kwargs)

        self.reports.llm = Spy()
        for number in range(8):
            self.assistant.ask(self.ivanov, self.chat.id, f"вопрос {number}")
        self.assertLessEqual(len(seen[-1]), HISTORY_DEPTH)

    def test_case_context_reaches_the_prompt(self):
        case = self.reports.create_case(
            {"report_type": CASE["report_type"], "case_id": CASE["case_id"], "facts": CASE},
            self.ivanov,
        )
        chat = self.assistant.create_chat(self.ivanov, case_ref=case.id)
        prompts: list[str] = []

        class Spy(StubLLM):
            def complete(self, system, user, **kwargs):
                prompts.append(user)
                return "Ответ."

        self.reports.llm = Spy()
        self.assistant.ask(self.ivanov, chat.id, "Что с этим обращением?")
        self.assertIn("КОНТЕКСТ ОБРАЩЕНИЯ", prompts[0])
        self.assertIn("SUP-2024-118", prompts[0])


class DomainFilterTests(AssistantTestCase):
    def test_domain_narrows_search(self):
        prompts: list[str] = []

        class Spy(StubLLM):
            def complete(self, system, user, **kwargs):
                prompts.append(user)
                return "Ответ."

        self.reports.llm = Spy()
        wide = self.assistant.create_chat(self.ivanov)
        narrow = self.assistant.create_chat(self.ivanov, domain="satellite")

        self.assistant.ask(self.ivanov, wide.id, "занимаемая полоса частот")
        self.assistant.ask(self.ivanov, narrow.id, "занимаемая полоса частот")

        self.assertIn("[S1]", prompts[0])
        # Библиотека размечена как modulation, поэтому в спутниковом чате пусто.
        self.assertIn("ничего подходящего не нашлось", prompts[1])


class EmptyLibraryTests(AssistantTestCase):
    with_library = False

    def test_answer_without_library_is_honest(self):
        chat = self.assistant.create_chat(self.ivanov)
        result = self.assistant.ask(self.ivanov, chat.id, "Что такое HDLC?")
        self.assertEqual(result["answer"]["sources"], [])
        self.assertEqual(result["answer"]["meta"]["found"], 0)


class StreamTests(AssistantTestCase):
    def test_event_order_and_final_state(self):
        chat = self.assistant.create_chat(self.ivanov)
        events = list(self.assistant.ask_stream(self.ivanov, chat.id, "предел EVM для QPSK"))
        kinds = [event["type"] for event in events]
        self.assertEqual(kinds[0], "question")
        self.assertEqual(kinds[1], "sources")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "done")

        streamed = "".join(e["text"] for e in events if e["type"] == "delta")
        stored = self.assistant.messages(self.ivanov, chat.id)[-1]
        self.assertEqual(stored.content, streamed.strip())

    def test_stream_stores_same_as_plain_ask(self):
        first = self.assistant.create_chat(self.ivanov)
        second = self.assistant.create_chat(self.ivanov)
        plain = self.assistant.ask(self.ivanov, first.id, "предел EVM")
        events = list(self.assistant.ask_stream(self.ivanov, second.id, "предел EVM"))
        done = events[-1]
        self.assertEqual(done["answer"]["content"], plain["answer"]["content"])

    def test_model_failure_leaves_chat_consistent(self):
        chat = self.assistant.create_chat(self.ivanov)

        class Broken(StubLLM):
            def stream(self, system, user, **kwargs):
                yield "начало ответа"
                raise RuntimeError("модель отвалилась")

        self.reports.llm = Broken()
        with self.assertRaises(RuntimeError):
            list(self.assistant.ask_stream(self.ivanov, chat.id, "вопрос"))
        messages = self.assistant.messages(self.ivanov, chat.id)
        # Вопрос сохранён, недописанного ответа нет — чат пригоден к продолжению.
        self.assertEqual([m.role for m in messages], ["user"])


class AssistantHttpTests(unittest.TestCase):
    """Тот же контур через HTTP, включая права и поток SSE."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"),
        )
        self.repos = Repositories(Database(":memory:"))
        fill_library(self.repos)
        service = ReportService(repos=self.repos, settings=settings, llm=StubLLM())
        self.client = TestClient(create_app(settings, self.repos, service))
        self.repos.users.create("ivanov", "пароль123", "Иванов", "engineer")
        self.repos.users.create("petrov", "пароль123", "Петров", "engineer")
        self.login("ivanov")

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    def login(self, name):
        response = self.client.post(
            "/api/auth/login", json={"login": name, "password": "пароль123"}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_full_cycle(self):
        chat = self.client.post("/api/chats", json={"domain": "modulation"}).json()["chat"]
        answer = self.client.post(f"/api/chats/{chat['id']}/ask", json={"text": "предел EVM"})
        self.assertEqual(answer.status_code, 200, answer.text)
        loaded = self.client.get(f"/api/chats/{chat['id']}").json()
        self.assertEqual(len(loaded["messages"]), 2)

        renamed = self.client.patch(f"/api/chats/{chat['id']}", json={"title": "Про EVM"})
        self.assertEqual(renamed.json()["chat"]["title"], "Про EVM")
        self.assertEqual(self.client.delete(f"/api/chats/{chat['id']}").status_code, 200)

    def test_foreign_chat_is_404_over_http(self):
        chat = self.client.post("/api/chats", json={}).json()["chat"]
        self.login("petrov")
        self.assertEqual(self.client.get(f"/api/chats/{chat['id']}").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/chats/{chat['id']}/ask", json={"text": "?"}).status_code, 404
        )

    def test_unknown_domain_is_rejected(self):
        response = self.client.post("/api/chats", json={"domain": "нет-такого"})
        self.assertEqual(response.status_code, 400)

    def test_stream_endpoint(self):
        chat = self.client.post("/api/chats", json={}).json()["chat"]
        kinds = []
        with self.client.stream("POST", f"/api/chats/{chat['id']}/stream",
                                json={"text": "предел EVM"}) as response:
            self.assertEqual(response.status_code, 200)
            for line in response.iter_lines():
                if line.startswith("data: "):
                    kinds.append(json.loads(line[6:])["type"])
        self.assertEqual(kinds[0], "question")
        self.assertEqual(kinds[-1], "done")

    def test_password_change(self):
        ok = self.client.post(
            "/api/me/password", json={"current": "пароль123", "new": "новыйпароль1"}
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.client.get("/api/me").json()["user"]["login"], "ivanov")

        wrong = self.client.post(
            "/api/me/password", json={"current": "неверный", "new": "ещёодинпароль"}
        )
        self.assertEqual(wrong.status_code, 403)

        short = self.client.post(
            "/api/me/password", json={"current": "новыйпароль1", "new": "123"}
        )
        self.assertEqual(short.status_code, 400)

    def test_me_summary(self):
        body = self.client.get("/api/me/summary").json()
        self.assertEqual(body["user"]["login"], "ivanov")
        self.assertIn("chats", body)
        self.assertIn("reports", body)

    def test_domains_endpoint_and_library_filter(self):
        domains = self.client.get("/api/domains").json()
        self.assertTrue(any(item["id"] == "protocols" for item in domains["items"]))

        filtered = self.client.get("/api/library", params={"domain": "modulation"}).json()
        self.assertTrue(filtered["items"])
        empty = self.client.get("/api/library", params={"domain": "satellite"}).json()
        self.assertEqual(empty["items"], [])

    def test_set_document_domain(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        response = self.client.put(f"/api/library/{doc_id}/domain", json={"domain": "protocols"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["document"]["domain"], "protocols")
        bad = self.client.put(f"/api/library/{doc_id}/domain", json={"domain": "выдумка"})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
