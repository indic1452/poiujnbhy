# -*- coding: utf-8 -*-
"""Окно модели спрашивается у сервера, а не берётся на веру из настройки.

Окно стояло в ДВУХ местах: в «-c» при запуске llama-server и в
settings.json. Держать их согласованными руками не вышло, и обе стороны
расхождения отдел прочувствовал.

Настройка БОЛЬШЕ настоящего окна — «Ошибка при обращении к модели: 36061
токенов, а так размер 32768». Промпт собирался под одно число, сервер
работал по другому, ответа не было вовсе. Самый частый источник этого
расхождения — «--parallel»: «-c 32768 --parallel 2» означает 32768 на ВСЕ
слоты, то есть 16384 на разговор, а в настройке стоит 32768.

Настройка МЕНЬШЕ — тише и потому хуже: подняли «-c» ради развёрнутых
ответов, а помощник об этом не узнал и продолжил урезать материал под
прежнее число. Никакой ошибки, просто ответ беднее, чем мог бы.

llama-server отдаёт настоящее число сам, по «GET /props», и оно уже
пооткошное — ровно то, что достанется нашему запросу.
"""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.config import Settings
from reportgen.llm import OpenAICompatLLM, StubLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService, _расхождение_окна

ROOT = Path(__file__).resolve().parents[1]


class Сервер(StubLLM):
    """Модель, которая называет своё окно, — как настоящий llama-server."""

    def __init__(self, окно):
        self._окно = окно
        self.спросили = 0

    def context_tokens(self, timeout: float = 3.0) -> int:
        self.спросили += 1
        return self._окно


class Молчун(StubLLM):
    """Сервер есть, а про окно не говорит: не llama.cpp или старая сборка."""

    def context_tokens(self, timeout: float = 3.0) -> int:
        return 0


class Обиженный(StubLLM):
    """Сервер не поднят — запрос падает."""

    def context_tokens(self, timeout: float = 3.0) -> int:
        raise OSError("соединение отклонено")


class Основа(unittest.TestCase):
    def собрать(self, llm, **настройки):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings.load(data_dir=tmp.name, db_path=":memory:",
                                 templates_dir=str(ROOT / "templates"),
                                 **настройки)
        reports = ReportService(repos=Repositories(Database(":memory:")),
                                settings=settings, llm=llm)
        return reports, AssistantService(reports=reports)


class ОкноБерётсяУСервера(Основа):
    def test_сказанное_сервером_сильнее_настройки(self):
        _, помощник = self.собрать(Сервер(16384), llm_context_tokens=32768)
        self.assertEqual(16384, помощник._context_tokens())

    def test_поднятое_окно_подхватывается_без_правки_настроек(self):
        _, помощник = self.собрать(Сервер(65536), llm_context_tokens=32768)
        self.assertEqual(65536, помощник._context_tokens())
        self.assertGreater(помощник._context_chars(), 40000,
                           "материалу не досталось выросшего окна")

    def test_молчащий_сервер_оставляет_настройку(self):
        _, помощник = self.собрать(Молчун(), llm_context_tokens=24576)
        self.assertEqual(24576, помощник._context_tokens())

    def test_упавший_запрос_не_ломает_ответ(self):
        """Сервер не поднят — это не повод не собрать промпт."""
        _, помощник = self.собрать(Обиженный(), llm_context_tokens=24576)
        self.assertEqual(24576, помощник._context_tokens())

    def test_модель_без_такого_умения_работает_по_старому(self):
        """StubLLM и прочие клиенты про /props не знают."""
        _, помощник = self.собрать(StubLLM(), llm_context_tokens=20480)
        self.assertEqual(20480, помощник._context_tokens())

    def test_спрашиваем_один_раз_а_не_на_каждый_вопрос(self):
        """Помощник создаётся на запрос, а служба отчётов одна на всех."""
        reports, _ = self.собрать(Сервер(16384))
        for _ in range(5):
            AssistantService(reports=reports)._context_tokens()
        self.assertEqual(1, reports.get_llm().спросили)


