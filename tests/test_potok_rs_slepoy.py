"""Код Рида — Соломона вслепую (rs_slepoy): префикс, пары, ступени 1 и 2, раскладка, снятие, разбор, слой, окно.

Эталон — свой кодер РС на списках Python (поле и порождающий многочлен строятся здесь же), сверенный
с кодером DVB (dvb.закодировать) и с rs_bch.закодировать_рс; двойной базис — по libfec (таблица
tal, как в test_potok_ispravlenie).
"""

import json
import re
import shutil
import subprocess
import time
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as синтез
from reportgen.potok import razbor
from reportgen.potok import rs_slepoy as R

# -- эталонный кодер -------------------------------------------------------------------------------


class Поле:
    """GF(2^m) по многочлену p — таблицами степеней и логарифмов (списки)."""

    def __init__(self, p):
        self.m = p.bit_length() - 1
        self.q1 = (1 << self.m) - 1
        self.exp, self.log = [0] * (2 * self.q1), [0] * (self.q1 + 1)
        x = 1
        for i in range(self.q1):
            self.exp[i] = self.exp[i + self.q1] = x
            self.log[x] = i
            x <<= 1
            if x >> self.m:
                x ^= p

    def умн(self, a, b):
        return 0 if not a or not b else self.exp[self.log[a] + self.log[b]]

    def степень(self, j):
        return self.exp[j % self.q1]


def кодировать(данные, p, fcr, корней, шаг=1, поле=None):
    """Систематическое слово: данные, затем остаток от D(x)·x^корней по g(x) = Π (x − α^(шаг·(fcr+i)))."""
    F = поле or Поле(p)
    g = [1]
    for i in range(корней):
        к = F.степень(шаг * (fcr + i))
        g = [a ^ F.умн(b, к) for a, b in zip(g + [0], [0] + g)]
    r = list(данные) + [0] * корней                     # деление «в столбик» от старшего
    for i in range(len(данные)):
        c = r[i]
        if c:
            for j in range(1, корней + 1):
                r[i + j] ^= F.умн(c, g[j])
    return list(данные) + r[len(данные):]


def значение(слово, F, j):
    """c(α^j) по схеме Горнера (первый символ — старшая степень)."""
    x = F.степень(j)
    s = 0
    for c in слово:
        s = F.умн(s, x) ^ c
    return s


_TAL = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)


def в_двойной(x):
    return int(np.bitwise_xor.reduce([_TAL[7 - k] for k in range(8) if x >> k & 1] or [0]))


def поток(N, K, *, p=0x11D, fcr=0, шаг=1, блоков=200, I=1, m=None, двойной=False, младший=False,
          фаза=0, ошибок=0.0, синхро="", сид=1, бит=None):
    """Блоки по I слов вперемешку (символ j·I + i — i-го слова) → биты; синхро — биты перед каждым блоком.

    Возвращает (биты, данные блоков: список массивов I·K символов в порядке передачи).
    """
    F = Поле(p)
    m = m or F.m
    г = np.random.default_rng(сид)
    части, данные = [г.integers(0, 2, фаза).astype(np.uint8)], []
    сдвиги = np.arange(m) if младший else np.arange(m - 1, -1, -1)
    всего = фаза
    while (бит is None and len(данные) < блоков) or (бит is not None and всего < бит):
        слова = [кодировать(г.integers(0, 1 << m, K).tolist(), p, fcr, N - K, шаг, F) for _ in range(I)]
        блок = np.array(слова, dtype=np.int64).T.reshape(-1)
        данные.append(блок[:I * K].copy())
        if двойной:
            блок = np.array([в_двойной(int(x)) for x in блок], dtype=np.int64)
        биты = ((блок[:, None] >> сдвиги) & 1).astype(np.uint8).reshape(-1)
        if синхро:
            биты = np.concatenate([np.array([int(ч) for ч in синхро], dtype=np.uint8), биты])
        части.append(биты)
        всего += len(биты)
    биты = np.concatenate(части)
    if бит is not None:
        биты = биты[:бит]
    if ошибок:
        биты = биты ^ (г.random(len(биты)) < ошибок).astype(np.uint8)
    return биты, данные


def в_символы_данных(данные, m, двойной=False, младший=False):
    """Данные блоков в том виде, как их отдаёт снятие: биты символов потока."""
    с = np.concatenate(данные)
    if двойной:
        с = np.array([в_двойной(int(x)) for x in с])
    сдвиги = np.arange(m) if младший else np.arange(m - 1, -1, -1)
    return ((с[:, None] >> сдвиги) & 1).astype(np.uint8).reshape(-1)


