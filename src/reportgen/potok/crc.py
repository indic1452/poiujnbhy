# -*- coding: utf-8 -*-
"""CRC вслепую: многочлен, начальное значение, финальный XOR, отражения.

Кадры с контрольной суммой в конце — самое частое в неизвестных протоколах,
а какая именно CRC — неизвестно. Модель CRC (как в каталоге RevEng):
ширина w, многочлен P, начальное значение init, финальный XOR xorout,
отражение входных байт refin и результата refout.

**Многочлен.** CRC аффинна: crc(m) = L(m) ⊕ c(длина), где L — линейная
часть (остаток m·x^w по модулю P), а c зависит только от init, xorout и
длины. У двух кадров ОДНОЙ длины c одинакова и сокращается:
crc(a) ⊕ crc(b) = (a ⊕ b)·x^w mod P. Значит многочлен
D = (a ⊕ b)·x^w ⊕ (crc(a) ⊕ crc(b)) делится на P — и P есть НОД таких D
по нескольким парам. Никакого перебора многочленов.

**init и xorout.** Когда P известен, для кадра длины ℓ бит
crc ⊕ (m·x^w mod P) = (init·x^ℓ mod P) ⊕ xorout. Кадры двух разных
длин дают линейную систему на init — и из неё xorout. Если все кадры одной
длины, разделить их нельзя: сообщается их общий вклад.

Отражения и порядок байт CRC в кадре неизвестны — перебираются все
варианты; верный даёт многочлен ровно степени w.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np

#: Известные CRC (имя, ширина, многочлен, init, refin, refout, xorout,
#: контрольное значение по строке «123456789» — сверяется в тестах).
КАТАЛОГ = (
    ("CRC-8/SMBUS", 8, 0x07, 0x00, False, False, 0x00, 0xF4),
    ("CRC-16/ARC", 16, 0x8005, 0x0000, True, True, 0x0000, 0xBB3D),
    ("CRC-16/MODBUS", 16, 0x8005, 0xFFFF, True, True, 0x0000, 0x4B37),
    ("CRC-16/KERMIT", 16, 0x1021, 0x0000, True, True, 0x0000, 0x2189),
    ("CRC-16/IBM-SDLC (X.25, HDLC)", 16, 0x1021, 0xFFFF, True, True, 0xFFFF, 0x906E),
    ("CRC-16/CCITT-FALSE", 16, 0x1021, 0xFFFF, False, False, 0x0000, 0x29B1),
    ("CRC-32 (Ethernet, HDLC, zip)", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF,
     0xCBF43926),
    ("CRC-32C (Castagnoli)", 32, 0x1EDC6F41, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xE3069283),
)


def _отразить(x: int, бит: int) -> int:
    return int(format(x, f"0{бит}b")[::-1], 2)


_ОТРАЖЁННЫЙ_БАЙТ = [_отразить(i, 8) for i in range(256)]


def crc(данные: bytes, w: int, P: int, init: int, refin: bool, refout: bool, xorout: int) -> int:
    """Прямой расчёт CRC по модели RevEng (побитно, для проверки и опознания)."""
    старший = 1 << (w - 1)
    маска = (1 << w) - 1
    регистр = init
    for байт in данные:
        if refin:
            байт = _ОТРАЖЁННЫЙ_БАЙТ[байт]
        for i in range(7, -1, -1):
            бит = (байт >> i) & 1
            верхний = (регистр & старший) != 0
            регистр = (регистр << 1) & маска
            if верхний ^ bool(бит):
                регистр ^= P
    if refout:
        регистр = _отразить(регистр, w)
    return регистр ^ xorout


# -- многочлены над GF(2) как целые ---------------------------------------------------

def _остаток(a: int, b: int) -> int:
    степень_b = b.bit_length()
    while a.bit_length() >= степень_b:
        a ^= b << (a.bit_length() - степень_b)
    return a


def _нод(a: int, b: int) -> int:
    while b:
        a, b = b, _остаток(a, b)
    return a


def _как_многочлен(данные: bytes, refin: bool) -> int:
    """Байты кадра → многочлен: первый бит — старший коэффициент."""
    if refin:
        данные = bytes(_ОТРАЖЁННЫЙ_БАЙТ[b] for b in данные)
    return int.from_bytes(данные, "big") if данные else 0


def _разобрать_кадр(кадр: bytes, w: int, порядок: str, refout: bool) -> Tuple[bytes, int]:
    байт = w // 8
    тело, хвост = кадр[:-байт], кадр[-байт:]
    значение = int.from_bytes(хвост, порядок)
    if refout:
        значение = _отразить(значение, w)
    return тело, значение


def многочлен(кадры: Sequence[bytes], w: int, refin: bool, refout: bool, порядок: str
              ) -> int | None:
    """P по парам кадров одной длины; None — если НОД не степени w."""
    по_длине: Dict[int, List[bytes]] = {}
    for кадр in кадры:
        по_длине.setdefault(len(кадр), []).append(кадр)
    g = 0
    пар = 0
    for группа in по_длине.values():
        if len(группа) < 2:
            continue
        опорный_тело, опорная_crc = _разобрать_кадр(группа[0], w, порядок, refout)
        a0 = _как_многочлен(опорный_тело, refin)
        for другой in группа[1:12]:
            тело, значение = _разобрать_кадр(другой, w, порядок, refout)
            D = ((a0 ^ _как_многочлен(тело, refin)) << w) ^ (опорная_crc ^ значение)
            if D:
                g = _нод(g, D) if g else D
                пар += 1
            if пар >= 24:
                break
    if not g or g.bit_length() - 1 != w:
        return None
    return g


def _решения(строки: List[int], правые: List[int], w: int) -> List[int]:
    """Все решения системы над GF(2) (строка_i · init = правая_i), если свободных ≤ 4.

    Когда в P есть множитель (x + 1) — а он есть у CRC-16/ARC, X.25 и
    многих других, — разности длин init не определяют: пары (init, xorout) и
    (init ⊕ Q, xorout ⊕ Q), Q = P/(x + 1), дают одинаковую CRC на любых
    данных. Такие модели равносильны, и возвращаются все.
    """
    опорные: Dict[int, Tuple[int, int]] = {}
    for строка, правая in zip(строки, правые):
        for бит in sorted(опорные, reverse=True):
            if (строка >> бит) & 1:
                о_строка, о_правая = опорные[бит]
                строка ^= о_строка
                правая ^= о_правая
        if строка:
            опорные[строка.bit_length() - 1] = (строка, правая)
        elif правая:
            return []                                     # несовместна
    свободные = [бит for бит in range(w) if бит not in опорные]
    if len(свободные) > 4:
        return []
    итог = []
    for набор in range(1 << len(свободные)):
        решение = 0
        for i, бит in enumerate(свободные):
            if (набор >> i) & 1:
                решение |= 1 << бит
        for бит in sorted(опорные):                       # младшие биты — первыми
            строка, правая = опорные[бит]
            прочие = строка & ~(1 << бит)
            if правая ^ (bin(прочие & решение).count("1") & 1):
                решение |= 1 << бит
        итог.append(решение)
    return итог


def начало_и_хвост(кадры: Sequence[bytes], w: int, P: int, refin: bool, refout: bool,
                   порядок: str) -> Tuple[List[Tuple[int, int]], int]:
    """Равносильные пары (init, xorout) — пусто, если все кадры одной длины — и вклад."""
    маска = (1 << w) - 1
    полный = (1 << w) | P
    вклады: Dict[int, int] = {}
    for кадр in кадры:
        тело, значение = _разобрать_кадр(кадр, w, порядок, refout)
        линейная = _остаток(_как_многочлен(тело, refin) << w, полный)
        вклады.setdefault(len(тело) * 8, значение ^ линейная)
    длины = sorted(вклады)
    ℓ0 = длины[0]
    if len(длины) < 2:
        return [], вклады[ℓ0]
    # вклад(ℓ) = init·x^ℓ mod P ⊕ xorout; разность двух длин — только от init.
    строки, правые = [], []
    for ℓ in длины[1:]:
        правая = вклады[ℓ0] ^ вклады[ℓ]
        столбцы = [_остаток((1 << (j + ℓ0)) ^ (1 << (j + ℓ)), полный) for j in range(w)]
        for i in range(w):
            строки.append(sum(((столбцы[j] >> i) & 1) << j for j in range(w)))
            правые.append((правая >> i) & 1)
    пары = []
    for init in _решения(строки, правые, w):
        xorout = вклады[ℓ0] ^ _остаток(init << ℓ0, полный)
        if refout:
            # Хранимое — отражённый регистр ⊕ xorout; отразив его целиком, мы
            # получили отражённый xorout.
            xorout = _отразить(xorout, w)
        пары.append((init & маска, xorout & маска))
    return пары, вклады[ℓ0]


def _выбрать(пары: List[Tuple[int, int]], w: int, P: int, refin: bool, refout: bool
             ) -> Tuple[int | None, int | None]:
    """Из равносильных пар — известную по каталогу, иначе с init = 0 или все единицы."""
    if not пары:
        return None, None
    for имя, cw, cP, cinit, crefin, crefout, cxor, _ in КАТАЛОГ:
        if (cw, cP, crefin, crefout) == (w, P, refin, refout) and (cinit, cxor) in пары:
            return cinit, cxor
    единицы = (1 << w) - 1
    for init, xorout in пары:
        if init in (0, единицы):
            return init, xorout
    return пары[0]


def найти(кадры: Sequence[bytes], ширины=(16, 32, 8, 24)) -> Dict | None:
    """Опознание CRC в конце кадров: ширина, многочлен, init, xorout, отражения."""
    кадры = [bytes(к) for к in кадры if len(к) >= 4][:2000]
    if len(кадры) < 4:
        return None
    for w in ширины:
        for refin in (False, True):
            for refout in (False, True):
                for порядок in ("big", "little"):
                    P_полный = многочлен(кадры, w, refin, refout, порядок)
                    if P_полный is None:
                        continue
                    P = P_полный & ((1 << w) - 1)
                    пары, вклад = начало_и_хвост(кадры, w, P, refin, refout, порядок)
                    init, xorout = _выбрать(пары, w, P, refin, refout)
                    итог = {"w": w, "P": P, "refin": refin, "refout": refout,
                            "порядок": порядок, "init": init, "xorout": xorout, "вклад": вклад,
                            "равносильные": [п for п in пары if п != (init, xorout)]}
                    итог["верно"] = _доля_верных(кадры, итог)
                    итог["имя"] = _по_каталогу(итог)
                    return итог
    return None


def _доля_верных(кадры: Sequence[bytes], м: Dict) -> float | None:
    if м["init"] is None:
        return None
    верных = 0
    for кадр in кадры[:500]:
        тело, хвост = кадр[:-м["w"] // 8], кадр[-м["w"] // 8:]
        значение = crc(тело, м["w"], м["P"], м["init"], м["refin"], м["refout"], м["xorout"])
        верных += значение == int.from_bytes(хвост, м["порядок"])
    return верных / min(len(кадры), 500)


def _по_каталогу(м: Dict) -> str | None:
    for имя, w, P, init, refin, refout, xorout, _ in КАТАЛОГ:
        if (w, P, refin, refout) == (м["w"], м["P"], м["refin"], м["refout"]) and \
                (м["init"] is None or (init, xorout) == (м["init"], м["xorout"])):
            return имя
    return None
