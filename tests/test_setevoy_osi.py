"""OSI по DCC и LLC: CLNP (ISO 8473-1), ES-IS (ISO 9542), транспорт ISO 8073 (TP4) и TL1. PDU собираются по
стандартам; контрольная сумма ставится алгоритмом ISO 8473 прил. C здесь же — независимо от разбора."""

import struct
import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy.pole import Пакет
from reportgen.setevoy.protokoly import kanalnye, osi
from reportgen.setevoy.razbor import ДОП_УРОВНИ, Разбор, разобрать_пакет
from test_setevoy_abis import lapd_i
from test_setevoy_kanalnye import q922, кадр_802_3

NSAP_А = bytes.fromhex("49000100000000000a01")      # 49.0001.0000.0000.000a.01 (NET-подобный, 10 октетов)
NSAP_Б = bytes.fromhex("3900010203040506070809aabbccddeeff00112201")


def с_суммой(pdu, место):
    """ISO 8473 прил. C: X и Y в октеты место, место + 1 (от нуля), чтобы суммы Флетчера дали 0."""
    б = bytearray(pdu)
    б[место:место + 2] = b"\x00\x00"
    длина = б[1] if б[0] in (0x81, 0x82) else len(б)
    c0 = c1 = 0
    for x in б[:длина]:
        c0 = (c0 + x) % 255
        c1 = (c1 + c0) % 255
    n = место + 1
    x = ((длина - n) * c0 - c1) % 255
    y = ((длина - n + 1) * (-c0) + c1) % 255
    б[место:место + 2] = bytes([x or 255, y or 255])
    return bytes(б)


def clnp(тип, данные=b"", куда=NSAP_А, откуда=NSAP_Б, жизнь=0x40, параметры=b"", сегмент=None, сумма=True):
    адреса = bytes([len(куда)]) + куда + bytes([len(откуда)]) + откуда
    li = 9 + len(адреса) + (6 if тип & 0x80 else 0) + len(параметры)
    if тип & 0x80 and сегмент is None:                   # SP = 1: часть сегментации обязательна
        сегмент = (7, 0, li + len(данные))
    сегм = struct.pack(">HHH", *сегмент) if тип & 0x80 else b""
    заголовок = bytes([0x81, li, 1, жизнь, тип]) + struct.pack(">H", li + len(данные)) + b"\x00\x00" + адреса \
        + сегм + параметры
    return (с_суммой(заголовок, 7) if сумма else заголовок) + данные


def tp_cr(tsap_от=b"OS1", tsap_к=b"NE1", кредит=0, сумма=True):
    параметры = bytes([0xC1, len(tsap_от)]) + tsap_от + bytes([0xC2, len(tsap_к)]) + tsap_к + b"\xc0\x01\x0b"
    if сумма:
        параметры += b"\xc3\x02\x00\x00"
    tpdu = bytes([6 + len(параметры), 0xE0 | кредит]) + struct.pack(">HH", 0, 0x1234) + b"\x40" + параметры
    if сумма:
        tpdu = с_суммой(tpdu, len(tpdu) - 2)
    return tpdu


def tp_dt(данные, ссылка=0x5678, номер=3, eot=1):
    return bytes([4, 0xF0]) + struct.pack(">H", ссылка) + bytes([eot << 7 | номер]) + данные


def по_llc(pdu):
    return разобрать_пакет(кадр_802_3(b"\xfe\xfe\x03" + pdu, dst="09:00:2b:00:00:04"))


def р_из(данные, сдвиг=16):
    return Разбор(Пакет(1, 0.0, bytes(сдвиг) + данные, сдвиг + len(данные), "RAW"))


def уровень(п, протокол):
    return [x for x in п.уровни if x.протокол == протокол][0]


def поля(п, протокол):
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(уровень(п, протокол).поля)
    return итог


