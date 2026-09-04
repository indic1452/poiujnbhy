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
            self.repos.users.create("zam", "пароль123", "Заместителев З. З.", "deputy")
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

    def submit(self, report_id: int):
        """Сдать отчёт на проверку. Проверяют только то, что сдали."""
        response = self.client.post(f"/api/reports/{report_id}/submit", json={})
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

    def test_health_counts_everything_that_accumulates(self):
        """Таблица, которой нет в счётчиках, для проверки не существует.

        База растёт бумагами письма, документами людей, перепиской и
        уведомлениями — а «сколько в базе» отвечали по одним письмам и
        отчётам, и рост, о котором никто не знает, обнаруживался местом
        на диске.
        """
        counts = self.client.get("/api/health").json()["counts"]
        for name in ("case_files", "case_notes", "person_files",
                     "notifications", "talks", "talk_messages"):
            self.assertIn(name, counts)

    def test_config_lists_outlines(self):
        body = self.client.get("/api/config").json()
        types = {outline["report_type"] for outline in body["outlines"]}
        self.assertIn("signal_issue", types)
        self.assertIn("literature", body["doc_types"])

    def test_health_without_login_says_only_that_it_is_alive(self):
        """Признак жизни нужен скриптам запуска и без входа.

        А сколько в отделе военнослужащих, писем и отчётов — сведения о работе
        организации: посторонним в них делать нечего. Адрес и имя модели —
        тем более.
        """
        self.client.cookies.clear()
        body = self.client.get("/api/health").json()
        self.assertEqual("ok", body["status"])
        self.assertNotIn("counts", body)
        self.assertNotIn("llm", body)

    def test_config_requires_login(self):
        # Окно входа справочников не запрашивает: оформление оно берёт
        # отдельным маршрутом. А состав шаблонов отчётов, перечень
        # направлений работы и адрес модели постороннему знать незачем.
        self.client.cookies.clear()
        self.assertEqual(401, self.client.get("/api/config").status_code)

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

    def test_the_journal_is_for_the_one_who_runs_the_system(self):
        """Журнал — не управление отделом, а протокол работы самой системы.

        Права администратора есть и у начальника группы: он заводит людей и
        правит библиотеку. Но в журнале видно, кто что открывал, менял и
        удалял, включая действия начальства, — читать его должен тот, кто за
        систему отвечает.
        """
        for who in ("engineer", "gruppa", "nachalnik", "zam"):
            self.login(who)
            self.assertEqual(403, self.client.get("/api/audit").status_code,
                             f"журнал открылся должности «{who}»")
        self.login("admin")
        self.assertEqual(200, self.client.get("/api/audit").status_code)

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

    def test_slash_in_the_number_does_not_eat_the_beginning(self):
        """«ВХ-2026/0423» — обычный входящий номер, а не путь.

        От него оставалось «0423»: письма разных лет выгружались в один и
        тот же файл и затирали друг друга в каталоге выгрузок.
        """
        from reportgen.web.api import _safe_name

        self.assertEqual("ВХ-2026-0423", _safe_name("ВХ-2026/0423"))
        self.assertNotEqual(_safe_name("ВХ-2026/0423"), _safe_name("ВХ-2025/0423"))

    def test_path_traversal_still_cannot_escape(self):
        from reportgen.web.api import _safe_name

        for evil in ("../../etc/passwd", "..\\..\\windows\\system32",
                     "/etc/shadow", "C:\\Windows\\win.ini"):
            with self.subTest(evil=evil):
                safe = _safe_name(evil)
                self.assertNotIn("/", safe)
                self.assertNotIn("\\", safe)

    def test_name_is_never_empty(self):
        from reportgen.web.api import _safe_name

        # Пустое имя после чистки — тоже имя файла: выгрузка ушла бы
        # в «-v1.docx» или вовсе в каталог.
        self.assertTrue(_safe_name("..."))
        self.assertTrue(_safe_name(""))

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


class ReviewFlowTests(WebTestCase):
    """Порядок проверки: сдал — начальник посмотрел — вернул или принял."""

    def setUp(self):
        super().setUp()
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])

    def clean_sections(self):
        """Довести отчёт до нуля ошибок: чисел мимо факт-пакета не остаётся."""
        for section in self.report["sections"]:
            self.client.put(
                f"/api/reports/{self.report['id']}/sections/{section['section_id']}",
                json={"text": "Текст инженера без числовых значений."})

    def test_engineer_sends_the_report_up_but_cannot_pass_it_himself(self):
        """Сдают отчёты все, проверяет начальник. Инженер себя не проверяет."""
        self.clean_sections()
        self.login("engineer")
        sent = self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.assertEqual(200, sent.status_code, sent.text)
        self.assertEqual("review", sent.json()["report"]["status"])
        self.assertEqual("на проверке", sent.json()["report"]["status_title"])
        # Письмо тоже видно как отправленное на проверку.
        self.assertEqual("review",
                         self.client.get(f"/api/cases/{self.case['id']}").json()["case"]["status"])

        refused = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(403, refused.status_code)
        self.assertIn("начальник отдела", refused.json()["error"])

    def test_group_lead_is_an_administrator_but_not_a_reviewer(self):
        # Начальник группы заводит людей, а отчёты проверяет не он.
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("gruppa")
        self.assertEqual(403,
                         self.client.post(f"/api/reports/{self.report['id']}/approve").status_code)
        self.assertEqual(403,
                         self.client.post(f"/api/reports/{self.report['id']}/rework",
                                          json={"note": "поправьте"}).status_code)

    def test_head_and_deputy_both_review(self):
        for login in ("nachalnik", "zam"):
            with self.subTest(login=login):
                self.login("admin")
                case = self.create_case({**CASE, "case_id": f"SUP-ПРОВ-{login}"})
                report = self.generate(case["id"])
                for section in report["sections"]:
                    self.client.put(
                        f"/api/reports/{report['id']}/sections/{section['section_id']}",
                        json={"text": "Текст инженера без числовых значений."})
                self.client.post(f"/api/reports/{report['id']}/submit")
                self.login(login)
                done = self.client.post(f"/api/reports/{report['id']}/approve")
                self.assertEqual(200, done.status_code, done.text)
                self.assertEqual("approved", done.json()["report"]["status"])

    def test_head_sends_it_back_with_a_remark_everyone_sees(self):
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        back = self.client.post(f"/api/reports/{self.report['id']}/rework",
                                json={"note": "В выводах нет ссылки на методику"})
        self.assertEqual(200, back.status_code, back.text)
        self.assertEqual("rework", back.json()["report"]["status"])
        self.assertEqual("требует исправления", back.json()["report"]["status_title"])

        # Замечание видит исполнитель, а не только начальник.
        self.login("engineer")
        mine = self.client.get(f"/api/reports/{self.report['id']}").json()["report"]
        self.assertEqual("В выводах нет ссылки на методику", mine["review_note"])
        self.assertEqual("rework", mine["status"])

    def test_remark_is_required_when_sending_back(self):
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        empty = self.client.post(f"/api/reports/{self.report['id']}/rework",
                                 json={"note": "   "})
        self.assertEqual(400, empty.status_code)
        self.assertIn("что именно исправить", empty.json()["error"])

    def test_corrected_report_goes_up_again_and_the_remark_clears(self):
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/rework",
                         json={"note": "поправьте выводы"})
        self.login("engineer")
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Выводы переписаны по замечанию."})
        again = self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.assertEqual(200, again.status_code, again.text)
        self.assertEqual("review", again.json()["report"]["status"])
        self.assertEqual("", again.json()["report"]["review_note"])

    def test_editing_a_report_on_review_returns_it_to_the_author(self):
        """Начальник читает то, что сдали, а не то, что правят под ним."""
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        section_id = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Правка уже после сдачи."})
        self.assertEqual("draft", self.repos.reports.get(self.report["id"]).status)

    def test_regenerating_a_section_also_removes_the_signature(self):
        """Перегенерация — такая же правка текста, как правка руками.

        Подпись снималась при правке и откате раздела, а перегенерацию
        пропустили: начальник оставался утвердившим текст, которого не
        видел, и это уходило в шапку выгружаемого документа.
        """
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual("approved", self.repos.reports.get(self.report["id"]).status)

        self.login("engineer")
        section_id = self.report["sections"][0]["section_id"]
        response = self.client.post(
            f"/api/reports/{self.report['id']}/sections/{section_id}/regenerate", json={})
        self.assertEqual(200, response.status_code, response.text)
        after = self.repos.reports.get(self.report["id"])
        self.assertEqual("draft", after.status)
        self.assertIsNone(after.approved_by, "проверяющий остался под чужим текстом")
        self.assertNotIn("УТВЕРЖДЁН", self.client.get(
            f"/api/reports/{self.report['id']}/export.md").text)

    def test_regenerating_a_section_on_review_returns_it_to_the_author(self):
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        section_id = self.report["sections"][0]["section_id"]
        self.client.post(
            f"/api/reports/{self.report['id']}/sections/{section_id}/regenerate", json={})
        self.assertEqual("draft", self.repos.reports.get(self.report["id"]).status)

    def test_letter_comes_back_together_with_the_report(self):
        """Письмо не должно числиться проверенным, когда отчёт — черновик.

        Подпись снималась только с отчёта: в списке писем стояло состояние
        законченной работы, а в карточке лежал черновик.
        """
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        # Начальник проверил — но ответ ещё не отправлен: письмо ждёт
        # исходящего номера, а не числится законченным.
        self.assertEqual("checked", self.repos.cases.get(self.case["id"]).status)

        self.login("engineer")
        section_id = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Правка после проверки."})
        self.assertEqual("draft", self.repos.reports.get(self.report["id"]).status)
        self.assertEqual("draft", self.repos.cases.get(self.case["id"]).status,
                         "письмо осталось отправленным при черновике отчёта")

    def test_only_a_handed_in_report_can_be_marked_checked(self):
        """Проверяют то, что сдали.

        Отчёт, лежащий в работе у исполнителя, начальник отмечать
        проверенным не должен: исполнитель ещё не сказал, что закончил.
        """
        self.clean_sections()
        self.login("nachalnik")
        early = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(409, early.status_code)
        self.assertIn("не отправлен на проверку", early.json()["error"])

        # И возвращённый на исправление — тоже: сначала пусть сдаст заново.
        self.login("engineer")
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/rework", json={"note": "поправьте"})
        again = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(409, again.status_code)

    def test_signature_is_wiped_and_not_left_from_the_previous_text(self):
        # Подтверждено сплошным разбором: при снятии подписи оставались
        # прежние «кто и когда проверил».
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        signed = self.repos.reports.get(self.report["id"])
        self.assertIsNotNone(signed.approved_by)

        section_id = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Правка после проверки."})
        after = self.repos.reports.get(self.report["id"])
        self.assertEqual("draft", after.status)
        self.assertIsNone(after.approved_by, "проверяющий остался под чужим текстом")
        self.assertIsNone(after.approved_at)

    def test_new_version_takes_the_old_one_off_the_head_desk(self):
        """Собрал новую версию — значит, прежнюю отозвал.

        Исполнитель собирал версию, пока начальник читал сданную: письмо
        уходило «в работу» и пропадало из очереди проверки, а прежний
        отчёт оставался помеченным «на проверке» — числился сданным,
        а найти его начальнику было уже негде.
        """
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        second = self.generate(self.case["id"])

        withdrawn = self.repos.reports.get(self.report["id"])
        self.assertEqual("draft", withdrawn.status,
                         "прежний отчёт остался на проверке без письма в очереди")
        self.assertEqual("draft",
                         self.client.get(f"/api/cases/{self.case['id']}").json()["case"]["status"])
        self.assertEqual("draft", self.repos.reports.get(second["id"]).status)

        # И отметить проверенным отозванное нельзя: сдают заново.
        self.login("nachalnik")
        refused = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(409, refused.status_code)

    def test_returned_report_leaves_the_training_set(self):
        """Начальник забраковал текст — учить модель на нём нельзя.

        Пары «черновик модели → финал инженера» собираются при отметке
        «проверен» (док. 03, 3.7). Если после этого отчёт вернули на
        исправление, тот же текст остаётся в наборе как образец — ровно
        то, от чего предостерегает документ.
        """
        section_id = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Правленый инженером текст без числовых значений."})
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertTrue(self.pairs(), "пары за утверждение не сохранились")

        self.client.post(f"/api/reports/{self.report['id']}/rework",
                         json={"note": "вывод не по делу, переписать"})
        self.assertEqual(0, self.pairs(), "забракованный текст остался в обучающем наборе")

    def test_revoked_signature_takes_the_pairs_with_it(self):
        # Подпись снимает верификатор, найдя число мимо факт-пакета.
        # Пары того утверждения учат ровно этому тексту.
        section_id = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Правленый инженером текст без числовых значений."})
        self.clean_sections()
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertTrue(self.pairs(), "пары за утверждение не сохранились")

        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Замер дал 4242 единицы, чего в исходных данных нет."})
        self.assertEqual("draft", self.repos.reports.get(self.report["id"]).status)
        self.assertEqual(0, self.pairs(), "пары остались под снятой подписью")

    def pairs(self) -> int:
        return int(self.repos.db.scalar("SELECT count(*) FROM edit_pairs") or 0)


class OutgoingNumberTests(WebTestCase):
    """Последний шаг порядка отдела: ответ ушёл, записан исходящий номер."""

    def setUp(self):
        super().setUp()
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])
        for section in self.report["sections"]:
            self.client.put(
                f"/api/reports/{self.report['id']}/sections/{section['section_id']}",
                json={"text": "Текст инженера без числовых значений."})

    def check(self):
        """Довести отчёт до «проверен» руками начальника."""
        self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.login("nachalnik")
        self.assertEqual(200, self.client.post(
            f"/api/reports/{self.report['id']}/approve").status_code)
        self.login("engineer")

    def case_now(self):
        return self.client.get(f"/api/cases/{self.case['id']}").json()["case"]

    def test_checked_is_not_yet_sent(self):
        """«Проверен» и «отправлено» — разные вещи.

        Начальник согласился с отчётом, но ответ ещё не ушёл: пока нет
        исходящего номера, письмо в отделе не закрыто и остаётся в работе.
        """
        self.check()
        fresh = self.case_now()
        self.assertEqual("checked", fresh["status"])
        self.assertEqual("проверен, к отправке", fresh["status_title"])
        self.assertEqual("", fresh["outgoing_no"])
        # И оно по-прежнему числится работой отдела.
        self.assertIn(self.case["case_id"], [item["case_id"] for item in self.client.get(
            "/api/cases", params={"status": "open"}).json()["items"]])

    def test_engineer_sends_the_answer_and_writes_the_number(self):
        self.check()
        sent = self.client.post(f"/api/cases/{self.case['id']}/send", json={
            "outgoing_no": "ИСХ-2026-1147", "outgoing_date": "2026-08-29"})
        self.assertEqual(200, sent.status_code, sent.text)
        fresh = self.case_now()
        self.assertEqual("approved", fresh["status"])
        self.assertEqual("отправлено", fresh["status_title"])
        self.assertEqual("ИСХ-2026-1147", fresh["outgoing_no"])
        self.assertEqual("2026-08-29", fresh["outgoing_date"])
        self.assertEqual("Инженеров И. И.", fresh["sent_by_name"])

    def test_nothing_goes_out_before_the_head_has_checked_it(self):
        # Порядок отдела: сначала проверка, потом отправка.
        early = self.client.post(f"/api/cases/{self.case['id']}/send",
                                 json={"outgoing_no": "ИСХ-1"})
        self.assertEqual(409, early.status_code)
        self.assertIn("не проверен", early.json()["error"])

    def test_a_letter_answered_outside_the_system_still_records_its_number(self):
        """Ответ составили в Word и отправили, отчёт сюда не заводили.

        Так в отделе бывает. Исходящий номер всё равно должен попасть в
        базу — иначе в учёте дыра: письмо закрыто, а чем ответили,
        неизвестно.
        """
        empty = self.create_case({**CASE, "case_id": "SUP-МИМО-1"})
        sent = self.client.post(f"/api/cases/{empty['id']}/send",
                                json={"outgoing_no": "ИСХ-2026-0007"})
        self.assertEqual(200, sent.status_code, sent.text)
        fresh = self.client.get(f"/api/cases/{empty['id']}").json()["case"]
        self.assertEqual("approved", fresh["status"])
        self.assertEqual("ИСХ-2026-0007", fresh["outgoing_no"])

    def test_the_number_is_required_and_bounded(self):
        self.check()
        blank = self.client.post(f"/api/cases/{self.case['id']}/send",
                                 json={"outgoing_no": "   "})
        self.assertEqual(400, blank.status_code)
        self.assertIn("исходящий номер", blank.json()["error"])
        long = self.client.post(f"/api/cases/{self.case['id']}/send",
                                json={"outgoing_no": "9" * 61})
        self.assertEqual(400, long.status_code)

    def test_the_answer_goes_out_once(self):
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        again = self.client.post(f"/api/cases/{self.case['id']}/send",
                                 json={"outgoing_no": "ИСХ-ДРУГОЙ"})
        self.assertEqual(409, again.status_code)
        self.assertIn("ИСХ-2026-1147", again.json()["error"])

    def test_a_sent_letter_is_closed_for_work(self):
        """Ответ ушёл — отчёт обязан совпадать с тем, что отправили.

        Иначе в отделе документ под исходящим номером один, а в системе
        по тому же номеру другой.
        """
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        section_id = self.report["sections"][0]["section_id"]
        for name, response in (
            ("правка раздела", self.client.put(
                f"/api/reports/{self.report['id']}/sections/{section_id}",
                json={"text": "правка после отправки"})),
            ("сборка заново", self.client.post(
                f"/api/cases/{self.case['id']}/generate", json={})),
            ("правка исходных данных", self.client.put(
                f"/api/cases/{self.case['id']}/facts", json={"facts": CASE})),
        ):
            with self.subTest(name=name):
                self.assertEqual(409, response.status_code, response.text)
                self.assertIn("ИСХ-2026-1147", response.json()["error"])

    def test_only_a_reviewer_withdraws_the_sending(self):
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        self.assertEqual(403, self.client.post(
            f"/api/cases/{self.case['id']}/unsend").status_code)
        self.login("gruppa")  # начальник группы — администратор, но не проверяющий
        self.assertEqual(403, self.client.post(
            f"/api/cases/{self.case['id']}/unsend").status_code)

        self.login("nachalnik")
        back = self.client.post(f"/api/cases/{self.case['id']}/unsend")
        self.assertEqual(200, back.status_code, back.text)
        fresh = self.case_now()
        self.assertEqual("checked", fresh["status"])
        self.assertEqual("", fresh["outgoing_no"])
        self.assertIsNone(fresh["sent_by"])
        # И работа по письму снова возможна.
        self.login("engineer")
        section_id = self.report["sections"][0]["section_id"]
        self.assertEqual(200, self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Поправленный текст без числовых значений."}).status_code)

    def test_the_outgoing_number_is_not_written_by_hand_in_the_card(self):
        # Иначе письмо числилось бы отправленным без проверенного отчёта.
        refused = self.client.patch(f"/api/cases/{self.case['id']}",
                                    json={"outgoing_no": "ИСХ-МИМО"})
        self.assertEqual(409, refused.status_code)
        self.assertIn("при отправке ответа", refused.json()["error"])
        self.assertEqual("", self.case_now()["outgoing_no"])

    def test_the_letter_states_stay_in_the_hands_of_the_flow(self):
        # «Проверен» руками тоже не выставляют: его даёт отметка начальника.
        for status in ("review", "checked", "approved"):
            with self.subTest(status=status):
                refused = self.client.patch(f"/api/cases/{self.case['id']}",
                                            json={"status": status})
                self.assertEqual(409, refused.status_code)


    def test_no_new_report_is_handed_in_for_a_sent_letter(self):
        """Ответ ушёл — сдавать по письму новый отчёт нельзя.

        Сдача файлом шла мимо запрета: письмо возвращалось «на проверку»,
        сохраняя запись об отправке. В учёте выходила небылица — ответ
        отправлен и одновременно лежит у начальника.
        """
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        refused = self.client.post("/api/reports/upload", data={
            "case_id": self.case["case_id"], "incoming_no": self.case["case_id"],
            "report_type": CASE["report_type"]},
            files={"file": ("о.md", "# T\n\nтекст\n".encode(), "text/markdown")})
        self.assertEqual(409, refused.status_code, refused.text)
        self.assertIn("ИСХ-2026-1147", refused.json()["error"])
        fresh = self.case_now()
        self.assertEqual("approved", fresh["status"])
        self.assertEqual("ИСХ-2026-1147", fresh["outgoing_no"])

    def test_errors_found_after_sending_do_not_falsify_the_record(self):
        """Отзыв подписи не отзывает бумагу у адресата.

        Верификатор ронял отправленный отчёт в черновик, а письмо
        оставалось «отправлено» с исходящим номером: система начинала
        врать — отправлено и черновик разом. Замечания сохраняем и
        показываем, отметку оставляем; отдел решает, отзывать ли отправку.
        """
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        # Портим текст мимо API — как если бы сменился шаблон или глоссарий.
        self.repos.db.connection.execute(
            "UPDATE report_sections SET text = ? WHERE report_id = ?",
            ("Замер дал 4242 единицы, чего в исходных данных нет.", self.report["id"]))
        self.repos.db.connection.commit()

        checked = self.client.post(f"/api/reports/{self.report['id']}/verify").json()
        self.assertTrue(checked["errors"], "верификатор ничего не нашёл")
        fresh = self.repos.reports.get(self.report["id"])
        self.assertEqual("approved", fresh.status,
                         "отправленный отчёт молча стал черновиком")
        self.assertEqual(checked["errors"],
                         sum(1 for i in fresh.issues if i["level"] == "error"),
                         "замечания не сохранены")
        self.assertEqual("approved", self.case_now()["status"])
        self.assertEqual("ИСХ-2026-1147", self.case_now()["outgoing_no"])

    def test_the_document_that_goes_out_carries_the_real_numbers(self):
        """Документ уходит адресату, а не остаётся в системе.

        В колонтитуле каждой страницы стоял внутренний учётный номер — тот,
        что придумала система. Снаружи он никому ничего не говорит: искать
        документ в делопроизводстве будут по входящему и исходящему.
        """
        from reportgen.export.docx import footer_for

        self.client.patch(f"/api/cases/{self.case['id']}",
                          json={"incoming_no": "ВХ-2026-0412"})
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})

        line = footer_for(self.case["case_id"], "ВХ-2026-0412", "ИСХ-2026-1147")
        self.assertIn("исх. ИСХ-2026-1147", line)
        self.assertIn("на вх. ВХ-2026-0412", line)
        self.assertNotIn(self.case["case_id"], line,
                         "внутренний номер уехал адресату")
        # Пока номеров нет, остаётся прежняя подпись — документ всё равно
        # должен как-то называться.
        self.assertIn("по обращению", footer_for("ОБР-1"))

    def test_the_engineer_sees_how_many_answers_he_sent(self):
        # Проверяет начальник, отправляет исполнитель — и отчитывается он
        # именно этим числом.
        self.check()
        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        self.assertEqual(1, self.client.get("/api/me/summary").json()["sent"])
        self.login("nachalnik")
        self.assertEqual(0, self.client.get("/api/me/summary").json()["sent"],
                         "отправка записана не тому, кто отправлял")

    def test_one_outgoing_number_may_close_several_letters(self):
        """Одним исходящим отвечают сразу на несколько писем.

        Так заведено в делопроизводстве, и система в это не вмешивается:
        её дело — записать, под каким номером ушёл ответ, а не решать за
        отдел, сколько писем одним ответом закрывать.
        """
        self.check()
        first = self.client.post(f"/api/cases/{self.case['id']}/send",
                                 json={"outgoing_no": "ИСХ-2026-1147"})
        self.assertEqual(200, first.status_code, first.text)

        # Второе письмо, тот же исходящий номер.
        other = self.create_case({**CASE, "case_id": "SUP-ОБЩИЙ-ИСХ"})
        second = self.client.post(f"/api/cases/{other['id']}/send",
                                  json={"outgoing_no": "ИСХ-2026-1147"})
        self.assertEqual(200, second.status_code, second.text)

        # И по номеру находятся оба.
        found = self.client.get("/api/cases", params={"q": "ИСХ-2026-1147"}).json()
        self.assertEqual(2, found["total"])

    def test_movement_counts_answers_that_actually_went_out(self):
        """«Ответов отправлено» — это отправленные, а не проверенные.

        Счёт шёл по отметке начальника. После разделения «проверен» и
        «отправлено» это разные числа: отчёт может лежать проверенным
        неделю, а отчётность отдела уже посчитала ответ ушедшим.
        """
        self.check()
        before = self.client.get("/api/board").json()["movement"]
        self.assertEqual(0, before["sent"], "проверенный отчёт посчитан отправленным")
        self.assertEqual(1, before["checked"])

        self.client.post(f"/api/cases/{self.case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        after = self.client.get("/api/board").json()["movement"]
        self.assertEqual(1, after["sent"])


class OwnReportTests(WebTestCase):
    """Свой отчёт по уже заведённому письму."""

    def setUp(self):
        super().setUp()
        self.login("engineer")
        response = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0720", "title": "Проверка полосы"})
        self.case = response.json()["case"]

    def upload(self, submit="1"):
        return self.client.post(
            "/api/reports/upload",
            files={"file": ("мой-отчёт.md", "# Отчёт\n\nОтклонений нет.".encode("utf-8"),
                            "text/markdown")},
            data={"case_id": self.case["case_id"], "submit": submit})

    def test_a_report_lands_on_the_letter_and_not_on_a_twin(self):
        """Помощник сыроват, и отчёты пишут руками — класть их надо на письмо.

        Раньше для этого возвращались в список и вводили входящий номер
        заново: ошибся в знаке — вместо новой редакции по своему письму
        заводилось второе письмо-двойник.
        """
        response = self.upload()
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(1, self.client.get("/api/cases").json()["total"],
                         "завелось второе письмо вместо редакции")
        reports = self.client.get(f"/api/cases/{self.case['id']}").json()["reports"]
        self.assertEqual(1, len(reports))
        full = self.client.get(f"/api/reports/{reports[0]['id']}").json()["report"]
        self.assertTrue(full["uploaded"], "отчёт не помечен как сданный файлом")

    def test_uploading_from_the_letter_does_not_send_it_off_at_once(self):
        """С открытого письма отчёт сперва смотрят, а потом отправляют.

        Иначе ошибочный файл оказывается на столе у начальника раньше, чем
        его успели заметить.
        """
        report = self.upload(submit="0").json()["report"]
        self.assertEqual("draft", report["status"])
        case = self.client.get(f"/api/cases/{self.case['id']}").json()["case"]
        self.assertEqual("draft", case["status"], "письмо ушло на проверку само")

        # Отправляет человек — отдельным движением.
        sent = self.client.post(f"/api/reports/{report['id']}/submit", json={})
        self.assertEqual(200, sent.status_code, sent.text)
        case = self.client.get(f"/api/cases/{self.case['id']}").json()["case"]
        self.assertEqual("review", case["status"])

    def test_from_the_list_the_report_still_goes_straight_to_the_head(self):
        # Там человек нажал «Сдать готовый отчёт» — он за тем и пришёл.
        report = self.upload(submit="1").json()["report"]
        self.assertEqual("review", report["status"])
        case = self.client.get(f"/api/cases/{self.case['id']}").json()["case"]
        self.assertEqual("review", case["status"])


class FactNamingTests(WebTestCase):
    """Значения отчёта называются по-русски, а не packet_count."""

    def test_every_fact_of_every_template_has_a_russian_name(self):
        """Ключ — имя для кода; человеку, который заносит числа, он не говорит
        ничего, а спросить не у кого.

        Названия живут в шаблоне рядом с ключами: заводят новый ключ — тут же
        и подписывают, иначе словарь разъезжается с шаблоном.
        """
        outlines = self.client.get("/api/config").json()["outlines"]
        self.assertTrue(outlines, "шаблонов нет — проверять нечего")
        for outline in outlines:
            titles = outline["fact_titles"]
            keys = set()
            for section in outline["sections"]:
                keys |= set(section["required_facts"]) | set(section["optional_facts"])
            with self.subTest(report_type=outline["report_type"]):
                missing = sorted(keys - set(titles))
                self.assertEqual([], missing, f"без названия остались: {missing}")
                for key, title in titles.items():
                    self.assertNotEqual(key, title, f"«{key}» подписан сам собой")
                    self.assertTrue(any(ch.isalpha() and ch.lower() in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
                                        for ch in title),
                                    f"название «{title}» не по-русски")

    def test_the_skeleton_comes_named_and_measured(self):
        # Заготовку собирает сервер, и человек видит в ней название и
        # единицу — а не ключ дважды, как было.
        case = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0730", "title": "Помеха",
            "report_type": "signal_issue"}).json()["case"]
        facts = self.client.get(f"/api/cases/{case['id']}").json()["case"]["facts"]
        snr = facts["measurements"]["snr"]
        self.assertEqual("Отношение сигнал/шум", snr["title"])
        self.assertEqual("дБ", snr["unit"])
        self.assertEqual("", snr["value"], "значение должен занести человек")


