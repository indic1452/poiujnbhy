"""Pydantic-схемы ответов REST API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    source_url: str
    video_url: str | None = None
    local_path: str | None = None
    poster_path: str | None = None
    width: int | None = None
    height: int | None = None
    duration: int | None = None
    mime: str | None = None
    analysis_ru: str | None = None


class SourceBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    lang: str


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str | None = None
    orig_title: str = ""
    title_ru: str | None = None
    summary_ru: str | None = None
    category: str | None = None
    published_at: datetime | None = None
    source: SourceBrief | None = None
    media: list[MediaOut] = []


class ClusterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    headline_ru: str
    digest_ru: str
    key_points: list | None = None
    category: str
    first_seen: datetime
    last_updated: datetime
    source_count: int
    primary_media: MediaOut | None = None
    sources: list[SourceBrief] = []
    items: list[ItemOut] = []


class FeedResponse(BaseModel):
    total: int
    limit: int
    offset: int
    clusters: list[ClusterOut]


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    url_or_username: str
    lang: str
    category_hint: str | None = None
    enabled: bool
    last_fetch: datetime | None = None


class SourceUpdate(BaseModel):
    enabled: bool


class StatusResponse(BaseModel):
    summarizer_backend: str
    summarizer_model: str
    summarizer_available: bool
    vision_backend: str
    vision_model: str
    vision_available: bool
    source_mode: str
    last_ingest: datetime | None = None
    clusters: int
    items: int
    sources: int


class RefreshResponse(BaseModel):
    started: bool
    detail: str
