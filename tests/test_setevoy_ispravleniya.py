"""Исправления по обзору: SLARP на Cisco HDLC, IPsec за NAT (UDP 4500), псевдопроводы MPLS и MEF 8.

Эталоны — разборщики Wireshark: packet-chdlc.c (dissect_slarp: коды 0/1/2, адрес и маска или
номера keepalive и надёжность 0xFFFF), packet-ipsec-udp.c (0xFF — NAT-keepalive, нулевой SPI —
non-ESP marker и IKE, иначе ESP), packet-mpls.c (первый полубайт 1 — G-ACh), packet-pw-cesopsn.c
(биты L, R, M, FRG, LEN слова), packet-cesoeth.c (ECID 20 бит, резерв 0x102).
"""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет


def поле(п, ключ):
    def обойти(поля):
        for п_ in поля:
            yield п_
            yield from обойти(п_.дети)
    for у in п.уровни:
        for п_ in обойти(у.поля):
            if п_.ключ == ключ:
                return п_
    raise AssertionError(f"нет поля {ключ} в {п.стек}")


def ike_заголовок(длина_тела=0):
    """Заголовок ISAKMP: SPI ×2, next payload, версия 2.0, IKE_SA_INIT, флаги, ID, длина."""
    длина = 28 + длина_тела
    return bytes(range(1, 9)) + bytes(8) + bytes([33, 0x20, 34, 0x08]) + bytes(4) + struct.pack(">I", длина) \
        + bytes(длина_тела)


class SlarpTests(unittest.TestCase):
    def test_keepalive(self):
        кадр = bytes([0x8F, 0x00]) + struct.pack(">HIIIH", 0x8035, 2, 1234, 1233, 0xFFFF)
        п = разобрать_пакет(кадр, "Cisco HDLC")
        self.assertIn("SLARP", п.стек)
        self.assertNotIn("ARP", [у.протокол for у in п.уровни])
        self.assertEqual("SLARP keepalive: свой 1234, получен 1233", п.инфо)
        self.assertEqual("0xffff", поле(п, "slarp.reliability").текст)

    def test_запрос_и_ответ(self):
        кадр = bytes([0x8F, 0x00]) + struct.pack(">HI", 0x8035, 1) + bytes([10, 1, 2, 3, 255, 255, 255, 252]) + bytes(2)
        п = разобрать_пакет(кадр, "Cisco HDLC")
        self.assertEqual("SLARP ответ с адресом: 10.1.2.3/255.255.255.252", п.инфо)

    def test_надёжность_не_ffff(self):
        кадр = bytes([0x8F, 0x00]) + struct.pack(">HIIIH", 0x8035, 2, 5, 4, 0x1234)
        п = разобрать_пакет(кадр, "Cisco HDLC")
        self.assertIn("не 0xFFFF", поле(п, "slarp.reliability").текст)

    def test_rarp_на_ethernet_остаётся(self):
        """0x8035 на Ethernet — по-прежнему RARP."""
        rarp = struct.pack(">HHBBH", 1, 0x0800, 6, 4, 3) + bytes(20)
        п = разобрать_пакет(с.eth(rarp, тип=0x8035))
        self.assertIn("ARP", п.стек)
        self.assertNotIn("SLARP", п.стек)


class NatTTests(unittest.TestCase):
    def test_keepalive(self):
        п = разобрать_пакет(с.eth(с.ip(с.udp(b"\xff", 4500, 4500), 17)))
        self.assertEqual("NAT-keepalive", п.инфо)

    def test_ike_за_маркером(self):
        п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(4) + ike_заголовок(8), 4500, 4500), 17)))
        self.assertIn("ISAKMP", п.стек)
        self.assertIn("IKEv2 IKE_SA_INIT", п.инфо)

    def test_esp_в_udp(self):
        п = разобрать_пакет(с.eth(с.ip(с.udp(struct.pack(">II", 0x11223344, 7) + bytes(40), 4500, 4500), 17)))
        self.assertIn("ESP", п.стек)
        self.assertEqual("ESP SPI 0x11223344, номер 7", п.инфо)

    def test_ike_на_500_как_был(self):
        п = разобрать_пакет(с.eth(с.ip(с.udp(ike_заголовок(8), 500, 500), 17)))
        self.assertIn("IKEv2 IKE_SA_INIT", п.инфо)


def mpls(метка, дно=1):
    return struct.pack(">I", (метка << 12) | (дно << 8) | 64)


