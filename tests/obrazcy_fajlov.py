"""Образцы файлов для тестов поиска по сигнатурам и объектов захвата.

Где можно — собираются стандартной библиотекой (zipfile, tarfile, gzip, bz2, lzma, sqlite3, wave);
где нельзя (RAR, CAB, Zstandard, OLE, TIFF, MP3, Matroska, FLV, MPEG-TS, Mach-O, ISO 9660) — наименьшие
корректные образцы по описанию формата, поле за полем (источники — в poisk.py у проверок).
"""

import base64
import bz2
import gzip
import io
import lzma
import os
import sqlite3
import struct
import tarfile
import tempfile
import wave
import zipfile
import zlib


def zip_(члены):
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w", zipfile.ZIP_DEFLATED) as z:
        for имя, данные in члены:
            z.writestr(имя, данные)
    return буфер.getvalue()


def docx():
    return zip_([("[Content_Types].xml", "<Types/>"), ("_rels/.rels", "<Relationships/>"),
                 ("word/document.xml", "<w:document>текст</w:document>")])


def xlsx():
    return zip_([("[Content_Types].xml", "<Types/>"), ("xl/workbook.xml", "<workbook/>")])


def pptx():
    return zip_([("[Content_Types].xml", "<Types/>"), ("ppt/presentation.xml", "<p/>")])


