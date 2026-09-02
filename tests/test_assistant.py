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


def fill_library(repos, *, domain: str = "signal"):
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
    library_domain = "signal"
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


class WindowCeilingTests(AssistantTestCase):
    """Промпт обязан помещаться в окно модели — целиком, а не по частям.

    assistant_context_chars назывался «жёсткой границей», но держал только
    блок источников: материалу гарантирован пол в MIN_LIBRARY_CHARS знаков,
    и когда вложения с разговором занимали больше окна, источники упирались
    в пол, а промпт всё равно вылезал. Замер на вложении в 40 000 знаков
    давал промпт 47 678 знаков при границе 26 000 — llama.cpp в этом месте
    молча выбрасывает начало промпта вместе с системной инструкцией, и
    модель перестаёт ставить ссылки.
    """

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)

    QUESTION = "Как измеряется занимаемая полоса частот?"
    #: Текст «дампа» из настоящих слов: иначе поиск по нему ничего не найдёт
    #: и проверять окажется нечего.
    DUMP = "занимаемая полоса частот спектр периодограмма сегмент окно "

    def prompt_with(self, attachment_chars, history_turns=0):
        self.settings.assistant_attachment_chars = attachment_chars
        for _ in range(history_turns):
            self.repos.chats.add_message(self.chat.id, "user", "в" * 1500)
            self.repos.chats.add_message(self.chat.id, "assistant", "о" * 1500)
        if attachment_chars:
            body = self.DUMP * (attachment_chars // len(self.DUMP) * 2 + 10)
            self.repos.chats.add_attachment(
                self.chat.id, name="дамп.txt", size=len(body),
                text=body, kind="text")
        prepared = self.assistant._prepare(
            self.ivanov, self.chat.id, self.QUESTION, top_k=None)
        return prepared["prompt"]

    def catalog_of(self, prompt):
        head = prompt.split("### ЧТО ЕСТЬ В БИБЛИОТЕКЕ ОТДЕЛА", 1)
        return head[1].split("### ЧТО НАШЛОСЬ", 1)[0] if len(head) > 1 else ""

    def test_a_huge_attachment_does_not_burst_the_window(self):
        window = self.settings.assistant_context_chars
        prompt = self.prompt_with(40000)
        self.assertLessEqual(
            len(prompt), window + 4000,
            f"промпт {len(prompt)} знаков при окне {window}")

    def test_a_long_conversation_does_not_burst_the_window(self):
        window = self.settings.assistant_context_chars
        prompt = self.prompt_with(8000, history_turns=3)
        self.assertLessEqual(len(prompt), window + 4000)

    def test_the_sources_survive_even_at_the_ceiling(self):
        # Резать до последнего фрагмента нельзя: без источников помощник
        # превращается в обычную модель без ссылок на нормы.
        self.assertIn("[S1]", self.prompt_with(40000))

    def test_the_truncation_of_the_file_is_admitted(self):
        # Молча показать модели первую тысячу знаков дампа нельзя: она
        # сделает вывод «ошибок больше нет» по обрезанному хвосту.
        self.assertIn("показано", self.prompt_with(40000))

    def test_the_map_gives_way_before_the_sources(self):
        """Карта полезна, но фрагменты важнее: её ужимают первой."""
        wide = self.assistant._prepare(
            self.ivanov, self.chat.id, self.QUESTION, top_k=None)["prompt"]
        narrow = self.prompt_with(40000)
        self.assertLess(len(self.catalog_of(narrow)), len(self.catalog_of(wide)))


class MaterialTests(AssistantTestCase):
    """Из чего собирается материал для модели.

    Пользователь просил, чтобы помощник «полностью знал содержимое файлов,
    несмотря на их размеры», сопоставлял источники и взаимодополнял их.
    Отвечает за это сборка промпта, а не сама модель, — её и проверяем.
    """

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)

    def prepared(self, question="Как измеряется занимаемая полоса частот?"):
        return self.assistant._prepare(self.ivanov, self.chat.id, question, top_k=None)

    def test_sources_are_grouped_by_document(self):
        # Фрагменты одного документа идут подряд: сопоставить стандарт
        # с паспортом микросхемы можно, только когда они не перемешаны.
        prepared = self.prepared()
        block = prepared["prompt"].split("### ИСТОЧНИКИ", 1)[1]
        seen, order = set(), []
        for line in block.splitlines():
            if line.startswith("— — — ДОКУМЕНТ:"):
                order.append(line)
        self.assertGreater(len(order), 1, "в выдаче ожидается несколько документов")
        for line in order:
            self.assertNotIn(line, seen, "документ встретился в промпте дважды")
            seen.add(line)

    def test_prompt_carries_document_map_with_outline(self):
        prepared = self.prepared()
        self.assertIn("### ЧТО НАШЛОСЬ ПО ЭТОМУ ВОПРОСУ", prepared["prompt"])
        self.assertIn("Разделы документа:", prepared["prompt"])
        self.assertTrue(prepared["documents"])
        card = prepared["documents"][0]
        self.assertTrue(card["labels"], "у документа не перечислены его фрагменты")
        self.assertTrue(card["outline"], "у документа нет оглавления")

    def test_document_card_names_type_year_and_status(self):
        # Модель должна отличать действующий стандарт от заменённого,
        # иначе в отчёт уедет отменённая норма.
        text = self.prepared()["prompt"]
        self.assertIn("действующий", text)
        self.assertRegex(text, r"— (литература|стандарт|прошлый отчёт|регламент)")

    def test_neighbours_are_added_but_not_duplicated(self):
        # Сосед, который и сам попал в выдачу, второй раз не нужен: тот же
        # текст занимал бы окно дважды.
        prepared = self.prepared()
        texts = [item["text"] for item in prepared["sources"]]
        for item in prepared["sources"]:
            for extra in (item["lead"], item["tail"]):
                if not extra:
                    continue
                for text in texts:
                    self.assertNotEqual(
                        extra.strip(), text.strip(),
                        "соседний фрагмент повторяет отдельный источник")

    def test_context_budget_is_enforced(self):
        # Переполненное окно llama.cpp обрезает молча — вместе с системной
        # инструкцией. Материал обязан укладываться в бюджет сам.
        wide = self.prepared()
        self.settings.assistant_context_chars = 900
        narrow = self.prepared()
        self.assertLess(len(narrow["sources"]), len(wide["sources"]),
                        "узкий бюджет обязан отсечь часть выдачи")
        material = sum(len(item["text"]) + len(item["lead"]) + len(item["tail"])
                       for item in narrow["sources"])
        # Один фрагмент кладём всегда, даже если он один длиннее бюджета:
        # иначе отвечать будет вовсе не на чем.
        biggest = max(len(item["text"]) for item in narrow["sources"])
        self.assertLessEqual(material, 900 + biggest)
        self.assertTrue(narrow["sources"], "бюджет не должен оставлять пустую выдачу")
        # Метки идут подряд с первой: пропуск в нумерации читается как потеря.
        self.assertEqual(
            [item["label"] for item in narrow["sources"]],
            [f"S{index}" for index in range(1, len(narrow["sources"]) + 1)])

    def test_tiny_budget_still_gives_one_source(self):
        self.settings.assistant_context_chars = 1
        prepared = self.prepared()
        self.assertEqual(1, len(prepared["sources"]))

    def test_previous_neighbour_is_cut_from_the_far_side(self):
        """Сосед слева обрезается спереди, а не сзади.

        Он примыкает к найденному фрагменту своим концом и в нём же
        продолжается. Обрезка с конца выбрасывала именно стык — то самое
        начало таблицы, ради которого соседа и берут.
        """
        # Соседу отводится половина меры фрагмента — текст обязан быть длиннее,
        # иначе обрезки не будет вовсе и проверять станет нечего.
        self.settings.assistant_source_chars = 600
        long_lead = "далёкое начало раздела. " * 40 + "СТЫК С НАЙДЕННЫМ ФРАГМЕНТОМ"

        class Neighbour:
            def __init__(self, chunk_id, text):
                self.chunk_id, self.text = chunk_id, text

        original = self.repos.chunks.neighbours

        def fake(anchors, radius):
            anchor = list(anchors)[0]
            doc, _, number = anchor.rpartition("#")
            before = (f"{doc}#{int(number) - 1:04d}a" if number.isdigit() else "a#0000")
            return {anchor: [Neighbour(before, long_lead)]}

        self.repos.chunks.neighbours = fake
        try:
            prepared = self.prepared()
        finally:
            self.repos.chunks.neighbours = original

        leads = [item["lead"] for item in prepared["sources"] if item["lead"]]
        self.assertTrue(leads, "сосед слева не попал в источники")
        self.assertIn("СТЫК С НАЙДЕННЫМ ФРАГМЕНТОМ", leads[0])

    def test_dropped_fragments_are_not_reported_as_unfound(self):
        """«Найдено» — это найденное поиском, а не уцелевшее после обрезки.

        Раньше здесь стояло одно число, и молча выброшенные окном модели
        фрагменты выглядели ненайденными: инженер решал, что в библиотеке
        больше ничего и нет.
        """
        self.settings.assistant_context_chars = 900
        answer = self.assistant.ask(self.ivanov, self.chat.id,
                                    "Как измеряется занимаемая полоса частот?")["answer"]
        meta = answer["meta"]
        self.assertGreater(meta["found"], meta["shown"], "обрезки не случилось")
        self.assertLessEqual(meta["cited"], meta["shown"])

    def test_a_made_up_reference_is_not_counted_as_a_citation(self):
        """Модель пишет [S9], когда фрагментов пять. Это не источник."""
        class Inventive(StubLLM):
            def complete(self, system, user, **kwargs):
                return "Ответ со ссылкой [S1] и с выдуманной [S99]."

        self.reports.llm = Inventive()
        answer = self.assistant.ask(self.ivanov, self.chat.id, "полоса частот")["answer"]
        self.assertEqual(1, answer["meta"]["cited"])
        self.assertEqual(["S1"], [item["label"] for item in answer["sources"]])

    def test_neighbours_can_be_switched_off(self):
        self.settings.assistant_neighbours = 0
        prepared = self.prepared()
        self.assertTrue(all(not item["lead"] and not item["tail"]
                            for item in prepared["sources"]))

    def test_outline_can_be_switched_off(self):
        self.settings.assistant_outlines = False
        prepared = self.prepared()
        self.assertNotIn("Разделы документа:", prepared["prompt"])
        self.assertTrue(all(not card["outline"] for card in prepared["documents"]))

    def test_assistant_asks_for_more_sources_than_the_report(self):
        # В отчёте материал ограничен факт-пакетом, в разговоре — только
        # вопросом, поэтому фрагментов берём больше.
        self.assertGreater(self.settings.assistant_top_k, self.settings.retrieval_top_k)

    def test_panel_does_not_get_neighbour_text(self):
        # Соседи нужны модели, инженеру в панели источников они ни к чему:
        # документ открывается целиком одним нажатием.
        self.assistant.ask(self.ivanov, self.chat.id, "Занимаемая полоса?")
        messages = self.assistant.messages(self.ivanov, self.chat.id)
        for item in messages[-1].sources:
            self.assertNotIn("lead", item)
            self.assertNotIn("tail", item)


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
        # Написанное до обрыва сохранено и помечено прерванным — ровно как
        # при уходе инженера с вкладки. Терять его нельзя: на длинном ответе
        # это пять минут работы модели, и обрыв модели тут ничем не отличается
        # от обрыва браузера. Пометка нужна, чтобы обрубок не выглядел ответом.
        self.assertEqual([m.role for m in messages], ["user", "assistant"])
        answer = messages[-1]
        self.assertEqual(answer.content, "начало ответа")
        self.assertTrue(answer.meta.get("interrupted"))

    def test_model_failure_before_a_single_word_saves_nothing(self):
        chat = self.assistant.create_chat(self.ivanov)

        class DeadOnArrival(StubLLM):
            def stream(self, system, user, **kwargs):
                raise RuntimeError("модель не поднялась")
                yield ""      # pragma: no cover — генератор без yield не генератор

        self.reports.llm = DeadOnArrival()
        with self.assertRaises(RuntimeError):
            list(self.assistant.ask_stream(self.ivanov, chat.id, "вопрос"))
        # Сохранять нечего: пустой ответ в разговоре — это мусор, а не работа.
        messages = self.assistant.messages(self.ivanov, chat.id)
        self.assertEqual([m.role for m in messages], ["user"])


