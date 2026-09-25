# -*- coding: utf-8 -*-
"""Длинная последовательность не съедает ответ, а оборванный — продолжается.

Отдел: «она в примерах или литературе нашла огромную двоичную комбинацию и
упёрлась в неё (длина ответа). Что делать, может нужна кнопка продолжить,
как это бывает в разных чатах».

Три части.

1. Инструкция: сырые данные длиннее 64 знаков не переписывать — назвать,
   что это, длину, первые знаки и источник.
2. Сторож: если модель всё же переписывает последовательность или
   зациклилась, генерация останавливается сразу — потолок ответа это ещё и
   очередь отдела. Хвост отрезается, обрыв помечается «повтор».
3. «Продолжить ответ»: по тем же источникам, с теми же метками, в тот же
   ответ. Шов не виден: повтор последней строки и шапки таблицы убирается.
"""

import json
import random
import unittest

import _bootstrap  # noqa: F401
from reportgen.prompts import (
    ASSISTANT_SYSTEM_PROMPT,
    CONTINUE_PROMPT,
)
from reportgen.web.assistant import (
    ДВОИЧНЫХ_ПОДРЯД,
    СторожПовтора,
    _вырождение,
    _основа_продолжения,
    _сшить,
)
from reportgen.web.service import ServiceError
from test_assistant import AssistantTestCase

#: Вопрос по учебной библиотеке: на него находится несколько источников, и
#: есть что цитировать в продолжении.
ВОПРОС = "Как измеряется занимаемая полоса и какой предел EVM, фазовый шум?"
БИТЫ = "".join(random.Random(7).choice("01") for _ in range(4096))


class СторожTests(unittest.TestCase):
    def test_двоичная_последовательность_ловится(self):
        найдено = _вырождение("Последовательность 2^15−1:\n" + " ".join(
            БИТЫ[i:i + 8] for i in range(0, 1600, 8)))
        self.assertIsNotNone(найдено)
        self.assertEqual("двоичная", найдено[1])

    def test_короткая_двоичная_в_разборе_не_трогается(self):
        текст = ("FAS — комбинация `0011011` в чётных циклах [S1]. CRC-4 — "
                 + " ".join(БИТЫ[i:i + 8] for i in range(0, 256, 8)) + " [S2].")
        self.assertIsNone(_вырождение(текст))

    def test_шестнадцатеричный_дамп_ловится(self):
        дамп = " ".join(f"{random.Random(i).randint(0, 255):02x}" for i in range(900))
        self.assertEqual("шестнадцатеричная", _вырождение("Кадр: " + дамп)[1])

    def test_таблица_десятичных_чисел_не_дамп(self):
        случай = random.Random(1)
        таблица = "".join(f"| {i} | {случай.randint(0, 99999)} | {случай.random():.4f} |\n"
                          for i in range(300))
        self.assertIsNone(_вырождение("## Замеры\n" + таблица))

    def test_перечень_десятичных_чисел_не_дамп(self):
        """Длинный ряд замеров через запятую — цифры, но не шестнадцатеричный дамп."""
        случай = random.Random(3)
        ряд = ", ".join(str(случай.randint(100, 99999)) for _ in range(500))
        self.assertIsNone(_вырождение("Отсчёты: " + ряд))

    def test_длинная_строка_трижды_не_зацикливание(self):
        """Меньше четырёх повторов длинной строки — ещё не зацикливание."""
        строка = ("| Поле | " + "значение с пояснением " * 14)[:279] + "\n"
        self.assertEqual(280, len(строка))
        # Три полных повтора и почти весь четвёртый — ещё не зацикливание.
        self.assertIsNone(_вырождение("Таблица:\n" + строка * 3 + строка[:270]))
        # Пять — уже да.
        self.assertEqual("повтор", _вырождение("Таблица:\n" + строка * 5)[1])

    def test_зацикливание_ловится(self):
        найдено = _вырождение("Итог:\n" + "| КИ1 | речь | [S1] |\n" * 60)
        self.assertEqual("повтор", найдено[1])

    def test_рамка_битовой_диаграммы_не_зацикливание(self):
        рамка = ("+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+\n"
                 + "|                               |\n" * 3
                 + "+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+\n")
        self.assertIsNone(_вырождение("Заголовок:\n" + рамка * 2))

    def test_живой_текст_не_трогается(self):
        from pathlib import Path

        текст = (Path(__file__).resolve().parents[1] / "docs" / "12-assistant.md"
                 ).read_text(encoding="utf-8")
        сторож = СторожПовтора()
        for начало in range(0, len(текст), 9):
            self.assertEqual("", сторож.добавить(текст[начало:начало + 9]),
                             f"ложное срабатывание: {сторож.текст[-120:]!r}")

    def test_чистый_ответ_оставляет_начало_последовательности(self):
        сторож = СторожПовтора()
        сторож.текст = "Последовательность: " + БИТЫ[:2000]
        чистый = сторож.чистый()
        self.assertTrue(чистый.startswith("Последовательность: " + БИТЫ[:32]))
        self.assertLess(len(чистый), 200)
        self.assertTrue(чистый.endswith("…"))

    def test_проверка_не_на_каждый_знак(self):
        """Сторож смотрит раз в ШАГ знаков: иначе он дороже самой генерации."""
        from reportgen.web import assistant as модуль

        проверок = []
        настоящая = модуль._вырождение

        def счёт(текст):
            проверок.append(len(текст))
            return настоящая(текст)

        модуль._вырождение = счёт
        try:
            сторож = СторожПовтора()
            for _ in range(640):
                сторож.добавить("а")
        finally:
            модуль._вырождение = настоящая
        self.assertLessEqual(len(проверок), 10,
                             "сторож проверяет хвост на каждый знак")
        self.assertGreater(ДВОИЧНЫХ_ПОДРЯД, СторожПовтора.ШАГ)


