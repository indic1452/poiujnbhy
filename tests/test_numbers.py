import unittest

import _bootstrap  # noqa: F401
from reportgen import numbers


class NormalizeTests(unittest.TestCase):
    def test_equivalent_forms_collapse(self):
        for raw in ("13.7", "13,7", "13.70", "+13.7"):
            self.assertEqual(numbers.normalize(raw), "13.7")

    def test_thousand_separators(self):
        self.assertEqual(numbers.normalize("1 200"), "1200")

    def test_exponent(self):
        self.assertEqual(numbers.normalize("3.2e-3"), "0.0032")

    def test_garbage(self):
        self.assertIsNone(numbers.normalize("дБ"))


class OutOfRangeTests(unittest.TestCase):
    """Числа за пределами разумного порядка."""

    def test_huge_exponent_does_not_crash_the_report(self):
        # decimal.Overflow ронял сохранение секции ошибкой 500 — и потом
        # любую проверку этого отчёта.
        self.assertEqual("1e999999999", numbers.normalize("1e999999999"))

    def test_huge_exponent_is_not_unfolded_into_a_megabyte(self):
        # «1e999999» в обычной записи — миллион знаков, и такая строка
        # ложилась в множество чисел на каждое вхождение в тексте.
        self.assertLess(len(numbers.normalize("1e999999") or ""), 40)

    def test_tiny_number_does_not_become_zero(self):
        # Молча превращался в 0 и мог совпасть с настоящим нулём из фактов.
        self.assertNotEqual("0", numbers.normalize("1e-999999999"))

    def test_infinity_is_not_a_measurement(self):
        for raw in ("Infinity", "-inf", "NaN"):
            with self.subTest(raw=raw):
                self.assertIsNone(numbers.normalize(raw))


class ThousandsSeparatorTests(unittest.TestCase):
    """Пробел разделяет разряды не всегда."""

    def test_groups_of_three_are_one_number(self):
        self.assertEqual({"1000000"}, numbers.extract("число 1 000 000 записано"))
        self.assertEqual({"2400"}, numbers.extract("частота 2 400 МГц"))

    def test_other_groups_are_separate_numbers(self):
        """«12 34» — два числа, а не 1234.

        Склеивалось всё подряд: верификатор блокировал отчёт из-за числа,
        которого никто не писал, а настоящие 12 и 34 не проверял вовсе.
        """
        self.assertEqual({"12", "34"}, numbers.extract("значения 12 34 идут подряд"))
        self.assertEqual({"2024", "100"}, numbers.extract("в 2024 100 пакетов"))

    def test_narrow_and_non_breaking_spaces_group_too(self):
        for space in ("\u00a0", "\u202f", "\u2007"):
            with self.subTest(space=repr(space)):
                self.assertEqual({"1000"}, numbers.extract(f"итого 1{space}000 штук"))


class ExtractTests(unittest.TestCase):
    def test_extracts_measurements(self):
        text = "ОСШ составил 13,7 дБ при полосе 1.85 МГц и смещении -1.9 кГц."
        self.assertEqual(numbers.extract(text), {"13.7", "1.85", "-1.9"})

    def test_ignores_structural_numbers(self):
        text = "## 3.1. Результаты\n\n1. Первый пункт\n\nСм. рис. 2 и [S4].\n"
        self.assertEqual(numbers.extract(text), set())

    def test_structural_mode_keeps_everything(self):
        self.assertIn("3.1", numbers.extract("## 3.1. Результаты", structural=True))

    def test_from_object_walks_nested(self):
        found = numbers.extract_from_object({"a": [1.5, {"b": "порог 7 дБ"}]})
        self.assertEqual(found, {"1.5", "7"})

    def test_derived_forms(self):
        self.assertEqual(numbers.derived_forms({"-34", "4.0"}), {"34", "4"})


