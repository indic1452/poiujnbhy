import datetime as dt
from types import SimpleNamespace

from app.sources.base import MediaRef
from app.sources.telegram_client import group_by_album, raw_item_from_group


def _msg(mid, gid=None, text="", date=None, forward=None):
    return SimpleNamespace(id=mid, grouped_id=gid, message=text, date=date, forward=forward)


def test_group_by_album_groups_consecutive_same_gid():
    msgs = [
        _msg(10, text="одиночное"),
        _msg(11, gid=5, text="подпись альбома"),
        _msg(12, gid=5),
        _msg(13, text="ещё одиночное"),
    ]
    groups = group_by_album(msgs)
    assert [len(g) for g in groups] == [1, 2, 1]


def test_raw_item_uses_caption_and_min_id_anchor():
    grp = [_msg(11, gid=5, text=""), _msg(12, gid=5, text="Подпись альбома")]
    item = raw_item_from_group(grp, "chan", "ru", [MediaRef(type="image", data=b"x")])
    assert item.text == "Подпись альбома"
    assert item.external_id == "chan/11"
    assert item.url.endswith("/11")
    assert item.lang == "ru"
    assert len(item.media) == 1


def test_raw_item_forwarded_prefix():
    fwd = SimpleNamespace(chat=SimpleNamespace(title="Канал X", username=None))
    item = raw_item_from_group([_msg(20, text="текст", forward=fwd)], "chan", "ru", [])
    assert item.text.startswith("[переслано из Канал X]")


def test_raw_item_naive_datetime_gets_utc():
    item = raw_item_from_group(
        [_msg(30, text="t", date=dt.datetime(2026, 7, 14, 8, 0))], "c", "en", []
    )
    assert item.published_at is not None and item.published_at.tzinfo is not None
    assert item.lang == "en"
