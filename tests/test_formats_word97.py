"""Двоичный Word 97 (.doc) без LibreOffice.

Главный формат ответов отдела: половину исходящих пишут в Word и сохраняют
как .doc — так делали десять лет назад и продолжают делать. Штатный путь для
таких файлов — LibreOffice, но в установке без модуля Writer он молча не
справляется, и документ оказывается нечитаемым: письмо не находится по
словам, а окно просмотра предлагает скачать файл и открыть его чем-нибудь.
"""

import struct
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.ingest.formats.word97 import (
    OLE_MAGIC,
    convert_doc_native,
    doc_text,
    cfb_streams,
)


def build_doc(text: str, *, compressed: bool = False) -> bytes:
    """Собрать простейший файл Word 97 с одним куском текста.

    Настоящего Word здесь нет, поэтому файл собирается по той же схеме, по
    какой его читает разборщик: составной файл OLE2 с потоками WordDocument
    и 1Table, таблица кусков из одной записи.
    """
    sector = 512

    if compressed:
        body_text = text.encode("cp1251")
        chars = len(body_text)
    else:
        body_text = text.encode("utf-16-le")
        chars = len(text)

    # --- поток WordDocument: FIB, затем текст ---
    text_at = 2048
    word = bytearray(text_at + len(body_text) + 16)
    struct.pack_into("<H", word, 0x0000, 0xA5EC)          # wIdent
    struct.pack_into("<H", word, 0x0002, 193)             # nFib: Word 97
    struct.pack_into("<H", word, 0x000A, 0x0200)          # текст в 1Table
    struct.pack_into("<I", word, 0x0018, text_at)         # fcMin
    struct.pack_into("<I", word, 0x001C, text_at + len(body_text))
    struct.pack_into("<I", word, 0x01A2, 0)               # fcClx
    word[text_at:text_at + len(body_text)] = body_text

    # --- поток 1Table: CLX из одного Pcdt с одним куском ---
    offset = (text_at * 2) | 0x40000000 if compressed else text_at
    plc = struct.pack("<II", 0, chars) + struct.pack("<HIH", 0, offset, 0)
    clx = bytes([0x02]) + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<I", word, 0x01A6, len(clx))        # lcbClx

    return _pack_ole({"WordDocument": bytes(word), "1Table": clx}, sector)


def build_doc_with_padding(text: str, *, pad: int) -> bytes:
    """Тот же файл, но список кусков лежит не в начале потока таблицы."""
    body_text = text.encode("utf-16-le")
    text_at = 2048
    word = bytearray(text_at + len(body_text) + 16)
    struct.pack_into("<H", word, 0x0000, 0xA5EC)
    struct.pack_into("<H", word, 0x0002, 193)
    struct.pack_into("<H", word, 0x000A, 0x0200)
    struct.pack_into("<I", word, 0x0018, text_at)
    struct.pack_into("<I", word, 0x001C, text_at + len(body_text))
    word[text_at:text_at + len(body_text)] = body_text

    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, text_at, 0)
    clx = bytes([0x02]) + struct.pack("<I", len(plc)) + plc
    # Перед списком кусков — чужие байты, как в настоящем файле.
    table = b"\x99" * pad + clx
    struct.pack_into("<I", word, 0x01A2, pad)
    struct.pack_into("<I", word, 0x01A6, len(clx))
    return _pack_ole({"WordDocument": bytes(word), "1Table": table}, 512)


def build_doc_with_decoy(text: str) -> bytes:
    """Тот же файл, но рядом с настоящей таблицей лежит негодная 0Table."""
    body_text = text.encode("utf-16-le")
    text_at = 2048
    word = bytearray(text_at + len(body_text) + 16)
    struct.pack_into("<H", word, 0x0000, 0xA5EC)
    struct.pack_into("<H", word, 0x0002, 193)
    struct.pack_into("<H", word, 0x000A, 0x0200)      # куски в 1Table
    struct.pack_into("<I", word, 0x0018, text_at)
    struct.pack_into("<I", word, 0x001C, text_at + len(body_text))
    struct.pack_into("<I", word, 0x01A2, 0)
    word[text_at:text_at + len(body_text)] = body_text

    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, text_at, 0)
    clx = bytes([0x02]) + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<I", word, 0x01A6, len(clx))
    decoy = b"\x00" * len(clx)
    return _pack_ole({"WordDocument": bytes(word),
                      "0Table": decoy, "1Table": clx}, 512)


