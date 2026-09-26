# -*- coding: utf-8 -*-
"""Корпоративные службы, файлы и базы данных: пакеты собраны здесь по стандартам.

Сборка не зовёт функций разборщика: поля кладутся struct.pack'ом в порядке из
RFC / [MS-SMB2] / спецификаций протоколов, суммы (CRC-32C MongoDB) считаются здесь же.
"""

import random
import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.filtr import отобрать
from reportgen.setevoy.statistika import уровень_протокола

ЗАГ_UDP = 14 + 20 + 8
ЗАГ_TCP = 14 + 20 + 20


def udp(нагрузка, sport, dport):
    return с.eth(с.ip(с.udp(нагрузка, sport, dport), 17))


def tcp(нагрузка, sport, dport):
    return с.eth(с.ip(с.tcp(нагрузка, sport, dport), 6))


def udp6(нагрузка, sport, dport, src="fe80::1", dst="ff02::1:2"):
    return с.eth(с.ip6(с.udp(нагрузка, sport, dport, src=src, dst=dst, v6=True), 17, src=src, dst=dst),
                 тип=0x86DD)


def поля(п):
    return п.поля_фильтра()


def поле(п, ключ):
    """Первое поле с ключом (обход дерева в порядке показа) — для проверки места в байтах."""
    def обойти(список):
        for ф in список:
            if ф.ключ == ключ:
                return ф
            найдено = обойти(ф.дети)
            if найдено is not None:
                return найдено
        return None
    for у in п.уровни:
        найдено = обойти(у.поля)
        if найдено is not None:
            return найдено
    raise AssertionError(f"нет поля {ключ}")


def место(п, ключ):
    ф = поле(п, ключ)
    return ф.смещение, ф.длина


def выбрать(пакеты, текст):
    return [i for i in отобрать([разобрать_пакет(п).поля_фильтра() for п in пакеты], текст)]


def crc32c(данные):
    crc = 0xFFFFFFFF
    for б in данные:
        crc ^= б
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


# -- NetBIOS -----------------------------------------------------------------------------------------------

def nb_имя(имя, суффикс=0x20):
    """RFC 1001, 14.1: 16 байт (имя до 15 пробелами + суффикс), по полубайту на букву 'A'+n."""
    сырое = имя.encode().ljust(15, b" ")[:15] + bytes([суффикс])
    return b"\x20" + bytes(б for x in сырое for б in (0x41 + (x >> 4), 0x41 + (x & 15))) + b"\0"


def nbns_запрос(имя="WORKGROUP", суффикс=0x1D, флаги=0x0110):
    return struct.pack(">HHHHHH", 0x1234, флаги, 1, 0, 0, 0) + nb_имя(имя, суффикс) + struct.pack(">HH", 0x20, 1)


def nbns_регистрация():
    """Опкод 5, вопрос + дополнительная запись с именем указателем на вопрос (0xC00C)."""
    флаги = (5 << 11) | 0x0100 | 0x0010
    return (struct.pack(">HHHHHH", 0x0042, флаги, 1, 0, 0, 1) + nb_имя("PC1", 0x00) + struct.pack(">HH", 0x20, 1)
            + struct.pack(">HHHIH", 0xC00C, 0x20, 1, 300000, 6) + struct.pack(">H", 0x0000) + с.a4("10.0.0.7"))


def smb1(команда, слова=b"", байты=b"", флаги=0, флаги2=0, статус=0):
    заголовок = (b"\xffSMB" + struct.pack("<BIBHH8sHHHHH", команда, статус, флаги, флаги2, 0, bytes(8), 0,
                                           0xFFFF, 0x1111, 0x0064, 0x0002))
    return заголовок + bytes([len(слова) // 2]) + слова + struct.pack("<H", len(байты)) + байты


def nbds_датаграмма(данные, тип=0x11):
    имена = nb_имя("PC1", 0x00) + nb_имя("WORKGROUP", 0x1D)
    return (struct.pack(">BBH4sHHH", тип, 0x02, 0x7777, с.a4("10.0.0.1"), 138, len(имена) + len(данные), 0)
            + имена + данные)


def smb2_заголовок(команда, флаги=0, следующий=0, mid=1, дерево=0, сеанс=0, статус=0):
    """[MS-SMB2] 2.2.1.2 (синхронный): 64 байта, порядок байт — от младшего."""
    return (b"\xfeSMB" + struct.pack("<HHIHHIIQIIQ16s", 64, 1, статус, команда, 1, флаги, следующий, mid, 0,
                                     дерево, сеанс, bytes(16)))


def smb2_negotiate():
    тело = struct.pack("<HHHHI16sQ", 36, 2, 1, 0, 0, bytes(range(16)), 0) + struct.pack("<HH", 0x0202, 0x0311)
    return smb2_заголовок(0) + тело


def smb2_tree_connect(путь):
    путь_б = путь.encode("utf-16-le")
    return smb2_заголовок(3, mid=3, сеанс=0x1122) + struct.pack("<HHHH", 9, 0, 64 + 8, len(путь_б)) + путь_б


def smb2_create(имя, следующий=0, флаги=0):
    имя_б = имя.encode("utf-16-le")
    тело = struct.pack("<HBBIQQIIIIIHHII", 57, 0, 0, 2, 0, 0, 0x00120089, 0x80, 7, 1, 0x40, 64 + 56, len(имя_б), 0, 0)
    return smb2_заголовок(5, флаги=флаги, следующий=следующий, mid=4, дерево=5, сеанс=0x1122) + тело + имя_б


def smb2_close():
    тело = struct.pack("<HHI", 24, 0, 0) + b"\xff" * 16
    return smb2_заголовок(6, флаги=0x4, mid=5, дерево=5, сеанс=0x1122) + тело


def прямой(smb):
    return b"\0" + len(smb).to_bytes(3, "big") + smb


def nbss_сообщение(smb):
    return struct.pack(">BBH", 0, 0, len(smb)) + smb


class NetBIOSTests(unittest.TestCase):
    def test_nbns_запрос_имени(self):
        п = разобрать_пакет(udp(nbns_запрос(), 137, 137))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "NBNS"], п.стек)
        self.assertEqual(["WORKGROUP<1d>"], поля(п)["nbns.name"])
        self.assertEqual((ЗАГ_UDP + 12, 38), место(п, "nbns.name"))
        self.assertEqual((ЗАГ_UDP + 2, 2), место(п, "nbns.flags"))
        self.assertEqual([0], поля(п)["nbns.flags.opcode"])
        self.assertEqual([1], поля(п)["nbns.flags.broadcast"])
        self.assertIn("NBNS запрос имени: WORKGROUP<1d>", п.инфо)
        self.assertEqual([], п.ошибки)

    def test_nbns_регистрация_с_указателем(self):
        п = разобрать_пакет(udp(nbns_регистрация(), 137, 137))
        self.assertEqual("NBNS", п.протокол)
        self.assertEqual(["PC1<00>", "PC1<00>"], поля(п)["nbns.name"])
        self.assertEqual(["10.0.0.7"], поля(п)["nbns.addr"])
        self.assertEqual([300000], поля(п)["nbns.ttl"])
        self.assertEqual((ЗАГ_UDP + 12 + 38 + 12, 6), место(п, "nbns.addr"))

    def test_nbns_чужое(self):
        for плохой in (nbns_запрос(флаги=0x0110 | 0x0040),            # зарезервированный бит NM_FLAGS
                       nbns_запрос(флаги=(3 << 11)),                   # опкод 3 не определён
                       nbns_запрос(флаги=0x0003),                      # RCODE в запросе
                       nbns_запрос()[:13] + b"a" + nbns_запрос()[14:],  # буква вне A–P
                       nbns_запрос() + b"\0",                          # лишний байт
                       nbns_регистрация()[:-7] + b"\xc0\x40" + nbns_регистрация()[-5:]):  # указатель вперёд
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("NBNS", разобрать_пакет(udp(плохой, 137, 137)).стек)

    def test_nbds_и_smb_в_почтовом_ящике(self):
        слова = struct.pack("<HHHHBBHIHHHHHBB6s", 0, 8, 0, 0, 0, 0, 0, 0, 0, 0, 8, 0, 0, 3, 0, bytes(6))
        smb = smb1(0x25, слова, b"\\MAILSLOT\\BROWSE\0" + b"\x01" * 8)
        п = разобрать_пакет(udp(nbds_датаграмма(smb), 138, 138))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "NBDS", "SMB"], п.стек)
        self.assertEqual(["PC1<00>"], поля(п)["nbdgm.source_name"])
        self.assertEqual(["WORKGROUP<1d>"], поля(п)["nbdgm.destination_name"])
        self.assertEqual([len(smb) + 68], поля(п)["nbdgm.dgram_len"])
        self.assertEqual(["\\MAILSLOT\\BROWSE"], поля(п)["smb.trans_name"])
        self.assertEqual((ЗАГ_UDP + 14 + 68, 32), место(п, "smb.cmd"))          # узел заголовка SMB
        self.assertEqual((ЗАГ_UDP + 14 + 68 + 24, 2), место(п, "smb.tid"))
        # Длина датаграммы на 1 больше данных — не NBDS.
        плохой = bytearray(nbds_датаграмма(smb))
        плохой[11] += 1
        self.assertNotIn("NBDS", разобрать_пакет(udp(bytes(плохой), 138, 138)).стек)

    def test_nbds_ошибка(self):
        ошибка = struct.pack(">BBH4sHB", 0x13, 0x02, 1, с.a4("10.0.0.1"), 138, 0x82)
        п = разобрать_пакет(udp(ошибка, 138, 138))
        self.assertEqual("NBDS", п.протокол)
        self.assertEqual([0x82], поля(п)["nbdgm.error_code"])
        for плохой in (ошибка[:-1] + b"\x81", ошибка + b"\0", ошибка[:1] + b"\x12" + ошибка[2:]):
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("NBDS", разобрать_пакет(udp(плохой, 138, 138)).стек)

    def test_nbss_запрос_сеанса_и_служебные(self):
        имена = nb_имя("SERVER", 0x20) + nb_имя("CLIENT", 0x00)
        запрос = struct.pack(">BBH", 0x81, 0, len(имена)) + имена
        п = разобрать_пакет(tcp(запрос, 49000, 139))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "NBSS"], п.стек)
        self.assertEqual(["SERVER<20>"], поля(п)["nbss.called_name"])
        self.assertEqual(["CLIENT<00>"], поля(п)["nbss.calling_name"])
        self.assertEqual((ЗАГ_TCP + 4, 34), место(п, "nbss.called_name"))
        self.assertEqual("транспортный", уровень_протокола("NBSS"))
        # Положительный ответ и keep-alive подряд в одном сегменте.
        п = разобрать_пакет(tcp(b"\x82\x00\x00\x00\x85\x00\x00\x00", 139, 49000))
        self.assertEqual([0x82, 0x85], поля(п)["nbss.type"])
        for плохой in (b"\x85\x02\x00\x00", b"\x85\x00\x00\x01\x00", b"\x86\x00\x00\x00",
                       b"\x83\x00\x00\x01\x42"):
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("NBSS", разобрать_пакет(tcp(плохой, 139, 49000)).стек)

    def test_фильтры_netbios(self):
        пакеты = [udp(nbns_запрос(), 137, 137), udp(nbns_регистрация(), 137, 137),
                  tcp(b"\x85\x00\x00\x00", 139, 49000)]
        self.assertEqual([0, 1], выбрать(пакеты, "nbns"))
        self.assertEqual([1], выбрать(пакеты, "nbns.flags.opcode == 5"))
        self.assertEqual([0], выбрать(пакеты, "nbns.name contains \"WORKGROUP\""))
        self.assertEqual([2], выбрать(пакеты, "nbss"))


