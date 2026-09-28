"""Сессии работы с потоками, разметка бит, быстрый поиск периода и скремблера, сырые байты.

Сессия — общий стол нескольких файлов и нескольких людей: создаётся одним именем,
файлы добавляются с обрезкой, делиться вправе владелец. Битовый просмотр берёт
байты массива целиком (``/raw``) и рисует их в браузере; разметка — отрезки и
правила «бит k с периодом P» — хранится на сервере и становится шагом обработки.
"""

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
import reportgen.potok.zadaniya as модуль
from reportgen.potok import rastr, skrembler
from reportgen.potok.cikl import автокорреляция, размер_бпф
from reportgen.potok.sessii import Сессии, годный_ид
from reportgen.potok.zadaniya import Задания


def поток_с_маркером(период, маркер, n, начало=0, ошибок=0.0, зерно=5):
    g = np.random.default_rng(зерно)
    б = g.integers(0, 2, n, dtype=np.uint8)
    м = np.array([int(c) for c in маркер], dtype=np.uint8)
    for s in range(начало, n - len(м), период):
        б[s:s + len(м)] = м
    if ошибок:
        б ^= (g.random(n) < ошибок).astype(np.uint8)
    return б


class СессииTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.с = Сессии(Path(self._tmp.name) / "sessii")

    def test_создать_и_доступ(self):
        ид = self.с.создать(владелец=1, имя="  Спутник   ночь ")
        self.assertTrue(годный_ид(ид))
        сессия = self.с.прочитать(ид)
        self.assertEqual(("Спутник ночь", 1, []), (сессия["имя"], сессия["владелец"], сессия["участники"]))
        self.assertEqual(ид, self.с.для(ид, 1)["ид"])
        with self.assertRaises(KeyError):
            self.с.для(ид, 2)                                  # чужому — как будто нет
        with self.assertRaises(KeyError):
            self.с.прочитать("../../etc")
        with self.assertRaises(KeyError):
            self.с.прочитать("0123456789ab")                  # годный вид, но такой нет
        with self.assertRaises(ValueError):
            self.с.создать(владелец=1, имя="   ")
        self.assertEqual(120, len(self.с.прочитать(self.с.создать(владелец=1, имя="я" * 500))["имя"]))
        self.assertFalse(годный_ид("0123456789aB"))
        self.assertFalse(годный_ид(""))

    def test_поделиться_покинуть_удалить(self):
        ид = self.с.создать(владелец=1, имя="общая")
        self.с.изменить(ид, 1, участники=[3, 2, 2, 1])
        self.assertEqual([2, 3], self.с.прочитать(ид)["участники"])   # без владельца и повторов
        self.assertEqual([ид], self.с.доступные(2))
        with self.assertRaises(PermissionError):
            self.с.изменить(ид, 2, имя="чужое")               # участник не переименовывает
        with self.assertRaises(PermissionError):
            self.с.удалить(ид, 2)
        with self.assertRaises(PermissionError):
            self.с.покинуть(ид, 1)                            # владелец не уходит — удаляет
        self.с.покинуть(ид, 2)
        self.assertEqual([3], self.с.прочитать(ид)["участники"])
        self.assertEqual([], self.с.доступные(2))
        self.с.изменить(ид, 1, имя="новое")
        self.assertEqual("новое", self.с.прочитать(ид)["имя"])
        self.с.удалить(ид, 1)
        self.assertEqual([], self.с.список(1))

    def test_изменить_чужую_недоступную(self):
        ид = self.с.создать(владелец=1, имя="a")
        with self.assertRaises(KeyError):
            self.с.изменить(ид, 2, имя="x")

    def test_порядок_свежие_сверху(self):
        а = self.с.создать(владелец=1, имя="а")
        time.sleep(0.01)
        б = self.с.создать(владелец=1, имя="б")
        self.assertEqual([б, а], [с["ид"] for с in self.с.список(1)])
        time.sleep(0.01)
        self.с.тронуть(а)
        self.assertEqual([а, б], [с["ид"] for с in self.с.список(1)])
        (Path(self._tmp.name) / "sessii" / "мусор.json").write_text("{", encoding="utf-8")
        self.assertEqual(2, len(self.с.список(1)))            # битый файл не роняет список
        self.assertEqual([], Сессии(Path(self._tmp.name) / "нет").список(1))


class РазметкаTests(unittest.TestCase):
    def test_проверка_сливает_отрезки(self):
        р = rastr.проверить_разметку({"отрезки": [[10, 5], [0, 3], [14, 4], [3, 2], [30, 1]],
                                      "правила": [{"период": 256, "сдвиг": 259, "ширина": 1}]})
        self.assertEqual([[0, 5], [10, 8], [30, 1]], р["отрезки"])
        self.assertEqual([{"период": 256, "сдвиг": 3, "ширина": 1, "от": 0, "до": 0}], р["правила"])
        self.assertEqual({"отрезки": [], "правила": []}, rastr.проверить_разметку({}))
        # Смежные отрезки — одним, вложенный — поглощается.
        self.assertEqual([[0, 10]], rastr.проверить_разметку({"отрезки": [[0, 5], [5, 5], [2, 1]]})["отрезки"])

    def test_проверка_ошибки(self):
        for плохое in ({"отрезки": [[-1, 3]]}, {"отрезки": [[0, 0]]},
                       {"правила": [{"период": 0}]}, {"правила": [{"период": 8, "ширина": 9}]},
                       {"правила": [{"период": 8, "ширина": 0}]}, {"правила": [{"период": 65537}]},
                       {"правила": [{"период": 8, "сдвиг": -1}]}, {"правила": [{"период": 8, "от": -1}]},
                       {"правила": [{"период": 8, "до": -1}]}, [1, 2],
                       {"правила": [{"период": 8}] * (rastr.ПРАВИЛ_ДО + 1)}):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                rastr.проверить_разметку(плохое)
        self.assertEqual(rastr.ПРАВИЛ_ДО, len(rastr.проверить_разметку(
            {"правила": [{"период": 8}] * rastr.ПРАВИЛ_ДО})["правила"]))
        старый = rastr.ОТРЕЗКОВ_ДО
        rastr.ОТРЕЗКОВ_ДО = 3
        try:
            self.assertEqual(3, len(rastr.проверить_разметку(
                {"отрезки": [[0, 1], [5, 1], [9, 1]]})["отрезки"]))
            with self.assertRaises(ValueError):
                rastr.проверить_разметку({"отрезки": [[0, 1], [5, 1], [9, 1], [20, 1]]})
        finally:
            rastr.ОТРЕЗКОВ_ДО = старый
        self.assertEqual(1, rastr.проверить_разметку({"правила": [{"период": 8, "ширина": 8}]})["правила"][0]["ширина"] // 8)
        self.assertEqual(65536, rastr.проверить_разметку({"правила": [{"период": 65536}]})["правила"][0]["период"])
        # Без периода правило — ошибка; без сдвига и ширины — место 0 шириной 1; период 1 — годен.
        with self.assertRaises(ValueError):
            rastr.проверить_разметку({"правила": [{"сдвиг": 1}]})
        self.assertEqual([{"период": 5, "сдвиг": 0, "ширина": 1, "от": 0, "до": 0}],
                         rastr.проверить_разметку({"правила": [{"период": 5}]})["правила"])
        self.assertEqual(1, rastr.проверить_разметку({"правила": [{"период": 1}]})["правила"][0]["период"])

    def test_маска_правила_и_отрезки(self):
        р = {"отрезки": [[1, 2]], "правила": [{"период": 10, "сдвиг": 7, "ширина": 4, "от": 0, "до": 0}]}
        маска = rastr.маска_разметки(25, р)
        ждём = [i in (1, 2) or (i - 7) % 10 < 4 for i in range(25)]
        self.assertEqual(ждём, list(маска))
        # Правило на участке: от и до — биты потока, фаза цикла — от начала потока.
        р = {"отрезки": [], "правила": [{"период": 8, "сдвиг": 3, "ширина": 1, "от": 12, "до": 40}]}
        self.assertEqual([19, 27, 35], list(np.flatnonzero(rastr.маска_разметки(64, р))))
        р = {"отрезки": [], "правила": [{"период": 8, "сдвиг": 3, "ширина": 1, "от": 11, "до": 1000}]}
        self.assertEqual([11, 19, 27], list(np.flatnonzero(rastr.маска_разметки(30, р))))
        р = {"отрезки": [], "правила": [{"период": 8, "сдвиг": 3, "ширина": 1, "от": 40, "до": 20}]}
        self.assertFalse(rastr.маска_разметки(64, р).any())
        р = {"отрезки": [], "правила": [{"период": 8, "сдвиг": 3, "ширина": 1, "от": 0, "до": 20}]}
        self.assertEqual([3, 11, 19], list(np.flatnonzero(rastr.маска_разметки(64, р))))

    def test_действия(self):
        биты = np.arange(20, dtype=np.uint8) % 2
        р = rastr.проверить_разметку({"отрезки": [[0, 2]], "правила": [{"период": 5, "сдвиг": 4}]})
        места = [0, 1, 4, 9, 14, 19]
        self.assertEqual(list(биты[места]), list(rastr.по_разметке(биты, р, "взять")))
        self.assertEqual([i for i in range(20) if i not in места],
                         list(np.flatnonzero(np.isin(np.arange(20), места, invert=True))))
        self.assertEqual(list(np.delete(биты, места)), list(rastr.по_разметке(биты, р, "убрать")))
        инв = rastr.по_разметке(биты, р, "инверсия")
        self.assertEqual([int(биты[i]) ^ (i in места) for i in range(20)], list(инв))
        self.assertEqual(биты.dtype, инв.dtype)
        with self.assertRaises(ValueError):
            rastr.по_разметке(биты, р, "съесть")

    def test_шаг_разметки(self):
        шаги = rastr.проверить_шаги([{"вид": "разметка", "действие": "взять",
                                       "разметка": {"правила": [{"период": 8, "сдвиг": 0, "ширина": 1}]}}])
        self.assertEqual(("разметка", True), (шаги[0]["вид"], шаги[0]["вкл"]))
        self.assertEqual("взять размеченные биты: бит 0 с периодом 8", rastr.описать_шаг(шаги[0]))
        биты = np.tile(np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.uint8), 10)
        итог, описание = rastr.применить(биты, шаги)
        self.assertEqual([1] * 10, list(итог))
        self.assertEqual(["взять размеченные биты: бит 0 с периодом 8"], описание)
        for плохой in ({"вид": "разметка", "действие": "взять", "разметка": {}},
                       {"вид": "разметка", "действие": "съесть",
                        "разметка": {"отрезки": [[0, 1]]}}):
            with self.subTest(плохой=плохой), self.assertRaises(ValueError):
                rastr.проверить_шаги([плохой])
        with self.assertRaises(ValueError):
            rastr.применить(биты, rastr.проверить_шаги([{"вид": "разметка", "действие": "убрать",
                                                         "разметка": {"отрезки": [[0, 80]]}}]))

    def test_описание(self):
        self.assertEqual("пусто", rastr.описать_разметку({}))
        self.assertEqual("биты 5–9", rastr.описать_разметку({"отрезки": [[5, 5]]}))
        self.assertEqual("2 отрезк. (7 бит); биты 3–4 с периодом 256",
                         rastr.описать_разметку({"отрезки": [[0, 2], [10, 5]],
                                                 "правила": [{"период": 256, "сдвиг": 3, "ширина": 2}]}))
        self.assertEqual("убрать размеченные биты: биты 0–0",
                         rastr.описать_шаг({"вид": "разметка", "действие": "убрать",
                                            "разметка": {"отрезки": [[0, 1]]}}))


