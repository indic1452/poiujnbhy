"""Источник Google News RSS-поиск.

Служит «обходом» для лент, у которых нет своего RSS (Reuters, AP) и для
тематических запросов на русском. По сути это RSS, но ссылки ведут на редирект
Google News (`news.google.com/rss/articles/...`) — финальный URL разрешается
позже, на этапе извлечения статьи (см. sources/extract.py).
"""
from __future__ import annotations

from .rss import RssSource


class GoogleNewsSource(RssSource):
    type = "gnews"

    @staticmethod
    def build_url(query: str, hl: str = "ru", gl: str = "RU", ceid: str = "RU:ru") -> str:
        from urllib.parse import quote

        return (
            f"https://news.google.com/rss/search?q={quote(query)}"
            f"&hl={hl}&gl={gl}&ceid={ceid}"
        )
