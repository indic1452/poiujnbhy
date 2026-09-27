"""Метрики: только администратору, и с телом вопросов к помощнику.

Распоряжение начальника отдела, дословно: «сделай, чтобы админ мог видеть
тело запроса пользователей в метриках» и «метрики доступны только админу».

Прежде было наоборот: сводку по работе отдела — сколько писем, чьи отчёты
правят и насколько сильно — видел любой вошедший, а вопросов к помощнику не
видел никто, включая начальника.

Начальник смотрит переписку подчинённых с помощником — это его право в его
системе. Но не втайне: каждый такой просмотр пишется в журнал действий,
ровно так же, как записано, кто открывал письмо и правил библиотеку.

Ради чего это заведено: в таблице сразу видно вопросы, на которых помощник
сработал вхолостую — нашёл мало, сослался ни на что. Такой вопрос и есть
повод пополнить словарь терминов или пересмотреть нарезку библиотеки.
"""

import _bootstrap  # noqa: F401
from test_web import WebTestCase


class МетрикиТолькоАдмину(WebTestCase):
    def test_инженеру_метрики_закрыты(self):
        self.login("engineer")
        ответ = self.client.get("/api/stats")
        self.assertEqual(403, ответ.status_code, ответ.text)

    def test_инженеру_закрыты_и_вопросы(self):
        self.login("engineer")
        ответ = self.client.get("/api/stats/questions")
        self.assertEqual(403, ответ.status_code, ответ.text)

    def test_начальнику_группы_метрики_открыты(self):
        """Права администратора — у должностей до начальника группы."""
        self.login("gruppa")
        self.assertEqual(200, self.client.get("/api/stats").status_code)
        self.assertEqual(200, self.client.get("/api/stats/questions").status_code)

    def test_без_входа_закрыто(self):
        self.client.post("/api/auth/logout")
        for адрес in ("/api/stats", "/api/stats/questions"):
            with self.subTest(адрес=адрес):
                self.assertEqual(401, self.client.get(адрес).status_code)


class ТелоВопросаВидноАдмину(WebTestCase):
    ВОПРОС = "что такое e1 уплотнение и его структура"

    def спросить(self, логин, вопрос):
        self.login(логин)
        chat = self.client.post("/api/chats", json={}).json()["chat"]
        ответ = self.client.post(f"/api/chats/{chat['id']}/ask",
                                 json={"text": вопрос})
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()

    def вопросы(self, **параметры):
        self.login("admin")
        ответ = self.client.get("/api/stats/questions", params=параметры)
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()["items"]

    def test_вопрос_инженера_виден_администратору_целиком(self):
        self.спросить("engineer", self.ВОПРОС)
        строки = self.вопросы()
        свежая = строки[0]
        self.assertEqual(self.ВОПРОС, свежая["question"])
        self.assertEqual("Инженеров И. И.", свежая["full_name"])
        self.assertEqual("engineer", свежая["login"])

    def test_видно_сработал_ли_помощник_вхолостую(self):
        """Ради этих чисел таблица и заведена.

        Сверяем их с тем, что записано в самом ответе: иначе столбец «что
        нашлось» будет показывать нули на любом вопросе, и вхолостую от
        удачного не отличить — а таблица именно за этим и нужна.
        """
        ответ = self.спросить("engineer", self.ВОПРОС)
        meta = ответ["answer"]["meta"]
        свежая = self.вопросы()[0]
        self.assertTrue(свежая["answered"])
        for поле in ("found", "shown", "cited", "documents"):
            with self.subTest(поле=поле):
                self.assertEqual(int(meta.get(поле) or 0), свежая[поле])
        self.assertGreater(свежая["found"], 0,
                           "образец подобран неудачно: поиск не нашёл ничего, "
                           "и нули здесь ничего не доказывают")

    def test_поиск_по_телу_вопроса(self):
        self.спросить("engineer", "какая полоса у ствола")
        self.спросить("zam", self.ВОПРОС)
        найдено = self.вопросы(query="уплотнение")
        self.assertEqual(1, len(найдено), найдено)
        self.assertEqual(self.ВОПРОС, найдено[0]["question"])

    def test_чужой_чат_по_прежнему_не_открыть(self):
        """Метрики — это сводка, а не ключ от чужого разговора.

        Администратор видит, о чём спрашивают. Войти в чужой разговор и
        писать в него от чужого имени он по-прежнему не может.
        """
        свой = self.спросить("engineer", self.ВОПРОС)
        chat_id = свой["chat"]["id"]
        self.login("admin")
        ответ = self.client.get(f"/api/chats/{chat_id}")
        self.assertIn(ответ.status_code, (403, 404), ответ.text)

    def test_просмотр_записан_в_журнал(self):
        """Право смотреть — не тайна смотреть."""
        self.спросить("engineer", self.ВОПРОС)
        self.вопросы()
        self.login("admin")
        записи = self.client.get("/api/audit?limit=50").json()["items"]
        просмотры = [z for z in записи if z["action"] == "stats.questions"]
        self.assertTrue(просмотры, f"просмотр не записан: "
                                   f"{[z['action'] for z in записи]}")
        self.assertEqual("admin", просмотры[0]["login"])


if __name__ == "__main__":
    import unittest

    unittest.main()