class ШагиTests(unittest.TestCase):
    """Проверка и выполнение шагов обработки: маска, слой, разметка, включение, ход."""

    def test_маска(self):
        шаг = rastr.проверить_шаги([{"вид": "маска", "маска": {"период": 8, "позиции": [8, 7, -1, 0, 7]}}])[0]
        self.assertEqual({"вид": "маска", "вкл": True, "маска": {"период": 8, "сдвиг": 0, "позиции": [0, 7]}}, шаг)
        self.assertEqual([0], rastr.проверить_шаги([{"вид": "маска", "маска": {"период": 1, "позиции": [0]}}])[0]["маска"]["позиции"])
        self.assertEqual(rastr.ПЕРИОД_ДО, rastr.проверить_шаги([{"вид": "маска", "маска": {"период": rastr.ПЕРИОД_ДО,
                                                                                          "позиции": [3]}}])[0]["маска"]["период"])
        for плохой in ({"вид": "маска"}, {"вид": "маска", "маска": {"позиции": [0]}},
                       {"вид": "маска", "маска": {"период": 8, "позиции": []}},
                       {"вид": "маска", "маска": {"период": 8, "позиции": [8]}},
                       {"вид": "маска", "маска": {"период": 0, "позиции": [0]}},
                       {"вид": "маска", "маска": {"период": rastr.ПЕРИОД_ДО + 1, "позиции": [0]}}):
            with self.subTest(плохой=плохой), self.assertRaises(ValueError):
                rastr.проверить_шаги([плохой])

    def test_слой_и_число_шагов(self):
        шаги = rastr.проверить_шаги([{"вид": "слой", "слой": "  инверсия  ", "вкл": False},
                                     {"вид": "слой", "слой": "x" * 600}])
        self.assertEqual({"вид": "слой", "вкл": False, "слой": "инверсия"}, шаги[0])
        self.assertEqual(500, len(шаги[1]["слой"]))
        for плохой in ({"вид": "слой", "слой": "   "}, {"вид": "слой"}, {"слой": "инверсия"}, "инверсия"):
            with self.subTest(плохой=плохой), self.assertRaises(ValueError):
                rastr.проверить_шаги([плохой])
        self.assertEqual(24, len(rastr.проверить_шаги([{"вид": "слой", "слой": "инверсия"}] * 24)))
        with self.assertRaises(ValueError):
            rastr.проверить_шаги([{"вид": "слой", "слой": "инверсия"}] * 25)

    def test_применить_ход_и_выключенные(self):
        биты = np.array([1, 0, 1, 1, 0, 0, 0, 1] * 4, dtype=np.uint8)
        ход = []
        итог, описание = rastr.применить(биты, [{"вид": "слой", "слой": "инверсия"},
                                               {"вид": "слой", "слой": "инверсия", "вкл": False},
                                               {"вид": "маска", "маска": {"период": 8, "позиции": [0]}}], ход=ход.append)
        self.assertEqual([0] * 4, list(итог))
        self.assertEqual(["шаг 1: инверсия", "шаг 3: канал по маске: период 8, сдвиг 0, позиции 0"], ход)
        self.assertEqual(2, len(описание))

    def test_описание_слоя_с_подробностями(self):
        # В описание шага идут три первые подробности снятого слоя, через «; ».
        from unittest import mock  # noqa: PLC0415

        from reportgen.potok.nahodka import Находка  # noqa: PLC0415
        находка = Находка(уровень="проба", что="снят слой", мера="", уверенность=1.0,
                          подробно=["один", "два", "три", "четыре"])
        with mock.patch("reportgen.potok.razbor.снять_вручную", return_value=(np.zeros(8, np.uint8), находка)):
            _, описание = rastr.применить(np.ones(8, np.uint8), [{"вид": "слой", "слой": "что-то"}])
        self.assertEqual(["снят слой: один; два; три"], описание)


