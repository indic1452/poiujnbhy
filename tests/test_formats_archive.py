"""Тесты приёма архивов: ZIP, 7z, RAR.

Все проверочные архивы собираются программно, во временном каталоге: ZIP —
штатным :mod:`zipfile`, архив «с паролем» — им же, с последующей правкой флага
шифрования в заголовках (записать настоящий зашифрованный ZIP стандартная
библиотека не умеет, а читать его отказывается — это и проверяется). В
репозиторий не кладётся ничего.

7z и RAR собрать нечем: в окружении нет ни 7-Zip, ни unrar, ни rar, а формат
RAR закрыт и свободного упаковщика не существует в принципе. Поэтому разбор
этих форматов проверяется двумя способами: то, что можно проверить без
архиватора (диагностика реестра, подсказка по установке, разбор технического
листинга архиватора), проверяется всегда, а полный проход — под
``skipUnless``, если архиватор в системе всё же есть.
"""

import shutil
import stat
import subprocess
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.ingest import registry
from reportgen.ingest.convert import convert_file
from reportgen.ingest.formats import archive
from reportgen.ingest.pipeline import chunks_from_markdown

TEXT = "Занимаемая полоса частот радиорелейного ствола. "


def make_zip(path, items, compression=zipfile.ZIP_DEFLATED):
    """Собирает ZIP: ``items`` — пары «имя внутри архива → содержимое»."""
    with zipfile.ZipFile(path, "w", compression) as bundle:
        for name, data in items.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            bundle.writestr(name, data)
    return Path(path)