class ТестCLNP(unittest.TestCase):
    def test_dt_с_tp4_cr(self):
        pdu = clnp(0x9C, tp_cr())
        п = по_llc(pdu)
        self.assertEqual(п.стек[-2:], ["CLNP", "TP"])
        self.assertEqual(п.ошибки, [])
        ф = поля(п, "CLNP")
        self.assertEqual((ф["clnp.type"].текст, ф["clnp.cnf.segmentation"].сырое, ф["clnp.cnf.report_error"].сырое),
                         ("DT (данные)", 1, 0))
        self.assertEqual(ф["clnp.ttl"].текст, "64 (32 с)")
        self.assertEqual(ф["clnp.dsap"].текст, "49.0001.0000.0000.000a.01")
        self.assertEqual(ф["clnp.ssap"].текст, "39.0001.0203.0405.0607.0809.aabb.cc.ddee.ff00.1122.01")
        self.assertTrue(ф["clnp.checksum"].текст.endswith("(верна)"))
        н = уровень(п, "CLNP").смещение
        self.assertEqual([(x.ключ, x.смещение - н, x.длина) for x in уровень(п, "CLNP").поля][:5],
                         [("clnp.nlpi", 0, 1), ("clnp.len", 1, 1), ("clnp.version", 2, 1), ("clnp.ttl", 3, 1),
                          ("clnp.type", 4, 1)])
        self.assertEqual((ф["clnp.pdu.len"].смещение - н, ф["clnp.checksum"].смещение - н, ф["clnp.dsap"].смещение - н),
                         (5, 7, 10))
        self.assertEqual(уровень(п, "CLNP").длина, pdu[1])
        т = поля(п, "TP")
        self.assertEqual((т["cotp.type"].текст, т["cotp.class"].сырое, т["cotp.srcref"].сырое), ("CR Connect Request", 4, 0x1234))
        self.assertEqual((т["cotp.src-tsap"].текст, т["cotp.dst-tsap"].текст, т["cotp.tpdu-size"].сырое), ("OS1", "NE1", 2048))
        self.assertTrue(т["cotp.checksum"].текст.endswith("(верна)"))
        self.assertEqual(п.инфо, "TP CR, класс 4, TSAP вызывающего OS1, TSAP вызываемого NE1")

    def test_контрольные_суммы(self):
        испорчено = bytearray(clnp(0x9C, tp_cr()))
        испорчено[3] ^= 1
        п = по_llc(bytes(испорчено))
        self.assertEqual(п.ошибки, ["CLNP: контрольная сумма не сходится"])
        self.assertTrue(поля(п, "CLNP")["clnp.checksum"].плохо)
        п = по_llc(clnp(0x9C, tp_cr(), сумма=False))
        self.assertEqual(поля(п, "CLNP")["clnp.checksum"].текст, "0x0000 (не считается)")
        tpdu = bytearray(tp_cr())
        tpdu[5] ^= 0xFF
        п = по_llc(clnp(0x1C, bytes(tpdu)))
        self.assertEqual(п.ошибки, ["ISO 8073: контрольная сумма не сходится"])
        self.assertEqual(osi.флетчер(b""), (0, 0))
        self.assertEqual(osi.флетчер(b"\x01\x02"), (3, 4))

    def test_er_сегменты_эхо(self):
        отброшенный = clnp(0x1C, b"", сумма=False)
        параметры = b"\xc1\x02\x02\x07"                       # общий класс, неверная контрольная сумма, октет 7
        п = по_llc(clnp(0x01, отброшенный, параметры=параметры))
        self.assertEqual([у.протокол for у in п.уровни][-2:], ["CLNP", "CLNP"])
        ф = поля(п, "CLNP")
        self.assertEqual(ф["clnp.option"].текст, "General: Incorrect checksum, октет 7")
        self.assertTrue(п.инфо.endswith("причина: General: Incorrect checksum, октет 7"), п.инфо)
        self.assertEqual(osi.nsap(b""), "—")
        п = по_llc(clnp(0xDC, b"part", сегмент=(1, 0, 100)))
        ф = поля(п, "CLNP")
        self.assertEqual((ф["clnp.data_unit_identifier"].сырое, ф["clnp.total_length"].сырое), (1, 100))
        self.assertEqual(уровень(п, "Данные").полное, "Данные: CLNP: сегмент")
        п = по_llc(clnp(0x9E, b"echo"))
        self.assertTrue(п.инфо.startswith("CLNP ERQ"), п.инфо)
        self.assertEqual(уровень(п, "Данные").полное, "Данные: CLNP: данные")
        п = по_llc(clnp(0x9C, b"\x00\x01"))
        self.assertEqual(уровень(п, "Данные").полное, "Данные: CLNP: данные")

    def test_отказы(self):
        хорошо = clnp(0x9C, tp_cr())
        for плохое, что in ((b"\x81" + хорошо[1:2] + b"\x02" + хорошо[3:], "версия 2"),
                            (хорошо[:4] + b"\x9b" + хорошо[5:], "тип 0x1B"),
                            (хорошо[:5] + b"\xff\xff" + хорошо[7:], "длина сегмента больше данных"),
                            (хорошо[:5] + b"\x00\x05" + хорошо[7:], "длина сегмента меньше заголовка"),
                            (хорошо[:1] + b"\x09" + хорошо[2:], "заголовок 9"),
                            (хорошо[:9], "короче 10"), (b"\x81\x0a\x01\x00\x9c\x00\x0a\x00\x00\x10", "адрес длиннее"),
                            (bytes([0x81, 12, 1, 0, 0x9C, 0, 12, 0, 0, 1, 0xAA, 3]), "адрес источника длиннее"),
                            (bytes([0x81, 12, 1, 0, 0x9C, 0, 12, 0, 0, 1, 0xAA, 0]) + bytes(0), "нет места под сегментацию")):
            with self.subTest(что):
                р = р_из(плохое)
                self.assertFalse(osi.clnp(р, 16, 16 + len(плохое)))
                self.assertEqual(р.п.уровни, [])


