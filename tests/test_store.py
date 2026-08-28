"""Тесты хранилища: схема, транзакции, пароли и все репозитории.

База поднимается в памяти на каждый тест, поэтому тесты независимы и быстры.
Внешние сервисы не используются: хранилище — чистый SQLite стандартной
библиотеки.
"""

import sqlite3
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any, Dict, List, Sequence

import _bootstrap  # noqa: F401
from reportgen.corpus import Chunk
from reportgen.store.db import SCHEMA_VERSION, Database, utcnow
from reportgen.store.models import (
    AuditEntry,
    Case,
    Document,
    Report,
    ReportSection,
    User,
    rows_to,
)
from reportgen.store.repo import (
    Repositories,
    hash_password,
    normalized_edit_distance,
    pack_vector,
    unpack_vector,
    verify_password,
)

PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2099-01-01T00:00:00+00:00"


def make_chunk(chunk_id: str, doc_id: str, doc_type: str, text: str,
               title_path: Sequence[str] | None = None,
               meta: Dict[str, Any] | None = None) -> Chunk:
    """Готовит чанк без обращения к файловой системе."""
    return Chunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        doc_type=doc_type,
        title_path=list(title_path or [doc_id]),
        text=text,
        meta=dict(meta or {}),
    )


class StoreTestCase(unittest.TestCase):
    """Общая подготовка: пустая база в памяти и все репозитории поверх неё."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)
        self.repos = Repositories(self.db)

    # -- вспомогательные фикстуры ------------------------------------------

    def make_user(self, login: str = "engineer", password: str = "пароль-123",
                  role: str = "engineer") -> User:
        return self.repos.users.create(login, password, f"Инженер {login}", role)

    def make_document(self, doc_id: str = "literature/kniga",
                      doc_type: str = "literature", sha256: str = "a" * 64) -> Document:
        return self.repos.documents.upsert(
            doc_id, doc_type, f"Документ {doc_id}", f"/library/{doc_id}.md", sha256,
        )

    def make_indexed_document(self, doc_id: str, doc_type: str, texts: Sequence[str],
                              sha256: str | None = None) -> Document:
        """Документ вместе с чанками и лексическим индексом."""
        document = self.make_document(doc_id, doc_type, sha256 or (doc_id[:1] * 64))
        chunks = [
            make_chunk(f"{doc_id}#{index:04d}", doc_id, doc_type, text,
                       title_path=[doc_id, f"Раздел {index}"])
            for index, text in enumerate(texts)
        ]
        self.repos.chunks.replace_for_document(document, chunks)
        return self.repos.documents.by_doc_id(doc_id)

    def make_case(self, case_id: str = "CASE-1", status: str | None = None) -> Case:
        case = self.repos.cases.create(
            case_id, "signal_issue", {"measurements": {"snr": {"value": 13.7}}},
            digest="digest-1", title="Падение SNR", customer="ООО «Связь»",
        )
        if status:
            self.repos.cases.set_status(case.id, status)
            case = self.repos.cases.get(case.id)
        return case

    def make_report(self, case_ref: int, sections: Sequence[Dict[str, Any]] | None = None,
                    issues: Sequence[Dict[str, Any]] = ()) -> Report:
        if sections is None:
            sections = [
                {"section_id": "summary", "title": "Резюме", "text": "Резюме черновика."},
                {"section_id": "method", "title": "Методика", "text": "Методика черновика.",
                 "sources": ["literature/kniga#0000"], "missing_facts": ["evm"]},
            ]
        return self.repos.reports.create(case_ref, "# Отчёт\n", {"outline": "signal_issue"},
                                         issues, sections)

    def set_column(self, table: str, column: str, value: Any, where: str,
                   params: Sequence[Any]) -> None:
        """Правит служебные поля напрямую — так проще подделать время."""
        self.db.execute(f"UPDATE {table} SET {column} = ? WHERE {where}", (value, *params))
        self.db.commit()


# ------------------------------------------------------------------ схема ---

class DatabaseTests(StoreTestCase):
    """Схема, транзакции и служебные методы Database."""

    def test_schema_creates_all_tables(self):
        rows = self.db.query("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        names = {row["name"] for row in rows}
        for table in ("meta", "users", "sessions", "documents", "chunks", "chunks_fts",
                      "embeddings", "cases", "reports", "report_sections", "edit_pairs",
                      "audit"):
            self.assertIn(table, names)

    def test_schema_version_is_written_to_meta(self):
        self.assertEqual(
            self.db.scalar("SELECT value FROM meta WHERE key = 'schema_version'"),
            SCHEMA_VERSION,
        )

    def test_migrate_is_idempotent(self):
        self.make_user("admin", role="admin")
        self.db.migrate()
        self.db.migrate()
        self.assertEqual(self.repos.users.count(), 1)
        self.assertEqual(
            self.db.scalar("SELECT count(*) FROM meta WHERE key = 'schema_version'"), 1
        )
        self.assertEqual(
            self.db.scalar("SELECT value FROM meta WHERE key = 'schema_version'"),
            SCHEMA_VERSION,
        )

    def test_migrate_keeps_indexed_chunks(self):
        self.make_indexed_document("literature/kniga", "literature", ["Полоса сигнала."])
        self.db.migrate()
        self.assertEqual(self.repos.chunks.count(), 1)
        self.assertEqual(len(self.repos.chunks.search_lexical("полосы")), 1)

    def test_foreign_keys_pragma_is_on(self):
        self.assertEqual(self.db.scalar("PRAGMA foreign_keys"), 1)

    def test_foreign_key_violation_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?,?,?,?)",
                ("токен", 999, utcnow(), FUTURE),
            )
        self.db.connection.rollback()
        self.assertEqual(self.db.scalar("SELECT count(*) FROM sessions"), 0)

    def test_transaction_rolls_back_on_exception(self):
        with self.assertRaises(RuntimeError):
            with self.db.transaction() as connection:
                connection.execute(
                    "INSERT INTO cases(case_id, report_type, facts_json, created_at, updated_at) "
                    "VALUES('C-999','t','{}',?,?)",
                    (utcnow(), utcnow()),
                )
                raise RuntimeError("сбой посреди транзакции")
        self.assertEqual(self.repos.cases.count(), 0)

    def test_transaction_commits_on_success(self):
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO cases(case_id, report_type, facts_json, created_at, updated_at) "
                "VALUES('C-1','t','{}',?,?)",
                (utcnow(), utcnow()),
            )
        self.assertEqual(self.repos.cases.count(), 1)

    def test_counts_on_empty_database(self):
        self.assertEqual(
            self.db.counts(),
            {"users": 0, "documents": 0, "chunks": 0, "cases": 0, "reports": 0,
             "edit_pairs": 0, "audit": 0},
        )

    def test_counts_reflect_inserted_rows(self):
        user = self.make_user()
        case = self.make_case()
        self.make_report(case.id)
        self.make_indexed_document("standards/gost", "standards", ["Первый.", "Второй."])
        self.repos.audit.log("проверка", user=user)
        counts = self.db.counts()
        self.assertEqual(counts["users"], 1)
        self.assertEqual(counts["cases"], 1)
        self.assertEqual(counts["reports"], 1)
        self.assertEqual(counts["documents"], 1)
        self.assertEqual(counts["chunks"], 2)
        self.assertEqual(counts["audit"], 1)

    def test_query_helpers_on_missing_rows(self):
        self.assertEqual(self.db.query("SELECT * FROM users"), [])
        self.assertIsNone(self.db.query_one("SELECT * FROM users WHERE id = 1"))
        self.assertIsNone(self.db.scalar("SELECT login FROM users WHERE id = 1"))

    def test_executemany_inserts_batch(self):
        self.db.executemany(
            "INSERT INTO meta(key, value) VALUES(?,?)", [("a", "1"), ("b", "2")]
        )
        self.db.commit()
        self.assertEqual(self.db.scalar("SELECT value FROM meta WHERE key = 'b'"), "2")

    def test_file_database_creates_parent_dirs_and_uses_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "вложенный" / "каталог" / "store.db"
            db = Database(path)
            self.addCleanup(db.close)
            self.assertTrue(path.exists())
            self.assertEqual(str(db.scalar("PRAGMA journal_mode")).lower(), "wal")
            self.assertEqual(db.counts()["users"], 0)

    def test_repositories_open_persists_data_between_connections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "store.db")
            repos = Repositories.open(path)
            repos.users.create("admin", "пароль-123", "Админ", "admin")
            repos.close()
            again = Repositories.open(path)
            self.addCleanup(again.close)
            user = again.users.by_login("admin")
            self.assertIsNotNone(user)
            self.assertEqual(user.role, "admin")


# ---------------------------------------------------------------- пароли ---

class PasswordTests(unittest.TestCase):
    """scrypt: соль, проверка и устойчивость к мусору на входе."""

    def test_hash_format(self):
        parts = hash_password("пароль-123").split("$")
        self.assertEqual(len(parts), 6)
        self.assertEqual(parts[0], "scrypt")
        self.assertEqual(len(parts[4]), 32)  # 16 байт соли в hex
        self.assertEqual(len(parts[5]), 64)  # 32 байта ключа в hex

    def test_same_password_gives_different_hashes(self):
        first = hash_password("пароль-123")
        second = hash_password("пароль-123")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("пароль-123", first))
        self.assertTrue(verify_password("пароль-123", second))

    def test_verify_accepts_correct_password(self):
        self.assertTrue(verify_password("пароль-123", hash_password("пароль-123")))

    def test_verify_rejects_wrong_password(self):
        encoded = hash_password("пароль-123")
        self.assertFalse(verify_password("пароль-124", encoded))
        self.assertFalse(verify_password("", encoded))
        self.assertFalse(verify_password("ПАРОЛЬ-123", encoded))

    def test_verify_handles_unicode_and_long_passwords(self):
        password = "Полоса пропускания — 25 кГц! " * 10
        self.assertTrue(verify_password(password, hash_password(password)))

    def test_verify_rejects_broken_encoded_strings(self):
        encoded = hash_password("пароль-123")
        broken = [
            "",
            "мусор",
            "scrypt$16384$8$1$deadbeef",              # не хватает полей
            "scrypt$много$8$1$aa$bb",                 # нечисловые параметры
            "scrypt$16384$8$1$zz$" + "aa" * 32,       # соль не hex
            "pbkdf2$16384$8$1$aa$bb",                 # чужой алгоритм
            encoded + "$лишнее",
            encoded[:-1],                             # обрезанный хеш
        ]
        for value in broken:
            with self.subTest(encoded=value):
                self.assertFalse(verify_password("пароль-123", value))


# ------------------------------------------------------------ UserRepo ----

class UserRepoTests(StoreTestCase):
    def test_create_returns_user_with_defaults(self):
        user = self.repos.users.create("engineer", "пароль-123")
        self.assertEqual(user.login, "engineer")
        self.assertEqual(user.role, "engineer")
        self.assertEqual(user.full_name, "")
        self.assertTrue(user.active)
        self.assertTrue(user.created_at)

    def test_login_is_normalized(self):
        user = self.repos.users.create("  ГлавИнж  ", "пароль-123")
        self.assertEqual(user.login, "главинж")
        self.assertIsNotNone(self.repos.users.by_login("ГЛАВИНЖ"))
        self.assertIsNotNone(self.repos.users.by_login(" главинж "))

    def test_duplicate_login_raises_integrity_error(self):
        self.repos.users.create("admin", "пароль-123")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repos.users.create("ADMIN", "другой-пароль")
        self.assertEqual(self.repos.users.count(), 1)

    def test_database_survives_failed_create(self):
        self.repos.users.create("admin", "пароль-123")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repos.users.create("admin", "другой-пароль")
        # После отката соединение остаётся рабочим.
        self.repos.users.create("engineer", "пароль-123")
        self.assertEqual(self.repos.users.count(), 2)

    def test_get_and_by_login_for_unknown(self):
        self.assertIsNone(self.repos.users.get(404))
        self.assertIsNone(self.repos.users.by_login("нет-такого"))

    def test_authenticate_accepts_correct_credentials(self):
        created = self.repos.users.create("engineer", "пароль-123")
        user = self.repos.users.authenticate("Engineer", "пароль-123")
        self.assertIsNotNone(user)
        self.assertEqual(user.id, created.id)

    def test_authenticate_rejects_wrong_password_and_unknown_login(self):
        self.repos.users.create("engineer", "пароль-123")
        self.assertIsNone(self.repos.users.authenticate("engineer", "пароль-124"))
        self.assertIsNone(self.repos.users.authenticate("нет-такого", "пароль-123"))

    def test_authenticate_rejects_inactive_user(self):
        user = self.repos.users.create("engineer", "пароль-123")
        self.repos.users.set_active(user.id, False)
        self.assertIsNone(self.repos.users.authenticate("engineer", "пароль-123"))
        self.assertFalse(self.repos.users.get(user.id).active)
        self.repos.users.set_active(user.id, True)
        self.assertIsNotNone(self.repos.users.authenticate("engineer", "пароль-123"))

    def test_set_password_invalidates_old_password(self):
        user = self.repos.users.create("engineer", "пароль-123")
        old_hash = self.db.scalar("SELECT password_hash FROM users WHERE id = ?", (user.id,))
        self.repos.users.set_password(user.id, "новый-пароль")
        new_hash = self.db.scalar("SELECT password_hash FROM users WHERE id = ?", (user.id,))
        self.assertNotEqual(old_hash, new_hash)
        self.assertIsNone(self.repos.users.authenticate("engineer", "пароль-123"))
        self.assertIsNotNone(self.repos.users.authenticate("engineer", "новый-пароль"))

    def test_list_all_is_sorted_by_login(self):
        for login in ("яков", "admin", "борис"):
            self.repos.users.create(login, "пароль-123")
        self.assertEqual([u.login for u in self.repos.users.list_all()],
                         ["admin", "борис", "яков"])

    def test_count(self):
        self.assertEqual(self.repos.users.count(), 0)
        self.make_user("a")
        self.make_user("b")
        self.assertEqual(self.repos.users.count(), 2)

    def test_role_properties(self):
        admin = self.repos.users.create("admin", "пароль-123", role="admin")
        viewer = self.repos.users.create("viewer", "пароль-123", role="viewer")
        engineer = self.repos.users.create("engineer", "пароль-123")
        self.assertTrue(admin.is_admin)
        self.assertTrue(admin.can_edit)
        self.assertTrue(engineer.can_edit)
        self.assertFalse(engineer.is_admin)
        self.assertFalse(viewer.can_edit)


# --------------------------------------------------------- SessionRepo ----

class SessionRepoTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = self.make_user("engineer")

    def test_create_and_resolve(self):
        token = self.repos.sessions.create(self.user.id, ttl_hours=12, user_agent="curl")
        self.assertTrue(token)
        user = self.repos.sessions.resolve(token)
        self.assertIsNotNone(user)
        self.assertEqual(user.id, self.user.id)

    def test_tokens_are_unique(self):
        tokens = {self.repos.sessions.create(self.user.id) for _ in range(5)}
        self.assertEqual(len(tokens), 5)

    def test_resolve_unknown_token(self):
        self.assertIsNone(self.repos.sessions.resolve("нет-такого-токена"))

    def test_user_agent_is_truncated(self):
        token = self.repos.sessions.create(self.user.id, user_agent="я" * 500)
        stored = self.db.scalar("SELECT user_agent FROM sessions WHERE token = ?", (token,))
        self.assertEqual(len(stored), 200)

    def test_expired_session_is_rejected_and_removed(self):
        token = self.repos.sessions.create(self.user.id)
        self.set_column("sessions", "expires_at", PAST, "token = ?", (token,))
        self.assertIsNone(self.repos.sessions.resolve(token))
        self.assertEqual(self.db.scalar("SELECT count(*) FROM sessions"), 0)

    def test_session_of_inactive_user_is_rejected(self):
        token = self.repos.sessions.create(self.user.id)
        self.repos.users.set_active(self.user.id, False)
        self.assertIsNone(self.repos.sessions.resolve(token))
        # Сессия не удаляется: пользователя могут вернуть в строй.
        self.assertEqual(self.db.scalar("SELECT count(*) FROM sessions"), 1)

    def test_delete_removes_only_one_token(self):
        first = self.repos.sessions.create(self.user.id)
        second = self.repos.sessions.create(self.user.id)
        self.repos.sessions.delete(first)
        self.assertIsNone(self.repos.sessions.resolve(first))
        self.assertIsNotNone(self.repos.sessions.resolve(second))

    def test_delete_for_user_removes_all_tokens(self):
        other = self.make_user("second")
        self.repos.sessions.create(self.user.id)
        self.repos.sessions.create(self.user.id)
        keep = self.repos.sessions.create(other.id)
        self.repos.sessions.delete_for_user(self.user.id)
        self.assertEqual(self.db.scalar("SELECT count(*) FROM sessions"), 1)
        self.assertIsNotNone(self.repos.sessions.resolve(keep))

    def test_purge_expired_returns_number_of_removed(self):
        expired_one = self.repos.sessions.create(self.user.id)
        expired_two = self.repos.sessions.create(self.user.id)
        alive = self.repos.sessions.create(self.user.id)
        for token in (expired_one, expired_two):
            self.set_column("sessions", "expires_at", PAST, "token = ?", (token,))
        self.assertEqual(self.repos.sessions.purge_expired(), 2)
        self.assertEqual(self.db.scalar("SELECT count(*) FROM sessions"), 1)
        self.assertIsNotNone(self.repos.sessions.resolve(alive))

    def test_purge_expired_on_empty_table(self):
        self.assertEqual(self.repos.sessions.purge_expired(), 0)

    def test_sessions_are_deleted_with_user(self):
        token = self.repos.sessions.create(self.user.id)
        self.db.execute("DELETE FROM users WHERE id = ?", (self.user.id,))
        self.db.commit()
        self.assertEqual(self.db.scalar("SELECT count(*) FROM sessions"), 0)
        self.assertIsNone(self.repos.sessions.resolve(token))


# -------------------------------------------------------- DocumentRepo ----

class DocumentRepoTests(StoreTestCase):
    def test_upsert_inserts_new_document(self):
        document = self.repos.documents.upsert(
            "standards/gost-r-52", "standards", "ГОСТ Р 52", "/library/gost.md",
            "b" * 64, confidentiality="public", meta={"year": "2019"},
        )
        self.assertEqual(document.doc_id, "standards/gost-r-52")
        self.assertEqual(document.doc_type, "standards")
        self.assertEqual(document.confidentiality, "public")
        self.assertEqual(document.meta, {"year": "2019"})
        self.assertEqual(document.chunk_count, 0)
        self.assertIsNone(document.indexed_at)

    def test_upsert_updates_existing_document_keeping_id(self):
        first = self.repos.documents.upsert(
            "literature/kniga", "literature", "Старое название", "/old.md", "a" * 64,
        )
        second = self.repos.documents.upsert(
            "literature/kniga", "standards", "Новое название", "/new.md", "c" * 64,
            confidentiality="nda", meta={"vendor": "ACME"},
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.title, "Новое название")
        self.assertEqual(second.doc_type, "standards")
        self.assertEqual(second.source_path, "/new.md")
        self.assertEqual(second.sha256, "c" * 64)
        self.assertEqual(second.confidentiality, "nda")
        self.assertEqual(second.meta, {"vendor": "ACME"})
        self.assertEqual(second.created_at, first.created_at)
        self.assertEqual(len(self.repos.documents.list()), 1)

    def test_by_sha256(self):
        self.make_document("literature/kniga", "literature", "a" * 64)
        found = self.repos.documents.by_sha256("a" * 64)
        self.assertIsNotNone(found)
        self.assertEqual(found.doc_id, "literature/kniga")
        self.assertIsNone(self.repos.documents.by_sha256("f" * 64))

    def test_by_doc_id_for_unknown(self):
        self.assertIsNone(self.repos.documents.by_doc_id("нет/такого"))

    def test_list_filters_by_type_and_sorts(self):
        self.repos.documents.upsert("literature/b", "literature", "Бета", "/b.md", "1" * 64)
        self.repos.documents.upsert("literature/a", "literature", "Альфа", "/a.md", "2" * 64)
        self.repos.documents.upsert("standards/g", "standards", "ГОСТ", "/g.md", "3" * 64)
        self.assertEqual([d.title for d in self.repos.documents.list("literature")],
                         ["Альфа", "Бета"])
        self.assertEqual([d.title for d in self.repos.documents.list("standards")], ["ГОСТ"])
        self.assertEqual(self.repos.documents.list("reports"), [])
        self.assertEqual([d.title for d in self.repos.documents.list()],
                         ["Альфа", "Бета", "ГОСТ"])

    def test_mark_indexed_sets_counters(self):
        document = self.make_document()
        self.repos.documents.mark_indexed(document.doc_id, 7)
        updated = self.repos.documents.by_doc_id(document.doc_id)
        self.assertEqual(updated.chunk_count, 7)
        self.assertIsNotNone(updated.indexed_at)

    def test_delete_removes_chunks_fts_and_vectors(self):
        document = self.make_indexed_document(
            "literature/kniga", "literature", ["Полоса сигнала.", "Уровень шума."],
        )
        other = self.make_indexed_document(
            "standards/gost", "standards", ["Полоса по стандарту."], sha256="b" * 64,
        )
        self.repos.vectors.put_many("bge-m3", {
            "literature/kniga#0000": [1.0, 0.0],
            "standards/gost#0000": [0.0, 1.0],
        })
        self.repos.documents.delete(document.doc_id)
        self.assertIsNone(self.repos.documents.by_doc_id(document.doc_id))
        self.assertEqual(self.repos.chunks.count(), 1)
        self.assertEqual(self.db.scalar("SELECT count(*) FROM chunks_fts"), 1)
        self.assertEqual(self.repos.vectors.count(), 1)
        self.assertEqual(self.repos.vectors.missing(["literature/kniga#0000"]),
                         ["literature/kniga#0000"])
        # Чужой документ не пострадал.
        self.assertIsNotNone(self.repos.documents.by_doc_id(other.doc_id))
        self.assertEqual([uid for uid, _ in self.repos.chunks.search_lexical("полоса")],
                         ["standards/gost#0000"])

    def test_delete_unknown_document_is_noop(self):
        self.make_document()
        self.repos.documents.delete("нет/такого")
        self.assertEqual(len(self.repos.documents.list()), 1)

    def test_stats_groups_by_type(self):
        self.make_indexed_document("literature/a", "literature", ["Раз.", "Два."])
        self.make_indexed_document("literature/b", "literature", ["Три."], sha256="b" * 64)
        self.make_indexed_document("standards/g", "standards", ["Четыре."], sha256="c" * 64)
        self.assertEqual(self.repos.documents.stats(), {
            "literature": {"documents": 2, "chunks": 3},
            "standards": {"documents": 1, "chunks": 1},
        })

    def test_stats_on_empty_library(self):
        self.assertEqual(self.repos.documents.stats(), {})


# ----------------------------------------------------------- ChunkRepo ----

class ChunkRepoTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.literature = self.make_indexed_document(
            "literature/kniga", "literature",
            ["Занимаемая полоса сигнала составляет 25 кГц.",
             "Отношение сигнал/шум измерено методом усреднения."],
        )
        self.standards = self.make_indexed_document(
            "standards/gost", "standards",
            ["Полосы пропускания фильтров нормируются стандартом."],
            sha256="b" * 64,
        )

    def test_replace_for_document_returns_count_and_fills_counters(self):
        document = self.repos.documents.by_doc_id("literature/kniga")
        self.assertEqual(document.chunk_count, 2)
        self.assertIsNotNone(document.indexed_at)
        self.assertEqual(self.repos.chunks.count(), 3)

    def test_replace_for_document_drops_previous_chunks(self):
        document = self.repos.documents.by_doc_id("literature/kniga")
        self.repos.vectors.put_many("bge-m3", {"literature/kniga#0000": [1.0, 0.0]})
        new_chunks = [make_chunk("literature/kniga#0000", "literature/kniga", "literature",
                                 "Совершенно другой текст про джиттер.")]
        written = self.repos.chunks.replace_for_document(document, new_chunks)
        self.assertEqual(written, 1)
        self.assertIsNone(self.repos.chunks.get("literature/kniga#0001"))
        self.assertIn("джиттер", self.repos.chunks.get("literature/kniga#0000").text)
        self.assertEqual(self.repos.documents.by_doc_id("literature/kniga").chunk_count, 1)
        # Старый индекс и старые векторы не остаются мусором.
        self.assertEqual(self.db.scalar("SELECT count(*) FROM chunks_fts"), 2)
        self.assertEqual(self.repos.vectors.count(), 0)
        self.assertEqual(self.repos.chunks.search_lexical("шум"), [])

    def test_replace_for_document_with_empty_list(self):
        document = self.repos.documents.by_doc_id("literature/kniga")
        self.assertEqual(self.repos.chunks.replace_for_document(document, []), 0)
        self.assertEqual(self.repos.documents.by_doc_id("literature/kniga").chunk_count, 0)
        self.assertEqual(self.repos.chunks.count(), 1)

    def test_get_restores_chunk_fields(self):
        chunk = self.repos.chunks.get("literature/kniga#0000")
        self.assertIsInstance(chunk, Chunk)
        self.assertEqual(chunk.doc_id, "literature/kniga")
        self.assertEqual(chunk.doc_type, "literature")
        self.assertEqual(chunk.title_path, ["literature/kniga", "Раздел 0"])
        self.assertIn("25 кГц", chunk.text)
        self.assertIsNone(self.repos.chunks.get("нет#0000"))

    def test_get_many_preserves_requested_order(self):
        uids = ["standards/gost#0000", "literature/kniga#0001", "literature/kniga#0000"]
        self.assertEqual([c.chunk_id for c in self.repos.chunks.get_many(uids)], uids)

    def test_get_many_skips_unknown_and_empty(self):
        got = self.repos.chunks.get_many(["нет#0000", "literature/kniga#0000"])
        self.assertEqual([c.chunk_id for c in got], ["literature/kniga#0000"])
        self.assertEqual(self.repos.chunks.get_many([]), [])

    def test_all_chunks_and_limit(self):
        self.assertEqual(len(self.repos.chunks.all_chunks()), 3)
        self.assertEqual(len(self.repos.chunks.all_chunks(limit=2)), 2)

    def test_search_lexical_matches_stemmed_form(self):
        hits = self.repos.chunks.search_lexical("полосы")
        uids = [uid for uid, _ in hits]
        self.assertIn("standards/gost#0000", uids)
        self.assertIn("literature/kniga#0000", uids)
        self.assertNotIn("literature/kniga#0001", uids)

    def test_search_lexical_matches_breadcrumbs(self):
        # В индекс попадают крошки: «Раздел 1» ищется, хотя в теле его нет.
        hits = self.repos.chunks.search_lexical("раздел")
        self.assertTrue(hits)

    def test_search_lexical_filters_by_doc_types(self):
        hits = self.repos.chunks.search_lexical("полосы", doc_types=["standards"])
        self.assertEqual([uid for uid, _ in hits], ["standards/gost#0000"])
        self.assertEqual(self.repos.chunks.search_lexical("полосы", doc_types=["reports"]), [])
        self.assertTrue(self.repos.chunks.search_lexical("полосы", doc_types=[]))

    def test_search_lexical_scores_are_sorted_descending(self):
        hits = self.repos.chunks.search_lexical("полоса сигнала")
        scores = [score for _, score in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_search_lexical_respects_limit(self):
        self.assertEqual(len(self.repos.chunks.search_lexical("полосы", limit=1)), 1)

    def test_search_lexical_on_empty_query(self):
        for query in ("", "   ", "\n\t"):
            with self.subTest(query=query):
                self.assertEqual(self.repos.chunks.search_lexical(query), [])

    def test_search_lexical_on_stopwords_only(self):
        self.assertEqual(self.repos.chunks.search_lexical("и в на что для при"), [])

    def test_search_lexical_escapes_fts_syntax(self):
        queries = [
            'полоса"',
            'полоса (25 кГц)',
            '"полоса" OR chunks_fts',
            "полоса* NEAR/2 шум",
            "полоса'; DROP TABLE chunks; --",
            "^полоса-",
            "*",
            "-",
        ]
        for query in queries:
            with self.subTest(query=query):
                hits = self.repos.chunks.search_lexical(query)
                self.assertIsInstance(hits, list)
        # Таблицы на месте, ничего не «уронилось».
        self.assertEqual(self.repos.chunks.count(), 3)

    def test_search_lexical_returns_nothing_for_unknown_word(self):
        self.assertEqual(self.repos.chunks.search_lexical("интермодуляция"), [])

    def test_count(self):
        self.assertEqual(self.repos.chunks.count(), 3)


# ---------------------------------------------------------- VectorRepo ----

class VectorRepoTests(StoreTestCase):
    def assert_vectors_close(self, got: List[float], expected: Sequence[float]) -> None:
        self.assertEqual(len(got), len(expected))
        for value, reference in zip(got, expected):
            self.assertAlmostEqual(value, reference, places=6)

    def test_pack_unpack_round_trip(self):
        values = [0.1, -0.25, 1e-3, 12.5]
        self.assert_vectors_close(unpack_vector(pack_vector(values)), values)

    def test_put_many_and_get_many(self):
        vectors = {"a#0": [0.1, 0.2, 0.3], "b#0": [-1.0, 0.5, 0.0]}
        self.repos.vectors.put_many("bge-m3", vectors)
        got = self.repos.vectors.get_many(["a#0", "b#0"])
        self.assertEqual(set(got), {"a#0", "b#0"})
        self.assert_vectors_close(got["a#0"], vectors["a#0"])
        self.assert_vectors_close(got["b#0"], vectors["b#0"])

    def test_dimension_is_stored(self):
        self.repos.vectors.put_many("bge-m3", {"a#0": [0.1] * 5})
        self.assertEqual(self.db.scalar("SELECT dim FROM embeddings WHERE chunk_uid = 'a#0'"), 5)

    def test_put_many_overwrites_existing_vector(self):
        self.repos.vectors.put_many("bge-m3", {"a#0": [1.0, 0.0]})
        self.repos.vectors.put_many("другая-модель", {"a#0": [0.0, 1.0, 0.0]})
        self.assertEqual(self.repos.vectors.count(), 1)
        self.assert_vectors_close(self.repos.vectors.get_many(["a#0"])["a#0"], [0.0, 1.0, 0.0])
        self.assertEqual(
            self.db.scalar("SELECT model FROM embeddings WHERE chunk_uid = 'a#0'"),
            "другая-модель",
        )

    def test_get_many_with_empty_and_unknown(self):
        self.repos.vectors.put_many("bge-m3", {"a#0": [1.0]})
        self.assertEqual(self.repos.vectors.get_many([]), {})
        self.assertEqual(self.repos.vectors.get_many(["нет"]), {})
        self.assertEqual(set(self.repos.vectors.get_many(["a#0", "нет"])), {"a#0"})

    def test_missing_returns_absent_uids_in_order(self):
        self.repos.vectors.put_many("bge-m3", {"b#0": [1.0]})
        self.assertEqual(self.repos.vectors.missing(["a#0", "b#0", "c#0"]), ["a#0", "c#0"])
        self.assertEqual(self.repos.vectors.missing([]), [])
        self.assertEqual(self.repos.vectors.missing(["b#0"]), [])

    def test_all_vectors_sorted_and_filtered_by_model(self):
        self.repos.vectors.put_many("bge-m3", {"b#0": [1.0, 0.0], "a#0": [0.0, 1.0]})
        self.repos.vectors.put_many("e5", {"c#0": [0.5, 0.5]})
        uids, vectors = self.repos.vectors.all_vectors()
        self.assertEqual(uids, ["a#0", "b#0", "c#0"])
        self.assert_vectors_close(vectors[0], [0.0, 1.0])
        uids, vectors = self.repos.vectors.all_vectors("e5")
        self.assertEqual(uids, ["c#0"])
        self.assert_vectors_close(vectors[0], [0.5, 0.5])
        self.assertEqual(self.repos.vectors.all_vectors("нет-такой"), ([], []))

    def test_clear_removes_everything(self):
        self.repos.vectors.put_many("bge-m3", {"a#0": [1.0], "b#0": [0.0]})
        self.assertEqual(self.repos.vectors.count(), 2)
        self.repos.vectors.clear()
        self.assertEqual(self.repos.vectors.count(), 0)
        self.assertEqual(self.repos.vectors.all_vectors(), ([], []))

    def test_put_many_with_empty_mapping(self):
        self.repos.vectors.put_many("bge-m3", {})
        self.assertEqual(self.repos.vectors.count(), 0)


# ------------------------------------------------------------ CaseRepo ----

class CaseRepoTests(StoreTestCase):
    def test_create_returns_case(self):
        user = self.make_user()
        case = self.repos.cases.create(
            "CASE-2024-118", "signal_issue", {"case_id": "CASE-2024-118"},
            digest="deadbeef", title="Срыв связи", customer="ООО «Связь»", user_id=user.id,
        )
        self.assertEqual(case.case_id, "CASE-2024-118")
        self.assertEqual(case.report_type, "signal_issue")
        self.assertEqual(case.status, "new")
        self.assertEqual(case.facts, {"case_id": "CASE-2024-118"})
        self.assertEqual(case.facts_digest, "deadbeef")
        self.assertEqual(case.created_by, user.id)
        self.assertEqual(case.created_at, case.updated_at)

    def test_duplicate_case_id_raises(self):
        self.make_case("CASE-1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.make_case("CASE-1")
        self.assertEqual(self.repos.cases.count(), 1)

    def test_get_and_by_case_id(self):
        case = self.make_case("CASE-1")
        self.assertEqual(self.repos.cases.get(case.id).case_id, "CASE-1")
        self.assertEqual(self.repos.cases.by_case_id("CASE-1").id, case.id)
        self.assertIsNone(self.repos.cases.get(404))
        self.assertIsNone(self.repos.cases.by_case_id("нет"))

    def test_list_is_sorted_by_updated_at_desc(self):
        for index, case_id in enumerate(("CASE-1", "CASE-2", "CASE-3"), start=1):
            case = self.make_case(case_id)
            self.set_column("cases", "updated_at", f"2026-01-0{index}T00:00:00+00:00",
                            "id = ?", (case.id,))
        self.assertEqual([c.case_id for c in self.repos.cases.list()],
                         ["CASE-3", "CASE-2", "CASE-1"])

    def test_list_filters_by_status(self):
        self.make_case("CASE-1")
        self.make_case("CASE-2", status="approved")
        self.assertEqual([c.case_id for c in self.repos.cases.list(status="approved")],
                         ["CASE-2"])
        self.assertEqual(self.repos.cases.list(status="archived"), [])
        self.assertEqual(len(self.repos.cases.list()), 2)

    def test_list_pagination(self):
        for index, case_id in enumerate(("CASE-1", "CASE-2", "CASE-3"), start=1):
            case = self.make_case(case_id)
            self.set_column("cases", "updated_at", f"2026-01-0{index}T00:00:00+00:00",
                            "id = ?", (case.id,))
        first = self.repos.cases.list(limit=2)
        second = self.repos.cases.list(limit=2, offset=2)
        self.assertEqual([c.case_id for c in first], ["CASE-3", "CASE-2"])
        self.assertEqual([c.case_id for c in second], ["CASE-1"])
        self.assertEqual(self.repos.cases.list(limit=2, offset=10), [])

    def test_update_facts_replaces_facts_and_digest(self):
        case = self.make_case()
        self.set_column("cases", "updated_at", PAST, "id = ?", (case.id,))
        self.repos.cases.update_facts(case.id, {"measurements": {"evm": {"value": 3.1}}}, "новый")
        updated = self.repos.cases.get(case.id)
        self.assertEqual(updated.facts, {"measurements": {"evm": {"value": 3.1}}})
        self.assertEqual(updated.facts_digest, "новый")
        self.assertGreater(updated.updated_at, PAST)

    def test_update_facts_coalesces_title_and_customer(self):
        case = self.make_case()
        self.repos.cases.update_facts(case.id, {}, "d")
        kept = self.repos.cases.get(case.id)
        self.assertEqual(kept.title, "Падение SNR")
        self.assertEqual(kept.customer, "ООО «Связь»")
        self.repos.cases.update_facts(case.id, {}, "d", title="Новый заголовок")
        half = self.repos.cases.get(case.id)
        self.assertEqual(half.title, "Новый заголовок")
        self.assertEqual(half.customer, "ООО «Связь»")
        self.repos.cases.update_facts(case.id, {}, "d", customer="АО «Радио»")
        both = self.repos.cases.get(case.id)
        self.assertEqual(both.title, "Новый заголовок")
        self.assertEqual(both.customer, "АО «Радио»")

    def test_set_status_touches_updated_at(self):
        case = self.make_case()
        self.set_column("cases", "updated_at", PAST, "id = ?", (case.id,))
        self.repos.cases.set_status(case.id, "review")
        updated = self.repos.cases.get(case.id)
        self.assertEqual(updated.status, "review")
        self.assertGreater(updated.updated_at, PAST)

    def test_delete_removes_case_with_reports(self):
        case = self.make_case()
        self.make_report(case.id)
        self.repos.cases.delete(case.id)
        self.assertIsNone(self.repos.cases.get(case.id))
        self.assertEqual(self.db.scalar("SELECT count(*) FROM reports"), 0)
        self.assertEqual(self.db.scalar("SELECT count(*) FROM report_sections"), 0)

    def test_count_with_and_without_status(self):
        self.make_case("CASE-1")
        self.make_case("CASE-2", status="approved")
        self.make_case("CASE-3", status="approved")
        self.assertEqual(self.repos.cases.count(), 3)
        self.assertEqual(self.repos.cases.count("approved"), 2)
        self.assertEqual(self.repos.cases.count("archived"), 0)


# ---------------------------------------------------------- ReportRepo ----

class ReportRepoTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.case = self.make_case("CASE-1")

    def test_create_returns_first_version_with_sections(self):
        report = self.make_report(self.case.id)
        self.assertEqual(report.version, 1)
        self.assertEqual(report.status, "draft")
        self.assertEqual(report.case_ref, self.case.id)
        self.assertEqual(report.meta, {"outline": "signal_issue"})
        self.assertEqual(len(report.sections), 2)
        method = report.sections[1]
        self.assertEqual(method.sources, ["literature/kniga#0000"])
        self.assertEqual(method.missing_facts, ["evm"])
        self.assertEqual(method.draft_text, method.text)
        self.assertFalse(method.edited)
        self.assertEqual(method.regenerated, 0)

    def test_version_increments_within_case(self):
        self.assertEqual(self.make_report(self.case.id).version, 1)
        self.assertEqual(self.make_report(self.case.id).version, 2)
        self.assertEqual(self.make_report(self.case.id).version, 3)
        self.assertEqual([r.version for r in self.repos.reports.list_for_case(self.case.id)],
                         [3, 2, 1])

    def test_version_is_independent_per_case(self):
        other = self.make_case("CASE-2")
        self.make_report(self.case.id)
        self.make_report(self.case.id)
        self.assertEqual(self.make_report(other.id).version, 1)

    def test_sections_are_returned_in_ord_order(self):
        report = self.make_report(self.case.id, sections=[
            {"section_id": "z-первая", "title": "Первая", "text": "1"},
            {"section_id": "a-вторая", "title": "Вторая", "text": "2"},
            {"section_id": "m-третья", "title": "Третья", "text": "3"},
        ])
        self.assertEqual([s.section_id for s in report.sections],
                         ["z-первая", "a-вторая", "m-третья"])
        self.assertEqual([s.ord for s in report.sections], [0, 1, 2])
        self.assertEqual([s.section_id for s in self.repos.reports.sections(report.id)],
                         ["z-первая", "a-вторая", "m-третья"])

    def test_get_without_sections(self):
        report = self.make_report(self.case.id)
        light = self.repos.reports.get(report.id, with_sections=False)
        self.assertEqual(light.sections, [])
        self.assertIsNone(self.repos.reports.get(404))

    def test_section_lookup(self):
        report = self.make_report(self.case.id)
        self.assertEqual(self.repos.reports.section(report.id, "summary").title, "Резюме")
        self.assertIsNone(self.repos.reports.section(report.id, "нет-такой"))

    def test_update_section_text_marks_edited(self):
        report = self.make_report(self.case.id)
        self.repos.reports.update_section_text(report.id, "summary", "Правка инженера.")
        section = self.repos.reports.section(report.id, "summary")
        self.assertEqual(section.text, "Правка инженера.")
        self.assertEqual(section.draft_text, "Резюме черновика.")
        self.assertTrue(section.edited)
        self.assertEqual(section.regenerated, 0)

    def test_update_section_text_can_keep_edited_flag_off(self):
        report = self.make_report(self.case.id)
        self.repos.reports.update_section_text(report.id, "summary", "Служебная правка",
                                               edited=False)
        self.assertFalse(self.repos.reports.section(report.id, "summary").edited)

    def test_replace_section_increments_regenerated_and_resets_edited(self):
        report = self.make_report(self.case.id)
        self.repos.reports.update_section_text(report.id, "summary", "Правка инженера.")
        self.repos.reports.replace_section(report.id, "summary", "Новый черновик модели.",
                                           ["standards/gost#0000"], ["snr"])
        section = self.repos.reports.section(report.id, "summary")
        self.assertEqual(section.text, "Новый черновик модели.")
        self.assertEqual(section.draft_text, "Новый черновик модели.")
        self.assertFalse(section.edited)
        self.assertEqual(section.regenerated, 1)
        self.assertEqual(section.sources, ["standards/gost#0000"])
        self.assertEqual(section.missing_facts, ["snr"])
        self.repos.reports.replace_section(report.id, "summary", "Ещё черновик.", [], [])
        self.assertEqual(self.repos.reports.section(report.id, "summary").regenerated, 2)

    def test_latest_for_case(self):
        self.make_report(self.case.id)
        newest = self.make_report(self.case.id, sections=[
            {"section_id": "summary", "title": "Резюме", "text": "Последняя версия."},
        ])
        latest = self.repos.reports.latest_for_case(self.case.id)
        self.assertEqual(latest.id, newest.id)
        self.assertEqual(latest.version, 2)
        self.assertEqual([s.text for s in latest.sections], ["Последняя версия."])
        light = self.repos.reports.latest_for_case(self.case.id, with_sections=False)
        self.assertEqual(light.sections, [])
        self.assertIsNone(self.repos.reports.latest_for_case(404))

    def test_update_markdown_and_meta(self):
        report = self.make_report(self.case.id)
        self.repos.reports.update_markdown(report.id, "# Новый текст\n")
        self.repos.reports.update_meta(report.id, {"llm": "qwen3-14b"})
        updated = self.repos.reports.get(report.id)
        self.assertEqual(updated.markdown, "# Новый текст\n")
        self.assertEqual(updated.meta, {"llm": "qwen3-14b"})

    def test_set_issues_and_counters(self):
        report = self.make_report(self.case.id)
        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)
        self.repos.reports.set_issues(report.id, [
            {"level": "error", "message": "число 42 отсутствует в факт-пакете"},
            {"level": "error", "message": "число 7 отсутствует в факт-пакете"},
            {"level": "warning", "message": "нет ссылки на источник"},
            {"message": "без уровня"},
        ])
        updated = self.repos.reports.get(report.id)
        self.assertEqual(len(updated.issues), 4)
        self.assertEqual(updated.error_count, 2)
        self.assertEqual(updated.warning_count, 1)

    def test_create_stores_issues(self):
        report = self.make_report(self.case.id, issues=[{"level": "warning", "message": "ok"}])
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report.error_count, 0)

    def test_set_status(self):
        report = self.make_report(self.case.id)
        self.repos.reports.set_status(report.id, "verified")
        self.assertEqual(self.repos.reports.get(report.id).status, "verified")

    def test_approve_sets_author_and_time(self):
        user = self.make_user("admin", role="admin")
        report = self.make_report(self.case.id)
        self.repos.reports.approve(report.id, user.id)
        approved = self.repos.reports.get(report.id)
        self.assertEqual(approved.status, "approved")
        self.assertEqual(approved.approved_by, user.id)
        self.assertTrue(approved.approved_at)

    def test_approve_without_user(self):
        report = self.make_report(self.case.id)
        self.repos.reports.approve(report.id, None)
        approved = self.repos.reports.get(report.id)
        self.assertEqual(approved.status, "approved")
        self.assertIsNone(approved.approved_by)

    def test_duplicate_version_is_rejected_by_schema(self):
        report = self.make_report(self.case.id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO reports(case_ref, version, markdown, created_at) VALUES(?,?,?,?)",
                (self.case.id, report.version, "# дубль", utcnow()),
            )
        self.db.connection.rollback()

    def test_sections_are_unique_per_report(self):
        report = self.make_report(self.case.id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO report_sections(report_id, section_id, title, ord, draft_text, "
                "text, updated_at) VALUES(?,?,?,?,?,?,?)",
                (report.id, "summary", "Резюме", 5, "x", "x", utcnow()),
            )
        self.db.connection.rollback()


# -------------------------------------------------------- EditPairRepo ----

class EditPairRepoTests(StoreTestCase):
    def add_pair(self, section_id: str = "summary", draft: str = "один два три четыре",
                 final: str = "один два три пять", report_type: str = "signal_issue",
                 **kwargs: Any) -> int:
        payload = {
            "case_id": "CASE-1",
            "report_id": None,
            "report_type": report_type,
            "section_id": section_id,
            "section_title": f"Секция {section_id}",
            "draft": draft,
            "final": final,
        }
        payload.update(kwargs)
        return self.repos.edits.add(**payload)

    def test_add_computes_edit_distance(self):
        pair_id = self.add_pair()
        pair = self.repos.edits.list()[0]
        self.assertEqual(pair.id, pair_id)
        self.assertAlmostEqual(pair.edit_distance, 0.25, places=4)
        self.assertEqual(pair.case_id, "CASE-1")
        self.assertEqual(pair.context, {})
        self.assertIsNone(pair.report_id)
        self.assertIsNone(pair.created_by)

    def test_add_keeps_context_and_author(self):
        user = self.make_user()
        case = self.make_case("CASE-1")
        report = self.make_report(case.id)
        self.add_pair(report_id=report.id, user_id=user.id, facts_digest="deadbeef",
                      context={"facts": {"snr": 13.7}, "sources": ["literature/kniga#0000"]})
        pair = self.repos.edits.list()[0]
        self.assertEqual(pair.report_id, report.id)
        self.assertEqual(pair.created_by, user.id)
        self.assertEqual(pair.facts_digest, "deadbeef")
        self.assertEqual(pair.context["sources"], ["literature/kniga#0000"])

    def test_identical_texts_give_zero_distance(self):
        self.add_pair(draft="Текст без правок.", final="Текст без правок.")
        self.assertEqual(self.repos.edits.list()[0].edit_distance, 0.0)

    def test_count(self):
        self.assertEqual(self.repos.edits.count(), 0)
        self.add_pair()
        self.add_pair(section_id="method")
        self.assertEqual(self.repos.edits.count(), 2)

    def test_mean_distance_overall_and_by_report_type(self):
        self.assertEqual(self.repos.edits.mean_distance(), 0.0)
        self.add_pair(draft="а б в г", final="а б в г")               # 0.0
        self.add_pair(draft="а б в г", final="д е ж з")               # 1.0
        self.add_pair(draft="а б в г", final="а б в д", report_type="rf_survey")  # 0.25
        self.assertAlmostEqual(self.repos.edits.mean_distance(), (0.0 + 1.0 + 0.25) / 3, places=4)
        self.assertAlmostEqual(self.repos.edits.mean_distance("signal_issue"), 0.5, places=4)
        self.assertAlmostEqual(self.repos.edits.mean_distance("rf_survey"), 0.25, places=4)
        self.assertEqual(self.repos.edits.mean_distance("нет-такого"), 0.0)

    def test_by_section_is_sorted_by_mean_distance(self):
        self.add_pair(section_id="summary", draft="а б в г", final="а б в д")   # 0.25
        self.add_pair(section_id="summary", draft="а б в г", final="а б в г")   # 0.0
        self.add_pair(section_id="method", draft="а б в г", final="д е ж з")    # 1.0
        rows = self.repos.edits.by_section()
        self.assertEqual([row["section_id"] for row in rows], ["method", "summary"])
        self.assertEqual(rows[0]["pairs"], 1)
        self.assertEqual(rows[0]["mean_distance"], 1.0)
        self.assertEqual(rows[1]["pairs"], 2)
        self.assertEqual(rows[1]["mean_distance"], 0.125)
        self.assertEqual(rows[1]["section_title"], "Секция summary")

    def test_by_section_on_empty_dataset(self):
        self.assertEqual(self.repos.edits.by_section(), [])

    def test_list_pagination_newest_first(self):
        ids = []
        for index, section in enumerate(("s1", "s2", "s3"), start=1):
            pair_id = self.add_pair(section_id=section)
            self.set_column("edit_pairs", "created_at", f"2026-01-0{index}T00:00:00+00:00",
                            "id = ?", (pair_id,))
            ids.append(pair_id)
        first_page = self.repos.edits.list(limit=2)
        second_page = self.repos.edits.list(limit=2, offset=2)
        self.assertEqual([p.id for p in first_page], [ids[2], ids[1]])
        self.assertEqual([p.id for p in second_page], [ids[0]])
        self.assertEqual(self.repos.edits.list(limit=2, offset=10), [])


# ----------------------------------------------------------- AuditRepo ----

class AuditRepoTests(StoreTestCase):
    def test_log_with_user(self):
        user = self.make_user("admin", role="admin")
        self.repos.audit.log("report.approve", user=user, object_type="report",
                             object_id=17, details={"version": 2})
        entry = self.repos.audit.list()[0]
        self.assertEqual(entry.action, "report.approve")
        self.assertEqual(entry.user_id, user.id)
        self.assertEqual(entry.login, "admin")
        self.assertEqual(entry.object_type, "report")
        self.assertEqual(entry.object_id, "17")   # приводится к строке
        self.assertEqual(entry.details, {"version": 2})
        self.assertTrue(entry.ts)

    def test_log_without_user(self):
        self.repos.audit.log("system.start")
        entry = self.repos.audit.list()[0]
        self.assertIsNone(entry.user_id)
        self.assertEqual(entry.login, "")
        self.assertEqual(entry.object_type, "")
        self.assertEqual(entry.object_id, "")
        self.assertEqual(entry.details, {})

    def test_list_is_newest_first(self):
        for action in ("первое", "второе", "третье"):
            self.repos.audit.log(action)
        self.assertEqual([e.action for e in self.repos.audit.list()],
                         ["третье", "второе", "первое"])

    def test_list_respects_limit(self):
        for index in range(5):
            self.repos.audit.log(f"действие-{index}")
        self.assertEqual(len(self.repos.audit.list(limit=2)), 2)
        self.assertEqual(self.repos.audit.list(limit=2)[0].action, "действие-4")

    def test_list_on_empty_journal(self):
        self.assertEqual(self.repos.audit.list(), [])


# ------------------------------------------------- расстояние правок ------

class NormalizedEditDistanceTests(unittest.TestCase):
    def test_identical_texts(self):
        self.assertEqual(normalized_edit_distance("Полоса 25 кГц", "Полоса 25 кГц"), 0.0)

    def test_completely_different_texts(self):
        self.assertEqual(normalized_edit_distance("один два три", "четыре пять шесть"), 1.0)

    def test_empty_final(self):
        self.assertEqual(normalized_edit_distance("инженер удалил секцию", ""), 1.0)

    def test_empty_draft(self):
        self.assertEqual(normalized_edit_distance("", "инженер написал сам"), 1.0)

    def test_both_empty(self):
        self.assertEqual(normalized_edit_distance("", ""), 0.0)
        self.assertEqual(normalized_edit_distance("   ", "\n"), 0.0)

    def test_partial_overlap_is_fractional(self):
        value = normalized_edit_distance("один два три четыре", "один два три пять")
        self.assertAlmostEqual(value, 0.25, places=4)
        self.assertGreater(value, 0.0)
        self.assertLess(value, 1.0)

    def test_normalized_by_longer_text(self):
        # Инженер сократил четыре слова до двух: изменилась половина.
        self.assertAlmostEqual(normalized_edit_distance("а б в г", "а б"), 0.5, places=4)
        self.assertAlmostEqual(normalized_edit_distance("а б", "а б в г"), 0.5, places=4)

    def test_whitespace_does_not_matter(self):
        self.assertEqual(normalized_edit_distance("а  б\nв", "а б в"), 0.0)

    def test_result_is_always_in_unit_range(self):
        pairs = [
            ("а б в", "а"),
            ("а", "а б в г д"),
            ("полоса 25 кГц", "полоса 30 кГц по данным измерения"),
        ]
        for draft, final in pairs:
            with self.subTest(draft=draft):
                value = normalized_edit_distance(draft, final)
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)


# -------------------------------------------------------------- модели ----

class ModelsTests(StoreTestCase):
    def test_broken_json_falls_back_to_default(self):
        document = self.make_document()
        self.set_column("documents", "meta_json", "{это не json", "id = ?", (document.id,))
        self.assertEqual(self.repos.documents.by_doc_id(document.doc_id).meta, {})
        case = self.make_case()
        self.set_column("cases", "facts_json", "", "id = ?", (case.id,))
        self.assertEqual(self.repos.cases.get(case.id).facts, {})
        report = self.make_report(case.id)
        self.set_column("reports", "issues_json", "[неполный", "id = ?", (report.id,))
        self.assertEqual(self.repos.reports.get(report.id).issues, [])

    def test_document_to_dict_shortens_sha(self):
        document = self.make_document("literature/kniga", "literature", "a" * 64)
        data = document.to_dict()
        self.assertEqual(data["sha256"], "a" * 12)
        self.assertEqual(data["doc_id"], "literature/kniga")

    def test_case_to_dict_hides_facts_by_default(self):
        case = self.make_case()
        self.assertNotIn("facts", case.to_dict())
        self.assertIn("facts", case.to_dict(with_facts=True))
        self.assertEqual(case.to_dict(with_facts=True)["facts"], case.facts)

    def test_report_to_dict_contains_counters_and_sections(self):
        case = self.make_case()
        report = self.make_report(case.id, issues=[{"level": "error", "message": "плохо"}])
        data = report.to_dict()
        self.assertEqual(data["errors"], 1)
        self.assertEqual(data["warnings"], 0)
        self.assertEqual(len(data["sections"]), 2)
        self.assertNotIn("markdown", data)
        self.assertIn("markdown", report.to_dict(with_markdown=True))

    def test_section_to_dict(self):
        case = self.make_case()
        report = self.make_report(case.id)
        data = report.sections[0].to_dict()
        self.assertEqual(data["section_id"], "summary")
        self.assertEqual(data["ord"], 0)
        self.assertFalse(data["edited"])

    def test_audit_entry_to_dict(self):
        user = self.make_user("admin", role="admin")
        self.repos.audit.log("login", user=user, object_type="user", object_id=user.id)
        data = self.repos.audit.list()[0].to_dict()
        self.assertEqual(data["login"], "admin")
        self.assertEqual(data["action"], "login")
        self.assertNotIn("id", data)

    def test_rows_to_builds_models(self):
        self.make_user("engineer")
        rows = self.db.query("SELECT * FROM users")
        users = rows_to(User, rows)
        self.assertEqual(len(users), 1)
        self.assertIsInstance(users[0], User)
        self.assertEqual(rows_to(User, []), [])

    def test_model_types_after_reading(self):
        case = self.make_case()
        report = self.make_report(case.id)
        document = self.make_document("literature/kniga")
        self.assertIsInstance(self.repos.cases.get(case.id), Case)
        self.assertIsInstance(self.repos.reports.get(report.id), Report)
        self.assertIsInstance(self.repos.reports.sections(report.id)[0], ReportSection)
        self.assertIsInstance(self.repos.documents.by_doc_id(document.doc_id), Document)
        self.repos.audit.log("проверка")
        self.assertIsInstance(self.repos.audit.list()[0], AuditEntry)



class DocumentReindexInvalidationTests(unittest.TestCase):
    """Смена содержимого файла обесценивает отметку об индексации."""

    def setUp(self):
        self.repos = Repositories(Database(":memory:"))

    def test_same_sha_keeps_index_marks(self):
        document = self.repos.documents.upsert("d1", "literature", "Книга", "/p", "sha-a")
        self.repos.documents.mark_indexed("d1", 7)
        again = self.repos.documents.upsert("d1", "literature", "Книга", "/p", "sha-a")
        self.assertEqual(again.chunk_count, 7)
        self.assertIsNotNone(again.indexed_at)
        self.assertEqual(document.doc_id, again.doc_id)

    def test_new_sha_clears_index_marks(self):
        self.repos.documents.upsert("d1", "literature", "Книга", "/p", "sha-a")
        self.repos.documents.mark_indexed("d1", 7)
        changed = self.repos.documents.upsert("d1", "literature", "Книга", "/p", "sha-b")
        self.assertIsNone(changed.indexed_at)
        self.assertEqual(changed.chunk_count, 0)

    def test_package_reexports_public_api(self):
        import reportgen.store as store

        for name in ("Database", "Repositories", "User", "Case", "Report",
                     "normalized_edit_distance"):
            self.assertTrue(hasattr(store, name), name)


class ConcurrencyTests(unittest.TestCase):
    """Одновременная работа нескольких инженеров.

    Регрессия: раньше все запросы шли через одно общее соединение, поэтому
    транзакции разных потоков перемешивались — чужой commit закрывал чужую
    транзакцию, и часть записей терялась вместе с ошибками
    «cannot start a transaction within a transaction».
    """

    def _run(self, database, threads=6, iterations=25):
        import threading

        repos = Repositories(database)
        user = repos.users.create("u", "пароль123", "U", "engineer")
        errors: list[str] = []

        def worker(number: int) -> None:
            try:
                for index in range(iterations):
                    name = f"C-{number}-{index}"
                    repos.cases.create(
                        name, "signal_issue",
                        {"case_id": name, "report_type": "signal_issue"},
                        user_id=user.id,
                    )
                    chat = repos.chats.create(user.id, title=f"чат {number}-{index}")
                    repos.chats.add_message(chat.id, "user", "вопрос")
                    repos.audit.log("test", user=user, object_id=name)
            except Exception as error:  # noqa: BLE001 — собираем всё, что случилось
                errors.append(f"{type(error).__name__}: {error}")

        workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
        for item in workers:
            item.start()
        for item in workers:
            item.join()
        return repos, errors, threads * iterations

    def test_parallel_writes_to_file_database(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            database = Database(str(Path(tmp) / "concurrent.db"))
            repos, errors, expected = self._run(database)
            self.assertEqual(errors, [])
            self.assertEqual(repos.cases.count(), expected)
            self.assertEqual(repos.chats.stats()["messages"], expected)
            database.close()

    def test_parallel_writes_to_memory_database(self):
        database = Database(":memory:")
        repos, errors, expected = self._run(database, threads=4, iterations=15)
        self.assertEqual(errors, [])
        self.assertEqual(repos.cases.count(), expected)

    def test_each_thread_gets_its_own_connection(self):
        import tempfile
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            database = Database(str(Path(tmp) / "threads.db"))
            seen: list[int] = []

            def grab() -> None:
                seen.append(id(database.connection))

            workers = [threading.Thread(target=grab) for _ in range(4)]
            for item in workers:
                item.start()
            for item in workers:
                item.join()
            self.assertEqual(len(set(seen)), 4)
            database.close()

    def test_rollback_does_not_leak_to_other_writes(self):
        database = Database(":memory:")
        repos = Repositories(database)
        repos.users.create("first", "пароль123", "", "engineer")
        with self.assertRaises(sqlite3.IntegrityError):
            repos.users.create("first", "пароль123", "", "engineer")
        repos.users.create("second", "пароль123", "", "engineer")
        self.assertEqual(repos.users.count(), 2)

if __name__ == "__main__":
    unittest.main()


class ConcurrentAccessTests(unittest.TestCase):
    """Загрузка библиотеки и открытый интерфейс не мешают друг другу.

    Инженер запускал load-library.ps1, не закрыв браузер, и получал
    «database is locked» — причём на записи в журнал действий, к которому
    ошибка отношения не имела.
    """

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "base.db")

    def tearDown(self):
        self._tmp.cleanup()

    WRITING = ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "REPLACE")

    def executed(self, database) -> list:
        """Записывает SQL, который база выполнила. Трассировка самого SQLite."""
        seen: list[str] = []
        database.connection.set_trace_callback(seen.append)
        return seen

    def writes(self, statements) -> list:
        out = []
        for text in statements:
            head = text.strip().split(None, 1)
            if head and head[0].upper() in self.WRITING:
                out.append(" ".join(text.split())[:70])
        return out

    def test_opening_a_ready_database_writes_nothing(self):
        """Главная причина: migrate() писала при каждом открытии соединения.

        executescript со схемой, UPDATE по всем документам ради переименования
        направлений и INSERT версии схемы выполнялись всегда — а соединение
        открывает каждый процесс: веб-сервер, приём, любая команда CLI.
        Стоило запустить загрузку библиотеки, не закрыв интерфейс, — и веб
        падал с «database is locked».
        """
        Database(self.path).migrate()

        opened = Database(self.path)
        statements = self.executed(opened)
        opened.migrate()
        opened.connection.set_trace_callback(None)
        self.assertEqual([], self.writes(statements),
                         "открытие готовой базы всё ещё пишет")

    def test_second_process_can_read_while_the_first_writes(self):
        import threading
        import time

        Database(self.path).migrate()
        errors = []

        def hold():
            writer = Database(self.path)
            with writer.transaction() as connection:
                connection.execute(
                    "INSERT INTO documents(doc_id, doc_type, title, source_path, sha256,"
                    " confidentiality, meta_json, created_at)"
                    " VALUES('x','standards','Т','/x','s','internal','{}','2026-01-01')")
                time.sleep(1.5)

        def read():
            time.sleep(0.3)
            try:
                Repositories(Database(self.path)).documents.list()
            except Exception as error:  # noqa: BLE001 — нам важен сам факт
                errors.append(f"{type(error).__name__}: {error}")

        writer = threading.Thread(target=hold)
        reader = threading.Thread(target=read)
        writer.start()
        reader.start()
        reader.join()
        writer.join()
        self.assertEqual([], errors)

    def test_audit_failure_does_not_break_the_action(self):
        # Журнал — вещь служебная: потерять строку «отчёт сохранён» не так
        # страшно, как отказать инженеру в сохранении отчёта.
        database = Database(self.path)
        database.migrate()
        repos = Repositories(database)
        user = repos.users.create("ivanov", "parol12345", "Иванов И.И.", "engineer")

        with mock.patch.object(type(database), "transaction",
                               side_effect=sqlite3.OperationalError("database is locked")):
            repos.audit.log("report.save", user=user, object_type="report", object_id="1")

    def test_domain_rename_does_not_lock_when_nothing_to_rename(self):
        # UPDATE по всей таблице берёт блокировку записи, даже меняя ноль строк.
        database = Database(self.path)
        database.migrate()
        statements = self.executed(database)
        database._rename_domains()
        database.connection.set_trace_callback(None)
        self.assertEqual([], self.writes(statements))
