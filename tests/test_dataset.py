"""Тесты сбора обучающего набора из правок инженеров (док. 03, 3.5 и 3.7)."""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.dataset import (
    DatasetError,
    SCHEMA_VERSION,
    build_dpo_examples,
    build_sft_examples,
    dataset_stats,
    export_dataset,
    read_jsonl,
    reverse_annotate,
    split_by_case,
    write_jsonl,
)
from reportgen.pipeline import Outline
from reportgen.prompts import SYSTEM_PROMPT
from reportgen.store.db import Database
from reportgen.store.repo import Repositories

ROOT = Path(__file__).resolve().parents[1]
OUTLINE = ROOT / "templates" / "outline_signal_issue.json"

CONTEXT = {
    "header": "Обращение: SUP-2024-118\nЗаказчик: ЗАКАЗЧИК_07",
    "instruction": "Опиши условия и методику измерений.",
    "title": "Условия и методика измерений",
    "target_words": 220,
    "style": "деловой технический",
    "facts": "| Параметр | Значение |\n|---|---|\n| Отношение сигнал/шум | 13.7 дБ |",
    "sources": "[S1] Методика измерений, с. 4 — Спектр\nЗанимаемая полоса измеряется методом 99 %.",
}

WORDS = [f"слово{i}" for i in range(30)]
DRAFT = " ".join(WORDS)
FINAL_COSMETIC = " ".join(WORDS[:-1] + ["правка"])          # расстояние ≈ 0.033
FINAL_REWRITTEN = " ".join(f"иное{i}" for i in range(30))   # расстояние 1.0


def make_repos() -> Repositories:
    return Repositories(Database(":memory:"))


def add_pair(repos, case_id="SUP-2024-118", section_id="conditions", *,
             draft=DRAFT, final=FINAL_REWRITTEN, context=None,
             report_type="signal_issue", title="Условия и методика измерений"):
    return repos.edits.add(
        case_id=case_id,
        report_id=None,
        report_type=report_type,
        section_id=section_id,
        section_title=title,
        draft=draft,
        final=final,
        facts_digest="abc123",
        context=CONTEXT if context is None else context,
    )


def make_examples(cases=4, sections=3):
    """Готовые примеры без базы — для проверки деления и статистики."""
    examples = []
    for case in range(cases):
        for section in range(sections):
            examples.append({
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"промпт {case}/{section}"},
                    {"role": "assistant", "content": f"текст раздела {section}"},
                ],
                "degraded": False,
                "meta": {
                    "case_id": f"SUP-{case:03d}",
                    "report_type": "signal_issue",
                    "section_id": f"s{section}",
                    "edit_distance": 0.2,
                },
            })
    return examples


