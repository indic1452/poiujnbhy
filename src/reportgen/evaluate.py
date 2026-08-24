"""Эвал-харнесс: измеримое качество отчётов (док. 05).

Без измеримого качества система через полгода тихо деградирует, и никто не
заметит: отчёты продолжат выходить, просто станут хуже. Поэтому набор метрик
считается автоматически и оформляется обычными юнит-тестами — «сломал промпт»
должно быть видно сразу, а не через месяц по жалобам заказчиков.

Метрики намеренно разделены на самостоятельные функции: каждая проверяет одно
свойство отчёта, тестируется отдельно и может использоваться в CI без запуска
всего конвейера.

======================  ========================================  ==========
Метрика                 Что означает                              Цель
======================  ========================================  ==========
numeric_fidelity        доля чисел текста, найденных в фактах     1.00
fact_recall             доля значимых находок, упомянутых в тексте 0.95
citation_precision      доля ссылок [S<N>], имеющих источник      0.90
structure_compliance    доля обязательных разделов на месте       1.00
glossary_compliance     доля терминов в канонической форме        0.98
reference_similarity    близость к эталонному отчёту человека     —
======================  ========================================  ==========

``reference_similarity`` цели не имеет: это тренд, а не приёмочный порог
(док. 05, 5.2). Следят за его ростом от версии к версии.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from . import numbers
from .facts import FactPack
from .llm import LLM
from .pipeline import Outline, generate_report
from .retrieval import Retriever, tokenize
from .store.repo import normalized_edit_distance
from .verify import APPENDIX_MARKER, SERVICE_MARKER, parse_sections, verify_report

# Целевые значения из док. 05, раздел 5.2. Ниже порога — сборка красная.
TARGETS: Dict[str, float] = {
    "numeric_fidelity": 1.0,
    "fact_recall": 0.95,
    "citation_precision": 0.90,
    "structure_compliance": 1.0,
    "glossary_compliance": 0.98,
}

METRIC_ORDER = (
    "numeric_fidelity",
    "fact_recall",
    "citation_precision",
    "structure_compliance",
    "glossary_compliance",
    "reference_similarity",
)

# Доля слов заголовка находки, которую текст обязан упомянуть, чтобы находка
# считалась раскрытой. Сравнение идёт по стеммированным токенам, поэтому
# склонения и числа роли не играют, а порог отсекает случайные совпадения.
DEFAULT_TITLE_OVERLAP = 0.6

_CITATION_RE = re.compile(r"\[(S\d+)\]")


class EvalError(RuntimeError):
    """Эвал-набор не удалось загрузить или прогнать."""


# ------------------------------------------------------------ структуры ---

@dataclass
class CaseResult:
    """Результат прогона одного кейса золотого набора."""

    case_id: str
    metrics: Dict[str, float] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def errors(self) -> int:
        return sum(1 for issue in self.issues if issue.get("level") == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for issue in self.issues if issue.get("level") == "warning")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "metrics": {name: round(float(value), 4) for name, value in self.metrics.items()},
            "issues": self.issues,
            "errors": self.errors,
            "warnings": self.warnings,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class EvalReport:
    """Прогон золотого набора целиком: результаты по кейсам и сводка."""

    results: List[CaseResult] = field(default_factory=list)
    aggregate: Dict[str, Any] = field(default_factory=dict)

    def metric_names(self) -> List[str]:
        seen: List[str] = []
        for name in METRIC_ORDER:
            if any(name in result.metrics for result in self.results):
                seen.append(name)
        for result in self.results:
            for name in result.metrics:
                if name not in seen:
                    seen.append(name)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "results": [result.to_dict() for result in self.results],
            "aggregate": self.aggregate,
        }

    def to_markdown(self) -> str:
        names = self.metric_names()
        lines: List[str] = ["# Прогон эвал-набора", ""]
        cases = self.aggregate.get("cases", len(self.results))
        seconds = float(self.aggregate.get("seconds_total", 0.0))
        lines.append(
            f"Кейсов: {cases}. Суммарное время генерации: {seconds:.2f} с "
            f"(в среднем {float(self.aggregate.get('seconds_mean', 0.0)):.2f} с на кейс)."
        )
        lines.append("")

        header = "| Кейс | " + " | ".join(names) + " | ошибок | предупр. | время, с |"
        lines.append(header)
        lines.append("|" + "---|" * (len(names) + 4))
        for result in self.results:
            cells = [
                f"{result.metrics[name]:.3f}" if name in result.metrics else "—"
                for name in names
            ]
            lines.append(
                f"| {result.case_id} | " + " | ".join(cells)
                + f" | {result.errors} | {result.warnings} | {result.seconds:.2f} |"
            )
        lines.append("")

        lines.append("## Сводка")
        lines.append("")
        lines.append("| Метрика | Среднее | Цель | Статус |")
        lines.append("|---|---|---|---|")
        means = self.aggregate.get("metrics", {})
        for name in names:
            if name not in means:
                continue
            target = TARGETS.get(name)
            value = float(means[name])
            if target is None:
                status, target_text = "—", "—"
            else:
                status = "ок" if value + 1e-9 >= target else "НИЖЕ ЦЕЛИ"
                target_text = f"≥ {target:.2f}"
            lines.append(f"| {name} | {value:.3f} | {target_text} | {status} |")
        lines.append("")

        failed = self.aggregate.get("below_target", [])
        if failed:
            lines.append(
                "Ниже целевых значений: " + ", ".join(failed)
                + ". Выкатка изменения не допускается до разбора (док. 05, 5.4)."
            )
        else:
            lines.append("Все метрики на целевом уровне или выше.")
        lines.append("")
        return "\n".join(lines)


# --------------------------------------------------------- разбор текста --

def _as_factpack(factpack: FactPack | Mapping[str, Any]) -> FactPack:
    if isinstance(factpack, FactPack):
        return factpack
    return FactPack.from_dict(dict(factpack))


def body_sections(markdown: str) -> List[Tuple[str, str]]:
    """Разделы, написанные моделью: без титула, оглавления, приложения и служебного блока.

    Метрики считаются только по ним: числа в оглавлении и в цитатах
    приложения не являются утверждениями отчёта.
    """
    index = markdown.find(SERVICE_MARKER)
    text = markdown if index == -1 else markdown[:index]
    return [
        (title, body)
        for title, body in parse_sections(text)
        if title.strip() and title.strip() != "Содержание" and APPENDIX_MARKER not in title
    ]


def body_text(markdown: str) -> str:
    return "\n\n".join(body for _, body in body_sections(markdown))


def appendix_text(markdown: str) -> str:
    return "\n".join(body for title, body in parse_sections(markdown) if APPENDIX_MARKER in title)


# ----------------------------------------------------------------- метрики -

def numeric_fidelity(markdown: str, factpack: FactPack | Mapping[str, Any]) -> float:
    """Доля чисел отчёта, присутствующих в факт-пакете.

    Главная метрика системы: цель — ровно 1.0, всё остальное означает, что
    модель назвала число, которого никто не измерял (док. 01, инвариант 1).
    Отчёт без единого числа считается добросовестным (1.0) — эту ситуацию
    ловит ``fact_recall``, а не эта метрика.
    """
    facts = _as_factpack(factpack)
    found = numbers.extract(body_text(markdown))
    if not found:
        return 1.0
    allowed = facts.allowed_numbers()
    return round(len(found & allowed) / len(found), 4)


def unknown_numbers(markdown: str, factpack: FactPack | Mapping[str, Any]) -> List[str]:
    """Какие именно числа отчёта не подтверждены факт-пакетом."""
    facts = _as_factpack(factpack)
    return sorted(numbers.extract(body_text(markdown)) - facts.allowed_numbers())


def fact_recall(
    markdown: str,
    factpack: FactPack | Mapping[str, Any],
    min_severity: str = "medium",
    *,
    overlap: float = DEFAULT_TITLE_OVERLAP,
) -> float:
    """Доля значимых находок, упомянутых в тексте отчёта.

    Сопоставление — по пересечению множеств стеммированных токенов заголовка
    находки с токенами отчёта (:func:`reportgen.retrieval.tokenize`), поэтому
    метрика устойчива к склонениям и к перестановке слов: инженер напишет
    «паразитной составляющей», а находка называется «паразитная составляющая».
    """
    facts = _as_factpack(factpack)
    findings = facts.findings_at_least(min_severity)
    if not findings:
        return 1.0
    report_tokens = set(tokenize(body_text(markdown)))
    mentioned = 0
    for finding in findings:
        title_tokens = set(tokenize(finding.title))
        if not title_tokens:
            continue
        share = len(title_tokens & report_tokens) / len(title_tokens)
        if share >= overlap:
            mentioned += 1
    return round(mentioned / len(findings), 4)


def missing_findings(
    markdown: str,
    factpack: FactPack | Mapping[str, Any],
    min_severity: str = "medium",
    *,
    overlap: float = DEFAULT_TITLE_OVERLAP,
) -> List[str]:
    """Идентификаторы находок, которых нет в тексте, — для разбора провала."""
    facts = _as_factpack(factpack)
    report_tokens = set(tokenize(body_text(markdown)))
    absent: List[str] = []
    for finding in facts.findings_at_least(min_severity):
        title_tokens = set(tokenize(finding.title))
        if not title_tokens:
            continue
        if len(title_tokens & report_tokens) / len(title_tokens) < overlap:
            absent.append(finding.id)
    return absent


def citation_precision(markdown: str) -> float:
    """Доля ссылок ``[S<N>]``, которым соответствует фрагмент в приложении.

    Полная версия метрики из док. 05 требует LLM-судьи (подтверждает ли
    фрагмент утверждение). Здесь считается её обязательная нижняя граница:
    ссылка вообще существует. Ссылка в никуда — это отчёт, который инженер не
    сможет проверить за пять секунд, ради чего приложение и заводилось.
    """
    known = set(_CITATION_RE.findall(appendix_text(markdown)))
    used = _CITATION_RE.findall(body_text(markdown))
    if not used:
        return 1.0
    resolved = sum(1 for label in used if label in known)
    return round(resolved / len(used), 4)


def structure_compliance(markdown: str, outline: Outline) -> float:
    """Доля обязательных секций шаблона, присутствующих в отчёте."""
    if not outline.sections:
        return 1.0
    present = {
        re.sub(r"^\d+\.\s*", "", title).strip()
        for title, _ in body_sections(markdown)
    }
    found = sum(1 for spec in outline.sections if spec.title in present)
    return round(found / len(outline.sections), 4)


def glossary_compliance(markdown: str, glossary: Mapping[str, str]) -> float:
    """Доля употреблений терминов в канонической форме.

    Глоссарий задаётся как «вариант → канон». Считаются все вхождения обеих
    форм; если ни одна не встретилась, отчёт глоссарию не противоречит (1.0).
    """
    if not glossary:
        return 1.0
    text = body_text(markdown)
    # Один канон может стоять за несколькими вариантами («с/ш» и
    # «signal-to-noise» → «ОСШ»); считаем каждый термин ровно один раз.
    canonical_terms = {str(value).strip() for value in glossary.values() if str(value).strip()}
    variant_terms = {
        str(key).strip() for key in glossary
        if str(key).strip() and str(key).strip() not in canonical_terms
    }
    canonical_hits = sum(_count_term(text, term) for term in canonical_terms)
    variant_hits = sum(_count_term(text, term) for term in variant_terms)
    total = canonical_hits + variant_hits
    if total == 0:
        return 1.0
    return round(canonical_hits / total, 4)


def _count_term(text: str, term: str) -> int:
    term = (term or "").strip()
    if not term:
        return 0
    pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
    return len(re.findall(pattern, text, re.IGNORECASE))


def reference_similarity(markdown: str, reference_markdown: str) -> float:
    """Близость к эталонному отчёту человека: 1 минус нормализованное расстояние.

    Расстояние считается тем же кодом, что и бизнес-метрика «черновик →
    финал» (:func:`reportgen.store.repo.normalized_edit_distance`), поэтому
    числа из эвала и из продуктивного мониторинга сравнимы между собой.
    """
    return round(1.0 - normalized_edit_distance(markdown, reference_markdown), 4)


# ------------------------------------------------------------- прогон ------

def load_golden_set(path: str | Path) -> List[Dict[str, Any]]:
    """Читает JSON-манифест золотого набора (док. 05, 5.1).

    Допустимы две формы: список кейсов или объект ``{"cases": [...]}`` с
    произвольными дополнительными полями (имя набора, дата заморозки).
    Относительные пути внутри манифеста считаются от каталога манифеста —
    набор должен переноситься между машинами целиком.
    """
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise EvalError(f"манифест золотого набора не найден: {manifest_path}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise EvalError(f"манифест {manifest_path} повреждён: {error}") from error

    items = raw.get("cases") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise EvalError(
            f"манифест {manifest_path}: ожидался список кейсов или объект с полем 'cases'"
        )

    base = manifest_path.parent
    cases: List[Dict[str, Any]] = []
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise EvalError(f"манифест {manifest_path}, кейс {number}: ожидался объект JSON")
        if not str(item.get("facts_path") or "").strip():
            raise EvalError(
                f"манифест {manifest_path}, кейс {number}: отсутствует обязательное поле "
                f"'facts_path' (путь к проверенному факт-пакету)"
            )
        case = dict(item)
        for key in ("facts_path", "reference_path", "outline_path"):
            value = case.get(key)
            if value:
                case[key] = str((base / str(value)).resolve())
        cases.append(case)
    return cases


def _resolve_outline(case: Mapping[str, Any], facts: FactPack, outline_dir: Path) -> Outline:
    explicit = case.get("outline_path")
    if explicit:
        try:
            return Outline.load(explicit)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise EvalError(f"шаблон {explicit} не читается: {error}") from error

    directory = Path(outline_dir)
    candidate = directory / f"outline_{facts.report_type}.json"
    paths = [candidate] if candidate.is_file() else sorted(directory.glob("outline_*.json"))
    for path in paths:
        try:
            outline = Outline.load(path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise EvalError(f"шаблон {path} повреждён: {error}") from error
        if outline.report_type == facts.report_type:
            return outline
    raise EvalError(
        f"в каталоге {directory} нет шаблона для типа отчёта '{facts.report_type}'"
    )


def evaluate_report(
    markdown: str,
    facts: FactPack,
    outline: Outline,
    *,
    reference_markdown: str | None = None,
    glossary: Mapping[str, str] | None = None,
) -> Dict[str, float]:
    """Все автометрики для одного готового отчёта."""
    metrics: Dict[str, float] = {
        "numeric_fidelity": numeric_fidelity(markdown, facts),
        "fact_recall": fact_recall(markdown, facts),
        "citation_precision": citation_precision(markdown),
        "structure_compliance": structure_compliance(markdown, outline),
    }
    if glossary:
        metrics["glossary_compliance"] = glossary_compliance(markdown, glossary)
    if reference_markdown is not None:
        metrics["reference_similarity"] = reference_similarity(markdown, reference_markdown)
    return metrics


def run_eval(
    cases: Sequence[Mapping[str, Any]],
    llm: LLM,
    outline_dir: str | Path,
    *,
    retriever: Retriever | None = None,
    glossary: Mapping[str, str] | None = None,
    top_k: int = 6,
) -> EvalReport:
    """Прогоняет золотой набор: генерация → метрики → сводка.

    Время генерации замеряется по каждому кейсу отдельно: latency — такая же
    метрика SLA, как и качество (док. 05, 5.2).
    """
    results: List[CaseResult] = []
    for number, case in enumerate(cases, start=1):
        facts_path = case.get("facts_path")
        if not facts_path:
            raise EvalError(f"кейс {number}: не указан путь к факт-пакету (facts_path)")
        try:
            facts = FactPack.load(facts_path)
        except (OSError, ValueError) as error:
            raise EvalError(f"кейс {number}: факт-пакет {facts_path} не загружается — {error}") from error

        outline = _resolve_outline(case, facts, Path(outline_dir))

        started = time.perf_counter()
        result = generate_report(facts, outline, llm, retriever, top_k=top_k)
        seconds = time.perf_counter() - started

        reference = None
        if case.get("reference_path"):
            reference = Path(str(case["reference_path"])).read_text(encoding="utf-8")

        metrics = evaluate_report(
            result.markdown, facts, outline,
            reference_markdown=reference, glossary=glossary,
        )
        issues = [
            {
                "level": issue.level,
                "code": issue.code,
                "message": issue.message,
                "section": issue.section,
            }
            for issue in verify_report(
                result.markdown, facts, outline, glossary=dict(glossary) if glossary else None
            )
        ]
        results.append(CaseResult(
            case_id=str(case.get("case_id") or facts.case_id),
            metrics=metrics,
            issues=issues,
            seconds=seconds,
        ))

    return EvalReport(results=results, aggregate=aggregate_metrics(results))


def aggregate_metrics(results: Sequence[CaseResult]) -> Dict[str, Any]:
    """Средние по кейсам, время и список метрик ниже целевых значений."""
    names: List[str] = []
    for name in METRIC_ORDER:
        if any(name in result.metrics for result in results):
            names.append(name)
    for result in results:
        for name in result.metrics:
            if name not in names:
                names.append(name)

    means: Dict[str, float] = {}
    for name in names:
        values = [result.metrics[name] for result in results if name in result.metrics]
        if values:
            means[name] = round(sum(values) / len(values), 4)

    seconds = [result.seconds for result in results]
    below = [
        name for name, value in means.items()
        if name in TARGETS and value + 1e-9 < TARGETS[name]
    ]
    return {
        "cases": len(results),
        "metrics": means,
        "targets": dict(TARGETS),
        "below_target": below,
        "passed": not below,
        "errors": sum(result.errors for result in results),
        "warnings": sum(result.warnings for result in results),
        "seconds_total": round(sum(seconds), 3),
        "seconds_mean": round(sum(seconds) / len(seconds), 3) if seconds else 0.0,
    }