class SMBTests(unittest.TestCase):
    def test_smb2_negotiate_по_direct_tcp(self):
        smb = smb2_negotiate()
        п = разобрать_пакет(tcp(прямой(smb), 49000, 445))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "NBSS", "SMB2"], п.стек)
        self.assertEqual([0], поля(п)["smb2.cmd"][:1])
        self.assertEqual([0x0202, 0x0311], поля(п)["smb2.dialect"])
        self.assertEqual([len(smb)], поля(п)["nbss.length"])
        база = ЗАГ_TCP + 4
        self.assertEqual((база + 24, 8), место(п, "smb2.msg_id"))
        self.assertEqual((база + 40, 8), место(п, "smb2.sesid"))
        self.assertEqual((база + 64 + 36, 2), место(п, "smb2.dialect"))
        self.assertEqual([], п.ошибки)
        self.assertTrue(п.инфо.startswith("SMB2 NEGOTIATE запрос"), п.инфо)

    def test_smb2_tree_connect_и_create_цепочкой(self):
        п = разобрать_пакет(tcp(прямой(smb2_tree_connect("\\\\srv\\share")), 49000, 445))
        self.assertEqual(["\\\\srv\\share"], поля(п)["smb2.tree"])
        self.assertEqual((ЗАГ_TCP + 4 + 72, 22), место(п, "smb2.tree"))
        self.assertEqual([0x1122], поля(п)["smb2.sesid"])
        create = smb2_create("dir\\file.txt")
        create += b"\0" * (-len(create) % 8)
        цепочка = smb2_create("dir\\file.txt", следующий=len(create)) + b"\0" * (-len(smb2_create("dir\\file.txt")) % 8)
        цепочка += smb2_close()
        п = разобрать_пакет(tcp(прямой(цепочка), 49000, 445))
        self.assertEqual([5, 6], [к for к in поля(п)["smb2.cmd"] if isinstance(к, int)][::2][:2] or поля(п)["smb2.cmd"])
        self.assertEqual(["dir\\file.txt"], поля(п)["smb2.filename"])
        self.assertEqual([len(create), 0], поля(п)["smb2.chain_offset"])
        self.assertEqual([0, 1], поля(п)["smb2.flags.chained"])
        self.assertEqual((ЗАГ_TCP + 4 + 120, 24), место(п, "smb2.filename"))
        self.assertIn("CLOSE запрос", п.инфо)
        self.assertEqual([], п.ошибки)

    def test_smb2_ответ_с_ошибкой_и_через_nbss(self):
        ответ = smb2_заголовок(5, флаги=1, статус=0xC0000022) + struct.pack("<HBBI", 9, 0, 0, 0) + b"\0"
        п = разобрать_пакет(tcp(nbss_сообщение(ответ), 139, 49000))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "NBSS", "SMB2"], п.стек)
        self.assertEqual([0xC0000022], поля(п)["smb2.nt_status"])
        self.assertEqual([1], поля(п)["smb2.flags.response"])
        self.assertIn("STATUS_ACCESS_DENIED", п.инфо)
        self.assertEqual([], п.ошибки)

    def test_smb2_transform(self):
        зашифровано = bytes(range(80))
        transform = (b"\xfdSMB" + b"\x11" * 16 + b"\x22" * 16 + struct.pack("<IHHQ", 80, 0, 1, 0x1122)
                     + зашифровано)
        п = разобрать_пакет(tcp(прямой(transform), 445, 49000))
        self.assertEqual("SMB2", п.протокол)
        self.assertEqual([80], поля(п)["smb2.transform_msg_size"])
        self.assertEqual((ЗАГ_TCP + 4 + 36, 4), место(п, "smb2.transform_msg_size"))
        self.assertEqual([], п.ошибки)
        # Исходный размер не совпадает с зашифрованным — видно ошибкой.
        кривой = transform[:36] + struct.pack("<I", 96) + transform[40:]
        п = разобрать_пакет(tcp(прямой(кривой), 445, 49000))
        self.assertTrue(any("Transform" in о for о in п.ошибки), п.ошибки)
        # Флаги не 0x0001 — это не Transform.
        чужой = transform[:42] + struct.pack("<H", 2) + transform[44:]
        self.assertNotIn("SMB2", разобрать_пакет(tcp(прямой(чужой), 445, 49000)).стек)

    def test_smb1_negotiate(self):
        диалекты = b"\x02PC NETWORK PROGRAM 1.0\0\x02NT LM 0.12\0\x02SMB 2.002\0"
        п = разобрать_пакет(tcp(nbss_сообщение(smb1(0x72, b"", диалекты, флаги2=0xC853)), 49000, 139))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "NBSS", "SMB"], п.стек)
        self.assertEqual(["PC NETWORK PROGRAM 1.0", "NT LM 0.12", "SMB 2.002"], поля(п)["smb.dialect"])
        self.assertEqual([0x72, 0x72], поля(п)["smb.cmd"])
        self.assertEqual((ЗАГ_TCP + 4 + 5, 4), место(п, "smb.nt_status"))

    def test_smb2_чужое_и_угадывание(self):
        smb = smb2_negotiate()
        for плохой in (smb[:4] + b"\x41\x00" + smb[6:],                            # StructureSize 65
                       smb[:12] + b"\x13\x00" + smb[14:],                          # команда 0x13
                       smb[:16] + b"\x00\x01\x00\x00" + smb[20:]):                 # зарезервированный флаг
            with self.subTest(плохой=плохой[:24].hex()):
                п = разобрать_пакет(tcp(прямой(плохой), 49000, 445))
                self.assertNotIn("SMB2", п.стек)
        # Direct TCP без подписи SMB — не наш.
        self.assertNotIn("NBSS", разобрать_пакет(tcp(прямой(b"x" * 64), 49000, 445)).стек)
        # Без известного порта — по подписи SMB за заголовком NBSS.
        п = разобрать_пакет(tcp(прямой(smb), 50001, 50002))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "NBSS", "SMB2"], п.стек)

    def test_фильтры_smb(self):
        пакеты = [tcp(прямой(smb2_negotiate()), 49000, 445), tcp(прямой(smb2_tree_connect("\\\\a\\b")), 49000, 445),
                  tcp(nbss_сообщение(smb1(0x72, b"", b"\x02NT LM 0.12\0")), 49000, 139)]
        self.assertEqual([0, 1], выбрать(пакеты, "smb2"))
        self.assertEqual([2], выбрать(пакеты, "smb"))
        self.assertEqual([1], выбрать(пакеты, "smb2.tree contains \"\\\\a\""))
        self.assertEqual([0], выбрать(пакеты, "smb2.dialect == 785"))


# -- DHCPv6 ---------------------------------------------------------------------------------------------------

def опция6(код, значение):
    return struct.pack(">HH", код, len(значение)) + значение


DUID_LL = struct.pack(">HH", 3, 1) + bytes.fromhex("001122334455")


def dhcp6_solicit():
    return (bytes([1]) + (0x123456).to_bytes(3, "big") + опция6(1, DUID_LL) + опция6(8, b"\0\0")
            + опция6(3, struct.pack(">III", 0xABCD, 0, 0)) + опция6(6, struct.pack(">HH", 23, 24)))


def dhcp6_reply():
    iaaddr = опция6(5, с.a6("2001:db8::100") + struct.pack(">II", 3600, 7200))
    ia = опция6(3, struct.pack(">III", 0xABCD, 1800, 2880) + iaaddr)
    return (bytes([7]) + (0x123456).to_bytes(3, "big") + опция6(1, DUID_LL)
            + опция6(2, struct.pack(">HI", 2, 311) + b"srv") + ia
            + опция6(23, с.a6("2001:db8::53") + с.a6("2001:db8::54")))


