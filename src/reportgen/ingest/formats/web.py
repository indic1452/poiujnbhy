"""Веб-форматы и почта: HTML, XHTML, письма ``.eml``, произвольный XML.

Модуль намеренно написан на одной стандартной библиотеке. В изолированном
контуре каждый сторонний пакет — это отдельная строка в заявке на поставку и
отдельный риск на установке; HTML и письма встречаются в библиотеке постоянно
(выгрузка из вики, сохранённая страница вендора, переписка с заказчиком с
дампом во вложении), поэтому их разбор не должен ни от чего зависеть.

Что здесь есть, кроме конвертеров:

* :func:`html_to_markdown` — разбор HTML в Markdown. Им же пользуется разбор
  глав EPUB (:mod:`reportgen.ingest.formats.opendoc`), поэтому чистка тегов в
  системе одна, а не две расходящиеся.
* :func:`rows_to_markdown` и :func:`cell_text` — сборка markdown-таблицы из
  готовых строк. Таблица собирается целиком, без разрезания: для технической
  библиотеки таблица допусков часто и есть весь смысл документа, а половина
  таблицы в чанке хуже, чем её отсутствие.
* :func:`merge_list_blocks` — соседние пункты списка склеиваются в один абзац,
  как это делает разбор DOCX в :mod:`reportgen.ingest.convert`.

Ни одна функция не бросает исключение на содержимом файла: битый HTML, письмо
без темы, XML с обрывом в середине дают то, что удалось разобрать, и понятное
предупреждение по-русски.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .. import registry
from ..convert import _ENCODINGS, ConvertedDocument, _clean_line, _read_text, _reason

__all__ = [
    "HtmlText",
    "cell_text",
    "convert_eml",
    "convert_html",
    "convert_xml",
    "decode_markup",
    "html_to_markdown",
    "merge_list_blocks",
    "rows_to_markdown",
]

#: Теги, содержимое которых в текст документа не попадает. Меню и подвал есть
#: на каждой странице вики: без их отсева поиск находит «Печать | Экспорт |
#: Обсуждение» в сотне документов сразу.
_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "nav", "footer", "svg", "iframe",
    "template", "object", "canvas", "map", "audio", "video",
})

#: Теги, закрывающие абзац. Всё, что накоплено, сбрасывается в отдельный блок.
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "header", "main", "aside", "address",
    "figure", "figcaption", "caption", "form", "fieldset", "details",
    "summary", "center", "body", "html", "thead", "tbody", "tfoot",
    "colgroup", "option", "label", "legend",
})

_HEADING_TAGS = {f"h{level}": level for level in range(1, 7)}

_LIST_CONTAINERS = frozenset({"ul", "ol", "dl", "menu"})
_LIST_ITEMS = frozenset({"li", "dt", "dd"})

#: Предел на объединённые ячейки: colspan="10000" встречается в машинной вёрстке.
_MAX_COLSPAN = 32

#: Строка, с которой начинается пункт списка (в любой из принятых записей).
_LIST_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]*?charset\s*=\s*["']?\s*([A-Za-z0-9_.:+-]+)""", re.IGNORECASE
)
_XML_ENCODING_RE = re.compile(
    rb"""<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9_.:+-]+)["']""", re.IGNORECASE
)

#: Сколько узлов XML выводим максимум: выгрузка биллинга на миллион записей в
#: библиотеке бесполезна, а память съест.
_MAX_XML_BLOCKS = 20000


# ------------------------------------------------------- общие помощники ---

def cell_text(text: str) -> str:
    """Текст ячейки для markdown-таблицы: без переводов строк и без сырых «|»."""
    cleaned = "\n".join(_clean_line(line) for line in str(text).split("\n"))
    cleaned = cleaned.replace("|", "\\|")
    cleaned = re.sub(r"\n{2,}", "\n", cleaned).strip("\n")
    return cleaned.replace("\n", " <br> ").strip()


