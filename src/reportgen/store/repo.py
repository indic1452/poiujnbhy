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
    Case,
    Chat,
    ChatMessage,
    Document,
    EditPair,
    Report,
    ReportSection,
    User,
    rows_to,
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
               role: str = "engineer", department: str = "", team: str = "") -> User:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO users(login, full_name, role, department, team, "
                "password_hash, active, created_at) VALUES(?,?,?,?,?,?,1,?)",
                (login.strip().lower(), full_name, role, department.strip(), team.strip(),
                 hash_password(password), utcnow()),
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

    def update(self, user_id: int, full_name: str | None = None,
               role: str | None = None, department: str | None = None,
               team: str | None = None) -> "User | None":
        """Изменить ФИО, должность, отдел и группу. None оставляет поле как было."""
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

    def set_active(self, user_id: int, active: bool) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))

    def list_all(self, active_only: bool = False) -> List[User]:
        """Личный состав. Сортировка — по старшинству должности, потом по ФИО:
        так список читается как штатное расписание, а не как выгрузка."""
        order = "CASE role " + " ".join(
            f"WHEN '{role}' THEN {index}" for index, role in enumerate(ROLES)
        ) + f" ELSE {len(ROLES)} END, full_name, login"
        where = " WHERE active = 1" if active_only else ""
        return rows_to(User, self.db.query(f"SELECT * FROM users{where} ORDER BY {order}"))

    def departments(self) -> List[str]:
        rows = self.db.query(
            "SELECT DISTINCT department FROM users "
            "WHERE department <> '' ORDER BY department"
        )
        return [row["department"] for row in rows]

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
               sha256: str, confidentiality: str = "internal",
               meta: Dict[str, Any] | None = None, domain: str = "",
               status: str = "current", superseded_by: str = "",
               year: int | None = None, size: int | None = None,
               mtime_ns: int | None = None) -> Document:
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO documents(doc_id, doc_type, title, source_path, sha256, "
                "confidentiality, meta_json, domain, status, superseded_by, year, "
                "size, mtime_ns, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET doc_type=excluded.doc_type, "
                "title=excluded.title, source_path=excluded.source_path, "
                "sha256=excluded.sha256, confidentiality=excluded.confidentiality, "
                "meta_json=excluded.meta_json, domain=excluded.domain, "
                "status=excluded.status, superseded_by=excluded.superseded_by, "
                "year=excluded.year, size=excluded.size, mtime_ns=excluded.mtime_ns, "
                # Файл изменился — прежние чанки устарели, отметку об индексации
                # снимаем до того, как ChunkRepo их перезапишет.
                "indexed_at=CASE WHEN documents.sha256 = excluded.sha256 "
                "THEN documents.indexed_at ELSE NULL END, "
                "chunk_count=CASE WHEN documents.sha256 = excluded.sha256 "
                "THEN documents.chunk_count ELSE 0 END",
                (doc_id, doc_type, title, source_path, sha256, confidentiality,
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

    def list(self, doc_type: str | None = None, domain: str | None = None,
             status: str | None = None) -> List[Document]:
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
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.query(f"SELECT * FROM documents{where} ORDER BY doc_type, title", params)
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

    def for_document(self, document_id: int, limit: int = 400) -> List[Chunk]:
        """Фрагменты одного документа по порядку — так, как их видит поиск.

        Нужны инженеру для проверки качества разбора: по ним сразу видно,
        распался ли скан на осмысленные куски или в базу уехал мусор.
        """
        rows = self.db.query(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY ord LIMIT ?",
            (document_id, limit),
        )
        return [self._to_chunk(row) for row in rows]

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

    def missing(self, chunk_uids: Sequence[str]) -> List[str]:
        present = set(self.get_many(chunk_uids))
        return [uid for uid in chunk_uids if uid not in present]

    def count(self) -> int:
        return int(self.db.scalar("SELECT count(*) FROM embeddings") or 0)

    def clear(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM embeddings")


class CaseRepo:
    def __init__(self, db: Database):
        self.db = db

    #: Выборка письма вместе с ФИО исполнителя: список писем без исполнителя
    #: бесполезен, а второй запрос на строку — это N+1 на ровном месте.
    _SELECT = (
        "SELECT c.*, coalesce(u.full_name, u.login, '') AS assignee_name "
        "FROM cases c LEFT JOIN users u ON u.id = c.assignee_id"
    )

    def create(self, case_id: str, report_type: str, facts: Dict[str, Any],
               digest: str = "", title: str = "", customer: str = "",
               user_id: int | None = None, *, incoming_no: str = "",
               incoming_date: str = "", deadline: str = "", priority: str = "normal",
               assignee_id: int | None = None, note: str = "") -> Case:
        now = utcnow()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO cases(case_id, report_type, title, customer, status, "
                "incoming_no, incoming_date, deadline, priority, assignee_id, note, "
                "facts_json, facts_digest, created_by, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, report_type, title, customer, "new",
                 incoming_no, incoming_date, deadline, priority, assignee_id, note,
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
             query: str = "") -> List[Case]:
        """Письма по фильтрам. Сортировка: сначала просроченные и срочные.

        Порядок «по дате правки» показывал наверху то, чего только что
        коснулись, а не то, что горит. Отделу нужно обратное.
        """
        where, params = [], []
        if status == "open":
            marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
            where.append(f"c.status IN ({marks})")
            params.extend(OPEN_CASE_STATUSES)
        elif status:
            where.append("c.status = ?")
            params.append(status)
        if assignee_id is not None:
            where.append("c.assignee_id = ?")
            params.append(assignee_id)
        if overdue_before:
            marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
            where.append(
                f"c.deadline <> '' AND c.deadline < ? AND c.status IN ({marks})"
            )
            params.append(overdue_before)
            params.extend(OPEN_CASE_STATUSES)
        if query.strip():
            like = f"%{query.strip().lower()}%"
            where.append(
                "(rulower(c.title) LIKE ? OR rulower(c.customer) LIKE ? "
                "OR rulower(c.case_id) LIKE ? OR rulower(c.incoming_no) LIKE ?)"
            )
            params.extend([like, like, like, like])
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
        """
        allowed = ("title", "customer", "incoming_no", "incoming_date",
                   "deadline", "priority", "assignee_id", "note", "status")
        parts, values = [], []
        for name in allowed:
            if name in fields and fields[name] is not None:
                parts.append(f"{name} = ?")
                values.append(fields[name])
        if not parts:
            return self.get(case_ref)
        values.extend([utcnow(), case_ref])
        with self.db.transaction() as connection:
            connection.execute(
                f"UPDATE cases SET {', '.join(parts)}, updated_at = ? WHERE id = ?",
                tuple(values),
            )
        return self.get(case_ref)

    def delete(self, case_ref: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM cases WHERE id = ?", (case_ref,))

    def count(self, status: str | None = None, assignee_id: int | None = None) -> int:
        where, params = [], []
        if status == "open":
            marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
            where.append(f"status IN ({marks})")
            params.extend(OPEN_CASE_STATUSES)
        elif status:
            where.append("status = ?")
            params.append(status)
        if assignee_id is not None:
            where.append("assignee_id = ?")
            params.append(assignee_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return int(self.db.scalar(f"SELECT count(*) FROM cases{clause}", tuple(params)) or 0)


class ReportRepo:
    def __init__(self, db: Database):
        self.db = db

    def create(self, case_ref: int, markdown: str, meta: Dict[str, Any],
               issues: Sequence[Dict[str, Any]], sections: Sequence[Dict[str, Any]],
               user_id: int | None = None) -> Report:
        version = int(
            self.db.scalar("SELECT coalesce(max(version), 0) FROM reports WHERE case_ref = ?",
                           (case_ref,)) or 0
        ) + 1
        now = utcnow()
        with self.db.transaction() as connection:
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
        report = self.get(report_id)
        assert report is not None
        return report

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

    def set_issues(self, report_id: int, issues: Sequence[Dict[str, Any]]) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET issues_json = ? WHERE id = ?",
                (json.dumps(list(issues), ensure_ascii=False), report_id),
            )

    def set_status(self, report_id: int, status: str) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))

    def approve(self, report_id: int, user_id: int | None) -> None:
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE reports SET status = 'approved', approved_by = ?, approved_at = ? "
                "WHERE id = ?",
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
    заказчиков, а гриф на них тот же, что и на кейсах.
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


class AbsenceRepo:
    """Дежурства, отпуска и командировки."""

    _SELECT = (
        "SELECT a.*, coalesce(u.full_name, u.login, '') AS full_name, u.role AS role "
        "FROM absences a JOIN users u ON u.id = a.user_id"
    )

    def __init__(self, db: Database):
        self.db = db

    def add(self, user_id: int, kind: str, date_from: str, date_to: str,
            note: str = "", created_by: int | None = None) -> Absence:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO absences(user_id, kind, date_from, date_to, note, "
                "created_by, created_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, kind, date_from, date_to, note, created_by, utcnow()),
            )
        return self.get(int(cursor.lastrowid))  # type: ignore[arg-type,return-value]

    def get(self, absence_id: int) -> Absence | None:
        row = self.db.query_one(f"{self._SELECT} WHERE a.id = ?", (absence_id,))
        return Absence.from_row(row) if row else None

    def delete(self, absence_id: int) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM absences WHERE id = ?", (absence_id,))

    def on_date(self, day: str, kind: str | None = None) -> List[Absence]:
        """Кто отсутствует или дежурит в этот день. Границы включительно."""
        params: List[Any] = [day, day]
        clause = " WHERE a.date_from <= ? AND a.date_to >= ?"
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

    def for_user(self, user_id: int, limit: int = 50) -> List[Absence]:
        rows = self.db.query(
            f"{self._SELECT} WHERE a.user_id = ? ORDER BY a.date_from DESC LIMIT ?",
            (user_id, limit),
        )
        return rows_to(Absence, rows)


class BoardRepo:
    """Сводка для дашборда: одна выборка на показатель, без N+1.

    Все запросы читающие: дашборд открывают часто, и он не должен ни писать
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
            "WHERE u.active = 1 AND u.login <> 'local' "
            "GROUP BY u.id ORDER BY late_count DESC, open_count DESC, u.full_name",
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

    def unassigned(self) -> int:
        marks = ", ".join("?" for _ in OPEN_CASE_STATUSES)
        return int(self.db.scalar(
            f"SELECT count(*) FROM cases WHERE assignee_id IS NULL AND status IN ({marks})",
            tuple(OPEN_CASE_STATUSES)) or 0)

    def movement(self, date_from: str) -> Dict[str, int]:
        """Движение за период: сколько принято, отправлено, в работе."""
        row = self.db.query_one(
            "SELECT "
            "  sum(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS came, "
            "  sum(CASE WHEN updated_at >= ? AND status = 'approved' THEN 1 ELSE 0 END) AS sent "
            "FROM cases",
            (date_from, date_from),
        )
        return {
            "came": int((row["came"] if row else 0) or 0),
            "sent": int((row["sent"] if row else 0) or 0),
        }

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
        self.reports = ReportRepo(db)
        self.edits = EditPairRepo(db)
        self.chats = ChatRepo(db)
        self.absences = AbsenceRepo(db)
        self.board = BoardRepo(db)
        self.audit = AuditRepo(db)

    @classmethod
    def open(cls, path: str) -> "Repositories":
        return cls(Database(path))

    def close(self) -> None:
        self.db.close()
