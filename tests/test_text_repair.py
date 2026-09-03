"""Разбор литературы: то, что приходит из PDF, и то, что должно получиться.

Библиотека отдела собрана из чужих файлов: стандарты в PDF от издательства,
книги в DjVu, методики в старом .doc. Текст в них почти никогда не лежит
таким, каким его набирали, и разница видна не глазом, а поиском: документ в
списке есть, страниц много, а найти в нём нельзя ничего.

Каждая проверка здесь — про один такой случай, встреченный в настоящей
библиотеке.
"""

import unittest
from pathlib import Path

import _bootstrap  # noqa: F401

from reportgen.ingest.convert import _repairs_note, convert_file
from reportgen.ingest.text_repair import (
    drop_running_titles,
    repair_report,
    repair_text,
    spell_out_super_and_subscripts,
    unify_homoglyphs,
    unify_ligatures,
    unify_math_letters,
)

FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf")


class LigatureTests(unittest.TestCase):
    """«ﬁ» — один знак, и слово с ним не совпадает со словом без него."""

    def test_the_fi_ligature_becomes_two_letters(self):
        self.assertEqual("filter", unify_ligatures("ﬁlter"))

    def test_the_longer_ligatures_too(self):
        self.assertEqual("efficiency", unify_ligatures("eﬃciency"))
        self.assertEqual("shuffle", unify_ligatures("shuﬄe"))

    def test_a_word_without_ligatures_is_untouched(self):
        self.assertEqual("фильтрация", unify_ligatures("фильтрация"))


class InvisibleTests(unittest.TestCase):
    """Мягкий перенос виден только поиску — и тот его не прощает."""

    def test_a_soft_hyphen_inside_a_word_is_dropped(self):
        self.assertEqual("радиорелейный", repair_text("радио­релейный"))

    def test_a_zero_width_space_is_dropped(self):
        self.assertEqual("коэффициент", repair_text("коэф​фициент"))

    def test_a_non_breaking_space_becomes_a_space(self):
        self.assertEqual("3 дБ", repair_text("3 дБ"))

    def test_a_decomposed_letter_is_put_back_together(self):
        """«й» из «и» и краткой — для поиска это другое слово."""
        self.assertEqual("линейный", repair_text("линейный"))


class ScriptTests(unittest.TestCase):
    """Степень и индекс — часть обозначения, а не украшение."""

    def test_a_power_is_written_with_a_caret(self):
        self.assertEqual("R^2", spell_out_super_and_subscripts("R²"))

    def test_an_index_is_written_with_an_underscore(self):
        self.assertEqual("H_2O", spell_out_super_and_subscripts("H₂O"))

    def test_a_two_digit_power_stays_one_power(self):
        self.assertEqual("10^12", spell_out_super_and_subscripts("10¹²"))

    def test_plain_text_is_untouched(self):
        self.assertEqual("R2 и H2O", spell_out_super_and_subscripts("R2 и H2O"))


class MathLetterTests(unittest.TestCase):
    """Обозначение из стандарта, набранное математическим шрифтом."""

    def test_a_math_italic_letter_becomes_a_plain_one(self):
        self.assertEqual("P", unify_math_letters("\U0001d443"))

    def test_ordinary_letters_are_untouched(self):
        self.assertEqual("Рвх", unify_math_letters("Рвх"))


class HomoglyphTests(unittest.TestCase):
    """«Мoдуляция» с латинской «o» выглядит безупречно и не находится ничем."""

    def test_a_latin_letter_inside_a_russian_word_is_replaced(self):
        fixed = unify_homoglyphs("Мoдуляция")
        self.assertEqual("Модуляция", fixed)
        self.assertNotIn("o", fixed)

    def test_a_russian_letter_inside_a_latin_word_is_replaced(self):
        self.assertEqual("filter", unify_homoglyphs("filtеr".replace("е", "e")))
        self.assertEqual("carrier", unify_homoglyphs("саrrier"))

    def test_a_genuinely_mixed_word_is_left_alone(self):
        """«Wi-Fi» и «ГОСТ Р ISO» — смешение настоящее, править нечего."""
        for word in ("КАМqam", "Мбитs", "Модуляцияqpsk"):
            self.assertEqual(word, unify_homoglyphs(word))

    def test_a_mix_with_an_unmatchable_letter_is_left_alone(self):
        # «g» русского двойника не имеет — значит, слово настоящее смешанное.
        self.assertEqual("Модуляцияg", unify_homoglyphs("Модуляцияg"))

    def test_pure_words_are_untouched(self):
        self.assertEqual("Модуляция filter", unify_homoglyphs("Модуляция filter"))


