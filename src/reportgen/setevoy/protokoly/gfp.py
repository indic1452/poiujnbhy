"""GFP (ITU-T G.7041) в анализаторе пакетов: LINKTYPE_GFP_F (171), LINKTYPE_GFP_T (170), MPLS (219).

Разбор — как у Wireshark (epan/dissectors/packet-gfp.c): запись pcap начинается с
основного заголовка уже без маски (cHEC считается по PLI как есть), область нагрузки —
уже без скремблера x⁴³ + 1. Основной заголовок: PLI и cHEC; при PLI ≥ 4 — заголовок
типа (PTI, PFI, EXI, UPI) и tHEC; EXI 1 — линейный: CID, запас, eHEC; при PFI = 1 —
pFCS в конце (CRC-32/BZIP2 поля данных клиента). HEC — CRC-16 x¹⁶ + x¹² + x⁵ + 1 с
начальным 0. Кадр клиента разбирается по UPI (привязки Wireshark, кроме его ошибочных
9 и 12): 1 — Ethernet с FCS (FCS проверяется), 2 — PPP (адрес и управление FF 03
необязательны), 13 — MPLS, 16 — IPv4, 17 — IPv6. Кадр управления клиентом (CMF, PTI 100)
— по своей таблице UPI. Таблицы — reportgen.potok.gfp_tablicy (собраны из packet-gfp.c).
"""

from __future__ import annotations

import zlib

from ...potok.gfp_tablicy import (
    EXI,
    LINKTYPE_GFP_F,
    LINKTYPE_GFP_T,
    LINKTYPE_MPLS,
    PLI,
    PTI,
    UPI_ДАННЫЕ,
    UPI_УПРАВЛЕНИЕ,
)
from ..chtenie import КАНАЛЫ
from ..pole import u16
from ..razbor import ДОП_УРОВНИ, КАНАЛ_В_РАЗБОРЩИК, Разбор, ethernet, ipv4, ipv6, mpls, ppp, данные

_ОБРАТНЫЙ = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def hec(данные_: bytes) -> int:
    """CRC-16 x¹⁶ + x¹² + x⁵ + 1, начальное 0, старшим битом вперёд (cHEC, tHEC, eHEC)."""
    рег = 0
    for байт in данные_:
        рег ^= байт << 8
        for _ in range(8):
            рег = ((рег << 1) ^ 0x1021) & 0xFFFF if рег & 0x8000 else (рег << 1) & 0xFFFF
    return рег


def pfcs(данные_: bytes) -> int:
    """CRC-32/BZIP2 (Wireshark: ~crc32_mpeg2) — через zlib на отражённых байтах."""
    return int(f"{zlib.crc32(данные_.translate(_ОБРАТНЫЙ)):032b}"[::-1], 2)


def _по_таблице(таблица, значение: int) -> str:
    return next((русское for от, до, _, русское in таблица if от <= значение <= до), str(значение))


def _hec_поле(р: Разбор, у, имя: str, ключ: str, м: int) -> bool:
    """Поле HEC после двух байт с места м; верен ли."""
    р.нужно(м, 4)
    есть, надо = u16(р.д, м + 2), hec(р.д[м:м + 2])
    у.поле(имя, ключ, f"0x{есть:04x} [" + ("верен]" if есть == надо else f"неверен, должен быть 0x{надо:04x}]"),
           м + 2, 2, есть, плохо=есть != надо)
    return есть == надо


