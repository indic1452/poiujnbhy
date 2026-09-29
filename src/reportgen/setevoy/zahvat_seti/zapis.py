"""Запись захвата в pcapng и синтез заголовков Ethernet / IPv4 / IPv6 / UDP.

Формат файла — draft-ietf-opsawg-pcapng (раздел «General Block Structure»,
SHB §4.1, IDB §4.2, EPB §4.3, ISB §4.6); писатель сверен с libpcap
(``sf-pcapng.c``): файл, записанный отсюда, libpcap читает, кадры совпадают
побайтно. Порядок байт — little-endian (его объявляет магия SHB 0x1A2B3C4D,
читатель узнаёт порядок по ней). Время — в микросекундах: опция IDB
``if_tsresol`` = 6 (10^-6 с), единое 64-битное число в EPB (старшие 32 бита,
затем младшие).

Заголовки синтезируются, когда захвачена только нагрузка (приём UDP сокетом)
или только IP (сырой сокет Windows SIO_RCVALL не отдаёт уровень Ethernet):
так в анализаторе пакетов всё выглядит одинаково — кадр Ethernet II.

* Ethernet II — RFC 894: получатель (6), отправитель (6), EtherType (2).
  Адреса MAC синтезированного кадра — нули: настоящих мы не видели.
* IPv4 — RFC 791 §3.1: заголовок 20 байт (IHL = 5), контрольная сумма по
  заголовку.
* IPv6 — RFC 8200 §3: заголовок 40 байт.
* UDP — RFC 768: 8 байт; контрольная сумма по псевдозаголовку IPv4 (RFC 768)
  или IPv6 (RFC 8200 §8.1), вычисленный 0 передаётся как 0xFFFF.
* Контрольная сумма — дополнение до единицы суммы 16-битных слов, RFC 1071.
"""

from __future__ import annotations

import ipaddress
import platform
import struct
from typing import BinaryIO

from ..pole import сумма16

#: Типы канального уровня в файле (LINKTYPE_*, tcpdump.org/linktypes.html,
#: libpcap pcap-common.c). DLT_RAW (12) в файл пишется как LINKTYPE_RAW (101).
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
#: MAXIMUM_SNAPLEN из libpcap pcap-int.h — «без обрезки».
SNAPLEN = 262144

#: EtherType (RFC 894, реестр IEEE): IPv4, IPv6, ARP, метки VLAN 802.1Q и 802.1ad.
ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_IPV6 = 0x86DD
ETHERTYPE_ARP = 0x0806
ETHERTYPE_VLAN = (0x8100, 0x88A8)
#: Номера протоколов IP (реестр IANA): ICMP, TCP, UDP, ICMPv6.
IP_ICMP, IP_TCP, IP_UDP, IP_ICMPV6 = 1, 6, 17, 58

#: Типы блоков pcapng и магия порядка байт (draft-ietf-opsawg-pcapng, sf-pcapng.c).
БЛОК_SHB = 0x0A0D0D0A
БЛОК_IDB = 0x00000001
БЛОК_ISB = 0x00000005
БЛОК_EPB = 0x00000006
МАГИЯ_ПОРЯДКА = 0x1A2B3C4D
#: Коды опций: общие, SHB, IDB, ISB.
OPT_ENDOFOPT, OPT_COMMENT = 0, 1
SHB_HARDWARE, SHB_OS, SHB_USERAPPL = 2, 3, 4
IF_NAME, IF_DESCRIPTION, IF_IPV4ADDR, IF_IPV6ADDR, IF_MACADDR = 2, 3, 4, 5, 6
IF_SPEED, IF_TSRESOL, IF_FILTER = 8, 9, 11
ISB_STARTTIME, ISB_ENDTIME, ISB_IFRECV, ISB_IFDROP = 2, 3, 4, 5
ISB_FILTERACCEPT, ISB_OSDROP, ISB_USRDELIV = 6, 7, 8
#: if_tsresol = 6: единица времени 10^-6 с.
МИКРОСЕКУНДЫ = 6


def _добить(данные: bytes) -> bytes:
    """Дополнить нулями до границы 32 бит (все поля pcapng выровнены на 4)."""
    return данные + b"\0" * (-len(данные) % 4)


def опция(код: int, значение: bytes) -> bytes:
    """Опция TLV: код u16, длина u16 (без дополнения), значение с дополнением до 4."""
    return struct.pack("<HH", код, len(значение)) + _добить(значение)


def блок(вид: int, тело: bytes) -> bytes:
    """Блок: тип, полная длина, тело (дополненное до 4), полная длина ещё раз."""
    тело = _добить(тело)
    всего = 12 + len(тело)
    return struct.pack("<II", вид, всего) + тело + struct.pack("<I", всего)


