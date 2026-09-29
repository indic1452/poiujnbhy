"""Матрицы над GF(2): Гаусс, ранг, обратная, системы, ядро, систематический вид, PLU, код, матрица из потока.

Каждое действие сверяется с независимым расчётом: пространство строк — перебором линейных
комбинаций (множество слов удваивается на каждую независимую строку), ранг — логарифм его
размера, обратная — A·A⁻¹ = I, определитель — перманент по модулю 2 перебором перестановок,
решения — подстановкой всех x, ядро — H·Gᵀ = 0 и перебором, спектр и d — перебором всех
кодовых слов. Коды строятся из определения: Хэмминг (7, 4) — столбцы H все ненулевые
3-битные векторы, расширенный (8, 4) — с общей проверкой чётности, Голей — квадратично-вычетный
код длины 23: циклические сдвиги слова с единицами на квадратичных вычетах по модулю 23.
Окно в браузере (разбор ввода, сводка, текст итога) — функции из app.js в node.
"""

import itertools
import json
import math
import shutil
import subprocess
import time
import unittest
from collections import Counter

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import matricy_gf2 as м
from test_potok_sessii import функции_js

# -- независимые расчёты -------------------------------------------------------------------

def в_число(строка) -> int:
    return int("".join(str(int(x)) for x in строка) or "0", 2)


def пространство(A) -> set[int]:
    """Все линейные комбинации строк A (как числа): каждая строка вне множества удваивает его."""
    слова = {0}
    for строка in np.asarray(A):
        v = в_число(строка)
        if v not in слова:
            слова |= {c ^ v for c in слова}
    return слова


def ранг_перебором(A) -> int:
    return int(math.log2(len(пространство(A))))


def независимы(A) -> bool:
    """Строки независимы, если у каждой есть свой столбец, где единица только у неё (единичная подматрица)."""
    A = np.asarray(A)
    единичные = A[:, A.sum(axis=0) == 1]
    return bool(единичные.any(axis=1).all())


def умножить(A, B) -> np.ndarray:
    return ((np.asarray(A, dtype=np.int64) @ np.asarray(B, dtype=np.int64)) % 2).astype(np.uint8)


def спектр_слов(слова: set[int], n: int) -> list[int]:
    счёт = Counter(bin(c).count("1") for c in слова)
    return [счёт.get(w, 0) for w in range(n + 1)]


def все_векторы(n: int) -> np.ndarray:
    return ((np.arange(1 << n)[:, None] >> np.arange(n)[::-1]) & 1).astype(np.uint8)


def матрица(*строки) -> np.ndarray:
    return np.array([[int(c) for c in с] for с in строки], dtype=np.uint8)


def многочлен(множители) -> list[int]:
    """Произведение многочленов с целыми коэффициентами (списками от младшего)."""
    итог = [1]
    for f in множители:
        новое = [0] * (len(итог) + len(f) - 1)
        for i, a in enumerate(итог):
            for j, b in enumerate(f):
                новое[i + j] += a * b
        итог = новое
    return итог


def хэмминг_H(r: int) -> np.ndarray:
    """Проверочная кода Хэмминга по определению: столбец j — двоичная запись j + 1 (все ненулевые r-битные)."""
    n = (1 << r) - 1
    return np.array([[((j + 1) >> (r - 1 - i)) & 1 for j in range(n)] for i in range(r)], dtype=np.uint8)


def квадратично_вычетный(p: int, вычеты: bool = True) -> np.ndarray:
    """Циклические сдвиги слова длины p с единицами на квадратичных (или невычетах) по модулю p."""
    Q = {x * x % p for x in range(1, p)}
    места = sorted(Q) if вычеты else sorted(set(range(1, p)) - Q)
    v = np.zeros(p, dtype=np.uint8)
    v[места] = 1
    return np.array([np.roll(v, i) for i in range(p)], dtype=np.uint8)


def случайные(rng, m, n, ранг=None) -> np.ndarray:
    """Случайная матрица; с ``ранг`` — произведение m × r на r × n (ранг не больше r)."""
    if ранг is None:
        return rng.integers(0, 2, (m, n), dtype=np.uint8)
    return умножить(rng.integers(0, 2, (m, ранг)), rng.integers(0, 2, (ранг, n)))


ФОРМЫ = [(1, 1), (1, 5), (5, 1), (3, 3), (4, 7), (7, 4), (8, 8), (10, 12), (12, 10), (6, 70), (70, 6), (9, 130)]


class ВводВыводTests(unittest.TestCase):
    def test_строки_текст_и_списки(self):
        ждём = матрица("101", "011")
        np.testing.assert_array_equal(ждём, м.разобрать(["1 0 1", "0\t1 1"]))
        np.testing.assert_array_equal(ждём, м.разобрать("101\n\n 011 \r\n"))
        np.testing.assert_array_equal(ждём, м.разобрать("101;011"))
        np.testing.assert_array_equal(ждём, м.разобрать([[1, 0, 1], [], [0, 1, 1]]))
        self.assertEqual(np.uint8, м.разобрать(["1"]).dtype)

    def test_ошибки_ввода(self):
        случаи = [
            (None, "A: ожидались строки из 0 и 1"),
            (5, "A: ожидались строки из 0 и 1"),
            ([5], "A: строка 1 — ожидались 0 и 1"),
            (["10", [1, 2]], "A: строка 2 — не только 0 и 1"),
            (["10", "1a"], "A: строка 2 — не только 0 и 1"),
            (["2"], "A: строка 1 — не только 0 и 1"),
            (["101", "", "01"], "A: в строке 3 2 бит, а в первой — 3"),
            (["", " "], "A: матрица пуста"),
            ([], "A: матрица пуста"),
            (["1"] * 4097, "A: 4097 × 1 — больше предела 4096 × 4096"),
            (["1" * 4097], "A: 1 × 4097 — больше предела 4096 × 4096"),
        ]
        for значение, текст in случаи:
            with self.subTest(значение=str(значение)[:30]), self.assertRaises(ValueError) as о:
                м.разобрать(значение)
            self.assertEqual(текст, str(о.exception))
        # Предел включительно.
        self.assertEqual((4096, 1), м.разобрать(["1"] * 4096).shape)
        self.assertEqual((1, 4096), м.разобрать(["1" * 4096]).shape)

    def test_вектор(self):
        np.testing.assert_array_equal([1, 0, 1], м.вектор("101"))
        np.testing.assert_array_equal([1, 0, 1], м.вектор(["1", "0", "1"]))
        np.testing.assert_array_equal([1], м.вектор("1"))
        with self.assertRaises(ValueError) as о:
            м.вектор(["101", "011"], "b")
        self.assertEqual("b: ожидался вектор — одна строка или один столбец, а тут 2 × 3", str(о.exception))

    def test_строками(self):
        self.assertEqual(["101", "011"], м.строками(матрица("101", "011")))
        self.assertEqual(["0110"], м.строками(np.array([0, 1, 1, 0], dtype=np.uint8)))
        self.assertEqual(["", ""], м.строками(np.zeros((2, 0), dtype=np.uint8)))
        self.assertEqual([], м.строками(np.zeros((0, 5), dtype=np.uint8)))
        self.assertEqual(["11"], м.строками(матрица("110", "101")[1:, ::2]))


