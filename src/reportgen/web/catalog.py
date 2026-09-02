"""Карта библиотеки для помощника: что вообще лежит на полках.

Помощник отвечал по двенадцати найденным фрагментам и о существовании
остальной библиотеки не знал. Отсюда два одинаково скверных исхода. Первый:
поиск не нашёл нужного (спросили другими словами), и помощник отвечает общим
знанием — а в томе рядом это описано подробно. Второй: в библиотеке этого
действительно нет, но сказать «у нас по коротковолновым линиям нет ничего»
помощнику неоткуда, и он рассуждает так же уверенно, как по документу.

Карта закрывает обе дыры. Она перечисляет ПОЛКИ с числами («Спутниковые
линии связи — 42 документа») и называет сами документы, начиная с полок,
которых коснулся поиск. Тогда ответ может звучать так, как звучал бы у
человека: «в справочнике по DVB-S2 есть глава про BBFrame — посмотрите её»
или «по этой линии у нас нет ничего, кроме одного паспорта».

Карта не заменяет источники: числа и утверждения по-прежнему берутся только
из фрагментов со ссылками. Она говорит модели, ГДЕ искать, а не ЧТО писать.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Dict, List, Sequence

if TYPE_CHECKING:  # pragma: no cover — только для подсказок типов
    from ..store.repo import Repositories

__all__ = ["CATALOG_CHARS", "CATALOG_SHELVES_CHARS", "LibraryCatalog", "render_catalog"]

#: Сколько знаков карты помещается в подсказку по умолчанию. Полтора десятка
#: строк: перечислить полки и назвать самые толстые документы. Больше — и
#: карта начнёт вытеснять сами фрагменты, ради которых всё и делается.
CATALOG_CHARS = 2500

#: Ниже этого карту не ужимаем, а убираем совсем: на трёхстах знаках не
#: помещается даже перечень полок, а обрубок вида «Спутниковые линии свя»
#: хуже, чем его отсутствие.
CATALOG_SHELVES_CHARS = 400

#: Названия типов документов — те же, что видит человек в библиотеке.
DOC_TYPE_TITLES = {
    "literature": "литература",
    "standards": "стандарты",
    "standard": "стандарт",
    "datasheets": "даташиты",
    "datasheet": "даташит",
    "reports": "отчёты",
    "report": "отчёт",
    "regulations": "регламенты",
    "regulation": "регламент",
    "misc": "прочее",
}

NO_DOMAIN = "не разобрано по направлениям"

#: Место, которое держим под строку «… и ещё N документов». Длиннее её не
#: бывает: «  - … и ещё 100000 документов» — это 30 знаков.
_TAIL_RESERVE = 34


class LibraryCatalog:
    """Опись библиотеки с кэшем: перечитывается, только когда она изменилась.

    Запрос дешёвый, но карта собирается на КАЖДЫЙ вопрос помощника, а
    вопросов в отделе за день сотни. Кэш сбрасывается сам, как только
    меняется число документов или фрагментов, — отдельно звать никого не
    надо, и забыть про сброс негде.
    """

    def __init__(self, repos: "Repositories"):
        self.repos = repos
        self._lock = threading.Lock()
        self._rows: List[Dict[str, Any]] = []
        self._stamp: tuple = ()

    def rows(self) -> List[Dict[str, Any]]:
        stamp = self._version()
        with self._lock:
            if stamp == self._stamp:
                return list(self._rows)
        rows = self.repos.documents.catalog()
        with self._lock:
            self._rows = list(rows)
            self._stamp = stamp
        return list(rows)

    def _version(self) -> tuple:
        documents = int(self.repos.db.scalar("SELECT count(*) FROM documents") or 0)
        # Одного числа документов мало: переиндексация меняет нарезку, не
        # трогая их число, и карта осталась бы со старыми объёмами.
        chunks = int(self.repos.db.scalar("SELECT count(*) FROM chunks") or 0)
        return (documents, chunks)


def render_catalog(rows: Sequence[Dict[str, Any]], *, domain_titles: Dict[str, str],
                   prefer: Sequence[str] = (), limit: int = CATALOG_CHARS) -> str:
    """Карта библиотеки текстом, уложенная в ``limit`` знаков.

    ``prefer`` — направления, которых коснулся поиск: их документы называются
    первыми. Полки перечисляются всегда, даже если на документы места не
    осталось: «сорок два документа по спутникам» — уже ответ на вопрос
    «есть ли у нас про это хоть что-нибудь».
    """
    if not rows:
        return "(библиотека пуста — источников для ответа нет)"

    shelves = _group(rows)
    order = _order(shelves, prefer)

    # Сначала — строки полок: они короткие и обязательные.
    lines: Dict[str, List[str]] = {}
    used = 0
    for domain in order:
        head = _shelf_line(domain, shelves[domain], domain_titles)
        lines[domain] = [head]
        used += len(head) + 1

    # Потом — сами документы, пока есть место. Идём по полкам в порядке
    # предпочтения, внутри полки — от толстых к тонким: толстый документ
    # чаще оказывается тем самым справочником.
    #
    # Место под строку «и ещё N документов» держим заранее. Если добавлять
    # её по остаточному принципу, она не влезает ровно тогда, когда нужнее
    # всего: список обрывается на середине полки, и модель считает, что
    # это вся библиотека.
    named: Dict[str, int] = {domain: 0 for domain in order}
    for domain in order:
        reserve = _TAIL_RESERVE if len(shelves[domain]) > 1 else 0
        for row in shelves[domain]:
            line = "  - " + _document_line(row)
            if used + len(line) + 1 + reserve > limit:
                break
            lines[domain].append(line)
            named[domain] += 1
            used += len(line) + 1
            if named[domain] == len(shelves[domain]):
                reserve = 0

    blocks = []
    for domain in order:
        rest = len(shelves[domain]) - named[domain]
        if rest > 0 and named[domain]:
            tail = f"  - … и ещё {rest} {_documents_word(rest)}"
            lines[domain].append(tail)
            used += len(tail) + 1
        blocks.append("\n".join(lines[domain]))
    return "\n".join(blocks)


def _group(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    shelves: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        shelves.setdefault(row.get("domain") or "", []).append(dict(row))
    for items in shelves.values():
        items.sort(key=lambda row: (-int(row.get("chunks") or 0), str(row.get("title") or "")))
    return shelves


def _order(shelves: Dict[str, List[Dict[str, Any]]], prefer: Sequence[str]) -> List[str]:
    """Полки поиска — первыми, дальше по объёму. Безымянная полка — последней."""
    wanted = [item for item in prefer if item in shelves]
    rest = [name for name in shelves if name not in wanted and name]
    rest.sort(key=lambda name: -len(shelves[name]))
    order = wanted + rest
    if "" in shelves and "" not in order:
        order.append("")
    return order


def _shelf_line(domain: str, items: Sequence[Dict[str, Any]],
                domain_titles: Dict[str, str]) -> str:
    title = domain_titles.get(domain) or (domain or NO_DOMAIN)
    chunks = sum(int(row.get("chunks") or 0) for row in items)
    return (f"{title} — {len(items)} {_documents_word(len(items))}, "
            f"{chunks} {_fragments_word(chunks)}:")


def _document_line(row: Dict[str, Any]) -> str:
    parts = [str(row.get("title") or row.get("doc_id") or "без названия")]
    kind = DOC_TYPE_TITLES.get(str(row.get("doc_type") or ""), row.get("doc_type"))
    tail = [str(kind)] if kind else []
    if row.get("year"):
        tail.append(f"{row['year']} г.")
    if tail:
        parts.append(" (" + ", ".join(tail) + ")")
    return "".join(parts)


def _plural(count: int, one: str, few: str, many: str) -> str:
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def _documents_word(count: int) -> str:
    return _plural(count, "документ", "документа", "документов")


def _fragments_word(count: int) -> str:
    return _plural(count, "фрагмент", "фрагмента", "фрагментов")
