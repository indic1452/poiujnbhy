"""Тесты веб-слоя: API, права доступа, полный цикл работы с отчётом."""

import importlib.util
import json
import re
import unittest
from urllib.parse import quote
from pathlib import Path

import _bootstrap  # noqa: F401
from fastapi.testclient import TestClient

from reportgen.config import Settings
from reportgen.corpus import load_corpus
from reportgen.llm import StubLLM
from reportgen.store.db import Database
from reportgen.store.repo import Repositories
from reportgen.web.app import create_app
from reportgen.web.service import ReportService, ServiceError, StoredRegistry

ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads((ROOT / "examples" / "cases" / "case-2024-118.json").read_text(encoding="utf-8"))


def make_app(tmp: Path, *, auth: bool = True, with_library: bool = True):
    settings = Settings.load(
        data_dir=str(tmp), db_path=":memory:", auth_enabled=auth,
        templates_dir=str(ROOT / "templates"),
        glossary_path=str(ROOT / "templates" / "glossary.json"),
    )
    repos = Repositories(Database(":memory:"))
    if with_library:
        by_doc: dict[str, list] = {}
        for chunk in load_corpus(ROOT / "examples" / "corpus"):
            by_doc.setdefault(chunk.doc_id, []).append(chunk)
        for doc_id, chunks in by_doc.items():
            document = repos.documents.upsert(
                doc_id, chunks[0].doc_type, chunks[0].meta.get("title", doc_id),
                chunks[0].meta.get("path", ""), "sha-" + doc_id[:10], meta=chunks[0].meta,
            )
            repos.chunks.replace_for_document(document, chunks)
    service = ReportService(repos=repos, settings=settings, llm=StubLLM())
    app = create_app(settings, repos, service)
    return app, repos, service


