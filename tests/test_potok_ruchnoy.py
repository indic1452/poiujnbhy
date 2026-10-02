"""Ручной разбор на столе против автомата: те же данные, те же функции — тот же итог.

Оператор идёт по шагам через API стола (как браузер): период, плоскость, код, скремблер.
Каждый шаг «Как в автомате» (``/as-auto``) берёт параметры из узла (модуляция из имени
файла, блок данных ТКБ, принятый период) и отдаёт шаги стола; цепочка шагов на синтетике
даёт ровно то, что снимает автомат. Вид массива (период, первый бит, порядок, выравнивание)
— свой у каждого узла и хранится на сервере.
"""

import re
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import ploskost, razbor, rastr, skrembler
from reportgen.potok.zadaniya import унаследованный_вид, чистый_вид
from test_potok_blok import НАЧАЛЬНОЕ, ОТВОДЫ, лрп
from test_potok_tpc3 import ЧУЖИЕ, comtech
from test_web import WebTestCase

ОБРАЗЕЦ = Path(__file__).resolve().parents[1].parent / "250_V_8085_8PSK_7556_K2964.bit"


def hdlc_биты(пакетов=60, флагов=40, сид=2):
    кадры = [с.ethernet(п) for п in с.пакеты_ip(пакетов, сид=сид)]
    return np.unpackbits(np.frombuffer(с.hdlc(кадры, флагов_между=флагов), np.uint8))


