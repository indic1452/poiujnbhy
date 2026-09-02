"""Извлечение и нормализация чисел.

Выделено в отдельный модуль, потому что этим пользуются и факт-пакет, и
верификатор, и они обязаны понимать числа одинаково: «13,7», «13.70» и
«13.7» — одно и то же значение, а «137» — уже другое.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Set

# Число с необязательным знаком, десятичной запятой или точкой и экспонентой.
#
# Пробел разделяет разряды только тогда, когда все группы после первой — по
# три цифры: «1 000 000» и «2 400» это одно число, а «12 34» — два. Раньше
# склеивалось всё подряд, и «значения 12 34» превращались в 1234: верификатор
# блокировал отчёт из-за числа, которого никто не писал, а настоящие 12 и 34
# при этом не проверял вовсе.
#
# Знак пишется только там, где перед ним нет буквы или цифры. Иначе дефис в
# «КАМ-16», «ФМ-4С», «MPEG-2» и в диапазоне «17919-18737» читался как минус:
# «КАМ-16» давал −16, и отчёт с диапазоном частот блокировался из-за числа
# −26000, которого в факт-пакете, разумеется, нет. Настоящий минус («смещение
# −1,9 кГц») стоит после пробела или скобки и распознаётся по-прежнему.
_SPACES = "\u0020\u00a0\u202f\u2007"
_NUMBER_RE = re.compile(
    rf"(?:(?<!\w)[-+])?(?:\d{{1,3}}(?:[{_SPACES}]\d{{3}})+|\d+)"
    rf"(?:[.,]\d+)?(?:[eE][-+]?\d+)?"
)

# Параметры кодов и скремблеров отдел пишет в скобках через запятую:
# «RS (204,188,12)», «LDPC (16128,11856)», «АС (21,19)», шаблон «АС(9,5).sid».
# Запятая здесь разделяет параметры, а не отделяет дробную часть. Общее
# правило читало «204,188,12» как 204.188 и 12 — ни длины кодового слова, ни
# числа информационных символов верификатор не видел вовсе. Хуже того, разбор
# зависел от пробела: «LDPC (16128,11856)» давало 16128.11856, а «LDPC (16128,
# 11856)» — 16128 и 11856, и один и тот же код, переписанный из описи с
# пробелом, блокировал отчёт числом «11856, которого нет в факт-пакете».
#
# Список опознаём по двум признакам сразу: перед скобкой стоит сокращение (две
# и более заглавные буквы), а внутри — только целые числа и запятые. Дробь
# «(13,7 дБ)» ни тому, ни другому не отвечает и остаётся дробью.
_CODE_PARAMS_RE = re.compile(
    r"[A-ZА-ЯЁ][A-ZА-ЯЁ0-9-]+\s*\((\s*\d+(?:\s*,\s*\d+)+\s*)\)"
)
_INTEGER_RE = re.compile(r"\d+")

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


#: Предел на порядок числа. Ни одно измерение связи и близко такого не
#: требует, а «1e999999» разворачивается в обычную запись длиной в миллион
#: знаков — по такой строке на каждое вхождение в тексте. За пределом
#: сохраняем исходную запись: число остаётся видимым верификатору, а
#: строка — короткой.
MAX_EXPONENT = 100


def normalize(raw: str) -> str | None:
    """Приводит запись числа к канонической форме или возвращает None."""
    cleaned = raw.strip().replace("\u00a0", "").replace("\u202f", "")
    cleaned = cleaned.replace("\u2007", "").replace(" ", "").replace(",", ".")
    if cleaned in {"", "+", "-", "."}:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    # Бесконечность и NaN — не измерения. Из текста отчёта они не приходят
    # (выражение выше берёт только цифры), но факт-пакет правят и руками.
    if not value.is_finite():
        return None
    # Порядок за пределами разумного разворачивать нельзя: decimal.Overflow
    # ронял сохранение секции с ошибкой 500, а «1e-999999999» молча
    # становился нулём и мог совпасть с настоящим нулём из фактов.
    if value != 0 and abs(value.adjusted()) > MAX_EXPONENT:
        return cleaned
    # normalize() даёт 1E+2 для 100 — приводим к обычной записи.
    try:
        text = format(value.normalize(), "f")
    except ArithmeticError:
        return cleaned
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _take_code_params(text: str, found: Set[str]) -> str:
    """Забирает параметры кодов «ИМЯ (a,b,…)» и гасит их в тексте.

    Гасим только сами скобки: сокращение перед ними остаётся на месте, иначе
    в «DVB-S2 (…)» пропала бы двойка из названия стандарта.
    """
    def replace(match: "re.Match[str]") -> str:
        for part in _INTEGER_RE.findall(match.group(1)):
            normalized = normalize(part)
            if normalized is not None:
                found.add(normalized)
        head = match.group(0)[: match.start(1) - match.start(0)]
        return head + " " * (len(match.group(0)) - len(head))

    return _CODE_PARAMS_RE.sub(replace, text)


def extract(text: str, *, structural: bool = False) -> Set[str]:
    """Возвращает множество нормализованных чисел, встреченных в тексте.

    :param structural: если False (по умолчанию), числа из разметки
        (нумерация разделов, списки, ссылки на рисунки) игнорируются.
    """
    if not structural:
        text = strip_structural(text)
    found: Set[str] = set()
    text = _take_code_params(text, found)
    for match in _NUMBER_RE.finditer(text):
        normalized = normalize(match.group(0))
        if normalized is not None:
            found.add(normalized)
    return found


def extract_from_object(obj: object, *, skip_keys: Iterable[str] = ()) -> Set[str]:
    """Рекурсивно собирает числа из произвольной JSON-подобной структуры.

    Собираются только ЗНАЧЕНИЯ. Имена полей игнорируются намеренно: иначе поле
    ``sha256`` разрешило бы в отчёте число 256, а ``phase_noise_1khz`` — единицу.
    ``skip_keys`` дополнительно исключает значения служебных полей (хеши,
    контрольные суммы): в них полно цифровых групп, которые ничего не значат,
    но делают верификатор слепым к правдоподобной выдумке.
    """
    skip = {key.casefold() for key in skip_keys}
    found: Set[str] = set()
    stack = [obj]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(
                value for key, value in item.items()
                if str(key).casefold() not in skip
            )
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
