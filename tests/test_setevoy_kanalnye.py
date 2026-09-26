# -*- coding: utf-8 -*-
"""Канальный уровень и глобальные сети: пакеты собраны здесь по стандартам, независимо от разборщика.

Каждый протокол: поля (значения и места в байтах), фильтр по имени и по полю, обрыв пакета без
исключений и 2000 случайных нагрузок на его тип/порт — их разборщик своими не признаёт.
"""

import random
import struct
import unittest
import zlib

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import прочитать_захват, разобрать_пакет
from reportgen.setevoy.filtr import отобрать
from reportgen.setevoy.statistika import уровень_протокола

MAC_А = bytes.fromhex("001122334455")
MAC_Б = bytes.fromhex("66778899aabb")


# -- поиск в разобранном пакете -------------------------------------------------------------------

def все_поля(п):
    def обойти(поля):
        for поле in поля:
            yield поле
            yield from обойти(поле.дети)
    for у in п.уровни:
        yield from обойти(у.поля)


def поле(п, ключ):
    for п_ in все_поля(п):
        if п_.ключ == ключ:
            return п_
    raise AssertionError(f"нет поля {ключ} в {п.стек}")


def место(п, ключ):
    """(сырое значение, смещение, длина) поля."""
    п_ = поле(п, ключ)
    return п_.сырое, п_.смещение, п_.длина


def значения(п, ключ):
    return п.поля_фильтра().get(ключ, [])


# -- сборка по стандартам ----------------------------------------------------------------------------

def кадр_802_3(нагрузка_llc, dst="01:80:c2:00:00:14"):
    return bytes.fromhex(dst.replace(":", "")) + MAC_Б + struct.pack(">H", len(нагрузка_llc)) + нагрузка_llc


def eapol(версия, тип, тело):
    """IEEE 802.1X-2010, 11.3: Protocol Version, Packet Type, Packet Body Length, тело."""
    return struct.pack(">BBH", версия, тип, len(тело)) + тело


def eap(код, ид, тип=None, данные=b""):
    """RFC 3748, 4: Code, Identifier, Length, [Type, Type-Data]."""
    тело = (bytes([тип]) if тип is not None else b"") + данные
    return struct.pack(">BBH", код, ид, 4 + len(тело)) + тело


def lacp_tlv(тип, приоритет, система, ключ, пр_порта, порт, состояние):
    """IEEE 802.1AX-2014, 6.4.2.3: TLV актора/партнёра, 20 байт (3 байта резерва)."""
    return struct.pack(">BBH6sHHHB3x", тип, 20, приоритет, система, ключ, пр_порта, порт, состояние)


def lacpdu():
    return (bytes([1, 1]) + lacp_tlv(1, 32768, MAC_А, 13, 128, 5, 0x3D)
            + lacp_tlv(2, 32768, MAC_Б, 7, 128, 9, 0x3F)
            + struct.pack(">BBH12x", 3, 16, 50) + b"\0\0" + bytes(50))


def ptp_заголовок(тип, длина, номер, флаги=0x0200, домен=0, поправка=0, часы=bytes.fromhex("0011 22ff fe33 4455"),
                  порт=1, управление=0, интервал=0):
    """IEEE 1588-2008, 13.3: общий заголовок, 34 байта."""
    return struct.pack(">BBHBBHq4s8sHHBb", тип, 2, длина, домен, 0, флаги, поправка, bytes(4), часы, порт, номер,
                       управление, интервал)


def ptp_метка(сек, нс):
    """5.3.3: секунды — 48 бит, наносекунды — 32 бита."""
    return struct.pack(">HII", сек >> 32, сек & 0xFFFFFFFF, нс)


def ptp_sync(номер=77):
    return ptp_заголовок(0x0, 44, номер) + ptp_метка(0, 0)


def ptp_follow_up(номер=77):
    return ptp_заголовок(0x8, 44, номер, управление=2) + ptp_метка(1_700_000_000, 123_456_789)


def ptp_announce():
    тело = ptp_метка(0, 0) + struct.pack(">hBBBBHB8sHB", 37, 0, 128, 6, 0x21, 0x4E5D, 127,
                                         bytes.fromhex("aabbccfffe000001"), 1, 0x20)
    return ptp_заголовок(0xB, 64, 3, флаги=0x0008, управление=5, интервал=1) + тело


def ber(тег, значение):
    """BER: тег, длина (краткая или 0x81/0x82), значение."""
    n = len(значение)
    if n < 0x80:
        дл = bytes([n])
    elif n < 0x100:
        дл = bytes([0x81, n])
    else:
        дл = bytes([0x82]) + struct.pack(">H", n)
    return bytes([тег]) + дл + значение


def goose_pdu(число_данных=2, st=5, sq=12, лишний_тег=None, без=None, переставить=False, t=None):
    """IEC 61850-8-1: IECGoosePdu [APPLICATION 1] IMPLICIT SEQUENCE, поля [0]…[11]."""
    поля = [(0x80, b"IED1LD0/LLN0$GO$gcb1"), (0x81, struct.pack(">H", 2000)), (0x82, b"IED1LD0/LLN0$DS1"),
            (0x83, b"GOOSE-1"), (0x84, t or struct.pack(">I", 1_700_000_000) + b"\x80\x00\x00\x0a"),
            (0x85, bytes([st])), (0x86, bytes([sq])), (0x87, b"\x00"), (0x88, b"\x01"), (0x89, b"\x00"),
            (0x8A, bytes([число_данных])), (0xAB, ber(0x83, b"\x01") + ber(0x84, b"\x06\xc0"))]
    if без is not None:
        поля = [п for п in поля if п[0] != без]
    if переставить:                                   # stNum и sqNum местами
        поля[5], поля[6] = поля[6], поля[5]
    if лишний_тег is not None:
        поля.append((лишний_тег, b"\x00"))
    return ber(0x61, b"".join(ber(т, з) for т, з in поля))


def iec61850(appid, pdu):
    """Прил. C IEC 61850-8-1: APPID, Length (8 + APDU), Reserved1, Reserved2."""
    return struct.pack(">HHHH", appid, 8 + len(pdu), 0, 0) + pdu


def sv_pdu(asdu_число=1, объявлено=1, тег_asdu=0x30, первый=0x80):
    """IEC 61850-9-2: savPdu [APPLICATION 0] — noASDU [0], asdu [2] SEQUENCE OF ASDU (SEQUENCE)."""
    asdu = b"".join(ber(тег_asdu, ber(0x80, b"MU01") + ber(0x82, struct.pack(">H", 3999 + i))
                        + ber(0x83, struct.pack(">I", 1)) + ber(0x85, b"\x02") + ber(0x87, bytes(64)))
                    for i in range(asdu_число))
    return ber(0x60, ber(первый, bytes([объявлено])) + ber(0xA2, asdu))


def флетчер(pdu, n):
    """ISO 8473-1, прил. C.2: байты суммы на позиции n (с 1) обнулены; X и Y (0 → 255)."""
    c0 = c1 = 0
    for б in pdu:
        c0 = (c0 + б) % 255
        c1 = (c1 + c0) % 255
    x = ((len(pdu) - n) * c0 - c1) % 255
    y = (c1 - (len(pdu) - n + 1) * c0) % 255
    return bytes([x or 255, y or 255])


СИСТЕМА = bytes.fromhex("192168001001")


def isis_iih():
    """ISO/IEC 10589, 9.5: L1 LAN IIH — общий заголовок 8 байт, фиксированная часть 19 байт, TLV."""
    tlv = (bytes([1, 4, 3, 0x49, 0x00, 0x01]) + bytes([129, 1, 0xCC]) + bytes([132, 4]) + с.a4("10.0.0.1")
           + bytes([6, 6]) + MAC_А + bytes([8, 10]) + bytes(10))
    длина = 27 + len(tlv)
    return (struct.pack(">8B", 0x83, 27, 1, 0, 15, 1, 0, 0)
            + struct.pack(">B6sHHB7s", 1, СИСТЕМА, 30, длина, 64, СИСТЕМА + b"\x01") + tlv)


def isis_lsp(испортить=False):
    """9.9: L2 LSP; сумма Флетчера — от LSP ID до конца PDU."""
    tlv = bytes([137, 2]) + b"R1" + bytes([1, 4, 3, 0x49, 0x00, 0x01]) + bytes([132, 4]) + с.a4("10.0.0.1")
    длина = 27 + len(tlv)
    pdu = bytearray(struct.pack(">8B", 0x83, 27, 1, 0, 20, 1, 0, 0)
                    + struct.pack(">HH8sIHB", длина, 1199, СИСТЕМА + b"\0\0", 10, 0, 0x03) + tlv)
    pdu[24:26] = флетчер(bytes(pdu[12:]), 13)
    if испортить:
        pdu[-1] ^= 0x01
    return bytes(pdu)


