"""Разметка ответа помощника: формулы, таблицы, списки.

Ответ рисует свой разбор Markdown — внешних библиотек в изолированном контуре
нет. Проверять его подстроками в исходнике бессмысленно: беда была не в том,
что кода нет, а в том, что он делает не то. Поэтому здесь разбор запускается
по-настоящему, через node, и сверяется то, что увидит человек.

Каждый случай — из настоящего ответа по библиотеке отдела:

* «S = P / (4 * pi * R^2)» показывалось как «S = P / (4  pi  R^2)»: звёздочки
  умножения съедал курсив, и операция из формулы пропадала;
* «P_вх = P_изл · G / (4πR^2)» показывалось значками — так эту запись отдаёт
  разбор литературы, а отрисовщик про «^» и «_» не знал ничего;
* таблица без крайних палок рассыпалась в текст, и инженер видел строку
  «--- | --- | ---»;
* порядок работ «1., 2., 3.» превращался в «1., 1., 1.», если между пунктами
  стояло пояснение.

Если node в системе нет, проверки пропускаются: это среда разработки, а не
условие работы отдела.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "reportgen" / "web" / "static" / "app.js"
NODE = shutil.which("node")

#: Заглушки для того немногого, чем разбор пользуется снаружи.
PRELUDE = """
function escapeHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function h(tag, attrs, ...kids){
  const node = {tag: tag, attrs: attrs || {}, kids: []};
  node.appendChild = function(c){ this.kids.push(c); };
  (kids || []).flat(9).filter(Boolean).forEach(function(k){ node.kids.push(k); });
  return node;
}
function clear(n){ n.kids = []; }
function append(n, kids){ (kids || []).flat(9).filter(Boolean)
  .forEach(function(k){ n.kids.push(k); }); }
"""

EPILOGUE = r"""
/* Собираем со всего дерева то, что проверяем: таблицы и списки. Таблица
   лежит внутри обёртки с прокруткой, поэтому обходим целиком, а не только
   верхний уровень. */
function collect(node, out) {
  if (!node || typeof node !== 'object' || !node.tag) return out;
  if (node.tag === 'table') {
    const rows = [];
    const walk = (n) => {
      if (!n || !n.tag) return;
      if (n.tag === 'tr') rows.push(n.kids.map((c) => (c.attrs && c.attrs.html) || ''));
      (n.kids || []).forEach(walk);
    };
    walk(node);
    out.push({tag: 'table', rows: rows});
    return out;
  }
  if (node.tag === 'ol' || node.tag === 'ul') {
    out.push({
      tag: node.tag,
      start: (node.attrs || {}).start || '',
      items: (node.kids || []).map((c) => (c.attrs && c.attrs.html) || ''),
    });
    return out;
  }
  out.push({tag: node.tag, html: (node.attrs || {}).html || ''});
  (node.kids || []).forEach((c) => collect(c, out));
  return out;
}
const input = JSON.parse(process.argv[2]);
const root = renderMarkdown(input.text);
const blocks = [];
(root.kids || []).forEach((c) => collect(c, blocks));
process.stdout.write(JSON.stringify({inline: mdInline(input.text), blocks: blocks}));
"""


def markup_block() -> str:
    """Кусок app.js с разбором разметки — от таблицы образцов до отрисовки ответа."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("const MD_BULLET")
    end = source.index("function renderAnswer", start)
    return source[start:end]


