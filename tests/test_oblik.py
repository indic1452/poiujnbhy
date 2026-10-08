"""Новый облик: приветствие сводки, путь писем, действия с ответом, клавиши.

Проверки не ищут подстроки там, где важно поведение: нужные функции
вырезаются из app.js как есть и выполняются в node на поддельной разметке.
Подстрокой проверяется только то, что подстрокой и является, — правила
стилей, у которых нет иного поведения, кроме того, что браузер их прочтёт.

Если node в системе нет, поведенческие проверки пропускаются: это среда
разработки, а не условие работы отдела.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "reportgen" / "web" / "static"
APP_JS = STATIC / "app.js"
STYLES = STATIC / "styles.css"
NODE = shutil.which("node")


def вырезать(исходник: str, имя: str) -> str:
    """Тело функции `имя` из app.js — от объявления до закрывающей скобки.

    Функции в app.js стоят с отступом, и закрывающая скобка стоит на том же
    отступе, что и слово function. По этому признаку и режем: разбирать
    JavaScript целиком ради шести функций незачем.
    """
    шаблон = re.compile(r"^( *)(?:async )?function " + re.escape(имя) + r"\(", re.M)
    найдено = шаблон.search(исходник)
    if not найдено:
        raise AssertionError(f"в app.js нет функции {имя}")
    отступ = найдено.group(1)
    конец = исходник.index("\n" + отступ + "}", найдено.end())
    return исходник[найдено.start():конец + len(отступ) + 2]


#: Поддельная разметка — ровно столько, сколько трогают проверяемые функции.
PRELUDE = r"""
function h(tag, attrs, ...kids) {
  const node = { tag: tag, attrs: attrs || {}, kids: [], dataset: {}, style: {},
    appendChild(c) { this.kids.push(c); } };
  const a = attrs || {};
  if (a.dataset) Object.assign(node.dataset, a.dataset);
  if (a.style) Object.assign(node.style, a.style);
  kids.flat(9).filter((k) => k !== null && k !== undefined && k !== false)
    .forEach((k) => node.kids.push(k));
  return node;
}
function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
function подсказка() {}
const state = { user: null };
"""


def выполнить(код: str):
    """Запустить кусок в node; последняя строка вывода — JSON с итогом."""
    итог = subprocess.run([NODE, "-e", код], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    if итог.returncode != 0:
        raise AssertionError("node упал:\n" + итог.stderr)
    return json.loads(итог.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node не установлен")
class ОбликНаДелеTests(unittest.TestCase):
    """Функции нового облика, выполненные по-настоящему."""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def куски(self, *имена):
        return PRELUDE + "\n".join(вырезать(self.js, имя) for имя in имена)

    # -- приветствие сводки ------------------------------------------------

    def test_приветствие_по_часу_суток(self):
        """Граница каждого времени суток — ровно на своём часе."""
        код = self.куски("имяОтчество", "приветствие") + r"""
        const RealDate = Date;
        const итог = {};
        for (const час of [0, 4, 5, 11, 12, 17, 18, 22, 23]) {
          global.Date = class extends RealDate { getHours() { return час; } };
          итог[час] = приветствие();
        }
        console.log(JSON.stringify(итог));
        """
        итог = выполнить(код)
        ждём = {"0": "Доброй ночи", "4": "Доброй ночи", "5": "Доброе утро",
                "11": "Доброе утро", "12": "Добрый день", "17": "Добрый день",
                "18": "Добрый вечер", "22": "Добрый вечер", "23": "Доброй ночи"}
        self.assertEqual(итог, ждём)

    def test_обращение_по_имени_отчеству(self):
        код = self.куски("имяОтчество", "приветствие") + r"""
        state.user = { full_name: 'Иванов Иван Иванович' };
        const полное = приветствие();
        console.log(JSON.stringify({
          полное: полное,
          три: имяОтчество('Петрова Анна Сергеевна'),
          два: имяОтчество('Сидоров Олег'),
          инициалы: имяОтчество('Иванов И. И.'),
          пусто: имяОтчество(''),
        }));
        """
        итог = выполнить(код)
        self.assertTrue(итог["полное"].endswith(", Иван Иванович"), итог["полное"])
        self.assertEqual(итог["три"], "Анна Сергеевна")
        self.assertEqual(итог["два"], "Олег")
        # По инициалам обращаться нельзя: «Доброе утро, И. И.» — не обращение.
        self.assertEqual(итог["инициалы"], "")
        self.assertEqual(итог["пусто"], "")

    def test_итог_дня_одной_строкой(self):
        код = self.куски("итогСловами") + r"""
        console.log(JSON.stringify({
          полный: итогСловами({ open: 6, overdue: 1, soon: 2, staff: 5, away: 1 }),
          тихо: итогСловами({ open: 0, overdue: 0, soon: 0, staff: 0 }),
          одно: итогСловами({ open: 1 }),
        }));
        """
        итог = выполнить(код)
        self.assertEqual(итог["полный"],
                         "6 писем в работе · просрочено: 1 · горят: 2 · в строю 4 из 5")
        self.assertEqual(итог["тихо"], "писем в работе нет · просрочек нет")
        self.assertEqual(итог["одно"], "1 письмо в работе · просрочек нет")

    # -- путь писем ------------------------------------------------------------

    def test_путь_писем_без_пустых_ступеней(self):
        """Ширина сегмента — число писем; пустая ступень не рисуется вовсе."""
        код = self.куски("путьПисем") + r"""
        const порядок = [
          { id: 'new', title: 'новые', count: 3 },
          { id: 'draft', title: 'черновик', count: 0 },
          { id: 'review', title: 'на проверке', count: 1 },
        ];
        const ступени = { new: 1, draft: 2, review: 3 };
        const полоса = путьПисем(порядок, 4, (id) => ступени[id]);
        console.log(JSON.stringify({
          класс: полоса.attrs.class,
          подпись: полоса.attrs['aria-label'],
          сегменты: полоса.kids.map((s) => [s.dataset.step, s.style.flexGrow]),
        }));
        """
        итог = выполнить(код)
        self.assertEqual(итог["класс"], "path-bar")
        self.assertEqual(итог["сегменты"], [["1", "3"], ["3", "1"]])
        # Подпись для чтеца экрана называет все ступени — и пустую тоже:
        # «черновиков нет» тоже сведение.
        self.assertEqual(итог["подпись"],
                         "Путь писем: новые — 3, черновик — 0, на проверке — 1")

    # -- обрезка текста подсказки ---------------------------------------------

    def test_обрезка_по_слову(self):
        код = self.куски("обрезать") + r"""
        console.log(JSON.stringify({
          коротко: обрезать('  канальный   интервал ', 50),
          длинно: обрезать('Поток E1 передаётся со скоростью 2048 кбит/с', 30),
          сплошь: обрезать('ааааааааааааааааааааааааааааааааааа', 10),
          пусто: обрезать(null, 10),
        }));
        """
        итог = выполнить(код)
        self.assertEqual(итог["коротко"], "канальный интервал")
        self.assertEqual(итог["длинно"], "Поток E1 передаётся со…")
        self.assertEqual(итог["сплошь"], "аааааааааа…")
        self.assertEqual(итог["пусто"], "")

    # -- действия с ответом ------------------------------------------------------

    def test_таблица_уходит_в_excel_табуляцией(self):
        """Ячейка с переносом строки не должна рвать ряд надвое."""
        код = self.куски("таблицаВТекст") + r"""
        const ряд = (...ячейки) => ({ cells: ячейки.map((t) => ({ innerText: t })) });
        const таблица = { rows: [
          ряд('Канальный\nинтервал', 'Назначение'),
          ряд('КИ0', '  цикловой   синхросигнал '),
        ] };
        console.log(JSON.stringify(таблицаВТекст(таблица)));
        """
        self.assertEqual(выполнить(код),
                         "Канальный интервал\tНазначение\nКИ0\tцикловой синхросигнал")

    def _буфер(self, защищённое: bool, clipboard_падает: bool = False):
        код = self.куски("вБуфер") + r"""
        const журнал = [];
        global.window = { isSecureContext: %s };
        // В node есть свой navigator, и простое присваивание он молча
        // пропускает — подменяем свойство целиком.
        Object.defineProperty(globalThis, 'navigator', { configurable: true,
          value: { clipboard: { async writeText(t) {
            if (%s) throw new Error('запрещено');
            журнал.push(['clipboard', t]); } } } });
        global.document = {
          body: { appendChild(n) { журнал.push(['поле', n.value]); } },
          execCommand(имя) { журнал.push(['команда', имя]); return true; },
        };
        const настоящий = h;
        h = function (tag, attrs) {
          const n = настоящий(tag, attrs);
          n.select = () => журнал.push(['выделено']);
          n.remove = () => журнал.push(['убрано']);
          return n;
        };
        вБуфер('ответ').then((ок) => console.log(JSON.stringify({ ок, журнал })));
        """ % ("true" if защищённое else "false", "true" if clipboard_падает else "false")
        return выполнить(код)

    def test_буфер_в_защищённом_окне(self):
        итог = self._буфер(защищённое=True)
        self.assertTrue(итог["ок"])
        self.assertEqual(итог["журнал"], [["clipboard", "ответ"]])

    def test_буфер_по_адресу_без_tls(self):
        """В сети отдела без https navigator.clipboard нет — выручает поле."""
        итог = self._буфер(защищённое=False)
        self.assertTrue(итог["ок"])
        self.assertEqual(итог["журнал"], [["поле", "ответ"], ["выделено"],
                                          ["команда", "copy"], ["убрано"]])

    def test_буфер_при_отказе_браузера(self):
        итог = self._буфер(защищённое=True, clipboard_падает=True)
        self.assertTrue(итог["ок"])
        self.assertIn(["команда", "copy"], итог["журнал"])

    def test_печать_одного_ответа_и_уборка_после(self):
        код = self.куски("печатьОтвета") + r"""
        const классы = (имя) => {
          const набор = new Set();
          return { add: (c) => набор.add(c), remove: (c) => набор.delete(c),
                   has: (c) => набор.has(c), имя };
        };
        const слушатели = {};
        let напечатано = 0;
        global.window = {
          addEventListener(имя, fn) { слушатели[имя] = fn; },
          removeEventListener(имя) { delete слушатели[имя]; },
          print() { напечатано += 1; },
        };
        global.document = { body: { classList: классы('body') } };
        const сообщение = { classList: классы('msg') };
        печатьОтвета(сообщение);
        const во_время = [сообщение.classList.has('is-printing'),
                          document.body.classList.has('print-one'), напечатано];
        слушатели.afterprint();
        const после = [сообщение.classList.has('is-printing'),
                       document.body.classList.has('print-one'),
                       'afterprint' in слушатели];
        печатьОтвета(null);
        console.log(JSON.stringify({ во_время, после, напечатано }));
        """
        итог = выполнить(код)
        self.assertEqual(итог["во_время"], [True, True, 1])
        # После печати страница обязана вернуться как была: иначе следующая
        # печать чего угодно выдаст только этот ответ.
        self.assertEqual(итог["после"], [False, False, False])
        self.assertEqual(итог["напечатано"], 1)

    # -- клавиша «?» ---------------------------------------------------------------

    def test_в_поле_ввода_вопрос_это_буква(self):
        код = self.куски("вводТекста") + r"""
        const цель = (внутри) => ({ closest: (sel) => внутри && sel.includes(внутри) ? {} : null });
        console.log(JSON.stringify({
          поле: вводТекста(цель('textarea')),
          строка: вводТекста(цель('input')),
          редактор: вводТекста(цель('[contenteditable="true"]')),
          страница: вводТекста(цель(null)),
          ничего: вводТекста(null),
        }));
        """
        итог = выполнить(код)
        self.assertEqual(итог, {"поле": True, "строка": True, "редактор": True,
                                "страница": False, "ничего": False})


class ШпаргалкаКлавишTests(unittest.TestCase):
    """Шпаргалка не должна обещать сочетаний, которых в системе нет."""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_каждое_сочетание_из_шпаргалки_есть_в_коде(self):
        лист = self.js.split("const КЛАВИШИ = [", 1)[1].split("\n    ];", 1)[0]
        сочетания = {tuple(re.findall(r"'([^']+)'", пара))
                     for пара in re.findall(r"\[\[([^\]]+)\]", лист)}
        self.assertIn(("Ctrl", "K"), сочетания)
        # Где в коде живёт каждое сочетание. Добавил строку в шпаргалку —
        # добавь и сюда, где она обрабатывается.
        где = {
            ("Ctrl", "K"): "event.key.toLowerCase() === 'k'",
            ("?",): "event.key !== '?'",
            ("Esc",): "event.key === 'Escape'",
            ("Enter",): "Enter — отправить",
            ("Shift", "Enter"): "Shift+Enter — новая строка",
            ("Ctrl", "S"): "event.code === 'KeyS'",
            ("Ctrl", "Enter"): "Ctrl+Enter",
            # Стол анализа: битовый просмотр (клавиша(e)) и таблица массивов.
            ("Shift", "F10"): "(e.shiftKey && e.key === 'F10')",
            ("F3",): "else if (e.key === 'F3') окноПоискаПериода(у);",
            ("F4",): "else if (e.key === 'F4') окноПоискаСкремблера(у);",
            ("T",): "else if (код === 'KeyT') окноОбрезки(у);",
            ("S",): "else if (код === 'KeyS') окноСведений(у);",
            ("M",): "else if (код === 'KeyM') переключитьРежим();",
            ("Delete",): "if (e.key === 'Delete') {",
            ("F1",): "else if (e.key === 'F1') справкаПросмотра();",
        }
        for сочетание in сочетания:
            with self.subTest(сочетание="+".join(сочетание)):
                self.assertIn(сочетание, где, "в шпаргалке сочетание без обработчика")
                self.assertIn(где[сочетание], self.js)

    def test_вопрос_в_поле_не_открывает_шпаргалку(self):
        обработчик = self.js.split("if (event.key !== '?'", 1)[1][:400]
        self.assertIn("вводТекста(event.target)", обработчик)
        # Поверх открытой палитры или окна второй лист не нужен.
        self.assertIn("cmd.open", обработчик)
        # Открытое модальное окно (плавающие окна стола — не в счёт): верхнееМодальное() ищет .modal-backdrop.
        self.assertIn("верхнееМодальное()", обработчик)
        помощник = self.js.split("function верхнееМодальное()", 1)[1][:300]
        self.assertIn(".modal-backdrop:not(.modal-backdrop--float)", помощник)

    def test_палитра_подсказывает_про_шпаргалку(self):
        self.assertIn("h('kbd', {}, '?'), ' все клавиши'", self.js)


class ОбликСтилиTests(unittest.TestCase):
    """Правила стилей, у которых нет поведения, кроме самого правила."""

    @classmethod
    def setUpClass(cls):
        cls.css = STYLES.read_text(encoding="utf-8")
        cls.js = APP_JS.read_text(encoding="utf-8")

    def правило(self, селектор: str) -> str:
        """Последнее объявление селектора: слой облика стоит в конце файла."""
        начало = self.css.rindex("\n" + селектор + " {")
        return self.css[начало:self.css.index("}", начало)]

    def test_знак_и_название_в_одну_строку(self):
        """Столбиком они не входили в высоту полосы, и знак срезался."""
        self.assertIn("flex-direction: row;", self.правило(".side-brand-text"))
        self.assertNotIn("margin-bottom", self.правило(".side-brand-text b"))

    def test_пустой_разговор_не_сжимается(self):
        self.assertIn("flex: 0 0 auto;", self.правило(".chat-empty"))

    def test_слово_в_ячейке_ответа_не_рвётся(self):
        правило = self.правило(".msg .body table th,\n.msg .body table td")
        self.assertIn("overflow-wrap: break-word;", правило)
        self.assertIn("word-break: normal;", правило)

    def test_печать_показывает_только_выбранный_ответ(self):
        печать = self.css.split("@media print {\n    body.print-one *", 1)[1]
        печать = печать[:печать.index("\n}\n")]
        self.assertIn("visibility: hidden !important;", печать)
        self.assertIn("body.print-one .msg.is-printing *", печать)
        self.assertIn("visibility: visible !important;", печать)
        self.assertIn("body.print-one .msg-actions", печать)

    def test_режим_чтения_убирает_панели(self):
        блок = self.css.split("body.reading .side,", 1)[1].split("}", 1)[0]
        for панель in (".panel--chatlist", ".panel--chatside", ".composer"):
            self.assertIn(панель, блок)
        self.assertIn("display: none !important;", блок)

    def test_esc_выводит_из_режима_чтения(self):
        обработчик = self.js.split("event.key === 'Escape' && document.body.classList"
                                   ".contains('reading')", 1)[1][:120]
        self.assertIn("режимЧтения(false)", обработчик)

    def test_каждая_ступень_пути_окрашена(self):
        for ступень in range(2, 7):
            self.assertIn(f'.path-seg[data-step="{ступень}"] {{ background: '
                          f'var(--seq-{ступень}); }}', self.css)

    def test_значки_облика_есть_в_наборе(self):
        """Имя значка без рисунка даёт пустой квадрат, а не ошибку."""
        набор = self.js.split("const ICONS = {", 1)[1].split("\n    };", 1)[0]
        есть = set(re.findall(r"^        (\w+):", набор, re.M))
        нужны = set(re.findall(r"\bicon\('(\w+)'\)", self.js))
        нужны |= set(re.findall(r"кнопка\('(\w+)'", self.js))
        # Значки плиток сводки — последнее слово в вызове tile(...).
        плитки = self.js.split("const tiles = h('div', { class: 'tiles' },", 1)[1]
        плитки = плитки.split("const columns", 1)[0]
        значки_плиток = set(re.findall(r"'(\w+)'\)+[,;]?\n", плитки))
        self.assertEqual(значки_плиток,
                         {"letters", "alert", "flame", "users", "roster", "send"})
        нужны |= значки_плиток
        self.assertFalse(нужны - есть, f"значков нет в наборе: {sorted(нужны - есть)}")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
