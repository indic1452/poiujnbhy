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
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

#: Сколько байт вида просматривать (на каждый из восьми сдвигов).
ВИД_ДО = 32 << 20
#: Самый большой вырезаемый файл.
ФАЙЛ_ДО = 64 << 20
НАХОДОК_ДО = 2000


# -- виды потока --------------------------------------------------------------------------

def виды(биты: np.ndarray, *, сдвиги: Sequence[int] = range(8), инверсия: bool = False
         ) -> list[tuple[int, bool, bytes]]:
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


def образец(текст: str, вид: str, кодировка: str = "utf-8") -> tuple[bytes | None, np.ndarray]:
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


def _все(данные: bytes, что: bytes, предел: int) -> list[int]:
    итог, место = [], данные.find(что)
    while место >= 0 and len(итог) < предел:
        итог.append(место)
        место = данные.find(что, место + 1)
    return итог


def найти_образец(биты: np.ndarray, байты: bytes | None, биты_образца: np.ndarray, *,
                  любой_сдвиг: bool = True, инверсия: bool = False,
                  предел: int = НАХОДОК_ДО) -> list[dict[str, object]]:
    """Все вхождения: позиция в битах, сдвиг, инверсия. Контекст — позже, по позиции."""
    найдено: list[dict[str, object]] = []
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


def контекст(биты: np.ndarray, бит: int, инверсия: bool, до: int = 16, после: int = 32) -> dict[str, str]:
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
    проверка: Callable[[bytes, int], int | None]   # длина файла, 0 — неизвестна, None — не он
    #: Где подпись от начала файла (MP4 — 4, tar — 257, ISO 9660 — 32769); проверке дают место подписи.
    сдвиг: int = 0
    #: Расширение по содержимому (DOCX/XLSX в ZIP, DOC/XLS в OLE, WAV/AVI в RIFF): (данные, начало файла) → расширение.
    вид: Callable[[bytes, int], str] | None = None


def _jpeg(д: bytes, м: int) -> int | None:
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


def _png(д: bytes, м: int) -> int | None:
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


def _gif(д: bytes, м: int) -> int | None:
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


def _pdf(д: bytes, м: int) -> int | None:
    if not re.match(rb"%PDF-\d\.\d", д[м:м + 8]):
        return None
    следующий = д.find(b"%PDF-", м + 5, м + ФАЙЛ_ДО)
    граница = следующий if следующий >= 0 else min(len(д), м + ФАЙЛ_ДО)
    try:
        конец = д.rindex(b"%%EOF", м, граница)
    except ValueError:                              # конца нет — длина неизвестна
        return 0
    # За маркером конца — перевод строки (CR LF, CR или LF): он ещё часть последней строки файла.
    конец += 5
    конец += 2 if д[конец:конец + 2] == b"\r\n" else 1 if д[конец:конец + 1] in (b"\r", b"\n") else 0
    return конец - м


def _zip(д: bytes, м: int) -> int | None:
    if м + 30 > len(д) or struct.unpack_from("<H", д, м + 4)[0] > 63:
        return None
    имя_дл = struct.unpack_from("<H", д, м + 26)[0]
    if not 0 < имя_дл < 1024:
        return None
    try:
        конец = д.index(b"PK\x05\x06", м, м + ФАЙЛ_ДО)
    except ValueError:                              # конца каталога нет — длина неизвестна
        return 0
    if конец + 22 > len(д):
        return 0
    # Начало архива — конец каталога минус его размер и смещение (zipfile._EndRecData: concat); у
    # заголовка члена в середине архива оно не сходится — это не отдельный ZIP. ZIP64 (0xFFFFFFFF) — без сверки.
    размер, смещение = struct.unpack_from("<II", д, конец + 12)
    if 0xFFFFFFFF not in (размер, смещение) and конец - размер - смещение != м:
        return None
    return конец + 22 + struct.unpack_from("<H", д, конец + 20)[0] - м


#: Первый член ZIP «mimetype» без сжатия и без доп. поля (длина имени 8 на смещении 26) — ODF и EPUB;
#: подвиды — как в libmagic Magdir/archive (vnd.oasis.opendocument.<вид>).
_ODF = ((b"application/vnd.oasis.opendocument.text", "odt"),
        (b"application/vnd.oasis.opendocument.spreadsheet", "ods"),
        (b"application/vnd.oasis.opendocument.presentation", "odp"),
        (b"application/vnd.oasis.opendocument.graphics", "odg"),
        (b"application/epub+zip", "epub"))
#: Office Open XML: [Content_Types].xml и каталог части — как в libmagic Magdir/msooxml.
_OOXML = ((b"word/", "docx"), (b"xl/", "xlsx"), (b"ppt/", "pptx"), (b"visio/", "vsdx"))


def _zip_вид(д: bytes, м: int) -> str:
    """Что внутри ZIP — по членам: ODF/EPUB по «mimetype», DOCX/XLSX/PPTX по [Content_Types].xml, JAR, APK."""
    окно = д[м:м + (1 << 16)]
    if окно[26:38] == b"\x08\x00\x00\x00mimetype":
        for метка, расширение in _ODF:
            if окно[38:38 + len(метка)] == метка:
                return расширение
    if b"[Content_Types].xml" in окно:
        for метка, расширение in _OOXML:
            if метка in окно:
                return расширение
    for метка, расширение in ((b"META-INF/MANIFEST.MF", "jar"), (b"AndroidManifest.xml", "apk")):
        if метка in окно:
            return расширение
    return "zip"


def _7z(д: bytes, м: int) -> int | None:
    if м + 32 > len(д) or д[м + 6] != 0:
        return None
    смещение, размер = struct.unpack_from("<QQ", д, м + 12)
    if zlib.crc32(д[м + 12:м + 32]) != struct.unpack_from("<I", д, м + 8)[0]:
        return None
    return 32 + смещение + размер if 32 + смещение + размер <= ФАЙЛ_ДО else 0


def _распаковка(распаковщик) -> Callable[[bytes, int], int | None]:
    def проверка(д: bytes, м: int) -> int | None:
        р = распаковщик()
        try:
            р.decompress(д[м:м + ФАЙЛ_ДО], 1 << 26)
        except (OSError, EOFError, ValueError, zlib.error, lzma.LZMAError):
            return None
        if not getattr(р, "eof", False):
            return 0
        return len(д[м:м + ФАЙЛ_ДО]) - len(р.unused_data)
    return проверка


def _gzip(д: bytes, м: int) -> int | None:
    if м + 10 > len(д) or д[м + 3] & 0xE0:
        return None
    return _распаковка(lambda: zlib.decompressobj(31))(д, м)


def _bmp(д: bytes, м: int) -> int | None:
    if м + 30 > len(д):
        return None
    размер, резерв, данные, заголовок = struct.unpack_from("<IIII", д, м + 2)
    if резерв or not 54 <= размер <= ФАЙЛ_ДО or not 26 <= данные < размер or заголовок not in (12, 40, 52, 56, 108, 124):
        return None
    return размер


def _riff(д: bytes, м: int) -> int | None:
    if м + 12 > len(д) or д[м + 8:м + 12] not in (b"WAVE", b"AVI ", b"WEBP", b"RMID"):
        return None
    размер = struct.unpack_from("<I", д, м + 4)[0] + 8
    return размер if размер <= ФАЙЛ_ДО else 0


def _ogg(д: bytes, м: int) -> int | None:
    место = м
    for _ in range(1 << 16):
        if д[место:место + 4] != b"OggS" or место + 27 > len(д) or д[место + 4] != 0:
            return None if место == м else 0
        флаги, сегментов = д[место + 5], д[место + 26]
        место += 27 + сегментов + sum(д[место + 27:место + 27 + сегментов])
        if флаги & 0x04:
            return место - м
    return 0


