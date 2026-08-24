"""REST API. Обработчики тонкие: разбор запроса и вызов сервисного слоя."""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..corpus import DOC_TYPES
from ..domains import registry as domain_registry
from ..store.models import CONFIDENTIALITY, Case, Report
from .auth import COOKIE_NAME, get_user, require_admin, require_editor, require_user
from .service import ServiceError

router = APIRouter(prefix="/api")

MAX_QUERY_LEN = 500


# ------------------------------------------------------------- служебное ---

def _service(request: Request):
    return request.app.state.service


def _assistant(request: Request):
    return request.app.state.assistant


def _domains(request: Request):
    settings = _settings(request)
    return domain_registry(Path(settings.templates_dir) / "domains.json")


def _repos(request: Request):
    return request.app.state.repos


def _settings(request: Request):
    return request.app.state.settings


def _body(request: Request) -> Dict[str, Any]:
    """Тело JSON-запроса; пустое тело считается пустым словарём."""
    raw = getattr(request.state, "json_body", None)
    if raw is None:
        raise ServiceError("ожидалось тело запроса в формате JSON", 400)
    return raw


def _case_or_404(request: Request, case_ref: int) -> Case:
    case = _repos(request).cases.get(case_ref)
    if case is None:
        raise ServiceError("кейс не найден", 404)
    return case


def _report_or_404(request: Request, report_id: int) -> Report:
    report = _repos(request).reports.get(report_id)
    if report is None:
        raise ServiceError("отчёт не найден", 404)
    return report


def _report_payload(service, report: Report, *, with_markdown: bool = True) -> Dict[str, Any]:
    data = report.to_dict(with_markdown=with_markdown)
    data["sources"] = service.sources(report)
    return data


# ------------------------------------------------------------------ вход ---

