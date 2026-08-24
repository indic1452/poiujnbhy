"""Тесты помощника: приватность разговоров, поиск под вопрос, поток, кабинет.

Библиотека берётся из ``examples/corpus`` и кладётся в настоящую базу в
памяти, модель подменяется заглушкой. Помощник проверяется целиком — от
репозитория до HTTP, без GPU и без сети.
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence

import _bootstrap  # noqa: F401
from fastapi.testclient import TestClient

from reportgen.config import Settings
from reportgen.corpus import load_corpus
from reportgen.llm import StubLLM
from reportgen.store.db import Database
from reportgen.store.repo import Repositories
from reportgen.web.app import create_app
from reportgen.web.assistant import (
    DEFAULT_TITLE,
    HISTORY_CHARS,
    HISTORY_DEPTH,
    MAX_QUESTION,
    AssistantService,
)
from reportgen.web.service import ReportService, ServiceError

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "examples" / "corpus"
CASE = json.loads((ROOT / "examples" / "cases" / "case-2024-118.json").read_text(encoding="utf-8"))

# Направления, которые тесты проставляют документам демонстрационного корпуса:
# два документа отвечают на один и тот же вопрос про занимаемую полосу, но
# лежат в разных направлениях — на этом проверяется фильтр.
DOC_DOMAINS = {
    "literature/spectrum-measurement": "modulation",
    "standards/obw-method": "measurement",
}

OBW_QUESTION = "Как измеряется занимаемая полоса частот?"


# ----------------------------------------------------------- окружение ---

def make_settings(tmp: Path, *, auth: bool = True) -> Settings:
    return Settings.load(
        data_dir=str(tmp), db_path=":memory:", auth_enabled=auth,
        templates_dir=str(ROOT / "templates"),
        glossary_path=str(ROOT / "templates" / "glossary.json"),
    )


def load_library(repos: Repositories, domains: Dict[str, str] = DOC_DOMAINS) -> int:
    """Кладёт демонстрационный корпус в базу вместе с направлениями."""
    by_doc: Dict[str, List[Any]] = {}
    for chunk in load_corpus(CORPUS):
        by_doc.setdefault(chunk.doc_id, []).append(chunk)
    for doc_id, chunks in by_doc.items():
        document = repos.documents.upsert(
            doc_id, chunks[0].doc_type, chunks[0].meta.get("title", doc_id),
            chunks[0].meta.get("path", ""), "sha-" + doc_id[:10],
            meta=chunks[0].meta, domain=domains.get(doc_id, ""),
        )
        repos.chunks.replace_for_document(document, chunks)
    return len(by_doc)


def parse_sse(body: str) -> List[Dict[str, Any]]:
    """Разбор тела ``text/event-stream`` на события (строки ``data: {json}``)."""
    events: List[Dict[str, Any]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        events.append(json.loads(block[len("data:"):].strip()))
    return events


class RecordingLLM:
    """Подменённая модель: запоминает промпты и отдаёт заданный ответ."""

    def __init__(self, answer: str = "Ответ помощника. [S1]",
                 pieces: Sequence[str] | None = None, fail_at: int | None = None):
        self.name = "recording"
        self.answer = answer
        self.pieces = list(pieces) if pieces is not None else None
        self.fail_at = fail_at
        self.calls: List[Dict[str, Any]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2,
                 history: List[Dict[str, str]] | None = None) -> str:
        self.calls.append({"system": system, "user": user, "history": list(history or [])})
        return self.answer

    def stream(self, system: str, user: str, *, max_tokens: int = 1200,
               temperature: float = 0.2,
               history: List[Dict[str, str]] | None = None) -> Iterator[str]:
        self.calls.append({"system": system, "user": user, "history": list(history or [])})
        pieces = self.pieces if self.pieces is not None else [self.answer]
        for index, piece in enumerate(pieces):
            if self.fail_at is not None and index == self.fail_at:
                raise RuntimeError("модель оборвала соединение")
            yield piece

    @property
    def prompt(self) -> str:
        return self.calls[-1]["user"]

    @property
    def history(self) -> List[Dict[str, str]]:
        return self.calls[-1]["history"]


class BlockingLLM:
    """Модель без потокового интерфейса: у неё есть только ``complete``."""

    name = "blocking"

    def __init__(self, answer: str = "Ответ целиком. [S1]"):
        self.answer = answer
        self.calls = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1200,
                 temperature: float = 0.2,
                 history: List[Dict[str, str]] | None = None) -> str:
        self.calls += 1
        return self.answer


class RecordingRetriever:
    """Обёртка над поиском: запоминает запросы и фильтры, результат не меняет."""

    def __init__(self, inner):
        self.inner = inner
        self.calls: List[Dict[str, Any]] = []

    def search(self, query, top_k=6, *, doc_types=None, meta_filter=None, domains=None):
        self.calls.append({"query": query, "top_k": top_k, "domains": list(domains or [])})
        return self.inner.search(
            query, top_k=top_k, doc_types=doc_types, meta_filter=meta_filter, domains=domains
        )

    @property
    def query(self) -> str:
        return self.calls[-1]["query"]


class LegacyRetriever:
    """Поисковик без поддержки направлений — запасной путь в ``_search``."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def search(self, query, top_k=6, *, doc_types=None, meta_filter=None):
        self.calls += 1
        return self.inner.search(query, top_k=top_k, doc_types=doc_types, meta_filter=meta_filter)