class LetterSearchTests(WebTestCase):
    """Поиск по письмам: реквизиты и текст самих отчётов."""

    def with_text(self, case_id, title, text):
        case = self.create_case({**CASE, "case_id": case_id})
        self.client.patch(f"/api/cases/{case['id']}", json={"title": title})
        report = self.generate(case["id"])
        self.client.put(
            f"/api/reports/{report['id']}/sections/{report['sections'][0]['section_id']}",
            json={"text": text})
        return case

    def found(self, query):
        body = self.client.get("/api/cases", params={"q": query}).json()
        return {item["case_id"]: item["found_in_report"] for item in body["items"]}

    def test_search_finds_words_from_the_report_body(self):
        """«Искать отчёты» — значит и по тому, что в них написано.

        Инженер помнит пару слов из вывода прошлогоднего отчёта, а не его
        учётный номер. Поиск подстрокой по теме тут не поможет.
        """
        self.with_text("SUP-ПОИСК-1", "Разбор обращения",
                       "Источник — неисправный возбудитель передатчика.")
        self.with_text("SUP-ПОИСК-2", "Совсем другое", "Ничего похожего здесь нет.")
        self.assertEqual({"SUP-ПОИСК-1": True}, self.found("возбудитель"))

    def test_search_knows_russian_word_forms(self):
        # «помеха» и «помехи» — одно слово. Подстрокой это не берётся.
        self.with_text("SUP-ПОИСК-3", "Обращение",
                       "Обнаружена узкополосная помеха в стволе.")
        for query in ("помеха", "помехи", "помехой", "узкополосный", "стволы"):
            with self.subTest(query=query):
                self.assertIn("SUP-ПОИСК-3", self.found(query),
                              f"не нашлось по «{query}»")

    def test_a_word_from_the_subject_is_not_called_a_word_from_the_report(self):
        # Пометка «в тексте отчёта» должна стоять только там, где иначе
        # непонятно, почему письмо в выдаче.
        self.with_text("SUP-ПОИСК-4", "Помеха в стволе", "Разбор проведён.")
        self.assertEqual({"SUP-ПОИСК-4": False}, self.found("помехи"))

    def test_all_letter_fields_are_searchable(self):
        case = self.create_case({**CASE, "case_id": "SUP-ПОИСК-5"})
        self.client.patch(f"/api/cases/{case['id']}", json={
            "title": "Обращение по РРЛ", "incoming_no": "ВХ-2026-0412",
            "note": "уточнение запрошено телефонограммой"})
        for query in ("SUP-ПОИСК-5", "ВХ-2026-0412", "0412", "РРЛ",
                      "1274", "телефонограммой"):
            with self.subTest(query=query):
                self.assertIn("SUP-ПОИСК-5", self.found(query),
                              f"не нашлось по «{query}»")

    def test_the_outgoing_number_is_searchable(self):
        # По исходящему в отделе ищут не реже, чем по входящему.
        case = self.create_case({**CASE, "case_id": "SUP-ПОИСК-6"})
        report = self.generate(case["id"])
        for section in report["sections"]:
            self.client.put(
                f"/api/reports/{report['id']}/sections/{section['section_id']}",
                json={"text": "Текст инженера без числовых значений."})
        self.client.post(f"/api/reports/{report['id']}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{report['id']}/approve")
        self.login("engineer")
        self.client.post(f"/api/cases/{case['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-1147"})
        self.assertIn("SUP-ПОИСК-6", self.found("ИСХ-2026-1147"))
        self.assertIn("SUP-ПОИСК-6", self.found("1147"))

    def test_a_full_number_does_not_drag_out_the_whole_journal(self):
        """Полный номер должен находить одно письмо, а не половину журнала.

        Слова запроса соединяются по «и»: список писем не ранжируется —
        он сортируется по сроку, как нужно отделу, — и «или» вытащило бы
        всё, где есть хотя бы «2026».
        """
        for number in range(1, 6):
            case = self.create_case({**CASE, "case_id": f"SUP-ЖУРНАЛ-{number}"})
            self.client.patch(f"/api/cases/{case['id']}",
                              json={"incoming_no": f"ВХ-2026-{number:04d}"})
        self.assertEqual(["SUP-ЖУРНАЛ-3"], list(self.found("ВХ-2026-0003")))
        self.assertEqual(5, len(self.found("ВХ-2026")))

    def test_a_report_handed_in_as_a_file_is_searchable_too(self):
        """Сдали файлом — текст должен искаться так же, как у собранного.

        Обновление указателя стояло в сервисном слое, а сдача файлом идёт
        мимо него: отчёт лежал в базе, а поиск по его тексту не находил
        ничего. Теперь указатель обновляет само хранилище — там, где
        пишется текст, и забыть про него негде.
        """
        self.client.post("/api/reports/upload", data={
            "case_id": "ВХ-ФАЙЛ-1", "incoming_no": "ВХ-ФАЙЛ-1",
            "report_type": CASE["report_type"]},
            files={"file": ("о.md",
                            "# Отчёт\n\nОбнаружен обрыв фидера антенны.\n".encode(),
                            "text/markdown")})
        self.assertEqual({"ВХ-ФАЙЛ-1": True}, self.found("фидера"))
        self.assertEqual({"ВХ-ФАЙЛ-1": True}, self.found("антенна"))

    def test_the_index_takes_the_file_name_and_the_remark_too(self):
        """У сканированного PDF текст не извлекается вовсе.

        Тогда письмо не найти ничем, кроме имени файла. Замечание
        начальника тоже ищут — «что мне тогда вернули по этому письму».
        """
        body = self.client.post("/api/reports/upload", data={
            "case_id": "ВХ-СКАН-1", "incoming_no": "ВХ-СКАН-1",
            "title": "Разбор обращения", "report_type": CASE["report_type"]},
            files={"file": ("Скан рефлектограммы.md", b"---\n", "text/markdown")}).json()
        self.assertIn("ВХ-СКАН-1", self.found("рефлектограммы"))

        self.login("nachalnik")
        self.client.post(f"/api/reports/{body['report']['id']}/rework",
                         json={"note": "нет ссылки на методику поверки"})
        self.login("engineer")
        self.assertIn("ВХ-СКАН-1", self.found("поверки"))

    def test_the_index_can_be_rebuilt_on_demand(self):
        # Перестроение может оборваться на середине: указатель наполовину
        # полон, признака «пуст» уже нет, и часть писем не находится.
        self.with_text("SUP-ЗАНОВО-1", "Тема", "Слово для проверки: интермодуляция.")
        self.repos.db.connection.execute("DELETE FROM cases_fts")
        self.repos.db.connection.commit()
        self.assertEqual({}, self.found("интермодуляция"))

        self.login("engineer")
        self.assertEqual(403, self.client.post("/api/cases/reindex").status_code,
                         "перестроение указателя доступно не администратору")
        self.login("admin")
        done = self.client.post("/api/cases/reindex")
        self.assertEqual(200, done.status_code, done.text)
        self.assertIn("SUP-ЗАНОВО-1", self.found("интермодуляция"))

    def test_a_deleted_letter_stops_being_found(self):
        # У виртуальной таблицы нет внешнего ключа: каскад её не трогает,
        # и удалённое письмо находилось бы поиском вечно.
        case = self.with_text("SUP-ПОИСК-7", "Тема", "Совершенно особое слово: криптоанализ.")
        self.assertIn("SUP-ПОИСК-7", self.found("криптоанализ"))
        self.assertEqual(200, self.client.delete(f"/api/cases/{case['id']}").status_code)
        self.assertEqual({}, self.found("криптоанализ"))
        self.assertEqual(0, self.repos.db.scalar("SELECT count(*) FROM cases_fts"))

    def test_the_index_is_built_for_letters_that_predate_it(self):
        """Поиск по тексту появился позже писем.

        На базе отдела указателя ещё нет, и просить «переиндексируйте»
        нельзя: система строит его сама при первом запуске новой версии.
        """
        self.with_text("SUP-ПОИСК-8", "Тема", "Слово для проверки: рефлектометр.")
        self.repos.db.connection.execute("DELETE FROM cases_fts")
        self.repos.db.connection.commit()
        self.assertEqual({}, self.found("рефлектометр"))
        self.assertTrue(self.repos.case_search.is_empty())

        self.repos.case_search.rebuild_all()
        self.assertIn("SUP-ПОИСК-8", self.found("рефлектометр"))