@router.post("/auth/login")
def login(request: Request, response: Response) -> Dict[str, Any]:
    payload = _body(request)
    login_name = str(payload.get("login", "")).strip().lower()
    password = str(payload.get("password", ""))
    if not login_name or not password:
        raise ServiceError("укажите логин и пароль", 400)

    settings = _settings(request)
    if not settings.auth_enabled:
        raise ServiceError("аутентификация отключена настройками", 400)

    throttle = request.app.state.throttle
    client = request.client.host if request.client else "?"
    key = f"{login_name}@{client}"
    throttle.check(key)

    repos = _repos(request)
    user = repos.users.authenticate(login_name, password)
    if user is None:
        throttle.failure(key)
        repos.audit.log("auth.fail", object_type="user", object_id=login_name,
                        details={"client": client})
        raise ServiceError("неверный логин или пароль", 401)

    throttle.success(key)
    token = repos.sessions.create(
        user.id, settings.session_ttl_hours, request.headers.get("user-agent", "")
    )
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    repos.audit.log("auth.login", user=user, object_type="user", object_id=user.login)
    return {"user": user.to_dict()}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> Dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        _repos(request).sessions.delete(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> Dict[str, Any]:
    user = get_user(request)
    return {
        "user": user.to_dict() if user else None,
        "auth_enabled": _settings(request).auth_enabled,
    }


@router.get("/config")
def config(request: Request) -> Dict[str, Any]:
    settings = _settings(request)
    service = _service(request)
    outlines = []
    for outline in service.outlines.all().values():
        outlines.append({
            "report_type": outline.report_type,
            "title": outline.title,
            "version": outline.version,
            "sections": [
                {
                    "id": spec.id,
                    "title": spec.title,
                    "required_facts": list(spec.required_facts),
                    "optional_facts": list(spec.optional_facts),
                    "target_words": spec.target_words,
                }
                for spec in outline.sections
            ],
        })
    return {
        "outlines": outlines,
        "doc_types": list(DOC_TYPES),
        "confidentiality": list(CONFIDENTIALITY),
        "llm": {"model": settings.llm_model, "base_url": settings.llm_base_url,
                "kind": settings.llm_kind},
        "auth_enabled": settings.auth_enabled,
        "brand": {
            "name": settings.brand_name,
            "subtitle": settings.brand_subtitle,
            "accent": settings.brand_accent,
            "logo": "/brand/logo" if _logo_path(settings) else None,
        },
        "search": {
            "dense": settings.embed_enabled,
            "rerank": settings.rerank_enabled,
        },
        "domains": _domains(request).to_dict(),
    }


def _logo_path(settings) -> Path | None:
    logo = settings.brand_logo
    if logo and Path(logo).is_file():
        return Path(logo)
    return None


# ----------------------------------------------------------------- кейсы ---

@router.get("/cases")
def list_cases(request: Request, status: str | None = None,
               limit: int = 100, offset: int = 0) -> Dict[str, Any]:
    require_user(request)
    repos = _repos(request)
    cases = repos.cases.list(status=status, limit=min(limit, 500), offset=max(offset, 0))
    return {
        "items": [case.to_dict() for case in cases],
        "total": repos.cases.count(status),
    }


@router.post("/cases")
def create_case(request: Request) -> Dict[str, Any]:
    user = require_editor(request)
    service = _service(request)
    case = service.create_case(_body(request), user)
    return {"case": case.to_dict(with_facts=True), "coverage": service.coverage(case)}


@router.get("/cases/{case_ref}")
def get_case(request: Request, case_ref: int) -> Dict[str, Any]:
    require_user(request)
    case = _case_or_404(request, case_ref)
    service = _service(request)
    repos = _repos(request)
    reports = repos.reports.list_for_case(case.id)
    return {
        "case": case.to_dict(with_facts=True),
        "coverage": service.coverage(case),
        "reports": [
            {
                "id": report.id,
                "version": report.version,
                "status": report.status,
                "created_at": report.created_at,
                "errors": report.error_count,
                "warnings": report.warning_count,
            }
            for report in reports
        ],
    }


@router.put("/cases/{case_ref}/facts")
def update_facts(request: Request, case_ref: int) -> Dict[str, Any]:
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    service = _service(request)
    payload = _body(request)
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        raise ServiceError("ожидался объект facts", 400)
    updated = service.update_facts(case, facts, user)
    return {"case": updated.to_dict(with_facts=True), "coverage": service.coverage(updated)}


@router.delete("/cases/{case_ref}")
def delete_case(request: Request, case_ref: int) -> Dict[str, Any]:
    user = require_admin(request)
    case = _case_or_404(request, case_ref)
    _repos(request).cases.delete(case.id)
    _repos(request).audit.log("case.delete", user=user, object_type="case",
                              object_id=case.case_id)
    return {"ok": True}


@router.post("/cases/{case_ref}/generate")
def generate(request: Request, case_ref: int) -> Dict[str, Any]:
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    service = _service(request)
    payload = getattr(request.state, "json_body", None) or {}
    top_k = payload.get("top_k")
    report = service.generate(case, user, top_k=int(top_k) if top_k else None)
    return {"report": _report_payload(service, report)}


@router.get("/cases/{case_ref}/report")
def latest_report(request: Request, case_ref: int) -> Dict[str, Any]:
    require_user(request)
    case = _case_or_404(request, case_ref)
    report = _repos(request).reports.latest_for_case(case.id)
    if report is None:
        raise ServiceError("для кейса ещё не сгенерирован отчёт", 404)
    return {"report": _report_payload(_service(request), report)}


# --------------------------------------------------------------- отчёты ---

@router.get("/reports/{report_id}")
def get_report(request: Request, report_id: int) -> Dict[str, Any]:
    require_user(request)
    report = _report_or_404(request, report_id)
    return {"report": _report_payload(_service(request), report)}


@router.post("/reports/{report_id}/verify")
def verify_report_endpoint(request: Request, report_id: int) -> Dict[str, Any]:
    require_user(request)
    report = _report_or_404(request, report_id)
    issues = _service(request).verify(report)
    return {
        "issues": issues,
        "errors": sum(1 for issue in issues if issue["level"] == "error"),
        "warnings": sum(1 for issue in issues if issue["level"] == "warning"),
    }


@router.post("/reports/{report_id}/sections/{section_id}/regenerate")
def regenerate_section(request: Request, report_id: int, section_id: str) -> Dict[str, Any]:
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    payload = getattr(request.state, "json_body", None) or {}
    section = service.regenerate_section(
        report, section_id, user, hint=str(payload.get("hint", ""))
    )
    updated = _report_or_404(request, report_id)
    return {"section": section.to_dict(), "report": _report_payload(service, updated)}


@router.put("/reports/{report_id}/sections/{section_id}")
def save_section(request: Request, report_id: int, section_id: str) -> Dict[str, Any]:
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    payload = _body(request)
    text = payload.get("text")
    if not isinstance(text, str):
        raise ServiceError("ожидалось текстовое поле text", 400)
    service = _service(request)
    section = service.save_section(report, section_id, text, user)
    updated = _report_or_404(request, report_id)
    return {"section": section.to_dict(), "report": _report_payload(service, updated)}


@router.post("/reports/{report_id}/sections/{section_id}/restore")
def restore_section(request: Request, report_id: int, section_id: str) -> Dict[str, Any]:
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    section = service.restore_section(report, section_id, user)
    updated = _report_or_404(request, report_id)
    return {"section": section.to_dict(), "report": _report_payload(service, updated)}


@router.post("/reports/{report_id}/approve")
def approve(request: Request, report_id: int) -> Dict[str, Any]:
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    approved = service.approve(report, user)
    return {"report": _report_payload(service, approved)}


@router.get("/reports/{report_id}/sources")
def report_sources(request: Request, report_id: int) -> Dict[str, Any]:
    require_user(request)
    report = _report_or_404(request, report_id)
    return {"items": _service(request).sources(report)}


@router.get("/reports/{report_id}/export.md")
def export_markdown(request: Request, report_id: int) -> Response:
    require_user(request)
    report = _report_or_404(request, report_id)
    case = _case_or_404(request, report.case_ref)
    filename = f"{_safe_name(case.case_id)}-v{report.version}.md"
    return Response(
        content=report.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _disposition(filename)},
    )


