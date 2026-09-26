"""Gigabit Ethernet 1000BASE-X (IEEE 802.3, разд. 36): упорядоченные наборы поверх 8B/10B → кадры.

Код 8B/10B несёт, кроме байт данных, служебные K-символы, и из них PCS
складывает упорядоченные наборы:

- /I1/ = K28.5 D5.6 и /I2/ = K28.5 D16.2 — паузы (idle) между кадрами;
- /C1/ = K28.5 D21.5 и /C2/ = K28.5 D2.2 и два байта — слово автосогласования;
- /S/ = K27.7 — начало кадра: стоит на месте первого байта преамбулы (0x55);
- /T/ = K29.7 — конец кадра, /R/ = K23.7 — продление несущей, /V/ = K30.7 — ошибка.

Между /S/ и /T/ — преамбула (ещё шесть 0x55), SFD 0xD5 и кадр Ethernet с FCS.
Коды K.x.7 строятся по правилу кода: 6b-подблок D.x и альтернативный 4b-подблок
0111/1000 (как в кодере LiteX code_8b10b: alt7 при K); поведение PCS — как в LiteEth
(pcs_1000basex.py: K28.5, K27.7 → первый байт преамбулы 0x55, K29.7, K23.7).
"""

from __future__ import annotations

import zlib
from collections import Counter
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import lineynye, pakety
from .nahodka import Находка

#: Имена K-символов (x, y) → имя набора.
ИМЕНА_K = {(28, 5): "K28.5", (23, 7): "/R/", (27, 7): "/S/", (29, 7): "/T/", (30, 7): "/V/"}


def _символ_k(x: int, y: int, rd: int) -> int:
    """10-битовый K-символ при текущей несбалансированности rd (0 — RD−, 1 — RD+)."""
    if (x, y) == (28, 5):
        return lineynye.K28_5[rd]
    шесть = lineynye._6B[x]
    if rd and bin(шесть).count("1") != 3:                   # у K.x.7 (x = 23, 27, 29, 30) 6b-код D.7 не бывает
        шесть ^= 0b111111
    после = rd ^ (bin(шесть).count("1") != 3)
    четыре = lineynye._4B_A7 ^ (0b1111 if после else 0)       # K.x.7 — всегда альтернативный 4b
    return (шесть << 4) | четыре


#: 10-битовый символ → имя K-символа.
K_СИМВОЛЫ: Dict[int, str] = {_символ_k(x, y, rd): имя for (x, y), имя in ИМЕНА_K.items() for rd in (0, 1)}
#: Байты данных после K28.5: пауза /I1/ /I2/ и слово автосогласования /C1/ /C2/.
ПОСЛЕ_ЗАПЯТОЙ = {0xC5: "/I1/", 0x50: "/I2/", 0xB5: "/C1/", 0x42: "/C2/"}   # D5.6, D16.2, D21.5, D2.2

ПРЕАМБУЛА = b"\x55" * 7 + b"\xd5"


def _crc32(данные: bytes) -> int:
    return zlib.crc32(данные) & 0xFFFFFFFF


def декодировать(символы: np.ndarray) -> List[Tuple[str, int]]:
    """Символы → [(«D», байт) | («K», имя) | («?», символ)]."""
    итог = []
    for с in символы.tolist():
        if с in K_СИМВОЛЫ:
            итог.append(("K", K_СИМВОЛЫ[с]))
        elif с in lineynye.ТАБЛИЦА_8B10B:
            итог.append(("D", lineynye.ТАБЛИЦА_8B10B[с]))
        else:
            итог.append(("?", с))
    return итог


def кадры(поток: List[Tuple[str, int]]) -> Tuple[List[bytes], Counter]:
    """Кадры Ethernet между /S/ и /T/ (после преамбулы и SFD) и счёт упорядоченных наборов."""
    счёт: Counter = Counter()
    итог: List[bytes] = []
    текущий: Optional[bytearray] = None
    for i, (вид, значение) in enumerate(поток):
        if вид == "K":
            if значение == "K28.5" and i + 1 < len(поток) and поток[i + 1][0] == "D":
                счёт[ПОСЛЕ_ЗАПЯТОЙ.get(поток[i + 1][1], "K28.5 + D?")] += 1
            else:
                счёт[значение] += 1
            if значение == "/S/":
                текущий = bytearray(b"\x55")                   # /S/ — вместо первого байта преамбулы
            elif значение == "/T/" and текущий is not None:
                итог.append(bytes(текущий))
                текущий = None
            elif значение not in ("/S/", "/T/"):
                текущий = None if значение != "/R/" else текущий
        elif вид == "D" and текущий is not None:
            текущий.append(значение)
        elif вид == "?":
            счёт["недопустимых символов"] += 1
            текущий = None
    return итог, счёт