def _опции(список: list[bytes]) -> bytes:
    return b"".join(список) + (struct.pack("<HH", OPT_ENDOFOPT, 0) if список else b"")


def _время(секунды: float) -> tuple[int, int]:
    """Время в единицах if_tsresol (мкс) — старшие и младшие 32 бита."""
    мкс = max(0, int(round(секунды * 1_000_000)))
    return (мкс >> 32) & 0xFFFFFFFF, мкс & 0xFFFFFFFF


def заголовок_секции(приложение: str = "reportgen") -> bytes:
    """SHB: магия порядка, версия 1.0, длина секции −1 («неизвестна»), ОС и приложение."""
    тело = struct.pack("<IHHq", МАГИЯ_ПОРЯДКА, 1, 0, -1)
    ос = f"{platform.system()} {platform.release()}".strip()
    тело += _опции([опция(SHB_OS, ос.encode("utf-8")), опция(SHB_USERAPPL, приложение.encode("utf-8"))])
    return блок(БЛОК_SHB, тело)


def описание_интерфейса(канал: int = LINKTYPE_ETHERNET, *, имя: str = "", описание: str = "",
                        фильтр: str = "", mac: bytes = b"", ipv4: tuple[str, int] | None = None,
                        скорость: int = 0, snaplen: int = SNAPLEN) -> bytes:
    """IDB: тип канала, snaplen и опции карты (имя, описание, MAC, адрес, скорость, фильтр)."""
    опции = []
    if имя:
        опции.append(опция(IF_NAME, имя.encode("utf-8")))
    if описание:
        опции.append(опция(IF_DESCRIPTION, описание.encode("utf-8")))
    if ipv4:
        адрес, префикс = ipv4
        сеть = ipaddress.IPv4Network(f"0.0.0.0/{префикс}")
        # if_IPv4addr: адрес и маска, по 4 байта в сетевом порядке (порядок SHB не влияет).
        опции.append(опция(IF_IPV4ADDR, ipaddress.IPv4Address(адрес).packed + сеть.netmask.packed))
    if len(mac) == 6:
        опции.append(опция(IF_MACADDR, bytes(mac)))
    if скорость > 0:
        опции.append(опция(IF_SPEED, struct.pack("<Q", скорость)))
    опции.append(опция(IF_TSRESOL, bytes([МИКРОСЕКУНДЫ])))
    if фильтр:
        # if_filter: первый октет — вид фильтра (0 — строка в синтаксисе libpcap).
        опции.append(опция(IF_FILTER, b"\0" + фильтр.encode("utf-8")))
    тело = struct.pack("<HHI", канал, 0, snaplen) + _опции(опции)
    return блок(БЛОК_IDB, тело)


def пакет_epb(время: float, данные: bytes, исходная_длина: int | None = None, интерфейс: int = 0) -> bytes:
    """EPB: номер интерфейса, время (мкс, старшие/младшие), длины записанного и исходного."""
    старшие, младшие = _время(время)
    исходно = max(len(данные), исходная_длина or 0)
    return блок(БЛОК_EPB, struct.pack("<IIIII", интерфейс, старшие, младшие, len(данные), исходно) + данные)


def статистика_isb(*, начало: float, конец: float, принято: int, отброшено: int | None,
                   отфильтровано_принято: int, доставлено: int, интерфейс: int = 0) -> bytes:
    """ISB в конце файла: время начала и конца захвата и счётчики (все u64)."""
    старшие, младшие = _время(конец)
    опции = [опция(ISB_STARTTIME, struct.pack("<II", *_время(начало))),
             опция(ISB_ENDTIME, struct.pack("<II", старшие, младшие)),
             опция(ISB_IFRECV, struct.pack("<Q", принято))]
    if отброшено is not None:
        опции.append(опция(ISB_IFDROP, struct.pack("<Q", отброшено)))
    опции += [опция(ISB_FILTERACCEPT, struct.pack("<Q", отфильтровано_принято)),
              опция(ISB_USRDELIV, struct.pack("<Q", доставлено))]
    return блок(БЛОК_ISB, struct.pack("<III", интерфейс, старшие, младшие) + _опции(опции))


