"""Сервисный слой: вся логика работы с кейсами и отчётами.

Веб-обработчики (:mod:`reportgen.web.api`) остаются тонкими — они разбирают
запрос и вызывают методы отсюда. Благодаря этому та же логика доступна из CLI
и из тестов без поднятия HTTP-сервера.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ..config import Settings
from ..corpus import Chunk
from ..facts import FactPack, FactPackError
from ..llm import LLM, build_llm
from ..pipeline import (
    GeneratedSection,
    Outline,
    check_facts_coverage,
    assemble,
    generate_report,
    generate_section,
)
from ..retrieval import BM25Index, Retriever
from ..store.models import Case, Report, ReportSection, User
from ..store.repo import Repositories
from ..verify import summarize, verify_report

APPENDIX_QUOTE_CHARS = 400


class ServiceError(RuntimeError):
    """Ошибка бизнес-логики, которую можно показать пользователю."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class SourceRecord:
    """Источник, на который сослался отчёт.

    Хранится в метаданных отчёта целиком (вместе с цитатой), чтобы приложение
    к отчёту не рассыпалось после переиндексации библиотеки.
    """

    label: str
    chunk_uid: str
    citation: str
    text: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "label": self.label,
            "chunk_uid": self.chunk_uid,
            "citation": self.citation,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, str]) -> "SourceRecord":
        return cls(
            label=raw["label"],
            chunk_uid=raw.get("chunk_uid", ""),
            citation=raw.get("citation", ""),
            text=raw.get("text", ""),
        )


class StoredRegistry:
    """Реестр источников, переживающий перезапуск и перегенерацию секций.

    Совместим по интерфейсу с :class:`reportgen.pipeline.SourceRegistry`
    (методы ``label`` и ``render_appendix``), но восстанавливается из
    метаданных сохранённого отчёта, а не из результата одной генерации.
    """

    def __init__(self, records: Iterable[SourceRecord] = ()):
        self._records: List[SourceRecord] = list(records)
        self._by_uid: Dict[str, SourceRecord] = {r.chunk_uid: r for r in self._records}

    @classmethod
    def from_meta(cls, meta: Dict[str, Any]) -> "StoredRegistry":
        return cls(SourceRecord.from_dict(item) for item in meta.get("sources", []))

    def label(self, chunk: Chunk) -> str:
        existing = self._by_uid.get(chunk.chunk_id)
        if existing is not None:
            return existing.label
        record = SourceRecord(
            label=f"S{len(self._records) + 1}",
            chunk_uid=chunk.chunk_id,
            citation=chunk.citation,
            text=" ".join(chunk.text.split()),
        )
        self._records.append(record)
        self._by_uid[record.chunk_uid] = record
        return record.label

    @property
    def records(self) -> List[SourceRecord]:
        return list(self._records)

    def render_appendix(self, quote_chars: int = APPENDIX_QUOTE_CHARS) -> str:
        if not self._records:
            return "Внешние источники не привлекались."
        blocks: List[str] = []
        for record in self._records:
            quote = record.text
            if len(quote) > quote_chars:
                quote = quote[:quote_chars].rstrip() + "…"
            blocks.append(f"**[{record.label}]** {record.citation}\n\n> {quote}\n")
        return "\n".join(blocks)

    def to_meta(self) -> List[Dict[str, str]]:
        return [record.to_dict() for record in self._records]


class OutlineLibrary:
    """Шаблоны-планы отчётов, перечитываемые при изменении файлов на диске."""

    def __init__(self, templates_dir: Path):
        self.templates_dir = Path(templates_dir)
        self._cache: Dict[str, Outline] = {}
        self._stamp: Dict[str, float] = {}

    def _paths(self) -> List[Path]:
        if not self.templates_dir.is_dir():
            return []
        return sorted(self.templates_dir.glob("outline_*.json"))

    def all(self) -> Dict[str, Outline]:
        current = {str(path): path.stat().st_mtime for path in self._paths()}
        if current != self._stamp:
            cache: Dict[str, Outline] = {}
            for path in self._paths():
                try:
                    outline = Outline.load(path)
                except (ValueError, KeyError, json.JSONDecodeError) as error:
                    raise ServiceError(f"шаблон {path.name} повреждён: {error}", 500) from error
                cache[outline.report_type] = outline
            self._cache = cache
            self._stamp = current
        return dict(self._cache)

    def get(self, report_type: str) -> Outline:
        outlines = self.all()
        if report_type not in outlines:
            known = ", ".join(sorted(outlines)) or "нет ни одного"
            raise ServiceError(
                f"неизвестный тип отчёта '{report_type}' (доступны: {known})", 400
            )
        return outlines[report_type]


