"""Маршрутизация, туннели и VPN: пакеты собраны здесь по RFC, независимо от разборщиков.

PIM, EIGRP, RSVP, DCCP, UDP-Lite (по номеру протокола IP); LDP, Geneve, LISP, CAPWAP,
OpenVPN, WireGuard, STUN/TURN, DTLS, Teredo, MPLS в UDP (по портам и по признакам).
"""

import random
import struct
import unittest
import zlib

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.filtr import отобрать
from reportgen.setevoy.statistika import уровень_протокола

СВОИ = {"PIM", "EIGRP", "RSVP", "DCCP", "UDP-Lite", "LDP", "Geneve", "LISP", "CAPWAP", "OpenVPN", "WireGuard",
        "STUN", "TURN", "DTLS", "Teredo"}


# -- сборка -------------------------------------------------------------------------------

def с_суммой(данные, место, заголовок=b""):
    """Вписать сумму RFC 1071 (по заголовку-псевдо и данным) в 2 байта на месте ``место``."""
    данные = данные[:место] + b"\0\0" + данные[место + 2:]
    return данные[:место] + struct.pack(">H", с.сумма(заголовок + данные)) + данные[место + 2:]


def псевдо4(протокол, длина, src="10.0.0.1", dst="10.0.0.2"):
    return с.a4(src) + с.a4(dst) + struct.pack(">BBH", 0, протокол, длина)


def псевдо6(протокол, длина, src="2001:db8::1", dst="2001:db8::2"):
    return с.a6(src) + с.a6(dst) + struct.pack(">IxxxB", длина, протокол)


def pim_адрес(адрес):
    return struct.pack(">BB", 1, 0) + с.a4(адрес)


def pim_группа(адрес, маска=32, флаги=0):
    return struct.pack(">BBBB", 1, 0, флаги, маска) + с.a4(адрес)


def pim_сообщение(тип, тело):
    return с_суммой(struct.pack(">BBH", 0x20 | тип, 0, 0) + тело, 2)


PIM_HELLO = pim_сообщение(0, struct.pack(">HHH", 1, 2, 105) + struct.pack(">HHI", 19, 4, 1)
                          + struct.pack(">HHI", 20, 4, 0xDEADBEEF))


def eigrp(опкод, tlv=b"", флаги=0, ack=0, as_=100):
    return с_суммой(struct.pack(">BBHIIIHH", 2, опкод, 0, флаги, 7, ack, 0, as_) + tlv, 2)


EIGRP_HELLO = eigrp(5, struct.pack(">HHBBBBBBH", 1, 12, 1, 0, 1, 0, 0, 0, 15)
                    + struct.pack(">HHBBBB", 4, 8, 12, 4, 2, 0))


def rsvp_объект(класс, ctype, тело):
    return struct.pack(">HBB", 4 + len(тело), класс, ctype) + тело


def rsvp(тип, объекты, с_суммой_=True, резерв=0, ttl=63):
    сообщение = struct.pack(">BBHBBH", 0x10, тип, 0, ttl, резерв, 8 + len(объекты)) + объекты
    return с_суммой(сообщение, 2) if с_суммой_ else сообщение


RSVP_PATH_ОБЪЕКТЫ = (rsvp_объект(1, 1, с.a4("10.0.0.2") + struct.pack(">BBH", 17, 0, 5000))
                     + rsvp_объект(3, 1, с.a4("10.0.0.1") + struct.pack(">I", 0))
                     + rsvp_объект(5, 1, struct.pack(">I", 30000))
                     + rsvp_объект(11, 1, с.a4("10.0.0.1") + struct.pack(">HH", 0, 4000)))
RSVP_PATH = rsvp(1, RSVP_PATH_ОБЪЕКТЫ)


def dccp(тип, x, seq, *, ack=None, служба=None, сброс=None, опции=b"", данные=b"", cscov=0, v6=False,
         смещение=None):
    """DCCP по RFC 4340, 5.1: при X=1 — резерв и 48-битный номер, при X=0 — 24-битный."""
    if x:
        заголовок = struct.pack(">HHBBHBB", 5001, 5002, 0, cscov, 0, (тип << 1) | 1, 0) + seq.to_bytes(6, "big")
    else:
        заголовок = struct.pack(">HHBBHB", 5001, 5002, 0, cscov, 0, тип << 1) + seq.to_bytes(3, "big")
    if ack is not None:
        заголовок += (b"\0\0" + ack.to_bytes(6, "big")) if x else (b"\0" + ack.to_bytes(3, "big"))
    if служба is not None:
        заголовок += struct.pack(">I", служба)
    if сброс is not None:
        заголовок += bytes([сброс, 0, 0, 0])
    заголовок += опции
    слов = len(заголовок) // 4 if смещение is None else смещение
    пакет = заголовок[:4] + bytes([слов]) + заголовок[5:] + данные
    покрыто = len(пакет) if cscov == 0 else слов * 4 + (cscov - 1) * 4
    псевдо = псевдо6(33, len(пакет)) if v6 else псевдо4(33, len(пакет))
    s = с.сумма(псевдо + пакет[:покрыто])
    return пакет[:6] + struct.pack(">H", s) + пакет[8:]


def udplite(нагрузка, покрытие, sport=40000, dport=53):
    дейтаграмма = struct.pack(">HHHH", sport, dport, покрытие, 0) + нагрузка
    охват = покрытие or len(дейтаграмма)
    s = с.сумма(псевдо4(136, len(дейтаграмма)) + дейтаграмма[:охват]) or 0xFFFF
    return дейтаграмма[:6] + struct.pack(">H", s) + дейтаграмма[8:]


def ldp_tlv(тип, значение):
    return struct.pack(">HH", тип, len(значение)) + значение


def ldp_сообщение(тип, ид, tlv):
    return struct.pack(">HHI", тип, 4 + len(tlv), ид) + tlv


def ldp_pdu(сообщения, lsr="1.1.1.1", пространство=0):
    return struct.pack(">HH", 1, 6 + len(сообщения)) + с.a4(lsr) + struct.pack(">H", пространство) + сообщения


LDP_HELLO = ldp_pdu(ldp_сообщение(0x0100, 1, ldp_tlv(0x0400, struct.pack(">HH", 15, 0))
                                  + ldp_tlv(0x0401, с.a4("1.1.1.1"))))

ВНУТРЕННИЙ = с.eth(с.ip(с.udp(b"inner", 1111, 2222, src="192.168.0.1", dst="192.168.0.2"), 17,
                        src="192.168.0.1", dst="192.168.0.2"), dst="02:00:00:00:00:02", src="02:00:00:00:00:01")
ВНУТРЕННИЙ_IP = с.ip(с.udp(b"inner", 1111, 2222, src="192.168.0.1", dst="192.168.0.2"), 17,
                     src="192.168.0.1", dst="192.168.0.2")


