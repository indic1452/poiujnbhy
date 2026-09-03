"""Починка текста, вынутого из PDF и сканов.

Текст из PDF почти никогда не приходит таким, каким его набирали. Верстальная
программа кладёт на страницу глифы, а не слова, и при обратном чтении
получается то, что человек в библиотеке отдела видит своими глазами:

* «Занимаемаяполосачастотизмеряется» — слова без пробелов;
* «ﬁльтрация» — лигатура вместо двух букв, и слово не находится;
* «Мoдуляция» — латинская «o» внутри русского слова, и оно не находится тоже;
* «(4πR2)» — потерянная степень, из формулы получилось другое число;
* колонтитул «2 специальный отдел — Методика измерений 17», попавший в каждый
  фрагмент книги и разбавивший смысл всех до одного.

Здесь собрано то, что чинится в самом тексте, без обращения к странице. Всё,
для чего нужна геометрия глифов — пробелы между словами, индексы и степени,
порядок колонок, — делает разборщик PDF.

Правила намеренно осторожные. Библиотека отдела — это стандарты и методики, в
которых числа и обозначения важнее гладкости: лучше оставить как есть, чем
поправить наугад.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List, Sequence, Tuple

__all__ = [
    "LIGATURES",
    "repair_text",
    "unify_ligatures",
    "unify_homoglyphs",
    "unify_math_letters",
    "spell_out_super_and_subscripts",
    "drop_running_titles",
    "repair_report",
]

#: Лигатуры: один знак вместо двух-трёх букв. В PDF они попадают из шрифта,
#: и поиск по слову «фильтрация» такое слово не находит — для него это другая
#: последовательность символов.
LIGATURES: Dict[str, str] = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    "Ĳ": "IJ", "ĳ": "ij",
    "№": "№",
}

#: Знаки, которых в тексте быть не должно: они невидимы, но слово с ними
#: перестаёт совпадать с тем же словом без них.
_INVISIBLE = str.maketrans({
    "­": "",        # мягкий перенос
    "​": "",        # нулевой пробел
    "‌": "",        # неразрывающий нуль
    "‍": "",        # соединитель
    "⁠": "",        # невидимый плюс
    "﻿": "",        # метка порядка байтов
    " ": " ",       # неразрывный пробел
    " ": " ",       # цифровой пробел
    " ": " ",       # тонкий пробел
    " ": " ",       # узкий неразрывный
    "　": " ",       # идеографический пробел
})

#: Латинские буквы, неотличимые на вид от русских. Только те, что совпадают
#: в начертании: «i» и «і» в список не входят — вторая украинская, и такая
#: замена испортила бы текст вместо починки.
_LAT_TO_CYR = {
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
}
_CYR_TO_LAT = {cyr: lat for lat, cyr in _LAT_TO_CYR.items()}

#: Верхние и нижние индексы, набранные готовыми знаками Unicode. Записываем
#: их так, как принято в тексте: «R^2», «H_2O». Иначе «R²» и «R2» — это два
#: разных слова для поиска, а в ответе помощника степень пропадает вовсе.
_SUPERSCRIPTS = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3",
    "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
    "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
    "ⁿ": "n", "ⁱ": "i",
}
_SUBSCRIPTS = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3",
    "₄": "4", "₅": "5", "₆": "6", "₇": "7",
    "₈": "8", "₉": "9", "₊": "+", "₋": "-",
}

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_LATIN = re.compile(r"[A-Za-z]")
_DIGITS_RE = re.compile(r"\d+")

#: Колонтитул считаем колонтитулом, если он повторился хотя бы на такой доле
#: страниц. Порог не низкий: в методике бывает раздел, начинающийся одинаково
#: на трёх страницах подряд, и выбрасывать его нельзя.
RUNNING_SHARE = 0.6

#: На книге в две страницы говорить о «повторяющемся колонтитуле» нельзя.
RUNNING_MIN_PAGES = 3

#: Сколько строк сверху и снизу страницы считаем возможным колонтитулом.
RUNNING_EDGE_LINES = 2

#: Строка длиннее этого — уже не колонтитул, а текст.
RUNNING_MAX_CHARS = 120


def unify_ligatures(text: str) -> str:
    """«ﬁльтрация» → «фильтрация»: один знак разворачиваем в буквы."""
    if not text:
        return text
    for glyph, letters in LIGATURES.items():
        if glyph in text:
            text = text.replace(glyph, letters)
    return text


def unify_math_letters(text: str) -> str:
    """Буквы из математических наборов Unicode — обычными буквами.

    В формулах вёрстка нередко берёт «𝑃» (математическая курсивная P) вместо
    обычной «P». На вид это та же буква, для поиска — другая, и обозначение
    из стандарта перестаёт находиться.
    """
    if not text:
        return text
    out = []
    for character in text:
        code = ord(character)
        if 0x1D400 <= code <= 0x1D7FF or 0x2100 <= code <= 0x214F:
            plain = unicodedata.normalize("NFKC", character)
            out.append(plain if plain.isalnum() else character)
        else:
            out.append(character)
    return "".join(out)


def spell_out_super_and_subscripts(text: str) -> str:
    """«R²» → «R^2», «H₂O» → «H_2O».

    Степень и индекс — часть обозначения, а не украшение. В виде отдельного
    знака Unicode они не находятся поиском и теряются в ответе помощника;
    записанные знаками «^» и «_» — читаются и человеком, и моделью.
    """
    if not text:
        return text
    out: List[str] = []
    mode = ""                       # какой ряд идёт сейчас: '^', '_' или никакой
    for character in text:
        if character in _SUPERSCRIPTS:
            if mode != "^":
                out.append("^")
                mode = "^"
            out.append(_SUPERSCRIPTS[character])
            continue
        if character in _SUBSCRIPTS:
            if mode != "_":
                out.append("_")
                mode = "_"
            out.append(_SUBSCRIPTS[character])
            continue
        mode = ""
        out.append(character)
    return "".join(out)


def unify_homoglyphs(text: str) -> str:
    """Латинские буквы внутри русских слов — и наоборот.

    «Мoдуляция» с латинской «o» выглядит безупречно и не находится ничем.
    Берётся такое из шрифтовых подстановок при вёрстке и из распознавания.

    Правим только слова, в которых буквы обеих азбук сразу, и только если
    все «чужие» буквы имеют неотличимого двойника. Слово вроде «Wi-Fi» или
    «ГОСТ Р ISO» не трогаем: там смешение настоящее.
    """
    if not text:
        return text

    def fix(match: "re.Match[str]") -> str:
        word = match.group(0)
        cyr = len(_CYRILLIC.findall(word))
        lat = len(_LATIN.findall(word))
        if not cyr or not lat or cyr == lat:
            # Поровну — значит, большинства нет и решать не за что: слово
            # вроде «КАМqam» одинаково похоже и на русское, и на латинское.
            return word
        table = _LAT_TO_CYR if cyr > lat else _CYR_TO_LAT
        alien = _LATIN if cyr > lat else _CYRILLIC
        # Хоть одна чужая буква без двойника — слово настоящее смешанное.
        if any(character not in table
               for character in word if alien.match(character)):
            return word
        return "".join(table.get(character, character) for character in word)

    return _WORD_RE.sub(fix, text)


def repair_text(text: str) -> str:
    """Все починки текста подряд — в том порядке, в каком они не мешают друг другу."""
    if not text:
        return text
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_INVISIBLE)
    text = unify_ligatures(text)
    text = unify_math_letters(text)
    text = spell_out_super_and_subscripts(text)
    return unify_homoglyphs(text)


def repair_report(before: str, after: str) -> Dict[str, int]:
    """Что именно починилось — числами, для карточки документа.

    Человеку важно знать не «текст поправлен», а что с ним сделали: если
    правок много, документ стоит пересохранить у себя, а не жить с починкой.
    """
    report: Dict[str, int] = {}
    ligatures = sum(before.count(glyph) for glyph in LIGATURES)
    if ligatures:
        report["ligatures"] = ligatures
    invisible = sum(before.count(chr(code)) for code in
                    (0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))
    if invisible:
        report["invisible"] = invisible
    scripts = sum(before.count(glyph)
                  for glyph in list(_SUPERSCRIPTS) + list(_SUBSCRIPTS))
    if scripts:
        report["scripts"] = scripts
    mixed = 0
    for match in _WORD_RE.finditer(before):
        word = match.group(0)
        if _CYRILLIC.search(word) and _LATIN.search(word):
            mixed += 1
    if mixed:
        # Считаем слова со смешением до починки; сколько из них поправлено,
        # видно по разнице.
        after_mixed = sum(
            1 for match in _WORD_RE.finditer(after)
            if _CYRILLIC.search(match.group(0)) and _LATIN.search(match.group(0)))
        if mixed > after_mixed:
            report["homoglyphs"] = mixed - after_mixed
    return report


def _may_be_running(line: str) -> bool:
    """Может ли строка вообще быть колонтитулом.

    Длинная строка — это текст: абзац, повторённый на каждой странице, бывает
    (оговорка о применении методики), и выбрасывать его нельзя.
    """
    text = (line or "").strip()
    if not text or len(text) > RUNNING_MAX_CHARS:
        return False
    # Заголовок раздела Markdown колонтитулом не бывает: его восстановил
    # разборщик по кеглю, и он несёт структуру документа.
    return not text.startswith("#")


#: Номер страницы стоит по краям строки колонтитула: «Методика измерений 17»
#: или «17 Методика измерений». Обезличиваем только его — цифры в середине
#: строки значат, и без них «Таблица 3.1» и «Таблица 3.7» стали бы одной
#: строкой, а пункт «Порог 96 дБм» — колонтитулом.
_EDGE_DIGITS_RE = re.compile(r"^[\s\W]*\d+[\s\W]*|[\s\W]*\d+[\s\W]*$")


def _running_key(line: str) -> str:
    """Колонтитулы отличаются только номером страницы — номер и обезличиваем."""
    text = " ".join((line or "").split()).strip().lower()
    if not text:
        return ""
    if not _DIGITS_RE.sub("", text).strip(" .,;:—-–()[]"):
        return "#"                    # строка из одних цифр — это номер страницы
    stripped = _EDGE_DIGITS_RE.sub(" ", text).strip()
    return stripped or "#"


def drop_running_titles(
    pages: Sequence[Sequence[str]],
) -> "Tuple[List[List[str]], List[str]]":
    """Убрать колонтитулы, повторяющиеся сверху и снизу страниц.

    «2 специальный отдел — Методика измерений 17» на каждой из шестисот
    страниц книги попадает в каждый фрагмент: смысл фрагмента разбавляется
    названием отдела, а поиск по названию отдела находит всю библиотеку.

    Возвращает страницы без колонтитулов и список того, что убрано, — чтобы
    в карточке документа было видно, что именно система сочла колонтитулом.
    """
    if len(pages) < RUNNING_MIN_PAGES:
        return [list(page) for page in pages], []

    # Считаем верх и низ страницы отдельно. Колонтитул держится своего края:
    # он либо шапка, либо подвал. Без этого под правило попадала бы любая
    # строка, которая случайно повторилась у другого края, — а в методике
    # такие есть: «Таблица 1» сверху и «Продолжение на следующей странице»
    # снизу живут по своим законам.
    top: Dict[str, int] = {}
    bottom: Dict[str, int] = {}
    for page in pages:
        for line in dict.fromkeys(page[:RUNNING_EDGE_LINES]):
            if _may_be_running(line):
                key = _running_key(line)
                top[key] = top.get(key, 0) + 1
        for line in dict.fromkeys(page[-RUNNING_EDGE_LINES:]):
            if _may_be_running(line):
                key = _running_key(line)
                bottom[key] = bottom.get(key, 0) + 1

    need = max(RUNNING_MIN_PAGES, int(len(pages) * RUNNING_SHARE))
    running_top = {key for key, count in top.items() if count >= need and key}
    running_bottom = {key for key, count in bottom.items()
                      if count >= need and key}
    if not running_top and not running_bottom:
        return [list(page) for page in pages], []

    dropped: List[str] = []
    cleaned: List[List[str]] = []
    for page in pages:
        keep: List[str] = []
        for index, line in enumerate(page):
            key = _running_key(line)
            at_top = index < RUNNING_EDGE_LINES
            at_bottom = index >= len(page) - RUNNING_EDGE_LINES
            if ((at_top and key in running_top)
                    or (at_bottom and key in running_bottom)):
                dropped.append(line)
                continue
            keep.append(line)
        cleaned.append(keep)
    # В список показываем разные колонтитулы, а не все шестьсот повторов.
    unique = list(dict.fromkeys(_running_key(line) for line in dropped))
    samples = []
    for key in unique:
        for line in dropped:
            if _running_key(line) == key:
                samples.append(line)
                break
    return cleaned, samples
