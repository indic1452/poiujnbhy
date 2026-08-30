"""REST API. Обработчики тонкие: разбор запроса и вызов сервисного слоя."""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import tempfile
import unicodedata
import urllib.parse
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..corpus import DOC_TYPES
from ..domains import registry as domain_registry
from ..store.models import (
    ABSENCE_KINDS,
    ABSENCE_TITLES,
    ADMIN_ROLES,
    CASE_PRIORITIES,
    CASE_STATUSES,
    DOC_STATUS_TITLES,
    CASE_STATUS_TITLES,
    DOC_STATUSES,
    LINE_FULL_TITLES,
    LINE_TITLES,
    LINE_TYPES,
    PERSON_FILE_KINDS,
    PERSON_FILE_SINGLE,
    PERSON_FILE_TITLES,
    PRESENT_KINDS,
    REVIEW_ROLES,
    ROLE_NOTES,
    ROLE_RANK,
    ROLE_TITLES,
    ROLES,
    Case,
    Report,
    User,
)
from .auth import (COOKIE_NAME, get_user, require_admin, require_editor,
                   require_reviewer, require_user)
from .service import CARD_LIMITS, ServiceError

router = APIRouter(prefix="/api")

MAX_QUERY_LEN = 500

#: Пределы строк карточки письма. Живут в сервисном слое: правило одно, а
#: применяют его и веб, и поля формы (см. CARD_LIMIT в app.js).
MAX_CARD_FIELDS = CARD_LIMITS

#: Формат дат в карточке письма и в отсутствиях.
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _today() -> str:
    """Сегодняшняя дата по местному времени.

    Именно местная: сроки писем ставит человек, глядя на календарь на стене,
    и «просрочено» должно совпадать с его представлением о дне. Метки времени
    в базе при этом в UTC — для них есть _since_utc.
    """
    return datetime.now().strftime("%Y-%m-%d")


def _since_utc(days: int) -> str:
    """Начало периода в том же виде, что метки created_at/updated_at.

    Сравнивать местную дату со строкой в UTC нельзя: в Москве вечерние
    письма попадали бы в следующие сутки, и «за неделю» считалось бы не то.
    """
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.isoformat(timespec="seconds")


#: Состояния письма, которые даёт ход отчёта, а не рука. «На проверке» —
#: отчёт сдан начальнику; «проверен» — начальник согласился; «отправлено» —
#: исполнитель отправил ответ и записал исходящий номер.
FLOW_CASE_STATUSES = ("review", "checked", "approved")


def _guard_case_status(repos: Any, case: Case, status: str) -> None:
    """Не давать выставить в карточке то, что означает работу с отчётом.

    Инженер ставил письму «отправлено» прямо в карточке — письмо уходило из
    работы, начальник его больше не видел, а отчёта никто не проверял. Эти
    два состояния письмо получает от отчёта: сдали на проверку — «на
    проверке», отметили проверенным — «отправлено».

    Письмо без единого отчёта — другое дело: на него ответили мимо системы,
    подменять нечего, и отметить его в карточке можно.
    """
    if status not in FLOW_CASE_STATUSES:
        return
    if status == "approved":
        # «Отправлено» всегда означает «есть исходящий номер»: иначе в
        # журнале отдела письмо закрыто, а чем ответили — неизвестно.
        raise ServiceError(
            "состояние «отправлено» ставится записью исходящего номера: "
            "откройте письмо и нажмите «Ответ отправлен»", 409)
    if not repos.reports.list_for_case(case.id):
        return
    raise ServiceError(
        f"состояние «{CASE_STATUS_TITLES[status]}» письму даёт проверка отчёта: "
        "отправьте отчёт на проверку или отметьте его проверенным", 409)


def _card_line(value: Any, name: str) -> str:
    """Строка карточки письма: без управляющих знаков и в пределах длины.

    Управляющие знаки в поля не вводят — они приезжают вставкой из Word и
    из выгрузок: нулевой байт рвёт и выгрузку в DOCX, и поиск. Убираем их
    молча. А про длину говорим: молча обрезанная тема — это потерянный
    текст, о котором человек не узнал.
    """
    limit = MAX_CARD_FIELDS[name]
    text = "".join(
        ch for ch in str(value or "")
        if ch in "\n\t" or unicodedata.category(ch)[0] != "C"
    )
    text = text.strip() if name == "note" else " ".join(text.split())
    if len(text) > limit:
        titles = {"title": "тема письма", "incoming_no": "входящий номер",
                  "note": "примечание", "outgoing_no": "исходящий номер"}
        raise ServiceError(f"{titles[name]}: длиннее {limit} знаков", 400)
    return text


def _group_or_empty(value: Any) -> str:
    """Номер группы: только чистка пробелов и предел длины.

    Формат задаёт делопроизводство отдела, а не программа: пишут и «1274»,
    и «12/345», и «в/ч 74326», и словами. Сама чистка живёт в
    facts.clean_group — факт-пакет правится не только карточкой письма, но и
    целиком в режиме JSON, и через API, а вторая копия правила рано или
    поздно разошлась бы с первой.
    """
    from ..facts import FactPackError, clean_group  # noqa: PLC0415

    try:
        return clean_group(value)
    except FactPackError as error:
        raise ServiceError(str(error), 400) from error


