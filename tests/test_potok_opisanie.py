# -*- coding: utf-8 -*-
"""Описание обработки — в правой сворачиваемой панели, а не рядом с деревом.

Пояснения «как это работает» на страницах разбора потока, рабочего стола и
пакетов пишутся в панель справа; свёрнута она по умолчанию, открыта ли —
помнит браузер. Поведение панели проверяется по-настоящему: функции
вырезаются из app.js и выполняются в node на поддельной разметке (как в
test_oblik); что тексты ушли из дерева и что страницы строят панель —
подстрокой.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from test_oblik import вырезать

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "reportgen" / "web" / "static"
NODE = shutil.which("node")

#: Поддельная разметка: ровно то, что трогает панель описания.
PRELUDE = r"""
function h(tag, attrs, ...kids) {
  const a = attrs || {};
  const node = {
    tag: tag, attrs: Object.assign({}, a), kids: [], parent: null, handlers: {}, hidden: !!a.hidden, id: a.id || '',
    classList: {
      set: new Set(String(a.class || '').split(/\s+/).filter(Boolean)),
      toggle(c, on) { if (on === undefined ? !this.set.has(c) : on) this.set.add(c); else this.set.delete(c); },
      contains(c) { return this.set.has(c); },
    },
    get children() { return this.kids.filter((k) => typeof k === 'object'); },
    getAttribute(k) { return k in this.attrs ? String(this.attrs[k]) : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    appendChild(c) { if (typeof c === 'object') c.parent = this; this.kids.push(c); return c; },
    insertBefore(c, ref) { c.parent = this; const i = ref ? this.kids.indexOf(ref) : -1; if (i < 0) this.kids.push(c); else this.kids.splice(i, 0, c); return c; },
    replaceWith(n) { const p = this.parent; p.kids[p.kids.indexOf(this)] = n; n.parent = p; this.parent = null; },
    remove() { const p = this.parent; if (p) { p.kids.splice(p.kids.indexOf(this), 1); this.parent = null; } },
    addEventListener(t, f) { (this.handlers[t] = this.handlers[t] || []).push(f); },
    fire(t, e) { (this.handlers[t] || []).forEach((f) => f(e || {})); },
    focus() { global.фокус = this; },
    get isConnected() { let n = this; while (n.parent) n = n.parent; return n === global.документ; },
    get text() { return this.kids.map((k) => (typeof k === 'object' ? k.text : String(k))).join(''); },
  };
  kids.flat(9).filter((k) => k !== null && k !== undefined && k !== false).forEach((k) => node.appendChild(k));
  return node;
}
function append(p, arr) { arr.flat(9).filter((k) => k !== null && k !== undefined && k !== false).forEach((k) => p.appendChild(k)); }
global.документ = h('body', {});
const хранилище = {};
global.localStorage = {
  getItem(k) { if (global.сломано) throw new Error('нет хранилища'); return k in хранилище ? хранилище[k] : null; },
  setItem(k, v) { if (global.сломано) throw new Error('нет хранилища'); хранилище[k] = String(v); },
};
function найти(узел, класс) {
  if (узел.classList && узел.classList.contains(класс)) return узел;
  for (const к of узел.children || []) { const н = найти(к, класс); if (н) return н; }
  return null;
}
function состояние(о) {
  const панель = найти(о.узел, 'opis-panel'), ручка = найти(о.узел, 'opis-handle');
  return { открыто: о.узел.classList.contains('is-open'), панель_скрыта: панель.hidden, ручка_скрыта: ручка.hidden,
           развёрнута: ручка.getAttribute('aria-expanded'),
           разделы: найти(о.узел, 'opis-body').children.map((с) => с.children[0].text) };
}
"""


def куски(js: str) -> str:
    """Константы панели и обе её функции — как они стоят в app.js."""
    начало = js.index("    const ОПИСАНИЕ = ")
    конец = js.index("\n    function сценаОписания(")
    return PRELUDE + js[начало:конец] + "\n" + вырезать(js, "сценаОписания") + "\n" + вырезать(js, "вОписание")


def выполнить(код: str):
    итог = subprocess.run([NODE, "-e", код], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if итог.returncode != 0:
        raise AssertionError("node упал:\n" + итог.stderr)
    return json.loads(итог.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "node не установлен")
class ПанельНаДеле(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_свёрнута_открывается_и_запоминается(self):
        код = куски(self.js) + r"""
        const итог = {};
        let о = сценаОписания(h('div', { class: 'page potok' }), 'potok');
        документ.appendChild(о.узел);
        итог.сначала = состояние(о);
        найти(о.узел, 'opis-handle').fire('click');
        итог.открыли = состояние(о);
        итог.фокус_на_свернуть = global.фокус && global.фокус.attrs['aria-label'];
        итог.запомнено = localStorage.getItem('opis-open:potok');
        о = сценаОписания(h('div', { class: 'page potok' }), 'potok');     // та же страница заново
        итог.заново = состояние(о);
        итог.другой_раздел = состояние(сценаОписания(h('div', {}), 'pakety'));
        найти(о.узел, 'opis-panel').fire('keydown', { key: 'Escape' });
        итог.escape = состояние(о);
        итог.после_escape = localStorage.getItem('opis-open:potok');
        найти(о.узел, 'opis-handle').fire('click');
        найти(о.узел, 'opis-head').children[1].fire('click');              // «›» — свернуть
        итог.свернули = состояние(о);
        console.log(JSON.stringify(итог));
        """
        итог = выполнить(код)
        свёрнута = {"открыто": False, "панель_скрыта": True, "ручка_скрыта": False, "развёрнута": "false", "разделы": []}
        self.assertEqual(итог["сначала"], свёрнута)
        self.assertEqual(итог["открыли"], {**свёрнута, "открыто": True, "панель_скрыта": False, "ручка_скрыта": True,
                                           "развёрнута": "true"})
        self.assertEqual(итог["фокус_на_свернуть"], "Свернуть описание вправо")
        self.assertEqual(итог["запомнено"], "1")
        self.assertTrue(итог["заново"]["открыто"])
        self.assertFalse(итог["другой_раздел"]["открыто"])                 # помнится по разделу
        self.assertFalse(итог["escape"]["открыто"])
        self.assertEqual(итог["после_escape"], "0")
        self.assertEqual(итог["свернули"], свёрнута)

    def test_разделы_по_порядку_страницы_и_без_повторов(self):
        код = куски(self.js) + r"""
        const о = сценаОписания(h('div', {}), 'potok');
        документ.appendChild(о.узел);
        о.добавить('файлы', 'Файлы', 'а');
        о.добавить('этапы', 'Этапы', 'б');
        о.добавить('свой', 'Свой раздел', 'в');
        о.добавить('разбор', 'Разбор', 'г');
        о.добавить('дерево', 'Дерево', 'д');
        о.добавить('этапы', 'Этапы заново', 'е');       // тот же ключ — на месте прежнего
        const до = состояние(о).разделы;
        о.убрать('свой');
        console.log(JSON.stringify({ до: до, после: состояние(о).разделы,
          абзацы: найти(о.узел, 'opis-body').children[0].children.slice(1).map((п) => п.tag + ':' + п.text) }));
        """
        итог = выполнить(код)
        self.assertEqual(итог["до"], ["Разбор", "Дерево", "Этапы заново", "Файлы", "Свой раздел"])
        self.assertEqual(итог["после"], ["Разбор", "Дерево", "Этапы заново", "Файлы"])
        self.assertEqual(итог["абзацы"], ["p:г"])

    def test_в_описание_или_на_месте(self):
        """Панель есть — пояснение уходит в неё (на месте ничего); нет или отцеплена — строкой на месте."""
        код = куски(self.js) + r"""
        const итог = {};
        итог.без_панели = вОписание('ldpc', 'Матрицы LDPC', 'Слой: ldpc ИМЯ.', 'Встроенные коды.');
        const о = сценаОписания(h('div', {}), 'stol');
        итог.отцеплена = вОписание('ldpc', 'Матрицы LDPC', 'Слой.');
        документ.appendChild(о.узел);
        итог.с_панелью = вОписание('ldpc', 'Матрицы LDPC', 'Слой: ldpc ИМЯ.', 'Встроенные коды.');
        итог.разделы = состояние(о).разделы;
        ОПИСАНИЕ.текущее = null;
        итог.после_перехода = вОписание('ldpc', 'Матрицы LDPC', 'Слой.');
        console.log(JSON.stringify({
          без_панели: [итог.без_панели.attrs.class, итог.без_панели.text],
          отцеплена: итог.отцеплена && итог.отцеплена.text, с_панелью: итог.с_панелью,
          разделы: итог.разделы, после_перехода: итог.после_перехода && итог.после_перехода.text }));
        """
        итог = выполнить(код)
        self.assertEqual(итог["без_панели"], ["muted small", "Слой: ldpc ИМЯ. Встроенные коды."])
        self.assertEqual(итог["отцеплена"], "Слой.")
        self.assertIsNone(итог["с_панелью"])
        self.assertEqual(итог["разделы"], ["Матрицы LDPC"])
        self.assertEqual(итог["после_перехода"], "Слой.")

    def test_без_хранилища_свёрнута_и_не_падает(self):
        код = куски(self.js) + r"""
        global.сломано = true;
        const о = сценаОписания(h('div', {}), 'potok');
        документ.appendChild(о.узел);
        const было = состояние(о);
        найти(о.узел, 'opis-handle').fire('click');
        console.log(JSON.stringify({ было: было.открыто, стало: состояние(о).открыто }));
        """
        self.assertEqual(выполнить(код), {"было": False, "стало": True})


class СтраницыИСтили(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "styles.css").read_text(encoding="utf-8")

    def test_страницы_строят_панель(self):
        for раздел in ("potok", "stol", "pakety"):
            with self.subTest(раздел=раздел):
                self.assertIn(f"const описание = сценаОписания(page, '{раздел}');", self.js)
        self.assertIn("await рисоватьЗадание(page, jobId, описание);", self.js)
        # Переход в другой раздел — прежняя панель больше не текущая.
        маршрут = вырезать(self.js, "renderRoute")
        self.assertLess(маршрут.index("ОПИСАНИЕ.текущее = null;"), маршрут.index("await рисоватьРаздел(route, сцена);"))

    def test_пояснения_ушли_из_дерева_и_страниц(self):
        """Каждое пояснение — только в разделе описания, не строкой рядом с деревом, этапами, пакетами."""
        for кусок in ("Узел — разбор или производный поток", "Байтовый поток с любого этапа",
                      "Сетевой захват по уровням", "Найдено автоматически: сигнатуры файлов",
                      "Свои цепочки шагов для потоков любого вида", "Нагрузка без известного разборщика",
                      "Позиции данных — всегда в потоке", "Выколотые позиции декодер восстанавливает",
                      "Щелчок по номеру байта — статистика столбца", "щелчок — выбрать, двойной — приблизить"):
            with self.subTest(кусок=кусок):
                места = [m.start() for m in re.finditer(re.escape(кусок), self.js)]
                self.assertEqual(1, len(места), кусок)
                перед = self.js[max(0, места[0] - 700):места[0]]
                self.assertRegex(перед, r"(вОписание\(|описание\.добавить\()[^;]*$")
        self.assertNotIn("' · правая кнопка — операции'", self.js)
        self.assertNotIn("'Пакеты и кадры из «Разбора потока» открываются кнопкой «Пакеты» у этапа.'", self.js)

    def test_пакет_дерево_слева_всё_прочее_справа(self):
        подробно = вырезать(self.js, "подробно")
        self.assertIn("const части = [h('div', { class: 'pk-panes' }, дерево, справа)];", подробно)
        справа = подробно[подробно.index("const справа = h('div', { class: 'pk-side' },"):подробно.index("const части")]
        for кусок in ("'pk-detail-head'", "'pk-errors'", "переключателиВида(", "выбранноеПоле,", "hex.узел);"):
            self.assertIn(кусок, справа)
        self.assertIn(".pk-side {", self.css)

    def test_стили_панели(self):
        for правило in (".opis-scene {", ".opis-handle {", ".opis-panel {", ".opis-panel[hidden] { display: none; }",
                        ".opis-handle[hidden] { display: none; }", "writing-mode: vertical-rl;",
                        "@media (max-width: 1100px) {\n    .opis-panel {\n        position: absolute;"):
            with self.subTest(правило=правило):
                self.assertIn(правило, self.css)
        # Цвета — только переменными темы: панель одинаково читается в светлой и тёмной.
        блок = self.css[self.css.index("Описание обработки: правая сворачиваемая панель"):]
        self.assertNotRegex(блок, r"#[0-9a-fA-F]{3,6}\b")


if __name__ == "__main__":
    unittest.main()
