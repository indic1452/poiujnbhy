import httpx

from app.config import BACKEND_DIR, settings
from app.models import Item
from app.sources.base import MediaRef
from app.sources.media import store_media

_SAMPLE = (BACKEND_DIR / "tests" / "fixtures" / "sample_photo.jpg").read_bytes()


async def test_store_image_downloads_from_fixture():
    item = Item(source_id=1, external_id="x/1", orig_title="t", orig_text="")
    refs = [MediaRef(type="image", url="https://example/photo.jpg")]
    async with httpx.AsyncClient() as c:
        media = await store_media(c, item, refs)
    assert len(media) == 1
    m = media[0]
    assert m.type == "image"
    assert m.local_path and m.local_path.startswith("/media/")
    assert (settings.media_path / m.local_path.split("/")[-1]).exists()


async def test_store_video_saves_poster_keeps_url():
    item = Item(source_id=1, external_id="x/2", orig_title="t", orig_text="")
    refs = [
        MediaRef(
            type="video",
            url="https://example/thumb.jpg",
            video_url="https://example/clip.mp4",
            duration=42,
        )
    ]
    async with httpx.AsyncClient() as c:
        media = await store_media(c, item, refs)
    m = media[0]
    assert m.type == "video"
    assert m.poster_path and m.poster_path.startswith("/media/")
    assert m.video_url == "https://example/clip.mp4"
    # в режиме image сам видеофайл не качаем
    assert settings.media_download == "image"
    assert m.local_path is None


async def test_store_image_from_bytes_no_network():
    # Telethon-путь: готовые байты, без сетевой загрузки
    item = Item(source_id=1, external_id="x/9", orig_title="t", orig_text="")
    refs = [MediaRef(type="image", data=_SAMPLE)]
    async with httpx.AsyncClient() as c:
        media = await store_media(c, item, refs)
    m = media[0]
    assert m.type == "image"
    assert m.local_path and m.local_path.startswith("/media/")
    assert (settings.media_path / m.local_path.split("/")[-1]).exists()


async def test_store_video_from_bytes_poster():
    item = Item(source_id=1, external_id="x/10", orig_title="t", orig_text="")
    refs = [MediaRef(type="video", poster_data=_SAMPLE, video_url="https://x/v.mp4", duration=12)]
    async with httpx.AsyncClient() as c:
        media = await store_media(c, item, refs)
    m = media[0]
    assert m.type == "video"
    assert m.poster_path and m.poster_path.startswith("/media/")
    assert m.duration == 12
