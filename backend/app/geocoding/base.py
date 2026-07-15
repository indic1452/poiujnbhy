"""Интерфейс геокодера: имя места → координаты + административный регион."""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class GeoResult:
    lat: float
    lon: float
    matched_name: str
    admin1: str | None = None  # область/край
    admin2: str | None = None  # район
    country: str | None = None  # RU/UA/BY
    kind: str = "gazetteer"  # object | gazetteer
    source: str = "gazetteer"
    confidence: float = 0.0


@dataclass
class AdminRegion:
    country: str | None = None
    admin1: str | None = None
    admin2: str | None = None


class Geocoder(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def geocode(
        self,
        name: str,
        *,
        admin_hint: str | None = None,
        country_hint: str | None = None,
        object_hint: str | None = None,
    ) -> GeoResult | None:
        """Разрешить название места в координаты (или None)."""
        raise NotImplementedError

    def find_mentions(self, text: str) -> list[str]:
        """Найти в тексте известные топонимы (для mock/подсказок)."""
        return []

    def reverse(self, lat: float, lon: float) -> AdminRegion | None:
        return None

    def health(self) -> bool:
        return True
