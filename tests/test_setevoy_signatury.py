"""База сигнатур поиска файлов (potok.poisk): каждый формат — подпись, сверка структуры и длина по описанию
формата; отказы на испорченной структуре; поиск в собранном потоке без перевода в биты."""

import io
import struct
import tarfile
import unittest
import zipfile
import zlib
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import obrazcy_fajlov as обр
from reportgen.potok import poisk


def опознать(данные):
    н = poisk.опознать(данные)
    return None if н is None else (н["что"], н["расширение"], н["длина"])


def в_потоке(данные, что, до=b"\x00" * 37, после=b"tail-of-stream"):
    """(место, длина, расширение) находки ``что`` в потоке «до + данные + после»."""
    найдено = [н for н in poisk.сигнатуры_в_байтах(до + данные + после) if н["что"] == что]
    return [(н["байт"], н["длина"], н["расширение"]) for н in найдено]


class ВсеОбразцыTests(unittest.TestCase):
    def test_каждый_образец_опознан_с_длиной_и_в_потоке(self):
        for имя, данные, что, расширение in обр.все():
            with self.subTest(имя):
                self.assertEqual((что, расширение, len(данные)), опознать(данные))
                self.assertEqual([(37, len(данные), расширение)], в_потоке(данные, что))

    def test_в_битах_то_же_что_в_байтах(self):
        поток = b"x" * 11 + обр.cab() + b"y" * 5 + обр.rar5()
        биты = np.unpackbits(np.frombuffer(поток, dtype=np.uint8))
        self.assertEqual(poisk.сигнатуры_в_байтах(поток), poisk.сигнатуры(биты, любой_сдвиг=False))

    def test_неизвестное_и_пустое(self):
        self.assertIsNone(poisk.опознать("просто текст".encode()))
        self.assertIsNone(poisk.опознать(b""))
        self.assertEqual([], poisk.сигнатуры_в_байтах(b""))

    def test_предел_находок(self):
        поток = (b"\x00" * 16 + обр.gif()) * 5
        self.assertEqual(2, len(poisk.сигнатуры_в_байтах(поток, предел=2)))


class ИтогTests(unittest.TestCase):
    def test_длина_больше_вырезаемого_неизвестна(self):
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 100):
            self.assertEqual(100, poisk._итог(100))
            self.assertEqual(0, poisk._итог(101))


class TarTests(unittest.TestCase):
    def test_числа_tar(self):
        self.assertEqual(0o644, poisk._tar_число(b"0000644\x00"))
        self.assertEqual(8, poisk._tar_число(b"10\x00junk   "))
        self.assertEqual(0, poisk._tar_число(b"\x00" * 8))
        self.assertEqual(0x0102, poisk._tar_число(b"\x80" + bytes(9) + b"\x01\x02"))
        self.assertEqual(-1, poisk._tar_число(b"\xff" * 12))
        self.assertIsNone(poisk._tar_число(b"12x45678"))

    def test_gnu_и_без_добивки_записи(self):
        буфер = io.BytesIO()
        with tarfile.open(fileobj=буфер, mode="w", format=tarfile.GNU_FORMAT) as t:
            инфо = tarfile.TarInfo("a.txt")
            инфо.size = 700
            t.addfile(инфо, io.BytesIO(bytes(700)))
        данные = буфер.getvalue()
        self.assertEqual(b"ustar  \x00", данные[257:265])
        self.assertEqual(("tar", "tar", len(данные)), опознать(данные))
        # Без добивки до записи 20 × 512: конец — сразу после двух нулевых блоков (заголовок, 2 блока данных, 2 нулевых).
        self.assertEqual(("tar", "tar", 5 * 512), опознать(данные[:5 * 512]))
        # Добивка есть не вся — не берётся.
        self.assertEqual(("tar", "tar", 5 * 512), опознать(данные[:5 * 512 + 100]))

    def test_отказы_и_обрывы(self):
        данные = обр.tar_([("x.txt", b"abc")])
        испорчен = bytearray(данные)
        испорчен[0] ^= 1                                        # сумма заголовка не сходится
        self.assertIsNone(poisk._tar(bytes(испорчен), 257))
        испорчен = bytearray(данные)
        испорчен[262] = ord("x")                                # после «ustar» не \0 и не пробел
        self.assertIsNone(poisk._tar(bytes(испорчен), 257))
        self.assertIsNone(poisk._tar(данные[1:], 256))          # подпись раньше 257 байта от начала
        self.assertEqual(0, poisk._tar(данные[:1536], 257))     # оборван на данных второго члена
        self.assertEqual(0, poisk._tar(данные[:3 * 512 + 100], 257))  # второй нулевой блок оборван
        второй = bytearray(данные)
        второй[3 * 512 + 3] = 1                                 # после первого нулевого — не нулевой
        self.assertEqual(0, poisk._tar(bytes(второй), 257))
        плохой_член = bytearray(данные)
        плохой_член[2 * 512 + 5] ^= 1                           # сумма второго заголовка не сходится
        self.assertEqual(0, poisk._tar(bytes(плохой_член), 257))
        отрицательный = bytearray(данные[:512])
        отрицательный[124:136] = b"\xff" * 12
        сумма = 256 + sum(отрицательный[:148]) + sum(отрицательный[156:])
        отрицательный[148:156] = b"%06o\x00 " % сумма
        self.assertIsNone(poisk._tar_заголовок(bytes(отрицательный), 0))
        self.assertIsNone(poisk._tar_заголовок(данные[:511], 0))

    def test_каталог_и_ссылки_без_данных(self):
        заголовок = bytearray(512)
        заголовок[0:3] = b"dir"
        заголовок[124:136] = b"00000001000\x00"                 # размер 512, но у каталога данных нет
        заголовок[156] = ord("5")
        заголовок[257:263] = b"ustar\x00"
        заголовок[148:156] = b" " * 8
        заголовок[148:156] = b"%06o\x00 " % sum(заголовок)
        self.assertEqual(0, poisk._tar_заголовок(bytes(заголовок), 0))
        заголовок[156] = ord("0")
        заголовок[148:156] = b" " * 8
        заголовок[148:156] = b"%06o\x00 " % sum(заголовок)
        self.assertEqual(512, poisk._tar_заголовок(bytes(заголовок), 0))

    def test_предел_обхода(self):
        данные = обр.tar_([("a", bytes(3000))])
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 1024):
            self.assertEqual(0, poisk._tar(данные, 257))


