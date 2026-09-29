"""Помощник на столе: разговор о массиве в панели справа, без отдельной вкладки «Разбор потока».

Решения панели — какой разговор помнить, о чём спрашивать, заводить ли новый —
берутся прямо из app.js и выполняются в node; устройство меню и стола
проверяется по тексту app.js, как у других страниц.
"""

import json
import shutil
import subprocess
import unittest

import _bootstrap  # noqa: F401
from test_potok_sessii import APP_JS, функции_js

ФУНКЦИИ = ["ключПомощникаСтола", "памятьПомощникаСтола", "вопросОМассивеСтола", "подписьМассиваПомощнику",
           "новыйРазговорОМассиве"]


@unittest.skipUnless(shutil.which("node"), "нужен node")
class РешенияПанелиTests(unittest.TestCase):
    def выполнить(self, случаи: list[dict]) -> list:
        код = функции_js(ФУНКЦИИ, []) + """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const итог = случаи.map((с) => {
    switch (с.что) {
    case 'ключ': return ключПомощникаСтола(с.сессия, с.задание);
    case 'память': return памятьПомощникаСтола(с.v);
    case 'вопрос': return вопросОМассивеСтола(с.у);
    case 'подпись': return подписьМассиваПомощнику(с.у);
    case 'новый': return новыйРазговорОМассиве(памятьПомощникаСтола(с.память), с.открыт, с.у, с.сообщений);
    }
    return null;
});
process.stdout.write(JSON.stringify(итог));
"""
        ход = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=60)
        self.assertEqual("", ход.stderr)
        return json.loads(ход.stdout)

    def test_ключ_у_сессии_и_разбора_свой(self):
        self.assertEqual(["stol-ai:s:abc", "stol-ai:j:77", "stol-ai:s:abc"],
                         self.выполнить([{"что": "ключ", "сессия": "abc", "задание": None},
                                         {"что": "ключ", "сессия": None, "задание": "77"},
                                         {"что": "ключ", "сессия": "abc", "задание": "77"}]))

    def test_память_испорченное_как_пустое(self):
        пусто = {"открыта": False, "чат": None, "ключ": None, "подпись": ""}
        случаи = [None, "строка", 5, [], {},
                  {"открыта": True, "чат": 12, "ключ": "3:0", "подпись": "массив 0.1"},
                  {"открыта": "да", "чат": "12", "ключ": 5, "подпись": None},
                  {"открыта": 1, "чат": 0}, {"чат": -3}, {"чат": 2.5}, {"чат": 1}]
        self.assertEqual([пусто] * 5 + [
            {"открыта": True, "чат": 12, "ключ": "3:0", "подпись": "массив 0.1"},
            # Номер разговора строкой — тоже номер (так он приходит из хранилища старых версий).
            {"открыта": False, "чат": 12, "ключ": None, "подпись": ""},
            пусто, пусто, пусто, dict(пусто, чат=1)],
            self.выполнить([{"что": "память", "v": v} for v in случаи]))

    def test_вопрос_об_этапе_или_о_потоке(self):
        self.assertEqual([{"stage": 0}, {"stage": 3, "topic": "этап"}, {"stage": 1, "topic": "этап"}],
                         self.выполнить([{"что": "вопрос", "у": {"stage": 0}}, {"что": "вопрос", "у": {"stage": 3}},
                                         {"что": "вопрос", "у": {"stage": 1}}]))

    def test_подпись(self):
        self.assertEqual(["массив 1.2 · «кадры»"],
                         self.выполнить([{"что": "подпись", "у": {"номер": "1.2", "имя": "кадры"}}]))

    def test_новый_разговор_только_когда_открытый_не_годится(self):
        у = {"ключ": "5:0"}
        помнится = {"чат": 9, "ключ": "5:0"}
        случаи = [
            # Открыт разговор об этом массиве, вопросов ещё нет — годится.
            (помнится, 9, у, 0, False),
            # В нём уже спрашивали — новый.
            (помнится, 9, у, 1, True),
            # Открыт другой разговор — новый.
            (помнится, 8, у, 0, True),
            # Помнится разговор о другом массиве — новый.
            ({"чат": 9, "ключ": "6:0"}, 9, у, 0, True),
            # Ничего не помнится (и ничего не открыто) — новый.
            ({}, None, у, 0, True),
        ]
        self.assertEqual([с[4] for с in случаи],
                         self.выполнить([{"что": "новый", "память": п, "открыт": о, "у": уу, "сообщений": н}
                                         for п, о, уу, н, _ in случаи]))


class УстройствоTests(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_вкладки_разбор_потока_в_меню_нет(self):
        меню = self.js[self.js.index("const SECTIONS = ["):self.js.index("];", self.js.index("const SECTIONS = ["))]
        self.assertNotIn("title: 'Разбор потока'", меню)
        self.assertIn("title: 'Сессии потоков'", меню)
        # Страница разборов осталась по адресу и подсвечивает «Сессии потоков».
        self.assertIn("potok: 'sessions'", self.js)
        self.assertIn("stol: 'sessions'", self.js)
        self.assertIn("'Разборы без сессии'", self.js)

    def test_помощник_в_панели_стола(self):
        # «Спросить помощника» больше не уводит со стола на страницу чата.
        self.assertIn("case 'помощник': помощник.спросить(у); return null;", self.js)
        стол = self.js[self.js.index("async function renderStol("):]
        self.assertIn("панельПомощникаСтола(ключПомощникаСтола(sessionId, jobId)", стол)
        self.assertIn("stol-ai-toggle", стол)
        self.assertIn("помощник.восстановить();", стол)
        # Лента, ответ потоком и источники — те же функции раздела «Помощник».
        панель = self.js[self.js.index("function панельПомощникаСтола("):self.js.index("async function renderStol(")]
        for кусок in ("resetChat();", "chat.nodes = {", "renderFeed();", "await send();", "abortAnswer()"):
            self.assertIn(кусок, панель)
        # Разговор с сервера принимается одной функцией — и страницей помощника, и панелью.
        self.assertEqual(2, self.js.count("принятьРазговор(await api.get('/api/chats/'"))


if __name__ == "__main__":
    unittest.main()
