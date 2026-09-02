import json
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


class HeaderEscapingTests(unittest.TestCase):
    """Шапка отчёта: данные факт-пакета попадают в документ дословно."""

    def test_group_number_with_markup_signs_is_escaped(self):
        from reportgen.pipeline import plain

        self.assertEqual("\\*1274\\*", plain("*1274*"))
        self.assertEqual("Р\\_168\\_М", plain("Р_168_М"))
        self.assertEqual("1274", plain("1274"), "обычный номер не трогаем лишним")
        self.assertEqual("", plain(None))

    def test_header_of_a_real_report_keeps_the_number_verbatim(self):
        """Проверяем не саму функцию, а что шапка ею пользуется."""
        import json as _json
        from reportgen.export.docx import _split_inline
        from reportgen.facts import FactPack
        from reportgen.pipeline import Outline, SourceRegistry, assemble

        raw = _json.loads((ROOT / "examples" / "cases" / "case-2024-118.json")
                          .read_text(encoding="utf-8"))
        outline = Outline.load(ROOT / "templates" / "outline_signal_issue.json")
        meta = {"generated_at": "2026-08-28", "model": "local", "outline_version": "1",
                "index_version": "docs=0", "facts_digest": "x"}
        for written in ("*1274*", "Р_168_М", "гр. 1*2*3", "[1274]"):
            with self.subTest(written=written):
                raw["group_no"] = written
                raw["equipment"] = {"модем": written}
                markdown = assemble(FactPack.from_dict(raw), outline, [],
                                    SourceRegistry(), meta)
                line = [x for x in markdown.splitlines() if "Номер группы" in x][0]
                # То же, что увидит Word после разбора разметки.
                seen = "".join(span.text for span in _split_inline(line))
                self.assertEqual(f"Номер группы: {written}", seen.strip())


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


class ParallelGenerationTests(unittest.TestCase):
    """Генерация волнами: быстрее, но результат обязан совпадать с последовательной."""

    def _report(self, parallel):
        facts = FactPack.load(CASE)
        outline = Outline.load(OUTLINE)
        retriever = Retriever(BM25Index(load_corpus(CORPUS)))
        return generate_report(
            facts, outline, StubLLM(), retriever,
            generated_at="2024-07-16", parallel_sections=parallel,
        )

    def test_wave_generation_matches_sequential(self):
        sequential = self._report(1)
        parallel = self._report(3)
        self.assertEqual(
            [s.spec.id for s in sequential.sections],
            [s.spec.id for s in parallel.sections],
        )
        self.assertEqual(len(sequential.registry.chunks), len(parallel.registry.chunks))

    def test_source_labels_are_unique_under_concurrency(self):
        result = self._report(4)
        labels = [label for label, _ in result.registry.items()]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(labels, [f"S{i}" for i in range(1, len(labels) + 1)])

    def test_zero_and_negative_fall_back_to_sequential(self):
        for value in (0, -5):
            result = self._report(value)
            self.assertEqual(len(result.sections), 7)

    def test_first_wave_has_no_previous_context(self):
        seen = []

        class Spy(StubLLM):
            def complete(self, system, user, **kwargs):
                seen.append("(это первый раздел отчёта)" in user)
                return super().complete(system, user, **kwargs)

        facts = FactPack.load(CASE)
        generate_report(facts, Outline.load(OUTLINE), Spy(), None,
                        generated_at="2024-07-16", parallel_sections=2)
        # Обе секции первой волны стартуют без предыдущего контекста, дальше он есть.
        self.assertEqual(seen[:2], [True, True])
        self.assertFalse(any(seen[2:]))


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


class StyleTests(unittest.TestCase):
    """Стиль из шапки шаблона обязан дойти до модели."""

    TEMPLATE = {
        "report_type": "проба",
        "title": "Проба",
        "style": "деловой технический, прошедшее время",
        "sections": [
            {"id": "a", "title": "А", "instruction": "пиши"},
            {"id": "b", "title": "Б", "instruction": "пиши",
             "style": "телеграфный, только перечисление"},
        ],
    }

    def outline(self):
        from reportgen.pipeline import Outline

        return Outline._from_raw(json.loads(json.dumps(self.TEMPLATE)))

    def test_a_section_inherits_the_style_of_the_template(self):
        # Стиль в шапке до модели не доходил вовсе: секция брала общий по
        # умолчанию, а автор шаблона был уверен, что задал тон всему отчёту.
        self.assertEqual("деловой технический, прошедшее время",
                         self.outline().sections[0].style)

    def test_a_section_with_its_own_style_keeps_it(self):
        self.assertEqual("телеграфный, только перечисление",
                         self.outline().sections[1].style)

    def test_a_template_without_a_style_falls_back_to_the_default(self):
        from reportgen.pipeline import DEFAULT_STYLE

        raw = json.loads(json.dumps(self.TEMPLATE))
        del raw["style"]
        self.assertEqual(DEFAULT_STYLE, Outline._from_raw(raw).sections[0].style)

    def test_the_style_reaches_the_prompt(self):
        """Проверяем не поле, а саму подсказку: между ними бывает обрыв."""
        facts = FactPack.load(CASE)
        captured = {}

        class Spy(StubLLM):
            def complete(self, system, user, **kwargs):
                captured.setdefault("first", user)
                return super().complete(system, user, **kwargs)

        from reportgen.pipeline import generate_section

        spec = self.outline().sections[0]
        generate_section(spec, facts, None, Spy(), registry=SourceRegistry())
        self.assertIn("деловой технический, прошедшее время", captured["first"])


class SeverityGuardTests(unittest.TestCase):
    """Порог находок проверяется при загрузке шаблона, а не при сборке."""

    def make(self, severity):
        from reportgen.pipeline import SectionSpec

        return SectionSpec.from_dict({
            "id": "выводы", "title": "Выводы", "instruction": "пиши",
            "findings_min_severity": severity,
        })

    def test_a_misspelled_level_is_refused_with_a_readable_message(self):
        # Опечатка доживала до сборки отчёта и роняла её сообщением
        # «tuple.index(x): x not in tuple» — ни шаблона, ни секции в нём нет.
        with self.assertRaises(ValueError) as caught:
            self.make("высокая")
        message = str(caught.exception)
        self.assertIn("выводы", message)
        self.assertIn("высокая", message)
        self.assertIn("critical", message)

    def test_every_declared_level_is_accepted(self):
        from reportgen.facts import SEVERITIES

        for severity in SEVERITIES:
            with self.subTest(severity=severity):
                self.assertEqual(severity, self.make(severity).findings_min_severity)

    def test_no_level_is_still_allowed(self):
        from reportgen.pipeline import SectionSpec

        spec = SectionSpec.from_dict({"id": "a", "title": "А", "instruction": "и"})
        self.assertIsNone(spec.findings_min_severity)


if __name__ == "__main__":


    unittest.main()
