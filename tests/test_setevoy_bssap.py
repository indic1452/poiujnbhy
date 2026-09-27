"""BSSAP (3GPP TS 48.008) поверх SCCP: BSSMAP и DTAP — сообщения собираются по 3.1–3.2 (дискриминатор, длина,
тип, элементы TV/TLV); уровень 3 — сообщения 24.008 из тестов GSM."""

import struct
import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy.pole import Пакет
from reportgen.setevoy.protokoly import bssap, oks7
from reportgen.setevoy.razbor import ДОП_УРОВНИ, Разбор, разобрать_пакет
from test_setevoy_abis import LU
from test_setevoy_oks7 import IMSI, bcd, sccp_udt, адрес_pc

ЯЧЕЙКА_CGI = bytes([0x00, 0x52, 0xF0, 0x10]) + struct.pack(">HH", 0x1234, 0x0042)


def tlv(iei, значение):
    return bytes([iei, len(значение)]) + значение


def bssmap(тип, элементы=b""):
    тело = bytes([тип]) + элементы
    return bytes([0x00, len(тело)]) + тело


def dtap(l3, dlci=0x00):
    return bytes([0x01, dlci, len(l3)]) + l3


def по_udt(данные, ssn=254):
    return разобрать_пакет(sccp_udt(адрес_pc(1, ssn), адрес_pc(2, ssn), данные), "SCCP")


def по_dt1(данные):
    return разобрать_пакет(bytes([0x06, 1, 2, 3, 0, 1, len(данные)]) + данные, "SCCP")


def по_cr(данные, ssn=254):
    вызываемый = адрес_pc(10, ssn)
    cr = (bytes([0x01, 0x01, 0x02, 0x03, 0x02, 2, 1 + 1 + len(вызываемый)]) + bytes([len(вызываемый)]) + вызываемый
          + tlv(0x0F, данные) + b"\x00")
    return разобрать_пакет(cr, "SCCP")


def личность(цифры, вид):
    return bytes([int(цифры[0]) << 4 | (8 if len(цифры) % 2 else 0) | вид]) + bcd(цифры[1:], 0xF)


def поля(п, протокол="BSSMAP"):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


