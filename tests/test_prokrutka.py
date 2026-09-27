"""Лента помощника не утаскивает читающего вниз.

Отдел: «когда ответ пишет, меня принудительно вниз отправляет страница, не
могу читать выше ничего». Лента прокручивалась вниз на каждый кусок текста,
раз в 70 мс, где бы человек ни читал.

Теперь как в обычных чатах: лента следует за ответом, пока человек внизу;
прокрутил вверх — стоит; вернулся вниз или нажал «↓ к ответу» — снова
следует. Проверяется поведение самих функций app.js в node, на поддельной
ленте с настоящей арифметикой прокрутки.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from test_oblik import вырезать

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "reportgen" / "web" / "static" / "app.js"
NODE = shutil.which("node")

#: Лента, кнопка и разговор — ровно то, что трогают функции прокрутки.
PRELUDE = r"""
function Лента() {
  const f = {
    scrollTop: 0, scrollHeight: 2000, clientHeight: 600, clientWidth: 800,
    offsetTop: 60, offsetParent: { clientHeight: 900 }, слушатели: {}, детей: 0,
    addEventListener(имя, fn) { this.слушатели[имя] = fn; },
    событие(имя, данные) { this.слушатели[имя](данные || {}); },
    appendChild() { this.детей += 1; this.scrollHeight += 400; },
  };
  let верх = 0;
  Object.defineProperty(f, 'scrollTop', {
    get() { return верх; },
    set(v) { верх = Math.max(0, Math.min(v, f.scrollHeight - f.clientHeight)); },
  });
  return f;
}
const кнопка = { hidden: true, style: {}, classList: { set: new Set(),
  toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } } };
