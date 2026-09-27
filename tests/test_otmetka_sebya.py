"""«Отметить себя» ставило наряд не тому.

Список личного состава (/api/staff) намеренно не содержит создателя системы:
он не дежурит, писем не ведёт, и в счёте «сколько людей в строю» только сбивал
бы итог. А окно отметки подставляло вошедшего в выпадающий список по его
номеру — и, не найдя своего человека, вставало на первого по списку. Создатель,
нажавший «Отметить себя», ставил наряд Орловой М. В. Молча: ни ошибки, ни
предупреждения, и сама Орлова об этом не узнала бы.

Подстроками такое не ловится: в исходнике всё написано правильно —
`selected: person.id === owner`. Беда в том, что owner в списке отсутствует.
Поэтому окно собирается по-настоящему, через node, и проверяется то, что
увидит человек: кто подставлен в поле «Военнослужащий».

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

PRELUDE = r"""
const записано = { сообщено: [], окна: [], послано: [] };

function h(tag, attrs, ...kids) {
  const node = {
    tag: tag, attrs: attrs || {}, kids: [],
    hidden: (attrs || {}).hidden === true,
    disabled: false,
    value: '',
    appendChild(c) { if (c) this.kids.push(c); },
    addEventListener(имя, обработчик) { (this.слушатели[имя] = this.слушатели[имя] || []).push(обработчик); },
    слушатели: {},
  };
  (kids || []).flat(9).filter((k) => k !== null && k !== undefined)
    .forEach((k) => node.kids.push(k));
  if (tag === 'select') {
    // Настоящий select показывает первую опцию, пока ни одна не помечена
    // выбранной. Именно на этом дефект и держался.
    Object.defineProperty(node, 'выбранная', {
      get() {
        const опции = this.kids.filter((k) => k && k.tag === 'option');
        const помеченная = опции.find((o) => o.attrs.selected === true);
        return помеченная || опции[0] || null;
      },
    });
  }
  return node;
}
function clear(n) { n.kids = []; }

const state = { user: null, config: {} };
const rosterState = { day: '2026-09-09', from: '2026-09-07', span: 7 };
const casesState = { staff: [] };

const ROSTER_KIND = {
  duty: { title: 'Дежурство', cls: 'duty' },
  leave: { title: 'Отпуск', cls: 'leave' },
};

let ШТАТ = [];
const api = {
  async get(url) {
    if (url === '/api/staff') return { items: JSON.parse(JSON.stringify(ШТАТ)) };
    throw new Error('нет ' + url);
  },
  async post(url, body) { записано.послано.push({ url, body }); return {}; },
  async patch(url, body) { записано.послано.push({ url, body }); return {}; },
  async del(url) { записано.послано.push({ url }); return {}; },
};

async function staffList() {
  if (casesState.staff.length) return casesState.staff;
  try {
    const data = await api.get('/api/staff');
    casesState.staff = data.items || [];
  } catch (error) {
    casesState.staff = [];
  }
  return casesState.staff;
}

function isAdmin() { return ['owner', 'head', 'deputy', 'lead'].indexOf((state.user || {}).role) !== -1; }
function toast(text, kind) { записано.сообщено.push({ текст: String(text), вид: kind || '' }); }
function toastError(error) { записано.сообщено.push({ текст: String(error && error.message || error), вид: 'error' }); }
function todayIso() { return '2026-09-09'; }
function openModal(opts) {
  записано.окна.push(opts.title);
  return { close() {}, opts: opts };
}
function confirmDialog() { return Promise.resolve(true); }
"""

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

function словами(node) {
  if (node === null || node === undefined) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return (node.kids || []).map(словами).join('');
}

const шаги = JSON.parse(process.argv[2]);
const out = [];

for (const шаг of шаги) {
  if (шаг.do === 'штат') {
    ШТАТ = шаг.люди;
    casesState.staff = [];
  } else if (шаг.do === 'вошёл') {
    state.user = шаг.кто;
  } else if (шаг.do === 'отметить') {
    записано.окна = [];
    записано.сообщено = [];
    await openRosterDialog({ user_id: state.user.id, date_from: rosterState.day },
                           () => {});
    const окно = записано.окна.length;
    let кто = null;
    if (окно) {
      // Собираем то же тело, что показали человеку.
      const тело = ПОСЛЕДНЕЕ_ОКНО.body;
      const список = найти(h('div', {}, тело), (n) => n.tag === 'select');
      кто = список && список.выбранная ? словами(список.выбранная) : null;
    }
    out.push({
      окно_открылось: Boolean(окно),
      подставлен: кто,
      сообщено: записано.сообщено.slice(),
    });
  } else {
    throw new Error('неизвестный шаг ' + шаг.do);
  }
}

process.stdout.write(JSON.stringify(out));
"""