def geneve(внутри=ВНУТРЕННИЙ, тип=0x6558, опции=b"", c=None, первый=None, второй=None, vni=5001, резерв=0):
    if c is None:
        c = 1 if опции else 0
    первый = (len(опции) // 4) if первый is None else первый
    второй = (c << 6) if второй is None else второй
    return struct.pack(">BBH", первый, второй, тип) + vni.to_bytes(3, "big") + bytes([резерв]) + опции + внутри


GENEVE_ОПЦИЯ = struct.pack(">HBB", 0x0102, 0x80, 1) + b"\x01\x02\x03\x04"      # критичная, 4 байта данных


def lisp_запись(eid="10.1.1.0", маска=24, локатор="192.0.2.10"):
    return (struct.pack(">IBBBBH", 1440, 1, маска, 0x10, 0, 0) + struct.pack(">H", 1) + с.a4(eid)
            + struct.pack(">BBBBH", 1, 100, 255, 0, 0x0005) + struct.pack(">H", 1) + с.a4(локатор))


NONCE = bytes.fromhex("0102030405060708")
LISP_MAP_REQUEST = (bytes([0x10, 0, 0, 1]) + NONCE + struct.pack(">H", 1) + с.a4("10.0.0.1")
                    + struct.pack(">H", 1) + с.a4("192.0.2.1") + struct.pack(">BBH", 0, 24, 1) + с.a4("10.1.1.0"))
LISP_MAP_REPLY = bytes([0x20, 0, 0, 1]) + NONCE + lisp_запись()
LISP_MAP_REGISTER = bytes([0x30, 0, 0, 1]) + NONCE + struct.pack(">HH", 1, 20) + bytes(range(20)) + lisp_запись()


def capwap_заголовок(hlen=2, wbid=1, t=0, k=0, m=0, флаги=0, преамбула=0, rsvd=0, доп=b""):
    слово = (hlen << 19) | (0 << 14) | (wbid << 9) | (t << 8) | (m << 4) | (k << 3) | флаги
    return bytes([преамбула]) + слово.to_bytes(3, "big") + struct.pack(">HH", 0, rsvd) + доп


def capwap_элемент(тип, значение):
    return struct.pack(">HH", тип, len(значение)) + значение


CAPWAP_ЭЛЕМЕНТЫ = capwap_элемент(20, b"\x01") + capwap_элемент(45, b"ap-1")
CAPWAP_DISCOVERY = (capwap_заголовок() + struct.pack(">IBHB", 1, 5, 3 + len(CAPWAP_ЭЛЕМЕНТЫ), 0)
                    + CAPWAP_ЭЛЕМЕНТЫ)


def tls_client_hello(версия_записи=0x0301):
    hello = struct.pack(">H", 0x0303) + bytes(32) + b"\x00" + struct.pack(">H", 2) + b"\x13\x01" + b"\x01\x00"
    рукопожатие = b"\x01" + len(hello).to_bytes(3, "big") + hello
    return bytes([22]) + struct.pack(">HH", версия_записи, len(рукопожатие)) + рукопожатие


SID = bytes.fromhex("1122334455667788")
SID2 = bytes.fromhex("99aabbccddeeff00")
OVPN_RESET = bytes([7 << 3]) + SID + b"\x00" + struct.pack(">I", 0)
OVPN_CONTROL = bytes([4 << 3]) + SID + b"\x01" + struct.pack(">I", 0) + SID2 + struct.pack(">I", 1) + tls_client_hello()
OVPN_ACK = bytes([5 << 3]) + SID + b"\x01" + struct.pack(">I", 3) + SID2
OVPN_TLS_AUTH = bytes([8 << 3 | 1]) + SID + bytes(range(20)) + struct.pack(">II", 1, 1700000000) + b"\x01" \
    + struct.pack(">I", 0) + SID2 + struct.pack(">I", 0)


def wg(тип, длина):
    return bytes([тип, 0, 0, 0]) + struct.pack("<I", 0x01020304) + bytes((i * 7) & 0xFF for i in range(длина - 8))


МАГИЯ = 0x2112A442
ТРАНЗАКЦИЯ = bytes.fromhex("a1a2a3a4a5a6a7a8a9aaabac")


def stun_атрибут(тип, значение):
    return struct.pack(">HH", тип, len(значение)) + значение + b"\0" * (-len(значение) % 4)


def stun(тип, атрибуты=b"", отпечаток=True, магия=МАГИЯ, испортить_отпечаток=False):
    длина = len(атрибуты) + (8 if отпечаток else 0)
    сообщение = struct.pack(">HHI", тип, длина, магия) + ТРАНЗАКЦИЯ + атрибуты
    if отпечаток:
        crc = (zlib.crc32(сообщение) ^ 0x5354554E ^ (1 if испортить_отпечаток else 0)) & 0xFFFFFFFF
        сообщение += struct.pack(">HHI", 0x8028, 4, crc)
    return сообщение


def xor_адрес4(адрес, порт):
    x = bytes(a ^ b for a, b in zip(с.a4(адрес), struct.pack(">I", МАГИЯ), strict=False))
    return struct.pack(">BBH", 0, 1, порт ^ 0x2112) + x


def xor_адрес6(адрес, порт):
    ключ = struct.pack(">I", МАГИЯ) + ТРАНЗАКЦИЯ
    x = bytes(a ^ b for a, b in zip(с.a6(адрес), ключ, strict=False))
    return struct.pack(">BBH", 0, 2, порт ^ 0x2112) + x


def отпечаток_не_последний():
    """FINGERPRINT с верной суммой, но за ним ещё атрибут (RFC 8489, 14.7: он последний)."""
    заголовок = struct.pack(">HHI", 0x0001, 16, МАГИЯ) + ТРАНЗАКЦИЯ
    crc = (zlib.crc32(заголовок) ^ 0x5354554E) & 0xFFFFFFFF
    return заголовок + struct.pack(">HHI", 0x8028, 4, crc) + stun_атрибут(0x8022, b"test")


STUN_ЗАПРОС = stun(0x0001, stun_атрибут(0x8022, b"test"))
STUN_ОТВЕТ = stun(0x0101, stun_атрибут(0x0020, xor_адрес4("198.51.100.7", 32853))
                  + stun_атрибут(0x0020, xor_адрес6("2001:db8::7", 4000)))


def dtls_запись(тип, тело, эпоха=0, номер=0, версия=0xFEFF):
    return struct.pack(">BHH", тип, версия, эпоха) + номер.to_bytes(6, "big") + struct.pack(">H", len(тело)) + тело


def dtls_hello():
    тело = struct.pack(">H", 0xFEFD) + bytes(range(32)) + b"\x00" + b"\x00" + struct.pack(">H", 2) + b"\xc0\x2b" \
        + b"\x01\x00"
    return struct.pack(">B", 1) + len(тело).to_bytes(3, "big") + struct.pack(">H", 0) + (0).to_bytes(3, "big") \
        + len(тело).to_bytes(3, "big") + тело


DTLS_HELLO = dtls_запись(22, dtls_hello())

ПУЗЫРЬ = с.ip6(b"", 59, src="2001:0:4136:e378:8000:63bf:3fff:fdd2", dst="fe80::8000:ffff:ffff:fffd")
TEREDO_ИСТОЧНИК = struct.pack(">HH", 0, 40000 ^ 0xFFFF) + bytes(б ^ 0xFF for б in с.a4("203.0.113.9"))


def mpls_метка(метка, дно=1, ttl=64):
    return struct.pack(">I", (метка << 12) | (дно << 8) | ttl)


def udp_пакет(нагрузка, sport, dport):
    return с.eth(с.ip(с.udp(нагрузка, sport, dport), 17))


def tcp_пакет(нагрузка, sport, dport):
    return с.eth(с.ip(с.tcp(нагрузка, sport, dport), 6))


# -- проверка -----------------------------------------------------------------------------

def все_поля(п):
    итог = []

    def обойти(поля):
        for ф in поля:
            итог.append(ф)
            обойти(ф.дети)

    for у in п.уровни:
        обойти(у.поля)
    return итог


def поле(п, ключ):
    for ф in все_поля(п):
        if ф.ключ == ключ:
            return ф
    raise AssertionError(f"нет поля {ключ}")


def значения(п, ключ):
    return п.поля_фильтра().get(ключ, [])


class Проверки(unittest.TestCase):
    def место(self, п, ключ, смещение, длина, значение=None):
        ф = поле(п, ключ)
        self.assertEqual((смещение, длина), (ф.смещение, ф.длина), ключ)
        if значение is not None:
            self.assertEqual(значение, ф.сырое, ключ)

    def свой(self, п, стек):
        self.assertEqual(стек, п.стек)


# -- по номеру протокола IP ----------------------------------------------------------------

class PimTests(Проверки):
    def test_hello_поля_и_дерево(self):
        п = разобрать_пакет(с.eth(с.ip(PIM_HELLO, 103, dst="224.0.0.13")))
        self.свой(п, ["Ethernet", "IPv4", "PIM"])
        self.assertEqual([], п.ошибки)
        self.место(п, "pim.version", 34, 1, 2)
        self.место(п, "pim.type", 34, 1, 0)
        self.место(п, "pim.holdtime", 42, 2, 105)
        self.место(п, "pim.dr_priority", 48, 4, 1)
        self.место(п, "pim.generation_id", 56, 4, 0xDEADBEEF)
        опция = [ф for ф in п.уровни[-1].поля if ф.ключ == "pim.option"][0]
        self.assertEqual(["pim.option_type", "pim.option_length", "pim.holdtime"], [д.ключ for д in опция.дети])
        self.assertIn("верна", поле(п, "pim.checksum").текст)
        self.assertEqual("PIM Hello, удержание 105 с, приоритет DR 1", п.инфо)
        self.assertEqual("сетевой", уровень_протокола("PIM"))

    def test_join_prune_группы_и_источники(self):
        тело = pim_адрес("10.0.0.254") + struct.pack(">BBH", 0, 1, 210) + pim_группа("239.1.1.1") \
            + struct.pack(">HH", 1, 1) + pim_группа("10.0.0.5", флаги=0x07) + pim_группа("10.0.0.6", флаги=0x04)
        п = разобрать_пакет(с.eth(с.ip(pim_сообщение(3, тело), 103)))
        self.assertEqual(["10.0.0.254"], значения(п, "pim.upstream_neighbor"))
        self.assertEqual(["239.1.1.1"], значения(п, "pim.group"))
        self.assertEqual(["10.0.0.5"], значения(п, "pim.join_src"))
        self.assertEqual(["10.0.0.6"], значения(п, "pim.prune_src"))
        self.assertEqual([1, 0], значения(п, "pim.src_flags.w"))
        self.место(п, "pim.numgroups", 34 + 4 + 6 + 1, 1, 1)
        группа = поле(п, "pim.group")
        self.assertEqual((34 + 14, 8 + 4 + 16), (группа.смещение, группа.длина))
        self.assertIn("присоединить 1, отсечь 1", п.инфо)
        # Лишний байт после групп — сумма сходится, но структура нет: ошибка видна.
        п = разобрать_пакет(с.eth(с.ip(pim_сообщение(3, тело + b"\0\0"), 103)))
        self.assertIn("PIM", п.стек)
        self.assertTrue(any("Join/Prune не сходятся" in о for о in п.ошибки))

    def test_register_сумма_по_заголовку_и_вложенный_ip(self):
        внутри = с.ip(с.udp(b"mcast", 5000, 5001, src="10.1.1.1", dst="239.1.1.1"), 17, src="10.1.1.1",
                      dst="239.1.1.1")
        заголовок = с_суммой(struct.pack(">BBHI", 0x21, 0, 0, 0), 2)            # сумма только по 8 байтам
        п = разобрать_пакет(с.eth(с.ip(заголовок + внутри, 103)))
        self.свой(п, ["Ethernet", "IPv4", "PIM", "IPv4", "UDP", "Данные"])
        self.assertIn("по заголовку Register", поле(п, "pim.checksum").текст)
        self.assertEqual([0], значения(п, "pim.register_flags.null"))
        # И сумма по всему сообщению (RFC 7761, 4.9.3 — принимать и её).
        целиком = pim_сообщение(1, struct.pack(">I", 0x40000000) + внутри)
        п = разобрать_пакет(с.eth(с.ip(целиком, 103)))
        self.assertEqual([1], значения(п, "pim.register_flags.null"))
        self.assertIn("Null-Register", п.уровни[2].итог)

    def test_ipv6_псевдозаголовок(self):
        сообщение = struct.pack(">BBH", 0x20, 0, 0) + struct.pack(">HHH", 1, 2, 105)
        s = с.сумма(псевдо6(103, len(сообщение)) + сообщение)
        сообщение = сообщение[:2] + struct.pack(">H", s) + сообщение[4:]
        п = разобрать_пакет(с.eth(с.ip6(сообщение, 103), тип=0x86DD))
        self.свой(п, ["Ethernet", "IPv6", "PIM"])
        # Та же сумма без псевдозаголовка — для IPv6 неверна.
        без = pim_сообщение(0, struct.pack(">HHH", 1, 2, 105))
        self.assertNotIn("PIM", разобрать_пакет(с.eth(с.ip6(без, 103), тип=0x86DD)).стек)

    def test_assert_bootstrap_register_stop(self):
        тело = pim_группа("239.2.2.2") + pim_адрес("10.0.0.9") + struct.pack(">II", 0x80000000 | 110, 20)
        п = разобрать_пакет(с.eth(с.ip(pim_сообщение(5, тело), 103)))
        self.assertEqual(([1], [110], [20]), (значения(п, "pim.rpt"), значения(п, "pim.metric_pref"),
                                              значения(п, "pim.metric")))
        bsr = struct.pack(">HBB", 7, 30, 64) + pim_адрес("10.0.0.1") + pim_группа("224.0.0.0", 4) \
            + struct.pack(">BBH", 1, 1, 0) + pim_адрес("10.0.0.2") + struct.pack(">HBB", 150, 192, 0)
        п = разобрать_пакет(с.eth(с.ip(pim_сообщение(4, bsr), 103)))
        self.assertEqual((["10.0.0.1"], ["10.0.0.2"], [150]), (значения(п, "pim.bsr_address"),
                                                               значения(п, "pim.rp_address"),
                                                               значения(п, "pim.rp_holdtime")))
        self.assertEqual("PIM Bootstrap, BSR 10.0.0.1, групп 1", п.инфо)
        стоп = pim_группа("239.3.3.3") + pim_адрес("10.9.9.9")
        п = разобрать_пакет(с.eth(с.ip(pim_сообщение(2, стоп), 103)))
        self.assertEqual("PIM Register-Stop, группа 239.3.3.3, источник 10.9.9.9", п.инфо)

    def test_не_pim(self):
        плохая = bytearray(PIM_HELLO)
        плохая[-1] ^= 1
        п = разобрать_пакет(с.eth(с.ip(bytes(плохая), 103)))
        self.assertNotIn("PIM", п.стек)
        self.assertTrue(any("PIM: контрольная сумма" in о for о in п.ошибки))
        версия1 = с_суммой(b"\x10" + PIM_HELLO[1:], 2)
        self.assertNotIn("PIM", разобрать_пакет(с.eth(с.ip(версия1, 103))).стек)
        # Сумма только по первым 8 байтам допустима лишь у Register (RFC 7761, 4.9.3).
        hello8 = с_суммой(PIM_HELLO[:8], 2) + PIM_HELLO[8:]
        self.assertNotIn("PIM", разобрать_пакет(с.eth(с.ip(hello8, 103))).стек)
        # Неизвестное семейство адреса при верной сумме — PIM, но с ошибкой разбора тела.
        странный = pim_сообщение(2, struct.pack(">BBBB", 9, 0, 0, 32) + bytes(8))
        п = разобрать_пакет(с.eth(с.ip(странный, 103)))
        self.assertIn("PIM", п.стек)
        self.assertTrue(any("семейство 9" in о for о in п.ошибки))


class EigrpTests(Проверки):
    def test_hello_параметры(self):
        п = разобрать_пакет(с.eth(с.ip(EIGRP_HELLO, 88, dst="224.0.0.10")))
        self.свой(п, ["Ethernet", "IPv4", "EIGRP"])
        self.место(п, "eigrp.version", 34, 1, 2)
        self.место(п, "eigrp.opcode", 35, 1, 5)
        self.место(п, "eigrp.as", 52, 2, 100)
        self.место(п, "eigrp.par.k1", 58, 1, 1)
        self.место(п, "eigrp.par.holdtime", 64, 2, 15)
        self.место(п, "eigrp.sw.os", 70, 2)
        self.assertEqual("12.4", поле(п, "eigrp.sw.os").текст)
        tlv = [ф for ф in п.уровни[-1].поля if ф.ключ == "eigrp.tlv_type"]
        self.assertEqual([1, 4], [ф.сырое for ф in tlv])
        self.assertIn("eigrp.par.k1", [д.ключ for д in tlv[0].дети])
        self.assertEqual("EIGRP Hello, AS 100", п.инфо)
        self.assertEqual("88 (EIGRP)", поле(п, "ip.proto").текст)

    def test_update_маршрут_и_флаги(self):
        маршрут = struct.pack(">HH", 0x0102, 28) + с.a4("0.0.0.0") + struct.pack(">II", 2560, 256000) \
            + (1500).to_bytes(3, "big") + bytes([0, 255, 1, 0, 0]) + b"\x18" + bytes([192, 168, 1])
        п = разобрать_пакет(с.eth(с.ip(eigrp(1, маршрут, флаги=0x9), 88)))
        self.assertEqual(["192.168.1.0"], значения(п, "eigrp.ipv4.destination"))
        self.assertEqual([24], значения(п, "eigrp.ipv4.prefixlen"))
        self.assertEqual([1500], значения(п, "eigrp.old_metric.mtu"))
        self.assertEqual(([1], [1], [0]), (значения(п, "eigrp.flags.init"), значения(п, "eigrp.flags.eot"),
                                           значения(п, "eigrp.flags.restart")))
        self.место(п, "eigrp.ipv4.destination", 34 + 20 + 24, 4, "192.168.1.0")
        self.assertIn("маршруты 192.168.1.0/24", п.инфо)

    def test_не_eigrp(self):
        плохая = bytearray(EIGRP_HELLO)
        плохая[10] ^= 1
        self.assertNotIn("EIGRP", разобрать_пакет(с.eth(с.ip(bytes(плохая), 88))).стек)
        версия1 = с_суммой(b"\x01" + EIGRP_HELLO[1:], 2)
        self.assertNotIn("EIGRP", разобрать_пакет(с.eth(с.ip(версия1, 88))).стек)
        опкод = с_суммой(EIGRP_HELLO[:1] + b"\x06" + EIGRP_HELLO[2:], 2)
        self.assertNotIn("EIGRP", разобрать_пакет(с.eth(с.ip(опкод, 88))).стек)


class RsvpTests(Проверки):
    def test_path_объекты(self):
        п = разобрать_пакет(с.eth(с.ip(RSVP_PATH, 46)))
        self.свой(п, ["Ethernet", "IPv4", "RSVP"])
        self.место(п, "rsvp.version", 34, 1, 1)
        self.место(п, "rsvp.msg", 35, 1, 1)
        self.место(п, "rsvp.length", 40, 2, len(RSVP_PATH))
        self.место(п, "rsvp.session.ip", 46, 4, "10.0.0.2")
        self.место(п, "rsvp.session.port", 52, 2, 5000)
        self.место(п, "rsvp.hop.ip", 58, 4, "10.0.0.1")
        self.место(п, "rsvp.time.refresh", 70, 4, 30000)
        self.assertEqual([1, 3, 5, 11], значения(п, "rsvp.object"))
        self.assertEqual([4000], значения(п, "rsvp.sender.port"))
        self.assertEqual("RSVP Path, сессия 10.0.0.2, порт 5000, протокол 17", п.инфо)
        self.assertIn("верна", поле(п, "rsvp.checksum").текст)

    def test_resv_стиль_и_нулевая_сумма(self):
        объекты = RSVP_PATH_ОБЪЕКТЫ[:24] + rsvp_объект(8, 1, struct.pack(">I", 0x12))
        п = разобрать_пакет(с.eth(с.ip(rsvp(2, объекты, с_суммой_=False), 46)))
        self.assertIn("RSVP", п.стек)
        self.assertIn("не передаётся", поле(п, "rsvp.checksum").текст)
        self.assertEqual(["SE (Shared Explicit)"], [ф.текст for ф in все_поля(п) if ф.ключ == "rsvp.style"])

    def test_не_rsvp(self):
        for плохое in (rsvp(1, RSVP_PATH_ОБЪЕКТЫ, резерв=1),                       # резерв не нуль
                       rsvp(1, RSVP_PATH_ОБЪЕКТЫ[:-12] + rsvp_объект(11, 1, bytes(6))),   # длина объекта не кратна 4
                       RSVP_PATH[:-1] + bytes([RSVP_PATH[-1] ^ 1]),                   # сумма
                       RSVP_PATH + b"\0\0\0\0"):                                     # длина сообщения
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("RSVP", разобрать_пакет(с.eth(с.ip(плохое, 46))).стек)
        без_суммы = rsvp(1, RSVP_PATH_ОБЪЕКТЫ, с_суммой_=False)
        self.assertNotIn("RSVP", разобрать_пакет(с.eth(с.ip(без_суммы[:6] + b"\0\x0c" + без_суммы[8:], 46))).стек)


class DccpTests(Проверки):
    def test_request_x1(self):
        пакет = dccp(0, 1, 0x010203040506, служба=42)
        п = разобрать_пакет(с.eth(с.ip(пакет, 33)))
        self.свой(п, ["Ethernet", "IPv4", "DCCP"])
        self.assertEqual([], п.ошибки)
        self.место(п, "dccp.srcport", 34, 2, 5001)
        self.место(п, "dccp.data_offset", 38, 1, 5)
        self.место(п, "dccp.type", 42, 1, 0)
        self.место(п, "dccp.seq", 44, 6, 0x010203040506)
        self.место(п, "dccp.service_code", 50, 4, 42)
        self.assertEqual([5001, 5002], значения(п, "dccp.port"))
        self.assertEqual("DCCP 5001 → 5002 [Request] seq=1108152157446", п.инфо)
        self.assertEqual("транспортный", уровень_протокола("DCCP"))

    def test_dataack_x0_cscov_и_опции(self):
        опции = struct.pack(">BBI", 41, 6, 12345) + b"\x00\x00"                   # Timestamp и две Padding
        пакет = dccp(4, 0, 77, ack=70, опции=опции, данные=b"payload-bytes", cscov=1)
        п = разобрать_пакет(с.eth(с.ip(пакет, 33)))
        self.assertEqual(["Ethernet", "IPv4", "DCCP", "Данные"], п.стек)
        self.место(п, "dccp.seq", 43, 3, 77)
        self.место(п, "dccp.ack", 47, 3, 70)
        self.assertEqual([41, 0, 0], значения(п, "dccp.option_type"))
        self.assertIn("покрыто 24 байт", поле(п, "dccp.checksum").текст)
        # CsCov=1: данные не покрыты суммой — их порча не мешает.
        испорчен = bytearray(с.eth(с.ip(пакет, 33)))
        испорчен[-1] ^= 0xFF
        self.assertIn("DCCP", разобрать_пакет(bytes(испорчен)).стек)
        испорчен[34 + 20] ^= 0xFF                                                  # а заголовок — покрыт
        self.assertNotIn("DCCP", разобрать_пакет(bytes(испорчен)).стек)

    def test_ipv6_и_reset(self):
        п = разобрать_пакет(с.eth(с.ip6(dccp(7, 1, 9, ack=8, сброс=3, v6=True), 33), тип=0x86DD))
        self.свой(п, ["Ethernet", "IPv6", "DCCP"])
        self.assertIn("No Connection", поле(п, "dccp.reset_code").текст)
        self.assertEqual([8], значения(п, "dccp.ack"))

    def test_не_dccp(self):
        for плохое in (dccp(0, 0, 5, служба=1),                            # Request с коротким номером (X=0)
                       dccp(10, 1, 5, ack=1),                                # зарезервированный тип
                       dccp(7, 1, 9, ack=8, сброс=3, смещение=6),            # код сброса за смещением данных
                       dccp(2, 1, 5, данные=b"abcd", смещение=3),            # смещение меньше заголовка
                       dccp(2, 1, 5, данные=b"abcd", смещение=20),           # смещение за пакетом
                       dccp(2, 1, 5, данные=b"abcd", cscov=3),               # CsCov покрывает больше данных
                       dccp(0, 1, 5, служба=1)[:-1] + b"\x00"):              # сумма
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("DCCP", разобрать_пакет(с.eth(с.ip(плохое, 33))).стек)
        self.assertIn("DCCP", разобрать_пакет(с.eth(с.ip(dccp(2, 1, 5, данные=b"abcd", cscov=2), 33))).стек)


class UdpLiteTests(Проверки):
    def test_покрытие_8_и_dns_внутри(self):
        пакет = udplite(с.dns_запрос("lite.example"), 8)
        п = разобрать_пакет(с.eth(с.ip(пакет, 136)))
        self.свой(п, ["Ethernet", "IPv4", "UDP-Lite", "DNS"])
        self.место(п, "udplite.checksum_coverage", 38, 2, 8)
        self.место(п, "udplite.dstport", 36, 2, 53)
        испорчен = bytearray(с.eth(с.ip(пакет, 136)))
        испорчен[-1] ^= 0x55                                               # не покрыто суммой
        self.assertIn("UDP-Lite", разобрать_пакет(bytes(испорчен)).стек)
        п = разобрать_пакет(с.eth(с.ip(udplite(b"hello", 0, dport=7000), 136)))
        self.assertEqual(["Ethernet", "IPv4", "UDP-Lite", "Данные"], п.стек)
        self.assertIn("вся дейтаграмма", поле(п, "udplite.checksum_coverage").текст)
        self.assertEqual("транспортный", уровень_протокола("UDP-Lite"))

    def test_не_udplite(self):
        for плохое in (udplite(b"hello", 5), udplite(b"hello", 14), udplite(b"hello", 0)[:-1] + b"X"):
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("UDP-Lite", разобрать_пакет(с.eth(с.ip(плохое, 136))).стек)
        # Подобранный порт источника: сумма «сходится» и при покрытии 6, и при нулевом поле
        # суммы — такие дейтаграммы стандарт всё равно велит отбросить.
        for покрытие, сумма_поля in ((6, 0x1234), (8, 0)):
            with self.subTest(покрытие=покрытие, сумма_поля=сумма_поля):
                хвост = struct.pack(">HHH", 53, покрытие, сумма_поля) + b"x"
                длина = 2 + len(хвост)
                охват = (b"\0\0" + хвост)[:покрытие or длина]
                s = с.сумма(псевдо4(136, длина) + охват)                # дополнение до нуля
                порт = s                                                    # ~(S + порт) == 0
                дейтаграмма = struct.pack(">H", порт) + хвост
                self.assertEqual(0, с.сумма(псевдо4(136, длина) + дейтаграмма[:покрытие or длина]))
                self.assertNotIn("UDP-Lite", разобрать_пакет(с.eth(с.ip(дейтаграмма, 136))).стек)


# -- по портам ------------------------------------------------------------------------------

class LdpTests(Проверки):
    def test_hello_udp(self):
        п = разобрать_пакет(udp_пакет(LDP_HELLO, 646, 646))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "LDP"])
        self.место(п, "ldp.hdr.version", 42, 2, 1)
        self.место(п, "ldp.hdr.pdu_len", 44, 2, len(LDP_HELLO) - 4)
        self.место(п, "ldp.hdr.ldp_id", 46, 4, "1.1.1.1")
        self.место(п, "ldp.msg.type", 52, len(LDP_HELLO) - 10, 0x0100)
        self.место(п, "ldp.msg.tlv.hello.hold", 64, 2, 15)
        self.место(п, "ldp.msg.tlv.ipv4.taddr", 72, 4, "1.1.1.1")
        pdu = п.уровни[-1].поля[0]
        self.assertEqual("ldp.msg.type", pdu.дети[-1].ключ)
        self.assertEqual("LDP Hello (LDP ID 1.1.1.1:0)", п.инфо)

    def test_tcp_два_pdu_и_метка(self):
        сессия = ldp_tlv(0x0500, struct.pack(">HHBBH", 1, 180, 0, 0, 4096) + с.a4("2.2.2.2") + b"\0\0")
        первый = ldp_pdu(ldp_сообщение(0x0200, 10, сессия) + ldp_сообщение(0x0201, 11, b""))
        fec = ldp_tlv(0x0100, b"\x02\x00\x01\x10\x0a\x01")
        второй = ldp_pdu(ldp_сообщение(0x0400, 12, fec + ldp_tlv(0x0200, struct.pack(">I", 100))))
        п = разобрать_пакет(tcp_пакет(первый + второй, 40000, 646))
        self.свой(п, ["Ethernet", "IPv4", "TCP", "LDP"])
        self.assertEqual([180], значения(п, "ldp.msg.tlv.sess.ka"))
        self.assertEqual(["2.2.2.2:0"], значения(п, "ldp.msg.tlv.sess.rxlsr"))
        self.assertEqual([100], значения(п, "ldp.msg.tlv.generic.label"))
        self.assertEqual(["10.1.0.0/16"], значения(п, "ldp.msg.tlv.fec.pfval"))
        self.assertEqual([0x0200, 0x0201, 0x0400], значения(п, "ldp.msg.type"))
        self.assertIn("Initialization, KeepAlive, Label Mapping", п.инфо)

    def test_не_ldp(self):
        версия2 = b"\x00\x02" + LDP_HELLO[2:]
        короткое = LDP_HELLO[:10] + struct.pack(">HH", 0x0100, 3) + LDP_HELLO[14:]
        лишний_tlv = LDP_HELLO[:12] + struct.pack(">H", u16(LDP_HELLO, 12) - 1) + LDP_HELLO[14:]
        пустое = ldp_pdu(struct.pack(">HH", 0x0201, 0) + ldp_сообщение(0x0201, 5, b""))   # сообщение без ID
        tlv_за_концом = ldp_pdu(ldp_сообщение(0x0100, 1, struct.pack(">HH", 0x0400, 6) + struct.pack(">HH", 15, 0)))
        for плохое in (версия2, короткое, лишний_tlv, LDP_HELLO + LDP_HELLO, LDP_HELLO + b"\0", пустое,
                       tlv_за_концом):
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("LDP", разобрать_пакет(udp_пакет(плохое, 646, 646)).стек)
        self.assertIn("LDP", разобрать_пакет(tcp_пакет(LDP_HELLO + LDP_HELLO, 646, 40000)).стек)


