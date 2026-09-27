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

ПОТОМ — сверка комплектности, тоже до ответа: модели показывают список уже
добытого и спрашивают, чего не хватает. Отличие от захода по названию в
том, ЧТО она видит: тот спрашивает вслепую по одному вопросу, а этот — по
собранному материалу.

Раньше второй заход стоял ПОСЛЕ ответа: готовый текст проверяли на
упоминание непрочитанного тома и, если находили, ответ стирали и писали
заново по дополненному материалу. Отдел: «почему она старый ответ затёрла
и начала новый писать, но уже по другим документам, не пойму, первый уже
лучше был».

И был прав. Дозаказанное вставало в начало подборки, а бюджет окна
расходуется по порядку — выдача поиска, на которой стоял первый ответ, до
промпта не доходила. Теперь ряды материала сливаются вперемежку, недостающее
выясняется до письма, а ответ пишется один раз.

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


class СверкаКомплектностиДоОтвета(Библиотека):
    """Заход по названию промахнулся — ловит сверка перед письмом.

    Прежде этот случай ловили ПОСЛЕ ответа: готовый текст проверяли на
    упоминание непрочитанного тома, и если оно находилось — ответ стирали и
    писали заново. Отдел: «почему она старый ответ затёрла и начала новый
    писать, но уже по другим документам, не пойму, первый уже лучше был».
    """

    def setUp(self):
        super().setUp()
        заходы = self.заходы = []
        спрошено = self.спрошено = []

        class Забывчивая(StubLLM):
            """Вслепую обозначений не назовёт, а по списку добытого — назовёт."""

            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    спрошено.append(user)
                    return "G.706"
                if system.startswith(ASSISTANT_SYSTEM_PROMPT[:40]):
                    заходы.append(user)
                return "Сверхцикл CRC-4 описан так: [S1]."

        self.reports.llm = Забывчивая()

    def test_недостающее_попало_в_материал_до_ответа(self):
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        self.assertIn("standards/T-REC-G.706",
                      {item["doc_id"] for item in prepared["sources"]})

    def test_модель_отвечает_один_раз(self):
        """Ради добора ответ больше не переписывают."""
        self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertEqual(1, len(self.заходы), "модель отвечала дважды")

    def test_модели_показывают_что_уже_достали(self):
        """Судить о полноте, не зная собранного, нельзя.

        Именно поэтому пробел и всплывал только в готовом ответе: заход по
        названию спрашивает вслепую, по одному вопросу.
        """
        self.assistant._prepare(self.user, self.chat.id, ВОПРОС, top_k=None)
        self.assertTrue(self.спрошено, "сверки комплектности не было")
        задание = self.спрошено[0]
        self.assertIn("УЖЕ ДОСТАЛИ С ПОЛКИ", задание)
        self.assertIn("G.704", задание, "список добытого пуст")

    def test_добор_виден_в_ходе_разбора(self):
        след = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                       top_k=None)["trail"]
        self.assertTrue([с for с in след if "добрал до ответа" in с], след)

    def test_сверку_можно_выключить(self):
        self.settings.assistant_gap_pass = 0
        self.assistant._prepare(self.user, self.chat.id, ВОПРОС, top_k=None)
        self.assertEqual([], self.спрошено)

    def test_названного_нет_на_полке_значит_и_не_придёт(self):
        """Отвечает опись, а не модель: выдуманный номер ничего не добавит."""
        class Выдумщица(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return "G.999, RFC 9999"
                return "Ответ [S1]."

        self.reports.llm = Выдумщица()
        след = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                       top_k=None)["trail"]
        self.assertEqual([], [с for с in след if "добрал до ответа" in с])

    def test_уже_прочитанное_второй_раз_не_подкладываем(self):
        """Тот же документ в материале дважды — только место занял бы."""
        class Повторяющая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "G.704"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return "G.704"
                return "Ответ [S1]."

        self.reports.llm = Повторяющая()
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        свои = [item for item in prepared["sources"]
                if item["doc_id"] == "standards/T-REC-G.704"]
        метки = {item["label"] for item in свои}
        self.assertEqual(len(свои), len(метки), "фрагменты задвоились")

    def test_добор_ограничен_настройкой(self):
        """Иначе модель на каждый вопрос утащит за собой полбиблиотеки.

        Ограничение считается по ДОБРАННОМУ, а не по названному: названного
        может быть десять, а на полке лежать два.
        """
        class Щедрая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return "G.703, G.706, G.711, G.712, G.713"
                return "Ответ [S1]."

        self.reports.llm = Щедрая()
        for обозначение in ("711", "712", "713"):
            self.положить(f"standards/T-REC-G.{обозначение}",
                          f"ITU-T Rec. G.{обозначение} Pulse code modulation",
                          "Pulse code modulation of voice frequencies. ")
        self.settings.assistant_gap_pass = 2
        добор, _ = self.assistant._чего_не_хватает(
            self.assistant._collect(
                self.chat, ВОПРОС, [], 12, attachments=[], rounds=1)[0],
            ВОПРОС, известные=[])
        self.assertEqual(2, len(добор), добор)

    def test_списка_документов_под_ответом_нет(self):
        """Отдел: «не надо мне документы эти показывать»."""
        готово = self.assistant.ask(self.user, self.chat.id, ВОПРОС)
        self.assertEqual([], готово["answer"]["meta"]["present"])


