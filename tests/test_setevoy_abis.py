"""GSM Abis поверх LAPD: RSL (TS 48.058) на SAPI 0 и OML (TS 12.21) на SAPI 62.

Сообщения собраны по раскладке libosmocore (gsm_08_58.h: заголовки abis_rsl_rll_hdr /
abis_rsl_dchan_hdr / abis_rsl_cchan_hdr — дискриминатор, тип, тег и номер канала; rsl.c
rsl_att_tlvdef — длины элементов; gsm_12_21.h — заголовок OML и FOM).
"""

import struct
import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy import разобрать_пакет
from test_setevoy_gsm import LAI_25001, imsi_ident, псевдо

LU = b"\x05\x08\x70" + LAI_25001 + b"\x33\x05\xf4\xde\xad\xbe\xef"


def lapd_i(информация, sapi=0, tei=0, ns=0, nr=0):
    return bytes([sapi << 2, (tei << 1) | 1, ns << 1, nr << 1]) + информация


def l3_info(сообщение):
    return b"\x0b" + struct.pack(">H", len(сообщение)) + сообщение


class RslTests(unittest.TestCase):
    def test_data_ind_с_location_update(self):
        """RLL DATA INDication: канал SDCCH/8 подканал 2 TS 1, link id SAPI 0, L3 — Location Updating Request."""
        rsl = bytes([0x02, 0x02, 0x01, (0x08 | 2) << 3 | 1, 0x02, 0x00]) + l3_info(LU)
        п = разобрать_пакет(lapd_i(rsl), "LAPD")
        self.assertEqual(["LAPD", "RSL", "GSM MM", "Данные"], п.стек)
        self.assertIn("Location Updating Request: MCC 250, MNC 01, LAC 8000; TMSI DEADBEEF", п.инфо)
        rsl_у = [у for у in п.уровни if у.протокол == "RSL"][0]
        self.assertEqual("DATA INDication, SDCCH/8, подканал 2, TS 1", rsl_у.итог)

    def test_imm_ass_на_agch(self):
        """CCHAN IMMEDIATE ASSIGN COMMAND: PCH/AGCH TS 0, Full Imm Assign Info — псевдодлина + RR."""
        rr = b"\x06\x3f" + bytes(20)
        rsl = bytes([0x0C, 0x16, 0x01, 0x12 << 3, 0x2B, 23]) + псевдо(rr)
        п = разобрать_пакет(lapd_i(rsl), "LAPD")
        self.assertIn("RSL", п.стек)
        self.assertIn("GSM RR", п.стек)
        self.assertIn("Immediate Assignment", п.инфо)

    def test_paging_command(self):
        ид = imsi_ident()
        rsl = bytes([0x0C, 0x15, 0x01, 0x12 << 3, 0x0E, 3, 0x0C, len(ид)]) + ид
        п = разобрать_пакет(lapd_i(rsl), "LAPD")
        self.assertEqual(["LAPD", "RSL"], п.стек)
        self.assertEqual("RSL PAGING CoMmanD, PCH/AGCH, TS 0", п.инфо)

    def test_channel_activation(self):
        """DCHAN CHANnel ACTIVation TCH/F TS 3: Activation Type, Channel Mode (TLV), MS Power, Timing Advance."""
        rsl = bytes([0x08, 0x21, 0x01, (0x01 << 3) | 3, 0x03, 0x00, 0x06, 4, 0x00, 0x08, 0x01, 0x01, 0x0D, 0x0F,
                     0x18, 12])
        п = разобрать_пакет(lapd_i(rsl), "LAPD")
        self.assertEqual("RSL CHANnel ACTIVation, TCH/F (Bm + ACCH), TS 3", п.инфо)

    def test_номера_каналов(self):
        """Биты C и TN (48.058 9.3.1): TCH/H подканал 1 TS 6, SDCCH/4 подканал 3 TS 0, BCCH TS 0."""
        for октет, текст in (((0x03 << 3) | 6, "TCH/H (Lm + ACCH), подканал 1, TS 6"),
                             ((0x07 << 3) | 0, "SDCCH/4, подканал 3, TS 0"),
                             ((0x01 << 3) | 7, "TCH/F (Bm + ACCH), TS 7"), (0x10 << 3, "BCCH, TS 0")):
            with self.subTest(текст=текст):
                п = разобрать_пакет(lapd_i(bytes([0x08, 0x2E, 0x01, октет])), "LAPD")
                self.assertEqual(f"RSL RF CHANnel RELease, {текст}", п.инфо)

    def test_элемент_фиксированной_длины(self):
        """Frame Number — FIXED 2 (rsl_att_tlvdef): сообщение с ним укладывается ровно; на байт длиннее — нет."""
        п = разобрать_пакет(lapd_i(bytes([0x0C, 0x13, 0x01, 0x11 << 3, 0x13, 0x25, 0x12, 0x34, 0x11, 3,
                                          0x08, 0x12, 0x34])), "LAPD")
        self.assertEqual("RSL CHANnel ReQuireD, RACH, TS 0", п.инфо)
        п = разобрать_пакет(lapd_i(bytes([0x0C, 0x13, 0x01, 0x11 << 3, 0x08, 0x12, 0x34, 0x56])), "LAPD")
        self.assertNotIn("RSL", п.стек)

    def test_q931_не_путается_с_rsl(self):
        """Q.931 SETUP: дискриминатор 0x08, как у выделенного канала RSL, но тип 0x01 (длина ссылки) — не из группы."""
        q931 = bytes([0x08, 0x01, 0x05, 0x05, 0x04, 0x03, 0x80, 0x90, 0xA3])
        п = разобрать_пакет(lapd_i(q931), "LAPD")
        self.assertIn("Q.931", п.стек)
        self.assertNotIn("RSL", п.стек)

    def test_лишний_байт_не_rsl(self):
        rsl = bytes([0x08, 0x21, 0x01, (0x01 << 3) | 3, 0x03, 0x00, 0x77])
        п = разобрать_пакет(lapd_i(rsl), "LAPD")
        self.assertNotIn("RSL", п.стек)