class RarTests(unittest.TestCase):
    def test_rar4_размеры_и_конец(self):
        данные = обр.rar4()
        self.assertEqual(len(данные), poisk._rar4(данные, 0))
        self.assertEqual(0, poisk._rar4(данные[:-3], 0))              # конец архива оборван
        self.assertEqual(0, poisk._rar4(данные[:40], 0))               # файл оборван

    def test_rar4_отказы(self):
        данные = bytearray(обр.rar4())
        данные[7] ^= 1                                                 # CRC главного заголовка
        self.assertIsNone(poisk._rar4(bytes(данные), 0))
        данные = bytearray(обр.rar4())
        данные[9] = 0x74                                               # первый блок — не главный
        self.assertIsNone(poisk._rar4(bytes(данные), 0))
        пароль = b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0x0080, bytes(6)) + bytes(40)
        self.assertEqual(0, poisk._rar4(пароль, 0))                    # зашифрованные заголовки
        чужой = b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0, bytes(6)) + b"\x00\x00\x99\x00\x00\x07\x00"
        self.assertEqual(0, poisk._rar4(чужой, 0))                     # неизвестный вид блока
        короткий = b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0, bytes(6)) + b"\x00\x00\x7b\x00\x00\x06\x00"
        self.assertEqual(0, poisk._rar4(короткий, 0))                  # размер блока меньше 7
        self.assertIsNone(poisk._rar4(b"Rar!\x1a\x07\x00" + b"\x00\x00\x73\x00\x00\x03\x00", 0))

    def test_rar4_доп_размер_и_большой_файл(self):
        # Служебный блок (0x7A) с флагом LARGE: упакованный размер — 32 бита + старшие 32 бита на смещении 32.
        тело = struct.pack("<IIBIIBBHI", 5, 5, 0, 0, 0, 20, 0x30, 1, 0x20) + struct.pack("<II", 0, 0) + b"n"
        служебный = обр._rar4_блок(0x7A, 0x8000 | 0x0100, тело) + b"12345"
        комментарий = обр._rar4_блок(0x75, 0x8000, struct.pack("<I", 3)) + b"abc"
        данные = (b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0, bytes(6)) + служебный + комментарий
                  + обр._rar4_блок(0x7B, 0x4000, b""))
        self.assertEqual(len(данные), poisk._rar4(данные, 0))
        большой = bytearray(данные)
        struct.pack_into("<I", большой, 7 + 13 + 7 + 25, 1)             # старшие 32 бита размера = 1
        self.assertEqual(0, poisk._rar4(bytes(большой), 0))

    def test_vint(self):
        self.assertEqual((0x7F, 1), poisk._vint(b"\x7f", 0))
        self.assertEqual((300, 2), poisk._vint(b"\xac\x02", 0))
        self.assertEqual((1 << 63, 10), poisk._vint(b"\x80" * 9 + b"\x01", 0))
        with self.assertRaises(ValueError):
            poisk._vint(b"\x80" * 10 + b"\x01", 0)

    def test_rar5_отказы_и_обрывы(self):
        данные = обр.rar5()
        self.assertEqual(len(данные), poisk._rar5(данные, 0))
        испорчен = bytearray(данные)
        испорчен[8] ^= 1                                                # CRC первого блока
        self.assertIsNone(poisk._rar5(bytes(испорчен), 0))
        второй = bytearray(данные)
        второй[-8] ^= 1                                                 # CRC блока конца
        self.assertEqual(0, poisk._rar5(bytes(второй), 0))
        self.assertEqual(0, poisk._rar5(данные[:-2], 0))
        self.assertEqual(0, poisk._rar5(данные[:12], 0))
        шифр = b"Rar!\x1a\x07\x01\x00" + обр._rar5_блок(4, 0, bytes(4))
        self.assertEqual(0, poisk._rar5(шифр, 0))
        доп = (b"Rar!\x1a\x07\x01\x00" + обр._rar5_блок(1, 0x0001, обр._vint(3) + обр._vint(0) + b"xyz")
               + обр._rar5_блок(5, 0, обр._vint(0)))
        self.assertEqual(len(доп), poisk._rar5(доп, 0))
        длинный = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 4 + b"\x80" * 11
        self.assertEqual(0, poisk._rar5(длинный, 0))


class CabZstdTests(unittest.TestCase):
    def test_cab_отказы(self):
        данные = обр.cab()
        self.assertEqual(len(данные), poisk._cab(данные, 0))
        for место, байт in ((24, 2), (25, 2), (26, 0), (28, 0)):
            испорчен = bytearray(данные)
            испорчен[место] = байт
            испорчен[место + 1 if место in (26, 28) else место] = 0 if место in (26, 28) else байт
            with self.subTest(место=место):
                self.assertIsNone(poisk._cab(bytes(испорчен), 0))
        мал = bytearray(данные)
        struct.pack_into("<I", мал, 8, 35)
        self.assertIsNone(poisk._cab(bytes(мал), 0))
        struct.pack_into("<I", мал, 8, 36)
        self.assertEqual(36, poisk._cab(bytes(мал), 0))
        self.assertIsNone(poisk._cab(данные[:35], 0))
        self.assertEqual(len(данные), poisk._cab(данные[:36], 0))

    def test_zstd_поля_заголовка(self):
        блок = ((5 << 3) | 1).to_bytes(3, "little") + b"abcde"
        for fhd, поля in ((0x00, b"\x40"), (0x01, b"\x40\x07"), (0x02, b"\x40\x07\x00"), (0x03, b"\x40" + bytes(4)),
                          (0x40, b"\x40\x00\x01"), (0x80, b"\x40" + bytes(4)), (0xC0, b"\x40" + bytes(8)),
                          (0x20, b"\x05"), (0x24, b"\x05")):
            хвост = b"CSUM" if fhd & 0x04 else b""
            данные = b"\x28\xb5\x2f\xfd" + bytes([fhd]) + поля + блок + хвост
            with self.subTest(fhd=hex(fhd)):
                self.assertEqual(len(данные), poisk._zstd(данные + b"????", 0))

    def test_zstd_блоки_и_отказы(self):
        rle = ((1000 << 3) | (1 << 1)).to_bytes(3, "little") + b"z"
        сжатый = ((4 << 3) | (2 << 1) | 1).to_bytes(3, "little") + b"wxyz"
        данные = b"\x28\xb5\x2f\xfd\x20\x05" + rle + сжатый
        self.assertEqual(len(данные), poisk._zstd(данные, 0))
        self.assertIsNone(poisk._zstd(b"\x28\xb5\x2f\xfd\x28\x05" + сжатый, 0))           # резервный бит
        self.assertIsNone(poisk._zstd(b"\x28\xb5\x2f\xfd\x20\x05" + ((3 << 1) | 1).to_bytes(3, "little"), 0))
        большой = ((((128 << 10) + 1) << 3) | 1).to_bytes(3, "little")
        self.assertIsNone(poisk._zstd(b"\x28\xb5\x2f\xfd\x20\x05" + большой, 0))
        предел = (((128 << 10) << 3) | 1).to_bytes(3, "little") + bytes(128 << 10)
        self.assertEqual(9 + (128 << 10), poisk._zstd(b"\x28\xb5\x2f\xfd\x20\x05" + предел, 0))
        self.assertEqual(0, poisk._zstd(b"\x28\xb5\x2f\xfd\x20\x05" + rle[:2], 0))
        self.assertIsNone(poisk._zstd(b"\x28\xb5\x2f\xfd\x20", 0))
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 8):
            self.assertEqual(0, poisk._zstd(данные, 0))