class ГауссTests(unittest.TestCase):
    """Ступенчатый и приведённый вид: свойства вида и то же пространство строк; журнал шагов — воспроизводим."""

    def проверить_вид(self, A, с, приведённый):
        m, n = A.shape
        R, опорные, r = с["матрица"], с["опорные"], с["ранг"]
        self.assertEqual((m, n), R.shape)
        self.assertEqual(ранг_перебором(A), r)
        self.assertEqual(r, len(опорные))
        self.assertEqual(sorted(set(опорные)), опорные)
        self.assertFalse(R[r:].any())
        self.assertEqual(пространство(A), пространство(R))
        for i, j in enumerate(опорные):
            self.assertFalse(R[i, :j].any())
            self.assertEqual(1, R[i, j])
            self.assertFalse(R[i + 1:, j].any())
            if приведённый:
                self.assertFalse(R[:i, j].any())

    def test_случайные_формы(self):
        rng = np.random.default_rng(1)
        for m, n in ФОРМЫ:
            for ранг in (None, 1, 2, min(m, n)):
                A = случайные(rng, m, n, ранг)
                for приведённый in (False, True):
                    with self.subTest(m=m, n=n, ранг=ранг, приведённый=приведённый):
                        self.проверить_вид(A, м.ступенчатый(A, приведённый), приведённый)
                        self.assertEqual(ранг_перебором(A), м.ранг(A))

    def test_опорные_как_у_приведённого(self):
        # Опорные столбцы определяются пространством строк: у Гаусса и Гаусса — Жордана они те же.
        rng = np.random.default_rng(2)
        for m, n in ФОРМЫ:
            A = случайные(rng, m, n, 2)
            self.assertEqual(м.ступенчатый(A)["опорные"], м.ступенчатый(A, True)["опорные"])

    def воспроизвести(self, A, шаги):
        a = A.copy()
        for ш in шаги:
            r = ш["строка"]
            if ш["обмен"] is not None:
                self.assertNotEqual(r, ш["обмен"])
                a[[r, ш["обмен"]]] = a[[ш["обмен"], r]]
            self.assertEqual(1, a[r, ш["столбец"]])
            for t in ш["прибавлена_к"]:
                self.assertEqual(1, a[t, ш["столбец"]])
                a[t] ^= a[r]
            self.assertEqual(м.строками(a), ш["матрица"])
        return a

    def test_журнал_шагов(self):
        rng = np.random.default_rng(3)
        for m, n in [(3, 7), (5, 5), (16, 64), (6, 3), (1, 1)]:
            A = случайные(rng, m, n)
            for приведённый in (False, True):
                с = м.ступенчатый(A, приведённый)
                self.assertEqual([ш["столбец"] for ш in с["шаги"]], с["опорные"])
                self.assertEqual(list(range(с["ранг"])), [ш["строка"] for ш in с["шаги"]])
                np.testing.assert_array_equal(с["матрица"], self.воспроизвести(A, с["шаги"]))
        # Пример по шагам: первый столбец — с перестановкой, у Жордана прибавление и выше опорной.
        A = матрица("011", "101", "110")
        шаги = м.ступенчатый(A, True)["шаги"]
        self.assertEqual({"столбец": 0, "строка": 0, "обмен": 1, "прибавлена_к": [2], "матрица": ["101", "011", "011"]}, шаги[0])
        self.assertEqual({"столбец": 1, "строка": 1, "обмен": None, "прибавлена_к": [2], "матрица": ["101", "011", "000"]}, шаги[1])
        self.assertEqual(2, len(шаги))
        self.assertEqual([2], м.ступенчатый(A)["шаги"][1]["прибавлена_к"])
        # Выше опорной — только у Жордана.
        B = матрица("11", "01")
        self.assertEqual([], м.ступенчатый(B)["шаги"][1]["прибавлена_к"])
        self.assertEqual([0], м.ступенчатый(B, True)["шаги"][1]["прибавлена_к"])

    def test_журнал_только_у_небольших(self):
        rng = np.random.default_rng(4)
        self.assertIsNotNone(м.ступенчатый(случайные(rng, 16, 64))["шаги"])
        self.assertIsNone(м.ступенчатый(случайные(rng, 17, 64))["шаги"])
        self.assertIsNone(м.ступенчатый(случайные(rng, 16, 65))["шаги"])

    def test_большая_быстро(self):
        rng = np.random.default_rng(5)
        A = случайные(rng, 4096, 4096)
        A[:, 7] = A[:, 3] ^ A[:, 5]                # один зависимый столбец — ранг меньше
        t = time.time()
        с = м.ступенчатый(A)
        self.assertLess(time.time() - t, 20)
        self.assertNotIn(7, с["опорные"])
        R = с["матрица"]
        self.assertEqual(с["ранг"], int(R.any(axis=1).sum()))
        for i, j in enumerate(с["опорные"][:50]):
            self.assertEqual(1, R[i, j])
            self.assertFalse(R[i + 1:, j].any())


class ОбратнаяОпределительTests(unittest.TestCase):
    @staticmethod
    def невырожденная(rng, n):
        L = np.tril(rng.integers(0, 2, (n, n)), -1) + np.eye(n, dtype=np.int64)
        U = np.triu(rng.integers(0, 2, (n, n)), 1) + np.eye(n, dtype=np.int64)
        return умножить(умножить(L, U)[rng.permutation(n)], np.eye(n, dtype=np.uint8))

    def test_обратная(self):
        rng = np.random.default_rng(6)
        for n in (1, 2, 5, 8, 33, 65, 130):
            A = self.невырожденная(rng, n)
            о = м.обратная(A)
            with self.subTest(n=n):
                np.testing.assert_array_equal(np.eye(n, dtype=np.uint8), умножить(A, о))
                np.testing.assert_array_equal(np.eye(n, dtype=np.uint8), умножить(о, A))
                self.assertEqual(1, м.определитель(A))

    def test_вырожденная(self):
        rng = np.random.default_rng(7)
        for n in (2, 5, 70):
            A = случайные(rng, n, n, n - 1)
            self.assertIsNone(м.обратная(A))
            self.assertEqual(0, м.определитель(A))

    def test_определитель_перебором_перестановок(self):
        # Над GF(2) определитель равен перманенту по модулю 2.
        rng = np.random.default_rng(8)
        for n in range(1, 7):
            for _ in range(6):
                A = случайные(rng, n, n)
                перманент = sum(all(A[i, s[i]] for i in range(n)) for s in itertools.permutations(range(n))) % 2
                self.assertEqual(перманент, м.определитель(A))

    def test_не_квадратная(self):
        for f, что in ((м.определитель, "определитель"), (м.обратная, "обратная")):
            with self.assertRaises(ValueError) as о:
                f(матрица("101", "011"))
            self.assertEqual(f"{что} — у квадратной матрицы, а у этой 2 × 3", str(о.exception))


class СистемыЯдроTests(unittest.TestCase):
    def test_решить_против_перебора(self):
        rng = np.random.default_rng(9)
        for m, n in [(1, 1), (3, 5), (5, 3), (6, 6), (8, 10), (4, 4)]:
            for ранг in (None, 1, 2):
                A = случайные(rng, m, n, ранг)
                for b in (rng.integers(0, 2, m, dtype=np.uint8), умножить(A, rng.integers(0, 2, (n, 1)))[:, 0]):
                    X = все_векторы(n)
                    решения = {в_число(x) for x in X[(умножить(X, A.T) == b).all(axis=1)]}
                    с = м.решить(A, b)
                    with self.subTest(m=m, n=n, ранг=ранг, b=b.tolist()):
                        self.assertEqual(bool(решения), с["совместна"])
                        self.assertEqual(ранг_перебором(A), с["ранг"])
                        self.assertEqual(n - с["ранг"], len(с["базис"]))
                        self.assertEqual(ранг_перебором(с["базис"]) if len(с["базис"]) else 0, len(с["базис"]))
                        self.assertFalse(умножить(с["базис"], A.T).any())
                        if решения:
                            ч = в_число(с["частное"])
                            self.assertEqual(решения, {ч ^ v for v in пространство(с["базис"])})
                        else:
                            self.assertIsNone(с["частное"])

    def test_решить_длина_b(self):
        with self.assertRaises(ValueError) as о:
            м.решить(матрица("10", "01"), np.array([1, 0, 1], dtype=np.uint8))
        self.assertEqual("b: 3 бит, а строк в A — 2", str(о.exception))

    def test_ядро(self):
        rng = np.random.default_rng(10)
        for m, n in ФОРМЫ + [(3, 12)]:
            for ранг in (None, 1, min(m, n)):
                G = случайные(rng, m, n, ранг)
                H = м.ядро(G)
                r = ранг_перебором(G)
                with self.subTest(m=m, n=n, ранг=ранг):
                    self.assertEqual((n - r, n), H.shape)
                    self.assertFalse(умножить(H, G.T).any())
                    if n <= 12:
                        X = все_векторы(n)
                        self.assertEqual({в_число(x) for x in X[~умножить(X, G.T).any(axis=1)]}, пространство(H))
                    else:
                        self.assertTrue(независимы(H))

    def test_проверочная_и_обратно(self):
        # Ядро ядра — снова пространство строк G.
        rng = np.random.default_rng(11)
        for m, n in [(4, 7), (3, 10), (6, 8)]:
            G = случайные(rng, m, n)
            self.assertEqual(пространство(G), пространство(м.ядро(м.ядро(G))))


