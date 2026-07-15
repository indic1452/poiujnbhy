"""ORM-модели PostgreSQL: Source, Item, Cluster, Media."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(20))  # rss | gnews | telegram
    url_or_username: Mapped[str] = mapped_column(String(500))
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    category_hint: Mapped[str | None] = mapped_column(String(80), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    last_fetch: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cursor: Mapped[str | None] = mapped_column(String(120), nullable=True)  # tg: last message_id

    items: Mapped[list["Item"]] = relationship(back_populates="source")


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    headline_ru: Mapped[str] = mapped_column(Text, default="")
    digest_ru: Mapped[str] = mapped_column(Text, default="")
    key_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str] = mapped_column(String(80), default="Прочее")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    primary_media_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Геолокация события (для карты)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    place_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    admin1: Mapped[str | None] = mapped_column(String(120), nullable=True)  # область/край
    admin2: Mapped[str | None] = mapped_column(String(120), nullable=True)  # район
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    geo_source: Mapped[str | None] = mapped_column(String(30), nullable=True)  # text|object|vision|manual
    geo_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    geo_needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    locations: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # все извлечённые точки

    items: Mapped[list["Item"]] = relationship(back_populates="cluster")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(500))  # guid / msg_id
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    orig_title: Mapped[str] = mapped_column(Text, default="")
    orig_text: Mapped[str] = mapped_column(Text, default="")
    lang: Mapped[str] = mapped_column(String(8), default="ru")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Результаты обработки моделью
    title_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    locations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    is_relevant: Mapped[bool] = mapped_column(Boolean, default=False)
    model_used: Mapped[str | None] = mapped_column(String(80), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"), nullable=True)

    source: Mapped["Source"] = relationship(back_populates="items")
    cluster: Mapped["Cluster | None"] = relationship(back_populates="items")
    media: Mapped[list["Media"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(10))  # image | video
    source_url: Mapped[str] = mapped_column(String(1000))
    video_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)  # /media/<...>
    poster_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # секунды
    mime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    analysis_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_analyzed: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    item: Mapped["Item"] = relationship(back_populates="media")