class LetterRegistrationTests(WebTestCase):
    """Регистрация письма: линия связи, номер ТС, бумаги, без JSON."""

    def register(self, **fields):
        payload = {"case_id": "ВХ-2026-0500", "title": "Описание письма"}
        payload.update(fields)
        response = self.client.post("/api/cases", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["case"]

    def test_a_letter_registers_without_a_fact_pack(self):
        """Письмо только спустили — чисел в нём ещё нет.

        Заготовку по шаблону собирает система: требовать JSON от того, кто
        регистрирует входящее, значит требовать того, чего он знать не может.
        """
        case = self.register()
        self.assertEqual("ВХ-2026-0500", case["case_id"])
        # Тип отчёта выбран сам, шаблон найден, заготовка собрана.
        self.assertTrue(case["report_type"])
        full = self.client.get(f"/api/cases/{case['id']}").json()["case"]
        self.assertIn("measurements", full["facts"])
        self.assertEqual(case["case_id"], full["facts"]["case_id"])
        # Ключи взяты из шаблона, а не выдуманы.
        outline = self.service.outlines.get(case["report_type"])
        expected = {key for section in outline.sections
                    for key in section.required_facts}
        self.assertEqual(expected, set(full["facts"]["measurements"]))

    def test_the_communication_line_is_one_of_the_known_ones(self):
        # Отдел работает по линиям, и «РРЛС» в поле — не свободный текст:
        # по нему потом отбирают письма своего хозяйства.
        case = self.register(line_type="rrls", tc_no="ТС-3081-07")
        self.assertEqual("rrls", case["line_type"])
        self.assertEqual("РРЛС", case["line_title"])
        self.assertEqual("ТС-3081-07", case["tc_no"])

        bad = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0501", "line_type": "оптика"})
        self.assertEqual(400, bad.status_code)
        self.assertIn("линия связи", bad.json()["error"])

    def test_the_line_may_be_left_empty(self):
        # Письмо иногда спускают раньше, чем ясно, к какой линии оно
        # относится. Запирать регистрацию из-за этого нельзя.
        case = self.register(line_type="")
        self.assertEqual("", case["line_type"])

    def test_the_equipment_number_is_searchable(self):
        # Номер ТС ищется наравне с номерами письма: в отделе спрашивают
        # «что было по этому средству», а не «по этому входящему».
        self.register(tc_no="ТС-3081-07")
        found = self.client.get("/api/cases", params={"q": "ТС-3081-07"}).json()
        self.assertEqual(["ВХ-2026-0500"], [item["case_id"] for item in found["items"]])
        # И по обрывку номера тоже: человек помнит «3081».
        found = self.client.get("/api/cases", params={"q": "3081"}).json()
        self.assertIn("ВХ-2026-0500", [item["case_id"] for item in found["items"]])

    def test_the_line_and_the_number_are_editable_in_the_card(self):
        case = self.register()
        response = self.client.patch(f"/api/cases/{case['id']}", json={
            "line_type": "sls", "tc_no": "ТС-9", "title": "Новое описание"})
        self.assertEqual(200, response.status_code, response.text)
        updated = response.json()["case"]
        self.assertEqual("sls", updated["line_type"])
        self.assertEqual("ТС-9", updated["tc_no"])
        self.assertEqual("Новое описание", updated["title"])


class LetterCardFieldTests(WebTestCase):
    """Карточка письма: входящее, указания, ТС, регистрации, ответ."""

    def register(self, **fields):
        payload = {"case_id": "ВХ-2026-0700", "title": "Проверка обмена по линии"}
        payload.update(fields)
        response = self.client.post("/api/cases", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["case"]

    def test_the_incoming_paper_is_written_down_whole(self):
        """Входящее письмо приходит с полным набором реквизитов.

        Их вписывают один раз при регистрации: разыскивать потом, каким
        указанием спущено письмо и по какому средству, придётся по бумажному
        журналу.
        """
        case = self.register(
            incoming_date="2026-08-14", order_no="УК-142", order_date="2026-08-12",
            tc_no="ТС-3081-07", tc_date="2026-08-10", line_type="rrls",
            registrations=3, note="Срочно, доложить начальнику отдела")
        self.assertEqual("2026-08-14", case["incoming_date"])
        self.assertEqual("УК-142", case["order_no"])
        self.assertEqual("2026-08-12", case["order_date"])
        self.assertEqual("ТС-3081-07", case["tc_no"])
        self.assertEqual("2026-08-10", case["tc_date"])
        self.assertEqual("rrls", case["line_type"])
        self.assertEqual(3, case["registrations"])
        self.assertIn("Срочно", case["note"])

    def test_the_fields_survive_a_reload(self):
        # Поле, которое сервер принял и потерял, хуже отсутствующего.
        case = self.register(order_no="УК-142", tc_date="2026-08-10", registrations=2)
        fresh = self.client.get(f"/api/cases/{case['id']}").json()["case"]
        self.assertEqual("УК-142", fresh["order_no"])
        self.assertEqual("2026-08-10", fresh["tc_date"])
        self.assertEqual(2, fresh["registrations"])

    def test_the_fields_are_editable_afterwards(self):
        # Указания приходят вдогонку письму — вписать их надо потом.
        case = self.register()
        response = self.client.patch(f"/api/cases/{case['id']}", json={
            "order_no": "УК-201", "order_date": "2026-08-20", "registrations": 5})
        self.assertEqual(200, response.status_code, response.text)
        updated = response.json()["case"]
        self.assertEqual("УК-201", updated["order_no"])
        self.assertEqual("2026-08-20", updated["order_date"])
        self.assertEqual(5, updated["registrations"])

    def test_the_order_number_is_searchable(self):
        # «Что было по указанию УК-142» — обычный вопрос отдела.
        self.register(order_no="УК-142")
        found = self.client.get("/api/cases", params={"q": "УК-142"}).json()
        self.assertEqual(["ВХ-2026-0700"], [item["case_id"] for item in found["items"]])
        # И по обрывку номера: человек помнит «142», а не «УК-142» целиком.
        # Обрывок берётся только подстрокой — полнотекстовый указатель ищет
        # словами, и на кусок слова он письма не найдёт.
        part = self.client.get("/api/cases", params={"q": "К-142"}).json()
        self.assertIn("ВХ-2026-0700", [item["case_id"] for item in part["items"]])

    def test_a_number_of_registrations_is_a_number(self):
        # «Количество регистраций» — счёт, а не свободный текст.
        bad = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0701", "registrations": "много"})
        self.assertEqual(400, bad.status_code)

    def test_a_crooked_date_is_refused_where_it_is_typed(self):
        bad = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0702", "order_date": "12.08.2026"})
        self.assertEqual(400, bad.status_code)
        self.assertIn("дата", bad.json()["error"].lower())

    def test_the_answer_is_written_down_with_its_note(self):
        """После проверки начальником ответ уходит — и это тоже запись.

        Номер исходящего, дата и примечание: без них в учёте дыра — письмо
        пришло, работа сделана, а чем ответили, неизвестно.
        """
        case = self.register()
        report = self.generate(case["id"])
        self.submit(report["id"])
        self.login("nachalnik")
        self.client.post(f"/api/reports/{report['id']}/approve", json={})
        self.login("admin")
        response = self.client.post(f"/api/cases/{case['id']}/send", json={
            "outgoing_no": "ИСХ-2026-0912", "outgoing_date": "2026-08-28",
            "outgoing_note": "Отправлено с приложением на 4 листах"})
        self.assertEqual(200, response.status_code, response.text)
        sent = response.json()["case"]
        self.assertEqual("ИСХ-2026-0912", sent["outgoing_no"])
        self.assertEqual("2026-08-28", sent["outgoing_date"])
        self.assertEqual("Отправлено с приложением на 4 листах", sent["outgoing_note"])
        self.assertEqual("approved", sent["status"])

    def test_papers_of_the_answer_are_kept_apart_from_the_letter(self):
        """Приложения к ответу — не бумаги входящего.

        В одной куче исполнитель через полгода не отличит скан письма от
        того, что ушло с ответом.
        """
        case = self.register()
        self.client.post(
            f"/api/cases/{case['id']}/files",
            files={"file": ("вход.txt", b"skan pisma", "text/plain")})
        self.client.post(
            f"/api/cases/{case['id']}/files", data={"stage": "outgoing"},
            files={"file": ("prilozhenie.txt", b"prilozhenie k otvetu", "text/plain")})

        everything = self.client.get(f"/api/cases/{case['id']}/files").json()["files"]
        self.assertEqual({"вход.txt": "incoming", "prilozhenie.txt": "outgoing"},
                         {item["name"]: item["stage"] for item in everything})
        only_out = self.client.get(f"/api/cases/{case['id']}/files",
                                   params={"stage": "outgoing"}).json()["files"]
        self.assertEqual(["prilozhenie.txt"], [item["name"] for item in only_out])
        self.assertEqual("к ответу", only_out[0]["stage_title"])


class LetterDeletionTests(WebTestCase):
    """Ошибочно заведённое письмо убирает тот, кто его завёл."""

    def setUp(self):
        super().setUp()
        self.login("engineer")
        self.engineer = self.repos.users.by_login("engineer")

    def register(self, case_id="ВХ-2026-0700"):
        response = self.client.post("/api/cases", json={
            "case_id": case_id, "title": "Ошибся при регистрации"})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["case"]

    def test_the_author_takes_back_their_own_mistake(self):
        """Ходить за начальником из-за собственной описки человек не должен."""
        case = self.register()
        response = self.client.delete(f"/api/cases/{case['id']}")
        self.assertEqual(200, response.status_code, response.text)
        self.assertIsNone(self.repos.cases.get(case["id"]))

    def test_a_stranger_letter_is_not_yours_to_remove(self):
        self.login("admin")
        case = self.register("ВХ-2026-0701")
        self.login("engineer")
        response = self.client.delete(f"/api/cases/{case['id']}")
        self.assertEqual(403, response.status_code)
        self.assertIn("чужое письмо", response.json()["error"])

    def test_once_the_work_started_it_is_not_a_mistake_anymore(self):
        """По письму сделали отчёт — оно стало работой отдела, а не опиской."""
        case = self.register("ВХ-2026-0702")
        self.generate(case["id"])
        response = self.client.delete(f"/api/cases/{case['id']}")
        self.assertEqual(409, response.status_code)
        self.assertIn("уже есть отчёт", response.json()["error"])
        # Администратор такое письмо всё же удаляет.
        self.login("admin")
        self.assertEqual(200, self.client.delete(
            f"/api/cases/{case['id']}").status_code)

    def test_a_letter_with_an_answer_sent_is_never_a_mistake(self):
        case = self.register("ВХ-2026-0703")
        self.repos.cases.update_card(case["id"], outgoing_no="ИСХ-2026-1")
        response = self.client.delete(f"/api/cases/{case['id']}")
        self.assertEqual(409, response.status_code)
        self.assertIn("отправлен ответ", response.json()["error"])


class AttachmentPreviewTests(WebTestCase):
    """Приложенный файл смотрят на экране, не скачивая."""

    def setUp(self):
        super().setUp()
        response = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0710", "title": "Помеха на линии"})
        self.case = response.json()["case"]

    def attach(self, name, body):
        return self.client.post(f"/api/cases/{self.case['id']}/files",
                                files={"file": (name, body, "application/octet-stream")})

    def test_a_picture_opens_in_the_page_instead_of_downloading(self):
        item = self.attach("скан.png", _tiny_png()).json()["file"]
        shown = self.client.get(
            f"/api/cases/{self.case['id']}/files/{item['id']}?inline=1")
        self.assertEqual(200, shown.status_code)
        self.assertTrue(shown.headers["content-disposition"].startswith("inline;"))
        self.assertEqual("image/png", shown.headers["content-type"])
        # Чужой файл в своём окне: исполняться он не должен.
        self.assertEqual("nosniff", shown.headers["x-content-type-options"])
        self.assertIn("sandbox", shown.headers["content-security-policy"])

        # Без флага — как и раньше, вложением.
        saved = self.client.get(f"/api/cases/{self.case['id']}/files/{item['id']}")
        self.assertTrue(saved.headers["content-disposition"].startswith("attachment;"))

    def test_a_kind_the_browser_should_not_run_is_never_shown_inline(self):
        """HTML и SVG показу не подлежат: в них живёт скрипт.

        Показанная встроенным окном страница получила бы права нашего же
        адреса, поэтому такие файлы отдаются вложением даже по запросу.
        """
        item = self.attach("выгрузка.csv", "a;b\n1;2".encode("utf-8")).json()["file"]
        shown = self.client.get(
            f"/api/cases/{self.case['id']}/files/{item['id']}?inline=1")
        self.assertTrue(shown.headers["content-type"].startswith("text/plain"))

        from reportgen.web.api import INLINE_TYPES

        for risky in (".html", ".htm", ".svg", ".js", ".zip", ".docx"):
            with self.subTest(suffix=risky):
                self.assertNotIn(risky, INLINE_TYPES)

    def test_the_person_sees_what_the_machine_read(self):
        # Чтобы не рассчитывать на распознанное там, где его нет.
        item = self.attach("журнал.txt",
                           "Ствол третий, поляризация вертикальная".encode("utf-8")).json()["file"]
        body = self.client.get(
            f"/api/cases/{self.case['id']}/files/{item['id']}/text").json()
        self.assertIn("поляризация", body["text"])
        self.assertFalse(body["recognised"], "текстовый файл не распознают")
        self.assertFalse(body["truncated"])


def _tiny_png() -> bytes:
    """Однопиксельный PNG. Меньше, чем описывать его словами."""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class OcrQualityTests(unittest.TestCase):
    """Каша из распознавания не должна попадать в поиск."""

    def test_mush_is_told_apart_from_words(self):
        from reportgen.web.api import _looks_like_mush

        mush = "Ha 4Ta 6ap n0 e | ~ 1l Bo 3a Ha ce 6b1 Tb Ha 4Ta 6ap n0 e u3"
        real = ("Схема радиорелейной линии Северная — Апатиты. Ствол третий, "
                "поляризация вертикальная, приёмник исправен.")
        self.assertTrue(_looks_like_mush(mush), "каша принята за текст")
        self.assertFalse(_looks_like_mush(real), "нормальный текст принят за кашу")
        # Короткую подпись судить не по чему — пусть остаётся.
        self.assertFalse(_looks_like_mush("Узел 3"))
        # Сплошные знаки без букв — тоже каша.
        self.assertTrue(_looks_like_mush("### @@@ %%% ^^^ &&& *** ((( ))) --- === +++ ~~~ ???"))

    def test_a_small_picture_is_enlarged_before_recognition(self):
        """На 75 dpi tesseract слипает слова — отсюда и каша.

        Снимок листа с телефона и скриншот окна — это 800–1200 точек по
        стороне, вчетверо меньше рабочих для распознавания 300 dpi.
        """
        from reportgen.ingest.formats.ocr import OCR_MIN_SIDE, _upscaled_for_ocr

        try:
            from PIL import Image
        except ImportError:                      # pragma: no cover
            self.skipTest("Pillow не установлен")

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            small = Path(folder) / "мелкая.png"
            Image.new("RGB", (900, 420), "white").save(small)
            bigger = _upscaled_for_ocr(small)
            self.assertIsNotNone(bigger, "мелкую картинку не растянули")
            try:
                with Image.open(bigger) as grown:
                    self.assertGreaterEqual(min(grown.size), OCR_MIN_SIDE)
            finally:
                bigger.unlink(missing_ok=True)

            # Большую не трогаем: растягивать нечего.
            large = Path(folder) / "крупная.png"
            Image.new("RGB", (2400, 1800), "white").save(large)
            self.assertIsNone(_upscaled_for_ocr(large))


class LetterFileTests(WebTestCase):
    """Бумаги, приложенные к письму."""

    def setUp(self):
        super().setUp()
        response = self.client.post("/api/cases", json={
            "case_id": "ВХ-2026-0600", "title": "Помеха на линии"})
        self.assertEqual(response.status_code, 200, response.text)
        self.case = response.json()["case"]

    def attach(self, name="схема.txt", body=b"", note=""):
        return self.client.post(
            f"/api/cases/{self.case['id']}/files",
            files={"file": (name, body or "текст".encode("utf-8"), "text/plain")},
            data={"note": note})

    def test_a_paper_attached_to_a_letter_can_be_downloaded_back(self):
        """Скан письма поднимают целиком, а не пересказом."""
        body = "Схема радиорелейной линии. Поляризация вертикальная.".encode("utf-8")
        response = self.attach("схема-линии.txt", body)
        self.assertEqual(200, response.status_code, response.text)
        item = response.json()["file"]
        self.assertEqual("схема-линии.txt", item["name"])
        self.assertEqual(len(body), item["size"])
        self.assertTrue(item["has_text"], "текст из файла не разобрали")

        listed = self.client.get(f"/api/cases/{self.case['id']}/files").json()["files"]
        self.assertEqual([item["id"]], [row["id"] for row in listed])

        back = self.client.get(f"/api/cases/{self.case['id']}/files/{item['id']}")
        self.assertEqual(200, back.status_code)
        self.assertEqual(body, back.content)

    def test_a_letter_is_found_by_words_from_its_papers(self):
        """Положить бумагу и не найти по ней письмо — незачем и прикладывать."""
        self.attach("схема.txt", "Поляризация вертикальная, ствол третий".encode("utf-8"))
        for query in ("поляризация", "стволы", "схема"):
            with self.subTest(query=query):
                found = self.client.get("/api/cases", params={"q": query}).json()
                self.assertIn("ВХ-2026-0600",
                              [row["case_id"] for row in found["items"]],
                              f"не нашлось по «{query}»")

    def test_removing_a_paper_takes_it_out_of_the_search_too(self):
        item = self.attach("схема.txt", "Поляризация вертикальная".encode("utf-8")).json()["file"]
        self.assertEqual(200, self.client.delete(
            f"/api/cases/{self.case['id']}/files/{item['id']}").status_code)
        found = self.client.get("/api/cases", params={"q": "поляризация"}).json()
        self.assertEqual([], found["items"], "убранная бумага осталась в поиске")

    def test_papers_of_a_sent_letter_are_not_touched(self):
        """Ответ ушёл — исходные бумаги задним числом не правят."""
        item = self.attach().json()["file"]
        report = self.generate(self.case["id"])
        self.submit(report["id"])
        self.login("nachalnik")
        self.client.post(f"/api/reports/{report['id']}/approve", json={})
        self.login("admin")
        sent = self.client.post(f"/api/cases/{self.case['id']}/send",
                                json={"outgoing_no": "ИСХ-2026-0900"})
        self.assertEqual(200, sent.status_code, sent.text)

        refused = self.client.delete(f"/api/cases/{self.case['id']}/files/{item['id']}")
        self.assertEqual(409, refused.status_code)
        self.assertIn("отправлен", refused.json()["error"])

    def test_the_letter_counts_its_papers(self):
        self.attach("первая.txt")
        self.attach("вторая.txt")
        listed = self.client.get("/api/cases").json()["items"]
        row = [item for item in listed if item["case_id"] == "ВХ-2026-0600"][0]
        self.assertEqual(2, row["files_count"])

    def test_any_kind_of_file_can_be_attached(self):
        """Перечислить заранее, что придёт с письмом, нельзя.

        В отдел приходят прошивки, выгрузки приборов, схемы в чужих САПР — и
        список разрешённых расширений отказывал ровно тем бумагам, ради
        которых письмо и завели.
        """
        for name in ("proshivka.bin", "vygruzka.dat", "shema.dwg", "bez_rasshireniya"):
            response = self.attach(name, b"\x00\x01\x02")
            self.assertEqual(200, response.status_code,
                             f"{name}: {response.text}")

    def test_a_file_of_an_unknown_kind_is_never_shown_inside_the_page(self):
        """Показать чужой файл в своей странице значит его исполнить.

        Ограничение на приём сняли, а это осталось запретом: страницу
        подписанного .html браузер выполнил бы как нашу собственную.
        """
        item = self.attach("stranica.html", b"<script>alert(1)</script>").json()["file"]
        answer = self.client.get(
            f"/api/cases/{self.case['id']}/files/{item['id']}?inline=1")
        self.assertEqual(200, answer.status_code)
        disposition = answer.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition,
                      "чужая страница открылась бы прямо в интерфейсе")
        self.assertNotIn("text/html", answer.headers.get("content-type", ""))

    def test_deleting_a_letter_takes_its_papers_off_the_disk(self):
        item = self.attach().json()["file"]
        path = Path(self.repos.case_files.get(item["id"]).path)
        self.assertTrue(path.is_file())
        self.assertEqual(200, self.client.delete(
            f"/api/cases/{self.case['id']}").status_code)
        self.assertFalse(path.exists(), "файл письма остался на диске навсегда")


class UploadedReportTests(WebTestCase):
    """Готовый отчёт, сданный файлом: его пишут не системой, а руками."""

    def upload(self, name="Отчёт по письму.md", body=b"# Otchet\n\nTekst inzhenera.\n",
               **fields):
        payload = {"case_id": "ВХ-2026-0101", "incoming_no": "ВХ-2026-0101",
                   "group_no": "1-я группа", "title": "Разбор помехи"}
        payload.update(fields)
        return self.client.post(
            "/api/reports/upload",
            files={"file": (name, body, "text/markdown")},
            data=payload,
        )

    def test_engineer_hands_in_a_finished_report(self):
        """Сдать отчёт файлом может любой военнослужащий, и он сразу на проверке."""
        self.login("engineer")
        response = self.upload()
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("review", body["report"]["status"])
        self.assertTrue(body["report"]["uploaded"])
        self.assertEqual("Отчёт по письму.md", body["report"]["file_name"])
        # Реквизиты письма заполнены тем, что ввели при загрузке.
        self.assertEqual("ВХ-2026-0101", body["case"]["incoming_no"])
        self.assertEqual("1-я группа", body["case"]["group_no"])
        self.assertEqual("Разбор помехи", body["case"]["title"])

    def test_executor_defaults_to_the_person_who_handed_it_in(self):
        # Чаще всего сдаёт тот, кто писал: заставлять выбирать себя из
        # списка — лишнее действие на ровном месте.
        self.login("engineer")
        engineer = self.repos.users.by_login("engineer")
        body = self.upload().json()
        self.assertEqual(engineer.id, body["case"]["assignee_id"])
        self.assertEqual("Инженеров И. И.", body["case"]["assignee_name"])

    def test_executor_can_be_someone_else(self):
        engineer = self.repos.users.by_login("engineer")
        body = self.upload(assignee_id=str(engineer.id)).json()
        self.assertEqual(engineer.id, body["case"]["assignee_id"])

    def test_the_file_comes_back_exactly_as_it_was_handed_in(self):
        raw = "# Отчёт\n\nИзмерения приложены отдельно.\n".encode("utf-8")
        report = self.upload(name="Отчёт.md", body=raw).json()["report"]
        got = self.client.get(f"/api/reports/{report['id']}/file")
        self.assertEqual(200, got.status_code, got.text)
        self.assertEqual(raw, got.content)

    def test_everyone_can_read_a_handed_in_report(self):
        # Смотреть и искать отчёты могут все — для того и заведён учёт.
        report = self.upload().json()["report"]
        self.login("engineer")
        self.assertEqual(200, self.client.get(f"/api/reports/{report['id']}").status_code)
        self.assertEqual(200, self.client.get(f"/api/reports/{report['id']}/file").status_code)

    def test_head_passes_a_handed_in_report_without_the_verifier(self):
        """У сданного файлом отчёта факт-пакета нет, сверять числа не с чем.

        Проверяет его человек — на то он и начальник.
        """
        report = self.upload().json()["report"]
        self.login("nachalnik")
        done = self.client.post(f"/api/reports/{report['id']}/approve")
        self.assertEqual(200, done.status_code, done.text)
        self.assertEqual("approved", done.json()["report"]["status"])

    def test_head_sends_a_handed_in_report_back(self):
        report = self.upload().json()["report"]
        self.login("nachalnik")
        back = self.client.post(f"/api/reports/{report['id']}/rework",
                                json={"note": "нет подписи исполнителя"})
        self.assertEqual(200, back.status_code, back.text)
        self.assertEqual("нет подписи исполнителя", back.json()["report"]["review_note"])

    def test_details_typed_for_an_existing_letter_are_applied(self):
        """Введённые реквизиты не должны пропадать.

        У заведённого письма из формы брался только исполнитель, а тема,
        номер группы, даты и важность выбрасывались молча.
        """
        self.upload(group_no="1274", title="Первая тема")
        again = self.upload(name="Отчёт-2.md", group_no="2-я группа связи",
                            title="Уточнённая тема", deadline="2026-09-30",
                            priority="urgent").json()
        case = again["case"]
        self.assertEqual("2-я группа связи", case["group_no"])
        self.assertEqual("Уточнённая тема", case["title"])
        self.assertEqual("2026-09-30", case["deadline"])
        self.assertEqual("urgent", case["priority"])

    def test_empty_fields_do_not_wipe_what_the_letter_already_has(self):
        first = self.upload(group_no="1274", title="Первая тема").json()["case"]
        again = self.upload(name="Отчёт-2.md", group_no="", title="").json()["case"]
        self.assertEqual(first["group_no"], again["group_no"])
        self.assertEqual(first["title"], again["title"])

    def test_handing_in_a_report_does_not_steal_someone_elses_letter(self):
        """Сдача отчёта по чужому письму не переводит письмо на себя."""
        engineer = self.repos.users.by_login("engineer")
        self.upload(assignee_id=str(engineer.id))
        self.login("nachalnik")
        body = self.upload(name="Отчёт-2.md").json()
        self.assertEqual(engineer.id, body["case"]["assignee_id"],
                         "письмо молча переписано на сдавшего")
        self.assertIn("исполнитель письма не изменён", body["note"])

    def test_letter_without_an_executor_gets_the_one_who_handed_it_in(self):
        self.login("engineer")
        engineer = self.repos.users.by_login("engineer")
        case = self.upload().json()["case"]
        self.assertEqual(engineer.id, case["assignee_id"])

    def test_text_of_the_handed_in_report_is_readable(self):
        """Отчёт должен читаться в карточке, а не только скачиваться.

        Формат разбирают по расширению файла, а временное имя, под которым
        файл писался на диск, расширения не имело: текст не извлекался ни
        из одного сданного отчёта — «неподдерживаемый формат без
        расширения», — и в карточке оставался один файл.
        """
        body = self.upload(
            name="Отчёт группы.md",
            body="# Отчёт\n\nПомеха устранена, замер в норме.\n".encode("utf-8")).json()
        self.assertEqual("", body.get("note", ""), body.get("note"))
        report = self.client.get(f"/api/reports/{body['report']['id']}").json()["report"]
        self.assertIn("Помеха устранена", report["markdown"])

    def test_handing_in_the_same_letter_at_once_does_not_break(self):
        """Двойное нажатие и двое разом — обычное дело в отделе.

        Номер версии считался до записи, а письмо заводилось после
        проверки «уже есть»: обе гонки давали то отказ, то срыв сервера.
        Сдачи должны выстроиться в очередь и получить номера подряд, а
        на проверке остаётся последняя.
        """
        import threading

        answers = []

        def hand_in(number):
            answers.append(self.upload(name=f"о{number}.md",
                                       case_id="ВХ-РАЗОМ", incoming_no="ВХ-РАЗОМ"))

        threads = [threading.Thread(target=hand_in, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        codes = sorted(answer.status_code for answer in answers)
        self.assertEqual([200] * 6, codes, [a.text[:120] for a in answers])
        case = self.repos.cases.by_case_id("ВХ-РАЗОМ")
        versions = sorted(r.version for r in self.repos.reports.list_for_case(case.id))
        self.assertEqual([1, 2, 3, 4, 5, 6], versions)
        on_desk = [r for r in self.repos.reports.list_for_case(case.id)
                   if r.status == "review"]
        self.assertEqual([6], [r.version for r in on_desk],
                         "на проверке должна остаться последняя сдача")

    def test_a_new_hand_in_takes_the_previous_one_off_the_desk(self):
        # Сдал заново — прежнее отозвал: начальник читает последнее.
        first = self.upload().json()["report"]
        self.assertEqual("review", first["status"])
        second = self.upload(name="Отчёт исправленный.md").json()["report"]
        self.assertEqual("draft", self.repos.reports.get(first["id"]).status)
        self.assertEqual("review", self.repos.reports.get(second["id"]).status)

    def test_handed_in_report_is_not_exported_as_our_own(self):
        """Наружу такой отчёт уходит подлинником, а не пересборкой.

        Текст сданного файлом отчёта — машинное чтение чужого документа:
        оформление, таблицы и подписи в нём уже потеряны. Собранный из
        него DOCX по фирменному бланку выглядит как отчёт и рискует уйти
        адресату вместо подлинника.
        """
        report = self.upload().json()["report"]
        for path in ("export.md", "export.docx"):
            with self.subTest(path=path):
                refused = self.client.get(f"/api/reports/{report['id']}/{path}")
                self.assertEqual(409, refused.status_code)
                self.assertIn("Скачать файл", refused.json()["error"])
        # А подлинник отдаётся всем.
        self.assertEqual(200, self.client.get(
            f"/api/reports/{report['id']}/file").status_code)

    def test_verification_says_it_had_nothing_to_check(self):
        """«Замечаний нет» и «сверять было нечем» — разные ответы.

        Верификатор докладывал по сданному файлом отчёту, что «обязательный
        раздел отсутствует», перечисляя разделы чужого шаблона, взятого
        письму по умолчанию. Факт-пакета за таким отчётом нет: документ
        целиком написал человек, читает его начальник.
        """
        report = self.upload().json()["report"]
        body = self.client.post(f"/api/reports/{report['id']}/verify").json()
        self.assertEqual([], body["issues"], "верификатор придумал замечания")
        self.assertFalse(body["checked"])
        self.assertIn("сдан файлом", body["note"])

    def test_direction_of_work_is_taken_from_the_form(self):
        # Направление писали первым по алфавиту — письмо выходило под чужой
        # темой. Оно приходит из формы сдачи.
        body = self.upload(report_type="signal_issue").json()
        self.assertEqual("signal_issue", body["case"]["report_type"])
        refused = self.upload(case_id="ВХ-2026-0102", incoming_no="ВХ-2026-0102",
                              report_type="такого-нет")
        self.assertEqual(400, refused.status_code)
        self.assertIn("тип отчёта", refused.json()["error"])

    def test_editing_the_facts_does_not_touch_the_handed_in_report(self):
        # Перепроверка письма проходит по всем отчётам. Сданный файлом
        # с факт-пакетом не связан: замечания по нему были бы выдуманными.
        body = self.upload().json()
        report_id = body["report"]["id"]
        facts = self.client.get(
            f"/api/cases/{body['case']['id']}").json()["case"]["facts"]
        facts["group_no"] = "4-я группа"
        self.assertEqual(200, self.client.put(
            f"/api/cases/{body['case']['id']}/facts", json={"facts": facts}).status_code)
        fresh = self.client.get(f"/api/reports/{report_id}").json()["report"]
        self.assertEqual([], fresh["issues"], "по сданному файлу выписали замечания")
        self.assertEqual("review", fresh["status"], "отчёт сняли с проверки без причины")

    def test_deleting_a_letter_removes_the_handed_in_files(self):
        """Интерфейс обещает удаление вместе со всеми версиями отчёта.

        Строки из базы уходили каскадом, а файлы оставались на диске
        навсегда — содержимое удалённого отчёта лежало бы там годами.
        """
        from pathlib import Path as _P

        body = self.upload().json()
        folder = _P(self.tmp / "reports" / str(body["case"]["id"]))
        self.assertTrue(any(folder.iterdir()), "файл не сохранён")
        self.assertEqual(200, self.client.delete(
            f"/api/cases/{body['case']['id']}").status_code)
        self.assertFalse(folder.exists() and any(folder.iterdir()),
                         "файл сданного отчёта остался на диске")

    def test_second_report_on_the_same_letter_is_a_new_version(self):
        first = self.upload().json()["report"]
        second = self.upload(name="Отчёт исправленный.md").json()["report"]
        self.assertEqual(first["version"] + 1, second["version"])
        self.assertEqual(first["case_ref"], second["case_ref"])

    def test_two_letters_with_similar_numbers_do_not_share_files(self):
        """«ВХ-2026/0423» и «ВХ-2026-0423» — разные письма.

        После чистки имени они совпадают, и отчёты по ним ложились в один
        каталог с одинаковым именем: второй затирал первый насмерть.
        """
        first = self.upload(name="Отчёт.md", body=b"pervyi otchet",
                            case_id="BX-2026/0423", incoming_no="BX-2026/0423").json()
        second = self.upload(name="Отчёт.md", body=b"vtoroi otchet",
                             case_id="BX-2026-0423", incoming_no="BX-2026-0423").json()
        self.assertNotEqual(first["case"]["id"], second["case"]["id"])
        self.assertEqual(b"pervyi otchet",
                         self.client.get(f"/api/reports/{first['report']['id']}/file").content)
        self.assertEqual(b"vtoroi otchet",
                         self.client.get(f"/api/reports/{second['report']['id']}/file").content)

    def test_versions_of_one_letter_keep_their_own_files(self):
        first = self.upload(name="Отчёт.md", body=b"versiya odin").json()["report"]
        second = self.upload(name="Отчёт.md", body=b"versiya dva").json()["report"]
        self.assertEqual(b"versiya odin",
                         self.client.get(f"/api/reports/{first['id']}/file").content)
        self.assertEqual(b"versiya dva",
                         self.client.get(f"/api/reports/{second['id']}/file").content)

    def test_refused_upload_leaves_no_rubbish_on_disk(self):
        from pathlib import Path as _P

        self.upload(name="Отчёт.md", body=b"normalnyi").json()
        before = sorted(x.name for x in _P(self.tmp / "reports").rglob("*") if x.is_file())
        self.upload(body=b"")
        self.upload(name="", body=b"chto-to")
        after = sorted(x.name for x in _P(self.tmp / "reports").rglob("*") if x.is_file())
        self.assertEqual(before, after, "отказанная сдача оставила файл на диске")

    def test_a_report_in_an_unreadable_format_is_taken_but_flagged(self):
        """Отказать нельзя: отчёт сдают в том, в чём он написан.

        Но по нечитаемому файлу нельзя сверить ни одного числа — а сверка
        чисел и есть то, ради чего система заведена. Поэтому берём и говорим
        об этом прямо, а не отказываем и не молчим.
        """
        response = self.upload(name="дамп.pcap", body=b"\xd4\xc3\xb2\xa1")
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertTrue(body["note"], "молча приняли нечитаемый отчёт")
        self.assertEqual("", body["report"]["markdown"].strip())

    def test_incoming_number_is_required(self):
        response = self.upload(case_id="", incoming_no="")
        self.assertEqual(400, response.status_code)
        self.assertIn("входящий номер", response.json()["error"].lower())

    def test_empty_file_is_refused(self):
        response = self.upload(body=b"")
        self.assertEqual(400, response.status_code)
        self.assertIn("пустой", response.json()["error"])


class DepartmentDayTests(WebTestCase):
    """Дни, отмеченные на весь отдел: общие работы, занятия, собрание.

    Это не расход одного человека. Записать «в четверг учения» отсутствием
    значило бы завести двадцать строк на один день и сломать счёт «сколько
    людей в строю».
    """

    def test_a_marked_day_shows_up_in_the_roster(self):
        answer = self.client.post("/api/roster/days", json={
            "kind": "work", "date_from": "2026-09-10", "date_to": "2026-09-10",
            "title": "Парко-хозяйственный день"})
        self.assertEqual(200, answer.status_code, answer.text)
        grid = self.client.get("/api/roster?date_from=2026-09-07&days=7").json()
        marked = grid["department_days"]
        self.assertIn("2026-09-10", marked)
        self.assertEqual("Парко-хозяйственный день", marked["2026-09-10"][0]["title"])
        self.assertEqual("общие работы отдела", marked["2026-09-10"][0]["kind_title"])

    def test_a_several_day_mark_covers_every_day(self):
        self.client.post("/api/roster/days", json={
            "kind": "study", "date_from": "2026-09-08", "date_to": "2026-09-10",
            "title": "Учения"})
        marked = self.client.get(
            "/api/roster?date_from=2026-09-07&days=7").json()["department_days"]
        self.assertEqual(["2026-09-08", "2026-09-09", "2026-09-10"],
                         sorted(marked.keys()))

    def test_a_day_outside_the_window_is_not_shown(self):
        self.client.post("/api/roster/days", json={
            "kind": "meeting", "date_from": "2026-12-01", "date_to": "2026-12-01"})
        marked = self.client.get(
            "/api/roster?date_from=2026-09-07&days=7").json()["department_days"]
        self.assertEqual({}, marked)

    def test_the_mark_does_not_touch_the_head_count(self):
        """Расход у каждого остаётся своим: отметка стоит на дне."""
        before = self.client.get("/api/roster/day?date=2026-09-10").json()
        self.client.post("/api/roster/days", json={
            "kind": "work", "date_from": "2026-09-10", "date_to": "2026-09-10"})
        after = self.client.get("/api/roster/day?date=2026-09-10").json()
        for key in ("present", "away", "marked", "total"):
            self.assertEqual(before[key], after[key], f"сбился счёт «{key}»")

    def test_the_mark_can_be_taken_off(self):
        day = self.client.post("/api/roster/days", json={
            "kind": "holiday", "date_from": "2026-09-10",
            "date_to": "2026-09-10"}).json()["day"]
        self.assertEqual(200, self.client.delete(
            f"/api/roster/days/{day['id']}").status_code)
        marked = self.client.get(
            "/api/roster?date_from=2026-09-07&days=7").json()["department_days"]
        self.assertEqual({}, marked)
        self.assertEqual(404, self.client.delete(
            f"/api/roster/days/{day['id']}").status_code)

    def test_an_unknown_kind_is_refused_with_the_list(self):
        answer = self.client.post("/api/roster/days", json={
            "kind": "shashlyk", "date_from": "2026-09-10"})
        self.assertEqual(400, answer.status_code)
        self.assertIn("work", answer.json()["error"])

    def test_a_backwards_period_is_refused(self):
        answer = self.client.post("/api/roster/days", json={
            "kind": "work", "date_from": "2026-09-10", "date_to": "2026-09-08"})
        self.assertEqual(400, answer.status_code)

    def test_an_engineer_does_not_mark_days_for_the_department(self):
        """День касается всех: ставит его начальство."""
        self.login("engineer")
        answer = self.client.post("/api/roster/days", json={
            "kind": "work", "date_from": "2026-09-10"})
        self.assertEqual(403, answer.status_code)
        self.assertFalse(self.client.get(
            "/api/roster?date_from=2026-09-07&days=7").json()["can_mark_days"])

    def test_the_boss_is_told_they_may_mark_days(self):
        self.login("nachalnik")
        self.assertTrue(self.client.get(
            "/api/roster?date_from=2026-09-07&days=7").json()["can_mark_days"])


class SummonTests(WebTestCase):
    """Вызов в кабинет идёт сверху вниз и только так."""

    def test_the_head_of_department_cannot_be_summoned(self):
        """К начальнику отдела заходят сами, а не вызывают его к себе.

        Права администратора есть и у начальника группы — без этого правила
        он мог бы вызвать начальника отдела.
        """
        head = self.repos.users.by_login("nachalnik")
        for who in ("gruppa", "zam", "admin"):
            self.login(who)
            answer = self.client.post("/api/notifications/call",
                                      json={"user_id": head.id})
            self.assertEqual(403, answer.status_code,
                             f"«{who}» вызвал начальника отдела")
            self.assertIn("заходят", answer.json()["error"])

    def test_an_engineer_can_still_be_summoned(self):
        engineer = self.repos.users.by_login("engineer")
        self.login("nachalnik")
        answer = self.client.post("/api/notifications/call",
                                  json={"user_id": engineer.id, "place": "каб. 214"})
        self.assertEqual(200, answer.status_code, answer.text)
        self.login("engineer")
        notices = self.client.get("/api/notifications").json()["items"]
        self.assertTrue([item for item in notices if item["kind"] == "call"])

    def test_the_creator_of_the_system_is_not_summoned_either(self):
        owner = self.repos.users.by_login("admin")
        self.login("nachalnik")
        self.assertEqual(403, self.client.post(
            "/api/notifications/call", json={"user_id": owner.id}).status_code)


class StaleFactsTests(WebTestCase):
    """Отчёт, собранный по прежней редакции исходных данных."""

    def setUp(self):
        super().setUp()
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])

    def test_fresh_report_is_not_marked_stale(self):
        body = self.client.get(f"/api/reports/{self.report['id']}").json()["report"]
        self.assertFalse(body["facts_stale"])

    def test_report_built_before_the_facts_changed_says_so(self):
        """Шапку документа правка фактов не переписывает — и правильно.

        Пересборка сменила бы текст под подписью. Значит, расхождение надо
        показывать: карточка письма уже показывает новые данные, а в самом
        документе стоят прежние. Подтверждено сплошным разбором.
        """
        facts = dict(self.client.get(f"/api/cases/{self.case['id']}").json()["case"]["facts"])
        facts["equipment"] = {**facts.get("equipment", {}), "модем": "МОДЕЛЬ-Б"}
        self.assertEqual(200, self.client.put(f"/api/cases/{self.case['id']}/facts",
                                              json={"facts": facts}).status_code)
        body = self.client.get(f"/api/reports/{self.report['id']}").json()["report"]
        self.assertTrue(body["facts_stale"], "расхождение с исходными данными не видно")

    def test_a_handed_in_file_is_never_marked_stale(self):
        # У сданного файлом отчёта факт-пакета за спиной нет: сверять нечего.
        response = self.client.post(
            "/api/reports/upload",
            files={"file": ("Отчёт.md", b"# Otchet\n", "text/markdown")},
            data={"case_id": "ВХ-СВЕЖ-1", "incoming_no": "ВХ-СВЕЖ-1"},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.json()["report"]["facts_stale"])


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

    def test_invented_number_blocks_the_report_from_leaving(self):
        """Число мимо исходных данных не пускает отчёт ни на шаг дальше.

        Ни на проверку — начальнику незачем ловить руками то, что ловит
        машина, — ни в «проверен».
        """
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Запас по мощности составил 7.3 дБ."},
        )
        verify = self.client.post(f"/api/reports/{self.report['id']}/verify").json()
        self.assertGreater(verify["errors"], 0)

        sent = self.client.post(f"/api/reports/{self.report['id']}/submit")
        self.assertEqual(409, sent.status_code)
        self.assertIn("верификатор нашёл ошибок", sent.json()["error"])

        response = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(409, response.status_code)
        self.assertIn("не отправлен на проверку", response.json()["error"])

    def test_approve_collects_edit_pairs(self):
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Выводы инженера: EVM 12.4 % при ОСШ 13.7 дБ."},
        )
        self.submit(self.report["id"])
        response = self.client.post(f"/api/reports/{self.report['id']}/approve")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["report"]["status"], "approved")
        self.assertEqual(self.repos.edits.count(), 1)
        pair = self.repos.edits.list()[0]
        self.assertGreater(pair.edit_distance, 0)
        self.assertLessEqual(pair.edit_distance, 1.0)
        self.assertIn("facts", pair.context)

    def test_approved_report_is_not_marked_a_draft(self):
        """Утверждённый отчёт уходит заказчику — «ЧЕРНОВИК» в шапке недопустим.

        Строка была прибита гвоздями при сборке, и подписанный документ
        и в Markdown, и в DOCX сообщал «требует проверки и подписи
        инженера». Для организации, которая этим отвечает на входящее
        письмо, это хуже опечатки.
        """
        report_id = self.report["id"]
        self.assertIn("ЧЕРНОВИК", self.client.get(
            f"/api/reports/{report_id}/export.md").text)
        self.submit(report_id)
        self.client.post(f"/api/reports/{report_id}/approve")
        text = self.client.get(f"/api/reports/{report_id}/export.md").text
        self.assertNotIn("ЧЕРНОВИК", text)
        self.assertIn("УТВЕРЖДЁН", text)
        # В шапке видно, кто подписал: без этого «утверждён» ничего не значит.
        self.assertIn("Утвердил:", text)

    def test_report_approved_before_the_signature_existed_is_fixed_on_export(self):
        """Отчёты, подписанные до появления подписи в шапке, чинятся при выгрузке.

        Они лежат в базе с отметкой «ЧЕРНОВИК» и уходили заказчику с ней.
        Текст отчёта — величина производная, он пересобирается из секций.
        """
        report_id = self.report["id"]
        self.submit(report_id)
        self.client.post(f"/api/reports/{report_id}/approve")
        # Возвращаем в базу шапку, какой она была до исправления.
        stale = self.repos.reports.get(report_id).markdown.replace(
            "> Статус документа: **УТВЕРЖДЁН**.",
            "> Статус документа: **ЧЕРНОВИК**. Требует проверки и подписи инженера.")
        self.repos.reports.update_markdown(report_id, stale)
        self.assertIn("ЧЕРНОВИК", self.repos.reports.get(report_id).markdown)

        text = self.client.get(f"/api/reports/{report_id}/export.md").text
        self.assertNotIn("ЧЕРНОВИК", text)
        self.assertIn("УТВЕРЖДЁН", text)

    def test_draft_export_carries_the_number_of_unresolved_errors(self):
        """Черновик выгружают и печатают — на бумаге панели замечаний нет.

        Верификатор блокирует утверждение, но не выгрузку: инженеру нужно
        уметь распечатать черновик и вычитать его на бумаге. Значит, сам
        документ обязан сказать, что он не проверен и сколько в нём
        несведённых чисел.
        """
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Запас по мощности составил 7.3 дБ."},
        )
        verify = self.client.post(f"/api/reports/{self.report['id']}/verify").json()
        self.assertGreater(verify["errors"], 0)
        text = self.client.get(f"/api/reports/{self.report['id']}/export.md").text
        self.assertIn("ЧЕРНОВИК", text)
        self.assertIn(f"Верификатор нашёл ошибок: {verify['errors']}", text)

    def test_clean_draft_is_not_scared_with_a_zero(self):
        text = self.client.get(f"/api/reports/{self.report['id']}/export.md").text
        self.assertIn("ЧЕРНОВИК", text)
        self.assertNotIn("Верификатор нашёл ошибок", text)

    def test_reapproval_replaces_the_pairs_and_not_appends(self):
        """Забракованный вариант не должен остаться в обучающем наборе.

        Отчёт утверждают, правят и утверждают снова. Пары от каждого
        утверждения копились: секция попадала в набор дважды, и первым —
        тот текст, который инженер сам же и забраковал. Учить на нём как
        на «финале инженера» — ровно то, от чего предостерегает док. 03.
        """
        report_id = self.report["id"]
        section_id = self.report["sections"][5]["section_id"]
        self.client.put(f"/api/reports/{report_id}/sections/{section_id}",
                        json={"text": "Первый вариант инженера."})
        self.submit(report_id)
        self.client.post(f"/api/reports/{report_id}/approve")
        self.assertEqual(1, self.repos.edits.count())

        self.client.put(f"/api/reports/{report_id}/sections/{section_id}",
                        json={"text": "Окончательный вариант инженера."})
        self.submit(report_id)
        self.client.post(f"/api/reports/{report_id}/approve")
        pairs = self.repos.edits.list()
        self.assertEqual(1, len(pairs), "пары от прошлого утверждения остались")
        self.assertEqual("Окончательный вариант инженера.", pairs[0].final)

    def test_approve_is_idempotent(self):
        self.submit(self.report["id"])
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

    def test_any_edit_of_a_signed_report_removes_the_signature(self):
        """Подпись стоит под тем текстом, который человек прочитал.

        Раньше её снимал только верификатор — если правка добавляла число
        мимо факт-пакета. Правка, ошибок не добавившая, оставляла отчёт
        утверждённым: в шапке «Утвердил: Иванов», а раздел переписан после
        него и, возможно, не им. Утвердить заново — одно нажатие, а вот
        отличить подписанный документ от подправленного после подписи было
        нельзя ничем.
        """
        self.submit(self.report["id"])
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        section_id = self.report["sections"][0]["section_id"]
        response = self.client.put(
            f"/api/reports/{self.report['id']}/sections/{section_id}",
            json={"text": "Короткий текст без единого числа."},
        )
        self.assertEqual("draft", response.json()["report"]["status"])
        self.assertEqual(0, response.json()["report"]["errors"])
        # И в выгрузке документ снова черновик, а не подписанный отчёт.
        text = self.client.get(f"/api/reports/{self.report['id']}/export.md").text
        self.assertIn("ЧЕРНОВИК", text)
        self.assertNotIn("УТВЕРЖДЁН", text)

    def test_returning_the_model_draft_also_removes_the_signature(self):
        section_id = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.report['id']}/sections/{section_id}",
                        json={"text": "Правка инженера без чисел."})
        self.submit(self.report["id"])
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        response = self.client.post(
            f"/api/reports/{self.report['id']}/sections/{section_id}/restore")
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("draft", self.repos.reports.get(self.report["id"]).status)

    def test_warning_from_changed_facts_does_not_revoke_approval(self):
        # Замечание-предупреждение подписи не снимает: снимает её ошибка,
        # то есть число в тексте мимо факт-пакета.
        self.submit(self.report["id"])
        self.client.post(f"/api/reports/{self.report['id']}/approve")
        case = self.client.get(f"/api/cases/{self.case['id']}").json()["case"]
        facts = dict(case["facts"])
        facts["request"] = "уточнение к обращению без единого числа"
        response = self.client.put(f"/api/cases/{self.case['id']}/facts",
                                   json={"facts": facts})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("approved", self.repos.reports.get(self.report["id"]).status)

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


class FakeIndexer:
    """Строитель векторов, которого считают по вызовам."""

    def __init__(self, state=None):
        self.state = dict(state or {"running": False, "missing": 0, "hint": ""})
        self.started = 0
        self.stopped = 0

    def start_if_needed(self):
        self.started += 1
        return dict(self.state)

    def stop(self, timeout=5.0):
        self.stopped += 1

    def running(self):
        return bool(self.state.get("running"))

    def status(self, *, fresh=False):
        return dict(self.state)


class VectorLifecycleTests(unittest.TestCase):
    """Пуск и остановка приложения: векторы достраиваются и не бросаются."""

    def build(self, indexer, **changes):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings.load(
            data_dir=str(tmp.name), db_path=":memory:", auth_enabled=False,
            templates_dir=str(ROOT / "templates"),
            glossary_path=str(ROOT / "templates" / "glossary.json"),
            **changes,
        )
        repos = Repositories(Database(":memory:"))
        service = ReportService(repos=repos, settings=settings, llm=StubLLM(),
                                vectors=indexer)
        return create_app(settings, repos, service), service

    def test_an_unfinished_build_is_resumed_at_startup(self):
        """Приложение перезапустили посреди работы.

        Половина библиотеки ищется только словами, и узнать об этом можно
        было, лишь открыв «Библиотеку» и нажав кнопку рукой.
        """
        indexer = FakeIndexer({"running": True, "missing": 40,
                               "hint": "строятся векторы"})
        app, _ = self.build(indexer)
        with TestClient(app):
            pass
        self.assertEqual(1, indexer.started)

    def test_the_build_is_stopped_when_the_application_shuts_down(self):
        # Брошенный на полуслове поток дописывает в базу, которую уже
        # закрывают: в журнале это «database is locked» на выходе.
        indexer = FakeIndexer()
        app, _ = self.build(indexer)
        with TestClient(app):
            pass
        self.assertEqual(1, indexer.stopped)

    def test_a_half_disabled_search_is_complained_about_at_startup(self):
        """Реранкер и эмбеддинги на одном адресе — реранк молча пропадает.

        Система при этом не падает и ничем себя не выдаёт, а на
        изолированной машине спросить некого.
        """
        app, _ = self.build(FakeIndexer(), embed_enabled=True, rerank_enabled=True,
                            embed_base_url="http://127.0.0.1:8001/v1",
                            rerank_base_url="http://127.0.0.1:8001/v1")
        with self.assertLogs("reportgen.web", level="WARNING") as logs:
            with TestClient(app):
                pass
        self.assertIn("rerank_base_url", "\n".join(logs.output))

    def test_the_search_knows_when_vectors_are_being_built(self):
        """Поисковику нужен признак постройки, иначе он перечитывает всё.

        Число векторов меняется после каждой пачки, а разбор вопроса делает
        несколько поисков подряд — библиотека распаковывалась бы из BLOB
        заново на каждый заход.
        """
        indexer = FakeIndexer({"running": True, "missing": 5, "hint": ""})
        _, service = self.build(indexer)
        retriever = service.get_retriever()
        self.assertIsNotNone(retriever)
        self.assertTrue(retriever.vectors_building())
        indexer.state["running"] = False
        self.assertFalse(retriever.vectors_building())


