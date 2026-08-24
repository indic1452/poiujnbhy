"""OpenDocument, электронные книги и FB2 — на одной стандартной библиотеке.

Все форматы этого модуля разбираются через :mod:`zipfile` и
:mod:`xml.etree.ElementTree`, без ``odfpy``, ``ebooklib`` и прочего. Причина
простая: установка в изолированном контуре идёт с зеркала или с флешки, и
каждый лишний пакет — это отдельная строка в заявке. ODT, ODS, ODP, EPUB — это
ZIP с XML внутри, FB2 — просто XML; ничего, кроме разбора XML, для них не нужно.

Что важно знать про результат:

* **Заголовки.** В ODT берутся из ``text:h`` с уровнем ``text:outline-level``,
  в FB2 — из ``title`` с учётом вложенности секций, в EPUB — из самих глав.
  Без решёток не работает нарезка на чанки по структуре (:mod:`reportgen.corpus`).
* **Таблицы.** Собираются целиком в markdown-таблицу. Повторы ячеек
  (``table:number-columns-repeated``) разворачиваются, но с потолком: лист с
  ``number-columns-repeated="16384"`` в хвосте — норма для LibreOffice, и
  разворачивать его буквально означает съесть память на пустом месте.
* **Номера страниц.** Ставятся там, где номер честный: лист книги ODS и слайд
  ODP — это страница при печати, а в ODT берутся настоящие разрывы
  (``text:soft-page-break``, которые пишет редактор, и принудительные разрывы
  из стилей). У EPUB и FB2 страниц нет вообще: у них разметка — главы, и
  выдумывать номер страницы для книги значит поставить в отчёт ссылку «с. 3»,
  которой в книге не существует. Место в них находится по «хлебным крошкам».
* **Битые файлы не роняют приём.** Не-ZIP, архив без ``content.xml``,
  документ под паролем, обрыв XML — это предупреждение по-русски и пустой
  текст, а не исключение.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple
from urllib.parse import unquote

from .. import registry
from ..convert import ConvertedDocument, _clean_line, _reason, page_marker
from .web import (
    decode_markup,
    first_heading,
    flatten_table,
    html_to_markdown,
    merge_list_blocks,
    rows_to_markdown,
    useful_href,
)

__all__ = [
    "convert_epub",
    "convert_fb2",
    "convert_odp",
    "convert_ods",
    "convert_odt",
]

# ------------------------------------------------------ пространства имён ---

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xlink": "http://www.w3.org/1999/xlink",
    "opf": "http://www.idpf.org/2007/opf",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
}

#: Предел на разворачивание повторяющейся ячейки. Больше двухсот одинаковых
#: значений подряд в осмысленной таблице не встречается, а вот
#: ``number-columns-repeated="1048576"`` встречается в каждом втором ODS.
MAX_CELL_REPEAT = 256
#: Предел ширины таблицы в колонках.
MAX_COLUMNS = 1024
#: Предел на разворачивание повторяющейся строки.
MAX_ROW_REPEAT = 32
#: Предел высоты таблицы в строках.
MAX_ROWS = 4096
#: Предел на ``text:s`` (подряд идущие пробелы).
MAX_SPACES = 40
#: Предел на распакованный размер одной записи архива. Защита от «зип-бомбы»:
#: content.xml на сто мегабайт в честном документе не встречается.
MAX_MEMBER_BYTES = 256 * 1024 * 1024

#: Инлайновые элементы ODF, содержимое которых в текст не идёт.
_ODF_SKIP_INLINE = frozenset({
    "annotation", "annotation-end", "tracked-changes", "note-citation",
    "bookmark", "bookmark-start", "bookmark-end", "reference-mark",
    "reference-mark-start", "reference-mark-end", "sequence-decls",
    "sequence-decl", "change", "change-start", "change-end", "hidden-text",
})

#: Блочные элементы ODF, в которые не спускаемся: правки в режиме рецензирования
#: и служебные объявления — это не текст документа.
_ODF_SKIP_BLOCK = frozenset({
    "tracked-changes", "sequence-decls", "user-field-decls", "variable-decls",
    "forms", "annotation", "note-citation", "shapes",
})

#: Названия дополнительных тел FB2.
_FB2_BODY_TITLES = {
    "notes": "Примечания",
    "comments": "Комментарии",
    "footnotes": "Сноски",
}


def _q(prefix: str, name: str) -> str:
    return "{%s}%s" % (_NS[prefix], name)


def _tag(element: Any) -> str:
    """Локальное имя тега без пространства имён (или пусто для комментариев)."""
    tag = getattr(element, "tag", None)
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(element: Any, name: str, *prefixes: str) -> str | None:
    """Атрибут по локальному имени: сначала в известных пространствах, потом без."""
    for prefix in prefixes:
        value = element.get(_q(prefix, name))
        if value is not None:
            return value
    return element.get(name)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _first(element: Any, name: str) -> Any | None:
    """Первый элемент поддерева с таким локальным именем."""
    for node in element.iter():
        if _tag(node) == name:
            return node
    return None


def _children(element: Any, name: str) -> List[Any]:
    return [child for child in element if _tag(child) == name]


def _clean_block(text: str) -> str:
    lines = [_clean_line(line) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------- накопитель ---

@dataclass
class _Blocks:
    """Куски Markdown в порядке появления плюс всё, что надо запомнить попутно."""

    blocks: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    images: int = 0
    page: int = 1
    paginated: bool = False
    clamped: int = 0
    _pending_page: int = 0

    def note(self, text: str) -> None:
        """Замечание о разборе. Повторы не копим: одного раза достаточно."""
        if text and text not in self.notes:
            self.notes.append(text)

    def add(self, text: str) -> None:
        if not text:
            return
        if self._pending_page:
            self.blocks.append(page_marker(self._pending_page))
            self._pending_page = 0
        self.blocks.append(text)

    def page_break(self) -> None:
        """Разрыв страницы внутри текста: маркер встанет перед следующим блоком."""
        self.page += 1
        self._pending_page = self.page
        self.paginated = True

    def mark_page(self, number: int) -> None:
        """Явная граница страницы (лист книги, слайд): маркер ставится сразу."""
        self.page = number
        self._pending_page = 0
        self.paginated = True
        self.blocks.append(page_marker(number))

    def image(self) -> str:
        self.images += 1
        return f"![рисунок {self.images}]()"

    def markdown(self, *, lead_marker: bool = False) -> str:
        blocks = list(self.blocks)
        if lead_marker and self.paginated and blocks and not blocks[0].startswith("<!-- page:"):
            blocks.insert(0, page_marker(1))
        return merge_list_blocks(blocks)


# ------------------------------------------------- ODF: текст и таблицы ----

def _odf_inline(element: Any, state: _Blocks) -> str:
    """Текст абзаца ODF со всеми вложенными span, ссылками и сносками."""
    parts: List[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        name = _tag(child)
        if name in _ODF_SKIP_INLINE:
            pass
        elif name == "s":
            parts.append(" " * min(max(_int(_attr(child, "c", "text"), 1), 1), MAX_SPACES))
        elif name == "tab":
            parts.append("\t")
        elif name == "line-break":
            parts.append("\n")
        elif name == "soft-page-break":
            state.page_break()
        elif name == "note":
            parts.append(_odf_note(child, state))
        elif name == "a":
            inner = _odf_inline(child, state)
            href = (_attr(child, "href", "xlink") or "").strip()
            parts.append(inner)
            if href and useful_href(href) and href not in inner:
                parts.append(f" ({href})")
        elif name == "image":
            parts.append(f" {state.image()} ")
        elif name in ("frame", "custom-shape", "g", "text-box"):
            parts.append(_odf_frame_inline(child, state))
        else:
            parts.append(_odf_inline(child, state))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _odf_note(note: Any, state: _Blocks) -> str:
    """Сноска ODF: её текст ставится прямо в строку, где стоял значок."""
    body = _first(note, "note-body")
    if body is None:
        return ""
    inner = " ".join(text for text in _odf_texts(body, state) if text)
    inner = _one_line(inner)
    return f" [сноска: {inner}]" if inner else ""


def _odf_frame_inline(frame: Any, state: _Blocks) -> str:
    """Рамка (картинка, надпись) внутри абзаца."""
    parts: List[str] = []
    for child in frame:
        name = _tag(child)
        if name == "image":
            parts.append(f" {state.image()} ")
        elif name in ("text-box", "frame", "g", "custom-shape"):
            inner = _one_line(" ".join(_odf_texts(child, state)))
            if inner:
                parts.append(f" {inner} ")
    return "".join(parts)


def _odf_texts(container: Any, state: _Blocks) -> List[str]:
    """Тексты всех абзацев и пунктов списка внутри контейнера, по порядку."""
    out: List[str] = []
    for child in container:
        name = _tag(child)
        if name in _ODF_SKIP_BLOCK:
            continue
        if name in ("p", "h"):
            text = _clean_block(_odf_inline(child, state))
            if text:
                out.append(text)
        elif name == "list":
            out.extend(_odf_list_texts(child, state))
        elif name == "table":
            markdown = odf_table_markdown(child, state)
            if markdown:
                out.append(flatten_table(markdown))
        elif len(child):
            out.extend(_odf_texts(child, state))
    return out


def _odf_list_texts(node: Any, state: _Blocks, depth: int = 0) -> List[str]:
    """Список ODF → строки вида «- пункт» с отступом по вложенности."""
    out: List[str] = []
    indent = "  " * depth
    for item in node:
        if _tag(item) not in ("list-item", "list-header"):
            continue
        first = True
        for child in item:
            name = _tag(child)
            if name in ("p", "h"):
                text = _one_line(_odf_inline(child, state))
                if not text:
                    continue
                out.append(f"{indent}- {text}" if first else f"{indent}  {text}")
                first = False
            elif name == "list":
                out.extend(_odf_list_texts(child, state, depth + 1))
    return out


def _iter_table_rows(table: Any) -> Iterator[Any]:
    """Строки таблицы, включая заголовочные группы и группы строк."""
    for child in table:
        name = _tag(child)
        if name == "table-row":
            yield child
        elif name in ("table-header-rows", "table-row-group", "table-rows"):
            yield from _iter_table_rows(child)


def _odf_cell_text(cell: Any, state: _Blocks) -> str:
    """Содержимое ячейки: абзацы, списки и даже вложенная таблица."""
    pieces = _odf_texts(cell, state)
    if not pieces:
        # Числовая ячейка без текстового представления — берём само значение.
        value = _attr(cell, "value", "office") or _attr(cell, "date-value", "office")
        if value:
            pieces = [_clean_line(str(value))]
    return "\n".join(pieces)


def _odf_row_cells(row: Any, state: _Blocks) -> List[str]:
    """Ячейки строки с развёрнутыми повторами.

    Хвост из пустых ячеек отбрасывается до разворачивания: LibreOffice пишет в
    конце каждой строки ``table:number-columns-repeated="16384"``, и буквальное
    разворачивание такого хвоста — это лист на миллион пустых колонок.
    """
    raw: List[Tuple[str, int]] = []
    for child in row:
        name = _tag(child)
        if name not in ("table-cell", "covered-table-cell"):
            continue
        repeat = _int(_attr(child, "number-columns-repeated", "table"), 1)
        text = "" if name == "covered-table-cell" else _odf_cell_text(child, state)
        raw.append((text, repeat))
    while raw and not raw[-1][0].strip():
        raw.pop()

    cells: List[str] = []
    for text, repeat in raw:
        count = max(1, repeat)
        if count > MAX_CELL_REPEAT:
            count = MAX_CELL_REPEAT
            state.clamped += 1
        for _ in range(count):
            if len(cells) >= MAX_COLUMNS:
                state.clamped += 1
                return cells
            cells.append(text)
    return cells


def odf_table_markdown(table: Any, state: _Blocks) -> str:
    """Таблица ODF (в тексте или на листе книги) → markdown-таблица целиком.

    Целиком — пока таблица правдоподобна. Всё, что пришлось отбросить,
    попадает в предупреждения поимённо: молча потерять половину таблицы
    допусков нельзя, инженер должен знать, что смотреть в исходнике.
    """
    label = _clean_line(_attr(table, "name", "table") or "") or "без названия"
    clamped_before = state.clamped
    rows: List[List[str]] = []
    source = _iter_table_rows(table)
    for row in source:
        cells = _odf_row_cells(row, state)
        if not any(cell.strip() for cell in cells):
            continue
        repeat = max(1, _int(_attr(row, "number-rows-repeated", "table"), 1))
        if repeat > MAX_ROW_REPEAT:
            repeat = MAX_ROW_REPEAT
            state.clamped += 1
        rows.extend([cells] * repeat)
        if len(rows) >= MAX_ROWS:
            skipped = sum(1 for _ in source)
            state.note(
                f"таблица «{label}»: в текст взяты первые {MAX_ROWS} строк, "
                f"ещё {skipped} пропущено — такой объём в библиотеку не помещают, "
                "выгрузку надо подавать сводкой"
            )
            break
    if state.clamped > clamped_before:
        state.note(
            f"таблица «{label}»: усечены повторяющиеся ячейки или строки "
            f"(предел — {MAX_CELL_REPEAT} повторов и {MAX_COLUMNS} колонок); "
            "обычно так размечено пустое оформление листа, данных там нет"
        )
    return rows_to_markdown(rows)


# ----------------------------------------------------------- ODF: чтение ---

def _page_break_styles(root: Any) -> Tuple[frozenset, frozenset]:
    """Имена стилей абзаца с принудительным разрывом страницы."""
    before: set = set()
    after: set = set()
    for container in root:
        if _tag(container) not in ("automatic-styles", "styles"):
            continue
        for style in container:
            if _tag(style) != "style":
                continue
            name = _attr(style, "name", "style")
            if not name:
                continue
            for properties in style:
                if _tag(properties) != "paragraph-properties":
                    continue
                if (_attr(properties, "break-before", "fo") or "") == "page":
                    before.add(name)
                if (_attr(properties, "break-after", "fo") or "") == "page":
                    after.add(name)
    return frozenset(before), frozenset(after)


def _odf_metadata(node: Any) -> Tuple[str, str, Dict[str, Any]]:
    """Название, автор и статистика из ``meta.xml`` (или из плоского файла)."""
    title = ""
    author = ""
    stats: Dict[str, Any] = {}
    if node is None:
        return title, author, stats
    meta = _first(node, "meta")
    if meta is None:
        return title, author, stats
    for child in meta:
        name = _tag(child)
        value = _clean_line(child.text or "")
        if name == "title" and value:
            title = value
        elif name == "creator" and value:
            author = value
        elif name == "initial-creator" and value and not author:
            author = value
        elif name == "document-statistic":
            pages = _int(_attr(child, "page-count", "meta"), 0)
            if pages:
                stats["page_count"] = pages
    return title, author, stats


def _member_size(archive: zipfile.ZipFile, name: str) -> int:
    try:
        return int(archive.getinfo(name).file_size)
    except (KeyError, OSError, zipfile.BadZipFile):
        return 0


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    if _member_size(archive, name) > MAX_MEMBER_BYTES:
        return None
    try:
        return archive.read(name)
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError):
        return None


def _load_odf(
    path: Path, result: ConvertedDocument
) -> Tuple[Any | None, Any | None, Any | None]:
    """Читает документ ODF: корни content.xml, meta.xml и styles.xml."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    try:
        with path.open("rb") as handle:
            head = handle.read(4)
    except OSError as error:
        result.warnings.append(f"файл не прочитан: {_reason(error)}")
        return None, None, None
    if not head:
        result.warnings.append("файл пуст — документа в нём нет")
        return None, None, None

    if not head.startswith(b"PK"):
        # Плоский ODF (.fodt/.fods/.fodp): весь документ одним XML-файлом.
        try:
            root = ET.parse(str(path)).getroot()
        except ET.ParseError as error:
            result.warnings.append(
                f"файл не является документом OpenDocument: не ZIP и не XML ({_reason(error)})"
            )
            return None, None, None
        except (OSError, ValueError, MemoryError) as error:
            result.warnings.append(f"файл не прочитан: {_reason(error)}")
            return None, None, None
        # В плоском ODF содержимое, метаданные и стили лежат в одном дереве.
        return root, root, root

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        result.warnings.append(
            f"файл повреждён: архив ODF не открывается ({_reason(error)})"
        )
        return None, None, None

    with archive:
        names = set(archive.namelist())
        manifest = _read_zip_member(archive, "META-INF/manifest.xml") or b""
        if b"encryption-data" in manifest:
            result.warnings.append(
                "документ защищён паролем — текст не извлечён; снимите пароль "
                "(Файл → Сохранить как, снять «Сохранить с паролем») и повторите приём"
            )
            return None, None, None
        if "content.xml" not in names:
            result.warnings.append(
                "в архиве нет content.xml — это не документ OpenDocument "
                "(возможно, переименованный ZIP или другой формат)"
            )
            return None, None, None
        size = _member_size(archive, "content.xml")
        if size > MAX_MEMBER_BYTES:
            result.warnings.append(
                f"содержимое документа неправдоподобно велико ({size // (1 << 20)} МБ) — "
                "файл не разбирается; проверьте, не подменён ли он архивом"
            )
            return None, None, None
        content = _read_zip_member(archive, "content.xml")
        if content is None:
            result.warnings.append("content.xml не читается — архив повреждён")
            return None, None, None
        meta_raw = _read_zip_member(archive, "meta.xml")
        styles_raw = _read_zip_member(archive, "styles.xml")

    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        result.warnings.append(f"content.xml разобран не полностью: {_reason(error)}")
        return None, None, None
    except (ValueError, MemoryError, RecursionError) as error:
        result.warnings.append(f"content.xml не разобран: {_reason(error)}")
        return None, None, None

    def _optional(raw: bytes | None) -> Any | None:
        if not raw:
            return None
        try:
            return ET.fromstring(raw)
        except Exception:  # noqa: BLE001 — без meta.xml и styles.xml документ читается
            return None

    return root, _optional(meta_raw), _optional(styles_raw)


