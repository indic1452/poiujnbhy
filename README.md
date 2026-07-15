# Военные сводки — новостной агрегатор на русском

Веб‑приложение, которое **парсит основные источники** (российские, зарубежные и
Telegram‑каналы), **дедуплицирует и обобщает** сообщения об одном событии в единую
**русскую сводку**, сопровождает каждый сюжет **сопутствующим медиа (фото/видео)** и
**анализирует изображения локальной vision‑моделью**. Тематический фокус: военные
события, Украина, Россия, западная коалиция.

Всё обобщение, перевод и анализ картинок выполняются **локальными моделями ИИ**
(Ollama, семейство Qwen) — **без облачных API**.

## Возможности

- 📰 **Источники:** RSS (РИА, ТАСС, РБК, Коммерсантъ, Интерфакс, Lenta, Gazeta, Meduza,
  BBC, Al Jazeera, Guardian, DW, France24, Politico, Kyiv Independent, Укр. Правда, NV,
  УНИАН), Reuters/AP через Google News, **Telegram‑каналы** (`t.me/s/`, без ключей).
- 🌐 **Единая лента на русском:** зарубежные материалы переводятся, всё сводится к
  русскому языку.
- 🧩 **Обобщение сюжетов:** похожие материалы из разных источников группируются
  (rapidfuzz) в «сюжет» с одной консолидированной сводкой.
- 🎯 **Фильтр тематики:** релевантность по ключевым словам (RU+EN) + категории
  (фронт, дипломатия, санкции, поставки вооружений, коалиция/НАТО, внутренняя политика РФ).
- 🖼️ **Медиа события:** извлечение фото и **видео** (особенно из Telegram), скачивание
  постеров/тумбнейлов, показ в карточке; каждый сюжет сопровождается медиа.
- 👁️ **Vision‑анализ:** локальная модель (`qwen2.5vl`) описывает изображение/кадр на
  русском, делает OCR карт и инфографики.
- ⚙️ **Локальные модели:** Ollama (`qwen3:8b` для текста, `qwen2.5vl:7b` для зрения),
  за интерфейсом провайдера с graceful‑деградацией на mock.
- 🗺️ **Карта событий:** геолокация из текста (топонимы/объекты → координаты) и
  **ассистируемо** из фото/видео; маркеры‑фото с кластеризацией, клик → карточка события.
- 📊 **Статистика и выгрузка:** классификация типов событий (удары/обстрелы/ПВО/…),
  сводка по районам и по дням, экспорт **GeoJSON/CSV/JSON** за период.

## Архитектура

```
Источники → Fetcher → извлечение медиа → фильтр релевантности → дедуп/кластеризация
   → [текст: перевод+обобщение] + [медиа: постер + vision-анализ]  → PostgreSQL → REST API → React UI
```

- **Backend:** Python 3.11, FastAPI, async SQLAlchemy + asyncpg (PostgreSQL), Alembic,
  APScheduler, httpx, feedparser, trafilatura, selectolax, rapidfuzz, Pillow.
- **Frontend:** Vite + React + TypeScript (интерфейс на русском, тёмная тема).
- **ИИ:** Ollama (локально).

## Карта событий, геолокация и аналитика

Интерфейс имеет три вида: **Лента**, **Карта**, **Статистика**.

- **Геолокация из текста.** Локальная LLM в том же вызове суммаризации извлекает `event_type`
  (тип события) и `locations` (место/область/объект/роль «цель удара»). Названия
  разрешаются в координаты **офлайн‑геокодером**: курируемый список военных объектов
  (`backend/data/war_objects.csv` — НПЗ/аэродромы/АЭС/подстанции) + газеттир населённых
  пунктов (`gazetteer_sample.csv`), с fuzzy‑матчингом словоформ (rapidfuzz).
