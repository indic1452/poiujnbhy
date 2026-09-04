# -*- coding: utf-8 -*-
"""Беседы: недописанное сообщение переживает опрос.

Жалоба начальника отдела: «в личных сообщениях если пишу сообщение дольше
10-20 секунд у меня все сообщение удаляется, поле очищается».

Причина ровно такая, как в жалобе указано время. Правая половина экрана
перерисовывалась целиком — вместе с полем ввода, — а опрос беседы идёт раз в
десять секунд (TALK_POLL_MS). Человек писал абзац, опрос выбрасывал поле с
набранным и ставил новое, пустое. Никакой ошибки при этом не показывалось:
текст просто исчезал, и восстановить его было неоткуда.

Проверять это подстроками в исходнике бессмысленно — беда была не в том, что
кода нет, а в том, что он делает не то. Поэтому сам кусок app.js запускается
здесь по-настоящему, через node: заглушки дают ему поддельные узлы разметки и
поддельный сервер, а опрос дёргается вручную — тем же обработчиком, который
заводит setInterval. Что увидит человек, то и сверяется.

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

#: Поддельная разметка и всё, чем беседы пользуются снаружи своего куска.
PRELUDE = r"""
const записано = { сообщено: [], послано: [], убрано: [], опрос: null, шаг: 0 };

function h(tag, attrs, ...kids) {
  const node = {
    tag: tag, attrs: attrs || {}, kids: [],
    scrollTop: 0, scrollHeight: 0, clientHeight: 0,
    appendChild(c) { this.kids.push(c); },
    click() { if (this.attrs.onclick) return this.attrs.onclick({}); },
  };
  if (tag === 'textarea' || tag === 'input') {
    node.value = '';
    node.selectionStart = 0;
    node.selectionEnd = 0;
    node.focus = function () { document.activeElement = this; };
    node.setSelectionRange = function (a, b) {
      this.selectionStart = a;
      this.selectionEnd = b;
    };
  } else {
    node.focus = function () { document.activeElement = this; };
  }
  (kids || []).flat(9).filter((k) => k !== null && k !== undefined)
    .forEach((k) => node.kids.push(k));
  return node;
}
function clear(n) { n.kids = []; }
function append(n, kids) {
  (kids || []).flat(9).filter((k) => k !== null && k !== undefined)
    .forEach((k) => n.kids.push(k));
}

const document = { activeElement: null };
const location = { hash: '' };

/* Поддельный сервер: список бесед и их содержимое. */
const сервер = { items: [], rooms: {} };
const api = {
  async get(url) {
    if (url === '/api/talks') return { items: JSON.parse(JSON.stringify(сервер.items)) };
    const id = Number(url.split('/')[3]);
    if (!сервер.rooms[id]) throw new Error('нет беседы ' + id);
    return JSON.parse(JSON.stringify(сервер.rooms[id]));
  },
  async post(url, body) {
    const id = Number(url.split('/')[3]);
    записано.послано.push({ talk: id, text: body.text });
    const room = сервер.rooms[id];
    записано.шаг += 1;
    room.messages.push({
      id: 900 + записано.шаг, user_id: 1, author: 'Я',
      text: body.text, created_at: '2026-09-04T10:0' + записано.шаг + ':00',
    });
    return {};
  },
  async del(url) {
    записано.убрано.push(url);
    const id = Number(url.split('/')[3]);
    сервер.items = сервер.items.filter((item) => item.id !== id);
    delete сервер.rooms[id];
    return {};
  },
};

/* Опрос не заводим по-настоящему: держим обработчик и дёргаем его руками —
   так проверка не зависит от часов и не ждёт десять секунд впустую. */
function setInterval(fn, ms) { записано.опрос = fn; записано.каждые = ms; return 7; }
function clearInterval() { записано.опрос = null; }

const state = { user: { id: 1 }, route: { name: 'talks', id: null } };
function setNavCount() {}
function toast(text) { записано.сообщено.push(String(text)); }
function toastError(error) { записано.сообщено.push(String(error && error.message || error)); }
function personLink(id, name) { return h('a', { html: String(name) }); }
function initials(name) { return String(name || '').slice(0, 2); }
function clockOf(when) { return String(when || '').slice(11, 16); }
function fmtDateTime(when) { return String(when || ''); }
function fmtWhen(when) { return String(when || ''); }
function iconGlyph(name) { return h('i', { html: name }); }
function messageCaption(message) { return (message.text || '').trim(); }
function talkFileRow(item) { return h('div', { html: item.name || '' }); }
function openNewTalk() {}
async function uploadFile() { return {}; }
async function confirmDialog() { return true; }
"""

#: Разбор шагов: что человек делает и что он после этого видит.
EPILOGUE = r"""
/* Дать микрозадачам доработать: опрос заводится обработчиком, который ничего
   не возвращает, а внутри у него цепочка await. */