class OleTests(unittest.TestCase):
    def test_виды_документов(self):
        for поток, расширение in (("WordDocument", "doc"), ("Workbook", "xls"), ("Book", "xls"),
                                  ("PowerPoint Document", "ppt"), ("VisioDocument", "vsd"),
                                  ("__substg1.0_0037001F", "msg"), ("Contents", "ole")):
            with self.subTest(поток):
                данные = обр.ole(поток)
                self.assertEqual(("OLE2 (DOC/XLS/PPT/MSG)", расширение, len(данные)), опознать(данные))

    def test_заголовок_и_обрывы(self):
        данные = обр.ole()
        for место, значение in ((0x1C, b"\xfe\xfe"), (0x1A, b"\x04\x00"), (0x1E, b"\x0c\x00")):
            испорчен = bytearray(данные)
            испорчен[место:место + 2] = значение
            with self.subTest(место=hex(место)):
                self.assertIsNone(poisk._ole(bytes(испорчен), 0))
        self.assertIsNone(poisk._ole(данные[:511], 0))
        self.assertEqual(0, poisk._ole(данные[:1000], 0))                 # сектор FAT за концом
        без_fat = bytearray(данные)
        struct.pack_into("<I", без_fat, 0x2C, 0)
        self.assertEqual(0, poisk._ole(bytes(без_fat), 0))
        пустой_fat = bytearray(данные)
        пустой_fat[512:1024] = b"\xff" * 512
        self.assertEqual(0, poisk._ole(bytes(пустой_fat), 0))
        self.assertEqual("ole", poisk._ole_вид(bytes(без_fat), 0))

    def test_сектор_4096_и_цепочка_difat(self):
        # Версия 4: сектор 4096; FAT — 110 секторов: 109 в заголовке, 110-й — в секторе DIFAT.
        сектор, всего_fat = 4096, 110
        заголовок = bytearray(4096)
        заголовок[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        struct.pack_into("<HHHHH", заголовок, 0x18, 0x3E, 4, 0xFFFE, 12, 6)
        struct.pack_into("<IIIIIIIII", заголовок, 0x28, 1, всего_fat, 111, 0, 4096, 0xFFFFFFFE, 0, 110, 1)
        struct.pack_into("<109I", заголовок, 0x4C, *range(109))
        difat = bytearray(b"\xff" * сектор)
        struct.pack_into("<I", difat, 0, 109)
        struct.pack_into("<I", difat, сектор - 4, 0xFFFFFFFE)
        fat = [bytearray(b"\xff" * сектор) for _ in range(всего_fat)]
        struct.pack_into("<I", fat[0], 0, 0xFFFFFFFD)
        struct.pack_into("<I", fat[0], 4 * 111, 0xFFFFFFFE)            # сектор 111 — каталог
        секторы = [bytes(f) for f in fat[:109]] + [bytes(difat), bytes(fat[109])]
        каталог = bytearray(сектор)
        имя = "Workbook\x00".encode("utf-16-le")
        каталог[0:len(имя)] = имя
        struct.pack_into("<HBB", каталог, 64, len(имя), 2, 1)
        данные = bytes(заголовок) + b"".join(секторы) + bytes(каталог)
        self.assertEqual(("OLE2 (DOC/XLS/PPT/MSG)", "xls", len(данные)), опознать(данные))
        self.assertIsNone(poisk._ole_fat(данные[:4096 * 110], 0, сектор))


class RtfTiffTests(unittest.TestCase):
    def test_rtf(self):
        данные = обр.rtf()
        self.assertEqual(len(данные), poisk._rtf(данные + b"}}", 0))
        self.assertIsNone(poisk._rtf(b"{\\rtfX}", 0))
        self.assertEqual(0, poisk._rtf(b"{\\rtf1 {not closed}", 0))
        self.assertEqual(15, poisk._rtf(b"{\\rtf1 \\bin-5 }x}}}}", 0))

    def test_tiff_порядки_плитки_и_цепочка(self):
        данные = обр.tiff()
        self.assertEqual(len(данные), poisk._tiff(данные, 0))
        # MM: IFD1 (8…50) — ширина, смещения плиток LONG×2 по смещению 80, длины плиток SHORT×3 по смещению 88;
        # IFD2 (50…80) — JPEG: смещение 120, длина 30. Конец — дальний: 120 + 30.
        записи1 = [(256, 3, 1, 2 << 16), (324, 4, 2, 80), (325, 3, 3, 88)]
        ifd1 = struct.pack(">H", 3) + b"".join(struct.pack(">HHII", *з) for з in записи1) + struct.pack(">I", 50)
        ifd2 = struct.pack(">H", 2) + struct.pack(">HHII", 513, 4, 1, 120) + struct.pack(">HHII", 514, 4, 1, 30) \
            + struct.pack(">I", 0)
        данные_mm = (b"MM\x00*" + struct.pack(">I", 8) + ifd1 + ifd2 + struct.pack(">II", 100, 110)
                     + struct.pack(">HHH", 5, 7, 9))
        данные_mm += bytes(200 - len(данные_mm))
        self.assertEqual(150, poisk._tiff(данные_mm, 0))
        без_jpeg = bytearray(данные_mm)
        struct.pack_into(">I", без_jpeg, 8 + 42 + 2 + 12 + 8, 0)          # длина JPEG 0
        struct.pack_into(">I", без_jpeg, 8 + 42 + 2 + 8, 0)               # смещение JPEG 0
        self.assertEqual(117, poisk._tiff(bytes(без_jpeg), 0))
        петля = bytearray(данные_mm)
        struct.pack_into(">I", петля, 50 + 2 + 24, 8)                      # следующая за IFD2 — снова IFD1
        self.assertEqual(150, poisk._tiff(bytes(петля), 0))
        длинные_за_концом = bytearray(данные_mm)
        struct.pack_into(">I", длинные_за_концом, 8 + 2 + 12 + 8, 190)     # смещения плиток — за концом данных
        self.assertEqual(max(150, 190 + 8), poisk._tiff(bytes(длинные_за_концом), 0))

    def test_tiff_отказы(self):
        данные = обр.tiff()
        self.assertIsNone(poisk._tiff(b"II*\x00" + struct.pack("<I", 7) + bytes(20), 0))
        self.assertIsNone(poisk._tiff(b"II*\x00\x08", 0))
        self.assertEqual(0, poisk._tiff(данные[:9], 0))
        self.assertEqual(0, poisk._tiff(данные[:40], 0))
        бесконечная = b"II*\x00" + struct.pack("<I", 8) + b"".join(
            struct.pack("<H", 0) + struct.pack("<I", 8 + 6 * (i + 1)) for i in range(300))
        self.assertEqual(0, poisk._tiff(бесконечная, 0))


class МедиаTests(unittest.TestCase):
    def test_riff_и_mp4_по_виду(self):
        self.assertEqual("rmi", poisk._riff_вид(b"RIFF\x00\x00\x00\x00RMID", 0))
        self.assertEqual("riff", poisk._riff_вид(b"RIFF\x00\x00\x00\x00ABCD", 0))
        for марка, расширение in ((b"qt  ", "mov"), (b"M4A ", "m4a"), (b"M4V ", "m4v"), (b"3gp5", "3gp"),
                                  (b"3g2a", "3g2"), (b"heic", "heic"), (b"heix", "heic"), (b"mif1", "heif"),
                                  (b"avif", "avif"), (b"isom", "mp4")):
            with self.subTest(марка):
                self.assertEqual(расширение, poisk._mp4_вид(b"\x00\x00\x00\x18ftyp" + марка, 0))

    def test_кадры_mpeg_аудио(self):
        # MPEG-1 слой III 128 кбит/с 44,1 кГц: 417 (+1 с добивкой); MPEG-2 слой III 64 кбит/с 22,05 кГц: 208;
        # MPEG-2.5 слой III 8 кбит/с 11,025 кГц: 104; слой I MPEG-1 32 кбит/с 48 кГц: 32; слой II 48 кбит/с 32 кГц: 216.
        for заголовок, длина in ((0xFFFB9000, 417), (0xFFFB9200, 418), (0xFFF38000, 208), (0xFFE31000, 52),
                                 (0xFFFF1400, 32), (0xFFFD2800, 216), (0xFFF71000, 68), (0xFFF51000, 52)):
            with self.subTest(hex(заголовок)):
                self.assertEqual(длина, poisk._mpa_кадр(struct.pack(">I", заголовок), 0))
        for плохой in (0xFFDB9000, 0xFFEB9000, 0xFFF99000, 0xFFFBF000, 0xFFFB0000, 0xFFFB9C00):
            with self.subTest(hex(плохой)):
                self.assertIsNone(poisk._mpa_кадр(struct.pack(">I", плохой), 0))
        self.assertIsNone(poisk._mpa_кадр(b"\xff\xfb\x90", 0))

    def test_mp3_цепочка_и_id3(self):
        кадр = b"\xff\xfb\x90\x00" + bytes(413)
        self.assertIsNone(poisk._mp3(кадр * 3 + b"xx", 0))
        self.assertEqual(4 * 417, poisk._mp3(кадр * 4, 0))
        self.assertEqual(4 * 417 + 128, poisk._mp3(кадр * 4 + b"TAG" + bytes(125), 0))
        self.assertEqual(4 * 417, poisk._mp3(кадр * 4 + b"TAG" + bytes(124), 0))
        self.assertEqual(0, poisk._mp3(кадр * 4 + кадр[:100], 0))
        self.assertIsNone(poisk._mp3(кадр * 2 + кадр[:100], 0))
        тег = b"ID3\x03\x00\x10" + bytes([0, 0, 1, 0]) + bytes(128 + 10)
        self.assertEqual(len(тег) + 417, poisk._mp3_id3(тег + кадр, 0))
        self.assertEqual(0, poisk._mp3_id3(тег, 0))
        self.assertEqual(0, poisk._mp3_id3(тег + кадр[:100], 0))
        self.assertIsNone(poisk._mp3_id3(тег + b"no frames here", 0))
        for i, байт in ((3, 1), (3, 5), (4, 0xFF), (6, 0x80), (9, 0x80)):
            плохой = bytearray(тег + кадр)
            плохой[i] = байт
            with self.subTest(i=i, байт=байт):
                self.assertIsNone(poisk._mp3_id3(bytes(плохой), 0))
        self.assertIsNone(poisk._mp3_id3(b"ID3\x03\x00", 0))

    def test_ebml(self):
        self.assertEqual((0x1A45DFA3, 4), poisk._ebml_число(b"\x1a\x45\xdf\xa3", 0, id_=True))
        self.assertEqual((2, 1), poisk._ebml_число(b"\x82", 0))
        self.assertEqual((-1, 1), poisk._ebml_число(b"\xff", 0))
        self.assertEqual((5, 8), poisk._ebml_число(b"\x01" + bytes(6) + b"\x05", 0))
        for плохое in (b"\x00", b"\x40"):
            with self.subTest(плохое), self.assertRaises((ValueError, IndexError)):
                poisk._ebml_число(плохое, 0)
        данные = обр.webm()                     # заголовок EBML — 14 байт, Segment — 4 + 2 + 53
        self.assertEqual(73, len(данные))
        self.assertEqual(len(данные), poisk._ebml(данные, 0))
        self.assertEqual(len(данные), poisk._ebml(данные[:24], 0))   # длина известна из заголовка Segment
        неизвестный = данные[:14] + b"\x18\x53\x80\x67\x01\xff\xff\xff\xff\xff\xff\xff" + bytes(10)
        self.assertEqual(0, poisk._ebml(неизвестный, 0))
        self.assertIsNone(poisk._ebml(данные[:14] + b"\x1f\x43\xb6\x75\x80", 0))      # не Segment
        self.assertEqual(0, poisk._ebml(данные[:17], 0))
        self.assertEqual(18 + 1 + 5, poisk._ebml(данные[:14] + b"\x18\x53\x80\x67\x85", 0))
        self.assertIsNone(poisk._ebml(данные[:14] + b"\x18\x53\x80\x67\x00", 0))
        self.assertIsNone(poisk._ebml(b"\x1a\x45\xdf\xa3\xff", 0))
        self.assertIsNone(poisk._ebml(b"\x1a\x45\xdf\xa3\x00", 0))
        mkv = b"\x1a\x45\xdf\xa3\x8b\x42\x82\x88matroska" + b"\x18\x53\x80\x67\x80"
        self.assertEqual("mkv", poisk._ebml_вид(mkv, 0))
        self.assertEqual("mkv", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x80", 0))
        self.assertEqual("mkv", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x80\x42\x82\x00", 0))

    def test_flv(self):
        данные = обр.flv()
        self.assertEqual(len(данные), poisk._flv(данные, 0))
        self.assertEqual(len(данные), poisk._flv(данные + b"\x07junk" + bytes(20), 0))  # чужая метка — конец
        self.assertEqual(0, poisk._flv(данные + b"\x09\x00", 0))
        self.assertEqual(0, poisk._flv(данные + b"\x09\x00\x00\x20" + bytes(7) + b"xx", 0))
        с_потоком = данные + b"\x09\x00\x00\x01" + bytes(3) + b"\x00" + b"\x00\x00\x01" + b"x" + struct.pack(">I", 12)
        self.assertEqual(len(данные), poisk._flv(с_потоком, 0))
        неверная = bytearray(данные)
        struct.pack_into(">I", неверная, len(данные) - 4, 99)
        self.assertEqual(len(данные) - 4 - 11 - 22, poisk._flv(bytes(неверная), 0))
        for место, байт in ((4, 0x02), (4, 0x08)):
            плохой = bytearray(данные)
            плохой[место] = байт
            with self.subTest(место=место, байт=байт):
                self.assertIsNone(poisk._flv(bytes(плохой), 0))
        сдвиг = bytearray(данные)
        struct.pack_into(">I", сдвиг, 5, 8)
        self.assertIsNone(poisk._flv(bytes(сдвиг), 0))
        первый = bytearray(данные)
        первый[12] = 1
        self.assertIsNone(poisk._flv(bytes(первый), 0))
        self.assertIsNone(poisk._flv(данные[:12], 0))
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 20):
            self.assertEqual(0, poisk._flv(данные, 0))

    def test_ts(self):
        self.assertEqual(5 * 188, poisk._ts(обр.ts(5), 0))
        self.assertIsNone(poisk._ts(обр.ts(4), 0))
        self.assertEqual(5 * 188, poisk._ts(обр.ts(5) + b"\x47" + bytes(100), 0))
        self.assertEqual(5 * 188, poisk._ts(обр.ts(5) + b"\x00" + bytes(187) + обр.ts(2), 0))
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 5 * 188):
            self.assertEqual(5 * 188, poisk._ts(обр.ts(9), 0))

    def test_annex_b(self):
        h264 = b"\x00\x00\x00\x01\x67\x42\x00\x1e" + b"\x00\x00\x01\x68\xce"
        self.assertEqual(("H.264 (Annex B)", "h264", 0), опознать(h264))
        self.assertIsNone(poisk.опознать(b"\x00\x00\x00\x01\xe7\x42" + b"\x00\x00\x01\x68\xce"))   # запретный бит
        self.assertIsNone(poisk.опознать(b"\x00\x00\x00\x01\x67\x42" + b"\x00\x00\x01\x65\xce"))   # не PPS
        self.assertIsNone(poisk.опознать(b"\x00\x00\x00\x01\x67\x42\x00\x00\x01"))                  # PPS не виден
        self.assertIsNone(poisk.опознать(b"\x00\x00\x00\x01\x67" + bytes(600) + b"\x00\x00\x01\x68"))
        h265 = b"\x00\x00\x00\x01\x40\x01\x0c" + b"\x00\x00\x01\x42\x01"
        self.assertEqual(("H.265 (Annex B)", "h265", 0), опознать(h265))
        self.assertIsNone(poisk.опознать(b"\x00\x00\x00\x01\x40\x01\x0c" + b"\x00\x00\x01\x44\x01"))


class ИсполняемыеОбразыTests(unittest.TestCase):
    def test_macho_32_и_64_оба_порядка(self):
        for порядок, магия, шире in (("<", 0xFEEDFACE, False), (">", 0xFEEDFACE, False), ("<", 0xFEEDFACF, True),
                                     (">", 0xFEEDFACF, True)):
            if шире:
                команда = struct.pack(порядок + "II16sQQQQiiII", 0x19, 72, b"__TEXT", 0, 0, 0, 3000, 5, 5, 0, 0)
            else:
                команда = struct.pack(порядок + "II16sIIIIiiII", 0x1, 56, b"__TEXT", 0, 0, 0, 3000, 5, 5, 0, 0)
            подпись = struct.pack(порядок + "IIII", 0x1D, 16, 3000, 500)
            заголовок = struct.pack(порядок + "IiiIIII", магия, 7, 3, 2, 2, len(команда) + 16, 0) + (
                b"\x00" * 4 if шире else b"")
            данные = заголовок + команда + подпись
            данные += bytes(3500 - len(данные))
            with self.subTest(порядок=порядок, шире=шире):
                self.assertEqual(("Mach-O", "macho", 3500), опознать(данные))
                self.assertEqual(0, poisk._macho(данные[:len(заголовок) + 20], 0))

    def test_macho_отказы(self):
        данные = обр.macho()
        for место, значение in ((16, 0), (16, 4096), (20, 0), (20, 1 << 24)):
            плохой = bytearray(данные)
            struct.pack_into("<I", плохой, место, значение)
            with self.subTest(место=место, значение=значение):
                self.assertIsNone(poisk._macho(bytes(плохой), 0))
        короткая = bytearray(данные)
        struct.pack_into("<I", короткая, 36, 7)
        self.assertIsNone(poisk._macho(bytes(короткая), 0))
        self.assertIsNone(poisk._macho(данные[:31], 0))
        без_сегментов = bytearray(данные)
        struct.pack_into("<I", без_сегментов, 32, 0x2)
        self.assertEqual(32 + 72, poisk._macho(bytes(без_сегментов), 0))

    def test_macho_fat(self):
        данные = обр.macho_fat()
        self.assertEqual(len(данные), poisk._macho_fat(данные, 0))
        for число in (0, 20):
            плохой = bytearray(данные)
            struct.pack_into(">I", плохой, 4, число)
            with self.subTest(число=число):
                self.assertIsNone(poisk._macho_fat(bytes(плохой), 0))
        self.assertEqual(("Mach-O (универсальный)", "macho", len(данные)), опознать(данные))
        внутри = bytearray(данные)
        struct.pack_into(">I", внутри, 16, 27)
        self.assertIsNone(poisk._macho_fat(bytes(внутри), 0))
        struct.pack_into(">I", внутри, 16, 28)
        self.assertEqual(28 + 4096, poisk._macho_fat(bytes(внутри), 0))
        self.assertEqual(0, poisk._macho_fat(данные[:20], 0))
        self.assertIsNone(poisk._macho_fat(данные[:7], 0))
        java = b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + bytes(40)
        self.assertIsNone(poisk._macho_fat(java, 0))

    def test_iso(self):
        данные = обр.iso()
        self.assertEqual(len(данные), poisk._iso(данные, 32769))
        for место, значение in ((32768, 2), (32774, 2)):
            плохой = bytearray(данные)
            плохой[место] = значение
            with self.subTest(место=место):
                self.assertIsNone(poisk._iso(bytes(плохой), 32769))
        for место, упаковка, значение in ((32768 + 84, ">I", 21), (32768 + 130, ">H", 1024)):
            плохой = bytearray(данные)
            struct.pack_into(упаковка, плохой, место, значение)
            with self.subTest(место=место):
                self.assertIsNone(poisk._iso(bytes(плохой), 32769))
        for блок in (512, 1024, 4096):
            другой = bytearray(данные)
            struct.pack_into("<H", другой, 32768 + 128, блок)
            struct.pack_into(">H", другой, 32768 + 130, блок)
            with self.subTest(блок=блок):
                self.assertEqual(None if блок == 4096 else 20 * блок, poisk._iso(bytes(другой), 32769))
        self.assertIsNone(poisk._iso(данные[:32768 + 130], 32769))
        self.assertIsNone(poisk._iso(данные[1:], 32768))

    def test_pem(self):
        for метка, расширение in (("CERTIFICATE", "crt"), ("X509 CRL", "crl"), ("CERTIFICATE REQUEST", "csr"),
                                  ("PRIVATE KEY", "key"), ("EC PRIVATE KEY", "key"), ("PKCS7", "pem")):
            данные = обр.pem(метка)
            with self.subTest(метка):
                self.assertEqual(("PEM", расширение, len(данные)), опознать(данные))
        разные = обр.pem("CERTIFICATE").replace(b"END CERTIFICATE", b"END PRIVATE KEY")
        self.assertIsNone(poisk._pem(разные, 0))
        self.assertEqual(len(обр.pem()) - 2, poisk._pem(обр.pem()[:-2], 0))


class ZipPdfTests(unittest.TestCase):
    def test_заголовок_члена_не_отдельный_архив(self):
        данные = обр.zip_([("a.txt", b"a" * 100), ("b.txt", b"b" * 100)])
        второй = данные.index(b"PK\x03\x04", 4)
        self.assertIsNone(poisk._zip(данные, второй))
        self.assertEqual([(37, len(данные), "zip")], в_потоке(данные, "ZIP"))
        zip64 = bytearray(данные)
        конец = данные.rindex(b"PK\x05\x06")
        struct.pack_into("<I", zip64, конец + 16, 0xFFFFFFFF)
        self.assertEqual(len(данные) - второй, poisk._zip(bytes(zip64), второй))
        struct.pack_into("<II", zip64, конец + 12, 0xFFFFFFFF, 5)
        self.assertEqual(len(данные) - второй, poisk._zip(bytes(zip64), второй))

    def test_вид_zip(self):
        def с_mimetype(тип):
            буфер = io.BytesIO()
            with zipfile.ZipFile(буфер, "w") as z:
                z.writestr(zipfile.ZipInfo("mimetype"), тип)
            return буфер.getvalue()
        for тип, расширение in (("application/vnd.oasis.opendocument.spreadsheet", "ods"),
                                ("application/vnd.oasis.opendocument.presentation", "odp"),
                                ("application/vnd.oasis.opendocument.graphics", "odg"),
                                ("application/epub+zip", "epub"), ("application/x-другое", "zip")):
            with self.subTest(тип):
                self.assertEqual(расширение, poisk._zip_вид(с_mimetype(тип), 0))
        self.assertEqual("vsdx", poisk._zip_вид(обр.zip_([("[Content_Types].xml", "x"), ("visio/a.xml", "x")]), 0))
        self.assertEqual("zip", poisk._zip_вид(обр.zip_([("word/document.xml", "x")]), 0))
        self.assertEqual("zip", poisk._zip_вид(обр.zip_([("[Content_Types].xml", "x"), ("other/a", "x")]), 0))
        self.assertEqual("jar", poisk._zip_вид(обр.zip_([("META-INF/MANIFEST.MF", "x")]), 0))
        self.assertEqual("apk", poisk._zip_вид(обр.zip_([("AndroidManifest.xml", "x")]), 0))
        дальний = bytes(1 << 16) + обр.zip_([("[Content_Types].xml", "x"), ("xl/a", "x")])
        self.assertEqual("zip", poisk._zip_вид(дальний[:1 << 16] + обр.zip_([("a", "b")]) + дальний[1 << 16:], 0))

    def test_pdf_конец_строки(self):
        for хвост, длина in ((b"\r\n", 2), (b"\r", 1), (b"\n", 1), (b"", 0), (b" ", 0)):
            данные = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF" + хвост
            with self.subTest(хвост=хвост):
                self.assertEqual(len(данные) - len(хвост) + длина, poisk._pdf(данные + b"zz", 0))


class ЧастиФайлаTests(unittest.TestCase):
    def test_части_tar_mp3_ts_не_отдельные_файлы(self):
        for данные, что in ((обр.tar_([("a", bytes(600)), ("b", bytes(10))]), "tar"),
                            (обр.mp3(8, id3=False), "MP3"), (обр.ts(6) + обр.ts(6), "MPEG-TS")):
            with self.subTest(что):
                self.assertEqual([(37, len(данные))], [(м, д) for м, д, _ in в_потоке(данные, что)])
        # Встроенный файл другого вида — остаётся отдельной находкой.
        встроен = обр.zip_([("a.jpg", обр.jpeg())])
        буфер = io.BytesIO()
        with zipfile.ZipFile(буфер, "w", zipfile.ZIP_STORED) as z:
            z.writestr("a.jpg", обр.jpeg())
        self.assertTrue(в_потоке(буфер.getvalue(), "JPEG"))
        self.assertTrue(в_потоке(встроен, "ZIP"))
        # Два tar подряд — два файла.
        два = обр.tar_([("a", b"1")]) + обр.tar_([("b", b"2")])
        self.assertEqual([(37, 10240), (37 + 10240, 10240)], [(м, д) for м, д, _ in в_потоке(два, "tar")])


class ОпознатьОшибкиTests(unittest.TestCase):
    def test_исключение_проверки_пропускает_формат(self):
        def ломается(д, м):
            raise struct.error("нет")
        формат = poisk.Формат("Ломкий", "x", b"ZZZZ", ломается)
        with mock.patch.object(poisk, "ФОРМАТЫ", [формат] + poisk.ФОРМАТЫ):
            self.assertEqual(("PNG", "png", len(обр.png())), опознать(обр.png()))
            self.assertIsNone(poisk.опознать(b"ZZZZ...."))
            self.assertEqual([], poisk.сигнатуры_в_байтах(b"..ZZZZ.."))

    def test_crc_rar_проверяется_по_16_битам(self):
        self.assertEqual(zlib.crc32(b"x") & 0xFFFF, zlib.crc32(b"x") & 0xFFFF)


# == Мутанты: поля и границы, которые обычные образцы не различают ==================================

def нулевой_блок(данные):
    """Номер первого нулевого блока tar."""
    return next(i for i in range(len(данные) // 512) if not данные[i * 512:(i + 1) * 512].strip(b"\x00"))


def заголовок_tar(сумма="%06o\x00 ", размер=b"00000000003\x00", время=b"14000000000\x00", имя=b"a.txt",
                  ссылка=b""):
    з = bytearray(512)
    з[0:len(имя)] = имя
    з[124:136] = размер
    з[136:148] = время
    з[156] = ord("0")
    з[157:157 + len(ссылка)] = ссылка
    з[257:265] = b"ustar\x0000"
    з[148:156] = b" " * 8
    з[148:156] = (сумма % sum(з)).encode()
    return bytes(з)


class МутантыОбщиеTests(unittest.TestCase):
    def test_далеко_в_потоке_при_пределе_в_длину_образца(self):
        # Файл в потоке далеко от начала, предел вырезаемого — его же длина (с запасом на хвост):
        # обходы «пока место − начало < предела» не должны зависеть от места в потоке.
        for имя, данные, что, расширение in обр.все():
            with self.subTest(имя), mock.patch.object(poisk, "ФАЙЛ_ДО", len(данные) + 100):
                до = b"\x00" * (2 * len(данные) + 1000)
                self.assertEqual([(len(до), len(данные), расширение)], в_потоке(данные, что, до=до))


class МутантыTarTests(unittest.TestCase):
    def test_сумма_и_длина_значащими_цифрами(self):
        длинные = dict(имя=b"\xff" * 100, ссылка=b"\xff" * 100)             # сумма ≥ 0o100000: 6 значащих цифр
        self.assertEqual(3, poisk._tar_заголовок(заголовок_tar(**длинные), 0))
        self.assertEqual(3, poisk._tar_заголовок(заголовок_tar("  %06o", **длинные), 0))   # без нуля в конце
        self.assertEqual(3, poisk._tar_заголовок(заголовок_tar(время=b"14000000000 "), 0))  # байт 147 — пробел
        self.assertEqual(8 ** 11, poisk._tar_заголовок(заголовок_tar(размер=b"100000000000"), 0))
        self.assertEqual(512, poisk._tar_заголовок(заголовок_tar(размер=b"000000001000"), 0))

    def test_версия_после_ustar_при_верной_сумме(self):
        з = bytearray(заголовок_tar())
        з[262] = ord("x")
        з[148:156] = b" " * 8
        з[148:156] = b"%06o\x00 " % sum(з)
        данные = bytes(з) + b"abc" + bytes(509) + bytes(1024)
        self.assertIsNotNone(poisk._tar_заголовок(bytes(з), 0))
        self.assertIsNone(poisk._tar(данные, 257))
        self.assertEqual(4 * 512, poisk._tar(заголовок_tar() + b"abc" + bytes(509) + bytes(1024), 257))

    def test_второй_нулевой_блок(self):
        данные = обр.tar_([("x.txt", b"abc")])
        н = нулевой_блок(данные)
        второй = bytearray(данные)
        второй[(н + 1) * 512 + 3] = 1
        self.assertEqual(0, poisk._tar(bytes(второй), 257))
        self.assertEqual(0, poisk._tar(данные[:(н + 1) * 512 + 100], 257))      # второй нулевой оборван
        self.assertEqual((н + 2) * 512, poisk._tar(данные[:(н + 2) * 512] + b"tail", 257))
        self.assertEqual((н + 2) * 512, poisk._tar(данные[:(н + 2) * 512], 257))


class МутантыRarTests(unittest.TestCase):
    ГЛАВНЫЙ = b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0, bytes(6))
    КОНЕЦ = обр._rar4_блок(0x7B, 0x4000, b"")

    def test_rar4_виды_блоков_на_границах(self):
        self.assertEqual(0, poisk._rar4(обр.rar4()[:20 + 6], 0))                  # от блока — 6 байт из 7
        for вид, итог in ((0x72, True), (0x71, False), (0x7C, False)):
            данные = self.ГЛАВНЫЙ + обр._rar4_блок(вид, 0, b"") + self.КОНЕЦ
            with self.subTest(вид=hex(вид)):
                self.assertEqual(len(данные) if итог else 0, poisk._rar4(данные, 0))

    def test_rar4_флаги(self):
        пароль = b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0x0080, bytes(6)) + self.КОНЕЦ
        self.assertEqual(0, poisk._rar4(пароль, 0))
        с_флагом = b"Rar!\x1a\x07\x00" + обр._rar4_блок(0x73, 0x0001, bytes(6)) + self.КОНЕЦ
        self.assertEqual(len(с_флагом), poisk._rar4(с_флагом, 0))
        комментарий = обр._rar4_блок(0x75, 0x0080, b"") + обр._rar4_блок(0x75, 0x0001, b"\x05\x00\x00\x00")
        данные = self.ГЛАВНЫЙ + комментарий + self.КОНЕЦ
        self.assertEqual(len(данные), poisk._rar4(данные, 0))

    def test_rar4_файл_без_флага_и_большой(self):
        def файл(флаги, атрибуты=0x20, старшие=b""):
            тело = struct.pack("<IIBIIBBHI", 3, 3, 0, 0, 0, 20, 0x30, 5, атрибуты) + старшие + b"b.txt"
            return обр._rar4_блок(0x74, флаги, тело) + b"abc"
        for флаги, атрибуты, старшие in ((0, 0x20, b""), (0x8001, 0x20, b""),
                                         (0x8100, 0x20000000, struct.pack("<II", 0, 1))):
            данные = self.ГЛАВНЫЙ + файл(флаги, атрибуты, старшие) + self.КОНЕЦ
            with self.subTest(флаги=hex(флаги)):
                self.assertEqual(len(данные), poisk._rar4(данные, 0))

    def test_rar5_границы_и_виды(self):
        подпись = b"Rar!\x1a\x07\x01\x00"
        self.assertIsNone(poisk._rar5(подпись + bytes(5), 0))
        данные = обр.rar5()
        первый = 4 + 1 + данные[12]
        self.assertEqual(0, poisk._rar5(данные[:8 + первый - 1], 0))
        служебный = обр._rar5_блок(3, 0x0002, обр._vint(3) + b"\x00\x01", b"DAT")
        данные = подпись + обр._rar5_блок(1, 0, обр._vint(0)) + служебный + обр._rar5_блок(5, 0, обр._vint(0))
        self.assertEqual(len(данные), poisk._rar5(данные, 0))
        доп = подпись + обр._rar5_блок(1, 0x0001, обр._vint(3) + b"xyz") + обр._rar5_блок(5, 0, обр._vint(0))
        self.assertEqual(len(доп), poisk._rar5(доп, 0))


class МутантыZstdTests(unittest.TestCase):
    def test_границы(self):
        self.assertEqual(0, poisk._zstd(b"\x28\xb5\x2f\xfd\x20\x05", 0))
        блок = ((5 << 3) | 1).to_bytes(3, "little") + b"abcde"
        данные = b"\x28\xb5\x2f\xfd\x41" + b"\x40\x07\x00\x01" + блок                # окно, DID 1, FCS 2
        self.assertEqual(len(данные), poisk._zstd(данные, 0))
        сырой = (10 << 3).to_bytes(3, "little") + bytes(10)
        плохой = (3 << 1).to_bytes(3, "little")
        with mock.patch.object(poisk, "ФАЙЛ_ДО", 19):
            self.assertEqual(0, poisk._zstd(b"\x28\xb5\x2f\xfd\x20\x05" + сырой + плохой, 0))


def ole_собрать(секторы, *, всего_fat, в_заголовке, каталог=0xFFFFFFFE, difat=0xFFFFFFFE, difat_число=0):
    """Составной документ версии 3 (сектор 512) из готовых секторов; сектор n лежит с (n + 1)·512."""
    список = list(в_заголовке) + [0xFFFFFFFF] * (109 - len(в_заголовке))
    заголовок = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(16) + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
                 + bytes(6) + struct.pack("<IIIIIIIII", 0, всего_fat, каталог, 0, 4096, 0xFFFFFFFE, 0, difat,
                                          difat_число) + struct.pack("<109I", *список))
    return заголовок + b"".join(секторы)


