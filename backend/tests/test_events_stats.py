import csv
import io
import json

from httpx import ASGITransport, AsyncClient

from app.db import get_sessionmaker
from app.main import app
from app.pipeline.ingest import run_ingest
from app.sources.loader import seed_sources


async def _seed_ingest(session):
    await seed_sources(session)
    await run_ingest(get_sessionmaker())


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_events_geojson_has_located_points(session):
    await _seed_ingest(session)
    async with await _client() as ac:
        r = await ac.get("/api/events.geojson")
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) >= 2
    f = fc["features"][0]
    lon, lat = f["geometry"]["coordinates"]
    assert -180 <= lon <= 180 and -90 <= lat <= 90  # порядок lon,lat
    props = f["properties"]
    assert props["event_type"]
    assert props["place_name"]
    assert props["marker-color"].startswith("#")
    # где-то должен быть удар по объекту (Афипский НПЗ)
    names = [ft["properties"]["place_name"] for ft in fc["features"]]
    assert any("Афипский" in (n or "") for n in names)


async def test_events_filter_by_type(session):
    await _seed_ingest(session)
    async with await _client() as ac:
        drones = (await ac.get("/api/events.geojson?event_type=удар_дрон")).json()
    assert all(f["properties"]["event_type"] == "удар_дрон" for f in drones["features"])
    assert len(drones["features"]) >= 1


async def test_stats_grouping(session):
    await _seed_ingest(session)
    async with await _client() as ac:
        st = (await ac.get("/api/stats")).json()
    assert st["total"] >= 1
    assert st["geolocated"] >= 2
    assert any(x["key"] for x in st["by_region"])
    assert any(x["count"] > 0 for x in st["by_type"])
    assert isinstance(st["by_day"], list)


async def test_export_formats(session):
    await _seed_ingest(session)
    async with await _client() as ac:
        gj = await ac.get("/api/export?format=geojson")
        cs = await ac.get("/api/export?format=csv")
        js = await ac.get("/api/export?format=json")

    assert "geo+json" in gj.headers["content-type"]
    assert json.loads(gj.text)["type"] == "FeatureCollection"

    assert "attachment" in cs.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(cs.text.lstrip("﻿"))))
    assert rows[0][:3] == ["id", "time", "event_type"]
    assert len(rows) >= 2

    data = json.loads(js.text)
    assert isinstance(data, list) and data and "lat" in data[0]
