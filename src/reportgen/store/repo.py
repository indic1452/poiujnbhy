"""Репозитории: весь SQL системы собран здесь.

Слои выше (веб, приём документов, поиск, датасет) работают только через эти
классы и не пишут SQL сами — так схему можно менять в одном месте.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
from array import array
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from ..corpus import Chunk
from ..retrieval import tokenize
from .db import Database, utcnow
from .models import (
    ADMIN_ROLES,
    OPEN_CASE_STATUSES,
    ROLES,
    SEARCHABLE_STATUSES,
    Absence,
    AuditEntry,
    ChatAttachment,
    Case,
    CaseFile,
    CaseNote,
    Chat,
    ChatMessage,
    Document,
    EditPair,
    Notice,
    PersonFile,
    Report,
    ReportSection,
    TalkFile,
    TalkMessage,
    User,
    rows_to,
    short_name,
)

log = logging.getLogger(__name__)

# Предел числа параметров в одном запросе SQLite (в старых сборках — 999).
SQL_PARAM_BATCH = 400


def _batched(items: Sequence[Any], size: int = SQL_PARAM_BATCH) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1


# --------------------------------------------------------------- пароли ---

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


# ------------------------------------------------------------- векторы ----

def pack_vector(values: Sequence[float]) -> bytes:
    return array("f", values).tobytes()


def unpack_vector(blob: bytes) -> List[float]:
    values = array("f")
    values.frombytes(blob)
    return list(values)


# ---------------------------------------------------------- репозитории ---

class UserRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, login: str, password: str, full_name: str = "",
               role: str = "engineer", department: str = "", team: str = "",
               approved: bool = True) -> User:
        """Завести сотрудника.

        department — не «в каком отделе работает»: работают все в одном, это
        и есть система. Здесь подразделение, в котором человек стоит по штату,
        если оно не то же самое. Колонка называется department по
        историческим причинам; в интерфейсе поле подписано «По штату».
        """
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO users(login, full_name, role, department, team, "
                "password_hash, active, approved, created_at) VALUES(?,?,?,?,?,?,1,?,?)",
                (login.strip().lower(), full_name, role, department.strip(), team.strip(),
                 hash_password(password), 1 if approved else 0, utcnow()),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[arg-type]

    def get(self, user_id: int) -> User | None:
        row = self.db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return User.from_row(row) if row else None

    def by_login(self, login: str) -> User | None:
        row = self.db.query_one("SELECT * FROM users WHERE login = ?", (login.strip().lower(),))
        return User.from_row(row) if row else None

    def authenticate(self, login: str, password: str) -> User | None:
        row = self.db.query_one("SELECT * FROM users WHERE login = ?", (login.strip().lower(),))
        if row is None or not row["active"]:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return User.from_row(row)

    def set_password(self, user_id: int, password: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )

    #: Поля, которые человек правит сам о себе. Отдельно от должности и
    #: отдела: их назначает начальник, а телефоны и кабинет — своё дело.
    CONTACT_FIELDS = ("phone_mobile", "phone_open", "phone_secure", "room",
                      "phone", "ext_no", "email")

    def update(self, user_id: int, full_name: str | None = None,
               role: str | None = None, department: str | None = None,
               team: str | None = None, **contacts: str | None) -> "User | None":
        """Изменить ФИО, должность, отдел, группу и контакты.

        None оставляет поле как было. Контакты передаются по имени колонки —
        телефоны (мобильный, открытый, режимный) и кабинет.
        """
        fields, values = [], []
        if full_name is not None:
            fields.append("full_name = ?")
            values.append(full_name.strip())
        if role is not None:
            fields.append("role = ?")
            values.append(role)
        if department is not None:
            fields.append("department = ?")
            values.append(department.strip())
        if team is not None:
            fields.append("team = ?")
            values.append(team.strip())
        for name in self.CONTACT_FIELDS:
            value = contacts.get(name)
            if value is not None:
                fields.append(f"{name} = ?")
                values.append(str(value).strip())
        if fields:
            values.append(user_id)
            with self.db.transaction() as connection:
                connection.execute(
                    f"UPDATE users SET {', '.join(fields)} WHERE id = ?", tuple(values)
                )
        return self.get(user_id)

    def count_admins(self, active_only: bool = True) -> int:
        """Сколько администраторов осталось.

        Нужно, чтобы нельзя было снять роль с последнего: иначе управлять
        пользователями станет некому, а на изолированной машине это чинится
        только командной строкой.
        """
        marks = ", ".join("?" for _ in ADMIN_ROLES)
        where = f"role IN ({marks})" + (" AND active = 1" if active_only else "")
        return int(self.db.scalar(
            f"SELECT count(*) FROM users WHERE {where}", tuple(ADMIN_ROLES)) or 0)

    def approve(self, user_id: int, by_user_id: int | None = None) -> "User | None":
        """Одобрить заявку: человек получает доступ."""
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE users SET approved = 1, approved_by = ?, approved_at = ? "
                "WHERE id = ?", (by_user_id, utcnow(), user_id))
        return self.get(user_id)

    def delete(self, user_id: int) -> None:
        """Убрать учётную запись целиком. Только для отклонённых заявок.

        Отклонённая заявка — это не сотрудник: держать её в списке отдела
        значит копить мусор, по которому никто не работает. Отключение
        (``set_active``) для другого случая — человек был и ушёл.
        """
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def pending(self) -> List[User]:
        """Заявки, ждущие одобрения. Старые сверху: очередь есть очередь."""
        return rows_to(User, self.db.query(
            "SELECT * FROM users WHERE approved = 0 ORDER BY created_at, id"))

    def set_active(self, user_id: int, active: bool) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))

    def list_all(self, active_only: bool = False, staff_only: bool = False) -> List[User]:
        """Личный состав. Сортировка — по старшинству должности, потом по ФИО:
        так список читается как штатное расписание, а не как выгрузка.

        ``staff_only`` убирает учётные записи, которые человеком отдела не
        являются: служебную запись режима без входа и создателя системы.
        Создатель — это тот, кто систему поставил и правит настройки; в
        расходе он не дежурит, писем за ним не числится, и в списках отдела
        он только сбивает счёт: «12 человек в строю» превращалось в 13.
        В разделе «Сотрудники» он, наоборот, обязан быть виден — это
        перечень учётных записей, и управлять невидимой записью нельзя.
        """
        order = "CASE role " + " ".join(
            f"WHEN '{role}' THEN {index}" for index, role in enumerate(ROLES)
        ) + f" ELSE {len(ROLES)} END, full_name, login"
        # Неодобренная заявка — ещё не сотрудник: в списках отдела, в выборе
        # исполнителя и в расходе ей не место, пока её не признали.
        clauses = ["approved = 1"]
        if active_only:
            clauses.append("active = 1")
        if staff_only:
            clauses.append("role <> 'owner'")
            clauses.append("login <> 'local'")
        where = " WHERE " + " AND ".join(clauses)
        return rows_to(User, self.db.query(f"SELECT * FROM users{where} ORDER BY {order}"))

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM users") or 0)


class SessionRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, user_id: int, ttl_hours: int = 12, user_agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO sessions(token, user_id, created_at, expires_at, user_agent) "
                "VALUES(?,?,?,?,?)",
                (token, user_id, utcnow(), expires.isoformat(timespec="seconds"), user_agent[:200]),
            )
        return token

    def resolve(self, token: str) -> User | None:
        row = self.db.query_one(
            "SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token = ?",
            (token,),
        )
        if row is None or not row["active"]:
            return None
        if row["expires_at"] < utcnow():
            self.delete(token)
            return None
        return User.from_row(row)

    def delete(self, token: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def delete_for_user(self, user_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def purge_expired(self) -> int:
        with self.db.transaction() as connection:
            cursor = connection.execute("DELETE FROM sessions WHERE expires_at < ?", (utcnow(),))
        return cursor.rowcount


class DocumentRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, doc_id: str, doc_type: str, title: str, source_path: str,
               sha256: str, meta: Dict[str, Any] | None = None, domain: str = "",
               status: str = "current", superseded_by: str = "",
               year: int | None = None, size: int | None = None,
               mtime_ns: int | None = None) -> Document:
        # Колонка confidentiality («гриф») осталась в таблице от прежней
        # версии и здесь не заполняется: значение по умолчанию поставит
        # SQLite. Удалять колонку на работающей установке незачем — она
        # никем не читается.
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO documents(doc_id, doc_type, title, source_path, sha256, "
                "meta_json, domain, status, superseded_by, year, "
                "size, mtime_ns, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET doc_type=excluded.doc_type, "
                "title=excluded.title, source_path=excluded.source_path, "
                "sha256=excluded.sha256, "
                "meta_json=excluded.meta_json, domain=excluded.domain, "
                "status=excluded.status, superseded_by=excluded.superseded_by, "
                "year=excluded.year, size=excluded.size, mtime_ns=excluded.mtime_ns, "
                # Файл изменился — прежние чанки устарели, отметку об индексации
                # снимаем до того, как ChunkRepo их перезапишет.
                "indexed_at=CASE WHEN documents.sha256 = excluded.sha256 "
                "THEN documents.indexed_at ELSE NULL END, "
                "chunk_count=CASE WHEN documents.sha256 = excluded.sha256 "
                "THEN documents.chunk_count ELSE 0 END",
                (doc_id, doc_type, title, source_path, sha256,
                 json.dumps(meta or {}, ensure_ascii=False), domain, status,
                 superseded_by, year, size, mtime_ns, utcnow()),
            )
        document = self.by_doc_id(doc_id)
        assert document is not None
        return document

    def touch(self, doc_id: str, size: int, mtime_ns: int) -> None:
        """Запомнить размер и дату файла без переиндексации.

        Нужно там, где документ пропущен по совпадению SHA-256: сам хеш уже
        стоил чтения всего файла, и если не записать дешёвые приметы, каждый
        следующий прогон будет читать библиотеку целиком заново. Особенно это
        важно для баз, заполненных до появления этих колонок.
        """
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE documents SET size = ?, mtime_ns = ? WHERE doc_id = ?",
                (int(size), int(mtime_ns), doc_id),
            )

    def by_doc_id(self, doc_id: str) -> Document | None:
        row = self.db.query_one("SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
        return Document.from_row(row) if row else None

    def by_sha256(self, sha256: str) -> Document | None:
        row = self.db.query_one("SELECT * FROM documents WHERE sha256 = ?", (sha256,))
        return Document.from_row(row) if row else None

    def _filters(self, doc_type: str | None, domain: str | None,
                 status: str | None, query: str = "") -> tuple:
        clauses, params = [], []
        if doc_type:
            clauses.append("doc_type = ?")
            params.append(doc_type)
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if status:
            clauses.append("status = ?")
            params.append(status)
        needle = " ".join(str(query or "").split())
        if needle:
            # Поиск по названию и опознавателю — по кускам слова: в
            # библиотеке отдела названия длинные («Методика контроля
            # излучения передатчика»), и целиком их никто не набирает.
            clauses.append("(title LIKE ? OR doc_id LIKE ?)")
            params.extend([f"%{needle}%", f"%{needle}%"])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def count_all(self, doc_type: str | None = None, domain: str | None = None,
                  status: str | None = None, query: str = "") -> int:
        where, params = self._filters(doc_type, domain, status, query)
        return int(self.db.scalar(
            f"SELECT count(*) FROM documents{where}", params) or 0)

    def list(self, doc_type: str | None = None, domain: str | None = None,
             status: str | None = None, query: str = "",
             limit: int | None = None, offset: int = 0) -> List[Document]:
        """Документы библиотеки. ``limit`` — страница, без него всё сразу.

        Библиотека отдела — тринадцать с половиной тысяч документов. Раньше
        экран запрашивал их все и рисовал одной таблицей: браузер получал
        несколько мегабайт JSON и подвисал на минуту, а человеку в этот миг
        нужны были три документа, которые он ищет по названию.
        """
        where, params = self._filters(doc_type, domain, status, query)
        sql = f"SELECT * FROM documents{where} ORDER BY doc_type, title"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = list(params) + [int(limit), max(0, int(offset))]
        rows = self.db.query(sql, params)
        return rows_to(Document, rows)

    def set_domain(self, doc_id: str, domain: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE documents SET domain = ? WHERE doc_id = ?", (domain, doc_id)
            )
            connection.execute(
                "UPDATE chunks SET domain = ? WHERE document_id = "
                "(SELECT id FROM documents WHERE doc_id = ?)",
                (domain, doc_id),
            )

    def set_status(self, doc_id: str, status: str, superseded_by: str = "") -> None:
        """Отметить актуальность документа.

        Заменённый и архивный документ исчезает из поиска: помощник и отчёты
        не должны цитировать отменённую редакцию стандарта как действующую.
        Сам документ остаётся в библиотеке — он нужен, чтобы разбирать старые
        обращения, выполненные по прежней редакции.
        """
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE documents SET status = ?, superseded_by = ? WHERE doc_id = ?",
                (status, superseded_by, doc_id),
            )
            connection.execute(
                "UPDATE chunks SET status = ? WHERE document_id = "
                "(SELECT id FROM documents WHERE doc_id = ?)",
                (status, doc_id),
            )

    def statuses(self) -> Dict[str, int]:
        rows = self.db.query(
            "SELECT status, count(*) AS documents FROM documents GROUP BY status"
        )
        return {row["status"]: row["documents"] for row in rows}

    def domains(self) -> Dict[str, int]:
        rows = self.db.query(
            "SELECT domain, count(*) AS documents FROM documents "
            "GROUP BY domain ORDER BY documents DESC"
        )
        return {row["domain"] or "не указано": row["documents"] for row in rows}

    def catalog(self) -> List[Dict[str, Any]]:
        """Опись библиотеки: по строке на документ, одним запросом.

        Нужна помощнику: без неё он знает о библиотеке только то, что попало
        в найденные фрагменты, и на вопрос «а что у нас есть по спутникам»
        отвечать ему нечем. Берём лишь то, что помещается в подсказку:
        направление, тип, название, год, состояние, объём.

        Устаревшие (``superseded``) документы не отдаём: направлять к ним
        инженера незачем, а место в окне они занимают наравне с
        действующими.
        """
        rows = self.db.query(
            "SELECT domain, doc_type, doc_id, title, year, status, chunk_count "
            "FROM documents WHERE status = 'current' "
            "ORDER BY domain, doc_type, chunk_count DESC, title"
        )
        return [
            {
                "domain": row["domain"] or "",
                "doc_type": row["doc_type"],
                "doc_id": row["doc_id"],
                "title": row["title"],
                "year": row["year"],
                "status": row["status"],
                "chunks": row["chunk_count"] or 0,
            }
            for row in rows
        ]

    def mark_indexed(self, doc_id: str, chunk_count: int) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE documents SET chunk_count = ?, indexed_at = ? WHERE doc_id = ?",
                (chunk_count, utcnow(), doc_id),
            )

    def clear_all(self) -> int:
        """Стереть библиотеку целиком: документы, фрагменты, векторы.

        Нужно для загрузки с нуля. Обращения, отчёты, правки инженеров и
        учётные записи НЕ трогаются: они живут в других таблицах и к
        библиотеке отношения не имеют. Ссылки на источники в уже готовых
        отчётах тоже целы — цитаты хранятся в самом отчёте, в приложении.
        """
        removed = int(self.db.scalar("SELECT count(*) FROM documents") or 0)
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM embeddings")
            connection.execute("DELETE FROM chunks_fts")
            connection.execute("DELETE FROM chunks")
            connection.execute("DELETE FROM documents")
        return removed

    def delete(self, doc_id: str) -> None:
        document = self.by_doc_id(doc_id)
        if document is None:
            return
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM embeddings WHERE chunk_uid IN "
                "(SELECT chunk_uid FROM chunks WHERE document_id = ?)",
                (document.id,),
            )
            connection.execute(
                "DELETE FROM chunks_fts WHERE chunk_uid IN "
                "(SELECT chunk_uid FROM chunks WHERE document_id = ?)",
                (document.id,),
            )
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document.id,))
            connection.execute("DELETE FROM documents WHERE id = ?", (document.id,))

    def stats(self) -> Dict[str, Dict[str, int]]:
        rows = self.db.query(
            "SELECT doc_type, count(*) AS documents, coalesce(sum(chunk_count), 0) AS chunks "
            "FROM documents GROUP BY doc_type ORDER BY doc_type"
        )
        return {
            row["doc_type"]: {"documents": row["documents"], "chunks": row["chunks"]}
            for row in rows
        }


# Точку сохраняем: номер пункта «5.3.2» ищется как фраза, а не как три числа.
_FTS_SPECIAL = re.compile(r'[^\w.]', re.UNICODE)


def fts_stemmed(*parts: Any) -> str:
    """Текст для полнотекстового указателя: приведён к основам слов.

    unicode61 русской морфологии не знает: «помеха» и «помехи» для него
    разные слова. Поэтому в указатель кладём уже разобранный текст, а
    запрос разбираем тем же способом — тогда одно находит другое.
    """
    text = " ".join(str(part or "") for part in parts)
    return " ".join(_FTS_SPECIAL.sub("", token) for token in tokenize(text)
                    if _FTS_SPECIAL.sub("", token))


def fts_match(query: str) -> str:
    """Запрос к FTS5 из строки, которую ввёл человек. Пусто — искать нечего.

    Слова соединяем по «и». У библиотеки они соединены по «или», и это
    там правильно: выдача ранжируется bm25 и обрезается, лишнее тонет
    внизу. Список писем не ранжируется вовсе — он сортируется по сроку,
    как нужно отделу. С «или» полный входящий номер «ВХ-2026-0423»
    распадался бы на слова и вытаскивал половину журнала.
    """
    terms = fts_stemmed(query).split()
    if not terms:
        return ""
    return " AND ".join(f'"{term}"' for term in terms)


class ChunkRepo:
    """Чанки и лексический индекс FTS5 поверх стеммированного текста."""

    def __init__(self, db: Database):
        self.db = db

    def replace_for_document(self, document: Document, chunks: Sequence[Chunk]) -> int:
        with self.db.transaction() as connection:
            connection.execute(
                "DELETE FROM embeddings WHERE chunk_uid IN "
                "(SELECT chunk_uid FROM chunks WHERE document_id = ?)",
                (document.id,),
            )
            connection.execute(
                "DELETE FROM chunks_fts WHERE chunk_uid IN "
                "(SELECT chunk_uid FROM chunks WHERE document_id = ?)",
                (document.id,),
            )
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document.id,))
            for ordinal, chunk in enumerate(chunks):
                connection.execute(
                    "INSERT INTO chunks(chunk_uid, document_id, ord, doc_type, title_path, "
                    "text, meta_json, domain, status, year) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        chunk.chunk_id,
                        document.id,
                        ordinal,
                        chunk.doc_type,
                        json.dumps(chunk.title_path, ensure_ascii=False),
                        chunk.text,
                        json.dumps(chunk.meta, ensure_ascii=False),
                        document.domain,
                        document.status,
                        document.year,
                    ),
                )
                connection.execute(
                    "INSERT INTO chunks_fts(stemmed, chunk_uid, doc_type) VALUES(?,?,?)",
                    (" ".join(tokenize(chunk.indexed_text)), chunk.chunk_id, chunk.doc_type),
                )
            # Счётчик и отметка об индексации — в той же транзакции, что и чанки:
            # иначе при сбое документ выглядел бы проиндексированным без чанков.
            connection.execute(
                "UPDATE documents SET chunk_count = ?, indexed_at = ? WHERE id = ?",
                (len(chunks), utcnow(), document.id),
            )
        return len(chunks)

    @staticmethod
    def _to_chunk(row) -> Chunk:
        keys = row.keys()
        meta = json.loads(row["meta_json"])
        if "domain" in keys and row["domain"]:
            meta.setdefault("domain", row["domain"])
        if "status" in keys and row["status"]:
            meta.setdefault("status", row["status"])
        if "year" in keys and row["year"]:
            meta.setdefault("year", int(row["year"]))
        return Chunk(
            chunk_id=row["chunk_uid"],
            doc_id=row["doc_id"] if "doc_id" in keys else row["chunk_uid"].split("#")[0],
            doc_type=row["doc_type"],
            title_path=json.loads(row["title_path"]),
            text=row["text"],
            meta=meta,
        )

    def get(self, chunk_uid: str) -> Chunk | None:
        row = self.db.query_one(
            "SELECT c.*, d.doc_id AS doc_id FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE c.chunk_uid = ?",
            (chunk_uid,),
        )
        return self._to_chunk(row) if row else None

    def get_many(self, chunk_uids: Sequence[str]) -> List[Chunk]:
        if not chunk_uids:
            return []
        by_uid: Dict[str, Chunk] = {}
        for batch in _batched(list(chunk_uids)):
            placeholders = ",".join("?" * len(batch))
            rows = self.db.query(
                f"SELECT c.*, d.doc_id AS doc_id FROM chunks c "
                f"JOIN documents d ON d.id = c.document_id WHERE c.chunk_uid IN ({placeholders})",
                tuple(batch),
            )
            by_uid.update({row["chunk_uid"]: self._to_chunk(row) for row in rows})
        return [by_uid[uid] for uid in chunk_uids if uid in by_uid]

    def for_document(self, document_id: int, limit: int = 400,
                     offset: int = 0) -> List[Chunk]:
        """Фрагменты одного документа по порядку — так, как их видит поиск.

        Нужны инженеру для проверки качества разбора: по ним сразу видно,
        распался ли скан на осмысленные куски или в базу уехал мусор.
        ``offset`` — чтобы можно было дочитать длинный том: в библиотеке
        отдела попадаются книги в полторы тысячи фрагментов, и первые
        четыреста из них — это ещё оглавление.
        """
        rows = self.db.query(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY ord "
            "LIMIT ? OFFSET ?",
            (document_id, int(limit), max(0, int(offset))),
        )
        return [self._to_chunk(row) for row in rows]

    def count_for_document(self, document_id: int) -> int:
        return int(self.db.scalar(
            "SELECT count(*) FROM chunks WHERE document_id = ?",
            (document_id,)) or 0)

    def find_sections(self, document_id: int, needle: str,
                      limit: int = 8) -> List[Chunk]:
        """Фрагменты документа, у которых в крошках встречается ``needle``.

        Помощник просит «ЧИТАТЬ: том | Глава 12». Раньше главу искали в
        первых четырёхстах фрагментах, поднятых в память: в книге на
        полторы тысячи фрагментов двенадцатой главы там просто нет, и
        помощник честно отвечал «раздела не нашёл» о разделе, который в
        документе есть. Ищем в базе и по всему документу.
        """
        needle = " ".join(str(needle or "").split())
        if not needle:
            return []
        # rulower, а не lower: встроенный lower() в SQLite знает только
        # латиницу, и «Глава» с «глава» для него разные слова.
        rows = self.db.query(
            "SELECT * FROM chunks WHERE document_id = ? "
            "AND rulower(title_path) LIKE ? ORDER BY ord LIMIT ?",
            (document_id, f"%{needle.lower()}%", int(limit)),
        )
        return [self._to_chunk(row) for row in rows]

    def neighbours(self, chunk_uids: Sequence[str], radius: int = 1) -> Dict[str, List[Chunk]]:
        """Соседние фрагменты тех же документов — по radius в каждую сторону.

        Поиск возвращает кусок в 1800 знаков, а таблица допусков или описание
        поля кадра в него не помещаются: начало осталось в предыдущем куске,
        продолжение — в следующем. Модель видит середину и отвечает по
        середине. Соседи достраивают связный отрывок.

        Ключ ответа — chunk_uid найденного фрагмента, значение — соседи по
        порядку следования в документе (сам фрагмент не включён).
        """
        if not chunk_uids or radius <= 0:
            return {}
        marks = ", ".join("?" for _ in chunk_uids)
        anchors = self.db.query(
            f"SELECT chunk_uid, document_id, ord FROM chunks WHERE chunk_uid IN ({marks})",
            tuple(chunk_uids),
        )
        result: Dict[str, List[Chunk]] = {}
        for anchor in anchors:
            rows = self.db.query(
                "SELECT c.*, d.doc_id AS doc_id FROM chunks c "
                "JOIN documents d ON d.id = c.document_id "
                "WHERE c.document_id = ? AND c.ord BETWEEN ? AND ? AND c.ord <> ? "
                "ORDER BY c.ord",
                (anchor["document_id"], anchor["ord"] - radius,
                 anchor["ord"] + radius, anchor["ord"]),
            )
            if rows:
                result[anchor["chunk_uid"]] = [self._to_chunk(row) for row in rows]
        return result

    def outline(self, doc_ids: Sequence[str], limit: int = 40) -> Dict[str, List[str]]:
        """Оглавление документов: заголовки разделов по порядку.

        Без него модель видит несколько кусков и не знает, что ещё есть в
        документе. С оглавлением она может сказать «порог задан в разделе 5.2,
        а методика измерения — в приложении Б» и подсказать, куда смотреть.
        """
        if not doc_ids:
            return {}
        marks = ", ".join("?" for _ in doc_ids)
        rows = self.db.query(
            "SELECT d.doc_id AS doc_id, c.ord AS ord, c.title_path AS title_path "
            f"FROM chunks c JOIN documents d ON d.id = c.document_id "
            f"WHERE d.doc_id IN ({marks}) ORDER BY d.doc_id, c.ord",
            tuple(doc_ids),
        )
        result: Dict[str, List[str]] = {}
        for row in rows:
            path = json.loads(row["title_path"] or "[]")
            if not path:
                continue
            # Первый элемент крошек — название документа, в оглавлении лишний.
            heading = " → ".join(path[1:]) if len(path) > 1 else path[0]
            bucket = result.setdefault(row["doc_id"], [])
            if heading and (not bucket or bucket[-1] != heading) and len(bucket) < limit:
                bucket.append(heading)
        return result

    def all_uids(self) -> List[str]:
        """Только опознаватели фрагментов, без текстов.

        Построение векторов раньше начиналось с ``all_chunks()`` — то есть
        поднимало в память ВСЮ библиотеку целиком. На корпусе отдела это
        полмиллиона фрагментов и больше гигабайта текста ради того, чтобы
        отправлять их пачками по шестнадцать. Опознаватели весят на два
        порядка меньше, а тексты берутся пачкой перед самой отправкой.
        """
        return [str(row["chunk_uid"]) for row in
                self.db.query("SELECT chunk_uid FROM chunks ORDER BY id")]

    def all_chunks(self, limit: int | None = None) -> List[Chunk]:
        sql = ("SELECT c.*, d.doc_id AS doc_id FROM chunks c "
               "JOIN documents d ON d.id = c.document_id ORDER BY c.id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._to_chunk(row) for row in self.db.query(sql)]

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM chunks") or 0)

    def search_lexical(
        self, query: str, limit: int = 50, doc_types: Iterable[str] | None = None,
        domains: Iterable[str] | None = None,
        statuses: Iterable[str] | None = SEARCHABLE_STATUSES,
    ) -> List[Tuple[str, float]]:
        """Поиск по FTS5. Возвращает пары (chunk_uid, оценка), лучшие первыми.

        Фильтры (тип документа, направление) применяются соединением с таблицей
        chunks: виртуальную таблицу FTS5 нельзя расширять новыми колонками,
        а направлений со временем станет больше.
        """
        terms = [_FTS_SPECIAL.sub("", term) for term in tokenize(query)]
        terms = [term for term in terms if term]
        if not terms:
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        params: List[Any] = [match]
        sql = ("SELECT f.chunk_uid AS chunk_uid, bm25(chunks_fts) AS rank "
               "FROM chunks_fts f JOIN chunks c ON c.chunk_uid = f.chunk_uid "
               "WHERE chunks_fts MATCH ?")
        types = list(doc_types or [])
        if types:
            sql += f" AND c.doc_type IN ({','.join('?' * len(types))})"
            params.extend(types)
        areas = [d for d in (domains or []) if d]
        if areas:
            sql += f" AND c.domain IN ({','.join('?' * len(areas))})"
            params.extend(areas)
        allowed_statuses = [s for s in (statuses or []) if s]
        if allowed_statuses:
            sql += f" AND c.status IN ({','.join('?' * len(allowed_statuses))})"
            params.extend(allowed_statuses)
        sql += " ORDER BY rank LIMIT ?"
        params.append(int(limit))
        # bm25() в SQLite тем меньше, чем релевантнее; переводим в «больше = лучше».
        return [(row["chunk_uid"], -float(row["rank"])) for row in self.db.query(sql, params)]


class VectorRepo:
    def __init__(self, db: Database):
        self.db = db

    def put_many(self, model: str, vectors: Dict[str, Sequence[float]]) -> None:
        rows = [
            (chunk_uid, model, len(vector), pack_vector(vector))
            for chunk_uid, vector in vectors.items()
        ]
        with self.db.transaction() as connection:
            connection.executemany(
                "INSERT INTO embeddings(chunk_uid, model, dim, vector) VALUES(?,?,?,?) "
                "ON CONFLICT(chunk_uid) DO UPDATE SET model=excluded.model, "
                "dim=excluded.dim, vector=excluded.vector",
                rows,
            )

    def get_many(self, chunk_uids: Sequence[str]) -> Dict[str, List[float]]:
        if not chunk_uids:
            return {}
        found: Dict[str, List[float]] = {}
        for batch in _batched(list(chunk_uids)):
            placeholders = ",".join("?" * len(batch))
            rows = self.db.query(
                f"SELECT chunk_uid, vector FROM embeddings WHERE chunk_uid IN ({placeholders})",
                tuple(batch),
            )
            found.update({row["chunk_uid"]: unpack_vector(row["vector"]) for row in rows})
        return found

    def all_vectors(self, model: str | None = None) -> Tuple[List[str], List[List[float]]]:
        if model:
            rows = self.db.query(
                "SELECT chunk_uid, vector FROM embeddings WHERE model = ? ORDER BY chunk_uid",
                (model,),
            )
        else:
            rows = self.db.query("SELECT chunk_uid, vector FROM embeddings ORDER BY chunk_uid")
        uids = [row["chunk_uid"] for row in rows]
        vectors = [unpack_vector(row["vector"]) for row in rows]
        return uids, vectors

    def load_index(self, model: str | None = None, batch: int = 20000) -> Any:
        """Матрица векторов корпуса — сразу в float32, без списков Python.

        ``all_vectors`` собирает ``List[List[float]]``: на корпусе отдела это
        полмиллиона списков по тысяче чисел, около восемнадцати гигабайт
        объектов. Здесь строки распаковываются пачками прямо в готовую
        матрицу: та же библиотека занимает 2,3 ГБ, и лишней копии в памяти
        не возникает ни на миг.
        """
        from ..embeddings import VectorIndex, build_index, normalize_rows  # noqa: PLC0415

        where, params = ("WHERE model = ?", (model,)) if model else ("", ())
        total = int(self.db.scalar(
            f"SELECT count(*) FROM embeddings {where}", params) or 0)
        if not total:
            return VectorIndex([], [], 0)

        first = self.db.query(
            f"SELECT vector FROM embeddings {where} ORDER BY chunk_uid LIMIT 1", params)
        dim = len(unpack_vector(first[0]["vector"])) if first else 0
        if not dim:
            return VectorIndex([], [], 0)

        try:
            import numpy as np                      # noqa: PLC0415
        except ImportError:
            uids, vectors = self.all_vectors(model)
            return build_index(uids, vectors)

        matrix = np.empty((total, dim), dtype="float32")
        uids: List[str] = []
        row = 0
        for start in range(0, total, batch):
            rows = self.db.query(
                f"SELECT chunk_uid, vector FROM embeddings {where} "
                f"ORDER BY chunk_uid LIMIT ? OFFSET ?",
                tuple(params) + (int(batch), int(start)))
            for item in rows:
                values = array("f")
                values.frombytes(item["vector"])
                if len(values) != dim:
                    # Вектор чужой модели или битая строка: место в матрице
                    # ему не отводим, иначе поиск сравнивал бы несравнимое.
                    continue
                matrix[row] = values
                uids.append(str(item["chunk_uid"]))
                row += 1
        if row != total:
            matrix = matrix[:row]
        normalize_rows(matrix)
        return VectorIndex(uids, matrix, dim)

    def missing(self, chunk_uids: Sequence[str]) -> List[str]:
        present = set(self.get_many(chunk_uids))
        return [uid for uid in chunk_uids if uid not in present]

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM embeddings") or 0)

    def clear(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM embeddings")


def refresh_case_index(db: "Database", case_ref: int) -> None:
    """Пересобрать строку письма в поисковом указателе.

    Строка целиком: реквизиты письма плюс текст всех редакций отчёта.
    Письма нет — строка убирается. Живёт отдельной функцией, потому что
    зовут её из двух мест: репозитория писем и репозитория отчётов. Текст
    меняют оба, и обновление указателя должно стоять там же, где запись, —
    иначе его рано или поздно забудут (так и вышло со сдачей файлом:
    отчёт лежал в базе, а поиск его не находил).
    """
    row = db.query_one(
        "SELECT case_id, title, customer, incoming_no, outgoing_no, tc_no, "
        "order_no, note, outgoing_note, facts_json FROM cases WHERE id = ?",
        (case_ref,))
    with db.transaction() as connection:
        connection.execute("DELETE FROM cases_fts WHERE case_ref = ?", (case_ref,))
        if row is None:
            return
        texts = [row["case_id"], row["title"], row["customer"],
                 row["incoming_no"], row["outgoing_no"], row["tc_no"],
                 row["order_no"], row["note"], row["outgoing_note"],
                 # Суть обращения и ключевые слова из факт-пакета: по ним
                 # ищут письмо, у которого отчёта ещё нет вовсе.
                 row["facts_json"]]
        for report in connection.execute(
                "SELECT markdown, file_name, review_note FROM reports "
                "WHERE case_ref = ?", (case_ref,)):
            texts.append(report["markdown"])
            # Имя сданного файла: у сканированного PDF текст не извлекается,
            # и без имени такое письмо не найти ничем. Замечание проверяющего
            # тоже ищут — «что мне тогда вернули по этому письму».
            texts.append(report["file_name"])
            texts.append(report["review_note"])
        # Приложенные к письму бумаги: скан самого письма, схема линии,
        # журнал измерений. Их текст разбирают при загрузке, и искать по
        # нему нужно так же, как по отчёту.
        for attachment in connection.execute(
                "SELECT name, text, note FROM case_files WHERE case_ref = ?",
                (case_ref,)):
            texts.append(attachment["name"])
            texts.append(attachment["text"])
            texts.append(attachment["note"])
        for section in connection.execute(
                "SELECT s.text FROM report_sections s JOIN reports r ON r.id = s.report_id "
                "WHERE r.case_ref = ?", (case_ref,)):
            texts.append(section["text"])
        stemmed = fts_stemmed(*texts)
        if stemmed:
            connection.execute(
                "INSERT INTO cases_fts(stemmed, case_ref) VALUES(?, ?)",
                (stemmed, case_ref))


class CaseFileRepo:
    """Бумаги, приложенные к письму.

    Файл лежит на диске, а строка здесь хранит имя, размер, путь и
    разобранный текст. Текст нужен поиску: письмо ищут и по словам из
    приложенной схемы, а не только по своим реквизитам.
    """

    _SELECT = (
        "SELECT f.*, coalesce(u.full_name, u.login, '') AS uploaded_by_name "
        "FROM case_files f LEFT JOIN users u ON u.id = f.uploaded_by"
    )

    def __init__(self, db: Database):
        self.db = db

    def add(self, case_ref: int, name: str, path: str, size: int = 0,
            text: str = "", note: str = "", user_id: int | None = None,
            stage: str = "incoming") -> CaseFile:
        now = utcnow()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO case_files(case_ref, stage, name, size, path, text, "
                "note, uploaded_by, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (case_ref, stage, name, size, path, text, note, user_id, now),
            )
        # Текст приложения попадает в поиск сразу: положить бумагу и не
        # найти по ней письмо — ровно тот случай, ради которого приложения
        # и заводили.
        refresh_case_index(self.db, case_ref)
        return self.get(int(cursor.lastrowid))  # type: ignore[arg-type]

    def get(self, file_id: int) -> CaseFile | None:
        row = self.db.query_one(f"{self._SELECT} WHERE f.id = ?", (file_id,))
        return CaseFile.from_row(row) if row else None

    def list_for_case(self, case_ref: int, stage: str = "") -> List[CaseFile]:
        clause = " WHERE f.case_ref = ?"
        params: List[Any] = [case_ref]
        if stage:
            clause += " AND f.stage = ?"
            params.append(stage)
        return rows_to(CaseFile, self.db.query(
            f"{self._SELECT}{clause} ORDER BY f.stage DESC, f.id", tuple(params)))

    def count_for_case(self, case_ref: int) -> int:
        row = self.db.query_one(
            "SELECT count(*) AS n FROM case_files WHERE case_ref = ?", (case_ref,))
        return int(row["n"]) if row else 0

    def delete(self, file_id: int) -> str:
        """Убрать строку. Возвращает путь к файлу — удалять его решает вызвавший."""
        item = self.get(file_id)
        if item is None:
            return ""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM case_files WHERE id = ?", (file_id,))
        refresh_case_index(self.db, item.case_ref)
        return item.path


class CaseSearchRepo:
    """Полнотекстовый указатель по письмам и их отчётам.

    Одна строка на письмо: реквизиты письма плюс текст всех редакций
    отчёта. Ищут в отделе по-разному — по обрывку входящего номера, по теме
    и по паре слов, которые запомнились из вывода прошлогоднего отчёта.
    Первое находит поиск подстрокой, последнее — только это.

    Строка перестраивается целиком при каждой правке: письмо в отделе
    правят несколько раз за жизнь, а не тысячу, и целая строка надёжнее
    попыток обновить её по частям.
    """

    def __init__(self, db: Database):
        self.db = db

    def refresh(self, case_ref: int) -> None:
        """Пересобрать строку письма. Письма нет — строка убирается."""
        refresh_case_index(self.db, case_ref)

    def rebuild_all(self) -> int:
        """Перестроить указатель целиком. Возвращает число писем.

        Нужно после обновления системы: на базе отдела указателя ещё нет, а
        поиск по тексту должен заработать сразу, без просьбы «переиндексируйте».
        """
        rows = self.db.query("SELECT id FROM cases")
        for row in rows:
            self.refresh(int(row["id"]))
        return len(rows)

    def is_empty(self) -> bool:
        return not int(self.db.scalar("SELECT count(*) FROM cases_fts") or 0)


class CaseRepo:
    def __init__(self, db: Database):
        self.db = db

    #: Выборка письма вместе с ФИО исполнителя: список писем без исполнителя
    #: бесполезен, а второй запрос на строку — это N+1 на ровном месте.
    _SELECT = (
        "SELECT c.*, coalesce(u.full_name, u.login, '') AS assignee_name, "
        "coalesce(s.full_name, s.login, '') AS sent_by_name, "
        # Сколько отчётов заведено по письму. Нужно карточке: состояния
        # «на проверке», «проверен» и «отправлено» даёт ход отчёта, и руками
        # их выставлять нельзя, пока по письму есть отчёты.
        "(SELECT count(*) FROM reports r WHERE r.case_ref = c.id) AS reports_count, "
        # Сколько бумаг приложено к письму. Список показывает скрепку, и
        # без счётчика пришлось бы спрашивать базу отдельно на каждую строку.
        "(SELECT count(*) FROM case_files f WHERE f.case_ref = c.id) AS files_count "
        "FROM cases c LEFT JOIN users u ON u.id = c.assignee_id "
        "LEFT JOIN users s ON s.id = c.sent_by"
    )

    def create(self, case_id: str, report_type: str, facts: Dict[str, Any],
               digest: str = "", title: str = "", customer: str = "",
               user_id: int | None = None, *, incoming_no: str = "",
               incoming_date: str = "", deadline: str = "", priority: str = "normal",
               assignee_id: int | None = None, note: str = "",
               line_type: str = "", tc_no: str = "", tc_date: str = "",
               order_no: str = "", order_date: str = "",
               registrations: int = 0) -> Case:
        now = utcnow()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO cases(case_id, report_type, title, customer, status, "
                "line_type, tc_no, tc_date, order_no, order_date, registrations, "
                "incoming_no, incoming_date, deadline, priority, "
                "assignee_id, note, facts_json, facts_digest, created_by, "
                "created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, report_type, title, customer, "new",
                 line_type, tc_no, tc_date, order_no, order_date, int(registrations or 0),
                 incoming_no, incoming_date, deadline, priority,
                 assignee_id, note,
                 json.dumps(facts, ensure_ascii=False), digest, user_id, now, now),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[arg-type]

    def get(self, case_ref: int) -> Case | None:
        row = self.db.query_one(f"{self._SELECT} WHERE c.id = ?", (case_ref,))
        return Case.from_row(row) if row else None

    def by_case_id(self, case_id: str) -> Case | None:
        row = self.db.query_one(f"{self._SELECT} WHERE c.case_id = ?", (case_id,))
        return Case.from_row(row) if row else None

    def list(self, status: str | None = None, limit: int = 100, offset: int = 0,
             assignee_id: int | None = None, overdue_before: str | None = None,
             deadline_from: str | None = None, deadline_to: str | None = None,
             query: str = "") -> List[Case]:
        """Письма по фильтрам. Сортировка: сначала просроченные и срочные.

        Порядок «по дате правки» показывал наверху то, чего только что
        коснулись, а не то, что горит. Отделу нужно обратное.
        """
        where, params = self._filters(
            status=status, assignee_id=assignee_id, overdue_before=overdue_before,
            deadline_from=deadline_from, deadline_to=deadline_to, query=query,
            prefix="c.")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order = (
            " ORDER BY CASE WHEN c.status IN ('approved', 'archived') THEN 1 ELSE 0 END, "
            "CASE WHEN c.deadline = '' THEN 1 ELSE 0 END, c.deadline, "
            "CASE c.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, "
            "c.updated_at DESC"
        )
        params.extend([limit, offset])
        rows = self.db.query(f"{self._SELECT}{clause}{order} LIMIT ? OFFSET ?", tuple(params))
        return rows_to(Case, rows)

    def update_facts(self, case_ref: int, facts: Dict[str, Any], digest: str,
                     title: str | None = None, customer: str | None = None) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE cases SET facts_json = ?, facts_digest = ?, updated_at = ?, "
                "title = coalesce(?, title), customer = coalesce(?, customer) WHERE id = ?",
                (json.dumps(facts, ensure_ascii=False), digest, utcnow(), title, customer, case_ref),
            )

    def set_status(self, case_ref: int, status: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
                (status, utcnow(), case_ref),
            )

    def update_card(self, case_ref: int, **fields: Any) -> Case | None:
        """Изменить карточку письма: исполнитель, срок, входящий номер и прочее.

        Принимает только известные колонки — остальное молча игнорируется,
        чтобы содержимое запроса из браузера не превращалось в SQL.

        Номер группы хранится в двух местах: колонкой письма и полем факт-пакета,
        откуда он попадает в отчёт. Пишем сразу в оба, иначе правка в
        карточке жила до первого сохранения фактов, а потом молча
        возвращалась к прежнему значению.
        """
        allowed = ("title", "customer", "incoming_no", "incoming_date",
                   "outgoing_no", "outgoing_date", "outgoing_note", "sent_by",
                   "line_type", "tc_no", "tc_date", "order_no", "order_date",
                   "registrations",
                   "deadline", "priority", "assignee_id", "note", "status")
        # Единственная колонка, где пусто — это значение, а не «не передано».
        # Снять исполнителя с письма было нельзя вовсе: None отбрасывался
        # вместе с непереданными полями, API отвечал 200 и писал в журнал, а
        # в базе оставался прежний человек.
        nullable = ("assignee_id", "sent_by")
        parts, values = [], []
        for name in allowed:
            if name not in fields:
                continue
            value = fields[name]
            if value is None and name not in nullable:
                continue
            parts.append(f"{name} = ?")
            values.append(value)
        if not parts:
            return self.get(case_ref)

        facts_json = None
        if fields.get("customer") is not None:
            current = self.get(case_ref)
            if current is not None:
                facts = dict(current.facts)
                facts["group_no"] = fields["customer"]
                # Прежний ключ убираем, иначе в пакете осталось бы два номера
                # и читался бы не тот, который только что вписали.
                facts.pop("customer", None)
                facts_json = json.dumps(facts, ensure_ascii=False)
        if facts_json is not None:
            parts.append("facts_json = ?")
            values.append(facts_json)

        values.extend([utcnow(), case_ref])
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE cases SET {', '.join(parts)}, updated_at = ? WHERE id = ?",
                tuple(values),
            )
        return self.get(case_ref)

    def delete(self, case_ref: int) -> None:
        with self.db.transaction() as connection:
            # Строку поискового указателя убираем руками: внешнего ключа у
            # виртуальной таблицы нет, каскад её не трогает — удалённое
            # письмо осталось бы находиться поиском вечно.
            connection.execute("DELETE FROM cases_fts WHERE case_ref = ?", (case_ref,))
            connection.execute("DELETE FROM cases WHERE id = ?", (case_ref,))

    #: Отбор по строке поиска. Один на список и на счётчик: иначе «показаны
    #: 3 из 12» при поиске врёт — списком одно, числом другое.
    #:
    #: Ищем по всем реквизитам письма: учётный номер, входящий, исходящий,
    #: описание, номер группы, номер ТС, примечание. Плюс отдельно — по
    #: тексту отчётов и приложенных файлов через полнотекстовый указатель
    #: cases_fts (см. _FTS_SQL).
    _SEARCH_FIELDS = ("title", "customer", "case_id", "incoming_no",
                      "outgoing_no", "tc_no", "order_no", "note",
                      "outgoing_note")
    _FTS_SQL = "{p}id IN (SELECT case_ref FROM cases_fts WHERE cases_fts MATCH ?)"

    @staticmethod
    def _search_needle(query: str) -> str:
        """Строка для LIKE. % и _ — знаки, которые ввёл инженер, а не подстановка."""
        needle = query.strip().lower()
        for sign in ("\\", "%", "_"):
            needle = needle.replace(sign, "\\" + sign)
        return f"%{needle}%"

    def _search_clause(self, query: str, prefix: str = "") -> tuple[str, List[Any]]:
        """Условие поиска и его параметры: реквизиты письма плюс текст отчёта.

        По реквизитам ищем подстрокой — инженер помнит «0423» и вводит
        обрывок номера. По тексту отчёта подстрокой искать бессмысленно:
        «помеха» не найдёт «помехи». Там работает полнотекстовый указатель
        со стеммингом, тот же, что и у библиотеки.

        Условия складываются по «или»: указатель может отстать (его строит
        код при каждой правке), и поиск по реквизитам обязан работать в
        любом случае.
        """
        needle = self._search_needle(query)
        parts = [f"rulower({prefix}{name}) LIKE ? ESCAPE '\\'"
                 for name in self._SEARCH_FIELDS]
        params: List[Any] = [needle] * len(self._SEARCH_FIELDS)
        match = fts_match(query)
        if match:
            parts.append(self._FTS_SQL.format(p=prefix))
            params.append(match)
        return "(" + " OR ".join(parts) + ")", params

    def matched_by_text(self, query: str, case_refs: Sequence[int]) -> set[int]:
        """Какие из этих писем нашлись ТОЛЬКО по тексту отчёта.

        Нужно списку: человек должен видеть, почему письмо в выдаче, если
        искомого слова не видно ни в теме, ни в номерах. Письмо, найденное
        по теме, помечать незачем — там и так всё на виду, а лишняя пометка
        только сбивает.

        Реквизиты сверяем по основам слов, а не подстрокой: «помехи» и
        «Помеха в стволе» для человека — одно и то же, и говорить ему
        «нашлось в тексте отчёта» про слово, которое стоит в теме, значит
        сбивать с толку.
        """
        terms = fts_stemmed(query).split()
        if not terms or not case_refs:
            return set()
        marks = ", ".join("?" for _ in case_refs)
        columns = ", ".join(f"c.{name}" for name in self._SEARCH_FIELDS)
        rows = self.db.query(
            f"SELECT c.id, {columns} FROM cases c WHERE c.id IN "
            "(SELECT case_ref FROM cases_fts WHERE cases_fts MATCH ?) "
            f"AND c.id IN ({marks})",
            (fts_match(query), *case_refs),
        )
        found = set()
        for row in rows:
            fields = fts_stemmed(*(row[name] for name in self._SEARCH_FIELDS)).split()
            if not all(term in fields for term in terms):
                found.add(int(row["id"]))
        return found

    def _filters(self, *, status: str | None = None, assignee_id: int | None = None,
                 overdue_before: str | None = None, deadline_from: str | None = None,
                 deadline_to: str | None = None, query: str = "",
                 prefix: str = "") -> tuple[List[str], List[Any]]:
        """Условия отбора писем — одни на список и на счётчик.

        Раньше их было два набора, и они разошлись: список знал про срок и
        просрочку, счётчик — нет. На вкладке «Просроченные» выходило
        «показаны 2 из 5»: показано верно, а число взято по всем письмам.
        Один набор условий на оба запроса — единственный способ, чтобы это
        не повторилось при следующем новом отборе.
        """
        where: List[str] = []
        params: List[Any] = []
        if status == "open":
            marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
            where.append(f"{prefix}status IN ({marks})")
            params.extend(OPEN_CASE_STATUSES)
        elif status:
            where.append(f"{prefix}status = ?")
            params.append(status)
        if assignee_id is not None:
            where.append(f"{prefix}assignee_id = ?")
            params.append(assignee_id)
        if overdue_before:
            marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
            where.append(
                f"{prefix}deadline <> '' AND {prefix}deadline < ? "
                f"AND {prefix}status IN ({marks})"
            )
            params.append(overdue_before)
            params.extend(OPEN_CASE_STATUSES)
        if deadline_from or deadline_to:
            # Отбор по сроку делает база, а не выборка «первых N с фильтром
            # на стороне кода»: сортировка ставит наверх просроченные, и на
            # отделе с полусотней просрочек список «горящих» выходил пустым
            # при ненулевой плитке над ним.
            marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
            where.append(f"{prefix}deadline <> '' AND {prefix}status IN ({marks})")
            params.extend(OPEN_CASE_STATUSES)
            if deadline_from:
                where.append(f"{prefix}deadline >= ?")
                params.append(deadline_from)
            if deadline_to:
                where.append(f"{prefix}deadline <= ?")
                params.append(deadline_to)
        if query.strip():
            clause, needles = self._search_clause(query, prefix)
            where.append(clause)
            params.extend(needles)
        return where, params

    def count(self, status: str | None = None, assignee_id: int | None = None,
              query: str = "", *, overdue_before: str | None = None,
              deadline_from: str | None = None, deadline_to: str | None = None) -> int:
        where, params = self._filters(
            status=status, assignee_id=assignee_id, overdue_before=overdue_before,
            deadline_from=deadline_from, deadline_to=deadline_to, query=query)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return int(self.db.scalar(f"SELECT count(*) FROM cases{clause}", tuple(params)) or 0)


def _next_version(connection: sqlite3.Connection, case_ref: int) -> int:
    """Следующий номер версии отчёта по письму — внутри транзакции.

    Считать его до транзакции нельзя: две одновременные сдачи брали один
    номер, и вторая падала на UNIQUE(case_ref, version). Замок записи
    выстраивает их в очередь, и номера выходят подряд.
    """
    row = connection.execute(
        "SELECT coalesce(max(version), 0) FROM reports WHERE case_ref = ?", (case_ref,)
    ).fetchone()
    return int(row[0] or 0) + 1


class ReportRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, case_ref: int, markdown: str, meta: Dict[str, Any],
               issues: Sequence[Dict[str, Any]], sections: Sequence[Dict[str, Any]],
               user_id: int | None = None) -> Report:
        now = utcnow()
        with self.db.transaction() as connection:
            # Номер версии считаем под замком записи: две сдачи по одному
            # письму (хоть бы и от двойного нажатия) брали один номер и
            # спотыкались об UNIQUE(case_ref, version).
            version = _next_version(connection, case_ref)
            cursor = connection.execute(
                "INSERT INTO reports(case_ref, version, status, markdown, meta_json, issues_json, "
                "created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (case_ref, version, "draft", markdown,
                 json.dumps(meta, ensure_ascii=False),
                 json.dumps(list(issues), ensure_ascii=False), user_id, now),
            )
            report_id = int(cursor.lastrowid)  # type: ignore[arg-type]
            for ordinal, section in enumerate(sections):
                connection.execute(
                    "INSERT INTO report_sections(report_id, section_id, title, ord, draft_text, "
                    "text, sources_json, missing_facts_json, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        report_id,
                        section["section_id"],
                        section["title"],
                        ordinal,
                        section["text"],
                        section["text"],
                        json.dumps(section.get("sources", []), ensure_ascii=False),
                        json.dumps(section.get("missing_facts", []), ensure_ascii=False),
                        now,
                    ),
                )
        refresh_case_index(self.db, case_ref)
        report = self.get(report_id)
        assert report is not None
        return report

    def create_uploaded(self, case_ref: int, *, markdown: str, file_name: str,
                        file_path: str, file_size: int,
                        user_id: int | None = None,
                        status: str = "review") -> Report:
        """Готовый отчёт, загруженный файлом.

        Секций у него нет: это не сборка по шаблону, а документ, который
        инженер написал сам. В markdown кладём извлечённый текст — по нему
        работает поиск и предпросмотр, а на руки выдаётся исходный файл.

        status решает вызывающий. Сдача из списка писем — сразу «на проверке»:
        человек за тем и пришёл. Загрузка на уже открытом письме — «в работе»:
        там отчёт сперва смотрят, а на проверку отправляют отдельной кнопкой.
        """
        with self.db.transaction() as connection:
            # Тот же расчёт под замком: см. ReportRepo.create.
            version = _next_version(connection, case_ref)
            cursor = connection.execute(
                "INSERT INTO reports(case_ref, version, status, markdown, meta_json, "
                "issues_json, source, file_name, file_path, file_size, created_by, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_ref, version, status, markdown, "{}", "[]",
                 "uploaded", file_name, file_path, int(file_size), user_id, utcnow()),
            )
            report_id = int(cursor.lastrowid)  # type: ignore[arg-type]
        refresh_case_index(self.db, case_ref)
        report = self.get(report_id)
        assert report is not None
        return report

    def set_file_path(self, report_id: int, path: str) -> None:
        """Постоянное имя файла: известно только после присвоения версии."""
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET file_path = ? WHERE id = ?", (path, report_id))

    def file_path(self, report_id: int) -> str:
        """Где лежит загруженный файл. Для собранных системой — пусто."""
        row = self.db.query_one("SELECT file_path FROM reports WHERE id = ?", (report_id,))
        return (row["file_path"] if row is not None else "") or ""

    def get(self, report_id: int, with_sections: bool = True) -> Report | None:
        row = self.db.query_one("SELECT * FROM reports WHERE id = ?", (report_id,))
        if row is None:
            return None
        report = Report.from_row(row)
        if with_sections:
            report.sections = self.sections(report_id)
        return report

    def sections(self, report_id: int) -> List[ReportSection]:
        rows = self.db.query(
            "SELECT * FROM report_sections WHERE report_id = ? ORDER BY ord", (report_id,)
        )
        return rows_to(ReportSection, rows)

    def section(self, report_id: int, section_id: str) -> ReportSection | None:
        row = self.db.query_one(
            "SELECT * FROM report_sections WHERE report_id = ? AND section_id = ?",
            (report_id, section_id),
        )
        return ReportSection.from_row(row) if row else None

    def latest_for_case(self, case_ref: int, with_sections: bool = True) -> Report | None:
        row = self.db.query_one(
            "SELECT * FROM reports WHERE case_ref = ? ORDER BY version DESC LIMIT 1", (case_ref,)
        )
        if row is None:
            return None
        report = Report.from_row(row)
        if with_sections:
            report.sections = self.sections(report.id)
        return report

    def current_for_case(self, case_ref: int) -> Report | None:
        """Отчёт письма. У письма он один — последняя редакция.

        Прежние редакции остаются в базе как история правок: по ним видно,
        что именно начальник вернул и что исполнитель поправил. Но «отчёт
        по письму» — всегда последняя.
        """
        row = self.db.query_one(
            "SELECT * FROM reports WHERE case_ref = ? ORDER BY version DESC LIMIT 1",
            (case_ref,),
        )
        return Report.from_row(row) if row else None

    def list_for_case(self, case_ref: int) -> List[Report]:
        rows = self.db.query(
            "SELECT * FROM reports WHERE case_ref = ? ORDER BY version DESC", (case_ref,)
        )
        return rows_to(Report, rows)

    def update_section_text(self, report_id: int, section_id: str, text: str,
                            *, edited: bool = True) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE report_sections SET text = ?, edited = ?, updated_at = ? "
                "WHERE report_id = ? AND section_id = ?",
                (text, int(edited), utcnow(), report_id, section_id),
            )
        self._reindex(report_id)

    def replace_section(self, report_id: int, section_id: str, text: str,
                        sources: Sequence[str], missing_facts: Sequence[str]) -> None:
        """Перегенерация: новый черновик становится и текущим текстом."""
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE report_sections SET draft_text = ?, text = ?, sources_json = ?, "
                "missing_facts_json = ?, regenerated = regenerated + 1, edited = 0, "
                "updated_at = ? WHERE report_id = ? AND section_id = ?",
                (text, text, json.dumps(list(sources), ensure_ascii=False),
                 json.dumps(list(missing_facts), ensure_ascii=False), utcnow(),
                 report_id, section_id),
            )
        self._reindex(report_id)

    def _reindex(self, report_id: int) -> None:
        """Обновить поисковую строку письма после правки текста отчёта.

        Стоит вплотную к записи, а не в сервисном слое: там про него
        забыли на сдаче файлом, и отчёт лежал в базе, а поиск по его
        тексту не находил ничего.
        """
        row = self.db.query_one("SELECT case_ref FROM reports WHERE id = ?", (report_id,))
        if row is not None:
            refresh_case_index(self.db, int(row["case_ref"]))

    def update_meta(self, report_id: int, meta: Dict[str, Any]) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET meta_json = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), report_id),
            )

    def update_markdown(self, report_id: int, markdown: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET markdown = ? WHERE id = ?", (markdown, report_id)
            )
        self._reindex(report_id)

    def set_issues(self, report_id: int, issues: Sequence[Dict[str, Any]]) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET issues_json = ? WHERE id = ?",
                (json.dumps(list(issues), ensure_ascii=False), report_id),
            )

    def set_status(self, report_id: int, status: str, *, note: str | None = None) -> None:
        """Сменить состояние отчёта.

        При выходе из «проверен» подпись снимается целиком: кто и когда
        проверил, относится к прежнему тексту. Иначе в базе оставался бы
        проверяющий под документом, которого он не видел.
        """
        with self.db.transaction() as connection:
            if status == "approved":
                connection.execute(
                    "UPDATE reports SET status = ?, review_note = ? WHERE id = ?",
                    (status, note or "", report_id))
            else:
                connection.execute(
                    "UPDATE reports SET status = ?, review_note = ?, "
                    "approved_by = NULL, approved_at = NULL WHERE id = ?",
                    (status, note if note is not None else "", report_id))
        # Замечание проверяющего тоже ищут: «что мне тогда вернули по
        # этому письму». Значит, оно должно попасть в указатель.
        self._reindex(report_id)

    def approve(self, report_id: int, user_id: int | None) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET status = 'approved', review_note = '', "
                "approved_by = ?, approved_at = ? WHERE id = ?",
                (user_id, utcnow(), report_id),
            )


class EditPairRepo:
    """Пары «черновик модели → финал инженера» — обучающий набор (док. 03, 3.7)."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, *, case_id: str, report_id: int | None, report_type: str, section_id: str,
            section_title: str, draft: str, final: str, facts_digest: str = "",
            context: Dict[str, Any] | None = None, user_id: int | None = None) -> int:
        distance = normalized_edit_distance(draft, final)
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO edit_pairs(case_id, report_id, report_type, section_id, "
                "section_title, draft, final, facts_digest, context_json, edit_distance, "
                "created_by, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, report_id, report_type, section_id, section_title, draft, final,
                 facts_digest, json.dumps(context or {}, ensure_ascii=False), distance,
                 user_id, utcnow()),
            )
        return int(cursor.lastrowid)  # type: ignore[arg-type]

    def drop_for_report(self, report_id: int) -> int:
        """Убрать пары прошлого утверждения этого отчёта.

        Отчёт утверждают, снимают подпись, правят и утверждают снова. Пары
        от каждого утверждения копились: одна и та же секция попадала в
        обучающий набор дважды, и первым — тот вариант, который инженер
        потом сам же и забраковал. Учить на отклонённом тексте как на
        «финале инженера» — ровно то, от чего предостерегает док. 03, 3.7.
        """
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM edit_pairs WHERE report_id = ?", (report_id,)
            )
        return cursor.rowcount or 0

    def list(self, limit: int = 500, offset: int = 0) -> List[EditPair]:
        rows = self.db.query(
            "SELECT * FROM edit_pairs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        return rows_to(EditPair, rows)

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM edit_pairs") or 0)

    def mean_distance(self, report_type: str | None = None) -> float:
        if report_type:
            value = self.db.scalar(
                "SELECT avg(edit_distance) FROM edit_pairs WHERE report_type = ?", (report_type,)
            )
        else:
            value = self.db.scalar("SELECT avg(edit_distance) FROM edit_pairs")
        return float(value or 0.0)

    def by_section(self) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT section_id, section_title, count(*) AS pairs, "
            "avg(edit_distance) AS mean_distance FROM edit_pairs "
            "GROUP BY section_id, section_title ORDER BY mean_distance DESC"
        )
        return [
            {
                "section_id": row["section_id"],
                "section_title": row["section_title"],
                "pairs": row["pairs"],
                "mean_distance": round(float(row["mean_distance"] or 0.0), 3),
            }
            for row in rows
        ]