# ------------------------------------------------------------------- ODT ---

def _odt_walk(
    element: Any,
    state: _Blocks,
    breaks: Tuple[frozenset, frozenset],
    depth: int = 0,
) -> None:
    """Обходит текстовое тело ODT, сохраняя порядок абзацев, списков и таблиц."""
    if depth > 24:  # защита от документа с патологической вложенностью секций
        return
    before, after = breaks
    for child in element:
        name = _tag(child)
        if name in _ODF_SKIP_BLOCK:
            continue
        if name == "soft-page-break":
            state.page_break()
            continue
        if name in ("h", "p"):
            style = _attr(child, "style-name", "text") or ""
            if style in before:
                state.page_break()
            text = _clean_block(_odf_inline(child, state))
            if text:
                if name == "h":
                    level = min(max(_int(_attr(child, "outline-level", "text"), 1), 1), 6)
                    state.add(f"{'#' * level} {_one_line(text)}")
                else:
                    state.add(text)
            if style in after:
                state.page_break()
            continue
        if name == "list":
            for line in _odf_list_texts(child, state):
                state.add(line)
            continue
        if name == "table":
            markdown = odf_table_markdown(child, state)
            if markdown:
                state.add(markdown)
            continue
        if len(child):
            _odt_walk(child, state, breaks, depth + 1)


