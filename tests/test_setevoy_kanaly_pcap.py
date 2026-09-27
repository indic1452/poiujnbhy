"""Каналы pcap: 204/205/206 (байт направления), 139 (MTP2 с псевдозаголовком), 123 (SunATM), 100/11 (ATM RFC 1483), 197 (ERF).

Раскладки — по tcpdump.org linktypes/LINKTYPE_*.html и Wireshark (wiretap/pcap-common.c, packet-erf.c,
packet-mtp2.c); см. модуль.
"""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет, прочитать_захват
from test_setevoy_kanalnye import q922
from test_setevoy_oks7 import isup_iam, метка

ИП = с.ip(с.udp(b"payload-" * 4, 1000, 2000), 17)
MSU = bytes([0x85]) + метка(10, 20, 1) + isup_iam()


def erf_запись(тип, нагрузка, флаги=0x01, расширенные=b"", подзаголовок=b"", wlen=None, секунды=1_700_000_000):
    тело = подзаголовок + нагрузка
    rlen = 16 + len(расширенные) + len(тело)
    дополнение = b"\0" * (-rlen % 8)
    rlen += len(дополнение)
    время = (секунды << 32) | (1 << 31)                    # + 0,5 с
    return (struct.pack("<Q", время) + bytes([тип | (0x80 if расширенные else 0), флаги])
            + struct.pack(">HHH", rlen, 0, len(нагрузка) if wlen is None else wlen) + расширенные + тело + дополнение)


class НаправлениеTests(unittest.TestCase):
    def test_ppp(self):
        for кадр in (b"\x01\xff\x03\x00\x21" + ИП, b"\x01\x00\x21" + ИП):     # с FF 03 и без
            п = разобрать_пакет(кадр, "PPP с направлением")
            self.assertEqual(["Направление", "PPP", "IPv4", "UDP", "Данные"], п.стек)
            self.assertEqual(["передан этим узлом"], [п_.текст for п_ in п.уровни[0].поля])
        п = разобрать_пакет(b"\x00\xff\x03\x00\x21" + ИП, "PPP с направлением")
        self.assertEqual("принят (PPP)", п.уровни[0].итог)

    def test_cisco_hdlc_и_fr(self):
        п = разобрать_пакет(b"\x00\x0f\x00\x08\x00" + ИП, "Cisco HDLC с направлением")
        self.assertEqual(["Направление", "CHDLC", "IPv4", "UDP", "Данные"], п.стек)
        п = разобрать_пакет(b"\x05" + q922(100) + b"\x03\xcc" + ИП, "Frame Relay с направлением")
        self.assertEqual("передан (Frame Relay)", п.уровни[0].итог)
        self.assertIn("IPv4", п.стек)

    def test_номера_каналов_в_pcap(self):
        for номер, канал in ((204, "PPP с направлением"), (205, "Cisco HDLC с направлением"),
                             (206, "Frame Relay с направлением"), (139, "MTP2 с псевдозаголовком"),
                             (123, "SunATM"), (100, "ATM RFC 1483"), (11, "ATM RFC 1483"), (197, "ERF")):
            with self.subTest(номер=номер):
                self.assertEqual(канал, прочитать_захват(данные=с.pcap([b"\0" * 8], канал=номер)).записи[0].канал)


    def test_выгрузка_под_основным_номером(self):
        """Захват с каналом 11 выгружается под номером 100 — LINKTYPE_ATM_RFC1483."""
        import tempfile
        import time
        from pathlib import Path
        from reportgen.setevoy.zahvaty import Захваты
        with tempfile.TemporaryDirectory() as папка:
            захваты = Захваты(Path(папка))
            ид = захваты.создать(владелец=1, имя="atm.pcap",
                                 данные=с.pcap([b"\xaa\xaa\x03\x00\x00\x00\x08\x00" + ИП], канал=11))
            for _ in range(200):
                if захваты.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                    break
                time.sleep(0.05)
            выгрузка = захваты.выгрузить_pcap(ид, [0])
            self.assertEqual(100, struct.unpack_from("<I", выгрузка, 20)[0])


