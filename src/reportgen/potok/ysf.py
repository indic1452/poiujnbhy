"""System Fusion (YSF, Yaesu C4FM): кадры по синхрослову, FICH (Golay (24, 12), свёртка K = 5, CRC), позывные.

Как у MMDVMHost (YSFDefines.h, YSFFICH.cpp, YSFConvolution.cpp, Golay24128.cpp, YSFPayload.cpp, CRC.cpp):
- кадр 960 бит: синхрослово 0xD471C9634D (40 бит), FICH 200 бит, полезная часть 720 бит;
- FICH: 100 пар бит, пара i — на месте (i mod 5)·40 + (i div 5)·2 (INTERLEAVE_TABLE), свёрточный код
  K = 5, 1/2 (1+D³+D⁴ первым, 1+D+D²+D⁴ вторым, 4 бита хвоста), четыре слова Golay (24, 12)
  (12 бит данных, остаток по 0xC75 и бит чётности — как ENCODING_TABLE_24128), 6 байт, CRC-16
  (0x1021, начальное 0, инверсия); поля: FI, CS, CM, BN, BT, FN, FT, DT, MR, признаки Dev и VoIP;
- в заголовке и конце (FI 0 и 2) полезная часть — 5 блоков по 18 байт, первые 9 байт каждого — канал
  данных: перемежение 9 × 20, та же свёртка (180 пар → 176 бит), 22 байта с CRC-16, затем XOR
  WHITENING_DATA; первые 10 знаков — позывной получателя, следующие 10 — отправителя.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from .nahodka import Находка

СИНХРО = 0xD471C9634D
КАДР = 960
ГОЛЕЙ_G = 0xC75
#: WHITENING_DATA из YSFPayload.cpp (MMDVMHost).
ОТБЕЛИВАНИЕ = bytes([0x93, 0xD7, 0x51, 0x21, 0x9C, 0x2F, 0x6C, 0xD0, 0xEF, 0x0F,
                     0xF8, 0x3D, 0xF1, 0x73, 0x20, 0x94, 0xED, 0x1E, 0x7C, 0xD8])
FI = {0: "заголовок", 1: "связь", 2: "конец", 3: "проверка"}
DT = {0: "речь и данные, режим 1", 1: "данные (полная скорость)", 2: "речь и данные, режим 2",
      3: "речь (полная скорость)"}
CM = {0: "общий вызов (CQ)", 1: "групповой (ID)", 2: "резерв", 3: "индивидуальный"}
НАЙТИ_ОТ = 3


def _бит_числа(значение: int, длина: int) -> np.ndarray:
    return np.array([(значение >> (длина - 1 - i)) & 1 for i in range(длина)], dtype=np.uint8)


def golay24(данные12: int) -> int:
    остаток = данные12 << 11
    for сдвиг in range(22, 10, -1):
        if остаток >> сдвиг & 1:
            остаток ^= ГОЛЕЙ_G << (сдвиг - 11)
    слово = (данные12 << 11) | остаток
    return (слово << 1) | (bin(слово).count("1") & 1)


СЛОВА_ГОЛЕЯ = np.array([golay24(д) for д in range(4096)], dtype=np.uint32)


def голей_декодировать(слово24: int) -> tuple[int, int]:
    """(12 бит данных, расстояние) — ближайшее из 4096 слов (расстояние кода 8: до 3 ошибок)."""
    разности = np.bitwise_count(СЛОВА_ГОЛЕЯ ^ np.uint32(слово24))
    данные = int(разности.argmin())
    return данные, int(разности[данные])


def свёртка(биты: np.ndarray) -> np.ndarray:
    d1 = d2 = d3 = d4 = 0
    итог = []
    for d in np.asarray(биты, dtype=np.uint8):
        d = int(d)
        итог += [(d + d3 + d4) & 1, (d + d1 + d2 + d4) & 1]
        d4, d3, d2, d1 = d3, d2, d1, d
    return np.array(итог, dtype=np.uint8)


def витерби(пары: np.ndarray, бит: int) -> np.ndarray:
    """Жёсткий Витерби K = 5 (16 состояний), путь кончается в нуле (хвост из нулей)."""
    пары = np.asarray(пары, dtype=np.uint8).reshape(-1, 2)
    INF = 1 << 30
    метрики = [0] + [INF] * 15                         # состояние = d1 d2 d3 d4 (d1 старший)
    пути: list[list[int]] = [[] for _ in range(16)]
    for a, b in пары:
        новые, новые_пути = [INF] * 16, [None] * 16
        for s in range(16):
            if метрики[s] >= INF:
                continue
            d1, d2, d3, d4 = (s >> 3) & 1, (s >> 2) & 1, (s >> 1) & 1, s & 1
            for d in (0, 1):
                g1, g2 = (d + d3 + d4) & 1, (d + d1 + d2 + d4) & 1
                m = метрики[s] + (g1 != a) + (g2 != b)
                t = (d << 3) | (d1 << 2) | (d2 << 1) | d3
                if m < новые[t]:
                    новые[t], новые_пути[t] = m, пути[s] + [d]
        метрики, пути = новые, новые_пути
    return np.array(пути[0][:бит], dtype=np.uint8)


def crc16(данные: bytes) -> bytes:
    c = 0
    for б in данные:
        c ^= б << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return (c ^ 0xFFFF).to_bytes(2, "big")


МЕСТА_FICH = [(i % 5) * 40 + (i // 5) * 2 for i in range(100)]
МЕСТА_ДАННЫХ = [(i % 9) * 40 + (i // 9) * 2 for i in range(180)]


def fich(кадр: np.ndarray) -> dict[str, object] | None:
    """Поля FICH кадра (с синхрословом в начале); None — CRC не сошлась."""
    биты = np.asarray(кадр, dtype=np.uint8)[40:240]
    if len(биты) < 200:
        return None
    пары = np.array([(биты[n], биты[n + 1]) for n in МЕСТА_FICH], dtype=np.uint8)
    данные = витерби(пары, 96)
    слова = [голей_декодировать(int("".join(map(str, данные[24 * k:24 * k + 24])), 2)) for k in range(4)]
    сорок_восемь = "".join(format(д, "012b") for д, _ in слова)
    б = bytes(int(сорок_восемь[8 * k:8 * k + 8], 2) for k in range(6))
    if crc16(б[:4]) != б[4:]:
        return None
    return {"fi": б[0] >> 6, "cs": (б[0] >> 4) & 3, "cm": (б[0] >> 2) & 3, "bn": б[0] & 3, "bt": б[1] >> 6,
            "fn": (б[1] >> 3) & 7, "ft": б[1] & 7, "dt": б[2] & 3, "mr": (б[2] >> 3) & 7, "voip": bool(б[2] & 4),
            "dev": bool(б[2] & 0x40), "исправлено": sum(р for _, р in слова)}


def позывные(кадр: np.ndarray) -> tuple[str, str] | None:
    """(получатель, отправитель) из канала данных заголовка или конца; None — CRC не сошлась."""
    полезная = np.asarray(кадр, dtype=np.uint8)[240:960]
    if len(полезная) < 720:
        return None
    dch = np.concatenate([полезная[144 * k:144 * k + 72] for k in range(5)])
    пары = np.array([(dch[n], dch[n + 1]) for n in МЕСТА_ДАННЫХ], dtype=np.uint8)
    данные = np.packbits(витерби(пары, 176)).tobytes()
    if crc16(данные[:20]) != данные[20:22]:
        return None
    текст = bytes(б ^ x for б, x in zip(данные[:20], ОТБЕЛИВАНИЕ, strict=True)).decode("ascii", "replace")
    return текст[:10].rstrip(), текст[10:20].rstrip()


def кадры(биты: np.ndarray) -> list[dict[str, object]]:
    биты = np.asarray(биты, dtype=np.uint8)
    if len(биты) < КАДР:
        return []
    окна = np.lib.stride_tricks.sliding_window_view(биты, 40)
    эталон = _бит_числа(СИНХРО, 40)
    обратный = эталон ^ np.array([1, 0] * 20, dtype=np.uint8)
    итог = []
    for образец, обратная in ((эталон, False), (обратный, True)):
        for м in np.flatnonzero((окна == образец).all(axis=1)):
            кадр = биты[int(м):int(м) + КАДР]
            if len(кадр) < КАДР:
                continue
            if обратная:
                кадр = кадр ^ np.resize(np.array([1, 0], dtype=np.uint8), КАДР)
            поля = fich(кадр)
            if поля is None:
                continue
            поля.update(место=int(м), обратная=обратная)
            if поля["fi"] in (0, 2):
                поля["позывные"] = позывные(кадр)
            итог.append(поля)
    return sorted(итог, key=lambda к: к["место"])


def найти(биты: np.ndarray) -> Находка | None:
    найдено = кадры(биты)
    if len(найдено) < НАЙТИ_ОТ:
        return None
    виды = Counter(FI[int(к["fi"])] for к in найдено)
    данные = Counter(DT[int(к["dt"])] for к in найдено)
    вызовы = Counter(f"{к['позывные'][1]} → {к['позывные'][0]}" for к in найдено if к.get("позывные"))
    подробно = [f"кадров YSF с верным FICH (Golay + свёртка + CRC): {len(найдено)}"
                + (" — полярность обратная" if all(к["обратная"] for к in найдено) else ""),
                "виды кадров (FI): " + ", ".join(f"{в} × {с}" for в, с in виды.most_common()),
                "тип данных (DT): " + ", ".join(f"{в} × {с}" for в, с in данные.most_common()),
                "вызов (CM): " + ", ".join(f"{в} × {с}" for в, с in Counter(CM[int(к["cm"])] for к in найдено).most_common()),
                f"исправлено бит в словах Golay FICH: {sum(int(к['исправлено']) for к in найдено)}"]
    for вызов, с in вызовы.most_common(20):
        подробно.append(f"позывные (отправитель → получатель): {вызов}" + (f" × {с}" if с > 1 else ""))
    return Находка(
        уровень="канальный", что="System Fusion (YSF): кадры C4FM",
        уверенность=1.0, мера=f"{len(найдено)} кадров по 960 бит с верным FICH",
        подробно=подробно, свойства={"кадров": len(найдено), "вызовов": sum(вызовы.values())})


def кадр(fi: int, dt: int = 2, cm: int = 0, fn: int = 0, ft: int = 6, получатель: str = "ALL",
         отправитель: str = "R3ABC", voip: bool = False) -> np.ndarray:
    """Кадр YSF (для проверок): синхрослово, FICH, у заголовка и конца — позывные, иначе нули."""
    поля = bytes([(fi << 6) | (cm << 2), (fn << 3) | ft, dt | (4 if voip else 0), 0])
    шесть = поля + crc16(поля)
    биты48 = "".join(format(б, "08b") for б in шесть)
    слова = "".join(format(golay24(int(биты48[12 * k:12 * k + 12], 2)), "024b") for k in range(4))
    свёрнуто = свёртка(np.array([int(с) for с in слова] + [0] * 4, dtype=np.uint8))
    fich_биты = np.zeros(200, dtype=np.uint8)
    for i, n in enumerate(МЕСТА_FICH):
        fich_биты[n], fich_биты[n + 1] = свёрнуто[2 * i], свёрнуто[2 * i + 1]
    полезная = np.zeros(720, dtype=np.uint8)
    if fi in (0, 2):
        текст = (получатель.ljust(10) + отправитель.ljust(10)).encode()
        данные = bytes(б ^ x for б, x in zip(текст, ОТБЕЛИВАНИЕ, strict=True))
        данные += crc16(данные)
        свёрнуто = свёртка(np.concatenate([np.unpackbits(np.frombuffer(данные, dtype=np.uint8)),
                                           np.zeros(4, dtype=np.uint8)]))
        dch = np.zeros(360, dtype=np.uint8)
        for i, n in enumerate(МЕСТА_ДАННЫХ):
            dch[n], dch[n + 1] = свёрнуто[2 * i], свёрнуто[2 * i + 1]
        for k in range(5):
            полезная[144 * k:144 * k + 72] = dch[72 * k:72 * k + 72]
    return np.concatenate([_бит_числа(СИНХРО, 40), fich_биты, полезная])
