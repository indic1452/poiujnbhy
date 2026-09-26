"""GSM поверх GSMTAP: LAPDm (44.006), псевдодлина CCCH/BCCH, сообщения RR/MM/CC (44.018, 24.008).

Названия сообщений и дискриминаторы — по таблицам Wireshark (packet-gsm_a_rr.c,
packet-gsm_a_dtap.c); идентификатор абонента и LAI — по 24.008 10.5.1.4 и 10.5.1.3
(Wireshark de_mid, de_lai).
"""

import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.protokoly import gsm
from test_setevoy_mobilnye import gsmtap, на_udp

LAI_25001 = bytes([0x52, 0xF0, 0x10]) + (8000).to_bytes(2, "big")      # MCC 250, MNC 01, LAC 8000
IMSI = "250011234567890"


def imsi_ident(цифры=IMSI):
    нечёт = len(цифры) % 2
    первый = (int(цифры[0]) << 4) | (нечёт << 3) | 1
    остаток = цифры[1:] + ("" if нечёт else "F")
    return bytes([первый]) + bytes((int(остаток[i + 1], 16) << 4 | int(остаток[i], 16)) if i + 1 < len(остаток)
                                   else (0xF0 | int(остаток[i])) for i in range(0, len(остаток), 2))


def псевдо(сообщение):
    return bytes([(len(сообщение) << 2) | 1]) + сообщение + b"\x2b" * (23 - 1 - len(сообщение))


def lapdm(сообщение, упр=0x00, sapi=0):
    return bytes([(sapi << 2) | 1, упр, (len(сообщение) << 2) | 1]) + сообщение + b"\x2b" * (20 - len(сообщение))


