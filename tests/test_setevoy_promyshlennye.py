"""Промышленные протоколы и телефония поверх IP: пакеты собраны по стандартам.

Сборка здесь своя и от разборщика не зависит: поля кладутся struct.pack'ом в
порядке из документа, CRC-16 DNP считается побитно прямо по определению
(многочлен 0x3D65, отражённый), BER — своей маленькой функцией.
"""

import random
import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.filtr import отобрать
from reportgen.setevoy.statistika import уровень_протокола

TCP_НАГРУЗКА = 14 + 20 + 20
UDP_НАГРУЗКА = 14 + 20 + 8


def tcp(нагрузка, dport, sport=40000):
    return с.eth(с.ip(с.tcp(нагрузка, sport, dport), 6))


def udp(нагрузка, dport, sport=40000):
    return с.eth(с.ip(с.udp(нагрузка, sport, dport), 17))


def поля(пакет):
    return пакет.поля_фильтра()


def места(пакет, ключ):
    """(смещение, длина) всех полей с ключом — во всех уровнях и на любой глубине."""
    итог = []

    def обойти(список):
        for п in список:
            if п.ключ == ключ:
                итог.append((п.смещение, п.длина))
            обойти(п.дети)

    for у in пакет.уровни:
        обойти(у.поля)
    return итог


def выбрать(пакеты, текст):
    return отобрать([разобрать_пакет(п).поля_фильтра() for п in пакеты], текст)


def без_сбоев(тест, пакет):
    """Каждый обрыв пакета разбирается без исключений и без «разбор прерван»."""
    for n in range(len(пакет)):
        п = разобрать_пакет(пакет[:n])
        тест.assertFalse([о for о in п.ошибки if о.startswith("разбор прерван")], (n, п.ошибки))


# -- DNP3: CRC побитно по определению (IEEE 1815, разд. 9: многочлен 0x3D65) ------------------

def crc_dnp(данные):
    crc = 0
    for б in данные:
        for i in range(8):
            бит = ((б >> i) & 1) ^ (crc & 1)          # отражённый вход: младший бит первым
            crc >>= 1
            if бит:
                crc ^= 0xA6BC                         # 0x3D65 с обратным порядком бит
    return crc ^ 0xFFFF


def dnp3_кадр(упр, получатель, источник, польз=b"", испортить_блок=False):
    заголовок = struct.pack("<BBBBHH", 0x05, 0x64, 5 + len(польз), упр, получатель, источник)
    кадр = заголовок + struct.pack("<H", crc_dnp(заголовок))
    for i in range(0, len(польз), 16):
        блок = польз[i:i + 16]
        crc = crc_dnp(блок) ^ (1 if испортить_блок else 0)
        кадр += блок + struct.pack("<H", crc)
    return кадр


# Запрос класса 0: READ g60v2, g60v3, g60v4, g60v1, квалификатор 0x06 (все).
DNP3_READ = dnp3_кадр(0xC4, 10, 1, bytes([0xC0, 0xC1, 0x01]) + bytes.fromhex("3c0206" "3c0306" "3c0406" "3c0106"))
# Ответ: IIN1 = 0x80 (перезапуск), g1v2 диапазон 0–3 (квалификатор 0x00) и 4 байта флагов.
DNP3_ОТВЕТ = dnp3_кадр(0x44, 1, 10, bytes([0xC0, 0xC2, 0x81, 0x80, 0x00]) + bytes.fromhex("010200" "0003")
                       + bytes([0x01, 0x81, 0x01, 0x01]))
# READ с пятью заголовками: данные пользователя (18 байт) переходят через границу блока в
# 16 байт — пятый заголовок (g1v0) лежит по обе стороны CRC первого блока.
DNP3_READ5 = dnp3_кадр(0xC4, 10, 1, bytes([0xC0, 0xC1, 0x01])
                       + bytes.fromhex("3c0206" "3c0306" "3c0406" "3c0106" "010006"))


class DNP3Tests(unittest.TestCase):
    def test_crc_проверочное(self):
        self.assertEqual(0xEA82, crc_dnp(b"123456789"))

    def test_запрос_read(self):
        п = разобрать_пакет(tcp(DNP3_READ, 20000))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "DNP3"], п.стек)
        ф = поля(п)
        self.assertEqual(([1], [10], [4], [1]),
                         (ф["dnp3.src"], ф["dnp3.dst"], ф["dnp3.ctl.prifunc"], ф["dnp3.ctl.prm"]))
        self.assertEqual([1], ф["dnp3.al.func"])
        self.assertEqual([0x3C02, 0x3C03, 0x3C04, 0x3C01], ф["dnp3.al.obj"])
        self.assertEqual([6, 6, 6, 6], ф["dnp3.al.objq.range"])
        м = TCP_НАГРУЗКА
        self.assertEqual([(м + 2, 1)], места(п, "dnp3.len"))
        self.assertEqual([(м + 4, 2)], места(п, "dnp3.dst"))
        self.assertEqual([(м + 8, 2)], места(п, "dnp3.hdr.crc"))
        self.assertEqual([(м + 12, 1)], места(п, "dnp3.al.func"))
        self.assertEqual((м + 25, 2), места(п, "dnp3.data_chunk_crc")[0])
        self.assertIn("READ g60v2", п.инфо)
        self.assertEqual([], [о for о in п.ошибки if "DNP3" in о])

    def test_ответ_iin(self):
        п = разобрать_пакет(udp(DNP3_ОТВЕТ, 20000, sport=20000))
        ф = поля(п)
        self.assertEqual([129], ф["dnp3.al.func"])
        self.assertEqual([0x8000], ф["dnp3.al.iin"])
        self.assertEqual([1], ф["dnp3.al.iin.rst"])
        self.assertEqual([0], ф["dnp3.al.iin.dt"])
        self.assertEqual(([0x0102], [0], [3]), (ф["dnp3.al.obj"], ф["dnp3.al.range.start"], ф["dnp3.al.range.stop"]))
        м = UDP_НАГРУЗКА + 10
        # Байты данных пользователя 0–15 — первый блок, за ним CRC (2 байта), дальше второй блок.
        self.assertEqual([(м + 3, 2)], места(п, "dnp3.al.iin"))
        self.assertEqual([(м + 8, 1)], места(п, "dnp3.al.range.start"))
        self.assertEqual([(м + 9, 1)], места(п, "dnp3.al.range.stop"))
        self.assertEqual([(м + 14, 2)], места(п, "dnp3.data_chunk_crc"))
        self.assertEqual([], п.ошибки)

    def test_заголовок_через_границу_блока(self):
        п = разобрать_пакет(tcp(DNP3_READ5, 20000))
        м = TCP_НАГРУЗКА + 10                              # начало данных пользователя
        self.assertEqual([(м + 16, 2), (м + 18 + 2, 2)], места(п, "dnp3.data_chunk_crc"))
        # Байт 15 данных — последний в первом блоке, 16 и 17 — за CRC (на 2 байта дальше).
        self.assertEqual((м + 15, 5), места(п, "dnp3.al.obj")[4])
        self.assertEqual((м + 18, 1), места(п, "dnp3.al.obj.variation")[4])
        self.assertEqual((м + 19, 1), места(п, "dnp3.al.objq")[4])
        self.assertEqual([0x3C02, 0x3C03, 0x3C04, 0x3C01, 0x0100], поля(п)["dnp3.al.obj"])
        self.assertEqual([], п.ошибки)

    def test_плохая_crc_блока_и_заголовка(self):
        испорчен = dnp3_кадр(0x44, 1, 10, bytes([0xC0, 0xC2, 0x81, 0, 0]), испортить_блок=True)
        п = разобрать_пакет(tcp(испорчен, 20000))
        self.assertEqual("DNP3", п.протокол)
        self.assertIn("DNP3: неверная CRC блока данных 1", п.ошибки)
        заголовок = bytearray(DNP3_READ)
        заголовок[8] ^= 1
        self.assertNotIn("DNP3", разобрать_пакет(tcp(bytes(заголовок), 20000)).стек)

    def test_несколько_кадров_и_угадывание_по_сигнатуре(self):
        кадр_статус = dnp3_кадр(0xC9, 10, 1)                   # REQUEST_LINK_STATUS, без данных
        п = разобрать_пакет(tcp(кадр_статус + DNP3_READ, 20000))
        self.assertIn("REQUEST_LINK_STATUS", п.инфо)
        self.assertEqual([9], поля(п)["dnp3.ctl.prifunc"][:1])
        self.assertEqual([(TCP_НАГРУЗКА + 10, 2)], места(п, "dnp3.start")[1:])
        # На чужом порту — по сигнатуре (начало 0x0564 и верная CRC заголовка).
        self.assertEqual("DNP3", разобрать_пакет(tcp(DNP3_READ, 31000)).протокол)
        self.assertEqual("DNP3", разобрать_пакет(udp(DNP3_READ, 31000)).протокол)

    def test_транспортное_продолжение(self):
        продолжение = dnp3_кадр(0x44, 1, 10, bytes([0x81]) + b"\x11" * 5)        # FIR=0, FIN=1
        п = разобрать_пакет(tcp(продолжение, 20000))
        ф = поля(п)
        self.assertEqual(([0], [1], [1]), (ф["dnp3.tr.fir"], ф["dnp3.tr.fin"], ф["dnp3.tr.seq"]))
        self.assertNotIn("dnp3.al.func", ф)
        self.assertIn("dnp3.al.fragment", ф)

    def test_длина_меньше_пяти_и_вторичный_кадр(self):
        # Поле длины считает управление и адреса (5 байт): меньше — не DNP3, даже с верной CRC.
        заголовок = struct.pack("<BBBBHH", 0x05, 0x64, 4, 0xC9, 10, 1)
        self.assertNotIn("DNP3", разобрать_пакет(tcp(заголовок + struct.pack("<H", crc_dnp(заголовок)), 20000)).стек)
        п = разобрать_пакет(tcp(dnp3_кадр(0x0B, 1, 10), 20000))          # PRM=0: LINK_STATUS
        ф = поля(п)
        self.assertEqual(([0], [11], [0]), (ф["dnp3.ctl.prm"], ф["dnp3.ctl.secfunc"], ф["dnp3.ctl.dfc"]))
        self.assertNotIn("dnp3.ctl.prifunc", ф)
        self.assertIn("LINK_STATUS", п.инфо)

    def test_незапрошенный_ответ_с_iin(self):
        кадр = dnp3_кадр(0x44, 1, 10, bytes([0xC0, 0xF0, 0x82, 0x02, 0x00]) + bytes.fromhex("020128" "0100" "0500"))
        ф = поля(разобрать_пакет(tcp(кадр, 20000)))
        self.assertEqual(([130], [0x0200], [1], [1]), (ф["dnp3.al.func"], ф["dnp3.al.iin"], ф["dnp3.al.iin.cls1d"],
                                                        ф["dnp3.al.uns"]))
        self.assertEqual([0x0201], ф["dnp3.al.obj"])

    def test_read_с_индексами(self):
        # READ g1v2, префикс — 1-байтовый индекс, число 2 (квалификатор 0x17), индексы 3 и 7; затем g60v1.
        кадр = dnp3_кадр(0xC4, 10, 1, bytes([0xC0, 0xC3, 0x01]) + bytes.fromhex("010217" "02" "03" "07" "3c0106"))
        ф = поля(разобрать_пакет(tcp(кадр, 20000)))
        self.assertEqual(([0x0102, 0x3C01], [2], [1, 0]), (ф["dnp3.al.obj"], ф["dnp3.al.range.quantity"],
                                                            ф["dnp3.al.objq.prefix"]))

    def test_фильтр(self):
        пакеты = [tcp(DNP3_READ, 20000), udp(DNP3_ОТВЕТ, 20000), udp(b"\x05\x64" + b"\0" * 20, 20000),
                  tcp(DNP3_READ5, 20000)]
        self.assertEqual([0, 1, 3], выбрать(пакеты, "dnp3"))
        self.assertEqual([0, 3], выбрать(пакеты, "dnp3.al.func == 1"))
        self.assertEqual([1, 3], выбрать(пакеты, "dnp3.al.obj.group == 1"))
        self.assertEqual([1], выбрать(пакеты, "dnp3.al.iin.rst == 1 and dnp3.src == 10"))

    def test_обрыв(self):
        без_сбоев(self, tcp(DNP3_READ, 20000))
        без_сбоев(self, udp(DNP3_ОТВЕТ, 20000))
        без_сбоев(self, tcp(DNP3_READ5, 20000))


