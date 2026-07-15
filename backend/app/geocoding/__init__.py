"""Фабрика геокодера (кэшируемый singleton, данные грузятся один раз)."""
from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import AdminRegion, Geocoder, GeoResult
from .gazetteer import GazetteerGeocoder, normalize

__all__ = [
    "AdminRegion",
    "Geocoder",
    "GeoResult",
    "GazetteerGeocoder",
    "normalize",
    "get_geocoder",
]


@lru_cache
def get_geocoder() -> Geocoder | None:
    if not settings.geocoder_enabled:
        return None
    return GazetteerGeocoder()
