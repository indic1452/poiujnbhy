"""Тесты конвертеров OpenDocument, книг и веб-форматов.

Все проверяемые файлы собираются программно: ODF, ODS, ODP и EPUB — это ZIP с
XML внутри, поэтому :mod:`zipfile` и строка с разметкой дают полноценный
документ, неотличимый от сохранённого редактором. Ни один тест не ходит в сеть,
не требует сторонних пакетов и ничего не оставляет в репозитории.
"""

import shutil
import tempfile
import time
import unittest
import zipfile
from email.message import EmailMessage
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.ingest import convert as convert_module
from reportgen.ingest import registry
from reportgen.ingest.formats import opendoc, web
from reportgen.ingest.pipeline import chunks_from_markdown

# --------------------------------------------------------- сборка файлов ---

_ODF_NS = " ".join(
    f'xmlns:{prefix}="{uri}"'
    for prefix, uri in (
        ("office", "urn:oasis:names:tc:opendocument:xmlns:office:1.0"),
        ("text", "urn:oasis:names:tc:opendocument:xmlns:text:1.0"),
        ("table", "urn:oasis:names:tc:opendocument:xmlns:table:1.0"),
        ("draw", "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"),
        ("style", "urn:oasis:names:tc:opendocument:xmlns:style:1.0"),
        ("fo", "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"),
        ("meta", "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"),
        ("presentation", "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"),
        ("dc", "http://purl.org/dc/elements/1.1/"),
        ("xlink", "http://www.w3.org/1999/xlink"),
    )
)

MIMETYPES = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odg": "application/vnd.oasis.opendocument.graphics",
}


def content_xml(body: str, styles: str = "") -> str:
    """Готовый content.xml с телом документа."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content {_ODF_NS} office:version="1.3">'
        f"{styles}"
        f"<office:body>{body}</office:body>"
        "</office:document-content>"
    )


def meta_xml(title: str = "", author: str = "", pages: int = 0) -> str:
    parts = []
    if title:
        parts.append(f"<dc:title>{title}</dc:title>")
    if author:
        parts.append(f"<dc:creator>{author}</dc:creator>")
    if pages:
        parts.append(f'<meta:document-statistic meta:page-count="{pages}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-meta {_ODF_NS} office:version=\"1.3\">"
        f"<office:meta>{''.join(parts)}</office:meta>"
        "</office:document-meta>"
    )


def make_odf(path: Path, kind: str, body: str, *, styles: str = "", meta: str = "",
             extra: dict | None = None) -> Path:
    """Собирает документ OpenDocument нужного вида."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", MIMETYPES[kind])
        archive.writestr("content.xml", content_xml(body, styles))
        if meta:
            archive.writestr("meta.xml", meta)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def odt_body(inner: str) -> str:
    return f"<office:text>{inner}</office:text>"


def ods_body(inner: str) -> str:
    return f"<office:spreadsheet>{inner}</office:spreadsheet>"


def odp_body(inner: str) -> str:
    return f"<office:presentation>{inner}</office:presentation>"


def odg_body(inner: str) -> str:
    return f"<office:drawing>{inner}</office:drawing>"


def cell(text: str, repeat: int = 0) -> str:
    attribute = f' table:number-columns-repeated="{repeat}"' if repeat else ""
    if text:
        return f'<table:table-cell{attribute}><text:p>{text}</text:p></table:table-cell>'
    return f"<table:table-cell{attribute}/>"


def row(*cells: str) -> str:
    return "<table:table-row>" + "".join(cells) + "</table:table-row>"


def make_epub(path: Path, chapters, spine, *, title="Книга", creator="Автор",
              extra: dict | None = None) -> Path:
    """EPUB из глав ``[(имя, html)]`` и явного порядка spine ``[имя, ...]``."""
    items = "".join(
        f'<item id="{name}" href="{name}.xhtml" media-type="application/xhtml+xml"/>'
        for name, _ in chapters
    )
    refs = "".join(f'<itemref idref="{name}"/>' for name in spine)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>"
        "<dc:language>ru</dc:language></metadata>"
        f"<manifest>{items}</manifest><spine>{refs}</spine></package>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", opf)
        for name, html in chapters:
            archive.writestr(f"OEBPS/{name}.xhtml", html)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
    'xmlns:l="http://www.w3.org/1999/xlink">'
    "<description><title-info>"
    "<book-title>Основы спутниковой связи</book-title>"
    "<author><first-name>Иван</first-name><last-name>Петров</last-name></author>"
    "<lang>ru</lang>"
    "<annotation><p>Пособие по расчёту бюджета спутниковой линии.</p></annotation>"
    "</title-info></description>"
    "<body>"
    "<title><p>Основы спутниковой связи</p></title>"
    "<section><title><p>Глава 1. Диапазоны</p></title>"
    "<epigraph><p>Связь начинается с бюджета линии.</p></epigraph>"
    "<p>В Ku-диапазоне дождевое затухание определяет запас на замирания.</p>"
    "<p>Подробности приведены в примечании<a l:href=\"#n1\" type=\"note\">1</a>.</p>"
    "<section><title><p>1.1. Ku и Ka</p></title>"
    "<p>Ka-диапазон чувствительнее к осадкам.</p></section>"
    "</section></body>"
    '<body name="notes"><section id="n1"><title><p>1</p></title>'
    "<p>Расчёт по рекомендации ITU-R P.618.</p></section></body>"
    '<binary id="cover.jpg" content-type="image/jpeg">QUJDREVGRw==</binary>'
    "</FictionBook>"
)

HTML_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Протокол измерений</title>
<style>body { color: red; }</style></head>
<body>
<nav>Главная | Печать | Обсуждение</nav>
<h1>Протокол измерений</h1>
<p>Измерения выполнены&nbsp;в полосе &laquo;30 кГц&raquo; при усреднении.</p>
<h2>Условия</h2>
<ul><li>Температура 20 &deg;C</li><li>Влажность 60 %</li></ul>
<table>
<tr><th>Параметр</th><th>Значение</th></tr>
<tr><td>EVM</td><td>3,1 %</td></tr>
<tr><td>Уровень</td><td>&minus;72 дБм</td></tr>
</table>
<p>Методика описана в <a href="https://example.org/method">руководстве</a>.</p>
<script>var secret = 1;</script>
<footer>Отдел измерений, 2025</footer>
</body></html>
"""


class TempCase(unittest.TestCase):
    """Общий временный каталог: в репозиторий тестовые файлы не кладём."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="reportgen-formats-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def path(self, name: str) -> Path:
        return self.root / name


