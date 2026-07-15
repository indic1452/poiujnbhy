"""Выгрузка событий за период: GET /api/export?format=geojson|csv|json."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_session
from ..models import Cluster, Item
from .geoquery import apply_event_filters, cluster_feature, media_brief

router = APIRouter(prefix="/api", tags=["export"])

_CSV_COLS = [
    "id", "time", "event_type", "category", "country", "admin1", "admin2",
    "place_name", "lat", "lon", "headline_ru", "digest_ru", "media_url",
]


@router.get("/export")
async def export(
    format: str = Query("geojson", pattern="^(geojson|csv|json)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    event_type: str | None = None,
    region: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> Response:
    stmt = apply_event_filters(
        select(Cluster), since=since, until=until, event_type=event_type, region=region
    ).options(selectinload(Cluster.items).selectinload(Item.media)).order_by(
        Cluster.last_updated.desc()
    )
    clusters = (await session.scalars(stmt)).all()

    if format == "geojson":
        fc = {
            "type": "FeatureCollection",
            "features": [cluster_feature(c) for c in clusters if c.lat is not None],
        }
        return Response(
            content=json.dumps(fc, ensure_ascii=False, indent=2),
            media_type="application/geo+json",
            headers={"Content-Disposition": "attachment; filename=events.geojson"},
        )

    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for c in clusters:
            media = media_brief(c)
            w.writerow(
                {
                    "id": c.id,
                    "time": c.last_updated.isoformat() if c.last_updated else "",
                    "event_type": c.event_type or "",
                    "category": c.category or "",
                    "country": c.country or "",
                    "admin1": c.admin1 or "",
                    "admin2": c.admin2 or "",
                    "place_name": c.place_name or "",
                    "lat": c.lat if c.lat is not None else "",
                    "lon": c.lon if c.lon is not None else "",
                    "headline_ru": c.headline_ru,
                    "digest_ru": c.digest_ru,
                    "media_url": (media or {}).get("url") or "",
                }
            )
        return Response(
            content="﻿" + buf.getvalue(),  # BOM для Excel
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=events.csv"},
        )

    # json
    rows = [
        {
            "id": c.id,
            "time": c.last_updated.isoformat() if c.last_updated else None,
            "event_type": c.event_type,
            "category": c.category,
            "country": c.country,
            "admin1": c.admin1,
            "admin2": c.admin2,
            "place_name": c.place_name,
            "lat": c.lat,
            "lon": c.lon,
            "headline_ru": c.headline_ru,
            "digest_ru": c.digest_ru,
            "media": media_brief(c),
        }
        for c in clusters
    ]
    return Response(
        content=json.dumps(rows, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=events.json"},
    )