def сектор_fat(записи):
    return struct.pack("<128I", *(list(записи) + [0xFFFFFFFF] * (128 - len(записи))))


def запись_каталога(имя, длина=None):
    имя16 = (имя + "\x00").encode("utf-16-le")
    return имя16 + bytes(64 - len(имя16)) + struct.pack("<HBB", len(имя16) if длина is None else длина, 2, 1) + bytes(60)


def сектор_каталога(*имена):
    return b"".join(запись_каталога(и) for и in имена) + bytes(128 * (4 - len(имена)))


class МутантыOleTests(unittest.TestCase):
    ПУСТО = 0xFFFFFFFF
    КОНЕЦ = 0xFFFFFFFE

    def test_границы_данных(self):
        данные = обр.ole()
        self.assertEqual(0, poisk._ole(данные[:512], 0))
        self.assertEqual(len(данные), poisk._ole(данные[:1024], 0))     # длина — по FAT, данных дальше может не быть
        self.assertEqual("doc", poisk._ole_вид(данные[:1536], 0))
        for длина in (1535, 1024 + 200):
            with self.subTest(длина=длина):
                self.assertEqual("ole", poisk._ole_вид(данные[:длина], 0))

    def test_номера_fat_сверх_числа(self):
        данные = bytearray(обр.ole())
        struct.pack_into("<I", данные, 0x4C + 4, 7)                     # в списке второй номер, а FAT — один сектор
        self.assertEqual(len(данные), poisk._ole(bytes(данные), 0))

    def test_последний_занятый_в_секторе_fat(self):
        for секторов in (110, 125, 126):
            данные = обр.ole(размер=512 * секторов)
            with self.subTest(секторов=секторов):
                self.assertEqual(len(данные), poisk._ole(данные, 0))
        только_fat = ole_собрать([сектор_fat([0xFFFFFFFD])], всего_fat=1, в_заголовке=[0])
        self.assertEqual(1024, poisk._ole(только_fat, 0))

    def test_второй_сектор_fat(self):
        fat0 = сектор_fat([0xFFFFFFFD, 0xFFFFFFFD, self.КОНЕЦ])
        fat1 = сектор_fat([self.КОНЕЦ])                                # занят только сектор 128
        данные = ole_собрать([fat0, fat1, сектор_каталога("Root Entry")], всего_fat=2, в_заголовке=[0, 1], каталог=2)
        self.assertEqual(130 * 512, poisk._ole(данные, 0))

    def test_difat_за_концом_и_не_нужен(self):
        fat = сектор_fat([0xFFFFFFFD, self.КОНЕЦ])
        каталог = сектор_каталога("Root Entry", "WordDocument")
        for всего, в_заголовке, difat, число, итог in ((1, [0], 50, 0, 1536), (1, [0], 50, 1, 1536),
                                                       (2, [0], self.КОНЕЦ, 1, 1536), (2, [0], 50, 1, 0)):
            данные = ole_собрать([fat, каталог], всего_fat=всего, в_заголовке=в_заголовке, каталог=1, difat=difat,
                                 difat_число=число)
            with self.subTest(всего=всего, difat=difat, число=число):
                self.assertEqual(итог, poisk._ole(данные, 0))

    def test_цепочка_difat_из_двух_секторов(self):
        # FAT — 240 секторов (0…239), DIFAT — 240 и 241, каталог — 242. В заголовке 109 номеров, в первом DIFAT —
        # 127 и ссылка на второй, во втором — 4 (и мусорный ноль сверх нужного числа). В последнем секторе FAT
        # занята запись 0: длина по FAT — (239·128 + 2)·512, хотя данных меньше.
        fat = [сектор_fat([]) for _ in range(240)]
        fat[0] = сектор_fat([0xFFFFFFFD] * 128)
        fat[1] = сектор_fat([0xFFFFFFFD] * 112 + [0xFFFFFFFC, 0xFFFFFFFC, self.КОНЕЦ])
        fat[239] = сектор_fat([self.КОНЕЦ])
        difat1 = struct.pack("<128I", *range(109, 236), 241)
        difat2 = struct.pack("<128I", 236, 237, 238, 239, 0, *([self.ПУСТО] * 122), self.КОНЕЦ)
        секторы = fat + [difat1, difat2, сектор_каталога("Root Entry", "Workbook")]
        данные = ole_собрать(секторы, всего_fat=240, в_заголовке=range(109), каталог=242, difat=240, difat_число=2)
        self.assertEqual((239 * 128 + 2) * 512, poisk._ole(данные, 0))
        self.assertEqual("xls", poisk._ole_вид(данные, 0))
        self.assertEqual(list(range(240)), poisk._ole_fat(данные, 0, 512))
        self.assertEqual(list(range(236)), poisk._ole_fat(данные[:(242 + 1) * 512], 0, 512)[:236])
        self.assertIsNone(poisk._ole_fat(данные[:(241 + 1) * 512 - 1], 0, 512))
        # Число секторов DIFAT в заголовке — 1: второй не читается, хоть ссылка на него и есть.
        одна = bytearray(данные)
        struct.pack_into("<I", одна, 0x48, 1)
        self.assertEqual(list(range(236)), poisk._ole_fat(bytes(одна), 0, 512))
        self.assertEqual((242 + 2) * 512, poisk._ole(bytes(одна), 0))

    def test_каталог_цепочкой_через_второй_сектор_fat(self):
        # Каталог: 1 → 130 → 131; следующий за 1 — в FAT0, за 130 — в FAT1 (сектор 2), имя — только в 131.
        fat0 = сектор_fat([0xFFFFFFFD, 130, 0xFFFFFFFD])
        fat1 = сектор_fat([self.ПУСТО, self.ПУСТО, 131, self.КОНЕЦ])
        секторы = [fat0, сектор_каталога("Root Entry", "Contents"), fat1] + [bytes(512)] * 127
        секторы += [сектор_каталога("Ole"), сектор_каталога("WordDocument")]
        данные = ole_собрать(секторы, всего_fat=2, в_заголовке=[0, 2], каталог=1)
        self.assertEqual("doc", poisk._ole_вид(данные, 0))
        self.assertEqual("doc", poisk._ole_вид(b"\x00" * 37 + данные, 37))
        петля = bytearray(данные)
        struct.pack_into("<I", петля, 512 + 4, 1)                       # 1 → 1: цепочка замкнута
        self.assertEqual("ole", poisk._ole_вид(bytes(петля), 0))

    def test_читается_только_каталог(self):
        # Каталог — сектор 3; в секторах 1, 2 и 4 — записи с «WordDocument», но это не каталог.
        чужой = сектор_каталога("WordDocument")
        fat = сектор_fat([0xFFFFFFFD, self.КОНЕЦ, self.КОНЕЦ, self.КОНЕЦ, self.КОНЕЦ])
        данные = ole_собрать([fat, чужой, чужой, сектор_каталога("Root Entry", "Contents"), чужой],
                             всего_fat=1, в_заголовке=[0], каталог=3)
        self.assertEqual("ole", poisk._ole_вид(данные, 0))
        self.assertEqual("ole", poisk._ole_вид(b"\x00" * 37 + данные, 37))

    def test_длина_имени_больше_64(self):
        fat = сектор_fat([0xFFFFFFFD, self.КОНЕЦ])
        каталог = запись_каталога("Root Entry") + запись_каталога("WordDocument", длина=66) + bytes(256)
        данные = ole_собрать([fat, каталог], всего_fat=1, в_заголовке=[0], каталог=1)
        self.assertEqual("doc", poisk._ole_вид(данные, 0))