class НайденноеПоискомНеВытесняется(Библиотека):
    """Подложенное и найденное сливаются вперемежку, а не встык.

    Вот настоящая причина, по которой второй ответ выходил беднее первого.
    Дозаказанное вставало В НАЧАЛО подборки, а урезание по окну снимает
    ХВОСТ, — и выдача поиска вылетала из материала целиком. Окно тут ни при
    чём: у отдела оно 32768, и библиотеке остаётся 27 000 знаков.
    """

    def ряд(self, doc_id, сколько):
        from reportgen.retrieval import Hit
        куски = [Chunk(chunk_id=f"{doc_id}#{n}", doc_id=doc_id,
                       doc_type="standards", title_path=[doc_id], text="т",
                       meta={}) for n in range(сколько)]
        return [Hit(chunk=кусок, score=0.0) for кусок in куски]

    def test_верх_каждого_ряда_доживает_до_обрезки(self):
        from reportgen.web.assistant import вперемежку

        подложено = self.ряд("закреплённый", 9)
        найдено = self.ряд("найденный", 30)
        слито = вперемежку(подложено, найдено)
        # Влезает десять фрагментов — ровно тот случай, что у отдела.
        в_окне = {hit.chunk.doc_id for hit in слито[:10]}
        self.assertIn("найденный", в_окне,
                      "выдача поиска вытеснена подложенным целиком")
        self.assertIn("закреплённый", в_окне)

    def test_ничего_не_теряется(self):
        from reportgen.web.assistant import вперемежку

        слито = вперемежку(self.ряд("а", 3), self.ряд("б", 5))
        self.assertEqual(8, len(слито))

    def test_места_проставлены_подряд(self):
        from reportgen.web.assistant import вперемежку

        слито = вперемежку(self.ряд("а", 2), self.ряд("б", 3))
        self.assertEqual([1, 2, 3, 4, 5], [hit.rank for hit in слито])

    def test_порядок_внутри_ряда_сохранён(self):
        from reportgen.web.assistant import вперемежку

        ряд = self.ряд("а", 3)
        слито = вперемежку(ряд, self.ряд("б", 1))
        свои = [hit for hit in слито if hit.chunk.doc_id == "а"]
        self.assertEqual([кусок.chunk.chunk_id for кусок in ряд],
                         [hit.chunk.chunk_id for hit in свои])

    def test_пустые_ряды_не_мешают(self):
        from reportgen.web.assistant import вперемежку

        self.assertEqual(3, len(вперемежку([], self.ряд("а", 3), [])))
        self.assertEqual([], вперемежку([], []))

    # -- то же самое, но на живой сборке материала ------------------------
    #
    # Проверять слияние в отдельности мало: оно может быть верным, а сборка
    # материала — складывать ряды мимо него. Ровно так и было.

    #: Окно, при котором порядок решает исход. Замер на этой библиотеке:
    #: встык — материал ['G.703', 'G.706'], вперемежку — ['G.703', 'G.704'].
    #: G.704 здесь единственное, что поиск поднимает сам.
    ТЕСНОЕ_ОКНО = 5000
    #: То, что поиск на этом вопросе находит без подсказки модели.
    НАЙДЕНО_ПОИСКОМ = "standards/T-REC-G.704"

    def подкладывает(self, где, что):
        """Модель называет документы на указанном заходе, и только на нём."""
        class Называющая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return что if где == "название" else "нет"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return что if где == "сверка" else "нет"
                return "Ответ [S1]."

        self.reports.llm = Называющая()
        self.settings.assistant_context_chars = self.ТЕСНОЕ_ОКНО

    def в_материале(self):
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        return {item["doc_id"] for item in prepared["sources"]}

    def test_подложенное_по_названию_не_вытесняет_выдачу_поиска(self):
        """Тот самый случай отдела: материал из одних подложенных томов.

        Урезание по окну — не отсечение хвоста, а набор по порядку: берётся
        всё, что влезает в бюджет, пока он не кончится. Поэтому место в
        очереди решает всё, и подложенное, поставленное впереди, съедало
        бюджет прежде, чем до него доходила выдача поиска.
        """
        self.подкладывает("название", "G.703, G.706")
        документы = self.в_материале()
        self.assertIn(self.НАЙДЕНО_ПОИСКОМ, документы,
                      "выдача поиска вытеснена подложенным")
        self.assertIn("standards/T-REC-G.703", документы,
                      "подложенное не дошло до материала")

    def test_добор_по_сверке_тоже_не_вытесняет(self):
        self.подкладывает("сверка", "G.703, G.706")
        self.assertIn(self.НАЙДЕНО_ПОИСКОМ, self.в_материале(),
                      "добор по сверке вытеснил выдачу поиска")

    def test_дошедшее_по_ссылкам_не_дописывается_в_хвост(self):
        """Дописанное в хвост доходит до бюджета последним — то есть никогда.

        Отдел: «помимо словесного описания существует ещё и ссылка в этих
        документах на G.704, где для каждого описана байтовая структура».
        Документ по ссылке — не довесок, а половина ответа, и складывать его
        в конец очереди нельзя.

        Замер на этой выдаче: при окне 10 000 встык G.706 до материала не
        доходит, вперемежку — доходит.
        """
        class Молчит(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return "нет"
                return "Ответ [S1]."

        self.reports.llm = Молчит()
        # Поиск находит G.704 сам, а тот ссылается на G.706, до которого по
        # словам вопроса поиск не дотягивается.
        self.положить(
            "standards/T-REC-G.704",
            "ITU-T Rec. G.704 Synchronous frame structures 2048 kbit/s",
            "Time slot 0 carries frame alignment. Time slot 16 signalling. "
            "CRC-4 is described in ITU-T Rec. G.706. ")
        # Ещё находки по тем же словам: без них бюджет не кончается и
        # порядок ничего не решает.
        for n in range(4):
            self.положить(f"lib/slots-{n}",
                          f"Таймслоты и структура цикла, часть {n}",
                          "Time slot structure. Frame alignment. Позиции и "
                          "таймслоты уплотнения E1. ")
        self.settings.assistant_context_chars = 10000
        prepared = self.assistant._prepare(self.user, self.chat.id, ВОПРОС,
                                           top_k=None)
        self.assertTrue([с for с in prepared["trail"] if "по ссылке" in с],
                        "ссылка не сработала — проверять нечего")
        self.assertIn("standards/T-REC-G.706",
                      {item["doc_id"] for item in prepared["sources"]},
                      "документ по ссылке до бюджета не дошёл")


class ВПотокеОтветОдин(Библиотека):
    """На экране ответ пишется один раз и не затирается.

    Отдел смотрел на это живьём: «почему она старый ответ затёрла и начала
    новый писать». Теперь стирать нечего — материал добран до письма.
    """

    def setUp(self):
        super().setUp()

        class Забывчивая(StubLLM):
            def complete(self, system, user, **kwargs):
                if system.startswith("Ты называешь ОБОЗНАЧЕНИЯ"):
                    return "нет"
                if system.startswith("Ты сверяешь КОМПЛЕКТНОСТЬ"):
                    return "G.706"
                return "Сверхцикл CRC-4 описан так [S1]."

        self.reports.llm = Забывчивая()

    def события(self):
        return list(self.assistant.ask_stream(self.user, self.chat.id, ВОПРОС))

    def test_перезапуска_в_потоке_нет(self):
        виды = [с["type"] for с in self.события()]
        self.assertNotIn("restart", виды, "ответ всё ещё переписывается")

    def test_написанное_целиком_доходит_до_разговора(self):
        события = self.события()
        текст = "".join(с["text"] for с in события if с["type"] == "delta")
        self.assertEqual(текст.strip(), события[-1]["answer"]["content"])

    def test_вопрос_в_разговоре_один(self):
        self.события()
        роли = [m.role for m in self.assistant.messages(self.user, self.chat.id)]
        self.assertEqual(["user", "assistant"], роли, роли)

    def test_сверка_названа_в_ходе_работы(self):
        этапы = [с["text"] for с in self.события() if с["type"] == "stage"]
        self.assertTrue([э for э in этапы if "комплектность" in э], этапы)
        self.assertEqual([], [э for э in этапы if "заново" in э],
                         "на экране всё ещё обещают переписать ответ")


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
