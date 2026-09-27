"""«Загрузите документ» — совет, который система обязана проверить.

Отдел, вопрос по SDH: «опять внизу: загрузите документы RFC и ITU-T, которые
уже лежат в библиотеке, значит он не полностью всё захватывает».

Причина нашлась в самой инструкции помощнику. Правило 3 гласило: «Нет данных
— скажи, каких именно не хватает и какой документ стоит загрузить в
библиотеку». Модель это и делала. А знать, чего в библиотеке нет, она не
может: 19 608 документов, в окно уходит выборка описи на несколько десятков
строк, и об этом там же написано прямо.

Отсюда два правила, и оба здесь проверяются.

1. Советовать загрузку модели больше не поручено. Сказать, какой документ
   нужен, — можно и нужно; утверждать, что его нет, — только по сверке.
2. Обозначения из ОТВЕТА сверяются с описью так же, как обозначения из
   вопроса. Если названный в ответе документ в библиотеке есть, инженер
   видит это прямо под ответом — вместе с опознавателем, чтобы открыть.
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

СЦБ = (
    "Синхронная цифровая иерархия. Сетевые узлы и стыки. Мультиплексирование "
    "и структура кадра STM-1. Виртуальные контейнеры и указатели. "
) * 6

ВОПРОС = "Как устроен кадр STM-1 в SDH?"


class Библиотека(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"))
        self.repos = Repositories(Database(":memory:"))
        # На полке лежат ровно те документы, которые ответ посоветует
        # загрузить. Поиск их не приносит: вопрос по-русски, документы
        # английские, смыслового поиска в этой сборке нет.
        self.положить("standards/T-REC-G.707",
                      "ITU-T Rec. G.707 Network node interface for the "
                      "synchronous digital hierarchy (SDH)",
                      "Network node interface. STM-N frame structure. " * 20)
        self.положить("rfc/rfc3591",
                      "RFC 3591 Definitions of Managed Objects for the "
                      "Optical Interface Type",
                      "Managed objects for optical interfaces. " * 20)
        self.положить("lib/sdh", "Цифровые системы группообразования", СЦБ)
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
            chunk_id=f"{doc_id}#0", doc_id=doc_id, doc_type="standards",
            title_path=[title], text=текст, meta={})])

    def ответить(self, текст_ответа, вопрос=ВОПРОС):
        class Свой(StubLLM):
            def complete(self, system, user, **kwargs):
                return текст_ответа

        self.reports.llm = Свой()
        return self.assistant.ask(self.user, self.chat.id, вопрос)["answer"]


class СоветЗагрузитьПроверяется(Библиотека):
    def test_названное_в_ответе_и_лежащее_на_полке_показано(self):
        """Дословно тот совет, который отдел увидел внизу ответа."""
        answer = self.ответить(
            "Кадр STM-1 описан в [S1]. Для полного разбора загрузите в "
            "библиотеку ITU-T G.707 и RFC 3591.")
        есть = {item["doc_id"] for item in answer["meta"]["present"]}
        self.assertIn("standards/T-REC-G.707", есть)
        self.assertIn("rfc/rfc3591", есть)

    def test_названы_с_опознавателем_и_названием(self):
        """Без них строка бесполезна: открыть документ будет нечем."""
        answer = self.ответить("Загрузите ITU-T G.707.")
        строка = answer["meta"]["present"][0]
        self.assertEqual("standards/T-REC-G.707", строка["doc_id"])
        self.assertIn("G.707", строка["title"])
        self.assertEqual("G.707", строка["designation"])

    def test_просьба_достать_прочитанное_замечена(self):
        """«Нужен стандарт ITU-T G.707» — так пишут чаще, чем «нужен документ».

        Документ при этом модель прочитала: он в материале. Требовать
        достать то, что сам же и процитировал, — ошибка грубее первой, и в
        числах метрик она должна быть видна.
        """
        prepared = self.assistant._prepare(self.user, self.chat.id,
                                           "что в G.707", top_k=None)
        self.assertIn("standards/T-REC-G.707",
                      {item["doc_id"] for item in prepared["sources"]},
                      "образец подобран неудачно: документ не прочитан")
        названо = self.assistant._named_in_answer(
            "Для полного разбора нужен стандарт ITU-T G.707.",
            prepared["sources"])
        self.assertEqual(["standards/T-REC-G.707"],
                         [item["doc_id"] for item in названо])

    def test_прочитанное_моделью_второй_раз_не_показывают(self):
        """Документ уже в материале — сообщать о нём как о находке незачем."""
        prepared = self.assistant._prepare(self.user, self.chat.id,
                                           "что в G.707", top_k=None)
        self.assertIn("standards/T-REC-G.707",
                      {item["doc_id"] for item in prepared["sources"]},
                      "образец подобран неудачно: документ не в материале")
        готово = self.assistant._finish(self.user, prepared,
                                        "Смотри G.707 и [S1].")
        self.assertEqual([], готово["answer"]["meta"]["present"])

    def test_чего_в_библиотеке_нет_не_обещаем(self):
        answer = self.ответить("Нужен ещё RFC 9999 и ГОСТ Р 99999.")
        self.assertEqual([], answer["meta"]["present"])

    def test_ответ_без_обозначений_ничего_не_добавляет(self):
        answer = self.ответить("Кадр STM-1 состоит из секционного заголовка.")
        self.assertEqual([], answer["meta"]["present"])

    def test_два_обозначения_одного_документа_названы_один_раз(self):
        """У МСЭ и ИСО на один документ бывает два обозначения.

        «ITU-T Rec. X.680 | ISO/IEC 8824-1» — это ОДИН том, и в списке он
        должен стоять один раз, а не двумя строками об одной и той же полке.
        """
        self.положить("standards/T-REC-X.680",
                      "ITU-T Rec. X.680 | ISO/IEC 8824-1 Abstract Syntax "
                      "Notation One (ASN.1)",
                      "Abstract syntax notation. " * 20)
        answer = self.ответить("Загрузите X.680 и ISO/IEC 8824-1.")
        найдено = [item["doc_id"] for item in answer["meta"]["present"]]
        self.assertIn("standards/T-REC-X.680", найдено)
        self.assertEqual(len(найдено), len(set(найдено)),
                         f"один документ назван дважды: {найдено}")

    def test_снятого_с_полки_документа_не_обещаем(self):
        """Карта библиотеки живёт в памяти дольше, чем сама библиотека.

        Опознать документ можно по КЭШУ описи, а к моменту ответа его на
        полке уже нет: библиотеку правят, пока идёт разговор. Обещать
        инженеру «откройте, он есть» в этом случае нельзя — открывать нечего.

        Кэш описи сам про это знает и сбрасывается по отпечатку из трёх
        чисел. Но отпечаток — не доказательство, и в самом кэше об этом
        написано прямо: «два числа совпали случайно (документ убрали, другой
        добавили)». Здесь собран ровно такой случай: один том снят, другой
        поставлен, названия одной длины, фрагмент у каждого один — все три
        числа сошлись, и карта осталась прежней.
        """
        # Первый заход строит карту библиотеки и кладёт её в кэш.
        self.assistant._prepare(self.user, self.chat.id, ВОПРОС, top_k=None)
        снимаем = self.repos.documents.by_doc_id("standards/T-REC-G.707")
        длина = len(снимаем.title)
        with self.repos.db.transaction() as connection:
            connection.execute("DELETE FROM documents WHERE id = ?",
                               (снимаем.id,))
        замена = "Ведомость эксплуатационных измерений тракта"
        self.положить("lib/vedomost", замена.ljust(длина, "."),
                      "Ведомость измерений. " * 20)

        self.assertEqual("standards/T-REC-G.707",
                         self.assistant._resolve_document("G.707"),
                         "образец подобран неудачно: кэш описи обновился, "
                         "и устаревшей карты, ради которой всё, здесь нет")
        self.assertEqual([], self.assistant._named_in_answer(
            "Загрузите ITU-T G.707.", []))


class ИнструкцияБольшеНеВелитСоветоватьЗагрузку(unittest.TestCase):
    """Правило 3 само порождало ложный совет — его надо было менять.

    Проверяем не букву формулировки, а её смысл: инструкция не должна
    поручать модели рассуждать о том, чего в библиотеке нет.
    """

    def test_загрузку_в_библиотеку_советовать_не_велено(self):
        for оборот in ("стоит загрузить в библиотеку",
                       "загрузить в библиотеку"):
            with self.subTest(оборот=оборот):
                self.assertNotIn(оборот, ASSISTANT_SYSTEM_PROMPT)

    def test_сказать_какого_документа_не_хватает_по_прежнему_велено(self):
        """Убрать надо было ложный совет, а не полезную часть правила."""
        self.assertIn("НЕ ВЫДУМЫВАЙ", ASSISTANT_SYSTEM_PROMPT)
        self.assertIn("каких именно не хватает", ASSISTANT_SYSTEM_PROMPT)

    def test_инструкция_не_диктует_фразу_про_пустую_библиотеку(self):
        """Правило 2 предписывало эту фразу ДОСЛОВНО, в кавычках.

        Отдел: «в конце пишет, что нет стандарта ITU-T G.703; проверяю в
        библиотеке — он конечно же есть и на месте». Модель не выдумывала
        оборот, ей его продиктовали: «сначала строка "В библиотеке ответа
        нет."». Остальные правила это же и запрещали — инструкция спорила
        сама с собой, а побеждала та её часть, где фраза стояла в кавычках.

        Разница существенная: «в присланных фрагментах» — про выдачу поиска,
        и говорить так можно всегда; «в библиотеке» — про тридцать тысяч
        документов, и это разрешено только по сверке с описью.
        """
        self.assertIn("В присланных фрагментах ответа нет",
                      ASSISTANT_SYSTEM_PROMPT)
        self.assertNotIn("«В библиотеке ответа нет", ASSISTANT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