@router.get("/reports/{report_id}/export.docx")
def export_docx(request: Request, report_id: int) -> FileResponse:
    user = require_user(request)
    report = _report_or_404(request, report_id)
    case = _case_or_404(request, report.case_ref)
    settings = _settings(request)

    try:
        from ..export.docx import export_report  # noqa: PLC0415
    except ImportError as error:
        raise ServiceError(
            "экспорт в DOCX недоступен: не установлен python-docx "
            "(pip install -r requirements.txt)", 501,
        ) from error

    settings.ensure_dirs()
    target = Path(settings.export_dir) / f"{_safe_name(case.case_id)}-v{report.version}.docx"
    try:
        export_report(
            report.markdown, target,
            case_id=case.case_id, status=report.status,
            template=settings.docx_template,
        )
    except ImportError as error:
        # MissingDependencyError из export.docx — пакет не установлен.
        raise ServiceError(f"экспорт в DOCX недоступен: {error}", 501) from error
    except Exception as error:  # noqa: BLE001 — показываем инженеру причину
        raise ServiceError(f"не удалось собрать DOCX: {error}", 500) from error

    _repos(request).audit.log("report.export", user=user, object_type="report",
                              object_id=str(report.id), details={"format": "docx"})
    return FileResponse(
        target,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=target.name,
    )


# ------------------------------------------------------------ библиотека ---

@router.get("/library")
def library(request: Request, doc_type: str | None = None,
            domain: str | None = None) -> Dict[str, Any]:
    require_user(request)
    repos = _repos(request)
    documents = repos.documents.list(doc_type, domain)
    return {
        "items": [document.to_dict() for document in documents],
        "stats": repos.documents.stats(),
        "domains": repos.documents.domains(),
        "chunks": repos.chunks.count(),
        "embeddings": repos.vectors.count(),
    }