async function отстояться() {
  for (let i = 0; i < 60; i += 1) await Promise.resolve();
}

/* Поле ищем в самой разметке, а не в служебных ссылках: человек видит на
   экране textarea, и проверка должна смотреть туда же. */
function поле() { return найти(talks.nodes.room, (n) => n.tag === 'textarea'); }

function найти(node, годится) {
  if (!node || typeof node !== 'object') return null;
  if (node.tag && годится(node)) return node;
  for (const kid of (node.kids || [])) {
    const found = найти(kid, годится);
    if (found) return found;
  }
  return null;
}

function текстом(node) {
  if (node === null || node === undefined) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  const свой = (node.attrs || {}).html || '';
  return свой + (node.kids || []).map(текстом).join('');
}

function кнопка(подпись) {
  return найти(talks.nodes.room, (n) => n.tag === 'button' && текстом(n).indexOf(подпись) !== -1);
}

function реплики() {
  const flow = найти(talks.nodes.room,
    (n) => (n.attrs || {}).class === 'talk-stream');
  if (!flow) return [];
  return (flow.kids || []).map((line) => текстом(line).trim());
}

/* Опознать узел, не меняя его: одинаковые числа — тот же самый объект. */
const талоны = new WeakMap();
let счёт = 0;
function талон(node) {
  if (!талоны.has(node)) { счёт += 1; талоны.set(node, счёт); }
  return талоны.get(node);
}

const view = h('div', {});
const steps = JSON.parse(process.argv[2]);
const out = [];

for (const step of steps) {
  if (step.do === 'сервер') {
    сервер.items = step.items;
    сервер.rooms = step.rooms;
  } else if (step.do === 'открыть') {
    state.route = { name: 'talks', id: step.talk };
    await renderTalks(view, step.talk);
  } else if (step.do === 'печатать') {
    const f = поле();
    document.activeElement = f;
    f.value = step.text;
    f.selectionStart = f.selectionEnd = step.text.length;
    if (f.attrs.oninput) f.attrs.oninput();
  } else if (step.do === 'курсор') {
    const f = поле();
    f.selectionStart = step.at;
    f.selectionEnd = step.to === undefined ? step.at : step.to;
  } else if (step.do === 'отвлечься') {
    document.activeElement = null;
  } else if (step.do === 'опрос') {
    записано.опрос();
    await отстояться();
  } else if (step.do === 'пришло') {
    const room = сервер.rooms[step.talk];
    room.messages.push({
      id: step.id, user_id: 2, author: 'Жуков А. С.',
      text: step.text, created_at: '2026-09-04T09:30:00',
    });
  } else if (step.do === 'отправить') {
    кнопка('Отправить').click();
    await отстояться();
  } else if (step.do === 'убрать') {
    await dropTalk(сервер.rooms[step.talk]);
    await отстояться();
  } else if (step.do === 'пусто') {
    paintTalkRoom(null);
  } else if (step.do === 'смотреть') {
    const f = поле();
    out.push({
      поле: f ? f.value : null,
      черновики: JSON.parse(JSON.stringify(talks.drafts || {})),
      вфокусе: Boolean(f) && document.activeElement === f,
      курсор: f ? [f.selectionStart, f.selectionEnd] : null,
      реплики: реплики(),
      беседа: talks.current,
      узел: f && f.__метка !== undefined ? f.__метка : null,
      комната: talks.nodes.room ? talks.nodes.room.kids.length : 0,
      пусто_узел: talks.nodes.room && talks.nodes.room.kids[0]
        ? талон(talks.nodes.room.kids[0]) : null,
      каждые: записано.каждые,
      послано: записано.послано.slice(),
      убрано: записано.убрано.slice(),
    });
  } else if (step.do === 'метка') {
    const f = поле();
    if (f) f.__метка = step.name;
  } else {
    throw new Error('неизвестный шаг ' + step.do);
  }
}

