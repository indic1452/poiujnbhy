"""Сборка отчёта: план → секции → сшивка.

Каждая секция генерируется отдельным вызовом модели со своим узким контекстом
(док. 04). Это даёт три вещи, недостижимые при генерации «одним промптом»:
ровное качество по всему документу, возможность перегенерировать один раздел
и параллельный запуск.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .corpus import Chunk, tidy_quote

#: Сколько символов фрагмента подаётся модели. Приложение к отчёту берёт то же
#: значение: см. SourceRegistry.render_appendix.
PROMPT_QUOTE_CHARS = 700
from .facts import FactPack
from .llm import LLM
from .prompts import SECTION_PROMPT, SYSTEM_PROMPT
from .retrieval import Hit, Retriever

DEFAULT_STYLE = "нейтральный технический, без оценочных суждений"


@dataclass
class SectionSpec:
    """Описание одной секции шаблона-плана."""

    id: str
    title: str
    instruction: str
    required_facts: Sequence[str] = ()
    optional_facts: Sequence[str] = ()
    findings_min_severity: str | None = None
    retrieval_queries: Sequence[str] = ()
    retrieval_doc_types: Sequence[str] = ()
    #: Ограничить поиск направлениями (спутник, релейка, протоколы …).
    #: Пусто — искать по всей библиотеке.
    retrieval_domains: Sequence[str] = ()
    target_words: int = 250
    style: str = DEFAULT_STYLE

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SectionSpec":
        for required in ("id", "title", "instruction"):
            if required not in raw:
                raise ValueError(f"секция шаблона: отсутствует поле '{required}'")
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"секция '{raw['id']}': неизвестные поля {sorted(unknown)}")
        return cls(**raw)


@dataclass
class Outline:
    """Шаблон-план отчёта одного типа."""

    report_type: str
    title: str
    sections: List[SectionSpec]
    style: str = DEFAULT_STYLE
    version: str = "1"

    @classmethod
    def load(cls, path: str | Path) -> "Outline":
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return cls(
            report_type=raw["report_type"],
            title=raw["title"],
            style=raw.get("style", DEFAULT_STYLE),
            version=str(raw.get("version", "1")),
            sections=[SectionSpec.from_dict(item) for item in raw["sections"]],
        )

    def required_facts(self) -> List[str]:
        seen: List[str] = []
        for section in self.sections:
            for key in section.required_facts:
                if key not in seen:
                    seen.append(key)
        return seen


class SourceRegistry:
    """Сквозная нумерация источников [S1], [S2], … по всему отчёту."""

    def __init__(self) -> None:
        self._by_chunk: Dict[str, str] = {}
        self._chunks: List[Chunk] = []
        # Секции могут генерироваться параллельно и метить источники одновременно.
        self._lock = threading.Lock()

    def label(self, chunk: Chunk) -> str:
        with self._lock:
            if chunk.chunk_id not in self._by_chunk:
                self._by_chunk[chunk.chunk_id] = f"S{len(self._chunks) + 1}"
                self._chunks.append(chunk)
            return self._by_chunk[chunk.chunk_id]

    @property
    def chunks(self) -> List[Chunk]:
        return list(self._chunks)

    def items(self) -> List[tuple[str, Chunk]]:
        """Пары (метка, фрагмент) в порядке первого упоминания в отчёте."""
        return [(self._by_chunk[chunk.chunk_id], chunk) for chunk in self._chunks]

    def render_appendix(self, quote_chars: int = PROMPT_QUOTE_CHARS) -> str:
        """Приложение «Источники» — то же, что видела модель.

        Приложение обязано быть НЕ КОРОЧЕ фрагмента, поданного в промпт.
        Верификатор считает числами из источника только то, что нашёл здесь:
        если модель законно взяла «2048 kbit/s» из символов 400–700 плотной
        английской таблицы, а в приложение попали первые 400, утверждение
        отчёта падает с «число отсутствует в факт-пакете» — на числе, которое
        инженер видит своими глазами в источнике. Причина при этом не видна
        нигде, и правка текста не помогает.
        """
        if not self._chunks:
            return "Внешние источники не привлекались."
        limit = max(int(quote_chars), PROMPT_QUOTE_CHARS)
        lines: List[str] = []
        for chunk in self._chunks:
            quote = tidy_quote(chunk.text, limit)
            body = quote.replace("\n", "\n> ")
            lines.append(f"**[{self._by_chunk[chunk.chunk_id]}]** {chunk.citation}\n\n> {body}\n")
        return "\n".join(lines)


@dataclass
class GeneratedSection:
    spec: SectionSpec
    text: str
    sources: List[str] = field(default_factory=list)
    missing_facts: List[str] = field(default_factory=list)


@dataclass
class ReportResult:
    markdown: str
    sections: List[GeneratedSection]
    registry: SourceRegistry
    missing_facts: List[str]
    meta: Dict[str, Any]


def _render_sources(hits: Sequence[Hit], registry: SourceRegistry,
                    quote_chars: int = PROMPT_QUOTE_CHARS) -> tuple[str, List[str]]:
    if not hits:
        return "(релевантных источников не найдено)", []
    blocks: List[str] = []
    labels: List[str] = []
    for hit in hits:
        label = registry.label(hit.chunk)
        labels.append(label)
        # Переводы строк сохраняем: таблицы норм и поля кадров в одну строку
        # нечитаемы и для модели, и для инженера.
        text = _tidy_quote(hit.chunk.text, quote_chars)
        status = hit.chunk.meta.get("status", "current")
        mark = "" if status == "current" else f" [ВНИМАНИЕ: документ не действующий — {status}]"
        blocks.append(f"[{label}] {hit.chunk.citation}{mark}\n{text}")
    return "\n\n".join(blocks), labels


def _tidy_quote(text: str, limit: int) -> str:
    """Цитата для промпта. См. :func:`reportgen.corpus.tidy_quote`."""
    return tidy_quote(text, limit)


def _summarize(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat[:limit].rstrip() + ("…" if len(flat) > limit else "")


def _section_query(spec: SectionSpec, facts: FactPack) -> str:
    parts = [spec.title, *spec.retrieval_queries]
    parts.extend(str(value) for value in facts.equipment.values())
    parts.extend(facts.keywords)
    for key in list(spec.required_facts) + list(spec.optional_facts):
        measurement = facts.measurements.get(key)
        if measurement is not None:
            parts.append(measurement.title)
    return " ".join(parts)


def generate_section(
    spec: SectionSpec,
    facts: FactPack,
    retriever: Retriever | None,
    llm: LLM,
    *,
    previously: Sequence[tuple[str, str]] = (),
    registry: SourceRegistry,
    top_k: int = 6,
) -> GeneratedSection:
    """Генерирует одну секцию. Секции независимы и могут считаться параллельно."""
    missing = facts.missing(spec.required_facts)

    keys = [*spec.required_facts, *spec.optional_facts]
    facts_block = facts.render_measurements(keys) if keys else "(измерения для раздела не заданы)"
    if spec.findings_min_severity:
        findings = facts.findings_at_least(spec.findings_min_severity)
        facts_block += "\n\n" + facts.render_findings(findings)
    if missing:
        facts_block += (
            "\n\nОТСУТСТВУЮТ ОБЯЗАТЕЛЬНЫЕ ДАННЫЕ: "
            + ", ".join(missing)
            + ". Отметь это строкой [ТРЕБУЕТ ПРОВЕРКИ: …]."
        )

    hits: List[Hit] = []
    if retriever is not None:
        query = _section_query(spec, facts)
        try:
            hits = retriever.search(
                query, top_k=top_k,
                doc_types=spec.retrieval_doc_types or None,
                domains=spec.retrieval_domains or None,
            )
        except TypeError:
            # Поисковик старого образца, без направлений.
            hits = retriever.search(
                query, top_k=top_k, doc_types=spec.retrieval_doc_types or None
            )
    sources_block, labels = _render_sources(hits, registry)

    previously_block = (
        "\n".join(f"- {title}: {summary}" for title, summary in previously)
        or "(это первый раздел отчёта)"
    )

    user = SECTION_PROMPT.format(
        header=facts.render_header(),
        title=spec.title,
        instruction=spec.instruction,
        target_words=spec.target_words,
        style=spec.style,
        facts=facts_block,
        sources=sources_block,
        previously=previously_block,
    )
    text = llm.complete(
        SYSTEM_PROMPT,
        user,
        # Русский текст — примерно 2.5 токена на слово, плюс запас на таблицы
        # и ссылки. Скупой лимит обрывал раздел на середине фразы, и инженер
        # видел это как «модель пишет мало».
        max_tokens=max(900, int(spec.target_words * 4)),
    )
    return GeneratedSection(spec=spec, text=text.strip(), sources=labels, missing_facts=missing)


def generate_report(
    facts: FactPack,
    outline: Outline,
    llm: LLM,
    retriever: Retriever | None = None,
    *,
    top_k: int = 6,
    generated_at: str | None = None,
    index_version: str = "—",
    parallel_sections: int = 1,
) -> ReportResult:
    """Полный цикл: секции → сшивка → служебный блок → приложение источников.

    ``parallel_sections`` — сколько секций писать одновременно. Секции идут
    волнами: внутри волны они пишутся параллельно, а каждая следующая волна
    видит краткое содержание всех предыдущих и потому не повторяется. Это
    компромисс между скоростью и связностью: при одном GPU и батчинге в
    llama.cpp волна из двух секций почти вдвое быстрее двух последовательных,
    потому что веса модели читаются из памяти один раз на обе.

    Порядок секций в результате всегда соответствует шаблону, поэтому отчёт
    остаётся воспроизводимым (инвариант 1.4.3).
    """
    if facts.report_type != outline.report_type:
        raise ValueError(
            f"тип отчёта в факт-пакете ('{facts.report_type}') не совпадает "
            f"с шаблоном ('{outline.report_type}')"
        )

    registry = SourceRegistry()
    generated: List[GeneratedSection] = []
    previously: List[tuple[str, str]] = []
    wave_size = max(1, int(parallel_sections))

    for start in range(0, len(outline.sections), wave_size):
        wave = outline.sections[start:start + wave_size]
        if len(wave) == 1:
            sections = [generate_section(
                wave[0], facts, retriever, llm,
                previously=previously, registry=registry, top_k=top_k,
            )]
        else:
            seen = list(previously)
            with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                sections = list(pool.map(
                    lambda spec: generate_section(
                        spec, facts, retriever, llm,
                        previously=seen, registry=registry, top_k=top_k,
                    ),
                    wave,
                ))
        for spec, section in zip(wave, sections):
            generated.append(section)
            previously.append((spec.title, _summarize(section.text)))

    meta = {
        "case_id": facts.case_id,
        "report_type": facts.report_type,
        "generated_at": generated_at or date.today().isoformat(),
        "outline_version": outline.version,
        "index_version": index_version,
        "model": getattr(llm, "name", "unknown"),
        "facts_digest": facts.digest(),
    }
    markdown = assemble(facts, outline, generated, registry, meta)
    missing = sorted({key for section in generated for key in section.missing_facts})
    return ReportResult(
        markdown=markdown,
        sections=generated,
        registry=registry,
        missing_facts=missing,
        meta=meta,
    )


def status_line(meta: Dict[str, Any]) -> str:
    """Строка о состоянии документа в шапке отчёта.

    Проверенный отчёт уходит по назначению. Пока строка была прибита гвоздями,
    он уходил с надписью «ЧЕРНОВИК, требует проверки и подписи инженера» —
    и в Markdown, и в DOCX. Для организации, которая этим отвечает на
    входящее письмо, это хуже опечатки.
    """
    if str(meta.get("status") or "draft") != "approved":
        line = "> Статус документа: **ЧЕРНОВИК**. Требует проверки и подписи инженера."
        # Число несведённых ошибок пишем прямо в документ. Черновик выгружают
        # и распечатывают — на бумаге не видно ни панели замечаний, ни того,
        # что верификатор вообще запускался.
        errors = int(meta.get("errors") or 0)
        if errors:
            line += (f" Верификатор нашёл ошибок: {errors} — числа в тексте"
                     " не подтверждены измерениями.")
        return line
    parts = ["> Статус документа: **УТВЕРЖДЁН**."]
    who = str(meta.get("approved_by_name") or "").strip()
    when = str(meta.get("approved_at") or "").strip()
    if who:
        parts.append(f"Утвердил: {who}.")
    if when:
        parts.append(f"Дата утверждения: {when[:10]}.")
    return " ".join(parts)


def assemble(
    facts: FactPack,
    outline: Outline,
    sections: Sequence[GeneratedSection],
    registry: SourceRegistry,
    meta: Dict[str, Any],
) -> str:
    """Сшивка: титул, служебный блок, оглавление, разделы, приложение."""
    lines: List[str] = [f"# {outline.title}", ""]
    lines.append(f"**Обращение:** {facts.case_id}  ")
    lines.append(f"**Номер группы:** {facts.group_no or '—'}  ")
    if facts.equipment:
        equipment = ", ".join(f"{k}: {v}" for k, v in facts.equipment.items())
        lines.append(f"**Оборудование:** {equipment}  ")
    lines.append(f"**Дата:** {meta['generated_at']}")
    lines.append("")
    lines.append(status_line(meta))
    lines.append("")

    lines.append("## Содержание")
    lines.append("")
    for number, section in enumerate(sections, start=1):
        lines.append(f"{number}. {section.spec.title}")
    lines.append(f"{len(sections) + 1}. Приложение А. Источники")
    lines.append("")

    for number, section in enumerate(sections, start=1):
        lines.append(f"## {number}. {section.spec.title}")
        lines.append("")
        lines.append(section.text)
        lines.append("")

    lines.append(f"## {len(sections) + 1}. Приложение А. Источники")
    lines.append("")
    lines.append(registry.render_appendix())
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("<!-- служебный блок: воспроизводимость (док. 01, инвариант 1.4.3) -->")
    lines.append("")
    lines.append("| Параметр сборки | Значение |")
    lines.append("|---|---|")
    for key in ("generated_at", "model", "outline_version", "index_version", "facts_digest"):
        lines.append(f"| {key} | {meta[key]} |")
    lines.append("")
    return "\n".join(lines)


def check_facts_coverage(facts: FactPack, outline: Outline) -> Dict[str, List[str]]:
    """Каких измерений не хватает до запуска модели.

    Вызывается сразу после автоанализа: инженеру сообщается, какой замер нужно
    доснять, до того как он потратит время на чтение черновика.
    """
    result: Dict[str, List[str]] = {}
    for spec in outline.sections:
        missing = facts.missing(spec.required_facts)
        if missing:
            result[spec.id] = missing
    return result