def u16(д, м):
    return struct.unpack_from(">H", д, м)[0]


class GeneveTests(Проверки):
    def test_опция_и_внутренний_ethernet(self):
        кадр = geneve(опции=GENEVE_ОПЦИЯ)
        п = разобрать_пакет(udp_пакет(кадр, 40000, 6081))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "Geneve", "Ethernet", "IPv4", "UDP", "Данные"])
        self.место(п, "geneve.vni", 46, 3, 5001)
        self.место(п, "geneve.proto_type", 44, 2, 0x6558)
        self.место(п, "geneve.option.class", 50, 2, 0x0102)
        self.место(п, "geneve.option.type.critical", 52, 1, 1)
        self.место(п, "geneve.flags.critical", 43, 1, 1)
        self.assertEqual(["01020304"], значения(п, "geneve.option.data"))
        self.assertEqual(("192.168.0.1", "192.168.0.2"), (п.источник, п.получатель))
        п = разобрать_пакет(udp_пакет(geneve(ВНУТРЕННИЙ_IP, тип=0x0800, c=0), 40000, 6081))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "Geneve", "IPv4", "UDP", "Данные"])
        self.assertEqual("прикладной", уровень_протокола("Geneve"))

    def test_не_geneve(self):
        for плохое in (geneve(опции=GENEVE_ОПЦИЯ, первый=0x40 | 2),               # версия 1
                       geneve(опции=GENEVE_ОПЦИЯ, второй=0x41),                    # резерв флагов
                       geneve(опции=GENEVE_ОПЦИЯ, резерв=1),                       # резерв за VNI
                       geneve(тип=0x1234, c=0),                                    # неизвестный тип
                       geneve(опции=GENEVE_ОПЦИЯ, c=0),                            # C не сходится
                       geneve(опции=GENEVE_ОПЦИЯ[:3] + b"\x02" + GENEVE_ОПЦИЯ[4:]),  # опция за длиной
                       geneve(опции=GENEVE_ОПЦИЯ[:3] + b"\x21" + GENEVE_ОПЦИЯ[4:]),  # резерв опции
                       geneve(внутри=b"", c=0)):                                  # пусто внутри
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("Geneve", разобрать_пакет(udp_пакет(плохое, 40000, 6081)).стек)


