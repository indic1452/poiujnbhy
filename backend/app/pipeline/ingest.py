"""Оркестрация конвейера: fetch → media → filter → summarize → cluster → store."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from .. import state
from ..config import settings
from ..models import Cluster, Item, Media, Source
from ..sources.base import RawItem
from ..sources.extract import extract_article
from ..sources.loader import records_index, runtime_source_for
from ..sources.media import store_media
from ..summarizer import get_summarizer
from ..summarizer.base import SourceDoc, SummarizerProvider
from ..summarizer.mock_provider import MockSummarizer
from ..vision import get_vision
from ..vision.base import VisionProvider
from ..vision.mock_vision import MockVision
from . import cluster as clusterlib
from . import relevance

log = logging.getLogger("ingest")


@dataclass
class IngestStats:
    sources_polled: int = 0
    new_items: int = 0
    new_clusters: int = 0
    updated_clusters: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sources_polled": self.sources_polled,
            "new_items": self.new_items,
            "new_clusters": self.new_clusters,
            "updated_clusters": self.updated_clusters,
            "errors": self.errors,
        }


def _web_to_file(web_path: str | None) -> Path | None:
    if not web_path or not web_path.startswith("/media/"):
        return None
    return settings.media_path / web_path[len("/media/") :]


async def _pick_provider(kind: str):
    """Вернуть провайдера с проверкой доступности и деградацией на mock."""
    if kind == "summarizer":
        prov: SummarizerProvider = get_summarizer()
        if await prov.health():
            return prov
        await prov.aclose()
        log.warning("Суммаризатор недоступен — переключаюсь на mock")
        return MockSummarizer()
    prov_v: VisionProvider = get_vision()
    if await prov_v.health():
        return prov_v
    await prov_v.aclose()
    log.warning("Vision-модель недоступна — переключаюсь на mock")
    return MockVision()


async def _analyze_primary(
    vision: VisionProvider, item: Item, media_objs: list[Media]
) -> str | None:
    """Проанализировать главное изображение/постер материала vision-моделью."""
    media = clusterlib.pick_primary_media(media_objs)
    if media is None:
        return None
    file_path = _web_to_file(media.local_path if media.type == "image" else media.poster_path)
    if file_path is None or not file_path.exists():
        return None
    try:
        note = await vision.analyze_image(
            file_path.read_bytes(), mime="image/jpeg", context=item.title_ru or item.orig_title
        )
    except Exception as exc:  # noqa: BLE001 — деградация, не роняем конвейер
        log.warning("Ошибка vision-анализа: %s", exc)
        return None
    media.analysis_ru = note
    media.is_analyzed = True
    return note


async def _process_source(
    session: AsyncSession,
    client: httpx.AsyncClient,
    src: Source,
    index: dict,
    summarizer: SummarizerProvider,
    vision: VisionProvider,
    stats: IngestStats,
    affected: set[int],
) -> None:
    runtime = runtime_source_for(src, index)
    result = await runtime.fetch(
        client, etag=src.etag, last_modified=src.last_modified, cursor=src.cursor
    )
    src.last_fetch = datetime.now(timezone.utc)
    if result.etag:
        src.etag = result.etag
    if result.last_modified:
        src.last_modified = result.last_modified
    if result.cursor:
        src.cursor = result.cursor
    if result.not_modified or not result.items:
        return

    existing_ids = set(
        (
            await session.scalars(
                select(Item.external_id).where(
                    Item.source_id == src.id,
                    Item.external_id.in_([i.external_id for i in result.items]),
                )
            )
        ).all()
    )

    for raw in result.items:
        if raw.external_id in existing_ids:
            continue
        ok, rel = relevance.is_relevant(raw.title, raw.text)
        if not ok:
            continue
        await _process_item(session, client, src, raw, rel, summarizer, vision, stats, affected)


async def _process_item(
    session: AsyncSession,
    client: httpx.AsyncClient,
    src: Source,
    raw: RawItem,
    rel: float,
    summarizer: SummarizerProvider,
    vision: VisionProvider,
    stats: IngestStats,
    affected: set[int],
) -> None:
    text = raw.text
    media_refs = list(raw.media)

    # Дообогащение статьи (только live, best-effort): полный текст + og-медиа
    if settings.source_mode == "live" and raw.url and src.type in ("rss", "gnews"):
        if len(text) < 400 or not media_refs:
            full_text, og_media = await extract_article(client, raw.url)
            if full_text:
                text = full_text
            if og_media and not media_refs:
                media_refs = og_media

    item = Item(
        source_id=src.id,
        external_id=raw.external_id,
        url=raw.url,
        orig_title=raw.title,
        orig_text=text,
        lang=raw.lang,
        published_at=raw.published_at,
        relevance=rel,
        is_relevant=True,
    )
    session.add(item)
    await session.flush()  # получить item.id

    media_objs = await store_media(client, item, media_refs)
    session.add_all(media_objs)
    await session.flush()

    # Перевод + краткое изложение материала
    try:
        summary = await summarizer.summarize_item(raw.title, text, raw.lang)
        item.title_ru = summary.title_ru
        item.summary_ru = summary.summary_ru
        item.category = summary.category
        item.key_points = summary.key_points
        item.model_used = summarizer.name
    except Exception as exc:  # noqa: BLE001
        log.warning("Ошибка суммаризации: %s", exc)
        item.title_ru = raw.title
        item.summary_ru = text[:300]
        item.category = src.category_hint or "Прочее"
    item.processed_at = datetime.now(timezone.utc)

    # Vision-анализ главного медиа
    await _analyze_primary(vision, item, media_objs)

    stats.new_items += 1
    await _attach_to_cluster(session, item, stats, affected)


async def _attach_to_cluster(
    session: AsyncSession, item: Item, stats: IngestStats, affected: set[int]
) -> None:
    window_start = datetime.now(timezone.utc) - timedelta(hours=settings.cluster_window_hours)
    recent = (
        await session.scalars(
            select(Cluster).where(Cluster.last_updated >= window_start)
        )
    ).all()
    candidates = [(c.id, f"{c.headline_ru} {item.category or ''}") for c in recent]
    match_id = clusterlib.best_match(
        item.title_ru or item.orig_title, candidates, float(settings.cluster_similarity)
    )

    if match_id is None:
        cluster = Cluster(
            headline_ru=item.title_ru or item.orig_title,
            digest_ru=item.summary_ru or "",
            category=item.category or "Прочее",
            key_points=item.key_points,
            source_count=1,
        )
        session.add(cluster)
        await session.flush()
        stats.new_clusters += 1
    else:
        cluster = await session.get(Cluster, match_id)
        stats.updated_clusters += 1

    item.cluster_id = cluster.id
    cluster.last_updated = datetime.now(timezone.utc)
    await session.flush()
    affected.add(cluster.id)


async def _recompute_cluster(
    session: AsyncSession, cluster_id: int, summarizer: SummarizerProvider
) -> None:
    cluster = await session.get(Cluster, cluster_id)
    if cluster is None:
        return
    items = (
        await session.scalars(
            select(Item)
            .where(Item.cluster_id == cluster_id)
            .options(selectinload(Item.media), selectinload(Item.source))
            .order_by(Item.published_at.desc().nullslast())
            .limit(8)
        )
    ).all()
    if not items:
        return

    docs = [
        SourceDoc(
            title=it.title_ru or it.orig_title,
            text=it.summary_ru or it.orig_text,
            lang="ru",
            source_name=it.source.name if it.source else "",
        )
        for it in items
    ]
    vision_notes = [m.analysis_ru for it in items for m in it.media if m.analysis_ru][:2]

    all_media = [m for it in items for m in it.media]
    primary = clusterlib.pick_primary_media(all_media)
    cluster.primary_media_id = primary.id if primary else None
    cluster.source_count = len({it.source_id for it in items})

    if len(items) == 1:
        it = items[0]
        cluster.headline_ru = it.title_ru or it.orig_title
        cluster.digest_ru = it.summary_ru or ""
        cluster.category = it.category or "Прочее"
        cluster.key_points = it.key_points
        return

    try:
        summ = await summarizer.summarize_cluster(docs, vision_notes)
        cluster.headline_ru = summ.headline_ru
        cluster.digest_ru = summ.digest_ru
        cluster.category = summ.category
        cluster.key_points = summ.key_points
    except Exception as exc:  # noqa: BLE001
        log.warning("Ошибка обобщения кластера: %s", exc)


async def run_ingest(sessionmaker: async_sessionmaker[AsyncSession]) -> IngestStats:
    """Один прогон конвейера по всем включённым источникам."""
    stats = IngestStats()
    if state.ingest_lock.locked():
        stats.errors.append("Ingest уже выполняется")
        return stats

    async with state.ingest_lock:
        summarizer = await _pick_provider("summarizer")
        vision = await _pick_provider("vision")
        index = records_index()
        affected: set[int] = set()
        try:
            async with httpx.AsyncClient(
                timeout=settings.request_timeout, http2=True
            ) as client:
                async with sessionmaker() as session:
                    sources = (
                        await session.scalars(
                            select(Source).where(Source.enabled.is_(True))
                        )
                    ).all()
                    for src in sources:
                        stats.sources_polled += 1
                        try:
                            await _process_source(
                                session, client, src, index, summarizer, vision, stats, affected
                            )
                            await session.commit()
                        except Exception as exc:  # noqa: BLE001
                            await session.rollback()
                            log.exception("Источник %s: ошибка", src.name)
                            stats.errors.append(f"{src.name}: {exc}")

                    for cid in affected:
                        try:
                            await _recompute_cluster(session, cid, summarizer)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("Кластер %s: %s", cid, exc)
                    await session.commit()
        finally:
            await summarizer.aclose()
            await vision.aclose()

        state.last_ingest = datetime.now(timezone.utc)
        state.last_stats = stats.as_dict()
    return stats
