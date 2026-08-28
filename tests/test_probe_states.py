"""Зонд по переходам состояний."""
import json, unittest
from pathlib import Path
import _bootstrap  # noqa
from test_web import WebTestCase, CASE


class Probe(WebTestCase):
    def setUp(self):
        super().setUp()
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])
        self.rid = self.report["id"]

    def clean(self, rid=None, text="Текст инженера без числовых значений."):
        rid = rid or self.rid
        rep = self.client.get(f"/api/reports/{rid}").json()["report"]
        for s in rep["sections"]:
            self.client.put(f"/api/reports/{rid}/sections/{s['section_id']}",
                            json={"text": text})

    def st(self, rid=None):
        rid = rid or self.rid
        return (self.repos.reports.get(rid).status,
                self.repos.cases.get(self.case["id"]).status)

    def test_A_full_cycle(self):
        log = []
        self.clean()
        log.append(("после правок", self.st()))
        self.login("engineer")
        r = self.client.post(f"/api/reports/{self.rid}/submit")
        log.append(("submit", r.status_code, self.st()))
        self.login("nachalnik")
        r = self.client.post(f"/api/reports/{self.rid}/rework", json={"note": "поправьте"})
        log.append(("rework", r.status_code, self.st()))
        self.login("engineer")
        r = self.client.post(f"/api/reports/{self.rid}/submit")
        log.append(("submit2", r.status_code, r.json(), self.st()))
        self.login("nachalnik")
        r = self.client.post(f"/api/reports/{self.rid}/approve")
        log.append(("approve", r.status_code, self.st()))
        # правка после проверки
        sid = self.report["sections"][0]["section_id"]
        self.login("engineer")
        r = self.client.put(f"/api/reports/{self.rid}/sections/{sid}",
                            json={"text": "Правка после проверки."})
        log.append(("edit", r.status_code, self.st()))
        print("\n=== A ===")
        for x in log: print(x)

    def test_B_repeat_submit_approve_rework(self):
        self.clean()
        self.login("engineer")
        print("\n=== B ===")
        r1 = self.client.post(f"/api/reports/{self.rid}/submit")
        r2 = self.client.post(f"/api/reports/{self.rid}/submit")
        print("submit x2:", r1.status_code, r2.status_code, self.st())
        self.login("nachalnik")
        a1 = self.client.post(f"/api/reports/{self.rid}/approve")
        pairs1 = self.repos.edits.count()
        a2 = self.client.post(f"/api/reports/{self.rid}/approve")
        pairs2 = self.repos.edits.count()
        print("approve x2:", a1.status_code, a2.status_code, self.st(), "pairs", pairs1, pairs2)
        b1 = self.client.post(f"/api/reports/{self.rid}/rework", json={"note": "раз"})
        b2 = self.client.post(f"/api/reports/{self.rid}/rework", json={"note": "два"})
        print("rework x2:", b1.status_code, b2.status_code, self.st())
        print("audit rework count:", sum(1 for e in self.repos.audit.list(500) if e.action=="report.rework"))

    def test_C_two_versions(self):
        print("\n=== C ===")
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.rid}/approve")
        print("v1 approved:", self.st())
        self.login("engineer")
        r2 = self.generate(self.case["id"])
        print("после generate v2:", "v1=", self.repos.reports.get(self.rid).status,
              "v2=", self.repos.reports.get(r2["id"]).status,
              "case=", self.repos.cases.get(self.case["id"]).status)
        self.clean(r2["id"])
        self.client.post(f"/api/reports/{r2['id']}/submit")
        self.login("nachalnik")
        bk = self.client.post(f"/api/reports/{r2['id']}/rework", json={"note": "нет"})
        print("v2 rework:", bk.status_code, "v1=", self.repos.reports.get(self.rid).status,
              "v2=", self.repos.reports.get(r2["id"]).status,
              "case=", self.repos.cases.get(self.case["id"]).status)
        latest = self.client.get(f"/api/cases/{self.case['id']}/report").json()["report"]
        print("итоговая по API:", latest["version"], latest["status"])

    def test_D_facts_edit_on_approved(self):
        print("\n=== D ===")
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.rid}/approve")
        rep = self.repos.reports.get(self.rid)
        print("до правки фактов:", rep.status, rep.approved_by, rep.approved_at)
        facts = dict(CASE)
        facts["group_no"] = "9-я группа"
        m = dict(facts["measurements"])
        k = sorted(m)[0]
        print("правим измерение", k, m[k])
        m[k] = {**m[k], "value": 12345.0} if isinstance(m[k], dict) else m[k]
        facts["measurements"] = m
        self.login("engineer")
        resp = self.client.put(f"/api/cases/{self.case['id']}/facts", json={"facts": facts})
        print("PUT facts:", resp.status_code, resp.text[:200])
        rep = self.repos.reports.get(self.rid)
        print("после правки фактов:", rep.status, rep.approved_by, rep.approved_at)
        print("stale:", self.service.facts_are_stale(rep))
        print("case:", self.repos.cases.get(self.case["id"]).status)

    def test_E_revalidate_on_review(self):
        print("\n=== E ===")
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        print("до:", self.st(), "errors", self.repos.reports.get(self.rid).error_count)
        # вернём в секцию число, которого нет в фактах, через правку фактов
        facts = dict(CASE)
        m = dict(facts["measurements"])
        print("keys", sorted(m)[:5])
        self.login("engineer")
        # ставим текст с числом
        sid = self.report["sections"][0]["section_id"]
        self.client.put(f"/api/reports/{self.rid}/sections/{sid}",
                        json={"text": "Значение составило 777,77 дБ."})
        print("после правки с числом:", self.st(),
              "errors", self.repos.reports.get(self.rid).error_count)
        # снова на проверку
        r = self.client.post(f"/api/reports/{self.rid}/submit")
        print("submit с ошибкой:", r.status_code, r.json())

    def test_F_revalidate_review_report(self):
        print("\n=== F ===")
        self.clean()
        sid = self.report["sections"][0]["section_id"]
        self.client.post(f"/api/reports/{self.rid}/submit")
        print("в review:", self.st())
        # правим факты так, чтобы отчёт стал неверным: меняем измерения
        facts = json.loads(json.dumps(CASE))
        facts["measurements"] = {}
        resp = self.client.put(f"/api/cases/{self.case['id']}/facts", json={"facts": facts})
        print("PUT facts:", resp.status_code, resp.text[:300])
        rep = self.repos.reports.get(self.rid)
        print("после:", rep.status, "errors", rep.error_count, "case",
              self.repos.cases.get(self.case["id"]).status)