class Mtp2Tests(unittest.TestCase):
    def test_обычные_номера(self):
        единица = bytes([10 | 0x80, 20, min(len(MSU), 63)]) + MSU
        п = разобрать_пакет(bytes([1, 0]) + struct.pack(">H", 513) + единица, "MTP2 с псевдозаголовком")
        self.assertEqual(["MTP2 PHDR", "MTP2", "MTP3", "ISUP"], п.стек)
        self.assertEqual([513], п.поля_фильтра()["mtp2.phdr.link_number"])
        self.assertEqual("звено 513, направление 1", п.уровни[0].итог)
        # «Неизвестно» (2) — обычные номера, как у Wireshark (расширенные — только при 1).
        п = разобрать_пакет(bytes([1, 2, 0, 1]) + единица, "MTP2 с псевдозаголовком")
        self.assertEqual(["MTP2 PHDR", "MTP2", "MTP3", "ISUP"], п.стек)

    def test_расширенные_номера(self):
        """Q.703 прил. A: BSN 12 бит + BIB, FSN 12 бит + FIB, LI 9 бит — младший байт первым."""
        заголовок = struct.pack("<HHH", 0x8000 | 1000, 2001, len(MSU))
        п = разобрать_пакет(bytes([0, 1, 0, 7]) + заголовок + MSU, "MTP2 с псевдозаголовком")
        self.assertEqual(["MTP2 PHDR", "MTP2", "MTP3", "ISUP"], п.стек)
        ф = п.поля_фильтра()
        self.assertEqual(([1000], [1], [2001], [0], [len(MSU)]),
                         (ф["mtp2.ext.bsn"], ф["mtp2.ext.bib"], ф["mtp2.ext.fsn"], ф["mtp2.ext.fib"], ф["mtp2.ext.li"]))
        self.assertEqual("MSU, BSN 1000/1, FSN 2001/0", п.уровни[1].итог)
        self.assertEqual([], п.ошибки)

    def test_расширенные_fisu_lssu_и_ошибка_li(self):
        п = разобрать_пакет(bytes([0, 1, 0, 0]) + struct.pack("<HHH", 5, 6, 0), "MTP2 с псевдозаголовком")
        self.assertEqual("MTP2 FISU, BSN 5/0, FSN 6/0", п.инфо)
        п = разобрать_пакет(bytes([0, 1, 0, 0]) + struct.pack("<HHH", 5, 6, 1) + b"\x03", "MTP2 с псевдозаголовком")
        self.assertEqual([3], п.поля_фильтра()["mtp2.sf"])
        for li in (len(MSU) + 1, len(MSU) - 1):
            п = разобрать_пакет(bytes([0, 1, 0, 0]) + struct.pack("<HHH", 5, 6, li) + MSU, "MTP2 с псевдозаголовком")
            self.assertIn("[LI не сходится]", п.инфо)
            self.assertTrue(п.ошибки)
            self.assertEqual(["MTP2 PHDR", "MTP2", "Данные"], п.стек)
        # Зарезервированные старшие биты не входят в номера и LI.
        п = разобрать_пакет(bytes([0, 1, 0, 0]) + struct.pack("<HHH", 0x7000 | 5, 0x7000 | 6, 0xFE00),
                            "MTP2 с псевдозаголовком")
        self.assertEqual("MTP2 FISU, BSN 5/0, FSN 6/0", п.инфо)


