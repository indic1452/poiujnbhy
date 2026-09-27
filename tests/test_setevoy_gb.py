"""Интерфейс Gb: NS (48.016) поверх UDP и Frame Relay, BSSGP (48.018), LLC (44.064) с FCS CRC-24, SNDCP
(44.065) → IP; внутри LLC — GMM/SM (24.008) и SMS. Кадры собираются по стандартам; FCS считается здесь
побитно прямо по порождающему многочлену 44.064 6.1.2 (без таблицы модуля)."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import prilozh
from reportgen.setevoy.protokoly import gb, gsm, kanalnye
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет
from test_setevoy_bssap import личность
from test_setevoy_kanalnye import q922
from test_setevoy_oks7 import IMSI

#: 44.064 6.1.2: X^24+X^23+X^21+X^20+X^19+X^17+X^16+X^15+X^13+X^8+X^7+X^5+X^4+X^2+1; биты передаются
#: младшим вперёд — поэтому многочлен в отражённой записи.
_СТЕПЕНИ = (23, 21, 20, 19, 17, 16, 15, 13, 8, 7, 5, 4, 2, 0)
_ОТРАЖЁННЫЙ = int(f"{sum(1 << e for e in _СТЕПЕНИ):024b}"[::-1], 2)


def fcs(данные):
    c = 0xFFFFFF
    for б in данные:
        c ^= б
        for _ in range(8):
            c = (c >> 1) ^ _ОТРАЖЁННЫЙ if c & 1 else c >> 1
    return (~c & 0xFFFFFF).to_bytes(3, "little")


def tlv(iei, значение):
    дл = len(значение)
    return bytes([iei, 0x80 | дл] if дл < 128 else [iei, дл >> 8, дл & 0xFF]) + значение


def llc_ui(sapi, информация, nu=0, e=0, pm=1, cr=0):
    заголовок = bytes([cr << 6 | sapi]) + struct.pack(">H", 0xC000 | nu << 2 | e << 1 | pm)
    тело = заголовок + информация
    return тело + fcs(тело if pm else тело[:3 + 4])


def llc(sapi, управление, информация=b""):
    тело = bytes([sapi]) + управление + информация
    return тело + fcs(тело)


RAI = bytes([0x52, 0xF0, 0x10]) + struct.pack(">HB", 0x1234, 5)
ЯЧЕЙКА = RAI + struct.pack(">H", 66)
QOS = bytes([0x00, 0x64, 0x40 | 3])            # 100 × 1000 бит/с, приоритет 3


def ul(tlli, llc_pdu, qos=QOS):
    return bytes([0x01]) + struct.pack(">I", tlli) + qos + tlv(0x08, ЯЧЕЙКА) + tlv(0x0E, llc_pdu)


def dl(tlli, llc_pdu):
    return bytes([0x00]) + struct.pack(">I", tlli) + QOS + tlv(0x16, b"\x00\x64") + tlv(0x0E, llc_pdu)


def ns_ud(bssgp, bvci=2, биты=0):
    return bytes([0x00, биты]) + struct.pack(">H", bvci) + bssgp


def по_udp(данные, порт=23000, как=None):
    return разобрать_пакет(с.eth(с.ip(с.udp(данные, порт, порт), 17)), как=как)


def по_fr(данные, dlci=16):
    return разобрать_пакет(q922(dlci) + данные, "Frame Relay")


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


ИДЕНТ = личность(IMSI, 1)
ATTACH = b"\x08\x01" + b"\x02\xe5\xe0" + b"\x01" + b"\x00\x00" + bytes([len(ИДЕНТ)]) + ИДЕНТ + RAI
TLLI = 0xC0001234


class ТестBSSGP(unittest.TestCase):
    def test_ul_unitdata_с_attach(self):
        п = по_udp(ns_ud(ul(TLLI, llc_ui(1, ATTACH, nu=7))))
        self.assertEqual(п.стек[-6:], ["UDP", "NS", "BSSGP", "LLC", "GSM GMM", "Данные"])
        self.assertEqual(п.ошибки, [])
        ф = поля(п, "NS")
        self.assertEqual((ф["nsip.pdu_type"].текст, ф["nsip.bvci"].сырое), ("0x00 (NS_UNITDATA)", 2))
        self.assertEqual((ф["nsip.control_bits.r"].сырое, ф["nsip.control_bits.c"].сырое), (0, 0))
        б = поля(п, "BSSGP")
        self.assertEqual(б["bssgp.pdu_type"].текст, "0x01 (UL-UNITDATA)")
        self.assertEqual((б["bssgp.tlli"].текст, б["bssgp.tlli"].смещение), ("0xc0001234 (местный)", 47))
        self.assertEqual(б["bssgp.qos"].текст, "пиковая скорость 100000 бит/с, приоритет 3")
        self.assertEqual(б["bssgp.ie.08"].текст, "RAI 250-01, LAC 4660, RAC 5, CI 66")
        self.assertEqual((б["bssgp.ie.08"].смещение, б["bssgp.ie.08"].длина), (54, 10))
        self.assertEqual((б["bssgp.llc_pdu"].смещение, б["bssgp.llc_pdu"].длина), (64, 2 + 3 + len(ATTACH) + 3))
        л = поля(п, "LLC")
        self.assertEqual(л["llcgprs.sapi"].текст, "LLGMM")
        self.assertEqual(л["llcgprs.control"].текст, "UI, N(U) 7, защищено, без шифра")
        self.assertEqual((л["llcgprs.nu"].сырое, л["llcgprs.e"].сырое, л["llcgprs.pm"].сырое), (7, 0, 1))
        self.assertTrue(л["llcgprs.fcs"].текст.endswith("(верна)"), л["llcgprs.fcs"].текст)
        у = уровень(п, "LLC")
        self.assertEqual((у.смещение, у.длина), (66, 3))
        self.assertEqual(уровень(п, "GSM GMM").смещение, 69)
        self.assertEqual(п.инфо, "BSSGP UL-UNITDATA, TLLI 0xc0001234, RAI 250-01, LAC 4660, RAC 5, CI 66; "
                                 f"LLC LLGMM UI, N(U) 7; GSM GMM Attach Request: IMSI {IMSI}")

    def test_dl_unitdata_sndcp_ip(self):
        ip = с.ip(с.udp(с.dns_запрос("example.org"), 40000, 53), 17, src="10.1.1.2", dst="8.8.8.8")
        sn = bytes([0x60 | 5, 0x00]) + struct.pack(">H", 0x0003) + ip
        п = по_udp(ns_ud(dl(0x80001234, llc_ui(3, sn))))
        self.assertEqual(п.стек[-6:], ["BSSGP", "LLC", "SNDCP", "IPv4", "UDP", "DNS"])
        б = поля(п, "BSSGP")
        self.assertEqual(б["bssgp.tlli"].текст, "0x80001234 (чужой)")
        self.assertEqual(б["bssgp.ie.16"].текст, "1 с")
        s = поля(п, "SNDCP")
        self.assertEqual((s["sndcp.nsapi"].сырое, s["sndcp.f"].сырое, s["sndcp.t"].сырое, s["sndcp.m"].сырое),
                         (5, 1, 1, 0))
        self.assertEqual((s["sndcp.segment"].сырое, s["sndcp.npduField2"].сырое), (0, 3))
        self.assertEqual(уровень(п, "SNDCP").длина, 4)
        self.assertEqual(п.источник, "10.1.1.2")
        self.assertIn("example.org", п.инфо)
        self.assertTrue(п.инфо.startswith("BSSGP DL-UNITDATA, TLLI 0x80001234; LLC LL3 UI, N(U) 0; DNS"), п.инфо)

    def test_sndcp_виды(self):
        ip = с.ip(с.udp(b"x", 1, 2), 17)
        # Подтверждаемый режим: DCOMP/PCOMP и номер N-PDU в 8 бит.
        п = по_udp(ns_ud(ul(TLLI, llc(5, b"\x00\x00\x00", bytes([0x40 | 6, 0x00, 9]) + ip))))
        s = поля(п, "SNDCP")
        self.assertEqual((s["sndcp.npduField1"].сырое, уровень(п, "SNDCP").длина), (9, 3))
        self.assertEqual(уровень(п, "SNDCP").итог, "NSAPI 6, SN-DATA, N-PDU 9")
        self.assertEqual(п.стек[-4:], ["SNDCP", "IPv4", "UDP", "Данные"])
        # Сжатие, сегменты, не IP.
        for sn, что in ((bytes([0x65, 0x10, 0, 1]) + ip, "сжато"), (bytes([0x75, 0x00, 0, 1]) + ip, "сегмент N-PDU"),
                        (bytes([0x25, 0x10, 1]) + ip, "сегмент N-PDU"), (bytes([0x65, 0x00, 0, 1]) + b"\x10abc",
                                                                         "сегмент N-PDU")):
            with self.subTest(sn=sn[:4].hex()):
                п = по_udp(ns_ud(ul(TLLI, llc_ui(3, sn))))
                self.assertEqual(п.стек[-2:], ["SNDCP", "Данные"])
                self.assertEqual(уровень(п, "Данные").полное, "Данные: SNDCP: " + что)
        # Не первый сегмент: без байта сжатия.
        п = по_udp(ns_ud(ul(TLLI, llc_ui(3, bytes([0x25, 0x20, 0x07]) + b"rest"))))
        s = поля(п, "SNDCP")
        self.assertEqual((s["sndcp.segment"].сырое, s["sndcp.npduField2"].сырое), (2, 7))
        self.assertEqual(уровень(п, "SNDCP").длина, 3)
        # Только заголовок — без данных; заголовок длиннее поля — SNDCP нет.
        п = по_udp(ns_ud(ul(TLLI, llc_ui(3, bytes([0x65, 0x00, 0, 1])))))
        self.assertEqual(п.стек[-1], "SNDCP")
        п = по_udp(ns_ud(ul(TLLI, llc_ui(3, bytes([0x65, 0x00, 0])))))
        self.assertEqual(п.стек[-2:], ["LLC", "Данные"])

    def test_сигнальные_pdu(self):
        paging = (bytes([0x06]) + tlv(0x0D, ИДЕНТ) + tlv(0x0A, b"\x00\x00") + tlv(0x04, b"\x00\x02")
                  + tlv(0x18, QOS) + tlv(0x20, b"\xde\xad\xbe\xef"))
        п = по_udp(ns_ud(paging, bvci=0))
        б = поля(п, "BSSGP")
        self.assertEqual(б["bssgp.ie.0d"].текст, f"IMSI {IMSI}")
        self.assertEqual(б["bssgp.ie.04"].текст, "2")
        self.assertEqual(б["bssgp.ie.20"].текст, "DEADBEEF")
        self.assertEqual(б["bssgp.ie.0a"].текст, "0000")
        self.assertEqual(п.инфо, f"BSSGP PAGING-PS, IMSI {IMSI}, BVCI 2")
        suspend = bytes([0x0B]) + tlv(0x1F, struct.pack(">I", 0x7A000001)) + tlv(0x1B, RAI)
        п = по_udp(ns_ud(suspend, bvci=0))
        self.assertEqual(п.инфо, "BSSGP SUSPEND, 0x7a000001 (случайный), RAI 250-01, LAC 4660, RAC 5")
        fc = (bytes([0x26]) + tlv(0x1E, b"\x07") + tlv(0x05, b"\x00\x0a") + tlv(0x03, b"\x00\x14")
              + tlv(0x01, b"\x00\x02") + tlv(0x1C, b"\x00\x05") + tlv(0x3E, b"\x00\x65"))
        б = поля(по_udp(ns_ud(fc, bvci=0)), "BSSGP")
        self.assertEqual([б[k].текст for k in ("bssgp.ie.1e", "bssgp.ie.05", "bssgp.ie.03", "bssgp.ie.01",
                                               "bssgp.ie.1c", "bssgp.ie.3e")],
                         ["7", "1000 октетов", "2000 бит/с", "200 октетов", "500 бит/с", "101"])
        п = по_udp(ns_ud(bytes([0x22]) + tlv(0x04, b"\x00\x02") + tlv(0x07, b"\x08") + tlv(0x10, RAI[:5]), bvci=0))
        б = поля(п, "BSSGP")
        self.assertEqual(б["bssgp.ie.07"].текст, "O&M intervention")
        self.assertEqual(б["bssgp.ie.10"].текст, "LAI 250-01, LAC 4660")
        self.assertEqual(п.инфо, "BSSGP BVC-RESET, BVCI 2, Cause O&M intervention")
        # Неизвестный элемент, причина вне таблицы, длинный элемент (длина в 2 октета), бесконечный срок.
        п = по_udp(ns_ud(bytes([0x41]) + tlv(0x07, b"\x7f") + tlv(0xF0, bytes(40)) + tlv(0x16, b"\xff\xff")
                         + tlv(0x15, bytes(200)), bvci=0))
        б = поля(п, "BSSGP")
        self.assertEqual((б["bssgp.ie.07"].текст, б["bssgp.ie.f0"].текст), ("0x7f", "40 байт"))
        self.assertEqual((б["bssgp.ie.16"].текст, б["bssgp.ie.15"].текст), ("бесконечно", "200 байт"))
        self.assertEqual(б["bssgp.ie.15"].длина, 203)
        self.assertEqual(б["bssgp.ie.f0"].имя, "IEI 0xf0: 40 байт")

    def test_значения(self):
        self.assertIsNone(gb._значение(0x04, b"\x01"))
        self.assertIsNone(gb._значение(0x08, bytes(7)))
        self.assertIsNone(gb._значение(0x0D, b"\x05"))
        self.assertEqual(gb._значение(0x1E, b"\x01\x02"), None)
        self.assertEqual(gb._значение(0x9B, RAI), "RAI 250-01, LAC 4660, RAC 5")
        self.assertEqual(gb._значение(0x11, b"\xf4\x01\x02\x03\x04"), "TMSI 01020304")
        self.assertEqual(gb._значение(0x70, ИДЕНТ), f"IMSI {IMSI}")
        self.assertEqual(gb._значение(0x12, b"\x00\x01"), "100 октетов")
        self.assertEqual(gb._значение(0x16, b"\x01\x2c"), "3 с")
        self.assertEqual(gb._значение(0x16, b"\x00\x05"), "0.05 с")
        for tlli, вид in ((0xC0000000, "местный"), (0xFFFFFFFF, "местный"), (0x80000000, "чужой"),
                          (0xBFFFFFFF, "чужой"), (0x78000000, "случайный"), (0x7FFFFFFF, "случайный"),
                          (0x70000000, "вспомогательный"), (0x77FFFFFF, "вспомогательный")):
            self.assertEqual(gb._tlli(tlli), f"0x{tlli:08x} ({вид})")
        self.assertEqual(gb._tlli(0x6FFFFFFF), "0x6fffffff")
        for байт, скорость in ((0x00, "10000 бит/с"), (0x40, "100000 бит/с"), (0x80, "1000000 бит/с"),
                               (0xC0, "10000000 бит/с")):
            self.assertEqual(gb._qos(bytes([0, 100, байт | 5])), f"пиковая скорость {скорость}, приоритет 5")
        self.assertEqual(gb._qos(b"\x00\x00\x07"), "пиковая скорость наилучшая попытка, приоритет 7")

    def test_не_bssgp(self):
        for плохое, что in ((bytes([0x03]) + bytes(4), "тип Reserved"), (bytes([0x01]) + bytes(6), "UNITDATA короче 8"),
                            (bytes([0x41, 0x07, 0x82, 0x01]), "элемент длиннее"), (bytes([0x41, 0x07]), "нет длины"),
                            (bytes([0x41, 0x07, 0x00]), "длина в 2 октета без второго"), (bytes([0xFF]), "тип 0xff")):
            with self.subTest(что):
                п = по_udp(ns_ud(плохое))
                self.assertNotIn("NS", п.стек)
        # Элементы после LLC-PDU показываются данными.
        pdu = ul(TLLI, llc_ui(1, ATTACH)) + tlv(0x1F, bytes(4))
        п = по_udp(ns_ud(pdu))
        данные = [x for x in п.уровни if x.протокол == "Данные"]
        self.assertEqual(данные[-1].полное, "Данные: элементы BSSGP после LLC-PDU")
        self.assertEqual((данные[-1].смещение, данные[-1].длина), (42 + 4 + len(pdu) - 6, 6))


class ТестLLC(unittest.TestCase):
    def разбор(self, кадр):
        return по_udp(ns_ud(ul(TLLI, кадр)))

    def test_fcs(self):
        self.assertEqual(gb.crc24(b"123456789").to_bytes(3, "little"), fcs(b"123456789"))
        self.assertEqual(gb.crc24(b""), 0)
        кадр = bytearray(llc_ui(1, ATTACH))
        кадр[-1] ^= 1
        п = self.разбор(bytes(кадр))
        self.assertEqual(len(п.ошибки), 1)
        self.assertTrue(п.ошибки[0].startswith("LLC: FCS не сходится"), п.ошибки)
        ф = поля(п, "LLC")["llcgprs.fcs"]
        self.assertTrue(ф.плохо)
        self.assertIn("должна быть", ф.текст)
        self.assertEqual((ф.смещение, ф.длина), (64 + 2 + len(кадр) - 3, 3))
        # PM=0: FCS — на заголовок и первые 4 октета: изменение дальше их FCS не портит.
        кадр = bytearray(llc_ui(1, ATTACH, pm=0))
        кадр[3 + 5] ^= 0xFF
        п = self.разбор(bytes(кадр))
        self.assertTrue(поля(п, "LLC")["llcgprs.fcs"].текст.endswith("(верна)"))
        кадр[3 + 3] ^= 0xFF
        self.assertTrue(self.разбор(bytes(кадр)).ошибки)
        # Короткое поле информации при PM=0: FCS до конца информации.
        п = self.разбор(llc_ui(1, b"\x08\x03", pm=0))
        self.assertEqual(п.ошибки, [])

    def test_шифрование(self):
        кадр = llc_ui(3, b"\x12\x34\x56\x78\x9a", e=1)
        кадр = кадр[:-3] + b"\x00\x00\x00"
        п = self.разбор(кадр)
        self.assertEqual(п.ошибки, [])
        ф = поля(п, "LLC")
        self.assertIn("возможно, из-за шифрования", ф["llcgprs.fcs"].текст)
        self.assertFalse(ф["llcgprs.fcs"].плохо)
        self.assertEqual(ф["llcgprs.control"].текст, "UI, N(U) 0, защищено, зашифровано")
        self.assertEqual(уровень(п, "Данные").полное, "Данные: LLC: зашифровано (GEA)")
        self.assertTrue(п.инфо.endswith("LLC LL3 UI, N(U) 0, зашифровано"), п.инфо)
        # Верная FCS у зашифрованного — без оговорки.
        п = self.разбор(llc_ui(3, b"\x12\x34", e=1, pm=0))
        self.assertTrue(поля(п, "LLC")["llcgprs.fcs"].текст.endswith("(верна)"))

    def test_виды_кадров(self):
        п = self.разбор(llc(1, bytes([0x20 | 0x05, 0x40 | 0x08, 0x0C | 0x01]), ATTACH))
        ф = поля(п, "LLC")
        self.assertEqual(ф["llcgprs.control"].текст, "I, N(S) 84, N(R) 3, ACK")
        self.assertEqual(уровень(п, "LLC").длина, 4)
        self.assertEqual(п.стек[-2], "GSM GMM")
        п = self.разбор(llc(1, bytes([0x00, 0x00, 0x03]), bytes([0x02, 0xAA, 0xBB, 0xCC]) + ATTACH))
        ф = поля(п, "LLC")
        self.assertEqual((ф["llcgprs.control"].текст, ф["llcgprs.sack"].текст), ("I, N(S) 0, N(R) 0, SACK", "3 байт"))
        self.assertEqual(уровень(п, "LLC").длина, 1 + 3 + 4)
        п = self.разбор(llc(1, bytes([0x80 | 0x01, 0x0A])))
        self.assertEqual(поля(п, "LLC")["llcgprs.control"].текст, "S, RNR, N(R) 66")
        self.assertEqual(п.стек[-1], "LLC")
        п = self.разбор(llc(1, bytes([0xE0 | 0x10 | 0x07])))
        self.assertEqual(поля(п, "LLC")["llcgprs.control"].текст, "U, SABM, P/F 1")
        self.assertEqual(п.инфо, "BSSGP UL-UNITDATA, TLLI 0xc0001234, RAI 250-01, LAC 4660, RAC 5, CI 66; LLC LLGMM U, SABM")
        п = self.разбор(llc(1, bytes([0xE0 | 0x0E])))
        self.assertEqual(поля(п, "LLC")["llcgprs.control"].текст, "U, команда 0xe, P/F 0")
        # XID: версия (1 октет), N201-U (2 октета), Layer-3 Parameters с длиной в 6 бит (XL).
        xid = bytes([0x00 << 2 | 1, 0]) + bytes([0x05 << 2 | 2, 0x05, 0xDC]) + bytes([0x80 | 0x0B << 2, 5 << 2]) + bytes(5)
        п = self.разбор(llc(1, bytes([0xE0 | 0x0B]), xid))
        т = [x for x in уровень(п, "LLC").поля if x.ключ == "llcgprs.xid.type"]
        self.assertEqual([(x.текст, x.смещение, x.длина) for x in т], [("0", 68, 2), ("1500", 70, 3), ("5 байт", 73, 7)])
        self.assertTrue(п.инфо.endswith("LLC LLGMM U, XID: Version=0, N201-U=1500, Layer-3 Parameters"), п.инфо)
        self.assertEqual(уровень(п, "LLC").длина, 2 + len(xid))
        п = self.разбор(llc(1, bytes([0xEB]), bytes([0x05 << 2 | 2, 0x05])))
        self.assertEqual(п.ошибки[-1], "LLC: параметр XID длиннее кадра")
        п = self.разбор(llc(1, bytes([0xEB]), bytes([0x80 | 0x0B << 2])))
        self.assertEqual([x for x in уровень(п, "LLC").поля if x.ключ == "llcgprs.xid.type"], [])
        self.assertEqual(gb.XID[0xC], "Reset")

    def test_sms_и_прочие_sapi(self):
        cp = b"\x09\x01\x03\x00\x01\x02"
        п = self.разбор(llc_ui(7, cp))
        self.assertEqual(п.стек[-3:], ["LLC", "GSM SMS", "Данные"])
        п = self.разбор(llc_ui(2, b"\x01\x02"))
        self.assertEqual(уровень(п, "Данные").полное, "Данные: LLC SAPI 2")
        п = self.разбор(llc_ui(1, b"\x08\xff"))
        self.assertEqual(уровень(п, "Данные").полное, "Данные: LLC SAPI 1")
        self.assertEqual(поля(п, "LLC")["llcgprs.sapi"].имя, "Адрес: SAPI 1 (LLGMM)")
        # Без информации — только заголовок; короче 5 байт и PD=1 — не LLC.
        п = self.разбор(llc_ui(1, b""))
        self.assertEqual(п.стек[-1], "LLC")
        for плохой in (b"\x01\xc0\x00\x00", b"\x81\xc0\x00" + fcs(b"\x81\xc0\x00")):
            with self.subTest(плохой=плохой.hex()):
                п = self.разбор(плохой)
                self.assertEqual(уровень(п, "Данные").полное, "Данные: LLC-PDU (не LLC)")
        п = self.разбор(bytes([0x01, 0x00, 0x00]) + fcs(b"\x01\x00\x00")[:2])
        self.assertIn("LLC: кадр короче заголовка и FCS", п.ошибки)


class ТестGPRS_L3(unittest.TestCase):
    def разбор(self, l3):
        return по_udp(ns_ud(ul(TLLI, llc_ui(1, l3))))

    def test_sm(self):
        apn = b"\x08internet\x03mts\x02ru"
        req = b"\x0a\x41\x05\x03" + b"\x03\x00\x00\x00" + b"\x02\x01\x21" + tlv(0x28, apn)[:1] + bytes([len(apn)]) + apn
        п = self.разбор(req)
        self.assertEqual(п.стек[-2:], ["GSM SM", "Данные"])
        self.assertTrue(п.инфо.endswith("GSM SM Activate PDP Context Request: APN internet.mts.ru"), п.инфо)
        acc = b"\x8a\x42\x03" + b"\x03\x00\x00\x00" + b"\x01" + b"\x2b\x06\x01\x21\x0a\x01\x02\x03"
        п = self.разбор(acc)
        self.assertTrue(п.инфо.endswith("Activate PDP Context Accept: адрес 10.1.2.3"), п.инфо)
        self.assertTrue(self.разбор(b"\x0a\x46\x24").инфо.endswith("Deactivate PDP Context Request: причина SM 36"))
        self.assertTrue(self.разбор(b"\x0a\x43\x1b").инфо.endswith("Activate PDP Context Reject: причина SM 27"))

    def test_адрес_pdp_и_tlv(self):
        v6 = bytes(range(16))
        self.assertEqual(gsm._адрес_pdp(b"\x01\x57" + v6), "1:203:405:607:809:a0b:c0d:e0f")
        self.assertEqual(gsm._адрес_pdp(b"\x01\x8d\x0a\x00\x00\x01" + v6), "10.0.0.1, 1:203:405:607:809:a0b:c0d:e0f")
        for плохой in (b"\x00\x21\x0a\x00\x00\x01", b"\x01\x21\x0a\x00\x00", b"\x01\x57" + v6[:15], b"\x01",
                       b"\x01\x8d" + v6, b"\x01\x99\x00\x00\x00\x00", b"\xf1\x21"):
            self.assertEqual(gsm._адрес_pdp(плохой), "", плохой.hex())
        self.assertEqual(gsm._адрес_pdp(b"\xf1\x21\x0a\x00\x00\x01"), "10.0.0.1")
        self.assertEqual(list(gsm._tlv(b"\xa1\x28\x01\x05\x27\x00", 0)), [(0x28, 3, 1), (0x27, 6, 0)])
        self.assertEqual(list(gsm._tlv(b"\x28\x05\x01", 0)), [])
        self.assertEqual(list(gsm._tlv(b"\x28", 0)), [])
        self.assertEqual(list(gsm._tlv(b"\x28\x02\x01", 0)), [])          # значение длиннее тела на байт
        self.assertEqual(gsm._адрес_pdp(b""), "")
        self.assertEqual(gsm._apn(b"\x03abc"), "abc")
        self.assertEqual(gsm._apn(b""), "")
        # APN не найден, адреса нет — без сведений; Accept без адреса.
        п = self.разбор(b"\x0a\x41\x05\x03\x01\x00\x01\x00" + tlv(0x27, b"\x80")[:1] + b"\x01\x80")
        self.assertTrue(п.инфо.endswith("GSM SM Activate PDP Context Request"), п.инфо)
        п = self.разбор(b"\x8a\x42\x03\x01\x00\x01")
        self.assertTrue(п.инфо.endswith("GSM SM Activate PDP Context Accept"), п.инфо)
        п = self.разбор(b"\x8a\x42\x03\x01\x00\x01\x2b\x02\x01\x99")
        self.assertTrue(п.инфо.endswith("GSM SM Activate PDP Context Accept"), п.инфо)

    def test_gmm(self):
        п = self.разбор(b"\x08\x16" + bytes([len(ИДЕНТ)]) + ИДЕНТ)
        self.assertTrue(п.инфо.endswith(f"GSM GMM Identity Response: IMSI {IMSI}"), п.инфо)
        п = self.разбор(b"\x08\x16\x00")
        self.assertTrue(п.инфо.endswith("GSM GMM Identity Response"), п.инфо)
        п = self.разбор(b"\x08\x01\x00\x01\x00\x00\x00")
        self.assertTrue(п.инфо.endswith("GSM GMM Attach Request"), п.инфо)
        п = self.разбор(b"\x08\x21")
        self.assertTrue(п.инфо.endswith("GSM GMM GMM Information"), п.инфо)
        self.assertEqual((len(gsm.GMM), len(gsm.SM)), (23, 29))


class ТестNS(unittest.TestCase):
    def test_reset_и_простые(self):
        reset = bytes([0x02]) + tlv(0x00, b"\x01") + tlv(0x01, b"\x00\x05") + tlv(0x04, b"\x00\x64")
        п = по_udp(reset)
        self.assertEqual(п.стек[-1], "NS")
        self.assertEqual(п.инфо, "NS NS_RESET, причина: o&m intervention, NS-VCI 5, NSEI 100")
        э = [x for x in уровень(п, "NS").поля if x.ключ.startswith("nsip.ie")]
        self.assertEqual([(x.имя, x.смещение, x.длина) for x in э],
                         [("Причина: O&M intervention", 43, 3), ("NS-VCI: 5", 46, 4), ("NSEI: 100", 50, 4)])
        for тип, имя in ((0x0A, "NS_ALIVE"), (0x0B, "NS_ALIVE_ACK"), (0x06, "NS_UNBLOCK"), (0x07, "NS_UNBLOCK_ACK")):
            self.assertEqual(по_udp(bytes([тип])).инфо, f"NS {имя}")
        self.assertEqual(по_udp(bytes([0x04]) + tlv(0x00, b"\x7f") + tlv(0x01, b"\x00\x05")).инфо,
                         "NS NS_BLOCK, причина: 0x7f, NS-VCI 5")
        п = по_udp(bytes([0x08]) + tlv(0x00, b"\x0a") + tlv(0x02, bytes([0x0A])) + tlv(0x03, b"\x00\x02"))
        self.assertEqual(п.инфо, "NS NS_STATUS, причина: pdu not compatible with the protocol state, BVCI 2")
        self.assertEqual(уровень(п, "NS").поля[2].имя, "NS PDU (1 байт)")

    def test_sns(self):
        ip4 = bytes([10, 0, 0, 1]) + struct.pack(">HBB", 23000, 1, 2)
        ip6 = bytes(15) + b"\x01" + struct.pack(">HBB", 23001, 3, 4)
        config = bytes([0x0F, 0x01]) + tlv(0x04, b"\x00\x64") + tlv(0x05, ip4) + tlv(0x06, ip6)
        п = по_udp(config)
        ф = поля(п, "NS")
        self.assertEqual((ф["nsip.end_flag"].сырое, ф["nsip.end_flag"].смещение), (1, 43))
        э = [x for x in уровень(п, "NS").поля if x.ключ in ("nsip.ie.05", "nsip.ie.06")]
        self.assertEqual([x.имя for x in э], ["List of IP4 Elements: 10.0.0.1:23000", "List of IP6 Elements: ::1:23001"])
        self.assertEqual((ф["nsip.ip_element.signalling_weight"].сырое, ф["nsip.ip_element.data_weight"].сырое), (1, 2))
        self.assertEqual((ф["nsip.ip_element"].смещение, ф["nsip.ip_element"].длина), (50, 8))
        self.assertEqual(п.инфо, "NS SNS_CONFIG, NSEI 100")
        self.assertEqual(поля(по_udp(bytes([0x0F, 0x00]) + tlv(0x04, b"\x00\x64") + tlv(0x05, b"")), "NS")["nsip.ie.05"].имя,
                         "List of IP4 Elements: пусто")
        ack = bytes([0x0C]) + tlv(0x04, b"\x00\x64") + b"\x07" + tlv(0x00, b"\x13") + bytes([0x0B, 1, 10, 0, 0, 9])
        п = по_udp(ack)
        ф = поля(п, "NS")
        self.assertEqual((ф["nsip.transaction_id"].сырое, ф["nsip.transaction_id"].смещение), (7, 47))
        self.assertEqual(п.инфо, "NS SNS_ACK, NSEI 100, причина: unknown ip address, адрес 10.0.0.9")
        dele = bytes([0x11]) + tlv(0x04, b"\x00\x64") + b"\x01" + bytes([0x0B, 2]) + bytes(15) + b"\x02"
        self.assertEqual(по_udp(dele).инфо, "NS SNS_DELETE, NSEI 100, адрес ::2")
        size = bytes([0x12]) + tlv(0x04, b"\x00\x64") + b"\x0a\x01" + b"\x07\x00\x08" + b"\x08\x00\x02" + b"\x09\x00\x00"
        п = по_udp(size)
        имена = [x.имя for x in уровень(п, "NS").поля]
        self.assertEqual(имена[2:], ["Reset Flag: 1", "Maximum Number of NS-VCs: 8", "Number of IP4 Endpoints: 2",
                                     "Number of IP6 Endpoints: 0"])
        for плохое, что in ((bytes([0x0C]) + tlv(0x00, b"\x01") + b"\x07", "первым не NSEI"),
                            (bytes([0x0C]) + tlv(0x04, b"\x00\x64"), "нет номера транзакции"),
                            (bytes([0x0C]) + tlv(0x04, b"\x00\x64") + b"\x07" + b"\x00", "хвост не TLV"),
                            (bytes([0x0C]), "нет элементов"),
                            (bytes([0x11]) + tlv(0x04, b"\x00\x64") + b"\x01" + bytes([0x0B, 3, 1, 2, 3, 4]), "вид адреса 3"),
                            (bytes([0x11]) + tlv(0x04, b"\x00\x64") + b"\x01" + bytes([0x0B]), "нет вида адреса"),
                            (bytes([0x12]) + tlv(0x04, b"\x00\x64") + b"\x07\x00", "TV короче"),
                            (bytes([0x01]), "тип не назначен"), (bytes([0x02, 0x00, 0x81]), "TLV длиннее"),
                            (bytes([0x02, 0x00]), "нет длины"), (bytes([0x02, 0x00, 0x00, 0x01]), "длина 2 октета, данных нет")):
            with self.subTest(что):
                self.assertNotIn("NS", по_udp(плохое).стек)

    def test_unitdata_проверки(self):
        pdu = ul(TLLI, llc_ui(1, ATTACH))
        for плохое, что in ((ns_ud(pdu, биты=0x04), "запасные биты"), (ns_ud(b""), "нет BSSGP"),
                            (ns_ud(b"\x03\x00"), "не BSSGP"), (b"\x00\x00\x00", "короче 5")):
            with self.subTest(что):
                self.assertNotIn("NS", по_udp(плохое).стек)
        п = по_udp(ns_ud(pdu, bvci=77, биты=0x03))
        ф = поля(п, "NS")
        self.assertEqual((ф["nsip.control_bits.r"].сырое, ф["nsip.control_bits.c"].сырое, ф["nsip.bvci"].сырое),
                         (1, 1, 77))
        self.assertEqual(уровень(п, "NS").итог, "NS_UNITDATA, BVCI 77")
        # Длина элемента в 2 октета у LLC-PDU.
        большое = llc_ui(3, bytes([0x65, 0x10, 0, 1]) + bytes(200))
        п = по_udp(ns_ud(ul(TLLI, большое)))
        self.assertEqual(п.стек[-3:], ["LLC", "SNDCP", "Данные"])
        self.assertEqual(поля(п, "BSSGP")["bssgp.llc_pdu"].длина, 3 + len(большое))

    def test_порты_как_fr(self):
        данные = ns_ud(ul(TLLI, llc_ui(1, ATTACH)))
        for порт in (2157, 19999, 23000):
            self.assertIn("BSSGP", по_udp(данные, порт).стек)
        п = по_udp(данные, 4000, как={"udp:4000": "NS (Gb)"})
        self.assertIn("BSSGP", п.стек)
        # Frame Relay без инкапсуляции RFC 2427: NS сразу после адреса.
        п = по_fr(данные)
        self.assertEqual(п.стек[:5], ["FR", "NS", "BSSGP", "LLC", "GSM GMM"])
        self.assertEqual((уровень(п, "FR").длина, уровень(п, "NS").смещение), (2, 2))
        self.assertEqual(уровень(п, "FR").итог, "DLCI 16")
        # NS_RESET_ACK (0x03) похож на поле управления UI, но за ним не NLPID.
        п = по_fr(bytes([0x03]) + tlv(0x01, b"\x00\x05") + tlv(0x04, b"\x00\x64"))
        self.assertEqual(п.стек, ["FR", "NS"])
        self.assertEqual(п.инфо, "NS NS_RESET_ACK, NS-VCI 5, NSEI 100")
        # Обычный FR с NLPID — как раньше; не NS — данные.
        п = по_fr(b"\x03\xcc" + с.ip(с.udp(b"x", 1, 2), 17))
        self.assertEqual(п.стек[:2], ["FR", "IPv4"])
        п = по_fr(b"\x55\x01\x02")
        self.assertEqual(п.стек, ["FR", "Данные"])
        п = по_fr(b"\x03\x99\x01\x02")
        self.assertEqual(п.стек, ["FR", "Данные"])
        self.assertEqual(по_fr(b"\x03\x99").стек, ["FR"])
        self.assertIn(gb.ns, kanalnye.FR_БЕЗ_NLPID)

    def test_регистрация(self):
        for порт in (2157, 19999, 23000):
            self.assertIs(prilozh.ПОРТЫ_UDP[порт], gb.ns)
        self.assertIs(prilozh.КАК["udp"]["NS (Gb)"], gb.ns)
        self.assertEqual([ДОП_УРОВНИ[x] for x in ("NS", "BSSGP", "LLC", "SNDCP")],
                         ["канальный", "сетевой", "канальный", "сетевой"])
        self.assertEqual((len(gb.NS_PDU), len(gb.NS_ПРИЧИНЫ), len(gb.BSSGP_ЭЛЕМЕНТЫ), len(gb.BSSGP_ПРИЧИНЫ)),
                         (18, 18, 133, 76))
        self.assertEqual(len(gb.BSSGP_PDU), 148)


if __name__ == "__main__":
    unittest.main()


# -- эталонные раскладки и прямые вызовы ---------------------------------------------------------------

from reportgen.setevoy.pole import Пакет  # noqa: E402
from reportgen.setevoy.razbor import Разбор  # noqa: E402

#: Прямые вызовы — не с нуля: иначе «конец − м» и «конец + м» не различить.
С = 16


def р_из(данные):
    return Разбор(Пакет(1, 0.0, bytes(С) + данные, С + len(данные), "RAW"))


def дерево(р):
    """[(протокол, место, длина), [(глубина, ключ, место, длина, сырое)]] по уровням."""
    def обойти(список, глубина, куда):
        for x in список:
            куда.append((глубина, x.ключ, x.смещение - С, x.длина, x.сырое))
            обойти(x.дети, глубина + 1, куда)
        return куда
    return [((у.протокол, у.смещение - С, у.длина), обойти(у.поля, 0, [])) for у in р.п.уровни]


class ТестЭталонGb(unittest.TestCase):
    def test_ns_bssgp_llc(self):
        д = ns_ud(ul(TLLI, llc_ui(1, b"\x08\x21", nu=5)), bvci=0x1234, биты=0x02)
        р = р_из(д)
        self.assertTrue(gb.ns(р, С, С + len(д)))
        self.assertEqual(дерево(р), [
            (("NS", 0, 4), [(0, "nsip.pdu_type", 0, 1, 0), (0, "nsip.control_bits", 1, 1, 2),
                            (1, "nsip.control_bits.r", 1, 1, 0), (1, "nsip.control_bits.c", 1, 1, 1),
                            (0, "nsip.bvci", 2, 2, 0x1234)]),
            (("BSSGP", 4, 20), [(0, "bssgp.pdu_type", 4, 1, 1), (0, "bssgp.tlli", 5, 4, TLLI),
                                (0, "bssgp.qos", 9, 3, "006443"), (0, "bssgp.ie.08", 12, 10, 8),
                                (0, "bssgp.llc_pdu", 22, 10, 14)]),
            (("LLC", 24, 3), [(0, "llcgprs.sapi", 24, 1, 1), (1, "llcgprs.cr", 24, 1, 0),
                              (0, "llcgprs.control", 25, 2, 0xC015), (0, "llcgprs.nu", 25, 2, 5),
                              (0, "llcgprs.e", 26, 1, 0), (0, "llcgprs.pm", 26, 1, 1),
                              (0, "llcgprs.fcs", 29, 3, int.from_bytes(fcs(д[24:29]), "little"))]),
            (("GSM GMM", 27, 2), [(0, "gsm_a.L3_protocol_discriminator", 27, 1, 8), (0, "gsm_a.skip.ind", 27, 1, 0),
                                  (0, "gsm_a.dtap.msg_gmm_type", 28, 1, 0x21)])])
        # Без LLC-PDU уровень BSSGP — до конца; с элементом после LLC-PDU — данные за ним.
        р = р_из(ns_ud(bytes([0x41]) + tlv(0x07, b"\x01")))
        self.assertTrue(gb.ns(р, С, С + 8))
        self.assertEqual(р.п.уровни[1].длина, 4)

    def test_sns(self):
        ip4 = bytes([10, 0, 0, 1]) + struct.pack(">HBB", 23000, 1, 2)
        д = bytes([0x0F, 0x01]) + tlv(0x04, b"\x00\x64") + tlv(0x05, ip4 + bytes(7))
        р = р_из(д)
        self.assertTrue(gb.ns(р, С, С + len(д)))
        self.assertEqual(дерево(р), [(("NS", 0, 23), [
            (0, "nsip.pdu_type", 0, 1, 15), (0, "nsip.end_flag", 1, 1, 1), (0, "nsip.ie.04", 2, 4, 4),
            (0, "nsip.ie.05", 6, 17, 5), (1, "nsip.ip_element", 8, 8, "10.0.0.1:23000"),
            (2, "nsip.ip_element.signalling_weight", 14, 1, 1), (2, "nsip.ip_element.data_weight", 15, 1, 2)])])
        д = bytes([0x0C]) + tlv(0x04, b"\x00\x64") + b"\x07" + bytes([0x0B, 1, 10, 0, 0, 9])
        р = р_из(д)
        self.assertTrue(gb.ns(р, С, С + len(д)))
        self.assertEqual(дерево(р), [(("NS", 0, 12), [
            (0, "nsip.pdu_type", 0, 1, 12), (0, "nsip.transaction_id", 5, 1, 7), (0, "nsip.ie.04", 1, 4, 4),
            (0, "nsip.ie.0b", 6, 6, 11)])])

    def test_llc_виды(self):
        д = llc(1, bytes([0x20 | 0x05, 0x50 | 0x08, 0x0C | 0x03]), bytes([0x03, 1, 2, 3, 4]) + b"\x08\x21")
        р = р_из(д)
        self.assertEqual(gb.llc(р, С, С + len(д)), "LLC LLGMM I, N(S) 85, N(R) 3, SACK; GSM GMM GMM Information")
        self.assertEqual(дерево(р)[0], (("LLC", 0, 9), [
            (0, "llcgprs.sapi", 0, 1, 1), (1, "llcgprs.cr", 0, 1, 0), (0, "llcgprs.control", 1, 3, 0x25),
            (0, "llcgprs.sack", 4, 5, "4 байт"), (0, "llcgprs.fcs", 11, 3, int.from_bytes(fcs(д[:11]), "little"))]))
        д = llc(0x41, bytes([0x80 | 0x10, 0x0C | 0x03]), b"\xaa\xbb")
        р = р_из(д)
        self.assertEqual(gb.llc(р, С, С + len(д)), "LLC LLGMM S, SACK, N(R) 3")
        self.assertEqual(дерево(р), [(("LLC", 0, 5), [
            (0, "llcgprs.sapi", 0, 1, 1), (1, "llcgprs.cr", 0, 1, 1), (0, "llcgprs.control", 1, 2, 0x90),
            (0, "llcgprs.sack", 3, 2, "2 байт"), (0, "llcgprs.fcs", 5, 3, int.from_bytes(fcs(д[:5]), "little"))])])
        xid = (bytes([0x0B << 2 | 0x80 | 1, 6 << 2]) + bytes(70) + bytes([0x01 << 2 | 0x80, 4 << 2])
               + b"\x01\x02\x03\x04")
        д = llc(1, bytes([0xEB]), xid)
        р = р_из(д)
        self.assertEqual(gb.llc(р, С, С + len(д)), "LLC LLGMM U, XID: Layer-3 Parameters, IOV-UI=16909060")
        self.assertEqual(дерево(р), [(("LLC", 0, 80), [
            (0, "llcgprs.sapi", 0, 1, 1), (1, "llcgprs.cr", 0, 1, 0), (0, "llcgprs.control", 1, 1, 0xEB),
            (0, "llcgprs.fcs", 80, 3, int.from_bytes(fcs(д[:80]), "little")),
            (0, "llcgprs.xid.type", 2, 72, 11), (0, "llcgprs.xid.type", 74, 6, 1)])])
        self.assertEqual(р.п.ошибки, [])

    def test_llc_границы_форматов(self):
        for первый, вид in ((0x7F, "I"), (0x80, "S"), (0xBF, "S"), (0xC0, "UI"), (0xDF, "UI"), (0xE0, "U")):
            управление = bytes([первый, 0x00, 0x00])[:{"I": 3, "S": 2, "UI": 2, "U": 1}[вид]]
            д = llc(1, управление)
            р = р_из(д)
            self.assertTrue(gb.llc(р, С, С + len(д)).startswith(f"LLC LLGMM {вид},"), (hex(первый), вид))
            self.assertEqual(р.п.ошибки, [], hex(первый))
        # Нечётный N(R) кадра S и UI с октетом 0xCB (младший полубайт как у XID) — не XID.
        д = llc(1, bytes([0x80, 0x0D]))
        self.assertEqual(gb.llc(р_из(д), С, С + len(д)), "LLC LLGMM S, ACK, N(R) 3")
        д = llc(1, bytes([0xCB, 0x01]), b"\x08\x21")
        self.assertTrue(gb.llc(р_из(д), С, С + len(д)).endswith("GSM GMM GMM Information"))
        # Зашифрованный UI без информации — без «зашифровано».
        д = llc(1, bytes([0xC0, 0x03]))
        self.assertEqual(gb.llc(р_из(д), С, С + len(д)), "LLC LLGMM UI, N(U) 0")

    def test_xid_границы(self):
        for параметры, ожидание in ((bytes([0x01 << 2 | 0x80]), []),                       # XL без второго октета
                                    (bytes([0x05 << 2 | 0x80, 1 << 2]) + b"\x07", [("7", 3)]),
                                    (bytes([0x02 << 2 | 3]) + b"\x01\x02\x03", [("66051", 4)])):
            д = llc(1, bytes([0xEB]), параметры)
            р = р_из(д)
            gb.llc(р, С, С + len(д))
            self.assertEqual([(x.текст, x.длина) for x in р.п.уровни[0].поля if x.ключ == "llcgprs.xid.type"], ожидание)
            self.assertEqual(р.п.ошибки, [])

    def test_sndcp(self):
        д = bytes([0x40 | 5, 0x13, 9]) + b"zz"
        р = р_из(д)
        self.assertTrue(gb.sndcp(р, С, С + len(д)))
        self.assertEqual(дерево(р)[0], (("SNDCP", 0, 3), [
            (0, "sndcp.nsapi", 0, 1, 5), (1, "sndcp.f", 0, 1, 1), (1, "sndcp.t", 0, 1, 0), (1, "sndcp.m", 0, 1, 0),
            (0, "sndcp.dcomp", 1, 1, "1/3"), (0, "sndcp.npduField1", 2, 1, 9)]))
        д = bytes([0x20 | 0x10 | 5, 0x21, 0x07]) + b"zz"
        р = р_из(д)
        self.assertTrue(gb.sndcp(р, С, С + len(д)))
        self.assertEqual(дерево(р)[0], (("SNDCP", 0, 3), [
            (0, "sndcp.nsapi", 0, 1, 5), (1, "sndcp.f", 0, 1, 0), (1, "sndcp.t", 0, 1, 1), (1, "sndcp.m", 0, 1, 1),
            (0, "sndcp.segment", 1, 1, 2), (0, "sndcp.npduField2", 1, 2, 0x107)]))
        # Только заголовок, IPv6, заголовок длиннее данных.
        р = р_из(bytes([0x65, 0x00, 0, 1]))
        self.assertTrue(gb.sndcp(р, С, С + 4))
        self.assertEqual([у.протокол for у in р.п.уровни], ["SNDCP"])
        ip = с.ip6(с.udp(b"x", 1, 2, src="2001:db8::1", dst="2001:db8::2", v6=True), 17)
        р = р_из(bytes([0x45, 0x00, 9]) + ip)
        self.assertTrue(gb.sndcp(р, С, С + 3 + len(ip)))
        self.assertEqual([у.протокол for у in р.п.уровни][:2], ["SNDCP", "IPv6"])
        self.assertFalse(gb.sndcp(р_из(b"\x65\x00\x00"), С, С + 3))


class ТестГраницGb(unittest.TestCase):
    def test_имена_значений_ns(self):
        for д, имя in ((bytes([0x0F, 0x01]) + tlv(0x04, b"\x00\x64") + tlv(0x05, b""), "End Flag"),
                       (bytes([0x0C]) + tlv(0x04, b"\x00\x64") + b"\x07", "Номер транзакции")):
            р = р_из(д)
            self.assertTrue(gb.ns(р, С, С + len(д)))
            self.assertEqual(р.п.уровни[0].поля[1].имя, имя)

    def test_xid_xl_без_значения_в_конце(self):
        д = llc(1, bytes([0xEB]), bytes([0x80 | 0x0B << 2, 0]))
        р = р_из(д)
        gb.llc(р, С, С + len(д))
        self.assertEqual([(x.текст, x.смещение - С, x.длина) for x in р.п.уровни[0].поля if x.ключ == "llcgprs.xid.type"],
                         [("0", 2, 2)])

    def test_s_кадр_без_карты_sack(self):
        for управление, хвост in ((bytes([0x80 | 0x10, 0x0C | 0x03]), b""), (bytes([0x80 | 0x10, 0x0C]), b"\xaa")):
            д = llc(0x41, управление, хвост)
            р = р_из(д)
            gb.llc(р, С, С + len(д))
            self.assertNotIn("llcgprs.sack", [x.ключ for x in р.п.уровни[0].поля], д.hex())

    def test_llc_короче_заголовка(self):
        р = р_из(b"\x01\xc0\x00\x00\x00")
        gb.llc(р, С, С + 5)
        self.assertEqual(р.п.ошибки, ["LLC: кадр короче заголовка и FCS"])
        self.assertEqual((р.п.уровни[0].смещение, р.п.уровни[0].длина), (С, 5))

    def test_sndcp_продолжение_подтверждаемого(self):
        # Раньше падало: у продолжения SN-DATA нет номера N-PDU (44.065 7.2), а сводка его требовала.
        р = р_из(b"\x15" + b"part")
        self.assertTrue(gb.sndcp(р, С, С + 5))
        self.assertEqual((р.п.уровни[0].итог, р.п.уровни[1].протокол), ("NSAPI 5, SN-DATA", "Данные"))

    def test_sndcp_заголовок_ровно(self):
        """Заголовок SNDCP (44.065 7.2): адрес; первый сегмент — ещё PCOMP/DCOMP; подтверждаемый первый —
        номер N-PDU (1 октет); неподтверждаемый — номер сегмента и N-PDU (2 октета)."""
        for первый_октет, заголовок in ((0x45, 3), (0x65, 4), (0x05, 1), (0x25, 3)):
            with self.subTest(октет=hex(первый_октет)):
                д = bytes([первый_октет]) + bytes(заголовок - 1)
                р = р_из(д)
                self.assertTrue(gb.sndcp(р, С, С + заголовок))
                self.assertEqual(р.п.уровни[0].длина, заголовок)
                if заголовок > 1:
                    self.assertFalse(gb.sndcp(р_из(д), С, С + заголовок - 1))

    def test_элемент(self):
        self.assertIsNone(gb._элемент(b"\x04", 0, 1))
        self.assertIsNone(gb._элемент(b"\x04\x00", 0, 2))
        self.assertEqual(gb._элемент(b"\x04\x00\x00", 0, 3), (4, 3, 0, 0))
        self.assertEqual(gb._элемент(b"\x04\x80", 0, 2), (4, 2, 0, 0))
        self.assertEqual(gb._элемент(b"\x04\x01\x02" + bytes(258), 0, 261), (4, 3, 258, 0))
        self.assertIsNone(gb._элемент(b"\x0b", 0, 1, gb.NS_TV))
        self.assertIsNone(gb._элемент(b"\x0b\x03\x00\x00\x00\x00", 0, 6, gb.NS_TV))
        self.assertIsNone(gb._элемент(b"\x0b\x01\x0a\x00\x00", 0, 5, gb.NS_TV))
        self.assertEqual(gb._элемент(b"\x0b\x02" + bytes(16), 0, 18, gb.NS_TV), (0x0B, 1, 17, 0))
        self.assertEqual(len(gb.CRC24), 256)

    def test_ns_и_bssgp_отказы(self):
        pdu = ul(TLLI, llc_ui(1, ATTACH))
        for данные, что in ((b"", "пусто"), (b"\x00", "UNITDATA из октета"), (b"\x00\x00\x00\x02", "без BSSGP"),
                            (b"\x0c", "SNS-ACK без элементов"), (bytes([0x0C]) + tlv(0x04, b"\x00\x64"), "без транзакции"),
                            (ns_ud(pdu, биты=0x80), "запасной бит")):
            with self.subTest(что):
                р = р_из(данные)
                self.assertFalse(gb.ns(р, С, С + len(данные)))
                self.assertEqual(р.п.уровни, [])
        # Наименьший UNITDATA: BSSGP из одного октета (RADIO-STATUS без элементов).
        р = р_из(ns_ud(b"\x0a"))
        self.assertTrue(gb.ns(р, С, С + 5))
        self.assertFalse(gb.bssgp_проба(р_из(b"\x01" + bytes(6)), С, С + 7))
        self.assertTrue(gb.bssgp_проба(р_из(b"\x01" + bytes(7)), С, С + 8))
        self.assertFalse(gb.bssgp_проба(р_из(b"\x01"), С + 1, С + 1))

    def test_значения_ns_и_bssgp(self):
        # NS: элемент из двух октетов не того вида — показывается октетами.
        р = р_из(bytes([0x08]) + tlv(0x00, b"\x01") + tlv(0x02, b"\x0a\x0b"))
        self.assertTrue(gb.ns(р, С, С + 8))
        self.assertEqual([x.имя for x in р.п.уровни[0].поля][1:], ["Причина: O&M intervention", "NS PDU (2 байт)"])
        self.assertIsNone(gb._значение(0x60, bytes(6)))
        self.assertIsNone(gb._значение(0x60, bytes(4)))
        self.assertIsNone(gb._значение(0x60, bytes(2)))
        # Октетами — до 32 включительно, длиннее — числом.
        р = р_из(ns_ud(bytes([0x41]) + tlv(0x60, bytes(32)) + tlv(0x61, bytes(33)), bvci=0))
        self.assertTrue(gb.ns(р, С, С + 4 + 1 + 34 + 35))
        тексты = [x.текст for x in р.п.уровни[1].поля if x.ключ in ("bssgp.ie.60", "bssgp.ie.61")]
        self.assertEqual(тексты, ["00" * 32, "33 байт"])
        # Mobile Id и NSEI — в сводке.
        р = р_из(ns_ud(bytes([0x06]) + tlv(0x11, b"\xf4\x01\x02\x03\x04") + tlv(0x3E, b"\x00\x65"), bvci=0))
        gb.ns(р, С, С + 4 + 1 + 7 + 4)
        self.assertEqual(р.п.инфо, "BSSGP PAGING-PS, TMSI 01020304, NSEI 101")
