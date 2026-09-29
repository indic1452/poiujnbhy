"""Захват с сети: запись pcapng, синтез заголовков, карты, источники, менеджер, обработка, API.

Сверки идут с независимой сборкой пакетов (setevoy_sintez — поля struct.pack'ом
по RFC) и со спецификацией pcapng побайтно; обратно файл читает существующий
читатель захватов проекта (setevoy.chtenie). Живые проверки — UDP на 127.0.0.1
(прав не нужно) и AF_PACKET на «lo» (нужен root или CAP_NET_RAW, иначе пропуск).
libpcap берётся из REPORTGEN_TEST_LIBPCAP или системная — иначе её проверки пропускаются.
"""

import ctypes
import errno
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import struct
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.config import Settings, settings_warnings
from reportgen.setevoy.chtenie import прочитать_захват
from reportgen.setevoy.zahvat_seti import istochniki, karty, obrabotka, parametry, pcap_bib, zapis
from reportgen.setevoy.zahvat_seti.istochniki import ОшибкаЗахвата
from reportgen.setevoy.zahvat_seti.menedzher import Менеджер
from reportgen.setevoy.zahvat_seti.parametry import Фильтр, проверить


def свободный_порт() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    порт = s.getsockname()[1]
    s.close()
    return порт


def дождаться(условие, секунд=10.0):
    конец = time.time() + секунд
    while time.time() < конец:
        if условие():
            return True
        time.sleep(0.05)
    return False


def можно_af_packet() -> bool:
    try:
        s = socket.socket(getattr(socket, "AF_PACKET", 17), socket.SOCK_RAW, 0)
    except (OSError, AttributeError):
        return False
    s.close()
    return True


def путь_libpcap() -> str:
    свой = os.environ.get("REPORTGEN_TEST_LIBPCAP", "")
    if свой and Path(свой).exists():
        return свой
    lib, _ = pcap_bib.загрузить()
    return lib.путь if lib else ""


def сумма_rfc1071(данные: bytes) -> int:
    """Независимый расчёт контрольной суммы — побайтно, как в RFC 1071 §4.1 (C-пример)."""
    s = 0
    for i in range(0, len(данные) - 1, 2):
        s += (данные[i] << 8) | данные[i + 1]
    if len(данные) % 2:
        s += данные[-1] << 8
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


# -- синтез заголовков ---------------------------------------------------------------------

class СинтезTests(unittest.TestCase):
    def test_пример_rfc1071(self):
        # RFC 1071 §3: байты 00 01 f2 03 f4 f5 f6 f7 — сумма ddf2, контрольная сумма 220d.
        данные = bytes.fromhex("0001f203f4f5f6f7")
        self.assertEqual(0x220D, сумма_rfc1071(данные))
        заголовок = zapis.ipv4(b"", 17, "10.0.0.1", "10.0.0.2")
        self.assertEqual(0, сумма_rfc1071(заголовок[:20]), "сумма по заголовку с полем суммы — ноль")

    def test_кадр_udp_ipv4_как_независимая_сборка(self):
        нагрузка = b"\x80\x60\x00\x01" + bytes(range(40))
        кадр = zapis.кадр_udp(нагрузка, "192.0.2.10", 40000, "192.0.2.20", 5004)
        эталон = с.eth(с.ip(с.udp(нагрузка, 40000, 5004, "192.0.2.10", "192.0.2.20"), 17, "192.0.2.10", "192.0.2.20",
                            ид=0), dst="00:00:00:00:00:00", src="00:00:00:00:00:00")
        self.assertEqual(эталон, кадр)
        ip = кадр[14:34]
        self.assertEqual((0x45, 20 + 8 + len(нагрузка), 0x4000, 64, 17), (ip[0], struct.unpack("!H", ip[2:4])[0],
                                                                           struct.unpack("!H", ip[6:8])[0], ip[8], ip[9]))
        self.assertEqual(0, сумма_rfc1071(ip))
        псевдо = ipaddress.IPv4Address("192.0.2.10").packed + ipaddress.IPv4Address("192.0.2.20").packed + \
            struct.pack("!BBH", 0, 17, 8 + len(нагрузка))
        self.assertEqual(0, сумма_rfc1071(псевдо + кадр[34:]), "сумма UDP сходится с псевдозаголовком RFC 768")
        self.assertEqual(0x0800, struct.unpack("!H", кадр[12:14])[0])

    def test_кадр_udp_ipv6(self):
        нагрузка = b"abc"                                   # нечётная длина — дополнение нулём в сумме
        кадр = zapis.кадр_udp(нагрузка, "2001:db8::1", 1000, "2001:db8::2", 2000)
        эталон = с.eth(с.ip6(с.udp(нагрузка, 1000, 2000, "2001:db8::1", "2001:db8::2", v6=True), 17),
                       тип=0x86DD, dst="00:00:00:00:00:00", src="00:00:00:00:00:00")
        self.assertEqual(эталон, кадр)
        псевдо = ipaddress.IPv6Address("2001:db8::1").packed + ipaddress.IPv6Address("2001:db8::2").packed + \
            struct.pack("!I3xB", 8 + len(нагрузка), 17)
        self.assertEqual(0, сумма_rfc1071(псевдо + кадр[54:]))

    def test_нулевая_сумма_пишется_единицами(self):
        # Подбираем нагрузку, при которой вычисленная сумма UDP — ноль: RFC 768 велит 0xFFFF.
        for i in range(0x10000):
            нагрузка = struct.pack("!H", i)
            сег = struct.pack("!HHHH", 1, 2, 10, 0) + нагрузка
            if сумма_rfc1071(zapis.псевдозаголовок("10.0.0.1", "10.0.0.2", 17, 10) + сег) == 0:
                break
        self.assertEqual(0xFFFF, struct.unpack("!H", zapis.udp(нагрузка, 1, 2, "10.0.0.1", "10.0.0.2")[6:8])[0])

    def test_пределы_длины(self):
        with self.assertRaises(ValueError):
            zapis.udp(b"\0" * 65528, 1, 2, "10.0.0.1", "10.0.0.2")
        with self.assertRaises(ValueError):
            zapis.ipv4(b"\0" * 65516, 17, "10.0.0.1", "10.0.0.2")
        with self.assertRaises(ValueError):
            zapis.ipv6(b"\0" * 65536, 17, "::1", "::1")
        with self.assertRaises(ValueError):
            zapis.псевдозаголовок("10.0.0.1", "::1", 17, 8)
        self.assertEqual(65535, len(zapis.udp(b"\0" * 65527, 1, 2, "10.0.0.1", "10.0.0.2")))

    def test_псевдозаголовки_побайтно(self):
        # RFC 768: адрес отправителя, получателя, ноль, протокол, длина UDP — 12 байт.
        self.assertEqual(bytes([10, 0, 0, 1, 10, 0, 0, 2, 0, 17, 0x01, 0x02]),
                         zapis.псевдозаголовок("10.0.0.1", "10.0.0.2", 17, 0x0102))
        # RFC 8200 §8.1: адреса по 16 байт, длина — 32 бита, три нуля, следующий заголовок — 40 байт.
        self.assertEqual(ipaddress.IPv6Address("::1").packed + ipaddress.IPv6Address("::2").packed
                         + bytes([0, 0, 0x01, 0x02, 0, 0, 0, 17]), zapis.псевдозаголовок("::1", "::2", 17, 0x0102))

    def test_граничные_длины_и_ид(self):
        self.assertEqual(65535, len(zapis.ipv4(b"\0" * 65515, 17, "10.0.0.1", "10.0.0.2")))
        self.assertEqual(40 + 65535, len(zapis.ipv6(b"\0" * 65535, 17, "::1", "::1")))
        self.assertEqual(b"\xff\xff", zapis.ipv4(b"", 17, "10.0.0.1", "10.0.0.2", ид=0x1FFFF)[4:6])
        self.assertEqual(b"\xff\xff", zapis.ipv4(b"", 17, "10.0.0.1", "10.0.0.2", ид=0xFFFF)[4:6])
        self.assertEqual(b"\x12\x35", zapis.ipv4(b"", 17, "10.0.0.1", "10.0.0.2", ид=0x1235)[4:6])

    def test_кадр_из_ip(self):
        self.assertEqual(b"\0" * 12 + b"\x08\x00", zapis.кадр_из_ip(b""))
        пакет4 = с.ip(с.udp(b"x", 1, 2), 17)
        self.assertEqual(b"\0" * 12 + b"\x08\x00" + пакет4, zapis.кадр_из_ip(пакет4))
        пакет6 = с.ip6(с.udp(b"x", 1, 2, "::1", "::2", v6=True), 17, "::1", "::2")
        self.assertEqual(b"\x86\xdd", zapis.кадр_из_ip(пакет6)[12:14])


