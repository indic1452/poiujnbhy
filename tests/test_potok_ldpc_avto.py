# -*- coding: utf-8 -*-
"""LDPC по загруженным матрицам — автоматом: схема передачи, начало слова, декодирование.

Матрица загружена (из стандарта, по своим параметрам) — дальше её не нужно
вписывать слоем: автомат пробует каждую загруженную матрицу со схемой,
сохранённой вместе с ней, а если известна длина блока данных — и с частыми
схемами выкалывания и укорочения; совпадение видно по вычислимым проверкам.
"""

import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import gf2, ldpc, razbor
from reportgen.potok.bity import в_байты
from test_potok_ldpc import Z, кодовые, передать, прототип


class СКаталогом(unittest.TestCase):
    def setUp(self):
        self._папка = tempfile.TemporaryDirectory()
        self.addCleanup(self._папка.cleanup)
        прежний, ldpc.КАТАЛОГ = ldpc.КАТАЛОГ, Path(self._папка.name)
        self.addCleanup(setattr, ldpc, "КАТАЛОГ", прежний)
        self.м = ldpc.из_прототипа(прототип(), Z)


class ПоЗагруженным(СКаталогом):
    def test_всё_слово(self):
        ldpc.сохранить("свой-z32", self.м)
        слова = кодовые(self.м, 120)
        схема = ldpc.Схема(self.м, np.array([], np.int64), np.array([], np.int64))
        найдено = ldpc.найти_по_матрицам(передать(слова, схема, доля=0.005, сдвиг=37))
        self.assertIsNotNone(найдено)
        self.assertIn("LDPC (384, 192) по матрице «свой-z32»", найдено.что)
        self.assertIn("слово передаётся целиком", найдено.что)
        self.assertEqual(найдено.свойства["начало"], 37)
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), найдено.дальше))
        self.assertGreaterEqual(найдено.уверенность, 0.99)

    def test_сохранённая_перфорация(self):
        """Выколоты первые 2Z позиций (как в 5G NR): схема сохранена при загрузке."""
        ldpc.сохранить("свой-выкол", self.м, выколоты="0-63")
        self.assertEqual("0-63", ldpc.список()[0]["выколоты"])
        слова = кодовые(self.м, 120)
        схема = ldpc.схема_сохранённая("свой-выкол")
        найдено = ldpc.найти_по_матрицам(передать(слова, схема, доля=0.004, сдвиг=5))
        self.assertIn("по схеме, сохранённой с матрицей", найдено.что)
        self.assertEqual(найдено.свойства["выколото"], 64)
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), найдено.дальше))

    def test_укорочение_по_длине_блока(self):
        """Схемы нет, но известен блок данных (4 слова в блоке): слово короче n на 40 бит —
        перебором схем находится «укорочены последние 40 информационных»."""
        ldpc.сохранить("свой-z32", self.м)
        данные_поз = ldpc.информационные(self.м)
        укорочены = данные_поз[-40:]
        H = self.м.плотная()
        нули = np.zeros((40, self.м.n), np.uint8)
        нули[np.arange(40), укорочены] = 1
        G = gf2.ядро(np.vstack([H, нули]))
        слова = (np.random.default_rng(6).integers(0, 2, (160, len(G))) @ G % 2).astype(np.uint8)
        схема = ldpc.Схема(self.м, np.array([], np.int64), укорочены)
        поток = передать(слова, схема, доля=0.003, сдвиг=0)
        self.assertIsNone(ldpc.найти_по_матрицам(поток))                 # без блока — не догадаться
        найдено = ldpc.найти_по_матрицам(поток, блок=4 * схема.длина)
        self.assertIsNotNone(найдено)
        self.assertIn("укорочены последние 40 информационных позиций", найдено.что)
        self.assertEqual(найдено.свойства["укорочено"], 40)
        self.assertTrue(np.array_equal(слова[:, данные_поз].reshape(-1), найдено.дальше))

    def test_чужой_поток(self):
        ldpc.сохранить("свой-z32", self.м)
        self.assertIsNone(ldpc.найти_по_матрицам(
            np.random.default_rng(1).integers(0, 2, 40000).astype(np.uint8)))
        # Код другой матрицы той же длины — не наш.
        чужая = ldpc.из_прототипа(прототип(сид=2), Z)
        слова = кодовые(чужая, 100)
        схема = ldpc.Схема(чужая, np.array([], np.int64), np.array([], np.int64))
        self.assertIsNone(ldpc.найти_по_матрицам(передать(слова, схема, доля=0.0, сдвиг=3)))

    def test_лучшая_из_нескольких(self):
        ldpc.сохранить("чужая", ldpc.из_прототипа(прототип(сид=2), Z))
        ldpc.сохранить("своя", self.м)
        слова = кодовые(self.м, 80)
        схема = ldpc.Схема(self.м, np.array([], np.int64), np.array([], np.int64))
        найдено = ldpc.найти_по_матрицам(передать(слова, схема, доля=0.002, сдвиг=9))
        self.assertEqual(найдено.свойства["матрица"], "своя")
        self.assertIn("загруженных матриц 2", найдено.подробно[0])

    def test_из_совпавших_лучшая_по_проверкам(self):
        """Совпали две схемы, одна заметно лучше по проверкам (за пределом «почти равных»): берётся
        она — без пробного декодирования."""
        ldpc.сохранить("а-хуже", self.м)
        ldpc.сохранить("б-лучше", self.м)
        пробы = iter([(0, 0.15, 0.5), (0, 0.01, 0.5)])
        with mock.patch.object(ldpc, "_проба", side_effect=lambda *а, **к: next(пробы)), \
                mock.patch.object(ldpc, "_пробное", return_value=(1.0, 0.0)) as пробное, \
                mock.patch.object(ldpc, "_снять", return_value=(np.zeros(8, np.uint8), [], 1.0)):
            найдено = ldpc.найти_по_матрицам(np.zeros(10_000, np.uint8))
        self.assertEqual(найдено.свойства["матрица"], "б-лучше")
        self.assertFalse(пробное.called)

    def test_плохая_схема_при_загрузке(self):
        for выколоты, укорочены in (("0-999", ""), ("0-9", "5-6")):
            with self.subTest(выколоты=выколоты), self.assertRaises(ValueError):
                ldpc.сохранить("плохая", self.м, выколоты=выколоты, укорочены=укорочены)
        self.assertEqual([], ldpc.список())

    def test_своя_матрица_под_именем_встроенной(self):
        """Загруженная матрица с именем встроенного кода важнее встроенной; удалена — снова встроенная."""
        ldpc.сохранить("nr-bg2-z16", self.м)
        self.assertEqual(384, ldpc.прочитать("nr-bg2-z16").n)
        self.assertEqual(0, len(ldpc.схема_сохранённая("nr-bg2-z16").выколоты))
        ldpc.удалить("nr-bg2-z16")
        self.assertEqual(832, ldpc.прочитать("nr-bg2-z16").n)
        self.assertEqual(32, len(ldpc.схема_сохранённая("nr-bg2-z16").выколоты))

    def test_укорочение_мимо_выколотых(self):
        """Выколоты первые 64 позиции (они же — начало данных): «укорочены первые d» — первые
        d информационных позиций из ещё передаваемых, а не выколотые."""
        схема = ldpc.Схема(self.м, np.arange(64), np.array([], np.int64))
        варианты = dict(ldpc.варианты_схемы(схема, 4 * (320 - 40)))
        укорочение = варианты["по схеме, сохранённой с матрицей; укорочены первые 40 информационных позиций"]
        данные = ldpc.информационные(self.м)
        self.assertEqual(укорочение.укорочены.tolist(), данные[~np.isin(данные, np.arange(64))][:40].tolist())
        self.assertEqual(укорочение.длина, 280)

    def test_варианты_схем(self):
        схема = ldpc.Схема(self.м, np.array([], np.int64), np.array([], np.int64))
        self.assertEqual(1, len(ldpc.варианты_схемы(схема)))
        # Блок 4·344: слово 344 = 384 − 40 — четыре схемы; блок не делится — только своя.
        варианты = ldpc.варианты_схемы(схема, 4 * 344)
        self.assertEqual([в for в, _ in варианты][1:], [
            "выколоты первые 40 переданных позиций", "выколоты последние 40 переданных позиций",
            "укорочены первые 40 информационных позиций", "укорочены последние 40 информационных позиций"])
        self.assertEqual(1, len(ldpc.варианты_схемы(схема, 997)))


