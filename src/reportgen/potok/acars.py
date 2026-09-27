"""ACARS (ARINC 618): блоки по SYN SYN SOH, нечётная чётность знаков, CRC, поля сообщения.

Правила — как у acarsdec (acars.c, output.c, syndrom.h):
- знаки 7 бит ASCII + бит нечётной чётности старшим, передаются младшим битом вперёд;
  синхронизация: SYN (0x16) SYN SOH (0x01), полярность бывает обратной (~SYN);
- блок: режим (1), адрес — регистрация борта (7, точки опускаются), ACK/NAK (1), метка (2),
  номер блока (1), STX (0x02) или сразу ETX (0x83); у блока с борта (номер блока — цифра) текст
  начинается номером сообщения (4) и рейсом (6); конец — ETX (0x83) или ETB (0x97);
- CRC: отражённый CCITT (0x8408, начальное 0) по всем знакам от режима до ETX/ETB включительно
  (с битами чётности), затем два байта CRC младшим вперёд — остаток нуль.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from .nahodka import Находка

SYN, SOH, STX, ETX, ETB = 0x16, 0x01, 0x02, 0x83, 0x97
ДЛИНА_ДО = 240
НАЙТИ_ОТ = 2


def _таблица() -> list[int]:
    итог = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
        итог.append(c)
    return итог


ТАБЛИЦА = _таблица()


def crc(данные: bytes, начальное: int = 0) -> int:
    """CRC-16 как update_crc acarsdec (отражённый 0x8408, начальное 0 — CRC-16/KERMIT)."""
    c = начальное
    for б in данные:
        c = (c >> 8) ^ ТАБЛИЦА[(c ^ б) & 0xFF]
    return c


def с_чётностью(знак: int) -> int:
    """7-битовый знак → байт с битом нечётной чётности старшим."""
    return знак | (0x80 if bin(знак).count("1") % 2 == 0 else 0)


def _байты(биты: np.ndarray, от: int, сколько: int) -> bytes:
    """Байты младшим битом вперёд с места ``от``."""
    кусок = биты[от:от + 8 * сколько]
    if len(кусок) < 8 * сколько:
        return b""
    return np.packbits(кусок.reshape(-1, 8)[:, ::-1], axis=1).reshape(-1).tobytes()


@dataclass
class Блок:
    место: int
    инверсия: bool
    знаки: bytes          # от режима до ETX/ETB включительно, с битами чётности
    crc_верна: bool
    ошибок_чётности: int

    def текст(self) -> str:
        return bytes(б & 0x7F for б in self.знаки).decode("ascii", "replace")

    def поля(self) -> dict:
        т = self.текст()
        адрес = т[1:8].replace(".", "")
        метка = т[9:11].replace("\x7f", "d")
        номер_блока = т[11]
        итог = {"режим": т[0], "адрес": адрес, "ack": "NAK" if т[8] == "\x15" else т[8], "метка": метка,
                "блок": номер_блока, "с_борта": номер_блока.isdigit(), "текст": ""}
        if len(т) > 12 and self.знаки[12] == STX:
            тело = т[13:-1]
            if итог["с_борта"]:
                итог["сообщение"], итог["рейс"], тело = тело[:4], тело[4:10], тело[10:]
            итог["текст"] = тело
        return итог


def блоки(биты: np.ndarray) -> list[Блок]:
    """Все блоки ACARS: SYN SYN SOH при любом битовом сдвиге и полярности, до ETX/ETB и CRC."""
    биты = np.asarray(биты, dtype=np.uint8)
    итог: list[Блок] = []
    образец = np.array([(б >> k) & 1 for б in (SYN, SYN, SOH) for k in range(8)], dtype=np.uint8)
    if len(биты) < 24 + 8 * 16:
        return итог
    окна = np.lib.stride_tricks.sliding_window_view(биты, 24)
    for инверсия in (False, True):
        эталон = 1 - образец if инверсия else образец
        ряд = 1 - биты if инверсия else биты
        for м in np.flatnonzero((окна == эталон).all(axis=1)):
            м = int(м) + 24
            знаки = bytearray()
            while len(знаки) < ДЛИНА_ДО:
                б = _байты(ряд, м + 8 * len(знаки), 1)
                if not б:
                    break
                знаки += б
                if б[0] in (ETX, ETB):
                    break
            if not знаки or знаки[-1] not in (ETX, ETB) or len(знаки) < 13:
                continue
            проверка = _байты(ряд, м + 8 * len(знаки), 2)
            if len(проверка) < 2:
                continue
            итог.append(Блок(м - 24, инверсия, bytes(знаки), crc(bytes(знаки) + проверка) == 0,
                             sum(1 for б in знаки if bin(б).count("1") % 2 == 0)))
    return sorted(итог, key=lambda б: б.место)


def найти(биты: np.ndarray) -> Находка | None:
    годные = [б for б in блоки(биты) if б.crc_верна and not б.ошибок_чётности]
    if len(годные) < НАЙТИ_ОТ:
        return None
    поля = [б.поля() for б in годные]
    борта = Counter(п["адрес"] for п in поля)
    метки = Counter(п["метка"] for п in поля)
    рейсы = Counter(п["рейс"] for п in поля if п.get("рейс"))
    подробно = [f"блоков с верной CRC и чётностью: {len(годные)}"
                + (" — полярность обратная" if all(б.инверсия for б in годные) else ""),
                f"с борта: {sum(п['с_борта'] for п in поля)}, на борт: {sum(not п['с_борта'] for п in поля)}",
                "борта (регистрация): " + ", ".join(f"{а} × {к}" for а, к in борта.most_common(12)),
                "метки: " + ", ".join(f"{м} × {к}" for м, к in метки.most_common(12))]
    if рейсы:
        подробно.append("рейсы: " + ", ".join(f"{р} × {к}" for р, к in рейсы.most_common(12)))
    for п in поля[:30]:
        подробно.append(f"{п['адрес']} [{п['метка']}] блок {п['блок']}"
                        + (f", сообщение {п['сообщение']}, рейс {п['рейс']}" if п.get("рейс") else "")
                        + (f": {п['текст'][:120]}" if п["текст"] else ""))
    return Находка(
        уровень="канальный", что="ACARS (ARINC 618): сообщения воздушных судов",
        уверенность=1.0, мера=f"{len(годные)} блоков SYN SYN SOH … ETX с верной CRC-16",
        подробно=подробно, свойства={"блоков": len(годные), "бортов": len(борта)})


def закодировать(режим: str, адрес: str, ack: str, метка: str, блок: str, текст: str = "",
                 сообщение: str = "", рейс: str = "", последний: bool = True) -> np.ndarray:
    """Блок ACARS в битах (для проверок): предключ, «+*», SYN SYN SOH, знаки, CRC, DEL."""
    тело = режим + адрес.rjust(7, ".") + ack + метка + блок
    знаки = bytes(с_чётностью(ord(с)) for с in тело)
    if текст or сообщение:
        знаки += bytes([STX]) + bytes(с_чётностью(ord(с)) for с in (сообщение + рейс + текст))
    знаки += bytes([ETX if последний else ETB])
    c = crc(знаки)
    кадр = (bytes([с_чётностью(0x2B), с_чётностью(0x2A), SYN, SYN, SOH]) + знаки
            + bytes([c & 0xFF, c >> 8, с_чётностью(0x7F)]))
    биты = [1] * 128                                              # предключ
    for б in кадр:
        биты += [(б >> k) & 1 for k in range(8)]
    return np.array(биты, dtype=np.uint8)

