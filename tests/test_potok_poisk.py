# -*- coding: utf-8 -*-
"""Поиск в потоке: образец при любом сдвиге, сигнатуры файлов со сверкой структуры, строки, блоки."""

import gzip
import io
import struct
import unittest
import zipfile
import zlib

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import poisk

rng = np.random.default_rng(7)


def случайные(n):
    return bytes(rng.integers(0, 256, n, dtype=np.uint8))


def jpeg(n=400):
    return (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + bytes(9)
            + b"\xff\xdb" + struct.pack(">H", 67) + bytes(65)
            + b"\xff\xda" + struct.pack(">H", 8) + bytes(6) + bytes(rng.integers(0, 255, n, dtype=np.uint8)) + b"\xff\xd9")


def png():
    кусок = lambda т, д: struct.pack(">I", len(д)) + т + д + struct.pack(">I", zlib.crc32(т + д))
    return b"\x89PNG\r\n\x1a\n" + кусок(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) \
        + кусок(b"IDAT", zlib.compress(bytes(14))) + кусок(b"IEND", b"")


def docx():
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w") as z:
        z.writestr("word/document.xml", "<w>текст</w>")
    return буфер.getvalue()


def в_биты(данные, сдвиг=0, инверсия=False):
    биты = np.unpackbits(np.frombuffer(данные, dtype=np.uint8))
    биты = np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8), биты])
    return 1 - биты if инверсия else биты


class ОбразецTests(unittest.TestCase):
    def test_виды_образца(self):
        self.assertEqual(b"\xff\xd8\xff", poisk.образец("ff:d8 FF", "hex")[0])
        self.assertEqual("отчёт".encode("cp1251"), poisk.образец("отчёт", "текст", "cp1251")[0])
        self.assertEqual("ab".encode("utf-16-le"), poisk.образец("ab", "текст", "utf-16-le")[0])
        байты, биты = poisk.образец("0011011", "биты")
        self.assertIsNone(байты)
        self.assertEqual([0, 0, 1, 1, 0, 1, 1], биты.tolist())
        for плохое in (("fff", "hex"), ("zz", "hex"), ("", "текст"), ("012", "биты"), ("x", "вид")):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                poisk.образец(*плохое)

    def test_любой_сдвиг_и_инверсия(self):
        данные = случайные(3000) + b"MARKER!" + случайные(500) + b"MARKER!" + случайные(100)
        for сдвиг in (0, 3, 7):
            for инверсия in (False, True):
                with self.subTest(сдвиг=сдвиг, инверсия=инверсия):
                    биты = в_биты(данные, сдвиг, инверсия)
                    байты, б = poisk.образец("MARKER!", "текст")
                    найдено = poisk.найти_образец(биты, байты, б, инверсия=True)
                    self.assertEqual([сдвиг + 8 * 3000, сдвиг + 8 * 3507], [н["бит"] for н in найдено])
                    self.assertEqual({инверсия}, {н["инверсия"] for н in найдено})
                    к = poisk.контекст(биты, найдено[0]["бит"], инверсия)
                    self.assertTrue(к["после"].startswith("4d 41 52 4b 45 52 21"))
        # Без «любого сдвига» — только по границе байта.
        биты = в_биты(данные, 3)
        байты, б = poisk.образец("MARKER!", "текст")
        self.assertEqual([], poisk.найти_образец(биты, байты, б, любой_сдвиг=False))

    def test_битовый_образец(self):
        биты = np.array([1, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0], dtype=np.uint8)
        байты, б = poisk.образец("0011011", "биты")
        self.assertEqual([2], [н["бит"] for н in poisk.найти_образец(биты, байты, б)])