# -- IEC 60870-5-104 --------------------------------------------------------------------------

def apdu_i(ns, nr, asdu):
    return struct.pack("<BBHH", 0x68, 4 + len(asdu), ns << 1, nr << 1) + asdu


def asdu(тип, vsq, причина, общий, объекты, инициатор=0):
    return struct.pack("<BBBBH", тип, vsq, причина, инициатор, общий) + объекты


def ioa(адрес):
    return struct.pack("<I", адрес)[:3]


def cp56(мс, минуты, часы, день, месяц, год):
    return struct.pack("<HBBBBB", мс, минуты, часы, день, месяц, год)


STARTDT = bytes([0x68, 4, 0x07, 0, 0, 0])
TESTFR_CON = bytes([0x68, 4, 0x83, 0, 0, 0])
S_ФОРМАТ = bytes([0x68, 4, 0x01, 0, 5 << 1, 0])
ОПРОС = apdu_i(0, 0, asdu(100, 1, 6, 1, ioa(0) + bytes([20])))
ИЗМЕРЕНИЯ = apdu_i(3, 1, asdu(13, 0x82, 3, 7, ioa(1000) + struct.pack("<fB", 1.5, 0) + struct.pack("<fB", -2.25, 0x80)))
С_ВРЕМЕНЕМ = apdu_i(4, 1, asdu(30, 1, 3, 7, ioa(15) + bytes([0x01]) + cp56(30500, 12, 8, 26, 9, 26)))


class IEC104Tests(unittest.TestCase):
    def test_u_и_s(self):
        п = разобрать_пакет(tcp(STARTDT, 2404))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "IEC104"], п.стек)
        self.assertEqual((["U"], [0x04]), (поля(п)["iec60870_104.apdu"], поля(п)["iec60870_104.utype"]))
        self.assertEqual("IEC 104 STARTDT act", п.инфо)
        п = разобрать_пакет(tcp(S_ФОРМАТ, 2404))
        self.assertEqual(([5], [(TCP_НАГРУЗКА + 4, 2)]), (поля(п)["iec60870_104.rx"], места(п, "iec60870_104.rx")))

    def test_общий_опрос(self):
        п = разобрать_пакет(tcp(ОПРОС, 2404, sport=2404))
        ф = поля(п)
        self.assertEqual(([100], [6], [1], [0], [20]), (ф["iec60870_asdu.typeid"], ф["iec60870_asdu.causetx"],
                                                         ф["iec60870_asdu.addr"], ф["iec60870_asdu.ioa"],
                                                         ф["iec60870_asdu.qoi"]))
        м = TCP_НАГРУЗКА
        self.assertEqual([(м + 6, 1)], места(п, "iec60870_asdu.typeid"))
        self.assertEqual([(м + 10, 2)], места(п, "iec60870_asdu.addr"))
        self.assertEqual([(м + 12, 4)], места(п, "iec60870_asdu.ioa"))
        self.assertIn("C_IC_NA_1 act, CA 1", п.инфо)

    def test_несколько_apdu_последовательность_и_время(self):
        п = разобрать_пакет(tcp(ИЗМЕРЕНИЯ + TESTFR_CON + С_ВРЕМЕНЕМ, 2404))
        ф = поля(п)
        self.assertEqual(["I", "U", "I"], ф["iec60870_104.apdu"])
        self.assertEqual([3, 4], ф["iec60870_104.tx"])
        self.assertEqual([1000, 1001, 15], ф["iec60870_asdu.ioa"])      # SQ=1: адреса подряд от первого
        self.assertEqual([1.5, -2.25], ф["iec60870_asdu.float"])
        self.assertEqual(0x80, ф["iec60870_asdu.quality"][1])
        self.assertEqual(["2026-09-26 08:12:30.500"], ф["iec60870_asdu.cp56time"])
        второй_float = TCP_НАГРУЗКА + 12 + 3 + 5
        self.assertIn((второй_float, 4), места(п, "iec60870_asdu.float"))
        self.assertEqual([1], ф["iec60870_asdu.sp"])

    def test_длины_не_сходятся(self):
        плохой = bytearray(ОПРОС)
        плохой[7] = 2                                     # два объекта, а места на один
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes(плохой), 2404)).стек)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 4, 0x07, 0, 0, 1]), 2404)).стек)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(STARTDT + b"\x00", 2404)).стек)
        # Последний APDU может продолжиться в следующем сегменте — если перед ним есть целый.
        п = разобрать_пакет(tcp(STARTDT + ИЗМЕРЕНИЯ[:15], 2404))
        self.assertEqual("IEC104", п.протокол)
        self.assertIn("IEC 104: APDU продолжается за пределами сегмента", п.ошибки)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(ИЗМЕРЕНИЯ[:15], 2404)).стек)
        for тип, причина in ((0, 6), (100, 0)):                   # тип 0 и причина 0 не определены
            with self.subTest(тип=тип, причина=причина):
                self.assertNotIn("IEC104", разобрать_пакет(tcp(apdu_i(0, 0, asdu(тип, 1, причина, 1, ioa(0) + b"\x14")),
                                                                  2404)).стек)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(apdu_i(0, 0, asdu(100, 0, 6, 1, b"")), 2404)).стек)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 4, 0x01, 0, 1, 0]), 2404)).стек)   # N(R): бит 0
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 4, 0x05, 0, 0, 0]), 2404)).стек)   # S: лишние биты
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 4, 0x0F, 0, 0, 0]), 2404)).стек)   # U: две функции
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 5, 0x07, 0, 0, 0, 0]), 2404)).стек)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 3, 0x07, 0, 0]), 2404)).стек)
        self.assertNotIn("IEC104", разобрать_пакет(tcp(apdu_i(0, 0, b"\x64\x01\x06\x00\x01"), 2404)).стек)
        # I-формат короче идентификатора блока данных (6 байт) — не ASDU, даже с незнакомым типом.
        self.assertNotIn("IEC104", разобрать_пакет(tcp(bytes([0x68, 7, 0, 0, 0, 0, 120, 1, 3]) + STARTDT, 2404)).стек)
        # Длина APDU не больше 253 (IEC 60870-5-104, 5.1): 254 и 255 — не наши, тип тут незнакомый.
        for дл in (254, 255):
            длинный = bytes([0x68, дл, 0, 0, 0, 0, 120, 1, 3, 0, 1, 0]) + b"\0" * (дл - 10)
            self.assertNotIn("IEC104", разобрать_пакет(tcp(длинный, 2404)).стек)
        self.assertEqual("IEC104", разобрать_пакет(tcp(bytes([0x68, 253, 0, 0, 0, 0, 120, 1, 3, 0, 1, 0])
                                                        + b"\0" * 243, 2404)).протокол)

    def test_неизвестный_тип_и_кодирование_полей(self):
        п = разобрать_пакет(tcp(apdu_i(0x1234, 0x0567, asdu(120, 1, 13, 0x0102, ioa(0x030201) + b"\xaa\xbb")), 2404))
        ф = поля(п)
        self.assertEqual(([0x1234], [0x0567], [120], [0x0102]), (ф["iec60870_104.tx"], ф["iec60870_104.rx"],
                                                                  ф["iec60870_asdu.typeid"], ф["iec60870_asdu.addr"]))
        self.assertIn("iec60870_asdu.data", ф)
        команда = apdu_i(1, 1, asdu(45, 1, 6, 1, ioa(0x0A0B0C) + bytes([0x81]), инициатор=3))
        ф = поля(разобрать_пакет(tcp(команда, 2404)))
        self.assertEqual(([0x0A0B0C], [1], [1], [3]), (ф["iec60870_asdu.ioa"], ф["iec60870_asdu.scs"],
                                                        ф["iec60870_asdu.se"], ф["iec60870_asdu.oa"]))
        отриц = apdu_i(1, 1, asdu(100, 1, 0x40 | 7, 1, ioa(0) + b"\x14"))
        ф = поля(разобрать_пакет(tcp(отриц, 2404)))
        self.assertEqual(([7], [1], [0]),
                         (ф["iec60870_asdu.causetx"], ф["iec60870_asdu.nega"], ф["iec60870_asdu.test"]))

    def test_фильтр_и_обрыв(self):
        пакеты = [tcp(STARTDT, 2404), tcp(ОПРОС, 2404), tcp(ИЗМЕРЕНИЯ, 2404)]
        self.assertEqual([0, 1, 2], выбрать(пакеты, "iec104"))
        self.assertEqual([1], выбрать(пакеты, "iec60870_asdu.typeid == 100"))
        self.assertEqual([2], выбрать(пакеты, "iec60870_asdu.ioa == 1001"))
        без_сбоев(self, tcp(ИЗМЕРЕНИЯ + С_ВРЕМЕНЕМ, 2404))


# -- TPKT / COTP / S7comm / сеанс / представление / MMS ----------------------------------------

def tpkt(нагрузка):
    return struct.pack(">BBH", 3, 0, 4 + len(нагрузка)) + нагрузка


COTP_DT = bytes([0x02, 0xF0, 0x80])


def tlv(тег, содержимое):
    n = len(содержимое)
    длина = bytes([n]) if n < 128 else bytes([0x81, n]) if n < 256 else bytes([0x82]) + struct.pack(">H", n)
    return bytes([тег]) + длина + содержимое


def s7(rosctr, ссылка, параметры, данные_=b"", ошибка=None):
    заголовок = struct.pack(">BBHHHH", 0x32, rosctr, 0, ссылка, len(параметры), len(данные_))
    if ошибка is not None:
        заголовок += struct.pack(">BB", *ошибка)
    return заголовок + параметры + данные_