class СистематическийTests(unittest.TestCase):
    def проверить(self, G, с):
        n = G.shape[1]
        k = ранг_перебором(G)
        перестановка = с["перестановка"]
        self.assertEqual((k, n), (с["k"], с["n"]))
        self.assertEqual(list(range(n)), sorted(перестановка))
        np.testing.assert_array_equal(np.eye(k, dtype=np.uint8), с["G"][:, :k])
        self.assertEqual(пространство(G[:, перестановка]), пространство(с["G"]))
        np.testing.assert_array_equal(с["G"][:, k:].T, с["H"][:, :k])
        np.testing.assert_array_equal(np.eye(n - k, dtype=np.uint8), с["H"][:, k:])
        self.assertFalse(умножить(с["H"], с["G"].T).any())
        self.assertFalse(умножить(с["H_исходные"], G.T).any())
        np.testing.assert_array_equal(с["H"], с["H_исходные"][:, перестановка])
        self.assertEqual(перестановка != list(range(n)), с["переставлены"])

    def test_случайные(self):
        rng = np.random.default_rng(12)
        for m, n in ФОРМЫ:
            G = случайные(rng, m, n, 2 if min(m, n) > 2 else None)
            with self.subTest(m=m, n=n):
                self.проверить(G, м.систематический(G))

    def test_перестановка_нужна_и_нет(self):
        rng = np.random.default_rng(13)
        G = np.hstack([np.eye(4, dtype=np.uint8), случайные(rng, 4, 3)])
        с = м.систематический(G)
        self.assertFalse(с["переставлены"])
        self.assertEqual(list(range(7)), с["перестановка"])
        np.testing.assert_array_equal(G, с["G"])
        # Первый столбец нулевой — опорные не первые: столбцы переставляются.
        G2 = матрица("0110", "0011")
        с2 = м.систематический(G2)
        self.проверить(G2, с2)
        self.assertEqual([1, 2, 0, 3], с2["перестановка"])
        self.assertTrue(с2["переставлены"])


class PLUTests(unittest.TestCase):
    def test_разложение(self):
        rng = np.random.default_rng(14)
        for m, n in ФОРМЫ + [(100, 80), (130, 20)]:
            for ранг in (None, 1, 3):
                A = случайные(rng, m, n, ранг)
                с = м.разложение(A)
                P, L, U = с["P"], с["L"], с["U"]
                with self.subTest(m=m, n=n, ранг=ранг):
                    np.testing.assert_array_equal(умножить(P, A), умножить(L, U))
                    self.assertTrue((P.sum(axis=0) == 1).all() and (P.sum(axis=1) == 1).all())
                    np.testing.assert_array_equal(A[с["перестановка"]], умножить(P, A))
                    np.testing.assert_array_equal(np.ones(m), np.diag(L))
                    self.assertFalse(np.triu(L, 1).any())
                    np.testing.assert_array_equal(м.ступенчатый(A)["матрица"], U)
                    if min(m, n) <= 12:
                        self.assertEqual(ранг_перебором(A), с["ранг"])

    def test_перестановка_с_множителями(self):
        # Перестановка после прибавлений переставляет и уже найденные множители L.
        A = матрица("100", "100", "001", "010")
        с = м.разложение(A)
        np.testing.assert_array_equal(умножить(с["P"], A), умножить(с["L"], с["U"]))
        self.assertEqual([0, 3, 2, 1], с["перестановка"])
        np.testing.assert_array_equal(матрица("1000", "0100", "0010", "1001"), с["L"])


class ДействияTests(unittest.TestCase):
    def test_произведение_сумма(self):
        rng = np.random.default_rng(15)
        for m, k, p in [(1, 1, 1), (3, 5, 2), (70, 130, 9), (2, 4096, 3)]:
            A, B = случайные(rng, m, k), случайные(rng, k, p)
            np.testing.assert_array_equal(умножить(A, B), м.произведение(A, B))
            np.testing.assert_array_equal(A ^ случайные(np.random.default_rng(1), m, k), м.сумма(A, случайные(np.random.default_rng(1), m, k)))
        with self.assertRaises(ValueError) as о:
            м.произведение(матрица("10", "01"), матрица("101"))
        self.assertEqual("произведение: столбцов у A 2, а строк у B 1 — должны совпадать", str(о.exception))
        with self.assertRaises(ValueError) as о:
            м.сумма(матрица("101", "011"), матрица("10"))
        self.assertEqual("сумма: у A 2 × 3, у B 1 × 2 — должны совпадать", str(о.exception))

    def test_синдромы(self):
        H = хэмминг_H(3)
        G = м.ядро(H)
        слова = умножить(np.array([[1, 0, 1, 1]]), G)
        ошибка = слова.copy()
        ошибка[0, 4] ^= 1
        с = м.синдромы(H, np.vstack([слова, ошибка]))
        np.testing.assert_array_equal([0, 0, 0], с[0])
        # Синдром одиночной ошибки — столбец H на её месте (у Хэмминга — номер места + 1 двоично).
        np.testing.assert_array_equal(H[:, 4], с[1])
        with self.assertRaises(ValueError) as о:
            м.синдромы(H, матрица("101"))
        self.assertEqual("векторы: 3 бит, а столбцов у H — 7", str(о.exception))


