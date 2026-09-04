"""Тесты распознавания: изображения, PDF-сканы, DjVu.

Материал для тестов создаётся программно: картинка со строками русского
текста рисуется через Pillow шрифтом DejaVuSans, PDF собирается PyMuPDF,
DjVu — программами djvulibre (``cjb2`` склеивает PBM в скан, ``djvm``
объединяет страницы, ``djvused`` кладёт текстовый слой). В репозитории
никаких образцов не лежит.

Распознанный текст сверяется по ключевым словам, а не побуквенно: OCR
ошибается в знаках препинания и отдельных буквах, и тест, требующий полного
совпадения, будет падать на каждой второй версии tesseract.
"""

import json
import _bootstrap  # noqa: F401

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reportgen.ingest import registry
from reportgen.ingest.convert import ConvertedDocument, convert_file, page_markers
from reportgen.ingest.formats import djvu, ocr

# --------------------------------------------------------------- условия ---

try:
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    HAS_PIL = True
except ImportError:  # pragma: no cover — зависит от окружения
    HAS_PIL = False

try:
    import pymupdf  # type: ignore

    HAS_PYMUPDF = True
except ImportError:  # pragma: no cover — зависит от окружения
    HAS_PYMUPDF = False

#: Шрифт с кириллицей. Без него нарисовать русский текст нечем и проверять
#: распознавание не на чем — такие тесты пропускаются.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/local/share/fonts/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\DejaVuSans.ttf",
)


def _font_path() -> str:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for directory in ("/usr/share/fonts", "/usr/local/share/fonts"):
        root = Path(directory)
        if not root.is_dir():
            continue
        for found in root.rglob("DejaVuSans.ttf"):
            return str(found)
    return ""


FONT = _font_path()
HAS_TESSERACT = ocr.ocr_available()
#: Полный комплект djvulibre: разбор (djvutxt, ddjvu) и сборка образцов
#: (cjb2, djvm, djvused). Без сборки тест-файл сделать не из чего.
DJVU_TOOLS = ("djvutxt", "ddjvu", "djvused", "cjb2", "djvm")
HAS_DJVU = all(djvu.djvu_binary(name) for name in DJVU_TOOLS)

SCAN_LINES = (
    "Радиорелейная линия связи",
    "Диаметр антенны 1,2 метра",
    "Затухание в тракте 3,5 дБ",
)


# ------------------------------------------------------------- помощники ---

def make_image(path: Path, lines=SCAN_LINES, size=(1500, 460)) -> Path:
    """Картинка со строками русского текста — имитация страницы скана."""
    image = Image.new("L", size, 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 40)
    for index, line in enumerate(lines):
        draw.text((40, 40 + index * 80), line, font=font, fill=0)
    image.save(path)
    return path


def make_blank_image(path: Path, size=(1200, 400)) -> Path:
    Image.new("L", size, 255).save(path)
    return path


def make_pdf(path: Path, pages) -> Path:
    """PDF из описаний страниц: ``('text', строки)`` или ``('image', файл)``."""
    document = pymupdf.open()
    for kind, payload in pages:
        page = document.new_page(width=595, height=842)
        if kind == "text":
            for index, line in enumerate(payload):
                page.insert_text(
                    (50, 90 + index * 24),
                    line,
                    fontname="dejavu",
                    fontfile=FONT,
                    fontsize=12,
                )
        else:
            page.insert_image(pymupdf.Rect(30, 30, 565, 250), filename=str(payload))
    document.save(str(path))
    document.close()
    return path


def _run_tool(name: str, arguments) -> None:
    binary = djvu.djvu_binary(name)
    assert binary, f"нет программы {name}"
    completed = subprocess.run(
        [binary, *arguments], capture_output=True, timeout=120, check=False
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{name} не отработал: {completed.stderr.decode('utf-8', 'replace')}"
        )


def make_djvu(path: Path, images, texts=None) -> Path:
    """DjVu из картинок; ``texts`` — текстовый слой по страницам (или ``None``)."""
    directory = path.parent
    parts = []
    for index, image in enumerate(images, start=1):
        pbm = directory / f"_page{index}.pbm"
        Image.open(image).convert("1").save(pbm)
        part = directory / f"_page{index}.djvu"
        _run_tool("cjb2", [str(pbm), str(part)])
        parts.append(part)
    if len(parts) == 1:
        shutil.copyfile(parts[0], path)
    else:
        _run_tool("djvm", ["-c", str(path), *[str(part) for part in parts]])
    if texts:
        script = []
        for number, text in enumerate(texts, start=1):
            layer = directory / f"_text{number}.txt"
            escaped = text.replace("\\", "\\\\").replace('"', '\\"')
            layer.write_text(f'(page 0 0 2000 600 "{escaped}")\n', encoding="utf-8")
            script.append(f"select {number}; set-txt {layer};")
        _run_tool("djvused", [str(path), "-e", " ".join(script) + " save"])
    return path