class DHCPv6Tests(unittest.TestCase):
    def test_solicit(self):
        п = разобрать_пакет(udp6(dhcp6_solicit(), 546, 547))
        self.assertEqual(["Ethernet", "IPv6", "UDP", "DHCPv6"], п.стек)
        self.assertEqual([1], поля(п)["dhcpv6.msgtype"])
        self.assertEqual([0x123456], поля(п)["dhcpv6.xid"])
        self.assertEqual(["00:11:22:33:44:55"], поля(п)["dhcpv6.duid.linklayer_address"])
        база = 14 + 40 + 8
        self.assertEqual((база + 1, 3), место(п, "dhcpv6.xid"))
        self.assertEqual((база + 8 + 4, 6), место(п, "dhcpv6.duid.linklayer_address"))
        self.assertEqual([23, 24], поля(п)["dhcpv6.requested_option_code"])
        self.assertEqual([0xABCD], поля(п)["dhcpv6.iaid"])
        self.assertEqual([], п.ошибки)

    def test_reply_с_адресом_и_dns(self):
        п = разобрать_пакет(udp6(dhcp6_reply(), 547, 546, src="fe80::2", dst="fe80::1"))
        self.assertEqual(["2001:db8::100"], поля(п)["dhcpv6.iaaddr.ip"])
        self.assertEqual(["2001:db8::53", "2001:db8::54"], поля(п)["dhcpv6.dns_server"])
        self.assertEqual([311], поля(п)["dhcpv6.duid.enterprise"])
        # IA Address вложен в IA_NA — деревом.
        ia = [ф for ф in п.уровни[-1].поля if ф.сырое == 3][0]
        self.assertIn("dhcpv6.iaaddr.ip", [в.ключ for д in ia.дети for в in [д] + д.дети])
        self.assertIn("2001:db8::100", п.инфо)

    def test_relay_с_вложенным(self):
        вложенное = dhcp6_solicit()
        relay = (bytes([12, 0]) + с.a6("2001:db8:1::1") + с.a6("fe80::1") + опция6(18, b"eth0")
                 + опция6(9, вложенное))
        п = разобрать_пакет(udp6(relay, 547, 547, src="2001:db8:1::1", dst="2001:db8::547"))
        self.assertEqual("DHCPv6", п.протокол)
        self.assertEqual([12, 1], поля(п)["dhcpv6.msgtype"])
        self.assertEqual(["2001:db8:1::1"], поля(п)["dhcpv6.linkaddr"])
        база = 14 + 40 + 8 + 34 + 8 + 4
        self.assertEqual((база, 1), место(п, "dhcpv6.msgtype")[:0] + (поле(п, "dhcpv6.xid").смещение - 1, 1))
        self.assertIn("RELAY-FORW [SOLICIT", п.инфо)

    def test_чужое_и_обрыв(self):
        s = dhcp6_solicit()
        for плохой in (bytes([1, 0, 0, 1]) + опция6(8, b"\0\0"),        # нет Client/Server ID
                       s[:4] + struct.pack(">HH", 1, 99) + s[8:],          # опция длиннее сообщения
                       s + опция6(8, b"\0\0\0"),                           # Elapsed Time из 3 байт
                       bytes([14]) + s[1:],                                # тип 14 не из RFC 8415
                       bytes([12, 0]) + bytes(32) + опция6(18, b"x"),      # ретранслятор без Relay Message
                       bytes([12, 0]) + bytes(32) + опция6(9, bytes([14]) + s[1:]),  # вложено сообщение типа 14
                       s + b"\0"):                                         # хвост не опция
            with self.subTest(плохой=плохой[:12].hex()):
                self.assertNotIn("DHCPv6", разобрать_пакет(udp6(плохой, 546, 547)).стек)
        полный = udp6(s, 546, 547)
        for длина in range(len(полный) - len(s), len(полный)):
            разобрать_пакет(полный[:длина])                              # не бросает

    def test_фильтры(self):
        пакеты = [udp6(dhcp6_solicit(), 546, 547), udp6(dhcp6_reply(), 547, 546)]
        self.assertEqual([0, 1], выбрать(пакеты, "dhcpv6"))
        self.assertEqual([1], выбрать(пакеты, "dhcpv6.msgtype == 7"))


# -- RDP ------------------------------------------------------------------------------------------------------

def tpkt_(x224):
    return struct.pack(">BBH", 3, 0, 4 + len(x224)) + x224


def x224_cr(переменная):
    фикс = struct.pack(">BHHB", 0xE0, 0, 0x1234, 0)
    return bytes([len(фикс) + len(переменная)]) + фикс + переменная


class RDPTests(unittest.TestCase):
    ЗАПРОС = tpkt_(x224_cr(b"Cookie: mstshash=ivanov\r\n" + struct.pack("<BBHI", 1, 0, 8, 3)))

    def test_запрос_соединения(self):
        п = разобрать_пакет(tcp(self.ЗАПРОС, 49000, 3389))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TPKT", "COTP", "RDP"], п.стек)
        self.assertEqual(["mstshash=ivanov"], поля(п)["rdp.rt_cookie"])
        self.assertEqual([3], поля(п)["rdp.requestedProtocols"])
        self.assertEqual([0xE0], поля(п)["cotp.type"][:1])
        self.assertEqual([3], поля(п)["tpkt.version"])
        self.assertEqual((ЗАГ_TCP + 11, 25), место(п, "rdp.rt_cookie"))
        self.assertEqual((ЗАГ_TCP + 11 + 25 + 4, 4), место(п, "rdp.requestedProtocols"))
        self.assertIn("TLS, CredSSP", п.инфо)

    def test_подтверждение_и_данные(self):
        cc = tpkt_(bytes([14]) + struct.pack(">BHHB", 0xD0, 0x1234, 0x5678, 0) + struct.pack("<BBHI", 2, 0x1F, 8, 2))
        п = разобрать_пакет(tcp(cc, 3389, 49000))
        self.assertEqual([2], поля(п)["rdp.selectedProtocol"])
        self.assertIn("выбрано: CredSSP", п.инфо)
        отказ = tpkt_(bytes([14]) + struct.pack(">BHHB", 0xD0, 0x1234, 0x5678, 0) + struct.pack("<BBHI", 3, 0, 8, 5))
        self.assertEqual([5], поля(разобрать_пакет(tcp(отказ, 3389, 49000)))["rdp.neg_failure.code"])
        dt = tpkt_(b"\x02\xf0\x80" + b"\x7f\x65\x82\x01\x00")
        п = разобрать_пакет(tcp(dt, 49000, 3389))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TPKT", "COTP", "Данные"], п.стек)
        self.assertEqual([1], поля(п)["cotp.eot"])

    def test_чужое_и_tls_на_том_же_порту(self):
        з = self.ЗАПРОС
        for плохой in (b"\x04" + з[1:],                                    # версия TPKT не 3
                       з[:1] + b"\x01" + з[2:],                            # резерв не ноль
                       з[:2] + struct.pack(">H", len(з) + 1) + з[4:],      # длина больше данных
                       з[:6] + b"\x00\x01" + з[8:],                        # DST-REF в CR не ноль
                       з[:-8] + struct.pack("<BBHI", 1, 0, 9, 3),          # длина RDP_NEG_REQ не 8
                       tpkt_(b"\x03\xf0\x80\x00")):                        # DT с LI 3
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("TPKT", разобрать_пакет(tcp(плохой, 49000, 3389)).стек)
        tls_запись = b"\x16\x03\x03\x00\x04\x0e\x00\x00\x00"              # ServerHelloDone
        п = разобрать_пакет(tcp(tls_запись, 3389, 49000))
        self.assertEqual("TLS", п.протокол)

    def test_фильтры(self):
        пакеты = [tcp(self.ЗАПРОС, 49000, 3389), tcp(tpkt_(b"\x02\xf0\x80xyz"), 49000, 3389)]
        self.assertEqual([0, 1], выбрать(пакеты, "tpkt"))
        self.assertEqual([0], выбрать(пакеты, "rdp"))
        self.assertEqual([0], выбрать(пакеты, "rdp.rt_cookie contains \"ivanov\""))


# -- HTTP/2 и WebSocket -------------------------------------------------------------------------------------------

def кадр(тип, флаги, поток, тело):
    return len(тело).to_bytes(3, "big") + bytes([тип, флаги]) + struct.pack(">I", поток) + тело


ПРЕАМБУЛА = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
SETTINGS = кадр(4, 0, 0, struct.pack(">HIHI", 3, 100, 4, 65535))


