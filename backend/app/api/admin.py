"""Служебные эндпоинты: POST /api/refresh, GET /api/status."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import state
from ..config import settings
from ..db import get_session, get_sessionmaker
from ..models import Cluster, Item, Source
from ..pipeline.ingest import run_ingest
from ..schemas import RefreshResponse, StatusResponse
from ..summarizer import get_summarizer
from ..vision import get_vision

router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/refresh", response_model=RefreshResponse)
async def refresh() -> RefreshResponse:
    if state.ingest_lock.locked():
        return RefreshResponse(started=False, detail="Обновление уже выполняется")

    async def _run() -> None:
        await run_ingest(get_sessionmaker())

    asyncio.create_task(_run())
    return RefreshResponse(started=True, detail="Обновление запущено")


async def _health(provider) -> bool:
    try:
        ok = await provider.health()
    except Exception:  # noqa: BLE001
        ok = False
    finally:
        await provider.aclose()
    return ok


@router.get("/status", response_model=StatusResponse)
async def status(session: AsyncSession = Depends(get_session)) -> StatusResponse:
    summ_ok = await _health(get_summarizer())
    vis_ok = await _health(get_vision())

    clusters = int((await session.scalar(select(func.count(Cluster.id)))) or 0)
    items = int((await session.scalar(select(func.count(Item.id)))) or 0)
    sources = int((await session.scalar(select(func.count(Source.id)))) or 0)

    return StatusResponse(
        summarizer_backend=settings.summarizer_backend,
        summarizer_model=settings.model,
        summarizer_available=summ_ok,
        vision_backend=settings.vision_backend,
        vision_model=settings.vision_model,
        vision_available=vis_ok,
        source_mode=settings.source_mode,
        last_ingest=state.last_ingest,
        clusters=clusters,
        items=items,
        sources=sources,
    )
