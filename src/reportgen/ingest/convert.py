"""Конвертация исходных файлов библиотеки в Markdown.

Слой приёма превращает то, что реально лежит у инженеров на диске (PDF,
DOCX, заметки в .md и .txt), в единый Markdown, который дальше нарезается на
чанки функциями из :mod:`reportgen.corpus`. Формат чанка при этом не меняется —
конвертер лишь готовит для него текст.

Что важно знать про результат конвертации:

* **Заголовки.** В PDF их нет как сущности, есть только кегль шрифта, поэтому
  заголовки восстанавливаются по размеру шрифта относительно основного текста
  (медиана по всему документу). В DOCX заголовки берутся из стилей ``Heading N``.
* **Номера страниц.** Для PDF в текст вставляются невидимые в разметке маркеры
  ``<!-- page: N -->``. По ним слой приёма проставляет ``meta['page']`` каждому
  чанку — без этого ссылка в отчёте («с. 47») невозможна.
* **Сканы.** Если текста в PDF почти нет, документ помечается ``needs_ocr``:
  система не притворяется, что разобрала скан, а честно говорит, что нужен OCR.
* **Битые файлы не роняют приём.** Любая ошибка разбора превращается в
  предупреждение и пустой текст: один испорченный файл не должен останавливать
  индексацию каталога на тысячу документов.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple

from ..corpus import DOC_TYPES

__all__ = [
    "ConvertedDocument",
    "MissingDependencyError",
    "SUPPORTED_SUFFIXES",
    "BUILTIN_SUFFIXES",
    "read_text",
    "clean_line",
    "external_path",
    "supported_suffixes",
    "format_support",
    "convert_file",
    "first_page_number",
    "guess_doc_type",
    "page_marker",
    "page_markers",
    "sha256_file",
    "strip_page_markers",
]

#: Расширения, которые разбирает само ядро, без модулей форматов и внешних
#: программ. Полный список того, что система читает на этой машине, отдаёт
#: supported_suffixes(); он же доступен как SUPPORTED_SUFFIXES (см. __getattr__).
BUILTIN_SUFFIXES = (".pdf", ".docx", ".dotx", ".md", ".markdown", ".txt")
DEFAULT_DOC_TYPE = "literature"

#: Ниже этого числа символов на страницу PDF считается сканом (нужен OCR).
MIN_CHARS_PER_PAGE = 100

#: Во сколько раз строка должна быть крупнее основного текста, чтобы считаться
#: заголовком. 1.12 — компромисс: подзаголовки обычно крупнее тела на 15–30 %.
HEADING_MIN_RATIO = 1.12
#: Длинный абзац крупным шрифтом (например, аннотация) заголовком не считается.
HEADING_MAX_CHARS = 200
#: Больше трёх уровней в восстановленной структуре смысла не имеет.
MAX_HEADING_LEVEL = 3

_READ_BLOCK = 1 << 20

#: Маркер страницы. Это HTML-комментарий: в разметке он не виден, в индекс не
#: попадает (слой приёма вырезает его), но позволяет привязать чанк к странице.
_PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")

_HYPHENS = "-‐‑­−"
_HEADING_STYLE_RE = re.compile(r"^(?:heading|заголовок)\s*(\d+)$", re.IGNORECASE)
_TITLE_STYLES = {"title", "название", "заголовок"}
_SUBTITLE_STYLES = {"subtitle", "подзаголовок"}
_CAPTION_STYLES = {"caption", "название объекта", "подпись", "подрисуночная подпись"}
_LIST_STYLE_MARKERS = ("list", "список", "bullet", "маркированный", "нумерованный")

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class MissingDependencyError(RuntimeError):
    """Не установлен пакет, нужный для разбора формата."""


@dataclass
class ConvertedDocument:
    """Результат конвертации одного файла в Markdown."""

    text: str = ""
    title: str = ""
    page_count: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)
    needs_ocr: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Пусто ли содержимое (с учётом того, что маркеры страниц — не текст)."""
        return not strip_page_markers(self.text).strip()

    @property
    def char_count(self) -> int:
        return len(strip_page_markers(self.text).strip())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "page_count": self.page_count,
            "chars": self.char_count,
            "needs_ocr": self.needs_ocr,
            "meta": self.meta,
            "warnings": list(self.warnings),
        }


