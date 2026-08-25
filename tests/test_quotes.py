"""Цитаты из документов: то, что видят модель и инженер.

Главный сценарий работы — «какие поля в этом заголовке и что в них лежит».
Ответ на него в RFC записан БИТОВОЙ ДИАГРАММОЙ, где разрядность поля задана
шириной ячейки, и колоночными таблицами. Если при подготовке цитаты схлопнуть
пробелы внутри строки, ширины перестают что-либо значить, и модели остаётся
угадывать разрядность — ровно то, о чём её спросили.
"""

import unittest

import _bootstrap  # noqa: F401
from reportgen.corpus import tidy_quote

DIAGRAM = """   0                   1                   2                   3
   0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |V=2|P|X|  CC   |M|     PT      |       sequence number         |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
"""

TABLE = """   Field Name              Length       Value
   Frame Count             8 bits       0..255
   Sequence Number        16 bits       0..65535
"""


class DiagramTests(unittest.TestCase):
    def test_cell_widths_survive(self):
        # Ширина ячейки — это и есть разрядность поля.
        quote = tidy_quote(DIAGRAM, 900)
        self.assertIn("|V=2|P|X|  CC   |M|     PT      |", quote)

    def test_ruler_stays_aligned_with_the_cells(self):
        """Линейка разрядов набрана одиночными пробелами.

        Признаков разметки в ней нет, и построчное решение выправило бы её
        отдельно — то есть сдвинуло на символ и соврало про номера битов.
        """
        lines = tidy_quote(DIAGRAM, 900).splitlines()
        self.assertEqual(lines[0].index("0"), lines[1].index("0"))
        self.assertEqual(len(lines[2]), len(lines[3]))

    def test_common_indent_is_removed(self):
        # Общий отступ ничего не значит, а место в промпте занимает.
        self.assertFalse(tidy_quote(DIAGRAM, 900).splitlines()[2].startswith(" "))

    def test_columns_survive(self):
        quote = tidy_quote(TABLE, 900)
        self.assertIn("Frame Count             8 bits       0..255", quote)

    def test_line_breaks_survive(self):
        self.assertEqual(5, len(tidy_quote(DIAGRAM, 900).splitlines()))


class ProseTests(unittest.TestCase):
    """Обычный текст по-прежнему выравнивается."""

    def test_ragged_spacing_is_normalised(self):
        # Одна строка с рваными пробелами — это мусор распознавания, а не
        # таблица: колонок в одну строку не бывает.
        self.assertEqual(
            "Each header field consists\nof a name.",
            tidy_quote("  Each   header   field   consists\n  of a name.", 300),
        )

    def test_plain_text_is_untouched(self):
        self.assertEqual(
            "Настоящий стандарт распространяется\nна цифровые линии связи.",
            tidy_quote("Настоящий стандарт распространяется\nна цифровые линии связи.", 300),
        )

    def test_blank_lines_are_collapsed(self):
        self.assertEqual("а\n\nб", tidy_quote("а\n\n\n\nб", 100))

    def test_empty_input(self):
        self.assertEqual("", tidy_quote("   \n\n  ", 100))

    def test_limit_is_respected(self):
        quote = tidy_quote("слово " * 200, 50)
        self.assertLessEqual(len(quote), 51)
        self.assertTrue(quote.endswith("…"))


class QuoteLimitTests(unittest.TestCase):
    """Приложение к отчёту не короче того, что видела модель.

    Верификатор считает числами из источника только то, что нашёл в
    приложении. Модель законно берёт «2048 kbit/s» из символов 400–700
    плотной английской таблицы, а в приложение попадали первые 400 — и
    утверждение отчёта падало с «число отсутствует в факт-пакете» на числе,
    которое инженер видит своими глазами в источнике.
    """

    def test_limits_are_the_same(self):
        from reportgen.pipeline import PROMPT_QUOTE_CHARS
        from reportgen.web.service import APPENDIX_QUOTE_CHARS

        self.assertEqual(PROMPT_QUOTE_CHARS, APPENDIX_QUOTE_CHARS)

    def test_number_from_the_tail_reaches_the_appendix(self):
        from reportgen.corpus import Chunk
        from reportgen.pipeline import PROMPT_QUOTE_CHARS, SourceRegistry, _tidy_quote

        text = ("Each header field consists of a case-insensitive field name. " * 8
                + "The interface shall operate at 2048 kbit/s per G.703.")
        self.assertLess(len(text), PROMPT_QUOTE_CHARS)
        self.assertIn("2048", _tidy_quote(text, PROMPT_QUOTE_CHARS))

        registry = SourceRegistry()
        chunk = Chunk(chunk_id="c#0", doc_id="standards/rfc/rfc7230",
                      doc_type="standards", title_path=["RFC 7230"], text=text)
        registry.label(chunk)
        self.assertIn("2048", registry.render_appendix())

    def test_appendix_never_shorter_than_the_prompt(self):
        from reportgen.corpus import Chunk
        from reportgen.pipeline import SourceRegistry

        registry = SourceRegistry()
        text = "цифра 12345 в конце. " * 40
        chunk = Chunk(chunk_id="c#0", doc_id="d", doc_type="standards",
                      title_path=["Д"], text=text)
        registry.label(chunk)
        # Даже если попросить меньше — приложение не короче промпта.
        self.assertIn("12345", registry.render_appendix(quote_chars=50))


if __name__ == "__main__":
    unittest.main()