class ЭталонTests(unittest.TestCase):
    def test_совпадает_с_кодером_dvb_и_rs_bch(self):
        from reportgen.potok import dvb, rs_bch
        г = np.random.default_rng(3)
        for _ in range(5):
            d = г.integers(0, 256, 188)
            self.assertEqual(кодировать(d.tolist(), 0x11D, 0, 16), dvb.закодировать(d)[0].tolist())
        for p, fcr, корней, шаг, n in [(0x187, 112, 32, 11, 255), (0x187, 120, 18, 1, 219), (0x13, 1, 4, 1, 15),
                                       (0x409, 0, 8, 1, 300)]:
            d = г.integers(0, 1 << (p.bit_length() - 1), n - корней).tolist()
            self.assertEqual(кодировать(d, p, fcr, корней, шаг), rs_bch.закодировать_рс(d, p, fcr, корней, шаг))

    def test_корни_зануляют_слово(self):
        F = Поле(0x187)
        c = кодировать(list(range(1, 224)), 0x187, 112, 32, 11)
        for i in range(32):
            self.assertEqual(значение(c, F, 11 * (112 + i)), 0)
        self.assertNotEqual(значение(c, F, 11 * 111), 0)

    def test_двойной_базис_как_у_libfec(self):
        from reportgen.potok import dlinnye
        self.assertEqual([в_двойной(x) for x in range(256)], dlinnye.В_ДВОЙНОЙ.tolist())


# -- префикс, пары, мера ----------------------------------------------------------------------------


class ПрефиксTests(unittest.TestCase):
    def test_равенство_префикса_это_корень(self):
        """P_j(t0 + N·I) = P_j(t0) ⇔ c(α^j) = 0 у слова полосы t0 — для j = 0 и j ≠ 0, I = 1 и 3."""
        F = Поле(0x11D)
        г = np.random.default_rng(5)
        for I in (1, 3):
            слова = [кодировать(г.integers(0, 256, 40).tolist(), 0x11D, 1, 8) for _ in range(I)]
            шум = г.integers(0, 256, 17).tolist()
            с = np.array(шум + np.array(слова).T.reshape(-1).tolist() + шум, dtype=np.int64)
            for j in (0, 1, 5, 8, 9, 200):
                P = R.префикс(с, I, j, 0x11D)
                for i in range(I):
                    t0 = 17 + i
                    корень = значение(слова[i], F, j) == 0
                    self.assertEqual(bool(P[t0] == P[t0 + 48 * I]), корень, (I, j, i))
                    self.assertEqual(корень, 1 <= j <= 8)

    def test_пары_как_перебором(self):
        г = np.random.default_rng(1)
        for I in (1, 2, 5):
            P = г.integers(0, 16, 600)
            н, d = R.пары(P, I, 3 * I, 20 * I)
            найдено = set(zip(н.tolist(), d.tolist()))
            ожидается = {(t, s) for t in range(len(P)) for s in range(3 * I, 20 * I + 1, I)
                         if t + s < len(P) and P[t] == P[t + s]}
            self.assertEqual(найдено, ожидается, I)

    def test_пары_вырожденного_ряда_ограничены_шагами(self):
        н, d = R.пары(np.zeros(1000, dtype=np.int64), 1, 1, 10_000, шагов=5)
        self.assertEqual(len(d), 5 * 1000 - 15)
        self.assertEqual(set(d.tolist()), {1, 2, 3, 4, 5})

    def test_мера_чернова(self):
        self.assertAlmostEqual(float(R.мера(np.array([10.0]), np.array([1.0]))[0]), 10 * np.log(10) - 9, places=9)
        self.assertEqual(R.мера(np.array([1.0, 0.5]), np.array([1.0, 2.0])).tolist(), [0.0, 0.0])

    def test_символы_и_обратно(self):
        """Биты → символы (фаза, порядок бит, двойной базис) и обратно — тождество."""
        г = np.random.default_rng(2)
        из_двойного = {в_двойной(x): x for x in range(256)}
        for m, порядок, базис in [(8, "старший", "обычный"), (8, "младший", "двойной"), (5, "младший", "обычный"),
                                  (10, "старший", "обычный")]:
            с = г.integers(0, 1 << m, 300)
            биты = R.в_биты(с, m, порядок, базис)
            сдвиги = np.arange(m) if порядок == "младший" else np.arange(m - 1, -1, -1)
            в_потоке = [в_двойной(int(x)) for x in с] if базис == "двойной" else с.tolist()
            self.assertEqual(биты.tolist(), ((np.array(в_потоке)[:, None] >> сдвиги) & 1).reshape(-1).tolist())
            self.assertEqual(R.символы(np.concatenate([[1, 0, 1], биты, [1]]), m, 3, порядок, базис).tolist(),
                             с.tolist())
            if базис == "двойной":
                self.assertEqual(R.символы(биты, m, 0, порядок, "обычный").tolist(), в_потоке)
                self.assertEqual([из_двойного[x] for x in в_потоке], с.tolist())
        with self.assertRaisesRegex(ValueError, "только у символов 8 бит"):
            R.символы(np.zeros(70, dtype=np.uint8), 7, 0, "старший", "двойной")


# -- быстрые проверки частей поиска (и для мутационной проверки) ----------------------------------


def конф1(I=1, m=8):
    return R.Конфигурация(1, m, tuple(range(m)), I)


def конф2(I=1, p=0x187, базис="двойной", порядок="старший", фазы=(0,)):
    return R.Конфигурация(2, 8, фазы, I, p, порядок, базис)


