import httpx

import app.sources.telegram_web as tgw
from app.sources.fetch import Content
from app.sources.telegram_web import TelegramWebSource, parse_page


def _msg_html(mid: int, text: str, extra: str = "") -> str:
    return (
        f'<div class="tgme_widget_message" data-post="chan/{mid}">'
        f'<div class="tgme_widget_message_text">{text}</div>{extra}'
        f'<div class="tgme_widget_message_date">'
        f'<time datetime="2026-07-14T08:00:0{mid % 10}+00:00"></time></div></div>'
    )


def _page(pairs: list[tuple[int, str]]) -> str:
    return "<html><body>" + "".join(_msg_html(i, t) for i, t in pairs) + "</body></html>"


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


def test_parse_page_uses_channel_language():
    items = parse_page(_page([(1, "Strike on Kharkiv")]), "chan", "en")
    assert items and items[0].lang == "en"


def test_parse_page_album_multiple_photos():
    extra = (
        "<a class=\"tgme_widget_message_photo_wrap\" "
        "style=\"background-image:url('https://c/1.jpg')\"></a>"
        "<a class=\"tgme_widget_message_photo_wrap\" "
        "style=\"background-image:url('https://c/2.jpg')\"></a>"
    )
    html = (
        '<div class="tgme_widget_message" data-post="chan/50">'
        '<div class="tgme_widget_message_text">Альбом</div>' + extra + "</div>"
    )
    items = parse_page(html, "chan", "ru")
    assert len(items) == 1
    assert len([m for m in items[0].media if m.type == "image"]) == 2


def test_parse_page_forwarded_prefix():
    extra = '<div class="tgme_widget_message_forwarded_from_name">Канал Y</div>'
    html = (
        '<div class="tgme_widget_message" data-post="chan/60">'
        '<div class="tgme_widget_message_text">привет</div>' + extra + "</div>"
    )
    items = parse_page(html, "chan", "ru")
    assert items[0].text.startswith("[переслано из Канал Y]")


async def test_multipage_pagination_merges_pages(monkeypatch):
    pages = {
        None: _page([(202, "удар a"), (201, "обстрел b"), (200, "c")]),
        "200": _page([(199, "d"), (198, "e"), (197, "f")]),
        "197": "",
    }

    async def fake_get_content(client, url, *, fixture=None, **kw):
        before = url.split("before=")[-1] if "before=" in url else None
        return Content(status=200, text=pages.get(before, ""))

    monkeypatch.setattr(tgw, "get_content", fake_get_content)

    src = TelegramWebSource("t", "chan", "ru")
    async with httpx.AsyncClient() as c:
        res = await src.fetch(c, cursor="196")
    ids = sorted(int(i.external_id.split("/")[-1]) for i in res.items)
    assert ids == [197, 198, 199, 200, 201, 202]
    assert res.cursor == "202"
