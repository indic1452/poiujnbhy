"""Ложные срабатывания: редкие слои (перемежение, стаффинг) доказывают себя сами.

Стаффинг и перемежение в потоках отдела редки, а их меры бывают высоки и у
случайных совпадений — поэтому автомат принимает их только с проверкой.
"""

import time
import unittest
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import cikl, razbor, tpc
from reportgen.potok.nahodka import Находка
from reportgen.potok.razbor import ПРОФИЛИ, Бюджет, _ступень


def бюджет():
    return Бюджет(конец=time.monotonic() + 120, профиль=ПРОФИЛИ["быстро"])


def детектор(имя, ряд):
    """Подменные детекторы: один «редкий» слой, выдающий заданный ряд."""
    def детекторы(выборка, б, глубина=1, путь="", блок=None):
        if глубина:
            return []
        return [(имя, lambda: Находка(уровень="перемежение", что="перемежение-обманка",
                                      уверенность=1.0, мера="подмена", дальше=ряд,
                                      вид_дальше="биты"), "после снятия перемежения")]
    return детекторы


class РедкиеСлои(unittest.TestCase):
    def test_перемежение_без_проверки_ниже_отвергнуто(self):
        шум = np.random.default_rng(1).integers(0, 2, 60000).astype(np.uint8)
        with mock.patch.object(razbor, "_детекторы", детектор("блочное перемежение над кодом", шум)):
            ветвь = _ступень(шум, 0, "", бюджет())
        self.assertFalse(any(н.уровень == "перемежение" for н in ветвь.находки))
        self.assertTrue(any("отвергнуто: слой редкий" in д for д in ветвь.другие), ветвь.другие)

    def test_перемежение_с_проверкой_ниже_принято(self):
        hdlc = np.unpackbits(np.frombuffer(с.hdlc(с.пакеты_ip(200)), np.uint8))
        шум = np.random.default_rng(2).integers(0, 2, 60000).astype(np.uint8)
        with mock.patch.object(razbor, "_детекторы", детектор("свёрточное перемежение Форни (I × M)", hdlc)):
            ветвь = _ступень(шум, 0, "", бюджет())
        уровни = [н.уровень for н in ветвь.находки]
        self.assertEqual(уровни[0], "перемежение")
        self.assertIn("канальный", уровни)

    def test_обычный_слой_не_редкий(self):
        шум = np.random.default_rng(3).integers(0, 2, 60000).astype(np.uint8)
        with mock.patch.object(razbor, "_детекторы", детектор("скремблер", шум)):
            ветвь = _ступень(шум, 0, "", бюджет())
        self.assertTrue(any(н.уровень == "перемежение" for н in ветвь.находки))


def мультиплекс(копий, *, кадров=3000, сид=4):
    """Кадр 200 бит: синхрослово 16 бит, управление — ``копий`` копий бита, место
    возможности — столбец 150: при стаффинге постоянный 0, иначе данные."""
    rng = np.random.default_rng(сид)
    таблица = rng.integers(0, 2, (кадров, 200)).astype(np.uint8)
    таблица[:, :16] = rng.integers(0, 2, 16)
    решение = (rng.random(кадров) < 0.3).astype(np.uint8)
    for к in range(копий):
        таблица[:, 40 + 50 * к] = решение
    таблица[решение == 1, 150] = 0
    return таблица.reshape(-1)


class Стаффинг(unittest.TestCase):
    def test_три_копии_и_пустое_место_стаффинг(self):
        текст = "\n".join(cikl.найти(мультиплекс(3)).подробно)
        self.assertIn("стаффинг: канал управления — столбцы 40, 90, 140", текст)
        self.assertIn("возможность — столбец 150, положительный", текст)

    def test_две_копии_служебный_бит(self):
        текст = "\n".join(cikl.найти(мультиплекс(2)).подробно)
        self.assertNotIn("стаффинг: канал управления", текст)
        self.assertIn("копий меньше 3", текст)


class Синхрослово(unittest.TestCase):
    def test_случайно_ожидаемое_слово_не_цикл(self):
        """Длинный цикл на коротком файле: одиночный постоянный бит находится случайно."""
        self.assertGreater(cikl.случайных_слов(16, 23148, 1), 0.1)
        self.assertLess(cikl.случайных_слов(16, 23148, 2), 1e-3)
        self.assertLess(cikl.случайных_слов(700, 5928, 8), 1e-100)

    def test_цикл_с_одним_постоянным_битом_не_находка(self):
        rng = np.random.default_rng(5)
        таблица = rng.integers(0, 2, (16, 4096)).astype(np.uint8)
        таблица[:, 100] = 1
        with mock.patch.object(cikl, "длина_цикла", return_value=(4096, 0.1, 0.001)), \
                mock.patch.object(cikl, "_узкий_цикл", return_value=None):
            self.assertIsNone(cikl.найти(таблица.reshape(-1)))

    def test_однородное_слово_слабее(self):
        rng = np.random.default_rng(6)
        таблица = rng.integers(0, 2, (600, 400)).astype(np.uint8)
        таблица[:, 50:62] = 0
        найдено = cikl.найти(таблица.reshape(-1))
        self.assertEqual(найдено.свойства["длина"], 400)
        self.assertAlmostEqual(найдено.уверенность, 0.5, places=2)
        self.assertTrue(any("скорее заполнение" in с_ for с_ in найдено.подробно))
        таблица[:, 50:62] = [1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 0]
        self.assertAlmostEqual(cikl.найти(таблица.reshape(-1)).уверенность, 1.0, places=2)


class КодПроизведения(unittest.TestCase):
    def test_без_чистых_блоков_не_находка(self):
        """Чётность нашлась, а декодирование не дало ни одного блока без нарушений."""
        находка = Находка(уровень="код", что="TPC", уверенность=0.2, мера="")
        with mock.patch.object(tpc, "снять", return_value=(np.zeros(8, np.uint8), находка)):
            self.assertIsNone(tpc.найти(np.zeros(8, np.uint8)))
        находка.уверенность = 0.6
        with mock.patch.object(tpc, "снять", return_value=(np.zeros(8, np.uint8), находка)):
            self.assertIs(tpc.найти(np.zeros(8, np.uint8)), находка)


if __name__ == "__main__":
    unittest.main()
