"""Загрузка списка источников из sources.yaml, сборка runtime-объектов и seed БД."""
from __future__ import annotations

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Source as SourceModel
from .base import Source
from .gnews import GoogleNewsSource
from .rss import RssSource
from .telegram_web import TelegramWebSource


def load_source_records() -> list[dict]:
    """Прочитать sources.yaml → нормализованные записи."""
    raw = yaml.safe_load(settings.sources_path.read_text(encoding="utf-8")) or {}
    records: list[dict] = []
    for r in raw.get("sources", []):
        typ = r["type"]
        if typ == "telegram":
            uou = str(r["username"]).lstrip("@")
        elif typ == "gnews":
            uou = r.get("url") or GoogleNewsSource.build_url(
                r["query"],
                hl=r.get("hl", "ru"),
                gl=r.get("gl", "RU"),
                ceid=r.get("ceid", "RU:ru"),
            )
        else:
            uou = r["url"]
        records.append(
            {
                "name": r["name"],
                "type": typ,
                "url_or_username": uou,
                "lang": r.get("lang", "ru"),
                "category_hint": r.get("category_hint"),
                "enabled": r.get("enabled", True),
                "fixture": r.get("fixture"),
            }
        )
    return records


def build_source(record: dict) -> Source:
    typ = record["type"]
    name = record["name"]
    lang = record.get("lang", "ru")
    fixture = record.get("fixture")
    uou = record["url_or_username"]
    if typ == "telegram":
        return TelegramWebSource(name, uou, lang, fixture)
    if typ == "gnews":
        return GoogleNewsSource(name, uou, lang, fixture)
    return RssSource(name, uou, lang, fixture)


def records_index() -> dict[tuple[str, str], dict]:
    return {(r["type"], r["url_or_username"]): r for r in load_source_records()}


def runtime_source_for(db_source: SourceModel, index: dict | None = None) -> Source:
    index = index if index is not None else records_index()
    record = index.get((db_source.type, db_source.url_or_username)) or {
        "name": db_source.name,
        "type": db_source.type,
        "url_or_username": db_source.url_or_username,
        "lang": db_source.lang,
        "fixture": None,
    }
    return build_source(record)


async def seed_sources(session: AsyncSession) -> int:
    """Добавить в БД источники из yaml, которых там ещё нет. Вернуть число новых."""
    existing = {
        (s.type, s.url_or_username)
        for s in (await session.scalars(select(SourceModel))).all()
    }
    added = 0
    for rec in load_source_records():
        key = (rec["type"], rec["url_or_username"])
        if key in existing:
            continue
        session.add(
            SourceModel(
                name=rec["name"],
                type=rec["type"],
                url_or_username=rec["url_or_username"],
                lang=rec["lang"],
                category_hint=rec["category_hint"],
                enabled=rec["enabled"],
            )
        )
        added += 1
    if added:
        await session.commit()
    return added