const chat = { nodes: {}, messages: [], live: null, current: { id: 1 } };
function liveIsHere() { return !!(chat.live && chat.live.chatId === 1); }
function clear(f) { f.детей = 0; f.scrollHeight = f.clientHeight; f.scrollTop = 0; }
function messageNode() { return {}; }
function liveNode() { return {}; }
function emptyChatState() { return {}; }
"""

ФУНКЦИИ = ("уНиза", "scrollFeed", "ленточныеСобытия", "кнопкаВниз", "renderFeed")


def выполнить(сценарий: str):
    js = APP_JS.read_text(encoding="utf-8")
    код = (PRELUDE + "const У_НИЗА_PX = 48;\n"
           + "\n".join(вырезать(js, имя) for имя in ФУНКЦИИ) + "\n" + сценарий)
    итог = subprocess.run([NODE, "-e", код], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    if итог.returncode != 0:
        raise AssertionError("node упал:\n" + итог.stderr)
    return json.loads(итог.stdout.strip().splitlines()[-1])


НАЧАЛО = r"""
const f = Лента();
chat.nodes.feed = f; chat.nodes.down = кнопка;
ленточныеСобытия(f);
chat.следить = true;
function растёт(н) { f.scrollHeight += н; scrollFeed(); if (chat.прокручиваем) f.событие('scroll'); }
"""


@unittest.skipUnless(NODE, "node не установлен")
class ПрокруткаTests(unittest.TestCase):
    def test_внизу_лента_следит_за_ответом(self):
        итог = выполнить(НАЧАЛО + r"""
        растёт(300); растёт(300);
        console.log(JSON.stringify({ низ: f.scrollHeight - f.scrollTop - f.clientHeight,
                                     кнопка: кнопка.hidden }));
        """)
        self.assertEqual({"низ": 0, "кнопка": True}, итог)

    def test_колесо_вверх_отпускает_ленту_сразу(self):
        """До события scroll: иначе кусок ответа вернул бы ленту вниз."""
        итог = выполнить(НАЧАЛО + r"""
        растёт(300);
        f.событие('wheel', { deltaY: -120 });
        const было = f.scrollTop;
        растёт(300); растёт(300);
        console.log(JSON.stringify({ стоит: f.scrollTop === было, следить: chat.следить,
                                     кнопка: кнопка.hidden }));
        """)
        self.assertEqual({"стоит": True, "следить": False, "кнопка": False}, итог)

    def test_колесо_вниз_не_отпускает(self):
        итог = выполнить(НАЧАЛО + r"""
        растёт(300);
        f.событие('wheel', { deltaY: 120 });
        растёт(300);
        console.log(JSON.stringify({ низ: f.scrollHeight - f.scrollTop - f.clientHeight }));
        """)
        self.assertEqual({"низ": 0}, итог)

    def test_касание_и_полоса_прокрутки_тоже_отпускают(self):
        итог = выполнить(НАЧАЛО + r"""
        f.событие('touchmove');
        const касание = chat.следить;
        chat.следить = true;
        f.событие('mousedown', { offsetX: 790 });
        const внутри = chat.следить;
        f.событие('mousedown', { offsetX: 805 });
        console.log(JSON.stringify({ касание, внутри, полоса: chat.следить }));
        """)
        self.assertEqual({"касание": False, "внутри": True, "полоса": False}, итог)

    def test_своя_прокрутка_не_решает_за_человека(self):
        итог = выполнить(НАЧАЛО + r"""
        f.scrollHeight += 500;
        scrollFeed();
        const флаг = chat.прокручиваем;
        f.scrollTop = 100;          // человек двинулся до того, как пришло событие
        f.событие('scroll');        // это событие — от нашей прокрутки
        const после_своего = chat.следить;
        f.событие('scroll');        // а это — уже человека
        console.log(JSON.stringify({ флаг, после_своего, после_человека: chat.следить }));
        """)
        self.assertEqual({"флаг": True, "после_своего": True, "после_человека": False}, итог)

    def test_сам_вернулся_вниз_снова_следит(self):
        итог = выполнить(НАЧАЛО + r"""
        f.событие('wheel', { deltaY: -120 }); f.scrollTop = 0; f.событие('scroll');
        f.scrollTop = f.scrollHeight;  f.событие('scroll');
        растёт(300);
        console.log(JSON.stringify({ следить: chat.следить,
                                     низ: f.scrollHeight - f.scrollTop - f.clientHeight }));
        """)
        self.assertEqual({"следить": True, "низ": 0}, итог)

    def test_кнопка_возвращает_и_включает_слежение(self):
        итог = выполнить(НАЧАЛО + r"""
        f.событие('wheel', { deltaY: -120 }); f.scrollTop = 0; f.событие('scroll');
        chat.live = { chatId: 1, running: true };
        растёт(300);
        const живая = кнопка.classList.set.has('is-live');
        const видна = !кнопка.hidden;
        scrollFeed(true);
        console.log(JSON.stringify({ живая, видна, следить: chat.следить,
                                     низ: f.scrollHeight - f.scrollTop - f.clientHeight,
                                     скрыта: кнопка.hidden, отступ: кнопка.style.bottom || '' }));
        """)
        self.assertEqual(True, итог["живая"])
        self.assertEqual(True, итог["видна"])
        self.assertEqual(True, итог["следить"])
        self.assertEqual(0, итог["низ"])
        self.assertEqual(True, итог["скрыта"])

    def test_кнопка_стоит_над_полем_ввода(self):
        итог = выполнить(НАЧАЛО + r"""
        f.событие('wheel', { deltaY: -120 }); f.scrollTop = 0; f.событие('scroll');
        кнопкаВниз();
        console.log(JSON.stringify({ отступ: кнопка.style.bottom }));
        """)
        # Панель 900, лента с 60 по 660: до нижнего края ленты 240, плюс 14.
        self.assertEqual({"отступ": "254px"}, итог)

    def test_пересборка_ленты_не_сдвигает_читающего(self):
        итог = выполнить(НАЧАЛО + r"""
        chat.messages = [{}, {}, {}, {}, {}, {}];
        renderFeed();
        f.событие('wheel', { deltaY: -120 }); f.scrollTop = 700; f.событие('scroll');
        renderFeed();
        const читающий = f.scrollTop;
        chat.следить = true;
        renderFeed();
        console.log(JSON.stringify({ читающий,
                                     следящий: f.scrollHeight - f.scrollTop - f.clientHeight }));
        """)
        self.assertEqual({"читающий": 700, "следящий": 0}, итог)


class ПодключениеTests(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_лента_слушает_человека(self):
        панель = вырезать(self.js, "buildTalkPanel")
        self.assertIn("ленточныеСобытия(feed);", панель)
        self.assertIn("onclick: () => scrollFeed(true)", панель)

    def test_новый_вопрос_и_новый_разговор_снова_следят(self):
        self.assertIn("chat.следить = true;", вырезать(self.js, "resetChat"))
        self.assertIn("chat.следить = true;", вырезать(self.js, "streamAnswer"))

    def test_прокрутка_в_потоке_идёт_через_слежение(self):
        """Поток рисует раз в 70 мс; прокручивать безусловно он не должен."""
        поток = вырезать(self.js, "streamAnswer")
        self.assertNotIn("feed.scrollTop = feed.scrollHeight", поток)
        self.assertEqual(1, self.js.count("feed.scrollTop = feed.scrollHeight"))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
