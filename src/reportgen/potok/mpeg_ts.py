"""Транспортный поток MPEG-TS: таблицы PSI/SI (PAT, PMT, SDT) и IP в секциях MPE.

Пакет TS (ISO/IEC 13818-1, 2.4.3): синхробайт 0x47, TEI, PUSI, PID (13 бит),
управление скремблированием и полем адаптации, счётчик непрерывности. Таблицы
идут секциями (2.4.4): table_id, длина секции (12 бит) и CRC-32 MPEG-2 в конце;
секция собирается по PUSI и указателю из нескольких пакетов.

- **PAT** (PID 0, table_id 0x00): номера программ → PID их PMT;
- **PMT** (table_id 0x02): PCR PID и элементарные потоки — тип (видео MPEG-2,
  H.264, HEVC, звук, данные DSM-CC …) и PID;
- **SDT** (PID 0x11, table_id 0x42/0x46): названия служб и поставщиков из
  описателя службы 0x48 (EN 300 468, 6.2.33; кодировка текста — по первому
  байту, прил. A: 0x01 — ISO 8859-5, кириллица);
- **MPE** (table_id 0x3E, EN 301 192, 7.1): секции датаграмм — адрес MAC
  получателя и IP-датаграмма (или LLC/SNAP), CRC-32 секции.

Поля и значения — по разборщикам Wireshark (packet-mp2t.c, packet-mpeg-sect.c,
packet-mpeg-pat.c, packet-mpeg-pmt.c, packet-dvb-sdt.c, packet-dvb-data-mpe.c,
packet-mpeg-descriptor.c).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .dvbs2 import crc32_mpeg2
from .nahodka import Находка

#: Типы элементарных потоков PMT (ISO/IEC 13818-1 табл. 2-34; Wireshark packet-mpeg-pmt.c) — главные.
ТИПЫ_ПОТОКОВ = {
    0x01: "видео MPEG-1", 0x02: "видео MPEG-2 (H.262)", 0x03: "звук MPEG-1", 0x04: "звук MPEG-2",
    0x05: "частные секции", 0x06: "частные данные в PES (телетекст, субтитры, AC-3 DVB)",
    0x0B: "DSM-CC тип B (карусель данных)", 0x0D: "DSM-CC тип D (MPE и др.)",
    0x0F: "звук AAC (ADTS)", 0x10: "видео MPEG-4 Part 2", 0x11: "звук AAC (LATM)",
    0x15: "метаданные в PES", 0x1B: "видео H.264/AVC", 0x24: "видео H.265/HEVC",
    0x81: "звук AC-3 (ATSC)", 0x87: "звук E-AC-3 (ATSC)",
}
#: Типы служб SDT (EN 300 468 табл. 87; Wireshark mpeg_descr_service_type_vals) — главные.
ТИПЫ_СЛУЖБ = {0x01: "ТВ", 0x02: "радио", 0x03: "телетекст", 0x0C: "передача данных",
              0x11: "ТВ HD MPEG-2", 0x16: "ТВ SD H.264", 0x19: "ТВ HD H.264", 0x1F: "ТВ HEVC"}


def текст_dvb(байты: bytes) -> str:
    """Строка DVB (EN 300 468 прил. A): первый байт 0x01–0x0B — таблица ISO 8859-5…15,
    0x10 — ISO 8859 по следующим двум байтам, 0x15 — UTF-8; иначе — латиница."""
    if not байты:
        return ""
    первый = байты[0]
    кодировка, тело = "latin-1", байты
    if 0x01 <= первый <= 0x0B:
        кодировка, тело = f"iso8859_{первый + 4}", байты[1:]
    elif первый == 0x10 and len(байты) >= 3:
        кодировка, тело = f"iso8859_{int.from_bytes(байты[1:3], 'big')}", байты[3:]
    elif первый == 0x15:
        кодировка, тело = "utf-8", байты[1:]
    elif первый < 0x20:
        тело = байты[1:]
    try:
        return тело.decode(кодировка, errors="replace").strip()
    except LookupError:
        return тело.decode("latin-1", errors="replace").strip()


@dataclass
class Разбор:
    пакетов: int = 0
    pid: Counter = field(default_factory=Counter)
    разрывов: int = 0                    # счётчик непрерывности сбился
    ошибок_tei: int = 0
    скремблировано: int = 0
    секций: int = 0
    crc_неверно: int = 0
    программы: Dict[int, int] = field(default_factory=dict)              # программа → PID PMT
    потоки: Dict[int, List[Tuple[int, int]]] = field(default_factory=dict)  # программа → [(тип, PID)]
    службы: Dict[int, Tuple[int, str, str]] = field(default_factory=dict)   # служба → (тип, поставщик, имя)
    датаграммы: List[bytes] = field(default_factory=list)
    mac: Counter = field(default_factory=Counter)


def _секция(р: Разбор, pid: int, с: bytes) -> None:
    р.секций += 1
    tid = с[0]
    синтаксис = с[1] >> 7
    if синтаксис and crc32_mpeg2(с) != 0:
        р.crc_неверно += 1
        return
    конец = len(с) - 4 if синтаксис else len(с)
    if tid == 0x00 and pid == 0:
        for м in range(8, конец - 3, 4):
            программа, pmt = int.from_bytes(с[м:м + 2], "big"), int.from_bytes(с[м + 2:м + 4], "big") & 0x1FFF
            if программа:                                   # 0 — PID сетевой информации (NIT)
                р.программы[программа] = pmt
    elif tid == 0x02:
        программа = int.from_bytes(с[3:5], "big")
        info = int.from_bytes(с[10:12], "big") & 0x0FFF
        м, потоки = 12 + info, []
        while м + 5 <= конец:
            тип, epid = с[м], int.from_bytes(с[м + 1:м + 3], "big") & 0x1FFF
            потоки.append((тип, epid))
            м += 5 + (int.from_bytes(с[м + 3:м + 5], "big") & 0x0FFF)
        р.потоки[программа] = потоки
    elif tid in (0x42, 0x46) and pid == 0x11:
        м = 11
        while м + 5 <= конец:
            служба = int.from_bytes(с[м:м + 2], "big")
            дл = int.from_bytes(с[м + 3:м + 5], "big") & 0x0FFF
            д, д_конец = м + 5, min(м + 5 + дл, конец)
            while д + 2 <= д_конец:
                тег, дд = с[д], с[д + 1]
                if тег == 0x48 and дд >= 3:
                    тип, дп = с[д + 2], с[д + 3]
                    поставщик = текст_dvb(с[д + 4:д + 4 + дп])
                    ди = с[д + 4 + дп]
                    имя = текст_dvb(с[д + 5 + дп:д + 5 + дп + ди])
                    р.службы[служба] = (тип, поставщик, имя)
                д += 2 + дд
            м += 5 + дл
    elif tid == 0x3E and len(с) >= 12 + 4:
        mac = bytes([с[11], с[10], с[9], с[8], с[4], с[3]])
        скремблирование, llc = (с[5] >> 4) & 3, (с[5] >> 1) & 1
        if not скремблирование and not llc:
            р.датаграммы.append(bytes(с[12:конец]))
        р.mac[mac.hex(":")] += 1


def разобрать(данные: bytes, длина: int = 188, сдвиг: int = 0, *, предел: int = 200_000) -> Разбор:
    """Пакеты TS подряд с места ``сдвиг``: счёт PID, непрерывность, секции PSI/SI и MPE."""
    р = Разбор()
    сборка: Dict[int, bytearray] = {}
    счётчики: Dict[int, int] = {}
    for м in range(сдвиг, len(данные) - 187, длина):
        п = данные[м:м + 188]
        if п[0] != 0x47:
            continue
        р.пакетов += 1
        if р.пакетов > предел:
            break
        tei, pusi = п[1] >> 7, (п[1] >> 6) & 1
        pid = ((п[1] & 0x1F) << 8) | п[2]
        скр, afc, cc = п[3] >> 6, (п[3] >> 4) & 3, п[3] & 15
        р.pid[pid] += 1
        р.ошибок_tei += tei
        if pid == 0x1FFF:
            continue                                        # пустые пакеты
        if afc & 1:                                         # есть полезная нагрузка — счётчик растёт
            if pid in счётчики and (счётчики[pid] + 1) % 16 != cc and счётчики[pid] != cc:
                р.разрывов += 1
            счётчики[pid] = cc
        if скр:
            р.скремблировано += 1
            continue
        начало = 4 + (1 + п[4] if afc & 2 else 0)
        if not afc & 1 or начало >= 188:
            continue
        нагрузка = п[начало:]
        if pusi:
            указатель = нагрузка[0]
            if pid in сборка:
                сборка[pid] += нагрузка[1:1 + указатель]
                _завершить(р, pid, сборка)
            сборка[pid] = bytearray(нагрузка[1 + указатель:])
            _завершить(р, pid, сборка)
        elif pid in сборка:
            сборка[pid] += нагрузка
            _завершить(р, pid, сборка)
    return р


def _завершить(р: Разбор, pid: int, сборка: Dict[int, bytearray]) -> None:
    """Выделить собранные секции из буфера PID (подряд, до набивки 0xFF)."""
    буфер = сборка[pid]
    while len(буфер) >= 3:
        if буфер[0] == 0xFF:
            del сборка[pid]
            return
        длина = 3 + (((буфер[1] & 0x0F) << 8) | буфер[2])
        if len(буфер) < длина:
            return
        _секция(р, pid, bytes(буфер[:длина]))
        del буфер[:длина]


def находка(данные: bytes, длина: int = 188, сдвиг: int = 0) -> Optional[Находка]:
    """Сводка транспортного потока; None — таблиц PSI нет (PAT не собралась)."""
    р = разобрать(данные, длина, сдвиг)
    if not р.программы and not р.датаграммы:
        return None
    подробно = [f"пакетов {р.пакетов}; PID: " + ", ".join(
        f"0x{pid:04X}×{с}" for pid, с in р.pid.most_common(10))]
    подробно.append(f"секций {р.секций}, CRC-32 неверна у {р.crc_неверно}; разрывов счётчика непрерывности "
                    f"{р.разрывов}; с ошибкой передачи (TEI) {р.ошибок_tei}; скремблированных {р.скремблировано}")
    for программа, pmt in sorted(р.программы.items())[:20]:
        потоки = "; ".join(f"PID 0x{pid:04X} — {ТИПЫ_ПОТОКОВ.get(тип, f'тип 0x{тип:02X}')}"
                           for тип, pid in р.потоки.get(программа, []))
        служба = р.службы.get(программа)
        имя = f" «{служба[2]}» ({служба[1] or 'поставщик не указан'}, {ТИПЫ_СЛУЖБ.get(служба[0], f'тип 0x{служба[0]:02X}')})" \
            if служба else ""
        подробно.append(f"программа {программа}{имя}: PMT PID 0x{pmt:04X}" + (f"; {потоки}" if потоки else ""))
    if р.датаграммы:
        подробно.append(f"MPE: IP-датаграмм {len(р.датаграммы)}; адреса MAC получателей: "
                        + ", ".join(f"{м}×{с}" for м, с in р.mac.most_common(5)))
    return Находка(
        уровень="транспортный", что="MPEG-TS: таблицы PSI/SI" + (" и IP в MPE" if р.датаграммы else ""),
        уверенность=1.0 if not р.crc_неверно else р.секций / (р.секций + р.crc_неверно),
        мера=f"секций с верной CRC-32: {р.секций - р.crc_неверно} из {р.секций}",
        подробно=подробно, дальше=р.датаграммы or None, вид_дальше="кадры" if р.датаграммы else "")