def make_encrypted_zip(path, name, data):
    """ZIP, объявленный зашифрованным.

    ``zipfile`` умеет читать зашифрованные архивы, но не писать их, а флаг
    шифрования при записи сбрасывается принудительно (``_open_to_write``).
    Поэтому обычный архив дописывается вручную: бит 0 флага общего назначения
    взводится и в локальном заголовке (смещение 6), и в записи центрального
    каталога (смещение 8). Для стандартной библиотеки такой файл неотличим от
    зашифрованного — это подтверждает проверка в самом тесте.
    """
    make_zip(path, {name: data}, compression=zipfile.ZIP_STORED)
    raw = bytearray(Path(path).read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = raw.find(signature)
        while position != -1:
            flags = int.from_bytes(raw[position + offset:position + offset + 2], "little")
            raw[position + offset:position + offset + 2] = (flags | 0x1).to_bytes(2, "little")
            position = raw.find(signature, position + 1)
    Path(path).write_bytes(bytes(raw))
    return Path(path)


def sections(text):
    """Заголовки разделов документа-архива."""
    return [line[3:] for line in text.splitlines() if line.startswith("## ")]


class TempCase(unittest.TestCase):
    """Общий временный каталог: проверочные файлы в репозиторий не попадают."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="archive-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def zip_with(self, items, name="материалы.zip", **kwargs):
        return make_zip(self.tmp / name, items, **kwargs)


# ------------------------------------------------------------ содержимое ---

class ZipContentTest(TempCase):

    def test_markdown_and_text_land_in_one_document(self):
        path = self.zip_with({
            "методика.md": "# Методика\n\n" + TEXT * 8,
            "заметка.txt": "Заметка инженера. " * 10,
        })
        result = archive.convert_zip(path)
        self.assertFalse(result.is_empty, result.warnings)
        self.assertIn("Занимаемая полоса частот", result.text)
        self.assertIn("Заметка инженера", result.text)
        self.assertEqual(result.meta["source_format"], "zip")
        self.assertEqual(result.meta["members"], 2)
        self.assertEqual(result.page_count, 2)

    def test_member_names_become_headings(self):
        path = self.zip_with({
            "отчёты/протокол.md": "# Протокол\n\n" + TEXT * 8,
            "заметка.txt": TEXT * 8,
        })
        result = archive.convert_zip(path)
        self.assertIn("отчёты/протокол.md", sections(result.text))
        self.assertIn("заметка.txt", sections(result.text))

    def test_each_member_gets_its_own_page_marker(self):
        """Номер «страницы» архива — номер вложенного файла: по нему ссылка."""
        path = self.zip_with({
            "первый.md": "# Первый\n\n" + TEXT * 8,
            "второй.md": "# Второй\n\n" + TEXT * 8,
        })
        result = archive.convert_zip(path)
        self.assertIn(archive.page_marker(1), result.text)
        self.assertIn(archive.page_marker(2), result.text)
        self.assertNotIn(archive.page_marker(3), result.text)
        first = result.text.index("первый.md")
        self.assertLess(first, result.text.index(archive.page_marker(1)))

    def test_section_note_names_the_format(self):
        path = self.zip_with({"заметка.txt": TEXT * 8})
        result = archive.convert_zip(path)
        self.assertIn("*Файл из архива, формат: text", result.text)

    def test_nested_headings_are_pushed_below_the_file_heading(self):
        """«# Название» вложенного файла не должно закрывать раздел файла."""
        path = self.zip_with({"методика.md": "# Методика\n\n" + TEXT * 8 +
                                             "\n\n## Раздел\n\n" + TEXT * 8})
        result = archive.convert_zip(path)
        self.assertIn("### Методика", result.text)
        self.assertIn("#### Раздел", result.text)
        self.assertEqual(sections(result.text), ["методика.md"])

    def test_table_from_a_member_survives_whole(self):
        """Таблица допусков часто и есть весь смысл документа — рвать нельзя."""
        table = (
            "| Параметр | Допуск |\n"
            "| --- | --- |\n"
            "| Отклонение частоты | ±1·10⁻⁶ |\n"
            "| Уровень МШУ | −3 дБ |"
        )
        path = self.zip_with({"допуски.md": "# Допуски\n\n" + TEXT * 8 + "\n\n" + table})
        result = archive.convert_zip(path)
        blocks = [block for block in result.text.split("\n\n") if block.startswith("| ")]
        self.assertEqual(len(blocks), 1, result.text)
        self.assertIn("Уровень МШУ", blocks[0])

    def test_page_numbers_reach_the_chunks(self):
        """Ссылка под цитатой должна показывать, из какого файла архива она взята."""
        path = self.zip_with({
            "первый.md": "# Первый\n\n" + TEXT * 12,
            "второй.md": "# Второй\n\n" + "Ослабление в осадках. " * 20,
        })
        result = archive.convert_zip(path)
        chunks = chunks_from_markdown(
            result.text, "literature/материалы", "literature", result.meta)
        pages = {}
        for chunk in chunks:
            if "Ослабление в осадках" in chunk.text:
                pages["второй"] = chunk.meta.get("page")
            elif "Занимаемая полоса" in chunk.text:
                pages.setdefault("первый", chunk.meta.get("page"))
        self.assertEqual(pages.get("первый"), 1, chunks)
        self.assertEqual(pages.get("второй"), 2, chunks)

    def test_files_are_siblings_in_the_breadcrumbs(self):
        """Второй файл архива не должен оказаться вложенным в первый.

        Нарезчик корпуса считает вершиной дерева первый встреченный заголовок,
        поэтому документ-архив начинается заголовком первого уровня — именем
        самого архива.
        """
        path = self.zip_with({
            "первый.md": "# Первый\n\n" + TEXT * 12,
            "второй.md": "# Второй\n\n" + "Ослабление в осадках. " * 20,
        })
        result = archive.convert_zip(path)
        self.assertTrue(result.text.startswith("# материалы\n"), result.text[:80])
        chunks = chunks_from_markdown(
            result.text, "literature/материалы", "literature", result.meta)
        for chunk in chunks:
            if "Ослабление в осадках" in chunk.text:
                self.assertNotIn("первый.md", chunk.title_path)
                self.assertIn("второй.md", chunk.title_path)
                break
        else:
            self.fail("чанк второго файла не найден")


# ---------------------------------------------------------------- защита ---

class ZipSafetyTest(TempCase):

    def test_path_traversal_member_is_dropped(self):
        """Классический обход каталога: «../evil.txt» не должен создать файл."""
        holder = self.tmp / "тмп"
        holder.mkdir()
        previous = tempfile.tempdir
        tempfile.tempdir = str(holder)
        try:
            path = self.zip_with({
                "../evil.txt": "вредонос",
                "документ.md": "# Документ\n\n" + TEXT * 8,
            })
            result = archive.convert_zip(path)
        finally:
            tempfile.tempdir = previous

        self.assertIn("Документ", result.text)
        self.assertNotIn("вредонос", result.text)
        self.assertTrue(any("за пределы каталога" in item for item in result.warnings),
                        result.warnings)
        self.assertIn("небезопасное имя", result.text)
        escaped = [item for item in holder.rglob("evil.txt")]
        self.assertEqual(escaped, [], "файл записан за пределы каталога распаковки")
        self.assertEqual(list(holder.iterdir()), [], "временный каталог не удалён")

    def test_safe_member_path_rejects_dangerous_names(self):
        root = self.tmp / "распаковка"
        root.mkdir()
        for name in ("../evil.txt", "a/../../evil.txt", "/etc/passwd",
                     r"C:\Windows\system32\evil.txt", "..", ""):
            self.assertIsNone(archive.safe_member_path(root, name), name)
        target = archive.safe_member_path(root, "папка/файл.md")
        self.assertIsNotNone(target)
        self.assertEqual(target, root / "папка" / "файл.md")

    def test_safe_member_path_keeps_suffix_of_long_and_reserved_names(self):
        root = self.tmp / "распаковка"
        root.mkdir()
        long_name = "и" * 300 + ".md"
        target = archive.safe_member_path(root, long_name)
        self.assertEqual(target.suffix, ".md")
        self.assertLess(len(target.name), 120)
        # Имена устройств DOS на Windows не создаются — конвертер их переименует.
        self.assertNotEqual(archive.safe_member_path(root, "CON.txt").name.lower(), "con.txt")

    def test_member_count_limit(self):
        items = {f"файл{number:02d}.md": f"# Файл {number}\n\n" + TEXT * 8
                 for number in range(10)}
        path = self.zip_with(items)
        result = archive.convert_zip(path, limits=archive.Limits(max_members=3))
        self.assertEqual(result.meta["members"], 3)
        self.assertEqual(result.meta["entries"], 10)
        self.assertTrue(any("больше 3 файлов" in item for item in result.warnings),
                        result.warnings)
        self.assertIn("Разобрано файлов: 3 из 10", result.text)

    def test_total_size_limit_stops_a_zip_bomb(self):
        """Хорошо сжимаемый файл: два мегабайта нулей весят в архиве килобайты."""
        path = self.zip_with({
            "бомба.txt": "0" * (2 * 1024 * 1024),
            "документ.md": "# Документ\n\n" + TEXT * 8,
        })
        self.assertLess(path.stat().st_size, 64 * 1024, "архив должен быть сжимаемым")
        result = archive.convert_zip(path, limits=archive.Limits(max_total_bytes=64 * 1024))
        self.assertTrue(any("объём" in item for item in result.warnings), result.warnings)
        self.assertTrue(any("бомб" in item for item in result.warnings), result.warnings)
        self.assertNotIn("0" * 1000, result.text)
        # Пропущенный файл назван в составе архива, а не потерян молча.
        self.assertIn("бомба.txt", result.text)
        # Второй файл маленький и в предел укладывается — его теряться не должно.
        self.assertIn("Документ", result.text)

    def test_nested_archive_is_not_unpacked(self):
        inner = make_zip(self.tmp / "внутренний.zip",
                         {"внутри.md": "# Внутри\n\nГлубокий текст. " + TEXT * 8})
        path = self.zip_with({
            "вложенный.zip": inner.read_bytes(),
            "документ.md": "# Документ\n\n" + TEXT * 8,
        })
        result = archive.convert_zip(path)
        self.assertNotIn("Глубокий текст", result.text)
        self.assertIn("вложенный архив", result.text)
        self.assertIn("Документ", result.text)

    def test_nested_archive_is_unpacked_when_depth_allows(self):
        """Ограничение вложенности — настоящее, а не «просто не умеем»."""
        inner = make_zip(self.tmp / "внутренний.zip",
                         {"внутри.md": "# Внутри\n\nГлубокий текст. " + TEXT * 8})
        path = self.zip_with({"вложенный.zip": inner.read_bytes()})
        result = archive.convert_zip(path, limits=archive.Limits(max_depth=2))
        self.assertIn("Глубокий текст", result.text)
        self.assertIn("вложенный.zip", sections(result.text))

    def test_symlink_member_is_skipped(self):
        path = self.tmp / "ссылки.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            link = zipfile.ZipInfo("ссылка.md")
            link.create_system = 3  # Unix
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "/etc/passwd")
            bundle.writestr("документ.md", "# Документ\n\n" + TEXT * 8)
        result = archive.convert_zip(path)
        self.assertTrue(any("символьная ссылка" in item for item in result.warnings),
                        result.warnings)
        self.assertIn("«ссылка.md» — символьная ссылка", result.text)
        self.assertNotIn("passwd", result.text)
        self.assertIn("Документ", result.text)

    def test_two_members_with_the_same_name_do_not_overwrite_each_other(self):
        """ZIP допускает одноимённые элементы — подменять содержимое нельзя."""
        path = self.tmp / "двойники.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # zipfile ругается на дубль имени
            with zipfile.ZipFile(path, "w") as bundle:
                bundle.writestr("отчёт.md", "# Первый\n\nРанняя редакция. " + TEXT * 8)
                bundle.writestr("отчёт.md", "# Второй\n\nПоздняя редакция. " + TEXT * 8)
        result = archive.convert_zip(path)
        self.assertEqual(result.meta["members"], 2)
        self.assertIn("Ранняя редакция", result.text)
        self.assertIn("Поздняя редакция", result.text)

    def test_service_files_are_ignored(self):
        path = self.zip_with({
            "__MACOSX/._документ.md": "мусор",
            ".DS_Store": "мусор",
            "Thumbs.db": "мусор",
            "~$черновик.docx": "мусор",
            "документ.md": "# Документ\n\n" + TEXT * 8,
        })
        result = archive.convert_zip(path)
        self.assertEqual(result.meta["entries"], 1)
        self.assertNotIn("мусор", result.text)

    def test_temporary_directory_is_always_removed(self):
        holder = self.tmp / "тмп"
        holder.mkdir()
        previous = tempfile.tempdir
        tempfile.tempdir = str(holder)
        try:
            path = self.zip_with({"документ.md": "# Документ\n\n" + TEXT * 8})
            archive.convert_zip(path)
            broken = self.tmp / "битый.zip"
            broken.write_bytes("PK\x03\x04 дальше мусор".encode("utf-8"))
            archive.convert_zip(broken)
        finally:
            tempfile.tempdir = previous
        self.assertEqual(list(holder.iterdir()), [], "временный каталог не удалён")


