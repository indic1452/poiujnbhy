# -*- coding: utf-8 -*-
"""Рабочий стол анализа: сетка бит, таблица кадров с отбором, статистика столбца, журнал
массива, притоки этапа отдельными массивами, усечение, SDH в автоматическом разборе."""

import base64
import time
import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import rastr
from reportgen.potok.razbor import снять_вручную


def распаковать(d):
    return np.frombuffer(base64.b64decode(d["данные"]), dtype=np.uint8)


class СеткаTests(unittest.TestCase):
    def setUp(self):
        self.биты = np.random.default_rng(1).integers(0, 2, 100_000).astype(np.uint8)

    def test_биты_по_строкам(self):
        d = rastr.сетка(self.биты, 1000, 3, 5, 10, 100, 20, 1)
        self.assertEqual((10, 20, "биты", 99), (d["строк"], d["пикселей"], d["вид"], d["всего_строк"]))
        байты = распаковать(d).reshape(10, 3)                       # 20 бит — 3 байта на строку
        ждём = self.биты[3:3 + 99 * 1000].reshape(99, 1000)[5:15, 100:120]
        self.assertTrue(np.array_equal(ждём, np.unpackbits(байты, axis=1)[:, :20]))

    def test_сжатие_долями(self):
        d = rastr.сетка(self.биты, 1000, 0, 0, 4, 0, 1000, 8)
        self.assertEqual(("доли", 125), (d["вид"], d["пикселей"]))
        доли = распаковать(d).reshape(4, 125)
        ждём = np.round(self.биты[:4000].reshape(4, 125, 8).mean(axis=2) * 255)
        self.assertTrue(np.array_equal(ждём, доли))
        # Хвост, не кратный сжатию, — своим пикселем.
        d = rastr.сетка(self.биты, 1003, 0, 0, 2, 0, 1003, 8)
        self.assertEqual(126, d["пикселей"])
        self.assertEqual(round(self.биты[1000:1003].mean() * 255), распаковать(d)[125])

    def test_края(self):
        d = rastr.сетка(self.биты, 1000, 0, 10_000, 5, 0, 10, 1)
        self.assertEqual((99, 1), (d["строка"], d["строк"]))       # последняя строка, не больше
        пусто = rastr.сетка(self.биты[:10], 1000, 0, 0, 5, 0, 10, 1)
        self.assertEqual((0, ""), (пусто["строк"], пусто["данные"]))


class ТаблицаКадровTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(2)
        кадры = rng.integers(0, 256, (300, 12)).astype(np.uint8)
        кадры[:, 0] = 0x47
        кадры[:, 1] = np.arange(300) % 256                        # счётчик
        кадры[::3, 2] = 0xA5
        self.кадры = кадры
        self.биты = np.concatenate([np.ones(5, np.uint8), np.unpackbits(кадры.reshape(-1))])

    def test_байты_и_отбор(self):
        t = rastr.кадры_таблицей(self.биты, 96, 5, 0, 4)
        self.assertEqual((300, 300, 12, [0, 1, 2, 3]), (t["всего"], t["отобрано"], t["байт_в_кадре"], t["номера"]))
        self.assertTrue(np.array_equal(self.кадры[:4].reshape(-1), распаковать(t)))
        t = rastr.кадры_таблицей(self.биты, 96, 5, 0, 1000, [{"место": 2, "значение": 0xA5}])
        self.assertEqual(list(range(0, 300, 3)), [н for н in t["номера"] if н % 3 == 0])
        self.assertTrue(all(self.кадры[н, 2] == 0xA5 for н in t["номера"]))
        кроме = rastr.кадры_таблицей(self.биты, 96, 5, 0, 1000, [{"место": 2, "значение": 0xA5, "не": True}])
        self.assertEqual(300, t["отобрано"] + кроме["отобрано"])
        старший = rastr.кадры_таблицей(self.биты, 96, 5, 0, 1000, [{"место": 2, "значение": 0xA, "полубайт": "старший"}])
        self.assertTrue(all(self.кадры[н, 2] >> 4 == 0xA for н in старший["номера"]))
        младший = rastr.кадры_таблицей(self.биты, 96, 5, 0, 1000, [{"место": 1, "значение": 3, "полубайт": "младший"}])
        self.assertEqual([н for н in range(300) if н % 16 == 3], младший["номера"])
        with self.assertRaises(ValueError):
            rastr.кадры_таблицей(self.биты, 96, 5, 0, 10, [{"место": 99, "значение": 1}])

    def test_порядок_бит_и_хвост(self):
        t = rastr.кадры_таблицей(self.биты, 96, 5, 0, 1, порядок="младший")
        self.assertEqual(0xE2, распаковать(t)[0])                  # 0x47 = 01000111 → 11100010
        t = rastr.кадры_таблицей(self.биты, 100, 5, 0, 1)
        self.assertEqual(13, t["байт_в_кадре"])                     # 100 бит → 13 байт, хвост нулями

    def test_столбец(self):
        self.assertEqual(["постоянное поле: 0x47"], rastr.столбец_кадров(self.биты, 96, 5, 0)["вывод"])
        self.assertIn("счётчик", " ".join(rastr.столбец_кадров(self.биты, 96, 5, 1)["вывод"]))
        отобрано = rastr.столбец_кадров(self.биты, 96, 5, 2, отбор=[{"место": 2, "значение": 0xA5}])
        self.assertEqual(["постоянное поле: 0xa5"], отобрано["вывод"])


