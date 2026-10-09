"""Слова LDPC в нагрузке сверхкадра модема с известными символами (``potok/sverhkadr.py``).

Синтетика повторяет строение записей «Arclight» (по read.me — DVB-S2 LDPC/BCH в своём кадре модема):
заголовок, слоты с маркером в начале, блоки пилотов в каждом седьмом слоте; слова кода подряд в остальном;
символы — в байтах файла тетрадами (младшая тетрада — первой). Поток собирается ``ldpc_potoki.сверхкадр_ldpc``.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import ldpc_potoki as лп
from reportgen.potok import razbor, sverhkadr
from reportgen.potok.razbor import слои_находки, снять_вручную

ИМЯ = "dvb-s2-16200-7200"
#: 84 слота по 400 бит (маркер 4, пилоты 72 в каждом седьмом) и заголовок 800 — нагрузка 2 × 16200.
СЛОТОВ = 84
P = 800 + СЛОТОВ * 400


def _поток(сверхкадров=12, ошибок=1e-3, **доп):
    б, c, u = лп.сверхкадр_ldpc(ИМЯ, сверхкадров, слотов=СЛОТОВ, бчх=False, **доп)
    return лп.ошибки(б, ошибок, сид=5), c, u


class Строение(unittest.TestCase):
    def test_известные_позиции_и_строение(self):
        б, _, _ = _поток()
        маска = sverhkadr.известные(б, P)
        self.assertEqual(800 + 12 * 72 + СЛОТОВ * 4, int(маска.sum()))
        с = sverhkadr.строение(маска)
        self.assertEqual(2800, с["шаг_пилотов"])
        self.assertEqual(400, с["шаг_маркеров"])

    def test_поправка_до_целого_числа_слов(self):
        б, _, _ = _поток()
        согласие = sverhkadr.совпадение(б, P)
        маска = sverhkadr.известные(б, P, согласие)
        лишние = маска.copy()
        лишние[[876, 877]] = True                      # случайно «постоянные» биты нагрузки у края участка
        согласие = согласие.copy()
        согласие[[876, 877]] = 0.9
        np.testing.assert_array_equal(маска, sverhkadr.под_слово(лишние, согласие, 16200))
        self.assertIsNone(sverhkadr.под_слово(маска, согласие, 16000))


class Опознание(unittest.TestCase):
    def test_тетрады_слова_и_слои(self):
        б, c, u = _поток()
        биты = np.unpackbits(np.frombuffer(лп.в_тетрадах(б), np.uint8))
        н = sverhkadr.найти(биты, P, срок=120)
        self.assertIsNotNone(н)
        self.assertIn("в нагрузке сверхкадра 34400 бит (2 слов на сверхкадр)", н.что)
        self.assertIn("тетрады", н.что)
        вышло = np.asarray(н.дальше).reshape(-1, 7200)
        self.assertEqual(len(u), int((вышло == u[:len(вышло)]).all(axis=1).sum()))
        слои = слои_находки(н)
        self.assertEqual(["реверс 8", "реверс 4", f"сверхкадр {P} слово 16200"], слои[:3])
        ряд = биты
        for слой in слои:
            ряд, _ = снять_вручную(ряд, слой)
        np.testing.assert_array_equal(np.asarray(н.дальше), ряд)

    def test_прямой_порядок_без_перебора(self):
        # 12 сверхкадров: на 10 граница второго слова в периоде выходила на бит раньше, синдром не обнулялся ни у
        # одного слова — такой код теперь (и верно) не находка.
        б, _, u = _поток(сверхкадров=12)
        н = sverhkadr.найти(б, P, верх=False, срок=120)
        self.assertIsNotNone(н)
        self.assertNotIn("тетрады", н.что)
        self.assertGreaterEqual(н.уверенность, 0.5)

    def test_бчх_не_стандарта_так_и_пишется(self):
        # Слова DVB-S2 без внешнего БЧХ стандарта: остаток не нулевой — честно, без находки БЧХ.
        б, _, _ = _поток(сверхкадров=12)
        н = sverhkadr.найти(б, P, верх=False, срок=120)
        self.assertTrue(any("остаток не нулевой" in п for п in н.подробно))

    def test_случайная_нагрузка_не_код(self):
        шум = np.random.default_rng(7).integers(0, 2, 12 * 32400).astype(np.uint8)
        б, _, _ = лп.сверхкадр_ldpc(ИМЯ, 12, слотов=СЛОТОВ, данные=шум)
        биты = np.unpackbits(np.frombuffer(лп.в_тетрадах(б), np.uint8))
        self.assertIsNone(sverhkadr.найти(биты, P, срок=120))

    def test_случайные_биты_не_сверхкадр(self):
        б = np.random.default_rng(8).integers(0, 2, 12 * P).astype(np.uint8)
        self.assertIsNone(sverhkadr.найти(б, P, срок=60))


class Автомат(unittest.TestCase):
    def test_цикл_сверхкадра_и_слова_ldpc(self):
        б, c, u = _поток(сверхкадров=24)
        р = razbor.разобрать(данные=лп.в_тетрадах(б), имя="s.bit", профиль="быстро")
        что = [н.что for н in р.находки]
        self.assertTrue(any("в нагрузке сверхкадра 34400 бит" in ч for ч in что), razbor.собрать_отчёт(р, 8000))
        н = next(н for н in р.находки if "в нагрузке сверхкадра" in н.что)
        вышло = np.asarray(н.дальше).reshape(-1, 7200)
        self.assertGreaterEqual(int((вышло == u[:len(вышло)]).all(axis=1).sum()), len(вышло) - 1)


if __name__ == "__main__":
    unittest.main()
