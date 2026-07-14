"""Анализ изображений/кадров локальной vision-моделью через Ollama.

Отправляет изображение (base64) в /api/chat вместе с русским промптом.
Модель по умолчанию — qwen2.5vl:7b (мультиязычная, хорошая OCR).
"""
from __future__ import annotations

import base64

import httpx

from ..config import settings
from .base import VisionProvider

_SYS = (
    "Ты анализируешь фото или кадр видео из новостей о военных событиях. Опиши НА "
    "РУССКОМ ЯЗЫКЕ, что изображено (объекты, техника, местность, разрушения). Если на "
    "изображении есть текст, карта или инфографика — распознай и передай ключевые данные "
    "(OCR). Не выдумывай того, чего не видно. 2–4 предложения."
)


class OllamaVision(VisionProvider):
    name = "ollama"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_url, timeout=httpx.Timeout(120.0)
        )
        self._model = settings.vision_model

    async def analyze_image(
        self, image_bytes: bytes, mime: str = "image/jpeg", context: str = ""
    ) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        user = "Проанализируй изображение."
        if context:
            user += f" Контекст новости: {context[:400]}"
        resp = await self._client.post(
            "/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": _SYS},
                    {"role": "user", "content": user, "images": [b64]},
                ],
                "stream": False,
                "options": {"temperature": 0},
                "keep_alive": "30m",
            },
        )
        resp.raise_for_status()
        return (resp.json()["message"]["content"] or "").strip()

    async def health(self) -> bool:
        try:
            r = await self._client.get("/api/tags", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
