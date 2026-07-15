"""Карта событий: GET /api/events.geojson (FeatureCollection точек)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Cluster, Item
from .geoquery import apply_event_filters, cluster_feature

router = APIRouter(prefix="/api", tags=["map"])


@router.get("/events.geojson")
async def events_geojson(
    since: datetime | None = None,
    until: datetime | None = None,
    event_type: str | None = None,
    region: str | None = None,
    needs_review: bool | None = None,
    limit: int = Query(2000, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    stmt = select(Cluster).where(Cluster.lat.isnot(None))
    stmt = apply_event_filters(
        stmt,
        since=since,
        until=until,
        event_type=event_type,
        region=region,
        needs_review=needs_review,
    )
    stmt = (
        stmt.options(selectinload(Cluster.items).selectinload(Item.media))
        .order_by(Cluster.last_updated.desc())
        .limit(limit)
    )
    clusters = (await session.scalars(stmt)).all()
    return {
        "type": "FeatureCollection",
        "features": [cluster_feature(c) for c in clusters],
    }