class ПоискПериодаTests(unittest.TestCase):
    def test_маркер_в_случайном_потоке(self):
        б = поток_с_маркером(2964, "1110101100100001", 1 << 23, начало=1000)
        найдено = rastr.поиск_периода(б, от=8, до=8192)
        self.assertEqual({"период": 2964, "первый_бит": 1000, "глубина": 16, "вес": 1.0,
                          "маркер": "1110101100100001", "заметка": ""}, найдено[0])
        # Узкий диапазон — свёрткой проверяются все лаги, без автокорреляции.
        self.assertEqual(найдено[0], rastr.поиск_периода(б, от=2900, до=3000)[0])
        # Шаг, не попадающий на период, — периода нет.
        self.assertEqual([], rastr.поиск_периода(б, от=2900, до=3000, шаг=7))

    def test_ошибки_бит_и_кратные(self):
        б = поток_с_маркером(1000, "111110011010110010000", 1 << 22, начало=333, ошибок=0.02)
        найдено = rastr.поиск_периода(б, от=900, до=3100)
        self.assertEqual((1000, 333, 21), (найдено[0]["период"], найдено[0]["первый_бит"], найдено[0]["глубина"]))
        self.assertGreater(найдено[0]["вес"], 0.95)
        # Широкий диапазон — отбор автокорреляцией: гармоники 2000 и 3000 туда не попадают.
        self.assertNotIn(2000, [н["период"] for н in найдено])
        # Лаги перечислены шагом — свёртка всех: кратные помечены и стоят ниже.
        найдено = rastr.поиск_периода(б, от=1000, до=3000, шаг=1000)
        self.assertEqual([(1000, ""), (2000, "кратен 1000"), (3000, "кратен 1000")],
                         [(н["период"], н["заметка"]) for н in найдено])
        self.assertEqual(найдено[0], rastr.поиск_периода(б, от=1000, до=3000, шаг=1000, сколько=1)[0])
        self.assertEqual(1, len(rastr.поиск_периода(б, от=1000, до=3000, шаг=1000, сколько=0)))
        найдено = rastr.поиск_периода(б, от=900, до=3100)
        # При строгом качестве маркер с ошибками весит меньше.
        # Строже качество — столбцы с 2 % ошибок на грани: маркер короче и весит меньше.
        строго = rastr.поиск_периода(б, от=1000, до=1000, качество=97)
        self.assertLess(строго[0]["глубина"], 21)
        self.assertLess(строго[0]["вес"], найдено[0]["вес"])
        # При 100 % ни один столбец с ошибками не устойчив — маркера нет.
        self.assertEqual([], rastr.поиск_периода(б, от=1000, до=1000, качество=100))

    def test_начала_потока_хватает(self):
        # API отдаёт поиску только начало потока: итог тот же, что по всему потоку.
        б = поток_с_маркером(3000, "1011001110001111", 1 << 25, начало=1234, ошибок=0.01)
        for до, глубина in ((8192, 64), (4000, 32), (65536, 64)):
            with self.subTest(до=до):
                нужно = rastr.бит_поиску_периода(до, глубина)
                self.assertLess(нужно, len(б))
                self.assertEqual(rastr.поиск_периода(б, от=8, до=до, глубина_до=глубина),
                                 rastr.поиск_периода(б[:нужно], от=8, до=до, глубина_до=глубина))

    def test_короткий_маркер_e1(self):
        б = поток_с_маркером(512, "0011011", 1 << 23, начало=1)
        найдено = rastr.поиск_периода(б, от=8, до=4096, глубина_от=4)
        self.assertEqual((512, 1, 7, "0011011"), (найдено[0]["период"], найдено[0]["первый_бит"],
                                                   найдено[0]["глубина"], найдено[0]["маркер"]))
        # Маркер короче наименьшей глубины — не маркер.
        self.assertEqual([], [н for н in rastr.поиск_периода(б, от=500, до=520, глубина_от=8)
                              if н["период"] == 512])

    def test_случайный_поток_и_края(self):
        б = np.random.default_rng(9).integers(0, 2, 1 << 22, dtype=np.uint8)
        self.assertEqual([], rastr.поиск_периода(б, от=8, до=8192))
        self.assertEqual([], rastr.поиск_периода(б[:100], от=8, до=64))    # меньше 16 циклов
        for плохое in ({"от": 1}, {"от": 100, "до": 50}, {"до": 70000}, {"глубина_от": 9, "глубина_до": 8},
                       {"качество": 49}, {"качество": 101}):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                rastr.поиск_периода(б, **плохое)

    def test_постоянный_поток(self):
        б = np.zeros(10_000, dtype=np.uint8)
        найдено = rastr.поиск_периода(б, от=16, до=16, глубина_до=12)
        self.assertEqual((16, 0, 12, 1.0), (найдено[0]["период"], найдено[0]["первый_бит"],
                                             найдено[0]["глубина"], найдено[0]["вес"]))

    def test_маркер_через_край_строки(self):
        # Маркер начинается в конце цикла и продолжается в следующем.
        б = поток_с_маркером(100, "1100101011110000", 200_000, начало=92)
        н = rastr.поиск_периода(б, от=100, до=100)[0]
        self.assertEqual((92, 16, "1100101011110000"), (н["первый_бит"], н["глубина"], н["маркер"]))

    def test_маркер_не_длиннее_наибольшей_глубины(self):
        б = поток_с_маркером(300, "1" * 10 + "0" * 30, 300_000, начало=5)
        н = rastr.поиск_периода(б, от=300, до=300, глубина_до=24)[0]
        self.assertEqual((5, 24), (н["первый_бит"], н["глубина"]))


class БыстрыеОсновыTests(unittest.TestCase):
    """Длина БПФ, делители и автокорреляция — на них держится скорость поиска периода."""

    def test_размер_бпф(self):
        for n in list(range(1, 400)) + [1000, 4259840, 4194305, 2 ** 20 + 1]:
            р = размер_бпф(n)
            self.assertGreaterEqual(р, n)
            м = р
            for п in (2, 3, 5):
                while м % п == 0:
                    м //= п
            self.assertEqual(1, м, (n, р))
            # Наименьшее такое: между n и р нет 5-гладких чисел.
            for к in range(n, р if n < 5000 else n):
                м = к
                for п in (2, 3, 5):
                    while м % п == 0:
                        м //= п
                self.assertNotEqual(1, м, (n, к))
        self.assertEqual([1, 2, 8, 1000, 4320000, 4199040, 1049760],
                         [размер_бпф(n) for n in (1, 2, 7, 1000, 4259840, 4194305, 2 ** 20 + 1)])
        self.assertEqual(1, размер_бпф(0))

    def test_делители(self):
        for n in list(range(1, 300)) + [65536, 2964, 9973 * 2, 1000000]:
            self.assertEqual([d for d in range(1, n + 1) if n % d == 0], rastr.делители(n), n)

    def test_автокорреляция_как_прямой_счёт(self):
        g = np.random.default_rng(21)
        for n, L in ((1000, 50), (4097, 300), (777, 776), (64, 1)):
            б = g.integers(0, 2, n, dtype=np.uint8)
            x = б.astype(np.float64) * 2 - 1
            x -= x.mean()
            прямо = np.array([np.dot(x[:n - k], x[k:]) / (n - k) for k in range(L + 1)]) / (x * x).mean()
            self.assertTrue(np.allclose(прямо, автокорреляция(б, L), atol=1e-4), (n, L))


