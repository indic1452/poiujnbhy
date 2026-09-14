# -*- coding: utf-8 -*-
"""Вопрос о целом собирает его части — сквозная проверка на случае отдела.

Жалоба: «у меня есть комплексы разные, отвечающие за построение тракта
(включая антенну), они просто названы как ДМА 256 — почему модель не
анализирует содержимое и не строит из них тракт».

Здесь та самая библиотека: четыре паспорта узлов тракта приёма, названные
обозначениями, и один общий документ с водой. Слов «тракт приёма» в
паспортах нет и быть не должно — паспорт демодулятора описывает демодулятор.

Замер до правки (тот же набор, тот же вопрос):

    5.158  Организация связи в войсках     ← общие слова, ничего по существу
           АДЭ-5, МШУ-2, ПЧ-70, ДМА 256 — ни одного

Помощник отвечал по единственному найденному документу, и ответ выходил
«однотипный и очень сухой», как отдел и написал.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen import parts
from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.retrieval import Hit, reciprocal_rank_fusion
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import PART_WEIGHT, AssistantService
from reportgen.web.research import Step
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]

#: Тракт приёма, разложенный по документам так, как он лежит у отдела:
#: название — обозначение изделия, о тракте в тексте ни слова.
ТРАКТ = [
    ("lib/ade5", "АДЭ-5",
     "Антенна АДЭ-5. Двухзеркальная антенна с облучателем и поляризационным "
     "селектором. Диаметр основного зеркала 5 м, диапазон 3,4-4,2 ГГц, "
     "коэффициент усиления 44 дБ."),
    ("lib/mshu2", "МШУ-2",
     "Усилитель малошумящий МШУ-2. Коэффициент шума 0,8 дБ, коэффициент "
     "усиления 45 дБ. Устанавливается непосредственно за облучателем антенны."),
    ("lib/pch70", "ПЧ-70",
     "Преобразователь частоты ПЧ-70. Перенос сигнала на промежуточную частоту "
     "70 МГц. Гетеродин синтезаторный, шаг перестройки 125 кГц."),
    ("lib/dma256", "ДМА 256",
     "Демодулятор ДМА 256. Вход - промежуточная частота 70 МГц, полоса 36 МГц. "
     "Виды модуляции ФМ-4, КАМ-16, КАМ-256. Декодер Рида - Соломона."),
]

#: Единственный документ, где слова вопроса есть, — и в нём ничего по существу.
ВОДА = ("lib/obshee", "Организация связи в войсках",
        "Тракт приёма радиорелейной линии обеспечивает доведение сигнала до "
        "оконечной аппаратуры. Вопросы построения трактов приёма решаются при "
        "проектировании линии.")

ВОПРОС = "расскажи про тракт приёма РРЛС"


class ТрактИзЧастей(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(parts.forget)
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"),
            parts_path=str(ROOT / "templates" / "parts.json"),
        )
        self.repos = Repositories(Database(":memory:"))
        for doc_id, title, текст in [*ТРАКТ, ВОДА]:
            документ = self.repos.documents.upsert(
                doc_id, "literature", title, "", "sha-" + doc_id[-8:],
                meta={}, domain="signal")
            self.repos.chunks.replace_for_document(документ, [Chunk(
                chunk_id=f"{doc_id}#0", doc_id=doc_id, doc_type="literature",
                title_path=[title], text=текст, meta={})])
        self.reports = ReportService(repos=self.repos, settings=self.settings,
                                     llm=StubLLM())
        self.assistant = AssistantService(reports=self.reports)
        self.user = self.repos.users.create("ivanov", "пароль123", "Иванов",
                                            "engineer")
        self.chat = self.assistant.create_chat(self.user, domain="signal")

    def названия(self, попадания):
        return {hit.chunk.breadcrumbs for hit in попадания}

    def test_один_поиск_по_вопросу_узлов_не_находит(self):
        """Замер, с которого всё началось. Он же сторожит причину.

        Если этот тест когда-нибудь начнёт падать — значит, поиск научился
        находить части по названию целого сам, и разбор состава больше не
        нужен. Пока он проходит, нужен.
        """
        найдено = self.assistant._search(self.chat, ВОПРОС, [], None)
        названия = self.названия(найдено)
        self.assertIn("Организация связи в войсках", названия)
        отсутствуют = {title for _id, title, _text in ТРАКТ} - названия
        self.assertEqual(4, len(отсутствуют),
                         f"поиск по вопросу нашёл узлы сам: {названия}")

    def test_разбор_состава_собирает_весь_тракт(self):
        найдено, след = self.assistant._collect(self.chat, ВОПРОС, [], None)
        названия = self.названия(найдено)
        for _id, title, _text in ТРАКТ:
            with self.subTest(документ=title):
                self.assertIn(title, названия,
                              "узел тракта не попал в материал для ответа")

    def test_след_разбора_показывает_что_искали(self):
        """Инженер должен видеть, ЧТО помощник искал, иначе не оспорит ответ."""
        _найдено, след = self.assistant._collect(self.chat, ВОПРОС, [], None)
        искали = [шаг.argument for шаг in след if шаг.kind == "искать"]
        self.assertIn("антенна", искали)
        self.assertIn("демодулятор", искали)
        for шаг in след:
            if шаг.argument in ("антенна", "демодулятор"):
                self.assertIn("узел из состава", шаг.note)

    def test_узлы_ищутся_по_ходу_сигнала(self):
        _найдено, след = self.assistant._collect(self.chat, ВОПРОС, [], None)
        искали = [шаг.argument for шаг in след if шаг.kind == "искать"]
        self.assertLess(искали.index("антенна"), искали.index("демодулятор"),
                        "узлы искались не по ходу сигнала")

    def test_разбор_можно_выключить(self):
        """Настройка в ноль — прежнее поведение, без единого лишнего поиска."""
        self.settings.assistant_parts = 0
        найдено, след = self.assistant._collect(self.chat, ВОПРОС, [], None)
        self.assertEqual([], [шаг for шаг in след if шаг.kind == "искать"])
        self.assertNotIn("ДМА 256", self.названия(найдено))

    def test_чужой_вопрос_состава_не_разбирает(self):
        """Разбор не должен включаться на вопросах, где целое не названо."""
        _найдено, след = self.assistant._collect(
            self.chat, "какой коэффициент шума у МШУ-2", [], None)
        self.assertEqual([], [шаг for шаг in след if шаг.kind == "искать"])

    def test_битый_справочник_не_роняет_вопрос(self):
        """Разбор состава — усиление, а не условие работы помощника."""
        битый = Path(self._tmp.name) / "parts.json"
        битый.write_text('{"parts": [ , ]}', encoding="utf-8")
        self.settings.parts_path = битый
        parts.forget()
        найдено, _след = self.assistant._collect(self.chat, ВОПРОС, [], None)
        self.assertTrue(найдено, "помощник остался без материала из-за справочника")

    def test_узлы_ищутся_без_реранка_а_вопрос_с_реранком(self):
        """Восемь узлов — это было восемь обращений к модели вместо одного.

        Проверяется вся проводка, а не одна ветка: тот же вызов, каким
        пользуется живой разговор.
        """
        настоящий = self.reports.get_retriever()
        вызовы = []

        class Записывающий:
            def search(self, query, top_k=6, **kwargs):
                вызовы.append((query, kwargs.get("rerank", True)))
                return настоящий.search(query, top_k=top_k, **kwargs)

        self.reports.get_retriever = lambda: Записывающий()
        self.assistant._collect(self.chat, ВОПРОС, [], None)
        узловые = [(запрос, реранк) for запрос, реранк in вызовы
                   if запрос in ("антенна", "демодулятор")]
        self.assertTrue(узловые, "разбор состава не сработал")
        for запрос, реранк in узловые:
            with self.subTest(узел=запрос):
                self.assertFalse(реранк, "поиск по узлу зовёт реранк")
        по_вопросу = [реранк for запрос, реранк in вызовы if ВОПРОС in запрос]
        self.assertTrue(по_вопросу and all(по_вопросу),
                        "поиск по самому вопросу остался без реранка")

    def test_вес_узла_доходит_до_слияния(self):
        """Списки узлов обязаны прийти в слияние с меньшим весом.

        Без этого восемь узловых списков вытесняют находки по вопросу — тот
        самый регресс, который отдел описал как «качество не улучшилось,
        а где-то даже ухудшилось».
        """
        from reportgen.web import assistant as модуль

        поймано = {}
        настоящее = модуль.reciprocal_rank_fusion

        def перехват(rankings, **kwargs):
            поймано.setdefault("weights", kwargs.get("weights"))
            return настоящее(rankings, **kwargs)

        модуль.reciprocal_rank_fusion = перехват
        try:
            self.assistant._collect(self.chat, ВОПРОС, [], None)
        finally:
            модуль.reciprocal_rank_fusion = настоящее
        веса = поймано.get("weights")
        self.assertIsNotNone(веса, "слияние вызвано без весов")
        self.assertEqual(1.0, веса[0], "список по вопросу должен весить единицу")
        self.assertTrue(len(веса) > 1, "узловые списки не дошли до слияния")
        for вес in веса[1:]:
            self.assertEqual(PART_WEIGHT, вес)


class РазборСоставаНеВытесняетОтветНаВопрос(unittest.TestCase):
    """Отдел: «стал иногда медленный ответ, но качество не улучшилось,
    а где-то даже ухудшилось».

    Это был регресс от самого разбора состава. К поиску по вопросу
    добавлялось до восьми поисков по узлам, и слияние по обратным рангам
    считало их РАВНЫМИ вопросу: восемь списков, в каждом свой фрагмент на
    первом месте, вытесняли то, что нашлось по самому вопросу инженера.

    Здесь проверяется размен: узел весит меньше вопроса, но документ,
    всплывший у НЕСКОЛЬКИХ узлов, по-прежнему поднимается наверх — он и есть
    узел тракта, ради которого всё затевалось.
    """

    def кусок(self, имя):
        return Chunk(chunk_id=имя, doc_id=имя, doc_type="literature",
                     title_path=[имя], text="")

    def расклад(self):
        по_вопросу = [Hit(chunk=self.кусок("общее описание тракта"), score=1.0),
                      Hit(chunk=self.кусок("методика измерений"), score=0.5)]
        узлы = [[Hit(chunk=self.кусок(f"узел-{номер}"), score=1.0)]
                for номер in range(8)]
        нашёлся_тремя = self.кусок("ДМА 256")
        for номер in (5, 6, 7):
            узлы[номер].append(Hit(chunk=нашёлся_тремя, score=0.9))
        return [по_вопросу, *узлы]

    def имена(self, слито):
        return [hit.chunk.chunk_id for hit in слито]

    def test_равные_веса_вытесняли_находки_по_вопросу(self):
        """Замер прежнего поведения — он же объясняет, зачем веса."""
        имена = self.имена(reciprocal_rank_fusion(self.расклад(), top_k=6))
        self.assertNotIn("методика измерений", имена,
                         "образец подобран неудачно: вытеснения не было, "
                         "и проверка весов ничего не стережёт")

    def test_с_весами_находки_по_вопросу_остаются(self):
        веса = [1.0] + [PART_WEIGHT] * 8
        имена = self.имена(reciprocal_rank_fusion(self.расклад(), top_k=6,
                                                  weights=веса))
        self.assertIn("общее описание тракта", имена)
        self.assertIn("методика измерений", имена)

    def test_один_узел_не_перевешивает_вопрос(self):
        веса = [1.0] + [PART_WEIGHT] * 8
        имена = self.имена(reciprocal_rank_fusion(self.расклад(), top_k=6,
                                                  weights=веса))
        self.assertLess(имена.index("общее описание тракта"),
                        имена.index("узел-0"))

    def test_несколько_узлов_подряд_перевешивают(self):
        """Документ, найденный тремя узлами, — это и есть узел тракта."""
        веса = [1.0] + [PART_WEIGHT] * 8
        имена = self.имена(reciprocal_rank_fusion(self.расклад(), top_k=6,
                                                  weights=веса))
        self.assertEqual("ДМА 256", имена[0])

    def test_без_весов_поведение_прежнее(self):
        """Вес — надстройка: два равноправных канала поиска её не касаются."""
        списки = self.расклад()[:2]
        self.assertEqual(self.имена(reciprocal_rank_fusion(списки, top_k=4)),
                         self.имена(reciprocal_rank_fusion(
                             списки, top_k=4, weights=[1.0, 1.0])))


class РазборСоставаНеЗовётРеранк(unittest.TestCase):
    """Восемь узлов — это было восемь обращений к модели вместо одного.

    Реранк ценен там, где надо отличить «обратный канал» от «прямого» в
    длинном вопросе. На запросе «антенна» уточнять ему нечего, а платит за
    него инженер ожиданием.
    """

    def test_узлы_ищутся_без_переоценки_моделью(self):
        вызовы = []

        class Поисковик:
            def search(self, query, top_k=6, **kwargs):
                вызовы.append((query, kwargs.get("rerank", True)))
                return []

        # Полноценная сборка службы здесь не нужна: проверяется одна ветка.
        помощник = AssistantService.__new__(AssistantService)
        помощник.reports = type("Заглушка", (), {
            "get_retriever": staticmethod(lambda: Поисковик())})()
        шаг = Step("искать", "антенна")
        помощник._step_search(шаг, _ПустойЧат(), rerank=False)
        self.assertEqual([("антенна", False)], вызовы)

    def test_обычный_заход_реранк_зовёт(self):
        вызовы = []

        class Поисковик:
            def search(self, query, top_k=6, **kwargs):
                вызовы.append((query, kwargs.get("rerank", True)))
                return []

        помощник = AssistantService.__new__(AssistantService)
        помощник.reports = type("Заглушка", (), {
            "get_retriever": staticmethod(lambda: Поисковик())})()
        помощник._step_search(Step("искать", "обратный канал SurfBeam 2"),
                              _ПустойЧат())
        self.assertEqual([("обратный канал SurfBeam 2", True)], вызовы)


class _ПустойЧат:
    domain = ""
    sources = "all"


if __name__ == "__main__":
    unittest.main()
