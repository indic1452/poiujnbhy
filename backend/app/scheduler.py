"""Фоновый планировщик опроса источников (APScheduler)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .db import get_sessionmaker
from .pipeline.ingest import run_ingest

log = logging.getLogger("scheduler")
_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    try:
        stats = await run_ingest(get_sessionmaker())
        log.info("Плановый ingest: %s", stats.as_dict())
    except Exception:  # noqa: BLE001
        log.exception("Ошибка планового ingest")


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _job,
        "interval",
        seconds=settings.poll_interval_seconds,
        id="ingest",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info("Планировщик запущен: интервал %s c", settings.poll_interval_seconds)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