- **Полный газеттир (опц.).** Для покрытия всех населённых пунктов RU/UA/BY соберите
  GeoNames (CC BY 4.0):
  ```bash
  cd backend && python scripts/build_gazetteer.py   # нужен доступ в сеть
  ```
  Результат `backend/data/gazetteer_full.csv` подхватывается автоматически.
- **Геолокация по медиа — ассистируемая.** Vision‑модель извлекает подсказки (OCR вывесок/
  знаков, ориентиры). Такие точки помечаются `needs_review` и требуют подтверждения — это
  не заявляется как полностью автоматическая геолокация.
- **API:** `GET /api/events.geojson` (FeatureCollection с фильтрами `since/until/event_type/
  region/needs_review`), `GET /api/stats` (по типам/районам/дням), `GET /api/export?format=
  geojson|csv|json`.
- **Тайлы карты:** по умолчанию OpenStreetMap (для личного/dev использования). Для продакшена
  задайте `VITE_TILE_URL` (CARTO/MapTiler/Stadia — с ключом) или разверните офлайн‑тайлы.
  Карта работает и без тайлов (маркеры на сером фоне).

> Слои **линии фронта (ЛБС)** и **воинских подразделений** в текущей версии не включены
> (отложены). Данные геолокации: © GeoNames (CC BY 4.0), © OpenStreetMap.

## Telegram‑каналы (основной источник)

Два режима (выбор автоматический по наличию ключей, переопределяется `TELEGRAM_METHOD`
или полем `method` у канала — `auto|telethon|web`):

- **Web‑скрейпинг `t.me/s/<канал>`** (по умолчанию, без ключей) — текст, фото и видео‑постер
  со ссылкой, многостраничная подкачка новых постов. Только публичные каналы с веб‑превью;
  сам видеофайл Telegram без авторизации не отдаёт.
- **Telethon (рекомендуется)** — полноценный доступ: **целые фото/видео**, история, каналы без
  превью, альбомы. Настройка:
  1. Получите `api_id`/`api_hash` на https://my.telegram.org/apps.
  2. В `.env`: `TELEGRAM_API_ID=...`, `TELEGRAM_API_HASH=...` (для видеофайлов —
     `MEDIA_DOWNLOAD=all`).
  3. Разовый вход (создаёт `.session`):
     ```bash
     cd backend && python -m app.telegram_login   # телефон → код → (2FA)
     ```
  Дальше опросы идут неинтерактивно. Обрабатываются `FloodWait`; лимит постов за опрос —
  `TELEGRAM_MAX_MESSAGES`.

**Список каналов** — в `backend/sources.yaml` (тип `telegram`, `username`, `lang`, `method`).
Идёт стартовый набор RU/UK/EN (официальные, медиа, OSINT — разные стороны). **Проверьте
актуальность username и курируйте список**: часть каналов — государственные/пропагандистские
или OSINT, оценка достоверности на стороне пользователя. Посты каналов проходят тот же фильтр
по теме, перевод и **геолокацию** — то есть попадают в ленту, на карту и в статистику.

## Быстрый старт (Docker Compose)

Поднимает PostgreSQL + Ollama + backend + frontend:

```bash
docker compose up -d --build
# загрузить модели (один раз):
docker compose exec ollama ollama pull qwen3:8b
docker compose exec ollama ollama pull qwen2.5vl:7b
```

Откройте **http://localhost:8080**. Первый опрос источников запустится автоматически
(`INGEST_ON_START=true`), далее — по расписанию каждые 10 минут, либо кнопкой «⟳ Обновить».

> Для CPU‑режима замените модель на `qwen2.5:7b` (переменная `MODEL`). Для GPU
> раскомментируйте секцию `deploy.resources` у сервиса `ollama`.

## Ручной запуск (для разработки)

### 1. PostgreSQL
Запустите Postgres и создайте БД, либо: `docker compose up -d postgres`.

### 2. Ollama
```bash
# установите Ollama (https://ollama.com), затем:
ollama pull qwen3:8b
ollama pull qwen2.5vl:7b
```

