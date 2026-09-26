"""V.110: кадр 80 бит, виды RA1 по E1 E2 E3, RA2 из 64 кбит/с, данные пользователя.

Эталон — кодер из libosmocore (src/isdn/v110.c: osmo_v110_encode_frame и функции
v110_adapt_*_to_IR*), переписанный здесь построчно и независимо от модуля: те же номера
бит (октет · 8 + бит), та же таблица E1 E2 E3 (osmo_v110_e1e2e3) и те же места заполнения.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import kanal_sintez as кс
from reportgen.potok import hdlc, kanal, v110, разобрать

F = 1   # как в libosmocore: «I actually couldn't find any reference as to the value of F(ill) bits»

#: osmo_v110_e1e2e3: скорость → E1 E2 E3.
E1E2E3 = {600: (1, 0, 0), 1200: (0, 1, 0), 2400: (1, 1, 0), 4800: (0, 1, 1), 7200: (1, 0, 1),
          9600: (0, 1, 1), 12000: (0, 0, 1), 14400: (1, 0, 1), 19200: (0, 1, 1), 24000: (0, 0, 1),
          28800: (1, 0, 1), 38400: (0, 1, 1)}


def osmo_encode_frame(d_bits, e_bits, s_bits, x_bits):
    """osmo_v110_encode_frame."""
    ra = [0] * 80
    for i in range(1, 10):
        ra[i * 8] = 1
    ra[2 * 8 + 7], ra[7 * 8 + 7] = x_bits
    for к, о in zip((0, 2, 3, 5, 7, 8), (1, 3, 4, 6, 8, 9), strict=True):
        ra[о * 8 + 7] = s_bits[к]
    ra[5 * 8 + 1:5 * 8 + 8] = e_bits
    for к, о in enumerate((1, 2, 3, 4, 6, 7, 8, 9)):
        ra[о * 8 + 1:о * 8 + 7] = d_bits[к * 6:к * 6 + 6]
    return ra


def osmo_adapt(скорость, d_in):
    """v110_adapt_*_to_IR*: биты пользователя одного кадра → 48 бит D."""
    if скорость == 600:
        return [b for b in d_in for _ in range(8)]
    if скорость == 1200:
        return [b for b in d_in for _ in range(4)]
    if скорость == 2400:
        return [b for b in d_in for _ in range(2)]
    if скорость in (4800, 9600, 19200, 38400):
        return list(d_in)
    if скорость in (7200, 14400, 28800):
        d = d_in
        return (d[0:10] + [F] * 2 + d[10:12] + [F] * 2 + d[12:14] + [F] * 2 + d[14:28] + [F] * 2
                + d[28:30] + [F] * 2 + d[30:32] + [F] * 2 + d[32:36])
    d = d_in
    return (d[0:10] + [F] * 2 + d[10:12] + [F] * 2 + d[12:14] + [F] * 2 + [d[14]] + [F] * 3
            + d[15:25] + [F] * 2 + d[25:27] + [F] * 2 + d[27:29] + [F] * 2 + [d[29]] + [F] * 3)


НА_КАДР = {600: 6, 1200: 12, 2400: 24, 4800: 48, 7200: 36, 9600: 48, 12000: 30, 14400: 36,
           19200: 48, 24000: 30, 28800: 36, 38400: 48}


def osmo_поток(скорость, данные, s=0, x=0, e47=(0, 0, 0, 0)):
    n = НА_КАДР[скорость]
    итог = []
    for к in range(len(данные) // n):
        d = [int(b) for b in данные[к * n:(к + 1) * n]]
        итог += osmo_encode_frame(osmo_adapt(скорость, d), list(E1E2E3[скорость]) + list(e47),
                                  [s] * 9, [x, x])
    return np.array(итог, np.uint8)


def ra2(поток, ir):
    """RA2 (V.110 5.2): IR 8/16/32 кбит/с → 64 кбит/с, биты 1…w каждого октета, прочие — 1."""
    w = ir // 8
    октеты = np.ones((len(поток) // w, 8), np.uint8)
    октеты[:, :w] = поток[:len(поток) // w * w].reshape(-1, w)
    return октеты.reshape(-1)


def ошибки(биты, p, сид=1):
    return биты ^ (np.random.default_rng(сид).random(len(биты)) < p).astype(np.uint8)


class КадрTests(unittest.TestCase):
    def test_кодер_модуля_как_libosmocore(self):
        rng = np.random.default_rng(2)
        for скорость in (600, 1200, 2400, 4800, 7200, 12000):
            with self.subTest(скорость=скорость):
                данные = rng.integers(0, 2, НА_КАДР[скорость] * 20).astype(np.uint8)
                self.assertTrue(np.array_equal(osmo_поток(скорость, данные),
                                               v110.закодировать(данные, E1E2E3[скорость])))

    def test_все_виды_и_данные(self):
        rng = np.random.default_rng(3)
        for скорость, имя in ((600, "600 бит/с, бит × 8"), (1200, "1200 бит/с, бит × 4"),
                              (2400, "2400 бит/с, бит × 2"), (9600, "N × 4800: 48 бит данных"),
                              (14400, "N × 3600: 36 бит данных и заполнение"),
                              (24000, "N × 12000: 30 бит данных и заполнение")):
            with self.subTest(скорость=скорость):
                данные = rng.integers(0, 2, НА_КАДР[скорость] * 300).astype(np.uint8)
                поток = osmo_поток(скорость, данные)[37:]
                найдено = v110.найти(поток)
                self.assertEqual(f"V.110: адаптация скорости, {имя}", найдено.что)
                self.assertEqual(43, найдено.свойства["сдвиг"])
                # Первый целый кадр — второй кадр источника.
                n = НА_КАДР[скорость]
                дальше = найдено.дальше
                self.assertTrue(np.array_equal(данные[n:n + len(дальше)], дальше))
                текст = " ".join(найдено.подробно)
                self.assertIn(f"E1 E2 E3 = {''.join(map(str, E1E2E3[скорость]))} (V.110 табл. 5)", текст)

    def test_скорость_без_ir_неоднозначна(self):
        данные = np.random.default_rng(4).integers(0, 2, 48 * 100).astype(np.uint8)
        найдено = v110.найти(osmo_поток(4800, данные))
        self.assertIn("4800 бит/с при IR 8 кбит/с или 9600 бит/с при IR 16 кбит/с или "
                      "19200 бит/с при IR 32 кбит/с или 38400 бит/с при IR 64 кбит/с",
                      " ".join(найдено.подробно))

    def test_повторы_решаются_большинством(self):
        данные = np.random.default_rng(5).integers(0, 2, 6 * 400).astype(np.uint8)
        поток = osmo_поток(600, данные)
        # Портятся только биты D (синхрокомбинация цела): по одному из восьми повторов.
        испорчено = поток.copy().reshape(-1, 80)
        испорчено[::3, 9] ^= 1
        найдено = v110.найти(испорчено.reshape(-1))
        self.assertTrue(np.array_equal(данные, найдено.дальше))
        self.assertTrue(any("повторы бит согласны в 94.4 % групп" in п for п in найдено.подробно))

    def test_биты_s_x_e(self):
        данные = np.ones(48 * 100, np.uint8)
        найдено = v110.найти(osmo_поток(9600, данные, s=1, x=1, e47=(1, 0, 1, 1)))
        текст = " ".join(найдено.подробно)
        self.assertIn("E4–E7 = 1011; S-биты ON (0) в 0 %, X-биты ON в 0 % — соединение не установлено", текст)
        self.assertIn("данные — сплошь 1: пауза", текст)
        найдено = v110.найти(osmo_поток(9600, np.random.default_rng(6).integers(0, 2, 4800).astype(np.uint8),
                                        s=0, x=1))
        текст = " ".join(найдено.подробно)
        self.assertIn("S-биты ON (0) в 100 %, X-биты ON в 0 %", текст)
        self.assertNotIn("не установлено", текст)
        self.assertNotIn("пауза", текст)

    def test_ошибки_линии(self):
        данные = np.random.default_rng(7).integers(0, 2, 48 * 500).astype(np.uint8)
        найдено = v110.найти(ошибки(osmo_поток(4800, данные), 1e-3))
        self.assertIsNotNone(найдено)
        self.assertGreater(найдено.уверенность, 0.9)

    def test_ra2_из_64к(self):
        rng = np.random.default_rng(8)
        for ir, скорость in ((8, 4800), (16, 9600), (32, 19200)):
            with self.subTest(ir=ir):
                данные = rng.integers(0, 2, 48 * 200).astype(np.uint8)
                найдено = v110.найти(ra2(osmo_поток(скорость, данные), ir))
                self.assertEqual(f"V.110: адаптация скорости, {скорость} бит/с", найдено.что)
                self.assertEqual(ir, найдено.свойства["IR"])
                w = ir // 8
                место = "бит 1 каждого октета" if w == 1 else f"биты 1–{w} каждого октета"
                self.assertTrue(any(место in п and f"{ir} кбит/с" in п for п in найдено.подробно))

    def test_не_v110(self):
        rng = np.random.default_rng(9)
        self.assertIsNone(v110.найти(rng.integers(0, 2, 80 * 500).astype(np.uint8)))
        self.assertIsNone(v110.найти(np.ones(80 * 500, np.uint8)))
        self.assertIsNone(v110.найти(np.zeros(80 * 500, np.uint8)))
        # Мало кадров — не решаем.
        данные = rng.integers(0, 2, 48 * 20).astype(np.uint8)
        self.assertIsNone(v110.найти(osmo_поток(4800, данные)))

    def test_испорченная_синхрокомбинация(self):
        """Один бит синхрокомбинации испорчен в каждом третьем кадре — это не V.110 (67 % < 90 %)."""
        данные = np.random.default_rng(10).integers(0, 2, 48 * 300).astype(np.uint8)
        # Каждый из 17 бит: нули октета 0 (0–7) и единицы в начале октетов 1–9 (8, 16, …, 72).
        for бит in list(range(8)) + list(range(8, 80, 8)):
            with self.subTest(бит=бит):
                поток = osmo_поток(4800, данные).reshape(-1, 80)
                поток[::3, бит] ^= 1
                self.assertIsNone(v110.найти(поток.reshape(-1)))


class ВручнуюTests(unittest.TestCase):
    def test_слой_v110(self):
        from reportgen.potok.razbor import снять_вручную
        данные = np.random.default_rng(11).integers(0, 2, 36 * 200).astype(np.uint8)
        ряд, запись = снять_вручную(ra2(osmo_поток(14400, данные), 32), "v110")
        self.assertTrue(np.array_equal(данные[:len(ряд)], ряд))
        self.assertGreater(len(ряд), 36 * 190)
        self.assertTrue(any("V.110: адаптация скорости, 14400 бит/с" in п for п in запись.подробно))
        with self.assertRaisesRegex(ValueError, "V.110: синхрокомбинация"):
            снять_вручную(np.random.default_rng(12).integers(0, 2, 80 * 300).astype(np.uint8), "V.110")


class ИнструментTests(unittest.TestCase):
    def test_инструмент_стола(self):
        from reportgen.potok import rastr
        данные = np.random.default_rng(13).integers(0, 2, 48 * 200).astype(np.uint8)
        найдено = rastr.инструмент(ra2(osmo_поток(4800, данные), 8), "v110")
        self.assertEqual("V.110: адаптация скорости, 4800 бит/с", найдено.что)


class АвтоматTests(unittest.TestCase):
    def test_lapd_в_v110_через_ra2(self):
        """ISDN: HDLC LAPD на 9600 бит/с, V.110 при IR 16 кбит/с, RA2 в 64 кбит/с."""
        биты = np.unpackbits(np.frombuffer(кс.поток_hdlc(кс.lapd(300)), dtype=np.uint8))
        биты = биты[:len(биты) // 48 * 48]
        поток = ra2(osmo_поток(9600, биты), 16)
        найдено = v110.найти(поток)
        self.assertEqual("V.110: адаптация скорости, 9600 бит/с", найдено.что)
        self.assertEqual("ISDN, LAPD (Q.921)", kanal.найти(hdlc.найти(найдено.дальше).дальше).что)
        поток = поток[:len(поток) // 8 * 8]
        разбор = разобрать(данные=np.packbits(поток).tobytes(), профиль="быстро")
        что = " | ".join(н.что for н in разбор.находки)
        self.assertIn("V.110: адаптация скорости, 9600 бит/с", что)
        self.assertIn("HDLC", что)


if __name__ == "__main__":
    unittest.main()