def odt():
    """ODF: первый член «mimetype» без сжатия (OpenDocument 1.2, ч. 3, 3.3)."""
    буфер = io.BytesIO()
    with zipfile.ZipFile(буфер, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("content.xml", "<office:document-content/>", zipfile.ZIP_DEFLATED)
    return буфер.getvalue()


def tar_(члены, режим="w"):
    буфер = io.BytesIO()
    with tarfile.open(fileobj=буфер, mode=режим) as t:
        каталог = tarfile.TarInfo("папка")
        каталог.type = tarfile.DIRTYPE
        t.addfile(каталог)
        for имя, данные in члены:
            инфо = tarfile.TarInfo(имя)
            инфо.size = len(данные)
            t.addfile(инфо, io.BytesIO(данные))
    return буфер.getvalue()


def sqlite_():
    with tempfile.TemporaryDirectory() as папка:
        путь = os.path.join(папка, "b.sqlite")
        с = sqlite3.connect(путь)
        с.execute("create table t (a text)")
        с.executemany("insert into t values (?)", [(f"строка {i}",) for i in range(50)])
        с.commit()
        с.close()
        return open(путь, "rb").read()


def wav_(отсчётов=800):
    буфер = io.BytesIO()
    with wave.open(буфер, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", (i * 37) % 3000 - 1500) for i in range(отсчётов)))
    return буфер.getvalue()


def riff_(форма, тело):
    return b"RIFF" + struct.pack("<I", 4 + len(тело)) + форма + тело


def webp():
    данные = b"\x2f" + bytes(13)                 # VP8L: подпись 0x2f и заголовок
    return riff_(b"WEBP", b"VP8L" + struct.pack("<I", len(данные)) + данные)


def avi():
    return riff_(b"AVI ", b"LIST" + struct.pack("<I", 4) + b"hdrl")


def _vint(n):
    """Число RAR 5: по 7 бит от младших, старший бит — «дальше»."""
    итог = bytearray()
    while True:
        итог.append((n & 0x7F) | (0x80 if n > 0x7F else 0))
        n >>= 7
        if not n:
            return bytes(итог)


def _rar5_блок(вид, флаги, поля, данные=b""):
    заголовок = _vint(вид) + _vint(флаги) + поля
    тело = _vint(len(заголовок)) + заголовок
    return struct.pack("<I", zlib.crc32(тело)) + тело + данные


def rar5(содержимое=b"hello rar5 " * 20, имя=b"a.txt"):
    """Подпись, главный заголовок (1), файл (2) без сжатия с CRC32 и конец архива (5)."""
    файл = (_vint(len(содержимое)) + _vint(0x0004) + _vint(len(содержимое)) + _vint(0x20)
            + struct.pack("<I", zlib.crc32(содержимое)) + _vint(0) + _vint(1) + _vint(len(имя)) + имя)
    return (b"Rar!\x1a\x07\x01\x00" + _rar5_блок(1, 0, _vint(0)) + _rar5_блок(2, 0x0002, файл, содержимое)
            + _rar5_блок(5, 0, _vint(0)))


def _rar4_блок(вид, флаги, тело):
    заголовок = struct.pack("<BHH", вид, флаги, 7 + len(тело)) + тело
    return struct.pack("<H", zlib.crc32(заголовок) & 0xFFFF) + заголовок


def rar4(содержимое=b"hello rar4 " * 20, имя=b"b.txt"):
    """Метка, главный заголовок (0x73), файл (0x74, метод 0x30 — хранение) и конец (0x7B)."""
    файл = struct.pack("<IIBIIBBHI", len(содержимое), len(содержимое), 0, zlib.crc32(содержимое), 0, 20, 0x30,
                       len(имя), 0x20) + имя
    return (b"Rar!\x1a\x07\x00" + _rar4_блок(0x73, 0, bytes(6)) + _rar4_блок(0x74, 0x8000, файл) + содержимое
            + _rar4_блок(0x7B, 0x4000, b""))


def cab(содержимое=b"hello cab " * 30, имя=b"c.txt"):
    """CFHEADER (36) + CFFOLDER (8) + CFFILE (16 + имя) + CFDATA (8 + данные), без сжатия."""
    файл = struct.pack("<IIHHHH", len(содержимое), 0, 0, 0, 0, 0x20) + имя + b"\x00"
    начало_данных = 36 + 8 + len(файл)
    всего = начало_данных + 8 + len(содержимое)
    return (b"MSCF" + struct.pack("<IIIIIBBHHHHH", 0, всего, 0, 44, 0, 3, 1, 1, 1, 0, 0x1234, 0)
            + struct.pack("<IHH", начало_данных, 1, 0) + файл
            + struct.pack("<IHH", 0, len(содержимое), len(содержимое)) + содержимое)


def zstd_(содержимое=b"hello zstd " * 10):
    """Кадр: подпись, дескриптор (одиночный сегмент, FCS 1 байт), размер, один сырой блок (последний)."""
    return (b"\x28\xb5\x2f\xfd" + bytes([0x20, len(содержимое)])
            + ((len(содержимое) << 3) | 1).to_bytes(3, "little") + содержимое)


def ole(поток="WordDocument", размер=4096):
    """Составной документ с секторами по 512: FAT (сектор 0), каталог (1), поток (2 …)."""
    секторов = размер // 512
    fat = [0xFFFFFFFD, 0xFFFFFFFE] + list(range(3, 2 + секторов)) + [0xFFFFFFFE]
    fat += [0xFFFFFFFF] * (128 - len(fat))
    заголовок = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(16)
                 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + bytes(6)
                 + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
                 + struct.pack("<109I", 0, *([0xFFFFFFFF] * 108)))

    def запись(имя, вид, ребёнок, начало, длина):
        имя16 = (имя + "\x00").encode("utf-16-le")
        return (имя16 + bytes(64 - len(имя16)) + struct.pack("<HBB", len(имя16), вид, 1)
                + struct.pack("<III", 0xFFFFFFFF, 0xFFFFFFFF, ребёнок) + bytes(16 + 4 + 16)
                + struct.pack("<II", начало, длина) + bytes(4))

    каталог = (запись("Root Entry", 5, 1, 0xFFFFFFFE, 0) + запись(поток, 2, 0xFFFFFFFF, 2, размер)
               + bytes(256))
    return заголовок + struct.pack("<128I", *fat) + каталог + bytes(range(256)) * (размер // 256)


def rtf():
    return b"{\\rtf1\\ansi{\\fonttbl{\\f0 Arial;}}\\f0 \\{ \\} \\\\ \\bin3 }}}{\\b text}}"


def tiff():
    """II, IFD с 7 записями (полоса — StripOffsets/StripByteCounts), затем 4 байта изображения."""
    записи = [(256, 3, 1, 2), (257, 3, 1, 2), (258, 3, 1, 8), (259, 3, 1, 1), (262, 3, 1, 1),
              (273, 4, 1, 8 + 2 + 12 * 7 + 4), (279, 4, 1, 4)]
    ifd = struct.pack("<H", len(записи)) + b"".join(struct.pack("<HHII", *з) for з in записи) + struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", 8) + ifd + b"\x10\x20\x30\x40"


def mp3(кадров=6, id3=True):
    """ID3v2.4 (10 байт + 20 байт тела) и кадры MPEG-1 слой III 128 кбит/с 44,1 кГц: 417 байт."""
    кадр = b"\xff\xfb\x90\x00" + bytes(413)
    тег = b"ID3\x04\x00\x00" + bytes([0, 0, 0, 20]) + bytes(20) if id3 else b""
    return тег + кадр * кадров


def _ebml(ид, данные):
    размер = len(данные)
    return ид + bytes([0x40 | (размер >> 8), размер & 0xFF]) + данные


def webm():
    заголовок = _ebml(b"\x1a\x45\xdf\xa3", _ebml(b"\x42\x82", b"webm"))
    return заголовок + _ebml(b"\x18\x53\x80\x67", _ebml(b"\xec", bytes(50)))


def flv(меток=3):
    итог = b"FLV\x01\x05" + struct.pack(">I", 9) + struct.pack(">I", 0)
    for i in range(меток):
        данные = bytes([0x17, i]) + bytes(20)
        итог += bytes([9]) + len(данные).to_bytes(3, "big") + bytes(4) + bytes(3) + данные
        итог += struct.pack(">I", 11 + len(данные))
    return итог


def ts(пакетов=6):
    return b"".join(bytes([0x47, 0x40 if i == 0 else 0x01, 0x00, 0x10 | (i & 15)]) + bytes(184)
                    for i in range(пакетов))


def macho(длина=4096):
    """Mach-O 64 LE: заголовок (32 байта) и одна команда LC_SEGMENT_64 (72 байта) на весь файл."""
    сегмент = struct.pack("<II16sQQQQiiII", 0x19, 72, b"__TEXT", 0, длина, 0, длина, 5, 5, 0, 0)
    заголовок = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 2, 1, len(сегмент), 0, 0)
    тело = заголовок + сегмент
    return тело + bytes(длина - len(тело))


def macho_fat():
    тонкий = macho()
    return (struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(">iiIII", 0x01000007, 3, 4096, len(тонкий), 12)
            + bytes(4096 - 28) + тонкий)


def iso(блоков=20):
    """ISO 9660: 16 секторов системной области, основной дескриптор (1), завершающий (255), данные."""
    pvd = bytearray(2048)
    pvd[0:7] = b"\x01CD001\x01"
    struct.pack_into("<I", pvd, 80, блоков)
    struct.pack_into(">I", pvd, 84, блоков)
    struct.pack_into("<H", pvd, 128, 2048)
    struct.pack_into(">H", pvd, 130, 2048)
    конец = b"\xffCD001\x01" + bytes(2041)
    return bytes(16 * 2048) + bytes(pvd) + конец + bytes((блоков - 18) * 2048)


def pem(метка="CERTIFICATE"):
    тело = base64.encodebytes(bytes(range(100))).replace(b"\n", b"\r\n")
    return f"-----BEGIN {метка}-----\r\n".encode() + тело + f"-----END {метка}-----\r\n".encode()


def pdf():
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def png():
    def кусок(т, д):
        return struct.pack(">I", len(д)) + т + д + struct.pack(">I", zlib.crc32(т + д))
    return (b"\x89PNG\r\n\x1a\n" + кусок(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + кусок(b"IDAT", zlib.compress(bytes(14))) + кусок(b"IEND", b""))


def gif():
    return (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0, 0, 0) + b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0)
            + b"\x02\x02\x44\x01\x00" + b"\x3b")


