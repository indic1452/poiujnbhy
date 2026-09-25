# -*- coding: utf-8 -*-
"""Поиск по комбинациям в любом потоке: образец, сигнатуры файлов, строки, частые n-граммы.

Поток после демодуляции к байтам не выровнен: комбинация может начинаться с
любого бита, а поток — идти инверсным. Поэтому всё ищется в восьми
«байтовых видах» потока (сдвиг 0…7 бит) и, по желанию, в их инверсии;
позиция находки — в битах от начала потока.

Сигнатура файла — это только начало: у случайного потока три байта FF D8 FF
встречаются раз на 16 миллионов позиций, и на сотне мегабайт это десятки
«JPEG». Поэтому каждая находка проверяется структурой формата (маркеры,
длины, контрольные суммы, распаковка), и там, где формат это позволяет,
вычисляется длина — файл можно вырезать и скачать.
"""

from __future__ import annotations

import bz2
import lzma
import re
import struct
import zlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

#: Сколько байт вида просматривать (на каждый из восьми сдвигов).
ВИД_ДО = 32 << 20
#: Самый большой вырезаемый файл.
ФАЙЛ_ДО = 64 << 20
НАХОДОК_ДО = 2000


# -- виды потока --------------------------------------------------------------------------

def виды(биты: np.ndarray, *, сдвиги: Sequence[int] = range(8), инверсия: bool = False
         ) -> List[Tuple[int, bool, bytes]]:
    """(сдвиг, инвертирован, байты): поток, упакованный с каждого из сдвигов."""
    итог = []
    for сдвиг in сдвиги:
        ряд = биты[сдвиг:сдвиг + ВИД_ДО * 8]
        ряд = ряд[:len(ряд) // 8 * 8]
        for инв in ((False, True) if инверсия else (False,)):
            итог.append((сдвиг, инв, np.packbits(ряд ^ 1 if инв else ряд).tobytes()))
    return итог


# -- образец ------------------------------------------------------------------------------

КОДИРОВКИ = ("utf-8", "cp1251", "utf-16-le", "koi8-r", "cp866")


def образец(текст: str, вид: str, кодировка: str = "utf-8") -> Tuple[Optional[bytes], np.ndarray]:
    """Образец поиска: (байты или None, биты). Вид — hex, текст или биты."""
    if not текст:
        raise ValueError("образец пуст")
    if вид == "hex":
        чистый = re.sub(r"[\s:,\-]|0x", "", текст.lower())
        if not чистый or len(чистый) % 2 or not re.fullmatch(r"[0-9a-f]+", чистый):
            raise ValueError("HEX — чётное число шестнадцатеричных цифр (пробелы и двоеточия можно)")
        байты = bytes.fromhex(чистый)
    elif вид == "текст":
        if кодировка not in КОДИРОВКИ:
            raise ValueError(f"кодировка — одна из {', '.join(КОДИРОВКИ)}")
        байты = текст.encode(кодировка)
    elif вид == "биты":
        if not re.fullmatch(r"[01 ]+", текст):
            raise ValueError("биты — только 0 и 1")
        биты = np.array([int(c) for c in текст if c in "01"], dtype=np.uint8)
        return (np.packbits(биты).tobytes() if len(биты) % 8 == 0 else None), биты
    else:
        raise ValueError("вид образца — hex, текст или биты")
    if len(байты) > 256:
        raise ValueError("образец длиннее 256 байт")
    return байты, np.unpackbits(np.frombuffer(байты, dtype=np.uint8))


def _все(данные: bytes, что: bytes, предел: int) -> List[int]:
    итог, место = [], данные.find(что)
    while место >= 0 and len(итог) < предел:
        итог.append(место)
        место = данные.find(что, место + 1)
    return итог


def найти_образец(биты: np.ndarray, байты: Optional[bytes], биты_образца: np.ndarray, *,
                  любой_сдвиг: bool = True, инверсия: bool = False,
                  предел: int = НАХОДОК_ДО) -> List[Dict[str, object]]:
    """Все вхождения: позиция в битах, сдвиг, инверсия. Контекст — позже, по позиции."""
    найдено: List[Dict[str, object]] = []
    if байты is not None:
        for сдвиг, инв, вид in виды(биты, сдвиги=range(8) if любой_сдвиг else (0,), инверсия=инверсия):
            for место in _все(вид, байты, предел):
                найдено.append({"бит": 8 * место + сдвиг, "сдвиг": сдвиг, "инверсия": инв})
    else:
        from .sinhro import несовпадения  # noqa: PLC0415
        выборка = биты[:ВИД_ДО * 8]
        н = несовпадения(выборка, биты_образца)
        for инв, места in ((False, np.flatnonzero(н == 0)),
                           (True, np.flatnonzero(н == len(биты_образца)) if инверсия else [])):
            for б in list(места)[:предел]:
                if любой_сдвиг or б % 8 == 0:
                    найдено.append({"бит": int(б), "сдвиг": int(б) % 8, "инверсия": инв})
    найдено.sort(key=lambda н: (н["бит"], н["инверсия"]))
    return найдено[:предел]


def контекст(биты: np.ndarray, бит: int, инверсия: bool, до: int = 16, после: int = 32) -> Dict[str, str]:
    """Байты вокруг находки (в её же выравнивании): HEX и печатный вид."""
    начало = max(бит % 8, бит - до * 8)
    ряд = биты[начало:бит + после * 8]
    ряд = ряд[:len(ряд) // 8 * 8]
    if инверсия:
        ряд = ряд ^ 1
    байты = np.packbits(ряд).tobytes()
    отступ = (бит - начало) // 8
    return {"до": байты[:отступ].hex(" "), "после": байты[отступ:].hex(" "),
            "текст": "".join(chr(б) if 32 <= б < 127 else "·" for б in байты), "отступ": str(отступ)}


# -- сигнатуры файлов ---------------------------------------------------------------------------

@dataclass
class Формат:
    имя: str
    расширение: str
    подпись: bytes
    проверка: Callable[[bytes, int], Optional[int]]   # длина файла, 0 — неизвестна, None — не он


def _jpeg(д: bytes, м: int) -> Optional[int]:
    if м + 4 > len(д) or not (0xE0 <= д[м + 3] <= 0xEF or д[м + 3] in (0xDB, 0xC0, 0xC2, 0xC4, 0xDD, 0xFE)):
        return None
    # Сегменты с длиной до SOS: у настоящего JPEG длины сегментов складываются.
    место = м + 2
    for _ in range(64):
        if место + 4 > len(д) or д[место] != 0xFF:
            return None
        маркер = д[место + 1]
        if маркер == 0xDA:                                   # SOS — дальше сжатые данные до EOI
            конец = д.find(b"\xff\xd9", место + 4, м + ФАЙЛ_ДО)
            return конец + 2 - м if конец >= 0 else 0
        длина = struct.unpack_from(">H", д, место + 2)[0]
        if длина < 2:
            return None
        место += 2 + длина
    return None


def _png(д: bytes, м: int) -> Optional[int]:
    if д[м + 8:м + 16] != b"\x00\x00\x00\x0dIHDR":
        return None
    if zlib.crc32(д[м + 12:м + 29]) != struct.unpack_from(">I", д, м + 29)[0]:
        return None
    место = м + 8
    while место + 12 <= len(д) and место - м < ФАЙЛ_ДО:
        длина = struct.unpack_from(">I", д, место)[0]
        вид = д[место + 4:место + 8]
        место += 12 + длина
        if вид == b"IEND":
            return место - м
        if not вид.isalpha():
            return None
    return 0


def _gif(д: bytes, м: int) -> Optional[int]:
    if м + 13 > len(д):
        return None
    ширина, высота, флаги = struct.unpack_from("<HHB", д, м + 6)
    if not ширина or not высота:
        return None
    место = м + 13 + (3 << ((флаги & 7) + 1) if флаги & 0x80 else 0)
    for _ in range(100000):
        if место >= len(д):
            return 0
        вид = д[место]
        if вид == 0x3B:
            return место + 1 - м
        if вид == 0x21:
            место += 2
        elif вид == 0x2C:
            if место + 10 > len(д):
                return 0
            флаги_к = д[место + 9]
            место += 10 + (3 << ((флаги_к & 7) + 1) if флаги_к & 0x80 else 0) + 1
        else:
            return None
        while место < len(д) and д[место]:                 # подблоки до нулевого
            место += 1 + д[место]
        место += 1
    return 0


def _pdf(д: bytes, м: int) -> Optional[int]:
    if not re.match(rb"%PDF-\d\.\d", д[м:м + 8]):
        return None
    следующий = д.find(b"%PDF-", м + 5, м + ФАЙЛ_ДО)
    граница = следующий if следующий >= 0 else min(len(д), м + ФАЙЛ_ДО)
    конец = д.rfind(b"%%EOF", м, граница)
    return конец + 5 - м if конец >= 0 else 0


def _zip(д: bytes, м: int) -> Optional[int]:
    if м + 30 > len(д) or struct.unpack_from("<H", д, м + 4)[0] > 63:
        return None
    имя_дл = struct.unpack_from("<H", д, м + 26)[0]
    if not 0 < имя_дл < 1024:
        return None
    конец = д.find(b"PK\x05\x06", м, м + ФАЙЛ_ДО)
    if конец < 0 or конец + 22 > len(д):
        return 0
    return конец + 22 + struct.unpack_from("<H", д, конец + 20)[0] - м


def _zip_вид(д: bytes, м: int) -> str:
    """Что внутри ZIP: docx/xlsx/pptx/odt/jar/apk — по именам в каталоге."""
    окно = д[м:м + 1 << 16]
    for метка, расширение in ((b"word/", "docx"), (b"xl/", "xlsx"), (b"ppt/", "pptx"),
                              (b"META-INF/MANIFEST.MF", "jar"), (b"AndroidManifest.xml", "apk"),
                              (b"mimetypeapplication/vnd.oasis.opendocument.text", "odt")):
        if метка in окно:
            return расширение
    return "zip"


def _7z(д: bytes, м: int) -> Optional[int]:
    if м + 32 > len(д) or д[м + 6] != 0:
        return None
    смещение, размер = struct.unpack_from("<QQ", д, м + 12)
    if zlib.crc32(д[м + 12:м + 32]) != struct.unpack_from("<I", д, м + 8)[0]:
        return None
    return 32 + смещение + размер if 32 + смещение + размер <= ФАЙЛ_ДО else 0


def _распаковка(распаковщик) -> Callable[[bytes, int], Optional[int]]:
    def проверка(д: bytes, м: int) -> Optional[int]:
        р = распаковщик()
        try:
            р.decompress(д[м:м + ФАЙЛ_ДО], 1 << 26)
        except (OSError, EOFError, ValueError, zlib.error, lzma.LZMAError):
            return None
        if not getattr(р, "eof", False):
            return 0
        return len(д[м:м + ФАЙЛ_ДО]) - len(р.unused_data)
    return проверка


def _gzip(д: bytes, м: int) -> Optional[int]:
    if м + 10 > len(д) or д[м + 3] & 0xE0:
        return None
    return _распаковка(lambda: zlib.decompressobj(31))(д, м)


def _bmp(д: bytes, м: int) -> Optional[int]:
    if м + 30 > len(д):
        return None
    размер, резерв, данные, заголовок = struct.unpack_from("<IIII", д, м + 2)
    if резерв or not 54 <= размер <= ФАЙЛ_ДО or not 26 <= данные < размер or заголовок not in (12, 40, 52, 56, 108, 124):
        return None
    return размер


def _riff(д: bytes, м: int) -> Optional[int]:
    if м + 12 > len(д) or д[м + 8:м + 12] not in (b"WAVE", b"AVI ", b"WEBP", b"RMID"):
        return None
    размер = struct.unpack_from("<I", д, м + 4)[0] + 8
    return размер if размер <= ФАЙЛ_ДО else 0


def _ogg(д: bytes, м: int) -> Optional[int]:
    место = м
    for _ in range(1 << 16):
        if д[место:место + 4] != b"OggS" or место + 27 > len(д) or д[место + 4] != 0:
            return None if место == м else 0
        флаги, сегментов = д[место + 5], д[место + 26]
        место += 27 + сегментов + sum(д[место + 27:место + 27 + сегментов])
        if флаги & 0x04:
            return место - м
    return 0


def _mp4(д: bytes, м: int) -> Optional[int]:
    # Подпись «ftyp» стоит на смещении 4: м указывает на неё, файл — с м − 4.
    начало = м - 4
    if начало < 0 or not 8 <= struct.unpack_from(">I", д, начало)[0] <= 256:
        return None
    место = начало
    for _ in range(4096):
        if место + 8 > len(д):
            break
        размер, вид = struct.unpack_from(">I", д, место)[0], д[место + 4:место + 8]
        if not re.fullmatch(rb"[a-zA-Z0-9 ]{4}", вид):
            break
        if размер == 1 and место + 16 <= len(д):
            размер = struct.unpack_from(">Q", д, место + 8)[0]
        if размер < 8:
            break
        место += размер
    return место - начало if место > начало + 8 else None


def _elf(д: bytes, м: int) -> Optional[int]:
    if м + 64 > len(д) or д[м + 4] not in (1, 2) or д[м + 5] not in (1, 2) or д[м + 6] != 1:
        return None
    порядок = "<" if д[м + 5] == 1 else ">"
    if д[м + 4] == 1:
        shoff, = struct.unpack_from(порядок + "I", д, м + 32)
        shentsize, shnum = struct.unpack_from(порядок + "HH", д, м + 46)
    else:
        shoff, = struct.unpack_from(порядок + "Q", д, м + 40)
        shentsize, shnum = struct.unpack_from(порядок + "HH", д, м + 58)
    длина = shoff + shentsize * shnum
    return длина if 64 <= длина <= ФАЙЛ_ДО else 0


def _pe(д: bytes, м: int) -> Optional[int]:
    if м + 64 > len(д):
        return None
    pe = struct.unpack_from("<I", д, м + 0x3C)[0]
    if not 64 <= pe < 4096 or д[м + pe:м + pe + 4] != b"PE\0\0":
        return None
    секций, = struct.unpack_from("<H", д, м + pe + 6)
    опц, = struct.unpack_from("<H", д, м + pe + 20)
    таблица = м + pe + 24 + опц
    длина = 0
    for i in range(min(секций, 96)):
        с = таблица + 40 * i
        if с + 40 > len(д):
            return 0
        размер, начало = struct.unpack_from("<II", д, с + 16)
        длина = max(длина, начало + размер)
    return длина if длина <= ФАЙЛ_ДО else 0


def _sqlite(д: bytes, м: int) -> Optional[int]:
    if м + 32 > len(д):
        return None
    страница, = struct.unpack_from(">H", д, м + 16)
    страница = 65536 if страница == 1 else страница
    if страница < 512 or страница & (страница - 1):
        return None
    длина = страница * struct.unpack_from(">I", д, м + 28)[0]
    return длина if длина <= ФАЙЛ_ДО else 0


def _der(д: bytes, м: int) -> Optional[int]:
    """Сертификат X.509 в DER: SEQUENCE { SEQUENCE (tbs) … } с согласованными длинами."""
    if м + 8 > len(д):
        return None
    внешняя = struct.unpack_from(">H", д, м + 2)[0]
    if д[м + 4:м + 6] != b"\x30\x82":
        return None
    внутренняя = struct.unpack_from(">H", д, м + 6)[0]
    if not 64 <= внутренняя < внешняя <= 1 << 15:
        return None
    return 4 + внешняя


def _без_длины(проверка: Callable[[bytes, int], bool]) -> Callable[[bytes, int], Optional[int]]:
    return lambda д, м: 0 if проверка(д, м) else None


ФОРМАТЫ: List[Формат] = [
    Формат("JPEG", "jpg", b"\xff\xd8\xff", _jpeg),
    Формат("PNG", "png", b"\x89PNG\r\n\x1a\n", _png),
    Формат("GIF", "gif", b"GIF87a", _gif),
    Формат("GIF", "gif", b"GIF89a", _gif),
    Формат("PDF", "pdf", b"%PDF-", _pdf),
    Формат("ZIP", "zip", b"PK\x03\x04", _zip),
    Формат("RAR 4", "rar", b"Rar!\x1a\x07\x00", _без_длины(lambda д, м: True)),
    Формат("RAR 5", "rar", b"Rar!\x1a\x07\x01\x00", _без_длины(lambda д, м: True)),
    Формат("7-Zip", "7z", b"7z\xbc\xaf\x27\x1c", _7z),
    Формат("GZIP", "gz", b"\x1f\x8b\x08", _gzip),
    Формат("BZIP2", "bz2", b"BZh", lambda д, м: _распаковка(bz2.BZ2Decompressor)(д, м)
           if м + 10 <= len(д) and 0x31 <= д[м + 3] <= 0x39 and д[м + 4:м + 10] == b"\x31\x41\x59\x26\x53\x59" else None),
    Формат("XZ", "xz", b"\xfd7zXZ\x00", _распаковка(lambda: lzma.LZMADecompressor(lzma.FORMAT_XZ))),
    Формат("BMP", "bmp", b"BM", _bmp),
    Формат("TIFF", "tif", b"II*\x00", _без_длины(lambda д, м: 8 <= struct.unpack_from("<I", д, м + 4)[0] < 1 << 26
                                                    if м + 8 <= len(д) else False)),
    Формат("TIFF", "tif", b"MM\x00*", _без_длины(lambda д, м: 8 <= struct.unpack_from(">I", д, м + 4)[0] < 1 << 26
                                                    if м + 8 <= len(д) else False)),
    Формат("RIFF", "riff", b"RIFF", _riff),
    Формат("Ogg", "ogg", b"OggS", _ogg),
    Формат("FLAC", "flac", b"fLaC", _без_длины(lambda д, м: м + 8 <= len(д) and д[м + 4] & 0x7F == 0)),
    Формат("MP3 (ID3)", "mp3", b"ID3", _без_длины(lambda д, м: м + 10 <= len(д) and 2 <= д[м + 3] <= 4
                                                   and all(б < 0x80 for б in д[м + 6:м + 10]))),
    Формат("MP4/MOV", "mp4", b"ftyp", _mp4),
    Формат("ELF", "elf", b"\x7fELF", _elf),
    Формат("PE (EXE/DLL)", "exe", b"MZ", _pe),
    Формат("SQLite", "sqlite", b"SQLite format 3\x00", _sqlite),
    Формат("OLE2 (DOC/XLS/MSG)", "ole", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", _без_длины(lambda д, м: True)),
    Формат("Сертификат X.509 (DER)", "der", b"\x30\x82", _der),
    Формат("pcap", "pcap", b"\xd4\xc3\xb2\xa1", _без_длины(lambda д, м: м + 24 <= len(д) and д[м + 4:м + 6] == b"\x02\x00")),
    Формат("pcapng", "pcapng", b"\x0a\x0d\x0d\x0a", _без_длины(lambda д, м: д[м + 8:м + 12] in (b"\x4d\x3c\x2b\x1a",
                                                                                               b"\x1a\x2b\x3c\x4d"))),
    Формат("XML", "xml", b"<?xml ", _без_длины(lambda д, м: True)),
]


def сигнатуры(биты: np.ndarray, *, любой_сдвиг: bool = True, инверсия: bool = False,
              предел: int = 500) -> List[Dict[str, object]]:
    """Файлы в потоке: подпись и структура сошлись. Позиция — в битах; длина — если известна."""
    найдено: List[Dict[str, object]] = []
    for сдвиг, инв, вид in виды(биты, сдвиги=range(8) if любой_сдвиг else (0,), инверсия=инверсия):
        for формат in ФОРМАТЫ:
            for место in _все(вид, формат.подпись, 20000):
                try:
                    длина = формат.проверка(вид, место)
                except (struct.error, IndexError):
                    длина = None
                if длина is None:
                    continue
                начало = место - 4 if формат.имя == "MP4/MOV" else место
                расширение = _zip_вид(вид, место) if формат.имя == "ZIP" else формат.расширение
                найдено.append({"что": формат.имя, "расширение": расширение, "бит": 8 * начало + сдвиг,
                                "сдвиг": сдвиг, "инверсия": инв, "байт": начало, "длина": int(длина)})
                if len(найдено) >= предел:
                    break
    # Вложенные подписи (JPEG внутри ZIP без сжатия, миниатюра в JPEG) оставляем:
    # аналитику важно и то, и другое. Повторы одной находки — нет.
    найдено.sort(key=lambda н: (н["бит"], н["что"]))
    итог, было = [], set()
    for н in найдено:
        ключ = (н["бит"], н["что"], н["инверсия"])
        if ключ not in было:
            было.add(ключ)
            итог.append(н)
    return итог[:предел]


def вырезать(биты: np.ndarray, бит: int, длина: int, инверсия: bool = False) -> bytes:
    """Байты файла с этой битовой позиции (длина 0 — до 1 МБ: конец неизвестен)."""
    длина = длина or (1 << 20)
    ряд = биты[бит:бит + min(длина, ФАЙЛ_ДО) * 8]
    ряд = ряд[:len(ряд) // 8 * 8]
    return np.packbits(ряд ^ 1 if инверсия else ряд).tobytes()


# -- строки и имена ------------------------------------------------------------------------------

РАСШИРЕНИЯ = ("jpg|jpeg|png|gif|bmp|tif|tiff|webp|pdf|doc|docx|xls|xlsx|ppt|pptx|odt|rtf|txt|csv|xml|html|"
              "htm|json|zip|rar|7z|gz|tgz|tar|bz2|xz|exe|dll|sys|bin|dat|img|iso|mp3|wav|ogg|flac|avi|mp4|"
              "mkv|mov|wmv|3gp|amr|sig|pcap|pcapng|jar|apk|db|sqlite|cfg|ini|conf|log|key|pem|crt|cer|p12")
ИМЯ_ФАЙЛА = re.compile(r"[\w\-() ]{1,80}\.(?:" + РАСШИРЕНИЯ + r")\b", re.I | re.U)
АДРЕС = re.compile(r"(?:https?|ftp)://[^\s\"'<>]{3,200}|[\w.+-]{1,64}@[\w-]+(?:\.[\w-]+)+|"
                   r"\b(?:\d{1,3}\.){3}\d{1,3}\b", re.U)


def похоже_на_текст(текст: str) -> bool:
    """Отсеять «строки» из случайных байт.

    Случайные байты в CP1251 — наполовину буквы (0xC0–0xFF), и прописных там
    столько же, сколько строчных; в ASCII — вперемешку со знаками. У текста
    почти всё — буквы, цифры, пробелы и обычная пунктуация, а среди букв
    преобладают строчные (или это короткий идентификатор заглавными).
    """
    без_пробелов = текст.replace(" ", "")
    if not без_пробелов:
        return False
    обычные = sum(1 for ч in текст if ч.isalnum() or ч in " .,:;/\\-_()@'\"!?=&%#+\t")
    if обычные < 0.9 * len(текст):
        return False
    буквы = [ч for ч in без_пробелов if ч.isalpha()]
    if len(буквы) < 0.5 * len(без_пробелов):
        return False
    # Знаков препинания в тексте немного.
    if sum(1 for ч in без_пробелов if not ч.isalnum()) > 0.25 * len(без_пробелов):
        return False
    # Форма слов: в тексте слово — строчными, с заглавной или целиком заглавными,
    # и одним письмом; у случайных байт регистр и письмо скачут внутри слова.
    годных = 0
    for слово in re.findall(r"[^\W\d_]+", текст):
        if len(слово) < 3:
            continue
        одно_письмо = all("а" <= ч.lower() <= "я" or ч in "ёЁ" for ч in слово) or слово.isascii()
        форма = слово.islower() or слово.isupper() or (слово[0].isupper() and слово[1:].islower())
        if одно_письмо and форма:
            годных += len(слово)
    return годных >= 4 and годных >= 0.6 * len(буквы)


def строки(биты: np.ndarray, *, наименьшая: int = 8, сдвиги: Sequence[int] = (0,), инверсия: bool = False,
           предел: int = 3000) -> Dict[str, object]:
    """Текстовые фрагменты (ASCII, CP1251, UTF-8, UTF-16LE), имена файлов, адреса."""
    найдено: List[Dict[str, object]] = []
    шаблоны = [
        ("ASCII", re.compile(rb"[\x20-\x7e\t]{%d,}" % наименьшая), "ascii"),
        ("UTF-8", re.compile(rb"(?:[\x20-\x7e]|[\xd0\xd1][\x80-\xbf]){%d,}" % наименьшая), "utf-8"),
        ("CP1251", re.compile(rb"(?:[\x20-\x7e]|[\xc0-\xff\xa8\xb8]){%d,}" % наименьшая), "cp1251"),
        ("UTF-16LE", re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % наименьшая), "utf-16-le"),
    ]
    for сдвиг, инв, вид in виды(биты, сдвиги=сдвиги, инверсия=инверсия):
        занято: List[Tuple[int, int]] = []
        for имя, шаблон, кодировка in шаблоны:
            for м in шаблон.finditer(вид):
                if len(найдено) >= предел:
                    break
                текст = м.group().decode(кодировка, "replace")
                if имя in ("UTF-8", "CP1251") and len(текст) < max(10, наименьшая):
                    continue
                if имя in ("UTF-8", "CP1251") and not re.search(r"[а-яёА-ЯЁ]{3,}", текст):
                    # Только с кириллическим словом: чистый ASCII уже найден первым
                    # шаблоном, а одиночные старшие байты в CP1251 — не текст.
                    continue
                if any(а <= м.start() < б for а, б in занято) and имя != "UTF-16LE":
                    continue
                if not похоже_на_текст(текст):
                    continue
                занято.append((м.start(), м.end()))
                найдено.append({"бит": 8 * м.start() + сдвиг, "сдвиг": сдвиг, "инверсия": инв,
                                "кодировка": имя, "текст": текст[:400], "длина": len(м.group())})
    найдено.sort(key=lambda н: н["бит"])
    имена, адреса = [], []
    for н in найдено:
        for м in ИМЯ_ФАЙЛА.finditer(н["текст"]):
            имена.append({"имя": м.group().strip(), "бит": н["бит"]})
        for м in АДРЕС.finditer(н["текст"]):
            адреса.append({"адрес": м.group(), "бит": н["бит"]})
    return {"строки": найдено, "имена_файлов": имена[:500], "адреса": адреса[:500]}


# -- частые комбинации ----------------------------------------------------------------------------

def частые(биты: np.ndarray, *, от: int = 2, до: int = 8, сдвиги: Sequence[int] = (0,),
           лучших: int = 40, выборка: int = 8 << 20) -> List[Dict[str, object]]:
    """Повторяющиеся комбинации от 2 до 8 байт — и блоки, которые из них вырастают.

    Комбинация берётся, если встречается заметно чаще случайного (с учётом
    частот самих байтов). Затем она расширяется влево и вправо, пока байты
    совпадают почти во всех вхождениях, — получается весь повторяющийся
    блок: маркер, заголовок с постоянными полями, синхрослово. Для блока —
    сколько раз, во сколько раз чаще случайного, типичный шаг между
    вхождениями и доля вхождений с этим шагом (регулярный шаг — кадр).
    """
    итог: List[Dict[str, object]] = []
    for сдвиг, _, вид in виды(биты, сдвиги=сдвиги):
        д = np.frombuffer(вид[:выборка], dtype=np.uint8)
        if len(д) < 64:
            continue
        частоты = np.bincount(д, minlength=256) / len(д)
        кандидаты = []
        for n in range(max(2, от), min(8, до) + 1):
            окна = np.lib.stride_tricks.sliding_window_view(д, n)
            ключи = np.zeros(len(окна), dtype=np.uint64)
            for i in range(n):
                ключи = (ключи << np.uint64(8)) | окна[:, i].astype(np.uint64)
            значения, сколько = np.unique(ключи, return_counts=True)
            for j in np.argsort(-сколько)[:200]:
                раз = int(сколько[j])
                if раз < 4:
                    break
                комбинация = int(значения[j]).to_bytes(n, "big")
                if len(set(комбинация)) == 1:
                    continue                          # 00 00 00…, FF FF… — заполнение, не маркер
                ждём = (len(д) - n + 1) * float(np.prod([частоты[б] for б in комбинация]))
                if раз / max(ждём, 1e-9) < 8:
                    continue
                кандидаты.append((раз * n, n, комбинация, np.flatnonzero(ключи == значения[j]), ждём))
        кандидаты.sort(key=lambda к: -к[0])
        покрыто = np.zeros(len(д), dtype=bool)
        for _, n, комбинация, места, ждём in кандидаты:
            if покрыто[места + n // 2].mean() > 0.5:
                continue
            лево, право = 0, n
            while лево < 64 and места.min() - лево - 1 >= 0:
                б = д[места - лево - 1]
                if np.bincount(б).max() < 0.9 * len(места):
                    break
                лево += 1
            while право < 128 and места.max() + право < len(д):
                б = д[места + право]
                if np.bincount(б).max() < 0.9 * len(места):
                    break
                право += 1
            начала = места - лево
            длина = лево + право
            блок = bytes(np.bincount(д[начала + k]).argmax() for k in range(длина))
            for н in начала:
                покрыто[н:н + длина] = True
            шаги = np.diff(места)
            шаг, доля_шага = 0, 0.0
            if len(шаги):
                вар, чис = np.unique(шаги, return_counts=True)
                шаг, доля_шага = int(вар[np.argmax(чис)]), float(чис.max() / len(шаги))
            итог.append({"hex": блок[:48].hex(" ") + (" …" if длина > 48 else ""),
                         "текст": "".join(chr(б) if 32 <= б < 127 else "·" for б in блок[:48]),
                         "байт": длина, "раз": len(места), "во_сколько": round(len(места) / max(ждём, 1e-9), 1),
                         "шаг": шаг, "доля_шага": round(доля_шага, 3), "сдвиг": сдвиг,
                         "первый_бит": 8 * int(начала[0]) + сдвиг, "ядро": комбинация.hex(" ")})
            if len(итог) >= лучших:
                break
    итог.sort(key=lambda з: -(з["раз"] * min(з["байт"], 16)))
    return итог[:лучших]
