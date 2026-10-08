"""Поточное чтение захватов pcap и pcapng — по записи, без загрузки файла в память.

Файл может ещё дописываться (идущий захват): неполный последний блок не
считается ошибкой — чтение возвращает «пока нечего» и продолжает с того же
места, когда блок допишут. Так прогон идёт следом за захватом.

Форматы:

* pcap — draft-ietf-opsawg-pcap (libpcap sf-pcap.c): заголовок файла 24 байта
  (магия 0xA1B2C3D4 — секунды и микросекунды, 0xA1B23C4D — наносекунды; порядок
  байт узнаётся по магии), затем записи: секунды, доли, записано, исходная длина
  (по 4 байта) и данные.
* pcapng — draft-ietf-opsawg-pcapng: блоки «тип, полная длина, тело, полная
  длина»; SHB задаёт порядок байт секции (магия 0x1A2B3C4D) и сбрасывает
  интерфейсы, IDB — тип канала, snaplen и единицу времени (if_tsresol: 10^-v, при
  старшем бите — 2^-v; по умолчанию 10^-6), EPB (тип 6) и устаревший OPB (тип 2) —
  пакеты с временем, SPB (тип 3) — пакет без времени (берётся время предыдущего),
  прочие блоки пропускаются по длине. Склейка файлов pcapng (несколько секций) —
  тоже файл pcapng.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from ..chtenie import КАНАЛЫ

#: Длиннее записи не бывает: MAXIMUM_SNAPLEN libpcap (pcap-int.h) — 262144; с запасом — 16 МБ.
ЗАПИСЬ_ДО = 1 << 24
БЛОК_ДО = ЗАПИСЬ_ДО + 1024
PCAP_МАГИИ = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
              b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}
SHB = b"\x0a\x0d\x0d\x0a"


def канал_по_типу(тип: int) -> str:
    """LINKTYPE_* → имя канала разборщика (как в chtenie.КАНАЛЫ)."""
    return КАНАЛЫ.get(тип & 0xFFFF, f"тип {тип & 0xFFFF}")


def доля_секунды(if_tsresol: bytes) -> float:
    """Единица времени интерфейса pcapng: if_tsresol — 10^-v, при старшем бите — 2^-(v & 0x7F)."""
    if not if_tsresol:
        return 1e-6
    v = if_tsresol[0]
    return 2.0 ** -(v & 0x7F) if v & 0x80 else 10.0 ** -v


def опции_блока(тело: bytes, место: int, порядок: str) -> dict[int, bytes]:
    """Опции блока pcapng (код u16, длина u16, значение с дополнением до 4) до opt_endofopt.
    Значение не читается за концом тела; повтор кода — берётся первое."""
    итог: dict[int, bytes] = {}
    while место + 4 <= len(тело):
        код, дл = struct.unpack_from(порядок + "HH", тело, место)
        if код == 0:
            break
        итог.setdefault(код, тело[место + 4:место + 4 + дл])
        место += 4 + дл + (-дл % 4)
    return итог


class ЧтецФайла:
    """Записи файла pcap/pcapng по одной: (время, данные, исходная длина, канал).

    ``следующий()`` — запись или None, если целой записи пока нет (файл дописывается
    или кончился); ``испорчен`` — причина, если файл дальше читать нельзя.
    """

    def __init__(self, путь: Path | str, файл: BinaryIO | None = None):
        self.путь = Path(путь)
        self.файл = файл or open(self.путь, "rb")  # noqa: SIM115 — закрывает закрыть()
        self.место = 0
        self.вид = ""                                # "pcap" или "pcapng", пока не узнан — ""
        self.порядок = "<"
        self.доля = 1e-6
        self.канал = ""
        self.snaplen = 0
        self.интерфейсы: list[tuple[str, float, int]] = []
        self.время = 0.0
        self.испорчен = ""
        #: Где в файле лежат данные последней выданной записи (хранилище «Пакетов» ссылается на них, не копируя).
        self.место_данных = 0

    def закрыть(self) -> None:
        self.файл.close()

    def __enter__(self) -> ЧтецФайла:
        return self

    def __exit__(self, *_) -> None:
        self.закрыть()

    def _размер(self) -> int:
        try:
            return os.fstat(self.файл.fileno()).st_size
        except (OSError, ValueError, AttributeError):
            текущее = self.файл.tell()
            self.файл.seek(0, os.SEEK_END)
            размер = self.файл.tell()
            self.файл.seek(текущее)
            return размер

    def _прочитать(self, место: int, сколько: int) -> bytes | None:
        if место + сколько > self._размер():
            return None
        self.файл.seek(место)
        данные = self.файл.read(сколько)
        return данные if len(данные) == сколько else None

    def следующий(self) -> tuple[float, bytes, int, str] | None:
        if self.испорчен:
            return None
        if not self.вид:
            начало = self._прочитать(0, 4)
            if начало is None:
                return None
            if начало in PCAP_МАГИИ:
                заголовок = self._прочитать(0, 24)
                if заголовок is None:
                    return None
                self.вид = "pcap"
                self.порядок, self.доля = PCAP_МАГИИ[начало]
                self.snaplen = struct.unpack_from(self.порядок + "I", заголовок, 16)[0]
                self.канал = канал_по_типу(struct.unpack_from(self.порядок + "I", заголовок, 20)[0])
                self.место = 24
            elif начало == SHB:
                self.вид = "pcapng"
            else:
                self.испорчен = "не pcap и не pcapng"
                return None
        return self._pcap() if self.вид == "pcap" else self._pcapng()

    def _pcap(self) -> tuple[float, bytes, int, str] | None:
        голова = self._прочитать(self.место, 16)
        if голова is None:
            return None
        сек, дробь, записано, исходно = struct.unpack(self.порядок + "IIII", голова)
        if записано > ЗАПИСЬ_ДО:
            self.испорчен = f"запись на смещении {self.место} испорчена (длина {записано})"
            return None
        данные = self._прочитать(self.место + 16, записано)
        if данные is None:
            return None
        self.место_данных = self.место + 16
        self.место += 16 + записано
        self.время = сек + дробь * self.доля
        return self.время, данные, исходно, self.канал

    def _pcapng(self) -> tuple[float, bytes, int, str] | None:
        while True:
            голова = self._прочитать(self.место, 12)
            if голова is None:
                return None
            if голова[:4] == SHB:
                self.порядок = "<" if голова[8:] == b"\x4d\x3c\x2b\x1a" else ">"      # голова — 12 байт
                self.интерфейсы = []
            вид, длина = struct.unpack_from(self.порядок + "II", голова)
            if длина < 12 or длина % 4 or длина > БЛОК_ДО:
                self.испорчен = f"блок на смещении {self.место} испорчен (длина {длина})"
                return None
            блок = self._прочитать(self.место, длина)
            if блок is None:
                return None
            if struct.unpack_from(self.порядок + "I", блок, длина - 4)[0] != длина:
                self.испорчен = f"блок на смещении {self.место}: длины в начале и в конце разные"
                return None
            начало_блока = self.место
            self.место += длина
            тело = блок[8:длина - 4]
            if вид == 1 and len(тело) >= 8:                                  # IDB
                тип, _, snap = struct.unpack_from(self.порядок + "HHI", тело)
                доля = доля_секунды(опции_блока(тело, 8, self.порядок).get(9, b""))
                self.интерфейсы.append((канал_по_типу(тип), доля, snap))
            elif вид in (6, 2) and len(тело) >= 20:                          # EPB, OPB
                if вид == 6:
                    номер, старшие, младшие, записано, исходно = struct.unpack_from(self.порядок + "IIIII", тело)
                else:
                    номер, _, старшие, младшие, записано, исходно = struct.unpack_from(self.порядок + "HHIIII", тело)
                if номер < len(self.интерфейсы):
                    канал, доля, _ = self.интерфейсы[номер]
                    self.время = ((старшие << 32) | младшие) * доля
                    self.место_данных = начало_блока + 8 + 20
                    return self.время, тело[20:20 + записано], исходно, канал
            elif вид == 3 and len(тело) >= 4 and self.интерфейсы:          # SPB — без времени
                исходно = struct.unpack_from(self.порядок + "I", тело)[0]
                канал, _, snap = self.интерфейсы[0]
                записано = min(исходно, snap or исходно)      # срез ниже не выйдет за конец тела
                self.место_данных = начало_блока + 8 + 4
                return self.время, тело[4:4 + записано], исходно, канал


def записи(путь: Path | str) -> Iterator[tuple[float, bytes, int, str]]:
    """Все целые записи файла (оборванный конец — просто конец)."""
    with ЧтецФайла(путь) as чтец:
        while (запись := чтец.следующий()) is not None:
            yield запись
