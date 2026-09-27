"""«Разбирать как»: выбор аналитика важнее таблицы портов, но мусор протоколом не делает."""

import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.prilozh import проверить_как
from reportgen.setevoy.zahvaty import Захваты

DNS_НА_5000 = с.eth(с.ip(с.udp(с.dns_запрос("kak.example"), 40000, 5000), 17))
HTTP_НА_8888 = с.eth(с.ip(с.tcp(b"GET /x HTTP/1.1\r\nHost: h\r\n\r\n", 40000, 8888), 6))


class РазбиратьКакTests(unittest.TestCase):
    def test_без_правил_и_с_правилом(self):
        self.assertEqual("UDP", разобрать_пакет(DNS_НА_5000).протокол)
        п = разобрать_пакет(DNS_НА_5000, как={"udp:5000": "DNS"})
        self.assertEqual("DNS", п.протокол)
        self.assertEqual([], п.ошибки)
        # Правило на другой транспорт или порт не действует.
        self.assertEqual("UDP", разобрать_пакет(DNS_НА_5000, как={"tcp:5000": "DNS", "udp:5001": "DNS"}).протокол)

    def test_правило_раньше_таблицы_и_угадывания(self):
        self.assertEqual("HTTP", разобрать_пакет(HTTP_НА_8888).протокол)          # угадан по тексту
        п = разобрать_пакет(HTTP_НА_8888, как={"tcp:8888": "данные"})
        self.assertEqual(["Ethernet", "IPv4", "TCP", "Данные"], п.стек)
        днс = с.eth(с.ip(с.udp(с.dns_запрос("a.b"), 40000, 53), 17))
        self.assertEqual("Данные", разобрать_пакет(днс, как={"udp:53": "данные"}).стек[-1])

    def test_не_подошло_видно_и_разбор_дальше(self):
        п = разобрать_пакет(HTTP_НА_8888, как={"tcp:8888": "BGP"})
        self.assertEqual(["разбирать как BGP (порт 8888): данные не подошли"], п.ошибки)
        self.assertEqual("HTTP", п.протокол)                                     # угадывание после отказа

    def test_проверка_правил(self):
        self.assertEqual({"udp:5000": "DNS", "tcp:80": "данные"},
                         проверить_как({"UDP:05000": "DNS", " tcp:80 ": "данные"}))
        for плохие in ({"udp:0": "DNS"}, {"udp:70000": "DNS"}, {"sctp:5": "DNS"}, {"udp:5": "HTTP"},
                       {"tcp:5": "нет такого"}, ["udp:5"], {f"udp:{i}": "DNS" for i in range(1, 66)}):
            with self.subTest(плохие=плохие), self.assertRaises(ValueError):
                проверить_как(плохие)


class ХранилищеКакTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.захваты = Захваты(Path(self._tmp.name))

    def дождаться(self, ид):
        for _ in range(200):
            if self.захваты.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                return
            time.sleep(0.05)
        self.fail("захват не разобрался")

    def test_пересборка_с_правилом(self):
        ид = self.захваты.создать(владелец=1, имя="k.pcap", данные=с.pcap([DNS_НА_5000, HTTP_НА_8888]))
        self.дождаться(ид)
        self.assertEqual([], self.захваты.отобрать(ид, "dns"))
        self.assertEqual({"udp:5000": "DNS"}, self.захваты.разбирать_как(ид, {"udp:5000": "DNS"}))
        self.дождаться(ид)
        self.assertEqual([0], self.захваты.отобрать(ид, "dns"))
        self.assertEqual("DNS", self.захваты.сводки(ид)[0]["протокол"])
        self.assertEqual("DNS", self.захваты.пакет(ид, 1)["уровни"][-1]["протокол"])
        self.assertEqual({"udp:5000": "DNS"}, self.захваты.прочитать(ид)["как"])
        with self.assertRaises(ValueError):
            self.захваты.разбирать_как(ид, {"udp:5000": "HTTP"})
        # Правила снимаются пустым словарём.
        self.захваты.разбирать_как(ид, {})
        self.дождаться(ид)
        self.assertEqual([], self.захваты.отобрать(ид, "dns"))

    def test_пока_разбирается_нельзя(self):
        ид = self.захваты.создать(владелец=1, имя="k.pcap", данные=с.pcap([DNS_НА_5000]))
        self.дождаться(ид)
        состояние = self.захваты.прочитать(ид)
        состояние["состояние"] = "идёт"
        self.захваты._записать(ид, состояние)
        with self.assertRaises(ValueError):
            self.захваты.разбирать_как(ид, {"udp:5000": "DNS"})


class КакЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def test_весь_путь(self):
        к = self.сеть.client
        self.сеть.login("engineer")
        протоколы = к.get("/api/pakety-protocols").json()
        self.assertIn("DNS", протоколы["udp"])
        self.assertIn("HTTP", протоколы["tcp"])
        self.assertNotIn("HTTP", протоколы["udp"])
        ид = к.post("/api/pakety", files={"file": ("k.pcap", с.pcap([DNS_НА_5000]), "application/octet-stream")}).json()["id"]
        for _ in range(200):
            if к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово":
                break
            time.sleep(0.05)
        ответ = к.post(f"/api/pakety/{ид}/decode-as", json={"rules": {"udp:5000": "DNS"}})
        self.assertEqual({"rules": {"udp:5000": "DNS"}}, ответ.json())
        for _ in range(200):
            if к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово":
                break
            time.sleep(0.05)
        self.assertEqual({"udp:5000": "DNS"}, к.get(f"/api/pakety/{ид}").json()["как"])
        self.assertEqual("DNS", к.get(f"/api/pakety/{ид}/list").json()["items"][0]["протокол"])
        self.assertEqual(400, к.post(f"/api/pakety/{ид}/decode-as", json={"rules": {"udp:1": "HTTP"}}).status_code)
        self.assertEqual(404, к.post("/api/pakety/20200101-000000-abcdef/decode-as", json={"rules": {}}).status_code)


if __name__ == "__main__":
    unittest.main()