def keywords_present(text: str, *words: str) -> bool:
    """Есть ли ключевые слова в распознанном тексте (регистр не важен)."""
    lowered = text.lower()
    return all(word.lower() in lowered for word in words)


class OcrSpy:
    """Подменяет ``ocr_image`` и считает вызовы, не мешая настоящему распознаванию."""

    def __init__(self, result=None, wrapped=None):
        self.calls = []
        self.result = result
        self.wrapped = wrapped

    def __call__(self, source, **kwargs):
        self.calls.append((source, kwargs))
        if self.wrapped is not None:
            return self.wrapped(source, **kwargs)
        return self.result if self.result is not None else ""


class TempCase(unittest.TestCase):
    """Общий каталог для образцов: создаётся на тест и удаляется после него."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="reportgen-test-ocr-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        ocr.reset_caches()
        self.addCleanup(ocr.reset_caches)

    def leftover(self, prefix: str):
        return sorted(Path(tempfile.gettempdir()).glob(f"{prefix}*"))


# --------------------------------------------------------------- реестр ----

class RegistryTests(TempCase):
    def test_image_converter_registered(self):
        spec = registry.find(".png")
        self.assertIsNotNone(spec)
        self.assertEqual("image-ocr", spec.name)
        for suffix in (".jpg", ".jpeg", ".tif", ".tiff", ".bmp"):
            self.assertEqual("image-ocr", registry.find(suffix).name, suffix)

    def test_pdf_ocr_has_higher_priority(self):
        spec = registry.find(".pdf")
        self.assertEqual("pdf-ocr", spec.name)
        self.assertEqual(10, spec.priority)
        names = {item.name for item in spec.requires}
        self.assertEqual({"pymupdf", "tesseract"}, names)

    def test_djvu_converter_registered(self):
        for suffix in (".djvu", ".djv"):
            spec = registry.find(suffix)
            self.assertEqual("djvu", spec.name, suffix)
        spec = registry.find(".djvu")
        self.assertEqual({"djvutxt", "ddjvu"}, {item.name for item in spec.requires})

    def test_hints_mention_windows_and_linux(self):
        """Подсказка должна годиться и для машины заказчика на Windows."""
        for suffix in (".png", ".djvu"):
            for requirement in registry.find(suffix).requires:
                if requirement.kind != "binary":
                    continue
                self.assertIn("Windows", requirement.hint, requirement.name)
                self.assertIn("apt install", requirement.hint, requirement.name)

    def test_pdf_falls_back_to_plain_converter_without_tesseract(self):
        """Нет tesseract — PDF всё равно разбирается обычным конвертером."""
        real = shutil.which

        def fake(name, *args, **kwargs):
            return None if name == "tesseract" else real(name, *args, **kwargs)

        with mock.patch("shutil.which", side_effect=fake):
            self.assertEqual("pdf", registry.find(".pdf").name)
            self.assertFalse(registry.find(".png").is_available())
            self.assertIn("tesseract", registry.missing_hint(registry.find(".png")))

    def test_modules_do_not_import_heavy_packages(self):
        """Импорт модуля не должен требовать pymupdf или Pillow (правило реестра)."""
        import ast

        for module in (ocr, djvu):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            top_level = []
            for node in tree.body:
                if isinstance(node, ast.Import):
                    top_level += [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top_level.append(node.module.split(".")[0])
            for heavy in ("pymupdf", "fitz", "PIL"):
                self.assertNotIn(heavy, top_level, module.__name__)


# ------------------------------------------------------ механизм tesseract ---

class TesseractTests(TempCase):
    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_binary_found(self):
        self.assertTrue(Path(ocr.tesseract_binary()).name.startswith("tesseract"))
        self.assertTrue(ocr.ocr_available())

    def test_available_without_binary(self):
        with mock.patch.object(ocr, "tesseract_binary", return_value=None):
            self.assertFalse(ocr.ocr_available())
            self.assertEqual(frozenset(), ocr.available_languages())

    def test_resolve_languages_filters_missing(self):
        with mock.patch.object(ocr, "available_languages", return_value=frozenset({"eng", "osd"})):
            usable, missing = ocr.resolve_languages("rus+eng")
            self.assertEqual("eng", usable)
            self.assertEqual(("rus",), missing)
            self.assertIn("rus", ocr.language_warning("rus+eng"))

    def test_resolve_languages_without_knowledge(self):
        """Список языков не выяснился — запрошенное передаём tesseract как есть."""
        with mock.patch.object(ocr, "available_languages", return_value=frozenset()):
            self.assertEqual(("rus+eng", ()), ocr.resolve_languages("rus+eng"))
            self.assertIsNone(ocr.language_warning("rus+eng"))

    def test_missing_binary_raises_with_hint(self):
        image = self.directory / "нет.png"
        image.write_bytes(b"")
        with mock.patch.object(ocr, "tesseract_binary", return_value=None):
            with self.assertRaises(ocr.OcrUnavailableError) as caught:
                ocr.ocr_image(image)
        self.assertIn("tesseract", str(caught.exception))
        self.assertIn("Windows", str(caught.exception))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_timeout_is_reported_and_temp_file_removed(self):
        before = self.leftover("reportgen-ocr-")
        error = subprocess.TimeoutExpired(cmd="tesseract", timeout=5)
        with mock.patch.object(ocr.subprocess, "run", side_effect=error):
            with self.assertRaises(ocr.OcrTimeoutError) as caught:
                ocr.ocr_image(b"\x89PNG\r\n", timeout=5)
        self.assertIn("не уложился", str(caught.exception))
        self.assertEqual(before, self.leftover("reportgen-ocr-"))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_broken_image_raises_ocr_error(self):
        image = self.directory / "битая.png"
        image.write_bytes("это не картинка, а обрывок файла".encode("utf-8"))
        with self.assertRaises(ocr.OcrError):
            ocr.ocr_image(image)

    def test_missing_file_raises_ocr_error(self):
        with mock.patch.object(ocr, "tesseract_binary", return_value="/usr/bin/tesseract"):
            with self.assertRaises(ocr.OcrError):
                ocr.ocr_image(self.directory / "нет-такого.png")

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_recognises_russian_text(self):
        image = make_image(self.directory / "скан.png")
        text = ocr.ocr_image(image)
        self.assertTrue(keywords_present(text, "линия", "антенны", "затухание"), text)

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_page_segmentation_mode_is_passed_through(self):
        """psm=6 («страница — один блок текста») пригодится на сканах таблиц."""
        image = make_image(self.directory / "скан.png")
        text = ocr.ocr_image(image, psm=6)
        self.assertTrue(keywords_present(text, "антенны"), text)

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_recognises_image_from_memory(self):
        image = make_image(self.directory / "скан.png")
        before = self.leftover("reportgen-ocr-")
        text = ocr.ocr_image(image.read_bytes())
        self.assertTrue(keywords_present(text, "радиорелейная"), text)
        # Сверяем с тем, что было ДО прогона, а не с пустотой: каталог общий на
        # всю машину, и чужое распознавание (второй приём, снятый по таймауту
        # прогон) оставляло здесь свой файл — тест падал, хотя убирать за собой
        # умеет и убирает.
        self.assertEqual(before, self.leftover("reportgen-ocr-"))


# --------------------------------------------------- разметка и качество ---

class MixedDocumentTests(TempCase):
    """Книга, где часть страниц набрана, а часть вклеена сканом.

    Так устроено полбиблиотеки отдела: пятнадцать страниц главы набраны, а
    приложение с таблицами измерений вклеено сканом. Признак «это скан»
    считался по СРЕДНЕМУ числу знаков на страницу, среднее выходило высоким,
    документ признавался обычным — и приложение пропадало молча: ни в тексте,
    ни в предупреждениях. Замер до починки: 20 страниц, 5 из них сканом, слова
    «ПРИЛОЖЕНИЕ» в тексте нет вовсе.
    """

    def книга(self):
        image = make_image(self.directory / "приложение.png",
                           ["ПРИЛОЖЕНИЕ А", "таблица измерений затухания"])
        полный = ["Обычный набранный текст главы отдела о методике измерений."] * 6
        страницы = [("text", [f"Страница {n}", *полный]) for n in range(1, 6)]
        страницы.append(("image", image))
        return make_pdf(self.directory / "книга.pdf", страницы)

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_вклеенный_скан_распознаётся(self):
        document = ocr.convert_pdf_ocr(self.книга())
        self.assertIn("ПРИЛОЖЕНИЕ", document.text.upper(),
                      "страница-скан пропала из документа: " + document.text[:300])

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_о_частичном_распознавании_сказано_честно(self):
        document = ocr.convert_pdf_ocr(self.книга())
        сказано = " ".join(document.warnings)
        self.assertIn("текстовый слой был не везде", сказано)
        self.assertNotIn("текстового слоя не было", сказано,
                         "про книгу с текстом сказано как про сплошной скан")

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_документ_целиком_с_текстом_не_трогаем(self):
        полный = ["Текста здесь достаточно для разбора без распознавания."] * 6
        путь = make_pdf(self.directory / "обычная.pdf", [
            ("text", [f"Страница {n}", *полный]) for n in range(1, 4)
        ])
        document = ocr.convert_pdf_ocr(путь)
        self.assertNotIn("ocr", document.meta,
                         "распознавание запустилось там, где текст и так есть")


class PageRangeTests(unittest.TestCase):
    """Номера страниц в предупреждении — диапазонами.

    У скана книги чертежей бывает три сотни, и строка на каждую топит
    настоящий отказ: человек пролистывает их все вместе с ним.
    """

    def test_подряд_идущие_свёрнуты(self):
        self.assertEqual("6-8", ocr.page_ranges([6, 7, 8]))

    def test_разрывы_сохранены(self):
        self.assertEqual("6-8, 11", ocr.page_ranges([6, 7, 8, 11]))

    def test_одиночные(self):
        self.assertEqual("2, 5, 9", ocr.page_ranges([9, 2, 5]))

    def test_повторы_и_беспорядок(self):
        self.assertEqual("1-3", ocr.page_ranges([3, 1, 2, 2]))

    def test_пусто(self):
        self.assertEqual("", ocr.page_ranges([]))


class MarkdownTests(unittest.TestCase):
    def test_headings_by_word_and_number(self):
        markdown = ocr.ocr_text_to_markdown(
            "ГЛАВА 2\n\n2.1 Модуляция сигнала\n\nСигнал передаётся в тракте.\n"
        )
        self.assertIn("# ГЛАВА 2", markdown)
        self.assertIn("## 2.1 Модуляция сигнала", markdown)
        self.assertNotIn("# Сигнал", markdown)

    def test_uppercase_line_is_heading(self):
        self.assertIn("## ТАБЛИЦА ДОПУСКОВ", ocr.ocr_text_to_markdown("ТАБЛИЦА ДОПУСКОВ\n"))

    def test_long_line_is_not_heading(self):
        line = "1. " + "очень длинная строка технического текста " * 3
        self.assertFalse(ocr.ocr_text_to_markdown(line).startswith("#"))

    def test_wrapped_word_is_joined(self):
        markdown = ocr.ocr_text_to_markdown("радиореле-\nйная линия связи\n")
        self.assertIn("радиорелейная линия связи", markdown)

    def test_table_rows_keep_line_breaks(self):
        """Столбец значений не должен слипаться в один абзац."""
        markdown = ocr.ocr_text_to_markdown("Частота 11 ГГц\nМощность 2 Вт\n")
        self.assertIn("Частота 11 ГГц\nМощность 2 Вт", markdown)

    def test_quality_warning_thresholds(self):
        self.assertIsNone(ocr.page_quality_warning(1, "т" * ocr.MIN_PAGE_CHARS))
        short = ocr.page_quality_warning(7, "мало")
        self.assertIn("страница 7 распозналась плохо", short)
        empty = ocr.page_quality_warning(7, "   ")
        self.assertIn("не распознан", empty)


# ---------------------------------------------------- конвертер картинок ---

class ImageConverterTests(TempCase):
    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_document_fields(self):
        image = make_image(self.directory / "страница.png")
        document = ocr.convert_image(image)
        self.assertIsInstance(document, ConvertedDocument)
        self.assertEqual("png", document.meta["source_format"])
        self.assertTrue(document.meta["ocr"])
        self.assertEqual(1, document.page_count)
        self.assertFalse(document.needs_ocr)
        self.assertEqual([(1, 0)], page_markers(document.text))
        self.assertTrue(keywords_present(document.text, "антенны", "затухание"), document.text)

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL, "нет tesseract или Pillow")
    def test_blank_image_warns(self):
        image = make_blank_image(self.directory / "пусто.png")
        document = ocr.convert_image(image)
        self.assertTrue(document.is_empty)
        self.assertTrue(any("не распознан" in item for item in document.warnings))

    def test_without_tesseract_warns_instead_of_failing(self):
        image = self.directory / "скан.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        with mock.patch.object(ocr, "tesseract_binary", return_value=None):
            document = ocr.convert_image(image)
        self.assertTrue(document.is_empty)
        self.assertTrue(document.needs_ocr)
        self.assertTrue(any("tesseract" in item for item in document.warnings))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_broken_file_warns_instead_of_failing(self):
        image = self.directory / "битая.jpg"
        image.write_bytes("JFIF-обрывок".encode("utf-8"))
        document = ocr.convert_image(image)
        self.assertTrue(document.is_empty)
        self.assertTrue(document.warnings)

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_dispatched_through_registry(self):
        image = make_image(self.directory / "скан.tiff")
        document = convert_file(image)
        self.assertEqual("tiff", document.meta["source_format"])
        self.assertTrue(keywords_present(document.text, "линия"), document.text)

    @unittest.skipUnless(HAS_TESSERACT and HAS_PIL and FONT, "нет tesseract, Pillow или шрифта")
    def test_page_number_reaches_chunks(self):
        """Маркер страницы должен доезжать до чанка — иначе не будет ссылки «с. 1»."""
        from reportgen.ingest.pipeline import chunks_from_markdown

        image = make_image(self.directory / "скан.png")
        document = ocr.convert_image(image)
        chunks = chunks_from_markdown(document.text, "library/скан", "literature", document.meta)
        self.assertTrue(chunks)
        self.assertEqual(1, chunks[0].meta["page"])


# --------------------------------------------------------- PDF и OCR ------

@unittest.skipUnless(HAS_PYMUPDF and HAS_PIL and FONT, "нет PyMuPDF, Pillow или шрифта")
class PdfOcrTests(TempCase):
    def _text_pages(self):
        return [
            "Расчёт запаса на замирания в пролёте радиорелейной линии связи",
            "Коэффициент готовности линии не хуже 99,99 процента за год",
            "Уровень сигнала на входе приёмника измеряется в дБм по методике",
        ]

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_scan_is_recognised(self):
        image = make_image(self.directory / "скан.png")
        path = make_pdf(self.directory / "скан.pdf", [("image", image)])
        document = ocr.convert_pdf_ocr(path)
        self.assertFalse(document.needs_ocr)
        self.assertTrue(document.meta["ocr"])
        self.assertEqual(1, document.meta["ocr_pages"])
        self.assertTrue(keywords_present(document.text, "антенны"), document.text)
        self.assertEqual([1], [number for number, _ in page_markers(document.text)])
        self.assertTrue(any("распознано страниц" in item for item in document.warnings))
        self.assertFalse(any("нужен слой OCR" in item for item in document.warnings))

    def test_text_layer_is_not_recognised_again(self):
        path = make_pdf(
            self.directory / "текст.pdf",
            [("text", self._text_pages()), ("text", self._text_pages())],
        )
        spy = OcrSpy(result="этого текста быть не должно")
        with mock.patch.object(ocr, "ocr_image", spy):
            document = ocr.convert_pdf_ocr(path)
        self.assertEqual([], spy.calls)
        self.assertFalse(document.needs_ocr)
        self.assertNotIn("ocr", document.meta)
        self.assertIn("Коэффициент готовности", document.text)

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_only_pages_without_text_are_recognised(self):
        image = make_image(self.directory / "скан.png")
        path = make_pdf(
            self.directory / "смешанный.pdf",
            [("text", ["Параметры радиорелейной линии связи и допуски"]), ("image", image)],
        )
        spy = OcrSpy(wrapped=ocr.ocr_image)
        with mock.patch.object(ocr, "ocr_image", spy):
            document = ocr.convert_pdf_ocr(path)
        self.assertEqual(1, len(spy.calls), "распознавать нужно только страницу без текста")
        self.assertIn("Параметры радиорелейной линии", document.text)
        self.assertTrue(keywords_present(document.text, "затухание"), document.text)
        self.assertEqual([1, 2], [number for number, _ in page_markers(document.text)])

    def test_without_tesseract_keeps_needs_ocr(self):
        image = make_blank_image(self.directory / "пусто.png")
        path = make_pdf(self.directory / "скан.pdf", [("image", image)])
        with mock.patch.object(ocr, "tesseract_binary", return_value=None):
            document = ocr.convert_pdf_ocr(path)
        self.assertTrue(document.needs_ocr)
        self.assertTrue(any("tesseract" in item for item in document.warnings))

    def test_page_limit_is_reported(self):
        image = make_blank_image(self.directory / "пусто.png")
        path = make_pdf(self.directory / "толстый.pdf", [("image", image)] * 3)
        notes = []
        spy = OcrSpy(result="распознанная строка достаточной длины для проверки")
        with mock.patch.object(ocr, "ocr_image", spy):
            pages = ocr.ocr_pdf_pages(path, [1, 2, 3], max_pages=2, warnings=notes)
        self.assertEqual([1, 2], sorted(pages))
        self.assertTrue(any("пропущено страниц 1" in item for item in notes), notes)

    def test_missing_page_is_reported(self):
        image = make_blank_image(self.directory / "пусто.png")
        path = make_pdf(self.directory / "одна.pdf", [("image", image)])
        notes = []
        spy = OcrSpy(result="строка")
        with mock.patch.object(ocr, "ocr_image", spy):
            pages = ocr.ocr_pdf_pages(path, [1, 99], warnings=notes)
        self.assertEqual([1], sorted(pages))
        self.assertTrue(any("99" in item for item in notes), notes)

    def test_recognition_failures_stop_the_document(self):
        """Битый скан не должен молотиться до последней страницы.

        Страницы распознаются пачками параллельно, поэтому счётчик неудач
        проверяется после пачки, а не после каждой страницы: на пачке из шести
        отработают шесть попыток, а не три. Существенно другое — что документ
        на сотню страниц не будет перемалываться целиком.
        """
        image = make_blank_image(self.directory / "пусто.png")
        path = make_pdf(self.directory / "битый.pdf", [("image", image)] * 40)
        notes = []
        failing = mock.Mock(side_effect=ocr.OcrError("tesseract упал"))
        with mock.patch.object(ocr, "ocr_image", failing):
            pages = ocr.ocr_pdf_pages(path, range(1, 41), warnings=notes)
        self.assertEqual({}, pages)
        limit = max(ocr.MAX_CONSECUTIVE_FAILURES, ocr.resolve_ocr_workers() * 2)
        self.assertLessEqual(failing.call_count, limit)
        self.assertGreaterEqual(failing.call_count, ocr.MAX_CONSECUTIVE_FAILURES)
        self.assertTrue(any("прервано" in item for item in notes), notes)

    def test_pages_are_recognised_in_parallel(self):
        # Один большой скан — сотни страниц по несколько секунд: без
        # параллелизма он занимает часы на одном ядре.
        image = make_blank_image(self.directory / "пусто2.png")
        path = make_pdf(self.directory / "многостраничный.pdf", [("image", image)] * 8)
        seen = []
        spy = mock.Mock(side_effect=lambda *a, **k: seen.append(1) or "строка")
        with mock.patch.object(ocr, "ocr_image", spy):
            pages = ocr.ocr_pdf_pages(path, range(1, 9), warnings=[])
        self.assertEqual(list(range(1, 9)), sorted(pages))
        self.assertEqual(8, len(seen))

    def test_progress_is_reported(self):
        # Двухчасовой файл не должен выглядеть как зависший.
        image = make_blank_image(self.directory / "пусто3.png")
        path = make_pdf(self.directory / "сдолгой.pdf", [("image", image)] * 5)
        steps = []
        with mock.patch.object(ocr, "ocr_image", mock.Mock(return_value="строка")):
            ocr.ocr_pdf_pages(path, range(1, 6), warnings=[],
                              progress=lambda done, total: steps.append((done, total)))
        self.assertTrue(steps)
        self.assertEqual((5, 5), steps[-1])

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_drawing_page_gets_quality_warning(self):
        image = make_blank_image(self.directory / "чертёж.png")
        path = make_pdf(self.directory / "чертёж.pdf", [("image", image)])
        document = ocr.convert_pdf_ocr(path)
        self.assertTrue(document.needs_ocr)
        self.assertTrue(any("не распознан" in item for item in document.warnings))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_dispatched_through_registry(self):
        image = make_image(self.directory / "скан.png")
        path = make_pdf(self.directory / "скан.pdf", [("image", image)])
        document = convert_file(path)
        self.assertTrue(keywords_present(document.text, "антенны"), document.text)

    def test_broken_pdf_does_not_raise(self):
        path = self.directory / "битый.pdf"
        path.write_bytes("%PDF-1.4 дальше обрыв".encode("utf-8"))
        document = ocr.convert_pdf_ocr(path)
        self.assertTrue(document.is_empty)
        self.assertTrue(document.warnings)


# ------------------------------------------------------------------ DjVu ---

@unittest.skipUnless(HAS_DJVU and HAS_PIL and FONT, "нет djvulibre, Pillow или шрифта")
class DjvuTests(TempCase):
    def _scan(self, name="книга.djvu", pages=2):
        images = [make_image(self.directory / f"стр{number}.png") for number in range(1, pages + 1)]
        return make_djvu(self.directory / name, images)

    def test_page_count(self):
        path = self._scan()
        self.assertEqual(2, djvu.djvu_page_count(path))

    def test_page_count_fallback_without_djvused(self):
        """Нет djvused — число страниц берётся из вывода djvutxt."""
        path = self._scan()
        real = djvu.djvu_binary

        def fake(name):
            return None if name == "djvused" else real(name)

        with mock.patch.object(djvu, "djvu_binary", side_effect=fake):
            notes = []
            self.assertEqual(2, djvu.djvu_page_count(path, warnings=notes))

    def test_text_layer_is_read_without_ocr(self):
        images = [make_blank_image(self.directory / f"пусто{number}.png") for number in (1, 2)]
        path = make_djvu(
            self.directory / "оцифрованная.djvu",
            images,
            texts=[
                "Спутниковая линия связи, диаметр антенны 2,4 метра",
                "Таблица допусков по уровню сигнала на входе приёмника",
            ],
        )
        spy = OcrSpy(result="распознавания быть не должно")
        with mock.patch.object(ocr, "ocr_image", spy):
            document = djvu.convert_djvu(path)
        self.assertEqual([], spy.calls)
        self.assertTrue(document.meta["text_layer"])
        self.assertEqual(2, document.meta["text_layer_pages"])
        self.assertFalse(document.meta["ocr"])
        self.assertIn("Спутниковая линия связи", document.text)
        self.assertIn("Таблица допусков", document.text)
        self.assertEqual([1, 2], [number for number, _ in page_markers(document.text)])

    def test_text_layer_limit_truncates_book(self):
        """Хвост многотысячестраничной подшивки не читается и не распознаётся молча."""
        images = [make_blank_image(self.directory / f"пусто{number}.png") for number in (1, 2)]
        path = make_djvu(
            self.directory / "подшивка.djvu",
            images,
            texts=[
                "Спутниковая линия связи, диаметр антенны 2,4 метра",
                "Таблица допусков по уровню сигнала на входе приёмника",
            ],
        )
        spy = OcrSpy(result="распознавания быть не должно")
        with mock.patch.object(djvu, "MAX_TEXT_PAGES", 1), mock.patch.object(ocr, "ocr_image", spy):
            document = djvu.convert_djvu(path)
        self.assertEqual([], spy.calls)
        self.assertEqual([1], [number for number, _ in page_markers(document.text)])
        self.assertTrue(any("1 из 2" in item for item in document.warnings),
                        document.warnings)

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_scan_without_layer_goes_through_ocr(self):
        path = self._scan(pages=1)
        document = djvu.convert_djvu(path)
        self.assertEqual("djvu", document.meta["source_format"])
        self.assertTrue(document.meta["ocr"])
        self.assertFalse(document.needs_ocr)
        self.assertEqual(1, document.page_count)
        self.assertTrue(keywords_present(document.text, "антенны", "затухание"), document.text)
        self.assertTrue(any("распознано страниц" in item for item in document.warnings))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_ocr_only_for_pages_without_layer(self):
        """Слой есть на первой странице, вторую надо распознать — и только её."""
        images = [make_image(self.directory / f"стр{number}.png") for number in (1, 2)]
        path = make_djvu(
            self.directory / "смешанная.djvu",
            images,
            texts=["Спутниковая линия связи, диаметр антенны 2,4 метра", ""],
        )
        spy = OcrSpy(wrapped=ocr.ocr_image)
        with mock.patch.object(ocr, "ocr_image", spy):
            document = djvu.convert_djvu(path)
        self.assertEqual(1, len(spy.calls))
        self.assertIn("Спутниковая линия связи", document.text)
        self.assertTrue(keywords_present(document.text, "затухание"), document.text)

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_page_limit_is_reported(self):
        path = self._scan(pages=2)
        with mock.patch.object(djvu, "MAX_OCR_PAGES", 1):
            document = djvu.convert_djvu(path)
        self.assertTrue(any("пропущено страниц 1" in item for item in document.warnings),
                        document.warnings)
        self.assertEqual([1], [number for number, _ in page_markers(document.text)])

    def test_without_tesseract_warns_instead_of_failing(self):
        path = self._scan(pages=1)
        with mock.patch.object(ocr, "tesseract_binary", return_value=None):
            document = djvu.convert_djvu(path)
        self.assertTrue(document.is_empty)
        self.assertTrue(document.needs_ocr)
        self.assertTrue(any("tesseract" in item for item in document.warnings))

    def test_broken_file_warns_instead_of_failing(self):
        path = self.directory / "битая.djvu"
        path.write_bytes("AT&Tи дальше мусор".encode("utf-8") * 8)
        document = djvu.convert_djvu(path)
        self.assertTrue(document.is_empty)
        self.assertTrue(any("повреждён" in item for item in document.warnings),
                        document.warnings)

    def test_timeout_of_external_tool(self):
        path = self._scan(pages=1)
        error = subprocess.TimeoutExpired(cmd="djvused", timeout=1)
        with mock.patch.object(djvu.subprocess, "run", side_effect=error):
            document = djvu.convert_djvu(path)
        self.assertTrue(document.is_empty)
        self.assertTrue(any("не уложился" in item for item in document.warnings),
                        document.warnings)

    def test_render_page_produces_image(self):
        path = self._scan(pages=1)
        target = self.directory / "стр1.pnm"
        djvu.render_djvu_page(path, 1, target)
        self.assertTrue(target.is_file())
        self.assertGreater(target.stat().st_size, 100)

    def test_render_of_broken_file_raises_tool_error(self):
        path = self.directory / "битая.djvu"
        path.write_bytes("AT&Tи дальше мусор".encode("utf-8") * 8)
        with self.assertRaises(djvu.DjvuToolError):
            djvu.render_djvu_page(path, 1, self.directory / "нет.pnm")

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_temporary_files_are_removed(self):
        before = self.leftover("reportgen-djvu-")
        before_ocr = self.leftover("reportgen-ocr-")
        path = self._scan(pages=1)
        djvu.convert_djvu(path)
        self.assertEqual(before, self.leftover("reportgen-djvu-"))
        self.assertEqual(before_ocr, self.leftover("reportgen-ocr-"))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_dispatched_through_registry(self):
        path = self._scan(pages=1)
        document = convert_file(path)
        self.assertEqual("djvu", document.meta["source_format"])
        self.assertTrue(keywords_present(document.text, "линия"), document.text)

    def test_missing_tools_are_reported(self):
        path = self.directory / "книга.djvu"
        path.write_bytes(b"AT&T")
        with mock.patch.object(djvu, "djvu_binary", return_value=None):
            document = djvu.convert_djvu(path)
        self.assertTrue(document.is_empty)
        self.assertTrue(any("djvutxt" in item for item in document.warnings))
        self.assertTrue(any("Windows" in item for item in document.warnings))


if __name__ == "__main__":  # pragma: no cover — ручной запуск
    unittest.main()


class MultipageTiffTests(unittest.TestCase):
    """Многостраничный TIFF — штатный вывод сканера МФУ и факс-сервера.

    Раньше весь такой файл уходил в tesseract одним куском: 120-секундный
    таймаут был отведён на весь документ, и скан протокола на сорок страниц
    пропадал целиком, с предупреждением «страница слишком большая», уводящим
    не в ту сторону. А если укладывался — склеивался в одну страницу, и ссылка
    «с. 17» в отчёте указывала на первую.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make_tiff(self, pages: int) -> Path:
        from PIL import Image, ImageDraw

        frames = []
        for number in range(pages):
            image = Image.new("RGB", (1200, 1600), "white")
            draw = ImageDraw.Draw(image)
            for line in range(12):
                draw.text((60, 60 + line * 70),
                          f"Stranica {number + 1} stroka {line + 1} uroven minus 62 dBm",
                          fill="black")
            frames.append(image)
        path = self.directory / "протокол.tif"
        frames[0].save(path, save_all=True, append_images=frames[1:])
        return path

    def test_frame_count_is_detected(self):
        path = self.make_tiff(4)
        self.assertEqual(4, ocr.image_frame_count(path))

    def test_single_frame_stays_single(self):
        from PIL import Image

        path = self.directory / "одна.png"
        Image.new("RGB", (300, 200), "white").save(path)
        self.assertEqual(1, ocr.image_frame_count(path))

    def test_pages_are_counted_without_pillow(self):
        """Без Pillow страницы не разделить — но сосчитать можно и нужно.

        На неполной установке сорокастраничный протокол уходил в библиотеку
        одной первой страницей, молча: по остальным тридцати девяти поиск не
        находил ничего, и человек об этом не узнавал.
        """
        path = self.make_tiff(4)
        self.assertEqual(4, ocr._tiff_frames(path))

    def test_a_png_is_not_mistaken_for_a_multipage_tiff(self):
        from PIL import Image

        path = self.directory / "снимок.png"
        Image.new("RGB", (300, 200), "white").save(path)
        self.assertEqual(1, ocr._tiff_frames(path))

    def test_the_loss_of_pages_is_announced(self):
        """Молчаливая потеря страниц — худшее, что может сделать библиотека."""
        import os
        import subprocess
        import sys
        from pathlib import Path as _Path

        корень = _Path(__file__).resolve().parents[1]
        path = self.make_tiff(4)
        заглушка = self.directory / "без-pillow"
        (заглушка / "PIL").mkdir(parents=True)
        (заглушка / "PIL" / "__init__.py").write_text(
            "raise ImportError(\"No module named 'PIL'\")", encoding="utf-8")
        готово = subprocess.run(
            [sys.executable, "-c",
             "import json,sys;"
             "from reportgen.ingest.formats import ocr;"
             "r=ocr.convert_image(__import__('pathlib').Path(sys.argv[1]));"
             "print(json.dumps({'p': r.meta.get('page_count'),"
             " 'w': r.warnings}, ensure_ascii=False))", str(path)],
            capture_output=True, text=True, timeout=600,
            env=dict(os.environ, PYTHONPATH=os.pathsep.join(
                [str(заглушка), str(корень / "src")])))
        self.assertEqual(0, готово.returncode, готово.stderr)
        итог = json.loads(готово.stdout.strip().splitlines()[-1])
        self.assertEqual(4, итог["p"])
        self.assertTrue(any("распознана только первая" in note
                            for note in итог["w"]),
                        f"о потере страниц не сказано: {итог['w']}")

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_every_page_gets_its_own_marker(self):
        path = self.make_tiff(4)
        result = ocr.convert_image(path)
        self.assertEqual(4, result.page_count)
        self.assertEqual(4, result.meta["page_count"])
        self.assertEqual(4, result.text.count("<!-- page:"))

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_chunks_keep_real_page_numbers(self):
        # Иначе ссылка «с. 17» в отчёте указывает на первую страницу, и при
        # сверке с бумажным оригиналом факт не находится.
        from reportgen.ingest.pipeline import chunks_from_markdown

        path = self.make_tiff(5)
        result = ocr.convert_image(path)
        chunks = chunks_from_markdown(result.text, "тест", "literature", result.meta)
        pages = {chunk.meta.get("page") for chunk in chunks}
        self.assertGreater(len(pages), 1, f"все фрагменты на одной странице: {pages}")
        self.assertLessEqual(max(pages), 5)

    @unittest.skipUnless(HAS_TESSERACT, "нет tesseract")
    def test_reports_how_many_pages_were_read(self):
        path = self.make_tiff(3)
        result = ocr.convert_image(path)
        self.assertEqual(3, result.meta["ocr_pages"])
        self.assertTrue(any("распознано страниц 3 из 3" in item for item in result.warnings),
                        result.warnings)

    def test_page_limit_is_announced(self):
        path = self.make_tiff(3)
        with mock.patch.object(ocr, "MAX_OCR_PAGES", 2), \
             mock.patch.object(ocr, "ocr_image", mock.Mock(return_value="строка текста")):
            result = ocr.convert_image(path)
        self.assertTrue(any("ограничено 2 страницами" in item for item in result.warnings),
                        result.warnings)

    def test_broken_frame_does_not_lose_the_document(self):
        path = self.make_tiff(4)
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ocr.OcrError("tesseract упал")
            return "строка текста"

        with mock.patch.object(ocr, "ocr_image", mock.Mock(side_effect=flaky)):
            result = ocr.convert_image(path)
        self.assertGreater(result.meta["ocr_pages"], 0)
        self.assertTrue(any("страница 2" in item for item in result.warnings), result.warnings)
