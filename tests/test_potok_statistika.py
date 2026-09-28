"""Статистические тесты NIST SP 800-22 и характеристики ENT — сверка с эталонными реализациями.

Эталоны получены программно: исходники NIST Statistical Test Suite (sts/src, открытый
репозиторий NIST STS, формат вывода p-значений изменён на %.15g) собраны с обвязкой,
которая подаёт ряд и вызывает тест; ENT (Fourmilab, github.com/Fourmilab/
ent_random_sequence_tester) — как есть. Ряды детерминированы: SHA-256 в режиме счётчика,
тот же с перекосом единиц, «каждый седьмой бит — единица» и пример из NIST SP 800-22
(первые 100 бит двоичного разложения e, раздел 2 документа).
Неприменимые случаи (эталон пишет 0 или nan: мало циклов для экскурсий, нет ни одной
матрицы 32×32 и т. п.) у нас — None: это не p-значение, а «тест не применим».
"""

import hashlib
import math
import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import statistika_bit as sb


def sha(n, порог=None):
    if порог is None:
        байты = b"".join(hashlib.sha256(i.to_bytes(8, "big")).digest() for i in range((n + 255) // 256))
        return np.unpackbits(np.frombuffer(байты, np.uint8))[:n]
    байты = b"".join(hashlib.sha256(i.to_bytes(8, "big")).digest() for i in range((n + 31) // 32))
    return (np.frombuffer(байты, np.uint8) < порог).astype(np.uint8)[:n]


def структурный(n):
    b = sha(n).copy()
    b[::7] = 1
    return b


РЯДЫ = {"sha": lambda: sha(1000000), "перекос": lambda: sha(1000000, 131), "структура": lambda: структурный(200000),
        "e100": lambda: np.array([int(c) for c in "1100100100001111110110101010001000100001011010001100001000110"
                                                   "100110001001100011001100010100010111000"], np.uint8)}

ФУНКЦИИ = {"frequency": lambda b, p: sb.частотный(b), "block": lambda b, p: sb.частотный_в_блоках(b, p),
           "cusum": lambda b, p: sb.кумулятивные_суммы(b), "runs": lambda b, p: sb.серии(b),
           "longest": lambda b, p: sb.длиннейшая_серия(b), "rank": lambda b, p: sb.ранг_матриц(b),
           "dft": lambda b, p: sb.спектральный(b), "overlapping": lambda b, p: sb.перекрывающиеся_шаблоны(b, p),
           "universal": lambda b, p: sb.универсальный(b), "apen": lambda b, p: sb.приближённая_энтропия(b, p),
           "serial": lambda b, p: sb.серийный(b, p), "linear": lambda b, p: sb.линейная_сложность(b, p),
           "excursions": lambda b, p: sb.случайные_экскурсии(b),
           "excursionsvar": lambda b, p: sb.случайные_экскурсии_вариант(b)}

ЭТАЛОН_STS = {
    "sha:frequency:None": [0.519575433993358],
    "sha:block:128": [0.86662450404365],
    "sha:cusum:None": [0.81028285581574, 0.828035107134009],
    "sha:runs:None": [0.436324363989087],
    "sha:longest:None": [0.661892815305412],
    "sha:rank:None": [0.531234125758483],
    "sha:dft:None": [0.007171959358849],
    "sha:overlapping:9": [0.540536986509201],
    "sha:universal:None": [0.657983067268067],
    "sha:apen:10": [0.411396653087013],
    "sha:serial:16": [0.55760833996959, 0.313112480018299],
    "sha:linear:500": [0.840885831474302],
    "sha:excursions:None": [0.671905893799491, 0.74961075580457, 0.536649113277676, 0.448318464239559, 0.0499479038978739, 0.933527336337408, 0.772389295284942, 0.958390497163853],
    "sha:excursionsvar:None": [0.812407148402401, 0.95878520974467, 0.901823518697056, 0.658095184774304, 0.614207105258253, 0.846704371236291, 0.834561869618082, 0.797340577269206, 0.124913969991103, 0.0530188912432155, 0.247862477188092, 0.403482528608834, 0.395909478861177, 0.309835451838048, 0.235297019760898, 0.339059384814814, 0.411585239393689, 0.658285485289038],
    "sha:block:10": [0.627798923747231],
    "sha:apen:2": [0.518916762599357],
    "sha:serial:3": [0.51854075867814, 0.32909790134867],
    "перекос:frequency:None": [1.19749576869942e-122],
    "перекос:block:128": [8.33916470797639e-06],
    "перекос:cusum:None": [0.0, 0.0],
    "перекос:runs:None": [0.0],
    "перекос:longest:None": [0.383581301890959],
    "перекос:rank:None": [0.971163262325761],
    "перекос:dft:None": [0.600927021417659],
    "перекос:overlapping:9": [2.02914749536691e-05],
    "перекос:universal:None": [0.368110285567094],
    "перекос:apen:10": [1.25155915125426e-26],
    "перекос:serial:16": [0.00178277674636029, 0.147208468530799],
    "перекос:linear:500": [0.44507200692003],
    "перекос:block:10": [0.27299050414325],
    "перекос:apen:2": [8.19249947219948e-120],
    "перекос:serial:3": [1.79686181311792e-119, 0.214875604930952],
    "структура:frequency:None": [0.0],
    "структура:block:128": [0.0],
    "структура:cusum:None": [0.0, 0.0],
    "структура:runs:None": [0.0],
    "структура:longest:None": [4.27289036982504e-83],
    "структура:rank:None": [0.0997909705189518],
    "структура:dft:None": [0.0],
    "структура:overlapping:9": [8.1324048653212e-45],
    "структура:apen:10": [0.0],
    "структура:serial:16": [0.0, 0.592023164144425],
    "структура:linear:500": [0.821616605290504],
    "структура:block:10": [3.05777199078463e-12],
    "структура:apen:2": [0.0],
    "структура:serial:3": [0.0, 0.940371752996395],
    "e100:frequency:None": [0.109598583399116],
    "e100:block:128": [1.0],
    "e100:cusum:None": [0.219193993485627, 0.114866215302521],
    "e100:runs:None": [0.50079791788709],
    "e100:dft:None": [0.64635519553949],
    "e100:apen:10": [1.0],
    "e100:serial:16": [0.498961087458597, 0.498530755295295],
    "e100:block:10": [0.706438449641281],
    "e100:apen:2": [0.235300745858987],
    "e100:serial:3": [0.308441041184003, 0.35345468195878],
}
НЕПРИМЕНИМЫ = ['e100:excursions:None', 'e100:excursionsvar:None', 'e100:linear:500', 'e100:longest:None', 'e100:overlapping:9', 'e100:rank:None', 'e100:universal:None', 'перекос:excursions:None', 'перекос:excursionsvar:None', 'структура:excursions:None', 'структура:excursionsvar:None', 'структура:universal:None']


#: pochisq(232,18176, 255) из chisq.c ENT — p для χ² этих 100 000 байт.
ЭНТ_ХИ_P = 0.844343957761679


class NistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ряды = {имя: f() for имя, f in РЯДЫ.items()}

    def выполнить(self, ключ):
        имя, тест, параметр = ключ.split(":")
        значение = ФУНКЦИИ[тест](self.ряды[имя], None if параметр == "None" else int(параметр))
        return None if значение is None else list(значение) if isinstance(значение, (tuple, list)) else [значение]

    def test_как_nist_sts(self):
        for ключ, ждём in ЭТАЛОН_STS.items():
            with self.subTest(ключ=ключ):
                получено = self.выполнить(ключ)
                self.assertEqual(len(ждём), len(получено))
                for а, б in zip(ждём, получено):
                    self.assertAlmostEqual(а, б, delta=1e-9 + 1e-9 * abs(а))

    def test_неприменимые(self):
        for ключ in НЕПРИМЕНИМЫ:
            with self.subTest(ключ=ключ):
                self.assertIsNone(self.выполнить(ключ))

    def test_пример_из_стандарта(self):
        # NIST SP 800-22, раздел 2.1.8: ε = 1011010101, n = 10 → P = 0,527089.
        self.assertAlmostEqual(0.527089, sb.частотный([1, 0, 1, 1, 0, 1, 0, 1, 0, 1]), places=6)
        # Раздел 2.3.8: ε = 1001101011 → P = 0,147232.
        self.assertAlmostEqual(0.147232, sb.серии([1, 0, 0, 1, 1, 0, 1, 0, 1, 1]), places=6)

    def test_линейная_сложность_блока(self):
        # Берлекэмп — Мэсси: длина кратчайшего РСЛОС. ПСП x^5 + x^2 + 1 (период 31) — 5; одни нули — 0.
        s = [1, 0, 0, 0, 0]
        for n in range(5, 62):
            s.append(s[n - 3] ^ s[n - 5])
        self.assertEqual(5, sb.линейная_сложность_блока(s))
        self.assertEqual(0, sb.линейная_сложность_блока([0] * 20))
        self.assertEqual(1, sb.линейная_сложность_блока([1] * 20))
        self.assertEqual(20, sb.линейная_сложность_блока([0] * 19 + [1]))

    def test_неполная_гамма(self):
        # Q(a, x): частные случаи с замкнутой формой — Q(1, x) = e^−x, Q(1/2, x) = erfc(√x).
        for x in (0.1, 0.5, 1.0, 2.0, 7.5, 30.0):
            with self.subTest(x=x):
                self.assertAlmostEqual(math.exp(-x), sb.igamc(1.0, x), delta=1e-14)
                self.assertAlmostEqual(math.erfc(math.sqrt(x)), sb.igamc(0.5, x), delta=1e-14)
                self.assertAlmostEqual(1.0, sb.igamc(3.0, x) + sb.igam(3.0, x), delta=1e-14)
        self.assertEqual((1.0, 1.0, 0.0), (sb.igamc(2.0, 0.0), sb.igamc(0.0, 3.0), sb.igam(2.0, -1.0)))
        self.assertEqual(0.0, sb.igamc(2.0, 5000.0))                     # за пределом exp — ноль

    def test_делить_как_в_c(self):
        self.assertEqual([2, -2, -2, 2, 0, 0], [sb._делить(7, 3), sb._делить(-7, 3), sb._делить(7, -3),
                                               sb._делить(-7, -3), sb._делить(-2, 4), sb._делить(2, 4)])


class EntTests(unittest.TestCase):
    """ENT (Fourmilab): эталон — вывод программы ent -t на тех же байтах (6 знаков) и pochisq (1e-8)."""

    def test_как_ent(self):
        g = hashlib.sha256
        случайные = b"".join(g(i.to_bytes(8, "big")).digest() for i in range(3125))           # 100 000 байт
        r = sb.ent(случайные)
        self.assertEqual(100000, r["байт"])
        for ключ, ждём in (("энтропия", 7.998330), ("хи", 232.181760), ("среднее", 127.421600), ("пи", 3.150366),
                           ("корреляция", 0.001081)):
            with self.subTest(ключ=ключ):
                self.assertAlmostEqual(ждём, r[ключ], delta=1.5e-6 * max(1, abs(ждём)))
        self.assertAlmostEqual(ЭНТ_ХИ_P, r["хи_p"], delta=1e-7)
        ряд = sb.ent(bytes(range(256)) * 300 + b"abc" * 1000)
        for ключ, ждём in (("энтропия", 7.951894), ("хи", 9511.278195), ("среднее", 126.390977), ("пи", 2.887218),
                           ("корреляция", 0.976822)):
            with self.subTest(ключ=ключ):
                self.assertAlmostEqual(ждём, ряд[ключ], delta=1.5e-6 * max(1, abs(ждём)))
        self.assertEqual(0.0, ряд["хи_p"])
        self.assertTrue(math.isnan(sb.ent(b"" * 100)["корреляция"]))    # у ENT — «неопределена»
        self.assertEqual(0.0, sb.ent(b"" * 3)["пи"])


class ПроверитьTests(unittest.TestCase):
    def test_сводка(self):
        итог = sb.проверить(sha(20000))
        self.assertEqual((20000, int(sha(20000).sum())), (итог["бит"], итог["единиц"]))
        по_ключу = {т["ключ"]: т for т in итог["тесты"]}
        self.assertEqual([к for к, *_ in sb.ТЕСТЫ], [т["ключ"] for т in итог["тесты"]])
        self.assertEqual((False, True), (по_ключу["частотный"]["мало"], по_ключу["маурер"]["мало"]))
        self.assertIsNone(по_ключу["маурер"]["годен"])                      # не применим — не «пройден»
        self.assertEqual(2, len(по_ключу["суммы"]["p"]))
        self.assertTrue(по_ключу["частотный"]["годен"])
        self.assertEqual(итог["проверено"], sum(1 for т in итог["тесты"] if т["годен"] is not None))
        self.assertEqual(итог["пройдено"], sum(1 for т in итог["тесты"] if т["годен"]))
        плохой = sb.проверить(структурный(20000))
        self.assertFalse({т["ключ"]: т for т in плохой["тесты"]}["частотный"]["годен"])
        self.assertLess(плохой["пройдено"], итог["пройдено"])
        self.assertEqual(round(sb.частотный(sha(20000)), 6), по_ключу["частотный"]["p"][0])
        пусто = sb.проверить([])
        self.assertEqual((0, None, 0), (пусто["бит"], пусто["ent"], пусто["проверено"]))
        self.assertIsNotNone(sb.проверить([1, 0] * 8)["ent"])
        self.assertIsNone(sb.проверить([1] * 7)["ent"])
        self.assertEqual(3, sb.проверить([1] * 24)["ent"]["байт"])       # ENT — по байтам ряда


if __name__ == "__main__":
    unittest.main()