class ChatRepo:
    """Личные разговоры с помощником.

    Все методы принимают ``user_id`` и проверяют владельца: чужой чат нельзя
    ни прочитать, ни изменить — в вопросах инженеров всплывают данные
    отдела, а порядок доступа к ним тот же, что и к письмам.
    """

    def __init__(self, db: Database):
        self.db = db

    def create(self, user_id: int, *, title: str = "Новый разговор",
               domain: str = "", case_ref: int | None = None) -> Chat:
        now = utcnow()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO chats(user_id, title, domain, case_ref, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (user_id, title.strip() or "Новый разговор", domain, case_ref, now, now),
            )
        chat = self.get(int(cursor.lastrowid), user_id)  # type: ignore[arg-type]
        assert chat is not None
        return chat

    def get(self, chat_id: int, user_id: int | None = None) -> Chat | None:
        row = self.db.query_one(
            "SELECT c.*, (SELECT count(*) FROM chat_messages m WHERE m.chat_id = c.id) "
            "AS message_count FROM chats c WHERE c.id = ?",
            (chat_id,),
        )
        if row is None:
            return None
        if user_id is not None and row["user_id"] != user_id:
            return None
        return Chat.from_row(row)

    def for_user(self, user_id: int, *, archived: bool = False,
                 limit: int = 100, offset: int = 0) -> List[Chat]:
        rows = self.db.query(
            "SELECT c.*, (SELECT count(*) FROM chat_messages m WHERE m.chat_id = c.id) "
            "AS message_count FROM chats c WHERE c.user_id = ? AND c.archived = ? "
            "ORDER BY c.updated_at DESC LIMIT ? OFFSET ?",
            (user_id, int(archived), limit, offset),
        )
        return rows_to(Chat, rows)

    def rename(self, chat_id: int, title: str) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
                (title.strip()[:200] or "Новый разговор", utcnow(), chat_id),
            )

    def update(self, chat_id: int, *, domain: str | None = None,
               archived: bool | None = None) -> None:
        with self.db.transaction() as connection:
            if domain is not None:
                connection.execute("UPDATE chats SET domain = ? WHERE id = ?", (domain, chat_id))
            if archived is not None:
                connection.execute(
                    "UPDATE chats SET archived = ? WHERE id = ?", (int(archived), chat_id)
                )
            connection.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ?", (utcnow(), chat_id)
            )

    def delete(self, chat_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def add_message(self, chat_id: int, role: str, content: str, *,
                    sources: Sequence[Dict[str, Any]] = (),
                    meta: Dict[str, Any] | None = None) -> ChatMessage:
        if role not in ("user", "assistant"):
            raise ValueError(f"недопустимая роль сообщения: {role}")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO chat_messages(chat_id, role, content, sources_json, meta_json, "
                "created_at) VALUES(?,?,?,?,?,?)",
                (chat_id, role, content,
                 json.dumps(list(sources), ensure_ascii=False),
                 json.dumps(meta or {}, ensure_ascii=False), utcnow()),
            )
            connection.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ?", (utcnow(), chat_id)
            )
        row = self.db.query_one(
            "SELECT * FROM chat_messages WHERE id = ?", (int(cursor.lastrowid),)  # type: ignore[arg-type]
        )
        assert row is not None
        return ChatMessage.from_row(row)

    def messages(self, chat_id: int, limit: int = 500) -> List[ChatMessage]:
        rows = self.db.query(
            "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id LIMIT ?",
            (chat_id, limit),
        )
        return rows_to(ChatMessage, rows)

    def tail(self, chat_id: int, count: int = 6) -> List[ChatMessage]:
        """Последние сообщения — история, которая уходит модели в контекст."""
        rows = self.db.query(
            "SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, count),
        )
        return list(reversed(rows_to(ChatMessage, rows)))

    # -- вложения -----------------------------------------------------------

    def add_attachment(self, chat_id: int, name: str, kind: str, *, size: int = 0,
                       text: str = "", note: str = "") -> ChatAttachment:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO chat_attachments(chat_id, name, kind, size, text, note, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (chat_id, name, kind, size, text, note, utcnow()),
            )
        return self.attachment(int(cursor.lastrowid))  # type: ignore[return-value]

    def attachment(self, attachment_id: int) -> ChatAttachment | None:
        row = self.db.query_one(
            "SELECT * FROM chat_attachments WHERE id = ?", (attachment_id,))
        return ChatAttachment.from_row(row) if row else None

    #: Колонки вложения без самого текста: для списка на экране его читать
    #: незачем, а весит он до сотен мегабайт на файл.
    _ATTACH_HEAD = ("id, chat_id, name, kind, size, note, message_id, created_at, "
                    "length(text) AS chars")

    def attachments(self, chat_id: int, *, pending_only: bool = False,
                    with_text: bool = True) -> List[ChatAttachment]:
        """Вложения разговора. pending_only — ещё не привязанные к вопросу.

        ``with_text`` выключают там, где нужен только список: открытие
        разговора читало в память полный текст каждого дампа, чтобы показать
        его длину.
        """
        clause = " AND message_id IS NULL" if pending_only else ""
        columns = "*" if with_text else self._ATTACH_HEAD
        rows = self.db.query(
            f"SELECT {columns} FROM chat_attachments WHERE chat_id = ?{clause} ORDER BY id",
            (chat_id,),
        )
        return rows_to(ChatAttachment, rows)

    def bind_attachments(self, chat_id: int, message_id: int) -> int:
        """Привязать неприкреплённые вложения к отправленному вопросу."""
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE chat_attachments SET message_id = ? "
                "WHERE chat_id = ? AND message_id IS NULL",
                (message_id, chat_id),
            )
        return cursor.rowcount or 0

    def delete_attachment(self, attachment_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM chat_attachments WHERE id = ?", (attachment_id,))

    def count_for_user(self, user_id: int, *, archived: bool = False) -> int:
        return int(self.db.scalar(
            "SELECT count(*) FROM chats WHERE user_id = ? AND archived = ?",
            (user_id, int(archived)),
        ) or 0)

    def stats(self) -> Dict[str, int]:
        """Обезличенная статистика: сколько разговоров и сообщений всего.

        Содержимое чужих чатов недоступно никому, но объём использования
        помощника администратору видеть полезно.
        """
        return {
            "chats": int(self.db.scalar("SELECT count(*) FROM chats") or 0),
            "messages": int(self.db.scalar("SELECT count(*) FROM chat_messages") or 0),
            "users": int(self.db.scalar("SELECT count(DISTINCT user_id) FROM chats") or 0),
        }


class AuditRepo:
    def __init__(self, db: Database):
        self.db = db

    def log(self, action: str, *, user: User | None = None, object_type: str = "",
            object_id: str = "", details: Dict[str, Any] | None = None) -> None:
        """Записать действие в журнал. Неудача журнала не ломает само действие.

        Журнал — вещь служебная. Если база в этот момент занята (идёт загрузка
        библиотеки в соседнем окне), инженер не должен получить ошибку на
        сохранении отчёта из-за того, что не удалось записать строку «отчёт
        сохранён». Пропуск записи хуже, чем потерянная работа, но несравнимо.
        """
        try:
            with self.db.transaction() as connection:
                connection.execute(
                    "INSERT INTO audit(ts, user_id, login, action, object_type, object_id, "
                    "details_json) VALUES(?,?,?,?,?,?,?)",
                    (utcnow(), user.id if user else None, user.login if user else "",
                     action, object_type, str(object_id),
                     json.dumps(details or {}, ensure_ascii=False)),
                )
        except sqlite3.Error as error:
            log.warning("запись в журнал действий не удалась (%s): %s", action, error)

    def list(self, limit: int = 200) -> List[AuditEntry]:
        rows = self.db.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        return rows_to(AuditEntry, rows)


def normalized_edit_distance(left: str, right: str) -> float:
    """Расстояние Левенштейна по словам, нормированное на длину большего текста.

    Главная бизнес-метрика системы (док. 05): показывает, какую долю текста
    инженеру пришлось переписать за моделью. Нормировка именно на большую из
    длин даёт значение в диапазоне [0, 1] и не взрывается, когда инженер
    сокращает многословный черновик до двух предложений.
    """
    source = left.split()
    target = right.split()
    if not source and not target:
        return 0.0
    if not source or not target:
        return 1.0
    previous = list(range(len(target) + 1))
    for i, left_word in enumerate(source, start=1):
        current = [i]
        for j, right_word in enumerate(target, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (left_word != right_word),
            ))
        previous = current
    return round(previous[-1] / max(len(source), len(target), 1), 4)


