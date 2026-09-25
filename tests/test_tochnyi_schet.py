# -*- coding: utf-8 -*-
"""Окно модели заполняется до края, и найденное читается всё.

Замер на настоящем токенизаторе Qwen (llama.cpp, словарь qwen2) и настоящей
нарезке библиотеки показал три потери сразу.

1. Бюджет окна считался по оценке «полтора знака на токен», а живой текст
   даёт 2,0–2,9 на русском и 2,6–8 на английском стандарте. Промпт занимал
   52 % отведённого ему места; 12 350 токенов пустовали, а 13 из 18
   найденных фрагментов уходили в выписки — то есть в пересказ.
2. Найденное за все заходы разбора сводилось и обрезалось до восемнадцати.
   Что заходы находили сверх этого, исчезало молча — ни целиком, ни выпиской.
3. Выписке на триста слов давали «слов × 2» токенов при настоящих 2,6 на
   слово: она обрывалась на двухсот тридцатом слове. А выписку длиннее
   отведённого места выбрасывали целиком, вместе со всем, что в ней нашлось.

После правки на том же замере: 48 найдено, 27 целиком, 21 выпиской, окно
занято на 98 %. Здесь это проверяется на учебной библиотеке с поддельным,
но детерминированным счётчиком токенов.
"""

import json
import math
import unittest

import _bootstrap  # noqa: F401
from reportgen.corpus import Chunk
from reportgen.llm import OpenAICompatLLM
from reportgen.prompts import ASSISTANT_SYSTEM_PROMPT, DIGEST_SYSTEM_PROMPT
from reportgen.web import assistant as модуль
from reportgen.web.assistant import TOKEN_SAFETY, _render_sources
from test_assistant import AssistantTestCase

ВОПРОС = "Как измеряется занимаемая полоса частот?"

#: Живой текст — около 2,5 знака на токен (замер: 2,0–2,9 у русского).
ЗНАКОВ_НА_ТОКЕН = 2.5


def счёт(текст: str) -> int:
    return max(1, math.ceil(len(текст) / ЗНАКОВ_НА_ТОКЕН))


class Модель:
    """Сервер модели: называет окно, считает токены, записывает обращения."""

    name = "проба"

    def __init__(self, окно: int, *, считает: bool = True, выписка: str = ""):
        self.окно = окно
        self.считает = считает
        self.выписка = выписка or (
            "Занимаемая полоса измеряется методом 99 процентов мощности [S1]. ") * 8
        self.обращения = []
        self.проб_счёта = 0
        self.сбой_выписки = False

    def context_tokens(self, timeout: float = 3.0) -> int:
        return self.окно

    def count_tokens(self, text: str, timeout: float = 10.0) -> int:
        if text == "проба счёта":
            self.проб_счёта += 1
        return счёт(text) if self.считает else 0

    def complete(self, system, user, **kwargs):
        self.обращения.append((system, user, kwargs))
        if system == DIGEST_SYSTEM_PROMPT:
            if self.сбой_выписки:
                raise OSError("модель не ответила")
            return self.выписка
        if system.startswith("Ты называешь") or system.startswith("Ты сверяешь"):
            return "нет"
        if system.startswith("Ты ведёшь разбор"):
            return "ХВАТИТ"
        return "Ответ [S1]."

    def stream(self, system, user, **kwargs):
        yield self.complete(system, user, **kwargs)

    def выписки(self):
        return [(user, kwargs) for system, user, kwargs in self.обращения
                if system == DIGEST_SYSTEM_PROMPT]

    def ответ(self):
        return [(user, kwargs) for system, user, kwargs in self.обращения
                if system == ASSISTANT_SYSTEM_PROMPT][-1]


