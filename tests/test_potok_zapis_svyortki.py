"""Свёрточные коды в записи стандартов: поток, закодированный как в libfec и GNU Radio.

libfec (fec.h): V27POLYB = 0x4F, V27POLYA = 0x6D, регистр (sr << 1) | бит — это
CCSDS/DVB-S 171/133 (старший бит записи стандартов — текущий вход). GNU Radio
(cc_encoder_impl.cc) — то же с многочленами 109 и 79. Выкалывание DVB-S 3/4 —
EN 300 421 табл. 2: X 101, Y 110. Анализатор должен называть такой поток
«171/133», находить материнский код выколотого и декодировать ручным слоем
«свёрточный 171/133 K=7».
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import kod, svyortka, vykalyvanie
from reportgen.potok.razbor import снять_вручную


def libfec(данные, многочлены=(0x4F, 0x6D), инвертировать=()):
    sr, выход = 0, []
    for бит in данные:
        sr = ((sr << 1) | int(бит)) & 0x7F
        выход += [(bin(sr & g).count("1") & 1) ^ (j in инвертировать) for j, g in enumerate(многочлены)]
    return np.array(выход, dtype=np.uint8)


class ЗаписьTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.данные = np.random.default_rng(11).integers(0, 2, 60_000).astype(np.uint8)

    def test_зеркало(self):
        self.assertEqual((0x4F, 0x6D), (svyortka.зеркало(0o171, 7), svyortka.зеркало(0o133, 7)))
        self.assertEqual(0o171, svyortka.зеркало(svyortka.зеркало(0o171, 7), 7))
        self.assertEqual((109, 79), (svyortka.зеркало(0o133, 7), svyortka.зеркало(0o171, 7)))  # GNU Radio
        self.assertEqual("171/133", svyortka.запись([0x4F, 0x6D], 7))
        self.assertEqual(0o13, svyortka.зеркало(0o15, 4))

    def test_nasa_по_libfec_назван_171_133(self):
        найдено = kod.найти(libfec(self.данные))
        self.assertIn("K=7, 171/133 (NASA, DVB-S, IEEE 802.11, Intelsat)", найдено.что)
        self.assertIn("g1 = 171, g2 = 133", найдено.подробно[0])
        self.assertTrue(np.array_equal(self.данные[:20_000], найдено.дальше[:20_000]))

    def test_ccsds_инверсия_второй_ветви(self):
        найдено = kod.найти(libfec(self.данные, инвертировать=(1,)))
        self.assertIn("как у CCSDS/NASA-GSFC", " ".join(найдено.подробно))

    def test_dvb_s_три_четверти(self):
        код = libfec(self.данные[:30_000])
        x, y = код[0::2].reshape(-1, 3), код[1::2].reshape(-1, 3)
        выколотый = np.column_stack([x[:, 0], y[:, 0], y[:, 1], x[:, 2]]).reshape(-1)
        найдено = vykalyvanie.найти(выколотый)
        self.assertIsNotNone(найдено)
        self.assertIn("скорости 3/4: материнский K=7, 171/133", найдено.что)
        self.assertIn("X 101 / Y 110", найдено.что)

    def test_ручной_слой_171_133(self):
        ряд, _ = снять_вручную(libfec(self.данные[:20_000]), "свёрточный 171/133 K=7")
        self.assertTrue(np.array_equal(self.данные[100:15_000], ряд[100:15_000]))


def gnuradio_кольцо(блок, многочлены, K=7):
    """cc_encoder_impl.cc, режим CC_TAILBITING, дословно: регистр заполнен последними K − 1 битами."""
    состояние = 0
    for i in range(K - 1):
        состояние = (состояние << 1) | int(блок[len(блок) - (K - 1) + i])
    выход = []
    for бит in блок:
        состояние = (состояние << 1) | int(бит)
        выход += [bin(состояние & g).count("1") & 1 for g in многочлены]
    return np.array(выход, dtype=np.uint8)


class КольцоTests(unittest.TestCase):
    #: LTE (36.212 5.1.3.1): G0 = 133, G1 = 171, G2 = 165 — в записи GNU Radio (зеркало) 109, 79, 87.
    LTE = (109, 79, 87)

    def test_кодер_как_gnuradio(self):
        блок = np.random.default_rng(1).integers(0, 2, 40).astype(np.uint8)
        внутр = [svyortka.зеркало(g, 7) for g in (0o133, 0o171, 0o165)]
        self.assertEqual(list(self.LTE), внутр)
        self.assertTrue(np.array_equal(gnuradio_кольцо(блок, self.LTE), svyortka.кодировать_кольцо(блок, внутр, 7)))

    def test_декодер_с_ошибками(self):
        rng = np.random.default_rng(2)
        данные = rng.integers(0, 2, (30, 40)).astype(np.uint8)
        принято = np.stack([gnuradio_кольцо(б, self.LTE) for б in данные])
        for б in принято:
            б[rng.choice(len(б), 3, replace=False)] ^= 1               # 3 ошибки на блок из 120 бит
        self.assertTrue(np.array_equal(данные, svyortka.витерби_кольцо(принято, list(self.LTE), 7)))

    def test_ручной_слой_кольцо(self):
        данные = np.random.default_rng(3).integers(0, 2, (20, 40)).astype(np.uint8)
        поток = np.concatenate([gnuradio_кольцо(б, self.LTE) for б in данные])
        ряд, запись = снять_вручную(поток, "свёрточный 133/171/165 K=7 кольцо 40")
        self.assertTrue(np.array_equal(данные.reshape(-1), ряд))
        self.assertIn("циклический хвост (tail-biting): 20 блоков по 40 бит; расхождение перекодированного "
                      "с принятым 0.00 %", " ".join(запись.подробно))
        with self.assertRaisesRegex(ValueError, "короче одного блока"):
            снять_вручную(поток[:100], "свёрточный 133/171/165 K=7 кольцо 40")



class СледКодаTests(unittest.TestCase):
    """Цикл, найденный по синхрослову сквозь свёрточный код (CCSDS: ASM за 171/133), — лишь одно
    из толкований: без проверенного содержимого под ним пробуется и снятие кода."""

    @staticmethod
    def _ветвь(*уровни):
        from reportgen.potok.nahodka import Находка
        from reportgen.potok.razbor import Ветвь
        в = Ветвь()
        for у in уровни:
            в.находки.append(Находка(уровень=у, что=у, уверенность=1.0, мера=""))
        return в

    def test_след(self):
        from reportgen.potok.razbor import _след_кода
        данные = np.random.default_rng(4).integers(0, 2, 60_000).astype(np.uint8)
        код = libfec(данные)
        self.assertTrue(_след_кода(self._ветвь("цикл", "скремблер", "код"), код))
        # Под циклом — кадры с проверкой: цикл настоящий.
        self.assertFalse(_след_кода(self._ветвь("цикл", "канальный"), код))
        # Первым найдено не цикл.
        self.assertFalse(_след_кода(self._ветвь("канальный"), код))
        # Поток — не свёрточный код.
        self.assertFalse(_след_кода(self._ветвь("цикл"), данные))


if __name__ == "__main__":
    unittest.main()
