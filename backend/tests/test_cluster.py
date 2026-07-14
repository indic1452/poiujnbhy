from types import SimpleNamespace

from app.pipeline import cluster


def test_similarity_high_for_near_duplicates():
    a = "Массированный удар по объектам энергетики Украины"
    b = "Удар по объектам энергетики Украины: массированная атака"
    assert cluster.similarity(a, b) >= 70


def test_best_match_selects_above_threshold():
    existing = [
        (1, "переговоры о перемирии и обмене"),
        (2, "удар по объектам энергетики украины"),
    ]
    cid = cluster.best_match("массированный удар по энергетике украины", existing, 60)
    assert cid == 2


def test_best_match_returns_none_below_threshold():
    existing = [(1, "прогноз погоды в москве")]
    assert cluster.best_match("санкции против банков", existing, 60) is None


def _m(**kw):
    base = dict(
        type="image", local_path=None, poster_path=None, source_url="",
        video_url=None, width=0, height=0, id=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_pick_primary_prefers_video_with_file():
    img = _m(type="image", local_path="/media/a.jpg", id=1)
    vid = _m(type="video", poster_path="/media/b.jpg", video_url="http://v.mp4", id=2)
    assert cluster.pick_primary_media([img, vid]).id == 2


def test_pick_primary_none_when_empty():
    assert cluster.pick_primary_media([]) is None
