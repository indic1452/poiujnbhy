"""Извлечение и нормализация чисел.

Выделено в отдельный модуль, потому что этим пользуются и факт-пакет, и
верификатор, и они обязаны понимать числа одинаково: «13,7», «13.70» и
«13.7» — одно и то же значение, а «137» — уже другое.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Set

# Число с необязательным знаком, разделителем разрядов (пробел/NBSP),
# десятичной запятой или точкой и экспонентой.
_NUMBER_RE = re.compile(
    r"[-+]?\d[\d   ]*(?:[.,]\d+)?(?:[eE][-+]?\d+)?"
)

# Структурная разметка, числа из которой не являются утверждениями о фактах.
_STRUCTURAL_RE = [
    re.compile(r"^#{1,6}\s*[\d.]+", re.MULTILINE),      # "## 3.1. Заголовок"
    re.compile(r"^\s*\d+[.)]\s", re.MULTILINE),          # нумерованные списки
    re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE),   # разделители таблиц
    re.compile(r"\[S\d+\]"),                             # маркеры источников
    re.compile(r"\bрис\.\s*\d+|\bтабл\.\s*\d+", re.IGNORECASE),
]


def strip_structural(text: str) -> str:
    """Убирает разметку, числа в которой не несут фактического смысла."""
    for pattern in _STRUCTURAL_RE:
        text = pattern.sub(" ", text)
    return text


def normalize(raw: str) -> str | None:
    """Приводит запись числа к канонической форме или возвращает None."""
    cleaned = raw.strip().replace(" ", "").replace(" ", "")
    cleaned = cleaned.replace(" ", "").replace(",", ".")
    if cleaned in {"", "+", "-", "."}:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    # normalize() даёт 1E+2 для 100 — приводим к обычной записи.
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def extract(text: str, *, structural: bool = False) -> Set[str]:
    """Возвращает множество нормализованных чисел, встреченных в тексте.

    :param structural: если False (по умолчанию), числа из разметки
        (нумерация разделов, списки, ссылки на рисунки) игнорируются.
    """
    if not structural:
        text = strip_structural(text)
    found: Set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        normalized = normalize(match.group(0))
        if normalized is not None:
            found.add(normalized)
    return found


def extract_from_object(obj: object) -> Set[str]:
    """Рекурсивно собирает числа из произвольной JSON-подобной структуры."""
    found: Set[str] = set()
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, (int, float, Decimal)):
            normalized = normalize(str(item))
            if normalized is not None:
                found.add(normalized)
        elif isinstance(item, str):
            found |= extract(item, structural=True)
    return found


def derived_forms(values: Iterable[str]) -> Set[str]:
    """Формы записи, которые инженер сочтёт тем же числом.

    Модель законно может написать «13,7 дБ» как «13.70 дБ» или «-3» как «3»
    в обороте «затухание 3 дБ». Считаем допустимыми модуль числа и
    целую часть, если дробная нулевая.
    """
    extra: Set[str] = set()
    for value in values:
        if value.startswith("-"):
            extra.add(value[1:])
        if "." in value:
            whole, _, frac = value.partition(".")
            if set(frac) == {"0"}:
                extra.add(whole)
    return extra
