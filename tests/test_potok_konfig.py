"""Конфигурации обработки потоков: хранение, доступ, перенос, применение к потоку."""

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok.konfig import Конфигурации, НетДоступа

ШАГИ = [{"вид": "слой", "слой": "инверсия"}, {"вид": "маска", "маска": {"период": 8, "сдвиг": 0, "позиции": [0, 1, 2, 3]}}]


def без_встроенных(список):
    """Встроенные общие конфигурации (GFP) есть всегда — их проверяет test_potok_gfp."""
    return [з for з in список if not з.get("встроенная")]


class ХранилищеTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.к = Конфигурации(Path(self._tmp.name))

    def test_свои_общие_чужие(self):
        своя = self.к.сохранить(1, имя="  РРЛ   вида А ", шаги=ШАГИ, описание="кадр 200")
        self.assertEqual("РРЛ вида А", своя["имя"])
        self.assertEqual([True, True], [ш["вкл"] for ш in своя["шаги"]])
        общая = self.к.сохранить(2, имя="Общая", шаги=ШАГИ, общая=True)
        self.к.сохранить(2, имя="Личная чужая", шаги=ШАГИ)
        self.assertEqual([("РРЛ вида А", True), ("Общая", False)],
                         [(з["имя"], з["своя"]) for з in без_встроенных(self.к.список(1))])
        self.assertEqual("Общая", self.к.прочитать(общая["ид"], 1)["имя"])
        with self.assertRaises(НетДоступа):
            self.к.сохранить(1, имя="захват", шаги=ШАГИ, ид=общая["ид"])
        with self.assertRaises(НетДоступа):
            self.к.удалить(общая["ид"], 1)
        личная = [з for з in self.к.список(2) if з["имя"] == "Личная чужая"][0]
        with self.assertRaises(KeyError):
            self.к.прочитать(личная["ид"], 1)

    def test_правка_и_удаление(self):
        з = self.к.сохранить(1, имя="А", шаги=ШАГИ)
        время = з["создано"]
        з2 = self.к.сохранить(1, имя="Б", шаги=ШАГИ[:1], ид=з["ид"], общая=True)
        self.assertEqual((з["ид"], "Б", 1, True, время), (з2["ид"], з2["имя"], len(з2["шаги"]), з2["общая"], з2["создано"]))
        self.к.удалить(з["ид"], 1)
        self.assertEqual([], без_встроенных(self.к.список(1)))
        for плохой in ("../x", "zzzzzzzzzzzz", 5):
            with self.subTest(плохой=плохой), self.assertRaises(KeyError):
                self.к.прочитать(плохой, 1)

    def test_путь_за_пределы_папки_не_читается(self):
        # Файл рядом с папкой конфигураций, похожий на конфигурацию: через «../» его не достать.
        папка = Path(self._tmp.name) / "konfig"
        к = Конфигурации(папка)
        к.сохранить(1, имя="своя", шаги=ШАГИ)                    # папка есть — «..» из неё проходит
        (Path(self._tmp.name) / "чужое.json").write_text('{"владелец": 1, "имя": "секрет", "шаги": []}', encoding="utf-8")
        for ид in ("../чужое", "..%2fчужое", "0123456789ab/../../чужое"):
            with self.subTest(ид=ид), self.assertRaises(KeyError):
                к.прочитать(ид, 1)

    def test_ошибки_формы(self):
        for имя, шаги, описание in (("", ШАГИ, ""), ("x" * 81, ШАГИ, ""), ("А", [], ""),
                                    ("А", [{"вид": "что-то"}], ""), ("А", ШАГИ, "o" * 4001)):
            with self.subTest(имя=имя[:5], шаги=len(шаги)), self.assertRaises(ValueError):
                self.к.сохранить(1, имя=имя, шаги=шаги, описание=описание)

    def test_выгрузка_и_загрузка(self):
        з = self.к.сохранить(1, имя="Перенос", шаги=ШАГИ, описание="для машины 2", общая=True)
        файл = Конфигурации.выгрузка(з)
        self.assertNotIn("владелец", файл)
        новая = self.к.загрузить(7, файл, автор="Петров")
        self.assertEqual(("Перенос", "для машины 2", 2, False, 7, "Петров"),
                         (новая["имя"], новая["описание"], len(новая["шаги"]), новая["общая"],
                          новая["владелец"], новая["автор"]))
        with self.assertRaises(ValueError):
            self.к.загрузить(7, {"имя": "x", "шаги": ШАГИ})

    def test_испорченный_файл_не_роняет_список(self):
        self.к.сохранить(1, имя="Целая", шаги=ШАГИ)
        (Path(self._tmp.name) / "0123456789ab.json").write_text("{не json", encoding="utf-8")
        self.assertEqual(["Целая"], [з["имя"] for з in без_встроенных(self.к.список(1))])


class ЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client

    def дождаться(self, ид):
        for _ in range(600):
            состояние = self.к.get(f"/api/potok/{ид}").json()
            if состояние["состояние"] in ("готово", "ошибка"):
                return состояние
            time.sleep(0.1)
        self.fail("разбор не закончился")

    def test_модуляция_словами_при_загрузке(self):
        к = self.к
        файл = {"file": ("p.bin", bytes(64), "application/octet-stream")}
        ид = к.post("/api/potok", data={"profile": "быстро", "bits": "8PSK"}, files=файл).json()["id"]
        состояние = self.дождаться(ид)
        self.assertEqual(([], [3]), (состояние["символ"], состояние["фм"]))
        ид = к.post("/api/potok", data={"profile": "быстро", "bits": "КАМ-64, 4"}, files=файл).json()["id"]
        состояние = self.дождаться(ид)
        self.assertEqual(([6, 4], []), (состояние["символ"], состояние["фм"]))
        ответ = к.post("/api/potok", data={"profile": "быстро", "bits": "16APSK"}, files=файл)
        self.assertEqual(400, ответ.status_code)
        self.assertIn("8PSK", ответ.text)

    def test_весь_путь(self):
        к = self.к
        ответ = к.post("/api/potok-configs", json={"name": "Инверсия и полубайт", "steps": ШАГИ, "description": "проба"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид_к = ответ.json()["ид"]
        self.assertEqual(["Инверсия и полубайт"], [з["имя"] for з in без_встроенных(к.get("/api/potok-configs").json()["items"])])
        self.assertEqual(400, к.post("/api/potok-configs", json={"name": "", "steps": ШАГИ}).status_code)
        self.assertEqual(404, к.post("/api/potok-configs", json={"name": "x", "steps": ШАГИ, "id": "0" * 12}).status_code)
        файл = к.get(f"/api/potok-configs/{ид_к}/export")
        self.assertEqual("конфигурация обработки потока", файл.json()["вид"])
        self.assertIn('filename="config-', файл.headers["content-disposition"])
        копия = к.post("/api/potok-configs", json={"file": файл.json()}).json()
        self.assertNotEqual(ид_к, копия["ид"])

        rng = np.random.default_rng(3)
        биты = rng.integers(0, 2, 80_000).astype(np.uint8)
        ид = к.post("/api/potok", data={"profile": "быстро"},
                    files={"file": ("p.bin", bytes(np.packbits(биты)), "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        # Проба на начале потока: инверсия, затем 4 бита из каждых 8.
        проба = к.post(f"/api/potok/{ид}/try", json={"stage": 0, "config": ид_к}).json()
        ждём = (1 - биты).reshape(-1, 8)[:, :4].reshape(-1)
        self.assertEqual((80_000, 40_000), (проба["бит_на_входе"], проба["бит_на_выходе"]))
        self.assertEqual(bytes(np.packbits(ждём[:4096])).hex(), проба["начало"])
        self.assertEqual(400, к.post(f"/api/potok/{ид}/try", json={"stage": 0, "steps": [{"вид": "слой", "слой": "ерунда"}]}).status_code)
        # Проба — только начало потока.
        from unittest import mock

        from reportgen.web import api
        with mock.patch.object(api, "ПРОБА_БИТ", 8000):
            мало = к.post(f"/api/potok/{ид}/try", json={"stage": 0, "config": ид_к}).json()
        self.assertEqual((8000, 80_000, 4000), (мало["бит_на_входе"], мало["весь_поток"], мало["бит_на_выходе"]))
        # Применить к этапу — новый узел дерева.
        узел = к.post(f"/api/potok/{ид}/derive", json={"stage": 0, "config": ид_к, "analyze": False}).json()["id"]
        состояние = self.дождаться(узел)
        self.assertIn("«Инверсия и полубайт»", состояние["имя"])
        self.assertEqual(len(ШАГИ), len(состояние["шаги"]))
        # При загрузке: шаги сразу над файлом, потом автомат.
        сразу = к.post("/api/potok", data={"profile": "быстро", "config": ид_к},
                       files={"file": ("q.bin", bytes(np.packbits(биты)), "application/octet-stream")}).json()["id"]
        состояние = self.дождаться(сразу)
        self.assertEqual("готово", состояние["состояние"], состояние.get("ошибка"))
        self.assertEqual("q.bin → «Инверсия и полубайт»", состояние["имя"])
        self.assertEqual(404, к.post("/api/potok", data={"config": "0" * 12},
                                     files={"file": ("q.bin", b"\x01\x02", "application/octet-stream")}).status_code)
        # Чужой: не видит личную, не правит и не удаляет.
        self.assertEqual(200, к.delete(f"/api/potok-configs/{копия['ид']}").status_code)
        self.assertEqual(404, к.delete(f"/api/potok-configs/{копия['ид']}").status_code)
