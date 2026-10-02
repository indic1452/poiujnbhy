"""Каталог кодов: чтение data/kody_katalog.json и поиск по семейству, классу, (n,k), многочлену.

Каталог собран из первоисточников (стандарты, руководства, патенты, открытый код) по пяти областям:
kosmos, mobilnaya, efir, provod, obshchie — справочник docs/22-kody.md. У каждой записи — параметры
в записи стандарта, ссылки на источник (файл istochniki/…, страница или строки, URL), проверка и что
из этого уже снимает проект («в проекте»: есть / частично / нет / справочно / не найдено). Таблицы
(перемежители, матрицы, шаблоны выкалывания) — data/kody/<область>/…, читаются функцией таблица().

Только стандартная библиотека.

    >>> from reportgen.potok import katalog_kodov as кк
    >>> кк.найти(многочлен="x^16+x^12+x^5+1")[:1]      # doctest: +SKIP
    >>> кк.найти("DVB-T2", класс="LDPC")                # doctest: +SKIP
"""

from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

ДАННЫЕ = Path(__file__).resolve().parent / "data"
ПУТЬ_КАТАЛОГА = ДАННЫЕ / "kody_katalog.json"
ПРЕФИКС_ДАННЫХ = "src/reportgen/potok/data/"
ОБЛАСТИ = ("kosmos", "mobilnaya", "efir", "provod", "obshchie")
В_ПРОЕКТЕ = ("есть", "частично", "нет", "справочно", "не найдено")
ПОЛЯ = ("ид", "имя", "семейство", "область", "вид", "класс", "параметры", "где применяется", "источник",
        "проверка", "сложность внедрения", "в проекте")
НЕОБЯЗАТЕЛЬНЫЕ = ("также в", "сверка", "см. также", "слиты")


@lru_cache(maxsize=1)
def каталог() -> dict:
    """Весь каталог: {"схема", "собран", "области", "записей", "записи"}."""
    return json.loads(ПУТЬ_КАТАЛОГА.read_text(encoding="utf-8"))


def записи(область: str | None = None) -> list[dict]:
    зз = каталог()["записи"]
    return [z for z in зз if z["область"] == область] if область else list(зз)


@lru_cache(maxsize=1)
def _по_ид() -> dict[str, dict]:
    return {z["ид"]: z for z in каталог()["записи"]}


def запись(ид: str) -> dict | None:
    """Запись по ид; ид присоединённой (слитой) записи ведёт к основной."""
    z = _по_ид().get(ид)
    if z is not None:
        return z
    for z in каталог()["записи"]:
        if any(с["ид"] == ид for с in z.get("слиты", [])):
            return z
    return None


def области() -> dict[str, dict]:
    return каталог()["области"]


def семейства(область: str | None = None) -> dict[str, int]:
    """Семейство → число записей (по убыванию)."""
    return dict(Counter(z["семейство"] for z in записи(область)).most_common())


def сводка() -> dict:
    """Числа по областям, классам и признаку «в проекте»."""
    зз = записи()
    return {
        "записей": len(зз),
        "по областям": dict(Counter(z["область"] for z in зз)),
        "по классам": dict(Counter(z["класс"] for z in зз).most_common()),
        "в проекте": dict(Counter(z["в проекте"] for z in зз)),
    }


# ---------- (n,k) ----------

_НК_ИМЯ = re.compile(r"\((\d{1,6})\s*,\s*(\d{1,6})(?:\s*,\s*\d{1,6})?\)")
_НК_ПОЛЕ = re.compile(r"^\s*(\d{1,6})\s*[,/]\s*(\d{1,6})")


def n_k(z: dict) -> list[tuple[int, int]]:
    """Пары (n, k) записи: поле параметров «n,k» и все «(n,k)» / «(n,k,d)» в имени."""
    пары: list[tuple[int, int]] = []
    поле = z.get("параметры", {}).get("n,k")
    if isinstance(поле, str):
        m = _НК_ПОЛЕ.match(поле)
        if m:
            пары.append((int(m.group(1)), int(m.group(2))))
    for m in _НК_ИМЯ.finditer(z.get("имя", "")):
        пара = (int(m.group(1)), int(m.group(2)))
        if пара[0] >= пара[1] and пара not in пары:
            пары.append(пара)
    return пары


# ---------- многочлены ----------

