"""Интерфейс провайдера суммаризации + перевода на русский."""
from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class ItemSummary:
    """Результат обработки одного материала."""

    title_ru: str
    summary_ru: str
    category: str
    key_points: list[str] = field(default_factory=list)
    event_type: str = "прочее"
    locations: list[dict] = field(default_factory=list)


@dataclass
class ClusterSummary:
    """Результат обобщения нескольких материалов об одном событии."""

    headline_ru: str
    digest_ru: str
    category: str
    key_points: list[str] = field(default_factory=list)
    event_type: str = "прочее"
    locations: list[dict] = field(default_factory=list)


@dataclass
class SourceDoc:
    """Вход для обобщения кластера: заголовок/текст одного источника."""

    title: str
    text: str
    lang: str = "ru"
    source_name: str = ""


class SummarizerProvider(abc.ABC):
    """Локальная модель: переводит на русский и обобщает."""

    name: str = "base"

    @abc.abstractmethod
    async def summarize_item(self, title: str, text: str, lang: str = "ru") -> ItemSummary:
        """Перевести (если нужно) и кратко изложить один материал на русском."""
        raise NotImplementedError

    @abc.abstractmethod
    async def summarize_cluster(
        self, docs: list[SourceDoc], vision_notes: list[str] | None = None
    ) -> ClusterSummary:
        """Свести несколько материалов об одном событии в единую русскую сводку."""
        raise NotImplementedError

    async def health(self) -> bool:
        """Доступна ли модель. По умолчанию — да (для mock)."""
        return True

    async def aclose(self) -> None:
        """Освободить ресурсы (соединения)."""
        return None
