"""Подключение к SQLite и применение схемы."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = "4"

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
    # Отдел и группа сотрудника: без них дашборд не может показать ни
    # списочный состав отдела, ни нагрузку по группам.
    ("users", "department", "TEXT NOT NULL DEFAULT ''"),
    ("users", "team", "TEXT NOT NULL DEFAULT ''"),
    # Письмо заказчика: кто исполнитель, к какому числу, входящий номер.
    # Без исполнителя и срока нельзя ни распределить работу, ни увидеть
    # просрочку — а это первое, что спрашивают с отдела.
    ("cases", "assignee_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("cases", "deadline", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "incoming_no", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "incoming_date", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "priority", "TEXT NOT NULL DEFAULT 'normal'"),
    ("cases", "note", "TEXT NOT NULL DEFAULT ''"),
)

#: Прежнее значение роли → нынешнее. Роли viewer/engineer/admin заменены
#: штатными должностями компании. Без переноса сотрудник с ролью, которой
#: больше нет, теряет права полностью: в интерфейсе пусто, в API — 403.
ROLE_RENAMES: tuple[tuple[str, str], ...] = (
    ("viewer", "engineer"),
    ("admin", "head"),
)

#: Прежний идентификатор направления → нынешний. Пополняется при правке
#: templates/domains.json, чтобы уже принятые документы не осиротели.
DOMAIN_RENAMES: tuple[tuple[str, str], ...] = (
    ("modulation", "signal"),
    ("measurement", "method"),
    ("equipment", "hardware"),
    ("regulation", "standard"),
)


def _lower(value: Any) -> Any:
    """Регистронезависимость для кириллицы.

    Встроенный lower() в SQLite умеет только латиницу: «Спектр» и «спектр»
    для него разные слова, и поиск письма по названию не находил ничего,
    пока не угадаешь регистр. Питоновский lower() знает Юникод целиком.
    """
    return value.lower() if isinstance(value, str) else value


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
        connection.create_function("rulower", 1, _lower, deterministic=True)
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
        """Привести схему к нужному виду. На готовой базе НИЧЕГО не пишет.

        Раньше писала всегда: executescript со схемой, UPDATE по всем
        документам ради переименования направлений и INSERT версии схемы — на
        каждом открытии соединения. А соединение открывает каждый процесс:
        веб-сервер, приём библиотеки, любая команда CLI.

        Стоило запустить загрузку библиотеки, не закрыв интерфейс, — и веб при
        первом же обращении падал с «database is locked», потому что приём
        держал запись. Ошибка вылезала на записи в журнал действий, хотя к
        журналу отношения не имела.

        Теперь сначала проверяем, надо ли что-то менять, и пишем только если
        надо. На готовой базе всё сводится к одному чтению.
        """
        if self._schema_is_current():
            return
        with self._lock:
            # Колонки добавляются дважды, и оба раза по делу.
            #
            # ДО схемы — ради уже работающей базы. В schema.sql есть индексы
            # по новым полям письма (idx_cases_assignee, idx_cases_deadline),
            # а CREATE TABLE стоит с IF NOT EXISTS: существующая таблица не
            # пересоздаётся, колонки в ней ещё нет, и CREATE INDEX падает с
            # «no such column». База после этого не открывается вообще — ни
            # интерфейсом, ни командной строкой.
            #
            # ПОСЛЕ схемы — ради новой базы. Часть колонок (documents.domain,
            # documents.status и другие поздние добавления) в schema.sql не
            # описана и живёт только в COLUMN_MIGRATIONS: на пустой базе
            # первый проход пропускает их, потому что таблиц ещё нет.
            #
            # Оба прохода идемпотентны: колонка на месте — ничего не делаем.
            self._ensure_columns()
            self.connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._ensure_columns()
            self._rename_domains()
            self._rename_roles()
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SCHEMA_VERSION,),
            )
            self.connection.commit()

    def _schema_is_current(self) -> bool:
        """Готова ли база к работе без единой записи."""
        try:
            row = self.connection.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.Error:
            return False          # таблицы meta ещё нет — база новая
        if row is None or str(row["value"]) != str(SCHEMA_VERSION):
            return False
        # Версия совпала, но колонки могли не доехать (база правилась руками).
        for table, column, _ in COLUMN_MIGRATIONS:
            try:
                names = {
                    item["name"]
                    for item in self.connection.execute(f"PRAGMA table_info({table})")
                }
            except sqlite3.Error:
                return False
            if names and column not in names:
                return False
        return True

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
                # Сначала смотрим, есть ли кого переименовывать: UPDATE по всей
                # таблице берёт блокировку записи, даже когда меняет ноль строк.
                stale = self.connection.execute(
                    f"SELECT 1 FROM {table} WHERE domain = ? LIMIT 1", (old_id,)
                ).fetchone()
                if stale is None:
                    continue
                self.connection.execute(
                    f"UPDATE {table} SET domain = ? WHERE domain = ?", (new_id, old_id)
                )

    def _rename_roles(self) -> None:
        """Перевод сотрудников на штатные должности.

        Первый по счёту администратор — тот, кто разворачивал систему, — и
        есть её создатель: он получает полные права. Остальные становятся
        начальниками отдела, тоже с правами администратора. Прав никто не
        теряет: разбираться, почему после обновления не открывается раздел
        сотрудников, придётся на изолированной машине без разработчика.
        """
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(users)")}
        if "role" not in columns:
            return
        # Запись «local» — служебная, под ней нельзя войти: делать её
        # создателем системы значит оставить систему без живого владельца.
        first_admin = self.connection.execute(
            "SELECT id FROM users WHERE role = 'admin' AND login <> 'local' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if first_admin is not None:
            self.connection.execute(
                "UPDATE users SET role = 'owner' WHERE id = ?", (first_admin["id"],)
            )
        moved = 0
        for old_role, new_role in ROLE_RENAMES:
            stale = self.connection.execute(
                "SELECT 1 FROM users WHERE role = ? LIMIT 1", (old_role,)
            ).fetchone()
            if stale is None:
                continue
            cursor = self.connection.execute(
                "UPDATE users SET role = ? WHERE role = ?", (new_role, old_role)
            )
            moved += cursor.rowcount or 0
        if moved or first_admin is not None:
            # Отметка в базе: через год иначе не объяснить, почему бывшие
            # администраторы стали начальниками отдела.
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES('roles_migrated_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (utcnow(),),
            )

    def _ensure_columns(self) -> None:
        """Добавляет недостающие колонки в уже существующие таблицы.

        Таблицы, которых ещё нет, пропускаем молча: их создаст schema.sql
        сразу с нужными колонками. Это и есть случай новой базы.
        """
        for table, column, declaration in COLUMN_MIGRATIONS:
            existing = {
                row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue
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
