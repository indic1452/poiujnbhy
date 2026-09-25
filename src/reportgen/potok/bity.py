# -*- coding: utf-8 -*-
"""Биты: распаковка, упаковка, порядок бит, выборка.

Порядок бит — не мелочь. Прибор пишет байты старшим битом вперёд, а HDLC
и многие последовательные стыки передают байт МЛАДШИМ битом вперёд. Один и
тот же поток в двух порядках — два разных битовых ряда, и флаг 0x7E в
одном из них есть, а в другом его нет. Поэтому детекторы смотрят оба.
"""

from __future__ import annotations

import numpy as np

#: Порядок бит в байте: «старший» — MSB первым, «младший» — LSB первым.
ПОРЯДКИ = ("старший", "младший")


def в_биты(данные: bytes | np.ndarray, порядок: str = "старший") -> np.ndarray:
    """Байты → массив бит (uint8, 0/1)."""
    массив = np.frombuffer(bytes(данные), dtype=np.uint8) if not isinstance(
        данные, np.ndarray) else данные.astype(np.uint8, copy=False)
    return np.unpackbits(массив, bitorder="big" if порядок == "старший" else "little")


def в_байты(биты: np.ndarray, порядок: str = "старший") -> bytes:
    """Биты → байты; хвост короче байта отбрасывается."""
    целых = len(биты) - len(биты) % 8
    return np.packbits(np.asarray(биты[:целых], dtype=np.uint8),
                       bitorder="big" if порядок == "старший" else "little").tobytes()


def из_текста(текст: str) -> np.ndarray | None:
    """Поток, присланный текстом: «0101…» или шестнадцатеричный дамп.

    None — это не поток, а обычный текст.
    """
    чистый = "".join(текст.split())
    if len(чистый) < 64:
        return None
    if set(чистый) <= set("01"):
        return np.frombuffer(чистый.encode("ascii"), dtype=np.uint8) - ord("0")
    шестн = чистый.replace("0x", "").replace("0X", "").replace(",", "").replace(":", "")
    if len(шестн) % 2 == 0 and set(шестн) <= set("0123456789abcdefABCDEF"):
        return в_биты(bytes.fromhex(шестн))
    return None


def единиц_доля(биты: np.ndarray) -> float:
    return float(биты.mean()) if len(биты) else 0.0


def самые_длинные_серии(биты: np.ndarray) -> tuple[int, int]:
    """Самые длинные серии нулей и единиц подряд."""
    if not len(биты):
        return 0, 0
    перепады = np.flatnonzero(np.diff(биты.astype(np.int8)) != 0)
    границы = np.concatenate(([-1], перепады, [len(биты) - 1]))
    длины = np.diff(границы)
    значения = биты[границы[1:]]
    нулей = int(длины[значения == 0].max()) if (значения == 0).any() else 0
    единиц = int(длины[значения == 1].max()) if (значения == 1).any() else 0
    return нулей, единиц


def инвертировать(биты: np.ndarray) -> np.ndarray:
    return (1 - биты).astype(np.uint8)
