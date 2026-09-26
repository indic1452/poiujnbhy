"""TRAU (GSM Abis): кадры 320 бит, вид по C1–C5, подканалы 16 кбит/с в 64 кбит/с, данные → V.110.

Эталон — кодеры libosmo-abis (src/trau/trau_frame.c: encode_sync16, encode16_data; src/trau_frame.c:
encode_fr), переписанные здесь построчно и независимо от модуля.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import kanal_sintez as кс
from reportgen.potok import hdlc, kanal, trau, разобрать
from test_potok_v110 import osmo_поток

FT_FR_UP = (0, 0, 0, 1, 0)
FT_DATA_DOWN = (1, 0, 1, 1, 0)
FT_IDLE_DOWN = (0, 1, 1, 1, 0)


def encode_sync16(trau_bits):
    trau_bits[0:16] = 0
    for i in range(16, 40 * 8, 16):
        trau_bits[i] = 1


def encode_fr(cbits5, d_bits, c_rest=None):
    """encode_fr (старый API) с синхрокомбинацией encode_sync16: C1–C15, D1–D260, C16–C21, T1–T4."""
    t = np.zeros(320, np.uint8)
    encode_sync16(t)
    c = list(cbits5) + list(c_rest if c_rest is not None else [0] * 16)
    t[17:32] = c[0:15]
    d_idx = 0
    for i in range(32, 304, 16):
        t[i] = 1
        t[i + 1:i + 16] = d_bits[d_idx:d_idx + 15]
        d_idx += 15
    t[304] = 1
    t[305:310] = d_bits[d_idx:d_idx + 5]
    t[310:316] = c[15:21]
    t[316:320] = 0
    return t


def encode16_data(cbits5, d_bits):
    """encode16_data: C1–C5, C6–C15, четыре группы по 9 октетов «1 + 7 бит D»."""
    t = np.zeros(320, np.uint8)
    encode_sync16(t)
    t[17:22] = cbits5
    t[22:32] = 1
    d_idx = 0
    for начало in (4, 13, 22, 31):
        for i in range(9):
            offset = (начало + i) * 8
            t[offset] = 1
            t[offset + 1:offset + 8] = d_bits[d_idx:d_idx + 7]
            d_idx += 7
    return t


def данные_из_v110(поток):
    """Кадры V.110 (80 бит) → биты D кадров данных TRAU: без октета нулей и без первых бит октетов."""
    кадры = поток[:len(поток) // 80 * 80].reshape(-1, 80)[:, 8:].reshape(-1, 9, 8)[:, :, 1:]
    return кадры.reshape(-1)


def кадры_данных(поток_v110):
    d = данные_из_v110(поток_v110)
    return np.concatenate([encode16_data(FT_DATA_DOWN, d[i:i + 252]) for i in range(0, len(d) // 252 * 252, 252)])


def речь(кадров, сид=1):
    rng = np.random.default_rng(сид)
    return np.concatenate([encode_fr(FT_FR_UP, rng.integers(0, 2, 260).astype(np.uint8)) for _ in range(кадров)])


def в_64к(подканалы):
    """Подканалы 16 кбит/с (номер → биты) в канал 64 кбит/с: биты 2k−1, 2k каждого октета; прочие — 1."""
    длина = min(len(б) for б in подканалы.values()) // 2
    октеты = np.ones((длина, 8), np.uint8)
    for номер, б in подканалы.items():
        октеты[:, 2 * (номер - 1):2 * номер] = б[:2 * длина].reshape(-1, 2)
    return октеты.reshape(-1)


class TrauTests(unittest.TestCase):
    def test_речь_fr(self):
        найдено = trau.найти(речь(100)[123:])
        self.assertEqual("GSM Abis: кадры TRAU 16 кбит/с (речь FR)", найдено.что)
        текст = " ".join(найдено.подробно)
        self.assertIn("поток: кадры 320 бит с бита 197, синхрокомбинация 35 бит верна в 100.0 %", текст)
        self.assertIn("речь FR вверх × 99", текст)
        self.assertIsNone(найдено.дальше)

    def test_данные_в_v110(self):
        пользователь = np.random.default_rng(2).integers(0, 2, 48 * 400).astype(np.uint8)
        поток = кадры_данных(osmo_поток(9600, пользователь))
        найдено = trau.найти(поток)
        self.assertEqual("GSM Abis: кадры TRAU 16 кбит/с (данные)", найдено.что)
        текст = "\n".join(найдено.подробно)
        self.assertIn("данные вниз × 100", текст)
        self.assertIn("в кадрах данных — V.110: адаптация скорости, N × 4800: 48 бит данных", текст)
        self.assertTrue(np.array_equal(пользователь, найдено.дальше))

    def test_подканалы_64к(self):
        пользователь = np.random.default_rng(3).integers(0, 2, 48 * 400).astype(np.uint8)
        данные = кадры_данных(osmo_поток(9600, пользователь))
        поток = в_64к({1: речь(len(данные) // 320), 3: данные})
        найдено = trau.найти(поток)
        self.assertEqual([1, 3], найдено.свойства["подканалы"])
        self.assertIn("подканалов 2", найдено.что)
        текст = "\n".join(найдено.подробно)
        self.assertIn("подканал 1 (биты 1–2 октета)", текст)
        self.assertIn("подканал 3 (биты 5–6 октета)", текст)
        self.assertTrue(np.array_equal(пользователь[:len(найдено.дальше)], найдено.дальше))

    def test_виды(self):
        rng = np.random.default_rng(4)
        поток = np.concatenate([encode_fr(FT_IDLE_DOWN, rng.integers(0, 2, 260).astype(np.uint8)) for _ in range(40)]
                               + [encode_fr((1, 1, 0, 1, 0), rng.integers(0, 2, 260).astype(np.uint8)) for _ in range(20)])
        найдено = trau.найти(поток)
        self.assertIn("пустой кадр вниз × 40, речь EFR × 20", " ".join(найдено.подробно))
        self.assertEqual({0x0E: 40, 0x1A: 20}, найдено.свойства["виды"])

    def test_не_trau(self):
        rng = np.random.default_rng(5)
        self.assertIsNone(trau.найти(rng.integers(0, 2, 320 * 200).astype(np.uint8)))
        self.assertIsNone(trau.найти(np.zeros(320 * 200, np.uint8)))
        self.assertIsNone(trau.найти(речь(10)))                       # мало кадров
        # Каждый из 35 бит синхрокомбинации (16 нулей и единицы в начале слов), испорченный в 20 % кадров.
        for бит in list(range(16)) + list(range(16, 320, 16)):
            with self.subTest(бит=бит):
                испорчено = речь(200).reshape(-1, 320)
                испорчено[::5, бит] ^= 1
                self.assertIsNone(trau.найти(испорчено.reshape(-1)))

    def test_стол(self):
        from reportgen.potok import rastr
        from reportgen.potok.razbor import снять_вручную
        пользователь = np.random.default_rng(6).integers(0, 2, 48 * 200).astype(np.uint8)
        поток = в_64к({4: кадры_данных(osmo_поток(4800, пользователь))})
        self.assertEqual([4], rastr.инструмент(поток, "trau").свойства["подканалы"])
        ряд, запись = снять_вручную(поток, "trau")
        self.assertTrue(np.array_equal(пользователь[:len(ряд)], ряд))
        self.assertGreater(len(ряд), 48 * 190)
        with self.assertRaisesRegex(ValueError, "TRAU: кадров данных"):
            снять_вручную(в_64к({4: речь(100)}), "trau")

    def test_автомат_hdlc_в_v110_в_trau(self):
        """E1 КИ: TRAU 16 кбит/с → данные → V.110 → HDLC LAPD."""
        биты = np.unpackbits(np.frombuffer(кс.поток_hdlc(кс.lapd(300)), dtype=np.uint8))
        биты = биты[:len(биты) // 48 * 48]
        данные = кадры_данных(osmo_поток(9600, биты))
        найдено = trau.найти(данные)
        self.assertEqual("ISDN, LAPD (Q.921)", kanal.найти(hdlc.найти(найдено.дальше).дальше).что)
        поток = в_64к({2: данные})
        поток = поток[:len(поток) // 8 * 8]
        разбор = разобрать(данные=np.packbits(поток).tobytes(), профиль="быстро")
        что = " | ".join(н.что for н in разбор.находки)
        self.assertIn("GSM Abis: кадры TRAU 16 кбит/с (данные)", что)
        self.assertIn("HDLC", что)


if __name__ == "__main__":
    unittest.main()