class ТестBSSMAP(unittest.TestCase):
    def test_complete_layer3(self):
        данные = bssmap(0x57, tlv(0x05, ЯЧЕЙКА_CGI) + tlv(0x17, LU))
        for п in (по_udt(данные), по_cr(данные)):
            self.assertEqual(п.стек[:3], ["SCCP", "BSSMAP", "GSM MM"])
            ф = поля(п)
            self.assertEqual(ф["gsm_a.bssmap.msgtype"].текст, "0x57 (Complete Layer 3 Information)")
            self.assertEqual(ф["gsm_a.bssmap.cell_id"].текст, "CGI 250-01, LAC 4660, CI 66")
            self.assertTrue(п.инфо.startswith("BSSMAP Complete Layer 3 Information, CGI 250-01, LAC 4660, CI 66; GSM MM"),
                            п.инфо)
            у = [x for x in п.уровни if x.протокол == "GSM MM"][0]
            self.assertEqual(п.данные[у.смещение:у.смещение + len(LU)], LU)

    def test_cipher_mode(self):
        kc = bytes.fromhex("0011223344556677")
        п = по_dt1(bssmap(0x53, tlv(0x0A, b"\x06" + kc)))
        ф = поля(п)
        self.assertEqual(ф["gsm_a.bssmap.enc_info.permitted"].текст, "A5/1, A5/2")
        self.assertEqual(ф["gsm_a.bssmap.enc_info_key"].текст, kc.hex())
        self.assertEqual(п.инфо, "BSSMAP Cipher Mode Command, шифры A5/1, A5/2")
        self.assertEqual(ф["gsm_a.bssmap.elem_id"].текст, f"A5/1, A5/2; Kc {kc.hex()}")
        п = по_dt1(bssmap(0x53, tlv(0x0A, b"\x01")))
        self.assertEqual(поля(п)["gsm_a.bssmap.enc_info.permitted"].текст, "A5/0")
        self.assertNotIn("gsm_a.bssmap.enc_info_key", поля(п))
        п = по_dt1(bssmap(0x53, tlv(0x0A, b"\x00")))
        self.assertEqual(п.инфо, "BSSMAP Cipher Mode Command, шифры нет")
        п = по_dt1(bssmap(0x55, b"\x2c\x02"))
        self.assertEqual(п.инфо, "BSSMAP Cipher Mode Complete, выбран A5/1")
        self.assertEqual(по_dt1(bssmap(0x55, b"\x2c\x01")).инфо, "BSSMAP Cipher Mode Complete, выбран без шифра (A5/0)")

    def test_причины_cic_канал(self):
        п = по_dt1(bssmap(0x20, tlv(0x04, b"\x09")))
        self.assertEqual(поля(п)["gsm_a.bssmap.cause"].сырое, 9)
        self.assertEqual(п.инфо, "BSSMAP Clear Command, причина: Call control")
        self.assertEqual(по_dt1(bssmap(0x22, tlv(0x04, b"\x99\x00"))).инфо, "BSSMAP Clear Request, причина: причина 25")
        п = по_dt1(bssmap(0x01, tlv(0x0B, b"\x01\x08\x01") + b"\x01" + struct.pack(">H", (3 << 5) | 17)))
        ф = поля(п)
        self.assertEqual(ф["gsm_a.bssmap.cic"].сырое, (3 << 5) | 17)
        элементы = [x for x in п.уровни[1].поля if x.ключ == "gsm_a.bssmap.elem_id"]
        self.assertEqual([x.текст for x in элементы], ["речь, вид канала 0x08", "мультиплексор 3, интервал 17"])
        self.assertEqual([(x.смещение, x.длина) for x in элементы], [(10, 5), (15, 3)])
        self.assertEqual(ф["gsm_a.bssmap.speech_data_ind"].текст, "речь")

    def test_paging_и_идентификаторы(self):
        п = по_udt(bssmap(0x52, tlv(0x08, личность(IMSI, 1)) + tlv(0x09, b"\xde\xad\xbe\xef")
                          + tlv(0x1A, b"\x05\x12\x34")))
        ф = поля(п)
        self.assertEqual(ф["e212.imsi"].текст, IMSI)
        self.assertEqual(ф["gsm_a.tmsi"].текст, "DEADBEEF")
        self.assertEqual(п.инфо, f"BSSMAP Paging, IMSI {IMSI}, TMSI DEADBEEF")
        п = по_dt1(bssmap(0x2F, tlv(0x08, личность(IMSI, 1))))
        self.assertEqual(п.инфо, f"BSSMAP Common Id, IMSI {IMSI}")

    def test_виды_ячейки(self):
        for значение, текст in ((b"\x01\x12\x34\x00\x42", "LAC 4660, CI 66"), (b"\x02\x00\x42", "CI 66"),
                                (b"\x04\x52\xf0\x10\x12\x34", "LAI 250-01, LAC 4660"), (b"\x05\x12\x34", "LAC 4660"),
                                (b"\x03", "без ячейки"), (b"\x06", "все ячейки BSS"), (b"\x08\x00", "вид 8"),
                                (b"\x00\x52\xf0", "вид 0"), (b"", "вид -1")):
            with self.subTest(текст):
                self.assertEqual(bssap._ячейка(значение), текст)

    def test_чужое(self):
        for плохое, что in ((bssmap(0x57, tlv(0x05, ЯЧЕЙКА_CGI))[:-1], "длина не совпадает"),
                            (bssmap(0x09), "тип не назначен"), (bssmap(0x20, b"\x04\x05\x09"), "элемент длиннее"),
                            (bssmap(0x20, b"\x04"), "нет байта длины"), (bssmap(0x55, b"\x2c"), "TV короче"),
                            (b"\x00\x00", "нет типа"), (b"\x02\x01\x20", "дискриминатор")):
            with self.subTest(что):
                п = по_udt(плохое)
                self.assertEqual(п.стек, ["SCCP", "Данные"])
        self.assertEqual(по_dt1(b"abc").стек, ["SCCP", "Данные"])

    def test_dtap(self):
        l3 = b"\x05\x19" + bytes([len(личность(IMSI, 1))]) + личность(IMSI, 1)       # MM Identity Response
        п = по_dt1(dtap(l3, 0x80 | 3))
        self.assertEqual(п.стек[:3], ["SCCP", "DTAP", "GSM MM"])
        ф = поля(п, "DTAP")
        self.assertEqual((ф["gsm_a.dtap.dlci.sapi"].сырое, ф["gsm_a.dtap.dlci.cc"].сырое), (3, 2))
        self.assertEqual(п.уровни[1].длина, 3)
        self.assertEqual(п.уровни[2].смещение, п.уровни[1].смещение + 3)
        for плохое in (dtap(l3, 0x08), dtap(l3)[:-1], dtap(b"\x05\xff\x00"), dtap(b"\x05")):
            with self.subTest(плохое=плохое.hex()):
                self.assertEqual(по_dt1(плохое).стек, ["SCCP", "Данные"])

    def test_регистрация(self):
        self.assertIs(oks7.SCCP_ПОДСИСТЕМЫ[254], bssap.bssap)
        self.assertIn(bssap.bssap, oks7.SCCP_СОЕДИНЕНИЯ)
        self.assertEqual((ДОП_УРОВНИ["BSSMAP"], ДОП_УРОВНИ["DTAP"]), ("прикладной", "прикладной"))
        self.assertFalse(bssap.bssap(None, 5, 5))


