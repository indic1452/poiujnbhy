"""«Разбирать как»: выбор аналитика важнее таблицы портов, но мусор протоколом не делает."""

import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import dekodirovat_kak as дк
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


def _кадр_udp(нагрузка, порт_к=5000):
    return с.eth(с.ip(с.udp(нагрузка, 40000, порт_к), 17))


def _стек(кадр, *правила):
    return разобрать_пакет(кадр, как={"правила": [дк.проверить_правило(п) for п in правила]})


#: ARP и 16 байт за ним: разборщик ARP вложенного не начинает — «после ARP» видит хвост сам.
ARP_С_ХВОСТОМ = (bytes(6) + b"\x02" * 6 + b"\x08\x06" + b"\x00\x01\x08\x00\x06\x04\x00\x01" + b"\x02" * 6
                 + bytes([10, 0, 0, 1]) + bytes(6) + bytes([10, 0, 0, 2]) + b"\xde\xad\xbe\xef" * 4)


class СтекВручнуюTests(unittest.TestCase):
    """Ручной стек: «после уровня X — D» (с условием на поле или без), сдвиг, кадр с N-го байта."""

    def test_после_уровня_без_поля(self):
        п = _стек(DNS_НА_5000, {"вид": "поле", "над": "UDP", "как": "DNS", "ид": "0000000a"})
        self.assertEqual(["Ethernet", "IPv4", "UDP", "DNS"], п.стек)
        self.assertEqual(["0000000a"], п.правила)
        # Правило другого уровня молчит; выключенное — тоже.
        self.assertEqual("Данные", _стек(DNS_НА_5000, {"вид": "поле", "над": "TCP", "как": "DNS"}).стек[-1])
        self.assertEqual("Данные", _стек(DNS_НА_5000, {"вид": "поле", "над": "UDP", "как": "DNS", "вкл": False}).стек[-1])

    def test_после_уровня_со_сдвигом(self):
        кадр = _кадр_udp(b"\x01\x02\x03\x04" + с.dns_запрос("kak.example"))
        п = _стек(кадр, {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": 4})
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные", "DNS"], п.стек)
        self.assertEqual((42, 4), (п.уровни[3].смещение, п.уровни[3].длина))
        self.assertEqual(46, п.уровни[4].смещение)
        # С условием на поле и сдвигом сразу; сдвиг за конец вложенного — только данные.
        п = _стек(кадр, {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "DNS", "сдвиг": 4})
        self.assertEqual("DNS", п.стек[-1])
        п = _стек(кадр, {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": 5000})
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Данные"], п.стек)

    def test_хвост_после_уровня_без_вложенного(self):
        self.assertEqual(["Ethernet", "ARP"], разобрать_пакет(ARP_С_ХВОСТОМ).стек)
        п = _стек(ARP_С_ХВОСТОМ, {"вид": "поле", "над": "ARP", "как": "данные"})
        self.assertEqual(["Ethernet", "ARP", "Данные"], п.стек)
        self.assertEqual((42, 16), (п.уровни[-1].смещение, п.уровни[-1].длина))
        # Хвоста нет — правило не срабатывает и не мешает.
        п = _стек(ARP_С_ХВОСТОМ[:42], {"вид": "поле", "над": "ARP", "как": "данные"})
        self.assertEqual(["Ethernet", "ARP"], п.стек)
        self.assertFalse(hasattr(п, "правила"))

    def test_кадр_со_смещения(self):
        п = _стек(b"\xaa\xbb\xcc\xdd" + DNS_НА_5000, {"вид": "смещение", "сдвиг": 4, "как": "Ethernet"})
        self.assertEqual(["Данные", "Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual((0, 4), (п.уровни[0].смещение, п.уровни[0].длина))
        п = _стек(DNS_НА_5000[14:], {"вид": "смещение", "как": "IPv4"})
        self.assertEqual(["IPv4", "UDP", "Данные"], п.стек, "кадр целиком другим разборщиком")
        п = _стек(DNS_НА_5000[:20], {"вид": "смещение", "сдвиг": 3, "как": "IPv6"})
        self.assertEqual(["Данные", "Данные"], п.стек, "17 байт — не IPv6: данными, с ошибкой")
        self.assertTrue(any("IPv6" in о for о in п.ошибки), п.ошибки)

    def test_вместе_с_прежними_правилами(self):
        """Свой заголовок перед кадром и служба на чужом порту — два правила одной записи."""
        п = _стек(b"\xaa\xbb" + DNS_НА_5000, {"вид": "смещение", "сдвиг": 2, "как": "Ethernet"},
                  {"вид": "поле", "над": "UDP", "как": "DNS"})
        self.assertEqual(["Данные", "Ethernet", "IPv4", "UDP", "DNS"], п.стек)
        self.assertEqual(2, len(п.правила))
        # Ошибочный уровень убран — дальше данные без разбора, правило «после» его уже не видит.
        п = _стек(DNS_НА_5000, {"вид": "протокол", "протокол": "UDP", "действие": "данные"},
                  {"вид": "поле", "над": "UDP", "как": "DNS"})
        self.assertEqual(["Ethernet", "IPv4", "Данные"], п.стек)

    def test_проверка_входа(self):
        п = дк.проверить_правило({"вид": "поле", "над": "UDP", "как": "DNS", "значение": "мусор"})
        self.assertEqual(("", ""), (п["поле"], п["значение"]), "без поля значение не хранится")
        self.assertNotIn("сдвиг", п, "нулевой сдвиг не хранится — прежние правила не меняются")
        self.assertEqual(16, дк.проверить_правило({"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": "0x10"})["сдвиг"])
        self.assertEqual(0, дк.проверить_правило({"вид": "смещение", "как": "IPv4"})["сдвиг"])
        плохие = [
            {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": -1},
            {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": дк.СДВИГ_ДО + 1},
            {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": True},
            {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": "4 байта"},
            {"вид": "поле", "над": "UDP"},
            {"вид": "поле", "над": "", "как": "DNS"},
            {"вид": "поле", "над": "UDP", "поле": "__import__('os')", "значение": 1, "как": "DNS"},
            {"вид": "смещение", "сдвиг": 4},
            {"вид": "смещение", "сдвиг": 4, "как": "eval"},
            {"вид": "смещение", "сдвиг": [4], "как": "IPv4"},
        ]
        for плохое in плохие:
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                дк.проверить_правило(плохое)

    def test_описание_и_замена_на_том_же_месте(self):
        о = [дк.описать(дк.проверить_правило(п)) for п in (
            {"вид": "поле", "над": "UDP", "как": "DNS"},
            {"вид": "поле", "над": "UDP", "как": "DNS", "сдвиг": 4},
            {"вид": "поле", "над": "UDP", "поле": "udp.port", "значение": 5000, "как": "данные", "сдвиг": 2},
            {"вид": "смещение", "как": "IPv4"},
            {"вид": "смещение", "сдвиг": 4, "как": "Ethernet"})]
        self.assertEqual(["после UDP → DNS", "после UDP → DNS (первые 4 байт — данные)",
                          "UDP: udp.port = 5000 → данные (не разбирать) (первые 2 байт — данные)",
                          "кадр целиком → IPv4", "кадр с 4-го байта → Ethernet (первые 4 байт — данные)"], о)
        with tempfile.TemporaryDirectory() as т:
            люди = дк.ПравилаЛюдей(Path(т))
            люди.добавить(1, {"вид": "смещение", "сдвиг": 4, "как": "Ethernet"})
            люди.добавить(1, {"вид": "поле", "над": "UDP", "как": "DNS"})
            люди.добавить(1, {"вид": "поле", "над": "UDP", "как": "SIP"})
            правила = люди.добавить(1, {"вид": "смещение", "сдвиг": 8, "как": "Ethernet"})
            self.assertEqual([("поле", "SIP"), ("смещение", "Ethernet")], [(п["вид"], п["как"]) for п in правила],
                             "одно «после UDP» и одно смещение — новое заменяет прежнее")
            self.assertEqual(8, правила[-1]["сдвиг"])
            self.assertEqual([], люди.прочитать(2), "у каждого свои правила")


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

    def test_пока_загружается_нельзя_а_пока_разбирается_можно(self):
        """Разобрать заново можно и посреди разбора (прежний останавливается), но не посреди загрузки."""
        данные = с.pcap([DNS_НА_5000] * 3)
        ид = self.захваты.создать(владелец=1, имя="k.pcap", загрузка=len(данные))
        self.захваты.дописать(ид, 0, данные[:30])
        with self.assertRaises(ValueError):
            self.захваты.разбирать_как(ид, {"udp:5000": "DNS"})
        self.захваты.дописать(ид, 30, данные[30:])
        self.захваты.закончить_загрузку(ид)
        self.дождаться(ид)
        self.assertEqual([], self.захваты.отобрать(ид, "dns"))
        self.захваты.разбирать_как(ид, {"udp:5000": "DNS"})
        self.дождаться(ид)
        self.assertEqual([0, 1, 2], self.захваты.отобрать(ид, "dns"))


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

    def test_ручной_стек_к_записи_целиком(self):
        к = self.сеть.client
        self.сеть.login("engineer")
        ид = к.post("/api/pakety", files={"file": ("s.pcap", с.pcap([DNS_НА_5000, b"\xaa\xbb" + DNS_НА_5000]),
                                                     "application/octet-stream")}).json()["id"]

        def дождаться():
            for _ in range(200):
                if к.get(f"/api/pakety/{ид}").json()["состояние"] == "готово":
                    return
                time.sleep(0.05)
            self.fail("запись не разобралась")

        дождаться()
        второй = к.get(f"/api/pakety/{ид}/list").json()["items"][1]["протокол"]
        self.assertEqual(400, к.post("/api/dekodirovat-kak", json={"rule": {"вид": "смещение", "сдвиг": -2,
                                                                            "как": "Ethernet"}}).status_code)
        self.assertEqual(400, к.post("/api/dekodirovat-kak", json={"rule": {"вид": "поле", "над": "UDP",
                                                                            "как": "os.system"}}).status_code)
        правила = к.post("/api/dekodirovat-kak", json={"rule": {"вид": "поле", "над": "UDP", "как": "DNS"}}).json()["items"]
        self.assertEqual(["после UDP → DNS"], [п["описание"] for п in правила])
        self.assertEqual(200, к.post(f"/api/pakety/{ид}/decode-rules", json={}).status_code)
        дождаться()
        self.assertEqual(["DNS", второй], [п["протокол"] for п in к.get(f"/api/pakety/{ид}/list").json()["items"]])
        self.assertEqual([правила[0]["ид"]], [п["ид"] for п in к.get(f"/api/pakety/{ид}").json()["как"]["правила"]])
        # Выключить — правило хранится, к записи применяется без него.
        к.patch(f"/api/dekodirovat-kak/{правила[0]['ид']}", json={"вкл": False})
        к.post(f"/api/pakety/{ид}/decode-rules", json={})
        дождаться()
        self.assertEqual(["UDP", второй], [п["протокол"] for п in к.get(f"/api/pakety/{ид}/list").json()["items"]])
        self.assertEqual([], к.get(f"/api/pakety/{ид}").json()["как"].get("правила", []))


if __name__ == "__main__":
    unittest.main()