@router.post("/library/upload")
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form("literature"),
    confidentiality: str = Form("internal"),
    domain: str = Form(""),
) -> Dict[str, Any]:
    user = require_editor(request)
    settings = _settings(request)
    if doc_type not in DOC_TYPES:
        raise ServiceError(f"неизвестный тип документа '{doc_type}'", 400)
    if confidentiality not in CONFIDENTIALITY:
        raise ServiceError(f"неизвестный гриф '{confidentiality}'", 400)
    if not _domains(request).is_known(domain):
        raise ServiceError(f"неизвестное направление '{domain}'", 400)

    name = _safe_name(Path(file.filename or "документ").name)
    if not name:
        raise ServiceError("некорректное имя файла", 400)

    settings.ensure_dirs()
    target_dir = Path(settings.library_dir) / doc_type
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name

    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with target.open("wb") as stream:
        while True:
            piece = file.file.read(1024 * 1024)
            if not piece:
                break
            size += len(piece)
            if size > limit:
                stream.close()
                target.unlink(missing_ok=True)
                raise ServiceError(
                    f"файл больше допустимых {settings.max_upload_mb} МБ", 413
                )
            stream.write(piece)

    result = _ingest_file(request, target, doc_type=doc_type,
                          confidentiality=confidentiality, domain=domain or None)
    repos = _repos(request)
    repos.audit.log("library.upload", user=user, object_type="document",
                    object_id=name, details={"doc_type": doc_type, "bytes": size})
    _service(request).reset_retriever()

    document = None
    for doc_id in (result.get("documents") or []):
        found = repos.documents.by_doc_id(doc_id)
        if found is not None:
            document = found.to_dict()
    return {"result": result, "document": document}


@router.post("/library/reindex")
def reindex(request: Request) -> Dict[str, Any]:
    user = require_editor(request)
    settings = _settings(request)
    payload = getattr(request.state, "json_body", None) or {}
    force = bool(payload.get("force", False))

    try:
        from ..ingest.pipeline import ingest_directory  # noqa: PLC0415
    except ImportError as error:
        raise ServiceError("модуль приёма документов недоступен", 501) from error

    settings.ensure_dirs()
    result = ingest_directory(_repos(request), settings.library_dir, force=force)
    _service(request).reset_retriever()
    _repos(request).audit.log("library.reindex", user=user, details={"force": force})
    return {"result": _ingest_to_dict(result)}


@router.delete("/library/{doc_id:path}")
def delete_document(request: Request, doc_id: str) -> Dict[str, Any]:
    user = require_admin(request)
    repos = _repos(request)
    if repos.documents.by_doc_id(doc_id) is None:
        raise ServiceError("документ не найден", 404)
    repos.documents.delete(doc_id)
    repos.audit.log("library.delete", user=user, object_type="document", object_id=doc_id)
    _service(request).reset_retriever()
    return {"ok": True}


@router.get("/search")
def search(request: Request, q: str = "", top_k: int = 10,
           doc_types: str | None = None, domains: str | None = None) -> Dict[str, Any]:
    require_user(request)
    query = q.strip()[:MAX_QUERY_LEN]
    if not query:
        raise ServiceError("пустой поисковый запрос", 400)
    retriever = _service(request).get_retriever()
    if retriever is None:
        return {"items": [], "note": "библиотека пуста — загрузите документы"}
    types = [t for t in (doc_types or "").split(",") if t] or None
    areas = [d for d in (domains or "").split(",") if d] or None
    try:
        hits = retriever.search(query, top_k=min(top_k, 50), doc_types=types, domains=areas)
    except TypeError:
        hits = retriever.search(query, top_k=min(top_k, 50), doc_types=types)
    warning = getattr(retriever, "last_warning", "")
    return {
        "warning": warning or None,
        "items": [
            {
                "chunk_uid": hit.chunk.chunk_id,
                "doc_type": hit.chunk.doc_type,
                "domain": hit.chunk.meta.get("domain", ""),
                "citation": hit.chunk.citation,
                "text": " ".join(hit.chunk.text.split())[:600],
                "score": round(float(hit.score), 4),
                "rank": hit.rank,
            }
            for hit in hits
        ]
    }


# ---------------------------------------------------------- направления ---

@router.get("/domains")
def domains(request: Request) -> Dict[str, Any]:
    require_user(request)
    return {
        "items": _domains(request).to_dict(),
        "documents": _repos(request).documents.domains(),
    }


@router.put("/library/{doc_id:path}/domain")
def set_document_domain(request: Request, doc_id: str) -> Dict[str, Any]:
    user = require_editor(request)
    payload = _body(request)
    domain = str(payload.get("domain", "")).strip()
    if not _domains(request).is_known(domain):
        raise ServiceError(f"неизвестное направление '{domain}'", 400)
    repos = _repos(request)
    if repos.documents.by_doc_id(doc_id) is None:
        raise ServiceError("документ не найден", 404)
    repos.documents.set_domain(doc_id, domain)
    repos.audit.log("library.domain", user=user, object_type="document",
                    object_id=doc_id, details={"domain": domain})
    _service(request).reset_retriever()
    return {"ok": True, "document": repos.documents.by_doc_id(doc_id).to_dict()}