def isis_csnp():
    """9.10: L1 CSNP с TLV LSP Entries (16 байт на запись)."""
    tlv = bytes([9, 16]) + struct.pack(">H8sIH", 1199, СИСТЕМА + b"\0\0", 10, 0x1234)
    return (struct.pack(">8B", 0x83, 33, 1, 0, 24, 1, 0, 0)
            + struct.pack(">H7s8s8s", 33 + len(tlv), СИСТЕМА + b"\0", bytes(8), b"\xff" * 8) + tlv)


def llc_osi(pdu):
    return кадр_802_3(b"\xfe\xfe\x03" + pdu)


def cdp_сообщение(домен=b"dom"):
    """CDP: версия 2, TTL, сумма RFC 1071 (у нечётной длины — по-сисковски), TLV: тип, длина, значение."""
    def tlv(тип, значение):
        return struct.pack(">HH", тип, 4 + len(значение)) + значение
    адреса = struct.pack(">IBBBH", 1, 1, 1, 0xCC, 4) + с.a4("10.1.1.1")
    тело = (tlv(1, b"SW1") + tlv(2, адреса) + tlv(3, b"GigabitEthernet0/1") + tlv(4, struct.pack(">I", 0x28))
            + tlv(5, b"Cisco IOS 15.2") + tlv(6, b"cisco WS-C2960") + tlv(10, struct.pack(">H", 1))
            + tlv(11, b"\x01") + tlv(9, домен))
    сообщение = bytes([2, 180, 0, 0]) + тело
    для_суммы = сообщение
    if len(сообщение) % 2:
        для_суммы = сообщение[:-1] + bytes([0xFF if сообщение[-1] & 0x80 else 0, сообщение[-1]])
    return сообщение[:2] + struct.pack(">H", с.сумма(для_суммы)) + сообщение[4:]