@dataclass
class ReportService:
    """Операции над кейсами и отчётами."""

    repos: Repositories
    settings: Settings
    llm: LLM | None = None
    retriever: Retriever | None = None
    glossary: Dict[str, str] = field(default_factory=dict)
    outlines: OutlineLibrary | None = None

    def __post_init__(self) -> None:
        if self.outlines is None:
            self.outlines = OutlineLibrary(self.settings.templates_dir)
        if not self.glossary:
            self.glossary = _load_glossary(self.settings.glossary_path)

    # -- вспомогательное ----------------------------------------------------

    def get_llm(self) -> LLM:
        if self.llm is None:
            self.llm = build_llm(
                self.settings.llm_kind,
                base_url=self.settings.llm_base_url,
                model=self.settings.llm_model,
                api_key=self.settings.llm_api_key,
                timeout=self.settings.llm_timeout,
                seed=self.settings.llm_seed,
            )
        return self.llm

    def get_retriever(self) -> Retriever | None:
        if self.retriever is None:
            self.retriever = _build_retriever(self.repos, self.settings)
        return self.retriever

    def reset_retriever(self) -> None:
        """Сбросить поиск после изменения библиотеки."""
        self.retriever = None

    def facts_of(self, case: Case) -> FactPack:
        try:
            return FactPack.from_dict(case.facts)
        except FactPackError as error:
            raise ServiceError(f"факт-пакет кейса некорректен: {error}", 400) from error

    def coverage(self, case: Case) -> Dict[str, List[str]]:
        """Каких обязательных измерений не хватает по шаблону (док. 04, 4.3)."""
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        return check_facts_coverage(self.facts_of(case), outline)

    # -- кейсы --------------------------------------------------------------

    def create_case(self, payload: Dict[str, Any], user: User | None) -> Case:
        raw = dict(payload.get("facts") or {})
        report_type = payload.get("report_type") or raw.get("report_type")
        if not report_type:
            raise ServiceError("не указан тип отчёта", 400)
        raw.setdefault("report_type", report_type)
        case_id = (payload.get("case_id") or raw.get("case_id") or "").strip()
        if not case_id:
            raise ServiceError("не указан идентификатор обращения", 400)
        raw["case_id"] = case_id
        if raw.get("report_type") != report_type:
            raise ServiceError(
                "тип отчёта в факт-пакете не совпадает с выбранным типом", 400
            )
        self.outlines.get(report_type)  # type: ignore[union-attr]
        if self.repos.cases.by_case_id(case_id) is not None:
            raise ServiceError(f"кейс '{case_id}' уже существует", 409)

        facts = _validate_facts(raw)
        case = self.repos.cases.create(
            case_id=case_id,
            report_type=report_type,
            facts=raw,
            digest=facts.digest(),
            title=payload.get("title", ""),
            customer=facts.customer,
            user_id=user.id if user else None,
        )
        self.repos.audit.log("case.create", user=user, object_type="case", object_id=case.case_id)
        return case

    def update_facts(self, case: Case, raw: Dict[str, Any], user: User | None) -> Case:
        raw = dict(raw)
        raw.setdefault("case_id", case.case_id)
        raw.setdefault("report_type", case.report_type)
        if raw["case_id"] != case.case_id:
            raise ServiceError("идентификатор обращения менять нельзя", 400)
        if raw["report_type"] != case.report_type:
            raise ServiceError("тип отчёта менять нельзя — создайте новый кейс", 400)
        facts = _validate_facts(raw)
        self.repos.cases.update_facts(case.id, raw, facts.digest(), customer=facts.customer)
        self.repos.audit.log(
            "case.facts.update", user=user, object_type="case", object_id=case.case_id,
            details={"digest": facts.digest()},
        )
        updated = self.repos.cases.get(case.id)
        assert updated is not None
        return updated

    # -- генерация ----------------------------------------------------------

    def generate(self, case: Case, user: User | None, *, top_k: int | None = None) -> Report:
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        result = generate_report(
            facts,
            outline,
            self.get_llm(),
            self.get_retriever(),
            top_k=top_k or self.settings.retrieval_top_k,
            index_version=self._index_version(),
        )
        registry = StoredRegistry(
            SourceRecord(
                label=label,
                chunk_uid=chunk.chunk_id,
                citation=chunk.citation,
                text=" ".join(chunk.text.split()),
            )
            for label, chunk in result.registry.items()
        )
        meta = dict(result.meta)
        meta["sources"] = registry.to_meta()
        issues = self._verify(result.markdown, facts, outline)

        report = self.repos.reports.create(
            case_ref=case.id,
            markdown=result.markdown,
            meta=meta,
            issues=issues,
            sections=[
                {
                    "section_id": section.spec.id,
                    "title": section.spec.title,
                    "text": section.text,
                    "sources": section.sources,
                    "missing_facts": section.missing_facts,
                }
                for section in result.sections
            ],
            user_id=user.id if user else None,
        )
        self.repos.cases.set_status(case.id, "draft")
        self.repos.audit.log(
            "report.generate", user=user, object_type="report", object_id=str(report.id),
            details={"case_id": case.case_id, "version": report.version,
                     "errors": report.error_count, "warnings": report.warning_count},
        )
        return report

    def regenerate_section(self, report: Report, section_id: str, user: User | None,
                           *, hint: str = "", top_k: int | None = None) -> ReportSection:
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("кейс отчёта не найден", 404)
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        spec = next((s for s in outline.sections if s.id == section_id), None)
        if spec is None:
            raise ServiceError(f"в шаблоне нет секции '{section_id}'", 404)

        if hint.strip():
            spec = _with_hint(spec, hint.strip())

        registry = StoredRegistry.from_meta(report.meta)
        previously = [
            (section.title, _summarize(section.text))
            for section in report.sections
            if section.section_id != section_id
        ]
        generated = generate_section(
            spec, facts, self.get_retriever(), self.get_llm(),
            previously=previously, registry=registry,
            top_k=top_k or self.settings.retrieval_top_k,
        )
        self.repos.reports.replace_section(
            report.id, section_id, generated.text, generated.sources, generated.missing_facts
        )
        meta = dict(report.meta)
        meta["sources"] = registry.to_meta()
        self.repos.reports.update_meta(report.id, meta)
        self.rebuild(report.id)
        self.repos.audit.log(
            "report.section.regenerate", user=user, object_type="report",
            object_id=str(report.id), details={"section": section_id, "hint": hint[:200]},
        )
        section = self.repos.reports.section(report.id, section_id)
        assert section is not None
        return section

    def save_section(self, report: Report, section_id: str, text: str,
                     user: User | None) -> ReportSection:
        section = self.repos.reports.section(report.id, section_id)
        if section is None:
            raise ServiceError(f"секция '{section_id}' не найдена", 404)
        edited = text.strip() != section.draft_text.strip()
        self.repos.reports.update_section_text(report.id, section_id, text, edited=edited)
        self.rebuild(report.id)
        self.repos.audit.log(
            "report.section.edit", user=user, object_type="report", object_id=str(report.id),
            details={"section": section_id},
        )
        updated = self.repos.reports.section(report.id, section_id)
        assert updated is not None
        return updated

    def restore_section(self, report: Report, section_id: str, user: User | None) -> ReportSection:
        """Вернуть черновик модели, отменив ручные правки секции."""
        section = self.repos.reports.section(report.id, section_id)
        if section is None:
            raise ServiceError(f"секция '{section_id}' не найдена", 404)
        self.repos.reports.update_section_text(
            report.id, section_id, section.draft_text, edited=False
        )
        self.rebuild(report.id)
        self.repos.audit.log(
            "report.section.restore", user=user, object_type="report",
            object_id=str(report.id), details={"section": section_id},
        )
        updated = self.repos.reports.section(report.id, section_id)
        assert updated is not None
        return updated

    # -- сборка и проверка --------------------------------------------------

    def rebuild(self, report_id: int) -> Report:
        """Пересобрать Markdown отчёта из текущих текстов секций и перепроверить."""
        report = self.repos.reports.get(report_id)
        if report is None:
            raise ServiceError("отчёт не найден", 404)
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("кейс отчёта не найден", 404)
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        registry = StoredRegistry.from_meta(report.meta)

        specs = {spec.id: spec for spec in outline.sections}
        generated: List[GeneratedSection] = []
        for section in report.sections:
            spec = specs.get(section.section_id)
            if spec is None:
                continue
            generated.append(
                GeneratedSection(
                    spec=spec, text=section.text,
                    sources=section.sources, missing_facts=section.missing_facts,
                )
            )
        markdown = assemble(facts, outline, generated, registry, report.meta)
        issues = self._verify(markdown, facts, outline)
        self.repos.reports.update_markdown(report.id, markdown)
        self.repos.reports.set_issues(report.id, issues)
        if report.status == "approved" and any(i["level"] == "error" for i in issues):
            # Правка сломала уже утверждённый отчёт — возвращаем его в черновики.
            self.repos.reports.set_status(report.id, "draft")
        updated = self.repos.reports.get(report.id)
        assert updated is not None
        return updated

    def verify(self, report: Report) -> List[Dict[str, Any]]:
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("кейс отчёта не найден", 404)
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        issues = self._verify(report.markdown, facts, outline)
        self.repos.reports.set_issues(report.id, issues)
        return issues

    def _verify(self, markdown: str, facts: FactPack, outline: Outline) -> List[Dict[str, Any]]:
        issues = verify_report(markdown, facts, outline, glossary=self.glossary)
        return [
            {"level": issue.level, "code": issue.code,
             "section": issue.section, "message": issue.message}
            for issue in issues
        ]

    # -- утверждение --------------------------------------------------------

    def approve(self, report: Report, user: User | None) -> Report:
        """Утвердить отчёт. Заблокировано, пока верификатор находит ошибки."""
        if report.status == "approved":
            return report
        issues = self.verify(report)
        errors = [issue for issue in issues if issue["level"] == "error"]
        if errors:
            raise ServiceError(
                f"отчёт не может быть утверждён: верификатор нашёл ошибок — {len(errors)}", 409
            )
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("кейс отчёта не найден", 404)

        pairs = self._collect_edit_pairs(report, case, user)
        self.repos.reports.approve(report.id, user.id if user else None)
        self.repos.cases.set_status(case.id, "approved")
        self.repos.audit.log(
            "report.approve", user=user, object_type="report", object_id=str(report.id),
            details={"case_id": case.case_id, "version": report.version, "edit_pairs": pairs},
        )
        updated = self.repos.reports.get(report.id)
        assert updated is not None
        return updated

    def _collect_edit_pairs(self, report: Report, case: Case, user: User | None) -> int:
        """Сохранить пары «черновик модели → финал инженера» (док. 03, 3.7)."""
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        specs = {spec.id: spec for spec in outline.sections}
        facts = self.facts_of(case)
        sources = {record["label"]: record for record in report.meta.get("sources", [])}
        saved = 0
        for section in report.sections:
            if section.text.strip() == section.draft_text.strip():
                continue
            spec = specs.get(section.section_id)
            context = {
                "header": facts.render_header(),
                "instruction": spec.instruction if spec else "",
                "title": section.title,
                "target_words": spec.target_words if spec else 0,
                "style": spec.style if spec else "",
                "facts": facts.render_measurements(
                    [*(spec.required_facts if spec else []), *(spec.optional_facts if spec else [])]
                ),
                "sources": "\n\n".join(
                    f"[{label}] {sources[label]['citation']}\n{sources[label]['text']}"
                    for label in section.sources if label in sources
                ),
            }
            self.repos.edits.add(
                case_id=case.case_id, report_id=report.id, report_type=case.report_type,
                section_id=section.section_id, section_title=section.title,
                draft=section.draft_text, final=section.text,
                facts_digest=case.facts_digest, context=context,
                user_id=user.id if user else None,
            )
            saved += 1
        return saved

    # -- прочее -------------------------------------------------------------

    def sources(self, report: Report) -> List[Dict[str, str]]:
        return [SourceRecord.from_dict(item).to_dict() for item in report.meta.get("sources", [])]

    def stats(self) -> Dict[str, Any]:
        counts = self.repos.db.counts()
        return {
            "cases": {
                "total": self.repos.cases.count(),
                "approved": self.repos.cases.count("approved"),
                "draft": self.repos.cases.count("draft"),
            },
            "reports": {
                "total": counts["reports"],
                "approved": int(self.repos.db.scalar(
                    "SELECT count(*) FROM reports WHERE status = 'approved'") or 0),
            },
            "edits": {
                "count": self.repos.edits.count(),
                "mean_distance": round(self.repos.edits.mean_distance(), 3),
                "by_section": self.repos.edits.by_section(),
            },
            "library": {
                "documents": counts["documents"],
                "chunks": counts["chunks"],
                "embeddings": self.repos.vectors.count(),
                "by_type": self.repos.documents.stats(),
            },
        }

    def _index_version(self) -> str:
        documents = self.repos.db.scalar("SELECT count(*) FROM documents") or 0
        chunks = self.repos.db.scalar("SELECT count(*) FROM chunks") or 0
        return f"docs={documents},chunks={chunks}"


# ------------------------------------------------------------- служебное ---

def _validate_facts(raw: Dict[str, Any]) -> FactPack:
    try:
        return FactPack.from_dict(raw)
    except FactPackError as error:
        raise ServiceError(f"факт-пакет некорректен: {error}", 400) from error


def _summarize(text: str, limit: int = 220) -> str:
    flat = " ".join(text.split())
    return flat[:limit].rstrip() + ("…" if len(flat) > limit else "")


def _with_hint(spec: Any, hint: str) -> Any:
    """Копия описания секции с пожеланием инженера, добавленным к инструкции."""
    from dataclasses import replace

    return replace(spec, instruction=f"{spec.instruction}\n\nДополнительно: {hint}")


def _load_glossary(path: Path) -> Dict[str, str]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _build_retriever(repos: Repositories, settings: Settings) -> Retriever | None:
    """Гибридный поиск, если модуль доступен; иначе — лексический по базе."""
    try:
        from ..search import build_retriever  # noqa: PLC0415
    except ImportError:
        chunks = repos.chunks.all_chunks()
        return Retriever(BM25Index(chunks)) if chunks else None
    return build_retriever(repos, settings)