def _date_or_empty(value: Any, field: str) -> str:
    """Дата вида ГГГГ-ММ-ДД либо пустая строка. Иначе — понятная ошибка."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not DATE_RE.fullmatch(text):
        raise ServiceError(f"{field}: дата в виде ГГГГ-ММ-ДД", 400)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ServiceError(f"{field}: такой даты не существует", 400) from None
    return text


# ------------------------------------------------------------- служебное ---

def _service(request: Request):
    return request.app.state.service


def _assistant(request: Request):
    return request.app.state.assistant


def _domains(request: Request):
    """Справочник направлений — тот же, по которому приём раскладывает документы.

    Здесь стоял путь `templates_dir/domains.json`, а приём читает
    `settings.domains_path`. Пока это один и тот же файл, разницы не видно; а
    стоит задать справочник отдельно — и приём раскладывает документы верно,
    зато в интерфейсе список направлений пуст и у всех документов «не
    указано». Причём данные при этом целы: расходится только показ.
    """
    settings = _settings(request)
    path = getattr(settings, "domains_path", None)
    if not path:
        path = Path(settings.templates_dir) / "domains.json"
    return domain_registry(path)


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
        raise ServiceError("письмо не найдено", 404)
    return case


def _report_or_404(request: Request, report_id: int) -> Report:
    report = _repos(request).reports.get(report_id)
    if report is None:
        raise ServiceError("отчёт не найден", 404)
    return report


def _report_payload(service, report: Report, *, with_markdown: bool = True) -> Dict[str, Any]:
    data = report.to_dict(with_markdown=with_markdown)
    data["sources"] = service.sources(report)
    data["facts_stale"] = service.facts_are_stale(report)
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
    """Справочники интерфейса: шаблоны отчётов, типы документов, направления.

    Требует входа. Окно входа этих сведений не запрашивает — оформление оно
    берёт отдельным маршрутом, — а состав шаблонов, перечень направлений
    работы и адрес модели постороннему знать незачем.
    """
    require_user(request)
    settings = _settings(request)
    service = _service(request)
    outlines = []
    for outline in service.outlines.all().values():
        outlines.append({
            "report_type": outline.report_type,
            "title": outline.title,
            "short_title": outline.short_title or outline.title,
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
        # Линии связи, по которым работает отдел. Отдаём справочником, а не
        # зашиваем в интерфейс: список читают и форма регистрации, и фильтр
        # списка писем, и разойтись они не должны.
        "line_types": [
            {"id": key, "title": LINE_TITLES[key], "full": LINE_FULL_TITLES[key]}
            for key in LINE_TYPES
        ],
        "doc_types": list(DOC_TYPES),
        "statuses": [{"id": key, "title": title} for key, title in DOC_STATUS_TITLES.items()],
        "llm": {"model": settings.llm_model, "base_url": settings.llm_base_url,
                "kind": settings.llm_kind},
        "auth_enabled": settings.auth_enabled,
        "brand": {
            "name": settings.brand_name,
            "short": settings.brand_short,
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
               limit: int = 100, offset: int = 0, assignee: int | None = None,
               overdue: bool = False, q: str = "") -> Dict[str, Any]:
    """Список писем. status=open — всё, что в работе; overdue — просроченные."""
    require_user(request)
    repos = _repos(request)
    cases = repos.cases.list(
        status=status,
        limit=min(limit, 500),
        offset=max(offset, 0),
        assignee_id=assignee,
        overdue_before=_today() if overdue else None,
        query=q[:MAX_QUERY_LEN],
    )
    # Чем нашлось письмо, человеку видно не всегда: искомого слова может не
    # быть ни в теме, ни в номере — оно в тексте отчёта. Помечаем такие.
    by_text = repos.cases.matched_by_text(
        q[:MAX_QUERY_LEN], [case.id for case in cases]) if q.strip() else set()
    items = []
    for case in cases:
        row = case.to_dict()
        row["found_in_report"] = case.id in by_text
        items.append(row)
    return {
        "items": items,
        # Считаем то же, что показываем, по ВСЕМ отборам — не только по
        # поиску: на вкладке «Просроченные» выходило «показаны 2 из 5».
        "total": repos.cases.count(
            status, assignee_id=assignee, query=q[:MAX_QUERY_LEN],
            overdue_before=_today() if overdue else None),
        "open": repos.cases.count("open"),
        "overdue": repos.board.deadline_counts(_today(), _today())["late"],
        "today": _today(),
    }


@router.patch("/cases/{case_ref}")
def update_case_card(request: Request, case_ref: int) -> Dict[str, Any]:
    """Карточка письма: исполнитель, срок, входящий номер, приоритет, статус."""
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    repos = _repos(request)
    payload = _body(request)

    fields: Dict[str, Any] = {}
    for name in MAX_CARD_FIELDS:
        if name in payload:
            fields[name] = _card_line(payload[name], name)
    # Исходящий номер вписывается отправкой ответа, а не правкой карточки:
    # иначе письмо числилось бы отправленным без проверенного отчёта.
    if "outgoing_no" in fields:
        raise ServiceError(
            "исходящий номер записывается при отправке ответа: откройте письмо "
            "и нажмите «Отправлено»", 409)
    # Наружу поле зовётся group_no, колонка в базе — customer (см. schema.sql).
    if "group_no" in payload or "customer" in payload:
        fields["customer"] = _group_or_empty(
            payload.get("group_no", payload.get("customer")))
    for name in ("incoming_date", "deadline"):
        if name in payload:
            fields[name] = _date_or_empty(payload[name], name)
    if "priority" in payload:
        priority = str(payload["priority"] or "normal")
        if priority not in CASE_PRIORITIES:
            raise ServiceError(f"неизвестный приоритет '{priority}'", 400)
        fields["priority"] = priority
    if "line_type" in payload:
        fields["line_type"] = _line_or_empty(payload["line_type"])
    if "status" in payload:
        status = str(payload["status"] or "")
        if status not in CASE_STATUSES:
            raise ServiceError(f"неизвестное состояние '{status}'", 400)
        if status != case.status:
            _guard_case_status(repos, case, status)
        fields["status"] = status
    if "assignee_id" in payload:
        raw = payload["assignee_id"]
        if raw in (None, "", 0):
            fields["assignee_id"] = None
        else:
            assignee = repos.users.get(int(raw))
            if assignee is None or not assignee.active:
                raise ServiceError("исполнитель не найден или отключён", 400)
            fields["assignee_id"] = assignee.id

    updated = _service(request).update_card(case, fields, user)
    repos.audit.log("case.update", user=user, object_type="case",
                    object_id=case.case_id, details=fields)
    return {"case": updated.to_dict() if updated else None}


@router.post("/cases")
def create_case(request: Request) -> Dict[str, Any]:
    """Регистрация входящего письма."""
    user = require_editor(request)
    service = _service(request)
    payload = _body(request)
    # Даты и приоритет проверяем здесь: сервисному слою достаётся уже
    # проверенное, а инженер видит понятную ошибку вместо отказа базы.
    for field in ("incoming_date", "deadline"):
        if field in payload:
            payload[field] = _date_or_empty(payload[field], field)
    priority = str(payload.get("priority") or "normal")
    if priority not in CASE_PRIORITIES:
        raise ServiceError(f"неизвестный приоритет '{priority}'", 400)
    payload["priority"] = priority
    payload["line_type"] = _line_or_empty(payload.get("line_type"))
    if "group_no" in payload or "customer" in payload:
        payload["group_no"] = _group_or_empty(
            payload.get("group_no", payload.get("customer")))
    for name in MAX_CARD_FIELDS:
        if name in payload:
            payload[name] = _card_line(payload[name], name)
    if payload.get("assignee_id"):
        assignee = _repos(request).users.get(int(payload["assignee_id"]))
        if assignee is None or not assignee.active:
            raise ServiceError("исполнитель не найден или отключён", 400)
    case = service.create_case(payload, user)
    return {"case": case.to_dict(with_facts=True), "coverage": service.coverage(case)}


#: Что кладут к письму: само письмо сканом, схема линии, журнал измерений,
#: выгрузка анализатора. Список широкий намеренно — отдел приносит разное, и
#: запрещать формат значит заставлять человека искать обходной путь.
CASE_FILE_SUFFIXES = (
    ".pdf", ".docx", ".doc", ".rtf", ".odt", ".xlsx", ".xls", ".csv",
    ".md", ".txt", ".log", ".json", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".zip", ".7z", ".rar",
)


@router.get("/cases/{case_ref}/files")
def list_case_files(request: Request, case_ref: int) -> Dict[str, Any]:
    """Бумаги, приложенные к письму. Смотреть может любой сотрудник."""
    require_user(request)
    case = _case_or_404(request, case_ref)
    items = _repos(request).case_files.list_for_case(case.id)
    return {"files": [item.to_dict() for item in items]}


@router.post("/cases/{case_ref}/files")
def attach_to_case(request: Request, case_ref: int,
                   file: UploadFile = File(...),
                   note: str = Form("")) -> Dict[str, Any]:
    """Приложить к письму бумагу.

    Файл остаётся на диске подлинником: письмо, пришедшее сканом, потом
    поднимают целиком, а не пересказом. Текст из него разбирается тут же и
    кладётся в поиск — иначе приложенную схему нельзя было бы найти по
    словам, и человек искал бы её глазами по всему журналу.
    """
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    settings = _settings(request)
    repos = _repos(request)

    name = _safe_name(Path(file.filename or "файл").name)
    if not name:
        raise ServiceError("некорректное имя файла", 400)
    suffix = Path(name).suffix.lower()
    if suffix not in CASE_FILE_SUFFIXES:
        known = ", ".join(CASE_FILE_SUFFIXES)
        raise ServiceError(f"такие файлы к письму не прикладывают (можно: {known})", 400)

    settings.ensure_dirs()
    target_dir = Path(settings.data_dir) / "case-files" / str(case.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    # Имя на диске с случайной приставкой: две бумаги с именем «письмо.pdf»
    # к одному письму — обычное дело, и вторая не должна затирать первую.
    target = target_dir / f"{secrets.token_hex(6)}-{name}"

    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with target.open("wb") as stream:
            while True:
                piece = file.file.read(1024 * 1024)
                if not piece:
                    break
                size += len(piece)
                if size > limit:
                    raise ServiceError(
                        f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
                stream.write(piece)
        if not size:
            raise ServiceError("файл пустой", 400)
        # Не прочиталось — не беда: подлинник на месте, откроют как есть.
        text, _problem = _extract_attachment(target)
        item = repos.case_files.add(
            case.id, name=name, path=str(target), size=size,
            text=text.strip(), note=str(note or "").strip()[:300],
            user_id=user.id if user else None)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    repos.audit.log("case.attach", user=user, object_type="case",
                    object_id=case.case_id, details={"name": name, "bytes": size})
    return {"file": item.to_dict()}


@router.get("/cases/{case_ref}/files/{file_id}")
def download_case_file(request: Request, case_ref: int, file_id: int) -> FileResponse:
    """Отдать приложенную бумагу подлинником."""
    require_user(request)
    case = _case_or_404(request, case_ref)
    item = _repos(request).case_files.get(file_id)
    if item is None or item.case_ref != case.id:
        raise ServiceError("файл не найден", 404)
    path = Path(item.path)
    if not path.is_file():
        raise ServiceError("файл не найден на диске", 404)
    return FileResponse(path, filename=item.name,
                        headers={"Content-Disposition": _disposition(item.name)})


@router.delete("/cases/{case_ref}/files/{file_id}")
def detach_from_case(request: Request, case_ref: int, file_id: int) -> Dict[str, Any]:
    """Убрать приложенную бумагу.

    Отправленное письмо не трогаем: убрать из него исходную бумагу задним
    числом — это правка того, что уже ушло адресату.
    """
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    repos = _repos(request)
    _service(request).guard_not_sent(case, "убрать из него приложенный файл")
    item = repos.case_files.get(file_id)
    if item is None or item.case_ref != case.id:
        raise ServiceError("файл не найден", 404)
    path = repos.case_files.delete(file_id)
    if path:
        Path(path).unlink(missing_ok=True)
    repos.audit.log("case.detach", user=user, object_type="case",
                    object_id=case.case_id, details={"name": item.name})
    return {"ok": True}


@router.get("/cases/{case_ref}")
def get_case(request: Request, case_ref: int) -> Dict[str, Any]:
    require_user(request)
    case = _case_or_404(request, case_ref)
    service = _service(request)
    repos = _repos(request)
    reports = repos.reports.list_for_case(case.id)
    # Покрытие считается по факт-пакету, и на битом пакете расчёт падает.
    # Раньше вместе с ним падал весь ответ, и письмо нельзя было даже
    # открыть, чтобы пакет починить. Теперь письмо открывается, а вместо
    # покрытия приходит причина.
    try:
        coverage = service.coverage(case)
        coverage_error = ""
    except ServiceError as error:
        coverage, coverage_error = None, str(error)

    return {
        "case": case.to_dict(with_facts=True),
        "coverage": coverage,
        "coverage_error": coverage_error,
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


@router.post("/cases/reindex")
def reindex_cases(request: Request) -> Dict[str, Any]:
    """Перестроить поисковый указатель по письмам.

    Обычно он строится сам: при каждой правке письма или отчёта, а на
    базе без указателя — при первом запуске. Но перестроение может
    оборваться на середине, и тогда часть писем не находится, а признака
    «указатель пуст» уже нет. Кнопка на такой случай — как и у библиотеки.
    """
    user = require_admin(request)
    repos = _repos(request)
    built = repos.case_search.rebuild_all()
    repos.audit.log("cases.reindex", user=user, object_type="case",
                    object_id="", details={"cases": built})
    return {"ok": True, "cases": built}


@router.post("/cases/{case_ref}/send")
def send_case(request: Request, case_ref: int) -> Dict[str, Any]:
    """Ответ по письму отправлен: записать исходящий номер.

    Последний шаг порядка отдела. Делает исполнитель — тот же, кто готовил
    и сдавал отчёт: отправляют ответы все, а проверяет начальник.
    """
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    payload = _body(request)
    updated = _service(request).send_out(
        case,
        _card_line(payload.get("outgoing_no", ""), "outgoing_no"),
        _date_or_empty(payload.get("outgoing_date", _today()), "outgoing_date"),
        user,
    )
    return {"case": updated.to_dict()}


@router.post("/cases/{case_ref}/unsend")
def unsend_case(request: Request, case_ref: int) -> Dict[str, Any]:
    """Отозвать отправку: номер вписали не тот или ответ ушёл не тому.

    Право проверяющего: запись об отправке — учётная, и снимать её должен
    тот, кто отвечает за проверку, а не любой сотрудник.
    """
    user = require_reviewer(request)
    case = _case_or_404(request, case_ref)
    updated = _service(request).withdraw_sending(case, user)
    return {"case": updated.to_dict()}


@router.delete("/cases/{case_ref}")
def delete_case(request: Request, case_ref: int) -> Dict[str, Any]:
    user = require_admin(request)
    case = _case_or_404(request, case_ref)
    # Сданные файлом отчёты лежат на диске: строки из базы уходят каскадом,
    # а файлы остались бы навсегда. Интерфейс обещает удаление вместе со
    # всеми редакциями отчёта — значит, и с их файлами.
    # Приложенные к письму бумаги лежат там же на диске: удаление письма
    # обещано вместе со всем, что к нему относится.
    data_dir = Path(_settings(request).data_dir)
    folders = [data_dir / "reports" / str(case.id),
               data_dir / "case-files" / str(case.id)]
    _repos(request).cases.delete(case.id)
    removed = 0
    for folder in folders:
        if not folder.is_dir():
            continue
        for item in folder.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
                removed += 1
        with suppress(OSError):
            folder.rmdir()
    _repos(request).audit.log("case.delete", user=user, object_type="case",
                              object_id=case.case_id, details={"files": removed})
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
        raise ServiceError("по этому письму отчёт ещё не готовили", 404)
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
    # «Ошибок нет» и «сверять было нечем» — разные ответы. У сданного файлом
    # отчёта факт-пакета нет, и пустой список замечаний не значит, что
    # документ проверен: его читает начальник, а не программа.
    checked = report.source != "uploaded"
    return {
        "issues": issues,
        "errors": sum(1 for issue in issues if issue["level"] == "error"),
        "warnings": sum(1 for issue in issues if issue["level"] == "warning"),
        "checked": checked,
        "note": "" if checked else "отчёт сдан файлом: сверять с исходными данными нечего",
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


#: Форматы готового отчёта, который сдают на проверку. Word и PDF — то, в чём
#: отчёты пишут; Markdown и текст — то, во что их выгружает сама система.
REPORT_UPLOAD_SUFFIXES = (".docx", ".doc", ".pdf", ".rtf", ".odt", ".md", ".txt")


@router.post("/reports/upload")
def upload_report(
    request: Request,
    file: UploadFile = File(...),
    case_id: str = Form(""),
    incoming_no: str = Form(""),
    incoming_date: str = Form(""),
    group_no: str = Form(""),
    title: str = Form(""),
    deadline: str = Form(""),
    priority: str = Form("normal"),
    assignee_id: str = Form(""),
    report_type: str = Form(""),
    note: str = Form(""),
) -> Dict[str, Any]:
    """Сдать готовый отчёт файлом на проверку начальнику.

    Загружать может любой сотрудник — свои отчёты в отдел сдают все.
    Исполнителем по умолчанию становится тот, кто загрузил: чаще всего он
    же его и писал. Письмо под отчёт заводится тем же действием, чтобы не
    заставлять человека делать два дела вместо одного.

    Числа такого отчёта не сверяются с факт-пакетом: его нет и быть не
    может — документ написан человеком целиком. Об этом сказано и в
    карточке, и в списке писем, чтобы проверенный файл не путали с
    отчётом, прошедшим машинную проверку.
    """
    user = require_editor(request)
    repos = _repos(request)
    settings = _settings(request)
    service = _service(request)

    name = _safe_name(Path(file.filename or "отчёт").name)
    suffix = Path(name).suffix.lower()
    if suffix not in REPORT_UPLOAD_SUFFIXES:
        raise ServiceError(
            "отчёт принимается в форматах: " + ", ".join(REPORT_UPLOAD_SUFFIXES), 400)

    # Те же пределы, что и в карточке: сдача файлом заводит письмо, и
    # строки в нём должны быть такими же, как у зарегистрированного руками.
    incoming_no = _card_line(incoming_no, "incoming_no")
    title = _card_line(title, "title") or Path(name).stem
    note = _card_line(note, "note")
    case_id = str(case_id or "").strip() or incoming_no
    if not case_id:
        raise ServiceError("укажите входящий номер письма", 400)

    assignee = user
    if str(assignee_id or "").strip():
        found = repos.users.get(int(assignee_id))
        if found is None or not found.active:
            raise ServiceError("исполнитель не найден или отключён", 400)
        assignee = found

    case = repos.cases.by_case_id(case_id)
    if case is None:
        payload = {
            "case_id": case_id,
            "report_type": report_type or _default_report_type(request),
            "title": title,
            "group_no": _group_or_empty(group_no),
            "incoming_no": incoming_no,
            "incoming_date": _date_or_empty(incoming_date, "incoming_date"),
            "deadline": _date_or_empty(deadline, "deadline"),
            "priority": priority if priority in CASE_PRIORITIES else "normal",
            "assignee_id": assignee.id,
            "note": note,
            "facts": {"case_id": case_id, "group_no": _group_or_empty(group_no),
                      "measurements": {}},
        }
        try:
            case = service.create_case(payload, user)
        except ServiceError as error:
            # Письмо успели завести, пока мы собирались: два человека сдают
            # отчёты по одному входящему разом или кто-то нажал дважды.
            # Заводить нечего — сдаём по тому, что уже есть.
            case = repos.cases.by_case_id(case_id)
            if case is None:
                raise error
        taken = ""
    else:
        # Ответ по письму уже ушёл под исходящим номером — сдавать по нему
        # новый отчёт нельзя: письмо вернулось бы «на проверку», сохранив
        # запись об отправке, и в учёте вышла бы небылица.
        service.guard_not_sent(case, "сдать по нему отчёт")
        # Письмо уже заведено — сдаём по нему ещё одну редакцию отчёта.
        # Реквизиты, которые человек ввёл, применяем: он вводил их не зря.
        # Пустые поля не трогают того, что в письме уже записано.
        fields: Dict[str, Any] = {}
        if title and title != Path(name).stem:
            fields["title"] = title
        # Входящий номер терялся: в наборе полей его не было вовсе. Письмо,
        # заведённое сдачей файла без номера, оставалось без него навсегда,
        # и по номеру такое письмо было не найти.
        if incoming_no and not case.incoming_no:
            fields["incoming_no"] = incoming_no
        if str(group_no or "").strip():
            fields["customer"] = _group_or_empty(group_no)
        if str(incoming_date or "").strip():
            fields["incoming_date"] = _date_or_empty(incoming_date, "incoming_date")
        if str(deadline or "").strip():
            fields["deadline"] = _date_or_empty(deadline, "deadline")
        if priority in CASE_PRIORITIES and priority != "normal":
            fields["priority"] = priority
        if str(note or "").strip():
            fields["note"] = str(note).strip()
        # Исполнителя переписываем, только если его не было. Иначе сдача
        # отчёта по чужому письму молча переводила бы письмо на себя.
        taken = ""
        if case.assignee_id is None:
            fields["assignee_id"] = assignee.id
        elif case.assignee_id != assignee.id:
            taken = (f"исполнитель письма не изменён: он уже назначен "
                     f"({case.assignee_name or 'другой сотрудник'})")
        if fields:
            service.update_card(case, fields, user)
            repos.audit.log("case.update", user=user, object_type="case",
                            object_id=case.case_id, details=fields)
            case = repos.cases.get(case.id) or case

    # Каталог по номеру письма в базе, а не по его учётному номеру: разные
    # номера после чистки имени совпадают («ВХ-2026/0423» и «ВХ-2026-0423»),
    # и два письма писали бы отчёты в один каталог.
    settings.ensure_dirs()
    target_dir = Path(settings.data_dir) / "reports" / str(case.id)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Пишем во временный файл: номер редакции присваивает база при вставке
    # строки, и только после неё известно, как файл назвать. Считать номер
    # заранее нельзя — две одновременные сдачи получили бы один и тот же.
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    # Расширение временному файлу обязательно: формат разбирают по нему, и
    # без него текст не читался ни из одного сданного отчёта — в карточке
    # оставался только файл, а прочитать и найти его было нельзя.
    handle, temp_name = tempfile.mkstemp(dir=str(target_dir), prefix="sdacha-", suffix=suffix)
    target = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            while True:
                piece = file.file.read(1024 * 1024)
                if not piece:
                    break
                size += len(piece)
                if size > limit:
                    raise ServiceError(
                        f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
                stream.write(piece)
        if not size:
            raise ServiceError("файл пустой", 400)

        # Текст нужен, чтобы отчёт можно было прочитать и найти, не скачивая.
        # Не прочитался — не беда: файл на месте, начальник откроет его как есть.
        text, problem = _extract_attachment(target)
        try:
            report = repos.reports.create_uploaded(
                case.id, markdown=text.strip(), file_name=name, file_path=str(target),
                file_size=size, user_id=user.id if user else None)
        except sqlite3.IntegrityError as error:
            # Две сдачи по одному письму столкнулись на номере редакции.
            raise ServiceError(
                "по этому письму прямо сейчас сдают отчёт — повторите через "
                "несколько секунд", 409) from error

        # Прежние версии уходят с проверки: начальник читает то, что сдали
        # последним, а не то, что исполнитель уже заменил.
        service.withdraw_previous(case, report, user)

        # Теперь номер версии известен — даём файлу постоянное имя.
        final = target_dir / f"v{report.version}-{name}"
        target.replace(final)
        repos.reports.set_file_path(report.id, str(final))
        target = final
    except BaseException:
        if target.exists() and target.name.startswith("sdacha-"):
            target.unlink(missing_ok=True)
        raise
    repos.cases.set_status(case.id, "review")
    repos.audit.log("report.upload", user=user, object_type="report",
                    object_id=str(report.id),
                    details={"case_id": case.case_id, "file": name, "bytes": size})
    return {
        "report": _report_payload(service, report),
        "case": repos.cases.get(case.id).to_dict(),  # type: ignore[union-attr]
        "note": "; ".join(x for x in (problem, taken) if x),
    }


def _default_report_type(request: Request) -> str:
    """Тип отчёта для загруженного файла: любой из заведённых.

    Загруженный отчёт по шаблону не собирается, тип ему нужен только чтобы
    письмо было полноценным — потом по нему же можно собрать и свой.
    """
    outlines = _service(request).outlines.all()
    if not outlines:
        raise ServiceError("не заведено ни одного шаблона отчёта", 500)
    return sorted(outlines)[0]


@router.post("/reports/{report_id}/submit")
def submit(request: Request, report_id: int) -> Dict[str, Any]:
    """Отправить отчёт на проверку начальнику. Может любой сотрудник."""
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    return {"report": _report_payload(service, service.submit(report, user))}


@router.post("/reports/{report_id}/approve")
def approve(request: Request, report_id: int) -> Dict[str, Any]:
    """Отметить отчёт проверенным. Только начальник отдела или заместитель."""
    user = require_reviewer(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    approved = service.approve(report, user)
    return {"report": _report_payload(service, approved)}


@router.post("/reports/{report_id}/rework")
def send_back(request: Request, report_id: int) -> Dict[str, Any]:
    """Вернуть отчёт исполнителю с замечанием. Только проверяющий."""
    user = require_reviewer(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    note = str(_body(request).get("note", ""))
    return {"report": _report_payload(service, service.send_back(report, note, user))}


@router.get("/reports/{report_id}/sources")
def report_sources(request: Request, report_id: int) -> Dict[str, Any]:
    require_user(request)
    report = _report_or_404(request, report_id)
    return {"items": _service(request).sources(report)}


@router.get("/reports/{report_id}/file")
def download_report_file(request: Request, report_id: int) -> FileResponse:
    """Отдать загруженный отчёт тем же файлом, каким его сдали.

    Смотреть отчёты может любой сотрудник: система для того и заведена,
    чтобы отдел видел, что кем сделано.
    """
    require_user(request)
    report = _report_or_404(request, report_id)
    if report.source != "uploaded":
        raise ServiceError("этот отчёт собран системой — выгрузите его в DOCX", 404)
    path = Path(_repos(request).reports.file_path(report.id))
    if not path.is_file():
        raise ServiceError("файл отчёта не найден на диске", 404)
    return FileResponse(
        path, filename=report.file_name,
        headers={"Content-Disposition": _disposition(report.file_name)},
    )


def _refuse_export_of_a_file(report: Report) -> None:
    """Сданный файлом отчёт наружу отдаётся подлинником, а не пересборкой.

    Текст такого отчёта — машинное чтение чужого документа: оформление,
    таблицы и подписи в нём уже потеряны. Собрать из него DOCX по
    фирменному бланку значит выдать пересказ за отчёт — и его отправят
    вместо подлинника. Подлинник отдаёт GET /api/reports/{id}/file.
    """
    if report.source == "uploaded":
        raise ServiceError(
            "этот отчёт сдан готовым файлом — выгружать его заново незачем: "
            "подлинник отдаёт кнопка «Скачать файл»", 409)


@router.get("/reports/{report_id}/export.md")
def export_markdown(request: Request, report_id: int) -> Response:
    require_user(request)
    report = _report_or_404(request, report_id)
    _refuse_export_of_a_file(report)
    report = _service(request).for_export(report)
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
    _refuse_export_of_a_file(report)
    report = _service(request).for_export(report)
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
            case_id=case.case_id, incoming_no=case.incoming_no,
            outgoing_no=case.outgoing_no, status=report.status,
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
            domain: str | None = None, status: str | None = None) -> Dict[str, Any]:
    require_user(request)
    repos = _repos(request)
    documents = repos.documents.list(doc_type, domain, status)
    return {
        "items": [document.to_dict() for document in documents],
        "stats": repos.documents.stats(),
        "domains": repos.documents.domains(),
        "statuses": repos.documents.statuses(),
        "chunks": repos.chunks.count(),
        "embeddings": repos.vectors.count(),
    }


@router.get("/library/{doc_id:path}/text")
def document_text(request: Request, doc_id: str) -> Dict[str, Any]:
    """Что система на самом деле вычитала из файла.

    Главный инструмент проверки качества: по этому тексту видно, распознался
    ли скан, не рассыпалась ли таблица и не приехали ли вместо букв заглушки.
    Отдаём и текст целиком, и фрагменты — ровно те, по которым идёт поиск.
    """
    require_user(request)
    repos = _repos(request)
    document = repos.documents.by_doc_id(doc_id)
    if document is None:
        raise ServiceError(f"документ '{doc_id}' не найден", 404)
    chunks = repos.chunks.for_document(document.id)
    source = Path(document.source_path)
    return {
        "document": document.to_dict(),
        "source_exists": source.is_file(),
        "source_name": source.name,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "title_path": list(chunk.title_path or []),
                "text": chunk.text,
                "chars": len(chunk.text),
            }
            for chunk in chunks
        ],
        "text": "\n\n".join(chunk.text for chunk in chunks),
    }


@router.get("/library/{doc_id:path}/file")
def document_file(request: Request, doc_id: str):
    """Отдать исходный файл — тот самый, что лежит в библиотеке на диске."""
    require_user(request)
    repos = _repos(request)
    settings = _settings(request)
    document = repos.documents.by_doc_id(doc_id)
    if document is None:
        raise ServiceError(f"документ '{doc_id}' не найден", 404)

    source = Path(document.source_path)
    # Отдаём только то, что лежит внутри библиотеки: source_path приходит из
    # базы, и без этой проверки правка записи превратилась бы в чтение любого
    # файла на машине.
    library = Path(settings.library_dir).resolve()
    try:
        resolved = source.resolve()
        resolved.relative_to(library)
    except (OSError, ValueError) as error:
        raise ServiceError("файл документа лежит вне каталога библиотеки", 403) from error
    if not resolved.is_file():
        raise ServiceError(f"исходный файл не найден: {source.name}", 404)

    return FileResponse(resolved, filename=resolved.name)


@router.post("/library/upload")
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form("literature"),
    domain: str = Form(""),
) -> Dict[str, Any]:
    user = require_editor(request)
    settings = _settings(request)
    if doc_type not in DOC_TYPES:
        raise ServiceError(f"неизвестный тип документа '{doc_type}'", 400)
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
                          domain=domain or None)
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
    result = ingest_directory(_repos(request), settings.library_dir, force=force,
                              domains_path=settings.domains_path)  # jobs — по числу ядер
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
    # Чем дополнился запрос по двуязычному словарю. Без этого выдача на
    # русский вопрос по английскому RFC выглядит необъяснимой: инженер видит
    # английский текст и не понимает, почему он нашёлся.
    expansion = list(getattr(retriever, "last_expansion", []) or [])
    return {
        "warning": warning or None,
        "expansion": expansion or None,
        "items": [
            {
                "chunk_uid": hit.chunk.chunk_id,
                "doc_type": hit.chunk.doc_type,
                "domain": hit.chunk.meta.get("domain", ""),
                "status": hit.chunk.meta.get("status", "current"),
                "citation": hit.chunk.citation,
                "text": " ".join(hit.chunk.text.split())[:600],
                "score": round(float(hit.score), 4),
                "rank": hit.rank,
            }
            for hit in hits
        ]
    }


# ---------------------------------------------------------- направления ---

@router.get("/llm/status")
def llm_status(request: Request) -> Dict[str, Any]:
    """Отвечает ли сервер модели. Спрашивает интерфейс при открытии.

    Отдельным запросом, а не в /api/config: проверка ходит по сети, и
    задерживать из-за неё показ первого экрана незачем.
    """
    require_user(request)
    llm = _service(request).get_llm()
    probe = getattr(llm, "available", None)
    settings = _settings(request)
    return {
        "available": bool(probe()) if callable(probe) else True,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
    }


@router.get("/formats")
def formats(request: Request) -> Dict[str, Any]:
    """Поддержка форматов документов: что читается, чего не хватает."""
    require_user(request)
    from ..ingest.convert import format_support, supported_suffixes  # noqa: PLC0415

    specs = format_support()
    return {
        "items": specs,
        "available": list(supported_suffixes(only_available=True)),
        "all": list(supported_suffixes()),
        "blocked": [spec for spec in specs if not spec["available"]],
    }


@router.get("/domains")
def domains(request: Request) -> Dict[str, Any]:
    require_user(request)
    return {
        "items": _domains(request).to_dict(),
        "documents": _repos(request).documents.domains(),
    }


@router.put("/library/{doc_id:path}/status")
def set_document_status(request: Request, doc_id: str) -> Dict[str, Any]:
    """Отметить актуальность документа.

    Заменённый и архивный документ пропадает из поиска: цитировать отменённую
    редакцию стандарта как действующую — прямая ошибка в отчёте.
    Сам документ остаётся в библиотеке для разбора старых обращений.
    """
    user = require_editor(request)
    payload = _body(request)
    status = str(payload.get("status", "")).strip()
    if status not in DOC_STATUSES:
        raise ServiceError(
            f"неизвестный статус '{status}' (допустимы: {', '.join(DOC_STATUSES)})", 400
        )
    superseded_by = str(payload.get("superseded_by", "")).strip()
    repos = _repos(request)
    if repos.documents.by_doc_id(doc_id) is None:
        raise ServiceError("документ не найден", 404)
    if superseded_by and repos.documents.by_doc_id(superseded_by) is None:
        raise ServiceError(f"документ на замену не найден: {superseded_by}", 400)

    repos.documents.set_status(doc_id, status, superseded_by)
    repos.audit.log("library.status", user=user, object_type="document",
                    object_id=doc_id, details={"status": status, "superseded_by": superseded_by})
    _service(request).reset_retriever()
    return {"ok": True, "document": repos.documents.by_doc_id(doc_id).to_dict()}


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
    repos = _repos(request)
    return {
        "chat": chat.to_dict(),
        "messages": [message.to_dict() for message in assistant.messages(user, chat_id)],
        # Без текста: на экране от вложения нужны имя, вид и длина.
        "attachments": [item.to_dict()
                        for item in repos.chats.attachments(chat_id, with_text=False)],
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


# ---------------------------------------------- вложения к вопросу --------

#: Что можно приложить к вопросу. Расширение решает, как файл читать;
#: сам разбор делает тот же конвертер, что и приём библиотеки.
ATTACH_IMAGE = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
ATTACH_DUMP = {".txt", ".log", ".csv", ".json", ".xml", ".pcap", ".pcapng", ".cap", ".har"}


def _attachment_kind(suffix: str) -> str:
    if suffix in ATTACH_IMAGE:
        return "image"
    if suffix in ATTACH_DUMP:
        return "dump"
    return "document"


@router.post("/chats/{chat_id}/attachments")
def attach_to_chat(request: Request, chat_id: int, file: UploadFile = File(...)) -> Dict[str, Any]:
    """Приложить к вопросу дамп, снимок экрана или документ.

    Файл разбирается сразу и текстом остаётся в разговоре: диск можно
    чистить, а разбор инженеру ещё понадобится.
    """
    user = require_user(request)
    assistant = _assistant(request)
    assistant.get_chat(user, chat_id)          # чужой разговор — 404
    settings = _settings(request)
    repos = _repos(request)

    name = _safe_name(Path(file.filename or "файл").name)
    if not name:
        raise ServiceError("некорректное имя файла", 400)

    settings.ensure_dirs()
    target = Path(settings.upload_dir) / f"chat-{chat_id}-{secrets.token_hex(6)}-{name}"
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
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
                        f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
                stream.write(piece)

        text, note = _extract_attachment(target)
    finally:
        # Разбор сохранён в базе, копия файла на диске больше не нужна:
        # каталог загрузок иначе растёт от каждого заданного вопроса.
        target.unlink(missing_ok=True)

    item = repos.chats.add_attachment(
        chat_id, name, _attachment_kind(Path(name).suffix.lower()),
        size=size, text=text, note=note,
    )
    repos.audit.log("chat.attach", user=user, object_type="chat",
                    object_id=str(chat_id), details={"name": name, "bytes": size})
    return {"attachment": item.to_dict()}


@router.delete("/chats/{chat_id}/attachments/{attachment_id}")
def detach_from_chat(request: Request, chat_id: int, attachment_id: int) -> Dict[str, Any]:
    user = require_user(request)
    assistant = _assistant(request)
    assistant.get_chat(user, chat_id)
    repos = _repos(request)
    item = repos.chats.attachment(attachment_id)
    if item is None or item.chat_id != chat_id:
        raise ServiceError("вложение не найдено", 404)
    repos.chats.delete_attachment(attachment_id)
    return {"ok": True}


#: Расширения, которых нет в приёме библиотеки, но которые инженер приносит
#: в разговор постоянно. Внутри это обычный текст, читаем как текст.
PLAIN_ATTACH = {".log", ".json", ".har", ".ini", ".conf", ".cfg", ".yaml", ".yml", ".out"}

#: Двоичные захваты. Разбирать их нечем, но сказать, что делать, можно.
CAPTURE_ATTACH = {
    ".pcap": "Wireshark: Файл → Экспортировать пакеты → Как обычный текст",
    ".pcapng": "Wireshark: Файл → Экспортировать пакеты → Как обычный текст",
    ".cap": "Wireshark: Файл → Экспортировать пакеты → Как обычный текст",
}


def _extract_attachment(path: Path) -> tuple[str, str]:
    """Текст файла и, если что-то пошло не так, объяснение по-русски."""
    suffix = path.suffix.lower()
    if suffix in CAPTURE_ATTACH:
        return "", (
            "двоичный захват прочитать нечем — приложите текстовую выгрузку "
            f"({CAPTURE_ATTACH[suffix]})"
        )
    try:
        from ..ingest.convert import convert_file, decode_bytes  # noqa: PLC0415
    except ImportError:
        return "", "модуль разбора файлов недоступен"

    if suffix in PLAIN_ATTACH:
        # Внутри это текст, просто расширение приёму библиотеки незнакомо.
        # Кодировку определяем так же, как для .txt: логи с изолированной
        # машины приходят и в UTF-8, и в cp1251.
        try:
            text, _encoding, problem = decode_bytes(path.read_bytes())
        except OSError as error:
            return "", f"файл прочитать не удалось: {error}"
        return text, problem or ""

    try:
        converted = convert_file(path)
    except Exception as error:          # noqa: BLE001 — вопрос важнее вложения
        return "", f"файл прочитать не удалось: {error}"
    note = "; ".join(converted.warnings[:2])
    if converted.is_empty and not note:
        note = "в файле не нашлось текста — возможно, это скан без распознавания"
    return converted.text, note


# ------------------------------------------------------------ сотрудники --

#: Что роль позволяет делать — показывается прямо в форме, чтобы не гадать.
def _user_public(user) -> Dict[str, Any]:
    return user.to_dict()


def _may_manage(actor, target) -> bool:
    """Может ли actor менять запись target.

    Правило одно: своё старшинство должно быть строго выше. Начальник группы
    не переназначает начальника отдела, заместитель не трогает создателя.
    Создателя не может тронуть никто, включая его самого, — иначе система
    остаётся без владельца, а чинить это на изолированной машине нечем.
    """
    if target.role == "owner":
        return False
    return actor.rank > target.rank or actor.is_owner


@router.get("/users")
def list_users(request: Request) -> Dict[str, Any]:
    actor = require_admin(request)
    repos = _repos(request)
    items = []
    for user in repos.users.list_all():
        if user.login == "local":
            continue          # служебная запись режима без входа
        data = _user_public(user)
        data["may_manage"] = _may_manage(actor, user)
        items.append(data)
    return {
        "items": items,
        "roles": [
            {
                "id": role,
                "title": ROLE_TITLES.get(role, role),
                "note": ROLE_NOTES.get(role, ""),
                "is_admin": role in ADMIN_ROLES,
                # Должность выше собственной назначить нельзя.
                "allowed": actor.is_owner or ROLE_RANK.get(role, 0) < actor.rank,
            }
            for role in ROLES
        ],
        "admins": repos.users.count_admins(),
    }


@router.get("/staff")
def staff(request: Request) -> Dict[str, Any]:
    """Список сотрудников для выбора исполнителя.

    Отдельно от /api/users: тот доступен только администратору и отдаёт
    полную запись, а назначить исполнителя письма вправе любой сотрудник —
    в том числе взять письмо на себя. Здесь только то, что нужно списку.
    """
    require_user(request)
    return {
        "items": [
            {
                "id": user.id,
                "login": user.login,
                "full_name": user.full_name or user.login,
                "role": user.role,
                "role_title": user.role_title,
                "department": user.department,
                "team": user.team,
                # Как человека найти. Не личные сведения: телефон и кабинет
                # в отделе и так знают, а искать их по бумажке — терять время.
                "phone": user.phone,
                "ext_no": user.ext_no,
                "room": user.room,
                "email": user.email,
            }
            for user in _repos(request).users.list_all(active_only=True)
            if user.login != "local"
        ]
    }


@router.post("/users")
def create_user(request: Request) -> Dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    payload = _body(request)

    login = str(payload.get("login", "")).strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,32}", login):
        raise ServiceError(
            "логин: от 3 до 32 знаков, латиница, цифры, точка, дефис или подчёркивание", 400
        )
    if repos.users.by_login(login) is not None:
        raise ServiceError(f"пользователь '{login}' уже есть", 409)

    password = str(payload.get("password", ""))
    if len(password) < 8:
        raise ServiceError("пароль короче 8 символов", 400)

    role = str(payload.get("role", "engineer"))
    if role not in ROLES:
        raise ServiceError(f"неизвестная должность '{role}'", 400)
    if role == "owner":
        raise ServiceError("создатель системы в единственном числе", 403)
    if not admin.is_owner and ROLE_RANK.get(role, 0) >= admin.rank:
        raise ServiceError("нельзя назначить должность выше собственной", 403)

    full_name = str(payload.get("full_name", "")).strip()
    user = repos.users.create(
        login, password, full_name=full_name, role=role,
        department=str(payload.get("department", "")).strip(),
        team=str(payload.get("team", "")).strip(),
    )
    repos.audit.log("user.create", user=admin, object_type="user", object_id=user.login,
                    details={"role": role})
    return {"user": _user_public(user)}


@router.patch("/users/{user_id}")
def update_user(request: Request, user_id: int) -> Dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("сотрудник не найден", 404)
    payload = _body(request)

    role = payload.get("role")
    if role is not None and role != user.role:
        if role not in ROLES:
            raise ServiceError(f"неизвестная должность '{role}'", 400)
        if not _may_manage(admin, user):
            raise ServiceError("нельзя менять должность сотруднику своего уровня или выше", 403)
        if role == "owner":
            raise ServiceError("создатель системы в единственном числе", 403)
        if not admin.is_owner and ROLE_RANK.get(role, 0) >= admin.rank:
            raise ServiceError("нельзя назначить должность выше собственной", 403)
        # Отдельная проверка «остался последний администратор» больше не нужна:
        # разжаловать можно только того, кто младше, значит сам разжалующий
        # администратором и остаётся. А создателя не трогает вообще никто.
    elif role is not None:
        role = None           # должность не меняется

    if not _may_manage(admin, user) and admin.id != user.id:
        raise ServiceError("недостаточно прав для правки этой записи", 403)

    updated = repos.users.update(
        user_id,
        full_name=_opt_str(payload, "full_name"),
        role=None if role is None else str(role),
        department=_opt_str(payload, "department"),
        team=_opt_str(payload, "team"),
    )
    repos.audit.log("user.update", user=admin, object_type="user", object_id=user.login,
                    details={k: payload.get(k) for k in ("role", "full_name", "department", "team")
                             if k in payload})
    return {"user": _user_public(updated)}


@router.post("/users/{user_id}/password")
def reset_user_password(request: Request, user_id: int) -> Dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("сотрудник не найден", 404)
    if not _may_manage(admin, user) and admin.id != user.id:
        raise ServiceError("недостаточно прав для смены этого пароля", 403)
    password = str(_body(request).get("password", ""))
    if len(password) < 8:
        raise ServiceError("пароль короче 8 символов", 400)
    repos.users.set_password(user_id, password)
    # Прежние сессии закрываем: смена пароля администратором — это и есть
    # способ отобрать доступ у того, кто его больше иметь не должен.
    repos.sessions.delete_for_user(user_id)
    repos.audit.log("user.password", user=admin, object_type="user", object_id=user.login)
    return {"ok": True}


@router.post("/users/{user_id}/active")
def set_user_active(request: Request, user_id: int) -> Dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("сотрудник не найден", 404)
    active = bool(_body(request).get("active", True))
    if not active and user.id == admin.id:
        raise ServiceError("нельзя отключить самого себя", 409)
    # Старшинство проверяем в обе стороны. Раньше — только при отключении, и
    # начальник группы возвращал доступ отключённому начальнику отдела: чужая
    # учётная запись старшего по должности не его дело ни в ту, ни в другую
    # сторону.
    if not _may_manage(admin, user):
        raise ServiceError(
            "недостаточно прав: этот сотрудник не младше вас по должности", 403
        )
    repos.users.set_active(user_id, active)
    if not active:
        repos.sessions.delete_for_user(user_id)
    repos.audit.log("user.active", user=admin, object_type="user", object_id=user.login,
                    details={"active": active})
    return {"user": _user_public(repos.users.get(user_id))}


# ------------------------------------------------------------- расход ----

#: Насколько далеко можно расписать расход одной записью. Год — предел
#: осмысленного: отпуск и учёба длятся месяцами, а «дежурство на пять лет»
#: это не расход, а описка, от которой сетка становится нечитаемой.
MAX_ROSTER_SPAN_DAYS = 366

#: Сколько дней показывает сетка расхода за раз. Две недели — предел, за
#: которым столбцы становятся уже подписи под ними.
MAX_ROSTER_WINDOW = 31


def _may_edit_roster(actor: User, user_id: int) -> bool:
    """Свой расход ведёт каждый; чужой — начальник, зам, создатель и начальник группы.

    Смысл расхода в том, что человек отмечает себя сам: иначе он собирается
    через начальника, устаревает за день и им никто не пользуется.
    """
    return actor.id == user_id or actor.is_admin


def _roster_bounds(payload: Dict[str, Any]) -> tuple[str, str]:
    start = _date_or_empty(payload.get("date_from"), "date_from")
    finish = _date_or_empty(payload.get("date_to"), "date_to") or start
    if not start:
        raise ServiceError("не указана дата начала", 400)
    if finish < start:
        raise ServiceError("дата окончания раньше даты начала", 400)
    if _days_between(start, finish) > MAX_ROSTER_SPAN_DAYS:
        raise ServiceError("одна запись расхода не может быть длиннее года", 400)
    return start, finish


def _days_between(start: str, finish: str) -> int:
    a = datetime.strptime(start, "%Y-%m-%d")
    b = datetime.strptime(finish, "%Y-%m-%d")
    return (b - a).days


@router.get("/roster")
def roster(request: Request, date_from: str = "", days: int = 7) -> Dict[str, Any]:
    """Расход отдела за промежуток: сетка «сотрудник × день».

    Готовую сетку собирает сервер, а не браузер. Раскладывать периоды по
    дням в трёх местах интерфейса — верный способ получить три разных
    расхода, а он в отделе один.
    """
    actor = require_user(request)
    repos = _repos(request)
    start = _date_or_empty(date_from, "date_from") or _today()
    span = min(max(int(days or 7), 1), MAX_ROSTER_WINDOW)
    finish = _shift(start, span - 1)

    days_list = [_shift(start, step) for step in range(span)]
    records = repos.absences.in_period_for_active(start, finish)

    staff = []
    for person in repos.users.list_all(active_only=True):
        staff.append({
            "id": person.id,
            "full_name": person.full_name or person.login,
            "role": person.role,
            "role_title": person.role_title,
            "team": person.team,
            # Расход отвечает «где человек»; телефон и кабинет — вторая
            # половина того же вопроса, и держать их в другом разделе значит
            # заставлять ходить туда-обратно.
            "phone": person.phone,
            "ext_no": person.ext_no,
            "room": person.room,
            "can_edit": _may_edit_roster(actor, person.id),
            "is_me": person.id == actor.id,
        })

    # Раскладка по дням: одна запись покрывает несколько суток, а сетке нужна
    # клетка. Ключ — «id сотрудника|день», чтобы браузер брал клетку прямо.
    cells: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        for day in days_list:
            if item.date_from <= day <= item.date_to:
                cells.setdefault(f"{item.user_id}|{day}", []).append(item.to_dict())

    return {
        "date_from": start,
        "date_to": finish,
        "today": _today(),
        "days": days_list,
        "staff": staff,
        "cells": cells,
        "items": [item.to_dict() for item in records],
        "kinds": [
            {"id": kind, "title": ABSENCE_TITLES[kind], "present": kind in PRESENT_KINDS}
            for kind in ABSENCE_KINDS
        ],
    }


@router.get("/roster/day")
def roster_day(request: Request, date: str = "") -> Dict[str, Any]:
    """Расход на день: кто где, по видам, плюс не отмеченные.

    Это то, что начальник читает вслух на разводе, поэтому список полный:
    отдел минус все отмеченные и есть те, о ком расход молчит.
    """
    require_user(request)
    repos = _repos(request)
    day = _date_or_empty(date, "date") or _today()
    records = repos.absences.on_date(day)

    marked: Dict[int, Any] = {}
    for item in records:
        # Отметок на один день может оказаться две (правили и не убрали
        # старую). Берём ту, что заведена позже: она и есть свежая правда.
        current = marked.get(item.user_id)
        if current is None or item.id > current.id:
            marked[item.user_id] = item

    groups: Dict[str, List[Dict[str, Any]]] = {kind: [] for kind in ABSENCE_KINDS}
    for item in sorted(marked.values(), key=lambda row: row.full_name):
        groups[item.kind].append(item.to_dict())

    unmarked = [
        {"id": person.id, "full_name": person.full_name or person.login,
         "role": person.role, "role_title": person.role_title, "team": person.team}
        for person in repos.users.list_all(active_only=True)
        if person.id not in marked
    ]
    present = sum(len(groups[kind]) for kind in PRESENT_KINDS)
    return {
        "date": day,
        "groups": [
            {"id": kind, "title": ABSENCE_TITLES[kind],
             "present": kind in PRESENT_KINDS, "people": groups[kind]}
            for kind in ABSENCE_KINDS
        ],
        "unmarked": unmarked,
        "total": len(unmarked) + len(marked),
        "present": present,
        "away": len(marked) - present,
    }


@router.get("/absences")
def list_absences(request: Request, date_from: str = "", date_to: str = "") -> Dict[str, Any]:
    """Расход за период. По умолчанию — ближайший месяц от сегодня."""
    require_user(request)
    repos = _repos(request)
    start = _date_or_empty(date_from, "date_from") or _today()
    finish = _date_or_empty(date_to, "date_to") or _shift(start, 30)
    return {
        "items": [item.to_dict() for item in repos.absences.in_period(start, finish)],
        "kinds": [{"id": kind, "title": ABSENCE_TITLES[kind]} for kind in ABSENCE_KINDS],
        "date_from": start,
        "date_to": finish,
    }


@router.post("/absences")
def add_absence(request: Request) -> Dict[str, Any]:
    """Отметить себя (или подчинённого) в расходе."""
    actor = require_editor(request)
    repos = _repos(request)
    payload = _body(request)

    user_id = int(payload.get("user_id") or 0) or actor.id
    user = repos.users.get(user_id)
    if user is None or not user.active:
        raise ServiceError("сотрудник не найден", 404)
    if not _may_edit_roster(actor, user.id):
        raise ServiceError("недостаточно прав: чужой расход ведёт начальник", 403)
    kind = str(payload.get("kind", ""))
    if kind not in ABSENCE_KINDS:
        raise ServiceError(f"неизвестный вид '{kind}'", 400)
    start, finish = _roster_bounds(payload)

    clash = repos.absences.overlapping(user.id, start, finish)
    if clash:
        first = clash[0]
        raise ServiceError(
            f"на эти дни уже есть отметка «{ABSENCE_TITLES.get(first.kind, first.kind)}» "
            f"({_human_date(first.date_from)} — {_human_date(first.date_to)}): "
            "поправьте её, а не заводите вторую", 409)

    item = repos.absences.add(user.id, kind, start, finish,
                              place=str(payload.get("place", "")).strip()[:120],
                              note=str(payload.get("note", "")).strip()[:300],
                              created_by=actor.id)
    repos.audit.log("absence.add", user=actor, object_type="user", object_id=user.login,
                    details={"kind": kind, "from": start, "to": finish})
    return {"absence": item.to_dict() if item else None}


@router.patch("/absences/{absence_id}")
def update_absence(request: Request, absence_id: int) -> Dict[str, Any]:
    """Поправить свою запись расхода: планы меняются чаще, чем расход пишут."""
    actor = require_editor(request)
    repos = _repos(request)
    item = repos.absences.get(absence_id)
    if item is None:
        raise ServiceError("запись не найдена", 404)
    if not _may_edit_roster(actor, item.user_id):
        raise ServiceError("недостаточно прав: чужой расход ведёт начальник", 403)

    payload = _body(request)
    fields: Dict[str, Any] = {}
    if "kind" in payload:
        kind = str(payload["kind"])
        if kind not in ABSENCE_KINDS:
            raise ServiceError(f"неизвестный вид '{kind}'", 400)
        fields["kind"] = kind
    if "date_from" in payload or "date_to" in payload:
        merged = {"date_from": payload.get("date_from", item.date_from),
                  "date_to": payload.get("date_to", item.date_to)}
        fields["date_from"], fields["date_to"] = _roster_bounds(merged)
        clash = repos.absences.overlapping(
            item.user_id, fields["date_from"], fields["date_to"], skip_id=item.id)
        if clash:
            raise ServiceError("на эти дни у сотрудника уже есть другая отметка", 409)
    if "place" in payload:
        fields["place"] = str(payload["place"] or "").strip()[:120]
    if "note" in payload:
        fields["note"] = str(payload["note"] or "").strip()[:300]

    updated = repos.absences.update(absence_id, **fields)
    repos.audit.log("absence.update", user=actor, object_type="user",
                    object_id=str(item.user_id), details=fields)
    return {"absence": updated.to_dict() if updated else None}


@router.delete("/absences/{absence_id}")
def delete_absence(request: Request, absence_id: int) -> Dict[str, Any]:
    actor = require_editor(request)
    repos = _repos(request)
    item = repos.absences.get(absence_id)
    if item is None:
        raise ServiceError("запись не найдена", 404)
    if not _may_edit_roster(actor, item.user_id):
        raise ServiceError("недостаточно прав: чужой расход ведёт начальник", 403)
    repos.absences.delete(absence_id)
    repos.audit.log("absence.delete", user=actor, object_type="user",
                    object_id=str(item.user_id), details={"kind": item.kind})
    return {"ok": True}


# ------------------------------------------------------------- дашборд ----

def _one_per_person(records: Iterable[Any]) -> Dict[int, Any]:
    """По одной записи на человека — той, что кончается позже."""
    chosen: Dict[int, Any] = {}
    for item in records:
        current = chosen.get(item.user_id)
        if current is None or item.date_to > current.date_to:
            chosen[item.user_id] = item
    return chosen


@router.get("/board")
def board(request: Request, days: int = 30) -> Dict[str, Any]:
    """Сводка отдела: люди, нагрузка, сроки, дежурство, движение за период."""
    require_user(request)
    repos = _repos(request)
    today = _today()
    period_days = min(max(int(days or 30), 1), 365)
    since = _since_utc(period_days)

    staff = repos.board.workload(today)
    records = repos.absences.on_date(today)
    # По человеку — одна запись, та, что кончается позже. И у отсутствия, и у
    # дежурства: сотруднику отмечают больничный и следом отпуск, дежурство и
    # подмену на те же сутки. Записей две, а человек один — счётчик обязан
    # считать людей, иначе «на дежурстве: 2» при одной фамилии в списке.
    # «На месте» — дежурный и занятый работами: их можно спросить и им можно
    # дать письмо. Раньше на месте был только дежурный, и любая отметка о
    # работах превращала человека в отсутствующего.
    away = _one_per_person(item for item in records if item.kind not in PRESENT_KINDS)
    on_duty = _one_per_person(item for item in records if item.kind == "duty")
    absent = sorted(away.values(), key=lambda item: (item.full_name, item.date_to))
    duty = sorted(on_duty.values(), key=lambda item: (item.full_name, item.date_to))

    people = []
    for row in staff:
        gone = away.get(row["id"])
        people.append({
            "id": row["id"],
            "login": row["login"],
            "full_name": row["full_name"] or row["login"],
            # Отключённый сотрудник остаётся в списке, пока за ним числятся
            # письма: их надо передать живому человеку, и это должно быть видно.
            "active": bool(row["active"]),
            "role": row["role"],
            "role_title": ROLE_TITLES.get(row["role"], row["role"]),
            "department": row["department"],
            "team": row["team"],
            "open": int(row["open_count"] or 0),
            "late": int(row["late_count"] or 0),
            "soon": int(row["soon_count"] or 0),
            "done": int(row["done_count"] or 0),
            "next_deadline": row["next_deadline"] or "",
            # Чем занят прямо сейчас: отсутствие важнее дежурства.
            "away": gone.kind if gone else "",
            "away_title": ABSENCE_TITLES.get(gone.kind, "") if gone else "",
            "away_until": gone.date_to if gone else "",
            "on_duty": any(item.user_id == row["id"] for item in duty),
        })

    statuses = repos.board.status_counts()
    soon_until = _shift(today, 3)
    # Счётчики считает база: списки ниже — только то, что показываем.
    deadlines = repos.board.deadline_counts(today, soon_until)
    overdue = repos.cases.list(overdue_before=today, limit=20)
    soon = repos.cases.list(deadline_from=today, deadline_to=soon_until, limit=20)

    return {
        "today": today,
        "period_days": period_days,
        "totals": {
            "open": repos.cases.count("open"),
            "overdue": deadlines["late"],
            "soon": deadlines["soon"],
            "unassigned": repos.board.unassigned(),
            # В строю — действующие. Отключённый сотрудник попадает в список
            # только пока за ним числятся письма, и в личный состав не идёт.
            "staff": sum(1 for item in people if item["active"]),
            "away": len(away),
            "on_duty": len(duty),
        },
        "statuses": [
            {"id": key, "title": CASE_STATUS_TITLES.get(key, key), "count": value}
            for key, value in sorted(statuses.items())
        ],
        "people": people,
        "duty": [item.to_dict() for item in duty],
        "absent": [item.to_dict() for item in absent],
        "overdue": [case.to_dict() for case in overdue],
        "soon": [case.to_dict() for case in soon],
        "movement": {
            **repos.board.movement(since),
            "reports": repos.board.reports_in_period(since),
            "since": _shift(today, -period_days),
        },
    }


def _human_date(day: str) -> str:
    """Дата по-русски: 2026-09-01 → 01.09.2026. Для сообщений человеку."""
    try:
        return datetime.strptime(day, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return day


def _shift(day: str, days: int) -> str:
    try:
        base = datetime.strptime(day[:10], "%Y-%m-%d")
    except ValueError:
        return day
    return (base + timedelta(days=days)).strftime("%Y-%m-%d")


def _opt_str(payload: Dict[str, Any], name: str) -> str | None:
    """Значение поля, если оно вообще пришло. None — «не менять»."""
    return None if name not in payload else str(payload[name] or "").strip()


# --------------------------------------------------------- личный кабинет --

#: В чём приносят документы сотрудника: набранный файл, скан, снимок.
PERSON_FILE_SUFFIXES = (
    ".pdf", ".docx", ".doc", ".rtf", ".odt", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",
)


def _may_see_person_files(actor: User, user_id: int) -> bool:
    """Свои документы видит каждый; чужие — начальник, заместитель, создатель.

    Начальник группы сюда не входит намеренно, хотя он и администратор:
    объективка — личные сведения, и круг тех, кому она открыта, уже круга
    тех, кто заводит учётные записи. Тот же круг проверяет отчёты.
    """
    return actor.id == user_id or actor.role in REVIEW_ROLES


def _person_or_404(request: Request, user_id: int) -> User:
    person = _repos(request).users.get(user_id)
    if person is None:
        raise ServiceError("сотрудник не найден", 404)
    return person


@router.get("/users/{user_id}/files")
def list_person_files(request: Request, user_id: int) -> Dict[str, Any]:
    """Документы сотрудника: справка-объективка, приказы, прочее."""
    actor = require_user(request)
    person = _person_or_404(request, user_id)
    if not _may_see_person_files(actor, person.id):
        raise ServiceError(
            "недостаточно прав: документы сотрудника видят он сам, начальник "
            "отдела, заместитель и создатель системы", 403)
    items = _repos(request).person_files.list_for_user(person.id)
    return {
        "user": _user_public(person),
        "files": [item.to_dict() for item in items],
        "kinds": [{"id": kind, "title": PERSON_FILE_TITLES[kind]}
                  for kind in PERSON_FILE_KINDS],
        "can_edit": actor.id == person.id or actor.role in REVIEW_ROLES,
    }


@router.post("/users/{user_id}/files")
def add_person_file(request: Request, user_id: int,
                    file: UploadFile = File(...),
                    kind: str = Form("profile"),
                    note: str = Form("")) -> Dict[str, Any]:
    """Приложить документ к сотруднику.

    Справка-объективка одна: новая заменяет прежнюю. Приказы и прочее
    копятся — таких бумаг у человека бывает много, и все они нужны.
    """
    actor = require_user(request)
    person = _person_or_404(request, user_id)
    if not _may_see_person_files(actor, person.id):
        raise ServiceError("недостаточно прав: чужие документы не ваши", 403)
    if kind not in PERSON_FILE_KINDS:
        raise ServiceError(f"неизвестный вид документа '{kind}'", 400)

    settings = _settings(request)
    repos = _repos(request)
    name = _safe_name(Path(file.filename or "документ").name)
    if not name:
        raise ServiceError("некорректное имя файла", 400)
    suffix = Path(name).suffix.lower()
    if suffix not in PERSON_FILE_SUFFIXES:
        known = ", ".join(PERSON_FILE_SUFFIXES)
        raise ServiceError(f"такие файлы к сотруднику не прикладывают (можно: {known})", 400)

    settings.ensure_dirs()
    target_dir = Path(settings.data_dir) / "person-files" / str(person.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{secrets.token_hex(6)}-{name}"

    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    try:
        with target.open("wb") as stream:
            while True:
                piece = file.file.read(1024 * 1024)
                if not piece:
                    break
                size += len(piece)
                if size > limit:
                    raise ServiceError(
                        f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
                stream.write(piece)
        if not size:
            raise ServiceError("файл пустой", 400)
        item = repos.person_files.add(
            person.id, name=name, path=str(target), size=size, kind=kind,
            note=str(note or "").strip()[:300],
            uploaded_by=actor.id if actor else None)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    # Справка-объективка одна: новая заменяет прежнюю. Иначе список копит
    # редакции, и начальник не знает, какая из них действующая. Приказы и
    # прочее копятся намеренно.
    if kind in PERSON_FILE_SINGLE:
        for old_item in repos.person_files.list_for_user(person.id):
            if old_item.kind != kind or old_item.id == item.id:
                continue
            path = repos.person_files.delete(old_item.id)
            if path:
                Path(path).unlink(missing_ok=True)

    # В журнал — только факт, имя файла и вид: содержимое документа туда не
    # попадает, журнал читают все администраторы.
    repos.audit.log("person.file.add", user=actor, object_type="user",
                    object_id=person.login, details={"name": name, "kind": kind})
    return {"file": item.to_dict()}


@router.get("/users/{user_id}/files/{file_id}")
def download_person_file(request: Request, user_id: int, file_id: int) -> FileResponse:
    actor = require_user(request)
    person = _person_or_404(request, user_id)
    if not _may_see_person_files(actor, person.id):
        raise ServiceError("недостаточно прав: чужие документы не ваши", 403)
    item = _repos(request).person_files.get(file_id)
    if item is None or item.user_id != person.id:
        raise ServiceError("файл не найден", 404)
    path = Path(item.path)
    if not path.is_file():
        raise ServiceError("файл не найден на диске", 404)
    return FileResponse(path, filename=item.name,
                        headers={"Content-Disposition": _disposition(item.name)})


@router.delete("/users/{user_id}/files/{file_id}")
def delete_person_file(request: Request, user_id: int, file_id: int) -> Dict[str, Any]:
    actor = require_user(request)
    person = _person_or_404(request, user_id)
    if not _may_see_person_files(actor, person.id):
        raise ServiceError("недостаточно прав: чужие документы не ваши", 403)
    repos = _repos(request)
    item = repos.person_files.get(file_id)
    if item is None or item.user_id != person.id:
        raise ServiceError("файл не найден", 404)
    path = repos.person_files.delete(file_id)
    if path:
        Path(path).unlink(missing_ok=True)
    repos.audit.log("person.file.delete", user=actor, object_type="user",
                    object_id=person.login, details={"name": item.name})
    return {"ok": True}


@router.patch("/me/contacts")
def update_my_contacts(request: Request) -> Dict[str, Any]:
    """Свои контакты человек правит сам.

    Справочник, который ведёт кадровик, устаревает быстрее, чем его правят;
    свой внутренний номер человек поправит в ту же минуту, когда переедет.
    """
    user = require_user(request)
    payload = _body(request)
    fields: Dict[str, Any] = {}
    for name in ("phone", "ext_no", "room", "email"):
        if name in payload:
            fields[name] = str(payload[name] or "").strip()[:120]
    if not fields:
        return {"user": _user_public(user)}
    updated = _repos(request).users.update(user.id, **fields)
    _repos(request).audit.log("user.contacts", user=user, object_type="user",
                              object_id=user.login, details={"fields": sorted(fields)})
    return {"user": _user_public(updated)}


@router.get("/me/summary")
def my_summary(request: Request) -> Dict[str, Any]:
    user = require_user(request)
    repos = _repos(request)
    today = _today()
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
        # Чем сотрудник отчитывается за последний шаг: сколько ответов он
        # отправил. «Проверено» — работа начальника, «отправлено» — его.
        "sent": int(repos.db.scalar(
            "SELECT count(*) FROM cases WHERE sent_by = ? AND outgoing_no <> ''",
            (user.id,)) or 0),
        "edits": {
            "pairs": int(edits["pairs"] or 0),
            "mean_distance": round(float(edits["mean"] or 0.0), 3),
        },
        "chats": repos.chats.count_for_user(user.id),
        # Что у человека на руках прямо сейчас. Кабинет должен отвечать не
        # только «сколько я сделал», но и «что за мной числится»: за вторым
        # приходят чаще.
        "my_cases": [item.to_dict() for item in repos.cases.list(
            status="open", assignee_id=user.id, limit=20)],
        "my_cases_total": repos.cases.count(status="open", assignee_id=user.id),
        "overdue": repos.cases.count(status="open", assignee_id=user.id,
                                     overdue_before=today),
        # Свой расход на ближайшие две недели: чаще всего человек заходит
        # сюда именно свериться, где он завтра.
        "roster": [item.to_dict() for item in repos.absences.for_user_period(
            user.id, today, _shift(today, 14))],
        "files": len(repos.person_files.list_for_user(user.id)),
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
    """Жив ли сервис. Отвечает и без входа — этим пользуются скрипты запуска.

    Без входа отдаём только признак жизни. Сколько в отделе сотрудников,
    писем и отчётов — сведения о работе организации, и посторонним в них
    делать нечего; модель и её адрес — тем более.
    """
    repos = _repos(request)
    settings = _settings(request)
    try:
        counts = repos.db.counts()
        database = "ok"
    except Exception as error:  # noqa: BLE001
        counts, database = {}, f"ошибка: {error}"
    body = {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "auth_enabled": settings.auth_enabled,
    }
    if get_user(request) is not None or not settings.auth_enabled:
        body["counts"] = counts
        body["llm"] = {"kind": settings.llm_kind, "model": settings.llm_model}
    return body


# ------------------------------------------------------------- служебное ---

# Пробел разрешён, а вот \s пропускал бы перевод строки — и тогда case_id
# с переводом строки уезжал бы прямо в заголовок HTTP-ответа.
_UNSAFE = re.compile(r"[^\w .()\-]", re.UNICODE)


def _line_or_empty(value: Any) -> str:
    """Линия связи: один из известных видов либо пусто.

    Пустое значение разрешено намеренно: письмо иногда спускают раньше, чем
    становится ясно, к какой линии оно относится, и запирать регистрацию
    из-за этого нельзя.
    """
    line = str(value or "").strip()
    if not line:
        return ""
    if line not in LINE_TYPES:
        known = ", ".join(LINE_TITLES[key] for key in LINE_TYPES)
        raise ServiceError(f"неизвестная линия связи '{line}' (известны: {known})", 400)
    return line


def _safe_name(name: str) -> str:
    """Имя файла без путей и управляющих символов, пригодное для ФС и заголовков.

    Разделители пути заменяются, а не отсекаются вместе с началом имени.
    Учётный номер вида «ВХ-2026/0423» — обычное делопроизводство, и от него
    оставалось «0423»: два письма разных лет выгружались в один и тот же
    файл и затирали друг друга в каталоге выгрузок.
    """
    name = unicodedata.normalize("NFC", name).replace("\\", "/").replace("/", "-")
    name = "".join(ch for ch in name if ch.isprintable())
    name = _UNSAFE.sub("_", name).strip().strip(".").strip()
    # Пустое имя после чистки — тоже имя файла: без запасного значения
    # выгрузка ушла бы в файл вида «-v1.docx» или вовсе в каталог.
    return name[:120] or "документ"


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
                 domain: str | None = None) -> Dict[str, Any]:
    try:
        from ..ingest.pipeline import ingest_path  # noqa: PLC0415
    except ImportError as error:
        raise ServiceError("модуль приёма документов недоступен", 501) from error
    settings = _settings(request)
    result = ingest_path(
        _repos(request), path,
        root=Path(settings.library_dir), doc_type=doc_type,
        force=True, domain=domain,
        domains_path=settings.domains_path,
    )
    return _ingest_to_dict(result)


def _ingest_to_dict(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    keys = ("added", "updated", "skipped", "failed", "chunks", "documents",
            "warnings", "failures", "notes")
    return {key: getattr(result, key, None) for key in keys if hasattr(result, key)}


def json_error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})