# ------------------------------------------------------------------- ODT ---

class OdtTests(TempCase):
    def test_headings_get_hash_by_outline_level(self):
        body = odt_body(
            '<text:h text:outline-level="1">Радиорелейная линия</text:h>'
            "<text:p>Пролёт длиной 42 км.</text:p>"
            '<text:h text:outline-level="2">Состав оборудования</text:h>'
            '<text:h text:outline-level="3">Наружный блок</text:h>'
            "<text:p>ODU с интегрированной антенной.</text:p>"
        )
        result = opendoc.convert_odt(make_odf(self.path("a.odt"), "odt", body))
        self.assertIn("# Радиорелейная линия", result.text)
        self.assertIn("## Состав оборудования", result.text)
        self.assertIn("### Наружный блок", result.text)
        # Уровень заголовка должен быть ровно тот, что указан в документе.
        self.assertNotIn("#### ", result.text)
        self.assertEqual(result.meta["source_format"], "odt")

    def test_heading_without_level_is_first_level(self):
        body = odt_body("<text:h>Общие положения</text:h><text:p>Текст.</text:p>")
        result = opendoc.convert_odt(make_odf(self.path("b.odt"), "odt", body))
        self.assertIn("# Общие положения", result.text)
        self.assertEqual(result.title, "Общие положения")

    def test_lists_and_nested_lists(self):
        body = odt_body(
            "<text:list>"
            "<text:list-item><text:p>Внешний блок</text:p>"
            "<text:list><text:list-item><text:p>Антенна 0,6 м</text:p></text:list-item></text:list>"
            "</text:list-item>"
            "<text:list-item><text:p>Внутренний блок</text:p></text:list-item>"
            "</text:list>"
        )
        result = opendoc.convert_odt(make_odf(self.path("c.odt"), "odt", body))
        self.assertIn("- Внешний блок", result.text)
        self.assertIn("  - Антенна 0,6 м", result.text)
        self.assertIn("- Внутренний блок", result.text)
        # Подряд идущие пункты — один абзац, а не три чанка.
        self.assertIn("- Внешний блок\n  - Антенна 0,6 м\n- Внутренний блок", result.text)

    def test_table_becomes_whole_markdown_table(self):
        body = odt_body(
            '<table:table table:name="Допуски">'
            '<table:table-column table:number-columns-repeated="3"/>'
            + row(cell("Параметр"), cell("Норма"), cell("Факт"))
            + row(cell("EVM, %"), cell("не более 5"), cell("3,1"))
            + row(cell("Уровень, дБм"), cell("−75…−60"), cell("−72"))
            + "</table:table>"
        )
        result = opendoc.convert_odt(make_odf(self.path("d.odt"), "odt", body))
        self.assertIn("| Параметр | Норма | Факт |", result.text)
        self.assertIn("| --- | --- | --- |", result.text)
        self.assertIn("| EVM, % | не более 5 | 3,1 |", result.text)
        self.assertIn("| Уровень, дБм | −75…−60 | −72 |", result.text)
        # Таблица не разорвана: шапка, разделитель и три строки идут подряд.
        lines = result.text.splitlines()
        table_lines = [index for index, line in enumerate(lines) if line.startswith("|")]
        self.assertEqual(len(table_lines), 4)
        self.assertEqual(table_lines, list(range(table_lines[0], table_lines[0] + 4)))

    def test_footnote_and_link_are_kept_in_text(self):
        body = odt_body(
            "<text:p>Норма взята из стандарта"
            '<text:note text:note-class="footnote"><text:note-citation>1</text:note-citation>'
            "<text:note-body><text:p>ГОСТ Р 53532, пункт 4.2</text:p></text:note-body>"
            "</text:note>"
            ' и уточнена <text:a xlink:href="https://example.org/rd">в РД</text:a>.</text:p>'
        )
        result = opendoc.convert_odt(make_odf(self.path("e.odt"), "odt", body))
        self.assertIn("[сноска: ГОСТ Р 53532, пункт 4.2]", result.text)
        self.assertIn("в РД (https://example.org/rd)", result.text)

    def test_soft_page_breaks_become_page_markers(self):
        body = odt_body(
            "<text:p>Первая страница отчёта.</text:p>"
            "<text:soft-page-break/>"
            "<text:p>Вторая страница отчёта.</text:p>"
            "<text:soft-page-break/>"
            "<text:p>Третья страница отчёта.</text:p>"
        )
        result = opendoc.convert_odt(make_odf(self.path("f.odt"), "odt", body))
        self.assertIn(convert_module.page_marker(1), result.text)
        self.assertIn(convert_module.page_marker(2), result.text)
        self.assertIn(convert_module.page_marker(3), result.text)
        self.assertEqual(result.page_count, 3)
        self.assertLess(
            result.text.index(convert_module.page_marker(2)),
            result.text.index("Вторая страница"),
        )

    def test_style_page_break_starts_new_page(self):
        styles = (
            "<office:automatic-styles>"
            '<style:style style:name="P2" style:family="paragraph">'
            '<style:paragraph-properties fo:break-before="page"/></style:style>'
            "</office:automatic-styles>"
        )
        body = odt_body(
            "<text:p>Титульный лист.</text:p>"
            '<text:h text:outline-level="1" text:style-name="P2">Раздел 1</text:h>'
            "<text:p>Содержание раздела.</text:p>"
        )
        result = opendoc.convert_odt(
            make_odf(self.path("g.odt"), "odt", body, styles=styles)
        )
        self.assertIn(convert_module.page_marker(2), result.text)
        self.assertLess(
            result.text.index(convert_module.page_marker(2)),
            result.text.index("# Раздел 1"),
        )

    def test_named_style_page_break_from_styles_xml(self):
        styles_document = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<office:document-styles {_ODF_NS} office:version="1.3"><office:styles>'
            '<style:style style:name="Heading_20_1" style:family="paragraph">'
            '<style:paragraph-properties fo:break-before="page"/></style:style>'
            "</office:styles></office:document-styles>"
        )
        body = odt_body(
            "<text:p>Титульный лист.</text:p>"
            '<text:h text:outline-level="1" text:style-name="Heading_20_1">Раздел 1</text:h>'
            "<text:p>Содержание раздела.</text:p>"
        )
        # Разрыв объявлен именованным стилем — он лежит не в content.xml,
        # а в styles.xml, и его тоже надо учитывать.
        result = opendoc.convert_odt(make_odf(
            self.path("named.odt"), "odt", body, extra={"styles.xml": styles_document}))
        self.assertIn(convert_module.page_marker(2), result.text)
        self.assertLess(
            result.text.index(convert_module.page_marker(2)),
            result.text.index("# Раздел 1"),
        )

    def test_no_page_markers_without_breaks(self):
        body = odt_body("<text:p>Документ без разрывов страниц.</text:p>")
        result = opendoc.convert_odt(make_odf(self.path("h.odt"), "odt", body))
        # Выдуманный номер страницы хуже отсутствующего: ссылка «с. 1» на текст
        # с сороковой страницы вводит инженера в заблуждение.
        self.assertEqual(convert_module.page_markers(result.text), [])

    def test_metadata_gives_title_and_author(self):
        body = odt_body("<text:h>Служебный заголовок</text:h><text:p>Текст.</text:p>")
        result = opendoc.convert_odt(make_odf(
            self.path("i.odt"), "odt", body,
            meta=meta_xml(title="Методика измерений РРЛ", author="Петров И.И.", pages=12),
        ))
        self.assertEqual(result.title, "Методика измерений РРЛ")
        self.assertEqual(result.meta["author"], "Петров И.И.")
        self.assertEqual(result.page_count, 12)

    def test_flat_opendocument_is_read(self):
        path = self.path("j.fodt")
        path.write_text(
            content_xml(odt_body('<text:h text:outline-level="1">Плоский ODF</text:h>'
                                 "<text:p>Документ одним XML-файлом.</text:p>")),
            encoding="utf-8",
        )
        result = opendoc.convert_odt(path)
        self.assertIn("# Плоский ODF", result.text)
        self.assertFalse(result.warnings)