def tiff_из(записи, хвост=b"", ifd=8, до_ifd=b""):
    """II: записи (тег, вид, число, поле) в IFD по смещению ifd, перед ним — до_ifd, за ним — хвост."""
    return (b"II*\x00" + struct.pack("<I", ifd) + до_ifd + struct.pack("<H", len(записи))
            + b"".join(struct.pack("<HHII", *з) for з in записи) + struct.pack("<I", 0) + хвост)


class МутантыTiffTests(unittest.TestCase):
    def test_границы_заголовка_и_ifd_в_конце(self):
        self.assertEqual(0, poisk._tiff(b"II*\x00" + struct.pack("<I", 8), 0))
        self.assertIsNone(poisk._tiff(b"II*\x00\x08\x00\x00", 0))
        данные = tiff_из([(273, 4, 1, 8), (279, 4, 1, 4)], ifd=12, до_ifd=b"\x10\x20\x30\x40")
        self.assertEqual(42, len(данные))
        self.assertEqual(42, poisk._tiff(данные, 0))
        self.assertEqual(0, poisk._tiff(данные[:-1], 0))
        self.assertEqual(0, poisk._tiff(b"\x00" * 37 + данные[:40], 37))

    def test_размеры_значений(self):
        self.assertEqual(206, poisk._tiff(tiff_из([(300, 99, 6, 200)]), 0))       # неизвестный вид — 1 байт
        self.assertEqual(305, poisk._tiff(tiff_из([(301, 1, 5, 300)]), 0))        # 5 байт — уже по смещению
        self.assertEqual(38, poisk._tiff(tiff_из([(273, 1, 1, 200), (279, 4, 1, 10)]), 0))  # BYTE — не кусок

    def test_массивы_кусков_у_конца_данных(self):
        описание = tiff_из([(270, 2, 30, 38)], b"x" * 30)
        self.assertEqual(68, poisk._tiff(описание, 0))
        впритык = tiff_из([(273, 4, 2, 38), (279, 4, 2, 46)], struct.pack("<IIII", 100, 200, 10, 20))
        self.assertEqual(220, poisk._tiff(впритык, 0))
        за_концом = tiff_из([(273, 4, 3, 38), (279, 4, 1, 5)], struct.pack("<II", 100, 200))
        self.assertEqual(50, poisk._tiff(за_концом, 0))
        self.assertEqual(50, poisk._tiff(b"\x00" * 37 + за_концом, 37))