COTP_CR = tpkt(bytes([17, 0xE0, 0, 0, 0, 1, 0x00]) + bytes([0xC0, 1, 0x0A, 0xC1, 2, 1, 0, 0xC2, 2, 1, 2]))
S7_SETUP = tpkt(COTP_DT + s7(1, 1, bytes([0xF0, 0, 0, 1, 0, 1, 0x01, 0xE0])))
S7_READ = tpkt(COTP_DT + s7(1, 2, bytes([0x04, 1, 0x12, 0x0A, 0x10, 0x02, 0, 4, 0, 1, 0x84, 0, 0x00, 0x50])))
S7_ОТВЕТ = tpkt(COTP_DT + s7(3, 2, bytes([0x04, 1]), bytes([0xFF, 0x04, 0, 32]) + b"\xde\xad\xbe\xef", (0, 0)))
S7_PLUS = tpkt(COTP_DT + bytes([0x72, 0x01, 0, 4]) + b"\x31\x00\x00\x04" + bytes([0x72, 0x01, 0, 0]))
ИМЕНА = tlv(0x1A, b"LD0") + tlv(0x1A, b"MMXU1$MX$A")
MMS_ЧТЕНИЕ = tlv(0xA0, tlv(0x02, b"\x07") + tlv(0xA4, tlv(0xA1, tlv(0xA0, tlv(0x30, tlv(0xA0, tlv(0xA1, ИМЕНА)))))))
PRES = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, MMS_ЧТЕНИЕ)))
MMS = tpkt(COTP_DT + bytes([0x01, 0x00, 0x01, 0x00]) + PRES)


class TPKTTests(unittest.TestCase):
    def test_cotp_cr(self):
        п = разобрать_пакет(tcp(COTP_CR, 102))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TPKT", "COTP"], п.стек)
        ф = поля(п)
        self.assertEqual(([22], [0xE], [1], [0]), (ф["tpkt.length"], ф["cotp.type"], ф["cotp.srcref"], ф["cotp.class"]))
        self.assertEqual(["0a"], ф["cotp.tpdu-size"])
        self.assertEqual(["0100"], ф["cotp.src-tsap"])
        self.assertEqual([(TCP_НАГРУЗКА + 4 + 7 + 3, 4)], места(п, "cotp.src-tsap"))
        self.assertEqual("транспортный", уровень_протокола("TPKT"))
        self.assertEqual("транспортный", уровень_протокола("COTP"))

    def test_s7_setup_и_чтение(self):
        п = разобрать_пакет(tcp(S7_SETUP, 102))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TPKT", "COTP", "S7comm"], п.стек)
        ф = поля(п)
        self.assertEqual(([1], [0xF0], [480]), (ф["s7comm.header.rosctr"], ф["s7comm.param.func"],
                                                ф["s7comm.param.neg_pdu_length"]))
        м = TCP_НАГРУЗКА + 4 + 3
        self.assertEqual([(м + 4, 2)], места(п, "s7comm.header.pduref"))
        self.assertEqual([(м + 16, 2)], места(п, "s7comm.param.neg_pdu_length"))
        п = разобрать_пакет(tcp(S7_READ, 102))
        ф = поля(п)
        self.assertEqual(([1], [0x84], [1], ["10.0"]), (ф["s7comm.param.itemcount"], ф["s7comm.param.item.area"],
                                                        ф["s7comm.param.item.db"], ф["s7comm.param.item.address"]))
        п = разобрать_пакет(tcp(S7_ОТВЕТ, 1500, sport=102))
        self.assertEqual(([3], [0], [8]), (поля(п)["s7comm.header.rosctr"], поля(п)["s7comm.header.errcls"],
                                           поля(п)["s7comm.header.datlg"]))
        self.assertIn("S7comm Ack_Data, Read Var", п.инфо)

    def test_s7_длины_не_сходятся_и_s7plus(self):
        плохой = tpkt(COTP_DT + s7(1, 1, bytes([0xF0, 0, 0, 1, 0, 1, 0x01, 0xE0]))[:-1] + b"\0\0")
        п = разобрать_пакет(tcp(плохой, 102))
        self.assertEqual(["TPKT", "COTP", "Данные"], п.стек[3:])
        п = разобрать_пакет(tcp(S7_PLUS, 102))
        self.assertEqual("S7comm-plus", п.протокол)
        self.assertEqual([1], поля(п)["s7comm-plus.header.version"])

    def test_mms_через_сеанс_и_представление(self):
        п = разобрать_пакет(tcp(MMS, 102))
        self.assertEqual(["TPKT", "COTP", "SES", "PRES", "MMS"], п.стек[3:])
        ф = поля(п)
        self.assertEqual(([0], [7], [4]), (ф["mms.pdu"], ф["mms.invokeid"], ф["mms.confirmedservicerequest"]))
        self.assertEqual(["LD0", "MMXU1$MX$A"], ф["mms.identifier"])
        self.assertEqual([3], ф["pres.presentation_context_identifier"])
        начало_mms = TCP_НАГРУЗКА + 4 + 3 + 4 + 2 + 2 + 3 + 2
        self.assertEqual([(начало_mms + 2, 3)], места(п, "mms.invokeid"))
        self.assertIn("MMS confirmed-RequestPDU, invokeID 7, read LD0, MMXU1$MX$A", п.инфо)

    def test_cotp_cc_dr_er_и_фрагмент(self):
        cc = tpkt(bytes([6, 0xD0, 0, 1, 0, 2, 0x00]))
        п = разобрать_пакет(tcp(cc, 50000, sport=102))
        self.assertEqual(([0xD], [1], [2]), (поля(п)["cotp.type"], поля(п)["cotp.destref"], поля(п)["cotp.srcref"]))
        dr = tpkt(bytes([6, 0x80, 0, 1, 0, 2, 0x80]))
        self.assertEqual([0x80], поля(разобрать_пакет(tcp(dr, 102)))["cotp.dr_reason"])
        er = tpkt(bytes([4, 0x70, 0, 1, 0x03]))
        self.assertEqual([3], поля(разобрать_пакет(tcp(er, 102)))["cotp.reject_cause"])
        # DT с EOT=0 — кусок, S7 в нём не ищется.
        фрагмент = tpkt(bytes([2, 0xF0, 0x00]) + s7(1, 1, bytes([0xF0, 0, 0, 1, 0, 1, 0x01, 0xE0])))
        self.assertEqual(["TPKT", "COTP", "Данные"], разобрать_пакет(tcp(фрагмент, 102)).стек[3:])
        # Кривые заголовки: класс > 4, параметры не ложатся в LI, DT с LI ≠ 2, DT с младшими битами.
        for плохой in (bytes([6, 0xE0, 0, 0, 0, 1, 0x50]), bytes([8, 0xE0, 0, 0, 0, 1, 0, 0xC0, 3]),
                       bytes([3, 0xF0, 0x80, 0]), bytes([2, 0xF1, 0x80]), bytes([5, 0x80, 0, 1, 0, 2]),
                       bytes([3, 0x70, 0, 1])):
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("COTP", разобрать_пакет(tcp(tpkt(плохой + b"\0" * 4), 102)).стек)

    def test_сеанс_connect_mms_conclude_и_acse(self):
        cp = tlv(0x31, tlv(0xA0, b"\x80\x02\x07\x80"))
        connect = bytes([0x0D, 4 + 2 + len(cp) + 1]) + bytes([0x05, 2, 0x13, 0x01]) + bytes([0xC1, len(cp)]) + cp
        п = разобрать_пакет(tcp(tpkt(COTP_DT + connect), 102))
        self.assertEqual(["TPKT", "COTP", "Данные"], п.стек[3:])            # LI не сошёлся с данными
        connect = bytes([0x0D, 4 + 2 + len(cp)]) + bytes([0x05, 2, 0x13, 0x01]) + bytes([0xC1, len(cp)]) + cp
        п = разобрать_пакет(tcp(tpkt(COTP_DT + connect), 102))
        self.assertEqual(["TPKT", "COTP", "SES", "PRES"], п.стек[3:])
        self.assertEqual([13], поля(п)["ses.type"])
        self.assertIn("pres.cptype", поля(п))
        заключение = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, b"\x8b\x00")))
        п = разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + заключение), 102))
        self.assertEqual((["MMS"], [11]), (п.стек[-1:], поля(п)["mms.pdu"]))
        # conclude-Request обязан быть пустым NULL; составной [11] — не MMS.
        кривое = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, b"\xab\x00")))
        self.assertNotIn("MMS", разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + кривое), 102)).стек)
        acse = tlv(0x61, tlv(0x30, tlv(0x02, b"\x01") + tlv(0xA0, tlv(0x62, b"\x80\x01\x00"))))
        п = разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + acse), 102))
        self.assertEqual((["PRES", "Данные"], [0x62]), (п.стек[-2:], поля(п)["pres.acse"]))
        # Без идентификатора контекста PDV-list не полон — это не представительный уровень.
        без_контекста = tlv(0x61, tlv(0x30, tlv(0xA0, MMS_ЧТЕНИЕ)))
        self.assertNotIn("PRES", разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + без_контекста), 102)).стек)
        # Сеанс: GIVE TOKENS с LI ≠ 0 и DATA TRANSFER без данных — не сеанс.
        for плохой in (bytes([1, 1, 0, 1, 0]) + PRES, bytes([1, 0, 1, 0])):
            self.assertNotIn("SES", разобрать_пакет(tcp(tpkt(COTP_DT + плохой), 102)).стек)
        mms_ответ = tlv(0xA1, tlv(0x02, b"\x07") + tlv(0xA4, tlv(0xA1, b"\x30\x00")))
        pres_ответ = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, mms_ответ)))
        п = разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + pres_ответ), 102))
        self.assertEqual(([1], [4]), (поля(п)["mms.pdu"], поля(п)["mms.confirmedserviceresponse"]))
        без_invoke = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, tlv(0xA0, tlv(0xA4, b"\x30\x00")))))
        self.assertNotIn("MMS", разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + без_invoke), 102)).стек)

    def test_уточнения_s7_сеанса_представления_mms(self):
        # ROSCTR 2 (Ack) — тоже с классом и кодом ошибки (заголовок 12 байт).
        ack = tpkt(COTP_DT + s7(2, 9, b"", b"", (0x81, 0x04)))
        ф = поля(разобрать_пакет(tcp(ack, 102)))
        self.assertEqual(([2], [0x81], [4]), (ф["s7comm.header.rosctr"], ф["s7comm.header.errcls"],
                                              ф["s7comm.header.errcod"]))
        # S7comm-plus: версия не из известных, длина больше данных — не он.
        for плохой in (bytes([0x72, 0x05, 0, 4]) + b"\0" * 8, bytes([0x72, 0x01, 0, 40]) + b"\0" * 8):
            self.assertNotIn("S7comm-plus", разобрать_пакет(tcp(tpkt(COTP_DT + плохой), 102)).стек)
        # Сеанс: GIVE TOKENS с параметрами здесь не принимается (MMS шлёт 01 00 01 00);
        # у CONNECT LI меньше данных — хвост без разметки, не сеанс.
        с_токенами = bytes([1, 3, 0x10, 1, 0x15, 1, 0]) + PRES
        self.assertNotIn("SES", разобрать_пакет(tcp(tpkt(COTP_DT + с_токенами), 102)).стек)
        короче = bytes([0x0D, 4]) + bytes([0x05, 2, 0x13, 0x01]) + b"\xc1\x00"
        self.assertNotIn("SES", разобрать_пакет(tcp(tpkt(COTP_DT + короче), 102)).стек)
        # Представление: simply-encoded-data [APPLICATION 0] и PDV-list не SEQUENCE — не наш разбор.
        for плохой in (b"\x60" + PRES[1:], PRES[:2] + b"\x31" + PRES[3:]):
            п = разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + плохой), 102))
            self.assertNotIn("PRES", п.стек)
        # MMS: PDU короче значения представления, conclude-Request не пустой.
        mms_хвост = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, MMS_ЧТЕНИЕ + b"\0")))
        self.assertNotIn("MMS", разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + mms_хвост), 102)).стек)
        не_пустой = tlv(0x61, tlv(0x30, tlv(0x02, b"\x03") + tlv(0xA0, b"\x8b\x01\x00")))
        self.assertNotIn("MMS", разобрать_пакет(tcp(tpkt(COTP_DT + bytes([1, 0, 1, 0]) + не_пустой), 102)).стек)

    def test_не_cotp_и_фильтр(self):
        self.assertNotIn("TPKT", разобрать_пакет(tcp(tpkt(bytes([2, 0x50, 0])), 102)).стек)
        self.assertNotIn("TPKT", разобрать_пакет(tcp(tpkt(bytes([6, 0x50, 0, 1, 0, 2, 0])), 102)).стек)
        self.assertNotIn("TPKT", разобрать_пакет(tcp(b"\x03\x01\x00\x07\x02\xf0\x80", 102)).стек)
        пакеты = [tcp(COTP_CR, 102), tcp(S7_SETUP, 102), tcp(MMS, 102)]
        self.assertEqual([0, 1, 2], выбрать(пакеты, "cotp"))
        self.assertEqual([1], выбрать(пакеты, "s7comm.param.func == 0xf0"))
        self.assertEqual([2], выбрать(пакеты, "mms.identifier contains mmxu"))

    def test_два_tpkt_и_обрыв(self):
        п = разобрать_пакет(tcp(S7_SETUP + S7_READ, 102))
        self.assertEqual(["TPKT", "COTP", "S7comm", "TPKT", "COTP", "S7comm"], п.стек[3:])
        for пакет in (COTP_CR, S7_READ, MMS, S7_PLUS):
            без_сбоев(self, tcp(пакет, 102))


