"""Фильтр релевантности темы (военные события / Украина / Россия / коалиция).

Дешёвый префильтр по ключевым словам до вызова модели — экономит ресурсы.
"""
from __future__ import annotations

from ..categories import TOPIC_KEYWORDS

_KEYWORDS = [k.lower() for k in TOPIC_KEYWORDS]


def score(title: str, text: str) -> float:
    """Доля релевантности 0..1 по числу уникальных совпадений ключевых слов."""
    blob = f"{title}\n{text}".lower()
    hits = sum(1 for kw in _KEYWORDS if kw in blob)
    return min(1.0, hits / 3.0)


def is_relevant(title: str, text: str, threshold: float = 0.001) -> tuple[bool, float]:
    s = score(title, text)
    return s >= threshold, s
