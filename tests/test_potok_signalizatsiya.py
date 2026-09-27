"""Сигнализация E1: ABCD в КИ16, линейная R2, тоны MFC R2 / R1 / DTMF в каналах A-закона.

Тоны синтезируются здесь и кодируются в A-закон эталоном audioop.lin2alaw (G.711); частоты и
коды пар — по openr2 (r2engine.c) и spandsp (bell_r2_mf.c, dtmf.c), см. модуль.
"""

import audioop
import random
import unittest
import warnings

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import cikl, signalizatsiya as сг
from reportgen.potok.bity import в_биты

warnings.filterwarnings("ignore", category=DeprecationWarning)

MFC_В = {"1": (1380, 1500), "2": (1380, 1620), "3": (1500, 1620), "4": (1380, 1740), "5": (1500, 1740),
         "6": (1620, 1740), "7": (1380, 1860), "8": (1500, 1860), "9": (1620, 1860), "0": (1740, 1860),
         "B": (1380, 1980), "C": (1500, 1980), "D": (1620, 1980), "E": (1740, 1980), "F": (1860, 1980)}
MFC_Н = {"1": (1140, 1020), "2": (1140, 900), "3": (1020, 900), "4": (1140, 780), "5": (1020, 780),
         "6": (900, 780)}
R1_П = {"1": (700, 900), "2": (700, 1100), "3": (900, 1100), "4": (700, 1300), "5": (900, 1300),
        "6": (1100, 1300), "0": (1300, 1500), "*": (1100, 1700), "#": (1500, 1700)}
DTMF = {"1": (697, 1209), "5": (770, 1336), "9": (852, 1477), "0": (941, 1336), "#": (941, 1477), "D": (941, 1633)}


def набор(цифры, таблица, тон_мс=70, пауза_мс=60, амплитуда=6000, шум=0):
    г = np.random.default_rng(1)
    части = [np.zeros(400)]
    for ц in цифры:
        f1, f2 = таблица[ц]
        t = np.arange(int(8 * тон_мс)) / 8000
        части.append(амплитуда * (np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t)))
        части.append(np.zeros(int(8 * пауза_мс)))
    x = np.concatenate(части) + (г.normal(0, шум, sum(len(ч) for ч in части)) if шум else 0)
    return np.frombuffer(audioop.lin2alaw(np.clip(x, -32000, 32000).astype(np.int16).tobytes(), 2), np.uint8)


class ALawTests(unittest.TestCase):
    def test_как_g711(self):
        эталон = np.frombuffer(audioop.alaw2lin(bytes(range(256)), 2), np.int16).astype(np.int32)
        self.assertTrue(np.array_equal(эталон, сг.ALAW))


class ТоныTests(unittest.TestCase):
    def test_mfc_вперёд(self):
        вид, найдено = сг.тоны(набор("4729B0", MFC_В))
        self.assertEqual("MFC R2 вперёд", вид)
        self.assertEqual("4729B0", "".join(ц for _, ц in найдено))

    def test_mfc_все_пятнадцать(self):
        вид, найдено = сг.тоны(набор("1234567890BCDEF", MFC_В))
        self.assertEqual("1234567890BCDEF", "".join(ц for _, ц in найдено))

    def test_перекос_уровней_не_тон(self):
        """Вторая частота слабее первой в 10 раз по амплитуде (в 100 по энергии) — перекос больше 5: отказ."""
        t = np.arange(8000) / 8000
        x = 8000 * np.sin(2 * np.pi * 1380 * t) + 800 * np.sin(2 * np.pi * 1500 * t)
        self.assertIsNone(сг.тоны(np.frombuffer(audioop.lin2alaw(x.astype(np.int16).tobytes(), 2), np.uint8)))

    def test_mfc_назад(self):
        вид, найдено = сг.тоны(набор("16354", MFC_Н))
        self.assertEqual(("MFC R2 назад", "16354"), (вид, "".join(ц for _, ц in найдено)))

    def test_r1(self):
        вид, найдено = сг.тоны(набор("*12340#", R1_П))
        self.assertEqual(("R1 / R1.5 (700–1700 Гц)", "*12340#"), (вид, "".join(ц for _, ц in найдено)))

    def test_dtmf(self):
        вид, найдено = сг.тоны(набор("1590#D", DTMF, амплитуда=4000))
        self.assertEqual(("DTMF", "1590#D"), (вид, "".join(ц for _, ц in найдено)))

    def test_повтор_цифры_через_паузу(self):
        вид, найдено = сг.тоны(набор("5555", MFC_В))
        self.assertEqual("5555", "".join(ц for _, ц in найдено))

    def test_время(self):
        _, найдено = сг.тоны(набор("12", MFC_В, тон_мс=70, пауза_мс=100))
        self.assertAlmostEqual(0.05, найдено[0][0], delta=0.02)
        self.assertAlmostEqual(0.22, найдено[1][0], delta=0.03)

    def test_с_шумом(self):
        вид, найдено = сг.тоны(набор("4729", MFC_В, шум=300))
        self.assertEqual("4729", "".join(ц for _, ц in найдено))

    def test_не_тон(self):
        г = np.random.default_rng(2)
        self.assertIsNone(сг.тоны(г.integers(0, 256, 16000).astype(np.uint8)))
        self.assertIsNone(сг.тоны(np.full(16000, 0xD5, np.uint8)))
        t = np.arange(16000) / 8000
        один = np.frombuffer(audioop.lin2alaw((6000 * np.sin(2 * np.pi * 1380 * t)).astype(np.int16).tobytes(), 2),
                             np.uint8)
        self.assertIsNone(сг.тоны(один))

    def test_короткий_тон_не_цифра(self):
        """Тон короче двух блоков (≈ 20 мс) не засчитывается."""
        self.assertIsNone(сг.тоны(набор("4", MFC_В, тон_мс=15)))

    def test_третья_частота_рядом_не_тон(self):
        """Три частоты сразу (не «2 из 6»): третья сильнее второй / 12,6 — отказ."""
        t = np.arange(8000) / 8000
        x = 6000 * (np.sin(2 * np.pi * 1380 * t) + np.sin(2 * np.pi * 1500 * t) + np.sin(2 * np.pi * 1620 * t))
        self.assertIsNone(сг.тоны(np.frombuffer(audioop.lin2alaw(x.astype(np.int16).tobytes(), 2), np.uint8)))