# -- EtherNet/IP и CIP -----------------------------------------------------------------------

def encap(команда, данные_=b"", сеанс=0, статус=0, контекст=b"ctx12345"):
    return struct.pack("<HHII8sI", команда, len(данные_), сеанс, статус, контекст, 0) + данные_


def cpf(*элементы):
    return struct.pack("<H", len(элементы)) + b"".join(struct.pack("<HH", т, len(д)) + д for т, д in элементы)


CIP_GET = bytes([0x0E, 3, 0x20, 0x01, 0x24, 0x01, 0x30, 0x07])     # Get_Attribute_Single Identity/1/7
ENIP_RS = encap(0x65, struct.pack("<HH", 1, 0))
ENIP_RR = encap(0x6F, struct.pack("<IH", 0, 10) + cpf((0, b""), (0xB2, CIP_GET)), сеанс=0x11223344)
ENIP_ОТВЕТ = encap(0x6F, struct.pack("<IH", 0, 0) + cpf((0, b""), (0xB2, bytes([0x8E, 0, 0, 0]) + b"\x09abcdefghi")),
                   сеанс=0x11223344)
ИМЯ = b"1756-EN2T/D"
ИДЕНТИЧНОСТЬ = (struct.pack("<H", 1) + struct.pack(">HH4s8x", 2, 44818, с.a4("10.0.0.2"))
                + struct.pack("<HHHBBHI", 1, 12, 166, 11, 2, 0x0030, 0x00C0FFEE) + bytes([len(ИМЯ)]) + ИМЯ + b"\x03")
ENIP_ID = encap(0x63, cpf((0x0C, ИДЕНТИЧНОСТЬ)))
ВЛОЖЕННЫЙ = bytes([0x01, 2, 0x20, 0x01, 0x24, 0x01])               # Get_Attributes_All Identity/1
UNCONN_SEND = (bytes([0x52, 2, 0x20, 0x06, 0x24, 0x01, 0x0A, 0xF0]) + struct.pack("<H", len(ВЛОЖЕННЫЙ)) + ВЛОЖЕННЫЙ
               + bytes([1, 0, 1, 0]))
ENIP_US = encap(0x6F, struct.pack("<IH", 0, 5) + cpf((0, b""), (0xB2, UNCONN_SEND)), сеанс=7)
CIP_IO = cpf((0x8002, struct.pack("<II", 0x00AB0001, 42)), (0x00B1, struct.pack("<H", 42) + b"\x01\x02\x03\x04"))


