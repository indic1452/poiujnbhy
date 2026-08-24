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


def split_document(markdown: str) -> tuple[List[tuple[str, str]], str]:
    """Делит отчёт на проверяемые разделы и приложение с источниками.

    Границы определяются ПОЛОЖЕНИЕМ блока, а не текстом заголовка. Это принципиально:
    заголовок внутри секции пишет модель или инженер, поэтому строка вида
    «## Приложение А. Источники (рабочий список)» посреди текста не должна создавать
    зону, свободную от проверки. Приложением считается только последний раздел
    документа, оглавлением — только первый; всё остальное проверяется как текст.
    """
    sections = parse_sections(_strip_service(markdown))
    titled = [(title.strip(), body) for title, body in sections if title.strip()]

    appendix = ""
    if titled and APPENDIX_MARKER in titled[-1][0]:
        appendix = titled[-1][1]
        titled = titled[:-1]
    if titled and _is_contents(titled[0][0]):
        titled = titled[1:]
    return titled, appendix


def _is_contents(title: str) -> bool:
    return re.sub(r"^\d+\.\s*", "", title).strip().casefold() == "содержание"


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
    sections: Sequence[tuple[str, str]] | None = None,
    appendix: str | None = None,
) -> List[Issue]:
    """Полная проверка готового отчёта. Возвращает список замечаний.

    ``sections`` и ``appendix`` можно передать явно — тогда разбор Markdown не
    выполняется вовсе. Так делает веб-слой: тексты секций и список источников он
    берёт из базы, то есть из данных, сформированных кодом, а не из документа,
    который правили модель и инженер. Это закрывает подделку границ разделов.
    """
    issues: List[Issue] = []
    if sections is None or appendix is None:
        parsed_sections, parsed_appendix = split_document(markdown)
        body_sections = list(sections) if sections is not None else parsed_sections
        appendix = appendix if appendix is not None else parsed_appendix
    else:
        body_sections = list(sections)

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
        # Заголовок раздела — такой же текст отчёта: и модель, и инженер могут
        # написать в нём число или запрещённую формулировку.
        for value in sorted(numbers.extract(f"{title}\n{body}")):
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
    for title, raw_body in sections:
        body = f"{title}\n{raw_body}"
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
    for title, raw_body in sections:
        body = f"{title}\n{raw_body}"
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
    for title, raw_body in sections:
        body = f"{title}\n{raw_body}"
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
