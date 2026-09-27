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

    def test_osi_pdu_прямо(self):
        """Не OSI по первому октету и пустой участок — «нет» без уровней; отказ разборщика откатывается."""
        for данные, конец in ((b"\x08\x01\x02", 3), (b"\x81", 0), (b"\x81\x05\x02", 3)):
            р = р_из(данные)
            self.assertFalse(kanalnye.osi_pdu(р, 16, 16 + конец), данные.hex())
            self.assertEqual(р.п.уровни, [])
        р = р_из(clnp(0x1C, b""))
        self.assertTrue(kanalnye.osi_pdu(р, 16, 16 + len(clnp(0x1C, b""))))


if __name__ == "__main__":
    unittest.main()


# -- эталоны раскладки: каждое поле — место, длина, сырое значение (по построению PDU) -----------------

С = 16
ХВОСТ = b"\xee" * 4


def р_хвост(pdu):
    """PDU с С байт впереди и чужими байтами за концом."""
    return Разбор(Пакет(1, 0.0, bytes(С) + pdu + ХВОСТ, С + len(pdu) + len(ХВОСТ), "RAW"))


def дерево(р):
    """[(протокол, место, длина), [(глубина, ключ, место, длина, сырое)]] по уровням, места — от начала PDU."""
    def обойти(список, глубина, куда):
        for x in список:
            куда.append((глубина, x.ключ, x.смещение - С, x.длина, x.сырое))
            обойти(x.дети, глубина + 1, куда)
        return куда
    return [((у.протокол, у.смещение - С, у.длина), обойти(у.поля, 0, [])) for у in р.п.уровни]


def cr_с_параметрами():
    параметры = (b"\xc1\x03OS1" + b"\xc2\x03NE1" + b"\xc0\x01\x0b" + b"\x85\x01\x07" + b"\xc3\x02\x00\x00")
    tpdu = bytes([6 + len(параметры), 0xE3]) + b"\x00\x00\x12\x34" + b"\x42" + параметры
    return с_суммой(tpdu, len(tpdu) - 2)