def jpeg():
    return (b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + bytes(9)
            + b"\xff\xdb" + struct.pack(">H", 67) + bytes(65)
            + b"\xff\xda" + struct.pack(">H", 8) + bytes(6) + bytes(range(1, 200)) + b"\xff\xd9")


def bmp():
    пиксели = bytes(16)
    return (b"BM" + struct.pack("<IIII", 54 + len(пиксели), 0, 54, 40)
            + struct.pack("<iiHHIIiiII", 2, 2, 1, 24, 0, len(пиксели), 0, 0, 0, 0) + пиксели)


def elf():
    """ELF64 LE: заголовок 64 байта, таблица секций (1 запись по 64) сразу за ним."""
    return (b"\x7fELF\x02\x01\x01" + bytes(9) + struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, 64, 0, 64, 0, 0, 64, 1, 0)
            + bytes(64))


def seven_z():
    """7z: подпись, версия 0.4, CRC стартового заголовка, смещение/размер/CRC следующего заголовка."""
    следующий = b"\x01\x04\x06\x00"                  # Header, MainStreamsInfo … — любые байты для длины
    стартовый = struct.pack("<QQI", 0, len(следующий), zlib.crc32(следующий))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", zlib.crc32(стартовый)) + стартовый + следующий


#: Образцы: (имя, данные, что, расширение) — «что» и «расширение», как их называет poisk.
def все():
    текст = "Отчёт по объекту. ".encode() * 40
    return [
        ("zip", zip_([("a.txt", текст), ("b/c.bin", bytes(range(256)))]), "ZIP", "zip"),
        ("docx", docx(), "ZIP", "docx"),
        ("xlsx", xlsx(), "ZIP", "xlsx"),
        ("pptx", pptx(), "ZIP", "pptx"),
        ("odt", odt(), "ZIP", "odt"),
        ("tar", tar_([("папка/отчёт.txt", текст), ("данные.bin", bytes(1000))]), "tar", "tar"),
        ("tar.gz", tar_([("x.txt", текст)], "w:gz"), "GZIP", "gz"),
        ("gz", gzip.compress(текст), "GZIP", "gz"),
        ("bz2", bz2.compress(текст), "BZIP2", "bz2"),
        ("xz", lzma.compress(текст), "XZ", "xz"),
        ("zst", zstd_(), "Zstandard", "zst"),
        ("rar4", rar4(), "RAR 4", "rar"),
        ("rar5", rar5(), "RAR 5", "rar"),
        ("7z", seven_z(), "7-Zip", "7z"),
        ("cab", cab(), "CAB", "cab"),
        ("pdf", pdf(), "PDF", "pdf"),
        ("doc", ole("WordDocument"), "OLE2 (DOC/XLS/PPT/MSG)", "doc"),
        ("xls", ole("Workbook"), "OLE2 (DOC/XLS/PPT/MSG)", "xls"),
        ("rtf", rtf(), "RTF", "rtf"),
        ("jpeg", jpeg(), "JPEG", "jpg"),
        ("png", png(), "PNG", "png"),
        ("gif", gif(), "GIF", "gif"),
        ("bmp", bmp(), "BMP", "bmp"),
        ("tiff", tiff(), "TIFF", "tif"),
        ("webp", webp(), "RIFF", "webp"),
        ("wav", wav_(), "RIFF", "wav"),
        ("avi", avi(), "RIFF", "avi"),
        ("mp3", mp3(), "MP3 (ID3)", "mp3"),
        ("mp3-без-id3", mp3(id3=False), "MP3", "mp3"),
        ("webm", webm(), "Matroska/WebM", "webm"),
        ("flv", flv(), "FLV", "flv"),
        ("ts", ts(), "MPEG-TS", "ts"),
        ("elf", elf(), "ELF", "elf"),
        ("macho", macho(), "Mach-O", "macho"),
        ("macho-fat", macho_fat(), "Mach-O (универсальный)", "macho"),
        ("sqlite", sqlite_(), "SQLite", "sqlite"),
        ("iso", iso(), "ISO 9660", "iso"),
        ("pem", pem(), "PEM", "crt"),
    ]
