"""Сборка FastAPI-приложения."""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import Settings
from ..store.db import Database
from ..store.repo import Repositories
from .api import router
from .assistant import AssistantService
from .auth import LoginThrottle, ensure_local_user
from .service import ReportService, ServiceError

STATIC_DIR = Path(__file__).parent / "static"
logger = logging.getLogger("reportgen.web")

#: Методы, меняющие состояние: их тело нельзя читать до проверки прав.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Пути, доступные без входа в систему. Заявка на доступ — среди них: её
#: подаёт человек, у которого доступа ещё нет. Ничего, кроме записи в очередь
#: на одобрение, она не создаёт.
OPEN_PATHS = frozenset({"/api/auth/login", "/api/auth/logout", "/api/auth/register"})
#: Потолок для JSON-тел: факт-пакет — это килобайты, а не сотни мегабайт.
MAX_JSON_BYTES = 8 * 1024 * 1024

PLACEHOLDER = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>reportgen</title></head>
<body style="font-family: system-ui; margin: 4rem auto; max-width: 40rem">
<h1>Интерфейс не установлен</h1>
<p>Файлы интерфейса отсутствуют в каталоге <code>src/reportgen/web/static</code>.
API при этом работает — проверьте <a href="/api/health">/api/health</a>.</p>
</body></html>"""


def create_app(settings: Settings | None = None,
               repos: Repositories | None = None,
               service: ReportService | None = None,
               assistant: AssistantService | None = None) -> FastAPI:
    """Создать приложение. Все зависимости можно подменить — это нужно тестам."""
    settings = settings or Settings.load()
    settings.ensure_dirs()

    if repos is None:
        repos = Repositories(Database(settings.db_path))
    if service is None:
        service = ReportService(repos=repos, settings=settings)
    if assistant is None:
        assistant = AssistantService(reports=service)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        repos.sessions.purge_expired()
        # Поиск по тексту отчётов появился позже писем: на базе отдела
        # указателя ещё нет. Строим его сам при первом запуске новой
        # версии, чтобы никого не просить «переиндексируйте».
        if repos.cases.count() and repos.case_search.is_empty():
            built = repos.case_search.rebuild_all()
            logger.info("Указатель поиска по письмам построен: %d", built)
        if settings.auth_enabled and repos.users.count() == 0:
            logger.warning(
                "В системе нет ни одного сотрудника. Заведите создателя системы: "
                "reportgen useradd --login admin --role owner"
            )
        # Достраиваем векторы, недостроенные в прошлый раз. Приложение могли
        # перезапустить посреди работы — тогда половина библиотеки ищется
        # только словами, и узнать об этом можно было, лишь открыв
        # «Библиотеку». Ничего не строится, если всё на месте или смысловой
        # поиск выключен.
        if service.vectors is not None:
            state = service.vectors.start_if_needed()
            if state.get("running"):
                logger.info("Достраиваем векторы: %s", state.get("hint", ""))
        yield
        # Выключение: досчитать начатую пачку даём, новых проходов не
        # начинаем. Брошенная на полуслове запись хуже пяти секунд ожидания.
        if service.vectors is not None:
            service.vectors.stop()

    app = FastAPI(
        lifespan=lifespan,
        title="Генератор технических отчётов",
        description="Локальная система подготовки, учёта и проверки технических отчётов отдела",
        version="0.1.0",
        # Штатные страницы FastAPI тянут swagger-ui и redoc с cdn.jsdelivr.net,
        # а иконку — с сайта проекта. В изолированном контуре это пустая белая
        # страница без единой ошибки в логах. Своя страница ниже рисует то же
        # самое из локального openapi.json.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.repos = repos
    app.state.service = service
    app.state.assistant = assistant
    app.state.throttle = LoginThrottle()
    # В локальном режиме работаем от имени настоящей записи в базе: на users(id)
    # ссылаются кейсы, отчёты и журнал.
    app.state.local_user = None if settings.auth_enabled else ensure_local_user(repos)

    _install_middleware(app)
    _install_handlers(app)
    app.include_router(router)
    _install_static(app)

    return app



#: Описание API без единого внешнего запроса: swagger-ui и redoc тянут скрипты
#: с CDN, которого в изолированном контуре нет.
API_DOCS_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>API — генератор технических отчётов</title>
<style>
 body { font: 15px/1.5 system-ui, "Segoe UI", sans-serif; margin: 0; padding: 2rem;
        background: #f6f7f9; color: #1c1f23; }
 h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
 p.sub { color: #5b6470; margin: 0 0 1.5rem; }
 .op { background: #fff; border: 1px solid #dfe3e8; border-radius: 6px;
       padding: .7rem .9rem; margin-bottom: .5rem; }
 .m { display: inline-block; min-width: 4.5rem; font-weight: 600; font-size: .8rem;
      text-transform: uppercase; }
 .get { color: #1a7f37; } .post { color: #9a3412; } .put { color: #1e40af; }
 .delete { color: #b91c1c; } .patch { color: #6b21a8; }
 code { font-family: Consolas, "SF Mono", monospace; }
 .sum { color: #5b6470; margin-left: .5rem; }
 a { color: #1e40af; }
</style></head><body>
<h1>API генератора технических отчётов</h1>
<p class="sub">Схема: <a href="/api/openapi.json">/api/openapi.json</a>.
Страница собрана локально — в изолированном контуре внешние скрипты недоступны.</p>
<div id="list">Загрузка…</div>
<script>
fetch('/api/openapi.json').then(r => r.json()).then(schema => {
  const order = ['get', 'post', 'put', 'patch', 'delete'];
  const rows = [];
  for (const [path, item] of Object.entries(schema.paths || {})) {
    for (const method of order) {
      if (!item[method]) continue;
      rows.push({ path, method, summary: item[method].summary || '' });
    }
  }
  rows.sort((a, b) => a.path.localeCompare(b.path));
  const list = document.getElementById('list');
  list.textContent = '';
  for (const row of rows) {
    const box = document.createElement('div');
    box.className = 'op';
    const method = document.createElement('span');
    method.className = 'm ' + row.method;
    method.textContent = row.method;
    const path = document.createElement('code');
    path.textContent = row.path;
    box.append(method, path);
    if (row.summary) {
      const sum = document.createElement('span');
      sum.className = 'sum';
      sum.textContent = row.summary;
      box.append(sum);
    }
    list.append(box);
  }
  if (!rows.length) { list.textContent = 'В схеме нет ни одного маршрута.'; }
}).catch(error => {
  document.getElementById('list').textContent = 'Не удалось прочитать схему: ' + error;
});
</script></body></html>
"""