class ТестESIS(unittest.TestCase):
    def esis(self, тип, тело, удержание=30):
        li = 9 + len(тело)
        return с_суммой(bytes([0x82, li, 1, 0, тип]) + struct.pack(">H", удержание) + b"\x00\x00" + тело, 7)

    def test_esh_ish_rd(self):
        п = по_llc(self.esis(2, bytes([2, len(NSAP_А)]) + NSAP_А + bytes([len(NSAP_Б)]) + NSAP_Б))
        self.assertEqual(п.стек[-1], "ES-IS")
        self.assertEqual(п.ошибки, [])
        ф = поля(п, "ES-IS")
        self.assertEqual((ф["esis.type"].сырое, ф["esis.htime"].текст), (2, "30 с"))
        self.assertTrue(ф["esis.chksum"].текст.endswith("(верна)"))
        self.assertEqual(п.инфо, "ES-IS ESH, NSAP 49.0001.0000.0000.000a.01, "
                                 "NSAP 39.0001.0203.0405.0607.0809.aabb.cc.ddee.ff00.1122.01, удержание 30 с")
        п = по_llc(self.esis(4, bytes([len(NSAP_А)]) + NSAP_А, 60))
        self.assertEqual(п.инфо, "ES-IS ISH, NET 49.0001.0000.0000.000a.01, удержание 60 с")
        snpa = bytes.fromhex("0000c0010203")
        п = по_llc(self.esis(6, bytes([len(NSAP_Б)]) + NSAP_Б + bytes([6]) + snpa + b"\x00"))
        self.assertEqual(п.инфо, "ES-IS RD, Назначение 39.0001.0203.0405.0607.0809.aabb.cc.ddee.ff00.1122.01, "
                                 "SNPA 00:00:c0:01:02:03, NET —, удержание 30 с")
        п = по_llc(self.esis(6, bytes([2]) + b"\x49\x01" + bytes([6]) + snpa))
        self.assertTrue(п.инфо.startswith("ES-IS RD, Назначение 49.01, SNPA"), п.инфо)
        for плохое in (self.esis(2, b"\x02\x01\xaa"), self.esis(8, b"\x01\xaa"), self.esis(4, b"\x05\xaa"),
                       self.esis(6, b"\x01\xaa")):
            р = р_из(плохое)
            self.assertFalse(osi.esis(р, 16, 16 + len(плохое)), плохое.hex())