class LispTests(Проверки):
    def test_данные_с_instance_id(self):
        кадр = bytes([0x88]) + b"\x12\x34\x56" + struct.pack(">I", (42 << 8) | 1) + ВНУТРЕННИЙ_IP
        п = разобрать_пакет(udp_пакет(кадр, 40000, 4341))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "LISP", "IPv4", "UDP", "Данные"])
        self.место(п, "lisp-data.iid", 46, 3, 42)
        self.место(п, "lisp-data.nonce", 43, 3, 0x123456)
        self.assertEqual([1], значения(п, "lisp-data.flags.iid"))
        self.assertEqual("сетевой", уровень_протокола("LISP"))

    def test_map_request_reply_register(self):
        п = разобрать_пакет(udp_пакет(LISP_MAP_REQUEST, 40000, 4342))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "LISP"])
        self.место(п, "lisp.type", 42, 1, 1)
        self.место(п, "lisp.nonce", 46, 8, 0x0102030405060708)
        self.assertEqual(["10.0.0.1"], значения(п, "lisp.mreq.srceid"))
        self.assertEqual(["192.0.2.1"], значения(п, "lisp.mreq.itr_rloc"))
        self.assertEqual("LISP Map-Request 10.1.1.0/24", п.инфо)
        п = разобрать_пакет(udp_пакет(LISP_MAP_REPLY, 4342, 40000))
        self.assertEqual(["192.0.2.10"], значения(п, "lisp.loc.locator"))
        self.assertEqual(([100], [1], [1440]), (значения(п, "lisp.loc.weight"), значения(п, "lisp.loc.reach"),
                                                значения(п, "lisp.mapping.ttl")))
        запись = поле(п, "lisp.mapping.eid.prefix")
        self.assertEqual((54, len(LISP_MAP_REPLY) - 12), (запись.смещение, запись.длина))
        п = разобрать_пакет(udp_пакет(LISP_MAP_REGISTER, 40000, 4342))
        self.assertEqual([20], значения(п, "lisp.mreg.authlen"))
        self.assertEqual("LISP Map-Register 10.1.1.0/24", п.инфо)
        с_xtr = bytes([0x30, 0, 0, 1]) + LISP_MAP_REGISTER[4:]
        с_xtr = bytes([0x32]) + с_xtr[1:] + bytes(range(24))
        self.assertEqual([bytes(range(16)).hex()], значения(разобрать_пакет(udp_пакет(с_xtr, 1, 4342)), "lisp.xtrid"))

    def test_ecm_с_вложенным_запросом(self):
        внутри = с.ip(с.udp(LISP_MAP_REQUEST, 40001, 4342, src="10.0.0.1", dst="10.1.1.1"), 17,
                      src="10.0.0.1", dst="10.1.1.1")
        п = разобрать_пакет(udp_пакет(bytes([0x80, 0, 0, 0]) + внутри, 40000, 4342))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "LISP", "IPv4", "UDP", "LISP"])
        self.assertEqual([8, 1], значения(п, "lisp.type"))

    def test_не_lisp(self):
        for плохое, порт in ((bytes([0x89]) + bytes(7) + ВНУТРЕННИЙ_IP, 4341),         # резервные флаги
                             (bytes([0x90]) + bytes(7) + ВНУТРЕННИЙ_IP, 4341),         # N и V вместе
                             (bytes([0x80]) + bytes(7) + ВНУТРЕННИЙ_IP[:-1], 4341),    # IP короче
                             (LISP_MAP_REQUEST + b"\0", 4342),                          # лишний байт
                             (LISP_MAP_REQUEST[:12] + b"\x00\x07" + LISP_MAP_REQUEST[14:], 4342),  # AFI 7
                             (LISP_MAP_REPLY[:3] + b"\0" + LISP_MAP_REPLY[4:], 4342),   # записей 0
                             (bytes([0x20, 0, 0, 0]) + NONCE, 4342),                   # записей 0, пусто
                             (LISP_MAP_REPLY + b"\0", 4342),                            # хвост Map-Reply
                             (LISP_MAP_REQUEST[:12] + b"\x00\x07" + LISP_MAP_REQUEST[18:], 4342),  # AFI 7 без адреса
                             (bytes([0x80]) + bytes(7) + ВНУТРЕННИЙ_IP + b"\0", 4341),   # IP короче данных
                             (bytes([0x80]) + bytes(7) + ВНУТРЕННИЙ_IP[:8] + b"\x3f" + ВНУТРЕННИЙ_IP[9:], 4341),
                             # ↑ TTL внутреннего IPv4 изменён — сумма заголовка не сходится
                             (bytes([0x60]) + LISP_MAP_REPLY[1:], 4342),                # тип 6
                             (LISP_MAP_REGISTER + bytes(24), 4342),                     # xTR-ID без бита I
                             (bytes([0x80, 0, 0, 0]) + ВНУТРЕННИЙ_IP[:-1], 4342)):      # ECM без целого IP
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("LISP", разобрать_пакет(udp_пакет(плохое, 40000, порт)).стек)


