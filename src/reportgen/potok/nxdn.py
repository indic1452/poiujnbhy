"""NXDN (4800 бит/с): кадры по FSW, скремблер, LICH, SACCH (RAN) и вызовы из сверхкадра SACCH.

Как у MMDVMHost (NXDNDefines.h, NXDNControl.cpp, NXDNLICH.cpp, NXDNSACCH.cpp, NXDNConvolution.cpp,
NXDNCRC.cpp, NXDNLayer3.cpp):
- кадр 384 бита: FSW 0xCDF59 (20 бит), LICH 16, SACCH 60, остальное — речь/данные; весь кадр (кроме
  нулевого начала таблицы) сложен со скремблером SCRAMBLER (48 байт);
- LICH: 8 бит — первые биты 8 дибитов (второй бит — 1): RFCT 2, FCT 2, опция 2, направление 1,
  чётность 1 (единица, если старшая тетрада 0x8 или 0xB — getParity);
- SACCH: 60 бит, i-й — на месте INTERLEAVE_TABLE[i] = (i mod 12)·5 + i div 12 после LICH; выколотые места
  5, 11, …, 71 и 8 нулей хвоста; свёртка K = 5 (1+D³+D⁴, 1+D+D²+D⁴); 36 бит: SR 2, RAN 6, 18 бит
  данных, CRC-6 (0x27, начальное 0x3F);
- четыре SACCH (SR 3, 2, 1, 0 — части 1/4 … 4/4) — сообщение уровня 3 (72 бита): тип (6 бит),
  признак группы (бит 7 байта 2 — нуль), источник (байты 3–4), получатель (5–6).
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

import numpy as np

from .nahodka import Находка

FSW = 0xCDF59
КАДР = 384
СКРЕМБЛЕР_БАЙТЫ = bytes([
    0x00, 0x00, 0x00, 0x82, 0xA0, 0x88, 0x8A, 0x00, 0xA2, 0xA8, 0x82, 0x8A, 0x82, 0x02,
    0x20, 0x08, 0x8A, 0x20, 0xAA, 0xA2, 0x82, 0x08, 0x22, 0x8A, 0xAA, 0x08, 0x28, 0x88,
    0x28, 0x28, 0x00, 0x0A, 0x02, 0x82, 0x20, 0x28, 0x82, 0x2A, 0xAA, 0x20, 0x22, 0x80,
    0xA8, 0x8A, 0x08, 0xA0, 0xAA, 0x02])
СКРЕМБЛЕР = np.unpackbits(np.frombuffer(СКРЕМБЛЕР_БАЙТЫ, dtype=np.uint8))
МЕСТА_SACCH = [(i % 12) * 5 + i // 12 for i in range(60)]
ВЫКОЛОТЫ = [5, 11, 17, 23, 29, 35, 41, 47, 53, 59, 65, 71]
RFCT = {0: "RCCH (управление)", 1: "RTCH (трафик)", 2: "RDCH (данные)", 3: "RTCH-C"}
FCT = {0: "SACCH без суперкадра", 1: "UDCH", 2: "SACCH суперкадр", 3: "SACCH простой"}
ТИПЫ = {0x01: "VCALL (речевой вызов)", 0x03: "VCALL_IV", 0x08: "TX_REL (конец передачи)", 0x09: "DCALL_HDR",
        0x0B: "DCALL_DATA", 0x0C: "DCALL_ACK", 0x0F: "HEAD_DLY", 0x10: "IDLE", 0x28: "AUTH_INQ_REQ",
        0x29: "AUTH_INQ_RESP", 0x30: "STAT_INQ_REQ", 0x31: "STAT_INQ_RESP", 0x32: "STAT_REQ", 0x33: "STAT_RESP",
        0x34: "REM_CON_REQ", 0x35: "REM_CON_RESP", 0x38: "SDCALL_REQ_HDR", 0x39: "SDCALL_REQ_DATA",
        0x3A: "SDCALL_IV", 0x3B: "SDCALL_RESP"}
НАЙТИ_ОТ = 3


def _бит_числа(значение: int, длина: int) -> np.ndarray:
    return np.array([(значение >> (длина - 1 - i)) & 1 for i in range(длина)], dtype=np.uint8)


def чётность_lich(lich: int) -> int:
    return 1 if lich & 0xF0 in (0x80, 0xB0) else 0


def crc6(биты: np.ndarray) -> int:
    crc = 0x3F
    for б in np.asarray(биты, dtype=np.uint8):
        старший = (crc >> 5) & 1
        crc = (crc << 1) & 0xFF
        if int(б) ^ старший:
            crc ^= 0x27
    return crc & 0x3F


def свёртка(биты: np.ndarray) -> np.ndarray:
    d1 = d2 = d3 = d4 = 0
    итог = []
    for d in np.asarray(биты, dtype=np.uint8):
        d = int(d)
        итог += [(d + d3 + d4) & 1, (d + d1 + d2 + d4) & 1]
        d4, d3, d2, d1 = d3, d2, d1, d
    return np.array(итог, dtype=np.uint8)


def витерби(символы: List[Optional[int]], бит: int) -> np.ndarray:
    """Жёсткий Витерби K = 5 со стёртыми символами (None), путь — в нулевое состояние."""
    INF = 1 << 30
    метрики = [0] + [INF] * 15
    пути: List[List[int]] = [[] for _ in range(16)]
    for k in range(0, len(символы), 2):
        a, b = символы[k], символы[k + 1]
        новые, новые_пути = [INF] * 16, [None] * 16
        for s in range(16):
            if метрики[s] >= INF:
                continue
            d1, d2, d3, d4 = (s >> 3) & 1, (s >> 2) & 1, (s >> 1) & 1, s & 1
            for d in (0, 1):
                g1, g2 = (d + d3 + d4) & 1, (d + d1 + d2 + d4) & 1
                m = метрики[s] + (a is not None and g1 != a) + (b is not None and g2 != b)
                t = (d << 3) | (d1 << 2) | (d2 << 1) | d3
                if m < новые[t]:
                    новые[t], новые_пути[t] = m, пути[s] + [d]
        метрики, пути = новые, новые_пути
    return np.array(пути[0][:бит], dtype=np.uint8)


def lich(кадр: np.ndarray) -> Optional[int]:
    """8 бит LICH (кадр уже без скремблера); None — чётность не сошлась."""
    биты = np.asarray(кадр, dtype=np.uint8)[20:36:2]
    значение = int("".join(map(str, биты)), 2)
    return значение if (значение & 1) == чётность_lich(значение) else None


def sacch(кадр: np.ndarray) -> Optional[Dict[str, int]]:
    """SR, RAN и 18 бит данных SACCH; None — CRC-6 не сошлась."""
    принято = np.asarray(кадр, dtype=np.uint8)[36:96]
    символы: List[Optional[int]] = []
    for n in МЕСТА_SACCH:
        while len(символы) in ВЫКОЛОТЫ:                  # стёртые места выколотого кода
            символы.append(None)
        символы.append(int(принято[n]))
    while len(символы) in ВЫКОЛОТЫ:                      # последнее выколотое место (71) — после данных
        символы.append(None)
    символы += [0] * 8
    данные = витерби(символы, 36)
    if crc6(данные[:26]) != int("".join(map(str, данные[26:32])), 2):
        return None
    sr = int(данные[0]) * 2 + int(данные[1])
    return {"sr": sr, "ran": int("".join(map(str, данные[2:8])), 2),
            "данные": int("".join(map(str, данные[8:26])), 2)}


def кадры(биты: np.ndarray) -> List[Dict[str, object]]:
    биты = np.asarray(биты, dtype=np.uint8)
    if len(биты) < КАДР:
        return []
    # 20-битовое число с каждого бита (первый — старший), без матрицы окон: выборка — миллионы бит
    n = len(биты) - 19
    б = биты.astype(np.int32)
    слово = np.zeros(n, dtype=np.int32)
    for k in range(20):
        слово |= б[k:k + n] << (19 - k)
    итог = []
    for образец, обратная in ((FSW, False), (FSW ^ 0xAAAAA, True)):     # обратная: у дибита меняется знак
        for м in np.flatnonzero(слово == образец):
            кадр = биты[int(м):int(м) + КАДР]
            if len(кадр) < КАДР:
                continue
            if обратная:
                кадр = кадр ^ np.resize(np.array([1, 0], dtype=np.uint8), КАДР)
            кадр = кадр ^ СКРЕМБЛЕР
            л = lich(кадр)
            if л is None:
                continue
            к: Dict[str, object] = {"место": int(м), "обратная": обратная, "rfct": л >> 6, "fct": (л >> 4) & 3,
                                    "опция": (л >> 2) & 3, "направление": (л >> 1) & 1}
            с = sacch(кадр) if (л >> 4) & 3 in (0, 2, 3) else None
            if с is not None:
                к.update(с)
            итог.append(к)
    return sorted(итог, key=lambda к: к["место"])


def вызовы(найдено: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Сообщения уровня 3 из четвёрок SACCH (SR 3, 2, 1, 0 подряд)."""
    итог = []
    с_sacch = [к for к in найдено if "sr" in к]
    for i in range(len(с_sacch) - 3):
        четыре = с_sacch[i:i + 4]
        if [к["sr"] for к in четыре] != [3, 2, 1, 0]:
            continue
        биты = "".join(format(int(к["данные"]), "018b") for к in четыре)
        байты = bytes(int(биты[8 * k:8 * k + 8], 2) for k in range(9))
        итог.append({"тип": байты[0] & 0x3F, "группа": not байты[2] & 0x80,
                     "источник": (байты[3] << 8) | байты[4], "получатель": (байты[5] << 8) | байты[6],
                     "ran": четыре[0]["ran"]})
    return итог