class БыстрыеTests(unittest.TestCase):
    def test_ступень_1_в_одной_конфигурации(self):
        биты, д = поток(64, 48, блоков=300, ошибок=1e-3, фаза=3, сид=1)
        код = R.искать_в(биты, конф1())
        self.assertEqual((код.N, код.k, код.I, код.p, код.fcr, код.шаг, код.фаза, код.сдвиг, код.ступень),
                         (64, 48, 1, 0x11D, 0, 1, 3, 3, 1))
        self.assertTrue(код.сплошь)
        self.assertEqual(код.неисправимых, 0)
        self.assertTrue(np.array_equal(код.данные, в_символы_данных(д, 8)[:len(код.данные)]))
        self.assertEqual(len(код.данные), 8 * 48 * len(код.блоки))
        self.assertEqual(len(код.блоки), 300)
        self.assertEqual(код.слов, 300)
        self.assertGreater(код.исправлено, 0)
        self.assertEqual(код.исправлено_слов, int(round((1 - код.чистых_до) * 300)))
        self.assertIsNone(R.искать_в(биты, конф1(I=2, m=7)))

    def test_ступень_2_ccsds(self):
        биты, д = поток(255, 223, p=0x187, fcr=112, шаг=11, I=2, двойной=True, блоков=50, ошибок=3e-4, сид=2)
        self.assertIsNone(R.искать_в(биты, конф1(I=2)))               # корня 1 нет — ступень 1 не видит
        self.assertIsNone(R.искать_в(биты, конф2(I=2, базис="обычный")))
        код = R.искать_в(биты, конф2(I=2))
        self.assertEqual((код.N, код.k, код.I, код.p, код.fcr, код.шаг, код.базис, код.ступень),
                         (255, 223, 2, 0x187, 112, 11, "двойной", 2))
        self.assertGreater(float((код.данные == в_символы_данных(д, 8, двойной=True)[:len(код.данные)]).mean()),
                           0.9999)

    def test_мельче_чередование(self):
        """IESS (126, 112) при I = 4: при глубине 2 «находится» слово 252 из двух — мельче даёт I = 4."""
        биты, д = поток(126, 112, p=0x187, fcr=120, I=4, блоков=60, сид=3)
        код = R.искать_в(биты, конф2(I=2, базис="обычный"))
        self.assertEqual((код.N, код.k, код.I, код.fcr, код.шаг), (126, 112, 4, 120, 1))
        self.assertTrue(np.array_equal(код.данные, в_символы_данных(д, 8)[:len(код.данные)]))

    def test_случайные_в_конфигурациях(self):
        шум = np.random.default_rng(4).integers(0, 2, 1 << 19).astype(np.uint8)
        for к in (конф1(), конф1(I=3), конф1(m=4), конф2(), конф2(I=2, p=0x11D, базис="обычный")):
            with self.subTest(к=к.подпись()):
                self.assertIsNone(R.искать_в(шум, к))

    def test_кандидаты_и_мера(self):
        биты, _ = поток(204, 188, блоков=200, сид=5)
        с = R.символы(биты, 8, 0)
        к = R._кандидаты(с, конф1(), 0, False, None)
        self.assertEqual((к[0].N, к[0].строка, к[0].j, к[0].фаза, к[0].без_свёртки), (204, 0, 0, 0, False))
        self.assertGreater(к[0].s, 500)
        self.assertGreaterEqual(len(к[0].начала), 150)
        self.assertTrue(all(н % 204 == 0 for н in к[0].начала.tolist()))
        шум = R.символы(np.random.default_rng(5).integers(0, 2, 1 << 19).astype(np.uint8), 8, 0)
        self.assertEqual(R._кандидаты(шум, конф1(), 0, False, None), [])
        self.assertEqual(R._кандидаты(шум[:20], конф1(), 0, False, None), [])

    def test_кандидат_в_кадрах(self):
        биты, _ = поток(255, 223, блоков=80, синхро=format(0x1ACFFC1D, "032b"), сид=6)
        с = R.символы(биты, 8, 0)
        к = R._кандидаты(с, конф1(), 0, False, None)
        self.assertTrue(к[0].без_свёртки)
        self.assertEqual(к[0].N, 255)
        self.assertTrue(all(н % 259 == 4 for н in к[0].начала.tolist()))
        мера, начала, F = R.в_кадрах(np.sort(к[0].начала), 255, 1)
        self.assertEqual(F, 259)
        self.assertGreater(мера, 100)
        self.assertIsNone(R.в_кадрах(np.arange(5), 255, 1))
        self.assertIsNone(R.в_кадрах(np.arange(0, 60, 10), 255, 1))     # все разности не длиннее слова

    def test_раскладка(self):
        # Сплошь: чистые начала в вычетах 7…9 по модулю 30 (I = 3, N = 10).
        чистые = np.array([7, 8, 9, 37, 38, 69, 97, 98, 99, 127, 128])
        сплошь, F, в_кадре, блоки = R.раскладка(200, чистые, 10, 3)
        self.assertEqual((сплошь, F, в_кадре), (True, 0, ()))
        self.assertEqual(блоки.tolist(), [7, 37, 67, 97, 127, 157])
        # Кадры по 100: блок с 4 и с 50 (I = 2, N = 20 — блок 40); в одном кадре второе слово блока испорчено.
        чистые = np.array([f * 100 + r for f in range(12) for r in (4, 5, 50, 51) if (f, r) != (3, 51)])
        сплошь, F, в_кадре, блоки = R.раскладка(1200, чистые, 20, 2)
        self.assertEqual((сплошь, F, в_кадре), (False, 100, (4, 50)))
        self.assertEqual(блоки[:4].tolist(), [4, 50, 104, 150])
        self.assertEqual(блоки[-1], 1150)
        # Блок на два кадра не помещается — не кадры.
        self.assertTrue(R.раскладка(1200, np.array([f * 100 + r for f in range(12) for r in (4, 50)]), 30, 2)[0])
        # Мало начал или без строя — сплошь по лучшему вычету.
        self.assertTrue(R.раскладка(1000, np.array([3, 400, 707]), 20, 1)[0])
        г = np.random.default_rng(1)
        self.assertTrue(R.раскладка(10_000, np.sort(г.choice(10_000, 40, replace=False)), 20, 1)[0])
        self.assertEqual(R.раскладка(100, np.zeros(0, dtype=np.int64), 20, 1)[3].tolist(), [])

    def test_опознать_и_разнообразие(self):
        F = Поле(0x187)
        г = np.random.default_rng(7)
        слова = np.array([кодировать(г.integers(0, 256, 30).tolist(), 0x187, 120, 6, 1, F) for _ in range(40)])
        п = R.опознать(слова, 8, [0x11D, 0x187])
        self.assertEqual((п["многочлен"], п["fcr"], п["шаг"], п["подряд"], п["корней"], п["доля"]),
                         (0x187, 120, 1, 6, 6, 1.0))
        п = R.опознать(слова, 8, [0x187], отобран_j=121)
        self.assertEqual((п["fcr"], п["подряд"]), (120, 6))
        self.assertIsNone(R.опознать(слова, 8, [0x187], отобран_j=5))        # корень отбора не в цепочке
        self.assertIsNone(R.опознать(слова, 8, [0x11D]))
        self.assertIsNone(R.опознать(г.integers(0, 256, (40, 36)), 8, [0x187]))
        self.assertTrue(R._разнообразны(слова))
        self.assertFalse(R._разнообразны(np.zeros((10, 36), dtype=np.int64)))
        self.assertFalse(R._разнообразны(np.tile(слова[:1], (10, 1))))
        self.assertFalse(R._разнообразны(слова[:3]))
        почти_нули = np.zeros((10, 36), dtype=np.int64)
        почти_нули[np.arange(10), np.arange(10)] = 1
        self.assertFalse(R._разнообразны(почти_нули))

    def test_лучшая_фаза_и_чистые(self):
        биты, _ = поток(255, 223, p=0x187, fcr=112, шаг=11, блоков=20, фаза=6, сид=8)
        п = {"многочлен": 0x187, "fcr": 112, "шаг": 11, "корней": 32, "m": 8}
        self.assertEqual(R.корень_j(п), (11 * 112) % 255)
        фаза, чистые = R.лучшая_фаза(биты, 8, 1, 255, п, "старший", "обычный")
        self.assertEqual(фаза, 6)
        self.assertEqual(чистые.tolist(), [255 * i for i in range(20)])

    def test_многочлены_поля(self):
        self.assertEqual(R.многочлены_поля(8)[:2], [0x11D, 0x187])
        self.assertEqual(len(R.многочлены_поля(8)), 16)
        self.assertEqual(R.многочлены_поля(8, [0x12D])[0], 0x12D)
        self.assertEqual(R.многочлены_поля(4), [0x13, 0x19])

    def test_слой_разбор_параметров(self):
        п = R.разобрать_слой("рс 255 223 поле 0x187 fcr 112 шаг 11 глубина 4 базис двойной порядок младший "
                             "сдвиг 13 кадр 8192 блоки 0, 4096")
        self.assertEqual(п, {"N": 255, "k": 223, "m": 8, "многочлен": 0x187, "fcr": 112, "шаг": 11, "I": 4,
                             "сдвиг": 13, "кадр": 8192, "базис": "двойной", "порядок": "младший",
                             "блоки": [0, 4096]})
        п = R.разобрать_слой("RS 15 11 символ 4")
        self.assertEqual((п["многочлен"], п["fcr"], п["шаг"], п["I"], п["сдвиг"], п["кадр"], п["блоки"]),
                         (0x13, 0, 1, 1, None, None, []))