# ------------------------------------------------------------ утилиты ----

def page_marker(page: int) -> str:
    """Маркер начала страницы, вставляемый в Markdown."""
    return f"<!-- page: {int(page)} -->"


def strip_page_markers(text: str) -> str:
    """Убирает маркеры страниц и лишние пустые строки после них."""
    cleaned = _PAGE_MARKER_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def page_markers(text: str) -> List[Tuple[int, int]]:
    """Все маркеры страниц во фрагменте: пары (номер страницы, смещение в тексте)."""
    return [(int(match.group(1)), match.start()) for match in _PAGE_MARKER_RE.finditer(text)]


def first_page_number(text: str) -> int | None:
    """Номер первой страницы, упомянутой в фрагменте, или ``None``."""
    match = _PAGE_MARKER_RE.search(text)
    return int(match.group(1)) if match else None


def sha256_file(path: str | Path) -> str:
    """SHA-256 файла. Читается блоками: даташит на 300 МБ в память не влезет."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_READ_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def guess_doc_type(path: str | Path, root: str | Path | None = None) -> str:
    """Тип документа по имени каталога верхнего уровня внутри корпуса.

    Правило то же, что в :func:`reportgen.corpus.load_file`: ``standards/ГОСТ.pdf``
    даёт ``standards``. Если каталог не опознан (или файл лежит прямо в корне
    корпуса), возвращается ``literature`` — библиотека по умолчанию.
    """
    path = Path(path)
    if root is not None:
        try:
            relative = _resolve(path).relative_to(_resolve(Path(root)))
        except ValueError:
            relative = None
        if relative is not None:
            if len(relative.parts) > 1 and relative.parts[0].lower() in DOC_TYPES:
                return relative.parts[0].lower()
            return DEFAULT_DOC_TYPE
    for parent in _resolve(path).parents:
        if parent.name.lower() in DOC_TYPES:
            return parent.name.lower()
    return DEFAULT_DOC_TYPE


@contextmanager
def external_path(path: Path, suffix: str | None = None) -> Iterator[Path]:
    """Путь к файлу, безопасный для сторонних программ.

    Часть внешних инструментов (djvulibre, а в некоторых сборках tesseract и
    LibreOffice) не открывает файлы, в имени или пути которых есть кириллица:
    они собраны без поддержки юникода в аргументах и молча возвращают ошибку
    «файл не найден». Проверено: cjb2 на файле «скан.pbm» падает, на «scan.pbm»
    работает.

    В библиотеке заказчика по-русски названо почти всё, поэтому перед вызовом
    внешней программы файл при необходимости копируется во временный каталог
    под латинским именем. Копия удаляется автоматически.
    """
    text = str(path)
    if text.isascii():
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="reportgen-tool-") as directory:
        target = Path(directory) / f"document{suffix or path.suffix.lower()}"
        shutil.copyfile(path, target)
        yield target


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover — экзотические ФС
        return path.absolute()


def _reason(error: BaseException) -> str:
    text = str(error).strip()
    return text or error.__class__.__name__


def _join_wrapped(left: str, right: str) -> str:
    """Склеивает строку, разорванную переносом слова."""
    if (
        len(left) >= 2
        and left[-1] in _HYPHENS
        and left[-2].isalpha()
        and right[:1].isalpha()
        and right[:1].islower()
    ):
        return left[:-1] + right
    return f"{left} {right}"


def clean_line(text: str) -> str:
    """Нормализует строку: неразрывные пробелы, мягкие переносы, хвостовые пробелы."""
    text = text.replace("­", "").replace(" ", " ").replace("﻿", "")
    text = "".join(
        character
        for character in text
        if character in "\t\n" or not unicodedata.category(character).startswith("C")
    )
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _weighted_median(samples: Sequence[Tuple[float, int]]) -> float:
    """Медиана размеров шрифта, взвешенная по числу символов.

    Взвешивание принципиально: колонтитулы и номера страниц набраны мелко, но
    их много, а по числу символов основной текст всё равно перевешивает.
    """
    pairs = sorted((float(size), max(1, int(weight))) for size, weight in samples)
    total = sum(weight for _, weight in pairs)
    if not total:
        return 0.0
    half = total / 2
    accumulated = 0
    for size, weight in pairs:
        accumulated += weight
        if accumulated >= half:
            return size
    return pairs[-1][0]


# ------------------------------------------------------- ленивый импорт ---

def _import_pymupdf():
    try:
        import pymupdf  # type: ignore
    except ImportError as error:  # pragma: no cover — зависит от окружения
        raise MissingDependencyError(
            "для разбора PDF нужен пакет pymupdf (pip install pymupdf); "
            "установите его в изолированном контуре из локального зеркала"
        ) from error
    return pymupdf


def _import_docx():
    try:
        import docx  # type: ignore
    except ImportError as error:  # pragma: no cover — зависит от окружения
        raise MissingDependencyError(
            "для разбора DOCX нужен пакет python-docx (pip install python-docx); "
            "установите его в изолированном контуре из локального зеркала"
        ) from error
    return docx


# ------------------------------------------------------------------ PDF ---

def _pdf_page_blocks(page: Any) -> List[List[Tuple[str, float]]]:
    """Блоки страницы: список абзацев, абзац — список пар (строка, кегль)."""
    data = page.get_text("dict", sort=True)
    blocks: List[List[Tuple[str, float]]] = []
    for raw_block in data.get("blocks", []):
        if raw_block.get("type", 0) != 0:  # тип 1 — изображение, текста в нём нет
            continue
        lines: List[Tuple[str, float]] = []
        for raw_line in raw_block.get("lines", []):
            spans = [span for span in raw_line.get("spans", []) if span.get("text", "").strip()]
            if not spans:
                continue
            text = clean_line("".join(span.get("text", "") for span in spans))
            if not text:
                continue
            size = max(float(span.get("size", 0.0)) for span in spans)
            lines.append((text, size))
        if lines:
            blocks.append(lines)
    return blocks


def _heading_levels(blocks: Sequence[Sequence[Tuple[str, float]]], body_size: float) -> Dict[float, int]:
    """Сопоставляет кегли заголовков уровням '#', '##', '###'.

    Абсолютные пороги («в 1.35 раза крупнее — это h1») ломаются на первом же
    документе с другой вёрсткой, поэтому кегли ранжируются: самый крупный из
    встреченных заголовочных размеров становится первым уровнем.
    """
    if body_size <= 0:
        return {}
    candidates = set()
    for block in blocks:
        text = " ".join(line for line, _ in block)
        if len(text) > HEADING_MAX_CHARS:
            continue
        size = max(size for _, size in block)
        if size >= body_size * HEADING_MIN_RATIO:
            candidates.add(round(size * 2) / 2)
    ranked = sorted(candidates, reverse=True)[:MAX_HEADING_LEVEL]
    return {size: level for level, size in enumerate(ranked, start=1)}


def _render_blocks(
    blocks: Sequence[Sequence[Tuple[str, float]]], levels: Dict[float, int]
) -> List[str]:
    """Превращает блоки страницы в куски Markdown."""
    parts: List[Tuple[str, str]] = []  # (вид, текст)
    for block in blocks:
        text = ""
        for line, _ in block:
            text = line if not text else _join_wrapped(text, line)
        if not text:
            continue
        size = round(max(size for _, size in block) * 2) / 2
        level = levels.get(size) if len(text) <= HEADING_MAX_CHARS else None
        if level:
            parts.append(("heading", f"{'#' * level} {text}"))
            continue
        if parts and parts[-1][0] == "paragraph" and parts[-1][1].endswith(tuple(_HYPHENS)):
            # Абзац разорван между блоками (частый случай в двухколоночной
            # вёрстке): слово с переносом склеиваем обратно.
            parts[-1] = ("paragraph", _join_wrapped(parts[-1][1], text))
            continue
        parts.append(("paragraph", text))
    return [text for _, text in parts]


def _convert_pdf(path: Path) -> ConvertedDocument:
    """PDF → Markdown через PyMuPDF, с восстановлением заголовков по кеглю."""
    result = ConvertedDocument(title=path.stem, meta={"source_format": "pdf"})
    try:
        pymupdf = _import_pymupdf()
    except MissingDependencyError as error:
        result.warnings.append(str(error))
        return result

    try:
        document = pymupdf.open(str(path))
    except Exception as error:
        result.warnings.append(f"не удалось открыть PDF: {_reason(error)}")
        return result

    try:
        if getattr(document, "needs_pass", False):
            result.warnings.append(
                "PDF защищён паролем — текст не извлечён; расшифруйте файл перед приёмом"
            )
            return result
        result.page_count = int(getattr(document, "page_count", 0) or 0)
        result.meta["page_count"] = result.page_count

        pages: List[List[List[Tuple[str, float]]]] = []
        for number in range(result.page_count):
            try:
                pages.append(_pdf_page_blocks(document[number]))
            except Exception as error:
                result.warnings.append(
                    f"страница {number + 1}: текст не извлечён ({_reason(error)})"
                )
                pages.append([])

        metadata = dict(getattr(document, "metadata", None) or {})
    except Exception as error:
        result.warnings.append(f"PDF разобран не полностью: {_reason(error)}")
        return result
    finally:
        try:
            document.close()
        except Exception:  # pragma: no cover — закрытие уже закрытого документа
            pass

    samples = [
        (size, len(line))
        for page in pages
        for block in page
        for line, size in block
    ]
    body_size = _weighted_median(samples)
    levels = _heading_levels([block for page in pages for block in page], body_size)
    result.meta["body_font_size"] = round(body_size, 2)

    pieces: List[str] = []
    for number, page in enumerate(pages, start=1):
        rendered = _render_blocks(page, levels)
        if not rendered:
            continue
        pieces.append(page_marker(number))
        pieces.extend(rendered)
    result.text = "\n\n".join(pieces)

    characters = sum(length for _, length in samples)
    if result.page_count and characters < MIN_CHARS_PER_PAGE * result.page_count:
        result.needs_ocr = True
        per_page = characters / result.page_count
        result.warnings.append(
            f"извлечено всего {characters} символов ({per_page:.0f} на страницу) — "
            "похоже на скан; нужен слой OCR (PaddleOCR или tesseract с языками rus+eng), "
            "документ в индекс не берём"
        )
    if not result.page_count:
        result.warnings.append("в PDF нет ни одной страницы")

    title = str(metadata.get("title") or "").strip()
    if not title:
        title = _first_markdown_heading(result.text)
    result.title = title or path.stem
    for key in ("author", "subject", "keywords"):
        value = str(metadata.get(key) or "").strip()
        if value:
            result.meta[key] = value
    return result


# ----------------------------------------------------------------- DOCX ---

def _style_name(paragraph: Any) -> str:
    try:
        return (paragraph.style.name or "").strip()
    except Exception:  # pragma: no cover — документ без таблицы стилей
        return ""


def _docx_heading_level(style: str) -> int | None:
    lowered = style.strip().lower()
    match = _HEADING_STYLE_RE.match(lowered)
    if match:
        return min(int(match.group(1)), 6)
    if lowered in _TITLE_STYLES:
        return 1
    if lowered in _SUBTITLE_STYLES:
        return 2
    return None


def _is_list_style(style: str) -> bool:
    lowered = style.strip().lower()
    if lowered in _CAPTION_STYLES:
        return False
    return any(marker in lowered for marker in _LIST_STYLE_MARKERS)


def _cell_text(cell: Any) -> str:
    lines = [clean_line(paragraph.text) for paragraph in cell.paragraphs]
    text = " <br> ".join(line for line in lines if line)
    return text.replace("|", "\\|").strip()


def _table_to_markdown(table: Any) -> str:
    """Таблица DOCX → таблица Markdown (целиком, без разрезания на части)."""
    rows: List[List[str]] = []
    for row in table.rows:
        cells: List[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            # Объединённые ячейки python-docx возвращает несколько раз подряд.
            marker = id(cell._tc)
            if marker in seen:
                continue
            seen.add(marker)
            cells.append(_cell_text(cell))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _paragraph_images(element: Any) -> int:
    return sum(
        1
        for child in element.iter()
        if child.tag in (f"{_WORD_NS}drawing", f"{_WORD_NS}pict")
    )


def _convert_docx(path: Path) -> ConvertedDocument:
    """DOCX → Markdown: стили заголовков, таблицы, списки, подписи к рисункам."""
    result = ConvertedDocument(title=path.stem, meta={"source_format": "docx"})
    try:
        docx = _import_docx()
    except MissingDependencyError as error:
        result.warnings.append(str(error))
        return result

    try:
        from docx.table import Table  # type: ignore
        from docx.text.paragraph import Paragraph  # type: ignore

        document = docx.Document(str(path))
    except Exception as error:
        result.warnings.append(f"не удалось открыть DOCX: {_reason(error)}")
        return result

    pieces: List[str] = []
    heading_title = ""
    images = 0
    try:
        for child in document.element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "tbl":
                try:
                    markdown = _table_to_markdown(Table(child, document))
                except Exception as error:
                    result.warnings.append(f"таблица пропущена: {_reason(error)}")
                    continue
                if markdown:
                    pieces.append(markdown)
                continue
            if tag != "p":
                continue
            paragraph = Paragraph(child, document)
            text = clean_line(paragraph.text)
            count = _paragraph_images(child)
            if count:
                for number in range(images + 1, images + count + 1):
                    # Картинки не извлекаем, но место в тексте помечаем: инженер
                    # должен видеть, что здесь был рисунок (док. 03, шаг 1).
                    pieces.append(f"![рисунок {number}]()")
                images += count
            if not text:
                continue
            style = _style_name(paragraph)
            level = _docx_heading_level(style)
            if level:
                pieces.append(f"{'#' * level} {text}")
                if not heading_title and level == 1:
                    heading_title = text
                continue
            if style.strip().lower() in _CAPTION_STYLES:
                # Подпись к рисунку/таблице — обычный текст: она несёт смысл и
                # должна попадать в поиск вместе с окружающим разделом.
                pieces.append(text)
                continue
            if _is_list_style(style):
                pieces.append(f"- {text}")
                continue
            pieces.append(text)
    except Exception as error:
        result.warnings.append(f"DOCX разобран не полностью: {_reason(error)}")

    result.text = _merge_list_items(pieces)
    if images:
        result.meta["images"] = images
        result.warnings.append(
            f"изображений в документе: {images} — они не извлекаются, "
            "в тексте оставлены плейсхолдеры"
        )
    try:
        properties = document.core_properties
        core_title = (properties.title or "").strip()
        author = (properties.author or "").strip()
    except Exception:  # pragma: no cover — документ без core.xml
        core_title, author = "", ""
    if author:
        result.meta["author"] = author
    result.title = core_title or heading_title or _first_markdown_heading(result.text) or path.stem
    if result.is_empty:
        result.warnings.append("в DOCX не найдено текста")
    return result


def _merge_list_items(pieces: Sequence[str]) -> str:
    """Соседние пункты списка держим одним абзацем, остальное разделяем пустой строкой."""
    out: List[str] = []
    for piece in pieces:
        if piece.startswith("- ") and out and out[-1].startswith("- "):
            out[-1] = f"{out[-1]}\n{piece}"
            continue
        out.append(piece)
    return "\n\n".join(out)


# -------------------------------------------------------- текст и Markdown ---

#: Порядок перебора кодировок: в контуре встречаются и старые файлы в cp1251.
_ENCODINGS = ("utf-8", "cp1251", "koi8-r")


def read_text(path: Path) -> Tuple[str, str | None, str | None]:
    """Читает текстовый файл, перебирая кодировки. Возвращает (текст, кодировка, ошибка)."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        return "", None, f"файл не прочитан: {_reason(error)}"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace"), "utf-8-sig", None
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding), encoding, None
        except UnicodeDecodeError:
            continue
    return (
        raw.decode("utf-8", errors="replace"),
        "utf-8/replace",
        "кодировка файла не распознана, часть символов заменена",
    )


