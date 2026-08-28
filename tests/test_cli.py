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


class DatabaseCliTests(unittest.TestCase):
    """Команды, работающие с установленной системой (SQLite)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "test.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_useradd_and_users(self):
        code, out = run(["--db", self.db, "useradd", "--login", "admin",
                         "--role", "owner", "--password", "пароль12345"])
        self.assertEqual(code, 0, out)
        code, out = run(["--db", self.db, "users"])
        self.assertEqual(code, 0)
        self.assertIn("admin", out)
        # В списке видно должность по-русски, а не служебный owner.
        self.assertIn("Создатель системы", out)

    def test_useradd_records_department_and_team(self):
        code, out = run(["--db", self.db, "useradd", "--login", "ivanov",
                         "--role", "engineer", "--password", "пароль12345",
                         "--name", "Иванов И. И.", "--department", "Отдел связи",
                         "--team", "1 группа"])
        self.assertEqual(code, 0, out)
        from reportgen.store.repo import Repositories
        repos = Repositories.open(self.db)
        user = repos.users.by_login("ivanov")
        self.assertEqual("Отдел связи", user.department)
        self.assertEqual("1 группа", user.team)
        repos.close()

    def test_useradd_rejects_short_password(self):
        code, out = run(["--db", self.db, "useradd", "--login", "u", "--password", "123"])
        self.assertEqual(code, 1)
        self.assertIn("8 символов", out)

    def test_useradd_rejects_duplicate(self):
        run(["--db", self.db, "useradd", "--login", "admin", "--password", "пароль12345"])
        code, out = run(["--db", self.db, "useradd", "--login", "admin",
                         "--password", "пароль12345"])
        self.assertEqual(code, 1)
        self.assertIn("уже существует", out)

    def test_passwd_changes_password(self):
        run(["--db", self.db, "useradd", "--login", "u1", "--password", "пароль12345"])
        code, out = run(["--db", self.db, "passwd", "--login", "u1",
                         "--password", "другойпароль"])
        self.assertEqual(code, 0, out)

    def test_passwd_unknown_user(self):
        code, out = run(["--db", self.db, "passwd", "--login", "нет", "--password", "пароль12345"])
        self.assertEqual(code, 1)

    def test_users_on_empty_base(self):
        code, out = run(["--db", self.db, "users"])
        self.assertEqual(code, 1)
        self.assertIn("useradd", out)

    def test_library_on_empty_base(self):
        code, out = run(["--db", self.db, "library"])
        self.assertEqual(code, 1)
        self.assertIn("пуста", out)

    def test_stats_prints_json(self):
        import json as _json

        code, out = run(["--db", self.db, "stats"])
        self.assertEqual(code, 0, out)
        payload = _json.loads(out)
        self.assertEqual(payload["cases"]["total"], 0)


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
