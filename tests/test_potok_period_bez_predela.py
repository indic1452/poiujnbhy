"""Период без предела: цикл, кадр, поиск периода, автокорреляция, растр — до сотен тысяч бит и дальше.

Прежде период везде был зашит константами (8192 у окна поиска и автокорреляции, 65 536 у
сервера, 16 384 у анализа символов), а настройки «максимальный период поиска» не было вовсе —
окно помнило свой «Конечный период», и сервер обрезал его до 65 536. Теперь предел один —
настройка человека (/api/potok-settings, по умолчанию миллион бит, 0 — без предела), и её
слушают автомат, окна стола и автокорреляция. Длинный цикл (синхрослово — доля процента бит)
автокорреляция не видит — он находится по повторам слов с любого бита.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import cikl, nastroyki, rastr, stol_raschety, turbo
from reportgen.potok import razbor
from reportgen.potok.razbor import разобрать


def кадры(период, кадров, *, слово_бит=32, сдвиг=777, сид=5, ошибок=0.0):
    """Поток: случайная нагрузка, в начале каждого кадра — одно и то же случайное синхрослово."""
    rng = np.random.default_rng(сид)
    слово = rng.integers(0, 2, слово_бит).astype(np.uint8)
    k = rng.integers(0, 2, (кадров, период)).astype(np.uint8)
    k[:, :слово_бит] = слово
    б = np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8), k.reshape(-1)])
    if ошибок:
        б ^= (rng.random(len(б)) < ошибок).astype(np.uint8)
    return б, слово


class НастройкаTests(unittest.TestCase):
    """Ввод, проверка, хранение настройки и предел текущего разбора."""

    def test_ввод_и_пределы(self):
        self.assertEqual(200_000, nastroyki.период_из("200 000"))
        self.assertEqual(200_000, nastroyki.период_из(200000.0))
        self.assertEqual(0, nastroyki.период_из("0"))
        self.assertEqual(64, nastroyki.период_из(64))
        self.assertEqual(nastroyki.ПЕРИОД_ДО_НАИБОЛЬШИЙ, nastroyki.период_из(nastroyki.ПЕРИОД_ДО_НАИБОЛЬШИЙ))
        for плохое in (63, 1, -5, nastroyki.ПЕРИОД_ДО_НАИБОЛЬШИЙ + 1, "abc", "1e6", "", None, True, 1.5, [1]):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                nastroyki.период_из(плохое)
        self.assertEqual({"период_до": 1_000_000}, nastroyki.проверить({}))
        self.assertEqual({"период_до": 0}, nastroyki.проверить({"период_до": 0, "лишнее": 1}))
        with self.assertRaises(ValueError):
            nastroyki.проверить([1])

    def test_наибольший(self):
        self.assertEqual(1_000_000, nastroyki.наибольший(None, 10 ** 9))
        self.assertEqual(500, nastroyki.наибольший(None, 8000, 16))
        self.assertEqual(4096, nastroyki.наибольший(4096, 10 ** 9, 16))
        self.assertEqual(10 ** 9 // 3, nastroyki.наибольший(0, 10 ** 9, 3))      # без предела — длина записи
        self.assertEqual(0, nastroyki.наибольший(0, -5))

    def test_хранение(self):
        папка = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, папка, True)
        н = nastroyki.НастройкиЛюдей(папка / "n")
        self.assertEqual({"период_до": 1_000_000}, н.прочитать(7))        # файла нет — по умолчанию
        self.assertEqual({"период_до": 200_000}, н.записать(7, {"период_до": "200 000"}))
        self.assertEqual(200_000, н.период_до(7))
        self.assertEqual(1_000_000, н.период_до(8))                         # у другого — своё
        self.assertEqual({"период_до": 200_000}, н.записать(7, {}))           # не названо — прежнее
        with self.assertRaises(ValueError):
            н.записать(7, {"период_до": 10})
        with self.assertRaises(ValueError):
            н.записать(7, "200000")
        self.assertEqual(200_000, н.период_до(7))                           # ошибка не портит сохранённое
        (папка / "n" / "7.json").write_text("{испорчено", encoding="utf-8")
        self.assertEqual(1_000_000, н.период_до(7))

    def test_предел_анализа_символов(self):
        """Анализ символов (разметка) ищет период в символах по k бит: предел — из той же настройки."""
        from reportgen.potok import razmetka  # noqa: PLC0415
        self.assertEqual({}, razmetka.предел_периода(None, 3))
        self.assertEqual({"период_до": 66_666}, razmetka.предел_периода(200_000, 3))
        self.assertEqual({"период_до": razmetka.ПЕРИОД_ОТ}, razmetka.предел_периода(64, 100))
        self.assertEqual({"период_до": 1 << 62}, razmetka.предел_периода(0, 3))
        self.assertEqual({"период_до": 64}, razmetka.предел_периода(64, 0))

    def test_предел_разбора(self):
        self.assertEqual(nastroyki.ПЕРИОД_ДО, nastroyki.период_до())
        with nastroyki.с_пределом(4096):
            self.assertEqual(4096, nastroyki.период_до())
            with nastroyki.с_пределом(0):
                self.assertEqual(0, nastroyki.период_до())
            with nastroyki.с_пределом(None):
                self.assertEqual(nastroyki.ПЕРИОД_ДО, nastroyki.период_до())
            self.assertEqual(4096, nastroyki.период_до())
        self.assertEqual(nastroyki.ПЕРИОД_ДО, nastroyki.период_до())
        with self.assertRaises(ValueError), nastroyki.с_пределом(5):
            pass


class ДлинныйЦиклTests(unittest.TestCase):
    """Автомат (cikl.найти): длинный цикл с синхрословом — и «мало периодов», и предел из настройки."""

    def test_циклы_сотни_тысяч(self):
        for период, кадров in ((200_000, 20), (131_072, 30), (98_304, 40), (150_001, 27), (150_001, 30)):
            with self.subTest(период=период, кадров=кадров):
                б, слово = кадры(период, кадров)
                подсказки = []
                найдено = cikl.найти(б, подсказки)
                self.assertIsNotNone(найдено, подсказки)
                self.assertEqual(период, найдено.свойства["длина"])
                self.assertIn(f"цикл {период} бит, синхрослово 32 бит", найдено.что)
                self.assertEqual([], подсказки)

    def test_нечётный_цикл_по_повторам_слов(self):
        """150 001 бит: не кратен байту — окна с шагом 8 его не видят, автокорреляция — тоже (32 бита на 150 001)."""
        б, _ = кадры(150_001, 27)
        self.assertIsNone(cikl.длина_цикла(б))
        self.assertIsNone(cikl._цикл_по_слову(б, cikl.ПОВТОР_БЕЗ_ПИКА))
        найдено = cikl.найти(б)
        self.assertIn("повтор участка через 150001 бит: 26 раз",
                      найдено.подробно[0])

    def test_с_ошибками_линии(self):
        б, _ = кадры(150_001, 30, ошибок=0.001)
        self.assertEqual(150_001, cikl.найти(б).свойства["длина"])

    def test_мало_периодов_подсказка(self):
        б, _ = кадры(150_001, 10)
        подсказки = []
        self.assertIsNone(cikl.найти(б, подсказки))
        self.assertEqual(1, len(подсказки))
        self.assertIn("похоже на цикл 150001 бит", подсказки[0])
        self.assertEqual("похоже на цикл 150001 бит (слово 32 бит): периодов 10, нужно от 16 — мало периодов", подсказки[0])
        self.assertIsNone(cikl.найти(б))                          # без списка — просто нет

    def test_длинные_не_проверялись_подсказка(self):
        """Случайный поток: цикла нет, и сказано, что циклы длиннее выборки / 16 не искались."""
        б = np.random.default_rng(3).integers(0, 2, 1 << 21, dtype=np.uint8)
        подсказки = []
        self.assertIsNone(cikl.найти(б, подсказки))
        self.assertEqual([f"циклы длиннее {len(б) // 16} бит не искались — мало периодов в выборке"], подсказки)

    def test_предел_из_настройки(self):
        """Маленький предел — длинный цикл не ищется; большой — находится; 0 — без предела."""
        б, _ = кадры(200_000, 20)
        with nastroyki.с_пределом(100_000):
            подсказки = []
            self.assertIsNone(cikl.найти(б, подсказки))
            self.assertEqual([], подсказки)
        for до in (200_000, 0):
            with nastroyki.с_пределом(до):
                self.assertEqual(200_000, cikl.найти(б).свойства["длина"])
        б, _ = кадры(3000, 300)
        with nastroyki.с_пределом(2999):
            self.assertIsNone(cikl.найти(б))
        with nastroyki.с_пределом(3000):
            self.assertEqual(3000, cikl.найти(б).свойства["длина"])

    def test_повторы_в_случайном_потоке_нет(self):
        б = np.random.default_rng(1).integers(0, 2, 1 << 22, dtype=np.uint8)
        self.assertEqual([], cikl.периоды_по_повторам(б, 32, len(б) // 3))
        self.assertIsNone(cikl._длинный_цикл(б))

    def test_повторы_меряют_неслучайность(self):
        б, _ = кадры(150_001, 6, слово_бит=64)
        лучший = cikl.периоды_по_повторам(б, 32, len(б) // 3)[0]
        # 5 повторов участка в 64 бита (41 совпадение 24-битных слов в каждом) ровно через период.
        self.assertEqual((150_001, 5), лучший[:2])
        self.assertGreaterEqual(лучший[2], 205)                      # и случайно совпавшие соседние биты
        self.assertGreater(лучший[3], cikl.ДЛИННЫЙ_ЗАПАС_БИТ)
        self.assertEqual([], cikl.периоды_по_повторам(б, 32, 100_000))       # период вне диапазона
        self.assertEqual([], cikl.периоды_по_повторам(б, 200, 100))
        self.assertEqual([], cikl.периоды_по_повторам(б[:20], 2, 10))

    def test_слова_подряд(self):
        rng = np.random.default_rng(2)
        for n in (0, 23, 24, 25, 1000, 4099):
            б = rng.integers(0, 2, n, dtype=np.uint8)
            for ширина in (8, 16, 24, 25, 32):
                with self.subTest(n=n, ширина=ширина):
                    ждём = cikl._окна(б, ширина, 1)
                    self.assertEqual(ждём.tolist(), cikl.слова_подряд(б, ширина).tolist())
                    ключи = cikl.слова_подряд(б, ширина, с_местом=True)
                    self.assertEqual(ждём.tolist(), (ключи >> np.uint64(32)).tolist())
                    self.assertEqual(list(range(len(ждём))), (ключи & np.uint64(0xFFFFFFFF)).tolist())

    def test_гребёнка_и_пик_с_общей_автокорреляцией(self):
        """Автокорреляция одна на поиск цикла: с готовой — тот же ответ."""
        б, _ = кадры(512, 3000, слово_бит=16)
        r = cikl.автокорреляция_выборки(б)
        self.assertEqual(cikl.длина_цикла(б), cikl.длина_цикла(б, r))
        self.assertEqual(cikl.гребёнка(б), cikl.гребёнка(б, r))
        self.assertEqual(512, cikl.длина_цикла(б)[0])
        self.assertIsNone(cikl.длина_цикла(б[:1000]))                      # выборка мала


def длина_цикла_прежде(r, наибольший, n):
    """Эталон: прежний перебор вершин пика по одному периоду (до векторизации)."""
    шум = 1.0 / np.sqrt(n)
    лучший = int(np.argmax(r[cikl.ЦИКЛ_ОТ:])) + cikl.ЦИКЛ_ОТ
    пик = float(r[лучший])
    if пик < cikl.ПОРОГ_СИГМ * шум:
        return None
    for p in range(cikl.ЦИКЛ_ОТ, лучший + 1):
        if r[p] < 0.6 * пик:
            continue
        if p + 1 < len(r) and r[p + 1] > r[p]:
            continue
        кратные = [r[k * p] for k in range(2, 5) if k * p <= наибольший]
        if all(к >= 0.5 * пик for к in кратные):
            return p, float(r[p]), шум
    return лучший, пик, шум


def гребёнка_прежде(r, наибольший, n):
    """Эталон: прежняя гребёнка периодом за периодом."""
    z = np.zeros(наибольший // 2 + 2)
    for P in range(cikl.ЦИКЛ_ОТ, наибольший // 2 + 1):
        кратные = np.arange(1, min(cikl.ГРЕБЁНКА_ДО, наибольший // P) + 1) * P
        z[P] = float(r[кратные].sum()) / float(np.sqrt((1.0 / (n - кратные)).sum()))
    лучший = int(np.argmax(z))
    if z[лучший] < cikl.ПОРОГ_СИГМ:
        return None
    for P in range(cikl.ЦИКЛ_ОТ, лучший + 1):
        if z[P] >= 0.6 * z[лучший] and z[P] >= z[P - 1] and z[P] >= z[P + 1]:
            return P, float(r[P]), 1.0 / np.sqrt(n)
    return лучший, float(r[лучший]), 1.0 / np.sqrt(n)


def повторы_прямо(биты, от, до, *, ширина=24, соседей=3, повторов_от=2, запас=12.0):
    """Эталон периоды_по_повторам: пары перебором по спискам мест каждого слова, серии — по соседству."""
    from math import factorial, log2  # noqa: PLC0415
    слова = cikl._окна(биты, ширина, 1).tolist()
    n = len(слова)
    места = {}
    for i, w in enumerate(слова):
        места.setdefault(w, []).append(i)
    пары = set()
    for сп in места.values():
        for а in range(len(сп)):
            for k in range(1, соседей + 1):
                if а + k < len(сп) and от <= сп[а + k] - сп[а] <= до:
                    пары.add((сп[а + k] - сп[а], сп[а]))
    серии = {}
    for d, i in sorted(пары):
        if (d, i - 1) in пары:
            серии[d][-1] += 1
        else:
            серии.setdefault(d, []).append(1)
    итог = []
    for d, длины in серии.items():
        бит = sum(ширина + л - 1 - log2(n) for л in длины) - log2(factorial(len(длины))) - log2(до - от + 1)
        if len(длины) >= повторов_от and бит >= запас:
            итог.append((d, len(длины), sum(длины), бит))
    return итог


class ВекторизацияКакПреждеTests(unittest.TestCase):
    """Пик, гребёнка и повторы без перебора в питоне — то же, что прямой перебор (эталоны выше)."""

    def ряды(self, сид, наибольший=256):
        """Ряды автокорреляции с пиками, плато, кратными на грани половины пика и пиком в самом конце."""
        rng = np.random.default_rng(сид)
        r = rng.integers(-40, 40, наибольший + 2) / 1024.0
        r[0] = 1.0
        for _ in range(int(rng.integers(0, 4))):
            p = int(rng.integers(cikl.ЦИКЛ_ОТ - 2, наибольший + 2))
            r[p] = int(rng.integers(100, 400)) / 1024.0
            if rng.random() < 0.3 and p + 1 < len(r):
                r[p + 1] = r[p]                                   # плато: вершина — правый край
            for k in range(2, 5):
                if k * p < len(r) and rng.random() < 0.7:
                    r[k * p] = r[p] * float(rng.choice([0.49, 0.5, 0.51, 1.0]))
            if rng.random() < 0.3:
                r[p - 1] = r[p] * 0.6                              # склон ровно на 60 %
        return r

    def test_пик_как_прежде(self):
        n, наибольший = 4096, 256
        биты = np.zeros(n, np.uint8)
        совпало = 0
        for сид in range(600):
            r = self.ряды(сид)
            with self.subTest(сид=сид):
                ждём = длина_цикла_прежде(r[:наибольший + 1], наибольший, n)
                self.assertEqual(ждём, cikl.длина_цикла(биты, (r, наибольший)))
                совпало += ждём is not None
        self.assertGreater(совпало, 100)

    def test_пик_на_гранях(self):
        """Вершина ровно на 60 % пика, кратные ровно на половине, пятое кратное не смотрится, пик в последнем лаге."""
        n, наибольший = 4096, 256
        биты = np.zeros(n, np.uint8)

        def ряд(точки):
            r = np.zeros(наибольший + 2)
            r[0] = 1.0
            for к, v in точки.items():
                r[к] = v
            return r

        случаи = {
            "60 %": (ряд({100: 0.5, 40: 0.5 * 0.6, 80: 0.3, 120: 0.3, 160: 0.3, 200: 0.5}), 40),
            "половина": (ряд({200: 0.5, 40: 0.4, 80: 0.5 * 0.5, 120: 0.5 * 0.5, 160: 0.5 * 0.5}), 40),
            "пятое кратное": (ряд({230: 0.5, 40: 0.4, 80: 0.3, 120: 0.3, 160: 0.3, 200: 0.0}), 40),
            "последний лаг": (ряд({256: 0.5, 255: 0.4}), 256),
            "плато": (ряд({70: 0.5, 71: 0.5, 140: 0.3, 210: 0.3}), 70),
        }
        for имя, (r, ждём) in случаи.items():
            with self.subTest(имя):
                self.assertEqual(длина_цикла_прежде(r[:наибольший + 1], наибольший, n)[0], ждём)
                self.assertEqual(ждём, cikl.длина_цикла(биты, (r, наибольший))[0])

    def test_гребёнка_как_прежде(self):
        """И при лагах, где у малых периодов больше восьми кратных (наибольший 1024 при выборке 4096)."""
        найдено = 0
        for n, наибольший, сидов in ((4096, 256, 300), (4096, 1024, 150)):
            биты = np.zeros(n, np.uint8)
            for сид in range(сидов):
                r = self.ряды(сид, наибольший) * 4
                r[0] = 1.0
                with self.subTest(сид=сид, наибольший=наибольший):
                    ждём = гребёнка_прежде(r[:наибольший + 1], наибольший, n)
                    итог = cikl.гребёнка(биты, (r, наибольший))
                    self.assertEqual(ждём is None, итог is None)
                    if ждём is not None:
                        найдено += 1
                        self.assertEqual(ждём[0], итог[0])
                        self.assertAlmostEqual(ждём[1], итог[1])
        self.assertGreater(найдено, 50)

    def test_выборка_для_автокорреляции(self):
        """Меньше 64 циклов наименьшей длины (2048 бит) — цикл не ищется; ровно 2048 — ищется."""
        б, _ = кадры(64, 32, слово_бит=24, сдвиг=0)
        self.assertEqual(2048, len(б))
        self.assertEqual(64, cikl.длина_цикла(б)[0])
        self.assertIsNone(cikl.длина_цикла(б[:2047]))
        self.assertIsNone(cikl.гребёнка(б[:2047]))
        self.assertIsNone(cikl._узкий_цикл(б[:2047]))
        self.assertEqual(64, cikl._длинный_цикл(б)[0])
        self.assertIsNone(cikl._длинный_цикл(б[:2047]))
        self.assertIsNone(cikl._длинный_цикл(б[:2040]))

    def случаи_повторов(self):
        rng = np.random.default_rng(21)
        б = rng.integers(0, 2, 20_011, dtype=np.uint8)
        узор = rng.integers(0, 2, 40, dtype=np.uint8)
        for к in range(500, 20_000 - 40, 3000):                 # цикл 3000: шесть вхождений
            б[к:к + 40] = узор
        другой = rng.integers(0, 2, 30, dtype=np.uint8)
        for к in (1000, 1777, 9000, 9777):                      # два повтора на 777
            б[к:к + 30] = другой
        б[15_000:15_200] = 0                                     # нули: слово 0 повторяется через 1, 2, 3
        б[17_000:17_030] = б[2_100:2_130]                        # один длинный повтор
        return б

    def test_повторы_как_прямой_перебор(self):
        б = self.случаи_повторов()
        for кусок in (cikl.КУСОК_ПОВТОРОВ, 7, 8, 1000):
            for от, до, повторов_от, запас in ((1, len(б), 1, -1e9), (2, 2999, 1, -1e9), (3000, 3000, 2, -1e9),
                                               (32, len(б), 2, 12.0), (777, 6000, 1, 0.0)):
                with self.subTest(кусок=кусок, от=от, до=до):
                    from unittest import mock  # noqa: PLC0415
                    with mock.patch.object(cikl, "КУСОК_ПОВТОРОВ", кусок):
                        итог = cikl.периоды_по_повторам(б, от, до, повторов_от=повторов_от, запас=запас, сколько=10 ** 6)
                    ждём = повторы_прямо(б, от, до, повторов_от=повторов_от, запас=запас)
                    self.assertEqual(sorted(т[:3] for т in ждём), sorted(т[:3] for т in итог))
                    по = {т[0]: т[3] for т in ждём}
                    for d, _, _, бит in итог:
                        self.assertAlmostEqual(по[d], бит, delta=0.051)
                        self.assertAlmostEqual(бит * 10, round(бит * 10), places=6)   # бит — с одним знаком
                    self.assertEqual(sorted(итог, key=lambda т: -т[3]), итог)         # сильнейшие первыми
        # По умолчанию — от двух повторов и 12 бит запаса: цикл 3000 есть, одиночный длинный повтор — нет.
        итог = cikl.периоды_по_повторам(б, 32, len(б))
        self.assertEqual(3000, итог[0][0])
        self.assertNotIn(17_000 - 2_100, [т[0] for т in итог])
        self.assertEqual(1, len(cikl.периоды_по_повторам(б, 32, len(б), сколько=1)))

    def test_повторы_края(self):
        нули = np.zeros(25, np.uint8)                           # два слова, одинаковых, через 1 бит
        self.assertEqual([(1, 1, 1)], [т[:3] for т in cikl.периоды_по_повторам(нули, 1, 1, повторов_от=1, запас=-1e9)])
        self.assertEqual([], cikl.периоды_по_повторам(нули, 2, 1, повторов_от=1, запас=-1e9))
        self.assertEqual([], cikl.периоды_по_повторам(нули[:24], 1, 1, повторов_от=1, запас=-1e9))
        self.assertEqual([(1, 1, 1)], [т[:3] for т in cikl.периоды_по_повторам(нули, 0, 1, повторов_от=1, запас=-1e9)])

    def test_слова_и_типы(self):
        б = np.random.default_rng(8).integers(0, 2, 300, dtype=np.uint8)
        for ширина, тип in ((25, np.uint32), (26, np.uint32), (31, np.uint32), (32, np.uint32), (33, np.uint64)):
            with self.subTest(ширина=ширина):
                слова = cikl.слова_подряд(б, ширина)
                self.assertEqual(cikl._окна(б, ширина, 1).tolist(), слова.tolist())
                self.assertEqual(тип, слова.dtype)

    def test_длинный_цикл_граница_и_одиночные_повторы(self):
        """Ровно 16 циклов — цикл; повтор участка лишь в двух местах таблицы — не цикл (и не падает)."""
        б, _ = кадры(100_003, 16)
        self.assertEqual(100_003, cikl._длинный_цикл(б)[0])
        rng = np.random.default_rng(30)
        б = rng.integers(0, 2, 2_000_000, dtype=np.uint8)
        узор = rng.integers(0, 2, 64, dtype=np.uint8)
        for к in (5_000, 105_000, 205_000):
            б[к:к + 64] = узор
        self.assertIn(100_000, [т[0] for т in cikl.периоды_по_повторам(б, 32, len(б) // 3)])
        подсказки = []
        self.assertIsNone(cikl._длинный_цикл(б, подсказки))
        self.assertEqual([], подсказки)

    def test_короткая_выборка_подсказка(self):
        """Выборка 520 бит: циклы длиннее 32 бит не искались — так и сказано."""
        б = np.random.default_rng(5).integers(0, 2, 520, dtype=np.uint8)
        подсказки = []
        self.assertIsNone(cikl.найти(б, подсказки))
        self.assertEqual(["циклы длиннее 32 бит не искались — мало периодов в выборке"],
                         подсказки)


class ПоискПериодаTests(unittest.TestCase):
    """Окно F3 (rastr.поиск_периода) — без предела 65 536."""

    def test_период_200000(self):
        б, слово = кадры(200_000, 40, ошибок=0.001)
        нужно = rastr.бит_поиску_периода(1_000_000, 64)
        найдено = rastr.поиск_периода(б[:нужно], от=8, до=1_000_000)
        self.assertEqual((200_000, 777, 32, ""), (найдено[0]["период"], найдено[0]["первый_бит"],
                                                   найдено[0]["глубина"], найдено[0]["заметка"]))
        self.assertEqual("".join(map(str, слово)), найдено[0]["маркер"])
        self.assertGreater(найдено[0]["вес"], 0.9)
        self.assertIn((400_000, "кратен 200000"), [(н["период"], н["заметка"]) for н in найдено])

    def test_нечётный_и_мало_периодов(self):
        б, _ = кадры(150_001, 30)
        self.assertEqual(150_001, rastr.поиск_периода(б, от=1000, до=10 ** 6)[0]["период"])
        # Четыре периода и маркер в 64 бита — найден, но помечено: периодов мало.
        б, _ = кадры(200_000, 4, слово_бит=64)
        н = rastr.поиск_периода(б, от=8, до=10 ** 6)[0]
        self.assertEqual((200_000, 64, "мало периодов (4)", 4), (н["период"], н["глубина"], н["заметка"], н["циклов"]))
        # Три периода и маркер в 8 бит — так и случайно бывает (у трёх строк столбец устойчив с вероятностью ¼,
        # 8 подряд на 200 000 местах — трижды): не маркер. В 16 бит — уже неслучаен (5·10⁻⁵), найден.
        б, _ = кадры(200_000, 3, слово_бит=8)
        self.assertEqual([], [н for н in rastr.поиск_периода(б, от=150_000, до=250_000, шаг=1000) if н["период"] == 200_000])
        б, _ = кадры(200_000, 3, слово_бит=16)
        self.assertEqual([200_000], [н["период"] for н in rastr.поиск_периода(б, от=150_000, до=250_000, шаг=1000)][:1])

    def test_предел_поиска(self):
        self.assertEqual({"до": 1000, "бит": rastr.бит_поиску_периода(1000, 64), "подсказка": ""},
                         rastr.предел_поиска(10 ** 7, 8, 1000, 64))
        п = rastr.предел_поиска(1_000_000, 8, 10 ** 6, 64)
        self.assertEqual((333_333, 1_000_000), (п["до"], п["бит"]))
        self.assertEqual("длиннее 333 333 бит не искали — мало периодов", п["подсказка"])
        п = rastr.предел_поиска(2_000_000, 8, 500_000, 64)
        self.assertEqual(500_000, п["до"])
        self.assertEqual("длиннее 125 000 бит — по 3–15 периодам", п["подсказка"])
        # Без предела на огромном массиве — смотрит не больше ПОИСК_БИТ_ДО.
        п = rastr.предел_поиска(10 ** 10, 8, rastr.ПЕРИОД_ДО, 64)
        self.assertEqual(rastr.ПОИСК_БИТ_ДО, п["бит"])

    def test_миллионы_лагов_без_массива(self):
        """Конечный период — без предела: полтора миллиона лагов не перечисляются массивом."""
        б, _ = кадры(150_001, 30)
        self.assertGreater(len(б) // 3, rastr.ЛАГОВ_МАССИВОМ_ДО)
        найдено = rastr.поиск_периода(б, от=2, до=rastr.ПЕРИОД_ДО)
        self.assertEqual(150_001, найдено[0]["период"])

    def test_проверки_ввода(self):
        б, _ = кадры(1000, 50)
        for плохое in ({"от": 1}, {"от": 100, "до": 50}, {"до": rastr.ПЕРИОД_ДО + 1}):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                rastr.поиск_периода(б, **плохое)
        self.assertEqual([], rastr.поиск_периода(б[:300], от=200, до=250))            # меньше трёх периодов
        # Лаги — не короче наименьшей глубины маркера и на сетке «от + k·шаг»: 6 + 142·7 = 1000.
        self.assertEqual(1000, rastr.поиск_периода(б, от=6, до=1000, шаг=7, глубина_от=20)[0]["период"])
        self.assertNotIn(1000, [н["период"] for н in rastr.поиск_периода(б, от=5, до=1000, шаг=7, глубина_от=20)])


class РастрКадрыTests(unittest.TestCase):
    """Растр, кадры, маска и разметка — с длиной строки 200 000: окном, без выделения всего."""

    def setUp(self):
        self.б, self.слово = кадры(200_000, 20)

    def test_сетка_окном(self):
        с = rastr.сетка(self.б, 200_000, 777, 2, 10, 0, 64)
        self.assertEqual((20, 10, 64, "биты"), (с["всего_строк"], с["строк"], с["пикселей"], с["вид"]))
        import base64  # noqa: PLC0415
        биты = np.unpackbits(np.frombuffer(base64.b64decode(с["данные"]), np.uint8)).reshape(10, 64)
        self.assertTrue((биты[:, :32] == self.слово).all())
        # Столбцы в глубине строки и сжатие: в пикселе доля единиц.
        с = rastr.сетка(self.б, 200_000, 777, 0, 20, 150_000, 4000, 64)
        self.assertEqual((20, 63, "доли"), (с["строк"], с["пикселей"], с["вид"]))
        с = rastr.сетка(self.б, 10 ** 9, 0, 0, 5, 0, 10)                         # длиннее массива — строк нет
        self.assertEqual(0, с["строк"])

    def test_кадры_и_поле(self):
        т = rastr.кадры_таблицей(self.б, 200_000, 777, 3, 5)
        self.assertEqual((20, 25_000, 5), (т["всего"], т["байт_в_кадре"], len(т["номера"])))
        поле = rastr.поле_кадров(self.б, 200_000, 777, 0, 32)
        self.assertEqual(1, поле["различных"])
        self.assertEqual(int("".join(map(str, self.слово)), 2), int(поле["значения"][0]["значение"]))

    def test_маска_и_разметка(self):
        шаг = rastr.проверить_шаги([{"вид": "маска", "маска": {"период": 200_000, "сдвиг": 777, "позиции": list(range(32))}}])
        канал = rastr.по_маске(self.б, шаг[0]["маска"])
        self.assertEqual(20 * 32, len(канал))
        self.assertTrue((канал.reshape(20, 32) == self.слово).all())
        разметка = rastr.проверить_разметку({"правила": [{"период": 200_000, "сдвиг": 777, "ширина": 32}]})
        взято = rastr.по_разметке(self.б, разметка, "взять")
        self.assertTrue((взято.reshape(-1, 32) == self.слово).all())
        self.assertEqual(200_000, len(rastr.столбцы(self.б, 200_000, 777)))


class АвтокорреляцияTests(unittest.TestCase):
    def test_корзины_лагов(self):
        б, _ = кадры(1000, 1000, слово_бит=64)
        папка = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, папка, True)
        файл = папка / "a.bin"
        файл.write_bytes(np.packbits(б).tobytes())
        и = stol_raschety.Источник(str(файл), len(б))
        мало = stol_raschety.автокорреляция(и, 5000)
        self.assertEqual((5001, 1, 5000), (len(мало["r"]), мало["шаг"], мало["лагов"]))
        self.assertNotIn("лаги", мало)
        много = stol_raschety.автокорреляция(и, 10 ** 6)
        лагов = len(б[:cikl.ВЫБОРКА]) // 2
        шаг = -(-(лагов + 1) // stol_raschety.АВТОКОРРЕЛЯЦИЯ_ТОЧЕК)
        self.assertEqual((лагов, шаг), (много["лагов"], много["шаг"]))
        полная = cikl.автокорреляция(б[:cikl.ВЫБОРКА], лагов)
        for i in (0, 1, 100, len(много["r"]) - 1):
            корзина = полная[i * шаг:(i + 1) * шаг]
            self.assertEqual(i * шаг + int(np.argmax(корзина)), много["лаги"][i])
            self.assertAlmostEqual(float(корзина.max()), много["r"][i], places=4)
            self.assertAlmostEqual(float(корзина.min()), много["r_мин"][i], places=4)
        self.assertEqual(0, stol_raschety.автокорреляция(и, 0)["лагов"])

    def test_длина_блока_турбокода_по_пределу(self):
        """Всплески нарушений одного вида в каждом блоке 50 000 шагов: блок длиннее прежних 8192 — находится;
        предел короче — нет."""
        rng = np.random.default_rng(4)
        нарушения = np.zeros(400_000, np.uint8)
        всплеск = rng.integers(0, 2, 64).astype(np.uint8)
        for к in range(0, len(нарушения) - 64, 50_000):
            нарушения[к:к + 64] = всплеск
        self.assertEqual(50_000, turbo.длина_блока(нарушения)[0])
        for найдено in (turbo.длина_блока(нарушения, до=40_000), self.с_пределом(40_000, нарушения)):
            self.assertTrue(найдено is None or найдено[0] != 50_000)
        self.assertIsNone(turbo.длина_блока(np.zeros(100, np.uint8)))
        self.assertIsNone(turbo.длина_блока(np.ones(30, np.uint8) ^ (np.arange(30) % 2).astype(np.uint8)))

    @staticmethod
    def с_пределом(до, нарушения):
        with nastroyki.с_пределом(до):
            return turbo.длина_блока(нарушения)


class АвтоматTests(unittest.TestCase):
    """Разбор целиком: предел — из настройки задания (``период_до``)."""

    def test_разбор_находит_и_слушает_предел(self):
        б, _ = кадры(200_000, 20)
        р = разобрать(данные=np.packbits(б).tobytes(), имя="rrl.bin", профиль="быстро")
        self.assertIn("цикл 200000 бит, синхрослово 32 бит", [н.что for н in р.находки])
        # «Как в автомате» — то же, что автомат, и с пределом задания: короче цикла — цикла нет.
        self.assertEqual(200_000, razbor.шаг_как_автомат(б, "цикл")["сведения"].get("кадр"))
        with nastroyki.с_пределом(100_000):
            self.assertEqual({}, razbor.шаг_как_автомат(б, "цикл")["сведения"])

    def test_мало_периодов_в_итоге(self):
        б, _ = кадры(150_001, 10)
        р = разобрать(данные=np.packbits(б).tobytes(), имя="rrl.bin", профиль="быстро")
        self.assertTrue(any("похоже на цикл 150001 бит" in н and "мало периодов" in н for н in р.не_найдено), р.не_найдено)


class НастройкиЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client

    def дождаться(self, ид):
        import time  # noqa: PLC0415
        for _ in range(3000):
            с = self.к.get(f"/api/potok/{ид}").json()
            if с["состояние"] in ("готово", "ошибка"):
                return с
            time.sleep(0.05)
        self.fail("не дождались")

    def test_настройки_и_окна(self):
        к = self.к
        к.post("/api/auth/logout")
        self.assertEqual(401, к.get("/api/potok-settings").status_code)
        self.assertEqual(401, к.put("/api/potok-settings", json={"период_до": 5000}).status_code)
        self.сеть.login("engineer")
        н = к.get("/api/potok-settings").json()
        self.assertEqual({"период_до": 1_000_000}, н["settings"])
        self.assertEqual({"period_max_from": 64, "period_max_to": 2 ** 31 - 1, "no_limit": 0}, н["limits"])
        for плохое in ({"период_до": "abc"}, {"период_до": 10}, {"период_до": -1}, {"период_до": True},
                       {"период_до": 2 ** 31}):
            with self.subTest(плохое=плохое):
                ответ = к.put("/api/potok-settings", json=плохое)
                self.assertEqual(400, ответ.status_code)
                self.assertIn("предел периода — целое от 64", ответ.json()["error"])
        self.assertEqual({"период_до": 1_000_000}, к.get("/api/potok-settings").json()["settings"])
        # Поток с циклом 200 000: маленький предел — F3 не находит, большой — находит; конечный период в теле — сильнее.
        сид = к.post("/api/sessions", json={"name": "РРЛ"}).json()["id"]
        б, _ = кадры(200_000, 40)
        ид = к.post(f"/api/sessions/{сид}/files", files={"file": ("rrl.bin", np.packbits(б).tobytes(),
                                                                    "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        self.assertEqual({"период_до": 100_000}, к.put("/api/potok-settings", json={"период_до": "100 000"}).json()["settings"])
        d = к.post(f"/api/potok/{ид}/period-search", json={"from": 1000}).json()
        self.assertNotIn(200_000, [н["период"] for н in d["items"]])
        self.assertEqual(100_000, d["до"])
        d = к.post(f"/api/potok/{ид}/period-search", json={"from": 1000, "to": 300_000}).json()
        self.assertEqual(200_000, d["items"][0]["период"])
        к.put("/api/potok-settings", json={"период_до": 0})
        d = к.post(f"/api/potok/{ид}/period-search", json={"from": 1000}).json()
        self.assertEqual(200_000, d["items"][0]["период"])
        self.assertEqual((d["бит"] // 3, len(б) + 7 >> 3), (d["до"], d["бит"] >> 3))     # файл — целыми байтами
        self.assertIn("не искали — мало периодов", d["подсказка"])
        self.assertEqual(400, к.post(f"/api/potok/{ид}/period-search", json={"to": 2 ** 31}).status_code)
        # Пики автокорреляции и «Как в автомате» — с тем же пределом; задание запоминает предел владельца.
        к.put("/api/potok-settings", json={"период_до": 50_000})
        self.assertEqual(50_000, к.post(f"/api/potok/{ид}/as-auto", json={"step": "цикл"}).json()["параметры"]["период_до"])
        ид2 = к.post(f"/api/sessions/{сид}/files",
                     files={"file": ("rrl2.bin", np.packbits(б[:1 << 20]).tobytes(), "application/octet-stream")}).json()["id"]
        self.assertEqual(50_000, к.get(f"/api/potok/{ид2}").json()["период_до"])
        # Настройки у каждого свои.
        self.сеть.login("admin")
        self.assertEqual({"период_до": 1_000_000}, к.get("/api/potok-settings").json()["settings"])


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноВБраузереTests(unittest.TestCase):
    """Функции app.js: проверка ввода настройки, пики корзин автокорреляции, окно растра."""

    def выполнить(self, случаи):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js(["периодДоИзВвода", "периодДоСловами", "пикиАвтокорреляции", "отметитьКратныеПики", "пикиГрафика",
                          "окноРастра", "битРастра"], ["ПИК_ОТ_СИГМ", "РАСТР_ПОДРЯД_ДО"]) + """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const итог = случаи.map((с) => {
    switch (с.что) {
    case 'ввод': return с.v.map(([т, без]) => периодДоИзВвода(т, без, { period_max_from: 64, period_max_to: 2147483647 }));
    case 'словами': return с.v.map(периодДоСловами);
    case 'пики': return пикиГрафика(с.r, с.шум, с.лаги, с.шаг);
    case 'окно': return с.v.map((a) => окноРастра(...a));
    case 'бит': return с.v.map(([r, c]) => битРастра(с.с, r, c));
    default: return null;
    }
});
process.stdout.write(JSON.stringify(итог));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=60)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_ввод_настройки(self):
        ввод, словами = self.выполнить([
            {"что": "ввод", "v": [["200 000", False], ["1000000", False], ["0", False], ["", True], ["abc", True],
                                  ["63", False], ["2147483648", False], ["1e6", False], ["", False], ["12.5", False]]},
            {"что": "словами", "v": [0, 200000]}])
        self.assertEqual([{"значение": 200000}, {"значение": 1000000}, {"значение": 0}, {"значение": 0}, {"значение": 0}],
                         ввод[:5])
        for о in ввод[5:]:
            self.assertIn("ошибка", о)
        self.assertIn("От 64 до 2", ввод[5]["ошибка"])
        self.assertEqual("без предела", словами[0])
        self.assertIn("200", словами[1])

    def test_пики_корзин(self):
        шум = 0.001
        r = [1.0] + [0.0] * 999
        лаги = list(range(0, 8000, 8))
        r[25] = 0.05          # лаг 200
        лаги[25] = 203
        r[50] = 0.04          # лаг 406 — кратен 203
        лаги[50] = 406
        r[300] = 0.004        # 4 σ — у корзин из 8 лагов ниже порога (3 + √(2 ln 8) ≈ 5 σ)
        пики, без = self.выполнить([{"что": "пики", "r": r, "шум": шум, "лаги": лаги, "шаг": 8},
                                    {"что": "пики", "r": r, "шум": шум, "лаги": None, "шаг": 1}])
        self.assertEqual([(203, 25, 0), (406, 50, 203)], [(п["лаг"], п["индекс"], п["кратен"]) for п in пики])
        self.assertAlmostEqual(50.0, пики[0]["вес"])
        self.assertEqual([25, 50, 300], sorted(п["лаг"] for п in без))
        self.assertEqual([25, 50, 300], sorted(п["индекс"] for п in без))

    def test_окно_растра(self):
        окна, биты = self.выполнить([
            {"что": "окно", "v": [[64, 100, 0, 1000], [200000, 160, 150000, 1365], [200000, 10, 199990, 1000],
                                  [200000, 30, 0, 1000]]},
            {"что": "бит", "с": {"сетка": {"строк": 2, "столбцов": 12, "биты": [0b10000000, 0b00010000, 0, 0b00100000]},
                                 "период": 200000, "столбец": 0},
             "v": [[0, 0], [0, 11], [1, 10], [1, 0], [2, 0], [0, 12]]}])
        self.assertEqual({"окном": False, "строк": 100, "столбец": 0, "столбцов": 64}, окна[0])
        self.assertEqual({"окном": True, "строк": 160, "столбец": 150000, "столбцов": 1365}, окна[1])
        self.assertEqual({"окном": False, "строк": 10, "столбец": 199990, "столбцов": 10}, окна[2])
        self.assertTrue(окна[3]["окном"])
        self.assertEqual([1, 1, 1, 0, 0, 0], биты)


if __name__ == "__main__":
    unittest.main()
