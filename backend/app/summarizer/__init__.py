"""Фабрика провайдера суммаризации/перевода."""
from __future__ import annotations

from ..config import settings
from .base import ClusterSummary, ItemSummary, SummarizerProvider
from .mock_provider import MockSummarizer

__all__ = [
    "ClusterSummary",
    "ItemSummary",
    "SummarizerProvider",
    "MockSummarizer",
    "get_summarizer",
]


def get_summarizer(backend: str | None = None) -> SummarizerProvider:
    backend = (backend or settings.summarizer_backend).lower()
    if backend == "ollama":
        from .ollama_provider import OllamaSummarizer

        return OllamaSummarizer()
    if backend == "nllb":
        from .nllb_provider import NllbSummarizer

        return NllbSummarizer()
    return MockSummarizer()