class ИнструкцияTests(unittest.TestCase):
    def test_сырые_данные_не_переписываются(self):
        self.assertIn("СЫРЫЕ ДАННЫЕ — единственное исключение", ASSISTANT_SYSTEM_PROMPT)
        self.assertIn("длиннее 64 знаков", ASSISTANT_SYSTEM_PROMPT)

    def test_продолжение_без_размышления(self):
        self.assertTrue(CONTINUE_PROMPT.endswith("/no_think"))


class ШовTests(unittest.TestCase):
    def test_недописанная_строка_убирается(self):
        ответ = "## Поля\n| Поле | Биты |\n|---|---|\n| FAS | 0011011 |\n| NF"
        self.assertEqual("## Поля\n| Поле | Биты |\n|---|---|\n| FAS | 0011011 |",
                         _основа_продолжения(ответ, "length"))

    def test_длинный_абзац_не_теряется(self):
        абзац = "Сверхцикл " + "из шестнадцати циклов " * 30
        self.assertEqual(абзац.rstrip(), _основа_продолжения("## Итог\n" + абзац, "length")
                         .split("\n", 1)[1])

    def test_после_повтора_ничего_не_режется(self):
        self.assertEqual("Последовательность: 0101 …",
                         _основа_продолжения("Последовательность: 0101 …", "повтор"))

    def test_таблица_продолжается_без_шапки(self):
        основа = "| Поле | Биты |\n|---|---|\n| FAS | 0011011 |"
        продолжение = "| Поле | Биты |\n|---|---|\n| NFAS | бит 2 = 1 |"
        self.assertEqual(основа + "\n| NFAS | бит 2 = 1 |", _сшить(основа, продолжение))

    def test_шапка_далеко_выше_тоже_не_повторяется(self):
        строки = "\n".join(f"| КИ{номер} | речь |" for номер in range(1, 9))
        основа = "| Интервал | Назначение |\n|---|---|\n" + строки
        продолжение = "| Интервал | Назначение |\n|---|---|\n| КИ9 | речь |"
        self.assertEqual(основа + "\n| КИ9 | речь |", _сшить(основа, продолжение))

    def test_повтор_последней_строки_убирается(self):
        основа = "Первое.\n\nСверхцикл — 16 циклов [S1]."
        self.assertEqual(основа + "\n\nCRC-4 — 4 бита [S2].",
                         _сшить(основа, "Сверхцикл — 16 циклов [S1].\nCRC-4 — 4 бита [S2]."))

    def test_новый_раздел_с_новой_строки(self):
        self.assertEqual("Абзац.\n\n## Дальше",
                         _сшить("Абзац.", "## Дальше"))


