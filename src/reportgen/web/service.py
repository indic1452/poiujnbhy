"""Сервисный слой: вся логика работы с кейсами и отчётами.

Веб-обработчики (:mod:`reportgen.web.api`) остаются тонкими — они разбирают
запрос и вызывают методы отсюда. Благодаря этому та же логика доступна из CLI
и из тестов без поднятия HTTP-сервера.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ..config import Settings
from ..corpus import Chunk
from ..facts import FactPack, FactPackError
from ..llm import LLM, build_llm
from ..pipeline import (
    PROMPT_QUOTE_CHARS,
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
from .quality import QualityChecker
from .vectors import VectorIndexer
from ..verify import summarize, verify_report

#: Предел на замечание проверяющего: это строка «что исправить», а не второй
#: отчёт. Длиннее — значит, разговор не для карточки письма.
MAX_REVIEW_NOTE = 2000

#: Пределы строк карточки письма. Это строки журнала входящих и исходящих,
#: а не текст отчёта: тема в пять тысяч знаков ломает и список писем, и
#: шапку документа. Предел один на всю систему — веб-слой и поля формы
#: берут его отсюда, иначе форма примет то, что сервер потом отвергнет.
CARD_LIMITS = {"title": 300, "incoming_no": 60, "outgoing_no": 60,
               "tc_no": 60, "order_no": 60, "note": 2000,
               "outgoing_note": 2000}
MAX_OUTGOING_NO = CARD_LIMITS["outgoing_no"]

#: Предел обрезки цитаты в приложении к отчёту. Он обязан совпадать с тем,
#: что видела модель, иначе верификатор блокирует число, законно взятое из
#: хвоста фрагмента. Значение одно и живёт в pipeline.
APPENDIX_QUOTE_CHARS = PROMPT_QUOTE_CHARS


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
        self._lock = threading.Lock()

    @classmethod
    def from_meta(cls, meta: Dict[str, Any]) -> "StoredRegistry":
        return cls(SourceRecord.from_dict(item) for item in meta.get("sources", []))

    def label(self, chunk: Chunk) -> str:
        with self._lock:
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
    vectors: "VectorIndexer | None" = None
    quality: "QualityChecker | None" = None

    def __post_init__(self) -> None:
        if self.outlines is None:
            self.outlines = OutlineLibrary(self.settings.templates_dir)
        if not self.glossary:
            self.glossary = _load_glossary(self.settings.glossary_path)
        if self.vectors is None:
            self.vectors = VectorIndexer(self.repos, self.settings)
        if self.quality is None:
            self.quality = QualityChecker(self.repos)

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
            # Поисковику показываем, идёт ли постройка векторов. Пока идёт,
            # он не перечитывает матрицу на каждый запрос: число векторов
            # меняется после каждой пачки, а разбор вопроса делает несколько
            # поисков подряд — библиотека распаковывалась бы из BLOB заново
            # на каждый заход.
            if self.retriever is not None and self.vectors is not None:
                if hasattr(self.retriever, "vectors_building"):
                    self.retriever.vectors_building = self.vectors.running
        return self.retriever

    def reset_retriever(self) -> None:
        """Сбросить поиск после изменения библиотеки."""
        self.retriever = None

    def facts_of(self, case: Case) -> FactPack:
        try:
            return FactPack.from_dict(case.facts)
        except FactPackError as error:
            raise ServiceError(f"факт-пакет письма некорректен: {error}", 400) from error

    def coverage(self, case: Case) -> Dict[str, List[str]]:
        """Каких обязательных измерений не хватает по шаблону (док. 04, 4.3)."""
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        # Разделы по описи разворачивает сама проверка полноты: ей нужен
        # шаблон, а не готовый план.
        return check_facts_coverage(self.facts_of(case), outline)

    # -- кейсы --------------------------------------------------------------

    def facts_skeleton(self, report_type: str, case_id: str) -> Dict[str, Any]:
        """Пустой факт-пакет по шаблону: ключи есть, значений ещё нет.

        Собирает его система, а не человек. При регистрации письмо только
        спустили, чисел в нём нет, и заставлять регистратора писать JSON —
        значит требовать от него того, чего он знать не может. Готовый
        пакет из приборного разбора кладут отдельным действием.
        """
        outline = self.outlines.get(report_type)  # type: ignore[union-attr]
        keys: List[str] = []
        for section in outline.sections:
            for key in getattr(section, "required_facts", ()) or ():
                if key not in keys:
                    keys.append(key)
        return {
            "case_id": case_id,
            "report_type": report_type,
            "group_no": "",
            "request": "",
            "equipment": {},
            "keywords": [],
            "artifacts": [],
            # Название и единицу берём из шаблона. Раньше в названии стоял
            # сам ключ, и человек видел в таблице «packet_count» дважды —
            # ни что заносить, ни в чём, из этого не следовало.
            "measurements": {
                key: {"title": outline.fact_title(key), "value": "",
                      "unit": outline.fact_units.get(key, ""),
                      "method": "", "uncertainty": ""} for key in keys
            },
            "findings": [],
            "timeline": [],
        }

    def default_report_type(self) -> str:
        """Тип отчёта, если его не выбирали руками.

        Шаблон нужен, чтобы вообще собрать отчёт, но при регистрации письма
        его не спрашивают: там речь про линию связи и номер средства. Берём
        первый по алфавиту — при единственном шаблоне выбора и нет, а при
        нескольких инженер сменит его в карточке, когда сядет за отчёт.
        """
        outlines = sorted(self.outlines.all())  # type: ignore[union-attr]
        if not outlines:
            raise ServiceError(
                "нет ни одного шаблона-плана: положите файл "
                "templates/outline_<тип>.json", 500)
        return outlines[0]

    def create_case(self, payload: Dict[str, Any], user: User | None) -> Case:
        raw = dict(payload.get("facts") or {})
        report_type = (payload.get("report_type") or raw.get("report_type")
                       or self.default_report_type())
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
            raise ServiceError(f"письмо '{case_id}' уже зарегистрировано", 409)

        # Номер группы берём из факт-пакета, а если его там нет — из формы:
        # при регистрации письма факт-пакет обычно ещё пустой. Номер из формы
        # кладём и в сам пакет: оттуда он попадает в шапку отчёта и в промпт
        # модели. Раньше он оставался только колонкой письма, и отчёт выходил
        # с прочерком вместо номера, хотя инженер его вводил. Правка карточки
        # (PATCH) синхронизировала оба места, а регистрация — нет.
        if not facts_group_no(raw):
            from_form = str(payload.get("group_no", payload.get("customer", ""))).strip()
            if from_form:
                raw["group_no"] = from_form

        # Пакета не прислали — собираем заготовку по шаблону сами.
        if not raw.get("measurements") and "measurements" not in raw:
            skeleton = self.facts_skeleton(report_type, case_id)
            skeleton.update(raw)
            skeleton["group_no"] = raw.get("group_no", skeleton["group_no"])
            raw = skeleton

        facts = _validate_facts(raw)
        try:
            case = self.repos.cases.create(
                case_id=case_id,
                report_type=report_type,
                facts=raw,
                digest=facts.digest(),
                title=payload.get("title", ""),
                customer=facts.group_no,
                user_id=user.id if user else None,
                incoming_no=str(payload.get("incoming_no", "")).strip(),
                incoming_date=str(payload.get("incoming_date", "")).strip(),
                deadline=str(payload.get("deadline", "")).strip(),
                priority=str(payload.get("priority") or "normal"),
                assignee_id=payload.get("assignee_id") or None,
                note=str(payload.get("note", "")).strip(),
                line_type=str(payload.get("line_type", "")).strip(),
                tc_no=str(payload.get("tc_no", "")).strip(),
                tc_date=str(payload.get("tc_date", "")).strip(),
                order_no=str(payload.get("order_no", "")).strip(),
                order_date=str(payload.get("order_date", "")).strip(),
                registrations=int(payload.get("registrations") or 0),
            )
        except sqlite3.IntegrityError as error:
            # Проверка «такое письмо уже есть» выше по коду ловит обычный
            # случай, но не гонку: двойное нажатие или два человека разом
            # доходили до вставки одновременно, и второй получал срыв
            # сервера вместо понятного отказа.
            raise ServiceError(f"письмо '{case_id}' уже зарегистрировано", 409) from error
        self.repos.case_search.refresh(case.id)
        self.repos.audit.log("case.create", user=user, object_type="case", object_id=case.case_id)
        return case

    def update_facts(self, case: Case, raw: Dict[str, Any], user: User | None) -> Case:
        raw = dict(raw)
        raw.setdefault("case_id", case.case_id)
        raw.setdefault("report_type", case.report_type)
        if raw["case_id"] != case.case_id:
            raise ServiceError("идентификатор обращения менять нельзя", 400)
        if raw["report_type"] != case.report_type:
            raise ServiceError("тип отчёта менять нельзя — зарегистрируйте новое письмо", 400)
        facts = _validate_facts(raw)
        # Ответ ушёл — числа в отчёте обязаны совпадать с отправленным.
        # Правка исходных данных сняла бы подпись и развернула письмо в
        # работу, хотя бумага уже у адресата.
        self.guard_not_sent(case, "править исходные данные")
        self.repos.cases.update_facts(case.id, raw, facts.digest(), customer=facts.group_no)
        # Изменились исходные данные — значит изменилось и множество чисел,
        # которые отчёт имеет право называть. Все отчёты кейса перепроверяются
        # сразу, иначе подписанный документ остался бы «утверждённым» с числами,
        # которых в факт-пакете больше нет.
        revoked = self.revalidate_case(case.id)
        self.repos.case_search.refresh(case.id)
        self.repos.audit.log(
            "case.facts.update", user=user, object_type="case", object_id=case.case_id,
            details={"digest": facts.digest(), "revoked": revoked},
        )
        updated = self.repos.cases.get(case.id)
        assert updated is not None
        return updated

    def update_card(self, case: Case, fields: Dict[str, Any],
                    user: User | None) -> Case | None:
        """Правка карточки письма.

        Отправитель живёт в двух местах — колонкой письма и полем
        факт-пакета, откуда он попадает в шапку отчёта. Запись шла прямо в
        базу, мимо :meth:`update_facts`: хеш пакета оставался от прежнего
        содержимого, а подписанные отчёты не перепроверялись.
        """
        # Номер группы уходит в шапку отчёта. У отправленного письма отчёт
        # обязан совпадать с тем, что ушло, — номер группы правке не
        # подлежит. Срок, важность, примечание и архив трогать можно.
        if "customer" in fields and fields["customer"] != case.customer:
            self.guard_not_sent(case, "менять номер группы")
        updated = self.repos.cases.update_card(case.id, **fields)
        # Описание, номера и примечание попадают в поиск: указатель надо
        # пересобрать, иначе письмо ищется по прежнему названию.
        self.repos.case_search.refresh(case.id)
        if updated is None or "customer" not in fields:
            return updated
        facts = _validate_facts(dict(updated.facts))
        self.repos.cases.update_facts(case.id, dict(updated.facts), facts.digest())
        self.revalidate_case(case.id)
        self.repos.case_search.refresh(case.id)
        return self.repos.cases.get(case.id)

    def revalidate_case(self, case_ref: int) -> int:
        """Перепроверить все отчёты кейса. Возвращает число снятых подписей."""
        case = self.repos.cases.get(case_ref)
        if case is None:
            return 0
        try:
            facts = self.facts_of(case)
            outline = self.outlines.get(case.report_type).expanded(facts)  # type: ignore[union-attr]
        except ServiceError:
            return 0
        revoked = 0
        for stored in self.repos.reports.list_for_case(case_ref):
            report = self.repos.reports.get(stored.id)
            # Сданный файлом отчёт с факт-пакетом не связан: проверять
            # в нём нечего, замечания были бы выдуманными.
            if report is None or report.source == "uploaded":
                continue
            sections, appendix = self._parts_of(report)
            issues = self._verify(report.markdown, facts, outline,
                                  sections=sections, appendix=appendix)
            was_approved = report.status == "approved"
            self._apply_issues(report, issues)
            if was_approved and any(issue["level"] == "error" for issue in issues):
                revoked += 1
        return revoked

    # -- генерация ----------------------------------------------------------

    def generate(self, case: Case, user: User | None, *, top_k: int | None = None) -> Report:
        self.guard_not_sent(case, "собрать отчёт заново")
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type)  # type: ignore[union-attr]
        result = generate_report(
            facts,
            outline,
            self.get_llm(),
            self.get_retriever(),
            top_k=top_k or self.settings.retrieval_top_k,
            index_version=self._index_version(),
            parallel_sections=self.settings.llm_parallel_sections,
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
        issues = self._verify(
            result.markdown, facts, outline,
            sections=[(item.spec.title, item.text) for item in result.sections],
            appendix=registry.render_appendix(),
        )

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
        self.withdraw_previous(case, report, user)
        self.repos.cases.set_status(case.id, "draft")
        self.repos.case_search.refresh(case.id)
        self.repos.audit.log(
            "report.generate", user=user, object_type="report", object_id=str(report.id),
            details={"case_id": case.case_id, "version": report.version,
                     "errors": report.error_count, "warnings": report.warning_count},
        )
        return report

    def withdraw_previous(self, case: Case, fresh: Report, user: User | None) -> None:
        """Снять с проверки прежние редакции отчёта по письму.

        Исполнитель собирал новую версию, пока начальник читал прежнюю:
        письмо возвращалось «в работу» и пропадало из очереди проверки, а
        старый отчёт оставался помеченным «на проверке» — начальник его в
        списке уже не находил, а отчёт числился сданным. Сдаёт исполнитель,
        и сдаёт он ту версию, которую считает готовой: новая сборка (или
        новая сдача файлом) означает, что прежнюю он отозвал.
        """
        for stored in self.repos.reports.list_for_case(case.id):
            # Отзываем только то, что старше сданного. Иначе две сдачи,
            # пришедшие разом, снимают друг друга, и на проверке не
            # остаётся ничего, хотя письмо помечено «на проверке».
            if stored.id == fresh.id or stored.status != "review":
                continue
            if stored.version > fresh.version:
                continue
            self.repos.reports.set_status(stored.id, "draft", note="")
            self.repos.audit.log(
                "report.withdraw", user=user, object_type="report",
                object_id=str(stored.id),
                details={"case_id": case.case_id, "version": stored.version,
                         "reason": "собрана редакция " + str(fresh.version)},
            )

    def regenerate_section(self, report: Report, section_id: str, user: User | None,
                           *, hint: str = "", top_k: int | None = None) -> ReportSection:
        case = self.guard_current(report, "переписывать разделы")
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type).expanded(facts)  # type: ignore[union-attr]
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
        # Перегенерация — такая же правка текста, как правка руками: подпись
        # стоит под тем, что прочитал проверяющий. Здесь этого не было, и
        # начальник оставался утвердившим текст, которого не видел.
        self._unsign(report, "section.regenerate", section_id)
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
        self.guard_current(report, "править разделы")
        section = self.repos.reports.section(report.id, section_id)
        if section is None:
            raise ServiceError(f"секция '{section_id}' не найдена", 404)
        edited = text.strip() != section.draft_text.strip()
        self.repos.reports.update_section_text(report.id, section_id, text, edited=edited)
        self._unsign(report, "section.edit", section_id)
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
        self.guard_current(report, "возвращать черновик модели")
        section = self.repos.reports.section(report.id, section_id)
        if section is None:
            raise ServiceError(f"секция '{section_id}' не найдена", 404)
        self.repos.reports.update_section_text(
            report.id, section_id, section.draft_text, edited=False
        )
        self._unsign(report, "section.restore", section_id)
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
        # Загруженный отчёт написан человеком целиком: ни секций шаблона, ни
        # факт-пакета за ним нет, пересобирать нечего и не из чего.
        if report.source == "uploaded":
            return report
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("письмо, к которому относится отчёт, не найдено", 404)
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type).expanded(facts)  # type: ignore[union-attr]
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
        # Состояние документа идёт в шапку: утверждённый отчёт не должен
        # уходить с надписью «ЧЕРНОВИК, требует подписи».
        head = {**report.meta, **self._signature(report)}
        markdown = assemble(facts, outline, generated, registry, head)
        issues = self._verify(
            markdown, facts, outline,
            sections=[(item.spec.title, item.text) for item in generated],
            appendix=registry.render_appendix(),
        )
        # Второй проход — ради одной строки шапки: сколько ошибок нашлось,
        # известно только после проверки. Сборка это склейка строк, дорого
        # не стоит, а черновик уходит на печать с честной отметкой.
        errors = sum(1 for issue in issues if issue.get("level") == "error")
        if errors != int(head.get("errors") or 0):
            markdown = assemble(facts, outline, generated, registry,
                                {**head, "errors": errors})
        self.repos.reports.update_markdown(report.id, markdown)
        self._apply_issues(report, issues)
        # Текст отчёта попал в поиск: пересборка идёт после каждой правки
        # раздела, и это единственное место, где текст меняется целиком.
        self.repos.case_search.refresh(report.case_ref)
        updated = self.repos.reports.get(report.id)
        assert updated is not None
        return updated

    def facts_are_stale(self, report: Report) -> bool:
        """Собран ли отчёт по прежней редакции исходных данных.

        Шапка документа (обращение, номер группы, оборудование, дата) пишется
        при сборке и остаётся в тексте как есть. Правка факт-пакета
        перепроверяет числа в разделах, но шапку не переписывает — и правильно
        делает: пересборка сменила бы текст под подписью. Значит, расхождение
        надо не прятать, а показывать: карточка письма уже показывает новые
        данные, а в документе стоят прежние.

        Сверяем хеш факт-пакета, записанный при сборке, с нынешним.
        """
        if report.source == "uploaded":
            return False
        made_with = str(report.meta.get("facts_digest") or "")
        if not made_with:
            return False
        case = self.repos.cases.get(report.case_ref)
        if case is None or not case.facts_digest:
            return False
        return made_with != case.facts_digest

    def for_export(self, report: Report) -> Report:
        """Отчёт с шапкой, отвечающей его состоянию.

        Шапка пишется при сборке, а состояние меняется позже: отчёт
        утверждают, проверяют, правят. Утверждённые до появления подписи
        уходили на проверку с отметкой «ЧЕРНОВИК», а свежий черновик — без
        числа несведённых ошибок. Текст отчёта величина производная, он
        пересобирается из секций при каждой правке, поэтому здесь его
        достаточно пересобрать, а не хранить особым образом.
        """
        from ..pipeline import status_line  # noqa: PLC0415 — только для сверки

        # Загруженный отчёт собирал человек: пересобирать в нём нечего,
        # а шапку ему система не писала и дописывать не станет.
        if report.source == "uploaded":
            return report
        expected = status_line({**self._signature(report), "errors": report.error_count})
        if expected in report.markdown:
            return report
        return self.rebuild(report.id)

    def _signature(self, report: Report) -> Dict[str, Any]:
        """Кто и когда подписал отчёт — для шапки документа."""
        signature: Dict[str, Any] = {
            "status": report.status,
            "approved_at": report.approved_at or "",
            "approved_by_name": "",
        }
        if report.approved_by:
            who = self.repos.users.get(report.approved_by)
            if who is not None:
                signature["approved_by_name"] = who.full_name or who.login
        return signature

    def verify(self, report: Report) -> List[Dict[str, Any]]:
        """Сверка отчёта с факт-пакетом. Возвращает замечания верификатора.

        У сданного файлом отчёта факт-пакета нет: его писали не здесь и не по
        нашему шаблону. Прогонять такой файл через проверку разделов
        бессмысленно — она честно докладывала, что «обязательный раздел
        отсутствует», перечисляя разделы чужого шаблона, взятого письму
        по умолчанию. Сверять нечего: замечаний нет, читает начальник.
        """
        if report.source == "uploaded":
            return []
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("письмо, к которому относится отчёт, не найдено", 404)
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type).expanded(facts)  # type: ignore[union-attr]
        sections, appendix = self._parts_of(report)
        issues = self._verify(report.markdown, facts, outline,
                              sections=sections, appendix=appendix)
        self._apply_issues(report, issues)
        return issues

    def _verify(self, markdown: str, facts: FactPack, outline: Outline,
                *, sections: List[tuple[str, str]] | None = None,
                appendix: str | None = None) -> List[Dict[str, Any]]:
        """Проверка отчёта. Секции и приложение берутся из базы, если они есть.

        Разбор Markdown — крайний случай: границы разделов в документе задаёт
        текст, который писали модель и инженер, а значит их можно подделать.
        Тексты секций и список источников в базе сформировал код.
        """
        issues = verify_report(
            markdown, facts, outline, glossary=self.glossary,
            sections=sections, appendix=appendix,
        )
        return [
            {"level": issue.level, "code": issue.code,
             "section": issue.section, "message": issue.message}
            for issue in issues
        ]

    def _unsign(self, report: Report, reason: str, section_id: str = "") -> None:
        """Снять подпись с отчёта, текст которого изменили.

        Подпись стоит под тем текстом, который человек прочитал. Раньше её
        снимал только верификатор — если правка добавляла число мимо
        факт-пакета. Правка, ошибок не добавившая, оставляла отчёт
        утверждённым: в шапке «Утвердил: Иванов», а раздел переписан после
        него и, возможно, не им. Утвердить заново — одно нажатие, а вот
        отличить подписанный документ от подправленного после подписи было
        нельзя ничем.
        """
        if report.status not in ("approved", "review"):
            return
        if self._is_sent(report):
            return
        self.repos.reports.set_status(report.id, "draft")
        # Текст под подписью изменили — пары «черновик модели → финал
        # инженера», собранные при утверждении, описывают уже не тот
        # документ, который прочитал начальник. Соберут заново при новом
        # утверждении (док. 03, 3.7).
        dropped = self.repos.edits.drop_for_report(report.id) if report.status == "approved" else 0
        # Письмо возвращаем вместе с отчётом. Иначе оно числилось бы
        # отправленным или лежащим у начальника, а отчёт по нему — черновик:
        # в списке писем одно, в карточке другое.
        case = self.repos.cases.get(report.case_ref)
        if case is not None and case.status in ("checked", "review"):
            self.repos.cases.set_status(case.id, "draft")
        self.repos.audit.log(
            "report.approval.revoked", object_type="report", object_id=str(report.id),
            details={"reason": reason, "section": section_id, "was": report.status,
                     "edit_pairs_dropped": dropped},
        )

    def _apply_issues(self, report: Report, issues: List[Dict[str, Any]]) -> None:
        """Сохранить замечания и снять подпись, если появились ошибки.

        Утверждённый отчёт не может оставаться утверждённым, когда верификатор
        находит в нём число мимо факт-пакета: это главный инвариант системы
        (док. 01, 1.4.1), и он не должен зависеть от того, каким путём отчёт
        стал неверным — правкой секции или изменением исходных данных.
        """
        self.repos.reports.set_issues(report.id, issues)
        has_errors = any(issue["level"] == "error" for issue in issues)
        # Ответ по письму уже ушёл — снимать отметку нельзя: отзыв подписи
        # не отзывает бумагу у адресата, а система начинает врать —
        # «отправлено» и «черновик» одновременно. Замечания сохраняем и
        # показываем: отдел сам решит, отзывать ли отправку и исправлять.
        if has_errors and self._is_sent(report):
            self.repos.audit.log(
                "report.errors.after.sending", object_type="report",
                object_id=str(report.id),
                details={"errors": sum(1 for i in issues if i["level"] == "error")},
            )
            return
        if has_errors and report.status == "approved":
            self.repos.reports.set_status(report.id, "draft")
            # Подпись снята — значит, в тексте есть число мимо факт-пакета.
            # Пары «черновик → финал», собранные при утверждении, учат
            # ровно этому тексту: убираем вместе с подписью.
            dropped = self.repos.edits.drop_for_report(report.id)
            self.repos.audit.log(
                "report.approval.revoked", object_type="report", object_id=str(report.id),
                details={"errors": sum(1 for i in issues if i["level"] == "error"),
                         "edit_pairs_dropped": dropped},
            )

    def _is_sent(self, report: Report) -> bool:
        """Ушёл ли ответ по письму этого отчёта под исходящим номером."""
        case = self.repos.cases.get(report.case_ref)
        return bool(case is not None and case.outgoing_no)

    def _parts_of(self, report: Report) -> tuple[List[tuple[str, str]], str]:
        """Тексты секций и приложение источников из базы — то, что проверяем."""
        sections = [(section.title, section.text) for section in report.sections]
        appendix = StoredRegistry.from_meta(report.meta).render_appendix()
        return sections, appendix

    # -- утверждение --------------------------------------------------------

    def submit(self, report: Report, user: User | None) -> Report:
        """Отправить отчёт на проверку начальнику.

        Может любой сотрудник — свои отчёты в отдел сдают все. Собранный
        системой отчёт с ошибками верификатора не отправляем: начальнику
        незачем ловить числа, которых нет в исходных данных, это работа
        машины. У загруженного файлом отчёта факт-пакета нет, и сверять
        нечего — он уходит на проверку как есть.
        """
        if report.status == "review":
            return report
        if report.status == "approved":
            raise ServiceError("отчёт уже проверен", 409)
        self.guard_current(report, "сдать отчёт на проверку")
        if report.source != "uploaded":
            errors = [i for i in self.verify(report) if i["level"] == "error"]
            if errors:
                raise ServiceError(
                    "нельзя отправить на проверку: верификатор нашёл ошибок — "
                    f"{len(errors)}", 409)
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("письмо, к которому относится отчёт, не найдено", 404)
        self.repos.reports.set_status(report.id, "review", note="")
        self.repos.cases.set_status(case.id, "review")
        self.repos.audit.log(
            "report.submit", user=user, object_type="report", object_id=str(report.id),
            details={"case_id": case.case_id, "version": report.version},
        )
        updated = self.repos.reports.get(report.id)
        assert updated is not None
        return updated

    def send_back(self, report: Report, note: str, user: User | None) -> Report:
        """Вернуть отчёт исполнителю с замечанием. Только для проверяющего."""
        if report.status not in ("review", "approved"):
            raise ServiceError("возвращать можно отчёт, отправленный на проверку", 409)
        self.guard_current(report, "вернуть отчёт на исправление")
        note = " ".join(str(note or "").split())
        if not note:
            raise ServiceError("напишите, что именно исправить", 400)
        if len(note) > MAX_REVIEW_NOTE:
            raise ServiceError(f"замечание длиннее {MAX_REVIEW_NOTE} знаков", 400)
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("письмо, к которому относится отчёт, не найдено", 404)
        # Отчёт был отмечен проверенным, а теперь возвращён: пары «черновик
        # модели → финал инженера» с того утверждения ушли в обучающий набор
        # (док. 03, 3.7). Начальник сказал, что текст негоден, — учить на нём
        # как на образце нельзя. Пары снимаем, при новом утверждении их
        # соберут заново из исправленного текста.
        dropped = 0
        if report.status == "approved":
            dropped = self.repos.edits.drop_for_report(report.id)
        self.repos.reports.set_status(report.id, "rework", note=note)
        self.repos.cases.set_status(case.id, "draft")
        self.repos.audit.log(
            "report.rework", user=user, object_type="report", object_id=str(report.id),
            details={"case_id": case.case_id, "version": report.version, "note": note,
                     "edit_pairs_dropped": dropped},
        )
        updated = self.repos.reports.get(report.id)
        assert updated is not None
        return updated

    def approve(self, report: Report, user: User | None) -> Report:
        """Отметить отчёт проверенным. Проверяет начальник отдела или его зам.

        Заблокировано, пока верификатор находит ошибки в собранном системой
        отчёте: подпись означает, что документ прочитан и годен, а не что
        на него закрыли глаза.
        """
        if report.status == "approved":
            return report
        # Проверяют то, что сдали. Отчёт, лежащий в работе у исполнителя,
        # или возвращённый ему же, начальник отмечать проверенным не должен:
        # исполнитель ещё не сказал, что закончил.
        if report.status != "review":
            raise ServiceError(
                "отчёт не отправлен на проверку: отметить проверенным нечего", 409)
        self.guard_current(report, "отметить отчёт проверенным")
        if report.source != "uploaded":
            issues = self.verify(report)
            errors = [issue for issue in issues if issue["level"] == "error"]
            if errors:
                raise ServiceError(
                    f"отчёт не может быть проверен: верификатор нашёл ошибок — {len(errors)}",
                    409,
                )
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("письмо, к которому относится отчёт, не найдено", 404)

        pairs = self._collect_edit_pairs(report, case, user)
        self.repos.reports.approve(report.id, user.id if user else None)
        # «Проверен» — ещё не «отправлено». Начальник прочитал и согласился;
        # ответ по письму отправляет исполнитель и записывает исходящий
        # номер. Пока номера нет, письмо в отделе не закрыто.
        self.repos.cases.set_status(case.id, "checked")
        # Пересобираем текст: в шапке стоит «ЧЕРНОВИК», а отчёт уже подписан.
        self.rebuild(report.id)
        self.repos.audit.log(
            "report.approve", user=user, object_type="report", object_id=str(report.id),
            details={"case_id": case.case_id, "version": report.version, "edit_pairs": pairs},
        )
        updated = self.repos.reports.get(report.id)
        assert updated is not None
        return updated

    # -- отправка ответа ----------------------------------------------------

    def send_out(self, case: Case, outgoing_no: str, outgoing_date: str,
                 user: User | None, outgoing_note: str = "") -> Case:
        """Записать исходящий номер: ответ по письму ушёл адресату.

        Последний шаг порядка отдела. Отчёт проверен начальником — дальше
        исполнитель отправляет ответ и записывает, под каким исходящим
        номером тот ушёл. Без этой записи в учёте нет главного: письмо
        пришло, работа сделана, а чем ответили — неизвестно.

        Отправляет исполнитель, а не проверяющий: сдают и отправляют все.
        """
        # Письмо без единого отчёта — это ответ мимо системы: составили в
        # Word, отправили, отчёт сюда не заводили. Исходящий номер всё
        # равно должен попасть в базу, иначе в учёте отдела дыра.
        report = self.repos.reports.current_for_case(case.id)
        if report is not None and report.status != "approved":
            raise ServiceError(
                "отчёт ещё не проверен начальником — отправлять рано", 409)
        if case.outgoing_no:
            raise ServiceError(
                f"ответ уже отправлен под номером «{case.outgoing_no}»", 409)
        outgoing_no = " ".join(str(outgoing_no or "").split())
        if not outgoing_no:
            raise ServiceError("укажите исходящий номер, под которым ушёл ответ", 400)
        if len(outgoing_no) > MAX_OUTGOING_NO:
            raise ServiceError(f"исходящий номер: длиннее {MAX_OUTGOING_NO} знаков", 400)

        updated = self.repos.cases.update_card(
            case.id, outgoing_no=outgoing_no, outgoing_date=outgoing_date,
            outgoing_note=str(outgoing_note or "").strip()[:CARD_LIMITS["note"]],
            sent_by=user.id if user else None, status="approved")
        self.repos.case_search.refresh(case.id)
        self.repos.audit.log(
            "case.send", user=user, object_type="case", object_id=case.case_id,
            details={"outgoing_no": outgoing_no, "outgoing_date": outgoing_date,
                     "report_version": report.version if report else 0},
        )
        assert updated is not None
        return updated

    def withdraw_sending(self, case: Case, user: User | None) -> Case:
        """Отозвать отправку: исходящий номер вписали не тот или не тому.

        Право проверяющего, а не исполнителя: запись об отправке — учётная,
        и снимать её должен тот же, кто отвечает за проверку. Письмо
        возвращается в «проверен, к отправке», отчёт остаётся проверенным.
        """
        if not case.outgoing_no:
            raise ServiceError("ответ по этому письму ещё не отправляли", 409)
        was = case.outgoing_no
        updated = self.repos.cases.update_card(
            case.id, outgoing_no="", outgoing_date="", sent_by=None,
            status="checked")
        self.repos.case_search.refresh(case.id)
        self.repos.audit.log(
            "case.send.withdraw", user=user, object_type="case",
            object_id=case.case_id, details={"was": was},
        )
        assert updated is not None
        return updated

    def guard_not_sent(self, case: Case, what: str) -> None:
        """Отправленное письмо не правят.

        Ответ ушёл адресату под исходящим номером — значит, отчёт в системе
        обязан совпадать с тем, что отправили. Понадобилось исправить —
        начальник сначала отзывает отправку, и это видно в журнале.
        """
        if case.outgoing_no:
            raise ServiceError(
                f"ответ по письму отправлен под номером «{case.outgoing_no}»: "
                f"{what} нельзя. Чтобы вернуться к работе, начальник отдела "
                "отзывает отправку", 409)

    def guard_current(self, report: Report, what: str) -> Case:
        """У письма один отчёт. Работают с ним, а не с прежней редакцией.

        Прежние редакции остаются в базе как история: по ним видно, что
        начальник вернул и что исполнитель поправил. Но править, сдавать и
        отмечать проверенным можно только нынешнюю — иначе в отделе два
        отчёта по одному письму, и неизвестно, который ушёл адресату.

        Заодно проверяет, что ответ по письму ещё не отправлен: после
        исходящего номера отчёт обязан совпадать с тем, что ушло.
        """
        case = self.repos.cases.get(report.case_ref)
        if case is None:
            raise ServiceError("письмо, к которому относится отчёт, не найдено", 404)
        current = self.repos.reports.current_for_case(case.id)
        if current is not None and current.id != report.id:
            raise ServiceError(
                f"это прежняя редакция отчёта (№{report.version} из "
                f"{current.version}): {what} можно только в нынешней", 409)
        self.guard_not_sent(case, what)
        return case

    def _collect_edit_pairs(self, report: Report, case: Case, user: User | None) -> int:
        """Сохранить пары «черновик модели → финал инженера» (док. 03, 3.7)."""
        facts = self.facts_of(case)
        outline = self.outlines.get(case.report_type).expanded(facts)  # type: ignore[union-attr]
        specs = {spec.id: spec for spec in outline.sections}
        sources = {record["label"]: record for record in report.meta.get("sources", [])}
        # Отчёт могли утвердить, поправить и утвердить снова. В наборе
        # остаётся только последнее утверждение: прежнее содержит вариант,
        # который инженер сам же и забраковал.
        self.repos.edits.drop_for_report(report.id)
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
                "by_domain": self.repos.documents.domains(),
            },
        }

    def _index_version(self) -> str:
        documents = self.repos.db.scalar("SELECT count(*) FROM documents") or 0
        chunks = self.repos.db.scalar("SELECT count(*) FROM chunks") or 0
        return f"docs={documents},chunks={chunks}"


# ------------------------------------------------------------- служебное ---

def facts_group_no(raw: Dict[str, Any]) -> str:
    """Номер группы, записанный в самом факт-пакете (или под прежним ключом)."""
    return str(raw.get("group_no") or raw.get("customer") or "").strip()


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
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _build_retriever(repos: Repositories, settings: Settings) -> Retriever | None:
    """Гибридный поиск, если модуль доступен; иначе — лексический по базе."""
    try:
        from ..search import build_retriever  # noqa: PLC0415
    except ImportError:
        chunks = repos.chunks.all_chunks()
        if not chunks:
            return None
        return Retriever(BM25Index(chunks),
                         terms_path=getattr(settings, "terms_path", None))
    return build_retriever(repos, settings)
