# -*- coding: utf-8 -*-
"""Гость: только помощник, без истории и без личного кабинета.

Начальник отдела попросил завести такую должность: человеку со стороны — из
соседнего отдела, из части, с проверки — иногда нужно спросить у помощника по
библиотеке, и заводить ради этого инженера с доступом к письмам нельзя.

Устройство простое и намеренно закрытое по умолчанию: гостя не пускает сам
require_user, через который проходят все разделы. Открыт ровно один помощник.
Маршрут, написанный завтра, окажется для гостя закрыт сам собой — забыть
закрыть легко, забыть открыть заметно сразу.

Переписка гостя не сохраняется: она стирается и при входе, и при выходе.
Уйти можно и не нажимая «выйти» — просто закрыв окно, — поэтому оба конца.
"""

import unittest

import _bootstrap  # noqa: F401

from test_web import WebTestCase

from reportgen.store.models import ROLE_TITLES, ROLES, STAFF_ROLES


class ДолжностьЕсть(unittest.TestCase):
    def test_гость_в_перечне_должностей(self):
        self.assertIn("guest", ROLES)
        self.assertEqual("Гость", ROLE_TITLES["guest"])

    def test_гость_не_штатная_должность(self):
        self.assertNotIn("guest", STAFF_ROLES)

    def test_гость_не_ведёт_писем_и_отчётов(self):
        """Признак проверяем на самой записи, а не через маршруты.

        Через маршруты гостя не пускает require_user, и этот признак там
        просто не спрашивают. Но должность отвечает за себя сама: «может
        вести письма» у гостя обязано быть ложью в любом месте кода.
        """
        from reportgen.store.models import User

        гость = User(id=1, login="gost", full_name="Гостев Г. Г.", role="guest")
        инженер = User(id=2, login="inzh", full_name="Инженеров И. И.",
                       role="engineer")
        self.assertFalse(гость.can_edit, "гость числится ведущим письма")
        self.assertFalse(гость.is_admin)
        self.assertFalse(гость.can_review)
        self.assertTrue(гость.is_guest)
        self.assertTrue(инженер.can_edit)
        self.assertFalse(инженер.is_guest)


class ГостюОткрытТолькоПомощник(WebTestCase):
    """Что закрыто — то закрыто, и не по одному маршруту, а всё сразу."""

    def setUp(self):
        super().setUp()
        self.repos.users.create("gost", "пароль123", "Гостев Г. Г.", "guest")
        self.login("gost")

    #: Разделы отдела. Гостю не положен ни один.
    ЗАКРЫТО = ("/api/cases", "/api/library", "/api/users", "/api/notifications",
               "/api/roster", "/api/talks", "/api/stats", "/api/audit")

    def test_разделы_отдела_закрыты(self):
        for путь in self.ЗАКРЫТО:
            with self.subTest(путь=путь):
                ответ = self.client.get(путь)
                self.assertEqual(403, ответ.status_code, ответ.text)
                self.assertIn("гост", ответ.text.lower())

    def test_писем_гость_не_заводит(self):
        ответ = self.client.post("/api/cases", json={"report_type": "x",
                                                    "case_id": "1", "facts": {}})
        self.assertIn(ответ.status_code, (403, 422))

    def test_помощник_открыт(self):
        self.assertEqual(200, self.client.get("/api/chats").status_code)
        self.assertEqual(200, self.client.get("/api/config").status_code)

    def test_кто_я_отвечает_и_говорит_что_это_гость(self):
        ответ = self.client.get("/api/me")
        self.assertEqual(200, ответ.status_code)
        человек = ответ.json()["user"]
        self.assertEqual("guest", человек["role"])
        self.assertTrue(человек["is_guest"])
        self.assertFalse(человек["is_admin"])
        self.assertFalse(человек["can_review"])

    def test_разговор_с_помощником_заводится(self):
        направление = self.client.get("/api/config").json()["domains"][0]["id"]
        ответ = self.client.post("/api/chats",
                                 json={"title": "Вопрос", "domain": направление})
        self.assertEqual(200, ответ.status_code, ответ.text)
        номер = ответ.json()["chat"]["id"]
        self.assertEqual(200, self.client.get(f"/api/chats/{номер}").status_code)


class ИсторияГостяНеХранится(WebTestCase):
    """Обещание «переписка не сохраняется» держится с обоих концов."""

    def setUp(self):
        super().setUp()
        self.гость = self.repos.users.create("gost", "пароль123", "Гостев Г. Г.", "guest")

    def завести_разговор(self):
        self.login("gost")
        направление = self.client.get("/api/config").json()["domains"][0]["id"]
        ответ = self.client.post("/api/chats",
                                 json={"title": "Вопрос гостя", "domain": направление})
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()["chat"]["id"]

    def test_выход_стирает_разговоры(self):
        self.завести_разговор()
        self.assertEqual(1, len(self.client.get("/api/chats").json()["items"]))
        self.client.post("/api/auth/logout", json={})
        self.login("gost")
        self.assertEqual([], self.client.get("/api/chats").json()["items"])

    def test_вход_стирает_то_что_осталось_от_прошлого_раза(self):
        """Окно можно просто закрыть, не нажимая «выйти»."""
        self.завести_разговор()
        # Уходим, не выходя: просто входим заново другим сеансом.
        self.client.cookies.clear()
        self.login("gost")
        self.assertEqual([], self.client.get("/api/chats").json()["items"])

    def test_сообщения_и_вложения_уходят_вместе_с_разговором(self):
        номер = self.завести_разговор()
        self.repos.chats.add_message(номер, "user", "Какая полоса у КАМ-16?")
        self.assertTrue(self.repos.chats.messages(номер))
        self.client.post("/api/auth/logout", json={})
        self.assertEqual([], self.repos.chats.messages(номер))

    def test_у_штатного_военнослужащего_история_остаётся(self):
        """Обратная сторона: инженеру переписка нужна именно сохранённой."""
        self.login("engineer")
        направление = self.client.get("/api/config").json()["domains"][0]["id"]
        self.client.post("/api/chats", json={"title": "Свой", "domain": направление})
        self.client.post("/api/auth/logout", json={})
        self.login("engineer")
        self.assertEqual(1, len(self.client.get("/api/chats").json()["items"]))


class ИнтерфейсГостя(unittest.TestCase):
    """Разделов, которые всё равно закрыты, гостю не показывают вовсе."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        корень = Path(__file__).resolve().parents[1]
        cls.js = (корень / "src" / "reportgen" / "web" / "static"
                  / "app.js").read_text(encoding="utf-8")

    def test_меню_гостя_только_помощник(self):
        self.assertIn("if (isGuest() && section.route !== 'chat') return;", self.js)

    def test_чужой_маршрут_возвращает_к_помощнику(self):
        self.assertIn("if (isGuest() && route.name !== 'chat')", self.js)

    def test_личного_кабинета_у_гостя_нет(self):
        self.assertIn("chip.removeAttribute('href')", self.js)

    def test_колокол_гостю_не_показывают(self):
        self.assertIn("if (!isGuest()) {", self.js)

    def test_гостя_предупреждают_что_разговор_не_сохранится(self):
        self.assertIn("guest-note", self.js)
        self.assertIn("не сохраняется", self.js)


if __name__ == "__main__":
    unittest.main()