class КодыTests(unittest.TestCase):
    """Спектр и d — перебором всех слов; коды строятся из определения."""

    def test_хэмминг_7_4(self):
        H = хэмминг_H(3)
        self.assertEqual({в_число(H[:, j]) for j in range(7)}, set(range(1, 8)))
        G = м.ядро(H)
        с = м.код(G)
        слова = пространство(G)
        веса = [w for w in range(1, 8) if спектр_слов(слова, 7)[w]]
        self.assertEqual((7, 4, 3, 1), (с["n"], с["k"], с["d"], с["исправляет"]))
        self.assertEqual(веса[0], с["d"])
        self.assertEqual([[w, c] for w, c in enumerate(спектр_слов(слова, 7)) if c], с["спектр"])
        self.assertEqual("перебор 2^4 кодовых слов", с["способ"])
        self.assertEqual("", с["сообщение"])

    def test_расширенный_хэмминг_8_4(self):
        G = м.ядро(хэмминг_H(3))
        G8 = np.hstack([G, G.sum(axis=1, keepdims=True) % 2]).astype(np.uint8)
        слова = пространство(G8)
        d = min(bin(c).count("1") for c in слова if c)
        self.assertEqual(4, d)
        с = м.код(G8)
        self.assertEqual((8, 4, 4, 1), (с["n"], с["k"], с["d"], с["исправляет"]))
        self.assertEqual([[w, c] for w, c in enumerate(спектр_слов(слова, 8)) if c], с["спектр"])

    def test_голей_квадратично_вычетный_23(self):
        for вычеты in (True, False):
            G = квадратично_вычетный(23, вычеты)
            слова = пространство(G)
            k = int(math.log2(len(слова)))
            d = min(bin(c).count("1") for c in слова if c)
            self.assertEqual((12, 7), (k, d))
            с = м.код(G)
            self.assertEqual((23, 12, 7, 3), (с["n"], с["k"], с["d"], с["исправляет"]))
            спектр = [[w, c] for w, c in enumerate(спектр_слов(слова, 23)) if c]
            self.assertEqual(спектр, с["спектр"])
            # Через двойственный код (2^11 слов) и тождество Мак-Вильямс — тот же спектр.
            self.assertEqual(спектр_слов(слова, 23), м.спектр_маквильямс(м.ядро(G)))

    def test_спектр_случайных(self):
        rng = np.random.default_rng(16)
        for m, n in [(1, 1), (3, 5), (6, 9), (10, 70), (12, 130), (9, 9), (4, 4)]:
            G = случайные(rng, m, n)
            with self.subTest(m=m, n=n):
                self.assertEqual(спектр_слов(пространство(G), n), м.спектр_перебором(G))

    def test_маквильямс_против_перебора(self):
        rng = np.random.default_rng(17)
        for m, n in [(3, 5), (7, 16), (12, 30), (5, 5), (1, 3)]:
            G = случайные(rng, m, n)
            with self.subTest(m=m, n=n):
                self.assertEqual(спектр_слов(пространство(G), n), м.спектр_маквильямс(м.ядро(G)))

    def test_кравчук_из_определения(self):
        for n in range(1, 13):
            for j in range(n + 1):
                ждём = многочлен([[1, 1]] * (n - j) + [[1, -1]] * j)
                self.assertEqual(ждём, м.кравчук(n, j), (n, j))

    def test_перебор_до_20(self):
        G = np.hstack([np.eye(20, dtype=np.uint8), np.zeros((20, 1), dtype=np.uint8)])
        self.assertEqual([math.comb(20, w) for w in range(20)] + [1, 0], м.спектр_перебором(G))
        with self.assertRaises(ValueError) as о:
            м.спектр_перебором(np.eye(21, dtype=np.uint8))
        self.assertEqual("весовой спектр перебором — при k ≤ 20, а у кода k = 21 (2^21 слов)", str(о.exception))
        с = м.код(np.eye(20, dtype=np.uint8))
        self.assertEqual("перебор 2^20 кодовых слов", с["способ"])

    def test_код_через_двойственный(self):
        # k = 21: перебор 2^21 слов не нужен — у двойственного одно слово (n − k = 0).
        с = м.код(np.eye(21, dtype=np.uint8))
        self.assertEqual([[w, math.comb(21, w)] for w in range(22)], с["спектр"])
        self.assertEqual((1, 0), (с["d"], с["исправляет"]))
        self.assertEqual("перебор 2^0 слов двойственного кода и тождество Мак-Вильямс", с["способ"])
        # k = 21, n = 26: независимый перебор всех 2^21 слов (сложение по удвоению).
        rng = np.random.default_rng(18)
        G = np.hstack([np.eye(21, dtype=np.uint8), случайные(rng, 21, 5)])
        слова = np.zeros(1, dtype=np.int64)
        for строка in G:
            слова = np.concatenate([слова, слова ^ в_число(строка)])
        веса = sum((слова >> b) & 1 for b in range(26))
        с = м.код(G)
        self.assertEqual([[w, int(c)] for w, c in enumerate(np.bincount(веса, minlength=27)) if c], с["спектр"])

    def test_код_отказ_и_пределы(self):
        rng = np.random.default_rng(19)
        с = м.код(np.hstack([np.eye(21, dtype=np.uint8), случайные(rng, 21, 21)]))
        self.assertEqual((None, None, None, ""), (с["d"], с["спектр"], с["исправляет"], с["способ"]))
        self.assertEqual("d и весовой спектр не считаются: перебор слов кода — при k ≤ 20, двойственного — при n − k ≤ 20 "
                         "и n ≤ 1024; здесь k = 21, n − k = 21", с["сообщение"])
        self.assertEqual((21, 42), с["G"].shape)

        # n = 1024 — ещё через двойственный, n = 1025 — уже нет. Код: e_i + e_(k+i) для i < 20, e_i дальше —
        # спектр по определению (1 + z²)^20 · (1 + z)^(k − 20).
        def код_длины(n):
            k = n - 20
            G = np.zeros((k, n), dtype=np.uint8)
            G[np.arange(k), np.arange(k)] = 1
            G[np.arange(20), k + np.arange(20)] = 1
            return G, k

        G, k = код_длины(1024)
        с = м.код(G)
        ждём = многочлен([[1, 0, 1]] * 20 + [[1, 1]] * (k - 20))
        self.assertEqual([[w, c if c < 1 << 53 else str(c)] for w, c in enumerate(ждём) if c], с["спектр"])
        self.assertEqual(1, с["d"])
        self.assertIsInstance(с["спектр"][1][1], int)
        self.assertIsInstance(с["спектр"][500][1], str)
        с = м.код(код_длины(1025)[0])
        self.assertIsNone(с["спектр"])
        self.assertIn("здесь k = 1005, n − k = 20", с["сообщение"])

    def test_повторение(self):
        # Код повторения: единственное ненулевое слово — из одних единиц, d = n.
        с = м.код(np.ones((3, 5), dtype=np.uint8))
        self.assertEqual((1, 5, 5, 2), (с["k"], с["n"], с["d"], с["исправляет"]))
        self.assertEqual([[0, 1], [5, 1]], с["спектр"])

    def test_нулевой_код(self):
        с = м.код(np.zeros((2, 5), dtype=np.uint8))
        self.assertEqual((0, 5), (с["k"], с["n"]))
        self.assertEqual([[0, 1]], с["спектр"])
        self.assertIsNone(с["d"])
        self.assertEqual("код из одного нулевого слова: d не определено", с["сообщение"])
        self.assertEqual((0, 5), с["G"].shape)
        np.testing.assert_array_equal(np.eye(5, dtype=np.uint8), с["H"])


class ИзПотокаTests(unittest.TestCase):
    def test_кадры_линейного_кода(self):
        rng = np.random.default_rng(20)
        n, k = 24, 10
        G = случайные(rng, k, n)
        while ранг_перебором(G) < k:
            G = случайные(rng, k, n)
        кадры = умножить(rng.integers(0, 2, (300, k)), G)
        биты = кадры.ravel()
        A = м.из_потока(биты, n, 200)
        np.testing.assert_array_equal(кадры[:200], A)
        self.assertEqual(k, м.ранг(A))
        H = м.ядро(A)
        self.assertEqual((n - k, n), H.shape)
        self.assertFalse(умножить(H, G.T).any())
        о = м.о_потоке(A)
        self.assertEqual({"строк": 200, "длина": 24, "ранг": 10}, {к: о[к] for к in ("строк", "длина", "ранг")})
        self.assertEqual("ранг 10 < 24: между битами строки 14 линейных проверок — похоже на линейный блоковый код (24, 10) "
                         "или постоянные биты; проверки — ядро (H)", о["вывод"])
        # Строк — сколько есть целых, не больше заданного.
        self.assertEqual((300, 24), м.из_потока(биты, n, 4096).shape)
        self.assertEqual((300, 24), м.из_потока(np.concatenate([биты, биты[:23]]), n, 4096).shape)
        self.assertEqual((1, 4096), м.из_потока(np.ones(4096, dtype=np.uint8), 4096, 1).shape)

    def test_выводы(self):
        rng = np.random.default_rng(21)
        self.assertEqual("все 5 строк независимы — для вывода о коде нужно больше строк, чем бит в строке (8)",
                         м.о_потоке(np.hstack([np.eye(5, dtype=np.uint8), np.zeros((5, 3), dtype=np.uint8)]))["вывод"])
        self.assertEqual("все 4 строк независимы — для вывода о коде нужно больше строк, чем бит в строке (4)",
                         м.о_потоке(np.eye(4, dtype=np.uint8))["вывод"])
        # Строк не больше длины, но они зависимы — проверки есть.
        self.assertEqual("ранг 1 < 8: между битами строки 7 линейных проверок — похоже на линейный блоковый код (8, 1) "
                         "или постоянные биты; проверки — ядро (H)", м.о_потоке(np.ones((3, 8), dtype=np.uint8))["вывод"])
        A = случайные(rng, 50, 8)
        self.assertEqual("ранг полный (8): линейных проверок нет — строки такой длины с этого бита не образуют линейного кода "
                         "(или в них ошибки)", м.о_потоке(A)["вывод"])

    def test_пределы(self):
        биты = np.ones(100, dtype=np.uint8)
        for длина, строк, текст in [(0, 5, "длина строки — от 1 до 4096 бит"), (4097, 5, "длина строки — от 1 до 4096 бит"),
                                    (8, 0, "строк — от 1 до 4096"), (8, 4097, "строк — от 1 до 4096"),
                                    (101, 5, "в массиве с этого бита нет ни одной строки длиной 101 бит")]:
            with self.subTest(длина=длина, строк=строк), self.assertRaises(ValueError) as о:
                м.из_потока(биты, длина, строк)
            self.assertEqual(текст, str(о.exception))
        self.assertEqual((1, 100), м.из_потока(биты, 100, 1).shape)
        self.assertEqual((7, 1), м.из_потока(биты, 1, 7).shape)


