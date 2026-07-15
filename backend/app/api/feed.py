"""Лента сюжетов: GET /api/feed."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Cluster, Item
from ..schemas import (
    ClusterOut,
    FeedResponse,
    ItemOut,
    MediaOut,
    SourceBrief,
)

router = APIRouter(prefix="/api", tags=["feed"])


def _apply_filters(stmt, category, q, since, source_id):
    if category:
        stmt = stmt.where(Cluster.category == category)
    if since:
        stmt = stmt.where(Cluster.last_updated >= since)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Cluster.headline_ru.ilike(like) | Cluster.digest_ru.ilike(like))
    if source_id:
        sub = select(Item.cluster_id).where(Item.source_id == source_id)
        stmt = stmt.where(Cluster.id.in_(sub))
    return stmt


@router.get("/feed", response_model=FeedResponse)
async def get_feed(
    category: str | None = None,
    q: str | None = None,
    since: datetime | None = None,
    source_id: int | None = None,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> FeedResponse:
    total_stmt = _apply_filters(select(func.count(Cluster.id)), category, q, since, source_id)
    total = int((await session.scalar(total_stmt)) or 0)

    stmt = _apply_filters(select(Cluster), category, q, since, source_id)
    stmt = (
        stmt.options(
            selectinload(Cluster.items).selectinload(Item.source),
            selectinload(Cluster.items).selectinload(Item.media),
        )
        .order_by(Cluster.last_updated.desc())
        .limit(limit)
        .offset(offset)
    )
    clusters = (await session.scalars(stmt)).all()

    out: list[ClusterOut] = []
    for c in clusters:
        items = sorted(
            c.items,
            key=lambda it: (it.published_at is not None, it.published_at or it.fetched_at),
            reverse=True,
        )
        all_media = [m for it in items for m in it.media]
        primary = next((m for m in all_media if m.id == c.primary_media_id), None)
        if primary is None and all_media:
            primary = all_media[0]

        seen_sources: dict[int, SourceBrief] = {}
        for it in items:
            if it.source and it.source.id not in seen_sources:
                seen_sources[it.source.id] = SourceBrief.model_validate(it.source)

        out.append(
            ClusterOut(
                id=c.id,
                headline_ru=c.headline_ru,
                digest_ru=c.digest_ru,
                key_points=c.key_points,
                category=c.category,
                event_type=c.event_type,
                first_seen=c.first_seen,
                last_updated=c.last_updated,
                source_count=c.source_count,
                lat=c.lat,
                lon=c.lon,
                place_name=c.place_name,
                admin1=c.admin1,
                admin2=c.admin2,
                country=c.country,
                geo_confidence=c.geo_confidence,
                geo_needs_review=c.geo_needs_review,
                primary_media=MediaOut.model_validate(primary) if primary else None,
                sources=list(seen_sources.values()),
                items=[ItemOut.model_validate(it) for it in items],
            )
        )

    return FeedResponse(total=total, limit=limit, offset=offset, clusters=out)
