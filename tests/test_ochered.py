"""Двое спрашивают одновременно: ответ каждого — только его.

llama-server запущен с «--parallel 1»: одно окно, одна очередь. Второй
вопрос ждёт, пока модель допишет первый, и получает то же окно целиком —
поэтому ответ от очереди не беднеет. Это держит сервер.

Но клиент модели в приложении ОДИН на всех, и на нём хранилось, чем кончилась
«последняя» генерация (сама или по потолку) и сколько токенов занял
«последний» промпт. Пока один ответ дописывается, сбор материала для второго
вопроса обращается к той же модели — и перезаписывал эти поля. Первый ответ
забирал чужое: пропадала плашка «ответ оборван по длине», хотя он оборван, и
замер в «Метриках» показывал чужой вопрос.

Здесь это проверяется на настоящем клиенте (OpenAICompatLLM) с поддельным
llama-server на уровне HTTP: чужой запрос вклинивается ровно между концом
генерации и её учётом — так, как это бывает, когда спрашивают двое.
"""

import json
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import reportgen.llm as модуль_llm
from reportgen.config import Settings
from reportgen.llm import OpenAICompatLLM
from reportgen.store import Database, Repositories
from reportgen.web.assistant import AssistantService
from reportgen.web.service import ReportService
from test_assistant import fill_library

ROOT = Path(__file__).resolve().parents[1]


class Ответ:
    """Ответ сервера: целиком (read) или построчно (итерация, как SSE)."""

    status = 200

    def __init__(self, тело=None, строки=()):
        self._тело = тело
        self._строки = [строка.encode("utf-8") for строка in строки]

    def read(self):
        return json.dumps(self._тело).encode("utf-8")

    def __iter__(self):
        return iter(self._строки)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def sse(кусок) -> str:
    return "data: " + json.dumps(кусок, ensure_ascii=False) + "\n"


