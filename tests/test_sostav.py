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
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
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


if __name__ == "__main__":
    unittest.main()