class SFTTests(unittest.TestCase):
    def setUp(self):
        self.repos = make_repos()

    def tearDown(self):
        self.repos.close()

    def test_example_has_three_messages_with_final_as_answer(self):
        add_pair(self.repos)
        example = build_sft_examples(self.repos)[0]
        roles = [message["role"] for message in example["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant"])
        self.assertEqual(example["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertEqual(example["messages"][2]["content"], FINAL_REWRITTEN)

    def test_prompt_is_restored_from_context(self):
        add_pair(self.repos)
        user = build_sft_examples(self.repos)[0]["messages"][1]["content"]
        self.assertIn("### ФАКТЫ", user)
        self.assertIn("Отношение сигнал/шум | 13.7 дБ", user)
        self.assertIn("[S1] Методика измерений", user)
        self.assertIn("Условия и методика измерений", user)
        self.assertIn("Опиши условия и методику измерений.", user)
        self.assertIn("220 слов", user)

    def test_cosmetic_edits_are_filtered_out(self):
        add_pair(self.repos, section_id="scope", final=FINAL_COSMETIC)
        self.assertEqual(len(build_sft_examples(self.repos, min_distance=0.02)), 1)
        self.assertEqual(len(build_sft_examples(self.repos, min_distance=0.05)), 0)

    def test_identical_draft_and_final_gives_no_example(self):
        add_pair(self.repos, final=DRAFT)
        self.assertEqual(build_sft_examples(self.repos), [])

    def test_filter_by_report_type(self):
        add_pair(self.repos, case_id="SUP-1", report_type="signal_issue")
        add_pair(self.repos, case_id="SUP-2", report_type="protocol_anomaly")
        examples = build_sft_examples(self.repos, report_types=["protocol_anomaly"])
        self.assertEqual([e["meta"]["case_id"] for e in examples], ["SUP-2"])

    def test_limit_caps_number_of_examples(self):
        for index in range(5):
            add_pair(self.repos, case_id=f"SUP-{index}")
        self.assertEqual(len(build_sft_examples(self.repos, limit=2)), 2)

    def test_meta_carries_case_and_distance(self):
        add_pair(self.repos)
        meta = build_sft_examples(self.repos)[0]["meta"]
        self.assertEqual(meta["case_id"], "SUP-2024-118")
        self.assertEqual(meta["section_id"], "conditions")
        self.assertEqual(meta["facts_digest"], "abc123")
        self.assertAlmostEqual(meta["edit_distance"], 1.0, places=3)

    def test_empty_context_produces_degraded_example(self):
        add_pair(self.repos, context={})
        example = build_sft_examples(self.repos)[0]
        self.assertTrue(example["degraded"])
        user = example["messages"][1]["content"]
        self.assertIn("SUP-2024-118", user)
        self.assertIn("Условия и методика измерений", user)
        self.assertIn("не сохранены вместе с правкой", user)

    def test_full_context_is_not_degraded(self):
        add_pair(self.repos)
        self.assertFalse(build_sft_examples(self.repos)[0]["degraded"])


class DPOTests(unittest.TestCase):
    def setUp(self):
        self.repos = make_repos()

    def tearDown(self):
        self.repos.close()

    def test_chosen_is_final_and_rejected_is_draft(self):
        add_pair(self.repos)
        example = build_dpo_examples(self.repos)[0]
        self.assertEqual(set(example) >= {"prompt", "chosen", "rejected"}, True)
        self.assertEqual(example["chosen"], FINAL_REWRITTEN)
        self.assertEqual(example["rejected"], DRAFT)
        self.assertIn("### ЗАДАНИЕ", example["prompt"])

    def test_small_edits_are_below_default_threshold(self):
        add_pair(self.repos, final=FINAL_COSMETIC)
        self.assertEqual(build_dpo_examples(self.repos), [])
        self.assertEqual(len(build_dpo_examples(self.repos, min_distance=0.01)), 1)

    def test_pairs_without_draft_are_skipped(self):
        add_pair(self.repos, draft="   ")
        self.assertEqual(build_dpo_examples(self.repos), [])


class SplitTests(unittest.TestCase):
    def test_sections_of_one_case_never_split(self):
        examples = make_examples(cases=4, sections=3)
        train, test = split_by_case(examples, test_ratio=0.25, seed=0)
        train_cases = {e["meta"]["case_id"] for e in train}
        test_cases = {e["meta"]["case_id"] for e in test}
        self.assertEqual(train_cases & test_cases, set())
        self.assertEqual(len(train) + len(test), len(examples))
        self.assertEqual(len(train_cases | test_cases), 4)

    def test_every_case_keeps_all_its_sections(self):
        examples = make_examples(cases=5, sections=3)
        train, test = split_by_case(examples, test_ratio=0.4, seed=7)
        for part in (train, test):
            counts = {}
            for example in part:
                key = example["meta"]["case_id"]
                counts[key] = counts.get(key, 0) + 1
            self.assertTrue(all(count == 3 for count in counts.values()), counts)

    def test_split_is_deterministic_for_seed(self):
        examples = make_examples()
        first = split_by_case(examples, seed=3)
        second = split_by_case(examples, seed=3)
        self.assertEqual([e["meta"] for e in first[1]], [e["meta"] for e in second[1]])

    def test_single_case_stays_in_train(self):
        examples = make_examples(cases=1, sections=4)
        train, test = split_by_case(examples)
        self.assertEqual(len(train), 4)
        self.assertEqual(test, [])

    def test_zero_ratio_gives_empty_test(self):
        train, test = split_by_case(make_examples(), test_ratio=0.0)
        self.assertEqual(len(train), 12)
        self.assertEqual(test, [])

    def test_invalid_ratio_is_rejected(self):
        with self.assertRaises(DatasetError):
            split_by_case(make_examples(), test_ratio=1.0)


class JsonlTests(unittest.TestCase):
    def test_write_jsonl_returns_count_and_lines_parse(self):
        examples = make_examples(cases=2, sections=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sub" / "train.jsonl"
            written = write_jsonl(examples, path)
            self.assertEqual(written, 4)
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 4)
            self.assertEqual([json.loads(line)["meta"]["case_id"] for line in lines],
                             [e["meta"]["case_id"] for e in examples])
            self.assertEqual(len(read_jsonl(path)), 4)

    def test_cyrillic_is_written_as_is(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.jsonl"
            write_jsonl([{"text": "Измерения выполнены"}], path)
            raw = path.read_bytes()
            self.assertIn("Измерения выполнены".encode("utf-8"), raw)
            self.assertNotIn(b"\\u04", raw)

    def test_stats_summarize_dataset(self):
        stats = dataset_stats(make_examples(cases=3, sections=2))
        self.assertEqual(stats["examples"], 6)
        self.assertEqual(stats["cases"], 3)
        self.assertEqual(stats["degraded"], 0)
        self.assertEqual(stats["report_types"], {"signal_issue": 6})
        self.assertEqual(stats["sections"], {"s0": 3, "s1": 3})
        self.assertAlmostEqual(stats["edit_distance"]["mean"], 0.2)

    def test_stats_of_empty_dataset(self):
        stats = dataset_stats([])
        self.assertEqual(stats["examples"], 0)
        self.assertEqual(stats["cases"], 0)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.repos = make_repos()
        for case in range(4):
            for section in ("scope", "results", "conclusions"):
                add_pair(self.repos, case_id=f"SUP-{case:03d}", section_id=section)

    def tearDown(self):
        self.repos.close()

    def test_export_sft_writes_files_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = export_dataset(self.repos, directory, kind="sft")
            out = Path(directory)
            self.assertTrue((out / "train.jsonl").is_file())
            self.assertTrue((out / "test.jsonl").is_file())
            self.assertTrue((out / "manifest.json").is_file())

            self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)
            self.assertEqual(manifest["kind"], "sft")
            self.assertIn("created_at", manifest)
            self.assertEqual(manifest["filters"]["min_distance"], 0.02)
            self.assertEqual(manifest["counts"]["total"], 12)
            self.assertEqual(
                manifest["counts"]["train"] + manifest["counts"]["test"], 12
            )
            saved = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["counts"], manifest["counts"])

    def test_export_keeps_cases_apart_in_files(self):
        with tempfile.TemporaryDirectory() as directory:
            export_dataset(self.repos, directory, kind="sft")
            train = read_jsonl(Path(directory) / "train.jsonl")
            test = read_jsonl(Path(directory) / "test.jsonl")
            train_cases = {e["meta"]["case_id"] for e in train}
            test_cases = {e["meta"]["case_id"] for e in test}
            self.assertTrue(test_cases)
            self.assertEqual(train_cases & test_cases, set())

    def test_export_dpo(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = export_dataset(self.repos, directory, kind="dpo")
            self.assertEqual(manifest["kind"], "dpo")
            self.assertEqual(manifest["filters"]["min_distance"], 0.05)
            examples = read_jsonl(Path(directory) / "train.jsonl")
            self.assertTrue(examples)
            self.assertIn("chosen", examples[0])
            self.assertIn("rejected", examples[0])

    def test_unknown_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DatasetError):
                export_dataset(self.repos, directory, kind="grpo")


class FakeLLM:
    """Модель-заглушка: возвращает заранее заданный ответ и помнит промпт."""

    name = "fake"

    def __init__(self, answer="", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def complete(self, system, user, *, max_tokens=1200, temperature=0.2):
        self.calls.append({"system": system, "user": user})
        if self.error is not None:
            raise self.error
        return self.answer


ANNOTATION = {
    "case_id": "SUP-2023-041",
    "report_type": "signal_issue",
    "customer": "1274",
    "measurements": {
        "snr": {"title": "Отношение сигнал/шум", "value": 11.2, "unit": "дБ",
                "method": "по внеполосным участкам спектра"},
        "evm": {"title": "Модуль вектора ошибки", "value": 15.8, "unit": "%"},
    },
    "findings": [
        {"id": "F1", "severity": "high", "title": "Качество модуляции ниже допустимого",
         "evidence": ["evm", "snr"]},
    ],
}

REPORT = "# Технический отчёт\n\n**Обращение:** SUP-2023-041\n\n## 1. Результаты\n\nОСШ 11.2 дБ, EVM 15.8 %.\n"


class ReverseAnnotateTests(unittest.TestCase):
    def test_plain_json_is_parsed_and_validated(self):
        llm = FakeLLM(json.dumps(ANNOTATION, ensure_ascii=False))
        data = reverse_annotate(llm, REPORT, "signal_issue")
        self.assertEqual(data["case_id"], "SUP-2023-041")
        self.assertEqual(data["measurements"]["snr"]["value"], 11.2)
        self.assertEqual(data["findings"][0]["severity"], "high")

    def test_json_in_backtick_fence_is_parsed(self):
        answer = "Вот факт-пакет:\n\n```json\n" + json.dumps(ANNOTATION, ensure_ascii=False) + "\n```\n"
        data = reverse_annotate(FakeLLM(answer), REPORT, "signal_issue")
        self.assertEqual(sorted(data["measurements"]), ["evm", "snr"])

    def test_report_type_is_forced_and_case_id_restored(self):
        payload = dict(ANNOTATION)
        payload.pop("case_id")
        payload["report_type"] = "что-то своё"
        data = reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                                REPORT, "signal_issue")
        self.assertEqual(data["report_type"], "signal_issue")
        self.assertEqual(data["case_id"], "SUP-2023-041")

    def test_measurements_given_as_list_are_normalized(self):
        payload = dict(ANNOTATION)
        payload["measurements"] = [
            {"key": "snr", "title": "Отношение сигнал/шум", "value": 11.2, "unit": "дБ"},
            {"key": "evm", "title": "Модуль вектора ошибки", "value": 15.8, "unit": "%"},
        ]
        data = reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                                REPORT, "signal_issue")
        self.assertEqual(data["measurements"]["evm"]["value"], 15.8)

    def test_prompt_mentions_outline_keys(self):
        llm = FakeLLM(json.dumps(ANNOTATION, ensure_ascii=False))
        reverse_annotate(llm, REPORT, "signal_issue", outline=Outline.load(OUTLINE))
        user = llm.calls[0]["user"]
        self.assertIn("occupied_bandwidth", user)
        self.assertIn("Результаты измерений", user)
        self.assertIn(REPORT.strip().splitlines()[0], user)

    def test_sender_number_from_an_old_report_survives(self):
        payload = dict(ANNOTATION)
        payload["customer"] = "12/345"
        data = reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                                REPORT, "signal_issue")
        self.assertEqual(data["customer"], "12/345")

    def test_organisation_name_instead_of_sender_does_not_kill_the_report(self):
        """Старый отчёт с «Заказчик: ПАО Ростелеком» — годная обучающая пара.

        Отправитель не измерение: ни одно число отчёта из него не берут.
        Ронять из-за него весь разбор нельзя, иначе обратная разметка
        отвергнет весь архив прошлых лет — там номеров ещё не писали.
        """
        payload = dict(ANNOTATION)
        payload["customer"] = "ПАО «Ростелеком»"
        data = reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                                REPORT, "signal_issue")
        self.assertEqual(data["customer"], "")

    def test_digits_are_not_dug_out_of_an_organisation_name(self):
        """«Связь-21» не должна стать отправителем 21: его никто не присылал."""
        payload = dict(ANNOTATION)
        payload["customer"] = "ООО «Связь-21»"
        data = reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                                REPORT, "signal_issue")
        self.assertEqual(data["customer"], "")

    def test_non_json_answer_raises(self):
        with self.assertRaises(DatasetError):
            reverse_annotate(FakeLLM("извините, не могу"), REPORT, "signal_issue")

    def test_empty_answer_raises(self):
        with self.assertRaises(DatasetError):
            reverse_annotate(FakeLLM("   "), REPORT, "signal_issue")

    def test_schema_violation_raises_with_reason(self):
        payload = json.loads(json.dumps(ANNOTATION))
        payload["findings"][0]["severity"] = "катастрофа"
        with self.assertRaises(DatasetError) as ctx:
            reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                             REPORT, "signal_issue")
        self.assertIn("severity", str(ctx.exception))

    def test_evidence_pointing_nowhere_raises(self):
        payload = json.loads(json.dumps(ANNOTATION))
        payload["findings"][0]["evidence"] = ["ber"]
        with self.assertRaises(DatasetError):
            reverse_annotate(FakeLLM(json.dumps(payload, ensure_ascii=False)),
                             REPORT, "signal_issue")

    def test_empty_report_raises(self):
        with self.assertRaises(DatasetError):
            reverse_annotate(FakeLLM("{}"), "   ", "signal_issue")

    def test_model_failure_is_wrapped(self):
        llm = FakeLLM(error=RuntimeError("соединение отвергнуто"))
        with self.assertRaises(DatasetError) as ctx:
            reverse_annotate(llm, REPORT, "signal_issue")
        self.assertIn("соединение отвергнуто", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
