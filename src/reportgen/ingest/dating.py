"""Год издания документа: откуда его взять и зачем.

В библиотеке рядом лежат редакции одного и того же: ГОСТ 2009 года и он же
2024-го, методичка две тысячи одиннадцатого и её переиздание. Поиск, который
не знает про даты, с равной охотой процитирует любую — и в отчёте
появится ссылка на норму, отменённую пятнадцать лет назад.

Год берётся из четырёх источников, по убыванию надёжности:

1. **Явная дата в метаданных файла** — PDF (``creationDate``), DOCX
   (``created``). Её ставит программа, а не человек, и она почти всегда верна
   для года выпуска документа.
2. **Номер стандарта** — ``ГОСТ Р 53363-2009``, ``ITU-T G.826 (02/99)``.
   Для нормативов это и есть год редакции.
3. **Год в имени файла или заголовке** — ``Методика 2018.pdf``.
4. **Год в первых строках текста** — титульный лист книги.

Год, которого не может быть (раньше 1950 или позже следующего календарного),
отбрасывается: в сканах регулярно попадаются номера страниц и артефакты
распознавания, похожие на даты.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, Tuple

#: Раньше этого года технической документации по связи, которая нужна в работе,
#: практически не бывает, а «1901» в скане — почти всегда мусор распознавания.
MIN_YEAR = 1950

#: Сколько строк начала текста считать титульным листом.
TITLE_LINES = 40

#: ГОСТ Р 53363-2009, ГОСТ 26.011-80, ОСТ 45.159-2000.
_STANDARD_RE = re.compile(r"\b(?:ГОСТ|ОСТ|СТО|СП|РД|ТУ)[^\n]{0,40}?[-–—](\d{2,4})\b", re.IGNORECASE)

#: ITU-T G.826 (02/99), Rec. G.703 (11/2001).
_ITU_RE = re.compile(r"\(\s*\d{1,2}\s*/\s*(\d{2,4})\s*\)")

#: «2018 г.», «издание 2011 года», «© 2020».
_YEAR_RE = re.compile(r"(?:^|[^\d])((?:19|20)\d{2})(?:[^\d]|$)")

#: Даты из метаданных: 2019-04-11T10:00:00, D:20190411100000.
_META_DATE_RE = re.compile(r"(?:D:)?((?:19|20)\d{2})[-:]?\d{2}")


def _plausible(year: int) -> bool:
    limit = datetime.date.today().year + 1
    return MIN_YEAR <= year <= limit


def _expand(raw: str) -> int | None:
    """«99» → 1999, «09» → 2009, «2009» → 2009."""
    value = int(raw)
    if value >= 1000:
        return value if _plausible(value) else None
    # Двузначный год: 80 → 1980, 09 → 2009. Граница по текущему веку.
    century_break = datetime.date.today().year % 100
    value = 2000 + value if value <= century_break + 1 else 1900 + value
    return value if _plausible(value) else None


def year_from_metadata(meta: Dict[str, Any]) -> int | None:
    """Год из метаданных файла: их ставит программа, а не человек."""
    for key in ("created", "creationDate", "creation_date", "modified", "modDate"):
        raw = meta.get(key)
        if not raw:
            continue
        found = _META_DATE_RE.search(str(raw))
        if found:
            year = _expand(found.group(1))
            if year:
                return year
    return None


def year_from_standard(text: str) -> int | None:
    """Год из номера стандарта — для нормативов это и есть год редакции."""
    for match in _STANDARD_RE.finditer(text):
        year = _expand(match.group(1))
        if year:
            return year
    for match in _ITU_RE.finditer(text):
        year = _expand(match.group(1))
        if year:
            return year
    return None


def year_from_text(text: str) -> int | None:
    """Самый поздний правдоподобный год из фрагмента текста.

    Берём именно поздний: на титульном листе рядом стоят год издания и годы
    ссылок на более старые работы.
    """
    years = [_expand(m.group(1)) for m in _YEAR_RE.finditer(text)]
    years = [year for year in years if year]
    return max(years) if years else None


def detect_year(*, title: str = "", filename: str = "", text: str = "",
                meta: Dict[str, Any] | None = None) -> Tuple[int | None, str]:
    """Год издания и то, откуда он взят.

    Возвращает ``(год, источник)``; источник попадает в карточку документа,
    чтобы инженер видел, чему верить: ``metadata``, ``standard``, ``title``
    или ``text``.
    """
    meta = meta or {}

    year = year_from_standard(f"{title}\n{filename}")
    if year:
        return year, "standard"

    year = year_from_metadata(meta)
    if year:
        return year, "metadata"

    year = year_from_text(f"{title}\n{filename}")
    if year:
        return year, "title"

    head = "\n".join(str(text).splitlines()[:TITLE_LINES])
    year = year_from_standard(head)
    if year:
        return year, "standard"
    year = year_from_text(head)
    if year:
        return year, "text"

    return None, ""