def _render_odt(root: Any, styles_root: Any, body: Any, state: _Blocks) -> None:
    """Разрывы страниц объявляются и в автоматических стилях content.xml, и в
    именованных стилях styles.xml — смотрим оба файла."""
    before, after = _page_break_styles(root)
    if styles_root is not None and styles_root is not root:
        extra_before, extra_after = _page_break_styles(styles_root)
        before, after = before | extra_before, after | extra_after
    _odt_walk(body, state, (before, after))


# ------------------------------------------------------------------- ODS ---

def _render_ods(body: Any, state: _Blocks) -> Tuple[int, List[str]]:
    """Листы книги: «## Лист «имя»» и таблица целиком. Возвращает (листов, пустые)."""
    number = 0
    empty: List[str] = []
    for index, sheet in enumerate(_children(body, "table"), start=1):
        name = _clean_line(_attr(sheet, "name", "table") or "") or f"Лист {index}"
        markdown = odf_table_markdown(sheet, state)
        if not markdown:
            empty.append(name)
            continue
        number += 1
        state.mark_page(number)
        state.add(f"## Лист «{name}»")
        state.add(markdown)
    return number, empty


# ------------------------------------------------------------------- ODP ---

def _odp_frame_class(frame: Any) -> str:
    return (_attr(frame, "class", "presentation") or "").strip().lower()