# --------------------------------------------------------- ODT: поломки ---

class BrokenFileTests(TempCase):
    def test_broken_zip_gives_warning_not_exception(self):
        path = self.path("broken.odt")
        path.write_bytes(b"PK\x03\x04" + b"\xff" * 200)
        result = opendoc.convert_odt(path)
        self.assertTrue(result.is_empty)
        self.assertTrue(result.warnings)
        self.assertIn("повреждён", result.warnings[0])

    def test_not_an_archive_at_all(self):
        path = self.path("plain.odt")
        path.write_bytes("это просто текст, а не документ".encode("utf-8"))
        result = opendoc.convert_odt(path)
        self.assertTrue(result.is_empty)
        self.assertIn("не является документом OpenDocument", result.warnings[0])

    def test_empty_file_gives_warning(self):
        path = self.path("empty.odt")
        path.write_bytes(b"")
        result = opendoc.convert_odt(path)
        self.assertTrue(result.is_empty)
        self.assertIn("пуст", result.warnings[0])

    def test_zip_without_content_xml(self):
        path = self.path("stranger.ods")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", "не документ")
        result = opendoc.convert_ods(path)
        self.assertTrue(result.is_empty)
        self.assertIn("content.xml", result.warnings[0])

    def test_password_protected_document(self):
        path = self.path("secret.odt")
        make_odf(
            path, "odt", odt_body("<text:p>Текст.</text:p>"),
            extra={"META-INF/manifest.xml": (
                '<?xml version="1.0"?><manifest:manifest '
                'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                '<manifest:file-entry manifest:full-path="content.xml">'
                "<manifest:encryption-data/></manifest:file-entry></manifest:manifest>"
            )},
        )
        result = opendoc.convert_odt(path)
        self.assertTrue(result.is_empty)
        self.assertIn("паролем", result.warnings[0])

    def test_broken_content_xml(self):
        path = self.path("cut.odt")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("content.xml", "<office:document-content><office:body>")
        result = opendoc.convert_odt(path)
        self.assertTrue(result.is_empty)
        self.assertTrue(result.warnings)

    def test_extension_and_content_disagree(self):
        body = ods_body(
            '<table:table table:name="Лист1">' + row(cell("A"), cell("B")) + "</table:table>"
        )
        result = opendoc.convert_odt(make_odf(self.path("k.odt"), "ods", body))
        self.assertIn("электронная таблица", result.warnings[0])
        self.assertEqual(result.meta["source_format"], "ods")
        self.assertIn("| A | B |", result.text)


# ------------------------------------------------------------------- ODS ---

