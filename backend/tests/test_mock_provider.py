from app.summarizer.base import SourceDoc
from app.summarizer.mock_provider import MockSummarizer
from app.vision.mock_vision import MockVision


async def test_mock_item_translates_prefix_for_non_russian():
    s = MockSummarizer()
    res = await s.summarize_item("Strike on energy grid", "Power stations were hit.", "en")
    assert res.title_ru.startswith("[перевод]")
    assert res.summary_ru
    assert res.category


async def test_mock_item_russian_no_prefix():
    s = MockSummarizer()
    res = await s.summarize_item("Удар по энергетике", "Повреждены подстанции.", "ru")
    assert not res.title_ru.startswith("[перевод]")


async def test_mock_cluster_digest():
    s = MockSummarizer()
    docs = [
        SourceDoc("Удар по энергетике", "Повреждены подстанции региона.", "ru", "РИА"),
        SourceDoc("Атака на энергосистему", "Введены аварийные отключения.", "ru", "BBC"),
    ]
    res = await s.summarize_cluster(docs, vision_notes=["видны разрушения подстанции"])
    assert res.digest_ru
    assert "медиа" in res.digest_ru.lower()
    assert res.headline_ru


async def test_mock_vision():
    v = MockVision()
    note = await v.analyze_image(b"\xff\xd8\xff" + b"0" * 2048, "image/jpeg", "удар по подстанции")
    assert "mock" in note.lower()
    assert await v.health() is True
