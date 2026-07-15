"""Общие фильтры и сборка свойств событий (карта/статистика/выгрузка)."""
from __future__ import annotations

from datetime import datetime

from ..categories import event_color
from ..models import Cluster, Item
from ..pipeline import cluster as clusterlib


def apply_event_filters(
    stmt,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    event_type: str | None = None,
    region: str | None = None,
    category: str | None = None,
    needs_review: bool | None = None,
):
    if since:
        stmt = stmt.where(Cluster.last_updated >= since)
    if until:
        stmt = stmt.where(Cluster.last_updated <= until)
    if event_type:
        stmt = stmt.where(Cluster.event_type == event_type)
    if category:
        stmt = stmt.where(Cluster.category == category)
    if region:
        stmt = stmt.where(Cluster.admin1.ilike(f"%{region}%"))
    if needs_review is not None:
        stmt = stmt.where(Cluster.geo_needs_review.is_(needs_review))
    return stmt


def _primary_media(cluster: Cluster):
    all_media = [m for it in cluster.items for m in it.media]
    if not all_media:
        return None
    primary = next((m for m in all_media if m.id == cluster.primary_media_id), None)
    return primary or clusterlib.pick_primary_media(all_media) or all_media[0]


def media_brief(cluster: Cluster) -> dict | None:
    m = _primary_media(cluster)
    if m is None:
        return None
    return {
        "type": m.type,
        "url": m.local_path or m.poster_path or m.source_url,
        "poster": m.poster_path,
        "video_url": m.video_url,
    }


def cluster_feature(cluster: Cluster) -> dict:
    """GeoJSON Feature события (Point) с simplestyle-свойствами."""
    color = event_color(cluster.event_type)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [cluster.lon, cluster.lat]},
        "properties": {
            "id": cluster.id,
            "headline_ru": cluster.headline_ru,
            "digest_ru": cluster.digest_ru,
            "event_type": cluster.event_type,
            "category": cluster.category,
            "place_name": cluster.place_name,
            "admin1": cluster.admin1,
            "admin2": cluster.admin2,
            "country": cluster.country,
            "time": cluster.last_updated.isoformat() if cluster.last_updated else None,
            "source_count": cluster.source_count,
            "geo_confidence": cluster.geo_confidence,
            "geo_needs_review": cluster.geo_needs_review,
            "media": media_brief(cluster),
            "marker-color": color,
            "marker-symbol": "danger" if (cluster.event_type or "").startswith("удар") else "",
        },
    }


# для selectinload в вызывающих модулях
CLUSTER_LOADS = (
    (Cluster.items, Item.media),
    (Cluster.items, Item.source),
)