class СигнатурыTests(unittest.TestCase):
    def test_файлы_с_длиной_при_сдвиге_и_инверсии(self):
        файлы = [("JPEG", "jpg", jpeg()), ("PNG", "png", png()), ("ZIP", "docx", docx()),
                 ("GZIP", "gz", gzip.compress(b"hello " * 100))]
        данные, места = случайные(1000), []
        for имя, расширение, тело in файлы:
            места.append((имя, расширение, len(данные), len(тело)))
            данные += тело + случайные(300)
        for сдвиг, инверсия in ((0, False), (5, True)):
            with self.subTest(сдвиг=сдвиг, инверсия=инверсия):
                найдено = poisk.сигнатуры(в_биты(данные, сдвиг, инверсия), инверсия=True)
                ждём = [(и, р, сдвиг + 8 * м, дл, инверсия) for и, р, м, дл in места]
                self.assertEqual(ждём, [(н["что"], н["расширение"], н["бит"], н["длина"], н["инверсия"])
                                        for н in найдено if н["что"] in ("JPEG", "PNG", "ZIP", "GZIP")])
                биты = в_биты(данные, сдвиг, инверсия)
                jpeg_ = next(н for н in найдено if н["что"] == "JPEG")
                self.assertEqual(файлы[0][2], poisk.вырезать(биты, jpeg_["бит"], jpeg_["длина"], инверсия))

    def test_случайный_поток_файлов_не_даёт(self):
        # 2 МБ случайных байт: подписи FF D8 FF, PK.., BM встречаются, а структура — нет.
        найдено = poisk.сигнатуры(в_биты(случайные(2 << 20)), любой_сдвиг=False)
        self.assertEqual([], [н for н in найдено if н["что"] not in ("RIFF", "MP3 (ID3)")])

    def test_испорченный_png_не_png(self):
        плохой = bytearray(png())
        плохой[20] ^= 1                                 # CRC IHDR больше не сходится
        self.assertEqual([], [н for н in poisk.сигнатуры(в_биты(bytes(плохой)), любой_сдвиг=False)
                              if н["что"] == "PNG"])


class СтрокиTests(unittest.TestCase):
    def test_текст_имена_адреса(self):
        текст = "Передача файла отчёт_2024.pdf на сервер 10.20.30.40 и http://example.com/a".encode("cp1251")
        данные = случайные(20000) + текст + случайные(2000) + "проверка связи".encode("utf-8") \
            + случайные(500) + "Test string here".encode("utf-16-le") + случайные(500)
        найдено = poisk.строки(в_биты(данные, 3, True), наименьшая=10, сдвиги=range(8), инверсия=True)
        тексты = [с["текст"] for с in найдено["строки"]]
        # За текстом — случайные байты; печатный байт, прилипший к последнему слову,
        # от текста не отличить (здесь это «Q»), поэтому — начало строки.
        self.assertTrue(any(т.startswith("Передача файла отчёт_2024.pdf на сервер 10.20.30.40 и "
                                         "http://example.com/a") for т in тексты), тексты)
        self.assertIn("проверка связи", тексты)
        self.assertIn("Test string here", тексты)
        первая = next(с for с in найдено["строки"] if с["текст"].startswith("Передача"))
        self.assertEqual((3 + 8 * 20000, True, "CP1251"), (первая["бит"], первая["инверсия"], первая["кодировка"]))
        self.assertIn("отчёт_2024.pdf", [и["имя"] for и in найдено["имена_файлов"]])
        адреса = {а["адрес"] for а in найдено["адреса"]}
        self.assertIn("10.20.30.40", адреса)
        self.assertTrue(any(а.startswith("http://example.com/a") for а in адреса), адреса)

    def test_случайные_байты_почти_не_текст(self):
        найдено = poisk.строки(в_биты(случайные(500_000)), наименьшая=10, сдвиги=range(8), инверсия=True)
        self.assertLessEqual(len(найдено["строки"]), 8, [с["текст"] for с in найдено["строки"]])

    def test_похоже_на_текст(self):
        for да in ("Передача файла отчёт", "взгляд на мир", "GET /index.html HTTP/1.1", "Hello, world!"):
            self.assertTrue(poisk.похоже_на_текст(да), да)
        for нет in ("яяяяяяяяяя", "Лфйщи4043>", "XRAEX)r;FM", "ЯОПСНПСМПСЛП}", "проверка }{x ]]q ^^w ~~z"):
            self.assertFalse(poisk.похоже_на_текст(нет), нет)