class ВыполнитьTests(unittest.TestCase):
    """Итог действия целиком — как его отдаёт API (матрицы строками)."""

    A = ["1101", "0111", "1010"]

    def test_все_действия(self):
        A = м.разобрать(self.A)
        B = м.разобрать(["10", "01", "11", "00"])
        и = {оп: м.выполнить(оп, A, B=B if оп == "произведение" else A if оп == "сумма" else None,
                              b=np.array([1, 0, 1], dtype=np.uint8), векторы=м.разобрать(["1101", "0000"]))
             for оп in м.ОПЕРАЦИИ if оп not in ("определитель", "обратная")}
        self.assertEqual({"операция": "гаусс", "размер": [3, 4], "матрица": ["1101", "0111", "0000"], "опорные": [0, 1], "ранг": 2,
                          "главная": "матрица"}, {к: в for к, в in и["гаусс"].items() if к != "шаги"})
        self.assertEqual(["1010", "0111", "0000"], и["жордан"]["матрица"])
        self.assertEqual(2, len(и["жордан"]["шаги"]))
        self.assertEqual(["100", "010", "001"], и["разложение"]["P"])
        self.assertEqual(["100", "010", "111"], и["разложение"]["L"])
        self.assertEqual(и["гаусс"]["матрица"], и["разложение"]["U"])
        р = и["разложение"]
        self.assertEqual(("U", [0, 1, 2], [0, 1], 2), (р["главная"], р["перестановка"], р["опорные"], р["ранг"]))
        self.assertEqual({"операция": "ранг", "размер": [3, 4], "ранг": 2, "опорные": [0, 1], "полный": False}, и["ранг"])
        self.assertEqual({"операция": "решить", "размер": [3, 4], "совместна": True, "ранг": 2, "базис": ["1110", "0101"],
                          "главная": "базис", "частное": "1000"}, и["решить"])
        for оп in ("ядро", "проверочная", "порождающая"):
            self.assertEqual({"операция": оп, "размер": [3, 4], "матрица": ["1110", "0101"], "ранг": 2, "размерность": 2,
                              "главная": "матрица"}, и[оп])
        с = и["систематический"]
        self.assertEqual((["1010", "0111"], ["1110", "0101"], [0, 1, 2, 3], False, 2, 4, "G"),
                         (с["G"], с["H"], с["перестановка"], с["переставлены"], с["k"], с["n"], с["главная"]))
        к = и["код"]
        self.assertEqual((2, [[0, 1], [2, 1], [3, 2]], "перебор 2^2 кодовых слов", 0), (к["d"], к["спектр"], к["способ"], к["исправляет"]))
        self.assertEqual({"операция": "синдромы", "размер": [3, 4], "матрица": ["101", "000"], "нулевых": 1, "главная": "матрица"},
                         и["синдромы"])
        self.assertEqual(["11", "10", "01"], и["произведение"]["матрица"])
        self.assertEqual(["0000"] * 3, и["сумма"]["матрица"])
        self.assertEqual(["101", "110", "011", "110"], и["транспонирование"]["матрица"])
        json.dumps(и)                               # всё — для JSON

    def test_определитель_обратная(self):
        A = м.разобрать(["110", "011", "001"])
        self.assertEqual({"операция": "определитель", "размер": [3, 3], "определитель": 1}, м.выполнить("определитель", A))
        о = м.выполнить("обратная", A)
        self.assertEqual({"операция": "обратная", "размер": [3, 3], "вырождена": False, "ранг": 3,
                          "матрица": ["111", "011", "001"], "главная": "матрица"}, о)
        о = м.выполнить("обратная", м.разобрать(["11", "11"]))
        self.assertEqual({"операция": "обратная", "размер": [2, 2], "вырождена": True, "ранг": 1}, о)
        с = м.выполнить("решить", м.разобрать(["11", "11"]), b=np.array([1, 0], dtype=np.uint8))
        self.assertEqual((False, None, ["11"]), (с["совместна"], с["частное"], с["базис"]))
        р = м.выполнить("ранг", м.разобрать(["10", "01", "11"]))
        self.assertTrue(р["полный"])

    def test_неизвестное(self):
        with self.assertRaises(ValueError) as о:
            м.выполнить("вращение", м.разобрать(["1"]))
        self.assertTrue(str(о.exception).startswith("неизвестное действие «вращение»: гаусс, жордан, разложение"))


class МатрицыЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — общая заготовка веб-тестов

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client

    def дождаться(self, ид):
        for _ in range(900):
            с = self.к.get(f"/api/potok/{ид}").json()
            if с["состояние"] in ("готово", "ошибка"):
                return с
            time.sleep(0.1)
        self.fail("не дождались")

    def ошибка(self, тело, код=400):
        о = self.к.post("/api/potok/matrix", json=тело)
        self.assertEqual(код, о.status_code, о.text)
        return о.json()["error"]

    def test_введённая(self):
        к = self.к
        о = к.post("/api/potok/matrix", json={"op": "гаусс", "A": ["011", "101", "110"]})
        self.assertEqual(200, о.status_code, о.text)
        d = о.json()
        self.assertEqual((["101", "011", "000"], [0, 1], 2), (d["матрица"], d["опорные"], d["ранг"]))
        self.assertEqual(2, len(d["шаги"]))
        d = к.post("/api/potok/matrix", json={"op": "решить", "A": "11\n01", "b": ["1", "1"]}).json()
        self.assertEqual((True, "01"), (d["совместна"], d["частное"]))
        d = к.post("/api/potok/matrix", json={"op": "произведение", "A": [[1, 1]], "B": ["10", "01"]}).json()
        self.assertEqual(["11"], d["матрица"])
        d = к.post("/api/potok/matrix", json={"op": "синдромы", "A": м.строками(хэмминг_H(3)), "vectors": ["0000001"]}).json()
        self.assertEqual(["111"], d["матрица"])
        self.assertNotIn("поток", d)

    def test_ошибки(self):
        self.assertTrue(self.ошибка({"op": "вращение", "A": ["1"]}).startswith("неизвестное действие с матрицей; есть: гаусс"))
        self.assertEqual("A: ожидались строки из 0 и 1", self.ошибка({"op": "ранг"}))
        self.assertEqual("A: в строке 2 1 бит, а в первой — 2", self.ошибка({"op": "ранг", "A": ["10", "1"]}))
        self.assertEqual("B: ожидались строки из 0 и 1", self.ошибка({"op": "произведение", "A": ["10"]}))
        self.assertEqual("произведение: столбцов у A 2, а строк у B 1 — должны совпадать",
                         self.ошибка({"op": "произведение", "A": ["10"], "B": ["10"]}))
        self.assertEqual("b: ожидался вектор — одна строка или один столбец, а тут 2 × 2",
                         self.ошибка({"op": "решить", "A": ["10", "01"], "b": ["10", "01"]}))
        self.assertEqual("векторы: матрица пуста", self.ошибка({"op": "синдромы", "A": ["10"], "vectors": [""]}))
        self.assertEqual("определитель — у квадратной матрицы, а у этой 1 × 2", self.ошибка({"op": "определитель", "A": ["10"]}))
        self.assertEqual("A: 1 × 4097 — больше предела 4096 × 4096", self.ошибка({"op": "ранг", "A": ["1" * 4097]}))
        # Второе, которое действию не нужно, не проверяется.
        self.assertEqual(200, self.к.post("/api/potok/matrix", json={"op": "ранг", "A": ["1"], "B": 5}).status_code)

    def test_из_массива(self):
        к = self.к
        rng = np.random.default_rng(22)
        n, kk = 24, 12
        G = случайные(rng, kk, n)
        while ранг_перебором(G) < kk:
            G = случайные(rng, kk, n)
        кадры = умножить(rng.integers(0, 2, (400, kk)), G)
        биты = np.concatenate([rng.integers(0, 2, 5, dtype=np.uint8), кадры.ravel(), np.zeros(3, dtype=np.uint8)])
        ид = к.post("/api/potok", data={"profile": "быстро"},
                    files={"file": ("код.bin", np.packbits(биты).tobytes(), "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        источник = {"job": ид, "stage": 0, "period": n, "shift": 5, "rows": 300}
        о = к.post("/api/potok/matrix", json={"op": "проверочная", "source": источник})
        self.assertEqual(200, о.status_code, о.text)
        d = о.json()
        H = м.разобрать(d["матрица"])
        self.assertEqual((n - kk, n), H.shape)
        self.assertFalse(умножить(H, G.T).any())
        self.assertEqual({"строк": 300, "длина": 24, "ранг": 12, "сдвиг": 5, "бит_в_массиве": len(биты)},
                         {к_: в for к_, в in d["поток"].items() if к_ != "вывод"})
        self.assertIn("код (24, 12)", d["поток"]["вывод"])
        self.assertEqual(м.строками(кадры[:256]), d["A"])
        # Код по кадрам: d — как перебором слов G.
        d = к.post("/api/potok/matrix", json={"op": "код", "source": источник | {"rows": 40}}).json()
        self.assertEqual(40, len(d["A"]))
        слова = пространство(G)
        self.assertEqual((12, min(bin(c).count("1") for c in слова if c)), (d["k"], d["d"]))
        # Строк — сколько есть целых после первого бита.
        d = к.post("/api/potok/matrix", json={"op": "ранг", "source": источник | {"rows": 4096}}).json()
        self.assertEqual((400, 12), (d["поток"]["строк"], d["ранг"]))
        # Не с того бита — проверок меньше (ранг больше k): куски двух кадров.
        d = к.post("/api/potok/matrix", json={"op": "ранг", "source": источник | {"shift": 0}}).json()
        self.assertEqual(17, d["ранг"])
        # Не та длина — проверок нет.
        d = к.post("/api/potok/matrix", json={"op": "ранг", "source": источник | {"period": 25}}).json()
        self.assertEqual(25, d["ранг"])
        self.assertTrue(d["поток"]["вывод"].startswith("ранг полный (25)"))
        # Ошибки источника.
        всего = len(биты)
        self.assertEqual(f"первый бит — от 0 до {всего - 1}: в массиве {всего} бит", self.ошибка({"op": "ранг", "source": источник | {"shift": всего}}))
        self.assertEqual(f"первый бит — от 0 до {всего - 1}: в массиве {всего} бит", self.ошибка({"op": "ранг", "source": источник | {"shift": -1}}))
        self.assertEqual(200, к.post("/api/potok/matrix", json={"op": "ранг", "source": источник | {"shift": всего - 1, "period": 1}}).status_code)
        self.assertEqual("длина строки — от 1 до 4096 бит", self.ошибка({"op": "ранг", "source": источник | {"period": 0}}))
        self.assertEqual("длина строки — от 1 до 4096 бит", self.ошибка({"op": "ранг", "source": источник | {"period": 5000}}))
        self.assertEqual("строк — от 1 до 4096", self.ошибка({"op": "ранг", "source": источник | {"rows": -2}}))
        self.assertEqual("строк — от 1 до 4096", self.ошибка({"op": "ранг", "source": источник | {"rows": 4097}}))
        self.assertEqual("источник: {job, stage, period, shift, rows} — массив и целые числа",
                         self.ошибка({"op": "ранг", "source": {"stage": 0}}))
        self.assertEqual("источник: {job, stage, period, shift, rows} — массив и целые числа",
                         self.ошибка({"op": "ранг", "source": источник | {"period": "x"}}))
        self.assertEqual("источник: {job, stage, period, shift, rows} — массив и целые числа",
                         self.ошибка({"op": "ранг", "source": "x"}))
        self.ошибка({"op": "ранг", "source": источник | {"job": "20200101-000000-abcdef"}}, 404)
        self.ошибка({"op": "ранг", "source": источник | {"stage": 9}}, 400)
        # Чужой массив не виден.
        self.сеть.login("zam")
        self.ошибка({"op": "ранг", "source": источник}, 404)


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноМатрицВБраузереTests(unittest.TestCase):
    """Разбор ввода, сводка и текст итога окна «Матрицы» — функции из app.js в node на итогах сервера."""

    ФУНКЦИИ = ["разобратьМатрицу", "примерХэмминга", "кускиСтрокиМатрицы", "линейкаМатрицы", "перестановкаКратко", "шагГаусса",
               "матрицыИтога", "сводкаМатриц", "текстИтогаМатриц"]

    def выполнить(self, случаи: list[dict]) -> list:
        код = функции_js(self.ФУНКЦИИ, ["ДЕЙСТВИЯ_МАТРИЦ"]) + """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const итог = случаи.map((с) => {
    switch (с.что) {
    case 'разбор': return разобратьМатрицу(с.текст, с.ширина);
    case 'хэмминг': return примерХэмминга(с.r);
    case 'куски': return кускиСтрокиМатрицы(с.строка, new Set(с.опорные), с.группа);
    case 'линейка': return линейкаМатрицы(с.n, с.группа);
    case 'шаг': return шагГаусса(с.ш);
    case 'перестановка': return перестановкаКратко(с.п);
    case 'матрицы': return матрицыИтога(с.d);
    case 'сводка': return сводкаМатриц(с.d);
    case 'текст': return текстИтогаМатриц(с.d, с.до === null ? Infinity : с.до);
    case 'действия': return ДЕЙСТВИЯ_МАТРИЦ.map((д) => [д.оп, д.второе || null]);
    default: return null;
    }
});
process.stdout.write(JSON.stringify(итог));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=60)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_действия_как_на_сервере(self):
        [д] = self.выполнить([{"что": "действия"}])
        self.assertEqual(list(м.ОПЕРАЦИИ), [оп for оп, _ in д])
        self.assertEqual(м.ВТОРОЕ, {оп: в for оп, в in д if в})

    def test_разбор_ввода(self):
        случаи = [
            ("1 0 1\n0,1,1", 0, {"строки": ["101", "011"]}),
            ("[1 0 1]; (0|1|1)\r\n\n_1_1_0_", 0, {"строки": ["101", "011", "110"]}),
            ("0x5\n0XA", 0, {"строки": ["0101", "1010"]}),
            ("0x5\n0xa", 6, {"строки": ["000101", "001010"]}),
            ("0x1F", 5, {"строки": ["11111"]}),
            ("0x1F", 4, {"ошибка": "строка 1: 0x1F не помещается в 4 бит"}),
            ("0x0F", 4, {"строки": ["1111"]}),
            ("0x0F", 8, {"строки": ["00001111"]}),
            ("0x0F", "2.7", {"ошибка": "строка 1: 0x0F не помещается в 2 бит"}),
            ("0x0F", "", {"строки": ["00001111"]}),
            ("0x0F", -3, {"строки": ["00001111"]}),
            ("0x", 0, {"ошибка": "строка 1: в HEX — только цифры 0–9 и a–f"}),
            ("10\n0xG1", 0, {"ошибка": "строка 2: в HEX — только цифры 0–9 и a–f"}),
            ("10\n12", 0, {"ошибка": "строка 2: только 0 и 1 (или HEX с 0x)"}),
            ("x01", 0, {"ошибка": "строка 1: только 0 и 1 (или HEX с 0x)"}),
            ("101\n\n11", 0, {"ошибка": "строка 3: 2 бит, а в первой — 3"}),
            ("101\n1101", 0, {"ошибка": "строка 2: 4 бит, а в первой — 3"}),
            ("  \n", 0, {"ошибка": "матрица пуста"}),
            (None, 0, {"ошибка": "матрица пуста"}),
            ("0x3\n11", 2, {"строки": ["11", "11"]}),
            ("0x9", 0, {"строки": ["1001"]}),
            ("0x8", 6, {"строки": ["001000"]}),
            ("0xF", 4, {"строки": ["1111"]}),
            ("0xbcde", 0, {"строки": ["1011110011011110"]}),
        ]
        итоги = self.выполнить([{"что": "разбор", "текст": т, "ширина": w} for т, w, _ in случаи])
        for (т, w, ждём), итог in zip(случаи, итоги, strict=True):
            self.assertEqual(ждём, итог, (т, w))
        # Разобранное сервер принимает как есть.
        np.testing.assert_array_equal(матрица("101", "011"), м.разобрать(итоги[0]["строки"]))

    def test_пример_хэмминга(self):
        итоги = self.выполнить([{"что": "хэмминг", "r": r} for r in (2, 3, 4)])
        for r, итог in zip((2, 3, 4), итоги, strict=True):
            self.assertEqual(м.строками(хэмминг_H(r)), итог)
        self.assertEqual(["0001111", "0110011", "1010101"], итоги[1])

    def test_куски_и_линейка(self):
        итоги = self.выполнить([
            {"что": "куски", "строка": "1011", "опорные": [], "группа": 0},
            {"что": "куски", "строка": "1011", "опорные": [0, 1, 3], "группа": 0},
            {"что": "куски", "строка": "1011", "опорные": [1], "группа": 2},
            {"что": "куски", "строка": "101100", "опорные": [2], "группа": 2},
            {"что": "куски", "строка": "1111", "опорные": [2, 3], "группа": 2},
            {"что": "линейка", "n": 12, "группа": 0},
            {"что": "линейка", "n": 20, "группа": 8},
            {"что": "линейка", "n": 16, "группа": 8},
            {"что": "линейка", "n": 1, "группа": 8},
        ])
        self.assertEqual([["1011", False]], итоги[0])
        self.assertEqual([["10", True], ["1", False], ["1", True]], итоги[1])
        self.assertEqual([["1", False], ["0", True], [" 11", False]], итоги[2])
        self.assertEqual([["10 ", False], ["1", True], ["1 00", False]], итоги[3])
        self.assertEqual([["11 ", False], ["11", True]], итоги[4])
        self.assertEqual("012345678901", итоги[5])
        self.assertEqual("0        8        16", итоги[6])
        self.assertEqual("0        8", итоги[7])
        self.assertEqual("0", итоги[8])
        # Линейка встаёт над строкой с промежутками: номер группы — над её первым битом.
        строка = "".join(т for т, _ in self.выполнить([{"что": "куски", "строка": "1" * 20, "опорные": [], "группа": 8}])[0])
        self.assertEqual([0, 9, 18], [i for i, ч in enumerate(итоги[6]) if ч != " " and (i == 0 or итоги[6][i - 1] == " ")])
        self.assertEqual(["1", "1", "1"], [строка[i] for i in (0, 9, 18)])
        self.assertEqual([" ", " "], [строка[i] for i in (8, 17)])

    def итоги_сервера(self):
        """Итоги выполнить() — как их получает окно."""
        H = хэмминг_H(3)
        G = м.ядро(H)
        итоги = {
            "гаусс": м.выполнить("гаусс", м.разобрать(["011", "101", "110"])),
            "жордан": м.выполнить("жордан", м.разобрать(["011", "101", "110"])),
            "разложение": м.выполнить("разложение", м.разобрать(["01", "10"])),
            "ранг": м.выполнить("ранг", м.разобрать(["10", "01"])),
            "ранг-неполный": м.выполнить("ранг", м.разобрать(["11", "11", "00"])),
            "определитель": м.выполнить("определитель", м.разобрать(["10", "01"])),
            "определитель-0": м.выполнить("определитель", м.разобрать(["11", "11"])),
            "обратная": м.выполнить("обратная", м.разобрать(["11", "01"])),
            "обратная-нет": м.выполнить("обратная", м.разобрать(["11", "11"])),
            "решить": м.выполнить("решить", м.разобрать(["11", "01"]), b=np.array([1, 1], dtype=np.uint8)),
            "решить-нет": м.выполнить("решить", м.разобрать(["11", "11"]), b=np.array([1, 0], dtype=np.uint8)),
            "ядро": м.выполнить("ядро", м.разобрать(["110"])),
            "проверочная": м.выполнить("проверочная", G),
            "порождающая": м.выполнить("порождающая", H),
            "систематический": м.выполнить("систематический", м.разобрать(["0110", "0011"])),
            "код": м.выполнить("код", G),
            "код-отказ": м.выполнить("код", np.hstack([np.eye(21, dtype=np.uint8), np.eye(21, dtype=np.uint8)])),
            "синдромы": м.выполнить("синдромы", H, векторы=м.разобрать(["0000000", "0000001"])),
            "произведение": м.выполнить("произведение", м.разобрать(["11"]), B=м.разобрать(["10", "01"])),
            "сумма": м.выполнить("сумма", м.разобрать(["11"]), B=м.разобрать(["10"])),
            "транспонирование": м.выполнить("транспонирование", м.разобрать(["110"])),
        }
        поток = м.разобрать(["1100", "0110", "1010"] * 3)
        итоги["поток"] = м.выполнить("ранг", поток) | {"поток": м.о_потоке(поток) | {"сдвиг": 7, "бит_в_массиве": 99},
                                                       "A": м.строками(поток[:4])}
        итоги["опорных-много"] = м.выполнить("ранг", np.eye(30, dtype=np.uint8))
        итоги["опорных-24"] = м.выполнить("ранг", np.eye(24, dtype=np.uint8))
        итоги["код-полный"] = м.выполнить("код", np.eye(3, dtype=np.uint8))
        return json.loads(json.dumps(итоги))

    def test_сводка(self):
        и = self.итоги_сервера()
        имена = list(и)
        сводки = dict(zip(имена, self.выполнить([{"что": "сводка", "d": и[к]} for к in имена]), strict=True))
        self.assertEqual({
            "гаусс": ["A: 3 × 3 · ранг 2 · опорные столбцы 0, 1"],
            "жордан": ["A: 3 × 3 · ранг 2 · опорные столбцы 0, 1"],
            "разложение": ["A: 2 × 2 · ранг 2 · опорные столбцы 0, 1"],
            "ранг": ["Ранг 2 — полный · опорные столбцы 0, 1"],
            "ранг-неполный": ["Ранг 1 — неполный: меньше 2 · опорные столбцы 0"],
            "определитель": ["Определитель 1 — матрица невырождена"],
            "определитель-0": ["Определитель 0 — матрица вырождена"],
            "обратная": ["Обратная есть: A·A⁻¹ = I"],
            "обратная-нет": ["Матрица вырождена: ранг 1 < 2 — обратной нет"],
            "решить": ["Совместна · ранг 2 · решений 2^0"],
            "решить-нет": ["Несовместна: b не выражается через столбцы A"],
            "ядро": ["Ранг 1 из 3 · размерность ядра 2"],
            "проверочная": ["Ранг 4 из 7 · строк итога 3"],
            "порождающая": ["Ранг 3 из 7 · строк итога 4"],
            "систематический": ["n = 4, k = 2 · столбцы переставлены (место ← исходный): 0←1, 1←2, 2←0"],
            "код": ["n = 7, k = 4, d = 3 · ошибок исправляет 1, обнаруживает 2 · перестановка не нужна",
                    "Спектр: перебор 2^4 кодовых слов"],
            "код-отказ": ["n = 42, k = 21 · перестановка не нужна",
                         "d и весовой спектр не считаются: перебор слов кода — при k ≤ 20, двойственного — при n − k ≤ 20 и n ≤ 1024; "
                         "здесь k = 21, n − k = 21"],
            "синдромы": ["Векторов 2 · нулевых синдромов (кодовых слов) 1"],
            "произведение": ["Итог 1 × 2"],
            "сумма": ["Итог 1 × 2"],
            "транспонирование": ["Итог 3 × 1"],
            "поток": ["Из массива: 9 строк по 4 бит с бита 7 — ранг 2 < 4: между битами строки 2 линейных проверок — похоже на "
                      "линейный блоковый код (4, 2) или постоянные биты; проверки — ядро (H)",
                      "Ранг 2 — неполный: меньше 4 · опорные столбцы 0, 1"],
            "опорных-много": ["Ранг 30 — полный · опорные столбцы " + ", ".join(map(str, range(24))) + ", …"],
            "опорных-24": ["Ранг 24 — полный · опорные столбцы " + ", ".join(map(str, range(24)))],
            "код-полный": ["n = 3, k = 3, d = 1 · ошибок исправляет 0, обнаруживает 0 · перестановка не нужна",
                          "Спектр: перебор 2^3 кодовых слов"],
        }, сводки)
        # Систематический вид без перестановки у «кода» и с ней — строки выше; итог без матрицы — «0» столбцов.
        [пусто] = self.выполнить([{"что": "сводка", "d": {"операция": "транспонирование", "размер": [1, 1], "матрица": []}}])
        self.assertEqual(["Итог 0 × 0"], пусто)

    def test_матрицы_итога(self):
        и = self.итоги_сервера()
        имена = list(и)
        м_ = dict(zip(имена, self.выполнить([{"что": "матрицы", "d": и[к]} for к in имена]), strict=True))
        self.assertEqual([["Ступенчатый вид", ["101", "011", "000"], [0, 1]]], м_["гаусс"])
        self.assertEqual([["Приведённый ступенчатый вид", ["101", "011", "000"], [0, 1]]], м_["жордан"])
        self.assertEqual(["P — перестановка строк", "L — нижняя треугольная", "U — ступенчатая"], [x[0] for x in м_["разложение"]])
        self.assertEqual([и["разложение"][к] for к in "PLU"], [x[1] for x in м_["разложение"]])
        self.assertEqual([[], [], [0, 1]], [x[2] for x in м_["разложение"]])
        self.assertEqual([], м_["ранг"])
        self.assertEqual([], м_["определитель"])
        self.assertEqual([["A⁻¹", ["11", "01"], []]], м_["обратная"])
        self.assertEqual([], м_["обратная-нет"])
        self.assertEqual([["Частное решение x", ["01"], []], ["Базис решений A·x = 0", [], []]], м_["решить"])
        self.assertEqual([], м_["решить-нет"])
        self.assertEqual([["Базис ядра", и["ядро"]["матрица"], []]], м_["ядро"])
        self.assertEqual([["H — проверочная", и["проверочная"]["матрица"], []]], м_["проверочная"])
        self.assertEqual([["G — порождающая", и["порождающая"]["матрица"], []]], м_["порождающая"])
        с = и["систематический"]
        self.assertEqual([["G = [I | P]", с["G"], [0, 1]], ["H = [Pᵀ | I]", с["H"], [2, 3]],
                          ["H в исходном порядке столбцов", с["H_исходные"], []]], м_["систематический"])
        к = и["код"]
        self.assertEqual([["G = [I | P]", к["G"], [0, 1, 2, 3]], ["H = [Pᵀ | I]", к["H"], [4, 5, 6]]], м_["код"])
        self.assertEqual([["G = [I | P]", ["100", "010", "001"], [0, 1, 2]], ["H = [Pᵀ | I]", [], []]], м_["код-полный"])
        self.assertEqual([["Синдромы — по строке на вектор", ["000", "111"], []]], м_["синдромы"])
        self.assertEqual([["Итог", ["11"], []]], м_["произведение"])
        self.assertEqual([["Итог", ["01"], []]], м_["сумма"])
        self.assertEqual([["Итог", ["1", "1", "0"], []]], м_["транспонирование"])
        self.assertEqual([["A — кадры массива (первые 4 из 9)", и["поток"]["A"], []]], м_["поток"])
        # A — после итога: сначала то, что посчитано.
        [проверочная] = self.выполнить([{"что": "матрицы", "d": и["проверочная"] | {"A": ["1"]}}])
        self.assertEqual(["H — проверочная", "A — кадры массива (первые 1 из 4)"], [x[0] for x in проверочная])
        [полный] = self.выполнить([{"что": "матрицы", "d": и["поток"] | {"A": ["1100"] * 9}}])
        self.assertEqual("A — кадры массива", полный[0][0])

    def test_шаг_и_текст(self):
        и = self.итоги_сервера()
        шаги = и["жордан"]["шаги"]
        итоги = self.выполнить([{"что": "шаг", "ш": ш} for ш in шаги] + [
            {"что": "шаг", "ш": {"столбец": 4, "строка": 2, "обмен": 0, "прибавлена_к": [], "матрица": []}},
            {"что": "текст", "d": и["жордан"], "до": None},
            {"что": "текст", "d": и["код"], "до": 2},
            {"что": "текст", "d": и["поток"], "до": 3},
            {"что": "текст", "d": и["ранг"], "до": 3},
            {"что": "текст", "d": и["систематический"], "до": 0},
            {"что": "текст", "d": и["гаусс"], "до": 3},
            {"что": "перестановка", "п": [0, 1, 2]},
            {"что": "перестановка", "п": list(range(1, 14)) + [0, 14]},
            {"что": "перестановка", "п": list(range(1, 12)) + [0, 12]},
        ])
        self.assertEqual("столбец 0: опорная строка 0 (переставлены строки 0 и 1); прибавлена к строкам 2", итоги[0])
        self.assertEqual("столбец 1: опорная строка 1; прибавлена к строкам 2", итоги[1])
        self.assertEqual("столбец 4: опорная строка 2 (переставлены строки 2 и 0); прибавлять не к чему", итоги[2])
        self.assertEqual("\n".join([
            "A: 3 × 3 · ранг 2 · опорные столбцы 0, 1", "", "Приведённый ступенчатый вид (3 × 3):", "101", "011", "000", "",
            "Ход метода:", "1. " + итоги[0], "2. " + итоги[1]]), итоги[3])
        к = и["код"]
        self.assertEqual("\n".join([
            "n = 7, k = 4, d = 3 · ошибок исправляет 1, обнаруживает 2 · перестановка не нужна", "Спектр: перебор 2^4 кодовых слов", "",
            "G = [I | P] (4 × 7):", к["G"][0], к["G"][1], "… ещё 2 строк", "", "H = [Pᵀ | I] (3 × 7):", к["H"][0], к["H"][1], "… ещё 1 строк", "",
            "Весовой спектр (вес: слов): 0: 1, 3: 7, 4: 7, 7: 1"]), итоги[4])
        self.assertEqual("\n".join(итоги[5].split("\n")[:5]), "\n".join([
            "Из массива: 9 строк по 4 бит с бита 7 — " + и["поток"]["поток"]["вывод"],
            "Ранг 2 — неполный: меньше 4 · опорные столбцы 0, 1", "", "A — кадры массива (первые 4 из 9) (4 × 4):", "1100"]))
        self.assertEqual("… ещё 1 строк", итоги[5].split("\n")[-1])
        self.assertEqual("Ранг 2 — полный · опорные столбцы 0, 1", итоги[6])
        с = и["систематический"]
        self.assertEqual("\n".join([
            "n = 4, k = 2 · столбцы переставлены (место ← исходный): 0←1, 1←2, 2←0", "", "G = [I | P] (2 × 4):", "… ещё 2 строк", "",
            "H = [Pᵀ | I] (2 × 4):", "… ещё 2 строк", "", "H в исходном порядке столбцов (2 × 4):", "… ещё 2 строк", "",
            "Перестановка столбцов (i-й — исходный): 1 2 0 3"]), итоги[7])
        self.assertEqual(4, len(с["перестановка"]))
        self.assertEqual("", итоги[9])
        self.assertTrue(итоги[8].startswith("A: 3 × 3 · ранг 2 · опорные столбцы 0, 1\n\nСтупенчатый вид (3 × 3):\n101\n011\n000\n\nХод метода:"))
        self.assertEqual("0←1, 1←2, 2←3, 3←4, 4←5, 5←6, 6←7, 7←8, 8←9, 9←10, 10←11, 11←12, …", итоги[10])
        self.assertEqual("0←1, 1←2, 2←3, 3←4, 4←5, 5←6, 6←7, 7←8, 8←9, 9←10, 10←11, 11←0", итоги[11])


if __name__ == "__main__":
    unittest.main()