class Основа(AssistantTestCase):
    #: Своя библиотека, без учебной: иначе поиск делит выдачу между ней и
    #: набитым материалом, и сколько найдено — зависит не от проверяемого.
    with_library = False

    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)
        self.settings.assistant_context_chars = 0
        self.settings.llm_context_tokens = 14000
        self.settings.assistant_max_tokens = 500
        self.settings.assistant_rounds = 0
        self.набить()
        self.модель = Модель(14000)
        self.reports.get_llm = lambda: self.модель

    def набить(self, документов: int = 10, кусков: int = 4):
        текст = ("Занимаемая полоса частот измеряется методом 99 процентов "
                 "мощности по спектру сигнала; спектр оценивается методом "
                 "периодограмм с усреднением по сегментам. ") * 12
        for номер in range(документов):
            doc_id = f"lib/polosa{номер}"
            документ = self.repos.documents.upsert(
                doc_id, "literature", f"Измерение полосы, часть {номер}",
                f"/lib/{doc_id}.pdf", "sha-полоса-" + str(номер),
                meta={}, domain="signal")
            self.repos.chunks.replace_for_document(документ, [Chunk(
                chunk_id=f"{doc_id}#{кусок}", doc_id=doc_id,
                doc_type="literature",
                title_path=[f"Измерение полосы, часть {номер}", f"Раздел {кусок}"],
                text=f"Раздел {кусок}. " + текст, meta={}) for кусок in range(кусков)])

    def готово(self, **kw):
        return self.assistant._prepare(self.ivanov, self.chat.id, ВОПРОС,
                                       top_k=None, **kw)

    def токенов_запроса(self, prepared) -> int:
        return (счёт(ASSISTANT_SYSTEM_PROMPT) + счёт(prepared["prompt"])
                + sum(счёт(item["content"]) for item in prepared["history"]))


class ОкноДоКраяTests(Основа):
    """Счёт сервером против оценки: сколько найденного модель видит целиком."""

    def test_точный_счёт_кладёт_целиком_больше(self):
        точно = len(self.готово()["sources"])
        self.модель.считает = False
        self.reports._llm_counts = None
        по_оценке = len(self.готово()["sources"])
        self.assertGreater(точно, по_оценке,
                           "по точному счёту целиком вошло не больше, чем по "
                           "оценке, — окно по-прежнему пустует")

    def test_промпт_не_вылезает_за_окно(self):
        prepared = self.готово()
        предел = (self.settings.llm_context_tokens
                  - self.settings.assistant_max_tokens - TOKEN_SAFETY)
        self.assertLessEqual(self.токенов_запроса(prepared), предел)

    def test_промпт_занимает_окно_почти_целиком(self):
        """Недобор не больше одного фрагмента и места под выписки."""
        prepared = self.готово()
        предел = (self.settings.llm_context_tokens
                  - self.settings.assistant_max_tokens - TOKEN_SAFETY)
        фрагмент = max(счёт(_render_sources([item])) for item in prepared["sources"])
        пусто = предел - self.токенов_запроса(prepared)
        self.assertLess(пусто, фрагмент + self.assistant._digest_room_tokens(),
                        f"в окне пустует {пусто} токенов")

    def test_ответу_отдан_остаток_окна(self):
        prepared = self.готово()
        ждём = (self.settings.llm_context_tokens - self.токенов_запроса(prepared)
                - TOKEN_SAFETY)
        потолок = self.settings.assistant_max_tokens
        # Остаток — но не больше полутора настроек: в очереди отдела потолок
        # одного ответа — это время, которое ждёт следующий.
        self.assertEqual(max(потолок, min(ждём, int(потолок * 1.5))),
                         prepared["answer_tokens"])
        self.assertGreater(prepared["answer_tokens"], потолок,
                           "остаток окна ответу не достался")

    def test_остаток_доходит_до_модели(self):
        prepared = self.готово()
        self.assistant._complete(prepared)
        _, kwargs = self.модель.ответ()
        self.assertEqual(prepared["answer_tokens"], kwargs["max_tokens"])

    def test_ответ_не_короче_настройки(self):
        """Настройка — гарантия ответу, а не предложение."""
        self.settings.llm_context_tokens = 4096
        self.модель.окно = 4096
        self.reports._llm_context_tokens = None
        prepared = self.готово()
        self.assertGreaterEqual(prepared["answer_tokens"],
                                self.settings.assistant_max_tokens)

    def test_в_быстром_режиме_потолок_не_растёт(self):
        self.assistant.update(self.ivanov, self.chat.id, mode="fast")
        prepared = self.готово()
        self.assertEqual(prepared["profile"]["max_tokens"], prepared["answer_tokens"])


