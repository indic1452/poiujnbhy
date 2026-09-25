# -*- coding: utf-8 -*-
"""Модель разобранного пакета: уровни, поля с местом в байтах, признаки ошибок."""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Поле:
    """Поле заголовка: подпись, ключ фильтра, значение словами, место в пакете."""
    имя: str
    ключ: str
    текст: str
    сырое: Any
    смещение: int
    длина: int
    дети: List["Поле"] = field(default_factory=list)
    плохо: bool = False

    def в_словарь(self) -> Dict[str, Any]:
        return {"имя": self.имя, "ключ": self.ключ, "текст": self.текст,
                "смещение": self.смещение, "длина": self.длина, "плохо": self.плохо,
                "дети": [д.в_словарь() for д in self.дети]}


@dataclass
class Уровень:
    """Уровень (протокол) пакета: его поля и итог одной строкой."""
    протокол: str
    полное: str
    смещение: int
    длина: int = 0
    поля: List[Поле] = field(default_factory=list)
    итог: str = ""

    def поле(self, имя: str, ключ: str, текст: Any, смещение: int, длина: int,
             сырое: Any = None, *, плохо: bool = False,
             родитель: Optional[Поле] = None) -> Поле:
        п = Поле(имя, ключ, str(текст), текст if сырое is None else сырое, смещение, длина,
                 плохо=плохо)
        (родитель.дети if родитель is not None else self.поля).append(п)
        return п

    def в_словарь(self) -> Dict[str, Any]:
        return {"протокол": self.протокол, "полное": self.полное, "смещение": self.смещение,
                "длина": self.длина, "итог": self.итог,
                # Поля без подписи — только для фильтра (ip.addr, tcp.port): в дереве их нет.
                "поля": [п.в_словарь() for п in self.поля if п.имя]}


@dataclass
class Пакет:
    """Разобранный пакет: уровни снизу вверх и сводка для списка."""
    номер: int
    время: float
    данные: bytes
    исходная_длина: int
    канал: str
    уровни: List[Уровень] = field(default_factory=list)
    источник: str = ""
    получатель: str = ""
    порт_от: Optional[int] = None
    порт_к: Optional[int] = None
    инфо: str = ""
    ошибки: List[str] = field(default_factory=list)
    #: Полезная нагрузка верхнего разобранного уровня (для потока TCP и неизвестных форматов).
    нагрузка: Optional[bytes] = None
    нагрузка_смещение: int = 0

    @property
    def протокол(self) -> str:
        """Верхний разобранный протокол; «Данные» над ним — нагрузка, не протокол."""
        свои = [у.протокол for у in self.уровни
                if у.протокол != "Данные" and not у.протокол.startswith("исходный ")]
        return свои[-1] if свои else (self.уровни[-1].протокол if self.уровни else "?")

    @property
    def стек(self) -> List[str]:
        return [у.протокол for у in self.уровни]

    def поля_фильтра(self) -> Dict[str, List[Any]]:
        """Все поля плоско: ключ → значения (для фильтров и таблиц)."""
        итог: Dict[str, List[Any]] = {"frame.len": [self.исходная_длина],
                                      "frame.number": [self.номер],
                                      "frame.time": [self.время]}

        def обойти(поля: List[Поле]):
            for п in поля:
                if п.ключ:
                    итог.setdefault(п.ключ, []).append(п.сырое)
                обойти(п.дети)

        for у in self.уровни:
            итог.setdefault(у.протокол.lower(), []).append(True)
            обойти(у.поля)
        if self.ошибки:
            итог["_ws.expert"] = list(self.ошибки)
        return итог

    def сводка(self) -> Dict[str, Any]:
        return {"номер": self.номер, "время": round(self.время, 6), "длина": self.исходная_длина,
                "протокол": self.протокол, "стек": self.стек, "источник": self.источник,
                "получатель": self.получатель, "инфо": self.инфо, "ошибки": self.ошибки}

    def в_словарь(self) -> Dict[str, Any]:
        return {**self.сводка(), "канал": self.канал, "данные": self.данные.hex(),
                "порт_от": self.порт_от, "порт_к": self.порт_к,
                "уровни": [у.в_словарь() for у in self.уровни]}


# -- чтение полей -------------------------------------------------------------------

def u8(д: bytes, м: int) -> int:
    return д[м]


def u16(д: bytes, м: int) -> int:
    return struct.unpack_from(">H", д, м)[0]


def u24(д: bytes, м: int) -> int:
    return (д[м] << 16) | (д[м + 1] << 8) | д[м + 2]


def u32(д: bytes, м: int) -> int:
    return struct.unpack_from(">I", д, м)[0]


def u64(д: bytes, м: int) -> int:
    return struct.unpack_from(">Q", д, м)[0]


def mac(д: bytes, м: int) -> str:
    return ":".join(f"{б:02x}" for б in д[м:м + 6])


def ip4(д: bytes, м: int) -> str:
    return str(ipaddress.IPv4Address(д[м:м + 4]))


def ip6(д: bytes, м: int) -> str:
    return str(ipaddress.IPv6Address(д[м:м + 16]))


def сумма16(данные: bytes) -> int:
    """Дополнительная до единицы сумма 16-битных слов (RFC 1071); 0 — сумма сошлась."""
    if len(данные) % 2:
        данные += b"\0"
    сумма = sum(struct.unpack(f">{len(данные) // 2}H", данные))
    while сумма >> 16:
        сумма = (сумма & 0xFFFF) + (сумма >> 16)
    return (~сумма) & 0xFFFF


def печатное(данные: bytes, предел: int = 60) -> str:
    текст = "".join(chr(б) if 32 <= б < 127 else "." for б in данные[:предел])
    return текст + ("…" if len(данные) > предел else "")


class Мало(Exception):
    """Пакет обрывается раньше, чем кончается заголовок."""