# ------------------------------------------------- битые и странные файлы ---

class ZipFailureTest(TempCase):

    def test_password_protected_archive_warns_instead_of_failing(self):
        path = make_encrypted_zip(self.tmp / "секретно.zip", "тайна.txt", "секретный текст")
        # Проверяем, что архив действительно выглядит зашифрованным.
        with zipfile.ZipFile(path) as bundle:
            with self.assertRaises(RuntimeError):
                bundle.read("тайна.txt")

        result = archive.convert_zip(path)
        self.assertTrue(any("паролем" in item for item in result.warnings), result.warnings)
        self.assertNotIn("секретный текст", result.text)
        self.assertEqual(result.meta.get("encrypted"), 1)
        self.assertIn("«тайна.txt» — зашифрован паролем", result.text)

    def test_empty_archive(self):
        path = self.zip_with({})
        result = archive.convert_zip(path)
        self.assertTrue(result.is_empty)
        self.assertTrue(any("пуст" in item for item in result.warnings), result.warnings)

    def test_broken_archive_gives_warning_not_exception(self):
        path = self.tmp / "битый.zip"
        path.write_bytes("PK\x03\x04 это не архив, а обрывок".encode("utf-8"))
        result = archive.convert_zip(path)
        self.assertTrue(result.is_empty)
        self.assertTrue(any("не открыт" in item for item in result.warnings), result.warnings)

    def test_directory_instead_of_file_does_not_raise(self):
        result = archive.convert_zip(self.tmp)
        self.assertTrue(result.is_empty)
        self.assertTrue(result.warnings)

    def test_archive_of_unknown_formats_lists_them_without_failing(self):
        path = self.zip_with({
            "чертёж.dwg": b"\x00\x01\x02",
            "прошивка.bin": b"\xff" * 32,
            "схема.sch": b"\x00",
        })
        result = archive.convert_zip(path)
        self.assertFalse(result.is_empty, "состав архива должен попасть в текст")
        self.assertIn("чертёж.dwg", result.text)
        self.assertIn("прошивка.bin", result.text)
        self.assertIn("нет конвертера", result.text)
        self.assertTrue(any("ни один файл архива не разобран" in item
                            for item in result.warnings), result.warnings)

    def test_broken_member_does_not_break_the_archive(self):
        path = self.zip_with({
            "битый.pdf": "%PDF-1.4 дальше мусор".encode("utf-8"),
            "документ.md": "# Документ\n\n" + TEXT * 8,
        })
        result = archive.convert_zip(path)
        self.assertIn("Документ", result.text)
        self.assertIn("битый.pdf", result.text)
        self.assertTrue(any("битый.pdf" in item for item in result.warnings), result.warnings)

    def test_member_with_broken_data_is_reported(self):
        """Повреждённый поток внутри исправного оглавления."""
        path = self.zip_with({
            "документ.md": "# Документ\n\n" + TEXT * 8,
            "порча.md": "# Порча\n\n" + TEXT * 20,
        })
        raw = bytearray(path.read_bytes())
        # Портим сжатые данные последнего элемента, оглавление не трогаем.
        position = raw.rfind(b"PK\x03\x04")
        raw[position + 40:position + 60] = b"\x00" * 20
        path.write_bytes(bytes(raw))
        result = archive.convert_zip(path)
        self.assertIn("Документ", result.text)
        self.assertNotIn("Порча", result.text.replace("«порча.md»", ""))
        self.assertTrue(result.warnings)


