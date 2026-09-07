"""Переключатель «Разбор / Быстро» в самом разговоре.

Разбор в несколько заходов сделал ответы точнее и источников в них больше —
но идёт минутами. Одна настройка на весь отдел тут не годится: тот же инженер
утром разбирает дамп по протоколу, а днём спрашивает «что означает поле FCS»
и ждать не готов. Поэтому режим принадлежит разговору, а не человеку и не
файлу настроек.

Главное, что здесь проверяется: быстрый режим экономит на ОБЪЁМЕ РАБОТЫ, а не
на добросовестности. Заходов меньше, ответ короче — но правила те же:
источники, ссылки, честное «в библиотеке этого нет».
"""

import unittest

import _bootstrap  # noqa: F401

from reportgen.store.models import Chat
from reportgen.web.assistant import (
    CHAT_MODES,
    DEFAULT_CHAT_MODE,
    FAST_LIMITS,
    chat_mode,
)
from reportgen.web.service import ServiceError

from test_assistant import AssistantHttpTests, AssistantTestCase  # noqa: E402


class РежимРазговора(AssistantTestCase):
    """Режим хранится у разговора и переживает перезагрузку страницы."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov, title="Разбор дампа")

    def test_по_умолчанию_разбор(self):
        # Умолчание — то, ради чего всё делалось. Быстрый режим выбирают, а не
        # получают молча.
        self.assertEqual("deep", self.chat.mode)
        self.assertEqual("deep", DEFAULT_CHAT_MODE)

    def test_режим_переключается_и_сохраняется(self):
        обновлён = self.assistant.update(self.ivanov, self.chat.id, mode="fast")
        self.assertEqual("fast", обновлён.mode)
        # Именно перечитанный из базы, а не тот же объект в памяти.
        self.assertEqual("fast", self.assistant.get_chat(self.ivanov, self.chat.id).mode)

    def test_режим_виден_в_ответе_интерфейсу(self):
        # Без этого браузер не сможет показать, какой режим выбран.
        self.assertIn("mode", self.chat.to_dict())

    def test_чужой_режим_не_принимается(self):
        with self.assertRaises(ServiceError) as поймано:
            self.assistant.update(self.ivanov, self.chat.id, mode="турбо")
        self.assertEqual(400, поймано.exception.status)
        self.assertEqual("deep", self.assistant.get_chat(self.ivanov, self.chat.id).mode)

    def test_режим_у_каждого_разговора_свой(self):
        второй = self.assistant.create_chat(self.ivanov, title="Что такое FCS")
        self.assistant.update(self.ivanov, второй.id, mode="fast")
        self.assertEqual("deep", self.assistant.get_chat(self.ivanov, self.chat.id).mode)
        self.assertEqual("fast", self.assistant.get_chat(self.ivanov, второй.id).mode)

    def test_мусор_в_поле_читается_как_разбор(self):
        # Записи из прежних версий и правки базы руками не должны приводить к
        # тому, что помощник тихо начнёт отвечать вполсилы.
        self.assertEqual("deep", chat_mode(Chat(id=1, user_id=1, mode="")))
        self.assertEqual("deep", chat_mode(Chat(id=1, user_id=1, mode="ГЛУБОКО")))
        self.assertEqual("deep", chat_mode(None))
        self.assertEqual("fast", chat_mode(Chat(id=1, user_id=1, mode="FAST")))


class ПрофильРаботы(AssistantTestCase):
    """Что именно меняет режим — и чего он не меняет."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov, title="Проба")

    def _профиль(self, mode):
        chat = self.assistant.update(self.ivanov, self.chat.id, mode=mode)
        return self.assistant._profile(chat)

    def test_разбор_берёт_настройки_отдела_как_есть(self):
        # Режим ничего не выдумывает: «глубоко» — это ровно то, что в
        # settings.json. Иначе правка настройки не имела бы силы.
        профиль = self._профиль("deep")
        self.assertEqual(int(self.settings.assistant_rounds), профиль["rounds"])
        self.assertEqual(int(self.settings.assistant_max_tokens), профиль["max_tokens"])
        self.assertEqual(int(self.settings.assistant_target_words), профиль["target_words"])

    def test_быстро_урезает_все_четыре_рычага(self):
        быстро = self._профиль("fast")
        глубоко = self._профиль("deep")
        for рычаг in ("rounds", "top_k", "target_words", "max_tokens"):
            with self.subTest(рычаг=рычаг):
                self.assertLessEqual(быстро[рычаг], глубоко[рычаг])
                self.assertLessEqual(быстро[рычаг], FAST_LIMITS[рычаг])

    def test_быстро_обходится_без_заходов(self):
        # Заходы — главная статья расхода времени: каждый это обращение к
        # модели плюс поиски.
        self.assertEqual(0, self._профиль("fast")["rounds"])

    def test_быстро_не_станет_медленнее_разбора(self):
        # Отдел вправе опустить настройки ниже быстрого режима. Тогда быстрый
        # обязан остаться быстрым, а не подтянуться к потолку.
        self.settings.assistant_rounds = 0
        self.settings.assistant_max_tokens = 400
        self.settings.assistant_target_words = 60
        быстро = self._профиль("fast")
        self.assertEqual(0, быстро["rounds"])
        self.assertEqual(400, быстро["max_tokens"])
        self.assertEqual(60, быстро["target_words"])

    def test_правила_ответа_одни_и_те_же(self):
        # Экономим на объёме работы, а не на добросовестности: системная
        # инструкция от режима не зависит вовсе.
        from reportgen.web import assistant as модуль
        self.assertNotIn("fast", модуль.ASSISTANT_SYSTEM_PROMPT)
        self.assertNotIn("быстр", модуль.ASSISTANT_SYSTEM_PROMPT.lower())


