"""REST API. Обработчики тонкие: разбор запроса и вызов сервисного слоя."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import unicodedata
import urllib.parse
from collections import Counter
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..corpus import DOC_TYPES
from ..domains import registry as domain_registry
from ..packages import pip_hint
from ..rerank import build_reranker
from ..store.models import (
    ABSENCE_KINDS,
    ABSENCE_TITLES,
    ADMIN_ROLES,
    CASE_PRIORITIES,
    CASE_STATUS_TITLES,
    CASE_STATUSES,
    DEPARTMENT_DAY_KINDS,
    DEPARTMENT_DAY_TITLES,
    DOC_STATUS_TITLES,
    DOC_STATUSES,
    FILE_STAGE_TITLES,
    FILE_STAGES,
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
    ROLES,
    Case,
    Report,
    User,
    role_title_of,
    short_name,
)
from .auth import (
    COOKIE_NAME,
    get_user,
    require_admin,
    require_anyone,
    require_editor,
    require_owner,
    require_reviewer,
    require_user,
)
from .pages import PageRenderError, is_renderable, page_count, render_page
from .service import CARD_LIMITS, ServiceError

log = logging.getLogger(__name__)

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
    moment = datetime.now(UTC) - timedelta(days=days)
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


def _body(request: Request) -> dict[str, Any]:
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


def _report_payload(service, report: Report, *, with_markdown: bool = True) -> dict[str, Any]:
    data = report.to_dict(with_markdown=with_markdown)
    data["sources"] = service.sources(report)
    data["facts_stale"] = service.facts_are_stale(report)
    return data


# ------------------------------------------------------------------ вход ---

@router.post("/auth/login")
def login(request: Request, response: Response) -> dict[str, Any]:
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
    # Заявка подана, но её ещё не признали. Говорим прямо: «неверный пароль»
    # человеку, который ввёл верный, — это час поисков несуществующей ошибки.
    # Отдел изолирован, и скрывать от своего военнослужащего, что он в очереди,
    # незачем.
    if not user.approved:
        throttle.success(key)
        raise ServiceError(
            "заявка на доступ ещё не одобрена — обратитесь к начальнику отдела "
            "или его заместителю", 403)

    throttle.success(key)
    # Гостю обещано, что переписка не сохраняется. Стираем её и на входе:
    # уйти можно и не нажимая «выйти» — просто закрыв окно, — и тогда
    # прошлый разговор ждал бы следующего гостя.
    if user.is_guest:
        repos.chats.forget_user(user.id)
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


@router.post("/auth/register")
def register(request: Request) -> dict[str, Any]:
    """Заявка на доступ. Заводит себя человек сам, одобряет начальство.

    Заводить каждого руками — работа, которую в отделе делать некому, а
    открытая регистрация без одобрения превращает систему в проходной двор.
    Поэтому заявку подаёт сам человек, а доступ ей открывает создатель
    системы, начальник отдела, его заместитель или начальник группы.

    Должность заявке не выбирают: её назначает тот, кто одобряет. Иначе
    любой пришедший записал бы себя начальником отдела.
    """
    settings = _settings(request)
    if not settings.auth_enabled:
        raise ServiceError("аутентификация отключена настройками", 400)
    # Вход этот путь не требует — значит, его можно дёргать в цикле. Тем же
    # ограничителем, что и вход: десяток заявок подряд с одного места — это
    # не отдел устраивается на работу.
    throttle = request.app.state.throttle
    key = "register@" + (request.client.host if request.client else "?")
    throttle.check(key)
    repos = _repos(request)
    payload = _body(request)

    login_name = str(payload.get("login", "")).strip().lower()
    full_name = _check_full_name(payload.get("full_name", ""))
    password = str(payload.get("password", ""))
    _check_login(login_name)
    if len(password) < 8:
        raise ServiceError("пароль короче 8 символов", 400)
    if repos.users.by_login(login_name) is not None:
        raise ServiceError("такой логин уже занят — выберите другой", 409)

    # Заявке даём самую младшую должность: настоящую назначит тот, кто
    # одобряет, и до одобрения она всё равно ничего не значит.
    user = repos.users.create(login_name, password, full_name, "engineer",
                              approved=False)
    # Заявка засчитывается ограничителю как попытка: подать их без счёта
    # нельзя, иначе очередь на одобрение забивается за минуту.
    throttle.failure(key)
    repos.audit.log("user.request", object_type="user", object_id=user.login,
                    details={"full_name": full_name})
    return {"ok": True, "login": user.login}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(COOKIE_NAME)
    уходит = get_user(request)
    repos = _repos(request)
    if уходит is not None and уходит.is_guest:
        repos.chats.forget_user(уходит.id)
    if token:
        repos.sessions.delete(token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict[str, Any]:
    user = get_user(request)
    return {
        "user": user.to_dict() if user else None,
        "auth_enabled": _settings(request).auth_enabled,
    }


@router.get("/config")
def config(request: Request) -> dict[str, Any]:
    """Справочники интерфейса: шаблоны отчётов, типы документов, направления.

    Требует входа. Окно входа этих сведений не запрашивает — оформление оно
    берёт отдельным маршрутом, — а состав шаблонов, перечень направлений
    работы и адрес модели постороннему знать незачем.
    """
    require_anyone(request)
    settings = _settings(request)
    service = _service(request)
    outlines = []
    for outline in service.outlines.all().values():
        outlines.append({
            "report_type": outline.report_type,
            "title": outline.title,
            "short_title": outline.short_title or outline.title,
            "version": outline.version,
            # Названия значений по-русски: без них экран показывает ключи
            # вида packet_count, и человек не знает, что туда заносить.
            "fact_titles": dict(outline.fact_titles),
            "fact_units": dict(outline.fact_units),
            "sections": [
                {
                    "id": spec.id,
                    "title": spec.title,
                    "required_facts": list(spec.required_facts),
                    "optional_facts": list(spec.optional_facts),
                    "target_words": spec.target_words,
                    # Раздел, который повторяется по описи: без этих трёх
                    # полей экран данных не знает ни что заводить списком,
                    # ни каких полей ждать от каждой записи.
                    "repeat_over": spec.repeat_over,
                    "item_required": list(spec.item_required),
                    "item_title": spec.item_title,
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
        },
        "search": {
            "dense": settings.embed_enabled,
            "rerank": settings.rerank_enabled,
        },
        "domains": _domains(request).to_dict(),
    }


# ----------------------------------------------------------------- кейсы ---

@router.get("/cases")
def list_cases(request: Request, status: str | None = None,
               limit: int = 100, offset: int = 0, assignee: int | None = None,
               overdue: bool = False, q: str = "") -> dict[str, Any]:
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
def update_case_card(request: Request, case_ref: int) -> dict[str, Any]:
    """Карточка письма: исполнитель, срок, входящий номер, приоритет, статус."""
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    repos = _repos(request)
    payload = _body(request)

    fields: dict[str, Any] = {}
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
    for name in ("incoming_date", "deadline", "tc_date", "order_date"):
        if name in payload:
            fields[name] = _date_or_empty(payload[name], name)
    if "registrations" in payload:
        fields["registrations"] = _count_or_zero(payload["registrations"])
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
    # Письмо назначили на человека — он должен об этом узнать, а не найти
    # его случайно в своём списке.
    fresh = fields.get("assignee_id")
    if fresh and fresh != case.assignee_id:
        _notify(request, fresh, "case.assigned",
                f"На вас письмо {case.incoming_no or case.case_id}",
                (case.title or "")[:200],
                link=f"#/case/{case.id}", from_id=user.id)
    return {"case": updated.to_dict() if updated else None}


@router.post("/cases")
def create_case(request: Request) -> dict[str, Any]:
    """Регистрация входящего письма."""
    user = require_editor(request)
    service = _service(request)
    payload = _body(request)
    # Даты и приоритет проверяем здесь: сервисному слою достаётся уже
    # проверенное, а инженер видит понятную ошибку вместо отказа базы.
    for field in ("incoming_date", "deadline", "tc_date", "order_date"):
        if field in payload:
            payload[field] = _date_or_empty(payload[field], field)
    payload["registrations"] = _count_or_zero(payload.get("registrations"))
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
#: Сколько разбора показывать в окне просмотра. Длинный текст целиком экрану
#: не нужен: смотрят начало, чтобы понять, прочиталось ли вообще.
ATTACHMENT_TEXT_LIMIT = 20000

#: Что к письму прикладывают чаще всего. Это подсказка окну выбора файла, а
#: НЕ запрет: библиотека отдела и переписка полны форматов, которых заранее не
#: перечислить, — прошивки, выгрузки приборов, схемы в чужих САПР. Файл, чей
#: текст система прочитать не может, просто хранится и скачивается: об этом в
#: карточке сказано прямо. Показывать чужой файл прямо в странице по-прежнему
#: разрешено только известным типам (INLINE_TYPES) — там список остаётся
#: запретом, и это другое: показать значит исполнить.
CASE_FILE_SUFFIXES = (
    ".pdf", ".docx", ".doc", ".rtf", ".odt", ".xlsx", ".xls", ".csv",
    ".md", ".txt", ".log", ".json", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".zip", ".7z", ".rar",
)


@router.get("/cases/{case_ref}/notes")
def list_case_notes(request: Request, case_ref: int) -> dict[str, Any]:
    """Примечания к письму: обсуждение прямо на деле."""
    require_user(request)
    case = _case_or_404(request, case_ref)
    items = _repos(request).case_notes.list_for_case(case.id)
    return {"notes": [item.to_dict() for item in items]}


@router.post("/cases/{case_ref}/notes")
def add_case_note(request: Request, case_ref: int) -> dict[str, Any]:
    """Оставить примечание к письму.

    Начальник пишет, что поправить, исполнитель отвечает — и всё это
    остаётся при письме, а не теряется в разговорах у стола. Тот, кого
    примечание касается (исполнитель письма и его автор), узнаёт о нём
    уведомлением: письмо он в этот момент может не держать открытым.
    """
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    repos = _repos(request)
    text = str(_body(request).get("text", "")).strip()
    if not text:
        raise ServiceError("примечание пустое", 400)
    if len(text) > 4000:
        raise ServiceError("примечание длиннее 4000 знаков", 400)

    note = repos.case_notes.add(case.id, user.id, text)
    who = case.incoming_no or case.case_id
    for target in {case.assignee_id, case.created_by}:
        _notify(request, target, "case.note",
                f"Примечание к письму {who}",
                text[:200], link=f"#/case/{case.id}", from_id=user.id)
    repos.audit.log("case.note", user=user, object_type="case",
                    object_id=case.case_id)
    return {"note": note.to_dict()}


@router.delete("/cases/{case_ref}/notes/{note_id}")
def delete_case_note(request: Request, case_ref: int, note_id: int) -> dict[str, Any]:
    """Убрать своё примечание. Чужое — только начальству."""
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    repos = _repos(request)
    note = repos.case_notes.get(note_id)
    if note is None or note.case_ref != case.id:
        raise ServiceError("примечание не найдено", 404)
    if note.user_id != user.id and not user.is_admin:
        raise ServiceError("недостаточно прав: чужое примечание убирает начальник", 403)
    repos.case_notes.delete(note_id)
    return {"ok": True}


@router.get("/cases/{case_ref}/files")
def list_case_files(request: Request, case_ref: int, stage: str = "") -> dict[str, Any]:
    """Бумаги письма. Смотреть может любой военнослужащий.

    stage отбирает стопку: incoming — пришли с письмом, outgoing — ушли с
    ответом. Пусто — обе, и тогда сперва идут те, что к ответу.
    """
    require_user(request)
    case = _case_or_404(request, case_ref)
    if stage and stage not in FILE_STAGES:
        raise ServiceError(f"неизвестный вид приложения '{stage}'", 400)
    items = _repos(request).case_files.list_for_case(case.id, stage=stage)
    return {
        "files": [with_pages(item) for item in items],
        "stages": [{"id": key, "title": FILE_STAGE_TITLES[key]} for key in FILE_STAGES],
    }


@router.post("/cases/{case_ref}/files")
def attach_to_case(request: Request, case_ref: int,
                   file: UploadFile = File(...),
                   note: str = Form(""),
                   stage: str = Form("incoming")) -> dict[str, Any]:
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
    if stage not in FILE_STAGES:
        raise ServiceError(f"неизвестный вид приложения '{stage}'", 400)

    name = _safe_name(Path(file.filename or "файл").name)
    if not name:
        raise ServiceError("некорректное имя файла", 400)
    _refuse_dangerous(name)

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
        text, _problem = _extract_attachment(target, name)
        item = repos.case_files.add(
            case.id, name=name, path=str(target), size=size,
            text=text.strip(), note=str(note or "").strip()[:300],
            user_id=user.id if user else None, stage=stage)
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    repos.audit.log("case.attach", user=user, object_type="case",
                    object_id=case.case_id, details={"name": name, "bytes": size})
    return {"file": with_pages(item)}


@router.get("/cases/{case_ref}/files/{file_id}")
def download_case_file(request: Request, case_ref: int, file_id: int,
                       inline: int = 0, page: int = 0):
    """Отдать приложенную бумагу подлинником.

    inline=1 — для просмотра прямо на экране: скан письма хочется увидеть, не
    скачивая его в «Загрузки» и не открывая сторонней программой. Показывать
    так можно только то, что браузер рисует сам и без опаски: картинки, PDF и
    простой текст. Всё прочее отдаётся вложением, как и раньше.
    """
    require_user(request)
    case = _case_or_404(request, case_ref)
    item = _repos(request).case_files.get(file_id)
    if item is None or item.case_ref != case.id:
        raise ServiceError("файл не найден", 404)
    path = Path(item.path)
    if not path.is_file():
        raise ServiceError("файл не найден на диске", 404)
    return _preview_reply(request, path, item.name, inline=inline, page=page)


@router.get("/cases/{case_ref}/files/{file_id}/text")
def case_file_text(request: Request, case_ref: int, file_id: int) -> dict[str, Any]:
    """Что система вычитала из приложенного файла.

    Показывается рядом с самим файлом — чтобы человек видел, что попало в
    поиск, и не рассчитывал на распознанное там, где его нет. Со сканов
    текст берётся машинным распознаванием, и доверять его числам нельзя:
    «3,5» и «8,5» на плохом снимке различаются одним штрихом.
    """
    require_user(request)
    case = _case_or_404(request, case_ref)
    item = _repos(request).case_files.get(file_id)
    if item is None or item.case_ref != case.id:
        raise ServiceError("файл не найден", 404)
    text = item.text.strip()
    return {
        "name": item.name,
        "text": text[:ATTACHMENT_TEXT_LIMIT],
        "truncated": len(text) > ATTACHMENT_TEXT_LIMIT,
        "recognised": Path(item.name).suffix.lower() in OCR_SUFFIXES,
    }


@router.delete("/cases/{case_ref}/files/{file_id}")
def detach_from_case(request: Request, case_ref: int, file_id: int) -> dict[str, Any]:
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
def get_case(request: Request, case_ref: int) -> dict[str, Any]:
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
def update_facts(request: Request, case_ref: int) -> dict[str, Any]:
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
def reindex_cases(request: Request) -> dict[str, Any]:
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
def send_case(request: Request, case_ref: int) -> dict[str, Any]:
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
        outgoing_note=str(payload.get("outgoing_note", "")),
    )
    return {"case": updated.to_dict()}


@router.post("/cases/{case_ref}/unsend")
def unsend_case(request: Request, case_ref: int) -> dict[str, Any]:
    """Отозвать отправку: номер вписали не тот или ответ ушёл не тому.

    Право проверяющего: запись об отправке — учётная, и снимать её должен
    тот, кто отвечает за проверку, а не любой военнослужащий.
    """
    user = require_reviewer(request)
    case = _case_or_404(request, case_ref)
    updated = _service(request).withdraw_sending(case, user)
    return {"case": updated.to_dict()}


@router.delete("/cases/{case_ref}")
def delete_case(request: Request, case_ref: int) -> dict[str, Any]:
    """Убрать письмо.

    Ошибиться при регистрации может каждый — не тот номер, не то письмо, — и
    ходить за начальником из-за собственной описки человек не должен. Поэтому
    своё письмо убирает тот, кто его завёл, но лишь пока по нему ничего не
    сделано: нет ни одного отчёта и ответ не отправлен. Как только за письмо
    взялись, оно перестаёт быть личной ошибкой и становится работой отдела —
    удалить его может только администратор.
    """
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    repos = _repos(request)
    if not user.is_admin:
        if case.created_by != user.id:
            raise ServiceError(
                "недостаточно прав: чужое письмо убирает администратор", 403)
        if repos.reports.list_for_case(case.id):
            raise ServiceError(
                "по письму уже есть отчёт — удалить его может только "
                "администратор", 409)
        if case.outgoing_no:
            raise ServiceError(
                "по письму отправлен ответ — такое письмо не удаляют", 409)
    # Сданные файлом отчёты лежат на диске: строки из базы уходят каскадом,
    # а файлы остались бы навсегда. Интерфейс обещает удаление вместе со
    # всеми редакциями отчёта — значит, и с их файлами.
    # Приложенные к письму бумаги лежат там же на диске: удаление письма
    # обещано вместе со всем, что к нему относится.
    data_dir = Path(_settings(request).data_dir)
    folders = [data_dir / "reports" / str(case.id),
               data_dir / "case-files" / str(case.id)]
    repos.cases.delete(case.id)
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
    repos.audit.log("case.delete", user=user, object_type="case",
                    object_id=case.case_id, details={"files": removed})
    return {"ok": True}


@router.post("/cases/{case_ref}/generate")
def generate(request: Request, case_ref: int) -> dict[str, Any]:
    user = require_editor(request)
    case = _case_or_404(request, case_ref)
    service = _service(request)
    payload = getattr(request.state, "json_body", None) or {}
    top_k = payload.get("top_k")
    report = service.generate(case, user, top_k=int(top_k) if top_k else None)
    return {"report": _report_payload(service, report)}


@router.get("/cases/{case_ref}/report")
def latest_report(request: Request, case_ref: int) -> dict[str, Any]:
    require_user(request)
    case = _case_or_404(request, case_ref)
    report = _repos(request).reports.latest_for_case(case.id)
    if report is None:
        raise ServiceError("по этому письму отчёт ещё не готовили", 404)
    return {"report": _report_payload(_service(request), report)}


# --------------------------------------------------------------- отчёты ---

@router.get("/reports/{report_id}")
def get_report(request: Request, report_id: int) -> dict[str, Any]:
    require_user(request)
    report = _report_or_404(request, report_id)
    return {"report": _report_payload(_service(request), report)}


@router.post("/reports/{report_id}/verify")
def verify_report_endpoint(request: Request, report_id: int) -> dict[str, Any]:
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
def regenerate_section(request: Request, report_id: int, section_id: str) -> dict[str, Any]:
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
def save_section(request: Request, report_id: int, section_id: str) -> dict[str, Any]:
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
def restore_section(request: Request, report_id: int, section_id: str) -> dict[str, Any]:
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    section = service.restore_section(report, section_id, user)
    updated = _report_or_404(request, report_id)
    return {"section": section.to_dict(), "report": _report_payload(service, updated)}


#: Форматы готового отчёта, который сдают на проверку. Word и PDF — то, в чём
#: отчёты пишут; Markdown и текст — то, во что их выгружает сама система.
#: Форматы, из которых система вычитывает текст отчёта. Список справочный:
#: отчёт примут в любом виде, но по нечитаемому файлу проверить числа нельзя,
#: и об этом говорится в карточке.
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
    submit: str = Form("1"),
) -> dict[str, Any]:
    """Сдать готовый отчёт файлом на проверку начальнику.

    Загружать может любой военнослужащий — свои отчёты в отдел сдают все.
    Исполнителем по умолчанию становится тот, кто загрузил: чаще всего он
    же его и писал. Письмо под отчёт заводится тем же действием, чтобы не
    заставлять человека делать два дела вместо одного.

    Числа такого отчёта не сверяются с факт-пакетом: его нет и быть не
    может — документ написан человеком целиком. Об этом сказано и в
    карточке, и в списке писем, чтобы проверенный файл не путали с
    отчётом, прошедшим машинную проверку.

    submit решает, уходит ли отчёт начальнику тем же движением. Из списка
    писем — да: человек нажал «Сдать готовый отчёт», он за тем и пришёл. С
    открытого письма — нет: там отчёт сперва смотрят и отправляют отдельной
    кнопкой, иначе ошибочный файл оказывается на столе у начальника раньше,
    чем его успели заметить.
    """
    user = require_editor(request)
    repos = _repos(request)
    settings = _settings(request)
    service = _service(request)

    name = _safe_name(Path(file.filename or "отчёт").name)
    suffix = Path(name).suffix.lower()

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
        fields: dict[str, Any] = {}
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
                     f"({case.assignee_name or 'другой военнослужащий'})")
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
        text, problem = _extract_attachment(target, поток=False)
        to_review = str(submit or "").strip().lower() not in ("0", "false", "no", "")
        try:
            report = repos.reports.create_uploaded(
                case.id, markdown=text.strip(), file_name=name, file_path=str(target),
                file_size=size, user_id=user.id if user else None,
                status="review" if to_review else "draft")
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
    # Письмо переходит «на проверку» только вместе с отчётом. Загруженный
    # черновик оставляет его «в работе»: на столе у начальника он ещё не был.
    repos.cases.set_status(case.id, "review" if to_review else "draft")
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
def submit(request: Request, report_id: int) -> dict[str, Any]:
    """Отправить отчёт на проверку начальнику. Может любой военнослужащий."""
    user = require_editor(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    result = service.submit(report, user)
    # Проверяющим — знать, что на столе появилась работа. Иначе отчёт лежит,
    # пока исполнитель не сходит и не скажет вслух.
    case = _repos(request).cases.get(report.case_ref)
    who = (case.incoming_no or case.case_id) if case else str(report.case_ref)
    for boss in _repos(request).users.list_all(active_only=True):
        if boss.can_review:
            _notify(request, boss.id, "report.review",
                    f"Отчёт на проверку по письму {who}",
                    f"Сдал {short_name(user.full_name) or user.login}.",
                    link=f"#/case/{report.case_ref}", from_id=user.id)
    return {"report": _report_payload(service, result)}


@router.post("/reports/{report_id}/approve")
def approve(request: Request, report_id: int) -> dict[str, Any]:
    """Отметить отчёт проверенным. Только начальник отдела или заместитель."""
    user = require_reviewer(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    approved = service.approve(report, user)
    case = _repos(request).cases.get(report.case_ref)
    who = (case.incoming_no or case.case_id) if case else str(report.case_ref)
    for target in {report.created_by, case.assignee_id if case else None}:
        _notify(request, target, "report.approved",
                f"Отчёт проверен по письму {who}",
                "Можно отправлять ответ и записывать исходящий номер.",
                link=f"#/case/{report.case_ref}", from_id=user.id)
    return {"report": _report_payload(service, approved)}


@router.post("/reports/{report_id}/rework")
def send_back(request: Request, report_id: int) -> dict[str, Any]:
    """Вернуть отчёт исполнителю с замечанием. Только проверяющий."""
    user = require_reviewer(request)
    report = _report_or_404(request, report_id)
    service = _service(request)
    note = str(_body(request).get("note", ""))
    result = service.send_back(report, note, user)
    # Возврат с замечанием — то, ради чего человека отрывают от работы:
    # пока он не узнает, письмо стоит. Уведомление громкое, со звуком.
    case = _repos(request).cases.get(report.case_ref)
    who = (case.incoming_no or case.case_id) if case else str(report.case_ref)
    for target in {report.created_by, case.assignee_id if case else None}:
        _notify(request, target, "report.rework",
                f"Отчёт возвращён на исправление: {who}",
                note.strip()[:300] or "Замечание см. в карточке письма.",
                link=f"#/case/{report.case_ref}", from_id=user.id)
    return {"report": _report_payload(service, result)}


@router.get("/reports/{report_id}/sources")
def report_sources(request: Request, report_id: int) -> dict[str, Any]:
    require_user(request)
    report = _report_or_404(request, report_id)
    return {"items": _service(request).sources(report)}


@router.get("/reports/{report_id}/file")
def download_report_file(request: Request, report_id: int) -> FileResponse:
    """Отдать загруженный отчёт тем же файлом, каким его сдали.

    Смотреть отчёты может любой военнослужащий: система для того и заведена,
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
            "экспорт в DOCX недоступен: не установлен python-docx. Отчёт "
            "можно забрать в виде Markdown. Чтобы вернуть выгрузку в DOCX, "
            "администратору нужно выполнить на сервере: "
            + pip_hint("python-docx"), 501,
        ) from error

    settings.ensure_dirs()
    target = Path(settings.export_dir) / f"{_safe_name(case.case_id)}-v{report.version}.docx"
    try:
        export_report(
            report.markdown, target,
            case_id=case.case_id, incoming_no=case.incoming_no,
            outgoing_no=case.outgoing_no, status=report.status,
            template=settings.docx_template,
            note=getattr(settings, "report_footer", ""),
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

#: Сколько документов показываем за раз. Библиотека отдела — тринадцать с
#: половиной тысяч, и одной таблицей это не читается и не рисуется.
LIBRARY_PAGE = 50

#: Больше этого за один запрос не отдаём даже по просьбе.
LIBRARY_PAGE_MAX = 200


@router.get("/library")
def library(request: Request, doc_type: str | None = None,
            domain: str | None = None, status: str | None = None,
            q: str = "", quality: str = "", page: int = 1,
            per_page: int = LIBRARY_PAGE) -> dict[str, Any]:
    """Страница библиотеки. Раньше отдавалась целиком — все документы разом.

    На корпусе отдела это несколько мегабайт JSON на каждое открытие
    раздела: сервер их собирал, браузер разбирал и рисовал таблицу в
    тринадцать тысяч строк. Человеку в этот миг нужны три документа,
    которые он ищет по названию.
    """
    require_user(request)
    repos = _repos(request)
    size = max(1, min(int(per_page or LIBRARY_PAGE), LIBRARY_PAGE_MAX))
    number = max(1, int(page or 1))
    query = str(q or "").strip()[:200]
    # «Плохо разобранные» — такой же фильтр, как тип или направление: без
    # него найти их среди тринадцати тысяч можно только глазами.
    bad = str(quality or "").strip().lower() or None
    total = repos.documents.count_all(doc_type, domain, status, query, bad)
    pages = max(1, (total + size - 1) // size)
    number = min(number, pages)
    documents = repos.documents.list(doc_type, domain, status, query, bad,
                                     limit=size, offset=(number - 1) * size)
    return {
        "items": [document.to_dict() for document in documents],
        "stats": repos.documents.stats(),
        "domains": repos.documents.domains(),
        "statuses": repos.documents.statuses(),
        "chunks": repos.chunks.count(),
        "embeddings": repos.vectors.count(),
        # Сколько всего нашлось и какую часть показали: без этих чисел
        # человек не знает, всё ли перед ним.
        "total": total,
        "page": number,
        "pages": pages,
        "per_page": size,
        "query": query,
        "quality": bad or "",
    }


@router.get("/library/{doc_id:path}/text")
def document_text(request: Request, doc_id: str) -> dict[str, Any]:
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
    # Книга в библиотеке отдела бывает и в полторы тысячи фрагментов.
    # Показываем страницами и ЧЕСТНО говорим, сколько их всего: молча
    # обрезанный на четырёхстах список читается как весь документ.
    total = repos.chunks.count_for_document(document.id)
    offset = max(0, int(request.query_params.get("offset", 0) or 0))
    limit = max(1, min(int(request.query_params.get("limit", 400) or 400), 400))
    chunks = repos.chunks.for_document(document.id, limit=limit, offset=offset)
    source = Path(document.source_path)
    return {
        "document": document.to_dict(),
        "source_exists": source.is_file(),
        "source_name": source.name,
        # Сколько страниц у подлинника: по этому числу окно документа
        # показывает его страницами, как справку в личном кабинете.
        "source_pages": page_count(source) if source.is_file() else 0,
        "chunks_total": total,
        "chunks_offset": offset,
        "chunks_limit": limit,
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
def document_file(request: Request, doc_id: str, page: int = 0):
    """Отдать исходный файл — тот самый, что лежит в библиотеке на диске.

    ``page=N`` отдаёт одну страницу картинкой: так подлинник смотрят, не
    скачивая. Кнопка «Открыть исходный файл» открывала его вложением, то есть
    на деле скачивала — и посмотреть страницу стандарта было нечем.
    """
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

    return _preview_reply(request, resolved, resolved.name, page=page)


@router.post("/library/upload")
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: str = Form("literature"),
    domain: str = Form(""),
) -> dict[str, Any]:
    # Пополнение библиотеки — за начальством. Документ ложится в общий поиск
    # всего отдела: неверный тип или направление портят выдачу всем, а
    # разложить обратно можно только руками.
    user = require_admin(request)
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
    service = _service(request)
    service.reset_retriever()
    # Векторы новых фрагментов строим сразу, фоном. Раньше их строила одна
    # команда из консоли, к которой на изолированной машине никто не
    # подходит: книга ложилась в библиотеку и оставалась невидимой для
    # смыслового поиска — молча, без единого признака.
    vectors = service.vectors.start_if_needed() if service.vectors else {}

    document = None
    for doc_id in (result.get("documents") or []):
        found = repos.documents.by_doc_id(doc_id)
        if found is not None:
            document = found.to_dict()
    return {"result": result, "document": document, "vectors": vectors}


@router.post("/library/reindex")
def reindex(request: Request) -> dict[str, Any]:
    user = require_admin(request)
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
    service = _service(request)
    service.reset_retriever()
    # Достраиваем только недостающее. Переиндексация документа сама сносит
    # его векторы вместе со старыми фрагментами (ChunkRepo.replace_for_document),
    # поэтому «недостающее» после неё — ровно то, что надо построить заново.
    # Полная перестройка нужна лишь при смене модели и делается отдельной
    # командой: на большой библиотеке это часы работы видеокарты.
    vectors = service.vectors.start_if_needed() if service.vectors else {}
    _repos(request).audit.log("library.reindex", user=user, details={"force": force})
    return {"result": _ingest_to_dict(result), "vectors": vectors}


@router.get("/library/vectors")
def vectors_status(request: Request) -> dict[str, Any]:
    """Состояние смыслового поиска: сколько фрагментов, сколько с векторами.

    Отдельная точка, потому что спрашивают её часто: экран библиотеки
    показывает состояние постоянно, а во время построения — ещё и ход работы.
    """
    require_user(request)
    service = _service(request)
    if service.vectors is None:
        return {"vectors": {"enabled": False, "hint": "смысловой поиск недоступен"}}
    return {"vectors": service.vectors.status()}


@router.get("/library/summary")
def library_summary(request: Request) -> dict[str, Any]:
    """Собралась ли библиотека — одним взглядом, после пересборки.

    Итог приёма сегодня виден только в консоли PowerShell, а на тринадцати
    тысячах документов её содержимое уезжает вверх задолго до конца. Человек,
    ради которого всё и затевалось, узнать «сколько принято и что не принято»
    уже не может.

    Числа берём как есть, из итога приёма, а не вычитанием одного из другого.
    «Файлов на диске минус документов в базе» назвать потерей нельзя: приём
    НАМЕРЕННО не заводит второй документ для файла с тем же содержимым, а таких
    пар в библиотеке отдела много — скан и распознанная версия, .md и выгрузка
    в .txt. Такая арифметика показала бы «потеряно 400» на безупречной сборке.

    Документы без векторов сюда НЕ идут: сразу после пересборки, то есть ровно
    тогда, когда карточку и открывают, этот подсчёт стоит около 400 мс — а
    говорит о них соседняя карточка «Смысловой поиск», её дело.
    """
    require_user(request)
    settings = _settings(request)
    repos = _repos(request)
    root = str(settings.library_dir)
    return {
        "root": root,
        "report": repos.library_report.load(root),
        "counts": repos.library_report.counts(),
    }


@router.get("/library/quality")
def quality_status(request: Request) -> dict[str, Any]:
    """Сколько документов разобрано плохо и идёт ли проверка."""
    require_user(request)
    service = _service(request)
    if service.quality is None:
        return {"quality": {"running": False, "glued": 0, "hint": ""}}
    return {"quality": service.quality.status()}


@router.post("/library/quality")
def quality_check(request: Request) -> dict[str, Any]:
    """Пройти по УЖЕ загруженной библиотеке и пометить плохо разобранное.

    Склейку текста система замечает при приёме документа, но библиотека
    отдела собрана раньше: тринадцать тысяч документов лежат без единой
    пометки. Перезагружать библиотеку ради этого нельзя — повторный разбор
    PDF занимает часы и ничего не изменит. Проверяем то, что уже в базе.
    """
    user = require_admin(request)
    service = _service(request)
    if service.quality is None:
        raise ServiceError("проверка качества недоступна", 501)
    state = service.quality.start()
    _repos(request).audit.log("library.quality", user=user)
    return {"quality": state}


@router.post("/library/vectors/check")
def vectors_check(request: Request) -> dict[str, Any]:
    """Отвечает ли служба эмбеддингов — одним коротким запросом.

    Раньше узнать это можно было единственным способом: нажать «Построить
    векторы» и ждать. Ответ приходил тот же самый — «сервер эмбеддингов
    недоступен», — только через минуту и в виде неудачи, а не проверки.
    """
    require_admin(request)
    service = _service(request)
    if service.vectors is None:
        raise ServiceError("смысловой поиск недоступен", 501)
    return {"check": service.vectors.check()}


@router.post("/library/rerank/check")
def rerank_check(request: Request) -> dict[str, Any]:
    """Отвечает ли реранкер — одним коротким запросом.

    Реранк — второй проход поиска: он видит пару «вопрос — фрагмент»
    целиком и потому отличает «обратный канал» от «прямого» там, где
    первый проход видит одно слово «канал». Когда он молча отваливается,
    ответы делаются общими и не по делу, а узнать причину можно было
    только по строчке в блоке источников — где стоял голый код ошибки.
    """
    require_admin(request)
    settings = _settings(request)
    if not getattr(settings, "rerank_enabled", False):
        return {"check": {
            "ok": False, "kind": "off", "model": "",
            "error": "реранк выключен в настройках",
            "advice": "включите rerank_enabled в settings.json и перезапустите "
                      "приложение; реранкеру нужна своя служба на своём порту",
        }}
    if settings.rerank_base_url == settings.embed_base_url:
        return {"check": {
            "ok": False, "kind": "same-port", "model": settings.rerank_model,
            "error": f"реранкер и эмбеддинги настроены на один адрес "
                     f"({settings.rerank_base_url})",
            "advice": "это разные службы на разных портах: у эмбеддингов "
                      "обычно 8001, у реранкера 8002. Поправьте "
                      "rerank_base_url в settings.json",
        }}
    reranker = build_reranker(settings)
    probe = getattr(reranker, "check", None)
    if reranker is None or probe is None:
        return {"check": {
            "ok": False, "kind": "other", "model": "",
            "error": "реранкер не собрался по настройкам",
            "advice": "сверьте rerank_base_url и rerank_model в settings.json",
        }}
    return {"check": probe()}


@router.post("/library/vectors")
def vectors_build(request: Request) -> dict[str, Any]:
    """Построить векторы. ``force`` — заново все, после смены модели.

    Полная перестройка — часы работы видеокарты на большой библиотеке,
    поэтому она за начальником, а не за любым, кто открыл библиотеку.
    """
    payload = getattr(request.state, "json_body", None) or {}
    force = bool(payload.get("force", False))
    user = require_admin(request)
    service = _service(request)
    if service.vectors is None:
        raise ServiceError("смысловой поиск недоступен", 501)
    if not service.vectors.enabled:
        raise ServiceError(
            "смысловой поиск выключен в настройках (embed_enabled) — "
            "включите его и перезапустите приложение", 400)
    state = service.vectors.start(force=force)
    _repos(request).audit.log("library.vectors", user=user, details={"force": force})
    return {"vectors": state}


@router.delete("/library/{doc_id:path}")
def delete_document(request: Request, doc_id: str) -> dict[str, Any]:
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
           doc_types: str | None = None, domains: str | None = None) -> dict[str, Any]:
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
def llm_status(request: Request) -> dict[str, Any]:
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
def formats(request: Request) -> dict[str, Any]:
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
def domains(request: Request) -> dict[str, Any]:
    require_user(request)
    return {
        "items": _domains(request).to_dict(),
        "documents": _repos(request).documents.domains(),
    }


@router.put("/library/{doc_id:path}/status")
def set_document_status(request: Request, doc_id: str) -> dict[str, Any]:
    """Отметить актуальность документа.

    Заменённый и архивный документ пропадает из поиска: цитировать отменённую
    редакцию стандарта как действующую — прямая ошибка в отчёте.
    Сам документ остаётся в библиотеке для разбора старых обращений.
    """
    user = require_admin(request)
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
def set_document_domain(request: Request, doc_id: str) -> dict[str, Any]:
    user = require_admin(request)
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
def list_chats(request: Request, archived: bool = False) -> dict[str, Any]:
    user = require_anyone(request)
    chats = _assistant(request).list_chats(user, archived=archived)
    return {"items": [chat.to_dict() for chat in chats]}


@router.post("/chats")
def create_chat(request: Request) -> dict[str, Any]:
    user = require_anyone(request)
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
def get_chat(request: Request, chat_id: int) -> dict[str, Any]:
    user = require_anyone(request)
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
def update_chat(request: Request, chat_id: int) -> dict[str, Any]:
    user = require_anyone(request)
    payload = _body(request)
    assistant = _assistant(request)
    if "title" in payload:
        assistant.rename(user, chat_id, str(payload["title"]))
    domain = payload.get("domain")
    if domain is not None and not _domains(request).is_known(str(domain)):
        raise ServiceError(f"неизвестное направление '{domain}'", 400)
    mode = payload.get("mode")
    sources = payload.get("sources")
    if (domain is not None or mode is not None or sources is not None
            or "archived" in payload):
        assistant.update(
            user, chat_id,
            domain=str(domain) if domain is not None else None,
            archived=bool(payload["archived"]) if "archived" in payload else None,
            mode=str(mode) if mode is not None else None,
            sources=str(sources) if sources is not None else None,
        )
    return {"chat": assistant.get_chat(user, chat_id).to_dict()}


@router.delete("/chats/{chat_id}")
def delete_chat(request: Request, chat_id: int) -> dict[str, Any]:
    user = require_anyone(request)
    _assistant(request).delete(user, chat_id)
    return {"ok": True}


@router.post("/chats/{chat_id}/ask")
def ask(request: Request, chat_id: int) -> dict[str, Any]:
    user = require_anyone(request)
    payload = _body(request)
    text = str(payload.get("text", ""))
    return _assistant(request).ask(user, chat_id, text)


@router.post("/chats/{chat_id}/stream")
def ask_stream(request: Request, chat_id: int) -> StreamingResponse:
    """Потоковый ответ: события SSE — вопрос, источники, куски текста, итог."""
    user = require_anyone(request)
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


@router.post("/chats/{chat_id}/continue")
def continue_answer(request: Request, chat_id: int) -> StreamingResponse:
    """Продолжить оборванный ответ: те же события SSE, что у вопроса.

    Первым приходит «base» — что осталось от ответа перед продолжением, —
    потом куски текста и итог с переписанным сообщением.
    """
    user = require_anyone(request)
    payload = _body(request)
    try:
        message_id = int(payload.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    assistant = _assistant(request)

    def events():
        try:
            for event in assistant.continue_stream(user, chat_id, message_id):
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


#: Что в отделе не пересылают файлом. Список ЗАПРЕТИТЕЛЬНЫЙ, а не
#: разрешительный, и это осознанно: инженеры шлют друг другу схемы .vsd,
#: чертежи .dwg, архивы и выгрузки приборов десятка форматов, и перечислить
#: всё годное заранее нельзя — разрешительный список просто мешал бы работе.
#: А вот запрещать надо ровно одно: исполняемое.
#:
#: Машины в отделе под Windows, и файл, пришедший «от своего» в переписке,
#: открывают не глядя. Проверка была задумана и описана тестом, но тест не
#: запускался: его класс затёрло вторым объявлением с тем же именем, и семь
#: проверок работы с файлами переписки молчали.
ОПАСНЫЕ_РАСШИРЕНИЯ = frozenset((
    ".exe", ".com", ".scr", ".pif", ".msi", ".msp", ".cpl", ".dll", ".sys",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
    ".wsh", ".hta", ".jar", ".reg", ".lnk", ".inf", ".chm", ".application",
))


def _refuse_dangerous(name: str) -> None:
    """Исполняемое файлом не пересылают — ни в беседе, ни в письме."""
    if Path(name).suffix.lower() in ОПАСНЫЕ_РАСШИРЕНИЯ:
        raise ServiceError(
            "исполняемые файлы в отделе не пересылают: пришлите документ, "
            "снимок экрана или выгрузку прибора", 400)


def _attachment_kind(suffix: str) -> str:
    if suffix in STREAM_ATTACH:
        return "stream"
    if suffix in ATTACH_IMAGE:
        return "image"
    if suffix in ATTACH_DUMP:
        return "dump"
    return "document"


@router.post("/chats/{chat_id}/attachments")
def attach_to_chat(request: Request, chat_id: int, file: UploadFile = File(...)) -> dict[str, Any]:
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
    _refuse_dangerous(name)

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

        text, note = _extract_attachment(target, name)
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


# -- анализатор пакетов: захваты, список, разбор, статистика, потоки -------------------

def _pakety(request: Request):
    """Захваты страницы «Пакеты» — одно хранилище на приложение, папка в data_dir."""
    захваты = getattr(request.app.state, "pakety", None)
    if захваты is None:
        from ..setevoy.zahvaty import Захваты  # noqa: PLC0415
        захваты = Захваты(Path(_settings(request).data_dir) / "pakety")
        request.app.state.pakety = захваты
    return захваты


def _захват_или_404(request: Request, user, ид: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", ид or ""):
        raise ServiceError("захват не найден", 404)
    try:
        состояние = _pakety(request).прочитать(ид)
    except KeyError:
        raise ServiceError("захват не найден", 404) from None
    if состояние.get("владелец") != user.id:
        raise ServiceError("захват не найден", 404)
    return состояние


def _готовый(request: Request, user, ид: str) -> dict[str, Any]:
    состояние = _захват_или_404(request, user, ид)
    if состояние["состояние"] != "готово":
        raise ServiceError("захват ещё разбирается" if состояние["состояние"] in ("ждёт", "идёт")
                           else f"захват не разобран: {состояние.get('ошибка', '')}", 409)
    return состояние


def _отобранные(request: Request, ид: str, фильтр: str) -> list[int]:
    from ..setevoy.filtr import ОшибкаФильтра  # noqa: PLC0415
    try:
        return _pakety(request).отобрать(ид, фильтр)
    except ОшибкаФильтра as ошибка:
        raise ServiceError(f"фильтр: {ошибка}", 400) from None


@router.get("/pakety-protocols")
def pakety_protocols(request: Request) -> dict[str, Any]:
    """Что можно выбрать в «разбирать как» для каждого транспорта."""
    from ..setevoy.prilozh import КАК, КАК_ДАННЫЕ  # noqa: PLC0415
    require_user(request)
    return {"udp": sorted(КАК["udp"], key=str.lower), "tcp": sorted(КАК["tcp"], key=str.lower), "данные": КАК_ДАННЫЕ}


@router.post("/pakety/{cap_id}/decode-as")
def pakety_decode_as(request: Request, cap_id: str) -> dict[str, Any]:
    """Задать правила «разбирать как» ({"udp:5000": "DNS"}) и разобрать захват заново."""
    user = require_user(request)
    _захват_или_404(request, user, cap_id)
    try:
        правила = _pakety(request).разбирать_как(cap_id, _body(request).get("rules") or {})
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 409 if "разбирается" in str(ошибка) else 400) from None
    return {"rules": правила}


@router.get("/pakety-filter")
def pakety_filter_check(request: Request, text: str = "") -> dict[str, Any]:
    """Проверить выражение фильтра, не отбирая: для подсветки ошибки по мере набора."""
    from ..setevoy.filtr import ОшибкаФильтра, собрать  # noqa: PLC0415
    require_user(request)
    try:
        собрать(text[:2000])
    except ОшибкаФильтра as ошибка:
        return {"ok": False, "error": str(ошибка)}
    return {"ok": True, "error": ""}


@router.get("/pakety")
def pakety_list(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"items": _pakety(request).список(user.id)}


@router.post("/pakety")
def pakety_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """Принять захват: pcap, pcapng или .sig. Разбор — в фоне."""
    user = require_user(request)
    settings = _settings(request)
    name = _safe_name(Path(file.filename or "захват.pcap").name) or "захват.pcap"
    limit = settings.max_upload_mb * 1024 * 1024
    данные = file.file.read(limit + 1)
    if len(данные) > limit:
        raise ServiceError(f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
    if not данные:
        raise ServiceError("файл пуст", 400)
    from ..setevoy.chtenie import прочитать_захват  # noqa: PLC0415
    try:
        прочитать_захват(данные=данные[:1 << 20] if len(данные) > 1 << 20 and данные[:4] in (
            b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d",
            b"\x0a\x0d\x0d\x0a") else данные)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    ид = _pakety(request).создать(владелец=user.id, имя=name, данные=данные)
    _repos(request).audit.log("pakety.upload", user=user, object_type="pakety", object_id=ид,
                              details={"name": name, "bytes": len(данные)})
    return {"id": ид}


@router.post("/pakety/from-potok")
def pakety_from_potok(request: Request) -> dict[str, Any]:
    """Пакеты или кадры после этапа разбора потока — в анализатор пакетов."""
    user = require_user(request)
    тело = _body(request)
    job_id, этап = str(тело.get("job") or ""), int(тело.get("stage") or 0)
    состояние = _задание_или_404(request, user, job_id)
    файл = _potok(request).файл_этапа(job_id, этап)
    if файл is None or файл.suffix not in (".pcap", ".sig"):
        raise ServiceError("у этого этапа нет пакетов или кадров", 400)
    имя = f"{Path(состояние['имя']).stem} — этап {этап}{файл.suffix}"
    ид = _pakety(request).создать(владелец=user.id, имя=имя, данные=файл.read_bytes(),
                                  от=f"{job_id}#{этап}")
    return {"id": ид}


@router.get("/pakety/{cap_id}")
def pakety_state(request: Request, cap_id: str) -> dict[str, Any]:
    user = require_user(request)
    return _захват_или_404(request, user, cap_id)


@router.delete("/pakety/{cap_id}")
def pakety_delete(request: Request, cap_id: str) -> dict[str, Any]:
    user = require_user(request)
    _захват_или_404(request, user, cap_id)
    _pakety(request).удалить(cap_id)
    return {"ok": True}


@router.get("/pakety/{cap_id}/list")
def pakety_packets(request: Request, cap_id: str, filter: str = "", offset: int = 0,
                   limit: int = 500) -> dict[str, Any]:
    """Список пакетов под фильтр — страницами."""
    user = require_user(request)
    _готовый(request, user, cap_id)
    отобрано = _отобранные(request, cap_id, filter)
    сводки = _pakety(request).сводки(cap_id)
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)
    return {"всего": len(сводки), "отобрано": len(отобрано),
            "items": [сводки[i] for i in отобрано[offset:offset + limit]]}


@router.get("/pakety/{cap_id}/packet/{number}")
def pakety_packet(request: Request, cap_id: str, number: int) -> dict[str, Any]:
    """Подробный разбор пакета: уровни, поля с местом в байтах, байты."""
    user = require_user(request)
    _готовый(request, user, cap_id)
    if not 1 <= number <= len(_pakety(request).сводки(cap_id)):
        raise ServiceError("нет такого пакета", 404)
    return _pakety(request).пакет(cap_id, number)


@router.get("/pakety/{cap_id}/stats")
def pakety_stats(request: Request, cap_id: str, kind: str = "hierarchy", level: str = "ip",
                 filter: str = "", path: str = "") -> dict[str, Any]:
    """Статистика по отобранным пакетам: протоколы, диалоги, узлы, время, DNS, HTTP, TLS, ошибки;
    protocol — соотношения узла дерева протоколов по ``path``."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    _готовый(request, user, cap_id)
    захваты = _pakety(request)
    номера = _отобранные(request, cap_id, filter)
    сводки = [захваты.сводки(cap_id)[i] for i in номера]
    if kind in ("dns", "http", "tls"):
        поля = [захваты.поля(cap_id)[i] for i in номера]
        return {"items": getattr(statistika, kind)(сводки, поля)}
    if kind == "hierarchy":
        return {"items": statistika.иерархия(сводки)}
    if kind == "conversations":
        if level not in ("eth", "ip", "tcp", "udp"):
            raise ServiceError("уровень диалогов: eth, ip, tcp или udp", 400)
        return {"items": statistika.диалоги(сводки, level)[:2000]}
    if kind == "endpoints":
        return {"items": statistika.узлы(сводки)[:2000]}
    if kind == "time":
        return statistika.по_времени(сводки)
    if kind == "errors":
        return {"items": statistika.ошибки(сводки)}
    if kind == "overview":
        from ..setevoy import obekty  # noqa: PLC0415
        поля = [захваты.поля(cap_id)[i] for i in номера]
        нагрузки = захваты.нагрузки(cap_id)
        итог = statistika.обзор(сводки, поля, [нагрузки[i] for i in номера])
        if итог.get("пакетов"):
            итог["объекты"] = obekty.по_видам(захваты.объекты(cap_id, filter, _настройки_выдачи(request)))
        return итог
    if kind == "protocol":
        return statistika.узел_протокола(сводки, _путь_протокола(path))
    if kind == "unknown":
        нагрузки = захваты.нагрузки(cap_id)
        return {"items": statistika.неизвестные(сводки, [нагрузки[i] for i in номера])}
    raise ServiceError("неизвестный вид статистики", 400)


def _ряды(request: Request, cap_id: str, base: str, номера: list[int]) -> list[bytes]:
    захваты = _pakety(request)
    if base == "payload":
        нагрузки = захваты.нагрузки(cap_id)
        return [нагрузки[i] or b"" for i in номера]
    кадры = захваты.кадры(cap_id)
    return [кадры[i] for i in номера]


@router.get("/pakety/{cap_id}/matrix")
def pakety_matrix(request: Request, cap_id: str, filter: str = "", base: str = "frame", start: int = 0,
                  count: int = 64, offset: int = 0, limit: int = 300) -> dict[str, Any]:
    """Матрица байт: пакеты под фильтр — строки, байты с ``start`` — столбцы; профиль столбцов."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    _готовый(request, user, cap_id)
    if base not in ("frame", "payload"):
        raise ServiceError("выравнивание — frame (кадр) или payload (нагрузка)", 400)
    номера = _отобранные(request, cap_id, filter)
    ряды = _ряды(request, cap_id, base, номера)
    start, count = max(0, start), max(1, min(count, 256))
    limit, offset = max(1, min(limit, 2000)), max(0, offset)
    сводки = _pakety(request).сводки(cap_id)
    return {"отобрано": len(номера), "наибольшая_длина": max((len(р) for р in ряды), default=0),
            "столбцы": statistika.профиль_столбцов(ряды, start, count),
            "строки": [{"номер": сводки[i]["номер"], "протокол": сводки[i]["протокол"],
                        "длина": len(р), "hex": р[start:start + count].hex()}
                       for i, р in list(zip(номера, ряды, strict=False))[offset:offset + limit]]}


@router.get("/pakety/{cap_id}/column")
def pakety_column(request: Request, cap_id: str, filter: str = "", base: str = "frame", pos: int = 0,
                  width: int = 1) -> dict[str, Any]:
    """Полная статистика столбца (поля 1–8 байт) по отобранным пакетам."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    _готовый(request, user, cap_id)
    if base not in ("frame", "payload") or not 1 <= width <= 8 or pos < 0:
        raise ServiceError("выравнивание frame/payload, ширина поля 1–8 байт", 400)
    номера = _отобранные(request, cap_id, filter)
    return statistika.столбец(_ряды(request, cap_id, base, номера), pos, width)


@router.get("/pakety/{cap_id}/files")
def pakety_files(request: Request, cap_id: str, filter: str = "") -> dict[str, Any]:
    """Файлы, переданные внутри потоков TCP/UDP: по сигнатуре со сверкой структуры."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    _готовый(request, user, cap_id)
    захваты = _pakety(request)
    номера = _отобранные(request, cap_id, filter)
    сводки, нагрузки = захваты.сводки(cap_id), захваты.нагрузки(cap_id)
    return {"items": statistika.файлы([сводки[i] for i in номера], [нагрузки[i] for i in номера])}


@router.get("/pakety/{cap_id}/file")
def pakety_file(request: Request, cap_id: str, flow: str, offset: int = 0, length: int = 0,
                ext: str = "bin") -> Response:
    """Вырезать файл из собранного потока."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    _готовый(request, user, cap_id)
    захваты = _pakety(request)
    try:
        данные = statistika.вырезать_из_потока(захваты.сводки(cap_id), захваты.нагрузки(cap_id), flow,
                                               max(0, offset), max(0, length))
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 404) from None
    расширение = re.sub(r"[^0-9a-z]", "", ext.lower())[:8] or "bin"
    return Response(данные, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="stream-{max(0, offset)}.{расширение}"; '
                             "filename*=UTF-8''" + urllib.parse.quote(f"из-потока-{max(0, offset)}.{расширение}")})


#: Пределы настроек выдачи объектов, которые может задать пользователь (сервер не должен упасть).
ОБЪЕКТЫ_НАИБ_МБ = 64
ZIP_НАИБ_МБ = 1024
ОБЪЕКТОВ_НАИБ = 50000


def _настройки_выдачи(request: Request):
    """Настройки выдачи из параметров запроса (страница «Пакеты» хранит их у пользователя в браузере):
    decompress, silence (0/1), audio (wav/raw), video (annexb/nal), json (pretty/compact), maxobj и maxzip (МБ),
    maxcount, names (proto/flow/num), tz (минуты к UTC)."""
    from ..setevoy.obekty import Настройки  # noqa: PLC0415
    п = request.query_params

    def да(имя: str) -> bool:
        return п.get(имя, "1") not in ("0", "false", "no")

    def число(имя: str, по: int, наиб: int, наим: int = 1) -> int:
        try:
            return max(наим, min(наиб, int(п.get(имя, по))))
        except ValueError:
            return по

    по = Настройки()
    return Настройки(снимать_сжатие=да("decompress"), тишина=да("silence"),
                     звук="сырой" if п.get("audio") == "raw" else "wav",
                     видео="nal" if п.get("video") == "nal" else "annexb", json_отступ=п.get("json") == "pretty",
                     объект_до=число("maxobj", по.объект_до >> 20, ОБЪЕКТЫ_НАИБ_МБ) << 20,
                     байт_до=число("maxzip", по.байт_до >> 20, ZIP_НАИБ_МБ) << 20,
                     объектов_до=число("maxcount", по.объектов_до, ОБЪЕКТОВ_НАИБ),
                     имена={"flow": "поток", "num": "номер"}.get(п.get("names", ""), "протокол"),
                     пояс=число("tz", 0, 14 * 60, -14 * 60))


def _объекты(request: Request, cap_id: str, фильтр: str, путь: str = "") -> tuple[dict[str, Any], list[Any], Any]:
    """(итог сборки, объекты — все или только с пакетами протокола по ``путь``, настройки)."""
    user = require_user(request)
    _готовый(request, user, cap_id)
    _отобранные(request, cap_id, фильтр)
    настройки = _настройки_выдачи(request)
    захваты = _pakety(request)
    итог = захваты.объекты(cap_id, фильтр, настройки)
    объекты = итог["объекты"]
    if путь:
        стек = _путь_протокола(путь)
        сводки = захваты.сводки(cap_id)
        объекты = [о for о in объекты if any(сводки[н - 1]["стек"][:len(стек)] == стек for н in о.пакеты)]
    return итог, объекты, настройки


def _путь_протокола(путь: str) -> list[str]:
    """Путь узла дерева протоколов: «Ethernet/IPv4/TCP» → ["Ethernet", "IPv4", "TCP"]."""
    return [ч for ч in путь.split("/") if ч]


def _имя_в_заголовке(имя: str, запасное: str) -> str:
    return (f'attachment; filename="{запасное}"; filename*=UTF-8\'\'' + urllib.parse.quote(имя, safe=""))


@router.get("/pakety/{cap_id}/objects")
def pakety_objects(request: Request, cap_id: str, filter: str = "", kind: str = "", path: str = "") -> dict[str, Any]:
    """Объекты и файлы захвата (как «Экспорт объектов» Wireshark, шире): вид, имя, тип, длина, поток, пакеты,
    заметки, вид по содержимому и опись архивов; под фильтром отбора пакетов; ``path`` — только объекты протокола."""
    итог, объекты, _ = _объекты(request, cap_id, filter, path)
    виды = [в for в in kind.split(",") if в]
    отобрано = [о for о in объекты if not виды or о.вид in виды]
    return {"items": [о.в_словарь() for о in отобрано], "виды": dict(Counter(о.вид for о in объекты)),
            "всего": len(объекты), "байт": sum(len(о.данные) for о in объекты),
            "отброшено": итог["отброшено"], "заметки": итог["заметки"]}


@router.get("/pakety/{cap_id}/object/{number}")
def pakety_object(request: Request, cap_id: str, number: int, filter: str = "", member: int = -1) -> Response:
    """Один объект (или член его архива, ``member`` — номер в описи) — файлом."""
    from ..setevoy import obekty  # noqa: PLC0415
    итог, объекты, настройки = _объекты(request, cap_id, filter)
    о = next((о for о in объекты if о.номер == number), None)
    if о is None:
        raise ServiceError("нет такого объекта", 404)
    if member >= 0:
        try:
            имя, данные = obekty.член(о.данные, о.расширение, member)
        except ValueError as ошибка:
            raise ServiceError(str(ошибка), 404) from None
        имя = obekty.безопасное_имя(имя)
        тип = "application/octet-stream"
    else:
        данные, тип = о.данные, о.тип if о.тип.startswith("audio/") else "application/octet-stream"
        время = итог["время"].get(о.пакеты[0]) if о.пакеты else None
        имя = о.имя if настройки.имена == "протокол" else obekty.имя_выгрузки(о, настройки, время)
        имя = obekty.безопасное_имя(имя)
    запасное = f"object-{number}" + (f"-{member}" if member >= 0 else "") + (
        "." + re.sub(r"[^0-9A-Za-z]", "", имя.rsplit(".", 1)[-1])[:8] if "." in имя else "")
    return Response(данные, media_type=тип, headers={"Content-Disposition": _имя_в_заголовке(имя, запасное),
                                                     "X-Content-Type-Options": "nosniff"})


@router.get("/pakety/{cap_id}/objects.zip")
def pakety_objects_zip(request: Request, cap_id: str, filter: str = "", kind: str = "", path: str = "") -> Response:
    """Все объекты (или отобранные виды) одним ZIP: имена без повторов и «..», опись.csv, что отброшено."""
    from ..setevoy import obekty  # noqa: PLC0415
    итог, объекты, настройки = _объекты(request, cap_id, filter, path)
    состояние = _pakety(request).прочитать(cap_id)
    данные = obekty.zip_объектов({**итог, "объекты": объекты}, [в for в in kind.split(",") if в], настройки)
    имя = f"{Path(состояние['имя']).stem}-объекты.zip"
    return Response(данные, media_type="application/zip",
                    headers={"Content-Disposition": _имя_в_заголовке(имя, "objects.zip")})


@router.get("/pakety/{cap_id}/stream/{number}")
def pakety_stream(request: Request, cap_id: str, number: int) -> dict[str, Any]:
    """Следовать за потоком TCP/UDP/SCTP, в котором стоит пакет."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    _готовый(request, user, cap_id)
    захваты = _pakety(request)
    try:
        return statistika.поток(захваты.сводки(cap_id), захваты.нагрузки(cap_id), number)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None


@router.get("/pakety/{cap_id}/export")
def pakety_export(request: Request, cap_id: str, filter: str = "", format: str = "pcap", path: str = "") -> Response:
    """Отобранные пакеты — pcap, pcapng, CSV (номер, время, адреса, протокол, длина, сведения), JSON (список)
    или dissect (полный разбор — дерево полей). ``path`` — только пакеты узла дерева протоколов."""
    from ..setevoy import vygruzka  # noqa: PLC0415
    user = require_user(request)
    состояние = _готовый(request, user, cap_id)
    номера = _отобранные(request, cap_id, filter)
    основа = Path(состояние["имя"]).stem
    захваты = _pakety(request)
    if path:
        стек = _путь_протокола(path)
        сводки_ = захваты.сводки(cap_id)
        номера = [i for i in номера if сводки_[i]["стек"][:len(стек)] == стек]
        основа += "-" + vygruzka.безопасно(стек[-1] if стек else "все")
    настройки = _настройки_выдачи(request)
    if format == "pcapng":
        return Response(захваты.выгрузить_pcapng(cap_id, номера), media_type="application/octet-stream",
                        headers={"Content-Disposition": _имя_в_заголовке(основа + "-отбор.pcapng", "export.pcapng")})
    if format == "json":
        список = vygruzka.список_пакетов([захваты.сводки(cap_id)[i] for i in номера], настройки.пояс)
        return Response(vygruzka.json_байты(список, настройки.json_отступ), media_type="application/json",
                        headers={"Content-Disposition": _имя_в_заголовке(основа + "-список.json", "list.json")})
    if format == "dissect":
        сводки_ = захваты.сводки(cap_id)

        def поток_json():
            yield b"["
            for j, i in enumerate(номера):
                п = next(iter(vygruzka.разбор([захваты.пакет(cap_id, сводки_[i]["номер"])])))
                yield (b"," if j else b"") + vygruzka.json_байты(п, настройки.json_отступ)
            yield b"]"
        return StreamingResponse(поток_json(), media_type="application/json",
                                 headers={"Content-Disposition": _имя_в_заголовке(основа + "-разбор.json",
                                                                                  "dissect.json")})
    if format == "csv":
        import csv  # noqa: PLC0415
        import io  # noqa: PLC0415
        буфер = io.StringIO()
        запись = csv.writer(буфер, delimiter=";")
        запись.writerow(["№", "время", "источник", "получатель", "протокол", "длина", "сведения"])
        сводки = _pakety(request).сводки(cap_id)
        for i in номера:
            с = сводки[i]
            запись.writerow([с["номер"], f"{с['время']:.6f}", с["источник"], с["получатель"], с["протокол"],
                             с["длина"], с["инфо"]])
        return Response(("\ufeff" + буфер.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''"
                                 + urllib.parse.quote(основа + ".csv")})
    данные = _pakety(request).выгрузить_pcap(cap_id, номера)
    return Response(данные, media_type="application/vnd.tcpdump.pcap",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''"
                             + urllib.parse.quote(основа + "-отбор.pcap")})


def _отбор_сводок(request: Request, cap_id: str, фильтр: str) -> tuple[dict[str, Any], list[int], list[Any]]:
    user = require_user(request)
    состояние = _готовый(request, user, cap_id)
    номера = _отобранные(request, cap_id, фильтр)
    сводки = _pakety(request).сводки(cap_id)
    return состояние, номера, [сводки[i] for i in номера]


@router.get("/pakety/{cap_id}/stats-file")
def pakety_stats_file(request: Request, cap_id: str, kind: str = "hierarchy", format: str = "csv",
                      filter: str = "") -> Response:
    """Таблица статистики файлом (CSV или JSON): hierarchy — дерево протоколов с долями, conversations-eth/ip/
    tcp/udp, endpoints, time, dns, http, tls, errors, unknown."""
    from ..setevoy import vygruzka  # noqa: PLC0415
    состояние, номера, сводки = _отбор_сводок(request, cap_id, filter)
    имена = {"hierarchy": "иерархия", "conversations-eth": "диалоги-eth", "conversations-ip": "диалоги-ip",
             "conversations-tcp": "диалоги-tcp", "conversations-udp": "диалоги-udp", "endpoints": "узлы",
             "time": "время", "dns": "dns", "http": "http", "tls": "tls", "errors": "ошибки", "unknown": "неизвестные"}
    if kind not in имена or format not in ("csv", "json"):
        raise ServiceError("вид таблицы или формат не известен", 400)
    захваты = _pakety(request)
    if kind == "hierarchy":
        from ..setevoy import statistika  # noqa: PLC0415
        таблица: Any = statistika.иерархия_строками(statistika.иерархия(сводки)[0])
    else:
        поля, нагрузки = захваты.поля(cap_id), захваты.нагрузки(cap_id)
        таблица = vygruzka.статистика(сводки, [поля[i] for i in номера], [нагрузки[i] for i in номера])[имена[kind]]
    настройки = _настройки_выдачи(request)
    имя = f"{Path(состояние['имя']).stem}-{имена[kind]}.{format}"
    данные = vygruzka.csv_байты(таблица) if format == "csv" else vygruzka.json_байты(таблица, настройки.json_отступ)
    return Response(данные, media_type="text/csv; charset=utf-8" if format == "csv" else "application/json",
                    headers={"Content-Disposition": _имя_в_заголовке(имя, f"stats.{format}")})


@router.get("/pakety/{cap_id}/fields")
def pakety_fields(request: Request, cap_id: str, path: str, format: str = "csv", filter: str = "") -> Response:
    """Поля протокола узла дерева (все разобранные поля этого уровня у его пакетов): CSV или JSON."""
    from ..setevoy import vygruzka  # noqa: PLC0415
    состояние, _, сводки = _отбор_сводок(request, cap_id, filter)
    стек = _путь_протокола(path)
    if not стек or format not in ("csv", "json"):
        raise ServiceError("нужен путь узла и формат csv или json", 400)
    свои = [с["номер"] for с in сводки if с["стек"][:len(стек)] == стек][:vygruzka.РАЗБОР_ДО]
    захваты = _pakety(request)
    строки = vygruzka.поля_по_протоколам((захваты.пакет(cap_id, н) for н in свои), стек).get(стек[-1], [])
    настройки = _настройки_выдачи(request)
    имя = f"{Path(состояние['имя']).stem}-поля-{vygruzka.безопасно(стек[-1])}.{format}"
    данные = vygruzka.csv_байты(строки) if format == "csv" else vygruzka.json_байты(строки, настройки.json_отступ)
    return Response(данные, media_type="text/csv; charset=utf-8" if format == "csv" else "application/json",
                    headers={"Content-Disposition": _имя_в_заголовке(имя, f"fields.{format}")})


@router.get("/pakety/{cap_id}/streams.zip")
def pakety_streams_zip(request: Request, cap_id: str, filter: str = "", path: str = "") -> Response:
    """Сырые потоки TCP/UDP по направлениям (.bin) одним ZIP с описью; ``path`` — только потоки узла дерева."""
    from ..setevoy import vygruzka  # noqa: PLC0415
    состояние, номера, сводки = _отбор_сводок(request, cap_id, filter)
    нагрузки = _pakety(request).нагрузки_тр(cap_id)
    настройки = _настройки_выдачи(request)
    архив = vygruzka.Архив(настройки.байт_до, настройки.пояс)
    for имя, данные, сторона, транспорт in vygruzka.потоки(сводки, [нагрузки[i] for i in номера],
                                                            _путь_протокола(path)):
        архив.положить(имя, данные, "сырой поток по направлению", поток=сторона.поток,
                       пакеты=[н for _, н in сторона.карта], протокол=транспорт)
    файл = архив.закрыть(отступ=настройки.json_отступ)
    return Response(файл.read(), media_type="application/zip",
                    headers={"Content-Disposition": _имя_в_заголовке(f"{Path(состояние['имя']).stem}-потоки.zip",
                                                                     "streams.zip")})


@router.get("/pakety/{cap_id}/report")
def pakety_report(request: Request, cap_id: str, filter: str = "", format: str = "html") -> Response:
    """Отчёт-обзор захвата: HTML без внешних ресурсов или текст."""
    from ..setevoy import obekty, statistika, vygruzka  # noqa: PLC0415
    состояние, номера, сводки = _отбор_сводок(request, cap_id, filter)
    захваты = _pakety(request)
    поля, нагрузки = захваты.поля(cap_id), захваты.нагрузки(cap_id)
    настройки = _настройки_выдачи(request)
    обзор = statistika.обзор(сводки, [поля[i] for i in номера], [нагрузки[i] for i in номера])
    итог = захваты.объекты(cap_id, filter, настройки)
    страница, текст = vygruzka.отчёт(состояние["имя"], обзор, statistika.иерархия_строками(
        statistika.иерархия(сводки)[0]), obekty.по_видам(итог), итог["объекты"], настройки.пояс)
    основа = Path(состояние["имя"]).stem
    if format == "txt":
        return Response(текст.encode(), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": _имя_в_заголовке(основа + "-обзор.txt", "report.txt")})
    return Response(страница.encode(), media_type="application/octet-stream",
                    headers={"Content-Disposition": _имя_в_заголовке(основа + "-обзор.html", "report.html"),
                             "X-Content-Type-Options": "nosniff"})


@router.get("/pakety/{cap_id}/outputs")
def pakety_outputs(request: Request, cap_id: str, filter: str = "") -> dict[str, Any]:
    """Что можно выгрузить из отбора: пункты (ключ, группа, название, отмечен ли по умолчанию) и сколько чего."""
    from ..setevoy import obekty, vygruzka  # noqa: PLC0415
    _, номера, сводки = _отбор_сводок(request, cap_id, filter)
    итог = _pakety(request).объекты(cap_id, filter, _настройки_выдачи(request))
    виды = obekty.по_видам(итог)
    ртп = sum(н for в, н in виды.items() if в in vygruzka.RTP_ВИДЫ)
    потоков = len({("TCP" in с["стек"], *sorted(((с["источник"], с["порт_от"]), (с["получатель"], с["порт_к"]))))
                   for с in сводки if с.get("порт_от") is not None and ({"TCP", "UDP"} & set(с["стек"]))})
    сколько = {"pcap": len(номера), "pcapng": len(номера), "list-csv": len(номера), "list-json": len(номера),
               "dissect-json": min(len(номера), vygruzka.РАЗБОР_ДО),
               "fields-csv": len({п for с in сводки for п in с["стек"]}), "stats": 12,
               "objects": sum(виды.values()) - ртп, "rtp": ртп, "streams": потоков, "report": 2}
    return {"items": [{"ключ": к, "группа": г, "название": н, "отмечен": о, "сколько": сколько[к]}
                      for к, г, н, о in vygruzka.ВЫХОДЫ], "виды": виды, "отброшено": итог["отброшено"]}


@router.get("/pakety/{cap_id}/bundle.zip")
def pakety_bundle(request: Request, cap_id: str, filter: str = "", items: str = "") -> Response:
    """Отмеченное одним ZIP: папки по видам, опись.csv и опись.json (путь, что, откуда, время, размер, SHA-256)."""
    from ..setevoy import vygruzka  # noqa: PLC0415
    состояние, _, _ = _отбор_сводок(request, cap_id, filter)
    пункты = [п for п in items.split(",") if п]
    if not пункты or any(п not in vygruzka.КЛЮЧИ for п in пункты):
        raise ServiceError("отметьте, что выгружать: " + ", ".join(vygruzka.КЛЮЧИ), 400)
    файл = vygruzka.собрать_выгрузку(_pakety(request), cap_id, filter, пункты, _настройки_выдачи(request))

    def куски():
        with файл:
            while кусок := файл.read(1 << 20):
                yield кусок
    return StreamingResponse(куски(), media_type="application/zip", headers={
        "Content-Disposition": _имя_в_заголовке(f"{Path(состояние['имя']).stem}-выгрузка.zip", "bundle.zip")})


@router.post("/pakety/{cap_id}/ask")
def pakety_ask(request: Request, cap_id: str) -> dict[str, Any]:
    """Разговор с помощником о захвате или пакете: разбор и статистика — вложением."""
    from ..setevoy import statistika  # noqa: PLC0415
    user = require_user(request)
    состояние = _готовый(request, user, cap_id)
    тело = _body(request)
    захваты = _pakety(request)
    сводки = захваты.сводки(cap_id)
    строки = [f"Захват «{состояние['имя']}» ({состояние['формат']}), пакетов {len(сводки)}."]

    def дерево(узлы, отступ=0):
        for у in узлы:
            строки.append("  " * отступ + f"{у['протокол']}: пакетов {у['пакетов']}, байт {у['байт']}")
            дерево(у["дети"], отступ + 1)

    строки.append("Иерархия протоколов:")
    дерево(statistika.иерархия(сводки))
    строки.append("Диалоги (IP), первые 15:")
    for д in statistika.диалоги(сводки, "ip")[:15]:
        строки.append(f"  {д['а']} ↔ {д['б']}: пакетов {д['пакетов']}, байт {д['байт']}, {д['протоколы']}")
    ошибки = statistika.ошибки(сводки)
    if ошибки:
        строки.append("Ошибки: " + "; ".join(f"{о['что']} ×{о['пакетов']}" for о in ошибки[:10]))
    номер = int(тело.get("number") or 0)
    вопрос = ("Разбери этот сетевой захват: что за трафик, какие протоколы и узлы, что необычного "
              "и что проверить дальше? Опирайся на документы библиотеки (RFC, стандарты).")
    if номер:
        if not 1 <= номер <= len(сводки):
            raise ServiceError("нет такого пакета", 404)
        пакет = захваты.пакет(cap_id, номер)
        строки.append(f"\nПакет №{номер}: {пакет['инфо']}")

        def поля(список, отступ=1):
            for п in список:
                строки.append("  " * отступ + f"{п['имя']}: {п['текст']} [байты {п['смещение']}…"
                              f"{п['смещение'] + max(0, п['длина'] - 1)}]" + (" — ОШИБКА" if п["плохо"] else ""))
                поля(п["дети"], отступ + 1)

        for у in пакет["уровни"]:
            строки.append(f"{у['полное']}: {у['итог']}")
            поля(у["поля"])
        строки.append("Байты: " + пакет["данные"][:4000])
        вопрос = (f"Объясни пакет №{номер} ({пакет['инфо']}): что это за протокол и сообщение, что "
                  "значат поля, нет ли в нём ошибок или необычного. Опирайся на RFC и стандарты из "
                  "библиотеки и называй их.")
    текст = "\n".join(строки)
    chat = _assistant(request).create_chat(user, title=f"Пакеты: {состояние['имя']}"[:120], domain="",
                                          case_ref=None)
    _repos(request).chats.add_attachment(chat.id, f"захват-{cap_id}" + (f"-пакет-{номер}" if номер else "")
                                         + ".txt", "dump", size=len(текст.encode("utf-8")), text=текст,
                                         note="разбор из анализатора пакетов")
    return {"chat": chat.to_dict(), "question": вопрос}


# -- разбор потока: задания по этапам ------------------------------------------------

def _potok(request: Request):
    """Очередь заданий разбора потока — одна на приложение, папка в data_dir."""
    задания = getattr(request.app.state, "potok", None)
    if задания is None:
        from ..potok.zadaniya import Задания  # noqa: PLC0415 — numpy только здесь
        задания = Задания(Path(_settings(request).data_dir) / "potok")
        request.app.state.potok = задания
    return задания


def _sessii(request: Request):
    """Сессии работы с потоками — общие столы нескольких файлов и людей."""
    сессии = getattr(request.app.state, "sessii", None)
    if сессии is None:
        from ..potok.sessii import Сессии  # noqa: PLC0415
        сессии = Сессии(Path(_settings(request).data_dir) / "sessii")
        request.app.state.sessii = сессии
    return сессии


def _мои_сессии(request: Request, user) -> list[str]:
    return _sessii(request).доступные(user.id)


def _вправе_удалить(request: Request, user, состояние: dict[str, Any]) -> None:
    """Узел сессии удаляет тот, кто его сделал, или владелец сессии — не любой участник."""
    причина = _нельзя_удалить(request, user, состояние)
    if причина:
        raise ServiceError(причина, 403)


def _нельзя_удалить(request: Request, user, состояние: dict[str, Any]) -> str:
    """Почему пользователю нельзя удалить узел задания; пусто — можно (см. ``_вправе_удалить``)."""
    if состояние.get("владелец") == user.id or not состояние.get("сессия"):
        return ""
    try:
        сессия = _sessii(request).прочитать(состояние["сессия"])
    except KeyError:
        сессия = {}
    return "" if сессия.get("владелец") == user.id else "удалить чужой узел сессии вправе только владелец сессии"


def _задание_или_404(request: Request, user, ид: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", ид or ""):
        raise ServiceError("задание не найдено", 404)
    try:
        состояние = _potok(request).прочитать(ид)
    except KeyError:
        raise ServiceError("задание не найдено", 404) from None
    # Как разговоры с помощником: разбор виден только тому, кто его запустил, —
    # или участникам сессии, в которой он лежит.
    if состояние.get("владелец") != user.id:
        сессия = состояние.get("сессия") or ""
        try:
            _sessii(request).для(сессия, user.id)
        except KeyError:
            raise ServiceError("задание не найдено", 404) from None
    return состояние


ПРОФИЛИ_РАЗБОРА = ("быстро", "обычно", "глубоко")


def _слои(значение: str) -> list[str]:
    """Слои для ручного снятия — по строке на слой."""
    return [строка.strip() for строка in (значение or "").splitlines() if строка.strip()][:12]


@router.post("/potok")
def potok_start(request: Request, file: UploadFile = File(...), profile: str = Form("обычно"),
                strip: str = Form(""), bits: str = Form(""), config: str = Form("")) -> dict[str, Any]:
    """Принять поток и поставить разбор в очередь. Этапы — по /api/potok/{ид}."""
    user = require_user(request)
    settings = _settings(request)
    name = _safe_name(Path(file.filename or "поток.bin").name) or "поток.bin"
    if profile not in ПРОФИЛИ_РАЗБОРА:
        raise ServiceError("неизвестный профиль разбора", 400)
    limit = settings.max_upload_mb * 1024 * 1024
    данные = file.file.read(limit + 1)
    if len(данные) > limit:
        raise ServiceError(f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
    if not данные:
        raise ServiceError("файл пуст", 400)
    from ..potok.ploskost import модуляции  # noqa: PLC0415
    символ, фм = модуляции(bits or "")
    символ, фм = символ[:4], фм[:4]
    if (bits or "").strip() and not (символ or фм):
        raise ServiceError("модуляция: 8PSK, QPSK, BPSK, 16QAM, КАМ-64 или бит на символ "
                           "(чётное — КАМ, нечётное — ФМ)", 400)
    if config:
        # Конфигурация — шаги по порядку над битами файла (у .Sig — тела пакетов подряд),
        # затем разбор автоматом того, что получилось.
        from ..potok import rastr  # noqa: PLC0415
        from ..potok.chtenie import прочитать as прочитать_поток  # noqa: PLC0415
        конфигурация = _конфигурация(request, user, config)
        поток = прочитать_поток(данные=данные, имя=name)
        описание = [rastr.описать_шаг(ш) for ш in конфигурация["шаги"] if ш["вкл"]]
        ид = _potok(request).создать(владелец=user.id, имя=f"{name} → «{конфигурация['имя']}»"[:200],
                                     данные=поток.данные, профиль=profile, шаги=конфигурация["шаги"],
                                     разбирать=True, происхождение=описание)
    else:
        ид = _potok(request).создать(владелец=user.id, имя=name, данные=данные, профиль=profile,
                                     снять=_слои(strip), символ=символ, фм=фм)
    _repos(request).audit.log("potok.start", user=user, object_type="potok", object_id=ид,
                              details={"name": name, "bytes": len(данные), "profile": profile})
    return {"id": ид}


@router.get("/potok")
def potok_list(request: Request) -> dict[str, Any]:
    user = require_user(request)
    return {"items": _potok(request).список(user.id, без_сессий=True)}


@router.get("/potok/{job_id}")
def potok_state(request: Request, job_id: str) -> dict[str, Any]:
    user = require_user(request)
    return _задание_или_404(request, user, job_id)


@router.get("/potok/{job_id}/stage/{stage}")
def potok_stage_file(request: Request, job_id: str, stage: int) -> FileResponse:
    """Поток после этапа: биты — .bin, кадры — .sig, пакеты IP — .pcap."""
    user = require_user(request)
    состояние = _задание_или_404(request, user, job_id)
    файл = (_potok(request).папка / job_id / "вход.bin") if int(stage) == 0 else _potok(request).файл_этапа(job_id, stage)
    if файл is None or not файл.exists():
        raise ServiceError("у этого этапа нет выгрузки", 404)
    основа = Path(состояние.get("имя") or "поток").stem
    return _file_reply(файл, f"{основа}-этап-{stage}{файл.suffix}")


@router.post("/potok/{job_id}/continue")
def potok_continue(request: Request, job_id: str) -> dict[str, Any]:
    """Продолжить разбор с потока после этапа — со снятием слоёв вручную."""
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    тело = _body(request)
    профиль = str(тело.get("profile") or "обычно")
    if профиль not in ПРОФИЛИ_РАЗБОРА:
        raise ServiceError("неизвестный профиль разбора", 400)
    try:
        ид = _potok(request).продолжить(job_id, int(тело.get("stage") or 0), владелец=user.id,
                                        снять=_слои(str(тело.get("strip") or "")),
                                        профиль=профиль)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    return {"id": ид}


# -- растр и ручные инструменты ----------------------------------------------------------

def _файл_бит_или_400(request: Request, user, job_id: str, stage: int) -> None:
    """Доступ к массиву и что у этапа есть биты — без распаковки самих бит."""
    _задание_или_404(request, user, job_id)
    try:
        _potok(request).файл_бит(job_id, int(stage))
    except (ValueError, OSError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None


def _биты_задания(request: Request, user, job_id: str, stage: int):
    _задание_или_404(request, user, job_id)
    try:
        return _potok(request).биты(job_id, int(stage))
    except (ValueError, OSError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None


@router.get("/potok/{job_id}/bits")
def potok_bits(request: Request, job_id: str, stage: int = 0, start: int = 0,
               count: int = 1 << 16) -> dict[str, Any]:
    """Окно бит для растра."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    return rastr.окно(_биты_задания(request, user, job_id, stage), start, count)


@router.get("/potok/{job_id}/periods")
def potok_periods(request: Request, job_id: str, stage: int = 0) -> dict[str, Any]:
    """Кандидаты периода по автокорреляции."""
    from ..potok import cikl, rastr  # noqa: PLC0415
    user = require_user(request)
    задания = _potok(request)
    _файл_бит_или_400(request, user, job_id, stage)
    return {"items": задания.запомнить(job_id, stage, "периоды",
                                       lambda: rastr.периоды(задания.биты_участка(job_id, stage, 0, cikl.ВЫБОРКА)))}


#: График автокорреляции — лаги не дальше этого.
АВТОКОРРЕЛЯЦИЯ_ДО = 1 << 16


@router.get("/potok/{job_id}/autocorr")
def potok_autocorr(request: Request, job_id: str, stage: int = 0, max: int = 8192) -> dict[str, Any]:  # noqa: A002
    """Автокорреляция по лагам 0…max (по началу массива, как поиск периода) — для графика в окне поиска."""
    from ..potok import cikl  # noqa: PLC0415
    user = require_user(request)
    _файл_бит_или_400(request, user, job_id, stage)
    задания = _potok(request)
    наибольший = min(АВТОКОРРЕЛЯЦИЯ_ДО, max if max > 0 else 8192)

    def посчитать():
        выборка = задания.биты_участка(job_id, stage, 0, cikl.ВЫБОРКА)
        лагов = min(наибольший, len(выборка) // 2)
        r = cikl.автокорреляция(выборка, лагов) if лагов >= 1 else []
        return {"r": [round(float(x), 4) for x in r], "бит": len(выборка),
                "шум": round(1.0 / len(выборка) ** 0.5, 5) if len(выборка) else 0.0}

    return задания.запомнить(job_id, stage, f"автокорреляция:{наибольший}", посчитать)


@router.post("/potok/{job_id}/tool")
def potok_tool(request: Request, job_id: str) -> dict[str, Any]:
    """Быстрый инструмент над потоком этапа или каналом по маске."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    try:
        if тело.get("mask"):
            биты = rastr.по_маске(биты, тело["mask"])
        найдено = rastr.инструмент(
            биты, str(тело.get("tool") or ""), int(тело.get("k") or 0),
            **({"период": int(тело.get("period") or 0), "сдвиг": int(тело.get("shift") or 0),
                "пропуск": int(тело.get("skip") or 0),
                "отводы": [int(t) for t in re.findall(r"\d+", str(тело.get("taps") or ""))]}
               if тело.get("tool") == "скремблер-кадр" else
               {"период": int(тело.get("period") or 0), "сдвиг": int(тело.get("shift") or 0)}
               if тело.get("tool") == "поля" else {}))
    except (ValueError, KeyError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    return {"found": rastr.в_словарь(найдено), "bits": int(len(биты))}


@router.post("/potok/{job_id}/derive")
def potok_derive(request: Request, job_id: str) -> dict[str, Any]:
    """Производный поток: канал по маске и/или снятые вручную слои — новым узлом дерева.

    Шаги (``steps`` — по порядку; или по-старому ``mask`` и ``strip``)
    выполняются в задании: долгие слои (LDPC, Форни) не держат запрос.
    """
    import numpy as np  # noqa: PLC0415 — numpy только для анализатора

    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    состояние = _задание_или_404(request, user, job_id)
    тело = _body(request)
    этап = int(тело.get("stage") or 0)
    биты = _биты_задания(request, user, job_id, этап)
    профиль = str(тело.get("profile") or "обычно")
    if профиль not in ПРОФИЛИ_РАЗБОРА:
        raise ServiceError("неизвестный профиль разбора", 400)
    шаги = list(тело.get("steps") or [])
    конфигурация = None
    if тело.get("config"):
        конфигурация = _конфигурация(request, user, str(тело["config"]))
        шаги = list(конфигурация["шаги"])
    if not шаги:
        if тело.get("mask"):
            шаги.append({"вид": "маска", "маска": тело["mask"]})
        шаги += [{"вид": "слой", "слой": с} for с in _слои(str(тело.get("strip") or ""))]
    try:
        шаги = rastr.проверить_шаги(шаги)
    except (ValueError, KeyError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    описание = [rastr.описать_шаг(ш) for ш in шаги if ш["вкл"]]
    имя = f"{состояние['имя']} → " + (f"«{конфигурация['имя']}»" if конфигурация
                                      else "; ".join(описание) or f"этап {этап}")
    ид = _potok(request).создать(владелец=user.id, имя=имя[:200],
                                 данные=np.packbits(биты).tobytes(),
                                 профиль=профиль, от=f"{job_id}#{этап}", шаги=шаги,
                                 разбирать=bool(тело.get("analyze")), происхождение=описание,
                                 сессия=состояние.get("сессия") or "", бит=len(биты))
    return {"id": ид}


# -- конфигурации обработки: цепочки шагов, составленные аналитиками ------------------------

def _konfig(request: Request):
    хранилище = getattr(request.app.state, "konfig", None)
    if хранилище is None:
        from ..potok.konfig import Конфигурации  # noqa: PLC0415
        хранилище = Конфигурации(Path(_settings(request).data_dir) / "konfig")
        request.app.state.konfig = хранилище
    return хранилище


def _конфигурация(request: Request, user, ид: str) -> dict[str, Any]:
    try:
        return _konfig(request).прочитать(ид, user.id)
    except KeyError:
        raise ServiceError("конфигурация не найдена", 404) from None


@router.get("/potok-configs")
def potok_configs(request: Request) -> dict[str, Any]:
    """Свои конфигурации и общие конфигурации отдела."""
    user = require_user(request)
    return {"items": _konfig(request).список(user.id)}


@router.post("/potok-configs")
def potok_config_save(request: Request) -> dict[str, Any]:
    """Создать (без id) или исправить свою конфигурацию; из файла — поле ``file``."""
    from ..potok.konfig import НетДоступа  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    хранилище = _konfig(request)
    try:
        if тело.get("file") is not None:
            запись = хранилище.загрузить(user.id, тело["file"], автор=user.full_name or user.login)
        else:
            запись = хранилище.сохранить(
                user.id, имя=тело.get("name", ""), описание=тело.get("description", ""),
                шаги=тело.get("steps") or [], общая=bool(тело.get("shared")),
                автор=user.full_name or user.login, ид=тело.get("id") or None)
    except KeyError:
        raise ServiceError("конфигурация не найдена", 404) from None
    except НетДоступа as ошибка:
        raise ServiceError(str(ошибка), 403) from None
    except (ValueError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    _repos(request).audit.log("potok.config", user=user, object_type="potok-config", object_id=запись["ид"],
                              details={"name": запись["имя"], "steps": len(запись["шаги"])})
    return запись


@router.delete("/potok-configs/{config_id}")
def potok_config_delete(request: Request, config_id: str) -> dict[str, Any]:
    from ..potok.konfig import НетДоступа  # noqa: PLC0415
    user = require_user(request)
    try:
        _konfig(request).удалить(config_id, user.id)
    except KeyError:
        raise ServiceError("конфигурация не найдена", 404) from None
    except НетДоступа as ошибка:
        raise ServiceError(str(ошибка), 403) from None
    return {"deleted": config_id}


@router.get("/potok-configs/{config_id}/export")
def potok_config_export(request: Request, config_id: str) -> Response:
    """Файл конфигурации — перенести на другую машину отдела или сохранить у себя."""
    from ..potok.konfig import Конфигурации  # noqa: PLC0415
    user = require_user(request)
    к = _конфигурация(request, user, config_id)
    имя = re.sub(r"[^\w\-. ]", "_", к["имя"])[:60] or "конфигурация"
    return Response(json.dumps(Конфигурации.выгрузка(к), ensure_ascii=False, indent=1),
                    media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="config-{config_id}.json"; '
                             "filename*=UTF-8''" + urllib.parse.quote(имя + ".json")})


#: Проверка шагов — на начале потока: быстро и видно, туда ли идёт обработка.
ПРОБА_БИТ = 4 << 20


@router.post("/potok/{job_id}/try")
def potok_try(request: Request, job_id: str) -> dict[str, Any]:
    """Пробно применить шаги (или конфигурацию) к началу потока этапа: что вышло, без задания."""
    import numpy as np  # noqa: PLC0415

    from ..potok import rastr  # noqa: PLC0415
    from ..potok.bity import в_байты  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    шаги = (_конфигурация(request, user, str(тело["config"]))["шаги"] if тело.get("config")
            else list(тело.get("steps") or []))
    проба = биты[:ПРОБА_БИТ]
    try:
        шаги = rastr.проверить_шаги(шаги)
        итог, описание = rastr.применить(проба, шаги)
    except (ValueError, KeyError, TypeError) as ошибка:
        raise ServiceError(f"шаги не выполнились: {ошибка}", 400) from None
    доля = float(np.mean(итог)) if len(итог) else 0.0
    return {"бит_на_входе": int(len(проба)), "весь_поток": int(len(биты)), "бит_на_выходе": int(len(итог)),
            "описание": описание, "доля_единиц": round(доля, 4),
            "начало": в_байты(итог[:4096]).hex()}


# -- рабочий стол анализа: сетка бит, таблица кадров, журнал массива ------------------------

@router.get("/potok/{job_id}/grid")
def potok_grid(request: Request, job_id: str, stage: int = 0, period: int = 64, shift: int = 0,
               row: int = 0, rows: int = 200, col: int = 0, cols: int = 64, per: int = 1) -> dict[str, Any]:
    """Прямоугольник битового просмотра: строки × столбцы, со сжатием по горизонтали."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    if period < 1:
        raise ServiceError("ширина строки — от 1 бита", 400)
    return rastr.сетка(_биты_задания(request, user, job_id, stage), period, shift, row, rows, col, cols, per)


#: Байты массива для битового просмотра в браузере — за один запрос не больше этого.
СЫРЫЕ_ДО = 64 << 20


@router.get("/potok/{job_id}/raw")
def potok_raw(request: Request, job_id: str, stage: int = 0, offset: int = 0, length: int = 0) -> Response:
    """Байты массива как есть (старший бит первым): битовый просмотр рисует их сам.

    Сдвиг растра, ширина, масштаб, разметка — всё в браузере, без запроса на
    каждое движение. Большой массив берётся кусками (``offset``, ``length``);
    полный размер — в заголовке X-Total-Bytes, точная длина в битах (хвост последнего байта
    бывает не в счёт) — в X-Total-Bits.
    """
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    try:
        файл = _potok(request).файл_бит(job_id, stage)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 409) from None
    размер = файл.stat().st_size
    бит = _potok(request).длина_бит(job_id, stage)
    начало = min(max(0, int(offset)), размер)
    сколько = min(int(length) if length > 0 else СЫРЫЕ_ДО, СЫРЫЕ_ДО, размер - начало)
    with файл.open("rb") as поток:
        поток.seek(начало)
        данные = поток.read(сколько)
    return Response(данные, media_type="application/octet-stream",
                    headers={"X-Total-Bytes": str(размер), "X-Total-Bits": str(бит), "X-Offset": str(начало),
                             "Cache-Control": "private, no-store"})


@router.get("/potok/{job_id}/marks")
def potok_marks(request: Request, job_id: str, stage: int = 0) -> dict[str, Any]:
    """Разметка массива: отрезки и правила — общие для участников сессии."""
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    return {"marks": _potok(request).разметка(job_id, stage)}


@router.put("/potok/{job_id}/marks")
def potok_marks_save(request: Request, job_id: str) -> dict[str, Any]:
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    тело = _body(request)
    try:
        разметка = rastr.проверить_разметку(тело.get("marks") or {})
    except (ValueError, KeyError, TypeError, IndexError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    _potok(request).записать_разметку(job_id, int(тело.get("stage") or 0), разметка)
    return {"marks": разметка}


@router.post("/potok/{job_id}/period-search")
def potok_period_search(request: Request, job_id: str) -> dict[str, Any]:
    """Быстрый поиск периода по синхромаркеру: период, первый бит, вес, сам маркер."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    этап = int(тело.get("stage") or 0)
    _файл_бит_или_400(request, user, job_id, этап)
    задания = _potok(request)
    try:
        параметры = {"от": int(тело.get("from") or 8), "до": int(тело.get("to") or 8192),
                     "шаг": int(тело.get("step") or 1), "глубина_от": int(тело.get("depth_min") or 8),
                     "глубина_до": int(тело.get("depth_max") or 64),
                     "качество": float(тело.get("quality") or 90)}
        нужно = rastr.бит_поиску_периода(параметры["до"], параметры["глубина_до"])
        найдено = задания.запомнить(
            job_id, этап, "поиск_периода:" + json.dumps(параметры, sort_keys=True),
            lambda: rastr.поиск_периода(задания.биты_участка(job_id, этап, 0, нужно), **параметры))
    except (ValueError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    return {"items": найдено}


#: У скольких лучших полиномов каждой степени считать начальную установку аддитивного скремблера.
СКРЕМБЛЕР_УСТАНОВОК = 5


@router.post("/potok/{job_id}/scrambler-search")
def potok_scrambler_search(request: Request, job_id: str) -> dict[str, Any]:
    """Поиск скремблера по одной степени: окно перебирает степени по очереди, с ходом и отменой."""
    from ..potok import skrembler  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    этап = int(тело.get("stage") or 0)
    _файл_бит_или_400(request, user, job_id, этап)
    задания = _potok(request)
    try:
        степень, отводов = int(тело.get("degree") or 0), int(тело.get("taps") or 2)
        первый = max(0, int(тело.get("first") or 0))
        аддитивный = bool(тело.get("additive"))
        # Длина кадра и его начало (из битового просмотра: длина строки и первый бит) —
        # для ПСП со сбросом в каждом кадре.
        кадр = max(0, int(тело.get("frame") or 0))
        начало_кадра = max(0, int(тело.get("frame_start") or 0))

        def посчитать():
            биты = задания.биты_участка(job_id, этап, первый, первый + skrembler.ВЫБОРКА_ПЕРЕБОРА)
            итог = skrembler.перебор_степени(биты, степень, отводов)
            for номер, лучший in enumerate(итог["лучшие"]):
                # Самосинхронизирующийся: регистр — первые биты потока (с первого бита дескремблера).
                лучший["самосинхр"] = "".join(str(int(б)) for б in биты[:степень])
                if аддитивный and номер < СКРЕМБЛЕР_УСТАНОВОК:
                    уст = skrembler.начальная_установка(
                        биты, лучший["отводы"], кадр=кадр, начало_кадра=(начало_кадра - первый) % кадр if кадр else 0)
                    # Места — в битах массива, а не выборки с первого бита.
                    if уст["способ"] == "кадр":
                        уст["место"] = (уст["место"] + первый) % кадр
                        уст["слой"] = уст["слой"].rsplit(" начало ", 1)[0] + f" начало {уст['место']}"
                    else:
                        уст["место"] += первый
                        if " с бита " in уст["слой"]:
                            уст["слой"] = уст["слой"].rsplit(" с бита ", 1)[0] + f" с бита {уст['место']}"
                    лучший["аддитивный"] = уст
            return итог

        return задания.запомнить(
            job_id, этап, f"скремблер:{степень}:{отводов}:{первый}:{int(аддитивный)}:{кадр}:{начало_кадра}", посчитать)
    except (ValueError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None


#: Разборы GFP окна стола: последние несколько (с областями нагрузки — для страниц таблицы и
#: выгрузки кадров в анализатор пакетов без повторного расчёта).
_GFP_ПОМНИТЬ = 3
_gfp_кэш: dict[tuple, Any] = {}
_gfp_замок = threading.Lock()


def _разбор_gfp(request: Request, user, job_id: str, тело: dict[str, Any]):
    """GFP в массиве по параметрам окна: маска (авто/HEX), скремблер, порядок бит, многочлен, участок."""
    from ..potok import gfp  # noqa: PLC0415
    этап = int(тело.get("stage") or 0)
    _файл_бит_или_400(request, user, job_id, этап)
    задания = _potok(request)
    маска_ = str(тело.get("mask") or "авто").strip()
    if маска_ != "авто" and not re.fullmatch(r"(0x)?[0-9A-Fa-f]{8}", маска_):
        raise ServiceError("маска — «авто» или 8 шестнадцатеричных знаков", 400)
    маска = "авто" if маска_ == "авто" else int(маска_[-8:], 16)
    скремблер = str(тело.get("scrambler") or "авто")
    порядок = str(тело.get("order") or "авто")
    многочлен = "авто" if тело.get("poly") == "авто" else "стандарт"
    if скремблер not in ("авто", "да", "нет", "инверсия") or порядок not in ("авто", "старший", "младший"):
        raise ServiceError("скремблер — авто/да/нет/инверсия, порядок — авто/старший/младший", 400)
    первый = max(0, int(тело.get("first") or 0))
    бит = min(gfp.БИТ_ДО, max(4096, int(тело.get("bits") or gfp.БИТ_ДО // 2)))
    файл = задания.файл_бит(job_id, этап)
    ключ = (str(файл), файл.stat().st_mtime_ns, str(маска), скремблер, порядок, многочлен, первый, бит)
    with _gfp_замок:
        if ключ in _gfp_кэш:
            return _gfp_кэш[ключ], этап
    биты = задания.биты_участка(job_id, этап, первый, первый + бит)
    итог = gfp.разобрать(биты, маска=маска, скремблер=скремблер, порядок=порядок, многочлен=многочлен,
                         от_бита=первый)
    with _gfp_замок:
        _gfp_кэш[ключ] = итог
        while len(_gfp_кэш) > _GFP_ПОМНИТЬ:
            _gfp_кэш.pop(next(iter(_gfp_кэш)))
    return итог, этап


@router.post("/potok/{job_id}/gfp")
def potok_gfp(request: Request, job_id: str) -> dict[str, Any]:
    """GFP (G.7041) в массиве: сводка (маска, пустой кадр на линии, счёт) и страница таблицы кадров."""
    from ..potok import gfp  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    try:
        р, _ = _разбор_gfp(request, user, job_id, тело)
        if р is None:
            return {"найдено": False, "причина": "заголовков GFP с верным cHEC подряд не найдено"
                    + ("" if (тело.get("mask") or "авто") == "авто" else " с этой маской")
                    + " ни при одном битовом сдвиге и порядке бит"}
        return {"найдено": True, "сводка": gfp.сводка(р),
                "таблица": gfp.таблица(р, max(0, int(тело.get("offset") or 0)),
                                       min(500, max(1, int(тело.get("limit") or 200))),
                                       пустые=bool(тело.get("idle", False)))}
    except (ValueError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None


@router.post("/potok/{job_id}/gfp/pakety")
def potok_gfp_pakety(request: Request, job_id: str) -> dict[str, Any]:
    """Кадры GFP в анализатор пакетов: клиенты (тип канала по UPI) или кадры GFP целиком (171)."""
    from ..potok import gfp  # noqa: PLC0415
    from ..potok.zadaniya import выгрузка  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    try:
        р, этап = _разбор_gfp(request, user, job_id, тело)
        cid = тело.get("cid")
        cid = None if cid in (None, "") else int(cid)
    except (ValueError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    if р is None:
        raise ServiceError("GFP не найден", 400)
    кадры = gfp.кадры_gfp(р, cid) if тело.get("what") == "кадры" else gfp.клиентские_кадры(р, cid)
    if not кадры:
        raise ServiceError("кадров с данными нет" + (f" в канале CID {cid}" if cid is not None else ""), 400)
    данные, _ = выгрузка(кадры, "кадры")
    состояние = _potok(request).прочитать(job_id)
    имя = (f"{Path(состояние['имя']).stem} — GFP {'кадры' if тело.get('what') == 'кадры' else 'клиенты'}"
           + (f", CID {cid}" if cid is not None else "") + f", массив {этап}.pcap")
    ид = _pakety(request).создать(владелец=user.id, имя=имя, данные=данные, от=f"{job_id}#{этап}")
    return {"id": ид, "кадров": len(кадры), "канал": кадры.канал}


#: Статистическим тестам по умолчанию — миллион бит (рекомендация NIST SP 800-22), самое большее — 16 млн.
СТАТИСТИКА_ПО = 1_000_000
СТАТИСТИКА_ДО = 16_000_000


@router.post("/potok/{job_id}/stats")
def potok_stats(request: Request, job_id: str) -> dict[str, Any]:
    """Тесты NIST SP 800-22 и характеристики ENT: над участком массива или над размеченными битами.

    ``from`` и ``length`` — участок (бит); ``marks`` — взять размеченные биты массива
    (разметка с сервера), из них — участок.
    """
    from ..potok import rastr, statistika_bit  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    этап = int(тело.get("stage") or 0)
    _файл_бит_или_400(request, user, job_id, этап)
    задания = _potok(request)
    try:
        от = max(0, int(тело.get("from") or 0))
        длина = min(СТАТИСТИКА_ДО, max(1, int(тело.get("length") or СТАТИСТИКА_ПО)))
    except (TypeError, ValueError):
        raise ServiceError("участок: from и length — целые числа бит", 400) from None
    по_разметке = bool(тело.get("marks"))
    разметка = задания.разметка(job_id, этап) if по_разметке else None
    if по_разметке and not (разметка["отрезки"] or разметка["правила"]):
        raise ServiceError("у массива нет разметки", 400)

    def посчитать():
        # ENT считает байты участка, упакованные с его первого бита.
        биты = (rastr.по_разметке(задания.биты(job_id, этап), разметка, "взять")[от:от + длина] if по_разметке
                else задания.биты_участка(job_id, этап, от, от + длина))
        return statistika_bit.проверить(биты)

    итог = задания.запомнить(job_id, этап, "статистика:" + json.dumps(
        {"от": от, "длина": длина, "разметка": разметка and {к: разметка[к] for к in ("отрезки", "правила")}},
        sort_keys=True, ensure_ascii=False), посчитать)
    return итог | {"from": от, "marks": по_разметке}


def _отбор_кадров(тело: dict[str, Any]) -> list[dict[str, Any]]:
    отбор = []
    for у in (тело.get("filter") or [])[:16]:
        try:
            if "bit" in у:
                # Поле бит: ширина 1…64 с любого бита кадра.
                отбор.append({"бит": int(у["bit"]), "ширина": int(у["width"]), "значение": int(у["value"]),
                              "младшим": bool(у.get("lsb_first")), "не": bool(у.get("not"))})
                continue
            отбор.append({"место": int(у["place"]), "значение": int(у["value"]),
                          "полубайт": {"hi": "старший", "lo": "младший"}.get(у.get("nibble") or "", ""),
                          "не": bool(у.get("not"))})
        except (KeyError, TypeError, ValueError):
            raise ServiceError("отбор кадров: {place, value, nibble?, not?} или {bit, width, value, lsb_first?, not?}", 400) from None
    return отбор


@router.post("/potok/{job_id}/frames")
def potok_frames(request: Request, job_id: str) -> dict[str, Any]:
    """Таблица кадров: байты строками по периоду, с отбором по байту или полубайту."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    период = int(тело.get("period") or 0)
    if период < 8:
        raise ServiceError("таблица кадров — при ширине строки от 8 бит", 400)
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    try:
        return rastr.кадры_таблицей(биты, период, int(тело.get("shift") or 0), int(тело.get("offset") or 0),
                                    int(тело.get("limit") or 200), _отбор_кадров(тело),
                                    "младший" if тело.get("order") == "lsb" else "старший")
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None


@router.post("/potok/{job_id}/framecol")
def potok_frame_column(request: Request, job_id: str) -> dict[str, Any]:
    """Статистика столбца кадров (1–8 байт): значения, энтропия, счётчик, длина, полубайты, биты."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    try:
        return rastr.столбец_кадров(биты, int(тело.get("period") or 0), int(тело.get("shift") or 0),
                                    int(тело.get("place") or 0), int(тело.get("width") or 1),
                                    _отбор_кадров(тело), "младший" if тело.get("order") == "lsb" else "старший")
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None


@router.post("/potok/{job_id}/framefield")
def potok_frame_field(request: Request, job_id: str) -> dict[str, Any]:
    """Статистика поля кадров любой длины: с бита ``bit`` шириной ``width`` бит (1…64), с отбором;
    ``lsb_first`` — младшим битом вперёд; ``find`` — сколько кадров с этим значением."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    try:
        искать = тело.get("find")
        return rastr.поле_кадров(биты, int(тело.get("period") or 0), int(тело.get("shift") or 0),
                                 int(тело.get("bit") or 0), int(тело.get("width") or 8), _отбор_кадров(тело),
                                 bool(тело.get("lsb_first")), None if искать in (None, "") else int(искать))
    except (TypeError, ValueError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None


@router.get("/potok/{job_id}/journal")
def potok_journal(request: Request, job_id: str) -> dict[str, Any]:
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    return {"journal": _potok(request).журнал_стола(job_id)}


@router.post("/potok/{job_id}/journal")
def potok_journal_add(request: Request, job_id: str) -> dict[str, Any]:
    """Запись в журнал массива: итог операции или заметка аналитика."""
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    тело = _body(request)
    return _potok(request).записать_в_журнал(job_id, int(тело.get("stage") or 0),
                                             {"операция": тело.get("operation", ""), "текст": тело.get("text", ""),
                                              "слой": тело.get("layer", "")})


@router.get("/potok/{job_id}/stage/{stage}/tributary/{number}")
def potok_tributary_file(request: Request, job_id: str, stage: int, number: int) -> Response:
    """Один приток этапа (PDH, SDH): биты — .bin."""
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    try:
        данные, имя = _potok(request).приток(job_id, stage, number)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 404) from None
    файл = re.sub(r"[^\w\-. ]", "_", имя)[:80] or f"приток-{number}"
    return Response(данные, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="tributary-{stage}-{number}.bin"; '
                             "filename*=UTF-8''" + urllib.parse.quote(файл + ".bin")})


@router.post("/potok/{job_id}/tributary")
def potok_tributary_node(request: Request, job_id: str) -> dict[str, Any]:
    """Приток этапа — отдельным узлом дерева: растр, инструменты, свой разбор."""
    user = require_user(request)
    состояние = _задание_или_404(request, user, job_id)
    тело = _body(request)
    этап, номер = int(тело.get("stage") or 0), int(тело.get("number") or 0)
    try:
        данные, имя = _potok(request).приток(job_id, этап, номер)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 404) from None
    ид = _potok(request).создать(владелец=user.id, имя=f"{состояние['имя']} → {имя}"[:200], данные=данные,
                                 профиль=str(тело.get("profile") or "обычно"), от=f"{job_id}#{этап}",
                                 разбирать=bool(тело.get("analyze", True)), происхождение=[f"приток: {имя}"],
                                 сессия=состояние.get("сессия") or "")
    return {"id": ид}


@router.get("/potok/{job_id}/tree")
def potok_tree(request: Request, job_id: str) -> dict[str, Any]:
    """Дерево обработки, в котором стоит разбор: корень, развилки по этапам, производные."""
    user = require_user(request)
    _задание_или_404(request, user, job_id)
    return {"tree": _potok(request).дерево(job_id, user.id, _мои_сессии(request, user))}


#: Сколько веток удаляется одним запросом — с запасом на любую таблицу массивов.
ВЕТОК_ЗА_РАЗ = 1000


@router.post("/potok/delete-many")
def potok_delete_many(request: Request) -> dict[str, Any]:
    """Удалить несколько веток таблицы массивов одним запросом: ``items`` — [{job, stage}].

    Права — на каждую ветку; ветка, ушедшая вместе с выбранной выше (или не видная
    пользователю), пропускается; чужая или с идущим разбором — не удаляется, но и не
    мешает остальным: причина — в ``refused``.
    """
    user = require_user(request)
    пункты = _body(request).get("items")
    if not isinstance(пункты, list) or not 0 < len(пункты) <= ВЕТОК_ЗА_РАЗ:
        raise ServiceError(f"нужен список веток: от 1 до {ВЕТОК_ЗА_РАЗ}", 400)
    разобраны = []
    for п in пункты:
        try:
            ид, этап = str(п["job"]), int(п.get("stage") or 0)
        except (TypeError, KeyError, ValueError):
            raise ServiceError("ветка — это {job, stage}", 400) from None
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", ид) or этап < 0:
            raise ServiceError("ветка — это {job, stage}", 400)
        разобраны.append((ид, этап))
    итог = _potok(request).удалить_ветки(разобраны, user.id, _мои_сессии(request, user),
                                         нельзя=lambda узел: _нельзя_удалить(request, user, узел))
    _repos(request).audit.log("potok.delete", user=user, object_type="potok", object_id=разобраны[0][0],
                              details={"nodes": len(итог["задания"]), "branches": len(разобраны),
                                       "arrays": итог["массивов"]})
    return {"deleted": итог["задания"], "stages": итог["этапы"], "arrays": итог["массивов"],
            "skipped": итог["пропущено"], "refused": итог["отказано"]}


@router.delete("/potok/{job_id}")
def potok_delete(request: Request, job_id: str, stage: int = 0) -> dict[str, Any]:
    """Удалить ветку дерева: узел и всё под ним — вход задания (``stage`` 0) или выход этапа автомата."""
    user = require_user(request)
    _вправе_удалить(request, user, _задание_или_404(request, user, job_id))
    try:
        ветка = _potok(request).удалить_ветку(job_id, stage, user.id, _мои_сессии(request, user),
                                              нельзя=lambda узел: _нельзя_удалить(request, user, узел))
    except KeyError:
        raise ServiceError("у задания нет такого этапа", 404) from None
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 409) from None
    _repos(request).audit.log("potok.delete", user=user, object_type="potok", object_id=job_id,
                              details={"nodes": len(ветка["задания"]), "stage": stage, "arrays": ветка["массивов"]})
    return {"deleted": ветка["задания"], "stages": ветка["этапов"], "arrays": ветка["массивов"]}


@router.post("/potok/{job_id}/rebuild")
def potok_rebuild(request: Request, job_id: str) -> dict[str, Any]:
    """Пересобрать узел с исправленными шагами: убрать, выключить, переставить.

    Производный узел пересобирается из своего исходника (поток родителя),
    разбор — из своего входа с новым списком слоёв «снять». ``replace`` —
    старый узел с ветвью удаляется, новый встаёт на его место под тем же
    родителем.
    """
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    состояние = _задание_или_404(request, user, job_id)
    тело = _body(request)
    задания = _potok(request)
    папка = задания.папка / job_id
    try:
        шаги = rastr.проверить_шаги(list(тело.get("steps") or []))
    except (ValueError, KeyError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    профиль = str(тело.get("profile") or состояние.get("профиль") or "обычно")
    if профиль not in ПРОФИЛИ_РАЗБОРА:
        raise ServiceError("неизвестный профиль разбора", 400)
    основа = состояние["имя"].split(" → ")[0]
    if состояние.get("шаги"):
        описание = [rastr.описать_шаг(ш) for ш in шаги if ш["вкл"]]
        новый = задания.создать(
            владелец=user.id, имя=(f"{основа} → " + ("; ".join(описание) or "копия"))[:200],
            данные=(папка / "исходник.bin").read_bytes(), профиль=профиль,
            от=состояние.get("от") or "", шаги=шаги, разбирать=bool(тело.get("analyze")),
            происхождение=описание, сессия=состояние.get("сессия") or "")
    else:
        if any(ш["вид"] != "слой" for ш in шаги):
            raise ServiceError("у разбора нет маски и разметки — они бывают у производного потока", 400)
        новый = задания.создать(
            владелец=user.id, имя=состояние["имя"], данные=(папка / "вход.bin").read_bytes(),
            профиль=профиль, от=состояние.get("от") or "",
            снять=[ш["слой"] for ш in шаги if ш["вкл"]], символ=состояние.get("символ") or (),
            фм=состояние.get("фм") or (), сессия=состояние.get("сессия") or "")
    if тело.get("replace"):
        _вправе_удалить(request, user, состояние)
        try:
            задания.удалить(job_id, user.id, _мои_сессии(request, user))
        except ValueError as ошибка:
            raise ServiceError(str(ошибка), 409) from None
    return {"id": новый}


# -- сессии: общий стол нескольких файлов и людей -------------------------------------------

def _сессия_или_404(request: Request, user, сессия: str) -> dict[str, Any]:
    try:
        return _sessii(request).для(сессия, user.id)
    except KeyError:
        raise ServiceError("сессия не найдена", 404) from None


def _люди(request: Request, ид: Iterable[int]) -> list[dict[str, Any]]:
    итог = []
    for и in ид:
        человек = _repos(request).users.get(int(и))
        if человек is not None:
            итог.append({"id": человек.id, "login": человек.login,
                         "full_name": short_name(человек.full_name) or человек.login})
    return итог


def _сессия_кратко(request: Request, user, сессия: dict[str, Any], файлы: list[dict[str, Any]]
                   ) -> dict[str, Any]:
    свои = [ф for ф in файлы if ф.get("сессия") == сессия["ид"]]
    владелец = _люди(request, [сессия["владелец"]])
    return {"id": сессия["ид"], "name": сессия["имя"], "owner": сессия["владелец"],
            "owner_name": владелец[0]["full_name"] if владелец else "",
            "mine": сессия["владелец"] == user.id, "members": сессия.get("участники") or [],
            "created": сессия.get("создано"), "changed": сессия.get("изменено"),
            "files": sum(1 for ф in свои if not ф.get("от")), "nodes": len(свои),
            "bytes": sum(int(ф.get("байт") or 0) for ф in свои if not ф.get("от"))}


@router.get("/sessions")
def sessions_list(request: Request) -> dict[str, Any]:
    """Сессии пользователя — свои и те, которыми с ним поделились."""
    user = require_user(request)
    сессии = _sessii(request).список(user.id)
    файлы = _potok(request).список(user.id, [с["ид"] for с in сессии])
    return {"items": [_сессия_кратко(request, user, с, файлы) for с in сессии]}


@router.post("/sessions")
def sessions_create(request: Request) -> dict[str, Any]:
    """Новая сессия — только имя; файлы добавляются потом."""
    user = require_user(request)
    try:
        ид = _sessii(request).создать(владелец=user.id, имя=str(_body(request).get("name") or ""))
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    _repos(request).audit.log("sessions.create", user=user, object_type="session", object_id=ид)
    return {"id": ид}


@router.get("/sessions/{session_id}")
def sessions_get(request: Request, session_id: str) -> dict[str, Any]:
    """Сессия: участники и файлы (корни деревьев обработки) с производными узлами."""
    user = require_user(request)
    сессия = _сессия_или_404(request, user, session_id)
    деревья = _potok(request).деревья_сессии(session_id)

    def узлы(у: dict[str, Any]) -> list[dict[str, Any]]:
        return [у] + [в for д in у["дети"] for в in узлы(д)]

    все = [в for д in деревья for в in узлы(д)]
    return {"session": _сессия_кратко(request, user, сессия, все),
            "people": _люди(request, [сессия["владелец"], *(сессия.get("участники") or [])]),
            "files": [{к: д.get(к) for к in ("ид", "имя", "байт", "состояние", "создано", "владелец",
                                             "разбирать")}
                      | {"узлов": len(узлы(д)) - 1} for д in деревья],
            # Деревья обработки всех файлов — стол сессии открывается одним запросом.
            "trees": деревья}


@router.patch("/sessions/{session_id}")
def sessions_update(request: Request, session_id: str) -> dict[str, Any]:
    """Переименовать сессию и (или) поделиться ею: участники — список id пользователей."""
    user = require_user(request)
    _сессия_или_404(request, user, session_id)
    тело = _body(request)
    участники = тело.get("members")
    if участники is not None:
        if not isinstance(участники, list):
            raise ServiceError("участники — список id пользователей", 400)
        try:
            участники = [int(у) for у in участники]
        except (TypeError, ValueError):
            raise ServiceError("участники — список id пользователей", 400) from None
        неизвестные = [у for у in участники if _repos(request).users.get(у) is None]
        if неизвестные:
            raise ServiceError(f"нет таких пользователей: {неизвестные}", 400)
    try:
        сессия = _sessii(request).изменить(session_id, user.id, имя=тело.get("name"), участники=участники)
    except PermissionError as ошибка:
        raise ServiceError(str(ошибка), 403) from None
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    if участники is not None:
        _repos(request).audit.log("sessions.share", user=user, object_type="session", object_id=session_id,
                                  details={"members": сессия["участники"]})
    return {"ok": True}


@router.post("/sessions/{session_id}/leave")
def sessions_leave(request: Request, session_id: str) -> dict[str, Any]:
    user = require_user(request)
    _сессия_или_404(request, user, session_id)
    try:
        _sessii(request).покинуть(session_id, user.id)
    except PermissionError as ошибка:
        raise ServiceError(str(ошибка), 409) from None
    return {"ok": True}


@router.delete("/sessions/{session_id}")
def sessions_delete(request: Request, session_id: str) -> dict[str, Any]:
    """Удалить сессию со всеми файлами и производными — только владелец."""
    user = require_user(request)
    сессия = _сессия_или_404(request, user, session_id)
    if сессия["владелец"] != user.id:
        raise ServiceError("удалить сессию вправе только её владелец", 403)
    try:
        удалены = _potok(request).удалить_сессию(session_id)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 409) from None
    _sessii(request).удалить(session_id, user.id)
    _repos(request).audit.log("sessions.delete", user=user, object_type="session", object_id=session_id,
                              details={"nodes": len(удалены)})
    return {"deleted": удалены}


@router.post("/sessions/{session_id}/files")
def sessions_add_file(request: Request, session_id: str, file: UploadFile = File(...),
                      start: str = Form(""), length: str = Form(""), sliced: str = Form(""),
                      analyze: str = Form(""), bit_order: str = Form("")) -> dict[str, Any]:
    """Файл в сессию: поток сразу виден битами; обрезка — с какого байта и сколько.

    .Sig — тела пакетов подряд, текст с битами или HEX — сами биты: обрезается
    уже поток. ``sliced`` — браузер сам вырезал кусок сырого файла (не гнать по
    сети лишнее): здесь только записываем, откуда он.

    ``bit_order`` — какой бит байта в файле первый: ``msb``, ``lsb`` (так пишут
    демодуляторы и старые средства отдела) или ``auto`` (по умолчанию: у сырого
    файла — по синхромаркеру цикла, см. ``rastr.порядок_бит``). Поток на сервере
    всегда хранится старшим битом первым — просмотр, поиск и операции видят одно.
    """
    from ..potok import rastr  # noqa: PLC0415
    from ..potok.chtenie import прочитать as прочитать_поток  # noqa: PLC0415
    user = require_user(request)
    _сессия_или_404(request, user, session_id)
    settings = _settings(request)
    name = _safe_name(Path(file.filename or "поток.bin").name) or "поток.bin"
    try:
        начало = max(0, int(start or 0))
        сколько = max(0, int(length or 0))
    except ValueError:
        raise ServiceError("обрезка: начало и длина — целые числа байт", 400) from None
    порядок = (bit_order or "auto").strip().lower()
    if порядок not in ("auto", "msb", "lsb"):
        raise ServiceError("порядок бит в байте: auto, msb или lsb", 400)
    limit = settings.max_upload_mb * 1024 * 1024
    данные = file.file.read(limit + 1)
    if len(данные) > limit:
        raise ServiceError(f"файл больше допустимых {settings.max_upload_mb} МБ", 413)
    if not данные:
        raise ServiceError("файл пуст", 400)
    происхождение = []
    if sliced:
        поток = данные
        происхождение.append(f"файл {name}: байты {начало}–{начало + len(данные) - 1}")
    else:
        разобранный = прочитать_поток(данные=данные, имя=name)
        поток = разобранный.данные
        происхождение.append(f"файл {name}: {разобранный.формат}")
        происхождение += list(разобранный.заметки)[:4]
        if начало or сколько:
            if начало >= len(поток):
                raise ServiceError(f"начало обрезки за концом потока ({len(поток)} байт)", 400)
            поток = поток[начало:начало + сколько] if сколько else поток[начало:]
            происхождение.append(f"обрезка: байты {начало}–{начало + len(поток) - 1} из {len(разобранный.данные)}")
    if not поток:
        raise ServiceError("после чтения файла поток пуст", 400)
    # Порядок бит в байте: сырой файл — как записан (авто — по маркеру цикла); текст с
    # битами и тела .Sig — уже в порядке линии, их разворачивает только явное «lsb».
    сырой = bool(sliced) or разобранный.вид == "bin"
    if порядок == "auto" and сырой:
        определено = rastr.порядок_бит(поток)
        младший = определено["порядок"] == "младший"
        происхождение.append("порядок бит в байте определён: " + определено["причина"])
    else:
        младший = порядок == "lsb"
        if порядок != "auto":
            происхождение.append("порядок бит в байте задан: " + ("младший" if младший else "старший") + " бит байта первым")
    if младший:
        поток = rastr.развернуть_биты(поток)
    ид = _potok(request).создать(владелец=user.id, имя=name, данные=поток, профиль="обычно",
                                 разбирать=analyze in ("1", "true", "да"), происхождение=происхождение,
                                 сессия=session_id)
    _sessii(request).тронуть(session_id)
    _repos(request).audit.log("sessions.file", user=user, object_type="session", object_id=session_id,
                              details={"name": name, "bytes": len(поток), "job": ид})
    return {"id": ид, "bytes": len(поток), "bit_order": "lsb" if младший else "msb",
            "bit_order_note": next((п for п in происхождение if п.startswith("порядок бит")), "")}


def _биты_поиска(request: Request, user, job_id: str, тело: dict[str, Any]):
    from ..potok import rastr  # noqa: PLC0415
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    if тело.get("mask"):
        try:
            биты = rastr.по_маске(биты, тело["mask"])
        except (ValueError, KeyError, TypeError) as ошибка:
            raise ServiceError(str(ошибка), 400) from None
    return биты


@router.post("/potok/{job_id}/search")
def potok_search(request: Request, job_id: str) -> dict[str, Any]:
    """Поиск образца (HEX, текст в кодировке, биты) при любом битовом сдвиге и в инверсии."""
    from ..potok import poisk  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_поиска(request, user, job_id, тело)
    try:
        байты, биты_о = poisk.образец(str(тело.get("pattern") or ""), str(тело.get("kind") or "hex"),
                                      str(тело.get("encoding") or "utf-8"))
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    найдено = poisk.найти_образец(биты, байты, биты_о, любой_сдвиг=тело.get("anyshift", True) is not False,
                                  инверсия=bool(тело.get("inverted")))
    for н in найдено[:300]:
        н["контекст"] = poisk.контекст(биты, н["бит"], н["инверсия"])
    return {"найдено": len(найдено), "бит_образца": int(len(биты_о)), "items": найдено[:300],
            "предел": len(найдено) >= poisk.НАХОДОК_ДО}


@router.post("/potok/{job_id}/files")
def potok_files(request: Request, job_id: str) -> dict[str, Any]:
    """Файлы внутри потока: сигнатура и структура сошлись, длина — где формат позволяет."""
    from ..potok import poisk  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_поиска(request, user, job_id, тело)
    return {"items": poisk.сигнатуры(биты, любой_сдвиг=тело.get("anyshift", True) is not False,
                                     инверсия=bool(тело.get("inverted")))}


@router.get("/potok/{job_id}/carve")
def potok_carve(request: Request, job_id: str, stage: int = 0, bit: int = 0, length: int = 0,
                inv: bool = False, ext: str = "bin") -> Response:
    """Вырезать файл из потока по битовой позиции и длине."""
    from ..potok import poisk  # noqa: PLC0415
    user = require_user(request)
    биты = _биты_задания(request, user, job_id, stage)
    if not 0 <= bit < len(биты):
        raise ServiceError("позиция вне потока", 400)
    данные = poisk.вырезать(биты, bit, max(0, length), inv)
    расширение = re.sub(r"[^0-9a-z]", "", ext.lower())[:8] or "bin"
    return Response(данные, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''"
                             + urllib.parse.quote(f"вырезано-бит-{bit}.{расширение}")})


@router.post("/potok/{job_id}/strings")
def potok_strings(request: Request, job_id: str) -> dict[str, Any]:
    """Текст в потоке (ASCII, UTF-8, CP1251, UTF-16LE), имена файлов с расширениями, адреса."""
    from ..potok import poisk  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_поиска(request, user, job_id, тело)
    наименьшая = max(4, min(64, int(тело.get("min") or 8)))
    итог = poisk.строки(биты, наименьшая=наименьшая,
                        сдвиги=range(8) if тело.get("anyshift") else (0,), инверсия=bool(тело.get("inverted")))
    итог["строки"] = итог["строки"][:1500]
    return итог


@router.post("/potok/{job_id}/stuffing")
def potok_stuffing(request: Request, job_id: str) -> dict[str, Any]:
    """Мультиплекс со стаффингом: каналы управления, позиция возможности и знак — по растру."""
    from ..potok import stafing  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_поиска(request, user, job_id, {**тело, "mask": None})
    try:
        найдено = stafing.найти(биты, int(тело.get("period") or 0), int(тело.get("shift") or 0))
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    return найдено or {"кадров": 0, "группы": []}


@router.post("/potok/{job_id}/ngrams")
def potok_ngrams(request: Request, job_id: str) -> dict[str, Any]:
    """Частые комбинации от 2 до 8 байт и повторяющиеся блоки вокруг них."""
    from ..potok import poisk  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_поиска(request, user, job_id, тело)
    return {"items": poisk.частые(биты, от=int(тело.get("from") or 2), до=int(тело.get("to") or 8),
                                  сдвиги=range(8) if тело.get("anyshift") else (0,))}


def _синхро(биты, тело: dict[str, Any]):
    """Синхрокомбинация из тела запроса: словом или из выделенных столбцов растра."""
    from ..potok import sinhro  # noqa: PLC0415
    столбцы = тело.get("columns")
    if столбцы:
        образец = sinhro.из_столбцов(биты, int(столбцы["период"]), int(столбцы.get("сдвиг", 0)),
                                     list(столбцы.get("позиции") or []))
        if образец is None:
            raise ValueError("в выделенных столбцах нет постоянных бит — это не синхрокомбинация")
    else:
        образец = sinhro.слово(str(тело.get("word") or ""))
    return образец, sinhro.найти(биты, образец, int(тело.get("errors") or 0))


@router.post("/potok/{job_id}/sync")
def potok_sync(request: Request, job_id: str) -> dict[str, Any]:
    """Синхрокомбинация: вхождения (прямые и инверсные), шаг — длина кадра, знакома ли."""
    from ..potok import rastr  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    биты = _биты_задания(request, user, job_id, int(тело.get("stage") or 0))
    try:
        if тело.get("mask"):
            биты = rastr.по_маске(биты, тело["mask"])
        _, найдено = _синхро(биты, тело)
    except (ValueError, KeyError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    найдено["позиции"] = найдено["позиции"][:2000]
    return найдено


# -- матрицы над GF(2): Гаусс, ранг, ядро, систематический вид, параметры кода --------------

@router.post("/potok/matrix")
def potok_matrix_gf2(request: Request) -> dict[str, Any]:
    """Действие над матрицей GF(2) — для анализа кодов на рабочем столе.

    ``op`` — действие (``matricy_gf2.ОПЕРАЦИИ``); исходная — ``A`` (строки «0101») или
    ``source`` {job, stage, period, shift, rows}: кадры массива строками — ``period`` бит с
    бита ``shift``, не больше ``rows`` строк; тогда в ответе ``поток`` — ранг и что он значит,
    и первые строки ``A``. ``B``, ``b``, ``vectors`` — второе, если действию оно нужно.
    """
    from ..potok import matricy_gf2 as мат  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    оп = str(тело.get("op") or "")
    if оп not in мат.ОПЕРАЦИИ:
        raise ServiceError("неизвестное действие с матрицей; есть: " + ", ".join(мат.ОПЕРАЦИИ), 400)
    источник = тело.get("source")
    поток = None
    if источник:
        try:
            job = str(источник["job"])
            этап, длина, сдвиг, строк = (int(источник.get(к) or 0) for к in ("stage", "period", "shift", "rows"))
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            raise ServiceError("источник: {job, stage, period, shift, rows} — массив и целые числа", 400) from None
        _файл_бит_или_400(request, user, job, этап)
        задания = _potok(request)
        всего = задания.длина_бит(job, этап)
        if not 0 <= сдвиг < всего:
            raise ServiceError(f"первый бит — от 0 до {всего - 1}: в массиве {всего} бит", 400)
        # Больше предела строк и столбцов не читается: лишнее всё равно отвергнет проверка размера.
        до = сдвиг + min(длина, мат.МАТРИЦА_ДО) * min(строк, мат.МАТРИЦА_ДО)
    try:
        if источник:
            A = мат.из_потока(задания.биты_участка(job, этап, сдвиг, до), длина, строк)
            поток = мат.о_потоке(A) | {"сдвиг": сдвиг, "бит_в_массиве": всего}
        else:
            A = мат.разобрать(тело.get("A"), "A")
        второе = мат.ВТОРОЕ.get(оп)
        итог = мат.выполнить(оп, A, B=мат.разобрать(тело.get("B"), "B") if второе == "B" else None,
                             b=мат.вектор(тело.get("b"), "b") if второе == "b" else None,
                             векторы=мат.разобрать(тело.get("vectors"), "векторы") if второе == "векторы" else None)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    if поток:
        итог.update(поток=поток, A=мат.строками(A[:мат.ПОКАЗАТЬ_СТРОК]))
    return итог


# -- матрицы LDPC: загружены из стандартов, общие для отдела ------------------------------

@router.get("/potok-matrices")
def potok_matrices(request: Request) -> dict[str, Any]:
    """Загруженные матрицы проверок LDPC (имя, n, k, веса, откуда) и встроенные коды стандартов."""
    from ..potok import ldpc, ldpc_std  # noqa: PLC0415
    require_user(request)
    _potok(request)                     # задаёт каталог матриц
    return {"items": ldpc.список(), "builtin": ldpc_std.список()}


@router.post("/potok-matrices")
def potok_matrix_add(request: Request) -> dict[str, Any]:
    """Загрузить H: alist, базовая матрица сдвигов с Z или таблица адресов с n и k.

    ``punctured`` и ``shortened`` — схема передачи («0-191, 1000-1023»): с ней
    автомат сам пробует матрицу на каждом разбираемом потоке.
    """
    from ..potok import ldpc  # noqa: PLC0415
    user = require_user(request)
    _potok(request)
    тело = _body(request)
    имя = str(тело.get("name") or "").strip()
    текст = str(тело.get("text") or "")
    if len(текст) > 8 * 1024 * 1024:
        raise ServiceError("матрица больше 8 МБ текста", 413)
    try:
        if ldpc.владелец(имя) not in (None, user.id):
            raise ServiceError("матрица с таким именем уже есть — её загрузил другой инженер", 409)
        матрица = ldpc.загрузить(str(тело.get("kind") or ""), текст, Z=int(тело.get("z") or 0),
                                 n=int(тело.get("n") or 0), k=int(тело.get("k") or 0))
        # Схема передачи (выколотые и укороченные позиции) — чтобы автомат пробовал матрицу сам.
        сводка = ldpc.сохранить(имя, матрица, user.id, выколоты=str(тело.get("punctured") or ""),
                                укорочены=str(тело.get("shortened") or ""))
    except (ValueError, TypeError) as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    _repos(request).audit.log("potok.matrix", user=user, object_type="ldpc", object_id=имя,
                              details={"n": сводка["n"], "m": сводка["m"]})
    return {"matrix": сводка}


@router.delete("/potok-matrices/{name}")
def potok_matrix_delete(request: Request, name: str) -> dict[str, Any]:
    from ..potok import ldpc  # noqa: PLC0415
    user = require_user(request)
    _potok(request)
    try:
        хозяин = ldpc.владелец(name)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    if хозяин is None:
        raise ServiceError("матрица не найдена", 404)
    if хозяин != user.id:
        raise ServiceError("удалить матрицу может только тот, кто её загрузил", 403)
    ldpc.удалить(name)
    return {"ok": True}


# -- модуляционный декодер: плоскости (.etl), просмотр, перебор вариантов ------------------

@router.get("/potok-planes")
def potok_planes(request: Request) -> dict[str, Any]:
    """Плоскости модуляционного декодера: встроенные и файлы .etl папки (битый — с ошибкой, список не роняет)."""
    from ..potok import moddekoder  # noqa: PLC0415
    require_user(request)
    _potok(request)                     # задаёт папку плоскостей
    return {"items": moddekoder.список()}


@router.post("/potok-planes")
def potok_plane_add(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """Положить .etl в папку плоскостей: имя очищается, только .etl, до 64 КБ, битый не сохраняется."""
    from ..potok import moddekoder  # noqa: PLC0415
    user = require_user(request)
    _potok(request)
    данные = file.file.read(moddekoder.ETL_ДО + 1)
    if len(данные) > moddekoder.ETL_ДО:
        raise ServiceError(f"файл .etl больше {moddekoder.ETL_ДО // 1024} КБ — это не картинка созвездия", 413)
    try:
        плоскость = moddekoder.сохранить(file.filename or "", данные)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    _repos(request).audit.log("potok.plane", user=user, object_type="ploskost", object_id=плоскость["имя"],
                              details={"M": плоскость["M"], "вид": плоскость["вид"]})
    return {"plane": плоскость}


@router.get("/potok-planes/{name}")
def potok_plane_file(request: Request, name: str) -> Response:
    """Плоскость файлом .etl: из папки — как лежит (и битую — чтобы поправить), встроенную — картинкой."""
    from ..potok import moddekoder  # noqa: PLC0415
    require_user(request)
    _potok(request)
    встроенная = next((с for с in moddekoder.встроенные() if с.имя == name), None)
    if встроенная is not None:
        данные, имя = moddekoder.в_etl(встроенная).encode("utf-8"), f"{name}.etl"
    else:
        путь = moddekoder.файл(name)
        if путь is None:
            raise ServiceError(f"плоскости «{name}» нет ни среди встроенных, ни в папке плоскостей", 404)
        данные, имя = путь.read_bytes(), путь.name
    return Response(данные, media_type="application/octet-stream",
                    headers={"Content-Disposition": _disposition(имя), "X-Content-Type-Options": "nosniff"})


#: Просмотр модуляционного декодера: бит результата — не больше.
МОДДЕКОДЕР_ПРОСМОТР_ДО = 1 << 17
#: «Дек. всех»: секунд на одну часть перебора (окно просит части подряд, показывает ход).
МОДДЕКОДЕР_СРОК = 2.0


def _моддекодер(request: Request, job_id: str, для_перебора: bool = False):
    """Тело запроса окна декодера → (тело, настройки, выборка от начала массива, всего бит) с проверкой доступа.

    ``для_перебора`` — вариант и внешняя таблица не нужны (перебор размечает точки сам), «только_грей» —
    из тела запроса, если есть.
    """
    from ..potok import moddekoder  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    try:
        этап = int(тело.get("stage") or 0)
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("stage — номер этапа", 400) from None
    _файл_бит_или_400(request, user, job_id, этап)
    параметры = тело.get("параметры")
    if для_перебора and isinstance(параметры, dict):
        параметры = {**параметры, "вариант": 0, "таблица": "",
                     "только_грей": тело.get("только_грей", параметры.get("только_грей", True))}
    try:
        н = moddekoder.настройки(параметры)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    задания = _potok(request)
    return тело, н, задания.биты_участка(job_id, этап, 0, moddekoder.ВЫБОРКА_СИМВОЛОВ * н.k), \
        задания.длина_бит(job_id, этап)


@router.post("/potok/{job_id}/moddecoder/preview")
def potok_moddecoder_preview(request: Request, job_id: str) -> dict[str, Any]:
    """Просмотр: ``бит`` бит результата с бита ``с`` выборки (упакованы, base64), мера структуры, таблица и шаг.

    ``с`` — чтобы строки мини-растра окна шли с той же фазы, что и строки просмотра (первый бит по модулю длины).
    """
    import base64  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from ..potok import moddekoder  # noqa: PLC0415
    тело, н, выборка, всего = _моддекодер(request, job_id)
    try:
        показать = min(max(1, int(тело.get("бит") or 4096)), МОДДЕКОДЕР_ПРОСМОТР_ДО)
        с = max(0, int(тело.get("с") or 0))
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("бит и с — сколько бит результата показать и с какого", 400) from None
    try:
        д = moddekoder.декодер(н)
        кадр = moddekoder.кадр(тело.get("кадр"))
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    ряд = д.применить(выборка)
    try:
        вариантов: int | None = moddekoder.число_вариантов(н)
    except ValueError:
        вариантов = None
    показано = ряд[с:с + показать]
    return {"биты": base64.b64encode(np.packbits(показано).tobytes()).decode("ascii"),
            "бит": int(len(показано)), "с": с, "мера": round(moddekoder.мера(ряд, кадр), 3),
            "мера_исходного": round(moddekoder.мера(выборка[:len(ряд)], кадр), 3),
            "таблица": list(д.таблица), "номера": list(д.номера), "метки": list(д.метки),
            "запись": moddekoder.запись(д.таблица or д.метки), "описание": д.описание, "слой": д.слой(),
            "символов": всего // н.k, "хвост": всего % н.k, "вариантов": вариантов}


@router.post("/potok/{job_id}/moddecoder/all")
def potok_moddecoder_all(request: Request, job_id: str) -> dict[str, Any]:
    """«Дек. всех»: варианты с номерами с…по — мера каждого и лучшие; часть — не дольше ``МОДДЕКОДЕР_СРОК``.

    ``кадр`` {длина, начало} — мера ещё и по строкам кода в кадрах (строка просмотра окна).
    """
    from ..potok import moddekoder  # noqa: PLC0415
    тело, н, выборка, _ = _моддекодер(request, job_id, для_перебора=True)
    try:
        с, по = int(тело.get("с") or 1), int(тело.get("по") or 1)
        лучших = min(100, int(тело.get("лучших") or 20))
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("с, по, лучших — номера и число вариантов", 400) from None
    try:
        return moddekoder.перебор(выборка, н, с, по, срок=МОДДЕКОДЕР_СРОК, лучших=лучших,
                                  кадр=moddekoder.кадр(тело.get("кадр")))
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None


# -- поиск блочных турбокодов (ТКБ): каталог режимов, поиск, просмотр, сохранение --------------

#: Поиск ТКБ: секунд на одну часть перебора режимов (окно просит части подряд, показывает ход).
ТКБ_СРОК = 2.0
#: Просмотр ТКБ: сколько блоков декодировать, сколько бит данных отдать.
ТКБ_ПРОСМОТР_БЛОКОВ = 64
ТКБ_ПРОСМОТР_БИТ = 1 << 16


@router.get("/potok-tkb/modes")
def potok_tkb_modes(request: Request) -> dict[str, Any]:
    """Каталог режимов ТКБ: коды, размеры, скорость, источник (файл и страница) и отметка достоверности."""
    from ..potok import tpc_rezhimy  # noqa: PLC0415
    require_user(request)
    return {"items": [tpc_rezhimy.описание(р) for р in tpc_rezhimy.КАТАЛОГ]}


def _ткб_этап(request: Request, job_id: str) -> tuple[dict[str, Any], Any, int]:
    """Тело запроса окна ТКБ → (тело, выборка бит этапа, этап) с проверкой доступа."""
    from ..potok import tpc_rezhimy  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    try:
        этап = int(тело.get("stage") or 0)
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("stage — номер этапа", 400) from None
    _файл_бит_или_400(request, user, job_id, этап)
    return тело, _potok(request).биты_участка(job_id, этап, 0, tpc_rezhimy.ВЫБОРКА), этап


@router.post("/potok/{job_id}/tkb/search")
def potok_tkb_search(request: Request, job_id: str) -> dict[str, Any]:
    """Поиск блочных турбокодов: режимы каталога с номерами с…по (часть — не дольше ``ТКБ_СРОК``).

    ``синхрослово`` (0/1) или ``кадр`` — длина кадра; без них длина кадра ищется по циклу
    потока (при первой части, ``с`` = 0; дальше окно присылает найденные ``кадры`` обратно).
    ``слепой`` — вместо каталога слепой поиск (последняя часть окна). ``метки`` — синхрометки
    (AHA4501, US7085987): при первой части ищутся по потоку (если не ``без_меток``) и возвращаются,
    окно присылает их обратно; режимы пробуются и на ряде без меток.
    """
    from ..potok import razbor, tpc, tpc_rezhimy  # noqa: PLC0415
    тело, ряд, _ = _ткб_этап(request, job_id)
    try:
        с = int(тело.get("с") or 0)
        по = int(тело.get("по") or 10 ** 6)
        кадры = [int(к) for к in (тело.get("кадры") or []) if int(к) > 0]
        кадр = int(тело.get("кадр") or 0)
    except (TypeError, ValueError, OverflowError):
        raise ServiceError("с, по, кадр, кадры — целые числа", 400) from None
    синхрослово = str(тело.get("синхрослово") or "")
    if any(ч not in "01 " for ч in синхрослово):
        raise ServiceError("синхрослово — нули и единицы", 400)
    имена = тело.get("режимы") or []
    if not isinstance(имена, list):
        raise ServiceError("режимы — список имён режимов каталога", 400)
    try:
        режимы = [tpc_rezhimy.режим(str(и)) for и in имена] or list(tpc_rezhimy.КАТАЛОГ)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    по = min(по, len(режимы))
    if кадр:
        кадры = sorted(set(кадры) | {кадр})
    elif с == 0 and not кадры:
        кадры = tpc_rezhimy.длины_кадра(ряд, синхрослово)
    метки = None
    м = тело.get("метки")
    if isinstance(м, dict):
        try:
            метки = tpc_rezhimy.Метки(int(м["период"]), int(м["длина"]), int(м["начало"]), str(м.get("слово") or ""),
                                      int(м.get("через") or 0), int(м.get("инв") or 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ServiceError("метки — {период, длина, начало[, через, инв]} целыми", 400) from None
        if not 0 < метки.длина < метки.период:
            raise ServiceError("метки: длина — от 1 до периода − 1", 400)
    elif с == 0 and not тело.get("без_меток") and not тело.get("слепой"):
        метки = tpc_rezhimy.найти_метки(ряд, tpc_rezhimy.периоды_меток(ряд, кадры))
    if тело.get("слепой"):
        варианты = []
        try:
            if кадры:
                данные, находка = tpc.снять_в_кадрах(ряд, razbor.начала_кадров(ряд, кадры[0]), кадры[0])
                находка.свойства["слой"] = f"ткб кадр {кадры[0]}"
            else:
                данные, находка = tpc.снять(ряд)
                находка.свойства["слой"] = (f"ткб строка {находка.свойства['строка']} начало "
                                            f"{находка.свойства['начало']} столбец {находка.свойства['столбец']} "
                                            f"блок {находка.свойства['блок']}"
                                            + (f" глубина {находка.свойства['глубина']} плоскость "
                                               f"{находка.свойства['плоскость']}" if находка.свойства.get("глубина")
                                               else " двумерный"))
            if находка.уверенность >= tpc_rezhimy.НАЙДЕН_ОТ:
                в = tpc_rezhimy.Вариант(tpc_rezhimy.КАТАЛОГ[0], int(находка.свойства.get("кадр") or 0),
                                        int(находка.свойства.get("начало_блока") or 0), float(находка.уверенность),
                                        чисто=float(находка.уверенность), слепой=True, находка=находка)
                варианты.append(в.в_словарь())
        except ValueError:
            pass
        return {"найдено": варианты, "по": по, "всего": len(режимы), "кадры": кадры}
    итог = tpc_rezhimy.поиск(ряд, режимы=режимы, кадры=кадры, с=с, по=по, срок=ТКБ_СРОК, метки=метки)
    return {"найдено": [в.в_словарь() for в in tpc_rezhimy.упорядочить(итог["найдено"])],
            "по": итог["по"], "всего": итог["всего"], "кадры": кадры,
            "метки": метки.в_словарь() if метки is not None else None}


def _ткб_снять(request: Request, job_id: str, слой: str, весь: bool):
    """Снять ТКБ слоем окна (``ткб режим …`` или слепой ``ткб …``): (данные, находка, всего бит этапа)."""
    from ..potok import razbor, tpc_rezhimy  # noqa: PLC0415
    тело, ряд, этап = _ткб_этап(request, job_id)
    if not слой.lower().startswith("ткб"):
        raise ServiceError("слой ТКБ начинается со слова «ткб»", 400)
    if весь:
        ряд = _potok(request).биты_участка(job_id, этап, 0, None)
    else:
        ряд = ряд[:max(1, int(тело.get("бит") or tpc_rezhimy.ВЫБОРКА))]
    try:
        данные, находка = razbor.снять_вручную(ряд, слой)
    except ValueError as ошибка:
        raise ServiceError(str(ошибка), 400) from None
    return данные, находка, ряд


@router.post("/potok/{job_id}/tkb/preview")
def potok_tkb_preview(request: Request, job_id: str) -> dict[str, Any]:
    """Просмотр варианта: подробности снятия, начало данных (base64) и первый блок с разметкой мест."""
    import base64  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from ..potok import tpc_rezhimy  # noqa: PLC0415
    тело = _body(request)
    слой = str(тело.get("слой") or "").strip()
    данные, находка, ряд = _ткб_снять(request, job_id, слой, False)
    показать = min(len(данные), ТКБ_ПРОСМОТР_БИТ)
    блок: dict[str, Any] | None = None
    м = re.search(r"режим\s+(\S+)", слой, re.IGNORECASE)
    if м:
        # Слой снят — режим в каталоге есть; раскладка — с укорочением B, подобранным по кадру.
        г = tpc_rezhimy.геометрия(tpc_rezhimy.режим(м.group(1)), бит_укор=(находка.свойства or {}).get("бит_укор"))
        блок = {"форма": list(г.форма), "метки": base64.b64encode(tpc_rezhimy.метки_мест(г).tobytes()).decode("ascii")}
    # Первая строка подробностей слоя ТКБ — что снято и мера (находка слоя — «снято по указанию»).
    return {"что": находка.подробно[0] if находка.подробно else находка.что, "мера": находка.мера,
            "уверенность": находка.уверенность, "подробно": находка.подробно, "биты": base64.b64encode(np.packbits(данные[:показать]).tobytes()).decode("ascii"),
            "бит": int(показать), "данных": int(len(данные)), "блок": блок,
            "свойства": {к: v for к, v in (находка.свойства or {}).items() if isinstance(v, (int, float, str))}}


@router.post("/potok/{job_id}/tkb/save")
def potok_tkb_save(request: Request, job_id: str) -> Response:
    """«Сохранить REC»: данные ТКБ снятого варианта по всему этапу — файлом (биты упакованы, старший первым)."""
    import numpy as np  # noqa: PLC0415
    тело = _body(request)
    слой = str(тело.get("слой") or "").strip()
    данные, находка, _ = _ткб_снять(request, job_id, слой, True)
    имя = re.sub(r"[^\w.-]+", "_", str((находка.свойства or {}).get("режим") or "tkb")).strip("_") or "tkb"
    return Response(np.packbits(данные).tobytes(), media_type="application/octet-stream",
                    headers={"Content-Disposition": _disposition(f"{имя}.rec"), "X-Content-Type-Options": "nosniff",
                             "X-Bits": str(len(данные))})


def _приметы_этапа(request: Request, user, job_id: str, stage: int):
    """Состояние, этап с битовым потоком (этот или ближайший ранее), приметы и подсказки.

    После кадров и пакетов битового потока нет — приметы тогда берутся с
    ближайшего этапа выше, где он есть: там и встал разбор битов.
    """
    from ..potok import podskazki  # noqa: PLC0415
    состояние = _задание_или_404(request, user, job_id)
    этап = max(0, min(int(stage), len(состояние.get("этапы") or [])))
    for номер in range(этап, -1, -1):
        try:
            биты = _potok(request).биты(job_id, номер)
        except (ValueError, OSError):
            continue
        приметы = podskazki.приметы(биты)
        return состояние, номер, приметы, podskazki.подсказки(состояние, номер, приметы)
    raise ServiceError("у задания нет битового потока", 400)


@router.get("/potok/{job_id}/hints")
def potok_hints(request: Request, job_id: str, stage: int = 0) -> dict[str, Any]:
    """Что делать, когда разбор встал: приметы потока и подсказки с действиями."""
    user = require_user(request)
    _, этап, приметы, подсказки = _приметы_этапа(request, user, job_id, stage)
    return {"stage": этап, "signs": приметы, "items": подсказки}


@router.post("/potok/{job_id}/ask")
def potok_ask(request: Request, job_id: str) -> dict[str, Any]:
    """Разговор с помощником о разборе: ход разбора и приметы — вложением, вопрос — черновиком.

    Помощник знает теорию из библиотеки; анализатор знает поток. Вложение
    соединяет одно с другим: помощник ищет по библиотеке с учётом того, что
    уже найдено, отвергнуто и как поток выглядит там, где разбор встал.
    Вопрос не отправляется сам — инженер его правит и спрашивает.
    """
    from ..potok import podskazki  # noqa: PLC0415
    user = require_user(request)
    тело = _body(request)
    состояние, этап, приметы, подсказки = _приметы_этапа(
        request, user, job_id, int(тело.get("stage") or 0))
    текст = podskazki.контекст(состояние, этап, приметы, подсказки)
    вопрос = podskazki.вопрос(состояние, этап)
    if тело.get("topic") == "этап":
        # Вопрос об одном этапе — о том, что просили (у кадров и пакетов приметы
        # берутся с ближайшего битового этапа выше, а вопрос — о самих кадрах).
        номер = int(тело.get("stage") or 0)
        вопрос = podskazki.вопрос_об_этапе(состояние, номер)
        целиком = podskazki.этап_целиком(состояние, номер)
        if целиком:
            текст += "\n\n" + целиком
    if тело.get("topic") == "ldpc":
        from ..potok import ldpc  # noqa: PLC0415
        вопрос = podskazki.вопрос_о_ldpc(состояние, этап, приметы)
        загружено = ldpc.список()
        текст += "\n\nЗагруженные матрицы LDPC: " + ("; ".join(
            f"{м['имя']} ({м['n']}, {м['k']}), {м['откуда']}" for м in загружено) or "нет")
    if тело.get("word") or тело.get("columns"):
        # Вопрос о синхрокомбинации: по ней определяют систему и строение кадра.
        from ..potok import sinhro  # noqa: PLC0415
        try:
            _, найдено = _синхро(_potok(request).биты(job_id, этап), тело)
        except (ValueError, KeyError, TypeError) as ошибка:
            raise ServiceError(str(ошибка), 400) from None
        текст += "\n\nСинхрокомбинация:\n" + sinhro.описать(найдено)
        вопрос = podskazki.вопрос_о_синхро(найдено)
    assistant = _assistant(request)
    chat = assistant.create_chat(user, title=f"Разбор потока: {состояние['имя']}"[:120],
                                 domain="", case_ref=None)
    repos = _repos(request)
    имя = f"разбор-потока-{job_id}-этап-{этап}.txt"
    repos.chats.add_attachment(chat.id, имя, "stream", size=len(текст.encode("utf-8")),
                               text=текст, note="ход разбора и приметы потока из анализатора")
    repos.audit.log("potok.ask", user=user, object_type="potok", object_id=job_id,
                    details={"chat": chat.id, "stage": этап})
    return {"chat": chat.to_dict(), "question": вопрос}


@router.delete("/chats/{chat_id}/attachments/{attachment_id}")
def detach_from_chat(request: Request, chat_id: int, attachment_id: int) -> dict[str, Any]:
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


#: Цифровые потоки: сырой поток (.bin, .dat, .raw), пакеты с двухбайтовой
#: длиной (.sig), биты или шестнадцатеричный дамп текстом (.bits, .hex).
#: Модель читать их не может — ни по объёму, ни по сути: вместо потока ей
#: уходит отчёт анализатора (reportgen.potok) — код, скремблер, цикл,
#: каналы, HDLC, IP — с мерами уверенности.
STREAM_ATTACH = {".bin", ".sig", ".dpo", ".dat", ".raw", ".bits", ".hex", ".pcap", ".cap"}


#: Расширения, текст в которых берётся распознаванием, а не чтением.
OCR_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")


def _looks_like_mush(text: str) -> bool:
    """Похож ли распознанный текст на кашу, а не на слова.

    Снимок экрана с телефона, фотография листа под углом, печать по клетчатой
    бумаге — распознавание выдаёт по такому набор обрывков вроде «Ha 4Ta 6ap».
    Класть это в поиск нельзя: письмо начинает находиться по случайным буквам,
    а настоящее — тонуть. Считаем по двум признакам сразу:

    * доля букв среди знаков — в словах она высокая, в каше её съедают
      мусорные символы;
    * доля слов длиннее трёх букв — распознанный шум рассыпается на огрызки
      по одной-две буквы, связный текст — нет.

    Порог намеренно мягкий: лучше принять сомнительную страницу, чем выкинуть
    настоящую. Отсекается только явная каша.
    """
    stripped = text.strip()
    if len(stripped) < 40:
        return False          # коротко — судить не по чему, пусть остаётся
    letters = sum(1 for ch in stripped if ch.isalpha())
    if letters / len(stripped) < 0.55:
        return True
    words = [word for word in stripped.split() if any(ch.isalpha() for ch in word)]
    if len(words) < 8:
        return False
    long_words = sum(1 for word in words if len(word) > 3)
    return long_words / len(words) < 0.35


def _extract_attachment(path: Path, name: str = "", *, поток: bool = True) -> tuple[str, str]:
    """Текст файла и, если что-то пошло не так, объяснение по-русски.

    ``name`` — имя, под которым файл прислали: на диске он лежит под
    служебным, а в отчёте анализатора должно стоять то, что инженер узнает.
    ``поток=False`` — не звать анализатор потоков: сданный отчёт — документ,
    а не запись с объекта, и «разбор потока» вместо его текста был бы ложью.
    """
    suffix = path.suffix.lower()
    if поток and suffix in STREAM_ATTACH:
        try:
            from ..potok import разобрать  # noqa: PLC0415
            разбор = разобрать(path, имя=name or path.name)
        except Exception as error:      # noqa: BLE001 — вопрос важнее вложения
            return "", f"поток разобрать не удалось: {error}"
        return разбор.отчёт(), (f"разобран анализатором потоков за "
                               f"{разбор.секунд:.0f} с: найдено уровней — "
                               f"{len(разбор.находки)}")
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

    # Распознанное с картинки проверяем на кашу. Обрывки вроде «Ha 4Ta 6ap»
    # в поиске хуже пустоты: письмо начинает находиться по случайным буквам,
    # а настоящее — тонуть. Сам файл при этом на месте, его открывают глазами.
    if suffix in OCR_SUFFIXES and _looks_like_mush(converted.text):
        return "", ("распознать текст с изображения не удалось — по словам из "
                    "него письмо не найдётся; сам файл на месте")
    return converted.text, note


# ------------------------------------------------------------ военнослужащие --

#: Что роль позволяет делать — показывается прямо в форме, чтобы не гадать.
def _user_public(user) -> dict[str, Any]:
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
def list_users(request: Request) -> dict[str, Any]:
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
                "title": role_title_of(role),
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
def staff(request: Request) -> dict[str, Any]:
    """Список военнослужащих для выбора исполнителя.

    Отдельно от /api/users: тот доступен только администратору и отдаёт
    полную запись, а назначить исполнителя письма вправе любой военнослужащий —
    в том числе взять письмо на себя. Здесь только то, что нужно списку.
    """
    require_user(request)
    return {
        "items": [
            {
                "id": user.id,
                "login": user.login,
                "full_name": short_name(user.full_name) or user.login,
                "role": user.role,
                "role_title": user.role_title,
                "department": user.department,
                "team": user.team,
                # Как человека найти. Не личные сведения: телефон и кабинет
                # в отделе и так знают, а искать их по бумажке — терять время.
                "phone_mobile": user.phone_mobile,
                "phone_open": user.phone_open,
                "phone_secure": user.phone_secure,
                "room": user.room,
                "last_seen_at": user.last_seen_at,
            }
            for user in _repos(request).users.list_all(active_only=True, staff_only=True)
        ]
    }


@router.post("/users")
def create_user(request: Request) -> dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    payload = _body(request)

    login = str(payload.get("login", "")).strip().lower()
    _check_login(login)
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

    full_name = _check_full_name(payload.get("full_name", ""))
    user = repos.users.create(
        login, password, full_name=full_name, role=role,
        department=str(payload.get("department", "")).strip(),
        team=str(payload.get("team", "")).strip(),
    )
    repos.audit.log("user.create", user=admin, object_type="user", object_id=user.login,
                    details={"role": role})
    return {"user": _user_public(user)}


@router.patch("/users/{user_id}")
def update_user(request: Request, user_id: int) -> dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("военнослужащий не найден", 404)
    payload = _body(request)

    role = payload.get("role")
    if role is not None and role != user.role:
        if role not in ROLES:
            raise ServiceError(f"неизвестная должность '{role}'", 400)
        if not _may_manage(admin, user):
            raise ServiceError("нельзя менять должность военнослужащему своего уровня или выше", 403)
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

    # Правка ФИО проходит ту же проверку, что и заведение: иначе полное имя
    # можно было бы стереть до фамилии сразу после того, как его завели.
    new_name = (_check_full_name(payload["full_name"])
                if "full_name" in payload else None)
    updated = repos.users.update(
        user_id,
        full_name=new_name,
        role=None if role is None else str(role),
        department=_opt_str(payload, "department"),
        team=_opt_str(payload, "team"),
    )
    repos.audit.log("user.update", user=admin, object_type="user", object_id=user.login,
                    details={k: payload.get(k) for k in ("role", "full_name", "department", "team")
                             if k in payload})
    return {"user": _user_public(updated)}


@router.get("/users/pending")
def pending_users(request: Request) -> dict[str, Any]:
    """Заявки, ждущие одобрения."""
    require_admin(request)
    items = _repos(request).users.pending()
    return {"items": [_user_public(item) for item in items]}


@router.post("/users/{user_id}/approve")
def approve_user(request: Request, user_id: int) -> dict[str, Any]:
    """Открыть доступ по заявке и назначить должность.

    Одобряет создатель системы, начальник отдела, его заместитель или
    начальник группы — тот же круг, что заводит военнослужащих руками. Должность
    назначает он же: заявка приходит с самой младшей, и назначить выше
    собственной по-прежнему нельзя.
    """
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("заявка не найдена", 404)
    if user.approved:
        raise ServiceError("эта учётная запись уже одобрена", 409)

    payload = _body(request)
    role = str(payload.get("role", user.role) or user.role)
    if role not in ROLES:
        raise ServiceError(f"неизвестная должность '{role}'", 400)
    if role == "owner":
        raise ServiceError("создатель системы в единственном числе", 403)
    if not admin.is_owner and ROLE_RANK.get(role, 0) >= admin.rank:
        raise ServiceError("нельзя назначить должность выше собственной", 403)

    repos.users.update(user_id, role=role,
                       team=_opt_str(payload, "team"),
                       department=_opt_str(payload, "department"))
    approved = repos.users.approve(user_id, admin.id)
    repos.audit.log("user.approve", user=admin, object_type="user",
                    object_id=user.login, details={"role": role})
    _notify(request, user_id, "user.approved",
            "Доступ открыт",
            f"Заявку одобрил {short_name(admin.full_name) or admin.login}. "
            f"Должность: {role_title_of(role)}.")
    return {"user": _user_public(approved)}


@router.post("/users/{user_id}/reject")
def reject_user(request: Request, user_id: int) -> dict[str, Any]:
    """Отклонить заявку: запись убирается совсем.

    Отклонённая заявка — это не военнослужащий; держать её в списке значит копить
    мусор, по которому никто не работает. Отключение — для другого случая:
    человек был и ушёл.
    """
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("заявка не найдена", 404)
    if user.approved:
        raise ServiceError(
            "эта учётная запись уже одобрена — её отключают, а не отклоняют", 409)
    repos.users.delete(user_id)
    repos.audit.log("user.reject", user=admin, object_type="user",
                    object_id=user.login)
    return {"ok": True}


@router.post("/users/{user_id}/password")
def reset_user_password(request: Request, user_id: int) -> dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("военнослужащий не найден", 404)
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
def set_user_active(request: Request, user_id: int) -> dict[str, Any]:
    admin = require_admin(request)
    repos = _repos(request)
    user = repos.users.get(user_id)
    if user is None:
        raise ServiceError("военнослужащий не найден", 404)
    active = bool(_body(request).get("active", True))
    if not active and user.id == admin.id:
        raise ServiceError("нельзя отключить самого себя", 409)
    # Старшинство проверяем в обе стороны. Раньше — только при отключении, и
    # начальник группы возвращал доступ отключённому начальнику отдела: чужая
    # учётная запись старшего по должности не его дело ни в ту, ни в другую
    # сторону.
    if not _may_manage(admin, user):
        raise ServiceError(
            "недостаточно прав: этот военнослужащий не младше вас по должности", 403
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


def _roster_bounds(payload: dict[str, Any]) -> tuple[str, str]:
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
def roster(request: Request, date_from: str = "", days: int = 7) -> dict[str, Any]:
    """Расход отдела за промежуток: сетка «военнослужащий × день».

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
    for person in repos.users.list_all(active_only=True, staff_only=True):
        staff.append({
            "id": person.id,
            "full_name": short_name(person.full_name) or person.login,
            "role": person.role,
            "role_title": person.role_title,
            "team": person.team,
            # Расход отвечает «где человек»; телефон и кабинет — вторая
            # половина того же вопроса, и держать их в другом разделе значит
            # заставлять ходить туда-обратно.
            "phone_mobile": person.phone_mobile,
            "phone_open": person.phone_open,
            "phone_secure": person.phone_secure,
            "room": person.room,
            "can_edit": _may_edit_roster(actor, person.id),
            "is_me": person.id == actor.id,
        })

    # Раскладка по дням: одна запись покрывает несколько суток, а сетке нужна
    # клетка. Ключ — «id военнослужащего|день», чтобы браузер брал клетку прямо.
    cells: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        for day in days_list:
            if item.date_from <= day <= item.date_to:
                cells.setdefault(f"{item.user_id}|{day}", []).append(item.to_dict())

    # Дни, отмеченные на весь отдел: учения, собрание, общие работы. Кладём
    # их по дням той же раскладкой, что и клетки: сетке нужен готовый ответ
    # на «что сегодня у отдела», а не список промежутков.
    marked = repos.department_days.in_period(start, finish)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for item in marked:
        for day in days_list:
            if item.date_from <= day <= item.date_to:
                by_day.setdefault(day, []).append(item.to_dict())

    return {
        "date_from": start,
        "date_to": finish,
        "today": _today(),
        "days": days_list,
        "staff": staff,
        "cells": cells,
        "items": [item.to_dict() for item in records],
        "department_days": by_day,
        "can_mark_days": bool(actor.is_admin),
        "day_kinds": [{"id": kind, "title": DEPARTMENT_DAY_TITLES[kind]}
                      for kind in DEPARTMENT_DAY_KINDS],
        "kinds": [
            {"id": kind, "title": ABSENCE_TITLES[kind], "present": kind in PRESENT_KINDS}
            for kind in ABSENCE_KINDS
        ],
    }


@router.post("/roster/days")
def mark_department_day(request: Request) -> dict[str, Any]:
    """Отметить день на весь отдел: общие работы, занятия, собрание.

    Это не отсутствие: отсутствие про человека, а такой день про сам день.
    Ставит начальство — день касается всех, и заводить его каждому по своему
    усмотрению значит спорить о том, что у отдела в четверг.
    """
    actor = require_admin(request)
    repos = _repos(request)
    payload = _body(request)
    kind = str(payload.get("kind") or "work").strip()
    if kind not in DEPARTMENT_DAY_KINDS:
        known = ", ".join(DEPARTMENT_DAY_KINDS)
        raise ServiceError(f"неизвестный вид дня '{kind}' (можно: {known})", 400)
    start = _date_or_empty(payload.get("date_from", ""), "date_from") or _today()
    finish = _date_or_empty(payload.get("date_to", ""), "date_to") or start
    if finish < start:
        raise ServiceError("конец промежутка раньше начала", 400)
    title = _card_line(payload.get("title", ""), "note")
    note = _card_line(payload.get("note", ""), "note")

    item = repos.department_days.add(kind, start, finish, title=title, note=note,
                                     created_by=actor.id)
    repos.audit.log("roster.day", user=actor, object_type="department_day",
                    object_id=str(item.id),
                    details={"kind": kind, "from": start, "to": finish})
    return {"day": item.to_dict()}


@router.delete("/roster/days/{day_id}")
def unmark_department_day(request: Request, day_id: int) -> dict[str, Any]:
    actor = require_admin(request)
    repos = _repos(request)
    item = repos.department_days.get(day_id)
    if item is None:
        raise ServiceError("отметка не найдена", 404)
    repos.department_days.delete(day_id)
    repos.audit.log("roster.day.remove", user=actor,
                    object_type="department_day", object_id=str(day_id),
                    details={"kind": item.kind, "from": item.date_from})
    return {"ok": True}


@router.get("/roster/day")
def roster_day(request: Request, date: str = "") -> dict[str, Any]:
    """Расход на день: кто где, по видам, плюс не отмеченные.

    Это то, что начальник читает вслух на разводе, поэтому список полный:
    отдел минус все отмеченные и есть те, о ком расход молчит.
    """
    require_user(request)
    repos = _repos(request)
    day = _date_or_empty(date, "date") or _today()
    records = repos.absences.on_date(day)

    marked: dict[int, Any] = {}
    for item in records:
        # Отметок на один день может оказаться две (правили и не убрали
        # старую). Берём ту, что заведена позже: она и есть свежая правда.
        current = marked.get(item.user_id)
        if current is None or item.id > current.id:
            marked[item.user_id] = item

    # Отдаём человека, а не запись расхода: экран показывает фамилии и по
    # щелчку открывает карточку военнослужащего, а id записи для этого не годится.
    groups: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ABSENCE_KINDS}
    for item in sorted(marked.values(), key=lambda row: row.full_name):
        groups[item.kind].append({
            "id": item.user_id,
            "full_name": short_name(item.full_name),
            "role": item.role,
            "role_title": role_title_of(item.role),
            "team": item.team,
            "place": item.place,
            "note": item.note,
            "date_to": item.date_to,
        })

    # Кто себя не отметил — тот на месте. Это положение по умолчанию, а не
    # неизвестность: человек приходит на службу, и отмечаются в расходе как
    # раз отклонения от этого. Иначе отдел, где все на местах, выглядел бы
    # ненаписанным расходом, и начальник каждое утро гонялся бы за отметками
    # от тех, у кого ничего не менялось.
    unmarked = [
        {"id": person.id, "full_name": short_name(person.full_name) or person.login,
         "role": person.role, "role_title": person.role_title, "team": person.team}
        for person in repos.users.list_all(active_only=True, staff_only=True)
        if person.id not in marked
    ]
    on_place = sum(len(groups[kind]) for kind in PRESENT_KINDS)
    return {
        "date": day,
        "groups": [
            {"id": kind, "title": ABSENCE_TITLES[kind],
             "present": kind in PRESENT_KINDS, "people": groups[kind]}
            for kind in ABSENCE_KINDS
        ],
        "unmarked": unmarked,
        "total": len(unmarked) + len(marked),
        # На месте — отмеченные дежурством и работами плюс все, кто себя не
        # отмечал вовсе.
        "present": on_place + len(unmarked),
        "marked_present": on_place,
        "away": len(marked) - on_place,
        "marked": len(marked),
    }


@router.get("/absences")
def list_absences(request: Request, date_from: str = "", date_to: str = "") -> dict[str, Any]:
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
def add_absence(request: Request) -> dict[str, Any]:
    """Отметить себя (или подчинённого) в расходе."""
    actor = require_editor(request)
    repos = _repos(request)
    payload = _body(request)

    user_id = int(payload.get("user_id") or 0) or actor.id
    user = repos.users.get(user_id)
    if user is None or not user.active:
        raise ServiceError("военнослужащий не найден", 404)
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
def update_absence(request: Request, absence_id: int) -> dict[str, Any]:
    """Поправить свою запись расхода: планы меняются чаще, чем расход пишут."""
    actor = require_editor(request)
    repos = _repos(request)
    item = repos.absences.get(absence_id)
    if item is None:
        raise ServiceError("запись не найдена", 404)
    if not _may_edit_roster(actor, item.user_id):
        raise ServiceError("недостаточно прав: чужой расход ведёт начальник", 403)

    payload = _body(request)
    fields: dict[str, Any] = {}
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
            raise ServiceError("на эти дни у военнослужащего уже есть другая отметка", 409)
    if "place" in payload:
        fields["place"] = str(payload["place"] or "").strip()[:120]
    if "note" in payload:
        fields["note"] = str(payload["note"] or "").strip()[:300]

    updated = repos.absences.update(absence_id, **fields)
    repos.audit.log("absence.update", user=actor, object_type="user",
                    object_id=str(item.user_id), details=fields)
    return {"absence": updated.to_dict() if updated else None}


@router.delete("/absences/{absence_id}")
def delete_absence(request: Request, absence_id: int) -> dict[str, Any]:
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


# ------------------------------------------------------- уведомления ----

def _notify(request: Request, user_id: int | None, kind: str, title: str,
            body: str = "", link: str = "", from_id: int | None = None) -> None:
    """Положить человеку уведомление. Молча, если класть некому.

    Уведомление — вещь вспомогательная: если оно не легло, работа всё равно
    сделана. Поэтому здесь не бросается ничего: сорванная запись в почтовый
    ящик не должна отменять проверку отчёта.
    """
    if not user_id:
        return
    try:
        _repos(request).notices.add(user_id, kind, title, body, link, from_id)
    except Exception:                   # noqa: BLE001 — работа важнее извещения
        log.warning("не удалось положить уведомление %s для %s", kind, user_id)


@router.get("/notifications")
def notifications(request: Request, limit: int = 50) -> dict[str, Any]:
    """Что человеку нужно знать. Свежие сверху."""
    user = require_user(request)
    repos = _repos(request)
    items = repos.notices.list_for(user.id, limit=min(max(int(limit or 50), 1), 200))
    return {
        "items": [item.to_dict() for item in items],
        "unseen": repos.notices.unseen(user.id),
        # Непрочитанные сообщения считаем тут же: значок в шапке один, и
        # два запроса ради одного числа — лишний разговор с сервером.
        "messages": repos.talks.unread_total(user.id),
    }


@router.post("/notifications/read")
def read_notifications(request: Request) -> dict[str, Any]:
    """Отметить прочитанным одно уведомление или все сразу.

    «Все сразу» снимает и непрочитанные сообщения бесед. В числе у
    колокольчика они считаются наравне с уведомлениями, а уведомление о
    сообщении можно прочесть и удалить, не открыв саму беседу, — и тогда
    число висело, снять его было нечем. Кнопка «прочитано всё» обязана
    делать то, что написано на ней, а не половину этого.
    """
    user = require_user(request)
    repos = _repos(request)
    payload = _body(request)
    raw = payload.get("id")
    repos.notices.mark_seen(user.id, int(raw) if raw else None)
    talks = 0
    if not raw:
        talks = repos.talks.mark_all_read(user.id)
    return {"unseen": repos.notices.unseen(user.id),
            "messages": repos.talks.unread_total(user.id),
            "talks_read": talks}


@router.delete("/notifications")
def clear_notifications(request: Request) -> dict[str, Any]:
    user = require_user(request)
    _repos(request).notices.clear(user.id)
    return {"ok": True}


#: Должности, которые вызвать нельзя. Вызов — это «подойдите ко мне», и
#: снизу вверх он не делается: к начальнику отдела заходят сами.
UNCALLABLE_ROLES = ("owner", "head")


@router.post("/notifications/call")
def call_to_office(request: Request) -> dict[str, Any]:
    """Вызвать военнослужащего в кабинет.

    Право начальства: создатель, начальник отдела, заместитель и начальник
    группы. Вызывать друг друга всем подряд — не порядок, а способ мешать
    работать.
    """
    admin = require_admin(request)
    repos = _repos(request)
    payload = _body(request)
    user = repos.users.get(int(payload.get("user_id") or 0))
    if user is None or not user.active or not user.approved:
        raise ServiceError("военнослужащий не найден", 404)
    if user.id == admin.id:
        raise ServiceError("себя вызывать не нужно", 400)
    if user.role in UNCALLABLE_ROLES:
        # В отделе к начальнику не вызывают — к нему заходят. Права
        # администратора есть и у начальника группы, и без этого правила он
        # мог бы вызвать начальника отдела к себе в кабинет.
        raise ServiceError(
            "начальника отдела не вызывают — к нему заходят сами", 403)
    where = _card_line(payload.get("place", ""), "tc_no")
    note = _card_line(payload.get("note", ""), "note")

    repos.notices.add(
        user.id, "call",
        f"Вас вызывает {short_name(admin.full_name) or admin.login}",
        " ".join(part for part in (where and f"Куда: {where}.", note) if part),
        link="", from_id=admin.id)
    repos.audit.log("user.call", user=admin, object_type="user",
                    object_id=user.login, details={"place": where})
    return {"ok": True}


# ---------------------------------------------------------- переписка ----

@router.get("/talks")
def list_talks(request: Request) -> dict[str, Any]:
    """Беседы человека: свежие сверху."""
    user = require_user(request)
    return {"items": _repos(request).talks.list_for(user.id)}


@router.post("/talks")
def create_talk(request: Request) -> dict[str, Any]:
    """Завести беседу: личную или на несколько человек.

    Личная беседа двоих не заводится дважды: иначе каждое «написать Иванову»
    рождало бы новую ветку, и переписка рассыпалась бы на одинаковые.
    """
    user = require_user(request)
    repos = _repos(request)
    payload = _body(request)

    raw = payload.get("members") or []
    if not isinstance(raw, list):
        raise ServiceError("список собеседников не разобран", 400)
    members = []
    for item in raw:
        person = repos.users.get(int(item or 0))
        if person is None or not person.active or not person.approved:
            raise ServiceError("военнослужащий не найден", 404)
        members.append(person.id)
    if not members:
        raise ServiceError("выберите, кому писать", 400)
    members = list(dict.fromkeys(members + [user.id]))
    title = _card_line(payload.get("title", ""), "title")

    if len(members) == 2 and not title:
        other = [item for item in members if item != user.id]
        existing = repos.talks.private_between(user.id, other[0] if other else user.id)
        if existing is not None:
            return {"talk_id": existing, "existed": True}

    talk_id = repos.talks.create(members, title=title, created_by=user.id)
    return {"talk_id": talk_id, "existed": False}


@router.get("/talks/{talk_id}")
def read_talk(request: Request, talk_id: int) -> dict[str, Any]:
    user = require_user(request)
    repos = _repos(request)
    if not repos.talks.is_member(talk_id, user.id):
        raise ServiceError("беседа не найдена", 404)
    messages = repos.talks.messages(talk_id)
    repos.talks.mark_read(talk_id, user.id)
    # Число страниц для приложенных PDF и сканов: без него окно не напишет
    # «страница 1 из 4» и не узнает, есть ли следующая.
    counts = {item.id: page_count(Path(item.path))
              for item in repos.talks.files(talk_id)
              if is_renderable(item.name) and item.path}
    listing = []
    for message in messages:
        data = message.to_dict()
        for attachment in data.get("files") or []:
            total = counts.get(int(attachment.get("id") or 0))
            if total:
                attachment["pages"] = total
        listing.append(data)
    return {
        "id": talk_id,
        "members": repos.talks.members(talk_id),
        "messages": listing,
    }


@router.delete("/talks/{talk_id}")
def leave_talk(request: Request, talk_id: int) -> dict[str, Any]:
    """Убрать беседу. У двоих — у обоих, в беседе нескольких — у себя.

    Прежде уходил только тот, кто удалял, и для беседы ДВОИХ это выходило
    хуже некуда: у собеседника оставалась беседа, отвечать в которой некому,
    а на попытку написать заново заводилась ВТОРАЯ беседа с тем же
    человеком. Отдел: «удалил чат, у другого остался, и чтобы снова
    написать, нужно создать ему доп. чат со мной».

    У беседы двоих нет третьего, чью запись мы бы стёрли: разговор
    принадлежит им обоим. В беседе НЕСКОЛЬКИХ так нельзя — остальные в ней
    остались, и решение одного за всех не решает; там по-прежнему уходит
    только тот, кто попросил, а беседа исчезает, когда её покинул последний.

    Сам факт удаления остаётся в журнале действий в обоих случаях.
    """
    user = require_user(request)
    repos = _repos(request)
    if not repos.talks.is_member(talk_id, user.id):
        raise ServiceError("беседа не найдена", 404)
    вдвоём = repos.talks.is_private(talk_id)
    участники = [member["id"] for member in repos.talks.members(talk_id)]
    if вдвоём:
        paths = repos.talks.purge(talk_id)
        purged = True
    else:
        paths = repos.talks.leave(talk_id, user.id)
        purged = not repos.talks.members(talk_id)
    for raw in paths:
        try:
            Path(raw).unlink()
        except OSError:                 # noqa: PERF203 — файла может уже не быть
            pass
    repos.audit.log("talk.leave", user=user, object_type="talk",
                    object_id=str(talk_id),
                    details={"purged": purged, "both": вдвоём,
                             "members": участники})
    return {"purged": purged, "both": вдвоём}


@router.post("/talks/{talk_id}/messages")
def write_to_talk(request: Request, talk_id: int) -> dict[str, Any]:
    user = require_user(request)
    repos = _repos(request)
    if not repos.talks.is_member(talk_id, user.id):
        raise ServiceError("беседа не найдена", 404)
    text = str(_body(request).get("text", "")).strip()
    if not text:
        raise ServiceError("сообщение пустое", 400)
    if len(text) > 4000:
        raise ServiceError("сообщение длиннее 4000 знаков", 400)

    message = repos.talks.add_message(talk_id, user.id, text)
    # Остальным участникам — уведомление: человек может не держать беседу
    # открытой, а сообщение чаще всего срочное.
    for member in repos.talks.members(talk_id):
        if member["id"] == user.id:
            continue
        _notify(request, member["id"], "message",
                f"Сообщение от {short_name(user.full_name) or user.login}",
                text[:200], link=f"#/talks/{talk_id}", from_id=user.id)
    return {"message": message.to_dict() if message else None}


@router.post("/talks/{talk_id}/files")
def attach_to_talk(request: Request, talk_id: int,
                   file: UploadFile = File(...),
                   text: str = Form("")) -> dict[str, Any]:
    """Приложить файл к сообщению в беседе.

    Половина вопросов по письму решается тем, что человек показывает
    картинку: «глянь, это тот же ствол?». Переслать её отделу было нечем —
    почты в изолированном контуре нет, а класть снимок экрана к письму,
    когда речь про соседнее, неправильно.

    Файл всегда идёт сообщением: подпись к нему необязательна, но пустая
    строка в переписке без слов — это и есть «вот, смотри».
    """
    user = require_user(request)
    repos = _repos(request)
    settings = _settings(request)
    if not repos.talks.is_member(talk_id, user.id):
        raise ServiceError("беседа не найдена", 404)

    name = _safe_name(Path(file.filename or "файл").name)
    if not name:
        raise ServiceError("некорректное имя файла", 400)
    _refuse_dangerous(name)

    settings.ensure_dirs()
    target_dir = Path(settings.data_dir) / "talk-files" / str(talk_id)
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
        caption = str(text or "").strip()[:CARD_LIMITS["note"]] or name
        # Текст вычитываем тем же конвертером, что и бумаги письма: без него
        # документ Word собеседнику пришлось бы скачивать, чтобы прочитать.
        body, _problem = _extract_attachment(target, name)
        message = repos.talks.add_message(talk_id, user.id, caption)
        item = repos.talks.add_file(talk_id, message.id if message else None,
                                    user.id, name, str(target), size,
                                    text=body.strip())
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    for member in repos.talks.members(talk_id):
        if member["id"] == user.id:
            continue
        _notify(request, member["id"], "message",
                f"Файл от {short_name(user.full_name) or user.login}",
                name, link=f"#/talks/{talk_id}", from_id=user.id)
    return {"file": item.to_dict(),
            "message": message.to_dict() if message else None}


@router.get("/talks/{talk_id}/files/{file_id}/text")
def read_talk_file_text(request: Request, talk_id: int, file_id: int) -> dict[str, Any]:
    """Что система вычитала из приложенного документа.

    Для Word и Excel это единственный способ прочитать документ, не
    скачивая его: браузер такие файлы не рисует.
    """
    user = require_user(request)
    repos = _repos(request)
    if not repos.talks.is_member(talk_id, user.id):
        raise ServiceError("беседа не найдена", 404)
    item = repos.talks.file(file_id)
    if item is None or item.talk_id != talk_id:
        raise ServiceError("файл не найден", 404)
    text = item.text.strip()
    return {"text": text[:ATTACHMENT_TEXT_LIMIT],
            "truncated": len(text) > ATTACHMENT_TEXT_LIMIT,
            "recognised": False}


@router.get("/talks/{talk_id}/files/{file_id}")
def read_talk_file(request: Request, talk_id: int, file_id: int,
                   inline: int = 0, page: int = 0):
    """Отдать приложенный к беседе файл. Только участнику беседы."""
    user = require_user(request)
    repos = _repos(request)
    if not repos.talks.is_member(talk_id, user.id):
        raise ServiceError("беседа не найдена", 404)
    item = repos.talks.file(file_id)
    if item is None or item.talk_id != talk_id:
        raise ServiceError("файл не найден", 404)
    path = Path(item.path)
    if not path.exists():
        raise ServiceError("файл не найден на диске", 410)
    return _preview_reply(request, path, item.name, inline=inline, page=page)


# -------------------------------------------------------------- сводка ----

def _one_per_person(records: Iterable[Any]) -> dict[int, Any]:
    """По одной записи на человека — той, что кончается позже."""
    chosen: dict[int, Any] = {}
    for item in records:
        current = chosen.get(item.user_id)
        if current is None or item.date_to > current.date_to:
            chosen[item.user_id] = item
    return chosen


@router.get("/board")
def board(request: Request, days: int = 30) -> dict[str, Any]:
    """Сводка отдела: люди, нагрузка, сроки, дежурство, движение за период."""
    require_user(request)
    repos = _repos(request)
    today = _today()
    period_days = min(max(int(days or 30), 1), 365)
    since = _since_utc(period_days)

    staff = repos.board.workload(today)
    records = repos.absences.on_date(today)
    # По человеку — одна запись, та, что кончается позже. И у отсутствия, и у
    # дежурства: военнослужащему отмечают больничный и следом отпуск, дежурство и
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
            "full_name": short_name(row["full_name"]) or row["login"],
            # Отключённый военнослужащий остаётся в списке, пока за ним числятся
            # письма: их надо передать живому человеку, и это должно быть видно.
            "active": bool(row["active"]),
            "role": row["role"],
            "role_title": role_title_of(row["role"]),
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
            # В строю — действующие. Отключённый военнослужащий попадает в список
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


def _opt_str(payload: dict[str, Any], name: str) -> str | None:
    """Значение поля, если оно вообще пришло. None — «не менять»."""
    return None if name not in payload else str(payload[name] or "").strip()


# --------------------------------------------------------- личный кабинет --

#: В чём приносят документы военнослужащего: набранный файл, скан, снимок.
#: Справочный список — как и CASE_FILE_SUFFIXES, ничего не запрещает.
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
        raise ServiceError("военнослужащий не найден", 404)
    return person


@router.get("/users/{user_id}/files")
def list_person_files(request: Request, user_id: int) -> dict[str, Any]:
    """Документы военнослужащего: справка-объективка, приказы, прочее."""
    actor = require_user(request)
    person = _person_or_404(request, user_id)
    if not _may_see_person_files(actor, person.id):
        raise ServiceError(
            "недостаточно прав: документы военнослужащего видят он сам, начальник "
            "отдела, заместитель и создатель системы", 403)
    items = _repos(request).person_files.list_for_user(person.id)
    return {
        "user": _user_public(person),
        "files": [with_pages(item) for item in items],
        "kinds": [{"id": kind, "title": PERSON_FILE_TITLES[kind]}
                  for kind in PERSON_FILE_KINDS],
        "can_edit": actor.id == person.id or actor.role in REVIEW_ROLES,
    }


@router.post("/users/{user_id}/files")
def add_person_file(request: Request, user_id: int,
                    file: UploadFile = File(...),
                    kind: str = Form("profile"),
                    note: str = Form("")) -> dict[str, Any]:
    """Приложить документ к военнослужащему.

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
    return {"file": with_pages(item)}


@router.get("/users/{user_id}/files/{file_id}")
def download_person_file(request: Request, user_id: int, file_id: int,
                         inline: int = 0, page: int = 0):
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
    return _preview_reply(request, path, item.name, inline=inline, page=page)


@router.delete("/users/{user_id}/files/{file_id}")
def delete_person_file(request: Request, user_id: int, file_id: int) -> dict[str, Any]:
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
def update_my_contacts(request: Request) -> dict[str, Any]:
    """Свои контакты человек правит сам.

    Справочник, который ведёт кадровик, устаревает быстрее, чем его правят;
    свой внутренний номер человек поправит в ту же минуту, когда переедет.
    """
    user = require_user(request)
    payload = _body(request)
    fields: dict[str, Any] = {}
    for name in ("phone_mobile", "phone_open", "phone_secure", "room"):
        if name in payload:
            fields[name] = str(payload[name] or "").strip()[:120]
    if not fields:
        return {"user": _user_public(user)}
    updated = _repos(request).users.update(user.id, **fields)
    _repos(request).audit.log("user.contacts", user=user, object_type="user",
                              object_id=user.login, details={"fields": sorted(fields)})
    return {"user": _user_public(updated)}


@router.get("/people/{user_id}")
def person_card(request: Request, user_id: int) -> dict[str, Any]:
    """Карточка военнослужащего, открытая всему отделу.

    Кто это, кем работает, в какой группе, по какому подразделению стоит по
    штату, как до него дозвониться и где он сегодня. Всё это в отделе и так
    знают друг о друге — а новому человеку спрашивать по коридору неудобно,
    и справочник для того и нужен.

    Это не «Военнослужащие»: там заводят учётные записи и меняют должности, и
    туда рядового инженера не пускают. И не документы: справка-объективка и
    приказы остаются закрытыми — их круг уже (см. `_may_see_person_files`).
    """
    actor = require_user(request)
    repos = _repos(request)
    person = repos.users.get(user_id)
    if person is None or not person.approved:
        raise ServiceError("военнослужащий не найден", 404)

    today = _today()
    # Где человек сегодня: та же запись расхода, что видна в общем списке.
    marks = [item for item in repos.absences.on_date(today)
             if item.user_id == person.id]
    marks.sort(key=lambda item: item.date_to, reverse=True)
    where = marks[0].to_dict() if marks else None

    card = {
        "id": person.id,
        "login": person.login,
        "full_name": person.full_name or person.login,
        "short_name": short_name(person.full_name) or person.login,
        "role": person.role,
        "role_title": role_title_of(person.role),
        "role_note": ROLE_NOTES.get(person.role, ""),
        # Работают все в отделе; «по штату» заполнено только у тех, кто
        # числится в другом подразделении.
        "team": person.team,
        "department": person.department,
        "phone_mobile": person.phone_mobile,
        "phone_open": person.phone_open,
        "phone_secure": person.phone_secure,
        "room": person.room,
        # Когда человека последний раз видели. Отвечает на обычный вопрос
        # отдела: писать ему сейчас или он ушёл и прочтёт завтра.
        "last_seen_at": person.last_seen_at,
        "active": person.active,
        "created_at": person.created_at,
        "where": where,
        # Нагрузка — не тайна: по ней и решают, кому отдать письмо.
        "open_cases": repos.cases.count(status="open", assignee_id=person.id),
        "is_me": actor.id == person.id,
        "may_see_files": _may_see_person_files(actor, person.id),
    }
    return {"person": card}


@router.get("/me/summary")
def my_summary(request: Request) -> dict[str, Any]:
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
        # Чем военнослужащий отчитывается за последний шаг: сколько ответов он
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
def change_password(request: Request, response: Response) -> dict[str, Any]:
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
def stats(request: Request) -> dict[str, Any]:
    """Метрики отдела — только администратору.

    По распоряжению начальника отдела: «метрики доступны только админу».
    Прежде их видел любой вошедший, а это сводка по работе отдела целиком —
    сколько писем, чьи отчёты правят и насколько сильно.
    """
    require_admin(request)
    return _service(request).stats()


@router.get("/stats/questions")
def stats_questions(request: Request, limit: int = 200,
                    query: str = "") -> dict[str, Any]:
    """О чём спрашивают помощника — тело вопроса и кто его задал.

    Заведено по распоряжению начальника отдела: «сделай, чтобы админ мог
    видеть тело запроса пользователей в метриках». Здесь видно то, ради чего
    это и заводилось: на каких вопросах помощник работает вхолостую — нашёл
    мало, сослался ни на что.

    Просмотр пишется в журнал действий. Начальник смотрит переписку
    подчинённых с помощником — это его право, но не тайна: в системе, где
    записано, кто открывал письмо и правил библиотеку, этот просмотр
    записан ровно так же.
    """
    user = require_admin(request)
    items = _repos(request).chats.questions(
        limit=max(1, min(int(limit), 1000)), query=query)
    _repos(request).audit.log(
        "stats.questions", user=user, object_type="chat",
        details={"limit": int(limit), "query": query, "shown": len(items)})
    return {"items": items}


@router.get("/audit")
def audit(request: Request, limit: int = 200) -> dict[str, Any]:
    """Журнал действий — только создателю системы.

    Права администратора в отделе есть и у начальника группы: он заводит
    людей и правит библиотеку. Но журнал — это не управление отделом, а
    протокол работы самой системы: кто что открывал, менял и удалял, включая
    действия начальства. Читать его должен тот, кто за систему отвечает, а не
    всякий, кому нужно завести нового инженера.
    """
    require_owner(request)
    entries = _repos(request).audit.list(limit=min(limit, 1000))
    return {"items": [entry.to_dict() for entry in entries]}


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Жив ли сервис. Отвечает и без входа — этим пользуются скрипты запуска.

    Без входа отдаём только признак жизни. Сколько в отделе военнослужащих,
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


def _count_or_zero(value: Any) -> int:
    """Счётная величина письма: неотрицательное целое, пусто — ноль.

    Отрицательное число регистраций — это описка, а не сведение; принимать
    его значит соглашаться, что письмо зарегистрировали минус три раза.
    """
    raw = str(value if value is not None else "").strip()
    if not raw:
        return 0
    try:
        number = int(float(raw.replace(",", ".")))
    except ValueError:
        raise ServiceError("количество регистраций — целое число", 400) from None
    if number < 0:
        raise ServiceError("количество регистраций не бывает отрицательным", 400)
    if number > 100000:
        raise ServiceError("количество регистраций слишком велико — проверьте ввод", 400)
    return number


#: Каким может быть логин. Один на заявку и на заведение руками: разойдутся —
#: и человек, чью заявку одобрили, не сможет войти под тем, что он вводил.
LOGIN_RE = r"[a-z0-9._-]{3,32}"
LOGIN_HINT = ("логин: от 3 до 32 знаков, латиница, цифры, точка, дефис "
              "или подчёркивание")


def _check_full_name(value: str) -> str:
    """ФИО заводят полностью: «Жуков Пётр Иванович», а не «Жуков П. И.».

    Полное имя нужно там, где документ подписывают человеком, а не
    сокращением: справка-объективка, приказ, исходящее письмо. В списках оно
    всё равно показывается инициалами — сокращать умеет система, а
    восстанавливать имя из «П. И.» не умеет никто.

    Одну фамилию не принимаем: имя есть у всех. Уже сокращённое «Жуков П. И.»
    проходит — записи, заведённые до этого правила, править насильно незачем.
    """
    full = " ".join(str(value or "").split())
    if not full:
        raise ServiceError("укажите фамилию, имя и отчество", 400)
    if len(full) > CARD_LIMITS["title"]:
        raise ServiceError(
            f"ФИО: длиннее {CARD_LIMITS['title']} знаков", 400)
    if len(full.split()) < 2:
        raise ServiceError(
            "напишите фамилию, имя и отчество полностью — "
            "одной фамилии недостаточно", 400)
    return full


def _check_login(login: str) -> None:
    if not re.fullmatch(LOGIN_RE, login):
        raise ServiceError(LOGIN_HINT, 400)


def _line_or_empty(value: Any) -> str:
    """Линия связи: один из известных видов либо пусто.

    Пустое значение разрешено намеренно: письмо иногда спускают раньше, чем
    становится ясно, к какой линии оно относится, и запирать регистрацию
    из-за этого нельзя.
    """
    line = str(value or "").strip()
    if not line:
        return ""
    if line in LINE_TYPES:
        return line
    # Название линии принимаем наравне с её кодом: «РРЛС» пишут и в описи, и
    # в переносе данных из другой системы, а отказ звучал издевательски —
    # «неизвестная линия связи 'РРЛС' (известны: СЛС, РРЛС, КВ, Другое)».
    for key in LINE_TYPES:
        if LINE_TITLES[key].casefold() == line.casefold():
            return key
    known = ", ".join(f"{key} ({LINE_TITLES[key]})" for key in LINE_TYPES)
    raise ServiceError(f"неизвестная линия связи '{line}' (известны: {known})", 400)


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


#: Что браузер показывает сам и без опаски. HTML и SVG сюда не входят
#: намеренно: в них живёт скрипт, а показанная встроенным окном страница
#: получила бы права нашего же адреса. Их отдаём вложением, как и всё прочее.
INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".json": "text/plain; charset=utf-8",
}


def with_pages(item: Any) -> dict[str, Any]:
    """Описание файла плюс число страниц — для показа картинками.

    Знать его окну нужно заранее: без этого нельзя написать «страница 1 из 4»
    и нельзя понять, есть ли следующая. Путь к файлу на сторону человека при
    этом не уходит — берём его из записи, а не из ответа. Открытие файла тоже
    почти ничего не стоит: MuPDF читает оглавление, до страниц дело не идёт.
    """
    data = item.to_dict()
    name = str(getattr(item, "name", "") or "")
    if not is_renderable(name):
        return data
    raw = str(getattr(item, "path", "") or "")
    if not raw:
        return data
    total = page_count(Path(raw))
    if total:
        data["pages"] = total
    return data


def _preview_reply(request: Request, path: Path, name: str, *,
                   inline: int = 0, page: int = 0):
    """Что отдать в ответ на просмотр: страницу картинкой или сам файл.

    PDF в окне не открывался вовсе: встроенное окно стоит в песочнице и с
    запретом «default-src 'none'» — чужой файл в своей странице — это чужой
    код в своей странице. Встроенный просмотрщик браузера тоже код, и запрет
    глушил его: человек видел пустой серый прямоугольник и шёл скачивать файл.

    Снимать запрет нельзя — PDF умеет исполнять свой код. Поэтому страницу
    рисуем у себя и отдаём картинкой: картинка кода не несёт ни при каком
    браузере. Заодно так показываются сканы TIFF, которых не показывает
    вообще ни один браузер.
    """
    if page > 0:
        try:
            data = render_page(path, page,
                               cache_root=Path(_settings(request).data_dir) / "kesh")
        except PageRenderError as error:
            raise ServiceError(str(error), 404) from error
        return Response(
            content=data, media_type="image/png",
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "sandbox; default-src 'none'",
            },
        )
    return _file_reply(path, name, inline=bool(inline))


def _file_reply(path: Path, name: str, *, inline: bool = False) -> FileResponse:
    """Отдать файл: вложением или для просмотра прямо на экране."""
    media = INLINE_TYPES.get(Path(name).suffix.lower())
    if not inline or media is None:
        # Тип не угадываем по имени. Ограничения на расширение при приёме нет,
        # и подписанный .html Starlette отдал бы как text/html: браузер, если
        # заголовок «скачать» когда-нибудь потеряется, выполнил бы чужую
        # страницу как нашу собственную. Файл, который мы не показываем сами,
        # уходит потоком байтов и ничем иным.
        return FileResponse(path, filename=name,
                            media_type="application/octet-stream",
                            headers={"Content-Disposition": _disposition(name),
                                     "X-Content-Type-Options": "nosniff"})
    # Content-Disposition: inline с тем же кодированием имени по RFC 5987:
    # заголовки HTTP — latin-1, а имена файлов у нас кириллические.
    return FileResponse(
        path, media_type=media,
        headers={
            "Content-Disposition": _disposition(name).replace("attachment;", "inline;", 1),
            # Показываем чужой файл в своём окне — запрещаем его исполнение
            # и переугадывание типа: подписанный .png, внутри которого HTML,
            # иначе выполнился бы как страница нашего адреса.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'",
        },
    )


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
                 domain: str | None = None) -> dict[str, Any]:
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


def _ingest_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    keys = ("added", "updated", "skipped", "failed", "chunks", "documents",
            "warnings", "failures", "notes")
    return {key: getattr(result, key, None) for key in keys if hasattr(result, key)}
