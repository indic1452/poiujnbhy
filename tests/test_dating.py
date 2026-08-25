"""Год издания документа и предпочтение свежих редакций.

В библиотеке рядом лежат ГОСТ 2009 года и он же 2024-го. Формулировки в них
почти совпадают, поэтому поиск ставит их рядом — и какая попадёт в отчёт,
раньше решал случай. Ссылка на отменённую редакцию в отчёте заказчику —
прямая ошибка, поэтому год определяется при приёме и учитывается в выдаче.
"""

import datetime
import unittest

import _bootstrap  # noqa: F401
from reportgen.corpus import Chunk
from reportgen.ingest.dating import MIN_YEAR, detect_year, year_from_standard, year_from_text
from reportgen.retrieval import Hit


class YearDetectionTests(unittest.TestCase):
    def test_standard_number_wins(self):
        year, source = detect_year(title="ГОСТ Р 53363-2009", filename="gost.pdf")
        self.assertEqual((2009, "standard"), (year, source))

    def test_two_digit_standard_year(self):
        # ГОСТ 26.011-80 — это 1980-й, а не 2080-й.
        self.assertEqual(1980, year_from_standard("ГОСТ 26.011-80"))

    def test_itu_recommendation_date(self):
        self.assertEqual(1999, year_from_standard("ITU-T G.826 (02/99)"))

    def test_metadata_beats_text(self):
        year, source = detect_year(
            title="Справочник", filename="s.pdf",
            meta={"created": "2015-04-11T10:00:00"},
            text="ссылка на работу 1998 года",
        )
        self.assertEqual((2015, "metadata"), (year, source))

    def test_year_from_filename(self):
        year, source = detect_year(title="Методика измерений", filename="Методика 2018.pdf")
        self.assertEqual((2018, "title"), (year, source))

    def test_title_page_year(self):
        year, source = detect_year(
            title="Книга", filename="book.pdf",
            text="Издательство «Радио и связь»\n2003 г.\nМосква",
        )
        self.assertEqual((2003, "text"), (year, source))

    def test_page_numbers_are_not_years(self):
        # «стр. 1901 из 12» в скане — не дата издания.
        self.assertIsNone(detect_year(title="Скан", filename="s.pdf", text="стр. 1901 из 12")[0])

    def test_future_years_rejected(self):
        ahead = datetime.date.today().year + 5
        self.assertIsNone(year_from_text(f"версия {ahead} года"))

    def test_ancient_years_rejected(self):
        self.assertIsNone(year_from_text(f"год {MIN_YEAR - 1}"))

    def test_missing_year_is_not_an_error(self):
        self.assertEqual((None, ""), detect_year(title="Без даты", filename="x.pdf", text="текст"))


def hit(chunk_id: str, score: float, year: int | None, title: str = "Стандарт") -> Hit:
    meta = {"year": year} if year else {}
    meta["title"] = title
    chunk = Chunk(chunk_id=chunk_id, doc_id="d", doc_type="standards",
                  title_path=[title], text="текст", meta=meta)
    return Hit(chunk=chunk, score=score)


class FreshnessTests(unittest.TestCase):
    """Свежая редакция обгоняет старую только при близкой релевантности."""

    def setUp(self):
        from reportgen.search import DatabaseRetriever

        self.prefer = DatabaseRetriever.__dict__["_prefer_fresh"]

        class Fake:
            freshness_window = 0.12
            freshness_min_gap = 3

        self.fake = Fake()

    def run_rule(self, hits):
        return [item.chunk.chunk_id for item in self.prefer(self.fake, hits)]

    def test_newer_edition_moves_ahead(self):
        order = self.run_rule([hit("старая", 0.90, 2009), hit("новая", 0.89, 2024),
                               hit("третья", 0.50, None)])
        self.assertEqual(["новая", "старая", "третья"], order)

    def test_relevance_still_wins_when_gap_is_large(self):
        order = self.run_rule([hit("точная", 0.95, 2009), hit("свежая", 0.40, 2024)])
        self.assertEqual(["точная", "свежая"], order)

    def test_close_years_are_not_reordered(self):
        # Разница в год — это не переиздание, а соседние документы.
        order = self.run_rule([hit("первая", 0.90, 2019), hit("вторая", 0.89, 2020),
                               hit("третья", 0.30, None)])
        self.assertEqual(["первая", "вторая", "третья"], order)

    def test_unknown_years_keep_order(self):
        order = self.run_rule([hit("a", 0.90, None), hit("b", 0.89, None), hit("c", 0.10, None)])
        self.assertEqual(["a", "b", "c"], order)

    def test_single_known_year_keeps_order(self):
        order = self.run_rule([hit("a", 0.90, None), hit("b", 0.89, 2024), hit("c", 0.10, None)])
        self.assertEqual(["a", "b", "c"], order)

    def test_only_editions_of_one_document_are_swapped(self):
        """RFC старше внутренних методичек — но это не переиздания.

        RFC 7230 — 2014 года, RFC 791 — 1981-го, а методичка отдела —
        2021-го. Правило «свежее вперёд» систематически уводило точный
        первоисточник с первого места под русский документ, который просто
        новее.
        """
        # Третий результат нужен, чтобы разброс оценок был настоящим: окно
        # считается от него, и на паре из двух хитов правило не срабатывает
        # никогда — проверка была бы пустой.
        order = self.run_rule([
            hit("rfc", 0.90, 2014, "RFC 7230. Hypertext Transfer Protocol (HTTP/1.1)"),
            hit("методичка", 0.89, 2021, "Методика контроля излучения передатчика"),
            hit("третий", 0.40, None, "Прочее"),
        ])
        self.assertEqual(["rfc", "методичка", "третий"], order)

    def test_editions_of_one_standard_are_still_swapped(self):
        order = self.run_rule([
            hit("старая", 0.90, 2009, "ГОСТ Р 53363-2009. Цифровые радиорелейные линии"),
            hit("новая", 0.89, 2024, "ГОСТ Р 53363-2024. Цифровые радиорелейные линии"),
            hit("третий", 0.40, None, "Прочее"),
        ])
        self.assertEqual(["новая", "старая", "третий"], order)

    def test_rule_cannot_reshuffle_the_whole_list(self):
        # Один проход обменов соседей: документ не может подняться с конца
        # выдачи наверх, как бы свеж он ни был.
        hits = [hit(f"h{i}", 0.9 - i * 0.001, 2000 + i) for i in range(8)]
        order = self.run_rule(hits)
        self.assertNotEqual("h7", order[0])
        self.assertEqual(len(hits), len(order))
        self.assertEqual({f"h{i}" for i in range(8)}, set(order))


if __name__ == "__main__":
    unittest.main()