class AssistantTestCase(unittest.TestCase):
    """Общая подготовка: база в памяти, корпус, заглушка модели, три инженера."""

    library = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.settings = make_settings(self.tmp)
        self.repos = Repositories(Database(":memory:"))
        if self.library:
            load_library(self.repos)
        self.service = ReportService(repos=self.repos, settings=self.settings, llm=StubLLM())
        self.assistant = AssistantService(reports=self.service)
        self.engineer = self.repos.users.create("engineer", "пароль123", "Инженер", "engineer")
        self.other = self.repos.users.create("other", "пароль123", "Второй инженер", "engineer")
        self.admin = self.repos.users.create("admin", "пароль123", "Админ", "admin")

    def tearDown(self):
        self.repos.close()
        self._tmp.cleanup()

    # -- помощники тестов ---------------------------------------------------

    def use_llm(self, llm):
        self.service.llm = llm
        return llm

    def use_recording_retriever(self) -> RecordingRetriever:
        wrapper = RecordingRetriever(self.service.get_retriever())
        self.service.retriever = wrapper
        return wrapper

    def make_case(self):
        return self.service.create_case(
            {"report_type": CASE["report_type"], "case_id": CASE["case_id"], "facts": CASE},
            self.engineer,
        )

    def messages(self, chat_id: int):
        return self.repos.chats.messages(chat_id)


# ------------------------------------------------------ жизненный цикл ---