class CapwapTests(Проверки):
    def test_discovery_request(self):
        п = разобрать_пакет(udp_пакет(CAPWAP_DISCOVERY, 40000, 5246))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "CAPWAP"])
        self.место(п, "capwap.preamble.version", 42, 1, 0)
        self.место(п, "capwap.header.length", 43, 1, 8)
        self.место(п, "capwap.header.wbid", 44, 1, 1)
        self.место(п, "capwap.control.header.seq_number", 54, 1, 5)
        self.assertEqual([20, 45], значения(п, "capwap.message_element.type"))
        self.assertEqual(["ap-1"], значения(п, "capwap.control.wtp_name"))
        self.assertEqual(["Static Configuration"],
                         [ф.текст for ф in все_поля(п) if ф.ключ == "capwap.control.discovery_type"])
        self.assertEqual("CAPWAP Discovery Request, номер 5", п.инфо)
        self.assertEqual("прикладной", уровень_протокола("CAPWAP"))

    def test_данные_802_3_mac_радио_и_dtls(self):
        mac_радио = bytes([6]) + bytes.fromhex("0a0b0c0d0e0f") + b"\0"
        кадр = capwap_заголовок(hlen=4, m=1, доп=mac_радио) + ВНУТРЕННИЙ
        п = разобрать_пакет(udp_пакет(кадр, 40000, 5247))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "CAPWAP", "Ethernet", "IPv4", "UDP", "Данные"])
        self.assertEqual(["0a:0b:0c:0d:0e:0f"], значения(п, "capwap.header.mac"))
        self.место(п, "capwap.header.mac", 50, 7)
        dtls = bytes([1, 0, 0, 0]) + DTLS_HELLO
        п = разобрать_пакет(udp_пакет(dtls, 40000, 5246))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "CAPWAP", "DTLS"])
        self.assertEqual([1], значения(п, "dtls.handshake.type"))
        keepalive = capwap_заголовок(k=1) + struct.pack(">H", 20) + capwap_элемент(35, bytes(16))
        п = разобрать_пакет(udp_пакет(keepalive, 5247, 5247))
        self.assertEqual("CAPWAP Keep-Alive", п.инфо)

    def test_не_capwap(self):
        управление = CAPWAP_DISCOVERY[8:]
        for плохое, порт in ((bytes([0x10]) + CAPWAP_DISCOVERY[1:], 5246),               # версия 1
                             (capwap_заголовок(флаги=1) + управление, 5246),              # флаги заголовка
                             (capwap_заголовок(rsvd=1) + управление, 5246),               # резерв фрагмента
                             (capwap_заголовок(wbid=2) + управление, 5246),               # WBID 2
                             (capwap_заголовок(hlen=3) + управление, 5246),               # HLEN за полями
                             (capwap_заголовок(hlen=3, доп=bytes(4)) + управление, 5246),  # HLEN без M и W
                             (capwap_заголовок() + управление[:7] + b"\x01" + управление[8:], 5246),  # флаги сообщения
                             (capwap_заголовок() + управление[:5] + b"\x00\x02" + управление[7:], 5246),  # длина
                             (capwap_заголовок() + struct.pack(">I", 99) + управление[4:], 5246),  # тип 99
                             (capwap_заголовок(t=1) + управление, 5246),                  # T в управлении
                             (bytes([1, 0, 0, 1]) + DTLS_HELLO, 5246),                    # резерв DTLS
                             (bytes([1, 0, 0, 0]) + b"\x16\x03\x03" + DTLS_HELLO[3:], 5246),   # не DTLS
                             (capwap_заголовок(), 5247)):                                 # пустой кадр
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("CAPWAP", разобрать_пакет(udp_пакет(плохое, 40000, порт)).стек)


