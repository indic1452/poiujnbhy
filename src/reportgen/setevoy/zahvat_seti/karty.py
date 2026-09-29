"""Сетевые карты машины-сервера: имя, описание, адреса IPv4/IPv6, MAC, состояние, скорость.

Linux — файлы ``/sys/class/net/<карта>/`` (Documentation/ABI/testing/sysfs-class-net:
``address``, ``operstate`` по RFC 2863, ``speed`` в Мбит/с — только у карт с
ethtool, ``mtu``, ``type`` — ARPHRD из linux/if_arp.h, ``flags``) и адреса из
``getifaddrs(3)`` (libc через ctypes).

Windows — ``GetAdaptersAddresses`` из iphlpapi.dll (документация Microsoft,
«GetAdaptersAddresses function», «IP_ADAPTER_ADDRESSES_LH structure»,
«IP_ADAPTER_UNICAST_ADDRESS_LH structure»; раскладка — mingw-w64 iptypes.h).
Имя устройства для Npcap (``\\Device\\NPF_{GUID}``) сопоставляется по GUID из
поля AdapterName.

Строки WCHAR читаются вручную как UTF-16LE: на Linux ``ctypes.c_wchar`` —
4 байта, и разбор (его проверяют тесты на поддельном буфере) ошибся бы.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import sys
from pathlib import Path
from typing import Any

from .pcap_bib import адрес_из_sockaddr, длина_префикса

WINDOWS = sys.platform == "win32"

#: linux/if.h: флаги карты.
IFF_UP = 1 << 0
IFF_LOOPBACK = 1 << 3
#: linux/if_arp.h: вид канального уровня карты.
ARPHRD_ETHER = 1
ARPHRD_LOOPBACK = 772
ARPHRD_NONE = 0xFFFE
ARPHRD_IEEE80211 = 801

#: RFC 2863 ifOperStatus словами (строки из sysfs operstate и IF_OPER_STATUS Windows).
СОСТОЯНИЯ = {
    "up": "работает", "down": "отключена", "testing": "проверка", "unknown": "неизвестно",
    "dormant": "ожидает", "notpresent": "нет устройства", "lowerlayerdown": "нет связи (кабель)",
}
#: IF_OPER_STATUS (ifdef.h): IfOperStatusUp = 1 … IfOperStatusLowerLayerDown = 7.
СОСТОЯНИЯ_WINDOWS = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notpresent",
                     7: "lowerlayerdown"}
#: IfType (ipifcons.h, IANA ifType).
ВИДЫ_WINDOWS = {1: "другое", 6: "Ethernet", 23: "PPP", 24: "петля", 71: "Wi-Fi", 131: "туннель",
                144: "IEEE 1394", 243: "мобильная связь"}


def _прочитать(путь: Path) -> str:
    try:
        return путь.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return ""


def _число(текст: str, основание: int = 10) -> int | None:
    try:
        return int(текст, основание)
    except ValueError:
        return None


# -- Linux --------------------------------------------------------------------------------

class ifaddrs(ctypes.Structure):
    pass


ifaddrs._fields_ = [("ifa_next", ctypes.POINTER(ifaddrs)), ("ifa_name", ctypes.c_char_p),
                    ("ifa_flags", ctypes.c_uint), ("ifa_addr", ctypes.c_void_p),
                    ("ifa_netmask", ctypes.c_void_p), ("ifa_ifu", ctypes.c_void_p),
                    ("ifa_data", ctypes.c_void_p)]


def адреса_getifaddrs() -> dict[str, list[dict[str, Any]]]:
    """getifaddrs(3): адреса IPv4/IPv6 каждой карты с длиной префикса (по маске)."""
    from .pcap_bib import sockaddr  # noqa: PLC0415 — та же структура, что у libpcap
    libc = ctypes.CDLL(None)
    libc.getifaddrs.argtypes = [ctypes.POINTER(ctypes.POINTER(ifaddrs))]
    libc.getifaddrs.restype = ctypes.c_int
    libc.freeifaddrs.argtypes = [ctypes.POINTER(ifaddrs)]
    libc.freeifaddrs.restype = None
    список = ctypes.POINTER(ifaddrs)()
    if libc.getifaddrs(ctypes.byref(список)) != 0:
        raise OSError(ctypes.get_errno(), "getifaddrs не удался")
    итог: dict[str, list[dict[str, Any]]] = {}
    try:
        у = список
        while у:
            з = у.contents
            имя = (з.ifa_name or b"").decode("utf-8", "replace")
            адрес = адрес_из_sockaddr(ctypes.cast(з.ifa_addr, ctypes.POINTER(sockaddr))) if з.ifa_addr else None
            if адрес and адрес[0] in ("ipv4", "ipv6"):
                маска = адрес_из_sockaddr(ctypes.cast(з.ifa_netmask, ctypes.POINTER(sockaddr))) \
                    if з.ifa_netmask else None
                итог.setdefault(имя, []).append({
                    "вид": адрес[0], "адрес": адрес[1].split("%")[0],
                    "префикс": длина_префикса(маска[1]) if маска else None})
            у = з.ifa_next
    finally:
        libc.freeifaddrs(список)
    return итог


#: linux/sockios.h: основной адрес IPv4 карты и его маска (netdevice(7)); ifreq — имя
#: (IFNAMSIZ = 16 байт), затем объединение, где лежит struct sockaddr_in (адрес — с байта 4).
SIOCGIFADDR, SIOCGIFNETMASK = 0x8915, 0x891B


def адреса_ioctl(имена: list[str], if_inet6: Path = Path("/proc/net/if_inet6")) -> dict[str, list[dict[str, Any]]]:
    """Запасной путь, когда getifaddrs не работает (служба systemd без AF_NETLINK):
    IPv4 — ioctl SIOCGIFADDR/SIOCGIFNETMASK (только основной адрес), IPv6 — /proc/net/if_inet6.

    Строка if_inet6 — ``%pi6 %02x %02x %02x %02x %8s`` (ядро, addrconf.c if6_seq_show): адрес
    32 шестнадцатеричных знака, номер карты, длина префикса (тоже шестнадцатерично!), область,
    флаги, имя карты.
    """
    import fcntl  # noqa: PLC0415 — только POSIX
    итог: dict[str, list[dict[str, Any]]] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as с:
        for имя in имена:
            запрос = struct.pack("256s", имя.encode())          # ядро само обрывает имя на IFNAMSIZ
            try:
                адрес = socket.inet_ntop(socket.AF_INET, fcntl.ioctl(с.fileno(), SIOCGIFADDR, запрос)[20:24])
            except OSError:
                continue
            try:
                маска = fcntl.ioctl(с.fileno(), SIOCGIFNETMASK, запрос)[20:24]
                префикс = длина_префикса(socket.inet_ntop(socket.AF_INET, маска))
            except OSError:
                префикс = None
            итог.setdefault(имя, []).append({"вид": "ipv4", "адрес": адрес,
                                             "префикс": префикс})
    try:
        строки = if_inet6.read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        строки = []
    for строка in строки:
        поля = строка.split()
        if len(поля) != 6 or len(поля[0]) != 32:
            continue
        try:
            адрес6 = socket.inet_ntop(socket.AF_INET6, bytes.fromhex(поля[0]))
            префикс6 = int(поля[2], 16)
        except ValueError:
            continue
        итог.setdefault(поля[5], []).append({"вид": "ipv6", "адрес": адрес6, "префикс": префикс6})
    return итог


def карты_linux(корень: Path = Path("/sys/class/net"), адреса: dict[str, list[dict[str, Any]]] | None = None
                ) -> list[dict[str, Any]]:
    """Карты Linux из sysfs; адреса — из getifaddrs (или переданы готовыми — для проверки)."""
    if адреса is None:
        try:
            адреса = адреса_getifaddrs()
        except (OSError, AttributeError):
            адреса = адреса_ioctl([п.name for п in корень.iterdir()] if корень.is_dir() else [])
    итог = []
    for папка in sorted(корень.iterdir()) if корень.is_dir() else []:
        имя = папка.name
        флаги = _число(_прочитать(папка / "flags"), 16) or 0
        тип = _число(_прочитать(папка / "type")) or 0
        операция = _прочитать(папка / "operstate") or "unknown"
        скорость = _число(_прочитать(папка / "speed"))       # Мбит/с; −1 или ошибка — неизвестна
        петля = тип == ARPHRD_LOOPBACK or bool(флаги & IFF_LOOPBACK)
        # У петли и виртуальных карт operstate «unknown», хотя они работают: тогда верим флагу
        # IFF_UP и наличию связи (carrier = 1). IFF_RUNNING в sysfs flags не попадает.
        работает = операция == "up" or (операция == "unknown" and bool(флаги & IFF_UP)
                                        and _прочитать(папка / "carrier") == "1")
        драйвер = (папка / "device" / "driver")
        драйвер_имя = драйвер.resolve().name if драйвер.exists() else ""
        вид = ("петля" if петля else "Wi-Fi" if (папка / "wireless").exists() or тип == ARPHRD_IEEE80211
               else "Ethernet" if тип == ARPHRD_ETHER else f"ARPHRD {тип}")
        mac = _прочитать(папка / "address")
        if mac.strip("0:") == "":
            mac = ""
        свои = адреса.get(имя, [])
        итог.append({
            "ид": имя, "имя": имя, "описание": вид + (f", драйвер {драйвер_имя}" if драйвер_имя else ""),
            "вид": вид, "mac": mac,
            "ipv4": [{"адрес": а["адрес"], "префикс": а["префикс"]} for а in свои if а["вид"] == "ipv4"],
            "ipv6": [{"адрес": а["адрес"], "префикс": а["префикс"]} for а in свои if а["вид"] == "ipv6"],
            "состояние": СОСТОЯНИЯ.get("up" if работает else операция, операция), "работает": работает,
            "скорость": скорость * 1_000_000 if скорость is not None and скорость > 0 else None,
            "mtu": _число(_прочитать(папка / "mtu")), "петля": петля, "arphrd": тип,
            "устройство_pcap": имя,
        })
    return итог


# -- Windows ------------------------------------------------------------------------------

class SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int32)]


class IP_ADAPTER_UNICAST_ADDRESS_LH(ctypes.Structure):
    # Начинается с union { ULONGLONG Alignment; struct { ULONG Length; DWORD Flags; } } —
    # выравнивание 8 даёт первое поле uint64 объединения; ниже оно разложено на два ULONG.
    _fields_ = [("Length", ctypes.c_uint32), ("Flags", ctypes.c_uint32), ("Next", ctypes.c_void_p),
                ("Address", SOCKET_ADDRESS), ("PrefixOrigin", ctypes.c_int32), ("SuffixOrigin", ctypes.c_int32),
                ("DadState", ctypes.c_int32), ("ValidLifetime", ctypes.c_uint32),
                ("PreferredLifetime", ctypes.c_uint32), ("LeaseLifetime", ctypes.c_uint32),
                ("OnLinkPrefixLength", ctypes.c_uint8), ("_выравнивание", ctypes.c_uint64 * 0)]


class IP_ADAPTER_ADDRESSES_LH(ctypes.Structure):
    # Как и у адреса, начало — union с ULONGLONG Alignment: выравнивание 8 даёт указатель Next.
    _fields_ = [("Length", ctypes.c_uint32), ("IfIndex", ctypes.c_uint32), ("Next", ctypes.c_void_p),
                ("AdapterName", ctypes.c_void_p), ("FirstUnicastAddress", ctypes.c_void_p),
                ("FirstAnycastAddress", ctypes.c_void_p), ("FirstMulticastAddress", ctypes.c_void_p),
                ("FirstDnsServerAddress", ctypes.c_void_p), ("DnsSuffix", ctypes.c_void_p),
                ("Description", ctypes.c_void_p), ("FriendlyName", ctypes.c_void_p),
                ("PhysicalAddress", ctypes.c_uint8 * 8), ("PhysicalAddressLength", ctypes.c_uint32),
                ("Flags", ctypes.c_uint32), ("Mtu", ctypes.c_uint32), ("IfType", ctypes.c_uint32),
                ("OperStatus", ctypes.c_int32), ("Ipv6IfIndex", ctypes.c_uint32),
                ("ZoneIndices", ctypes.c_uint32 * 16), ("FirstPrefix", ctypes.c_void_p),
                ("TransmitLinkSpeed", ctypes.c_uint64), ("ReceiveLinkSpeed", ctypes.c_uint64)]
    # Дальше в IP_ADAPTER_ADDRESSES_LH (iptypes.h) — FirstWinsServerAddress … FirstDnsSuffix, всего
    # 448 байт на x64. Они не читаются, а по цепочке идём по указателю Next, не по размеру
    # структуры, — поэтому структура описана до ReceiveLinkSpeed.


#: Семейства адресов Windows (ws2def.h): AF_INET = 2, AF_INET6 = 23.
AF_INET_WINDOWS, AF_INET6_WINDOWS = 2, 23
#: Флаги GetAdaptersAddresses (iptypes.h): без anycast, multicast и DNS — нам не нужны.
GAA_FLAG_SKIP_ANYCAST, GAA_FLAG_SKIP_MULTICAST, GAA_FLAG_SKIP_DNS_SERVER = 0x02, 0x04, 0x08
ERROR_BUFFER_OVERFLOW = 111
#: Предел длины строки WCHAR при чтении — защита от указателя в никуда.
ДЛИНА_СТРОКИ = 1024


def строка_utf16(адрес: int | None) -> str:
    """Строка WCHAR (UTF-16LE, до нулевого слова) по адресу."""
    if not адрес:
        return ""
    слова = []
    for i in range(ДЛИНА_СТРОКИ):
        слово = ctypes.c_uint16.from_address(адрес + 2 * i).value
        if слово == 0:
            break
        слова.append(слово)
    return b"".join(с.to_bytes(2, "little") for с in слова).decode("utf-16-le", "replace")


def адрес_windows(sa: int | None) -> tuple[str, str] | None:
    """SOCKADDR Windows: sockaddr_in (адрес с байта 4) или sockaddr_in6 (с байта 8), ws2def.h/ws2ipdef.h."""
    if not sa:
        return None
    семейство = ctypes.c_uint16.from_address(sa).value
    if семейство == AF_INET_WINDOWS:
        return "ipv4", socket.inet_ntop(socket.AF_INET, ctypes.string_at(sa + 4, 4))
    if семейство == AF_INET6_WINDOWS:
        return "ipv6", socket.inet_ntop(socket.AF_INET6, ctypes.string_at(sa + 8, 16))
    return None


def разобрать_адаптеры(первый: int) -> list[dict[str, Any]]:
    """Цепочка IP_ADAPTER_ADDRESSES_LH (по полю Next) → карты в общем виде."""
    итог = []
    адрес, шагов = первый, 0
    while адрес and шагов < 256:
        а = IP_ADAPTER_ADDRESSES_LH.from_address(адрес)
        имя_адаптера = ctypes.string_at(а.AdapterName).decode("ascii", "replace") if а.AdapterName else ""
        ipv4, ipv6 = [], []
        у, адресов = а.FirstUnicastAddress, 0
        while у and адресов < 64:
            у_ = IP_ADAPTER_UNICAST_ADDRESS_LH.from_address(у)
            ip = адрес_windows(у_.Address.lpSockaddr)
            if ip:
                (ipv4 if ip[0] == "ipv4" else ipv6).append({"адрес": ip[1], "префикс": int(у_.OnLinkPrefixLength)})
            у, адресов = у_.Next, адресов + 1
        операция = СОСТОЯНИЯ_WINDOWS.get(int(а.OperStatus), "unknown")
        скорость = int(а.ReceiveLinkSpeed)
        итог.append({
            "ид": имя_адаптера, "имя": строка_utf16(а.FriendlyName) or имя_адаптера,
            "описание": строка_utf16(а.Description), "вид": ВИДЫ_WINDOWS.get(int(а.IfType), f"IfType {а.IfType}"),
            "mac": bytes(а.PhysicalAddress)[:int(а.PhysicalAddressLength)].hex(":"),
            "ipv4": ipv4, "ipv6": ipv6, "состояние": СОСТОЯНИЯ[операция], "работает": операция == "up",
            # «Неизвестно» Windows пишет как ULONG64 из единиц (документация GetAdaptersAddresses).
            "скорость": скорость if 0 < скорость < 0xFFFFFFFFFFFFFFFF else None,
            "mtu": int(а.Mtu), "петля": int(а.IfType) == 24, "arphrd": None,
            "устройство_pcap": "\\Device\\NPF_" + имя_адаптера if имя_адаптера.startswith("{") else None,
            "индекс": int(а.IfIndex),
        })
        адрес, шагов = а.Next, шагов + 1
    return итог


def карты_windows() -> list[dict[str, Any]]:
    """GetAdaptersAddresses(AF_UNSPEC): два вызова — узнать размер буфера и получить данные."""
    iphlpapi = ctypes.WinDLL("iphlpapi")                              # type: ignore[attr-defined]
    функция = iphlpapi.GetAdaptersAddresses
    функция.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.POINTER(ctypes.c_uint32)]
    функция.restype = ctypes.c_uint32
    флаги = GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER
    размер = ctypes.c_uint32(15 * 1024)                               # рекомендовано документацией
    for _ in range(4):
        буфер = ctypes.create_string_buffer(размер.value)
        код = функция(0, флаги, None, буфер, ctypes.byref(размер))
        if код == 0:
            return разобрать_адаптеры(ctypes.addressof(буфер))
        if код != ERROR_BUFFER_OVERFLOW:
            raise OSError(код, f"GetAdaptersAddresses: ошибка {код}")
    raise OSError(ERROR_BUFFER_OVERFLOW, "GetAdaptersAddresses: буфер всё время мал")


# -- общее --------------------------------------------------------------------------------

def карты_из_pcap(устройства: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Карты по одному pcap_findalldevs — на системах, где своего перечня нет."""
    from .pcap_bib import (  # noqa: PLC0415
        PCAP_IF_LOOPBACK,
        PCAP_IF_RUNNING,
        PCAP_IF_UP,
    )
    итог = []
    for у in устройства:
        работает = bool(у["флаги"] & PCAP_IF_UP) and bool(у["флаги"] & PCAP_IF_RUNNING)
        итог.append({"ид": у["имя"], "имя": у["имя"], "описание": у["описание"], "вид": "",
                     "mac": у.get("mac", ""),
                     "ipv4": [{"адрес": а["адрес"], "префикс": а["префикс"]} for а in у["адреса"] if а["вид"] == "ipv4"],
                     "ipv6": [{"адрес": а["адрес"], "префикс": а["префикс"]} for а in у["адреса"] if а["вид"] == "ipv6"],
                     "состояние": "работает" if работает else "отключена", "работает": работает,
                     "скорость": None, "mtu": None, "петля": bool(у["флаги"] & PCAP_IF_LOOPBACK),
                     "arphrd": None, "устройство_pcap": у["имя"]})
    return итог