# ------------------------------------------------------------ помощники ----

class HelperTest(unittest.TestCase):

    def test_shift_headings(self):
        text = "# Один\n\nтекст\n\n## Два\n\n###### Шесть"
        shifted = archive.shift_headings(text, 2)
        self.assertIn("### Один", shifted)
        self.assertIn("#### Два", shifted)
        self.assertIn("###### Шесть", shifted)  # глубже шестого уровня не бывает
        self.assertEqual(archive.shift_headings(text, 0), text)

    def test_shift_headings_keeps_code_blocks(self):
        text = "# Заголовок\n\n```\n# это команда, а не заголовок\n```"
        shifted = archive.shift_headings(text, 2)
        self.assertIn("### Заголовок", shifted)
        self.assertIn("\n# это команда", shifted)

    def test_dos_encoded_member_name_is_decoded(self):
        """Русский проводник и старый WinRAR пишут имена в cp866."""
        info = zipfile.ZipInfo()
        info.filename = "отчёт по линии.txt".encode("cp866").decode("cp437")
        info.flag_bits = 0
        self.assertEqual(archive.decode_member_name(info), "отчёт по линии.txt")

    def test_utf8_and_latin_names_are_left_alone(self):
        info = zipfile.ZipInfo()
        info.filename = "отчёт.txt"
        info.flag_bits = 0x800
        self.assertEqual(archive.decode_member_name(info), "отчёт.txt")
        plain = zipfile.ZipInfo()
        plain.filename = "report_2024.txt"
        plain.flag_bits = 0
        self.assertEqual(archive.decode_member_name(plain), "report_2024.txt")

    def test_seven_zip_listing_is_parsed(self):
        """Технический листинг 7-Zip: по нему считается объём до распаковки."""
        output = (
            "7-Zip 16.02 : Copyright (c) 1999-2016 Igor Pavlov\n\n"
            "Listing archive: материалы.7z\n\n"
            "--\n"
            "Path = материалы.7z\n"
            "Type = 7z\n"
            "Physical Size = 4096\n\n"
            "----------\n"
            "Path = методика.docx\n"
            "Size = 20480\n"
            "Encrypted = -\n"
            "Attributes = A_ -rw-r--r--\n\n"
            "Path = папка\n"
            "Size = 0\n"
            "Folder = +\n\n"
            "Path = таблица.xlsx\n"
            "Size = 4096\n"
            "Encrypted = +\n"
        )
        entries = archive._parse_listing(output)
        self.assertEqual(len(entries), 3)
        total, encrypted = archive._listing_totals(entries)
        self.assertEqual(total, 24576)
        self.assertTrue(encrypted)

    def test_unrar_listing_is_parsed(self):
        output = (
            "UNRAR 6.11 freeware      Copyright (c) 1993-2022 Alexander Roshal\n\n"
            "Archive: материалы.rar\n"
            "Details: RAR 5\n\n"
            "        Name: протокол.pdf\n"
            "        Type: File\n"
            "        Size: 131072\n"
            " Packed size: 65536\n"
            "  Attributes: -rw-r--r--\n\n"
            "        Name: схема.png\n"
            "        Type: File\n"
            "        Size: 1024\n"
            "       Flags: encrypted\n"
        )
        entries = archive._parse_listing(output)
        self.assertEqual(len(entries), 2)
        total, encrypted = archive._listing_totals(entries)
        self.assertEqual(total, 132096)
        self.assertTrue(encrypted)

    def test_human_size(self):
        self.assertEqual(archive._human_size(512), "512 байт")
        self.assertEqual(archive._human_size(2048), "2,0 КБ")
        self.assertEqual(archive._human_size(3 * 1024 * 1024), "3,0 МБ")