def _pack_ole(streams: dict, sector: int) -> bytes:
    """Уложить потоки в составной файл OLE2.

    Мелкие потоки (меньше 4096 байт) в настоящем файле лежат не в секторах
    файла, а внутри мини-потока корневой записи, и у них своя таблица. Так
    устроен любой невеликий .doc, поэтому сборка это повторяет — иначе
    проверка обходила бы половину разбора.
    """
    mini_size = 64
    cutoff = 4096
    names = list(streams)
    big = [name for name in names if len(streams[name]) >= cutoff]
    small = [name for name in names if len(streams[name]) < cutoff]

    # --- мини-поток: мелкие потоки подряд по 64 байта ---
    mini_data = bytearray()
    mini_starts = {}
    mini_fat: list[int] = []
    for name in small:
        payload = streams[name]
        count = max(1, -(-len(payload) // mini_size))
        mini_starts[name] = len(mini_data) // mini_size
        mini_data += payload.ljust(count * mini_size, b"\x00")
        base = mini_starts[name]
        for step in range(count):
            mini_fat.append(base + step + 1 if step + 1 < count else 0xFFFFFFFE)

    # --- секторы: крупные потоки, мини-поток, каталог, мини-таблица, FAT ---
    data = bytearray()
    starts = {}
    used = 0

    def put(payload: bytes) -> int:
        nonlocal used, data
        count = max(1, -(-len(payload) // sector))
        first = used
        data += payload.ljust(count * sector, b"\x00")
        used += count
        return first

    for name in big:
        starts[name] = put(streams[name])
    mini_start_sector = put(bytes(mini_data)) if mini_data else 0xFFFFFFFE

    directory = bytearray()

    def entry(name, kind, start, size):
        record = bytearray(128)
        raw = name.encode("utf-16-le") + b"\x00\x00"
        record[:len(raw)] = raw
        struct.pack_into("<H", record, 64, len(raw))
        record[66] = kind
        record[67] = 1
        struct.pack_into("<III", record, 68, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        struct.pack_into("<I", record, 116, start)
        struct.pack_into("<Q", record, 120, size)
        return bytes(record)

    directory += entry("Root Entry", 5, mini_start_sector, len(mini_data))
    for name in names:
        directory += entry(
            name, 2,
            mini_starts[name] if name in mini_starts else starts[name],
            len(streams[name]))
    directory = bytes(directory).ljust(sector, b"\x00")
    dir_start = put(directory)

    mini_fat_bytes = b"".join(struct.pack("<I", item) for item in mini_fat)
    mini_fat_start = put(mini_fat_bytes.ljust(sector, b"\xff")) if mini_fat else 0xFFFFFFFE

    fat_sector = used
    slots = max(used + 1, sector // 4)
    fat = [0xFFFFFFFF] * slots

    def chain_of(first: int, payload_len: int) -> None:
        count = max(1, -(-payload_len // sector))
        for step in range(count):
            index = first + step
            fat[index] = index + 1 if step + 1 < count else 0xFFFFFFFE

    for name in big:
        chain_of(starts[name], len(streams[name]))
    if mini_data:
        chain_of(mini_start_sector, len(mini_data))
    chain_of(dir_start, len(directory))
    if mini_fat:
        chain_of(mini_fat_start, sector)
    fat[fat_sector] = 0xFFFFFFFD
    fat_bytes = b"".join(struct.pack("<I", item) for item in fat[:sector // 4])

    header = bytearray(sector)
    header[:8] = OLE_MAGIC
    struct.pack_into("<H", header, 0x1A, 0x003E)
    struct.pack_into("<H", header, 0x1C, 0xFFFE)
    struct.pack_into("<H", header, 0x1E, 9)               # 1 << 9 = 512
    struct.pack_into("<H", header, 0x20, 6)               # мини-сектор 64
    struct.pack_into("<I", header, 0x2C, 1)               # секторов FAT
    struct.pack_into("<I", header, 0x30, dir_start)
    struct.pack_into("<I", header, 0x38, cutoff)
    struct.pack_into("<I", header, 0x3C, mini_fat_start)
    struct.pack_into("<I", header, 0x40, 1 if mini_fat else 0)
    struct.pack_into("<I", header, 0x44, 0xFFFFFFFE)
    struct.pack_into("<I", header, 0x48, 0)
    for index in range(109):
        struct.pack_into("<I", header, 0x4C + index * 4,
                         fat_sector if index == 0 else 0xFFFFFFFF)

    return bytes(header) + bytes(data) + fat_bytes.ljust(sector, b"\x00")


class Word97Tests(unittest.TestCase):
    def test_a_russian_document_is_read(self):
        raw = build_doc("Результаты технического анализа\rЛиния связи: РРЛС.\r")
        self.assertEqual("Результаты технического анализа\nЛиния связи: РРЛС.",
                         _text(raw))

    def test_a_compressed_piece_is_read_as_windows_1251(self):
        """Word хранит куски двумя способами: байт на знак и UTF-16.

        Какой именно — сказано битом в смещении, и перепутать их значит
        получить кашу вместо текста.
        """
        raw = build_doc("Ствол 3, поляризация вертикальная\r", compressed=True)
        self.assertEqual("Ствол 3, поляризация вертикальная", _text(raw))

    def test_service_marks_become_line_breaks_or_disappear(self):
        """Знаки разметки Word значат разное, и выкинуть их все нельзя.

        0x07 — конец ячейки таблицы, и на его месте должен встать перевод
        строки: иначе строка таблицы слипается в «ЯчейкаВторая». 0x0B —
        разрыв строки, 0x13/0x14/0x15 — обвязка поля, её видно быть не
        должно, а 0xA0 — неразрывный пробел, он остаётся пробелом.
        """
        raw = build_doc("Ячейка\x07Вторая\rСтрока\x0bдалее\r"
                        "\x13НОМЕРСТР\x14 7 \x15\rДо\xa0востребования\r")
        text = _text(raw)
        self.assertIn("Ячейка\nВторая", text)
        self.assertIn("Строка\nдалее", text)
        self.assertIn("До востребования", text)
        for mark in ("\x07", "\x0b", "\x13", "\x14", "\x15", "\xa0"):
            self.assertNotIn(mark, text)

    def test_empty_paragraphs_are_squeezed(self):
        # Пустой абзац в .doc — обычный приём вёрстки, и их бывает по десятку.
        raw = build_doc("Первый\r\r\r\r\rВторой\r")
        self.assertEqual("Первый\n\nВторой", _text(raw))

    def test_the_title_is_the_first_line(self):
        """Разметки в двоичном .doc мы не восстанавливаем, значит, и стиля
        «Заголовок 1» нет. Первая строка отчёта — его название."""
        with _as_file(build_doc("Результаты технического анализа\rДалее текст.\r")) as path:
            document = convert_doc_native(path)
            self.assertEqual("Результаты технического анализа", document.title)

    def test_the_reader_says_what_it_did_not_restore(self):
        # Обещать разметку, которой нет, нельзя: по такому тексту потом
        # сверяют таблицу допусков.
        with _as_file(build_doc("Отчёт\r")) as path:
            document = convert_doc_native(path)
        self.assertTrue(any("разметка" in item for item in document.warnings))

    def test_a_long_document_lies_in_the_sectors_and_not_in_the_mini_stream(self):
        """Мелкие потоки OLE лежат внутри мини-потока, крупные — в секторах.

        Пути разные, и файл отдела на семь страниц идёт вторым: проверка
        обязана пройти оба, иначе половина разбора остаётся неисхоженной.
        """
        long_text = "".join("Абзац номер %d, ствол 3, поляризация.\r" % n
                            for n in range(200))
        raw = build_doc(long_text)
        streams = cfb_streams(raw)
        self.assertGreater(len(streams["WordDocument"]), 4096)
        text = _text(raw)
        self.assertIn("Абзац номер 0,", text)
        self.assertIn("Абзац номер 199,", text)

    def test_the_pieces_are_taken_from_their_place_in_the_table(self):
        """Список кусков лежит не в начале таблицы, а по записанному смещению.

        Перед ним в потоке таблицы стоят другие структуры Word. Читать
        таблицу с начала значит принять их за список кусков.
        """
        raw = build_doc_with_padding("Верный текст отчёта\r", pad=64)
        self.assertEqual("Верный текст отчёта", _text(raw))

    def test_the_flag_chooses_which_table_holds_the_pieces(self):
        """В файле бывают обе таблицы — 0Table и 1Table.

        Какая из них настоящая, сказано битом в заголовке. Взять не ту
        значит прочитать чужие числа как список кусков и выдать мусор.
        """
        raw = bytearray(build_doc("Верный текст отчёта\r"))
        # Подкладываем вторую таблицу с заведомо негодным содержимым и
        # убеждаемся, что разборщик её не взял.
        streams = cfb_streams(bytes(raw))
        self.assertIn("1Table", streams)
        both = build_doc_with_decoy("Верный текст отчёта\r")
        self.assertEqual("Верный текст отчёта", _text(both))

    def test_a_file_that_is_not_a_compound_document_is_refused(self):
        with self.assertRaises(ValueError):
            doc_text("PK\x03\x04 это docx, а не doc".encode("utf-8"))

    def test_a_truncated_file_does_not_hang_or_crash(self):
        raw = build_doc("Отчёт\r")
        for cut in (600, 1200, 2000, len(raw) - 40):
            with self.subTest(cut=cut):
                with self.assertRaises((ValueError, struct.error, IndexError)):
                    doc_text(raw[:cut])

    def test_the_streams_of_the_container_are_found(self):
        streams = cfb_streams(build_doc("Отчёт\r"))
        self.assertIn("WordDocument", streams)
        self.assertIn("1Table", streams)


class DocFallbackTests(unittest.TestCase):
    """Когда LibreOffice не справился, документ читает свой разборщик."""

    def test_an_empty_conversion_is_a_refusal_and_not_an_empty_document(self):
        """soffice печатает ошибку и выходит с нулём.

        Судить о нём по коду возврата нельзя — только по содержимому, и
        пустой результат означает отказ, а не пустой документ.
        """
        from reportgen.ingest.convert import ConvertedDocument
        from reportgen.ingest.formats import legacy

        empty = ConvertedDocument(text="", title="x",
                                  warnings=["LibreOffice не преобразовал файл"])
        original = legacy._via_soffice
        legacy._via_soffice = lambda *args, **kwargs: empty
        try:
            with _as_file(build_doc("Результаты анализа\rЛиния: РРЛС.\r")) as path:
                document = legacy.convert_doc(path)
        finally:
            legacy._via_soffice = original

        self.assertIn("Результаты анализа", document.text)
        self.assertTrue(any("не преобразовал" in item for item in document.warnings))

    def test_a_working_libreoffice_keeps_its_result(self):
        # Свой разборщик отдаёт только текст, LibreOffice — заголовки и
        # таблицы. Когда тот работает, подменять его нечем.
        from reportgen.ingest.convert import ConvertedDocument
        from reportgen.ingest.formats import legacy

        rich = ConvertedDocument(text="# Заголовок\n\n| a | b |\n", title="из LibreOffice")
        original = legacy._via_soffice
        legacy._via_soffice = lambda *args, **kwargs: rich
        try:
            with _as_file(build_doc("Другой текст\r")) as path:
                document = legacy.convert_doc(path)
        finally:
            legacy._via_soffice = original

        self.assertEqual("из LibreOffice", document.title)
        self.assertIn("# Заголовок", document.text)

    def test_a_word_file_without_text_keeps_the_first_answer(self):
        """Документ из одних картинок читается пустым обоими путями.

        Отвечать должен тот, кто за формат отвечает: свой разборщик тут
        ничего не добавил и подменять чужой ответ ему нечем.
        """
        from reportgen.ingest.convert import ConvertedDocument
        from reportgen.ingest.formats import legacy

        empty = ConvertedDocument(text="", title="из LibreOffice",
                                  warnings=["в документе только картинки"])
        original = legacy._via_soffice
        legacy._via_soffice = lambda *args, **kwargs: empty
        try:
            with _as_file(build_doc("\r\r\r")) as path:
                document = legacy.convert_doc(path)
        finally:
            legacy._via_soffice = original
        self.assertEqual("из LibreOffice", document.title)
        self.assertEqual(["в документе только картинки"], document.warnings)

    def test_a_file_that_is_not_word_97_keeps_the_first_answer(self):
        # Не Word 97 — значит, свой разборщик тут ни при чём, и отвечать
        # должен тот путь, который за формат отвечает.
        from reportgen.ingest.convert import ConvertedDocument
        from reportgen.ingest.formats import legacy

        empty = ConvertedDocument(text="", title="x", warnings=["не вышло"])
        original = legacy._via_soffice
        legacy._via_soffice = lambda *args, **kwargs: empty
        try:
            with _as_file(b"PK\x03\x04 not a doc") as path:
                document = legacy.convert_doc(path)
        finally:
            legacy._via_soffice = original
        self.assertEqual(["не вышло"], document.warnings)


def _text(raw: bytes) -> str:
    with _as_file(raw) as path:
        return convert_doc_native(path).text


class _as_file:
    """Байты — временным файлом: конвертеры работают с путями."""

    def __init__(self, raw: bytes):
        self.raw = raw

    def __enter__(self) -> Path:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "otchet.doc"
        path.write_bytes(self.raw)
        return path

    def __exit__(self, *_):
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