def сопоставить_npcap(карты: list[dict[str, Any]], устройства: list[dict[str, Any]]) -> None:
    """Имя устройства Npcap (\\Device\\NPF_{GUID}) — к карте с тем же GUID."""
    по_guid = {у["имя"][у["имя"].index("_{") + 1:].upper(): у for у in устройства if "_{" in у["имя"]}
    for к in карты:
        у = по_guid.get(str(к["ид"]).upper())
        к["устройство_pcap"] = у["имя"] if у else None
        if у and not к["описание"]:
            к["описание"] = у["описание"]


def перечень(устройства_pcap: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Карты этой машины и заметки (что не удалось узнать)."""
    заметки: list[str] = []
    if WINDOWS:
        try:
            карты = карты_windows()
        except OSError as ошибка:
            заметки.append(f"перечень карт Windows не получен: {ошибка}")
            карты = карты_из_pcap(устройства_pcap or [])
        else:
            if устройства_pcap is not None:
                сопоставить_npcap(карты, устройства_pcap)
    elif Path("/sys/class/net").is_dir():
        карты = карты_linux()
    else:
        карты = карты_из_pcap(устройства_pcap or [])
        if not карты:
            заметки.append("перечень карт недоступен: нет /sys/class/net и библиотеки захвата")
    return карты, заметки