class РасхождениеНазваноСловами(unittest.TestCase):
    def test_окно_меньше_настройки_объяснено_через_parallel(self):
        сказано = _расхождение_окна(16384, 32768)
        self.assertIn("16384", сказано)
        self.assertIn("32768", сказано)
        self.assertIn("parallel", сказано.lower())

    def test_окно_больше_настройки_тоже_названо(self):
        сказано = _расхождение_окна(65536, 32768)
        self.assertIn("65536", сказано)
        self.assertNotIn("parallel", сказано.lower(),
                         "при выросшем окне слоты ни при чём")

    def test_когда_всё_сходится_молчим(self):
        self.assertEqual("", _расхождение_окна(32768, 32768))

    def test_когда_сервер_не_сказал_молчим(self):
        """Сравнивать не с чем: обвинять настройку не в чем."""
        self.assertEqual("", _расхождение_окна(0, 32768))


class БюджетВиденВМетриках(Основа):
    def test_названо_откуда_взято_окно(self):
        reports, _ = self.собрать(Сервер(16384), llm_context_tokens=32768)
        сводка = reports.model_budget()
        self.assertEqual(16384, сводка["context_tokens"])
        self.assertEqual("сервер", сводка["context_from"])
        self.assertIn("16384", сводка["context_note"])

    def test_настройка_помечена_как_запасная(self):
        reports, _ = self.собрать(Молчун(), llm_context_tokens=32768)
        сводка = reports.model_budget()
        self.assertEqual("настройка", сводка["context_from"])
        self.assertEqual("", сводка["context_note"])

    def test_видно_как_поделено_окно(self):
        """Ради этих трёх чисел карточка и заведена."""
        reports, _ = self.собрать(Сервер(32768))
        сводка = reports.model_budget()
        self.assertGreater(сводка["answer_tokens"], 0)
        self.assertGreater(сводка["library_chars"], 0)
        self.assertGreater(сводка["target_words"], 0)
        # Ответ и материал вместе не могут превышать окно — иначе карточка
        # обещает то, чего нет.
        занято = (сводка["answer_tokens"]
                  + сводка["library_chars"] / 1.5)
        self.assertLessEqual(занято, сводка["context_tokens"])

    def test_стата_несёт_бюджет_модели(self):
        reports, _ = self.собрать(Сервер(32768))
        self.assertIn("model", reports.stats())


class ЧислоПооткошное(unittest.TestCase):
    """Разбор ответа /props: берём именно окно СЛОТА."""

    def разобрать(self, тело):
        клиент = OpenAICompatLLM(base_url="http://127.0.0.1:8000/v1")
        видели = {}

        class Ответ:
            status = 200

            def read(self_):
                return json.dumps(тело).encode("utf-8")

            def __enter__(self_):
                return self_

            def __exit__(self_, *_):
                return False

        import reportgen.llm as модуль
        было = модуль._http.urlopen

        def подмена(request, timeout=None):
            видели["url"] = request.full_url
            return Ответ()

        модуль._http.urlopen = подмена
        try:
            return клиент.context_tokens(), видели.get("url", "")
        finally:
            модуль._http.urlopen = было

    def test_берём_окно_слота(self):
        число, _ = self.разобрать(
            {"default_generation_settings": {"n_ctx": 16384}, "n_ctx": 32768})
        self.assertEqual(16384, число,
                         "взято общее окно вместо пооткошного: при "
                         "--parallel 2 это вдвое больше правды")

    def test_запасное_поле_когда_слотового_нет(self):
        число, _ = self.разобрать({"n_ctx": 8192})
        self.assertEqual(8192, число)

    def test_пустое_слотовое_поле_не_заслоняет_запасное(self):
        """Так отвечает сервер, пока модель ещё грузится.

        Слоты не заведены, и окно слота — ноль; общее окно при этом уже
        известно. Принять ноль за ответ значило бы решить, что сервер про
        окно не сказал, и уйти на настройку — при живом числе рядом.
        """
        число, _ = self.разобрать(
            {"default_generation_settings": {"n_ctx": 0}, "n_ctx": 32768})
        self.assertEqual(32768, число)

    def test_чужой_ответ_даёт_ноль(self):
        for тело in ({}, {"n_ctx": 0}, {"n_ctx": "много"}):
            with self.subTest(тело=тело):
                self.assertEqual(0, self.разобрать(тело)[0])

    def test_спрашиваем_в_корне_а_не_внутри_v1(self):
        """/props лежит у llama-server в корне; base_url указывает на /v1."""
        _, адрес = self.разобрать({"n_ctx": 4096})
        self.assertEqual("http://127.0.0.1:8000/props", адрес)


if __name__ == "__main__":
    unittest.main()
