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


def _выколоть(u: np.ndarray, x: str, y: str, mil: bool) -> np.ndarray:
    """Свой кодер НСК: 171/133 K = 7 (EN 300 421 рис. 3) регистром-списком, шаблон табл. 2;
    MIL-STD-188-165B — во 2-м и следующих символах периода I и Q переставлены (табл. I–II)."""
    рег = [0] * 6
    переданные = []
    for t, e in enumerate(u.tolist()):
        X = e ^ рег[0] ^ рег[1] ^ рег[2] ^ рег[5]
        Y = e ^ рег[1] ^ рег[2] ^ рег[4] ^ рег[5]
        рег = [e] + рег[:5]
        if x[t % len(x)] == "1":
            переданные.append(X)
        if y[t % len(y)] == "1":
            переданные.append(Y)
    п = np.array(переданные, dtype=np.uint8)
    P = (x + y).count("1")
    п = п[:len(п) // P * P].reshape(-1, P)
    if mil:
        п[:, 2::2], п[:, 3::2] = п[:, 3::2].copy(), п[:, 2::2].copy()
    return п.reshape(-1)


class НскTests(unittest.TestCase):
    U = np.random.default_rng(8).integers(0, 2, 30_000).astype(np.uint8)

    def test_dvb_и_mil(self):
        for скорость, x, y, mil in (("3/4", "101", "110", False), ("7/8", "1000101", "1111010", False),
                                    ("2/3", "10", "11", False), ("5/6", "10101", "11010", False),
                                    ("3/4", "101", "110", True), ("7/8", "1000101", "1111010", True)):
            with self.subTest(скорость=скорость, mil=mil):
                п = _выколоть(self.U, x, y, mil)
                п ^= (np.random.default_rng(1).random(len(п)) < 0.002).astype(np.uint8)
                ряд, находка = razbor.снять_вручную(п, f"нск {скорость}" + (" mil" if mil else ""))
                self.assertTrue(np.array_equal(self.U[:5000], ряд[:5000]), находка.подробно)

    def test_сск_и_кбк_честно(self):
        with self.assertRaisesRegex(ValueError, "многочлены кода есть только в IESS-309"):
            razbor.снять_вручную(self.U, "сск")
        with self.assertRaisesRegex(ValueError, "расшифровки нет"):
            razbor.снять_вручную(self.U, "кбк")


if __name__ == "__main__":
    unittest.main()