@unittest.skipUnless(NODE, "нет node — разбор разметки не проверить")
class MarkupTestCase(unittest.TestCase):
    """Общая часть: гоняем настоящий разбор из app.js."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls._tmp.name) / "razmetka.mjs"
        cls.script.write_text(PRELUDE + markup_block() + EPILOGUE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def render(self, text: str) -> dict:
        done = subprocess.run(
            [NODE, str(self.script), json.dumps({"text": text})],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)

    def inline(self, text: str) -> str:
        return self.render(text)["inline"]

    def tables(self, text: str):
        return [b for b in self.render(text)["blocks"]
                if isinstance(b, dict) and b.get("tag") == "table"]


class FormulaTests(MarkupTestCase):
    """Формула теряла операцию, а степень и индекс показывались значками."""

    def test_the_multiplication_signs_survive(self):
        """«S = P / (4  pi  R^2)» — по такой записи не сказать, что там было."""
        out = self.inline("S = P / (4 * pi * R^2)")
        self.assertIn("4 * pi * R", out)
        self.assertNotIn("<i>", out)

    def test_three_stars_in_a_row_survive(self):
        out = self.inline("Q = 2 * pi * f_0 * L / R")
        self.assertEqual(3, out.count(" * "), out)

    def test_emphasis_still_works(self):
        out = self.inline("это *важно* и **очень важно**")
        self.assertIn("<i>важно</i>", out)
        self.assertIn("<b>очень важно</b>", out)

    def test_a_power_and_an_index_are_shown_as_such(self):
        out = self.inline("P_вх = P_изл · G / (4πR^2), Вт.")
        self.assertIn("P<sub>вх</sub>", out)
        self.assertIn("P<sub>изл</sub>", out)
        self.assertIn("R<sup>2</sup>", out)

    def test_a_negative_power_and_a_latin_index(self):
        out = self.inline("BER = 10^-6 при E_b/N_0 = 10,5 дБ")
        self.assertIn("10<sup>-6</sup>", out)
        self.assertIn("E<sub>b</sub>", out)
        self.assertIn("N<sub>0</sub>", out)

    def test_a_formula_of_water(self):
        self.assertIn("H<sub>2</sub>O", self.inline("H_2O"))

    def test_a_long_russian_index(self):
        self.assertIn("T<sub>приёмника</sub>", self.inline("T_приёмника = 300 К"))


class NotAFormulaTests(MarkupTestCase):
    """Подчёркивание чаще разделяет слова, чем обозначает индекс."""

    def test_register_names_from_datasheets_are_left_alone(self):
        """«RX_LOS» — имя регистра, инженер ищет его в логе именно так."""
        out = self.inline("Регистр RX_LOS и поле Transfer_Encoding")
        self.assertIn("RX_LOS", out)
        self.assertIn("Transfer_Encoding", out)
        self.assertNotIn("<sub>", out)

    def test_the_departments_own_notation_is_left_alone(self):
        out = self.inline("ГОСТ_Р, КАМ_16, ФМ_4")
        self.assertNotIn("<sub>", out)

    def test_inline_code_stays_literal(self):
        """В коде ничего не разбирается — ни курсив, ни степень."""
        out = self.inline("Регистр `P_вх` и `10^2` внутри кода")
        self.assertIn("<code>P_вх</code>", out)
        self.assertIn("<code>10^2</code>", out)
        self.assertNotIn("<sub>", out)
        self.assertNotIn("<sup>", out)

    def test_an_explanation_of_indices_is_not_italic(self):
        out = self.inline("Индексы: _вх — вход, _вых — выход.")
        self.assertIn("_вх", out)
        self.assertIn("_вых", out)
        self.assertNotIn("<i>", out)


class TableTests(MarkupTestCase):
    """Таблица рассыпалась в текст с сырыми палками тремя способами."""

    def test_a_table_without_edge_pipes_is_still_a_table(self):
        """Самая частая запись у локальной модели."""
        found = self.tables("Параметр | Значение | Источник\n"
                            "--- | --- | ---\n"
                            "Полоса | 36 МГц | [S1]")
        self.assertEqual(1, len(found), found)
        self.assertIn("Параметр", found[0]["rows"][0][0])

    def test_two_columns_without_edge_pipes(self):
        found = self.tables("Параметр | Значение\n--- | ---\nПолоса | 36 МГц")
        self.assertEqual(1, len(found), found)

    def test_a_body_row_without_a_closing_pipe_stays_in_the_table(self):
        """Иначе хвост таблицы вываливался сырыми палками в текст ответа."""
        found = self.tables("| Параметр | Значение |\n|---|---|\n"
                            "| Полоса | 36 МГц\n| G/T | 12,5 дБ/К")
        self.assertEqual(1, len(found), found)
        self.assertEqual(3, len(found[0]["rows"]), found[0]["rows"])

    def test_a_proper_table_still_works(self):
        found = self.tables("| Параметр | Значение |\n|---|---|\n| Полоса | 36 МГц |")
        self.assertEqual(1, len(found), found)


class NotATableTests(MarkupTestCase):
    """Палка в строке — ещё не таблица: в паспортах и RFC их полно."""

    def test_a_bit_mask_in_a_list_stays_a_list(self):
        self.assertEqual([], self.tables("- Флаги заголовка: URG | ACK | PSH"))

    def test_a_bit_mask_in_a_sentence_stays_a_sentence(self):
        self.assertEqual([], self.tables(
            "Регистр собирается как O_RDONLY | O_CREAT | O_TRUNC и пишется в поле."))

    def test_a_bit_mask_in_a_numbered_step_stays_a_step(self):
        self.assertEqual([], self.tables("1. Проверить SYN | ACK | FIN в дампе."))

    def test_a_quote_ending_with_a_pipe_stays_a_quote(self):
        self.assertEqual([], self.tables("> Из паспорта: поле FCS |"))

    def test_a_mask_in_code_stays_in_code(self):
        self.assertEqual([], self.tables("Маска `SYN|ACK|FIN` разбирается так же."))


class CitationTests(MarkupTestCase):
    """Ссылка на источник — то, чем ответ связан с библиотекой отдела."""

    def buttons(self, text):
        import re as _re

        out = self.inline(text)
        return _re.findall(r'data-label="([^"]+)"', out)

    def test_a_plain_label_is_a_button(self):
        self.assertEqual(["S1"], self.buttons("Порог 12 дБ [S1]."))

    def test_two_labels_in_one_bracket_are_two_buttons(self):
        """Модель пишет «[S1, S2]» — по-русски это самая естественная запись.

        Такая метка кнопкой не становилась: человек не мог открыть источник,
        а сервер считал, что ответ не сослался ни на что.
        """
        self.assertEqual(["S1", "S2"], self.buttons("Порог 12 дБ [S1, S2]."))

    def test_the_other_separators_too(self):
        for text in ("[S1; S2]", "[S1 и S2]", "[ S1 , S2 ]"):
            self.assertEqual(["S1", "S2"], self.buttons(text), text)

    def test_a_range_is_opened_up(self):
        self.assertEqual(["S1", "S2", "S3"], self.buttons("[S1—S3]"))
        self.assertEqual(["S1", "S2", "S3"], self.buttons("[S1-S3]"))

    def test_a_russian_letter_instead_of_the_latin_one(self):
        """По раскладке «С» вместо «S» — обычная опечатка модели."""
        self.assertEqual(["S1", "S2"], self.buttons("[С1, С2]"))

    def test_a_leading_zero_is_not_a_dead_button(self):
        self.assertEqual(["S1"], self.buttons("[S01]"))

    def test_the_buttons_are_separated_in_the_text(self):
        """«[S1][S2]» подряд читается как одна метка с опечаткой."""
        self.assertIn("</button>, <button", self.inline("[S1, S2]"))

    def test_a_bracket_that_is_not_a_citation_is_left_alone(self):
        self.assertEqual([], self.buttons("Смотри [ГОСТ 1-3] и [примечание]."))


class OrderedListTests(MarkupTestCase):
    """Порядок шагов методики терялся: «1., 1., 1.» вместо «1., 2., 3.»."""

    STEPS = ("1. Измерить уровень на входе.\n\n"
             "Прибор ставится в разрыв тракта.\n\n"
             "2. Снять спектр в полосе 36 МГц.\n\n"
             "3. Сравнить с нормой [S1].")

    def lists(self, text):
        return [b for b in self.render(text)["blocks"]
                if isinstance(b, dict) and b.get("tag") == "ol"]

    def test_the_numbering_carries_on_after_an_explanation(self):
        starts = [item.get("start") for item in self.lists(self.STEPS)]
        self.assertEqual(["", "2", "3"], starts, starts)

    def test_a_list_that_was_not_broken_has_no_start(self):
        starts = [item.get("start") for item in self.lists(
            "1. Первое\n2. Второе\n3. Третье")]
        self.assertEqual([""], starts)

    def test_the_item_text_is_kept(self):
        items = self.lists("1. Измерить уровень на входе.")
        self.assertIn("Измерить уровень", items[0]["items"][0])


if __name__ == "__main__":
    unittest.main()