def rows_to_markdown(rows: Sequence[Sequence[str]]) -> str:
    """Строки таблицы → таблица Markdown целиком.

    Первая непустая строка становится шапкой: в технических документах это
    почти всегда так, а Markdown без шапки таблицу не рисует.
    """
    cleaned: List[List[str]] = []
    for row in rows:
        prepared = [cell_text(cell) for cell in row]
        if any(prepared):
            cleaned.append(prepared)
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    if not width:
        return ""
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    lines = [
        "| " + " | ".join(cleaned[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in cleaned[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def flatten_table(markdown: str) -> str:
    """Вложенная таблица внутри ячейки: строки через « / », без разметки."""
    parts: List[str] = []
    for line in markdown.split("\n"):
        stripped = line.strip().strip("|").strip()
        if not stripped or set(stripped.replace("|", "").strip()) <= {"-", " "}:
            continue
        parts.append(" / ".join(item.strip() for item in stripped.split("|") if item.strip()))
    return "; ".join(part for part in parts if part)


def merge_list_blocks(blocks: Sequence[str]) -> str:
    """Собирает блоки в Markdown, склеивая подряд идущие пункты списка."""
    out: List[str] = []
    for block in blocks:
        if not block:
            continue
        if _LIST_LINE_RE.match(block) and out:
            last_line = out[-1].rsplit("\n", 1)[-1]
            if _LIST_LINE_RE.match(last_line):
                out[-1] = f"{out[-1]}\n{block}"
                continue
        out.append(block)
    return "\n\n".join(out)


def useful_href(href: str) -> bool:
    """Стоит ли выносить адрес ссылки в текст."""
    lowered = href.strip().lower()
    if not lowered:
        return False
    return not lowered.startswith(("#", "javascript:", "data:", "about:", "cid:"))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def human_size(size: int) -> str:
    """Размер файла по-русски: «1,4 МБ»."""
    for unit, limit in (("ГБ", 1 << 30), ("МБ", 1 << 20), ("КБ", 1 << 10)):
        if size >= limit:
            return f"{size / limit:.1f}".replace(".", ",") + f" {unit}"
    return f"{size} Б"


def first_heading(text: str) -> str:
    """Первый заголовок Markdown — запасное название документа."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


# ---------------------------------------------------------------- HTML ----

@dataclass
class HtmlText:
    """Результат разбора куска HTML."""

    text: str = ""
    title: str = ""
    images: int = 0


class _TableBuilder:
    """Накопитель одной таблицы HTML.

    Собирается целиком и только потом отдаётся в Markdown: разрезать таблицу
    по границе чанка нельзя, иначе в индекс попадут строки без шапки.
    """

    def __init__(self) -> None:
        self.rows: List[List[str]] = []
        self.before: List[str] = []
        self._row: List[str] | None = None
        self._cell: List[str] | None = None
        self._colspan = 1

    def start_row(self) -> None:
        self.end_row()
        self._row = []

    def end_row(self) -> None:
        self.end_cell()
        if self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None

    def start_cell(self, colspan: int = 1) -> None:
        self.end_cell()
        if self._row is None:
            self._row = []
        self._cell = []
        self._colspan = max(1, min(colspan, _MAX_COLSPAN))

    def end_cell(self) -> None:
        if self._cell is None:
            return
        text = "\n".join(part for part in self._cell if part)
        if self._row is None:
            self._row = []
        self._row.append(text)
        self._row.extend([""] * (self._colspan - 1))
        self._cell = None
        self._colspan = 1

    def add_text(self, text: str) -> None:
        if not text:
            return
        if self._cell is not None:
            self._cell.append(text)
        else:
            # Подпись к таблице (<caption>) или текст между ячейками.
            self.before.append(text)

    def render(self) -> str:
        self.end_row()
        table = rows_to_markdown(self.rows)
        parts = [part for part in self.before if part]
        if table:
            parts.append(table)
        return "\n\n".join(parts)


class _HtmlToMarkdown(HTMLParser):
    """Разборщик HTML в Markdown на ``html.parser``.

    Сущности (``&nbsp;``, ``&laquo;``) декодирует сам ``HTMLParser``
    (``convert_charrefs=True``), неразрывные пробелы и мягкие переносы убирает
    :func:`reportgen.ingest.convert._clean_line` — тот же код, что и для PDF.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[str] = []
        self.images = 0
        self._title_parts: List[str] = []
        self._buffer: List[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        self._in_title = False
        self._heading = 0
        self._quote_depth = 0
        self._lists: List[Dict[str, Any]] = []
        self._tables: List[_TableBuilder] = []
        self._links: List[Tuple[str, int]] = []

    # --- накопление текста ---

    def _take(self) -> str:
        raw = "".join(self._buffer)
        self._buffer.clear()
        if not raw.strip():
            return ""
        lines: List[str] = []
        for line in raw.split("\n"):
            cleaned = _clean_line(line)
            if cleaned:
                lines.append(cleaned)
            elif lines and lines[-1]:
                lines.append("")
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _close_block(self) -> None:
        text = self._take()
        if not text:
            return
        if self._tables:
            self._tables[-1].add_text(text)
            return
        if self._lists and self._lists[-1]["open"]:
            self._add_list_line(text)
            return
        if self._heading:
            self.blocks.append(f"{'#' * self._heading} {text.replace(chr(10), ' ')}")
            return
        if self._quote_depth:
            self.blocks.append("\n".join(f"> {line}" for line in text.split("\n")))
            return
        self.blocks.append(text)

    def _add_list_line(self, text: str) -> None:
        entry = self._lists[-1]
        indent = "  " * (len(self._lists) - 1)
        if entry["lines"]:
            # Второй и следующие абзацы того же пункта — продолжение строки.
            self.blocks.append(f"{indent}  {text}")
        elif entry["ordered"]:
            self.blocks.append(f"{indent}{entry['index']}. {text}")
            entry["index"] += 1
        else:
            self.blocks.append(f"{indent}- {text}")
        entry["lines"] += 1

    def _flush_pre(self) -> None:
        raw = "".join(self._buffer)
        self._buffer.clear()
        body = raw.strip("\n")
        if not body.strip():
            return
        lines = [line.rstrip() for line in body.split("\n")]
        block = "```\n" + "\n".join(lines) + "\n```"
        if self._tables:
            self._tables[-1].add_text("\n".join(lines))
        else:
            self.blocks.append(block)

    def _push_list(self, ordered: bool, start: int) -> None:
        self._lists.append({"ordered": ordered, "index": start, "lines": 0, "open": False})

    # --- обработчики html.parser ---

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth += 1
            return
        values = {name.lower(): (value or "") for name, value in attrs}
        if tag in _SKIP_TAGS:
            self._close_block()
            self._skip_depth = 1
            return
        if tag == "title":
            self._close_block()
            self._in_title = True
            return
        if tag == "br":
            self._buffer.append("\n")
            return
        if tag == "hr":
            self._close_block()
            return
        if tag == "img":
            self.images += 1
            alt = _clean_line(values.get("alt") or values.get("title") or "")
            if alt:
                self._buffer.append(f" ![{alt}]() ")
            return
        if tag in _HEADING_TAGS:
            self._close_block()
            self._heading = _HEADING_TAGS[tag]
            return
        if tag == "table":
            self._close_block()
            self._tables.append(_TableBuilder())
            return
        if tag == "tr":
            self._close_block()
            if self._tables:
                self._tables[-1].start_row()
            return
        if tag in ("td", "th"):
            self._close_block()
            if self._tables:
                self._tables[-1].start_cell(_int(values.get("colspan"), 1))
            return
        if tag in _LIST_CONTAINERS:
            self._close_block()
            self._push_list(tag == "ol", _int(values.get("start"), 1))
            return
        if tag in _LIST_ITEMS:
            self._close_block()
            if not self._lists:
                self._push_list(False, 1)
            self._lists[-1]["open"] = True
            self._lists[-1]["lines"] = 0
            return
        if tag == "pre":
            self._close_block()
            self._pre_depth += 1
            return
        if tag == "blockquote":
            self._close_block()
            self._quote_depth += 1
            return
        if tag == "a":
            self._links.append(((values.get("href") or "").strip(), len(self._buffer)))
            return
        if tag in _BLOCK_TAGS:
            self._close_block()

    def handle_startendtag(self, tag: str, attrs: Sequence[Tuple[str, str | None]]) -> None:
        # Одиночный тег (<br/>, <img/>): закрывать нечего.
        self.handle_starttag(tag, attrs)
        if tag.lower() in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in _SKIP_TAGS:
                self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _HEADING_TAGS:
            self._close_block()
            self._heading = 0
            return
        if tag == "table":
            self._close_block()
            if self._tables:
                self._finish_table(self._tables.pop())
            return
        if tag == "tr":
            self._close_block()
            if self._tables:
                self._tables[-1].end_row()
            return
        if tag in ("td", "th"):
            self._close_block()
            if self._tables:
                self._tables[-1].end_cell()
            return
        if tag in _LIST_CONTAINERS:
            self._close_block()
            if self._lists:
                self._lists.pop()
            return
        if tag in _LIST_ITEMS:
            self._close_block()
            if self._lists:
                self._lists[-1]["open"] = False
            return
        if tag == "pre":
            self._flush_pre()
            self._pre_depth = max(0, self._pre_depth - 1)
            return
        if tag == "blockquote":
            self._close_block()
            self._quote_depth = max(0, self._quote_depth - 1)
            return
        if tag == "a":
            self._close_link()
            return
        if tag in _BLOCK_TAGS:
            self._close_block()

    def _close_link(self) -> None:
        if not self._links:
            return
        href, start = self._links.pop()
        text = "".join(self._buffer[start:]).strip()
        if href and text and useful_href(href) and href not in text:
            self._buffer.append(f" ({href})")

    def _finish_table(self, table: _TableBuilder) -> None:
        rendered = table.render()
        if not rendered:
            return
        if self._tables:
            self._tables[-1].add_text(flatten_table(rendered))
        else:
            self.blocks.append(rendered)

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._pre_depth:
            self._buffer.append(data)
            return
        self._buffer.append(re.sub(r"\s+", " ", data))

    # --- результат ---

    def document_title(self) -> str:
        return _clean_line(" ".join(self._title_parts))

    def markdown(self) -> str:
        if self._pre_depth:
            self._flush_pre()
            self._pre_depth = 0
        self._close_block()
        while self._tables:
            self._finish_table(self._tables.pop())
        return merge_list_blocks(self.blocks)


def html_to_markdown(source: str) -> HtmlText:
    """HTML → Markdown. Не бросает исключений: что разобралось, то и вернём."""
    parser = _HtmlToMarkdown()
    try:
        parser.feed(source)
    except Exception:  # noqa: BLE001 — html.parser спотыкается на экзотическом мусоре
        pass
    try:
        parser.close()
    except Exception:  # noqa: BLE001
        pass
    text = parser.markdown()
    return HtmlText(text=text, title=parser.document_title() or first_heading(text),
                    images=parser.images)


def declared_encoding(raw: bytes) -> str | None:
    """Кодировка, объявленная в самом файле: meta charset или заголовок XML."""
    head = raw[:4096]
    for pattern in (_META_CHARSET_RE, _XML_ENCODING_RE):
        match = pattern.search(head)
        if not match:
            continue
        name = match.group(1).decode("ascii", errors="ignore").strip().strip("\"'")
        if name:
            return name
    return None


def decode_markup(raw: bytes, path: Path | None = None) -> Tuple[str, str, str | None]:
    """Байты разметки → текст. Возвращает (текст, кодировка, предупреждение).

    Порядок такой: BOM, объявленная в файле кодировка, затем перебор из
    :mod:`reportgen.ingest.convert` (utf-8, cp1251, koi8-r) — в контуре лежат и
    страницы, сохранённые Internet Explorer в cp1251.
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace"), "utf-8-sig", None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace"), "utf-16", None
    name = declared_encoding(raw)
    if name:
        try:
            return raw.decode(name), name.lower(), None
        except (UnicodeDecodeError, LookupError):
            pass
    if path is not None:
        text, encoding, problem = _read_text(path)
        return text, encoding or "utf-8", problem
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding), encoding, None
        except UnicodeDecodeError:
            continue
    return (
        raw.decode("utf-8", errors="replace"),
        "utf-8/replace",
        "кодировка не распознана, часть символов заменена",
    )


def _read_bytes(path: Path, result: ConvertedDocument) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError as error:
        result.warnings.append(f"файл не прочитан: {_reason(error)}")
        return None


def convert_html(path: Path) -> ConvertedDocument:
    """HTML/XHTML → Markdown: заголовки, таблицы, списки, ссылки с адресами."""
    result = ConvertedDocument(title=path.stem, meta={"source_format": "html"})
    raw = _read_bytes(path, result)
    if raw is None:
        return result
    if not raw.strip():
        result.warnings.append("файл пуст — текста в нём нет")
        return result

    text, encoding, problem = decode_markup(raw, path)
    result.meta["encoding"] = encoding
    if problem:
        result.warnings.append(problem)

    parsed = html_to_markdown(text)
    result.text = parsed.text
    result.title = parsed.title or path.stem
    if parsed.images:
        result.meta["images"] = parsed.images
    if result.is_empty:
        result.warnings.append(
            "в HTML не найдено текста: страница либо пустая, либо собирается "
            "скриптами на стороне браузера — такую надо сохранять как PDF"
        )
    return result


# ----------------------------------------------------------------- EML ----

#: Заголовки письма, которые попадают в шапку документа.
_MAIL_HEADERS = (
    ("Subject", "Тема"),
    ("From", "От"),
    ("To", "Кому"),
    ("Cc", "Копия"),
    ("Date", "Дата"),
)

_META_HEADERS = {"From": "email_from", "To": "email_to", "Date": "email_date"}


def _header_value(message: Any, name: str) -> str:
    """Значение заголовка письма в читаемом виде (RFC 2047 уже раскодирован)."""
    try:
        raw = message.get(name)
    except Exception:  # noqa: BLE001 — заголовок с дефектом не должен ронять разбор
        return ""
    if raw is None:
        return ""
    try:
        text = str(raw)
    except Exception:  # noqa: BLE001
        return ""
    if "=?" in text:  # письмо разобрано в режиме совместимости, декодируем сами
        try:
            from email.header import decode_header, make_header  # noqa: PLC0415

            text = str(make_header(decode_header(text)))
        except Exception:  # noqa: BLE001
            pass
    return _clean_line(text.replace("\n", " "))


def _part_text(part: Any) -> str:
    """Текст одной части письма с учётом её кодировки."""
    try:
        content = part.get_content()
        if isinstance(content, str):
            return content
    except Exception:  # noqa: BLE001 — неизвестная кодировка, битый base64
        pass
    try:
        payload = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        payload = None
    if not payload:
        return ""
    charset = part.get_content_charset()
    if charset:
        try:
            return payload.decode(charset)
        except (UnicodeDecodeError, LookupError):
            pass
    return decode_markup(payload)[0]


def _mail_parts(message: Any) -> Tuple[Any | None, Any | None, List[Any]]:
    """Разбирает письмо на текстовую часть, HTML-часть и вложения."""
    plain: Any | None = None
    html: Any | None = None
    attachments: List[Any] = []
    try:
        parts = list(message.walk())
    except Exception:  # noqa: BLE001 — письмо с дефектной структурой
        parts = [message]
    for part in parts:
        try:
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get_content_disposition() or "").lower()
            filename = part.get_filename()
            content_type = part.get_content_type()
        except Exception:  # noqa: BLE001
            continue
        if disposition == "attachment" or (filename and content_type not in
                                           ("text/plain", "text/html")):
            attachments.append(part)
            continue
        if content_type == "text/plain" and plain is None:
            plain = part
        elif content_type == "text/html" and html is None:
            html = part
        elif filename:
            attachments.append(part)
    return plain, html, attachments


def _attachment_lines(attachments: Sequence[Any]) -> List[str]:
    lines: List[str] = []
    for number, part in enumerate(attachments, start=1):
        try:
            name = part.get_filename() or f"вложение {number}"
            content_type = part.get_content_type()
            payload = part.get_payload(decode=True) or b""
            size = len(payload)
        except Exception:  # noqa: BLE001 — битое вложение всё равно надо перечислить
            name, content_type, size = f"вложение {number}", "неизвестно", 0
        name = _clean_line(str(name))
        size_text = human_size(size) if size else "размер не определён"
        lines.append(f"- {name} — {size_text}, {content_type}")
    return lines


def convert_eml(path: Path) -> ConvertedDocument:
    """Письмо ``.eml`` → Markdown: шапка, тело, перечень вложений.

    Сами вложения не разбираются: заказчики шлют дампы и таблицы приложениями,
    и вытаскивать их надо осознанно, отдельным шагом. Но в тексте документа они
    перечислены с именами и размерами — иначе инженер не узнает, что дамп был.
    """
    result = ConvertedDocument(title=path.stem, meta={"source_format": "eml"})
    raw = _read_bytes(path, result)
    if raw is None:
        return result
    if not raw.strip():
        result.warnings.append("файл пуст — письма в нём нет")
        return result

    import email  # noqa: PLC0415 — стандартная библиотека, но импорт небыстрый
    import email.policy  # noqa: PLC0415

    try:
        message = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:  # noqa: BLE001 — строгий разбор споткнулся, пробуем старый
        try:
            message = email.message_from_bytes(raw)
        except Exception as error:  # noqa: BLE001
            result.warnings.append(f"письмо не разобрано: {_reason(error)}")
            return result

    subject = _header_value(message, "Subject")
    header_lines: List[str] = []
    for name, label in _MAIL_HEADERS:
        value = _header_value(message, name)
        if value:
            header_lines.append(f"- **{label}:** {value}")
            if name in _META_HEADERS:
                result.meta[_META_HEADERS[name]] = value

    sender = _header_value(message, "From")
    if sender:
        result.meta["author"] = sender

    plain, html, attachments = _mail_parts(message)
    body = ""
    if plain is not None:
        raw_body = _part_text(plain).replace("\r\n", "\n")
        body = "\n\n".join(
            part.strip() for part in re.split(r"\n\s*\n", raw_body) if part.strip()
        )
        result.meta["body_format"] = "text"
    elif html is not None:
        parsed = html_to_markdown(_part_text(html))
        body = parsed.text
        result.meta["body_format"] = "html"
        if parsed.images:
            result.meta["images"] = parsed.images
    else:
        result.warnings.append("в письме нет текстовой части — только вложения или разметка")

    pieces: List[str] = [f"# {subject or 'Письмо без темы'}"]
    if header_lines:
        pieces.append("\n".join(header_lines))
    if body.strip():
        pieces.append("## Текст письма")
        pieces.append(body.strip())
    if attachments:
        result.meta["attachments"] = len(attachments)
        pieces.append("## Вложения")
        pieces.extend(_attachment_lines(attachments))
        result.warnings.append(
            f"во вложениях файлов: {len(attachments)} — их содержимое не разбирается, "
            "в тексте только перечень; нужные приложите в библиотеку отдельными файлами"
        )

    result.text = merge_list_blocks(pieces)
    result.title = subject or path.stem
    if not body.strip() and not attachments:
        result.warnings.append("письмо пустое: ни текста, ни вложений")
    return result


# ----------------------------------------------------------------- XML ----

def _xml_local(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _xml_attributes(element: Any) -> str:
    parts: List[str] = []
    for name, value in list(element.attrib.items())[:6]:
        cleaned = _clean_line(str(value))
        if not cleaned:
            continue
        if len(cleaned) > 60:
            cleaned = cleaned[:57] + "…"
        parts.append(f"{_xml_local(name)}={cleaned}")
    return ", ".join(parts)


def _xml_blocks(element: Any, depth: int, blocks: List[str]) -> None:
    """Дерево XML → Markdown: вложенность тегов становится вложенностью заголовков."""
    if len(blocks) >= _MAX_XML_BLOCKS:
        return
    name = _xml_local(element.tag)
    if not name:  # комментарий или инструкция обработки
        return
    attributes = _xml_attributes(element)
    label = f"{name} ({attributes})" if attributes else name
    text = _clean_line(element.text or "")
    children = [child for child in element if isinstance(child.tag, str)]
    if not children:
        if text:
            blocks.append(f"- **{label}:** {text}")
        elif attributes:
            blocks.append(f"- **{label}**")
        return
    blocks.append(f"{'#' * min(max(depth, 1), 6)} {label}")
    if text:
        blocks.append(text)
    for child in children:
        _xml_blocks(child, depth + 1, blocks)
        tail = _clean_line(child.tail or "")
        if tail and len(blocks) < _MAX_XML_BLOCKS:
            blocks.append(tail)


def convert_xml(path: Path) -> ConvertedDocument:
    """Произвольный XML → Markdown.

    FB2 и сохранённый XHTML определяются по корневому элементу и уходят своим
    конвертерам; всё остальное выводится как дерево: теги — заголовками,
    значения — списком «поле: значение». Это не красиво, но выгрузка из системы
    мониторинга в таком виде хотя бы ищется.
    """
    result = ConvertedDocument(title=path.stem, meta={"source_format": "xml"})
    raw = _read_bytes(path, result)
    if raw is None:
        return result
    if not raw.strip():
        result.warnings.append("файл пуст — разбирать нечего")
        return result

    import xml.etree.ElementTree as ET  # noqa: PLC0415

    root = None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        head = raw[:1024].lower()
        if b"<html" in head or b"<!doctype html" in head:
            return convert_html(path)
        result.warnings.append(f"XML не разобран: {_reason(error)}")
        return result
    except (ValueError, MemoryError, RecursionError) as error:
        result.warnings.append(f"XML не разобран: {_reason(error)}")
        return result

    local = _xml_local(root.tag).lower()
    if local == "fictionbook":
        from .opendoc import convert_fb2  # noqa: PLC0415 — взаимная ссылка модулей форматов

        return convert_fb2(path)
    if local == "html":
        return convert_html(path)

    blocks: List[str] = []
    try:
        _xml_blocks(root, 1, blocks)
    except (RecursionError, MemoryError, ValueError) as error:
        result.warnings.append(f"XML разобран не полностью: {_reason(error)}")
    if len(blocks) >= _MAX_XML_BLOCKS:
        result.warnings.append(
            f"в XML больше {_MAX_XML_BLOCKS} узлов — выведено начало файла, "
            "остальное пропущено; такие выгрузки лучше подавать сводкой"
        )
    result.text = merge_list_blocks(blocks)
    result.title = first_heading(result.text) or path.stem
    result.meta["root"] = local
    if result.is_empty:
        result.warnings.append("в XML нет текстовых узлов — только структура")
    return result


# -------------------------------------------------------- регистрация ----

registry.register(registry.ConverterSpec(
    name="html",
    suffixes=(".html", ".htm", ".xhtml"),
    convert=convert_html,
    requires=(),
    note="HTML и XHTML: заголовки, таблицы, списки, ссылки; только стандартная библиотека",
))

registry.register(registry.ConverterSpec(
    name="eml",
    suffixes=(".eml",),
    convert=convert_eml,
    requires=(),
    note="письмо: шапка (тема, от, кому, дата), тело, перечень вложений",
))

registry.register(registry.ConverterSpec(
    name="xml",
    suffixes=(".xml",),
    convert=convert_xml,
    requires=(),
    note="произвольный XML деревом; FB2 и XHTML передаются своим конвертерам",
))