class OdsTests(TempCase):
    def test_sheets_become_sections_with_page_markers(self):
        body = ods_body(
            '<table:table table:name="Бюджет канала">'
            + row(cell("Статья"), cell("дБ"))
            + row(cell("ЭИИМ"), cell("52,0"))
            + "</table:table>"
            '<table:table table:name="Замирания">'
            + row(cell("Месяц"), cell("Запас, дБ"))
            + row(cell("Июль"), cell("4,2"))
            + "</table:table>"
        )
        result = opendoc.convert_ods(make_odf(self.path("a.ods"), "ods", body))
        self.assertIn("## Лист «Бюджет канала»", result.text)
        self.assertIn("## Лист «Замирания»", result.text)
        self.assertIn("| ЭИИМ | 52,0 |", result.text)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.meta["sheets"], 2)
        self.assertEqual(
            [number for number, _ in convert_module.page_markers(result.text)], [1, 2]
        )
        self.assertLess(
            result.text.index(convert_module.page_marker(2)),
            result.text.index("## Лист «Замирания»"),
        )

    def test_title_falls_back_to_file_name(self):
        body = ods_body(
            '<table:table table:name="Лист1">' + row(cell("A"), cell("B")) + "</table:table>"
        )
        result = opendoc.convert_ods(make_odf(self.path("бюджет-канала.ods"), "ods", body))
        # Название листа — не название документа: иначе в ссылке под цитатой
        # окажется «Лист1» вместо имени файла.
        self.assertEqual(result.title, "бюджет-канала")
        with_meta = opendoc.convert_ods(make_odf(
            self.path("x.ods"), "ods", body, meta=meta_xml(title="Бюджет линии")))
        self.assertEqual(with_meta.title, "Бюджет линии")

    def test_repeated_columns_are_expanded(self):
        body = ods_body(
            '<table:table table:name="Развороты">'
            + row(cell("Канал"), cell("1"), cell("2"), cell("3"))
            + row(cell("Состояние"), cell("норма", repeat=3))
            + "</table:table>"
        )
        result = opendoc.convert_ods(make_odf(self.path("b.ods"), "ods", body))
        self.assertIn("| Состояние | норма | норма | норма |", result.text)

    def test_huge_repeat_does_not_explode(self):
        body = ods_body(
            '<table:table table:name="Хвост">'
            + row(cell("Параметр"), cell("Значение"), cell("", repeat=16384))
            + row(cell("Повтор"), cell("x", repeat=1048576))
            + "</table:table>"
        )
        started = time.perf_counter()
        result = opendoc.convert_ods(make_odf(self.path("c.ods"), "ods", body))
        spent = time.perf_counter() - started
        self.assertLess(spent, 5.0, "разворачивание повторов не должно занимать секунды")
        lines = [line for line in result.text.splitlines() if line.startswith("|")]
        # Повтор в миллион ячеек усечён до предела, а не развёрнут буквально.
        repeated = next(line for line in lines if line.startswith("| Повтор"))
        self.assertLessEqual(repeated.count(" x "), opendoc.MAX_CELL_REPEAT)
        self.assertLessEqual(max(line.count("|") for line in lines),
                             opendoc.MAX_CELL_REPEAT + 2)
        self.assertTrue(any("усечены" in warning for warning in result.warnings))
        # Хвост из 16384 пустых ячеек отброшен целиком, а не развёрнут: ширину
        # таблицы задала строка с данными, а не оформление пустого листа.
        self.assertTrue(lines[0].startswith("| Параметр | Значение |"))

    def test_repeated_rows_are_expanded_with_limit(self):
        body = ods_body(
            '<table:table table:name="Строки">'
            + row(cell("Пролёт"), cell("Запас"))
            + '<table:table-row table:number-rows-repeated="3">'
            + cell("Р-1") + cell("4,0")
            + "</table:table-row>"
            + "</table:table>"
        )
        result = opendoc.convert_ods(make_odf(self.path("d.ods"), "ods", body))
        self.assertEqual(result.text.count("| Р-1 | 4,0 |"), 3)

    def test_numeric_cell_without_text_uses_value(self):
        body = ods_body(
            '<table:table table:name="Числа">'
            + row(cell("Частота"), cell("Уровень"))
            + "<table:table-row>"
            + '<table:table-cell office:value-type="float" office:value="11700"/>'
            + '<table:table-cell office:value-type="float" office:value="-72.4"/>'
            + "</table:table-row>"
            + "</table:table>"
        )
        result = opendoc.convert_ods(make_odf(self.path("e.ods"), "ods", body))
        self.assertIn("| 11700 | -72.4 |", result.text)

    def test_empty_sheets_are_reported(self):
        body = ods_body(
            '<table:table table:name="Данные">'
            + row(cell("A"), cell("B"))
            + "</table:table>"
            '<table:table table:name="Черновик"/>'
        )
        result = opendoc.convert_ods(make_odf(self.path("f.ods"), "ods", body))
        self.assertEqual(result.meta["sheets"], 1)
        self.assertTrue(any("Черновик" in warning for warning in result.warnings))

    def test_page_numbers_reach_chunks(self):
        filler = ("Значение получено по методике измерений и подтверждено "
                  "повторным прогоном на том же оборудовании. ")
        body = ods_body(
            '<table:table table:name="Первый">'
            + row(cell("Параметр"), cell("Комментарий"))
            + row(cell("EVM"), cell(filler * 2))
            + "</table:table>"
            '<table:table table:name="Второй">'
            + row(cell("Параметр"), cell("Комментарий"))
            + row(cell("BER"), cell(filler * 2))
            + "</table:table>"
        )
        result = opendoc.convert_ods(make_odf(self.path("g.ods"), "ods", body))
        chunks = chunks_from_markdown(result.text, "library/measurements",
                                      meta={"title": "Измерения"})
        pages = {chunk.meta.get("page") for chunk in chunks}
        self.assertEqual(pages, {1, 2})


# ------------------------------------------------------------------- ODP ---

class OdpTests(TempCase):
    def test_slides_become_sections(self):
        slide_one = (
            '<draw:page draw:name="page1">'
            '<draw:frame presentation:class="title"><draw:text-box>'
            "<text:p>Спутниковая линия Ku</text:p></draw:text-box></draw:frame>"
            '<draw:frame presentation:class="outline"><draw:text-box><text:list>'
            "<text:list-item><text:p>Дождевое затухание</text:p></text:list-item>"
            "<text:list-item><text:p>Запас на замирания</text:p></text:list-item>"
            "</text:list></draw:text-box></draw:frame>"
            "<presentation:notes><draw:frame><draw:text-box>"
            "<text:p>Сослаться на ITU-R P.618</text:p>"
            "</draw:text-box></draw:frame></presentation:notes>"
            "</draw:page>"
        )
        slide_two = (
            '<draw:page draw:name="page2">'
            '<draw:frame presentation:class="title"><draw:text-box>'
            "<text:p>Результаты</text:p></draw:text-box></draw:frame>"
            "<draw:frame><draw:text-box>"
            '<table:table table:name="t1">'
            + row(cell("Месяц"), cell("Готовность"))
            + row(cell("Июль"), cell("99,95 %"))
            + "</table:table>"
            "</draw:text-box></draw:frame>"
            "</draw:page>"
        )
        result = opendoc.convert_odp(
            make_odf(self.path("a.odp"), "odp", odp_body(slide_one + slide_two))
        )
        self.assertIn("## Слайд 1. Спутниковая линия Ku", result.text)
        self.assertIn("## Слайд 2. Результаты", result.text)
        self.assertIn("- Дождевое затухание", result.text)
        self.assertIn("Заметки к слайду: Сослаться на ITU-R P.618", result.text)
        self.assertIn("| Июль | 99,95 % |", result.text)
        self.assertEqual(result.page_count, 2)
        self.assertEqual(
            [number for number, _ in convert_module.page_markers(result.text)], [1, 2]
        )

    def test_slide_without_title_is_still_numbered(self):
        slide = (
            '<draw:page draw:name="page1"><draw:frame><draw:text-box>'
            "<text:p>Только текст без заголовка</text:p>"
            "</draw:text-box></draw:frame></draw:page>"
        )
        result = opendoc.convert_odp(make_odf(self.path("b.odp"), "odp", odp_body(slide)))
        self.assertIn("## Слайд 1", result.text)
        self.assertIn("Только текст без заголовка", result.text)