def cw(номер, l_бит=0, r_бит=0, m=0, frg=0, длина=0):
    return bytes([(l_бит << 3) | (r_бит << 2) | m, (frg << 6) | длина]) + struct.pack(">H", номер)


class ПсевдопроводыTests(unittest.TestCase):
    def test_ethernet_с_управляющим_словом(self):
        внутри = с.eth(с.ip(с.udp(b"x" * 10, 1000, 2000), 17))
        п = разобрать_пакет(с.eth(mpls(100) + cw(5) + внутри, тип=0x8847))
        self.assertEqual(2, п.стек.count("Ethernet"))
        self.assertIn("UDP", п.стек)

    def test_satop_e1(self):
        """SAToP: 8 кадров E1 по 32 байта — не Ethernet, а TDM."""
        tdm = bytes(range(256))
        п = разобрать_пакет(с.eth(mpls(200) + cw(77) + tdm, тип=0x8847))
        self.assertIn("PW-TDM", п.стек)
        self.assertEqual(1, п.стек.count("Ethernet"))
        self.assertEqual("PW TDM номер 77, 256 байт: E1 целиком — 8 кадров G.704 (SAToP) или n×64", п.инфо)
        self.assertEqual(77, поле(п, "pwtdm.cw.seqno").сырое)

    def test_cesopsn_отказ_и_длина(self):
        """CESoPSN n×64: 5 КИ × 4 кадра = 20 байт < 64 — длина в слове (нагрузка + 4), L — отказ стыка."""
        tdm = bytes(20)
        п = разобрать_пакет(с.eth(mpls(300) + cw(9, l_бит=1, длина=24) + tdm + bytes(26), тип=0x8847))
        self.assertEqual("отказ стыка: данные TDM неверны", поле(п, "pwtdm.cw.lm").текст)
        self.assertEqual("20 байт", поле(п, "pwtdm.payload").текст)
        self.assertIn("n×64 кбит/с (CESoPSN)", п.инфо)

    def test_бит_r_отдельно_от_l_m(self):
        п = разобрать_пакет(с.eth(mpls(300) + cw(4, r_бит=1) + bytes(64), тип=0x8847))
        self.assertEqual("норма", поле(п, "pwtdm.cw.lm").текст)
        self.assertEqual(1, поле(п, "pwtdm.cw.rbit").сырое)

    def test_вид_по_длине_нагрузки(self):
        for длина, вид in ((48, "T1 целиком — 2 кадров"), (80, "n×64 кбит/с (CESoPSN)"), (96, "E1 целиком — 3 кадров")):
            with self.subTest(длина=длина):
                п = разобрать_пакет(с.eth(mpls(300) + cw(1) + bytes(длина), тип=0x8847))
                self.assertIn(вид, п.инфо)

    def test_g_ach(self):
        п = разобрать_пакет(с.eth(mpls(13) + bytes([0x10, 0, 0x00, 0x22]) + bytes(24), тип=0x8847))
        self.assertIn("PWACH", п.стек)
        self.assertEqual("G-ACh BFD CC (RFC 6428)", п.инфо)

    def test_cesoeth(self):
        tdm = bytes(range(64))
        п = разобрать_пакет(с.eth(struct.pack(">I", (0x12345 << 12) | 0x102) + cw(3) + tdm, тип=0x88D8))
        self.assertIn("CESoETH", п.стек)
        self.assertEqual("0x12345", поле(п, "cesoeth.ecid").текст)
        self.assertEqual("0x102", поле(п, "cesoeth.reserved").текст)
        self.assertIn("E1 целиком — 2 кадров", п.инфо)


if __name__ == "__main__":
    unittest.main()


class ПодписьСкремблераTests(unittest.TestCase):
    def test_v22_без_непроверенной_приписки(self):
        """1 + x⁻¹⁴ + x⁻¹⁷ — скремблер V.22bis; принадлежность Intelsat не подтверждена (у синхронного
        скремблера IESS по открытым выдержкам — 1 + x⁻¹⁴ + x⁻¹⁵), приписки нет."""
        from reportgen.potok import skrembler
        имена = [имя for имя, отводы in skrembler.ИЗВЕСТНЫЕ.items() if tuple(отводы) == (14, 17)]
        self.assertEqual(["ITU-T V.22/V.22bis (1 + x⁻¹⁴ + x⁻¹⁷)"], имена)