class GsmTests(unittest.TestCase):
    def test_идентификатор_и_lai(self):
        self.assertEqual(("IMSI", IMSI), gsm.идентификатор(imsi_ident()))
        self.assertEqual(("IMSI", "25001123456789"), gsm.идентификатор(imsi_ident("25001123456789")))
        self.assertEqual(("TMSI", "DEADBEEF"), gsm.идентификатор(bytes.fromhex("F4DEADBEEF")))
        imei = bytearray(imsi_ident("353456789012345"))
        imei[0] = imei[0] & 0xF8 | 2                                 # вид 2 — IMEI
        self.assertEqual(("IMEI", "353456789012345"), gsm.идентификатор(bytes(imei)))
        self.assertIsNone(gsm.идентификатор(b""))
        self.assertIsNone(gsm.идентификатор(b"\x00"))
        self.assertEqual("MCC 250, MNC 01, LAC 8000", gsm.lai(LAI_25001))
        self.assertEqual("MCC 310, MNC 410, LAC 1", gsm.lai(bytes([0x13, 0x00, 0x14, 0x00, 0x01])))
        self.assertIsNone(gsm.lai(LAI_25001[:4]))

    def test_bcch_si3(self):
        п = разобрать_пакет(на_udp(gsmtap(подтип=1, нагрузка=псевдо(b"\x06\x1b" + (4660).to_bytes(2, "big")
                                                                        + LAI_25001 + bytes(11))), 4729))
        self.assertEqual(["GSMTAP", "L2 pseudo", "GSM RR", "Данные"], п.стек[3:])
        self.assertEqual("GSM RR System Information Type 3: CI 4660, MCC 250, MNC 01, LAC 8000", п.инфо)

    def test_pch_вызов_по_imsi(self):
        ид = imsi_ident()
        п = разобрать_пакет(на_udp(gsmtap(подтип=5, нагрузка=псевдо(b"\x06\x21\x00" + bytes([len(ид)]) + ид)), 4729))
        self.assertEqual(f"GSM RR Paging Request Type 1: IMSI {IMSI}", п.инфо)

    def test_sdcch_lapdm_lu_request(self):
        mm = b"\x05\x08\x70" + LAI_25001 + b"\x33\x05\xf4\xde\xad\xbe\xef"
        п = разобрать_пакет(на_udp(gsmtap(подтип=7, нагрузка=lapdm(mm)), 4729))
        self.assertEqual(["GSMTAP", "LAPDm", "GSM MM", "Данные"], п.стек[3:])
        self.assertEqual("GSM MM Location Updating Request: MCC 250, MNC 01, LAC 8000; TMSI DEADBEEF", п.инфо)

    def test_facch_cc_setup_номер_и_nsd(self):
        # Тип 0x45: N(SD) = 1 в битах 8–7, сам тип — 0x05 (Setup).
        # Перед номером — однобайтовый элемент CLIR suppression (0xA1) и Bearer capability (TLV).
        cc = b"\x03\x45" + b"\xa1\x04\x01\xa0" + b"\x5e\x06\x81\x94\x15\x32\x54\x76"
        п = разобрать_пакет(на_udp(gsmtap(подтип=9, нагрузка=lapdm(cc)), 4729))
        self.assertEqual("GSM CC Setup: номер 4951234567", п.инфо)

    def test_sacch_заголовок_l1(self):
        rr = b"\x06\x15" + bytes(17)                                # Measurement Report
        п = разобрать_пакет(на_udp(gsmtap(подтип=0x80 | 6, нагрузка=b"\x05\x02" + lapdm(rr, упр=0x03)[:21]), 4729))
        self.assertEqual(["GSMTAP", "SACCH L1", "LAPDm", "GSM RR", "Данные"], п.стек[3:])
        self.assertEqual("GSM RR Measurement Report", п.инфо)

    def test_фрагмент_и_s_кадр(self):
        # M = 1 — начало длинного сообщения: не разбирается, даже если похоже на целое.
        rr = b"\x06\x1b" + (4660).to_bytes(2, "big") + LAI_25001 + bytes(11)
        п = разобрать_пакет(на_udp(gsmtap(подтип=6, нагрузка=b"\x01\x00" + bytes([(len(rr) << 2) | 3]) + rr), 4729))
        self.assertIn("фрагмент", п.инфо)
        self.assertNotIn("GSM RR", п.стек)
        п = разобрать_пакет(на_udp(gsmtap(подтип=6, нагрузка=b"\x01\x41\x01" + b"\x2b" * 20), 4729))
        self.assertEqual("LAPDm SAPI 0, RR, N(R)=2, 0 байт", п.инфо)

    def test_псевдодлина_без_01_и_не_um(self):
        rr = b"\x06\x1b" + (4660).to_bytes(2, "big") + LAI_25001 + bytes(11)
        # Октет псевдодлины с 00 в младших битах — не CCCH, хотя дальше похоже на SI 3.
        п = разобрать_пакет(на_udp(gsmtap(подтип=1, нагрузка=bytes([len(rr) << 2]) + rr + bytes(4)), 4729))
        self.assertNotIn("GSM RR", п.стек)
        # Тип GSMTAP 2 (Abis) — нагрузка не Um: данными.
        п = разобрать_пакет(на_udp(gsmtap(тип=2, подтип=1, нагрузка=псевдо(rr)), 4729))
        self.assertNotIn("GSM RR", п.стек)
        # ACCH на подтипе BCCH — это SACCH: заголовок L1 и LAPDm, а не псевдодлина.
        п = разобрать_пакет(на_udp(gsmtap(подтип=0x81, нагрузка=b"\x05\x02" + lapdm(b"\x06\x15" + bytes(17), упр=0x03)[:21]), 4729))
        self.assertIn("SACCH L1", п.стек)

    def test_не_gsm_остаётся_данными(self):
        # Псевдодлина без 01 в младших битах, неизвестный тип RR, адрес LAPDm без EA — данные.
        for подтип, нагрузка in ((1, bytes(23)), (1, псевдо(b"\x06\xee")), (6, b"\x00\x00\x01" + bytes(20))):
            with self.subTest(подтип=подтип, нагрузка=нагрузка[:3].hex()):
                п = разобрать_пакет(на_udp(gsmtap(подтип=подтип, нагрузка=нагрузка), 4729))
                self.assertNotIn("GSM RR", п.стек)
                self.assertEqual("Данные", п.стек[-1])


if __name__ == "__main__":
    unittest.main()
