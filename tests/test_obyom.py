# -*- coding: utf-8 -*-
"""Развёрнутый ответ: инженерный разбор, а не справка.

Отдел: «все ответы очень неподробные и краткие. Мне всё-таки нужны
развёрнутые ответы с полным описанием структуры, скоростей, полей. В общем
всего что есть и всего что сопоставить нужно, инженерная модель так сказать».

Причина была прямо в настройке. `assistant_target_words` стояло 500, и это
число уходит в инструкцию модели дословно: «целевой объём — около 500 слов».
Модель слушалась буквально. Пятисот слов не хватит ни на таблицу полей, ни
на перечень скоростей, ни на сопоставление двух документов.

Здесь закреплены три вещи, которые вместе и делают ответ инженерным:

1. числа — объём ответа и потолок в токенах — заданы под разбор, а не под
   справку, и согласованы между собой: потолок должен вмещать целевой объём,
   иначе ответ оборвётся на середине таблицы;
2. инструкция требует ПОЛНОТЫ поимённо: все позиции, все поля, все числа,
   перечень целиком, без «и так далее»;
3. когда окно тесное, подробное задание уступает место источникам ПЕРВЫМ —
   это инструкция, а не данные, — но короткое задание сохраняет всё, без
   чего ответ становится неверным.
"""

import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.config import Settings
from reportgen.corpus import Chunk
from reportgen.llm import StubLLM
from reportgen.prompts import (
    ASSISTANT_SYSTEM_PROMPT,
    ASSISTANT_TASK,
    ASSISTANT_TASK_SHORT,
)
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]