def найти(биты: np.ndarray) -> Optional[Находка]:
    """1000BASE-X: выравнивание по запятой K28.5, упорядоченные наборы, кадры с верной FCS."""
    выборка = np.asarray(биты[:lineynye.ВЫБОРКА * 4], dtype=np.uint8)
    лучший = None
    for сдвиг in range(10):
        символы = lineynye._символы(выборка, 10, сдвиг)
        if len(символы) < 200:
            return None
        запятых = int(np.isin(символы, lineynye.K28_5).sum())
        if лучший is None or запятых > лучший[0]:
            лучший = (запятых, сдвиг, символы)
    запятых, сдвиг, символы = лучший
    if запятых < 3:
        return None
    поток = декодировать(символы)
    сырые, счёт = кадры(поток)
    годные, с_преамбулой = [], 0
    for к in сырые:
        if к[:8] == ПРЕАМБУЛА:
            с_преамбулой += 1
            кадр = к[8:]
            if len(кадр) >= 64 and _crc32(кадр[:-4]) == int.from_bytes(кадр[-4:], "little"):
                годные.append(кадр[:-4])
    if not годные:
        return None
    ip = pakety.найти_в_кадрах(годные, "кадрах Ethernet")
    подробно = [
        f"символы по 10 бит с бита {сдвиг}; запятых K28.5 на границе символа: {запятых}",
        "упорядоченные наборы: " + ", ".join(f"{и}×{с}" for и, с in счёт.most_common(10)),
        f"кадров между /S/ и /T/: {len(сырые)}, с преамбулой и SFD: {с_преамбулой}, "
        f"FCS (CRC-32) верна у {len(годные)}",
    ]
    if ip is not None:
        подробно.append(f"IP в кадрах: {len(ip.дальше)} пакетов")
    return Находка(
        уровень="канальный", что="Gigabit Ethernet 1000BASE-X: кадры Ethernet",
        уверенность=len(годные) / max(1, с_преамбулой),
        мера=f"{len(годные)} кадров с верной FCS между /S/ и /T/ поверх 8B/10B",
        подробно=подробно, дальше=годные, вид_дальше="кадры")


def закодировать(кадры_: List[bytes], пауз: int = 6) -> np.ndarray:
    """Кодер 1000BASE-X (для проверок): /I/ между кадрами, /S/ преамбула SFD кадр FCS /T/ /R/."""
    rd = 0
    символы: List[int] = []

    def данные(байт: int) -> None:
        nonlocal rd
        x, y = байт & 31, байт >> 5
        шесть = lineynye._6B[x]
        if rd and (bin(шесть).count("1") != 3 or шесть == 0b111000):
            шесть ^= 0b111111
        rd ^= bin(шесть).count("1") != 3
        четыре = lineynye._4B[y]
        if y == 7 and ((rd == 0 and x in (17, 18, 20)) or (rd == 1 and x in (11, 13, 14))):
            четыре = lineynye._4B_A7
        if rd and (bin(четыре).count("1") != 2 or четыре == 0b1100):
            четыре ^= 0b1111
        rd ^= bin(четыре).count("1") != 2
        символы.append((шесть << 4) | четыре)

    def k(x: int, y: int) -> None:
        nonlocal rd
        с = _символ_k(x, y, rd)
        символы.append(с)
        rd ^= bin(с).count("1") != 5

    def пауза() -> None:
        k(28, 5)
        данные(0xC5 if rd else 0x50)                      # /I1/ возвращает RD−, /I2/ — сохраняет
    for _ in range(пауз):
        пауза()
    for кадр in кадры_:
        тело = кадр + _crc32(кадр).to_bytes(4, "little")
        k(27, 7)
        for б in ПРЕАМБУЛА[1:] + тело:
            данные(б)
        k(29, 7)
        k(23, 7)
        if len(символы) % 2:
            k(23, 7)
        for _ in range(пауз):
            пауза()
    return np.array([(с >> (9 - i)) & 1 for с in символы for i in range(10)], dtype=np.uint8)
