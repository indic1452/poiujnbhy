"""Модели строк базы. Тонкие dataclass-обёртки над sqlite3.Row.

Хранилище намеренно не тянет ORM: таблиц немного, запросы простые, а явный SQL
проще сопровождать инженеру, который придёт после автора системы.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from ..corpus import SEARCHABLE_STATUSES

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
#: Путь письма в отделе: пришло — сделали отчёт — сдали начальнику — он
#: проверил — инженер отправил ответ на исходящий номер — сдали в архив.
#: «Проверен» и «отправлено» — разные вещи: между ними живёт работа
#: исполнителя, и пока ответ не ушёл, письмо считается незакрытым.
CASE_STATUSES = ("new", "draft", "review", "checked", "approved", "archived")
CASE_STATUS_TITLES = {
    "new": "принято",
    "draft": "в работе",
    "review": "на проверке",
    "checked": "проверен, к отправке",
    "approved": "отправлено",
    "archived": "в архиве",
}
#: Письма, которые считаются работой в текущий момент. Проверенное, но не
#: отправленное письмо — тоже работа: ответ ещё не ушёл.
OPEN_CASE_STATUSES = ("new", "draft", "review", "checked")

#: Линии связи, по которым работает отдел. Идентификатор латиницей — по нему
#: сверяется код и строится фильтр; человеку показывается сокращение из
#: LINE_TITLES, принятое в отделе. «Другое» оставлено намеренно: линий больше,
#: чем три, и заставлять человека врать в поле нельзя — чем именно занято
#: письмо, он напишет в описании.
LINE_TYPES = ("sls", "rrls", "kv", "other")
LINE_TITLES = {
    "sls": "СЛС",
    "rrls": "РРЛС",
    "kv": "КВ",
    "other": "Другое",
}
#: Полные названия — для подсказок и отчётов о работе.
LINE_FULL_TITLES = {
    "sls": "Спутниковая линия связи",
    "rrls": "Радиорелейная линия связи",
    "kv": "Коротковолновая связь",
    "other": "Другое",
}

CASE_PRIORITIES = ("normal", "high", "urgent")
CASE_PRIORITY_TITLES = {"normal": "обычный", "high": "важный", "urgent": "срочный"}

#: Документы сотрудника. Главный из них — справка-объективка, её в отделе
#: спрашивают чаще прочего и она одна: новая заменяет прежнюю. Остальное —
#: приказы, свидетельства, допуски — копится, таких бумаг бывает много.
PERSON_FILE_KINDS = ("profile", "order", "other")
PERSON_FILE_TITLES = {
    "profile": "справка-объективка",
    "order": "приказ",
    "other": "прочее",
}
#: Виды, которых у человека может быть только по одному.
PERSON_FILE_SINGLE = ("profile",)

#: Расход личного состава: чем занят человек в этот день. Порядок важен —
#: в таком виде он и показывается в сетке расхода, от «на месте» к «нет».
ABSENCE_KINDS = ("duty", "work", "trip", "study", "vacation", "sick", "dayoff")
ABSENCE_TITLES = {
    "duty": "дежурство",
    "work": "работы",
    "trip": "командировка",
    "study": "учёба",
    "vacation": "отпуск",
    "sick": "больничный",
    "dayoff": "отгул",
}
#: Кто считается на месте. Дежурный и занятый работами в отделе — на месте:
#: их можно спросить и им можно дать письмо. Раньше «на месте» был только
#: дежурный, и любая отметка о работах превращала человека в отсутствующего.
PRESENT_KINDS = ("duty", "work")
#: Тот, у кого на день нет ни одной отметки, в расходе показывается как
#: «не отмечен»: это не отсутствие, а незаполненный расход.
ABSENCE_UNMARKED = "unmarked"
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


def short_name(full: str) -> str:
    """«Жуков Пётр Иванович» → «Жуков П. И.».

    В записи человек хранится полностью — так его пишут в приказе и в
    справке-объективке. В списках и таблицах полное имя занимает столько
    места, что вытесняет то, ради чего в список смотрят; отдел друг друга
    знает и по фамилии с инициалами.

    Правило идемпотентно само по себе: первая буква инициала «П.» — та же
    «П», и «Жуков П. И.» остаётся собой. Это важно для записей, заведённых
    до полного ФИО: их прогоняют через то же сокращение, и точки не должны
    множиться или пропадать.

    Двойная фамилия через дефис на инициалы не разбирается:
    «Римский-Корсаков» — одно слово и одна фамилия.
    """
    parts = str(full or "").split()
    if not parts:
        return ""
    return " ".join([parts[0]] + [part[0].upper() + "." for part in parts[1:]])


@dataclass
class User:
    id: int
    login: str
    full_name: str = ""
    role: str = "engineer"
    #: Подразделение, в котором человек стоит ПО ШТАТУ. Работают все в одном
    #: отделе — это и есть система, — а по штату сотрудник может числиться в
    #: другом подразделении. Пусто — стоит там же, где работает. Колонка
    #: называется department по историческим причинам.
    department: str = ""
    #: Группа внутри отдела.
    team: str = ""
    #: Как найти человека. Заполняет он сам в личном кабинете. Телефоны
    #: названы по-отдельски: по мобильному звонят, по открытому говорят о
    #: работе в общих словах, по режимному — обо всём остальном. Одно поле
    #: «Телефон» на все три не годилось: по номеру не понять, можно ли по
    #: нему говорить.
    phone_mobile: str = ""
    phone_open: str = ""
    phone_secure: str = ""
    room: str = ""
    #: Старые поля. Наружу не показываются, но и не стираются: в них лежат
    #: номера, набранные людьми до переименования.
    phone: str = ""
    ext_no: str = ""
    email: str = ""
    active: bool = True
    #: Заявку одобрили. Пока нет — человек заведён, но войти не может.
    approved: bool = True
    approved_by: int | None = None
    approved_at: str = ""
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
            phone_mobile=_col(row, "phone_mobile", "") or "",
            phone_open=_col(row, "phone_open", "") or "",
            phone_secure=_col(row, "phone_secure", "") or "",
            room=_col(row, "room", "") or "",
            phone=_col(row, "phone", "") or "",
            ext_no=_col(row, "ext_no", "") or "",
            email=_col(row, "email", "") or "",
            active=bool(row["active"]),
            approved=bool(_col(row, "approved", 1)),
            approved_by=_col(row, "approved_by", None),
            approved_at=_col(row, "approved_at", "") or "",
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "full_name": self.full_name,
            # Для списков и таблиц: полное ФИО в них не помещается.
            "short_name": short_name(self.full_name),
            "role": self.role,
            "role_title": self.role_title,
            "department": self.department,
            "team": self.team,
            "phone_mobile": self.phone_mobile,
            "phone_open": self.phone_open,
            "phone_secure": self.phone_secure,
            "room": self.room,
            "is_admin": self.is_admin,
            "approved": self.approved,
            "approved_at": self.approved_at,
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
            # Отдельным полем, а не только внутри meta: по нему рисуется
            # метка в списке библиотеки, и лезть за ней в словарь на каждой
            # из тринадцати тысяч строк незачем.
            "text_quality": str(self.meta.get("text_quality", "") or ""),
            "meta": self.meta,
        }


@dataclass
class Case:
    id: int
    case_id: str
    report_type: str
    #: Описание письма — о чём оно. В интерфейсе поле так и называется.
    title: str = ""
    customer: str = ""
    status: str = "new"
    #: Линия связи: sls | rrls | kv | other. Пусто — не указана.
    line_type: str = ""
    #: Номер технического средства и его дата.
    tc_no: str = ""
    tc_date: str = ""
    #: Указания, по которым письмо отрабатывают: номер и дата.
    order_no: str = ""
    order_date: str = ""
    #: Сколько регистраций числится по письму. В журнале отдела это счётная
    #: величина, по ней потом отчитываются за объём работы.
    registrations: int = 0
    #: Входящий номер и дата входящего письма.
    incoming_no: str = ""
    incoming_date: str = ""
    #: Исходящий номер ответа, дата отправки и кто отправил. Проставляются,
    #: когда проверенный отчёт ушёл адресату: до этого письмо не закрыто.
    outgoing_no: str = ""
    outgoing_date: str = ""
    #: Что написали при отправке ответа. Отдельно от примечания к письму:
    #: одно про входящее, другое про то, чем ответили.
    outgoing_note: str = ""
    sent_by: int | None = None
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
    #: Сколько файлов приложено к письму. Считает выборка, в таблице нет.
    files_count: int = 0
    #: ФИО отправившего ответ. Заполняется выборкой со связкой.
    sent_by_name: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Case":
        return cls(
            id=row["id"],
            case_id=row["case_id"],
            report_type=row["report_type"],
            title=row["title"],
            customer=row["customer"],
            status=row["status"],
            line_type=_col(row, "line_type", "") or "",
            tc_no=_col(row, "tc_no", "") or "",
            tc_date=_col(row, "tc_date", "") or "",
            order_no=_col(row, "order_no", "") or "",
            order_date=_col(row, "order_date", "") or "",
            registrations=int(_col(row, "registrations", 0) or 0),
            outgoing_note=_col(row, "outgoing_note", "") or "",
            incoming_no=_col(row, "incoming_no", ""),
            incoming_date=_col(row, "incoming_date", ""),
            outgoing_no=_col(row, "outgoing_no", "") or "",
            outgoing_date=_col(row, "outgoing_date", "") or "",
            sent_by=_col(row, "sent_by", None),
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
            files_count=int(_col(row, "files_count", 0) or 0),
            sent_by_name=_col(row, "sent_by_name", "") or "",
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
            # Название состояния по-русски — одно на всю систему. Иначе
            # интерфейс и отчёты о работе называют одно и то же по-разному.
            "status_title": CASE_STATUS_TITLES.get(self.status, self.status),
            "line_type": self.line_type,
            "line_title": LINE_TITLES.get(self.line_type, ""),
            "tc_no": self.tc_no,
            "tc_date": self.tc_date,
            "order_no": self.order_no,
            "order_date": self.order_date,
            "registrations": self.registrations,
            "incoming_no": self.incoming_no,
            "incoming_date": self.incoming_date,
            "outgoing_no": self.outgoing_no,
            "outgoing_date": self.outgoing_date,
            "outgoing_note": self.outgoing_note,
            "sent_by": self.sent_by,
            "sent_by_name": short_name(self.sent_by_name),
            "deadline": self.deadline,
            "priority": self.priority,
            "assignee_id": self.assignee_id,
            "assignee_name": short_name(self.assignee_name),
            "note": self.note,
            "facts_digest": self.facts_digest,
            "reports_count": self.reports_count,
            "files_count": self.files_count,
            # Кто завёл письмо. Нужно интерфейсу: своё ошибочно заведённое
            # письмо человек убирает сам, чужое — только администратор.
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if with_facts:
            data["facts"] = self.facts
        return data


#: К чему относится бумага: пришла с письмом или ушла с ответом. В журнале
#: отдела это две разные стопки, и смешивать их нельзя: по одной отвечают,
#: вторую отправляют.
FILE_STAGES = ("incoming", "outgoing")
FILE_STAGE_TITLES = {"incoming": "к письму", "outgoing": "к ответу"}


@dataclass
class CaseFile:
    """Бумага, приложенная к письму: скан письма, схема линии, журнал.

    Текст хранится рядом с путём намеренно. Файл на диске можно потерять
    или перенести, а искать письмо по словам из приложения нужно всегда —
    и после переноса тоже.
    """

    id: int
    case_ref: int
    name: str
    #: incoming — пришла с письмом, outgoing — ушла с ответом.
    stage: str = "incoming"
    size: int = 0
    path: str = ""
    text: str = ""
    note: str = ""
    uploaded_by: int | None = None
    uploaded_by_name: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CaseFile":
        return cls(
            id=row["id"],
            case_ref=row["case_ref"],
            name=row["name"],
            stage=_col(row, "stage", "incoming") or "incoming",
            size=int(_col(row, "size", 0) or 0),
            path=_col(row, "path", "") or "",
            text=_col(row, "text", "") or "",
            note=_col(row, "note", "") or "",
            uploaded_by=_col(row, "uploaded_by", None),
            uploaded_by_name=_col(row, "uploaded_by_name", "") or "",
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "case_ref": self.case_ref,
            "name": self.name,
            "stage": self.stage,
            "stage_title": FILE_STAGE_TITLES.get(self.stage, self.stage),
            "size": self.size,
            "note": self.note,
            # Сам текст наружу не отдаём: он нужен поиску, а не экрану, и
            # на скане журнала это сотни килобайт в каждом списке.
            "has_text": bool(self.text.strip()),
            "uploaded_by": self.uploaded_by,
            "uploaded_by_name": short_name(self.uploaded_by_name),
            "created_at": self.created_at,
        }


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


#: О чём система сообщает человеку. Порядок не важен, важно, чтобы вид знали
#: обе стороны: экран рисует по нему значок и звук.
NOTICE_KINDS = (
    "report.rework",     # начальник вернул отчёт с замечанием
    "report.review",     # вам сдали отчёт на проверку
    "report.approved",   # ваш отчёт проверен
    "case.assigned",     # письмо назначено на вас
    "case.note",         # к письму оставили примечание
    "case.sent",         # по письму отправлен ответ
    "call",              # вызов в кабинет
    "message",           # личное сообщение
    "user.approved",     # заявку на доступ одобрили
)
#: Что показывать заметно и со звуком: вызов в кабинет и возврат отчёта —
#: это то, ради чего человека отрывают от работы. Остальное ждёт.
LOUD_NOTICES = ("call", "report.rework")


@dataclass
class Notice:
    """Уведомление для одного человека."""

    id: int
    user_id: int
    kind: str
    title: str = ""
    body: str = ""
    link: str = ""
    from_id: int | None = None
    from_name: str = ""
    seen: bool = False
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Notice":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            kind=row["kind"],
            title=_col(row, "title", "") or "",
            body=_col(row, "body", "") or "",
            link=_col(row, "link", "") or "",
            from_id=_col(row, "from_id", None),
            from_name=_col(row, "from_name", "") or "",
            seen=bool(_col(row, "seen", 0)),
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "link": self.link,
            "from_id": self.from_id,
            "from_name": short_name(self.from_name),
            "seen": self.seen,
            "loud": self.kind in LOUD_NOTICES,
            "created_at": self.created_at,
        }


@dataclass
class TalkMessage:
    """Сообщение в беседе между людьми."""

    id: int
    talk_id: int
    user_id: int | None = None
    text: str = ""
    author: str = ""
    created_at: str = ""
    #: Приложенные файлы. Подставляются выборкой, в самой строке их нет.
    files: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TalkMessage":
        return cls(
            id=row["id"],
            talk_id=row["talk_id"],
            user_id=_col(row, "user_id", None),
            text=row["text"],
            author=_col(row, "author", "") or "",
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "talk_id": self.talk_id,
            "user_id": self.user_id,
            "author": short_name(self.author),
            "text": self.text,
            "files": self.files,
            "created_at": self.created_at,
        }


@dataclass
class TalkFile:
    """Файл, приложенный к сообщению: снимок экрана, выгрузка, схема.

    Половина вопросов по письму решается тем, что человек показывает
    картинку, — а переслать её отделу было нечем: почты в изолированном
    контуре нет.
    """

    id: int
    talk_id: int
    message_id: int | None = None
    user_id: int | None = None
    name: str = ""
    path: str = ""
    size: int = 0
    #: Наружу не отдаём: он нужен окну просмотра отдельным запросом, а в
    #: списке переписки это сотни килобайт на каждое сообщение.
    text: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TalkFile":
        return cls(
            id=row["id"],
            talk_id=row["talk_id"],
            message_id=_col(row, "message_id", None),
            user_id=_col(row, "user_id", None),
            name=row["name"],
            path=_col(row, "path", "") or "",
            size=int(_col(row, "size", 0) or 0),
            text=_col(row, "text", "") or "",
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "talk_id": self.talk_id,
            "message_id": self.message_id,
            "name": self.name,
            "size": self.size,
            "has_text": bool(self.text.strip()),
            "created_at": self.created_at,
        }


@dataclass
class CaseNote:
    """Примечание к письму: обсуждение прямо на деле."""

    id: int
    case_ref: int
    user_id: int | None = None
    text: str = ""
    author: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CaseNote":
        return cls(
            id=row["id"],
            case_ref=row["case_ref"],
            user_id=_col(row, "user_id", None),
            text=row["text"],
            author=_col(row, "author", "") or "",
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "case_ref": self.case_ref,
            "user_id": self.user_id,
            "author": short_name(self.author),
            "text": self.text,
            "created_at": self.created_at,
        }


@dataclass
class PersonFile:
    """Документ сотрудника: справка-объективка, приказ, прочее."""

    id: int
    user_id: int
    kind: str = "profile"
    name: str = ""
    size: int = 0
    path: str = ""
    note: str = ""
    uploaded_by: int | None = None
    uploaded_by_name: str = ""
    created_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PersonFile":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            kind=_col(row, "kind", "profile") or "profile",
            name=row["name"],
            size=int(_col(row, "size", 0) or 0),
            path=_col(row, "path", "") or "",
            note=_col(row, "note", "") or "",
            uploaded_by=_col(row, "uploaded_by", None),
            uploaded_by_name=_col(row, "uploaded_by_name", "") or "",
            created_at=row["created_at"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "kind": self.kind,
            "kind_title": PERSON_FILE_TITLES.get(self.kind, self.kind),
            "name": self.name,
            "size": self.size,
            "note": self.note,
            "uploaded_by": self.uploaded_by,
            "uploaded_by_name": short_name(self.uploaded_by_name),
            "created_at": self.created_at,
        }


@dataclass
class Absence:
    """Период отсутствия или дежурства сотрудника."""

    id: int
    user_id: int
    kind: str
    date_from: str
    date_to: str
    #: Где человек в эти дни: узел, аппаратная, объект, часть. Свободный
    #: текст: мест в отделе больше, чем можно перечислить справочником, и
    #: заставлять выбирать из списка «прочее» значит терять сведения.
    place: str = ""
    note: str = ""
    created_by: int | None = None
    created_at: str = ""
    #: ФИО, должность и группа подтягиваются выборкой со связкой.
    full_name: str = ""
    role: str = ""
    team: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Absence":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            kind=row["kind"],
            date_from=row["date_from"],
            date_to=row["date_to"],
            place=_col(row, "place", "") or "",
            note=_col(row, "note", ""),
            created_by=_col(row, "created_by", None),
            created_at=_col(row, "created_at", ""),
            full_name=_col(row, "full_name", "") or "",
            role=_col(row, "role", "") or "",
            team=_col(row, "team", "") or "",
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "kind": self.kind,
            "kind_title": ABSENCE_TITLES.get(self.kind, self.kind),
            "date_from": self.date_from,
            "date_to": self.date_to,
            "place": self.place,
            "note": self.note,
            "present": self.kind in PRESENT_KINDS,
            "full_name": short_name(self.full_name),
            "role": self.role,
            "role_title": ROLE_TITLES.get(self.role, self.role),
            "team": self.team,
            "created_by": self.created_by,
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