class CodeParamsTests(unittest.TestCase):
    """Параметры кодов и скремблеров: «RS (204,188,12)»."""

    def test_parameters_are_separate_numbers(self):
        # Читалось как 204.188 и 12: ни длины кодового слова, ни числа
        # информационных символов верификатор не видел вовсе.
        found = numbers.extract("код Рида-Соломона RS (204,188,12)")
        self.assertEqual({"204", "188", "12"}, found)

    def test_the_space_after_the_comma_changes_nothing(self):
        """Из описи код переписывают и с пробелом, и без.

        Раньше «LDPC (16128,11856)» давало 16128.11856, а тот же код с
        пробелом — 16128 и 11856: отчёт блокировался числом, которое
        человек аккуратно списал с оригинала.
        """
        tight = numbers.extract("код LDPC (16128,11856)")
        loose = numbers.extract("код LDPC (16128, 11856)")
        self.assertEqual({"16128", "11856"}, tight)
        self.assertEqual(tight, loose)

    def test_scrambler_pairs(self):
        found = numbers.extract("аддитивный скремблер АС (21,19) и АС (9,5)")
        self.assertEqual({"21", "19", "9", "5"}, found)

    def test_template_name_without_a_space(self):
        found = numbers.extract('шаблон «АС(21,19).sid»')
        self.assertEqual({"21", "19"}, found)

    def test_a_decimal_in_brackets_stays_a_decimal(self):
        """«(13,7 дБ)» — дробь, а не список параметров."""
        self.assertEqual({"13.7"}, numbers.extract("затухание (13,7 дБ)"))
        self.assertEqual({"13.7"}, numbers.extract("порог составил (13,7)"))

    def test_the_name_before_the_brackets_keeps_its_digits(self):
        """В «DVB-S2 (16,8)» двойка из названия стандарта не пропадает."""
        self.assertEqual({"2", "16", "8"}, numbers.extract("стандарт DVB-S2 (16,8)"))

    def test_a_lower_case_word_before_the_brackets_is_not_a_code(self):
        # «частота (13,7)» — это дробь после обычного слова.
        self.assertEqual({"13.7"}, numbers.extract("частота (13,7)"))

    def test_a_single_letter_label_is_not_a_code(self):
        """«канал А (13,7)» — подпись канала и дробь, а не код АС(n,k).

        Сокращение кода — это две буквы и больше: RS, АС, НСК, LDPC.
        """
        self.assertEqual({"13.7"}, numbers.extract("канал А (13,7) дБ"))
        self.assertEqual({"13.7"}, numbers.extract("точка B (13,7)"))


class HyphenTests(unittest.TestCase):
    """Дефис после буквы или цифры — не минус."""

    def test_modulation_is_not_a_negative_number(self):
        self.assertEqual({"16"}, numbers.extract("вид модуляции КАМ-16"))
        self.assertEqual({"4"}, numbers.extract("вид модуляции ФМ-4С"))
        self.assertEqual({"2"}, numbers.extract("транспортный поток MPEG-2 TS"))

    def test_a_range_gives_two_positive_numbers(self):
        # Давало 17919 и −18737, и отчёт блокировался числом −18737.
        self.assertEqual({"17919", "18737"}, numbers.extract("стволы 17919-18737"))

    def test_a_real_minus_survives(self):
        self.assertEqual({"-1.9"}, numbers.extract("смещение -1.9 кГц"))
        self.assertEqual({"-3"}, numbers.extract("запас (-3 дБ)"))


class RealLetterTests(unittest.TestCase):
    """Строки из настоящего ответа отдела."""

    def test_the_reed_solomon_paragraph(self):
        text = (
            "с применением каскадного кодирования НСК 5/6 и Рида-Соломона "
            "RS (204,188,12) на основе стандарта DVB-S."
        )
        self.assertEqual({"5", "6", "204", "188", "12"}, numbers.extract(text))

    def test_the_frame_paragraph(self):
        text = (
            "Кадр передачи содержит два кодовых слова кода LDPC (16128,11856) "
            "в сочетании с аддитивным скремблированием АС (21,19) и АС (9,5)."
        )
        self.assertEqual(
            {"16128", "11856", "21", "19", "9", "5"}, numbers.extract(text)
        )


if __name__ == "__main__":

    unittest.main()
