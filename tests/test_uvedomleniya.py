"""Число у колокольчика и удаление беседы.

Две жалобы отдела, и обе про одно — про то, что человек не может привести
систему в порядок своими руками:

* «Я прочитал сообщение, даже удалил из вкладки уведомлений, но цифра висит,
  и никак её не убрать». Число у колокольчика складывается из двух
  источников: непрочитанных уведомлений и непрочитанных сообщений бесед.
  Второго в списке уведомлений не видно, и «прочитать всё» его не снимало —
  человек читал всё, что видел, а число оставалось.

* «Удалил чат, у другого остался, и чтобы снова написать, нужно создать ему
  доп. чат со мной». Уходил только тот, кто удалял. У собеседника оставалась
  беседа, отвечать в которой некому, а следующее «написать Иванову» заводило
  ВТОРУЮ беседу с тем же человеком: прежнюю поиск уже не признавал беседой
  двоих, потому что участник в ней остался один.
"""

import unittest

import _bootstrap  # noqa: F401
from test_web import WebTestCase


class Беседы(WebTestCase):
    def кто(self, login: str) -> int:
        пользователь = self.repos.users.by_login(login)
        assert пользователь is not None
        return пользователь.id

    def завести(self, *логины: str, title: str = "") -> int:
        return self.repos.talks.create([self.кто(логин) for логин in логины],
                                       title=title,
                                       created_by=self.кто(логины[0]))

    def колокольчик(self) -> dict:
        ответ = self.client.get("/api/notifications")
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()


class УдалениеБеседыДвоих(Беседы):
    """У разговора двоих нет третьего, чью запись мы бы стёрли."""

    def test_удаляется_у_обоих(self):
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Смотрите вложение")
        ответ = self.client.delete(f"/api/talks/{talk}")
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertTrue(ответ.json()["both"])

        self.login("engineer")
        свои = [item["id"] for item in self.repos.talks.list_for(self.кто("engineer"))]
        self.assertNotIn(talk, свои, "у собеседника беседа осталась")
        self.assertEqual(404, self.client.get(f"/api/talks/{talk}").status_code)

    def test_сообщения_и_участники_убраны_совсем(self):
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Есть вопрос")
        self.client.delete(f"/api/talks/{talk}")
        self.assertEqual([], self.repos.talks.members(talk))
        self.assertEqual([], self.repos.talks.messages(talk))

    def test_следующая_переписка_не_вторая_ветка(self):
        """Ровно то, на что пожаловался отдел.

        Прежде после удаления прежняя беседа переставала быть беседой двоих
        (участник в ней остался один), поиск её не признавал, и «написать
        Иванову» заводило вторую ветку к тому же человеку.
        """
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("admin"), "Первый разговор")
        self.client.delete(f"/api/talks/{talk}")

        ответ = self.client.post("/api/talks",
                                 json={"members": [self.кто("engineer")]})
        self.assertEqual(200, ответ.status_code, ответ.text)
        новая = ответ.json()["talk_id"]
        self.assertFalse(ответ.json()["existed"], "нашлась удалённая беседа")
        # Номер может совпасть со старым — SQLite переиспользует освободившийся.
        # Важно не это, а что разговор начался с чистого листа.
        self.assertEqual([], self.repos.talks.messages(новая))

        self.login("engineer")
        свои = self.repos.talks.list_for(self.кто("engineer"))
        с_админом = [беседа for беседа in свои
                     if any(участник["id"] == self.кто("admin")
                            for участник in беседа["members"])]
        self.assertEqual(1, len(с_админом),
                         "у собеседника оказалось две ветки с одним человеком")

    def test_удаление_осталось_в_журнале(self):
        """Переписка удалена — это действие, и след о нём обязан быть."""
        talk = self.завести("admin", "engineer")
        self.client.delete(f"/api/talks/{talk}")
        записи = self.repos.audit.list(limit=20)
        свои = [запись for запись in записи if запись.action == "talk.leave"]
        self.assertTrue(свои, "удаление беседы не попало в журнал")
        self.assertTrue(свои[0].details.get("both"))