# -------------------------------------------------------------- помощник ---

@router.get("/chats")
def list_chats(request: Request, archived: bool = False) -> Dict[str, Any]:
    user = require_user(request)
    chats = _assistant(request).list_chats(user, archived=archived)
    return {"items": [chat.to_dict() for chat in chats]}


@router.post("/chats")
def create_chat(request: Request) -> Dict[str, Any]:
    user = require_user(request)
    payload = getattr(request.state, "json_body", None) or {}
    domain = str(payload.get("domain", "")).strip()
    if not _domains(request).is_known(domain):
        raise ServiceError(f"неизвестное направление '{domain}'", 400)
    case_ref = payload.get("case_ref")
    chat = _assistant(request).create_chat(
        user,
        title=str(payload.get("title", "Новый разговор")),
        domain=domain,
        case_ref=int(case_ref) if case_ref else None,
    )
    return {"chat": chat.to_dict()}


@router.get("/chats/{chat_id}")
def get_chat(request: Request, chat_id: int) -> Dict[str, Any]:
    user = require_user(request)
    assistant = _assistant(request)
    chat = assistant.get_chat(user, chat_id)
    return {
        "chat": chat.to_dict(),
        "messages": [message.to_dict() for message in assistant.messages(user, chat_id)],
    }


@router.patch("/chats/{chat_id}")
def update_chat(request: Request, chat_id: int) -> Dict[str, Any]:
    user = require_user(request)
    payload = _body(request)
    assistant = _assistant(request)
    if "title" in payload:
        assistant.rename(user, chat_id, str(payload["title"]))
    domain = payload.get("domain")
    if domain is not None and not _domains(request).is_known(str(domain)):
        raise ServiceError(f"неизвестное направление '{domain}'", 400)
    if domain is not None or "archived" in payload:
        assistant.update(
            user, chat_id,
            domain=str(domain) if domain is not None else None,
            archived=bool(payload["archived"]) if "archived" in payload else None,
        )
    return {"chat": assistant.get_chat(user, chat_id).to_dict()}


@router.delete("/chats/{chat_id}")
def delete_chat(request: Request, chat_id: int) -> Dict[str, Any]:
    user = require_user(request)
    _assistant(request).delete(user, chat_id)
    return {"ok": True}


@router.post("/chats/{chat_id}/ask")
def ask(request: Request, chat_id: int) -> Dict[str, Any]:
    user = require_user(request)
    payload = _body(request)
    text = str(payload.get("text", ""))
    return _assistant(request).ask(user, chat_id, text)