def _odp_frame_blocks(frame: Any, state: _Blocks) -> List[str]:
    """Содержимое рамки слайда: список, абзацы, таблица или картинка."""
    out: List[str] = []
    for child in frame:
        name = _tag(child)
        if name == "text-box":
            for item in child:
                item_name = _tag(item)
                if item_name == "list":
                    out.extend(_odf_list_texts(item, state))
                elif item_name in ("p", "h"):
                    text = _clean_block(_odf_inline(item, state))
                    if text:
                        out.append(text)
                elif item_name == "table":
                    markdown = odf_table_markdown(item, state)
                    if markdown:
                        out.append(markdown)
        elif name == "image":
            out.append(state.image())
        elif name == "table":
            markdown = odf_table_markdown(child, state)
            if markdown:
                out.append(markdown)
    return out


def _render_odp(body: Any, state: _Blocks) -> int:
    """Слайды разделами: «## Слайд N. Заголовок», текст, таблицы, заметки."""
    slides = _children(body, "page")
    for number, slide in enumerate(slides, start=1):
        state.mark_page(number)
        frames = [child for child in slide if _tag(child) in ("frame", "custom-shape", "g")]
        title = ""
        body_blocks: List[str] = []
        for frame in frames:
            blocks = _odp_frame_blocks(frame, state)
            if not blocks:
                continue
            if not title and _odp_frame_class(frame) in ("title", "subtitle"):
                title = _one_line(blocks[0])
                body_blocks.extend(blocks[1:])
                continue
            body_blocks.extend(blocks)
        if not title:
            name = _clean_line(_attr(slide, "name", "draw") or "")
            title = name if name and not name.lower().startswith("page") else ""
        state.add(f"## Слайд {number}" + (f". {title}" if title else ""))
        for block in body_blocks:
            state.add(block)
        notes = _first(slide, "notes")
        if notes is not None:
            note_text = _one_line(" ".join(_odf_texts(notes, state)))
            if note_text:
                state.add(f"Заметки к слайду: {note_text}")
    return len(slides)