def ки16_cas(сверхциклов, значение):
    """Байты КИ16: цикл 0 — MFAS 0000 + 1011 (x y x x), циклы 1–15 — ABCD каналов k и k+15."""
    итог = []
    for n in range(сверхциклов):
        итог.append(0x0B)
        for k in range(1, 16):
            итог.append((значение(n, k) << 4) | значение(n, k + 15))
    return np.array(итог, np.uint8)


class AbcdTests(unittest.TestCase):
    def test_каналы_и_смены(self):
        """КИ5 (канал 5): 1001 → 0001 (занятие вперёд); КИ21 (канал 20): 1001 → 1101 → 0101 (ответ назад)."""
        def знач(n, канал):
            if канал == 5:
                return 0b1001 if n < 10 else 0b0001
            if канал == 20:
                return 0b1001 if n < 10 else (0b1101 if n < 20 else 0b0101)
            return 0b1001
        значения, номера = сг.abcd(np.concatenate([np.array([0x99, 0x99, 0x99], np.uint8), ки16_cas(40, знач)]))
        self.assertEqual(40, len(значения))
        self.assertEqual(5, номера[4])
        self.assertEqual(21, номера[19])
        строки = сг.линия(значения, номера)
        self.assertIn("КИ5: AB 10 (исходное / отбой вперёд) → 00 (занятие) — вперёд", строки)
        self.assertIn("КИ21: AB 10 (исходное) → 11 (подтверждение занятия / отбой назад / блокировка) → 01 (ответ) — назад",
                      строки)
        self.assertEqual(2, len(строки))

    def test_смешанные_состояния(self):
        def знач(n, канал):
            return (0b1001, 0b0001, 0b1101)[n // 5 % 3] if канал == 1 else 0b1001
        строки = сг.линия(*сг.abcd(ки16_cas(30, знач)))
        self.assertTrue(строки[0].startswith("КИ1: AB 10 → 00 → 11"))
        self.assertTrue(строки[0].endswith("— направление неясно"))

    def test_mfas_с_битами_x_y(self):
        """MFAS — 0000 в старшей тетраде; младшая (x, y — авария, x) бывает любой."""
        данные = ки16_cas(20, lambda n, k: 0b1001)
        данные[::16] = [0x0F, 0x09, 0x0D, 0x0B] * 5
        значения, _ = сг.abcd(данные)
        self.assertEqual(20, len(значения))

    def test_без_сверхцикла(self):
        self.assertIsNone(сг.abcd(np.random.default_rng(3).integers(16, 256, 640).astype(np.uint8)))
        # MFAS через 8 циклов, а не через 16 — не CAS.
        байты = np.full(640, 0x99, np.uint8)
        байты[::8] = 0x0B
        self.assertIsNone(сг.abcd(байты))


class E1Tests(unittest.TestCase):
    def test_e1_с_cas_и_mfc(self):
        """E1: КИ16 — CAS со сменой КИ5, КИ5 — набор MFC R2 вперёд; всё видно в отчёте цикла."""
        циклов = 16 * 600
        ки16 = ки16_cas(600, lambda n, k: 0b0001 if (k == 5 and n >= 100) else 0b1001)
        голос = набор("8123456", MFC_В)
        г = random.Random(4)

        def байт(цикл, ки):
            if ки == 16:
                return int(ки16[цикл])
            if ки == 5:
                return int(голос[цикл]) if цикл < len(голос) else 0xD5
            return г.randrange(256)
        найдено = cikl.e1(в_биты(с.e1(циклов, каналы=байт)))
        текст = "\n".join(найдено.подробно)
        self.assertIn("КИ5: AB 10 (исходное / отбой вперёд) → 00 (занятие) — вперёд", текст)
        self.assertIn("КИ5: MFC R2 вперёд — 8 1 2 3 4 5 6", текст)
        self.assertIn("CAS: ABCD по 600 сверхциклам", текст)


if __name__ == "__main__":
    unittest.main()
