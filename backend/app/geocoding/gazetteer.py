"""Офлайн-геокодер: курируемые военные объекты + газеттир населённых пунктов.

Матчинг — точный по нормализованному имени, затем fuzzy (rapidfuzz). Данные
берутся из backend/data/*.csv; при наличии — из полного газеттира
(settings.gazetteer_full_csv, собирается scripts/build_gazetteer.py).
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from ..config import BACKEND_DIR, settings
from .base import AdminRegion, Geocoder, GeoResult

_DATA = BACKEND_DIR / "data"
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е").replace("'", "").replace("’", "")
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


@dataclass
class Entry:
    canonical: str
    lat: float
    lon: float
    admin1: str | None
    admin2: str | None
    country: str | None
    kind: str  # object | gazetteer
    otype: str  # refinery/airfield/city/...
    names: list[str] = field(default_factory=list)  # нормализованные имена+алиасы


def _split_aliases(raw: str) -> list[str]:
    return [a.strip() for a in (raw or "").split("|") if a.strip()]


class GazetteerGeocoder(Geocoder):
    name = "gazetteer"

    def __init__(self) -> None:
        self._entries: list[Entry] = []
        self._exact: dict[str, list[Entry]] = {}
        self._choices: list[str] = []  # параллельно _choice_entry
        self._choice_entry: list[Entry] = []
        self._load()

    # ---- загрузка данных ----
    def _add_entry(self, e: Entry) -> None:
        self._entries.append(e)
        for n in e.names:
            self._exact.setdefault(n, []).append(e)
            self._choices.append(n)
            self._choice_entry.append(e)

    def _load_csv(self, path: Path, kind: str) -> None:
        if not path.exists():
            return
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    lat = float(row["lat"])
                    lon = float(row["lon"])
                except (KeyError, ValueError):
                    continue
                names_src = [
                    row.get("name_ru"),
                    row.get("name_uk"),
                    row.get("name_en"),
                    *_split_aliases(row.get("aliases", "")),
                ]
                names = []
                for n in names_src:
                    nn = normalize(n or "")
                    if len(nn) >= 3 and nn not in names:
                        names.append(nn)
                if not names:
                    continue
                self._add_entry(
                    Entry(
                        canonical=(row.get("name_ru") or row.get("name_en") or names[0]),
                        lat=lat,
                        lon=lon,
                        admin1=(row.get("admin1") or None),
                        admin2=(row.get("admin2") or None),
                        country=(row.get("country") or None),
                        kind=kind,
                        otype=(row.get("type") or row.get("feature") or ""),
                        names=names,
                    )
                )

    def _load(self) -> None:
        self._load_csv(_DATA / "war_objects.csv", "object")
        self._load_csv(_DATA / "gazetteer_sample.csv", "gazetteer")
        # полный газеттир (если собран build_gazetteer.py)
        self._load_csv(_DATA / "gazetteer_full.csv", "gazetteer")
        if settings.gazetteer_full_csv:
            self._load_csv(Path(settings.gazetteer_full_csv), "gazetteer")

    # ---- геокодирование ----
    def _pick(
        self, entries: list[Entry], country_hint: str | None, object_hint: str | None
    ) -> Entry:
        cands = entries
        if country_hint:
            ch = country_hint.strip().upper()
            filtered = [e for e in cands if (e.country or "").upper() == ch]
            if filtered:
                cands = filtered
        if object_hint:
            objs = [e for e in cands if e.kind == "object"]
            if objs:
                cands = objs
        # объекты приоритетнее нас. пунктов
        cands.sort(key=lambda e: (e.kind != "object",))
        return cands[0]

    def geocode(
        self,
        name: str,
        *,
        admin_hint: str | None = None,
        country_hint: str | None = None,
        object_hint: str | None = None,
    ) -> GeoResult | None:
        query = normalize(object_hint or name)
        if not query:
            return None

        # 1) точное совпадение
        if query in self._exact:
            entry = self._pick(self._exact[query], country_hint, object_hint)
            conf = 0.9 if entry.kind == "object" else 0.8
            return self._result(entry, conf)

        # 2) fuzzy
        if not self._choices:
            return None
        matches = process.extract(
            query, self._choices, scorer=fuzz.WRatio, limit=5, score_cutoff=85
        )
        if not matches:
            return None
        # собрать entry-кандидатов, сохранив лучший score
        seen: dict[int, float] = {}
        cands: list[Entry] = []
        for _m, score, idx in matches:
            e = self._choice_entry[idx]
            key = id(e)
            if key not in seen or score > seen[key]:
                seen[key] = score
                if e not in cands:
                    cands.append(e)
        best_score = max(seen.values())
        entry = self._pick(cands, country_hint, object_hint)
        conf = round(best_score / 100.0 * 0.8, 3)
        return self._result(entry, conf)

    def _result(self, e: Entry, confidence: float) -> GeoResult:
        return GeoResult(
            lat=e.lat,
            lon=e.lon,
            matched_name=e.canonical,
            admin1=e.admin1,
            admin2=e.admin2,
            country=e.country,
            kind=e.kind,
            source="object" if e.kind == "object" else "gazetteer",
            confidence=confidence,
        )

    @staticmethod
    def _token_hit(name_token: str, tokens: list[str], tokenset: set[str]) -> bool:
        """Слово имени присутствует в тексте (учитывая падежные окончания).

        Словоформа считается совпавшей, если основа имени (без 1–2 последних
        букв) является префиксом токена, а токен не намного длиннее — так
        «покровск»→«покровском» проходит, а «красноармейск» ≠ «краснодарском».
        """
        if name_token in tokenset:
            return True
        if len(name_token) < 5:
            return False
        stems = [s for s in (name_token[:-1], name_token[:-2]) if len(s) >= 4]
        for t in tokens:
            if len(t) - len(name_token) > 5:  # допускаем прилаг. формы (Харьковской)
                continue
            if any(t.startswith(s) for s in stems):
                return True
        return False

    def find_mentions(self, text: str) -> list[str]:
        """Найти известные топонимы в тексте с учётом словоформ (падежи)."""
        norm = normalize(text)
        if not norm:
            return []
        tokens = norm.split()
        tokenset = set(tokens)
        found: list[str] = []
        seen: set[str] = set()
        for e in self._entries:
            hit = False
            for n in e.names:
                parts = [p for p in n.split() if len(p) >= 3]
                if not parts:
                    continue
                if all(self._token_hit(p, tokens, tokenset) for p in parts):
                    hit = True
                    break
            if hit and e.canonical not in seen:
                seen.add(e.canonical)
                found.append(e.canonical)
        return found[:8]

    def reverse(self, lat: float, lon: float) -> AdminRegion | None:
        """Грубый reverse: ближайшая запись газеттира (для ручных точек)."""
        if not self._entries:
            return None
        best = min(
            self._entries,
            key=lambda e: (e.lat - lat) ** 2 + (e.lon - lon) ** 2,
        )
        return AdminRegion(country=best.country, admin1=best.admin1, admin2=best.admin2)

    def health(self) -> bool:
        return bool(self._entries)
