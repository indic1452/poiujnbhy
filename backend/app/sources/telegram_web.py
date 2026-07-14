"""Скрейпер публичных Telegram-каналов через веб-превью t.me/s/<channel>.

Без ключей и авторизации. Извлекает текст, дату, фото и ВИДЕО (src/постер/
длительность). Пагинация назад — параметр ?before=<message_id>; курсор —
максимальный виденный message_id (опрашиваем только новое).

Селекторы Telegram могут меняться — они собраны в этом модуле, при поломке
чинить здесь (сверять с живой страницей t.me/s/<channel>).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser, Node

from .base import FetchResult, MediaRef, RawItem, Source
from .fetch import get_content

_BG_RE = re.compile(r"background-image\s*:\s*url\(['\"]?(.*?)['\"]?\)")


def _bg_url(node: Node | None) -> str | None:
    if node is None:
        return None
    style = node.attributes.get("style") or ""
    m = _BG_RE.search(style)
    return m.group(1) if m else None


def _duration_seconds(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    sec = 0
    for n in nums:
        sec = sec * 60 + n
    return sec or None


def _parse_message(node: Node, channel: str) -> RawItem | None:
    data_post = node.attributes.get("data-post")
    if not data_post or "/" not in data_post:
        return None
    msg_id = data_post.split("/")[-1]

    text_node = node.css_first(".tgme_widget_message_text")
    text = text_node.text(separator=" ", strip=True) if text_node else ""

    published_at: datetime | None = None
    time_node = node.css_first(".tgme_widget_message_date time")
    if time_node:
        dt_attr = time_node.attributes.get("datetime")
        if dt_attr:
            try:
                published_at = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
            except ValueError:
                published_at = None
    if published_at and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    media: list[MediaRef] = []

    # Видео: <video ... src=...> + постер-превью + длительность
    video_node = node.css_first("video.tgme_widget_message_video, .tgme_widget_message_video")
    if video_node is not None:
        src = video_node.attributes.get("src")
        thumb = _bg_url(node.css_first(".tgme_widget_message_video_thumb"))
        dur_node = node.css_first(
            ".tgme_widget_message_video_duration, .message_video_duration"
        )
        dur = _duration_seconds(dur_node.text(strip=True) if dur_node else None)
        media.append(
            MediaRef(
                type="video",
                url=thumb or src or "",
                video_url=src,
                duration=dur,
                mime="video/mp4" if src else None,
            )
        )

    # Фото
    for photo in node.css(".tgme_widget_message_photo_wrap"):
        url = _bg_url(photo)
        if url:
            media.append(MediaRef(type="image", url=url))

    # Картинка превью ссылки
    if not media:
        prev = _bg_url(node.css_first(".tgme_widget_message_link_preview_image"))
        if prev:
            media.append(MediaRef(type="image", url=prev))

    media = [m for m in media if m.url or m.video_url]

    if not text and not media:
        return None

    return RawItem(
        external_id=f"{channel}/{msg_id}",
        title=text[:120] if text else f"Сообщение {channel}/{msg_id}",
        text=text,
        url=f"https://t.me/{channel}/{msg_id}",
        lang="ru",
        published_at=published_at,
        media=media,
    )


class TelegramWebSource(Source):
    type = "telegram"

    def __init__(
        self, name: str, username: str, lang: str = "ru", fixture: str | None = None
    ) -> None:
        super().__init__(name=name, lang=lang, fixture=fixture)
        self.username = username.lstrip("@")

    async def fetch(
        self,
        client: httpx.AsyncClient,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        cursor: str | None = None,
    ) -> FetchResult:
        url = f"https://t.me/s/{self.username}"
        content = await get_content(client, url, fixture=self.fixture)
        tree = HTMLParser(content.text)

        items: list[RawItem] = []
        max_id = int(cursor) if (cursor and cursor.isdigit()) else 0
        new_max = max_id
        for node in tree.css(".tgme_widget_message"):
            item = _parse_message(node, self.username)
            if item is None:
                continue
            mid = item.external_id.split("/")[-1]
            mid_int = int(mid) if mid.isdigit() else 0
            if mid_int > new_max:
                new_max = mid_int
            if mid_int > max_id:
                items.append(item)

        return FetchResult(items=items, cursor=str(new_max) if new_max else cursor)
