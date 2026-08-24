import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.corpus import load_corpus
from reportgen.facts import FactPack
from reportgen.llm import StubLLM
from reportgen.pipeline import (
    Outline,
    SourceRegistry,
    check_facts_coverage,
    generate_report,
)
from reportgen.retrieval import BM25Index, Retriever

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples" / "cases" / "case-2024-118.json"
OUTLINE = ROOT / "templates" / "outline_signal_issue.json"
CORPUS = ROOT / "examples" / "corpus"


def build_report(**kwargs):
    facts = FactPack.load(CASE)
    outline = Outline.load(OUTLINE)
    retriever = Retriever(BM25Index(load_corpus(CORPUS)))
    return facts, outline, generate_report(
        facts, outline, StubLLM(), retriever, generated_at="2024-07-16", **kwargs
    )


class OutlineTests(unittest.TestCase):
    def test_loads_sections(self):
        outline = Outline.load(OUTLINE)
        self.assertEqual(outline.report_type, "signal_issue")
        self.assertEqual(outline.sections[0].id, "scope")

    def test_rejects_unknown_field(self):
        from reportgen.pipeline import SectionSpec

        with self.assertRaises(ValueError):
            SectionSpec.from_dict({"id": "a", "title": "t", "instruction": "i", "опечатка": 1})


class CoverageTests(unittest.TestCase):
    def test_example_case_is_complete(self):
        facts = FactPack.load(CASE)
        self.assertEqual(check_facts_coverage(facts, Outline.load(OUTLINE)), {})

    def test_reports_missing_measurement(self):
        raw = dict(FactPack.load(CASE).raw)
        raw["measurements"] = {k: v for k, v in raw["measurements"].items() if k != "snr"}
        missing = check_facts_coverage(FactPack.from_dict(raw), Outline.load(OUTLINE))
        self.assertIn("results", missing)
        self.assertIn("snr", missing["results"])


class SourceRegistryTests(unittest.TestCase):
    def test_labels_are_stable_and_unique(self):
        chunks = load_corpus(CORPUS)[:2]
        registry = SourceRegistry()
        self.assertEqual(registry.label(chunks[0]), "S1")
        self.assertEqual(registry.label(chunks[1]), "S2")
        self.assertEqual(registry.label(chunks[0]), "S1")
        self.assertIn("[S2]", registry.render_appendix())


class GenerateTests(unittest.TestCase):
    def test_all_sections_present(self):
        _, outline, result = build_report()
        for spec in outline.sections:
            self.assertIn(f"## {outline.sections.index(spec) + 1}. {spec.title}", result.markdown)

    def test_appendix_and_service_block(self):
        _, _, result = build_report()
        self.assertIn("Приложение А. Источники", result.markdown)
        self.assertIn("facts_digest", result.markdown)
        self.assertIn("ЧЕРНОВИК", result.markdown)

    def test_deterministic(self):
        first = build_report()[2].markdown
        second = build_report()[2].markdown
        self.assertEqual(first, second)

    def test_report_type_mismatch_is_rejected(self):
        facts = FactPack.load(CASE)
        outline = Outline.load(ROOT / "templates" / "outline_protocol_anomaly.json")
        with self.assertRaises(ValueError):
            generate_report(facts, outline, StubLLM())

    def test_missing_facts_are_propagated(self):
        raw = dict(FactPack.load(CASE).raw)
        raw["measurements"] = {k: v for k, v in raw["measurements"].items() if k != "snr"}
        facts = FactPack.from_dict(raw)
        result = generate_report(facts, Outline.load(OUTLINE), StubLLM(), generated_at="2024-07-16")
        self.assertIn("snr", result.missing_facts)
        self.assertIn("ТРЕБУЕТ ПРОВЕРКИ", result.markdown)

    def test_works_without_retriever(self):
        facts = FactPack.load(CASE)
        result = generate_report(facts, Outline.load(OUTLINE), StubLLM(), None, generated_at="2024-07-16")
        self.assertIn("Внешние источники не привлекались.", result.markdown)


class SecondTemplateTests(unittest.TestCase):
    """Второй тип отчёта должен работать без изменений в коде — только шаблон."""

    def test_protocol_anomaly_case(self):
        facts = FactPack.load(ROOT / "examples" / "cases" / "case-2024-206.json")
        outline = Outline.load(ROOT / "templates" / "outline_protocol_anomaly.json")
        self.assertEqual(check_facts_coverage(facts, outline), {})
        result = generate_report(facts, outline, StubLLM(), generated_at="2024-09-06")
        self.assertIn("Выявленные отклонения от эталона", result.markdown)
        self.assertEqual(result.missing_facts, [])


class PromptTests(unittest.TestCase):
    def test_prompt_carries_only_section_facts(self):
        """Секция «Условия» не должна видеть измерения из «Результатов»."""
        facts = FactPack.load(CASE)
        outline = Outline.load(OUTLINE)
        captured = {}

        class Spy(StubLLM):
            def complete(self, system, user, **kwargs):
                captured.setdefault("first", user)
                return super().complete(system, user, **kwargs)

        spec = next(s for s in outline.sections if s.id == "conditions")
        from reportgen.pipeline import generate_section

        generate_section(spec, facts, None, Spy(), registry=SourceRegistry())
        prompt = captured["first"]
        self.assertIn("Частота дискретизации", prompt)
        self.assertNotIn("Модуль вектора ошибки", prompt)


if __name__ == "__main__":
    unittest.main()
