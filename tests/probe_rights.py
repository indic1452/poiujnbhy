"""Пробы прав нового порядка проверки отчётов."""
import json
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
from test_web import WebTestCase, CASE


class Probe(WebTestCase):
    def setUp(self):
        super().setUp()
        self.case = self.create_case()
        self.report = self.generate(self.case["id"])
        self.rid = self.report["id"]

    def clean(self):
        for section in self.report["sections"]:
            self.client.put(f"/api/reports/{self.rid}/sections/{section['section_id']}",
                            json={"text": "Текст инженера без числовых значений."})

    # ---- 1. перегенерация секции подписанного отчёта
    def test_1_regenerate_on_approved(self):
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        self.login("nachalnik")
        r = self.client.post(f"/api/reports/{self.rid}/approve")
        print("approve:", r.status_code, r.json()["report"]["status"])
        before = self.repos.reports.get(self.rid)
        print("approved_by:", before.approved_by, "at:", before.approved_at)
        self.login("engineer")
        sid = self.report["sections"][0]["section_id"]
        g = self.client.post(f"/api/reports/{self.rid}/sections/{sid}/regenerate", json={})
        print("regenerate:", g.status_code)
        after = self.repos.reports.get(self.rid)
        print("СОСТОЯНИЕ ПОСЛЕ ПЕРЕГЕНЕРАЦИИ:", after.status,
              "approved_by:", after.approved_by, "at:", after.approved_at)
        old = [s.text for s in before.sections if s.section_id == sid][0]
        new = [s.text for s in after.sections if s.section_id == sid][0]
        print("текст изменился:", old != new)
        print("шапка:", [l for l in after.markdown.splitlines() if "твер" in l or "ЕРНОВ" in l][:4])

    # ---- 2. перегенерация секции отчёта, лежащего на проверке
    def test_2_regenerate_on_review(self):
        self.clean()
        self.login("engineer")
        self.client.post(f"/api/reports/{self.rid}/submit")
        print("после submit:", self.repos.reports.get(self.rid).status)
        sid = self.report["sections"][0]["section_id"]
        g = self.client.post(f"/api/reports/{self.rid}/sections/{sid}/regenerate", json={})
        print("regenerate:", g.status_code)
        print("СОСТОЯНИЕ:", self.repos.reports.get(self.rid).status)

    # ---- 3. инженер правит статус письма
    def test_3_engineer_patches_case_status(self):
        self.login("engineer")
        r = self.client.patch(f"/api/cases/{self.case['id']}", json={"status": "approved"})
        print("PATCH status=approved:", r.status_code, r.json()["case"]["status"])
        lst = self.client.get("/api/cases", params={"status": "approved"}).json()
        print("в наборе «отправлено»:", lst["total"])
        print("отчёт при этом:", self.repos.reports.get(self.rid).status)

    # ---- 4. начальник группы правит статус письма
    def test_4_lead_patches_case_status(self):
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        self.login("gruppa")
        a = self.client.post(f"/api/reports/{self.rid}/approve")
        print("lead approve:", a.status_code)
        r = self.client.patch(f"/api/cases/{self.case['id']}", json={"status": "approved"})
        print("lead PATCH case:", r.status_code, r.json()["case"]["status"])

    # ---- 5. инженер: двойной submit / submit после approve
    def test_5_submit_twice(self):
        self.clean()
        self.login("engineer")
        a = self.client.post(f"/api/reports/{self.rid}/submit")
        b = self.client.post(f"/api/reports/{self.rid}/submit")
        print("submit1:", a.status_code, a.json()["report"]["status"])
        print("submit2:", b.status_code, b.json()["report"]["status"])
        self.login("nachalnik")
        self.client.post(f"/api/reports/{self.rid}/approve")
        self.login("engineer")
        c = self.client.post(f"/api/reports/{self.rid}/submit")
        print("submit после approve:", c.status_code, c.text[:120])

    # ---- 6. проверяющего разжаловали/отключили, пока отчёт у него
    def test_6_reviewer_demoted(self):
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        head = self.repos.users.by_login("nachalnik")
        self.login("nachalnik")
        # владелец разжалует начальника отдела в инженеры
        self.login("admin")
        r = self.client.patch(f"/api/users/{head.id}", json={"role": "engineer"})
        print("разжалование:", r.status_code, r.json()["user"]["role"])
        # старая сессия начальника
        self.login("nachalnik")
        a = self.client.post(f"/api/reports/{self.rid}/approve")
        print("approve после разжалования:", a.status_code, a.text[:150])

    def test_7_reviewer_deactivated(self):
        self.clean()
        self.client.post(f"/api/reports/{self.rid}/submit")
        head = self.repos.users.by_login("nachalnik")
        self.login("nachalnik")
        me = self.client.get("/api/me").status_code
        self.login("admin")
        r = self.client.post(f"/api/users/{head.id}/active", json={"active": False})
        print("отключение:", r.status_code)
        # пробуем прежним печеньем
        self.login("admin")
        print("отчёт остался:", self.repos.reports.get(self.rid).status)
        # заместитель принимает
        self.login("zam")
        a = self.client.post(f"/api/reports/{self.rid}/approve")
        print("зам approve:", a.status_code, a.json()["report"]["status"] if a.status_code == 200 else a.text[:120])