class Модель:
    """Отвечает заготовкой; главный ответ — потоком, как llama-server."""

    name = "проба"

    def __init__(self):
        self.ответ = "Ответ [S1]."
        self.обрыв = "stop"
        self.обращения = []
        self.закрыт = False
        self.выдано = 0

    def complete(self, system, user, **kwargs):
        self.обращения.append((system, user, kwargs))
        if system.startswith("Ты ведёшь разбор"):
            return "ХВАТИТ"
        if system.startswith("Ты называешь") or system.startswith("Ты сверяешь"):
            return "нет"
        return self.ответ

    def stream(self, system, user, **kwargs):
        self.обращения.append((system, user, kwargs))
        итог = kwargs.get("итог")
        модель = self

        class Поток:
            """Поток с явным close(): закрытие не должно зависеть от сборщика
            мусора — соединение с llama-server надо рвать сразу."""

            def __init__(себя):
                себя.куски = [модель.ответ[начало:начало + 16]
                              for начало in range(0, len(модель.ответ), 16)]

            def __iter__(себя):
                return себя

            def __next__(себя):
                if not себя.куски:
                    if итог is not None:
                        итог["обрыв"] = модель.обрыв
                    raise StopIteration
                модель.выдано += 1
                return себя.куски.pop(0)

            def close(себя):
                модель.закрыт = True

        return Поток()

    пишет_итог = True


class Основа(AssistantTestCase):
    def setUp(self):
        super().setUp()
        self.chat = self.assistant.create_chat(self.ivanov)
        self.модель = Модель()
        self.reports.get_llm = lambda: self.модель

    def спросить(self):
        return list(self.assistant.ask_stream(self.ivanov, self.chat.id, ВОПРОС))[-1]

    def продолжить(self, message_id):
        return list(self.assistant.continue_stream(self.ivanov, self.chat.id, message_id))


class ОстановкаTests(Основа):
    def test_переписывание_останавливается_сразу(self):
        self.модель.ответ = "Последовательность 2^12: " + БИТЫ
        итог = self.спросить()
        self.assertTrue(self.модель.закрыт, "поток модели не закрыт")
        self.assertLess(self.модель.выдано, len(self.модель.ответ) // 16 // 2,
                        "модель дописывала последовательность до конца")
        self.assertEqual("повтор", итог["answer"]["meta"]["cut"])
        self.assertLess(len(итог["answer"]["content"]), 200)

    def test_целый_ответ_тоже_очищается(self):
        self.модель.ответ = "Итог:\n" + "| КИ1 | речь | [S1] |\n" * 80
        итог = self.assistant.ask(self.ivanov, self.chat.id, ВОПРОС)
        self.assertEqual("повтор", итог["answer"]["meta"]["cut"])
        self.assertLess(итог["answer"]["content"].count("| КИ1 |"), 3)

    def test_живой_ответ_не_трогается(self):
        # Разные фразы: одна и та же, повторённая сорок раз, — это и есть
        # зацикливание, и сторож прав, что его останавливает.
        self.модель.ответ = "## Цикл\n" + "".join(
            f"Канальный интервал {номер} несёт {номер * 8} бит [S1]. " for номер in range(40))
        итог = self.спросить()
        self.assertEqual("stop", итог["answer"]["meta"]["cut"])
        self.assertEqual(self.модель.ответ.strip(), итог["answer"]["content"])