# ------------------------------------------------------------------- FB2 ---

class OdgTests(TempCase):
    """Чертежи LibreOffice Draw.

    Схемы трактов, сетей и стоек рисуют именно так. Ценность файла — не в
    геометрии, а в подписях внутри фигур: названия узлов, номера портов,
    адреса. Раньше .odg не читался вовсе — файл отвергался как формат, о
    котором система не знает.
    """

    def make_drawing(self, name: str, inner: str, meta: str = "") -> Path:
        return make_odf(self.path(name), "odg", odg_body(inner), meta=meta)

    def test_shape_labels_become_text(self):
        inner = (
            '<draw:page draw:name="Схема тракта Е1">'
            '<draw:custom-shape><text:p>Мультиплексор ХХ-100</text:p></draw:custom-shape>'
            '<draw:frame><draw:text-box>'
            '<text:p>Порт 1 — 2048 кбит/с</text:p><text:p>Порт 2 — резерв</text:p>'
            '</draw:text-box></draw:frame>'
            '<draw:connector><text:p>G.703</text:p></draw:connector>'
            '</draw:page>'
        )
        result = opendoc.convert_odg(self.make_drawing("схема.odg", inner))
        self.assertEqual("odg", result.meta["source_format"])
        self.assertIn("## Лист 1. Схема тракта Е1", result.text)
        self.assertIn("Мультиплексор ХХ-100", result.text)
        self.assertIn("Порт 1 — 2048 кбит/с", result.text)
        self.assertIn("Порт 2 — резерв", result.text)
        self.assertIn("G.703", result.text)
        self.assertFalse(result.warnings, result.warnings)

    def test_grouped_shapes_are_walked(self):
        # В схемах узел почти всегда группа: рамка, подпись и порты вместе.
        inner = (
            '<draw:page draw:name="page1">'
            '<draw:g><draw:rect><text:p>Кросс 19"</text:p></draw:rect>'
            '<draw:ellipse><text:p>ОРС-3</text:p></draw:ellipse></draw:g>'
            '</draw:page>'
        )
        result = opendoc.convert_odg(self.make_drawing("группа.odg", inner))
        self.assertIn('Кросс 19"', result.text)
        self.assertIn("ОРС-3", result.text)

    def test_repeated_labels_are_not_duplicated(self):
        inner = (
            '<draw:page draw:name="page1">'
            '<draw:custom-shape><text:p>Порт 1</text:p></draw:custom-shape>'
            '<draw:custom-shape><text:p>Порт 1</text:p></draw:custom-shape>'
            '</draw:page>'
        )
        result = opendoc.convert_odg(self.make_drawing("повтор.odg", inner))
        self.assertEqual(1, result.text.count("Порт 1"))

    def test_sheets_are_counted_and_marked(self):
        inner = (
            '<draw:page draw:name="Тракт"><draw:custom-shape><text:p>Узел А</text:p>'
            '</draw:custom-shape></draw:page>'
            '<draw:page draw:name="page2"><draw:custom-shape><text:p>Стойка 4</text:p>'
            '</draw:custom-shape></draw:page>'
        )
        result = opendoc.convert_odg(self.make_drawing("два.odg", inner))
        self.assertEqual(2, result.meta["sheets"])
        self.assertEqual(2, result.page_count)
        # Маркеры страниц нужны, чтобы ссылка вела на конкретный лист.
        self.assertIn("<!-- page: 1 -->", result.text)
        self.assertIn("<!-- page: 2 -->", result.text)
        # Безымянный лист не должен получать заголовок «page2».
        self.assertIn("## Лист 2", result.text)
        self.assertNotIn("page2", result.text)

    def test_title_comes_from_metadata(self):
        inner = ('<draw:page draw:name="page1"><draw:custom-shape><text:p>Узел</text:p>'
                 '</draw:custom-shape></draw:page>')
        result = opendoc.convert_odg(self.make_drawing(
            "б.odg", inner, meta=meta_xml(title="Схема тракта Е1 Кемерово — Юрга")))
        self.assertEqual("Схема тракта Е1 Кемерово — Юрга", result.title)

    def test_empty_drawing_explains_itself(self):
        inner = '<draw:page draw:name="page1"><draw:line/></draw:page>'
        result = opendoc.convert_odg(self.make_drawing("пусто.odg", inner))
        # Заголовок «## Лист 1» текстом документа не считается: иначе пустой
        # чертёж выглядел бы принятым, а искать в нём нечего.
        self.assertEqual(0, result.meta["labels"])
        self.assertTrue(any("подписи" in warning for warning in result.warnings),
                        result.warnings)

    def test_registered_suffixes(self):
        from reportgen.ingest.convert import supported_suffixes

        supported = supported_suffixes()
        for suffix in (".odg", ".otg", ".fodg"):
            self.assertIn(suffix, supported)