def _mp4(д: bytes, м: int) -> int | None:
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


def _elf(д: bytes, м: int) -> int | None:
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


def _pe(д: bytes, м: int) -> int | None:
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


def _sqlite(д: bytes, м: int) -> int | None:
    if м + 32 > len(д):
        return None
    страница, = struct.unpack_from(">H", д, м + 16)
    страница = 65536 if страница == 1 else страница
    if страница < 512 or страница & (страница - 1):
        return None
    длина = страница * struct.unpack_from(">I", д, м + 28)[0]
    return длина if длина <= ФАЙЛ_ДО else 0


def _der(д: bytes, м: int) -> int | None:
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


def _без_длины(проверка: Callable[[bytes, int], bool]) -> Callable[[bytes, int], int | None]:
    return lambda д, м: 0 if проверка(д, м) else None


def _итог(длина: int) -> int:
    """Длина файла, если он не больше вырезаемого; больше — «неизвестна» (0)."""
    return длина if длина <= ФАЙЛ_ДО else 0


# -- архивы: tar, RAR, CAB, Zstandard -------------------------------------------------------------

#: tar: заголовок 512 байт, «ustar» на смещении 257 (POSIX «ustar\0», GNU «ustar »), сумма — на 148 (8 байт,
#: восьмеричная; считается по заголовку с пробелами на её месте), длина — на 124 (12 байт: восьмеричная или
#: base-256 со старшим битом). Данные идут за заголовком блоками по 512 у обычных файлов, неизвестных видов и
#: служебных (длинное имя L/K, pax x/g/X); конец архива — два нулевых блока, дальше добивка до записи
#: 20 × 512 (Python tarfile.py: RECORDSIZE, calc_chksums, _proc_builtin; libmagic Magdir/archive).
TAR_БЛОК = 512
TAR_ЗАПИСЬ = 20 * 512
_TAR_БЕЗ_ДАННЫХ = frozenset(b"123456")          # жёсткая и символьная ссылки, устройства, каталог, FIFO


def _tar_число(поле: bytes) -> int | None:
    if поле[:1] in (b"\x80", b"\xff"):              # base-256 (GNU): старший бит первого байта
        значение = int.from_bytes(поле[1:], "big")
        return значение - 256 ** (len(поле) - 1) if поле[:1] == b"\xff" else значение
    текст = поле.partition(b"\x00")[0].strip()
    if not текст:
        return 0
    try:
        return int(текст, 8)
    except ValueError:
        return None


def _tar_заголовок(д: bytes, н: int) -> int | None:
    """Длина данных члена, если заголовок в ``н`` цел (сумма сошлась); иначе None."""
    заголовок = д[н:н + TAR_БЛОК]
    if len(заголовок) < TAR_БЛОК:
        return None
    сумма = _tar_число(заголовок[148:156])
    if сумма != 256 + sum(заголовок[:148]) + sum(заголовок[156:]):
        return None
    размер = _tar_число(заголовок[124:136])
    if размер is None or размер < 0:
        return None
    return 0 if заголовок[156] in _TAR_БЕЗ_ДАННЫХ else размер


