# -*- coding: utf-8 -*-
"""Нижняя панель помощника: два ряда вместо четырёх.

Панель под разговором разрослась до четырёх рядов: настройки, отбор
источников, кнопка «Приложить файл» со своей полосой и само поле ввода. На
экране, где высота — это место под ответ, четыре ряда служебного управления
над одним полем читаются как приборная доска вместо разговора, и половина
из них стоит пустой: полоса вложений висела всегда, даже когда вложений не
было ни одного.

Осталось два ряда: тонкая строка настроек и строка ввода со скрепкой внутри.
Полоса вложений появляется, только когда есть что показать.

Сличать это подстроками в исходнике нельзя: перестановка узлов — как раз то,
что подстроки не видят. Поэтому кусок app.js запускается по-настоящему, через
node, с поддельными узлами разметки; проверяется собранное дерево — то же,
что увидит человек. Приём тот же, что в проверках бесед.

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

#: Поддельная разметка и всё, чем нижняя панель пользуется снаружи.
PRELUDE = r"""
const записано = { сообщено: [], загружено: [], убрано: [], послано: 0 };

function h(tag, attrs, ...kids) {
  const node = {
    tag: tag, attrs: attrs || {}, kids: [], parentNode: null,
    hidden: (attrs || {}).hidden === true,
    disabled: false,
    style: {},
    classList: {
      _own: node_классы(attrs),
      add(имя) { if (this._own.indexOf(имя) === -1) this._own.push(имя); },
      contains(имя) { return this._own.indexOf(имя) !== -1; },
    },
    appendChild(c) { if (c) { c.parentNode = this; this.kids.push(c); } },
    click() { if (this.attrs.onclick) return this.attrs.onclick({}); },
  };
  if (tag === 'textarea' || tag === 'input') node.value = '';
  (kids || []).flat(9).filter((k) => k !== null && k !== undefined)
    .forEach((k) => { if (k.tag) k.parentNode = node; node.kids.push(k); });
  return node;
}
function node_классы(attrs) {
  return String((attrs || {}).class || '').split(/\s+/).filter(Boolean);
}
function clear(n) { n.kids = []; }

/* Разговор и всё, что панель дёргает у соседей. */
const chat = { nodes: {}, attachments: [], current: null, creating: null };
const ATTACH_KIND_LABEL = { dump: 'дамп', text: 'текст' };
function fmtNumber(n) { return String(n); }
function iconGlyph(name) { return h('i', { class: 'glyph', name: name }); }
function domainSelect(opts) { return h('select', { class: 'domain', title: (opts || {}).title }); }
function buildModeSwitch() { return h('div', { class: 'seg seg--mode' }); }
function buildSourcesSwitch() { return h('div', { class: 'seg seg--sources' }); }
function growComposer() {}
function saveDraft() {}
function send() { записано.послано += 1; }
function abortAnswer() {}
function toast(text) { записано.сообщено.push(String(text)); }
function toastError(error) { записано.сообщено.push(String(error && error.message || error)); }
function upsertChatInList() {}
function renderTalkHead() {}
function rememberChat() {}
function replaceHash() {}
function FormData() { this.append = function () {}; }

const api = {
  async post() { return { chat: { id: 1, domain: '' } }; },
  async del(url) { записано.убрано.push(url); return {}; },
};
let счётчик = 0;
async function uploadFile(url, form) {
  счётчик += 1;
  записано.загружено.push(url);
  return { attachment: приложение(счётчик) };
}
function приложение(n) {
  return { id: n, name: 'дамп-' + n + '.txt', chars: 1200, kind: 'dump', note: '' };
}
"""

#: Разбор шагов: что человек делает и что он после этого видит.
EPILOGUE = r"""
function найти(node, годится) {
  if (!node || typeof node !== 'object' || !node.tag) return null;
  if (годится(node)) return node;
  for (const kid of (node.kids || [])) {
    const found = найти(kid, годится);
    if (found) return found;
  }
  return null;
}

function класс(node) { return String((node.attrs || {}).class || ''); }

/* Ряд — это прямой ребёнок панели. Пустой ряд рядом не считается: человек
   его не видит, а высоту он не занимает. */
function ряды(панель) {
  return (панель.kids || []).filter((n) => n && n.tag && !n.hidden);
}

/* Где лежит узел: цепочка классов от него вверх до панели. */
function путь(узел, панель) {
  const звенья = [];
  let текущий = узел;
  while (текущий && текущий !== панель) {
    звенья.unshift(класс(текущий) || текущий.tag);
    текущий = текущий.parentNode;
  }
  return звенья.join(' > ');
}

const steps = JSON.parse(process.argv[2]);
const out = [];
let панель = null;

for (const step of steps) {
  if (step.do === 'собрать') {
    панель = buildComposer();
  } else if (step.do === 'приложить') {
    for (let i = 0; i < (step.сколько || 1); i += 1) await uploadAttachment({ name: 'ф' });
  } else if (step.do === 'снять') {
    await dropAttachment(chat.attachments[step.номер || 0]);
  } else if (step.do === 'смотреть') {
    const поле = найти(панель, (n) => n.tag === 'textarea');
    const скрепка = chat.nodes.attachButton;
    const полоса = найти(панель, (n) => класс(n) === 'attach-bar');
    const список = chat.nodes.attachList;
    out.push({
      рядов: ряды(панель).length,
      ряды: ряды(панель).map(класс),
      все_ряды: (панель.kids || []).map(класс),
      скрепка_где: скрепка ? путь(скрепка, панель) : null,
      поле_где: поле ? путь(поле, панель) : null,
      скрепка_подпись: скрепка ? (скрепка.attrs['aria-label'] || '') : null,
      скрепка_объяснение: скрепка ? String(скрепка.attrs.title || '') : null,
      скрепка_словами: скрепка ? словами(скрепка) : null,
      полоса_скрыта: полоса ? полоса.hidden : null,
      вложений: список ? список.kids.length : 0,
      настройки: ряды(панель).length
        ? (ряды(панель)[0].kids || []).map((n) => класс(n) || n.tag)
        : [],
    });
  } else {
    throw new Error('неизвестный шаг ' + step.do);
  }
}