class ENIPTests(unittest.TestCase):
    def test_register_и_sendrrdata(self):
        п = разобрать_пакет(tcp(ENIP_RS, 44818))
        self.assertEqual(["ENIP"], п.стек[3:])
        self.assertEqual(([0x65], [1]), (поля(п)["enip.command"], поля(п)["enip.rs.version"]))
        п = разобрать_пакет(tcp(ENIP_RR, 44818))
        self.assertEqual(["ENIP", "CIP"], п.стек[3:])
        ф = поля(п)
        self.assertEqual(([0x0E], [1], [1], [7]), (ф["cip.sc"], ф["cip.class"], ф["cip.instance"], ф["cip.attribute"]))
        self.assertEqual([0x11223344], ф["enip.session"])
        cip = TCP_НАГРУЗКА + 24 + 6 + 2 + 4 + 4
        self.assertEqual([(cip, 1)], места(п, "cip.sc"))
        self.assertEqual([(cip + 2, 2)], места(п, "cip.class"))
        self.assertEqual([(cip + 6, 2)], места(п, "cip.attribute"))
        self.assertIn("Get_Attribute_Single (класс 0x01 (Identity), экземпляр 1, атрибут 7)", п.инфо)

    def test_ответ_cip_и_unconnected_send(self):
        п = разобрать_пакет(tcp(ENIP_ОТВЕТ, 50000, sport=44818))
        ф = поля(п)
        self.assertEqual(([0x0E], [1], [0]), (ф["cip.sc"], ф["cip.rr"], ф["cip.genstat"]))
        п = разобрать_пакет(tcp(ENIP_US, 44818))
        self.assertEqual(["ENIP", "CIP", "CIP"], п.стек[3:])
        self.assertEqual([0x52, 0x01], поля(п)["cip.sc"])
        self.assertIn("Unconnected_Send", п.инфо)
        self.assertIn("Get_Attributes_All", п.инфо)

    def test_listidentity_по_udp_и_ввод_вывод(self):
        п = разобрать_пакет(udp(ENIP_ID, 50000, sport=44818))
        ф = поля(п)
        self.assertEqual((["1756-EN2T/D"], [1], ["10.0.0.2"], [44818]),
                         (ф["enip.lir.name"], ф["enip.lir.vendor"], ф["enip.lir.sa.sinaddr"], ф["enip.lir.sa.sinport"]))
        имя = UDP_НАГРУЗКА + 24 + 2 + 4 + 32
        self.assertEqual([(имя, 1 + len(ИМЯ))], места(п, "enip.lir.name"))
        п = разобрать_пакет(udp(CIP_IO, 2222, sport=2222))
        self.assertEqual(["ENIP"], п.стек[3:])
        self.assertEqual(([0x00AB0001], [42]), (поля(п)["enip.cpf.sai.connid"], поля(п)["enip.cpf.sai.seq"]))

    def test_sendunitdata_путь_16_бит_и_класс_не_менеджера(self):
        # Соединённые данные: 2 байта номера, затем запрос CIP; путь с 16-битным экземпляром (0x25 + заполнитель).
        запрос = bytes([0x10, 4, 0x20, 0x04, 0x25, 0x00, 0x34, 0x12, 0x30, 0x03]) + b"\x01\x02"
        pdu = encap(0x70, struct.pack("<IH", 0, 0) + cpf((0xA1, struct.pack("<I", 0x42)),
                                                          (0xB1, struct.pack("<H", 9) + запрос)), сеанс=5)
        п = разобрать_пакет(tcp(pdu, 44818))
        ф = поля(п)
        self.assertEqual(([0x10], [4], [0x1234], [3], [9], [0x42]),
                         (ф["cip.sc"], ф["cip.class"], ф["cip.instance"], ф["cip.attribute"], ф["enip.cpf.cdi.seqcnt"],
                          ф["enip.cpf.cai.connid"]))
        cip = TCP_НАГРУЗКА + 24 + 6 + 2 + 8 + 4 + 2
        self.assertEqual([(cip + 4, 4)], места(п, "cip.instance"))
        # 0x52 не у менеджера соединений (класс 6) — не Unconnected_Send, внутрь не смотрим.
        чужой = bytes([0x52, 2, 0x20, 0x04, 0x24, 0x01]) + struct.pack("<BBH", 0x0A, 0xF0, 6) + ВЛОЖЕННЫЙ
        п = разобрать_пакет(tcp(encap(0x6F, struct.pack("<IH", 0, 0) + cpf((0, b""), (0xB2, чужой))), 44818))
        self.assertEqual((["ENIP", "CIP"], [0x52]), (п.стек[3:], поля(п)["cip.sc"]))
        self.assertNotIn("Unconnected_Send", п.инфо)
        # Ответ с дополнительным состоянием (1 слово) и ошибкой «объект не существует».
        ответ = bytes([0x8E, 0, 0x16, 1, 0x34, 0x12])
        п = разобрать_пакет(tcp(encap(0x6F, struct.pack("<IH", 0, 0) + cpf((0, b""), (0xB2, ответ))), 1234,
                                sport=44818))
        self.assertEqual(([0x16], ["3412"]), (поля(п)["cip.genstat"], поля(п)["cip.ext_status"]))
        self.assertIn("объект не существует", п.инфо)

    def test_проверки(self):
        с_опцией = ENIP_RS[:20] + b"\x01\0\0\0" + ENIP_RS[24:]
        self.assertNotIn("ENIP", разобрать_пакет(tcp(encap(0x65, struct.pack("<HH", 2, 0)), 44818)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(tcp(encap(0x66, b"\0"), 44818)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(tcp(encap(0x99), 44818)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(udp(cpf((0x8001, b"\0" * 8), (0xB1, b"12")), 2222)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(udp(b"\x03\x00" + CIP_IO[2:], 2222)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(tcp(ENIP_RS[:26], 44818)).стек)
        # Элементы CPF легли, но после них — лишние байты данных команды.
        хвост = encap(0x6F, struct.pack("<IH", 0, 0) + cpf((0, b""), (0xB2, CIP_GET)) + b"\0\0")
        self.assertNotIn("ENIP", разобрать_пакет(tcp(хвост, 44818)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(tcp(с_опцией, 44818)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(udp(ENIP_ID + b"\0", 44818)).стек)          # длина не сходится
        кривой_cpf = encap(0x6F, struct.pack("<IH", 0, 0) + struct.pack("<HHH", 2, 0, 0))
        self.assertNotIn("ENIP", разобрать_пакет(tcp(кривой_cpf, 44818)).стек)
        self.assertNotIn("ENIP", разобрать_пакет(udp(CIP_IO + b"\0", 2222)).стек)

    def test_фильтр_и_обрыв(self):
        пакеты = [tcp(ENIP_RS, 44818), tcp(ENIP_RR, 44818), udp(ENIP_ID, 44818)]
        self.assertEqual([0, 1, 2], выбрать(пакеты, "enip"))
        self.assertEqual([1], выбрать(пакеты, "cip.class == 1"))
        self.assertEqual([2], выбрать(пакеты, "enip.lir.name contains en2t"))
        for пакет in (tcp(ENIP_RR, 44818), udp(ENIP_ID, 44818), tcp(ENIP_US, 44818), udp(CIP_IO, 2222)):
            без_сбоев(self, пакет)


# -- BACnet/IP ----------------------------------------------------------------------------------

def bvlc(функция, тело):
    return struct.pack(">BBH", 0x81, функция, 4 + len(тело)) + тело


WHO_IS = bvlc(0x0B, bytes([0x01, 0x20, 0xFF, 0xFF, 0x00, 0xFF]) + bytes([0x10, 0x08]))
I_AM = bvlc(0x0B, bytes([0x01, 0x00]) + bytes([0x10, 0x00, 0xC4]) + struct.pack(">I", (8 << 22) | 1234)
            + bytes([0x22, 0x05, 0xC4, 0x91, 0x00, 0x21, 0x0F]))
READ_PROPERTY = bvlc(0x0A, bytes([0x01, 0x04]) + bytes([0x00, 0x05, 0x01, 0x0C, 0x0C]) + struct.pack(">I", 1)
                     + bytes([0x19, 0x55]))
BVLC_RESULT = bvlc(0x00, struct.pack(">H", 0))


class BACnetTests(unittest.TestCase):
    def test_who_is_и_i_am(self):
        п = разобрать_пакет(udp(WHO_IS, 47808, sport=47808))
        self.assertEqual(["BVLC", "BACnet", "BACapp"], п.стек[3:])
        ф = поля(п)
        self.assertEqual(([0x0B], [0xFFFF], [255], [8]), (ф["bvlc.function"], ф["bacnet.dnet"], ф["bacnet.hopc"],
                                                          ф["bacapp.unconfirmed_service"]))
        self.assertEqual([(UDP_НАГРУЗКА + 4 + 2, 2)], места(п, "bacnet.dnet"))
        self.assertEqual("BACnet who-Is", п.инфо)
        п = разобрать_пакет(udp(I_AM, 47808))
        ф = поля(п)
        self.assertEqual(([8], [1234]), (ф["bacapp.objecttype"], ф["bacapp.instance_number"]))
        self.assertIn("i-Am device,1234", п.инфо)

    def test_read_property(self):
        п = разобрать_пакет(udp(READ_PROPERTY, 47808))
        ф = поля(п)
        self.assertEqual(([0], [1], [12], [85]), (ф["bacapp.type"], ф["bacapp.invoke_id"],
                                                  ф["bacapp.confirmed_service"], ф["bacapp.property_identifier"]))
        self.assertEqual([0], ф["bacapp.objecttype"])
        apdu = UDP_НАГРУЗКА + 4 + 2
        self.assertEqual([(apdu + 2, 1)], места(п, "bacapp.invoke_id"))
        self.assertEqual([(apdu + 9, 2)], места(п, "bacapp.property_identifier"))
        self.assertIn("readProperty, invoke 1 analog-input,1 present-value", п.инфо)

    def test_проверки(self):
        self.assertEqual(["BVLC"], разобрать_пакет(udp(BVLC_RESULT, 47808)).стек[3:])
        длиннее = WHO_IS[:2] + struct.pack(">H", len(WHO_IS) + 1) + WHO_IS[4:]
        self.assertNotIn("BVLC", разобрать_пакет(udp(длиннее, 47808)).стек)
        версия2 = WHO_IS[:4] + b"\x02" + WHO_IS[5:]
        self.assertNotIn("BVLC", разобрать_пакет(udp(версия2, 47808)).стек)
        резерв = WHO_IS[:5] + bytes([WHO_IS[5] | 0x40]) + WHO_IS[6:]
        self.assertNotIn("BVLC", разобрать_пакет(udp(резерв, 47808)).стек)
        self.assertNotIn("BVLC", разобрать_пакет(udp(bvlc(0x00, b"\0\0\0"), 47808)).стек)
        self.assertNotIn("BVLC", разобрать_пакет(udp(b"\x82" + WHO_IS[1:], 47808)).стек)
        self.assertNotIn("BVLC", разобрать_пакет(udp(bvlc(0x0D, bytes([1, 0, 0x10, 0x08])), 47808)).стек)
        self.assertNotIn("BVLC", разобрать_пакет(udp(WHO_IS + b"\0", 47808)).стек)        # длина BVLC меньше данных
        # SNET с нулевой длиной адреса источника запрещён (разд. 6.2.2).
        self.assertNotIn("BVLC", разобрать_пакет(udp(bvlc(0x0A, bytes([1, 0x08, 0, 5, 0, 0x10, 0x08])), 47808)).стек)
        # Зарезервированные биты первого байта APDU (Unconfirmed-Request: младшие 4).
        self.assertNotIn("BVLC", разобрать_пакет(udp(bvlc(0x0A, bytes([1, 0, 0x11, 0x08])), 47808)).стек)

    def test_пересланный_ответ_и_сетевое(self):
        # Forwarded-NPDU: 6 байт исходного адреса B/IP, затем NPDU с SNET/SADR и ComplexACK ReadProperty.
        ack = (bytes([0x30, 0x07, 0x0C, 0x0C]) + struct.pack(">I", (2 << 22) | 5)
               + bytes([0x19, 0x4D, 0x3E, 0x75, 0x04, 0x00]) + b"AHU" + bytes([0x3F]))
        npdu = bytes([0x01, 0x08, 0x00, 0x07, 0x01, 0x2A]) + ack
        п = разобрать_пакет(udp(bvlc(0x04, с.a4("192.168.1.5") + struct.pack(">H", 47808) + npdu), 47808))
        ф = поля(п)
        self.assertEqual((["192.168.1.5"], [47808], [7], ["2a"]),
                         (ф["bvlc.fwd_ip"], ф["bvlc.fwd_port"], ф["bacnet.snet"], ф["bacnet.sadr"]))
        self.assertEqual(([3], [7], [12], [77]), (ф["bacapp.type"], ф["bacapp.invoke_id"],
                                                  ф["bacapp.confirmed_service"], ф["bacapp.property_identifier"]))
        self.assertEqual([2], ф["bacapp.objecttype"])
        self.assertIn("«AHU»", п.инфо)
        self.assertEqual([(UDP_НАГРУЗКА + 10 + 2, 2)], места(п, "bacnet.snet"))
        сетевое = bvlc(0x0B, bytes([0x01, 0x80, 0x00]) + struct.pack(">H", 5))
        п = разобрать_пакет(udp(сетевое, 47808))
        self.assertEqual(([0], "BACnet Who-Is-Router-To-Network"), (поля(п)["bacnet.mesgtyp"], п.инфо))
        ошибка = bvlc(0x0A, bytes([0x01, 0x00, 0x50, 0x03, 0x0C, 0x91, 0x02, 0x91, 0x20]))
        п = разобрать_пакет(udp(ошибка, 47808))
        self.assertEqual(([5], [3]), (поля(п)["bacapp.type"], поля(п)["bacapp.invoke_id"]))

    def test_фильтр_и_обрыв(self):
        пакеты = [udp(WHO_IS, 47808), udp(I_AM, 47808), udp(READ_PROPERTY, 47808)]
        self.assertEqual([0, 1, 2], выбрать(пакеты, "bacnet"))
        self.assertEqual([2], выбрать(пакеты, "bacapp.property_identifier == 85"))
        self.assertEqual([1], выбрать(пакеты, "bacapp.instance_number == 1234"))
        for пакет in пакеты:
            без_сбоев(self, пакет)


# -- OPC UA -------------------------------------------------------------------------------------

def ua_строка(текст):
    return struct.pack("<i", len(текст)) + текст


def ua(тип, признак, тело):
    return тип + признак + struct.pack("<I", 8 + len(тело)) + тело


АДРЕС = b"opc.tcp://plc.example:4840/ua"
HEL = ua(b"HEL", b"F", struct.pack("<5I", 0, 65536, 65536, 0, 0) + ua_строка(АДРЕС))
ACK = ua(b"ACK", b"F", struct.pack("<5I", 0, 65536, 65536, 16777216, 5000))
OPN = ua(b"OPN", b"F", struct.pack("<I", 0) + ua_строка(b"http://opcfoundation.org/UA/SecurityPolicy#None")
         + struct.pack("<ii", -1, -1) + struct.pack("<II", 51, 1) + bytes([0x01, 0x00]) + struct.pack("<H", 446)
         + b"\0" * 20)
MSG = ua(b"MSG", b"F", struct.pack("<IIII", 7, 1, 52, 2) + bytes([0x01, 0x00]) + struct.pack("<H", 631) + b"\0" * 12)
ERR = ua(b"ERR", b"F", struct.pack("<I", 0x80020000) + ua_строка(b"bad"))


class OPCUATests(unittest.TestCase):
    def test_hello_ack(self):
        п = разобрать_пакет(tcp(HEL, 4840))
        self.assertEqual(["OPCUA"], п.стек[3:])
        ф = поля(п)
        self.assertEqual((["HEL"], [65536], [АДРЕС.decode()]), (ф["opcua.transport.type"], ф["opcua.transport.rbs"],
                                                                ф["opcua.transport.endpoint"]))
        self.assertEqual([(TCP_НАГРУЗКА + 28, 4 + len(АДРЕС))], места(п, "opcua.transport.endpoint"))
        п = разобрать_пакет(tcp(ACK + ERR, 50000, sport=4840))
        self.assertEqual([16777216], поля(п)["opcua.transport.mms"])
        self.assertEqual([0x80020000], поля(п)["opcua.transport.error"])

    def test_opn_и_msg(self):
        п = разобрать_пакет(tcp(OPN, 4840))
        ф = поля(п)
        self.assertEqual(([446], [51]), (ф["opcua.servicenodeid.numeric"], ф["opcua.security.seq"]))
        self.assertIn("OpenSecureChannelRequest", п.инфо)
        п = разобрать_пакет(tcp(MSG, 4840))
        ф = поля(п)
        self.assertEqual(([7], [631], [1]), (ф["opcua.transport.scid"], ф["opcua.servicenodeid.numeric"],
                                             ф["opcua.security.tokenid"]))
        self.assertEqual([(TCP_НАГРУЗКА + 24, 4)], места(п, "opcua.servicenodeid.numeric"))

    def test_защищённый_opn_rhe_clo(self):
        политика = b"http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256"
        opn = ua(b"OPN", b"F", struct.pack("<I", 0) + ua_строка(политика) + ua_строка(b"\x30\x82" + b"\0" * 30)
                 + ua_строка(b"\x11" * 20) + b"\x5a" * 64)
        п = разобрать_пакет(tcp(opn, 4840))
        ф = поля(п)
        self.assertEqual(([политика.decode()], ["зашифровано"]), (ф["opcua.transport.spu"], ф["opcua.encrypted"]))
        self.assertNotIn("opcua.servicenodeid.numeric", ф)
        rhe = ua(b"RHE", b"F", ua_строка(b"urn:srv") + ua_строка(АДРЕС))
        self.assertEqual([АДРЕС.decode()], поля(разобрать_пакет(tcp(rhe, 4840)))["opcua.transport.endpoint"])
        clo = ua(b"CLO", b"F", struct.pack("<IIII", 7, 1, 60, 9) + bytes([0x01, 0x00]) + struct.pack("<H", 452))
        п = разобрать_пакет(tcp(clo, 4840))
        self.assertEqual([452], поля(п)["opcua.servicenodeid.numeric"])
        self.assertIn("CloseSecureChannelRequest", п.инфо)
        # Кусок C у CLO и у HEL не бывает; MSG кусками — бывает.
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(b"CLOC" + clo[4:], 4840)).стек)
        self.assertEqual(["C"], поля(разобрать_пакет(tcp(b"MSGC" + MSG[4:], 4840)))["opcua.transport.chunk"])
        # Числовой NodeId с пространством имён ≠ 0 — не тип сервиса.
        чужой = ua(b"MSG", b"F", struct.pack("<IIII", 7, 1, 52, 2) + bytes([0x01, 0x02]) + struct.pack("<H", 631))
        self.assertNotIn("opcua.servicenodeid.numeric", поля(разобрать_пакет(tcp(чужой, 4840))))
        # Полная числовая кодировка NodeId (0x02: UInt16 пространство, UInt32 идентификатор).
        полный = ua(b"MSG", b"F", struct.pack("<IIII", 7, 1, 53, 3) + bytes([0x02, 0, 0]) + struct.pack("<I", 634))
        п = разобрать_пакет(tcp(полный, 4840))
        self.assertEqual(([634], [(TCP_НАГРУЗКА + 24, 7)]), (поля(п)["opcua.servicenodeid.numeric"],
                                                             места(п, "opcua.servicenodeid.numeric")))

    def test_проверки_и_угадывание(self):
        длиннее_url = HEL[:28] + struct.pack("<i", len(АДРЕС) + 1) + HEL[32:]
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(длиннее_url, 4840)).стек)
        короче_url = HEL[:28] + struct.pack("<i", len(АДРЕС) - 1) + HEL[32:]
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(короче_url, 4840)).стек)
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(ua(b"ACK", b"F", ACK[8:] + b"\0\0\0\0"), 4840)).стек)
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(b"HELC" + HEL[4:], 4840)).стек)
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(ACK[:-1], 4840)).стек)
        # На чужом порту — только сообщение, сошедшееся целиком.
        self.assertEqual("OPCUA", разобрать_пакет(tcp(HEL, 48010)).протокол)
        self.assertNotIn("OPCUA", разобрать_пакет(tcp(MSG[:30], 48010)).стек)
        # На своём порту длинное сообщение может продолжиться в следующем сегменте.
        self.assertEqual("OPCUA", разобрать_пакет(tcp(MSG[:30], 4840)).протокол)

    def test_фильтр_и_обрыв(self):
        пакеты = [tcp(HEL, 4840), tcp(OPN, 4840), tcp(MSG, 4840)]
        self.assertEqual([0, 1, 2], выбрать(пакеты, "opcua"))
        self.assertEqual([2], выбрать(пакеты, "opcua.servicenodeid.numeric == 631"))
        for пакет in пакеты:
            без_сбоев(self, пакет)