@router.post("/chats/{chat_id}/stream")
def ask_stream(request: Request, chat_id: int) -> StreamingResponse:
    """Потоковый ответ: события SSE — вопрос, источники, куски текста, итог."""
    user = require_user(request)
    payload = _body(request)
    text = str(payload.get("text", ""))
    assistant = _assistant(request)

    def events():
        try:
            for event in assistant.ask_stream(user, chat_id, text):
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except ServiceError as error:
            yield "data: " + json.dumps(
                {"type": "error", "error": str(error)}, ensure_ascii=False) + "\n\n"
        except Exception as error:  # noqa: BLE001 — поток нельзя оборвать молча
            yield "data: " + json.dumps(
                {"type": "error", "error": f"ошибка модели: {error}"},
                ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------- личный кабинет --

@router.get("/me/summary")
def my_summary(request: Request) -> Dict[str, Any]:
    user = require_user(request)
    repos = _repos(request)
    reports = repos.db.query_one(
        "SELECT count(*) AS total, "
        "sum(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved "
        "FROM reports WHERE created_by = ?", (user.id,),
    )
    edits = repos.db.query_one(
        "SELECT count(*) AS pairs, coalesce(avg(edit_distance), 0) AS mean "
        "FROM edit_pairs WHERE created_by = ?", (user.id,),
    )
    return {
        "user": user.to_dict(),
        "cases": int(repos.db.scalar(
            "SELECT count(*) FROM cases WHERE created_by = ?", (user.id,)) or 0),
        "reports": {
            "total": int(reports["total"] or 0),
            "approved": int(reports["approved"] or 0),
        },
        "edits": {
            "pairs": int(edits["pairs"] or 0),
            "mean_distance": round(float(edits["mean"] or 0.0), 3),
        },
        "chats": repos.chats.count_for_user(user.id),
    }


@router.post("/me/password")
def change_password(request: Request, response: Response) -> Dict[str, Any]:
    user = require_user(request)
    settings = _settings(request)
    if not settings.auth_enabled:
        raise ServiceError("аутентификация отключена настройками", 400)
    payload = _body(request)
    current = str(payload.get("current", ""))
    fresh = str(payload.get("new", ""))
    if len(fresh) < 8:
        raise ServiceError("новый пароль короче 8 символов", 400)
    if fresh == current:
        raise ServiceError("новый пароль совпадает со старым", 400)

    repos = _repos(request)
    if repos.users.authenticate(user.login, current) is None:
        raise ServiceError("текущий пароль указан неверно", 403)

    repos.users.set_password(user.id, fresh)
    # Все прежние сессии закрываем, текущую выдаём заново.
    repos.sessions.delete_for_user(user.id)
    token = repos.sessions.create(
        user.id, settings.session_ttl_hours, request.headers.get("user-agent", "")
    )
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
        max_age=settings.session_ttl_hours * 3600, path="/",
    )
    repos.audit.log("user.password", user=user, object_type="user", object_id=user.login)
    return {"ok": True}


# ------------------------------------------------------- метрики и журнал --

@router.get("/stats")
def stats(request: Request) -> Dict[str, Any]:
    require_user(request)
    return _service(request).stats()


@router.get("/audit")
def audit(request: Request, limit: int = 200) -> Dict[str, Any]:
    require_admin(request)
    entries = _repos(request).audit.list(limit=min(limit, 1000))
    return {"items": [entry.to_dict() for entry in entries]}


@router.get("/health")
def health(request: Request) -> Dict[str, Any]:
    repos = _repos(request)
    settings = _settings(request)
    try:
        counts = repos.db.counts()
        database = "ok"
    except Exception as error:  # noqa: BLE001
        counts, database = {}, f"ошибка: {error}"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "counts": counts,
        "llm": {"kind": settings.llm_kind, "model": settings.llm_model},
        "auth_enabled": settings.auth_enabled,
    }


# ------------------------------------------------------------- служебное ---

# Пробел разрешён, а вот \s пропускал бы перевод строки — и тогда case_id
# с переводом строки уезжал бы прямо в заголовок HTTP-ответа.
_UNSAFE = re.compile(r"[^\w .()\-]", re.UNICODE)


def _safe_name(name: str) -> str:
    """Имя файла без путей и управляющих символов, пригодное для ФС и заголовков."""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE.sub("_", name).strip().strip(".")
    return name[:120]


def _disposition(filename: str) -> str:
    """Заголовок Content-Disposition, выдерживающий кириллицу в имени файла.

    Заголовки HTTP кодируются в latin-1, поэтому «отчёт.md» напрямую положить
    нельзя — Starlette упадёт. По RFC 5987 отдаём ASCII-запасной вариант и
    процентное представление настоящего имени.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    quoted = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8\'\'{quoted}"


def _ingest_file(request: Request, path: Path, *, doc_type: str,
                 confidentiality: str, domain: str | None = None) -> Dict[str, Any]:
    try:
        from ..ingest.pipeline import ingest_path  # noqa: PLC0415
    except ImportError as error:
        raise ServiceError("модуль приёма документов недоступен", 501) from error
    settings = _settings(request)
    result = ingest_path(
        _repos(request), path,
        root=Path(settings.library_dir), doc_type=doc_type,
        confidentiality=confidentiality, force=True, domain=domain,
    )
    return _ingest_to_dict(result)


def _ingest_to_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    keys = ("added", "updated", "skipped", "failed", "chunks", "documents", "warnings")
    return {key: getattr(result, key, None) for key in keys if hasattr(result, key)}


def json_error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})