class OpenVpnTests(Проверки):
    def test_udp_сброс_управление_подтверждение(self):
        п = разобрать_пакет(udp_пакет(OVPN_RESET, 40000, 1194))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "OpenVPN"])
        self.место(п, "openvpn.type", 42, 1, 7)
        self.место(п, "openvpn.sessionid", 43, 8, 0x1122334455667788)
        self.место(п, "openvpn.mpid", 52, 4, 0)
        self.assertEqual("OpenVPN P_CONTROL_HARD_RESET_CLIENT_V2, ключ 0, номер 0", п.инфо)
        п = разобрать_пакет(udp_пакет(OVPN_CONTROL, 40000, 1194))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "OpenVPN", "TLS"])
        self.assertEqual([0], значения(п, "openvpn.mpid_arrayelement"))
        self.assertEqual([0x99AABBCCDDEEFF00], значения(п, "openvpn.rsessionid"))
        self.assertIn("P_CONTROL_V1", п.инфо)
        п = разобрать_пакет(udp_пакет(OVPN_ACK, 1194, 40000))
        self.assertEqual(([5], [3]), (значения(п, "openvpn.type"), значения(п, "openvpn.mpid_arrayelement")))
        п = разобрать_пакет(udp_пакет(OVPN_TLS_AUTH, 1194, 40000))
        self.assertEqual(([1], [bytes(range(20)).hex()]), (значения(п, "openvpn.keyid"), значения(п, "openvpn.hmac")))
        self.assertEqual([1700000000], значения(п, "openvpn.net_time"))
        self.assertEqual("прикладной", уровень_протокола("OpenVPN"))

    def test_данные_только_по_tcp_или_по_выбору(self):
        данные_ = bytes([9 << 3]) + b"\x00\x00\x05" + bytes(range(40))
        self.assertNotIn("OpenVPN", разобрать_пакет(udp_пакет(данные_, 40000, 1194)).стек)
        п = разобрать_пакет(udp_пакет(данные_, 40000, 1194), как={"udp:1194": "OpenVPN"})
        self.assertEqual([5], значения(п, "openvpn.peerid"))
        поток = struct.pack(">H", len(OVPN_RESET)) + OVPN_RESET + struct.pack(">H", len(данные_)) + данные_
        п = разобрать_пакет(tcp_пакет(поток, 40000, 1194))
        self.свой(п, ["Ethernet", "IPv4", "TCP", "OpenVPN"])
        self.assertEqual([len(OVPN_RESET), len(данные_)], значения(п, "openvpn.plen"))
        self.assertEqual([7, 9], значения(п, "openvpn.type"))
        self.место(п, "openvpn.plen", 54, 2 + len(OVPN_RESET))

    def test_не_openvpn(self):
        for плохое in (bytes([7 << 3]) + SID + b"\x00" + struct.pack(">I", 1),       # сброс не с номером 0
                       bytes([5 << 3]) + SID + b"\x09" + bytes(36) + SID2,          # 9 подтверждений
                       bytes([4 << 3]) + SID + b"\x00" + struct.pack(">I", 1) + b"not tls at all",
                       bytes([3 << 3]) + SID + b"\x00" + struct.pack(">I", 0) + b"x",  # мягкий сброс с нагрузкой
                       bytes([12 << 3]) + SID + b"\x00" + struct.pack(">I", 0),     # опкод 12
                       OVPN_ACK + b"\0",
                       bytes([5 << 3]) + SID + b"\x00"):                             # P_ACK без подтверждений
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("OpenVPN", разобрать_пакет(udp_пакет(плохое, 40000, 1194)).стек)
        кадр = struct.pack(">H", len(OVPN_RESET) + 1) + OVPN_RESET
        self.assertNotIn("OpenVPN", разобрать_пакет(tcp_пакет(кадр, 40000, 1194)).стек)


class WireGuardTests(Проверки):
    def test_все_четыре_сообщения(self):
        for тип, длина, ключ in ((1, 148, "wg.encrypted_timestamp"), (2, 92, "wg.encrypted_empty"),
                                 (3, 64, "wg.encrypted_cookie"), (4, 32, "wg.encrypted_packet"),
                                 (4, 80, "wg.counter")):
            with self.subTest(тип=тип, длина=длина):
                п = разобрать_пакет(udp_пакет(wg(тип, длина), 40000, 51820))
                self.свой(п, ["Ethernet", "IPv4", "UDP", "WireGuard"])
                self.место(п, "wg.type", 42, 1, тип)
                self.assertTrue(значения(п, ключ))
        п = разобрать_пакет(udp_пакет(wg(1, 148), 40000, 51820))
        self.место(п, "wg.sender", 46, 4, 0x01020304)
        self.место(п, "wg.ephemeral", 50, 32)
        self.место(п, "wg.mac2", 42 + 132, 16)
        п = разобрать_пакет(udp_пакет(wg(4, 48), 40000, 51820))
        self.место(п, "wg.receiver", 46, 4, 0x01020304)
        self.место(п, "wg.encrypted_packet", 58, 32)

    def test_строгие_длины(self):
        for плохое in (wg(1, 149), wg(2, 91), wg(3, 68), wg(4, 40), wg(4, 16), wg(5, 64),
                       bytes([1, 0, 1, 0]) + wg(1, 148)[4:]):
            with self.subTest(плохое=плохое[:4].hex() + f"/{len(плохое)}"):
                self.assertNotIn("WireGuard", разобрать_пакет(udp_пакет(плохое, 40000, 51820)).стек)


