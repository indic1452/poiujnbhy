"""PPP: MP (RFC 1990), PAP, CHAP, EAP, BAP, CCP и опции LCP / IPCP — раскладка как у Wireshark packet-ppp.c."""

import struct
import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.zahvaty import Захваты

АДРЕС = b"\xff\x03"


def ppp(протокол, тело):
    return АДРЕС + struct.pack(">H", протокол) + тело


def cp(код, ид, тело=b""):
    return bytes([код, ид]) + struct.pack(">H", 4 + len(тело)) + тело


def опции(*пары):
    return b"".join(bytes([т, 2 + len(з)]) + з for т, з in пары)


def mp_длинный(b, e, номер, тело, класс=0):
    return ppp(0x003D, bytes([(0x80 if b else 0) | (0x40 if e else 0) | (класс << 2)]) + номер.to_bytes(3, "big") + тело)


def mp_короткий(b, e, номер, тело):
    return ppp(0x003D, struct.pack(">H", (0x8000 if b else 0) | (0x4000 if e else 0) | номер) + тело)


class LcpTests(unittest.TestCase):
    def test_опции_mp_и_аутентификации(self):
        тело = опции((1, struct.pack(">H", 1500)), (3, b"\xc2\x23\x81"), (5, b"\x12\x34\x56\x78"),
                     (17, struct.pack(">H", 1524)), (18, b""), (19, b"\x03\x00\x11\x22\x33\x44\x55"), (7, b""))
        п = разобрать_пакет(ppp(0xC021, cp(1, 3, тело)), "PPP-HDLC")
        self.assertEqual(["PPP", "LCP"], п.стек)
        тексты = [п_.текст for п_ in п.уровни[1].поля]
        for ожидается in ("MRU 1500", "аутентификация CHAP (MS-CHAP-2)", "Magic-Number 0x12345678",
                          "MRRU (MP) 1524", "короткие номера MP (SSNH)",
                          "Endpoint Discriminator (MP): MAC IEEE 802.1 00:11:22:33:44:55",
                          "сжатие поля протокола (PFC)"):
            self.assertIn(ожидается, тексты)
        self.assertEqual([False], п.поля_фильтра()["lcp.opt.ssnh"])      # в запросе — ещё не договорено

    def test_опции_на_границах(self):
        """Алгоритм — только у CHAP; MAC — только ровно 6 байт."""
        тело = опции((3, b"\xc2\x27\x05"), (19, b"\x03\x01\x02\x03\x04"), (19, b"\x02\x0a\x00\x00\x01"),
                     (19, b"\x02\x0a\x00\x00\x01\x02"))
        тексты = [п_.текст for п_ in разобрать_пакет(ppp(0xC021, cp(1, 3, тело)), "PPP-HDLC").уровни[1].поля]
        self.assertIn("аутентификация EAP", тексты)
        self.assertIn("Endpoint Discriminator (MP): MAC IEEE 802.1 01020304", тексты)
        self.assertIn("Endpoint Discriminator (MP): IP-адрес 10.0.0.1", тексты)
        self.assertIn("Endpoint Discriminator (MP): IP-адрес 0a00000102", тексты)

    def test_echo_и_protocol_reject(self):
        п = разобрать_пакет(ppp(0xC021, cp(9, 1, b"\xde\xad\xbe\xef")), "PPP-HDLC")
        self.assertEqual("LCP Echo-Request", п.инфо)
        self.assertEqual([0xDEADBEEF], п.поля_фильтра()["lcp.magic_number"])
        п = разобрать_пакет(ppp(0xC021, cp(8, 2, b"\x80\xfd" + cp(1, 1))), "PPP-HDLC")
        self.assertEqual("LCP Protocol-Reject CCP", п.инфо)
        п = разобрать_пакет(ppp(0xC021, cp(8, 2, b"\xc0\x23")), "PPP-HDLC")       # без самого пакета
        self.assertEqual("LCP Protocol-Reject PAP", п.инфо)
        п = разобрать_пакет(ppp(0xC021, cp(12, 3, b"\0\0\0\1!")), "PPP-HDLC")
        self.assertEqual("LCP Identification: «!»", п.инфо)
        п = разобрать_пакет(ppp(0xC021, cp(12, 3, b"\0\0\0\1" + "Привет".encode())), "PPP-HDLC")
        self.assertEqual("LCP Identification: «Привет»", п.инфо)
        п = разобрать_пакет(ppp(0xC021, cp(13, 4, b"\0\0\0\1" + struct.pack(">I", 3600))), "PPP-HDLC")
        self.assertEqual("LCP Time-Remaining, 3600 с", п.инфо)

    def test_ipcp_dns(self):
        тело = опции((3, bytes([10, 0, 0, 1])), (129, bytes([8, 8, 8, 8])), (131, bytes([1, 1, 1, 1])),
                     (2, b"\x00\x2d\x0f\x01"))
        п = разобрать_пакет(ppp(0x8021, cp(2, 1, тело)), "PPP-HDLC")
        тексты = [п_.текст for п_ in п.уровни[1].поля]
        self.assertIn("IP-адрес 10.0.0.1", тексты)
        self.assertIn("первичный DNS 8.8.8.8", тексты)
        self.assertIn("вторичный DNS 1.1.1.1", тексты)
        self.assertIn("IP-Compression-Protocol: Ван Якобсон", тексты)


