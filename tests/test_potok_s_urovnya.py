"""Разбор «с уровня»: на верхнем уровне поиски уровней раньше выбранного не проводятся, ниже — как обычно.

Поток с известным строением (potok_sintez): пропуск ранних уровней не мешает найти поздний, пропущенные
детекторы на самом потоке не зовутся (слежка за вызовами), а «неизвестно» — ровно прежний разбор.
"""

import json
import shutil
import subprocess
import time
import unittest
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import dmr, kod, razbor, skrembler, разобрать
from reportgen.potok.bity import в_байты, в_биты


def скремблированный_hdlc() -> bytes:
    """IP → HDLC → скремблер V.35: цепочка «скремблер → канальный → сетевой»."""
    return в_байты(с.скремблировать(в_биты(с.hdlc(с.пакеты_ip(60), флагов_между=4)), (3, 20)))


def следить(модуль, имя: str, длины: list):
    """Подменить детектор: длины рядов, на которых его звали, — в ``длины``; ответ — настоящий."""
    настоящий = getattr(модуль, имя)

    def обёртка(выборка, *args, **kwargs):
        длины.append(len(выборка))
        return настоящий(выборка, *args, **kwargs)
    return mock.patch.object(модуль, имя, обёртка)


class СУровняTests(unittest.TestCase):
    def test_со_скремблера_та_же_цепочка_без_ранних_поисков(self):
        данные = скремблированный_hdlc()
        циклы, коды, радио = [], [], []
        настоящий = razbor._по_циклу

        def по_циклу(выборка, глубина, путь, *args, **kwargs):
            циклы.append(путь)
            return настоящий(выборка, глубина, путь, *args, **kwargs)
        with mock.patch.object(razbor, "_по_циклу", по_циклу), следить(kod, "найти", коды), \
                следить(dmr, "найти", радио):
            разбор = разобрать(данные=данные, имя="запись.bin", с_уровня="скремблер")
        self.assertEqual(["скремблер", "канальный", "сетевой"], [н.уровень for н in разбор.находки])
        # На самом потоке не искались ни цикл, ни радиопротоколы, ни коды…
        верх = len(данные) * 8
        self.assertNotIn("", циклы)
        self.assertNotIn(верх, радио)
        self.assertNotIn(верх, коды)
        # …а ниже скремблера разбор шёл как обычно: радиопротоколы там пробовались, кадры HDLC нашлись.
        self.assertTrue(радио)
        self.assertEqual("после снятия скремблера", разбор.находки[1].путь)
        # В отчёте — одной строкой, и о непроведённых поисках не сказано «нет».
        self.assertTrue(разбор.ограничения[0].startswith("начато с уровня «скремблер» — на верхнем уровне поиски "
                                                         "модуляционной плоскости, кадра"))
        верхние = [с_ for с_ in разбор.не_найдено if "после снятия скремблера" not in с_]
        for чего_нет in ("цикл и синхрослово", "PLHEADER", "DMR", "TETRA", "код Рида", "свёрточный"):
            self.assertFalse([с_ for с_ in верхние if чего_нет in с_], чего_нет)
        self.assertIn("начато с уровня «скремблер»", разбор.отчёт())

    def test_неизвестно_как_прежде(self):
        данные = в_байты(в_биты(с.hdlc(с.пакеты_ip(40), флагов_между=4)))
        прежний = разобрать(данные=данные, имя="запись.bin")
        for уровень in ("неизвестно", None, "", 0):
            разбор = разобрать(данные=данные, имя="запись.bin", с_уровня=уровень)
            self.assertEqual([н.что for н in прежний.находки], [н.что for н in разбор.находки])
            self.assertEqual(прежний.не_найдено, разбор.не_найдено)
            self.assertEqual(прежний.другие, разбор.другие)
            self.assertEqual(прежний.ограничения, разбор.ограничения)
        # Детекторы уровня без «с уровня» — тот же список (не копия): порядок и состав прежние.
        б = razbor.Бюджет(конец=time.monotonic() + 60)
        детекторы = razbor._детекторы(np.zeros(1 << 14, np.uint8), б, 0, "")
        self.assertIs(детекторы, razbor._по_уровню(детекторы, б, 0, ""))
        self.assertEqual(frozenset(), razbor._мимо(б, 0, ""))

    def test_кадры_данных_и_пакеты(self):
        hdlc = в_байты(в_биты(с.hdlc(с.пакеты_ip(40), флагов_между=4)))
        разбор = разобрать(данные=hdlc, имя="запись.bin", с_уровня="кадры данных")
        self.assertEqual(["канальный", "сетевой"], [н.уровень for н in разбор.находки])
        # С уровня «пакеты» кадров уже не ищут: в кадрах HDLC пакетов подряд нет — и найденного нет.
        разбор = разобрать(данные=hdlc, имя="запись.bin", с_уровня="пакеты")
        self.assertEqual([], разбор.находки)
        # Пакеты IP подряд — находятся и с уровня «пакеты».
        пакеты = b"".join(с.пакеты_ip(40))
        разбор = разобрать(данные=пакеты, имя="запись.bin", с_уровня="пакеты")
        self.assertEqual(["сетевой"], [н.уровень for н in разбор.находки])

    def test_шум_с_уровня_пакеты_быстро(self):
        шум = np.random.default_rng(7).integers(0, 256, 1 << 16, dtype=np.uint8).tobytes()
        скремблеры = []
        начало = time.monotonic()
        with следить(skrembler, "найти", скремблеры):
            разбор = разобрать(данные=шум, имя="шум.bin", с_уровня="пакеты")
        self.assertLess(time.monotonic() - начало, 30.0)      # без уровня — больше минуты
        self.assertEqual([], разбор.находки)
        self.assertEqual([], скремблеры)
        self.assertIn("пакеты IP подряд и MPEG-TS: нет", разбор.не_найдено)
        self.assertFalse([с_ for с_ in разбор.не_найдено if "HDLC" in с_ or "цикл" in с_])
        self.assertIn("кадров данных (HDLC, GFP, ATM", разбор.ограничения[0])

    def test_детекторы_по_уровням(self):
        б = razbor.Бюджет(конец=time.monotonic() + 60, профиль=razbor.ПРОФИЛИ["глубоко"], символ=(4,), фм=(2,))
        детекторы = razbor._детекторы(np.zeros(1 << 20, np.uint8), б, 0, "", блок=1024)
        уровни = {имя: razbor._уровень_детектора(имя) for имя, _, _ in детекторы}
        self.assertEqual({"модуляционная плоскость ФМ-4", "модуляционная плоскость КАМ-16"},
                         {и for и, у in уровни.items() if у == "плоскость"})
        self.assertEqual({"кодирование в линии (4B/5B, 8B/10B)"}, {и for и, у in уровни.items() if у == "кадры данных"})
        self.assertEqual({"скремблер", "ПСП со сбросом на каждом блоке (1024 бит)"},
                         {и for и, у in уровни.items() if у == "скремблер"})
        for имя in ("код Рида — Соломона вслепую", "свёрточный 1/2 и короткий блочный код", "ТКБ (турбокод блочный)",
                    "блочное перемежение над кодом", "свёрточное перемежение Форни (I × M)",
                    "длинный блочный код (LDPC, Рида — Соломона, БЧХ)", "LDPC по загруженным и встроенным матрицам"):
            self.assertEqual("код", уровни[имя], имя)
        # С уровня «код»: плоскости нет; «скремблер»: остались скремблер, ПСП блока и кодирование в линии.
        б.с_уровня = razbor.уровень_старта("код")
        self.assertFalse([д for д in razbor._по_уровню(детекторы, б, 0, "") if "плоскость" in д[0]])
        б.с_уровня = razbor.уровень_старта("скремблер")
        self.assertEqual({"скремблер", "ПСП со сбросом на каждом блоке (1024 бит)", "кодирование в линии (4B/5B, 8B/10B)"},
                         {д[0] for д in razbor._по_уровню(детекторы, б, 0, "")})
        # Только на верхнем уровне (и в запасном проходе «младший бит первым»): ниже и в притоках — все.
        self.assertIs(детекторы, razbor._по_уровню(детекторы, б, 1, "после снятия кода"))
        self.assertIs(детекторы, razbor._по_уровню(детекторы, б, 0, "VC-4 1"))
        self.assertTrue(razbor._мимо(б, 0, "младший бит первым"))
        # С плоскости: пропускать нечего, подбор плоскости — первым.
        б.с_уровня = razbor.уровень_старта("плоскость")
        первые = [д[0] for д in razbor._по_уровню(детекторы, б, 0, "")][:2]
        self.assertEqual(["модуляционная плоскость ФМ-4", "модуляционная плоскость КАМ-16"], первые)

    def test_значения(self):
        self.assertEqual(("неизвестно", "плоскость", "кадр", "код", "скремблер", "кадры данных", "пакеты"),
                         razbor.УРОВНИ_СТАРТА)
        self.assertEqual(3, razbor.уровень_старта("код"))
        self.assertEqual(0, razbor.уровень_старта(None))
        with self.assertRaises(ValueError):
            razbor.уровень_старта("линия")
        with self.assertRaises(ValueError):
            разобрать(данные=b"\x00" * 64, имя="x.bin", с_уровня="линия")


