"""Двоичный Word 97–2003 (.doc) без посторонних программ.

Отдел отвечает на запросы документами Word, и половина из них — старого
двоичного формата: так их писали десять лет назад, так продолжают писать.
Штатный путь для таких файлов — headless LibreOffice (:mod:`legacy`): он
сохраняет заголовки по стилям и таблицы целиком, и первым идёт именно он.

Но LibreOffice есть не везде. В установке без ``libreoffice-writer``
преобразование молча не удаётся, и главный формат отдела оказывается
нечитаемым: письмо не находится по словам, а окно просмотра предлагает
скачать документ и открыть его чем-нибудь. Поэтому здесь лежит запасной
путь на чистом Python — он достаёт из файла текст и ничего больше.

Что он делает и чего не делает:

* достаёт **текст** — весь, включая текст таблиц (в Word они хранятся тем
  же потоком, разделители ячеек становятся переводами строк);
* **не восстанавливает разметку**: ни заголовков, ни границ таблиц, ни
  колонок. Для поиска и чтения этого хватает, для верстки — нет.

Устройство файла, ради которого написан разбор:

``.doc`` — это составной файл OLE2 (Compound File Binary): заголовок,
таблица FAT, каталог потоков. Нужны два потока: ``WordDocument`` с текстом
и служебными числами (FIB) и таблица кусков ``1Table`` либо ``0Table`` —
какая именно, сказано битом в FIB.

Текст лежит кусками, и порядок кусков задаёт таблица CLX в потоке таблицы.
Кусок бывает двух видов: «сжатый» — байт на знак по кодовой странице
Windows-1251, и обычный — UTF-16LE. Вид определяется битом в смещении.
Ради этой мелочи и нужен весь разбор: без таблицы кусков текст в файле идёт
не подряд, а вперемешку с удалёнными кусками правки.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List

from .. import registry
from ..convert import ConvertedDocument, _clean_line

__all__ = ["OLE_MAGIC", "cfb_streams", "doc_text", "convert_doc_native"]

#: Подпись составного файла OLE2 в первых восьми байтах.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: Конец цепочки и «сектор свободен» в таблице FAT.
_END_OF_CHAIN = 0xFFFFFFFE
_FREE_SECTOR = 0xFFFFFFFF

#: Запись каталога: 2 — поток, 5 — корень.
_TYPE_STREAM = 2

#: Смещения в FIB (заголовке потока WordDocument).
_FIB_FLAGS = 0x000A          # набор битов; бит 0x0200 — какая таблица
_FIB_FC_CLX = 0x01A2         # где в таблице лежит CLX
_FIB_LCB_CLX = 0x01A6        # и сколько его

#: Знаки, которыми Word размечает служебное. В текст им нельзя.
_CONTROL = {
    0x00: "", 0x01: "", 0x02: "", 0x03: "", 0x04: "", 0x05: "",
    0x07: "\n",          # конец ячейки таблицы
    0x08: "", 0x0B: "\n",  # разрыв строки
    0x0C: "\n",          # разрыв страницы
    0x0D: "\n",          # конец абзаца
    0x13: "", 0x14: "", 0x15: "",  # поля: код, разделитель, конец
    0x1E: "-", 0x1F: "",
    0xA0: " ",           # неразрывный пробел
}


def cfb_streams(data: bytes) -> Dict[str, bytes]:
    """Разобрать составной файл OLE2: имя потока → его байты.

    Полноценной библиотеки для этого в проекте нет и заводить её незачем:
    нужны два потока из десятка, и весь разбор — заголовок, FAT и каталог.
    """
    if len(data) < 512 or data[:8] != OLE_MAGIC:
        raise ValueError("файл не похож на составной документ OLE2")

    sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
    fat_count = struct.unpack_from("<I", data, 0x2C)[0]
    dir_start = struct.unpack_from("<I", data, 0x30)[0]
    mini_cutoff = struct.unpack_from("<I", data, 0x38)[0]
    mini_start = struct.unpack_from("<I", data, 0x3C)[0]
    difat_start = struct.unpack_from("<I", data, 0x44)[0]
    difat_count = struct.unpack_from("<I", data, 0x48)[0]
    if sector < 64 or sector > 1 << 20:
        raise ValueError("неправдоподобный размер сектора")

    def at(index: int) -> int:
        """Смещение сектора в файле. Нулевой сектор идёт сразу за заголовком."""
        return (index + 1) * sector

    # DIFAT: первые 109 записей лежат в заголовке, остальные — цепочкой.
    difat: List[int] = list(struct.unpack_from("<109I", data, 0x4C))
    node, left = difat_start, difat_count
    seen: set[int] = set()
    while left > 0 and node < _END_OF_CHAIN and node not in seen:
        seen.add(node)
        block = data[at(node):at(node) + sector]
        if len(block) < sector:
            break
        difat.extend(struct.unpack_from("<%dI" % (sector // 4 - 1), block, 0))
        node = struct.unpack_from("<I", block, sector - 4)[0]
        left -= 1

    fat: List[int] = []
    for index in difat[:fat_count]:
        if index >= _END_OF_CHAIN:
            continue
        if at(index) + sector > len(data):
            continue
        fat.extend(struct.unpack_from("<%dI" % (sector // 4), data, at(index)))

    def chain(start: int) -> List[int]:
        """Цепочка секторов от start. Петлю в битом файле обрываем."""
        out: List[int] = []
        node, visited = start, set()
        while node < _END_OF_CHAIN and node not in visited:
            visited.add(node)
            out.append(node)
            node = fat[node] if node < len(fat) else _END_OF_CHAIN
        return out

    def read_big(start: int, size: int) -> bytes:
        buf = b"".join(data[at(index):at(index) + sector] for index in chain(start))
        return buf[:size] if size else buf

    directory = read_big(dir_start, 0)
    entries = []
    for offset in range(0, len(directory) - 127, 128):
        record = directory[offset:offset + 128]
        name_len = struct.unpack_from("<H", record, 64)[0]
        name = record[:max(0, min(name_len - 2, 64))].decode("utf-16-le", "replace")
        entries.append({
            "name": name,
            "type": record[66],
            "start": struct.unpack_from("<I", record, 116)[0],
            "size": struct.unpack_from("<Q", record, 120)[0],
        })
    if not entries:
        raise ValueError("в составном файле нет каталога")

    # Мелкие потоки лежат не в секторах файла, а внутри одного «мини-потока»
    # корневой записи, и у них своя таблица.
    mini_fat: List[int] = []
    for index in chain(mini_start):
        if at(index) + sector <= len(data):
            mini_fat.extend(struct.unpack_from("<%dI" % (sector // 4), data, at(index)))
    mini_stream = b"".join(
        data[at(index):at(index) + sector] for index in chain(entries[0]["start"]))

    def read_mini(start: int, size: int) -> bytes:
        out = bytearray()
        node, visited = start, set()
        while node < _END_OF_CHAIN and node not in visited:
            visited.add(node)
            out += mini_stream[node * mini_size:(node + 1) * mini_size]
            node = mini_fat[node] if node < len(mini_fat) else _END_OF_CHAIN
        return bytes(out[:size])

    streams: Dict[str, bytes] = {}
    for entry in entries:
        if entry["type"] != _TYPE_STREAM or not entry["name"]:
            continue
        size = int(entry["size"])
        streams[entry["name"]] = (read_mini(entry["start"], size)
                                  if size < mini_cutoff
                                  else read_big(entry["start"], size))
    return streams


def doc_text(data: bytes) -> str:
    """Текст документа Word 97–2003 из байтов файла."""
    streams = cfb_streams(data)
    body = streams.get("WordDocument")
    if not body or len(body) < _FIB_LCB_CLX + 4:
        raise ValueError("в файле нет потока WordDocument")

    flags = struct.unpack_from("<H", body, _FIB_FLAGS)[0]
    wanted = "1Table" if flags & 0x0200 else "0Table"
    table = streams.get(wanted) or streams.get("1Table") or streams.get("0Table")
    if not table:
        raise ValueError("в файле нет таблицы кусков")

    fc_clx = struct.unpack_from("<I", body, _FIB_FC_CLX)[0]
    lcb_clx = struct.unpack_from("<I", body, _FIB_LCB_CLX)[0]
    # Срез за концом потока даёт пустоту, а не ошибку, — обрезанный файл
    # отобьётся ниже, на поиске списка кусков, с понятной причиной.
    clx = table[fc_clx:fc_clx + lcb_clx]

    # Перед таблицей кусков (0x02) могут стоять блоки свойств (0x01) —
    # пропускаем их по записанной длине.
    pos = 0
    while pos + 3 <= len(clx) and clx[pos] == 0x01:
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos + 5 > len(clx) or clx[pos] != 0x02:
        raise ValueError("в таблице кусков не найден список кусков")

    length = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + length]
    count = (len(plc) - 4) // 12
    if count <= 0:
        raise ValueError("список кусков пуст")
    marks = struct.unpack_from("<%dI" % (count + 1), plc, 0)

    parts: List[str] = []
    for index in range(count):
        base = 4 * (count + 1) + index * 8
        offset = struct.unpack_from("<I", plc, base + 2)[0]
        chars = marks[index + 1] - marks[index]
        if chars <= 0:
            continue
        if offset & 0x40000000:
            # Сжатый кусок: байт на знак. Кодовая страница у документов
            # отдела всегда Windows-1251 — кириллица иначе не хранится.
            start = (offset & ~0x40000000) // 2
            parts.append(body[start:start + chars].decode("cp1251", "replace"))
        else:
            parts.append(body[offset:offset + chars * 2].decode("utf-16-le", "replace"))
    return "".join(parts)


def _readable(raw: str) -> str:
    """Убрать служебные знаки Word и склеить строки в абзацы."""
    out: List[str] = []
    for char in raw:
        code = ord(char)
        if code in _CONTROL:
            out.append(_CONTROL[code])
        elif code < 0x20 and char not in "\n\t":
            out.append("")
        else:
            out.append(char)
    lines = [_clean_line(line) for line in "".join(out).split("\n")]

    # Пустых строк подряд в .doc бывает по десятку: пустой абзац там —
    # обычный приём вёрстки. Схлопываем их в одну.
    result: List[str] = []
    for line in lines:
        if not line and (not result or not result[-1]):
            continue
        result.append(line)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def _title_of(text: str) -> str:
    """Заголовок документа — его первая строка.

    Разметки в двоичном .doc мы не восстанавливаем, значит, и стиля
    «Заголовок 1» здесь нет. Но первая строка отчёта — это его название
    («Результаты технического анализа»), и это лучше имени файла.
    """
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def convert_doc_native(path: Path) -> ConvertedDocument:
    """DOC → текст своими силами, без LibreOffice."""
    path = Path(path)
    result = ConvertedDocument(text="", title=path.stem,
                               meta={"source_format": "doc"})
    raw = doc_text(path.read_bytes())
    result.text = _readable(raw)
    result.title = _title_of(result.text) or path.stem
    result.meta["reader"] = "word97"
    if result.is_empty:
        result.warnings.append(
            "в документе не найдено текста — возможно, он состоит из картинок")
    else:
        result.warnings.append(
            "документ прочитан своими силами, без LibreOffice: текст на месте, "
            "разметка (заголовки, таблицы) не восстановлена — поставьте "
            "LibreOffice, если разметка нужна")
    return result


def _is_ole(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(8) == OLE_MAGIC
    except OSError:
        return False


# ------------------------------------------------------------- регистрация ----

# Приоритет ниже, чем у пути через LibreOffice: тот сохраняет заголовки и
# таблицы, и когда он есть — работать должен он. Этот подхватывает, когда
# LibreOffice не установлен или в установке нет модуля Writer.
registry.register(registry.ConverterSpec(
    name="doc-native",
    suffixes=(".doc", ".dot"),
    convert=convert_doc_native,
    priority=-10,
    note="Word 97–2003 своими силами: текст без разметки, ничего не требует",
))
