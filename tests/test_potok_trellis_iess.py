"""Trellis (прагматический TCM EN 301 210) и внешний РС IESS: свой кодер → ошибки → данные.

Кодеры тестов — ``turbo_koder.dsng_tcm`` (точки созвездия по рис. 7 и 13, раскладка байтов
по табл. 3–4) и ``turbo_koder.iess_rs`` (поле и корни ETR 192 п. 6.1, умножение сдвигами) —
написаны отдельно от анализатора.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import turbo_koder as тк
from reportgen.potok import iess, razbor, trellis


def метки(точки_: np.ndarray, режим: str) -> np.ndarray:
    т, м = trellis.точки(режим)
    return м[np.abs(точки_[:, None] - т[None, :]).argmin(axis=1)]


def в_биты(метки_: np.ndarray, k: int) -> np.ndarray:
    return ((метки_[:, None] >> np.arange(k - 1, -1, -1)) & 1).reshape(-1).astype(np.uint8)


class TrellisTests(unittest.TestCase):
    ДАННЫЕ = np.random.default_rng(3).integers(0, 2, 24 * 1500).tolist()

    def test_кодер_совпадает(self):
        for режим in ("8psk", "16qam"):
            with self.subTest(режим=режим):
                self.assertTrue(np.array_equal(
                    метки(np.array(тк.dsng_tcm(self.ДАННЫЕ, режим)), режим),
                    trellis.закодировать(np.array(self.ДАННЫЕ), режим)))

    def test_8psk_ошибки_в_соседнюю_точку(self):
        точки_ = np.array(тк.dsng_tcm(self.ДАННЫЕ, "8psk"))
        случай = np.random.default_rng(4)
        сбой = случай.random(len(точки_)) < 0.03
        точки_[сбой] *= np.exp(1j * np.pi / 4 * случай.choice([-1, 1], сбой.sum()))
        ряд, находка = razbor.снять_вручную(в_биты(метки(точки_, "8psk"), 3), "trellis 8psk")
        self.assertTrue(np.array_equal(np.array(self.ДАННЫЕ[:len(ряд)]), ряд))
        self.assertTrue(any("поворот 0" in п for п in находка.подробно))

    def test_8psk_метки_греем_с_поворотом(self):
        # Плоскость отдала номер точки кодом Грея и с поворотом на 3 шага: перебор находит.
        т, м = trellis.точки("8psk")
        номер = np.abs(np.array(тк.dsng_tcm(self.ДАННЫЕ, "8psk"))[:, None] - т[None, :]).argmin(axis=1)
        сдвинутый = (номер - 3) % 8
        грей = сдвинутый ^ (сдвинутый >> 1)
        ряд, находка = razbor.снять_вручную(в_биты(грей, 3), "trellis 8psk")
        # Поворот на 180° неразличим (код прозрачен к дополнению): данные — те же или U1 инвертирован.
        данные = np.array(self.ДАННЫЕ[:len(ряд)]).reshape(-1, 16)
        ряд = ряд.reshape(-1, 16)
        self.assertTrue(np.array_equal(данные[:, :8], ряд[:, :8]) or np.array_equal(данные[:, :8], 1 - ряд[:, :8]))
        self.assertTrue(any("код «грей»" in п for п in находка.подробно))

    def test_16qam(self):
        точки_ = np.array(тк.dsng_tcm(self.ДАННЫЕ, "16qam"))
        ряд, _ = razbor.снять_вручную(в_биты(метки(точки_, "16qam"), 4), "trellis 16qam поворот 0 код dsng")
        self.assertTrue(np.array_equal(np.array(self.ДАННЫЕ[:len(ряд)]), ряд))
        # Ошибки по I на внешний уровень: кодированный бит C1 восстанавливается Витерби.
        случай = np.random.default_rng(5)
        сбой = (случай.random(len(точки_)) < 0.02) & (np.abs(точки_.real) == 1)
        точки_[сбой] += np.sign(точки_[сбой].real) * 2
        ряд, _ = razbor.снять_вручную(в_биты(метки(точки_, "16qam"), 4), "trellis 16qam поворот 0 код dsng")
        данные = np.array(self.ДАННЫЕ[:len(ряд)]).reshape(-1, 24)
        self.assertTrue(np.array_equal(данные[:, :8], ряд.reshape(-1, 24)[:, :8]))


class RsIessTests(unittest.TestCase):
    def test_idr_e1_глубина_4_и_8(self):
        for глубина in (4, 8):
            with self.subTest(глубина=глубина):
                биты, данные = тк.iess_поток(64, 219, 201, глубина)
                сбой = np.random.default_rng(глубина).random(len(биты)) < 0.0005
                ряд, находка = razbor.снять_вручную((биты ^ сбой)[13:], f"рс iess idr e1 глубина {глубина}")
                байты = np.packbits(ряд).reshape(-1, 201)
                self.assertTrue(np.array_equal(данные[глубина:глубина + len(байты)], байты))
                self.assertTrue(any("исправлено" in п for п in находка.подробно))

    def test_ibs_по_числам(self):
        биты, данные = тк.iess_поток(40, 126, 112, 4, сид=2)
        ряд, _ = razbor.снять_вручную(биты, "рс iess 126 112")
        self.assertTrue(np.array_equal(данные.reshape(-1), np.packbits(ряд)))

    def test_не_тот_код(self):
        биты, _ = тк.iess_поток(40, 126, 112, 4, сид=2)
        with self.assertRaises(ValueError):
            iess.снять(биты, "рс iess 225 205")


if __name__ == "__main__":
    unittest.main()