class ChatLifecycleTests(AssistantTestCase):
    def test_new_chat_has_default_title_and_no_messages(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assertEqual(chat.title, DEFAULT_TITLE)
        self.assertEqual(chat.message_count, 0)
        self.assertFalse(chat.archived)
        self.assertEqual(self.messages(chat.id), [])

    def test_chat_can_be_created_with_domain_and_title(self):
        chat = self.assistant.create_chat(
            self.engineer, title="Про пролёт", domain="microwave"
        )
        self.assertEqual(chat.title, "Про пролёт")
        self.assertEqual(chat.domain, "microwave")

    def test_chat_with_unknown_case_is_rejected(self):
        with self.assertRaises(ServiceError) as ctx:
            self.assistant.create_chat(self.engineer, case_ref=4242)
        self.assertEqual(ctx.exception.status, 404)

    def test_chat_binds_to_existing_case(self):
        case = self.make_case()
        chat = self.assistant.create_chat(self.engineer, case_ref=case.id)
        self.assertEqual(chat.case_ref, case.id)

    def test_rename_changes_title(self):
        chat = self.assistant.create_chat(self.engineer)
        renamed = self.assistant.rename(self.engineer, chat.id, "  Занимаемая полоса  ")
        self.assertEqual(renamed.title, "Занимаемая полоса")

    def test_rename_to_blank_falls_back_to_default(self):
        chat = self.assistant.create_chat(self.engineer, title="Что-то")
        renamed = self.assistant.rename(self.engineer, chat.id, "   ")
        self.assertEqual(renamed.title, DEFAULT_TITLE)

    def test_archive_hides_chat_from_active_list(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.update(self.engineer, chat.id, archived=True)
        active = [item.id for item in self.assistant.list_chats(self.engineer)]
        archived = [item.id for item in self.assistant.list_chats(self.engineer, archived=True)]
        self.assertNotIn(chat.id, active)
        self.assertIn(chat.id, archived)

    def test_archived_chat_returns_to_active_list(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.update(self.engineer, chat.id, archived=True)
        self.assistant.update(self.engineer, chat.id, archived=False)
        self.assertIn(chat.id, [item.id for item in self.assistant.list_chats(self.engineer)])

    def test_domain_can_be_changed_later(self):
        chat = self.assistant.create_chat(self.engineer)
        updated = self.assistant.update(self.engineer, chat.id, domain="satellite")
        self.assertEqual(updated.domain, "satellite")

    def test_delete_removes_chat_with_messages(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertEqual(len(self.messages(chat.id)), 2)
        self.assistant.delete(self.engineer, chat.id)
        self.assertIsNone(self.repos.chats.get(chat.id))
        self.assertEqual(
            self.repos.db.scalar(
                "SELECT count(*) FROM chat_messages WHERE chat_id = ?", (chat.id,)
            ),
            0,
        )

    def test_delete_is_recorded_in_audit(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.delete(self.engineer, chat.id)
        self.assertIn("chat.delete", {entry.action for entry in self.repos.audit.list()})

    def test_title_is_taken_from_first_question(self):
        chat = self.assistant.create_chat(self.engineer)
        result = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertEqual(result["chat"]["title"], OBW_QUESTION)

    def test_long_question_makes_clipped_title(self):
        chat = self.assistant.create_chat(self.engineer)
        question = (
            "Какие требования предъявляются к длительности записи и частоте "
            "дискретизации при измерении занимаемой полосы частот?"
        )
        result = self.assistant.ask(self.engineer, chat.id, question)
        title = result["chat"]["title"]
        self.assertTrue(title.endswith("…"), title)
        self.assertLessEqual(len(title), 61)

    def test_manual_title_survives_first_question(self):
        chat = self.assistant.create_chat(self.engineer, title="Разбор обращения 118")
        result = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertEqual(result["chat"]["title"], "Разбор обращения 118")

    def test_message_count_grows_with_dialogue(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assistant.ask(self.engineer, chat.id, "А какая погрешность у метода?")
        listed = {item.id: item for item in self.assistant.list_chats(self.engineer)}
        self.assertEqual(listed[chat.id].message_count, 4)


# ---------------------------------------------------------- приватность ---

class PrivacyTests(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.engineer, title="Личный разговор")
        self.assistant.ask(self.engineer, self.chat.id, OBW_QUESTION)

    def assertHidden(self, call):
        with self.assertRaises(ServiceError) as ctx:
            call()
        self.assertEqual(ctx.exception.status, 404)
        return ctx.exception

    def test_foreign_chat_is_not_readable(self):
        self.assertHidden(lambda: self.assistant.get_chat(self.other, self.chat.id))

    def test_foreign_messages_are_not_readable(self):
        self.assertHidden(lambda: self.assistant.messages(self.other, self.chat.id))

    def test_foreign_chat_cannot_be_renamed(self):
        self.assertHidden(lambda: self.assistant.rename(self.other, self.chat.id, "чужое"))
        self.assertEqual(
            self.assistant.get_chat(self.engineer, self.chat.id).title, "Личный разговор"
        )

    def test_foreign_chat_cannot_be_archived(self):
        self.assertHidden(lambda: self.assistant.update(self.other, self.chat.id, archived=True))
        self.assertFalse(self.assistant.get_chat(self.engineer, self.chat.id).archived)

    def test_foreign_chat_cannot_be_deleted(self):
        self.assertHidden(lambda: self.assistant.delete(self.other, self.chat.id))
        self.assertIsNotNone(self.repos.chats.get(self.chat.id))

    def test_question_into_foreign_chat_is_rejected_and_writes_nothing(self):
        before = len(self.messages(self.chat.id))
        self.assertHidden(lambda: self.assistant.ask(self.other, self.chat.id, "подсмотрю"))
        self.assertEqual(len(self.messages(self.chat.id)), before)

    def test_stream_into_foreign_chat_is_rejected(self):
        with self.assertRaises(ServiceError) as ctx:
            list(self.assistant.ask_stream(self.other, self.chat.id, "подсмотрю"))
        self.assertEqual(ctx.exception.status, 404)

    def test_administrator_has_no_backdoor_to_foreign_chat(self):
        """Администратор — такой же посторонний: чат принадлежит инженеру."""
        self.assertTrue(self.admin.is_admin)
        self.assertHidden(lambda: self.assistant.get_chat(self.admin, self.chat.id))

    def test_foreign_and_missing_chat_are_indistinguishable(self):
        foreign = self.assertHidden(lambda: self.assistant.get_chat(self.other, self.chat.id))
        missing = self.assertHidden(lambda: self.assistant.get_chat(self.other, 99999))
        self.assertEqual(str(foreign), str(missing))

    def test_list_shows_only_own_chats(self):
        mine = self.assistant.create_chat(self.other, title="Мой разговор")
        listed = self.assistant.list_chats(self.other)
        self.assertEqual([item.id for item in listed], [mine.id])

    def test_admin_sees_only_anonymous_statistics(self):
        stats = self.repos.chats.stats()
        self.assertEqual(stats["chats"], 1)
        self.assertEqual(stats["messages"], 2)
        self.assertEqual(stats["users"], 1)
        self.assertNotIn("title", stats)
        self.assertNotIn("content", stats)

    def test_audit_of_question_keeps_no_text(self):
        entry = next(item for item in self.repos.audit.list() if item.action == "chat.ask")
        self.assertEqual(entry.login, "engineer")
        self.assertEqual(set(entry.details), {"found", "cited"})
        self.assertNotIn(OBW_QUESTION, json.dumps(entry.details, ensure_ascii=False))


# --------------------------------------------------------------- ответ ---

class AskTests(AssistantTestCase):
    def test_both_messages_are_saved(self):
        chat = self.assistant.create_chat(self.engineer)
        result = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        stored = self.messages(chat.id)
        self.assertEqual([message.role for message in stored], ["user", "assistant"])
        self.assertEqual(stored[0].content, OBW_QUESTION)
        self.assertEqual(stored[1].content, result["answer"]["content"])
        self.assertTrue(stored[1].content.strip())

    def test_answer_carries_sources_with_links(self):
        chat = self.assistant.create_chat(self.engineer)
        sources = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]["sources"]
        self.assertTrue(sources)
        for source in sources:
            self.assertTrue(source["label"].startswith("S"))
            self.assertTrue(source["chunk_uid"])
            self.assertTrue(source["citation"])
            self.assertTrue(source["text"])
            self.assertIn("doc_type", source)
            self.assertIn("domain", source)

    def test_meta_records_found_and_cited(self):
        chat = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]
        self.assertEqual(answer["meta"]["found"], len(answer["sources"]))
        self.assertEqual(answer["meta"]["cited"], len(answer["sources"]))
        self.assertEqual(answer["meta"]["model"], "stub")

    def test_only_cited_sources_reach_the_panel(self):
        self.use_llm(RecordingLLM(answer="Полоса измеряется методом 99 % мощности [S2]."))
        chat = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]
        self.assertEqual([source["label"] for source in answer["sources"]], ["S2"])
        self.assertEqual(answer["meta"]["cited"], 1)
        self.assertGreater(answer["meta"]["found"], 1)

    def test_answer_without_links_keeps_first_three_sources(self):
        self.use_llm(RecordingLLM(answer="В библиотеке ответа нет. (общее знание)"))
        chat = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]
        self.assertEqual(len(answer["sources"]), 3)
        self.assertEqual(answer["meta"]["cited"], 0)

    def test_empty_question_is_rejected_and_saves_nothing(self):
        chat = self.assistant.create_chat(self.engineer)
        with self.assertRaises(ServiceError) as ctx:
            self.assistant.ask(self.engineer, chat.id, "")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.messages(chat.id), [])

    def test_whitespace_question_is_rejected(self):
        chat = self.assistant.create_chat(self.engineer)
        with self.assertRaises(ServiceError) as ctx:
            self.assistant.ask(self.engineer, chat.id, "   \n\t ")
        self.assertEqual(ctx.exception.status, 400)

    def test_too_long_question_is_rejected(self):
        chat = self.assistant.create_chat(self.engineer)
        with self.assertRaises(ServiceError) as ctx:
            self.assistant.ask(self.engineer, chat.id, "а" * (MAX_QUESTION + 1))
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.messages(chat.id), [])

    def test_question_at_the_limit_is_accepted(self):
        self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, "а" * MAX_QUESTION)
        self.assertEqual(len(self.messages(chat.id)), 2)

    def test_top_k_limits_number_of_fragments(self):
        chat = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION, top_k=2)["answer"]
        self.assertEqual(answer["meta"]["found"], 2)

    def test_domain_of_chat_narrows_the_search(self):
        retriever = self.use_recording_retriever()
        narrow = self.assistant.create_chat(self.engineer, domain="measurement")
        answer = self.assistant.ask(self.engineer, narrow.id, OBW_QUESTION)["answer"]
        self.assertEqual(retriever.calls[-1]["domains"], ["measurement"])
        self.assertTrue(answer["sources"])
        self.assertEqual({source["domain"] for source in answer["sources"]}, {"measurement"})

    def test_chat_without_domain_searches_the_whole_library(self):
        wide = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, wide.id, OBW_QUESTION)["answer"]
        self.assertGreater(len({source["domain"] for source in answer["sources"]}), 1)

    def test_search_without_domain_support_still_answers(self):
        self.service.retriever = LegacyRetriever(self.service.get_retriever())
        chat = self.assistant.create_chat(self.engineer, domain="measurement")
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]
        self.assertGreater(answer["meta"]["found"], 0)

    def test_previous_question_is_added_to_the_query(self):
        retriever = self.use_recording_retriever()
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, "Какой предел EVM допустим для QPSK?")
        self.assistant.ask(self.engineer, chat.id, "А для 16-QAM?")
        self.assertIn("16-QAM", retriever.query)
        self.assertIn("EVM", retriever.query)

    def test_query_is_capped_in_length(self):
        retriever = self.use_recording_retriever()
        self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, "полоса " * 400)
        self.assertLessEqual(len(retriever.query), 1000)

    def test_case_facts_reach_the_prompt(self):
        llm = self.use_llm(RecordingLLM())
        case = self.make_case()
        chat = self.assistant.create_chat(self.engineer, case_ref=case.id)
        self.assistant.ask(self.engineer, chat.id, "Что известно по этому обращению?")
        prompt = llm.prompt
        self.assertIn("КОНТЕКСТ ОБРАЩЕНИЯ", prompt)
        self.assertIn(CASE["case_id"], prompt)
        self.assertIn(CASE["customer"], prompt)
        self.assertIn("12.4", prompt)

    def test_chat_without_case_has_no_case_block(self):
        llm = self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertNotIn("КОНТЕКСТ ОБРАЩЕНИЯ", llm.prompt)

    def test_prompt_carries_question_and_sources(self):
        llm = self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertIn(OBW_QUESTION, llm.prompt)
        self.assertIn("### ИСТОЧНИКИ", llm.prompt)
        self.assertIn("[S1]", llm.prompt)

    def test_history_is_limited_to_depth(self):
        llm = self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        for index in range(HISTORY_DEPTH + 4):
            role = "user" if index % 2 == 0 else "assistant"
            self.repos.chats.add_message(chat.id, role, f"сообщение {index}")
        self.assistant.ask(self.engineer, chat.id, "И что в итоге?")
        history = llm.history
        self.assertEqual(len(history), HISTORY_DEPTH)
        self.assertEqual(history[-1]["content"], f"сообщение {HISTORY_DEPTH + 3}")

    def test_history_entries_are_clipped(self):
        llm = self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.repos.chats.add_message(chat.id, "assistant", "щ" * (HISTORY_CHARS + 500))
        self.assistant.ask(self.engineer, chat.id, "Кратко?")
        content = llm.history[-1]["content"]
        self.assertTrue(content.endswith("…"))
        self.assertLessEqual(len(content), HISTORY_CHARS + 1)

    def test_current_question_is_not_duplicated_into_history(self):
        llm = self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertEqual(llm.calls[0]["history"], [])

    def test_question_is_logged_with_counters(self):
        chat = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]
        entry = next(item for item in self.repos.audit.list() if item.action == "chat.ask")
        self.assertEqual(entry.details["found"], answer["meta"]["found"])
        self.assertEqual(entry.details["cited"], answer["meta"]["cited"])


