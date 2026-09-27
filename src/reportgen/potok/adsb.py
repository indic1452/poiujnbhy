"""ADS-B / Mode S (1090 МГц): сообщения по CRC-24, опознавание, высота, скорость, координаты CPR.

Раскладка и правила — как у dump1090 (FlightAware: crc.c, mode_s.c, cpr.c, ais_charset.c):
- сообщение DF17 (ADS-B) и DF18 (TIS-B/ADS-R, не транспондер) — 112 бит: DF 5, CA/CF 3, адрес
  ICAO 24, ME 56, паритет 24; остаток от деления всего сообщения на x²⁴ + 0xFFF409 — нуль;
  DF11 (ответ на общий запрос) — 56 бит, паритет — CRC с адресом запросчика (у самопроизвольного
  сквиттера — нуль);
- ME: TC 1–4 — позывной (8 знаков по 6 бит, набор ais_charset), 9–18 — положение в воздухе
  (высота 12 бит: при Q = 1 — N·25 − 1000 футов, CPR: признак чётности, широта и долгота по 17 бит),
  19 — скорость (подтипы 1–2: EW/NS, путевой угол, 3–4: курс и воздушная скорость; вертикальная
  скорость ±64 фут/мин), 20–22 — положение с высотой GNSS;
- глобальное декодирование CPR по чётному и нечётному сообщению одного борта (NZ = 15, NL — по
  формуле, та же таблица, что в cpr.c).
Высота в формате Гиллхэма (Q = 0) и наземные сообщения (TC 5–8) — показываются сырыми полями.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .nahodka import Находка

ПОРОЖДАЮЩИЙ = 0xFFF409          # x²⁴ + … (dump1090 MODES_GENERATOR_POLY)
ЗНАКИ = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"
NZ = 15
НАЙТИ_ОТ = 3


def crc(биты: np.ndarray) -> int:
    """Остаток сообщения Mode S (все биты, паритет включительно) по x²⁴ + 0xFFF409."""
    остаток = 0
    for б in np.asarray(биты, dtype=np.uint8):
        старший = (остаток >> 23) & 1
        остаток = ((остаток << 1) & 0xFFFFFF) | int(б)
        if старший:
            остаток ^= ПОРОЖДАЮЩИЙ
    return остаток


def _столбцы(длина: int) -> np.ndarray:
    """Вклад каждого бита окна в остаток — (длина, 24) бит; остаток линеен по битам."""
    итог = np.zeros((длина, 24), dtype=np.uint8)
    for i in range(длина):
        e = np.zeros(длина, dtype=np.uint8)
        e[i] = 1
        r = crc(e)
        итог[i] = [(r >> (23 - k)) & 1 for k in range(24)]
    return итог


СТОЛБЦЫ = {112: _столбцы(112), 56: _столбцы(56)}


def _число(биты: np.ndarray, от: int, до: int) -> int:
    """Биты с номерами от…до включительно, нумерация с 1 (как getbits в dump1090)."""
    return int("".join(str(int(б)) for б in биты[от - 1:до]), 2) if до >= от else 0


def nl(широта: float) -> int:
    """Число зон долготы NL (1–59) — формула ICAO; совпадает с таблицей cprNLFunction."""
    ш = abs(широта)
    if ш < 1e-9:
        return 59
    if abs(ш - 87) < 1e-9:
        return 2
    if ш > 87:
        return 1
    a = 1 - math.cos(math.pi / (2 * NZ))
    b = math.cos(math.pi / 180 * ш) ** 2
    return int(math.floor(2 * math.pi / math.acos(1 - a / b)))


def cpr_глобально(чётное: Tuple[int, int], нечётное: Tuple[int, int], последнее_нечётное: bool
                  ) -> Optional[Tuple[float, float]]:
    """Широта и долгота по паре CPR в воздухе (decodeCPRairborne); None — разные зоны широты."""
    lat0, lon0 = чётное
    lat1, lon1 = нечётное
    j = math.floor((59 * lat0 - 60 * lat1) / 131072 + 0.5)
    rlat0 = 360 / 60 * (j % 60 + lat0 / 131072)
    rlat1 = 360 / 59 * (j % 59 + lat1 / 131072)
    rlat0 -= 360 if rlat0 >= 270 else 0
    rlat1 -= 360 if rlat1 >= 270 else 0
    if not (-90 <= rlat0 <= 90 and -90 <= rlat1 <= 90) or nl(rlat0) != nl(rlat1):
        return None
    if последнее_нечётное:
        широта, n = rlat1, max(nl(rlat1) - 1, 1)
        m = math.floor((lon0 * (nl(rlat1) - 1) - lon1 * nl(rlat1)) / 131072 + 0.5)
        долгота = 360 / n * (m % n + lon1 / 131072)
    else:
        широта, n = rlat0, max(nl(rlat0), 1)
        m = math.floor((lon0 * (nl(rlat0) - 1) - lon1 * nl(rlat0)) / 131072 + 0.5)
        долгота = 360 / n * (m % n + lon0 / 131072)
    долгота -= 360 if долгота >= 180 else 0
    return широта, долгота


@dataclass
class Борт:
    icao: int
    позывные: Counter = field(default_factory=Counter)
    высоты: List[int] = field(default_factory=list)
    скорости: List[str] = field(default_factory=list)
    положения: List[Tuple[float, float]] = field(default_factory=list)
    сообщений: int = 0
    _cpr: Dict[int, Tuple[int, int]] = field(default_factory=dict)


def разобрать_сообщение(биты: np.ndarray) -> Dict[str, object]:
    """Поля сообщения DF17/18 (112 бит) — без проверки CRC."""
    df, ca, icao = _число(биты, 1, 5), _число(биты, 6, 8), _число(биты, 9, 32)
    me = биты[32:88]
    tc = _число(me, 1, 5)
    итог: Dict[str, object] = {"df": df, "ca": ca, "icao": icao, "tc": tc}
    if 1 <= tc <= 4:
        итог["позывной"] = "".join(ЗНАКИ[_число(me, 9 + 6 * k, 14 + 6 * k)] for k in range(8)).rstrip()
        итог["категория"] = f"{chr(ord('A') + 4 - tc)}{_число(me, 6, 8)}"
    elif 9 <= tc <= 18 or 20 <= tc <= 22:
        ac12 = _число(me, 9, 20)
        if 9 <= tc <= 18:
            if ac12 & 0x10:
                n = ((ac12 & 0x0FE0) >> 1) | (ac12 & 0x000F)
                итог["высота"] = n * 25 - 1000
            else:
                итог["высота_гиллхэм"] = ac12
        итог["cpr"] = (_число(me, 22, 22), _число(me, 23, 39), _число(me, 40, 56))
    elif tc == 19:
        под = _число(me, 6, 8)
        итог["подтип"] = под
        if под in (1, 2):
            ew, ns = _число(me, 15, 24), _число(me, 26, 35)
            if ew and ns:
                множитель = 4 if под == 2 else 1
                v_ew = (ew - 1) * (-1 if me[13] else 1) * множитель
                v_ns = (ns - 1) * (-1 if me[24] else 1) * множитель
                итог["путевая_скорость"] = math.sqrt(v_ns * v_ns + v_ew * v_ew)
                угол = math.degrees(math.atan2(v_ew, v_ns))
                итог["путевой_угол"] = угол + 360 if угол < 0 else угол
        elif под in (3, 4):
            if me[13]:
                итог["курс"] = _число(me, 15, 24) * 360 / 1024
            воздушная = _число(me, 26, 35)
            if воздушная:
                итог["TAS" if me[24] else "IAS"] = (воздушная - 1) * (4 if под == 4 else 1)
        vr = _число(me, 38, 46)
        if vr:
            итог["вертикальная"] = (vr - 1) * (-64 if me[36] else 64)
    return итог


def сообщения(биты: np.ndarray) -> List[Tuple[int, np.ndarray]]:
    """(место, 112 бит) всех сообщений DF17/DF18 с нулевым остатком CRC-24; не перекрываются."""
    биты = np.asarray(биты, dtype=np.uint8)
    if len(биты) < 112:
        return []
    окна = np.lib.stride_tricks.sliding_window_view(биты, 112)
    df = окна[:, 0] * 16 + окна[:, 1] * 8 + окна[:, 2] * 4 + окна[:, 3] * 2 + окна[:, 4]
    кандидаты = np.flatnonzero((df == 17) | (df == 18))
    итог, конец = [], -1
    for от in range(0, len(кандидаты), 65536):
        часть = кандидаты[от:от + 65536]
        остатки = (окна[часть].astype(np.int32) @ СТОЛБЦЫ[112].astype(np.int32)) & 1
        for м in часть[~остатки.any(axis=1)]:
            if м >= конец:
                итог.append((int(м), окна[м].copy()))
                конец = м + 112
    return итог


def найти(биты: np.ndarray) -> Optional[Находка]:
    найдено = сообщения(биты)
    if len(найдено) < НАЙТИ_ОТ:
        return None
    борта: Dict[int, Борт] = {}
    виды: Counter = Counter()
    for _, с in найдено:
        п = разобрать_сообщение(с)
        б = борта.setdefault(int(п["icao"]), Борт(int(п["icao"])))
        б.сообщений += 1
        tc = int(п["tc"])
        виды["опознавание" if 1 <= tc <= 4 else "положение" if 9 <= tc <= 22 and tc != 19
             else "скорость" if tc == 19 else f"TC {tc}"] += 1
        if "позывной" in п:
            б.позывные[п["позывной"]] += 1
        if "высота" in п:
            б.высоты.append(int(п["высота"]))
        if "путевая_скорость" in п:
            б.скорости.append(f"{п['путевая_скорость']:.0f} уз, путевой угол {п['путевой_угол']:.1f}°"
                              + (f", вертикальная {п['вертикальная']:+d} фут/мин" if "вертикальная" in п else ""))
        if "cpr" in п and 9 <= tc <= 18:
            нечётное, lat, lon = п["cpr"]
            б._cpr[нечётное] = (lat, lon)
            if len(б._cpr) == 2:
                место = cpr_глобально(б._cpr[0], б._cpr[1], bool(нечётное))
                if место is not None:
                    б.положения.append(место)
    df = Counter(_число(с, 1, 5) for _, с in найдено)
    подробно = [f"сообщений с верной CRC-24: {len(найдено)} — " + ", ".join(f"DF{d} × {к}" for d, к in df.most_common())
                + "; " + ", ".join(f"{в} × {к}" for в, к in виды.most_common()),
                f"бортов (адресов ICAO): {len(борта)}"]
    for б in sorted(борта.values(), key=lambda б: -б.сообщений)[:30]:
        части = [f"ICAO {б.icao:06X}"]
        if б.позывные:
            части.append(f"позывной {б.позывные.most_common(1)[0][0]}")
        if б.высоты:
            части.append(f"высота {б.высоты[-1]} футов")
        if б.скорости:
            части.append(б.скорости[-1])
        if б.положения:
            широта, долгота = б.положения[-1]
            части.append(f"координаты {широта:.5f}, {долгота:.5f}")
        части.append(f"сообщений {б.сообщений}")
        подробно.append("; ".join(части))
    return Находка(
        уровень="канальный", что="ADS-B (Mode S, 1090 МГц): расширенные сквиттеры",
        уверенность=1.0, мера=f"{len(найдено)} сообщений по 112 бит с нулевым остатком CRC-24 (DF17/18)",
        подробно=подробно, свойства={"бортов": len(борта), "сообщений": len(найдено)})


def cpr_закодировать(широта: float, долгота: float, нечётное: bool) -> Tuple[int, int]:
    """Координаты → (широта, долгота) CPR по 17 бит в воздухе (ICAO Doc 9871, C.2.6; для проверок)."""
    i = 1 if нечётное else 0
    dlat = 360 / (60 - i)
    yz = math.floor(2 ** 17 * (широта % dlat) / dlat + 0.5)
    rlat = dlat * (yz / 2 ** 17 + math.floor(широта / dlat))
    dlon = 360 / max(nl(rlat) - i, 1)
    xz = math.floor(2 ** 17 * (долгота % dlon) / dlon + 0.5)
    return yz % 2 ** 17, xz % 2 ** 17


def закодировать(данные88: np.ndarray) -> np.ndarray:
    """88 бит (DF … ME) → 112 бит с паритетом (для проверок и синтеза)."""
    биты = np.concatenate([np.asarray(данные88, dtype=np.uint8), np.zeros(24, dtype=np.uint8)])
    r = crc(биты)
    биты[88:] = [(r >> (23 - k)) & 1 for k in range(24)]
    return биты


def из_hex(строка: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(bytes.fromhex(строка), dtype=np.uint8))