class РазборКадраTests(unittest.TestCase):
    def test_udp_ipv4_с_дополнением_ethernet(self):
        кадр = с.eth(с.ip(с.udp(b"hi", 1234, 5678, "10.1.1.1", "10.2.2.2"), 17, "10.1.1.1", "10.2.2.2"))
        кадр += b"\0" * (60 - len(кадр))                  # короткий кадр дополнен до 60 байт
        р = zapis.разобрать_кадр(кадр)
        self.assertEqual((4, "10.1.1.1", "10.2.2.2", 17, 1234, 5678, b"hi", False),
                         (р.версия, р.от, р.к, р.протокол, р.порт_от, р.порт_к, р.нагрузка, р.фрагмент))

    def test_vlan_двойная_метка(self):
        ip_ = с.ip(с.udp(b"v", 1, 2), 17)
        кадр = b"\0" * 12 + b"\x88\xa8\x00\x05\x81\x00\x00\x07\x08\x00" + ip_
        р = zapis.разобрать_кадр(кадр)
        self.assertEqual((0x0800, 17, b"v"), (р.тип, р.протокол, р.нагрузка))

    def test_ipv6_с_расширениями(self):
        сег = с.udp(b"six", 7, 8, "2001:db8::1", "2001:db8::2", v6=True)
        hop = bytes([17, 0]) + b"\0" * 6                        # Hop-by-Hop: следующий UDP, длина 0 (8 байт)
        кадр = с.eth(с.ip6(hop + сег, 0), тип=0x86DD)
        р = zapis.разобрать_кадр(кадр)
        self.assertEqual((6, 17, 7, 8, b"six"), (р.версия, р.протокол, р.порт_от, р.порт_к, р.нагрузка))
        фрагмент = bytes([17, 0, 0, 0x01]) + b"\0\0\0\x01"      # Fragment: смещение 0, M = 1
        р = zapis.разобрать_кадр(с.eth(с.ip6(фрагмент + сег, 44), тип=0x86DD))
        self.assertTrue(р.фрагмент)
        self.assertEqual((7, 8, None), (р.порт_от, р.порт_к, р.нагрузка))
        второй = bytes([17, 0]) + struct.pack("!H", 2 << 3) + b"\0\0\0\x01"
        р = zapis.разобрать_кадр(с.eth(с.ip6(второй + b"xxxxxxxx", 44), тип=0x86DD))
        self.assertEqual((17, -1), (р.протокол, р.порт_к))

    def test_фрагменты_ipv4(self):
        первый = с.eth(с.ip(с.udp(b"a" * 20, 1, 2), 17, флаги=0x2000))
        р = zapis.разобрать_кадр(первый)
        self.assertEqual((True, 2, None), (р.фрагмент, р.порт_к, р.нагрузка))
        второй = с.eth(с.ip(b"b" * 16, 17, флаги=0x0003))
        р = zapis.разобрать_кадр(второй)
        self.assertEqual((True, -1, -1), (р.фрагмент, р.порт_от, р.порт_к))

    def test_границы_заголовков(self):
        р = zapis.разобрать_кадр
        self.assertEqual((0x0806, 0), (р(b"\0" * 12 + b"\x08\x06").тип, р(b"\0" * 12 + b"\x08\x06").версия))
        self.assertEqual(0, р(b"\0" * 11 + b"\x08\x06").тип, "13 байт — не кадр")
        метка = b"\0" * 12 + b"\x81\x00\x00\x05"
        self.assertEqual(0x0800, р(метка + b"\x08\x00").тип, "метка VLAN во всю длину кадра")
        self.assertEqual(0x8100, р(метка + b"\x08").тип, "обрывок метки — тип не читается")
        голый = с.eth(с.ip(b"", 17))                           # заголовок IPv4 без нагрузки: 34 байта
        self.assertEqual((4, 17, "10.0.0.1", -1), (р(голый).версия, р(голый).протокол, р(голый).от, р(голый).порт_к))
        self.assertEqual(0, р(голый[:33]).версия, "33 байта — заголовок IPv4 оборван")
        шесть = с.eth(с.ip(с.udp(b"x", 1, 2), 17))
        шесть = шесть[:14] + bytes([0x65]) + шесть[15:]           # EtherType IPv4, а версия 6
        self.assertEqual(0, р(шесть).версия)
        чужой = с.eth(с.ip(с.udp(b"x", 1, 2), 17), тип=0x86DD)   # EtherType IPv6, а внутри IPv4
        self.assertEqual(0, р(чужой).версия)
        # Длина IP меньше, чем обещает UDP: нагрузка — только в пределах пакета IP (+ дополнение кадра отсечено).
        обрезанный = с.ip(с.udp(b"abcdefgh", 1, 2), 17)
        обрезанный = обрезанный[:2] + struct.pack("!H", 20 + 8 + 4) + обрезанный[4:] + b"\0" * 10
        self.assertEqual(b"abcd", р(с.eth(обрезанный)).нагрузка)

    def test_не_ip_и_обрывки(self):
        self.assertEqual(0, zapis.разобрать_кадр(b"\0" * 10).тип)
        arp = zapis.разобрать_кадр(с.eth(b"\0" * 28, тип=0x0806))
        self.assertEqual((0x0806, -1, ""), (arp.тип, arp.протокол, arp.от))
        tcp = zapis.разобрать_кадр(с.eth(с.ip(с.tcp(b"z", 80, 81), 6)))
        self.assertEqual((6, 80, 81, None), (tcp.протокол, tcp.порт_от, tcp.порт_к, tcp.нагрузка))
        короткий = с.eth(с.ip(b"\x00\x01\x00\x02\x00\x04\x00\x00", 17))     # длина UDP 4 < 8 — нагрузки нет
        self.assertIsNone(zapis.разобрать_кадр(короткий).нагрузка)


# -- pcapng ---------------------------------------------------------------------------------

class PcapngTests(unittest.TestCase):
    def записать(self, кадры, **интерфейс):
        путь = Path(tempfile.mkdtemp()) / "t.pcapng"
        self.addCleanup(lambda: путь.unlink(missing_ok=True))
        with open(путь, "wb") as f:
            п = zapis.ПисательPcapng(f, **интерфейс)
            for время, кадр in кадры:
                п.пакет(время, кадр)
            п.итог(начало=1.0, конец=2.5, принято=len(кадры) + 1, отброшено=3, отфильтровано_принято=len(кадры),
                   доставлено=len(кадры))
            self.assertEqual(f.tell(), п.записано)
        return путь

    def test_поля_блоков_по_спецификации(self):
        кадр = с.eth(с.ip(с.udp(b"12345", 1, 2), 17))
        путь = self.записать([(1790000000.123456, кадр)], имя="eth0", описание="Сетевая карта", фильтр="udp",
                             mac=bytes.fromhex("020000000001"), ipv4=("192.0.2.5", 24), скорость=10**9)
        д = путь.read_bytes()
        # SHB: тип, длина (в начале и в конце), магия 1A2B3C4D, версия 1.0, длина секции −1.
        тип, длина, магия, старшая, младшая, секция = struct.unpack_from("<IIIHHq", д, 0)
        self.assertEqual((0x0A0D0D0A, 0x1A2B3C4D, 1, 0, -1), (тип, магия, старшая, младшая, секция))
        self.assertEqual(длина, struct.unpack_from("<I", д, длина - 4)[0])
        self.assertEqual(0, длина % 4)
        опции = self.опции(д[24:длина - 4])
        self.assertIn(4, опции)                                          # shb_userappl
        self.assertEqual(b"reportgen", опции[4])
        # IDB: LINKTYPE_ETHERNET, резерв 0, snaplen 262144; if_name, if_description (UTF-8), if_tsresol = 6.
        idb = длина
        тип, дл_idb, канал, резерв, snap = struct.unpack_from("<IIHHI", д, idb)
        self.assertEqual((1, 1, 0, 262144), (тип, канал, резерв, snap))
        опции = self.опции(д[idb + 16:idb + дл_idb - 4])
        self.assertEqual(b"eth0", опции[2])
        self.assertEqual("Сетевая карта".encode(), опции[3])
        self.assertEqual(bytes([192, 0, 2, 5, 255, 255, 255, 0]), опции[4])
        self.assertEqual(bytes.fromhex("020000000001"), опции[6])
        self.assertEqual(struct.pack("<Q", 10**9), опции[8])
        self.assertEqual(b"\x06", опции[9])
        self.assertEqual(b"\0udp", опции[11])
        # EPB: интерфейс 0, время — одно 64-битное число микросекунд (старшие, младшие), длины, данные с дополнением.
        epb = idb + дл_idb
        тип, дл_epb, интерфейс, ст, мл, записано, исходно = struct.unpack_from("<IIIIIII", д, epb)
        self.assertEqual((6, 0, len(кадр), len(кадр)), (тип, интерфейс, записано, исходно))
        self.assertEqual(1790000000123456, (ст << 32) | мл)
        self.assertEqual(кадр, д[epb + 28:epb + 28 + len(кадр)])
        self.assertEqual(32 + len(кадр) + (-len(кадр) % 4), дл_epb)
        self.assertEqual(b"\0" * (-len(кадр) % 4), д[epb + 28 + len(кадр):epb + дл_epb - 4])
        # ISB: интерфейс 0, опции времени начала/конца и счётчиков (u64).
        isb = epb + дл_epb
        тип, дл_isb, интерфейс = struct.unpack_from("<III", д, isb)
        self.assertEqual((5, 0), (тип, интерфейс))
        опции = self.опции(д[isb + 20:isb + дл_isb - 4])
        self.assertEqual(struct.pack("<II", 0, 1_000_000), опции[2])
        self.assertEqual(struct.pack("<II", 0, 2_500_000), опции[3])
        self.assertEqual((2, 3, 1, 1), tuple(struct.unpack("<Q", опции[к])[0] for к in (4, 5, 6, 8)))
        self.assertEqual(len(д), isb + дл_isb)

    def test_время_и_длины_epb(self):
        def поля(блок):
            return struct.unpack_from("<IIIII", блок, 8)
        self.assertEqual((0, 0, 0, 0, 0), поля(zapis.пакет_epb(0.0, b"")))
        self.assertEqual((0, 0, 0, 0, 0), поля(zapis.пакет_epb(-5.0, b"")), "время до 1970 — ноль")
        self.assertEqual((0, 3, 5, 2, 9), поля(zapis.пакет_epb(((3 << 32) + 5) / 1e6, b"ab", 9)))
        self.assertEqual((0, 0, 7, 2, 2), поля(zapis.пакет_epb(7e-6, b"ab", 1)), "исходная длина не меньше записанной")
        без = self.опции(zapis.описание_интерфейса()[16:-4])
        self.assertEqual({9}, set(без), "без скорости, имени и адресов — только if_tsresol")
        self.assertEqual(struct.pack("<Q", 1), self.опции(zapis.описание_интерфейса(скорость=1)[16:-4])[8])

    @staticmethod
    def опции(тело: bytes) -> dict[int, bytes]:
        итог, место = {}, 0
        while место + 4 <= len(тело):
            код, дл = struct.unpack_from("<HH", тело, место)
            if код == 0:
                assert дл == 0
                break
            итог[код] = тело[место + 4:место + 4 + дл]
            место += 4 + дл + (-дл % 4)
        return итог

    def test_читается_читателем_проекта(self):
        кадры = [(1000.5 + i, с.eth(с.ip(с.udp(bytes([i]) * (i + 1), 5000, 6000), 17))) for i in range(7)]
        путь = self.записать(кадры, имя="lo")
        захват = прочитать_захват(путь)
        self.assertEqual(("pcapng", []), (захват.формат, захват.заметки))
        self.assertEqual([к for _, к in кадры], [з.данные for з in захват.записи])
        self.assertEqual(["Ethernet"] * 7, [з.канал for з in захват.записи])
        self.assertAlmostEqual(1003.5, захват.записи[3].время, places=5)
        with open(путь, "rb") as f:
            свои = list(obrabotka.кадры_pcapng(f))
        self.assertEqual([к for _, к in кадры], [д for _, д, _ in свои])
        self.assertEqual({1}, {т for _, _, т in свои})

    def test_оборванный_файл_читается_до_обрыва(self):
        путь = self.записать([(1.0, b"A" * 60), (2.0, b"B" * 60)])
        д = путь.read_bytes()
        путь.write_bytes(д[:len(д) - 100 - 20])               # без ISB и без хвоста второго EPB
        with open(путь, "rb") as f:
            self.assertEqual([b"A" * 60], [д for _, д, _ in obrabotka.кадры_pcapng(f)])
        путь.write_bytes(д[:40] + b"\x06\0\0\0\x05\0\0\0")      # длина блока не кратна 4 — стоп
        with open(путь, "rb") as f:
            self.assertEqual([], list(obrabotka.кадры_pcapng(f)))

    def test_большой_порядок_байт_и_разрешение_времени(self):
        """Чужой pcapng: big-endian SHB и if_tsresol = 9 (наносекунды) — наш поточный читатель понимает."""
        def блок(вид, тело):
            тело += b"\0" * (-len(тело) % 4)
            return struct.pack(">II", вид, 12 + len(тело)) + тело + struct.pack(">I", 12 + len(тело))
        shb = блок(0x0A0D0D0A, struct.pack(">IHHq", 0x1A2B3C4D, 1, 0, -1))
        idb = блок(1, struct.pack(">HHI", 101, 0, 0) + struct.pack(">HH", 9, 1) + b"\x09\0\0\0" + b"\0\0\0\0")
        пакет = с.ip(с.udp(b"ns", 9, 10), 17)
        epb = блок(6, struct.pack(">IIIII", 0, 0, 1_500_000_000, len(пакет), len(пакет)) + пакет)
        путь = Path(tempfile.mkdtemp()) / "be.pcapng"
        путь.write_bytes(shb + idb + epb)
        with open(путь, "rb") as f:
            (время, данные, тип), = list(obrabotka.кадры_pcapng(f))
        self.assertEqual((101, пакет), (тип, данные))
        self.assertAlmostEqual(1.5, время)
        self.assertEqual([(10, b"ns")], [(р.порт_к, р.нагрузка) for _, р in obrabotka.датаграммы(путь)])