class ПродолжениеTests(Основа):
    def оборванный(self, текст="## Поля\n| Поле | Биты |\n|---|---|\n| FAS | 0011011 [S1] |\n| NF",
                   обрыв="length"):
        self.модель.ответ = текст
        self.модель.обрыв = обрыв
        return self.спросить()["answer"]

    def test_продолжение_дописывается_в_тот_же_ответ(self):
        ответ = self.оборванный()
        self.модель.ответ = "| NFAS | бит 2 = 1 [S1] |\n\nДальше сверьте CRC-4."
        self.модель.обрыв = "stop"
        события = self.продолжить(ответ["id"])
        готово = события[-1]
        self.assertEqual("done", готово["type"])
        self.assertEqual(ответ["id"], готово["answer"]["id"])
        self.assertEqual(
            "## Поля\n| Поле | Биты |\n|---|---|\n| FAS | 0011011 [S1] |\n"
            "| NFAS | бит 2 = 1 [S1] |\n\nДальше сверьте CRC-4.",
            готово["answer"]["content"])
        self.assertEqual("stop", готово["answer"]["meta"]["cut"])
        self.assertEqual(1, готово["answer"]["meta"]["continued"])
        сообщения = self.repos.chats.messages(self.chat.id)
        self.assertEqual(2, len(сообщения), "продолжение легло новым сообщением")

    def test_первым_приходит_основа_без_недописанной_строки(self):
        ответ = self.оборванный()
        события = self.продолжить(ответ["id"])
        self.assertEqual("base", события[0]["type"])
        self.assertFalse(события[0]["text"].endswith("| NF"))

    def test_модель_видит_те_же_источники_и_хвост(self):
        ответ = self.оборванный()
        self.продолжить(ответ["id"])
        system, user, kwargs = self.модель.обращения[-1]
        self.assertEqual(ASSISTANT_SYSTEM_PROMPT, system)
        self.assertIn("| FAS | 0011011 [S1] |", user)
        for источник in ответ["sources"]:
            with self.subTest(метка=источник["label"]):
                self.assertIn(f"[{источник['label']}]", user)
        self.assertIn("кончился потолок длины", user)
        self.assertTrue(user.endswith("/no_think"))

    def test_после_повтора_причина_названа(self):
        ответ = self.оборванный("Последовательность 2^12: " + БИТЫ, обрыв="stop")
        self.assertEqual("повтор", ответ["meta"]["cut"])
        self.продолжить(ответ["id"])
        _, user, _ = self.модель.обращения[-1]
        self.assertIn("переписывать длинную последовательность", user)

    def test_можно_продолжать_снова(self):
        ответ = self.оборванный()
        self.модель.ответ = "| NFAS | 1 [S1] |\n| CRC"
        self.модель.обрыв = "length"
        второй = self.продолжить(ответ["id"])[-1]["answer"]
        self.assertEqual("length", второй["meta"]["cut"])
        self.модель.ответ = "| CRC-4 | 4 бита [S1] |"
        self.модель.обрыв = "stop"
        третий = self.продолжить(ответ["id"])[-1]["answer"]
        self.assertEqual(2, третий["meta"]["continued"])
        self.assertIn("| NFAS | 1 [S1] |\n| CRC-4 | 4 бита [S1] |", третий["content"])

    def test_законченный_ответ_не_продолжается(self):
        self.модель.ответ = "Ответ [S1]."
        ответ = self.спросить()["answer"]
        with self.assertRaises(ServiceError) as поймано:
            self.продолжить(ответ["id"])
        self.assertEqual(400, поймано.exception.status)

    def test_не_последний_ответ_не_продолжается(self):
        """Даже если и следующий ответ оборван: дописать надо ТОТ, что просили."""
        ответ = self.оборванный()
        self.модель.ответ = "Второй ответ, тоже оборванный [S1]. | NF"
        self.модель.обрыв = "length"
        self.спросить()
        with self.assertRaises(ServiceError):
            self.продолжить(ответ["id"])

    def test_чужой_разговор_недоступен(self):
        ответ = self.оборванный()
        with self.assertRaises(ServiceError) as поймано:
            list(self.assistant.continue_stream(self.petrov, self.chat.id, ответ["id"]))
        self.assertEqual(404, поймано.exception.status)

    def test_продолжение_тоже_под_сторожем(self):
        ответ = self.оборванный()
        self.модель.ответ = "| NFAS | 1 |\n" + БИТЫ
        итог = self.продолжить(ответ["id"])[-1]["answer"]
        self.assertEqual("повтор", итог["meta"]["cut"])
        self.assertLess(len(итог["content"]), 400)

    def test_прерванное_продолжение_сохраняется_и_продолжается_снова(self):
        ответ = self.оборванный()
        self.модель.ответ = "| NFAS | бит 2 = 1 [S1] |\n" + "Сверьте CRC-4. " * 20
        поток = self.assistant.continue_stream(self.ivanov, self.chat.id, ответ["id"])
        for событие in поток:
            if событие["type"] == "delta":
                break
        поток.close()
        сохранено = self.repos.chats.messages(self.chat.id)[-1]
        self.assertIn("| NFAS |", сохранено.content)
        self.assertEqual("length", сохранено.meta["cut"],
                         "прерванное продолжение не даст продолжить снова")

    def test_новые_ссылки_помечаются_процитированными(self):
        ответ = self.оборванный()
        метки = [item["label"] for item in ответ["sources"]]
        self.assertGreater(len(метки), 1)
        последняя = метки[-1]
        self.модель.ответ = f"| NFAS | 1 [{последняя}] |"
        self.модель.обрыв = "stop"
        итог = self.продолжить(ответ["id"])[-1]["answer"]
        процитированы = {item["label"] for item in итог["sources"] if item["cited"]}
        self.assertIn(последняя, процитированы)
        self.assertEqual(len(процитированы), итог["meta"]["cited"])


