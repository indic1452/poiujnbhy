"""Проверка параметров захвата и фильтр кадров (тот же, что уходит в BPF).

Всё, что пришло из браузера, проверяется здесь: порты — целые 1…65535, адреса —
через ``ipaddress``, пределы — не выше потолков из настроек. Выражение BPF
собирается только из проверенных значений (синтаксис — pcap-filter(7)), так что
произвольный текст в libpcap не попадает.

Трафик самого сервера в захват с карты не попадает никогда, что бы ни задал
человек: порты TCP приложения и его служб (``исключить``) вычёркиваются и в
BPF, и в отборе программой. Иначе захват петли или рабочей карты отдал бы
открытым текстом cookie сессий и пароли входа (nginx передаёт запросы
приложению по http на 127.0.0.1:8080, docs/10) и запросы к llama-server.
Оговорка pcap-filter(7): «port» совпадает только с первым фрагментом IPv4 и с
IPv6 без заголовков расширения — так же и в программе (у нефрагмента порта нет).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any

from .zapis import ETHERTYPE_ARP, IP_ICMP, IP_ICMPV6, IP_TCP, IP_UDP, Сведения

#: Сколько портов UDP можно слушать одним захватом.
ПОРТОВ_ДО = 16
#: Предел числа пакетов, выше которого не пустим ни при каких настройках.
ПАКЕТОВ_ДО = 10_000_000
РЕЖИМЫ = ("udp", "карта")
СПОСОБЫ = ("авто", "npcap", "libpcap", "af_packet", "sio_rcvall")
ПРОТОКОЛЫ = ("", "ip", "ip6", "udp", "tcp", "icmp", "arp")
#: Метки VLAN, которые libpcap понимает под «vlan»: 802.1Q, 802.1ad, QinQ
#: (gencode.c gen_vlan_tpid_test; значения — ethertype.h ETHERTYPE_8021Q/8021AD/8021QINQ).
МЕТКИ_VLAN = (0x8100, 0x88A8, 0x9100)


def адрес_ip(текст: Any, что: str) -> str:
    if текст in (None, ""):
        return ""
    try:
        return str(ipaddress.ip_address(str(текст).strip()))
    except ValueError:
        raise ValueError(f"{что}: «{str(текст)[:60]}» — не адрес IPv4 или IPv6") from None


def целое(значение: Any, что: str, наименьшее: int, наибольшее: int) -> int:
    if isinstance(значение, bool):
        raise ValueError(f"{что}: нужно целое число")
    try:
        число = int(str(значение).strip())
    except ValueError:
        raise ValueError(f"{что}: нужно целое число, а не «{str(значение)[:40]}»") from None
    if not наименьшее <= число <= наибольшее:
        raise ValueError(f"{что}: допустимо от {наименьшее} до {наибольшее}")
    return число


def разобрать_порты(значение: Any) -> list[int]:
    """«5000, 5002-5004» или [5000, 5002] → [5000, 5002, 5003, 5004]; каждый 1…65535."""
    if isinstance(значение, list | tuple):
        части = [str(ч) for ч in значение]
    else:
        части = [ч for ч in re.split(r"[,;\s]+", str(значение or "").strip()) if ч]
    порты: list[int] = []
    for часть in части:
        if re.fullmatch(r"\d+\s*[-–]\s*\d+", часть):
            а, б = (целое(ч, "порт", 1, 65535) for ч in re.split(r"\s*[-–]\s*", часть))
            if б < а or б - а >= ПОРТОВ_ДО:
                raise ValueError(f"порты: диапазон «{часть}» задан неверно или длиннее {ПОРТОВ_ДО}")
            порты += range(а, б + 1)
        else:
            порты.append(целое(часть, "порт", 1, 65535))
    порты = list(dict.fromkeys(порты))
    if len(порты) > ПОРТОВ_ДО:
        raise ValueError(f"порты: не больше {ПОРТОВ_ДО} за один захват")
    return порты


@dataclass
class Фильтр:
    """Отбор кадров: протокол, адрес отправителя/получателя/любой стороны, порт любой стороны."""

    протокол: str = ""
    от: str = ""
    к: str = ""
    хост: str = ""
    порт: int = 0
    #: Порты TCP самого сервера — не пишутся никогда (задаёт сервер, не человек).
    исключить: tuple[int, ...] = ()

    @classmethod
    def из(cls, данные: Any) -> Фильтр:
        данные = данные if isinstance(данные, dict) else {}
        протокол = str(данные.get("протокол") or данные.get("protocol") or "").strip().lower()
        if протокол not in ПРОТОКОЛЫ:
            raise ValueError(f"фильтр: протокол — одно из {', '.join(п for п in ПРОТОКОЛЫ if п)} или пусто")
        порт = данные.get("порт") or данные.get("port") or 0
        return cls(протокол=протокол,
                   от=адрес_ip(данные.get("от") or данные.get("src"), "фильтр: адрес отправителя"),
                   к=адрес_ip(данные.get("к") or данные.get("dst"), "фильтр: адрес получателя"),
                   хост=адрес_ip(данные.get("хост") or данные.get("host"), "фильтр: адрес"),
                   порт=целое(порт, "фильтр: порт", 1, 65535) if порт not in (0, "0", "") else 0)

    def пуст(self) -> bool:
        return not (self.протокол or self.от or self.к or self.хост or self.порт or self.исключить)

    def в_словарь(self) -> dict[str, Any]:
        return {"протокол": self.протокол, "от": self.от, "к": self.к, "хост": self.хост, "порт": self.порт,
                "исключить": list(self.исключить)}

    def bpf(self, vlan: bool = True) -> str:
        """Выражение pcap-filter(7). Метки VLAN: то же выражение ещё раз после «vlan».

        «vlan» сдвигает разбор для всего, что после него (pcap-filter(7)), поэтому ветка без
        меток идёт первой. С вычеркнутыми портами в ней есть «not», и кадр с меткой прошёл бы
        её мимо проверки портов (у него на месте типа — 0x8100) — такие кадры отсекаются по типу
        явно. ``vlan=False`` — для каналов без уровня Ethernet (там «vlan» не собирается).
        """
        части = []
        if self.протокол:
            части.append({"icmp": "(icmp or icmp6)"}.get(self.протокол, self.протокол))
        for слово, адрес in (("src host", self.от), ("dst host", self.к), ("host", self.хост)):
            if адрес:
                версия = "ip6" if ":" in адрес else "ip"
                части.append(f"{версия} {слово} {адрес}")
        if self.порт:
            части.append(f"(tcp port {self.порт} or udp port {self.порт})")
        if self.исключить:
            части.append("not (" + " or ".join(f"tcp port {п}" for п in self.исключить) + ")")
        if not части:
            return ""
        выражение = " and ".join(части)
        if not vlan:
            return выражение
        if self.исключить:
            метки = " or ".join(f"ether proto 0x{т:04x}" for т in МЕТКИ_VLAN)
            return f"(not ({метки}) and {выражение}) or (vlan and ({выражение}))"
        return f"({выражение}) or (vlan and ({выражение}))"

    def подходит(self, с: Сведения) -> bool:
        """То же, что BPF выше, на разобранном кадре (для способов без BPF и для проверки)."""
        if self.протокол:
            годен = {"ip": с.версия == 4, "ip6": с.версия == 6, "udp": с.протокол == IP_UDP,
                     "tcp": с.протокол == IP_TCP, "icmp": с.протокол in (IP_ICMP, IP_ICMPV6),
                     "arp": с.тип == ETHERTYPE_ARP}[self.протокол]
            if not годен:
                return False
        if self.от and с.от != self.от:
            return False
        if self.к and с.к != self.к:
            return False
        if self.хост and self.хост not in (с.от, с.к):
            return False
        if self.порт:
            if с.протокол not in (IP_TCP, IP_UDP) or self.порт not in (с.порт_от, с.порт_к):
                return False
        if с.протокол == IP_TCP and (с.порт_от in self.исключить or с.порт_к in self.исключить):
            return False
        return True


@dataclass
class Параметры:
    режим: str
    карта: str = ""
    адрес: str = ""
    порты: list[int] = field(default_factory=list)
    группа: str = ""
    способ: str = "авто"
    фильтр: Фильтр = field(default_factory=Фильтр)
    неразборчиво: bool = True
    секунд: int = 0
    пакетов: int = 0
    байт: int = 0
    имя: str = ""

    def в_словарь(self) -> dict[str, Any]:
        return {"режим": self.режим, "карта": self.карта, "адрес": self.адрес, "порты": self.порты,
                "группа": self.группа, "способ": self.способ, "фильтр": self.фильтр.в_словарь(),
                "неразборчиво": self.неразборчиво,
                "пределы": {"секунд": self.секунд, "пакетов": self.пакетов, "байт": self.байт}}


def проверить(данные: Any, *, потолок_секунд: int, потолок_байт: int,
             исключить_порты: tuple[int, ...] | list[int] = ()) -> Параметры:
    """Параметры из запроса → Параметры или ValueError с понятной причиной.

    ``исключить_порты`` — порты TCP сервера и его служб: при захвате с карты они
    вычёркиваются всегда (задаёт сервер, из запроса не берётся).
    """
    if not isinstance(данные, dict):
        raise ValueError("параметры захвата — объект JSON")
    режим = str(данные.get("режим") or данные.get("mode") or "").strip().lower()
    if режим not in РЕЖИМЫ:
        raise ValueError("режим: «udp» (приём UDP) или «карта» (захват с карты)")
    пределы = данные.get("пределы") or данные.get("limits") or {}
    if not isinstance(пределы, dict):
        raise ValueError("пределы — объект: секунд, пакетов, мегабайт")
    секунд = пределы.get("секунд") or 0
    пакетов = пределы.get("пакетов") or 0
    мегабайт = пределы.get("мегабайт") or 0
    п = Параметры(
        режим=режим,
        карта=str(данные.get("карта") or данные.get("card") or "").strip()[:200],
        секунд=целое(секунд, "предел времени, с", 1, потолок_секунд) if секунд else потолок_секунд,
        пакетов=целое(пакетов, "предел пакетов", 1, ПАКЕТОВ_ДО) if пакетов else ПАКЕТОВ_ДО,
        байт=(целое(мегабайт, "предел объёма, МБ", 1, max(1, потолок_байт >> 20)) << 20) if мегабайт else потолок_байт,
        неразборчиво=bool(данные.get("неразборчиво", True)),
        имя=re.sub(r"[\x00-\x1f\\/:*?\"<>|]", "", str(данные.get("имя") or ""))[:80].strip(),
    )
    п.байт = min(п.байт, потолок_байт)
    if режим == "udp":
        п.адрес = адрес_ip(данные.get("адрес") or данные.get("address") or "0.0.0.0", "адрес приёма")
        п.порты = разобрать_порты(данные.get("порты") or данные.get("ports"))
        if not п.порты:
            raise ValueError("укажите порт UDP (1…65535), можно несколько через запятую")
        группа = адрес_ip(данные.get("группа") or данные.get("group"), "группа многоадресной рассылки")
        if группа:
            if not ipaddress.ip_address(группа).is_multicast:
                raise ValueError(f"группа {группа} — не адрес многоадресной рассылки")
            if ipaddress.ip_address(группа).version != ipaddress.ip_address(п.адрес).version:
                raise ValueError("группа и адрес приёма — разных версий IP")
        п.группа = группа
    else:
        if not п.карта:
            raise ValueError("выберите сетевую карту")
        п.способ = str(данные.get("способ") or данные.get("method") or "авто").strip().lower()
        if п.способ not in СПОСОБЫ:
            raise ValueError("способ захвата: " + ", ".join(СПОСОБЫ))
        п.фильтр = Фильтр.из(данные.get("фильтр") or данные.get("filter") or {})
        п.фильтр.исключить = tuple(sorted({int(п_) for п_ in исключить_порты if 1 <= int(п_) <= 65535}))
    return п
