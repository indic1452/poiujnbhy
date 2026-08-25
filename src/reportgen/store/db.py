"""Подключение к SQLite и применение схемы."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = "3"

# Колонки, добавленные после первого выпуска. Схема применяется идемпотентно
# (CREATE TABLE IF NOT EXISTS), но существующая таблица от этого не меняется,
# поэтому новые поля добавляются здесь — так база обновляется вместе с кодом,
# без ручных ALTER на стороне заказчика.
COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("documents", "domain", "TEXT NOT NULL DEFAULT ''"),
    ("chunks", "domain", "TEXT NOT NULL DEFAULT ''"),
    # Актуальность документа: заменённый стандарт не должен цитироваться
    # как действующий — это прямая ошибка в отчёте заказчику.
    ("documents", "status", "TEXT NOT NULL DEFAULT 'current'"),
    ("documents", "superseded_by", "TEXT NOT NULL DEFAULT ''"),
    ("chunks", "status", "TEXT NOT NULL DEFAULT 'current'"),
    # Год издания: при прочих равных цитировать надо свежую редакцию, а не ту,
    # что случайно оказалась выше в выдаче.
    ("documents", "year", "INTEGER"),
    ("chunks", "year", "INTEGER"),
    # Размер и время правки файла. Приём сверяет их до вычисления SHA-256:
    # добавить пять файлов к десяти тысячам не должно означать перечитывание
    # всей библиотеки с диска. stat() дешевле хеша примерно в сотню раз.
    ("documents", "size", "INTEGER"),
    ("documents", "mtime_ns", "INTEGER"),
)

#: Прежний идентификатор направления → нынешний. Пополняется при правке
#: templates/domains.json, чтобы уже принятые документы не осиротели.
DOMAIN_RENAMES: tuple[tuple[str, str], ...] = (
    ("modulation", "signal"),
    ("measurement", "method"),
    ("equipment", "hardware"),
    ("regulation", "standard"),
)


def utcnow() -> str:
    """Единый формат меток времени во всей системе."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Тонкая обёртка над sqlite3: схема, транзакции, удобные выборки."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        # Веб-сервер обрабатывает запросы в пуле потоков. Одно общее соединение
        # на всех означало бы, что транзакции разных запросов перемешиваются:
        # чужой commit закрывает вашу транзакцию на середине. Поэтому у каждого
        # потока своё соединение, а записи дополнительно сериализуются замком —
        # так SQLite не отдаёт SQLITE_BUSY на параллельных вставках.
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._shared: sqlite3.Connection | None = None

        if self.path == ":memory:":
            # База в памяти существует ровно в одном соединении, разделить её
            # между потоками нельзя — значит, сериализуем вообще всё.
            self._shared = self._connect()
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    # -- соединения ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        self._connections.append(connection)
        return connection

    @property
    def connection(self) -> sqlite3.Connection:
        """Соединение текущего потока (для базы в памяти — единственное общее)."""
        if self._shared is not None:
            return self._shared
        connection = getattr(self._local, "connection", None)
        if connection is None:
            with self._lock:
                connection = self._connect()
            self._local.connection = connection
        return connection

    @contextmanager
    def _read_guard(self):
        """Чтения из общей базы в памяти тоже нужно сериализовать."""
        if self._shared is None:
            yield
        else:
            with self._lock:
                yield

    # -- схема --------------------------------------------------------------

    def migrate(self) -> None:
        self.connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self._ensure_columns()
        self._rename_domains()
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION,),
        )
        self.connection.commit()

    def _rename_domains(self) -> None:
        """Переименование направлений при смене справочника.

        Направления правятся под работу конкретной компании, и часть прежних
        идентификаторов при этом исчезает. Без переноса уже проиндексированные
        документы остались бы с направлением, которого больше нет: в списке
        библиотеки — пусто, в поиске с фильтром — не находятся, и понять
        причину нельзя. Переиндексация всей библиотеки ради этого не нужна.
        """
        for old_id, new_id in DOMAIN_RENAMES:
            for table in ("documents", "chunks"):
                columns = {
                    row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
                }
                if "domain" not in columns:
                    continue
                self.connection.execute(
                    f"UPDATE {table} SET domain = ? WHERE domain = ?", (new_id, old_id)
                )

    def _ensure_columns(self) -> None:
        """Добавляет недостающие колонки в уже существующие таблицы."""
        for table, column, declaration in COLUMN_MIGRATIONS:
            existing = {
                row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                self.connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                )

    # -- выполнение ---------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.execute(sql, params)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self.connection.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._read_guard():
            return self.connection.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._read_guard():
            return self.connection.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Атомарный блок: при исключении изменения откатываются целиком.

        Замок держится на всё время блока: при одновременной работе нескольких
        инженеров записи выстраиваются в очередь вместо того, чтобы портить
        транзакции друг друга.
        """
        with self._lock:
            connection = self.connection
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def commit(self) -> None:
        with self._lock:
            self.connection.commit()

    def close(self) -> None:
        with self._lock:
            for connection in self._connections:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            self._connections.clear()
            self._shared = None
            self._local = threading.local()

    # -- сервис -------------------------------------------------------------

    def vacuum(self) -> None:
        with self._lock:
            self.connection.execute("VACUUM")

    def counts(self) -> dict[str, int]:
        tables = ("users", "documents", "chunks", "cases", "reports", "edit_pairs", "audit")
        return {name: int(self.scalar(f"SELECT count(*) FROM {name}") or 0) for name in tables}
