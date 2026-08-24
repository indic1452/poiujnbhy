"""Направления техники: спутник, релейка, протоколы, измерения и прочее.

Библиотека компании неоднородна: рядом лежат методичка по работе с модемом,
том по спутниковым линиям и описание кадра HDLC. Если искать по всему сразу,
запрос «уровень» одинаково охотно найдёт уровень сигнала и уровень модели OSI.
Направление — это грубый, но дешёвый фильтр, который снимает большую часть
таких промахов, а инженеру даёт понятную группировку библиотеки.

Список направлений лежит в ``templates/domains.json`` и правится без участия
программиста (см. док. 13).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Sequence

DEFAULT_PATH = Path("templates/domains.json")
UNSET = ""
CLASSIFY_CHARS = 20000
MIN_HITS = 2


@dataclass(frozen=True)
class Domain:
    id: str
    title: str
    keywords: Sequence[str] = ()
    #: Запасное направление выбирается, только если ни одно предметное не
    #: набрало достаточно совпадений. Иначе слова «ГОСТ», «стандарт», «норма»
    #: перетянули бы к себе любой отраслевой документ — и стандарт на
    #: радиорелейную линию уехал бы из «релеек» в «нормативы».
    fallback: bool = False

    def score(self, text: str) -> int:
        """Сколько характерных слов направления встретилось в тексте."""
        return sum(1 for keyword in self.keywords if keyword in text)

    def to_dict(self) -> Dict[str, object]:
        return {"id": self.id, "title": self.title, "keywords": list(self.keywords)}


@dataclass
class DomainRegistry:
    """Справочник направлений с простым классификатором по ключевым словам."""

    domains: List[Domain]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "DomainRegistry":
        file = Path(path)
        if not file.is_file():
            return cls(domains=[])
        try:
            raw = json.loads(file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as error:
            raise ValueError(f"справочник направлений {file} повреждён: {error}") from error
        items = raw.get("domains", raw) if isinstance(raw, dict) else raw
        domains = []
        for item in items:
            if not isinstance(item, dict) or "id" not in item:
                continue
            domains.append(Domain(
                id=str(item["id"]),
                title=str(item.get("title", item["id"])),
                keywords=tuple(str(k).lower() for k in item.get("keywords", ())),
                fallback=bool(item.get("fallback", False)),
            ))
        return cls(domains=domains)

    @property
    def ids(self) -> List[str]:
        return [domain.id for domain in self.domains]

    def get(self, domain_id: str) -> Domain | None:
        return next((d for d in self.domains if d.id == domain_id), None)

    def is_known(self, domain_id: str) -> bool:
        return not domain_id or domain_id in self.ids

    def title(self, domain_id: str) -> str:
        domain = self.get(domain_id)
        return domain.title if domain else (domain_id or "не указано")

    def classify(self, *parts: str) -> str:
        """Определить направление по названию и тексту документа.

        Возвращает пустую строку, если уверенности нет. Это осознанный размен:
        неверное направление уводит документ в чужую выборку, а пустое делает
        его невидимым для поиска С фильтром (без фильтра он находится всегда).
        Неразмеченные документы видны в библиотеке — их доразмечают вручную.
        """
        text = " ".join(part for part in parts if part).lower()[:CLASSIFY_CHARS]
        if not text or not self.domains:
            return UNSET

        primary = [d for d in self.domains if not d.fallback]
        chosen = self._best(primary, text)
        if chosen:
            return chosen
        return self._best([d for d in self.domains if d.fallback], text)

    @staticmethod
    def _best(candidates: Sequence[Domain], text: str) -> str:
        scored = sorted(((domain.score(text), domain.id) for domain in candidates), reverse=True)
        if not scored:
            return UNSET
        best_score, best_id = scored[0]
        if best_score < MIN_HITS:
            return UNSET
        # Ничья между направлениями — тоже повод не гадать.
        if len(scored) > 1 and scored[1][0] == best_score:
            return UNSET
        return best_id

    def to_dict(self) -> List[Dict[str, object]]:
        return [{"id": d.id, "title": d.title} for d in self.domains]


@lru_cache(maxsize=8)
def _cached(path: str, stamp: float) -> DomainRegistry:
    return DomainRegistry.load(path)


def registry(path: str | Path = DEFAULT_PATH) -> DomainRegistry:
    """Справочник с перечитыванием файла при его изменении."""
    file = Path(path)
    stamp = file.stat().st_mtime if file.is_file() else 0.0
    return _cached(str(file), stamp)
