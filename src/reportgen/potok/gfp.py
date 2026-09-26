# -*- coding: utf-8 -*-
"""GFP (G.7041) и ATM: кадры по контрольной сумме заголовка.

Обе процедуры выделяют кадры не флагами, а проверкой заголовка — так же
поступает и анализатор:

- **GFP.** Основной заголовок — 2 байта длины полезной нагрузки (PLI) и 2
  байта CRC-16 над ними (cHEC, x¹⁶ + x¹² + x⁵ + 1, начальное значение 0),
  сложенные с маской B6AB31E0. Верный заголовок — там, где cHEC сходится, а
  следующий такой же стоит ровно через 4 + PLI байт. Пустой кадр (PLI = 0) на
  линии выглядит как B6 AB 31 E0 — это и сверка. Область нагрузки
  скремблирована самосинхронизирующимся x⁴³ + 1; в ней — заголовок типа
  (PTI, PFI, EXI, UPI) со своей CRC-16 (tHEC) и кадр клиента (Ethernet, PPP…).
- **ATM.** Ячейка 53 байта, заголовок 5 байт: GFC/VPI, VCI, PT, CLP и HEC —
  CRC-8 (x⁸ + x² + x + 1) над четырьмя байтами, сложенная с 0x55. Ячейки
  выделяются по сходящемуся HEC через каждые 53 байта.

Байтовая граница в битовом ряду неизвестна — пробуются все 8 сдвигов.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import crc as crc_
from . import pakety, skrembler
from .bity import в_байты, в_биты
from .nahodka import Находка

МАСКА = bytes.fromhex("B6AB31E0")
#: Типы нагрузки GFP (UPI), в которых уверены; прочие показываются числом.
UPI = {0x01: "Ethernet (кадровое отображение)", 0x02: "PPP (кадровое отображение)"}
ПОДРЯД = 8


def hec16(данные: bytes) -> int:
    return crc_.crc(данные, 16, 0x1021, 0, False, False, 0)


def hec8(данные: bytes) -> int:
    return crc_.crc(данные, 8, 0x07, 0, False, False, 0) ^ 0x55


def _заголовок_gfp(данные: bytes, место: int) -> Optional[int]:
    """PLI, если в этом месте верный основной заголовок GFP."""
    if место + 4 > len(данные):
        return None
    сырые = bytes(a ^ b for a, b in zip(данные[место:место + 4], МАСКА))
    pli = int.from_bytes(сырые[:2], "big")
    if hec16(сырые[:2]) != int.from_bytes(сырые[2:], "big"):
        return None
    return pli


def _цепочка_gfp(данные: bytes, начало: int, предел: int = 20000) -> List[Tuple[int, int]]:
    кадры = []
    место = начало
    while len(кадры) < предел:
        pli = _заголовок_gfp(данные, место)
        if pli is None:
            break
        кадры.append((место, pli))
        место += 4 + pli
    return кадры


def gfp(биты: np.ndarray) -> Находка | None:
    """GFP в битовом ряду: сдвиг, кадры, пустые и с нагрузкой, типы, клиенты."""
    выборка = биты[:1 << 23]
    for сдвиг in range(8):
        данные = в_байты(выборка[сдвиг:])
        for начало in range(min(len(данные) - 4, 1 << 16)):
            if _заголовок_gfp(данные, начало) is None:
                continue
            цепь = _цепочка_gfp(данные, начало)
            if len(цепь) >= ПОДРЯД:
                return _описать_gfp(данные, цепь, сдвиг)
    return None


def _описать_gfp(данные: bytes, цепь: List[Tuple[int, int]], сдвиг: int) -> Находка:
    пустых = sum(1 for _, pli in цепь if pli == 0)
    с_нагрузкой = [(м, pli) for м, pli in цепь if pli >= 4]
    # Область нагрузки скремблирована x⁴³ + 1 сплошным потоком по всем
    # кадрам: собираем её подряд, снимаем, режем обратно.
    области = [данные[м + 4:м + 4 + pli] for м, pli in с_нагрузкой]
    сплошь = skrembler.снять(в_биты(b"".join(области)), (43,))
    сплошь = np.concatenate([np.zeros(43, dtype=np.uint8), сплошь])
    снятые, место = [], 0
    for область in области:
        снятые.append(в_байты(сплошь[место:место + 8 * len(область)]))
        место += 8 * len(область)
    типы: Counter = Counter()
    клиенты: List[bytes] = []
    верных_thec = 0
    for область in снятые[1:]:                   # первая — без начала регистра x⁴³
        if len(область) < 4:
            continue
        тип = область[:2]
        if hec16(тип) == int.from_bytes(область[2:4], "big"):
            верных_thec += 1
            pti, pfi, exi, upi = тип[0] >> 5, (тип[0] >> 4) & 1, тип[0] & 0x0F, тип[1]
            типы[(pti, pfi, exi, upi)] += 1
            конец = len(область) - (4 if pfi else 0)
            клиенты.append(bytes(область[4 + (0 if exi == 0 else 4):конец]))
    проверено = max(1, len(снятые) - 1)
    подробно = [
        f"байтовая граница — с бита {сдвиг}; кадров подряд с верным cHEC: {len(цепь)} "
        f"(пустых — {пустых}, с нагрузкой — {len(с_нагрузкой)})",
        "маска основного заголовка B6AB31E0 снята; нагрузка — самосинхронизирующийся "
        "скремблер x⁴³ + 1 снят",
        f"заголовок типа с верным tHEC — в {верных_thec} из {проверено} кадров",
    ]
    for (pti, pfi, exi, upi), счёт in типы.most_common(4):
        подробно.append(f"PTI {pti:03b} ({'данные клиента' if pti == 0 else 'управление'}), "
                        f"pFCS {'есть' if pfi else 'нет'}, EXI {exi}, UPI 0x{upi:02X}"
                        f"{' — ' + UPI[upi] if upi in UPI else ''}: {счёт} кадров")
    return Находка(
        уровень="канальный",
        что="GFP (G.7041)" + (f": {UPI[типы.most_common(1)[0][0][3]]}"
                              if типы and типы.most_common(1)[0][0][3] in UPI else ""),
        уверенность=min(1.0, len(цепь) / 32),
        мера=f"{len(цепь)} основных заголовков подряд с верным cHEC, каждый ровно через "
             f"4 + PLI байт от предыдущего",
        подробно=подробно, дальше=клиенты, вид_дальше="кадры")


# -- ATM -------------------------------------------------------------------------------

def atm(биты: np.ndarray) -> Находка | None:
    выборка = биты[:1 << 23]
    for сдвиг in range(8):
        данные = в_байты(выборка[сдвиг:])
        for начало in range(min(53, len(данные) - 53 * ПОДРЯД)):
            ячеек = (len(данные) - начало) // 53
            if ячеек < ПОДРЯД:
                break
            массив = np.frombuffer(данные[начало:начало + ячеек * 53],
                                   dtype=np.uint8).reshape(ячеек, 53)
            образец = массив[:ПОДРЯД]
            if not all(hec8(bytes(я[:4])) == я[4] for я in образец):
                continue
            верных = [hec8(bytes(я[:4])) == я[4] for я in массив[:20000]]
            доля = float(np.mean(верных))
            if доля < 0.9:
                continue
            return _описать_atm(массив, доля, сдвиг, начало)
    return None


def _описать_atm(ячейки: np.ndarray, доля: float, сдвиг: int, начало: int) -> Находка:
    vpi = ((ячейки[:, 0].astype(int) & 0x0F) << 4) | (ячейки[:, 1] >> 4)
    vci = ((ячейки[:, 1].astype(int) & 0x0F) << 12) | (ячейки[:, 2].astype(int) << 4) \
        | (ячейки[:, 3] >> 4)
    pt = (ячейки[:, 3] >> 1) & 0x07
    каналы = Counter(zip(vpi.tolist(), vci.tolist()))
    пустых = sum(с for (п, к), с in каналы.items() if п == 0 and к == 0)
    # AAL5: ячейки канала до ячейки с признаком конца (PT = 0x1 в младшем бите
    # типа пользователя); в конце PDU — длина и CRC-32.
    pdu = _aal5(ячейки, vpi, vci, pt)
    # Нагрузка ячеек бывает скремблирована x⁴³ + 1 (I.432.1: ATM в SDH/PDH): скремблер
    # идёт сплошь по нагрузкам всех ячеек, на заголовке останавливается с сохранением
    # состояния. Не сошлась CRC-32 — снимаем и собираем AAL5 заново.
    скремблер_снят = False
    if sum(1 for _, верна in pdu if верна) * 2 < max(1, len(pdu)):
        снятые = _снять_x43(ячейки)
        заново = _aal5(снятые, vpi, vci, pt)
        if sum(1 for _, верна in заново if верна) > sum(1 for _, верна in pdu if верна):
            ячейки, pdu, скремблер_снят = снятые, заново, True
    подробно = [f"байтовая граница — с бита {сдвиг}, первая ячейка — с байта {начало}",
                f"ячеек проверено: {min(len(ячейки), 20000)}; HEC верен у {доля * 100:.1f} %",
                "каналы (VPI/VCI): " + ", ".join(f"{п}/{к}×{с}" for (п, к), с in
                                                 каналы.most_common(8))
                + (f"; пустых ячеек (0/0) — {пустых}" if пустых else "")]
    if скремблер_снят:
        подробно.append("нагрузка ячеек — самосинхронизирующийся скремблер x⁴³ + 1 снят (I.432.1)")
    if pdu:
        подробно.append(f"AAL5: собрано PDU {len(pdu)}, CRC-32 сошлась у "
                        f"{sum(1 for _, верна in pdu if верна)}")
        инкапсуляция = Counter(_rfc2684(п) for п, верна in pdu if верна)
        if инкапсуляция:
            подробно.append("инкапсуляция (RFC 2684): " + ", ".join(
                f"{и}×{с}" for и, с in инкапсуляция.most_common(4)))
    return Находка(уровень="канальный", что="ячейки ATM (53 байта)", уверенность=доля,
                   мера=f"HEC верен у {доля * 100:.1f} % ячеек через каждые 53 байта",
                   подробно=подробно, дальше=[п for п, верна in pdu if верна] or None,
                   вид_дальше="кадры")


def _снять_x43(ячейки: np.ndarray) -> np.ndarray:
    """Снять x⁴³ + 1 с нагрузок ячеек подряд (заголовки — как есть)."""
    нагрузки = ячейки[:, 5:].reshape(-1)
    сплошь = skrembler.снять(np.unpackbits(нагрузки), (43,))
    сплошь = np.concatenate([np.zeros(43, dtype=np.uint8), сплошь])
    итог = ячейки.copy()
    итог[:, 5:] = np.packbits(сплошь[:len(нагрузки) * 8]).reshape(-1, 48)
    return итог


#: RFC 2684 (LLC/SNAP): заголовок LLC AA-AA-03, OUI 00-00-00 — маршрутизируемый протокол по
#: EtherType; OUI 00-80-C2 — мостовой (PID 0x0001/0x0007 — Ethernet с FCS/без).
def _rfc2684(pdu: bytes) -> str:
    if pdu[:6] == b"\xaa\xaa\x03\x00\x00\x00" and len(pdu) >= 8:
        тип = int.from_bytes(pdu[6:8], "big")
        return {0x0800: "LLC/SNAP, IPv4", 0x86DD: "LLC/SNAP, IPv6", 0x0806: "LLC/SNAP, ARP"}.get(
            тип, f"LLC/SNAP, EtherType 0x{тип:04X}")
    if pdu[:6] == b"\xaa\xaa\x03\x00\x80\xc2":
        return "LLC/SNAP, мост (Ethernet)"
    if pdu[:1] and pdu[0] >> 4 in (4, 6):
        return "VC-mux, IP без заголовка"
    return "другая"


def _aal5(ячейки: np.ndarray, vpi: np.ndarray, vci: np.ndarray, pt: np.ndarray
          ) -> List[Tuple[bytes, bool]]:
    собрано: Dict[Tuple[int, int], bytearray] = {}
    итог: List[Tuple[bytes, bool]] = []
    for номер in range(min(len(ячейки), 20000)):
        ключ = (int(vpi[номер]), int(vci[номер]))
        if ключ == (0, 0) or pt[номер] & 0b100:
            continue                             # пустая ячейка или служебная (OAM)
        собрано.setdefault(ключ, bytearray()).extend(ячейки[номер, 5:].tobytes())
        if pt[номер] & 0b001:                    # последняя ячейка PDU
            pdu = bytes(собрано.pop(ключ))
            длина = int.from_bytes(pdu[-6:-4], "big")
            верна = crc_.crc(pdu[:-4], 32, 0x04C11DB7, 0xFFFFFFFF, False, False,
                             0xFFFFFFFF) == int.from_bytes(pdu[-4:], "big")
            итог.append((pdu[:длина] if верна and длина <= len(pdu) - 8 else pdu, верна))
    return итог


def найти(биты: np.ndarray) -> Находка | None:
    return gfp(биты) or atm(биты)
