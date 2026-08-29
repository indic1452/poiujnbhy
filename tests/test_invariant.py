"""Атаки на главный инвариант: ни одного числа мимо факт-пакета.

Каждый тест здесь воспроизводит реальную находку состязательного разбора.
Если такой тест падает — значит подписанный отчёт снова может уйти заказчику
с выдуманным числом, и это самое дорогое, что может сломаться в системе.
"""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from fastapi.testclient import TestClient

from reportgen.config import Settings
from reportgen.corpus import load_corpus
from reportgen.facts import FactPack
from reportgen.llm import StubLLM
from reportgen.store import Database, Repositories
from reportgen.verify import blocking, split_document, verify_report
from reportgen.web.app import create_app
from reportgen.web.service import ReportService

ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads((ROOT / "examples" / "cases" / "case-2024-118.json").read_text(encoding="utf-8"))


class AttackTestCase(unittest.TestCase):
    """Полный контур: кейс, сгенерированный отчёт, правка секции, попытка утвердить."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        settings = Settings.load(
            data_dir=str(tmp), db_path=":memory:", auth_enabled=False,
            templates_dir=str(ROOT / "templates"),
            glossary_path=str(ROOT / "templates" / "glossary.json"),
        )
        self.repos = Repositories(Database(":memory:"))
        by_doc: dict[str, list] = {}
        for chunk in load_corpus(ROOT / "examples" / "corpus"):
            by_doc.setdefault(chunk.doc_id, []).append(chunk)
        for doc_id, chunks in by_doc.items():
            document = self.repos.documents.upsert(
                doc_id, chunks[0].doc_type, chunks[0].meta.get("title", doc_id),
                "", "sha-" + doc_id[:10], meta=chunks[0].meta,
            )
            self.repos.chunks.replace_for_document(document, chunks)
        self.service = ReportService(repos=self.repos, settings=settings, llm=StubLLM())
        self.client = TestClient(create_app(settings, self.repos, self.service))

        created = self.client.post("/api/cases", json={
            "report_type": CASE["report_type"], "case_id": CASE["case_id"], "facts": CASE,
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.case_id = created.json()["case"]["id"]
        report = self.client.post(f"/api/cases/{self.case_id}/generate", json={})
        self.assertEqual(report.status_code, 200, report.text)
        self.report = report.json()["report"]
        self.assertEqual(self.report["errors"], 0)

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    def edit(self, text: str, index: int = 5):
        section_id = self.report["sections"][index]["section_id"]
        response = self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}", json={"text": text}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["report"]

    def approve(self):
        """Сдать отчёт и отметить проверенным.

        Проверяют только то, что сдали, поэтому сдача входит в шаг: этим
        тестам важен не порядок сдачи, а инвариант «ни одного числа мимо
        факт-пакета в проверенном документе».
        """
        sent = self.client.post(f"/api/reports/{self.report['id']}/submit")
        if sent.status_code != 200:
            return sent
        return self.client.post(f"/api/reports/{self.report['id']}/approve")

    def assert_blocked(self, report_payload):
        codes = {issue["code"] for issue in report_payload["issues"]}
        self.assertIn("unknown-number", codes, report_payload["issues"])
        self.assertGreater(report_payload["errors"], 0)
        self.assertEqual(self.approve().status_code, 409)


class HeadingAttacks(AttackTestCase):
    def test_number_in_section_heading_is_caught(self):
        report = self.edit(
            "## Итог: отношение сигнал/шум 14,7 дБ, запас по мощности 7,3 дБ\n\nТекст раздела."
        )
        self.assert_blocked(report)

    def test_forbidden_wording_in_heading_is_caught(self):
        report = self.edit("## Мы гарантируем устойчивость канала\n\nТекст раздела.")
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("forbidden-wording", codes)
        self.assertEqual(self.approve().status_code, 409)

    def test_placeholder_in_heading_is_caught(self):
        report = self.edit("## Раздел {{value}}\n\nТекст раздела.")
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("placeholder-left", codes)

    def test_honest_heading_passes(self):
        report = self.edit("## Итог\n\nИзмеренный EVM составил 12.4 %.")
        self.assertEqual(report["errors"], 0, report["issues"])
        self.assertEqual(self.approve().status_code, 200)


class FakeBoundaryAttacks(AttackTestCase):
    def test_fake_appendix_heading_does_not_create_unchecked_zone(self):
        report = self.edit(
            "## Приложение А. Источники (рабочий список)\n\n"
            "Запас по мощности составил 7,3 дБ, зафиксировано 999 срывов."
        )
        self.assert_blocked(report)

    def test_fake_appendix_does_not_legalize_numbers_elsewhere(self):
        self.edit(
            "## Приложение А. Источники (рабочий список)\n\nОпорное значение 777 дБ.", index=6
        )
        report = self.edit("Измеренное значение составило 777 дБ.", index=5)
        self.assert_blocked(report)

    def test_fake_contents_heading_is_checked(self):
        report = self.edit("## Содержание\n\nЗафиксировано 888 срывов связи.")
        self.assert_blocked(report)

    def test_empty_heading_is_checked(self):
        report = self.edit("## \n\nЗафиксировано 555 срывов связи.")
        self.assert_blocked(report)

    def test_real_appendix_still_supplies_warnings_not_errors(self):
        # 11 % — предел EVM из процитированного конспекта: это источник, а не выдумка.
        report = self.edit("Ориентировочный предел составляет 11 %.")
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("number-from-source", codes)
        self.assertEqual(report["errors"], 0)


class ServiceFieldAttacks(AttackTestCase):
    def test_number_from_field_name_is_not_allowed(self):
        # 256 приходило из имени поля sha256, 713 — из обрезанного хеша артефакта.
        report = self.edit("Размер БПФ составил 256 отсчётов, 713 срывов синхронизации.")
        self.assert_blocked(report)

    def test_full_hash_does_not_open_numbers(self):
        facts = FactPack.from_dict({
            "case_id": "SUP-2025-001", "report_type": "signal_issue",
            "artifacts": [{"name": "capture.iq", "sha256": "9f86d081884c7d659a2feaa0c55ad015"}],
            "measurements": {"snr": {"title": "ОСШ", "value": 13.7, "unit": "дБ"}},
        })
        allowed = facts.allowed_numbers()
        self.assertIn("13.7", allowed)
        for leaked in ("256", "86", "081", "015", "9"):
            self.assertNotIn(leaked, allowed, f"из хеша просочилось {leaked}")

    def test_measurement_method_numbers_stay_allowed(self):
        # Числа в описании метода — часть факт-пакета, отчёт вправе их называть.
        allowed = FactPack.load(ROOT / "examples" / "cases" / "case-2024-118.json").allowed_numbers()
        for honest in ("99", "64", "512", "20000"):
            self.assertIn(honest, allowed)


class ApprovalLifecycleAttacks(AttackTestCase):
    def test_changing_facts_revokes_approval(self):
        self.assertEqual(self.approve().status_code, 200)
        facts = json.loads(json.dumps(CASE))
        facts["measurements"] = {"center_frequency": facts["measurements"]["center_frequency"]}
        facts["findings"] = []
        response = self.client.put(f"/api/cases/{self.case_id}/facts", json={"facts": facts})
        self.assertEqual(response.status_code, 200, response.text)

        report = self.client.get(f"/api/reports/{self.report['id']}").json()["report"]
        self.assertGreater(report["errors"], 0, "устаревшие замечания не пересчитаны")
        self.assertEqual(report["status"], "draft", "подпись осталась на неверном отчёте")

    def test_explicit_verify_revokes_approval(self):
        self.assertEqual(self.approve().status_code, 200)
        self.repos.cases.update_facts(
            self.case_id,
            {"case_id": CASE["case_id"], "report_type": CASE["report_type"], "measurements": {}},
            "digest",
        )
        verified = self.client.post(f"/api/reports/{self.report['id']}/verify").json()
        self.assertGreater(verified["errors"], 0)
        report = self.client.get(f"/api/reports/{self.report['id']}").json()["report"]
        self.assertEqual(report["status"], "draft")

    def test_revocation_is_recorded_in_audit(self):
        self.approve()
        self.repos.cases.update_facts(
            self.case_id,
            {"case_id": CASE["case_id"], "report_type": CASE["report_type"], "measurements": {}},
            "digest",
        )
        self.client.post(f"/api/reports/{self.report['id']}/verify")
        actions = {entry.action for entry in self.repos.audit.list()}
        self.assertIn("report.approval.revoked", actions)

    def test_harmless_fact_change_keeps_approval(self):
        self.assertEqual(self.approve().status_code, 200)
        facts = json.loads(json.dumps(CASE))
        facts["request"] = facts["request"] + " Уточнение от заказчика."
        self.client.put(f"/api/cases/{self.case_id}/facts", json={"facts": facts})
        report = self.client.get(f"/api/reports/{self.report['id']}").json()["report"]
        self.assertEqual(report["status"], "approved")


class SplitDocumentTests(unittest.TestCase):
    """Границы разделов определяются положением, а не текстом заголовка."""

    def test_appendix_only_at_the_end(self):
        markdown = (
            "# Отчёт\n\nшапка\n\n## Содержание\n\n1. Раз\n\n"
            "## 1. Раз\n\n## Приложение А. Источники (подделка)\n\nтело\n\n"
            "## 2. Приложение А. Источники\n\nнастоящее приложение\n"
        )
        sections, appendix = split_document(markdown)
        titles = [title for title, _ in sections]
        self.assertNotIn("Содержание", titles)
        self.assertIn("Приложение А. Источники (подделка)", titles)
        self.assertIn("настоящее приложение", appendix)

    def test_contents_only_at_the_start(self):
        markdown = "# Отчёт\n\n## Содержание\n\n1. Раз\n\n## 1. Раз\n\nтело\n\n## Содержание\n\n999\n"
        sections, _ = split_document(markdown)
        titles = [title for title, _ in sections]
        self.assertEqual(titles.count("Содержание"), 1)

    def test_document_without_appendix(self):
        sections, appendix = split_document("# Отчёт\n\n## 1. Раз\n\nтело\n")
        self.assertEqual(appendix, "")
        self.assertEqual(len(sections), 1)


class DirectVerifierTests(unittest.TestCase):
    def test_explicit_sections_bypass_markdown_parsing(self):
        facts = FactPack.load(ROOT / "examples" / "cases" / "case-2024-118.json")
        issues = verify_report(
            "любой текст документа", facts, None,
            sections=[("Выводы", "Запас составил 7.3 дБ.")], appendix="",
        )
        self.assertTrue(blocking(issues))
        self.assertIn("unknown-number", {issue.code for issue in issues})


if __name__ == "__main__":
    unittest.main()
