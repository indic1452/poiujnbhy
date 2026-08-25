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
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Sequence

DEFAULT_PATH = Path("templates/domains.json")
UNSET = ""


def default_path() -> Path:
    """Где искать справочник направлений, если путь не задан явно.

    Раньше это был просто ``templates/domains.json`` — то есть относительно
    ТЕКУЩЕГО каталога. Инструкция велит запускать приём библиотеки из
    ``scripts\\windows``, и там такого файла нет: справочник молча оказывался
    пустым, а все документы — без направления. Поиск по направлению при этом
    ничего не находил, и понять, почему, было невозможно.

    Порядок: переменная окружения, затем каталог запуска (так работают тесты и
    привычный запуск из корня), затем каталог рядом с установленным пакетом.
    """
    override = os.environ.get("REPORTGEN_DOMAINS_PATH")
    if override:
        return Path(override)
    if DEFAULT_PATH.is_file():
        return DEFAULT_PATH
    beside_package = Path(__file__).resolve().parents[2] / "templates" / "domains.json"
    if beside_package.is_file():
        return beside_package
    return DEFAULT_PATH
CLASSIFY_CHARS = 20000
MIN_HITS = 2


#: Короче этого латинское слово ищется только целиком.
SHORT_LATIN = 6

_LATIN_RE = re.compile(r"^[a-z0-9][a-z0-9./-]*$")


@lru_cache(maxsize=4096)
def _needs_boundary(keyword: str) -> bool:
    """Нужно ли искать это слово только целиком."""
    stripped = keyword.strip()
    return len(stripped) < SHORT_LATIN and bool(_LATIN_RE.match(stripped))


@lru_cache(maxsize=4096)
def _boundary_pattern(keyword: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword.strip()) + r"(?![a-z0-9])")


def _found(keyword: str, text: str) -> bool:
    if _needs_boundary(keyword):
        return bool(_boundary_pattern(keyword).search(text))
    return keyword in text


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
    #: Полка «Прочее»: сюда попадает документ, у которого не набралось вообще
    #: ничего. Пустое направление делало такие документы невидимыми для поиска
    #: с фильтром и незаметными для инженера — их просто не было в списках.
    catch_all: bool = False

    def score(self, text: str) -> int:
        """Сколько характерных слов направления встретилось в тексте.

        Короткие латинские слова ищутся только целиком. Иначе «nr» находится
        внутри «Internet», «sim» — внутри «similar», и англоязычный RFC про
        HTTP уезжает в «Мобильные сети». Русские корни, наоборот, задаются
        нарочно усечёнными («спутник» → «спутниковый»), и границу слова им
        ставить нельзя.
        """
        return sum(1 for keyword in self.keywords if _found(keyword, text))

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
                catch_all=bool(item.get("catch_all", False)),
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

        Порядок такой: сначала предметные направления, затем запасные
        (нормативы), затем полка «Прочее». Раньше на последнем шаге
        возвращалась пустая строка — и документ становился невидимым для
        поиска с фильтром и незаметным в списках. Отдельная полка честнее:
        по ней сразу видно, сколько библиотеки осталось неразобранной.
        """
        text = " ".join(part for part in parts if part).lower()[:CLASSIFY_CHARS]
        if not text or not self.domains:
            return UNSET

        primary = [d for d in self.domains if not d.fallback and not d.catch_all]
        chosen = self._best(primary, text)
        if chosen:
            return chosen
        chosen = self._best([d for d in self.domains if d.fallback], text)
        if chosen:
            return chosen
        return self.catch_all_id()

    def catch_all_id(self) -> str:
        """Идентификатор полки «Прочее», если она заведена."""
        for domain in self.domains:
            if domain.catch_all:
                return domain.id
        return UNSET

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


def registry(path: str | Path | None = None) -> DomainRegistry:
    """Справочник с перечитыванием файла при его изменении."""
    file = Path(path) if path is not None else default_path()
    stamp = file.stat().st_mtime if file.is_file() else 0.0
    return _cached(str(file), stamp)
