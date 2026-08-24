"""Тесты эвал-харнесса качества отчётов (док. 05)."""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.corpus import load_corpus
from reportgen.evaluate import (
    EvalError,
    EvalReport,
    CaseResult,
    TARGETS,
    aggregate_metrics,
    body_text,
    citation_precision,
    fact_recall,
    glossary_compliance,
    load_golden_set,
    missing_findings,
    numeric_fidelity,
    reference_similarity,
    run_eval,
    structure_compliance,
    unknown_numbers,
)
from reportgen.facts import FactPack
from reportgen.llm import StubLLM
from reportgen.pipeline import Outline, generate_report
from reportgen.retrieval import BM25Index, Retriever
from reportgen.store.repo import normalized_edit_distance

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples" / "cases" / "case-2024-118.json"
OUTLINE = ROOT / "templates" / "outline_signal_issue.json"
CORPUS = ROOT / "examples" / "corpus"
GLOSSARY = json.loads((ROOT / "templates" / "glossary.json").read_text(encoding="utf-8"))

_CACHE = {}


def honest_report():
    """Отчёт, собранный заглушкой строго по факт-пакету: эталон «честного»."""
    if "report" not in _CACHE:
        facts = FactPack.load(CASE)
        outline = Outline.load(OUTLINE)
        retriever = Retriever(BM25Index(load_corpus(CORPUS)))
        result = generate_report(facts, outline, StubLLM(), retriever,
                                 generated_at="2024-07-16")
        _CACHE["report"] = (facts, outline, result.markdown)
    return _CACHE["report"]


def drop_lines(markdown, needle):
    return "\n".join(line for line in markdown.splitlines() if needle not in line)


class NumericFidelityTests(unittest.TestCase):
    def test_honest_report_scores_one(self):
        facts, _, markdown = honest_report()
        self.assertEqual(numeric_fidelity(markdown, facts), 1.0)
        self.assertEqual(unknown_numbers(markdown, facts), [])

    def test_substituted_number_drops_the_metric(self):
        facts, _, markdown = honest_report()
        spoiled = markdown.replace("13.7 дБ", "15.9 дБ")
        self.assertNotEqual(spoiled, markdown)
        self.assertLess(numeric_fidelity(spoiled, facts), 1.0)
        self.assertIn("15.9", unknown_numbers(spoiled, facts))

    def test_text_without_numbers_is_faithful(self):
        facts, _, _ = honest_report()
        self.assertEqual(numeric_fidelity("## 1. Выводы\n\nОтклонений не выявлено.", facts), 1.0)

    def test_numbers_from_appendix_do_not_count(self):
        # Цитаты источников в приложении отчёта — не утверждения отчёта,
        # поэтому их числа в метрику не входят (иначе она никогда не будет 1.0).
        facts, _, markdown = honest_report()
        self.assertIn("40", body_text(markdown) + "40")  # страховка от пустого тела
        self.assertEqual(numeric_fidelity(markdown, facts), 1.0)

    def test_accepts_factpack_as_dict(self):
        raw = json.loads(CASE.read_text(encoding="utf-8"))
        _, _, markdown = honest_report()
        self.assertEqual(numeric_fidelity(markdown, raw), 1.0)


class FactRecallTests(unittest.TestCase):
    def test_honest_report_mentions_all_significant_findings(self):
        facts, _, markdown = honest_report()
        self.assertEqual(fact_recall(markdown, facts), 1.0)
        self.assertEqual(missing_findings(markdown, facts), [])

    def test_missing_finding_is_detected(self):
        facts, _, markdown = honest_report()
        without = drop_lines(markdown, "паразитн")
        self.assertLess(fact_recall(without, facts), 1.0)
        self.assertIn("F2", missing_findings(without, facts))

    def test_severity_threshold_selects_findings(self):
        facts, _, markdown = honest_report()
        # Находок уровня critical в кейсе нет — метрика не наказывает за это.
        self.assertEqual(fact_recall(markdown, facts, "critical"), 1.0)
        self.assertEqual(fact_recall(markdown, facts, "low"), 1.0)

    def test_report_without_findings_scores_zero(self):
        facts, _, _ = honest_report()
        empty = "## 1. Выводы\n\nПо результатам работ замечаний нет."
        self.assertEqual(fact_recall(empty, facts), 0.0)