class ПисательPcapng:
    """Потоковая запись: SHB и IDB сразу, затем EPB по мере прихода, в конце ISB.

    Файл годен к чтению в любой момент: оборвись захват (выключили сервер) —
    останутся SHB, IDB и целые EPB, а ISB просто не будет.
    """

    def __init__(self, файл: BinaryIO, **интерфейс):
        self.файл = файл
        self.записано = 0
        self._писать(заголовок_секции() + описание_интерфейса(**интерфейс))

    def _писать(self, данные: bytes) -> None:
        self.файл.write(данные)
        self.записано += len(данные)

    def пакет(self, время: float, данные: bytes, исходная_длина: int | None = None) -> None:
        self._писать(пакет_epb(время, данные, исходная_длина))

    def итог(self, **счётчики) -> None:
        self._писать(статистика_isb(**счётчики))
        self.файл.flush()


# -- синтез заголовков ---------------------------------------------------------------------

def ethernet(нагрузка: bytes, тип: int = ETHERTYPE_IPV4, *, получатель: bytes = b"\0" * 6,
             отправитель: bytes = b"\0" * 6) -> bytes:
    """Кадр Ethernet II (RFC 894) без FCS: так его отдают libpcap и AF_PACKET."""
    return bytes(получатель) + bytes(отправитель) + struct.pack("!H", тип) + нагрузка


def ipv4(нагрузка: bytes, протокол: int, отправитель: str, получатель: str, *, ttl: int = 64,
         ид: int = 0) -> bytes:
    """Заголовок IPv4 (RFC 791 §3.1) без опций, флаг DF, с контрольной суммой заголовка."""
    всего = 20 + len(нагрузка)
    if всего > 0xFFFF:
        raise ValueError(f"пакет IPv4 длиннее 65535 байт: {всего}")
    заголовок = struct.pack("!BBHHHBBH4s4s", 0x45, 0, всего, ид & 0xFFFF, 0x4000, ttl, протокол, 0,
                            ipaddress.IPv4Address(отправитель).packed,
                            ipaddress.IPv4Address(получатель).packed)
    сумма = сумма16(заголовок)
    return заголовок[:10] + struct.pack("!H", сумма) + заголовок[12:] + нагрузка


def ipv6(нагрузка: bytes, следующий: int, отправитель: str, получатель: str, *, hop: int = 64) -> bytes:
    """Заголовок IPv6 (RFC 8200 §3): версия 6, класс и метка потока — нули."""
    if len(нагрузка) > 0xFFFF:
        raise ValueError(f"нагрузка IPv6 длиннее 65535 байт: {len(нагрузка)}")
    return (struct.pack("!IHBB", 6 << 28, len(нагрузка), следующий, hop)
            + ipaddress.IPv6Address(отправитель).packed + ipaddress.IPv6Address(получатель).packed
            + нагрузка)


def псевдозаголовок(отправитель: str, получатель: str, протокол: int, длина: int) -> bytes:
    """Псевдозаголовок для контрольной суммы UDP/TCP: RFC 768 (IPv4) или RFC 8200 §8.1 (IPv6)."""
    от, к = ipaddress.ip_address(отправитель), ipaddress.ip_address(получатель)
    if от.version != к.version:
        raise ValueError("адреса отправителя и получателя разных версий IP")
    if от.version == 4:
        return от.packed + к.packed + struct.pack("!BBH", 0, протокол, длина)
    return от.packed + к.packed + struct.pack("!I3xB", длина, протокол)


def udp(нагрузка: bytes, порт_от: int, порт_к: int, отправитель: str, получатель: str) -> bytes:
    """Заголовок UDP (RFC 768) с контрольной суммой; вычисленный ноль пишется как 0xFFFF."""
    длина = 8 + len(нагрузка)
    if длина > 0xFFFF:
        raise ValueError(f"датаграмма UDP длиннее 65535 байт: {длина}")
    заголовок = struct.pack("!HHHH", порт_от, порт_к, длина, 0)
    сумма = сумма16(псевдозаголовок(отправитель, получатель, IP_UDP, длина) + заголовок + нагрузка)
    return struct.pack("!HHHH", порт_от, порт_к, длина, сумма or 0xFFFF) + нагрузка


def кадр_udp(нагрузка: bytes, отправитель: str, порт_от: int, получатель: str, порт_к: int) -> bytes:
    """Датаграмма, принятая сокетом, — целым кадром Ethernet / IP / UDP."""
    сегмент = udp(нагрузка, порт_от, порт_к, отправитель, получатель)
    if ipaddress.ip_address(отправитель).version == 4:
        return ethernet(ipv4(сегмент, IP_UDP, отправитель, получатель), ETHERTYPE_IPV4)
    return ethernet(ipv6(сегмент, IP_UDP, отправитель, получатель), ETHERTYPE_IPV6)