_СТЕПЕНИ = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_ЧЛЕН = r"(?:[xXD](?:\s*\^?\s*-?\d+)?|1)"
_МНОГОЧЛЕН = re.compile(rf"(?<![A-Za-zА-Яа-я0-9_]){_ЧЛЕН}(?:\s*\+\s*{_ЧЛЕН})+(?![A-Za-zА-Яа-я0-9_])")
_HEX = re.compile(r"(?<![A-Za-z0-9_])0[xX]([0-9A-Fa-f]{1,32})(?![0-9A-Za-z_])")


def _степени_в_число(текст: str) -> int | None:
    v = 0
    есть_x = False
    for член in re.split(r"\s*\+\s*", текст.strip()):
        член = член.replace(" ", "")
        if член == "1":
            степень = 0
        else:
            есть_x = True
            хвост = член[1:].lstrip("^")
            степень = abs(int(хвост)) if хвост else 1
        if степень > 4096:
            return None
        v ^= 1 << степень
    return v if есть_x else None


def многочлен_в_числа(запись_многочлена: str) -> set[int]:
    """Возможные двоичные значения записи: «x^16+x^12+x^5+1», «1 + D^3 + D^4», «0x1021» (без старшего
    члена — добавляется вариант со старшим членом по числу шестнадцатеричных знаков), «0x11021»."""
    s = запись_многочлена.translate(_СТЕПЕНИ).strip()
    итог: set[int] = set()
    m = re.fullmatch(r"0[xX]([0-9A-Fa-f]+)", s)
    if m:
        v = int(m.group(1), 16)
        итог.add(v)
        итог.add(v | (1 << (4 * len(m.group(1)))))
        return итог
    m = _МНОГОЧЛЕН.fullmatch(s) or _МНОГОЧЛЕН.search(s)
    if m:
        v = _степени_в_число(m.group(0))
        if v:
            итог.add(v)
    return итог


def _взаимный(v: int) -> int:
    return int(bin(v)[2:][::-1], 2)


@lru_cache(maxsize=4096)
def _многочлены_записи(ид: str) -> frozenset[int]:
    z = _по_ид()[ид]
    текст = (z["имя"] + " " + json.dumps(z["параметры"], ensure_ascii=False)).translate(_СТЕПЕНИ)
    итог: set[int] = set()
    for m in _МНОГОЧЛЕН.finditer(текст):
        v = _степени_в_число(m.group(0))
        if v and v > 3:
            итог.add(v)
    ширина = z["параметры"].get("width") if isinstance(z["параметры"].get("width"), int) else None
    for m in _HEX.finditer(текст):
        v = int(m.group(1), 16)
        итог.add(v)
        итог.add(v | (1 << (4 * len(m.group(1)))))
        if ширина:
            итог.add(v | (1 << ширина))
    return frozenset(итог)


def многочлены(z: dict) -> set[int]:
    """Все многочлены записи (из имени и параметров) двоичными числами."""
    return set(_многочлены_записи(z["ид"]))


# ---------- поиск ----------

def _норм(s: str) -> str:
    return s.lower().replace("ё", "е")


@lru_cache(maxsize=4096)
def _стог(ид: str) -> str:
    z = _по_ид()[ид]
    return _норм(" ".join([z["имя"], z["семейство"], z["вид"], z["класс"], z["где применяется"],
                           json.dumps(z["параметры"], ensure_ascii=False)]))


