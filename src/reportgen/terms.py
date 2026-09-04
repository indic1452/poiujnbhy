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

#: Сколько равнозначных РУССКИХ написаний добавлять к запросу. Их немного, и
#: идут они первыми: сокращение — одно слово и самое редкое слово в запросе,
#: места оно почти не занимает, а пользы даёт больше английского эквивалента.
#: Больше двух не нужно: третье написание одного термина — уже редкость, а
#: место в общем пределе оно отнимает у английского.
MAX_RU_EXPANSIONS = 2

_WORD = re.compile(r"[a-zа-я0-9]+")

#: Цифра в написании: признак того, что это КОНКРЕТНАЯ разновидность
#: (КАМ-16, ФМ-4, ОКС-7), а не другое написание того же самого.
_DIGIT = re.compile(r"[0-9]")

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
    #: Равнозначные написания того же термина по-русски: КСВ и КСВН, ОСШ и
    #: С/Ш, ФМ-4 и ОФМ-4. Инженер печатает одно, а в книге стоит другое, и
    #: словесный поиск их не сводит — для BM25 это разные слова. Только
    #: ОДНОСЛОВНЫЕ сокращения: многословную расшифровку к запросу добавлять
    #: нельзя, она тянет в выдачу всё, где есть «коэффициент» или «сигнал».
    ru_syn: Tuple[str, ...] = ()
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

    def __init__(self, terms: Sequence[Term], *, source: Path | None = None,
                 problems: Sequence[str] = ()):
        # Длинные основы вперёд: «полоса пропускания» точнее, чем «полоса», и
        # если сработали обе — брать надо точную.
        self.terms: List[Term] = sorted(terms, key=lambda t: len(t.ru), reverse=True)
        self.source = source
        #: Записи, которые словарь отбросил, и почему. Справочник заявлен
        #: пополняемым, а отбрасывал строки МОЛЧА: двухбуквенное сокращение,
        #: запись без эквивалентов, лишняя запятая в JSON — всё это выключало
        #: термин (а битый файл — весь словарь) без единого слова человеку.
        #: Печатает этот список команда «reportgen terms».
        self.problems: List[str] = list(problems)

    def __len__(self) -> int:
        return len(self.terms)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TermGlossary":
        """Читает словарь. Нет файла или он испорчен — пустой словарь.

        Молча: поиск без расширения работает, просто хуже. Ронять приём
        библиотеки из-за справочника нельзя.
        """
        resolved = Path(path) if path else default_path()
        problems: List[str] = []
        try:
            raw = json.loads(resolved.read_text(encoding="utf-8-sig"))
        except OSError as error:
            return cls([], source=None, problems=[f"файл не прочитан: {error}"])
        except ValueError as error:
            # Лишняя запятая выключает ВЕСЬ словарь, и до команды «terms» об
            # этом не говорил никто: поиск просто переставал добавлять слова.
            return cls([], source=resolved,
                       problems=[f"файл не разобран как JSON: {error}"])

        rows = raw.get("terms", raw) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return cls([], source=resolved,
                       problems=["в файле нет списка «terms»"])

        terms: List[Term] = []
        for number, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                problems.append(f"запись {number}: не объект — пропущена")
                continue
            ru = normalize(str(row.get("ru", "")).strip())
            english = row.get("en") or []
            if isinstance(english, str):
                english = [english]
            english = tuple(
                str(item).strip().lower() for item in english if str(item).strip()
            )
            synonyms = row.get("ru_syn") or []
            if isinstance(synonyms, str):
                synonyms = [synonyms]
            # Чистим по тем же правилам, что и «ru»: двухбуквенное написание в
            # поиске бесполезно и опасно, а многословная расшифровка к запросу
            # не добавляется (см. MAX_RU_EXPANSIONS) — она и есть «ru».
            synonyms = tuple(dict.fromkeys(
                word for word in (normalize(str(item).strip()) for item in synonyms)
                if len(word) >= MIN_TERM and word != ru
            ))
            # Запись без английского эквивалента разрешена: СЛС и РРЛС оба
            # русские, выдумывать им английское соответствие незачем.
            if len(ru) < MIN_TERM:
                problems.append(
                    f"запись {number}: «{ru}» короче {MIN_TERM} букв — пропущена "
                    "(двухбуквенное сокращение в поиске бесполезно и опасно; "
                    "пишите расшифровку в «ru», а сокращение в «ru_syn»)"
                )
                continue
            if not (english or synonyms):
                problems.append(
                    f"запись {number}: «{ru}» без «en» и без «ru_syn» — пропущена, "
                    "добавлять к запросу нечего"
                )
                continue
            terms.append(Term(
                ru=ru,
                en=english,
                ru_syn=synonyms,
                risk=str(row.get("risk", "нет")).strip() or "нет",
                note=str(row.get("note", "")).strip(),
            ))
        return cls(terms, source=resolved, problems=problems)

    def matches(self, query: str) -> List[Term]:
        """Термины словаря, встретившиеся в запросе.

        Запись срабатывает на ЛЮБОЕ из своих написаний: «КСВ» и «КСВН» — одна
        и та же величина, и записана она в файле один раз. Без этого
        равнозначные написания пришлось бы заводить отдельными записями, по
        одной на написание, и держать их в согласии руками.
        """
        text = normalize(query)
        if not text:
            return []
        return [term for term in self.terms
                if _hit(term.ru, text)
                or any(_hit(word, text) for word in term.ru_syn)]

    def expand(self, query: str, *, limit: int = MAX_EXPANSIONS,
               russian_limit: int = MAX_RU_EXPANSIONS) -> List[str]:
        """Слова, которые стоит добавить к запросу.

        Сначала равнозначные русские написания, потом английские эквиваленты.
        Порядок не косметический: русских добавляется не больше двух, и стой
        они после английских, до них не дошла бы очередь — общий предел
        выбирается уже на втором-третьем сработавшем термине.

        Уже написанное в запросе не дублируется: инженер вполне может спросить
        «поля заголовка header fields» — второй раз добавлять нечего.
        """
        already = set(_WORD.findall(normalize(query)))
        out: List[str] = []
        seen: set[str] = set()

        def add(variant: str) -> bool:
            """Добавить слово. Ложь — слово не пригодилось."""
            if variant in seen:
                return False
            words = set(_WORD.findall(variant))
            if words and words <= already:
                return False
            seen.add(variant)
            out.append(variant)
            return True

        matched = self.matches(query)
        text = normalize(query)
        russian = 0
        for term in matched:
            # У записи без написаний брать нечего: подсовывать в запрос её
            # собственную ОСНОВУ («заголов») бессмысленно — в указателе стоит
            # «заголовк», слово не найдёт ничего и займёт место в пределе.
            if not term.ru_syn:
                continue
            # Написание С ЦИФРОЙ срабатывает, но к запросу не добавляется:
            # КАМ-16 и КАМ-64, ФМ-2 и ФМ-4 — это РАЗНЫЕ модуляции, а не разные
            # написания одной. Подставив соседнее, поиск вытащил бы документы
            # про другую величину (замерено: к вопросу про КАМ-16 добавлялось
            # КАМ-64). Обозначение без цифры — «кам», «офм» — общее для всей
            # семьи, его добавлять и полезно, и безопасно.
            spellings = [word for word in term.ru_syn if not _DIGIT.search(word)]
            # Расшифровку добавляем — и это главное в записи. Спросили «ОСШ», а
            # в книге написано «отношение сигнал/шум»: без расшифровки словесный
            # поиск не находит НИЧЕГО. Замерено на настоящем пути поиска и
            # настоящем размере (25 000 фрагментов, отсев частых слов включён):
            # без неё 0 попаданий из 8, с ней — 8 из 8 первым местом. Опасение,
            # что общие слова расшифровки размоют выдачу, замер не подтвердил.
            #
            # Берётся она, только если запись нашлась НЕ по ней: когда человек и
            # так написал расшифровку, повторять её незачем — место отнимет, а
            # нового не добавит.
            # Ключ записи годится в добавку, только если это НАСТОЯЩАЯ
            # расшифровка из нескольких слов. Односложный ключ — это основа
            # («плезиохрон», «радиорелейн»), и она не сходится со словом
            # документа: «плезиохронная» приводится к «плезиохронн», на букву
            # длиннее. Такую добавку поиск не найдёт, а место в пределе она
            # займёт. Полное написание для таких записей лежит в ru_syn.
            if " " in term.ru and not _hit(term.ru, text):
                spellings.insert(0, term.ru)
            for word in spellings:
                if russian >= russian_limit or len(out) >= limit:
                    break
                if add(word):
                    russian += 1
        for term in matched:
            for english in term.en:
                for variant in _plural_variants(english):
                    if len(out) >= limit:
                        return out
                    add(variant)
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
