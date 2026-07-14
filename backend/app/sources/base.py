"""Общие контракты источников: MediaRef, RawItem, FetchResult, Source."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime

import httpx


@dataclass
class MediaRef:
    """Ссылка на медиа, найденное в материале (ещё не скачано)."""

    type: str  # image | video
    url: str  # URL картинки или постера
    video_url: str | None = None  # прямой URL видеофайла (mp4/webm), если есть
    duration: int | None = None  # длительность видео, сек
    width: int | None = None
    height: int | None = None
    mime: str | None = None


@dataclass
class RawItem:
    """Нормализованный материал из любого источника (до обработки моделью)."""

    external_id: str
    title: str = ""
    text: str = ""
    url: str | None = None
    lang: str = "ru"
    published_at: datetime | None = None
    media: list[MediaRef] = field(default_factory=list)


@dataclass
class FetchResult:
    """Результат опроса источника."""

    items: list[RawItem] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None
    cursor: str | None = None
    not_modified: bool = False


class Source(abc.ABC):
    """Абстрактный источник материалов."""

    #: тип для БД: rss | gnews | telegram
    type: str = "rss"

    def __init__(self, name: str, lang: str = "ru", fixture: str | None = None) -> None:
        self.name = name
        self.lang = lang
        self.fixture = fixture

    @abc.abstractmethod
    async def fetch(
        self,
        client: httpx.AsyncClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        cursor: str | None = None,
    ) -> FetchResult:
        """Вернуть свежие материалы источника."""
        raise NotImplementedError