def _install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def parse_json_and_secure(request: Request, call_next: Any):
        request.state.json_body = None
        settings = app.state.settings
        path = request.url.path
        content_type = request.headers.get("content-type", "")

        # Права проверяются ДО чтения тела. Иначе неаутентифицированный клиент
        # заставляет сервер принять и сложить на диск сотни мегабайт, прежде чем
        # получит 401: FastAPI разбирает форму раньше, чем вызывает обработчик,
        # поэтому никакая зависимость этот порядок не меняет — только middleware.
        if (request.method in WRITE_METHODS and path.startswith("/api/")
                and path not in OPEN_PATHS and settings.auth_enabled):
            token = request.cookies.get("rg_session")
            user = app.state.repos.sessions.resolve(token) if token else None
            if user is None:
                return JSONResponse(
                    status_code=401, content={"error": "требуется вход в систему"}
                )

        # Заявленный объём тоже отсекаем заранее, не читая тело.
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            size = int(declared)
            limit = (MAX_JSON_BYTES if "application/json" in content_type
                     else settings.max_upload_mb * 1024 * 1024)
            if size > limit:
                return JSONResponse(
                    status_code=413,
                    content={"error": f"тело запроса больше допустимых "
                                      f"{limit // (1024 * 1024)} МБ"},
                )

        if request.method in ("POST", "PUT", "PATCH") and "application/json" in content_type:
            raw = await _read_capped(request, MAX_JSON_BYTES)
            if raw is None:
                return JSONResponse(
                    status_code=413,
                    content={"error": f"тело JSON больше допустимых "
                                      f"{MAX_JSON_BYTES // (1024 * 1024)} МБ"},
                )
            if raw:
                try:
                    # utf-8-sig, а не utf-8: файл, сохранённый Блокнотом,
                    # приезжает с BOM, и обычный utf-8 на нём падает.
                    parsed = json.loads(raw.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return JSONResponse(
                        status_code=400, content={"error": "тело запроса не является корректным JSON"}
                    )
                if not isinstance(parsed, dict):
                    return JSONResponse(
                        status_code=400, content={"error": "ожидался объект JSON"}
                    )
                request.state.json_body = parsed
            else:
                request.state.json_body = {}

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


async def _read_capped(request: Request, limit: int) -> bytes | None:
    """Читает тело запроса, обрывая приём при превышении предела.

    Нужно именно потоковое чтение: у запроса с chunked-передачей нет
    Content-Length, и проверить объём заранее невозможно.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error_handler(request: Request, error: ServiceError) -> JSONResponse:
        if error.status >= 500:
            logger.error("Ошибка сервиса: %s", error, exc_info=True)
        return JSONResponse(status_code=error.status, content={"error": str(error)})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, error: Exception) -> JSONResponse:
        logger.exception("Необработанная ошибка при %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": f"внутренняя ошибка сервера: {type(error).__name__}: {error}"},
        )


def _install_static(app: FastAPI) -> None:
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> Any:
        page = STATIC_DIR / "index.html"
        return FileResponse(page) if page.is_file() else HTMLResponse(PLACEHOLDER)

    @app.get("/login", include_in_schema=False)
    def login_page() -> Any:
        page = STATIC_DIR / "login.html"
        if page.is_file():
            return FileResponse(page)
        return FileResponse(STATIC_DIR / "index.html") if (STATIC_DIR / "index.html").is_file() \
            else HTMLResponse(PLACEHOLDER)

    @app.get("/api/docs", include_in_schema=False)
    def api_docs() -> Any:
        return HTMLResponse(API_DOCS_PAGE)

    @app.get("/brand/logo", include_in_schema=False)
    def brand_logo() -> Any:
        logo = app.state.settings.brand_logo
        if logo and Path(logo).is_file():
            return FileResponse(logo)
        return JSONResponse({"error": "логотип не задан"}, status_code=404)

    @app.get("/brand/login-image", include_in_schema=False)
    def brand_login_image() -> Any:
        """Фон окна входа: свой файл, а если его нет — кадр из поставки.

        Отдаём кадр по умолчанию, а не 404: страница входа тогда просто
        показывает картинку, без проверок в браузере и без красной строки
        в консоли на каждой загрузке.
        """
        image = app.state.settings.brand_login_image
        if image and Path(image).is_file():
            return FileResponse(image)
        return FileResponse(STATIC_DIR / "login-bg.jpg")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Any:
        icon = STATIC_DIR / "favicon.ico"
        return FileResponse(icon) if icon.is_file() else JSONResponse({}, status_code=204)


def run(settings: Settings | None = None) -> None:  # pragma: no cover — точка входа
    """Запуск сервера через uvicorn."""
    try:
        import uvicorn
    except ImportError:
        print("не установлен uvicorn: pip install -r requirements.txt", file=sys.stderr)
        raise SystemExit(2) from None

    settings = settings or Settings.load()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