if __name__ == "__main__":
    unittest.main()


def раскладка(п, протокол):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = []

    def обойти(список):
        for x in список:
            итог.append((x.ключ, x.смещение - у.смещение, x.длина, x.сырое))
            обойти(x.дети)
    обойти(у.поля)
    return итог


class ТестРаскладкиBSSMAP(unittest.TestCase):
    def test_заголовок_и_элементы(self):
        kc = bytes(range(8))
        элементы = (b"\x01" + struct.pack(">H", (3 << 5) | 17) + tlv(0x0A, b"\x82" + kc) + tlv(0x09, b"\xde\xad\xbe\xef")
                    + tlv(0x0B, b"\x11\x08") + b"\x2c\x03" + tlv(0x05, b"\x03") + tlv(0x60, b"\xaa\xbb\xcc\xdd"))
        данные = bssmap(0x53, элементы)
        п = по_dt1(данные)
        у = п.уровни[1]
        self.assertEqual((у.протокол, у.длина), ("BSSMAP", len(данные)))
        self.assertEqual(раскладка(п, "BSSMAP"), [
            ("gsm_a.bssmap.msgtype_discriminator", 0, 1, 0), ("bssap.length", 1, 1, len(данные) - 2),
            ("gsm_a.bssmap.msgtype", 2, 1, 0x53),
            ("gsm_a.bssmap.elem_id", 3, 3, 0x01), ("gsm_a.bssmap.cic", 4, 2, (3 << 5) | 17),
            ("gsm_a.bssmap.elem_id", 6, 11, 0x0A), ("gsm_a.bssmap.enc_info.permitted", 8, 1, 0x82),
            ("gsm_a.bssmap.enc_info_key", 9, 8, kc.hex()),
            ("gsm_a.bssmap.elem_id", 17, 6, 0x09), ("gsm_a.tmsi", 19, 4, "DEADBEEF"),
            ("gsm_a.bssmap.elem_id", 23, 4, 0x0B), ("gsm_a.bssmap.speech_data_ind", 25, 1, 1),
            ("gsm_a.bssmap.elem_id", 27, 2, 0x2C), ("gsm_a.bssmap.enc_alg", 28, 1, 3),
            ("gsm_a.bssmap.elem_id", 29, 3, 0x05), ("gsm_a.bssmap.cell_id", 31, 1, "без ячейки"),
            ("gsm_a.bssmap.elem_id", 32, 6, 0x60)])
        ф = поля(п)
        self.assertEqual(ф["gsm_a.bssmap.enc_info.permitted"].текст, "A5/1, A5/7")
        self.assertEqual(ф["gsm_a.bssmap.speech_data_ind"].текст, "речь")
        self.assertEqual(ф["gsm_a.bssmap.enc_alg"].текст, "A5/2")
        self.assertEqual(ф["gsm_a.bssmap.cell_id"].текст, "без ячейки")
        тексты = [x.текст for x in п.уровни[1].поля if x.ключ == "gsm_a.bssmap.elem_id"]
        self.assertEqual(тексты, ["мультиплексор 3, интервал 17", f"A5/1, A5/7; Kc {kc.hex()}", "DEADBEEF",
                                  "речь, вид канала 0x08", "A5/2", "без ячейки", "aabbccdd"])
        self.assertEqual(п.инфо, "BSSMAP Cipher Mode Command, шифры A5/1, A5/7, TMSI DEADBEEF, выбран A5/2, без ячейки")

    def test_короткие_значения(self):
        # Encryption Information: 1 октет — без ключа; 2 — ключ из одного октета.
        п = по_dt1(bssmap(0x53, tlv(0x0A, b"\x01") + tlv(0x0A, b"\x02\xaa")))
        тексты = [x.текст for x in п.уровни[1].поля if x.ключ == "gsm_a.bssmap.elem_id"]
        self.assertEqual(тексты, ["A5/0", "A5/1; Kc aa"])
        # Channel Type из двух октетов разбирается, из одного — нет.
        п = по_dt1(bssmap(0x01, tlv(0x0B, b"\x02\x09") + tlv(0x0B, b"\x03")))
        тексты = [x.текст for x in п.уровни[1].поля if x.ключ == "gsm_a.bssmap.elem_id"]
        self.assertEqual(тексты, ["данные, вид канала 0x09", "03"])
        # Элементы нулевой длины в конце и чужой элемент из одного октета.
        п = по_dt1(bssmap(0x20, tlv(0x60, b"\x07") + tlv(0x05, b"")))
        тексты = [x.текст for x in п.уровни[1].поля if x.ключ == "gsm_a.bssmap.elem_id"]
        self.assertEqual(тексты, ["07", ""])
        # Сообщение без элементов (3 октета) — BSSMAP.
        self.assertEqual(по_dt1(bssmap(0x55)).стек[:2], ["SCCP", "BSSMAP"])
        # Длина не сходится, хотя элементы сами по себе целы — не BSSMAP.
        self.assertEqual(по_dt1(b"\x00\x05\x55\x2c\x02").стек, ["SCCP", "Данные"])

    def test_личность_в_0x29_и_l3_в_0x20(self):
        п = по_dt1(bssmap(0x57, tlv(0x29, личность(IMSI, 1))))
        ф = поля(п)
        self.assertEqual((ф["e212.imsi"].имя, ф["e212.imsi"].текст), ("IMSI", IMSI))
        п = по_dt1(bssmap(0x2F, tlv(0x08, b"\xf4\x01\x02\x03\x04")))
        ф = поля(п)
        self.assertEqual((ф["gsm_a.bssmap.mobile_identity"].имя, ф["gsm_a.bssmap.mobile_identity"].текст),
                         ("TMSI", "01020304"))
        п = по_dt1(bssmap(0x2D, tlv(0x20, LU)))
        self.assertEqual(п.стек[:3], ["SCCP", "BSSMAP", "GSM MM"])

    def test_ячейки_коротких_длин(self):
        for значение, текст in ((b"\x00" + bytes(6), "вид 0"), (b"\x01" + bytes(3), "вид 1"), (b"\x02\x00", "вид 2"),
                                (b"\x04" + bytes(4), "вид 4"), (b"\x05\x00", "вид 5"),
                                (b"\x05\x12\x34\x00\x00\x00", "LAC 4660")):
            with self.subTest(текст):
                self.assertEqual(bssap._ячейка(значение), текст)


