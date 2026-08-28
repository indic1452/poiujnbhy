"""RFC: заголовок документа, структура разделов, отменённые редакции.

Почему для RFC отдельный разборщик, хотя это обычный текст.

RFC — главный источник ответа на вопрос «какие поля в этом кадре и что в них
лежит». Инженер, разбирающий дамп мультиплексора или необъяснимое поле в
заголовке, идёт именно туда. Но как простой ``.txt`` такой файл попадает в
базу плохо:

* **Название** становится ``rfc791`` — по такому в списке библиотеки ничего не
  найти, а в отчёте это выглядит как ссылка в никуда. Настоящее название лежит
  в шапке, отдельной строкой по центру.
* **Год** не определяется: в шапке он записан как ``September 1981``, а
  общий определитель дат ищет четыре цифры подряд рядом с русскими словами.
* **Отменённые редакции цитируются как действующие.** В шапке написано
  ``Obsoletes: 2616`` — то есть этот RFC отменяет тот. Обратной пометки в
  старом файле нет, и без разбора шапки система будет с равной охотой
  ссылаться и на RFC 2616, и на заменивший его 7230. Для отчёта это
  прямая ошибка.
* **Разделы теряются.** Нумерованные заголовки (``6.  Response Status Codes``)
  без разметки не отличаются от текста, и фрагмент приходит без указания, из
  какого раздела он взят.

Разборщик достаёт шапку, размечает разделы и переносит колонтитулы страниц в
маркеры страниц — чтобы ссылка «RFC 7230, с. 34» вела туда, куда надо.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from .. import registry
from ..convert import ConvertedDocument, page_marker, read_text

__all__ = [
    "RFC_SUFFIXES",
    "convert_rfc",
    "is_rfc_text",
    "parse_header",
]

#: Расширения, на которых имеет смысл искать RFC. Сам файл опознаётся по шапке.
RFC_SUFFIXES: Tuple[str, ...] = (".txt",)

#: Сколько строк начала файла считать шапкой.
HEADER_LINES = 60

#: Месяцы в шапке RFC записаны по-английски.
_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

_RFC_NUMBER_RE = re.compile(r"^Request for Comments:\s*(\d+)", re.MULTILINE)
_OBSOLETES_RE = re.compile(r"^Obsoletes:\s*([\d,\s]+)", re.MULTILINE)
_OBSOLETED_BY_RE = re.compile(r"^Obsoleted by:\s*([\d,\s]+)", re.MULTILINE)
_UPDATES_RE = re.compile(r"^Updates:\s*([\d,\s]+)", re.MULTILINE)
_UPDATED_BY_RE = re.compile(r"^Updated by:\s*([\d,\s]+)", re.MULTILINE)
_CATEGORY_RE = re.compile(r"^Category:\s*(.+)$", re.MULTILINE)
_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d{2})\b", re.IGNORECASE
)
#: Разрыв страницы: «[Page 12]» в конце и подвал следующей страницы.
_PAGE_BREAK_RE = re.compile(r"\[Page\s+(\d+)\]\s*\f?", re.IGNORECASE)
#: Нумерованный заголовок раздела: «6.  Response Status Codes», «6.1. …».
_SECTION_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s{1,6}(\S.{0,90})$")
#: Служебные строки шапки, которые не являются названием документа.
_HEADER_FIELDS = (
    "request for comments", "obsoletes", "obsoleted by", "updates", "updated by",
    "category", "issn", "isbn", "bcp", "std", "fyi", "errata", "network working group",
    "internet engineering task force", "internet architecture board", "independent submission",
)


def is_rfc_text(text: str) -> bool:
    """Похож ли текст на RFC. Опознаём по шапке, а не по имени файла."""
    head = "\n".join(text.splitlines()[:HEADER_LINES])
    return bool(_RFC_NUMBER_RE.search(head))


def _numbers(raw: str | None) -> List[int]:
    if not raw:
        return []
    return [int(item) for item in re.findall(r"\d+", raw)]


def parse_header(text: str) -> Dict[str, object]:
    """Разобрать шапку RFC: номер, название, дата, статус, связи с другими."""
    lines = text.splitlines()
    head = "\n".join(lines[:HEADER_LINES])
    data: Dict[str, object] = {}

    number = _RFC_NUMBER_RE.search(head)
    if number:
        data["rfc"] = int(number.group(1))

    date = _DATE_RE.search(head)
    if date:
        data["year"] = int(date.group(2))
        data["month"] = date.group(1).capitalize()

    category = _CATEGORY_RE.search(head)
    if category:
        data["category"] = category.group(1).strip()

    for key, pattern in (
        ("obsoletes", _OBSOLETES_RE),
        ("obsoleted_by", _OBSOLETED_BY_RE),
        ("updates", _UPDATES_RE),
        ("updated_by", _UPDATED_BY_RE),
    ):
        found = pattern.search(head)
        if found:
            values = _numbers(found.group(1))
            if values:
                data[key] = values

    data["title"] = _extract_title(lines)
    return data


def _extract_title(lines: List[str]) -> str:
    """Название RFC — центрированная строка под шапкой.

    Шапка идёт двумя колонками (слева служебные поля, справа автор и дата),
    затем пустая строка, затем название по центру — иногда в несколько строк.
    """
    started = False
    collected: List[str] = []
    for raw in lines[:HEADER_LINES]:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if collected:
                break
            started = True
            continue
        low = stripped.lower()
        if any(low.startswith(field) for field in _HEADER_FIELDS):
            continue
        if not started:
            continue
        # Название центрировано, то есть с отступом слева. Отступ бывает
        # маленьким: длинное название в 72 колонках центруется почти вплотную
        # к краю. А вот «Abstract», «Status of This Memo» и номера разделов
        # всегда прижаты к нулевой колонке — их и отсекаем.
        indent = len(line) - len(line.lstrip())
        if indent < 2:
            if collected:
                break
            continue
        collected.append(stripped)
        if len(collected) >= 3:
            break
    return " ".join(collected).strip()


def _strip_page_furniture(text: str) -> Tuple[str, int]:
    """Убрать колонтитулы страниц, расставив маркеры. Возвращает (текст, страниц)."""
    out: List[str] = []
    page = 1
    out.append(page_marker(page))
    out.append("")
    skip_next_header = False
    for line in text.splitlines():
        if skip_next_header:
            # После разрыва идёт подвал: пустые строки и строка колонтитула.
            if not line.strip():
                continue
            skip_next_header = False
            # Колонтитул новой страницы: «Fielding & Reschke  Standards Track  [Page 2]»
            # уже съеден, а эта строка — шапка вида «RFC 7230  HTTP/1.1  June 2014».
            if line.lstrip().startswith("RFC "):
                continue
        found = _PAGE_BREAK_RE.search(line)
        if found:
            page = int(found.group(1)) + 1
            out.append("")
            out.append(page_marker(page))
            out.append("")
            skip_next_header = True
            continue
        out.append(line.rstrip())
    return "\n".join(out), page


def _mark_sections(text: str) -> str:
    """Нумерованные заголовки разделов → заголовки Markdown."""
    out: List[str] = []
    for line in text.splitlines():
        found = _SECTION_RE.match(line)
        if found and not line.startswith(" "):
            depth = min(4, found.group(1).count(".") + 2)
            out.append("#" * depth + f" {found.group(1)}. {found.group(2).strip()}")
        else:
            out.append(line)
    return "\n".join(out)


def convert_rfc(path: Path) -> ConvertedDocument:
    """RFC-файл → Markdown с шапкой в метаданных и размеченными разделами.

    Конвертер объявлен на весь ``.txt`` с высоким приоритетом, потому что RFC
    не отличить по расширению: у него обычное имя и обычный текст. Файл, у
    которого нет шапки RFC, отдаётся обычному текстовому разборщику — так
    заметка инженера не превратится в «RFC 0».
    """
    raw, encoding, note = read_text(path)
    if not is_rfc_text(raw):
        from ..convert import _convert_text  # noqa: PLC0415 — обычный текст

        return _convert_text(path)

    result = ConvertedDocument(title=path.stem, meta={"source_format": "rfc"})
    if encoding:
        result.meta["encoding"] = encoding
    if note:
        result.warnings.append(note)

    header = parse_header(raw)
    body, pages = _strip_page_furniture(raw)
    result.text = _mark_sections(body)
    result.page_count = pages
    result.meta["page_count"] = pages

    number = header.get("rfc")
    title = str(header.get("title") or "").strip()
    if number:
        result.meta["rfc"] = number
        result.title = f"RFC {number}. {title}" if title else f"RFC {number}"
    elif title:
        result.title = title

    for key in ("year", "month", "category", "obsoletes", "obsoleted_by",
                "updates", "updated_by"):
        if key in header:
            result.meta[key] = header[key]

    # Отменённый RFC не должен цитироваться как действующий: помечаем прямо
    # при приёме, чтобы поиск его не выдавал (см. состояние документа).
    if header.get("obsoleted_by"):
        replacements = ", ".join(f"RFC {item}" for item in header["obsoleted_by"])
        result.meta["status"] = "superseded"
        result.meta["superseded_by"] = f"standards/rfc/rfc{header['obsoleted_by'][0]}"
        result.warnings.append(
            f"документ отменён: заменён на {replacements} — помечен как заменённый "
            "и в поиск не попадёт"
        )
    if header.get("obsoletes"):
        result.meta["obsoletes_note"] = ", ".join(
            f"RFC {item}" for item in header["obsoletes"]
        )
    if not result.text.strip():
        result.warnings.append("файл пуст")
    return result


registry.register(registry.ConverterSpec(
    name="rfc",
    suffixes=RFC_SUFFIXES,
    convert=convert_rfc,
    priority=20,
    note="RFC: название и год из шапки, разделы, отменённые редакции исключаются из поиска",
))