class Сервер:
    """Поддельный llama-server: окно, счёт токенов, ответы и поток."""

    def __init__(self):
        self.поток_обрыв = "length"
        self.поток_промпт = 11111
        #: Поток, оборванный посреди размышления Qwen3: «<think>» открыт и не
        #: закрыт, потолок кончился раньше, чем модель начала отвечать.
        self.на_мысли = False

    def __call__(self, request, timeout=None):
        адрес = request.full_url
        if адрес.endswith("/props"):
            return Ответ({"default_generation_settings": {"n_ctx": 32768}})
        if адрес.endswith("/tokenize"):
            текст = json.loads(request.data.decode("utf-8"))["content"]
            return Ответ({"tokens": [0] * max(1, len(текст) // 3)})
        тело = json.loads(request.data.decode("utf-8"))
        if тело.get("stream") and self.на_мысли:
            return Ответ(строки=[
                sse({"choices": [{"delta": {"content": "<think>сверяю G.704 с"}}]}),
                sse({"choices": [{"delta": {"content": " G.706"},
                                  "finish_reason": "length"}]}),
                "data: [DONE]\n",
            ])
        if тело.get("stream"):
            # Последний кусок несёт и текст, и причину конца — так бывает у
            # llama.cpp, и между ними у генератора есть точка уступки.
            # Расход приходит раньше последнего куска: иначе чужой запрос
            # не успел бы его перезаписать, и проверка ловила бы удачу.
            return Ответ(строки=[
                sse({"choices": [{"delta": {"content": "Ответ [S1]"}}]}),
                sse({"choices": [], "usage": {"prompt_tokens": self.поток_промпт,
                                              "completion_tokens": 7000}}),
                sse({"choices": [{"delta": {"content": " — таблица полей"},
                                  "finish_reason": self.поток_обрыв}]}),
                "data: [DONE]\n",
            ])
        return Ответ({"choices": [{"message": {"content": "ХВАТИТ"},
                                   "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 222, "completion_tokens": 3}})


class ДвоеСпрашиваютTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = Settings.load(data_dir=tmp.name, db_path=":memory:",
                                      templates_dir=str(ROOT / "templates"),
                                      llm_kind="openai",
                                      llm_base_url="http://127.0.0.1:8000/v1")
        self.repos = Repositories(Database(":memory:"))
        fill_library(self.repos, domain="signal")
        self.reports = ReportService(repos=self.repos, settings=self.settings)
        self.помощник = AssistantService(reports=self.reports)
        self.иванов = self.repos.users.create("ivanov", "пароль123", "Иванов", "engineer")
        self.сервер = Сервер()
        было = модуль_llm._http.urlopen
        модуль_llm._http.urlopen = self.сервер
        self.addCleanup(setattr, модуль_llm._http, "urlopen", было)
        self.assertIsInstance(self.reports.get_llm(), OpenAICompatLLM)

    def спросить_пока_другой_вклинивается(self):
        """Первый ответ дописан; до его учёта модель зовёт чужой вопрос."""
        разговор = self.помощник.create_chat(self.иванов)
        поток = self.помощник.ask_stream(self.иванов, разговор.id,
                                          "Как устроен цикл E1?")
        кусков = 0
        for событие in поток:
            if событие["type"] == "delta":
                кусков += 1
                if кусков == 2:
                    # Чужой вопрос в это время собирает материал.
                    self.reports.get_llm().complete("Ты ведёшь разбор", "чужой")
            if событие["type"] == "done":
                return событие
        self.fail("ответ не завершился")

    def test_обрыв_по_длине_не_теряется_из_за_чужого_запроса(self):
        готово = self.спросить_пока_другой_вклинивается()
        self.assertEqual("length", готово["answer"]["meta"]["cut"],
                         "плашка «оборван по длине» пропала: взят чужой итог")

    def test_ложного_обрыва_не_бывает(self):
        self.сервер.поток_обрыв = "stop"
        разговор = self.помощник.create_chat(self.иванов)
        # Чужой вопрос оборвался по потолку — это не наше дело.
        поток = self.помощник.ask_stream(self.иванов, разговор.id,
                                          "Как устроен цикл E1?")
        кусков = 0
        for событие in поток:
            if событие["type"] == "delta":
                кусков += 1
                if кусков == 2:
                    self.reports.get_llm().последний_обрыв = "length"
            if событие["type"] == "done":
                self.assertEqual("stop", событие["answer"]["meta"]["cut"])
                return
        self.fail("ответ не завершился")

    def test_замер_в_метриках_свой_а_не_чужой(self):
        self.спросить_пока_другой_вклинивается()
        замер = self.reports.model_budget()["measured"]
        self.assertEqual(11111, замер.get("prompt_tokens"),
                         f"в «Метриках» чужой промпт: {замер}")

    def test_обрыв_на_мысли_в_своём_итоге(self):
        """Потолок кончился посреди размышления — ответа не было вовсе."""
        self.сервер.на_мысли = True
        итог = {}
        self.assertEqual([], [кусок for кусок in self.reports.get_llm().stream(
            "с", "в", итог=итог) if кусок.strip()])
        self.assertEqual("размышление", итог["обрыв"])

    def test_итог_пишется_в_свой_словарь(self):
        """Клиент кладёт итог вызова туда, куда велели, а не только себе."""
        клиент = self.reports.get_llm()
        итог = {}
        self.assertEqual("ХВАТИТ", клиент.complete("с", "в", итог=итог))
        self.assertEqual("stop", итог["обрыв"])
        self.assertEqual(222, итог["расход"]["prompt_tokens"])
        итог_потока = {}
        list(клиент.stream("с", "в", итог=итог_потока))
        self.assertEqual("length", итог_потока["обрыв"])
        self.assertEqual(11111, итог_потока["расход"]["prompt_tokens"])


class ОчередьНеБеднитОтветTests(unittest.TestCase):
    """Окно разговора при одном слоте — целиком, а потолок ответа — разумный.

    Потолок ответа растёт на остаток окна (см. test_tochnyi_schet). Но в
    очереди он же — время, которое ждёт следующий: зациклившаяся модель с
    потолком в двадцать тысяч токенов держала бы очередь отдела десять
    минут. Поэтому рост ограничен полутора настройками.
    """

    def test_рост_потолка_ограничен(self):
        from test_tochnyi_schet import Основа  # noqa: PLC0415

        class Проба(Основа):
            def runTest(self_):
                pass

        проба = Проба()
        проба.setUp()
        try:
            проба.модель.окно = 60000
            проба.settings.llm_context_tokens = 60000
            проба.reports._llm_context_tokens = None
            проба.settings.assistant_max_tokens = 4000
            prepared = проба.готово()
            self.assertEqual(6000, prepared["answer_tokens"],
                             "потолок вырос сверх полутора настроек")
        finally:
            проба.tearDown()

    def test_по_умолчанию_один_слот(self):
        скрипт = (ROOT / "scripts" / "windows" / "start-llm.ps1").read_text(
            encoding="utf-8-sig")
        self.assertIn("[int]$Parallel   = 1", скрипт)
        self.assertIn("'--parallel', $Parallel", скрипт)
        запуск = (ROOT / "scripts" / "windows" / "start-all.ps1").read_text(
            encoding="utf-8-sig")
        self.assertNotIn("-Parallel", запуск,
                         "общий запуск переопределяет число слотов")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
