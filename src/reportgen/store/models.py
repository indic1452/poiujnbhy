"""Модели строк базы. Тонкие dataclass-обёртки над sqlite3.Row.

Хранилище намеренно не тянет ORM: таблиц немного, запросы простые, а явный SQL
проще сопровождать инженеру, который придёт после автора системы.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

#: Штатные должности. Идентификатор в базе — латиницей, потому что по нему
#: сверяются права в коде; человеку везде показывается название из ROLE_TITLES.
#:
#: owner    — создатель системы: полные права, включая разжалование других
#:            администраторов и безвозвратное удаление сотрудников;
#: head     — начальник отдела;
#: deputy   — заместитель начальника отдела;
#: lead     — начальник группы;
#: senior   — старший инженер отдела;
#: engineer — инженер отдела.
ROLES = ("owner", "head", "deputy", "lead", "senior", "engineer")

ROLE_TITLES = {
    "owner": "Создатель системы",
    "head": "Начальник отдела",
    "deputy": "Заместитель начальника отдела",
    "lead": "Начальник группы",
    "senior": "Старший инженер отдела",
    "engineer": "Инженер отдела",
}

ROLE_NOTES = {
    "owner": "Полные права: проверяет отчёты, ведёт сотрудников, библиотеку, "
             "журнал и настройки. Может менять должность любому. Отключить "
             "или разжаловать самого создателя нельзя.",
    "head": "Проверяет отчёты отдела: отмечает проверенными или возвращает "
            "с замечанием. Ведёт письма, заводит и отключает сотрудников, "
            "видит нагрузку всего отдела.",
    "deputy": "То же, что начальник отдела, включая проверку отчётов.",
    "lead": "Ведёт письма и готовит отчёты, заводит и отключает сотрудников "
            "своей группы. Отчёты проверяет не он.",
    "senior": "Ведёт письма, готовит отчёты и сдаёт их на проверку, "
              "пополняет библиотеку.",
    "engineer": "Ведёт письма, готовит отчёты и сдаёт их на проверку, "
                "пополняет библиотеку.",
}

#: Должности с правами администратора: заводят сотрудников, удаляют документы,
#: читают журнал действий. Начальник группы — последняя такая должность.
ADMIN_ROLES = ("owner", "head", "deputy", "lead")

#: Старшинство: больше — выше. Нужно, чтобы начальник группы не менял роль
#: начальнику отдела, а заместитель — создателю.
ROLE_RANK = {"owner": 50, "head": 40, "deputy": 30, "lead": 20, "senior": 10, "engineer": 0}

#: Как читать роли, оставшиеся от прежней версии. viewer был «только чтение»,
#: а в штатном расписании компании такой должности нет — становится инженером.
LEGACY_ROLES = {"viewer": "engineer", "admin": "head"}
CHAT_ROLES = ("user", "assistant")
CASE_STATUSES = ("new", "draft", "review", "approved", "archived")
CASE_STATUS_TITLES = {
    "new": "принято",
    "draft": "в работе",
    "review": "на проверке",
    "approved": "отправлено",
    "archived": "в архиве",
}
#: Письма, которые считаются работой в текущий момент.
OPEN_CASE_STATUSES = ("new", "draft", "review")

CASE_PRIORITIES = ("normal", "high", "urgent")
CASE_PRIORITY_TITLES = {"normal": "обычный", "high": "важный", "urgent": "срочный"}

#: Виды отсутствия и дежурства.
ABSENCE_KINDS = ("duty", "vacation", "sick", "trip", "study")
ABSENCE_TITLES = {
    "duty": "дежурство",
    "vacation": "отпуск",
    "sick": "больничный",
    "trip": "командировка",
    "study": "учёба",
}
#: Путь отчёта. Готовит его исполнитель, проверяет начальник отдела или
#: заместитель: draft — в работе у исполнителя; review — отправлен на
#: проверку; rework — проверяющий вернул с замечанием; approved — проверен.
REPORT_STATUSES = ("draft", "review", "rework", "approved")
REPORT_STATUS_TITLES = {
    "draft": "в работе",
    "review": "на проверке",
    "rework": "требует исправления",
    "approved": "проверен",
}
#: Кто может проверять отчёты: начальник отдела, его заместитель и создатель
#: системы. Начальник группы — администратор (заводит людей), но отчёты
#: проверяет не он: так устроен порядок в отделе.
REVIEW_ROLES = ("owner", "head", "deputy")
#: Откуда взялся отчёт: собран системой по факт-пакету или загружен готовым
#: файлом. У загруженного факт-пакета нет, и числа в нём никто не сверял —
#: это обязано быть видно и в карточке, и в списке.
REPORT_SOURCES = ("generated", "uploaded")

#: Актуальность документа библиотеки.
#: current    — действующий, участвует в поиске;
#: superseded — заменён более новой редакцией, из поиска исключён;
#: archived   — выведен из обращения, из поиска исключён;
#: draft      — проект, ещё не введён в действие, из поиска исключён.
DOC_STATUSES = ("current", "superseded", "archived", "draft")
DOC_STATUS_TITLES = {
    "current": "действующий",
    "superseded": "заменён",
    "archived": "архив",
    "draft": "проект",
}
#: Что участвует в поиске по умолчанию.
SEARCHABLE_STATUSES = ("current",)


def _col(row: sqlite3.Row, name: str, default: Any) -> Any:
    """Значение колонки, которой может не быть в старой базе."""
    return row[name] if name in row.keys() else default


def _json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass
class User:
    id: int
    login: str
    full_name: str = ""
    role: str = "engineer"
    department: str = ""
    team: str = ""
    active: bool = True
    created_at: str = ""

    @property
    def can_edit(self) -> bool:
        """Может вести письма и править отчёты. Это все штатные должности."""
        return self.role in ROLES

    @property
    def is_admin(self) -> bool:
        """Заводит и отключает сотрудников, удаляет документы, читает журнал."""
        return self.role in ADMIN_ROLES

    @property
    def can_review(self) -> bool:
        """Проверяет отчёты: начальник отдела, заместитель, создатель системы."""
        return self.role in REVIEW_ROLES

    @property
    def is_owner(self) -> bool:
        """Создатель системы: права без ограничений."""
        return self.role == "owner"

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(self.role, 0)

    @property
    def role_title(self) -> str:
        return ROLE_TITLES.get(self.role, self.role)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            login=row["login"],
            full_name=row["full_name"],
            role=row["role"],
            department=_col(row, "department", ""),
            team=_col(row, "team", ""),
            active=bool(row["active"]),
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "full_name": self.full_name,
            "role": self.role,
            "role_title": self.role_title,
            "department": self.department,
            "team": self.team,
            "is_admin": self.is_admin,
            "can_review": self.can_review,
            "is_owner": self.is_owner,
            "active": self.active,
            "created_at": self.created_at,
        }


@dataclass
class Document:
    id: int
    doc_id: str
    doc_type: str
    title: str
    source_path: str
    sha256: str
    domain: str = ""
    status: str = "current"
    superseded_by: str = ""
    #: Год издания. None — определить не удалось.
    year: int | None = None
    meta: Dict[str, Any] = field(default_factory=dict)
    chunk_count: int = 0
    #: Размер и время правки файла на момент приёма. По ним приём решает, надо
    #: ли вообще читать файл: считать SHA-256 всей библиотеки ради пяти новых
    #: документов незачем.
    size: int | None = None
    mtime_ns: int | None = None
    indexed_at: str | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Document":
        return cls(
            id=row["id"],
            doc_id=row["doc_id"],
            doc_type=row["doc_type"],
            title=row["title"],
            source_path=row["source_path"],
            sha256=row["sha256"],
            domain=row["domain"] if "domain" in row.keys() else "",
            status=row["status"] if "status" in row.keys() else "current",
            superseded_by=row["superseded_by"] if "superseded_by" in row.keys() else "",
            year=row["year"] if "year" in row.keys() else None,
            meta=_json(row["meta_json"], {}),
            chunk_count=row["chunk_count"],
            size=row["size"] if "size" in row.keys() else None,
            mtime_ns=row["mtime_ns"] if "mtime_ns" in row.keys() else None,
            indexed_at=row["indexed_at"],
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "title": self.title,
            "sha256": self.sha256[:12],
            "year": self.year,
            "domain": self.domain,
            "status": self.status,
            "status_title": DOC_STATUS_TITLES.get(self.status, self.status),
            "superseded_by": self.superseded_by,
            "searchable": self.status in SEARCHABLE_STATUSES,
            "chunk_count": self.chunk_count,
            "indexed_at": self.indexed_at,
            "meta": self.meta,
        }


@dataclass
class Case:
    id: int
    case_id: str
    report_type: str
    title: str = ""
    customer: str = ""
    status: str = "new"
    #: Входящий номер и дата входящего письма.
    incoming_no: str = ""
    incoming_date: str = ""
    #: Срок ответа, ГГГГ-ММ-ДД. Пусто — срок не задан.
    deadline: str = ""
    priority: str = "normal"
    assignee_id: int | None = None
    note: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)
    facts_digest: str = ""
    created_by: int | None = None
    created_at: str = ""
    updated_at: str = ""
    #: ФИО исполнителя. Заполняется выборкой со связкой, в таблице не хранится.
    assignee_name: str = ""
    #: Сколько отчётов заведено по письму. Считает выборка, в таблице нет.
    reports_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Case":
        return cls(
            id=row["id"],
            case_id=row["case_id"],
            report_type=row["report_type"],
            title=row["title"],
            customer=row["customer"],
            status=row["status"],
            incoming_no=_col(row, "incoming_no", ""),
            incoming_date=_col(row, "incoming_date", ""),
            deadline=_col(row, "deadline", ""),
            priority=_col(row, "priority", "normal") or "normal",
            assignee_id=_col(row, "assignee_id", None),
            note=_col(row, "note", ""),
            facts=_json(row["facts_json"], {}),
            facts_digest=row["facts_digest"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            assignee_name=_col(row, "assignee_name", "") or "",
            reports_count=int(_col(row, "reports_count", 0) or 0),
        )

    def to_dict(self, *, with_facts: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "case_id": self.case_id,
            "report_type": self.report_type,
            "title": self.title,
            # Колонка в базе называется customer по историческим причинам,
            # наружу отдаём то слово, которое стоит в интерфейсе.
            "group_no": self.customer,
            "status": self.status,
            "incoming_no": self.incoming_no,
            "incoming_date": self.incoming_date,
            "deadline": self.deadline,
            "priority": self.priority,
            "assignee_id": self.assignee_id,
            "assignee_name": self.assignee_name,
            "note": self.note,
            "facts_digest": self.facts_digest,
            "reports_count": self.reports_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if with_facts:
            data["facts"] = self.facts
        return data


@dataclass
class ReportSection:
    id: int
    report_id: int
    section_id: str
    title: str
    ord: int
    draft_text: str
    text: str
    sources: List[str] = field(default_factory=list)
    missing_facts: List[str] = field(default_factory=list)
    regenerated: int = 0
    edited: bool = False
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ReportSection":
        return cls(
            id=row["id"],
            report_id=row["report_id"],
            section_id=row["section_id"],
            title=row["title"],
            ord=row["ord"],
            draft_text=row["draft_text"],
            text=row["text"],
            sources=_json(row["sources_json"], []),
            missing_facts=_json(row["missing_facts_json"], []),
            regenerated=row["regenerated"],
            edited=bool(row["edited"]),
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "ord": self.ord,
            "text": self.text,
            "draft_text": self.draft_text,
            "sources": self.sources,
            "missing_facts": self.missing_facts,
            "regenerated": self.regenerated,
            "edited": self.edited,
            "updated_at": self.updated_at,
        }


@dataclass
class Report:
    id: int
    case_ref: int
    version: int
    status: str = "draft"
    markdown: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    created_by: int | None = None
    created_at: str = ""
    approved_by: int | None = None
    approved_at: str | None = None
    #: Замечание проверяющего при возврате на исправление. Видно всем:
    #: исполнитель должен знать, что править, а отдел — что происходит.
    review_note: str = ""
    #: Собран системой или загружен готовым файлом.
    source: str = "generated"
    #: Имя и размер загруженного файла; для собранных системой — пусто.
    file_name: str = ""
    file_size: int = 0
    sections: List[ReportSection] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Report":
        return cls(
            id=row["id"],
            case_ref=row["case_ref"],
            version=row["version"],
            status=row["status"],
            markdown=row["markdown"],
            meta=_json(row["meta_json"], {}),
            issues=_json(row["issues_json"], []),
            created_by=row["created_by"],
            created_at=row["created_at"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            review_note=_col(row, "review_note", "") or "",
            source=_col(row, "source", "generated") or "generated",
            file_name=_col(row, "file_name", "") or "",
            file_size=int(_col(row, "file_size", 0) or 0),
        )

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.get("level") == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.get("level") == "warning")

    def to_dict(self, *, with_markdown: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "case_ref": self.case_ref,
            "version": self.version,
            "status": self.status,
            "meta": self.meta,
            "issues": self.issues,
            "errors": self.error_count,
            "warnings": self.warning_count,
            "status_title": REPORT_STATUS_TITLES.get(self.status, self.status),
            "created_at": self.created_at,
            "approved_at": self.approved_at,
            "review_note": self.review_note,
            "source": self.source,
            "uploaded": self.source == "uploaded",
            "file_name": self.file_name,
            "file_size": self.file_size,
            "sections": [section.to_dict() for section in self.sections],
        }
        if with_markdown:
            data["markdown"] = self.markdown
        return data


@dataclass
class EditPair:
    id: int
    case_id: str
    report_id: int | None
    report_type: str
    section_id: str
    section_title: str
    draft: str
    final: str
    facts_digest: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    edit_distance: float = 0.0
    created_by: int | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "EditPair":
        return cls(
            id=row["id"],
            case_id=row["case_id"],
            report_id=row["report_id"],
            report_type=row["report_type"],
            section_id=row["section_id"],
            section_title=row["section_title"],
            draft=row["draft"],
            final=row["final"],
            facts_digest=row["facts_digest"],
            context=_json(row["context_json"], {}),
            edit_distance=row["edit_distance"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )


@dataclass
class Chat:
    """Разговор с помощником. Принадлежит одному пользователю."""

    id: int
    user_id: int
    title: str = "Новый разговор"
    domain: str = ""
    case_ref: int | None = None
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Chat":
        keys = row.keys()
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            domain=row["domain"],
            case_ref=row["case_ref"],
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"] if "message_count" in keys else 0,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "domain": self.domain,
            "case_ref": self.case_ref,
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
        }


@dataclass
class ChatMessage:
    id: int
    chat_id: int
    role: str
    content: str
    sources: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ChatMessage":
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            role=row["role"],
            content=row["content"],
            sources=_json(row["sources_json"], []),
            meta=_json(row["meta_json"], {}),
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "sources": self.sources,
            "meta": self.meta,
            "created_at": self.created_at,
        }


#: Виды вложений к вопросу помощнику.
ATTACHMENT_KINDS = ("dump", "image", "document")
ATTACHMENT_TITLES = {
    "dump": "дамп или лог",
    "image": "снимок экрана",
    "document": "документ",
}


@dataclass
class ChatAttachment:
    """Файл, приложенный к вопросу: дамп, снимок экрана, документ."""

    id: int
    chat_id: int
    name: str
    kind: str = "document"
    size: int = 0
    text: str = ""
    #: Длина извлечённого текста. Отдельным полем — чтобы показать её, не
    #: вычитывая сам текст: дамп занимает до 200 МБ, а на экране от него
    #: нужно одно число.
    chars: int = -1
    note: str = ""
    message_id: int | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ChatAttachment":
        text = _col(row, "text", "") or ""
        return cls(
            id=row["id"],
            chat_id=row["chat_id"],
            name=row["name"],
            kind=row["kind"],
            size=int(row["size"] or 0),
            text=text,
            chars=int(_col(row, "chars", len(text)) or 0),
            note=row["note"] or "",
            message_id=row["message_id"],
            created_at=row["created_at"],
        )

    def to_dict(self, *, with_text: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "chat_id": self.chat_id,
            "name": self.name,
            "kind": self.kind,
            "kind_title": ATTACHMENT_TITLES.get(self.kind, self.kind),
            "size": self.size,
            "chars": self.chars if self.chars >= 0 else len(self.text),
            "note": self.note,
            "message_id": self.message_id,
        }
        if with_text:
            data["text"] = self.text
        return data


@dataclass
class Absence:
    """Период отсутствия или дежурства сотрудника."""

    id: int
    user_id: int
    kind: str
    date_from: str
    date_to: str
    note: str = ""
    created_by: int | None = None
    created_at: str = ""
    #: ФИО и должность подтягиваются выборкой со связкой.
    full_name: str = ""
    role: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Absence":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            kind=row["kind"],
            date_from=row["date_from"],
            date_to=row["date_to"],
            note=_col(row, "note", ""),
            created_by=_col(row, "created_by", None),
            created_at=_col(row, "created_at", ""),
            full_name=_col(row, "full_name", "") or "",
            role=_col(row, "role", "") or "",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "kind": self.kind,
            "kind_title": ABSENCE_TITLES.get(self.kind, self.kind),
            "date_from": self.date_from,
            "date_to": self.date_to,
            "note": self.note,
            "full_name": self.full_name,
            "role": self.role,
            "role_title": ROLE_TITLES.get(self.role, self.role),
        }


@dataclass
class AuditEntry:
    id: int
    ts: str
    user_id: int | None
    login: str
    action: str
    object_type: str = ""
    object_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuditEntry":
        return cls(
            id=row["id"],
            ts=row["ts"],
            user_id=row["user_id"],
            login=row["login"],
            action=row["action"],
            object_type=row["object_type"],
            object_id=row["object_id"],
            details=_json(row["details_json"], {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "login": self.login,
            "action": self.action,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "details": self.details,
        }


def rows_to(model: Any, rows: Sequence[sqlite3.Row]) -> List[Any]:
    return [model.from_row(row) for row in rows]