# -- поиск ------------------------------------------------------------------------------------------


def найти(биты, профиль="обычно", срок=240):
    итог = R.поиск(биты, профиль=профиль, срок=срок)
    return итог["найдено"][0] if итог["найдено"] else None


class ПоискTests(unittest.TestCase):
    def проверить(self, биты, данные, N, K, I=1, *, m=8, p=0x11D, fcr=0, шаг=1, базис="обычный",
                  порядок="старший", сдвиг=0, профиль="обычно", точно=True):
        код = найти(биты, профиль)
        self.assertIsNotNone(код)
        self.assertEqual((код.N, код.k, код.I, код.m, код.p, код.fcr, код.шаг, код.базис, код.порядок),
                         (N, K, I, m, p, fcr, шаг, базис, порядок))
        self.assertEqual(код.сдвиг, сдвиг)
        ожидается = в_символы_данных(данные, m, базис == "двойной", порядок == "младший")
        выход = код.данные
        if точно:
            self.assertEqual(код.неисправимых, 0)
            self.assertTrue(np.array_equal(выход, ожидается[:len(выход)]))
            self.assertGreaterEqual(len(выход), len(ожидается) - m * I * K)
        return код

    def test_сплошной_255_223_и_204_188(self):
        for N, K in ((255, 223), (204, 188), (255, 239)):
            with self.subTest(N=N):
                биты, д = поток(N, K, блоков=150, сид=N)
                код = self.проверить(биты, д, N, K)
                self.assertEqual((код.ступень, код.чистых_до, код.исправлено, код.сплошь), (1, 1.0, 0, True))

    def test_ошибки_линии_до_2e_3(self):
        """BER 1e-3 у RS(255, 223): ~87 % слов с ошибками — находится и исправляется; 2e-3 — на 2 Мбит."""
        for N, K, I, ber, бит in ((255, 223, 1, 1e-3, 1 << 20), (204, 188, 1, 1e-3, 1 << 20),
                                  (255, 239, 4, 1e-3, 1 << 21), (255, 223, 1, 2e-3, 1 << 21),
                                  (255, 223, 5, 2e-3, 1 << 22)):
            with self.subTest(N=N, I=I, ber=ber):
                биты, д = поток(N, K, I=I, ошибок=ber, бит=бит, сид=7)
                код = self.проверить(биты, д, N, K, I, точно=False)
                self.assertLess(код.чистых_до, 0.2 if ber >= 1e-3 else 1)
                self.assertGreater(код.исправлено, 0)
                ожидается = в_символы_данных(д, 8)
                совпало = float((код.данные == ожидается[:len(код.данные)]).mean())
                self.assertGreater(совпало, 0.999)

    def test_чередование(self):
        for I in (2, 4, 5, 8):
            with self.subTest(I=I):
                биты, д = поток(255, 223, I=I, бит=1 << 20, сид=I)
                self.проверить(биты, д, 255, 223, I)

    def test_размер_символа_4_7_10_и_укороченные(self):
        for N, K, p, блоков in ((15, 11, 0x13, 3000), (64, 48, 0x11D, 800), (120, 100, 0x89, 400),
                                (300, 284, 0x409, 120)):
            with self.subTest(N=N, p=hex(p)):
                биты, д = поток(N, K, p=p, блоков=блоков, сид=N)
                self.проверить(биты, д, N, K, m=p.bit_length() - 1, p=p)

    def test_битовая_фаза_и_младший_бит(self):
        биты, д = поток(204, 188, блоков=120, фаза=5, сид=3)
        self.проверить(биты, д, 204, 188, сдвиг=5)
        биты, д = поток(204, 188, блоков=120, младший=True, сид=4)
        self.проверить(биты, д, 204, 188, порядок="младший")
        биты, д = поток(15, 11, p=0x13, блоков=3000, фаза=2, младший=True, сид=5)
        self.проверить(биты, д, 15, 11, m=4, p=0x13, порядок="младший", сдвиг=2)

    def test_ступень_2_ccsds_и_iess(self):
        """Корня 1 нет: CCSDS (0x187, fcr 112, шаг 11, двойной базис), IESS (0x187, fcr 120), 0x11D с fcr 1."""
        биты, д = поток(255, 223, p=0x187, fcr=112, шаг=11, I=4, двойной=True, блоков=40, сид=1)
        код = self.проверить(биты, д, 255, 223, 4, p=0x187, fcr=112, шаг=11, базис="двойной")
        self.assertEqual(код.ступень, 2)
        биты, д = поток(219, 201, p=0x187, fcr=120, I=4, ошибок=1e-3, бит=1 << 21, сид=2)
        код = self.проверить(биты, д, 219, 201, 4, p=0x187, fcr=120, точно=False)
        self.assertEqual(код.ступень, 2)
        биты, д = поток(255, 239, fcr=1, блоков=200, сид=3)
        self.проверить(биты, д, 255, 239, fcr=1)

    def test_ccsds_на_сдвинутой_фазе_и_с_ошибками(self):
        биты, д = поток(255, 223, p=0x187, fcr=112, шаг=11, двойной=True, ошибок=5e-4, бит=1 << 21, фаза=5, сид=6)
        код = self.проверить(биты, д, 255, 223, p=0x187, fcr=112, шаг=11, базис="двойной", сдвиг=5, точно=False)
        self.assertEqual(код.ступень, 2)

    def test_слова_в_кадрах_с_заголовком(self):
        """ASM 32 бита + блок I = 2: кадр 4112 бит, начала слов по модулю N не постоянны."""
        asm = format(0x1ACFFC1D, "032b")
        биты, д = поток(255, 223, I=2, блоков=60, синхро=asm, ошибок=5e-4, сид=8)
        код = self.проверить(биты, д, 255, 223, 2, сдвиг=32, точно=False)
        self.assertFalse(код.сплошь)
        self.assertEqual((код.кадр * 8, код.в_кадре), (4112, (4,)))
        self.assertIn("кадр 4112 блоки 0", код.слой())
        self.assertGreater(float((код.данные == в_символы_данных(д, 8)[:len(код.данные)]).mean()), 0.999)
        н = R.описать(код, len(биты))
        self.assertIn("в кадрах по 4112 бит", н.что)
        self.assertTrue(any("остальные 32 бит кадра" in с for с in н.подробно))

    def test_синхробайт_вне_слова(self):
        """0x47 + RS(203, 187) — не DVB: кадр 204 байта."""
        биты, д = поток(203, 187, блоков=150, синхро=format(0x47, "08b"), сид=9)
        код = self.проверить(биты, д, 203, 187, сдвиг=8)
        self.assertEqual(код.кадр * 8, 204 * 8)

    def test_ложных_нет(self):
        """Случайные биты, свёрточный код, LDPC, текст, HDLC — РС не находится (план «быстро»)."""
        г = np.random.default_rng(12)
        H = синтез.qc_ldpc()
        G, _ = синтез.систематический(H)
        ldpc = (г.integers(0, 2, (300, G.shape[0])).astype(np.uint8) @ G % 2).reshape(-1).astype(np.uint8)
        текст = ("Код Рида — Соломона вслепую: длина слова и начало по префиксу. " * 3000).encode()
        кадры = синтез.hdlc([bytes(г.integers(0, 256, int(г.integers(40, 300)), dtype=np.uint8)) for _ in range(600)])
        случаи = {"случайные": г.integers(0, 2, 1 << 20).astype(np.uint8),
                  "свёрточный": синтез.свёрточный(г.integers(0, 2, 1 << 20).astype(np.uint8)),
                  "LDPC": ldpc, "текст": np.unpackbits(np.frombuffer(текст, np.uint8)),
                  "HDLC": np.unpackbits(np.frombuffer(кадры, np.uint8)),
                  "нули": np.zeros(1 << 20, dtype=np.uint8)}
        for имя, биты in случаи.items():
            with self.subTest(имя=имя):
                self.assertIsNone(найти(биты, "быстро"))

    def test_ложных_нет_в_обычном_плане(self):
        """Весь план «обычно» на случайных битах — ни одной находки (ступень 2 на всех фазах и т. д.)."""
        итог = R.поиск(np.random.default_rng(21).integers(0, 2, 1 << 20).astype(np.uint8), профиль="обычно",
                       все=True)
        self.assertEqual(итог["найдено"], [])
        self.assertEqual(итог["по"], итог["всего"])

    def test_план_по_профилям(self):
        быстро, обычно, глубоко = (R.план(п) for п in ("быстро", "обычно", "глубоко"))
        self.assertLess(len(быстро), len(обычно))
        self.assertLess(len(обычно), len(глубоко))
        self.assertEqual(быстро[0], R.Конфигурация(1, 8, tuple(range(8)), 1))
        self.assertTrue(set(быстро) <= set(обычно) <= set(глубоко))
        self.assertFalse(any(к.ступень == 2 and к.фазы != (0,) for к in быстро))
        self.assertTrue(any(к.ступень == 2 and к.порядок == "младший" for к in обычно))
        self.assertTrue(any(к.ступень == 2 and к.p == 0x1F5 for к in глубоко))
        self.assertFalse(any(к.базис == "двойной" and к.m != 8 for к in глубоко))
        self.assertEqual(max(к.I for к in глубоко), 16)

    def test_стоп_и_срок(self):
        биты, _ = поток(255, 223, блоков=150)
        итог = R.поиск(биты, стоп=lambda: True)
        self.assertEqual((итог["найдено"], итог["по"]), ([], 0))
        шум = np.random.default_rng(1).integers(0, 2, 1 << 20).astype(np.uint8)
        t = time.monotonic()
        итог = R.поиск(шум, профиль="глубоко", срок=1.0)
        self.assertLess(time.monotonic() - t, 30)
        self.assertLess(итог["по"], итог["всего"])
        ходы = []
        R.поиск(биты, ход=ходы.append)
        self.assertEqual(ходы, ["РС вслепую: 1/" + str(len(R.план())) + " — ступень 1: символ 8 бит, фазы 0…7, глубина 1"])

    def test_находка(self):
        биты, д = поток(255, 223, I=4, блоков=40, ошибок=2e-4, сид=4)
        н = R.найти(биты)
        self.assertEqual(н.уровень, "код")
        self.assertTrue(н.что.startswith("код Рида — Соломона (255, 223) над GF(2^8), t = 16, чередование 4"))
        с = н.свойства
        self.assertEqual((с["N"], с["k"], с["глубина"], с["многочлен"], с["fcr"], с["данных_в_блоке"]),
                         (255, 223, 4, 0x11D, 0, 8 * 4 * 223))
        self.assertEqual(с["слой"], "рс 255 223 поле 0x11D fcr 0 шаг 1 глубина 4 базис обычный порядок старший сдвиг 0")
        self.assertTrue(any(стр.startswith("исправление алгебраическое") for стр in н.подробно))
        self.assertIsNone(R.найти(биты[:4000]))


