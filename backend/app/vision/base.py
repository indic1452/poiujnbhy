"""Интерфейс vision-провайдера: анализ изображения/кадра на русском."""
from __future__ import annotations

import abc


class VisionProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def analyze_image(
        self, image_bytes: bytes, mime: str = "image/jpeg", context: str = ""
    ) -> str:
        """Вернуть русскоязычное описание/анализ изображения.

        ``context`` — заголовок/текст новости, чтобы модель понимала обстановку.
        """
        raise NotImplementedError

    async def health(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None