class РежимДоходитДоОтвета(AssistantTestCase):
    """Поле в базе бесполезно, если ответ его не замечает."""

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov, title="Проба")

    def _подготовка(self, mode):
        self.assistant.update(self.ivanov, self.chat.id, mode=mode)
        return self.assistant._prepare(
            self.ivanov, self.chat.id, "что такое FCS", top_k=None)

    def test_профиль_доезжает_до_подготовки_ответа(self):
        подготовка = self._подготовка("fast")
        self.assertIn("profile", подготовка)
        self.assertEqual(0, подготовка["profile"]["rounds"])

    def test_целевой_объём_попадает_в_сам_промпт(self):
        # Число из профиля должно оказаться в тексте задания, иначе модель о
        # выбранном режиме не узнает.
        быстро = self._подготовка("fast")
        self.assertIn(str(быстро["profile"]["target_words"]), быстро["prompt"])
        глубоко = self._подготовка("deep")
        self.assertIn(str(глубоко["profile"]["target_words"]), глубоко["prompt"])
        self.assertNotEqual(быстро["profile"]["target_words"],
                            глубоко["profile"]["target_words"])

    def test_быстрый_режим_не_ходит_заходами(self):
        # След разбора — это и есть заходы. В быстром режиме его нет.
        self.assertEqual([], self._подготовка("fast")["trail"])

    def test_профиль_доезжает_до_сбора_материала(self):
        """Число заходов и ширина поиска должны дойти до самого сбора.

        Иначе режим влиял бы только на длину ответа, а самая долгая часть —
        заходы поиска — шла бы как ни в чём не бывало.
        """
        from unittest import mock

        поймано = {}
        настоящий = self.assistant._collect

        def перехват(chat, question, history, top_k, **kwargs):
            поймано["top_k"] = top_k
            поймано["rounds"] = kwargs.get("rounds")
            return настоящий(chat, question, history, top_k, **kwargs)

        with mock.patch.object(self.assistant, "_collect", side_effect=перехват):
            self._подготовка("fast")
        self.assertEqual(0, поймано["rounds"], "заходы не урезаны")
        self.assertLessEqual(поймано["top_k"], FAST_LIMITS["top_k"])

        поймано.clear()
        with mock.patch.object(self.assistant, "_collect", side_effect=перехват):
            self._подготовка("deep")
        self.assertEqual(int(self.settings.assistant_rounds), поймано["rounds"])

    def test_потолок_длины_ответа_берётся_из_профиля(self):
        # Иначе быстрый режим отличался бы только на словах: модель писала бы
        # ровно столько же.
        источник = (_bootstrap.ROOT / "src" / "reportgen" / "web" / "assistant.py"
                    ).read_text(encoding="utf-8")
        self.assertIn('max_tokens=prepared["profile"]["max_tokens"]', источник)
        # И нигде в выдаче ответа не осталось прежнего общего потолка.
        выдача = источник[источник.index("    def ask("):источник.index("    # -- внутреннее")]
        self.assertNotIn("max_tokens=self._max_tokens()", выдача)


