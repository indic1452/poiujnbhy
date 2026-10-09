"""Поток «как модем CASC»: пилотная вставка → кадр с синхрословом → QC-LDPC с проверками в начале слова →
аддитивная ПСП 1 + x² + x³ + x⁹ + x¹² со сбросом на слове (байты данных младшим битом вперёд).

Эталон — ``casc_sintez`` (свой кодер по лестнице, своя ПСП, своя вставка — без кода анализатора). Каждая
восстановленная проверка сверяется с истинными кодовыми словами (ортогональна каждому), данные после
декодирования — с закодированными, данные после снятия ПСП — с исходными байтами.
"""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import casc_sintez as cs
from reportgen.potok import cikl, gf2, ldpc, ldpc_nagruzka, ldpc_yadro, razbor, skrembler, vstavka
from reportgen.potok.razbor import слои_находки, снять_вручную

_ПОТОК = None


def поток() -> cs.Поток:
    global _ПОТОК
    if _ПОТОК is None:
        _ПОТОК = cs.поток(700)
    return _ПОТОК


def _ортогональна(слова: np.ndarray, h: np.ndarray) -> bool:
    return not np.bitwise_xor.reduce(слова[:, h], axis=1).any()


class Вставка(unittest.TestCase):

    def test_пилоты_внутри_кадра(self):
        п = поток()
        цикл = cikl.найти(п.биты[:razbor.ВЫБОРКА_БИТ])
        self.assertIsNotNone(цикл)
        в = vstavka.в_цикле(п.биты, int(цикл.свойства["длина"]))
        self.assertIsNotNone(в)
        self.assertEqual((128, (38, 39), "11"), (в.период, в.места, в.значения))
        self.assertLess(в.ошибок, 1e-3)

    def test_без_вставки_кадр_с_синхрословом(self):
        п = поток()
        в = vstavka.проверить(п.биты, 128)
        ряд = vstavka.снять(п.биты, в)
        места = np.arange(len(п.биты)) % 128
        self.assertTrue(np.array_equal(п.биты[(места != 38) & (места != 39)], ряд))
        цикл = cikl.найти(ряд[:razbor.ВЫБОРКА_БИТ])
        self.assertEqual(п.кадр, int(цикл.свойства["длина"]))

    def test_сплошной_поток(self):
        """Пилоты в случайных данных без кадра — по автокорреляции."""
        rng = np.random.default_rng(4)
        биты = rng.integers(0, 2, 1 << 20, dtype=np.uint8)
        биты.reshape(-1, 256)[:, [100, 101]] = 1
        в = vstavka.найти(биты)
        self.assertIsNotNone(в)
        self.assertEqual((256, (100, 101), "11"), (в.период, в.места, в.значения))

    def test_синхрослово_кадра_не_вставка(self):
        """Кадр с длинным синхрословом и случайной нагрузкой — не вставка; случайный поток — тоже."""
        rng = np.random.default_rng(5)
        кадры = rng.integers(0, 2, (600, 2048), dtype=np.uint8)
        кадры[:, :32] = rng.integers(0, 2, 32, dtype=np.uint8)
        ряд = кадры.reshape(-1)
        self.assertIsNone(vstavka.в_цикле(ряд, 2048))
        self.assertIsNone(vstavka.найти(ряд))
        self.assertIsNone(vstavka.найти(rng.integers(0, 2, 1 << 20, dtype=np.uint8)))

    def test_ручной_слой(self):
        п = поток()
        ряд, запись = снять_вручную(п.биты, "пилоты период 128 места 38,39 значения 11")
        в = vstavka.проверить(п.биты, 128)
        self.assertTrue(np.array_equal(vstavka.снять(п.биты, в), ряд))
        self.assertTrue(any("совпали у 99." in с or "совпали у 100" in с for с in запись.подробно))
        находка = vstavka.находка(в, len(п.биты), len(ряд), кадр=0)
        self.assertEqual(["пилоты период 128 места 38,39 значения 11"], слои_находки(находка))


class Ядро(unittest.TestCase):

    def test_зависимые_строки(self):
        """Слово с ошибкой — вне линейных зависимостей слов, кодовые — в них."""
        п = поток()
        W = п.слова[:2100].copy()
        W[[5, 77], [600, 1500]] ^= 1
        зависимые, ранг = gf2.зависимые_и_ранг(W)
        self.assertLess(ранг, W.shape[1])
        self.assertFalse(зависимые[5] or зависимые[77])
        self.assertGreater(зависимые.mean(), 0.9)

    def test_восстановление(self):
        п = поток()
        rng = np.random.default_rng(9)
        W = п.слова ^ (rng.random(п.слова.shape) < 1e-4).astype(np.uint8)
        итог = ldpc_yadro.восстановить(W, срок=60)
        self.assertEqual(п.код.r, итог.ранг)
        for h in итог.проверки:
            self.assertTrue(_ортогональна(п.слова, h))
        self.assertTrue(итог.в_начале)
        self.assertTrue(np.array_equal(np.arange(п.код.r, п.код.n), итог.данные))
        self.assertEqual(64, итог.Z)
        исправленные, ок, _ = ldpc_yadro.декодировать(W, итог.проверки)
        self.assertTrue(ок.all())
        self.assertTrue(np.array_equal(п.слова, исправленные))

    def test_слов_мало(self):
        п = поток()
        итог = ldpc_yadro.восстановить(п.слова[:1000], срок=10)
        self.assertEqual(0, итог.ранг)
        self.assertIn("меньше длины слова", итог.почему)