class VectorStatusTests(WebTestCase):
    """Состояние смыслового поиска видно из приложения, а не из консоли."""

    def test_the_state_is_readable_by_anyone_who_logged_in(self):
        # Смотрит на библиотеку весь отдел, а не только начальник: инженер
        # должен понимать, почему его вопрос ничего не нашёл.
        self.login("engineer")
        response = self.client.get("/api/library/vectors")
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()["vectors"]
        self.assertIn("chunks", body)
        self.assertIn("missing", body)
        self.assertIn("hint", body)

    def test_a_switched_off_search_says_so_and_refuses_to_build(self):
        # Свежая установка без сервера эмбеддингов: поиск словами работает,
        # но об этом надо сказать, а не молчать.
        state = self.client.get("/api/library/vectors").json()["vectors"]
        self.assertFalse(state["enabled"])
        self.assertIn("выключен", state["hint"])
        response = self.client.post("/api/library/vectors", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("embed_enabled", response.json()["error"])

    def test_uploading_a_document_reports_the_state_of_the_search(self):
        """Положили книгу — сразу видно, попала ли она в смысловой поиск.

        Векторы строила одна команда из консоли, к которой на изолированной
        машине никто не подходит: книга ложилась в библиотеку и молча
        оставалась невидимой для поиска по смыслу.
        """
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("книга.md",
                            "# Том\n\n".encode() + "Радиорелейная линия. ".encode() * 40,
                            "text/markdown")},
            data={"doc_type": "literature"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("vectors", response.json())

    def test_the_state_counts_chunks_not_rows_of_the_table(self):
        state = self.client.get("/api/library/vectors").json()["vectors"]
        self.assertGreater(state["chunks"], 0)
        self.assertEqual(state["chunks"] - state["vectors"], state["missing"])


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


class LetterCardTests(WebTestCase):
    """Письмо: входящий номер, срок, исполнитель, состояние."""

    def engineer_id(self):
        return self.repos.users.by_login("engineer").id

    def test_registration_takes_letter_fields(self):
        payload = dict(CASE)
        response = self.client.post("/api/cases", json={
            "report_type": payload["report_type"], "case_id": payload["case_id"],
            "facts": payload, "title": "Помеха в стволе",
            "incoming_no": "ВХ-2026-0412", "incoming_date": "2026-08-20",
            "deadline": "2026-09-01", "priority": "urgent",
            "assignee_id": self.engineer_id(),
        })
        self.assertEqual(200, response.status_code, response.text)
        case = response.json()["case"]
        self.assertEqual("ВХ-2026-0412", case["incoming_no"])
        self.assertEqual("2026-09-01", case["deadline"])
        self.assertEqual("urgent", case["priority"])
        self.assertEqual(self.engineer_id(), case["assignee_id"])

    def test_card_is_editable(self):
        case = self.create_case()
        response = self.client.patch(f"/api/cases/{case['id']}", json={
            "deadline": "2026-09-15", "assignee_id": self.engineer_id(),
            "priority": "high", "status": "review", "note": "запросили уточнение",
        })
        self.assertEqual(200, response.status_code, response.text)
        fresh = response.json()["case"]
        self.assertEqual("2026-09-15", fresh["deadline"])
        self.assertEqual("review", fresh["status"])
        self.assertEqual("Инженеров И. И.", fresh["assignee_name"])

    def test_bad_date_is_refused_with_a_readable_reason(self):
        # Инженер должен прочитать, в каком виде нужна дата, а не «422».
        case = self.create_case()
        for bad, expected in (("вчера", "ГГГГ-ММ-ДД"),
                              ("01.09.2026", "ГГГГ-ММ-ДД"),
                              ("2026-13-40", "не существует")):
            with self.subTest(value=bad):
                response = self.client.patch(f"/api/cases/{case['id']}",
                                             json={"deadline": bad})
                self.assertEqual(400, response.status_code)
                self.assertIn(expected, response.json()["error"])
                self.assertIn("deadline", response.json()["error"])

    def test_empty_deadline_clears_it(self):
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={"deadline": "2026-09-01"})
        fresh = self.client.patch(f"/api/cases/{case['id']}",
                                  json={"deadline": ""}).json()["case"]
        self.assertEqual("", fresh["deadline"])

    def test_any_employee_can_pick_the_assignee_list(self):
        # Взять письмо на себя вправе любой инженер, а раздел военнослужащих
        # ему закрыт — поэтому список исполнителей отдаётся отдельно.
        self.login("engineer")
        self.assertEqual(403, self.client.get("/api/users").status_code)
        response = self.client.get("/api/staff")
        self.assertEqual(200, response.status_code)
        logins = {item["login"] for item in response.json()["items"]}
        self.assertIn("nachalnik", logins)
        self.assertNotIn("local", logins, "служебной записи в списке быть не должно")
        for item in response.json()["items"]:
            self.assertNotIn("password_hash", item)

    def test_disabled_employee_is_not_offered(self):
        victim = self.repos.users.by_login("engineer")
        self.repos.users.set_active(victim.id, False)
        logins = {item["login"] for item in self.client.get("/api/staff").json()["items"]}
        self.assertNotIn("engineer", logins)

    def test_group_number_accepts_anything_written_by_hand(self):
        """Формат номера группы задаёт делопроизводство отдела, не программа.

        Пишут и цифрами, и словами: «1274», «12/345», «в/ч 74326»,
        «группа связи». Строгая проверка на число отвергала половину из
        этого и заставляла инженера подгонять запись под программу.
        """
        case = self.create_case()
        for written in ("1274", "12/345", "1274-3", "в/ч 74326",
                        "группа связи", "ПАО «Ростелеком»"):
            with self.subTest(written=written):
                response = self.client.patch(f"/api/cases/{case['id']}",
                                             json={"group_no": written})
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(written, response.json()["case"]["group_no"])

    def test_group_number_refuses_a_whole_paragraph(self):
        # Предел один — длина: в поле не должен уехать абзац.
        case = self.create_case()
        response = self.client.patch(f"/api/cases/{case['id']}",
                                     json={"group_no": "я" * 500})
        self.assertEqual(400, response.status_code)
        self.assertIn("номер группы", response.json()["error"].lower())

    def test_group_number_given_at_registration_reaches_the_report(self):
        """Номер из формы регистрации доходит до шапки отчёта."""
        payload = dict(CASE)
        response = self.client.post("/api/cases", json={
            "report_type": payload["report_type"], "case_id": "SUP-ФОРМА-1",
            "facts": {**payload, "case_id": "SUP-ФОРМА-1", "group_no": ""},
            "group_no": "1-я группа",
        })
        self.assertEqual(200, response.status_code, response.text)
        case = response.json()["case"]
        self.assertEqual("1-я группа", case["group_no"])
        self.assertEqual("1-я группа", case["facts"]["group_no"])

        report = self.client.post(f"/api/cases/{case['id']}/generate").json()["report"]
        text = self.client.get(f"/api/reports/{report['id']}/export.md").text
        self.assertIn("**Номер группы:** 1-я группа", text)
        self.assertNotIn("Заказчик", text)
        self.assertNotIn("Отправитель", text)

    def test_group_number_in_the_fact_pack_wins_over_the_form(self):
        payload = dict(CASE)
        case = self.client.post("/api/cases", json={
            "report_type": payload["report_type"], "case_id": "SUP-ФОРМА-2",
            "facts": {**payload, "case_id": "SUP-ФОРМА-2", "group_no": "1274"},
            "group_no": "5150",
        }).json()["case"]
        self.assertEqual("1274", case["group_no"])
        self.assertEqual("1274", case["facts"]["group_no"])

    def test_old_fact_pack_with_the_previous_key_still_reads(self):
        # Факт-пакеты принятых обращений лежат в базе с ключом customer.
        payload = dict(CASE)
        payload.pop("group_no", None)
        case = self.client.post("/api/cases", json={
            "report_type": payload["report_type"], "case_id": "SUP-СТАРЫЙ-1",
            "facts": {**payload, "case_id": "SUP-СТАРЫЙ-1", "customer": "1274"},
        }).json()["case"]
        self.assertEqual("1274", case["group_no"])

    def test_search_treats_percent_as_a_sign_not_a_wildcard(self):
        """«100%» — это то, что ввёл инженер, а не «найди всё подряд»."""
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={"title": "Запас 100% мощности"})
        other = self.create_case({**CASE, "case_id": "SUP-ДРУГОЕ-1"})
        self.client.patch(f"/api/cases/{other['id']}", json={"title": "Совсем про другое"})

        found = self.client.get("/api/cases", params={"q": "100%"}).json()
        titles = {item["title"] for item in found["items"]}
        self.assertIn("Запас 100% мощности", titles)
        self.assertNotIn("Совсем про другое", titles, "% сработал как подстановка")

        # И одиночное подчёркивание не заменяет любой знак.
        self.assertEqual(0, self.client.get(
            "/api/cases", params={"q": "10_%"}).json()["total"])

    def test_card_lines_have_a_limit_and_say_so(self):
        """Тема в пять тысяч знаков ломает и список, и шапку документа.

        Молча обрезать нельзя: человек не узнает, что часть текста
        потерялась. Говорим, какое поле и какой у него предел.
        """
        case = self.create_case()
        for field, limit, word in (("title", 300, "тема письма"),
                                   ("incoming_no", 60, "входящий номер"),
                                   ("note", 2000, "примечание")):
            with self.subTest(field=field):
                refused = self.client.patch(f"/api/cases/{case['id']}",
                                            json={field: "т" * (limit + 1)})
                self.assertEqual(400, refused.status_code)
                self.assertIn(word, refused.json()["error"])
                self.assertIn(str(limit), refused.json()["error"])
                ok = self.client.patch(f"/api/cases/{case['id']}",
                                       json={field: "т" * limit})
                self.assertEqual(200, ok.status_code, ok.text)

    def test_card_lines_drop_control_characters(self):
        """Нулевой байт приезжает вставкой из Word и рвёт выгрузку и поиск.

        Тема и входящий номер — строки журнала: перевод строки в них
        сминается в пробел. Примечание — заметка на полях, там абзацы
        уместны и сохраняются.
        """
        case = self.create_case()
        fresh = self.client.patch(f"/api/cases/{case['id']}", json={
            "title": "Помеха\x00 в\nстволе", "incoming_no": "ВХ\t-1",
            "note": "первая строка\nвторая строка"}).json()["case"]
        self.assertEqual("Помеха в стволе", fresh["title"])
        self.assertEqual("ВХ -1", fresh["incoming_no"])
        self.assertEqual("первая строка\nвторая строка", fresh["note"])

    def test_registering_the_same_letter_twice_at_once_is_refused_not_crashed(self):
        """Двойное нажатие на «Зарегистрировать письмо» — обычное дело.

        Проверка «такое письмо уже есть» стоит до вставки: гонка
        проскакивала мимо неё, и второй получал срыв сервера вместо
        понятного отказа.
        """
        import threading

        answers = []

        def register():
            answers.append(self.client.post("/api/cases", json={
                "report_type": CASE["report_type"], "case_id": "SUP-РАЗОМ-1",
                "facts": {**CASE, "case_id": "SUP-РАЗОМ-1"}}))

        threads = [threading.Thread(target=register) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        codes = sorted(answer.status_code for answer in answers)
        self.assertEqual([200, 409, 409, 409, 409], codes,
                         [a.text[:100] for a in answers])
        for answer in answers:
            if answer.status_code == 409:
                self.assertIn("уже зарегистрировано", answer.json()["error"])
        self.assertEqual(1, self.repos.cases.count(query="SUP-РАЗОМ-1"))

    def test_registration_holds_the_same_limits(self):
        # Предел поля не должен зависеть от того, каким действием письмо
        # завели: карточкой или регистрацией.
        refused = self.client.post("/api/cases", json={
            "report_type": CASE["report_type"], "case_id": "SUP-ДЛИННЫЙ-1",
            "title": "т" * 301, "facts": {**CASE, "case_id": "SUP-ДЛИННЫЙ-1"}})
        self.assertEqual(400, refused.status_code)
        self.assertIn("тема письма", refused.json()["error"])

    def test_flow_states_are_not_set_by_hand_when_a_report_exists(self):
        """«На проверке» и «отправлено» письму даёт отчёт, а не карточка.

        Инженер ставил письму «отправлено» прямо в карточке: письмо уходило
        из работы, начальник его больше не видел, а отчёт никто не проверял.
        """
        case = self.create_case()
        self.generate(case["id"])
        self.login("engineer")
        for status in ("review", "checked"):
            with self.subTest(status=status):
                refused = self.client.patch(f"/api/cases/{case['id']}",
                                            json={"status": status})
                self.assertEqual(409, refused.status_code, refused.text)
                self.assertIn("проверка отчёта", refused.json()["error"])
        # «Отправлено» — тем более: его даёт запись исходящего номера.
        refused = self.client.patch(f"/api/cases/{case['id']}",
                                    json={"status": "approved"})
        self.assertEqual(409, refused.status_code)
        self.assertIn("исходящего номера", refused.json()["error"])
        # Остальные состояния карточка ставит как ставила.
        for status in ("new", "draft", "archived"):
            with self.subTest(status=status):
                response = self.client.patch(f"/api/cases/{case['id']}",
                                             json={"status": status})
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(status, response.json()["case"]["status"])

    def test_sent_is_never_set_by_hand_even_without_a_report(self):
        """«Отправлено» всегда значит «есть исходящий номер».

        Иначе в журнале отдела письмо закрыто, а чем ответили —
        неизвестно. Ответ мимо системы записывается тем же действием
        «Ответ отправлен», просто без отчёта.
        """
        case = self.create_case()
        refused = self.client.patch(f"/api/cases/{case['id']}", json={"status": "approved"})
        self.assertEqual(409, refused.status_code)
        self.assertIn("исходящего номера", refused.json()["error"])
        # А «принято», «в работе» и «в архиве» по-прежнему ставятся руками.
        for status in ("new", "draft", "archived"):
            with self.subTest(status=status):
                ok = self.client.patch(f"/api/cases/{case['id']}", json={"status": status})
                self.assertEqual(200, ok.status_code, ok.text)

    def test_letter_says_how_many_reports_it_has(self):
        # Число нужно карточке: по нему она запирает состояния «на проверке»
        # и «отправлено», которые даёт проверка отчёта.
        case = self.create_case()
        self.assertEqual(0, self.client.get(
            f"/api/cases/{case['id']}").json()["case"]["reports_count"])
        self.generate(case["id"])
        self.generate(case["id"])
        self.assertEqual(2, self.client.get(
            f"/api/cases/{case['id']}").json()["case"]["reports_count"])
        listed = self.client.get("/api/cases").json()["items"]
        self.assertEqual(2, [x for x in listed if x["id"] == case["id"]][0]["reports_count"])

    def test_counter_counts_what_the_filter_shows_not_just_the_search(self):
        """«Показаны 2 из 5» на вкладке «Просроченные».

        Условия отбора были написаны дважды — для списка и для счётчика — и
        разошлись: список знал про срок, счётчик считал по всем письмам.
        """
        for number in range(1, 6):
            case = self.create_case({**CASE, "case_id": f"SUP-СРОК-{number}"})
            self.client.patch(f"/api/cases/{case['id']}", json={
                "deadline": "2000-01-01" if number < 3 else "2030-01-01"})
        body = self.client.get("/api/cases", params={"overdue": "true"}).json()
        self.assertEqual(len(body["items"]), body["total"],
                         "счётчик считает не то, что показано")
        self.assertEqual(2, body["total"])

    def test_numbers_and_assignees_may_repeat(self):
        """Входящие номера и исполнители в отделе повторяются.

        Так заведено в делопроизводстве: одним входящим номером приходит
        несколько обращений, одним исходящим отвечают на несколько писем, а
        один инженер ведёт их десятками. Система в это не вмешивается — её
        дело учесть, а не спорить с журналом отдела.
        """
        engineer = self.engineer_id()
        letters = []
        for name in ("SUP-ПОВТОР-1", "SUP-ПОВТОР-2"):
            case = self.create_case({**CASE, "case_id": name})
            fresh = self.client.patch(f"/api/cases/{case['id']}", json={
                "incoming_no": "ВХ-2026-0900", "assignee_id": engineer})
            self.assertEqual(200, fresh.status_code, fresh.text)
            letters.append(fresh.json()["case"])

        self.assertEqual(["ВХ-2026-0900", "ВХ-2026-0900"],
                         [item["incoming_no"] for item in letters])
        self.assertEqual([engineer, engineer],
                         [item["assignee_id"] for item in letters])

        # И регистрация с тем же входящим номером проходит.
        third = self.client.post("/api/cases", json={
            "report_type": CASE["report_type"], "case_id": "SUP-ПОВТОР-3",
            "incoming_no": "ВХ-2026-0900",
            "facts": {**CASE, "case_id": "SUP-ПОВТОР-3"}})
        self.assertEqual(200, third.status_code, third.text)

        # По номеру находятся все три — на то он и общий.
        found = self.client.get("/api/cases", params={"q": "ВХ-2026-0900"}).json()
        self.assertEqual(3, found["total"])

    def test_counter_counts_what_the_search_shows(self):
        # «Показаны 1 из 12» при поиске врало: список считал одно, число другое.
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={"title": "Особая тема"})
        self.create_case({**CASE, "case_id": "SUP-ПРОЧЕЕ-1"})
        body = self.client.get("/api/cases", params={"q": "Особая"}).json()
        self.assertEqual(len(body["items"]), body["total"])

    def test_group_number_extra_spaces_are_trimmed(self):
        case = self.create_case()
        fresh = self.client.patch(f"/api/cases/{case['id']}",
                                  json={"group_no": "  12   345  "}).json()["case"]
        self.assertEqual("12 345", fresh["group_no"])

    def test_group_number_stays_in_step_with_the_fact_pack(self):
        # Номер хранится и колонкой письма, и полем факт-пакета — оттуда он
        # попадает в отчёт. Правка в карточке жила до первого сохранения
        # фактов, а потом молча возвращалась к прежнему значению.
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={"group_no": "4210"})
        body = self.client.get(f"/api/cases/{case['id']}").json()["case"]
        self.assertEqual("4210", body["group_no"])
        self.assertEqual("4210", body["facts"]["group_no"])

        facts = dict(body["facts"])
        facts["request"] = "уточнение к обращению"
        self.client.put(f"/api/cases/{case['id']}/facts", json={"facts": facts})
        again = self.client.get(f"/api/cases/{case['id']}").json()["case"]
        self.assertEqual("4210", again["group_no"])


    def test_unknown_assignee_is_refused(self):
        case = self.create_case()
        response = self.client.patch(f"/api/cases/{case['id']}", json={"assignee_id": 9999})
        self.assertEqual(400, response.status_code)

    def test_disabled_employee_cannot_be_assigned(self):
        # Отключённому военнослужащему письмо не поручают: он не войдёт в систему.
        case = self.create_case()
        victim = self.repos.users.by_login("engineer")
        self.repos.users.set_active(victim.id, False)
        response = self.client.patch(f"/api/cases/{case['id']}",
                                     json={"assignee_id": victim.id})
        self.assertEqual(400, response.status_code)

    def test_open_and_overdue_filters(self):
        early = self.create_case()
        self.client.patch(f"/api/cases/{early['id']}", json={"deadline": "2000-01-01"})
        body = self.client.get("/api/cases", params={"overdue": "true"}).json()
        self.assertEqual([early["case_id"]], [item["case_id"] for item in body["items"]])
        self.assertEqual(1, body["overdue"])
        # Проверенное, но не отправленное — всё ещё просрочка: ответ не ушёл.
        self.repos.cases.set_status(early["id"], "checked")
        body = self.client.get("/api/cases", params={"overdue": "true"}).json()
        self.assertEqual([early["case_id"]], [item["case_id"] for item in body["items"]],
                         "проверенный, но не отправленный ответ снял просрочку")
        self.assertEqual(1, body["overdue"])

        # А отправленное письмо просроченным не считается.
        self.client.post(f"/api/cases/{early['id']}/send",
                         json={"outgoing_no": "ИСХ-2026-0001"})
        body = self.client.get("/api/cases", params={"overdue": "true"}).json()
        self.assertEqual([], body["items"])
        self.assertEqual(0, body["overdue"])

    def test_search_is_case_insensitive_for_russian(self):
        # Встроенный lower() в SQLite знает только латиницу: без своей
        # функции поиск по русскому названию зависел от регистра.
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={"title": "Помеха в стволе"})
        for query in ("помеха", "ПОМЕХА", "Помеха", "стволе"):
            with self.subTest(query=query):
                body = self.client.get("/api/cases", params={"q": query}).json()
                self.assertEqual(1, len(body["items"]), f"не нашлось по «{query}»")

    def test_overdue_first_in_the_list(self):
        # Отделу нужны горящие письма наверху, а не те, которых недавно
        # коснулись: сортировка по дате правки показывала обратное.
        far = self.create_case()
        near = self.create_case({**CASE, "case_id": "SUP-2"})
        self.client.patch(f"/api/cases/{far['id']}", json={"deadline": "2030-01-01"})
        self.client.patch(f"/api/cases/{near['id']}", json={"deadline": "2000-01-01"})
        items = self.client.get("/api/cases").json()["items"]
        self.assertEqual(near["case_id"], items[0]["case_id"])