def описание(в: Dict[str, object]) -> str:
    тип = int(в["тип"])
    return (f"{ТИПЫ.get(тип, f'тип 0x{тип:02X}')}: {в['источник']} → "
            + ("группа " if в["группа"] else "") + str(в["получатель"]))


def подряд(найдено: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Кадры, у которых есть сосед той же полярности через 1–4 кадра: передача NXDN идёт кадрами подряд,
    а случайное совпадение FSW (20 бит) с верной чётностью LICH одиночно."""
    места = {(к["место"], к["обратная"]) for к in найдено}
    return [к for к in найдено
            if any((к["место"] + з * КАДР, к["обратная"]) in места for з in (-4, -3, -2, -1, 1, 2, 3, 4))]


def найти(биты: np.ndarray) -> Optional[Находка]:
    найдено = подряд(кадры(биты))
    if len(найдено) < НАЙТИ_ОТ:
        return None
    ran = Counter(к["ran"] for к in найдено if "ran" in к)
    каналы = Counter(RFCT[int(к["rfct"])] for к in найдено)
    сообщения = вызовы(найдено)
    подробно = [f"кадров NXDN с верным LICH: {len(найдено)}"
                + (" — полярность обратная" if all(к["обратная"] for к in найдено) else ""),
                "канал (RFCT): " + ", ".join(f"{в} × {с}" for в, с in каналы.most_common()),
                f"SACCH с верной CRC-6: {sum(ran.values())}"
                + ("; RAN: " + ", ".join(f"{r} × {с}" for r, с in ran.most_common(6)) if ran else "")]
    for строка, с in Counter(описание(в) for в in сообщения).most_common(20):
        подробно.append(строка + (f" × {с}" if с > 1 else ""))
    return Находка(
        уровень="канальный", что="NXDN: кадры 4800 бит/с",
        уверенность=1.0, мера=f"{len(найдено)} кадров подряд по FSW с верной чётностью LICH",
        подробно=подробно, свойства={"кадров": len(найдено), "вызовов": len(сообщения)})


def кадр(rfct: int = 1, fct: int = 2, опция: int = 0, направление: int = 0, sr: int = 0, ran: int = 1,
         данные18: int = 0) -> np.ndarray:
    """Кадр NXDN (для проверок): FSW, LICH, SACCH, нули в остальном; со скремблером."""
    л = (rfct << 6) | (fct << 4) | (опция << 2) | (направление << 1)
    л |= чётность_lich(л)
    ядро = np.concatenate([_бит_числа(sr, 2), _бит_числа(ran, 6), _бит_числа(данные18, 18)])
    с_crc = np.concatenate([ядро, _бит_числа(crc6(ядро), 6), np.zeros(4, dtype=np.uint8)])
    свёрнуто = свёртка(с_crc)[:72]
    переданы = [int(б) for n, б in enumerate(свёрнуто) if n not in ВЫКОЛОТЫ]
    sacch_биты = np.zeros(60, dtype=np.uint8)
    for i, n in enumerate(МЕСТА_SACCH):
        sacch_биты[n] = переданы[i]
    lich_биты = np.array([b for k in range(8) for b in ((л >> (7 - k)) & 1, 1)], dtype=np.uint8)
    кадр_ = np.concatenate([_бит_числа(FSW, 20), lich_биты, sacch_биты, np.zeros(КАДР - 96, dtype=np.uint8)])
    return кадр_ ^ СКРЕМБЛЕР


def вызов(источник: int, получатель: int, группа: bool = True, тип: int = 0x01, ran: int = 1) -> List[np.ndarray]:
    """Четыре кадра со сверхкадром SACCH (SR 3, 2, 1, 0) — сообщение уровня 3 (для проверок)."""
    байты = bytes([тип, 0, 0 if группа else 0x80, источник >> 8, источник & 0xFF, получатель >> 8,
                   получатель & 0xFF, 0, 0])
    биты = "".join(format(б, "08b") for б in байты)
    return [кадр(sr=sr, ran=ran, данные18=int(биты[18 * k:18 * k + 18], 2)) for k, sr in enumerate((3, 2, 1, 0))]