class MhtmlTests(TempCase):
    """Страницы, сохранённые браузером «полностью» (.mht/.mhtml).

    По формату это MIME-архив — тот же контейнер, что у письма. Через
    разборщик писем такой файл давал «письмо без темы» с полусотней
    «вложений»: вложениями считались картинки вёрстки.
    """

    PAGE = (
        b"From: <Saved by Blink>\r\n"
        b"Snapshot-Content-Location: https://example.org/docs/g703\r\n"
        b"Subject: =?utf-8?B?0KDQtdC60L7QvNC10L3QtNCw0YbQuNGPIEcuNzAz?=\r\n"
        b"Date: Mon, 12 Aug 2024 09:14:00 +0300\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: multipart/related; type="text/html"; boundary="--BOUND--"\r\n'
        b"\r\n"
        b"----BOUND--\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n"
        b"Content-Location: https://example.org/docs/g703\r\n"
        b"\r\n"
        b"<html><body><h1>=D0=A1=D1=82=D1=8B=D0=BA G.703</h1>"
        b"<p>2048 =D0=BA=D0=B1=D0=B8=D1=82/=D1=81, HDB3.</p></body></html>\r\n"
        b"----BOUND--\r\n"
        b"Content-Type: image/png\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"Content-Location: https://example.org/docs/logo.png\r\n"
        b"\r\n"
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==\r\n"
        b"----BOUND----\r\n"
    )

    def test_saved_page_is_read_as_a_page(self):
        path = self.path("g703.mht")
        path.write_bytes(self.PAGE)
        result = web.convert_mhtml(path)
        self.assertEqual("Рекомендация G.703", result.title)
        self.assertEqual("mhtml", result.meta["source_format"])
        self.assertEqual("https://example.org/docs/g703", result.meta["source_url"])
        self.assertIn("Стык G.703", result.text)
        self.assertIn("2048 кбит/с, HDB3.", result.text)
        # Картинка вёрстки — не приложенный документ.
        self.assertNotIn("## Вложения", result.text)
        self.assertFalse(result.warnings, result.warnings)

    def test_page_without_markup_says_so(self):
        path = self.path("пусто.mht")
        path.write_bytes(b"From: <x>\r\nMIME-Version: 1.0\r\n"
                         b"Content-Type: text/plain\r\n\r\n"
                         + "ни разметки, ни страницы\r\n".encode("utf-8"))
        result = web.convert_mhtml(path)
        self.assertTrue(result.warnings)

    def test_registered_suffixes(self):
        from reportgen.ingest.convert import supported_suffixes

        supported = supported_suffixes()
        self.assertIn(".mht", supported)
        self.assertIn(".mhtml", supported)


class Fb2Tests(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.path("book.fb2")
        self.book.write_text(FB2, encoding="utf-8")

    def test_title_and_author_from_description(self):
        result = opendoc.convert_fb2(self.book)
        self.assertEqual(result.title, "Основы спутниковой связи")
        self.assertEqual(result.meta["author"], "Иван Петров")
        self.assertEqual(result.meta["language"], "ru")
        self.assertEqual(result.meta["source_format"], "fb2")

    def test_sections_give_nested_headings(self):
        result = opendoc.convert_fb2(self.book)
        self.assertIn("# Основы спутниковой связи", result.text)
        self.assertIn("## Глава 1. Диапазоны", result.text)
        self.assertIn("### 1.1. Ku и Ka", result.text)

    def test_epigraph_notes_and_annotation(self):
        result = opendoc.convert_fb2(self.book)
        self.assertIn("> Связь начинается с бюджета линии.", result.text)
        self.assertIn("# Аннотация", result.text)
        self.assertIn("Пособие по расчёту бюджета", result.text)
        self.assertIn("# Примечания", result.text)
        # Примечания — раздел верхнего уровня, его секции лежат внутри.
        self.assertIn("# Примечания\n\n## 1\n", result.text)
        self.assertIn("Расчёт по рекомендации ITU-R P.618.", result.text)
        self.assertIn("примечании[1]", result.text)

    def test_binary_payload_never_leaks_into_text(self):
        result = opendoc.convert_fb2(self.book)
        # base64 обложки — это картинка, а не текст: в индексе ей делать нечего.
        self.assertNotIn("QUJDREVGRw", result.text)

    def test_broken_xml_gives_warning(self):
        path = self.path("cut.fb2")
        path.write_text("<FictionBook><body><p>обрыв", encoding="utf-8")
        result = opendoc.convert_fb2(path)
        self.assertTrue(result.is_empty)
        self.assertIn("FB2 не разобран", result.warnings[0])

    def test_wrong_root_element(self):
        path = self.path("other.fb2")
        path.write_text('<?xml version="1.0"?><rss><channel/></rss>', encoding="utf-8")
        result = opendoc.convert_fb2(path)
        self.assertTrue(result.is_empty)
        self.assertIn("не FB2", result.warnings[0])

    def test_cp1251_book_with_wrong_declaration(self):
        path = self.path("cp1251.fb2")
        text = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
            "<description><title-info><book-title>Радиорелейные линии</book-title>"
            "</title-info></description>"
            "<body><section><p>Пролёт через реку.</p></section></body></FictionBook>"
        )
        path.write_bytes(text.encode("cp1251"))
        result = opendoc.convert_fb2(path)
        self.assertEqual(result.title, "Радиорелейные линии")
        self.assertIn("Пролёт через реку.", result.text)


# ------------------------------------------------------------------ EPUB ---

