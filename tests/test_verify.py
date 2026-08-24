import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.corpus import load_corpus
from reportgen.facts import FactPack
from reportgen.llm import StubLLM
from reportgen.pipeline import Outline, generate_report
from reportgen.retrieval import BM25Index, Retriever
from reportgen.verify import blocking, parse_sections, summarize, verify_report

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples" / "cases" / "case-2024-118.json"
OUTLINE = ROOT / "templates" / "outline_signal_issue.json"
CORPUS = ROOT / "examples" / "corpus"


def clean_report():
    facts = FactPack.load(CASE)
    outline = Outline.load(OUTLINE)
    retriever = Retriever(BM25Index(load_corpus(CORPUS)))
    result = generate_report(facts, outline, StubLLM(), retriever, generated_at="2024-07-16")
    return facts, outline, result.markdown


def codes(issues, level=None):
    return {i.code for i in issues if level is None or i.level == level}


class ParseTests(unittest.TestCase):
    def test_splits_by_second_level_headings(self):
        sections = parse_sections("# T\n\nвступление\n\n## 1. Раз\n\nтело\n\n## 2. Два\n\nтело2\n")
        titles = [title for title, _ in sections]
        self.assertEqual(titles, ["", "1. Раз", "2. Два"])


class CleanReportTests(unittest.TestCase):
    def test_generated_report_has_no_errors(self):
        facts, outline, markdown = clean_report()
        issues = verify_report(markdown, facts, outline)
        self.assertFalse(blocking(issues), [str(i) for i in issues])

    def test_summarize_counts_levels(self):
        facts, outline, markdown = clean_report()
        counts = summarize(verify_report(markdown, facts, outline))
        self.assertEqual(counts["error"], 0)


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.facts, self.outline, self.markdown = clean_report()

    def _mutate(self, addition):
        return self.markdown.replace(
            "## 6. Выводы\n", f"## 6. Выводы\n\n{addition}\n", 1
        )

    def test_invented_number_is_error(self):
        issues = verify_report(self._mutate("Запас по мощности составил 7.3 дБ."), self.facts)
        self.assertIn("unknown-number", codes(issues, "error"))

    def test_number_from_fact_pack_passes(self):
        issues = verify_report(self._mutate("Измеренный EVM составил 12.4 %."), self.facts)
        self.assertNotIn("unknown-number", codes(issues, "error"))

    def test_alternative_notation_passes(self):
        # «13,70 дБ» — та же величина, что «13.7 дБ» в факт-пакете.
        issues = verify_report(self._mutate("ОСШ составил 13,70 дБ."), self.facts)
        self.assertNotIn("unknown-number", codes(issues, "error"))

    def test_number_only_from_source_is_warning(self):
        # 11 % — предел EVM из процитированного конспекта, а не измерение.
        issues = verify_report(self._mutate("Предел составляет 11 %."), self.facts)
        self.assertIn("number-from-source", codes(issues, "warning"))
        self.assertNotIn("unknown-number", codes(issues, "error"))

    def test_unknown_citation_is_error(self):
        issues = verify_report(self._mutate("Согласно методике [S99], норма соблюдена."), self.facts)
        self.assertIn("unknown-citation", codes(issues, "error"))

    def test_forbidden_wording_is_error(self):
        issues = verify_report(self._mutate("Мы гарантируем восстановление канала."), self.facts)
        self.assertIn("forbidden-wording", codes(issues, "error"))

    def test_commercial_wording_is_error(self):
        issues = verify_report(self._mutate("Стоимость работ уточняется."), self.facts)
        self.assertIn("forbidden-wording", codes(issues, "error"))

    def test_leftover_placeholder_is_error(self):
        issues = verify_report(self._mutate("Значение {{value}} требует уточнения."), self.facts)
        self.assertIn("placeholder-left", codes(issues, "error"))

    def test_needs_review_marker_is_warning(self):
        issues = verify_report(self._mutate("[ТРЕБУЕТ ПРОВЕРКИ: нет записи с эталонного плеча]"), self.facts)
        self.assertIn("needs-review", codes(issues, "warning"))
        self.assertFalse(blocking(issues))

    def test_missing_section_is_error(self):
        truncated = self.markdown.split("## 7. Рекомендации")[0]
        issues = verify_report(truncated, self.facts, self.outline)
        self.assertIn("missing-section", codes(issues, "error"))

    def test_glossary_variant_is_warning(self):
        issues = verify_report(
            self._mutate("Измеренный КСВ в норме."), self.facts, glossary={"КСВ": "КСВН"}
        )
        self.assertIn("glossary", codes(issues, "warning"))

    def test_service_block_numbers_are_ignored(self):
        # Хеш факт-пакета и версии в служебном блоке не должны считаться числами отчёта.
        self.assertNotIn("unknown-number", codes(verify_report(self.markdown, self.facts), "error"))


if __name__ == "__main__":
    unittest.main()
