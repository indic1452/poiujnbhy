import httpx

from app.sources.gnews import GoogleNewsSource


def test_build_url_encodes_query():
    url = GoogleNewsSource.build_url("война Украина", hl="ru", gl="RU", ceid="RU:ru")
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "hl=ru" in url and "ceid=RU:ru" in url


async def test_gnews_feed_parses():
    async with httpx.AsyncClient() as c:
        src = GoogleNewsSource("GN", "https://news.google.com/x", "en", fixture="tests/fixtures/gnews.xml")
        res = await src.fetch(c)
    assert len(res.items) == 1
    item = res.items[0]
    assert "reuters" in item.title.lower()
    assert item.url and "news.google.com" in item.url
    assert src.type == "gnews"
