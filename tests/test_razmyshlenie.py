# -*- coding: utf-8 -*-
"""Рассуждение модели не должно ни попадать в ответ, ни съедать его.

Рабочая модель отдела — Qwen3-14B, гибридная: размышление у неё включено по
умолчанию, и блок «<think>…</think>» идёт первым, до ответа. Токены этого
блока считаются в тот же потолок, что и ответ.

Как это выглядело на живой машине. В логе llama-server три задачи подряд
закончились ровно на 120, ровно на 200 и ровно на 200 сгенерированных
токенов. Это в точности потолки из кода: RESEARCH_TOKENS у планировщика
разбора, NAME_TOKENS у добора документов по названию, GAP_TOKENS у сверки
комплектности. Каждый из этих заходов отвечает ОДНОЙ строкой — «ХВАТИТ» или
«G.703, G.704», то есть десятком токенов. Упереться в потолок такая строка не
может; упирается в него рассуждение, а до строки дело не доходит.

Значит на машине отдела молча не работали: цикл заходов по библиотеке, добор
документов по названию, сверка комплектности и реранк. Ровно то, что
начальник отдела называет «чтением и анализом всей библиотеки».

Чинится в трёх местах сразу, и намеренно с запасом — сборку llama.cpp на
изолированной машине не поменять, а полагаться на одну защиту здесь нельзя:

1. «/no_think» в служебных подсказках — переключатель Qwen3, который она
   понимает из самого текста, без «--jinja» и без пересборки сервера;
2. отсечение блока на приёме — работает при любой сборке и при любой модели;
3. поднятые потолки служебных заходов — чтобы заход выжил даже там, где
   первые две меры не сработали.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen import llm as модуль
from reportgen.llm import ОтсекательМысли, OpenAICompatLLM, без_мысли
from reportgen.prompts import (
    ASSISTANT_SYSTEM_PROMPT,
    DIGEST_SYSTEM_PROMPT,
    GAP_SYSTEM_PROMPT,
    NAME_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    БЕЗ_РАЗМЫШЛЕНИЯ,
)
from reportgen.web.assistant import GAP_TOKENS, NAME_TOKENS, RESEARCH_TOKENS
from reportgen.web.research import parse_step

ROOT = Path(__file__).resolve().parents[1]


class ОтсечениеЦеликомTests(unittest.TestCase):
    """Ответ пришёл одним куском — мысль из него убрана."""

    def test_закрытый_блок_вырезается(self):
        self.assertEqual(
            "Ответ [S1].",
            без_мысли("<think>прикину, что тут к чему</think>Ответ [S1]."))

    def test_текст_без_мысли_не_трогается(self):
        self.assertEqual("Ответ [S1].", без_мысли("Ответ [S1]."))

    def test_текст_до_блока_сохраняется(self):
        """Модель иногда начинает с ответа и только потом задумывается."""
        self.assertEqual(
            "Начало. Конец.",
            без_мысли("Начало. <think>сомневаюсь</think>Конец."))

    def test_незакрытая_мысль_не_показывается(self):
        """Потолок кончился посреди рассуждения — ответа нет вовсе.

        Показать обрывок значило бы выдать черновые мысли модели за разбор.
        """
        self.assertEqual("", без_мысли("<think>рассуждаю и не успеваю"))

    def test_несколько_блоков_подряд(self):
        self.assertEqual(
            "разодин два",
            без_мысли("<think>а</think>раз<think>б</think>один два"))


class ОтсечениеВПотокеTests(unittest.TestCase):
    """Поток идёт кусками, и тег рвётся между ними — обычное дело."""

    def прогнать(self, куски):
        отсекатель = ОтсекательМысли()
        видимое = "".join(отсекатель.кусок(к) for к in куски)
        return видимое + отсекатель.хвост(), отсекатель

    def test_тег_разорванный_между_кусками(self):
        текст, _ = self.прогнать(
            ["<thi", "nk>раз", "мышление</thi", "nk>Отв", "ет [S1]."])
        self.assertEqual("Ответ [S1].", текст)

    def test_поток_без_мысли_проходит_целиком(self):
        """Обычный ответ не должен задерживаться в буфере ни на знак."""
        текст, _ = self.прогнать(["Ответ ", "по ", "материалу ", "[S1]."])
        self.assertEqual("Ответ по материалу [S1].", текст)

    def test_поток_кончился_внутри_мысли(self):
        текст, отсекатель = self.прогнать(["<think>думаю и ", "не успеваю"])
        self.assertEqual("", текст)
        self.assertTrue(отсекатель.оборвалось_на_мысли,
                        "обрыв на рассуждении не отмечен")

    def test_обычный_поток_обрывом_не_считается(self):
        _, отсекатель = self.прогнать(["<think>кратко</think>Ответ."])
        self.assertFalse(отсекатель.оборвалось_на_мысли)

    def test_одинокая_угловая_скобка_не_теряется(self):
        """В тексте стандартов «<» встречается сам по себе: «BER < 10^-6»."""
        текст, _ = self.прогнать(["Порог BER < 10^-6 по [S1].", ""])
        self.assertEqual("Порог BER < 10^-6 по [S1].", текст)


class ТелоЗапросаTests(unittest.TestCase):
    """Что именно уходит на llama-server."""

    def тело(self, **kwargs):
        клиент = OpenAICompatLLM()
        return клиент._payload("система", "вопрос", 100, 0.2, None, **kwargs)

    def test_просим_не_рассуждать(self):
        """Работает только при «--jinja», но вреда от поля нет нигде."""
        self.assertEqual({"enable_thinking": False},
                         self.тело()["chat_template_kwargs"])

    def test_в_потоке_просим_расход_токенов(self):
        """Без этого настоящий размер промпта неизвестен и непроверяем."""
        self.assertEqual({"include_usage": True},
                         self.тело(stream=True)["stream_options"])


class ПереключательВПодсказкахTests(unittest.TestCase):
    """«/no_think» — служебным заходам, и только им."""

    СЛУЖЕБНЫЕ = {
        "добор по названию": NAME_SYSTEM_PROMPT,
        "сверка комплектности": GAP_SYSTEM_PROMPT,
        "выписка": DIGEST_SYSTEM_PROMPT,
        "планировщик разбора": RESEARCH_SYSTEM_PROMPT,
    }

    def test_переключатель_это_именно_то_слово(self):
        """Проверять «кончается на БЕЗ_РАЗМЫШЛЕНИЯ» мало.

        Опустошить константу — и такая проверка станет истинной для любой
        строки: на пустое кончается всё. Сторожим само слово, которое понимает
        модель, а не имя, которым мы его назвали.
        """
        self.assertEqual(" /no_think", БЕЗ_РАЗМЫШЛЕНИЯ)

    def test_каждый_служебный_заход_просит_не_рассуждать(self):
        for имя, подсказка in self.СЛУЖЕБНЫЕ.items():
            with self.subTest(заход=имя):
                self.assertTrue(подсказка.rstrip().endswith("/no_think"),
                                f"{имя}: переключателя нет")

    def test_главному_ответу_рассуждать_не_запрещают(self):
        """Там размышление помогает: связать стандарт с паспортом и заметить
        расхождение. Потолка ему хватает, а блок мысли отсекается на приёме.
        """
        self.assertNotIn("/no_think", ASSISTANT_SYSTEM_PROMPT)

    def test_реранк_тоже_просит_не_рассуждать(self):
        """Иначе он молча скатывается в порядок выдачи поиска."""
        from reportgen.rerank import LLMReranker

        self.assertIn("/no_think", LLMReranker.SYSTEM)


class ПотолкиСлужебныхЗаходовTests(unittest.TestCase):
    """Потолок должен пережить рассуждение, если переключатель не услышан."""

    def test_потолки_выше_обычного_блока_рассуждения(self):
        # Прежние 120 и 200 рассуждение съедало целиком — это и видно в логе
        # отдела. Пятисот мало, семьсот — с запасом.
        for имя, значение in (("планировщик", RESEARCH_TOKENS),
                              ("по названию", NAME_TOKENS),
                              ("сверка", GAP_TOKENS)):
            with self.subTest(заход=имя):
                self.assertGreaterEqual(значение, 500, f"{имя}: потолок мал")


class ЛовушкаВРазбореШагаTests(unittest.TestCase):
    """Русская фраза не должна становиться командой разбора.

    Беда не только в рассуждении: словарь шагов относил к «ЗАКОНЧИТЬ» слово
    «ответ», а разделитель перед аргументом был необязателен. Поэтому любая
    фраза, начинающаяся с нужного слова, становилась шагом — и рассуждение
    лишь умножало число входов в эту ловушку.
    """

    ОПАСНЫЕ = (
        "Ответ на этот вопрос требует данных по G.704.",
        "Достаточно ли у меня данных? Пока нет.",
        "Поиск по слову «уплотнение» ничего не дал.",
        "Готово будет, когда найдётся структура цикла.",
    )

    def test_фраза_не_обрывает_разбор(self):
        for фраза in self.ОПАСНЫЕ:
            with self.subTest(фраза=фраза):
                шаг = parse_step(f"{фраза}\nИСКАТЬ: структура цикла G.704")
                self.assertIsNotNone(шаг, "шаг не найден вовсе")
                self.assertEqual("искать", шаг.kind,
                                 f"«{фраза}» разобрана как команда")
                self.assertEqual("структура цикла G.704", шаг.argument)

    def test_фраза_сама_по_себе_шагом_не_считается(self):
        for фраза in self.ОПАСНЫЕ:
            with self.subTest(фраза=фраза):
                self.assertIsNone(parse_step(фраза))

    def test_настоящая_команда_по_прежнему_понятна(self):
        self.assertEqual("хватит", parse_step("ХВАТИТ").kind)
        self.assertEqual("хватит", parse_step("Достаточно").kind)
        self.assertEqual("искать", parse_step("ИСКАТЬ: кадр").kind)

    def test_рассуждение_перед_командой_не_мешает(self):
        разбор = parse_step(
            "<think>Надо понять, что спрашивают. Ответ требует G.704.</think>\n"
            "ИСКАТЬ: канальные интервалы")
        self.assertEqual("искать", разбор.kind)
        self.assertEqual("канальные интервалы", разбор.argument)


class ОбрывПоДлинеВиденTests(unittest.TestCase):
    """Обрезанный ответ выглядит законченным — значит, надо сказать словами."""

    def тело_ответа(self, *, обрыв, текст="Ответ [S1]."):
        return json.dumps({
            "choices": [{"message": {"content": текст}, "finish_reason": обрыв}],
            "usage": {"prompt_tokens": 18000, "completion_tokens": 7000},
        }).encode("utf-8")

    def подменить(self, тело):
        class Ответ:
            status = 200

            def read(self):
                return тело

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        прежний = модуль._http.urlopen
        модуль._http.urlopen = lambda *a, **k: Ответ()
        self.addCleanup(lambda: setattr(модуль._http, "urlopen", прежний))

    def test_обрыв_по_потолку_запомнен(self):
        self.подменить(self.тело_ответа(обрыв="length"))
        клиент = OpenAICompatLLM()
        self.assertEqual("Ответ [S1].", клиент.complete("с", "в"))
        self.assertEqual("length", клиент.последний_обрыв)

    def test_обычное_завершение_обрывом_не_зовётся(self):
        self.подменить(self.тело_ответа(обрыв="stop"))
        клиент = OpenAICompatLLM()
        клиент.complete("с", "в")
        self.assertEqual("stop", клиент.последний_обрыв)

    def test_расход_токенов_запомнен(self):
        """Оценка «полтора знака на токен» ничем не проверялась — вот замер."""
        self.подменить(self.тело_ответа(обрыв="stop"))
        клиент = OpenAICompatLLM()
        клиент.complete("с", "в")
        self.assertEqual(18000, клиент.последний_расход.get("prompt_tokens"))

    def test_мысль_вырезана_и_при_обычном_вызове(self):
        self.подменить(self.тело_ответа(
            обрыв="stop", текст="<think>прикидываю</think>Ответ [S1]."))
        self.assertEqual("Ответ [S1].", OpenAICompatLLM().complete("с", "в"))


class ПлашкаОбОбрывеTests(unittest.TestCase):
    """Инженер должен увидеть, что ответ не закончен."""

    def setUp(self):
        статика = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (статика / "app.js").read_text(encoding="utf-8")
        self.css = (статика / "styles.css").read_text(encoding="utf-8")

    def test_плашка_есть_и_подключена(self):
        self.assertIn("function cutNote(cut)", self.js)
        self.assertIn("cutNote(message.meta.cut)", self.js)

    def test_разведены_два_разных_обрыва(self):
        плашка = self.js.split("function cutNote(cut)", 1)[1].split("\n    }", 1)[0]
        self.assertIn("'length'", плашка)
        self.assertIn("'размышление'", плашка)

    def test_плашка_говорит_что_делать(self):
        плашка = self.js.split("function cutNote(cut)", 1)[1].split("\n    }", 1)[0]
        self.assertIn("продолжить", плашка)

    def test_стиль_плашки_есть_в_обеих_темах(self):
        self.assertIn(".msg-note--warn {", self.css)
        self.assertIn("--warn-soft:", self.css)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
