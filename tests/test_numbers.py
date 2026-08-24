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