# ------------------------------------------------------------- реестр -----

class RegistryTest(TempCase):

    def test_zip_is_registered_and_needs_nothing(self):
        spec = registry.find(".zip")
        self.assertIsNotNone(spec)
        self.assertTrue(spec.is_available(), "ZIP разбирается стандартной библиотекой")
        self.assertEqual(spec.requires, ())

    def test_seven_zip_and_rar_declare_binaries(self):
        for suffix, expected in ((".7z", {"7z", "7za", "7zz"}), (".rar", {"unrar", "7z"})):
            spec = registry.find(suffix)
            self.assertIsNotNone(spec, suffix)
            names = {item.name for item in spec.requires}
            self.assertTrue(names <= expected, (suffix, names))
            for requirement in spec.requires:
                self.assertEqual(requirement.kind, "binary")
                # Подсказка нужна и для Windows: контур изолированный.
                self.assertIn("Windows", requirement.hint)
                self.assertIn("PATH", requirement.hint)

    def test_every_declared_binary_is_actually_tried(self):
        """Перебор архиваторов в коде и требования в реестре не должны разойтись."""
        declared = {
            suffix: {item.name for spec in registry.all_specs()
                     if suffix in spec.suffixes for item in spec.requires}
            for suffix in (".7z", ".rar")
        }
        self.assertEqual(declared[".7z"], {"7z", "7za", "7zz"})
        self.assertEqual(declared[".rar"], {"unrar", "7z"})

    def test_zip_goes_through_the_dispatcher(self):
        path = self.zip_with({"документ.md": "# Документ\n\n" + TEXT * 8})
        result = convert_file(path)
        self.assertEqual(result.meta["source_format"], "zip")
        self.assertIn("Документ", result.text)

    def test_missing_archiver_is_reported_with_a_hint(self):
        if archive._archive_tool("7z") is not None:
            self.skipTest("в системе есть 7-Zip — проверяется другим тестом")
        path = self.tmp / "материалы.7z"
        path.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32)
        result = convert_file(path)
        self.assertTrue(result.is_empty)
        text = " ".join(result.warnings)
        self.assertIn("7z", text)
        self.assertIn("p7zip", text)

    def test_seven_zip_converter_does_not_raise_without_tool(self):
        path = self.tmp / "материалы.7z"
        path.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32)
        result = archive.convert_seven_zip(path)
        self.assertTrue(result.warnings)
        self.assertEqual(result.meta["source_format"], "7z")