# -- параметры и фильтр --------------------------------------------------------------------

class ПараметрыTests(unittest.TestCase):
    def п(self, **данные):
        return проверить(данные, потолок_секунд=600, потолок_байт=50 << 20)

    def test_порты(self):
        self.assertEqual([5000, 5002, 5003, 5004], parametry.разобрать_порты("5000, 5002-5004"))
        self.assertEqual([1, 65535], parametry.разобрать_порты([1, "65535", 1]))
        for плохо in ("0", "65536", "abc", "5-3", "1-40", ",".join(str(i) for i in range(1, 18)), "-1"):
            with self.assertRaises(ValueError, msg=плохо):
                parametry.разобрать_порты(плохо)
        self.assertEqual(list(range(1, 17)), parametry.разобрать_порты("1-16"))

    def test_udp_по_умолчанию(self):
        п = self.п(режим="udp", порты="5004")
        self.assertEqual(("0.0.0.0", [5004], 600, parametry.ПАКЕТОВ_ДО, 50 << 20), (п.адрес, п.порты, п.секунд, п.пакетов, п.байт))
        п = self.п(режим="udp", адрес="::1", порты=[9], пределы={"секунд": 5, "пакетов": 7, "мегабайт": 2}, имя="мой/захват\x01")
        self.assertEqual(("::1", 5, 7, 2 << 20, "мойзахват"), (п.адрес, п.секунд, п.пакетов, п.байт, п.имя))

    def test_плохие_данные(self):
        плохие = [
            ("не объект", []), ("режим", {"режим": "всё"}), ("нет порта", {"режим": "udp"}),
            ("адрес", {"режим": "udp", "порты": "1", "адрес": "10.0.0.300"}),
            ("группа не multicast", {"режим": "udp", "порты": "1", "группа": "10.0.0.1"}),
            ("группа другой версии", {"режим": "udp", "порты": "1", "группа": "ff02::1"}),
            ("выше потолка времени", {"режим": "udp", "порты": "1", "пределы": {"секунд": 601}}),
            ("выше потолка объёма", {"режим": "udp", "порты": "1", "пределы": {"мегабайт": 51}}),
            ("пакетов ноль и меньше", {"режим": "udp", "порты": "1", "пределы": {"пакетов": -1}}),
            ("пределы не объект", {"режим": "udp", "порты": "1", "пределы": [1]}),
            ("булево — не число", {"режим": "udp", "порты": "1", "пределы": {"секунд": True}}),
            ("карта не выбрана", {"режим": "карта"}),
            ("способ", {"режим": "карта", "карта": "lo", "способ": "scapy"}),
            ("протокол", {"режим": "карта", "карта": "lo", "фильтр": {"протокол": "sctp"}}),
            ("порт фильтра", {"режим": "карта", "карта": "lo", "фильтр": {"порт": 70000}}),
            ("адрес фильтра", {"режим": "карта", "карта": "lo", "фильтр": {"от": "host; rm -rf"}}),
        ]
        for что, данные in плохие:
            with self.assertRaises(ValueError, msg=что):
                проверить(данные, потолок_секунд=600, потолок_байт=50 << 20)
        п = self.п(режим="udp", порты="1", группа="239.1.2.3")
        self.assertEqual("239.1.2.3", п.группа)

    def test_фильтр_bpf_и_разбор_согласованы(self):
        ф = Фильтр.из({"протокол": "udp", "от": "10.0.0.1", "к": "10.0.0.2", "порт": "5000"})
        self.assertEqual("(udp and ip src host 10.0.0.1 and ip dst host 10.0.0.2 and (tcp port 5000 or udp port 5000)) or "
                         "(vlan and (udp and ip src host 10.0.0.1 and ip dst host 10.0.0.2 and (tcp port 5000 or udp port 5000)))",
                         ф.bpf())
        self.assertEqual("(icmp or icmp6)", Фильтр(протокол="icmp").bpf().split(" or (vlan")[0][1:-1])
        self.assertEqual("(ip6 host 2001:db8::1) or (vlan and (ip6 host 2001:db8::1))", Фильтр(хост="2001:db8::1").bpf())
        self.assertEqual("", Фильтр().bpf())
        self.assertTrue(Фильтр().пуст())
        р = zapis.разобрать_кадр
        годный = р(с.eth(с.ip(с.udp(b"", 1234, 5000, "10.0.0.1", "10.0.0.2"), 17, "10.0.0.1", "10.0.0.2")))
        self.assertTrue(ф.подходит(годный))
        обратный = р(с.eth(с.ip(с.udp(b"", 5000, 1234, "10.0.0.2", "10.0.0.1"), 17, "10.0.0.2", "10.0.0.1")))
        self.assertFalse(ф.подходит(обратный))
        self.assertTrue(Фильтр(хост="10.0.0.1", порт=5000).подходит(обратный))
        self.assertFalse(Фильтр(порт=5001).подходит(годный))
        self.assertFalse(Фильтр(протокол="tcp").подходит(годный))
        self.assertTrue(Фильтр(протокол="ip").подходит(годный))
        self.assertFalse(Фильтр(протокол="ip6").подходит(годный))
        self.assertFalse(Фильтр(к="10.0.0.9").подходит(годный))
        self.assertFalse(Фильтр(хост="10.0.0.9").подходит(годный))
        icmp6 = р(с.eth(с.ip6(b"\x80\0\0\0", 58), тип=0x86DD))
        self.assertTrue(Фильтр(протокол="icmp").подходит(icmp6))
        self.assertTrue(Фильтр(протокол="ip6").подходит(icmp6))
        arp = р(с.eth(b"\0" * 28, тип=0x0806))
        self.assertTrue(Фильтр(протокол="arp").подходит(arp))
        self.assertFalse(Фильтр(порт=1).подходит(arp))
        self.assertFalse(Фильтр(протокол="arp").подходит(годный))
        tcp = р(с.eth(с.ip(с.tcp(b"", 5000, 80), 6)))
        self.assertTrue(Фильтр(порт=5000).подходит(tcp))
        self.assertEqual({"протокол": "udp", "от": "10.0.0.1", "к": "10.0.0.2", "хост": "", "порт": 5000}, ф.в_словарь())


# -- карты ------------------------------------------------------------------------------------

