"""Источник RSS/Atom/RDF (feedparser + httpx с браузерным UA)."""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import httpx
from selectolax.parser import HTMLParser

from .base import FetchResult, MediaRef, RawItem, Source
from .fetch import get_content

_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
_VID_EXT = (".mp4", ".webm", ".mov", ".m4v")


def _struct_to_dt(entry) -> datetime | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    try:
        return datetime(*st[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        return HTMLParser(html).text(separator=" ", strip=True)
    except Exception:
        return html


def _media_from_entry(entry) -> list[MediaRef]:
    refs: list[MediaRef] = []
    seen: set[str] = set()

    def add(url: str | None, mime: str | None, medium: str | None = None) -> None:
        if not url or url in seen:
            return
        low = url.lower()
        is_video = (
            (mime and mime.startswith("video"))
            or medium == "video"
            or low.endswith(_VID_EXT)
        )
        is_image = (
            (mime and mime.startswith("image"))
            or medium == "image"
            or low.endswith(_IMG_EXT)
        )
        if is_video:
            seen.add(url)
            refs.append(MediaRef(type="video", url=url, video_url=url, mime=mime))
        elif is_image:
            seen.add(url)
            refs.append(MediaRef(type="image", url=url, mime=mime))

    for mc in entry.get("media_content", []) or []:
        add(mc.get("url"), mc.get("type"), mc.get("medium"))
    for mt in entry.get("media_thumbnail", []) or []:
        add(mt.get("url"), None, "image")
    for enc in entry.get("enclosures", []) or []:
        add(enc.get("href") or enc.get("url"), enc.get("type"))
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure":
            add(link.get("href"), link.get("type"))

    # og-подобные картинки, встроенные в summary/content
    html = entry.get("summary", "")
    for c in entry.get("content", []) or []:
        html += " " + (c.get("value") or "")
    if html and not refs:
        try:
            for img in HTMLParser(html).css("img"):
                add(img.attributes.get("src"), None, "image")
        except Exception:
            pass
    return refs


class RssSource(Source):
    type = "rss"

    def __init__(
        self, name: str, url: str, lang: str = "ru", fixture: str | None = None
    ) -> None:
        super().__init__(name=name, lang=lang, fixture=fixture)
        self.url = url

    async def fetch(
        self,
        client: httpx.AsyncClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        cursor: str | None = None,
    ) -> FetchResult:
        content = await get_content(
            client, self.url, fixture=self.fixture, etag=etag, last_modified=last_modified
        )
        if content.not_modified:
            return FetchResult(not_modified=True, etag=etag, last_modified=last_modified)

        parsed = feedparser.parse(content.content or content.text)
        items: list[RawItem] = []
        for e in parsed.entries:
            ext_id = e.get("id") or e.get("link") or e.get("title", "")
            if not ext_id:
                continue
            title = _strip_html(e.get("title", ""))
            summary = _strip_html(e.get("summary", ""))
            items.append(
                RawItem(
                    external_id=str(ext_id),
                    title=title,
                    text=summary,
                    url=e.get("link"),
                    lang=self.lang,
                    published_at=_struct_to_dt(e),
                    media=_media_from_entry(e),
                )
            )
        return FetchResult(
            items=items, etag=content.etag, last_modified=content.last_modified
        )