class EpubTests(TempCase):
    def test_chapters_follow_spine_order(self):
        chapters = [
            ("first", "<html><head><title>Глава первая</title></head>"
                      "<body><p>Расчёт пролёта.</p></body></html>"),
            ("second", "<html><head><title>Глава вторая</title></head>"
                       "<body><p>Зоны Френеля.</p></body></html>"),
        ]
        # Порядок spine намеренно обратный порядку манифеста.
        path = make_epub(self.path("a.epub"), chapters, ["second", "first"])
        result = opendoc.convert_epub(path)
        self.assertLess(result.text.index("Зоны Френеля"), result.text.index("Расчёт пролёта"))
        self.assertIn("# Глава вторая", result.text)
        self.assertIn("# Глава первая", result.text)
        self.assertEqual(result.title, "Книга")
        self.assertEqual(result.meta["author"], "Автор")
        self.assertEqual(result.meta["chapters"], 2)

    def test_chapter_markup_is_cleaned_like_html(self):
        chapters = [(
            "one",
            "<html><head><title>Таблицы</title><style>p{}</style></head><body>"
            "<h1>Параметры модема</h1>"
            "<table><tr><th>Режим</th><th>Скорость</th></tr>"
            "<tr><td>QPSK 3/4</td><td>4,2 Мбит/с</td></tr></table>"
            "<script>alert(1)</script></body></html>",
        )]
        result = opendoc.convert_epub(make_epub(self.path("b.epub"), chapters, ["one"]))
        self.assertIn("# Параметры модема", result.text)
        self.assertIn("| QPSK 3/4 | 4,2 Мбит/с |", result.text)
        self.assertNotIn("alert", result.text)

    def test_percent_encoded_and_missing_hrefs(self):
        chapters = [("глава 1", "<html><body><p>Кириллица в имени файла.</p></body></html>")]
        path = self.path("c.epub")
        make_epub(path, chapters, ["глава 1"])
        result = opendoc.convert_epub(path)
        self.assertIn("Кириллица в имени файла.", result.text)

    def test_drm_protected_book(self):
        chapters = [("one", "<html><body><p>Текст.</p></body></html>")]
        path = make_epub(self.path("d.epub"), chapters, ["one"],
                         extra={"META-INF/encryption.xml": "<encryption/>"})
        result = opendoc.convert_epub(path)
        self.assertTrue(result.is_empty)
        self.assertIn("DRM", result.warnings[0])

    def test_broken_epub_archive(self):
        path = self.path("e.epub")
        path.write_bytes(b"PK\x03\x04not really a zip")
        result = opendoc.convert_epub(path)
        self.assertTrue(result.is_empty)
        self.assertIn("повреждён", result.warnings[0])


# ------------------------------------------------------------------ HTML ---

class HtmlTests(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.page = self.path("page.html")
        self.page.write_text(HTML_PAGE, encoding="utf-8")

    def test_script_style_nav_and_footer_are_dropped(self):
        result = web.convert_html(self.page)
        self.assertNotIn("secret", result.text)
        self.assertNotIn("color: red", result.text)
        self.assertNotIn("Обсуждение", result.text)
        self.assertNotIn("Отдел измерений", result.text)

    def test_headings_lists_and_title(self):
        result = web.convert_html(self.page)
        self.assertEqual(result.title, "Протокол измерений")
        self.assertIn("# Протокол измерений", result.text)
        self.assertIn("## Условия", result.text)
        self.assertIn("- Температура 20 °C", result.text)
        self.assertIn("- Влажность 60 %", result.text)

    def test_table_is_converted(self):
        result = web.convert_html(self.page)
        self.assertIn("| Параметр | Значение |", result.text)
        self.assertIn("| --- | --- |", result.text)
        self.assertIn("| EVM | 3,1 % |", result.text)
        self.assertIn("| Уровень | −72 дБм |", result.text)

    def test_entities_are_decoded(self):
        result = web.convert_html(self.page)
        self.assertIn("«30 кГц»", result.text)
        self.assertNotIn("&laquo;", result.text)
        self.assertNotIn("&nbsp;", result.text)
        # Неразрывный пробел заменён обычным — иначе поиск по фразе не срабатывает.
        self.assertIn("выполнены в полосе", result.text)

    def test_links_keep_address(self):
        result = web.convert_html(self.page)
        self.assertIn("руководстве (https://example.org/method)", result.text)

    def test_encoding_from_meta_charset(self):
        path = self.path("cp1251.html")
        markup = (
            '<html><head><meta http-equiv="Content-Type" '
            'content="text/html; charset=windows-1251"><title>Отчёт</title></head>'
            "<body><h1>Приёмо-сдаточные измерения</h1>"
            "<p>Отклонений не выявлено.</p></body></html>"
        )
        path.write_bytes(markup.encode("cp1251"))
        result = web.convert_html(path)
        self.assertEqual(result.meta["encoding"], "windows-1251")
        self.assertIn("# Приёмо-сдаточные измерения", result.text)
        self.assertEqual(result.title, "Отчёт")

    def test_empty_file_gives_warning(self):
        path = self.path("empty.html")
        path.write_bytes(b"   \n")
        result = web.convert_html(path)
        self.assertTrue(result.is_empty)
        self.assertIn("пуст", result.warnings[0])

    def test_page_without_text_is_reported(self):
        path = self.path("spa.html")
        path.write_text("<html><body><div id='root'></div>"
                        "<script>render()</script></body></html>", encoding="utf-8")
        result = web.convert_html(path)
        self.assertTrue(result.is_empty)
        self.assertTrue(any("не найдено текста" in warning for warning in result.warnings))

    def test_broken_markup_does_not_raise(self):
        path = self.path("broken.html")
        path.write_text("<html><body><p>Начало <b>жирный <table><tr><td>ячейка"
                        "<p>без закрытия", encoding="utf-8")
        result = web.convert_html(path)
        self.assertIn("Начало", result.text)


# ------------------------------------------------------------------- EML ---

class EmlTests(TempCase):
    def make_mail(self, name: str, message: EmailMessage) -> Path:
        path = self.path(name)
        path.write_bytes(message.as_bytes())
        return path

    def test_headers_body_and_attachment(self):
        message = EmailMessage()
        message["Subject"] = "Замирания на пролёте Р-12"
        message["From"] = "Иванов Иван <ivanov@example.org>"
        message["To"] = "support@example.com"
        message["Cc"] = "chief@example.com"
        message["Date"] = "Mon, 03 Mar 2025 10:00:00 +0300"
        message.set_content("Добрый день!\n\nПрилагаю дамп с анализатора.")
        message.add_attachment(b"\x00" * 2048, maintype="application",
                               subtype="octet-stream", filename="dump.pcap")
        result = web.convert_eml(self.make_mail("a.eml", message))

        self.assertEqual(result.title, "Замирания на пролёте Р-12")
        self.assertIn("# Замирания на пролёте Р-12", result.text)
        self.assertIn("**От:** Иванов Иван <ivanov@example.org>", result.text)
        self.assertIn("**Кому:** support@example.com", result.text)
        self.assertIn("**Копия:** chief@example.com", result.text)
        self.assertIn("**Дата:** Mon, 03 Mar 2025 10:00:00 +0300", result.text)
        self.assertIn("Прилагаю дамп с анализатора.", result.text)
        self.assertIn("## Вложения", result.text)
        self.assertIn("- dump.pcap — 2,0 КБ, application/octet-stream", result.text)
        self.assertEqual(result.meta["attachments"], 1)
        self.assertTrue(any("вложениях" in warning for warning in result.warnings))

    def test_html_only_body_is_parsed_as_html(self):
        message = EmailMessage()
        message["Subject"] = "Ответ вендора"
        message["From"] = "vendor@example.net"
        message.set_content(
            "<html><body><h1>Маска передатчика</h1>"
            "<p>Соответствует требованиям.</p>"
            "<style>b{}</style></body></html>",
            subtype="html",
        )
        result = web.convert_eml(self.make_mail("b.eml", message))
        self.assertEqual(result.meta["body_format"], "html")
        self.assertIn("# Маска передатчика", result.text)
        self.assertIn("Соответствует требованиям.", result.text)
        self.assertNotIn("b{}", result.text)

    def test_encoded_subject_is_decoded(self):
        raw = (
            b"Subject: =?utf-8?B?0J/RgNC+0YLQvtC60L7Quw==?=\r\n"
            b"From: test@example.org\r\n"
            b"Content-Type: text/plain; charset=windows-1251\r\n"
            b"Content-Transfer-Encoding: 8bit\r\n"
            b"\r\n"
        ) + "Текст письма в кодировке cp1251.".encode("cp1251")
        path = self.path("c.eml")
        path.write_bytes(raw)
        result = web.convert_eml(path)
        self.assertEqual(result.title, "Протокол")
        self.assertIn("Текст письма в кодировке cp1251.", result.text)

    def test_empty_file_gives_warning(self):
        path = self.path("d.eml")
        path.write_bytes(b"")
        result = web.convert_eml(path)
        self.assertTrue(result.is_empty)
        self.assertIn("пуст", result.warnings[0])


# ------------------------------------------------------------------- XML ---

class XmlTests(TempCase):
    def test_generic_tree_becomes_headings_and_values(self):
        path = self.path("a.xml")
        path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<measurements device="анализатор спектра">'
            '<channel number="1">'
            "<frequency>11,7 ГГц</frequency><level>−72 дБм</level>"
            "</channel></measurements>",
            encoding="utf-8",
        )
        result = web.convert_xml(path)
        self.assertIn("# measurements (device=анализатор спектра)", result.text)
        self.assertIn("## channel (number=1)", result.text)
        self.assertIn("- **frequency:** 11,7 ГГц", result.text)
        self.assertIn("- **level:** −72 дБм", result.text)

    def test_fictionbook_is_handed_to_fb2_converter(self):
        path = self.path("book.xml")
        path.write_text(FB2, encoding="utf-8")
        result = web.convert_xml(path)
        self.assertEqual(result.meta["source_format"], "fb2")
        self.assertEqual(result.title, "Основы спутниковой связи")

    def test_xhtml_is_handed_to_html_converter(self):
        path = self.path("page.xml")
        path.write_text(
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Страница</title></head>'
            "<body><h1>Заголовок</h1><p>Текст.</p></body></html>",
            encoding="utf-8",
        )
        result = web.convert_xml(path)
        self.assertEqual(result.meta["source_format"], "html")
        self.assertIn("# Заголовок", result.text)

    def test_broken_xml_gives_warning(self):
        path = self.path("broken.xml")
        path.write_text("<root><item>обрыв", encoding="utf-8")
        result = web.convert_xml(path)
        self.assertTrue(result.is_empty)
        self.assertIn("не разобран", result.warnings[0])

    def test_empty_xml_gives_warning(self):
        path = self.path("empty.xml")
        path.write_bytes(b"")
        result = web.convert_xml(path)
        self.assertTrue(result.is_empty)
        self.assertIn("пуст", result.warnings[0])