def найти(текст: str | None = None, *, область: str | None = None, семейство: str | None = None,
          класс: str | None = None, в_проекте: str | None = None, n: int | None = None, k: int | None = None,
          многочлен: str | None = None, взаимный: bool = True, предел: int | None = None) -> list[dict]:
    """Записи, подходящие под все заданные условия.

    текст — все слова должны встретиться в имени, семействе, виде, «где применяется» или параметрах;
    семейство — подстрока семейства (без учёта регистра); класс — точное значение поля «класс»;
    n, k — пара из n_k(); многочлен — запись «x^…», «0x…» или восьмеричная цифрами («171»: ищется как
    отдельное число в параметрах); взаимный — засчитывать и взаимный (обращённый) многочлен.
    """
    слова = _норм(текст).split() if текст else []
    искомые: set[int] = set()
    восьм = None
    if многочлен:
        if re.fullmatch(r"[0-7]{2,}", многочлен.strip()):
            восьм = re.compile(rf"(?<![0-9A-Za-z]){многочлен.strip()}(?![0-9A-Za-z])")
        else:
            искомые = многочлен_в_числа(многочлен)
            if взаимный:
                искомые |= {_взаимный(v) for v in искомые}
            if not искомые:
                raise ValueError(f"не понял запись многочлена: {многочлен!r}")
    итог = []
    for z in каталог()["записи"]:
        if область and z["область"] != область and область not in z.get("также в", []):
            continue
        if семейство and _норм(семейство) not in _норм(z["семейство"]):
            continue
        if класс and z["класс"] != класс:
            continue
        if в_проекте and z["в проекте"] != в_проекте:
            continue
        if n is not None or k is not None:
            if not any((n is None or nn == n) and (k is None or kk == k) for nn, kk in n_k(z)):
                continue
        if слова:
            стог = _стог(z["ид"])
            if not all(с in стог for с in слова):
                continue
        if искомые and not (искомые & _многочлены_записи(z["ид"])):
            continue
        if восьм and not восьм.search(json.dumps(z["параметры"], ensure_ascii=False) + " " + z["имя"]):
            continue
        итог.append(z)
        if предел and len(итог) >= предел:
            break
    return итог


# ---------- таблицы и файлы ----------

def путь_таблицы(путь: str) -> Path:
    """Путь таблицы из записи («src/reportgen/potok/data/kody/…» или «kody/…») → файл в пакете."""
    if путь.startswith(ПРЕФИКС_ДАННЫХ):
        путь = путь[len(ПРЕФИКС_ДАННЫХ):]
    p = (ДАННЫЕ / путь).resolve()
    if ДАННЫЕ.resolve() not in p.parents:
        raise ValueError(f"путь вне data/: {путь}")
    return p


def таблица(путь: str):
    """Прочитать таблицу (JSON) из data/kody/…; другие файлы — содержимым в байтах."""
    p = путь_таблицы(путь)
    if p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    return p.read_bytes()


def таблицы_записи(z: dict) -> list[str]:
    """Пути таблиц data/kody/…, упомянутые в параметрах записи."""
    текст = json.dumps(z["параметры"], ensure_ascii=False)
    return sorted(set(re.findall(r"src/reportgen/potok/data/kody/[^\s\"'(),;«»]+", текст)))


def корень_репозитория() -> Path | None:
    """Корень репозитория, если пакет запущен из него (там лежит istochniki/); у установленного — None."""
    p = Path(__file__).resolve().parents[3]
    return p if (p / "istochniki").is_dir() else None


def файл_источника(путь: str) -> Path | None:
    """Файл первоисточника istochniki/… (только при запуске из репозитория; в колесо он не входит)."""
    корень = корень_репозитория()
    if корень is None:
        return None
    p = корень / путь
    return p if p.exists() else None


def main(argv: list[str] | None = None) -> int:
    import argparse  # noqa: PLC0415 — только для запуска из командной строки

    п = argparse.ArgumentParser(prog="python -m reportgen.potok.katalog_kodov",
                                description="Поиск в каталоге кодов (docs/22-kody.md)")
    п.add_argument("текст", nargs="*", help="слова для поиска")
    п.add_argument("--область", choices=ОБЛАСТИ)
    п.add_argument("--семейство")
    п.add_argument("--класс")
    п.add_argument("--в-проекте", dest="в_проекте", choices=В_ПРОЕКТЕ)
    п.add_argument("--n", type=int)
    п.add_argument("--k", type=int)
    п.add_argument("--многочлен")
    п.add_argument("--предел", type=int, default=50)
    п.add_argument("--подробно", action="store_true", help="печатать запись целиком (JSON)")
    а = п.parse_args(argv)
    нашлось = найти(" ".join(а.текст) or None, область=а.область, семейство=а.семейство, класс=а.класс,
                    в_проекте=а.в_проекте, n=а.n, k=а.k, многочлен=а.многочлен, предел=а.предел)
    for z in нашлось:
        if а.подробно:
            print(json.dumps(z, ensure_ascii=False, indent=1))
        else:
            print(f"{z['ид']} | {z['область']} | {z['класс']} | в проекте: {z['в проекте']} | {z['имя']}")
    print(f"найдено: {len(нашлось)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