class КартыLinuxTests(unittest.TestCase):
    @unittest.skipUnless(Path("/sys/class/net/lo").exists(), "нет sysfs")
    def test_петля_есть_всегда(self):
        список, заметки = karty.перечень()
        lo = next(к for к in список if к["ид"] == "lo")
        self.assertTrue(lo["петля"])
        self.assertEqual("петля", lo["вид"])
        self.assertIn({"адрес": "127.0.0.1", "префикс": 8}, lo["ipv4"])
        self.assertTrue(lo["работает"])
        self.assertEqual("lo", lo["устройство_pcap"])

    def test_поддельный_sysfs(self):
        корень = Path(tempfile.mkdtemp())
        def карта(имя, **файлы):
            п = корень / имя
            п.mkdir()
            for к, з in файлы.items():
                (п / к).write_text(з + "\n")
            return п
        карта("eth7", address="02:00:00:00:00:07", operstate="up", speed="1000", mtu="1500", type="1", flags="0x1003")
        карта("eth8", address="00:00:00:00:00:00", operstate="lowerlayerdown", speed="-1", type="1", flags="0x1003")
        карта("tun0", operstate="unknown", type="65534", flags="0x10d1", carrier="1")
        (карта("wl0", operstate="dormant", type="1", flags="0x1003") / "wireless").mkdir()
        адреса = {"eth7": [{"вид": "ipv4", "адрес": "10.1.1.1", "префикс": 24},
                           {"вид": "ipv6", "адрес": "fe80::1", "префикс": 64}]}
        итог = {к["ид"]: к for к in karty.карты_linux(корень, адреса)}
        e7 = итог["eth7"]
        self.assertEqual(("Ethernet", "02:00:00:00:00:07", "работает", True, 10**9, 1500),
                         (e7["вид"], e7["mac"], e7["состояние"], e7["работает"], e7["скорость"], e7["mtu"]))
        self.assertEqual(([{"адрес": "10.1.1.1", "префикс": 24}], [{"адрес": "fe80::1", "префикс": 64}]), (e7["ipv4"], e7["ipv6"]))
        e8 = итог["eth8"]
        self.assertEqual(("", "нет связи (кабель)", False, None), (e8["mac"], e8["состояние"], e8["работает"], e8["скорость"]))
        self.assertEqual(("ARPHRD 65534", True, "работает"), (итог["tun0"]["вид"], итог["tun0"]["работает"], итог["tun0"]["состояние"]))
        self.assertEqual(("Wi-Fi", "ожидает", False), (итог["wl0"]["вид"], итог["wl0"]["состояние"], итог["wl0"]["работает"]))

    def test_запасной_путь_ioctl_и_if_inet6(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux")
        файл = Path(tempfile.mkdtemp()) / "if_inet6"
        файл.write_text("fe800000000000000000000000000001 02 40 20 80     eth0\n"
                        "00000000000000000000000000000001 01 80 10 80       lo\n"
                        "испорчено\n20010db8000000000000000000000001 03 zz 00 80 eth1\n", encoding="utf-8")
        адреса = karty.адреса_ioctl(["lo", "нет-такой0"], файл)
        self.assertEqual([{"вид": "ipv4", "адрес": "127.0.0.1", "префикс": 8}, {"вид": "ipv6", "адрес": "::1", "префикс": 128}],
                         адреса["lo"])
        self.assertEqual([{"вид": "ipv6", "адрес": "fe80::1", "префикс": 64}], адреса["eth0"], "длина префикса — hex")
        self.assertNotIn("нет-такой0", адреса)
        self.assertNotIn("eth1", адреса)
        with mock.patch.object(karty, "адреса_getifaddrs", side_effect=OSError("нет netlink")):
            lo = next(к for к in karty.карты_linux() if к["ид"] == "lo")
        self.assertIn({"адрес": "127.0.0.1", "префикс": 8}, lo["ipv4"])

    def test_getifaddrs_видит_петлю(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("Linux")
        self.assertIn({"вид": "ipv4", "адрес": "127.0.0.1", "префикс": 8}, karty.адреса_getifaddrs().get("lo", []))


def _utf16(буфер, место: int, текст: str) -> int:
    данные = текст.encode("utf-16-le") + b"\0\0"
    буфер[место:место + len(данные)] = данные
    return место + len(данные)


@unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "раскладка проверяется для x64")
class КартыWindowsTests(unittest.TestCase):
    """GetAdaptersAddresses на поддельном буфере: поля кладутся по смещениям x64 из iptypes.h."""

    def test_смещения_структур(self):
        А, У = karty.IP_ADAPTER_ADDRESSES_LH, karty.IP_ADAPTER_UNICAST_ADDRESS_LH
        ожидаемые = {"Length": 0, "IfIndex": 4, "Next": 8, "AdapterName": 16, "FirstUnicastAddress": 24,
                     "Description": 64, "FriendlyName": 72, "PhysicalAddress": 80, "PhysicalAddressLength": 88,
                     "Flags": 92, "Mtu": 96, "IfType": 100, "OperStatus": 104, "ZoneIndices": 112, "FirstPrefix": 176,
                     "TransmitLinkSpeed": 184, "ReceiveLinkSpeed": 192, "Luid": 224, "Dhcpv4Server": 232,
                     "NetworkGuid": 252, "Dhcpv6Server": 280, "Dhcpv6ClientDuid": 296, "FirstDnsSuffix": 440}
        self.assertEqual(ожидаемые, {к: getattr(А, к).offset for к in ожидаемые})
        self.assertEqual(448, ctypes.sizeof(А))
        self.assertEqual({"Next": 8, "Address": 16, "OnLinkPrefixLength": 56},
                         {к: getattr(У, к).offset for к in ("Next", "Address", "OnLinkPrefixLength")})
        self.assertEqual(64, ctypes.sizeof(У))

    def test_разбор_цепочки(self):
        буфер = ctypes.create_string_buffer(4096)
        база = ctypes.addressof(буфер)
        б = bytearray(4096)
        # Адаптер 1 @0, адаптер 2 @448; адреса @1024…; строки @2048…
        guid = b"{12345678-ABCD-4EF0-8123-456789ABCDEF}"
        б[2048:2048 + len(guid) + 1] = guid + b"\0"
        описание = 2200
        имя = _utf16(б, описание, "Intel(R) Ethernet Connection I219-LM")
        конец = _utf16(б, имя, "Ethernet 2 — стенд")
        имя_петли = конец
        _utf16(б, имя_петли, "Loopback Pseudo-Interface 1")
        # sockaddr_in @1500: AF_INET = 2, порт 0, адрес 192.0.2.5; sockaddr_in6 @1532: AF_INET6 = 23, адрес @+8.
        struct.pack_into("<H", б, 1500, 2)
        б[1504:1508] = bytes([192, 0, 2, 5])
        struct.pack_into("<H", б, 1532, 23)
        б[1540:1556] = ipaddress.IPv6Address("fe80::1234").packed
        # Адрес 1 @1024 → адрес 2 @1088; SOCKET_ADDRESS @16 (указатель, длина), OnLinkPrefixLength @56.
        struct.pack_into("<IIQQi", б, 1024, 64, 0, база + 1088, база + 1500, 16)
        б[1024 + 56] = 24
        struct.pack_into("<IIQQi", б, 1088, 64, 0, 0, база + 1532, 28)
        б[1088 + 56] = 64
        # Адаптер 1.
        struct.pack_into("<IIQQQ", б, 0, 448, 12, база + 448, база + 2048, база + 1024)
        struct.pack_into("<QQ", б, 64, база + описание, база + имя)
        б[80:86] = bytes.fromhex("0050569a0b0c")
        struct.pack_into("<IIIIiI", б, 88, 6, 0, 1500, 6, 1, 12)
        struct.pack_into("<QQ", б, 184, 10**9, 10**9)
        # Адаптер 2: петля, скорость неизвестна (все единицы), состояние Down, без MAC и без Next.
        struct.pack_into("<IIQQQ", б, 448, 448, 1, 0, 0, 0)
        struct.pack_into("<QQ", б, 448 + 64, 0, база + имя_петли)
        struct.pack_into("<IIIIiI", б, 448 + 88, 0, 0, 1500, 24, 2, 1)
        struct.pack_into("<QQ", б, 448 + 184, 2**64 - 1, 2**64 - 1)
        ctypes.memmove(буфер, bytes(б), len(б))
        первый, второй = karty.разобрать_адаптеры(база)
        self.assertEqual({
            "ид": guid.decode(), "имя": "Ethernet 2 — стенд", "описание": "Intel(R) Ethernet Connection I219-LM",
            "вид": "Ethernet", "mac": "00:50:56:9a:0b:0c", "ipv4": [{"адрес": "192.0.2.5", "префикс": 24}],
            "ipv6": [{"адрес": "fe80::1234", "префикс": 64}], "состояние": "работает", "работает": True,
            "скорость": 10**9, "mtu": 1500, "петля": False, "arphrd": None,
            "устройство_pcap": "\\Device\\NPF_" + guid.decode(), "индекс": 12}, первый)
        self.assertEqual(("Loopback Pseudo-Interface 1", "", "петля", True, None, "отключена", False, "", None),
                         (второй["имя"], второй["ид"], второй["вид"], второй["петля"], второй["скорость"],
                          второй["состояние"], второй["работает"], второй["mac"], второй["устройство_pcap"]))
        карты = [dict(первый), dict(второй)]
        karty.сопоставить_npcap(карты, [{"имя": "\\Device\\NPF_" + guid.decode().lower(), "описание": "Npcap"},
                                        {"имя": "\\Device\\NPF_Loopback", "описание": "Adapter for loopback"}])
        self.assertEqual("\\Device\\NPF_" + guid.decode().lower(), карты[0]["устройство_pcap"])
        self.assertIsNone(карты[1]["устройство_pcap"])

    def test_строка_utf16_и_адрес(self):
        self.assertEqual("", karty.строка_utf16(0))
        self.assertIsNone(karty.адрес_windows(None))
        буфер = ctypes.create_string_buffer(struct.pack("<H", 99) + b"\0" * 30)
        self.assertIsNone(karty.адрес_windows(ctypes.addressof(буфер)))

    def test_карты_из_pcap(self):
        список = karty.карты_из_pcap([{"имя": "en0", "описание": "Wi-Fi", "mac": "", "флаги": 0x6,
                                        "адреса": [{"вид": "ipv4", "адрес": "10.0.0.2", "префикс": 24},
                                                   {"вид": "ipv6", "адрес": "fe80::2", "префикс": 64}]},
                                       {"имя": "lo0", "описание": "", "mac": "", "флаги": 0x1 | 0x2, "адреса": []}])
        self.assertEqual((True, [{"адрес": "10.0.0.2", "префикс": 24}], [{"адрес": "fe80::2", "префикс": 64}]),
                         (список[0]["работает"], список[0]["ipv4"], список[0]["ipv6"]))
        self.assertEqual((False, True, "отключена"), (список[1]["работает"], список[1]["петля"], список[1]["состояние"]))


# -- libpcap: структуры, разбор адресов ---------------------------------------------------------

class PcapБиблиотекаTests(unittest.TestCase):
    def test_раскладка_по_платформе(self):
        win, posix = pcap_bib.структуры(True), pcap_bib.структуры(False)
        self.assertEqual(16, ctypes.sizeof(win["pcap_pkthdr"]))
        self.assertEqual((8, 12), (win["pcap_pkthdr"].caplen.offset, win["pcap_pkthdr"].len.offset))
        self.assertEqual(24, ctypes.sizeof(win["pcap_stat"]))
        self.assertEqual(12, ctypes.sizeof(posix["pcap_stat"]))
        if ctypes.sizeof(ctypes.c_long) == 8:
            self.assertEqual(24, ctypes.sizeof(posix["pcap_pkthdr"]))
        self.assertEqual(40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20, ctypes.sizeof(pcap_bib.pcap_if))
        self.assertEqual(8, ctypes.sizeof(pcap_bib.bpf_insn))

    def test_тип_в_файл(self):
        self.assertEqual((101, 101, 1, 113, 0), tuple(pcap_bib.тип_в_файл(д) for д in (12, 14, 1, 113, 0)))

    def test_адрес_из_sockaddr(self):
        def sa(байты):
            буфер = ctypes.create_string_buffer(байты + b"\0" * (32 - len(байты)))
            self.addCleanup(lambda: буфер)
            return ctypes.cast(буфер, ctypes.POINTER(pcap_bib.sockaddr))
        v4 = struct.pack("=H", socket.AF_INET) + b"\0\0" + bytes([10, 1, 2, 3])
        self.assertEqual(("ipv4", "10.1.2.3"), pcap_bib.адрес_из_sockaddr(sa(v4)))
        v6 = struct.pack("=H", socket.AF_INET6) + b"\0" * 6 + ipaddress.IPv6Address("2001:db8::7").packed
        self.assertEqual(("ipv6", "2001:db8::7"), pcap_bib.адрес_из_sockaddr(sa(v6)))
        if sys.platform.startswith("linux"):
            ll = struct.pack("=HHiHBB", 17, 0, 2, 1, 0, 6) + bytes.fromhex("02aabbccddee")
            self.assertEqual(("mac", "02:aa:bb:cc:dd:ee"), pcap_bib.адрес_из_sockaddr(sa(ll)))
            self.assertIsNone(pcap_bib.адрес_из_sockaddr(sa(struct.pack("=HHiHBB", 17, 0, 2, 1, 0, 0))))
        self.assertIsNone(pcap_bib.адрес_из_sockaddr(sa(struct.pack("=H", 99))))
        self.assertIsNone(pcap_bib.адрес_из_sockaddr(None))
        self.assertEqual((24, 64, 0, 32), tuple(pcap_bib.длина_префикса(м) for м in
                                                ("255.255.255.0", "ffff:ffff:ffff:ffff::", "0.0.0.0", "255.255.255.255")))

    def test_пути_и_честная_причина(self):
        with mock.patch.object(pcap_bib, "WINDOWS", True), mock.patch.dict(os.environ, {"SystemRoot": r"C:\Win"}):
            пути = pcap_bib.пути_библиотеки("D:/свой/wpcap.dll")
            self.assertEqual("D:/свой/wpcap.dll", пути[0])
            self.assertTrue(пути[1].replace("\\", "/").endswith("System32/Npcap/wpcap.dll"))
            lib, причина = pcap_bib.загрузить("/нет/такой/wpcap.dll")
            self.assertIsNone(lib)
            self.assertIn("Npcap не установлен", причина)
        with mock.patch.object(pcap_bib, "WINDOWS", False), mock.patch.object(pcap_bib, "пути_библиотеки",
                                                                              return_value=["/нет/libpcap.so"]):
            lib, причина = pcap_bib.загрузить()
            self.assertIsNone(lib)
            self.assertIn("libpcap не найдена", причина)
            self.assertIn("/нет/libpcap.so: файла нет", причина)

    @unittest.skipUnless(путь_libpcap(), "libpcap не найдена (задайте REPORTGEN_TEST_LIBPCAP)")
    def test_живая_libpcap(self):
        lib = pcap_bib.Libpcap(путь_libpcap())
        self.assertIn("libpcap", lib.версия().lower())
        устройства = {у["имя"]: у for у in lib.устройства()}
        self.assertIn("lo", устройства)
        self.assertIn({"вид": "ipv4", "адрес": "127.0.0.1", "префикс": 8}, устройства["lo"]["адреса"])
        self.assertTrue(устройства["lo"]["флаги"] & pcap_bib.PCAP_IF_LOOPBACK)
        программа = lib.скомпилировать("udp dst port 5000")
        self.assertTrue(программа and all(len(к) == 4 for к in программа))
        self.assertEqual(6, программа[-1][0], "последняя команда — BPF_RET")
        with self.assertRaises(pcap_bib.ОшибкаPcap):
            lib.скомпилировать("udp dst port")


# -- источники и выбор способа --------------------------------------------------------------

class ПоддельныйСокет:
    """Записывает вызовы; recv_into отдаёт заготовленные пакеты, затем «нет данных»."""

    def __init__(self, *арг, ошибка_создания=None, пакеты=(), ошибка_bind=None):
        if ошибка_создания:
            raise ошибка_создания
        self.вызовы = [("socket", арг)]
        self.пакеты = list(пакеты)
        self.ошибка_bind = ошибка_bind

    def setsockopt(self, *а):
        self.вызовы.append(("setsockopt", а))

    def bind(self, адрес):
        self.вызовы.append(("bind", адрес))
        if self.ошибка_bind:
            raise self.ошибка_bind

    def ioctl(self, *а):
        self.вызовы.append(("ioctl", а))

    def setblocking(self, флаг):
        self.вызовы.append(("setblocking", флаг))

    def recv_into(self, буфер):
        if not self.пакеты:
            raise BlockingIOError
        п = self.пакеты.pop(0)
        буфер[:len(п)] = п
        return len(п)

    def fileno(self):
        return -1

    def close(self):
        self.вызовы.append(("close",))


class ИсточникиTests(unittest.TestCase):
    def test_понять_ошибку(self):
        self.assertEqual("занято", istochniki.понять_ошибку(OSError(errno.EADDRINUSE, "x"), что="t").вид)
        self.assertEqual("права", istochniki.понять_ошибку(OSError(errno.EPERM, "x"), что="t").вид)
        self.assertEqual("права", istochniki.понять_ошибку(OSError(errno.EACCES, "x"), что="t").вид)
        self.assertEqual("адрес", istochniki.понять_ошибку(OSError(errno.EADDRNOTAVAIL, "x"), что="t").вид)
        self.assertEqual("адрес", istochniki.понять_ошибку(OSError(errno.ENODEV, "x"), что="t").вид)
        self.assertEqual("ошибка", istochniki.понять_ошибку(OSError(errno.EIO, "беда"), что="t").вид)
        self.assertIn("IPv6", str(istochniki.понять_ошибку(OSError(errno.EAFNOSUPPORT, "x"), что="t")))
        win = OSError(errno.EACCES, "x")
        win.winerror = 10013
        self.assertEqual("права", istochniki.понять_ошибку(win, что="t").вид)
        self.assertEqual("занято", istochniki.понять_ошибку(win, что="t", udp=True).вид,
                         "WSAEACCES при bind UDP — исключительный доступ другой программы")
        win.winerror = 10048
        self.assertIn("порт занят", str(istochniki.понять_ошибку(win, что="UDP")))
        win.winerror = 10049
        self.assertEqual("адрес", istochniki.понять_ошибку(win, что="t").вид)

    def test_sio_rcvall_порядок_вызовов(self):
        пакет = с.ip(с.udp(b"win", 1, 2, "10.0.0.5", "10.0.0.6"), 17, "10.0.0.5", "10.0.0.6")
        сокеты = []

        def фабрика(*арг):
            сокеты.append(ПоддельныйСокет(*арг, пакеты=[пакет]))
            return сокеты[-1]
        with mock.patch.object(istochniki, "_ждать", side_effect=lambda с_, т: с_):
            источник = istochniki.ЗахватSioRcvall("10.0.0.5", фабрика=фабрика)
            ((время, кадр, длина),) = источник.прочитать(0.1)
        self.assertEqual(b"\0" * 12 + b"\x08\x00" + пакет, кадр)
        self.assertEqual(len(кадр), длина)
        источник.закрыть()
        в = сокеты[0].вызовы
        self.assertEqual(("socket", (socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)), в[0])
        виды = [x[0] for x in в]
        self.assertLess(виды.index("bind"), виды.index("ioctl"))
        self.assertIn(("bind", ("10.0.0.5", 0)), в)
        self.assertIn(("setsockopt", (socket.IPPROTO_IP, socket.IP_HDRINCL, 1)), в)
        код = getattr(socket, "SIO_RCVALL", istochniki.SIO_RCVALL)
        self.assertEqual([(код, 1), (код, 0)], [x[1] for x in в if x[0] == "ioctl"])
        self.assertEqual(("close",), в[-1])

    def test_sio_rcvall_без_прав(self):
        отказ = OSError(errno.EACCES, "Permission denied")
        отказ.winerror = 10013
        with self.assertRaises(ОшибкаЗахвата) as к:
            istochniki.ЗахватSioRcvall("10.0.0.5", фабрика=lambda *а: ПоддельныйСокет(ошибка_создания=отказ))
        self.assertEqual("права", к.exception.вид)
        self.assertIn("администратора", str(к.exception))
        нет_адреса = OSError(errno.EADDRNOTAVAIL, "x")
        сокеты = []
        with self.assertRaises(ОшибкаЗахвата) as к:
            istochniki.ЗахватSioRcvall("10.9.9.9", фабрика=lambda *а: сокеты.append(ПоддельныйСокет(ошибка_bind=нет_адреса)) or сокеты[-1])
        self.assertEqual("адрес", к.exception.вид)
        self.assertEqual(("close",), сокеты[0].вызовы[-1])

    def test_возможности_и_выбор_без_npcap(self):
        отказ = OSError(errno.EACCES, "denied")
        нет_прав = istochniki.возможности(None, "Npcap не установлен", windows=True,
                                          фабрика=lambda *а: ПоддельныйСокет(ошибка_создания=отказ))
        self.assertEqual({"udp", "npcap", "sio_rcvall"}, set(нет_прав))
        self.assertFalse(нет_прав["npcap"]["можно"])
        self.assertEqual("Npcap не установлен", нет_прав["npcap"]["почему"])
        self.assertFalse(нет_прав["sio_rcvall"]["можно"])
        with self.assertRaises(ОшибкаЗахвата) as к:
            istochniki.выбрать_способ(нет_прав)
        self.assertEqual("права", к.exception.вид)
        self.assertIn("Npcap не установлен", str(к.exception))
        с_правами = istochniki.возможности(None, "Npcap не установлен", windows=True, фабрика=ПоддельныйСокет)
        self.assertEqual("sio_rcvall", istochniki.выбрать_способ(с_правами))
        with self.assertRaises(ОшибкаЗахвата) as к:
            istochniki.выбрать_способ(с_правами, "npcap")
        self.assertIn("Npcap недоступен", str(к.exception))
        self.assertEqual("нет", к.exception.вид)
        with self.assertRaises(ОшибкаЗахвата):
            istochniki.выбрать_способ(с_правами, "af_packet")
        self.assertEqual("sio_rcvall", istochniki.выбрать_способ(с_правами, "sio_rcvall"))
        linux = istochniki.возможности(None, "нет", windows=False, фабрика=ПоддельныйСокет)
        self.assertEqual({"udp", "libpcap", "af_packet"}, set(linux))
        self.assertEqual("af_packet", istochniki.выбрать_способ(linux, "авто"))
        lib = mock.Mock()
        lib.версия.return_value = "libpcap version 1.10"
        оба = istochniki.возможности(lib, "", windows=False, фабрика=ПоддельныйСокет)
        self.assertEqual(("libpcap", "libpcap version 1.10"), (istochniki.выбрать_способ(оба), оба["libpcap"]["версия"]))
        npcap = istochniki.возможности(lib, "", windows=True, фабрика=ПоддельныйСокет)
        self.assertEqual("npcap", istochniki.выбрать_способ(npcap))

    def test_udp_порт_занят(self):
        занятый = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        занятый.bind(("127.0.0.1", 0))
        self.addCleanup(занятый.close)
        порт = занятый.getsockname()[1]
        with self.assertRaises(ОшибкаЗахвата) as к:
            istochniki.ПриёмUDP("127.0.0.1", [свободный_порт(), порт])
        self.assertEqual("занято", к.exception.вид)
        self.assertIn(str(порт), str(к.exception))

    def test_udp_все_адреса_и_адрес_получателя(self):
        порт = свободный_порт()
        источник = istochniki.ПриёмUDP("0.0.0.0", [порт])
        self.addCleanup(источник.закрыть)
        отправитель = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(отправитель.close)
        отправитель.sendto(b"dst?", ("127.0.0.1", порт))
        пачка = []
        дождаться(lambda: пачка.extend(источник.прочитать(0.2)) or пачка)
        р = zapis.разобрать_кадр(пачка[0][1])
        self.assertEqual((b"dst?", порт, "127.0.0.1"), (р.нагрузка, р.порт_к, р.от))
        if источник.pktinfo:
            self.assertEqual("127.0.0.1", р.к, "адрес получателя — из IP_PKTINFO")
            self.assertEqual(0, источник.отброшено())

    def test_udp_ipv6(self):
        if not socket.has_ipv6:
            self.skipTest("нет IPv6")
        try:
            источник = istochniki.ПриёмUDP("::1", [0])
        except ОшибкаЗахвата as ошибка:
            self.skipTest(f"::1 недоступен: {ошибка}")
        self.addCleanup(источник.закрыть)
        порт = источник.сокеты[0].getsockname()[1]
        источник.порт_сокета[источник.сокеты[0]] = порт
        о = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.addCleanup(о.close)
        о.sendto(b"v6", ("::1", порт))
        пачка = []
        дождаться(lambda: пачка.extend(источник.прочитать(0.2)) or пачка)
        р = zapis.разобрать_кадр(пачка[0][1])
        self.assertEqual((6, "::1", "::1", b"v6"), (р.версия, р.от, р.к, р.нагрузка))


@unittest.skipUnless(sys.platform.startswith("linux") and можно_af_packet(), "AF_PACKET: нужен root или CAP_NET_RAW")
class AFPacketTests(unittest.TestCase):
    def test_на_петле_с_фильтром_по_порту(self):
        порт = свободный_порт()
        источник = istochniki.ЗахватAFPacket("lo", arphrd=772, неразборчиво=True)
        self.addCleanup(источник.закрыть)
        о = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(о.close)
        for i in range(3):
            о.sendto(b"P%d" % i, ("127.0.0.1", порт))
            о.sendto(b"other", ("127.0.0.1", порт + 1 if порт < 65535 else порт - 1))
        ф = Фильтр(протокол="udp", порт=порт)
        свои = []
        def собрать():
            for _, кадр, _ in источник.прочитать(0.2):
                р = zapis.разобрать_кадр(кадр)
                if ф.подходит(р):
                    свои.append(р.нагрузка)
            return len(свои) >= 3
        self.assertTrue(дождаться(собрать, 5))
        self.assertEqual([b"P0", b"P1", b"P2"], свои, "на петле исходящие не дублируются")
        self.assertEqual(0, источник.отброшено())

    @unittest.skipUnless(путь_libpcap(), "нужна libpcap для BPF")
    def test_bpf_в_ядре(self):
        lib = pcap_bib.Libpcap(путь_libpcap())
        порт = свободный_порт()
        выражение = Фильтр(протокол="udp", порт=порт).bpf()
        источник = istochniki.ЗахватAFPacket("lo", arphrd=772, bpf=lib.скомпилировать(выражение), bpf_текст=выражение)
        self.addCleanup(источник.закрыть)
        self.assertEqual(выражение, источник.bpf)
        о = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(о.close)
        time.sleep(0.1)
        источник.прочитать(0.05)                        # что успело прийти до фильтра — прочь
        о.sendto(b"no", ("127.0.0.1", порт ^ 1 or 1))
        о.sendto(b"yes", ("127.0.0.1", порт))
        кадры = []
        дождаться(lambda: кадры.extend(источник.прочитать(0.2)) or кадры, 5)
        time.sleep(0.2)
        кадры += источник.прочитать(0.1)
        self.assertEqual([b"yes"], [zapis.разобрать_кадр(к).нагрузка for _, к, _ in кадры])

    def test_неизвестная_карта(self):
        with self.assertRaises(ОшибкаЗахвата) as к:
            istochniki.ЗахватAFPacket("нет-такой0", arphrd=1)
        self.assertEqual("адрес", к.exception.вид)


# -- менеджер ------------------------------------------------------------------------------

class МенеджерTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp()) / "zahvat"
        self.м = Менеджер(self.папка, путь_libpcap="/нет/libpcap.so", потолок_секунд=30, потолок_байт=4 << 20)
        self.addCleanup(self.м.остановить_все)
        self.о = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.о.close)

    def начать_udp(self, порт, **пределы):
        п = проверить({"режим": "udp", "адрес": "127.0.0.1", "порты": [порт], "пределы": пределы},
                      потолок_секунд=30, потолок_байт=4 << 20)
        return self.м.начать(п, владелец=1, кто="Инженеров")

    def дождаться_конца(self, ид, секунд=10):
        self.assertTrue(дождаться(lambda: self.м.состояние(ид)["состояние"] != "идёт", секунд))
        return self.м.состояние(ид)

    def test_udp_до_предела_пакетов(self):
        порт = свободный_порт()
        ид = self.начать_udp(порт, пакетов=5)
        with self.assertRaises(ОшибкаЗахвата) as к:
            self.начать_udp(порт)
        self.assertEqual("занято", к.exception.вид)
        for i in range(8):
            self.о.sendto(b"%02d" % i, ("127.0.0.1", порт))
            time.sleep(0.005)
        с_ = self.дождаться_конца(ид)
        self.assertEqual(("готово", "достигнут предел пакетов", 5), (с_["состояние"], с_["причина"], с_["пакетов"]))
        self.assertEqual({str(порт): [5, 10]}, с_["по_портам"])
        self.assertEqual(["127.0.0.1"], sorted({к.split(":")[0] for к in с_["источники"]}))
        захват = прочитать_захват(self.м.файл(ид))
        self.assertEqual([b"%02d" % i for i in range(5)], [zapis.разобрать_кадр(з.данные).нагрузка for з in захват.записи])
        self.assertEqual(с_["файл_байт"], self.м.файл(ид).stat().st_size)
        ид2 = self.начать_udp(порт, пакетов=1)                  # порт освобождён
        self.м.остановить(ид2)
        self.assertEqual("остановлен по команде", self.м.состояние(ид2)["причина"])

    def test_предел_времени_и_объёма(self):
        порт = свободный_порт()
        ид = self.начать_udp(порт, секунд=1)
        с_ = self.дождаться_конца(ид, 5)
        self.assertEqual(("готово", "достигнут предел времени", 0), (с_["состояние"], с_["причина"], с_["пакетов"]))
        self.assertGreaterEqual(с_["длится"], 0.9)
        ид = self.начать_udp(свободный_порт(), мегабайт=1)
        порт = self.м.состояние(ид)["порты"][0]
        данные = b"\xaa" * 60000
        for _ in range(40):
            self.о.sendto(данные, ("127.0.0.1", порт))
            time.sleep(0.002)
        с_ = self.дождаться_конца(ид)
        self.assertEqual("достигнут предел объёма", с_["причина"])
        self.assertLessEqual(self.м.файл(ид).stat().st_size, 1 << 20)
        self.assertGreater(self.м.файл(ид).stat().st_size, (1 << 20) - 70000)
        self.assertEqual(с_["пакетов"], len(прочитать_захват(self.м.файл(ид)).записи))

    def test_скорость_по_отсчётам(self):
        self.assertEqual((0.0, 0.0), Менеджер.скорость([], 10.0, 0, 0))
        отсчёты = [(1.0, 0, 0), (8.0, 70, 7000), (9.0, 80, 8000)]
        # База — последний отсчёт до окна (1.0): (100 − 0) / 9 с.
        self.assertEqual((11.1, 8888.9), Менеджер.скорость(отсчёты, 10.0, 100, 10000))
        # Все отсчёты в окне — база самый ранний из них: (100 − 70) / 2 с.
        self.assertEqual((15.0, 12000.0), Менеджер.скорость(отсчёты[1:], 10.0, 100, 10000))
        self.assertEqual((1.0, 80.0), Менеджер.скорость([(0.0, 0, 0)], 100.0, 100, 1000))
        self.assertEqual((0.0, 0.0), Менеджер.скорость([(5.0, 1, 1)], 5.0, 1, 1))

    def test_пределы_одновременности(self):
        порты = [свободный_порт() for _ in range(3)]
        иды = [self.начать_udp(порты[0]), self.начать_udp(порты[1])]
        with self.assertRaises(ОшибкаЗахвата) as к:
            self.начать_udp(порты[2])
        self.assertIn("у вас уже идут 2", str(к.exception))
        self.assertEqual(2, len(self.м.идущие()))
        for ид in иды:
            self.assertTrue(self.м.остановить(ид))
        self.assertFalse(self.м.остановить(иды[0]))

    def test_мало_места(self):
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10 << 20)):
            with self.assertRaises(ОшибкаЗахвата) as к:
                self.начать_udp(свободный_порт())
        self.assertIn("мало места", str(к.exception))

    def test_нет_такой_карты_и_занятость_карты(self):
        п = проверить({"режим": "карта", "карта": "нет-такой"}, потолок_секунд=30, потолок_байт=4 << 20)
        with self.assertRaises(ОшибкаЗахвата) as к:
            self.м.начать(п, владелец=1, кто="")
        self.assertEqual("адрес", к.exception.вид)

    def test_прежние_прерваны_и_уборка(self):
        старый = self.папка / "20260101-000000-abcdef"
        старый.mkdir(parents=True)
        (старый / "состояние.json").write_text(json.dumps({"ид": старый.name, "владелец": 1, "состояние": "идёт",
                                                           "начато": 1.0, "имя": "x"}), encoding="utf-8")
        м = Менеджер(self.папка)
        с_ = м.состояние(старый.name)
        self.assertEqual(("прерван", "сервер перезапускался во время захвата"), (с_["состояние"], с_["причина"]))
        for плохой in ("../x", "20260101-000000-ABCDEF", ""):
            with self.assertRaises(KeyError):
                м.состояние(плохой)
            with self.assertRaises(KeyError):
                м.файл(плохой)
            with self.assertRaises(KeyError):
                м.удалить(плохой)
        for i in range(12):
            п = self.папка / f"20260101-0000{i:02d}-00000{i % 10}"
            п.mkdir()
            (п / "состояние.json").write_text(json.dumps({"ид": п.name, "владелец": 2, "состояние": "готово",
                                                          "начато": float(i), "имя": str(i)}))
        м._прибрать(2)
        self.assertEqual(10, len(м.список(2)))
        self.assertEqual("11", м.список(2)[0]["имя"], "остаются новые")
        self.assertEqual(1, len(м.список(1)))
        м.отметить(старый.name, в_пакетах="x")
        self.assertEqual("x", м.состояние(старый.name)["в_пакетах"])

    @unittest.skipUnless(sys.platform.startswith("linux") and можно_af_packet(), "AF_PACKET: нужен root")
    def test_захват_с_карты_af_packet(self):
        порт = свободный_порт()
        п = проверить({"режим": "карта", "карта": "lo", "способ": "af_packet",
                       "фильтр": {"протокол": "udp", "порт": порт}, "пределы": {"пакетов": 3}},
                      потолок_секунд=30, потолок_байт=4 << 20)
        # Без libpcap BPF не собрать — отбор идёт в программе, и отфильтрованные видны в счётчике.
        with mock.patch.object(self.м, "библиотека", return_value=(None, "нет")):
            ид = self.м.начать(п, владелец=1, кто="")
        with self.assertRaises(ОшибкаЗахвата) as к:
            self.м.начать(п, владелец=2, кто="")
        self.assertIn("карта уже занята", str(к.exception))
        lo = next(к for к in self.м.карты()[0] if к["ид"] == "lo")
        self.assertEqual(ид, lo["занята"]["ид"])
        time.sleep(0.2)
        for i in range(5):
            self.о.sendto(b"C%d" % i, ("127.0.0.1", порт))
            self.о.sendto(b"-", ("127.0.0.1", порт ^ 1 or 1))
            time.sleep(0.01)
        с_ = self.дождаться_конца(ид)
        self.assertEqual(("af_packet", 3, "достигнут предел пакетов"), (с_["способ"], с_["пакетов"], с_["причина"]))
        self.assertEqual("", с_["bpf"])
        self.assertGreater(с_["отфильтровано"], 0)
        self.assertEqual(b"C0C1C2", obrabotka.нагрузка(self.м.файл(ид), порт)[0])
        self.assertIsNone(next(к for к in self.м.карты()[0] if к["ид"] == "lo")["занята"])

    @unittest.skipUnless(путь_libpcap() and можно_af_packet(), "нужна libpcap и права")
    def test_захват_libpcap_и_стоп(self):
        м = Менеджер(self.папка, путь_libpcap=путь_libpcap(), потолок_секунд=30, потолок_байт=4 << 20)
        self.addCleanup(м.остановить_все)
        порт = свободный_порт()
        п = проверить({"режим": "карта", "карта": "lo", "способ": "libpcap", "фильтр": {"порт": порт}},
                      потолок_секунд=30, потолок_байт=4 << 20)
        ид = м.начать(п, владелец=1, кто="")
        time.sleep(0.3)
        self.о.sendto(b"L", ("127.0.0.1", порт))
        self.assertTrue(дождаться(lambda: м.состояние(ид)["пакетов"] >= 1, 5))
        начало = time.time()
        self.assertTrue(м.остановить(ид))
        self.assertLess(time.time() - начало, 2, "pcap_breakloop будит поток")
        с_ = м.состояние(ид)
        self.assertEqual(("libpcap", "готово", "остановлен по команде"), (с_["способ"], с_["состояние"], с_["причина"]))
        self.assertIn("udp port", с_["bpf"])


