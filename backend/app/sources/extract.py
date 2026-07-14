"""Извлечение полного текста статьи и медиа (og:image/og:video) через trafilatura."""
from __future__ import annotations

import httpx
import trafilatura
from selectolax.parser import HTMLParser

from .base import MediaRef
from .fetch import browser_headers

_OG_IMAGE = ["og:image", "og:image:url", "twitter:image", "twitter:image:src"]
_OG_VIDEO = ["og:video", "og:video:url", "og:video:secure_url"]


def _og_media(html: str) -> list[MediaRef]:
    refs: list[MediaRef] = []
    seen: set[str] = set()
    try:
        tree = HTMLParser(html)
    except Exception:
        return refs
    for meta in tree.css("meta"):
        prop = (meta.attributes.get("property") or meta.attributes.get("name") or "").lower()
        url = meta.attributes.get("content")
        if not url or url in seen:
            continue
        if prop in _OG_VIDEO:
            seen.add(url)
            refs.append(MediaRef(type="video", url=url, video_url=url))
        elif prop in _OG_IMAGE:
            seen.add(url)
            refs.append(MediaRef(type="image", url=url))
    return refs


async def extract_article(
    client: httpx.AsyncClient, url: str
) -> tuple[str, list[MediaRef]]:
    """Скачать статью и вернуть (текст, медиа). Best-effort: при ошибке ('', [])."""
    try:
        resp = await client.get(url, headers=browser_headers(), follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except httpx.HTTPError:
        return "", []

    text = ""
    try:
        extracted = trafilatura.extract(
            html, include_comments=False, include_tables=False, favor_recall=True
        )
        text = extracted or ""
    except Exception:
        text = ""

    return text, _og_media(html)
