"""Вид кадра HDLC без типа канала: MTP2, LAPD, Frame Relay, LAPB, AX.25.

Кадры из потока (после снятия битстаффинга и FCS) и из .Sig приходят без
типа канала pcap. Вид определяется по признакам, которые стандарты требуют
у каждого кадра, — так же, как разборщики Wireshark проверяют поля
(packet-mtp2.c, packet-lapd.c, packet-fr.c, packet-lapb.c, packet-ax25.c):

- **MTP2** (Q.703 2.2–2.3): LI (6 бит) равен числу октетов после него (63 —
  «63 и больше»), запасные биты октета LI — нули; у LSSU запасные биты поля
  состояния — нули; у MSU индикатор службы SIO (Q.704 14.2.1) — из
  определённых и метка маршрутизации есть;
- **LAPD** (Q.921 3.2–3.6): адрес из двух октетов с EA0 = 0 и EA1 = 1, SAPI
  из определённых (0, 1, 16, 63 — Q.921; 62 — ЭиТО GSM Abis), поле
  управления — кадр I (модуль 128, два октета), S (RR/RNR/REJ без поля
  сведений) или U (SABME, DM, UI, DISC, UA, FRMR, XID);
- **Frame Relay** (Q.922 3.3, RFC 2427): адрес Q.922 из двух октетов, затем
  UI 0x03 и NLPID (IP 0xCC, IPv6 0x8E, SNAP 0x80, OSI 0x81–0x83, PPP 0xCF, с
  выравнивающим 0x00), LMI (Q.933 прил. A на DLCI 0 / ANSI T1.617 прил. D,
  LMI Cisco на DLCI 1023: дискриминатор 0x08, пустая ссылка вызова, STATUS
  ENQUIRY 0x75 / STATUS 0x7D) или EtherType (инкапсуляция Cisco);
- **LAPB** (X.25 2.3–2.4): адрес A/B (0x03/0x01) или C/D многоканального
  (0x0F/0x07), кадр I с пакетом X.25 (GFI: модуль 8 или 128), S или U без
  поля сведений (FRMR — с тремя октетами);
- **AX.25** (v2.2, 3.12): адреса получателя и отправителя — позывные из
  заглавных латинских букв, цифр и пробелов, сдвинутые на бит влево, бит
  расширения — только у последнего адреса.

Порознь признаки одного кадра могут совпасть случайно (у короткого кадра
LSSU MTP2 и кадр S LAPD одной длины), поэтому вид всего потока кадров
решает доля: у настоящего протокола признаки сходятся почти у всех кадров.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: Индикаторы службы MTP3 (Q.704 14.2.1): 0 SNM, 1–2 SNTM, 3 SCCP, 4 TUP, 5 ISUP,
#: 6–7 DUP, 8 испытания MTP, 9 B-ISUP, 10 ISUP спутниковый; 11–15 — запас.
SI_MTP3 = frozenset(range(11))
#: SAPI LAPD: 0 — Q.931, 1 — Q.931 пакетного режима, 16 — X.25, 63 — управление уровнем 2
#: (Q.921, табл. 2); 62 — ЭиТО (OML) интерфейса Abis GSM (packet-lapd.h).
SAPI_LAPD = frozenset({0, 1, 16, 62, 63})
#: Кадры U модуля 128 без бита P/F (Q.921 табл. 5): SABME, DM, UI, DISC, UA, FRMR, XID.
U_LAPD = frozenset({0x6F, 0x0F, 0x03, 0x43, 0x63, 0x87, 0xAF})
#: NLPID после UI в Frame Relay (RFC 2427; packet-osi.h): SNAP, CLNP, ES-IS, IS-IS, IPv6,
#: сжатие, IP, PPP.
NLPID_FR = frozenset({0x80, 0x81, 0x82, 0x83, 0x8E, 0xB0, 0xCC, 0xCF})
#: EtherType у инкапсуляции Cisco Frame Relay: IPv4, ARP, IPv6.
ETHERTYPE_FR = frozenset({0x0800, 0x0806, 0x86DD})
#: Адреса LAPB (X.25 2.4.2): 0x01 B, 0x03 A; многоканальный — 0x07 D, 0x0F C.
АДРЕСА_LAPB = frozenset({0x01, 0x03, 0x07, 0x0F})
#: Кадры U модуля 8 без бита P/F (X.25 2.3.4): SABM, SABME, DISC, DM, UA.
U_LAPB = frozenset({0x2F, 0x6F, 0x43, 0x0F, 0x63})
_ПОЗЫВНОЙ = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")


def mtp2(д: bytes) -> bool:
    """Сигнальная единица MTP2 без проверочных битов."""
    if len(д) < 3 or д[2] >> 6:
        return False
    li, после = д[2] & 0x3F, len(д) - 3
    if li != (после if после < 63 else 63):
        return False
    if li in (1, 2):
        return not д[3] >> 3                       # запасные биты поля состояния (Q.703 11.1.2)
    # MSU: SIO и метка маршрутизации (4 октета) и хотя бы один октет сведений.
    return li == 0 or (li >= 6 and (д[3] & 0x0F) in SI_MTP3)


def _адрес_q92x(д: bytes) -> bool:
    """Двухоктетный адрес Q.921/Q.922: EA первого октета 0, второго — 1."""
    return len(д) >= 3 and not д[0] & 1 and bool(д[1] & 1)


def lapd(д: bytes) -> bool:
    if not _адрес_q92x(д) or д[0] >> 2 not in SAPI_LAPD:
        return False
    упр, n = д[2], len(д)
    if not упр & 1:                                # I: два октета управления и сведения
        return n >= 5
    if упр & 3 == 1:                               # S: RR, RNR, REJ; второй октет — N(R), P/F
        return упр in (0x01, 0x05, 0x09) and n == 4
    основа = упр & 0xEF
    if основа not in U_LAPD:
        return False
    if основа in (0x03, 0xAF):                     # UI и XID — со сведениями
        return n >= 4
    if основа == 0x87:                             # FRMR — пять октетов сведений (модуль 128)
        return n == 8
    return n == 3


def dlci(д: bytes) -> int:
    """DLCI двухоктетного адреса Q.922: 6 старших бит первого и 4 старших второго октета."""
    return ((д[0] >> 2) << 4) | (д[1] >> 4)


def fr(д: bytes) -> bool:
    if not _адрес_q92x(д) or len(д) < 4:
        return False
    if д[2] == 0x03:
        if д[3] in NLPID_FR or (д[3] == 0x00 and len(д) > 4 and д[4] in NLPID_FR):
            return True
        # LMI: Q.933 прил. A / T1.617 прил. D на DLCI 0, LMI Cisco на DLCI 1023.
        return (д[3] == 0x08 and dlci(д) in (0, 1023) and len(д) >= 6
                and д[4] == 0x00 and д[5] in (0x75, 0x7D))
    return len(д) >= 24 and int.from_bytes(д[2:4], "big") in ETHERTYPE_FR


def lapb(д: bytes) -> bool:
    if len(д) < 2 or д[0] not in АДРЕСА_LAPB:
        return False
    упр, n = д[1], len(д)
    if not упр & 1:                                # I: пакет X.25, GFI — модуль 8 (01) или 128 (10)
        return n >= 5 and (д[2] >> 4) & 3 in (1, 2)
    if упр & 3 == 1:
        return упр & 0x0F in (0x01, 0x05, 0x09) and n == 2
    основа = упр & 0xEF
    if основа == 0x87:                             # FRMR: три октета сведений
        return n == 5
    return основа in U_LAPB and n == 2


def _позывной(д: bytes, м: int) -> bool:
    return (м + 7 <= len(д) and all(not б & 1 and (б >> 1) in _ПОЗЫВНОЙ for б in д[м:м + 6])
            and any((б >> 1) != 0x20 for б in д[м:м + 6]))


def ax25(д: bytes) -> bool:
    """Адреса AX.25: получатель, отправитель и до 8 ретрансляторов; за ними — управление."""
    место = 0
    for номер in range(10):
        if not _позывной(д, место):
            return False
        последний = д[место + 6] & 1
        место += 7
        if последний:
            return номер >= 1 and место < len(д)
    return False


def ais(д: bytes) -> bool:
    """Сообщение AIS (ITU-R M.1371): тип 1–5, 18, 19, 21, 24 и его точная длина, MMSI не нуль."""
    from ..potok.ais import годное_по_длине  # noqa: PLC0415 — кольцевой импорт пакетов
    return (len(д) >= 5 and годное_по_длине(8 * len(д), д[0] >> 2)
            and (int.from_bytes(д[1:5], "big") >> 2) & 0x3FFFFFFF != 0)


#: Проверки в порядке предпочтения при равной доле: у FR признаки строже (NLPID),
#: у MTP2 — слабее всех (случайный кадр сходится по LI в 1/256). AIS — после MTP2: по одному
#: кадру MSU длиной 21 байт сходится с AIS в 6 случаях из 64 (тип по BSN), а кадр AIS с MTP2 —
#: лишь при совпавшем LI и нулевом запасе (≈ 0,4 %); у всего потока решает доля.
ПРОВЕРКИ: Tuple[Tuple[str, Callable[[bytes], bool]], ...] = (
    ("Frame Relay", fr), ("LAPD", lapd), ("LAPB", lapb), ("AX.25", ax25), ("MTP2", mtp2), ("AIS", ais))


def вид(кадр: bytes) -> Optional[str]:
    """Вид одного кадра — первый, чьи признаки сошлись; None — ни один."""
    for имя, проверка in ПРОВЕРКИ:
        if проверка(кадр):
            return имя
    return None


#: Доля кадров, у которых признаки вида должны сойтись, чтобы признать вид всего потока.
ДОЛЯ = 0.8
#: Меньше кадров — вид не решается: у короткой выборки доля случайна.
КАДРОВ_ОТ = 8


def вид_потока(кадры: Sequence[bytes]) -> Optional[Tuple[str, float, Dict[str, float]]]:
    """Вид канального протокола по всем кадрам: (вид, доля, доли всех видов)."""
    if len(кадры) < КАДРОВ_ОТ:
        return None
    доли = {имя: sum(1 for к in кадры if проверка(к)) / len(кадры) for имя, проверка in ПРОВЕРКИ}
    порядок = [имя for имя, _ in ПРОВЕРКИ]
    лучший = max(порядок, key=lambda имя: (доли[имя], -порядок.index(имя)))
    if доли[лучший] < ДОЛЯ:
        return None
    return лучший, доли[лучший], доли


def сводка(кадры: Sequence[bytes], разобрать: Callable[[bytes], object], сколько: int = 2000) -> List[str]:
    """Частые описания кадров (``разобрать`` возвращает пакет с полем ``инфо``)."""
    счёт: Counter = Counter()
    for кадр in кадры[:сколько]:
        инфо = getattr(разобрать(кадр), "инфо", "") or ""
        счёт[инфо.split(",")[0].strip()] += 1
    return [f"{текст}×{с}" for текст, с in счёт.most_common(12) if текст]