class AtmTests(unittest.TestCase):
    def test_sunatm_llc(self):
        кадр = bytes([0x82, 1]) + struct.pack(">H", 32) + b"\xaa\xaa\x03\x00\x00\x00\x08\x00" + ИП
        п = разобрать_пакет(кадр, "SunATM")
        self.assertEqual(["SunATM", "LLC", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual("VPI/VCI 1/32, LLC", п.уровни[0].итог)
        ф = п.поля_фильтра()
        self.assertEqual(([1], [32], [1]), (ф["atm.vpi"], ф["atm.vci"], ф["sunatm.direction"]))
        self.assertEqual(["из сети"], [п_.текст for п_ in п.уровни[0].поля if п_.ключ == "sunatm.direction"])
        п = разобрать_пакет(bytes([0x03]) + кадр[1:], "SunATM")
        self.assertEqual(["в сеть"], [п_.текст for п_ in п.уровни[0].поля if п_.ключ == "sunatm.direction"])

    def test_sunatm_lane_и_прочее(self):
        кадр = bytes([0x01, 0]) + struct.pack(">H", 100) + b"\x00\x05" + с.eth(ИП)
        п = разобрать_пакет(кадр, "SunATM")
        self.assertEqual(["SunATM", "Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        # Сырой с LLC/SNAP внутри — LLC; сырой без него и Q.2931 — данные.
        self.assertIn("LLC", разобрать_пакет(bytes([0, 0, 0, 5]) + b"\xaa\xaa\x03\0\0\0\x08\0" + ИП, "SunATM").стек)
        self.assertEqual(["SunATM", "Данные"], разобрать_пакет(bytes([6, 0, 0, 5]) + b"\xaa\xaa\x03\0\0\0\x08\0",
                                                              "SunATM").стек)
        self.assertEqual(["SunATM", "Данные"], разобрать_пакет(bytes([0, 0, 0, 5]) + b"\x01\x02\x03", "SunATM").стек)

    def test_rfc1483(self):
        п = разобрать_пакет(b"\xaa\xaa\x03\x00\x00\x00\x08\x00" + ИП, "ATM RFC 1483")
        self.assertEqual(["LLC", "IPv4", "UDP", "Данные"], п.стек)


class ErfTests(unittest.TestCase):
    def test_ethernet_с_дополнением(self):
        кадр = с.eth(ИП)
        запись = erf_запись(2, кадр, подзаголовок=b"\x00\x00")
        self.assertEqual(0, len(запись) % 8)
        п = разобрать_пакет(запись, "ERF")
        self.assertEqual(["ERF", "Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        ф = п.поля_фильтра()
        self.assertEqual([2], ф["erf.types.type"])
        self.assertEqual([len(кадр)], ф["erf.wlen"])
        self.assertEqual("ERF Ethernet, интерфейс 1", "ERF " + п.уровни[0].итог)
        self.assertEqual("1700000000.500000000 с", п.уровни[0].поля[0].текст)

    def test_hdlc_ppp_и_cisco(self):
        п = разобрать_пакет(erf_запись(1, b"\xff\x03\x00\x21" + ИП), "ERF")
        self.assertEqual(["ERF", "PPP", "IPv4", "UDP", "Данные"], п.стек)
        for первый in (0x0F, 0x8F):
            п = разобрать_пакет(erf_запись(1, bytes([первый]) + b"\x00\x08\x00" + ИП), "ERF")
            self.assertEqual(["ERF", "CHDLC", "IPv4", "UDP", "Данные"], п.стек)

    def test_многоканальный_hdlc_и_расширенный_заголовок(self):
        расширенные = bytes([0x83]) + bytes(7) + bytes([0x04]) + bytes(7)          # два: 3 (есть ещё) и 4
        запись = erf_запись(5, b"\x0f\x00\x08\x00" + ИП, расширенные=расширенные,
                            подзаголовок=struct.pack(">I", 0x40000000 | 17))
        п = разобрать_пакет(запись, "ERF")
        self.assertEqual(["ERF", "CHDLC", "IPv4", "UDP", "Данные"], п.стек)
        ф = п.поля_фильтра()
        self.assertEqual([17], ф["erf.mchdlc.cn"])
        self.assertEqual([3, 4], ф["erf.ehdr"])

    def test_aal5_ip_и_флаги(self):
        ячейка = struct.pack(">I", (3 << 20) | (77 << 4))
        п = разобрать_пакет(erf_запись(4, ячейка + b"\xaa\xaa\x03\x00\x00\x00\x08\x00" + ИП, флаги=0x10), "ERF")
        self.assertEqual(["ERF", "ATM", "LLC", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual("VPI/VCI 3/77", п.уровни[1].итог)
        self.assertEqual("AAL5, интерфейс 0, ошибка приёма", п.уровни[0].итог)
        self.assertEqual(["ERF", "IPv4", "UDP", "Данные"], разобрать_пакет(erf_запись(22, ИП), "ERF").стек)
        self.assertEqual(["ERF", "ATM", "Данные"], разобрать_пакет(erf_запись(3, ячейка + bytes(48)), "ERF").стек)

    def test_wlen_отсекает_дополнение(self):
        """Кадр PPP короче записи: хвост дополнения до 8 байт в разбор не попадает."""
        нагрузка = b"\xff\x03\x40\x21" + b"abc"            # Stacker LZS: данные до конца кадра
        запись = erf_запись(1, нагрузка)
        self.assertGreater(len(запись), 16 + len(нагрузка))                 # дополнение есть
        п = разобрать_пакет(запись, "ERF")
        данные = [у for у in п.уровни if у.протокол == "Данные"][0]
        self.assertEqual(16 + len(нагрузка), данные.смещение + данные.длина)


if __name__ == "__main__":
    unittest.main()