class ЭталонOSI(unittest.TestCase):
    def test_clnp_dt_с_сегментацией_и_tp_cr(self):
        tpdu = cr_с_параметрами()
        откуда = b"\x49\x00\x01"
        pdu = clnp(0xBC, tpdu, откуда=откуда, параметры=b"\x05\x02\xab\xcd", сегмент=(7, 0, 34 + len(tpdu)))
        self.assertEqual(pdu[1], 34)
        р = р_хвост(pdu)
        self.assertTrue(osi.clnp(р, С, С + len(pdu)))
        сумма, сумма_tp = int.from_bytes(pdu[7:9], "big"), int.from_bytes(tpdu[-2:], "big")
        self.assertEqual(дерево(р), [
            (("CLNP", 0, 34), [
                (0, "clnp.nlpi", 0, 1, 0x81), (0, "clnp.len", 1, 1, 34), (0, "clnp.version", 2, 1, 1),
                (0, "clnp.ttl", 3, 1, 0x40), (0, "clnp.type", 4, 1, 0xBC), (1, "clnp.cnf.segmentation", 4, 1, 1),
                (1, "clnp.cnf.more_segments", 4, 1, 0), (1, "clnp.cnf.report_error", 4, 1, 1),
                (0, "clnp.pdu.len", 5, 2, len(pdu)), (0, "clnp.checksum", 7, 2, сумма),
                (0, "clnp.dsap.len", 9, 1, 10), (0, "clnp.dsap", 10, 10, "49.0001.0000.0000.000a.01"),
                (0, "clnp.ssap.len", 20, 1, 3), (0, "clnp.ssap", 21, 3, "49.0001"),
                (0, "clnp.data_unit_identifier", 24, 2, 7), (0, "clnp.segment_offset", 26, 2, 0),
                (0, "clnp.total_length", 28, 2, len(pdu)), (0, "clnp.option", 30, 4, 0x05)]),
            (("TP", 34, tpdu[0] + 1), [
                (0, "cotp.li", 34, 1, tpdu[0]), (0, "cotp.type", 35, 1, 0xE), (0, "cotp.cdt", 35, 1, 3),
                (0, "cotp.destref", 36, 2, 0), (0, "cotp.srcref", 38, 2, 0x1234), (0, "cotp.class", 40, 1, 4),
                (0, "cotp.opts", 40, 1, 2), (0, "cotp.src-tsap", 41, 5, "OS1"), (0, "cotp.dst-tsap", 46, 5, "NE1"),
                (0, "cotp.tpdu-size", 51, 3, 2048), (0, "cotp.parameter", 54, 3, 0x85),
                (0, "cotp.checksum", 57 + 2, 2, сумма_tp)])])
        self.assertEqual(р.п.ошибки, [])
        self.assertEqual(р.п.уровни[0].итог, "DT 49.0001 → 49.0001.0000.0000.000a.01")

    def test_clnp_er_и_параметры(self):
        отброшенный = clnp(0x1C, b"", сумма=False)
        pdu = clnp(0x01, отброшенный, параметры=b"\xc1\x02\x02\x07" + b"\xcd\x00")
        р = р_хвост(pdu)
        self.assertTrue(osi.clnp(р, С, С + len(pdu)))
        уровни = дерево(р)
        li = pdu[1]
        self.assertEqual([у[0] for у in уровни], [("CLNP", 0, li), ("CLNP", li, отброшенный[1])])
        self.assertEqual(уровни[0][1][-2:], [(0, "clnp.option", li - 6, 4, 0xC1), (0, "clnp.option", li - 2, 2, 0xCD)])
        self.assertEqual(р.п.инфо, "CLNP ER 39.0001.0203.0405.0607.0809.aabb.cc.ddee.ff00.1122.01 → "
                                   "49.0001.0000.0000.000a.01, причина: General: Incorrect checksum, октет 7")
        # Причина из одного октета — без места; параметр длиннее заголовка — ошибка.
        pdu = clnp(0x01, b"", параметры=b"\xc1\x01\x02")
        р = р_хвост(pdu)
        osi.clnp(р, С, С + len(pdu))
        self.assertTrue(р.п.инфо.endswith("причина: General: Incorrect checksum"), р.п.инфо)
        pdu = bytearray(clnp(0x01, b"", параметры=b"\xc1\x05\x02", сумма=False))
        р = р_хвост(bytes(pdu))
        osi.clnp(р, С, С + len(pdu))
        self.assertEqual(р.п.ошибки, ["CLNP: параметр длиннее заголовка"])
        # Неизвестные класс и код — числами.
        pdu = clnp(0x01, b"", параметры=b"\xc1\x02\x7f\x01")
        р = р_хвост(pdu)
        osi.clnp(р, С, С + len(pdu))
        self.assertTrue(р.п.инфо.endswith("причина: класс 7: код 15, октет 1"), р.п.инфо)

    def test_clnp_данные_по_видам(self):
        for тип, данные_, вид, сегмент in ((0xDC, b"part", "Данные: CLNP: сегмент", (1, 0, 0)),
                                            (0x9C, b"rest", "Данные: CLNP: сегмент", (1, 8, 0)),
                                            (0x9E, b"echo", "Данные: CLNP: данные", None),
                                            (0x1D, b"\x01\x02", "Данные: CLNP: данные", None),
                                            (0x1F, b"x", "Данные: CLNP: данные", None)):
            pdu = clnp(тип, данные_, сегмент=сегмент and (сегмент[0], сегмент[1], 0))
            if сегмент:
                pdu = clnp(тип, данные_, сегмент=(сегмент[0], сегмент[1], len(pdu)))
            р = р_хвост(pdu)
            self.assertTrue(osi.clnp(р, С, С + len(pdu)))
            последний = р.п.уровни[-1]
            self.assertEqual((последний.полное, последний.смещение - С, последний.длина),
                             (вид, pdu[1], len(данные_)), hex(тип))
        # Без данных — только заголовок.
        pdu = clnp(0x1C, b"")
        р = р_хвост(pdu)
        self.assertTrue(osi.clnp(р, С, С + len(pdu)))
        self.assertEqual([у.протокол for у in р.п.уровни], ["CLNP"])

    def test_clnp_границы(self):
        хорошо = clnp(0x1C, b"")                             # заголовок = весь PDU
        р = р_хвост(хорошо)
        self.assertTrue(osi.clnp(р, С, С + len(хорошо)))
        self.assertFalse(osi.clnp(р_хвост(хорошо), С, С + len(хорошо) - 1))
        # 10 октетов не вмещают двух длин адресов — нет; 11 — два пустых адреса.
        for li, да in ((10, False), (11, True), (12, True)):
            pdu = bytes([0x81, li, 1, 0, 0x1C]) + li.to_bytes(2, "big") + b"\x00\x00" + bytes(li - 9)
            self.assertEqual(osi.clnp(р_хвост(pdu), С, С + li), да, li)
        # Адрес источника доходит до края заголовка ровно — да; на октет дальше — нет.
        pdu = bytes([0x81, 13, 1, 0, 0x1C, 0, 13, 0, 0, 1, 0xAA, 1, 0xBB])
        self.assertTrue(osi.clnp(р_хвост(pdu), С, С + 13))
        # Часть сегментации ровно помещается — да; не помещается — нет.
        pdu = bytes([0x81, 17, 1, 0, 0x9C, 0, 17, 0, 0, 0, 0]) + bytes(6)
        self.assertTrue(osi.clnp(р_хвост(pdu), С, С + 17))
        pdu = bytes([0x81, 16, 1, 0, 0x9C, 0, 16, 0, 0, 0, 0]) + bytes(5)
        self.assertFalse(osi.clnp(р_хвост(pdu), С, С + 16))
        # Длина сегмента больше конца — нет; тип вне таблицы — нет; не 0x81 — нет.
        for плохое in (bytes([0x82]) + хорошо[1:], хорошо[:4] + b"\x02" + хорошо[5:]):
            self.assertFalse(osi.clnp(р_хвост(плохое), С, С + len(плохое)), плохое.hex())

    def test_nsap_и_флетчер(self):
        self.assertEqual(osi.nsap(bytes(range(1, 8))), "01.0203.0405.0607")
        self.assertEqual(osi.nsap(bytes(range(1, 9))), "01.0203.0405.0607.08")
        self.assertEqual(osi.nsap(bytes(range(1, 10))), "01.02.0304.0506.0708.09")
        self.assertEqual(osi.флетчер(bytes([254, 1, 3])), (3, 2))
        self.assertEqual(osi.флетчер(bytes([200, 100])), (45, 245))

    def test_esis_раскладка(self):
        тело = bytes([2, len(NSAP_А)]) + NSAP_А + bytes([3]) + b"\x49\x00\x01" + b"\xc5\x01\x09"
        pdu = с_суммой(bytes([0x82, 9 + len(тело), 1, 0, 2, 0, 30, 0, 0]) + тело, 7)
        р = р_хвост(pdu)
        self.assertTrue(osi.esis(р, С, С + len(pdu)))
        li = pdu[1]
        self.assertEqual(дерево(р), [(("ES-IS", 0, li), [
            (0, "esis.nlpi", 0, 1, 0x82), (0, "esis.length", 1, 1, li), (0, "esis.ver", 2, 1, 1),
            (0, "esis.type", 4, 1, 2), (0, "esis.htime", 5, 2, 30), (0, "esis.chksum", 7, 2, int.from_bytes(pdu[7:9], "big")),
            (0, "esis.nsap", 10, 11, "49.0001.0000.0000.000a.01"), (0, "esis.nsap", 21, 4, "49.0001"),
            (0, "clnp.option", 25, 3, 0xC5)])])
        # RD без NET: SNPA — октетами через двоеточие.
        тело = bytes([3]) + b"\x49\x00\x01" + bytes([2]) + b"\xab\xcd"
        pdu = с_суммой(bytes([0x82, 9 + len(тело), 1, 0, 6, 0, 5, 0, 0]) + тело, 7)
        р = р_хвост(pdu)
        self.assertTrue(osi.esis(р, С, С + len(pdu)))
        self.assertEqual(дерево(р)[0][1][-2:], [(0, "esis.nsap", 9, 4, "49.0001"), (0, "esis.bsnpa", 13, 3, "ab:cd")])
        self.assertEqual(р.п.инфо, "ES-IS RD, Назначение 49.0001, SNPA ab:cd, удержание 5 с")
        # После PDU — данные; PDU ровно до конца — без них.
        р = р_хвост(pdu + b"zz")
        self.assertTrue(osi.esis(р, С, С + len(pdu) + 2))
        self.assertEqual((р.п.уровни[-1].протокол, р.п.уровни[-1].смещение - С, р.п.уровни[-1].длина),
                         ("Данные", len(pdu), 2))
        # Границы: RD без SNPA — нет; ESH: адрес ровно до края — да, за краем — нет; LI 9 — нет.
        тело = bytes([3]) + b"\x49\x00\x01"
        pdu = с_суммой(bytes([0x82, 9 + len(тело), 1, 0, 6, 0, 5, 0, 0]) + тело, 7)
        self.assertFalse(osi.esis(р_хвост(pdu), С, С + len(pdu)))
        pdu = bytes([0x82, 12, 1, 0, 4, 0, 5, 0, 0, 2, 0x49, 0x01])
        self.assertTrue(osi.esis(р_хвост(pdu), С, С + 12))
        pdu = bytes([0x82, 12, 1, 0, 4, 0, 5, 0, 0, 3, 0x49, 0x01])
        self.assertFalse(osi.esis(р_хвост(pdu), С, С + 12))
        pdu = bytes([0x82, 9, 1, 0, 4, 0, 5, 0, 0, 0])
        self.assertFalse(osi.esis(р_хвост(pdu), С, С + 10))
        pdu = bytes([0x82, 12, 1, 0, 4, 0, 5, 0, 0, 2, 0x49, 0x01])
        self.assertFalse(osi.esis(р_хвост(pdu), С, С + 11))

    def test_tp_раскладка_видов(self):
        случаи = (
            (bytes([2, 0xF0, 0x85]) + b"ab", "DT", [(0, "cotp.li", 0, 1, 2), (0, "cotp.type", 1, 1, 0xF),
                                                    (0, "cotp.tpdu-number", 2, 1, 5), (0, "cotp.eot", 2, 1, 1)]),
            (bytes([6, 0x80, 0, 1, 0, 2, 3]) + b"zz", "DR", [(0, "cotp.li", 0, 1, 6), (0, "cotp.type", 1, 1, 8),
                                                              (0, "cotp.destref", 2, 2, 1), (0, "cotp.srcref", 4, 2, 2),
                                                              (0, "cotp.dr_reason", 6, 1, 3)]),
            (bytes([4, 0x70, 0, 1, 2]), "ER", [(0, "cotp.li", 0, 1, 4), (0, "cotp.type", 1, 1, 7),
                                              (0, "cotp.destref", 2, 2, 1), (0, "cotp.reject_cause", 4, 1, 2)]),
            (bytes([4, 0x51, 0, 1, 0x86]), "RJ", [(0, "cotp.li", 0, 1, 4), (0, "cotp.type", 1, 1, 5),
                                                 (0, "cotp.cdt", 1, 1, 1), (0, "cotp.destref", 2, 2, 1),
                                                 (0, "cotp.next-tpdu-number", 4, 1, 6)]),
            (bytes([4, 0x20, 0, 1, 0x07]), "EA", [(0, "cotp.li", 0, 1, 4), (0, "cotp.type", 1, 1, 2),
                                                 (0, "cotp.destref", 2, 2, 1), (0, "cotp.next-tpdu-number", 4, 1, 7)]),
            (bytes([4, 0x10, 0, 1, 0x03]) + b"q", "ED", [(0, "cotp.li", 0, 1, 4), (0, "cotp.type", 1, 1, 1),
                                                        (0, "cotp.destref", 2, 2, 1), (0, "cotp.tpdu-number", 4, 1, 3),
                                                        (0, "cotp.eot", 4, 1, 0)]),
            (bytes([6, 0xD5, 0, 1, 0, 2, 0x20]), "CC", [(0, "cotp.li", 0, 1, 6), (0, "cotp.type", 1, 1, 0xD),
                                                       (0, "cotp.cdt", 1, 1, 5), (0, "cotp.destref", 2, 2, 1),
                                                       (0, "cotp.srcref", 4, 2, 2), (0, "cotp.class", 6, 1, 2),
                                                       (0, "cotp.opts", 6, 1, 0)]),
            (bytes([5, 0xC0, 0, 1, 0, 2]), "DC", [(0, "cotp.li", 0, 1, 5), (0, "cotp.type", 1, 1, 0xC),
                                                 (0, "cotp.destref", 2, 2, 1), (0, "cotp.srcref", 4, 2, 2)]),
        )
        for tpdu, вид, поля_ in случаи:
            with self.subTest(вид):
                р = р_хвост(tpdu)
                self.assertTrue(osi.tp(р, С, С + len(tpdu)))
                у = дерево(р)
                self.assertEqual(у[0], (("TP", 0, tpdu[0] + 1), поля_))
                if len(tpdu) > tpdu[0] + 1:
                    self.assertEqual(у[-1][0][1:], (tpdu[0] + 1, len(tpdu) - tpdu[0] - 1))
        # Не TPDU после первого — данные.
        tpdu = bytes([4, 0x60, 0, 1, 0]) + b"\x09\xff"
        р = р_хвост(tpdu)
        self.assertTrue(osi.tp(р, С, С + len(tpdu)))
        self.assertEqual((р.п.уровни[-1].полное, р.п.уровни[-1].смещение - С), ("Данные: ISO 8073: не TPDU", 5))

    def test_tpdu_границы(self):
        т = osi._tpdu
        self.assertEqual(т(bytes([4, 0x60, 0, 1, 0]), 0, 5), (6, 4, 5))
        self.assertIsNone(т(bytes([4, 0x60, 0, 1, 0]), 0, 4))
        self.assertIsNone(т(bytes([3, 0x60, 0, 1]), 0, 4))                     # короче постоянной части
        self.assertIsNone(т(bytes([255, 0x60]) + bytes(255), 0, 257))
        self.assertIsNone(т(b"\x04", 0, 1))
        self.assertEqual(т(bytes([2, 0xF0, 0x80]) + b"d", 0, 4), (0xF, 2, 4))
        self.assertEqual(т(bytes([7, 0x60, 0, 1, 0, 0x85, 1, 7]), 0, 8), (6, 7, 8))     # параметр впритык
        self.assertIsNone(т(bytes([7, 0x60, 0, 1, 0, 0x85, 2, 7]), 0, 8))
        self.assertEqual(т(bytes([6, 0x60, 0, 1, 0, 0x85, 0]), 0, 7), (6, 6, 7))     # пустой параметр впритык
        self.assertIsNone(т(bytes([6, 0x60, 0, 1, 0, 0x85, 1]), 0, 7))
        self.assertIsNone(т(bytes([5, 0x60, 0, 1, 0, 0x85]), 0, 6))                  # от параметра — один октет
        # DR несёт данные до конца, AK — нет.
        self.assertEqual(т(bytes([6, 0x80, 0, 1, 0, 2, 0]) + b"xx", 0, 9), (8, 6, 9))
        self.assertEqual(т(bytes([4, 0x60, 0, 1, 0]) + b"xx", 0, 7), (6, 4, 5))

    def test_tl1_раскладка(self):
        команда = b"RTRV-ALM-ALL:NODE7:ALL:42;"
        р = р_хвост(команда)
        self.assertTrue(osi.tl1(р, С, С + len(команда)))
        self.assertEqual(дерево(р)[0][0], ("TL1", 0, len(команда)))
        self.assertEqual(дерево(р)[0][1][:4], [(0, "tl1.cmd_code", 0, 12, "RTRV-ALM-ALL"),
                                                (0, "tl1.tid", 0, len(команда), "NODE7"),
                                                (0, "tl1.aid", 0, len(команда), "ALL"),
                                                (0, "tl1.ctag", 0, len(команда), "42")])
        for текст, да in ((b"ACT-USER::a:1;", True), (b"ACT-USER::a:1>", True), (b"ACT-USER::a:1<", True),
                          (b"ACT-USER::a:1", False), (b"A:b:c:d;", False), (b"ACT-USER::a:1;\x01", False),
                          (b"\tACT-USER::a:1;\r\n", True), (b"", False), (b"M 5 COMPLD\r\n;", True),
                          (b"** 17 REPT\r\n;", True), (b"A 17 REPT\r\n;", True), (b"*C 17 X\r\n;", True),
                          (b"just text;", False)):
            with self.subTest(текст=текст):
                self.assertEqual(osi.tl1(р_хвост(текст), С, С + len(текст)), да)
        р = р_хвост(b"** 17 REPT ALM\r\n;")
        osi.tl1(р, С, С + 17)
        self.assertEqual(р.п.инфо, "TL1 автономное ** ATAG 17 REPT")
        р = р_хвост(b"ACT-USER:::;")
        osi.tl1(р, С, С + 12)
        self.assertEqual(р.п.инфо, "TL1 команда ACT-USER, TID —, CTAG —")