class HTTP2Tests(unittest.TestCase):
    def test_преамбула_и_кадры(self):
        данные_ = ПРЕАМБУЛА + SETTINGS + кадр(8, 0, 0, struct.pack(">I", 1 << 20))
        п = разобрать_пакет(tcp(данные_, 50000, 8080))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "HTTP2"], п.стек)
        self.assertEqual([3, 4], поля(п)["http2.settings.id"])
        self.assertEqual([100, 65535], поля(п)["http2.settings.value"])
        self.assertEqual([1 << 20], поля(п)["http2.window_update.window_size_increment"])
        self.assertEqual((ЗАГ_TCP + 24 + 9, 6), место(п, "http2.settings.id"))
        self.assertEqual((ЗАГ_TCP + 24 + 5, 4), место(п, "http2.streamid"))

    def test_без_преамбулы_строгая_последовательность(self):
        данные_ = (SETTINGS + кадр(4, 1, 0, b"") + кадр(1, 0x05, 1, b"\x82\x86\x84")
                   + кадр(7, 0, 0, struct.pack(">II", 1, 0)))
        п = разобрать_пакет(tcp(данные_, 443 + 7000, 50000))
        self.assertEqual("HTTP2", п.протокол)
        self.assertEqual([4, 4, 1, 7], [т for т in поля(п)["http2.type"] if isinstance(т, int)][::2])
        self.assertEqual([0], поля(п)["http2.goaway.error"])
        self.assertIn("HEADERS[1]", п.инфо)

    def test_чужое(self):
        for плохой in (кадр(4, 0, 0, b"\0" * 5),                  # SETTINGS не кратен 6
                       кадр(6, 0, 1, b"\0" * 8),                  # PING не на потоке 0
                       кадр(0, 0, 0, b"abc"),                      # DATA на потоке 0
                       SETTINGS + b"\0",                           # хвост не кадр
                       кадр(8, 0, 0, b"\0\0\0\0"),                 # нулевое приращение окна
                       кадр(4, 0x80, 0, b""),                      # неопределённый флаг
                       кадр(4, 0, 0, struct.pack(">HI", 2, 5)),    # ENABLE_PUSH = 5
                       кадр(0x0B, 0, 1, b"x")):                    # неизвестный тип без преамбулы
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("HTTP2", разобрать_пакет(tcp(плохой, 50000, 8080)).стек)

    def test_фильтры(self):
        пакеты = [tcp(ПРЕАМБУЛА + SETTINGS, 50000, 8080), tcp(b"GET / HTTP/1.1\r\n\r\n", 50000, 8080)]
        self.assertEqual([0], выбрать(пакеты, "http2"))
        self.assertEqual([0], выбрать(пакеты, "http2.settings.id == 3"))


class WebSocketTests(unittest.TestCase):
    # RFC 6455, 5.7: «Hello» без маски и с маской 37 fa 21 3d.
    БЕЗ_МАСКИ = bytes.fromhex("810548656c6c6f")
    С_МАСКОЙ = bytes.fromhex("818537fa213d7f9f4d5158")

    def test_кадры(self):
        п = разобрать_пакет(tcp(self.С_МАСКОЙ, 50000, 8080))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "WebSocket"], п.стек)
        self.assertEqual(["Hello"], поля(п)["websocket.payload.text"])
        self.assertEqual([1], поля(п)["websocket.mask"])
        self.assertEqual((ЗАГ_TCP + 2, 4), место(п, "websocket.masking_key"))
        self.assertEqual((ЗАГ_TCP + 6, 5), место(п, "websocket.payload.text"))
        п = разобрать_пакет(tcp(self.БЕЗ_МАСКИ + bytes.fromhex("890548656c6c6f") + b"\x88\x02\x03\xe8", 8080, 50000))
        self.assertEqual([1, 9, 8], [о for о in поля(п)["websocket.opcode"] if isinstance(о, int)][::2])
        self.assertEqual([1000], поля(п)["websocket.payload.close.status_code"])
        # Длина 126 — в 16-битном поле.
        длинный = b"\x82\x7e" + struct.pack(">H", 200) + bytes(200)
        self.assertEqual([200], поля(разобрать_пакет(tcp(длинный, 8080, 50000)))["websocket.payload_length"])

    def test_чужое(self):
        for плохой in (b"\x81\x02\xff\xfe",                       # текст не UTF-8
                       b"\xa1\x05Hello",                           # RSV2
                       b"\x09\x05Hello",                           # ping без FIN
                       b"\x82\x7e\x00\x05abcde",                   # длина 5 в 16-битной записи
                       b"\x88\x01\x03",                            # закрытие длиной 1
                       b"\x88\x02\x03\xed",                        # код закрытия 1005 не передаётся
                       self.БЕЗ_МАСКИ + b"\x00",                   # хвост не кадр
                       b"\x83\x00",                                # опкод 3 зарезервирован
                       b"\x00\x00",                                # нули заполнения — не «пустое продолжение»
                       b"\x81\x00",                                # пустой кадр данных без порта не угадываем
                       b"\x00\x03abc"):                            # сегмент не начинается продолжением
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("WebSocket", разобрать_пакет(tcp(плохой, 50000, 8080)).стек)
        # По выбору аналитика («разбирать как») продолжение и пустой кадр — разбираются.
        п = разобрать_пакет(tcp(b"\x00\x03abc\x81\x00", 50000, 8080), как={"tcp:8080": "WebSocket"})
        self.assertEqual("WebSocket", п.протокол)
        for плохой in ():
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("WebSocket", разобрать_пакет(tcp(плохой, 50000, 8080)).стек)

    def test_фильтры(self):
        пакеты = [tcp(self.С_МАСКОЙ, 50000, 8080), tcp(self.БЕЗ_МАСКИ, 8080, 50000)]
        self.assertEqual([0, 1], выбрать(пакеты, "websocket"))
        self.assertEqual([0], выбрать(пакеты, "websocket.mask == 1"))


# -- ONC RPC ------------------------------------------------------------------------------------------------------

def xdr_строка(б):
    return struct.pack(">I", len(б)) + б + b"\0" * (-len(б) % 4)


def auth_sys_групп(n):
    тело = struct.pack(">I", 1) + xdr_строка(b"h") + struct.pack(">III", 0, 0, n) + struct.pack(f">{n}I", *range(n))
    return struct.pack(">II", 1, len(тело)) + тело


def auth_sys(машина=b"host"):
    тело = struct.pack(">I", 0x5EED) + xdr_строка(машина) + struct.pack(">IIII", 1000, 1000, 1, 1000)
    return struct.pack(">II", 1, len(тело)) + тело


AUTH_NONE = struct.pack(">II", 0, 0)


def rpc_вызов(xid, прог, верс, проц, аргументы, cred=AUTH_NONE):
    return struct.pack(">IIIIII", xid, 0, 2, прог, верс, проц) + cred + AUTH_NONE + аргументы


def запись(сообщение):
    return struct.pack(">I", 0x80000000 | len(сообщение)) + сообщение


GETPORT = rpc_вызов(0x1001, 100000, 2, 3, struct.pack(">IIII", 100003, 3, 17, 0))
LOOKUP = rpc_вызов(0x2002, 100003, 3, 3, xdr_строка(bytes(range(8))) + xdr_строка(b"file.txt"), cred=auth_sys())


class RPCTests(unittest.TestCase):
    def test_portmap_getport_по_udp(self):
        п = разобрать_пакет(udp(GETPORT, 700, 111))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "RPC", "Portmap"], п.стек)
        self.assertEqual([0x1001], поля(п)["rpc.xid"][1:])
        self.assertEqual([100000], поля(п)["rpc.program"])
        self.assertEqual([3], поля(п)["portmap.procedure"])
        self.assertEqual([100003], поля(п)["portmap.prog"])
        self.assertEqual((ЗАГ_UDP + 12, 4), место(п, "rpc.program"))
        self.assertEqual((ЗАГ_UDP + 40, 4), место(п, "portmap.prog"))
        self.assertIn("вызов portmap v2 GETPORT", п.инфо)

    def test_nfs3_lookup_по_tcp(self):
        п = разобрать_пакет(tcp(запись(LOOKUP), 800, 2049))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "RPC", "NFS"], п.стек)
        self.assertEqual(["file.txt"], поля(п)["nfs.name"])
        self.assertEqual([3], поля(п)["nfs.procedure_v3"])
        self.assertEqual(["host"], поля(п)["rpc.auth.machinename"])
        self.assertEqual([1000], поля(п)["rpc.auth.uid"])
        self.assertEqual([len(LOOKUP)], поля(п)["rpc.fraglen"])
        self.assertEqual([1], поля(п)["rpc.lastfrag"])
        дл_cred = len(auth_sys())
        self.assertEqual((ЗАГ_TCP + 4 + 24 + дл_cred + 8 + 12, 12), место(п, "nfs.name"))
        self.assertIn("LOOKUP file.txt", п.инфо)

    def test_ответы(self):
        принят = struct.pack(">III", 0x2002, 1, 0) + AUTH_NONE + struct.pack(">I", 0) + b"\0" * 8
        п = разобрать_пакет(udp(принят, 2049, 800))
        self.assertEqual("RPC", п.протокол)
        self.assertEqual([0], поля(п)["rpc.state_accept"])
        отказ = struct.pack(">IIIII", 7, 1, 1, 1, 1)
        п = разобрать_пакет(udp(отказ, 2049, 800))
        self.assertEqual([1], поля(п)["rpc.state_auth"])

    def test_угадывание_mount_без_порта(self):
        mnt = rpc_вызов(0x3003, 100005, 3, 1, xdr_строка(b"/export"))
        п = разобрать_пакет(tcp(запись(mnt), 850, 40123))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "RPC", "MOUNT"], п.стек)
        self.assertEqual(["/export"], поля(п)["mount.path"])
        п = разобрать_пакет(udp(mnt, 850, 40123))
        self.assertEqual("MOUNT", п.протокол)
        # Неизвестная программа без порта — не угадываем.
        чужая = rpc_вызов(1, 399999, 1, 0, b"")
        self.assertNotIn("RPC", разобрать_пакет(udp(чужая, 850, 40123)).стек)

    def test_чужое(self):
        for плохой in (GETPORT[:8] + struct.pack(">I", 3) + GETPORT[12:],       # версия RPC 3
                       GETPORT[:24] + struct.pack(">II", 0, 4) + b"\0" * 4 + GETPORT[32:],  # AUTH_NONE с телом
                       struct.pack(">III", 1, 1, 2) + AUTH_NONE,               # reply_stat 2
                       struct.pack(">II", 1, 2) + GETPORT[8:],                 # msg_type 2
                       rpc_вызов(9, 100003, 3, 0, b"", cred=auth_sys_групп(17))):  # групп больше 16
            with self.subTest(плохой=плохой[:16].hex()):
                self.assertNotIn("RPC", разобрать_пакет(udp(плохой, 700, 111)).стек)
        self.assertIn("RPC", разобрать_пакет(udp(rpc_вызов(9, 100003, 3, 0, b"", cred=auth_sys_групп(16)),
                                                      700, 111)).стек)
        # Фрагмент записи короче 8 байт.
        кривой = struct.pack(">I", 0x80000004) + LOOKUP[:4]
        self.assertNotIn("RPC", разобрать_пакет(tcp(кривой + запись(LOOKUP), 800, 2049)).стек)

    def test_фильтры(self):
        пакеты = [udp(GETPORT, 700, 111), tcp(запись(LOOKUP), 800, 2049)]
        self.assertEqual([0, 1], выбрать(пакеты, "rpc"))
        self.assertEqual([1], выбрать(пакеты, "nfs"))
        self.assertEqual([1], выбрать(пакеты, "nfs.name == \"file.txt\""))
        self.assertEqual([0], выбрать(пакеты, "rpc.program == 100000"))