# -- MGCP, Megaco, SDP ------------------------------------------------------------------------------

ТЕЛО_SDP = (b"v=0\r\no=- 25678 753849 IN IP4 128.96.41.1\r\ns=-\r\nc=IN IP4 128.96.41.1\r\nt=0 0\r\n"
            b"m=audio 3456 RTP/AVP 0 8\r\na=rtpmap:0 PCMU/8000\r\na=rtpmap:8 PCMA/8000\r\n")
CRCX = (b"CRCX 1204 aaln/1@rgw-2567.whatever.net MGCP 1.0\r\nC: A3C47F21456789F0\r\nL: p:10, a:PCMU\r\n"
        b"M: recvonly\r\n\r\n" + ТЕЛО_SDP)
ОТВЕТ_MGCP = b"200 1204 OK\r\nI: FDE234C8\r\n"
СКЛЕЙКА = b"200 1203 OK\n.\nRQNT 1205 aaln/1@rgw.example.net MGCP 1.0\nX: 0123456789AC\nR: hd\n"
MEGACO = (b"MEGACO/1 [124.124.124.222]:55555\r\nTransaction = 9998 {\r\n  Context = - {\r\n"
          b"    Add = A4444 { Media { Stream = 1 { LocalControl { Mode = SendReceive } } } },\r\n"
          b"    Add = $ { Media { Stream = 1 { Local { v=0 c=IN IP4 $ m=audio $ RTP/AVP 4 } } } }\r\n  }\r\n}\r\n")
MEGACO_КРАТКО = b"!/1 <mg1.example.net>\nP=9998{C=2000{A=A4444,A=A4445}}"
H248_ДВОИЧНЫЙ = tlv(0x30, tlv(0xA1, tlv(0x80, b"\x01") + tlv(0xA1, tlv(0x80, b"\x7c\x7c\x7c\xde"))
                              + tlv(0xA2, tlv(0xA1, b"\x30\x00"))))
SIP_INVITE = (b"INVITE sip:bob@example.com SIP/2.0\r\nVia: SIP/2.0/UDP pc33.example.com\r\n"
              b"Content-Type: application/sdp\r\nContent-Length: %d\r\n\r\n" % len(ТЕЛО_SDP)) + ТЕЛО_SDP


class ТелефонияТекстTests(unittest.TestCase):
    def test_mgcp_команда_с_sdp(self):
        п = разобрать_пакет(udp(CRCX, 2427))
        self.assertEqual(["MGCP", "SDP"], п.стек[3:])
        ф = поля(п)
        self.assertEqual((["CRCX"], [1204], ["aaln/1@rgw-2567.whatever.net"]),
                         (ф["mgcp.req.verb"], ф["mgcp.transid"], ф["mgcp.req.endpoint"]))
        self.assertEqual(["A3C47F21456789F0"], ф["mgcp.param.callid"])
        self.assertEqual(["recvonly"], ф["mgcp.param.connectionmode"])
        self.assertEqual([(UDP_НАГРУЗКА + 5, 4)], места(п, "mgcp.transid"))
        self.assertEqual(([3456], ["PCMU", "PCMA"]), (ф["sdp.media.port"], ф["sdp.mime.type"]))
        начало_sdp = UDP_НАГРУЗКА + CRCX.index(b"v=0")
        порт = начало_sdp + ТЕЛО_SDP.index(b"3456")
        self.assertEqual([(порт, 4)], места(п, "sdp.media.port"))
        self.assertEqual(["0", "8"], ф["sdp.media.format"])
        self.assertEqual("MGCP CRCX 1204 aaln/1@rgw-2567.whatever.net (с SDP)", п.инфо)

    def test_mgcp_ответ_и_склейка(self):
        п = разобрать_пакет(udp(ОТВЕТ_MGCP, 50000, sport=2427))
        self.assertEqual(([200], ["FDE234C8"]), (поля(п)["mgcp.rsp.rspcode"], поля(п)["mgcp.param.connectionid"]))
        п = разобрать_пакет(udp(СКЛЕЙКА, 2727))
        self.assertEqual([1203, 1205], поля(п)["mgcp.transid"])
        self.assertEqual(["RQNT"], поля(п)["mgcp.req.verb"])
        self.assertNotIn("MGCP", разобрать_пакет(udp(b"CRCX 0 a@b MGCP 1.0\r\n", 2427)).стек)
        self.assertNotIn("MGCP", разобрать_пакет(udp(b"ABCD 12 a@b MGCP 1.0\r\n", 2427)).стек)
        self.assertNotIn("MGCP", разобрать_пакет(udp(b"CRCX 12 ab MGCP 1.0\r\n", 2427)).стек)

    def test_megaco_текст(self):
        п = разобрать_пакет(udp(MEGACO, 2944))
        self.assertEqual(["MEGACO"], п.стек[3:])
        ф = поля(п)
        self.assertEqual(([1], ["[124.124.124.222]:55555"], ["9998"], ["-"]),
                         (ф["megaco.version"], ф["megaco.mid"], ф["megaco.transid"], ф["megaco.context"]))
        self.assertEqual(["Add", "Add"], ф["megaco.command"])
        self.assertEqual(["A4444", "$"], ф["megaco.termid"])
        self.assertEqual([(UDP_НАГРУЗКА + MEGACO.index(b"A4444"), 5)], места(п, "megaco.termid")[:1])
        п = разобрать_пакет(udp(MEGACO_КРАТКО, 2944))
        self.assertEqual((["ответ"], ["2000"], ["A4444", "A4445"]),
                         (поля(п)["megaco.transaction"], поля(п)["megaco.context"], поля(п)["megaco.termid"]))
        self.assertNotIn("MEGACO", разобрать_пакет(udp(MEGACO.replace(b"}\r\n}", b"}\r\n"), 2944)).стек)
        self.assertNotIn("MEGACO", разобрать_пакет(udp(b"MEGACO/1 [1.2.3.4]\r\nFoo = 1 {}", 2944)).стек)

    def test_megaco_двоичный_и_tcp(self):
        п = разобрать_пакет(udp(H248_ДВОИЧНЫЙ, 2945))
        self.assertEqual((["H.248"], [1]), (п.стек[3:], поля(п)["h248.version"]))
        п = разобрать_пакет(tcp(tpkt(MEGACO_КРАТКО), 2944))
        self.assertEqual(["TPKT", "MEGACO"], п.стек[3:])
        self.assertEqual("MEGACO", разобрать_пакет(tcp(MEGACO, 2944)).протокол)
        # mId вплотную к тексту, версия не цифрами, строка в кавычках не закрыта — не Megaco.
        for плохой in (b"MEGACO/1 [1.2.3.4]T=1{}", b"MEGACO/x [1.2.3.4] T=1{}", b"!/1 <a> T=1{C=1{A=\"x}}",
                       b"!/1 <a>\nT=1{}}{"):
            self.assertNotIn("MEGACO", разобрать_пакет(udp(плохой, 2944)).стек)
        # Версия вне 1–3, лишний байт после сообщения, внешняя SEQUENCE короче содержимого — не H.248.
        короче = H248_ДВОИЧНЫЙ[:1] + bytes([H248_ДВОИЧНЫЙ[1] - 1]) + H248_ДВОИЧНЫЙ[2:]
        for плохой in (H248_ДВОИЧНЫЙ.replace(b"\x80\x01\x01", b"\x80\x01\x07"), H248_ДВОИЧНЫЙ + b"\0", короче):
            self.assertNotIn("H.248", разобрать_пакет(udp(плохой, 2945)).стек)
        # TPKT с длиной меньше заголовка — не рамка (и разбор не зацикливается).
        self.assertNotIn("TPKT", разобрать_пакет(tcp(b"\x03\x00\x00\x00" + MEGACO_КРАТКО, 2944)).стек)
        self.assertNotIn("TPKT", разобрать_пакет(tcp(tpkt(b"\x01\x02\x03\x04"), 2944)).стек)

    def test_sip_с_sdp(self):
        п = разобрать_пакет(udp(SIP_INVITE, 5060))
        self.assertEqual(["SIP", "SDP"], п.стек[3:])
        ф = поля(п)
        self.assertEqual((["0"], ["128.96.41.1"], ["audio"], [0, 8]),
                         (ф["sdp.version"], ф["sdp.connection_info.address"], ф["sdp.media.media"], ф["sdp.rtpmap.pt"]))
        адрес = UDP_НАГРУЗКА + SIP_INVITE.index(b"128.96.41.1\r\nt=")
        self.assertEqual([(адрес, len("128.96.41.1"))], места(п, "sdp.connection_info.address"))
        self.assertTrue(п.инфо.startswith("SIP INVITE") and п.инфо.endswith("(с SDP)"))
        self.assertEqual("SIP", разобрать_пакет(tcp(SIP_INVITE, 5060)).стек[-2])
        # Тело не SDP — SDP нет; разобранный SIP остаётся.
        не_sdp = SIP_INVITE.replace(b"v=0\r\n", b"x=0\r\n")
        self.assertEqual(["SIP"], разобрать_пакет(udp(не_sdp, 5060)).стек[3:])
        заголовки = SIP_INVITE.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
        for тело in (b"v=1\r\nm=audio 49170 RTP/AVP 0\r\n",                 # версия SDP — только 0
                     b"v=0\r\nm=audio 49170 RTP/AVP 0\r\nzz\r\n",          # строка без «=»
                     b"v=0\r\nm=audio 49170 RTP/AVP 0\r\nQ=1\r\n",         # неизвестный вид строки
                     b"v=0\r\ns=-\r\n"):                                   # ни o=, ни m=
            with self.subTest(тело=тело):
                self.assertEqual(["SIP"], разобрать_пакет(udp(заголовки + тело, 5060)).стек[3:])
        self.assertNotIn("SDP", разобрать_пакет(udp(SIP_INVITE, 5070)).стек)
        п = разобрать_пакет(udp(SIP_INVITE, 5070), как={"udp:5070": "SIP"})
        self.assertEqual(["SIP", "SDP"], п.стек[3:])
        self.assertEqual("прикладной", уровень_протокола("SDP"))

    def test_фильтр_и_обрыв(self):
        пакеты = [udp(CRCX, 2427), udp(MEGACO, 2944), udp(SIP_INVITE, 5060)]
        self.assertEqual([0, 2], выбрать(пакеты, "sdp"))
        self.assertEqual([0], выбрать(пакеты, "mgcp.req.verb == CRCX"))
        self.assertEqual([1], выбрать(пакеты, "megaco.termid == A4444"))
        self.assertEqual([0, 2], выбрать(пакеты, "sdp.mime.type == PCMA"))
        for пакет in пакеты + [udp(H248_ДВОИЧНЫЙ, 2945), tcp(tpkt(MEGACO_КРАТКО), 2944)]:
            без_сбоев(self, пакет)