class EmptyLibraryTests(AssistantTestCase):
    library = False

    def test_answer_without_library_is_honest(self):
        chat = self.assistant.create_chat(self.engineer)
        answer = self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)["answer"]
        self.assertEqual(answer["sources"], [])
        self.assertEqual(answer["meta"], {"model": "stub", "found": 0, "cited": 0})
        self.assertIn("ничего подходящего не нашлось", answer["content"])

    def test_prompt_says_that_library_is_silent(self):
        llm = self.use_llm(RecordingLLM())
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertIn("(в библиотеке ничего подходящего не нашлось)", llm.prompt)

    def test_chat_stays_usable_after_empty_answer(self):
        chat = self.assistant.create_chat(self.engineer)
        self.assistant.ask(self.engineer, chat.id, OBW_QUESTION)
        self.assertEqual(len(self.messages(chat.id)), 2)


# ------------------------------------------------------------- поток -----

class StreamTests(AssistantTestCase):
    def test_event_order(self):
        chat = self.assistant.create_chat(self.engineer)
        events = list(self.assistant.ask_stream(self.engineer, chat.id, OBW_QUESTION))
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "question")
        self.assertEqual(types[1], "sources")
        self.assertEqual(types[-1], "done")
        self.assertTrue(set(types[2:-1]) == {"delta"})
        self.assertGreater(types.count("delta"), 1)

    def test_question_and_sources_arrive_before_text(self):
        chat = self.assistant.create_chat(self.engineer)
        events = list(self.assistant.ask_stream(self.engineer, chat.id, OBW_QUESTION))
        self.assertEqual(events[0]["message"]["content"], OBW_QUESTION)
        self.assertTrue(events[1]["sources"])
        self.assertTrue(events[1]["sources"][0]["citation"])

    def test_deltas_add_up_to_the_saved_answer(self):
        chat = self.assistant.create_chat(self.engineer)
        events = list(self.assistant.ask_stream(self.engineer, chat.id, OBW_QUESTION))
        joined = "".join(event["text"] for event in events if event["type"] == "delta")
        self.assertEqual(joined.strip(), events[-1]["answer"]["content"])
        self.assertEqual(self.messages(chat.id)[1].content, events[-1]["answer"]["content"])

    def test_stream_saves_exactly_what_ask_saves(self):
        self.use_llm(RecordingLLM(
            answer="Полоса определяется методом 99 % мощности [S1].",
            pieces=["Полоса определяется ", "методом 99 % мощности [S1]."],
        ))
        whole = self.assistant.create_chat(self.engineer, title="обычный")
        streamed = self.assistant.create_chat(self.engineer, title="поток")
        direct = self.assistant.ask(self.engineer, whole.id, OBW_QUESTION)
        events = list(self.assistant.ask_stream(self.engineer, streamed.id, OBW_QUESTION))
        done = events[-1]

        self.assertEqual(done["answer"]["content"], direct["answer"]["content"])
        self.assertEqual(done["answer"]["sources"], direct["answer"]["sources"])
        self.assertEqual(done["answer"]["meta"], direct["answer"]["meta"])
        self.assertEqual(done["question"]["content"], direct["question"]["content"])
        self.assertEqual(
            [message.content for message in self.messages(streamed.id)],
            [message.content for message in self.messages(whole.id)],
        )

    def test_done_event_returns_updated_chat(self):
        chat = self.assistant.create_chat(self.engineer)
        events = list(self.assistant.ask_stream(self.engineer, chat.id, OBW_QUESTION))
        self.assertEqual(events[-1]["chat"]["title"], OBW_QUESTION)
        self.assertEqual(events[-1]["chat"]["message_count"], 2)

    def test_model_without_stream_returns_one_delta(self):
        self.use_llm(BlockingLLM())
        chat = self.assistant.create_chat(self.engineer)
        events = list(self.assistant.ask_stream(self.engineer, chat.id, OBW_QUESTION))
        deltas = [event for event in events if event["type"] == "delta"]
        self.assertEqual(len(deltas), 1)
        self.assertEqual(events[-1]["answer"]["content"], "Ответ целиком. [S1]")

    def test_model_failure_does_not_break_the_chat(self):
        self.use_llm(RecordingLLM(pieces=["первый кусок ", "второй кусок"], fail_at=1))
        chat = self.assistant.create_chat(self.engineer)
        with self.assertRaises(RuntimeError):
            list(self.assistant.ask_stream(self.engineer, chat.id, OBW_QUESTION))

        stored = self.messages(chat.id)
        self.assertEqual([message.role for message in stored], ["user"])
        self.assertEqual(self.assistant.get_chat(self.engineer, chat.id).message_count, 1)

        self.use_llm(RecordingLLM(answer="Теперь всё хорошо [S1]."))
        result = self.assistant.ask(self.engineer, chat.id, "Повторю вопрос.")
        self.assertEqual(result["answer"]["content"], "Теперь всё хорошо [S1].")
        self.assertEqual(len(self.messages(chat.id)), 3)

    def test_empty_question_fails_before_any_event(self):
        chat = self.assistant.create_chat(self.engineer)
        stream = self.assistant.ask_stream(self.engineer, chat.id, "  ")
        with self.assertRaises(ServiceError) as ctx:
            next(stream)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(self.messages(chat.id), [])


