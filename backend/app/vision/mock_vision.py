"""Детерминированный mock vision-провайдера (оффлайн-тесты и деградация)."""
from __future__ import annotations

from .base import VisionProvider


class MockVision(VisionProvider):
    name = "mock"

    async def analyze_image(
        self, image_bytes: bytes, mime: str = "image/jpeg", context: str = ""
    ) -> str:
        size_kb = max(1, len(image_bytes) // 1024)
        hint = (context or "").strip()
        hint = (hint[:80] + "…") if len(hint) > 80 else hint
        base = f"[анализ отключён — mock] изображение {size_kb} КБ, тип {mime}."
        return f"{base} Контекст: {hint}" if hint else base