class ТестРаскладкиDTAP(unittest.TestCase):
    def test_заголовок(self):
        l3 = b"\x05\x1b"                                             # TMSI Reallocation Complete — 2 октета
        п = по_dt1(dtap(l3, 0x80 | 3))
        self.assertEqual(п.стек[:3], ["SCCP", "DTAP", "GSM MM"])
        self.assertEqual(раскладка(п, "DTAP"), [
            ("gsm_a.bssmap.msgtype_discriminator", 0, 1, 1), ("gsm_a.dtap.dlci.sapi", 1, 1, 3),
            ("gsm_a.dtap.dlci.cc", 1, 1, 2), ("bssap.length", 2, 1, 2)])
        self.assertEqual(поля(п, "DTAP")["gsm_a.bssmap.msgtype_discriminator"].текст, "DTAP")
        self.assertEqual(по_dt1(dtap(b"\x05")).стек, ["SCCP", "Данные"])


class ТестГраницПрямо(unittest.TestCase):
    """Прямые вызовы: за концом данных (там, где в пакете дальше идут чужие байты) не читается."""

    def test_элементы_и_заголовок(self):
        self.assertIsNone(bssap._элементы(b"\x04", 0, 1))
        self.assertIsNone(bssap._элементы(b"\x04\x05", 0, 1))
        self.assertEqual(bssap._элементы(b"\x04\x00", 0, 2), [(4, 2, 0, 0)])
        р = Разбор(Пакет(1, 0.0, b"\x00\x00\x01", 3, "RAW"))
        self.assertFalse(bssap.bssap(р, 0, 2))
        self.assertEqual(р.п.уровни, [])
        р = Разбор(Пакет(1, 0.0, b"\x00\x01\x55", 3, "RAW"))
        self.assertTrue(bssap.bssap(р, 0, 3))