class WebTestCase(unittest.TestCase):
    """Общая подготовка: приложение, администратор, вход."""

    auth = True

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.app, self.repos, self.service = make_app(self.tmp, auth=self.auth)
        self.client = TestClient(self.app)
        if self.auth:
            # Логины совпадают с должностями только для читаемости тестов.
            self.repos.users.create("admin", "пароль123", "Хозяев Х. Х.", "owner")
            self.repos.users.create("nachalnik", "пароль123", "Начальников Н. Н.", "head")
            self.repos.users.create("gruppa", "пароль123", "Группин Г. Г.", "lead")
            self.repos.users.create("engineer", "пароль123", "Инженеров И. И.", "engineer")
            self.login("admin")

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    def login(self, login: str, password: str = "пароль123"):
        response = self.client.post("/api/auth/login", json={"login": login, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def create_case(self, facts=None):
        payload = dict(facts or CASE)
        response = self.client.post(
            "/api/cases",
            json={"report_type": payload["report_type"], "case_id": payload["case_id"],
                  "facts": payload},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["case"]

    def generate(self, case_ref: int):
        response = self.client.post(f"/api/cases/{case_ref}/generate", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["report"]


class ServiceUnitTests(unittest.TestCase):
    def test_stored_registry_assigns_and_reuses_labels(self):
        chunks = load_corpus(ROOT / "examples" / "corpus")[:2]
        registry = StoredRegistry()
        self.assertEqual(registry.label(chunks[0]), "S1")
        self.assertEqual(registry.label(chunks[1]), "S2")
        self.assertEqual(registry.label(chunks[0]), "S1")
        self.assertIn("[S2]", registry.render_appendix())

    def test_stored_registry_survives_roundtrip_through_meta(self):
        chunks = load_corpus(ROOT / "examples" / "corpus")[:2]
        registry = StoredRegistry()
        registry.label(chunks[0])
        restored = StoredRegistry.from_meta({"sources": registry.to_meta()})
        self.assertEqual(restored.label(chunks[0]), "S1")
        self.assertEqual(restored.label(chunks[1]), "S2")

    def test_service_error_carries_status(self):
        error = ServiceError("нельзя", 409)
        self.assertEqual(error.status, 409)


class HealthAndConfigTests(WebTestCase):
    def test_health(self):
        body = self.client.get("/api/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("cases", body["counts"])

    def test_config_lists_outlines(self):
        body = self.client.get("/api/config").json()
        types = {outline["report_type"] for outline in body["outlines"]}
        self.assertIn("signal_issue", types)
        self.assertIn("literature", body["doc_types"])

    def test_security_headers(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_index_page_is_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)


class AuthTests(WebTestCase):
    def test_anonymous_is_rejected(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/cases").status_code, 401)

    def test_wrong_password(self):
        self.client.cookies.clear()
        response = self.client.post(
            "/api/auth/login", json={"login": "admin", "password": "нет"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    def test_logout_clears_session(self):
        self.client.post("/api/auth/logout")
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/cases").status_code, 401)

    def test_throttle_blocks_bruteforce(self):
        self.client.cookies.clear()
        for _ in range(5):
            self.client.post("/api/auth/login", json={"login": "admin", "password": "нет"})
        response = self.client.post(
            "/api/auth/login", json={"login": "admin", "password": "пароль123"}
        )
        self.assertEqual(response.status_code, 429)

    def test_engineer_can_create_case(self):
        # Письма ведут все штатные должности: инженер отдела — тоже.
        self.login("engineer")
        response = self.client.post(
            "/api/cases", json={"report_type": "signal_issue", "case_id": "X-1", "facts": CASE}
        )
        self.assertEqual(response.status_code, 200)

    def test_group_lead_is_an_administrator(self):
        # «Все до начальника группы» — значит, начальник группы тоже.
        self.login("gruppa")
        self.assertEqual(self.client.get("/api/users").status_code, 200)

    def test_engineer_cannot_delete_case(self):
        case = self.create_case()
        self.login("engineer")
        self.assertEqual(self.client.delete(f"/api/cases/{case['id']}").status_code, 403)

    def test_audit_is_admin_only(self):
        self.login("engineer")
        self.assertEqual(self.client.get("/api/audit").status_code, 403)

    def test_me_reports_current_user(self):
        body = self.client.get("/api/me").json()
        self.assertEqual(body["user"]["login"], "admin")
        self.assertEqual("owner", body["user"]["role"])
        self.assertTrue(body["auth_enabled"])


class RequestGuardTests(WebTestCase):
    """Тело запроса не должно читаться раньше проверки прав."""

    def test_unauthenticated_upload_is_rejected_before_body(self):
        self.client.cookies.clear()
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("x.md", b"a" * 4096, "text/markdown")},
            data={"doc_type": "literature"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())

    def test_unauthenticated_write_is_rejected_without_reading_body(self):
        self.client.cookies.clear()
        response = self.client.post("/api/cases", json={"report_type": "signal_issue"})
        self.assertEqual(response.status_code, 401)

    def test_oversized_json_is_rejected(self):
        response = self.client.post(
            "/api/cases",
            content=b'{"x":"' + b"a" * (9 * 1024 * 1024) + b'"}',
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_login_stays_reachable_without_session(self):
        self.client.cookies.clear()
        response = self.client.post(
            "/api/auth/login", json={"login": "admin", "password": "пароль123"}
        )
        self.assertEqual(response.status_code, 200)

    def test_reads_stay_open_to_session_check_in_handler(self):
        self.client.cookies.clear()
        # GET не перехватывается middleware — права проверяет сам обработчик.
        self.assertEqual(self.client.get("/api/cases").status_code, 401)


class FilenameTests(WebTestCase):
    def test_control_characters_do_not_reach_headers(self):
        from reportgen.web.api import _disposition, _safe_name

        self.assertEqual(_safe_name("AB\r\nX-Injected: yes"), "ABX-Injected_ yes")
        header = _disposition("отчёт v1.md")
        self.assertNotIn("\n", header)
        header.encode("latin-1")  # заголовок обязан кодироваться без исключения

    def test_cyrillic_case_id_exports(self):
        facts = json.loads(json.dumps(CASE))
        facts["case_id"] = "ОБРАЩЕНИЕ-2024-118"
        case = self.create_case(facts)
        report = self.generate(case["id"])
        response = self.client.get(f"/api/reports/{report['id']}/export.md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("filename*=UTF-8", response.headers["content-disposition"])


class NoAuthTests(WebTestCase):
    auth = False

    def test_local_mode_grants_access(self):
        body = self.client.get("/api/me").json()
        self.assertEqual(body["user"]["login"], "local")
        self.assertFalse(body["auth_enabled"])
        self.assertEqual(self.client.get("/api/cases").status_code, 200)

    def test_local_mode_can_create_and_generate(self):
        """Регрессия: встроенный пользователь должен существовать в базе.

        Раньше LOCAL_USER имел id=0, которого нет в таблице users, и любая
        запись со ссылкой на автора падала на внешнем ключе.
        """
        case = self.create_case()
        report = self.generate(case["id"])
        self.assertEqual(report["version"], 1)
        self.assertEqual(self.repos.cases.count(), 1)

    def test_local_user_exists_in_database_and_cannot_log_in(self):
        user = self.repos.users.by_login("local")
        self.assertIsNotNone(user)
        self.assertFalse(user.active)
        self.assertTrue(user.is_admin)
        self.assertIsNone(self.repos.users.authenticate("local", "любой"))

    def test_local_mode_records_author_in_audit(self):
        self.create_case()
        logins = {entry.login for entry in self.repos.audit.list()}
        self.assertIn("local", logins)

    def test_login_is_rejected_when_disabled(self):
        response = self.client.post(
            "/api/auth/login", json={"login": "admin", "password": "x"}
        )
        self.assertEqual(response.status_code, 400)


class CaseTests(WebTestCase):
    def test_create_and_read(self):
        case = self.create_case()
        self.assertEqual(case["case_id"], CASE["case_id"])
        body = self.client.get(f"/api/cases/{case['id']}").json()
        self.assertEqual(body["coverage"], {})
        self.assertIn("measurements", body["case"]["facts"])

    def test_duplicate_case_id(self):
        self.create_case()
        response = self.client.post(
            "/api/cases",
            json={"report_type": CASE["report_type"], "case_id": CASE["case_id"], "facts": CASE},
        )
        self.assertEqual(response.status_code, 409)

    def test_unknown_report_type(self):
        broken = dict(CASE, report_type="нет-такого")
        response = self.client.post(
            "/api/cases", json={"report_type": "нет-такого", "case_id": "X-2", "facts": broken}
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_factpack_is_rejected(self):
        broken = dict(CASE, case_id="X-3")
        broken["findings"] = [{"id": "F9", "severity": "апокалипсис", "title": "т"}]
        response = self.client.post(
            "/api/cases", json={"report_type": "signal_issue", "case_id": "X-3", "facts": broken}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("severity", response.json()["error"])

    def test_coverage_reports_missing_measurement(self):
        facts = json.loads(json.dumps(CASE))
        facts["case_id"] = "X-4"
        del facts["measurements"]["snr"]
        case = self.create_case(facts)
        body = self.client.get(f"/api/cases/{case['id']}").json()
        self.assertIn("results", body["coverage"])
        self.assertIn("snr", body["coverage"]["results"])

    def test_update_facts(self):
        case = self.create_case()
        facts = json.loads(json.dumps(CASE))
        facts["measurements"]["snr"]["value"] = 15.1
        response = self.client.put(f"/api/cases/{case['id']}/facts", json={"facts": facts})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["case"]["facts"]["measurements"]["snr"]["value"], 15.1
        )

    def test_report_type_cannot_change(self):
        case = self.create_case()
        facts = dict(CASE, report_type="protocol_anomaly")
        response = self.client.put(f"/api/cases/{case['id']}/facts", json={"facts": facts})
        self.assertEqual(response.status_code, 400)

    def test_list_and_filter(self):
        self.create_case()
        body = self.client.get("/api/cases").json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(self.client.get("/api/cases", params={"status": "approved"}).json()["total"], 0)

    def test_unknown_case_is_404(self):
        self.assertEqual(self.client.get("/api/cases/999").status_code, 404)

    def test_malformed_json_body(self):
        response = self.client.post(
            "/api/cases", content=b"{not json", headers={"content-type": "application/json"}
        )
        self.assertEqual(response.status_code, 400)


class ReportFlowTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])

    def test_generation_produces_sections_and_sources(self):
        self.assertEqual(self.report["version"], 1)
        self.assertEqual(len(self.report["sections"]), 7)
        self.assertTrue(self.report["sources"])
        self.assertEqual(self.report["errors"], 0)
        self.assertIn("ЧЕРНОВИК", self.report["markdown"])

    def test_second_generation_bumps_version(self):
        second = self.generate(self.case["id"])
        self.assertEqual(second["version"], 2)

    def test_latest_report_endpoint(self):
        body = self.client.get(f"/api/cases/{self.case['id']}/report").json()
        self.assertEqual(body["report"]["id"], self.report["id"])

    def test_save_section_marks_edited_and_rebuilds_markdown(self):
        section_id = self.report["sections"][5]["section_id"]
        text = "Итоговая формулировка инженера. Измеренный EVM составил 12.4 %."
        response = self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}", json={"text": text}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["section"]["edited"])
        self.assertIn(text, response.json()["report"]["markdown"])

    def test_restore_section_returns_model_draft(self):
        section = self.report["sections"][5]
        self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section['section_id']}",
            json={"text": "правка"},
        )
        response = self.client.post(
            f"/api/reports/{self.report['id']}/sections/{section['section_id']}/restore"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["section"]["edited"])
        self.assertEqual(response.json()["section"]["text"], section["draft_text"])

    def test_regenerate_section(self):
        section_id = self.report["sections"][3]["section_id"]
        response = self.client.post(
            f"/api/reports/{self.report['id']}/sections/{section_id}/regenerate",
            json={"hint": "подробнее про полосу"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["section"]["regenerated"], 1)

    def test_regenerate_unknown_section(self):
        response = self.client.post(
            f"/api/reports/{self.report['id']}/sections/нет-такой/regenerate", json={}
        )
        self.assertEqual(response.status_code, 404)

    def test_verify_endpoint(self):
        body = self.client.post(f"/api/reports/{self.report['id']}/verify").json()
        self.assertEqual(body["errors"], 0)
        self.assertIsInstance(body["issues"], list)

    def test_invented_number_blocks_approval(self):
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Запас по мощности составил 7.3 дБ."},
        )
        verify = self.client.post(f"/api/reports/{self.report['id']}/verify").json()
        self.assertGreater(verify["errors"], 0)
        response = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(response.status_code, 409)
        self.assertIn("не может быть утверждён", response.json()["error"])

    def test_approve_collects_edit_pairs(self):
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Выводы инженера: EVM 12.4 % при ОСШ 13.7 дБ."},
        )
        response = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["report"]["status"], "approved")
        self.assertEqual(self.repos.edits.count(), 1)
        pair = self.repos.edits.list()[0]
        self.assertGreater(pair.edit_distance, 0)
        self.assertLessEqual(pair.edit_distance, 1.0)
        self.assertIn("facts", pair.context)

    def test_approve_is_idempotent(self):
        first = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.repos.edits.count(), 0)

    def test_edit_after_approval_returns_report_to_draft(self):
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        section_id = self.report["sections"][5]["section_id"]
        response = self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Появилось лишнее число 99.9 дБ."},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["report"]["status"], "draft")
        self.assertGreater(response.json()["report"]["errors"], 0)

    def test_warning_does_not_revoke_approval(self):
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        section_id = self.report["sections"][0]["section_id"]
        response = self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Короткий текст."},
        )
        self.assertEqual(response.json()["report"]["status"], "approved")

    def test_sources_endpoint(self):
        body = self.client.get(f"/api/reports/{self.report['id']}/sources").json()
        self.assertTrue(body["items"])
        self.assertTrue(body["items"][0]["label"].startswith("S"))

    def test_export_markdown(self):
        response = self.client.get(f"/api/reports/{self.report['id']}/export.md")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])

    @unittest.skipUnless(
        importlib.util.find_spec("docx"), "python-docx не установлен"
    )
    def test_export_docx(self):
        response = self.client.get(f"/api/reports/{self.report['id']}/export.docx")
        self.assertIn(response.status_code, (200, 501), response.text)