# -- IAX2 и Skinny ----------------------------------------------------------------------------------

def iax_полный(источник, получатель, время, oseq, iseq, тип, подкласс, тело=b""):
    return struct.pack(">HHIBBBB", 0x8000 | источник, получатель, время, oseq, iseq, тип, подкласс) + тело


def ie(номер, значение):
    return bytes([номер, len(значение)]) + значение


IAX_NEW = iax_полный(2, 0, 3, 0, 0, 0x06, 0x01, ie(0x0B, b"\x00\x02") + ie(0x01, b"100") + ie(0x06, b"alice")
                     + ie(0x09, struct.pack(">I", 4)))
IAX_ГОЛОС = iax_полный(2, 5, 160, 3, 2, 0x02, 0x04, b"\xff" * 160)
IAX_МИНИ = struct.pack(">HH", 2, 320) + b"\xff" * 160


def skinny(ид, тело=b""):
    return struct.pack("<III", 4 + len(тело), 0, ид) + тело


SKINNY_REGISTER = skinny(0x0001, b"SEP001122334455\0" + struct.pack("<IIIII", 0, 1, 0x0200000A, 7, 0))
SKINNY_KEEPALIVE = skinny(0x0000)


class IAX2SkinnyTests(unittest.TestCase):
    def test_iax_new_и_голос(self):
        п = разобрать_пакет(udp(IAX_NEW, 4569, sport=4569))
        self.assertEqual(["IAX2"], п.стек[3:])
        ф = поля(п)
        self.assertEqual(([2], [0], [6], [1]), (ф["iax2.src_call"], ф["iax2.dst_call"], ф["iax2.type"],
                                                ф["iax2.iax.subclass"]))
        self.assertEqual((["2"], ["100"], ["alice"]), (ф["iax2.iax.version"], ф["iax2.iax.called_number"],
                                                       ф["iax2.iax.username"]))
        self.assertEqual([(UDP_НАГРУЗКА + 16, 5)], места(п, "iax2.iax.called_number"))
        self.assertIn("IAX NEW, вызов 2 → 0", п.инфо)
        п = разобрать_пакет(udp(IAX_ГОЛОС, 4569, sport=40001))
        self.assertEqual(([2], [4], [3], [2]), (поля(п)["iax2.type"], поля(п)["iax2.voice.csub"],
                                                поля(п)["iax2.oseqno"], поля(п)["iax2.iseqno"]))

    def test_мини_кадр(self):
        п = разобрать_пакет(udp(IAX_МИНИ, 4569, sport=4569))
        self.assertEqual((["IAX2"], [2], [320]), (п.стек[3:], поля(п)["iax2.src_call"], поля(п)["iax2.minits"]))
        # Проверить мини-кадр нечем: с другого порта — не признаём, пока аналитик не укажет.
        self.assertNotIn("IAX2", разобрать_пакет(udp(IAX_МИНИ, 4569, sport=40001)).стек)
        п = разобрать_пакет(udp(IAX_МИНИ, 4569, sport=40001), как={"udp:4569": "IAX2"})
        self.assertEqual("IAX2", п.протокол)
        # Номер вызова 0 у мини-кадра не бывает (первые два нуля — мета-кадр), пустой голос — тоже.
        self.assertNotIn("IAX2", разобрать_пакет(udp(b"\0\0" + IAX_МИНИ[2:], 4569, sport=4569)).стек)
        self.assertNotIn("IAX2", разобрать_пакет(udp(IAX_МИНИ[:4], 4569, sport=4569)).стек)

    def test_iax_проверки(self):
        кривые_ie = IAX_NEW + b"\x01"
        self.assertNotIn("IAX2", разобрать_пакет(udp(кривые_ie, 4569, sport=4569)).стек)
        два_кодека = iax_полный(2, 5, 160, 3, 2, 0x02, 0x06, b"\xff" * 20)
        self.assertNotIn("IAX2", разобрать_пакет(udp(два_кодека, 4569, sport=4569)).стек)
        dtmf = iax_полный(2, 5, 160, 3, 2, 0x01, ord("5"))
        self.assertEqual(["5"], поля(разобрать_пакет(udp(dtmf, 4569)))["iax2.dtmf.csub"])
        self.assertNotIn("IAX2", разобрать_пакет(udp(dtmf + b"x", 4569)).стек)

    def test_skinny(self):
        п = разобрать_пакет(tcp(SKINNY_REGISTER + SKINNY_KEEPALIVE, 2000))
        self.assertEqual(["SKINNY"], п.стек[3:])
        ф = поля(п)
        self.assertEqual(([1, 0], ["SEP001122334455"], [40, 4]), (ф["skinny.messageid"], ф["skinny.devicename"],
                                                                  ф["skinny.data_length"]))
        self.assertEqual([(TCP_НАГРУЗКА + 8, 4), (TCP_НАГРУЗКА + 48 + 8, 4)], места(п, "skinny.messageid"))
        self.assertNotIn("SKINNY", разобрать_пакет(tcp(SKINNY_KEEPALIVE + b"\0", 2000)).стек)
        self.assertNotIn("SKINNY", разобрать_пакет(tcp(skinny(0x10000), 2000)).стек)
        резерв = struct.pack("<III", 4, 0x01000000, 0)
        self.assertNotIn("SKINNY", разобрать_пакет(tcp(резерв, 2000)).стек)
        # Длина меньше 4 (нет места номеру сообщения) — даже если дальше лежит верное сообщение.
        self.assertNotIn("SKINNY", разобрать_пакет(tcp(b"\0" * 8 + SKINNY_KEEPALIVE, 2000)).стек)
        кнопка = skinny(0x0003, struct.pack("<I", 7))
        self.assertEqual([7], поля(разобрать_пакет(tcp(кнопка, 2000)))["skinny.keypadbutton"])

    def test_фильтр_и_обрыв(self):
        пакеты = [udp(IAX_NEW, 4569), udp(IAX_МИНИ, 4569, sport=4569), tcp(SKINNY_REGISTER, 2000)]
        self.assertEqual([0, 1], выбрать(пакеты, "iax2"))
        self.assertEqual([0], выбрать(пакеты, "iax2.iax.username == alice"))
        self.assertEqual([2], выбрать(пакеты, "skinny.messageId == 1"))
        for пакет in пакеты:
            без_сбоев(self, пакет)


# -- отрицательная проверка: случайная нагрузка на порты протоколов ----------------------------------

НАШИ = {"DNP3", "IEC104", "TPKT", "COTP", "SES", "PRES", "MMS", "S7comm", "S7comm-plus", "ENIP", "CIP", "BVLC",
        "BACnet", "BACapp", "OPCUA", "MGCP", "MEGACO", "H.248", "IAX2", "SKINNY", "SDP"}
ПОРТЫ = [("tcp", 20000), ("udp", 20000), ("tcp", 2404), ("tcp", 102), ("tcp", 44818), ("udp", 44818),
         ("udp", 2222), ("udp", 47808), ("tcp", 4840), ("udp", 2427), ("udp", 2727), ("udp", 2944),
         ("udp", 2945), ("tcp", 2944), ("tcp", 2945), ("udp", 4569), ("tcp", 2000),
         ("tcp", 31337), ("udp", 31337)]                  # последние — угадывание по сигнатуре


class СлучайныеДанныеTests(unittest.TestCase):
    def test_случайная_нагрузка_не_наша(self):
        сл = random.Random(1815)
        for транспорт, порт in ПОРТЫ:
            ложных = []
            for _ in range(2000):
                нагрузка = bytes(сл.getrandbits(8) for _ in range(сл.randint(1, 300)))
                sport = сл.randint(1024, 65535)
                if sport in (порт, 4569, 5060):
                    sport += 1
                пакет = (tcp if транспорт == "tcp" else udp)(нагрузка, порт, sport=sport)
                стек = разобрать_пакет(пакет).стек
                if НАШИ & set(стек):
                    ложных.append(стек)
            with self.subTest(транспорт=транспорт, порт=порт):
                self.assertEqual([], ложных)

    def test_sip_со_случайным_телом_без_sdp(self):
        сл = random.Random(8866)
        ложных = 0
        for _ in range(2000):
            тело = b"v=0" + bytes(сл.getrandbits(8) for _ in range(сл.randint(0, 200)))
            п = разобрать_пакет(udp(SIP_INVITE.split(b"\r\n\r\n")[0] + b"\r\n\r\n" + тело, 5060))
            ложных += "SDP" in п.стек
        self.assertEqual(0, ложных)