class УсечениеTests(unittest.TestCase):
    def test_усечение(self):
        биты = np.arange(1000) % 2
        ряд, запись = снять_вручную(биты.astype(np.uint8), "усечение от 10 длина 100")
        self.assertEqual(100, len(ряд))
        self.assertTrue(np.array_equal(биты[10:110], ряд))
        ряд, _ = снять_вручную(биты.astype(np.uint8), "усечение от 900 до 950")
        self.assertEqual(50, len(ряд))
        for плохое in ("усечение от 2000", "усечение от 10 до 5", "усечение от 0 длина 5000"):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                снять_вручную(биты.astype(np.uint8), плохое)


class СтолЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client

    def дождаться(self, ид):
        for _ in range(900):
            с = self.к.get(f"/api/potok/{ид}").json()
            if с["состояние"] in ("готово", "ошибка"):
                return с
            time.sleep(0.1)
        self.fail("не дождались")

    def test_сетка_таблица_журнал(self):
        к = self.к
        rng = np.random.default_rng(3)
        кадры = rng.integers(0, 256, (200, 16)).astype(np.uint8)
        кадры[:, 0] = 0x1A
        ид = к.post("/api/potok", data={"profile": "быстро"},
                    files={"file": ("t.bin", кадры.tobytes(), "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        g = к.get(f"/api/potok/{ид}/grid", params={"stage": 0, "period": 128, "rows": 3, "cols": 8}).json()
        self.assertEqual([0x1A] * 3, list(распаковать(g)))
        self.assertEqual(400, к.get(f"/api/potok/{ид}/grid", params={"period": 0}).status_code)
        t = к.post(f"/api/potok/{ид}/frames", json={"stage": 0, "period": 128, "limit": 5,
                                                    "filter": [{"place": 1, "value": int(кадры[0, 1])}]}).json()
        self.assertTrue(all(кадры[н, 1] == кадры[0, 1] for н in t["номера"]))
        self.assertEqual(400, к.post(f"/api/potok/{ид}/frames", json={"period": 4}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/frames", json={"period": 128, "filter": [{"value": 1}]}).status_code)
        стат = к.post(f"/api/potok/{ид}/framecol", json={"period": 128, "place": 0}).json()
        self.assertEqual(["постоянное поле: 0x1a"], стат["вывод"])
        # Журнал массива: записи по этапам, переживают перезапрос.
        к.post(f"/api/potok/{ид}/journal", json={"stage": 0, "operation": "Поиск периода", "text": "128 бит", "layer": ""})
        к.post(f"/api/potok/{ид}/journal", json={"stage": 1, "operation": "заметка", "text": "x" * 30000})
        журнал = к.get(f"/api/potok/{ид}/journal").json()["journal"]
        self.assertEqual(["Поиск периода"], [з["операция"] for з in журнал["0"]])
        self.assertEqual(20000, len(журнал["1"][0]["текст"]))
        self.assertEqual(404, к.get("/api/potok/20200101-000000-abcdef/journal").status_code)
        # Дерево: у узлов — этапы коротко и происхождение (для сетки массивов).
        дерево = к.get(f"/api/potok/{ид}/tree").json()["tree"]
        self.assertIn("этапы_кратко", дерево)
        self.assertEqual([], дерево["происхождение"])
        # Исходный массив скачивается как этап 0.
        self.assertEqual(кадры.tobytes(), к.get(f"/api/potok/{ид}/stage/0").content)

    def test_притоки_отдельными_массивами(self):
        # E2 из четырёх E1: этап с притоками, каждый приток — файлом и новым узлом.
        import potok_sintez as с
        from reportgen.potok import pdh
        from reportgen.potok.bity import в_биты
        e2 = next(и for и in pdh.ИЕРАРХИИ if и.имя == "E2")
        притоки = [в_биты(с.e1(2600, сид=i)) for i in range(4)]
        поток = с.pdh(притоки, e2, 2400, [0.42, 0.40, 0.45, 0.43])
        к = self.к
        ид = к.post("/api/potok", data={"profile": "быстро"},
                    files={"file": ("e2.bin", bytes(np.packbits(поток)), "application/octet-stream")}).json()["id"]
        состояние = self.дождаться(ид)
        этап = next(э for э in состояние["этапы"] if э.get("притоки"))
        self.assertEqual(4, len(этап["притоки"]))
        n = этап["номер"]
        файл = к.get(f"/api/potok/{ид}/stage/{n}/tributary/1")
        self.assertEqual(200, файл.status_code)
        self.assertGreater(len(файл.content), 1000)
        self.assertEqual(404, к.get(f"/api/potok/{ид}/stage/{n}/tributary/9").status_code)
        узел = к.post(f"/api/potok/{ид}/tributary", json={"stage": n, "number": 1, "analyze": False}).json()["id"]
        новый = self.дождаться(узел)
        self.assertEqual("готово", новый["состояние"])
        self.assertEqual(f"{ид}#{n}", новый["от"])
        self.assertEqual(файл.content, к.get(f"/api/potok/{узел}/stage/0").content)
        self.assertEqual(404, к.post(f"/api/potok/{ид}/tributary", json={"stage": n, "number": 7}).status_code)


class АвтоматSdhTests(unittest.TestCase):
    def test_stm1_до_e1_и_каналов(self):
        # STM-1 с тремя E1 в TU-12: автомат проходит SDH → VC-4 → TU-12 → E1 → каналы.
        import test_potok_sdh as т
        from reportgen.potok.bity import в_байты, в_биты
        from reportgen.potok.razbor import разобрать
        import potok_sintez as с
        rng = np.random.default_rng(3)
        кадров = 200
        tu = {}
        for адрес, p, сид in (((0, 0, 0), 5, 1), ((1, 2, 1), 77, 2)):
            vc, _ = т.vc12_из_e1(в_биты(с.e1(400, сид=сид)), кадров // 4 + 2, rng)
            tu[адрес] = т.tu12_кадры(vc, p, кадров)
        поток = т.stm([т.vc4_кадры(кадров, tu)], [522])
        разбор = разобрать(данные=в_байты(поток), имя="stm1.bin", профиль="быстро")
        что = [н.что for н in разбор.находки]
        self.assertTrue(что[0].startswith("SDH STM-1 (STS-3) (G.707): VC-4"), что)
        self.assertEqual(2, sum(1 for ч in что if ч.startswith("E1 по G.704") and "TU-12" in ч), что)


if __name__ == "__main__":
    unittest.main()