def _tar(д: bytes, м: int) -> int | None:
    начало = м - 257
    if начало < 0 or д[м + 5:м + 6] not in (b"\x00", b" ") or _tar_заголовок(д, начало) is None:
        return None
    место = начало
    while место - начало < ФАЙЛ_ДО:
        блок = д[место:место + TAR_БЛОК]
        if len(блок) < TAR_БЛОК:
            return 0
        if not блок.strip(b"\x00"):
            if д[место + TAR_БЛОК:место + 2 * TAR_БЛОК].strip(b"\x00") or len(д) < место + 2 * TAR_БЛОК:
                return 0
            конец = место + 2 * TAR_БЛОК
            добивка = -(конец - начало) % TAR_ЗАПИСЬ
            if len(д) >= конец + добивка and not д[конец:конец + добивка].strip(b"\x00"):
                конец += добивка
            return _итог(конец - начало)
        размер = _tar_заголовок(д, место)
        if размер is None:
            return 0
        место += TAR_БЛОК + -(-размер // TAR_БЛОК) * TAR_БЛОК
    return 0


def _rar4(д: bytes, м: int) -> int | None:
    """RAR 1.5–4.x (libarchive archive_read_support_format_rar.c): блоки «CRC16, тип, флаги, размер»;
    флаг 0x8000 — за заголовком ADD_SIZE (у файла — упакованный размер), у файла с флагом 0x0100 — ещё
    старшие 32 бита размера на смещении 32; главный заголовок (0x73) сверяется CRC32 & 0xFFFF;
    конец — блок 0x7B. Зашифрованные заголовки (флаг главного 0x0080) не обойти — длина неизвестна."""
    место = м + 7
    while место - м < ФАЙЛ_ДО:
        первый = место == м + 7
        if место + 7 > len(д):
            return 0
        crc, вид, флаги, размер = struct.unpack_from("<HBHH", д, место)
        if размер < 7 or not 0x72 <= вид <= 0x7B:
            return None if первый else 0
        if первый and (вид != 0x73 or zlib.crc32(д[место + 2:место + размер]) & 0xFFFF != crc):
            return None
        if вид == 0x73 and флаги & 0x0080:
            return 0
        if вид in (0x74, 0x7A):                     # файл и служебный: упакованный размер (+ старшие 32 бита)
            if флаги & 0x0100 and struct.unpack_from("<I", д, место + 32)[0]:
                return 0                            # больше 4 ГиБ — больше вырезаемого
            данные = struct.unpack_from("<I", д, место + 7)[0]
        else:
            данные = struct.unpack_from("<I", д, место + 7)[0] if флаги & 0x8000 else 0
        место += размер + данные
        if вид == 0x7B:
            return _итог(место - м)
    return 0


def _vint(д: bytes, м: int) -> tuple[int, int]:
    """Число RAR 5: по 7 бит от младших, старший бит — «дальше» (не больше 10 байт); (значение, байт)."""
    значение = 0
    for i in range(10):
        б = д[м + i]
        значение |= (б & 0x7F) << (7 * i)
        if not б & 0x80:
            return значение, i + 1
    raise ValueError("vint длиннее 10 байт")


def _rar5(д: bytes, м: int) -> int | None:
    """RAR 5 (libarchive archive_read_support_format_rar5.c, process_base_block): блок — CRC32 (над
    размером и заголовком), размер заголовка (vint), тип, флаги; флаг 1 — размер доп. области, флаг 2 —
    размер данных за заголовком; конец — тип 5, зашифрованные заголовки — тип 4 (длина неизвестна)."""
    место = м + 8
    while место - м < ФАЙЛ_ДО:
        первый = место == м + 8
        try:                                        # короткие данные — IndexError у _vint
            размер, n = _vint(д, место + 4)
            if место + 4 + n + размер > len(д):
                return 0
            if zlib.crc32(д[место + 4:место + 4 + n + размер]) != struct.unpack_from("<I", д, место)[0]:
                return None if первый else 0
            вид, k = _vint(д, место + 4 + n)
            флаги, j = _vint(д, место + 4 + n + k)
            поле = место + 4 + n + k + j
            if флаги & 1:
                поле += _vint(д, поле)[1]
            данные = _vint(д, поле)[0] if флаги & 2 else 0
        except (IndexError, ValueError):
            return 0
        if вид == 4:
            return 0
        место += 4 + n + размер + данные
        if вид == 5:
            return _итог(место - м)
    return 0


def _cab(д: bytes, м: int) -> int | None:
    """CAB (CFHEADER — libarchive archive_read_support_format_cab.c): cbCabinet на 8 — длина всего файла,
    версия 1.3 (байты 25 и 24), папок и файлов (на 26 и 28) — не ноль."""
    if м + 36 > len(д):
        return None
    размер = struct.unpack_from("<I", д, м + 8)[0]
    папок, файлов = struct.unpack_from("<HH", д, м + 26)
    if д[м + 24:м + 26] != b"\x03\x01" or not папок or not файлов or размер < 36:
        return None
    return _итог(размер)


def _zstd(д: bytes, м: int) -> int | None:
    """Кадр Zstandard (RFC 8878; facebook/zstd doc/zstd_compression_format.md): дескриптор заголовка
    (FCS-флаг — биты 7–6, одиночный сегмент — 5, резерв — 3 и ноль, сумма содержимого — 2, DID — 1–0),
    окно (если не одиночный сегмент), DID 0/1/2/4 байта, FCS 0(1)/2/4/8; блоки: 3 байта «от младшего»
    (последний — бит 0, вид — 1–2, размер — 3–23; RLE — один байт; вид 3 запрещён; не больше 128 КиБ);
    за последним — 4 байта суммы, если есть флаг."""
    if м + 6 > len(д) or д[м + 4] & 0x08:
        return None
    fhd = д[м + 4]
    одиночный = bool(fhd & 0x20)
    место = м + 5 + (0 if одиночный else 1) + (0, 1, 2, 4)[fhd & 3] + (int(одиночный), 2, 4, 8)[fhd >> 6]
    while место - м < ФАЙЛ_ДО:
        if место + 3 > len(д):
            return 0
        заголовок = д[место] | д[место + 1] << 8 | д[место + 2] << 16
        вид, размер = (заголовок >> 1) & 3, заголовок >> 3
        if вид == 3 or размер > 128 << 10:
            return None
        место += 3 + (1 if вид == 1 else размер)
        if заголовок & 1:
            return _итог(место - м + (4 if fhd & 0x04 else 0))
    return 0


# -- документы: OLE (DOC/XLS/PPT/MSG), RTF ------------------------------------------------------------

#: OLE2 (составной документ; заголовок — olefile.py, StructuredStorageHeader): порядок байт 0xFFFE на 0x1C,
#: сдвиг сектора на 0x1E (9 — 512 байт при версии 3, 12 — 4096 при версии 4 на 0x1A), число секторов FAT
#: на 0x2C, начало каталога — 0x30, начало и число секторов DIFAT — 0x44 и 0x48, первые 109 номеров
#: секторов FAT — с 0x4C. Сектор n лежит с (n + 1)·размер; свободный в FAT — 0xFFFFFFFF.
OLE_СВОБОДНЫЙ = 0xFFFFFFFF
OLE_КОНЕЦ = 0xFFFFFFFE
#: Потоки в каталоге, по которым назван документ (как в libmagic Magdir/ole2compounddocs: Word — WordDocument,
#: Excel — Workbook/Book, PowerPoint — «PowerPoint Document», Outlook — __substg1.0_…, Visio — VisioDocument).
OLE_ПОТОКИ = (("WordDocument", "doc"), ("Workbook", "xls"), ("Book", "xls"), ("PowerPoint Document", "ppt"),
              ("VisioDocument", "vsd"))


def _ole_сектор(д: bytes, м: int) -> int | None:
    if м + 512 > len(д) or struct.unpack_from("<H", д, м + 0x1C)[0] != 0xFFFE:
        return None
    версия, сдвиг = struct.unpack_from("<H", д, м + 0x1A)[0], struct.unpack_from("<H", д, м + 0x1E)[0]
    if (версия, сдвиг) not in ((3, 9), (4, 12)):
        return None
    return 1 << сдвиг


def _ole_fat(д: bytes, м: int, сектор: int) -> list[int] | None:
    """Номера секторов FAT: из заголовка и по цепочке DIFAT; None — сектор за концом данных."""
    всего = struct.unpack_from("<I", д, м + 0x2C)[0]
    номера = [н for н in struct.unpack_from("<109I", д, м + 0x4C) if н != OLE_СВОБОДНЫЙ][:всего]
    difat, осталось = struct.unpack_from("<II", д, м + 0x44)
    в_секторе = сектор // 4
    while len(номера) < всего and осталось and difat < OLE_КОНЕЦ:
        место = м + (difat + 1) * сектор
        if место + сектор > len(д):
            return None
        записи = struct.unpack_from(f"<{в_секторе}I", д, место)
        номера += [н for н in записи[:-1] if н != OLE_СВОБОДНЫЙ][:всего - len(номера)]
        difat, осталось = записи[-1], осталось - 1
    return номера


def _ole(д: bytes, м: int) -> int | None:
    сектор = _ole_сектор(д, м)
    if сектор is None:
        return None
    номера = _ole_fat(д, м, сектор)
    if номера is None:                              # пустой список — ниже «последний» не найдётся: тоже 0
        return 0
    в_секторе, последний = сектор // 4, None
    for i, н in enumerate(номера):
        место = м + (н + 1) * сектор
        if место + сектор > len(д):
            return 0
        записи = struct.unpack_from(f"<{в_секторе}I", д, место)
        занятые = [j for j, з in enumerate(записи) if з != OLE_СВОБОДНЫЙ]
        if занятые:                                 # сектор FAT i описывает секторы с i·в_секторе
            последний = i * в_секторе + занятые[-1]
    return 0 if последний is None else _итог((последний + 2) * сектор)


def _ole_вид(д: bytes, м: int) -> str:
    """DOC, XLS, PPT, MSG или VSD — по именам потоков в каталоге (цепочка секторов по FAT)."""
    сектор = _ole_сектор(д, м)
    номера = _ole_fat(д, м, сектор) if сектор else None
    if not номера:
        return "ole"
    в_секторе = сектор // 4

    def следующий(н: int) -> int:                   # номер FAT за концом — IndexError, конец цепочки
        return struct.unpack_from("<I", д, м + (номера[н // в_секторе] + 1) * сектор + 4 * (н % в_секторе))[0]

    # Записи каталога по 128 байт: имя UTF-16 до 64 байт с нулём в конце, длина имени — на 64 ([MS-CFB] 2.6).
    # Особые номера (конец цепочки, свободный) огромны: такой сектор всегда «за концом данных».
    имена, н, пройдено = set(), struct.unpack_from("<I", д, м + 0x30)[0], set()
    try:
        while н not in пройдено and м + (н + 2) * сектор <= len(д):
            пройдено.add(н)
            for з in range(м + (н + 1) * сектор, м + (н + 2) * сектор, 128):
                длина = struct.unpack_from("<H", д, з + 64)[0]
                имена.add(д[з:з + min(длина, 64)].decode("utf-16-le", "replace").rstrip("\x00"))
            н = следующий(н)
    except (struct.error, IndexError):
        pass
    for имя, расширение in OLE_ПОТОКИ:
        if имя in имена:
            return расширение
    return "msg" if any(и.startswith("__substg1.0_") for и in имена) else "ole"


def _rtf(д: bytes, м: int) -> int | None:
    """RTF: весь документ — группа «{\\rtf1 … }»; скобки \\{ \\} \\\\ экранированы, \\binN — N байт как есть
    (Microsoft RTF 1.9.1, группы и \\bin; подпись — libmagic Magdir/rtf)."""
    if not д[м + 5:м + 6].isdigit():
        return None
    глубина, место, конец = 0, м, min(len(д), м + ФАЙЛ_ДО)
    знаки = re.compile(rb"[{}]|\\bin(-?\d+) ?|\\.", re.S)
    while True:
        н = знаки.search(д, место, конец)
        if н is None:
            return 0
        место = н.end()
        if н.group(1) is not None:
            место += max(0, int(н.group(1)))
        elif н.group() == b"{":
            глубина += 1
        elif н.group() == b"}":
            глубина -= 1
            if not глубина:
                return место - м


# -- изображения, звук, видео: TIFF, RIFF, MP4, MP3, Matroska, FLV, MPEG-TS ------------------------

#: Размеры типов полей TIFF 6.0 (BYTE … DOUBLE; Wireshark file-tiff.c, tiff_type_len).
TIFF_ТИПЫ = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
#: Пары «смещения — длины» кусков изображения: полосы (273/279), плитки (324/325), JPEG (513/514).
TIFF_КУСКИ = ((273, 279), (324, 325), (513, 514))


def _tiff(д: bytes, м: int) -> int | None:
    """TIFF: IFD по цепочке (число записей, записи по 12 байт, смещение следующего), значения длиннее
    4 байт — по смещению, куски изображения — по парам «смещения, длины»; длина — дальний конец."""
    порядок = "<" if д[м] == 0x49 else ">"
    if м + 8 > len(д):
        return None
    ifd = struct.unpack_from(порядок + "I", д, м + 4)[0]
    if ifd < 8:
        return None
    конец, виденные = ifd, set()
    while ifd and ifd not in виденные:             # цепочка IFD — до нуля или до повтора
        виденные.add(ifd)
        if м + ifd + 2 > len(д):
            return 0
        записей = struct.unpack_from(порядок + "H", д, м + ifd)[0]
        if м + ifd + 6 + 12 * записей > len(д):
            return 0
        значения: dict[int, tuple[int, ...]] = {}
        for i in range(записей):
            тег, вид, число, поле = struct.unpack_from(порядок + "HHII", д, м + ifd + 2 + 12 * i)
            размер = TIFF_ТИПЫ.get(вид, 1) * число
            место = поле if размер > 4 else ifd + 2 + 12 * i + 8
            конец = max(конец, место + размер)
            if вид in (3, 4) and any(тег in пара for пара in TIFF_КУСКИ) and место + размер <= len(д) - м:
                значения[тег] = struct.unpack_from(f"{порядок}{число}{'H' if вид == 3 else 'I'}", д, м + место)
        for смещения, длины in TIFF_КУСКИ:
            for о, д_ in zip(значения.get(смещения, ()), значения.get(длины, ()), strict=False):
                конец = max(конец, о + д_)
        конец = max(конец, ifd + 6 + 12 * записей)
        ifd = struct.unpack_from(порядок + "I", д, м + ifd + 2 + 12 * записей)[0]
    return _итог(конец)


#: Вид RIFF по форме (байты 8–11): WAVE, AVI, WebP, MIDI (libmagic Magdir/riff).
RIFF_ВИДЫ = {b"WAVE": "wav", b"AVI ": "avi", b"WEBP": "webp", b"RMID": "rmi"}


def _riff_вид(д: bytes, м: int) -> str:
    return RIFF_ВИДЫ.get(д[м + 8:м + 12], "riff")


#: Марки ISO BMFF (байты 8–11 файла): QuickTime, M4A, 3GPP, HEIF, AVIF (libmagic Magdir/animation).
MP4_МАРКИ = ((b"qt  ", "mov"), (b"M4A ", "m4a"), (b"M4V ", "m4v"), (b"3gp", "3gp"), (b"3g2", "3g2"),
             (b"heic", "heic"), (b"heix", "heic"), (b"mif1", "heif"), (b"avif", "avif"))


def _mp4_вид(д: bytes, м: int) -> str:
    return next((р for метка, р in MP4_МАРКИ if д.startswith(метка, м + 8)), "mp4")


#: MPEG-аудио (ISO 11172-3, 13818-3; FFmpeg mpegaudiotabs.h и mpegaudiodecheader.c): скорости, кбит/с —
#: [MPEG-1 / MPEG-2 и 2.5][слой I, II, III][индекс 0–14]; частоты MPEG-1 (у MPEG-2 — вдвое, у 2.5 — вчетверо меньше).
MPA_СКОРОСТИ = (((0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
                 (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
                 (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)),
                ((0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
                 (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
                 (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)))
MPA_ЧАСТОТЫ = (44100, 48000, 32000)


def _mpa_кадр(д: bytes, м: int) -> int | None:
    """Длина кадра MPEG-аудио по заголовку (ff_mpa_check_header, ff_mpegaudio_decode_header); None — не кадр."""
    if м + 4 > len(д):
        return None
    з = struct.unpack_from(">I", д, м)[0]
    if (з & 0xFFE00000 != 0xFFE00000 or з & (3 << 19) == 1 << 19 or not з & (3 << 17)
            or з & (0xF << 12) in (0, 0xF << 12) or з & (3 << 10) == 3 << 10):
        return None
    if з & (1 << 20):
        lsf, mpeg25 = int(not з & (1 << 19)), 0
    else:
        lsf, mpeg25 = 1, 1
    слой = 4 - ((з >> 17) & 3)
    частота = MPA_ЧАСТОТЫ[(з >> 10) & 3] >> (lsf + mpeg25)
    скорость, добивка = MPA_СКОРОСТИ[lsf][слой - 1][(з >> 12) & 0xF], (з >> 9) & 1
    if слой == 1:
        return (скорость * 12000 // частота + добивка) * 4
    if слой == 2:
        return скорость * 144000 // частота + добивка
    return скорость * 144000 // (частота << lsf) + добивка


#: Сколько кадров подряд должно сойтись у MP3 без ID3 (одна пара байт синхронизации — не доказательство).
MPA_КАДРОВ = 4


def _mpa_кадры(д: bytes, место: int, наименьшее: int) -> int | None:
    """Конец цепочки кадров MPEG-аудио с ``место`` (и тега ID3v1 «TAG», 128 байт); None — кадров меньше нужного."""
    кадров = 0
    while True:
        длина = _mpa_кадр(д, место)                  # у допустимого заголовка — не меньше 24 байт
        if длина is None:
            break
        if место + длина > len(д):                      # оборванный кадр — заголовок его всё же сошёлся
            return 0 if кадров + 1 >= наименьшее else None
        место += длина
        кадров += 1
    if кадров < наименьшее:
        return None
    return место + (128 if д[место:место + 3] == b"TAG" and место + 128 <= len(д) else 0)


def _mp3_id3(д: bytes, м: int) -> int | None:
    """ID3v2 (ff_id3v2_match, ff_id3v2_tag_len): версия 2–4, размер — 4 байта по 7 бит, флаг 0x10 — ещё
    10 байт; за тегом — кадры MPEG-аудио."""
    if м + 10 > len(д) or not 2 <= д[м + 3] <= 4 or д[м + 4] == 0xFF or any(б & 0x80 for б in д[м + 6:м + 10]):
        return None
    размер = 10 + (д[м + 6] << 21 | д[м + 7] << 14 | д[м + 8] << 7 | д[м + 9]) + (10 if д[м + 5] & 0x10 else 0)
    if м + размер >= len(д):
        return 0
    конец = _mpa_кадры(д, м + размер, 1)
    return None if конец is None else _итог(конец - м) if конец else 0


def _mp3(д: bytes, м: int) -> int | None:
    конец = _mpa_кадры(д, м, MPA_КАДРОВ)
    return None if конец is None else _итог(конец - м) if конец else 0


def _ebml_число(д: bytes, м: int, id_: bool = False) -> tuple[int, int]:
    """VINT EBML (RFC 8794, 4): ширина — по нулям до бита-метки; у ID метка остаётся, у размера — снимается.
    Размер из одних единиц — «неизвестен» (−1)."""
    первый = д[м]
    if not первый:                                  # ширина больше 8 — не VINT
        raise ValueError("неверный VINT")
    ширина = 8 - первый.bit_length() + 1
    if len(д) < м + ширина:
        raise IndexError("VINT за концом")
    значение = int.from_bytes(д[м:м + ширина], "big")
    if id_:
        return значение, ширина
    значение &= (1 << (7 * ширина)) - 1
    return (-1 if значение == (1 << (7 * ширина)) - 1 else значение), ширина


def _ebml(д: bytes, м: int) -> int | None:
    """EBML (RFC 8794): заголовок EBML (ID 0x1A45DFA3) и за ним Segment (0x18538067, RFC 9559) с размером;
    длина — оба элемента целиком. Размер Segment «неизвестен» — длина неизвестна."""
    try:
        размер, ширина = _ebml_число(д, м + 4)
        if размер < 0:
            return None
        место = м + 4 + ширина + размер
        if место + 4 > len(д):
            return 0
        ид, ш_ид = _ebml_число(д, место, id_=True)
        if ид != 0x18538067:
            return None
        размер_с, ш_с = _ebml_число(д, место + ш_ид)
    except (ValueError, IndexError):
        return None
    return _итог(место + ш_ид + ш_с + размер_с - м) if размер_с >= 0 else 0


def _ebml_вид(д: bytes, м: int) -> str:
    """WebM или Matroska — по DocType (ID 0x4282) среди элементов заголовка EBML (RFC 8794, 11.2.6;
    libmagic Magdir/matroska)."""
    try:
        размер, ширина = _ebml_число(д, м + 4)
        место, конец = м + 4 + ширина, м + 4 + ширина + размер
        while место < конец:
            ид, ш_ид = _ebml_число(д, место, id_=True)
            длина, ш = _ebml_число(д, место + ш_ид)
            if ид == 0x4282:
                return "webm" if д[место + ш_ид + ш:место + ш_ид + ш + длина] == b"webm" else "mkv"
            место += ш_ид + ш + max(длина, 0)
    except (ValueError, IndexError):
        pass
    return "mkv"


def _flv(д: bytes, м: int) -> int | None:
    """FLV (Adobe FLV 10.1, прил. E; FFmpeg flvdec.c): заголовок «FLV», версия, флаги, смещение данных (≥ 9);
    PreviousTagSize0 = 0; метки: вид (8, 9, 18), размер (24 бита), время (24 + 8), поток 0, данные и
    PreviousTagSize = 11 + размер. Длина — до последней целой метки."""
    try:                                            # заголовок и PreviousTagSize0 за концом — не FLV
        флаги, смещение = struct.unpack_from(">BI", д, м + 4)
        нулевой = struct.unpack_from(">I", д, м + смещение)[0]
    except struct.error:
        return None
    if флаги & 0xFA or смещение < 9 or нулевой:
        return None
    место = м + смещение + 4
    while True:                                     # каждая метка — не меньше 15 байт: обход конечен
        if место == len(д):
            return _итог(место - м)
        if место + 11 > len(д):
            return 0
        вид, размер = д[место] & 0x1F, int.from_bytes(д[место + 1:место + 4], "big")
        if вид not in (8, 9, 18) or д[место + 8:место + 11] != b"\x00\x00\x00":
            return _итог(место - м)
        if место + 15 + размер > len(д):
            return 0
        if struct.unpack_from(">I", д, место + 11 + размер)[0] != 11 + размер:
            return _итог(место - м)
        место += 15 + размер


def _annex_b(первый: int, маска: int, следующий: int, далее: int = 512) -> Callable[[bytes, int], int | None]:
    """Поток NAL с кодами начала (ITU-T H.264, прил. B): за первым набором параметров — следующий (SPS → PPS,
    VPS → SPS) с кодом 00 00 01 в пределах ``далее`` байт. Длина — неизвестна (идёт до конца потока)."""
    def проверка(д: bytes, м: int) -> int | None:
        if д[м + 4] & 0x80 or д[м + 4] & маска != первый:
            return None
        try:
            место = д.index(b"\x00\x00\x01", м + 5, м + далее)
        except ValueError:
            return None
        return 0 if место + 3 < len(д) and д[место + 3] & маска == следующий else None
    return проверка


#: Пакет транспортного потока MPEG-2 (ISO/IEC 13818-1): 188 байт, синхробайт 0x47; подпись — начало PAT
#: (PID 0, начало блока), подтверждение — синхробайты пяти пакетов подряд (libmagic Magdir/animation).
TS_ПАКЕТ = 188
TS_ПАКЕТОВ = 5


def _ts(д: bytes, м: int) -> int | None:
    место = м
    while место < len(д) and д[место] == 0x47 and место - м < ФАЙЛ_ДО:
        место += TS_ПАКЕТ
    пакетов = (min(место, len(д)) - м) // TS_ПАКЕТ
    return пакетов * TS_ПАКЕТ if пакетов >= TS_ПАКЕТОВ else None


# -- исполняемые Mach-O, образы ISO 9660, PEM ---------------------------------------------------------

def _macho(д: bytes, м: int) -> int | None:
    """Mach-O (mach-o/loader.h): заголовок (magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags
    [, reserved у 64-бит]); длина — дальний конец сегментов LC_SEGMENT (0x1) / LC_SEGMENT_64 (0x19,
    fileoff и filesize) и подписи LC_CODE_SIGNATURE (0x1D, dataoff и datasize)."""
    магия = д[м:м + 4]
    порядок = ">" if магия[:3] == b"\xfe\xed\xfa" else "<"
    шире = магия in (b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")
    if м + 32 > len(д):
        return None
    команд, размер = struct.unpack_from(порядок + "II", д, м + 16)
    if not 0 < команд < 4096 or not 0 < размер < 1 << 24:
        return None
    место, концы = м + (32 if шире else 28), []
    for _ in range(команд):
        if место + 8 > len(д):
            return 0
        команда, длина = struct.unpack_from(порядок + "II", д, место)
        if длина < 8:
            return None
        if команда == 0x19 and место + 56 <= len(д):
            смещение, объём = struct.unpack_from(порядок + "QQ", д, место + 40)
            концы.append(смещение + объём)
        elif команда == 0x1 and место + 40 <= len(д):
            смещение, объём = struct.unpack_from(порядок + "II", д, место + 32)
            концы.append(смещение + объём)
        elif команда == 0x1D and место + 16 <= len(д):
            смещение, объём = struct.unpack_from(порядок + "II", д, место + 8)
            концы.append(смещение + объём)
        место += длина
    return _итог(max([место - м, *концы]))


def _macho_fat(д: bytes, м: int) -> int | None:
    """Универсальный Mach-O (mach-o/fat.h): 0xCAFEBABE, число архитектур < 20 (иначе это класс Java —
    libmagic Magdir/cafebabe), записи fat_arch по 20 байт: cputype, cpusubtype, offset, size, align."""
    if м + 8 > len(д):
        return None
    архитектур = struct.unpack_from(">I", д, м + 4)[0]
    if not 0 < архитектур < 20:
        return None
    концы = []
    for i in range(архитектур):
        if м + 8 + 20 * i + 20 > len(д):
            return 0
        смещение, размер = struct.unpack_from(">II", д, м + 16 + 20 * i)
        if смещение < 8 + 20 * архитектур:
            return None
        концы.append(смещение + размер)
    return _итог(max(концы))


def _iso(д: bytes, м: int) -> int | None:
    """ISO 9660 (ECMA-119; libarchive archive_read_support_format_iso9660.c): основной дескриптор тома в
    секторе 16 (байт 32768): вид 1, «CD001», версия 1; размер тома (в блоках) — на 80 (LE) и 84 (BE),
    размер блока — на 128 (LE) и 130 (BE); длина — их произведение."""
    начало = м - 32769
    if начало < 0 or д[м - 1] != 1 or м + 131 > len(д) or д[м + 5] != 1:
        return None
    блоков, блоков_be = struct.unpack_from("<I", д, м + 79)[0], struct.unpack_from(">I", д, м + 83)[0]
    блок, блок_be = struct.unpack_from("<H", д, м + 127)[0], struct.unpack_from(">H", д, м + 129)[0]
    if блоков != блоков_be or блок != блок_be or блок not in (512, 1024, 2048):
        return None
    return _итог(блоков * блок)


#: Текстовая обёртка PKIX (RFC 7468, 3, «lax»): метка из печатных знаков без дефиса по краям, тело base64.
PEM = re.compile(rb"-----BEGIN ((?:[\x21-\x2c\x2e-\x7e](?:[- ]?[\x21-\x2c\x2e-\x7e])*)?)-----"
                 rb"[\sA-Za-z0-9+/=]{0,1048576}?-----END \1-----[ \t]*(?:\r\n|\r|\n)?")


def _pem(д: bytes, м: int) -> int | None:
    н = PEM.match(д, м)
    return н.end() - м if н else None


def _pem_вид(д: bytes, м: int) -> str:
    """Расширение по метке: CERTIFICATE — crt, X509 CRL — crl, CERTIFICATE REQUEST — csr, ключи — key."""
    метка = PEM.match(д, м).group(1)
    return {b"CERTIFICATE": "crt", b"X509 CRL": "crl", b"CERTIFICATE REQUEST": "csr"}.get(
        метка, "key" if метка.endswith(b"KEY") else "pem")


ФОРМАТЫ: list[Формат] = [
    Формат("JPEG", "jpg", b"\xff\xd8\xff", _jpeg),
    Формат("PNG", "png", b"\x89PNG\r\n\x1a\n", _png),
    Формат("GIF", "gif", b"GIF87a", _gif),
    Формат("GIF", "gif", b"GIF89a", _gif),
    Формат("PDF", "pdf", b"%PDF-", _pdf),
    Формат("ZIP", "zip", b"PK\x03\x04", _zip, вид=_zip_вид),
    Формат("RAR 4", "rar", b"Rar!\x1a\x07\x00", _rar4),
    Формат("RAR 5", "rar", b"Rar!\x1a\x07\x01\x00", _rar5),
    Формат("7-Zip", "7z", b"7z\xbc\xaf\x27\x1c", _7z),
    Формат("tar", "tar", b"ustar", _tar, сдвиг=257),
    Формат("CAB", "cab", b"MSCF\x00\x00\x00\x00", _cab),
    Формат("GZIP", "gz", b"\x1f\x8b\x08", _gzip),
    Формат("BZIP2", "bz2", b"BZh", lambda д, м: _распаковка(bz2.BZ2Decompressor)(д, м)
           if м + 10 <= len(д) and 0x31 <= д[м + 3] <= 0x39 and д[м + 4:м + 10] == b"\x31\x41\x59\x26\x53\x59" else None),
    Формат("XZ", "xz", b"\xfd7zXZ\x00", _распаковка(lambda: lzma.LZMADecompressor(lzma.FORMAT_XZ))),
    Формат("Zstandard", "zst", b"\x28\xb5\x2f\xfd", _zstd),
    Формат("BMP", "bmp", b"BM", _bmp),
    Формат("TIFF", "tif", b"II*\x00", _tiff),
    Формат("TIFF", "tif", b"MM\x00*", _tiff),
    Формат("RIFF", "riff", b"RIFF", _riff, вид=_riff_вид),
    Формат("Ogg", "ogg", b"OggS", _ogg),
    Формат("FLAC", "flac", b"fLaC", _без_длины(lambda д, м: м + 8 <= len(д) and д[м + 4] & 0x7F == 0)),
    Формат("MP3 (ID3)", "mp3", b"ID3", _mp3_id3),
    *(Формат("MP3", "mp3", синхро, _mp3) for синхро in (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2")),
    Формат("MP4/MOV", "mp4", b"ftyp", _mp4, сдвиг=4, вид=_mp4_вид),
    Формат("Matroska/WebM", "mkv", b"\x1a\x45\xdf\xa3", _ebml, вид=_ebml_вид),
    Формат("FLV", "flv", b"FLV\x01", _flv),
    Формат("MPEG-TS", "ts", b"\x47\x40\x00", _ts),
    # Файлы AMR (RFC 4867, 5.1) и потоки Annex B: H.264 — SPS (тип 7 в младших 5 битах, libmagic Magdir/animation
    # «JVT NAL sequence»), затем PPS (8); H.265 — VPS (тип 32: байт 0x40, RFC 7798, 1.1.4), затем SPS (33: 0x42).
    Формат("AMR", "amr", b"#!AMR\n", _без_длины(lambda д, м: True)),
    Формат("AMR-WB", "awb", b"#!AMR-WB\n", _без_длины(lambda д, м: True)),
    Формат("H.264 (Annex B)", "h264", b"\x00\x00\x00\x01", _annex_b(7, 0x1F, 8)),
    Формат("H.265 (Annex B)", "h265", b"\x00\x00\x00\x01", _annex_b(0x40, 0x7E, 0x42)),
    Формат("ELF", "elf", b"\x7fELF", _elf),
    Формат("PE (EXE/DLL)", "exe", b"MZ", _pe),
    *(Формат("Mach-O", "macho", магия, _macho) for магия in (b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
                                                             b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe")),
    Формат("Mach-O (универсальный)", "macho", b"\xca\xfe\xba\xbe", _macho_fat),
    Формат("SQLite", "sqlite", b"SQLite format 3\x00", _sqlite),
    Формат("OLE2 (DOC/XLS/PPT/MSG)", "ole", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", _ole, вид=_ole_вид),
    Формат("RTF", "rtf", b"{\\rtf", _rtf),
    Формат("ISO 9660", "iso", b"CD001", _iso, сдвиг=32769),
    Формат("Сертификат X.509 (DER)", "der", b"\x30\x82", _der),
    Формат("PEM", "pem", b"-----BEGIN ", _pem, вид=_pem_вид),
    Формат("pcap", "pcap", b"\xd4\xc3\xb2\xa1", _без_длины(lambda д, м: м + 24 <= len(д) and д[м + 4:м + 6] == b"\x02\x00")),
    Формат("pcapng", "pcapng", b"\x0a\x0d\x0d\x0a", _без_длины(lambda д, м: д[м + 8:м + 12] in (b"\x4d\x3c\x2b\x1a",
                                                                                               b"\x1a\x2b\x3c\x4d"))),
    Формат("XML", "xml", b"<?xml ", _без_длины(lambda д, м: True)),
]


#: Виды, у которых подпись повторяется внутри файла как его часть, а не как вложенный файл.
ЧАСТИ_ФАЙЛА = frozenset({"tar", "mp3", "ts"})


def сигнатуры(биты: np.ndarray, *, любой_сдвиг: bool = True, инверсия: bool = False,
              предел: int = 500) -> list[dict[str, object]]:
    """Файлы в потоке: подпись и структура сошлись. Позиция — в битах; длина — если известна."""
    найдено: list[dict[str, object]] = []
    for сдвиг, инв, вид in виды(биты, сдвиги=range(8) if любой_сдвиг else (0,), инверсия=инверсия):
        _найти_в(вид, сдвиг, инв, найдено, предел)
    return _без_повторов(найдено, предел)


def сигнатуры_в_байтах(данные: bytes, предел: int = 500) -> list[dict[str, object]]:
    """То же для байт, выровненных по границе (собранный поток TCP/UDP), — без перевода в биты."""
    найдено: list[dict[str, object]] = []
    _найти_в(данные, 0, False, найдено, предел)
    return _без_повторов(найдено, предел)


def _найти_в(вид: bytes, сдвиг: int, инв: bool, найдено: list[dict[str, object]], предел: int) -> None:
    for формат in ФОРМАТЫ:
        for место in _все(вид, формат.подпись, 20000):
            начало = место - формат.сдвиг
            try:
                длина = формат.проверка(вид, место)
                if длина is None:
                    continue
                расширение = формат.вид(вид, начало) if формат.вид else формат.расширение
            except (struct.error, IndexError, ValueError):
                continue
            найдено.append({"что": формат.имя, "расширение": расширение, "бит": 8 * начало + сдвиг,
                            "сдвиг": сдвиг, "инверсия": инв, "байт": начало, "длина": int(длина)})
            if len(найдено) >= предел:
                break


def _без_повторов(найдено: list[dict[str, object]], предел: int) -> list[dict[str, object]]:
    # Вложенные подписи (JPEG внутри ZIP без сжатия, миниатюра в JPEG) оставляем:
    # аналитику важно и то, и другое. Повторы одной находки — нет; и частей того же файла
    # (заголовки членов tar, кадры MP3, повторные PAT транспортного потока) — тоже нет.
    найдено.sort(key=lambda н: (н["бит"], н["что"]))
    итог, было, концы = [], set(), {}
    for н in найдено:
        ключ = (н["бит"], н["что"], н["инверсия"])
        часть = (н["расширение"], н["сдвиг"], н["инверсия"])
        if ключ in было or н["расширение"] in ЧАСТИ_ФАЙЛА and н["байт"] < концы.get(часть, -1):
            continue
        было.add(ключ)
        итог.append(н)
        if н["длина"]:
            концы[часть] = max(концы.get(часть, -1), н["байт"] + н["длина"])
    return итог[:предел]


def опознать(данные: bytes) -> dict[str, object] | None:
    """Вид файла по содержимому (магия с начала данных и сверка структуры), а не по имени и типу:
    {"что", "расширение", "длина"} первого сошедшегося формата или None."""
    for формат in ФОРМАТЫ:
        место = формат.сдвиг
        if данные[место:место + len(формат.подпись)] != формат.подпись:
            continue
        try:
            длина = формат.проверка(данные, место)
            if длина is None:
                continue
            расширение = формат.вид(данные, 0) if формат.вид else формат.расширение
        except (struct.error, IndexError, ValueError):
            continue
        return {"что": формат.имя, "расширение": расширение, "длина": int(длина)}
    return None


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
ИМЯ_ФАЙЛА = re.compile(r"[\w\-()]{1,80}\.(?:" + РАСШИРЕНИЯ + r")\b", re.I | re.U)
АДРЕС = re.compile(r"(?:https?|ftp)://[^\s\"'<>]{3,200}|[\w.+-]{1,64}@[\w-]+(?:\.[\w-]+)+|"
                   r"\b(?:\d{1,3}\.){3}\d{1,3}\b", re.U)


ОБЫЧНАЯ_ПУНКТУАЦИЯ = set("._-/\\:@,;!?()\"'«»№")
#: Длиннее — не слово (путь и адрес — тоже короче): отбрасывается сразу, без разбора.
СЛОВО_ДО = 200


def _слово_годное(слово: str) -> bool:
    if len(слово) > СЛОВО_ДО:
        return False
    буквы = re.sub(r"[^\w]", "", слово)
    if not буквы:
        return False
    # Скобки, фигурные, угловые, «^`~|=%» посреди «слова» — признак случайных байт.
    if sum(1 for x in слово if not x.isalnum() and x not in ОБЫЧНАЯ_ПУНКТУАЦИЯ) > 0:
        return False
    # Внутри слова из знаков допустимы только . - _ / : @ ' («e-mail», «file.txt»);
    # «!?,;()"» — только по краям слова («связи!», «(см.»).
    if re.search(r"\w[!?,;()\"«»№]+\w", слово):
        return False
    # Цифра между буквами («lz2wre», «Юсф5ОЁ») — случайные байты; а «mp3», «IPv4»,
    # «x86_64», «отчёт_2024» — нет: там цифры в конце части слова.
    if re.search(r"[^\W\d_]\d+[^\W\d_]", слово):
        return False
    # Латиница и кириллица в одной части слова — тоже.
    for часть in re.findall(r"[^\W\d_]+", слово):
        if not (часть.isascii() or all("а" <= x.lower() <= "я" or x in "ёЁ" for x in часть)):
            return False
    if re.fullmatch(r"[\d.:/\-_]+", слово):
        return True                                    # числа, адреса, даты
    return all(_буквы_годные(ч) for ч in re.findall(r"[^\W\d_]+", слово))


ГЛАСНЫЕ = set("аеёиоуыэюяaeiouy")


def _буквы_годные(ч: str) -> bool:
    """Слово из букв похоже на слово: одно письмо, обычный регистр, есть гласные,
    нет длинных цепочек согласных и повторов одной буквы."""
    if len(ч) < 3:
        return True
    кирилл = all("а" <= x.lower() <= "я" or x in "ёЁ" for x in ч)
    if not (кирилл or ч.isascii()):
        return False
    if not (ч.islower() or ч.isupper() or (ч[0].isupper() and ч[1:].islower())):
        return False
    if len(set(ч.lower())) < 3 or re.search(r"(.)\1\1\1", ч):
        return False
    подряд, наибольшее = 0, 0
    for x in ч.lower():
        подряд = 0 if x in ГЛАСНЫЕ else подряд + 1
        наибольшее = max(наибольшее, подряд)
    # Аббревиатуры (HTTP, JFIF, ГОСТ) и расширения (html, png, pdf) бывают без гласных — только короткие.
    if (ч.isupper() and len(ч) <= 5) or (ч.isascii() and len(ч) <= 4):
        return True
    return наибольшее <= 4 and any(x in ГЛАСНЫЕ for x in ч.lower())


def похоже_на_текст(текст: str) -> bool:
    """Отсеять «строки» из случайных байт.

    Случайные байты в CP1251 — наполовину буквы (0xC0–0xFF), прописных там
    столько же, сколько строчных, а в ASCII — вперемешку со знаками. Текст
    же — это слова: одно письмо в слове, обычный регистр, гласные, без цифр
    посреди букв и без «{}<>=^». Годные слова должны составлять почти весь
    текст, и хоть одно — быть словом из трёх букв и больше.
    """
    слова = текст.split()
    if not слова:
        return False
    годные = [с for с in слова if _слово_годное(с)]
    знаков = sum(len(с) for с in слова)
    if sum(len(с) for с in годные) < 0.8 * знаков:
        return False
    return any(len(ч) >= 3 and _буквы_годные(ч) for с in годные for ч in re.findall(r"[^\W\d_]+", с))


def обрезать(текст: str) -> tuple[int, str]:
    """Отрезать мусор по краям: случайные байты перед текстом тоже «печатные».

    Текст режется на слова по пробелам; с начала и с конца отбрасываются
    слова, которые словами не выглядят. Возвращает (сколько символов
    отрезано слева, остаток).
    """
    слова = [(м.start(), м.group()) for м in re.finditer(r"\S+", текст)]
    # Мусор бывает приклеен к первому слову: «ЩПередача» — пробуем отрезать до 8 знаков.
    if слова and (not _слово_годное(слова[0][1]) or re.match(r"[\W\d_]+[^\W\d_]{4,}$", слова[0][1][:СЛОВО_ДО])):
        for k in range(1, 9):
            if len(слова[0][1]) > k + 2 and _слово_годное(слова[0][1][k:]):
                слова[0] = (слова[0][0] + k, слова[0][1][k:])
                break
    while слова and not _слово_годное(слова[0][1]):
        слова.pop(0)
    # И к последнему: «example.com/aЖЕ3» — самый длинный годный префикс.
    if слова and not _слово_годное(слова[-1][1]):
        последнее = слова[-1][1]
        for k in range(min(len(последнее) - 1, СЛОВО_ДО), 2, -1):
            if _слово_годное(последнее[:k]):
                слова[-1] = (слова[-1][0], последнее[:k])
                break
    while слова and not _слово_годное(слова[-1][1]):
        слова.pop()
    if not слова:
        return 0, ""
    начало = слова[0][0]
    конец = слова[-1][0] + len(слова[-1][1])
    return начало, текст[начало:конец]


def _одно_слово_годное(текст: str) -> bool:
    """Кириллица одним «словом» без пробелов — только с настоящим словом внутри.

    Случайные байты в CP1251 дают «ЩОЖ-@В_R\\мо», «Мёйя4.яэ_2»: куски по
    три-четыре буквы, склеенные знаками. Текст без пробелов («отчёт_2024.doc»,
    «Передача») держится на слове из пяти букв и больше, и оно — заметная
    часть строки.
    """
    if re.search(r"\s", текст.strip()):
        return True
    куски = re.findall(r"[а-яёА-ЯЁ]+", текст)
    длиннейший = max((len(к) for к in куски), default=0)
    return длиннейший >= 5 and 3 * длиннейший >= len(текст.strip())


#: Повтор одного знака (байта, у UTF-16 — пары байт) не короче стольких — заполнение.
ЗАПОЛНЕНИЕ_ОТ = 16


class _Кусок:
    """Часть совпадения между полосами заполнения — с тем же видом, что у re.Match."""

    def __init__(self, байты: bytes, начало: int):
        self._байты, self._начало = байты, начало

    def group(self) -> bytes:
        return self._байты

    def start(self) -> int:
        return self._начало

    def end(self) -> int:
        return self._начало + len(self._байты)


def _куски_без_заполнения(м: re.Match[bytes], единица: int, наименьшая: int):
    """Совпадение без полос из одного повторённого знака: куски между ними (не короче ``наименьшая``).

    Знак — байт или (у UTF-16) пара байт от начала совпадения: полоса режется
    только по границам знаков, иначе текст за ней читался бы со сдвигом на байт.
    """
    данные, начало = м.group(), м.start()
    n = len(данные) // единица
    if n < ЗАПОЛНЕНИЕ_ОТ:
        yield м
        return
    знаки = np.frombuffer(данные[:n * единица], dtype=np.uint8 if единица == 1 else "<u2")
    границы = np.concatenate([[0], np.flatnonzero(знаки[1:] != знаки[:-1]) + 1, [n]])
    длины = np.diff(границы)
    полосы = np.flatnonzero(длины >= ЗАПОЛНЕНИЕ_ОТ)
    if not len(полосы):
        yield м
        return
    место = 0
    for i in полосы:
        от, до = int(границы[i]), int(границы[i + 1])
        if от - место >= наименьшая:
            yield _Кусок(данные[место * единица:от * единица], начало + место * единица)
        место = до
    if len(данные) - место * единица >= наименьшая * единица:
        yield _Кусок(данные[место * единица:], начало + место * единица)


def строки(биты: np.ndarray, *, наименьшая: int = 8, сдвиги: Sequence[int] = (0,), инверсия: bool = False,
           предел: int = 3000) -> dict[str, object]:
    """Текстовые фрагменты (ASCII, CP1251, UTF-8, UTF-16LE), имена файлов, адреса."""
    найдено: list[dict[str, object]] = []
    шаблоны = [
        ("ASCII", re.compile(rb"[\x20-\x7e\t]{%d,}" % наименьшая), "ascii"),
        ("UTF-8", re.compile(rb"(?:[\x20-\x7e]|[\xd0\xd1][\x80-\xbf]){%d,}" % наименьшая), "utf-8"),
        ("CP1251", re.compile(rb"(?:[\x20-\x7e]|[\xc0-\xff\xa8\xb8]){%d,}" % наименьшая), "cp1251"),
        ("UTF-16LE", re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % наименьшая), "utf-16-le"),
    ]
    for сдвиг, инв, вид in виды(биты, сдвиги=сдвиги, инверсия=инверсия):
        занято: list[tuple[int, int]] = []
        for имя, шаблон, кодировка in шаблоны:
            единица = 2 if имя == "UTF-16LE" else 1
            for м_ in шаблон.finditer(вид):
                # «~~~~» (флаги HDLC 0x7E в байтовой фазе), «    », «UUUU» — заполнение,
                # не текст: длинные повторы одного знака вырезаются, куски между ними
                # разбираются каждый сам (иначе и текст внутри пропал бы, и перебор
                # краёв такой «строки» был бы квадратичным).
                for м in _куски_без_заполнения(м_, единица, наименьшая):
                    if len(найдено) >= предел:
                        break
                    текст = м.group().decode(кодировка, "replace")
                    отрезано, текст = обрезать(текст)
                    if len(текст) < наименьшая:
                        continue
                    сдвиг_байт = len(м.group().decode(кодировка, "replace")[:отрезано].encode(кодировка))
                    if имя in ("UTF-8", "CP1251") and len(текст) < max(10, наименьшая):
                        continue
                    if имя in ("UTF-8", "CP1251") and not re.search(r"[а-яёА-ЯЁ]{3,}", текст):
                        # Только с кириллическим словом: чистый ASCII уже найден первым
                        # шаблоном, а одиночные старшие байты в CP1251 — не текст.
                        continue
                    if имя in ("UTF-8", "CP1251") and not _одно_слово_годное(текст):
                        continue
                    if any(а <= м.start() < б for а, б in занято) and имя != "UTF-16LE":
                        continue
                    if not похоже_на_текст(текст):
                        continue
                    занято.append((м.start(), м.end()))
                    найдено.append({"бит": 8 * (м.start() + сдвиг_байт) + сдвиг, "сдвиг": сдвиг, "инверсия": инв,
                                    "кодировка": имя, "текст": текст[:400],
                                    "длина": len(текст.encode(кодировка, "replace"))})
    # Строка внутри другой строки того же вида (ASCII-часть текста в CP1251) — не повтор.
    найдено.sort(key=lambda н: (н["сдвиг"], н["инверсия"], н["бит"], -н["длина"]))
    без_вложенных, конец_вида = [], {}
    for н in найдено:
        вид_ = (н["сдвиг"], н["инверсия"])
        if н["бит"] + 8 * н["длина"] <= конец_вида.get(вид_, -1):
            continue
        конец_вида[вид_] = max(конец_вида.get(вид_, -1), н["бит"] + 8 * н["длина"])
        без_вложенных.append(н)
    найдено = sorted(без_вложенных, key=lambda н: н["бит"])
    имена, адреса = [], []
    for н in найдено:
        for м in ИМЯ_ФАЙЛА.finditer(н["текст"]):
            имена.append({"имя": м.group().strip(), "бит": н["бит"]})
        for м in АДРЕС.finditer(н["текст"]):
            адреса.append({"адрес": м.group(), "бит": н["бит"]})
    return {"строки": найдено, "имена_файлов": имена[:500], "адреса": адреса[:500]}


# -- частые комбинации ----------------------------------------------------------------------------

def частые(биты: np.ndarray, *, от: int = 2, до: int = 8, сдвиги: Sequence[int] = (0,),
           лучших: int = 40, выборка: int = 8 << 20) -> list[dict[str, object]]:
    """Повторяющиеся комбинации от 2 до 8 байт — и блоки, которые из них вырастают.

    Комбинация берётся, если встречается заметно чаще случайного (с учётом
    частот самих байтов). Затем она расширяется влево и вправо, пока байты
    совпадают почти во всех вхождениях, — получается весь повторяющийся
    блок: маркер, заголовок с постоянными полями, синхрослово. Для блока —
    сколько раз, во сколько раз чаще случайного, типичный шаг между
    вхождениями и доля вхождений с этим шагом (регулярный шаг — кадр).
    """
    итог: list[dict[str, object]] = []
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
