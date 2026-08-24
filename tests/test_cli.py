import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from reportgen.cli import main

ROOT = Path(__file__).resolve().parents[1]
CASE = str(ROOT / "examples" / "cases" / "case-2024-118.json")
OUTLINE = str(ROOT / "templates" / "outline_signal_issue.json")
CORPUS = str(ROOT / "examples" / "corpus")
GLOSSARY = str(ROOT / "templates" / "glossary.json")


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(argv)
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = str(Path(tmp) / "index.json")
            report = str(Path(tmp) / "report.md")

            code, out = run(["index", "--corpus", CORPUS, "--out", index])
            self.assertEqual(code, 0, out)
            self.assertTrue(Path(index).exists())

            code, out = run(["check-facts", "--facts", CASE, "--outline", OUTLINE])
            self.assertEqual(code, 0, out)

            code, out = run([
                "generate", "--facts", CASE, "--outline", OUTLINE, "--index", index,
                "--out", report, "--llm", "stub", "--generated-at", "2024-07-16",
                "--glossary", GLOSSARY,
            ])
            self.assertEqual(code, 0, out)
            self.assertTrue(Path(report).exists())
            self.assertTrue(Path(report).with_suffix(".meta.json").exists())

            code, out = run(["verify", "--facts", CASE, "--report", report, "--outline", OUTLINE])
            self.assertEqual(code, 0, out)

    def test_verify_blocks_invented_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.md"
            report.write_text(
                "# Отчёт\n\n## 1. Выводы\n\nЗапас по мощности составил 7.3 дБ.\n",
                encoding="utf-8",
            )
            code, out = run(["verify", "--facts", CASE, "--report", str(report)])
            self.assertEqual(code, 1)
            self.assertIn("ЭКСПОРТ ЗАБЛОКИРОВАН", out)

    def test_check_facts_reports_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            import json

            raw = json.loads(Path(CASE).read_text(encoding="utf-8"))
            del raw["measurements"]["snr"]
            path = Path(tmp) / "case.json"
            path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            code, out = run(["check-facts", "--facts", str(path), "--outline", OUTLINE])
            self.assertEqual(code, 1)
            self.assertIn("snr", out)

    def test_bad_fact_pack_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text('{"report_type": "signal_issue"}', encoding="utf-8")
            code, out = run(["check-facts", "--facts", str(path), "--outline", OUTLINE])
            self.assertEqual(code, 2)
            self.assertIn("case_id", out)


if __name__ == "__main__":
    unittest.main()