class МутантыMpegTests(unittest.TestCase):
    def test_кадры_с_особыми_полями(self):
        # Бит 0 (выделение), частота 48 кГц, добивка у слоёв I и II.
        for заголовок, длина in ((0xFFFB9001, 417), (0xFFFB9400, 384), (0xFFFF1600, 36), (0xFFFD2A00, 217)):
            with self.subTest(hex(заголовок)):
                self.assertEqual(длина, poisk._mpa_кадр(struct.pack(">I", заголовок), 0))

    def test_id3_границы_и_поля(self):
        кадр = b"\xff\xfb\x90\x00" + bytes(413)
        self.assertEqual(0, poisk._mp3_id3(b"ID3\x03\x00\x00\x00\x00\x00\x00", 0))
        self.assertIsNone(poisk._mp3_id3(b"ID3\x03\x00\x00\x00\x00\x00", 0))
        for заголовок, тело in ((b"ID3\x02\x00\x00\x00\x00\x00\x01", b"\x00"),     # версия 2.2
                                (b"ID3\x03\x00\x80\x00\x00\x00\x01", b"\x00"),     # флаг «несинхронизация»
                                (b"ID3\x03\x00\x00\x00\x00\x00\x01", b"\xff")):    # тело с байта ≥ 0x80
            with self.subTest(заголовок=заголовок, тело=тело):
                self.assertEqual(11 + 417, poisk._mp3_id3(заголовок + тело + кадр, 0))
        # Размер — все четыре байта по 7 бит: 1·2²¹ + 2·2¹⁴ + 3·2⁷ + 4; флаг 0x01 — не «подвал».
        размер = (1 << 21) + (2 << 14) + (3 << 7) + 4
        тег = b"ID3\x04\x00\x01\x01\x02\x03\x04" + bytes(размер)
        self.assertEqual(len(тег) + 417, poisk._mp3_id3(тег + кадр, 0))


