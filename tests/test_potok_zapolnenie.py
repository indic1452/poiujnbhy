"""Заполнение (флаги 0x7E, нули) между случайными данными — не код; настоящий код на таком потоке находится.

Под скремблером модема бывают зашифрованные данные вперемешку с заполнением (Arclight: флаги 0x7E в
любой битовой фазе, целые блоки заполнения в начале записи). Заполнение выполняет проверки любого
линейного кода по своему узору, и меры кодов без учёта этого давали «находки» на случайных данных:
блочный (4, 2), выколотый 7/8, РС (6, 2) над GF(8)… Меры считаются по словам вне заполнения.
"""

from __future__ import annotations

import time
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import (
    bity,
    dlinnye,
    kod,
    rs_bch,
    rs_peremezhenie,
    rs_slepoy,
    svyortka,
    vykalyvanie,
)
from reportgen.potok.razbor import ПРОФИЛИ, Бюджет, _ступень

ФЛАГ = np.unpackbits(np.array([0x7E], dtype=np.uint8))


def случайные_с_заполнением(блоков: int = 64, блок: int = 16200, сид: int = 7,
                            данные=None) -> np.ndarray:
    """Блоки: в начале блока данные (случайные или ``данные``), остаток — флаги 0x7E в случайной битовой
    фазе; первые два блока — заполнение целиком."""
    г = np.random.default_rng(сид)
    куски = []
    взято = 0
    for b in range(блоков):
        длина = 0 if b < 2 else int(г.integers(0, блок))
        if данные is None:
            куски.append(г.integers(0, 2, длина, dtype=np.uint8))
        else:
            куски.append(данные[взято:взято + длина])
            взято += длина
        ф = int(г.integers(0, 8))
        куски.append(np.tile(ФЛАГ, блок // 8 + 2)[ф:ф + блок - длина])
    return np.concatenate(куски).astype(np.uint8)


class МаскаTests(unittest.TestCase):
    def test_случайные_без_заполнения(self):
        self.assertFalse(bity.заполнение(с.случайные_биты(1 << 18)).any())

    def test_флаги_и_нули_отмечены(self):
        ряд = np.concatenate([с.случайные_биты(5000, 1), np.tile(ФЛАГ, 100)[3:700], np.zeros(300, np.uint8),
                              с.случайные_биты(5000, 2)])
        м = bity.заполнение(ряд)
        self.assertTrue(м[5000:5997].all())
        self.assertFalse(м[:4990].any())
        self.assertFalse(м[6010:].any())
        # Окна и символы целиком в заполнении.
        self.assertEqual([False, True, False], bity.в_заполнении(м, np.array([4980, 5100, 5990]), 32).tolist())
        симв = bity.символы_в_заполнении(м, 8, 0)
        self.assertTrue(симв[630:745].all())
        self.assertFalse(симв[:620].any())


class СлучайныеTests(unittest.TestCase):
    """Случайные данные с заполнением: ни один детектор кода не находит."""

    @classmethod
    def setUpClass(cls):
        cls.ряд = случайные_с_заполнением()

    def test_короткие_коды(self):
        self.assertIsNone(kod.блочный_устойчивый(self.ряд))
        self.assertIsNone(kod.свёрточный(self.ряд))
        self.assertIsNone(svyortka.найти(self.ряд))

    def test_выколотый(self):
        self.assertIsNone(vykalyvanie.найти(self.ряд, стандартные=True, бюджет=20))
        self.assertIsNone(vykalyvanie.найти(self.ряд, бюджет=10))

    def test_рс_с_перемежением(self):
        self.assertIsNone(rs_peremezhenie.найти(self.ряд, профиль="быстро", бюджет=10, профили="основные",
                                                вслепую=False))

    def test_чистые_слова_против_случая(self):
        """RS(6, 2) над GF(8): 8 чистых из 20 000 — случайно (ожидание ≈ 4,9); у RS(255, 223) — нет."""
        self.assertFalse(rs_slepoy.чисто_не_случайно(8, 20000, 3 * 4))
        self.assertFalse(rs_slepoy.чисто_не_случайно(12, 20000, 3 * 4))
        self.assertTrue(rs_slepoy.чисто_не_случайно(40, 20000, 3 * 4))
        self.assertTrue(rs_slepoy.чисто_не_случайно(8, 20000, 8 * 32))


class НастоящийКодTests(unittest.TestCase):
    """Код поверх данных с заполнением находится: слова вне заполнения проходят проверки целиком."""

    def test_выколотый_3_4_над_заполнением(self):
        данные = случайные_с_заполнением(блоков=12, блок=8000, сид=3)
        шаблон = (1, 1, 0, 1, 1, 0)
        поток = vykalyvanie.выколоть(с.свёрточный(данные), шаблон)
        найдено = vykalyvanie.найти(поток, стандартные=True, бюджет=20)
        self.assertIsNotNone(найдено)
        self.assertIn("скорости 3/4", найдено.что)
        self.assertGreater(найдено.уверенность, 0.9)

    def test_свёрточный_1_2_над_заполнением(self):
        данные = случайные_с_заполнением(блоков=12, блок=8000, сид=4)
        найдено = kod.свёрточный(с.свёрточный(данные))
        self.assertIsNotNone(найдено)
        self.assertIn("171/133", найдено.что)

    def test_хэмминг_над_заполнением(self):
        данные = случайные_с_заполнением(блоков=12, блок=8000, сид=5)
        найдено = kod.блочный_устойчивый(с.хэмминг74(данные))
        self.assertIsNotNone(найдено)
        self.assertEqual((7, 4), (найдено.свойства["n"], найдено.свойства["k"]))

    def test_рс_над_заполнением(self):
        """RS(204, 188): у 60 % слов данные — 0xFF (как нулевые пакеты), проверочные байты другие — слово
        целиком в заполнении не лежит, и доля чистых — по всем словам; слова целиком из нулей — не в счёт."""
        г = np.random.default_rng(6)
        слова = []
        for i in range(400):
            данные = [0xFF] * 188 if г.random() < 0.6 else г.integers(0, 256, 188).tolist()
            if i % 10 == 9:
                данные = [0] * 188                    # нулевое слово кодовое у любого кода
            слова.append(rs_bch.закодировать_рс(данные, 0x11D, 0, 16))
        ряд = np.unpackbits(np.array(слова, dtype=np.uint8).reshape(-1))
        п = {"многочлен": 0x11D, "fcr": 0, "шаг": 1, "корней": 16, "доля": 1.0, "m": 8}
        код = rs_slepoy.собрать(ряд, m=8, фаза=0, I=1, N=204, п=п, порядок="старший", базис="обычный")
        self.assertIsNotNone(код)
        self.assertEqual(1.0, код.чистых_до)
        self.assertEqual(400, код.слов)
        # Те же слова, но вместо данных — сплошные нули: чистые все, но в счёт не идёт ни одно.
        нули = np.zeros(len(ряд), dtype=np.uint8)
        self.assertIsNone(rs_slepoy.собрать(нули, m=8, фаза=0, I=1, N=204, п=п, порядок="старший",
                                            базис="обычный"))


class СкользящиеTests(unittest.TestCase):
    """Связи выколотого свёрточного кода видны и в окне длины n как «блочный (40, 37)»; но они держатся при
    любом сдвиге на период кода — это не слова по n (NSC 3/4 у образца отдела)."""

    def test_выколотый_3_4_не_блочный(self):
        шаблон = (1, 1, 0, 1, 1, 0)
        поток = vykalyvanie.выколоть(с.свёрточный(с.случайные_биты(60_000, 8)), шаблон)
        g1, g2, K = vykalyvanie.МАТЕРИНСКИЕ[0]
        H = vykalyvanie.проверки_кандидата(g1, g2, K, шаблон, 3)
        self.assertEqual(4, dlinnye._скользящие(поток, H.shape[1], 0, H))

    def test_блочный_код_держится_только_на_своём_выравнивании(self):
        H = с.qc_ldpc(12, 24, 27, 4, сид=3)
        G, _ = с.систематический(H)
        слова = (с.случайные_биты(300 * len(G), 9).reshape(300, len(G)).astype(np.int64) @ G) % 2
        self.assertEqual(0, dlinnye._скользящие(слова.reshape(-1).astype(np.uint8), H.shape[1], 0, H))


class АвтоматTests(unittest.TestCase):
    def test_ступень_на_случайных_с_заполнением_не_растит_цепочку(self):
        """Уровень разбора на случайных данных с заполнением: ни одного преобразования."""
        ряд = случайные_с_заполнением(блоков=24)
        б = Бюджет(конец=time.monotonic() + 25, профиль={**ПРОФИЛИ["быстро"], "глубина": 1})
        ветвь = _ступень(ряд, 0, "", б)
        self.assertEqual([], [н.что for н in ветвь.находки])


if __name__ == "__main__":
    unittest.main()
