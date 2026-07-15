"""Статистика событий: GET /api/stats (по типам/районам/дням)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Cluster
from .geoquery import apply_event_filters

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def stats(
    since: datetime | None = None,
    until: datetime | None = None,
    event_type: str | None = None,
    region: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    def base(col):
        return apply_event_filters(
            select(col, func.count(Cluster.id)),
            since=since,
            until=until,
            event_type=event_type,
            region=region,
        ).group_by(col).order_by(func.count(Cluster.id).desc())

    by_type = [
        {"key": t or "прочее", "count": n}
        for t, n in (await session.execute(base(Cluster.event_type))).all()
    ]
    by_region = [
        {"key": r, "count": n}
        for r, n in (await session.execute(base(Cluster.admin1))).all()
        if r
    ]

    day = func.date_trunc("day", Cluster.last_updated).label("day")
    day_stmt = apply_event_filters(
        select(day, func.count(Cluster.id)),
        since=since,
        until=until,
        event_type=event_type,
        region=region,
    ).group_by(day).order_by(day)
    by_day = [
        {"day": d.date().isoformat() if d else None, "count": n}
        for d, n in (await session.execute(day_stmt)).all()
    ]

    total = int((await session.scalar(
        apply_event_filters(select(func.count(Cluster.id)),
                            since=since, until=until,
                            event_type=event_type, region=region)
    )) or 0)
    geolocated = int((await session.scalar(
        apply_event_filters(select(func.count(Cluster.id)).where(Cluster.lat.isnot(None)),
                            since=since, until=until,
                            event_type=event_type, region=region)
    )) or 0)

    return {
        "total": total,
        "geolocated": geolocated,
        "by_type": by_type,
        "by_region": by_region,
        "by_day": by_day,
    }