class МераПополамTests(Основа):
    """_fit_exact сам по себе: наибольшее число, что входит, и уступки."""

    def кандидаты(self, сколько=12):
        hits = self.assistant._collect(self.chat, ВОПРОС, [], сколько)[0]
        sources, за_бюджетом = self.assistant._build_sources(hits)
        return sources + за_бюджетом

    def собрать(self, куски, карточки):
        from reportgen.web.assistant import _render_map

        return _render_map(карточки) + "\n" + _render_sources(куски, карточки)

    def test_берётся_наибольшее_число_что_входит(self):
        кандидаты = self.кандидаты()
        место = sum(счёт(_render_sources([item])) for item in кандидаты) // 2
        оставлено, _, prompt, снято = self.assistant._fit_exact(
            кандидаты, self.собрать, history=[], место=место)
        self.assertTrue(снято, "всё вошло — проверять нечего")
        self.assertLessEqual(счёт(ASSISTANT_SYSTEM_PROMPT) + счёт(prompt), место)
        # На один больше — уже не входит.
        больше = кандидаты[:len(оставлено) + 1]
        карточки = self.assistant._document_cards(больше, оглавления=False)
        self.assertGreater(счёт(ASSISTANT_SYSTEM_PROMPT)
                           + счёт(self.собрать(больше, карточки)), место)
        self.assertEqual(кандидаты, оставлено + снято, "порядок выдачи нарушен")

    def test_оглавления_уступают_раньше_фрагментов(self):
        кандидаты = self.кандидаты()
        без = self.assistant._document_cards(кандидаты, оглавления=False)
        с = self.assistant._document_cards(кандидаты)
        self.assertTrue(any(card["outline"] for card in с), "оглавлений нет вовсе")
        место = (счёт(ASSISTANT_SYSTEM_PROMPT) + счёт(self.собрать(кандидаты, без)))
        оставлено, карточки, _, снято = self.assistant._fit_exact(
            кандидаты, self.собрать, history=[], место=место)
        self.assertEqual([], снято, "снят фрагмент, хотя хватало убрать оглавления")
        self.assertFalse(any(card["outline"] for card in карточки))

    def test_последний_фрагмент_остаётся_всегда(self):
        кандидаты = self.кандидаты()
        оставлено, _, _, _ = self.assistant._fit_exact(
            кандидаты, self.собрать, history=[], место=10)
        self.assertEqual(1, len(оставлено))

    def test_без_счёта_решает_оценка(self):
        self.модель.считает = False
        self.assertIsNone(self.assistant._fit_exact(
            self.кандидаты(), self.собрать, history=[], место=5000))


class СчётчикTests(Основа):
    def test_умение_спрашивается_один_раз(self):
        for _ in range(3):
            self.assertIsNotNone(self.assistant._счётчик())
        self.assertEqual(1, self.модель.проб_счёта)

    def test_неумение_переспрашивается_через_минуту(self):
        """Сервер модели поднимают после приложения — «не умеет» не навсегда."""
        self.модель.считает = False
        часы = [1000.0]
        было = модуль.time.monotonic
        модуль.time.monotonic = lambda: часы[0]
        try:
            self.assertIsNone(self.assistant._счётчик())
            self.модель.считает = True
            self.assertIsNone(self.assistant._счётчик(), "переспросили сразу же")
            часы[0] += модуль.COUNT_RETRY_SECONDS + 1
            self.assertIsNotNone(self.assistant._счётчик())
        finally:
            модуль.time.monotonic = было

    def test_сбой_счёта_посреди_работы_уводит_на_оценку(self):
        self.assertIsNotNone(self.assistant._счётчик())
        self.модель.считает = False
        self.assertIsNone(self.assistant._токенов("текст", []))

    def test_модель_без_счёта_работает_по_оценке(self):
        class Старая(Модель):
            count_tokens = None

        self.reports.get_llm = lambda: Старая(14000)
        self.assertIsNone(self.assistant._счётчик())
        self.assertTrue(self.готово()["sources"])

    def test_метрики_говорят_какой_счёт(self):
        self.assertTrue(self.reports.model_budget()["counts_exact"])
        self.модель.считает = False
        self.reports._llm_counts = None
        self.assertFalse(self.reports.model_budget()["counts_exact"])