# -- слой стола и разбор ----------------------------------------------------------------------------


class СлойTests(unittest.TestCase):
    def test_по_параметрам(self):
        биты, д = поток(255, 223, p=0x187, fcr=112, шаг=11, I=4, двойной=True, блоков=30, фаза=13, ошибок=5e-4)
        ожидается = в_символы_данных(д, 8, двойной=True)
        for слой in ("рс 255 223 поле 0x187 fcr 112 шаг 11 глубина 4 базис двойной",
                     "рс 255 223 поле 0x187 fcr 112 шаг 11 глубина 4 базис двойной сдвиг 13"):
            ряд, находка = razbor.снять_вручную(биты, слой)
            self.assertGreater(float((ряд == ожидается[:len(ряд)]).mean()), 0.9999)
            self.assertEqual(len(ряд), len(ожидается))
            self.assertEqual(находка.свойства["сдвиг"], 13)
            self.assertIn("параметры заданы слоем", " ".join(находка.подробно))

    def test_младший_бит_символ_и_кадры(self):
        биты, д = поток(15, 11, p=0x13, блоков=500, младший=True, сид=2)
        ряд, _ = razbor.снять_вручную(биты, "рс 15 11 поле 0x13 символ 4 порядок младший")
        self.assertTrue(np.array_equal(ряд, в_символы_данных(д, 4, младший=True)))
        биты, д = поток(255, 223, I=2, блоков=20, синхро=format(0x1ACFFC1D, "032b"))
        ряд, _ = razbor.снять_вручную(биты, "рс 255 223 глубина 2 сдвиг 32 кадр 4112 блоки 0")
        self.assertTrue(np.array_equal(ряд, в_символы_данных(д, 8)))

    def test_вслепую(self):
        биты, д = поток(204, 188, блоков=60)
        ряд, находка = razbor.снять_вручную(биты, "рс вслепую быстро")
        self.assertTrue(np.array_equal(ряд, в_символы_данных(д, 8)))
        self.assertIn("рс 204 188 поле 0x11D", находка.свойства["слой"])
        with self.assertRaisesRegex(ValueError, "не найден"):
            razbor.снять_вручную(np.random.default_rng(1).integers(0, 2, 1 << 18).astype(np.uint8), "рс вслепую быстро")

    def test_ошибки_указания(self):
        шум = np.random.default_rng(1).integers(0, 2, 1 << 16).astype(np.uint8)
        for слой, текст in [("рс", "слой РС"), ("рс 255 224", "N − K чётно"), ("рс 300 284", "N ≤ 255"),
                            ("рс 255 223 поле 0x11B", "не примитивный"), ("рс 255 223 шаг 5", "взаимно простой"),
                            ("рс 15 11 поле 0x13 символ 4 базис двойной", "двойной базис"),
                            ("рс 255 223 кадр 4113 блоки 0", "кратны"),
                            ("рс 255 223", "слов с нулевыми синдромами нет")]:
            with self.subTest(слой=слой), self.assertRaisesRegex(ValueError, текст):
                razbor.снять_вручную(шум, слой)
        # «рс iess …» — прежний слой IESS, не этот.
        with self.assertRaisesRegex(ValueError, "RS \\(219, 201\\) IESS"):
            razbor.снять_вручную(шум, "рс iess idr e1 глубина 4")