class СерверTests(unittest.TestCase):
    """Уровень — у задания: страница «Разбор потока», продолжение с этапа, автомат с узла, пересборка."""

    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — веб грузится только здесь

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
            if состояние["состояние"] not in ("ждёт", "идёт"):
                return состояние
            time.sleep(0.2)
        self.fail("задание не закончилось")

    def test_уровень_у_задания(self):
        from reportgen.web.api import УРОВНИ_РАЗБОРА  # noqa: PLC0415
        self.assertEqual(razbor.УРОВНИ_СТАРТА, УРОВНИ_РАЗБОРА)
        файл = {"file": ("запись.bin", скремблированный_hdlc(), "application/octet-stream")}
        self.assertEqual(400, self.к.post("/api/potok", data={"profile": "быстро", "level": "линия"},
                                          files=файл).status_code)
        ид = self.к.post("/api/potok", data={"profile": "быстро", "level": "скремблер"}, files=файл).json()["id"]
        состояние = self.дождаться(ид)
        self.assertEqual("скремблер", состояние["с_уровня"])
        self.assertEqual(["скремблер", "канальный", "сетевой"], [э["уровень"] for э in состояние["этапы"]])
        self.assertTrue(состояние["ограничения"][0].startswith("начато с уровня «скремблер»"))
        self.assertEqual("скремблер", self.к.get("/api/potok").json()["items"][0]["с_уровня"])
        # Продолжение с этапа (поток после скремблера) — со своим уровнем.
        ответ = self.к.post(f"/api/potok/{ид}/continue", json={"stage": 1, "profile": "быстро",
                                                               "level": "кадры данных"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        дальше = self.дождаться(ответ.json()["id"])
        self.assertEqual("кадры данных", дальше["с_уровня"])
        self.assertEqual(["канальный", "сетевой"], [э["уровень"] for э in дальше["этапы"]])
        self.assertEqual(400, self.к.post(f"/api/potok/{ид}/continue", json={"stage": 1, "level": "x"}).status_code)
        # Автомат с узла (стол) — уровень в запросе; без него — «неизвестно».
        ответ = self.к.post(f"/api/potok/{ид}/derive", json={"stage": 0, "steps": [], "analyze": True,
                                                             "profile": "быстро", "level": "пакеты"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        автомат = self.дождаться(ответ.json()["id"])
        self.assertEqual(("пакеты", []), (автомат["с_уровня"], автомат["этапы"]))
        копия = self.к.post(f"/api/potok/{ид}/derive", json={"stage": 0, "steps": []}).json()["id"]
        self.assertEqual("неизвестно", self.к.get(f"/api/potok/{копия}").json()["с_уровня"])
        # Пересборка (ручной разбор) хранит уровень узла.
        ответ = self.к.post(f"/api/potok/{ид}/rebuild", json={"steps": []})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual("скремблер", self.дождаться(ответ.json()["id"])["с_уровня"])


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ИнтерфейсTests(unittest.TestCase):
    """Выбор уровня рядом с профилем: страница «Разбор потока», «Продолжить с этапа», автомат на столе."""

    def test_список_и_подсказка(self):
        from test_potok_sessii import APP_JS, функции_js  # noqa: PLC0415
        js = APP_JS.read_text(encoding="utf-8")
        код = ("function h(tag, attrs, ...kids) { return { tag, attrs: attrs || {}, kids: kids.flat(9) }; }\n"
               + функции_js(["выборУровня", "сУровня"], ["УРОВНИ_РАЗБОРА", "ПОДСКАЗКА_УРОВНЯ"])
               + "\nconst в = выборУровня('код');"
               + "\nprocess.stdout.write(JSON.stringify({ уровни: УРОВНИ_РАЗБОРА, подсказка: ПОДСКАЗКА_УРОВНЯ,"
               + " значения: в.kids.map((о) => о.attrs.value), выбран: в.kids.filter((о) => о.attrs.selected).map((о) => о.attrs.value),"
               + " по: выборУровня().kids.filter((о) => о.attrs.selected).map((о) => о.attrs.value),"
               + " у: сУровня({ с_уровня: 'код' }), нет: сУровня({ с_уровня: 'неизвестно' }) + сУровня({}) }));")
        готово = subprocess.run(["node", "-e", код], capture_output=True, text=True, timeout=30)
        self.assertEqual(0, готово.returncode, готово.stderr)
        итог = json.loads(готово.stdout)
        self.assertEqual(list(razbor.УРОВНИ_СТАРТА), итог["уровни"])
        self.assertEqual(итог["уровни"], итог["значения"])
        self.assertEqual((["код"], ["неизвестно"]), (итог["выбран"], итог["по"]))
        self.assertEqual((" · с уровня «код»", ""), (итог["у"], итог["нет"]))
        self.assertLessEqual(len(итог["подсказка"]), 90)            # одной строкой
        # Автомат на столе: профиль и уровень в окне операции — те же списки, запоминаются у сессии.
        авто = js[js.index("{ id: 'a-auto'"):js.index("{ id: 'a-config'")]
        self.assertIn("'Полный автоанализ…'", авто)
        self.assertIn("выбор: ['быстро', 'обычно', 'глубоко']", авто)
        self.assertIn("выбор: [" + ", ".join(f"'{у}'" for у in итог["уровни"]) + "]", авто)
        self.assertIn("подсказка: '" + итог["подсказка"] + "'", авто)
        for кусок in ("const ключАвтомата = 'stol-avto:' + (sessionId ? 's:' + sessionId : 'j:' + jobId);",
                      "analyze: true, profile: профиль, level: уровень });",
                      "сохранитьСтола(ключАвтомата, { профиль, уровень });",
                      # Страница «Разбор потока» и «Продолжить с этапа» — рядом с профилем.
                      "form.append('level', уровень.value);", "сохранитьСтола('potok-level', уровень.value);",
                      "strip: слои.value, profile: профиль.value, level: уровень.value });",
                      "h('span', {}, 'Начать с уровня'), уровень, h('span', { class: 'muted small' }, ПОДСКАЗКА_УРОВНЯ)"):
            self.assertIn(кусок, js)
        self.assertEqual(2, js.count("h('span', {}, 'Начать с уровня'), уровень,"))


if __name__ == "__main__":
    unittest.main()