class PersonFileRepo:
    """Личные документы сотрудника: справка-объективка и всё, что к ней."""

    _SELECT = (
        "SELECT f.*, coalesce(u.full_name, u.login, '') AS uploaded_by_name "
        "FROM person_files f LEFT JOIN users u ON u.id = f.uploaded_by"
    )

    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, name: str, path: str, size: int = 0,
            kind: str = "profile", note: str = "",
            uploaded_by: int | None = None) -> PersonFile:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO person_files(user_id, kind, name, size, path, note, "
                "uploaded_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, kind, name, size, path, note, uploaded_by, utcnow()),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[arg-type,return-value]

    def get(self, file_id: int) -> PersonFile | None:
        row = self.db.query_one(f"{self._SELECT} WHERE f.id = ?", (file_id,))
        return PersonFile.from_row(row) if row else None

    def list_for_user(self, user_id: int) -> List[PersonFile]:
        return rows_to(PersonFile, self.db.query(
            f"{self._SELECT} WHERE f.user_id = ? ORDER BY f.kind, f.id DESC",
            (user_id,)))

    def counts(self) -> Dict[int, int]:
        """Сколько документов у каждого. Нужно списку сотрудников одним запросом."""
        rows = self.db.query(
            "SELECT user_id, count(*) AS n FROM person_files GROUP BY user_id")
        return {int(row["user_id"]): int(row["n"]) for row in rows}

    def delete(self, file_id: int) -> str:
        """Убрать строку. Возвращает путь — удалять файл решает вызвавший."""
        item = self.get(file_id)
        if item is None:
            return ""
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM person_files WHERE id = ?", (file_id,))
        return item.path


class AbsenceRepo:
    """Расход личного состава: чем занят человек в эти дни."""

    _SELECT = (
        "SELECT a.*, coalesce(u.full_name, u.login, '') AS full_name, "
        "u.role AS role, u.team AS team "
        "FROM absences a JOIN users u ON u.id = a.user_id"
    )

    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, kind: str, date_from: str, date_to: str,
            note: str = "", created_by: int | None = None,
            place: str = "") -> Absence:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO absences(user_id, kind, date_from, date_to, place, "
                "note, created_by, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, kind, date_from, date_to, place, note, created_by, utcnow()),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[arg-type,return-value]

    def update(self, absence_id: int, **fields: Any) -> Absence | None:
        """Правка записи расхода. Принимает только известные колонки."""
        allowed = ("kind", "date_from", "date_to", "place", "note")
        parts, values = [], []
        for name in allowed:
            if name in fields:
                parts.append(f"{name} = ?")
                values.append(fields[name])
        if not parts:
            return self.get(absence_id)
        values.append(absence_id)
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE absences SET {', '.join(parts)} WHERE id = ?", tuple(values))
        return self.get(absence_id)

    def overlapping(self, user_id: int, date_from: str, date_to: str,
                    skip_id: int | None = None) -> List[Absence]:
        """Записи этого человека, пересекающиеся с промежутком.

        Нужны, чтобы не заводить вторую отметку на те же дни: расход, где
        человек одновременно в отпуске и на дежурстве, — не расход.
        """
        params: List[Any] = [user_id, date_to, date_from]
        clause = " WHERE a.user_id = ? AND a.date_from <= ? AND a.date_to >= ?"
        if skip_id is not None:
            clause += " AND a.id <> ?"
            params.append(skip_id)
        return rows_to(Absence, self.db.query(
            f"{self._SELECT}{clause} ORDER BY a.date_from", tuple(params)))

    def in_period_for_active(self, date_from: str, date_to: str) -> List[Absence]:
        """Расход за промежуток, только по действующим сотрудникам.

        Уволенный человек с отпуском до конца месяца в расходе не нужен: он
        занимал бы строку в сетке, которую никто не может заполнить.
        """
        rows = self.db.query(
            f"{self._SELECT} WHERE u.active = 1 AND a.date_from <= ? AND a.date_to >= ? "
            "ORDER BY full_name, a.date_from",
            (date_to, date_from),
        )
        return rows_to(Absence, rows)

    def get(self, absence_id: int) -> Absence | None:
        row = self.db.query_one(f"{self._SELECT} WHERE a.id = ?", (absence_id,))
        return Absence.from_row(row) if row else None

    def delete(self, absence_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM absences WHERE id = ?", (absence_id,))

    def on_date(self, day: str, kind: str | None = None) -> List[Absence]:
        """Кто отсутствует или дежурит в этот день. Границы включительно.

        Только действующие сотрудники. Отпуск уволенного длится в базе до
        своей даты и раньше считался как отсутствие: отдел вечно недосчитывался
        человека, которого в нём давно нет.
        """
        params: List[Any] = [day, day]
        clause = " WHERE u.active = 1 AND a.date_from <= ? AND a.date_to >= ?"
        if kind:
            clause += " AND a.kind = ?"
            params.append(kind)
        rows = self.db.query(f"{self._SELECT}{clause} ORDER BY a.kind, full_name", tuple(params))
        return rows_to(Absence, rows)

    def in_period(self, date_from: str, date_to: str) -> List[Absence]:
        """Все периоды, пересекающиеся с промежутком."""
        rows = self.db.query(
            f"{self._SELECT} WHERE a.date_from <= ? AND a.date_to >= ? "
            "ORDER BY a.date_from, full_name",
            (date_to, date_from),
        )
        return rows_to(Absence, rows)

    def for_user_period(self, user_id: int, date_from: str, date_to: str) -> List[Absence]:
        """Расход одного человека за промежуток. Для его личного кабинета."""
        return rows_to(Absence, self.db.query(
            f"{self._SELECT} WHERE a.user_id = ? AND a.date_from <= ? "
            "AND a.date_to >= ? ORDER BY a.date_from",
            (user_id, date_to, date_from)))

    def for_user(self, user_id: int, limit: int = 50) -> List[Absence]:
        rows = self.db.query(
            f"{self._SELECT} WHERE a.user_id = ? ORDER BY a.date_from DESC LIMIT ?",
            (user_id, limit),
        )
        return rows_to(Absence, rows)


class BoardRepo:
    """Сводка по отделу: одна выборка на показатель, без N+1.

    Все запросы читающие: сводку открывают часто, и она не должна ни писать
    в базу, ни держать блокировку дольше одного SELECT.
    """

    def __init__(self, db: Database):
        self.db = db

    def workload(self, today: str) -> List[Dict[str, Any]]:
        """Нагрузка по людям: сколько писем в работе, сколько просрочено."""
        marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
        rows = self.db.query(
            "SELECT u.id, u.login, u.full_name, u.role, u.department, u.team, u.active, "
            f"  sum(CASE WHEN c.status IN ({marks}) THEN 1 ELSE 0 END) AS open_count, "
            f"  sum(CASE WHEN c.status IN ({marks}) AND c.deadline <> '' "
            "        AND c.deadline < ? THEN 1 ELSE 0 END) AS late_count, "
            f"  sum(CASE WHEN c.status IN ({marks}) AND c.deadline <> '' "
            "        AND c.deadline >= ? AND c.deadline <= ? THEN 1 ELSE 0 END) AS soon_count, "
            "  sum(CASE WHEN c.status = 'approved' THEN 1 ELSE 0 END) AS done_count, "
            "  min(CASE WHEN c.status IN (" + marks + ") AND c.deadline <> '' "
            "        THEN c.deadline END) AS next_deadline "
            "FROM users u LEFT JOIN cases c ON c.assignee_id = u.id "
            # Отключённый сотрудник остаётся в списке, пока за ним числятся
            # письма в работе. Иначе они исчезали отовсюду: из нагрузки —
            # вместе с человеком, а в «без исполнителя» не попадали, потому
            # что исполнитель у них есть. Три письма отдела не видел никто.
            # Заявка на доступ — ещё не сотрудник: в сводке отдела ей делать
            # нечего. Писем за ней не числится по определению, поэтому и
            # оговорка про «остаётся, пока есть письма» её не касается.
            # Создатель системы — учётная запись, а не человек отдела: он не
            # дежурит и писем за ним не числится, а в сводке добавлял бы
            # лишнюю единицу к «человек в строю».
            "WHERE u.login <> 'local' AND u.role <> 'owner' AND u.approved = 1 "
            "  AND (u.active = 1 OR c.id IS NOT NULL) "
            "GROUP BY u.id "
            "HAVING u.active = 1 OR open_count > 0 "
            "ORDER BY late_count DESC, open_count DESC, u.full_name",
            # Порядок подстановок повторяет порядок «?» в запросе:
            # открытые, просроченные, ближайшие, следующий срок.
            tuple(OPEN_CASE_STATUSES)
            + tuple(OPEN_CASE_STATUSES) + (today,)
            + tuple(OPEN_CASE_STATUSES) + (today, _shift_days(today, 3))
            + tuple(OPEN_CASE_STATUSES),
        )
        return [dict(row) for row in rows]

    def status_counts(self) -> Dict[str, int]:
        rows = self.db.query("SELECT status, count(*) AS n FROM cases GROUP BY status")
        return {row["status"]: int(row["n"]) for row in rows}

    def deadline_counts(self, today: str, soon_until: str) -> Dict[str, int]:
        """Сколько писем просрочено и сколько горит. Считаем в базе.

        Раньше сводка выбирала до 500 полных писем и мерила длину списка:
        на пятьсот первом письме счётчик замирал и показывал неправду, а
        ради двух чисел читалась половина таблицы.
        """
        marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
        row = self.db.query_one(
            "SELECT "
            "  sum(CASE WHEN deadline <> '' AND deadline < ? THEN 1 ELSE 0 END) AS late, "
            "  sum(CASE WHEN deadline <> '' AND deadline >= ? AND deadline <= ? "
            "      THEN 1 ELSE 0 END) AS soon "
            f"FROM cases WHERE status IN ({marks})",
            (today, today, soon_until, *OPEN_CASE_STATUSES),
        )
        return {
            "late": int((row["late"] if row else 0) or 0),
            "soon": int((row["soon"] if row else 0) or 0),
        }

    def unassigned(self) -> int:
        marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
        return int(self.db.scalar(
            f"SELECT count(*) FROM cases WHERE assignee_id IS NULL AND status IN ({marks})",
            tuple(OPEN_CASE_STATUSES)) or 0)

    def movement(self, date_from: str) -> Dict[str, int]:
        """Движение за период: сколько принято, проверено и отправлено.

        Считаем по дате отправки ответа, а не по времени правки письма и
        не по отметке начальника. По правке выходила неправда: поправил
        примечание в письме прошлого года — и оно попадало в отправленные
        за текущий месяц. По отметке начальника — тоже: проверенный отчёт,
        ответ по которому ещё не ушёл, отправленным не считается, и в
        отчётности отдела это разные числа.
        """
        came = int(self.db.scalar(
            "SELECT count(*) FROM cases WHERE created_at >= ?", (date_from,)) or 0)
        sent = int(self.db.scalar(
            "SELECT count(*) FROM cases WHERE outgoing_no <> '' AND outgoing_date >= ?",
            (date_from[:10],)) or 0)
        checked = int(self.db.scalar(
            "SELECT count(DISTINCT case_ref) FROM reports "
            "WHERE status = 'approved' AND approved_at >= ?", (date_from,)) or 0)
        return {"came": came, "sent": sent, "checked": checked}

    def reports_in_period(self, date_from: str) -> int:
        return int(self.db.scalar(
            "SELECT count(*) FROM reports WHERE created_at >= ?", (date_from,)) or 0)


def _shift_days(day: str, days: int) -> str:
    """Дата через N дней. На нечитаемой дате возвращаем её же — фильтр по
    «скоро» тогда просто ничего не найдёт, а страница откроется."""
    try:
        base = datetime.strptime(day[:10], "%Y-%m-%d")
    except ValueError:
        return day
    return (base + timedelta(days=days)).strftime("%Y-%m-%d")


class NoticeRepo:
    """Уведомления: что человеку нужно знать.

    Хранятся у получателя, а не рассылаются: машина изолирована, почты и
    телефона нет, и единственное надёжное место для «вам сообщение» — та же
    база, куда человек и так заходит работать.
    """

    _SELECT = (
        "SELECT n.*, coalesce(u.full_name, u.login, '') AS from_name "
        "FROM notifications n LEFT JOIN users u ON u.id = n.from_id"
    )

    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, kind: str, title: str, body: str = "",
            link: str = "", from_id: int | None = None) -> Notice | None:
        """Положить уведомление. Себе самому не кладём: это шум."""
        if from_id is not None and from_id == user_id:
            return None
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO notifications(user_id, kind, title, body, link, "
                "from_id, seen, created_at) VALUES(?,?,?,?,?,?,0,?)",
                (user_id, kind, title, body, link, from_id, utcnow()),
            )
        return self.get(int(cursor.lastrowid))

    def get(self, notice_id: int) -> Notice | None:
        row = self.db.query_one(f"{self._SELECT} WHERE n.id = ?", (notice_id,))
        return Notice.from_row(row) if row else None

    def list_for(self, user_id: int, limit: int = 50) -> List[Notice]:
        return rows_to(Notice, self.db.query(
            f"{self._SELECT} WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?",
            (user_id, limit)))

    def unseen(self, user_id: int) -> int:
        return int(self.db.scalar(
            "SELECT count(*) FROM notifications WHERE user_id = ? AND seen = 0",
            (user_id,)) or 0)

    def mark_seen(self, user_id: int, notice_id: int | None = None) -> None:
        """Отметить прочитанным одно уведомление или все сразу."""
        with self.db.transaction() as connection:
            if notice_id is None:
                connection.execute(
                    "UPDATE notifications SET seen = 1 WHERE user_id = ?", (user_id,))
            else:
                connection.execute(
                    "UPDATE notifications SET seen = 1 WHERE user_id = ? AND id = ?",
                    (user_id, notice_id))

    def clear(self, user_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))