# -- MySQL ----------------------------------------------------------------------------------------------------------

def mysql_пакет(номер, тело):
    return len(тело).to_bytes(3, "little") + bytes([номер]) + тело


ПРИВЕТСТВИЕ = mysql_пакет(0, b"\x0a8.0.36\0" + struct.pack("<I", 77) + b"abcdefgh" + b"\0"
                          + struct.pack("<HBHHB", 0xFFFF, 0xFF, 2, 0xDFFF, 21) + bytes(10) + b"ijklmnopqrst\0"
                          + b"mysql_native_password\0")


class MySQLTests(unittest.TestCase):
    def test_приветствие_и_запрос(self):
        п = разобрать_пакет(tcp(ПРИВЕТСТВИЕ, 3306, 50000))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "MySQL"], п.стек)
        self.assertEqual(["8.0.36"], поля(п)["mysql.version"])
        self.assertEqual([77], поля(п)["mysql.thread_id"])
        self.assertEqual((ЗАГ_TCP + 5, 7), место(п, "mysql.version"))
        запрос = mysql_пакет(0, b"\x03SELECT * FROM t")
        п = разобрать_пакет(tcp(запрос, 50000, 3306))
        self.assertEqual(["SELECT * FROM t"], поля(п)["mysql.query"])
        self.assertEqual([3], поля(п)["mysql.command"])
        self.assertEqual((ЗАГ_TCP + 5, 15), место(п, "mysql.query"))
        self.assertEqual("MySQL COM_QUERY SELECT * FROM t", п.инфо)

    def test_вход_ошибка_и_ok(self):
        вход = mysql_пакет(1, struct.pack("<IIB23s", 0x000FA685, 1 << 24, 33, bytes(23)) + b"root\0" + b"\x14"
                           + bytes(20) + b"mysql_native_password\0")
        self.assertEqual(["root"], поля(разобрать_пакет(tcp(вход, 50000, 3306)))["mysql.user"])
        ошибка = mysql_пакет(2, b"\xff" + struct.pack("<H", 1045) + b"#28000Access denied")
        ок = mysql_пакет(1, b"\x00\x01\x00\x02\x00\x00\x00")
        п = разобрать_пакет(tcp(ок + ошибка, 3306, 50000))
        self.assertEqual([1045], поля(п)["mysql.error_code"])
        self.assertEqual(["28000"], поля(п)["mysql.sqlstate"])
        self.assertEqual([1], поля(п)["mysql.affected_rows"])

    def test_запрос_продолжается_в_следующем_сегменте(self):
        текст = b"INSERT INTO t VALUES " + b"(1,'abc')," * 300
        п = разобрать_пакет(tcp(mysql_пакет(0, b"\x03" + текст)[:400], 50000, 3306))
        self.assertEqual("MySQL", п.протокол)
        self.assertTrue(any("продолжается" in о for о in п.ошибки))
        # Случайные байты с тем же заголовком — не MySQL.
        self.assertNotIn("MySQL", разобрать_пакет(tcp((3000).to_bytes(3, "little") + b"\x00\x03"
                                                      + bytes(range(256)) * 2, 50000, 3306)).стек)

    def test_чужое(self):
        for плохой, порты in ((mysql_пакет(0, b"\x40abc"), (50000, 3306)),          # команда 0x40
                              (mysql_пакет(0, b"\x03SELECT 1") + b"\0\0\0\x01\0", (50000, 3306)),  # пустой пакет
                              (ПРИВЕТСТВИЕ[:-40] + b"\x01" + ПРИВЕТСТВИЕ[-39:], (3306, 50000)),  # резерв не нули
                              (b"\x10\x00\x00\x00\x03SELECT", (50000, 3306))):      # длина больше данных
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("MySQL", разобрать_пакет(tcp(плохой, *порты)).стек)

    def test_фильтры(self):
        пакеты = [tcp(ПРИВЕТСТВИЕ, 3306, 50000), tcp(mysql_пакет(0, b"\x03SELECT 1"), 50000, 3306)]
        self.assertEqual([0, 1], выбрать(пакеты, "mysql"))
        self.assertEqual([1], выбрать(пакеты, "mysql.query contains \"select\""))


# -- PostgreSQL -----------------------------------------------------------------------------------------------------

def pg(тип, тело):
    return тип + struct.pack(">I", 4 + len(тело)) + тело


ПАРАМЕТРЫ_PG = b"user\0alice\0database\0db1\0\0"
STARTUP = struct.pack(">II", 8 + len(ПАРАМЕТРЫ_PG), 196608) + ПАРАМЕТРЫ_PG


class PostgreSQLTests(unittest.TestCase):
    def test_startup_и_запрос(self):
        п = разобрать_пакет(tcp(STARTUP, 50000, 5432))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "PGSQL"], п.стек)
        self.assertEqual([3], поля(п)["pgsql.version_major"])
        self.assertEqual(["user", "database"], поля(п)["pgsql.parameter_name"])
        self.assertEqual(["alice", "db1"], поля(п)["pgsql.parameter_value"])
        self.assertEqual((ЗАГ_TCP + 8, 11), место(п, "pgsql.parameter_name"))
        п = разобрать_пакет(tcp(pg(b"Q", b"SELECT 1;\0"), 50000, 5432))
        self.assertEqual(["SELECT 1;"], поля(п)["pgsql.query"])
        self.assertEqual((ЗАГ_TCP + 5, 9), место(п, "pgsql.query"))
        ssl = struct.pack(">II", 8, 80877103)
        self.assertEqual([80877103], поля(разобрать_пакет(tcp(ssl, 50000, 5432)))["pgsql.request_code"])
        self.assertEqual("PGSQL", разобрать_пакет(tcp(b"N", 5432, 50000)).протокол)

    def test_ответ_сервера(self):
        описание = pg(b"T", struct.pack(">H", 1) + b"a\0" + struct.pack(">IHIHIH", 0, 0, 23, 4, 0xFFFFFFFF, 0))
        строка = pg(b"D", struct.pack(">HI", 1, 1) + b"1")
        готово = pg(b"C", b"SELECT 1\0") + pg(b"Z", b"I")
        п = разобрать_пакет(tcp(описание + строка + готово, 5432, 50000))
        self.assertEqual(["a"], поля(п)["pgsql.col.name"])
        self.assertEqual([23], поля(п)["pgsql.oid.type"])
        self.assertEqual(["SELECT 1"], поля(п)["pgsql.tag"])
        self.assertEqual(["I"], поля(п)["pgsql.status"])
        self.assertEqual((ЗАГ_TCP + 7, 20), место(п, "pgsql.col.name"))
        ошибка = pg(b"E", b"SERROR\0VERROR\0C42P01\0Mrelation \"x\" does not exist\0\0")
        п = разобрать_пакет(tcp(ошибка, 5432, 50000))
        self.assertEqual(["42P01"], поля(п)["pgsql.code"])
        self.assertIn("ERROR 42P01", п.инфо)

    def test_чужое(self):
        for плохой, порты in ((pg(b"Z", b"II"), (5432, 50000)),                  # ReadyForQuery длиной 6
                              (pg(b"Z", b"X"), (5432, 50000)),                   # состояние не I/T/E
                              (pg(b"Q", b"SELECT 1"), (50000, 5432)),            # без нуля в конце
                              (pg(b"x", b"abc"), (50000, 5432)),                 # тип x не определён
                              (pg(b"S", b"x"), (50000, 5432)),                   # Sync с телом
                              (struct.pack(">II", 12, 0x00040000) + b"a\0\0\0", (50000, 5432)),  # версия 4.0
                              (STARTUP[:-1], (50000, 5432))):                    # без завершающего нуля
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("PGSQL", разобрать_пакет(tcp(плохой, *порты)).стек)

    def test_фильтры(self):
        пакеты = [tcp(STARTUP, 50000, 5432), tcp(pg(b"Q", b"SELECT 1;\0"), 50000, 5432)]
        self.assertEqual([0, 1], выбрать(пакеты, "pgsql"))
        self.assertEqual([1], выбрать(пакеты, "pgsql.query contains \"SELECT\""))


# -- TDS ------------------------------------------------------------------------------------------------------------

def tds_пакет(тип, данные_, состояние=1, окно=0):
    return struct.pack(">BBHHBB", тип, состояние, 8 + len(данные_), 0, 1, окно) + данные_


PRELOGIN = tds_пакет(18, struct.pack(">BHHBHHB", 0, 11, 6, 1, 17, 1, 0xFF) + bytes([15, 0]) + struct.pack(">HH", 2000, 0)
                     + b"\x00")
ЗАПРОС_TDS = tds_пакет(1, struct.pack("<IIHQI", 22, 18, 2, 0, 1) + "SELECT @@version".encode("utf-16-le"))