class ПереключательВИнтерфейсе(unittest.TestCase):
    """Кнопка должна быть на экране, иначе режимом никто не воспользуется."""

    def setUp(self):
        self.js = (_bootstrap.ROOT / "src" / "reportgen" / "web" / "static"
                   / "app.js").read_text(encoding="utf-8")

    def test_переключатель_нарисован(self):
        self.assertIn("buildModeSwitch", self.js)
        self.assertIn("'Разбор'", self.js)
        self.assertIn("'Быстро'", self.js)

    def test_выбор_уходит_на_сервер(self):
        self.assertIn("{ mode: value }", self.js)

    def test_у_кнопок_есть_пояснение(self):
        # На экране два слова; чем они отличаются — должно быть во всплывающей
        # подсказке, иначе выбор делается наугад.
        self.assertIn("Несколько заходов поиска", self.js)
        self.assertIn("Один поиск и короткий ответ", self.js)

    def test_неудача_возвращает_прежний_выбор(self):
        # Кнопка отзывается сразу, до ответа сервера. Если запрос не прошёл,
        # экран не должен врать о выбранном режиме.
        self.assertIn("chat.current.mode = прежний", self.js)

    def test_переключатель_обновляется_при_смене_разговора(self):
        голова = self.js[self.js.index("function renderTalkHead()"):]
        self.assertIn("renderModeSwitch()", голова[:600])

    def test_режимы_описаны_одинаково_на_обоих_концах(self):
        for режим in CHAT_MODES:
            with self.subTest(режим=режим):
                self.assertIn("id: '%s'" % режим, self.js)


class РежимЧерезHTTP(AssistantHttpTests):
    """Переключатель ходит по той же дороге, что и браузер."""

    def test_режим_переключается_запросом(self):
        chat = self.client.post("/api/chats", json={}).json()["chat"]
        self.assertEqual("deep", chat["mode"])
        ответ = self.client.patch("/api/chats/%d" % chat["id"], json={"mode": "fast"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual("fast", ответ.json()["chat"]["mode"])
        # И это сохранилось, а не показалось.
        снова = self.client.get("/api/chats/%d" % chat["id"]).json()["chat"]
        self.assertEqual("fast", снова["mode"])

    def test_чужой_режим_отклоняется(self):
        chat = self.client.post("/api/chats", json={}).json()["chat"]
        ответ = self.client.patch("/api/chats/%d" % chat["id"], json={"mode": "турбо"})
        self.assertEqual(400, ответ.status_code, ответ.text)
        снова = self.client.get("/api/chats/%d" % chat["id"]).json()["chat"]
        self.assertEqual("deep", снова["mode"])

    def test_правка_названия_не_сбрасывает_режим(self):
        # Переименование идёт тем же запросом PATCH и режима не касается.
        chat = self.client.post("/api/chats", json={}).json()["chat"]
        self.client.patch("/api/chats/%d" % chat["id"], json={"mode": "fast"})
        self.client.patch("/api/chats/%d" % chat["id"], json={"title": "Про FCS"})
        снова = self.client.get("/api/chats/%d" % chat["id"]).json()["chat"]
        self.assertEqual("fast", снова["mode"])
        self.assertEqual("Про FCS", снова["title"])

    def test_в_быстром_режиме_ответ_приходит(self):
        # Самое главное: режим не ломает сам ответ.
        chat = self.client.post("/api/chats", json={"domain": "signal"}).json()["chat"]
        self.client.patch("/api/chats/%d" % chat["id"], json={"mode": "fast"})
        ответ = self.client.post("/api/chats/%d/ask" % chat["id"], json={"text": "предел EVM"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        разговор = self.client.get("/api/chats/%d" % chat["id"]).json()
        self.assertEqual(2, len(разговор["messages"]))
