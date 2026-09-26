# -*- coding: utf-8 -*-
"""TPC трёхмерный и в кадрах модема (Comtech): синхрослово, чередование, плоскость, выбор полного кода.

Коды собираются здесь же (кодер расширенного Хэмминга — из test_potok_tpc,
независимый от анализатора); кадр как у Comtech: синхрослово 20 бит в двух
чередующихся вариантах и блок (64, 57) × (46, 39) = 2944 бит.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from test_potok_tpc import хэмминг, тпк
from reportgen.potok import cikl, modem, ploskost, razbor, tpc

СИНХРО = [np.array([int(x) for x in "11110101000010111000"], np.uint8),
          np.array([int(x) for x in "00001010111101000111"], np.uint8)]
ЧУЖИЕ = {"порядок": "старший", "код": "Грей", "поворот": 3, "отражение": True, "метка": [1, 0, 2],
         "код_выход": "натуральный"}


def тпк3(данные, k=11, m=4):
    """Данные (блоков × k × k × k) → трёхмерный TPC по строкам, плоскость за плоскостью."""
    e = хэмминг(k, m)
    x = e(данные)                                                   # строки
    x = e(x.transpose(0, 1, 3, 2)).transpose(0, 1, 3, 2)            # столбцы
    return e(x.transpose(0, 3, 2, 1)).transpose(0, 3, 2, 1)         # глубина


def comtech(данные, *, приставка=501, ошибок=0.0, сид=1):
    """Кадры: синхрослово (чередуется) + блок (64, 57) × (46, 39); (поток, начала кадров)."""
    rng = np.random.default_rng(сид)
    блоки = тпк(данные, 57, 6, 39, 6).reshape(len(данные), 2944)
    кадры = np.stack([np.concatenate([СИНХРО[f % 2], блоки[f]]) for f in range(len(данные))])
    поток = np.concatenate([rng.integers(0, 2, приставка).astype(np.uint8), кадры.reshape(-1)])
    поток = поток ^ (rng.random(len(поток)) < ошибок).astype(np.uint8)
    return поток, [приставка + f * 2964 for f in range(len(данные))]


def совпадение(выход, данные):
    блок = int(np.prod(данные.shape[1:]))
    m = min(len(выход) // блок, len(данные))
    return float((выход[:m * блок].reshape(m, -1) == данные[:m].reshape(m, -1)).mean())


class ТрёхмерныйTpc(unittest.TestCase):
    def test_находится_и_декодируется(self):
        rng = np.random.default_rng(2)
        данные = rng.integers(0, 2, (60, 11, 11, 11)).astype(np.uint8)
        поток = np.concatenate([rng.integers(0, 2, 123).astype(np.uint8), тпк3(данные).reshape(-1)])
        поток ^= (rng.random(len(поток)) < 1e-3).astype(np.uint8)
        выход, находка = tpc.снять(поток)
        self.assertIn("трёхмерный", находка.что)
        self.assertIn("(16, 11) × (16, 11) × (16, 11)", находка.что)
        self.assertEqual(tpc.измерения(находка), (16, 16, 16))
        self.assertEqual(находка.свойства["начало_блока"], 123)
        self.assertEqual(совпадение(выход, данные), 1.0)

    def test_двумерный_не_становится_трёхмерным(self):
        rng = np.random.default_rng(3)
        данные = rng.integers(0, 2, (150, 57, 57)).astype(np.uint8)
        _, находка = tpc.снять(тпк(данные))
        self.assertNotIn("трёхмерный", находка.что)
        self.assertEqual(находка.свойства["глубина"], 0)
        with self.assertRaisesRegex(ValueError, "третьего измерения"):
            tpc.снять(тпк(данные), трёхмерный=True)

    def test_декодер_по_трём_осям(self):
        rng = np.random.default_rng(4)
        данные = rng.integers(0, 2, (4, 11, 11, 11)).astype(np.uint8)
        блоки = тпк3(данные)
        H = [tpc.проверки(tpc._слова_оси(блоки, ось)) for ось in (3, 2, 1)]
        порча = блоки.copy()
        порча[0, 1, 2, 3] ^= 1
        порча[1, 4, 5, 6] ^= 1
        порча[1, 4, 5, 9] ^= 1               # двойная в строке — её доделывают столбцы и глубина
        порча[2, 0, 0, 0] ^= 1
        порча[2, 9, 0, 0] ^= 1               # ещё двойная по глубине
        чистые, сводка = tpc.декодировать(порча, *H)
        self.assertTrue(np.array_equal(чистые, блоки))
        self.assertEqual(сводка["блоков_чисто"], 1.0)
        with self.assertRaisesRegex(ValueError, "глубины"):
            tpc.декодировать(порча, H[0], H[1])


class СтолбцыСоСкремблером(unittest.TestCase):
    def test_нечётные_столбцы_не_гасят_друг_друга(self):
        """Скремблер со сбросом на блоке делает часть столбцов нечётными: средняя по
        столбцам доля нечётных ≈ ½, но у каждого столбца перекос полный."""
        rng = np.random.default_rng(5)
        данные = rng.integers(0, 2, (120, 57, 57)).astype(np.uint8)
        блоки = тпк(данные).reshape(120, 64, 64)
        маска = np.zeros((64, 64), np.uint8)
        маска[0, ::2] = 1                    # в первой строке — каждый второй столбец инвертирован
        поток = (блоки ^ маска).reshape(-1)
        кандидаты = tpc.столбцы_по_чётности(поток.reshape(-1, 64))
        self.assertEqual((кандидаты[0]["длина"], кандидаты[0]["начало"]), (64, 0))
        _, находка = tpc.снять(поток)
        self.assertEqual((находка.свойства["k1"], находка.свойства["k2"]), (57, 57))


class ВКадрах(unittest.TestCase):
    def test_строки_от_начала_кадра_при_неточном_начале(self):
        rng = np.random.default_rng(6)
        данные = rng.integers(0, 2, (200, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные, ошибок=1e-3)
        for сдвиг in (-3, 0, 4):
            with self.subTest(сдвиг=сдвиг):
                выход, находка = tpc.снять_в_кадрах(поток, [н + сдвиг for н in начала], 2964)
                self.assertIn("(64, 57) × (46, 39)", находка.что)
                self.assertEqual(находка.свойства["строк_в_кадре"], 46)
                self.assertEqual(находка.свойства["начало_в_кадре"], 20 - сдвиг)
                self.assertGreater(совпадение(выход, данные), 0.9999)

    def test_сплошным_потоком_не_находится(self):
        """Синхрослово между блоками ломает строки сплошного потока — поэтому и нужен поиск в кадрах."""
        rng = np.random.default_rng(7)
        данные = rng.integers(0, 2, (200, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные)
        self.assertIsNone(tpc.найти(поток))
        self.assertIsNotNone(tpc.найти_в_кадрах(поток, начала, 2964))

    def test_мера_в_кадрах(self):
        rng = np.random.default_rng(8)
        данные = rng.integers(0, 2, (64, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные)
        self.assertGreater(tpc.мера_в_кадрах(поток, начала[:32], 2964), 50)
        шум = rng.integers(0, 2, len(поток)).astype(np.uint8)
        self.assertLess(tpc.мера_в_кадрах(шум, начала[:32], 2964), tpc.ПОРОГ_Z)


class ОшибкиВЛинии(unittest.TestCase):
    def test_доля_по_исправленным(self):
        rng = np.random.default_rng(23)
        данные = rng.integers(0, 2, (200, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные, ошибок=1e-3)
        _, находка = tpc.снять_в_кадрах(поток, начала, 2964)
        self.assertTrue(0.8e-3 < находка.свойства["ошибок_в_линии"] < 1.1e-3, находка.свойства)

    def test_осш_по_доле_ошибок(self):
        """Опоры из теории (не таблицы): ФМ-2 и ФМ-4 при 10⁻³ — Eb/N0 ≈ 6,8 дБ; ФМ-8 — ≈ 10 дБ."""
        from math import log10
        self.assertAlmostEqual(modem.ош_фм(1e-3, 1), 6.79, delta=0.02)
        self.assertAlmostEqual(modem.ош_фм(1e-3, 2) - 10 * log10(2), 6.79, delta=0.02)
        self.assertAlmostEqual(modem.ош_фм(1e-3, 3) - 10 * log10(3), 10.0, delta=0.1)
        self.assertGreater(modem.ош_фм(1e-5, 3), modem.ош_фм(1e-3, 3))
        self.assertIsNone(modem.ош_фм(0.0, 3))
        self.assertIsNone(modem.ош_фм(0.3, 3))


class ПовторыЗаполнения(unittest.TestCase):
    def test_без_повторов(self):
        rng = np.random.default_rng(12)
        окна = rng.integers(0, 2, (20, 300)).astype(np.uint8)
        окна[5] = окна[1]
        окна[9] = окна[1] ^ (rng.random(300) < 0.02)          # почти повтор (ошибки линии)
        окна[19] = окна[3]
        окна[12] = окна[2] ^ (rng.random(300) < 0.2)           # не повтор
        self.assertEqual(np.flatnonzero(~tpc.без_повторов(окна)).tolist(), [5, 9, 19])
        self.assertTrue(tpc.без_повторов(окна[:1]).all())

    def test_строки_среди_кадров_заполнения(self):
        """Три кадра из четырёх — заполнение, повторяющееся через 4 кадра: чётность любого
        окна в них постоянна, и короткие «строки» выделялись бы сильнее кода."""
        rng = np.random.default_rng(13)
        данные = rng.integers(0, 2, (240, 39, 57)).astype(np.uint8)
        заполнение = rng.integers(0, 2, (4, 39, 57)).astype(np.uint8)
        for f in range(240):
            if f % 4:
                данные[f] = заполнение[f % 4]
        поток, начала = comtech(данные, ошибок=1e-3)
        строки = tpc.строки_в_кадрах(поток, начала, 2964)
        self.assertEqual((строки[0]["длина"], строки[0]["начало"], строки[0]["строк"]), (64, 20, 46))
        выход, находка = tpc.снять_в_кадрах(поток, начала, 2964)
        self.assertIn("(64, 57) × (46, 39)", находка.что)
        self.assertGreater(совпадение(выход, данные), 0.9999)

    def test_сдвиг_блоков_после_выброса_повторов_проверяется(self):
        """Блок кода — два кадра; выброс одного «повтора» сдвинул бы блоки: найденное по кадрам
        с данными проверяется декодированием всех кадров, при неудаче — поиск по всем кадрам."""
        from unittest import mock
        rng = np.random.default_rng(21)
        данные = rng.integers(0, 2, (100, 39, 57)).astype(np.uint8)
        блоки = тпк(данные, 57, 6, 39, 6).reshape(200, 1472)       # полблока (23 строки) на кадр
        кадры = np.stack([np.concatenate([СИНХРО[f % 2], блоки[f]]) for f in range(200)])
        поток = np.concatenate([rng.integers(0, 2, 77).astype(np.uint8), кадры.reshape(-1)])
        начала = [77 + f * 1492 for f in range(200)]
        настоящий = tpc.без_повторов

        def с_выбросом(окна):
            маска = настоящий(окна)
            if len(маска) == 200:
                маска[3] = False                         # «повтор» — один кадр из середины блока
            return маска

        with mock.patch.object(tpc, "без_повторов", с_выбросом):
            выход, находка = tpc.снять_в_кадрах(поток, начала, 1492, строка=64, начало=20, строк=23)
        self.assertIn("(64, 57) × (46, 39)", находка.что)
        self.assertEqual(находка.уверенность, 1.0)
        self.assertGreater(совпадение(выход, данные), 0.9999)

    def test_одинаковые_плоскости_не_третье_измерение(self):
        """Заполнение из одинаковых плоскостей даёт «проверки глубины» x_i = x_j, но в блоках
        из разных плоскостей их нет — кода глубины нет."""
        rng = np.random.default_rng(14)
        данные = rng.integers(0, 2, (400, 11, 11)).astype(np.uint8)
        данные[:300] = данные[0]
        поток = тпк(данные, 11, 4, 11, 4).reshape(-1)
        поток ^= (rng.random(len(поток)) < 1e-3).astype(np.uint8)
        _, находка = tpc.снять(поток)
        self.assertEqual(находка.свойства["глубина"], 0)
        self.assertIn("(16, 11) × (16, 11), скорость 121/256", находка.что)

    def test_парные_плоскости_не_третье_измерение(self):
        """Плоскости парами (каждая пара своя): чётность и «проверка глубины» x₀ = x₁ есть,
        но блоки из разных плоскостей ей не подчиняются — это повторы, а не код глубины."""
        rng = np.random.default_rng(18)
        данные = rng.integers(0, 2, (400, 11, 11)).astype(np.uint8)
        данные[1:300:2] = данные[0:300:2]
        поток = тпк(данные, 11, 4, 11, 4).reshape(-1)
        _, находка = tpc.снять(поток)
        self.assertEqual(находка.свойства["глубина"], 0)
        self.assertEqual(tpc.измерения(находка), (16, 16))

    def test_маска_на_кадре_после_кода(self):
        """Скремблер со сбросом на кадре поверх кода: у каждой строки и столбца своя поправка
        чётности — строки видны по перекосу каждого места, проверки — по разностям блоков."""
        rng = np.random.default_rng(15)
        данные = rng.integers(0, 2, (200, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные, ошибок=1e-3)
        маска = rng.integers(0, 2, 2944).astype(np.uint8)
        for н in начала:
            поток[н + 20:н + 2964] ^= маска
        выход, находка = tpc.снять_в_кадрах(поток, начала, 2964)
        self.assertIn("(64, 57) × (46, 39)", находка.что)
        self.assertGreaterEqual(находка.уверенность, 0.99)
        self.assertTrue(any("постоянная маска" in п for п in находка.подробно))
        на_данных = маска.reshape(46, 64)[:39, :57].reshape(-1)
        блоки = выход.reshape(-1, 2223)
        self.assertGreater(float(((блоки ^ данные.reshape(200, -1)[:len(блоки)]) == на_данных).mean()),
                           0.9999)


def hdlc_данные(блоков, сид=1, флагов=24):
    """Кадры HDLC с IP и флагами в паузах — данные блоков TPC (блоков × 39 × 57)."""
    биты = np.unpackbits(np.frombuffer(с.hdlc(с.пакеты_ip(блоков * 2, сид=сид), флагов_между=флагов),
                                       np.uint8))
    return np.resize(биты, блоков * 2223).reshape(блоков, 39, 57)


class Плоскость(unittest.TestCase):
    def test_полный_код_а_не_подкод(self):
        """У частично верного варианта меток под ним тоже линейный код — только слабее;
        выбирается вариант с наибольшим числом проверок, а из снимающих код одинаково —
        тот, под которым данные открываются."""
        rng = np.random.default_rng(9)
        данные = hdlc_данные(160)
        поток, начала = comtech(данные, приставка=501)
        поток = поток[:len(поток) // 3 * 3]
        метки = ploskost.преобразовать_фм(поток, 3, **ЧУЖИЕ)
        метки ^= (rng.random(len(метки)) < 1e-3).astype(np.uint8)
        находки, выход = modem.код_в_кадрах(метки, [н - 3 for н in начала], 2964, фм=(3,))
        self.assertEqual([н.уровень for н in находки], ["плоскость", "код"])
        self.assertIn("(64, 57) × (46, 39)", находки[1].что)
        self.assertIn("поворот 3", находки[0].что)
        self.assertIn("натуральный → Грей", находки[0].что)
        self.assertTrue(any("данные открываются сразу" in п for п in находки[0].подробно))
        self.assertGreater(совпадение(выход, данные), 0.9999)

    def test_равноценные_варианты_на_случайных_данных(self):
        """Случайные данные не различают варианты, отличающиеся инверсией бита метки: берётся
        первый по мере, и данные — истинные с точностью до постоянной маски на блоке."""
        rng = np.random.default_rng(9)
        данные = rng.integers(0, 2, (160, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные, приставка=501)
        поток = поток[:len(поток) // 3 * 3]
        метки = ploskost.преобразовать_фм(поток, 3, **ЧУЖИЕ)
        находки, выход = modem.код_в_кадрах(метки, [н - 3 for н in начала], 2964, фм=(3,))
        self.assertTrue(any("ни под одним не проще прочих" in п for п in находки[0].подробно))
        блоки = выход.reshape(-1, 2223)
        маски = блоки ^ данные.reshape(160, -1)[:len(блоки)]
        self.assertTrue((маски == маски[0]).all())


class ПлоскостьПоВремени(unittest.TestCase):
    def test_подбор_в_нагрузке_ограничен_по_времени(self):
        """Без структуры полная мера нужна всем 331 варианту (минуты) — срок её обрывает."""
        import time
        шум = np.random.default_rng(22).integers(0, 2, 1 << 17).astype(np.uint8)
        начало = time.monotonic()
        self.assertIsNone(ploskost.найти_фм(шум, 3, бюджет=1.0))
        self.assertLess(time.monotonic() - начало, 30)

    def test_классы_по_инверсии(self):
        """Варианты класса отличаются лишь постоянной x ⊕ c в таблице меток."""
        классы = ploskost.классы_по_инверсии(3)
        self.assertEqual(sum(len(к) for к in классы), len(ploskost.варианты_фм(3)))
        все = np.unpackbits(np.arange(8, dtype=np.uint8)[:, None], axis=1)[:, 5:].reshape(-1)
        for класс in классы:
            образы = [ploskost.преобразовать_фм(все, 3, **в).reshape(-1, 3) for в in класс]
            for о in образы[1:]:
                self.assertTrue(((о ^ образы[0]) == (о ^ образы[0])[0]).all())


class ВыборРавноценных(unittest.TestCase):
    def _код(self, данные, маска):
        from reportgen.potok.nahodka import Находка
        return Находка(уровень="код", что="", уверенность=1.0, мера="",
                       дальше=(данные.reshape(-1, 2223) ^ маска).reshape(-1), вид_дальше="биты",
                       свойства={"данных_в_блоке": 2223})

    def test_ступени_простоты(self):
        rng = np.random.default_rng(16)
        данные = hdlc_данные(400, сид=3, флагов=120).reshape(-1)
        случайная = rng.integers(0, 2, 2223).astype(np.uint8)
        from test_potok_blok import лрп
        псп = лрп((2, 3, 9, 12), "101000011100", 2223)
        self.assertEqual(modem.простота(self._код(данные, np.zeros(2223, np.uint8)))[0], 3)
        self.assertEqual(modem.простота(self._код(данные, псп))[0], 2)
        self.assertEqual(modem.простота(self._код(данные, псп ^ случайная))[0], 1)
        i, пояснение = modem._выбрать_равноценный(
            [(None, self._код(данные, псп ^ случайная)), (None, self._код(данные, псп))])
        self.assertEqual(i, 1)
        self.assertIn("ПСП блока", пояснение)

    def test_скремблер_различает(self):
        """Под самосинхронизирующимся скремблером данные случайны у всех вариантов, но у верного
        скремблер снимается и открывает структуру — отрыв много больше."""
        rng = np.random.default_rng(17)
        данные = с.скремблировать(hdlc_данные(400, сид=4).reshape(-1), [3, 20])
        случайная = rng.integers(0, 2, 2223).astype(np.uint8)
        верный = self._код(данные, np.zeros(2223, np.uint8))
        порченый = self._код(данные, случайная)
        i, пояснение = modem._выбрать_равноценный([(None, порченый), (None, верный)])
        self.assertEqual(i, 1, пояснение)
        self.assertIn("сильнее всего проявляется скремблер", пояснение)


class РучныеСлои(unittest.TestCase):
    def test_tpc_в_кадрах(self):
        rng = np.random.default_rng(19)
        данные = rng.integers(0, 2, (200, 39, 57)).astype(np.uint8)
        поток, _ = comtech(данные, приставка=333, ошибок=1e-3)
        ряд, запись = razbor.снять_вручную(поток, "tpc кадр 2964")
        self.assertTrue(any("(64, 57) × (46, 39)" in п for п in запись.подробно))
        self.assertTrue(any("в кадрах по 2964 бит" in п for п in запись.подробно))
        self.assertGreater(совпадение(ряд, данные), 0.999)
        with self.assertRaisesRegex(ValueError, "кадры по 2000 бит не выделились"):
            razbor.снять_вручную(поток, "tpc кадр 2000")

    def test_tpc_измерения_вручную(self):
        rng = np.random.default_rng(20)
        данные = rng.integers(0, 2, (40, 11, 11, 11)).astype(np.uint8)
        поток = тпк3(данные).reshape(-1)
        _, трёх = razbor.снять_вручную(поток, "tpc глубина 16 плоскость 0")
        self.assertTrue(any("трёхмерный" in п for п in трёх.подробно))
        _, двух = razbor.снять_вручную(поток, "tpc двумерный")
        self.assertFalse(any("трёхмерный" in п for п in двух.подробно))
        self.assertTrue(any("(16, 11) × (16, 11), скорость 121/256" in п for п in двух.подробно))

    def test_аддитивный_кадр_при_флагах(self):
        """Сброс ПСП на каждом блоке, а в паузах — флаги: устойчивых столбцов нет, ПСП — по окнам."""
        from test_potok_blok import НАЧАЛЬНОЕ, ОТВОДЫ, hdlc_с_паузами, лрп, по_блокам
        данные = hdlc_с_паузами(сид=7)
        поток = по_блокам(данные, лрп(ОТВОДЫ, НАЧАЛЬНОЕ, 2223))
        ряд, запись = razbor.снять_вручную(поток, "аддитивный кадр 2223")
        self.assertTrue(np.array_equal(ряд, данные))
        self.assertTrue(any("1 + x^-2 + x^-3 + x^-9 + x^-12" in п for п in запись.подробно))
        ряд, _ = razbor.снять_вручную(поток, "аддитивный отводы 2,3,9,12 кадр 2223")
        self.assertTrue(np.array_equal(ряд, данные))
        with self.assertRaisesRegex(ValueError, "не восстановилась"):
            razbor.снять_вручную(поток, "аддитивный отводы 3,20 кадр 2223")


class Чередование(unittest.TestCase):
    def test_два_слова_через_полцикла(self):
        self.assertTrue(cikl.чередование([(0, 24), (2968, 24)], 5928))
        self.assertFalse(cikl.чередование([(0, 24), (1000, 24)], 5928))
        self.assertFalse(cikl.чередование([(0, 24)], 5928))
        self.assertFalse(cikl.чередование([(0, 24), (2968, 24)], 5927))

    def test_цикл_вдвое_и_начала_кадров(self):
        rng = np.random.default_rng(10)
        данные = rng.integers(0, 2, (300, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные, приставка=777, ошибок=1e-3)
        найдено = cikl.найти(поток)
        self.assertEqual(найдено.свойства["кадр"], 2964)
        self.assertTrue(найдено.свойства["чередование"])
        self.assertIn("синхрослово чередуется", найдено.что)
        свои, сведения = cikl.кадры(поток, найдено.свойства["длина"])
        self.assertEqual(сведения["кадр"], 2964)
        # Начала — через кадр и не дальше нескольких бит от истинных.
        self.assertEqual(set(np.diff(свои[:20]).tolist()), {2964})
        self.assertLessEqual(min(abs(свои[0] - н) for н in начала), 8)


class Автомат(unittest.TestCase):
    def test_comtech_8psk_tpc_скремблер_hdlc_ip(self):
        """8PSK по имени → кадр 2964 с чередующимся синхрословом → плоскость по коду в кадрах →
        TPC (64, 57) × (46, 39) → скремблер (3, 20) → HDLC → IP."""
        rng = np.random.default_rng(11)
        биты = np.unpackbits(np.frombuffer(с.hdlc(с.пакеты_ip(600)), np.uint8))
        биты = с.скремблировать(биты, [3, 20])
        F = len(биты) // 2223
        поток, _ = comtech(биты[:F * 2223].reshape(F, 39, 57), приставка=1001)
        поток = поток[:len(поток) // 3 * 3]
        метки = ploskost.преобразовать_фм(поток, 3, **ЧУЖИЕ)
        метки ^= (rng.random(len(метки)) < 1e-3).astype(np.uint8)
        р = razbor.разобрать(данные=np.packbits(метки).tobytes(), имя="comtech_8PSK.bit")
        уровни = [н.уровень for н in р.находки]
        отчёт = razbor.собрать_отчёт(р, 20000)
        self.assertEqual(уровни[:6], ["цикл", "плоскость", "код", "скремблер", "канальный", "сетевой"], отчёт)
        self.assertIn("кадр 2964 бит, синхрослово чередуется", р.находки[0].что)
        self.assertIn("(64, 57) × (46, 39)", р.находки[2].что)
        self.assertIn("1 + x^-3 + x^-20", р.находки[3].что)


if __name__ == "__main__":
    unittest.main()