class StunTests(Проверки):
    def test_запрос_и_ответ_с_xor_адресами(self):
        п = разобрать_пакет(udp_пакет(STUN_ЗАПРОС, 40000, 3478))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "STUN"])
        self.место(п, "stun.type", 42, 2, 1)
        self.место(п, "stun.cookie", 46, 4, МАГИЯ)
        self.место(п, "stun.att.software", 66, 4, "test")
        self.assertIn("верна", поле(п, "stun.att.crc32").текст)
        self.assertEqual("STUN Binding запрос", п.инфо)
        п = разобрать_пакет(udp_пакет(STUN_ОТВЕТ, 3478, 40000))
        self.assertEqual(["198.51.100.7"], значения(п, "stun.att.ipv4"))
        self.assertEqual(["2001:db8::7"], значения(п, "stun.att.ipv6"))
        self.assertEqual([32853, 4000], значения(п, "stun.att.port"))
        self.место(п, "stun.att.ipv4", 70, 4)
        атрибут = [ф for ф in п.уровни[-1].поля if ф.ключ == "stun.attribute"][0]
        self.assertEqual("XOR-MAPPED-ADDRESS: 198.51.100.7:32853", атрибут.текст)
        self.assertIn("успешный ответ", п.инфо)

    def test_по_признакам_и_по_tcp(self):
        п = разобрать_пакет(udp_пакет(STUN_ЗАПРОС, 50000, 50001))
        self.assertIn("STUN", п.стек)
        п = разобрать_пакет(tcp_пакет(STUN_ЗАПРОС + STUN_ОТВЕТ, 50000, 50001))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "STUN", "STUN"], п.стек)
        канал = struct.pack(">HH", 0x4001, 5) + b"hello"
        п = разобрать_пакет(udp_пакет(канал, 40000, 3478))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "TURN", "Данные"])
        self.место(п, "turn.channel", 42, 2, 0x4001)
        п = разобрать_пакет(tcp_пакет(канал + b"\0\0\0" + STUN_ЗАПРОС, 40000, 3478))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TURN", "STUN"], п.стек)
        # ChannelData по признакам не угадывается: четыре байта ничего не доказывают.
        self.assertNotIn("TURN", разобрать_пакет(udp_пакет(канал, 50000, 50001)).стек)
        self.assertEqual("прикладной", уровень_протокола("STUN"))

    def test_не_stun(self):
        for плохое in (stun(0x0001, stun_атрибут(0x8022, b"test"), магия=0x2112A443),
                       stun(0x0001, stun_атрибут(0x8022, b"test"), испортить_отпечаток=True),
                       stun(0xC001, stun_атрибут(0x8022, b"test")),
                       STUN_ЗАПРОС + b"\0\0\0\0",
                       STUN_ЗАПРОС[:2] + struct.pack(">H", u16(STUN_ЗАПРОС, 2) - 2) + STUN_ЗАПРОС[4:-2],
                       stun(0x0001, stun_атрибут(0x8028, b"\0\0\0\0") + stun_атрибут(0x8022, b"test"), отпечаток=False),
                       STUN_ЗАПРОС + STUN_ОТВЕТ,                                      # два сообщения в дейтаграмме
                       отпечаток_не_последний(),
                       struct.pack(">HH", 0x5001, 5) + b"hello",
                       struct.pack(">HH", 0x4001, 9) + b"hello"):
            with self.subTest(плохое=плохое.hex()):
                self.assertFalse({"STUN", "TURN"} & set(разобрать_пакет(udp_пакет(плохое, 40000, 3478)).стек))


class DtlsTests(Проверки):
    def test_client_hello_по_признакам(self):
        п = разобрать_пакет(udp_пакет(DTLS_HELLO, 40000, 4433))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "DTLS"])
        self.место(п, "dtls.record.content_type", 42, len(DTLS_HELLO), 22)
        self.место(п, "dtls.record.version", 43, 2, 0xFEFF)
        self.место(п, "dtls.record.epoch", 45, 2, 0)
        self.место(п, "dtls.record.length", 53, 2, len(DTLS_HELLO) - 13)
        self.место(п, "dtls.handshake.type", 55, len(DTLS_HELLO) - 13, 1)
        self.место(п, "dtls.handshake.version", 67, 2, 0xFEFD)
        self.assertEqual(["—"], значения(п, "dtls.handshake.cookie"))
        self.assertEqual("DTLS ClientHello", п.инфо)
        self.assertEqual("прикладной", уровень_протокола("DTLS"))

    def test_несколько_записей_и_единый_заголовок(self):
        пакет = dtls_запись(20, b"\x01", версия=0xFEFD) + dtls_запись(22, bytes(40), эпоха=1, номер=0, версия=0xFEFD) \
            + bytes([0x2C]) + b"\x00\x07" + struct.pack(">H", 20) + bytes(20)
        п = разобрать_пакет(udp_пакет(пакет, 40000, 4433))
        self.assertEqual("DTLS ChangeCipherSpec, зашифрованное рукопожатие, зашифрованная запись 1.3", п.инфо)
        self.assertEqual([0, 1], значения(п, "dtls.record.epoch"))

    def test_не_dtls(self):
        for плохое in (b"\x16\x03\x03" + DTLS_HELLO[3:],                           # версия TLS, не DTLS
                       DTLS_HELLO + b"\0",                                           # хвост
                       DTLS_HELLO[:11] + struct.pack(">H", len(DTLS_HELLO) - 12) + DTLS_HELLO[13:],   # длина за концом
                       DTLS_HELLO[:13 + 6] + (10 ** 6).to_bytes(3, "big") + DTLS_HELLO[13 + 9:],       # фрагмент
                       bytes([0x2C]) + b"\x00\x07" + struct.pack(">H", 20) + bytes(20)):   # единый заголовок первым
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("DTLS", разобрать_пакет(udp_пакет(плохое, 40000, 4433)).стек)


class TeredoTests(Проверки):
    def test_индикаторы_и_ipv6(self):
        п = разобрать_пакет(udp_пакет(TEREDO_ИСТОЧНИК + ПУЗЫРЬ, 3544, 40000))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "Teredo", "IPv6"])
        self.место(п, "teredo.orig.port", 44, 2, 40000)
        self.место(п, "teredo.orig.addr", 46, 4, "203.0.113.9")
        аутентификация = struct.pack(">HBB", 1, 0, 0) + bytes(range(8)) + b"\x00"
        п = разобрать_пакет(udp_пакет(аутентификация + TEREDO_ИСТОЧНИК + ПУЗЫРЬ, 3544, 40000))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "Teredo", "IPv6"])
        self.место(п, "teredo.auth.nonce", 46, 8, "0001020304050607")
        self.assertEqual("аутентификация, источник 203.0.113.9:40000", п.уровни[3].итог)

    def test_не_teredo(self):
        for плохое in (TEREDO_ИСТОЧНИК + ПУЗЫРЬ + b"\0", TEREDO_ИСТОЧНИК + ВНУТРЕННИЙ_IP,
                       struct.pack(">HBB", 1, 5, 0) + bytes(9) + ПУЗЫРЬ):
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("Teredo", разобрать_пакет(udp_пакет(плохое, 3544, 40000)).стек)


class MplsUdpTests(Проверки):
    def test_стек_меток_и_ip(self):
        п = разобрать_пакет(udp_пакет(mpls_метка(1000, 0) + mpls_метка(100) + ВНУТРЕННИЙ_IP, 40000, 6635))
        self.свой(п, ["Ethernet", "IPv4", "UDP", "MPLS", "IPv4", "UDP", "Данные"])
        self.assertEqual([1000, 100], значения(п, "mpls.label"))

    def test_не_mpls(self):
        for плохое in (b"".join(mpls_метка(16 + i, 0) for i in range(16)) + mpls_метка(100) + ВНУТРЕННИЙ_IP,
                       mpls_метка(100) + ВНУТРЕННИЙ_IP[:-1], mpls_метка(100, 0) + mpls_метка(1, 0)):
            with self.subTest(плохое=плохое.hex()):
                self.assertNotIn("MPLS", разобрать_пакет(udp_пакет(плохое, 40000, 6635)).стек)


# -- фильтры, обрывы, случайные данные ------------------------------------------------------

ОБРАЗЦЫ = [
    с.eth(с.ip(PIM_HELLO, 103)),                                            # 0
    с.eth(с.ip(EIGRP_HELLO, 88)),                                           # 1
    с.eth(с.ip(RSVP_PATH, 46)),                                             # 2
    с.eth(с.ip(dccp(0, 1, 5, служба=42), 33)),                              # 3
    с.eth(с.ip(udplite(с.dns_запрос("f.example"), 8), 136)),                # 4
    udp_пакет(LDP_HELLO, 646, 646),                                         # 5
    udp_пакет(geneve(опции=GENEVE_ОПЦИЯ), 40000, 6081),                     # 6
    udp_пакет(LISP_MAP_REPLY, 4342, 40000),                                 # 7
    udp_пакет(CAPWAP_DISCOVERY, 40000, 5246),                               # 8
    udp_пакет(OVPN_CONTROL, 40000, 1194),                                   # 9
    udp_пакет(wg(1, 148), 40000, 51820),                                    # 10
    udp_пакет(STUN_ОТВЕТ, 3478, 40000),                                     # 11
    udp_пакет(DTLS_HELLO, 40000, 4433),                                     # 12
    udp_пакет(TEREDO_ИСТОЧНИК + ПУЗЫРЬ, 3544, 40000),                       # 13
    udp_пакет(mpls_метка(100) + ВНУТРЕННИЙ_IP, 40000, 6635),                # 14
    udp_пакет(struct.pack(">HH", 0x4001, 5) + b"hello", 40000, 3478),       # 15
    tcp_пакет(struct.pack(">H", len(OVPN_RESET)) + OVPN_RESET, 40000, 1194),  # 16
    udp_пакет(bytes([0x88]) + b"\x12\x34\x56" + struct.pack(">I", 42 << 8) + ВНУТРЕННИЙ_IP, 40000, 4341),  # 17
]


class ФильтрыTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.поля = [разобрать_пакет(п, номер=i + 1).поля_фильтра() for i, п in enumerate(ОБРАЗЦЫ)]

    def test_по_имени_и_по_полю(self):
        for текст, ждём in (("pim", [0]), ("pim.holdtime == 105", [0]), ("eigrp", [1]), ("eigrp.as == 100", [1]),
                            ("rsvp", [2]), ("rsvp.session.port == 5000", [2]), ("dccp", [3]),
                            ("dccp.service_code == 42", [3]), ("udp-lite", [4]),
                            ("udplite.checksum_coverage == 8", [4]),
                            ("udp-lite and dns", [4]), ("ldp", [5]), ("ldp.hdr.ldp_id == 1.1.1.1", [5]),
                            ("geneve", [6]), ("geneve.vni == 5001", [6]), ("lisp", [7, 17]),
                            ("lisp.loc.locator == 192.0.2.10", [7]), ("lisp-data.iid == 42", [17]),
                            ("capwap", [8]), ("capwap.control.header.msg_type.enterprise_specific == 1", [8]),
                            ("openvpn", [9, 16]), ("openvpn.type == 4", [9]), ("wireguard", [10]),
                            ("wg.type == 1", [10]), ("stun", [11]), ("stun.att.ipv4 == 198.51.100.0/24", [11]),
                            ("turn", [15]), ("turn.channel == 0x4001", [15]), ("dtls", [12]),
                            ("dtls.record.version == 0xfeff", [12]), ("teredo", [13]),
                            ("teredo.orig.addr == 203.0.113.9", [13]), ("mpls.label == 100", [14]),
                            ("tls", [9])):
            with self.subTest(текст=текст):
                self.assertEqual(ждём, отобрать(self.поля, текст))


class ОбрывTests(unittest.TestCase):
    def test_любой_обрыв_без_исключений(self):
        for i, пакет in enumerate(ОБРАЗЦЫ):
            for длина in range(len(пакет)):
                with self.subTest(образец=i, длина=длина):
                    п = разобрать_пакет(пакет[:длина])
                    self.assertFalse([о for о in п.ошибки if о.startswith("разбор прерван")], п.ошибки)

    def test_оборванный_захват_по_ip(self):
        # Захват короче объявленной длины IP: сумму не проверить, заголовок разбирается.
        # Обрыв — посреди первого поля за общим заголовком (PIM 4, EIGRP 20, RSVP 8, DCCP 20 байт).
        for i, длина in ((0, 34 + 6), (1, 34 + 22), (2, 34 + 10), (3, 34 + 18)):
            пакет = ОБРАЗЦЫ[i]
            п = разобрать_пакет(пакет[:длина])
            with self.subTest(образец=i):
                self.assertTrue(set(п.стек) & СВОИ, п.стек)
                self.assertIn("пакет оборван: заголовок длиннее записанных байт", п.ошибки)


class СлужебныеTests(unittest.TestCase):
    def test_обрыв_внутри_туннеля_не_отменяет_туннель(self):
        кадр = udp_пакет(geneve(опции=GENEVE_ОПЦИЯ), 40000, 6081)
        п = разобрать_пакет(кадр[:42 + 16 + 14 + 10])                   # внутренний IPv4 оборван
        self.assertEqual(["Ethernet", "IPv4", "UDP", "Geneve", "Ethernet", "IPv4"], п.стек)
        self.assertIn("пакет оборван: заголовок длиннее записанных байт", п.ошибки)
        self.assertTrue(п.уровни[-1].итог.endswith("[оборван]"))

    def test_оборванный_tls_в_openvpn_остаётся_данными(self):
        запись = tls_client_hello()
        обрезанная = запись[:5] + запись[5:30]                             # заголовок записи цел, тело — нет
        пакет = OVPN_CONTROL[:-len(запись)] + обрезанная
        п = разобрать_пакет(udp_пакет(пакет, 40000, 1194))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "OpenVPN", "Данные"], п.стек)
        self.assertFalse([о for о in п.ошибки if о.startswith("разбор прерван")], п.ошибки)
        self.assertIn("OpenVPN P_CONTROL_V1", п.инфо)

    def test_занятый_порт_пробует_оба(self):
        from reportgen.setevoy.protokoly.marshrutizatsiya import _на_порт
        вызовы = []

        def мой(р, м, конец):
            вызовы.append("мой")
            return True

        for прежний, ждём in ((lambda р, м, к: вызовы.append("прежний") or True, ["прежний"]),
                              (lambda р, м, к: вызовы.append("прежний") and False, ["прежний", "мой"]),
                              (lambda р, м, к: вызовы.append("прежний") or [][1], ["прежний", "мой"])):
            with self.subTest(ждём=ждём):
                вызовы.clear()
                таблица = {7: прежний}
                _на_порт(таблица, 7, мой)
                р = type("Р", (), {"п": type("П", (), {"уровни": []})()})()
                self.assertTrue(таблица[7](р, 0, 0))
                self.assertEqual(ждём, вызовы)
        таблица = {}
        _на_порт(таблица, 8, мой)
        self.assertIs(мой, таблица[8])


class СлучайныеTests(unittest.TestCase):
    """2000 случайных нагрузок на каждый номер протокола и порт: своими не признаются."""

    ПАКЕТОВ = 2000

    def нагрузки(self, зерно):
        rng = random.Random(зерно)
        for _ in range(self.ПАКЕТОВ):
            yield bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 200)))

    def проверить(self, собрать, запрещено=СВОИ, зерно=1, допуск=0):
        чужих = 0
        for нагрузка in self.нагрузки(зерно):
            стек = set(разобрать_пакет(собрать(нагрузка)).стек)
            чужих += bool(стек & запрещено)
        self.assertLessEqual(чужих, допуск)
        return чужих

    def test_номера_протоколов_ip(self):
        for номер in (103, 88, 46, 33, 136):
            with self.subTest(номер=номер):
                self.проверить(lambda н, номер=номер: с.eth(с.ip(н, номер)), зерно=номер)

    def test_порты_udp(self):
        for порт in (646, 6081, 4341, 4342, 5246, 5247, 1194, 51820, 3478, 3544):
            with self.subTest(порт=порт):
                self.проверить(lambda н, порт=порт: udp_пакет(н, 40000, порт), зерно=порт)
        self.проверить(lambda н: udp_пакет(н, 40000, 6635), СВОИ | {"MPLS"}, зерно=6635)

    def test_порты_tcp_и_признаки(self):
        for порт in (646, 3478):
            with self.subTest(порт=порт):
                self.проверить(lambda н, порт=порт: tcp_пакет(н, 40000, порт), зерно=порт + 1)
        # OpenVPN по TCP: у пакета данных проверяемо только 16-битное поле длины кадра
        # (совпадает с сегментом с вероятностью 2^-16), остальное зашифровано. Допуск — 0,5 %.
        for зерно in (1195, 1):
            with self.subTest(openvpn_tcp=зерно):
                self.проверить(lambda н: tcp_пакет(н, 40000, 1194), зерно=зерно, допуск=self.ПАКЕТОВ // 200)
        self.проверить(lambda н: udp_пакет(н, 50000, 50001), зерно=7)
        self.проверить(lambda н: tcp_пакет(н, 50000, 50001), зерно=8)

    def test_случайное_с_верным_началом(self):
        """Верные первые байты и случайный хвост — проверки глубже первого байта тоже работают."""
        rng = random.Random(99)
        for начало, порт, имя in ((b"\x00\x10\x02\x00", 5246, "CAPWAP"), (b"\x38", 1194, "OpenVPN"),
                                  (b"\x20\x00\x00\x01", 4342, "LISP"), (b"\x00\x01\x00\x0c\x21\x12\xa4\x42", 3478,
                                                                         "STUN"),
                                  (b"\x16\xfe\xff\x00\x00", 4433, "DTLS"), (b"\x00\x01", 646, "LDP")):
            with self.subTest(имя=имя):
                чужих = 0
                for _ in range(self.ПАКЕТОВ):
                    хвост = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 120)))
                    чужих += имя in разобрать_пакет(udp_пакет(начало + хвост, 40000, порт)).стек
                self.assertEqual(0, чужих)


if __name__ == "__main__":
    unittest.main()