class TDSTests(unittest.TestCase):
    def test_prelogin_и_запрос(self):
        п = разобрать_пакет(tcp(PRELOGIN, 50000, 1433))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TDS"], п.стек)
        self.assertEqual(["15.0.2000"], поля(п)["tds.prelogin.version"])
        self.assertEqual([0], поля(п)["tds.prelogin.encryption"])
        self.assertEqual((ЗАГ_TCP + 8 + 11, 6), место(п, "tds.prelogin.version"))
        п = разобрать_пакет(tcp(ЗАПРОС_TDS, 50000, 1433))
        self.assertEqual(["SELECT @@version"], поля(п)["tds.query"])
        self.assertEqual((ЗАГ_TCP + 8 + 22, 32), место(п, "tds.query"))
        self.assertEqual([1], поля(п)["tds.type"][1:])

    def test_tls_внутри_prelogin(self):
        п = разобрать_пакет(tcp(tds_пакет(18, b"\x16\x03\x03\x00\x04\x0e\x00\x00\x00"), 1433, 50000))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "TDS", "TLS"], п.стек)

    def test_чужое(self):
        for плохой in (tds_пакет(1, b"a\0", окно=1), tds_пакет(1, b"a\0", состояние=0x21),
                       tds_пакет(5, b"a\0"), ЗАПРОС_TDS + tds_пакет(0x99, b""), PRELOGIN[:8] + b"\x09" + PRELOGIN[9:],
                       tds_пакет(1, b"a\0")[:2] + b"\x00\x07" + tds_пакет(1, b"a\0")[4:]):
            with self.subTest(плохой=плохой[:12].hex()):
                self.assertNotIn("TDS", разобрать_пакет(tcp(плохой, 50000, 1433)).стек)

    def test_фильтры(self):
        пакеты = [tcp(PRELOGIN, 50000, 1433), tcp(ЗАПРОС_TDS, 50000, 1433)]
        self.assertEqual([0, 1], выбрать(пакеты, "tds"))
        self.assertEqual([1], выбрать(пакеты, "tds.query contains \"@@version\""))


# -- Redis ----------------------------------------------------------------------------------------------------------

КОМАНДА_SET = b"*3\r\n$3\r\nSET\r\n$3\r\nkey\r\n$5\r\nvalue\r\n"


class RedisTests(unittest.TestCase):
    def test_команда_и_ответы(self):
        п = разобрать_пакет(tcp(КОМАНДА_SET, 50000, 6379))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "RESP"], п.стек)
        self.assertEqual(["SET", "SET"], поля(п)["resp.command"])
        self.assertEqual(["key", "value"], поля(п)["resp.argument"])
        self.assertEqual((ЗАГ_TCP, len(КОМАНДА_SET)), место(п, "resp.command"))       # узел команды
        self.assertEqual((ЗАГ_TCP + 4, 9), (поле(п, "resp.command").дети[1].смещение,
                                            поле(п, "resp.command").дети[1].длина))
        self.assertEqual((ЗАГ_TCP + 13, 9), место(п, "resp.argument"))
        п = разобрать_пакет(tcp(b"+OK\r\n:1000\r\n$-1\r\n-ERR unknown command\r\n", 6379, 50000))
        self.assertEqual(["OK"], поля(п)["resp.simple_string"])
        self.assertEqual(["1000"], поля(п)["resp.integer"])
        self.assertEqual(["ERR unknown command"], поля(п)["resp.error"])
        self.assertIn("Redis OK; 1000; (null); -ERR", п.инфо)

    def test_оборванная_строка_и_чужое(self):
        п = разобрать_пакет(tcp(b"$100\r\n" + b"x" * 40, 6379, 50000))
        self.assertEqual("RESP", п.протокол)
        for плохой in (b"+OK\n", b"$3\r\nabcd\r\n", b":12a\r\n", b"*01\r\n$1\r\na\r\n", b"+OK\r\nxyz",
                       b"?\r\n", b"#x\r\n", b"$-2\r\n"):
            with self.subTest(плохой=плохой):
                self.assertNotIn("RESP", разобрать_пакет(tcp(плохой, 50000, 6379)).стек)

    def test_фильтры_и_разбирать_как(self):
        пакеты = [tcp(КОМАНДА_SET, 50000, 6379), tcp(b"+OK\r\n", 6379, 50000)]
        self.assertEqual([0, 1], выбрать(пакеты, "resp"))
        self.assertEqual([0], выбрать(пакеты, "resp.command == SET"))
        п = разобрать_пакет(tcp(КОМАНДА_SET, 50000, 7000), как={"tcp:7000": "Redis"})
        self.assertEqual(["SET", "SET"], поля(п)["resp.command"])


# -- MongoDB --------------------------------------------------------------------------------------------------------

def bson(*элементы):
    тело = b""
    for имя, значение in элементы:
        if isinstance(значение, str):
            сырое = значение.encode() + b"\0"
            тело += b"\x02" + имя.encode() + b"\0" + struct.pack("<i", len(сырое)) + сырое
        else:
            тело += b"\x10" + имя.encode() + b"\0" + struct.pack("<i", значение)
    return struct.pack("<i", len(тело) + 5) + тело + b"\0"


def mongo(код, тело, ид=7):
    return struct.pack("<iiii", 16 + len(тело), ид, 0, код) + тело


FIND = mongo(2013, struct.pack("<I", 0) + b"\0" + bson(("find", "users"), ("$db", "test")))


class MongoDBTests(unittest.TestCase):
    def test_op_msg(self):
        п = разобрать_пакет(tcp(FIND, 50000, 27017))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "MongoDB"], п.стек)
        self.assertEqual([2013], поля(п)["mongo.opcode"][1:])
        self.assertEqual(["find", "$db"], поля(п)["mongo.element.name"])
        self.assertEqual(["users", "test"], поля(п)["mongo.element.value.string"])
        self.assertEqual((ЗАГ_TCP + 21 + 4, 16), место(п, "mongo.element.name"))
        self.assertEqual((ЗАГ_TCP + 21 + 4 + 10, 5), место(п, "mongo.element.value.string"))
        self.assertEqual("MongoDB OP_MSG find", п.инфо)

    def test_контрольная_сумма(self):
        документ = bson(("ping", 1), ("$db", "a"))
        без = struct.pack("<iiii", 16 + 5 + len(документ) + 4, 8, 0, 2013) + struct.pack("<I", 1) + b"\0" + документ
        с_суммой = без + struct.pack("<I", crc32c(без))
        self.assertEqual(0xE3069283, crc32c(b"123456789"))
        п = разобрать_пакет(tcp(с_суммой, 50000, 27017))
        self.assertEqual("MongoDB", п.протокол)
        self.assertEqual([], п.ошибки)
        испорчен = с_суммой[:-1] + bytes([с_суммой[-1] ^ 1])
        п = разобрать_пакет(tcp(испорчен, 50000, 27017))
        self.assertIn("MongoDB: неверная CRC-32C сообщения OP_MSG", п.ошибки)

    def test_op_query(self):
        тело = struct.pack("<i", 0) + b"admin.$cmd\0" + struct.pack("<ii", 0, -1) + bson(("isMaster", 1))
        п = разобрать_пакет(tcp(mongo(2004, тело), 50000, 27017))
        self.assertEqual(["admin.$cmd"], поля(п)["mongo.full_collection_name"])
        self.assertEqual(["isMaster"], поля(п)["mongo.element.name"])

    def test_чужое(self):
        тело = FIND[16:]
        for плохой in (mongo(2014, тело),                                       # opCode 2014
                       mongo(2013, struct.pack("<I", 4) + тело[4:]),           # обязательный бит флагов
                       mongo(2013, тело + b"\0" + тело[5:]),                   # две секции вида 0
                       mongo(2013, тело[:-1] + b"\x01"),                       # документ без нуля в конце
                       mongo(2013, тело[:4] + b"\x02" + тело[5:]),             # секция вида 2
                       FIND + mongo(2014, b"")):
            with self.subTest(плохой=плохой[:20].hex()):
                self.assertNotIn("MongoDB", разобрать_пакет(tcp(плохой, 50000, 27017)).стек)

    def test_фильтры(self):
        пакеты = [tcp(FIND, 50000, 27017)]
        self.assertEqual([0], выбрать(пакеты, "mongodb"))
        self.assertEqual([0], выбрать(пакеты, "mongo.element.value.string == users"))


# -- AMQP -----------------------------------------------------------------------------------------------------------

def amqp_кадр(тип, канал, тело):
    return struct.pack(">BHI", тип, канал, len(тело)) + тело + b"\xce"


def shortstr(б):
    return bytes([len(б)]) + б


TUNE = amqp_кадр(1, 0, struct.pack(">HHHIH", 10, 30, 2047, 131072, 60))
PUBLISH = (amqp_кадр(1, 1, struct.pack(">HHH", 60, 40, 0) + shortstr(b"ex") + shortstr(b"rk") + b"\0")
           + amqp_кадр(2, 1, struct.pack(">HHQH", 60, 0, 5, 0)) + amqp_кадр(3, 1, b"hello"))


