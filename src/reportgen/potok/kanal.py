"""Канальный протокол в кадрах HDLC: ОКС-7 (MTP2), ISDN (LAPD), Frame Relay, X.25 (LAPB), AX.25.

После снятия битстаффинга и FCS в кадрах бывает не IP, а сигнализация и
канальные протоколы телефонных и выделенных линий: в КИ16 E1 — ОКС-7, в
D-канале ISDN PRI — LAPD с Q.931, на выделенных линиях — Frame Relay и
X.25. Вид решают признаки стандартов у всей массы кадров
(``setevoy.vid_kadra``), а дальше кадры разбирают те же разборщики, что и в
анализаторе пакетов: MTP2 → MTP3 → SCCP/TCAP/MAP/ISUP, LAPD → Q.931, FR →
IP/LMI, LAPB → X.25. Этап выгружается как pcap с типом канала (MTP2 — 140,
LAPD — 203, Frame Relay — 107, AX.25 — 3): анализатор пакетов открывает его
сразу нужным разборщиком.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional, Sequence

from ..setevoy import vid_kadra
from .nahodka import Находка

#: Вид → (канал разборщика анализатора пакетов, LINKTYPE pcap или None — выгрузка .Sig, полное имя).
КАНАЛЫ = {
    "MTP2": ("MTP2", 140, "ОКС-7, MTP2 (Q.703)"),
    "LAPD": ("LAPD", 203, "ISDN, LAPD (Q.921)"),
    "Frame Relay": ("Frame Relay", 107, "Frame Relay (Q.922, RFC 2427)"),
    "LAPB": ("LAPB без направления", None, "X.25, LAPB"),
    "AX.25": ("AX.25", 3, "AX.25"),
}
#: Сколько кадров разбирать ради сводки (остальные идут в выгрузку как есть).
РАЗБИРАТЬ_ДО = 3000


class Кадры(list):
    """Кадры этапа с типом канала pcap: выгрузка — pcap с этим LINKTYPE, а не .Sig."""

    def __init__(self, кадры: Sequence[bytes], канал: Optional[int]):
        super().__init__(bytes(к) for к in кадры)
        self.канал = канал


def _уровни(пакеты) -> List[str]:
    """Цепочки уровней (MTP2 → MTP3 → SCCP …) — частые."""
    счёт = Counter(" → ".join(у.протокол for у in п.уровни) for п in пакеты if п.уровни)
    return [f"{цепь}×{с}" for цепь, с in счёт.most_common(6)]


def _частые(пакеты, сколько: int = 10) -> List[str]:
    счёт = Counter((п.инфо or "").strip() for п in пакеты)
    return [f"{т}×{с}" for т, с in счёт.most_common(сколько) if т]


def найти(кадры: Sequence[bytes], откуда: str = "кадрах") -> Optional[Находка]:
    """Канальный протокол во всей массе кадров; None — ни один вид не сошёлся у большинства."""
    решение = vid_kadra.вид_потока(кадры)
    if решение is None:
        return None
    вид, доля, доли = решение
    from ..setevoy.razbor import разобрать_пакет  # noqa: PLC0415 — анализатор пакетов тяжёлый
    канал, linktype, имя = КАНАЛЫ[вид]
    пакеты = [разобрать_пакет(к, канал, номер=н + 1) for н, к in enumerate(кадры[:РАЗБИРАТЬ_ДО])]
    ошибок = sum(1 for п in пакеты if п.ошибки)
    подробно = [
        f"признаки вида по стандарту сошлись у {round(доля * 100)} % кадров; у других видов: "
        + ", ".join(f"{и} {round(д * 100)} %" for и, д in доли.items() if и != вид),
        "уровни: " + "; ".join(_уровни(пакеты)),
        "частые сообщения: " + "; ".join(_частые(пакеты)),
    ]
    if вид == "Frame Relay":
        подробно.append("DLCI: " + ", ".join(
            f"{д}×{с}" for д, с in Counter(vid_kadra.dlci(к) for к in кадры).most_common(8)))
    elif вид == "LAPD":
        подробно.append("SAPI/TEI: " + ", ".join(
            f"{с_ >> 2}/{т >> 1}×{с}" for (с_, т), с in Counter((к[0], к[1]) for к in кадры).most_common(8)))
    if ошибок:
        подробно.append(f"с замечаниями разборщика: {ошибок} из {len(пакеты)} разобранных "
                        f"(оборванные, неверные поля — подробно в анализаторе пакетов)")
    подробно.append("в анализаторе пакетов — кнопкой «Пакеты» у этапа: "
                    + (f"pcap с типом канала {linktype}" if linktype else "кадры .Sig (вид — по признакам)"))
    return Находка(
        уровень="канальный", что=имя, уверенность=доля,
        мера=f"признаки {вид} сошлись у {round(доля * len(кадры))} из {len(кадры)} {откуда}",
        подробно=подробно, дальше=Кадры(кадры, linktype), вид_дальше="кадры")