def как_comtech(пакетов=60, флагов=300, сид=1):
    """Как образец K2964, без ошибок линии: (байты файла младшим битом вперёд, данные HDLC под ПСП)."""
    биты = hdlc_биты(пакетов, флагов, сид)
    F = len(биты) // 2223
    данные = биты[:F * 2223].reshape(F, 2223) ^ лрп(ОТВОДЫ, НАЧАЛЬНОЕ, 2223)
    поток, _ = comtech(данные.reshape(F, 39, 57), приставка=1001)
    поток = поток[:len(поток) // 3 * 3]
    метки = ploskost.преобразовать_фм(поток, 3, **ЧУЖИЕ)
    return np.packbits(метки, bitorder="little").tobytes(), биты[:F * 2223]


class Стол(WebTestCase):
    def setUp(self):
        super().setUp()
        self.login("engineer")
        self.сид = self.client.post("/api/sessions", json={"name": "ручной разбор"}).json()["id"]

    def дождаться(self, ид):
        for _ in range(6000):
            состояние = self.client.get(f"/api/potok/{ид}").json()
            if состояние["состояние"] in ("готово", "ошибка"):
                self.assertEqual("готово", состояние["состояние"], состояние.get("ошибка"))
                return состояние
            time.sleep(0.05)
        self.fail("не дождались")

    def файл(self, имя, данные, порядок="auto"):
        ответ = self.client.post(f"/api/sessions/{self.сид}/files", data={"bit_order": порядок},
                                 files={"file": (имя, данные, "application/octet-stream")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид = ответ.json()["id"]
        self.дождаться(ид)
        return ид

    def шаг(self, ид, шаг, **доп):
        ответ = self.client.post(f"/api/potok/{ид}/as-auto", json={"stage": 0, "step": шаг, **доп})
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()

    def снять(self, ид, слои):
        ответ = self.client.post(f"/api/potok/{ид}/derive", json={
            "stage": 0, "analyze": False, "steps": [{"вид": "слой", "слой": сл, "вкл": True} for сл in слои]})
        self.assertEqual(200, ответ.status_code, ответ.text)
        новый = ответ.json()["id"]
        return новый, self.дождаться(новый)

    def биты(self, ид):
        ответ = self.client.get(f"/api/potok/{ид}/raw")
        return np.unpackbits(np.frombuffer(ответ.content, np.uint8))[:int(ответ.headers["X-Total-Bits"])]


class РучнойКакАвтомат(Стол):
    def test_comtech_по_шагам(self):
        """Плоскость → ТКБ → ПСП блока вручную: данные HDLC точно, как у автомата; кадр ТКБ — 2964, не сверхкадр."""
        данные, hdlc = как_comtech()
        ид = self.файл("250_V_8PSK_K2964.bit", данные)
        # Скремблер на сыром потоке — рано: подсказка о кадрах, а не «не найдено» молча.
        рано = self.шаг(ид, "скремблер")
        self.assertEqual([], рано["найдено"])
        self.assertIn("кадры со служебным словом", рано["подсказка"])
        # Цикл — кадр 2964 (синхрослово чередуется), оператор принимает его длиной строки.
        цикл = self.шаг(ид, "цикл")
        self.assertEqual(2964, цикл["сведения"]["кадр"])
        вид = self.client.put(f"/api/potok/{ид}/view", json={"stage": 0, "view": {
            "ширина": 2964, "сдвиг": цикл["сведения"]["первый_бит"], "принят": True}}).json()["view"]
        self.assertTrue(вид["принят"])
        # Поиск ТКБ в окне: кадр — по служебным словам (2964), кратные ему длины не пробуются.
        кадры = self.client.post(f"/api/potok/{ид}/tkb/search", json={"stage": 0, "с": 0, "по": 1}).json()["кадры"]
        self.assertEqual(2964, кадры[0])
        self.assertFalse([к for к in кадры[1:] if к % 2964 == 0], кадры)
        # Плоскость: модуляция — из имени файла (8PSK), кадр — принятый период.
        п = self.шаг(ид, "плоскость")
        self.assertEqual([3], п["параметры"]["фм"])
        self.assertEqual(2964, п["параметры"]["кадр"])
        self.assertEqual(["плоскость", "код"], [н["уровень"] for н in п["найдено"]])
        # Файл записан младшим битом вперёд, а при загрузке порядок определился как «старший» (маркер
        # сверхцикла виден и так): автомат пробует оба порядка — шаг «как в автомате» тоже, «реверс 8» первым.
        плоскость = [сл for сл in п["слои"] if not сл.startswith("tpc")]
        self.assertEqual("реверс 8", плоскость[0])
        self.assertTrue(плоскость[-1].startswith("моддекодер 3:"), плоскость)
        self.assertIn("порядке бит", п["подсказка"])
        ид2, _ = self.снять(ид, плоскость)
        # Ребёнок после моддекодера наследует вид (строка та же).
        вид2 = self.client.get(f"/api/potok/{ид2}/view").json()["view"]
        self.assertEqual((2964, True), (вид2["ширина"], вид2["принят"]))
        self.assertIn(ид, вид2["откуда"])
        т = self.шаг(ид2, "ткб")
        self.assertEqual(1, len(т["слои"]))
        self.assertIn("без скремблера", т["слои"][0])
        ид3, состояние3 = self.снять(ид2, т["слои"])
        self.assertEqual(2223, состояние3["свойства"]["данных_в_блоке"])
        # После кода вид не наследуется: строка другая.
        self.assertEqual({}, self.client.get(f"/api/potok/{ид3}/view").json()["view"])
        # Скремблер: блок данных — из свойств узла (как автомат за кодом).
        скр = self.шаг(ид3, "скремблер")
        self.assertEqual(2223, скр["параметры"]["блок"])
        self.assertEqual(["аддитивный блок 2223"], скр["слои"])
        self.assertIn("1 + x^-2 + x^-3 + x^-9 + x^-12", скр["найдено"][0]["что"])
        self.assertEqual(НАЧАЛЬНОЕ, скр["найдено"][0]["свойства"]["начальное"])
        ид4, _ = self.снять(ид3, скр["слои"])
        итог = self.биты(ид4)
        self.assertTrue(np.array_equal(hdlc[:len(итог)], итог[:len(hdlc)]))
        self.assertGreaterEqual(len(итог), len(hdlc) - 3 * 2223)     # последние кадры без запаса — как у автомата
        # Под кадрами — HDLC, как у автомата.
        self.assertIn("HDLC", rastr.инструмент(итог, "кадры").что)
        # Слой ТКБ без «без скремблера» снимает и ПСП: поиск скремблера тогда объясняет, почему пусто.
        ид5, _ = self.снять(ид2, [т["слои"][0].replace(" без скремблера", "")])
        пусто = self.шаг(ид5, "скремблер")
        self.assertEqual([], пусто["найдено"])
        self.assertIn("уже видна", пусто["подсказка"])

    def test_слои_находок_повторяют_автомат(self):
        """Шаги стола из находок автомата (razbor.слои_находки) дают те же данные, что этапы автомата."""
        данные, _ = как_comtech(пакетов=40, флагов=200, сид=3)
        р = razbor.разобрать(данные=данные, имя="x_8PSK_K2964.bit", профиль="обычно")
        self.assertEqual(["цикл", "плоскость", "код", "скремблер"], [н.уровень for н in р.находки[:4]])
        биты = np.unpackbits(np.frombuffer(rastr.развернуть_биты(данные), np.uint8))
        for находка in р.находки[1:4]:
            for слой in razbor.слои_находки(находка):
                биты, _ = razbor.снять_вручную(биты, слой)
            if находка.дальше is None:
                continue                    # у плоскости данные отдаёт код в кадрах (следующая находка)
            ждём = np.asarray(находка.дальше, dtype=np.uint8)
            self.assertTrue(np.array_equal(ждём, биты), находка.что)

    def test_hdlc_самосинхронизирующийся(self):
        hdlc = hdlc_биты()
        поток = с.скремблировать(hdlc, (12, 17))
        ид = self.файл("h.bin", np.packbits(поток).tobytes(), "msb")
        скр = self.шаг(ид, "скремблер")
        self.assertEqual(["скремблер 12,17"], скр["слои"])
        ид2, _ = self.снять(ид, скр["слои"])
        авто = razbor.разобрать(данные=np.packbits(поток).tobytes(), имя="h.bin")
        self.assertEqual("скремблер", авто.находки[0].уровень)
        self.assertTrue(np.array_equal(np.asarray(авто.находки[0].дальше, np.uint8), self.биты(ид2)))
        # Инструмент растра «скремблер» — та же находка, и слой для снятия при ней.
        найдено = self.client.post(f"/api/potok/{ид}/tool", json={"tool": "скремблер"}).json()["found"]
        self.assertIn("слой для снятия: скремблер 12,17", найдено["подробно"])

    def test_свёрточный_и_аддитивный(self):
        hdlc = hdlc_биты()
        псп = skrembler.псп((14, 15), np.array([1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0], np.uint8), len(hdlc))
        поток = с.свёрточный(hdlc ^ псп)
        ид = self.файл("c.bin", np.packbits(поток).tobytes(), "msb")
        код = self.шаг(ид, "код")
        self.assertEqual(["свёрточный 171/133 K=7"], код["слои"])
        ид2, _ = self.снять(ид, код["слои"])
        скр = self.шаг(ид2, "скремблер")
        self.assertEqual(["аддитивный отводы 14,15"], скр["слои"])
        ид3, _ = self.снять(ид2, скр["слои"])
        авто = razbor.разобрать(данные=np.packbits(поток).tobytes(), имя="c.bin")
        self.assertEqual(["код", "скремблер"], [н.уровень for н in авто.находки[:2]])
        self.assertTrue(np.array_equal(np.asarray(авто.находки[0].дальше, np.uint8), self.биты(ид2)))
        self.assertTrue(np.array_equal(np.asarray(авто.находки[1].дальше, np.uint8), self.биты(ид3)))

    def test_ошибки_запроса(self):
        ид = self.файл("r.bin", np.packbits(с.случайные_биты(20000)).tobytes(), "msb")
        for тело in ({"step": "что-то"}, {"step": "скремблер", "block": "x"}, {"step": "скремблер", "block": 10},
                     {"step": "ткб", "frame": 3}):
            with self.subTest(тело=тело):
                self.assertEqual(400, self.client.post(f"/api/potok/{ид}/as-auto", json=тело).status_code)
        плоскость = self.шаг(ид, "плоскость")
        self.assertIn("модуляцию", плоскость["подсказка"])
        self.login("gruppa")
        self.assertEqual(404, self.client.post(f"/api/potok/{ид}/as-auto", json={"step": "цикл"}).status_code)

    @unittest.skipUnless(ОБРАЗЕЦ.exists(), "нет образца 250_V_8085_8PSK_7556_K2964.bit")
    def test_образец_k2964(self):
        """Образец пользователя: ручная цепочка «как в автомате» — те же данные, что у автомата."""
        сырые = ОБРАЗЕЦ.read_bytes()
        ид = self.файл(ОБРАЗЕЦ.name, сырые)
        период = self.client.post(f"/api/potok/{ид}/period-search", json={}).json()["items"][0]
        self.assertEqual(2964, период["период"])
        self.client.put(f"/api/potok/{ид}/view", json={"view": {"ширина": 2964, "сдвиг": период["первый_бит"], "принят": True}})
        п = self.шаг(ид, "плоскость")
        ид2, _ = self.снять(ид, [сл for сл in п["слои"] if not сл.startswith("tpc")])
        т = self.шаг(ид2, "ткб")
        ид3, _ = self.снять(ид2, т["слои"])
        скр = self.шаг(ид3, "скремблер")
        self.assertEqual(["аддитивный блок 2223"], скр["слои"])
        ид4, _ = self.снять(ид3, скр["слои"])
        вручную = self.биты(ид4)
        # Автомат — на первом мегабайте, как он и разбирает: плоскость и код в кадрах, ПСП блока.
        биты = np.unpackbits(np.frombuffer(rastr.развернуть_биты(сырые), np.uint8))[:razbor.ВЫБОРКА_БИТ]
        from reportgen.potok import modem
        находки, данные, _ = modem.разобрать_кадры(биты, 5928, фм=(3,))
        авто = np.asarray(skrembler.по_блоку(np.asarray(данные, np.uint8), 2223).дальше, np.uint8)
        m = len(авто) - 3 * 2223
        self.assertTrue(np.array_equal(авто[:m], вручную[:m]))


class ВидМассива(Стол):
    def test_у_каждого_узла_свой_и_общий_в_сессии(self):
        к = self.client
        а = self.файл("a.bin", bytes(range(256)) * 64, "msb")
        б = self.файл("b.bin", bytes(range(256)) * 64, "msb")
        self.assertEqual({}, к.get(f"/api/potok/{а}/view").json()["view"])
        к.put(f"/api/potok/{а}/view", json={"stage": 0, "view": {"ширина": 2964, "сдвиг": 2037 + 2964, "принят": True,
                                                                "порядок": "младший", "лишнее": 1,
                                                                "строкиПо": {"слово": "1111010110", "ошибок": 1}}})
        к.put(f"/api/potok/{б}/view", json={"stage": 0, "view": {"ширина": 5928, "сдвиг": 3}})
        вид = к.get(f"/api/potok/{а}/view").json()["view"]
        self.assertEqual((2964, 2037, True, "младший"), (вид["ширина"], вид["сдвиг"], вид["принят"], вид["порядок"]))
        self.assertEqual({"слово": "1111010110", "ошибок": 1, "неБлиже": 0}, вид["строкиПо"])
        self.assertNotIn("лишнее", вид)
        self.assertEqual(("оператор", "Инженеров И. И."), (вид["откуда"], вид["изменил_имя"]))
        self.assertEqual(5928, к.get(f"/api/potok/{б}/view").json()["view"]["ширина"])     # соседний файл — свой
        self.assertEqual({}, к.get(f"/api/potok/{а}/view", params={"stage": 1}).json()["view"])
        # Вторая сессия того же пользователя — свой файл и свой вид.
        другая = к.post("/api/sessions", json={"name": "другая"}).json()["id"]
        в = к.post(f"/api/sessions/{другая}/files", data={"bit_order": "msb"},
                   files={"file": ("a.bin", bytes(range(256)) * 64, "application/octet-stream")}).json()["id"]
        self.дождаться(в)
        self.assertEqual({}, к.get(f"/api/potok/{в}/view").json()["view"])
        # Участник сессии видит и правит вид; чужой — нет.
        участник = self.repos.users.by_login("zam").id
        к.patch(f"/api/sessions/{self.сид}", json={"members": [участник]})
        self.login("zam")
        self.assertEqual(2964, к.get(f"/api/potok/{а}/view").json()["view"]["ширина"])
        к.put(f"/api/potok/{а}/view", json={"view": {"ширина": 1482, "принят": True}})
        self.login("gruppa")
        self.assertEqual(404, к.get(f"/api/potok/{а}/view").status_code)
        self.assertEqual(404, к.put(f"/api/potok/{а}/view", json={"view": {"ширина": 8}}).status_code)
        self.login("engineer")
        вид = к.get(f"/api/potok/{а}/view").json()["view"]
        self.assertEqual((1482, "Заместителев З. З."), (вид["ширина"], вид["изменил_имя"]))
        # Неверное — 400; пустой — сбросить.
        for плохо in ({"ширина": 0}, {"ширина": "x"}, {"ширина": True}, {"сдвиг": -1}, [1],
                      {"ширина": 8, "строкиПо": {"слово": "01", "ошибок": "x"}}):
            with self.subTest(плохо=плохо):
                self.assertEqual(400, к.put(f"/api/potok/{а}/view", json={"view": плохо}).status_code)
        self.assertEqual({}, к.put(f"/api/potok/{а}/view", json={"view": {}}).json()["view"])
        self.assertEqual({}, к.get(f"/api/potok/{а}/view").json()["view"])

    def test_наследование_веткой(self):
        к = self.client
        а = self.файл("a.bin", bytes(range(256)) * 64, "msb")
        к.put(f"/api/potok/{а}/view", json={"view": {"ширина": 100, "сдвиг": 30, "принят": True}})

        def ветка(*слои):
            ид, _ = self.снять(а, list(слои))
            return к.get(f"/api/potok/{ид}/view").json()["view"]

        обрезка = ветка("обрезка начало 50 конец 0")
        self.assertEqual((100, 80, True), (обрезка["ширина"], обрезка["сдвиг"], обрезка["принят"]))
        self.assertEqual((100, 30), (ветка("инверсия")["ширина"], ветка("инверсия")["сдвиг"]))
        self.assertEqual({}, ветка("свёрточный 171/133 K=7"))                # после кода строка другая
        маска = к.post(f"/api/potok/{а}/derive", json={"steps": [{"вид": "маска", "маска": {"период": 100, "позиции": [1, 2]}}]}).json()["id"]
        self.дождаться(маска)
        self.assertEqual({}, к.get(f"/api/potok/{маска}/view").json()["view"])
        # Вид ребёнка — свой: правка не трогает родителя.
        ид, _ = self.снять(а, ["инверсия"])
        к.put(f"/api/potok/{ид}/view", json={"view": {"ширина": 7}})
        self.assertEqual(100, к.get(f"/api/potok/{а}/view").json()["view"]["ширина"])

    def test_чистый_и_унаследованный(self):
        self.assertEqual({}, чистый_вид(None))
        self.assertEqual({"ширина": 10, "сдвиг": 3}, чистый_вид({"ширина": 10, "сдвиг": 13}))
        self.assertEqual({"ширина": 10, "принят": True}, чистый_вид({"ширина": "10", "принят": 1}))
        self.assertEqual({"принят": False}, чистый_вид({"принят": True}))   # без ширины принимать нечего
        self.assertEqual({"ширина": 1 << 20}, чистый_вид({"ширина": 1 << 20}))
        with self.assertRaises(ValueError):
            чистый_вид({"ширина": (1 << 20) + 1})
        self.assertEqual({"кадрыВид": "16", "вид": "HEX"}, чистый_вид({"кадрыВид": "16", "вид": "HEX", "порядок": "любой"}))
        вид = {"ширина": 100, "сдвиг": 30, "принят": True, "строкиПо": {"слово": "01", "ошибок": 0, "неБлиже": 0}}
        слой = lambda т, вкл=True: {"вид": "слой", "слой": т, "вкл": вкл}  # noqa: E731
        self.assertEqual(80, унаследованный_вид(вид, [слой("обрезка начало 50 конец 7")])["сдвиг"])
        self.assertNotIn("строкиПо", унаследованный_вид(вид, [слой("сдвиг 5")]))
        self.assertEqual(25, унаследованный_вид(вид, [слой("сдвиг 5")])["сдвиг"])
        self.assertEqual(вид, унаследованный_вид(вид, [слой("ткб кадр 2964", вкл=False), слой("моддекодер 3: 0 1 2 3 4 5 6 7")]))
        self.assertEqual(вид, унаследованный_вид(вид, [слой("аддитивный блок 2223")]))
        self.assertEqual({}, унаследованный_вид(вид, [слой("аддитивный отводы 14,15")]))   # снятое — с места паузы
        self.assertEqual({}, унаследованный_вид(вид, [{"вид": "разметка", "действие": "взять", "разметка": {}}]))
        self.assertEqual({}, унаследованный_вид({}, [слой("инверсия")]))

    def test_удаление_этапов_уносит_вид(self):
        к = self.client
        а = self.файл("a.bin", bytes(range(256)) * 64, "msb")
        задания = self.app.state.potok if hasattr(self.app.state, "potok") else None
        к.put(f"/api/potok/{а}/view", json={"stage": 0, "view": {"ширина": 9}})
        if задания is None:
            self.skipTest("хранилище заданий недоступно")
        задания.записать_вид(а, 2, {"ширина": 5})
        from reportgen.potok.zadaniya import _без_этапов_с, ВИД_ФАЙЛ
        _без_этапов_с(задания.папка / а / ВИД_ФАЙЛ, 1)
        self.assertEqual({}, задания.вид(а, 2))
        self.assertEqual(9, задания.вид(а, 0)["ширина"])


class СлоиНаходок(unittest.TestCase):
    """Находка автомата → шаги стола: прямые эталоны по свойствам."""

    def н(self, уровень, **свойства):
        from reportgen.potok.nahodka import Находка
        return Находка(уровень=уровень, что="x", уверенность=1.0, мера="", свойства=свойства)

    def test_скремблеры(self):
        сл = razbor.слои_находки
        self.assertEqual(["аддитивный блок 2223"], сл(self.н("скремблер", длина_блока=2223, отводы=[2, 3, 9, 12])))
        self.assertEqual(["скремблер 12,17"], сл(self.н("скремблер", вид="самосинхронизирующийся", отводы=[12, 17])))
        self.assertEqual(["аддитивный отводы 14,15"], сл(self.н("скремблер", вид="аддитивный", отводы=[14, 15], место=3)))
        self.assertEqual([], сл(self.н("скремблер", вид="аддитивный")))
        self.assertEqual([], сл(self.н("скремблер", вид="другой", отводы=[1, 2])))
        self.assertEqual([], сл(self.н("канальный", отводы=[1, 2])))

    def test_коды(self):
        сл = razbor.слои_находки
        self.assertEqual(["ткб режим Comtech-3/4 кадр 2964 фаза 2055 без скремблера"],
                         сл(self.н("код", режим="Comtech-3/4", кадр=2964, фаза=2055, бит_укор=None, метки="")))
        self.assertEqual(["ткб режим R кадр 100 фаза 5 укорочение 0 метки 100 20 3 без скремблера"],
                         сл(self.н("код", режим="R", кадр=100, фаза=5, бит_укор=0, метки="метки 100 20 3")))
        self.assertEqual(["tpc строка 64 столбец 46 блок 0 двумерный начало 18 кадр 2964 строк 46 с бита 2037 без скремблера"],
                         сл(self.н("код", строка=64, столбец=46, блок=0, глубина=0, кадр=2964, начало_в_кадре=18,
                                   строк_в_кадре=46, первый_кадр=2037)))
        self.assertEqual(["tpc строка 16 столбец 16 блок 2 глубина 16 плоскость 1 трёхмерный начало 123 без скремблера"],
                         сл(self.н("код", строка=16, столбец=16, блок=2, глубина=16, плоскость=1, начало=123)))
        self.assertEqual(["tpc строка 8 столбец 8 блок 0 двумерный начало 0 кадр 100 строк 9 без скремблера"],
                         сл(self.н("код", строка=8, столбец=8, кадр=100, строк_в_кадре=9)))
        self.assertEqual(["свёрточный 171/133 K=7"], сл(self.н("код", свёрточный=[0o171, 0o133], K=7, инверсия=[0, 0])))
        self.assertEqual([], сл(self.н("код", свёрточный=[0o171, 0o133], K=7, инверсия=[0, 1])))
        self.assertEqual([], сл(self.н("код", n=7, k=4)))

    def test_плоскость(self):
        сл = razbor.слои_находки
        вариант = {"порядок": "старший", "код": "натуральный", "поворот": 3, "отражение": True, "метка": [0, 1, 2],
                   "код_выход": "Грей"}
        self.assertEqual(["моддекодер 3: 2 3 1 0 4 5 7 6 (как автомат: плоскость по коду в кадрах)"],
                         сл(self.н("плоскость", вариант=вариант, фаза=0, k=3)))
        слои = сл(self.н("плоскость", вариант={"порядок": "старший", "код": "Грей", "поворот": 1, "отражение": False,
                                               "метка": [0, 1], "код_выход": "Грей"}, фаза=1, k=2))
        self.assertEqual("обрезка начало 1 конец 0", слои[0])
        # Таблица — та же, что преобразование плоскости над каждым символом.
        таблица = [int(ч) for ч in слои[1].split(":")[1].split("(")[0].split()]
        символы = np.array([0, 1, 2, 3] * 3, dtype=np.uint8)
        биты = ((символы[:, None] >> np.array([1, 0])) & 1).astype(np.uint8).reshape(-1)
        ждём = ploskost.преобразовать_фм(биты, 2, порядок="старший", код="Грей", поворот=1, отражение=False,
                                         метка=[0, 1], код_выход="Грей")
        self.assertEqual(ждём.tolist(), [int(б) for v in символы for б in ((таблица[v] >> 1) & 1, таблица[v] & 1)])
        self.assertEqual([], сл(self.н("плоскость", k=3)))

    def test_простые_свойства(self):
        self.assertEqual({"a": 1, "b": 2.5, "c": "x", "d": True, "e": 7},
                         razbor.простые_свойства({"a": 1, "b": 2.5, "c": "x", "d": True, "e": np.int64(7),
                                                  "f": [1], "g": np.bool_(True), "h": None}))
        self.assertIsInstance(razbor.простые_свойства({"e": np.int64(7)})["e"], int)
        self.assertEqual({}, razbor.простые_свойства(None))

    def test_аддитивный_блок(self):
        g = np.random.default_rng(4)
        данные = g.integers(0, 2, (40, 2223), dtype=np.uint8)
        данные[:, 200:1400] = np.tile(np.array([0, 1, 1, 1, 1, 1, 1, 0], np.uint8), 150)   # флаги — заполнение
        поток = (данные ^ лрп(ОТВОДЫ, НАЧАЛЬНОЕ, 2223)).reshape(-1)
        снято, запись = razbor.снять_вручную(поток, "аддитивный блок 2223")
        self.assertTrue(np.array_equal(данные.reshape(-1), снято))
        self.assertIn("со сбросом в начале каждого блока 2223", запись.подробно[0])
        with self.assertRaises(ValueError):
            razbor.снять_вручную(поток, "аддитивный блок 63")
        with self.assertRaises(ValueError):
            razbor.снять_вручную(g.integers(0, 2, 40 * 2223, dtype=np.uint8), "аддитивный блок 2223")


class СтраницаСтола(unittest.TestCase):
    def test_вид_в_браузере_без_периода(self):
        js = (Path(__file__).resolve().parents[1] / "src/reportgen/web/static/app.js").read_text(encoding="utf-8")
        # Общий ключ вида больше не несёт длину строки; предпочтения — с ключом пользователя.
        запись = js[js.index("сохранитьСтола('stol-view', {"):]
        self.assertNotIn("ширина: с.ширина", запись[:запись.index("});")])
        self.assertNotIn("['ширина', 'масштаб'", js)
        self.assertIn("return ключ + '@' + (кто || '');", js)
        for кусок in ("'/view?stage='", "'/view', тело", "'/as-auto'", "'Как в автомате'", "' без скремблера'",
                      "с.принят = true; просмотр.применитьШирину(); запомнитьВидУзла(); }", "frame: с.принят ? с.ширина : 0"):
            self.assertIn(кусок, js)
        self.assertTrue(re.search(r"с\.ширина = вид_ \? вид_\.ширина : ШИРИНА_ПО_УМОЛЧАНИЮ;", js))


if __name__ == "__main__":
    unittest.main()