class CcpTests(unittest.TestCase):
    def test_mppe_и_deflate(self):
        тело = опции((18, struct.pack(">I", 0x01000040)), (26, b"\x78\x00"))
        п = разобрать_пакет(ppp(0x80FD, cp(1, 5, тело)), "PPP-HDLC")
        self.assertEqual(["PPP", "CCP"], п.стек)
        тексты = [п_.текст for п_ in п.уровни[1].поля]
        self.assertIn("MPPE/MPPC (Microsoft): MPPE 128 бит, без состояния (0x01000040)", тексты)
        self.assertIn("Deflate: окно 32768 байт, метод 8", тексты)

    def test_reset_request(self):
        п = разобрать_пакет(ppp(0x80FD, cp(14, 9)), "PPP-HDLC")
        self.assertEqual("CCP Reset-Request", п.инфо)
        # Код 14 у LCP — не Reset-Request.
        self.assertEqual("LCP 14", разобрать_пакет(ppp(0xC021, cp(14, 9)), "PPP-HDLC").инфо)

    def test_bacp(self):
        п = разобрать_пакет(ppp(0xC02B, cp(1, 1, опции((1, b"\x00\x00\x00\x01")))), "PPP-HDLC")
        self.assertEqual("BACP Configure-Request", п.инфо)


