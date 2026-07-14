"""Общие фикстуры: временный Postgres, схема, изоляция, оффлайн-режим."""
from __future__ import annotations

import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.pool import NullPool

# Оффлайн-режим ДО импорта app-модулей, использующих settings
from app.config import settings  # noqa: E402

settings.source_mode = "fixtures"
settings.summarizer_backend = "mock"
settings.vision_backend = "mock"
settings.media_download = "image"
settings.auto_create_tables = False
settings.media_dir = tempfile.mkdtemp(prefix="media_")

from app import db  # noqa: E402

# NullPool: не переиспользовать asyncpg-соединения между event loop'ами тестов
db.configure_engine(poolclass=NullPool)

from tests.pgserver import LocalPostgres  # noqa: E402


@pytest.fixture(scope="session")
def _pg():
    pg = LocalPostgres()
    settings.database_url = pg.start()
    yield pg
    pg.stop()


@pytest_asyncio.fixture(autouse=True)
async def _db(_pg):
    await db.create_all()
    async with db.get_engine().begin() as conn:
        await conn.execute(
            text("TRUNCATE media, items, clusters, sources RESTART IDENTITY CASCADE")
        )
    yield


@pytest_asyncio.fixture
async def session(_db):
    async with db.get_sessionmaker()() as s:
        yield s