class ПереборСкремблераTests(unittest.TestCase):
    @staticmethod
    def самосинхронный(x, отводы):
        y = x.copy()
        for n in range(len(y)):
            v = x[n]
            for t in отводы:
                if n >= t:
                    v ^= y[n - t]
            y[n] = v
        return y

    def setUp(self):
        g = np.random.default_rng(3)
        кусок = np.array([0, 1, 1, 1, 1, 1, 1, 0] * 20 + list(g.integers(0, 2, 400)), dtype=np.uint8)
        self.x = np.resize(кусок, 1 << 16)

    def test_находит_полином_своей_степени(self):
        y = self.самосинхронный(self.x, (5, 23))
        итог = skrembler.перебор_степени(y, 23, 2)
        self.assertEqual((23, 2, 22), (итог["степень"], итог["отводов"], итог["сочетаний"]))
        лучший = итог["лучшие"][0]
        self.assertEqual(([5, 23], "1 + x^-5 + x^-23"), (лучший["отводы"], лучший["полином"]))
        self.assertGreater((лучший["мера"] - итог["медиана"]) / итог["разброс"], 50)
        self.assertEqual(20, len(итог["лучшие"]))
        self.assertEqual(3, len(skrembler.перебор_степени(y, 23, 2, лучших=3)["лучшие"]))
        # Соседние степени — толпа без выделяющегося.
        чужая = skrembler.перебор_степени(y, 22, 2)
        self.assertLess((чужая["лучшие"][0]["мера"] - чужая["медиана"]) / чужая["разброс"], 8)
        # Доля нулей — у снятого потока.
        снятое = skrembler.снять(y[:skrembler.ВЫБОРКА_ПЕРЕБОРА], (5, 23))
        self.assertAlmostEqual(1 - снятое.mean(), лучший["нули"], places=4)

    def test_один_и_три_отвода(self):
        y = self.самосинхронный(self.x, (43,))
        self.assertEqual([43], skrembler.перебор_степени(y, 43, 1)["лучшие"][0]["отводы"])
        self.assertEqual(1, skrembler.перебор_степени(y, 43, 1)["сочетаний"])
        y = self.самосинхронный(self.x, (2, 9, 13))
        итог = skrembler.перебор_степени(y, 13, 3)
        self.assertEqual((66, [2, 9, 13]), (итог["сочетаний"], итог["лучшие"][0]["отводы"]))

    def test_ошибки(self):
        y = self.x
        for степень, отводов in ((0, 1), (65, 2), (5, 0), (5, 6), (40, 9)):
            with self.subTest(степень=степень, отводов=отводов), self.assertRaises(ValueError):
                skrembler.перебор_степени(y, степень, отводов)
        with self.assertRaises(ValueError):
            skrembler.перебор_степени(y, 40, 5)                 # C(39,4) = 82251 > 20000
        self.assertEqual(8, skrembler.перебор_степени(y[:5000], 8, 8)["отводов"])
        with self.assertRaises(ValueError):
            skrembler.перебор_степени(y[:4000], 8, 2)            # мало бит
        self.assertEqual(7, skrembler.перебор_степени(y[:4096 + 8], 8, 2)["сочетаний"])

    def test_начальная_установка(self):
        g = np.random.default_rng(4)
        псп = skrembler.псп((14, 15), np.ones(15, dtype=np.uint8), 1 << 16)
        данные = g.integers(0, 2, 1 << 16, dtype=np.uint8)
        данные[:3000] = 0
        w = данные ^ псп
        уст = skrembler.начальная_установка(w, (14, 15))
        self.assertEqual("нули", уст["заполнение"])
        место = уст["место"]
        self.assertEqual("".join(str(int(б)) for б in псп[место:место + 15]), уст["регистр"])
        # Флаги HDLC в паузе — регистр за вычетом заполнения.
        флаги = np.resize(np.array([0, 1, 1, 1, 1, 1, 1, 0], dtype=np.uint8), 1 << 16)
        флаги[20_000:] = g.integers(0, 2, (1 << 16) - 20_000)
        уст = skrembler.начальная_установка(флаги ^ псп, (14, 15))
        self.assertTrue(уст["заполнение"].startswith("флаги HDLC"))
        self.assertEqual("".join(str(int(б)) for б in псп[уст["место"]:уст["место"] + 15]), уст["регистр"])
        self.assertIsNone(skrembler.начальная_установка(g.integers(0, 2, 1 << 16, dtype=np.uint8), (14, 15)))


class ЗаданияСессийTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.з = Задания(Path(self._tmp.name) / "potok")

    def готово(self, ид):
        for _ in range(300):
            if self.з.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                return self.з.прочитать(ид)
            time.sleep(0.02)
        self.fail("не дождались")

    def test_биты_из_памяти_и_только_чтение(self):
        ид = self.з.создать(владелец=1, имя="a.bin", данные=bytes(range(256)) * 4, разбирать=False)
        self.готово(ид)
        первые = self.з.биты(ид, 0)
        self.assertIs(первые, self.з.биты(ид, 0))
        self.assertFalse(первые.flags.writeable)
        self.assertTrue(np.array_equal(np.unpackbits(np.frombuffer(bytes(range(256)) * 4, np.uint8)), первые))
        with self.assertRaises(ValueError):
            self.з.биты(ид, 5)                                   # такого этапа нет
        with self.assertRaises(ValueError):
            self.з.файл_бит("20990101-000000-000000", 0)

    def test_биты_участка(self):
        данные = bytes(np.random.default_rng(2).integers(0, 256, 4096, dtype=np.uint8))
        ид = self.з.создать(владелец=1, имя="a", данные=данные, разбирать=False)
        self.готово(ид)
        все = np.unpackbits(np.frombuffer(данные, np.uint8))
        for от, до in ((0, 100), (3, 17), (13, 13), (8, 16), (5, None), (32760, 40000), (-4, 9), (20, 10)):
            with self.subTest(от=от, до=до):
                ждём = все[max(0, от):до] if до is None or до >= max(0, от) else все[:0]
                кусок = self.з.биты_участка(ид, 0, от, до)
                self.assertTrue(np.array_equal(ждём, кусок))
                self.assertFalse(кусок.flags.writeable)
        # Весь массив уже в памяти — участок берётся из неё, без чтения файла.
        полные = self.з.биты(ид, 0)
        self.assertIs(полные.base if полные.base is not None else полные,
                      self.з.биты_участка(ид, 0, 10, 20).base)
        with self.assertRaises(ValueError):
            self.з.биты_участка(ид, 4, 0, 10)

    def test_память_ограничена(self):
        старый = модуль.КЭШ_БИТ
        модуль.КЭШ_БИТ = 8 * 1024 * 2 + 1
        try:
            а = self.з.создать(владелец=1, имя="a", данные=b"\x01" * 1024, разбирать=False)
            б = self.з.создать(владелец=1, имя="b", данные=b"\x02" * 1024, разбирать=False)
            в = self.з.создать(владелец=1, имя="c", данные=b"\x03" * 1024, разбирать=False)
            for и in (а, б, в):
                self.готово(и)
            ба = self.з.биты(а, 0)
            self.з.биты(б, 0)
            self.assertIs(ба, self.з.биты(а, 0))                 # оба помещаются
            self.з.биты(в, 0)                                    # вытесняет самый давний — б
            self.assertIs(ба, self.з.биты(а, 0))
            self.assertEqual(2, len(self.з._кэш))
            большой = self.з.создать(владелец=1, имя="d", данные=b"\x04" * 4096, разбирать=False)
            self.готово(большой)
            self.з.биты(большой, 0)                              # больше всей памяти — не кладётся
            self.assertEqual(2, len(self.з._кэш))
        finally:
            модуль.КЭШ_БИТ = старый

    def test_запомнить_считает_один_раз(self):
        ид = self.з.создать(владелец=1, имя="a", данные=b"\x0f" * 64, разбирать=False)
        self.готово(ид)
        вызовов = []

        def посчитать():
            n = len(self.з.биты(ид, 0))
            вызовов.append(n)
            return {"бит": n}

        self.assertEqual({"бит": 512}, self.з.запомнить(ид, 0, "к", посчитать))
        self.assertEqual({"бит": 512}, self.з.запомнить(ид, 0, "к", посчитать))
        self.assertEqual([512], вызовов)
        self.з.запомнить(ид, 0, "другой", посчитать)
        self.assertEqual(2, len(вызовов))
        старый = модуль.ИТОГОВ_ПОМНИТЬ
        модуль.ИТОГОВ_ПОМНИТЬ = 2
        try:
            self.з.запомнить(ид, 0, "третий", посчитать)
            self.assertEqual(2, len(self.з._итоги))
            self.з.запомнить(ид, 0, "к", посчитать)             # вытеснен самый давний — «к»
            self.assertEqual(4, len(вызовов))
            self.з.запомнить(ид, 0, "третий", посчитать)        # а «третий» ещё помнится
            self.assertEqual(4, len(вызовов))
        finally:
            модуль.ИТОГОВ_ПОМНИТЬ = старый

    def test_разметка_по_этапам(self):
        ид = self.з.создать(владелец=1, имя="a", данные=b"\x00" * 8, разбирать=False)
        self.assertEqual({"отрезки": [], "правила": []}, self.з.разметка(ид, 0))
        р = {"отрезки": [[1, 2]], "правила": []}
        self.з.записать_разметку(ид, 0, р)
        self.з.записать_разметку(ид, 2, {"отрезки": [], "правила": [{"период": 8}]})
        self.assertEqual(р, self.з.разметка(ид, 0))
        self.assertEqual([{"период": 8}], self.з.разметка(ид, 2)["правила"])
        self.з.записать_разметку(ид, 0, {"отрезки": [], "правила": []})   # пустая — стирается
        self.assertEqual({"отрезки": [], "правила": []}, self.з.разметка(ид, 0))
        self.assertEqual(["2"], list(json.loads(
            (self.з.папка / ид / "разметка.json").read_text(encoding="utf-8"))))
        (self.з.папка / ид / "разметка.json").write_text("{", encoding="utf-8")
        self.assertEqual({"отрезки": [], "правила": []}, self.з.разметка(ид, 2))
        self.з.записать_разметку(ид, 1, р)                     # битый файл переписывается
        self.assertEqual(р, self.з.разметка(ид, 1))

    def test_список_удаление_и_наследование(self):
        свой = self.з.создать(владелец=1, имя="свой", данные=b"\x00" * 8, разбирать=False)
        общий = self.з.создать(владелец=2, имя="общий", данные=b"\x00" * 8, разбирать=False, сессия="aaaaaaaaaaaa")
        self.готово(свой)
        self.готово(общий)
        self.assertEqual({свой}, {з["ид"] for з in self.з.список(1)})
        self.assertEqual({свой, общий}, {з["ид"] for з in self.з.список(1, ["aaaaaaaaaaaa"])})
        self.assertEqual({свой}, {з["ид"] for з in self.з.список(1, ["aaaaaaaaaaaa"], без_сессий=True)})
        self.assertEqual({свой, общий}, {з["ид"] for з in self.з.список()})
        self.assertEqual("общий", self.з.дерево(общий, 1, ["aaaaaaaaaaaa"])["имя"])
        with self.assertRaises(KeyError):
            self.з.дерево(общий, 1)
        # Узел сессии, сделанный продолжением, остаётся в сессии.
        папка = self.з.папка / общий
        (папка / "этап-1.bin").write_bytes(b"\xff" * 4)
        продолжение = self.з.продолжить(общий, 1, владелец=1)
        self.assertEqual("aaaaaaaaaaaa", self.з.прочитать(продолжение)["сессия"])
        self.assertEqual("", self.з.прочитать(свой)["сессия"])
        for _ in range(600):
            if self.з.прочитать(продолжение)["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.05)
        удалены = self.з.удалить_сессию("aaaaaaaaaaaa")
        self.assertEqual({общий, продолжение}, set(удалены))
        self.assertEqual([свой], [з["ид"] for з in self.з.список()])

    def test_деревья_сессии(self):
        а = self.з.создать(владелец=1, имя="a", данные=b"\x00" * 8, разбирать=False, сессия="dddddddddddd")
        time.sleep(0.01)
        б = self.з.создать(владелец=2, имя="b", данные=b"\x01" * 8, разбирать=False, сессия="dddddddddddd")
        чужой = self.з.создать(владелец=1, имя="c", данные=b"\x02" * 8, разбирать=False, сессия="eeeeeeeeeeee")
        for и in (а, б, чужой):
            self.готово(и)
        ребёнок = self.з.создать(владелец=2, имя="a → x", данные=b"\x00" * 8, разбирать=False,
                                 от=f"{а}#0", сессия="dddddddddddd")
        self.готово(ребёнок)
        деревья = self.з.деревья_сессии("dddddddddddd")
        self.assertEqual([а, б], [д["ид"] for д in деревья])                 # по времени добавления
        self.assertEqual([ребёнок], [д["ид"] for д in деревья[0]["дети"]])
        self.assertEqual((0, []), (деревья[0]["дети"][0]["этап_родителя"], деревья[1]["дети"]))
        self.assertEqual(self.з.дерево(а, 1, ["dddddddddddd"]), деревья[0])     # то же, что дерево узла
        self.assertEqual([], self.з.деревья_сессии("ffffffffffff"))

    def test_удалить_сессию_с_идущим_разбором(self):
        ид = self.з.создать(владелец=1, имя="a", данные=b"\x00" * 8, разбирать=False, сессия="bbbbbbbbbbbb")
        self.готово(ид)
        self.з._идёт = ид
        with self.assertRaises(ValueError):
            self.з.удалить_сессию("bbbbbbbbbbbb")
        self.assertTrue((self.з.папка / ид).exists())
        self.з._идёт = ""
        self.assertEqual([ид], self.з.удалить_сессию("bbbbbbbbbbbb"))

    def test_уборка_не_трогает_сессии(self):
        старый = модуль.ХРАНИТЬ
        модуль.ХРАНИТЬ = 1
        try:
            с = self.з.создать(владелец=1, имя="s", данные=b"\x00", разбирать=False, сессия="cccccccccccc")
            self.готово(с)
            а = self.з.создать(владелец=1, имя="a", данные=b"\x00", разбирать=False)
            self.готово(а)
            б = self.з.создать(владелец=1, имя="b", данные=b"\x00", разбирать=False)
            self.готово(б)
            self.з.создать(владелец=1, имя="c", данные=b"\x00", разбирать=False)
            ид_ = {з["ид"] for з in self.з.список()}
            self.assertIn(с, ид_)
            self.assertNotIn(а, ид_)
        finally:
            модуль.ХРАНИТЬ = старый


class СессииЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — общая заготовка веб-тестов

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client

    def войти(self, кто):
        self.сеть.login(кто)

    def ид_пользователя(self, логин):
        return self.сеть.repos.users.by_login(логин).id

    def дождаться(self, ид):
        for _ in range(900):
            с = self.к.get(f"/api/potok/{ид}").json()
            if с["состояние"] in ("готово", "ошибка"):
                return с
            time.sleep(0.05)
        self.fail("не дождались")

    def test_сессия_целиком(self):
        к = self.к
        self.войти("engineer")
        self.assertEqual(400, к.post("/api/sessions", json={"name": "  "}).status_code)
        сид = к.post("/api/sessions", json={"name": "Ночная запись"}).json()["id"]
        данные = bytes(range(256)) * 64
        ответ = к.post(f"/api/sessions/{сид}/files", data={"start": "256", "length": "1024"},
                       files={"file": ("запись.bin", данные, "application/octet-stream")}).json()
        ид = ответ["id"]
        self.assertEqual(1024, ответ["bytes"])
        состояние = self.дождаться(ид)
        self.assertEqual(("готово", сид), (состояние["состояние"], состояние["сессия"]))
        self.assertIn("обрезка: байты 256–1279 из 16384", состояние["происхождение"])
        # Сырые байты — ровно файл, кусками; размер — в заголовке.
        сырые = к.get(f"/api/potok/{ид}/raw")
        self.assertEqual((200, данные[256:1280], "1024"),
                         (сырые.status_code, сырые.content, сырые.headers["X-Total-Bytes"]))
        кусок = к.get(f"/api/potok/{ид}/raw", params={"offset": 1000, "length": 100})
        self.assertEqual((данные[1256:1280], "1000"), (кусок.content, кусок.headers["X-Offset"]))
        self.assertEqual(b"", к.get(f"/api/potok/{ид}/raw", params={"offset": 5000}).content)
        self.assertEqual(409, к.get(f"/api/potok/{ид}/raw", params={"stage": 3}).status_code)
        # Список сессий и сама сессия.
        сессии = к.get("/api/sessions").json()["items"]
        self.assertEqual([(сид, "Ночная запись", 1, 1024, True)],
                         [(с["id"], с["name"], с["files"], с["bytes"], с["mine"]) for с in сессии])
        сессия = к.get(f"/api/sessions/{сид}").json()
        self.assertEqual([ид], [ф["ид"] for ф in сессия["files"]])
        self.assertEqual([ид], [д["ид"] for д in сессия["trees"]])
        self.assertEqual(0, сессия["files"][0]["узлов"])
        self.assertEqual(["engineer"], [ч["login"] for ч in сессия["people"]])
        # Задания сессии не в «моих разборах».
        self.assertNotIn(ид, [з["ид"] for з in к.get("/api/potok").json()["items"]])
        # Разметка: сохраняется очищенной, читается всеми участниками.
        р = к.put(f"/api/potok/{ид}/marks", json={"stage": 0, "marks": {
            "отрезки": [[8, 8], [0, 8]], "правила": [{"период": 256, "сдвиг": 3}]}}).json()["marks"]
        self.assertEqual([[0, 16]], р["отрезки"])
        self.assertEqual(р, к.get(f"/api/potok/{ид}/marks").json()["marks"])
        self.assertEqual(400, к.put(f"/api/potok/{ид}/marks", json={"marks": {"отрезки": [[-1, 1]]}}).status_code)
        self.assertEqual(400, к.put(f"/api/potok/{ид}/marks", json={"marks": {"отрезки": [["x"]]}}).status_code)
        # Производный узел по разметке — в той же сессии.
        новый = к.post(f"/api/potok/{ид}/derive", json={"stage": 0, "steps": [
            {"вид": "разметка", "действие": "взять", "разметка": р}]}).json()["id"]
        self.assertEqual(сид, self.дождаться(новый)["сессия"])
        self.assertEqual(16 + 32 - 2, 8 * len(к.get(f"/api/potok/{новый}/raw").content) - 2)
        # Второй пользователь не видит — пока не поделились.
        сессия_id_инженера = self.ид_пользователя("engineer")
        self.войти("gruppa")
        self.assertEqual(404, к.get(f"/api/sessions/{сид}").status_code)
        self.assertEqual(404, к.get(f"/api/potok/{ид}/raw").status_code)
        self.assertEqual([], к.get("/api/sessions").json()["items"])
        self.войти("engineer")
        группа = self.ид_пользователя("gruppa")
        self.assertEqual(400, к.patch(f"/api/sessions/{сид}", json={"members": [99999]}).status_code)
        self.assertEqual(400, к.patch(f"/api/sessions/{сид}", json={"members": "всем"}).status_code)
        self.assertEqual(400, к.patch(f"/api/sessions/{сид}", json={"members": ["x"]}).status_code)
        self.assertEqual(200, к.patch(f"/api/sessions/{сид}", json={"members": [группа]}).status_code)
        self.войти("gruppa")
        self.assertEqual([(сид, False)], [(с["id"], с["mine"]) for с in к.get("/api/sessions").json()["items"]])
        self.assertEqual(данные[256:1280], к.get(f"/api/potok/{ид}/raw").content)
        self.assertEqual(р, к.get(f"/api/potok/{ид}/marks").json()["marks"])
        дерево = к.get(f"/api/potok/{ид}/tree").json()["tree"]
        self.assertEqual([новый], [д["ид"] for д in дерево["дети"]])
        у_группы = к.get(f"/api/sessions/{сид}").json()
        self.assertEqual(дерево, у_группы["trees"][0])
        self.assertEqual((1, 1, 2), (у_группы["files"][0]["узлов"], у_группы["session"]["files"], у_группы["session"]["nodes"]))
        self.assertEqual(403, к.patch(f"/api/sessions/{сид}", json={"name": "моё"}).status_code)
        self.assertEqual(403, к.delete(f"/api/sessions/{сид}").status_code)
        self.assertEqual(403, к.delete(f"/api/potok/{новый}").status_code)   # чужой узел
        свой = к.post(f"/api/sessions/{сид}/files", data={"analyze": ""},
                      files={"file": ("второй.bin", b"\xaa" * 100, "application/octet-stream")}).json()["id"]
        self.дождаться(свой)
        self.assertEqual(200, к.delete(f"/api/potok/{свой}").status_code)   # свой — можно
        self.assertEqual(200, к.post(f"/api/sessions/{сид}/leave").status_code)
        self.assertEqual(404, к.get(f"/api/sessions/{сид}").status_code)
        self.войти("engineer")
        self.assertEqual(409, к.post(f"/api/sessions/{сид}/leave").status_code)
        self.assertEqual(200, к.patch(f"/api/sessions/{сид}", json={"name": "Переименована"}).status_code)
        self.assertEqual("Переименована", к.get(f"/api/sessions/{сид}").json()["session"]["name"])
        удалены = к.delete(f"/api/sessions/{сид}").json()["deleted"]
        self.assertEqual({ид, новый}, set(удалены))
        self.assertEqual(404, к.get(f"/api/potok/{ид}").status_code)
        self.assertEqual([], к.get("/api/sessions").json()["items"])
        self.assertIsInstance(сессия_id_инженера, int)

    def test_производные_в_сессии(self):
        к = self.к
        self.войти("engineer")
        сид = к.post("/api/sessions", json={"name": "ветви"}).json()["id"]
        ид = к.post(f"/api/sessions/{сид}/files",
                    files={"file": ("a.bin", bytes(range(256)) * 8, "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        # Этап с битовым выходом и притоком — как после автомата.
        папка = self.сеть.app.state.potok.папка / ид
        (папка / "этап-1.bin").write_bytes(b"\x0f" * 64)
        (папка / "приток-1-1.bin").write_bytes(b"\xf0" * 32)
        состояние = json.loads((папка / "состояние.json").read_text(encoding="utf-8"))
        состояние["этапы"] = [{"номер": 1, "уровень": "проба", "что": "проба", "выход": "биты", "выгрузка": "bin",
                               "притоки": [{"номер": 1, "имя": "E1 №1", "бит": 256}]}]
        (папка / "состояние.json").write_text(json.dumps(состояние, ensure_ascii=False), encoding="utf-8")
        приток = к.post(f"/api/potok/{ид}/tributary", json={"stage": 1, "number": 1, "analyze": False}).json()["id"]
        self.assertEqual(сид, self.дождаться(приток)["сессия"])
        дальше = к.post(f"/api/potok/{ид}/continue", json={"stage": 1}).json()["id"]
        self.assertEqual(сид, к.get(f"/api/potok/{дальше}").json()["сессия"])
        # Пересборка производного и корня — тоже в сессии.
        новый = к.post(f"/api/potok/{ид}/derive", json={"steps": [
            {"вид": "разметка", "действие": "взять", "разметка": {"отрезки": [[0, 100]]}}]}).json()["id"]
        self.дождаться(новый)
        пересобран = к.post(f"/api/potok/{новый}/rebuild", json={"steps": [
            {"вид": "разметка", "действие": "убрать", "разметка": {"отрезки": [[0, 8]]}}]}).json()["id"]
        # Пересобирается из исходника (биты родителя, 16 384): без первых 8 — 16 376 бит, 2047 байт.
        готов = self.дождаться(пересобран)
        self.assertEqual((сид, 2047), (готов["сессия"], готов["байт"]))
        self.assertEqual(400, к.post(f"/api/potok/{ид}/rebuild", json={"steps": [
            {"вид": "разметка", "действие": "взять", "разметка": {"отрезки": [[0, 8]]}}]}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/rebuild", json={"steps": [
            {"вид": "маска", "маска": {"период": 8, "позиции": [0]}}]}).status_code)
        корень = к.post(f"/api/potok/{ид}/rebuild", json={"steps": [{"вид": "слой", "слой": "инверсия"}],
                                                         "profile": "быстро"}).json()["id"]
        self.assertEqual(сид, к.get(f"/api/potok/{корень}").json()["сессия"])
        # Заменить чужой узел участник не вправе, свой — вправе.
        self.assertEqual(200, к.patch(f"/api/sessions/{сид}", json={"members": [self.ид_пользователя("gruppa")]}).status_code)
        self.войти("gruppa")
        self.assertEqual(403, к.post(f"/api/potok/{новый}/rebuild", json={"replace": True, "steps": []}).status_code)
        self.assertEqual(200, к.get(f"/api/potok/{новый}").status_code)
        свой = к.post(f"/api/potok/{новый}/derive", json={"steps": []}).json()["id"]
        self.дождаться(свой)
        заменён = к.post(f"/api/potok/{свой}/rebuild", json={"replace": True, "steps": []}).json()["id"]
        self.assertEqual(404, к.get(f"/api/potok/{свой}").status_code)
        self.assertEqual(сид, self.дождаться(заменён)["сессия"])
        # Владелец сессии удаляет и чужой узел.
        self.войти("engineer")
        self.assertEqual(200, к.delete(f"/api/potok/{заменён}").status_code)

    def test_файлы_особых_видов_и_ошибки(self):
        к = self.к
        self.войти("engineer")
        сид = к.post("/api/sessions", json={"name": "виды"}).json()["id"]
        # Текст с битами — сами биты; обрезка — уже потока.
        ответ = к.post(f"/api/sessions/{сид}/files", data={"start": "1"},
                       files={"file": ("биты.txt", b"11110000 10101010 00001111\n" * 3, "text/plain")}).json()
        self.assertEqual(8, ответ["bytes"])
        self.assertEqual(b"\xaa\x0f\xf0" * 2 + b"\xaa\x0f", к.get(f"/api/potok/{ответ['id']}/raw").content)
        # Браузер уже вырезал кусок: сервер только записывает, откуда он.
        ответ = к.post(f"/api/sessions/{сид}/files", data={"start": "4096", "sliced": "1"},
                       files={"file": ("кусок.bin", b"\x01\x02\x03", "application/octet-stream")}).json()
        состояние = self.дождаться(ответ["id"])
        self.assertEqual(["файл кусок.bin: байты 4096–4098"], состояние["происхождение"])
        for данные, поля, код in ((b"", {}, 400), (b"\x00" * 10, {"start": "10"}, 400),
                                  (b"\x00" * 10, {"start": "x"}, 400), (b"\x00" * 10, {"length": "-"}, 400)):
            with self.subTest(поля=поля):
                self.assertEqual(код, к.post(f"/api/sessions/{сид}/files", data=поля,
                                             files={"file": ("a.bin", данные, "application/octet-stream")}).status_code)
        self.assertEqual(404, к.post("/api/sessions/0123456789ab/files",
                                     files={"file": ("a.bin", b"\x00", "application/octet-stream")}).status_code)
        self.assertEqual(404, к.get("/api/sessions/плохой").status_code)
        # Автоанализ при добавлении — задание встаёт в очередь разбора.
        ответ = к.post(f"/api/sessions/{сид}/files", data={"analyze": "1"},
                       files={"file": ("авто.bin", bytes(range(256)) * 16, "application/octet-stream")}).json()
        self.assertTrue(к.get(f"/api/potok/{ответ['id']}").json()["разбирать"])

    def test_поиск_периода_и_скремблера(self):
        к = self.к
        self.войти("engineer")
        сид = к.post("/api/sessions", json={"name": "поиск"}).json()["id"]
        б = поток_с_маркером(1000, "1111100110101100", 1 << 20, начало=77)
        ид = к.post(f"/api/sessions/{сид}/files",
                    files={"file": ("m.bin", np.packbits(б).tobytes(), "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        найдено = к.post(f"/api/potok/{ид}/period-search", json={"from": 900, "to": 1100}).json()["items"]
        self.assertEqual((1000, 77, 16), (найдено[0]["период"], найдено[0]["первый_бит"], найдено[0]["глубина"]))
        self.assertEqual(400, к.post(f"/api/potok/{ид}/period-search", json={"from": 1}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/period-search", json={"quality": "x"}).status_code)
        # Повтор — из памяти, тот же ответ.
        self.assertEqual(найдено, к.post(f"/api/potok/{ид}/period-search", json={"from": 900, "to": 1100}).json()["items"])
        итог = к.post(f"/api/potok/{ид}/scrambler-search", json={"degree": 7, "taps": 2, "additive": True}).json()
        self.assertEqual((7, 6), (итог["степень"], итог["сочетаний"]))
        self.assertIn("аддитивный", итог["лучшие"][0])
        self.assertNotIn("аддитивный", итог["лучшие"][3])
        self.assertNotIn("аддитивный", к.post(f"/api/potok/{ид}/scrambler-search",
                                              json={"degree": 7, "taps": 2, "first": 8}).json()["лучшие"][0])
        self.assertEqual(400, к.post(f"/api/potok/{ид}/scrambler-search", json={"degree": 99}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/scrambler-search", json={"degree": "x"}).status_code)
        # Кандидаты периода — тоже из памяти при повторе.
        periods = к.get(f"/api/potok/{ид}/periods").json()["items"]
        self.assertEqual(periods, к.get(f"/api/potok/{ид}/periods").json()["items"])


КОРЕНЬ = Path(__file__).resolve().parents[1]
APP_JS = КОРЕНЬ / "src" / "reportgen" / "web" / "static" / "app.js"


def функции_js(имена: list[str], константы: list[str]) -> str:
    """Функции и константы из app.js — текстом, по имени (скобки считаются по вложенности)."""
    текст = APP_JS.read_text(encoding="utf-8")
    куски = []
    for имя in константы:
        начало = текст.index(f"    const {имя} = ")
        i, глубина = начало, 0
        while not (текст[i] == ";" and глубина == 0):
            глубина += (текст[i] in "([{") - (текст[i] in ")]}")
            i += 1
        куски.append(текст[начало:i + 1])
    for имя in имена:
        начало = текст.index(f"    function {имя}(")
        i = текст.index("{", текст.index(")", начало))
        глубина = 0
        while True:
            if текст[i] == "{":
                глубина += 1
            elif текст[i] == "}":
                глубина -= 1
                if глубина == 0:
                    break
            i += 1
        куски.append(текст[начало:i + 1])
    return "\n".join(куски)


@unittest.skipUnless(shutil.which("node"), "нужен node")
class РазметкаВБраузереTests(unittest.TestCase):
    """Разметка и подсчёт бит в битовом просмотре — те же, что на сервере.

    Просмотр рисует метки сам (``маскаМеток``), а новый массив из разметки
    делает сервер (``rastr.маска_разметки``): разойдись они — аналитик увидит
    жёлтым одно, а в массив попадёт другое. Функции берутся прямо из app.js и
    выполняются в node на тех же случаях, что и серверные.
    """

    def выполнить(self, случаи: list[dict]) -> list:
        код = функции_js(["битМассива", "единицВОтрезке", "битыСтрокой", "пустаяРазметка", "изменитьОтрезки",
                          "маскаМеток", "итогРазметки", "описатьРазметку", "латиницей"], ["ЕДИНИЦ_В_БАЙТЕ", "ЛАТИНИЦА"])
        код += """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const итог = случаи.map((с) => {
    if (с.что === 'маска') { const out = new Uint8Array(с.длина + 5).fill(7); маскаМеток(с.р, с.от, с.длина, out); return Array.from(out.subarray(0, с.длина)); }
    if (с.что === 'отрезки') { let о = []; с.шаги.forEach(([а, к, д]) => { о = изменитьОтрезки(о, а, к, д); }); return о; }
    if (с.что === 'описание') return описатьРазметку(с.р);
    if (с.что === 'латиницей') return латиницей(с.текст);
    const б = Uint8Array.from(с.байты);
    if (с.что === 'единиц') return единицВОтрезке(б, с.а, с.к);
    if (с.что === 'строкой') return битыСтрокой(б, с.а, с.к);
    if (с.что === 'итог') return итогРазметки(с.р, б, с.n);
    return null;
});
process.stdout.write(JSON.stringify(итог));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=20)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_маска_как_на_сервере(self):
        g = np.random.default_rng(11)
        случаи, ждём = [], []
        for _ in range(60):
            р = rastr.проверить_разметку({
                "отрезки": [[int(g.integers(0, 3000)), int(g.integers(1, 200))] for _ in range(int(g.integers(0, 6)))],
                "правила": [{"период": int(п), "сдвиг": int(g.integers(0, 5000)), "ширина": int(g.integers(1, п + 1)),
                             "от": int(g.integers(0, 400)) * int(g.integers(0, 2)),
                             "до": int(g.integers(0, 4000)) * int(g.integers(0, 2))}
                            for п in g.integers(1, 300, int(g.integers(0, 4)))]})
            от, длина = int(g.integers(0, 3500)), int(g.integers(1, 1500))
            случаи.append({"что": "маска", "р": р, "от": от, "длина": длина})
            ждём.append([int(х) for х in rastr.маска_разметки(от + длина, р)[от:]])
        # Правило с первого бита потока; правило «весь период» с концом участка внутри окна; пустой участок.
        for р, от, длина in (({"отрезки": [], "правила": [{"период": 8, "сдвиг": 0, "ширина": 1, "от": 0, "до": 0}]}, 0, 20),
                             ({"отрезки": [], "правила": [{"период": 4, "сдвиг": 1, "ширина": 4, "от": 0, "до": 10}]}, 2, 20),
                             ({"отрезки": [], "правила": [{"период": 4, "сдвиг": 1, "ширина": 2, "от": 30, "до": 12}]}, 0, 40)):
            случаи.append({"что": "маска", "р": р, "от": от, "длина": длина})
            ждём.append([int(х) for х in rastr.маска_разметки(от + длина, р)[от:]])
        self.assertEqual(ждём, self.выполнить(случаи))

    def test_отрезки_как_множество(self):
        g = np.random.default_rng(12)
        случаи, ждём = [], []
        for _ in range(80):
            шаги, множество = [], set()
            for _ in range(int(g.integers(1, 12))):
                а = int(g.integers(0, 200))
                к = а + int(g.integers(0, 40))
                добавить = bool(g.integers(0, 2))
                шаги.append([а, к, добавить])
                множество = множество | set(range(а, к)) if добавить else множество - set(range(а, к))
            случаи.append({"что": "отрезки", "шаги": шаги})
            слитые = rastr.проверить_разметку({"отрезки": [[i, 1] for i in sorted(множество)]})["отрезки"]
            ждём.append(слитые)
        # Края: снять начало и конец отрезка ровно по его границам; снять касающееся — отрезок цел.
        for шаги, слитые in (([[5, 10, True], [5, 7, False]], [[7, 3]]), ([[5, 10, True], [8, 10, False]], [[5, 3]]),
                             ([[5, 10, True], [0, 5, False]], [[5, 5]]), ([[5, 10, True], [10, 12, False]], [[5, 5]]),
                             ([[5, 10, True], [5, 10, False]], []), ([[5, 10, True], [10, 12, True]], [[5, 7]])):
            случаи.append({"что": "отрезки", "шаги": шаги})
            ждём.append(слитые)
        self.assertEqual(ждём, self.выполнить(случаи))

    def test_единицы_и_биты_строкой(self):
        g = np.random.default_rng(13)
        байты = g.integers(0, 256, 64, dtype=np.uint8)
        биты = np.unpackbits(байты)
        случаи, ждём = [], []
        for а, к in [(0, 512), (0, 0), (3, 3), (1, 7), (5, 21), (8, 16), (13, 500), (504, 512)] + [
                tuple(sorted(int(x) for x in g.integers(0, 513, 2))) for _ in range(40)]:
            случаи.append({"что": "единиц", "байты": байты.tolist(), "а": а, "к": к})
            ждём.append(int(биты[а:к].sum()))
            случаи.append({"что": "строкой", "байты": байты.tolist(), "а": а, "к": к})
            ждём.append("".join(str(int(б)) for б in биты[а:к]))
        self.assertEqual(ждём, self.выполнить(случаи))

    def test_имя_снимка_латиницей(self):
        случаи = ["Запись Щука.bin", "ЁЖИК-2 ъ", "abc_1.2-x", "", "a/b:c*d", "Юля", "Log-X9.Z", "a  // b"]
        self.assertEqual(["Zapis_Shchuka.bin", "EZhIK-2_", "abc_1.2-x", "massiv", "a_b_c_d", "Yulya", "Log-X9.Z", "a_b"],
                         self.выполнить([{"что": "латиницей", "текст": т} for т in случаи]))

    def test_итог_и_описание(self):
        g = np.random.default_rng(14)
        байты = g.integers(0, 256, 20000, dtype=np.uint8)
        биты = np.unpackbits(байты)
        # Отрезок у бита 94 464: в последнем куске подсчёта (65 536 бит на кусок) он лежит сразу за концом.
        р = rastr.проверить_разметку({"отрезки": [[5, 70000], [94000, 1000], [150000, 10]],
                                      "правила": [{"период": 256, "сдвиг": 3, "ширина": 2}]})
        маска = rastr.маска_разметки(len(биты), р)
        итог = self.выполнить([{"что": "итог", "байты": байты.tolist(), "р": р, "n": len(биты)},
                               {"что": "описание", "р": р},
                               {"что": "описание", "р": {"отрезки": [[5, 5]], "правила": [
                                   {"период": 8, "сдвиг": 1, "ширина": 1, "от": 16, "до": 0}]}},
                               {"что": "описание", "р": {"отрезки": [], "правила": []}},
                               {"что": "описание", "р": {"отрезки": [[10, 3]], "правила": [
                                   {"период": 16, "сдвиг": 2, "ширина": 3, "от": 0, "до": 50}]}}])
        self.assertEqual({"всего": int(маска.sum()), "единиц": int(биты[маска].sum())}, итог[0])
        # Размечен первый бит потока — он тоже в счёте.
        первый = self.выполнить([{"что": "итог", "байты": [0x80, 0x00], "р": {"отрезки": [[0, 1], [15, 1]], "правила": []}, "n": 16}])
        self.assertEqual([{"всего": 2, "единиц": 1}], первый)
        self.assertEqual("3 отрезк. (71010 бит); биты 3–4 с периодом 256", итог[1])
        self.assertEqual("биты 5–9; бит 1 с периодом 8 (участок 16…конец)", итог[2])
        self.assertEqual("пусто", итог[3])
        self.assertEqual("биты 10–12; биты 2–4 с периодом 16 (участок 0…50)", итог[4])


class СтраницаСессийTests(unittest.TestCase):
    """Разделы и клавиши интерфейса — на месте (проверка по тексту app.js, как у других страниц)."""

    def test_интерфейс(self):
        js = APP_JS.read_text(encoding="utf-8")
        for кусок in ("route: 'sessions', href: '#/sessions', title: 'Сессии потоков'",
                      "else if (route.name === 'session') await renderStol(view, null, route.id);",
                      "else if (route.name === 'sessions') await renderSessions(view);",
                      "'/raw?stage='", "'/marks'", "'/period-search'", "'/scrambler-search'",
                      "'Быстрый поиск периода — массив '", "'Поиск скремблера — массив '", "'Разметка по периоду'",
                      "['Полный автоанализ',", "'Удалить файл из сессии'", "'Поделиться'", "'Добавить файлы'",
                      "e.key === 'F8'", "e.key === 'F3'", "e.key === 'F4'", "e.key === 'F1'",
                      "e.key === '*' || код === 'NumpadMultiply'", "e.key === '/' || код === 'NumpadDivide'",
                      "код === 'BracketRight'", "код === 'KeyM'", "метка1: rgba(255, 214, 0)", "выбор1: rgba(255, 255, 255)",
                      "форма.append('sliced', '1')", "подключитьПеретаскивание(page"):
            self.assertIn(кусок, js)
        # Каждая клавиша из справки обработана.
        self.assertGreaterEqual(len(re.findall(r"\['[^']+', '[^']+'\],", js[js.index("const КЛАВИШИ_ПРОСМОТРА"):
                                                                          js.index("];", js.index("const КЛАВИШИ_ПРОСМОТРА"))])), 20)


if __name__ == "__main__":
    unittest.main()
