"""Асинхронное подключение к PostgreSQL (SQLAlchemy 2.0 + asyncpg)."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_engine_kwargs: dict = {}


def configure_engine(**kwargs) -> None:
    """Задать доп. параметры движка (напр. poolclass=NullPool в тестах)."""
    global _engine_kwargs, _engine, _sessionmaker
    _engine_kwargs = kwargs
    _engine = None
    _sessionmaker = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url, pool_pre_ping=True, **_engine_kwargs
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: выдаёт сессию на запрос."""
    async with get_sessionmaker()() as session:
        yield session


async def create_all() -> None:
    """Создать схему напрямую (используется в тестах вместо alembic)."""
    from . import models  # noqa: F401  (регистрация моделей)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
