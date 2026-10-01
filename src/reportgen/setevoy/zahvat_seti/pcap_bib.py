"""libpcap (Linux) и Npcap (Windows, wpcap.dll) через ctypes — без сторонних пакетов.

Прототипы, константы и структуры — из заголовков libpcap (``pcap/pcap.h``,
``pcap/bpf.h``) и man-страниц ``pcap_findalldevs(3PCAP)``, ``pcap_open_live``,
``pcap_compile``, ``pcap_setfilter``, ``pcap_next_ex``, ``pcap_stats``,
``pcap_breakloop``, ``pcap_init``. Раскладка проверена на libpcap 1.10 (x86_64).

Тонкости, из-за которых раскладка ветвится по платформе:

* ``struct pcap_pkthdr`` начинается с ``struct timeval``. В Windows ``long`` —
  32 бита, заголовок 16 байт; в Linux LP64 — 24 байта.
* ``struct pcap_stat`` в Windows длиннее на три поля (``ps_capt``, ``ps_sent``,
  ``ps_netdrop``, pcap.h под ``_WIN32``); ``pcap_stats`` их не заполняет, но
  место под них отводим — чтобы запись не вышла за структуру.
* Семейство IPv6 в ``sockaddr``: Linux 10, Windows 23, FreeBSD 28, macOS 30 —
  берём из модуля ``socket`` той же машины, где работает библиотека.
* ``struct sockaddr`` в Linux и Windows начинается с 16-битного семейства; в BSD
  и macOS — с байта длины ``sa_len`` и 8-битного ``sa_family_t`` (FreeBSD
  sys/socket.h и sys/_types.h, XNU bsd/sys/socket.h и _sa_family_t.h), MAC там —
  ``struct sockaddr_dl`` семейства AF_LINK = 18 (net/if_dl.h: sdl_nlen в байте 5,
  sdl_alen в байте 6, адрес — в sdl_data с 8-го байта после имени карты).

Npcap ставит DLL в ``%SystemRoot%\\System32\\Npcap`` (руководство Npcap,
«Npcap's DLLs»): грузим wpcap.dll полным путём — тогда загрузчик Windows ищет
её зависимость Packet.dll в той же папке (LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR,
см. ctypes ``__init__.py``), а не подхватывает старый WinPcap из System32.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import socket
import sys
from pathlib import Path
from typing import Any

WINDOWS = sys.platform == "win32"
#: BSD-раскладка sockaddr (sa_len + 8-битное семейство): macOS, FreeBSD, OpenBSD, NetBSD, DragonFly.
BSD = sys.platform == "darwin" or "bsd" in sys.platform or sys.platform.startswith("dragonfly")

PCAP_ERRBUF_SIZE = 256
PCAP_NETMASK_UNKNOWN = 0xFFFFFFFF
PCAP_CHAR_ENC_UTF_8 = 0x00000001
#: Флаги pcap_if.flags (pcap.h).
PCAP_IF_LOOPBACK = 0x00000001
PCAP_IF_UP = 0x00000002
PCAP_IF_RUNNING = 0x00000004
PCAP_IF_WIRELESS = 0x00000008
PCAP_IF_CONNECTION_STATUS = 0x00000030
PCAP_IF_CONNECTION_STATUS_CONNECTED = 0x00000010
PCAP_IF_CONNECTION_STATUS_DISCONNECTED = 0x00000020
#: Возвраты pcap_next_ex и коды ошибок pcap_activate (pcap.h).
PCAP_ERROR = -1
PCAP_ERROR_BREAK = -2
PCAP_ERROR_NO_SUCH_DEVICE = -5
PCAP_ERROR_PERM_DENIED = -8
PCAP_ERROR_IFACE_NOT_UP = -9
PCAP_ERROR_PROMISC_PERM_DENIED = -11
#: DLT (pcap/dlt.h) → LINKTYPE в файле (pcap-common.c, dlt_to_linktype): различаются
#: только коды, чьи значения DLT разные на разных системах; остальные совпадают.
DLT_EN10MB, DLT_RAW, DLT_RAW_OPENBSD = 1, 12, 14
#: MAXIMUM_SNAPLEN (pcap-int.h); оптимизировать байткод в pcap_compile (любое ненулевое — «да»).
MAXIMUM_SNAPLEN = 262144
ОПТИМИЗИРОВАТЬ_BPF = 1
LINKTYPE_RAW = 101
AF_PACKET_LINUX = 17                 # linux/socket.h; адрес карты — struct sockaddr_ll
AF_LINK_BSD = 18                     # FreeBSD/XNU sys/socket.h; адрес карты — struct sockaddr_dl


def тип_в_файл(dlt: int) -> int:
    """Код канального уровня libpcap (DLT_*) → код для файла (LINKTYPE_*)."""
    if dlt in (DLT_RAW, DLT_RAW_OPENBSD):
        return LINKTYPE_RAW
    return dlt


class timeval_posix(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class timeval_win(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int32), ("tv_usec", ctypes.c_int32)]


def структуры(windows: bool = WINDOWS) -> dict[str, type]:
    """Структуры libpcap для платформы: pcap_pkthdr и pcap_stat ветвятся (см. описание модуля)."""
    timeval = timeval_win if windows else timeval_posix

    class pcap_pkthdr(ctypes.Structure):
        _fields_ = [("ts", timeval), ("caplen", ctypes.c_uint32), ("len", ctypes.c_uint32)]

    поля_stat = [("ps_recv", ctypes.c_uint), ("ps_drop", ctypes.c_uint), ("ps_ifdrop", ctypes.c_uint)]
    if windows:
        поля_stat += [("ps_capt", ctypes.c_uint), ("ps_sent", ctypes.c_uint), ("ps_netdrop", ctypes.c_uint)]

    class pcap_stat(ctypes.Structure):
        _fields_ = поля_stat

    return {"pcap_pkthdr": pcap_pkthdr, "pcap_stat": pcap_stat}


class sockaddr(ctypes.Structure):
    # Из общего sockaddr нужно только семейство; сам адрес читается по раскладке своего семейства.
    # Поле — 16-битное семейство Linux/Windows; в BSD семейство читается байтом 1 (см. выше).
    _fields_ = [("sa_family", ctypes.c_ushort)]


class pcap_addr(ctypes.Structure):
    pass


pcap_addr._fields_ = [("next", ctypes.POINTER(pcap_addr)), ("addr", ctypes.POINTER(sockaddr)),
                      ("netmask", ctypes.POINTER(sockaddr)), ("broadaddr", ctypes.POINTER(sockaddr)),
                      ("dstaddr", ctypes.POINTER(sockaddr))]


class pcap_if(ctypes.Structure):
    pass


pcap_if._fields_ = [("next", ctypes.POINTER(pcap_if)), ("name", ctypes.c_char_p),
                    ("description", ctypes.c_char_p), ("addresses", ctypes.POINTER(pcap_addr)),
                    ("flags", ctypes.c_uint32)]


class bpf_insn(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class bpf_program(ctypes.Structure):
    _fields_ = [("bf_len", ctypes.c_uint), ("bf_insns", ctypes.POINTER(bpf_insn))]


class ОшибкаPcap(OSError):
    """Ошибка libpcap/Npcap с текстом библиотеки."""


def пути_библиотеки(свой: str = "") -> list[str]:
    """Где искать библиотеку: путь из настроек, затем Npcap (Windows) или системная libpcap."""
    пути = [свой] if свой else []
    if WINDOWS:
        система = os.environ.get("SystemRoot", r"C:\Windows")
        пути.append(str(Path(система) / "System32" / "Npcap" / "wpcap.dll"))
    else:
        найдено = ctypes.util.find_library("pcap")
        if найдено:
            пути.append(найдено)
        пути += ["libpcap.so.1", "libpcap.so.0.8", "libpcap.so", "libpcap.dylib"]
    return пути


def адрес_из_sockaddr(sa: Any, bsd: bool = BSD) -> tuple[str, str] | None:
    """(«ipv4»|«ipv6»|«mac», текст) из указателя на sockaddr или None.

    sockaddr_in: адрес с 4-го байта; sockaddr_in6: с 8-го (RFC 3493 §3.3; в BSD
    те же смещения — netinet/in.h, netinet6/in6.h); sockaddr_ll (Linux,
    AF_PACKET): длина адреса в байте 11, адрес с 12-го (linux/if_packet.h);
    sockaddr_dl (BSD, AF_LINK): см. описание модуля.
    """
    if not sa:
        return None
    адрес = ctypes.cast(sa, ctypes.c_void_p).value
    семейство = ctypes.string_at(адрес + 1, 1)[0] if bsd else sa.contents.sa_family
    if семейство == socket.AF_INET:
        return "ipv4", socket.inet_ntop(socket.AF_INET, ctypes.string_at(адрес + 4, 4))
    if семейство == socket.AF_INET6:
        return "ipv6", socket.inet_ntop(socket.AF_INET6, ctypes.string_at(адрес + 8, 16))
    if bsd:
        if семейство == AF_LINK_BSD:
            имя, длина = ctypes.string_at(адрес + 5, 2)             # sdl_nlen, sdl_alen
            if длина:
                return "mac", ctypes.string_at(адрес + 8 + имя, длина).hex(":")
        return None
    if семейство == AF_PACKET_LINUX and not WINDOWS:
        длина = min(ctypes.string_at(адрес + 11, 1)[0], 8)       # sll_halen; sll_addr — 8 байт
        if длина:
            return "mac", ctypes.string_at(адрес + 12, длина).hex(":")
    return None


def кодировка_строк(utf8: bool, windows: bool) -> str:
    """Кодировка строк libpcap: UTF-8 после pcap_init(PCAP_CHAR_ENC_UTF_8) и всегда вне Windows;
    иначе — ANSI-кодовая страница Windows (man pcap_init)."""
    return "utf-8" if utf8 or not windows else "mbcs"


def длина_префикса(маска: str) -> int:
    """Маска в тексте (255.255.255.0, ffff:ffff::) → число единичных бит."""
    семейство = socket.AF_INET6 if ":" in маска else socket.AF_INET
    return sum(bin(б).count("1") for б in socket.inet_pton(семейство, маска))


class Libpcap:
    """Загруженная libpcap/wpcap.dll с описанными прототипами."""

    def __init__(self, путь: str):
        self.путь = путь
        self.lib = ctypes.CDLL(путь)                  # все функции libpcap — cdecl
        с = структуры()
        self.pcap_pkthdr, self.pcap_stat = с["pcap_pkthdr"], с["pcap_stat"]
        lib, p = self.lib, ctypes.c_void_p
        lib.pcap_lib_version.restype = ctypes.c_char_p
        lib.pcap_lib_version.argtypes = []
        lib.pcap_findalldevs.argtypes = [ctypes.POINTER(ctypes.POINTER(pcap_if)), ctypes.c_char_p]
        lib.pcap_findalldevs.restype = ctypes.c_int
        lib.pcap_freealldevs.argtypes = [ctypes.POINTER(pcap_if)]
        lib.pcap_freealldevs.restype = None
        lib.pcap_open_live.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p]
        lib.pcap_open_live.restype = p
        lib.pcap_open_dead.argtypes = [ctypes.c_int, ctypes.c_int]
        lib.pcap_open_dead.restype = p
        lib.pcap_compile.argtypes = [p, ctypes.POINTER(bpf_program), ctypes.c_char_p, ctypes.c_int, ctypes.c_uint32]
        lib.pcap_compile.restype = ctypes.c_int
        lib.pcap_setfilter.argtypes = [p, ctypes.POINTER(bpf_program)]
        lib.pcap_setfilter.restype = ctypes.c_int
        lib.pcap_freecode.argtypes = [ctypes.POINTER(bpf_program)]
        lib.pcap_freecode.restype = None
        lib.pcap_next_ex.argtypes = [p, ctypes.POINTER(ctypes.POINTER(self.pcap_pkthdr)),
                                     ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        lib.pcap_next_ex.restype = ctypes.c_int
        lib.pcap_stats.argtypes = [p, ctypes.POINTER(self.pcap_stat)]
        lib.pcap_stats.restype = ctypes.c_int
        lib.pcap_breakloop.argtypes = [p]
        lib.pcap_breakloop.restype = None
        lib.pcap_datalink.argtypes = [p]
        lib.pcap_datalink.restype = ctypes.c_int
        lib.pcap_geterr.argtypes = [p]
        lib.pcap_geterr.restype = ctypes.c_char_p
        lib.pcap_close.argtypes = [p]
        lib.pcap_close.restype = None
        # pcap_init (libpcap ≥ 1.10): строки в UTF-8 — иначе в Windows имена карт в ANSI-кодировке.
        self.utf8 = hasattr(lib, "pcap_init")
        if self.utf8:
            lib.pcap_init.argtypes = [ctypes.c_uint, ctypes.c_char_p]
            lib.pcap_init.restype = ctypes.c_int
            ошибка = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
            self.utf8 = lib.pcap_init(PCAP_CHAR_ENC_UTF_8, ошибка) == 0
        self.кодировка = кодировка_строк(self.utf8, WINDOWS)

    def _текст(self, сырое: bytes | None) -> str:
        return сырое.decode(self.кодировка, "replace") if сырое else ""

    def версия(self) -> str:
        return self._текст(self.lib.pcap_lib_version())

    def устройства(self) -> list[dict[str, Any]]:
        """pcap_findalldevs: имя устройства, описание, адреса, флаги PCAP_IF_*."""
        ошибка = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        все = ctypes.POINTER(pcap_if)()
        if self.lib.pcap_findalldevs(ctypes.byref(все), ошибка) != 0:
            raise ОшибкаPcap(self._текст(ошибка.value))
        итог = []
        try:
            у = все
            while у:
                д = у.contents
                адреса, mac = [], ""
                а = д.addresses
                while а:
                    адрес = адрес_из_sockaddr(а.contents.addr)
                    if адрес and адрес[0] == "mac":
                        mac = адрес[1]
                    elif адрес:
                        маска = адрес_из_sockaddr(а.contents.netmask)
                        адреса.append({"вид": адрес[0], "адрес": адрес[1],
                                       "префикс": длина_префикса(маска[1]) if маска else None})
                    а = а.contents.next
                итог.append({"имя": self._текст(д.name), "описание": self._текст(д.description),
                             "адреса": адреса, "mac": mac, "флаги": int(д.flags)})
                у = д.next
        finally:
            self.lib.pcap_freealldevs(все)
        return итог

    def открыть(self, устройство: str, *, snaplen: int, неразборчиво: bool, таймаут_мс: int) -> int:
        ошибка = ctypes.create_string_buffer(PCAP_ERRBUF_SIZE)
        р = self.lib.pcap_open_live(устройство.encode(self.кодировка), snaplen, int(неразборчиво), таймаут_мс, ошибка)
        if not р:
            raise ОшибкаPcap(self._текст(ошибка.value) or "pcap_open_live не открыл устройство")
        return р

    def ошибка(self, р: int) -> str:
        return self._текст(self.lib.pcap_geterr(р))

    def фильтр(self, р: int, выражение: str) -> None:
        """pcap_compile + pcap_setfilter; маска сети неизвестна — PCAP_NETMASK_UNKNOWN."""
        программа = bpf_program()
        if self.lib.pcap_compile(р, ctypes.byref(программа), выражение.encode("ascii"), ОПТИМИЗИРОВАТЬ_BPF,
                                 PCAP_NETMASK_UNKNOWN) != 0:
            raise ОшибкаPcap(f"фильтр «{выражение}»: {self.ошибка(р)}")
        try:
            if self.lib.pcap_setfilter(р, ctypes.byref(программа)) != 0:
                raise ОшибкаPcap(f"фильтр не установлен: {self.ошибка(р)}")
        finally:
            self.lib.pcap_freecode(ctypes.byref(программа))

    def скомпилировать(self, выражение: str, канал: int = DLT_EN10MB, snaplen: int = MAXIMUM_SNAPLEN
                       ) -> list[tuple[int, int, int, int]]:
        """Байткод BPF для выражения без живой карты (pcap_open_dead) — для SO_ATTACH_FILTER."""
        р = self.lib.pcap_open_dead(канал, snaplen)
        if not р:
            raise ОшибкаPcap("pcap_open_dead не удался")
        try:
            программа = bpf_program()
            if self.lib.pcap_compile(р, ctypes.byref(программа), выражение.encode("ascii"), ОПТИМИЗИРОВАТЬ_BPF,
                                     PCAP_NETMASK_UNKNOWN) != 0:
                raise ОшибкаPcap(f"фильтр «{выражение}»: {self.ошибка(р)}")
            try:
                return [(программа.bf_insns[i].code, программа.bf_insns[i].jt, программа.bf_insns[i].jf,
                         программа.bf_insns[i].k) for i in range(программа.bf_len)]
            finally:
                self.lib.pcap_freecode(ctypes.byref(программа))
        finally:
            self.lib.pcap_close(р)

    def следующий(self, р: int) -> tuple[int, float, bytes, int] | tuple[int, None, None, None]:
        """pcap_next_ex: (1, время, байты, исходная длина) или (код, None, None, None).

        Заголовок и данные принадлежат libpcap и живут до следующего вызова —
        байты копируются сразу (man pcap_next_ex).
        """
        заголовок = ctypes.POINTER(self.pcap_pkthdr)()
        данные = ctypes.POINTER(ctypes.c_ubyte)()
        код = self.lib.pcap_next_ex(р, ctypes.byref(заголовок), ctypes.byref(данные))
        if код != 1:
            return код, None, None, None
        з = заголовок.contents
        return 1, з.ts.tv_sec + з.ts.tv_usec / 1e6, ctypes.string_at(данные, з.caplen), int(з.len)

    def статистика(self, р: int) -> tuple[int, int, int] | None:
        с = self.pcap_stat()
        if self.lib.pcap_stats(р, ctypes.byref(с)) != 0:
            return None
        return int(с.ps_recv), int(с.ps_drop), int(с.ps_ifdrop)

    def канал(self, р: int) -> int:
        return int(self.lib.pcap_datalink(р))

    def прервать(self, р: int) -> None:
        self.lib.pcap_breakloop(р)

    def закрыть(self, р: int) -> None:
        self.lib.pcap_close(р)


_загруженные: dict[str, Libpcap] = {}


def загрузить(свой_путь: str = "") -> tuple[Libpcap | None, str]:
    """Первая найденная библиотека и пусто — или None и честная причина, почему нет."""
    причины = []
    for путь in пути_библиотеки(свой_путь):
        if путь in _загруженные:
            return _загруженные[путь], ""
        if ("/" in путь or "\\" in путь) and not Path(путь).exists():
            причины.append(f"{путь}: файла нет")
            continue
        try:
            lib = Libpcap(путь)
        except (OSError, AttributeError) as ошибка:
            причины.append(f"{путь}: {ошибка}")
            continue
        _загруженные[путь] = lib
        return lib, ""
    if WINDOWS:
        return None, ("Npcap не установлен (нет %SystemRoot%\\System32\\Npcap\\wpcap.dll). Поставьте Npcap "
                      "из комплекта установки — или приём пойдёт сырым сокетом (только IPv4, от администратора).")
    return None, "libpcap не найдена (" + (причины[0] if причины else "нет путей") + ")"