class ВыходИзБеседыНескольких(Беседы):
    """Решение одного участника не решает за всех."""

    def test_у_остальных_беседа_остаётся(self):
        talk = self.завести("admin", "engineer", "nachalnik", title="Разбор линии")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Готово")
        ответ = self.client.delete(f"/api/talks/{talk}")
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertFalse(ответ.json()["both"])
        self.assertFalse(ответ.json()["purged"])

        оставшиеся = {участник["id"] for участник in self.repos.talks.members(talk)}
        self.assertEqual({self.кто("engineer"), self.кто("nachalnik")}, оставшиеся)
        self.assertTrue(self.repos.talks.messages(talk),
                        "переписка остальных участников стёрта")

    def test_последний_участник_убирает_беседу_целиком(self):
        talk = self.завести("admin", "engineer", title="Вдвоём, но с названием")
        self.client.delete(f"/api/talks/{talk}")
        self.login("engineer")
        ответ = self.client.delete(f"/api/talks/{talk}")
        self.assertTrue(ответ.json()["purged"])
        self.assertEqual([], self.repos.talks.members(talk))

    def test_беседа_с_названием_не_считается_беседой_двоих(self):
        """Признак — не число участников само по себе, а отсутствие названия.

        Беседу с названием человек заводил как общую, и удалять её у другого
        по своему решению он не вправе, даже если их пока двое.
        """
        talk = self.завести("admin", "engineer", title="Приёмка комплекса")
        self.assertFalse(self.repos.talks.is_private(talk))
        ответ = self.client.delete(f"/api/talks/{talk}")
        self.assertFalse(ответ.json()["both"])
        self.assertEqual([self.кто("engineer")],
                         [участник["id"] for участник in
                          self.repos.talks.members(talk)])


class ЧислоУКолокольчика(Беседы):
    def test_непрочитанные_сообщения_входят_в_число(self):
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Посмотрите")
        self.assertEqual(1, self.колокольчик()["messages"])

    def test_прочитать_всё_снимает_и_сообщения(self):
        """Кнопка обязана делать то, что на ней написано, а не половину."""
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Первое")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Второе")
        self.assertEqual(2, self.колокольчик()["messages"])

        ответ = self.client.post("/api/notifications/read", json={})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual(0, ответ.json()["messages"])
        self.assertEqual(2, ответ.json()["talks_read"])
        self.assertEqual(0, self.колокольчик()["messages"])

    def test_чтение_одного_уведомления_бесед_не_трогает(self):
        """Прочесть одно — это прочесть одно, а не всё сразу.

        Иначе щелчок по уведомлению молча стирал бы непрочитанное в беседах,
        которые человек и не открывал.
        """
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Посмотрите")
        уведомление = self.repos.notices.add(
            self.кто("admin"), "message", "Сообщение", "текст", "", None)
        ответ = self.client.post("/api/notifications/read",
                                 json={"id": уведомление.id})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual(1, ответ.json()["messages"])

    def test_своё_сообщение_себе_не_считается(self):
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("admin"), "Моё")
        self.assertEqual(0, self.колокольчик()["messages"])

    def test_своё_не_считается_даже_в_обход_отправки(self):
        """Правило записано в самом счёте, а не только в порядке записи.

        Обычно своё сообщение не считается потому, что отправка сразу
        двигает отметку прочтения автора. Но это бухгалтерия, и она может
        не сработать: сообщение заведено другим путём, отметка не сдвинута.
        Счёт обязан устоять и тогда — иначе человек увидит число от
        собственных слов и не сможет его снять ничем, кроме как открыв
        беседу, где читать нечего.
        """
        talk = self.завести("admin", "engineer")
        self.repos.db.execute(
            "INSERT INTO talk_messages(talk_id, user_id, text, created_at) "
            "VALUES(?,?,?,datetime('now'))",
            (talk, self.кто("admin"), "Своё, мимо отправки"))
        self.assertEqual(0, self.repos.talks.unread_total(self.кто("admin")))
        self.assertEqual(1, self.repos.talks.unread_total(self.кто("engineer")))

    def test_служебная_запись_без_автора_не_считается(self):
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, None, "Беседа создана")
        self.assertEqual(0, self.колокольчик()["messages"])

    def test_открытая_беседа_снимает_своё(self):
        talk = self.завести("admin", "engineer")
        self.repos.talks.add_message(talk, self.кто("engineer"), "Посмотрите")
        self.client.get(f"/api/talks/{talk}")
        self.assertEqual(0, self.колокольчик()["messages"])


if __name__ == "__main__":
    unittest.main()
