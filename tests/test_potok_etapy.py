# -*- coding: utf-8 -*-
"""Разбор по этапам: дерево гипотез, ручное снятие слоёв, карта файла, pcap,
задания со страницы «Разбор потока» и сама страница."""

import struct
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import karta, разобрать
from reportgen.potok.bity import в_байты, в_биты
from reportgen.potok.razbor import снять_вручную, этапы
from reportgen.potok.zadaniya import Задания, выгрузка

КОРЕНЬ = Path(__file__).resolve().parents[1]


def цепочка(доля_ошибок=0.005):
    """IP → HDLC → скремблер V.35 → свёрточный 171/133 → ошибки линии."""
    x = в_биты(с.hdlc(с.пакеты_ip(160), флагов_между=4))
    код = с.свёрточный(с.скремблировать(x, (3, 20)))
    return в_байты(код ^ (np.random.default_rng(2).random(len(код)) < доля_ошибок)
                   .astype(np.uint8))


class ДеревоTests(unittest.TestCase):
    def test_этапы_и_альтернативы(self):
        разбор = разобрать(данные=цепочка(), имя="запись.bin")
        список = этапы(разбор)
        self.assertEqual(["код", "скремблер", "канальный", "сетевой"],
                         [э["уровень"] for э in список])
        self.assertEqual("131072 бит", список[0]["выход"])
        self.assertIn("110 кадров", список[2]["выход"])
        # У уровня кода, кроме выбранного, проверена и другая гипотеза.
        отчёт = разбор.отчёт()
        self.assertIn("ДРУГИЕ ГИПОТЕЗЫ", отчёт)

    def test_ход_и_этапы_сообщаются(self):
        строки, найдено = [], []
        разобрать(данные=цепочка(), имя="запись.bin", ход=строки.append, этап=найдено.append)
        self.assertTrue(any("пробую — скремблер" in с_ for с_ in строки))
        self.assertTrue(any(с_.startswith("найдено: пакеты IP") for с_ in строки))
        self.assertEqual(["код", "скремблер", "канальный", "сетевой"],
                         [н.уровень for н in найдено])


class РучноеСнятиеTests(unittest.TestCase):
    def test_скремблер_и_код_вручную(self):
        разбор = разобрать(данные=цепочка(0.0), имя="запись.bin",
                           снять=["свёрточный 171/133 K=7", "скремблер 3,20"])
        self.assertEqual(["вручную", "вручную", "канальный", "сетевой"],
                         [н.уровень for н in разбор.находки])
        self.assertIn("снято по указанию: свёрточный 171/133 K=7", разбор.находки[0].что)

    def test_простые_слои(self):
        биты = с.случайные_биты(1000)
        self.assertTrue(np.array_equal(1 - биты, снять_вручную(биты, "инверсия")[0]))
        self.assertTrue(np.array_equal(биты[5:], снять_вручную(биты, "сдвиг 5")[0]))
        d = с.случайные_биты(20_000)
        манчестер = np.column_stack([1 - d, d]).reshape(-1)
        self.assertTrue(np.array_equal(d, снять_вручную(манчестер, "манчестер")[0]))

    def test_непонятное_указание(self):
        with self.assertRaises(ValueError):
            снять_вручную(с.случайные_биты(1000), "расшифровать всё")


class КартаTests(unittest.TestCase):
    def test_участки_и_сигнатуры_и_zlib(self):
        import zlib
        текст = ("Проверка связи. " * 3000).encode()
        данные = (bytes(20_000) + np.random.default_rng(1).bytes(60_000) + текст
                  + zlib.compress(текст))
        найдено = karta.описать(данные)
        подробно = " ".join(найдено.подробно)
        self.assertIn("заполнение 0x00", подробно)
        self.assertIn("похоже на случайный", подробно)
        self.assertIn("упорядоченный", подробно)
        self.assertIn("распаковались куски zlib", подробно)

    def test_колебание_около_порога_не_дробит(self):
        # SLIP с IP — энтропия около 7,5: без слияния карта дробилась на десятки
        # участков «случайный/смешанный».
        участки = karta.участки(с.slip(с.пакеты_ip(400)))
        self.assertLessEqual(len(участки), 2)

    def test_pcap(self):
        пакеты = с.пакеты_ip(30)
        данные = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101) + b"".join(
            struct.pack("<IIII", 0, 0, len(п), len(п)) + п for п in пакеты)
        канал, прочитано = karta.pcap(данные)
        self.assertEqual(("IP", пакеты), (канал, прочитано))
        разбор = разобрать(данные=данные, имя="захват.pcap")
        self.assertEqual("сетевой", разбор.находки[0].уровень)
        self.assertIn("30 из 30", разбор.находки[0].мера)


class ЗаданияTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp())
        self.задания = Задания(self.папка)

    def дождаться(self, ид):
        for _ in range(600):
            состояние = self.задания.прочитать(ид)
            if состояние["состояние"] not in ("ждёт", "идёт"):
                return состояние
            time.sleep(0.2)
        self.fail("задание не закончилось")

    def test_этапы_выгрузки_и_продолжение(self):
        ид = self.задания.создать(владелец=1, имя="запись.bin", данные=цепочка(),
                                  профиль="быстро")
        состояние = self.дождаться(ид)
        self.assertEqual("готово", состояние["состояние"], состояние.get("ошибка"))
        self.assertEqual(["bin", "bin", "sig", "pcap"],
                         [э.get("выгрузка") for э in состояние["этапы"]])
        self.assertTrue(any("пробую" in строка for строка in состояние["журнал"]))
        # Выгрузка кадров — в формате .Sig: два байта длины и кадр.
        sig = self.задания.файл_этапа(ид, 3).read_bytes()
        длина = struct.unpack(">H", sig[:2])[0]
        self.assertGreater(длина, 20)
        # pcap с сырыми IP открывается как захват.
        канал, пакеты = karta.pcap(self.задания.файл_этапа(ид, 4).read_bytes())
        self.assertEqual(("IP", 110), (канал, len(пакеты)))
        # Продолжение с этапа 1 со снятием скремблера вручную.
        ид2 = self.задания.продолжить(ид, 1, владелец=1, снять=["скремблер 3,20"],
                                      профиль="быстро")
        продолжение = self.дождаться(ид2)
        self.assertEqual(["вручную", "канальный", "сетевой"],
                         [э["уровень"] for э in продолжение["этапы"]])
        self.assertEqual(f"{ид}#1", продолжение["от"])

    def test_продолжать_не_с_чего(self):
        ид = self.задания.создать(владелец=1, имя="захват.sig", данные=с.sig(с.пакеты_ip(20)),
                                  профиль="быстро")
        self.дождаться(ид)
        with self.assertRaises(ValueError):
            self.задания.продолжить(ид, 1, владелец=1)

    def test_выгрузка_по_виду(self):
        self.assertEqual("bin", выгрузка(np.array([1, 0, 1], np.uint8), "биты")[1])
        self.assertIsNone(выгрузка(None, "биты"))


class СтраницаTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def загрузить(self, данные=None, профиль="быстро", слои=""):
        return self.сеть.client.post(
            "/api/potok", data={"profile": профиль, "strip": слои},
            files={"file": ("запись.bin", данные or цепочка(), "application/octet-stream")})

    def дождаться(self, ид):
        for _ in range(600):
            состояние = self.сеть.client.get(f"/api/potok/{ид}").json()
            if состояние["состояние"] not in ("ждёт", "идёт"):
                return состояние
            time.sleep(0.2)
        self.fail("задание не закончилось")

    def test_весь_путь_через_сервер(self):
        self.сеть.login("engineer")
        ответ = self.загрузить()
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид = ответ.json()["id"]
        состояние = self.дождаться(ид)
        self.assertEqual(4, len(состояние["этапы"]))
        файл = self.сеть.client.get(f"/api/potok/{ид}/stage/4")
        self.assertEqual(200, файл.status_code)
        from urllib.parse import unquote
        self.assertIn("запись-этап-4.pcap", unquote(файл.headers.get("content-disposition", "")))
        продолжение = self.сеть.client.post(f"/api/potok/{ид}/continue",
                                             json={"stage": 1, "strip": "скремблер 3,20",
                                                   "profile": "быстро"})
        self.assertEqual(200, продолжение.status_code, продолжение.text)
        список = self.сеть.client.get("/api/potok").json()["items"]
        self.assertEqual(2, len(список))

    def test_чужое_задание_не_видно_и_профиль_проверяется(self):
        self.сеть.login("engineer")
        ид = self.загрузить().json()["id"]
        self.assertEqual(400, self.загрузить(профиль="всё").status_code)
        self.сеть.login("zam")        # другой военнослужащий, не администратор
        self.assertEqual(404, self.сеть.client.get(f"/api/potok/{ид}").status_code)
        self.assertEqual(404, self.сеть.client.get("/api/potok/../etc").status_code)
        self.assertEqual([], self.сеть.client.get("/api/potok").json()["items"])

    def test_страница_в_интерфейсе(self):
        js = (КОРЕНЬ / "src" / "reportgen" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("route: 'potok', href: '#/potok', title: 'Разбор потока'", js)
        self.assertIn("else if (route.name === 'potok') await renderPotok(view, route.id);", js)
        self.assertIn("остановитьОпросПотока();", js)
        self.assertIn("'/api/potok/' + encodeURIComponent(jobId) + '/continue'", js)


if __name__ == "__main__":
    unittest.main()
