"""Подключение к SQLite и применение схемы."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = "14"

# Колонки, добавленные после первого выпуска. Схема применяется идемпотентно
# (CREATE TABLE IF NOT EXISTS), но существующая таблица от этого не меняется,
# поэтому новые поля добавляются здесь — так база обновляется вместе с кодом,
# без ручных ALTER на стороне отдела.
COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("documents", "domain", "TEXT NOT NULL DEFAULT ''"),
    ("chunks", "domain", "TEXT NOT NULL DEFAULT ''"),
    # Актуальность документа: заменённый стандарт не должен цитироваться
    # как действующий — это прямая ошибка в отчёте.
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
    # Отдел и группа сотрудника: без них сводка не может показать ни
    # списочный состав отдела, ни нагрузку по группам.
    ("users", "department", "TEXT NOT NULL DEFAULT ''"),
    ("users", "team", "TEXT NOT NULL DEFAULT ''"),
    # Входящее письмо: кто исполнитель, к какому числу, входящий номер.
    # Без исполнителя и срока нельзя ни распределить работу, ни увидеть
    # просрочку — а это первое, что спрашивают с отдела.
    ("cases", "assignee_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("cases", "deadline", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "incoming_no", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "incoming_date", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "priority", "TEXT NOT NULL DEFAULT 'normal'"),
    ("cases", "note", "TEXT NOT NULL DEFAULT ''"),
    # Проверка отчёта начальником: замечание при возврате на исправление,
    # откуда взялся отчёт (собран системой или загружен файлом) и сам файл.
    ("reports", "review_note", "TEXT NOT NULL DEFAULT ''"),
    ("reports", "source", "TEXT NOT NULL DEFAULT 'generated'"),
    ("reports", "file_name", "TEXT NOT NULL DEFAULT ''"),
    ("reports", "file_path", "TEXT NOT NULL DEFAULT ''"),
    ("reports", "file_size", "INTEGER NOT NULL DEFAULT 0"),
    # Исходящий номер ответа. Отчёт проверен начальником — это ещё не конец:
    # инженер отправляет ответ и записывает исходящий номер. Без него в
    # учёте отдела нет главного: под каким номером ушёл ответ на письмо.
    ("cases", "outgoing_no", "TEXT NOT NULL DEFAULT ''"),
    # Линия связи и номер технического средства. Отдел работает по линиям —
    # спутниковым, радиорелейным, коротковолновым, — и без этих двух полей
    # письмо нельзя ни найти, ни отнести к своему хозяйству.
    # Как найти человека. Заполняет он сам в кабинете — справочник кадровика
    # устаревает быстрее, чем его правят.
    #
    # Телефоны отдела названы по-своему: мобильный, открытый и режимный. Одно
    # поле «Телефон» на все три не годилось — по номеру не понять, можно ли
    # по нему говорить о работе, а это в отделе первый вопрос. Старые
    # колонки phone/ext_no/email оставлены на месте: в них лежат номера,
    # набранные людьми, и стирать их ради переименования нельзя.
    ("users", "phone", "TEXT NOT NULL DEFAULT ''"),
    ("users", "ext_no", "TEXT NOT NULL DEFAULT ''"),
    ("users", "room", "TEXT NOT NULL DEFAULT ''"),
    ("users", "email", "TEXT NOT NULL DEFAULT ''"),
    # Когда человека последний раз видели в системе. Нужна не «слежка», а
    # ответ на обычный вопрос отдела: писать ему сейчас или он ушёл и
    # прочтёт завтра. Обновляется не чаще раза в минуту — иначе на каждый
    # щелчок в интерфейсе приходилась бы запись в базу.
    ("users", "last_seen_at", "TEXT NOT NULL DEFAULT ''"),
    ("users", "phone_mobile", "TEXT NOT NULL DEFAULT ''"),
    ("users", "phone_open", "TEXT NOT NULL DEFAULT ''"),
    ("users", "phone_secure", "TEXT NOT NULL DEFAULT ''"),
    # Одобрение заявки. Значение по умолчанию 1: все, кто уже заведён,
    # остаются одобренными — иначе обновление системы заперло бы отдел.
    ("users", "approved", "INTEGER NOT NULL DEFAULT 1"),
    ("users", "approved_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("users", "approved_at", "TEXT NOT NULL DEFAULT ''"),
    # Где человек в эти дни. Без места расход отвечает «дежурство», но не
    # отвечает «где», а начальнику нужно именно второе.
    ("absences", "place", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "line_type", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "tc_no", "TEXT NOT NULL DEFAULT ''"),
    # Указания, по которым письмо отрабатывают, дата средства и число
    # регистраций. Всё это в журнале отдела заполняют на входящем, и без
    # них письмо приходится держать в голове или на бумаге рядом.
    ("cases", "tc_date", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "order_no", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "order_date", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "registrations", "INTEGER NOT NULL DEFAULT 0"),
    # Что написали при отправке ответа. Отдельно от примечания к письму:
    # одно — про входящее, другое — про то, чем ответили.
    ("cases", "outgoing_note", "TEXT NOT NULL DEFAULT ''"),
    # Бумага пришла с письмом или ушла с ответом.
    ("case_files", "stage", "TEXT NOT NULL DEFAULT 'incoming'"),
    ("cases", "outgoing_date", "TEXT NOT NULL DEFAULT ''"),
    ("cases", "sent_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    # Текст приложенного к беседе документа: Word браузер не рисует, а
    # прочитать его собеседник должен, не скачивая файл.
    ("talk_files", "text", "TEXT NOT NULL DEFAULT ''"),
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
            self._restore_group_numbers()
            self._move_phones()
            self._drop_orphan_vectors()
            self.connection.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (SCHEMA_VERSION,),
            )
            self.connection.commit()

    def _move_phones(self) -> None:
        """Старые номера — в новые поля, по одному разу.

        «Телефон» люди заполняли мобильным (в отделе спрашивают именно его),
        «Внутренний» — это и есть номер по открытой АТС. Переносим так и
        только в пустое: если человек уже вписал номер в новое поле, чужая
        догадка не должна его затирать. Старые колонки не трогаем — если
        догадка неверна, номера на месте и их видно в базе.
        """
        try:
            names = {item["name"] for item in
                     self.connection.execute("PRAGMA table_info(users)")}
        except sqlite3.Error:
            return
        if not {"phone", "ext_no", "phone_mobile", "phone_open"} <= names:
            return
        self.connection.execute(
            "UPDATE users SET phone_mobile = phone "
            "WHERE phone_mobile = '' AND phone <> ''")
        self.connection.execute(
            "UPDATE users SET phone_open = ext_no "
            "WHERE phone_open = '' AND ext_no <> ''")

    def _drop_orphan_vectors(self) -> None:
        """Векторы фрагментов, которых уже нет, — по одному разу на базу.

        Правило «вектор живёт со своим фрагментом» теперь стоит триггером в
        самой схеме, но до него база успела пожить, и сироты в ней могли
        остаться. Они не безобидны: состояние смыслового поиска считает
        векторы, и лишние делают «не хватает» нулём при непостроенной
        библиотеке — человек видит «всё готово» и не понимает, почему поиск
        ничего не находит.

        Проход тяжёлый — полмиллиона строк, — поэтому делаем его один раз и
        запоминаем в meta. Дальше сирот не будет: их не даст завести триггер.
        """
        try:
            done = self.connection.execute(
                "SELECT 1 FROM meta WHERE key = 'orphan_vectors_dropped_at'"
            ).fetchone()
        except sqlite3.Error:
            return
        if done is not None:
            return
        try:
            self.connection.execute(
                "DELETE FROM embeddings WHERE chunk_uid NOT IN "
                "(SELECT chunk_uid FROM chunks)")
        except sqlite3.Error:
            return                # таблиц ещё нет — база новая, сирот неоткуда взять
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES('orphan_vectors_dropped_at', ?) "
            "ON CONFLICT(key) DO NOTHING",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
        )

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
        # Таблиц целиком в COLUMN_MIGRATIONS нет: там только колонки. Наличие
        # виртуальных и новых таблиц проверяем отдельно —
        # иначе указатель поиска мог не доехать на базе, где версия схемы
        # уже поднята, а таблицы ещё нет, и поиск молча ничего не находил.
        try:
            present = {
                row["name"] for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        except sqlite3.Error:
            return False
        return {"chunks_fts", "cases_fts", "case_files", "person_files",
                "notifications", "talks", "talk_members",
                "talk_messages", "talk_files", "case_notes"}.issubset(present)

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

    def _restore_group_numbers(self) -> None:
        """Вернуть на место названия, стёртые прежней проверкой номера.

        Одно время поле «номер группы» проверялось как число, и обновление
        стирало из него всё, что числом не было, дописывая стёртое в
        примечание к письму. Теперь в этом поле разрешён любой текст —
        значит, стирать было не за что, и написанное надо вернуть.

        Отметку прежнего обновления оставляем: по ней видно, что письмо
        через это прошло, а примечание чистим от служебной строки, чтобы
        она не мозолила глаза в карточке.
        """
        from ..facts import MAX_GROUP  # noqa: PLC0415 — только ради предела длины

        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(cases)")}
        if not columns or "customer" not in columns or "note" not in columns:
            return
        marker = self.connection.execute(
            "SELECT 1 FROM meta WHERE key = 'senders_migrated_at'"
        ).fetchone()
        if marker is None:
            return

        prefix = "Отправитель до обновления: "
        rows = self.connection.execute(
            "SELECT id, customer, note, facts_json FROM cases WHERE note LIKE ?",
            (f"%{prefix}%",),
        ).fetchall()
        for row in rows:
            kept, rest = "", []
            for line in (row["note"] or "").splitlines():
                if line.startswith(prefix) and not kept:
                    kept = line[len(prefix):].strip()
                else:
                    rest.append(line)
            if not kept or (row["customer"] or "").strip():
                continue
            # Подрезаем до предела поля: вернуть можно только то, что поле
            # принимает, иначе письмо откроется, а отчёт по нему не собрать.
            if len(kept) > MAX_GROUP:
                kept = kept[:MAX_GROUP].rstrip()
            facts_text = row["facts_json"] or ""
            try:
                facts = json.loads(facts_text) if facts_text else None
            except (json.JSONDecodeError, TypeError):
                facts = None
            if isinstance(facts, dict):
                facts["group_no"] = kept
                facts.pop("customer", None)
                facts_text = json.dumps(facts, ensure_ascii=False)
            self.connection.execute(
                "UPDATE cases SET customer = ?, note = ?, facts_json = ? WHERE id = ?",
                (kept, "\n".join(rest).strip(), facts_text, row["id"]),
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

    def stream(self, sql: str, params: Sequence[Any] = (),
               chunk: int = 2000) -> Iterator[list[sqlite3.Row]]:
        """Читает выборку кусками, не поднимая её в память целиком.

        ``query`` делает ``fetchall``: на таблице векторов отдела это два с
        лишним гигабайта BLOB-ов разом. Обходить это через LIMIT/OFFSET —
        значит заставлять SQLite на каждой странице пропускать всё, что
        перед ней: на полумиллионе строк последняя страница отматывает
        полмиллиона. Один курсор с ``fetchmany`` читает ту же выборку одним
        проходом и держит в памяти только текущий кусок.

        Замок общей базы в памяти держится на всё чтение: курсор живёт
        дольше одного вызова, и отпускать его на середине нельзя.
        """
        with self._read_guard():
            cursor = self.connection.execute(sql, params)
            try:
                while True:
                    rows = cursor.fetchmany(max(1, int(chunk)))
                    if not rows:
                        return
                    yield rows
            finally:
                cursor.close()

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

    def release(self) -> None:
        """Закрыть соединение ТЕКУЩЕГО потока и забыть его.

        Список соединений сам не чистится: он держит ссылку, и соединение
        умершего потока живёт до конца работы приложения. Пулу потоков это
        безразлично — он их переиспользует, а вот фоновое построение
        векторов заводит новый поток на каждую книгу. За полгода работы
        отдела это сотни навсегда открытых файлов (при WAL — ещё и -wal,
        -shm к каждому). Общая база в памяти соединения не имеет — там
        закрывать нечего.
        """
        if self._shared is not None:
            return
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return
        with self._lock:
            try:
                self._connections.remove(connection)
            except ValueError:
                pass
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._local.connection = None

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
        # Всё, что копится от работы отдела. Проверка здоровья отвечает на
        # вопрос «что в базе есть», и таблица, которой в этом списке нет,
        # для неё как будто не существует.
        tables = ("users", "documents", "chunks", "cases", "case_files",
                  "case_notes", "reports", "edit_pairs",
                  "chats", "chat_messages", "chat_attachments",
                  "absences", "person_files", "notifications",
                  "talks", "talk_messages", "audit")
        return {name: int(self.scalar(f"SELECT count(*) FROM {name}") or 0) for name in tables}
