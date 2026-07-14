"""Опциональный провайдер: перевод-only через NLLB-200 (HuggingFace transformers).

Даёт качественный дословный перевод на русский, но НЕ обобщает — суммаризация
делается экстрактивно (первые предложения). Используйте, когда важна точность
перевода без парафраза модели. Требует `pip install transformers sentencepiece`.

Загружается лениво, чтобы тяжёлые зависимости не тянулись без необходимости.
"""
from __future__ import annotations

from ..categories import DEFAULT_CATEGORY
from .base import ClusterSummary, ItemSummary, SourceDoc, SummarizerProvider
from .mock_provider import _first_sentences, _guess_category


class NllbSummarizer(SummarizerProvider):
    name = "nllb"

    _MODEL = "facebook/nllb-200-distilled-1.3B"
    _LANG_MAP = {"ru": "rus_Cyrl", "en": "eng_Latn", "uk": "ukr_Cyrl"}

    def __init__(self) -> None:
        self._pipe = None

    def _ensure(self):
        if self._pipe is None:
            from transformers import pipeline  # type: ignore

            self._pipe = pipeline("translation", model=self._MODEL)
        return self._pipe

    def _to_ru(self, text: str, lang: str) -> str:
        text = (text or "").strip()
        if not text or lang == "ru":
            return text
        pipe = self._ensure()
        src = self._LANG_MAP.get(lang, "eng_Latn")
        chunk = text[:1000]
        out = pipe(chunk, src_lang=src, tgt_lang="rus_Cyrl")
        return out[0]["translation_text"]

    async def summarize_item(self, title: str, text: str, lang: str = "ru") -> ItemSummary:
        title_ru = self._to_ru(title, lang)
        body_ru = self._to_ru(_first_sentences(text, n=3), lang)
        return ItemSummary(
            title_ru=title_ru,
            summary_ru=_first_sentences(body_ru, n=2),
            category=_guess_category(f"{title} {text}"),
            key_points=[p for p in body_ru.split(". ") if p][:3],
        )

    async def summarize_cluster(
        self, docs: list[SourceDoc], vision_notes: list[str] | None = None
    ) -> ClusterSummary:
        parts = [self._to_ru(f"{d.title}. {_first_sentences(d.text, 2)}", d.lang) for d in docs]
        digest = _first_sentences(" ".join(parts), n=4, limit=500)
        if vision_notes:
            digest = f"{digest} На медиа: {vision_notes[0]}"
        return ClusterSummary(
            headline_ru=parts[0].split(".")[0] if parts else "Сюжет",
            digest_ru=digest,
            category=_guess_category(" ".join(d.title + " " + d.text for d in docs)),
            key_points=[p for p in parts][:4],
        )
