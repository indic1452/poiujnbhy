-- Схема хранилища. Одна база SQLite на установку: кейсы, отчёты, библиотека,
-- датасет правок и журнал действий. Внешних сервисов не требуется.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------- люди ----

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    login         TEXT    NOT NULL UNIQUE,
    full_name     TEXT    NOT NULL DEFAULT '',
    -- Штатная должность: owner | head | deputy | lead | senior | engineer.
    -- Права администратора — до начальника группы включительно.
    role          TEXT    NOT NULL DEFAULT 'engineer',
    department    TEXT    NOT NULL DEFAULT '',   -- отдел
    team          TEXT    NOT NULL DEFAULT '',   -- группа внутри отдела
    password_hash TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

-- Отсутствия: дежурство, отпуск, больничный, командировка. Отдельная таблица,
-- а не пара колонок в users: у одного человека бывает несколько периодов,
-- и нужна история — по ней дашборд показывает движение за период.
CREATE TABLE IF NOT EXISTS absences (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,            -- duty | vacation | sick | trip | study
    date_from  TEXT    NOT NULL,            -- ГГГГ-ММ-ДД включительно
    date_to    TEXT    NOT NULL,            -- ГГГГ-ММ-ДД включительно
    note       TEXT    NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_absences_user ON absences(user_id, date_from);
CREATE INDEX IF NOT EXISTS idx_absences_range ON absences(date_from, date_to);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- ----------------------------------------------------------- библиотека ---

CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    doc_id          TEXT    NOT NULL UNIQUE,   -- относительный путь без расширения
    doc_type        TEXT    NOT NULL,          -- literature|standards|datasheets|reports|regulations
    title           TEXT    NOT NULL,
    source_path     TEXT    NOT NULL,
    sha256          TEXT    NOT NULL,
    confidentiality TEXT    NOT NULL DEFAULT 'internal',  -- public|internal|nda
    meta_json       TEXT    NOT NULL DEFAULT '{}',
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    size            INTEGER,                     -- размер файла на момент приёма
    mtime_ns        INTEGER,                     -- время правки файла на момент приёма
    indexed_at      TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_sha  ON documents(sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    chunk_uid   TEXT    NOT NULL UNIQUE,       -- doc_id#0007
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord         INTEGER NOT NULL,
    doc_type    TEXT    NOT NULL,
    title_path  TEXT    NOT NULL DEFAULT '[]', -- JSON-список крошек
    text        TEXT    NOT NULL,
    meta_json   TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_type     ON chunks(doc_type);

-- Полнотекстовый индекс по нормализованному (стеммированному) тексту:
-- unicode61 не знает русской морфологии, поэтому стемминг делаем сами
-- в reportgen.retrieval.tokenize и кладём сюда уже готовые токены.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    stemmed,
    chunk_uid UNINDEXED,
    doc_type  UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_uid TEXT PRIMARY KEY,
    model     TEXT    NOT NULL,
    dim       INTEGER NOT NULL,
    vector    BLOB    NOT NULL     -- float32, little-endian, L2-нормированный
);

-- --------------------------------------------------------------- письма ---

-- Таблица называется cases по историческим причинам: так же названы API и
-- код. В интерфейсе это «письма» — обращение заказчика и подготовленный на
-- него ответ. Переименовывать таблицу на работающей установке дороже, чем
-- один раз объяснить это здесь.
CREATE TABLE IF NOT EXISTS cases (
    id            INTEGER PRIMARY KEY,
    case_id       TEXT    NOT NULL UNIQUE,
    report_type   TEXT    NOT NULL,
    title         TEXT    NOT NULL DEFAULT '',
    customer      TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'new',  -- new|draft|review|approved|archived
    incoming_no   TEXT    NOT NULL DEFAULT '',     -- входящий номер письма
    incoming_date TEXT    NOT NULL DEFAULT '',     -- дата письма, ГГГГ-ММ-ДД
    deadline      TEXT    NOT NULL DEFAULT '',     -- срок ответа, ГГГГ-ММ-ДД
    priority      TEXT    NOT NULL DEFAULT 'normal',  -- normal | high | urgent
    assignee_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note          TEXT    NOT NULL DEFAULT '',
    facts_json    TEXT    NOT NULL,
    facts_digest  TEXT    NOT NULL DEFAULT '',
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_status   ON cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_assignee ON cases(assignee_id);
CREATE INDEX IF NOT EXISTS idx_cases_deadline ON cases(deadline);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY,
    case_ref    INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'draft',  -- draft|review|rework|approved
    markdown    TEXT    NOT NULL,
    review_note TEXT    NOT NULL DEFAULT '',       -- замечание проверяющего
    source      TEXT    NOT NULL DEFAULT 'generated', -- generated|uploaded
    file_name   TEXT    NOT NULL DEFAULT '',       -- имя загруженного файла
    file_path   TEXT    NOT NULL DEFAULT '',       -- где он лежит
    file_size   INTEGER NOT NULL DEFAULT 0,
    meta_json   TEXT    NOT NULL DEFAULT '{}',
    issues_json TEXT    NOT NULL DEFAULT '[]',
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL,
    approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    approved_at TEXT,
    UNIQUE(case_ref, version)
);

CREATE TABLE IF NOT EXISTS report_sections (
    id                 INTEGER PRIMARY KEY,
    report_id          INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    section_id         TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    ord                INTEGER NOT NULL,
    draft_text         TEXT    NOT NULL,   -- исходная генерация модели
    text               TEXT    NOT NULL,   -- текущий текст после правок
    sources_json       TEXT    NOT NULL DEFAULT '[]',
    missing_facts_json TEXT    NOT NULL DEFAULT '[]',
    regenerated        INTEGER NOT NULL DEFAULT 0,
    edited             INTEGER NOT NULL DEFAULT 0,
    updated_at         TEXT    NOT NULL,
    UNIQUE(report_id, section_id)
);

-- ------------------------------------------------- датасет для обучения ---

CREATE TABLE IF NOT EXISTS edit_pairs (
    id            INTEGER PRIMARY KEY,
    case_id       TEXT    NOT NULL,
    report_id     INTEGER,
    report_type   TEXT    NOT NULL,
    section_id    TEXT    NOT NULL,
    section_title TEXT    NOT NULL,
    draft         TEXT    NOT NULL,
    final         TEXT    NOT NULL,
    facts_digest  TEXT    NOT NULL DEFAULT '',
    context_json  TEXT    NOT NULL DEFAULT '{}',  -- факты и источники секции
    edit_distance REAL    NOT NULL DEFAULT 0,
    created_by    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edit_pairs_case ON edit_pairs(case_id);

-- ----------------------------------------------------------- помощник ----

-- Личные разговоры с помощником. Читать чужие чаты не может никто, включая
-- администратора: в вопросах инженеров всплывают данные заказчиков.
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL DEFAULT 'Новый разговор',
    domain     TEXT    NOT NULL DEFAULT '',   -- ограничение поиска по направлению
    case_ref   INTEGER REFERENCES cases(id) ON DELETE SET NULL,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY,
    chat_id      INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role         TEXT    NOT NULL,            -- user | assistant
    content      TEXT    NOT NULL,
    sources_json TEXT    NOT NULL DEFAULT '[]',
    meta_json    TEXT    NOT NULL DEFAULT '{}',
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, id);

-- Вложения к вопросу: дамп, лог, снимок экрана, документ. Текст извлекается
-- при загрузке тем же конвертером, что и библиотека, и хранится здесь: файл
-- на диске может быть удалён, а разбор в разговоре должен остаться.
CREATE TABLE IF NOT EXISTS chat_attachments (
    id         INTEGER PRIMARY KEY,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES chat_messages(id) ON DELETE SET NULL,
    name       TEXT    NOT NULL,
    kind       TEXT    NOT NULL DEFAULT 'document',  -- dump | image | document
    size       INTEGER NOT NULL DEFAULT 0,
    text       TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_attachments ON chat_attachments(chat_id, id);

-- --------------------------------------------------------------- журнал ---

CREATE TABLE IF NOT EXISTS audit (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,
    user_id      INTEGER,
    login        TEXT NOT NULL DEFAULT '',
    action       TEXT NOT NULL,
    object_type  TEXT NOT NULL DEFAULT '',
    object_id    TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