def _first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _convert_text(path: Path) -> ConvertedDocument:
    """Markdown и обычный текст берём как есть — их уже написал человек."""
    kind = "markdown" if path.suffix.lower() in (".md", ".markdown") else "text"
    result = ConvertedDocument(title=path.stem, meta={"source_format": kind})
    text, encoding, problem = read_text(path)
    if problem:
        result.warnings.append(problem)
    if encoding:
        result.meta["encoding"] = encoding
    result.text = text.replace("\r\n", "\n").replace("\r", "\n")

    from ..corpus import parse_front_matter

    front, body = parse_front_matter(result.text)
    for key, value in front.items():
        result.meta.setdefault(key, value)
    result.title = front.get("title") or _first_markdown_heading(body) or path.stem
    if not result.text.strip():
        result.warnings.append("файл пуст")
    return result


# ---------------------------------------------------------- диспетчер ----

def _register_builtin_converters() -> None:
    """Форматы, которые система разбирает сама, без сторонних инструментов."""
    from . import registry

    registry.register(registry.ConverterSpec(
        name="pdf",
        suffixes=(".pdf",),
        convert=_convert_pdf,
        requires=(registry.Requirement("python", "pymupdf", "pip install pymupdf"),),
        note="текстовый слой PDF, заголовки по кеглю, номера страниц",
    ))
    registry.register(registry.ConverterSpec(
        name="docx",
        suffixes=(".docx", ".dotx"),
        convert=_convert_docx,
        requires=(registry.Requirement("python", "docx", "pip install python-docx"),),
        note="Word 2007 и новее: заголовки по стилям, таблицы, списки",
    ))
    registry.register(registry.ConverterSpec(
        name="text",
        suffixes=(".md", ".markdown", ".txt"),
        convert=_convert_text,
        note="текст и Markdown, кодировка определяется автоматически",
    ))