def _today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def _shift_days(day: str, delta: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


class BoardTests(WebTestCase):
    """Дашборд: нагрузка, сроки, дежурство, движение за период."""

    def setUp(self):
        super().setUp()
        self.engineer = self.repos.users.by_login("engineer")

    def test_board_counts_open_overdue_and_unassigned(self):
        mine = self.create_case()
        self.client.patch(f"/api/cases/{mine['id']}", json={
            "deadline": "2000-01-01", "assignee_id": self.engineer.id})
        self.create_case({**CASE, "case_id": "SUP-2"})      # без исполнителя

        body = self.client.get("/api/board").json()
        self.assertEqual(2, body["totals"]["open"])
        self.assertEqual(1, body["totals"]["overdue"])
        self.assertEqual(1, body["totals"]["unassigned"])
        person = [item for item in body["people"] if item["id"] == self.engineer.id][0]
        self.assertEqual(1, person["open"])
        self.assertEqual(1, person["late"])
        self.assertEqual("2000-01-01", person["next_deadline"])

    def test_letters_of_a_disabled_employee_do_not_vanish(self):
        """Письма отключённого военнослужащего исчезали отовсюду.

        Из нагрузки — вместе с человеком, а в «без исполнителя» они не
        попадали, потому что исполнитель у них есть. Отдел не видел их
        никак, хотя отвечать по ним всё равно кому-то придётся.
        """
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}",
                          json={"assignee_id": self.engineer.id})
        self.repos.users.set_active(self.engineer.id, False)

        body = self.client.get("/api/board").json()
        row = [p for p in body["people"] if p["id"] == self.engineer.id]
        self.assertTrue(row, "отключённый исполнитель пропал из нагрузки")
        self.assertFalse(row[0]["active"], "в списке не видно, что он отключён")
        self.assertEqual(1, row[0]["open"])
        # Сумма по людям и «без исполнителя» покрывают все письма в работе.
        by_people = sum(p["open"] for p in body["people"])
        self.assertEqual(body["totals"]["open"],
                         by_people + body["totals"]["unassigned"])

    def test_absence_of_a_disabled_employee_is_not_counted(self):
        """Отпуск уволенного длится в базе до своей даты.

        Он считался отсутствием, и отдел вечно недосчитывался человека,
        которого в нём давно нет. В строю числятся действующие.
        """
        today = self.client.get("/api/board").json()["today"]
        self.client.post("/api/absences", json={
            "user_id": self.engineer.id, "kind": "vacation",
            "date_from": today, "date_to": _shift_days(today, 30)})
        self.assertEqual(1, self.client.get("/api/board").json()["totals"]["away"])

        self.repos.users.set_active(self.engineer.id, False)
        body = self.client.get("/api/board").json()
        self.assertEqual(0, body["totals"]["away"])
        self.assertEqual(0, len(body["absent"]))
        self.assertNotIn(self.engineer.id, {p["id"] for p in body["people"]})

    def test_roster_counts_only_active_people(self):
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}",
                          json={"assignee_id": self.engineer.id})
        before = self.client.get("/api/board").json()["totals"]["staff"]
        self.repos.users.set_active(self.engineer.id, False)
        body = self.client.get("/api/board").json()
        # В списке нагрузки он остался — за ним письмо. В строю — нет.
        self.assertIn(self.engineer.id, {p["id"] for p in body["people"]})
        self.assertEqual(before - 1, body["totals"]["staff"])

    def test_disabled_employee_without_letters_is_not_listed(self):
        self.repos.users.set_active(self.engineer.id, False)
        body = self.client.get("/api/board").json()
        self.assertNotIn(self.engineer.id, {p["id"] for p in body["people"]})

    def test_editing_an_old_letter_does_not_inflate_sent_this_period(self):
        """«Ответов отправлено» считается по времени утверждения отчёта.

        По времени последней правки выходила неправда: поправил примечание
        в письме прошлого года — и оно попадало в отправленные за текущий
        месяц, задирая отчётность отдела.
        """
        case = self.create_case()
        report = self.generate(case["id"])
        self.submit(report["id"])
        self.client.post(f"/api/reports/{report['id']}/approve")
        # Утверждаем «в прошлом году» — переносим отметку подписи назад.
        self.repos.db.execute(
            "UPDATE reports SET approved_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (report["id"],))
        self.assertEqual(0, self.client.get("/api/board?days=30").json()["movement"]["sent"])

        # Правка карточки старого письма в счётчик текущего периода не идёт.
        self.client.patch(f"/api/cases/{case['id']}", json={"note": "уточнение"})
        self.assertEqual(0, self.client.get("/api/board?days=30").json()["movement"]["sent"])

    def test_service_record_is_not_counted_as_staff(self):
        logins = {item["login"] for item in self.client.get("/api/board").json()["people"]}
        self.assertNotIn("local", logins)

    def test_duty_and_absence_show_up(self):
        today = self.client.get("/api/board").json()["today"]
        self.assertEqual(200, self.client.post("/api/absences", json={
            "user_id": self.engineer.id, "kind": "duty",
            "date_from": today, "date_to": today}).status_code)
        body = self.client.get("/api/board").json()
        self.assertEqual(1, body["totals"]["on_duty"])
        person = [item for item in body["people"] if item["id"] == self.engineer.id][0]
        self.assertTrue(person["on_duty"])
        self.assertEqual("", person["away"], "дежурство — это не отсутствие")

    def test_burning_letters_are_listed_even_behind_many_overdue(self):
        """Список «горящих» не должен вытесняться просроченными.

        Он собирался из первых 60 писем с фильтром на стороне кода, а
        сортировка ставит наверх просроченные: на отделе с полусотней
        просрочек список выходил пустым при ненулевой плитке над ним.
        """
        today = self.client.get("/api/board").json()["today"]
        soon = _shift_days(today, 2)
        long_ago = _shift_days(today, -400)
        for number in range(70):
            self.repos.db.execute(
                "INSERT INTO cases(case_id, report_type, title, customer, status, "
                "facts_json, facts_digest, deadline, created_at, updated_at) "
                "VALUES(?,?,'','','draft','{}','',?,?,?)",
                (f"L-ПРОСРОК-{number}", "signal_issue", long_ago, today, today),
            )
        self.repos.db.execute(
            "INSERT INTO cases(case_id, report_type, title, customer, status, "
            "facts_json, facts_digest, deadline, created_at, updated_at) "
            "VALUES(?,?,'Горящее','','draft','{}','',?,?,?)",
            ("L-ГОРИТ-1", "signal_issue", soon, today, today),
        )
        body = self.client.get("/api/board").json()
        self.assertGreaterEqual(body["totals"]["soon"], 1)
        self.assertTrue(body["soon"], "плитка считает, а список пуст")
        self.assertIn("L-ГОРИТ-1", {case["case_id"] for case in body["soon"]})

    def test_two_duty_records_for_one_person_count_once(self):
        # Дежурство и подмена на те же сутки: записей две, человек один.
        # Плитка показывала «на дежурстве: 2» при одной фамилии в списке.
        today = self.client.get("/api/board").json()["today"]
        for note in ("суточное", "подмена"):
            self.client.post("/api/absences", json={
                "user_id": self.engineer.id, "kind": "duty",
                "date_from": today, "date_to": today, "note": note})
        body = self.client.get("/api/board").json()
        self.assertEqual(1, body["totals"]["on_duty"])
        self.assertEqual(1, len(body["duty"]))

    def test_vacation_marks_person_as_away(self):
        today = self.client.get("/api/board").json()["today"]
        added = self.client.post("/api/absences", json={
            "user_id": self.engineer.id, "kind": "vacation",
            "date_from": today, "date_to": _shift_days(today, 30)})
        self.assertEqual(200, added.status_code, added.text)
        body = self.client.get("/api/board").json()
        self.assertEqual(1, body["totals"]["away"])
        person = [item for item in body["people"] if item["id"] == self.engineer.id][0]
        self.assertEqual("vacation", person["away"])
        self.assertEqual("отпуск", person["away_title"])

    def test_two_absences_for_one_person_count_once(self):
        """У военнослужащего на одни сутки две отметки: больничный и отпуск.

        Через расход такое больше не заводится — он не даёт положить вторую
        отметку на занятые дни. Но в базе отдела такие пары остались от
        прежней версии, и счётчик обязан считать людей, а не записи: иначе
        «отсутствуют: 2» при одной фамилии в списке.
        """
        today = self.client.get("/api/board").json()["today"]
        for kind, until in (("sick", today), ("vacation", _shift_days(today, 20))):
            self.repos.absences.add(self.engineer.id, kind, today, until)
        body = self.client.get("/api/board").json()
        self.assertEqual(1, body["totals"]["away"])
        self.assertEqual(1, len(body["absent"]))
        # Показываем ту запись, что кончается позже: она и есть текущая.
        self.assertEqual("vacation", body["absent"][0]["kind"])

    def test_everyone_marks_themselves_but_not_the_others(self):
        """Расход тем и жив, что человек отмечает себя сам.

        Собранный через начальника расход устаревает за день, и им никто не
        пользуется. Поэтому свою отметку ставит каждый — а чужую по-прежнему
        только начальник.
        """
        today = self.client.get("/api/board").json()["today"]
        self.login("engineer")
        mine = self.client.post("/api/absences", json={
            "kind": "duty", "date_from": today, "date_to": today,
            "place": "Узел 3"})
        self.assertEqual(200, mine.status_code, mine.text)
        self.assertEqual("Узел 3", mine.json()["absence"]["place"])

        others = self.client.post("/api/absences", json={
            "user_id": self.repos.users.by_login("nachalnik").id, "kind": "duty",
            "date_from": _shift_days(today, 1), "date_to": _shift_days(today, 1)})
        self.assertEqual(403, others.status_code)
        self.assertIn("чужой расход", others.json()["error"])

    def test_reversed_dates_are_refused(self):
        response = self.client.post("/api/absences", json={
            "user_id": self.engineer.id, "kind": "vacation",
            "date_from": "2026-09-10", "date_to": "2026-09-01"})
        self.assertEqual(400, response.status_code)

    def test_unknown_kind_is_refused(self):
        response = self.client.post("/api/absences", json={
            "user_id": self.engineer.id, "kind": "прогул",
            "date_from": "2026-09-01", "date_to": "2026-09-02"})
        self.assertEqual(400, response.status_code)

    def test_counts_do_not_stop_at_the_page_limit(self):
        # Счётчик просрочек раньше мерил длину выборки в 500 писем: на
        # пятьсот первом он замирал и показывал неправду.
        for index in range(12):
            case = self.create_case({**CASE, "case_id": f"SUP-{index}"})
            self.repos.cases.update_card(case["id"], deadline="2000-01-01")
        body = self.client.get("/api/board").json()
        self.assertEqual(12, body["totals"]["overdue"])
        self.assertLessEqual(len(body["overdue"]), 20, "список для показа должен быть коротким")

    def test_movement_counts_the_period(self):
        self.create_case()
        body = self.client.get("/api/board", params={"days": 30}).json()
        self.assertEqual(1, body["movement"]["came"])
        self.assertIn("since", body["movement"])


class RosterTests(WebTestCase):
    """Расход личного состава: кто где и чем занят."""

    def setUp(self):
        super().setUp()
        self.engineer = self.repos.users.by_login("engineer")
        self.today = _today_iso()

    def mark(self, kind="duty", days=0, span=0, place="", user_id=None, note=""):
        start = _shift_days(self.today, days)
        payload = {"kind": kind, "date_from": start,
                   "date_to": _shift_days(start, span), "place": place, "note": note}
        if user_id is not None:
            payload["user_id"] = user_id
        return self.client.post("/api/absences", json=payload)

    def test_the_roster_is_a_grid_of_people_and_days(self):
        """Сетку раскладывает сервер, а не браузер.

        Разложить период по суткам в трёх местах интерфейса — верный способ
        получить три разных расхода, а он в отделе один.
        """
        self.mark("trip", days=1, span=2, place="в/ч 74326", user_id=self.engineer.id)
        body = self.client.get("/api/roster", params={"date_from": self.today,
                                                      "days": 7}).json()
        self.assertEqual(7, len(body["days"]))
        self.assertEqual(self.today, body["days"][0])
        self.assertEqual(_shift_days(self.today, 6), body["date_to"])

        # Одна запись на трое суток даёт три клетки, а не одну.
        marked = [day for day in body["days"]
                  if f"{self.engineer.id}|{day}" in body["cells"]]
        self.assertEqual([_shift_days(self.today, step) for step in (1, 2, 3)], marked)
        cell = body["cells"][f"{self.engineer.id}|{_shift_days(self.today, 1)}"][0]
        self.assertEqual("командировка", cell["kind_title"])
        self.assertEqual("в/ч 74326", cell["place"])
        self.assertFalse(cell["present"])

    def test_the_grid_lists_the_whole_department(self):
        # Пустая строка — тоже сведение: видно, кто расход не заполнил.
        body = self.client.get("/api/roster").json()
        logins = {person["full_name"] for person in body["staff"]}
        self.assertIn("Инженеров И. И.", logins)
        self.assertIn("Начальников Н. Н.", logins)

    def test_your_own_row_is_editable(self):
        self.login("engineer")
        body = self.client.get("/api/roster").json()
        me = [person for person in body["staff"] if person["is_me"]][0]
        self.assertTrue(me["can_edit"])

    def test_the_system_creator_is_not_a_person_of_the_department(self):
        """Создатель систему поставил, а не служит в отделе.

        Он не дежурит, писем за ним не числится, и в расходе его строка —
        всегда пустая, зато сбивает счёт: «12 человек в строю» становилось
        13. В разделе «Военнослужащие» он, наоборот, обязан быть виден: это
        перечень учётных записей.
        """
        body = self.client.get("/api/roster").json()
        self.assertNotIn("Хозяев Х. Х.",
                         {person["full_name"] for person in body["staff"]})
        # Список для выбора исполнителя письма — тоже список отдела.
        staff = self.client.get("/api/staff").json()["items"]
        self.assertNotIn("Хозяев Х. Х.", {item["full_name"] for item in staff})
        board = self.client.get("/api/board").json()
        self.assertNotIn("Хозяев Х. Х.",
                         {item["full_name"] for item in board.get("people", [])})
        # А в учётных записях — на месте.
        users = self.client.get("/api/users").json()["items"]
        self.assertIn("admin", {item["login"] for item in users})

    def test_the_creator_is_not_counted_in_the_headcount(self):
        board = self.client.get("/api/board").json()
        staff = board["totals"]["staff"]
        active = [u for u in self.repos.users.list_all(active_only=True)
                  if u.role != "owner"]
        self.assertEqual(len(active), staff)

    def test_an_engineer_may_edit_only_their_own_row(self):
        self.login("engineer")
        body = self.client.get("/api/roster").json()
        mine = [person for person in body["staff"] if person["is_me"]][0]
        others = [person for person in body["staff"] if not person["is_me"]]
        self.assertTrue(mine["can_edit"])
        self.assertTrue(all(not person["can_edit"] for person in others),
                        "инженеру дали править чужой расход")

    def test_the_window_of_the_grid_is_bounded(self):
        # Месяц столбцов в одной сетке ещё читается, год — уже нет.
        body = self.client.get("/api/roster", params={"days": 400}).json()
        self.assertLessEqual(len(body["days"]), 31)
        body = self.client.get("/api/roster", params={"days": 1}).json()
        self.assertEqual(1, len(body["days"]))
        # Без указания — неделя: столько отдел планирует за раз.
        self.assertEqual(7, len(self.client.get("/api/roster").json()["days"]))

    def test_two_marks_on_the_same_days_are_refused(self):
        """Расход, где человек разом в отпуске и на дежурстве, — не расход."""
        self.assertEqual(200, self.mark("duty", span=2).status_code)
        clash = self.mark("vacation", days=1, span=5)
        self.assertEqual(409, clash.status_code)
        self.assertIn("уже есть отметка", clash.json()["error"])
        # Соседние сутки не пересекаются — их занимать можно.
        self.assertEqual(200, self.mark("vacation", days=3, span=1).status_code)

    def test_a_mark_may_be_corrected_instead_of_duplicated(self):
        # Планы меняются чаще, чем расход пишут.
        item = self.mark("duty", place="Узел 3").json()["absence"]
        response = self.client.patch(f"/api/absences/{item['id']}", json={
            "kind": "work", "place": "Аппаратная 2", "note": "замена модема"})
        self.assertEqual(200, response.status_code, response.text)
        fixed = response.json()["absence"]
        self.assertEqual("работы", fixed["kind_title"])
        self.assertEqual("Аппаратная 2", fixed["place"])
        self.assertTrue(fixed["present"], "работы в отделе — это «на месте»")

    def test_correcting_a_mark_does_not_step_on_another(self):
        first = self.mark("duty", span=1).json()["absence"]
        self.mark("vacation", days=5, span=2)
        clash = self.client.patch(f"/api/absences/{first['id']}", json={
            "date_from": self.today, "date_to": _shift_days(self.today, 6)})
        self.assertEqual(409, clash.status_code)

    def test_a_stranger_mark_is_not_yours_to_change(self):
        item = self.mark("duty", user_id=self.engineer.id).json()["absence"]
        self.login("engineer")
        self.assertEqual(200, self.client.patch(
            f"/api/absences/{item['id']}", json={"place": "Узел 1"}).status_code)

        boss = self.repos.users.by_login("nachalnik")
        theirs = self.repos.absences.add(boss.id, "duty", _shift_days(self.today, 9),
                                         _shift_days(self.today, 9))
        self.assertEqual(403, self.client.patch(
            f"/api/absences/{theirs.id}", json={"place": "Узел 1"}).status_code)
        self.assertEqual(403, self.client.delete(
            f"/api/absences/{theirs.id}").status_code)

    def test_the_day_view_says_who_is_where_and_who_is_silent(self):
        """Расход на день — то, что начальник читает на разводе.

        Кто себя не отметил, тот на месте: человек приходит на службу, и
        отмечают в расходе как раз отклонения. Иначе отдел, где все на
        местах, выглядел бы ненаписанным расходом.
        """
        self.mark("duty", place="Узел 3", user_id=self.engineer.id)
        body = self.client.get("/api/roster/day", params={"date": self.today}).json()
        duty = [group for group in body["groups"] if group["id"] == "duty"][0]
        self.assertEqual(["Инженеров И. И."],
                         [person["full_name"] for person in duty["people"]])
        self.assertEqual(0, body["away"])
        self.assertEqual(1, body["marked"])
        self.assertEqual(1, body["marked_present"])
        # На месте — дежурный и все, кто себя не отмечал.
        self.assertEqual(body["total"], body["present"])
        self.assertEqual(body["total"] - 1, len(body["unmarked"]))
        # Кто именно молчит — видно поимённо.
        self.assertIn("Начальников Н. Н.",
                      {person["full_name"] for person in body["unmarked"]})

    def test_an_absent_person_is_the_only_one_missing_from_the_place(self):
        # Отсутствие — только явно отмеченное. Остальные на месте.
        self.mark("vacation", span=3, user_id=self.engineer.id)
        body = self.client.get("/api/roster/day", params={"date": self.today}).json()
        self.assertEqual(1, body["away"])
        self.assertEqual(body["total"] - 1, body["present"])

    def test_work_in_the_department_is_not_an_absence(self):
        """«Работы» — человек на месте: его можно спросить и ему можно дать письмо."""
        self.mark("work", place="Аппаратная 2", user_id=self.engineer.id)
        board = self.client.get("/api/board").json()
        self.assertEqual(0, board["totals"]["away"])
        day = self.client.get("/api/roster/day").json()
        self.assertEqual(1, day["marked_present"])
        self.assertEqual(day["total"], day["present"])

    def test_a_mark_longer_than_a_year_is_a_typo(self):
        # 2099 вместо 2029 — обычная описка, и сетка от неё становится
        # нечитаемой на годы вперёд.
        response = self.client.post("/api/absences", json={
            "kind": "vacation", "date_from": self.today, "date_to": "2099-01-01"})
        self.assertEqual(400, response.status_code)
        self.assertIn("длиннее года", response.json()["error"])


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
    """Военнослужащие, должности и пароли — из интерфейса, а не только из CLI."""

    def test_only_admin_sees_the_list(self):
        self.login("engineer")
        self.assertEqual(403, self.client.get("/api/users").status_code)

    def test_list_has_roles_with_explanations(self):
        data = self.client.get("/api/users").json()
        self.assertTrue(data["items"])
        titles = {role["id"]: role for role in data["roles"]}
        self.assertEqual(
            {"owner", "head", "deputy", "lead", "senior", "engineer", "guest"},
            set(titles)
        )
        for role in data["roles"]:
            self.assertTrue(role["note"], f"у должности {role['id']} нет пояснения")
            self.assertTrue(role["title"], f"у должности {role['id']} нет названия")
        # Права администратора — до начальника группы включительно.
        self.assertTrue(all(titles[key]["is_admin"]
                            for key in ("owner", "head", "deputy", "lead")))
        self.assertFalse(any(titles[key]["is_admin"]
                             for key in ("senior", "engineer", "guest")))

    def test_local_service_record_is_hidden(self):
        # Запись «local» — служебная, в списке личного состава ей не место.
        logins = {item["login"] for item in self.client.get("/api/users").json()["items"]}
        self.assertNotIn("local", logins)

    def test_create_and_update(self):
        created = self.client.post("/api/users", json={
            "login": "petrov", "full_name": "Петров Пётр Петрович",
            "password": "пароль12345", "role": "engineer",
        })
        self.assertEqual(200, created.status_code, created.text)
        user = created.json()["user"]
        # В записи имя лежит целиком, в списках показывается инициалами.
        self.assertEqual("Петров Пётр Петрович", user["full_name"])
        self.assertEqual("Петров П. П.", user["short_name"])

        patched = self.client.patch(f"/api/users/{user['id']}", json={
            "role": "senior", "department": "Отдел связи", "team": "1 группа"})
        self.assertEqual("senior", patched.json()["user"]["role"])
        self.assertEqual("Отдел связи", patched.json()["user"]["department"])
        self.assertEqual("Старший инженер отдела", patched.json()["user"]["role_title"])

    def test_login_rules_are_enforced(self):
        for bad in ("ab", "Петров", "with space", "x" * 40):
            with self.subTest(login=bad):
                response = self.client.post("/api/users", json={
                    "login": bad, "full_name": "Плохов Логин Логинович",
                    "password": "пароль12345", "role": "engineer"})
                self.assertEqual(400, response.status_code)

    def test_short_password_refused(self):
        response = self.client.post("/api/users", json={
            "login": "sidorov", "full_name": "Сидоров Сидор Сидорович",
            "password": "коротк", "role": "engineer"})
        self.assertEqual(400, response.status_code)

    def test_duplicate_login_refused(self):
        payload = {"login": "dublikat", "full_name": "Дубликатов Дубль Дублевич",
                   "password": "пароль12345", "role": "engineer"}
        self.assertEqual(200, self.client.post("/api/users", json=payload).status_code)
        self.assertEqual(409, self.client.post("/api/users", json=payload).status_code)

    def test_owner_cannot_be_demoted_by_anyone(self):
        # Создателя не разжалует никто, включая его самого: иначе система
        # остаётся без владельца, а чинится это только командной строкой.
        me = self.client.get("/api/me").json()["user"]
        response = self.client.patch(f"/api/users/{me['id']}", json={"role": "engineer"})
        self.assertEqual(403, response.status_code)

    def test_admins_are_counted_by_position(self):
        # Создатель, начальник отдела, заместитель и начальник группы —
        # администраторы; старший инженер и инженер — нет.
        self.assertEqual(4, self.repos.users.count_admins())
        engineer = self.repos.users.by_login("engineer")
        self.assertFalse(engineer.is_admin)

    def test_role_notes_agree_with_the_code(self):
        """Описание должности не должно обещать того, чего нет.

        Карточка прав и личный кабинет читают ROLE_NOTES; там значилось,
        что отчёты утверждает каждый.
        """
        from reportgen.store.models import REVIEW_ROLES, ROLE_NOTES, ROLES

        for role in ROLES:
            note = ROLE_NOTES[role]
            low = note.lower()
            says_reviews = ("проверяет отчёт" in low
                            or "включая проверку отчётов" in low)
            with self.subTest(role=role):
                self.assertEqual(role in REVIEW_ROLES, says_reviews, note)
            # Слово «утверждает» из описаний ушло: отчёты теперь проверяют.
            self.assertNotIn("утвержда", note, role)

    def test_reviewers_are_not_the_same_set_as_administrators(self):
        """Проверять отчёты — не то же самое, что заводить людей.

        Начальник группы администратор, но отчёты проверяет начальник
        отдела или его заместитель: так устроен порядок в отделе.
        """
        by_login = {login: self.repos.users.by_login(login)
                    for login in ("admin", "nachalnik", "zam", "gruppa", "engineer")}
        self.assertTrue(by_login["gruppa"].is_admin)
        self.assertFalse(by_login["gruppa"].can_review)
        for login in ("admin", "nachalnik", "zam"):
            self.assertTrue(by_login[login].can_review, login)
        self.assertFalse(by_login["engineer"].can_review)

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
            "login": "vyshe", "full_name": "Вышев Выше Вышевич",
            "password": "пароль12345", "role": "head"})
        self.assertEqual(403, response.status_code)

    def test_cannot_disable_self(self):
        me = self.client.get("/api/me").json()["user"]
        response = self.client.post(f"/api/users/{me['id']}/active", json={"active": False})
        self.assertEqual(409, response.status_code)

    def test_junior_cannot_switch_a_senior_back_on(self):
        """Старшинство действует в обе стороны.

        Проверка стояла только на отключении, и начальник группы возвращал
        доступ отключённому начальнику отдела. Чужая учётная запись
        старшего по должности — не его дело ни в ту, ни в другую сторону.
        """
        head = [u for u in self.client.get("/api/users").json()["items"]
                if u["role"] == "head"][0]
        self.repos.users.set_active(head["id"], False)
        self.login("gruppa")
        response = self.client.post(f"/api/users/{head['id']}/active", json={"active": True})
        self.assertEqual(403, response.status_code)
        self.assertFalse(self.repos.users.get(head["id"]).active)

    def test_junior_is_switched_back_on_as_before(self):
        engineer = [u for u in self.client.get("/api/users").json()["items"]
                    if u["role"] == "engineer"][0]
        self.repos.users.set_active(engineer["id"], False)
        self.login("gruppa")
        response = self.client.post(f"/api/users/{engineer['id']}/active",
                                    json={"active": True})
        self.assertEqual(200, response.status_code, response.text)
        self.assertTrue(self.repos.users.get(engineer["id"]).active)

    def test_password_reset_closes_sessions(self):
        created = self.client.post("/api/users", json={
            "login": "smena", "full_name": "Сменов Смена Сменович",
            "password": "пароль12345", "role": "engineer"}).json()["user"]
        response = self.client.post(f"/api/users/{created['id']}/password",
                                    json={"password": "новыйпароль1"})
        self.assertEqual(200, response.status_code)


class PersonalFileTests(WebTestCase):
    """Документы военнослужащего: справка-объективка, приказы, прочее."""

    def setUp(self):
        super().setUp()
        self.engineer = self.repos.users.by_login("engineer")

    def upload(self, user_id, name="объективка.docx", kind="profile"):
        return self.client.post(
            f"/api/users/{user_id}/files",
            files={"file": (name, "справка о военнослужащем".encode("utf-8"),
                            "application/octet-stream")},
            data={"kind": kind})

    def test_everyone_keeps_their_own_file(self):
        """Свою объективку человек грузит и скачивает сам."""
        self.login("engineer")
        response = self.upload(self.engineer.id)
        self.assertEqual(200, response.status_code, response.text)
        item = response.json()["file"]
        self.assertEqual("справка-объективка", item["kind_title"])

        listed = self.client.get(f"/api/users/{self.engineer.id}/files").json()
        self.assertEqual([item["id"]], [row["id"] for row in listed["files"]])
        self.assertTrue(listed["can_edit"])

        back = self.client.get(f"/api/users/{self.engineer.id}/files/{item['id']}")
        self.assertEqual(200, back.status_code)
        self.assertEqual("справка о военнослужащем".encode("utf-8"), back.content)

    def test_only_the_head_deputy_and_owner_see_someone_elses_file(self):
        """Объективка — личные сведения, и открыта не всему отделу.

        Начальник группы сюда не входит, хотя он и администратор: круг тех,
        кому открыта справка, уже круга тех, кто заводит учётные записи.
        """
        self.login("engineer")
        self.upload(self.engineer.id)

        for who in ("nachalnik", "zam", "admin"):
            with self.subTest(who=who):
                self.login(who)
                response = self.client.get(f"/api/users/{self.engineer.id}/files")
                self.assertEqual(200, response.status_code, f"{who} не видит дело")
                self.assertEqual(1, len(response.json()["files"]))

        for who in ("gruppa",):
            with self.subTest(who=who):
                self.login(who)
                response = self.client.get(f"/api/users/{self.engineer.id}/files")
                self.assertEqual(403, response.status_code, f"{who} читает чужое дело")
                self.assertIn("документы военнослужащего", response.json()["error"])

    def test_an_engineer_cannot_reach_a_colleagues_file(self):
        self.login("engineer")
        boss = self.repos.users.by_login("nachalnik")
        self.assertEqual(403, self.client.get(f"/api/users/{boss.id}/files").status_code)
        self.assertEqual(403, self.upload(boss.id).status_code)

    def test_a_new_certificate_replaces_the_old_one(self):
        """Справка-объективка одна: новая заменяет прежнюю.

        Иначе список копит редакции, и начальник не знает, какая из них
        действующая. Приказы и прочее копятся — таких бумаг бывает много.
        """
        self.login("engineer")
        first = self.upload(self.engineer.id, "объективка-старая.docx").json()["file"]
        old_path = Path(self.repos.person_files.get(first["id"]).path)
        second = self.upload(self.engineer.id, "объективка-новая.docx").json()["file"]

        files = self.client.get(f"/api/users/{self.engineer.id}/files").json()["files"]
        self.assertEqual([second["id"]], [row["id"] for row in files])
        self.assertEqual("справка-объективка", files[0]["kind_title"])
        self.assertFalse(old_path.exists(), "прежняя справка осталась на диске")

    def test_orders_and_the_rest_pile_up(self):
        """К военнослужащему кладут не одну бумагу: приказов и допусков много."""
        self.login("engineer")
        self.upload(self.engineer.id, "объективка.docx")
        self.upload(self.engineer.id, "приказ-14.pdf", kind="order")
        self.upload(self.engineer.id, "приказ-27.pdf", kind="order")
        self.upload(self.engineer.id, "допуск.pdf", kind="other")
        files = self.client.get(f"/api/users/{self.engineer.id}/files").json()["files"]
        self.assertEqual(4, len(files))
        self.assertEqual({"справка-объективка", "приказ", "прочее"},
                         {row["kind_title"] for row in files})

    def test_the_kinds_come_from_the_server(self):
        # Список видов один на сервер и на экран: разойтись им нельзя.
        from reportgen.store.models import PERSON_FILE_KINDS, PERSON_FILE_TITLES

        self.login("engineer")
        kinds = self.client.get(
            f"/api/users/{self.engineer.id}/files").json()["kinds"]
        self.assertEqual(list(PERSON_FILE_KINDS), [item["id"] for item in kinds])
        self.assertEqual([PERSON_FILE_TITLES[key] for key in PERSON_FILE_KINDS],
                         [item["title"] for item in kinds])

        unknown = self.client.post(
            f"/api/users/{self.engineer.id}/files",
            files={"file": ("что-то.pdf", b"%PDF-1.4", "application/pdf")},
            data={"kind": "выдумка"})
        self.assertEqual(400, unknown.status_code)

    def test_a_removed_file_leaves_the_disk(self):
        self.login("engineer")
        item = self.upload(self.engineer.id).json()["file"]
        path = Path(self.repos.person_files.get(item["id"]).path)
        self.assertTrue(path.is_file())
        self.assertEqual(200, self.client.delete(
            f"/api/users/{self.engineer.id}/files/{item['id']}").status_code)
        self.assertFalse(path.exists())

    def test_any_kind_of_document_can_be_put_in_a_personal_file(self):
        """Бумаги на человека приходят в чём угодно: скан, выписка, архив."""
        self.login("engineer")
        for name in ("vypiska.rar", "skan.tiff", "prikaz.dat", "bez_rasshireniya"):
            response = self.client.post(
                f"/api/users/{self.engineer.id}/files",
                files={"file": (name, b"\x00\x01", "application/octet-stream")},
                data={"kind": "order"})
            self.assertEqual(200, response.status_code, f"{name}: {response.text}")

    def test_contacts_are_filled_in_by_the_person_themselves(self):
        """Справочник кадровика устаревает быстрее, чем его правят."""
        self.login("engineer")
        response = self.client.patch("/api/me/contacts", json={
            "phone_mobile": "+7 900 000-00-00", "phone_open": "3-45",
            "phone_secure": "2-17", "room": "214"})
        self.assertEqual(200, response.status_code, response.text)
        user = response.json()["user"]
        self.assertEqual("+7 900 000-00-00", user["phone_mobile"])
        self.assertEqual("3-45", user["phone_open"])
        self.assertEqual("2-17", user["phone_secure"])
        self.assertEqual("214", user["room"])

        summary = self.client.get("/api/me/summary").json()
        self.assertEqual("214", summary["user"]["room"])

    def test_a_phone_says_whether_one_may_talk_on_it(self):
        """Телефонов в отделе три, и это не прихоть.

        По мобильному звонят, по открытому говорят о работе в общих словах,
        по режимному — обо всём остальном. Одно поле «Телефон» на все три не
        отвечало на первый же вопрос: можно ли по этому номеру говорить.
        """
        self.login("engineer")
        self.client.patch("/api/me/contacts", json={
            "phone_mobile": "+7 900 111-22-33", "phone_secure": "2-17"})
        person = self.repos.users.by_login("engineer")
        card = self.client.get(f"/api/people/{person.id}").json()["person"]
        self.assertEqual("+7 900 111-22-33", card["phone_mobile"])
        self.assertEqual("2-17", card["phone_secure"])
        # Почты в кабинете больше нет: отдел изолированный, писать некуда.
        self.assertNotIn("email", card)

    def test_contacts_are_not_a_way_to_change_a_rank(self):
        # Через контакты нельзя дотянуться до должности: она не своё дело.
        self.login("engineer")
        self.client.patch("/api/me/contacts", json={"role": "owner", "phone_mobile": "1"})
        self.assertEqual("engineer", self.repos.users.by_login("engineer").role)

    def test_the_cabinet_says_what_is_on_the_person_right_now(self):
        """Кабинет отвечает не только «сколько я сделал», но и «что за мной»."""
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={
            "assignee_id": self.engineer.id, "deadline": "2000-01-01"})
        self.login("engineer")
        body = self.client.get("/api/me/summary").json()
        self.assertEqual(1, body["my_cases_total"])
        self.assertEqual(1, body["overdue"])
        self.assertEqual([case["case_id"]], [item["case_id"] for item in body["my_cases"]])

    def test_the_cabinet_shows_the_persons_own_roster(self):
        self.login("engineer")
        today = _today_iso()
        self.client.post("/api/absences", json={
            "kind": "duty", "date_from": _shift_days(today, 2),
            "date_to": _shift_days(today, 2), "place": "Узел 3"})
        body = self.client.get("/api/me/summary").json()
        self.assertEqual(["Узел 3"], [item["place"] for item in body["roster"]])
        # Далёкая отметка в ближайшие две недели не попадает.
        self.client.post("/api/absences", json={
            "kind": "vacation", "date_from": _shift_days(today, 40),
            "date_to": _shift_days(today, 50)})
        body = self.client.get("/api/me/summary").json()
        self.assertEqual(1, len(body["roster"]))


class SelfRegistrationTests(WebTestCase):
    """Заявка на доступ: заводит себя человек, открывает начальство."""

    def apply(self, login="sidorov", password="пароль12345", full_name="Сидоров С. С."):
        self.client.cookies.clear()
        return self.client.post("/api/auth/register", json={
            "login": login, "password": password, "full_name": full_name})

    def test_an_application_needs_no_login_of_its_own(self):
        """Заявку подаёт тот, у кого доступа ещё нет.

        Путь за проверкой входа означал бы, что подать заявку может только
        уже вошедший, — то есть никто.
        """
        response = self.apply()
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("sidorov", response.json()["login"])

    def test_an_unapproved_person_cannot_get_in(self):
        # Пароль верный, а доступа нет: система обязана сказать почему,
        # иначе человек час ищет опечатку в пароле.
        self.apply()
        response = self.client.post("/api/auth/login", json={
            "login": "sidorov", "password": "пароль12345"})
        self.assertEqual(403, response.status_code)
        self.assertIn("не одобрена", response.json()["error"])

    def test_an_application_does_not_choose_its_own_rank(self):
        # Иначе пришедший записал бы себя начальником отдела.
        self.apply()
        self.login("admin")
        pending = self.client.get("/api/users/pending").json()["items"]
        self.assertEqual(["sidorov"], [item["login"] for item in pending])
        self.assertEqual("engineer", pending[0]["role"])

    def test_an_applicant_is_not_yet_a_member_of_the_department(self):
        # До одобрения это не военнослужащий: ни в списке личного состава, ни в
        # сводке отдела его быть не должно.
        self.apply()
        self.login("admin")
        staff = self.client.get("/api/users").json()["items"]
        self.assertNotIn("sidorov", [item["login"] for item in staff])
        board = self.client.get("/api/board").json()
        self.assertNotIn("sidorov", [item["login"] for item in board["people"]])

    def test_the_boss_opens_access_and_assigns_the_rank(self):
        self.apply()
        self.login("admin")
        person = self.client.get("/api/users/pending").json()["items"][0]
        response = self.client.post(f"/api/users/{person['id']}/approve",
                                    json={"role": "senior", "team": "3 группа"})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("senior", response.json()["user"]["role"])
        self.assertEqual([], self.client.get("/api/users/pending").json()["items"])

        self.client.cookies.clear()
        entered = self.client.post("/api/auth/login", json={
            "login": "sidorov", "password": "пароль12345"})
        self.assertEqual(200, entered.status_code, entered.text)

    def test_the_approved_person_learns_about_it(self):
        # Человек не должен пробовать войти каждый день, гадая, одобрили ли.
        self.apply()
        self.login("admin")
        person = self.client.get("/api/users/pending").json()["items"][0]
        self.client.post(f"/api/users/{person['id']}/approve", json={"role": "engineer"})
        self.client.cookies.clear()
        self.login("sidorov", "пароль12345")
        notices = self.client.get("/api/notifications").json()["items"]
        self.assertEqual(["user.approved"], [item["kind"] for item in notices])

    def test_an_engineer_neither_sees_nor_approves_applications(self):
        # Одобряет только создатель, начальник отдела, зам и начальник группы.
        self.apply()
        self.login("admin")
        person = self.client.get("/api/users/pending").json()["items"][0]
        self.login("engineer")
        self.assertEqual(403, self.client.get("/api/users/pending").status_code)
        refused = self.client.post(f"/api/users/{person['id']}/approve",
                                   json={"role": "engineer"})
        self.assertEqual(403, refused.status_code)
        # Отказ именно по должности: инженер не ведёт личный состав. Разбор
        # «кого кем можно назначить» дальше по коду до него не доходит —
        # иначе запрет держался бы случайно, на равенстве старшинства.
        self.assertIn("начальника группы", refused.json()["error"])
        self.assertEqual(
            403, self.client.post(f"/api/users/{person['id']}/reject",
                                  json={}).status_code)

    def test_a_group_lead_may_approve(self):
        self.apply()
        self.login("gruppa")
        person = self.client.get("/api/users/pending").json()["items"][0]
        response = self.client.post(f"/api/users/{person['id']}/approve",
                                    json={"role": "engineer"})
        self.assertEqual(200, response.status_code, response.text)

    def test_nobody_is_promoted_above_the_one_who_approves(self):
        self.apply()
        self.login("gruppa")
        person = self.client.get("/api/users/pending").json()["items"][0]
        response = self.client.post(f"/api/users/{person['id']}/approve",
                                    json={"role": "head"})
        self.assertEqual(403, response.status_code)
        self.assertIn("выше собственной", response.json()["error"])

    def test_a_rejected_application_leaves_nothing_behind(self):
        # Отклонённая заявка — не военнослужащий: держать её в списке значит
        # копить мусор, по которому никто не работает.
        self.apply()
        self.login("admin")
        person = self.client.get("/api/users/pending").json()["items"][0]
        self.assertEqual(200, self.client.post(
            f"/api/users/{person['id']}/reject", json={}).status_code)
        self.assertEqual([], self.client.get("/api/users/pending").json()["items"])
        # Логин освободился: человек может подать заявку заново.
        self.assertEqual(200, self.apply().status_code)

    def test_an_approved_account_is_switched_off_and_not_rejected(self):
        self.apply()
        self.login("admin")
        person = self.client.get("/api/users/pending").json()["items"][0]
        self.client.post(f"/api/users/{person['id']}/approve", json={"role": "engineer"})
        again = self.client.post(f"/api/users/{person['id']}/reject", json={})
        self.assertEqual(409, again.status_code)
        self.assertIn("отключают", again.json()["error"])

    def test_a_taken_login_is_refused_plainly(self):
        self.client.cookies.clear()
        response = self.client.post("/api/auth/register", json={
            "login": "engineer", "password": "пароль12345", "full_name": "Другой Д. Д."})
        self.assertEqual(409, response.status_code)
        self.assertIn("занят", response.json()["error"])

    def test_a_short_password_is_refused_before_the_record_appears(self):
        response = self.apply(password="123")
        self.assertEqual(400, response.status_code)
        self.login("admin")
        self.assertEqual([], self.client.get("/api/users/pending").json()["items"])

    def test_applications_cannot_be_poured_in_endlessly(self):
        """Путь открыт без входа — значит, его можно дёргать в цикле.

        Десяток заявок подряд с одного места — это не отдел устраивается
        на работу, а очередь на одобрение, забитая за минуту.
        """
        codes = [self.apply(login=f"chelovek{n}").status_code for n in range(8)]
        self.assertIn(429, codes)
        self.assertIn("слишком много", self.apply(login="ещё").json()["error"])


