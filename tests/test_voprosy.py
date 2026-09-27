"""Свод вопросов: нужный документ доходит до модели при любой записи.

Отдел: «проверь все другие варианты и комбинации и вообще разные вопросы, у
меня скачано всё, всё должно работать, давай чтобы больше не возвращаться к
данным типам ошибок».

Тип ошибки один и тот же, а поводов было четыре, и все четыре разобраны
здесь на одной библиотеке:

1. «t-Rex-g.732.pdf» — обозначение без слова «рекомендация» впереди;
2. «в чем разница стандарта g 733 от 734» — серия через пробел, у второго
   номера серии нет вовсе;
3. «что такое E1 уплотнение и его структура» — обозначений нет НИ ОДНОГО,
   вопрос русский и словесный, документы английские;
4. «загрузите документы RFC и ITU-T» — про то, что лежит на полке.

Что проверяется и чего эта проверка НЕ доказывает. Библиотека здесь
настоящая: документы с английскими названиями и английским текстом, как в
выгрузке МСЭ. Поиск, словарь, сверка с описью, подкладывание по имени и по
ссылке работают по-настоящему. А вот знание модели («в каких стандартах
описан E1») подменено заглушкой: проверяется, что система ЭТИМ ЗНАНИЕМ
пользуется и сверяет его с описью, а не что живая модель знает верно.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]

#: Библиотека как в отделе: названия и опознаватели из выгрузки МСЭ, текст
#: английский, направление «прочее» — так оно в описи и стоит.
БИБЛИОТЕКА = [
    ("standards/T-REC-G.703",
     "ITU-T Rec. G.703 (11/2001) Physical/electrical characteristics of "
     "hierarchical digital interfaces",
     "Physical and electrical characteristics of hierarchical digital "
     "interfaces. Interface at 2048 kbit/s. Nominal impedance 120 ohms "
     "balanced. Line code HDB3. Pulse mask and jitter tolerance. "),
    ("standards/T-REC-G.704",
     "ITU-T Rec. G.704 (10/98) Synchronous frame structures used at 1544, "
     "6312, 2048, 8448 and 44736 kbit/s hierarchical levels",
     "Synchronous frame structures. Basic frame of 256 bits at 2048 kbit/s. "
     "Time slot 0 carries the frame alignment signal FAS. Time slot 16 "
     "carries channel associated signalling. Multiframe of 16 frames. "),
    ("standards/T-REC-G.732",
     "ITU-T Rec. G.732 (11/88) Characteristics of primary PCM multiplex "
     "equipment operating at 2048 kbit/s",
     "Characteristics of primary PCM multiplex equipment at 2048 kbit/s. "
     "Thirty speech channels. A-law companding. Frame structure per "
     "Recommendation G.704. "),
    ("standards/T-REC-G.733",
     "ITU-T Rec. G.733 (11/88) Characteristics of primary PCM multiplex "
     "equipment operating at 1544 kbit/s",
     "Characteristics of primary PCM multiplex equipment at 1544 kbit/s. "
     "Twenty four channels, mu-law companding. Frame structure is given in "
     "Recommendation G.704. "),
    ("standards/T-REC-G.734",
     "ITU-T Rec. G.734 (11/88) Characteristics of synchronous digital "
     "multiplex equipment operating at 1544 kbit/s",
     "Characteristics of synchronous digital multiplex equipment at 1544 "
     "kbit/s. Frame structure is defined in Recommendation G.704. "),
    ("standards/T-REC-G.707",
     "ITU-T Rec. G.707 Network node interface for the synchronous digital "
     "hierarchy (SDH)",
     "Network node interface for SDH. STM-1 frame of 2430 bytes. Virtual "
     "containers VC-12 and VC-4. Administrative unit pointers. "),
    ("rfc/rfc4818",
     "RFC 4818 RADIUS Delegated-IPv6-Prefix Attribute",
     "The Delegated-IPv6-Prefix attribute carries an IPv6 prefix to be "
     "delegated. Attribute type 123. Prefix-Length field. "),
    ("gost/r-53363-2009",
     "ГОСТ Р 53363-2009. Цифровые радиорелейные линии. Показатели качества",
     "Показатели качества цифровых радиорелейных линий. Коэффициент "
     "готовности, коэффициент ошибок по битам, замирания на пролёте. "),
    ("lib/gruppa",
     "Цифровые системы группообразования",
     "Учебник. Плезиохронная и синхронная цифровые иерархии, принципы "
     "объединения потоков, выравнивание скоростей. "),
    ("lib/svyaz",
     "Организация связи в войсках",
     "Организация связи. Общие принципы построения систем передачи и "
     "распределения каналов по направлениям. "),
]

#: Что «знает» модель, когда её спрашивают, в каком стандарте это описано.
#: Заглушка намеренно скупая: она отвечает так, как ответила бы живая
#: модель, — обозначениями через запятую и иногда мимо.
ЗНАНИЕ = {
    "e1": "G.703, G.704",
    "уплотнени": "G.703, G.704, G.732",
    "канальн": "G.704",
    "сверхцикл": "G.704",
    "hdb3": "G.703",
    "линейн": "G.703",
    "код": "G.703",
    "stm": "G.707",
    "сци": "G.707",
    "sdh": "G.707",
    "delegated": "RFC 4818",
    "радиорелейн": "ГОСТ Р 53363",
}


class ЗнающаяМодель(StubLLM):
    """Обозначения знает, содержания не выдумывает."""

    def complete(self, system, user, **kwargs):
        if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
            низ = user.lower()
            for ключ, ответ in ЗНАНИЕ.items():
                if ключ in низ:
                    return ответ
            return "нет"
        return "Ответ по материалу [S1]."


class Свод(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.settings = Settings.load(
            data_dir=cls._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"))
        cls.repos = Repositories(Database(":memory:"))
        for doc_id, title, текст in БИБЛИОТЕКА:
            документ = cls.repos.documents.upsert(
                doc_id, "standards", title, f"/{doc_id}.pdf",
                "sha-" + doc_id[-12:], meta={}, domain="other")
            cls.repos.chunks.replace_for_document(документ, [Chunk(
                chunk_id=f"{doc_id}#{n}", doc_id=doc_id, doc_type="standards",
                title_path=[title], text=текст * 6, meta={})
                for n in range(3)])
        cls.reports = ReportService(repos=cls.repos, settings=cls.settings,
                                    llm=ЗнающаяМодель())
        cls.assistant = AssistantService(reports=cls.reports)
        cls.user = cls.repos.users.create("ivanov", "пароль123", "Иванов",
                                          "engineer")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def разобрать(self, вопрос):
        chat = self.assistant.create_chat(self.user)
        return self.assistant._prepare(self.user, chat.id, вопрос, top_k=None)

    def документы(self, вопрос):
        return {item["doc_id"] for item in self.разобрать(вопрос)["sources"]}

    def проверить(self, случаи):
        for вопрос, ждём in случаи:
            with self.subTest(вопрос=вопрос):
                пришло = self.документы(вопрос)
                не_хватает = set(ждём) - пришло
                self.assertFalse(
                    не_хватает,
                    f"«{вопрос}»: до модели не дошло {sorted(не_хватает)}; "
                    f"пришло {sorted(пришло)}")


class ОбозначениеНазваноВВопросе(Свод):
    """Все написания, какими в отделе печатают номер документа."""

    def test_рекомендации_мсэ(self):
        self.проверить([
            ("структура цикла G.704", ["standards/T-REC-G.704"]),
            ("что описано в g.704", ["standards/T-REC-G.704"]),
            ("рекомендация G.704", ["standards/T-REC-G.704"]),
            ("МСЭ-Т G.704", ["standards/T-REC-G.704"]),
            ("ITU-T Rec. G.704", ["standards/T-REC-G.704"]),
            ("T-REC-G.704.pdf", ["standards/T-REC-G.704"]),
            ("стандарт G 704", ["standards/T-REC-G.704"]),
            ("рекомендация G-704", ["standards/T-REC-G.704"]),
            ("itu t g.704", ["standards/T-REC-G.704"]),
        ])

    def test_два_номера_в_одном_вопросе(self):
        оба = ["standards/T-REC-G.733", "standards/T-REC-G.734"]
        self.проверить([
            ("В чём разница стандарта G.733 от G.734?", оба),
            ("В чем разница стандарта g 733 от 734?", оба),
            ("чем отличается g 733 от 734", оба),
            ("G.733 и G.734 — в чём разница", оба),
            ("сравни G.733, 734", оба),
            ("g.733 vs g.734", оба),
        ])

    def test_ссылка_внутри_документа_открывает_документ(self):
        """G.733 и G.734 байтовую структуру не приводят — она в G.704."""
        self.проверить([
            ("В чем разница стандарта g 733 от 734?",
             ["standards/T-REC-G.704"]),
        ])

    def test_rfc_и_гост(self):
        self.проверить([
            ("есть ли у нас RFC 4818", ["rfc/rfc4818"]),
            ("что в rfc4818.txt", ["rfc/rfc4818"]),
            ("RFC-4818 про что", ["rfc/rfc4818"]),
            ("ГОСТ Р 53363 показатели качества", ["gost/r-53363-2009"]),
            ("гост р 53363-2009", ["gost/r-53363-2009"]),
        ])


class ОбозначенийВВопросеНет(Свод):
    """Самый частый и самый трудный случай: вопрос словами.

    Русский вопрос про английский стандарт. Раньше поиск возвращал ноль, и
    ответ писался по памяти модели — вместе с «в библиотеке этого нет».
    """

    def test_вопрос_отдела_дословно(self):
        self.проверить([
            ("что такое e1 уплотнение и его структура",
             ["standards/T-REC-G.703", "standards/T-REC-G.704"]),
        ])

    def test_вопросы_словами_по_трактам(self):
        self.проверить([
            ("что такое E1 и какая у него скорость",
             ["standards/T-REC-G.703"]),
            ("как устроен канальный интервал 16",
             ["standards/T-REC-G.704"]),
            ("что такое сверхцикл", ["standards/T-REC-G.704"]),
            ("линейный код HDB3 на стыке", ["standards/T-REC-G.703"]),
            ("что такое STM-1", ["standards/T-REC-G.707"]),
            ("расскажи про SDH", ["standards/T-REC-G.707"]),
        ])

    def test_вопрос_по_аббревиатуре_отдела(self):
        self.проверить([
            ("что такое СЦИ", ["standards/T-REC-G.707"]),
            ("показатели качества радиорелейной линии",
             ["gost/r-53363-2009"]),
        ])


class НазванныйДокументОткрываетсяНаНужномМесте(Свод):
    """Подложить документ мало — надо подложить нужное его место.

    Документы МСЭ начинаются одинаково: Contents, Scope, Definitions, и
    только потом суть. Внутри названного документа искал голый BM25 по
    СЫРОМУ вопросу, а вопрос русский против английского текста: общих слов
    нет, выдача пуста, и срабатывал запасной путь — первые три фрагмента по
    порядку. То есть обложка.

    Отсюда и жалоба отдела: G.703 подложен, лежит в источниках, а структуры
    цикла в ответе нет. Её и не могло быть — модели прислали титульный лист.
    """

    #: Настоящий порядок разделов рекомендации МСЭ.
    СТРАНИЦЫ = (
        ("Contents", "Table of contents. Summary. History. Foreword."),
        ("Scope", "This Recommendation specifies the scope of application."),
        ("Definitions", "Definitions and abbreviations used herein."),
        ("5. Basic frame structure",
         "Basic frame of 256 bits. Time slot 0 carries the frame alignment "
         "signal. Time slot 16 carries channel associated signalling."),
    )

    def setUp(self):
        документ = self.repos.documents.upsert(
            "standards/T-REC-G.999", "standards",
            "ITU-T Rec. G.999 Synchronous frame structures", "/g999.pdf",
            "sha-g999", meta={}, domain="other")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"standards/T-REC-G.999#{n}", doc_id="standards/T-REC-G.999",
            doc_type="standards", title_path=["ITU-T Rec. G.999", заголовок],
            text=текст * 8, meta={})
            for n, (заголовок, текст) in enumerate(self.СТРАНИЦЫ)])
        self.addCleanup(self.repos.documents.delete, "standards/T-REC-G.999")

    def подложено(self, вопрос):
        куски = self.assistant._pin_mentioned([], ["standards/T-REC-G.999"], вопрос)
        return [hit.chunk.breadcrumbs for hit in куски]

    def test_подкладывается_суть_а_не_обложка(self):
        """Русский вопрос по английскому документу — через словарь отдела."""
        крошки = self.подложено("какая структура цикла и канальные интервалы")
        self.assertTrue(крошки, "не подложено ничего")
        self.assertTrue(any("frame structure" in к.lower() for к in крошки),
                        f"вместо сути подложено: {крошки}")
        self.assertFalse(any("contents" in к.lower() for к in крошки),
                         f"подложена обложка: {крошки}")

    def test_словарь_и_правда_расширяет_вопрос(self):
        """Если словарь перестанет знать эту пару, тест выше станет случайным."""
        расширенный = self.assistant._расширить("структура цикла")
        self.assertIn("frame structure", расширенный.lower())

    def test_без_попаданий_берём_заголовок_а_не_начало(self):
        """Запасной путь тоже не должен упираться в титульный лист."""
        крошки = self.подложено("definitions")
        self.assertTrue(any("definitions" in к.lower() for к in крошки),
                        f"запасной путь дал: {крошки}")

    def test_совсем_чужой_вопрос_всё_равно_что_то_подкладывает(self):
        """Подложить нечего — хуже, чем подложить начало документа."""
        self.assertTrue(self.подложено("погода на выходных"))


class ЛишнегоНеПритягиваем(Свод):
    """Цена ошибки несимметрична и в другую сторону тоже."""

    def test_чужой_вопрос_не_тянет_стандарты(self):
        """Модель на такой вопрос обозначений не назовёт — и не надо."""
        пришло = self.документы("как организована связь в войсках")
        self.assertNotIn("rfc/rfc4818", пришло)
        self.assertNotIn("standards/T-REC-G.707", пришло)

    def test_заход_делается_и_когда_документ_в_вопросе_назван(self):
        """Сверка с описью знает только то, что напечатано в вопросе.

        Сперва заход включался лишь на вопросах без номеров — и этого мало.
        Инженер спрашивает «что даёт G.732 для линейного кода», а линейный
        код стоит в G.703, которую он не называл. Сверка её не подложит:
        в вопросе её нет. Знание о том, КАКИЕ ЕЩЁ документы к делу
        относятся, есть только у модели, и спросить надо всегда.
        """
        пришло = self.документы("что даёт G.732 для линейного кода")
        self.assertIn("standards/T-REC-G.732", пришло)
        self.assertIn("standards/T-REC-G.703", пришло,
                      "названного моделью соседа не подложили")

    def test_след_не_обещает_того_чего_не_подложили(self):
        """Опознать документ можно по КЭШУ описи, а на полке его уже нет.

        Кэш сбрасывается по отпечатку из трёх чисел, и это отпечаток, а не
        доказательство: один том сняли, другой поставили, названия одной
        длины, фрагментов поровну — числа сошлись, карта осталась старой.
        Написать в следе «нашёл по названию: G.703», ничего не подложив,
        нельзя: инженер пойдёт искать в ответе то, чего там нет.
        """
        # Заход строит карту библиотеки и кладёт её в кэш.
        self.разобрать("что такое сверхцикл")
        снимаем = self.repos.documents.by_doc_id("standards/T-REC-G.704")
        длина = len(снимаем.title)
        with self.repos.db.transaction() as connection:
            connection.execute("DELETE FROM documents WHERE id = ?",
                               (снимаем.id,))
        замена = "Ведомость эксплуатационных измерений тракта"
        документ = self.repos.documents.upsert(
            "lib/vedomost", "standards", замена.ljust(длина, "."),
            "/lib/vedomost.pdf", "sha-vedomost", meta={}, domain="other")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"lib/vedomost#{n}", doc_id="lib/vedomost",
            doc_type="standards", title_path=[замена],
            text="Ведомость измерений. " * 20, meta={}) for n in range(3)])
        try:
            self.assertEqual(
                "standards/T-REC-G.704",
                self.assistant._resolve_document("G.704"),
                "образец подобран неудачно: кэш описи обновился")
            след = self.разобрать("что такое сверхцикл")["trail"]
            self.assertEqual([], [с for с in след if "по названию" in с],
                             f"след обещает снятый с полки документ: {след}")
        finally:
            # Библиотека общая на весь класс — возвращаем как было.
            self.repos.documents.delete("lib/vedomost")

    def test_сверка_комплектности_тоже_не_обещает_снятого(self):
        """Тот же обман отпечатка, но на втором заходе к модели.

        Заходов, опознающих документ по названию, два: «в каких стандартах
        это описано» и «чего не хватает в собранном». Проверка «а есть ли он
        на полке» нужна обоим — закрыть один и оставить другой значит
        починить половину.
        """
        self.разобрать("что такое сверхцикл")          # строим кэш описи
        снимаем = self.repos.documents.by_doc_id("standards/T-REC-G.704")
        длина = len(снимаем.title)
        with self.repos.db.transaction() as connection:
            connection.execute("DELETE FROM documents WHERE id = ?",
                               (снимаем.id,))
        замена = "Ведомость эксплуатационных измерений тракта"
        документ = self.repos.documents.upsert(
            "lib/vedomost-2", "standards", замена.ljust(длина, "."),
            "/lib/vedomost-2.pdf", "sha-vedomost2", meta={}, domain="other")
        self.repos.chunks.replace_for_document(документ, [Chunk(
            chunk_id=f"lib/vedomost-2#{n}", doc_id="lib/vedomost-2",
            doc_type="standards", title_path=[замена],
            text="Ведомость измерений. " * 20, meta={}) for n in range(3)])

        class ПроситСнятое(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return "G.704"
                return "нет"

        прежняя = self.reports.llm
        self.reports.llm = ПроситСнятое()
        try:
            self.assertEqual(
                "standards/T-REC-G.704",
                self.assistant._resolve_document("G.704"),
                "образец подобран неудачно: кэш описи обновился")
            профиль = self.assistant._profile(
                self.assistant.create_chat(self.user))
            hits, _ = self.assistant._collect(
                self.assistant.create_chat(self.user), "что такое сверхцикл",
                [], профиль["top_k"], attachments=[], rounds=1)
            добор, след = self.assistant._чего_не_хватает(
                hits, "что такое сверхцикл", известные=[])
            self.assertEqual([], добор, "подложен снятый с полки документ")
            self.assertEqual([], след, f"след обещает снятое: {след}")
        finally:
            self.reports.llm = прежняя
            self.repos.documents.delete("lib/vedomost-2")
            вернуть = self.repos.documents.upsert(
                "standards/T-REC-G.704", "standards", снимаем.title,
                снимаем.source_path, снимаем.sha256, meta={}, domain="other")
            self.repos.chunks.replace_for_document(вернуть, [Chunk(
                chunk_id=f"standards/T-REC-G.704#{n}",
                doc_id="standards/T-REC-G.704", doc_type="standards",
                title_path=[снимаем.title], text=БИБЛИОТЕКА[1][2] * 6,
                meta={}) for n in range(3)])

    def test_заход_по_названию_делается_когда_обозначений_нет(self):
        """Обратная половина того же правила — иначе первую нечем отличить."""
        след = self.разобрать("что такое сверхцикл")["trail"]
        self.assertTrue([с for с in след if "по названию" in с],
                        f"заход не сделан там, где имя взять неоткуда: {след}")

    def test_подложенного_по_названию_не_больше_предела(self):
        """Одна тема тянет за собой десяток соседних рекомендаций."""
        class Щедрая(ЗнающаяМодель):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return ("G.703, G.704, G.732, G.733, G.734, G.707, "
                            "RFC 4818")
                return "ответ"

        прежняя = self.reports.llm
        self.reports.llm = Щедрая()
        self.settings.assistant_name_pass = 2
        try:
            след = self.разобрать("что такое сверхцикл")["trail"]
            строка = [с for с in след if "по названию" in с]
            self.assertEqual(1, len(строка), f"нет следа захода: {след}")
            названо = строка[0].split(":")[-1].split(",")
            self.assertEqual(2, len(названо),
                             f"предел не соблюдён: {строка[0]}")
            # Обрезаем по пределу — значит берём то, что модель назвала
            # ПЕРВЫМ, а не то, что раньше нашлось по шаблону.
            self.assertEqual(["G.703", "G.704"],
                             [x.strip() for x in названо],
                             f"взяты не первые названные: {строка[0]}")
        finally:
            self.reports.llm = прежняя
            self.settings.assistant_name_pass = 3

    def test_заход_по_названию_можно_выключить(self):
        self.settings.assistant_name_pass = 0
        try:
            след = self.разобрать("что такое сверхцикл")["trail"]
            self.assertEqual([], [с for с in след if "по названию" in с])
        finally:
            self.settings.assistant_name_pass = 2

    def test_названного_моделью_но_отсутствующего_не_придумываем(self):
        """Опись сильнее модели: чего нет на полке, того и не придёт."""
        class Фантазёрка(ЗнающаяМодель):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "G.999, RFC 9999"
                return "ответ"

        прежняя = self.reports.llm
        self.reports.llm = Фантазёрка()
        try:
            prepared = self.разобрать("что такое сверхцикл")
            self.assertEqual([], [с for с in prepared["trail"]
                                  if "по названию" in с])
        finally:
            self.reports.llm = прежняя


class ПроОтсутствиеГоворимТолькоПоОписи(Свод):
    """Инструкция не должна сама подсказывать ложное «в библиотеке нет»."""

    def test_названный_документ_помечен_как_числящийся(self):
        prompt = self.разобрать("что в G.704")["prompt"]
        self.assertIn("G.704 — ЧИСЛИТСЯ", prompt)

    def test_ненайденный_документ_помечен_честно(self):
        prompt = self.разобрать("есть ли у нас RFC 9999")["prompt"]
        self.assertIn("RFC 9999 — в библиотеке НЕ ЧИСЛИТСЯ", prompt)


if __name__ == "__main__":
    unittest.main()
