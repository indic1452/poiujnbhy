"""Карта библиотеки: что вообще лежит на полках.

Помощник отвечал по двенадцати найденным фрагментам и о существовании
остальной библиотеки не знал. Спросили другими словами — поиск не нашёл, и
помощник отвечает по памяти, хотя в соседнем томе это расписано. Или
наоборот: по теме в библиотеке нет ничего, а сказать об этом неоткуда.
"""

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.store.db import Database
from reportgen.store.repo import Repositories
from reportgen.web.catalog import (
    CATALOG_SHELVES_CHARS,
    LibraryCatalog,
    render_catalog,
)

ROOT = Path(__file__).resolve().parents[1]

TITLES = {
    "satellite": "Спутниковые линии связи",
    "microwave": "Радиорелейные линии связи",
    "signal": "Обработка сигналов",
    "misc": "Прочее",
}


def rows(*items):
    """Строки описи: (направление, название, тип, год, фрагментов)."""
    return [
        {"domain": domain, "title": title, "doc_type": doc_type,
         "year": year, "chunks": chunks, "doc_id": title, "status": "current"}
        for domain, title, doc_type, year, chunks in items
    ]


LIBRARY = rows(
    ("satellite", "Справочник по спутниковой связи", "literature", 2019, 240),
    ("satellite", "DVB-S2 EN 302 307-1", "standards", 2014, 90),
    ("satellite", "Паспорт модема CX-900", "datasheets", None, 12),
    ("microwave", "Радиорелейные линии: расчёт пролёта", "literature", 2008, 130),
    ("microwave", "MiniLink TN. Описание", "datasheets", None, 40),
    ("signal", "Помехоустойчивое кодирование", "literature", 2011, 300),
)


class ShelfTests(unittest.TestCase):
    def test_every_shelf_is_named_with_its_numbers(self):
        text = render_catalog(LIBRARY, domain_titles=TITLES)
        self.assertIn("Спутниковые линии связи — 3 документа, 342 фрагмента:", text)
        self.assertIn("Радиорелейные линии связи — 2 документа, 170 фрагментов:", text)
        self.assertIn("Обработка сигналов — 1 документ, 300 фрагментов:", text)

    def test_documents_are_named_with_type_and_year(self):
        text = render_catalog(LIBRARY, domain_titles=TITLES)
        self.assertIn("Справочник по спутниковой связи (литература, 2019 г.)", text)
        self.assertIn("Паспорт модема CX-900 (даташиты)", text)

    def test_the_thick_document_comes_first(self):
        """Толстый том чаще и есть тот самый справочник."""
        text = render_catalog(LIBRARY, domain_titles=TITLES)
        shelf = text.split("Радиорелейные")[0]
        self.assertLess(shelf.index("Справочник по спутниковой"),
                        shelf.index("Паспорт модема"))

    def test_the_searched_shelf_goes_first(self):
        text = render_catalog(LIBRARY, domain_titles=TITLES, prefer=["microwave"])
        self.assertTrue(text.startswith("Радиорелейные линии связи"))

    def test_an_unnamed_shelf_is_called_by_its_name_not_by_a_blank(self):
        text = render_catalog(rows(("", "Без направления", "misc", None, 3)),
                              domain_titles=TITLES)
        self.assertIn("не разобрано по направлениям", text)

    def test_an_empty_library_says_so(self):
        self.assertIn("пуста", render_catalog([], domain_titles=TITLES))