class NotificationTests(WebTestCase):
    """Уведомления: смена состояния письма, вызов в кабинет, звук."""

    def setUp(self):
        super().setUp()
        self.engineer = self.repos.users.by_login("engineer")
        self.login("engineer")
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])
        self.submit(self.report["id"])

    def notices(self, login="engineer"):
        self.login(login)
        return self.client.get("/api/notifications").json()

    def test_the_owner_learns_that_the_report_came_back(self):
        # Начальник нашёл ошибку — исполнитель должен узнать об этом, не
        # заглядывая в письмо по расписанию.
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/rework",
                         json={"note": "Ствол 3: поляризация не та"})
        data = self.notices()
        kinds = [item["kind"] for item in data["items"]]
        self.assertIn("report.rework", kinds)
        back = [item for item in data["items"] if item["kind"] == "report.rework"][0]
        self.assertIn("Ствол 3", back["body"])
        self.assertIn(str(self.case["id"]), back["link"])

    def test_a_returned_report_rings(self):
        # Возврат на доработку и вызов в кабинет — со звуком: остальное
        # подождёт до того, как человек посмотрит на экран.
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/rework", json={"note": "поправить"})
        loud = [item for item in self.notices()["items"] if item["loud"]]
        self.assertEqual(["report.rework"], [item["kind"] for item in loud])

    def test_the_boss_is_told_that_a_report_was_handed_in(self):
        data = self.notices("nachalnik")
        self.assertIn("report.review", [item["kind"] for item in data["items"]])

    def test_the_counter_shows_only_what_has_not_been_looked_at(self):
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.report['id']}/rework", json={"note": "поправить"})
        data = self.notices()
        self.assertEqual(data["unseen"], len([i for i in data["items"] if not i["seen"]]))
        first = data["items"][0]["id"]
        self.client.post("/api/notifications/read", json={"ids": [first]})
        self.assertEqual(data["unseen"] - 1,
                         self.client.get("/api/notifications").json()["unseen"])

    def test_a_call_to_the_office_reaches_the_person(self):
        self.login("nachalnik")
        response = self.client.post("/api/notifications/call", json={
            "user_id": self.engineer.id, "place": "каб. 214", "note": "с журналом"})
        self.assertEqual(200, response.status_code, response.text)
        call = [item for item in self.notices()["items"] if item["kind"] == "call"][0]
        self.assertIn("Начальников", call["title"])
        self.assertIn("каб. 214", call["body"])
        self.assertIn("с журналом", call["body"])
        self.assertTrue(call["loud"])

    def test_calling_is_the_right_of_the_leadership(self):
        # Вызывать друг друга всем подряд — не порядок, а способ мешать
        # работать.
        self.login("engineer")
        boss = self.repos.users.by_login("nachalnik")
        response = self.client.post("/api/notifications/call", json={"user_id": boss.id})
        self.assertEqual(403, response.status_code)

    def test_nobody_calls_themselves(self):
        self.login("nachalnik")
        boss = self.repos.users.by_login("nachalnik")
        response = self.client.post("/api/notifications/call", json={"user_id": boss.id})
        self.assertEqual(400, response.status_code)
        self.assertIn("себя", response.json()["error"])

    def test_notices_are_private(self):
        # Уведомление адресовано человеку, а не отделу.
        self.login("nachalnik")
        self.client.post("/api/notifications/call", json={"user_id": self.engineer.id})
        others = self.client.get("/api/notifications").json()["items"]
        self.assertNotIn("call", [item["kind"] for item in others])


class CaseNoteTests(WebTestCase):
    """Примечания к письму: обсуждение остаётся при деле."""

    def setUp(self):
        super().setUp()
        self.login("engineer")
        self.case = self.create_case()

    def add(self, text):
        return self.client.post(f"/api/cases/{self.case['id']}/notes", json={"text": text})

    def test_a_note_stays_with_the_letter(self):
        response = self.add("Ствол 3: сверить поляризацию с журналом")
        self.assertEqual(200, response.status_code, response.text)
        notes = self.client.get(f"/api/cases/{self.case['id']}/notes").json()["notes"]
        self.assertEqual(["Ствол 3: сверить поляризацию с журналом"],
                         [item["text"] for item in notes])
        self.assertEqual("Инженеров И. И.", notes[0]["author"])

    def test_an_empty_note_is_not_a_note(self):
        self.assertEqual(400, self.add("   ").status_code)

    def test_the_one_it_concerns_is_told(self):
        # Письмо в эту минуту он может не держать открытым.
        self.login("nachalnik")
        self.add("Переделать раздел о выводах")
        self.login("engineer")
        notices = self.client.get("/api/notifications").json()["items"]
        note = [item for item in notices if item["kind"] == "case.note"][0]
        self.assertIn("Переделать раздел", note["body"])
        self.assertIn(str(self.case["id"]), note["link"])

    def test_a_person_does_not_get_a_notice_about_their_own_note(self):
        self.add("Себе на память")
        notices = self.client.get("/api/notifications").json()["items"]
        self.assertNotIn("case.note", [item["kind"] for item in notices])

    def test_own_note_is_removed_by_its_author(self):
        note = self.add("Опечатка").json()["note"]
        self.assertEqual(200, self.client.delete(
            f"/api/cases/{self.case['id']}/notes/{note['id']}").status_code)
        self.assertEqual([], self.client.get(
            f"/api/cases/{self.case['id']}/notes").json()["notes"])

    def test_a_stranger_does_not_remove_a_note_but_the_boss_does(self):
        note = self.add("Замечание по делу").json()["note"]
        second = self.repos.users.create("drugoy", "пароль123", "Другов Д. Д.", "engineer")
        self.assertTrue(second)
        self.login("drugoy")
        self.assertEqual(403, self.client.delete(
            f"/api/cases/{self.case['id']}/notes/{note['id']}").status_code)
        self.login("nachalnik")
        self.assertEqual(200, self.client.delete(
            f"/api/cases/{self.case['id']}/notes/{note['id']}").status_code)


class TalkTests(WebTestCase):
    """Переписка между людьми отдела: личная и на несколько человек."""

    def setUp(self):
        super().setUp()
        self.engineer = self.repos.users.by_login("engineer")
        self.boss = self.repos.users.by_login("nachalnik")

    def test_a_private_talk_is_not_started_twice(self):
        """Иначе каждое «написать Иванову» рождало бы новую ветку.

        Переписка рассыпалась бы на одинаковые беседы, и найти в них
        сказанное на прошлой неделе стало бы нельзя.
        """
        self.login("nachalnik")
        first = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.assertFalse(first["existed"])
        second = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.assertTrue(second["existed"])
        self.assertEqual(first["talk_id"], second["talk_id"])
        # И с другой стороны — та же беседа, а не вторая.
        self.login("engineer")
        third = self.client.post("/api/talks", json={"members": [self.boss.id]}).json()
        self.assertEqual(first["talk_id"], third["talk_id"])

    def test_a_talk_is_removed_for_the_one_who_left(self):
        """«Убрать беседу» убирает её у себя, а не у собеседника."""
        self.login("nachalnik")
        talk = self.client.post("/api/talks",
                                json={"members": [self.engineer.id]}).json()
        talk_id = talk["talk_id"]
        self.client.post(f"/api/talks/{talk_id}/messages",
                         json={"text": "Подойдите с материалами"})
        answer = self.client.delete(f"/api/talks/{talk_id}")
        self.assertEqual(200, answer.status_code, answer.text)
        self.assertFalse(answer.json()["purged"], "стёрли у собеседника тоже")
        self.assertEqual([], self.client.get("/api/talks").json()["items"])
        self.assertEqual(404, self.client.get(f"/api/talks/{talk_id}").status_code)

    def test_the_other_side_keeps_the_talk(self):
        """Его половина разговора — тоже запись о работе отдела."""
        self.login("nachalnik")
        talk_id = self.client.post(
            "/api/talks", json={"members": [self.engineer.id]}).json()["talk_id"]
        self.client.post(f"/api/talks/{talk_id}/messages", json={"text": "Срочно"})
        self.client.delete(f"/api/talks/{talk_id}")
        self.login("engineer")
        items = self.client.get("/api/talks").json()["items"]
        self.assertEqual([talk_id], [item["id"] for item in items])
        data = self.client.get(f"/api/talks/{talk_id}").json()
        self.assertEqual(["Срочно"], [item["text"] for item in data["messages"]])

    def test_the_last_one_out_takes_the_talk_with_them(self):
        """Беседу, из которой ушли все, держать незачем."""
        self.login("nachalnik")
        talk_id = self.client.post(
            "/api/talks", json={"members": [self.engineer.id]}).json()["talk_id"]
        self.client.post(f"/api/talks/{talk_id}/messages", json={"text": "Срочно"})
        self.client.delete(f"/api/talks/{talk_id}")
        self.login("engineer")
        answer = self.client.delete(f"/api/talks/{talk_id}")
        self.assertTrue(answer.json()["purged"])
        self.assertEqual(0, self.repos.db.scalar(
            "SELECT count(*) FROM talks WHERE id = ?", (talk_id,)))
        self.assertEqual(0, self.repos.db.scalar(
            "SELECT count(*) FROM talk_messages WHERE talk_id = ?", (talk_id,)))

    def test_the_attached_files_leave_the_disk_with_the_talk(self):
        """Иначе снимки экрана из удалённых бесед копились бы годами."""
        import io
        from pathlib import Path

        self.login("nachalnik")
        talk_id = self.client.post(
            "/api/talks", json={"members": [self.engineer.id]}).json()["talk_id"]
        answer = self.client.post(
            f"/api/talks/{talk_id}/files",
            files={"file": ("spektr.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64),
                            "image/png")})
        self.assertEqual(200, answer.status_code, answer.text)
        stored = [Path(item.path) for item in self.repos.talks.files(talk_id)]
        self.assertTrue(stored and stored[0].is_file(), stored)
        self.client.delete(f"/api/talks/{talk_id}")
        self.login("engineer")
        self.client.delete(f"/api/talks/{talk_id}")
        self.assertFalse(stored[0].exists(), "файл остался на диске")
        self.assertEqual(0, self.repos.db.scalar(
            "SELECT count(*) FROM talk_files WHERE talk_id = ?", (talk_id,)))

    def test_a_stranger_cannot_remove_a_talk(self):
        self.login("nachalnik")
        talk_id = self.client.post(
            "/api/talks", json={"members": [self.engineer.id]}).json()["talk_id"]
        self.login("gruppa")
        self.assertEqual(404, self.client.delete(f"/api/talks/{talk_id}").status_code)
        self.login("engineer")
        self.assertEqual([talk_id],
                         [item["id"] for item in
                          self.client.get("/api/talks").json()["items"]])

    def test_writing_again_starts_a_clean_talk(self):
        """Ушли и написали снова — разговор начинается с чистого листа."""
        self.login("nachalnik")
        first = self.client.post(
            "/api/talks", json={"members": [self.engineer.id]}).json()["talk_id"]
        self.client.post(f"/api/talks/{first}/messages", json={"text": "Старое"})
        self.client.delete(f"/api/talks/{first}")
        again = self.client.post("/api/talks",
                                 json={"members": [self.engineer.id]}).json()
        self.assertFalse(again["existed"])
        self.assertNotEqual(first, again["talk_id"])
        self.assertEqual([], self.client.get(
            f"/api/talks/{again['talk_id']}").json()["messages"])

    def test_both_sides_see_the_message(self):
        self.login("nachalnik")
        talk = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.client.post(f"/api/talks/{talk['talk_id']}/messages",
                         json={"text": "Подойдите с материалами по РРЛС-4"})
        self.login("engineer")
        data = self.client.get(f"/api/talks/{talk['talk_id']}").json()
        self.assertEqual(["Подойдите с материалами по РРЛС-4"],
                         [item["text"] for item in data["messages"]])
        self.assertEqual("Начальников Н. Н.", data["messages"][0]["author"])

    def test_a_message_reaches_the_person_who_is_not_looking(self):
        self.login("nachalnik")
        talk = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.client.post(f"/api/talks/{talk['talk_id']}/messages", json={"text": "Срочно"})
        self.login("engineer")
        notices = self.client.get("/api/notifications").json()["items"]
        message = [item for item in notices if item["kind"] == "message"][0]
        self.assertIn("Начальников", message["title"])
        self.assertIn(str(talk["talk_id"]), message["link"])

    def test_unread_is_counted_until_the_talk_is_opened(self):
        self.login("nachalnik")
        talk = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.client.post(f"/api/talks/{talk['talk_id']}/messages", json={"text": "Раз"})
        self.client.post(f"/api/talks/{talk['talk_id']}/messages", json={"text": "Два"})
        self.login("engineer")
        listing = self.client.get("/api/talks").json()["items"]
        self.assertEqual(2, listing[0]["unread"])
        self.assertEqual("Два", listing[0]["last_text"])
        self.client.get(f"/api/talks/{talk['talk_id']}")
        self.assertEqual(0, self.client.get("/api/talks").json()["items"][0]["unread"])

    def test_own_messages_are_never_unread(self):
        self.login("nachalnik")
        talk = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.client.post(f"/api/talks/{talk['talk_id']}/messages", json={"text": "Раз"})
        self.assertEqual(0, self.client.get("/api/talks").json()["items"][0]["unread"])

    def test_any_member_of_the_department_may_start_a_talk(self):
        """Завести беседу — не право должности.

        Спрашивать разрешения написать товарищу по отделу не у кого и
        незачем: инженер заводит и личную переписку, и общую беседу так же,
        как начальник.
        """
        lead = self.repos.users.by_login("gruppa")
        self.login("engineer")
        private = self.client.post("/api/talks", json={"members": [self.boss.id]})
        self.assertEqual(200, private.status_code, private.text)
        group = self.client.post("/api/talks", json={
            "members": [self.boss.id, lead.id], "title": "Разбор по стволу 3"})
        self.assertEqual(200, group.status_code, group.text)
        # И обе беседы у него на месте.
        mine = self.client.get("/api/talks").json()["items"]
        self.assertEqual(2, len(mine))
        self.assertIn("Разбор по стволу 3", [item["title"] for item in mine])

    def test_a_group_talk_holds_everyone(self):
        lead = self.repos.users.by_login("gruppa")
        self.login("nachalnik")
        talk = self.client.post("/api/talks", json={
            "members": [self.engineer.id, lead.id], "title": "Разбор РРЛС-4"}).json()
        self.login("gruppa")
        data = self.client.get(f"/api/talks/{talk['talk_id']}").json()
        self.assertEqual(3, len(data["members"]))
        self.assertEqual("Разбор РРЛС-4",
                         self.client.get("/api/talks").json()["items"][0]["title"])

    def test_a_stranger_does_not_read_a_talk(self):
        # Переписка двоих — это переписка двоих, даже для начальника отдела.
        self.login("engineer")
        lead = self.repos.users.by_login("gruppa")
        talk = self.client.post("/api/talks", json={"members": [lead.id]}).json()
        self.login("nachalnik")
        self.assertEqual(404, self.client.get(f"/api/talks/{talk['talk_id']}").status_code)
        self.assertEqual(404, self.client.post(
            f"/api/talks/{talk['talk_id']}/messages", json={"text": "влезаю"}).status_code)

    def test_a_talk_with_nobody_is_refused(self):
        self.login("nachalnik")
        self.assertEqual(400, self.client.post("/api/talks", json={"members": []}).status_code)

    def test_an_empty_message_is_not_sent(self):
        self.login("nachalnik")
        talk = self.client.post("/api/talks", json={"members": [self.engineer.id]}).json()
        self.assertEqual(400, self.client.post(
            f"/api/talks/{talk['talk_id']}/messages", json={"text": "  "}).status_code)


class NameFormatTests(unittest.TestCase):
    """Полное ФИО в записи, фамилия с инициалами в списках."""

    def test_a_full_name_becomes_initials(self):
        from reportgen.store.models import short_name
        self.assertEqual("Жуков П. И.", short_name("Жуков Пётр Иванович"))
        self.assertEqual("Жуков П.", short_name("Жуков Пётр"))

    def test_an_already_short_name_stays_itself(self):
        """Правило применяется к тому, что уже лежит в базе.

        Записи, заведённые до полного ФИО, хранят «Жуков П. И.» — прогон их
        через сокращение не должен съедать точки и превращать имя в кашу.
        """
        from reportgen.store.models import short_name
        self.assertEqual("Жуков П. И.", short_name("Жуков П. И."))
        self.assertEqual("Жуков П. И.", short_name(short_name("Жуков Пётр Иванович")))
        # И строчный инициал приводится к прописному, а не остаётся как был.
        self.assertEqual("Жуков П. И.", short_name("Жуков п. и."))

    def test_a_double_surname_is_not_torn_into_initials(self):
        # «Римский-Корсаков» — одно слово и одна фамилия.
        from reportgen.store.models import short_name
        self.assertEqual("Римский-Корсаков Н. А.",
                         short_name("Римский-Корсаков Николай Андреевич"))

    def test_a_surname_alone_and_an_empty_name_survive(self):
        from reportgen.store.models import short_name
        self.assertEqual("Жуков", short_name("Жуков"))
        self.assertEqual("", short_name(""))
        self.assertEqual("", short_name(None))


class FullNameEntryTests(WebTestCase):
    """ФИО заводят целиком — сокращает система."""

    def test_a_new_record_keeps_the_name_whole(self):
        created = self.client.post("/api/users", json={
            "login": "vorobev", "full_name": "Воробьёв Игорь Семёнович",
            "password": "пароль12345", "role": "engineer"})
        self.assertEqual(200, created.status_code, created.text)
        user = created.json()["user"]
        self.assertEqual("Воробьёв Игорь Семёнович", user["full_name"])
        self.assertEqual("Воробьёв И. С.", user["short_name"])

    def test_one_surname_is_not_a_name(self):
        """Имя есть у всех, а восстановить его из «Жуков» потом не сможет никто.

        Полное имя нужно там, где документ подписывают человеком: справка,
        приказ, исходящее письмо.
        """
        refused = self.client.post("/api/users", json={
            "login": "odin", "full_name": "Одинов",
            "password": "пароль12345", "role": "engineer"})
        self.assertEqual(400, refused.status_code)
        self.assertIn("полностью", refused.json()["error"])

    def test_an_application_asks_for_the_whole_name_too(self):
        self.client.cookies.clear()
        refused = self.client.post("/api/auth/register", json={
            "login": "zayavka", "full_name": "Заявкин", "password": "пароль12345"})
        self.assertEqual(400, refused.status_code)
        ok = self.client.post("/api/auth/register", json={
            "login": "zayavka", "full_name": "Заявкин Заявка Заявкович",
            "password": "пароль12345"})
        self.assertEqual(200, ok.status_code, ok.text)

    def test_the_name_cannot_be_erased_down_to_a_surname_later(self):
        # Иначе полное имя стирают сразу после того, как его завели.
        user = self.repos.users.by_login("engineer")
        refused = self.client.patch(f"/api/users/{user.id}", json={"full_name": "Инженеров"})
        self.assertEqual(400, refused.status_code)

    def test_lists_show_initials_not_the_whole_name(self):
        """«Инженеров Иван Иванович» в столбце «Исполнитель» вытесняет то,
        ради чего в список смотрят."""
        person = self.repos.users.by_login("engineer")
        self.repos.users.update(person.id, full_name="Инженеров Иван Иванович")
        case = self.create_case()
        self.client.patch(f"/api/cases/{case['id']}", json={"assignee_id": person.id})

        listing = self.client.get("/api/cases").json()["items"]
        row = [item for item in listing if item["id"] == case["id"]][0]
        self.assertEqual("Инженеров И. И.", row["assignee_name"])

        board = self.client.get("/api/board").json()
        found = [item for item in board["people"] if item["id"] == person.id][0]
        self.assertEqual("Инженеров И. И.", found["full_name"])

        staff = self.client.get("/api/staff").json()["items"]
        found = [item for item in staff if item["id"] == person.id][0]
        self.assertEqual("Инженеров И. И.", found["full_name"])

    def test_a_talk_and_a_note_are_signed_with_initials(self):
        person = self.repos.users.by_login("engineer")
        self.repos.users.update(person.id, full_name="Инженеров Иван Иванович")
        case = self.create_case()
        self.login("engineer")
        self.client.post(f"/api/cases/{case['id']}/notes", json={"text": "Сверить ствол"})
        notes = self.client.get(f"/api/cases/{case['id']}/notes").json()["notes"]
        self.assertEqual("Инженеров И. И.", notes[0]["author"])

        boss = self.repos.users.by_login("nachalnik")
        talk = self.client.post("/api/talks", json={"members": [boss.id]}).json()
        self.client.post(f"/api/talks/{talk['talk_id']}/messages", json={"text": "Готово"})
        data = self.client.get(f"/api/talks/{talk['talk_id']}").json()
        self.assertEqual("Инженеров И. И.", data["messages"][0]["author"])
        self.assertIn("Инженеров И. И.",
                      [item["full_name"] for item in data["members"]])


class PersonCardTests(WebTestCase):
    """Карточка военнослужащего открыта всему отделу."""

    def setUp(self):
        super().setUp()
        self.person = self.repos.users.by_login("nachalnik")
        self.repos.users.update(self.person.id, full_name="Начальников Николай Николаевич",
                                team="1 группа", department="Отдел радиосвязи",
                                phone="+7 900 000-00-00", room="214")

    def test_any_member_of_the_department_may_open_it(self):
        """Спрашивать по коридору «а Титов — это кто?» новому человеку
        неудобно, а справочник для того и нужен."""
        self.login("engineer")
        response = self.client.get(f"/api/people/{self.person.id}")
        self.assertEqual(200, response.status_code, response.text)
        card = response.json()["person"]
        self.assertEqual("Начальников Николай Николаевич", card["full_name"])
        self.assertEqual("Начальников Н. Н.", card["short_name"])
        self.assertEqual("Начальник отдела", card["role_title"])
        self.assertEqual("1 группа", card["team"])
        self.assertEqual("Отдел радиосвязи", card["department"])
        self.assertEqual("214", card["room"])

    def test_the_card_says_where_the_person_is_today(self):
        self.repos.absences.add(self.person.id, "trip", _today_iso(), _today_iso(),
                                place="узел К-12")
        self.login("engineer")
        card = self.client.get(f"/api/people/{self.person.id}").json()["person"]
        self.assertEqual("trip", card["where"]["kind"])
        self.assertEqual("узел К-12", card["where"]["place"])

    def test_the_card_is_not_a_door_to_personal_documents(self):
        """Справка-объективка остаётся в своём круге.

        Карточка отвечает «кто это и как найти», а не «что о нём в кадрах».
        """
        self.login("engineer")
        card = self.client.get(f"/api/people/{self.person.id}").json()["person"]
        self.assertFalse(card["may_see_files"])
        self.assertEqual(403, self.client.get(
            f"/api/users/{self.person.id}/files").status_code)

    def test_a_stranger_without_a_login_gets_nothing(self):
        self.client.cookies.clear()
        self.assertEqual(401, self.client.get(f"/api/people/{self.person.id}").status_code)

    def test_an_unapproved_application_has_no_card(self):
        # До одобрения это не военнослужащий — и в справочнике его нет.
        self.client.cookies.clear()
        self.client.post("/api/auth/register", json={
            "login": "novichok", "full_name": "Новичков Новик Новикович",
            "password": "пароль12345"})
        pending = self.repos.users.by_login("novichok")
        self.login("engineer")
        self.assertEqual(404, self.client.get(f"/api/people/{pending.id}").status_code)


def _today_iso() -> str:
    from datetime import date
    return date.today().isoformat()


class TalkFileTests(WebTestCase):
    """Файлы в переписке: показать картинку, не заводя письма."""

    def setUp(self):
        super().setUp()
        self.boss = self.repos.users.by_login("nachalnik")
        self.login("engineer")
        self.talk = self.client.post(
            "/api/talks", json={"members": [self.boss.id]}).json()["talk_id"]

    def attach(self, name="skhema.txt", body=b"stvol 3", text=""):
        return self.client.post(
            f"/api/talks/{self.talk}/files",
            data={"text": text},
            files={"file": (name, body, "text/plain")})

    def test_a_file_comes_as_a_message(self):
        """Файл без сообщения повис бы в беседе без места и времени.

        Пустая строка в переписке без слов — это и есть «вот, смотри»,
        поэтому подпись необязательна, а сообщение обязательно.
        """
        response = self.attach()
        self.assertEqual(200, response.status_code, response.text)
        data = self.client.get(f"/api/talks/{self.talk}").json()
        self.assertEqual(1, len(data["messages"]))
        message = data["messages"][0]
        self.assertEqual("skhema.txt", message["text"])
        self.assertEqual(["skhema.txt"], [item["name"] for item in message["files"]])

    def test_a_caption_replaces_the_file_name(self):
        self.attach(text="Глянь, это тот же ствол?")
        message = self.client.get(f"/api/talks/{self.talk}").json()["messages"][0]
        self.assertEqual("Глянь, это тот же ствол?", message["text"])
        self.assertEqual(1, len(message["files"]))

    def test_the_other_side_is_told_about_the_file(self):
        self.attach()
        self.login("nachalnik")
        notices = self.client.get("/api/notifications").json()["items"]
        self.assertIn("Файл от", notices[0]["title"])
        self.assertEqual("skhema.txt", notices[0]["body"])

    def test_the_file_is_read_back_by_a_member(self):
        item = self.attach().json()["file"]
        got = self.client.get(f"/api/talks/{self.talk}/files/{item['id']}")
        self.assertEqual(200, got.status_code)
        self.assertEqual(b"stvol 3", got.content)

    def test_a_stranger_reads_neither_the_talk_nor_its_files(self):
        # Переписка двоих — это переписка двоих, и приложенное к ней тоже.
        item = self.attach().json()["file"]
        self.login("gruppa")
        self.assertEqual(404, self.client.get(
            f"/api/talks/{self.talk}/files/{item['id']}").status_code)
        self.assertEqual(404, self.client.post(
            f"/api/talks/{self.talk}/files",
            files={"file": ("chuzhoy.txt", b"...", "text/plain")}).status_code)

    def test_an_unknown_kind_of_file_is_refused(self):
        refused = self.attach(name="virus.exe", body=b"MZ")
        self.assertEqual(400, refused.status_code)
        self.assertIn("не пересылают", refused.json()["error"])

    def test_an_empty_file_leaves_nothing_behind(self):
        refused = self.attach(body=b"")
        self.assertEqual(400, refused.status_code)
        self.assertEqual([], self.client.get(
            f"/api/talks/{self.talk}").json()["messages"])


class LoginBackgroundTests(WebTestCase):
    """Фон окна входа: кадр из поставки и своя фотография вместо него."""

    def test_default_image_is_served(self):
        # Инженер просил не рисованную заставку. Кадр лежит в поставке, чтобы
        # изолированная машина ничего не скачивала.
        response = self.client.get("/brand/login-image")
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.headers["content-type"].startswith("image/"))
        self.assertGreater(len(response.content), 20_000, "кадр подозрительно мал")

    def test_own_photo_wins(self):
        own = self.tmp / "login-bg.jpg"
        own.write_bytes(b"\xff\xd8\xff" + "своя фотография".encode("utf-8") * 100)
        self.app.state.settings.brand_login_image = own
        response = self.client.get("/brand/login-image")
        self.assertEqual(200, response.status_code)
        self.assertIn("своя фотография".encode("utf-8"), response.content)

    def test_photo_is_found_next_to_the_settings_file(self):
        # Путь у всех разный, а имя файла одно: в настройки лезть не надо.
        import json
        import tempfile
        from pathlib import Path

        from reportgen.config import Settings

        folder = Path(tempfile.mkdtemp())
        (folder / "settings.json").write_text(json.dumps({"port": 8080}), encoding="utf-8")
        (folder / "login-bg.jpg").write_bytes(b"\xff\xd8\xff")
        settings = Settings.load(folder / "settings.json")
        self.assertEqual(folder / "login-bg.jpg", settings.brand_login_image)

    def test_login_page_does_not_probe_for_a_missing_image(self):
        # Страница просит один адрес, который всегда отвечает: иначе в
        # консоли браузера на каждой загрузке краснела строка про 404.
        page = (ROOT / "src" / "reportgen" / "web" / "static" / "login.html").read_text(
            encoding="utf-8")
        self.assertIn('src="/brand/login-image"', page)
        self.assertNotIn("new Image()", page)


