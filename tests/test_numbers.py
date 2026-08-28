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


if __name__ == "__main__":
    unittest.main()
