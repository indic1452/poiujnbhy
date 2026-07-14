"""Утилита загрузки контента источника (live через httpx или из fixtures)."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import BACKEND_DIR, settings


@dataclass
class Content:
    status: int
    text: str = ""
    content: bytes = b""
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    final_url: str | None = None


def browser_headers(extra: dict | None = None) -> dict:
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml,"
        "application/rss+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    }
    if extra:
        headers.update(extra)
    return headers


async def get_content(
    client: httpx.AsyncClient,
    url: str,
    *,
    fixture: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> Content:
    """Скачать URL. В режиме fixtures читает локальный файл ``fixture``."""
    if settings.source_mode == "fixtures":
        if not fixture:
            # В режиме fixtures источник без образца просто пустой (не ходим в сеть).
            return Content(status=204, final_url=url)
        path = BACKEND_DIR / fixture
        data = path.read_bytes()
        return Content(
            status=200,
            text=data.decode("utf-8", errors="replace"),
            content=data,
            final_url=url,
        )

    headers = browser_headers()
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    resp = await client.get(url, headers=headers, follow_redirects=True)
    if resp.status_code == 304:
        return Content(status=304, not_modified=True, final_url=str(resp.url))
    resp.raise_for_status()
    return Content(
        status=resp.status_code,
        text=resp.text,
        content=resp.content,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
        final_url=str(resp.url),
    )