class МутантыEbmlTests(unittest.TestCase):
    def test_числа_и_размеры(self):
        self.assertEqual((-1, 2), poisk._ebml_число(b"\x7f\xff", 0))
        self.assertEqual((0x3FFE, 2), poisk._ebml_число(b"\x7f\xfe", 0))
        данные = обр.webm()
        self.assertEqual(11, poisk._ebml(b"\x1a\x45\xdf\xa3\x80" + b"\x18\x53\x80\x67\x81\x00", 0))
        self.assertIsNone(poisk._ebml(данные[:14] + b"\x18\x53\x80\x67", 0))
        self.assertEqual(19, poisk._ebml(данные[:14] + b"\x18\x53\x80\x67\x80", 0))

    def test_doctype_среди_элементов_заголовка(self):
        webm = b"\x42\x82\x84webm"
        self.assertEqual("webm", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x8a\x42\x86\x80" + webm, 0))
        self.assertEqual("webm", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x40\x07" + webm, 0))
        self.assertEqual("mkv", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x80" + webm, 0))            # за заголовком
        self.assertEqual("mkv", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84matr", 0))
        self.assertEqual("mkv", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x87\x42\x82\x84we", 0))
        self.assertEqual("mkv", poisk._ebml_вид(b"\x1a\x45\xdf\xa3\x85\x42\x86\xff\x42\x82", 0))


class МутантыFlvTests(unittest.TestCase):
    def метка(self, вид, данные):
        return bytes([вид]) + len(данные).to_bytes(3, "big") + bytes(7) + данные + struct.pack(">I", 11 + len(данные))

    def test_границы(self):
        данные = обр.flv()
        self.assertEqual(13, poisk._flv(данные[:13], 0))
        self.assertIsNone(poisk._flv(данные[:12], 0))
        self.assertEqual(len(данные), poisk._flv(данные + b"\x07" + bytes(10), 0))    # 11 байт, чужой вид
        self.assertEqual(0, poisk._flv(данные + bytes(10), 0))
        self.assertEqual(0, poisk._flv(данные[:-1], 0))
        for смещение in (4, 8):
            плохой = bytearray(данные)
            плохой[4] = 0
            struct.pack_into(">I", плохой, 5, смещение)
            with self.subTest(смещение=смещение):
                self.assertIsNone(poisk._flv(bytes(плохой), 0))

    def test_виды_и_размер_метки(self):
        данные = обр.flv()
        for вид, входит in ((8, True), (18, True), (19, False), (17, False), (0x28, True)):
            with self.subTest(вид=вид):
                с_меткой = данные + self.метка(вид, b"abc")
                self.assertEqual(len(с_меткой) if входит else len(данные), poisk._flv(с_меткой, 0))
        большая = данные + self.метка(9, bytes(70000))
        self.assertEqual(len(большая), poisk._flv(большая, 0))


class МутантыAnnexBTests(unittest.TestCase):
    def проверка(self, данные, м=0):
        return next(ф.проверка for ф in poisk.ФОРМАТЫ if ф.имя == "H.264 (Annex B)")(данные, м)

    def test_следующий_код_начала(self):
        self.assertIsNone(self.проверка(b"\x00\x00\x00\x01\x67\x42\x00\x00\x01"))           # код в самом конце
        self.assertEqual(0, self.проверка(b"\x00\x00\x00\x01\x67\x42\x00\x00\x01\x68"))
        self.assertEqual(0, self.проверка(b"\x00\x00\x00\x01\x67\x00\x00\x01\x68\xce"))      # SPS из одного байта
        self.assertEqual(0, self.проверка(b"\x00" * 37 + b"\x00\x00\x00\x01\x67\x42\x00\x00\x01\x68\xce", 37))


class МутантыMachOTests(unittest.TestCase):
    def заголовок(self, шире, команд, размер):
        if шире:
            return struct.pack("<IiiIIIII", 0xFEEDFACF, 7, 3, 2, команд, размер, 0, 0)
        return struct.pack("<IiiIIII", 0xFEEDFACE, 7, 3, 2, команд, размер, 0)

    def test_команда_из_8_байт_в_конце(self):
        сегмент = struct.pack("<II16sQQQQiiII", 0x19, 72, b"__TEXT", 0, 112, 0, 112, 5, 5, 0, 0)
        данные = self.заголовок(True, 2, 80) + сегмент + struct.pack("<II", 0x26, 8)
        self.assertEqual(112, poisk._macho(данные, 0))

    def test_поля_команд_у_конца_данных(self):
        сегмент64 = struct.pack("<II16sQQQQiiII", 0x19, 72, b"__TEXT", 0, 0, 0, 5000, 5, 5, 0, 0)
        данные = self.заголовок(True, 1, 72) + сегмент64
        self.assertEqual(5000, poisk._macho(данные[:32 + 56], 0))
        self.assertEqual(32 + 72, poisk._macho(данные[:32 + 55], 0))
        сегмент32 = struct.pack("<II16sIIIIiiII", 0x1, 56, b"__TEXT", 0, 0, 100, 3400, 5, 5, 0, 0)
        данные = self.заголовок(False, 1, 56) + сегмент32
        self.assertEqual(3500, poisk._macho(данные, 0))
        self.assertEqual(3500, poisk._macho(данные[:28 + 40], 0))
        self.assertEqual(28 + 56, poisk._macho(данные[:28 + 39], 0))
        подпись = struct.pack("<IIII", 0x1D, 16, 4000, 96)
        данные = self.заголовок(False, 1, 16) + подпись
        self.assertEqual(4096, poisk._macho(данные, 0))
        self.assertEqual(28 + 16, poisk._macho(данные[:-1], 0))

    def test_универсальный_короткий(self):
        self.assertEqual(0, poisk._macho_fat(обр.macho_fat()[:8], 0))


if __name__ == "__main__":
    unittest.main()