class LineTypeTests(WebTestCase):
    """Линия связи принимается и кодом, и названием."""

    def test_the_title_is_accepted_as_well_as_the_code(self):
        # Отказ звучал издевательски: «неизвестная линия связи 'РРЛС'
        # (известны: СЛС, РРЛС, КВ, Другое)» — название и было в списке.
        response = self.client.post("/api/cases", json={
            "case_id": "2026-0001", "title": "проба",
            "report_type": CASE["report_type"], "line_type": "РРЛС"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual("rrls", response.json()["case"]["line_type"])

    def test_the_case_of_the_title_does_not_matter(self):
        response = self.client.post("/api/cases", json={
            "case_id": "2026-0002", "title": "проба",
            "report_type": CASE["report_type"], "line_type": "слс"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual("sls", response.json()["case"]["line_type"])

    def test_an_unknown_line_names_both_the_code_and_the_title(self):
        response = self.client.post("/api/cases", json={
            "case_id": "2026-0003", "title": "проба",
            "report_type": CASE["report_type"], "line_type": "тропосферная"})
        self.assertEqual(response.status_code, 400)
        message = response.json()["error"]
        self.assertIn("rrls (РРЛС)", message)
        self.assertIn("sls (СЛС)", message)


class RepeatingSectionCaseTests(WebTestCase):
    """Письмо с описью: по разделу отчёта на каждую регистрацию."""

    OPIS = json.loads(
        (ROOT / "examples" / "cases" / "opis-signals-2026.json").read_text(encoding="utf-8"))

    def test_a_report_gets_a_section_per_registration(self):
        case = self.create_case(self.OPIS)
        report = self.generate(case["id"])
        titles = [section["title"] for section in report["sections"]]
        # Исходные данные + шесть регистраций + выводы.
        self.assertEqual(8, len(titles))
        self.assertIn("Файл в каталоге «7745_34000_QAM-256»", titles)
        # Один каталог — «Файл в каталоге», несколько — «Файлы в каталогах»:
        # так написано и в настоящем ответе отдела.
        self.assertIn("Файлы в каталогах «17919_26000_QAM-16_H», "
                      "«18737_26000_QAM-16_H», «18737_26000_QAM-16_V»", titles)
        self.assertIn("Файл в каталоге «62_memoteck»", titles)

    def test_an_empty_list_is_reported_as_missing_data(self):
        """Пустая опись — нехватка данных, а не молчаливо пустой отчёт."""
        facts = json.loads(json.dumps(self.OPIS))
        facts["registrations"] = []
        case = self.create_case(facts)
        coverage = self.client.get(f"/api/cases/{case['id']}").json()["coverage"]
        self.assertIn("__list__:registrations", coverage)

    def test_a_record_without_required_fields_is_named_in_the_coverage(self):
        facts = json.loads(json.dumps(self.OPIS))
        del facts["registrations"][2]["modulation"]
        case = self.create_case(facts)
        coverage = self.client.get(f"/api/cases/{case['id']}").json()["coverage"]
        gaps = [key for keys in coverage.values() for key in keys]
        self.assertIn("registrations[3].modulation", gaps)

    def test_the_list_survives_a_save_from_the_screen(self):
        """Экран данных шлёт факт-пакет целиком — опись обязана вернуться."""
        case = self.create_case(self.OPIS)
        facts = self.client.get(f"/api/cases/{case['id']}").json()["case"]["facts"]
        facts["registrations"][0]["equipment"] = "Ericsson MiniLink (Швеция)"
        response = self.client.put(f"/api/cases/{case['id']}/facts", json={"facts": facts})
        self.assertEqual(response.status_code, 200, response.text)
        again = self.client.get(f"/api/cases/{case['id']}").json()["case"]["facts"]
        self.assertEqual(6, len(again["registrations"]))
        self.assertEqual("Ericsson MiniLink (Швеция)",
                         again["registrations"][0]["equipment"])


class RegistrationEditorTests(unittest.TestCase):
    """Опись регистраций правится в самом приложении.

    Раздел отчёта повторяется по описи, но заносить опись было негде: список
    жил только в файле факт-пакета, а в приложении его не показывали вовсе —
    повторяющиеся разделы были недостижимы для инженера.
    """

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")

    def test_the_template_tells_the_screen_what_repeats(self):
        from reportgen.pipeline import Outline

        outline = Outline.load(ROOT / "templates" / "outline_signal_analysis.json")
        section = next(spec for spec in outline.sections if spec.repeat_over)
        self.assertEqual("registrations", section.repeat_over)
        # Экран данных читает то же самое из /api/config.
        self.assertIn('"repeat_over": spec.repeat_over', self.api_source())
        self.assertIn('"item_required": list(spec.item_required)', self.api_source())
        self.assertIn('"item_title": spec.item_title', self.api_source())

    def api_source(self):
        return (ROOT / "src" / "reportgen" / "web" / "api.py").read_text(encoding="utf-8")

    def test_the_screen_has_a_place_for_the_list(self):
        self.assertIn("registrationsSection", self.js)
        self.assertIn("'+ регистрация'", self.js)
        self.assertIn("Опись, по которой пришли сигналы", self.js)
        self.assertIn(".reg-card {", self.css)

    def test_a_new_record_comes_with_the_fields_the_template_wants(self):
        # Пустая карточка заставляла бы угадывать имена полей.
        self.assertIn("function addRegistration", self.js)
        self.assertIn("itemTitleKeys().concat(itemRequired())", self.js)

    def test_the_order_of_records_can_be_changed(self):
        # Порядок карточек — порядок разделов в письме.
        self.assertIn("function moveRegistration", self.js)
        self.assertIn("Поднять выше", self.js)
        self.assertIn("Опустить ниже", self.js)

    def test_a_required_field_cannot_be_renamed(self):
        self.assertIn("Поле требуется шаблоном отчёта — имя менять нельзя", self.js)

    def test_the_list_is_read_and_written_back(self):
        self.assertIn("wb.regs = list.map", self.js)
        self.assertIn("facts[listName] = (wb.regs || []).map", self.js)

    def test_an_empty_record_does_not_paint_the_measurements_table_red(self):
        """Поле записи и строка измерений могут называться одинаково."""
        self.assertIn("entry.kind !== 'reg'", self.js)


class ResearchTrailTests(unittest.TestCase):
    """Ход разбора виден инженеру, а не только модели."""

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")

    def test_the_steps_reach_the_screen(self):
        # Ответ «в библиотеке этого нет» без списка запросов не проверить.
        self.assertIn("event.type === 'step'", self.js)
        self.assertIn("chat.pendingSteps", self.js)
        self.assertIn("'Ход разбора:'", self.js)
        self.assertIn(".chat-steps {", self.css)

    def test_the_steps_are_dropped_together_with_the_rest(self):
        # Шаги прошлого ответа не должны висеть над следующим.
        cleanup = self.js.split("chat.pendingWarning = null;")[1][:200]
        self.assertIn("chat.pendingSteps = null;", cleanup)


class VectorCardTests(unittest.TestCase):
    """Состояние смыслового поиска — карточкой, а не серой строчкой."""

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")

    def test_the_library_shows_the_state_of_the_search(self):
        self.assertIn("function renderVectors", self.js)
        self.assertIn("'Смысловой поиск'", self.js)
        self.assertIn(".vectors-card.is-bad {", self.css)

    def test_work_in_progress_wins_over_the_result(self):
        """При перестройке заново векторы на месте, а поиск — в разобранном виде.

        Карточка светилась зелёным «работает по всей библиотеке» ровно тогда,
        когда указатель переписывался наполовину.
        """
        self.assertIn("(busy ? ' is-busy' : (state.ready ? ' is-ok' : ' is-bad'))", self.js)

    def test_a_full_rebuild_asks_first(self):
        # Часы работы видеокарты по нечаянному нажатию — не то, что можно
        # отменить.
        self.assertIn("Построить векторы заново", self.js)
        self.assertIn("часы работы видеокарты", self.js)

    def test_the_progress_is_polled_while_it_works(self):
        self.assertIn("libState.vectorsTimer", self.js)
        self.assertIn(".vectors-bar-fill", self.css)

    def test_the_card_says_what_to_do_and_not_only_what_broke(self):
        """«Сервер эмбеддингов недоступен» — это диагноз, а не действие.

        Человек сидит за той же машиной, где всё стоит, и ему нужна команда.
        """
        self.assertIn("state.advice", self.js)
        self.assertIn(".vectors-advice {", self.css)

    def test_the_connection_can_be_checked_without_a_build(self):
        # Иначе узнать, поднялась ли служба, можно было единственным
        # способом: занять видеокарту на полчаса и подождать неудачи.
        self.assertIn("'Проверить связь'", self.js)
        self.assertIn("/api/library/vectors/check", self.js)

    def test_filling_the_library_is_the_chiefs_business(self):
        # Кнопки, которые всё равно ответят «нет прав», показывать незачем.
        self.assertIn("isAdmin() ? h('div', { class: 'card card-pad' },", self.js)
        self.assertIn("}, 'Прочитать каталог') : null)),", self.js)


class TalkFileTests(unittest.TestCase):
    """Вложение в беседе щелчком СКАЧИВАЕТСЯ, а не пытается открыться."""

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")

    def test_a_click_downloads_the_file(self):
        """В переписке ходят чертежи, таблицы и архивы.

        Половину из них показать в браузере нечем, и человек вместо файла
        получал окно с ошибкой. В беседе файл посылают, чтобы открыть его
        своей программой.
        """
        row = self.js.split("function talkFileRow(item)", 1)[1].split("\n    }", 1)[0]
        self.assertIn("download: item.name", row)
        self.assertIn("'Скачать '", row)
        self.assertNotIn("openFilePreview", row)

    def test_a_photo_is_shown_right_in_the_conversation(self):
        """Снимок посылают, чтобы на него посмотрели.

        Плашка с именем файла вместо картинки заставляла скачивать и
        открывать её своей программой ради одного взгляда.
        """
        row = self.js.split("function talkFileRow(item)", 1)[1].split("\n    }", 1)[0]
        self.assertIn("PREVIEW_IMAGE.indexOf(fileExt(item.name))", row)
        self.assertIn("'talk-photo'", row)
        self.assertIn("openPhoto(item, href)", row)

    def test_the_photo_opens_full_size_and_can_be_saved(self):
        self.assertIn("function openPhoto(item, href)", self.js)
        photo = self.js.split("function openPhoto(item, href)", 1)[1].split("\n    }", 1)[0]
        self.assertIn("'Скачать'", photo)
        css = (ROOT / "src" / "reportgen" / "web" / "static"
               / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".talk-photo img {", css)
        self.assertIn(".photo-view img {", css)


class LastSeenTests(WebTestCase):
    """«Писать ему сейчас или он прочтёт завтра» — обычный вопрос отдела."""

    def test_the_card_says_when_the_person_was_last_seen(self):
        self.login("engineer")
        person = self.repos.users.by_login("engineer")
        card = self.client.get(f"/api/people/{person.id}").json()["person"]
        self.assertTrue(card["last_seen_at"], "отметка не проставилась при входе")

    def test_the_mark_is_not_written_on_every_click(self):
        """Иначе запись в базу приходилась бы на каждый щелчок в интерфейсе."""
        person = self.repos.users.by_login("engineer")
        self.assertTrue(self.repos.users.mark_seen(person))
        self.assertFalse(self.repos.users.mark_seen(person))

    def test_an_old_mark_is_refreshed(self):
        person = self.repos.users.by_login("engineer")
        self.repos.users.mark_seen(person, now="2020-01-01T00:00:00+00:00")
        self.assertTrue(self.repos.users.mark_seen(person))
        fresh = self.repos.users.by_login("engineer")
        self.assertNotEqual("2020-01-01T00:00:00+00:00", fresh.last_seen_at)

    def test_the_directory_carries_it_too(self):
        staff = self.client.get("/api/staff").json()["items"]
        self.assertTrue(all("last_seen_at" in item for item in staff))


class LibraryIsForTheChiefTests(WebTestCase):
    """Библиотека — общее хозяйство: правит её начальство.

    Документ ложится в один поиск на весь отдел. Неверно указанный тип или
    направление меняют выдачу всем, а разобрать это обратно можно только
    руками — по одному документу из тринадцати тысяч. Читают и ищут по
    библиотеке все, это не меняется.
    """

    def test_an_engineer_may_read_and_search_the_library(self):
        self.login("engineer")
        self.assertEqual(200, self.client.get("/api/library").status_code)
        self.assertEqual(200, self.client.get("/api/search?q=полоса").status_code)

    def test_an_engineer_may_not_add_documents(self):
        self.login("engineer")
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("proba.md", b"# Proba\n\nTekst.", "text/markdown")},
            data={"doc_type": "literature", "domain": ""})
        self.assertEqual(403, response.status_code, response.text)

    def test_an_engineer_may_not_reindex_or_build_vectors(self):
        self.login("engineer")
        for path, payload in (("/api/library/reindex", {}),
                              ("/api/library/vectors", {}),
                              ("/api/library/vectors/check", {})):
            with self.subTest(path=path):
                self.assertEqual(403, self.client.post(path, json=payload).status_code)

    def test_the_chief_may(self):
        self.login("nachalnik")
        response = self.client.post(
            "/api/library/upload",
            files={"file": ("proba.md", b"# Proba\n\nTekst otdela.", "text/markdown")},
            data={"doc_type": "literature", "domain": ""})
        self.assertEqual(200, response.status_code, response.text)


class VectorTroubleTests(WebTestCase):
    """Связь со службой проверяют кнопкой, а не получасом построения."""

    def test_the_check_says_what_is_wrong_and_what_to_do(self):
        self.login("admin")
        self.service.settings.embed_enabled = True
        self.service.settings.embed_base_url = "http://127.0.0.1:8/v1"
        self.service.settings.embed_timeout = 1
        self.service.vectors.settings = self.service.settings
        answer = self.client.post("/api/library/vectors/check", json={}).json()["check"]
        self.assertFalse(answer["ok"])
        # Не только «не работает», но и что нажать.
        self.assertIn("start-embed.ps1", answer["advice"])

    def test_a_switched_off_search_says_so_plainly(self):
        self.login("admin")
        self.service.settings.embed_enabled = False
        answer = self.client.post("/api/library/vectors/check", json={}).json()["check"]
        self.assertFalse(answer["ok"])
        self.assertIn("embed_enabled", answer["advice"])


class LibraryScreenTests(unittest.TestCase):
    """Экран библиотеки при тринадцати тысячах документов."""

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")

    def test_the_table_is_drawn_by_the_page(self):
        # Тринадцать тысяч строк в одной таблице браузер рисует минуту.
        self.assertIn("'page=' + libState.page", self.js)
        self.assertIn("function renderPager()", self.js)
        self.assertIn(".pager {", self.css)

    def test_the_page_says_how_much_of_the_whole_is_shown(self):
        # Без этих чисел человек не знает, всё ли перед ним.
        self.assertIn("' из ' + libState.total", self.js)
        self.assertIn("'страница ' + libState.page + ' из ' + libState.pages", self.js)

    def test_a_document_is_found_by_name(self):
        """Листать пятьюдесятью страницами по тринадцати тысячам никто не станет."""
        self.assertIn("Поиск по названию документа", self.js)
        self.assertIn("q=' + encodeURIComponent(libState.query)", self.js)

    def test_nothing_found_is_not_the_same_as_an_empty_library(self):
        self.assertIn("'Ничего не нашлось'", self.js)

    def test_a_badly_read_file_is_marked_in_the_list(self):
        """Документ есть, фрагментов много, а найти в нём нельзя ничего."""
        self.assertIn("item.text_quality === 'glued'", self.js)
        self.assertIn("'текст склеен'", self.js)

    def test_an_upload_says_what_actually_happened(self):
        # «Готово, фрагментов: 231» не отвечало на вопрос, заменил ли файл
        # прежний документ и прочитался ли текст целиком.
        self.assertIn("function showUploadResult(", self.js)
        self.assertIn("'заменил прежний · фрагментов: '", self.js)
        self.assertIn("'добавлен · фрагментов: '", self.js)
        self.assertIn("'посмотреть, что прочитано'", self.js)
        self.assertIn(".upload-remarks {", self.css)


class QuietScreensTests(unittest.TestCase):
    """Экран показывает работу, а не рассказывает о себе.

    Под заголовком раздела стояла строка, пересказывающая заголовок; над
    списком отдела — шесть карточек с абзацами прав; в карточке письма одно
    и то же объяснение шло дважды. Инженер приходит сюда работать, а не
    читать: служебные подробности убраны под свёрнутую строку, пересказы
    сняты совсем.
    """

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")

    def test_a_section_does_not_retell_its_own_title(self):
        for retelling in (
            "Входящие письма и подготовленные ответы",
            "Что в отделе происходит сегодня",
            "Объём работы, качество черновиков и состояние библиотеки",
            "Личный состав отдела, должности и доступ",
            "Переписка отдела: личная и на несколько человек",
        ):
            with self.subTest(retelling=retelling):
                self.assertNotIn(retelling, self.js)

    def test_two_sections_whose_names_say_too_little_keep_their_line(self):
        """«Расход» — слово отдела, а не общепонятное.

        Строка под таким заголовком — не пересказ, а ответ на «что здесь
        вообще?». То же у личного кабинета: под ним лежит больше, чем
        обещает название, — работа, документы и пароль, а не только
        учётная запись.
        """
        self.assertIn("'Кто где и чем занят: дежурство, работы, выезды, отсутствия'",
                      self.js)
        self.assertIn("'Ваши данные, работа, документы и пароль'", self.js)

    def test_the_numbers_line_of_the_library_stays(self):
        # Убирали пересказы, а не данные: «документов: 5 · фрагментов: 21» —
        # это ответ на вопрос, а не подпись к заголовку.
        self.assertIn("const statsLine = h('div', { class: 'page-note' });", self.js)

    def test_who_may_do_what_is_folded_away(self):
        """Права читают при заведении военнослужащего — то есть изредка."""
        self.assertIn("'Какие бывают должности и что они дают'", self.js)
        self.assertIn("h('details', { class: 'fold' }", self.js)
        # Развёрнутого заголовка карточки больше нет: список отдела начинается
        # сразу под кнопками.
        self.assertNotIn("'Должности и права'", self.js)

    def test_your_own_powers_are_folded_too(self):
        self.assertIn("'Что вам доступно'", self.js)
        self.assertNotIn("h('dt', {}, 'Права')", self.js)

    def test_a_folded_line_shows_that_it_opens(self):
        # Свёрнутая строка без значка неотличима от обычной подписи.
        self.assertIn(".fold > summary::before {", self.css)
        self.assertIn(".fold[open] > summary::before {", self.css)

    def test_the_same_thing_is_not_explained_twice_on_a_case(self):
        """Карточка «Не хватает данных» стоит вплотную к самим полям.

        Полоса шагов пересказывала её же другими словами — и обе строки
        читались как два разных требования.
        """
        self.assertNotIn("Шаблон отчёта требует данных", self.js)
        self.assertIn("'Не хватает значений: '", self.js)
        self.assertIn("'Щёлкните — строка добавится в таблицу.'", self.js)

    def test_a_department_name_is_not_cut_in_the_staff_list(self):
        """«Отдел радиосвязи» в поле шириной 77 px — это «Отдел р».

        Два столбца — подразделение и группа — съедали 300 px, которых у
        таблицы нет: браузер ужимал их под содержимое соседей, и оба
        значения читались огрызками. Свели в один столбец в две строки,
        ширину закрепили: ширина без min-width — только пожелание.
        """
        self.assertIn("h('th', {}, 'По штату и группа')", self.js)
        self.assertIn("h('td', { class: 'cell-stack' }, depInput, teamInput)", self.js)
        self.assertIn("min-width: 185px;", self.css)
        # И строка таблицы от второй строки в клетке не раздувается:
        # двенадцать человек отдела должны быть видны разом.
        self.assertIn("table.grid--users td.cell-stack input {", self.css)

    def test_a_section_is_not_defined_under_its_own_title(self):
        """«Что обнаружено» не нуждается в подписи «отклонения, которые нашли».

        А вот правило, по которому работает вся система, — нуждается, и
        оно осталось: в отчёт идут только занесённые сюда числа.
        """
        self.assertNotIn("Отклонения, которые нашли при проверке", self.js)
        self.assertNotIn("Числа, полученные при проверке", self.js)
        self.assertIn("'Других чисел в отчёте не будет.'", self.js)

    def test_the_hour_a_person_was_added_is_not_a_column(self):
        # Минута, в которую человека внесли в систему, не нужна никому —
        # а столбец под неё отбирал место у подразделения.
        self.assertIn("}, fmtDate(user.created_at)),", self.js)
        self.assertIn("title: fmtDateTime(user.created_at),", self.js)


class InterfaceCopyTests(unittest.TestCase):
    """Подписи интерфейса: без жаргона и без разговоров ни о чём."""

    def setUp(self):
        static = ROOT / "src" / "reportgen" / "web" / "static"
        self.js = (static / "app.js").read_text(encoding="utf-8")
        self.html = (static / "index.html").read_text(encoding="utf-8")
        self.css = (static / "styles.css").read_text(encoding="utf-8")
        self.login = (static / "login.html").read_text(encoding="utf-8")

    def test_editing_a_signed_report_warns_before_removing_the_signature(self):
        # Инженер мог открыть раздел, чтобы просто перечитать. Молча снимать
        # подпись с отправленного отчёта нельзя.
        self.assertIn("confirmUnsign", self.js)
        self.assertIn("Правка снимет подпись", self.js)
        self.assertIn("Править и снять подпись", self.js)

    def test_handed_in_report_is_shown_and_not_only_offered_for_download(self):
        """Начальник открывает отчёт, чтобы прочитать его, а не скачать.

        Сданный файлом отчёт показывал одно имя файла и кнопку: чтобы
        прочитать, начальнику надо было скачать документ и найти, чем его
        открыть, — на каждом отчёте отдела. Текст извлекается при сдаче,
        значит, его надо показать, честно сказав, что подлинник — файл.
        """
        self.assertIn("function uploadedReportView(report)", self.js)
        self.assertIn("renderMarkdown(text)", self.js)
        self.assertIn("Так система прочитала сданный файл", self.js)
        self.assertNotIn("Разделов у него нет", self.js)

    def test_a_notice_does_not_wait_twenty_seconds(self):
        """Сообщение написали — уведомление приходило через полминуты.

        Опрос стоял раз в двадцать секунд, и человек справедливо считал, что
        уведомления «не приходят сразу». Запрос дешёвый: выборка своих
        уведомлений по одному индексу.
        """
        self.assertIn("const NOTICE_POLL_MS = 5000;", self.js)
        self.assertNotIn("const NOTICE_POLL_MS = 20000;", self.js)
        # И спрашиваем сразу, как человек вернулся к окну: он мог отойти
        # на час, и ждать ещё пять секунд ради случившегося незачем.
        self.assertIn("visibilitychange", self.js)

    def test_a_hidden_window_is_asked_less_often(self):
        """Свёрнутой вкладке браузер всё равно тормозит таймеры."""
        self.assertIn("const NOTICE_POLL_HIDDEN_MS = 30000;", self.js)

    def test_the_sound_has_a_volume_and_it_is_loud_by_default(self):
        """Сигнал звучал на одной десятой мощности — в аппаратной не слышно."""
        self.assertIn("function soundLevel()", self.js)
        self.assertIn("rg-notice-volume", self.js)
        self.assertIn("const SOUND_LOUDEST = 0.9;", self.js)
        # Прежний потолок громкости остаться не должен.
        self.assertNotIn("exponentialRampToValueAtTime(0.14", self.js)

    def test_a_minimised_window_gets_a_desktop_notice(self):
        """Свёрнутое окно карточку над колокольчиком не покажет."""
        self.assertIn("function showDesk(item)", self.js)
        self.assertIn("window.Notification", self.js)
        # Показываем только когда окно не на виду: иначе два уведомления
        # об одном — на экране и рядом с ним.
        self.assertIn("if (!deskOn() || !document.hidden) return;", self.js)

    def test_the_department_is_told_when_desktop_notices_are_impossible(self):
        """Браузер показывает их только по https или на самой машине.

        Отдел работает по http на адрес в сети — на клиентских местах такого
        окна нет вовсе, и делать вид, что настройка работает, нельзя. Но и
        отправлять человека что-то устанавливать нельзя тоже: на рабочих
        местах это запрещено. Значит, надо сказать, что срочное найдёт его и
        без этого.
        """
        self.assertIn("function deskAllowed()", self.js)
        self.assertIn("Окно Windows поверх других: здесь недоступно", self.js)
        self.assertIn("Ставить на эту машину тоже ничего не", self.js)
        self.assertIn("искать не нужно", self.js)
        self.assertIn("ВЫЗОВ", self.js)

    def test_the_tab_title_counts_what_is_unread(self):
        """Работает при любом браузере и любом адресе — видно в панели задач."""
        self.assertIn("function paintTitle(count)", self.js)
        self.assertIn("paintTitle(notices.unseen)", self.js)

    def test_the_login_page_lets_a_newcomer_apply(self):
        """Заводить каждого руками в отделе некому.

        Окно входа — единственное место, куда попадает человек без доступа;
        если заявку подать неоткуда, её и не подадут.
        """
        self.assertIn('id="signup-form"', self.login)
        self.assertIn("/api/auth/register", self.login)
        self.assertIn("Подать заявку", self.login)
        # Должность в заявке не спрашиваем: её назначает тот, кто одобряет.
        self.assertNotIn('id="signup-role"', self.login)
        # И честно говорим, что будет дальше.
        self.assertIn("Доступ откроет начальник отдела", self.login)

    def test_the_login_page_stays_on_its_own(self):
        # Страница входа не подключает внешних сценариев: её должно быть
        # видно даже при ошибке в основном приложении.
        self.assertNotIn("<script src", self.login)

    def test_applications_are_shown_where_people_are_managed(self):
        # Заявка, о которой забыли, — это человек, который не может работать.
        self.assertIn("/api/users/pending", self.js)
        # Именно подписью на экране, а не словами в комментарии рядом.
        self.assertIn("'Заявки на доступ',", self.js)
        self.assertIn("до этого войти он не может", self.js)
        self.assertIn("/approve", self.js)
        self.assertIn("/reject", self.js)

    def test_the_alert_sound_survives_the_browsers_autoplay_rule(self):
        """Свежий AudioContext стоит приостановленным до касания страницы.

        Всё, что в него расписали до этого, уходит в тишину: вызов в кабинет
        приходил бы молча — то есть ровно так, как если бы звука не было
        вовсе. Контекст поэтому один на сеанс, будится первым же касанием и
        ещё раз перед самим сигналом.
        """
        self.assertIn("function wakeAudio()", self.js)
        self.assertIn("audioCtx.state === 'suspended'", self.js)
        self.assertIn("audioCtx.resume()", self.js)
        self.assertIn("['pointerdown', 'keydown'].forEach", self.js)
        # И контекст не закрывается после сигнала: закрытый не разбудить.
        self.assertNotIn("ctx.close()", self.js)

    def test_a_person_can_be_called_to_the_office(self):
        self.assertIn("/api/notifications/call", self.js)
        self.assertIn("Вызвать в кабинет", self.js)
        self.assertIn("уведомление со звуком", self.js)

    def test_the_letter_screen_holds_the_talk_about_the_letter(self):
        # Разговор у стола через неделю не вспомнить, а вопрос «что именно
        # не так» по возвращённой бумаге возникает всегда.
        self.assertIn("function renderCaseNotes(body)", self.js)
        self.assertIn("/notes", self.js)
        self.assertIn("Примечания (", self.js)
        self.assertIn("Увидит исполнитель и автор письма", self.js)

    def test_attachments_are_split_into_the_letter_and_the_answer(self):
        # В одной куче исполнитель через полгода не отличит скан входящего
        # от приложения к своему ответу.
        self.assertIn("Приложено к письму", self.js)
        self.assertIn("Ушло с ответом", self.js)
        # Обе стопки набираются перебором: пустая вторая — это та же куча.
        self.assertIn("items.filter((file) => file.stage !== 'outgoing')", self.js)
        self.assertIn("items.filter((file) => file.stage === 'outgoing')", self.js)

    def test_messages_are_a_section_of_their_own(self):
        self.assertIn("async function renderTalks(view, talkId, writeTo)", self.js)
        self.assertIn("title: 'Сообщения'", self.js)
        self.assertIn("/api/talks", self.js)
        # Личная и на несколько человек — обе.
        self.assertIn("Личная переписка с одним человеком", self.js)
        self.assertIn("Беседа на ", self.js)

    def test_the_talk_poll_is_stopped_when_the_screen_is_left(self):
        """Опрос, который никто не гасит, живёт до перезагрузки страницы.

        Уйдя в «Письма», человек продолжал бы дёргать сервер каждые десять
        секунд — и так на каждом заходе в «Сообщения».
        """
        self.assertIn("function stopTalkPoll()", self.js)
        start = self.js.index("async function renderRoute(route)")
        head = self.js[start:start + 600]
        self.assertIn("stopTalkPoll();", head)

    def test_notifications_live_in_the_corner_and_not_in_the_header(self):
        """Значок в шапке соперничал за внимание с названием раздела.

        Нужен он ровно между делами, а смотрят между делами в правый нижний
        угол. Заодно исчезло модальное окно: список уведомлений — не то, ради
        чего затемняют экран.
        """
        self.assertIn('id="dock"', self.html)
        self.assertIn('id="notice-panel"', self.html)
        # Колокольчик ушёл из шапки: в ней его больше нет.
        head = self.html[self.html.index('<header class="topbar">'):
                         self.html.index('</header>')]
        self.assertNotIn('id="bell"', head)
        self.assertIn('id="bell"', self.html)
        self.assertIn("function toggleNotices(", self.js)
        self.assertNotIn("function openNotices(", self.js)

    def test_the_notice_panel_closes_the_way_popups_do(self):
        # Щелчком мимо и Escape — так ведёт себя всё всплывающее, и рука
        # это уже знает.
        self.assertIn("function outsideNotices(", self.js)
        self.assertIn("function escNotices(", self.js)
        self.assertIn("event.key === 'Escape'", self.js)

    def test_an_arriving_notice_shows_itself_without_stopping_the_work(self):
        # Перебивать работу окном ради «письмо передано вам» нельзя, а
        # промолчать — значит заставить открывать список наугад.
        self.assertIn("function liveNotice(item)", self.js)
        self.assertIn("item.loud ? 15000 : 8000", self.js)

    def test_the_corner_is_freed_where_the_screen_has_its_own_button(self):
        """Иначе «Отправить» в переписке оказывается под колокольчиком.

        Кнопка отправки стоит ровно в том углу, который занял значок
        уведомлений, и нажать её было нельзя.
        """
        self.assertIn("body.has-dock .talk-send,\nbody.has-dock .composer {\n"
                      "    padding-right: 70px;", self.css)
        self.assertIn("body.has-dock #toasts {\n    bottom: 78px;", self.css)

    def test_one_line_finds_a_letter_a_person_and_a_talk(self):
        """Путь «Письма → набор → поиск → строка» человек проходит по
        двадцать раз в день. Ctrl+K — общепринятое сочетание."""
        self.assertIn("function openPalette()", self.js)
        self.assertIn("async function paletteSearch(text)", self.js)
        self.assertIn("event.key.toLowerCase() === 'k'", self.js)
        self.assertIn('id="cmd-open"', self.html)
        # Сочетание, о котором никто не знает, всё равно что его нет.
        self.assertIn('<kbd>Ctrl K</kbd>', self.html)
        # Ищем теми же маршрутами, что и разделы: своего второго поиска нет.
        self.assertIn("'/api/cases?q=' + encodeURIComponent(query)", self.js)

    def test_the_palette_does_not_ask_the_server_on_every_letter(self):
        # Набирающий «ВХ-2026-0423» иначе шлёт двенадцать запросов подряд.
        self.assertIn("const CMD_DELAY_MS", self.js)
        self.assertIn("cmd.timer = setTimeout(", self.js)
        # И ответ на устаревший запрос не должен перебить свежий.
        self.assertIn("if (mine !== cmd.seq", self.js)

    def test_the_palette_is_walked_by_the_keyboard(self):
        self.assertIn("if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {",
                      self.js)
        self.assertIn("if (event.key === 'Enter' && cmd.items[cmd.cursor])", self.js)
        self.assertIn("scrollIntoView", self.js)

    def test_a_person_found_in_the_palette_is_opened_by_writing_to_them(self):
        # Человека ищут, чтобы ему написать: личный состав и так рядом.
        self.assertIn("'#/talks?to=' + person.id", self.js)
        self.assertIn("async function openTalkWith(userId)", self.js)

    def test_choosing_whom_to_write_to_has_a_search(self):
        # Отдел на десяток человек ищут глазами, на полсотни — уже нет.
        self.assertIn("'Фамилия или логин'", self.js)
        self.assertIn("function paintPeople()", self.js)
        self.assertIn("function paintChosen()", self.js)
        # Двое — личная переписка, больше — беседа, и ей нужно имя.
        self.assertIn("titleField.hidden = !many", self.js)

    def test_loading_keeps_the_shape_of_the_screen(self):
        """Кружок говорит «жди», но не говорит, чего именно.

        Экран прыгает, когда данные приходят: заглушка держит ту же
        разметку, что и готовый экран.
        """
        self.assertIn("function skeleton(rows, kind)", self.js)
        self.assertIn("skeleton(6, 'page')", self.js)
        self.assertIn(".sk-line", self.css)

    def test_movement_is_dropped_for_those_who_asked(self):
        # Кому движение мешает — тому его не показываем.
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)

    def test_time_is_written_the_way_it_is_spoken(self):
        # «Сегодня 18:45» человек читает мгновенно, «01.09.2026 18:45» —
        # сверяя с календарём в голове.
        self.assertIn("function fmtWhen(value)", self.js)
        self.assertIn("return 'сегодня ' + clock", self.js)
        self.assertIn("return 'вчера ' + clock", self.js)
        # Полная дата остаётся подсказкой при наведении.
        self.assertIn("title: fmtDateTime(item.created_at)", self.js)

    def test_the_line_and_the_equipment_are_separate_columns(self):
        """Линия связи и номер средства — разные вещи.

        В одной ячейке их не отсортировать и не сравнить глазами по столбцу,
        а отбирают письма своего хозяйства именно по ним.
        """
        self.assertIn("h('th', {}, 'Линия'),", self.js)
        self.assertIn("h('th', {}, 'Номер ТС'),", self.js)
        self.assertNotIn("'Линия · ТС'", self.js)

    def test_the_letters_table_lays_out_by_set_widths(self):
        """Одно длинное описание растягивало свой столбец, таблица вылезала
        за рамку, и кнопки правки уезжали за правый край — при том, что
        места хватало."""
        self.assertIn(".grid--letters {\n    table-layout: fixed;", self.css)
        self.assertIn(".grid--letters th:nth-child(4) { width: 118px; }", self.css)

    def test_the_day_roster_is_chips_and_not_a_column_of_names(self):
        """Столбик фамилий в цветной коробке читался на десятерых.

        На двадцати он превращался в простыню, которую надо прокручивать,
        чтобы найти одну фамилию.
        """
        self.assertIn("function rosterDayBoard(day)", self.js)
        self.assertIn("class: 'day-chip kind-' + row.cls", self.js)
        self.assertIn(".day-people {\n    display: flex;\n    flex-wrap: wrap;", self.css)
        # Отсутствующие первыми: их и ищут, присутствие подразумевается.
        self.assertIn("rows.sort((a, b) => (b.away ? 1 : 0) - (a.away ? 1 : 0));", self.js)

    def test_a_name_anywhere_opens_the_person(self):
        # Спрашивать по коридору «а Титов — это кто?» новому человеку неудобно.
        self.assertIn("async function openPerson(userId)", self.js)
        self.assertIn("function personLink(userId, name, extra)", self.js)
        self.assertIn("'/api/people/' + userId", self.js)
        # В списке писем, в сводке отдела и в расходе — везде.
        self.assertIn("personLink(item.assignee_id, item.assignee_name)", self.js)
        self.assertIn("personLink(person.id, person.full_name)", self.js)

    def test_the_person_card_splits_the_unit_from_the_staffing(self):
        # Слитое «отдел, группа» не отвечает ни на «в какой он группе»,
        # ни на «где числится по штату».
        self.assertIn("['Отдел', brandName()]", self.js)
        self.assertIn("rows.push(['Группа', person.team])", self.js)
        self.assertIn("rows.push(['По штату', person.department])", self.js)

    def test_the_idle_tail_of_the_board_is_folded(self):
        """В отделе на двадцать человек свободных больше, чем занятых.

        Сводка «кто чем занят» превращалась в две страницы нулей, из-за
        которых не видно тех, у кого горит.
        """
        self.assertIn("const idle = people.filter(", self.js)
        self.assertIn("'Показать ещё ' + idle.length", self.js)
        self.assertIn("'Скрыть свободных'", self.js)

    def test_the_whole_name_is_asked_for_where_a_person_is_entered(self):
        self.assertIn("'Фамилия, имя, отчество'", self.js)
        self.assertIn("placeholder: 'Петров Пётр Петрович'", self.js)
        self.assertIn("Фамилия, имя, отчество", self.login)
        self.assertIn('placeholder="Жуков Пётр Иванович"', self.login)
        # И одной фамилии окно входа не пропускает.
        self.assertIn("fullName.split(/\\s+/).length < 2", self.login)

    def test_the_header_shows_initials_and_not_the_whole_name(self):
        # «Никитин Виктор Павлович» в шапке вытесняет название раздела.
        self.assertIn("const name = state.user.short_name || state.user.full_name",
                      self.js)

    def test_a_word_document_is_shown_and_not_only_offered_for_download(self):
        """Браузер .docx не рисует и не будет: это упакованный архив.

        Но текст система вычитала при загрузке — и до сих пор окно по .docx
        говорило «скачайте и откройте своей программой», то есть отказывалось
        показать документ, который уже прочитан.
        """
        self.assertIn("const PREVIEW_READ = ['docx', 'doc', 'rtf', 'odt', 'xlsx', 'xls']",
                      self.js)
        self.assertIn("Так система прочитала документ", self.js)
        self.assertIn("class: 'preview-doc'", self.js)
        # И честно: это содержимое, а не вид документа.
        self.assertIn("Так система прочитала документ: только текст, ", self.js)
        self.assertIn("без разметки, колонок и таблиц. Подлинник — ", self.js)

    def test_an_archive_says_plainly_that_it_cannot_be_opened(self):
        self.assertIn("const PREVIEW_NONE = ['zip', '7z', 'rar']", self.js)
        self.assertIn("Архив на экране не раскрыть", self.js)

    def test_the_open_dropdown_is_not_transparent(self):
        """Раскрытый список рисует браузер, и цвет он берёт у самого select.

        У тихих списков в таблицах фон прозрачный — и раскрытый список
        выходил прозрачным: строки видно насквозь, а в тёмной теме их не
        видно вовсе.
        """
        self.assertIn("select option,\nselect optgroup {\n"
                      "    background-color: var(--panel);", self.css)
        self.assertIn("select option:disabled {", self.css)

    def test_a_file_can_be_sent_in_a_talk(self):
        # Половина вопросов решается тем, что человек показывает картинку,
        # а переслать её отделу было нечем: почты в контуре нет.
        self.assertIn("'/api/talks/' + data.id + '/files'", self.js)
        self.assertIn("function talkFileRow(item)", self.js)
        self.assertIn("title: 'Приложить файл'", self.js)

    def test_a_talk_leads_to_the_person_card(self):
        self.assertIn("class: 'talk-members grow'", self.js)
        self.assertIn("personLink(member.id, member.full_name)", self.js)
        self.assertIn("personLink(message.user_id, message.author || 'кто-то')", self.js)

    def test_the_sound_can_be_turned_off_and_checked(self):
        """Настройка, о которой никто не знает, — то же, что её отсутствие.

        Человек не знал ни того, что звук есть, ни того, что его можно
        убрать: и то и другое кончается выключенными колонками.
        """
        self.assertIn("function openNoticeSettings()", self.js)
        self.assertIn("function soundOn()", self.js)
        self.assertIn("'Проверить звук'", self.js)
        # Звук молчит, когда его выключили.
        self.assertIn("    function playAlert() {\n        if (!soundOn()) return;", self.js)
        # И видно, что приходит со звуком, а что молча.
        self.assertIn("['Вас вызывают в кабинет', true]", self.js)
        self.assertIn("['Сообщение в беседе', false]", self.js)

    def test_form_fields_hold_the_same_limits_as_the_server(self):
        """Поле не должно принимать то, что сервер потом отвергнет.

        Человек уже напечатал: узнать о пределе после нажатия «Сохранить» —
        значит потерять набранное. Пределы держим в одном месте и на обеих
        сторонах.
        """
        import re as _re

        from reportgen.web.api import MAX_CARD_FIELDS
        from reportgen.facts import MAX_GROUP

        found = _re.search(r"const CARD_LIMIT = \{([^}]+)\}", self.js)
        self.assertIsNotNone(found, "в интерфейсе нет пределов полей")
        limits = dict(_re.findall(r"(\w+): (\d+)", found.group(1)))
        for name, limit in MAX_CARD_FIELDS.items():
            with self.subTest(field=name):
                self.assertEqual(str(limit), limits.get(name),
                                 f"предел поля «{name}» в интерфейсе разошёлся с сервером")
        self.assertEqual(str(MAX_GROUP), limits.get("group_no"))

    def test_letter_card_locks_the_states_the_report_gives(self):
        # «На проверке», «проверен» и «отправлено» письму даёт ход отчёта.
        # В карточке эти состояния заперты, пока по письму есть отчёты.
        self.assertIn("CASE_BY_FLOW", self.js)
        self.assertIn("Это состояние письмо получает от проверки отчёта", self.js)

    def test_the_department_emblem_lives_in_the_corner_of_the_header(self):
        """Начальник отдела просил знак отдела в углу шапки.

        Проверяем не красоту — её видно глазом, — а то, что знак на месте,
        стоит правее карточки пользователя, ведёт в сводку и откликается
        на наведение.
        """
        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")

        head = self.html[self.html.index('class="topbar-right"'):
                         self.html.index("</header>")]
        self.assertIn('class="emblem"', head)
        self.assertLess(head.index("user-chip"), head.index('class="emblem"'),
                        "эмблема оказалась левее карточки пользователя")
        self.assertIn('href="#/board"', head[head.index('class="emblem"'):])

        # Отзывается на наведение и на нажатие, и это состояние, а не движение.
        self.assertIn(".emblem:hover .emblem-img", css)
        self.assertIn(".emblem--held", css)

    def test_the_emblem_is_the_department_sign_itself(self):
        """Знак — присланный оригинал файлом, а не перерисовка.

        Перерисовка вектором с каждым заходом была ближе к образцу, но
        оригиналом так и не стала. Проверяем, что в обоих окнах стоит один
        и тот же файл и что от перерисовки не осталось ни следа.
        """
        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")
        static = ROOT / "src" / "reportgen" / "web" / "static"

        for name in ("emblem.png", "emblem-small.png"):
            with self.subTest(file=name):
                self.assertTrue((static / name).is_file(), f"нет файла {name}")
                self.assertGreater((static / name).stat().st_size, 2000,
                                   f"{name} подозрительно пуст")

        for name, text in (("index.html", self.html), ("login.html", self.login)):
            with self.subTest(file=name):
                self.assertIn('class="emblem-img"', text)
                self.assertIn("/static/emblem", text)
                # Ни одного пути от прежней перерисовки.
                for gone in ("em-frame", "em-star", "em-merid", "em-arrow", "emblem-svg"):
                    self.assertNotIn(gone, text, f"в {name} остался {gone} от перерисовки")

        for gone in ("em-frame", "em-star", "em-merid", "emblem-turn", "em-fine"):
            self.assertNotIn(gone, css, f"в стилях остался {gone} от перерисовки")

    def test_the_emblem_never_moves(self):
        """У снимка вращать нечего, и он не дёргается.

        Прежний знак был набором путей, и глобус в нём крутился. Сейчас это
        фотография: любое движение здесь — либо подделка вращения, либо
        рывок. Проверяем, что ни анимации, ни смены габарита у знака нет.
        """
        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")
        block = css[css.index("--- эмблема отдела ---"):
                    css.index("--- личный кабинет ---")]
        for moving in ("animation", "scale(", "translate("):
            self.assertNotIn(moving, block, f"знак снова двигается: {moving}")
        self.assertNotIn("updatePlaybackRate", self.js)

    def test_the_small_emblem_is_scaled_before_shipping(self):
        """В шапке стоит заранее уменьшенный файл.

        Браузер сжимает 256 px до 34 хуже, чем это делает хороший фильтр при
        сборке; на экранах с двойной плотностью подставляется полный файл.
        """
        static = ROOT / "src" / "reportgen" / "web" / "static"
        from PIL import Image

        with Image.open(static / "emblem-small.png") as small:
            with Image.open(static / "emblem.png") as full:
                self.assertLess(small.size[0], full.size[0],
                                "мелкий файл не мельче полного")
                self.assertGreaterEqual(small.size[0], 68,
                                        "мелкого файла не хватит на 34 px при двойной плотности")

        head = self.html[self.html.index('class="topbar-right"'):
                         self.html.index("</header>")]
        mark = head[head.index('class="emblem"'):]
        self.assertIn("/static/emblem-small.png", mark)
        self.assertIn("/static/emblem.png 2x", mark)
        # На карточке входа знак крупный — там мелкий файл был бы мылом.
        self.assertNotIn("emblem-small", self.login)

    def test_nobody_is_asked_to_edit_json_anywhere(self):
        """Начальник отдела сказал прямо: правку JSON убрать.

        Ни при регистрации письма, ни на экране самого письма человек не
        должен видеть скобок и кавычек. Заготовку по шаблону собирает
        сервер, измерения правятся таблицей.
        """
        for gone in ("json-editor", "jsonMode", "renderFactsJson",
                     "toggleJsonMode", "Показать JSON", "facts-advanced"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.js, f"правка JSON вернулась: {gone}")

        create = self.js[self.js.index("function openNewCaseDialog"):]
        create = create[:create.index("\n    function ", 10)]
        self.assertNotIn("JSON", create, "в окне регистрации снова просят JSON")
        self.assertIn("Обязательны входящий номер и описание", create)

    def test_registering_a_letter_asks_what_the_department_needs(self):
        """Поля окна регистрации — те, что диктует порядок в отделе.

        Спускается письмо с входящим номером; в нём есть описание, номер
        группы, номер технического средства и линия связи; вместе с письмом
        приходят бумаги. Всё это и спрашивается — ни больше, ни меньше.
        """
        create = self.js[self.js.index("function openNewCaseDialog"):]
        create = create[:create.index("\n    function ", 10)]
        for label in ("Входящий номер", "Дата письма", "Номер группы", "Номер ТС",
                      "Линия связи", "Срок ответа", "Исполнитель", "Приоритет",
                      "Описание", "Учётный номер", "Приложенные файлы"):
            with self.subTest(field=label):
                self.assertIn(f"'{label}'", create, f"в окне нет поля «{label}»")
        # Тип отчёта при регистрации не спрашивают: шаблон выбирают, когда
        # садятся за текст, а не когда принимают входящее.
        self.assertNotIn("Тип отчёта", create)
        # Файлы кладутся после того, как письмо заведено: раньше не к чему.
        self.assertIn("/files", create)

    def test_the_word_subject_is_replaced_by_description_everywhere(self):
        # «Тема» и «описание» — разные слова, и в отделе говорят второе.
        self.assertNotIn("'Тема'", self.js)
        self.assertNotIn("без темы", self.js)
        self.assertIn("'Описание'", self.js)
        self.assertIn("без описания", self.js)

    def test_the_theme_switch_left_the_header(self):
        """Выбор темы оформления живёт в личном кабинете, а не в шапке.

        В шапке место дорогое: там имя, выход и знак отдела. Оформление
        меняют раз в жизни, и ради этого кнопка в углу не нужна.
        """
        head = self.html[self.html.index('class="topbar-right"'):
                         self.html.index("</header>")]
        self.assertNotIn("theme-btn", head)
        self.assertNotIn("theme-btn", self.js)
        self.assertNotIn("cycleTheme", self.js)
        # Но сам выбор никуда не делся.
        self.assertIn("function themeCard", self.js)

    def test_the_search_hint_fits_the_field(self):
        """Подсказка обрывалась на «и» — человек не понимал, чем она кончится.

        Считаем грубо, по числу знаков: поле шириной 320 px при 13 px шрифта
        держит примерно полсотни знаков, но подсказка серая и мелкая, и
        запас нужен. Сорок знаков — предел, за которым её резало.
        """
        import re as _re

        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")
        self.assertIn(".field-search", css, "поле поиска снова без заданной ширины")
        self.assertIn("class: 'field-search'", self.js)

        found = _re.search(r"type: 'search', class: 'field-search',\s*\n\s*"
                           r"placeholder: '([^']+)'", self.js)
        self.assertIsNotNone(found, "не нашёл подсказку поля поиска")
        self.assertLessEqual(len(found.group(1)), 40,
                             "подсказка поиска снова не помещается в поле")

    def test_the_department_is_one_and_is_not_asked_of_everybody(self):
        """Работают все в одном отделе — это и есть система.

        Спрашивать название отдела у каждого военнослужащего незачем: оно одно и
        стоит в настройках. Поле осталось для другого — человек может по
        штату числиться в другом подразделении, — и подписано «По штату».
        Справочника названий отделов больше нет ни на сервере, ни на экране.
        """
        self.assertNotIn("'Отдел', department", self.js,
                         "у военнослужащего снова спрашивают название отдела")
        self.assertIn("'По штату'", self.js)
        # Списка названий нет: подсказывать нечего, отдел один.
        self.assertNotIn("list: 'departments'", self.js)
        self.assertNotIn("datalist", self.js)
        self.assertNotIn("data.departments", self.js)

        # В кабинете отдел берётся из названия системы, а не из записи.
        cabinet = self.js[self.js.index("async function renderMe"):]
        cabinet = cabinet[:cabinet.index("\n    /* Что за человеком числится")]
        self.assertIn("brandName()", cabinet)

    def test_the_cabinet_grew_beyond_a_password_form(self):
        """Кабинет должен отвечать «что за мной», а не только «кто я».

        Раньше в нём были учётная запись, счётчики и смена пароля. Теперь —
        письма за человеком, его расход, контакты и документы.
        """
        # Берём только тело renderMe, до первой вспомогательной функции:
        # иначе проверка проходила бы на одном лишь определении карточки,
        # даже если её перестали ставить на страницу.
        cabinet = self.js[self.js.index("async function renderMe"):]
        cabinet = cabinet[:cabinet.index("\n    /* Что за человеком числится")]
        for part in ("myCasesCard(data)", "myRosterCard(data)", "contactsCard(user)",
                     "personFilesCard(user.id, true)", "passwordCard()", "themeCard()"):
            with self.subTest(card=part):
                self.assertIn(part, cabinet, f"в кабинете нет карточки {part}")

    def test_the_documents_are_opened_only_by_those_who_may(self):
        """Кнопка стоит только у того круга, кому документы открыты.

        Показать кнопку и ответить на неё отказом — худший из вариантов:
        человек видит дверь, которая не открывается.
        """
        self.assertIn("canReview() ? h('button'", self.js)
        self.assertIn("'Документы'", self.js)
        opener = self.js[self.js.index("function openPersonFiles"):]
        opener = opener[:opener.index("\n    async function renderUsers")]
        self.assertIn("personFilesCard(user.id, false)", opener)

    def test_the_folder_is_called_by_what_lies_in_it(self):
        """Раздел зовут «Документы», а не «личным делом».

        Личное дело — папка кадрового органа, и путать одно с другим нельзя.
        Здесь лежат справка-объективка, приказы и прочее.
        """
        self.assertNotIn("Личное дело", self.js)
        self.assertNotIn("личное дело", self.js)
        self.assertIn("Ваши документы", self.js)
        self.assertIn("справка-объективка", self.js)
        # Вид документа выбирается, и список видов приходит с сервера.
        card = self.js[self.js.index("function personFilesCard"):]
        self.assertIn("kindPick", card)
        self.assertIn("fillKinds(data.kinds)", card)

    def test_the_roster_has_its_own_section(self):
        """Расход — раздел, а не строчка в сводке.

        Им пользуются каждый день и все, а не только начальник: человек
        отмечает себя сам. Значит, у него своё место в меню и свой адрес.
        """
        self.assertIn("route: 'roster'", self.js)
        self.assertIn("'#/roster'", self.js)
        self.assertIn("'Расход'", self.js)
        self.assertIn("renderRoster", self.js)
        # Адрес должен разбираться маршрутизатором, иначе ссылка молча
        # уводит в сводку — так и было, пока раздел не внесли в список.
        known = self.js[self.js.index("['board', 'cases'"):]
        self.assertIn("'roster'", known[:200])

    def test_the_roster_kinds_match_the_server(self):
        """Виды занятости — один список на сервер и на экран.

        Разойдутся — человек отметит «отгул», а сетка нарисует пустую
        клетку, и расход перестанет сходиться.
        """
        import re as _re

        from reportgen.store.models import ABSENCE_KINDS, ABSENCE_TITLES

        found = _re.search(r"const ROSTER_KIND = \{(.+?)\n    \};", self.js, _re.S)
        self.assertIsNotNone(found, "в интерфейсе нет справочника видов занятости")
        block = found.group(1)
        for kind in ABSENCE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(f"{kind}:", block, f"в сетке нет вида «{kind}»")
                self.assertIn(f"'{ABSENCE_TITLES[kind]}'", block)
        # Каждому виду свой цвет: сетка читается цветом, а не подписью.
        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")
        for kind in ABSENCE_KINDS:
            with self.subTest(kind=kind):
                self.assertIn(f".kind-{kind} {{", css, f"у вида «{kind}» нет цвета")

    def test_the_roster_grid_keeps_the_names_in_sight(self):
        # На четырнадцати днях таблица едет вбок, и без фамилии на виду
        # читать её нельзя: клетка есть, а чья — непонятно.
        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")
        block = css[css.index(".grid--roster .roster-name {"):]
        block = block[:block.index("}")]
        self.assertIn("position: sticky", block)
        self.assertIn("left: 0", block)

    def test_nothing_on_the_screen_is_fetched_from_outside(self):
        """Изолированная машина: ни одного обращения наружу.

        Ни шрифтов, ни картинок, ни библиотек с чужих адресов — иначе на
        машине отдела экран встанет с пустыми местами и будет ждать сети.
        """
        import re as _re

        css = (ROOT / "src" / "reportgen" / "web" / "static" / "styles.css").read_text(
            encoding="utf-8")
        for name, text in (("index.html", self.html), ("login.html", self.login),
                           ("app.js", self.js), ("styles.css", css)):
            with self.subTest(file=name):
                outside = _re.findall(r"https?://[^\s\"')]+", text)
                self.assertEqual([], [a for a in outside
                                      if "w3.org" not in a and "schemas" not in a],
                                 f"{name} тянет что-то снаружи")

    def test_the_department_is_named_everywhere_the_same(self):
        # Название отдела стоит в трёх местах и должно совпадать.
        from reportgen.config import Settings

        settings = Settings()
        self.assertEqual("2 специальный отдел", settings.brand_name)
        self.assertEqual("2СО", settings.brand_short)
        self.assertIn(settings.brand_name, self.login)
        self.assertIn(settings.brand_name, self.html)
        self.assertIn(settings.brand_short, self.html)

    def test_letter_states_in_the_interface_match_the_server(self):
        """Состояние, которого нет в словаре интерфейса, ломает карточку.

        Список «Состояние» строится прямо из CASE_FLOW: пропущенное
        состояние не совпадёт ни с одним пунктом, и сохранение карточки
        молча переведёт письмо назад в «принято».
        """
        import re as _re

        from reportgen.store.models import CASE_STATUSES, CASE_STATUS_TITLES
        from reportgen.web.api import FLOW_CASE_STATUSES

        titles = dict(_re.findall(r"(\w+): '([^']+)'",
                                  _re.search(r"const CASE_STATUS = \{([^}]+)\}",
                                             self.js).group(1)))
        flow = _re.findall(r"'(\w+)'",
                           _re.search(r"const CASE_FLOW = \[([^\]]+)\]",
                                      self.js).group(1))
        by_flow = _re.findall(r"'(\w+)'",
                              _re.search(r"const CASE_BY_FLOW = \[([^\]]+)\]",
                                         self.js).group(1))
        self.assertEqual(list(CASE_STATUSES), flow, "порядок состояний разошёлся")
        self.assertEqual(sorted(FLOW_CASE_STATUSES), sorted(by_flow))
        for status in CASE_STATUSES:
            with self.subTest(status=status):
                self.assertEqual(CASE_STATUS_TITLES[status], titles.get(status),
                                 f"название состояния «{status}» разошлось с сервером")

    def test_direction_of_work_is_asked_when_handing_in_a_file(self):
        # Направление бралось первым по алфавиту — письмо выходило под
        # чужой темой. Спрашиваем при сдаче.
        self.assertIn("Направление работы", self.js)
        self.assertIn("form.append('report_type'", self.js)

    def test_files_are_uploaded_one_after_another(self):
        """Разом — это две беды сразу.

        Файлы выстраивались в порядке ответа сервера, а не выбора, и в
        пустом разговоре каждая загрузка успевала создать свой чат: два
        файла расходились по двум разговорам. Проверено в браузере —
        со старым кодом создавалось два разговора, с новым один.
        """
        self.assertIn("for (const file of files) await uploadAttachment(file)", self.js)
        self.assertNotIn("files.forEach((file) => uploadAttachment(file))", self.js)
        # Второй вызов ждёт первый, а не заводит свой разговор.
        self.assertIn("if (chat.creating) return chat.creating", self.js)

    def test_answer_without_sources_says_so(self):
        # Строка со счётчиком пропадала, когда источников не нашлось вовсе,
        # — а это самый важный случай: ответ написан по памяти модели.
        self.assertIn("ответ не опирается на библиотеку", self.js)

    def test_handed_in_report_card_does_not_demand_measurements(self):
        """Письмо под сданный файлом отчёт не требует факт-пакета.

        Его там нет и не нужно: отчёт написан человеком целиком. Красная
        врезка «не хватает обязательных измерений» на такой карточке —
        требование ни к чему.
        """
        self.assertIn("Отчёт написан человеком", self.js)
        self.assertIn("coverage-box--calm", self.js)
        # И вместо «в отчёте нет секций» — сам отчёт: имя файла, прочитанный
        # текст и кнопка на подлинник.
        self.assertIn("uploadedReportView(report)", self.js)

    def test_deletion_says_what_it_does_not_remove(self):
        # Выгруженные DOCX лежат в каталоге выгрузок: инженер выгрузил их сам,
        # стирать их за него нельзя, но и молчать об этом тоже.
        self.assertIn("Уже выгруженные файлы DOCX", self.js)

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

    def test_board_folds_into_one_column_before_the_table_is_squeezed(self):
        """Таблица нагрузки не должна уезжать под горизонтальную прокрутку.

        Оценка «две колонки помещаются от 1500» была занижена: на 1600
        таблице оставалось 739 px при нужных 807, и главную таблицу
        сводки приходилось листать вбок. Замерено в браузере на
        1366/1440/1600/1920/2560.
        """
        self.assertIn("@media (max-width: 1750px)", self.css)
        fold = self.css.split("@media (max-width: 1750px)", 1)[1][:200]
        self.assertIn(".board-cols", fold)
        self.assertIn("grid-template-columns: 1fr", fold)

    def test_page_head_wraps(self):
        self.assertIn("flex-wrap: wrap", self.block(".page-head"))