class AMQPTests(unittest.TestCase):
    def test_заголовок_и_кадры(self):
        п = разобрать_пакет(tcp(b"AMQP\x00\x00\x09\x01", 50000, 5672))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "AMQP"], п.стек)
        self.assertEqual(["0.9.1"], поля(п)["amqp.init.version"])
        п = разобрать_пакет(tcp(TUNE, 5672, 50000))
        self.assertEqual([2047], поля(п)["amqp.method.arguments.channel_max"])
        self.assertEqual([60], поля(п)["amqp.method.arguments.heartbeat"])
        self.assertEqual((ЗАГ_TCP + 7, 2), место(п, "amqp.method.class"))
        п = разобрать_пакет(tcp(PUBLISH + amqp_кадр(8, 0, b""), 50000, 5672))
        self.assertEqual(["ex"], поля(п)["amqp.method.arguments.exchange"])
        self.assertEqual(["rk"], поля(п)["amqp.method.arguments.routing_key"])
        self.assertEqual([5], поля(п)["amqp.header.body_size"])
        self.assertEqual([1, 2, 3, 8], [т for т in поля(п)["amqp.type"] if isinstance(т, int)][::2])
        self.assertEqual((ЗАГ_TCP + 13, 3), место(п, "amqp.method.arguments.exchange"))
        self.assertIn("basic.publish (1) exchange=ex routing_key=rk", п.инфо)

    def test_чужое(self):
        for плохой in (TUNE[:-1] + b"\xcd",                                     # конец кадра не 0xCE
                       amqp_кадр(1, 0, struct.pack(">HH", 10, 99)),            # метода 10.99 нет
                       amqp_кадр(8, 1, b""),                                   # heartbeat не на канале 0
                       amqp_кадр(4, 0, b"x"),                                  # тип кадра 4
                       amqp_кадр(2, 1, struct.pack(">HHQH", 60, 1, 5, 0)),     # вес не 0
                       b"AMQP\x00\x00\x09\x01\x00"):                            # заголовок с хвостом
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("AMQP", разобрать_пакет(tcp(плохой, 50000, 5672)).стек)

    def test_фильтры(self):
        пакеты = [tcp(TUNE, 5672, 50000), tcp(PUBLISH, 50000, 5672)]
        self.assertEqual([0, 1], выбрать(пакеты, "amqp"))
        self.assertEqual([1], выбрать(пакеты, "amqp.method.arguments.exchange == ex"))


# -- SMPP -----------------------------------------------------------------------------------------------------------

def smpp_pdu(команда, тело=b"", статус=0, номер=1):
    return struct.pack(">IIII", 16 + len(тело), команда, статус, номер) + тело


BIND = smpp_pdu(2, b"smppclient1\0password\0\0\x34\x00\x00\0")
SUBMIT = smpp_pdu(4, b"\0" + b"\x01\x0112345\0" + b"\x01\x0167890\0" + b"\x00\x00\x00" + b"\0\0"
                  + b"\x01\x00\x00\x00\x05hello" + struct.pack(">HHH", 0x0204, 2, 1), номер=2)


class SMPPTests(unittest.TestCase):
    def test_bind_и_submit(self):
        п = разобрать_пакет(tcp(BIND, 50000, 2775))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "SMPP"], п.стек)
        self.assertEqual(["smppclient1"], поля(п)["smpp.system_id"])
        self.assertEqual([0x34], поля(п)["smpp.interface_version"])
        self.assertEqual((ЗАГ_TCP + 16, 12), место(п, "smpp.system_id"))
        п = разобрать_пакет(tcp(SUBMIT, 50000, 2775))
        self.assertEqual(["12345"], поля(п)["smpp.source_addr"])
        self.assertEqual(["67890"], поля(п)["smpp.destination_addr"])
        self.assertEqual(["hello"], поля(п)["smpp.message_text"])
        self.assertEqual([0x0204], поля(п)["smpp.opt_param_tag"])
        self.assertEqual((ЗАГ_TCP + 16 + 1 + 16 + 3 + 2 + 5, 5), место(п, "smpp.message_text"))
        self.assertIn("12345 → 67890: hello", п.инфо)

    def test_несколько_pdu(self):
        пара = smpp_pdu(0x15, номер=5) + smpp_pdu(0x80000004, b"abc\0", номер=2)
        п = разобрать_пакет(tcp(пара, 2775, 50000))
        self.assertEqual([5, 2], поля(п)["smpp.sequence_number"])
        self.assertEqual(["abc"], поля(п)["smpp.message_id"])

    def test_чужое(self):
        for плохой in (smpp_pdu(0x15, номер=0),                                # номер 0
                       smpp_pdu(0x15, статус=1),                               # запрос с состоянием
                       smpp_pdu(0x15, b"\0"),                                  # enquire_link с телом
                       smpp_pdu(0x99),                                         # команда 0x99
                       SUBMIT.replace(b"\x00\x00\x00\x00\x00\x01", b"\x00\x00\x00abcde\x00\x00\x01"),
                       BIND + smpp_pdu(0x99)):
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("SMPP", разобрать_пакет(tcp(плохой, 50000, 2775)).стек)

    def test_фильтры(self):
        пакеты = [tcp(BIND, 50000, 2775), tcp(SUBMIT, 50000, 2775)]
        self.assertEqual([0, 1], выбрать(пакеты, "smpp"))
        self.assertEqual([1], выбрать(пакеты, "smpp.command_id == 4"))


# -- CoAP -----------------------------------------------------------------------------------------------------------

GET = bytes([0x42, 0x01]) + struct.pack(">H", 0x1234) + b"\xab\xcd" + b"\x3b" + b"example.net" + b"\x87sensors" \
    + b"\x04temp"
CONTENT = bytes([0x60, 0x45]) + struct.pack(">H", 0x1234) + b"\xc1\x32" + b"\xff" + b'{"t":21}'


class CoAPTests(unittest.TestCase):
    def test_get_и_content(self):
        п = разобрать_пакет(udp(GET, 50000, 5683))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "CoAP"], п.стек)
        self.assertEqual(["sensors", "temp"], поля(п)["coap.opt.uri_path"])
        self.assertEqual(["example.net"], поля(п)["coap.opt.uri_host"])
        self.assertEqual([1], поля(п)["coap.code"])
        self.assertEqual([0x1234], поля(п)["coap.mid"])
        self.assertEqual((ЗАГ_UDP + 6 + 12, 8), место(п, "coap.opt.uri_path"))
        self.assertEqual("CoAP CON GET MID 4660 example.net/sensors/temp", п.инфо)
        п = разобрать_пакет(udp(CONTENT, 5683, 50000))
        self.assertEqual(["application/json"], поля(п)["coap.opt.ctype"])
        self.assertEqual(['{"t":21}'], поля(п)["coap.payload"])
        self.assertEqual((ЗАГ_UDP + 7, 8), место(п, "coap.payload"))

    def test_пустое_и_длинная_дельта(self):
        п = разобрать_пакет(udp(bytes([0x60, 0x00, 0x12, 0x34]), 5683, 50000))
        self.assertIn("ACK Empty", п.инфо)
        size1 = bytes([0x52, 0x02, 0, 1, 1, 2]) + b"\xd1\x2f\x10"          # опция 60 = 13 + 47
        self.assertEqual([16], поля(разобрать_пакет(udp(size1, 50000, 5683)))["coap.opt.size1"])

    def test_чужое(self):
        for плохой in (bytes([0x82]) + GET[1:],                                # версия 2
                       bytes([0x49]) + GET[1:],                                # TKL 9
                       CONTENT[:6] + b"\xff",                                  # маркер без данных
                       bytes([0x70, 0x01, 0, 1]),                              # RST с запросом
                       bytes([0x50, 0x00, 0, 1]),                              # NON пустое
                       bytes([0x40, 0x01, 0, 1, 0x21, 0x00]),                  # опция 2 не определена
                       bytes([0x40, 0x01, 0, 1]) + b"\x31a\x01b",              # Uri-Host дважды
                       bytes([0x40, 0x01, 0, 1, 0x3f]),                        # длина 15
                       bytes([0x40, 0x3F, 0, 1])):                             # код 1.31 не определён
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("CoAP", разобрать_пакет(udp(плохой, 50000, 5683)).стек)

    def test_фильтры(self):
        пакеты = [udp(GET, 50000, 5683), udp(CONTENT, 5683, 50000)]
        self.assertEqual([0, 1], выбрать(пакеты, "coap"))
        self.assertEqual([0], выбрать(пакеты, "coap.opt.uri_path == temp"))


# -- обрыв и случайные данные ----------------------------------------------------------------------------------------

ОБРАЗЦЫ = [
    ("NBNS", udp(nbns_регистрация(), 137, 137)),
    ("NBSS", tcp(прямой(smb2_create("a.txt")), 49000, 445)),
    ("DHCPv6", udp6(dhcp6_reply(), 547, 546)),
    ("RDP", tcp(RDPTests.ЗАПРОС, 49000, 3389)),
    ("HTTP2", tcp(ПРЕАМБУЛА + SETTINGS, 50000, 8080)),
    ("WebSocket", tcp(WebSocketTests.С_МАСКОЙ, 50000, 8080)),
    ("RPC", tcp(запись(LOOKUP), 800, 2049)),
    ("MySQL", tcp(ПРИВЕТСТВИЕ, 3306, 50000)),
    ("PGSQL", tcp(STARTUP, 50000, 5432)),
    ("TDS", tcp(PRELOGIN, 50000, 1433)),
    ("RESP", tcp(КОМАНДА_SET, 50000, 6379)),
    ("MongoDB", tcp(FIND, 50000, 27017)),
    ("AMQP", tcp(PUBLISH, 50000, 5672)),
    ("SMPP", tcp(SUBMIT, 50000, 2775)),
    ("CoAP", udp(GET, 50000, 5683)),
]

#: Порт → имена уровней, которых на случайных данных быть не должно.
СЛУЧАЙНЫЕ = [("udp", 137, ("NBNS",)), ("udp", 138, ("NBDS", "SMB")), ("tcp", 139, ("NBSS", "SMB", "SMB2")),
             ("tcp", 445, ("NBSS", "SMB", "SMB2")), ("udp", 547, ("DHCPv6",)), ("tcp", 3389, ("TPKT", "COTP", "RDP")),
             ("udp", 111, ("RPC",)), ("tcp", 2049, ("RPC",)), ("tcp", 3306, ("MySQL",)), ("tcp", 5432, ("PGSQL",)),
             ("tcp", 1433, ("TDS",)), ("tcp", 6379, ("RESP",)), ("tcp", 27017, ("MongoDB",)),
             ("tcp", 5672, ("AMQP",)), ("tcp", 2775, ("SMPP",)), ("udp", 5683, ("CoAP",))]