class РазборTests(unittest.TestCase):
    def разобрать(self, биты, профиль="быстро"):
        return razbor.разобрать(данные=np.packbits(биты).tobytes(), профиль=профиль)

    def test_автомат_255_223_и_204_188(self):
        for N, K, ber in ((255, 223, 0.0), (255, 223, 1e-3), (204, 188, 5e-4)):
            with self.subTest(N=N, ber=ber):
                биты, _ = поток(N, K, бит=1 << 21, ошибок=ber, сид=N)
                р = self.разобрать(биты)
                первая = р.находки[0]
                self.assertTrue(первая.что.startswith(f"код Рида — Соломона ({N}, {K})"), первая.что)
                self.assertEqual(первая.свойства["N"], N)
                self.assertIn(f"код Рида — Соломона ({N}, {K})", р.отчёт())

    def test_рс_останавливает_перебор_уровня(self):
        биты, _ = поток(255, 223, бит=1 << 20)
        р = self.разобрать(биты)
        self.assertTrue(any("не проверялось: поток опознан как код Рида — Соломона" in д for д in р.другие))

    def test_dvb_по_прежнему_и_пакеты_204_не_dvb(self):
        р = razbor.разобрать(данные=синтез.dvb(400, перемежать=False), профиль="быстро")
        self.assertTrue(any(н.что.startswith("код Рида — Соломона (204, 188) DVB") for н in р.находки))
        биты, _ = поток(203, 187, блоков=600, синхро=format(0x47, "08b"))
        р = self.разобрать(биты)
        что = [н.что for н in р.находки]
        self.assertIn("MPEG-TS, пакеты по 204 байт", что)
        self.assertTrue(any(ч.startswith("код Рида — Соломона (203, 187)") and "не DVB" in ч for ч in что), что)

    def test_рс_в_блоке_запасной_путь(self):
        """Блок = два слова RS(204, 188) подряд младшим битом — быстрый путь не видит, слепой находит."""
        from reportgen.potok import dlinnye
        биты, д = поток(204, 188, блоков=40, младший=True)
        н = dlinnye.рс_в_блоке(биты, 2 * 204 * 8)
        self.assertIsNotNone(н)
        self.assertEqual((н.свойства["N"], н.свойства["порядок"], н.свойства["данных_в_блоке"]),
                         (204, "младший", 2 * 188 * 8))
        self.assertTrue(np.array_equal(н.дальше, в_символы_данных(д, 8, младший=True)))
        self.assertIn("в блоке 3264 бит", н.что)


