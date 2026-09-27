"""Файлы внутри потоков TCP/UDP захвата: сборка по номерам, сигнатура, вырезание."""

import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import statistika
from reportgen.setevoy.zahvaty import Захваты
from test_potok_poisk import jpeg


class ФайлыВПотоках(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.захваты = Захваты(Path(self._tmp.name))

    def захват(self, пакеты):
        ид = self.захваты.создать(владелец=1, имя="f.pcap", данные=с.pcap(пакеты))
        for _ in range(200):
            if self.захваты.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.05)
        return self.захваты.сводки(ид), self.захваты.нагрузки(ид)

    def test_jpeg_в_ответе_http_по_кускам_с_повтором(self):
        картинка = jpeg(3000)
        тело = b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n\r\n" + картинка
        куски = [тело[i:i + 1000] for i in range(0, len(тело), 1000)]
        кл, сер = "10.0.0.1", "10.0.0.2"
        пакеты = [с.eth(с.ip(с.tcp(b"GET /a.jpg HTTP/1.1\r\n\r\n", 40000, 80, seq=1, src=кл, dst=сер),
                             6, src=кл, dst=сер))]
        порядок = [0, 1, 1, 2] + list(range(3, len(куски)))       # второй кусок повторён
        for i in порядок:
            пакеты.append(с.eth(с.ip(с.tcp(куски[i], 80, 40000, seq=7000 + 1000 * i, src=сер, dst=кл),
                                     6, src=сер, dst=кл)))
        сводки, нагрузки = self.захват(пакеты)
        файлы = statistika.файлы(сводки, нагрузки)
        self.assertEqual(["JPEG"], [ф["что"] for ф in файлы])
        ф = файлы[0]
        self.assertEqual(("TCP 10.0.0.2:80 → 10.0.0.1:40000", len(тело) - len(картинка), len(картинка), 2),
                         (ф["поток"], ф["смещение"], ф["длина"], ф["пакет"]))
        вырезано = statistika.вырезать_из_потока(сводки, нагрузки, ф["поток"], ф["смещение"], ф["длина"])
        self.assertEqual(картинка, вырезано)

    def test_первый_пакет_файла_в_середине_потока(self):
        картинка = jpeg(500)
        пакеты = [с.eth(с.ip(с.udp(b"x" * 300, 5000, 6000), 17)),
                  с.eth(с.ip(с.udp(b"y" * 100 + картинка[:200], 5000, 6000), 17)),
                  с.eth(с.ip(с.udp(картинка[200:], 5000, 6000), 17))]
        файлы = statistika.файлы(*self.захват(пакеты))
        self.assertEqual([(400, 2)], [(ф["смещение"], ф["пакет"]) for ф in файлы])

    def test_длина_неизвестна_до_предела(self):
        from unittest import mock

        from reportgen.potok import poisk
        сводки, нагрузки = self.захват([с.eth(с.ip(с.udp(bytes(range(200)), 5000, 6000), 17))])
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 100):
            self.assertEqual(bytes(range(10, 110)),
                             statistika.вырезать_из_потока(сводки, нагрузки, "UDP 10.0.0.1:5000 → 10.0.0.2:6000", 10, 0))
            self.assertEqual(100, len(statistika.вырезать_из_потока(
                сводки, нагрузки, "UDP 10.0.0.1:5000 → 10.0.0.2:6000", 0, 150)))

    def test_нет_файлов_и_неизвестный_поток(self):
        сводки, нагрузки = self.захват([с.eth(с.ip(с.udp(b"hello world" * 10, 5000, 6000), 17))])
        self.assertEqual([], statistika.файлы(сводки, нагрузки))
        with self.assertRaises(ValueError):
            statistika.вырезать_из_потока(сводки, нагрузки, "TCP 1:1 → 2:2", 0, 10)


if __name__ == "__main__":
    unittest.main()


class ФайлыЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def test_файлы_проверка_фильтра_и_скачивание(self):
        к = self.сеть.client
        к.cookies.clear()
        self.assertEqual(401, к.get("/api/pakety-filter?text=tcp").status_code)
        self.сеть.login("engineer")
        self.assertEqual({"ok": True, "error": ""}, к.get("/api/pakety-filter?text=tcp").json())
        плохой = к.get("/api/pakety-filter", params={"text": "(tcp"}).json()
        self.assertFalse(плохой["ok"])
        self.assertTrue(плохой["error"])
        картинка = jpeg(800)
        пакеты = [с.eth(с.ip(с.udp(b"hdr:" + картинка[:500], 5000, 6000), 17)),
                  с.eth(с.ip(с.udp(картинка[500:], 5000, 6000), 17))]
        ответ = к.post("/api/pakety", files={"file": ("f.pcap", с.pcap(пакеты), "application/octet-stream")})
        ид = ответ.json()["id"]
        for _ in range(200):
            if к.get(f"/api/pakety/{ид}").json()["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.05)
        файлы = к.get(f"/api/pakety/{ид}/files").json()["items"]
        self.assertEqual([("JPEG", 4, 1)], [(ф["что"], ф["смещение"], ф["пакет"]) for ф in файлы])
        self.assertEqual([], к.get(f"/api/pakety/{ид}/files?filter=tcp").json()["items"])
        обзор = к.get(f"/api/pakety/{ид}/stats?kind=overview").json()
        self.assertEqual((2, 1), (обзор["пакетов"], обзор["файлов"]))
        self.assertEqual({"пакетов": 0}, к.get(f"/api/pakety/{ид}/stats?kind=overview&filter=tcp").json())
        ф = файлы[0]
        файл = к.get(f"/api/pakety/{ид}/file", params={"flow": ф["поток"], "offset": 4, "length": ф["длина"],
                                                        "ext": "jpg/../x"})
        self.assertEqual(картинка, файл.content)
        self.assertIn('filename="stream-4.jpgx"', файл.headers["content-disposition"])
        self.assertEqual(404, к.get(f"/api/pakety/{ид}/file", params={"flow": "нет", "offset": 0}).status_code)


class ОбзорTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.захваты = Захваты(Path(self._tmp.name))

    def test_главное_и_приметы(self):
        кл, сер = "10.0.0.1", "10.0.0.2"
        картинка = jpeg(300)
        пакеты = [
            с.eth(с.ip(с.udp(с.dns_запрос("mail.example"), 5000, 53), 17)),
            с.eth(с.ip(с.udp(с.dns_запрос("mail.example"), 5001, 53), 17)),
            с.eth(с.ip(с.udp(с.dns_ответ("mail.example", ["10.9.9.9"]), 53, 5000, src=сер, dst=кл), 17, src=сер, dst=кл)),
            с.eth(с.ip(с.udp(с.dns_запрос("lost.example"), 5002, 53), 17)),
            с.eth(с.ip(с.tcp(b"GET / HTTP/1.1\r\nHost: web.local\r\n\r\n", 40000, 80), 6)),
            с.eth(с.ip(с.udp(b"img" + картинка, 7000, 7001), 17)),
        ] + [с.eth(с.ip(с.udp(bytes([i]) * 30, 9000, 9001), 17)) for i in range(3)]
        ид = self.захваты.создать(владелец=1, имя="o.pcap", данные=с.pcap(пакеты, времена=[i * 0.5 for i in range(9)]))
        for _ in range(200):
            if self.захваты.прочитать(ид)["состояние"] == "готово":
                break
            time.sleep(0.05)
        о = statistika.обзор(self.захваты.сводки(ид), self.захваты.поля(ид), self.захваты.нагрузки(ид))
        self.assertEqual((9, 4.0), (о["пакетов"], о["длительность"]))
        self.assertEqual(round(8 * о["байт"] / 4.0), о["скорость"])
        self.assertEqual({"протокол": "DNS", "пакетов": 4, "доля": round(4 / 9, 4)}, о["протоколы"][0])
        self.assertEqual([{"имя": "mail.example", "раз": 2}, {"имя": "lost.example", "раз": 1}], о["dns"])
        self.assertEqual([{"имя": "web.local", "раз": 1}], о["http"])
        self.assertEqual((1, "JPEG"), (о["файлов"], о["файлы"][0]["что"]))
        self.assertEqual([{"группа": "UDP порт 9000", "пакетов": 3}, {"группа": "UDP порт 7000", "пакетов": 1}],
                         о["неразобрано"])
        приметы = " | ".join(п["что"] for п in о["приметы"])
        self.assertIn("открытым текстом: HTTP ×1", приметы)
        self.assertIn("DNS-запросов без ответа: 2 из 3", приметы)   # mail с 5001 и lost
        self.assertIn("больше всего неразобранного: UDP порт 9000 (3 пакетов)", приметы)
        self.assertIn("в потоках найдено файлов: 1", приметы)
        self.assertEqual({"пакетов": 0}, statistika.обзор([], [], []))

    def test_тихий_захват_без_примет(self):
        пакеты = [с.eth(с.ip(с.udp(с.dns_запрос("a.example"), 5000, 53), 17)),
                  с.eth(с.ip(с.udp(с.dns_ответ("a.example", ["10.1.1.1"]), 53, 5000, src="10.0.0.2", dst="10.0.0.1"),
                             17, src="10.0.0.2", dst="10.0.0.1"))]
        ид = self.захваты.создать(владелец=1, имя="q.pcap", данные=с.pcap(пакеты, времена=[5.0, 5.0]))
        for _ in range(200):
            if self.захваты.прочитать(ид)["состояние"] == "готово":
                break
            time.sleep(0.05)
        о = statistika.обзор(self.захваты.сводки(ид), self.захваты.поля(ид), self.захваты.нагрузки(ид))
        self.assertEqual([], о["приметы"])
        self.assertEqual(0, о["скорость"])


class ОбзорСуммTests(unittest.TestCase):
    def обзор(self, пакеты):
        from reportgen.setevoy import разобрать_пакет
        from reportgen.setevoy.zahvaty import _сводка
        разобранные = [разобрать_пакет(п, номер=i + 1) for i, п in enumerate(пакеты)]
        сводки = [_сводка(п, п.поля_фильтра()) for п in разобранные]
        return statistika.обзор(сводки, [п.поля_фильтра() for п in разобранные], [п.нагрузка for п in разобранные])

    def test_суммы_tcp_у_большинства_и_у_меньшинства(self):
        верный = с.eth(с.ip(с.tcp(b"x", 40000, 5555), 6))
        # Сумма TCP посчитана для других адресов — как при захвате до сетевой карты.
        неверный = с.eth(с.ip(с.tcp(b"x", 40000, 5555, src="10.9.9.9"), 6))
        много = self.обзор([неверный, неверный, верный])
        self.assertTrue(any("передающем узле" in п["что"] for п in много["приметы"]))
        мало = self.обзор([неверный, верный, верный])
        self.assertFalse(any("передающем узле" in п["что"] for п in мало["приметы"]))

    def test_односторонний_захват_dns_не_примета(self):
        # Ответов нет вовсе — захват видел одну сторону; «без ответа» тут не признак.
        запросы = [с.eth(с.ip(с.udp(с.dns_запрос(f"h{i}.example", ид=i), 5000 + i, 53), 17)) for i in range(3)]
        self.assertFalse(any("без ответа" in п["что"] for п in self.обзор(запросы)["приметы"]))