# ------------------------------------------------- ODF: общий конвертер ----

_ODF_BODY_KINDS = {
    "text": "odt",
    "spreadsheet": "ods",
    "presentation": "odp",
    "drawing": "odp",
}

_ODF_KIND_NAMES = {
    "odt": "текстовый документ",
    "ods": "электронная таблица",
    "odp": "презентация",
}


def _convert_odf(path: Path, expected: str) -> ConvertedDocument:
    result = ConvertedDocument(title=path.stem, meta={"source_format": expected})
    root, meta_root, styles_root = _load_odf(path, result)
    if root is None:
        return result

    body = _first(root, "body")
    if body is None:
        result.warnings.append("в документе нет содержимого (office:body не найден)")
        return result
    content = None
    for child in body:
        if _tag(child) in _ODF_BODY_KINDS:
            content = child
            break
    if content is None:
        result.warnings.append("в документе нет ни текста, ни листов, ни слайдов")
        return result

    kind = _ODF_BODY_KINDS[_tag(content)]
    if kind != expected:
        result.warnings.append(
            f"содержимое файла — {_ODF_KIND_NAMES[kind]}, хотя расширение обещает "
            f"{_ODF_KIND_NAMES[expected]}; разобрано по содержимому"
        )
        result.meta["source_format"] = kind

    state = _Blocks()
    lead_marker = False
    try:
        if kind == "odt":
            _render_odt(root, styles_root, content, state)
            lead_marker = True
        elif kind == "ods":
            sheets, empty = _render_ods(content, state)
            result.page_count = sheets
            result.meta["sheets"] = sheets
            if empty:
                result.warnings.append(
                    "пустые листы пропущены: " + ", ".join(f"«{name}»" for name in empty[:10])
                )
        else:
            slides = _render_odp(content, state)
            result.page_count = slides
            result.meta["slides"] = slides
    except (ValueError, RecursionError, MemoryError) as error:
        result.warnings.append(f"документ разобран не полностью: {_reason(error)}")

    result.text = state.markdown(lead_marker=lead_marker)
    if kind == "odt" and state.paginated:
        result.page_count = state.page
    if state.images:
        result.meta["images"] = state.images
        result.warnings.append(
            f"изображений в документе: {state.images} — они не извлекаются, "
            "в тексте оставлены плейсхолдеры"
        )
    result.warnings.extend(state.notes)

    meta_title, author, stats = _odf_metadata(meta_root)
    if author:
        result.meta["author"] = author
    if kind == "odt" and stats.get("page_count"):
        result.page_count = int(stats["page_count"])
    # У книги и презентации первый заголовок — это имя листа или номер
    # слайда: названием документа он быть не может, лучше имя файла.
    if kind == "odt":
        result.title = meta_title or first_heading(result.text) or path.stem
    else:
        result.title = meta_title or path.stem
    if result.page_count:
        result.meta["page_count"] = result.page_count
    if result.is_empty:
        result.warnings.append(
            f"в документе не найдено текста ({_ODF_KIND_NAMES[kind]} пуст "
            "или состоит из одних изображений)"
        )
    return result