# -- обработка: RTP и нагрузка порта ---------------------------------------------------------

def rtp(номер, нагрузка, *, ssrc=0x1234, cc=0, x=b"", p=0):
    первый = 0x80 | (0x20 if p else 0) | (0x10 if x else 0) | cc
    return bytes([первый, 96]) + struct.pack("!HII", номер & 0xFFFF, номер * 160, ssrc) + b"\0\0\0\1" * cc + x + \
        нагрузка + (bytes([0] * (p - 1) + [p]) if p else b"")


class ОбработкаTests(unittest.TestCase):
    def файл(self, датаграммы):
        путь = Path(tempfile.mkdtemp()) / "захват.pcapng"
        with open(путь, "wb") as f:
            п = zapis.ПисательPcapng(f)
            for i, (порт, от, данные) in enumerate(датаграммы):
                п.пакет(float(i), zapis.кадр_udp(данные, от, 40000, "10.0.0.1", порт))
        return путь

    def test_заголовок_rtp(self):
        self.assertEqual((7, 0x1234, 12, 0), obrabotka.заголовок_rtp(rtp(7, b"x")))
        self.assertEqual((8, 0x1234, 20, 0), obrabotka.заголовок_rtp(rtp(8, b"x", cc=2)))
        расш = struct.pack("!HH", 0xBEDE, 2) + b"\0" * 8
        self.assertEqual((9, 0x1234, 24, 0), obrabotka.заголовок_rtp(rtp(9, b"x", x=расш)))
        self.assertEqual((10, 0x1234, 12, 3), obrabotka.заголовок_rtp(rtp(10, b"abc", p=3)))
        self.assertIsNone(obrabotka.заголовок_rtp(b"\x40" + b"\0" * 20), "версия 1")
        self.assertIsNone(obrabotka.заголовок_rtp(b"\x80" * 11))
        self.assertIsNone(obrabotka.заголовок_rtp(bytes([0x8F]) + b"\0" * 20), "CSRC не помещаются")
        self.assertIsNone(obrabotka.заголовок_rtp(bytes([0x90]) + b"\0" * 12), "нет места под расширение")
        self.assertIsNone(obrabotka.заголовок_rtp(bytes([0xA0]) + b"\0" * 12), "P = 1, а длина дополнения 0")
        self.assertIsNone(obrabotka.заголовок_rtp(bytes([0xA0]) + b"\0" * 11 + b"\x10"), "дополнение длиннее датаграммы")

    def test_расширенные_номера(self):
        self.assertEqual([65534, 65535, 65536, 65537, 65535], obrabotka.расширить_номера([65534, 65535, 0, 1, 65535]))
        self.assertEqual([5, 3, 6], obrabotka.расширить_номера([5, 3, 6]))
        self.assertEqual([0, -1, 1], obrabotka.расширить_номера([0, 65535, 1]))
        self.assertEqual([], obrabotka.расширить_номера([]))

    def test_нагрузка_без_среза_и_со_срезом(self):
        путь = self.файл([(5004, "10.0.0.7", rtp(1, b"AAAA")), (5006, "10.0.0.7", b"other"),
                          (5004, "10.0.0.8", rtp(2, b"BBBB")), (5004, "10.0.0.7", b"\x80")])
        поток, заметки, сводка = obrabotka.нагрузка(путь, 5004)
        self.assertEqual(rtp(1, b"AAAA") + rtp(2, b"BBBB") + b"\x80", поток)
        self.assertEqual(3, сводка["датаграмм"])
        поток, заметки, сводка = obrabotka.нагрузка(путь, 5004, срез=12)
        self.assertEqual(b"AAAABBBB", поток)
        self.assertEqual(1, сводка["короче_среза"])
        self.assertTrue(any("короче среза: 1" in з for з in заметки))
        поток, _, _ = obrabotka.нагрузка(путь, 5004, срез=12, источник="10.0.0.8")
        self.assertEqual(b"BBBB", поток)
        поток, заметки, сводка = obrabotka.нагрузка(путь, 5004, срез="rtp")
        self.assertEqual((b"AAAABBBB", 1), (поток, сводка["не_rtp"]))
        self.assertIn("не RTP", " ".join(заметки))
        self.assertEqual([{"порт": 5004, "датаграмм": 3, "байт": 33,
                           "источники": [{"адрес": "10.0.0.7", "датаграмм": 2}, {"адрес": "10.0.0.8", "датаграмм": 1}]},
                          {"порт": 5006, "датаграмм": 1, "байт": 5, "источники": [{"адрес": "10.0.0.7", "датаграмм": 1}]}],
                         obrabotka.порты(путь))
        поток, заметки, сводка = obrabotka.нагрузка(путь, 5004, предел=20)
        self.assertEqual((rtp(1, b"AAAA"), True), (поток, сводка["обрезано_пределом"]))
        self.assertIn("обрезана пределом", заметки[-1])

    def test_упорядочивание_rtp(self):
        порядок = [65533, 65535, 65534, 0, 0, 3, 2]            # перестановки, повтор, потеря номера 1, переход через 0
        путь = self.файл([(7000, "10.0.0.7", rtp(н, bytes([н & 0xFF]), p=2 if н == 3 else 0)) for н in порядок] +
                         [(7000, "10.0.0.7", rtp(9, b"S", ssrc=0x99))])
        поток, заметки, сводка = obrabotka.нагрузка(путь, 7000, срез="rtp", упорядочить=True)
        self.assertEqual(bytes([0xFD, 0xFE, 0xFF, 0, 2, 3]) + b"S", поток)
        self.assertEqual((2, 1, 1, 0), (сводка["переставлено_rtp"], сводка["пропущено_rtp"], сводка["повторы_rtp"],
                                        сводка["разрывы_rtp"]))
        self.assertEqual(["00001234", "00000099"], сводка["ssrc"])
        self.assertTrue(any("несколько источников RTP" in з for з in заметки))
        поток, _, сводка = obrabotka.нагрузка(путь, 7000, срез=12, упорядочить=True)
        self.assertEqual(bytes([0xFD, 0xFE, 0xFF, 0, 2, 3, 0, 2]) + b"S", поток, "срез числом: дополнение RTP остаётся")
        скачок = self.файл([(7000, "10.0.0.7", rtp(н, b"z")) for н in (1, 2, 5000)])
        _, заметки, сводка = obrabotka.нагрузка(скачок, 7000, срез="rtp", упорядочить=True)
        self.assertEqual((1, 0), (сводка["разрывы_rtp"], сводка["пропущено_rtp"]))
        self.assertIn("разрывов нумерации 1", " ".join(заметки))

    def test_фрагменты_и_raw_ip(self):
        путь = Path(tempfile.mkdtemp()) / "raw.pcapng"
        with open(путь, "wb") as f:
            п = zapis.ПисательPcapng(f, канал=zapis.LINKTYPE_RAW)
            п.пакет(0.0, с.ip(с.udp(b"raw", 1, 5), 17))
            п.пакет(1.0, с.ip(с.udp(b"frag", 1, 5), 17, флаги=0x2000))
        поток, заметки, сводка = obrabotka.нагрузка(путь, 5)
        self.assertEqual((b"raw", 1), (поток, сводка["фрагменты"]))
        self.assertIn("фрагментированные", " ".join(заметки))
        прочий = Path(tempfile.mkdtemp()) / "ppp.pcapng"
        with open(прочий, "wb") as f:
            zapis.ПисательPcapng(f, канал=9).пакет(0.0, b"\xff\x03\x00\x21" + с.ip(с.udp(b"p", 1, 5), 17))
        self.assertEqual([], obrabotka.порты(прочий))