# ------------------------------------------------------------- реестр ----

class RegistryTests(TempCase):
    def test_all_formats_are_registered_and_available(self):
        names = {spec.name for spec in registry.all_specs()}
        for name in ("opendocument-text", "opendocument-spreadsheet",
                     "opendocument-presentation", "fb2", "epub", "html", "eml", "xml"):
            self.assertIn(name, names)
        for name in ("opendocument-text", "fb2", "epub", "html", "eml", "xml"):
            spec = next(item for item in registry.all_specs() if item.name == name)
            # Всё на стандартной библиотеке: недостающих требований быть не может.
            self.assertEqual(spec.missing(), [], f"{name}: {registry.missing_hint(spec)}")

    def test_suffixes_are_supported(self):
        suffixes = convert_module.supported_suffixes()
        for suffix in (".odt", ".ott", ".fodt", ".ods", ".odp", ".fb2", ".epub",
                       ".html", ".htm", ".xhtml", ".eml", ".xml"):
            self.assertIn(suffix, suffixes)
            self.assertIsNotNone(registry.find(suffix))

    def test_convert_file_dispatches_to_our_converter(self):
        body = odt_body('<text:h text:outline-level="1">Через диспетчер</text:h>'
                        "<text:p>Текст документа.</text:p>")
        path = make_odf(self.path("dispatch.odt"), "odt", body)
        result = convert_module.convert_file(path)
        self.assertIn("# Через диспетчер", result.text)

    def test_converter_never_raises_on_garbage(self):
        """Приём каталога не должен падать ни на одном файле."""
        garbage = b"\x00\x01\x02 not a document \xff\xfe"
        # У структурных форматов мусор обязан дать внятное предупреждение;
        # HTML структуры не требует, и «просто текст» для него — законный ввод.
        strict = {".odt", ".ods", ".odp", ".fb2", ".epub", ".xml"}
        for suffix, convert in (
            (".odt", opendoc.convert_odt),
            (".ods", opendoc.convert_ods),
            (".odp", opendoc.convert_odp),
            (".fb2", opendoc.convert_fb2),
            (".epub", opendoc.convert_epub),
            (".html", web.convert_html),
            (".eml", web.convert_eml),
            (".xml", web.convert_xml),
        ):
            path = self.path(f"garbage{suffix}")
            path.write_bytes(garbage)
            result = convert(path)
            self.assertEqual(result.meta.get("source_format"), suffix.lstrip("."))
            if suffix in strict:
                self.assertTrue(result.warnings, f"{suffix}: нет предупреждения")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