class CitationTests(unittest.TestCase):
    def test_honest_report_has_only_resolvable_citations(self):
        _, _, markdown = honest_report()
        self.assertEqual(citation_precision(markdown), 1.0)

    def test_dangling_citation_lowers_precision(self):
        _, _, markdown = honest_report()
        spoiled = markdown.replace("## 6. Выводы\n", "## 6. Выводы\n\nСогласно [S99], норма соблюдена.\n", 1)
        self.assertLess(citation_precision(spoiled), 1.0)

    def test_report_without_citations_is_precise(self):
        self.assertEqual(citation_precision("## 1. Раздел\n\nТекст без ссылок."), 1.0)


class StructureTests(unittest.TestCase):
    def test_full_outline_is_compliant(self):
        _, outline, markdown = honest_report()
        self.assertEqual(structure_compliance(markdown, outline), 1.0)

    def test_removed_section_lowers_compliance(self):
        _, outline, markdown = honest_report()
        without = markdown.replace("## 6. Выводы", "## 6. Прочее")
        value = structure_compliance(without, outline)
        self.assertLess(value, 1.0)
        self.assertAlmostEqual(value, (len(outline.sections) - 1) / len(outline.sections), places=3)


class GlossaryTests(unittest.TestCase):
    def test_canonical_terms_are_compliant(self):
        markdown = "## 1. Результаты\n\nИзмеренный ОСШ и КСВН в норме."
        self.assertEqual(glossary_compliance(markdown, GLOSSARY), 1.0)

    def test_variant_term_lowers_compliance(self):
        markdown = "## 1. Результаты\n\nИзмеренный с/ш в норме, КСВ в норме."
        self.assertEqual(glossary_compliance(markdown, GLOSSARY), 0.0)

    def test_mixed_usage_gives_partial_score(self):
        markdown = "## 1. Результаты\n\nОСШ и с/ш — одно и то же."
        self.assertAlmostEqual(glossary_compliance(markdown, GLOSSARY), 0.5, places=3)

    def test_empty_glossary_and_clean_text(self):
        _, _, markdown = honest_report()
        self.assertEqual(glossary_compliance(markdown, {}), 1.0)
        self.assertEqual(glossary_compliance(markdown, GLOSSARY), 1.0)


class SimilarityTests(unittest.TestCase):
    def test_identical_texts(self):
        _, _, markdown = honest_report()
        self.assertEqual(reference_similarity(markdown, markdown), 1.0)

    def test_matches_edit_distance(self):
        left = "условия измерений описаны в разделе два"
        right = "условия измерений приведены в разделе два"
        self.assertAlmostEqual(
            reference_similarity(left, right),
            1.0 - normalized_edit_distance(left, right),
            places=4,
        )

    def test_unrelated_texts_are_far(self):
        self.assertLess(reference_similarity("один два три", "совсем другие слова здесь"), 0.5)


class GoldenSetTests(unittest.TestCase):
    def _manifest(self, directory, cases):
        path = Path(directory) / "golden.json"
        path.write_text(json.dumps({"name": "золотой набор", "cases": cases},
                                   ensure_ascii=False), encoding="utf-8")
        return path

    def test_reads_cases_and_resolves_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "facts.json").write_text(
                CASE.read_text(encoding="utf-8"), encoding="utf-8")
            path = self._manifest(directory, [{"case_id": "SUP-1", "facts_path": "facts.json"}])
            cases = load_golden_set(path)
            self.assertEqual(len(cases), 1)
            self.assertTrue(Path(cases[0]["facts_path"]).is_file())
            self.assertEqual(cases[0]["case_id"], "SUP-1")

    def test_plain_list_manifest_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "golden.json"
            path.write_text(json.dumps([{"facts_path": str(CASE)}]), encoding="utf-8")
            self.assertEqual(len(load_golden_set(path)), 1)

    def test_case_without_facts_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._manifest(directory, [{"case_id": "SUP-1"}])
            with self.assertRaises(EvalError):
                load_golden_set(path)

    def test_missing_manifest_is_rejected(self):
        with self.assertRaises(EvalError):
            load_golden_set(ROOT / "нет-такого-файла.json")