class ВДереве(СКаталогом):
    def test_автомат_снимает_сам(self):
        ldpc.сохранить("свой-z32", self.м, выколоты="0-63")
        слова = кодовые(self.м, 300)
        схема = ldpc.схема_сохранённая("свой-z32")
        поток = передать(слова, схема, доля=0.003, сдвиг=17)
        р = razbor.разобрать(данные=в_байты(поток), имя="ldpc.bin", профиль="быстро")
        отчёт = р.отчёт(20000)
        коды = [н for н in р.находки if н.уровень == "код"]
        self.assertTrue(коды and "по матрице «свой-z32»" in коды[0].что, отчёт)

    def test_без_матриц_только_встроенные_наверху(self):
        """Загруженных матриц нет: на самом потоке (в обоих порядках бит в байте) пробуются
        встроенные коды стандартов — это и записано; глубже без длины блока ничего не
        пробуется и не пишется."""
        р = razbor.разобрать(данные=в_байты(np.random.default_rng(2).integers(0, 2, 60000)
                                            .astype(np.uint8)), имя="шум.bin", профиль="быстро")
        записи = sorted(с for с in р.не_найдено if "матрицам" in с)
        self.assertEqual(["LDPC по загруженным и встроенным матрицам (младший бит первым): нет",
                          "LDPC по загруженным и встроенным матрицам: нет"], записи)


class ЧерезСервер(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def test_схема_при_загрузке(self):
        self.сеть.login("engineer")
        к = self.сеть.client
        ответ = к.post("/api/potok-matrices", json={"name": "nr-проба", "kind": "базовая",
                                                  "text": прототип(), "z": Z, "punctured": "0-63"})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual("0-63", ответ.json()["matrix"]["выколоты"])
        элементы = к.get("/api/potok-matrices").json()["items"]
        self.assertEqual(("nr-проба", "0-63", ""), (элементы[0]["имя"], элементы[0]["выколоты"],
                                                   элементы[0]["укорочены"]))
        плохо = к.post("/api/potok-matrices", json={"name": "nr-плохо", "kind": "базовая",
                                                  "text": прототип(), "z": Z, "punctured": "0-9999"})
        self.assertEqual(400, плохо.status_code)
        встроенные = к.get("/api/potok-matrices").json()["builtin"]
        self.assertEqual(192, len(встроенные))
        nr = next(к for к in встроенные if к["имя"] == "nr-bg1-z384")
        self.assertEqual((nr["n"], nr["k"], nr["выколоты"]), (26112, 8448, "0-767"))


if __name__ == "__main__":
    unittest.main()