class TalkRepo:
    """Переписка между людьми: личная и на несколько человек."""

    def __init__(self, db: Database):
        self.db = db

    def create(self, member_ids: Sequence[int], title: str = "",
               created_by: int | None = None) -> int:
        now = utcnow()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO talks(title, created_by, created_at, updated_at) "
                "VALUES(?,?,?,?)", (title, created_by, now, now))
            talk_id = int(cursor.lastrowid)
            for user_id in dict.fromkeys(member_ids):
                connection.execute(
                    "INSERT OR IGNORE INTO talk_members(talk_id, user_id, seen_id) "
                    "VALUES(?,?,0)", (talk_id, user_id))
        return talk_id

    def private_between(self, first: int, second: int) -> int | None:
        """Уже заведённая беседа ровно этих двоих. Иначе None.

        Без этого каждое «написать Иванову» заводило бы новую ветку, и
        переписка рассыпалась бы на десяток одинаковых.
        """
        row = self.db.query_one(
            "SELECT t.id FROM talks t "
            "JOIN talk_members a ON a.talk_id = t.id AND a.user_id = ? "
            "JOIN talk_members b ON b.talk_id = t.id AND b.user_id = ? "
            "WHERE t.title = '' AND "
            "(SELECT count(*) FROM talk_members m WHERE m.talk_id = t.id) = 2 "
            "ORDER BY t.id LIMIT 1", (first, second))
        return int(row["id"]) if row else None

    def members(self, talk_id: int) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT m.user_id, m.seen_id, coalesce(u.full_name, u.login, '') AS name, "
            "u.role AS role FROM talk_members m JOIN users u ON u.id = m.user_id "
            "WHERE m.talk_id = ? ORDER BY name", (talk_id,))
        # Участники беседы — списком в заголовке: полное ФИО там не помещается
        # и не нужно, беседу узнают по фамилиям.
        return [{"id": int(row["user_id"]), "full_name": short_name(row["name"]),
                 "role": row["role"], "seen_id": int(row["seen_id"])} for row in rows]

    def is_member(self, talk_id: int, user_id: int) -> bool:
        return self.db.query_one(
            "SELECT 1 FROM talk_members WHERE talk_id = ? AND user_id = ?",
            (talk_id, user_id)) is not None

    def list_for(self, user_id: int) -> List[Dict[str, Any]]:
        """Беседы человека: свежие сверху, с последним сообщением и счётчиком."""
        rows = self.db.query(
            "SELECT t.id, t.title, t.updated_at, m.seen_id, "
            "(SELECT text FROM talk_messages x WHERE x.talk_id = t.id "
            " ORDER BY x.id DESC LIMIT 1) AS last_text, "
            "(SELECT count(*) FROM talk_messages x WHERE x.talk_id = t.id "
            " AND x.id > m.seen_id AND x.user_id <> ?) AS unread "
            "FROM talks t JOIN talk_members m ON m.talk_id = t.id "
            "WHERE m.user_id = ? ORDER BY t.updated_at DESC, t.id DESC",
            (user_id, user_id))
        out = []
        for row in rows:
            out.append({
                "id": int(row["id"]),
                "title": row["title"] or "",
                "updated_at": row["updated_at"],
                "last_text": (row["last_text"] or "")[:160],
                "unread": int(row["unread"] or 0),
                "members": self.members(int(row["id"])),
            })
        return out

    def add_message(self, talk_id: int, user_id: int | None, text: str) -> TalkMessage:
        now = utcnow()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO talk_messages(talk_id, user_id, text, created_at) "
                "VALUES(?,?,?,?)", (talk_id, user_id, text, now))
            connection.execute("UPDATE talks SET updated_at = ? WHERE id = ?",
                               (now, talk_id))
            # Своё сообщение прочитанным считается сразу: счётчик непрочитанных
            # у автора иначе рос бы от его же слов.
            if user_id is not None:
                connection.execute(
                    "UPDATE talk_members SET seen_id = ? WHERE talk_id = ? AND user_id = ?",
                    (int(cursor.lastrowid), talk_id, user_id))
        return self.message(int(cursor.lastrowid))  # type: ignore[return-value]

    def message(self, message_id: int) -> TalkMessage | None:
        row = self.db.query_one(
            "SELECT m.*, coalesce(u.full_name, u.login, '') AS author "
            "FROM talk_messages m LEFT JOIN users u ON u.id = m.user_id "
            "WHERE m.id = ?", (message_id,))
        return TalkMessage.from_row(row) if row else None

    def messages(self, talk_id: int, limit: int = 200) -> List[TalkMessage]:
        rows = self.db.query(
            "SELECT m.*, coalesce(u.full_name, u.login, '') AS author "
            "FROM talk_messages m LEFT JOIN users u ON u.id = m.user_id "
            "WHERE m.talk_id = ? ORDER BY m.id DESC LIMIT ?", (talk_id, limit))
        items = list(reversed(rows_to(TalkMessage, rows)))
        # Файлы всей беседы одним запросом и раскладываем по сообщениям: по
        # запросу на сообщение — это двести запросов на открытие переписки.
        by_message: Dict[int, List[Dict[str, Any]]] = {}
        for item in self.files(talk_id):
            by_message.setdefault(int(item.message_id or 0), []).append(item.to_dict())
        for message in items:
            message.files = by_message.get(message.id, [])
        return items

    # -- вложения -----------------------------------------------------------

    def files(self, talk_id: int) -> List[TalkFile]:
        return rows_to(TalkFile, self.db.query(
            "SELECT * FROM talk_files WHERE talk_id = ? ORDER BY id", (talk_id,)))

    def file(self, file_id: int) -> TalkFile | None:
        row = self.db.query_one("SELECT * FROM talk_files WHERE id = ?", (file_id,))
        return TalkFile.from_row(row) if row else None

    def add_file(self, talk_id: int, message_id: int | None, user_id: int | None,
                 name: str, path: str, size: int, text: str = "") -> TalkFile:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO talk_files(talk_id, message_id, user_id, name, path, "
                "size, text, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (talk_id, message_id, user_id, name, path, int(size or 0),
                 text, utcnow()))
        return self.file(int(cursor.lastrowid))  # type: ignore[return-value]

    def mark_read(self, talk_id: int, user_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE talk_members SET seen_id = "
                "coalesce((SELECT max(id) FROM talk_messages WHERE talk_id = ?), 0) "
                "WHERE talk_id = ? AND user_id = ?", (talk_id, talk_id, user_id))

    def unread_total(self, user_id: int) -> int:
        return int(self.db.scalar(
            "SELECT count(*) FROM talk_messages x "
            "JOIN talk_members m ON m.talk_id = x.talk_id AND m.user_id = ? "
            "WHERE x.id > m.seen_id AND x.user_id <> ?", (user_id, user_id)) or 0)