# -- окно стола: пункты app.js и поиск через сервер ------------------------------------------------


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ПунктыСтолаTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js([], ["ВКЛАДКИ_КОДОВ", "МОДУЛЯЦИИ_ПОИСКА", "ОПЕРАЦИИ_СТОЛА"])
        код += "process.stdout.write(JSON.stringify({в: ВКЛАДКИ_КОДОВ, о: ОПЕРАЦИИ_СТОЛА}));"
        готово = subprocess.run(["node", "-e", код], capture_output=True, text=True, timeout=20)
        assert готово.returncode == 0, готово.stderr
        д = json.loads(готово.stdout)
        cls.вкладки = {в["имя"]: в for в in д["в"]}
        cls.операции = {о["id"]: о for о in д["о"]}

    def test_пункты_и_вкладка_rs(self):
        from test_potok_turbo_std import ВкладкиКодовTests  # noqa: PLC0415
        о = self.операции
        self.assertEqual(self.вкладки["RS"]["пункты"], ["c-rs-iess", "c-rs-blind", "c-rs-set"])
        self.assertEqual((о["c-rs-blind"]["вид"], о["c-rs-blind"]["сделать"], о["c-rs-blind"]["раздел"]),
                         ("действие", "поиск-рс", "ПУ код"))
        слой = ВкладкиКодовTests.заполнить(о["c-rs-set"])
        self.assertEqual(слой, "рс 255 223 поле 0x11D fcr 0 шаг 1 глубина 1 базис обычный порядок старший символ 8")
        биты, д = поток(255, 223, блоков=30)
        ряд, _ = razbor.снять_вручную(биты, слой)
        self.assertTrue(np.array_equal(ряд, в_символы_данных(д, 8)))
        слой = ВкладкиКодовTests.заполнить(о["c-rs-set"], {"поле": "0x187", "fcr": 112, "шаг": 11, "глубина": 4,
                                                             "базис": "двойной", "сдвиг": " 13 "})
        self.assertTrue(слой.endswith("базис двойной порядок старший символ 8 сдвиг 13"), слой)

    def test_окно_в_коде(self):
        from test_potok_sessii import APP_JS  # noqa: PLC0415
        текст = APP_JS.read_text(encoding="utf-8")
        self.assertIn("case 'поиск-рс': окноПоискаРс(у); return null;", текст)
        окно = текст[текст.index("function окноПоискаРс("):текст.index("function окноПоискаПериода(")]
        for кусок in ("'/rs/'", "path + 'search'".replace("path", "путь"), "стоп = true", "'Обработка'", "/derive",
                      "x.ключ === в.ключ"):
            self.assertIn(кусок, окно)
        self.assertNotIn("accept", окно)


