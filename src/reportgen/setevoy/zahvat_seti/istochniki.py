"""Источники кадров для захвата: приём UDP, libpcap/Npcap, AF_PACKET (Linux), SIO_RCVALL (Windows).

У всех один порядок работы: открыть (здесь же — честная ошибка, если нет прав
или порт занят), ``прочитать(таймаут)`` → список (время, кадр, исходная длина),
``отброшено()`` — сколько потеряло ядро или драйвер, ``прервать()`` из другого
потока, ``закрыть()``. Кадр всегда отдаётся в том канальном уровне, что
объявлен в ``канал`` (LINKTYPE_*): где уровня Ethernet нет — он синтезирован.

Первоисточники:

* UDP-сокет: POSIX/Winsock ``bind``; на Linux ``recvmsg`` с ``IP_PKTINFO`` = 8
  (linux/in.h, ``struct in_pktinfo``: ipi_ifindex, ipi_spec_dst, ipi_addr),
  ``IPV6_RECVPKTINFO`` = 49 / ``IPV6_PKTINFO`` = 50 (linux/in6.h,
  ``struct in6_pktinfo``: ipi6_addr, ipi6_ifindex) — адрес получателя, когда
  слушаем все адреса; ``SO_RXQ_OVFL`` = 40 (asm-generic/socket.h, socket(7)) —
  сколько датаграмм сокет потерял с момента создания.
* Многоадресная рассылка: вступление — ``IP_ADD_MEMBERSHIP`` со ``struct ip_mreq``
  (imr_multiaddr, imr_interface: адрес карты; 0.0.0.0 — карту выбирает система)
  или, на Linux без адреса IPv4 у карты, ``struct ip_mreqn`` с imr_ifindex
  (linux/in.h; man IP_ADD_MEMBERSHIP(2const), ip_mreqn(2type)); в Windows номер
  карты пишется в imr_interface как адрес 0.x.x.x (документация Microsoft «IP_MREQ
  structure»). IPv6 — ``IPV6_JOIN_GROUP`` со ``struct ipv6_mreq`` (адрес группы,
  номер карты; linux/in6.h, «IPV6_MREQ structure»). На Linux сокет, привязанный к
  адресу карты, датаграмм группы не получает: доставка сверяет адрес привязки с
  адресом получателя (net/ipv4/udp.c ``__udp_is_mcast_sock``, net/ipv6/udp.c
  ``__udp_v6_is_mcast_sock``), поэтому с группой сокет привязывается к адресу
  группы (для IPv6 — с номером карты: net/ipv6/af_inet6.c требует его у адресов
  канального уровня). В Windows — к адресу, заданному человеком.
* AF_PACKET — packet(7), linux/if_packet.h: ``SOL_PACKET`` = 263,
  ``PACKET_ADD_MEMBERSHIP`` = 1 с ``PACKET_MR_PROMISC`` = 1 (struct
  packet_mreq: mr_ifindex int, mr_type u16, mr_alen u16, mr_address[8]),
  ``PACKET_STATISTICS`` = 6 (struct tpacket_stats: tp_packets, tp_drops;
  чтение обнуляет счётчики), ``PACKET_OUTGOING`` = 4; ``SO_ATTACH_FILTER`` = 26
  (struct sock_fprog: len u16, указатель на sock_filter).
* SIO_RCVALL — документация Microsoft «SIO_RCVALL control code» и «TCP/IP raw
  sockets»: сокет ``AF_INET, SOCK_RAW, IPPROTO_IP``, привязка к адресу карты
  (не к 0.0.0.0), ``RCVALL_ON`` = 1; приходят только пакеты IPv4 с заголовком IP,
  без уровня Ethernet; нужны права администратора (WSAEACCES = 10013 при
  создании сокета).
* libpcap/Npcap — см. pcap_bib.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import ipaddress
import select
import socket
import struct
import sys
import threading
import time
from typing import Any

from . import zapis
from .pcap_bib import DLT_EN10MB, PCAP_ERROR_BREAK, Libpcap, ОшибкаPcap, тип_в_файл

WINDOWS = sys.platform == "win32"

#: Linux (см. описание модуля).
IP_PKTINFO = 8
IPV6_RECVPKTINFO, IPV6_PKTINFO = 49, 50
SO_RXQ_OVFL = 40
SOL_PACKET = 263
PACKET_ADD_MEMBERSHIP, PACKET_DROP_MEMBERSHIP = 1, 2
PACKET_MR_PROMISC = 1
PACKET_STATISTICS = 6
PACKET_OUTGOING = 4
SO_ATTACH_FILTER = 26
ETH_P_ALL = 0x0003
#: Winsock: коды ошибок (документация Microsoft «Windows Sockets Error Codes»).
WSAEACCES, WSAEAFNOSUPPORT, WSAEADDRINUSE, WSAEADDRNOTAVAIL = 10013, 10047, 10048, 10049
#: SIO_RCVALL = _WSAIOW(IOC_VENDOR, 1) (mstcpip.h); RCVALL_OFF = 0, RCVALL_ON = 1.
SIO_RCVALL = 0x98000001
RCVALL_OFF, RCVALL_ON = 0, 1

#: Сколько кадров забирать за один проход — чтобы счётчики и остановка не ждали.
ПАЧКА = 2000
#: Буфер приёма сокета: запас на всплески, пока поток пишет файл.
БУФЕР_ПРИЁМА = 8 * 1024 * 1024
#: Наибольшая датаграмма UDP (IPv4: 65535 − 20 − 8) — буфер приёма с запасом.
ДАТАГРАММА = 65535


class ОшибкаЗахвата(Exception):
    """Честная причина, почему захват не начался: ``вид`` — права, занято, адрес, нет, ошибка."""

    def __init__(self, текст: str, вид: str = "ошибка"):
        super().__init__(текст)
        self.вид = вид


def _номер_ошибки(ошибка: OSError) -> int:
    return getattr(ошибка, "winerror", None) or ошибка.errno or 0


def понять_ошибку(ошибка: OSError, *, что: str, udp: bool = False) -> ОшибкаЗахвата:
    """OSError сокета → понятная причина (коды errno и Winsock)."""
    номер = _номер_ошибки(ошибка)
    if номер in (errno.EADDRINUSE, WSAEADDRINUSE) or (udp and номер == WSAEACCES):
        # WSAEACCES при bind UDP — порт взят другой программой с исключительным доступом.
        return ОшибкаЗахвата(f"{что}: порт занят другой программой", "занято")
    if номер in (errno.EPERM, errno.EACCES, WSAEACCES):
        return ОшибкаЗахвата(f"{что}: у сервера нет прав на захват — нужен запуск от администратора "
                             "(Windows) или root / CAP_NET_RAW (Linux)", "права")
    if номер in (errno.EADDRNOTAVAIL, WSAEADDRNOTAVAIL):
        return ОшибкаЗахвата(f"{что}: такого адреса нет у этой машины", "адрес")
    if номер == errno.ENODEV:
        return ОшибкаЗахвата(f"{что}: такой карты нет", "адрес")
    if номер in (errno.EAFNOSUPPORT, WSAEAFNOSUPPORT):
        return ОшибкаЗахвата(f"{что}: семейство адресов запрещено или выключено на этой машине — IPv6 выключен "
                             "или служба systemd не пускает (RestrictAddressFamilies, см. docs/21)", "адрес")
    return ОшибкаЗахвата(f"{что}: {ошибка.strerror or ошибка}")


class Источник:
    """Общее у источников: канал файла, описание, BPF-выражение, отметка остановки."""

    канал = zapis.LINKTYPE_ETHERNET
    способ = ""
    bpf = ""                            # выражение, которое отбирает уже ядро/драйвер

    def прочитать(self, таймаут: float) -> list[tuple[float, bytes, int]]:
        raise NotImplementedError

    def отброшено(self) -> int | None:
        return None

    def прервать(self) -> None:
        pass

    def закрыть(self) -> None:
        pass


def _ждать(сокеты: list[Any], таймаут: float) -> list[Any]:
    try:
        готовые, _, _ = select.select(сокеты, [], [], таймаут)
    except (OSError, ValueError):                  # сокет закрыли из другого потока
        return []
    return готовые


# -- приём UDP ----------------------------------------------------------------------------

class ПриёмUDP(Источник):
    """Слушать адрес:порт (несколько портов); датаграмма → кадр Ethernet/IP/UDP.

    Прав не требует. Каждый порт — свой сокет без SO_REUSEADDR: занятый порт
    обнаруживается сразу, а не делится молча с другой программой.
    """

    способ = "udp"

    def __init__(self, адрес: str, порты: list[int], *, группа: str = "", карта_ipv4: str = "", индекс: int = 0,
                 фабрика: Any = socket.socket):
        """``карта_ipv4``/``индекс`` — адрес и номер карты, на которой вступать в группу (выбранная
        в таблице карта); пусто/0 — карту выбирает система (по маршруту)."""
        self.адрес = адрес
        ip = ipaddress.ip_address(адрес)
        self.семейство = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        self.любой = ip.is_unspecified
        self.группа = группа
        self.карта_ipv4 = карта_ipv4
        self.индекс = индекс
        # Linux: привязка к адресу карты отсекает датаграммы группы — слушаем адрес группы.
        привязка: tuple[Any, ...] = (адрес,)
        if группа and not WINDOWS:
            привязка = (группа,) if ipaddress.ip_address(группа).version == 4 else (группа, 0, индекс)
        self.сокеты: list[Any] = []
        self.порт_сокета: dict[Any, int] = {}
        self._потеряно: dict[Any, int] = {}
        self.pktinfo = not WINDOWS and hasattr(socket.socket, "recvmsg")
        try:
            for порт in порты:
                try:
                    с = фабрика(self.семейство, socket.SOCK_DGRAM)
                except OSError as ошибка:
                    raise понять_ошибку(ошибка, что=f"UDP {адрес}") from None
                self.сокеты.append(с)
                with contextlib.suppress(OSError):
                    с.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, БУФЕР_ПРИЁМА)
                if self.pktinfo:
                    with contextlib.suppress(OSError):
                        с.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
                    with contextlib.suppress(OSError):
                        if self.семейство == socket.AF_INET:
                            с.setsockopt(socket.IPPROTO_IP, IP_PKTINFO, 1)
                        else:
                            с.setsockopt(socket.IPPROTO_IPV6, IPV6_RECVPKTINFO, 1)
                try:
                    с.bind((привязка[0], порт, *привязка[1:]))
                except OSError as ошибка:
                    raise понять_ошибку(ошибка, что=f"UDP {адрес}:{порт}", udp=True) from None
                if группа:
                    self._вступить(с, группа)
                с.setblocking(False)
                self.порт_сокета[с] = порт
        except BaseException:
            self.закрыть()
            raise

    def _вступить(self, с: Any, группа: str) -> None:
        """Вступить в группу многоадресной рассылки (IP_ADD_MEMBERSHIP / IPV6_JOIN_GROUP)."""
        г = ipaddress.ip_address(группа)
        try:
            if г.version == 4:
                с.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, self.запрос_группы(г))
            else:
                с.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, г.packed + struct.pack("@I", self.индекс))
        except OSError as ошибка:
            raise понять_ошибку(ошибка, что=f"группа {группа}") from None

    def запрос_группы(self, г: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bytes:
        """ip_mreq (8 байт) или ip_mreqn (12 байт, Linux) — на какой карте вступать в группу IPv4."""
        if not self.любой:
            return г.packed + ipaddress.IPv4Address(self.адрес).packed       # адрес карты задал человек
        if self.карта_ipv4:
            return г.packed + ipaddress.IPv4Address(self.карта_ipv4).packed
        if self.индекс and WINDOWS:
            return г.packed + struct.pack("!I", self.индекс)                   # номер карты как 0.x.x.x
        if self.индекс:
            return г.packed + bytes(4) + struct.pack("@i", self.индекс)        # ip_mreqn.imr_ifindex
        return г.packed + bytes(4)                                             # INADDR_ANY — выберет система

    def _получатель(self, служебные: list[tuple[int, int, bytes]], с: Any) -> str:
        for уровень, вид, данные in служебные:
            if уровень == socket.IPPROTO_IP and вид == IP_PKTINFO and len(данные) >= 12:
                return str(ipaddress.IPv4Address(данные[8:12]))          # in_pktinfo.ipi_addr
            if уровень == socket.IPPROTO_IPV6 and вид == IPV6_PKTINFO and len(данные) >= 16:
                return str(ipaddress.IPv6Address(данные[:16]))           # in6_pktinfo.ipi6_addr
            if уровень == socket.SOL_SOCKET and вид == SO_RXQ_OVFL and len(данные) >= 4:
                self._потеряно[с] = struct.unpack("@I", данные[:4])[0]
        return self.адрес

    def прочитать(self, таймаут: float) -> list[tuple[float, bytes, int]]:
        итог: list[tuple[float, bytes, int]] = []
        for с in _ждать(self.сокеты, таймаут):
            порт = self.порт_сокета.get(с)
            if порт is None:
                continue
            while len(итог) < ПАЧКА:
                try:
                    if self.pktinfo:
                        данные, служебные, _, откуда = с.recvmsg(ДАТАГРАММА, 256)
                        получатель = self._получатель(служебные, с)
                    else:
                        данные, откуда = с.recvfrom(ДАТАГРАММА)
                        получатель = self.адрес
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    # Windows отвечает WSAECONNRESET на датаграмму после ICMP «порт недоступен» —
                    # это не наша ошибка, приём продолжается.
                    break
                if ipaddress.ip_address(получатель).version != ipaddress.ip_address(откуда[0].split("%")[0]).version:
                    получатель = "::" if self.семейство == socket.AF_INET6 else "0.0.0.0"
                кадр = zapis.кадр_udp(данные, откуда[0].split("%")[0], откуда[1], получатель, порт)
                итог.append((time.time(), кадр, len(кадр)))
        return итог

    def отброшено(self) -> int | None:
        return sum(self._потеряно.values()) if self._потеряно else (None if not self.pktinfo else 0)

    def закрыть(self) -> None:
        for с in self.сокеты:
            with contextlib.suppress(OSError):
                с.close()
        self.сокеты = []


# -- libpcap / Npcap ----------------------------------------------------------------------

class ЗахватPcap(Источник):
    """Кадры карты через libpcap (Linux) или Npcap (Windows); фильтр — BPF в драйвере."""

    def __init__(self, lib: Libpcap, устройство: str, *, фильтр: str = "", фильтр_без_vlan: str | None = None,
                 неразборчиво: bool = True, snaplen: int = zapis.SNAPLEN):
        """``фильтр_без_vlan`` — выражение для канала не Ethernet (петля Npcap — DLT_NULL, туннели —
        DLT_RAW): «vlan» там не собирается (gencode.c gen_vlan), а отбор нужен тот же."""
        self.lib = lib
        self.способ = "npcap" if WINDOWS else "libpcap"
        # pcap_breakloop из другого потока и pcap_close — под одним замком: pcap_close освобождает
        # дескриптор (man pcap_close), а breakloop на Linux пишет в его poll_breakloop_fd
        # (pcap-linux.c pcap_breakloop_linux) — после закрытия это чужой файл или падение.
        self._замок = threading.Lock()
        self._закрыт = False
        try:
            self.р = lib.открыть(устройство, snaplen=snaplen, неразборчиво=неразборчиво, таймаут_мс=200)
        except ОшибкаPcap as ошибка:
            текст = str(ошибка)
            вид = "права" if any(с in текст.lower() for с in ("permission", "operation not permitted",
                                                                  "access is denied", "отказано")) else "ошибка"
            raise ОшибкаЗахвата(f"{self.способ}: {текст}" + (
                " — нужен запуск от администратора / root" if вид == "права" else ""), вид) from None
        try:
            канал = lib.канал(self.р)
            выражение = фильтр if фильтр_без_vlan is None or канал == DLT_EN10MB else фильтр_без_vlan
            if выражение:
                lib.фильтр(self.р, выражение)
                self.bpf = выражение
        except ОшибкаPcap as ошибка:
            lib.закрыть(self.р)
            raise ОшибкаЗахвата(str(ошибка)) from None
        self.канал = тип_в_файл(канал)

    def прочитать(self, таймаут: float) -> list[tuple[float, bytes, int]]:
        """Один пакет за вызов: на Linux (TPACKET_V3) pcap_next_ex без трафика не возвращается по
        таймауту буфера (man pcap_set_timeout: таймер может не начаться до первого пакета), и пачка
        ждала бы вечно. Остановка и предел времени будят его через pcap_breakloop."""
        if self._закрыт:
            return []
        код, время, данные, длина = self.lib.следующий(self.р)
        if код == 1:
            return [(время, данные, длина)]
        if код == 0 or код == PCAP_ERROR_BREAK:        # истёк таймаут буфера / прервали
            return []
        raise ОшибкаЗахвата(f"{self.способ}: {self.lib.ошибка(self.р)}")

    def отброшено(self) -> int | None:
        с = self.lib.статистика(self.р)
        return None if с is None else с[1] + с[2]

    def прервать(self) -> None:
        with self._замок:
            if not self._закрыт:
                self.lib.прервать(self.р)

    def закрыть(self) -> None:
        with self._замок:
            if not self._закрыт:
                self._закрыт = True
                self.lib.закрыть(self.р)


# -- AF_PACKET ----------------------------------------------------------------------------

class sock_filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class sock_fprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(sock_filter))]


class ЗахватAFPacket(Источник):
    """Linux: сокет AF_PACKET на карте; неразборчивый режим — PACKET_MR_PROMISC.

    Карты с уровнем Ethernet (и петля: у неё тот же 14-байтовый заголовок)
    читаются SOCK_RAW. Прочие (туннели, ARPHRD_NONE) — SOCK_DGRAM: ядро
    снимает свой заголовок, а мы синтезируем Ethernet с EtherType из адреса.
    Исходящие на петле пропускаются — каждый такой пакет ядро покажет ещё и
    входящим (так же делает libpcap, pcap-linux.c linux_check_direction).
    """

    способ = "af_packet"

    def __init__(self, карта: str, *, arphrd: int = 1, неразборчиво: bool = True,
                 bpf: list[tuple[int, int, int, int]] | None = None, bpf_текст: str = "",
                 фабрика: Any = None):
        from .karty import ARPHRD_ETHER, ARPHRD_LOOPBACK  # noqa: PLC0415
        self.сырой = arphrd in (ARPHRD_ETHER, ARPHRD_LOOPBACK)
        self.петля = arphrd == ARPHRD_LOOPBACK
        фабрика = фабрика or socket.socket
        try:
            self.с = фабрика(getattr(socket, "AF_PACKET", 17), socket.SOCK_RAW if self.сырой else socket.SOCK_DGRAM,
                             socket.htons(ETH_P_ALL))
        except OSError as ошибка:
            raise понять_ошибку(ошибка, что="AF_PACKET") from None
        self._буфер = bytearray(zapis.SNAPLEN)
        self._потеряно = 0
        try:
            with contextlib.suppress(OSError):
                self.с.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, БУФЕР_ПРИЁМА)
            if bpf and self.сырой:
                self._фильтр(bpf)
                self.bpf = bpf_текст
            self.с.bind((карта, 0))
            self.индекс = socket.if_nametoindex(карта)
            self.неразборчиво = False
            if неразборчиво:
                self.с.setsockopt(SOL_PACKET, PACKET_ADD_MEMBERSHIP,
                                  struct.pack("@iHH8s", self.индекс, PACKET_MR_PROMISC, 0, b""))
                self.неразборчиво = True
            self.с.setblocking(False)
        except OSError as ошибка:
            self.с.close()
            raise понять_ошибку(ошибка, что=f"AF_PACKET {карта}") from None

    def _фильтр(self, программа: list[tuple[int, int, int, int]]) -> None:
        команды = (sock_filter * len(программа))(*[sock_filter(*к) for к in программа])
        fprog = sock_fprog(len(программа), ctypes.cast(команды, ctypes.POINTER(sock_filter)))
        self.с.setsockopt(socket.SOL_SOCKET, SO_ATTACH_FILTER, bytes(fprog))

    def прочитать(self, таймаут: float) -> list[tuple[float, bytes, int]]:
        итог: list[tuple[float, bytes, int]] = []
        if not _ждать([self.с], таймаут):
            return итог
        while len(итог) < ПАЧКА:
            try:
                n, откуда = self.с.recvfrom_into(self._буфер)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            if откуда[2] == PACKET_OUTGOING and self.петля:
                continue
            кадр = bytes(self._буфер[:n])
            if not self.сырой:
                кадр = zapis.ethernet(кадр, откуда[1])
            итог.append((time.time(), кадр, len(кадр)))
        return итог

    def отброшено(self) -> int | None:
        try:
            _, потеряно = struct.unpack("@II", self.с.getsockopt(SOL_PACKET, PACKET_STATISTICS, 8))
        except OSError:
            return self._потеряно
        self._потеряно += потеряно                  # чтение обнуляет счётчик ядра — копим сами
        return self._потеряно

    def закрыть(self) -> None:
        with contextlib.suppress(OSError):
            if self.неразборчиво:
                self.с.setsockopt(SOL_PACKET, PACKET_DROP_MEMBERSHIP,
                                  struct.pack("@iHH8s", self.индекс, PACKET_MR_PROMISC, 0, b""))
        with contextlib.suppress(OSError):
            self.с.close()


# -- SIO_RCVALL ---------------------------------------------------------------------------

class ЗахватSioRcvall(Источник):
    """Windows без Npcap: сырой сокет IPv4 с SIO_RCVALL на адресе карты.

    Видны только пакеты IPv4 (и входящие, и исходящие) с заголовком IP; ARP и
    прочее не-IP не видно. Уровень Ethernet синтезируется (нулевые MAC).
    """

    способ = "sio_rcvall"

    def __init__(self, адрес: str, *, фабрика: Any = None):
        фабрика = фабрика or socket.socket
        try:
            self.с = фабрика(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
        except OSError as ошибка:
            raise понять_ошибку(ошибка, что="сырой сокет (SIO_RCVALL)") from None
        self._буфер = bytearray(ДАТАГРАММА)
        self.включён = False
        try:
            with contextlib.suppress(OSError):
                self.с.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, БУФЕР_ПРИЁМА)
            self.с.bind((адрес, 0))
            self.с.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            self.с.ioctl(getattr(socket, "SIO_RCVALL", SIO_RCVALL), getattr(socket, "RCVALL_ON", RCVALL_ON))
            self.включён = True
            self.с.setblocking(False)
        except OSError as ошибка:
            self.с.close()
            raise понять_ошибку(ошибка, что=f"SIO_RCVALL на {адрес}") from None

    def прочитать(self, таймаут: float) -> list[tuple[float, bytes, int]]:
        итог: list[tuple[float, bytes, int]] = []
        if not _ждать([self.с], таймаут):
            return итог
        while len(итог) < ПАЧКА:
            try:
                n = self.с.recv_into(self._буфер)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            кадр = zapis.кадр_из_ip(bytes(self._буфер[:n]))
            итог.append((time.time(), кадр, len(кадр)))
        return итог

    def закрыть(self) -> None:
        with contextlib.suppress(OSError, AttributeError):
            if self.включён:
                self.с.ioctl(getattr(socket, "SIO_RCVALL", SIO_RCVALL), getattr(socket, "RCVALL_OFF", RCVALL_OFF))
        with contextlib.suppress(OSError):
            self.с.close()


# -- какие способы есть у этой машины ------------------------------------------------------

def пробный_сокет(семейство: int, вид: int, протокол: int, фабрика: Any = None) -> str:
    """Пусто — сокет создаётся (права есть); иначе — причина словами."""
    фабрика = фабрика or socket.socket
    try:
        с = фабрика(семейство, вид, протокол)
    except OSError as ошибка:
        return str(понять_ошибку(ошибка, что="проба"))
    with contextlib.suppress(OSError):
        с.close()
    return ""


def возможности(lib: Libpcap | None, причина_lib: str, *, windows: bool = WINDOWS,
                фабрика: Any = None) -> dict[str, dict[str, Any]]:
    """Способы захвата с карты: доступен ли каждый и почему нет. «udp» доступен всегда."""
    итог: dict[str, dict[str, Any]] = {"udp": {"можно": True, "почему": "", "название": "приём UDP сокетом"}}
    имя_lib = "npcap" if windows else "libpcap"
    итог[имя_lib] = {"можно": lib is not None, "почему": "" if lib else причина_lib,
                     "название": "Npcap" if windows else "libpcap",
                     "версия": lib.версия() if lib else ""}
    if windows:
        почему = пробный_сокет(socket.AF_INET, getattr(socket, "SOCK_RAW", 3), socket.IPPROTO_IP, фабрика)
        итог["sio_rcvall"] = {"можно": not почему, "почему": почему,
                              "название": "сырой сокет SIO_RCVALL (только IPv4)"}
    else:
        почему = пробный_сокет(getattr(socket, "AF_PACKET", 17), socket.SOCK_RAW, 0, фабрика)
        итог["af_packet"] = {"можно": not почему, "почему": почему, "название": "AF_PACKET"}
    return итог


def выбрать_способ(возможное: dict[str, dict[str, Any]], желаемый: str = "авто") -> str:
    """Способ захвата с карты: заданный (если доступен) или лучший из доступных."""
    порядок = ["npcap", "libpcap", "af_packet", "sio_rcvall"]
    if желаемый and желаемый != "авто":
        с = возможное.get(желаемый)
        if с is None:
            raise ОшибкаЗахвата(f"способ «{желаемый}» на этой машине не бывает", "нет")
        if not с["можно"]:
            raise ОшибкаЗахвата(f"{с['название']} недоступен: {с['почему']}", "права"
                                if "прав" in с["почему"] else "нет")
        return желаемый
    for имя in порядок:
        if возможное.get(имя, {}).get("можно"):
            return имя
    причины = "; ".join(f"{с['название']}: {с['почему']}" for к, с in возможное.items() if к != "udp")
    raise ОшибкаЗахвата("захват с карты невозможен — " + причины, "права" if "прав" in причины else "нет")