class ЧастыеTests(unittest.TestCase):
    def test_блок_заголовка_и_шаг(self):
        кадры = b"".join(b"\xaa\x55HDR\x01" + i.to_bytes(2, "big") + случайные(56) for i in range(500))
        лучший = poisk.частые(в_биты(кадры))[0]
        self.assertEqual(("aa 55 48 44 52 01", 500, 64, 1.0), (лучший["hex"], лучший["раз"], лучший["шаг"],
                                                               лучший["доля_шага"]))
        self.assertEqual([], poisk.частые(в_биты(случайные(100_000))))

    def test_блок_дорастает_в_обе_стороны(self):
        # Постоянная часть 12 байт — длиннее самой длинной комбинации (8): блок
        # должен дорасти до неё целиком, влево и вправо от ядра.
        заголовок = bytes.fromhex("1acffc1d0a0b0c0d0e0f1011")
        кадры = b"".join(случайные(3) + заголовок + случайные(49) for _ in range(400))
        лучший = poisk.частые(в_биты(кадры))[0]
        self.assertEqual((заголовок.hex(" "), 12, 400), (лучший["hex"], лучший["байт"], лучший["раз"]))

    def test_jpeg_проверяет_маркер(self):
        # FF D8 FF 00 — не JPEG, даже если дальше «сегменты» складываются.
        поддельный = b"\xff\xd8\xff\x00\x00\x04\x00\x00\xff\xda\x00\x04\x00\x00" + случайные(50) + b"\xff\xd9"
        self.assertEqual([], [н for н in poisk.сигнатуры(в_биты(поддельный), любой_сдвиг=False) if н["что"] == "JPEG"])


if __name__ == "__main__":
    unittest.main()


class ПоискЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def test_поиск_файлы_строки_блоки_вырезка(self):
        import time
        к = self.сеть.client
        self.сеть.login("engineer")
        тело = jpeg()
        данные = случайные(4000) + тело + "Файл снимок_01.jpg передан".encode("cp1251") + случайные(4000)
        поток = bytes(np.packbits(в_биты(данные, 2)))
        ид = к.post("/api/potok", data={"profile": "быстро"},
                    files={"file": ("s.bin", поток, "application/octet-stream")}).json()["id"]
        for _ in range(600):
            состояние = к.get(f"/api/potok/{ид}").json()
            if состояние["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.2)
        # Автомат сам нашёл файл и текст.
        self.assertTrue(any("JPEG (.jpg) с бита 32002" in с for с in состояние["содержимое"]), состояние["содержимое"])
        найдено = к.post(f"/api/potok/{ид}/search", json={"stage": 0, "pattern": "снимок", "kind": "текст",
                                                          "encoding": "cp1251"}).json()
        self.assertEqual(1, найдено["найдено"])
        self.assertEqual(2, найдено["items"][0]["сдвиг"])
        файлы = к.post(f"/api/potok/{ид}/files", json={"stage": 0}).json()["items"]
        ф = next(ф for ф in файлы if ф["что"] == "JPEG")
        вырезано = к.get(f"/api/potok/{ид}/carve?stage=0&bit={ф['бит']}&length={ф['длина']}&ext=jpg")
        self.assertEqual(тело, вырезано.content)
        строки = к.post(f"/api/potok/{ид}/strings", json={"stage": 0, "anyshift": True, "min": 8}).json()
        self.assertIn("снимок_01.jpg", [и["имя"] for и in строки["имена_файлов"]])
        self.assertEqual(200, к.post(f"/api/potok/{ид}/ngrams", json={"stage": 0}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/search", json={"stage": 0, "pattern": "zz",
                                                                     "kind": "hex"}).status_code)
        self.сеть.login("gruppa")
        self.assertEqual(404, к.post(f"/api/potok/{ид}/files", json={"stage": 0}).status_code)