def кадр_из_ip(пакет: bytes) -> bytes:
    """Пакет IP без канального уровня (SIO_RCVALL, AF_PACKET SOCK_DGRAM) — в кадр Ethernet."""
    return ethernet(пакет, ETHERTYPE_IPV6 if пакет[:1] and пакет[0] >> 4 == 6 else ETHERTYPE_IPV4)


# -- разбор кадра Ethernet до UDP (для фильтра и для нагрузки порта) -------------------------

#: Заголовки расширения IPv6, которые можно перешагнуть (RFC 8200 §4): Hop-by-Hop,
#: Routing, Destination Options — длина в 8-октетных словах без первого.
РАСШИРЕНИЯ_IPV6 = (0, 43, 60)
ФРАГМЕНТ_IPV6 = 44


class Сведения:
    """Что нужно фильтру и выборке нагрузки из кадра: адреса, протокол, порты, нагрузка UDP."""

    __slots__ = ("тип", "версия", "от", "к", "протокол", "порт_от", "порт_к", "нагрузка", "фрагмент")

    def __init__(self) -> None:
        self.тип = 0
        self.версия = 0
        self.от = self.к = ""
        self.протокол = -1
        self.порт_от = self.порт_к = -1
        self.нагрузка: bytes | None = None
        self.фрагмент = False


def разобрать_кадр(кадр: bytes) -> Сведения:
    """Ethernet II (с метками VLAN) → IPv4/IPv6 → TCP/UDP: адреса, порты, нагрузка UDP.

    Нагрузка UDP берётся по полю длины UDP (RFC 768), а не по концу кадра: в
    коротком кадре Ethernet есть дополнение до 60 байт. Фрагменты IP не
    собираются — у них отмечается ``фрагмент``.
    """
    с = Сведения()
    if len(кадр) < 14:
        return с
    место, тип = 14, struct.unpack_from("!H", кадр, 12)[0]
    while тип in ETHERTYPE_VLAN and len(кадр) >= место + 4:     # 802.1Q / 802.1ad: TCI, затем тип
        тип = struct.unpack_from("!H", кадр, место + 2)[0]
        место += 4
    с.тип = тип
    if тип == ETHERTYPE_IPV4 and len(кадр) >= место + 20 and кадр[место] >> 4 == 4:
        ihl = (кадр[место] & 0x0F) * 4
        всего = struct.unpack_from("!H", кадр, место + 2)[0]
        флаги = struct.unpack_from("!H", кадр, место + 6)[0]
        с.версия, с.протокол = 4, кадр[место + 9]
        с.от = str(ipaddress.IPv4Address(кадр[место + 12:место + 16]))
        с.к = str(ipaddress.IPv4Address(кадр[место + 16:место + 20]))
        с.фрагмент = (флаги & 0x3FFF) != 0              # MF (0x2000) или смещение фрагмента (0x1FFF) ≠ 0
        конец = min(len(кадр), место + всего) if всего >= ihl else len(кадр)
        место += ihl
        if с.фрагмент and (флаги & 0x1FFF):
            return с                                                    # не первый фрагмент: портов нет
    elif тип == ETHERTYPE_IPV6 and len(кадр) >= место + 40 and кадр[место] >> 4 == 6:
        длина = struct.unpack_from("!H", кадр, место + 4)[0]
        следующий = кадр[место + 6]
        с.версия = 6
        с.от = str(ipaddress.IPv6Address(кадр[место + 8:место + 24]))
        с.к = str(ipaddress.IPv6Address(кадр[место + 24:место + 40]))
        конец = min(len(кадр), место + 40 + длина)
        место += 40
        while следующий in РАСШИРЕНИЯ_IPV6 + (ФРАГМЕНТ_IPV6,) and место + 8 <= конец:
            if следующий == ФРАГМЕНТ_IPV6:
                с.фрагмент = True
                смещение = struct.unpack_from("!H", кадр, место + 2)[0] >> 3
                следующий = кадр[место]
                место += 8
                if смещение:
                    с.протокол = следующий
                    return с
                continue
            следующий, место = кадр[место], место + (кадр[место + 1] + 1) * 8
        с.протокол = следующий
    else:
        return с
    if с.протокол in (IP_TCP, IP_UDP) and место + 4 <= конец:
        с.порт_от, с.порт_к = struct.unpack_from("!HH", кадр, место)
        if с.протокол == IP_UDP and место + 8 <= конец and not с.фрагмент:
            длина_udp = struct.unpack_from("!H", кадр, место + 4)[0]
            if длина_udp >= 8:
                с.нагрузка = bytes(кадр[место + 8:min(конец, место + длина_udp)])
    return с
