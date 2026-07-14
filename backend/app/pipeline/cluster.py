"""Кластеризация «сюжетов»: near-dup сопоставление и выбор главного медиа."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    """Нормализовать заголовок/текст для сравнения."""
    text = (text or "").lower()
    text = _WORD_RE.sub(" ", text)
    return " ".join(text.split())


def similarity(a: str, b: str) -> float:
    """Похожесть двух текстов, 0..100 (rapidfuzz token_set_ratio)."""
    return fuzz.token_set_ratio(normalize(a), normalize(b))


def best_match(
    candidate: str, existing: list[tuple[int, str]], threshold: float
) -> int | None:
    """Найти id наиболее похожего существующего сюжета выше порога, иначе None."""
    best_id: int | None = None
    best_score = threshold
    for cid, text in existing:
        s = similarity(candidate, text)
        if s >= best_score:
            best_score = s
            best_id = cid
    return best_id


def _media_rank(media) -> tuple:
    """Ключ сортировки: сначала видео с постером, затем фото с файлом."""
    has_file = bool(getattr(media, "local_path", None) or getattr(media, "poster_path", None))
    is_video = media.type == "video"
    area = (media.width or 0) * (media.height or 0)
    return (is_video, has_file, area)


def pick_primary_media(media_list: list):
    """Выбрать главное медиа сюжета: приоритет видео → фото, с локальным файлом."""
    usable = [m for m in media_list if getattr(m, "local_path", None)
              or getattr(m, "poster_path", None) or m.source_url or m.video_url]
    if not usable:
        return None
    return max(usable, key=_media_rank)
