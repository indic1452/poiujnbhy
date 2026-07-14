import httpx

from app.sources.telegram_web import TelegramWebSource


async def _fetch(cursor=None):
    async with httpx.AsyncClient() as c:
        src = TelegramWebSource(
            "TG", "example_war_channel", "ru", fixture="tests/fixtures/tme_channel.html"
        )
        return await src.fetch(c, cursor=cursor)


async def test_parses_messages_photo_and_video():
    res = await _fetch()
    assert len(res.items) == 3
    assert res.cursor == "1052"

    videos = [i for i in res.items if any(m.type == "video" for m in i.media)]
    assert videos, "должно быть видео-сообщение"
    v = videos[0].media[0]
    assert v.video_url and v.video_url.endswith("strike.mp4")
    assert v.duration == 42
    assert v.url.endswith("strike_thumb.jpg")  # постер

    photos = [i for i in res.items if any(m.type == "image" for m in i.media)]
    assert photos


async def test_cursor_filters_old_messages():
    res = await _fetch(cursor="1050")
    ids = {i.external_id.split("/")[-1] for i in res.items}
    assert ids == {"1051", "1052"}
    assert res.cursor == "1052"
