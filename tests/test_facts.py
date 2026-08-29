import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.facts import FactPack, FactPackError

CASE = Path(__file__).resolve().parents[1] / "examples" / "cases" / "case-2024-118.json"

MINIMAL = {
    "case_id": "C-1",
    "report_type": "signal_issue",
    "measurements": {"snr": {"title": "ОСШ", "value": 13.7, "unit": "дБ"}},
}


class FactPackTests(unittest.TestCase):
    def test_loads_example(self):
        facts = FactPack.load(CASE)
        self.assertEqual(facts.case_id, "SUP-2024-118")
        self.assertIn("evm", facts.measurements)
        self.assertEqual(len(facts.findings), 3)

    def test_missing_reports_absent_keys(self):
        facts = FactPack.from_dict(MINIMAL)
        self.assertEqual(facts.missing(["snr", "evm"]), ["evm"])

    def test_requires_case_id(self):
        with self.assertRaises(FactPackError):
            FactPack.from_dict({"report_type": "x"})

    def test_wrong_shape_of_a_field_is_named_not_crashed(self):
        """Инженер правит факт-пакет как JSON прямо в интерфейсе.

        «measurements: []» вместо «measurements: {}» — обычная описка. Она
        валила запрос пятисотой ошибкой без единого слова о причине:
        AttributeError на `.items()` уходил наружу как срыв сервера.
        Надо сказать, где именно ошиблись.
        """
        cases = {
            "measurements": ([], "объектом"),
            "equipment": (["антенна"], "объектом"),
            "findings": ({"a": 1}, "списком"),
            "artifacts": (5, "списком"),
            "timeline": (5, "списком"),
            "keywords": ("раз, два", "списком"),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(field=name):
                with self.assertRaises(FactPackError) as caught:
                    FactPack.from_dict({**MINIMAL, name: value})
                message = str(caught.exception)
                self.assertIn(name, message, message)
                self.assertIn(expected, message, message)

    def test_pack_itself_must_be_an_object(self):
        with self.assertRaises(FactPackError) as caught:
            FactPack.from_dict(["case_id", "report_type"])
        self.assertIn("объектом JSON", str(caught.exception))

    def test_case_id_and_type_must_be_text(self):
        # Номер обращения числом уезжал в шапку отчёта как есть и падал
        # на разметке. Говорим сразу.
        for name in ("case_id", "report_type"):
            with self.subTest(field=name):
                with self.assertRaises(FactPackError) as caught:
                    FactPack.from_dict({**MINIMAL, name: 17})
                self.assertIn("строкой", str(caught.exception))

    def test_empty_field_is_still_allowed(self):
        # Пустое значение — это «не заполнено», а не ошибка вида.
        facts = FactPack.from_dict({**MINIMAL, "keywords": None, "findings": None})
        self.assertEqual([], facts.keywords)
        self.assertEqual([], facts.findings)

    def test_rejects_unknown_severity(self):
        raw = dict(MINIMAL, findings=[{"id": "F1", "severity": "апокалипсис", "title": "т"}])
        with self.assertRaises(FactPackError):
            FactPack.from_dict(raw)

    def test_rejects_dangling_evidence(self):
        raw = dict(MINIMAL, findings=[{"id": "F1", "severity": "high", "title": "т", "evidence": ["ber"]}])
        with self.assertRaises(FactPackError):
            FactPack.from_dict(raw)

    def test_allowed_numbers_cover_measurements(self):
        facts = FactPack.load(CASE)
        allowed = facts.allowed_numbers()
        self.assertIn("13.7", allowed)
        self.assertIn("-34", allowed)
        self.assertIn("34", allowed)      # модуль числа — допустимая форма
        self.assertNotIn("99.9", allowed)

    def test_digest_is_stable(self):
        self.assertEqual(FactPack.load(CASE).digest(), FactPack.load(CASE).digest())

    def test_findings_at_least(self):
        facts = FactPack.load(CASE)
        self.assertEqual([f.id for f in facts.findings_at_least("medium")], ["F1", "F2"])

    def test_render_measurements_is_markdown_table(self):
        table = FactPack.from_dict(MINIMAL).render_measurements(["snr"])
        self.assertIn("| ОСШ | 13.7 дБ |", table)


if __name__ == "__main__":
    unittest.main()
