"""Эталонный генератор GFP (G.7041) для тестов — независимо от reportgen.potok.gfp.

Всё считается здесь же, побитно и по определению, без кода анализатора:

* cHEC, tHEC, eHEC — CRC-16 x¹⁶ + x¹² + x⁵ + 1, начальное 0, старшим битом вперёд
  (Wireshark crc16_r3_ccitt; RFC 2823, 3.9: «initial value zero»);
* pFCS — CRC-32 0x04C11DB7, начальное FFFFFFFF, старшим битом вперёд, результат
  дополняется, передаётся старшим байтом вперёд (packet-gfp.c: fcs == ~crc32_mpeg2);
* основной заголовок складывается с маской; область нагрузки скремблируется
  самосинхронизирующимся x⁴³ + 1 — y[n] = x[n] ⊕ y[n − 43] — сквозным состоянием
  по всем кадрам (скремблер не тактируется на заголовках ядра, RFC 2823, 3.7).
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence

import numpy as np


def crc16(данные: bytes, многочлен: int = 0x1021) -> int:
    рег = 0
    for байт in данные:
        рег ^= байт << 8
        for _ in range(8):
            рег = ((рег << 1) ^ многочлен) & 0xFFFF if рег & 0x8000 else (рег << 1) & 0xFFFF
    return рег


def crc32_старшим(данные: bytes) -> int:
    рег = 0xFFFFFFFF
    for байт in данные:
        рег ^= байт << 24
        for _ in range(8):
            рег = ((рег << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if рег & 0x80000000 else (рег << 1) & 0xFFFFFFFF
    return рег ^ 0xFFFFFFFF


def ethernet_с_fcs(кадр: bytes) -> bytes:
    """Кадр Ethernet с FCS (IEEE 802.3: CRC-32, младшим байтом вперёд)."""
    return кадр + zlib.crc32(кадр).to_bytes(4, "little")


def нагрузка(данные: bytes, *, pti: int = 0, upi: int = 1, pfi: bool = False, cid: int | None = None,
             запас: int = 0, плохой_thec: bool = False, многочлен: int = 0x1021,
             порядок_hec: str = "big") -> bytes:
    """Область нагрузки: поле типа с tHEC, [CID, запас, eHEC], данные, [pFCS].
    ``порядок_hec`` — «little»: tHEC и eHEC младшим байтом вперёд (нестандартная аппаратура)."""
    exi = 0 if cid is None else 1
    тип = bytes([(pti << 5) | (int(pfi) << 4) | exi, upi])
    thec = crc16(тип, многочлен) ^ (0x0101 if плохой_thec else 0)
    область = тип + thec.to_bytes(2, порядок_hec)
    if cid is not None:
        доп = bytes([cid, запас])
        область += доп + crc16(доп, многочлен).to_bytes(2, порядок_hec)
    область += данные
    if pfi:
        область += crc32_старшим(данные).to_bytes(4, "big")
    return область


class Скремблер:
    """y[n] = x[n] ⊕ y[n − 43], состояние — последние 43 выходных бита."""

    def __init__(self) -> None:
        self.история = np.zeros(43, dtype=np.uint8)

    def __call__(self, байты: bytes) -> bytes:
        x = np.unpackbits(np.frombuffer(байты, dtype=np.uint8))
        y = np.concatenate([self.история, np.zeros(len(x), dtype=np.uint8)])
        for n in range(len(x)):
            y[43 + n] = x[n] ^ y[n]
        self.история = y[-43:].copy()
        return np.packbits(y[43:]).tobytes()


def поток(кадры: Sequence[bytes | None], маска: int = 0xB6AB31E0, *, скремблер: bool = True,
          многочлен: int = 0x1021, порядок_hec: str = "big") -> bytes:
    """Кадры подряд: None — пустой кадр, байты — область нагрузки (PLI = её длина);
    ``порядок_hec`` — «little»: cHEC младшим байтом вперёд."""
    м = маска.to_bytes(4, "big")
    скр = Скремблер()
    итог = bytearray()
    for кадр in кадры:
        область = b"" if кадр is None else bytes(кадр)
        pli = len(область).to_bytes(2, "big")
        заголовок = pli + crc16(pli, многочлен).to_bytes(2, порядок_hec)
        итог += bytes(a ^ b for a, b in zip(заголовок, м, strict=True))
        итог += скр(область) if скремблер and область else область
    return bytes(итог)


def места_заголовков(кадры: Sequence[bytes | None], от: int = 0) -> list[int]:
    """Байт основного заголовка каждого кадра в потоке ``поток(кадры)`` (с ``от`` байт впереди)."""
    места, место = [], от
    for кадр in кадры:
        места.append(место)
        место += 4 + (0 if кадр is None else len(кадр))
    return места


def испортить(данные: bytes, *биты: int) -> bytes:
    """Инвертировать биты с этими номерами (0 — старший бит первого байта)."""
    ряд = bytearray(данные)
    for б in биты:
        ряд[б // 8] ^= 0x80 >> (б % 8)
    return bytes(ряд)


def в_ряд(данные: bytes, *, сдвиг: int = 0, порядок: str = "старший", инверсия: bool = False) -> np.ndarray:
    """Байты → биты (с ``сдвиг`` бит мусора впереди), байт младшим битом вперёд — по желанию."""
    биты = np.unpackbits(np.frombuffer(данные, dtype=np.uint8),
                         bitorder="big" if порядок == "старший" else "little")
    биты = np.concatenate([np.random.default_rng(сдвиг).integers(0, 2, сдвиг).astype(np.uint8), биты])
    return (1 - биты).astype(np.uint8) if инверсия else биты
