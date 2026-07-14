"""Фабрика vision-провайдера (анализ изображений/кадров видео)."""
from __future__ import annotations

from ..config import settings
from .base import VisionProvider
from .mock_vision import MockVision

__all__ = ["VisionProvider", "MockVision", "get_vision"]


def get_vision(backend: str | None = None) -> VisionProvider:
    backend = (backend or settings.vision_backend).lower()
    if backend == "ollama":
        from .ollama_vision import OllamaVision

        return OllamaVision()
    return MockVision()
