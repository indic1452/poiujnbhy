# -*- coding: utf-8 -*-
"""Срочное находит человека и без https.

Окно уведомления поверх других браузер показывает только защищённой странице.
Отдел работает по обычному адресу в сети, и требовать ради этого https со
всех рабочих мест — «слишком сложно и муторно», и это правда.

Поэтому срочное достучивается тем, что работает ВЕЗДЕ:

* заголовок вкладки мигает «‼ ВЫЗОВ В КАБИНЕТ» — свёрнутое окно показывает
  его прямо в панели задач;
* сигнал повторяется, пока человек не вернётся к окну, но не бесконечно:
  сигнализация, которая воет вечно, кончается выключенным звуком.

Проверяется это не подстроками в исходнике: настоящий кусок app.js
запускается через node с поддельными часами, и сверяется то, что человек
увидит в панели задач.
"""

import json
import re
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
const записано = { сигналов: 0, заголовки: [] };
const часы = { дальше: [], время: 0 };

function setInterval(fn, ms) { часы.дальше.push({ fn: fn, ms: ms }); return часы.дальше.length; }
function clearInterval(id) { часы.дальше = []; }
function такт(сколько) { for (let i = 0; i < сколько; i += 1) часы.дальше.forEach((t) => t.fn()); }

const document = { hidden: true, _title: '2 специальный отдел' };
Object.defineProperty(document, 'title', {
  get() { return this._title; },
  set(v) { this._title = v; записано.заголовки.push(v); },
});
function playAlert() { записано.сигналов += 1; }
function brandShort() { return '2СО'; }
const notices = { unseen: 0 };
"""

EPILOGUE = r"""
const шаги = JSON.parse(process.argv[2]);
const итог = [];
for (const шаг of шаги) {
  if (шаг.do === 'скрыть') document.hidden = true;
  else if (шаг.do === 'показать') document.hidden = false;
  else if (шаг.do === 'непрочитано') { notices.unseen = шаг.n; paintTitle(шаг.n); }
  else if (шаг.do === 'тревога') startAlarm(alarmWord({ title: шаг.title }));
  else if (шаг.do === 'показ') startAlarm(alarmWord({ title: шаг.title }), true);
  else if (шаг.do === 'такт') такт(шаг.n);
  else if (шаг.do === 'снять') { stopAlarm(); paintTitle(notices.unseen); }
  else if (шаг.do === 'смотреть') {
    итог.push({
      заголовок: document.title,
      сигналов: записано.сигналов,
      мигает: Boolean(alarm.timer),
      все: записано.заголовки.slice(),
    });
  } else throw new Error('неизвестный шаг ' + шаг.do);
}
process.stdout.write(JSON.stringify(итог));
"""


def кусок(имя: str, source: str) -> str:
    """Тело функции по имени — обходом фигурных скобок."""
    начало = source.index("function " + имя)
    место = source.index("{", начало)
    глубина = 0
    while True:
        if source[место] == "{":
            глубина += 1
        elif source[место] == "}":
            глубина -= 1
            if глубина == 0:
                break
        место += 1
    return source[начало:место + 1]


def тревога_из_app() -> str:
    source = APP_JS.read_text(encoding="utf-8")
    начало = source.index("    let titleBase = '';")
    конец = source.index("    function playAlert() {")
    return source[начало:конец] + "\n" + кусок("alarmWord", source)


@unittest.skipUnless(NODE, "нет node — тревогу не проверить")
class ТревогаTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.script = Path(cls._tmp.name) / "trevoga.mjs"
        cls.script.write_text(PRELUDE + тревога_из_app() + EPILOGUE, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def прогнать(self, шаги):
        готово = subprocess.run(
            [NODE, str(self.script), json.dumps(шаги, ensure_ascii=False)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)


class ЗаголовокМигает(ТревогаTestCase):
    def test_в_панели_задач_видно_вызов_в_кабинет(self):
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "такт", "n": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertIn("ВЫЗОВ", видно["заголовок"])
        self.assertTrue(видно["мигает"])

    def test_видно_и_кто_вызывает(self):
        """Так вызов и приходит на самом деле: «Вас вызывает Никитин В. П.».

        В панели задач место узкое, и первым словом должно стоять «ВЫЗОВ», а
        не фамилия: по фамилии не понять, что от тебя хотят.
        """
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывает Никитин В. П."},
            {"do": "такт", "n": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertTrue(видно["заголовок"].startswith("‼ ВЫЗОВ"), видно["заголовок"])
        self.assertIn("НИКИТИН В. П.", видно["заголовок"])

    def test_заголовок_чередуется_а_не_застывает(self):
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "такт", "n": 4},
            {"do": "смотреть"},
        ])[0]
        мигания = [t for t in видно["все"] if "ВЫЗОВ" in t]
        обычные = [t for t in видно["все"] if "ВЫЗОВ" not in t]
        self.assertGreaterEqual(len(мигания), 2, "надпись не мигает")
        self.assertGreaterEqual(len(обычные), 1, "надпись не гаснет — это не мигание")

    def test_отчёт_вернули_называется_своими_словами(self):
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Отчёт возвращён на доработку"},
            {"do": "такт", "n": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertIn("ОТЧЁТ ВЕРНУЛИ", видно["заголовок"])


class СигналПовторяется(ТревогаTestCase):
    def test_первый_сигнал_сразу(self):
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "смотреть"},
        ])[0]
        self.assertEqual(1, видно["сигналов"])

    def test_один_сигнал_можно_прослушать_поэтому_он_не_один(self):
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "такт", "n": 20},
            {"do": "смотреть"},
        ])[0]
        self.assertGreater(видно["сигналов"], 1, "сигнал прозвучал один раз")

    def test_сигнализация_не_воет_вечно(self):
        """Иначе звук выключат насовсем — и тогда не услышат ничего."""
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "такт", "n": 400},
            {"do": "смотреть"},
        ])[0]
        self.assertLessEqual(видно["сигналов"], 10)
        self.assertFalse(видно["мигает"], "тревога не унялась сама")


class ПоказПоПросьбе(ТревогаTestCase):
    """«Проверить вызов»: человек смотрит на экран и должен увидеть, что будет."""

    def test_показ_идёт_и_при_открытом_окне(self):
        видно = self.прогнать([
            {"do": "показать"},
            {"do": "показ", "title": "Вас вызывает Никитин В. П."},
            {"do": "такт", "n": 3},
            {"do": "смотреть"},
        ])[0]
        мигания = [t for t in видно["все"] if "ВЫЗОВ" in t]
        self.assertTrue(мигания,
                        "проверка ничего не показывает: окно ведь открыто")
        self.assertTrue(видно["мигает"])

    def test_показ_сам_кончается(self):
        видно = self.прогнать([
            {"do": "показать"},
            {"do": "показ", "title": "Вас вызывает Никитин В. П."},
            {"do": "такт", "n": 12},
            {"do": "смотреть"},
        ])[0]
        self.assertFalse(видно["мигает"], "проверка мигает без конца")
        self.assertNotIn("ВЫЗОВ", видно["заголовок"])


class ЧеловекВернулся(ТревогаTestCase):
    def test_вернулся_к_окну_тревога_снимается(self):
        видно = self.прогнать([
            {"do": "непрочитано", "n": 3},
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "такт", "n": 3},
            {"do": "показать"},
            {"do": "такт", "n": 1},
            {"do": "смотреть"},
        ])[0]
        self.assertFalse(видно["мигает"])
        self.assertNotIn("ВЫЗОВ", видно["заголовок"])
        self.assertIn("(3)", видно["заголовок"],
                      "счётчик непрочитанного не вернулся в заголовок")

    def test_счётчик_не_перебивает_тревогу(self):
        """Пока мигает срочное, обычный счётчик заголовок не отнимает."""
        видно = self.прогнать([
            {"do": "скрыть"},
            {"do": "тревога", "title": "Вас вызывают в кабинет"},
            {"do": "такт", "n": 1},
            {"do": "непрочитано", "n": 5},
            {"do": "смотреть"},
        ])[0]
        self.assertIn("ВЫЗОВ", видно["заголовок"])
        self.assertNotIn("(5)", видно["заголовок"])


class НичегоНеТребуетОтМашины(unittest.TestCase):
    """Главное свойство: это работает по любому адресу, без https."""

    def test_тревога_не_зависит_от_разрешений_браузера(self):
        текст = тревога_из_app()
        self.assertNotIn("Notification", текст,
                         "тревога упёрлась в разрешения браузера — а по http "
                         "их не спросить")
        self.assertNotIn("isSecureContext", текст)

    def test_в_настройках_сказано_что_работает_и_без_https(self):
        """И не сказано ничего ставить: на рабочих местах это запрещено."""
        текст = APP_JS.read_text(encoding="utf-8")
        self.assertIn("ВЫЗОВ В КАБИНЕТ", текст)
        начало = текст.index("Окно Windows поверх других: здесь недоступно")
        подсказка = текст[начало:начало + 1400]
        self.assertIn("Ставить на эту машину тоже ничего не", подсказка)
        for запрет in ("setup-https", "установите", "Доверять серверу"):
            self.assertNotIn(запрет, подсказка,
                             "человека снова отправляют что-то устанавливать")


if __name__ == "__main__":
    unittest.main()
