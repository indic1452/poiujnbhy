"""Конфигурация приложения (pydantic-settings, читает .env)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # База данных
    database_url: str = "postgresql+asyncpg://newsuser:newspass@localhost:5432/newsdb"

    # Источники
    source_mode: str = "live"  # live | fixtures
    sources_file: str = "sources.yaml"

    # Текстовая модель (обобщение + перевод)
    summarizer_backend: str = "ollama"  # ollama | mock | nllb
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3:8b"

    # Vision-модель (анализ медиа)
    vision_backend: str = "ollama"  # ollama | mock
    vision_model: str = "qwen2.5vl:7b"

    # Медиа
    media_download: str = "image"  # image | all | off
    media_dir: str = "media"
    max_media_bytes: int = 15_000_000

    # Планировщик
    poll_interval_seconds: int = 600
    ingest_on_start: bool = False

    # Создавать таблицы при старте (для быстрого демо без alembic)
    auto_create_tables: bool = False

    # Кластеризация
    cluster_similarity: int = 82
    cluster_window_hours: int = 48

    # Геокодирование (карта событий)
    geocoder_enabled: bool = True
    geo_confidence_floor: float = 0.5
    gazetteer_full_csv: str | None = None  # опц. полный газеттир (build_gazetteer.py)
    map_tile_attribution: str = "© OpenStreetMap contributors, GeoNames (CC BY 4.0)"

    # Telegram (опционально)
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str = "tg.session"

    # HTTP
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
    request_timeout: float = 20.0

    # CORS
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def media_path(self) -> Path:
        p = Path(self.media_dir)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        return p

    @property
    def sources_path(self) -> Path:
        p = Path(self.sources_file)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