class Нагрузка(unittest.TestCase):

    def _без_вставки(self) -> np.ndarray:
        п = поток()
        return vstavka.снять(п.биты, vstavka.проверить(п.биты, 128))

    def test_слова_в_нагрузке(self):
        п = поток()
        ряд = self._без_вставки()
        with mock.patch.object(ldpc, "КАТАЛОГ", None):
            находка, почему = ldpc_nagruzka.найти(ряд, п.кадр, срок=120)
        self.assertIsNotNone(находка, почему)
        с = находка.свойства
        self.assertEqual((4, 2048, 1536), (с["слов_в_кадре"], с["n"], с["k"]))
        self.assertTrue(с["проверки_в_начале"])
        данные = np.asarray(находка.дальше).reshape(-1, 1536)
        истинные = п.слова[:, п.код.r:]
        self.assertTrue(np.array_equal(истинные[:len(данные)], данные))

    def test_сохранённая_матрица(self):
        """Восстановленная H — в папку отдела; второй разбор берёт её сразу."""
        п = поток()
        ряд = self._без_вставки()
        with tempfile.TemporaryDirectory() as папка, mock.patch.object(ldpc, "КАТАЛОГ", Path(папка)):
            первая, _ = ldpc_nagruzka.найти(ряд, п.кадр, срок=120)
            имя = первая.свойства["матрица_файл"]
            self.assertTrue(имя and (Path(папка) / f"{имя}.alist").exists())
            начато = time.monotonic()
            вторая, _ = ldpc_nagruzka.найти(ряд, п.кадр, срок=120)
            self.assertLess(time.monotonic() - начато, 30)
            self.assertIn("сохранённая", " ".join(вторая.подробно))
            self.assertTrue(np.array_equal(np.asarray(первая.дальше), np.asarray(вторая.дальше)))
            self.assertTrue(слои_находки(вторая)[0].startswith(f"ldpc {имя} начало "))


class ПСПблока(unittest.TestCase):

    def test_байты_младшим_вперёд(self):
        п = поток()
        данные = п.слова[:, п.код.r:].reshape(-1)
        найдено = skrembler.по_блоку(данные, п.код.k)
        self.assertIsNotNone(найдено)
        self.assertEqual([2, 3, 9, 12], sorted(найдено.свойства["отводы"]))
        self.assertTrue(найдено.свойства["младший"])
        self.assertEqual(1536, найдено.свойства["длина_блока"])
        self.assertTrue(np.array_equal(np.unpackbits(п.данные, axis=1).reshape(-1), найдено.дальше))
        self.assertEqual(["реверс 8", "аддитивный блок 1536"], слои_находки(найдено))


class Автомат(unittest.TestCase):

    def test_цепочка(self):
        """Автомат сам: вставка → кадр 8256/64 → LDPC (2048, 1536), 4 слова в кадре → ПСП 12,9,3,2."""
        п = поток()
        with mock.patch.object(ldpc, "КАТАЛОГ", None):
            разбор = razbor.разобрать(данные=cs.в_байты(п.биты), имя="casc.bin", профиль="обычно")
        уровни = [н.уровень for н in разбор.находки]
        self.assertEqual(["вставка", "цикл", "код", "скремблер"], уровни[:4], разбор.отчёт())
        вставка, цикл, код, псп = разбор.находки[:4]
        self.assertEqual(128, вставка.свойства["период"])
        self.assertEqual(п.кадр, int(цикл.свойства["длина"]))
        self.assertIn("LDPC (2048, 1536)", код.что)
        self.assertEqual([2, 3, 9, 12], sorted(псп.свойства["отводы"]))


class АвтоматДругойПорядок(unittest.TestCase):

    def test_цепочка_младший_первым_и_инверсия(self):
        """Тот же поток, но байты файла — младшим битом вперёд и слова кода инвертированы (у части проверок
        нечётный вес): известного кода нет ни в одном порядке, матрица восстанавливается в обоих; верный
        порядок — тот, где проверки квазицикличны; инверсия — постоянная добавка к словам (опорное слово)."""
        п = поток()
        линия = п.биты.copy()
        # Инверсия слов кода — всех бит линии, кроме пилотов (пилоты — «11» и после инверсии остаются местами
        # вставки: значение «00» на тех же местах).
        линия ^= 1
        байты = np.packbits(линия[:len(линия) // 8 * 8], bitorder="little").tobytes()
        with mock.patch.object(ldpc, "КАТАЛОГ", None):
            разбор = razbor.разобрать(данные=байты, имя="casc_lsb.bin", профиль="обычно")
        уровни = [н.уровень for н in разбор.находки]
        self.assertEqual(["вставка", "цикл", "код", "скремблер"], уровни[:4], разбор.отчёт())
        вставка, цикл, код, псп = разбор.находки[:4]
        self.assertIn("младший первым", вставка.что)
        self.assertEqual(["реверс 8", "пилоты период 128 места 38,39 значения 00"], слои_находки(вставка))
        self.assertEqual(п.кадр, int(цикл.свойства["длина"]))
        self.assertIn("LDPC (2048, 1536)", код.что)
        self.assertGreaterEqual(код.уверенность, 0.95)
        self.assertEqual([2, 3, 9, 12], sorted(псп.свойства["отводы"]))


if __name__ == "__main__":
    unittest.main()