class OmlTests(unittest.TestCase):
    def test_opstart_radio_carrier(self):
        fom = bytes([0x74, 0x02, 0x00, 0x01, 0xFF])
        oml = bytes([0x80, 0x80, 0x00, len(fom)]) + fom
        п = разобрать_пакет(lapd_i(oml, sapi=62), "LAPD")
        self.assertEqual(["LAPD", "OML"], п.стек)
        self.assertEqual("OML Opstart: Radio Carrier (0, 1, 255)", п.инфо)

    def test_state_changed_с_атрибутами(self):
        fom = bytes([0x61, 0x03, 0x00, 0x00, 0x02]) + bytes([0x24, 0x02, 0x07, 0x01])
        oml = bytes([0x80, 0x80, 0x05, len(fom)]) + fom
        п = разобрать_пакет(lapd_i(oml, sapi=62), "LAPD")
        self.assertEqual("OML State Changed Event Report: Channel (0, 0, 2)", п.инфо)

    def test_длина_не_сходится_не_oml(self):
        fom = bytes([0x74, 0x02, 0x00, 0x01, 0xFF])
        п = разобрать_пакет(lapd_i(bytes([0x80, 0x80, 0x00, len(fom) + 1]) + fom, sapi=62), "LAPD")
        self.assertNotIn("OML", п.стек)

    def test_хвост_за_длиной_не_oml(self):
        fom = bytes([0x74, 0x02, 0x00, 0x01, 0xFF, 0x24, 0x02, 0x07, 0x01])
        п = разобрать_пакет(lapd_i(bytes([0x80, 0x80, 0x00, len(fom) - 1]) + fom, sapi=62), "LAPD")
        self.assertNotIn("OML", п.стек)

    def test_неизвестный_тип_fom_не_oml(self):
        fom = bytes([0x00, 0x02, 0x00, 0x01, 0xFF])
        п = разобрать_пакет(lapd_i(bytes([0x80, 0x80, 0x00, len(fom)]) + fom, sapi=62), "LAPD")
        self.assertNotIn("OML", п.стек)

    def test_mmi(self):
        oml = bytes([0x40, 0x80, 0x00, 3]) + b"abc"
        п = разобрать_пакет(lapd_i(oml, sapi=62), "LAPD")
        self.assertEqual("OML MMI, 3 байт", п.инфо)


if __name__ == "__main__":
    unittest.main()


class ПотокTests(unittest.TestCase):
    def test_кадры_abis_называются_abis(self):
        """Поток HDLC с кадрами LAPD, в которых RSL и OML, — «GSM Abis», а не ISDN."""
        from reportgen.potok import kanal
        rsl = bytes([0x02, 0x02, 0x01, (0x08 | 2) << 3 | 1, 0x02, 0x00]) + l3_info(LU)
        fom = bytes([0x74, 0x02, 0x00, 0x01, 0xFF])
        кадры = []
        for i in range(60):
            кадры.append(lapd_i(rsl, ns=i % 128, nr=i % 128))
            кадры.append(lapd_i(bytes([0x80, 0x80, i % 256, len(fom)]) + fom, sapi=62, ns=i % 128))
        найдено = kanal.найти(кадры)
        self.assertEqual("GSM Abis: LAPD, RSL (48.058) и OML (12.21)", найдено.что)
        self.assertIn("сообщений RSL/OML: 120 из 120", найдено.подробно[1])

    def test_редкий_rsl_среди_q931_не_abis(self):
        """2 кадра RSL на 60 Q.931 (меньше 20 %) — это ISDN, а не Abis."""
        from reportgen.potok import kanal
        q931 = bytes([0x08, 0x01, 0x05, 0x05, 0x04, 0x03, 0x80, 0x90, 0xA3])
        rsl = bytes([0x08, 0x2E, 0x01, 0x08])
        кадры = [lapd_i(q931, ns=i % 128) for i in range(60)] + [lapd_i(rsl), lapd_i(rsl, ns=1)]
        self.assertEqual("ISDN, LAPD (Q.921)", kanal.найти(кадры).что)

    def test_isdn_остаётся_isdn(self):
        from reportgen.potok import kanal
        q931 = bytes([0x08, 0x01, 0x05, 0x05, 0x04, 0x03, 0x80, 0x90, 0xA3])
        self.assertEqual("ISDN, LAPD (Q.921)", kanal.найти([lapd_i(q931, ns=i % 128) for i in range(60)]).что)
