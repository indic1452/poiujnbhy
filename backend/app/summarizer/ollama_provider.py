"""Провайдер суммаризации/перевода через локальную модель Ollama.

Использует /api/chat со структурированным выводом (format=JSON-схема) и
temperature=0 для детерминированности. Требует запущенного `ollama serve`
и загруженной модели (`ollama pull qwen3:8b`).
"""
from __future__ import annotations

import json

import httpx

from ..categories import CATEGORIES, DEFAULT_CATEGORY
from ..config import settings
from .base import ClusterSummary, ItemSummary, SourceDoc, SummarizerProvider

_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "title_ru": {"type": "string"},
        "summary_ru": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title_ru", "summary_ru", "category"],
}

_CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "headline_ru": {"type": "string"},
        "digest_ru": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline_ru", "digest_ru", "category"],
}

_SYS_ITEM = (
    "Ты — редактор новостной ленты о военных событиях (Украина, Россия, западная "
    "коалиция). Кратко и нейтрально изложи материал НА РУССКОМ ЯЗЫКЕ. Если исходный "
    "текст не на русском — переведи. Верни строго JSON по схеме: title_ru (краткий "
    "заголовок), summary_ru (2–3 предложения), category (одна из списка), key_points "
    "(до 3 пунктов). Без выдумок — только то, что есть в тексте."
)

_SYS_CLUSTER = (
    "Ты — редактор новостной ленты о военных событиях. Тебе дан набор сообщений из "
    "разных источников об ОДНОМ событии (возможно на разных языках). Сведи их в единую "
    "сводку НА РУССКОМ ЯЗЫКЕ, без повторов и без выдумок. Верни строго JSON: headline_ru "
    "(заголовок сюжета), digest_ru (связная сводка 3–5 предложений), category (одна из "
    "списка), key_points (ключевые факты списком). Если приведены заметки по медиа — учти их."
)


class OllamaSummarizer(SummarizerProvider):
    name = "ollama"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url, timeout=httpx.Timeout(120.0)
        )
        self._model = settings.model

    async def _chat(self, system: str, user: str, schema: dict) -> dict:
        resp = await self._client.post(
            "/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "format": schema,
                "options": {"temperature": 0},
                "keep_alive": "30m",
            },
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)

    async def summarize_item(self, title: str, text: str, lang: str = "ru") -> ItemSummary:
        user = f"Заголовок: {title}\n\nТекст:\n{text[:6000]}"
        data = await self._chat(_SYS_ITEM, user, _ITEM_SCHEMA)
        return ItemSummary(
            title_ru=data.get("title_ru") or title,
            summary_ru=data.get("summary_ru") or "",
            category=data.get("category") or DEFAULT_CATEGORY,
            key_points=list(data.get("key_points") or []),
        )

    async def summarize_cluster(
        self, docs: list[SourceDoc], vision_notes: list[str] | None = None
    ) -> ClusterSummary:
        blocks = []
        for i, d in enumerate(docs, 1):
            src = f" [{d.source_name}]" if d.source_name else ""
            blocks.append(f"Источник {i}{src} (яз. {d.lang}):\n{d.title}\n{d.text[:3000]}")
        user = "\n\n".join(blocks)
        if vision_notes:
            user += "\n\nЗаметки по медиа:\n" + "\n".join(f"- {n}" for n in vision_notes if n)
        data = await self._chat(_SYS_CLUSTER, user, _CLUSTER_SCHEMA)
        return ClusterSummary(
            headline_ru=data.get("headline_ru") or (docs[0].title if docs else ""),
            digest_ru=data.get("digest_ru") or "",
            category=data.get("category") or DEFAULT_CATEGORY,
            key_points=list(data.get("key_points") or []),
        )

    async def health(self) -> bool:
        try:
            r = await self._client.get("/api/tags", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