class ЧитаетсяВсёНайденноеTests(Основа):
    """Прежде всё сверх assistant_top_k терялось после слияния заходов."""

    def test_читается_больше_чем_top_k(self):
        self.settings.assistant_top_k = 6
        self.settings.assistant_read_limit = 30
        prepared = self.готово()
        self.assertGreater(prepared["hits"], 6,
                           "найденное снова обрезано до top_k")
        self.assertLessEqual(prepared["hits"], 30)

    def test_предел_не_ниже_top_k(self):
        self.settings.assistant_top_k = 12
        self.settings.assistant_read_limit = 4
        self.assertEqual(12, self.assistant._profile(self.chat)["read_limit"])

    def test_быстрый_режим_читает_только_свои_восемь(self):
        self.assistant.update(self.ivanov, self.chat.id, mode="fast")
        chat = self.assistant.get_chat(self.ivanov, self.chat.id)
        self.assertEqual(8, self.assistant._profile(chat)["read_limit"])

    def test_всё_найденное_либо_целиком_либо_выпиской(self):
        self.settings.assistant_top_k = 6
        self.settings.assistant_read_limit = 40
        prepared = self.готово()
        целиком = len(prepared["sources"])
        выпиской = len(prepared["digest_sources"])
        self.assertGreater(выпиской, 0, "выписок не было — проверять нечего")
        self.assertEqual(prepared["hits"], целиком + выпиской,
                         f"найдено {prepared['hits']}, а дошло {целиком} + "
                         f"{выпиской}: остальное пропало")
        self.assertNotIn("не прочитаны", prepared["warning"] or "")