# ---------------------------------------------------------------- HTTP ---

class AssistantHttpTestCase(unittest.TestCase):
    """Тот же помощник, но через настоящий HTTP-клиент и cookie-сессию."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        settings = make_settings(self.tmp)
        self.repos = Repositories(Database(":memory:"))
        load_library(self.repos)
        self.service = ReportService(repos=self.repos, settings=settings, llm=StubLLM())
        self.app = create_app(settings, self.repos, self.service)
        self.client = TestClient(self.app)
        self.repos.users.create("admin", "пароль123", "Админ", "admin")
        self.repos.users.create("engineer", "пароль123", "Инженер", "engineer")
        self.repos.users.create("viewer", "пароль123", "Наблюдатель", "viewer")
        self.login("engineer")

    def tearDown(self):
        self.client.close()
        self.repos.close()
        self._tmp.cleanup()

    def login(self, login: str, password: str = "пароль123"):
        response = self.client.post(
            "/api/auth/login", json={"login": login, "password": password}
        )
        self.assertEqual(response.status_code, 200, response.text)

    def new_chat(self, **payload) -> Dict[str, Any]:
        response = self.client.post("/api/chats", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["chat"]


class ChatApiTests(AssistantHttpTestCase):
    def test_full_cycle(self):
        chat = self.new_chat(title="Полоса", domain="measurement")
        ask = self.client.post(f"/api/chats/{chat['id']}/ask", json={"text": OBW_QUESTION})
        self.assertEqual(ask.status_code, 200, ask.text)
        body = ask.json()
        self.assertEqual(body["question"]["role"], "user")
        self.assertEqual(body["answer"]["role"], "assistant")
        self.assertTrue(body["answer"]["sources"])

        opened = self.client.get(f"/api/chats/{chat['id']}").json()
        self.assertEqual(len(opened["messages"]), 2)
        self.assertEqual(opened["chat"]["message_count"], 2)

        renamed = self.client.patch(f"/api/chats/{chat['id']}", json={"title": "Занимаемая полоса"})
        self.assertEqual(renamed.json()["chat"]["title"], "Занимаемая полоса")

        archived = self.client.patch(f"/api/chats/{chat['id']}", json={"archived": True})
        self.assertTrue(archived.json()["chat"]["archived"])
        self.assertEqual(self.client.get("/api/chats").json()["items"], [])
        self.assertEqual(
            len(self.client.get("/api/chats", params={"archived": True}).json()["items"]), 1
        )

        self.assertEqual(self.client.delete(f"/api/chats/{chat['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/chats/{chat['id']}").status_code, 404)

    def test_anonymous_has_no_access(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/chats").status_code, 401)
        self.assertEqual(self.client.post("/api/chats", json={}).status_code, 401)

    def test_viewer_may_use_the_assistant(self):
        self.login("viewer")
        chat = self.new_chat()
        response = self.client.post(f"/api/chats/{chat['id']}/ask", json={"text": OBW_QUESTION})
        self.assertEqual(response.status_code, 200, response.text)

    def test_foreign_chat_is_404_for_everyone(self):
        chat = self.new_chat(title="Мой личный")
        for login in ("admin", "viewer"):
            self.login(login)
            self.assertEqual(self.client.get(f"/api/chats/{chat['id']}").status_code, 404)
            self.assertEqual(
                self.client.patch(f"/api/chats/{chat['id']}", json={"title": "чужое"}).status_code,
                404,
            )
            self.assertEqual(self.client.delete(f"/api/chats/{chat['id']}").status_code, 404)
            self.assertEqual(
                self.client.post(
                    f"/api/chats/{chat['id']}/ask", json={"text": "подсмотрю"}
                ).status_code,
                404,
            )
            self.assertNotIn(chat["id"], [item["id"] for item in
                                          self.client.get("/api/chats").json()["items"]])

    def test_missing_chat_answers_like_a_foreign_one(self):
        chat = self.new_chat()
        self.login("admin")
        foreign = self.client.get(f"/api/chats/{chat['id']}")
        missing = self.client.get("/api/chats/99999")
        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.json(), missing.json())

    def test_unknown_domain_is_rejected(self):
        response = self.client.post("/api/chats", json={"domain": "телепатия"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("направление", response.json()["error"])

    def test_unknown_domain_is_rejected_on_update(self):
        chat = self.new_chat()
        response = self.client.patch(f"/api/chats/{chat['id']}", json={"domain": "телепатия"})
        self.assertEqual(response.status_code, 400)

    def test_empty_question_is_400(self):
        chat = self.new_chat()
        response = self.client.post(f"/api/chats/{chat['id']}/ask", json={"text": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_chat_bound_to_case(self):
        case = self.client.post(
            "/api/cases",
            json={"report_type": CASE["report_type"], "case_id": CASE["case_id"], "facts": CASE},
        ).json()["case"]
        chat = self.new_chat(case_ref=case["id"])
        self.assertEqual(chat["case_ref"], case["id"])

    def test_chat_with_unknown_case_is_404(self):
        response = self.client.post("/api/chats", json={"case_ref": 4242})
        self.assertEqual(response.status_code, 404)


class StreamApiTests(AssistantHttpTestCase):
    def test_stream_returns_sse_events(self):
        chat = self.new_chat()
        response = self.client.post(
            f"/api/chats/{chat['id']}/stream", json={"text": OBW_QUESTION}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/event-stream", response.headers["content-type"])

        events = parse_sse(response.text)
        types = [event["type"] for event in events]
        self.assertEqual(types[0], "question")
        self.assertEqual(types[1], "sources")
        self.assertIn("delta", types)
        self.assertEqual(types[-1], "done")

        answer = events[-1]["answer"]
        joined = "".join(event["text"] for event in events if event["type"] == "delta")
        self.assertEqual(joined.strip(), answer["content"])
        saved = self.client.get(f"/api/chats/{chat['id']}").json()["messages"]
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[1]["content"], answer["content"])

    def test_stream_reports_errors_as_an_event(self):
        chat = self.new_chat()
        response = self.client.post(f"/api/chats/{chat['id']}/stream", json={"text": ""})
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual([event["type"] for event in events], ["error"])
        self.assertIn("пустой вопрос", events[0]["error"])
        self.assertEqual(self.client.get(f"/api/chats/{chat['id']}").json()["messages"], [])

    def test_stream_into_foreign_chat_is_404(self):
        chat = self.new_chat()
        self.login("admin")
        response = self.client.post(
            f"/api/chats/{chat['id']}/stream", json={"text": OBW_QUESTION}
        )
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("не найден", events[0]["error"])

    def test_stream_requires_login(self):
        chat = self.new_chat()
        self.client.cookies.clear()
        response = self.client.post(
            f"/api/chats/{chat['id']}/stream", json={"text": OBW_QUESTION}
        )
        self.assertEqual(response.status_code, 401)


class CabinetApiTests(AssistantHttpTestCase):
    def test_summary_shape(self):
        body = self.client.get("/api/me/summary").json()
        self.assertEqual(body["user"]["login"], "engineer")
        self.assertEqual(body["cases"], 0)
        self.assertEqual(body["reports"], {"total": 0, "approved": 0})
        self.assertEqual(body["edits"], {"pairs": 0, "mean_distance": 0.0})
        self.assertEqual(body["chats"], 0)

    def test_summary_counts_only_own_chats(self):
        self.new_chat()
        self.new_chat()
        self.assertEqual(self.client.get("/api/me/summary").json()["chats"], 2)
        self.login("admin")
        self.assertEqual(self.client.get("/api/me/summary").json()["chats"], 0)

    def test_summary_counts_own_cases_and_reports(self):
        case = self.client.post(
            "/api/cases",
            json={"report_type": CASE["report_type"], "case_id": CASE["case_id"], "facts": CASE},
        ).json()["case"]
        self.client.post(f"/api/cases/{case['id']}/generate", json={})
        body = self.client.get("/api/me/summary").json()
        self.assertEqual(body["cases"], 1)
        self.assertEqual(body["reports"]["total"], 1)

    def test_password_change_succeeds_and_takes_effect(self):
        response = self.client.post(
            "/api/me/password", json={"current": "пароль123", "new": "новыйпароль456"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(self.client.get("/api/chats").status_code, 200)

        self.client.cookies.clear()
        self.assertEqual(
            self.client.post(
                "/api/auth/login", json={"login": "engineer", "password": "пароль123"}
            ).status_code,
            401,
        )
        self.login("engineer", "новыйпароль456")

    def test_password_change_with_wrong_current_is_rejected(self):
        response = self.client.post(
            "/api/me/password", json={"current": "не тот", "new": "новыйпароль456"}
        )
        self.assertEqual(response.status_code, 403)
        self.login("engineer")

    def test_short_new_password_is_rejected(self):
        response = self.client.post(
            "/api/me/password", json={"current": "пароль123", "new": "1234567"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("8", response.json()["error"])

    def test_new_password_equal_to_old_is_rejected(self):
        response = self.client.post(
            "/api/me/password", json={"current": "пароль123", "new": "пароль123"}
        )
        self.assertEqual(response.status_code, 400)

    def test_password_change_requires_login(self):
        self.client.cookies.clear()
        response = self.client.post(
            "/api/me/password", json={"current": "пароль123", "new": "новыйпароль456"}
        )
        self.assertEqual(response.status_code, 401)


class DomainApiTests(AssistantHttpTestCase):
    def test_config_lists_domains_and_brand(self):
        body = self.client.get("/api/config").json()
        ids = {item["id"] for item in body["domains"]}
        self.assertIn("satellite", ids)
        self.assertIn("protocols", ids)
        self.assertEqual(set(body["brand"]), {"name", "subtitle", "accent", "logo"})

    def test_domains_endpoint_counts_documents(self):
        body = self.client.get("/api/domains").json()
        self.assertIn("measurement", {item["id"] for item in body["items"]})
        self.assertEqual(body["documents"]["measurement"], 1)
        self.assertEqual(body["documents"]["не указано"], 3)

    def test_library_filter_by_domain(self):
        body = self.client.get("/api/library", params={"domain": "measurement"}).json()
        self.assertEqual([item["doc_id"] for item in body["items"]], ["standards/obw-method"])
        self.assertGreater(len(self.client.get("/api/library").json()["items"]), 1)

    def test_document_domain_can_be_set(self):
        doc_id = "reports/2023-041-signal"
        response = self.client.put(f"/api/library/{doc_id}/domain", json={"domain": "microwave"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["document"]["domain"], "microwave")

        listed = self.client.get("/api/library", params={"domain": "microwave"}).json()["items"]
        self.assertEqual([item["doc_id"] for item in listed], [doc_id])
        chat = self.new_chat(domain="microwave")
        answer = self.client.post(
            f"/api/chats/{chat['id']}/ask", json={"text": "Что было в отчёте по деградации?"}
        ).json()["answer"]
        self.assertTrue(answer["sources"])
        self.assertEqual({source["domain"] for source in answer["sources"]}, {"microwave"})

    def test_unknown_document_domain_is_rejected(self):
        response = self.client.put(
            "/api/library/standards/obw-method/domain", json={"domain": "телепатия"}
        )
        self.assertEqual(response.status_code, 400)

    def test_domain_of_missing_document_is_404(self):
        response = self.client.put(
            "/api/library/нет/такого/domain", json={"domain": "microwave"}
        )
        self.assertEqual(response.status_code, 404)

    def test_search_reports_degradation_warning(self):
        body = self.client.get("/api/search", params={"q": "занимаемая полоса"}).json()
        self.assertIn("warning", body)
        self.assertTrue(body["items"])
        self.assertIn("domain", body["items"][0])


if __name__ == "__main__":
    unittest.main()