class ОкноЧерезСерверTests(unittest.TestCase):
    """Поиск РС вслепую через сервер: части по срокам, найденный вариант со слоем, ошибки запроса."""

    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client
        self.assertEqual(200, self.к.get("/api/potok").status_code)

    def задание(self, ряд):
        з = self.сеть.app.state.potok
        ид = з.создать(владелец=self.сеть.repos.users.by_login("engineer").id, имя="rs.bin",
                       данные=np.packbits(ряд).tobytes(), разбирать=False, бит=len(ряд))
        for _ in range(200):
            if з.прочитать(ид)["состояние"] == "готово":
                break
            time.sleep(0.05)
        return ид

    def test_поиск_частями_и_обработка(self):
        биты, д = поток(255, 223, I=4, блоков=40, ошибок=5e-4)
        путь = f"/api/potok/{self.задание(биты)}/rs/search"
        с, найдено, всего = 0, [], None
        while True:
            d = self.к.post(путь, json={"stage": 0, "профиль": "быстро", "с": с}).json()
            найдено += d["найдено"]
            всего = d["всего"]
            self.assertGreater(d["по"], с)
            с = d["по"]
            if найдено or с >= всего:
                break
        self.assertEqual(всего, len(R.план("быстро")))
        в = найдено[0]
        self.assertEqual((в["N"], в["k"], в["глубина"], в["многочлен"]), (255, 223, 4, "0x11D"))
        self.assertEqual(в["подпись"], "RS(255,223) GF(2^8) 0x11D fcr 0 I=4")
        ряд, _ = razbor.снять_вручную(биты, в["слой"])
        self.assertGreater(float((ряд == в_символы_данных(д, 8)).mean()), 0.9999)

    def test_ошибки_запроса(self):
        путь = f"/api/potok/{self.задание(np.zeros(4096, dtype=np.uint8))}/rs/search"
        о = self.к.post(путь, json={"stage": 0, "профиль": "медленно"})
        self.assertEqual(400, о.status_code)
        self.assertIn("профиль", о.json()["error"])
        self.assertEqual(400, self.к.post(путь, json={"stage": 0, "с": -1}).status_code)
        self.assertEqual(400, self.к.post(путь, json={"stage": "x"}).status_code)
        self.assertEqual(404, self.к.post("/api/potok/20200101-000000-abcdef/rs/search", json={"stage": 0}).status_code)
        self.к.post("/api/auth/logout")
        self.assertEqual(401, self.к.post(путь, json={"stage": 0}).status_code)


if __name__ == "__main__":
    unittest.main()