# ----------------------------------------- полный проход внешних форматов ---

@unittest.skipUnless(shutil.which("7z") or shutil.which("7za") or shutil.which("7zz"),
                     "7-Zip не установлен")
class SevenZipTest(TempCase):
    """Проверяется, только если архиватор в системе есть: файл собирает он сам."""

    def test_seven_zip_archive_is_parsed(self):
        source = self.tmp / "методика.md"
        source.write_text("# Методика\n\n" + TEXT * 8, encoding="utf-8")
        tool = archive._archive_tool("7z")
        path = self.tmp / "материалы.7z"
        subprocess.run([tool, "a", "-y", str(path), str(source)],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=180, check=False)
        if not path.is_file():
            self.skipTest("архиватор не собрал 7z")
        result = archive.convert_seven_zip(path)
        self.assertIn("Занимаемая полоса частот", result.text)
        self.assertIn("методика.md", sections(result.text))


@unittest.skipUnless(shutil.which("rar") and shutil.which("unrar"),
                     "нет упаковщика rar и распаковщика unrar")
class RarTest(TempCase):
    """RAR закрыт: без штатного упаковщика проверочный архив взять неоткуда."""

    def test_rar_archive_is_parsed(self):
        source = self.tmp / "методика.md"
        source.write_text("# Методика\n\n" + TEXT * 8, encoding="utf-8")
        path = self.tmp / "материалы.rar"
        subprocess.run(["rar", "a", "-y", str(path), str(source)],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       cwd=str(self.tmp), timeout=180, check=False)
        if not path.is_file():
            self.skipTest("упаковщик не собрал rar")
        result = archive.convert_rar(path)
        self.assertIn("Занимаемая полоса частот", result.text)


if __name__ == "__main__":
    unittest.main()
