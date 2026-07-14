"""Детерминированный mock суммаризатора — для оффлайн-тестов и деградации.

Не обращается к сети. Даёт предсказуемый русскоязычный вывод, чтобы можно
было прогнать весь конвейер и фронтенд без модели и без интернета.
"""
from __future__ import annotations

from ..categories import DEFAULT_CATEGORY, TOPIC_KEYWORDS
from .base import ClusterSummary, ItemSummary, SourceDoc, SummarizerProvider


def _first_sentences(text: str, n: int = 2, limit: int = 320) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in ".!?":
            out.append(buf.strip())
            if len(out) >= n:
                break
            buf = ""
    result = " ".join(out) if out else text
    return (result[:limit].rstrip() + "…") if len(result) > limit else result


def _guess_category(text: str) -> str:
    low = (text or "").lower()
    table = {
        "Санкции": ["санкц", "sanction", "эмбарго", "embargo"],
        "Военная помощь/поставки": ["поставк", "помощь", "aid", "weapons", "himars", "patriot"],
        "Дипломатия/переговоры": ["переговор", "перемир", "ceasefire", "talks", "диплом"],
        "Западная коалиция/НАТО": ["нато", "nato", "коалиц", "coalition", "ес ", "eu "],
        "Внутренняя политика РФ": ["мобилизац", "кремл", "mobiliz", "kremlin", "госдум"],
        "Фронт/боевые действия": [
            "фронт", "обстрел", "удар", "наступлен", "frontline", "offensive",
            "strike", "shelling", "дрон", "ракет", "missile",
        ],
    }
    for cat, keys in table.items():
        if any(k in low for k in keys):
            return cat
    return DEFAULT_CATEGORY


class MockSummarizer(SummarizerProvider):
    name = "mock"

    async def summarize_item(self, title: str, text: str, lang: str = "ru") -> ItemSummary:
        prefix = "[перевод] " if lang != "ru" else ""
        summary = _first_sentences(text or title, n=2)
        return ItemSummary(
            title_ru=f"{prefix}{title}".strip(),
            summary_ru=f"{prefix}{summary}".strip() or f"{prefix}{title}".strip(),
            category=_guess_category(f"{title} {text}"),
            key_points=[p for p in _first_sentences(text, n=3).split(". ") if p][:3],
        )

    async def summarize_cluster(
        self, docs: list[SourceDoc], vision_notes: list[str] | None = None
    ) -> ClusterSummary:
        head = docs[0].title if docs else "Сюжет"
        joined = " ".join(f"{d.title}. {d.text}" for d in docs)
        digest = _first_sentences(joined, n=3, limit=500)
        if vision_notes:
            digest = f"{digest} На медиа: {vision_notes[0]}"
        points = []
        for d in docs[:4]:
            s = _first_sentences(d.text or d.title, n=1, limit=140)
            if s:
                points.append(s)
        return ClusterSummary(
            headline_ru=head,
            digest_ru=digest or head,
            category=_guess_category(joined),
            key_points=points,
        )