process.stdout.write(JSON.stringify(out));
"""


def talks_block() -> str:
    """Кусок app.js про беседы — от частоты опроса до удаления беседы."""
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("const TALK_POLL_MS")
    end = source.index("/** Подпись к сообщению", start)
    return source[start:end]


#: Две беседы: с Жуковым и с Титовым. Числа и фамилии — как в отделе.
БЕСЕДЫ = [
    {"id": 1, "title": "", "members": [{"id": 1, "full_name": "Никитин В. П."},
                                       {"id": 2, "full_name": "Жуков А. С."}],
     "unread": 0, "last_text": "Добрый день", "updated_at": "2026-09-04T09:00:00"},
    {"id": 2, "title": "", "members": [{"id": 1, "full_name": "Никитин В. П."},
                                       {"id": 3, "full_name": "Титов И. Н."}],
     "unread": 0, "last_text": "Принял", "updated_at": "2026-09-04T08:00:00"},
]

КОМНАТЫ = {
    "1": {"id": 1, "members": БЕСЕДЫ[0]["members"], "messages": [
        {"id": 11, "user_id": 2, "author": "Жуков А. С.", "text": "Добрый день",
         "created_at": "2026-09-04T09:00:00"}]},
    "2": {"id": 2, "members": БЕСЕДЫ[1]["members"], "messages": [
        {"id": 21, "user_id": 3, "author": "Титов И. Н.", "text": "Принял",
         "created_at": "2026-09-04T08:00:00"}]},
}

#: Настоящий абзац из переписки отдела — такой пишут дольше десяти секунд.
АБЗАЦ = ("Александр Сергеевич, по письму 47/312: несущая 1575,42 МГц, "
         "модуляция КАМ-16, скорость 2048 кбит/с. Прошу глянуть, это тот же "
         "ствол, что и в декабрьском заключении, или всё-таки соседний?")


@unittest.skipUnless(NODE, "нет node — беседы не проверить")
class БеседыTestCase(unittest.TestCase):
    """Общая часть: гоняем настоящий код бесед из app.js."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls._tmp.name) / "besedy.mjs"
        cls.script.write_text(PRELUDE + talks_block() + EPILOGUE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def прогнать(self, шаги):
        полные = [{"do": "сервер", "items": БЕСЕДЫ, "rooms": КОМНАТЫ}] + шаги
        done = subprocess.run(
            [NODE, str(self.script), json.dumps(полные, ensure_ascii=False)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        return json.loads(done.stdout)


class НабранноеПереживаетОпрос(БеседыTestCase):
    """Сама жалоба: писал дольше десяти секунд — всё пропало."""

    def test_абзац_на_месте_после_опроса(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(АБЗАЦ, видно["поле"], "опрос стёр набранное")

    def test_абзац_переживает_три_опроса_подряд(self):
        """Полминуты за письмом — это три опроса, а не один."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "опрос"}, {"do": "опрос"}, {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(АБЗАЦ, видно["поле"])

    def test_пришедшее_сообщение_не_уносит_набранное(self):
        """Ответ собеседника показывается, а недописанное остаётся."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "пришло", "talk": 1, "id": 12, "text": "Смотрю"},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(АБЗАЦ, видно["поле"], "новое сообщение стёрло набранное")
        self.assertIn("Смотрю", " ".join(видно["реплики"]),
                      "ответ собеседника не показался")

    def test_курсор_остаётся_там_где_был(self):
        """Пишущему вернули текст, но курсор в конце — правка посреди фразы срывается."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "курсор", "at": 30},
            {"do": "пришло", "talk": 1, "id": 12, "text": "Смотрю"},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertTrue(видно["вфокусе"], "поле потеряло фокус — дописывать нечем")
        self.assertEqual([30, 30], видно["курсор"])

    def test_отвлёкшемуся_фокус_не_навязывают(self):
        """Набрал, ушёл читать письмо — курсор не должен прыгать обратно."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "отвлечься"},
            {"do": "пришло", "talk": 1, "id": 12, "text": "Смотрю"},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(АБЗАЦ, видно["поле"])
        self.assertFalse(видно["вфокусе"], "фокус увели у того, кто читает письмо")


class ЛишнегоНеПерерисовываем(БеседыTestCase):
    """Когда ничего не изменилось, правую половину не трогают вовсе."""

    def test_поле_то_же_самое_после_пустого_опроса(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "метка", "name": "первое"},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual("первое", видно["узел"],
                         "поле пересобрали, хотя в беседе ничего не менялось")

    def test_новое_сообщение_поле_всё_же_пересобирает(self):
        """Обратная сторона: пропустить настоящее изменение нельзя."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "метка", "name": "первое"},
            {"do": "пришло", "talk": 1, "id": 12, "text": "Смотрю"},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertIsNone(видно["узел"], "лента не обновилась вместе с сообщением")
        self.assertIn("Смотрю", " ".join(видно["реплики"]))

    def test_пустая_половина_не_пересобирается(self):
        """«Беседа не выбрана» — тоже картинка, и мигать ей незачем."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "пусто"},
            {"do": "смотреть"},
            {"do": "пусто"},
            {"do": "смотреть"},
        ])
        self.assertIsNotNone(видно[0]["пусто_узел"])
        self.assertEqual(видно[0]["пусто_узел"], видно[1]["пусто_узел"],
                         "пустую половину собрали заново")


class ЧерновикЗнаетСвоюБеседу(БеседыTestCase):
    """Начатое Жукову не должно оказаться в разговоре с Титовым."""

    def test_черновик_не_переходит_в_другую_беседу(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "открыть", "talk": 2},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual("", видно["поле"],
                         "написанное Жукову подставилось в беседу с Титовым")
        self.assertEqual(2, видно["беседа"])

    def test_черновик_ждёт_в_своей_беседе(self):
        """Отвлёкся на другой разговор и вернулся — абзац на месте."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "открыть", "talk": 2},
            {"do": "открыть", "talk": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(АБЗАЦ, видно["поле"], "черновик не дождался в своей беседе")

    def test_два_черновика_держатся_врозь(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": "Жукову про 47/312"},
            {"do": "открыть", "talk": 2},
            {"do": "печатать", "text": "Титову про дежурство"},
            {"do": "открыть", "talk": 1},
            {"do": "смотреть"},
            {"do": "открыть", "talk": 2},
            {"do": "смотреть"},
        ])
        self.assertEqual("Жукову про 47/312", видно[0]["поле"])
        self.assertEqual("Титову про дежурство", видно[1]["поле"])


class ОтправкаЗакрываетЧерновик(БеседыTestCase):
    """Отправленное не возвращается, а курсор остаётся в поле."""

    def test_после_отправки_поле_пустое(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "отправить"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual([{"talk": 1, "text": АБЗАЦ}], видно["послано"])
        self.assertEqual("", видно["поле"], "отправленное осталось висеть в поле")
        self.assertEqual({}, видно["черновики"], "черновик пережил отправку")

    def test_отправленное_не_возвращается_опросом(self):
        """Худший случай: отправил, поле очистилось, а через десять секунд текст вернулся."""
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "отправить"},
            {"do": "пришло", "talk": 1, "id": 13, "text": "Тот же"},
            {"do": "опрос"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual("", видно["поле"], "отправленный абзац вернулся в поле")

    def test_после_отправки_можно_писать_дальше_не_целясь_мышью(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "отправить"},
            {"do": "смотреть"},
        ])[0]
        self.assertTrue(видно["вфокусе"], "курсор ушёл из поля — следующую фразу не начать")

    def test_отправленное_видно_в_ленте(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "отправить"},
            {"do": "смотреть"},
        ])[0]
        self.assertIn("КАМ-16", " ".join(видно["реплики"]))


class УбраннаяБеседаНеОставляетСледа(БеседыTestCase):
    """Ушёл из беседы — недописанное туда же."""

    def test_черновик_убранной_беседы_забыт(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "убрать", "talk": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertNotIn("1", видно["черновики"],
                         "черновик остался от беседы, которой больше нет")
        self.assertEqual(["/api/talks/1"], видно["убрано"])

    def test_курсор_не_прыгает_в_оставшуюся_беседу(self):
        """Беседа исчезла из-под рук — соседняя не должна забирать курсор себе.

        Убранная беседа сменяется соседней прямо на месте, без пересборки
        экрана. Если считать её «той же», в чужое поле уедет и фокус, и место
        курсора от разговора, которого уже нет: человек, убравший беседу,
        обнаружит себя пишущим Титову.
        """
        видно = self.прогнать([
            {"do": "открыть", "talk": 2},
            {"do": "печатать", "text": "Титову про дежурство"},
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "убрать", "talk": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(2, видно["беседа"], "после удаления не открылась соседняя беседа")
        self.assertEqual("Титову про дежурство", видно["поле"],
                         "черновик соседней беседы потерялся при удалении")
        self.assertFalse(видно["вфокусе"],
                         "курсор сам перескочил в другой разговор")


class ЧастотаОпроса(БеседыTestCase):
    """Заодно закрепляем сам срок: жалоба была именно про эти секунды."""

    def test_опрос_раз_в_десять_секунд(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(10000, видно["каждые"])


class ЧерновикиНеНакапливаются(БеседыTestCase):
    """Пустое поле не должно оставлять запись: словарь черновиков не свалка."""

    def test_стёртое_поле_не_оставляет_черновика(self):
        видно = self.прогнать([
            {"do": "открыть", "talk": 1},
            {"do": "печатать", "text": АБЗАЦ},
            {"do": "печатать", "text": ""},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual({}, видно["черновики"])


if __name__ == "__main__":
    unittest.main()