class ReportTests(unittest.TestCase):
    """Человеку нужно знать не «текст поправлен», а что именно с ним сделали."""

    def test_what_was_fixed_is_counted(self):
        raw = "ﬁlter Мoдуляция R² радио­релейный"
        report = repair_report(raw, repair_text(raw))
        self.assertEqual(1, report["ligatures"])
        self.assertEqual(1, report["homoglyphs"])
        self.assertEqual(1, report["scripts"])
        self.assertEqual(1, report["invisible"])

    def test_a_clean_text_has_nothing_to_report(self):
        self.assertEqual({}, repair_report("Обычный русский текст.",
                                           "Обычный русский текст."))

    def test_the_note_agrees_with_the_number_in_russian(self):
        self.assertIn("1 лигатура", _repairs_note({"ligatures": 1}))
        self.assertIn("2 лигатуры", _repairs_note({"ligatures": 2}))
        self.assertIn("5 лигатур", _repairs_note({"ligatures": 5}))
        self.assertIn("11 лигатур", _repairs_note({"ligatures": 11}))
        self.assertIn("21 лигатура", _repairs_note({"ligatures": 21}))


class RunningTitleTests(unittest.TestCase):
    """Колонтитул на каждой из шестисот страниц книги — в каждом фрагменте.

    Смысл фрагмента разбавляется названием отдела, а поиск по названию отдела
    находит всю библиотеку целиком.
    """

    #: Тело страницы книги: у настоящих абзацев нет ничего общего между собой.
    BODY = [
        "Занимаемая полоса частот измеряется методом процентов мощности",
        "Порог срабатывания приёмника установлен на минус девяносто шесть",
        "Отношение сигнал-шум на входе демодулятора не ниже двенадцати",
    ]

    def pages(self, count: int):
        """Страницы книги: колонтитул сверху, номер снизу, тело в середине."""
        return [
            ["2 специальный отдел — Методика измерений"]
            + [f"{line}, страница {number}, абзац {index} по счёту."
               for index, line in enumerate(self.BODY)]
            + [str(16 + number)]
            for number in range(count)
        ]

    def test_a_repeated_header_and_page_number_are_dropped(self):
        cleaned, dropped = drop_running_titles(self.pages(6))
        for page in cleaned:
            self.assertEqual(3, len(page), page)
            self.assertNotIn("2 специальный отдел", " ".join(page))
        joined = " ".join(dropped)
        self.assertIn("2 специальный отдел", joined)

    def test_the_page_number_does_not_save_the_footer(self):
        """Колонтитулы отличаются только номером — номер и обезличиваем."""
        _, dropped = drop_running_titles(self.pages(4))
        self.assertTrue(any("2 специальный отдел" in line for line in dropped))

    def test_a_short_document_is_left_alone(self):
        """На двух страницах о «повторяющемся колонтитуле» говорить нельзя."""
        cleaned, dropped = drop_running_titles(self.pages(2))
        self.assertEqual([], dropped)
        self.assertEqual(5, len(cleaned[0]))

    def test_a_line_repeated_only_twice_out_of_ten_stays(self):
        """Раздел, начинающийся одинаково на двух страницах, — не колонтитул."""
        pages = self.pages(10)
        pages[0][0] = pages[1][0] = "Раздел 5. Измерения"
        cleaned, _ = drop_running_titles(pages)
        self.assertIn("Раздел 5. Измерения", cleaned[0])
        self.assertIn("Раздел 5. Измерения", cleaned[1])

    def test_a_long_line_is_never_a_footer(self):
        """Абзац, повторённый на каждой странице, — это текст, а не колонтитул."""
        long_line = "Настоящая методика распространяется на измерения " * 3
        pages = self.pages(8)
        for page in pages:
            page[0] = long_line
        cleaned, dropped = drop_running_titles(pages)
        self.assertIn(long_line, cleaned[0])
        self.assertNotIn(long_line, " ".join(dropped))

    def test_a_footer_does_not_take_the_header_with_it(self):
        """Колонтитул держится своего края: подвал не отменяет шапку."""
        pages = [["Шапка раздела", f"тело раздела номер {n} и далее по тексту",
                  "Подвал методики"] for n in range(8)]
        cleaned, _ = drop_running_titles(pages)
        for number, page in enumerate(cleaned):
            self.assertEqual([f"тело раздела номер {number} и далее по тексту"],
                             page)

    def test_a_number_inside_a_line_is_not_a_page_number(self):
        """«Таблица 3.1» и «Таблица 3.7» — разные строки, а не колонтитул.

        Обезличивать все цифры подряд нельзя: тогда пункт «Порог минус 96 дБм»
        стал бы тем же, что «Порог минус 12 дБм», и оба ушли бы в колонтитул.
        """
        pages = [[f"Таблица 3.{n} — результаты измерений", "тело страницы",
                  str(20 + n)] for n in range(8)]
        cleaned, dropped = drop_running_titles(pages)
        for number, page in enumerate(cleaned):
            self.assertIn(f"Таблица 3.{number} — результаты измерений", page)
        # А вот номер страницы и повторяющееся тело — колонтитулы.
        self.assertTrue(dropped)

    def test_a_heading_is_never_a_running_title(self):
        """Заголовок раздела восстановлен по кеглю и несёт структуру книги."""
        pages = [["# Глава 4. Измерения", f"тело {n}", "стр."] for n in range(8)]
        cleaned, _ = drop_running_titles(pages)
        for page in cleaned:
            self.assertIn("# Глава 4. Измерения", page)

    def test_a_footer_in_the_middle_of_the_page_stays(self):
        """Ищем колонтитул только по краям: в середине это содержание."""
        pages = [["начало", "второе", "серёдка", "четвёртое", "конец"]
                 for _ in range(8)]
        for page in pages:
            page[2] = "Таблица 1"
        cleaned, _ = drop_running_titles(pages)
        self.assertIn("Таблица 1", cleaned[0])


