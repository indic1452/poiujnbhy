"""Двуязычный словарь терминов: русский запрос — английские документы.

Половина библиотеки компании связи написана по-английски: около 9800 RFC и
все паспорта на импортные микросхемы. Вопросы инженеры задают по-русски.

Смысловой поиск (bge-m3) язык переступает сам — он многоязычный. А вот
лексический поиск (BM25) не переступает никак: он ищет буквальные слова, и
запрос «какие поля в заголовке» в тексте RFC не находит НИЧЕГО. Проверено:
ноль фрагментов на русский запрос, один — на «header fields».

Это не мелочь по двум причинам. Во-первых, половина поискового сигнала на
таких вопросах пропадает, а лексический канал как раз тот, который точно
попадает в НАЗВАНИЕ ПОЛЯ — то самое, что инженер и ищет, разбирая дамп.
Во-вторых, пока не построены векторы (или не поднята служба эмбеддингов),
поиск остаётся ТОЛЬКО лексическим — и английская половина библиотеки
становится ненаходимой вовсе.

Словарь это чинит без всякой модели: увидев в запросе «поля заголовка», к
поиску добавляются «header field», «header fields». Работает офлайн,
детерминированно, результат виден инженеру, и справочник можно пополнять
самим — это обычный JSON рядом с направлениями.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

__all__ = ["TermGlossary", "glossary", "default_path", "expand_query"]

DEFAULT_PATH = Path("templates") / "terms.json"

#: Начиная с этой длины основа ищется как подстрока: «заголов» найдётся и в
#: «заголовок», и в «заголовке», и в «заголовкам» — падежей у русского слова
#: много, а стеммер системы сводит их к разным формам.
STEM_LENGTH = 5

#: Слово короче ищется целиком, с точностью до окончания. Иначе «код» ловится
#: внутри «кодировки», а «сеть» — внутри «сетевого». Но выбрасывать короткие
#: слова нельзя: АЦП, ЦАП, ФАПЧ, МШУ, ОСШ — это три-четыре буквы, и печатают
#: их постоянно.
MAX_INFLECTION = 3

#: Совсем короткое (одна-две буквы) в поиске бесполезно и опасно.
MIN_TERM = 3

#: Сколько слов добавлять к запросу. Без предела длинный вопрос превращается в
#: перечисление полусотни терминов, и BM25 перестаёт различать документы:
#: побеждает тот, где случайно совпало больше общих слов.
MAX_EXPANSIONS = 12

_WORD = re.compile(r"[a-zа-я0-9]+")

#: Сколько чужих слов допускается между словами составного термина. «Поля
#: заголовка» и «какие поля в заголовке» — один и тот же вопрос, а между
#: словами стоит предлог.
GAP_WORDS = 3


def normalize(text: str) -> str:
    """Приводит к виду, в котором слова сравниваются.

    «Ё» пишут через раз: инженер напечатает «приемопередатчик», а в словаре
    стоит «приёмопередатчик» — и паспорта импортных микросхем перестанут
    находиться из-за одной буквы. Сводим обе формы к «е».
    """
    return (text or "").lower().replace("ё", "е")

#: Кэш собранных выражений: поиск идёт на каждый запрос, а словарь большой.
_PATTERNS: Dict[str, "re.Pattern[str]"] = {}


@dataclass(frozen=True)
class Term:
    """Одна пара словаря."""

    ru: str
    en: Tuple[str, ...]
    risk: str = "нет"
    note: str = ""

    @property
    def ambiguous(self) -> bool:
        """Английское слово частое и в другом смысле («field», «window»)."""
        return self.risk == "омоним"


def default_path() -> Path:
    """Где искать словарь, если путь не задан.

    Порядок тот же, что у справочника направлений: переменная окружения,
    каталог запуска, каталог рядом с установленным пакетом. Инструкция велит
    запускать приём из scripts\\windows, где никакого templates нет.
    """
    override = os.environ.get("REPORTGEN_TERMS_PATH")
    if override:
        return Path(override)
    if DEFAULT_PATH.is_file():
        return DEFAULT_PATH
    beside_package = Path(__file__).resolve().parents[2] / "templates" / "terms.json"
    if beside_package.is_file():
        return beside_package
    return DEFAULT_PATH


def _pattern(word: str) -> "re.Pattern[str]":
    """Выражение для короткого слова: целиком, но с любым окончанием.

    «ацп» найдётся в «ацп» и «ацпшный» не найдётся, «код» — в «код», «кода»,
    «коде», но не в «кодировке»: больше трёх букв после основы — это уже
    другое слово.
    """
    found = _PATTERNS.get(word)
    if found is None:
        found = re.compile(
            rf"(?<![a-zа-яё0-9]){re.escape(word)}[а-яё]{{0,{MAX_INFLECTION}}}"
            rf"(?![a-zа-яё0-9])"
        )
        _PATTERNS[word] = found
    return found


def _word_hit(word: str, text: str) -> bool:
    """Встретилось ли одно слово термина."""
    if len(word) >= STEM_LENGTH:
        # Основа достаточно длинная, чтобы искать подстрокой: так ловятся
        # все падежи разом и не нужен словарь окончаний.
        return word in text
    return bool(_pattern(word).search(text))


def _hit(term: str, text: str) -> bool:
    """Встретился ли термин в тексте запроса.

    Составной термин ищется по словам, а не подстрокой: между «поля» и
    «заголовка» инженер запросто поставит предлог, и требование стоять
    вплотную оставило бы такой запрос без расширения. Слова должны быть все
    и в том же порядке, но не обязательно рядом.
    """
    words = term.split()
    if len(words) == 1:
        return _word_hit(term, text)
    position = 0
    for index, word in enumerate(words):
        found = _find_word(word, text, position)
        if found < 0:
            return False
        if index and _words_between(text, position, found) > GAP_WORDS:
            return False
        position = found + len(word)
    return True


def _find_word(word: str, text: str, start: int) -> int:
    """Где встретилось слово начиная с позиции. -1 — не встретилось."""
    if len(word) >= STEM_LENGTH:
        return text.find(word, start)
    found = _pattern(word).search(text, start)
    return found.start() if found else -1


def _words_between(text: str, start: int, end: int) -> int:
    return len(_WORD.findall(text[start:end])) if end > start else 0


#: Окончания, при которых слово НЕ множественное, хотя и кончается на «s».
_NOT_PLURAL = ("ss", "us", "is", "os", "as")

#: «bus» и «status» — обычные слова, множественное у них правильное
#: («buses», «statuses»); «analysis» и «basis» — нет.

#: После этих окончаний множественное число даёт «es», а не «s».
_ES_ENDINGS = ("s", "sh", "ch", "x", "z")

_VOWELS = "aeiou"


def _plural_variants(word: str) -> List[str]:
    """«header field» и «header fields» — для поиска это разные слова.

    Стеммер в системе русский: английские окончания он не срезает, поэтому
    единственное и множественное число не сходятся сами. Добавляем обе формы —
    это дешевле, чем трогать стеммер и переиндексировать библиотеку.

    Правило приходится писать аккуратно. Наивное «убрать s с конца» портит
    «loss» в «los», «address» в «addres», «endianness» в «endiannes» — такие
    обрубки не находят ничего и занимают место в и без того ограниченном
    списке добавляемых слов.
    """
    out = [word]
    head, _, last = word.rpartition(" ")

    def add(form: str) -> None:
        joined = f"{head} {form}".strip()
        if joined and joined not in out:
            out.append(joined)

    if len(last) < 3:
        return out

    if last.endswith("ies") and len(last) > 4:
        add(last[:-3] + "y")
    elif last.endswith("es") and last[:-2].endswith(_ES_ENDINGS):
        add(last[:-2])
    elif last.endswith("s") and not last.endswith(_NOT_PLURAL):
        if len(last) > 3:
            add(last[:-1])
    elif last.endswith("is"):
        # analysis → analyses, basis → bases: формы неправильные, и гадать
        # тут дороже, чем промолчать.
        pass
    elif last.endswith("y") and last[-2] not in _VOWELS:
        add(last[:-1] + "ies")
    elif last.endswith(_ES_ENDINGS):
        add(last + "es")
    else:
        add(last + "s")
    return out


class TermGlossary:
    """Словарь терминов, загруженный из JSON."""

    def __init__(self, terms: Sequence[Term], *, source: Path | None = None):
        # Длинные основы вперёд: «полоса пропускания» точнее, чем «полоса», и
        # если сработали обе — брать надо точную.
        self.terms: List[Term] = sorted(terms, key=lambda t: len(t.ru), reverse=True)
        self.source = source

    def __len__(self) -> int:
        return len(self.terms)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TermGlossary":
        """Читает словарь. Нет файла или он испорчен — пустой словарь.

        Молча: поиск без расширения работает, просто хуже. Ронять приём
        библиотеки из-за справочника нельзя.
        """
        resolved = Path(path) if path else default_path()
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return cls([], source=None)

        rows = raw.get("terms", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return cls([], source=resolved)

        terms: List[Term] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ru = normalize(str(row.get("ru", "")).strip())
            english = row.get("en") or []
            if isinstance(english, str):
                english = [english]
            english = tuple(
                str(item).strip().lower() for item in english if str(item).strip()
            )
            if len(ru) < MIN_TERM or not english:
                continue
            terms.append(Term(
                ru=ru,
                en=english,
                risk=str(row.get("risk", "нет")).strip() or "нет",
                note=str(row.get("note", "")).strip(),
            ))
        return cls(terms, source=resolved)

    def matches(self, query: str) -> List[Term]:
        """Термины словаря, встретившиеся в запросе."""
        text = normalize(query)
        if not text:
            return []
        return [term for term in self.terms if _hit(term.ru, text)]

    def expand(self, query: str, *, limit: int = MAX_EXPANSIONS) -> List[str]:
        """Английские слова, которые стоит добавить к запросу.

        Уже написанное в запросе не дублируется: инженер вполне может спросить
        «поля заголовка header fields» — второй раз добавлять нечего.
        """
        already = set(_WORD.findall(normalize(query)))
        out: List[str] = []
        seen: set[str] = set()
        for term in self.matches(query):
            for english in term.en:
                for variant in _plural_variants(english):
                    if variant in seen:
                        continue
                    words = set(_WORD.findall(variant))
                    if words and words <= already:
                        continue
                    seen.add(variant)
                    out.append(variant)
                    if len(out) >= limit:
                        return out
        return out


#: Разобранный словарь и время правки файла, по которому он прочитан.
_cache: Dict[str, Tuple[float, TermGlossary]] = {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def glossary(path: str | Path | None = None) -> TermGlossary:
    """Словарь с запоминанием: он читается на каждый запрос поиска.

    Запомненное сбрасывается, как только файл правили: справочник заявлен
    пополняемым, и если дописанные термины начинают работать только после
    перезапуска сервера, пополняемость существует лишь на словах. Сверка идёт
    по времени правки — это дешевле разбора JSON.
    """
    resolved = Path(path) if path else default_path()
    key = str(resolved)
    stamp = _mtime(resolved)
    remembered = _cache.get(key)
    if remembered is not None and remembered[0] == stamp:
        return remembered[1]
    found = TermGlossary.load(resolved)
    _cache[key] = (stamp, found)
    return found


def expand_query(query: str, path: str | Path | None = None,
                 *, limit: int = MAX_EXPANSIONS) -> Tuple[str, List[str]]:
    """Запрос с добавленными английскими терминами и список добавленного.

    Возвращает пару: во что превратился запрос и что именно добавлено — второе
    показывается инженеру, иначе выдача выглядит необъяснимой.
    """
    added = glossary(path).expand(query, limit=limit)
    if not added:
        return query, []
    return f"{query} {' '.join(added)}", added


def forget() -> None:
    """Сбросить запомненное — нужно тестам и после правки справочника."""
    _cache.clear()
    _PATTERNS.clear()
