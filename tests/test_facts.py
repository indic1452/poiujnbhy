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