function словами(node) {
  if (node === null || node === undefined) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return (node.kids || []).map(словами).join('');
}

process.stdout.write(JSON.stringify(out));
"""


def блок_панели() -> str:
    """Кусок app.js про нижнюю панель — от полосы вложений до сборки панели."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("    function buildAttachBar()")
    end = source.index("/** Плашка с номером письма", start)
    return source[start:end]


@unittest.skipUnless(NODE, "нет node — нижнюю панель не проверить")
class ПанельTestCase(unittest.TestCase):
    """Общая часть: гоняем настоящий код панели из app.js."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls._tmp.name) / "panel.mjs"
        cls.script.write_text(PRELUDE + блок_панели() + EPILOGUE, encoding="utf-8")

    def прогнать(self, шаги):
        done = subprocess.run(
            [NODE, str(self.script), json.dumps(шаги, ensure_ascii=False)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)


class ПанельВДваРяда(ПанельTestCase):
    """Сама перестройка: рядов над полем ввода должно быть два."""

    def test_рядов_два(self):
        видно = self.прогнать([{"do": "собрать"}, {"do": "смотреть"}])[0]
        self.assertEqual(2, видно["рядов"],
                         f"рядов в панели {видно['рядов']}: {видно['ряды']}")
        self.assertEqual(["composer-top", "composer-row"], видно["ряды"])

    def test_настройки_собраны_в_один_ряд(self):
        """Направление, источники, режим и плашка письма — одной строкой.

        Раньше отбор источников занимал отдельный ряд. Разъехавшись по
        рядам, четыре одинаковых по смыслу переключателя перестают читаться
        как одна группа настроек.
        """
        видно = self.прогнать([{"do": "собрать"}, {"do": "смотреть"}])[0]
        self.assertEqual(["domain", "seg seg--sources", "seg seg--mode", "case-plate"],
                         видно["настройки"])

    def test_скрепка_стоит_в_строке_ввода(self):
        """Кнопка вложения — внутри строки с полем, а не своим рядом."""
        видно = self.прогнать([{"do": "собрать"}, {"do": "смотреть"}])[0]
        self.assertEqual("composer-row > btn btn--icon composer-clip",
                         видно["скрепка_где"])
        self.assertEqual("composer-row > composer-input", видно["поле_где"])

    def test_у_скрепки_вместо_подписи_значок(self):
        """Подпись «Приложить файл» занимала строку ради понятного значка.

        Убрать подпись можно только вместе с заменой: читающему с экрана и
        наводящему мышь смысл кнопки обязан остаться.
        """
        видно = self.прогнать([{"do": "собрать"}, {"do": "смотреть"}])[0]
        self.assertEqual("", видно["скрепка_словами"].strip(),
                         "подпись у скрепки осталась — ряд снова занят словами")
        self.assertEqual("Приложить файл", видно["скрепка_подпись"])
        self.assertIn("дамп", видно["скрепка_объяснение"])


class ПолосаВложенийПоНадобности(ПанельTestCase):
    """Пустая полоса под полем съедала высоту, на которой мог стоять ответ."""

    def test_без_вложений_полоса_скрыта(self):
        видно = self.прогнать([{"do": "собрать"}, {"do": "смотреть"}])[0]
        self.assertTrue(видно["полоса_скрыта"], "пустая полоса вложений занимает ряд")
        # Ряд в разметке есть — но человеку не показан.
        self.assertIn("attach-bar", видно["все_ряды"])

    def test_с_вложением_полоса_появляется(self):
        видно = self.прогнать([
            {"do": "собрать"}, {"do": "приложить"}, {"do": "смотреть"}])[0]
        self.assertFalse(видно["полоса_скрыта"], "вложение приложено, а полосы не видно")
        self.assertEqual(3, видно["рядов"])
        self.assertEqual(1, видно["вложений"])

    def test_снял_последнее_полоса_ушла(self):
        """Обратный ход обязателен: иначе полоса остаётся висеть пустой."""
        видно = self.прогнать([
            {"do": "собрать"}, {"do": "приложить"}, {"do": "снять"}, {"do": "смотреть"}])[0]
        self.assertTrue(видно["полоса_скрыта"], "полоса осталась после снятия вложения")
        self.assertEqual(2, видно["рядов"])
        self.assertEqual(0, видно["вложений"])

    def test_снял_одно_из_двух_полоса_осталась(self):
        видно = self.прогнать([
            {"do": "собрать"}, {"do": "приложить", "сколько": 2},
            {"do": "снять", "номер": 0}, {"do": "смотреть"}])[0]
        self.assertFalse(видно["полоса_скрыта"], "полоса ушла, а вложение ещё есть")
        self.assertEqual(1, видно["вложений"])


if __name__ == "__main__":
    unittest.main()
