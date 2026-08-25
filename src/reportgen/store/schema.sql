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
    role          TEXT    NOT NULL DEFAULT 'engineer',  -- viewer | engineer | admin
    password_hash TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

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

-- ---------------------------------------------------------------- кейсы ---

CREATE TABLE IF NOT EXISTS cases (
    id           INTEGER PRIMARY KEY,
    case_id      TEXT    NOT NULL UNIQUE,
    report_type  TEXT    NOT NULL,
    title        TEXT    NOT NULL DEFAULT '',
    customer     TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'new',  -- new|draft|review|approved|archived
    facts_json   TEXT    NOT NULL,
    facts_digest TEXT    NOT NULL DEFAULT '',
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY,
    case_ref    INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'draft',  -- draft|verified|approved
    markdown    TEXT    NOT NULL,
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
