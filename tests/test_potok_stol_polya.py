"""Стол — единственная обработка потока, деревом массивов.

Загрузка потока и любой производный массив ведут на стол; правая кнопка мыши на
битовом поле открывает всё меню операций (со всеми разделами), а сверху — действия
по месту щелчка; из выделенных столбцов делаются именованные поля: их значения
видны в строке под курсором и сводятся по видимым строкам (постоянно, счётчик,
меняется). Чистые функции полей выполняются в node (как в test_oblik), новые
операции меню прогоняются через разбор сервера с их значениями по умолчанию.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok.razbor import снять_вручную
from test_oblik import вырезать

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "src" / "reportgen" / "web" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

PRELUDE = r"""
const хранилище = {};
global.localStorage = {
  getItem(k) { if (global.сломано) throw new Error('нет'); return k in хранилище ? хранилище[k] : null; },
  setItem(k, v) { if (global.сломано) throw new Error('нет'); хранилище[k] = String(v); },
};
"""


def выполнить(сценарий: str):
    код = PRELUDE + "\n".join(вырезать(APP, имя) for имя in (
        "ключСтола", "хранилищеСтола", "сохранитьСтола", "поляМассива", "сохранитьПоля",
        "остальныеСтолбцы", "значениеПоляСтрокой", "описаниеЗначений")) + "\n" + сценарий
    итог = subprocess.run([NODE, "-e", код], capture_output=True, text=True, timeout=30)
    if итог.returncode:
        raise AssertionError(итог.stderr)
    return json.loads(итог.stdout)


def операция(ид: str) -> str:
    найдено = re.search(r"\{ id: '" + re.escape(ид) + r"'.*?\}(?:\] )?\},?\n", APP, re.S)
    assert найдено, ид
    return найдено.group(0)


@unittest.skipUnless(NODE, "нужен node")
class ПоляTests(unittest.TestCase):
    def test_остальные_столбцы(self):
        self.assertEqual([[0, 3, 4], [], [0, 1, 2]], выполнить("""
            console.log(JSON.stringify([остальныеСтолбцы(5, new Set([1, 2])), остальныеСтолбцы(2, new Set([0, 1])),
                                        остальныеСтолбцы(3, new Set([7]))]));"""))

    def test_значение_поля(self):
        self.assertEqual(["1010 (0xA, 10)", "000000001 (0x001, 1)", "", "1" * 33,
                          "1" * 64 + "…", "1" * 32 + " (0xFFFFFFFF, 4294967295)"], выполнить("""
            console.log(JSON.stringify([значениеПоляСтрокой('1010'), значениеПоляСтрокой('000000001'), значениеПоляСтрокой(''),
                значениеПоляСтрокой('1'.repeat(33)), значениеПоляСтрокой('1'.repeat(65)), значениеПоляСтрокой('1'.repeat(32))]));"""))

    def test_описание_значений(self):
        итог = выполнить("""
            const счёт = []; for (let i = 0; i < 20; i += 1) счёт.push(((i + 14) % 16).toString(2).padStart(4, '0'));
            const сбой = счёт.slice(); сбой[5] = '0000'; сбой[6] = '0000';
            const редко = Array(30).fill('01'); редко[15] = '10'; редко[16] = '10';
            const почти = Array(10).fill('01'); почти[5] = '10';
            console.log(JSON.stringify([описаниеЗначений([]), описаниеЗначений(['11', '11', '11']), описаниеЗначений(счёт),
                описаниеЗначений(сбой), описаниеЗначений(редко), описаниеЗначений(почти),
                описаниеЗначений(['0'.repeat(40), '1'.repeat(40)]),
                описаниеЗначений(Array(15).fill('01').concat(Array(15).fill('10'))),
                описаниеЗначений(['1'.repeat(32), '0'.repeat(32), '0'.repeat(31) + '1'])]));""")
        self.assertEqual("нет видимых строк", итог[0])
        self.assertEqual("постоянно 11 (0x3, 3)", итог[1])
        # Счётчик по модулю 2^n: переход 15 → 0 — тоже +1.
        self.assertEqual("счётчик +1 (19 из 19 переходов), с 1110 (0xE, 14)", итог[2])
        self.assertEqual("меняется: значений 14 из 20 строк", итог[3])
        self.assertEqual("меняется редко: 2 смен, значений 2", итог[4])
        # 2 смены на 9 переходов — больше 10 %: не «редко».
        self.assertEqual("меняется: значений 2 из 10 строк", итог[5])
        self.assertEqual("меняется: значений 2 из 2 строк", итог[6])
        # Одна смена за 29 переходов — «редко», хотя половина строк отличается от первой.
        self.assertEqual("меняется редко: 1 смен, значений 2", итог[7])
        # Счётчик и у поля в 32 бита: переход 2^32 − 1 → 0.
        self.assertTrue(итог[8].startswith("счётчик +1 (2 из 2"), итог[8])

    def test_счётчик_на_границе_доли(self):
        # 9 переходов +1 из 10 — ровно 90 %: ещё счётчик; 8 из 10 — уже нет.
        итог = выполнить("""
            const а = []; for (let i = 0; i < 11; i += 1) а.push((i % 8).toString(2).padStart(3, '0'));
            const б = а.slice(); б[10] = '111';
            const в = б.slice(); в[9] = '000';
            console.log(JSON.stringify([описаниеЗначений(б), описаниеЗначений(в)]));""")
        self.assertTrue(итог[0].startswith("счётчик +1 (9 из 10"), итог[0])
        self.assertFalse(итог[1].startswith("счётчик"), итог[1])

    def test_поля_по_массивам_и_без_хранилища(self):
        итог = выполнить("""
            const а = { ключ: 'j1:2' }, б = { ключ: 'j1:3' };
            сохранитьПоля(а, [{ имя: 'счётчик', от: 3, длина: 4, ширина: 64 }]);
            const есть = [поляМассива(а), поляМассива(б)];
            global.сломано = true;
            сохранитьПоля(б, [{ имя: 'x', от: 0, длина: 1, ширина: 8 }]);
            есть.push(поляМассива(а));
            global.сломано = false;
            localStorage.setItem('stol-fields@', JSON.stringify({ 'j1:2': 'не список' }));   // ключ — с пользователем
            есть.push(поляМассива(а));
            console.log(JSON.stringify(есть));""")
        self.assertEqual([[{"имя": "счётчик", "от": 3, "длина": 4, "ширина": 64}], [], [], []], итог)


class МенюСтолаTests(unittest.TestCase):
    def test_пкм_на_битовом_поле_открывает_все_операции(self):
        начало = APP.index("холст.addEventListener('contextmenu'")
        обработчик = APP[начало:APP.index("});", начало)]
        self.assertIn("меню(e.clientX, e.clientY, пункты)", обработчик)
        self.assertNotIn("всплывающееМеню", обработчик)
        for пункт in ("Поле из выделенных столбцов", "Убрать выделенные столбцы", "удалить поле",
                      "Поля: значения по видимым строкам", "Начать поток с этого бита → новая ветка (F)", "Копировать ",
                      "Закладка на ", "Сравнить с другим массивом", "Сведения о массиве"):
            self.assertIn(пункт, обработчик)
        # Подменю у нижнего края окна не уходит за окно.
        self.assertIn("группа.addEventListener('mouseenter', () => { if (открытое && открытое.dataset.mouse) открытьГруппу(группа, false); });", APP)
        # Действия по месту — над разделами операций.
        self.assertIn("function меню(x, y, сверху)", APP)
        self.assertIn("stol-menu-top", APP[APP.index("function меню(x, y, сверху)"):])

    def test_колесо_масштаб_и_ширина(self):
        # Колесо битового просмотра — тот обработчик холста, что листает (в файле есть и другие холсты
        # с колесом: созвездие моддекодера, облако I/Q — у них колесо только масштабирует).
        обработчики = []
        начало = APP.find("холст.addEventListener('wheel'")
        while начало >= 0:
            обработчики.append(APP[начало:APP.index("}, { passive: false });", начало)])
            начало = APP.find("холст.addEventListener('wheel'", начало + 1)
        обработчик = next(о for о in обработчики if "шир(" in о)
        # Ctrl + колесо — масштаб (как в настольных средствах), Alt + колесо — длина строки ± шаг.
        self.assertIn("if (e.ctrlKey || e.metaKey) { приблизить(-знак); return; }", обработчик)
        self.assertIn("if (e.altKey) { шир(-знак * с.шаг); return; }", обработчик)
        # Ctrl и Alt проверяются раньше Shift: иначе Ctrl+Shift листал бы столбцы.
        self.assertLess(обработчик.index("e.ctrlKey"), обработчик.index("if (e.shiftKey)"))
        self.assertLess(обработчик.index("e.altKey"), обработчик.index("if (e.shiftKey)"))

    def test_загрузка_и_производные_ведут_на_стол(self):
        self.assertNotIn("navigate('#/potok/' + encodeURIComponent(data.id))", APP)
        self.assertGreaterEqual(APP.count("navigate('#/stol/' + encodeURIComponent(data.id))"), 8)

    def test_новые_операции_понимает_сервер(self):
        rng = np.random.default_rng(2)
        биты = rng.integers(0, 2, 4000).astype(np.uint8)
        for ид, ожидание in (("r-flip", биты[::-1]),
                             ("r-drop", np.delete(биты, np.arange(0, 4000, 8))),
                             ("r-insert", None), ("m-integr", np.bitwise_xor.accumulate(биты))):
            with self.subTest(ид=ид):
                текст = операция(ид)
                слой = re.search(r"слой: '([^']+)'", текст).group(1)
                for ключ, по in re.findall(r"ключ: '([^']+)'.*?по: (\d+)", текст):
                    слой = слой.replace("{" + ключ + "}", по)
                self.assertNotIn("{", слой)
                ряд = снять_вручную(биты, слой)[0]
                if ожидание is not None:
                    self.assertTrue(np.array_equal(ожидание, ряд), слой)
                else:
                    # Каждое 8-е место итога (с фазы 0) — вставленный 0, остальные — исходные подряд.
                    self.assertFalse(ряд[::8].any())
                    self.assertTrue(np.array_equal(биты, np.delete(ряд, np.arange(0, len(ряд), 8))[:4000]))
        for ид in ("m-4b5b", "m-8b10b", "r-split", "r-cutcols"):
            self.assertIn(ид, APP)


if __name__ == "__main__":
    unittest.main()