class ПродолжениеПоHttpTests(unittest.TestCase):
    """Тот же контур через HTTP: поток SSE и ошибка событием."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from fastapi.testclient import TestClient
        from reportgen.config import Settings
        from reportgen.store import Database, Repositories
        from reportgen.web.app import create_app
        from reportgen.web.service import ReportService
        from test_assistant import fill_library

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings = Settings.load(
            data_dir=tmp.name, db_path=":memory:", auth_enabled=True,
            templates_dir=str(Path(__file__).resolve().parents[1] / "templates"))
        repos = Repositories(Database(":memory:"))
        fill_library(repos)
        self.модель = Модель()
        service = ReportService(repos=repos, settings=settings, llm=self.модель)
        self.client = TestClient(create_app(settings, repos, service))
        self.addCleanup(self.client.close)
        repos.users.create("ivanov", "пароль123", "Иванов", "engineer")
        self.client.post("/api/auth/login", json={"login": "ivanov", "password": "пароль123"})

    def события(self, адрес, тело):
        with self.client.stream("POST", адрес, json=тело) as ответ:
            return [json.loads(строка[5:]) for строка in ответ.iter_lines()
                    if строка.startswith("data:")]

    def test_поток_событий(self):
        разговор = self.client.post("/api/chats", json={}).json()["chat"]
        self.модель.ответ = "| Поле | Биты |\n|---|---|\n| FAS | 0011011 |\n| NF"
        self.модель.обрыв = "length"
        оборванный = self.события(f"/api/chats/{разговор['id']}/stream",
                                  {"text": ВОПРОС})[-1]["answer"]
        self.assertEqual("length", оборванный["meta"]["cut"])
        self.модель.ответ = "| NFAS | 1 |"
        self.модель.обрыв = "stop"
        события = self.события(f"/api/chats/{разговор['id']}/continue",
                               {"message_id": оборванный["id"]})
        self.assertEqual(["base", "stage"], [с["type"] for с in события[:2]])
        self.assertEqual("done", события[-1]["type"])
        self.assertIn("| NFAS | 1 |", события[-1]["answer"]["content"])

    def test_ошибка_приходит_событием(self):
        разговор = self.client.post("/api/chats", json={}).json()["chat"]
        события = self.события(f"/api/chats/{разговор['id']}/continue",
                               {"message_id": 999})
        self.assertEqual("error", события[-1]["type"])
        self.assertIn("последний ответ", события[-1]["error"])


class КнопкаTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path

        статика = Path(__file__).resolve().parents[1] / "src" / "reportgen" / "web" / "static"
        self.js = (статика / "app.js").read_text(encoding="utf-8")

    def test_кнопка_только_у_последнего_ответа(self):
        плашка = self.js.split("function cutNote(cut, message)", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("chat.messages[chat.messages.length - 1].id", плашка)
        self.assertIn("const кнопка = последний ?", плашка)
        for обрыв in ("'length'", "'повтор'"):
            self.assertIn(f"if (cut === {обрыв})", плашка)

    def test_продолжение_идёт_на_свой_адрес_и_останавливается_стопом(self):
        функция = self.js.split("async function продолжитьОтвет(message)", 1)[1]
        функция = функция.split("\n    }\n", 1)[0]
        self.assertIn("'/continue'", функция)
        self.assertIn("message_id: message.id", функция)
        self.assertIn("signal: controller.signal", функция)
        стоп = self.js.split("function stopStreaming()", 1)[1].split("\n    }\n", 1)[0]
        self.assertIn("chat.continuing", стоп)

    def test_итог_берётся_с_сервера(self):
        функция = self.js.split("async function продолжитьОтвет(message)", 1)[1]
        функция = функция.split("\n    }\n", 1)[0]
        self.assertIn("? готово.answer : item", функция)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