@unittest.skipUnless(FONT.is_file(), "нет шрифта с кириллицей для сборки PDF")
class PdfReadingTests(unittest.TestCase):
    """Настоящий PDF, собранный так, как их делают вёрстка и TeX."""

    @classmethod
    def setUpClass(cls):
        try:
            import pymupdf
        except ImportError:                      # pragma: no cover
            raise unittest.SkipTest("нет pymupdf")
        import tempfile

        cls._tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls._tmp.name) / "kniga.pdf"

        doc = pymupdf.open()
        regular = pymupdf.Font(fontfile=str(FONT))
        bold = pymupdf.Font(fontfile=str(FONT_BOLD))
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname="rus", fontfile=str(FONT))
        page.insert_font(fontname="rusb", fontfile=str(FONT_BOLD))

        def put(target, x, y, text, size=11, font="rus"):
            target.insert_text((x, y), text, fontname=font, fontsize=size)
            metric = bold if font == "rusb" else regular
            return metric.text_length(text, fontsize=size)

        put(page, 60, 70, "Глава 4. Измерение занимаемой полосы",
            size=16, font="rusb")
        # Абзац, набранный словами вразбивку: часть слов полужирные, и в
        # файле между ними нет ни одного пробела — только координаты.
        x = 60.0
        for word, font in [("Занимаемая", "rus"), ("полоса", "rusb"),
                           ("частот", "rus"), ("измеряется", "rus"),
                           ("методом", "rusb"), ("99", "rus"),
                           ("процентов", "rus"), ("мощности", "rus"),
                           ("сигнала.", "rus")]:
            metric = bold if font == "rusb" else regular
            x += put(page, x, 110, word, font=font)
            x += metric.text_length(" ", fontsize=11)
        # Перенос слова через строку.
        put(page, 60, 140, "Спутниковая линия связи работает в диапазоне радио-")
        put(page, 60, 155, "релейных частот и требует прямой видимости.")
        # Формула: индекс и степень отдельными спанами мельче строки.
        x = 60.0
        x += put(page, x, 190, "P")
        x += put(page, x, 193, "вх", size=7)
        x += put(page, x, 190, " = P")
        x += put(page, x, 193, "изл", size=7)
        x += put(page, x, 190, " · G / (4πR")
        x += put(page, x, 186, "2", size=7)
        put(page, x, 190, ")")
        # Лигатура и латиница внутри русского слова.
        put(page, 60, 220, "ﬁlter Мoдуляция коэф­фициент")
        put(page, 60, 800, "2 специальный отдел — Методика измерений", size=8)
        put(page, 520, 800, "17", size=8)

        for number, line in enumerate([
                "Порог срабатывания приёмника равен минус 96 дБм.",
                "Отношение сигнал-шум на входе демодулятора не ниже 12 дБ.",
                "Допустимая нестабильность несущей — 1 кГц за сутки."], start=18):
            extra = doc.new_page(width=595, height=842)
            extra.insert_font(fontname="rus", fontfile=str(FONT))
            put(extra, 60, 100, line)
            put(extra, 60, 800, "2 специальный отдел — Методика измерений", size=8)
            put(extra, 520, 800, str(number), size=8)

        doc.save(str(cls.path))
        doc.close()
        cls.result = convert_file(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_words_are_separated_even_without_a_single_space_in_the_file(self):
        """Главная беда библиотеки: «Занимаемаяполосачастот…».

        Спан в PDF обрывается на каждой смене шрифта, и абзац с выделенными
        словами приходил из файла склеенным целиком. Документ ложился в
        библиотеку целым на вид и не находился ни по одному слову.
        """
        self.assertIn("Занимаемая полоса частот измеряется методом 99 процентов "
                      "мощности сигнала.", self.result.text)
        self.assertNotIn("Занимаемаяполоса", self.result.text)

    def test_a_word_broken_by_a_hyphen_is_put_back_together(self):
        self.assertIn("радиорелейных частот", self.result.text)
        self.assertNotIn("радио- релейных", self.result.text)

    def test_a_power_survives_the_reading(self):
        """«4πR2» читается как число 2 при R, а не как квадрат."""
        self.assertIn("(4πR^2)", self.result.text)

    def test_an_index_survives_the_reading(self):
        self.assertIn("P_вх", self.result.text)
        self.assertIn("P_изл", self.result.text)

    def test_the_ligature_and_the_latin_letter_are_repaired(self):
        self.assertIn("filter", self.result.text)
        self.assertIn("Модуляция", self.result.text)
        self.assertIn("коэффициент", self.result.text)

    def test_the_running_footer_is_out_of_every_page(self):
        self.assertNotIn("2 специальный отдел — Методика измерений",
                         self.result.text)
        self.assertEqual(["2 специальный отдел — Методика измерений 17"],
                         self.result.meta.get("running_titles"))

    def test_the_body_text_stays_whole(self):
        for line in ("Порог срабатывания приёмника равен минус 96 дБм.",
                     "Отношение сигнал-шум на входе демодулятора не ниже 12 дБ.",
                     "Допустимая нестабильность несущей — 1 кГц за сутки."):
            self.assertIn(line, self.result.text)

    def test_the_heading_is_recognised_by_its_size(self):
        self.assertIn("# Глава 4. Измерение занимаемой полосы", self.result.text)

    def test_what_was_repaired_is_told_to_the_person(self):
        note = " ".join(self.result.warnings)
        self.assertIn("поправлен при чтении", note)
        self.assertIn("лигатур", note)

    def test_a_well_read_document_is_not_called_glued(self):
        self.assertNotIn("склеен", " ".join(self.result.warnings))


class EveryFormatTests(unittest.TestCase):
    """Лигатуры и латиница в русских словах — не только беда PDF.

    Так же они приходят из DOCX, из старого .doc, из распознанного скана и из
    DjVu. Починка стоит в общем месте — на выходе любого разборщика.
    """

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def test_a_plain_text_file_is_repaired_too(self):
        path = self.folder / "metodika.txt"
        path.write_text("ﬁlter и Мoдуляция R² радио­релейный",
                        encoding="utf-8")
        result = convert_file(path)
        self.assertIn("filter", result.text)
        self.assertIn("Модуляция", result.text)
        self.assertIn("R^2", result.text)
        self.assertIn("радиорелейный", result.text)

    def test_the_person_is_told_what_was_repaired(self):
        path = self.folder / "kniga.md"
        path.write_text("# Глава\n\nﬁlter Мoдуляция", encoding="utf-8")
        result = convert_file(path)
        self.assertIn("поправлен при чтении", " ".join(result.warnings))
        self.assertEqual({"ligatures": 1, "homoglyphs": 1},
                         result.meta.get("text_repairs"))

    def test_a_clean_file_gets_no_note(self):
        path = self.folder / "chisto.txt"
        path.write_text("Обычный русский текст без единой беды.",
                        encoding="utf-8")
        result = convert_file(path)
        self.assertNotIn("поправлен при чтении", " ".join(result.warnings))
        self.assertNotIn("text_repairs", result.meta)

    def test_a_docx_is_repaired(self):
        try:
            import docx                          # noqa: F401
        except ImportError:                      # pragma: no cover
            self.skipTest("нет python-docx")
        from docx import Document

        path = self.folder / "prikaz.docx"
        document = Document()
        document.add_paragraph("ﬁlter и Мoдуляция в диапазоне")
        document.save(str(path))
        result = convert_file(path)
        self.assertIn("filter", result.text)
        self.assertIn("Модуляция", result.text)


@unittest.skipUnless(FONT.is_file(), "нет шрифта с кириллицей для сборки PDF")
class TwoColumnTests(unittest.TestCase):
    """Книга в две колонки: строки колонок чередовались, и текст не читался."""

    def build(self, columns: bool):
        try:
            import pymupdf
        except ImportError:                      # pragma: no cover
            raise unittest.SkipTest("нет pymupdf")
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "dve.pdf"
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_font(fontname="rus", fontfile=str(FONT))
        left = ["Первая мысль левой колонки.", "Вторая мысль левой колонки.",
                "Третья мысль левой колонки."]
        right = ["Первая мысль правой колонки.", "Вторая мысль правой колонки.",
                 "Третья мысль правой колонки."]
        for index, line in enumerate(left):
            x = 40 if columns else 40
            page.insert_text((x, 100 + index * 60), line,
                             fontname="rus", fontsize=9)
        for index, line in enumerate(right):
            x = 320 if columns else 40
            y = 100 + index * 60 if columns else 300 + index * 60
            page.insert_text((x, y), line, fontname="rus", fontsize=9)
        doc.save(str(path))
        doc.close()
        return convert_file(path).text

    def test_a_column_is_read_whole_before_the_next_one(self):
        text = self.build(columns=True)
        positions = [text.index(f"{word} мысль левой")
                     for word in ("Первая", "Вторая", "Третья")]
        self.assertEqual(sorted(positions), positions, text)
        self.assertLess(text.index("Третья мысль левой"),
                        text.index("Первая мысль правой"), text)

    def test_a_single_column_page_keeps_its_order(self):
        text = self.build(columns=False)
        self.assertLess(text.index("Первая мысль левой"),
                        text.index("Первая мысль правой"), text)


if __name__ == "__main__":
    unittest.main()
