"""Коды РС: систематический кодер и декодер ошибок со стираниями (синдромы Форни, Ψ = Λ·Γ)."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import rs_bch

# (поле, первый корень, проверочных, шаг корней): VDL2, DVB/CCSDS-подобные, шаг ≠ 1
КОДЫ = [(0x187, 120, 6, 1), (0x11D, 0, 16, 1), (0x11D, 1, 10, 1), (0x187, 112, 32, 11), (0x13, 1, 4, 1)]


def слово(г, p, fcr, корней, шаг):
    n = (1 << (p.bit_length() - 1)) - 1
    данные = г.integers(0, n + 1, n - корней).tolist()
    return rs_bch.закодировать_рс(данные, p, fcr, корней, шаг), данные


class КодерTests(unittest.TestCase):
    def test_систематический_и_синдромы_нулевые(self):
        г = np.random.default_rng(1)
        for p, fcr, корней, шаг in КОДЫ:
            with self.subTest(p=hex(p), fcr=fcr, шаг=шаг):
                c, данные = слово(г, p, fcr, корней, шаг)
                self.assertEqual(данные, c[:len(данные)])
                self.assertFalse(rs_bch.синдромы(np.array([c]), p, fcr, корней, шаг).any())

    def test_укороченный(self):
        """Укорочение — нули впереди: слово короче тоже кодовое."""
        c = rs_bch.закодировать_рс([5, 7, 9], 0x187, 120, 6)
        self.assertEqual(9, len(c))
        self.assertFalse(rs_bch.синдромы(np.array([c]), 0x187, 120, 6).any())
        полное = rs_bch.закодировать_рс([0] * 246 + [5, 7, 9], 0x187, 120, 6)
        self.assertEqual(c, полное[246:])

    def test_линейность(self):
        г = np.random.default_rng(2)
        a = г.integers(0, 256, 20).tolist()
        b = г.integers(0, 256, 20).tolist()
        ca = rs_bch.закодировать_рс(a, 0x11D, 0, 8)
        cb = rs_bch.закодировать_рс(b, 0x11D, 0, 8)
        self.assertEqual([x ^ y for x, y in zip(ca, cb, strict=False)],
                         rs_bch.закодировать_рс([x ^ y for x, y in zip(a, b, strict=False)], 0x11D, 0, 8))


class СтиранияTests(unittest.TestCase):
    def test_в_пределах_2e_плюс_f(self):
        г = np.random.default_rng(3)
        for p, fcr, корней, шаг in КОДЫ:
            n = (1 << (p.bit_length() - 1)) - 1
            for _ in range(40):
                c, _ = слово(г, p, fcr, корней, шаг)
                f = int(г.integers(0, корней + 1))
                e = int(г.integers(0, (корней - f) // 2 + 1))
                места = г.choice(n, f + e, replace=False).tolist()
                x = list(c)
                for м in места:
                    x[м] ^= int(г.integers(1, n + 1))
                with self.subTest(p=hex(p), f=f, e=e):
                    y, число = rs_bch.исправить_со_стираниями(x, места[:f], p, fcr, корней, шаг)
                    self.assertEqual(c, y)
                    ненулевых = sum(1 for м in места[:f] if c[м]) + e
                    self.assertEqual(ненулевых if (e or f) else 0, число)

    def test_стёртый_ноль_не_считается(self):
        c = rs_bch.закодировать_рс([0, 0, 0, 1, 2, 3], 0x187, 120, 6)
        x = list(c)
        x[5] ^= 77                                              # одна ошибка
        y, число = rs_bch.исправить_со_стираниями(x, [0, 1, 2], 0x187, 120, 6)    # стёрты нули
        self.assertEqual((c, 1), (y, число))

    def test_без_ошибок(self):
        c = rs_bch.закодировать_рс(list(range(10)), 0x187, 120, 6)
        self.assertEqual((c, 0), rs_bch.исправить_со_стираниями(c, [], 0x187, 120, 6))
        self.assertEqual((c, 0), rs_bch.исправить_со_стираниями(c, [3], 0x187, 120, 6)[:1] + (0,))

    def test_сверх_возможного(self):
        """2e + f > корней: неудача (−1) или другое кодовое слово — но не мусор. У полного кода с 6
        проверочными ложное «исправление» за пределом вероятно (около 1/t!), поэтому неудач — не все."""
        г = np.random.default_rng(4)
        неудач = 0
        for _ in range(60):
            c, _ = слово(г, 0x187, 120, 6, 1)
            места = г.choice(255, 5, replace=False).tolist()
            x = list(c)
            for м in места:
                x[м] ^= int(г.integers(1, 256))
            y, число = rs_bch.исправить_со_стираниями(x, места[:2], 0x187, 120, 6)
            if число < 0:
                неудач += 1
            else:
                self.assertFalse(rs_bch.синдромы(np.array([y]), 0x187, 120, 6).any())
                self.assertNotEqual(c, y)
        self.assertGreater(неудач, 10)

    def test_предел_2e_плюс_f(self):
        """GF(16), 4 проверочных, 3 стирания: ошибок исправимо 0 — многочлен ошибок степени 1 отвергается,
        хотя дал бы кодовое слово (найдено перебором)."""
        x = [9, 11, 13, 6, 2, 2, 6, 2, 15, 14, 1, 1, 8, 11, 1]
        self.assertEqual(-1, rs_bch.исправить_со_стираниями(x, [5, 7, 3], 0x13, 1, 4)[1])

    def test_стираний_больше_проверочных(self):
        c = rs_bch.закодировать_рс([1, 2, 3], 0x187, 120, 6)
        self.assertEqual(-1, rs_bch.исправить_со_стираниями(c, list(range(7)), 0x187, 120, 6)[1])

    def test_как_декодер_ошибок(self):
        """Без стираний — то же, что ``исправить``."""
        г = np.random.default_rng(5)
        слова = []
        for _ in range(20):
            c, _ = слово(г, 0x11D, 0, 16, 1)
            x = list(c)
            for м in г.choice(255, int(г.integers(0, 9)), replace=False).tolist():
                x[м] ^= int(г.integers(1, 256))
            слова.append(x)
        исправленные, числа = rs_bch.исправить(np.array(слова), 0x11D, 0, 16)
        for x, ожидаемое, число in zip(слова, исправленные.tolist(), числа.tolist(), strict=False):
            y, k = rs_bch.исправить_со_стираниями(x, [], 0x11D, 0, 16)
            self.assertEqual((ожидаемое, max(число, 0)), (y, max(k, 0)))


if __name__ == "__main__":
    unittest.main()
