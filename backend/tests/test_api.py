from httpx import ASGITransport, AsyncClient

from app.db import get_sessionmaker
from app.main import app
from app.pipeline.ingest import run_ingest
from app.sources.loader import seed_sources


async def _seed_and_ingest(session):
    await seed_sources(session)
    stats = await run_ingest(get_sessionmaker())
    return stats


async def test_ingest_produces_feed_with_media(session):
    stats = await _seed_and_ingest(session)
    assert stats.new_items > 0
    assert stats.new_clusters > 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/feed")
        assert r.status_code == 200
        data = r.json()

    assert data["total"] >= 1
    clusters = data["clusters"]
    assert clusters
    # русские заголовки и сводки заполнены
    assert all(c["headline_ru"] for c in clusters)
    # хотя бы у одного сюжета есть сопутствующее медиа
    assert any(c["primary_media"] for c in clusters)
    # где-то есть проанализированное медиа (vision mock отработал)
    analyzed = [
        m
        for c in clusters
        for it in c["items"]
        for m in it["media"]
        if m.get("analysis_ru")
    ]
    assert analyzed
    # где-то есть видео (из Telegram)
    videos = [
        m for c in clusters for it in c["items"] for m in it["media"] if m["type"] == "video"
    ]
    assert videos
    assert videos[0]["video_url"]


async def test_english_items_translated_prefix(session):
    await _seed_and_ingest(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        data = (await ac.get("/api/feed?limit=100")).json()
    titles = [it["title_ru"] for c in data["clusters"] for it in c["items"] if it["title_ru"]]
    assert any(t.startswith("[перевод]") for t in titles)


async def test_status_and_sources_endpoints(session):
    await seed_sources(session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        st = (await ac.get("/api/status")).json()
        assert st["source_mode"] == "fixtures"
        assert st["summarizer_backend"] == "mock"
        assert st["summarizer_available"] is True
        assert st["sources"] > 0

        srcs = (await ac.get("/api/sources")).json()
        assert len(srcs) > 0
        sid = srcs[0]["id"]
        upd = await ac.post(f"/api/sources/{sid}", json={"enabled": False})
        assert upd.status_code == 200
        assert upd.json()["enabled"] is False