def convert_odt(path: Path) -> ConvertedDocument:
    """ODT → Markdown: заголовки по ``text:outline-level``, списки, таблицы, сноски."""
    return _convert_odf(path, "odt")


def convert_ods(path: Path) -> ConvertedDocument:
    """ODS → Markdown: каждый лист отдельным разделом с таблицей целиком."""
    return _convert_odf(path, "ods")


def convert_odp(path: Path) -> ConvertedDocument:
    """ODP → Markdown: каждый слайд отдельным разделом, с заметками докладчика."""
    return _convert_odf(path, "odp")


# ------------------------------------------------------------------- FB2 ---

def _fb2_inline(element: Any, state: _Blocks) -> str:
    """Текст абзаца FB2 со ссылками, сносками и выделениями."""
    parts: List[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        name = _tag(child)
        if name == "empty-line":
            parts.append("\n")
        elif name == "image":
            parts.append(f" {state.image()} ")
        elif name == "a":
            inner = _one_line(_fb2_inline(child, state))
            href = (_attr(child, "href", "xlink") or "").strip()
            kind = (_attr(child, "type") or "").strip().lower()
            if kind == "note" or href.startswith("#"):
                parts.append(f"[{inner}]" if inner else "")
            else:
                parts.append(inner)
                if href and useful_href(href) and href not in inner:
                    parts.append(f" ({href})")
        else:
            parts.append(_fb2_inline(child, state))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _fb2_paragraphs(element: Any, state: _Blocks) -> List[str]:
    """Абзацы внутри title/annotation/epigraph — по порядку, без пустых."""
    out: List[str] = []
    for child in element:
        name = _tag(child)
        if name in ("p", "v", "subtitle", "text-author"):
            text = _clean_block(_fb2_inline(child, state))
            if text:
                out.append(text)
        elif name == "empty-line":
            continue
        elif len(child):
            out.extend(_fb2_paragraphs(child, state))
    return out


def _fb2_table(table: Any, state: _Blocks) -> str:
    rows: List[List[str]] = []
    for row in table:
        if _tag(row) != "tr":
            continue
        cells: List[str] = []
        for cell in row:
            if _tag(cell) not in ("td", "th"):
                continue
            cells.append(_one_line(_fb2_inline(cell, state)))
            span = max(1, min(_int(_attr(cell, "colspan"), 1), 32))
            cells.extend([""] * (span - 1))
        if cells:
            rows.append(cells)
    return rows_to_markdown(rows)


def _fb2_quote(element: Any, state: _Blocks) -> str:
    """Эпиграф или цитата — блоком с «>», как принято в Markdown."""
    lines = _fb2_paragraphs(element, state)
    if not lines:
        return ""
    return "\n".join(f"> {_one_line(line)}" for line in lines if line.strip())


def _fb2_section(element: Any, state: _Blocks, level: int) -> None:
    for child in element:
        name = _tag(child)
        if name == "title":
            text = _one_line(" ".join(_fb2_paragraphs(child, state)))
            if text:
                state.add(f"{'#' * min(max(level, 1), 6)} {text}")
        elif name == "subtitle":
            text = _one_line(_fb2_inline(child, state))
            if text:
                state.add(f"{'#' * min(level + 1, 6)} {text}")
        elif name == "section":
            _fb2_section(child, state, level + 1)
        elif name == "p":
            state.add(_clean_block(_fb2_inline(child, state)))
        elif name in ("epigraph", "cite"):
            state.add(_fb2_quote(child, state))
        elif name in ("poem", "stanza"):
            for line in _fb2_paragraphs(child, state):
                state.add(line)
        elif name == "table":
            state.add(_fb2_table(child, state))
        elif name == "image":
            state.add(state.image())
        elif name == "text-author":
            text = _one_line(_fb2_inline(child, state))
            if text:
                state.add(f"— {text}")
        elif name == "annotation":
            for line in _fb2_paragraphs(child, state):
                state.add(line)
        elif name == "empty-line":
            continue
        elif len(child):
            _fb2_section(child, state, level)


def _fb2_author(node: Any) -> str:
    parts = []
    for name in ("first-name", "middle-name", "last-name", "nickname"):
        child = _first(node, name)
        if child is not None:
            value = _clean_line(child.text or "")
            if value:
                parts.append(value)
    return " ".join(parts)


def _fb2_description(root: Any, state: _Blocks, result: ConvertedDocument) -> str:
    """Разбирает description: название книги, авторы, аннотация."""
    description = _first(root, "description")
    if description is None:
        return ""
    title_info = _first(description, "title-info")
    if title_info is None:
        return ""
    book_title = ""
    node = _first(title_info, "book-title")
    if node is not None:
        book_title = _clean_line(node.text or "")
    authors = []
    for child in title_info:
        if _tag(child) == "author":
            author = _fb2_author(child)
            if author:
                authors.append(author)
    if authors:
        result.meta["author"] = ", ".join(authors)
    language = _first(title_info, "lang")
    if language is not None and (language.text or "").strip():
        result.meta["language"] = _clean_line(language.text or "")
    annotation = _first(title_info, "annotation")
    if annotation is not None:
        lines = _fb2_paragraphs(annotation, state)
        if lines:
            state.add("# Аннотация")
            for line in lines:
                state.add(line)
    return book_title


def convert_fb2(path: Path) -> ConvertedDocument:
    """FB2 → Markdown: название из description, секции заголовками, сноски отдельно.

    Номера страниц не проставляются: в FB2 страниц нет, они появляются только
    в читалке и зависят от её настроек. Место в книге находится по заголовкам —
    они попадают в «хлебные крошки» чанка и в ссылку под цитатой.
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    result = ConvertedDocument(title=path.stem, meta={"source_format": "fb2"})
    try:
        raw = path.read_bytes()
    except OSError as error:
        result.warnings.append(f"файл не прочитан: {_reason(error)}")
        return result
    if not raw.strip():
        result.warnings.append("файл пуст — книги в нём нет")
        return result

    root = None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        # Частый случай: объявлена одна кодировка, файл сохранён в другой.
        text, encoding, _ = decode_markup(raw, path)
        stripped = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
        try:
            root = ET.fromstring(stripped)
            result.meta["encoding"] = encoding
        except (ET.ParseError, ValueError) as second:
            result.warnings.append(
                f"FB2 не разобран: {_reason(error)}; после смены кодировки — {_reason(second)}"
            )
            return result
    except (ValueError, MemoryError, RecursionError) as error:
        result.warnings.append(f"FB2 не разобран: {_reason(error)}")
        return result

    if _tag(root).lower() != "fictionbook":
        result.warnings.append(
            f"корневой элемент «{_tag(root) or '?'}» — это не FB2; "
            "переименуйте файл в .xml, тогда он разберётся как обычный XML"
        )
        return result

    state = _Blocks()
    bodies = _children(root, "body")
    if not bodies:
        result.warnings.append("в книге нет ни одного body — текста нет")
    try:
        book_title = _fb2_description(root, state, result)
        for body in bodies:
            name = (body.get("name") or "").strip().lower()
            if name and not _children(body, "title"):
                # Примечания и комментарии своего заголовка обычно не имеют, а
                # разделом в оглавлении быть должны.
                state.add(f"# {_FB2_BODY_TITLES.get(name, name.capitalize())}")
            _fb2_section(body, state, 1)
    except (ValueError, RecursionError, MemoryError) as error:
        book_title = ""
        result.warnings.append(f"книга разобрана не полностью: {_reason(error)}")

    result.text = state.markdown()
    result.warnings.extend(state.notes)
    if state.images:
        result.meta["images"] = state.images
    result.title = book_title or first_heading(result.text) or path.stem
    if result.is_empty:
        result.warnings.append("в FB2 не найдено текста")
    return result


# ------------------------------------------------------------------ EPUB ---

_EPUB_TEXT_TYPES = ("application/xhtml+xml", "text/html", "application/x-dtbook+xml")


def _epub_opf_path(archive: zipfile.ZipFile, result: ConvertedDocument) -> str | None:
    """Путь к OPF из META-INF/container.xml (или поиск по архиву как запасной вариант)."""
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    raw = _read_zip_member(archive, "META-INF/container.xml")
    if raw:
        try:
            container = ET.fromstring(raw)
            for node in container.iter():
                if _tag(node) == "rootfile":
                    full_path = (node.get("full-path") or "").strip()
                    if full_path:
                        return unquote(full_path)
        except ET.ParseError:
            result.warnings.append("META-INF/container.xml повреждён — OPF ищем по архиву")
    candidates = [name for name in archive.namelist() if name.lower().endswith(".opf")]
    if candidates:
        return sorted(candidates, key=lambda name: (name.count("/"), len(name)))[0]
    return None


def _epub_resolve(base: str, href: str, names: Dict[str, str]) -> str | None:
    """Ссылка из OPF → имя записи в архиве (с учётом %-кодирования и «..»)."""
    target = unquote(href.split("#", 1)[0].strip())
    if not target:
        return None
    for candidate in (posixpath.normpath(posixpath.join(base, target)), target):
        cleaned = candidate.lstrip("./")
        if cleaned in names:
            return names[cleaned]
        lowered = cleaned.lower()
        if lowered in names:
            return names[lowered]
    return None


def convert_epub(path: Path) -> ConvertedDocument:
    """EPUB → Markdown: главы в порядке spine, HTML внутри чистится как обычный HTML.

    Номера страниц не проставляются намеренно: в EPUB их нет — вёрстка
    подстраивается под экран читалки. Навигация по книге в системе идёт по
    заголовкам глав, они же попадают в ссылку под цитатой.
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    result = ConvertedDocument(title=path.stem, meta={"source_format": "epub"})
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        result.warnings.append(f"файл повреждён: архив EPUB не открывается ({_reason(error)})")
        return result

    blocks: List[str] = []
    chapters = 0
    images = 0
    with archive:
        entries = archive.namelist()
        if "META-INF/encryption.xml" in entries:
            result.warnings.append(
                "книга защищена DRM — текст не извлечь; нужна версия без защиты"
            )
            return result
        names = {name.lstrip("./"): name for name in entries}
        names.update({name.lstrip("./").lower(): name for name in entries})

        opf_name = _epub_opf_path(archive, result)
        if opf_name is None:
            result.warnings.append("в архиве нет OPF-описания — это не EPUB")
            return result
        opf_raw = _read_zip_member(archive, names.get(opf_name.lstrip("./"), opf_name))
        if opf_raw is None:
            result.warnings.append(f"описание книги {opf_name} не читается")
            return result
        try:
            opf = ET.fromstring(opf_raw)
        except ET.ParseError as error:
            result.warnings.append(f"описание книги повреждено: {_reason(error)}")
            return result

        base = posixpath.dirname(opf_name)
        manifest: Dict[str, Dict[str, str]] = {}
        for node in opf.iter():
            if _tag(node) != "item":
                continue
            item_id = (node.get("id") or "").strip()
            if item_id:
                manifest[item_id] = {
                    "href": (node.get("href") or "").strip(),
                    "type": (node.get("media-type") or "").strip().lower(),
                    "properties": (node.get("properties") or "").strip().lower(),
                }

        book_title = ""
        author = ""
        for node in opf.iter():
            name = _tag(node)
            value = _clean_line(node.text or "")
            if name == "title" and value and not book_title:
                book_title = value
            elif name == "creator" and value and not author:
                author = value
            elif name == "language" and value:
                result.meta.setdefault("language", value)

        order: List[str] = []
        unknown = 0
        for node in opf.iter():
            if _tag(node) != "itemref":
                continue
            item_id = (node.get("idref") or "").strip()
            if not item_id or item_id not in manifest:
                unknown += 1
                continue
            item = manifest[item_id]
            if "nav" in item["properties"]:
                continue  # оглавление EPUB 3: дублирует заголовки глав
            if item["type"] and item["type"] not in _EPUB_TEXT_TYPES:
                continue
            order.append(item_id)
        if not order:
            order = [
                item_id for item_id, item in manifest.items()
                if item["type"] in _EPUB_TEXT_TYPES and "nav" not in item["properties"]
            ]
            if order:
                result.warnings.append(
                    "в описании книги пуст spine — главы взяты в порядке манифеста"
                )

        missing = unknown
        for number, item_id in enumerate(order, start=1):
            member = _epub_resolve(base, manifest[item_id]["href"], names)
            if member is None:
                missing += 1
                continue
            raw = _read_zip_member(archive, member)
            if raw is None:
                missing += 1
                continue
            parsed = html_to_markdown(decode_markup(raw)[0])
            if not parsed.text.strip():
                continue
            chapters += 1
            images += parsed.images
            heading = parsed.title or f"Раздел {number}"
            first_line = parsed.text.lstrip().split("\n", 1)[0]
            if not first_line.startswith("# "):
                blocks.append(f"# {_one_line(heading)}")
            blocks.append(parsed.text)
        if missing:
            result.warnings.append(
                f"глав не найдено в архиве: {missing} — книга собрана с ошибками, "
                "разобрано то, что есть"
            )

    result.text = merge_list_blocks(blocks)
    result.meta["chapters"] = chapters
    if author:
        result.meta["author"] = author
    if images:
        result.meta["images"] = images
    result.title = book_title or first_heading(result.text) or path.stem
    if result.is_empty:
        result.warnings.append(
            "в книге не найдено текста: либо это скан в картинках, либо разметка пуста"
        )
    return result


# -------------------------------------------------------- регистрация ----

registry.register(registry.ConverterSpec(
    name="opendocument-text",
    suffixes=(".odt", ".ott", ".fodt"),
    convert=convert_odt,
    requires=(),
    note="OpenDocument Writer: заголовки по outline-level, списки, таблицы, сноски",
))

registry.register(registry.ConverterSpec(
    name="opendocument-spreadsheet",
    suffixes=(".ods", ".ots", ".fods"),
    convert=convert_ods,
    requires=(),
    note="OpenDocument Calc: каждый лист разделом, таблица целиком",
))

registry.register(registry.ConverterSpec(
    name="opendocument-presentation",
    suffixes=(".odp", ".otp", ".fodp"),
    convert=convert_odp,
    requires=(),
    note="OpenDocument Impress: слайд разделом, с заметками докладчика",
))

registry.register(registry.ConverterSpec(
    name="fb2",
    suffixes=(".fb2",),
    convert=convert_fb2,
    requires=(),
    note="FictionBook 2: название из description, секции заголовками",
))

registry.register(registry.ConverterSpec(
    name="epub",
    suffixes=(".epub",),
    convert=convert_epub,
    requires=(),
    note="EPUB 2 и 3: главы в порядке spine, разметка чистится разбором HTML",
))
