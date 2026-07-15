import httpx

from app.sources.rss import RssSource


async def test_ria_feed_parses_items_and_media():
    async with httpx.AsyncClient() as c:
        src = RssSource("РИА", "https://ria.ru/x", "ru", fixture="tests/fixtures/rss_ria.xml")
        res = await src.fetch(c)
    assert len(res.items) == 4
    energy = res.items[0]
    assert "энергетик" in energy.title.lower()
    assert energy.media and energy.media[0].type == "image"
    assert energy.media[0].url.endswith(".jpg")
    assert energy.published_at is not None


async def test_bbc_feed_media_thumbnail():
    async with httpx.AsyncClient() as c:
        src = RssSource("BBC", "https://bbc/x", "en", fixture="tests/fixtures/rss_bbc.xml")
        res = await src.fetch(c)
    assert len(res.items) == 2
    assert all(i.lang == "en" for i in res.items)
    assert res.items[0].media and res.items[0].media[0].type == "image"