class NeighbourTrimTests(unittest.TestCase):
    """Обрезка соседних фрагментов документа."""

    def test_previous_fragment_keeps_the_end_next_to_the_hit(self):
        """У соседа СЛЕВА полезен хвост: он продолжается в найденном куске.

        Обрезка с конца (как у всех остальных фрагментов) оставляла дальний
        край и выбрасывала ровно то место, ради которого соседа и брали, —
        начало таблицы, обрывающейся в найденном фрагменте.
        """
        from reportgen.web.assistant import _tidy, _tidy_end

        text = "начало документа " * 20 + "ТАБЛИЦА ДОПУСКОВ начинается здесь"
        lead = _tidy_end(text, 60)
        self.assertLessEqual(len(lead), 61)
        self.assertIn("начинается здесь", lead)
        self.assertTrue(lead.startswith("…"), lead)
        # А обычная обрезка по-прежнему оставляет начало: соседу СПРАВА нужно оно.
        self.assertIn("начало документа", _tidy(text, 60))

    def test_short_neighbour_is_not_marked_as_cut(self):
        from reportgen.web.assistant import _tidy_end

        self.assertEqual("две строки", _tidy_end("две строки", 100))


class AttachmentTests(AssistantTestCase):
    """Дамп, снимок экрана и документ, приложенные к вопросу."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)

    def attach(self, name="dump.log", text="ERROR frame 118 checksum mismatch", kind="dump"):
        return self.repos.chats.add_attachment(
            self.chat.id, name, kind, size=len(text), text=text)

    def prepared(self, question="Что не так в дампе?"):
        return self.assistant._prepare(self.ivanov, self.chat.id, question, top_k=None)

    def test_attachment_text_reaches_the_prompt(self):
        self.attach()
        prompt = self.prepared()["prompt"]
        self.assertIn("### ПРИЛОЖЕННЫЕ ФАЙЛЫ", prompt)
        self.assertIn("dump.log", prompt)
        self.assertIn("checksum mismatch", prompt)

    def test_long_attachment_is_cut_and_says_so(self):
        # Дамп на десятки мегабайт в окно не влезет никогда. Молчать об
        # обрезке нельзя: модель сделает вывод «ошибок больше нет».
        self.settings.assistant_attachment_chars = 200
        self.attach(text="строка дампа " * 500)
        prompt = self.prepared()["prompt"]
        self.assertIn("показано 200 из", prompt)
        self.assertIn("файл показан не целиком", prompt)

    def test_attachment_leaves_room_for_the_library(self):
        # Вложение не должно вытеснить источники: без них помощник
        # превращается в обычную модель без ссылок на нормы.
        self.settings.assistant_attachment_chars = 40000
        self.attach(text=("Занимаемая полоса частот измеряется методом 99 процентов "
                          "мощности, спектр, модуляция, EVM. ") * 400)
        prepared = self.prepared("Что не так в дампе?")
        self.assertTrue(prepared["sources"], "библиотека вытеснена вложением")

    def test_ten_files_together_fit_the_same_limit(self):
        """Предел был на КАЖДЫЙ файл, и десять файлов выносили промпт за окно.

        Переполнение окна llama.cpp не сообщает: он молча выбрасывает начало
        промпта вместе с системной инструкцией, и модель перестаёт ставить
        ссылки на документы — то есть перестаёт быть помощником.
        """
        self.settings.assistant_attachment_chars = 1000
        for number in range(10):
            self.attach(name=f"dump-{number}.log", text=f"строка{number} " * 400)
        block, chars = self.assistant._attachment_block(
            self.repos.chats.attachments(self.chat.id, pending_only=True))
        self.assertLessEqual(chars, 1000 + 10 * 200, f"вложения заняли {chars} знаков")
        for number in range(10):
            self.assertIn(f"dump-{number}.log", block, "файл выпал из промпта целиком")

    def test_a_short_file_does_not_hold_room_it_cannot_use(self):
        # Поровну — но короткая записка не должна отнимать место у дампа.
        self.settings.assistant_attachment_chars = 1000
        self.attach(name="note.txt", text="перезвонить в понедельник")
        self.attach(name="dump.log", text="строка дампа " * 500)
        block, _ = self.assistant._attachment_block(
            self.repos.chats.attachments(self.chat.id, pending_only=True))
        self.assertIn("перезвонить в понедельник", block)
        # Дампу досталось всё, что не выбрала записка, а не ровно половина.
        self.assertIn("показано 9", block.split("dump.log")[1][:60])

    def test_words_for_the_search_are_taken_from_every_file(self):
        # Слова первого дампа выбирали всю норму, и второй файл на поиск не
        # влиял вовсе — а прикладывают их как раз затем, чтобы сопоставить.
        from reportgen.web.assistant import ATTACHMENT_KEYWORDS, _attachment_keywords

        self.attach(name="first.log", text=" ".join(f"альфа{i}" for i in range(200)))
        self.attach(name="second.log", text=" ".join(f"бета{i}" for i in range(200)))
        words = _attachment_keywords(
            self.repos.chats.attachments(self.chat.id, pending_only=True)).split()
        self.assertEqual(len(words), ATTACHMENT_KEYWORDS)
        self.assertTrue([w for w in words if w.startswith("альфа")])
        self.assertTrue([w for w in words if w.startswith("бета")], "второй файл не в запросе")

    def test_listing_attachments_does_not_read_the_dump(self):
        """Открытие разговора не тянет в память текст каждого дампа.

        Показать нужно имя, вид и длину. Текст файла — до сотен мегабайт,
        и читался он целиком ради одного числа.
        """
        self.attach(name="big.log", text="строка дампа " * 5000)
        listed = self.repos.chats.attachments(self.chat.id, with_text=False)
        self.assertEqual("", listed[0].text)
        # Длина при этом честная: её считает сама база.
        self.assertEqual(len("строка дампа " * 5000), listed[0].to_dict()["chars"])
        # А там, где текст нужен модели, он на месте.
        full = self.repos.chats.attachments(self.chat.id, pending_only=True)
        self.assertTrue(full[0].text.startswith("строка дампа"))

    def test_attachment_is_bound_to_the_question(self):
        item = self.attach()
        self.assertIsNone(item.message_id)
        self.assistant.ask(self.ivanov, self.chat.id, "Что не так?")
        again = self.repos.chats.attachment(item.id)
        self.assertIsNotNone(again.message_id, "вложение не привязано к вопросу")
        self.assertEqual([], self.repos.chats.attachments(self.chat.id, pending_only=True))

    def test_repeated_dump_lines_do_not_drown_the_question(self):
        # В логе одна строка повторяется сотнями. Если брать начало файла
        # как есть, запрос состоит из неё целиком, и поиск находит не то,
        # о чём спрашивали, а то, что чаще повторяется в файле.
        from reportgen.web.assistant import _attachment_keywords

        class Fake:
            text = "ERROR checksum mismatch\n" * 300

        keywords = _attachment_keywords([Fake()])
        self.assertEqual(["ERROR", "checksum", "mismatch"], keywords.split())

    def test_attachment_words_reach_the_search_query(self):
        # По вопросу «что тут не так?» без содержимого файла не найдётся
        # ничего: искать надо по кодам ошибок и именам полей из дампа.
        seen = {}

        class Spy(StubLLM):
            pass

        original = self.assistant._search

        def watch(chat, question, history, top_k, attachments=()):
            from reportgen.web.assistant import _attachment_keywords
            seen["query"] = question + " " + _attachment_keywords(attachments)
            return original(chat, question, history, top_k, attachments=attachments)

        self.assistant._search = watch
        self.attach(text="occupied bandwidth 99 percent power")
        self.prepared("Что тут не так?")
        self.assertIn("occupied bandwidth", seen["query"])

    def test_unreadable_attachment_does_not_break_the_answer(self):
        self.repos.chats.add_attachment(
            self.chat.id, "скан.pdf", "document", size=10, text="",
            note="в файле не нашлось текста")
        prompt = self.prepared()["prompt"]
        self.assertIn("текст извлечь не удалось", prompt)
        self.assertIn("в файле не нашлось текста", prompt)


class InterruptedStreamTests(AssistantTestCase):
    """Уход со вкладки во время ответа не должен стирать работу модели."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)

    def test_partial_answer_is_saved_when_client_disconnects(self):
        class Slow(StubLLM):
            def stream(self, system, prompt, **kwargs):
                yield "Первая часть ответа. "
                yield "Вторая часть ответа. "
                yield "Третья часть — до неё не дойдёт."

        self.reports.llm = Slow()
        events = self.assistant.ask_stream(self.ivanov, self.chat.id, "Полоса частот?")
        seen = []
        for event in events:
            seen.append(event["type"])
            if seen.count("delta") == 2:
                break                 # инженер ушёл со вкладки
        events.close()                # так Starlette закрывает поток

        messages = self.assistant.messages(self.ivanov, self.chat.id)
        self.assertEqual(2, len(messages), "ответ модели должен остаться в разговоре")
        answer = messages[-1]
        self.assertEqual("assistant", answer.role)
        self.assertIn("Первая часть", answer.content)
        self.assertIn("Вторая часть", answer.content)
        self.assertNotIn("Третья часть", answer.content)
        self.assertTrue(answer.meta.get("interrupted"), "ответ не помечен как прерванный")

    def test_completed_answer_is_not_marked_interrupted(self):
        events = list(self.assistant.ask_stream(self.ivanov, self.chat.id, "Полоса частот?"))
        self.assertEqual("done", events[-1]["type"])
        answer = self.assistant.messages(self.ivanov, self.chat.id)[-1]
        self.assertFalse(answer.meta.get("interrupted"))

    def test_disconnect_before_any_text_leaves_no_empty_answer(self):
        class Silent(StubLLM):
            def stream(self, system, prompt, **kwargs):
                return iter(())

        self.reports.llm = Silent()
        events = self.assistant.ask_stream(self.ivanov, self.chat.id, "Полоса частот?")
        next(events)                  # question
        events.close()
        messages = self.assistant.messages(self.ivanov, self.chat.id)
        self.assertEqual(1, len(messages), "пустой ответ сохранять незачем")


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
        chat = self.client.post("/api/chats", json={"domain": "signal"}).json()["chat"]
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

        filtered = self.client.get("/api/library", params={"domain": "signal"}).json()
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