_register_builtin_converters()


def supported_suffixes(*, only_available: bool = False) -> Tuple[str, ...]:
    """Все расширения, которые система умеет разбирать сейчас."""
    from . import registry

    return registry.supported_suffixes(only_available=only_available)


def format_support() -> List[Dict[str, Any]]:
    """Состояние поддержки форматов: что доступно, чего не хватает."""
    from . import registry

    return registry.report()


def convert_file(path: str | Path) -> ConvertedDocument:
    """Конвертирует файл в Markdown по его расширению.

    Функция никогда не бросает исключений на содержимом файла: битый PDF или
    защищённый паролем DOCX дают пустой текст и предупреждение. Это осознанное
    решение — приём каталога на тысячу файлов не должен падать на одном.

    Если формат известен, но инструмент для него не установлен, в
    предупреждении будет сказано, что именно доложить — в изолированном
    контуре это экономит часы выяснений.
    """
    from . import registry

    path = Path(path)
    suffix = path.suffix.lower()
    if not path.is_file():
        result = ConvertedDocument(title=path.stem)
        result.warnings.append(f"файл не найден: {path}")
        return result

    spec = registry.find(suffix)
    if spec is None:
        result = ConvertedDocument(title=path.stem,
                                   meta={"source_format": suffix.lstrip(".")})
        available = ", ".join(registry.supported_suffixes(only_available=True))
        result.warnings.append(
            f"неподдерживаемый формат «{suffix or 'без расширения'}»: "
            f"сейчас доступны {available}"
        )
        return result

    if not spec.is_available():
        result = ConvertedDocument(title=path.stem,
                                   meta={"source_format": suffix.lstrip(".")})
        result.warnings.append(
            f"формат «{suffix}» разбирается конвертером «{spec.name}», но "
            f"{registry.missing_hint(spec)}"
        )
        return result

    try:
        return spec.convert(path)
    except MissingDependencyError as error:
        result = ConvertedDocument(title=path.stem,
                                   meta={"source_format": suffix.lstrip(".")})
        result.warnings.append(str(error))
        return result
    except Exception as error:  # noqa: BLE001 — один файл не должен ронять приём каталога
        result = ConvertedDocument(title=path.stem,
                                   meta={"source_format": suffix.lstrip(".")})
        result.warnings.append(
            f"конвертер «{spec.name}» не справился с файлом: {_reason(error)}"
        )
        return result


#: Старые имена: на них опираются модули форматов и внешний код.
_read_text = read_text
_clean_line = clean_line


def __getattr__(name: str) -> object:
    """SUPPORTED_SUFFIXES отдаёт настоящий список форматов этой установки.

    Раньше это был кортеж из пяти расширений, зашитый в код. После перехода на
    реестр конвертеров он стал врать: система читает шесть десятков форматов,
    а константа обещала пять. Вычисляем на лету.
    """
    if name == "SUPPORTED_SUFFIXES":
        return supported_suffixes()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