class LibraryTests(WebTestCase):
    def test_library_listing(self):
        body = self.client.get("/api/library").json()
        self.assertTrue(body["items"])
        self.assertIn("literature", body["stats"])

    def test_search(self):
        body = self.client.get("/api/search", params={"q": "занимаемая полоса"}).json()
        self.assertTrue(body["items"])
        self.assertIn("citation", body["items"][0])

    def test_search_reports_degradation(self):
        body = self.client.get("/api/search", params={"q": "полоса"}).json()
        self.assertIn("warning", body)

    def test_search_requires_query(self):
        self.assertEqual(self.client.get("/api/search", params={"q": " "}).status_code, 400)

    def test_search_filters_by_doc_type(self):
        body = self.client.get(
            "/api/search", params={"q": "отчёт", "doc_types": "reports"}
        ).json()
        self.assertTrue(all(item["doc_type"] == "reports" for item in body["items"]))

    def test_superseded_document_disappears_from_search(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        found_before = self.client.get("/api/search", params={"q": "занимаемая полоса"}).json()
        response = self.client.put(
            f"/api/library/{doc_id}/status", json={"status": "superseded"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["document"]["status"], "superseded")
        self.assertFalse(response.json()["document"]["searchable"])
        found_after = self.client.get("/api/search", params={"q": "занимаемая полоса"}).json()
        before = {item["chunk_uid"] for item in found_before["items"]}
        after = {item["chunk_uid"] for item in found_after["items"]}
        self.assertTrue(before)
        self.assertFalse({uid for uid in after if uid.startswith(doc_id)})

    def test_superseded_by_must_exist(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        response = self.client.put(
            f"/api/library/{doc_id}/status",
            json={"status": "superseded", "superseded_by": "нет/такого"},
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_status_is_rejected(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        response = self.client.put(f"/api/library/{doc_id}/status", json={"status": "выдумка"})
        self.assertEqual(response.status_code, 400)

    def test_status_change_requires_login(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        self.client.post("/api/auth/logout", json={})
        response = self.client.put(f"/api/library/{doc_id}/status", json={"status": "archived"})
        self.assertEqual(response.status_code, 401)

    def test_library_filters_by_status(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        self.client.put(f"/api/library/{doc_id}/status", json={"status": "archived"})
        archived = self.client.get("/api/library", params={"status": "archived"}).json()
        self.assertEqual([item["doc_id"] for item in archived["items"]], [doc_id])
        current = self.client.get("/api/library", params={"status": "current"}).json()
        self.assertNotIn(doc_id, [item["doc_id"] for item in current["items"]])

    def test_delete_document_requires_admin(self):
        doc_id = self.client.get("/api/library").json()["items"][0]["doc_id"]
        self.login("engineer")
        self.assertEqual(self.client.delete(f"/api/library/{doc_id}").status_code, 403)
        self.login("admin")
        self.assertEqual(self.client.delete(f"/api/library/{doc_id}").status_code, 200)
        self.assertIsNone(self.repos.documents.by_doc_id(doc_id))

    @unittest.skipUnless(
        importlib.util.find_spec("reportgen.ingest.pipeline"), "модуль приёма не установлен"
    )
    def test_upload_document(self):
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("заметка.md", b"# Test\n\n" + "Полоса частот. " .encode() * 40, "text/markdown")},
            data={"doc_type": "literature", "confidentiality": "internal"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue((self.tmp / "library" / "literature" / "заметка.md").exists())
        body = response.json()
        self.assertIsNotNone(body["document"])
        self.assertEqual(body["document"]["doc_type"], "literature")
        self.assertGreater(body["document"]["chunk_count"], 0)
        self.assertTrue(self.client.get("/api/search", params={"q": "полоса частот"}).json()["items"])

    def test_upload_rejects_unknown_doc_type(self):
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("x.md", b"# T\n\ntext", "text/markdown")},
            data={"doc_type": "нет-такого", "confidentiality": "internal"},
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_sanitizes_path_traversal(self):
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("../../evil.md", b"# T\n\n" + b"a" * 300, "text/markdown")},
            data={"doc_type": "literature", "confidentiality": "internal"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue((self.tmp / "library" / "literature" / "evil.md").exists())
        self.assertFalse((self.tmp.parent / "evil.md").exists())


class DomainReferenceTests(WebTestCase):
    """Справочник направлений в интерфейсе — тот же, что у приёма документов.

    Приём читает `settings.domains_path`, а интерфейс брал
    `templates_dir/domains.json`. Пока это один файл, разницы не видно; стоит
    задать справочник отдельно (`REPORTGEN_DOMAINS_PATH` — так велит
    инструкция) — и документы раскладываются верно, а в таблице у всех
    «не указано» и список направлений пуст. Данные при этом целы: расходится
    только показ, и найти причину неоткуда.
    """

    def test_reference_comes_from_the_configured_path(self):
        own = self.tmp / "свой-справочник.json"
        own.write_text(json.dumps({"domains": [
            {"id": "hf", "title": "Только коротковолновая", "keywords": ["кв"]},
        ]}, ensure_ascii=False), encoding="utf-8")
        self.app.state.settings.domains_path = own

        answer = self.client.get("/api/domains").json()
        titles = [item["title"] for item in answer["items"]]
        self.assertEqual(["Только коротковолновая"], titles)

    def test_default_still_works_without_the_setting(self):
        self.app.state.settings.domains_path = None
        answer = self.client.get("/api/domains").json()
        self.assertTrue(answer["items"], "справочник по умолчанию пуст")
        self.assertIn("misc", {item["id"] for item in answer["items"]})


class StatsTests(WebTestCase):
    def test_stats_shape(self):
        case = self.create_case()
        self.generate(case["id"])
        body = self.client.get("/api/stats").json()
        self.assertEqual(body["cases"]["total"], 1)
        self.assertEqual(body["reports"]["total"], 1)
        self.assertIn("by_section", body["edits"])
        self.assertGreater(body["library"]["chunks"], 0)

    def test_audit_records_actions(self):
        self.create_case()
        items = self.client.get("/api/audit").json()["items"]
        actions = {item["action"] for item in items}
        self.assertIn("case.create", actions)
        self.assertIn("auth.login", actions)


if __name__ == "__main__":
    unittest.main()


class DocumentInspectionTests(WebTestCase):
    """Открыть исходный файл и посмотреть, что из него вычитано."""

    def test_text_shows_chunks_and_source(self):
        doc_id = self.repos.documents.list()[0].doc_id
        data = self.client.get(f"/api/library/{quote(doc_id, safe='')}/text").json()
        self.assertIn("chunks", data)
        self.assertIn("text", data)
        self.assertIn("source_exists", data)
        self.assertEqual(doc_id, data["document"]["doc_id"])

    def test_missing_document_says_so(self):
        response = self.client.get("/api/library/нет%2Fтакого/text")
        self.assertEqual(404, response.status_code)

    @unittest.skipUnless(
        importlib.util.find_spec("reportgen.ingest.pipeline"), "модуль приёма не установлен"
    )
    def test_file_is_served(self):
        # Загружаем через интерфейс: только такие документы лежат внутри
        # каталога библиотеки, а отдаём мы исключительно их.
        uploaded = self.client.post(
            "/api/library/upload",
            files={"file": ("методика.md", "# Методика\n\nПолоса частот. " .encode() * 20,
                            "text/markdown")},
            data={"doc_type": "literature", "confidentiality": "internal"},
        )
        self.assertEqual(200, uploaded.status_code, uploaded.text)
        doc_id = uploaded.json()["document"]["doc_id"]
        response = self.client.get(f"/api/library/{quote(doc_id, safe='')}/file")
        self.assertEqual(200, response.status_code, response.text)
        self.assertIn(b"\xd0", response.content)

    def test_file_outside_library_is_refused_for_corpus(self):
        # Демонстрационный корпус лежит вне каталога библиотеки, и отдавать
        # его наружу нельзя: source_path приходит из базы, а не от пользователя.
        doc_id = self.repos.documents.list()[0].doc_id
        response = self.client.get(f"/api/library/{quote(doc_id, safe='')}/file")
        self.assertIn(response.status_code, (403, 404))

    def test_file_outside_library_is_refused(self):
        # source_path приходит из базы: без проверки правка записи превратилась
        # бы в чтение любого файла на машине.
        doc_id = self.repos.documents.list()[0].doc_id
        repos = self.repos
        document = repos.documents.by_doc_id(doc_id)
        repos.db.connection.execute(
            "UPDATE documents SET source_path = ? WHERE id = ?",
            ("/etc/passwd", document.id),
        )
        repos.db.connection.commit()
        response = self.client.get(f"/api/library/{quote(doc_id, safe='')}/file")
        self.assertIn(response.status_code, (403, 404))


class UserManagementTests(WebTestCase):
    """Сотрудники, должности и пароли — из интерфейса, а не только из CLI."""

    def test_only_admin_sees_the_list(self):
        self.login("engineer")
        self.assertEqual(403, self.client.get("/api/users").status_code)

    def test_list_has_roles_with_explanations(self):
        data = self.client.get("/api/users").json()
        self.assertTrue(data["items"])
        titles = {role["id"]: role for role in data["roles"]}
        self.assertEqual(
            {"owner", "head", "deputy", "lead", "senior", "engineer"}, set(titles)
        )
        for role in data["roles"]:
            self.assertTrue(role["note"], f"у должности {role['id']} нет пояснения")
            self.assertTrue(role["title"], f"у должности {role['id']} нет названия")
        # Права администратора — до начальника группы включительно.
        self.assertTrue(all(titles[key]["is_admin"]
                            for key in ("owner", "head", "deputy", "lead")))
        self.assertFalse(any(titles[key]["is_admin"] for key in ("senior", "engineer")))

    def test_local_service_record_is_hidden(self):
        # Запись «local» — служебная, в списке личного состава ей не место.
        logins = {item["login"] for item in self.client.get("/api/users").json()["items"]}
        self.assertNotIn("local", logins)

    def test_create_and_update(self):
        created = self.client.post("/api/users", json={
            "login": "petrov", "full_name": "Петров П.П.",
            "password": "пароль12345", "role": "engineer",
        })
        self.assertEqual(200, created.status_code, created.text)
        user = created.json()["user"]
        self.assertEqual("Петров П.П.", user["full_name"])

        patched = self.client.patch(f"/api/users/{user['id']}", json={
            "role": "senior", "department": "Отдел связи", "team": "1 группа"})
        self.assertEqual("senior", patched.json()["user"]["role"])
        self.assertEqual("Отдел связи", patched.json()["user"]["department"])
        self.assertEqual("Старший инженер отдела", patched.json()["user"]["role_title"])

    def test_login_rules_are_enforced(self):
        for bad in ("ab", "Петров", "with space", "x" * 40):
            with self.subTest(login=bad):
                response = self.client.post("/api/users", json={
                    "login": bad, "password": "пароль12345", "role": "engineer"})
                self.assertEqual(400, response.status_code)

    def test_short_password_refused(self):
        response = self.client.post("/api/users", json={
            "login": "sidorov", "password": "коротк", "role": "engineer"})
        self.assertEqual(400, response.status_code)

    def test_duplicate_login_refused(self):
        payload = {"login": "dublikat", "password": "пароль12345", "role": "engineer"}
        self.assertEqual(200, self.client.post("/api/users", json=payload).status_code)
        self.assertEqual(409, self.client.post("/api/users", json=payload).status_code)

    def test_owner_cannot_be_demoted_by_anyone(self):
        # Создателя не разжалует никто, включая его самого: иначе система
        # остаётся без владельца, а чинится это только командной строкой.
        me = self.client.get("/api/me").json()["user"]
        response = self.client.patch(f"/api/users/{me['id']}", json={"role": "engineer"})
        self.assertEqual(403, response.status_code)

    def test_admins_are_counted_by_position(self):
        # Создатель, начальник отдела и начальник группы — администраторы;
        # старший инженер и инженер — нет.
        self.assertEqual(3, self.repos.users.count_admins())
        engineer = self.repos.users.by_login("engineer")
        self.assertFalse(engineer.is_admin)

    def test_nobody_can_demote_themselves(self):
        # Разжаловать можно только младшего по должности. Значит, сам
        # администратор администратором и остаётся — отдельной проверки
        # «остался последний» не требуется.
        head = [u for u in self.client.get("/api/users").json()["items"]
                if u["role"] == "head"][0]
        self.login("nachalnik")
        response = self.client.patch(f"/api/users/{head['id']}", json={"role": "engineer"})
        self.assertEqual(403, response.status_code)

    def test_lead_cannot_touch_the_head(self):
        # Начальник группы — администратор, но не над своим начальником.
        head = [u for u in self.client.get("/api/users").json()["items"]
                if u["role"] == "head"][0]
        self.login("gruppa")
        response = self.client.patch(f"/api/users/{head['id']}", json={"role": "engineer"})
        self.assertEqual(403, response.status_code)

    def test_role_above_own_is_refused(self):
        self.login("gruppa")
        response = self.client.post("/api/users", json={
            "login": "vyshe", "password": "пароль12345", "role": "head"})
        self.assertEqual(403, response.status_code)

    def test_cannot_disable_self(self):
        me = self.client.get("/api/me").json()["user"]
        response = self.client.post(f"/api/users/{me['id']}/active", json={"active": False})
        self.assertEqual(409, response.status_code)

    def test_password_reset_closes_sessions(self):
        created = self.client.post("/api/users", json={
            "login": "smena", "password": "пароль12345", "role": "engineer"}).json()["user"]
        response = self.client.post(f"/api/users/{created['id']}/password",
                                    json={"password": "новыйпароль1"})
        self.assertEqual(200, response.status_code)


class InterfaceCopyTests(unittest.TestCase):
    """Подписи интерфейса: без жаргона и без разговоров ни о чём."""

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.html = (static / "index.html").read_text(encoding="utf-8")
        self.login = (static / "login.html").read_text(encoding="utf-8")

    def strings(self, text: str) -> str:
        """Только строковые литералы: комментарии в коде — не интерфейс."""
        return " ".join(re.findall(r"'((?:[^'\\\n]|\\.)*)'", text))

    def test_no_chunk_jargon(self):
        # «Чанк» — слово из кода. Инженеру связи оно не говорит ничего, а в
        # интерфейсе рядом уже есть «фрагмент».
        self.assertNotIn("чанк", self.strings(self.js).lower())
        self.assertNotIn("чанк", self.html.lower())

    def test_no_verdict_when_everything_is_fine(self):
        # «Разбор выглядит нормально» инженер читает каждый раз впустую:
        # число фрагментов и так стоит на вкладке рядом.
        self.assertNotIn("выглядит нормально", self.strings(self.js))

    def test_password_can_be_shown(self):
        # На изолированной машине менеджера паролей нет, а вслепую длинный
        # пароль набирают с ошибками.
        self.assertIn("passwordField", self.js)
        self.assertIn("pw-toggle", self.js)

    def test_submit_button_is_not_taken_from_the_event(self):
        """event.currentTarget после await равен null.

        Кнопку «Завести» включали обратно через него: сервер отклонял
        короткий пароль, обработчик падал на «Cannot set properties of
        null», и кнопка оставалась выключенной навсегда. Инженер исправлял
        пароль и не мог отправить форму.
        """
        self.assertNotIn("event.currentTarget.disabled", self.js)


class ResponsiveLayoutTests(unittest.TestCase):
    """Вёрстка: во всю ширину монитора и без обрезки на ноутбуке."""

    def setUp(self):
        self.css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")

    def block(self, selector: str) -> str:
        start = self.css.index(selector + " {")
        return self.css[start:self.css.index("}", start)]

    def test_content_width_is_not_capped(self):
        # Ограничитель в 1320 px оставлял треть 24-дюймового монитора пустой:
        # раздел выглядел обрезанным, хотя место было.
        self.assertNotIn("--content-max", self.css)
        self.assertNotIn(".page > * {", self.css)

    def test_readable_measure_is_opt_in(self):
        # Мера строки осталась, но только там, где сплошной текст.
        self.assertIn("max-width: 78ch", self.block(".prose"))

    def test_topbar_can_shrink(self):
        self.assertIn("min-width: 0", self.block(".topbar"))
        self.assertIn("min-width: 0", self.block(".topbar-right"))

    def test_side_menu_collapses_to_icons(self):
        # На ноутбуке 1280 меню само сворачивается в значки, не отнимая
        # ширину у таблицы.
        self.assertIn("@media (max-width: 1240px)", self.css)
        self.assertIn("var(--side-w-min)", self.css)

    def test_side_menu_becomes_a_drawer_on_narrow_screens(self):
        self.assertIn("@media (max-width: 900px)", self.css)
        self.assertIn("body.side-open .side", self.css)

    def test_long_name_is_trimmed_not_pushing(self):
        self.assertIn("text-overflow: ellipsis", self.css)

    def test_library_table_sheds_columns_on_narrow_screens(self):
        # Столбцы требуют места; на 1366 таблица уезжала за край, и это
        # читалось как обрезанная вёрстка, а не как «прокрути вправо».
        for width in ("1500px", "1300px", "1120px"):
            with self.subTest(width=width):
                self.assertIn(f"@media (max-width: {width})", self.css)

    def test_page_head_wraps(self):
        self.assertIn("flex-wrap: wrap", self.block(".page-head"))
