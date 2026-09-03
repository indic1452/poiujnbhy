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
    -- Подразделение, в котором человек стоит ПО ШТАТУ. Работают все в одном
    -- отделе, это и есть система; поле нужно только тем, кто числится в
    -- другом подразделении. Пусто — стоит там же, где работает.
    department    TEXT    NOT NULL DEFAULT '',   -- по штату
    team          TEXT    NOT NULL DEFAULT '',   -- группа внутри отдела
    -- Как человека найти. Заполняет он сам в личном кабинете: справочник,
    -- который ведёт кадровик, устаревает быстрее, чем его правят.
    phone         TEXT    NOT NULL DEFAULT '',   -- телефон
    ext_no        TEXT    NOT NULL DEFAULT '',   -- внутренний номер
    room          TEXT    NOT NULL DEFAULT '',   -- кабинет
    email         TEXT    NOT NULL DEFAULT '',   -- почта
    -- Заявка одобрена. Человек заводит себя сам, но войти сможет только
    -- после того, как его признает создатель, начальник отдела, заместитель
    -- или начальник группы: система отдела не проходной двор.
    approved      INTEGER NOT NULL DEFAULT 1,
    approved_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    approved_at   TEXT    NOT NULL DEFAULT '',
    password_hash TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

-- Расход личного состава: чем занят человек в эти дни — дежурство, работы,
-- командировка, отпуск, больничный, учёба, отгул. Отдельная таблица, а не
-- пара колонок в users: у одного человека бывает несколько периодов, и нужна
-- история — по ней дашборд показывает движение за период, а расход строит
-- сетку по дням. Таблица называется absences по историческим причинам: в
-- интерфейсе это «расход».
CREATE TABLE IF NOT EXISTS absences (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,            -- duty|work|trip|study|vacation|sick|dayoff
    date_from  TEXT    NOT NULL,            -- ГГГГ-ММ-ДД включительно
    date_to    TEXT    NOT NULL,            -- ГГГГ-ММ-ДД включительно
    place      TEXT    NOT NULL DEFAULT '', -- где: узел, аппаратная, объект
    note       TEXT    NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_absences_user ON absences(user_id, date_from);
CREATE INDEX IF NOT EXISTS idx_absences_range ON absences(date_from, date_to);

-- День, отмеченный на весь отдел: общие работы, занятия, собрание, нерабочий
-- день. Это не отсутствие: отсутствие про человека («Жуков в командировке»),
-- а такой день про сам день («в четверг весь отдел на учениях»). Держать их
-- в одной таблице нельзя — счёт «сколько людей в строю» сразу перестал бы
-- сходиться, и пришлось бы заводить строку на каждого из двадцати.
CREATE TABLE IF NOT EXISTS department_days (
    id         INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL,            -- work|study|meeting|holiday
    date_from  TEXT    NOT NULL,            -- ГГГГ-ММ-ДД включительно
    date_to    TEXT    NOT NULL,            -- ГГГГ-ММ-ДД включительно
    title      TEXT    NOT NULL DEFAULT '', -- что именно: «Парко-хозяйственный день»
    note       TEXT    NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_department_days ON department_days(date_from, date_to);

-- Личные документы сотрудника: справка-объективка и всё, что к ней. Файл
-- лежит на диске, строка хранит имя, размер и путь. Своё грузит и смотрит
-- каждый; чужое — начальник отдела, заместитель и создатель системы: это
-- личные сведения, и открывать их всему отделу нельзя.
CREATE TABLE IF NOT EXISTS person_files (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL DEFAULT 'profile',  -- profile | other
    name        TEXT    NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    path        TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_person_files ON person_files(user_id, id);

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

-- Словарь полнотекстового указателя: слово и в скольких фрагментах оно
-- встречается. Нужен, чтобы отличить слово, которое что-то значит, от слова
-- вроде «связь» или «линия», стоящего в каждом втором фрагменте библиотеки
-- радиотехнического отдела. Своих данных не хранит — это взгляд на
-- chunks_fts, и получается такой ответ одним обращением к указателю.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vocab USING fts5vocab(chunks_fts, 'row');

CREATE TABLE IF NOT EXISTS embeddings (
    chunk_uid TEXT PRIMARY KEY,
    model     TEXT    NOT NULL,
    dim       INTEGER NOT NULL,
    vector    BLOB    NOT NULL     -- float32, little-endian, L2-нормированный
);

-- Состояние смыслового поиска спрашивают с экрана «Библиотека» раз в две
-- секунды, пока идёт построение. Без индекса «сколько векторов нашей
-- моделью» — это полный проход по полумиллиону строк; с ним счёт идёт по
-- одному индексу, не касаясь самих векторов.
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

-- Вектор живёт ровно столько, сколько живёт его фрагмент. Все места, где
-- фрагменты удаляются, чистят векторы сами, но забыть об этом в новом месте
-- слишком легко, а осиротевший вектор врёт молча: он попадает в счёт, и
-- «не хватает векторов» превращается в ноль при непостроенной библиотеке.
-- Здесь это правило записано в самой базе и обойти его нельзя.
CREATE TRIGGER IF NOT EXISTS trg_chunks_drop_embedding
AFTER DELETE ON chunks
BEGIN
    DELETE FROM embeddings WHERE chunk_uid = OLD.chunk_uid;
END;

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
    status        TEXT    NOT NULL DEFAULT 'new',  -- new|draft|review|checked|approved|archived
    line_type     TEXT    NOT NULL DEFAULT '',     -- sls | rrls | kv | other
    tc_no         TEXT    NOT NULL DEFAULT '',     -- номер технического средства
    tc_date       TEXT    NOT NULL DEFAULT '',     -- дата ТС, ГГГГ-ММ-ДД
    order_no      TEXT    NOT NULL DEFAULT '',     -- номер указаний
    order_date    TEXT    NOT NULL DEFAULT '',     -- дата указаний, ГГГГ-ММ-ДД
    registrations INTEGER NOT NULL DEFAULT 0,      -- количество регистраций
    outgoing_note TEXT    NOT NULL DEFAULT '',     -- примечание при отправке
    incoming_no   TEXT    NOT NULL DEFAULT '',     -- входящий номер письма
    incoming_date TEXT    NOT NULL DEFAULT '',     -- дата письма, ГГГГ-ММ-ДД
    outgoing_no   TEXT    NOT NULL DEFAULT '',     -- исходящий номер ответа
    outgoing_date TEXT    NOT NULL DEFAULT '',     -- дата отправки, ГГГГ-ММ-ДД
    sent_by       INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- кто отправил
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
CREATE INDEX IF NOT EXISTS idx_cases_outgoing ON cases(outgoing_no);
CREATE INDEX IF NOT EXISTS idx_cases_assignee ON cases(assignee_id);
CREATE INDEX IF NOT EXISTS idx_cases_deadline ON cases(deadline);
CREATE INDEX IF NOT EXISTS idx_cases_line     ON cases(line_type);

-- Файлы, приложенные к письму: само письмо сканом, схема линии, журнал
-- измерений. Хранится и путь на диске, и разобранный текст: файл на диске
-- можно потерять, а искать письмо по словам из приложения нужно всегда.
-- Отдельно от reports: там сданный на проверку отчёт, тут исходные бумаги.
CREATE TABLE IF NOT EXISTS case_files (
    id          INTEGER PRIMARY KEY,
    case_ref    INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    -- К чему бумага относится: incoming — пришла с письмом, outgoing — ушла
    -- с ответом. В журнале отдела это две разные стопки, и смешивать их
    -- нельзя: по одной отвечают, вторую отправляют.
    stage       TEXT    NOT NULL DEFAULT 'incoming',
    name        TEXT    NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    path        TEXT    NOT NULL,
    text        TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT '',
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_files ON case_files(case_ref, id);

-- Поиск по письмам и отчётам. Тот же приём, что и для библиотеки
-- (chunks_fts): unicode61 русской морфологии не знает, поэтому стемминг
-- делает reportgen.retrieval.tokenize, а сюда кладутся готовые токены.
-- В строку письма попадают его реквизиты и текст всех редакций отчёта:
-- искать в отделе нужно и «по входящему 0423», и «по паре слов из вывода».
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    stemmed,
    case_ref UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

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

-- ------------------------------------------------------- уведомления ---

-- Что человеку нужно знать: начальник вернул отчёт, письмо назначили на
-- вас, вас вызывают в кабинет. Хранится у получателя, а не рассылается:
-- изолированная машина без почты и телефона, и единственное надёжное место
-- для «вам сообщение» — та же база.
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,               -- см. NOTICE_KINDS
    title      TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL DEFAULT '',
    link       TEXT    NOT NULL DEFAULT '',    -- куда вести по щелчку
    from_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    seen       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications ON notifications(user_id, seen, id);

-- ---------------------------------------------------------- переписка ---

-- Беседа: между двумя людьми или на несколько человек. Отдельно от чата с
-- помощником (chats): там разговор с моделью, тут — между людьми.
CREATE TABLE IF NOT EXISTS talks (
    id         INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL DEFAULT '',    -- пусто — беседа двоих
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS talk_members (
    talk_id   INTEGER NOT NULL REFERENCES talks(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seen_id   INTEGER NOT NULL DEFAULT 0,      -- до какого сообщения прочитано
    PRIMARY KEY (talk_id, user_id)
);

CREATE TABLE IF NOT EXISTS talk_messages (
    id         INTEGER PRIMARY KEY,
    talk_id    INTEGER NOT NULL REFERENCES talks(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_talk_messages ON talk_messages(talk_id, id);

-- Файлы, приложенные к сообщению: снимок экрана, выгрузка, схема. Половина
-- вопросов по письму решается тем, что человек показывает картинку, — а
-- пересылать её отделу было нечем: почты в изолированном контуре нет.
CREATE TABLE IF NOT EXISTS talk_files (
    id          INTEGER PRIMARY KEY,
    talk_id     INTEGER NOT NULL REFERENCES talks(id) ON DELETE CASCADE,
    message_id  INTEGER REFERENCES talk_messages(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name        TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    -- Текст, вычитанный при загрузке: Word и Excel браузер не рисует, а
    -- прочитать документ собеседник должен, не скачивая его.
    text        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_talk_files ON talk_files(talk_id, id);

-- Примечания к письму: обсуждение прямо на деле. Начальник пишет, что
-- поправить, исполнитель отвечает — и всё это остаётся при письме, а не
-- теряется в разговорах.
CREATE TABLE IF NOT EXISTS case_notes (
    id         INTEGER PRIMARY KEY,
    case_ref   INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    text       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_notes ON case_notes(case_ref, id);

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
