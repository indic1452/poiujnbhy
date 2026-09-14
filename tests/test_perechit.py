# -*- coding: utf-8 -*-
"""Помощник дочитывает сам и рассказывает, чем занят.

Отдел, глядя на живой ответ: «даже в конце ссылки выдаёт, а модель говорит,
что нет документов». И следом, о том списке ссылок: «не надо мне документы
эти показывать, нужно чтобы модель сама всё видела, анализировала и давала
ответ со ссылками на эти документы, я же для этого модель использую, чтобы
она полностью анализ и поиск выполняла».

Возражение справедливое. Список под ответом перекладывал работу обратно на
инженера: вот документы, которые нужны были ответу, — открывайте сами.
Теперь иначе.

СНАЧАЛА — заход по названию, ДО всякого ответа и на каждом вопросе: у
модели спрашивают, в каких стандартах это описано, сверяют с описью и
подкладывают найденное. Это и есть «сразу всё проанализировать».

ПОТОМ — сеть: если ответ всё-таки назвал документ, который лежит на полке и
прочитан не был, это не ответ, а заявка на материал. Заявку исполняют:
подкладывают названное и пишут ответ заново. Один раз — иначе во втором
ответе модель назовёт третий документ, и так до вечера.

И третье: «добавь, чтобы был виден процесс — что делает в данный момент
модель, в очереди ответа или что». Сбор материала идёт минуты, и всё это
время на экране не происходило ничего.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.llm import StubLLM
from reportgen.prompts import ASSISTANT_SYSTEM_PROMPT
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]

#: Вопрос отдела с живого экрана.
ВОПРОС = "расскажи все про уплотнение e1, его структуру позиции и таймслоты"


class Библиотека(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"))
        self.repos = Repositories(Database(":memory:"))
        self.положить(
            "standards/T-REC-G.703",
            "ITU-T Rec. G.703 Physical/electrical characteristics of "
            "hierarchical digital interfaces",
            "Interface at 2048 kbit/s. Line code HDB3. ")
        self.положить(
            "standards/T-REC-G.704",
            "ITU-T Rec. G.704 Synchronous frame structures used at 1544 and "
            "2048 kbit/s hierarchical levels",
            "Basic frame of 256 bits. Time slot 0 carries the frame "
            "alignment signal. Time slot 16 carries signalling. ")
        # То, что нашёл поиск у отдела: паспорта аппаратуры, не стандарты.
        self.положить("lib/mux", "Мультиплексоры SN10.M1 и EDMAC874",
                      "Статический мультиплексор. Кадр 1280 бит. "
                      "Служебные биты 22-29, 37-45, 312-319. ")
        # Соседняя рекомендация: по словам вопроса поиск до неё не
        # дотягивается — ни «уплотнение», ни «таймслоты» в ней не стоят.
        self.положить(
            "standards/T-REC-G.706",
            "ITU-T Rec. G.706 Frame alignment and cyclic redundancy check "
            "procedures",
            "Frame alignment procedures. CRC-4 procedure. ")
        self.reports = ReportService(repos=self.repos, settings=self.settings,
                                     llm=StubLLM())
        self.assistant = AssistantService(reports=self.reports)
        self.user = self.repos.users.create("ivanov", "пароль123", "Иванов",
                                            "engineer")
        self.chat = self.assistant.create_chat(self.user)

    def положить(self, doc_id, title, текст):
        документ = self.repos.documents.upsert(
            doc_id, "standards", title, f"/{doc_id}.pdf",
            "sha-" + doc_id[-10:], meta={}, domain="other")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"{doc_id}#{n}", doc_id=doc_id, doc_type="standards",
            title_path=[title], text=текст * 20, meta={}) for n in range(3)])


class МодельНазываетНужноеСама(Библиотека):
    """Заход по названию — ДО ответа, а не после."""

    def setUp(self):
        super().setUp()
        ответы = self.ответы = []

        class Знающая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "G.703, G.704"
                # Считаем ТОЛЬКО сам ответ: помощник обращается к модели и
                # за обозначениями, и за планом поиска, и за выписками.
                if system.startswith(ASSISTANT_SYSTEM_PROMPT[:40]):
                    ответы.append(user)
                return "Структура цикла описана в [S1]."

        self.reports.llm = Знающая()

    def test_стандарты_попали_в_материал_до_ответа(self):
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        документы = {item["doc_id"] for item in prepared["sources"]}
        self.assertIn("standards/T-REC-G.703", документы)
        self.assertIn("standards/T-REC-G.704", документы)

    def test_один_ответ_а_не_два(self):
        """Раз материал собран сразу — перечитывать нечего."""
        self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertEqual(1, len(self.ответы), "модель отвечала дважды")

    def test_заход_виден_в_ходе_разбора(self):
        след = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                       top_k=None)["trail"]
        self.assertTrue([с for с in след if "по названию" in с], след)


class ЗаявкуНаМатериалИсполняют(Библиотека):
    """Заход промахнулся, а ответ назвал документ — дочитываем сами."""

    def setUp(self):
        super().setUp()
        заходы = self.заходы = []

        class Забывчивая(StubLLM):
            """Обозначений заранее не назовёт, а в ответе — назовёт."""

            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if system.startswith(ASSISTANT_SYSTEM_PROMPT[:40]):
                    заходы.append(user)
                if "T-REC-G.706" in user:
                    return "Сверхцикл CRC-4 описан так: [S1]."
                return ("В присланных фрагментах ответа нет. Для проверки "
                        "цикловой синхронизации нужен стандарт ITU-T G.706.")

        self.reports.llm = Забывчивая()

    def test_ответ_переписан_по_настоящему_документу(self):
        готово = self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertEqual(2, len(self.заходы), "перечитывания не было")
        self.assertIn("Сверхцикл CRC-4", готово["answer"]["content"])
        self.assertNotIn("нужен стандарт", готово["answer"]["content"],
                         "черновик по неполному материалу остался в разговоре")

    def test_в_разговоре_один_вопрос_и_один_ответ(self):
        self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        сообщения = self.assistant.messages(self.user, self.chat.id)
        роли = [m.role for m in сообщения]
        self.assertEqual(["user", "assistant"], роли, роли)

    def test_перечитываем_один_раз(self):
        """Иначе во втором ответе назовут третий документ, и так до вечера."""
        class Бесконечная(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                return "Нужен ещё стандарт ITU-T G.706 и G.703."

        self.reports.llm = Бесконечная()
        # Не зависает и завершается: важен сам факт возврата.
        готово = self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertTrue(готово["answer"]["content"])

    def test_перечитывание_можно_выключить(self):
        self.settings.assistant_reread = 0
        self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertEqual(1, len(self.заходы))

    def test_прочитанное_второй_раз_не_перечитываем(self):
        """Документ уже в материале — второй заход даст то же самое.

        Это была бы не доработка ответа, а вторая попытка наугад: тот же
        материал, та же модель, лишние минуты ожидания.
        """
        class Упрямая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                return "Структура цикла описана в ITU-T G.704 [S1]."

        self.reports.llm = Упрямая()
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        self.assertIn("standards/T-REC-G.704",
                      {i["doc_id"] for i in prepared["sources"]},
                      "образец подобран неудачно: документ и так не прочитан")
        self.assertEqual([], self.assistant._to_reread(
            "Структура цикла описана в ITU-T G.704 [S1].", prepared))

    def test_дошедшее_выпиской_перечитываем(self):
        """Выписка — пересказ на триста слов, а не текст документа.

        Если ответ просит документ, который дошёл до модели ВЫПИСКОЙ,
        значит пересказа не хватило. Перечитывание кладёт такой документ
        поимённо, и приходит он фрагментами целиком — ровно то, чего ответу
        недостало. Считать выписку за прочитанное значит отказывать в
        единственном, что здесь может помочь.
        """
        prepared = {
            "sources": [{"doc_id": "lib/mux", "label": "S1"}],
            "digest_sources": [{"doc_id": "standards/T-REC-G.704",
                                "label": "S2"}],
        }
        self.assertEqual(
            ["standards/T-REC-G.704"],
            self.assistant._to_reread("Нужен стандарт ITU-T G.704.", prepared))

    def test_прочитанное_текстом_не_перечитываем(self):
        prepared = {
            "sources": [{"doc_id": "standards/T-REC-G.704", "label": "S1"}],
            "digest_sources": [],
        }
        self.assertEqual(
            [], self.assistant._to_reread("Нужен стандарт ITU-T G.704.",
                                          prepared))

    def test_списка_документов_под_ответом_больше_нет(self):
        """Отдел: «не надо мне документы эти показывать».

        Число осталось в метриках — по нему видно, где помощник работает
        вхолостую, — но работу инженеру обратно не перекладываем.
        """
        готово = self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertEqual([], готово["answer"]["meta"]["present"],
                         "ответу всё ещё не хватает документов после "
                         "перечитывания")


class ПотокомТожеПеречитываем(Библиотека):
    """В потоке черновик заменяется, а не дописывается."""

    def setUp(self):
        super().setUp()
        self.положить(
            "standards/T-REC-G.706",
            "ITU-T Rec. G.706 Frame alignment and cyclic redundancy check "
            "procedures", "Frame alignment procedures. CRC-4 procedure. ")

        class Забывчивая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if "T-REC-G.706" in user:
                    return "Сверхцикл CRC-4 описан так [S1]."
                return "Нужен стандарт ITU-T G.706."

        self.reports.llm = Забывчивая()

    def события(self):
        return list(self.assistant.ask_stream(self.user, self.chat.id, ВОПРОС))

    def test_черновик_убирается_с_экрана(self):
        события = self.события()
        виды = [с["type"] for с in события]
        self.assertIn("restart", виды, "перечитывания в потоке не было")
        # Всё, что пришло ДО перезапуска, — черновик по неполному
        # материалу. Инженеру он не нужен, и в ответе его быть не должно.
        после = виды.index("restart")
        текст = "".join(с["text"] for с in события[после:]
                        if с["type"] == "delta")
        self.assertIn("Сверхцикл CRC-4", текст)
        self.assertEqual(текст.strip(), события[-1]["answer"]["content"],
                         "в разговор попал черновик вместе с ответом")

    def test_вопрос_в_разговоре_один(self):
        """Перечитывание не должно задваивать сообщение инженера."""
        self.события()
        роли = [m.role for m in self.assistant.messages(self.user, self.chat.id)]
        self.assertEqual(["user", "assistant"], роли, роли)

    def test_перечитывание_названо_в_ходе_работы(self):
        этапы = [с["text"] for с in self.события() if с["type"] == "stage"]
        self.assertTrue([э for э in этапы if "заново" in э], этапы)


class ВыбираемДокументАНеПоправкуКНему(Библиотека):
    """У одной рекомендации рядом лежат сама она и поправка к ней.

    Отдел прислал снимок: на вопрос про E1 система подложила «ITU-T Rec.
    G.703 Amendment 1 (05/2021)» — поправку на 69 фрагментов вместо самой
    рекомендации. Прежде побеждал ПЕРВЫЙ в описи, а опись отсортирована по
    названию: выбор решал алфавит.
    """

    def setUp(self):
        super().setUp()
        self.положить(
            "standards/T-REC-G.703-amd1",
            "ITU-T Rec. G.703 Amendment 1 (05/2021) Physical/Electrical "
            "Characteristics of Hierarchical Digital Interfaces Amendment 1",
            "Amendment 1. Clause 9 is replaced by the following. ")
        self.положить(
            "standards/T-REC-G.703-corr1",
            "ITU-T Rec. G.703 Corrigendum 1 Physical/electrical "
            "characteristics of hierarchical digital interfaces Corrigendum 1",
            "Corrigendum 1. Editorial corrections. ")

    def test_открываем_саму_рекомендацию(self):
        self.assertEqual("standards/T-REC-G.703",
                         self.assistant._resolve_document("G.703"))

    def test_поправка_открывается_если_её_и_просят(self):
        """Отказываться от поправки нельзя — её тоже спрашивают поимённо."""
        self.assertEqual(
            "standards/T-REC-G.703-amd1",
            self.assistant._resolve_document("T-REC-G.703-amd1"))

    def test_когда_самой_рекомендации_нет_берём_что_есть(self):
        """Поправка лучше, чем ничего: ответить «не числится» было бы ложью.

        Какая именно из двух прибавок — неважно и намеренно не закреплено:
        обе про G.703, и обе лучше пустоты.
        """
        self.repos.documents.delete("standards/T-REC-G.703")
        self.assertIn(self.assistant._resolve_document("G.703"),
                      ("standards/T-REC-G.703-amd1",
                       "standards/T-REC-G.703-corr1"))


class ВиденХодРаботы(Библиотека):
    """«Добавь, чтобы был виден процесс — что делает модель прямо сейчас»."""

    def setUp(self):
        super().setUp()

        class Знающая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "G.703, G.704"
                return "Ответ [S1]."

        self.reports.llm = Знающая()

    def события(self):
        return list(self.assistant.ask_stream(self.user, self.chat.id, ВОПРОС))

    def test_этапы_приходят_до_первого_куска_текста(self):
        события = self.события()
        виды = [с["type"] for с in события]
        self.assertIn("stage", виды)
        self.assertLess(виды.index("stage"), виды.index("delta"),
                        "этапы пришли после текста — показывать нечего")

    def test_названы_все_долгие_места(self):
        """Каждое из них — это секунды и минуты тишины на экране."""
        этапы = " | ".join(с["text"] for с in self.события()
                           if с["type"] == "stage")
        for место in ("ищу по библиотеке", "в каких стандартах",
                      "по ссылкам", "жду ответа модели"):
            with self.subTest(место=место):
                self.assertIn(место, этапы)

    def test_сбор_материала_отчитывается_числами(self):
        этапы = [с["text"] for с in self.события() if с["type"] == "stage"]
        self.assertTrue([э for э in этапы if "нашлось фрагментов" in э], этапы)

    def test_обычный_вызов_этапов_не_плодит(self):
        """`_prepare` зовут отовсюду — этапы там не нужны и не мешают."""
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        self.assertTrue(prepared["sources"])


if __name__ == "__main__":
    unittest.main()
