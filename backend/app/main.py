"""Точка входа FastAPI: маршруты, статика /media, планировщик, старт."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import admin, events, export, feed, sources, stats
from .config import settings
from .db import create_all, dispose_engine, get_sessionmaker
from .pipeline.ingest import run_ingest
from .scheduler import start_scheduler, stop_scheduler
from .sources.loader import seed_sources
from .sources.media import ensure_media_dir

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_media_dir()
    if settings.auto_create_tables:
        await create_all()
    try:
        async with get_sessionmaker()() as session:
            added = await seed_sources(session)
            if added:
                log.info("Добавлено источников из sources.yaml: %s", added)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось засеять источники (выполните миграции alembic)")

    start_scheduler()
    if settings.ingest_on_start:
        asyncio.create_task(run_ingest(get_sessionmaker()))

    yield

    stop_scheduler()
    await dispose_engine()


app = FastAPI(title="Военные сводки — агрегатор", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feed.router)
app.include_router(sources.router)
app.include_router(admin.router)
app.include_router(events.router)
app.include_router(stats.router)
app.include_router(export.router)


@app.get("/api/meta")
async def meta() -> dict:
    from .categories import CATEGORIES, EVENT_COLORS, EVENT_TYPES

    return {
        "categories": CATEGORIES,
        "event_types": EVENT_TYPES,
        "event_colors": EVENT_COLORS,
        "tile_url": None,  # фронтенд берёт из VITE_TILE_URL
        "tile_attribution": settings.map_tile_attribution,
    }

ensure_media_dir()
app.mount("/media", StaticFiles(directory=str(settings.media_path)), name="media")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}