def блок_окна() -> str:
    """Кусок app.js про окно отметки в расходе."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("    async function openRosterDialog(")
    end = source.index("\n    async function ", start + 40)
    кусок = source[start:end]
    # Запоминаем показанное окно: в заглушке openModal тела не видно.
    кусок = кусок.replace(
        "        const dialog = openModal({",
        "        const dialog = openModal(ПОСЛЕДНЕЕ_ОКНО = {", 1)
    return "let ПОСЛЕДНЕЕ_ОКНО = null;\n" + кусок


#: Личный состав отдела — ровно как его отдаёт /api/staff: без создателя.
ШТАТ = [
    {"id": 4, "login": "orlova", "full_name": "Орлова М. В.", "role": "engineer"},
    {"id": 2, "login": "petrov", "full_name": "Петров П. С.", "role": "engineer"},
    {"id": 3, "login": "sidorov", "full_name": "Сидоров А. Н.", "role": "lead"},
]

СОЗДАТЕЛЬ = {"id": 1, "login": "ivanov", "full_name": "Иванов И. В.", "role": "owner"}
ИНЖЕНЕР = {"id": 2, "login": "petrov", "full_name": "Петров П. С.", "role": "engineer"}
НАЧАЛЬНИК = {"id": 3, "login": "sidorov", "full_name": "Сидоров А. Н.", "role": "lead"}


@unittest.skipUnless(NODE, "нет node — окно отметки не проверить")
class ОтметкаTestCase(unittest.TestCase):
    """Общая часть: гоняем настоящий код окна из app.js."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls._tmp.name) / "otmetka.mjs"
        cls.script.write_text(PRELUDE + блок_окна() + EPILOGUE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def прогнать(self, шаги):
        полные = [{"do": "штат", "люди": ШТАТ}] + шаги
        done = subprocess.run(
            [NODE, str(self.script), json.dumps(полные, ensure_ascii=False)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)


class ОтметкаДостаётсяТому1КтоЕёСтавит(ОтметкаTestCase):
    """Кого подставили в поле «Военнослужащий»."""

    def test_инженер_видит_себя(self):
        видно = self.прогнать([
            {"do": "вошёл", "кто": ИНЖЕНЕР},
            {"do": "отметить"},
        ])[0]
        self.assertTrue(видно["окно_открылось"])
        self.assertIn("Петров П. С.", видно["подставлен"] or "")
        self.assertIn("(я)", видно["подставлен"] or "",
                      "своего человека в списке ничем не отметили")

    def test_начальник_видит_себя_а_не_первого_по_списку(self):
        """Начальник ведёт чужой расход — тем легче промахнуться мимо себя."""
        видно = self.прогнать([
            {"do": "вошёл", "кто": НАЧАЛЬНИК},
            {"do": "отметить"},
        ])[0]
        self.assertIn("Сидоров А. Н.", видно["подставлен"] or "")
        self.assertNotIn("Орлова", видно["подставлен"] or "",
                         "подставился первый по списку вместо вошедшего")

    def test_того1кого_нет_в_расходе_молча_не_подменяют(self):
        """Сам дефект: создателя в расходе нет, и наряд уходил Орловой."""
        видно = self.прогнать([
            {"do": "вошёл", "кто": СОЗДАТЕЛЬ},
            {"do": "отметить"},
        ])[0]
        self.assertFalse(видно["окно_открылось"],
                         "окно открылось на человека, которого в расходе нет")
        self.assertEqual(1, len(видно["сообщено"]), "человеку ничего не сказали")
        сказано = видно["сообщено"][0]
        self.assertEqual("error", сказано["вид"])
        self.assertIn("создатель системы", сказано["текст"].lower())
        # И ни одной записи никуда не ушло.
        self.assertNotIn("Орлова", json.dumps(видно, ensure_ascii=False))


class КнопкаНеПоказываетсяТому1КтоНеВРасходе(unittest.TestCase):
    """Отказ вслух лучше молчаливой подмены, но кнопки лучше не показывать.

    Человек, которому нечего отмечать, не должен видеть кнопку, чтобы потом
    получить отказ. Проверяем, что кнопка заведена скрытой и открывается
    только после сверки со списком личного состава.
    """

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_кнопка_заведена_скрытой(self):
        кусок = self.js.split("const markSelf = h('button'")[1].split("}, 'Отметить себя')")[0]
        self.assertIn("hidden: true", кусок,
                      "кнопка показывается до сверки со списком личного состава")

    def test_кнопка_открывается_по_списку_личного_состава(self):
        кусок = self.js.split("const markSelf = h('button'")[1][:600]
        self.assertIn("staffList().then", кусок)
        self.assertIn("markSelf.hidden = !staff.some", кусок)
        self.assertIn("person.id === (state.user || {}).id", кусок)


if __name__ == "__main__":
    unittest.main()
