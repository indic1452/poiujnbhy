"""P25 фаза 1 (TIA-102): кадры по синхрослову, NID (NAC, DUID) с BCH (63, 16), TSBK управляющего канала.

Как у MMDVMHost (P25Defines.h, P25NID.cpp, BCH.cpp, P25Utils.cpp, P25Trellis.cpp, P25Data.cpp, CRC.cpp)
и op25 (tk_p25.py, поля TSBK):
- кадр: синхрослово 0x5575F5FF77FF (48 бит), NID 64 бита — NAC 12, DUID 4, проверочные BCH (63, 16)
  (порождающий многочлен g[] BCH.cpp, степень 47) и последний бит; в кадре через каждые 72 бита, начиная
  с 70-го, — два бита символа состояния, они выбрасываются;
- DUID: 0 — заголовок HDU, 3 — конец TDU, 5 — LDU1, 7 — TSDU, 10 — LDU2, 12 — PDU, 15 — конец с LC;
- TSDU: TSBK по 196 бит (98 дибитов) с бита 114 — перемежение, решётчатый код 1/2 (ENCODE_TABLE_12),
  12 байт: LB и P, код операции (6 бит), MFID, 8 байт данных, CRC-16 (0x1021, начальное 0, инверсия,
  старший байт первым); до трёх TSBK подряд, у последнего — бит LB;
- NID декодируется ближайшим из 65536 кодовых слов (расстояние 23 — исправляет до 11 ошибок).
Обратная полярность C4FM (+3 ↔ −3) меняет первый бит каждого дибита — снимается сама.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from .nahodka import Находка

СИНХРО = 0x5575F5FF77FF
DUID = {0: "HDU (заголовок)", 3: "TDU (конец)", 5: "LDU1 (речь)", 7: "TSDU (управление)", 10: "LDU2 (речь)",
        12: "PDU (данные)", 15: "TDULC (конец с LC)"}
#: Порождающий многочлен BCH (63, 16): коэффициенты g[0] (при x⁰) … g[47] (BCH.cpp MMDVMHost).
G_BCH = (1, 1, 0, 0, 1, 1, 0, 1, 1, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0, 1, 0, 1, 1,
         1, 1, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0, 1, 0, 1, 0, 1, 1)
ПЕРЕМЕЖЕНИЕ_ТРЕЛЛИС = (0, 1, 8, 9, 16, 17, 24, 25, 32, 33, 40, 41, 48, 49, 56, 57, 64, 65, 72, 73, 80, 81, 88, 89,
                       96, 97, 2, 3, 10, 11, 18, 19, 26, 27, 34, 35, 42, 43, 50, 51, 58, 59, 66, 67, 74, 75, 82, 83,
                       90, 91, 4, 5, 12, 13, 20, 21, 28, 29, 36, 37, 44, 45, 52, 53, 60, 61, 68, 69, 76, 77, 84, 85,
                       92, 93, 6, 7, 14, 15, 22, 23, 30, 31, 38, 39, 46, 47, 54, 55, 62, 63, 70, 71, 78, 79, 86, 87,
                       94, 95)
КОД_12 = (0, 15, 12, 3, 4, 11, 8, 7, 13, 2, 1, 14, 9, 6, 5, 10)
#: Точка созвездия → пара символов (pointsToDibits), символ → два бита (+3 → 01, +1 → 00, −1 → 10, −3 → 11).
ТОЧКИ = ((+1, -1), (-1, -1), (+3, -3), (-3, -3), (-3, -1), (+3, -1), (-1, -3), (+1, -3),
         (-3, +3), (+3, +3), (-1, +1), (+1, +1), (+1, +3), (-1, +3), (+3, +1), (-3, +1))
СИМВОЛ_БИТЫ = {+3: (0, 1), +1: (0, 0), -1: (1, 0), -3: (1, 1)}
ОПЕРАЦИИ = {0x00: "GRP_V_CH_GRANT (групповой вызов: канал)", 0x02: "GRP_V_CH_GRANT_UPDT (обновление каналов)",
            0x03: "GRP_V_CH_GRANT_UPDT_EXP", 0x16: "SNDCP_DATA_CH", 0x1F: "CALL_ALRT (вызов-оповещение)",
            0x20: "ACK_RSP_FNE (подтверждение)", 0x28: "GRP_AFF_RSP (ответ на привязку к группе)",
            0x29: "SCCB_EXP", 0x2B: "LOC_REG_RSP", 0x2C: "U_REG_RSP (ответ на регистрацию)",
            0x2F: "U_DE_REG_ACK", 0x33: "IDEN_UP_TDMA", 0x34: "IDEN_UP_VU", 0x35: "TIME_DATE_ANN",
            0x38: "SYS_SRV_BCST", 0x39: "SCCB (вторичный канал управления)",
            0x3A: "RFSS_STS_BCST (состояние RFSS)", 0x3B: "NET_STS_BCST (состояние сети)",
            0x3C: "ADJ_STS_BCST (соседний узел)", 0x3D: "IDEN_UP (таблица частот)"}
НАЙТИ_ОТ = 3
NID_ОШИБОК_ДО = 11


def _бит_числа(значение: int, длина: int) -> np.ndarray:
    return np.array([(значение >> (длина - 1 - i)) & 1 for i in range(длина)], dtype=np.uint8)


def bch_проверочные(информационные: np.ndarray) -> np.ndarray:
    """47 проверочных бит для 16 информационных (encode BCH.cpp: регистр bb[])."""
    bb = [0] * 47
    for i in range(15, -1, -1):
        обратная = int(информационные[i]) ^ bb[46]
        for j in range(46, 0, -1):
            bb[j] = bb[j - 1] ^ обратная if G_BCH[j] else bb[j - 1]
        bb[0] = обратная if G_BCH[0] else 0
    return np.array(bb, dtype=np.uint8)


def _таблица_nid() -> np.ndarray:
    """Все 65536 кодовых слов (63 бита, упакованы в uint64) — по линейности из базиса."""
    базис = []
    for k in range(16):
        e = np.zeros(16, dtype=np.uint8)
        e[k] = 1
        базис.append(np.concatenate([e, bch_проверочные(e)]))
    базис = np.array(базис, dtype=np.uint8)
    веса = np.array([1 << (62 - i) for i in range(63)], dtype=np.uint64)
    строки = (базис.astype(np.uint64) * веса).sum(axis=1).astype(np.uint64)
    слова = np.zeros(1 << 16, dtype=np.uint64)
    for k in range(16):
        слова ^= np.where((np.arange(1 << 16) >> (15 - k)) & 1, строки[k], np.uint64(0)).astype(np.uint64)
    return слова


СЛОВА_NID = _таблица_nid()


def декодировать_nid(nid64: np.ndarray) -> tuple[int, int, int]:
    """(NAC, DUID, ошибок) — ближайшее кодовое слово по 63 битам."""
    принято = np.uint64(int("".join(map(str, np.asarray(nid64, dtype=np.uint8)[:63])), 2))
    разности = np.bitwise_count(СЛОВА_NID ^ принято)
    инфо = int(разности.argmin())
    return инфо >> 4, инфо & 15, int(разности[инфо])


def _без_состояния(кадр: np.ndarray, от: int, до: int) -> np.ndarray:
    места = [i for i in range(от, min(до, len(кадр))) if not (i >= 70 and (i - 70) % 72 in (0, 1))]
    return кадр[места]


def _со_состоянием(биты: np.ndarray, от: int) -> np.ndarray:
    """Вставить символы состояния (01) в поток кадра, начиная с позиции ``от``."""
    итог, место = [], от
    for б in биты:
        while место >= 70 and (место - 70) % 72 in (0, 1):
            итог.append(0 if (место - 70) % 72 == 0 else 1)
            место += 1
        итог.append(int(б))
        место += 1
    return np.array(итог, dtype=np.uint8)


def закодировать_nid(nac: int, duid: int) -> np.ndarray:
    инфо = _бит_числа((nac << 4) | duid, 16)
    return np.concatenate([инфо, bch_проверочные(инфо), [1 if duid in (5, 10) else 0]]).astype(np.uint8)


def crc_tsbk(данные10: bytes) -> bytes:
    c = 0
    for б in данные10:
        c ^= б << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return (c ^ 0xFFFF).to_bytes(2, "big")


def закодировать_треллис(данные12: bytes) -> np.ndarray:
    биты = np.unpackbits(np.frombuffer(данные12, dtype=np.uint8))
    дибиты = [int(биты[2 * i]) * 2 + int(биты[2 * i + 1]) for i in range(48)] + [0]
    точки, состояние = [], 0
    for д in дибиты:
        точки.append(КОД_12[состояние * 4 + д])
        состояние = д
    символы = [с for т in точки for с in ТОЧКИ[т]]
    итог = np.zeros(196, dtype=np.uint8)
    for i in range(98):
        итог[2 * i], итог[2 * i + 1] = СИМВОЛ_БИТЫ[символы[ПЕРЕМЕЖЕНИЕ_ТРЕЛЛИС[i]]]
    return итог


def декодировать_треллис(эфир196: np.ndarray) -> tuple[bytes, int]:
    """Витерби по точкам: (12 байт, расстояние)."""
    э = np.asarray(эфир196, dtype=np.uint8)
    символьные = [None] * 98
    for i in range(98):
        символьные[ПЕРЕМЕЖЕНИЕ_ТРЕЛЛИС[i]] = (int(э[2 * i]), int(э[2 * i + 1]))
    биты_точек = [СИМВОЛ_БИТЫ[a] + СИМВОЛ_БИТЫ[b] for a, b in ТОЧКИ]
    INF = 1 << 30
    метрики = [0, INF, INF, INF]
    пути: list[list[int]] = [[], [], [], []]
    for шаг in range(49):
        принято = символьные[2 * шаг] + символьные[2 * шаг + 1]
        новые, новые_пути = [INF] * 4, [None] * 4
        for s in range(4):
            if метрики[s] >= INF:
                continue
            for д in range(4):
                т = КОД_12[s * 4 + д]
                m = метрики[s] + sum(x != y for x, y in zip(биты_точек[т], принято, strict=True))
                if m < новые[д]:
                    новые[д], новые_пути[д] = m, пути[s] + [д]
        метрики, пути = новые, новые_пути
    дибиты, расстояние = пути[0], метрики[0]                      # последний дибит — нулевой хвост
    биты = [b for д in дибиты[:48] for b in (д >> 1, д & 1)]
    return np.packbits(np.array(биты, dtype=np.uint8)).tobytes(), int(расстояние)


def разобрать_tsbk(tsbk: bytes) -> dict[str, object]:
    t = int.from_bytes(tsbk, "big")
    код, mfid = tsbk[0] & 0x3F, tsbk[1]
    п: dict[str, object] = {"последний": bool(tsbk[0] & 0x80), "код": код, "mfid": mfid,
                            "операция": ОПЕРАЦИИ.get(код, f"код 0x{код:02X}")}
    if mfid == 0:
        if код == 0x00:
            п.update(опции=(t >> 72) & 0xFF, канал=(t >> 56) & 0xFFFF, группа=(t >> 40) & 0xFFFF,
                     источник=(t >> 16) & 0xFFFFFF)
        elif код == 0x1F:
            п.update(кому=(t >> 40) & 0xFFFFFF, источник=(t >> 16) & 0xFFFFFF)
        elif код == 0x3A:
            п.update(sysid=(t >> 56) & 0xFFF, rfss=(t >> 48) & 0xFF, узел=(t >> 40) & 0xFF, канал=(t >> 24) & 0xFFFF)
        elif код == 0x3B:
            п.update(wacn=(t >> 52) & 0xFFFFF, sysid=(t >> 40) & 0xFFF, канал=(t >> 24) & 0xFFFF)
        elif код == 0x3C:
            п.update(rfss=(t >> 48) & 0xFF, узел=(t >> 40) & 0xFF, канал=(t >> 24) & 0xFFFF)
    return п


def описание_tsbk(п: dict[str, object]) -> str:
    части = [str(п["операция"])]
    for ключ, подпись in (("группа", "группа"), ("источник", "от"), ("кому", "кому"), ("wacn", "WACN"),
                          ("sysid", "SYSID"), ("rfss", "RFSS"), ("узел", "узел"), ("канал", "канал")):
        if ключ in п:
            значение = п[ключ]
            части.append(f"{подпись} " + (f"0x{значение:X}" if ключ in ("wacn", "sysid", "канал") else str(значение)))
    if п["mfid"]:
        части.append(f"MFID 0x{п['mfid']:02X}")
    return ", ".join(части)


def кадры(биты: np.ndarray) -> list[dict[str, object]]:
    """Кадры: синхрослово (прямо или с обратной полярностью), NID, у TSDU — TSBK."""
    from . import sinhro  # noqa: PLC0415 — sinhro знает слова протоколов
    биты = np.asarray(биты, dtype=np.uint8)
    if len(биты) < 114:
        return []
    эталон = _бит_числа(СИНХРО, 48)
    обратный = эталон ^ np.array([1, 0] * 24, dtype=np.uint8)
    итог = []
    for образец, обратная in ((эталон, False), (обратный, True)):
        # Места, где все 48 бит совпали, — счётом несовпадений (а не окнами 48 × N в памяти).
        for м in np.flatnonzero(sinhro.несовпадения(биты, образец) == 0):
            м = int(м)
            кадр = биты[м:м + 900]
            if обратная:
                кадр = кадр ^ np.resize(np.array([1, 0], dtype=np.uint8), len(кадр))
            nid = _без_состояния(кадр, 48, 114)
            if len(nid) < 64:
                continue
            nac, duid, ошибок = декодировать_nid(nid)
            if ошибок > NID_ОШИБОК_ДО:
                continue
            к: dict[str, object] = {"место": м, "обратная": обратная, "nac": nac, "duid": duid, "ошибок": ошибок,
                                    "tsbk": []}
            if duid == 7:
                поток = _без_состояния(кадр, 114, len(кадр))
                for n in range(3):
                    if len(поток) < 196 * (n + 1):
                        break
                    данные, расстояние = декодировать_треллис(поток[196 * n:196 * (n + 1)])
                    if crc_tsbk(данные[:10]) != данные[10:]:
                        break
                    п = разобрать_tsbk(данные)
                    к["tsbk"].append(п)
                    if п["последний"]:
                        break
            итог.append(к)
    return sorted(итог, key=lambda к: к["место"])


def найти(биты: np.ndarray) -> Находка | None:
    найдено = кадры(биты)
    if len(найдено) < НАЙТИ_ОТ:
        return None
    nac = Counter(к["nac"] for к in найдено)
    виды = Counter(DUID.get(int(к["duid"]), f"DUID {к['duid']}") for к in найдено)
    tsbk = [п for к in найдено for п in к["tsbk"]]
    подробно = [f"кадров P25 с верным NID: {len(найдено)}"
                + (" — полярность обратная" if all(к["обратная"] for к in найдено) else ""),
                "NAC: " + ", ".join(f"0x{n:03X} × {к}" for n, к in nac.most_common(6)),
                "виды кадров (DUID): " + ", ".join(f"{в} × {к}" for в, к in виды.most_common()),
                f"исправлено бит в NID (BCH (63, 16)): {sum(int(к['ошибок']) for к in найдено)}"]
    if tsbk:
        подробно.append(f"TSBK с верной CRC: {len(tsbk)}")
        for строка, к in Counter(описание_tsbk(п) for п in tsbk).most_common(30):
            подробно.append(строка + (f" × {к}" if к > 1 else ""))
    return Находка(
        уровень="канальный", что="P25 фаза 1 (TIA-102): кадры" + (", управляющий канал" if tsbk else ""),
        уверенность=1.0, мера=f"{len(найдено)} кадров по синхрослову с NID BCH (63, 16)",
        подробно=подробно, свойства={"nac": nac.most_common(1)[0][0], "кадров": len(найдено), "tsbk": len(tsbk)})


def кадр_tsdu(nac: int, tsbk: list[bytes]) -> np.ndarray:
    """Кадр TSDU (для проверок): синхрослово, NID, TSBK (10 байт + CRC), символы состояния."""
    блоки = []
    for n, данные in enumerate(tsbk):
        данные = bytes([данные[0] | (0x80 if n == len(tsbk) - 1 else 0)]) + данные[1:10]
        блоки.append(закодировать_треллис(данные + crc_tsbk(данные)))
    тело = np.concatenate([закодировать_nid(nac, 7)] + блоки)
    return np.concatenate([_бит_числа(СИНХРО, 48), _со_состоянием(тело, 48)])


def кадр_голоса(nac: int, duid: int = 5, длина: int = 1568) -> np.ndarray:
    """Кадр с NID и нулевым содержимым (для проверок): LDU — 1568 бит (216 байт минус заголовок)."""
    тело = np.concatenate([закодировать_nid(nac, duid), np.zeros(длина - 48 - 64, dtype=np.uint8)])
    return np.concatenate([_бит_числа(СИНХРО, 48), _со_состоянием(тело, 48)])[:длина]