def cdp_кадр(домен=b"dom"):
    return кадр_802_3(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00" + cdp_сообщение(домен), dst="01:00:0c:cc:cc:cc")


def q922(dlci, cr=0, fecn=0, becn=0, de=0):
    """ITU-T Q.922, 3.3: двухбайтовое поле адреса."""
    return bytes([((dlci >> 4) << 2) | (cr << 1), ((dlci & 15) << 4) | (fecn << 3) | (becn << 2) | (de << 1) | 1])


def ax25_адрес(позывной, ssid, c=0, последний=False):
    """AX.25 v2.2, 3.12: 6 знаков, сдвинутых влево на бит, и байт SSID (C/H, RR=11, SSID, расширение)."""
    return bytes(ord(ч) << 1 for ч in позывной.ljust(6)) + bytes([(c << 7) | 0x60 | (ssid << 1) | int(последний)])


def разобрать_захват(кадры, канал):
    захват = прочитать_захват(данные=с.pcap(кадры, канал=канал))
    return [разобрать_пакет(з.данные, з.канал, номер=i + 1) for i, з in enumerate(захват.записи)]


# -- пакеты для общих проверок: (имя протокола, кадр, канал) ------------------------------------------

ИП = с.ip(с.udp(b"hello-ip-payload" * 3, 1000, 2000), 17)


def fcoe_кадр(испортить_crc=False, sof=0x2E, eof=0x42):
    fc = struct.pack(">B3sB3sB3sBBHHHI", 0x06, b"\x01\x02\x03", 0, b"\x0a\x0b\x0c", 0x08, b"\x29\x00\x00", 1, 0, 0,
                     0x1234, 0xFFFF, 0) + bytes(range(32))
    crc = zlib.crc32(fc) ^ (1 if испортить_crc else 0)
    return bytes(13) + bytes([sof]) + fc + struct.pack("<I", crc) + bytes([eof]) + bytes(3)


def fip_объявление():
    """FC-BB-5: FIP Discovery Advertisement — версия 1, дескрипторы Priority, MAC, Name, Fabric, FKA."""
    дескр = (bytes([1, 1, 0, 128]) + bytes([2, 2]) + MAC_А + bytes([4, 3, 0, 0]) + bytes.fromhex("100000051e000001")
             + bytes([5, 4]) + struct.pack(">HB3s8s", 0, 0, b"\x0e\xfc\x00", bytes.fromhex("100000051e0000ff"))
             + bytes([12, 2, 0, 0]) + struct.pack(">I", 8000))
    return struct.pack(">BBHBBHH", 0x10, 0, 1, 0, 2, len(дескр) // 4, 0x8005) + дескр


def dcp_ответ():
    """PN-DCP Identify.res (FrameID 0xFEFF): блоки NameOfStation (нечётный, с добивкой), IP, DeviceID."""
    имя = struct.pack(">BBHH", 2, 2, 7, 0) + b"plc-1" + b"\0"
    ip_ = struct.pack(">BBHH", 1, 2, 14, 1) + с.a4("192.168.0.10") + с.a4("255.255.255.0") + с.a4("192.168.0.1")
    ид = struct.pack(">BBHHHH", 2, 3, 6, 0, 0x002A, 0x0105)
    блоки = имя + ip_ + ид
    return struct.pack(">HBBIHH", 0xFEFF, 5, 1, 0x01020304, 0, len(блоки)) + блоки


def ethercat_кадр():
    """Заголовок (длина, тип 1) и две датаграммы — FPRD и LRW; поля от младшего байта."""
    д1 = struct.pack("<BBHHHH", 4, 1, 0x1001, 0x0130, 2 | 0x8000, 0) + b"\xaa\xbb" + struct.pack("<H", 1)
    д2 = struct.pack("<BBIHH", 12, 2, 0x00010000, 4, 0) + b"\x01\x02\x03\x04" + struct.pack("<H", 3)
    тело = д1 + д2
    return struct.pack("<H", len(тело) | 0x1000) + тело


ХОРОШИЕ = [
    ("EAP", с.eth(eapol(3, 0, eap(2, 7, 1, b"alice")), тип=0x888E), "Ethernet"),
    ("LACP", с.eth(lacpdu(), тип=0x8809), "Ethernet"),
    ("PTP", с.eth(ptp_sync(), тип=0x88F7), "Ethernet"),
    ("MACsec", с.eth(struct.pack(">BBI", 0x2E, 0, 5) + MAC_А + b"\x00\x01" + bytes(range(60)) + bytes(16),
                     тип=0x88E5), "Ethernet"),
    ("PBB", с.eth(struct.pack(">I", (3 << 29) | 0x123456) + MAC_А + MAC_Б + b"\x08\x00" + ИП, тип=0x88E7),
     "Ethernet"),
    ("FC", с.eth(fcoe_кадр(), тип=0x8906), "Ethernet"),
    ("FIP", с.eth(fip_объявление(), тип=0x8914), "Ethernet"),
    ("PN-DCP", с.eth(dcp_ответ() + bytes(10), тип=0x8892), "Ethernet"),
    ("EtherCAT", с.eth(ethercat_кадр() + bytes(14), тип=0x88A4), "Ethernet"),
    ("GOOSE", с.eth(iec61850(0x0001, goose_pdu()), тип=0x88B8), "Ethernet"),
    ("SV", с.eth(iec61850(0x4000, sv_pdu()), тип=0x88BA), "Ethernet"),
    ("WOL", с.eth(b"\xff" * 6 + MAC_А * 16, тип=0x0842), "Ethernet"),
    ("ISIS", llc_osi(isis_lsp()), "Ethernet"),
    ("CDP", cdp_кадр(), "Ethernet"),
    ("HSRP", с.eth(с.ip(с.udp(struct.pack(">8B8s4s", 0, 0, 16, 3, 10, 120, 1, 0, b"cisco\0\0\0",
                                          с.a4("10.0.0.254")), 1985, 1985, dst="224.0.0.2"), 17, dst="224.0.0.2")),
     "Ethernet"),
    ("FR", q922(100) + b"\x03\xcc" + ИП, "Frame Relay"),
    ("AX.25", ax25_адрес("APRS", 0, c=1) + ax25_адрес("N0CALL", 7, последний=True) + b"\x03\xf0hello", "AX.25"),
    ("X.25", b"\x00\x03\xa4" + b"\x10\x05\x62abc", "LAPB"),
]


class EapolTests(unittest.TestCase):
    def test_eap_response_identity(self):
        п = разобрать_пакет(ХОРОШИЕ[0][1])
        self.assertEqual(["Ethernet", "EAPOL", "EAP"], п.стек)
        self.assertEqual((3, 14, 1), место(п, "eapol.version"))
        self.assertEqual((0, 15, 1), место(п, "eapol.type"))
        self.assertEqual((10, 16, 2), место(п, "eapol.len"))
        self.assertEqual((2, 18, 1), место(п, "eap.code"))
        self.assertEqual((7, 19, 1), место(п, "eap.id"))
        self.assertEqual((10, 20, 2), место(п, "eap.len"))
        self.assertEqual((1, 22, 1), место(п, "eap.type"))
        self.assertEqual(("alice", 23, 5), место(п, "eap.identity"))
        self.assertIn("alice", п.инфо)

    def test_start_success_key_и_отказы(self):
        п = разобрать_пакет(с.eth(eapol(1, 1, b"") + bytes(42), тип=0x888E))    # с добивкой кадра
        self.assertEqual(["Ethernet", "EAPOL"], п.стек)
        self.assertEqual([1], значения(п, "eapol.type"))
        п = разобрать_пакет(с.eth(eapol(2, 0, eap(3, 9)) + bytes(38), тип=0x888E))
        self.assertEqual(["Ethernet", "EAPOL", "EAP"], п.стек)
        ключ = bytes([2]) + struct.pack(">HHQ", 0x008A, 16, 1) + bytes(82)
        п = разобрать_пакет(с.eth(eapol(2, 3, ключ), тип=0x888E))
        self.assertEqual(([2], [0x008A], [1]), (значения(п, "eapol.keydes.type"), значения(п, "eapol.keydes.key_info"),
                                                значения(п, "eapol.keydes.replay_counter")))
        for плохой in (eapol(4, 0, eap(3, 1)),               # версия 4 не бывает
                       eapol(2, 9, b""),                     # тип пакета 9 не определён
                       eapol(2, 0, eap(3, 1, 1)),            # Success длиннее 4 байт
                       eapol(2, 0, eap(5, 1, 1)),            # код EAP 5
                       eapol(2, 0, eap(1, 1, 1, b"x") + b"\x00"),  # длина EAP короче тела EAPOL
                       struct.pack(">BBH", 2, 1, 200)):      # тело длиннее кадра
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("EAPOL", разобрать_пакет(с.eth(плохой + bytes(40), тип=0x888E)).стек)


class SlowTests(unittest.TestCase):
    def test_lacp(self):
        п = разобрать_пакет(ХОРОШИЕ[1][1])
        self.assertEqual(["Ethernet", "LACP"], п.стек)
        self.assertEqual((1, 15, 1), место(п, "lacp.version"))
        self.assertEqual(("00:11:22:33:44:55", 20, 6), место(п, "lacp.actor.sysid"))
        self.assertEqual((13, 26, 2), место(п, "lacp.actor.key"))
        self.assertEqual((5, 30, 2), место(п, "lacp.actor.port"))
        self.assertEqual((0x3D, 32, 1), место(п, "lacp.actor.state"))
        self.assertEqual(([1], [0], [1]), (значения(п, "lacp.actor.state.activity"),
                                           значения(п, "lacp.actor.state.timeout"),
                                           значения(п, "lacp.actor.state.synchronization")))
        self.assertEqual((9, 50, 2), место(п, "lacp.partner.port"))
        self.assertEqual((50, 58, 2), место(п, "lacp.collector.max_delay"))
        # Сдвиг TLV партнёра, неверная длина TLV, версия 3, нет терминатора — не LACP.
        хороший = lacpdu()
        for i, байт in ((22, 3), (23, 19), (1, 3), (58, 1)):
            плохой = bytearray(хороший)
            плохой[i] = байт
            with self.subTest(i=i):
                self.assertEqual(["Ethernet", "Данные"], разобрать_пакет(с.eth(bytes(плохой), тип=0x8809)).стек)
        self.assertNotIn("LACP", разобрать_пакет(с.eth(хороший[:100], тип=0x8809)).стек)

    def test_marker_и_oam(self):
        marker = bytes([2, 1, 1, 16]) + struct.pack(">H6sIH", 9, MAC_А, 77, 0) + b"\0\0" + bytes(90)
        п = разобрать_пакет(с.eth(marker, тип=0x8809))
        self.assertEqual(["Ethernet", "Marker"], п.стек)
        for i, байт in ((1, 2), (2, 3), (3, 15), (18, 1)):
            плохой = bytearray(marker)
            плохой[i] = байт
            self.assertNotIn("Marker", разобрать_пакет(с.eth(bytes(плохой), тип=0x8809)).стек, i)
        self.assertEqual((77, 26, 4), место(п, "marker.requester_transaction_id"))
        локальный = struct.pack(">BBBHBBH3s4s", 1, 16, 1, 0, 0, 0x1A, 1518, b"\x00\x10\x94", bytes(4))
        oam = bytes([3]) + struct.pack(">HB", 0x0050, 0) + локальный + локальный.replace(b"\x01\x10", b"\x02\x10", 1)
        п = разобрать_пакет(с.eth(oam + bytes(8), тип=0x8809))
        self.assertEqual(["Ethernet", "OAM"], п.стек)
        self.assertEqual((0x50, 15, 2), место(п, "oampdu.flags"))
        self.assertEqual(([1], [0]), (значения(п, "oampdu.flags.local_stable"), значения(п, "oampdu.flags.dying_gasp")))
        self.assertEqual((0, 17, 1), место(п, "oampdu.code"))
        self.assertEqual([1, 2], значения(п, "oampdu.tlv.type"))
        self.assertEqual(("00:10:94", 27, 3), место(п, "oampdu.info.oui"))
        for флаги, код in ((0x0100, 0), (0x0018, 0), (0x0060, 0), (0, 5)):
            плохой = bytes([3]) + struct.pack(">HB", флаги, код) + bytes(40)
            with self.subTest(флаги=флаги, код=код):
                self.assertNotIn("OAM", разобрать_пакет(с.eth(плохой, тип=0x8809)).стек)


class PtpTests(unittest.TestCase):
    def test_sync_ethernet(self):
        п = разобрать_пакет(ХОРОШИЕ[2][1])
        self.assertEqual(["Ethernet", "PTP"], п.стек)
        self.assertEqual((0, 14, 1), место(п, "ptp.v2.messagetype"))
        self.assertEqual((2, 15, 1), место(п, "ptp.v2.versionptp"))
        self.assertEqual((44, 16, 2), место(п, "ptp.v2.messagelength"))
        self.assertEqual((1, 20, 2), (значения(п, "ptp.v2.flags.twostep")[0], 20, 2))
        self.assertEqual(("00:11:22:ff:fe:33:44:55", 34, 8), место(п, "ptp.v2.sourceportidentity.clockidentity"))
        self.assertEqual(["00:11:22:ff:fe:33:44:55"], значения(п, "ptp.v2.clockidentity"))
        self.assertEqual((77, 44, 2), место(п, "ptp.v2.sequenceid"))
        self.assertEqual((0, 48, 6), место(п, "ptp.v2.sdr.origintimestamp.seconds"))
        self.assertEqual("PTP Sync, seq 77, домен 0, 0.000000000", п.инфо)

    def test_udp_follow_up_announce_и_порты(self):
        fu = с.eth(с.ip(с.udp(ptp_follow_up(), 320, 320, dst="224.0.1.129"), 17, dst="224.0.1.129"))
        п = разобрать_пакет(fu)
        self.assertEqual(["Ethernet", "IPv4", "UDP", "PTP"], п.стек)
        self.assertEqual(["1700000000.123456789"], значения(п, "ptp.v2.fu.preciseorigintimestamp"))
        self.assertEqual((123456789, 82, 4), место(п, "ptp.v2.fu.preciseorigintimestamp.nanoseconds"))
        п = разобрать_пакет(с.eth(с.ip(с.udp(ptp_announce(), 320, 320), 17)))
        self.assertEqual((6, 90, 1), место(п, "ptp.v2.an.grandmasterclockclass"))
        self.assertEqual((127, 94, 1), место(п, "ptp.v2.an.priority2"))
        self.assertEqual(("aa:bb:cc:ff:fe:00:00:01", 95, 8), место(п, "ptp.v2.an.grandmasterclockidentity"))
        self.assertEqual([0x20], значения(п, "ptp.v2.timesource"))
        self.assertEqual([37], значения(п, "ptp.v2.an.origincurrentutcoffset"))
        # Событие (Sync) — на 319; общее сообщение на порт событий — не PTP.
        self.assertEqual("PTP", разобрать_пакет(с.eth(с.ip(с.udp(ptp_sync(), 319, 319), 17))).протокол)
        self.assertEqual("UDP", разобрать_пакет(с.eth(с.ip(с.udp(ptp_follow_up(), 319, 319), 17))).протокол)
        self.assertEqual("UDP", разобрать_пакет(с.eth(с.ip(с.udp(ptp_sync(), 320, 320), 17))).протокол)

    def test_отказы(self):
        хороший = ptp_sync()
        for плохой in (хороший[:1] + b"\x01" + хороший[2:],                         # PTPv1
                       b"\x05" + хороший[1:],                                       # тип 5 — резерв
                       хороший[:2] + b"\x00\x28" + хороший[4:],                     # длина 40 < 44
                       хороший[:2] + b"\x00\x40" + хороший[4:],                     # длина больше данных
                       хороший[:40] + struct.pack(">I", 10 ** 9)):                  # наносекунды ≥ 10^9
            with self.subTest(плохой=плохой[:4].hex()):
                self.assertNotIn("PTP", разобрать_пакет(с.eth(плохой, тип=0x88F7)).стек)


class MacsecTests(unittest.TestCase):
    def test_зашифрованный_с_sci(self):
        п = разобрать_пакет(ХОРОШИЕ[3][1])
        self.assertEqual(["Ethernet", "MACsec"], п.стек)
        self.assertEqual(([1], [1], [1], [0]), tuple(значения(п, f"macsec.tci.{б}") for б in ("sc", "e", "c", "es")))
        self.assertEqual((2, 14, 1), место(п, "macsec.an"))
        self.assertEqual((5, 16, 4), место(п, "macsec.pn"))
        self.assertEqual(("00:11:22:33:44:55/1", 20, 8), место(п, "macsec.sci"))
        self.assertEqual((60, 28, 60), место(п, "macsec.encrypted_data"))
        self.assertEqual(("00" * 16, 88, 16), место(п, "macsec.icv"))

    def test_только_целостность_и_короткий(self):
        открытый = struct.pack(">BBI", 0x20, 0, 9) + MAC_А + b"\x00\x01" + b"\x08\x00" + ИП + bytes(16)
        п = разобрать_пакет(с.eth(открытый, тип=0x88E5))
        self.assertEqual(["Ethernet", "MACsec", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual((0x0800, 28, 2), место(п, "macsec.etype"))
        # SL=10: за SecTAG 10 байт данных и ICV, кадр добит до 46 байт нагрузки.
        короткий = struct.pack(">BBI", 0x4C, 10, 1) + bytes(10) + bytes(16)
        п = разобрать_пакет(с.eth(короткий + bytes(46 - len(короткий)), тип=0x88E5))
        self.assertEqual(([10], [1]), (значения(п, "macsec.sl"), значения(п, "macsec.tci.es")))
        self.assertEqual((10, 20, 10), место(п, "macsec.encrypted_data"))
        self.assertEqual((30, 16), место(п, "macsec.icv")[1:])
        self.assertNotIn("MACsec", разобрать_пакет(с.eth(короткий + bytes(5), тип=0x88E5)).стек)

    def test_отказы(self):
        данные = bytes(80)
        for tci, sl in ((0xAE, 0), (0x6E, 0), (0x3E, 0), (0x2E, 0x40), (0x0C, 48)):
            плохой = struct.pack(">BBI", tci, sl, 1) + MAC_А + b"\x00\x01" + данные
            with self.subTest(tci=tci, sl=sl):
                self.assertNotIn("MACsec", разобрать_пакет(с.eth(плохой, тип=0x88E5)).стек)
        # SL=48 (должно быть < 48) при длине ровно под SL; SL=0, но данных меньше 48 байт.
        self.assertNotIn("MACsec", разобрать_пакет(с.eth(struct.pack(">BBI", 0x0C, 48, 1) + bytes(64),
                                                          тип=0x88E5)).стек)
        self.assertIn("MACsec", разобрать_пакет(с.eth(struct.pack(">BBI", 0x0C, 47, 1) + bytes(63),
                                                       тип=0x88E5)).стек)
        self.assertNotIn("MACsec", разобрать_пакет(с.eth(struct.pack(">BBI", 0x0C, 0, 1) + bytes(60),
                                                          тип=0x88E5)).стек)


class PbbTests(unittest.TestCase):
    def test_i_tag_и_кадр_клиента(self):
        п = разобрать_пакет(ХОРОШИЕ[4][1])
        self.assertEqual(["Ethernet", "PBB", "Ethernet", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual((0x123456, 15, 3), место(п, "ieee8021ah.isid"))
        self.assertEqual([3], значения(п, "ieee8021ah.priority"))
        self.assertEqual(["00:11:22:33:44:55"], значения(п, "ieee8021ah.c_daddr"))

    def test_отказы(self):
        for tci, sa in ((0x01000001, MAC_Б), (0x00000001, b"\x01" + MAC_Б[1:])):
            плохой = struct.pack(">I", tci) + MAC_А + sa + b"\x08\x00" + ИП
            with self.subTest(tci=tci):
                self.assertNotIn("PBB", разобрать_пакет(с.eth(плохой, тип=0x88E7)).стек)
        неизвестный = struct.pack(">I", 1) + MAC_А + MAC_Б + b"\x7f\x7f" + bytes(40)
        self.assertNotIn("PBB", разобрать_пакет(с.eth(неизвестный, тип=0x88E7)).стек)


class FcoeTests(unittest.TestCase):
    def test_fcoe_и_fc(self):
        п = разобрать_пакет(ХОРОШИЕ[5][1])
        self.assertEqual(["Ethernet", "FCoE", "FC", "Данные"], п.стек)
        self.assertEqual((0x2E, 27, 1), место(п, "fcoe.sof"))
        self.assertEqual((0x06, 28, 1), место(п, "fc.r_ctl"))
        self.assertEqual(("01.02.03", 29, 3), место(п, "fc.d_id"))
        self.assertEqual(("0a.0b.0c", 33, 3), место(п, "fc.s_id"))
        self.assertEqual((0x08, 36, 1), место(п, "fc.type"))
        self.assertEqual((0x1234, 44, 2), место(п, "fc.ox_id"))
        self.assertEqual((0x42, len(ХОРОШИЕ[5][1]) - 4, 1), место(п, "fcoe.eof"))
        self.assertFalse(поле(п, "fcoe.crc").плохо)
        self.assertEqual([], п.ошибки)
        п = разобрать_пакет(с.eth(fcoe_кадр(испортить_crc=True), тип=0x8906))
        self.assertTrue(поле(п, "fcoe.crc").плохо)
        self.assertIn("FCoE: неверная CRC кадра Fibre Channel", п.ошибки)

    def test_отказы(self):
        хороший = fcoe_кадр()
        for плохой in (b"\x10" + хороший[1:], хороший[:5] + b"\x01" + хороший[6:], fcoe_кадр(sof=0x30),
                       fcoe_кадр(eof=0x43), хороший[:-1] + b"\x01"):
            with self.subTest(плохой=плохой[:14].hex() + "…" + плохой[-4:].hex()):
                self.assertNotIn("FCoE", разобрать_пакет(с.eth(плохой, тип=0x8906)).стек)

    def test_fip(self):
        п = разобрать_пакет(ХОРОШИЕ[6][1])
        self.assertEqual(["Ethernet", "FIP"], п.стек)
        self.assertEqual((1, 16, 2), место(п, "fip.opcode"))
        self.assertEqual((2, 19, 1), место(п, "fip.subcode"))
        self.assertEqual((12, 20, 2), место(п, "fip.dl_len"))
        self.assertEqual(([1], [1], [0]), (значения(п, "fip.flags.fpma"), значения(п, "fip.flags.fport"),
                                           значения(п, "fip.flags.spma")))
        self.assertEqual([1, 2, 4, 5, 12], значения(п, "fip.desc_type"))
        self.assertEqual(("00:11:22:33:44:55", 30, 6), место(п, "fip.mac"))
        self.assertEqual([8000], значения(п, "fip.fka_adv_period"))
        хороший = fip_объявление()
        for плохой in (b"\x20" + хороший[1:], хороший[:3] + b"\x09" + хороший[4:],
                       хороший[:5] + b"\x03" + хороший[6:], хороший[:6] + b"\x00\x0d" + хороший[8:],
                       хороший[:23] + b"\x00" + хороший[24:]):             # длина дескриптора 0
            with self.subTest(плохой=плохой[:10].hex()):
                self.assertNotIn("FIP", разобрать_пакет(с.eth(плохой, тип=0x8914)).стек)


class ProfinetTests(unittest.TestCase):
    def test_dcp_identify_ответ(self):
        п = разобрать_пакет(ХОРОШИЕ[7][1])
        self.assertEqual(["Ethernet", "PN-RT", "PN-DCP"], п.стек)
        self.assertEqual((0xFEFF, 14, 2), место(п, "pn_rt.frame_id"))
        self.assertEqual((5, 16, 1), место(п, "pn_dcp.service_id"))
        self.assertEqual((1, 17, 1), место(п, "pn_dcp.service_type"))
        self.assertEqual((0x01020304, 18, 4), место(п, "pn_dcp.xid"))
        self.assertEqual((40, 24, 2), место(п, "pn_dcp.data_length"))
        self.assertEqual(("plc-1", 32, 5), место(п, "pn_dcp.suboption_device_nameofstation"))
        self.assertEqual(("192.168.0.10", 44, 4), место(п, "pn_dcp.suboption_ip_ip"))
        self.assertEqual((0x002A, 62, 2), место(п, "pn_dcp.suboption_vendor_id"))
        self.assertEqual((0x0105, 64, 2), место(п, "pn_dcp.suboption_device_id"))
        self.assertIn("plc-1", п.инфо)

    def test_dcp_запрос_и_циклический(self):
        запрос = struct.pack(">HBBIHH", 0xFEFE, 5, 0, 7, 1, 4) + struct.pack(">BBH", 0xFF, 0xFF, 0)
        п = разобрать_пакет(с.eth(запрос + bytes(32), тип=0x8892))
        self.assertEqual(["Ethernet", "PN-RT", "PN-DCP"], п.стек)
        self.assertEqual([1], значения(п, "pn_dcp.response_delay"))
        циклический = struct.pack(">H", 0x8001) + bytes(range(40)) + struct.pack(">HBB", 0x1234, 0x35, 0)
        п = разобрать_пакет(с.eth(циклический, тип=0x8892))
        self.assertEqual(["Ethernet", "PN-RT"], п.стек)
        self.assertEqual((0x1234, 56, 2), место(п, "pn_rt.cycle_counter"))
        self.assertEqual((0x35, 58, 1), место(п, "pn_rt.ds"))
        self.assertEqual(([1], [0]), (значения(п, "pn_rt.ds.datavalid"), значения(п, "pn_rt.ds.redundancy")))
        self.assertNotIn("PN-RT", разобрать_пакет(с.eth(циклический[:2] + циклический[3:], тип=0x8892)).стек)
        self.assertNotIn("PN-RT", разобрать_пакет(с.eth(b"\x01\x00" + циклический[2:], тип=0x8892)).стек)
        self.assertNotIn("PN-RT", разобрать_пакет(с.eth(b"\xfc\x01" + циклический[2:], тип=0x8892)).стек)
        for хвост in (struct.pack(">HBB", 1, 0x35, 1), struct.pack(">HBB", 1, 0x3D, 0)):
            self.assertNotIn("PN-RT", разобрать_пакет(с.eth(циклический[:-4] + хвост, тип=0x8892)).стек)

    def test_отказы_dcp(self):
        хороший = dcp_ответ()
        for плохой in (хороший[:2] + b"\x06" + хороший[3:],                        # Hello на FrameID ответа
                       хороший[:3] + b"\x02" + хороший[4:],                        # тип 2 не бывает
                       хороший[:10] + b"\x00\x29" + хороший[12:],                  # длина не по блокам
                       b"\xfe\xfd\x05" + хороший[3:]):                             # Identify на Get/Set
            with self.subTest(плохой=плохой[:12].hex()):
                self.assertNotIn("PN-DCP", разобрать_пакет(с.eth(плохой, тип=0x8892)).стек)


class EthercatTests(unittest.TestCase):
    def test_датаграммы(self):
        п = разобрать_пакет(ХОРОШИЕ[8][1])
        self.assertEqual(["Ethernet", "EtherCAT"], п.стек)
        self.assertEqual((30, 14, 2), место(п, "ecatf.length"))
        self.assertEqual([4, 12], значения(п, "ecat.cmd"))
        self.assertEqual((0x1001, 18, 2), место(п, "ecat.adp"))
        self.assertEqual((0x0130, 20, 2), место(п, "ecat.ado"))
        self.assertEqual((0x00010000, 32, 4), место(п, "ecat.lad"))
        self.assertEqual([1, 3], значения(п, "ecat.cnt"))
        self.assertEqual([1, 0], значения(п, "ecat.subframe.more"))
        self.assertIn("FPRD, LRW", п.инфо)

    def test_отказы(self):
        хороший = ethercat_кадр()
        for плохой in (struct.pack("<H", 30 | 0x2000) + хороший[2:],          # тип 2
                       struct.pack("<H", 30 | 0x1800) + хороший[2:],          # бит резерва
                       struct.pack("<H", 31 | 0x1000) + хороший[2:],          # длина не по датаграммам
                       хороший[:2] + b"\x0f" + хороший[3:],                   # команда 15
                       хороший[:8] + struct.pack("<H", 2 | 0x8800) + хороший[10:]):   # резерв в длине
            with self.subTest(плохой=плохой[:10].hex()):
                self.assertNotIn("EtherCAT", разобрать_пакет(с.eth(плохой + bytes(14), тип=0x88A4)).стек)


class Iec61850Tests(unittest.TestCase):
    def test_goose(self):
        п = разобрать_пакет(ХОРОШИЕ[9][1])
        self.assertEqual(["Ethernet", "GOOSE"], п.стек)
        self.assertEqual((1, 14, 2), место(п, "goose.appid"))
        self.assertEqual(("IED1LD0/LLN0$GO$gcb1", 24, 22), место(п, "goose.gocbref"))
        self.assertEqual(("IED1LD0/LLN0$DS1", 50, 18), место(п, "goose.datset"))
        self.assertEqual(["GOOSE-1"], значения(п, "goose.goid"))
        self.assertEqual([5], значения(п, "goose.stnum"))
        self.assertEqual([12], значения(п, "goose.sqnum"))
        self.assertEqual(["2023-11-14 22:13:20.500000 UTC, качество 0x0a"], значения(п, "goose.t"))
        self.assertEqual(["true", "c0 (неисп. бит 6)"], значения(п, "goose.data"))
        self.assertEqual([], п.ошибки)
        п = разобрать_пакет(с.eth(iec61850(1, goose_pdu(число_данных=3)), тип=0x88B8))
        self.assertIn("GOOSE: numDatSetEntries 3, а значений 2", п.ошибки)

    def test_sv(self):
        п = разобрать_пакет(ХОРОШИЕ[10][1])
        self.assertEqual(["Ethernet", "SV"], п.стек)
        self.assertEqual((0x4000, 14, 2), место(п, "sv.appid"))
        self.assertEqual([1], значения(п, "sv.noasdu"))
        self.assertEqual(("MU01", 31, 6), место(п, "sv.svid"))
        self.assertEqual((3999, 37, 4), место(п, "sv.smpcnt"))
        п = разобрать_пакет(с.eth(iec61850(0x4001, sv_pdu(2, 2)), тип=0x88BA))
        self.assertEqual([3999, 4000], значения(п, "sv.smpcnt"))

    def test_отказы(self):
        pdu = goose_pdu()
        for плохой in (iec61850(1, b"\x62" + pdu[1:]),                               # не [APPLICATION 1]
                       struct.pack(">HHHH", 1, 7 + len(pdu), 0, 0) + pdu,             # Length на байт меньше
                       iec61850(1, goose_pdu(лишний_тег=0x8D)),                       # тег [13] в IECGoosePdu нет
                       iec61850(1, ber(0x61, ber(0x82, b"x") + ber(0x80, b"y"))),    # порядок полей
                       iec61850(1, goose_pdu(переставить=True)),                     # sqNum раньше stNum
                       iec61850(1, goose_pdu(без=0x82)),                              # нет datSet
                       iec61850(1, goose_pdu(без=0xAB)),                              # нет allData
                       iec61850(1, goose_pdu(t=bytes(9))),                            # UtcTime — 8 байт
                       iec61850(1, goose_pdu() + ber(0x8C, b""))):                    # за APDU внутри Length
            with self.subTest(плохой=плохой[:12].hex()):
                self.assertNotIn("GOOSE", разобрать_пакет(с.eth(плохой, тип=0x88B8)).стек)
        self.assertNotIn("SV", разобрать_пакет(с.eth(iec61850(0x4000, sv_pdu(1, 2)), тип=0x88BA)).стек)
        self.assertNotIn("SV", разобрать_пакет(с.eth(iec61850(0x4000, sv_pdu(тег_asdu=0x31)), тип=0x88BA)).стек)
        self.assertNotIn("SV", разобрать_пакет(с.eth(iec61850(0x4000, sv_pdu(первый=0x81)), тип=0x88BA)).стек)
        self.assertNotIn("SV", разобрать_пакет(с.eth(iec61850(0x4000, goose_pdu()), тип=0x88BA)).стек)


class WolTests(unittest.TestCase):
    def test_ethertype_и_udp(self):
        п = разобрать_пакет(ХОРОШИЕ[11][1])
        self.assertEqual(["Ethernet", "WOL"], п.стек)
        self.assertEqual(("00:11:22:33:44:55", 20, 96), место(п, "wol.mac"))
        магия = b"\xff" * 6 + MAC_Б * 16 + b"\x01\x02\x03\x04"
        п = разобрать_пакет(с.eth(с.ip(с.udp(магия, 40000, 9), 17)))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "WOL"], п.стек)
        self.assertEqual(["01:02:03:04"], значения(п, "wol.passwd"))
        self.assertNotIn("WOL", разобрать_пакет(с.eth(магия[:-10] + b"\x00" * 10, тип=0x0842)).стек)
        self.assertNotIn("WOL", разобрать_пакет(с.eth(b"\xfe" * 6 + MAC_А * 16, тип=0x0842)).стек)
        self.assertNotIn("WOL", разобрать_пакет(с.eth(с.ip(с.udp(магия + b"x", 40000, 9), 17))).стек)


class IsisTests(unittest.TestCase):
    def test_lan_iih(self):
        п = разобрать_пакет(llc_osi(isis_iih()))
        self.assertEqual(["Ethernet", "LLC", "ISIS"], п.стек)
        self.assertEqual((15, 21, 1), место(п, "isis.type"))
        self.assertEqual((1, 25, 1), место(п, "isis.hello.circuit_type"))
        self.assertEqual(("1921.6800.1001", 26, 6), место(п, "isis.hello.source_id"))
        self.assertEqual((30, 32, 2), место(п, "isis.hello.holding_timer"))
        self.assertEqual((64, 36, 1), место(п, "isis.hello.priority"))
        self.assertEqual(("1921.6800.1001.01", 37, 7), место(п, "isis.hello.lan_id"))
        self.assertEqual([1, 129, 132, 6, 8], значения(п, "isis.tlv.type"))
        self.assertEqual(("49.0001", 46, 4), место(п, "isis.area_address"))
        self.assertEqual([0xCC], значения(п, "isis.nlpid"))
        self.assertEqual(["10.0.0.1"], значения(п, "isis.ipv4_interface_address"))
        self.assertEqual(["00:11:22:33:44:55"], значения(п, "isis.is_neighbor"))

    def test_lsp_с_суммой_и_csnp(self):
        п = разобрать_пакет(llc_osi(isis_lsp()))
        self.assertEqual(("1921.6800.1001.00-00", 29, 8), место(п, "isis.lsp.lsp_id"))
        self.assertEqual((10, 37, 4), место(п, "isis.lsp.sequence_number"))
        self.assertFalse(поле(п, "isis.lsp.checksum").плохо)
        self.assertEqual(["R1"], значения(п, "isis.hostname"))
        self.assertEqual([3], значения(п, "isis.lsp.is_type"))
        self.assertEqual([], п.ошибки)
        п = разобрать_пакет(llc_osi(isis_lsp(испортить=True)))
        self.assertTrue(поле(п, "isis.lsp.checksum").плохо)
        self.assertIn("IS-IS: неверная контрольная сумма LSP", п.ошибки)
        # Перестановка байт: сумма байт (C0) та же, взвешенная (C1) — нет.
        lsp = bytearray(isis_lsp())
        lsp[-10], lsp[-9] = lsp[-9], lsp[-10]
        self.assertTrue(поле(разобрать_пакет(llc_osi(bytes(lsp))), "isis.lsp.checksum").плохо)
        п = разобрать_пакет(llc_osi(isis_csnp()))
        self.assertEqual(("ffff.ffff.ffff.ff-ff", 42, 8), место(п, "isis.csnp.end_lsp_id"))
        self.assertEqual(["1921.6800.1001.00-00"], значения(п, "isis.lsp_entry.lsp_id"))

    def test_отказы(self):
        хороший = isis_iih()
        for i, байт in ((0, 0x82), (1, 26), (2, 2), (4, 0x2F), (5, 2), (6, 1), (3, 9), (8, 0), (8, 5)):
            плохой = bytearray(хороший)
            плохой[i] = байт
            with self.subTest(i=i, байт=байт):
                self.assertNotIn("ISIS", разобрать_пакет(llc_osi(bytes(плохой))).стек)
        # Длина PDU больше кадра 802.3; TLV вылезает за PDU; LSP с IS Type 2.
        self.assertNotIn("ISIS", разобрать_пакет(llc_osi(хороший[:-3])).стек)
        плохой = bytearray(хороший)
        плохой[-11] = 11
        self.assertNotIn("ISIS", разобрать_пакет(llc_osi(bytes(плохой))).стек)
        # Длина заголовка не по типу, но TLV с нового места читаются: Padding нулевой длины.
        хороший_pad = хороший[:27] + b"\x08\x00" + хороший[27:]
        хороший_pad = хороший_pad[:17] + struct.pack(">H", len(хороший_pad)) + хороший_pad[19:]
        self.assertIn("ISIS", разобрать_пакет(llc_osi(хороший_pad)).стек)
        self.assertNotIn("ISIS", разобрать_пакет(llc_osi(хороший_pad[:1] + b"\x1d" + хороший_pad[2:])).стек)
        # Длина ID системы 9 (допустимо 1–8) при согласованном заголовке P2P IIH.
        ид8 = struct.pack(">8B", 0x83, 22, 1, 8, 17, 1, 0, 0) + struct.pack(">B8sHHB", 1, bytes(8), 30, 22, 1)
        ид9 = struct.pack(">8B", 0x83, 23, 1, 9, 17, 1, 0, 0) + struct.pack(">B9sHHB", 1, bytes(9), 30, 23, 1)
        self.assertIn("ISIS", разобрать_пакет(llc_osi(ид8)).стек)
        self.assertNotIn("ISIS", разобрать_пакет(llc_osi(ид9)).стек)
        # Захват обрезан, а видимый TLV уже вылезает за длину PDU.
        кадр = llc_osi(bytes(плохой))
        self.assertNotIn("ISIS", разобрать_пакет(кадр[:len(кадр) - 3], исходная_длина=len(кадр)).стек)
        lsp = bytearray(isis_lsp())
        lsp[26] = 0x02
        self.assertNotIn("ISIS", разобрать_пакет(llc_osi(bytes(lsp))).стек)
        п = разобрать_пакет(кадр_802_3(b"\xfe\xfe\x03\x81" + bytes(40)))
        self.assertEqual(["Ethernet", "LLC", "Данные"], п.стек)
        self.assertIn("CLNP", п.уровни[-1].полное)


class CdpTests(unittest.TestCase):
    def test_поля_и_сумма(self):
        п = разобрать_пакет(cdp_кадр())
        self.assertEqual(["Ethernet", "LLC", "CDP"], п.стек)
        self.assertEqual((2, 22, 1), место(п, "cdp.version"))
        self.assertEqual((180, 23, 1), место(п, "cdp.ttl"))
        self.assertFalse(поле(п, "cdp.checksum").плохо)
        self.assertEqual(("SW1", 30, 3), место(п, "cdp.deviceid"))
        self.assertEqual(["GigabitEthernet0/1"], значения(п, "cdp.portid"))
        self.assertEqual(["cisco WS-C2960"], значения(п, "cdp.platform"))
        self.assertEqual(["10.1.1.1"], значения(п, "cdp.nrgyz.ip_address"))
        self.assertEqual(([1], [1], [0]), (значения(п, "cdp.capabilities.switch"),
                                           значения(п, "cdp.capabilities.igmp_capable"),
                                           значения(п, "cdp.capabilities.router")))
        self.assertEqual([1], значения(п, "cdp.native_vlan"))
        self.assertEqual([], п.ошибки)
        self.assertEqual(0, len(cdp_сообщение()) % 2)
        for домен in (b"do", b"d\xc1"):                  # нечётная длина; последний байт < 0x80 и ≥ 0x80
            п = разобрать_пакет(cdp_кадр(домен))
            self.assertEqual(1, len(cdp_сообщение(домен)) % 2)
            self.assertFalse(поле(п, "cdp.checksum").плохо, домен)
        испорчен = bytearray(cdp_кадр())
        испорчен[-1] ^= 0x20
        п = разобрать_пакет(bytes(испорчен))
        self.assertTrue(поле(п, "cdp.checksum").плохо)

    def test_добивка_кадра_802_3(self):
        """Короткое сообщение в кадре с добивкой до 60 байт: TLV кончаются по полю длины 802.3."""
        сообщение = bytes([1, 60]) + b"\0\0" + struct.pack(">HH", 1, 7) + b"R2\0"
        сообщение = сообщение[:2] + struct.pack(">H", с.сумма(сообщение[:-1] + b"\0\0")) + сообщение[4:]
        кадр = кадр_802_3(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00" + сообщение, dst="01:00:0c:cc:cc:cc")
        п = разобрать_пакет(кадр + bytes(60 - len(кадр)))
        self.assertEqual(["Ethernet", "LLC", "CDP"], п.стек)
        self.assertEqual(["R2\x00"], значения(п, "cdp.deviceid"))
        self.assertEqual(11, п.уровни[-1].длина)
        self.assertFalse(поле(п, "cdp.checksum").плохо)

    def test_не_cdp(self):
        сообщение = cdp_сообщение()
        # Тот же тип 0x2000 как EtherType Ethernet II, SNAP с чужим OUI, версия 3, TLV за концом.
        self.assertNotIn("CDP", разобрать_пакет(с.eth(сообщение, тип=0x2000)).стек)
        self.assertNotIn("CDP", разобрать_пакет(кадр_802_3(b"\xaa\xaa\x03\x00\x00\x00\x20\x00" + сообщение)).стек)
        self.assertNotIn("CDP", разобрать_пакет(кадр_802_3(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00\x03"
                                                           + сообщение[1:])).стек)
        self.assertNotIn("CDP", разобрать_пакет(кадр_802_3(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00"
                                                           + сообщение[:-1])).стек)


class FrameRelayTests(unittest.TestCase):
    def test_канал_nlpid_snap_isis(self):
        кадры = [q922(100, fecn=1, de=1) + b"\x03\xcc" + ИП,
                 q922(200) + b"\x03\x00\x80\x00\x00\x00\x08\x00" + ИП,
                 q922(300) + b"\x03" + isis_iih(),
                 q922(400) + b"\x03\x00\x80\x00\x00\x0c\x20\x00" + cdp_сообщение()]
        п1, п2, п3, п4 = разобрать_захват(кадры, 107)
        self.assertEqual("Frame Relay", п1.канал)
        self.assertEqual(["FR", "IPv4", "UDP", "Данные"], п1.стек)
        self.assertEqual((100, 0, 2), место(п1, "fr.dlci"))
        self.assertEqual(([1], [0], [1]), (значения(п1, "fr.fecn"), значения(п1, "fr.becn"), значения(п1, "fr.de")))
        self.assertEqual((0xCC, 3, 1), место(п1, "fr.nlpid"))
        self.assertEqual(["FR", "IPv4", "UDP", "Данные"], п2.стек)
        self.assertEqual((0x0800, 8, 2), место(п2, "fr.snap.pid"))
        self.assertEqual(["FR", "ISIS"], п3.стек)
        self.assertEqual(["FR", "CDP"], п4.стек)

    def test_lmi(self):
        q933 = q922(0) + b"\x03\x08\x00\x75" + bytes([0x51, 1, 0, 0x53, 2, 5, 4])
        статус = (q922(0) + b"\x03\x08\x00\x7d" + bytes([0x51, 1, 0, 0x53, 2, 6, 5])
                  + bytes([0x57, 3, (100 >> 4) & 0x3F, 0x80 | ((100 & 15) << 3), 0x82]))
        ansi = q922(0) + b"\x03\x08\x00\x75\x95" + bytes([0x01, 1, 1, 0x03, 2, 9, 8])
        п1, п2, п3 = разобрать_захват([q933, статус, ansi], 107)
        self.assertEqual(["FR", "LMI"], п1.стек)
        self.assertEqual((0x75, 5, 1), место(п1, "q933.message_type"))
        self.assertEqual((5, 11, 1), место(п1, "q933.link_verf.txseq"))
        self.assertEqual([4], значения(п1, "q933.link_verf.rxseq"))
        self.assertEqual((100, 15, 2), место(п2, "q933.pvc_status.dlci"))
        self.assertEqual(([1], [0]), (значения(п2, "q933.pvc_status.active"), значения(п2, "q933.pvc_status.new")))
        self.assertEqual(["FR", "LMI"], п3.стек)
        self.assertEqual([1], значения(п3, "q933.report_type"))
        self.assertEqual([9], значения(п3, "q933.link_verf.txseq"))
        for плохой in (q922(0) + b"\x03\x08\x01\x75" + bytes([0x51, 1, 0, 0x53, 2, 5, 4]),     # ссылка вызова
                       q922(0) + b"\x03\x08\x00\x76" + bytes([0x51, 1, 0, 0x53, 2, 5, 4]),     # тип сообщения
                       q922(0) + b"\x03\x08\x00\x75" + bytes([0x51, 2, 0, 0, 0x53, 2, 5, 4]),  # длина отчёта
                       q922(0) + b"\x03\x08\x00\x75" + bytes([0x53, 2, 5, 4])):                # нет отчёта
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("LMI", разобрать_захват([плохой], 107)[0].стек)

    def test_не_ui_мост_и_q933_не_на_dlci_0(self):
        мост = q922(500) + b"\x03\x00\x80\x00\x80\xc2\x00\x07" + с.eth(ИП)
        п1, п2, п3 = разобрать_захват([q922(100) + b"\x13\xcc" + ИП, мост,
                                       q922(100) + b"\x03\x08\x00\x75" + bytes([0x51, 1, 0, 0x53, 2, 5, 4])], 107)
        self.assertEqual(["FR", "Данные"], п1.стек)
        self.assertEqual(["FR", "Ethernet", "IPv4", "UDP", "Данные"], п2.стек)
        self.assertEqual(["FR", "Данные"], п3.стек)

    def test_неверный_адрес(self):
        п = разобрать_захват([b"\x19\x41\x03\xcc" + ИП], 107)[0]
        self.assertEqual(["FR", "Данные"], п.стек)
        self.assertTrue(п.ошибки)


class Ax25Tests(unittest.TestCase):
    def test_ui_и_ip_через_ретранслятор(self):
        п = разобрать_захват([ХОРОШИЕ[16][1]], 3)[0]
        self.assertEqual(["AX.25", "Данные"], п.стек)
        self.assertEqual(("APRS", 0, 7), место(п, "ax25.dst"))
        self.assertEqual(("N0CALL-7", 7, 7), место(п, "ax25.src"))
        self.assertEqual([2], значения(п, "ax25.cr"))
        self.assertEqual("команда", поле(п, "ax25.cr").текст)
        self.assertEqual((0x03, 14, 1), место(п, "ax25.ctl"))
        self.assertEqual((0xF0, 15, 1), место(п, "ax25.pid"))
        кадр = (ax25_адрес("N0CALL", 0, c=1) + ax25_адрес("K1ABC", 1) + ax25_адрес("WIDE1", 1, c=1, последний=True)
                + bytes([(3 << 5) | (2 << 1)]) + b"\xcc" + ИП)
        п = разобрать_захват([кадр], 3)[0]
        self.assertEqual(["AX.25", "IPv4", "UDP", "Данные"], п.стек)
        self.assertEqual(("WIDE1-1", 14, 7), место(п, "ax25.via"))
        self.assertIn("N(S)=2, N(R)=3", поле(п, "ax25.ctl").текст)

    def test_неверный_адрес(self):
        хороший = ХОРОШИЕ[16][1]
        for плохой in (b"aprs" + bytes(20),
                       bytes(ord(ч) << 1 for ч in "aprs  ") + хороший[6:],     # строчные буквы
                       bytes([хороший[0] | 1]) + хороший[1:],                  # бит 0 в знаке позывного
                       хороший[:6] + bytes([хороший[6] | 1]) + хороший[7:]):   # конец адресов после первого
            with self.subTest(плохой=плохой[:7].hex()):
                п = разобрать_захват([плохой], 3)[0]
                self.assertEqual(["AX.25", "Данные"], п.стек)
                self.assertTrue(п.ошибки)


class LapbX25Tests(unittest.TestCase):
    def test_данные_вызов_рестарт(self):
        вызов = b"\x01\x01\x10" + b"\x10\x01\x0b\x45\x12\x34\x56\x78\x90\x00\xcc\x00\x00\x00"
        рестарт = b"\x00\x03\x00" + b"\x10\x00\xfb\x00\x00"
        sabm = b"\x00\x03\x3f"
        п1, п2, п3, п4 = разобрать_захват([ХОРОШИЕ[17][1], вызов, рестарт, sabm], 207)
        self.assertEqual("LAPB", п1.канал)
        self.assertEqual(["LAPB", "X.25", "Данные"], п1.стек)
        self.assertEqual((0, 0, 1), место(п1, "lapb.direction"))
        self.assertEqual((3, 1, 1), место(п1, "lapb.address"))
        self.assertEqual((0xA4, 2, 1), место(п1, "lapb.control"))
        self.assertEqual((5, 3, 2), место(п1, "x25.lcn"))
        self.assertEqual(([1], [3], [8]), (значения(п1, "x25.p_s"), значения(п1, "x25.p_r"), значения(п1, "x25.mod")))
        self.assertEqual(["LAPB", "X.25"], п2.стек)
        self.assertEqual(("12345", 7, 5), место(п2, "x25.called_address"))
        self.assertEqual(["6789"], значения(п2, "x25.calling_address"))
        self.assertEqual(["cc000000"], значения(п2, "x25.call_user_data"))
        self.assertEqual([0xFB], значения(п3, "x25.type"))
        self.assertEqual(["LAPB"], п4.стек)
        self.assertIn("SABM", п4.инфо)

    def test_отказы(self):
        for плохой in (b"\x00\x03\x00" + b"\x00\x05\x62abc",        # модуль 00
                       b"\x00\x03\x00" + b"\x30\x05\x62abc",        # модуль 11 (расширение)
                       b"\x00\x03\x00" + b"\x10\x00\x62abc",        # данные на канале 0
                       b"\x00\x03\x00" + b"\x10\x05\xfb\x00\x00",   # рестарт не на канале 0
                       b"\x00\x03\x00" + b"\x10\x05\x03"):          # тип 0x03 не определён
            with self.subTest(плохой=плохой.hex()):
                п = разобрать_захват([плохой], 207)[0]
                self.assertEqual(["LAPB", "Данные"], п.стек)
        п = разобрать_захват([b"\x00\x05\x00\x10\x05\x62abc"], 207)[0]
        self.assertEqual(["LAPB", "Данные"], п.стек)
        self.assertTrue(п.ошибки)


class HsrpTests(unittest.TestCase):
    def test_поля_и_отказы(self):
        п = разобрать_пакет(ХОРОШИЕ[14][1])
        self.assertEqual(["Ethernet", "IPv4", "UDP", "HSRP"], п.стек)
        self.assertEqual((0, 42, 1), место(п, "hsrp.version"))
        self.assertEqual((16, 44, 1), место(п, "hsrp.state"))
        self.assertEqual((120, 47, 1), место(п, "hsrp.priority"))
        self.assertEqual((1, 48, 1), место(п, "hsrp.group"))
        self.assertEqual(("cisco", 50, 8), место(п, "hsrp.auth_data"))
        self.assertEqual(("10.0.0.254", 58, 4), место(п, "hsrp.virt_ip"))
        короткий = struct.pack(">8B8s3s", 0, 0, 16, 3, 10, 120, 1, 0, bytes(8), bytes(3))
        self.assertEqual("UDP", разобрать_пакет(с.eth(с.ip(с.udp(короткий, 1985, 1985), 17))).протокол)
        # 16 байт в UDP, за датаграммой — хвост кадра Ethernet: HSRP короче 20 байт не бывает.
        с_хвостом = с.eth(с.ip(с.udp(короткий[:16], 1985, 1985), 17)) + bytes(8)
        self.assertEqual("UDP", разобрать_пакет(с_хвостом).протокол)
        for версия, код, состояние in ((1, 0, 16), (0, 3, 16), (0, 0, 3)):
            плохой = struct.pack(">8B8s4s", версия, код, состояние, 3, 10, 120, 1, 0, bytes(8), bytes(4))
            with self.subTest(версия=версия, код=код, состояние=состояние):
                self.assertEqual("UDP", разобрать_пакет(с.eth(с.ip(с.udp(плохой, 1985, 1985), 17))).протокол)


class ОбщиеTests(unittest.TestCase):
    def разобрать(self, кадр, канал):
        if канал == "Ethernet":
            return разобрать_пакет(кадр)
        номер = {"Frame Relay": 107, "AX.25": 3, "LAPB": 207}[канал]
        return разобрать_захват([кадр], номер)[0]

    def test_фильтр_по_имени_и_полю(self):
        пакеты = [self.разобрать(кадр, канал) for _, кадр, канал in ХОРОШИЕ]
        поля = [п.поля_фильтра() for п in пакеты]
        for i, (имя, _, _) in enumerate(ХОРОШИЕ):
            with self.subTest(имя=имя):
                self.assertIn(имя, пакеты[i].стек)
                self.assertIn(i, отобрать(поля, имя.lower()))
        for текст, ждём in (("eapol", ["EAP"]), ("eap.identity == alice", ["EAP"]),
                            ("lacp.actor.key == 13", ["LACP"]), ("ptp.v2.sequenceid == 77", ["PTP"]),
                            ("macsec.an == 2", ["MACsec"]), ("ieee8021ah.isid == 0x123456", ["PBB"]),
                            ("fcoe and fc.type == 8", ["FC"]), ("fip.mac == 00:11:22:33:44:55", ["FIP"]),
                            ("pn_dcp.suboption_device_nameofstation contains plc", ["PN-DCP"]),
                            ("pn-rt", ["PN-DCP"]), ("ecat.cmd == 12", ["EtherCAT"]),
                            ("goose.stnum == 5", ["GOOSE"]), ("sv.smpcnt >= 3999", ["SV"]),
                            ("wol.mac == 00:11:22:33:44:55", ["WOL"]), ("isis.hostname == R1", ["ISIS"]),
                            ("cdp.deviceid == SW1", ["CDP"]), ("hsrp.virt_ip == 10.0.0.0/24", ["HSRP"]),
                            ("fr.dlci == 100", ["FR"]), ("ax25.src == N0CALL-7", ["AX.25"]),
                            ("x25.lcn == 5 and lapb", ["X.25"]), ("ax.25 or x.25", ["AX.25", "X.25"])):
            with self.subTest(текст=текст):
                self.assertEqual(ждём, [ХОРОШИЕ[i][0] for i in отобрать(поля, текст)])

    def test_уровни_дерева(self):
        for имя, уровень in (("EAPOL", "канальный"), ("LACP", "канальный"), ("FC", "канальный"),
                             ("MACsec", "канальный"), ("X.25", "канальный"), ("ISIS", "сетевой"),
                             ("PTP", "прикладной"), ("HSRP", "прикладной"), ("GOOSE", "прикладной"),
                             ("SV", "прикладной"), ("PN-DCP", "прикладной"), ("PN-RT", "канальный")):
            with self.subTest(имя=имя):
                self.assertEqual(уровень, уровень_протокола(имя))

    def test_обрыв_без_исключений(self):
        for имя, кадр, канал in ХОРОШИЕ:
            for n in range(len(кадр)):
                with self.subTest(имя=имя, n=n):
                    п = self.разобрать(кадр[:n], канал)
                    self.assertFalse([о for о in п.ошибки if "разбор прерван" in о], п.ошибки)
        # Захват обрезан (snaplen): протокол виден, уровень помечен «оборван».
        for имя, кадр, n in (("PTP", ХОРОШИЕ[2][1], 52), ("GOOSE", ХОРОШИЕ[9][1], 60),
                             ("ISIS", ХОРОШИЕ[12][1], 50), ("EAP", ХОРОШИЕ[0][1], 24), ("FC", ХОРОШИЕ[5][1], 60)):
            with self.subTest(имя=имя):
                п = разобрать_пакет(кадр[:n], исходная_длина=len(кадр))
                self.assertIn(имя, п.стек)
                self.assertIn("пакет оборван: заголовок длиннее записанных байт", п.ошибки)


class СлучайныеTests(unittest.TestCase):
    """2000 случайных нагрузок на тип/порт: своими не признаются (или долей ≤ 0,5 %, где полей для
    проверки у протокола нет: у PBB — Res2 и индивидуальный C-SA, у MACsec — зашифрованные данные,
    у циклических кадров PROFINET — только хвост APDU-статуса)."""

    N = 2000

    def счёт(self, сборщик, имя, доля=0.0, seed=1):
        с_ = random.Random(seed)
        признано = 0
        for _ in range(self.N):
            п = сборщик(с_)
            self.assertFalse([о for о in п.ошибки if "разбор прерван" in о], п.ошибки)
            признано += имя in п.стек
        self.assertLessEqual(признано, int(self.N * доля), f"{имя}: признано {признано} из {self.N}")
        return признано

    def test_ethertype(self):
        for тип, имя, доля in ((0x888E, "EAPOL", 0), (0x8809, "LACP", 0), (0x8809, "Marker", 0), (0x8809, "OAM", 0),
                               (0x88F7, "PTP", 0), (0x88E5, "MACsec", 0.005), (0x88E7, "PBB", 0.005),
                               (0x8906, "FCoE", 0), (0x8914, "FIP", 0), (0x8892, "PN-RT", 0.005),
                               (0x88A4, "EtherCAT", 0), (0x88B8, "GOOSE", 0), (0x88BA, "SV", 0),
                               (0x0842, "WOL", 0)):
            with self.subTest(имя=имя):
                self.счёт(lambda р, тип=тип: разобрать_пакет(с.eth(р.randbytes(р.randint(46, 300)), тип=тип)), имя, доля)

    def test_ethertype_с_верным_началом(self):
        """Первые байты — как у протокола (версия, подтип), остальное случайно."""
        for тип, начало, имя in ((0x888E, b"\x02\x00", "EAPOL"), (0x8809, b"\x01\x01", "LACP"),
                                 (0x8809, b"\x03", "OAM"), (0x88F7, b"\x00\x02", "PTP"),
                                 (0x8906, bytes(13) + b"\x2e", "FCoE"), (0x8914, b"\x10\x00\x00\x01", "FIP"),
                                 (0x8892, b"\xfe\xff\x05\x01", "PN-DCP"), (0x88A4, b"\x00\x10", "EtherCAT"),
                                 (0x88B8, b"\x00\x01", "GOOSE"), (0x88BA, b"\x40\x00", "SV")):
            with self.subTest(имя=имя):
                self.счёт(lambda р, тип=тип, начало=начало: разобрать_пакет(с.eth(начало + р.randbytes(р.randint(46, 300)), тип=тип)), имя)

    def test_порты_udp(self):
        for порт, имя in ((319, "PTP"), (320, "PTP"), (1985, "HSRP"), (40009, "WOL")):
            with self.subTest(порт=порт):
                self.счёт(lambda р, порт=порт: разобрать_пакет(с.eth(с.ip(с.udp(р.randbytes(р.randint(1, 200)), 50000, порт),
                                                               17))), имя)

    def test_llc_snap_и_каналы(self):
        self.счёт(lambda р: разобрать_пакет(llc_osi(b"\x83" + р.randbytes(р.randint(10, 200)))), "ISIS")
        self.счёт(lambda р: разобрать_пакет(кадр_802_3(b"\xaa\xaa\x03\x00\x00\x0c\x20\x00"
                                                       + р.randbytes(р.randint(10, 200)))), "CDP")
        кадры = [q922(0) + b"\x03\x08" + random.Random(i).randbytes(random.Random(i).randint(1, 60))
                 for i in range(self.N)]
        признано = sum("LMI" in п.стек for п in разобрать_захват(кадры, 107))
        self.assertEqual(0, признано)
        # Каналы pcap: случайный кадр — без исключений; адреса AX.25 и LAPB проверяются.
        случ = random.Random(5)
        кадры = [случ.randbytes(случ.randint(1, 80)) for _ in range(self.N)]
        for номер, имя, верх in ((107, "FR", None), (3, "AX.25", "IPv4"), (207, "LAPB", "X.25")):
            пакеты = разобрать_захват(кадры, номер)
            self.assertTrue(all(п.стек[0] == имя for п in пакеты))
            if верх:
                self.assertLessEqual(sum(верх in п.стек for п in пакеты), self.N * 0.005, имя)


if __name__ == "__main__":
    unittest.main()