class ЧислаПодРазбор(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()

    def test_объём_задан_под_разбор_а_не_под_справку(self):
        """Пятьсот слов — это справка на страницу. Их и просили изменить."""
        self.assertGreaterEqual(self.settings.assistant_target_words, 1200)

    def test_потолок_вмещает_целевой_объём(self):
        """Иначе ответ оборвётся на середине таблицы полей.

        Русский технический текст идёт примерно по три токена на слово:
        редкие слова вроде «плезиохронный» и «демультиплексор» разбиваются
        на много кусков. Полтора запаса сверх этого — на разметку таблиц.
        """
        нужно = self.settings.assistant_target_words * 3
        self.assertGreaterEqual(
            self.settings.assistant_max_tokens, нужно,
            f"на {self.settings.assistant_target_words} слов нужно около "
            f"{нужно} токенов, а потолок — "
            f"{self.settings.assistant_max_tokens}")

    def test_ответ_не_съедает_окно_целиком(self):
        """Потолок вычитается из окна: материалу должно остаться на разбор."""
        остаток = int(
            (self.settings.llm_context_tokens
             - self.settings.assistant_max_tokens - 512)
            * self.settings.assistant_chars_per_token)
        остаток -= len(ASSISTANT_SYSTEM_PROMPT) + len(ASSISTANT_TASK)
        self.assertGreater(остаток, 20000,
                           "материалу осталось меньше десяти фрагментов — "
                           "разбирать будет нечего")


class ИнструкцияТребуетПолноты(unittest.TestCase):
    """Проверяем требования поимённо: на них и держится объём."""

    def test_перечень_переносится_целиком(self):
        """Запрет нужен в ОБЕИХ инструкциях, и проверять их надо порознь.

        Системная задаёт тон всему разговору, задание — порядок работы над
        этим вопросом. Модель слушается ближней; проверка «есть хоть
        где-нибудь» пропускала потерю запрета в задании.
        """
        for имя, текст in (("системная", ASSISTANT_SYSTEM_PROMPT),
                           ("задание", ASSISTANT_TASK)):
            for оборот in ("и так далее", "и другие"):
                with self.subTest(инструкция=имя, оборот=оборот):
                    self.assertIn(оборот, текст,
                                  f"в «{имя}» нет запрета сокращать перечень")

    def test_названы_структура_поля_числа(self):
        вместе = (ASSISTANT_SYSTEM_PROMPT + ASSISTANT_TASK).lower()
        for слово in ("структур", "поля", "разрядност", "скорост",
                      "единиц", "таблиц", "сопостав"):
            with self.subTest(слово=слово):
                self.assertIn(слово, вместе)

    def test_объём_подставляется_числом_из_настроек(self):
        """Иначе настройку меняют, а модель просят о прежнем."""
        self.assertIn("{target_words}", ASSISTANT_TASK)
        self.assertIn("{target_words}", ASSISTANT_TASK_SHORT)


class КороткоеЗаданиеНеЛомаетОтвет(unittest.TestCase):
    """Уступая место источникам, задание теряет полноту, но не верность."""

    def test_главное_осталось(self):
        for правило, кусок in (
            ("ссылки на источники", "[S<номер>]"),
            ("числа только из источников", "не по памяти"),
            ("перечень целиком", "целиком"),
            ("граница про библиотеку", "НЕ ЧИСЛИТСЯ"),
            ("требование объёма", "{target_words}"),
        ):
            with self.subTest(правило=правило):
                self.assertIn(кусок, ASSISTANT_TASK_SHORT)

    def test_короткое_действительно_короткое(self):
        self.assertLess(len(ASSISTANT_TASK_SHORT), len(ASSISTANT_TASK) / 4,
                        "короткое задание не даёт выигрыша, ради которого "
                        "его и заводили")


class ПорядокУступок(unittest.TestCase):
    """Данные уступают ПОСЛЕ инструкций — иначе разбирать будет нечего."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings = Settings.load(
            data_dir=self._tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(ROOT / "templates"))
        self.repos = Repositories(Database(":memory:"))
        for n in range(12):
            документ = self.repos.documents.upsert(
                f"lib/tom{n}", "standards", f"Аппаратура ИКМ, том {n}",
                f"/lib/tom{n}.pdf", f"sha-tom{n}", meta={}, domain="other")
            self.repos.chunks.replace_for_document(документ, [Chunk(
                chunk_id=f"lib/tom{n}#{i}", doc_id=f"lib/tom{n}",
                doc_type="standards", title_path=[f"Аппаратура ИКМ, том {n}"],
                text="Цикл 125 мкс, канальные интервалы, сверхцикл. " * 30,
                meta={}) for i in range(3)])
        self.reports = ReportService(repos=self.repos, settings=self.settings,
                                     llm=StubLLM())
        self.assistant = AssistantService(reports=self.reports)
        self.user = self.repos.users.create("ivanov", "пароль123", "Иванов",
                                            "engineer")

    def разобрать(self, окно):
        self.settings.llm_context_tokens = окно
        self.settings.assistant_max_tokens = min(1200, окно // 4)
        chat = self.assistant.create_chat(self.user)
        return self.assistant._prepare(
            self.user, chat.id, "что такое канальный интервал и сверхцикл",
            top_k=None)

    def test_на_тесном_окне_уступает_задание_а_не_источники(self):
        prepared = self.разобрать(8192)
        self.assertNotIn("Порядок работы:", prepared["prompt"],
                         "подробное задание не уступило место источникам")
        self.assertIn("[S<номер>]", prepared["prompt"],
                      "из промпта пропало требование ссылаться")
        self.assertTrue(prepared["sources"], "источников не осталось вовсе")

    def test_о_сокращении_задания_сказано_словами(self):
        prepared = self.разобрать(8192)
        self.assertIn("менее развёрнутым", prepared["warning"] or "")

    def test_на_просторном_окне_задание_полное(self):
        prepared = self.разобрать(32768)
        self.assertIn("Порядок работы:", prepared["prompt"])
        self.assertNotIn("менее развёрнутым", prepared["warning"] or "")

    def test_число_объёма_доходит_до_промпта(self):
        self.settings.assistant_target_words = 1700
        prepared = self.разобрать(32768)
        self.assertIn("1700 слов", prepared["prompt"])


if __name__ == "__main__":
    unittest.main()
