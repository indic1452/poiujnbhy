"""Управление источниками: GET /api/sources, POST /api/sources/{id}."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Source
from ..schemas import SourceOut, SourceUpdate

router = APIRouter(prefix="/api", tags=["sources"])


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(session: AsyncSession = Depends(get_session)) -> list[Source]:
    return list((await session.scalars(select(Source).order_by(Source.id))).all())


@router.post("/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: int,
    payload: SourceUpdate,
    session: AsyncSession = Depends(get_session),
) -> Source:
    src = await session.get(Source, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Источник не найден")
    src.enabled = payload.enabled
    await session.commit()
    await session.refresh(src)
    return src