def gfp(р: Разбор, м: int) -> None:
    у = р.уровень("GFP", "Generic Framing Procedure (G.7041)", м)
    р.нужно(м, 4)
    pli = u16(р.д, м)
    у.поле("PLI (длина нагрузки)", "gfp.pli", f"{pli} ({_по_таблице(PLI, pli)})", м, 2, pli)
    _hec_поле(р, у, "cHEC", "gfp.chec", м)
    у.длина = 4
    if pli == 0:
        у.итог = "пустой кадр"
        if len(р.д) > м + 4:
            р.ошибка("в пустом кадре есть нагрузка")
        return
    if pli < 4:
        у.итог = "служебный кадр (резерв)"
        return
    if len(р.д) < м + 4 + pli:
        р.ошибка(f"PLI {pli} больше записанного ({len(р.д) - м - 4} байт)")
    конец = min(len(р.д), м + 4 + pli)
    м += 4
    р.нужно(м, 4)
    pti, pfi, exi, upi = р.д[м] >> 5, (р.д[м] >> 4) & 1, р.д[м] & 0x0F, р.д[м + 1]
    таблица = UPI_УПРАВЛЕНИЕ if pti == 4 else UPI_ДАННЫЕ
    тип = у.поле("Поле типа", "gfp.type", f"0x{u16(р.д, м):04x}", м, 2, u16(р.д, м))
    у.поле("PTI", "gfp.pti", f"{pti:03b} ({PTI.get(pti, ('', 'резерв'))[1]})", м, 1, pti, родитель=тип)
    у.поле("PFI", "gfp.pfi", "pFCS есть" if pfi else "pFCS нет", м, 1, pfi, родитель=тип)
    у.поле("EXI", "gfp.exi", f"{exi} ({EXI.get(exi, ('', 'резерв'))[1]})", м, 1, exi, родитель=тип)
    у.поле("UPI", "gfp.upi", f"0x{upi:02x} ({_по_таблице(таблица, upi)})", м + 1, 1, upi, родитель=тип)
    _hec_поле(р, у, "tHEC", "gfp.thec", м)
    м += 4
    if exi == 1:
        р.нужно(м, 4)
        у.поле("CID (канал)", "gfp.cid", р.д[м], м, 1)
        у.поле("Запас", "gfp.spare", f"0x{р.д[м + 1]:02x}", м + 1, 1, р.д[м + 1])
        _hec_поле(р, у, "eHEC", "gfp.ehec", м)
        м += 4
    if pfi and конец - м >= 4:
        конец -= 4
        есть, надо = int.from_bytes(р.д[конец:конец + 4], "big"), pfcs(р.д[м:конец])
        у.поле("pFCS", "gfp.fcs", f"0x{есть:08x} [" + ("верна]" if есть == надо else f"неверна, должна быть 0x{надо:08x}]"),
               конец, 4, есть, плохо=есть != надо)
    у.длина = м - у.смещение
    у.итог = (f"{PTI.get(pti, ('', f'PTI {pti:03b}'))[1]}: {_по_таблице(таблица, upi)}"
              + (f", канал {р.д[у.смещение + 8]}" if exi == 1 else "") + f", PLI {pli}")
    if pti == 4:
        р.п.инфо = "GFP CMF: " + _по_таблице(таблица, upi)
        данные(р, м, "управление клиентом GFP", конец)
    elif pti in (0, 5) and upi == 1 and конец - м >= 18:
        fcs = int.from_bytes(р.д[конец - 4:конец], "little")
        верна = zlib.crc32(р.д[м:конец - 4]) == fcs
        у.поле("FCS Ethernet", "eth.fcs", f"0x{fcs:08x} [" + ("верна]" if верна else "неверна]"), конец - 4, 4, fcs,
               плохо=not верна)
        ethernet(р, м)
    elif pti in (0, 5) and upi in _КЛИЕНТЫ:
        _КЛИЕНТЫ[upi](р, м)
    else:
        данные(р, м, f"GFP UPI 0x{upi:02x} ({_по_таблице(таблица, upi)})", конец)


_КЛИЕНТЫ = {2: ppp, 13: mpls, 16: ipv4, 17: ipv6}

for _номер, _канал, _разборщик in ((LINKTYPE_GFP_F, "GFP-F", gfp), (LINKTYPE_GFP_T, "GFP-T", gfp),
                                   (LINKTYPE_MPLS, "MPLS", mpls)):
    КАНАЛЫ.setdefault(_номер, _канал)
    КАНАЛ_В_РАЗБОРЩИК.setdefault(_канал, _разборщик)
ДОП_УРОВНИ.setdefault("GFP", "канальный")
