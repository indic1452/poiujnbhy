"""Telethon-источник: авторизованный доступ к Telegram (полные фото/видео, история).

Требует TELEGRAM_API_ID/HASH и разовый вход (`python -m app.telegram_login`,
создаёт .session). Импорт telethon — ленивый (внутри функций), поэтому модуль
и тесты работают без установленного telethon и без ключей.

Чистые помощники (group_by_album, raw_item_from_group) не зависят от telethon и
покрыты юнит-тестами на фейковых объектах сообщений.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone

from ..config import settings
from .base import FetchResult, MediaRef, RawItem, Source

log = logging.getLogger("telegram")

_client = None
_lock = asyncio.Lock()


async def get_tg_client():
    """Общий на процесс Telethon-клиент (лениво подключается)."""
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        if not (settings.telegram_api_id and settings.telegram_api_hash):
            raise RuntimeError("Не заданы TELEGRAM_API_ID/TELEGRAM_API_HASH")
        from telethon import TelegramClient  # ленивый импорт

        client = TelegramClient(
            settings.telegram_session,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(
                "Telethon-сессия не авторизована — запустите: python -m app.telegram_login"
            )
        _client = client
        return _client


async def close_tg_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        _client = None


# ---------- чистые помощники (без telethon) ----------
def _msg_text(msg) -> str:
    return getattr(msg, "message", None) or getattr(msg, "text", None) or ""


def _msg_dt(msg):
    d = getattr(msg, "date", None)
    if d is None:
        return None
    if getattr(d, "tzinfo", None) is None:
        return d.replace(tzinfo=timezone.utc)
    return d


def _forwarded_name(msg) -> str | None:
    fwd = getattr(msg, "forward", None)
    if not fwd:
        return None
    chat = getattr(fwd, "chat", None)
    if chat is not None:
        return getattr(chat, "title", None) or getattr(chat, "username", None)
    return getattr(fwd, "from_name", None)


def group_by_album(messages: list) -> list[list]:
    """Сгруппировать подряд идущие сообщения с одинаковым grouped_id (альбомы)."""
    groups: list[list] = []
    cur: list = []
    cur_gid = None
    for m in messages:
        gid = getattr(m, "grouped_id", None)
        if gid is not None and gid == cur_gid and cur:
            cur.append(m)
        else:
            if cur:
                groups.append(cur)
            cur = [m]
            cur_gid = gid
    if cur:
        groups.append(cur)
    return groups


def raw_item_from_group(
    group: list, channel: str, lang: str, media_refs: list[MediaRef]
) -> RawItem:
    """Собрать RawItem из группы сообщений (альбома) + готовых медиа."""
    base = group[0]
    text = ""
    for m in group:
        t = _msg_text(m)
        if t:
            text = t
            break
    ids = [int(getattr(m, "id", 0) or 0) for m in group]
    anchor = min(ids) if ids else 0
    fwd = _forwarded_name(base)
    if fwd:
        text = f"[переслано из {fwd}] {text}".strip()
    return RawItem(
        external_id=f"{channel}/{anchor}",
        title=(text[:120] if text else f"Сообщение {channel}/{anchor}"),
        text=text,
        url=f"https://t.me/{channel}/{anchor}",
        lang=lang,
        published_at=_msg_dt(base),
        media=media_refs,
    )


class TelegramClientSource(Source):
    type = "telegram"

    def __init__(
        self, name: str, username: str, lang: str = "ru", fixture: str | None = None
    ) -> None:
        super().__init__(name=name, lang=lang, fixture=fixture)
        self.username = username.lstrip("@")

    async def _group_media(self, tg, group: list, download_video: bool) -> list[MediaRef]:
        refs: list[MediaRef] = []
        for m in group:
            try:
                if getattr(m, "photo", None) is not None:
                    data = await tg.download_media(m, file=bytes)
                    if data:
                        refs.append(MediaRef(type="image", data=data))
                elif getattr(m, "video", None) is not None:
                    poster = await tg.download_media(m, thumb=-1, file=bytes)
                    vdata = None
                    mfile = getattr(m, "file", None)
                    size_ok = mfile and (mfile.size or 0) <= settings.max_media_bytes
                    if download_video and size_ok:
                        vdata = await tg.download_media(m, file=bytes)
                    dur = getattr(mfile, "duration", None)
                    refs.append(
                        MediaRef(
                            type="video",
                            poster_data=poster,
                            data=vdata,
                            duration=int(dur) if dur else None,
                            mime="video/mp4",
                        )
                    )
            except Exception as exc:  # noqa: BLE001 — одно медиа не должно ронять опрос
                log.warning("Telegram media %s: %s", self.username, exc)
        return refs

    async def fetch(
        self,
        client,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        cursor: str | None = None,
    ) -> FetchResult:
        try:
            tg = await get_tg_client()
        except Exception as exc:  # noqa: BLE001
            log.warning("Telethon недоступен (%s): %s", self.username, exc)
            return FetchResult(cursor=cursor)

        from telethon.errors import FloodWaitError

        min_id = int(cursor) if (cursor and str(cursor).isdigit()) else 0
        try:
            entity = await tg.get_entity(self.username)
            messages = [
                m
                async for m in tg.iter_messages(
                    entity, min_id=min_id, limit=settings.telegram_max_messages
                )
            ]
        except FloodWaitError as exc:
            wait = min(getattr(exc, "seconds", 60), settings.telegram_flood_cap)
            log.warning("FloodWait %s c для %s", wait, self.username)
            await asyncio.sleep(wait)
            return FetchResult(cursor=cursor)
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram fetch %s: %s", self.username, exc)
            return FetchResult(cursor=cursor)

        if not messages:
            return FetchResult(cursor=cursor)

        messages.reverse()  # iter_messages отдаёт от новых к старым
        all_ids = [int(getattr(m, "id", 0) or 0) for m in messages]
        new_cursor = str(max(all_ids)) if all_ids else cursor
        download_video = settings.media_download == "all"

        items: list[RawItem] = []
        for group in group_by_album(messages):
            media_refs = await self._group_media(tg, group, download_video)
            item = raw_item_from_group(group, self.username, self.lang, media_refs)
            if item.text or item.media:
                items.append(item)

        return FetchResult(items=items, cursor=new_cursor)