if __name__ == "__main__":
    unittest.main()


# -- SDP: полная раскладка и описания потоков ---------------------------------------------------------

from reportgen.setevoy.pole import Пакет  # noqa: E402
from reportgen.setevoy.protokoly import promyshlennye  # noqa: E402
from reportgen.setevoy.razbor import Разбор  # noqa: E402

SDP_ПОЛНЫЙ = (b"v=0\r\no=- 1 2 IN IP4 10.0.0.1\r\ns=-\r\ni=sess\r\nc=IN IP4 10.0.0.1\r\nt=0 0\r\na=sendrecv\r\n"
              b"m=audio 40000 RTP/AVP 8 101\r\ni=voice\r\nc=IN IP4 10.0.0.5\r\na=rtpmap:101 telephone-event/8000\r\n"
              b"a=ptime:20\r\nm=video 0 RTP/AVP 31\r\n\r\n\r\n")


def р_из(данные):
    return Разбор(Пакет(1, 0.0, данные, len(данные), "RAW"), None, {})


def дерево(у):
    итог = []

    def обойти(список, глубина):
        for x in список:
            итог.append((глубина, x.ключ, x.смещение, x.длина, x.сырое))
            обойти(x.дети, глубина + 1)
    обойти(у.поля, 0)
    return итог


class ТестSDPРаскладка(unittest.TestCase):
    def test_дерево_полей(self):
        собрано = []
        сборщик = lambda р, описания: собрано.append(описания)  # noqa: E731
        promyshlennye.SDP_ОПИСАНИЯ.append(сборщик)
        try:
            р = р_из(SDP_ПОЛНЫЙ)
            self.assertTrue(promyshlennye.sdp(р, 0, len(SDP_ПОЛНЫЙ)))
        finally:
            promyshlennye.SDP_ОПИСАНИЯ.remove(сборщик)
        у = р.п.уровни[0]
        self.assertEqual((у.длина, у.итог), (len(SDP_ПОЛНЫЙ), "audio 40000, video 0, адрес 10.0.0.1"))
        self.assertEqual(дерево(у), [
            (0, "sdp.version", 0, 3, "0"), (0, "sdp.owner", 5, 23, "- 1 2 IN IP4 10.0.0.1"),
            (1, "sdp.owner.username", 7, 1, "-"), (1, "sdp.owner.sessionid", 9, 1, "1"),
            (1, "sdp.owner.version", 11, 1, "2"), (1, "sdp.owner.network_type", 13, 2, "IN"),
            (1, "sdp.owner.address_type", 16, 3, "IP4"), (1, "sdp.owner.address", 20, 8, "10.0.0.1"),
            (0, "sdp.session_name", 30, 3, "-"), (0, "sdp.session_info", 35, 6, "sess"),
            (0, "sdp.connection_info", 43, 17, "IN IP4 10.0.0.1"),
            (1, "sdp.connection_info.network_type", 45, 2, "IN"), (1, "sdp.connection_info.address_type", 48, 3, "IP4"),
            (1, "sdp.connection_info.address", 52, 8, "10.0.0.1"), (0, "sdp.time", 62, 5, "0 0"),
            (0, "sdp.session_attr", 69, 10, "sendrecv"), (0, "sdp.media", 81, 102, "audio 40000 RTP/AVP 8 101"),
            (1, "sdp.media.media", 83, 5, "audio"), (1, "sdp.media.port", 89, 5, 40000),
            (1, "sdp.media.proto", 95, 7, "RTP/AVP"), (1, "sdp.media.format", 103, 1, "8"),
            (1, "sdp.media.format", 105, 3, "101"), (1, "sdp.media_title", 110, 7, "voice"),
            (1, "sdp.connection_info", 119, 17, "IN IP4 10.0.0.5"),
            (2, "sdp.connection_info.network_type", 121, 2, "IN"), (2, "sdp.connection_info.address_type", 124, 3, "IP4"),
            (2, "sdp.connection_info.address", 128, 8, "10.0.0.5"),
            (1, "sdp.media_attr", 138, 33, "rtpmap:101 telephone-event/8000"), (2, "sdp.rtpmap.pt", 147, 3, 101),
            (2, "sdp.mime.type", 151, 15, "telephone-event"), (2, "sdp.sample_rate", 167, 4, 8000),
            (1, "sdp.media_attr", 173, 10, "ptime:20"), (0, "sdp.media", 185, 20, "video 0 RTP/AVP 31"),
            (1, "sdp.media.media", 187, 5, "video"), (1, "sdp.media.port", 193, 1, 0),
            (1, "sdp.media.proto", 195, 7, "RTP/AVP"), (1, "sdp.media.format", 203, 2, "31")])
        self.assertEqual(собрано, [[
            {"вид": "audio", "порт": 40000, "протокол": "RTP/AVP", "форматы": ["8", "101"], "адрес": "10.0.0.5",
             "rtpmap": {101: "telephone-event/8000"}},
            {"вид": "video", "порт": 0, "протокол": "RTP/AVP", "форматы": ["31"], "адрес": "10.0.0.1", "rtpmap": {}}]])

    def test_строки_не_по_правилам(self):
        # rtpmap и c=/o= с другим числом частей — только строка; m= без числового порта — без описания.
        данные = (b"v=0\r\no=- 1 2 IN IP4\r\nc=IN IP4\r\nm=audio x RTP/AVP 0\r\na=rtpmap:x PCMU\r\n"
                  b"m=audio 5000\r\nm=image 6000 udptl t38\r\nc=IN IP4 10.0.0.9 extra\r\n")
        собрано = []
        сборщик = lambda р, описания: собрано.append(описания)  # noqa: E731
        promyshlennye.SDP_ОПИСАНИЯ.append(сборщик)
        try:
            р = р_из(данные)
            self.assertTrue(promyshlennye.sdp(р, 0, len(данные)))
        finally:
            promyshlennye.SDP_ОПИСАНИЯ.remove(сборщик)
        ключи = {x[1] for x in дерево(р.п.уровни[0])}
        for нет in ("sdp.owner.username", "sdp.connection_info.address", "sdp.rtpmap.pt"):
            self.assertNotIn(нет, ключи)
        self.assertEqual(собрано, [[{"вид": "image", "порт": 6000, "протокол": "udptl", "форматы": ["t38"],
                                     "адрес": "", "rtpmap": {}}]])
        # Атрибут и сведения до первого m= — сеанса, rtpmap до m= не относится к потоку.
        данные = b"v=0\r\no=- 1 2 IN IP4 1.2.3.4\r\na=rtpmap:96 X/1\r\ni=s\r\nm=audio 7000 RTP/AVP 96\r\n"
        собрано.clear()
        promyshlennye.SDP_ОПИСАНИЯ.append(сборщик)
        try:
            р = р_из(данные)
            self.assertTrue(promyshlennye.sdp(р, 0, len(данные)))
        finally:
            promyshlennye.SDP_ОПИСАНИЯ.remove(сборщик)
        д = дерево(р.п.уровни[0])
        self.assertIn((0, "sdp.session_attr", 29, 15, "rtpmap:96 X/1"), д)
        self.assertIn((0, "sdp.session_info", 46, 3, "s"), д)
        self.assertEqual(собрано[0][0]["rtpmap"], {})

    def собрать(self, данные, сдвиг=0):
        собрано = []
        сборщик = собрано.append
        promyshlennye.SDP_ОПИСАНИЯ.append(lambda р, описания: сборщик(описания))
        try:
            р = Разбор(Пакет(1, 0.0, bytes(сдвиг) + данные + b"x=1\r\n", сдвиг + len(данные) + 5, "RAW"), None, {})
            self.assertTrue(promyshlennye.sdp(р, сдвиг, сдвиг + len(данные)))
        finally:
            promyshlennye.SDP_ОПИСАНИЯ.pop()
        return р, собрано[0]

    def test_поток_без_форматов_и_длина_с_места(self):
        данные = b"v=0\r\no=- 1 2 IN IP4 1.2.3.4\r\nm=audio 5000 RTP/AVP\r\n"
        р, описания = self.собрать(данные, 16)
        self.assertEqual(описания, [{"вид": "audio", "порт": 5000, "протокол": "RTP/AVP", "форматы": [],
                                     "адрес": "", "rtpmap": {}}])
        self.assertEqual((р.п.уровни[0].смещение, р.п.уровни[0].длина), (16, len(данные)))

    def test_строки_потока_без_описания_не_к_прежнему(self):
        """c= и rtpmap потока с нечисловым портом не приписываются предыдущему потоку."""
        данные = (b"v=0\r\no=- 1 2 IN IP4 1.2.3.4\r\nm=audio 7000 RTP/AVP 0\r\nm=audio x RTP/AVP 8\r\n"
                  b"c=IN IP4 10.0.0.5\r\na=rtpmap:8 PCMA/8000\r\n")
        _, описания = self.собрать(данные)
        self.assertEqual(описания, [{"вид": "audio", "порт": 7000, "протокол": "RTP/AVP", "форматы": ["0"],
                                     "адрес": "", "rtpmap": {}}])
        # c= сеанса (до первого m=) — адрес по умолчанию; c= потока — только его.
        _, описания = self.собрать(b"v=0\r\no=- 1 2 IN IP4 1.2.3.4\r\nc=IN IP4 10.0.0.1\r\nm=audio 7000 RTP/AVP 0\r\n"
                                   b"m=audio 7002 RTP/AVP 0\r\nc=IN IP4 10.0.0.2\r\n")
        self.assertEqual([о["адрес"] for о in описания], ["10.0.0.1", "10.0.0.2"])

    def test_rtpmap_только_в_атрибуте(self):
        р, _ = self.собрать(b"v=0\r\no=- 1 2 IN IP4 1.2.3.4\r\ns=rtpmap:0 PCMU/8000\r\nm=audio 7000 RTP/AVP 0\r\n")
        self.assertNotIn("sdp.rtpmap.pt", {x[1] for x in дерево(р.п.уровни[0])})

    def test_отказы(self):
        for данные, что in ((b"v=1\r\no=- 1 2 IN IP4 1.2.3.4\r\n", "не v=0"), (b"v=0\r\n\r\n", "только v=0"),
                            (b"v=0\r\nX=1\r\nm=audio 1 RTP/AVP 0\r\n", "не строчная буква"),
                            (b"v=0\r\nx=1\r\nm=audio 1 RTP/AVP 0\r\n", "неизвестная строка"),
                            (b"v=0\r\ns=-\r\n", "нет o= и m="), (b"v=0\r\nm=audio 1 RTP/AVP 0\r\n\r\nx", "пустая строка внутри")):
            with self.subTest(что):
                р = р_из(данные)
                self.assertFalse(promyshlennye.sdp(р, 0, len(данные)))
                self.assertEqual(р.п.уровни, [])