class BudgetTests(unittest.TestCase):
    """Карта не должна вытеснять сами фрагменты."""

    BIG = rows(*[
        ("satellite", f"Документ по спутниковой связи номер {index}",
         "literature", 2000 + index % 20, 100 - index % 50)
        for index in range(200)
    ])

    def test_the_map_fits_the_limit(self):
        for limit in (400, 800, 1500, 2500):
            with self.subTest(limit=limit):
                text = render_catalog(self.BIG, domain_titles=TITLES, limit=limit)
                self.assertLessEqual(len(text), limit)

    def test_the_shelf_line_survives_even_a_tiny_limit(self):
        """«Сорок два документа по спутникам» — уже ответ на вопрос.

        Даже когда на названия места нет, полка обязана остаться: иначе
        помощник решит, что библиотека пуста, и станет отвечать по памяти.
        """
        text = render_catalog(self.BIG, domain_titles=TITLES,
                              limit=CATALOG_SHELVES_CHARS)
        self.assertIn("Спутниковые линии связи — 200 документов", text)

    def test_the_rest_is_counted_not_dropped_silently(self):
        text = render_catalog(self.BIG, domain_titles=TITLES, limit=800)
        self.assertIn("и ещё", text)

    def test_a_shelf_is_never_cut_in_half(self):
        # Обрубок «Спутниковые линии свя» хуже, чем отсутствие карты.
        text = render_catalog(self.BIG, domain_titles=TITLES, limit=800)
        for line in text.splitlines():
            self.assertTrue(line.endswith(":") or line.startswith("  - "), line)


class PluralTests(unittest.TestCase):
    """Числительные по-русски: «1 документ», «2 документа», «11 документов»."""

    def line(self, count):
        items = rows(*[("satellite", f"Том {i}", "literature", None, 1)
                       for i in range(count)])
        return render_catalog(items, domain_titles=TITLES).splitlines()[0]

    def test_forms(self):
        for count, word in ((1, "1 документ,"), (2, "2 документа,"),
                            (5, "5 документов,"), (11, "11 документов,"),
                            (21, "21 документ,"), (102, "102 документа,")):
            with self.subTest(count=count):
                self.assertIn(word, self.line(count))


class CacheTests(unittest.TestCase):
    """Карта собирается на каждый вопрос — перечитывать её каждый раз незачем."""

    def build(self):
        repos = Repositories(Database(":memory:"))
        return repos, LibraryCatalog(repos)

    def add(self, repos, doc_id, chunks=1):
        from reportgen.corpus import Chunk

        document = repos.documents.upsert(
            doc_id=doc_id, doc_type="literature", title=f"Том {doc_id}",
            source_path=f"{doc_id}.md", sha256=doc_id.ljust(64, "0"),
            domain="satellite")
        repos.chunks.replace_for_document(document, [
            Chunk(chunk_id=f"{doc_id}#{i:04d}", doc_id=doc_id,
                  doc_type="literature", title_path=[], text="текст")
            for i in range(chunks)
        ])

    def test_the_second_call_does_not_go_to_the_database(self):
        repos, catalog = self.build()
        self.add(repos, "a")
        calls = []
        original = repos.documents.catalog
        repos.documents.catalog = lambda: (calls.append(1) or original())
        catalog.rows()
        catalog.rows()
        self.assertEqual(1, len(calls))

    def test_a_new_document_refreshes_the_map_by_itself(self):
        repos, catalog = self.build()
        self.add(repos, "a")
        self.assertEqual(1, len(catalog.rows()))
        self.add(repos, "b")
        self.assertEqual(2, len(catalog.rows()))

    def test_recutting_the_same_document_refreshes_the_map(self):
        """Переиндексация меняет нарезку, не трогая число документов."""
        repos, catalog = self.build()
        self.add(repos, "a", chunks=2)
        self.assertEqual(2, catalog.rows()[0]["chunks"])
        self.add(repos, "a", chunks=5)
        self.assertEqual(5, catalog.rows()[0]["chunks"])

    def test_a_superseded_document_is_not_offered(self):
        """Направлять инженера к заменённому документу незачем."""
        repos, catalog = self.build()
        self.add(repos, "a")
        repos.documents.set_status("a", "superseded", superseded_by="b")
        self.assertEqual([], catalog.rows())


if __name__ == "__main__":
    unittest.main()