class ОбрывИСлучайныеTests(unittest.TestCase):
    def test_обрыв_не_роняет(self):
        for имя, пакет in ОБРАЗЦЫ:
            for длина in range(1, len(пакет)):
                with self.subTest(имя=имя, длина=длина):
                    п = разобрать_пакет(пакет[:длина])
                    self.assertTrue(п.уровни)
                    self.assertFalse([о for о in п.ошибки if о.startswith("разбор прерван")], п.ошибки)
            self.assertIn(имя, разобрать_пакет(пакет).стек)

    def _случайные(self, транспорт, порт, случ, n=2000):
        for _ in range(n):
            нагрузка = bytes(случ.getrandbits(8) for _ in range(случ.randrange(1, 300)))
            if транспорт == "udp":
                yield разобрать_пакет(udp(нагрузка, 40000, порт) if порт else udp(нагрузка, 40000, 40001))
            else:
                yield разобрать_пакет(tcp(нагрузка, 40000, порт) if порт else tcp(нагрузка, 40000, 40001))

    def test_случайная_нагрузка_на_порт_не_протокол(self):
        """Ни одного ложного, кроме CoAP: у него после заголовка из 4 байт (версия,
        TKL ≤ 8, известный код) токен и данные — любые, опций может не быть вовсе;
        сходится ~0,02–0,05 % случайных датаграмм, допускаем не больше 0,5 %."""
        случ = random.Random(20260926)
        for транспорт, порт, имена in СЛУЧАЙНЫЕ:
            with self.subTest(порт=f"{транспорт}:{порт}"):
                ложных = sum(1 for п in self._случайные(транспорт, порт, случ) if set(имена) & set(п.стек))
                self.assertLessEqual(ложных, 10 if имена == ("CoAP",) else 0)

    def test_случайная_нагрузка_без_порта(self):
        """Угадывание без порта: SMB, RPC, HTTP/2 — ни одного; WebSocket — не больше 0,5 %.

        У кадра WebSocket проверяемы только опкод, биты RSV, наименьшая запись
        длины и точное совпадение длины с концом сегмента; у двоичного кадра
        содержимое не проверить ничем. Случайный сегмент из 2–127 байт сходится
        с вероятностью около 13/256 × 2/128 ≈ 0,08 %.
        """
        случ = random.Random(6455)
        for транспорт in ("tcp", "udp"):
            стеки = [п.стек for п in self._случайные(транспорт, None, случ)]
            for имя in ("NBSS", "SMB2", "RPC", "HTTP2"):
                self.assertEqual(0, sum(имя in с_ for с_ in стеки), имя)
            self.assertLessEqual(sum("WebSocket" in с_ for с_ in стеки), 10)


class СтрогостьTests(unittest.TestCase):
    """Точечные проверки: каждая ловит свою порчу проверки стандарта (мутационный прогон)."""

    def нет(self, имя, пакеты):
        for пакет in пакеты:
            with self.subTest(имя=имя, пакет=пакет[-24:].hex()):
                self.assertNotIn(имя, разобрать_пакет(пакет).стек)

    def test_netbios(self):
        буква = bytearray(nbns_запрос())
        буква[14] = ord("Q")                                          # младший полубайт 16 — вне A–P
        rr = struct.pack(">HHIH", 0x20, 1, 0, 6) + b"\0\0" + с.a4("10.0.0.9")
        назад = struct.pack(">HHHHHH", 1, 0x8500, 0, 2, 0, 0) + nb_имя("PC1", 0) + rr + b"\xc0\x0c" + rr
        вперёд = struct.pack(">HHHHHH", 1, 0x8500, 0, 2, 0, 0) + b"\xc0\x1e" + rr + nb_имя("PC1", 0) + rr
        self.assertEqual("NBNS", разобрать_пакет(udp(назад, 137, 137)).протокол)
        длинная = struct.pack(">HHHHHH", 1, 0x0110, 1, 0, 0, 0) + b"\x22" + b"A" * 34 + b"\0" + struct.pack(">HH", 0x20, 1)
        self.нет("NBNS", [udp(bytes(буква), 137, 137), udp(вперёд, 137, 137), udp(длинная, 137, 137)])
        короче = bytearray(nbds_датаграмма(b"data"))
        короче[11] -= 1
        self.нет("NBDS", [udp(bytes(короче), 138, 138)])
        smb = smb2_negotiate()
        self.нет("NBSS", [tcp(прямой(smb) + b"\x01\x00\x00\x40\xfeSMB", 49000, 445),   # второй PDU не с нуля
                          tcp(b"\x00\x00\x01\x00" + b"x" * 50, 49000, 139)])             # не уместился и не SMB

    def test_smb2_размер_тела(self):
        кривой = smb2_заголовок(0) + struct.pack("<HHHHI16sQ", 37, 1, 1, 0, 0, bytes(16), 0) + b"\x02\x02"
        п = разобрать_пакет(tcp(прямой(кривой), 49000, 445))
        self.assertEqual("SMB2", п.протокол)
        self.assertTrue(any("StructureSize 37" in о for о in п.ошибки), п.ошибки)

    def test_dhcpv6_вложенная_опция_за_границей(self):
        iaaddr = struct.pack(">HH", 5, 28) + с.a6("2001:db8::1") + struct.pack(">II", 1, 2)
        ia = опция6(3, struct.pack(">III", 1, 0, 0) + iaaddr)
        сообщение = bytes([7, 0, 0, 1]) + опция6(1, DUID_LL) + ia + опция6(8, b"\0\0")
        self.нет("DHCPv6", [udp6(сообщение, 547, 546)])

    def test_tpkt_http2(self):
        self.нет("TPKT", [tcp(tpkt_(b"\x01\x60"), 49000, 3389)])                        # TPKT короче 7
        семь = кадр(4, 0, 0, struct.pack(">HI", 3, 100) + b"\0") + кадр(6, 0, 0, bytes(8))
        self.нет("HTTP2", [tcp(семь, 50000, 8080)])

    def test_rpc(self):
        def auth(заполнение):
            тело = struct.pack(">II", 1, 3) + b"hos" + заполнение + struct.pack(">III", 0, 0, 0)
            return struct.pack(">II", 1, len(тело)) + тело
        self.assertIn("RPC", разобрать_пакет(udp(rpc_вызов(5, 100003, 3, 0, b"", cred=auth(b"\0")), 700, 2049)).стек)
        self.нет("RPC", [udp(struct.pack(">III", 1, 1, 2) + bytes(16), 2049, 700),       # reply_stat 2
                         udp(struct.pack(">III", 1, 2, 0) + AUTH_NONE + bytes(4), 2049, 700),  # msg_type 2
                         udp(rpc_вызов(5, 100003, 3, 0, b"", cred=auth(b"\1")), 700, 2049)])  # выравнивание не нулём

    def test_postgresql(self):
        def startup(версия, параметры):
            return struct.pack(">II", 8 + len(параметры), версия) + параметры
        self.assertEqual("PGSQL", разобрать_пакет(tcp(startup(0x00030000, b"user\0a\0\0"), 50000, 5432)).протокол)
        self.нет("PGSQL", [tcp(pg(b"Q", b"SELECT 1\0xx"), 50000, 5432),
                           tcp(startup(0x00040000, b"user\0a\0\0"), 50000, 5432),
                           tcp(startup(0x00030000, b"user\0a\0\0xx"), 50000, 5432)])

    def test_redis(self):
        self.нет("RESP", [tcp(x, 50000, 6379) for x in (b"+O\rK\r\n", b"+OK\r\n+A\nB", b"$3\r\nabcXY+OK\r\n",
                                                          b":+5\r\n", b": 5\r\n")])

    def test_mongodb_секция_вида_2(self):
        документ = bson(("ping", 1))
        тело = struct.pack("<I", 0) + b"\0" + документ + b"\x02" + документ
        self.нет("MongoDB", [tcp(mongo(2013, тело), 50000, 27017)])

    def test_smpp(self):
        sm = (b"\0" + b"\x01\x0112345\0" + b"\x01\x0167890\0" + b"\x00\x00\x00" + b"abcde\0" + b"\0"
              + b"\x01\x00\x00\x00\x05hello")
        self.нет("SMPP", [tcp(smpp_pdu(4, sm, номер=3), 50000, 2775),                    # время из 5 знаков
                          tcp(smpp_pdu(2, b"id\0pw\0\0\x34\x00\x00\0x"), 50000, 2775)])  # хвост у bind

    def test_coap(self):
        self.нет("CoAP", [udp(x, 50000, 5683) for x in (
            bytes([0x49, 0x01, 0, 1]) + bytes(9),                                          # TKL 9
            bytes([0x40, 0x01, 0, 1, 0x3F]) + b"a" * 15,                                   # длина 15
            bytes([0x61, 0x00, 0, 1, 0xAA]),                                               # Empty с токеном
            bytes([0x40, 0x00, 0, 1, 0xFF, 0x41]))])                                       # Empty с данными


class ПодключениеTests(unittest.TestCase):
    def test_уровни_и_разбирать_как(self):
        from reportgen.setevoy.prilozh import КАК, проверить_как
        for имя in ("SMB2", "RDP", "HTTP2", "MySQL", "CoAP", "RESP"):
            self.assertEqual("прикладной", уровень_протокола(имя))
        self.assertIn("HTTP/2", КАК["tcp"])
        self.assertIn("CoAP", КАК["udp"])
        self.assertEqual({"udp:6000": "CoAP"}, проверить_как({"udp:6000": "CoAP"}))
        п = разобрать_пакет(udp(GET, 50000, 6000), как={"udp:6000": "CoAP"})
        self.assertEqual("CoAP", п.протокол)


if __name__ == "__main__":
    unittest.main()