class ВыпискиПоОкнуTests(Основа):
    def setUp(self):
        super().setUp()
        self.settings.assistant_top_k = 6
        self.settings.assistant_read_limit = 40

    def test_проход_берёт_столько_сколько_входит_в_окно(self):
        prepared = self.готово()
        проходы = self.модель.выписки()
        выписано = len(prepared["digest_sources"])
        self.assertGreater(выписано, 6, "мало материала для проверки")
        self.assertLess(len(проходы), math.ceil(выписано / 6),
                        "проходов столько же, сколько по шесть фрагментов")
        for user, kwargs in проходы:
            with self.subTest(проход=user[:60]):
                self.assertLessEqual(
                    счёт(DIGEST_SYSTEM_PROMPT) + счёт(user) + kwargs["max_tokens"],
                    self.settings.llm_context_tokens - TOKEN_SAFETY,
                    "проход разбора не влезает в окно")

    def test_заданная_порция_соблюдается(self):
        self.settings.assistant_digest_chunk = 2
        self.settings.assistant_digest_passes = 50
        prepared = self.готово()
        self.assertEqual(math.ceil(len(prepared["digest_sources"]) / 2),
                         len(self.модель.выписки()))

    def test_порция_по_окну_и_без_счёта_сервера(self):
        self.модель.считает = False
        self.reports._llm_counts = None
        prepared = self.готово()
        self.assertGreater(len(prepared["digest_sources"]),
                           6 * len(self.модель.выписки()) // 2,
                           "по оценке проход снова берёт по шесть фрагментов")

    def test_потолок_выписки_по_настоящему_счёту_слов(self):
        """«Слов × 2» обрывал выписку на двухсот тридцатом слове из трёхсот."""
        self.готово()
        user, kwargs = self.модель.выписки()[0]
        слов = int(user.rsplit("Не длиннее ", 1)[1].split()[0])
        self.assertGreaterEqual(kwargs["max_tokens"], слов * 2.6,
                                "потолка не хватит на столько слов")

    def test_меньше_проходов_длиннее_выписка(self):
        """Место под выписки рассчитано на все проходы — пустовать ему незачем."""
        self.settings.assistant_digest_passes = 6
        self.готово()
        проходы = self.модель.выписки()
        self.assertLess(len(проходы), 6, "понадобились все проходы — проверять нечего")
        user, _ = проходы[0]
        слов = int(user.rsplit("Не длиннее ", 1)[1].split()[0])
        задано = self.settings.assistant_digest_words
        self.assertEqual(6 * задано // len(проходы), слов)
        self.assertGreater(слов, задано)

    def test_сбойный_проход_не_выдаётся_за_прочитанный(self):
        self.модель.сбой_выписки = True
        prepared = self.готово()
        self.assertIn("не прочитаны", prepared["warning"] or "",
                      f"сбой выдан за чтение: {prepared['warning']!r}")
        self.assertNotIn("выключен настройкой", prepared["warning"] or "")

    def test_пустые_выписки_названы_правдой(self):
        """Проходы были, но относящегося не нашлось — это не «разбор выключен»."""
        self.модель.выписка = "по вопросу здесь ничего нет"
        prepared = self.готово()
        замечание = prepared["warning"] or ""
        self.assertIn("не нашлось", замечание)
        self.assertNotIn("выключен настройкой", замечание)

    def test_без_места_модель_не_зовут_впустую(self):
        self.settings.assistant_digest_chunk = 2
        self.settings.assistant_digest_passes = 50
        self.settings.assistant_digest_words = 40
        self.модель.выписка = "Полоса по 99 процентам мощности [S1]. " * 60
        prepared = self.готово()
        проходов = len(self.модель.выписки())
        частей = math.ceil((prepared["hits"] - len(prepared["sources"])) / 2)
        self.assertLess(проходов, частей, "модель звали и без места под выписку")
        self.assertIn("не прочитаны", prepared["warning"] or "")


class ВыпискаСтрокамиTests(unittest.TestCase):
    """Проход берёт больше фрагментов — пересказ «там описаны поля» теряет их."""

    def test_таблицы_переносятся_строками(self):
        self.assertIn("Таблицы и перечни переноси СТРОКАМИ", DIGEST_SYSTEM_PROMPT)
        self.assertIn("«поле — разрядность — значения [S12]»", DIGEST_SYSTEM_PROMPT)

    def test_выписка_по_прежнему_без_размышления(self):
        self.assertTrue(DIGEST_SYSTEM_PROMPT.endswith("/no_think"))


class ТемператураОтветаTests(Основа):
    def test_по_умолчанию_как_велит_карточка_qwen3(self):
        prepared = self.готово()
        self.assistant._complete(prepared)
        self.assertEqual(0.6, self.модель.ответ()[1]["temperature"])

    def test_ноль_законное_значение(self):
        self.settings.assistant_temperature = 0.0
        prepared = self.готово()
        self.assistant._complete(prepared)
        self.assertEqual(0.0, self.модель.ответ()[1]["temperature"])

    def test_поток_берёт_ту_же_температуру_и_потолок(self):
        self.settings.assistant_temperature = 0.45
        события = list(self.assistant.ask_stream(self.ivanov, self.chat.id, ВОПРОС))
        _, kwargs = self.модель.ответ()
        self.assertEqual(0.45, kwargs["temperature"])
        self.assertGreater(kwargs["max_tokens"], self.settings.assistant_max_tokens)
        self.assertEqual("done", события[-1]["type"])


class КлиентСчитаетTests(unittest.TestCase):
    """OpenAICompatLLM.count_tokens: POST /tokenize в корне сервера."""

    def спросить(self, тело=None, ошибка=None, текст="поток E1"):
        клиент = OpenAICompatLLM(base_url="http://127.0.0.1:8000/v1")
        видели = {}

        class Ответ:
            status = 200

            def read(self_):
                return json.dumps(тело).encode("utf-8")

            def __enter__(self_):
                return self_

            def __exit__(self_, *_):
                return False

        import reportgen.llm as llm

        было = llm._http.urlopen

        def подмена(request, timeout=None):
            видели["url"] = request.full_url
            видели["тело"] = json.loads(request.data.decode("utf-8"))
            видели["метод"] = request.get_method()
            if ошибка:
                raise ошибка
            return Ответ()

        llm._http.urlopen = подмена
        try:
            return клиент.count_tokens(текст), видели
        finally:
            llm._http.urlopen = было

    def test_число_токенов_со_слов_сервера(self):
        число, видели = self.спросить({"tokens": [1, 2, 3, 4]})
        self.assertEqual(4, число)
        self.assertEqual("http://127.0.0.1:8000/tokenize", видели["url"])
        self.assertEqual("POST", видели["метод"])
        self.assertEqual({"content": "поток E1"}, видели["тело"])

    def test_сбой_даёт_ноль(self):
        self.assertEqual(0, self.спросить(ошибка=OSError("нет сервера"))[0])

    def test_чужой_ответ_даёт_ноль(self):
        for тело in ({}, {"tokens": "много"}, [1, 2]):
            with self.subTest(тело=тело):
                self.assertEqual(0, self.спросить(тело)[0])


class ОтборТокеновTests(unittest.TestCase):
    """Отбор по карточке Qwen3 доходит до сервера — и только если задан."""

    def тело(self, **поля):
        клиент = OpenAICompatLLM(**поля)
        return клиент._payload("система", "вопрос", 100, 0.6)

    def test_заданное_уходит_в_запрос(self):
        тело = self.тело(top_k=20, top_p=0.95, min_p=0.0)
        self.assertEqual(20, тело["top_k"])
        self.assertEqual(0.95, тело["top_p"])
        self.assertEqual(0.0, тело["min_p"], "ноль потерян как «пусто»")

    def test_незаданное_решает_сервер(self):
        тело = self.тело()
        for поле in ("top_k", "top_p", "min_p"):
            self.assertNotIn(поле, тело)

    def test_служба_берёт_отбор_из_настроек(self):
        from reportgen.config import Settings
        from reportgen.store import Database, Repositories
        from reportgen.web.service import ReportService

        settings = Settings.load(db_path=":memory:", llm_kind="openai",
                                 llm_top_k=33, llm_top_p=0.9, llm_min_p=0.05)
        reports = ReportService(repos=Repositories(Database(":memory:")),
                                settings=settings)
        llm = reports.get_llm()
        self.assertEqual((33, 0.9, 0.05), (llm.top_k, llm.top_p, llm.min_p))

    def test_по_умолчанию_как_в_карточке_qwen3(self):
        from reportgen.config import Settings

        settings = Settings.load(db_path=":memory:")
        self.assertEqual((20, 0.95, 0.0),
                         (settings.llm_top_k, settings.llm_top_p, settings.llm_min_p))
        self.assertEqual(0.6, settings.assistant_temperature)


class МетрикиTests(unittest.TestCase):
    def test_карточка_говорит_какой_счёт(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[1] / "src" / "reportgen" / "web"
              / "static" / "app.js").read_text(encoding="utf-8")
        карточка = js.split("function modelCard(model)", 1)[1].split("\n    }", 1)[0]
        self.assertIn("'Счёт токенов'", карточка)
        self.assertIn("model.counts_exact", карточка)
        self.assertIn("замер.answer_limit", карточка)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
