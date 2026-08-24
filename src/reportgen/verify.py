"""Верификатор — слой, который делает систему пригодной для реальной работы.

Проверки блокирующие (``error``) останавливают экспорт отчёта. Именно это
отличает рабочую систему от демонстрации: предупреждение, которое можно
проигнорировать, через месяц игнорируется всегда.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence

from . import numbers
from .facts import FactPack
from .pipeline import Outline

APPENDIX_MARKER = "Приложение А. Источники"
SERVICE_MARKER = "<!-- служебный блок"

DEFAULT_FORBIDDEN = {
    r"гарантиру\w*": "гарантия от лица компании",
    r"обязуемся": "обязательство от лица компании",
    r"вин[аоуы]\b|виноват\w*": "распределение вины между сторонами",
    r"стоимост\w+|цена\b|руб\.": "коммерческие сведения в техническом отчёте",
    r"как известно|очевидно, что": "необоснованное утверждение без источника",
}

LEVELS = ("error", "warning", "info")


@dataclass(frozen=True)
class Issue:
    level: str
    code: str
    message: str
    section: str = ""

    def __str__(self) -> str:
        where = f" [{self.section}]" if self.section else ""
        return f"{self.level.upper():7} {self.code}{where}: {self.message}"


def parse_sections(markdown: str) -> List[tuple[str, str]]:
    """Разбирает отчёт на пары (заголовок, тело) по заголовкам второго уровня."""
    sections: List[tuple[str, str]] = []
    title = ""
    body: List[str] = []
    for line in markdown.splitlines():
        if line.startswith("## "):
            if title or body:
                sections.append((title, "\n".join(body).strip()))
            title = line[3:].strip()
            body = []
        else:
            body.append(line)
    if title or body:
        sections.append((title, "\n".join(body).strip()))
    return sections


def _strip_service(markdown: str) -> str:
    index = markdown.find(SERVICE_MARKER)
    return markdown if index == -1 else markdown[:index]


def verify_report(
    markdown: str,
    facts: FactPack,
    outline: Outline | None = None,
    *,
    glossary: Dict[str, str] | None = None,
    forbidden: Dict[str, str] | None = None,
) -> List[Issue]:
    """Полная проверка готового отчёта. Возвращает список замечаний."""
    issues: List[Issue] = []
    sections = parse_sections(_strip_service(markdown))
    # Титульный блок (без заголовка), оглавление и приложение формирует код
    # сборки, а не модель, — проверять в них нечего.
    body_sections = [
        (title, body)
        for title, body in sections
        if title.strip() and title.strip() != "Содержание" and APPENDIX_MARKER not in title
    ]
    appendix = "\n".join(b for t, b in sections if APPENDIX_MARKER in t)

    issues += _check_numbers(body_sections, facts, appendix)
    issues += _check_citations(body_sections, appendix)
    issues += _check_placeholders(body_sections)
    issues += _check_forbidden(body_sections, forbidden or DEFAULT_FORBIDDEN)
    if glossary:
        issues += _check_glossary(body_sections, glossary)
    if outline is not None:
        issues += _check_structure(body_sections, outline)
    return issues


def _check_numbers(
    sections: Sequence[tuple[str, str]], facts: FactPack, appendix: str
) -> List[Issue]:
    """Главная проверка: ни одного числа мимо факт-пакета."""
    allowed_facts = facts.allowed_numbers()
    from_sources = numbers.extract(appendix, structural=True)
    from_sources |= numbers.derived_forms(from_sources)

    issues: List[Issue] = []
    for title, body in sections:
        for value in sorted(numbers.extract(body)):
            if value in allowed_facts:
                continue
            if value in from_sources:
                issues.append(
                    Issue(
                        "warning",
                        "number-from-source",
                        f"число {value} взято из процитированного источника, а не из "
                        f"измерений — убедитесь, что оно не выдаётся за результат замера",
                        title,
                    )
                )
                continue
            issues.append(
                Issue(
                    "error",
                    "unknown-number",
                    f"число {value} отсутствует в факт-пакете",
                    title,
                )
            )
    return issues


def _check_citations(sections: Sequence[tuple[str, str]], appendix: str) -> List[Issue]:
    known = set(re.findall(r"\[(S\d+)\]", appendix))
    issues: List[Issue] = []
    for title, body in sections:
        for label in sorted(set(re.findall(r"\[(S\d+)\]", body))):
            if label not in known:
                issues.append(
                    Issue(
                        "error",
                        "unknown-citation",
                        f"ссылка [{label}] отсутствует в приложении с источниками",
                        title,
                    )
                )
    return issues


def _check_placeholders(sections: Sequence[tuple[str, str]]) -> List[Issue]:
    issues: List[Issue] = []
    for title, body in sections:
        for match in re.finditer(r"\[ТРЕБУЕТ ПРОВЕРКИ[^\]]*\]", body):
            issues.append(
                Issue("warning", "needs-review", f"требуется участие инженера: {match.group(0)}", title)
            )
        for match in re.finditer(r"\{\{[^}]+\}\}|TODO|XXX|<[^>\s]*вставить[^>]*>", body, re.IGNORECASE):
            issues.append(
                Issue("error", "placeholder-left", f"в тексте остался плейсхолдер: {match.group(0)}", title)
            )
    return issues


def _check_forbidden(sections: Sequence[tuple[str, str]], forbidden: Dict[str, str]) -> List[Issue]:
    issues: List[Issue] = []
    for title, body in sections:
        for pattern, reason in forbidden.items():
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                issues.append(
                    Issue(
                        "error",
                        "forbidden-wording",
                        f"недопустимая формулировка «{match.group(0)}» — {reason}",
                        title,
                    )
                )
    return issues


def _check_glossary(sections: Sequence[tuple[str, str]], glossary: Dict[str, str]) -> List[Issue]:
    issues: List[Issue] = []
    for title, body in sections:
        for variant, canonical in glossary.items():
            if re.search(rf"\b{re.escape(variant)}\b", body, re.IGNORECASE):
                issues.append(
                    Issue(
                        "warning",
                        "glossary",
                        f"термин «{variant}» следует привести к «{canonical}»",
                        title,
                    )
                )
    return issues


def _check_structure(sections: Sequence[tuple[str, str]], outline: Outline) -> List[Issue]:
    issues: List[Issue] = []
    present = [re.sub(r"^\d+\.\s*", "", title).strip() for title, _ in sections]
    for spec in outline.sections:
        if spec.title not in present:
            issues.append(
                Issue("error", "missing-section", f"обязательный раздел «{spec.title}» отсутствует")
            )
            continue
        body = next(b for t, b in sections if re.sub(r"^\d+\.\s*", "", t).strip() == spec.title)
        words = len(body.split())
        low, high = spec.target_words * 0.4, spec.target_words * 2.0
        if words < low or words > high:
            issues.append(
                Issue(
                    "warning",
                    "section-length",
                    f"объём {words} слов при целевых {spec.target_words} "
                    f"(допустимо {int(low)}–{int(high)})",
                    spec.title,
                )
            )
    return issues


def summarize(issues: Iterable[Issue]) -> Dict[str, int]:
    counts = {level: 0 for level in LEVELS}
    for issue in issues:
        counts[issue.level] = counts.get(issue.level, 0) + 1
    return counts


def blocking(issues: Iterable[Issue]) -> bool:
    return any(issue.level == "error" for issue in issues)