# -- сервер: права, проверка входа, аудит, обработка -------------------------------------------

class ЗахватСервераTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — тяжёлый импорт только здесь

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client
        self.settings = self.сеть.app.state.settings
        self.settings.capture_libpcap = "/нет/libpcap.so"
        self.addCleanup(lambda: getattr(self.сеть.app.state, "zahvat_seti", None) and
                        self.сеть.app.state.zahvat_seti.остановить_все())
        self.о = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.о.close)

    def действия(self):
        return [з.action for з in self.сеть.repos.audit.list()]

    def test_права_по_должностям(self):
        self.сеть.login("engineer")
        ответ = self.к.get("/api/zahvat-karty")
        self.assertEqual(200, ответ.status_code, ответ.text)
        данные = ответ.json()
        self.assertIn("udp", данные["sposoby"])
        self.assertEqual({"секунд": 3600, "мегабайт": 200}, данные["potolki"])
        if Path("/sys/class/net/lo").exists():
            self.assertIn("lo", [к["ид"] for к in данные["karty"]])
        self.settings.capture_min_role = "lead"
        ответ = self.к.get("/api/zahvat")
        self.assertEqual(403, ответ.status_code)
        self.assertIn("Начальник группы", ответ.json()["error"])
        self.assertEqual(403, self.к.post("/api/zahvat", json={"режим": "udp", "порты": "5000"}).status_code)
        self.assertIn("zahvat.denied", self.действия())
        self.сеть.login("gruppa")
        self.assertEqual(200, self.к.get("/api/zahvat").status_code)
        self.settings.capture_min_role = "off"
        self.assertIn("выключен", self.к.get("/api/zahvat").json()["error"])
        self.settings.capture_min_role = "начальник"
        self.assertIn("неизвестная должность", self.к.get("/api/zahvat").json()["error"])
        self.settings.capture_min_role = "engineer"
        self.сеть.repos.users.create("gost", "пароль123", "Гостев Г. Г.", "guest")
        self.сеть.login("gost")
        self.assertEqual(403, self.к.get("/api/zahvat-karty").status_code)

    def test_плохие_входные_данные(self):
        self.сеть.login("engineer")
        for тело in ({"режим": "udp", "порты": "0"}, {"режим": "udp", "порты": "65536"}, {"режим": "udp", "порты": "x"},
                     {"режим": "udp", "порты": "5000", "адрес": "300.1.1.1"}, {"режим": "никакой"},
                     {"режим": "udp", "порты": "5000", "пределы": {"секунд": 3601}},
                     {"режим": "udp", "порты": "5000", "пределы": {"мегабайт": 201}},
                     {"режим": "карта", "карта": "lo", "фильтр": {"от": "1.2.3"}}):
            ответ = self.к.post("/api/zahvat", json=тело)
            self.assertEqual(400, ответ.status_code, тело)
            self.assertTrue(ответ.json()["error"])
        self.assertEqual(400, self.к.post("/api/zahvat", json={"режим": "карта", "карта": "нет-такой"}).status_code)
        self.assertEqual(404, self.к.get("/api/zahvat/..%2F..%2Fetc").status_code)
        self.assertEqual(404, self.к.get("/api/zahvat/20260101-000000-abcdef").status_code)

    def начать(self, порт, **пределы):
        ответ = self.к.post("/api/zahvat", json={"режим": "udp", "адрес": "127.0.0.1", "порты": str(порт),
                                                 "пределы": пределы, "имя": "Модем 2"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()["id"]

    def test_весь_путь_udp(self):
        self.сеть.login("engineer")
        порт = свободный_порт()
        ид = self.начать(порт, пакетов=4)
        ответ = self.к.post("/api/zahvat", json={"режим": "udp", "адрес": "127.0.0.1", "порты": str(порт)})
        self.assertEqual(409, ответ.status_code)
        self.assertIn("уже занят", ответ.json()["error"])
        self.assertEqual(409, self.к.get(f"/api/zahvat/{ид}/ports").status_code, "пока идёт — обработки нет")
        датаграммы = [rtp(i, b"%c" % (65 + i) * 3) for i in range(4)]
        for д in датаграммы:
            self.о.sendto(д, ("127.0.0.1", порт))
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/zahvat/{ид}").json()["состояние"] == "готово"))
        с_ = self.к.get(f"/api/zahvat/{ид}").json()
        self.assertEqual((4, "Модем 2", "Инженеров И. И."), (с_["пакетов"], с_["имя"], с_["кто"]))
        self.assertEqual([{"порт": порт, "датаграмм": 4, "байт": 60,
                           "источники": [{"адрес": "127.0.0.1", "датаграмм": 4}]}],
                         self.к.get(f"/api/zahvat/{ид}/ports").json()["ports"])
        # В анализатор пакетов: захват разобран как обычный pcapng.
        пакеты = self.к.post(f"/api/zahvat/{ид}/to-pakety", json={}).json()["id"]
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/pakety/{пакеты}").json()["состояние"] in ("готово", "ошибка")))
        разбор = self.к.get(f"/api/pakety/{пакеты}").json()
        self.assertEqual(("готово", "pcapng", 4, "Модем 2.pcapng", ""), (разбор["состояние"], разбор["формат"],
                                                                         разбор["пакетов"], разбор["имя"], разбор["от"]))
        уровни = [у["протокол"] for у in self.к.get(f"/api/pakety/{пакеты}/packet/1").json()["уровни"]]
        self.assertEqual(["Ethernet", "IPv4", "UDP"], уровни[:3])
        self.assertEqual(пакеты, self.к.post(f"/api/zahvat/{ид}/to-pakety", json={}).json()["id"], "второй раз — тот же")
        # В сессию: биты = нагрузка без среза.
        сид = self.к.post("/api/sessions", json={"name": "Стенд"}).json()["id"]
        ответ = self.к.post(f"/api/zahvat/{ид}/to-session", json={"session": сид, "port": порт})
        self.assertEqual(200, ответ.status_code, ответ.text)
        работа = ответ.json()
        self.assertEqual((b"".join(датаграммы), "msb"), (self.сырые(работа["id"]), работа["bit_order"]))
        ответ = self.к.post(f"/api/zahvat/{ид}/to-session", json={"session": сид, "port": порт, "cut": "rtp",
                                                                   "rtp_order": True, "bit_order": "lsb"})
        работа = ответ.json()
        развёрнутые = bytes(int(f"{б:08b}"[::-1], 2) for б in b"AAABBBCCCDDD")
        self.assertEqual(развёрнутые, self.сырые(работа["id"]))
        состояние = self.к.get(f"/api/potok/{работа['id']}").json()
        self.assertEqual(сид, состояние["сессия"])
        self.assertTrue(any("захват с сети" in з for з in состояние["происхождение"]))
        self.assertTrue(any("упорядочено по номеру RTP" in з for з in состояние["происхождение"]))
        for тело, код in (({"session": сид, "port": 0}, 400), ({"session": сид, "port": порт, "cut": -1}, 400),
                          ({"session": сид, "port": порт, "bit_order": "x"}, 400),
                          ({"session": сид, "port": порт + 1 if порт < 65535 else 1}, 400),
                          ({"session": сид, "port": порт, "source": "10.0.0.x"}, 400),
                          ({"session": "000000000000", "port": порт}, 404)):
            self.assertEqual(код, self.к.post(f"/api/zahvat/{ид}/to-session", json=тело).status_code, тело)
        # Файл, чужой захват, аудит, удаление.
        файл = self.к.get(f"/api/zahvat/{ид}/file")
        self.assertEqual(200, файл.status_code)
        self.assertEqual(b"\x0a\x0d\x0d\x0a", файл.content[:4])
        self.assertIn("Модем 2.pcapng", urllib_unquote(файл.headers["content-disposition"]))
        действия = self.действия()
        for нужное in ("zahvat.start", "zahvat.pakety", "zahvat.session"):
            self.assertIn(нужное, действия)
        self.сеть.login("zam")
        self.assertEqual(200, self.к.get(f"/api/zahvat/{ид}").status_code, "администратор видит чужой захват")
        self.assertEqual(404, self.к.post(f"/api/zahvat/{ид}/to-pakety", json={}).status_code)
        self.assertEqual(404, self.к.delete(f"/api/zahvat/{ид}").status_code)
        self.сеть.login("engineer")
        self.assertEqual(200, self.к.delete(f"/api/zahvat/{ид}").status_code)
        self.assertEqual(404, self.к.get(f"/api/zahvat/{ид}").status_code)
        self.assertIn("zahvat.delete", self.действия())

    def сырые(self, job):
        self.assertTrue(дождаться(lambda: self.к.get(f"/api/potok/{job}").json()["состояние"] in ("готово", "ошибка")))
        return self.к.get(f"/api/potok/{job}/raw").content

    def test_стоп_и_чужой_инженер(self):
        self.сеть.repos.users.create("engineer2", "пароль123", "Другов Д. Д.", "engineer")
        self.сеть.login("engineer")
        порт = свободный_порт()
        ид = self.начать(порт)
        self.сеть.login("engineer2")
        self.assertEqual(404, self.к.get(f"/api/zahvat/{ид}").status_code)
        self.assertEqual(404, self.к.post(f"/api/zahvat/{ид}/stop", json={}).status_code)
        ответ = self.к.post("/api/zahvat", json={"режим": "udp", "адрес": "127.0.0.1", "порты": str(порт)})
        self.assertIn("Инженеров", ответ.json()["error"])
        self.сеть.login("gruppa")
        ответ = self.к.post(f"/api/zahvat/{ид}/stop", json={})
        self.assertEqual(200, ответ.status_code)
        self.assertEqual("готово", ответ.json()["состояние"])
        self.assertIn("остановлен по команде (Группин", ответ.json()["причина"])
        self.assertIn("zahvat.stop", self.действия())
        self.сеть.login("engineer")
        self.assertEqual([ид], [з["ид"] for з in self.к.get("/api/zahvat").json()["items"]])

    def test_хранилище_не_в_копию(self):
        места = {м["имя"]: м for м in Settings.load().storage()}
        self.assertFalse(места["zahvat"]["в_копию"])
        self.assertEqual([], [т for т in settings_warnings(Settings.load()) if "capture" in т])
        self.assertTrue([т for т in settings_warnings(Settings.load(capture_min_role="x")) if "capture_min_role" in т])
        self.assertEqual([], [т for т in settings_warnings(Settings.load(capture_min_role="off")) if "capture" in т])


NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node не установлен")
class СтраницаTests(unittest.TestCase):
    """Помощники страницы «Захват с сети» — вырезаются из app.js и выполняются в node."""

    @classmethod
    def setUpClass(cls):
        from test_oblik import вырезать  # noqa: PLC0415
        cls.js = (Path(_bootstrap.ROOT) / "src" / "reportgen" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        cls.вырезать = staticmethod(вырезать)

    def выполнить(self, код: str):
        итог = subprocess.run([NODE, "-e", код], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(0, итог.returncode, итог.stderr)
        return json.loads(итог.stdout.strip().splitlines()[-1])

    def test_скорость_и_адреса_словами(self):
        код = self.вырезать(self.js, "fmtBitRate") + "\n" + self.вырезать(self.js, "адресаКарты") + r"""
        console.log(JSON.stringify({
            бит: [0, 999, 1000, 1500, 999999, 2500000, 1e9, 3.5e12, 'x'].map(fmtBitRate),
            адреса: адресаКарты({ ipv4: [{ адрес: '10.0.0.1', префикс: 24 }, { адрес: '10.0.0.2', префикс: null }],
                                  ipv6: [{ адрес: 'fe80::1', префикс: 64 }, { адрес: '::1', префикс: 0 }] }),
            пусто: адресаКарты({}),
        }));"""
        итог = self.выполнить(код)
        self.assertEqual(["0 бит/с", "999 бит/с", "1,0 кбит/с", "1,5 кбит/с", "1000,0 кбит/с", "2,5 Мбит/с",
                          "1,0 Гбит/с", "3500,0 Гбит/с", "0 бит/с"], итог["бит"])
        self.assertEqual(["10.0.0.1/24", "10.0.0.2", "fe80::1/64", "::1/0"], итог["адреса"])
        self.assertEqual([], итог["пусто"])

    def test_маршрут_и_остановка_опроса(self):
        код = self.вырезать(self.js, "parseHash") + r"""
        console.log(JSON.stringify(['#/zahvat', '#/zahvat/20260929-150737-3deda1', '#/zahvat/a%2Fb'].map(parseHash)));"""
        self.assertEqual([{"name": "zahvat", "id": None}, {"name": "zahvat", "id": "20260929-150737-3deda1"},
                          {"name": "zahvat", "id": "a/b"}], self.выполнить(код))
        self.assertIn("zahvat: 'pakety'", self.js, "подсветка пункта «Пакеты» для экрана захвата")
        self.assertIn("остановитьОпросЗахватаСети();", self.вырезать(self.js, "renderRoute"))
        self.assertIn("renderZahvat(view, route.id)", self.вырезать(self.js, "рисоватьРаздел"))
        for страница in ("renderPakety", "renderSessions"):
            self.assertIn("href: '#/zahvat'", self.вырезать(self.js, страница), страница)
        код = self.вырезать(self.js, "остановитьОпросЗахватаСети") + r"""
        const захватСети = { таймер: setTimeout(() => { console.log('"не снят"'); process.exit(1); }, 50) };
        остановитьОпросЗахватаСети();
        остановитьОпросЗахватаСети();
        setTimeout(() => console.log(JSON.stringify(захватСети.таймер)), 100);"""
        self.assertIsNone(self.выполнить(код))


def urllib_unquote(текст: str) -> str:
    return urllib.parse.unquote(текст)


if __name__ == "__main__":
    unittest.main()