class АутентификацияTests(unittest.TestCase):
    def test_pap(self):
        п = разобрать_пакет(ppp(0xC023, cp(1, 7, b"\x04user\x06secret")), "PPP-HDLC")
        self.assertEqual("PAP Authenticate-Request: «user», пароль «secret»", п.инфо)
        self.assertEqual(["user"], п.поля_фильтра()["pap.peer_id"])
        п = разобрать_пакет(ppp(0xC023, cp(3, 7, b"\x0bbad passwd")), "PPP-HDLC")
        self.assertEqual("PAP Authenticate-Nak: «bad passwd»", п.инфо)

    def test_chap(self):
        п = разобрать_пакет(ppp(0xC223, cp(1, 2, b"\x04\x01\x02\x03\x04server")), "PPP-HDLC")
        self.assertEqual("CHAP Challenge: «server», значение 01020304", п.инфо)
        п = разобрать_пакет(ppp(0xC223, cp(2, 2, b"\x10" + bytes(range(16)) + b"alice")), "PPP-HDLC")
        self.assertEqual(["alice"], п.поля_фильтра()["chap.name"])
        п = разобрать_пакет(ppp(0xC223, cp(3, 2, b"Welcome")), "PPP-HDLC")
        self.assertEqual("CHAP Success: «Welcome»", п.инфо)

    def test_chap_испорченный_размер(self):
        п = разобрать_пакет(ppp(0xC223, cp(1, 2, b"\x20\x01\x02")), "PPP-HDLC")
        self.assertEqual("CHAP Challenge (размер значения испорчен)", п.инфо)
        п = разобрать_пакет(ppp(0xC223, b"\x01\x02\x00\x03"), "PPP-HDLC")
        self.assertEqual("CHAP Challenge (длина меньше 4)", п.инфо)
        # Значение ровно до конца — годно; на байт длиннее — испорчено.
        п = разобрать_пакет(ppp(0xC223, cp(1, 2, b"\x03\x01\x02\x03")), "PPP-HDLC")
        self.assertEqual("CHAP Challenge: значение 010203", п.инфо)
        п = разобрать_пакет(ppp(0xC223, cp(1, 2, b"\x04\x01\x02\x03")), "PPP-HDLC")
        self.assertEqual("CHAP Challenge (размер значения испорчен)", п.инфо)

    def test_chap_хвост_за_длиной(self):
        """После сообщения CHAP в кадре лишние байты (FCS, заполнение) — имя по полю длины."""
        п = разобрать_пакет(ppp(0xC223, cp(1, 2, b"\x02\x01\x02server") + b"\xaa\xbb"), "PPP-HDLC")
        self.assertEqual(["server"], п.поля_фильтра()["chap.name"])

    def test_eap_по_ppp(self):
        п = разобрать_пакет(ppp(0xC227, cp(2, 1, b"\x01alice@example")), "PPP-HDLC")
        self.assertEqual(["PPP", "EAP"], п.стек)
        self.assertEqual(["alice@example"], п.поля_фильтра()["eap.identity"])

    def test_bap(self):
        п = разобрать_пакет(ppp(0xC02D, cp(2, 1, b"\x01")), "PPP-HDLC")
        self.assertEqual("BAP Call-Response, Request-Nak", п.инфо)
        п = разобрать_пакет(ppp(0xC02D, cp(8, 1, b"\x03")), "PPP-HDLC")
        self.assertEqual("BAP Call-Status-Response, Request-Full-Nak", п.инфо)
        п = разобрать_пакет(ppp(0xC02D, cp(7, 1, b"\x03")), "PPP-HDLC")
        self.assertEqual("BAP Call-Status-Indication", п.инфо)