class ТестTP(unittest.TestCase):
    def test_сцепление_и_tl1(self):
        ak = bytes([4, 0x62]) + struct.pack(">H", 0x5678) + b"\x05"
        команда = b"ACT-USER:NE1:ADMIN:100::SECRET;"
        п = по_llc(clnp(0x9C, ak + tp_dt(команда)))
        self.assertEqual(п.стек[-3:], ["TP", "TP", "TL1"])
        т = [у for у in п.уровни if у.протокол == "TP"]
        self.assertEqual([у.итог for у in т], ["AK, YR 5", "DT, N 3"])
        self.assertEqual(поля(п, "TP")["cotp.cdt"].сырое, 2)
        tl = поля(п, "TL1")
        self.assertEqual((tl["tl1.cmd_code"].текст, tl["tl1.tid"].текст, tl["tl1.aid"].текст, tl["tl1.ctag"].текст),
                         ("ACT-USER", "NE1", "ADMIN", "100"))
        self.assertEqual(п.инфо, "TL1 команда ACT-USER, TID NE1, CTAG 100")
        ответ = b"\r\n\n   NE1 06-07-15 12:00:00\r\nM  100 COMPLD\r\n;"
        п = по_llc(clnp(0x9C, tp_dt(ответ)))
        self.assertEqual(п.инфо, "TL1 ответ CTAG 100: COMPLD")
        авария = b"\r\n\n   NE1 06-07-15 12:00:00\r\n*C 17 REPT ALM EC1\r\n   \"FAC-1:CR,LOS,SA\"\r\n;"
        п = по_llc(clnp(0x9C, tp_dt(авария)))
        self.assertEqual(п.инфо, "TL1 автономное *C ATAG 17 REPT")
        п = по_llc(clnp(0x9C, ak + tp_dt(b"\x09\x00")))            # сеанс ISO 8327: FINISH
        self.assertEqual(п.стек[-3:], ["TP", "TP", "SES"])
        self.assertFalse(п.инфо.startswith("TP"), п.инфо)
        # Не TL1: не текст, без «;», без узнаваемой строки.
        for тело in (b"\x01\x02\x03", b"hello world", b"just text;"):
            п = по_llc(clnp(0x9C, tp_dt(тело)))
            self.assertEqual(уровень(п, "Данные").полное, "Данные: ISO 8073: данные пользователя", тело)

    def test_прочие_tpdu(self):
        dr = bytes([6, 0x80]) + struct.pack(">HH", 1, 2) + b"\x00"
        п = по_llc(clnp(0x9C, dr))
        self.assertTrue(п.инфо.startswith("TP DR, причина:"), п.инфо)
        er = bytes([4, 0x70]) + struct.pack(">H", 1) + b"\x01"
        п = по_llc(clnp(0x9C, er))
        self.assertTrue(п.инфо.startswith("TP ER, причина:"), п.инфо)
        dc = bytes([5, 0xC0]) + struct.pack(">HH", 1, 2)
        self.assertEqual(по_llc(clnp(0x9C, dc)).инфо, "TP DC")
        dt0 = bytes([2, 0xF0, 0x80]) + b"x"                      # класс 0: без ссылки
        п = по_llc(clnp(0x9C, dt0))
        self.assertEqual(уровень(п, "TP").итог, "DT, N 0")
        self.assertNotIn("cotp.destref", поля(п, "TP"))
        п = по_llc(clnp(0x9C, tp_dt(b"frag", eot=0)))
        self.assertEqual(уровень(п, "Данные").полное, "Данные: ISO 8073: данные DT")
        for плохое in (bytes([3, 0xF0, 0, 0]), bytes([5, 0xE0, 0, 0, 0, 0]), bytes([4, 0x30, 0, 0, 0]),
                       bytes([4, 0xF0, 0, 0]), bytes([7, 0xE0, 0, 0, 0, 0, 0x40, 0xC1])):
            р = р_из(плохое)
            self.assertFalse(osi.tp(р, 16, 16 + len(плохое)), плохое.hex())


class ТестПути(unittest.TestCase):
    def test_lapd_dcc_и_fr(self):
        pdu = clnp(0x9C, tp_cr())
        п = разобрать_пакет(lapd_i(pdu), "LAPD")
        self.assertEqual(п.стек, ["LAPD", "CLNP", "TP"])
        п = разобрать_пакет(q922(100) + b"\x03\x81" + pdu[1:], "Frame Relay")
        self.assertEqual(п.стек[:3], ["FR", "CLNP", "TP"])
        п = разобрать_пакет(q922(100) + b"\x03\x82\x05", "Frame Relay")
        self.assertEqual(п.стек, ["FR", "Данные"])
        self.assertEqual(уровень(п, "Данные").полное, "Данные: ES-IS (не разобран)")

    def test_регистрация(self):
        self.assertIs(kanalnye.OSI_NLPID[0x81], osi.clnp)
        self.assertIs(kanalnye.OSI_NLPID[0x82], osi.esis)
        self.assertIs(kanalnye.OSI_NLPID[0x83], kanalnye.isis)
        self.assertEqual([ДОП_УРОВНИ[x] for x in ("CLNP", "ES-IS", "TP", "TL1")],
                         ["сетевой", "сетевой", "транспортный", "прикладной"])
        self.assertEqual(osi.nsap(b"\x49\x00\x01"), "49.0001")
        self.assertEqual(len(osi.TP_ПАРАМЕТРЫ), 21)


if __name__ == "__main__":
    unittest.main()
