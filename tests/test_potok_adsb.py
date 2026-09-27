"""ADS-B / Mode S: CRC-24, позывной, высота, скорость, глобальный CPR.

Образцы — известные сообщения из «The 1090 MHz Riddle» (Junzi Sun), они же в документации pyModeS:
KLM1023; пара положений 40621D (52.2572, 3.91937, 38000 футов); скорость 485020 (159 уз, 182.88°,
−832 фут/мин). Таблица NL — пороги cprNLFunction dump1090.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import adsb as a
from reportgen.potok.razbor import разобрать as разобрать_поток

ОПОЗНАВАНИЕ = "8D4840D6202CC371C32CE0576098"
ЧЁТНОЕ = "8D40621D58C382D690C8AC2863A7"
НЕЧЁТНОЕ = "8D40621D58C386435CC412692AD6"
СКОРОСТЬ = "8D485020994409940838175B284F"


class СообщенияTests(unittest.TestCase):
    def test_crc(self):
        for h in (ОПОЗНАВАНИЕ, ЧЁТНОЕ, НЕЧЁТНОЕ, СКОРОСТЬ):
            self.assertEqual(0, a.crc(a.из_hex(h)), h)
        испорчено = a.из_hex(ОПОЗНАВАНИЕ)
        испорчено[40] ^= 1
        self.assertNotEqual(0, a.crc(испорчено))
        # Кодер восстанавливает паритет.
        self.assertEqual(a.из_hex(ОПОЗНАВАНИЕ).tolist(), a.закодировать(a.из_hex(ОПОЗНАВАНИЕ)[:88]).tolist())

    def test_опознавание(self):
        п = a.разобрать_сообщение(a.из_hex(ОПОЗНАВАНИЕ))
        self.assertEqual((17, 0x4840D6, 4, "KLM1023", "A0"), (п["df"], п["icao"], п["tc"], п["позывной"], п["категория"]))

    def test_положение_и_высота(self):
        ч, н = a.разобрать_сообщение(a.из_hex(ЧЁТНОЕ)), a.разобрать_сообщение(a.из_hex(НЕЧЁТНОЕ))
        self.assertEqual((38000, 38000), (ч["высота"], н["высота"]))
        self.assertEqual(((0, 93000, 51372), (1, 74158, 50194)), (ч["cpr"], н["cpr"]))
        широта, долгота = a.cpr_глобально(ч["cpr"][1:], н["cpr"][1:], False)
        self.assertAlmostEqual(52.2572, широта, places=4)
        self.assertAlmostEqual(3.91937, долгота, places=5)
        широта, долгота = a.cpr_глобально(ч["cpr"][1:], н["cpr"][1:], True)     # по нечётному — рядом
        self.assertAlmostEqual(52.2658, широта, places=4)
        self.assertAlmostEqual(3.93891, долгота, places=4)

    def test_скорость(self):
        п = a.разобрать_сообщение(a.из_hex(СКОРОСТЬ))
        self.assertEqual((19, 1, -832), (п["tc"], п["подтип"], п["вертикальная"]))
        self.assertAlmostEqual(159.20, п["путевая_скорость"], places=2)
        self.assertAlmostEqual(182.88, п["путевой_угол"], places=2)

    def test_сверхзвуковая_скорость(self):
        """Подтип 2: скорости в 4 раза (узлы · 4)."""
        биты = a.из_hex(СКОРОСТЬ)[:88].copy()
        биты[32 + 5:32 + 8] = [0, 1, 0]
        п = a.разобрать_сообщение(a.закодировать(биты))
        self.assertEqual(2, п["подтип"])
        self.assertAlmostEqual(4 * 159.20, п["путевая_скорость"], places=1)

    def test_nl_как_таблица_dump1090(self):
        for порог, выше in ((10.47047130, 58), (14.82817437, 57), (29.91135686, 51), (59.95459277, 29),
                            (80.24923213, 9), (86.53536998, 2), (87.0, 1)):
            with self.subTest(порог=порог):
                self.assertEqual(выше + 1, a.nl(порог - 1e-6))
                self.assertEqual(выше, a.nl(порог + 1e-6))
                self.assertEqual(выше, a.nl(-порог - 1e-6))
        self.assertEqual(59, a.nl(0.0))
        # Ровно 87° — 2 по ICAO (формула там не определена: arccos вне области).
        self.assertEqual((2, 2), (a.nl(87.0), a.nl(-87.0)))

    def test_cpr_туда_и_обратно(self):
        """Кодер ICAO и глобальное декодирование: все полушария; по нечётному и по чётному."""
        for широта, долгота in ((52.2572, 3.91937), (-33.9425, 151.1750), (40.6413, -73.7781),
                                (-54.8433, -68.2958), (0.5, -179.9), (86.5, 12.3)):
            for последнее_нечётное in (False, True):
                with self.subTest(широта=широта, долгота=долгота, нечётное=последнее_нечётное):
                    ч, н = a.cpr_закодировать(широта, долгота, False), a.cpr_закодировать(широта, долгота, True)
                    ш, д = a.cpr_глобально(ч, н, последнее_нечётное)
                    self.assertAlmostEqual(широта, ш, delta=0.001)
                    self.assertAlmostEqual(долгота, д, delta=0.002)

    def test_cpr_разные_зоны(self):
        """Чётное и нечётное по разные стороны границы зон NL (10.4704713°) — не складываются."""
        ч = a.cpr_закодировать(10.46, 20.0, False)
        н = a.cpr_закодировать(10.48, 20.0, True)
        self.assertIsNone(a.cpr_глобально(ч, н, True))
        # По одну сторону границы — складываются.
        self.assertIsNotNone(a.cpr_глобально(a.cpr_закодировать(10.44, 20.0, False),
                                             a.cpr_закодировать(10.46, 20.0, True), True))


class ПотокTests(unittest.TestCase):
    def поток(self, образцы, сид=1, между=300):
        г = np.random.default_rng(сид)
        части = []
        for h in образцы:
            части.append(г.integers(0, 2, int(г.integers(между // 2, между))).astype(np.uint8))
            части.append(a.из_hex(h))
        части.append(г.integers(0, 2, 500).astype(np.uint8))
        return np.concatenate(части)

    def test_борта(self):
        н = a.найти(self.поток([ОПОЗНАВАНИЕ, НЕЧЁТНОЕ, СКОРОСТЬ, ЧЁТНОЕ, ОПОЗНАВАНИЕ]))
        self.assertEqual("ADS-B (Mode S, 1090 МГц): расширенные сквиттеры", н.что)
        текст = "\n".join(н.подробно)
        self.assertIn("ICAO 4840D6; позывной KLM1023; сообщений 2", текст)
        self.assertIn("ICAO 40621D; высота 38000 футов; координаты 52.25720, 3.91937; сообщений 2", текст)
        self.assertIn("ICAO 485020; 159 уз, путевой угол 182.9°, вертикальная -832 фут/мин; сообщений 1", текст)
        self.assertEqual(3, н.свойства["бортов"])

    def test_мало_и_шум(self):
        self.assertIsNone(a.найти(self.поток([ОПОЗНАВАНИЕ, СКОРОСТЬ])))
        self.assertIsNone(a.найти(np.random.default_rng(7).integers(0, 2, 300_000).astype(np.uint8)))
        испорченные = self.поток([ОПОЗНАВАНИЕ] * 5)
        for м, _ in a.сообщения(испорченные):
            испорченные[м + 50] ^= 1
        self.assertIsNone(a.найти(испорченные))

    def test_перекрытие_окон(self):
        """Второе окно с верной CRC внутри первого сообщения не считается отдельным."""
        б = a.из_hex(ОПОЗНАВАНИЕ)
        # Первое сообщение: DF17 + 51 свободный бит + первые 56 бит второго; свободные биты
        # подбираются так, чтобы остаток CRC-24 был нулём (система над GF(2)).
        столбцы = a.СТОЛБЦЫ[112]
        цель = (np.concatenate([[1, 0, 0, 0, 1], np.zeros(51, np.uint8), б[:56]]) @ столбцы) % 2
        система = np.concatenate([столбцы[5:56].T, цель[:, None]], axis=1).astype(np.uint8)
        строка, опоры = 0, []
        for столбец in range(51):
            опора = next((r for r in range(строка, 24) if система[r, столбец]), None)
            if опора is None:
                continue
            система[[строка, опора]] = система[[опора, строка]]
            for r in range(24):
                if r != строка and система[r, столбец]:
                    система[r] ^= система[строка]
            опоры.append(столбец)
            строка += 1
        свободные = np.zeros(51, np.uint8)
        for r, столбец in enumerate(опоры):
            свободные[столбец] = система[r, -1]
        первое = np.concatenate([[1, 0, 0, 0, 1], свободные, б[:56]]).astype(np.uint8)
        self.assertEqual(0, a.crc(первое))
        поток = np.concatenate([первое, б[56:]])
        self.assertEqual([0], [м for м, _ in a.сообщения(поток)])

    def test_df18_и_другие_df(self):
        """DF18 (TIS-B/ADS-R) — тоже; DF11 и DF20 с нулевой CRC не считаются (другой формат)."""
        df18 = a.из_hex(ОПОЗНАВАНИЕ)[:88].copy()
        df18[:5] = [1, 0, 0, 1, 0]
        df20 = df18.copy()
        df20[:5] = [1, 0, 1, 0, 0]
        н = a.найти(np.concatenate([a.закодировать(df18)] * 3 + [a.закодировать(df20)] * 3))
        self.assertIn("DF18 × 3", н.подробно[0])
        self.assertEqual(3, н.свойства["сообщений"])

    def test_автомат(self):
        биты = self.поток([ОПОЗНАВАНИЕ, ЧЁТНОЕ, НЕЧЁТНОЕ, СКОРОСТЬ] * 3, между=2000)
        биты = биты[:len(биты) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(биты).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("ADS-B") for н in р.находки), [н.что for н in р.находки])


if __name__ == "__main__":
    unittest.main()