class MpTests(unittest.TestCase):
    def test_целый_пакет_в_одном_фрагменте(self):
        датаграмма = с.ip(с.udp(b"hello", 1000, 2000), 17)
        п = разобрать_пакет(mp_длинный(True, True, 0x010203, b"\x21" + датаграмма), "PPP-HDLC")
        self.assertEqual(["PPP", "MP", "PPP", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual([0x010203], п.поля_фильтра()["mp.seq"])

    def test_фрагмент(self):
        п = разобрать_пакет(mp_длинный(True, False, 5, b"\x00\x21\x45"), "PPP-HDLC")
        self.assertEqual("MP фрагмент 5 [B]", п.инфо)
        self.assertEqual(["PPP", "MP", "Данные"], п.стек)
        п = разобрать_пакет(mp_длинный(False, False, 6, b"xyz", класс=3), "PPP-HDLC")
        self.assertEqual("MP фрагмент 6, класс 3", п.инфо)

    def test_короткий_по_зарезервированным_битам(self):
        """B E 0 0 и старшие биты номера 0x301 — у длинного заголовка были бы заняты зарезервированные биты."""
        п = разобрать_пакет(mp_короткий(True, True, 0x301, b"\x21" + с.ip(с.udp(b"x", 1, 2), 17)), "PPP-HDLC")
        self.assertEqual([0x301], п.поля_фильтра()["mp.sseq"])
        self.assertIn("UDP", п.стек)

    def test_короткий_по_правилу(self):
        кадр = mp_короткий(True, True, 0x010, b"\x21" + с.ip(с.udp(b"x", 1, 2), 17))
        self.assertNotIn("UDP", разобрать_пакет(кадр, "PPP-HDLC").стек)       # по умолчанию — длинный
        п = разобрать_пакет(кадр, "PPP-HDLC", как={"ppp:mp": "короткие"})
        self.assertEqual([0x010], п.поля_фильтра()["mp.sseq"])
        self.assertIn("UDP", п.стек)


class СборкаMpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.захваты = Захваты(Path(self._tmp.name))

    def разобрать(self, кадры):
        ид = self.захваты.создать(владелец=1, имя="mp.pcap", данные=с.pcap(кадры, канал=50))
        for _ in range(200):
            if self.захваты.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.05)
        self.assertEqual("готово", self.захваты.прочитать(ид)["состояние"], self.захваты.прочитать(ид))
        return ид

    def test_сборка_длинных_вперемешку_с_другим_классом(self):
        датаграмма = b"\x00\x21" + с.ip(с.udp(с.dns_ответ("mp.example", ["10.1.2.3"]), 53, 5000), 17)
        куски = [датаграмма[:20], датаграмма[20:50], датаграмма[50:]]
        кадры = [mp_длинный(True, False, 0xFFFFFE, куски[0]),
                 mp_длинный(True, True, 0, b"\x21" + с.ip(с.udp(b"z", 1, 2), 17), класс=1),
                 mp_длинный(False, True, 0, куски[2]),              # номер переходит через 2²⁴,
                 mp_длинный(False, False, 0xFFFFFF, куски[1]),      # конец пришёл раньше середины
                 mp_длинный(False, True, 0, куски[2])]              # повтор конца — второй сборки нет
        ид = self.разобрать(кадры)
        сводки = self.захваты.сводки(ид)
        self.assertIn("[собран из 3 фрагментов MP]", сводки[3]["инфо"])
        self.assertEqual([3], [i for i, с_ in enumerate(сводки) if "собран" in с_["инфо"]])
        self.assertIn("UDP", сводки[1]["инфо"] + сводки[1]["протокол"])
        self.assertEqual("DNS", сводки[3]["протокол"])
        пакет = self.захваты.пакет(ид, 4)
        self.assertEqual(["PPP", "IPv4", "UDP", "DNS"], [у["протокол"] for у in пакет["собранный"]["уровни"]])
        self.assertEqual([4], [i + 1 for i in self.захваты.отобрать(ид, "dns.a == 10.1.2.3")])

    def test_конец_раньше_начала(self):
        """Фрагменты пришли не по порядку (по разным звеньям): E, затем середина, затем B."""
        датаграмма = b"\x00\x21" + с.ip(с.udp(b"abcdefghij" * 5, 7, 9), 17)
        кадры = [mp_длинный(False, True, 12, датаграмма[40:]), mp_длинный(False, False, 11, датаграмма[20:40]),
                 mp_длинный(True, False, 10, датаграмма[:20])]
        ид = self.разобрать(кадры)
        self.assertIn("[собран из 3 фрагментов MP]", self.захваты.сводки(ид)[2]["инфо"])

    def test_пропуск_не_собирается(self):
        датаграмма = b"\x00\x21" + с.ip(с.udp(b"q" * 40, 7, 9), 17)
        кадры = [mp_длинный(True, False, 1, датаграмма[:20]), mp_длинный(False, True, 3, датаграмма[40:])]
        ид = self.разобрать(кадры)
        self.assertTrue(all("собран" not in с_["инфо"] for с_ in self.захваты.сводки(ид)))

    def test_короткие_после_configure_ack(self):
        """Configure-Ack LCP с опцией 18 — дальше заголовки MP короткие (номер 12 бит)."""
        датаграмма = b"\x00\x21" + с.ip(с.udp(b"w" * 30, 7, 9), 17)
        кадры = [ppp(0xC021, cp(1, 1, опции((18, b"")))), ppp(0xC021, cp(2, 1, опции((18, b"")))),
                 mp_короткий(True, False, 0x0C0, датаграмма[:25]), mp_короткий(False, True, 0x0C1, датаграмма[25:]),
                 ppp(0xC021, cp(2, 2, опции((18, b""))))]         # повторное согласование — начало то же
        ид = self.разобрать(кадры)
        сводки = self.захваты.сводки(ид)
        self.assertIn("[собран из 2 фрагментов MP]", сводки[3]["инфо"])
        # Подробный разбор фрагмента — тоже с коротким заголовком.
        пакет = self.захваты.пакет(ид, 3)
        self.assertIn("mp.sseq", [п_["ключ"] for п_ in пакет["уровни"][1]["поля"]])
        self.assertEqual(["PPP", "IPv4", "UDP", "Данные"], [у["протокол"] for у in
                                                           self.захваты.пакет(ид, 4)["собранный"]["уровни"]])


if __name__ == "__main__":
    unittest.main()
