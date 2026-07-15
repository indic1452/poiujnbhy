"""Скачивание и хранение медиа: фото и постеры/файлы видео.

Режим MEDIA_DOWNLOAD:
  image — качать фото и постеры видео (по умолчанию)
  all   — качать ещё и сами видеофайлы
  off   — ничего не качать, хранить только ссылки

В режиме SOURCE_MODE=fixtures вместо сети используются локальные образцы
(tests/fixtures/sample_photo.jpg, sample_video.mp4), чтобы демо показывало медиа.
"""
from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from ..config import BACKEND_DIR, settings
from ..models import Media
from .base import MediaRef
from .fetch import browser_headers

_SAMPLE_PHOTO = BACKEND_DIR / "tests" / "fixtures" / "sample_photo.jpg"
_SAMPLE_VIDEO = BACKEND_DIR / "tests" / "fixtures" / "sample_video.mp4"


def ensure_media_dir() -> Path:
    p = settings.media_path
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _download(client: httpx.AsyncClient, url: str) -> bytes | None:
    if not url:
        return None
    try:
        async with client.stream(
            "GET", url, headers=browser_headers(), follow_redirects=True
        ) as resp:
            resp.raise_for_status()
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > settings.max_media_bytes:
                    return None
            return bytes(buf)
    except httpx.HTTPError:
        return None


def _save_image(data: bytes) -> tuple[str, int, int] | None:
    """Сжать до тумбнейла и сохранить как JPEG. Вернуть (web_path, w, h)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        return None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((1600, 1600))
    digest = hashlib.sha256(data).hexdigest()[:24]
    fname = f"{digest}.jpg"
    out = ensure_media_dir() / fname
    if not out.exists():
        img.save(out, format="JPEG", quality=85)
    return f"/media/{fname}", img.width, img.height


def _save_blob(data: bytes, ext: str) -> str:
    digest = hashlib.sha256(data).hexdigest()[:24]
    fname = f"{digest}{ext}"
    out = ensure_media_dir() / fname
    if not out.exists():
        out.write_bytes(data)
    return f"/media/{fname}"


def _fixture_bytes(kind: str) -> bytes | None:
    src = _SAMPLE_PHOTO if kind == "image" else _SAMPLE_VIDEO
    return src.read_bytes() if src.exists() else None


async def store_media(
    client: httpx.AsyncClient, item, refs: list[MediaRef], limit: int = 4
) -> list[Media]:
    """Создать записи Media для материала (файлы — по режиму MEDIA_DOWNLOAD)."""
    mode = settings.media_download
    fixtures = settings.source_mode == "fixtures"
    result: list[Media] = []

    for ref in refs[:limit]:
        media = Media(
            type=ref.type,
            source_url=ref.url or ref.video_url or "",
            video_url=ref.video_url,
            duration=ref.duration,
            width=ref.width,
            height=ref.height,
            mime=ref.mime,
        )

        if mode != "off":
            if ref.type == "image":
                if ref.data is not None:  # готовые байты (Telethon)
                    data = ref.data
                elif fixtures:
                    data = _fixture_bytes("image")
                else:
                    data = await _download(client, ref.url)
                if data:
                    saved = _save_image(data)
                    if saved:
                        media.local_path, media.width, media.height = saved
                        media.content_hash = hashlib.sha256(data).hexdigest()[:24]
            else:  # video
                if ref.poster_data is not None:  # постер уже скачан (Telethon)
                    pdata = ref.poster_data
                elif fixtures:
                    pdata = _fixture_bytes("image")
                else:
                    pdata = await _download(client, ref.url)
                if pdata:
                    saved = _save_image(pdata)
                    if saved:
                        media.poster_path = saved[0]
                if mode == "all":
                    if ref.data is not None:  # видеофайл уже скачан (Telethon)
                        vdata = ref.data
                    elif fixtures:
                        vdata = _fixture_bytes("video")
                    elif ref.video_url:
                        vdata = await _download(client, ref.video_url)
                    else:
                        vdata = None
                    if vdata:
                        media.local_path = _save_blob(vdata, ".mp4")

        media.item_id = item.id  # FK напрямую, без ленивой загрузки item.media
        result.append(media)
    return result
