"""Модели строк базы. Тонкие dataclass-обёртки над sqlite3.Row.

Хранилище намеренно не тянет ORM: таблиц немного, запросы простые, а явный SQL
проще сопровождать инженеру, который придёт после автора системы.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

ROLES = ("viewer", "engineer", "admin")
CASE_STATUSES = ("new", "draft", "review", "approved", "archived")
REPORT_STATUSES = ("draft", "verified", "approved")
CONFIDENTIALITY = ("public", "internal", "nda")


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
    active: bool = True
    created_at: str = ""

    @property
    def can_edit(self) -> bool:
        return self.role in ("engineer", "admin")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            login=row["login"],
            full_name=row["full_name"],
            role=row["role"],
            active=bool(row["active"]),
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "full_name": self.full_name,
            "role": self.role,
            "active": self.active,
        }


@dataclass
class Document:
    id: int
    doc_id: str
    doc_type: str
    title: str
    source_path: str
    sha256: str
    confidentiality: str = "internal"
    meta: Dict[str, Any] = field(default_factory=dict)
    chunk_count: int = 0
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
            confidentiality=row["confidentiality"],
            meta=_json(row["meta_json"], {}),
            chunk_count=row["chunk_count"],
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
            "confidentiality": self.confidentiality,
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
    facts: Dict[str, Any] = field(default_factory=dict)
    facts_digest: str = ""
    created_by: int | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Case":
        return cls(
            id=row["id"],
            case_id=row["case_id"],
            report_type=row["report_type"],
            title=row["title"],
            customer=row["customer"],
            status=row["status"],
            facts=_json(row["facts_json"], {}),
            facts_digest=row["facts_digest"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self, *, with_facts: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "case_id": self.case_id,
            "report_type": self.report_type,
            "title": self.title,
            "customer": self.customer,
            "status": self.status,
            "facts_digest": self.facts_digest,
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
            "created_at": self.created_at,
            "approved_at": self.approved_at,
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