### 3. Backend
```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # отредактируйте DATABASE_URL и модели
alembic upgrade head          # создать схему
uvicorn app.main:app --reload # http://localhost:8000
```

### 4. Frontend
```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173 (проксирует /api и /media на :8000)
```

## Оффлайн‑демо без сети и без модели (fixtures + mock)

Позволяет увидеть ленту с русскими сводками и медиа, не имея доступа к источникам и
без запущенной модели — данные берутся из `backend/tests/fixtures`:

```bash
cd backend && . .venv/bin/activate
export SOURCE_MODE=fixtures SUMMARIZER_BACKEND=mock VISION_BACKEND=mock
export AUTO_CREATE_TABLES=true INGEST_ON_START=true
export DATABASE_URL=postgresql+asyncpg://<user>@localhost:5432/newsdb
uvicorn app.main:app
```

Затем запустите фронтенд (`npm run dev`) и откройте лоту, либо дёрните API:
`curl 'http://localhost:8000/api/feed' | jq`.

## Конфигурация (`.env`)

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DATABASE_URL` | строка PostgreSQL (asyncpg) | `postgresql+asyncpg://newsuser:newspass@localhost:5432/newsdb` |
| `SOURCE_MODE` | `live` или `fixtures` | `live` |
| `SUMMARIZER_BACKEND` | `ollama` / `mock` / `nllb` | `ollama` |
| `MODEL` | тег текстовой модели Ollama | `qwen3:8b` |
| `VISION_BACKEND` | `ollama` / `mock` | `ollama` |
| `VISION_MODEL` | тег vision‑модели | `qwen2.5vl:7b` |
| `OLLAMA_URL` | адрес Ollama | `http://localhost:11434` |
| `MEDIA_DOWNLOAD` | `image` / `all` (с видеофайлами) / `off` | `image` |
| `POLL_INTERVAL_SECONDS` | интервал опроса | `600` |
| `INGEST_ON_START` | опрос при старте | `false` |
| `CLUSTER_SIMILARITY` | порог схожести (0–100) | `82` |
| `TELEGRAM_API_ID/HASH` | опц. Telethon (видео/история) | — |

## Источники (`backend/sources.yaml`)

Список лент и Telegram‑каналов правится в `sources.yaml`. Добавьте свои публичные
Telegram‑каналы:

```yaml
- name: Мой канал
  type: telegram
  username: some_public_channel
  lang: ru
```

## Рекомендованные локальные модели

| Ресурсы | Текст (`MODEL`) | Зрение (`VISION_MODEL`) |
|---|---|---|
| CPU / ~8 ГБ ОЗУ | `qwen2.5:7b` | `qwen2.5vl:3b` |
| GPU ~16–24 ГБ | `qwen3:8b`/`14b` | `qwen2.5vl:7b` |
| GPU 24 ГБ+ | `qwen3:32b` / `gemma3:27b` | `qwen2.5vl:32b` |

## Тесты

```bash
cd backend && . .venv/bin/activate
pytest            # поднимает временный локальный PostgreSQL, работает оффлайн (mock)
```

## Ограничения и заметки

- **Доступ к источникам.** Российские госленты часто гео‑блокируют не‑RU IP —
  при хостинге вне РФ настройте RU/нейтральный egress или прокси. Все запросы идут с
  браузерным `User-Agent`; при `403/451` — повтор с backoff.
- **Reuters/AP** не имеют официального RSS — берутся через Google News RSS‑поиск.
- **Telegram.** По умолчанию — скрейпинг публичного веб‑превью `t.me/s/` без ключей;
  для приватных/без превью каналов и надёжной выгрузки видео используйте Telethon
  (`TELEGRAM_API_ID/HASH`). Соблюдайте лимиты и ToS Telegram.
- **Селекторы Telegram** могут меняться — они изолированы в
  `app/sources/telegram_web.py`.
- Приложение не хранит и не публикует чужой контент публично — это персональный
  агрегатор открытых источников.