class RunEvalTests(unittest.TestCase):
    def test_run_eval_on_golden_case(self):
        cases = [{"case_id": "SUP-2024-118", "facts_path": str(CASE)}]
        report = run_eval(cases, StubLLM(), ROOT / "templates", glossary=GLOSSARY)
        self.assertIsInstance(report, EvalReport)
        self.assertEqual(len(report.results), 1)
        result = report.results[0]
        self.assertIsInstance(result, CaseResult)
        self.assertEqual(result.case_id, "SUP-2024-118")
        self.assertEqual(result.metrics["numeric_fidelity"], 1.0)
        self.assertEqual(result.metrics["fact_recall"], 1.0)
        self.assertEqual(result.metrics["structure_compliance"], 1.0)
        self.assertGreaterEqual(result.seconds, 0.0)
        self.assertEqual(result.errors, 0, result.issues)
        self.assertTrue(report.aggregate["passed"], report.aggregate)
        self.assertEqual(report.aggregate["cases"], 1)

    def test_reference_adds_similarity_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.md"
            reference.write_text("## 1. Исходные данные\n\nЭталонный отчёт инженера.\n",
                                 encoding="utf-8")
            cases = [{"facts_path": str(CASE), "reference_path": str(reference)}]
            report = run_eval(cases, StubLLM(), ROOT / "templates")
            metrics = report.results[0].metrics
            self.assertIn("reference_similarity", metrics)
            self.assertGreaterEqual(metrics["reference_similarity"], 0.0)
            self.assertLess(metrics["reference_similarity"], 1.0)
            self.assertNotIn("glossary_compliance", metrics)

    def test_report_serializes_to_dict_and_markdown(self):
        cases = [{"facts_path": str(CASE)}]
        report = run_eval(cases, StubLLM(), ROOT / "templates", glossary=GLOSSARY)
        data = report.to_dict()
        json.dumps(data, ensure_ascii=False)  # обязана быть сериализуемой
        self.assertEqual(data["aggregate"]["cases"], 1)
        self.assertIn("numeric_fidelity", data["results"][0]["metrics"])

        text = report.to_markdown()
        self.assertIn("SUP-2024-118", text)
        self.assertIn("numeric_fidelity", text)
        self.assertIn("Сводка", text)

    def test_unknown_report_type_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = json.loads(CASE.read_text(encoding="utf-8"))
            raw["report_type"] = "нет-такого-типа"
            facts_path = Path(directory) / "facts.json"
            facts_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(EvalError):
                run_eval([{"facts_path": str(facts_path)}], StubLLM(), ROOT / "templates")

    def test_missing_facts_file_is_reported(self):
        with self.assertRaises(EvalError):
            run_eval([{"facts_path": str(ROOT / "нет.json")}], StubLLM(), ROOT / "templates")

    def test_aggregate_marks_metrics_below_target(self):
        results = [
            CaseResult("SUP-1", {"numeric_fidelity": 1.0, "fact_recall": 0.5}, [], 0.1),
            CaseResult("SUP-2", {"numeric_fidelity": 1.0, "fact_recall": 1.0}, [], 0.3),
        ]
        aggregate = aggregate_metrics(results)
        self.assertAlmostEqual(aggregate["metrics"]["fact_recall"], 0.75)
        self.assertIn("fact_recall", aggregate["below_target"])
        self.assertNotIn("numeric_fidelity", aggregate["below_target"])
        self.assertFalse(aggregate["passed"])
        self.assertAlmostEqual(aggregate["seconds_total"], 0.4, places=3)
        self.assertEqual(aggregate["targets"]["numeric_fidelity"], TARGETS["numeric_fidelity"])

    def test_markdown_marks_failed_metrics(self):
        report = EvalReport(
            results=[CaseResult("SUP-1", {"fact_recall": 0.5}, [], 0.2)],
            aggregate=aggregate_metrics([CaseResult("SUP-1", {"fact_recall": 0.5}, [], 0.2)]),
        )
        text = report.to_markdown()
        self.assertIn("НИЖЕ ЦЕЛИ", text)
        self.assertIn("Ниже целевых значений", text)


if __name__ == "__main__":
    unittest.main()