class CaseNoteRepo:
    """Примечания к письму: обсуждение прямо на деле."""

    _SELECT = (
        "SELECT n.*, coalesce(u.full_name, u.login, '') AS author "
        "FROM case_notes n LEFT JOIN users u ON u.id = n.user_id"
    )

    def __init__(self, db: Database):
        self.db = db

    def add(self, case_ref: int, user_id: int | None, text: str) -> CaseNote:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO case_notes(case_ref, user_id, text, created_at) "
                "VALUES(?,?,?,?)", (case_ref, user_id, text, utcnow()))
        return self.get(int(cursor.lastrowid))  # type: ignore[return-value]

    def get(self, note_id: int) -> CaseNote | None:
        row = self.db.query_one(f"{self._SELECT} WHERE n.id = ?", (note_id,))
        return CaseNote.from_row(row) if row else None

    def list_for_case(self, case_ref: int) -> List[CaseNote]:
        return rows_to(CaseNote, self.db.query(
            f"{self._SELECT} WHERE n.case_ref = ? ORDER BY n.id", (case_ref,)))

    def delete(self, note_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM case_notes WHERE id = ?", (note_id,))


class Repositories:
    """Единая точка доступа ко всем репозиториям."""

    def __init__(self, db: Database):
        self.db = db
        self.users = UserRepo(db)
        self.sessions = SessionRepo(db)
        self.documents = DocumentRepo(db)
        self.chunks = ChunkRepo(db)
        self.vectors = VectorRepo(db)
        self.cases = CaseRepo(db)
        self.case_search = CaseSearchRepo(db)
        self.case_files = CaseFileRepo(db)
        self.reports = ReportRepo(db)
        self.edits = EditPairRepo(db)
        self.chats = ChatRepo(db)
        self.absences = AbsenceRepo(db)
        self.person_files = PersonFileRepo(db)
        self.board = BoardRepo(db)
        self.notices = NoticeRepo(db)
        self.talks = TalkRepo(db)
        self.case_notes = CaseNoteRepo(db)
        self.audit = AuditRepo(db)

    @classmethod
    def open(cls, path: str) -> "Repositories":
        return cls(Database(path))

    def close(self) -> None:
        self.db.close()
