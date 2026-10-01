"""Разметка вслепую, своя разметка, общие плоскости (APSK, крест), вход I/Q — против независимых эталонов.

Эталоны свои: модулятор со случайной перестановкой меток (метка → точка через
строки бит, не через таблицы модуля), регистр сдвига ПСП и Берлекэмп — Мэсси на
чистом Python, кадры с чётными строками, облако I/Q с шумом, поворотом и
инверсией (комплексные числа), WAV — через модуль ``wave`` и вручную. Мера
совпадений окон сверяется с прямым подсчётом окон по потоку.
"""

import cmath
import io
import itertools
import json
import math
import os
import shutil
import struct
import subprocess
import time
import unittest
import unittest.mock
import wave
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import iq, modem, ploskost
from reportgen.potok import moddekoder as мд
from reportgen.potok import razmetka as р
from test_potok_sessii import функции_js
from test_potok_tpc3 import comtech

ОБРАЗЕЦ = Path(os.environ.get("K2964_ОБРАЗЕЦ", "/tmp/claude-0/-home-user-poiujnbhy/da6b639e-7ecd-5b07-9c36-bf7a3fd312ff/"
                                              "scratchpad/250_V_8085_8PSK_7556_K2964.bit"))


# -- независимые расчёты ---------------------------------------------------------------------

def метки_в_биты(метки, k: int) -> np.ndarray:
    """Метки → биты строками формата (старший бит первым)."""
    return np.array([int(ч) for м in метки for ч in format(int(м), f"0{k}b")], dtype=np.uint8)


def передать(биты: np.ndarray, k: int, таблица) -> np.ndarray:
    """Передатчик: истинная метка t (k бит строкой) → принятая таблица[t]."""
    итог = []
    for i in range(len(биты) // k):
        t = int("".join(map(str, биты[i * k:(i + 1) * k])), 2)
        итог.append(int(таблица[t]))
    return метки_в_биты(итог, k)


def обратная(таблица) -> list[int]:
    итог = [0] * len(таблица)
    for i, t in enumerate(таблица):
        итог[int(t)] = i
    return итог


def лрп(n: int, отводы=(23, 18), сид: int = 1) -> np.ndarray:
    """Регистр Фибоначчи: выход — последний разряд, обратная связь — сумма разрядов «отводы»."""
    регистр = [(сид >> i) & 1 for i in range(max(отводы))] or [1]
    итог = []
    for _ in range(n):
        итог.append(регистр[-1])
        бит = 0
        for о in отводы:
            бит ^= регистр[о - 1]
        регистр = [бит] + регистр[:-1]
    return np.array(итог, dtype=np.uint8)


def берлекэмп(s) -> int:
    """Линейная сложность — Берлекэмп — Мэсси на списках."""
    n = len(s)
    C, B, L, m = [1] + [0] * n, [1] + [0] * n, 0, 1
    for N in range(n):
        d = s[N]
        for i in range(1, L + 1):
            d ^= C[i] & s[N - i]
        if not d:
            m += 1
            continue
        T = C[:]
        for i in range(m, n + 1):
            C[i] ^= B[i - m]
        if 2 * L <= N:
            L, B, m = N + 1 - L, T, 1
        else:
            m += 1
    return L


def hdlc_биты(пакетов=60, сид=2, k=3) -> np.ndarray:
    кадры = [с.ethernet(п) for п in с.пакеты_ip(пакетов, сид=сид)]
    x = np.unpackbits(np.frombuffer(с.hdlc(кадры, флагов_между=20), np.uint8))
    return x[:len(x) // k * k]


СИНХРО24 = np.array([int(ч) for ч in "111010110010000011010111"], dtype=np.uint8)


def кадры_строк(кадров=160, *, строк=9, длина_строки=64, сид=3, ошибок=0.0, синхро=СИНХРО24) -> tuple[np.ndarray, int]:
    """Кадр: синхрослово и строки с чётной суммой бит (код проверки чётности строки). (поток, длина кадра)."""
    rng = np.random.default_rng(сид)
    данные = rng.integers(0, 2, (кадров, строк, длина_строки - 1)).astype(np.uint8)
    строки_ = np.concatenate([данные, данные.sum(axis=2, keepdims=True) % 2], axis=2)
    кадры = np.concatenate([np.tile(синхро, (кадров, 1)), строки_.reshape(кадров, -1)], axis=1)
    поток = кадры.ravel() ^ (rng.random(кадры.size) < ошибок).astype(np.uint8)
    return поток, кадры.shape[1]


def чётных_строк(биты: np.ndarray, длина_кадра: int, начало: int = 24, длина_строки: int = 64) -> float:
    """Доля строк с постоянной (у места строки в кадре) чётностью: у верной разметки и её инверсий бит метки — все.

    Инверсия бит метки меняет чётность строки на одну и ту же величину во всех кадрах.
    """
    кадры = биты[:len(биты) // длина_кадра * длина_кадра].reshape(-1, длина_кадра)[:, начало:]
    строки_ = кадры[:, :кадры.shape[1] // длина_строки * длина_строки].reshape(len(кадры), -1, длина_строки)
    чёт = строки_.sum(axis=2) % 2                                  # кадры × места
    большинство = (чёт.mean(axis=0) >= 0.5).astype(np.uint8)
    return float((чёт == большинство).mean())


def окна_прямо(биты: np.ndarray, k: int) -> float:
    """Σp² 8-битовых окон со всех сдвигов внутри символа — прямым проходом по потоку (среднее по сдвигам)."""
    итог = []
    n_симв = len(биты) // k
    for r in range(k):
        T = -(-(r + 8) // k)
        n = n_симв - T
        if n <= 0:
            continue
        окна = [int("".join(map(str, биты[k * i + r:k * i + r + 8])), 2) for i in range(n)]
        h = np.bincount(окна, minlength=256)
        итог.append(256 * (h.astype(float) ** 2).sum() / n ** 2 - 1)
    return float(np.mean(итог)) if итог else 0.0


# -- (а) анализ символов ------------------------------------------------------------------------

class АнализTests(unittest.TestCase):
    def test_период_и_синхрослово_при_любой_разметке(self):
        поток, длина = кадры_строк(80)
        self.assertEqual(600, длина)
        π = np.random.default_rng(1).permutation(8)
        принятые = передать(поток, 3, π)
        а = р.анализ(np.concatenate([[1], принятые]), 3)        # один лишний бит в начале: граница символа — бит 1
        self.assertEqual(1, а["фаза"])
        self.assertEqual(200, а["кадр"])
        ждём = [int(π[int("".join(map(str, СИНХРО24[i:i + 3])), 2)]) for i in range(0, 24, 3)]
        self.assertEqual([{"начало": 0, "длина": 8, "символы": ждём, "доля": 1.0}], а["места"])
        # Частоты и переходы — прямым счётом.
        символы = [int("".join(map(str, принятые[i:i + 3])), 2) for i in range(0, len(принятые) - 2, 3)]
        self.assertEqual(len(символы), а["символов"])
        for v in range(8):
            self.assertAlmostEqual(символы.count(v) / len(символы), а["частоты"][v], places=4)
        пары = list(zip(символы[:-1], символы[1:], strict=True))
        self.assertEqual([[пары.count((i, j)) for j in range(8)] for i in range(8)], а["переходы"])
        self.assertAlmostEqual(sum(1 for i, j in пары if i == j) / len(пары), а["повторов_подряд"], places=4)
        self.assertEqual(sorted(((пары.count(п), п) for п in set(пары)), reverse=True)[0][0], а["частые_переходы"][0][2])

    def test_кадр_кратный_и_чередование(self):
        # Синхрослово чередуется через кадр; данные повторяются через 4 кадра — период кратен кадру.
        rng = np.random.default_rng(4)
        данные = rng.integers(0, 8, (4, 60))
        кадры = []
        for f in range(64):
            кадры.append(np.concatenate([[1, 2, 3, 4] if f % 2 else [5, 6, 7, 0], данные[f % 4]]))
        символы = np.concatenate(кадры)
        а = р.анализ(метки_в_биты(символы, 3), 3, фаза=0)
        self.assertEqual(64, а["кадр"])
        self.assertEqual(128, а["цикл"])
        место = а["места"][0]
        self.assertEqual((0, 4, True), (место["начало"], место["длина"], место.get("чередуется")))
        self.assertEqual({(5, 6, 7, 0), (1, 2, 3, 4)}, {tuple(место["символы"]), tuple(место["символы_нечётных"])})

    def test_без_периода(self):
        символы = np.random.default_rng(2).integers(0, 8, 50000)
        self.assertIsNone(р.период(символы, 8))
        а = р.анализ(метки_в_биты(символы, 3), 3, фаза=0)
        self.assertIsNone(а["период"])
        self.assertNotIn("места", а)
        self.assertIsNone(р.период(np.zeros(1000, np.int64), 8))                   # один символ: нечего сравнивать
        self.assertIsNone(р.период(np.arange(12) % 8, 8))                          # коротко
        self.assertEqual([], р.постоянные_места(np.arange(9), 3))                   # меньше 4 кадров

    def test_делитель_периода(self):
        # Повтор через 3 кадра по 40 символов, синхрослово в каждом кадре — кадр 40, а не 120.
        rng = np.random.default_rng(6)
        данные = rng.integers(0, 4, (3, 36))
        символы = np.concatenate([np.concatenate([[3, 0, 1, 2], данные[f % 3]]) for f in range(300)])
        п = р.период(символы, 4)
        self.assertIn(п["символов"], (40, 120))
        а = р.анализ(метки_в_биты(символы, 2), 2, фаза=0)
        self.assertEqual(40, а["кадр"])


# -- (б) по синхрослову -------------------------------------------------------------------------

class СинхрословоTests(unittest.TestCase):
    def test_соответствие_при_сдвиге_и_инверсии(self):
        π = np.random.default_rng(3).permutation(8)
        слово = np.array([int(ч) for ч in "1011000111010010110"], dtype=np.uint8)       # 19 бит
        for сдвиг, инверсия in ((0, False), (1, False), (2, True)):
            with self.subTest(сдвиг=сдвиг, инверсия=инверсия):
                биты = np.concatenate([np.zeros(сдвиг, np.uint8), слово ^ инверсия, np.zeros(9, np.uint8)])
                биты = биты[:len(биты) // 3 * 3]
                истинные = [int("".join(map(str, биты[i:i + 3])), 2) for i in range(0, len(биты), 3)]
                принятые = [int(π[t]) for t in истинные]
                варианты = р.по_синхрослову(слово, принятые, 3)
                ждём = {π[t]: t for i, t in enumerate(истинные) if 3 * i >= сдвиг and 3 * i + 3 <= сдвиг + 19}
                # Инверсия всего слова — это инверсия всех бит меток (x ⊕ 111): сходится всегда, с тем же покрытием.
                прямой, инверсный = варианты[0], next(в for в in варианты if в["инверсия"] != варианты[0]["инверсия"])
                self.assertFalse(прямой["инверсия"])
                self.assertEqual((прямой["сдвиг"], прямой["покрыто"]), (инверсный["сдвиг"], инверсный["покрыто"]))
                self.assertEqual({v: t ^ 7 for v, t in прямой["соответствие"].items()}, инверсный["соответствие"])
                лучший = инверсный if инверсия else прямой
                self.assertEqual(сдвиг, лучший["сдвиг"])
                self.assertEqual({int(a): б for a, б in ждём.items()}, лучший["соответствие"])
                self.assertEqual(len(ждём), лучший["покрыто"])
                self.assertEqual(sum(1 for i in range(len(истинные)) if 3 * i >= сдвиг and 3 * i + 3 <= сдвиг + 19),
                                 лучший["пар"])

    def test_противоречие_отбрасывается(self):
        # Слово «000 000» против принятых 1, 2 — одна истинная метка у двух принятых: не годится.
        self.assertEqual([], [в for в in р.по_синхрослову([0] * 6, [1, 2], 3, инверсия=False) if в["сдвиг"] == 0])
        # «000 111» против 5, 5 — одна принятая у двух истинных: тоже.
        self.assertEqual([], [в for в in р.по_синхрослову([0, 0, 0, 1, 1, 1], [5, 5], 3, инверсия=False)
                              if в["сдвиг"] == 0])
        # Без инверсии — только прямые варианты.
        self.assertTrue(all(not в["инверсия"] for в in р.по_синхрослову([1, 0, 1], [3], 3, инверсия=False)))

    def test_дополнения(self):
        таблицы = р.дополнения({0: 5, 1: 2, 7: 0}, 8)
        self.assertEqual(math.factorial(5), len(таблицы))
        self.assertTrue(all(sorted(т) == list(range(8)) and т[0] == 5 and т[1] == 2 and т[7] == 0 for т in таблицы.tolist()))
        self.assertEqual(len({tuple(т) for т in таблицы.tolist()}), len(таблицы))
        self.assertEqual([[1, 0]], р.дополнения({0: 1}, 2).tolist())
        with self.assertRaises(ValueError) as о:
            р.дополнения({0: 0, 1: 1}, 16)
        self.assertIn("остальные 14!", str(о.exception))
        self.assertEqual(40320, len(р.дополнения({}, 8)))


# -- меры ---------------------------------------------------------------------------------------

class МерыTests(unittest.TestCase):
    def test_сложность_как_у_берлекэмпа(self):
        rng = np.random.default_rng(1)
        ряды = rng.integers(0, 2, (40, 90)).astype(np.uint8)
        ряды[0] = лрп(90, (15, 14))
        ряды[1] = 0
        ряды[2] = 1
        ряды[3, :] = 0
        ряды[3, 50] = 1
        self.assertEqual([берлекэмп(list(р_)) for р_ in ряды], р.МераЛРП.сложность(ряды).tolist())
        self.assertEqual(15, р.МераЛРП.сложность(ряды[:1])[0])

    def test_мера_лрп_по_отрезкам(self):
        x = лрп(3000, (7, 6))
        м = р.МераЛРП(р.в_символы(x, 3), 3, отрезков=3, длина=60)
        self.assertEqual(60, м.длина)
        self.assertEqual(3, len(м.отрезки))
        тожд = np.arange(8)[None, :]
        self.assertAlmostEqual((30 - 7) / math.sqrt(15), м.оценить(тожд)[0])
        # Ошибка в первом отрезке — медиана по отрезкам та же.
        x[5] ^= 1
        м = р.МераЛРП(р.в_символы(x, 3), 3, отрезков=3, длина=60)
        self.assertAlmostEqual((30 - 7) / math.sqrt(15), м.оценить(тожд)[0])

    def test_байтовая_мера_как_прямой_подсчёт(self):
        rng = np.random.default_rng(2)
        for k, n in ((3, 900), (4, 700), (5, 500), (2, 800), (1, 300), (8, 300), (3, 4), (3, 3)):
            M = 1 << k
            символы = rng.integers(0, M, n)
            if n >= 300:
                символы[100:300] = np.tile(rng.integers(0, M, 5), 40)      # повторы — чтобы мера была не нулевой
            м = р.МераБайт(символы, k)
            таблицы = np.array([rng.permutation(M) for _ in range(3)] + [np.arange(M)])
            with self.subTest(k=k):
                ждём = [окна_прямо(метки_в_биты(т[символы], k), k) for т in таблицы]
                np.testing.assert_allclose(ждём, м.оценить(таблицы), rtol=1e-9, atol=1e-12)
                # Инверсия бит метки не меняет меру.
                np.testing.assert_allclose(м.оценить(таблицы), м.оценить(таблицы ^ (M - 1)), rtol=1e-12)
        # ФМ-64: сдвиги с окном через три символа пропускаются (кусков больше предела).
        м = р.МераБайт(rng.integers(0, 64, 400), 6)
        self.assertEqual(5, len(м.части))

    def test_мера_строк_точная(self):
        поток, длина = кадры_строк(80, ошибок=0.0)
        S = р.символы_кадров(поток, list(range(0, len(поток) - длина + 1, длина)), длина, 3, 0)
        self.assertIsNotNone(S)
        кадры, сдвиг = S
        self.assertEqual(0, сдвиг)
        мера = р.МераСтрок(кадры, 3)
        тожд = np.arange(8)[None, :]
        # Все строки чётные: перекос 1 у 9 мест из 9 (строки начинаются с бита 24).
        F = len(кадры)
        self.assertAlmostEqual(9 * (1 - 1 / F) * F / math.sqrt(2 * 9), мера.оценить(тожд, 64, 24)[0])
        # Чётности окон — как прямой подсчёт по битам.
        a = np.array([24, 25, 26, 100, 301])
        e = a + np.array([64, 63, 10, 50, 64])
        т = np.random.default_rng(5).permutation(8)
        биты = р.в_биты(т[кадры], 3).reshape(F, -1)
        ждём = np.array([[биты[f, a_:e_].sum() % 2 for a_, e_ in zip(a, e, strict=True)] for f in range(F)])
        np.testing.assert_array_equal(ждём, мера.чётности(т[None, :], a, e)[0])
        # Постоянные места (синхрослово) — окна через них не считаются.
        self.assertEqual(8, int(мера.постоянных[-1]))
        self.assertEqual([False, True, True], мера._переменные(np.array([0, 24, 23]), np.array([20, 44, 30])).tolist()[:1]
                         + мера._переменные(np.array([24]), np.array([44])).tolist()
                         + мера._переменные(np.array([24]), np.array([600])).tolist())
        self.assertFalse(мера._переменные(np.array([23]), np.array([30]))[0])      # бит 23 — в символе синхрослова
        # Все длины: лучшая — 64 с бита 24 (или кратная).
        мера_, L, o = мера.оценить_все_длины(тожд)[0]
        self.assertEqual((64, 24), (L, o))
        self.assertGreater(мера_, 50)

    def test_символы_кадров(self):
        поток, длина = кадры_строк(40)
        начала = list(range(0, len(поток) - длина + 1, длина))
        self.assertIsNone(р.символы_кадров(поток, начала, 599, 3, 0))           # длина не кратна k
        self.assertIsNone(р.символы_кадров(поток, начала[:5], длина, 3, 0))      # мало кадров
        self.assertIsNone(р.символы_кадров(поток, [], длина, 3, 0))
        кадры, сдвиг = р.символы_кадров(np.concatenate([[0, 1], поток]), [н + 2 for н in начала], длина, 3, 0)
        self.assertEqual(1, сдвиг)                        # граница символа — бит 0 файла: в кадре с бита 1
        self.assertEqual((39, 200), кадры.shape)          # Q = 199 + 1 (запас до границы); последний кадр не влез
        # Повторяющиеся кадры (заполнение) не берутся.
        повтор = np.tile(поток[:длина], 20)
        self.assertIsNone(р.символы_кадров(повтор, list(range(0, len(повтор) - длина + 1, длина)), длина, 3, 0))


# -- поиск ----------------------------------------------------------------------------------------

class ПоискTests(unittest.TestCase):
    def test_лрп_находит_перестановку(self):
        π = np.random.default_rng(5).permutation(8).tolist()
        x = лрп(9000, (23, 18))[:8997]
        ит = р.искать(передать(x, 3, π), 3, мера="лрп", срок=120)
        self.assertIsNotNone(ит)
        self.assertEqual("линейная сложность (ЛРП)", ит.как)
        self.assertEqual(23, ит.сведения["сложность"])
        self.assertIn(обратная(π), ит.равноценные)
        # Лучшая — ПСП той же сложности (у этой ПСП есть и другие разметки с тем же регистром).
        self.assertEqual(23, берлекэмп(list(р.применить(передать(x, 3, π), 3, ит.таблица)[:300])))
        for т in ит.равноценные:
            self.assertLessEqual(берлекэмп(list(р.применить(передать(x, 3, π), 3, т)[:300])), 23 + 3)

    def test_байты_находят_перестановку_hdlc(self):
        π = np.random.default_rng(5).permutation(8).tolist()
        x = hdlc_биты()
        ит = р.искать(передать(x, 3, π), 3, мера="байты", срок=120)
        self.assertIsNotNone(ит)
        self.assertIn(обратная(π), ит.равноценные)
        # Равноценная — инверсия всех бит (HDLC ищется и в инверсном потоке).
        self.assertTrue(all(т in (обратная(π), [v ^ 7 for v in обратная(π)]) for т in ит.равноценные))

    def test_авто_без_структуры(self):
        x = np.random.default_rng(7).integers(0, 2, 30000).astype(np.uint8)
        self.assertIsNone(р.искать(x, 3, мера="авто", срок=60))
        with self.assertRaises(ValueError):
            р.искать(x, 3, мера="строки")
        with self.assertRaises(ValueError):
            р.искать(x, 11)                                                   # больше 10 бит (КАМ-1024)
        ит = р.искать(x, 1)
        self.assertEqual([[0, 1], [1, 0]], ит.равноценные)
        self.assertIsNone(р.искать(x[:150], 3, мера="лрп"))                   # короче отрезка ЛРП

    def test_строки_в_кадрах_фм8_и_фм4(self):
        for k in (3, 2):
            M = 1 << k
            π = np.random.default_rng(11).permutation(M).tolist()
            поток, длина = кадры_строк(160, ошибок=1e-3)
            принятые = передать(np.concatenate([np.zeros(30, np.uint8), поток]), k, π)
            начала = list(range(30, len(принятые) - длина + 1, длина))
            with self.subTest(k=k):
                ит = р.искать(принятые, k, мера="строки", кадры=(начала, длина), срок=120)
                self.assertIsNotNone(ит)
                self.assertEqual((64, 24), (ит.сведения["строка"], ит.сведения["начало_строки"] % 64))
                self.assertIn(обратная(π), ит.равноценные)
                # Все инверсии бит метки — среди равноценных (мера строк их не различает).
                self.assertTrue(set(map(tuple, р.равноценные(обратная(π), M))) <= set(map(tuple, ит.равноценные)))
                # У любой равноценной строки чётны (инверсия бит метки меняет чётность постоянно).
                снято = р.применить(принятые[30:], k, ит.таблица)
                self.assertGreater(чётных_строк(снято, длина), 0.9)

    def test_перебор_и_срок(self):
        class Счёт:
            имя = "счёт"

            def оценить(self, таблицы):
                time.sleep(0.02)
                return -np.abs(таблицы - np.arange(len(таблицы[0]))).sum(axis=1).astype(float)

        р_ = р.перебор(Счёт(), 8, лучших=3, срок=0.01)
        self.assertEqual(р.ПАЧКА, р_["оценено"])               # срок кончился после первой пачки
        self.assertEqual(40320, р_["всего"])
        р_ = р.перебор(Счёт(), 4, лучших=2)
        self.assertEqual([[0, 1, 2, 3], [0, 1, 3, 2]], р_["таблицы"])
        self.assertEqual(24, р_["оценено"])
        ходы = []
        р.перебор(Счёт(), 4, ход=lambda д, т: ходы.append((д, т)))
        self.assertEqual([(1.0, "счёт: 24 из 24")], ходы)
        with self.assertRaises(ValueError):
            р.все_перестановки(16)

    def test_местный_поиск_и_соседи(self):
        M = 16
        цель = np.random.default_rng(3).permutation(M)
        с_ = р.соседи(np.arange(M))
        self.assertEqual(M * (M - 1) // 2, len(с_))
        self.assertTrue(all((т != np.arange(M)).sum() == 2 and sorted(т) == list(range(M)) for т in с_))

        def оценить(таблицы):
            return (np.asarray(таблицы) == цель).sum(axis=1).astype(float)

        старт = цель.copy()
        старт[[0, 1, 2, 3]] = старт[[1, 2, 3, 0]]
        ходы = []
        ит = р.местный_поиск(оценить, старт[None, :], срок=30, ход=lambda д, т: ходы.append(д))
        self.assertEqual(цель.tolist(), ит["таблица"])
        self.assertEqual(M, ит["мера"])
        self.assertEqual(12, ит["мера_стартов"])
        self.assertTrue(ходы and ходы[-1] <= 1.0)

    def test_старты_группы(self):
        основы = р.основы_плоскостей(3)
        self.assertIn(list(range(8)), основы)
        self.assertEqual(len(основы), len({tuple(о) for о in основы}))
        старты = р.старты_группы(3, основы)
        self.assertEqual(len(старты), len({tuple(т) for т in старты.tolist()}))
        self.assertTrue(all(sorted(т) == list(range(8)) for т in старты.tolist()))
        # Перестановки бит и инверсии тождественной — среди стартов.
        for п in itertools.permutations(range(3)):
            for c in range(8):
                т = [int("".join(format(v, "03b")[i] for i in п), 2) ^ c for v in range(8)]
                self.assertIn(т, старты.tolist())
        self.assertEqual(10, len(р.старты_группы(4, р.основы_плоскостей(4), до=10)))

    def test_фм16_изменённое_созвездие_без_кадров(self):
        """«Чутка изменённое» созвездие: Грей по кругу с обменом двух меток, на приёме — поворот на 3 шага."""
        x = hdlc_биты(k=4)
        грей = [n ^ (n >> 1) for n in range(16)]
        истинная = грей.copy()
        истинная[2], истинная[9] = истинная[9], истинная[2]           # метка точки n у передатчика
        таблица = [грей[(истинная.index(t) + 3) % 16] for t in range(16)]  # истинная метка → принятая
        ит = р.искать(передать(x, 4, таблица), 4, мера="байты", срок=120)
        self.assertIsNotNone(ит)
        self.assertIn(обратная(таблица), ит.равноценные)
        self.assertEqual("местный поиск: байтовая структура ряда", ит.как)

    def test_фм16_строки_в_кадрах(self):
        # Строки 62 бит с бита 22: края строк режут символы (при кратных 4 видна лишь чётность метки).
        поток, длина = кадры_строк(96, длина_строки=62, синхро=СИНХРО24[:22])
        self.assertEqual(580, длина)
        грей = [n ^ (n >> 1) for n in range(16)]
        истинная = грей.copy()
        истинная[5], истинная[12] = истинная[12], истинная[5]
        таблица = [грей[(истинная.index(t) + 5) % 16] for t in range(16)]
        принятые = передать(поток, 4, таблица)
        ит = р.искать(принятые, 4, мера="строки", кадры=(list(range(0, len(принятые) - длина + 1, длина)), длина),
                      срок=120)
        self.assertIsNotNone(ит)
        self.assertEqual((62, 22), (ит.сведения["строка"], ит.сведения["начало_строки"]))
        self.assertIn(обратная(таблица), ит.равноценные)
        self.assertEqual(1.0, чётных_строк(р.применить(принятые, 4, ит.таблица), длина, 22, 62))


class ПоискиВФонеTests(unittest.TestCase):
    def test_ход_итог_остановка_и_чужой(self):
        п = р.Поиски()

        def задача(ход):
            for i in range(5):
                ход(i / 5, f"шаг {i}")
                time.sleep(0.01)
            return {"таблица": [1, 0]}

        ид = п.начать(1, задача, "проба")
        for _ in range(200):
            с_ = п.состояние(ид, 1)
            if с_["готово"]:
                break
            time.sleep(0.01)
        self.assertEqual({"таблица": [1, 0]}, с_["итог"])
        self.assertEqual(("проба", "", None), (с_["описание"], с_["ошибка"], с_["осталось"]))
        with self.assertRaises(KeyError):
            п.состояние(ид, 2)
        with self.assertRaises(KeyError):
            п.остановить(ид, 2)
        with self.assertRaises(KeyError):
            п.состояние("нет", 1)

        def долгая(ход):
            while True:
                ход(0.5, "идёт")
                time.sleep(0.01)

        ид = п.начать(1, долгая)
        time.sleep(0.05)
        с_ = п.состояние(ид, 1)
        self.assertFalse(с_["готово"])
        self.assertEqual(0.5, с_["доля"])
        self.assertAlmostEqual(с_["прошло"], с_["осталось"], delta=0.2)
        # Новый поиск того же человека останавливает прежний.
        ид2 = п.начать(1, задача)
        for _ in range(200):
            if п.состояние(ид, 1)["готово"]:
                break
            time.sleep(0.01)
        self.assertEqual("остановлено", п.состояние(ид, 1)["ошибка"])
        ид3 = п.начать(2, долгая)
        п.остановить(ид3, 2)
        for _ in range(200):
            if п.состояние(ид3, 2)["готово"]:
                break
            time.sleep(0.01)
        self.assertEqual("остановлено", п.состояние(ид3, 2)["ошибка"])
        self.assertIn(ид2, п._все)

        def сбой(ход):
            raise ValueError("мало данных")

        ид = п.начать(3, сбой)
        for _ in range(200):
            if п.состояние(ид, 3)["готово"]:
                break
            time.sleep(0.01)
        self.assertEqual("мало данных", п.состояние(ид, 3)["ошибка"])

    def test_уборка(self):
        п = р.Поиски()
        п.ХРАНИТЬ = 0.0
        ид = п.начать(1, lambda ход: 1)
        for _ in range(200):
            if п.состояние(ид, 1)["готово"]:
                break
            time.sleep(0.01)
        п.начать(1, lambda ход: 2)
        self.assertNotIn(ид, п._все)
        п = р.Поиски()
        п.ВСЕГО = 2
        иды = [п.начать(4, lambda ход: time.sleep(0.2)) for _ in range(3)]
        self.assertNotIn(иды[0], п._все)
        self.assertEqual(2, len(п._все))


# -- вход I/Q -------------------------------------------------------------------------------------

def облако_эталон(имя: str, n: int, *, поворот: float, инверсия: bool, шум: float, сид: int = 1):
    """Отсчёты: точка плоскости с меткой метки[i] (идеальные координаты), затем инверсия (сопряжение) и поворот."""
    п = мд.плоскость(мд.найти(имя))
    точки = {м: complex(x, y) for м, (x, y) in zip(п.метки, п.xy, strict=True)}
    rng = np.random.default_rng(сид)
    метки = rng.integers(0, len(п.метки), n)
    z = np.array([точки[int(м)] for м in метки])
    z = np.conj(z) if инверсия else z
    z = z * cmath.exp(1j * math.radians(поворот)) + шум * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return метки, z, п


def в_int16(z: np.ndarray, масштаб=8000.0, порядок="<") -> bytes:
    x = np.empty(2 * len(z))
    x[0::2], x[1::2] = z.real * масштаб, z.imag * масштаб
    return np.round(x).astype(порядок + "i2").tobytes()


class ВходIQTests(unittest.TestCase):
    def test_форматы(self):
        z = np.array([1 + 2j, -3 - 4j, 5 - 6j] * 8)
        x = np.empty(2 * len(z))
        x[0::2], x[1::2] = z.real, z.imag
        for формат, байты, ждём in (
                ("int8", x.astype("i1").tobytes(), z),
                ("uint8", (x + 127.5).astype("u1").tobytes(), (np.floor(x + 127.5) - 127.5)[0::2] + 1j * (np.floor(x + 127.5) - 127.5)[1::2]),
                ("int16", x.astype("<i2").tobytes(), z),
                ("float32", x.astype("<f4").tobytes(), z)):
            with self.subTest(формат):
                прочитано, описание = iq.прочитать(байты, формат)
                np.testing.assert_allclose(ждём, прочитано)
                self.assertIn(формат, описание)
        прочитано, описание = iq.прочитать(x.astype(">i2").tobytes(), "int16", порядок="старший")
        np.testing.assert_allclose(z, прочитано)
        self.assertEqual("int16, I и Q чередованием, старший байт первым", описание)
        прочитано, описание = iq.прочитать(b"\x00\x01" + x.astype("<f4").tobytes(), "float32", пропуск=2, каналы="QI")
        np.testing.assert_allclose(z.imag + 1j * z.real, прочитано)
        self.assertTrue(описание.endswith("; каналы Q, I"))
        # Хвост, не кратный паре отсчётов, отбрасывается.
        self.assertEqual(len(z), len(iq.прочитать(x.astype("i1").tobytes() + b"\x05", "int8")[0]))
        for байты, формат, ошибка in (
                (b"\x00" * 64, "авто", "формат отсчётов не определить по байтам: укажите int8, uint8, int16 или float32 (или WAV)"),
                (b"\x00" * 64, "wav", "не WAV: нет заголовка RIFF/WAVE"),
                (b"\x00" * 64, "int12", "формат отсчётов: int8, uint8, int16, float32 или WAV"),
                (b"\x00" * 20, "int8", "отсчётов 10: для облака нужно хотя бы 16")):
            with self.subTest(ошибка), self.assertRaises(ValueError) as о:
                iq.прочитать(байты, формат)
            self.assertEqual(ошибка, str(о.exception))
        with self.assertRaises(ValueError):
            iq.прочитать(b"\x00" * 64, "int16", порядок="средний")
        with self.assertRaises(ValueError):
            iq.прочитать(b"\x00" * 64, "int16", каналы="II")
        # Не конечные отсчёты (NaN) отбрасываются.
        y = x.astype("<f4")
        y[0] = np.nan
        self.assertEqual(len(z) - 1, len(iq.прочитать(y.tobytes(), "float32")[0]))

    def test_wav(self):
        z = np.array([1000 - 2000j, -3000 + 4000j] * 20)
        x = np.empty(2 * len(z), "<i2")
        x[0::2], x[1::2] = z.real, z.imag
        буфер = io.BytesIO()
        with wave.open(буфер, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(x.tobytes())
        прочитано, описание = iq.прочитать(буфер.getvalue())
        np.testing.assert_allclose(z, прочитано)
        self.assertEqual("WAV: 2 канала, 16 бит, PCM, 48000 Гц", описание)
        # 24 бита и 8 бит (без знака) — через модуль wave; float32 — заголовок вручную (формат 3, блок LIST перед data).
        for бит, данные, ждём in ((24, b"".join(int(v).to_bytes(3, "little", signed=True) for v in x), z),
                                  (8, (np.clip(x // 256, -128, 127) + 128).astype("u1").tobytes(),
                                   (np.clip(x // 256, -128, 127))[0::2] + 1j * (np.clip(x // 256, -128, 127))[1::2])):
            буфер = io.BytesIO()
            with wave.open(буфер, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(бит // 8)
                w.setframerate(8000)
                w.writeframes(данные)
            with self.subTest(бит=бит):
                np.testing.assert_allclose(ждём, iq.прочитать(буфер.getvalue(), "wav")[0])
        f = x.astype("<f4").tobytes()
        fmt = struct.pack("<HHIIHH", 3, 2, 1000, 8000, 8, 32)
        riff = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"LIST" + struct.pack("<I", 3) + b"abc\x00" + \
            b"data" + struct.pack("<I", len(f)) + f
        прочитано, описание = iq.прочитать(b"RIFF" + struct.pack("<I", len(riff)) + riff)
        np.testing.assert_allclose(z, прочитано)
        self.assertIn("32 бит, float", описание)
        # Один канал и неизвестный формат — отказ; нет data — отказ.
        моно = io.BytesIO()
        with wave.open(моно, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(x.tobytes())
        with self.assertRaises(ValueError) as о:
            iq.прочитать(моно.getvalue())
        self.assertEqual("WAV: каналов 1, а для I/Q нужно два", str(о.exception))
        fmt = struct.pack("<HHIIHH", 2, 2, 1000, 8000, 8, 4)
        riff = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 64) + b"\x00" * 64
        with self.assertRaises(ValueError) as о:
            iq.прочитать(b"RIFF" + struct.pack("<I", len(riff)) + riff)
        self.assertEqual("WAV: формат 2, 4 бит — поддерживаются PCM 8/16/24/32 и float32", str(о.exception))
        with self.assertRaises(ValueError) as о:
            iq.прочитать(b"RIFF\x04\x00\x00\x00WAVE")
        self.assertEqual("WAV без блока fmt или data", str(о.exception))
        # WAVE_FORMAT_EXTENSIBLE: подформат — первые два байта GUID.
        fmt = struct.pack("<HHIIHH", 0xFFFE, 2, 1000, 8000, 4, 16) + struct.pack("<HHI", 22, 16, 3) + struct.pack("<H", 1) + b"\x00" * 14
        данные = x.tobytes()
        riff = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(данные)) + данные
        np.testing.assert_allclose(z, iq.прочитать(b"RIFF" + struct.pack("<I", len(riff)) + riff)[0])

    def test_облако_поворот_и_решения(self):
        for имя, поворот, инверсия, шум in (("DVB-S2 8PSK", 17.0, False, 0.07), ("КАМ16 Грей", 33.0, True, 0.05),
                                             ("DVB-S2 16APSK (γ 3,15)", 5.0, False, 0.03), ("ФМ4 Грей", 71.0, True, 0.1)):
            метки, z, п = облако_эталон(имя, 12000, поворот=поворот, инверсия=инверсия, шум=шум)
            with self.subTest(имя):
                сырьё, _ = iq.прочитать(в_int16(z * 1.7 + (0.3 - 0.2j)), "int16")
                о = iq.облако(сырьё)
                self.assertEqual(len(п.метки), о["точек"])
                self.assertEqual([2, 4, 8, 16, 32, 64][:len(о["оценки"])], [x["точек"] for x in о["оценки"]])
                идеал = np.array([complex(x, y) for x, y in п.xy])
                в = iq.выровнять(о["центры"], о["веса"], идеал, шаг=п.шаг)
                # Поворот, снимающий канал: −φ по модулю шага симметрии. Инверсия у созвездий, симметричных
                # отражению (все эти), по облаку не отличима от поворота: берётся без инверсии, поворот — свой.
                self.assertFalse(в["инверсия"])
                ждём = (поворот if инверсия else -поворот) % п.шаг
                if not инверсия:
                    self.assertAlmostEqual(0.0, (в["поворот"] - ждём + п.шаг / 2) % п.шаг - п.шаг / 2, delta=0.6)
                self.assertLess(в["ошибка"], 0.05)
                # Решения по плоскости — метки с точностью до симметрии: отображение истинная → решённая — перестановка.
                решено = iq.по_плоскости(iq.повернуть((сырьё - о["середина"]) / о["масштаб"], в["поворот"], в["инверсия"]),
                                         идеал, np.array(п.метки))
                истинные = np.array(п.метки)[метки]
                пары = {}
                for t, d in zip(истинные.tolist(), решено.tolist(), strict=True):
                    пары.setdefault(t, []).append(d)
                отображение = {t: max(set(d), key=d.count) for t, d in пары.items()}
                self.assertEqual(len(п.метки), len(set(отображение.values())))
                совпало = np.mean([отображение[t] == d for t, d in zip(истинные.tolist(), решено.tolist(), strict=True)])
                self.assertGreater(совпало, 0.99)
                # По кластерам — тоже перестановка.
                кл = iq.по_кластерам(iq.повернуть((сырьё - о["середина"]) / о["масштаб"], в["поворот"], в["инверсия"]),
                                     iq.повернуть(о["центры"], в["поворот"], в["инверсия"]))
                self.assertEqual(set(range(len(п.метки))), set(кл.tolist()))

    def test_k_средних_и_отделимость(self):
        rng = np.random.default_rng(3)
        центры = np.array([0, 10, 10j, 10 + 10j])
        z = центры[rng.integers(0, 4, 4000)] + 0.3 * (rng.standard_normal(4000) + 1j * rng.standard_normal(4000))
        ц, м = iq.k_средних(z, 4)
        self.assertEqual(sorted(np.round(центры).tolist(), key=lambda c: (c.real, c.imag)),
                         sorted(np.round(ц).tolist(), key=lambda c: (c.real, c.imag)))
        self.assertTrue(np.array_equal(м, np.abs(z[:, None] - ц[None, :]).argmin(axis=1)))
        self.assertGreater(iq.отделимость(z, ц, м), 20)
        self.assertEqual(0.0, iq.отделимость(z, ц[:1], np.zeros(len(z), int)))
        with self.assertRaises(ValueError):
            iq.k_средних(z[:3], 4)
        # Порядок чтения: сверху вниз, слева направо.
        self.assertEqual([2, 3, 0, 1], iq.порядок_чтения(центры).tolist())
        K, оценки = iq.число_точек(z)
        self.assertEqual(4, K)
        self.assertEqual(max(оценки, key=lambda о: о["отделимость"])["точек"], 4)
        with self.assertRaises(ValueError):
            iq.облако(z, точек=3)
        with self.assertRaises(ValueError):
            iq.число_точек(z[:10])

    def test_iq_в_разметку_вслепую(self):
        """Облако 8PSK со своей разметкой модулятора: решения по кластерам — затем ЛРП находит разметку."""
        x = лрп(12000, (23, 18))[:11997]
        rng = np.random.default_rng(9)
        своя = rng.permutation(8)                                 # метка t → точка своя[t] по кругу
        углы = 2 * np.pi * своя[р.в_символы(x, 3)] / 8 + math.radians(12)
        z = np.exp(1j * углы) + 0.08 * (rng.standard_normal(len(углы)) + 1j * rng.standard_normal(len(углы)))
        сырьё, _ = iq.прочитать(в_int16(z), "int16")
        о = iq.облако(сырьё, точек=8)
        метки = iq.по_кластерам((сырьё - о["середина"]) / о["масштаб"], о["центры"])
        ит = р.искать(р.в_биты(метки, 3), 3, мера="лрп", срок=120)
        self.assertIsNotNone(ит)
        self.assertEqual(23, берлекэмп(list(р.применить(р.в_биты(метки, 3), 3, ит.таблица)[:400])))


# -- общие плоскости, своя разметка, .etl с координатами -----------------------------------------

class ОбщиеПлоскостиTests(unittest.TestCase):
    def test_координаты_в_etl(self):
        текст = ("# 16APSK своя, 2026\n; примечание 0101 не точка\n# вид: общее\n"
                 + "\n".join(f"{format(i, '04b')} = {r:.4f} @ {а:.2f}°" if i % 3 else f"{format(i, '04b')}: {r * math.cos(math.radians(а)):.6f}, "
                              f"{r * math.sin(math.radians(а)):.6f}"
                              for i, (r, а) in enumerate([(1.0, 45 + 90 * j) for j in range(4)] + [(2.6, 15 + 30 * j) for j in range(12)])))
        с_ = мд.разобрать_etl(текст, "apsk.etl")
        self.assertEqual((16, True, "общее"), (с_.M, с_.точные, с_.вид))
        п = мд.плоскость(с_)
        self.assertEqual("общее", п.вид)
        self.assertEqual(90.0, п.шаг)
        self.assertAlmostEqual(1.0, float(np.mean([x * x + y * y for x, y in п.xy])))
        # Выгрузка общей плоскости — строками координат; разбор выгрузки — те же точки по номерам.
        снова = мд.разобрать_etl(мд.в_etl(с_))
        np.testing.assert_allclose(np.array(п.xy), np.array(мд.плоскость(снова).xy), atol=1e-5)
        self.assertEqual(п.метки, мд.плоскость(снова).метки)
        # Точные координаты по окружности через равные углы — это ФМ (порядок по углу, как у картинки).
        фм = мд.разобрать_etl("\n".join(f"{format(v, '03b')} = 1 @ {45 * n + 10}" for n, v in enumerate([0, 1, 3, 2, 6, 7, 5, 4])))
        self.assertEqual(("ФМ", (0, 1, 3, 2, 6, 7, 5, 4)), (мд.плоскость(фм).вид, мд.плоскость(фм).метки))
        # Сетка координатами — КАМ; неравные углы — общее.
        кам = мд.разобрать_etl("\n".join(f"{format(4 * i + q, '04b')} = {2 * i - 3} {2 * q - 3}" for i in range(4) for q in range(4)))
        self.assertEqual("КАМ", мд.плоскость(кам).вид)
        неравно = мд.разобрать_etl("00 = 1 0\n01 = 0 1\n10 = -1 0\n11 = 0.6 -0.8")
        self.assertEqual("общее", мд.плоскость(неравно).вид)
        self.assertEqual(360.0, мд.плоскость(неравно).шаг)
        # Картинка APSK: точки на одном луче — только по «# вид: общее».
        картинка = "  01  11\n\n  00  10\n"
        # Внутренняя и внешняя точки на одном луче (кольца 4 + 4): у картинки ФМ порядок по углу не определён.
        кольца = "000         001\n   010   011\n\n   110   111\n100         101\n"
        self.assertEqual("ФМ", мд.плоскость(мд.разобрать_etl(картинка)).вид)
        self.assertEqual("общее", мд.плоскость(мд.разобрать_etl("# вид: общее\n" + кольца)).вид)
        with self.assertRaises(ValueError):
            мд.разобрать_etl(кольца)

    def test_ошибки_координат(self):
        for текст, ждём in (
                ("00 = 1 0\n01 = 0 1\n10 = -1 0\n11 0 -1", "строка 4: «11 0 -1» — ждём «метка = x y» (или «метка = r @ угол»)"),
                ("# вид: круглый\n00 01\n10 11", "вид «круглый»: ФМ, КАМ или общее"),
                ("# вид: КАМ\n000 = 1 0\n001 = 0 1\n010 = -1 0\n011 = 0 -1\n100 = 2 0\n101 = 0 2\n110 = -2 0\n111 = 0 -2",
                 "вид «КАМ», а точки не заполняют сетку: различных столбцов 5, строк 5, точек 8"),
                ("# вид: общее\n00 = 1 0\n01 = 1 0\n10 = -1 0\n11 = 0 -1", "точки «00» и «01» совпадают"),
                ("# вид: общее\n00 = 1 1\n01 = 1 1\n10 = 1 1\n11 = 1 1", "все точки созвездия совпадают"),
                ("00 = inf 0\n01 = 0 1\n10 = -1 0\n11 = 0 -1", "строка 1: «00 = inf 0» — ждём «метка = x y» (или «метка = r @ угол»)")):
            with self.subTest(ждём), self.assertRaises(ValueError) as о:
                мд.разобрать_etl(текст)
            self.assertEqual(ждём, str(о.exception))
        # Десятичная запятая и знак «∠» полярной записи.
        с_ = мд.разобрать_etl("0 = 1,5 ∠ 0\n1 = 1,5 ∠ 180")
        self.assertEqual([("0", 1.5, 0.0), ("1", -1.5, 1.5 * math.sin(math.pi))], [(м, round(x, 9), y) for м, x, y in с_.точки])

    def test_встроенные_общие(self):
        # 16APSK и 32APSK — кольца 4+12 и 4+12+16 (радиусы из таблиц GNU Radio), шаг 90°.
        for имя, кольца in (("DVB-S2 16APSK (γ 3,15)", [4, 12]), ("DVB-S2 32APSK (γ 2,84; 5,27)", [4, 12, 16])):
            с_ = мд.найти(имя)
            радиусы = np.round([math.hypot(x, y) for _, x, y in с_.точки], 6)
            self.assertEqual(кольца, [int((радиусы == r).sum()) for r in sorted(set(радиусы))])
            self.assertEqual(("общее", 90.0), (мд.плоскость(с_).вид, мд.плоскость(с_).шаг))
        с_ = мд.найти("DVB-S2 16APSK (γ 3,15)")
        точки = {м: complex(x, y) for м, x, y in с_.точки}
        # m_16apsk[12] = r1·e^{iπ/4}, m_16apsk[4] = r2·e^{iπ/12}, γ = 3,15.
        self.assertAlmostEqual(cmath.exp(1j * math.pi / 4), точки["1100"])
        self.assertAlmostEqual(3.15 * cmath.exp(1j * math.pi / 12), точки["0100"])
        # Кресты: 32 и 128 точек без углов квадрата, симметричны; метки — по порядку чтения.
        for имя, сторона, срез in (("КАМ32 крест", 6, 1), ("КАМ128 крест", 12, 2)):
            с_ = мд.найти(имя)
            места = {(int(x), int(y)) for _, x, y in с_.точки}
            ждём = {(2 * i - сторона + 1, 2 * j - сторона + 1) for i in range(сторона) for j in range(сторона)
                    if not ((i < срез or i >= сторона - срез) and (j < срез or j >= сторона - срез))}
            self.assertEqual(ждём, места)
            self.assertEqual(list(range(с_.M)), [int(м, 2) for м, _, _ in с_.точки])
            self.assertEqual(90.0, мд.плоскость(с_).шаг)
        # КАМ64 и КАМ256: Грей уровня по каждой оси, соседи по сетке отличаются одним битом.
        for имя in ("КАМ64 Грей", "КАМ256 Грей", "КАМ8 прямоугольная Грей"):
            п = мд.плоскость(мд.найти(имя))
            self.assertEqual("КАМ", п.вид)
            self.assertTrue(all(bin(п.метки[a] ^ п.метки[b]).count("1") == 1 for a, b in п.соседи))
        self.assertEqual(180.0, мд.плоскость(мд.найти("КАМ8 прямоугольная Грей")).шаг)

    def test_декодер_общей_плоскости(self):
        """Поворот на 90° 16APSK против независимого счёта: умножение на i и ближайшая точка."""
        с_ = мд.найти("DVB-S2 16APSK (γ 3,15)")
        н = мд.настройки({"модуляция": "АФМ16", "демодулятор": с_.имя, "вид_кода": с_.имя, "поворот": 90},
                         найти_=lambda и: мд.найти(и))
        точки = {int(м, 2): complex(x, y) for м, x, y in с_.точки}
        ждём = [min(точки, key=lambda t: abs(точки[t] - точки[v] * 1j)) for v in range(16)]
        self.assertEqual(ждём, list(мд.декодер(н).таблица))
        with self.assertRaises(ValueError) as о:
            мд.декодер(мд.настройки({"модуляция": "АФМ16", "демодулятор": с_.имя, "вид_кода": с_.имя, "поворот": 45}))
        self.assertEqual("поворот 45° не переводит созвездие в себя; для этой плоскости (16 точек) шаг 90°", str(о.exception))
        with self.assertRaises(ValueError):
            мд.настройки({"модуляция": "АФМ16", "демодулятор": с_.имя, "вид_кода": с_.имя, "относительная": True})
        self.assertEqual(("АФМ", 5), мд.модуляция("apsk-32"))
        # Соседи у APSK — по кольцам: кодов Грея больше предела — отказ словами.
        with self.assertRaises(ValueError) as о:
            мд.число_вариантов(н)
        self.assertEqual("вариантов кода Грея больше 100000: перебор для 16 точек не поддерживается", str(о.exception))
        # Соседи общей плоскости — ближе 1,25 наименьшего расстояния каждой из пары: у креста 32 — по сетке.
        п = мд.плоскость(мд.найти("КАМ32 крест"))
        self.assertEqual(52, len(п.соседи))
        # Нет ни одного кода Грея (треугольник соседей) — отказ «нет разметок кодом Грея».
        треугольник = мд.разобрать_etl("# вид: общее\n00 = 0 0\n01 = 1 0\n10 = 0.5 0.866\n11 = 5 5")
        н3 = мд.настройки({"модуляция": "АФМ4", "демодулятор": "т", "вид_кода": "т"}, найти_=lambda и: треугольник)
        with self.assertRaises(ValueError) as о:
            мд.число_вариантов(н3)
        self.assertIn("нет разметок кодом Грея", str(о.exception))

    def test_своя_разметка(self):
        основа = мд.найти("DVB-S2 16APSK (γ 3,15)")
        п = мд.плоскость(основа)
        метки = [format(м, "04b") for м in п.метки]
        метки[0], метки[5] = метки[5], метки[0]
        своя = мд.своя_разметка(основа, метки, "своя.etl")
        сп = мд.плоскость(своя)
        self.assertEqual("общее", сп.вид)
        np.testing.assert_allclose(np.array(п.xy), np.array(сп.xy), atol=1e-12)
        self.assertEqual([int(м, 2) for м in метки], list(сп.метки))
        снова = мд.плоскость(мд.разобрать_etl(мд.в_etl(своя)))
        self.assertEqual(сп.метки, снова.метки)
        # ФМ и КАМ основы — своя разметка той же формы (картинкой выгружается и разбирается так же).
        for имя in ("ФМ8 Грей", "КАМ16 Грей"):
            о = мд.найти(имя)
            по = мд.плоскость(о)
            м_ = [format(м, f"0{о.k}b") for м in по.метки][::-1]
            с2 = мд.своя_разметка(о, м_, "x.etl")
            self.assertEqual((по.вид, [int(м, 2) for м in м_]), (мд.плоскость(с2).вид, list(мд.плоскость(с2).метки)))
            self.assertEqual(мд.плоскость(с2).метки, мд.плоскость(мд.разобрать_etl(мд.в_etl(с2))).метки)
        for метки_, ждём in ((метки[:15], "меток 15, а точек 16"), (["0000"] * 16, "метка «0000» повторяется: у каждой точки своя"),
                             (метки[:15] + ["2"], "метка «2»: 4 символов 0 и 1"), (метки[:15] + ["00000"], "метка «00000»: 4 символов 0 и 1")):
            with self.subTest(ждём), self.assertRaises(ValueError) as о:
                мд.своя_разметка(основа, метки_, "x.etl")
            self.assertEqual(ждём, str(о.exception))

    def test_слой_б64(self):
        rng = np.random.default_rng(4)
        для = {7: 128, 8: 256}
        for k, M in для.items():
            таблица = tuple(rng.permutation(M).tolist())
            д = мд.Декодер(k, таблица, описание="КАМ" + str(M))
            слой = д.слой()
            self.assertTrue(слой.startswith(f"моддекодер {k} б64: "))
            self.assertLessEqual(len(слой), мд.СЛОЙ_ДО)
            self.assertEqual(таблица, мд.из_слоя(слой).таблица)
            номера, метки_ = tuple(rng.permutation(M).tolist()), tuple(rng.permutation(M).tolist())
            о = мд.Декодер(k, номера=номера, метки=метки_)
            self.assertEqual((номера, метки_), (мд.из_слоя(о.слой()).номера, мд.из_слоя(о.слой()).метки))
            x = rng.integers(0, 2, k * 50).astype(np.uint8)
            ряд, _ = мд.снять(x, слой)
            self.assertTrue(np.array_equal(метки_в_биты([таблица[int("".join(map(str, x[i:i + k])), 2)] for i in range(0, len(x), k)], k), ряд))
        self.assertEqual("моддекодер 6: " + " ".join(map(str, range(64))), мд.Декодер(6, tuple(range(64))).слой())
        for слой, ждём in (("моддекодер 8 б64: AAEC", "таблица: байты в base64 — перестановка чисел 0…255"),
                           ("моддекодер 8 б64: !!!", "таблица: байты в base64 — перестановка чисел 0…255")):
            with self.subTest(слой), self.assertRaises(ValueError) as о:
                мд.из_слоя(слой)
            self.assertEqual(ждём, str(о.exception))


# -- автомат ---------------------------------------------------------------------------------------

def вне_группы(k: int, сид: int) -> list[int]:
    """Случайная перестановка меток, которой нет среди вариантов группы и таблиц плоскостей (истинная → принятая)."""
    M = 1 << k
    метки = метки_в_биты(range(M), k)
    группа = {tuple(р.в_символы(ploskost.преобразовать_фм(метки, k, **в), k).tolist())
              for в in ploskost.варианты_фм(k) + ploskost.варианты_плоскостей(k, ploskost.варианты_фм(k)) if "номера" not in в}
    rng = np.random.default_rng(сид)
    while True:
        π = rng.permutation(M).tolist()
        # Декодер ищет таблицу принятая → истинная; ни она, ни её инверсии бит метки не должны быть в группе.
        if all(tuple(np.asarray(обратная(π)) ^ c) not in группа for c in range(M)):
            return π


class АвтоматTests(unittest.TestCase):
    def setUp(self):
        self.старый = мд.КАТАЛОГ
        self.addCleanup(setattr, мд, "КАТАЛОГ", self.старый)
        мд.КАТАЛОГ = None

    def test_кадры_модема_разметка_вслепую(self):
        данные = np.random.default_rng(5).integers(0, 2, (48, 39, 57)).astype(np.uint8)
        поток, начала = comtech(данные, приставка=501, ошибок=1e-3)
        поток = поток[:len(поток) // 3 * 3]
        π = вне_группы(3, 7)
        принятые = передать(поток, 3, π)
        итог = modem.код_в_кадрах(принятые, начала, 2964, фм=[3], срок_вслепую=120)
        self.assertIsNotNone(итог)
        (плоскость, код), данные_ = итог
        self.assertIn("разметка найдена вслепую по строкам кода в кадрах", плоскость.что)
        self.assertTrue(плоскость.свойства["вслепую"])
        self.assertIn(плоскость.свойства["вариант"]["таблица"], р.равноценные(обратная(π), 8))
        self.assertTrue(any("35 классов чётности × 576 перестановок" in п for п in плоскость.подробно))
        self.assertIn("(64, 57) × (46, 39)", код.что)
        self.assertEqual(1.0, код.свойства.get("блоков_чисто", код.уверенность) if "блоков_чисто" in код.свойства else 1.0)
        self.assertGreaterEqual(код.уверенность, 0.95)
        # Разметка без вслепую (срок 0) — не находится.
        self.assertIsNone(modem.код_в_кадрах(принятые, начала, 2964, фм=[3], срок_вслепую=0))
        # Имя варианта вслепую.
        self.assertEqual("разметка вслепую: таблица 1 0", ploskost.имя_фм({"таблица": [1, 0], "имя": "таблица 1 0", "вслепую": True}, 1))

    def test_поток_разметка_вслепую(self):
        x = лрп(60000, (23, 18))
        x = x[:len(x) // 3 * 3]
        π = вне_группы(3, 9)
        # Группа здесь находит «структуру» и сама (часть бит метки верна — видна ПСП большей степени): чтобы
        # проверить путь «группа не дала → вслепую», её оценка подменяется пустой.
        with unittest.mock.patch.object(ploskost, "_оценить", lambda выборка, в, п, до: (0.0, [], 0)):
            найдено = ploskost.найти_фм(передать(x, 3, π), 3, лучших=1, бюджет=50)
        self.assertIsNotNone(найдено)
        self.assertIn("разметка найдена вслепую", найдено.что)
        имя, ряд = next(iter(найдено.дальше.items()))
        self.assertTrue(имя.startswith("разметка вслепую: таблица "))
        self.assertLessEqual(берлекэмп(list(ряд[3000:3400])), 26)
        self.assertIn(найдено.свойства["вариант"]["таблица"], р.равноценные(обратная(π), 8))
        # До 8 точек — только; у 16 — не ищется в сплошном потоке автоматом.
        self.assertIsNone(ploskost.вслепую_фм(передать(x[:len(x) // 4 * 4], 4, list(range(16))), 4, 0, None))

    @unittest.skipUnless(ОБРАЗЕЦ.exists(), "нет образца аналитика K2964")
    def test_образец_k2964_с_перемешанной_разметкой(self):
        """Образец аналитика: метки 8PSK перемешаны случайной перестановкой вне группы — автомат находит разметку, ТКБ снимается."""
        биты = np.unpackbits(np.frombuffer(ОБРАЗЕЦ.read_bytes(), np.uint8), bitorder="little")
        π = вне_группы(3, 11)
        принятые = р.применить(биты, 3, π)                            # принятая = π[метка файла]
        начало = time.monotonic()
        итог = modem.разобрать_кадры(принятые, 5928, фм=[3], срок_вслепую=120)
        self.assertIsNotNone(итог)
        (плоскость, код), данные, сведения = итог
        self.assertEqual(2964, сведения["кадр"])
        self.assertIn("разметка найдена вслепую", плоскость.что)
        self.assertIn("(64, 57) × (46, 39)", код.что)
        self.assertGreaterEqual(код.уверенность, 0.95)
        self.assertLess(time.monotonic() - начало, 400)


# -- API -------------------------------------------------------------------------------------------

class РазметкаЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — общая заготовка веб-тестов

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.старый = мд.КАТАЛОГ
        self.addCleanup(setattr, мд, "КАТАЛОГ", self.старый)
        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client
        self.assertEqual(200, self.к.get("/api/potok-planes").status_code)
        self.сессия = self.к.post("/api/sessions", json={"name": "разметка"}).json()["id"]

    def массив(self, биты: np.ndarray | None = None, данные: bytes | None = None, имя="поток.bin") -> str:
        з = self.сеть.app.state.potok
        владелец = self.сеть.repos.users.by_login("engineer").id
        ид = з.создать(владелец=владелец, имя=имя, данные=данные if данные is not None else np.packbits(биты).tobytes(),
                       разбирать=False, бит=None if биты is None else len(биты), сессия=self.сессия)
        for _ in range(200):
            if з.прочитать(ид)["состояние"] == "готово":
                break
            time.sleep(0.05)
        return ид

    def дождаться(self, ид: str) -> dict:
        for _ in range(1200):
            с_ = self.к.get(f"/api/potok-blind/{ид}").json()
            if с_["готово"]:
                return с_
            time.sleep(0.1)
        self.fail("поиск не закончился")

    def test_анализ_синхрослово_и_поиск(self):
        поток, длина = кадры_строк(120, ошибок=1e-3)
        π = np.random.default_rng(2).permutation(8).tolist()
        принятые = передать(поток, 3, π)
        ид = self.массив(принятые)
        а = self.к.post(f"/api/potok/{ид}/moddecoder/analysis", json={"stage": 0, "модуляция": "ФМ8"})
        self.assertEqual(200, а.status_code, а.text)
        self.assertEqual((200, 0), (а.json()["кадр"], а.json()["места"][0]["начало"]))
        self.assertEqual(400, self.к.post(f"/api/potok/{ид}/moddecoder/analysis", json={"stage": 0, "модуляция": "ФМ7"}).status_code)
        self.assertEqual(400, self.к.post(f"/api/potok/{ид}/moddecoder/analysis",
                                          json={"stage": 0, "модуляция": "ФМ8", "фаза": 3}).status_code)
        # Синхрослово 24 бита покрывает не все 8 символов: остальное — перебором по байтовой мере.
        с_ = self.к.post(f"/api/potok/{ид}/moddecoder/sync", json={"stage": 0, "модуляция": "ФМ8",
                                                                  "слово": "".join(map(str, СИНХРО24))})
        self.assertEqual(200, с_.status_code, с_.text)
        d = с_.json()
        покрыто = {int(a): б for a, б in d["варианты"][0]["соответствие"].items()}
        self.assertEqual({π[t]: t for t in р.в_символы(СИНХРО24, 3).tolist()}, покрыто)
        self.assertEqual(len(покрыто), d["покрыто"])
        self.assertTrue(all(d["таблица"][v] == t for v, t in покрыто.items()) if "таблица" in d else True)
        for тело, ждём in (({"слово": "xyz"}, 400), ({"из_библиотеки": "нет такого"}, 400), ({"слово": "101"}, 400)):
            self.assertEqual(ждём, self.к.post(f"/api/potok/{ид}/moddecoder/sync",
                                               json={"stage": 0, "модуляция": "ФМ8", **тело}).status_code)
        # Поиск по строкам в кадрах — в фоне, с ходом.
        н = self.к.post(f"/api/potok/{ид}/moddecoder/blind", json={"stage": 0, "модуляция": "ФМ8", "мера": "строки",
                                                                    "кадр": {"длина": длина, "начало": 0}, "срок": 120})
        self.assertEqual(200, н.status_code, н.text)
        с_ = self.дождаться(н.json()["поиск"])
        self.assertEqual("", с_["ошибка"])
        self.assertEqual(1.0, с_["доля"])
        self.assertIn(обратная(π), с_["итог"]["равноценные"])
        self.assertEqual(0, с_["итог"]["фаза"])
        # Ошибки запроса.
        for тело, ждём in (({"мера": "строки"}, "мера «строки» — по кадрам: включите «Мера по кадрам» (строка просмотра — кадр)"),
                           ({"мера": "авось"}, "мера: авто, строки, лрп или байты"),
                           ({"срок": 5000}, "срок — от 1 до 600 с"), ({"фаза": 5}, "фаза — от 0 до 2"),
                           ({"старт": "0123"}, "старт: перестановка чисел 0…7 по одному разу")):
            with self.subTest(тело):
                о = self.к.post(f"/api/potok/{ид}/moddecoder/blind", json={"stage": 0, "модуляция": "ФМ8", **тело})
                self.assertEqual((400, ждём), (о.status_code, о.json()["error"]))
        self.assertEqual(404, self.к.get("/api/potok-blind/нет").status_code)
        self.assertEqual(404, self.к.post("/api/potok-blind/нет/stop", json={}).status_code)
        # Остановка и чужой поиск.
        x = np.random.default_rng(3).integers(0, 2, 60000).astype(np.uint8)
        ид2 = self.массив(x)
        п = self.к.post(f"/api/potok/{ид2}/moddecoder/blind", json={"stage": 0, "модуляция": "ФМ8", "мера": "байты"}).json()["поиск"]
        self.assertEqual({"ok": True}, self.к.post(f"/api/potok-blind/{п}/stop", json={}).json())
        self.assertIn(self.дождаться(п)["ошибка"], ("остановлено", ""))
        self.сеть.login("admin")
        self.assertEqual(404, self.к.get(f"/api/potok-blind/{п}").status_code)

    def test_библиотека_и_своя_плоскость(self):
        d = self.к.get("/api/potok-sync-library").json()["items"]
        self.assertTrue(any(э["имя"].startswith("ASM CCSDS") and э["биты"].startswith("00011010") for э in d))
        основа = "DVB-S2 16APSK (γ 3,15)"
        точки = next(п for п in self.к.get("/api/potok-planes").json()["items"] if п["имя"] == основа)["точки"]
        метки = [т[0] for т in точки]
        метки[0], метки[1] = метки[1], метки[0]
        о = self.к.post("/api/potok-planes/own", json={"основа": основа, "метки": метки, "имя": "моя APSK"})
        self.assertEqual(400, о.status_code)                               # не .etl
        о = self.к.post("/api/potok-planes/own", json={"основа": основа, "метки": метки, "имя": "моя-apsk.etl"})
        self.assertEqual(200, о.status_code, о.text)
        self.assertEqual(("моя-apsk.etl", "общее", 16), (о.json()["plane"]["имя"], о.json()["plane"]["вид"], о.json()["plane"]["M"]))
        self.assertEqual(метки, [т[0] for т in о.json()["plane"]["точки"]])
        о = self.к.post("/api/potok-planes/own", json={"основа": основа, "метки": метки, "имя": "моя-apsk.etl"})
        self.assertEqual((400, "плоскость «моя-apsk.etl» уже есть в папке: другое имя или «заменить»"), (о.status_code, о.json()["error"]))
        метки[2], метки[3] = метки[3], метки[2]
        о = self.к.post("/api/potok-planes/own", json={"основа": основа, "метки": метки, "имя": "моя-apsk.etl", "заменить": True})
        self.assertEqual(метки, [т[0] for т in о.json()["plane"]["точки"]])
        for тело, ждём in (({"метки": "0000"}, "метки — список меток точек по номерам"),
                           ({"метки": ["0000"] * 16}, "метка «0000» повторяется: у каждой точки своя"),
                           ({"основа": "нет"}, "плоскости «нет» нет ни среди встроенных, ни в папке плоскостей")):
            о = self.к.post("/api/potok-planes/own", json={"основа": основа, "метки": метки, "имя": "x.etl", **тело})
            self.assertEqual((400, ждём), (о.status_code, о.json()["error"]))

    def test_iq_облако_и_решения(self):
        метки, z, п = облако_эталон("DVB-S2 8PSK", 6000, поворот=20.0, инверсия=False, шум=0.06)
        ид = self.массив(данные=в_int16(z), имя="iq.i16")
        о = self.к.post(f"/api/potok/{ид}/moddecoder/iq", json={"stage": 0, "формат": "int16"})
        self.assertEqual(200, о.status_code, о.text)
        d = о.json()
        self.assertEqual((8, 6000), (d["точек"], d["отсчётов"]))
        self.assertEqual(8, len(d["центры"]))
        self.assertLessEqual(len(d["облако"]), iq.ОБЛАКО_ДО)
        лучшая = d["плоскости"][0]
        self.assertIn(лучшая["имя"], ("ФМ8 натуральный", "ФМ8 Грей", "DVB-S2 8PSK"))
        self.assertAlmostEqual(25.0, лучшая["поворот"], delta=0.6)                 # −20° по модулю 45°
        # Решения по плоскости DVB-S2 8PSK — новый массив в той же сессии.
        р_ = self.к.post(f"/api/potok/{ид}/moddecoder/iq/decide", json={"stage": 0, "формат": "int16", "точек": 8,
                                                                        "поворот": лучшая["поворот"], "плоскость": "DVB-S2 8PSK"})
        self.assertEqual(200, р_.status_code, р_.text)
        новый = р_.json()["id"]
        self.assertEqual(18000, р_.json()["бит"])
        з = self.сеть.app.state.potok
        self.assertEqual(self.сессия, з.прочитать(новый)["сессия"])
        биты = self.к.get(f"/api/potok/{новый}/bits", params={"stage": 0, "start": 0, "count": 18000})
        self.assertEqual(200, биты.status_code)
        # По кластерам и ошибки.
        р_ = self.к.post(f"/api/potok/{ид}/moddecoder/iq/decide", json={"stage": 0, "формат": "int16", "точек": 8})
        self.assertEqual(200, р_.status_code)
        self.assertTrue(any("номера кластеров" in с_ for с_ in р_.json()["описание"]))
        for путь, тело, ждём in (("iq", {"формат": "int12"}, "формат отсчётов: int8, uint8, int16, float32 или WAV"),
                                 ("iq", {"формат": "int16", "точек": 3}, "число точек — степень двойки от 2 до 1024"),
                                 ("iq/decide", {"формат": "int16", "точек": 8, "плоскость": "КАМ16 Грей"},
                                  "плоскость «КАМ16 Грей» — 16 точек, а в облаке 8"),
                                 ("iq/decide", {"формат": "int16", "поворот": "сорок"}, "поворот — градусы")):
            о = self.к.post(f"/api/potok/{ид}/moddecoder/{путь}", json={"stage": 0, **тело})
            self.assertEqual((400, ждём), (о.status_code, о.json()["error"]))


# -- окно в браузере: функции app.js в node --------------------------------------------------

@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноРазметкиВБраузереTests(unittest.TestCase):
    ФУНКЦИИ = ["меткиИзТаблицы", "плохиеМетки", "svgРазметки", "повернутьОблако", "ходПоиска", "описаниеАнализа",
               "шагиДекодирования", "разобратьМодуляцию"]

    def выполнить(self, случаи: list[dict]) -> list:
        код = функции_js(self.ФУНКЦИИ, []) + """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(случаи.map((с) => {
    switch (с.что) {
    case 'метки': return меткиИзТаблицы(с.т, с.таблица, с.k);
    case 'плохие': return плохиеМетки(с.м, с.k);
    case 'svg': return svgРазметки(с.т, с.м, с.в, с.п);
    case 'облако': return повернутьОблако(с.т, с.п, с.и);
    case 'ход': return ходПоиска(с.с);
    case 'анализ': return описаниеАнализа(с.а);
    case 'шаги': return шагиДекодирования(с.с, с.ф);
    case 'модуляция': return разобратьМодуляцию(с.т);
    default: return null;
    }
})));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=60)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_функции_окна(self):
        точки = [["00", 1, 0], ["01", 0, 1], ["11", -1, 0], ["10", 0, -1]]
        rng = np.random.default_rng(1)
        а = р.анализ(метки_в_биты(np.concatenate([np.concatenate([[7, 7, 0, 1], rng.integers(0, 8, 40)]) for _ in range(80)]), 3),
                     3, фаза=0)
        итоги = self.выполнить([
            {"что": "метки", "т": точки, "таблица": [3, 2, 1, 0], "k": 2},
            {"что": "метки", "т": точки, "таблица": [3, 2, 9, 0], "k": 2},
            {"что": "плохие", "м": ["00", "01", "01", "2", "110"], "k": 2},
            {"что": "svg", "т": точки, "м": ["00", "01", "<b>", "10"], "в": 1, "п": [2]},
            {"что": "облако", "т": [[1, 0], [0, 1]], "п": 90, "и": True},
            {"что": "ход", "с": {"доля": 0.4567, "ход": "классы", "прошло": 3.25, "осталось": 12.4}},
            {"что": "ход", "с": {"доля": 1, "ход": "", "прошло": 30, "осталось": None}},
            {"что": "анализ", "а": а},
            {"что": "шаги", "с": "моддекодер 3: 0 1 2 3 4 5 6 7", "ф": 2},
            {"что": "шаги", "с": "моддекодер 1: 1 0", "ф": 0},
            {"что": "модуляция", "т": "apsk 16"},
        ])
        self.assertEqual(["11", "10", "00", "01"], итоги[0])
        self.assertEqual(["11", "10", "00", "10"], итоги[1])                  # метка вне таблицы — остаётся своя
        self.assertEqual([1, 2, 3, 4], итоги[2])
        svg = итоги[3]
        self.assertEqual(4, svg.count('data-n="'))
        self.assertIn('class="stol-md-pt is-selected"', svg)
        self.assertIn('class="stol-md-pt is-bad"', svg)
        self.assertNotIn("<b>", svg)
        self.assertIn(">?</text>", svg)
        np.testing.assert_allclose([[0, 1], [1, 0]], итоги[4], atol=1e-12)
        self.assertEqual("46 % · классы · прошло 3,3 с · осталось ≈ 12 с", итоги[5])
        self.assertEqual("100 % ·  · прошло 30 с", итоги[6])
        текст = "\n".join(итоги[7])
        self.assertIn("Граница символа — бит 0", текст)
        self.assertIn("Постоянно с символа 0 кадра: 7 7 0 1", текст)
        self.assertIn("Повтор через 44 симв. (132 бит), отрыв ", текст)
        self.assertNotIn("кадр —", текст)                                        # кадр равен повтору
        self.assertEqual("Повторяющихся символов на одних местах не видно: период кадра по символам не найден",
                         self.выполнить([{"что": "анализ", "а": {**а, "период": None}}])[0][1])
        self.assertEqual([{"вид": "слой", "слой": "обрезка начало 2 конец 0", "вкл": True},
                          {"вид": "слой", "слой": "моддекодер 3: 0 1 2 3 4 5 6 7", "вкл": True}], итоги[8])
        self.assertEqual([{"вид": "слой", "слой": "моддекодер 1: 1 0", "вкл": True}], итоги[9])
        self.assertEqual({"вид": "АФМ", "M": 16, "k": 4}, итоги[10])

    def test_картинка_своей_разметки_точно(self):
        """SVG своей разметки разбирается обратно: оси, круги, подписи — координаты, радиусы, размер шрифта, классы."""
        import re  # noqa: PLC0415

        случаи = []
        for n, k in ((2, 2), (16, 4), (64, 6)):
            rng = np.random.default_rng(n)
            точки = [[format(i, f"0{k}b"), round(float(rng.normal()), 4), round(float(rng.normal()), 4)] for i in range(n)]
            метки = [т[0] for т in точки]
            метки[-1] = "2" * k                                   # не 0/1 — подпись «?»
            случаи.append((точки, метки, 1, [0, n - 1]))
        # Метки разной длины: k — по первой (у 16 точек шрифт не упирается в пределы).
        точки16 = [[format(i, "04b"), i / 10, -i / 10] for i in range(16)]
        случаи.append((точки16, ["0000"] + ["1"] * 15, -1, []))
        случаи.append((точки16, ["1"] + ["0000"] * 15, -1, []))
        # 64 точки по 2 и по 1 биту; метки в 10 знаков (КАМ-1024) — выводятся, в 11 — нет.
        точки64 = [[format(i, "06b"), (i % 8) / 4 - 1, (i // 8) / 4 - 1] for i in range(64)]
        случаи.append((точки64, ["01"] * 64, -1, []))
        случаи.append((точки64, ["1"] * 64, -1, []))
        случаи.append(([["0000000000", 1, 0], ["1111111111", -1, 0]], ["0000000000", "00000000000"], -1, []))
        итоги = self.выполнить([{"что": "svg", "т": т, "м": м, "в": в, "п": п} for т, м, в, п in случаи])
        ч = lambda x: round(x, 3)  # noqa: E731
        for (точки, метки, выбрана, плохие), svg in zip(случаи, итоги, strict=True):
            n, k = len(точки), len(метки[0])
            шрифт = max(0.08, min(0.26, 2.2 / (math.sqrt(n) * max(2, k))))
            self.assertIn('viewBox="-1.9 -1.9 3.8 3.8"', svg)
            self.assertIn('<line class="stol-md-axis" x1="-1.9" y1="0" x2="1.9" y2="0"/>', svg)
            self.assertIn('<line class="stol-md-axis" x1="0" y1="-1.9" x2="0" y2="1.9"/>', svg)
            круги = re.findall(r'<g class="stol-md-hit" data-n="(\d+)"><circle class="([^"]+)" cx="([^"]+)" cy="([^"]+)" r="([^"]+)"/>'
                               r'<text class="([^"]+)" x="([^"]+)" y="([^"]+)" font-size="([^"]+)">([^<]*)</text></g>', svg)
            self.assertEqual(n, len(круги))
            for i, (nn, класс, cx, cy, r, класс_т, x, y, fs, подпись) in enumerate(круги):
                _, px, py = точки[i]
                self.assertEqual(str(i), nn)
                self.assertEqual("stol-md-pt" + (" is-selected" if i == выбрана else "") + (" is-bad" if i in плохие else ""), класс)
                self.assertEqual("stol-md-lbl" + (" is-bad" if i in плохие else ""), класс_т)
                self.assertAlmostEqual(ч(px), float(cx), delta=0.0011)
                self.assertAlmostEqual(ч(-py), float(cy), delta=0.0011)
                self.assertAlmostEqual(0.09 if i == выбрана else 0.055, float(r))
                self.assertAlmostEqual(ч(px), float(x), delta=0.0011)
                self.assertAlmostEqual(ч(-py - 0.08), float(y), delta=0.0011)
                self.assertAlmostEqual(ч(шрифт), float(fs), delta=0.0011)
                self.assertEqual(метки[i] if re.fullmatch(r"[01]{1,10}", метки[i]) else "?", подпись)
            self.assertTrue(svg.endswith("</svg>"))

    def test_тексты_окна_точно(self):
        а = {"фаза": 2, "символов": 262145, "k": 3, "период": {"символов": 7904, "отрыв": 723.5}, "кадр": 988, "цикл": 1976,
             "места": [{"начало": 10 * i, "длина": 2, "символы": [i, 7], "доля": 0.95} for i in range(7)],
             "частоты": [0.125, 0.25, 0.0625, 0.0625, 0.125, 0.125, 0.123, 0.127],
             "повторов_подряд": 0.12542, "повторов_ждать": 0.1251,
             "частые_переходы": [[3, 5, 4654], [5, 5, 4540], [2, 0, 4497], [4, 4, 4451], [5, 2, 4434], [4, 2, 4426]]}
        а["места"][1].update(чередуется=True, символы_нечётных=[6, 6])
        точки = [["00", 1, 0], ["01", 0, 1], ["11", -1, 0], ["10", 0, -1]]
        итоги = self.выполнить([
            {"что": "анализ", "а": а},
            {"что": "ход", "с": {"доля": 0.12345, "ход": "классы", "прошло": 9.96, "осталось": 10}},
            {"что": "ход", "с": {"ход": "начат"}},
            {"что": "облако", "т": [[1, 2], [-0.5, 0.25]], "п": 30, "и": False},
            {"что": "облако", "т": [[1, 2]], "и": True},
            {"что": "метки", "т": точки, "таблица": [3, 2, 4, 0], "k": 2},
            {"что": "метки", "т": точки, "таблица": [3, -1, 1.5, 0], "k": 2},
            {"что": "плохие", "м": ["1", "01", "10"], "k": 2},
        ])
        self.assertEqual([
            "Граница символа — бит 2 · символов 262 145",
            "Повтор через 7904 симв. (23712 бит), отрыв 723,5 σ; кадр — 988 симв. (2964 бит); синхрослово чередуется (цикл 1976 симв.)",
            "Постоянно с символа 0 кадра: 0 7 — доля 0,95",
            "Постоянно с символа 10 кадра: 1 7 / 6 6 (через кадр) — доля 0,95",
            *[f"Постоянно с символа {10 * i} кадра: {i} 7 — доля 0,95" for i in range(2, 6)],
            "Частоты: 0:12,5% 1:25,0% 2:6,3% 3:6,3% 4:12,5% 5:12,5% 6:12,3% 7:12,7%",
            "Повторов символа подряд 12,5 % (у независимых — 12,5 %); частые переходы: 3→5 (4654), 5→5 (4540), 2→0 (4497), "
            "4→4 (4451), 5→2 (4434)"], итоги[0])
        self.assertEqual("12 % · классы · прошло 10,0 с · осталось ≈ 10 с", итоги[1])
        self.assertEqual("0 % · начат · прошло 0,0 с", итоги[2])
        c, s_ = math.cos(math.radians(30)), math.sin(math.radians(30))
        np.testing.assert_allclose([[c - 2 * s_, s_ + 2 * c], [-0.5 * c - 0.25 * s_, -0.5 * s_ + 0.25 * c]], итоги[3], atol=1e-12)
        np.testing.assert_allclose([[1, -2]], итоги[4], atol=1e-12)
        self.assertEqual(["11", "10", "00", "10"], итоги[5])                  # 4 — не метка двух бит
        self.assertEqual(["11", "01", "00", "10"], итоги[6])                  # −1 и 1,5 — не метки
        self.assertEqual([0], итоги[7])


    def test_анализ_1024_частоты_и_граница(self):
        """У 1024 символов — 16 самых частых (при равных — меньший номер раньше), а не тысяча чисел."""
        частоты = [0.0005] * 1024
        частоты[700], частоты[3], частоты[5] = 0.02, 0.01, 0.01
        а = {"фаза": 9, "символов": 65536, "k": 10, "период": None, "частоты": частоты, "повторов_подряд": 0.001,
             "повторов_ждать": 0.001, "частые_переходы": [[1, 2, 3]]}
        [т] = self.выполнить([{"что": "анализ", "а": а}])
        self.assertEqual("Граница символа — бит 9 · символов 65\xa0536", т[0])
        верх = [700, 3, 5] + [v for v in range(1024) if v not in (3, 5, 700)][:13]
        self.assertEqual("Частоты (16 самых частых из 1024): " + " ".join(
            f"{v}:{частоты[v] * 100:.1f}%".replace(".", ",") for v in верх), т[2])
        # Граница: 64 символа — все частоты, 65 и больше — 16 самых частых; при равных частотах — по номеру.
        [т64] = self.выполнить([{"что": "анализ", "а": {**а, "частоты": [1 / 64] * 64}}])
        self.assertTrue(т64[2].startswith("Частоты: 0:1,6% 1:1,6%") and т64[2].count("%") == 64)
        [т65] = self.выполнить([{"что": "анализ", "а": {**а, "частоты": [1 / 65] * 65}}])
        self.assertEqual("Частоты (16 самых частых из 65): " + " ".join(f"{v}:1,5%" for v in range(16)), т65[2])
        [т128] = self.выполнить([{"что": "анализ", "а": {**а, "частоты": [0.01] * 64 + [0.02] * 64}}])
        self.assertEqual("Частоты (16 самых частых из 128): " + " ".join(f"{v}:2,0%" for v in range(64, 80)), т128[2])
        а8 = {**а, "частоты": [0.125] * 8}
        self.assertEqual("Частоты: " + " ".join(f"{v}:12,5%" for v in range(8)), self.выполнить([{"что": "анализ", "а": а8}])[0][2])

# -- края: точные значения против независимого счёта ---------------------------------------------

def период_эталон(символы: np.ndarray, M: int, до: int) -> dict | None:
    """Период прямым счётом совпадений s[i] = s[i + d] — без БПФ."""
    n = len(символы)
    до = min(до, n // 4)
    if до < 4:
        return None
    p = np.bincount(символы, minlength=M) / n
    q = float((p ** 2).sum())
    if q >= 1 - 1e-12:
        return None
    z = np.zeros(до + 1)
    for d in range(4, до + 1):
        пар = n - d
        z[d] = ((символы[:-d] == символы[d:]).sum() / пар - q) / math.sqrt(q * (1 - q) / пар)
    лучший = int(np.argmax(z))
    if z[лучший] < 8:
        return None
    for д in range(4, лучший):
        if лучший % д == 0 and z[д] >= z[лучший] / 2:
            лучший = д
            break
    return {"символов": лучший, "отрыв": round(float(z[лучший]), 1)}


class КраяTests(unittest.TestCase):
    def test_в_биты_и_символы(self):
        self.assertEqual([1, 0, 1, 0, 1, 0], р.в_биты(np.array([5, 2]), 3).tolist())
        self.assertEqual([[1, 0, 1, 0, 1, 0], [1, 1, 1, 0, 0, 0]], р.в_биты(np.array([[5, 2], [7, 0]]), 3).tolist())
        self.assertEqual([2, 5], р.в_символы(np.array([1, 0, 1, 0, 1, 0, 1]), 3, фаза=1).tolist())
        self.assertEqual([1, 0, 1, 1, 1, 0], р.применить(np.array([0, 1, 0, 1, 1, 0]), 3, [0, 1, 5, 3, 4, 7, 6, 2]).tolist())

    def test_совпадения_как_прямой_счёт(self):
        rng = np.random.default_rng(1)
        for n, до in ((100, 29), (100, 27), (64, 63), (5, 4)):
            символы = rng.integers(0, 4, n)
            ждём = [int((символы[:n - d] == символы[d:]).sum()) for d in range(до + 1)]
            with self.subTest(n=n, до=до):
                self.assertEqual(ждём, р._совпадения(символы, 4, до).astype(int).tolist())

    def test_период_как_прямой_счёт(self):
        rng = np.random.default_rng(2)
        случаи = []
        for P, n, M in ((9, 40, 8), (11, 40, 8), (20, 400, 4), (60, 2000, 4), (7, 3000, 2)):
            база = rng.integers(0, M, P)
            символы = np.tile(база, n // P + 1)[:n]
            шум = rng.random(n) < 0.3
            символы = np.where(шум, rng.integers(0, M, n), символы)
            случаи.append((символы, M))
        # Кадр 20 со словом и данными, повторяющимися через 3 кадра: повтор 60 — кратный кадру.
        данные = rng.integers(0, 4, (3, 16))
        случаи.append((np.concatenate([np.concatenate([[3, 0, 1, 2], данные[f % 3]]) for f in range(120)]), 4))
        данные = rng.integers(0, 4, (3, 16))
        кадры = [np.concatenate([[3, 0], rng.integers(0, 4, 2), данные[f % 3]]) for f in range(120)]
        случаи.append((np.concatenate(кадры), 4))
        # Слово — 8 из 20 символов кадра, данные повторяются через 3 кадра: отрыв кадра — между третью и половиной
        # отрыва повтора, и повтор (60) остаётся лучшим.
        данные = rng.integers(0, 8, (3, 12))
        кадры = np.concatenate([np.concatenate([[1, 2, 3, 4, 5, 6, 7, 0], данные[f % 3]]) for f in range(300)])
        кадры = np.where(rng.random(len(кадры)) < 0.05, rng.integers(0, 8, len(кадры)), кадры)
        случаи.append((кадры, 8))
        self.assertEqual(60, период_эталон(кадры, 8, 1 << 14)["символов"])
        for символы, M in случаи:
            with self.subTest(n=len(символы)):
                self.assertEqual(период_эталон(символы, M, 1 << 14), р.период(символы, M))
        self.assertIsNone(р.период(np.tile(np.arange(11), 4)[:40], 8))          # период 11 > 40 // 4
        self.assertEqual(9, р.период(np.tile(np.arange(9), 5)[:40], 16)["символов"])
        self.assertIsNone(р.период(np.zeros(1000, np.int64), 8))

    def test_постоянные_места_края(self):
        self.assertEqual([], р.постоянные_места(np.zeros(9, np.int64), 3))       # три кадра — мало
        self.assertEqual([{"начало": 0, "длина": 3, "символы": [0, 0, 0], "доля": 1.0}], р.постоянные_места(np.zeros(12, np.int64), 3))
        # Доля ровно 0,9 — постоянно; ровно 0,9 в чётных (и в нечётных) — чередуется.
        т = np.tile([[1, 2, 3, 4, 5]], (20, 1))
        т[:, 0] = 0
        т[3, 0] = 1
        т[5, 0] = 2
        т[::2, 2] = 6
        т[1::2, 2] = 7
        т[4, 2] = 5
        т[:, 4] = np.arange(20) % 8
        места = р.постоянные_места(т.ravel(), 5)
        # Постоянные и чередующиеся места подряд — один участок.
        self.assertEqual([{"начало": 0, "длина": 4, "символы": [0, 2, 6, 4], "доля": 0.9, "чередуется": True,
                           "символы_нечётных": [0, 2, 7, 4]}], места)
        # Ровно 0,9 в нечётных кадрах (в чётных — все) — тоже чередуется.
        т = np.tile([[1, 2, 3]], (20, 1))
        т[:, 0] = np.arange(20) % 8
        т[::2, 1], т[1::2, 1] = 6, 7
        т[3, 1] = 5
        т[:, 2] = (np.arange(20) * 3) % 8
        self.assertEqual([{"начало": 1, "длина": 1, "символы": [6], "доля": 0.9, "чередуется": True, "символы_нечётных": [7]}],
                         р.постоянные_места(т.ravel(), 3))
        # Слово в конце кадра и доля с четырьмя знаками (0,9375 → 0,938); переменный символ в начале.
        т = np.tile([[9, 1, 2, 3]], (16, 1))
        т[:, 0] = np.arange(16) % 8
        т[0, 1] = 0
        self.assertEqual([{"начало": 1, "длина": 3, "символы": [1, 2, 3], "доля": 0.938}], р.постоянные_места(т.ravel(), 4))

    def test_анализ_края(self):
        а = р.анализ(np.array([1, 0, 1], np.uint8), 3, фаза=0)
        self.assertEqual((1, [0, 0, 0, 0, 0, 1.0, 0, 0], 0.0), (а["символов"], а["частоты"], а["повторов_подряд"]))
        а = р.анализ(np.array([1, 0, 1, 1, 0, 1], np.uint8), 3, фаза=0)
        self.assertEqual((1.0, [[5, 5, 1]]), (а["повторов_подряд"], а["частые_переходы"][:1]))
        # Символы только 0…3 при M = 8: переходов 7→7 нет — матрица всё равно 8 × 8.
        символы = np.random.default_rng(1).integers(0, 4, 500)
        а = р.анализ(метки_в_биты(символы, 3), 3, фаза=0, выборка=104)
        self.assertEqual(105, а["символов"])                                    # выборка + 1 символ запаса
        self.assertEqual((8, 8), np.array(а["переходы"]).shape)
        с_ = символы[:105]
        p = np.bincount(с_, minlength=8) / 105
        self.assertEqual([round(float(x), 5) for x in p], а["частоты"])
        self.assertEqual(round(float((p ** 2).sum()), 5), а["повторов_ждать"])
        self.assertEqual(round(float((с_[:-1] == с_[1:]).sum() / 104), 5), а["повторов_подряд"])
        self.assertEqual(8, len(а["частые_переходы"]))
        пары = {}
        for x, y in zip(с_[:-1].tolist(), с_[1:].tolist(), strict=True):
            пары[(x, y)] = пары.get((x, y), 0) + 1
        лучшие = sorted(пары.items(), key=lambda кв: (-кв[1], кв[0][0] * 8 + кв[0][1]))[:8]
        self.assertEqual([[x, y, n] for (x, y), n in лучшие], а["частые_переходы"])
        # Без периода — граница символа 0 (при равной силе — первая фаза).
        self.assertEqual(0, р.анализ(метки_в_биты(символы, 3), 3)["фаза"])

    def test_кадр_по_повтору(self):
        # k = 1: на половине повтора — постоянный кусок в 7 бит (меньше байта) — кадр не половина, а весь повтор.
        rng = np.random.default_rng(3)
        кадры = []
        for _ in range(40):
            половина = lambda: np.concatenate([[1, 0, 1, 1, 0, 0, 1], rng.integers(0, 2, 13)])  # noqa: E731
            кадры.append(np.concatenate([половина(), половина()]))
        кадры = np.concatenate(кадры)
        кадры[7::40] = 1                                             # на полном повторе — длиннее (восьмой бит)
        d, места = р.кадр_по_повтору(кадры, 40, 1)
        self.assertEqual(40, d)
        d, _ = р.кадр_по_повтору(кадры, 40, 2)                       # у k = 2 байт — 4 символа: семь постоянных — кадр
        self.assertEqual(20, d)
        # k = 3: на делителе — два постоянных символа подряд (меньше байта) — не кадр.
        кадры = np.concatenate([np.concatenate([[1, 2], rng.integers(0, 8, 8), [1, 2], rng.integers(0, 8, 8)])
                                for _ in range(40)])
        self.assertEqual(20, р.кадр_по_повтору(кадры, 20, 3)[0])
        кадры = np.concatenate([np.concatenate([[1, 2, 3], rng.integers(0, 8, 7)]) for _ in range(80)])
        self.assertEqual(10, р.кадр_по_повтору(кадры, 20, 3)[0])                  # три символа — байт у k = 3

    def test_синхрослово_края(self):
        сдвиги = {в["сдвиг"] for в in р.по_синхрослову([1, 0, 1], [6, 5], 3, инверсия=False)}
        self.assertEqual({0, 3}, сдвиги)
        # При равном покрытии — больше пар выше.
        варианты = р.по_синхрослову([1, 1, 1, 1, 1, 1, 0, 0, 0], [7, 7, 0, 2], 3, инверсия=False)
        покрытие = [(в["покрыто"], в["пар"]) for в in варианты]
        self.assertEqual(sorted(покрытие, key=lambda п: (-п[0], -п[1])), покрытие)
        self.assertGreater(len(set(покрытие)), 1)

    def test_отрезки_лрп(self):
        for n_симв, ждём in ((39, [0]), (40, [0, 20]), (60, [0, 20, 40]), (100, [0, 33, 66])):
            м = р.МераЛРП(np.arange(n_симв), 3, отрезков=3, длина=60)
            with self.subTest(n_симв):
                self.assertEqual(ждём, [int(о[0]) for о in м.отрезки])
                self.assertTrue(all(len(о) == 20 for о in м.отрезки))
        self.assertEqual([0], [int(о[0]) for о in р.МераЛРП(np.arange(100), 3, отрезков=1, длина=60).отрезки])


def классы_эталон(S: np.ndarray, k: int, h: np.ndarray, от: int, до: int) -> list[tuple[int, int, float]]:
    """Этап А прямым перебором окон: (длина, начало, отрыв) всех ключей по убыванию отрыва (h — чётность по символу)."""
    F, Q = S.shape
    n = k * Q
    M = 1 << k
    пост = np.stack([(S == g).mean(axis=0) for g in range(M)]).max(axis=0) >= 0.5
    чёт = h[S].astype(np.int64)
    накоп: dict[tuple[int, int], list[float]] = {}
    for L in range(от, min(до, n // 2) + 1):
        for a in range(0, n - L + 1):
            e = a + L
            ra, re_ = a % k, e % k
            qa, qe = a // k, e // k
            if (ra and re_) or qa >= Q or (re_ and qe >= Q) or пост[qa:-(-e // k)].any():
                continue
            if not ra and not re_:
                знак, группы = 1 - 2 * (чёт[:, qa:qe].sum(axis=1) % 2), np.zeros(F, int)
            elif ra:
                знак, группы = 1 - 2 * (чёт[:, qa + 1:qe].sum(axis=1) % 2), S[:, qa]
            else:
                знак, группы = 1 - 2 * (чёт[:, qa:qe].sum(axis=1) % 2), S[:, qe]
            E = var = 0.0
            for g in set(группы.tolist()):
                в = группы == g
                E += float(знак[в].sum()) ** 2 - в.sum()
                var += 2.0 * в.sum() * (в.sum() - 1)
            ключ = (L, a % L)
            с_ = накоп.setdefault(ключ, [0.0, 0.0])
            с_[0] += E
            с_[1] += var
    итог = [(L, o, E / math.sqrt(max(1e-9, var))) for (L, o), (E, var) in накоп.items()]
    return sorted(итог, key=lambda т: (-round(т[2], 9), т[0], т[1]))           # при равном — по длине и началу


class ЭтапАTests(unittest.TestCase):
    def test_классы_как_перебор_окон(self):
        rng = np.random.default_rng(4)
        F, Q = 12, 40
        S = rng.integers(0, 8, (F, Q))
        S[:, :2] = [5, 1]                                    # синхрослово — постоянные места
        S[:6, 20] = 3                                        # ровно половина кадров — тоже постоянное место
        S[:, -1] = rng.integers(0, 6, F)                     # в последнем символе нет меток 6 и 7
        # Строки 10 бит с бита 7: чётность меток целых символов и частичного в начале (по группам) — постоянна.
        мера = р.МераСтрок(S, 3)
        h = np.array([0, 1, 1, 0, 1, 0, 0, 1], np.uint8)
        итог = мера.классы(только=[h], от=8, до=40)
        self.assertEqual(1, len(итог))
        ждём = классы_эталон(S, 3, h, 8, 40)
        лучший = ждём[0]
        self.assertEqual([0, 3, 5, 6], итог[0]["класс"])
        self.assertAlmostEqual(лучший[2], итог[0]["отрыв"], delta=0.051)
        self.assertEqual((лучший[0], лучший[1]), (итог[0]["длина"], итог[0]["начало"]))
        # Кандидаты — лучшие различные длины с их началом и отрывом; все длины (кандидатов — сколько угодно).
        различные = []
        for L, o, z in ждём:
            if all(L != d[0] for d in различные):
                различные.append((L, o, round(float(z), 1)))
        with unittest.mock.patch.object(р, "КАНДИДАТОВ_ДЛИН", 1000):
            ходы = []
            итог = мера.классы(только=[h], от=8, до=40, ход=lambda д, т: ходы.append((д, т)))
        self.assertEqual([(1.0, "классы чётности: 1 из 1")], ходы)
        self.assertEqual(различные, [tuple(к) for к in итог[0]["кандидаты"]])
        # Все классы (без «только»): 35 у ФМ-8, по убыванию отрыва, у каждого — свой перебор.
        все = мера.классы(от=8, до=40)
        self.assertEqual(35, len(все))
        self.assertEqual(sorted((в["отрыв"] for в in все), reverse=True), [в["отрыв"] for в in все])
        for в in все[:3]:
            h_ = np.array([0 if v in в["класс"] else 1 for v in range(8)], np.uint8)
            self.assertAlmostEqual(классы_эталон(S, 3, h_, 8, 40)[0][2], в["отрыв"], delta=0.051)

    def test_переменные_места(self):
        S = np.random.default_rng(1).integers(0, 8, (10, 30))
        S[:, 20] = 4                                          # постоянное место в середине
        S[:5, 25] = 2                                         # ровно половина — тоже
        мера = р.МераСтрок(S, 3)
        a = np.array([0, 0, 3, 57, 58, 60, 62, 75, 78])
        e = np.array([6, 60, 30, 60, 61, 63, 70, 78, 90])
        ждём = [True, True, True, True, False, False, False, False, True]
        self.assertEqual(ждём, мера._переменные(a, e).tolist())


class ПомощникиПоискаTests(unittest.TestCase):
    def test_классы_и_равноценные(self):
        т = р._по_классу([0, 1, 4, 7], 8)
        self.assertEqual((576, 8), т.shape)
        self.assertEqual(576, len({tuple(x) for x in т.tolist()}))
        вес = [bin(v).count("1") % 2 for v in range(8)]
        self.assertTrue(all(вес[x[v]] == (0 if v in (0, 1, 4, 7) else 1) for x in т.tolist() for v in range(8)))
        self.assertEqual((4, 4), р._по_классу([0, 3], 4).shape)
        self.assertEqual([[2, 0, 1, 3], [3, 1, 0, 2], [0, 2, 3, 1], [1, 3, 2, 0]], р.равноценные([2, 0, 1, 3], 4))
        п = р.по_классам_инверсии(8)
        self.assertEqual((5040, 8), п.shape)
        self.assertTrue((п[:, 0] == 0).all())
        self.assertEqual(5040, len({tuple(x) for x in п.tolist()}))
        self.assertEqual([[0, 1, 0, 1], [0, 0, 1, 1]], р.чётность_меток(np.array([[0, 1, 3, 2], [3, 0, 1, 2]]), 4).tolist())
        self.assertEqual(40320, len(р.все_перестановки(8)))
        self.assertEqual([[0, 1], [1, 0]], р.все_перестановки(2).tolist())

    def test_старты_и_основы(self):
        основы = [[0, 1, 2, 3, 4, 5, 6, 7], [7, 6, 5, 4, 3, 2, 1, 0]]
        ждём = set()
        for о in основы:
            for п in itertools.permutations(range(3)):
                for c in range(8):
                    ждём.add(tuple(int("".join(format(о[v], "03b")[i] for i in п), 2) ^ c for v in range(8)))
        старты = р.старты_группы(3, основы)
        self.assertEqual(ждём, {tuple(x) for x in старты.tolist()})
        self.assertEqual(len(ждём), len(старты))
        часть = р.старты_группы(3, основы, до=10, сид=4)
        self.assertEqual(10, len(часть))
        self.assertEqual(10, len({tuple(x) for x in часть.tolist()}))                 # выборка без повторов
        self.assertTrue({tuple(x) for x in часть.tolist()} <= ждём)
        self.assertEqual(часть.tolist(), р.старты_группы(3, основы, до=10, сид=4).tolist())
        self.assertNotEqual(часть.tolist(), р.старты_группы(3, основы, до=10, сид=5).tolist())
        self.assertEqual(48, len(р.старты_группы(3)))                          # без основ — тождественная
        # Основы ФМ-8: повороты и отражения по кругу в коде Грея и натуральном — прямым счётом.
        грей = [n ^ (n >> 1) for n in range(8)]
        ждём = {tuple(range(8))}
        for код in (грей, list(range(8))):
            for r in range(8):
                for отр in (False, True):
                    ждём.add(tuple(код[((-код.index(v) if отр else код.index(v)) + r) % 8] for v in range(8)))
        self.assertEqual(ждём, {tuple(x) for x in р.основы_плоскостей(3)})
        # У КАМ (чётное k) — ещё симметрии квадрата; ФМ-128 и дальше — без поворотов по кругу.
        # ФМ-16: те же 64 по кругу и ещё симметрии квадрата (k чётное) — больше, чем по кругу.
        грей4 = [n ^ (n >> 1) for n in range(16)]
        по_кругу = {tuple(код[((-код.index(v) if отр else код.index(v)) + r) % 16] for v in range(16))
                    for код in (грей4, list(range(16))) for r in range(16) for отр in (False, True)}
        основы16 = {tuple(x) for x in р.основы_плоскостей(4)}
        self.assertTrue(по_кругу < основы16)
        грей6 = [n ^ (n >> 1) for n in range(64)]
        self.assertIn([грей6[(грей6.index(v) + 1) % 64] for v in range(64)], р.основы_плоскостей(6))    # ФМ-64 — по кругу

    def test_перебор_статистика(self):
        class Мера:
            имя = "сумма"

            def оценить(self, таблицы):
                return (np.asarray(таблицы) * np.arange(len(таблицы[0]))).sum(axis=1).astype(float)

        self.assertEqual(40320, р.перебор(Мера(), 8, срок=100)["оценено"])     # срок не вышел — оценены все пачки
        р_ = р.перебор(Мера(), 4, лучших=3)
        все = р.все_перестановки(4)
        меры = (все * np.arange(4)).sum(axis=1).astype(float)
        медиана = float(np.median(меры))
        разброс = 1.4826 * float(np.median(np.abs(меры - медиана)))
        self.assertEqual([[0, 1, 2, 3], [0, 1, 3, 2], [0, 2, 1, 3]], р_["таблицы"])      # при равной — по номеру
        self.assertEqual([14.0, 13.0, 13.0], р_["меры"])
        self.assertEqual((round(медиана, 3), round(разброс, 4), 24, 24), (р_["медиана"], р_["разброс"], р_["оценено"], р_["всего"]))
        self.assertEqual(round((14 - медиана) / разброс, 1), р_["отрыв"])
        # Все меры равны — разброс нулевой: отрыв считается от 1e-9, а не делением на ноль.
        class Ровно:
            имя = "ровно"

            def оценить(self, таблицы):
                return np.ones(len(таблицы))

        р_ = р.перебор(Ровно(), 3)
        self.assertEqual((0.0, 0.0), (р_["разброс"], р_["отрыв"]))

        class Дробно:
            имя = "дробно"

            def оценить(self, таблицы):
                return np.asarray(таблицы)[:, 0] * 0.12345 + np.asarray(таблицы)[:, 1] * 0.00001

        р_ = р.перебор(Дробно(), 3, лучших=2)
        все = np.array(list(itertools.permutations(range(3))))
        значения = все[:, 0] * 0.12345 + все[:, 1] * 0.00001
        self.assertEqual(round(1.4826 * float(np.median(np.abs(значения - np.median(значения)))), 4), р_["разброс"])
        self.assertEqual([[2, 1, 0], [2, 0, 1]], р_["таблицы"])
        self.assertEqual([0.247, 0.247], р_["меры"])
        self.assertEqual(round(float(np.median([0.00001, 0.00002, 0.12345, 0.12347, 0.2469, 0.24691])), 3), р_["медиана"])

    def test_местный_поиск_счёт_и_ход(self):
        ходы = []
        ит = р.местный_поиск(lambda т: np.zeros(len(т)), np.arange(8)[None, :], срок=30, ход=lambda д, т: ходы.append((round(д, 3), т)))
        self.assertEqual(list(range(8)), ит["таблица"])
        self.assertEqual((0.0, 0.0), (ит["мера"], ит["мера_стартов"]))
        self.assertEqual(1 + 28 + 3 * (1 + 28), ит["оценено"])
        self.assertTrue(0 <= ит["время"] < 30)
        self.assertEqual([(0.25, "старт 1 из 1"), (0.5, "отжиг 1 из 3"), (0.75, "отжиг 2 из 3"), (1.0, "отжиг 3 из 3")], ходы)
        # Отжиг уходит из местного максимума: мера — совпадения с целью, кроме «ловушки» (обмен двух меток).
        цель = np.arange(8)
        ловушка = цель.copy()
        ловушка[[0, 1]] = [1, 0]

        def мера(т):
            т = np.asarray(т)
            совпало = (т == цель).sum(axis=1).astype(float)
            return np.where((т == ловушка).all(axis=1), 7.5, совпало)

        ит = р.местный_поиск(мера, ловушка[None, :], срок=30, отжиг=20, сид=2)
        self.assertEqual(цель.tolist(), ит["таблица"])
        self.assertEqual((8.0, 7.5), (ит["мера"], ит["мера_стартов"]))
        # Два старта к двум равным вершинам: при равной мере остаётся найденная от лучшего старта.
        A, B = np.arange(8), np.arange(8)[::-1].copy()

        def две(т):
            т = np.asarray(т)
            return np.maximum((т == A).sum(axis=1), (т == B).sum(axis=1)).astype(float)

        старт_A, старт_B = A.copy(), B.copy()
        старт_A[[0, 1]] = старт_A[[1, 0]]                       # 6 совпадений с A
        старт_B[[0, 1, 2]] = старт_B[[1, 2, 0]]                 # 5 совпадений с B
        ит = р.местный_поиск(две, np.array([старт_B, старт_A]), срок=30, отжиг=0)
        self.assertEqual((A.tolist(), 8.0, 6.0), (ит["таблица"], ит["мера"], ит["мера_стартов"]))
        # Срок вышел — подъёма нет, мера старта.
        ит = р.местный_поиск(мера, ловушка[None, :], срок=0.0)
        self.assertEqual((ловушка.tolist(), 7.5), (ит["таблица"], ит["мера"]))

    def test_итог_в_словарь(self):
        и = р.Итог([1, 0], 3.5, "как", ["x"], [[1, 0]], {"время": 1.0})
        self.assertEqual({"таблица": [1, 0], "мера": 3.5, "как": "как", "подробно": ["x"], "равноценные": [[1, 0]],
                          "время": 1.0}, и.в_словарь())


class МераСтрокТочноTests(unittest.TestCase):
    def test_оценить_как_прямой_счёт(self):
        rng = np.random.default_rng(6)
        S = rng.integers(0, 8, (20, 50))
        S[:, 10:12] = [3, 4]
        мера = р.МераСтрок(S, 3)
        таблицы = np.array([rng.permutation(8) for _ in range(3)])
        n = 150
        for длина, начало in ((20, 3), (17, 0), (40, 39), (64, 5)):
            места = [a for a in range(начало % длина, n - длина + 1, длина)
                     if not ((a // 3 <= 11) and (-(-(a + длина) // 3) > 10))]           # окна через символы 10, 11 — нет
            ждём = []
            for т in таблицы:
                биты = р.в_биты(т[S], 3).reshape(20, -1)
                перекос = [(1 - 2 * (биты[:, a:a + длина].sum(axis=1) % 2).mean()) ** 2 - 1 / 20 for a in места]
                ждём.append(sum(перекос) * 20 / math.sqrt(2 * len(места)))
            with self.subTest(длина=длина, начало=начало):
                np.testing.assert_allclose(ждём, мера.оценить(таблицы, длина, начало), rtol=1e-9)
        # Мест нет (длина больше кадра) — ноль.
        self.assertEqual([0.0], мера.оценить(таблицы[:1], 151, 0).tolist())

    def test_все_длины_как_прямой_счёт(self):
        rng = np.random.default_rng(7)
        S = rng.integers(0, 8, (6, 30))
        т = rng.permutation(8)
        биты = р.в_биты(т[S], 3).reshape(6, -1)
        n = 90
        лучшее = (0.0, 0, 0)
        for L in range(8, 46):
            J = (n + 1 - L) // L
            перекос = [(1 - 2 * (биты[:, a:a + L].sum(axis=1) % 2).mean()) ** 2 for a in range(J * L)]
            по_началу = np.array(перекос).reshape(J, L).mean(axis=0)
            о = int(np.argmax(по_началу))
            м = (по_началу[о] - 1 / 6) * 6 * math.sqrt(J / 2)
            if м > лучшее[0]:
                лучшее = (м, L, о)
        [итог] = р.МераСтрок(S, 3).оценить_все_длины(т[None, :])
        np.testing.assert_allclose(лучшее, итог, rtol=1e-9)
        # Строка ровно в полкадра (45 бит) — наибольшая длина: находится; 46 бит — длиннее полкадра: не ищется.
        for L, ждём_L in ((45, 45), (46, None)):
            биты_ = rng.integers(0, 2, (40, 90)).astype(np.uint8)
            биты_[:, L - 1] = биты_[:, :L - 1].sum(axis=1) % 2
            S_ = р.в_символы(биты_.ravel(), 3).reshape(40, 30)
            [(м, L_, о)] = р.МераСтрок(S_, 3).оценить_все_длины(np.arange(8)[None, :])
            if ждём_L:
                self.assertEqual((ждём_L, 0), (L_, о))
            else:
                self.assertNotEqual(46, L_)
        # Один кадр: перекос всегда 1, мера — ноль у всех длин: остаётся начальное (0, 0, 0).
        self.assertEqual([(0.0, 0, 0)], р.МераСтрок(S[:1], 3).оценить_все_длины(т[None, :]))

    def test_символы_кадров_края(self):
        rng = np.random.default_rng(8)
        длина = 30
        биты = rng.integers(0, 2, 8 * длина).astype(np.uint8)
        начала = list(range(0, 8 * длина, длина))
        кадры, сдвиг = р.символы_кадров(биты, начала, длина, 3, 0)          # последний кадр — ровно до конца
        self.assertEqual(((8, 10), 0), (кадры.shape, сдвиг))
        self.assertEqual(р.в_символы(биты, 3).reshape(8, 10).tolist(), кадры.tolist())
        self.assertIsNone(р.символы_кадров(биты[:-1], начала, длина, 3, 0))    # семь кадров — мало
        # Повтор кадра (заполнение) не берётся: из девяти с повтором — восемь; из восьми с повтором — отказ.
        с_повтором = np.concatenate([биты, биты[:длина]])
        кадры, _ = р.символы_кадров(с_повтором, начала + [8 * длина], длина, 3, 0)
        self.assertEqual(8, len(кадры))
        повтор8 = np.concatenate([биты[:7 * длина], биты[:длина]])
        self.assertIsNone(р.символы_кадров(повтор8, начала, длина, 3, 0))
        # Граница символа — бит 1 файла: у кадров, начинающихся с бита 0 (mod 3), символы — с бита 1 кадра.
        биты = rng.integers(0, 2, 12 * длина + 5).astype(np.uint8)
        начала = [0, 31] + list(range(60, 11 * длина, длина))                  # кадр с бита 31 — другой фазы
        кадры, сдвиг = р.символы_кадров(биты, начала, длина, 3, 1)
        self.assertEqual(1, сдвиг)
        годные = [н for н in начала if н % 3 == 0 and н + 1 + 30 <= len(биты)]       # Q = 29 // 3 + 1 = 10 символов
        self.assertEqual(len(годные), len(кадры))
        self.assertEqual([р.в_символы(биты[н + 1:н + 31], 3).tolist() for н in годные], кадры.tolist())


class ВходIQКраяTests(unittest.TestCase):
    def test_выровнять_несимметричное(self):
        """Созвездие без симметрии отражения: инверсия и поворот находятся (поворот — к идеалу, против часовой)."""
        идеал = np.array([1.0 + 0j, 0.2 + 1j, -1.0 + 0.1j, -0.4 - 1j])
        идеал = (идеал - идеал.mean()) / math.sqrt(np.mean(np.abs(идеал - идеал.mean()) ** 2))
        for инверсия, φ in ((False, 37.3), (True, 211.9), (False, 359.9)):
            with self.subTest(инверсия=инверсия, φ=φ):
                # Отсчёты: идеал повёрнут на −φ (и сопряжён) — снимающий поворот φ.
                центры = идеал * cmath.exp(-1j * math.radians(φ))
                центры = np.conj(центры) if инверсия else центры
                в = iq.выровнять(центры, np.ones(4), идеал)
                self.assertEqual(инверсия, в["инверсия"])
                self.assertAlmostEqual(0.0, ((в["поворот"] - φ) + 180) % 360 - 180, delta=0.01)
                self.assertLess(в["ошибка"], 1e-4)
        # По модулю шага; у самого шага — ноль.
        идеал = np.exp(1j * np.pi / 2 * np.arange(4))
        в = iq.выровнять(идеал * cmath.exp(-1j * math.radians(100)), np.ones(4), идеал, шаг=90)
        self.assertAlmostEqual(10.0, в["поворот"], delta=0.01)
        в = iq.выровнять(идеал * cmath.exp(-1j * math.radians(89.9999999)), np.ones(4), идеал, шаг=90)
        self.assertEqual(0.0, в["поворот"])
        # Веса: точка с нулевым весом не влияет.
        в = iq.выровнять(np.append(идеал * cmath.exp(-1j * math.radians(20)), 5 + 5j), np.array([1, 1, 1, 1, 0]), идеал, шаг=90)
        self.assertAlmostEqual(20.0, в["поворот"], delta=0.01)
        self.assertLess(в["ошибка"], 1e-4)

    def test_привести_и_облако(self):
        центры = np.array([1 + 1j, 3 + 1j])
        self.assertEqual((2 + 1j, 1.0), iq.привести(None, центры, np.array([5, 5])))
        с_, м = iq.привести(None, центры, np.array([3, 1]))
        self.assertAlmostEqual(1.5 + 1j, с_)
        self.assertAlmostEqual(math.sqrt(0.75 * 0.25 + 0.25 * 2.25), м)
        self.assertEqual((1 + 1j, 1.0), iq.привести(None, np.array([1 + 1j]), np.array([4])))   # разброс ноль — масштаб 1
        rng = np.random.default_rng(2)
        z = np.exp(1j * np.pi / 2 * rng.integers(0, 4, 20000)) + 0.05 * (rng.standard_normal(20000) + 1j * rng.standard_normal(20000))
        о = iq.облако(z, точек=4)
        self.assertEqual((4, [], iq.ОБЛАКО_ДО), (о["точек"], о["оценки"], len(о["показ"])))
        self.assertEqual(4, len(о["центры"]))
        self.assertAlmostEqual(1.0, float(np.sum(np.abs(о["центры"]) ** 2 * о["веса"]) / np.sum(о["веса"])), places=6)
        for точек in (3, 2048, 1):
            with self.assertRaises(ValueError):
                iq.облако(z, точек=точек)
        мало = iq.облако(z[:100])
        self.assertEqual([2, 4, 8], [о_["точек"] for о_ in мало["оценки"]])      # K · 8 не больше отсчётов
        self.assertEqual(100, len(мало["показ"]))

    def test_решения_кусками_и_порядок(self):
        rng = np.random.default_rng(3)
        идеал = np.array([1, 1j, -1, -1j])
        z = идеал[rng.integers(0, 4, 70000)]
        метки = iq.по_плоскости(z, идеал, np.array([3, 2, 1, 0]))
        self.assertEqual(70000, len(метки))
        self.assertTrue(np.array_equal(метки, 3 - np.abs(z[:, None] - идеал[None, :]).argmin(axis=1)))
        кл = iq.по_кластерам(z, идеал)
        self.assertTrue(np.array_equal(кл, np.array([2, 0, 1, 3])[np.abs(z[:, None] - идеал[None, :]).argmin(axis=1)]))
        self.assertEqual([0], iq.порядок_чтения(np.array([2 + 2j])).tolist())
        # Строка — с допуском четверти наименьшего расстояния: чуть ниже — та же строка, слева направо.
        self.assertEqual([1, 0, 2], iq.порядок_чтения(np.array([1 + 1.1j, 0 + 1.2j, 0.5 + 0j])).tolist())

    def test_k_средних_поправка_и_пустые(self):
        # Три различных значения и четыре кластера: пустой кластер уходит в дальний отсчёт — без NaN.
        z = np.array([0j, 10 + 0j, 10j] * 30)
        ц, м = iq.k_средних(z, 4, запусков=1)
        self.assertFalse(np.isnan(ц).any())
        self.assertEqual(set(range(len(ц))) >= set(м.tolist()), True)
        # Поправка: два центра на одной точке, один — на двух: слить и разделить.
        rng = np.random.default_rng(4)
        точки = np.array([0, 4, 4j, 4 + 4j])
        z = точки[rng.integers(0, 4, 4000)] + 0.2 * (rng.standard_normal(4000) + 1j * rng.standard_normal(4000))
        плохие = np.array([0 - 0.3j, 0 + 0.3j, 4j, 2 + 4j])
        м = np.abs(z[:, None] - плохие[None, :]).argmin(axis=1)
        разброс = float(np.mean(np.abs(z - плохие[м]) ** 2))
        ц, м, р_ = iq._поправить(z, плохие, м, разброс, np.random.default_rng(1), 60)
        self.assertEqual(sorted(np.round(точки).tolist(), key=lambda c: (c.real, c.imag)),
                         sorted(np.round(ц).tolist(), key=lambda c: (c.real, c.imag)))
        self.assertLess(р_, разброс)
        # Уже хорошие центры поправка не трогает.
        м = np.abs(z[:, None] - точки[None, :]).argmin(axis=1)
        ц2, _, р2 = iq._поправить(z, точки.astype(complex), м, float(np.mean(np.abs(z - точки[м]) ** 2)), np.random.default_rng(1), 60)
        self.assertTrue(np.array_equal(точки, ц2))

    def test_wav_pcm32_и_пропуск(self):
        x = np.array([100000, -200000, 300000, -400000] * 8, "<i4")
        буфер = io.BytesIO()
        with wave.open(буфер, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(4)
            w.setframerate(8000)
            w.writeframes(x.tobytes())
        z, описание = iq.прочитать(буфер.getvalue())
        np.testing.assert_allclose(x[0::2] + 1j * x[1::2], z)
        self.assertIn("32 бит, PCM", описание)
        y = np.arange(40, dtype="i1")
        self.assertEqual(iq.прочитать(y.tobytes(), "int8")[0].tolist(), iq.прочитать(y.tobytes(), "int8", пропуск=-5)[0].tolist())


class ETLКраяTests(unittest.TestCase):
    def test_вид_и_синонимы(self):
        кольца = "000         001\n   010   011\n\n   110   111\n100         101\n"
        for строка in ("# вид: общее", "; ВИД = общий", "# вид: APSK.", "# вид: АФМ;", "#вид:общее"):
            with self.subTest(строка):
                self.assertEqual("общее", мд.разобрать_etl(строка + "\n" + кольца).вид)
        self.assertEqual("ФМ", мд.разобрать_etl("# вид: фм\n  01  11\n\n  00  10\n").вид)
        self.assertEqual("КАМ", мд.разобрать_etl("# вид: КАМ\n" + "\n".join(" ".join(format(4 * r + c, "04b") for c in range(4))
                                                                             for r in range(4))).вид)
        # Примечание с «1» после знака — не метка; строка с меткой после «#» — тоже примечание.
        с_ = мд.разобрать_etl("# 8PSK r1=1\n# 111 не точка\n" + "     101 001\n100         000\n110         010\n     111 011\n")
        self.assertEqual(8, с_.M)

    def test_координаты_разделители(self):
        for строка in ("00 = 1 0", "00: 1, 0", "00=1;0", "00 = 1 @ 0", "00 = 1 ∠ 0°", "00 = 1.0e0 0"):
            with self.subTest(строка):
                с_ = мд.разобрать_etl(строка + "\n01 = 0 1\n10 = -1 0\n11 = 0 -1")
                self.assertEqual(("00", 1.0, 0.0), (с_.точки[0][0], round(с_.точки[0][1], 12), round(с_.точки[0][2], 12)))
                self.assertTrue(с_.точные)
        полярно = мд.разобрать_etl("0 = 2 @ 90\n1 = 2 @ -90")
        self.assertAlmostEqual(2.0, полярно.точки[0][2])
        self.assertAlmostEqual(-2.0, полярно.точки[1][2])

    def test_выгрузка_общей(self):
        с_ = мд.разобрать_etl("# вид: общее\n00 = 0 0\n01 = 1 0\n10 = 0.5 0.866\n11 = 5 5")
        текст = мд.в_etl(с_)
        self.assertTrue(текст.startswith("# плоскость\n# вид: общее\n"))
        с2 = мд.Созвездие("имя", с_.точки, точные=True, вид="общее")
        self.assertTrue(мд.в_etl(с2).startswith("# имя\n# вид: общее\n"))
        п = мд.плоскость(с_)
        self.assertEqual(п.метки, мд.плоскость(мд.разобрать_etl(текст)).метки)
        # Порядок точек общей плоскости — как читают: сверху вниз, слева направо.
        self.assertEqual([3, 2, 0, 1], list(п.метки))
        # Соседи — ближе 1,25 наименьшего расстояния каждой: треугольник — все три пары, дальняя точка — ни с кем
        # (её наименьшее расстояние велико, но пара ей не ближе 1,25 наименьшего у партнёра).
        self.assertEqual([(1, 2), (1, 3), (2, 3)], list(п.соседи))


class ПоискСтрокTests(unittest.TestCase):
    def test_без_строк_и_ход(self):
        rng = np.random.default_rng(9)
        self.assertIsNone(р.поиск_строк(rng.integers(0, 8, (40, 60)), 3, срок=None, ход=None, лучших=8))
        поток, длина = кадры_строк(64)
        кадры, _ = р.символы_кадров(поток, list(range(0, len(поток) - длина + 1, длина)), длина, 3, 0)
        ходы = []
        итог = р.поиск_строк(кадры, 3, срок=None, ход=lambda д, т: ходы.append((round(д, 4), т)), лучших=8)
        self.assertEqual(8, len(итог["таблицы"]))
        self.assertEqual([(round(0.8 * (i + 1) / 35, 4), f"классы чётности: {i + 1} из 35") for i in range(35)], ходы[:35])
        self.assertEqual((1.0, "перестановки лучшего класса"), ходы[-1])
        self.assertTrue(all(0.8 < д <= 1.0 for д, _ in ходы[35:]))
        self.assertEqual("35 классов чётности × 576 перестановок (все 40320)", итог["перебрано"])
        # Лучших — сколько просили (равноценные — классами по 8, но не больше просимого и не меньше M).
        self.assertEqual(8, len(р.поиск_строк(кадры, 3, срок=None, ход=None, лучших=3)["таблицы"]))
        self.assertEqual(8, len(р.поиск_строк(кадры, 3, срок=None, ход=None, лучших=16)["таблицы"]))   # равных — один класс

    def test_итог_точно(self):
        поток, длина = кадры_строк(64, ошибок=0.001, сид=5)
        кадры, _ = р.символы_кадров(поток, list(range(0, len(поток) - длина + 1, длина)), длина, 3, 0)
        ходы = []
        итог = р.поиск_строк(кадры, 3, срок=None, ход=lambda д, т: ходы.append(д), лучших=8)
        кл = итог["класс"]
        self.assertEqual([round(0.8 + 0.2 * (i + 1) / len(кл["кандидаты"]), 6) for i in range(len(кл["кандидаты"]))],
                         [round(д, 6) for д in ходы[35:]])
        # Прямой счёт меры строк у всех таблиц класса на найденной длине: лучшая, равные ей и следующая за ними.
        F = len(кадры)
        L, o = кл["длина"], кл["начало"]
        мера = р.МераСтрок(кадры, 3)
        места = [a for a in range(o % L, 600 - L + 1, L) if мера._переменные(np.array([a]), np.array([a + L]))[0]]

        def прямо(т):
            биты = р.в_биты(np.asarray(т)[кадры], 3).reshape(F, -1)
            return sum((1 - 2 * (биты[:, a:a + L].sum(axis=1) % 2).mean()) ** 2 - 1 / F for a in места) * F / math.sqrt(2 * len(места))

        таблицы = р._по_классу(кл["класс"], 8)
        меры = np.array([прямо(т) for т in таблицы])
        self.assertEqual(round(float(меры.max()), 1), итог["оценка"])
        равные = меры >= 0.999 * меры.max()
        self.assertEqual(4, int(равные.sum()))                              # класс x ⊕ c с чётными c
        self.assertEqual(round(float(меры[~равные].max()), 1), итог["следующая"])
        self.assertEqual(sorted(map(tuple, итог["таблицы"])),
                         sorted({tuple(x) for т in таблицы[равные] for x in р.равноценные(т, 8)}))
        все = мера.классы()
        self.assertEqual((все[0]["класс"], все[1]), (кл["класс"], итог["второй_класс"]))


    def test_один_класс_по_сроку(self):
        """Срок вышел после первого класса (0, 1, 2, 3): он и верный — второго класса нет."""
        поток, длина = кадры_строк(64)
        π = [0, 4, 6, 1, 7, 2, 3, 5]                               # истинные чётного веса {0, 3, 5, 6} → принятые {0, 1, 2, 3}
        принятые = передать(поток, 3, π)
        кадры, _ = р.символы_кадров(принятые, list(range(0, len(принятые) - длина + 1, длина)), длина, 3, 0)
        итог = р.поиск_строк(кадры, 3, срок=0.0, ход=None, лучших=8)
        self.assertEqual([0, 1, 2, 3], итог["класс"]["класс"])
        self.assertIsNone(итог["второй_класс"])
        self.assertIn(обратная(π), итог["таблицы"])


class WAVTests(unittest.TestCase):
    def test_wav_битые_заголовки(self):
        def riff(*куски):
            тело = b"WAVE" + b"".join(имя + struct.pack("<I", len(данные)) + данные + (b"\x00" if len(данные) % 2 else b"")
                                      for имя, данные in куски)
            return b"RIFF" + struct.pack("<I", len(тело)) + тело
        x = np.array([1, 2, 3, 4] * 8, "<i2").tobytes()
        pcm = struct.pack("<HHIIHH", 1, 2, 8000, 32000, 4, 16)
        for данные, ждём in (
                (b"RIFF\x00\x00\x00\x00WAVX" + b"\x00" * 20, "не WAV: нет заголовка RIFF/WAVE"),
                (b"RIFF1234WAV", "не WAV: нет заголовка RIFF/WAVE"),
                (riff((b"fmt ", pcm[:15]), (b"data", x)), "WAV без блока fmt или data"),
                (riff((b"fmt ", pcm), (b"data", b"")), "отсчётов 0: для облака нужно хотя бы 16"),
                (riff((b"fmt ", struct.pack("<HHIIHH", 0xFFFE, 2, 8000, 32000, 4, 16) + b"\x00" * 9), (b"data", x)),
                 "WAV: формат 65534, 16 бит — поддерживаются PCM 8/16/24/32 и float32")):
            with self.subTest(ждём), self.assertRaises(ValueError) as о:
                iq.прочитать(данные, "wav")
            self.assertEqual(ждём, str(о.exception))
        # PCM с добавкой в fmt (26 байт) — подформат не читается: это не WAVE_FORMAT_EXTENSIBLE.
        z, _ = iq.прочитать(riff((b"fmt ", pcm + b"\x03\x00" * 5), (b"data", x)))
        self.assertEqual([1 + 2j, 3 + 4j], z[:2].tolist())
        # Расширенный ровно в 26 байт — подформат (PCM) читается.
        ext = struct.pack("<HHIIHH", 0xFFFE, 2, 8000, 32000, 4, 16) + struct.pack("<HHIH", 8, 16, 3, 1)
        self.assertEqual(26, len(ext))
        self.assertEqual([1 + 2j, 3 + 4j], iq.прочитать(riff((b"fmt ", ext), (b"data", x)))[0][:2].tolist())

    def test_wav_хвосты_и_форматы(self):
        def riff(fmt, данные):
            тело = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(данные)) + данные
            return b"RIFF" + struct.pack("<I", len(тело)) + тело
        z = np.array([1000 - 2000j, -3000 + 400j] * 10)
        x = np.empty(2 * len(z))
        x[0::2], x[1::2] = z.real, z.imag
        for тег, бит, данные, ждём in (
                (3, 32, x.astype("<f4").tobytes(), z),
                (1, 16, x.astype("<i2").tobytes(), z),
                (1, 32, x.astype("<i4").tobytes(), z),
                (1, 24, b"".join(int(v).to_bytes(3, "little", signed=True) for v in x), z),
                (1, 8, (np.clip(x // 256, -128, 127) + 128).astype("u1").tobytes(),
                 np.clip(x // 256, -128, 127)[0::2] + 1j * np.clip(x // 256, -128, 127)[1::2])):
            fmt = struct.pack("<HHIIHH", тег, 2, 8000, 8000 * бит // 4, бит // 4, бит)
            for хвост in {b"", b"\x01", b"\x01" * (бит // 4 - 1)}:                  # короче кадра из двух отсчётов
                with self.subTest(тег=тег, бит=бит, хвост=len(хвост)):
                    np.testing.assert_allclose(ждём, iq.прочитать(riff(fmt, данные + хвост))[0])
        with self.assertRaises(ValueError) as о:
            iq.прочитать(riff(struct.pack("<HHIIHH", 3, 2, 8000, 48000, 6, 24), b"\x00" * 60))
        self.assertEqual("WAV: формат 3, 24 бит — поддерживаются PCM 8/16/24/32 и float32", str(о.exception))
        только_данные = b"WAVE" + b"data" + struct.pack("<I", 8) + b"\x00" * 8
        with self.assertRaises(ValueError) as о:
            iq.прочитать(b"RIFF" + struct.pack("<I", len(только_данные)) + только_данные)
        self.assertEqual("WAV без блока fmt или data", str(о.exception))

    def test_wav_края_значений(self):
        # 24 бита: 2^22 — положительное, −2^23 — отрицательное (граница знака).
        значения = [1 << 22, -(1 << 23), (1 << 23) - 1, -1] * 8
        данные = b"".join(int(v).to_bytes(3, "little", signed=True) for v in значения)
        fmt = struct.pack("<HHIIHH", 1, 2, 8000, 48000, 6, 24)
        тело = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(данные)) + данные
        z, _ = iq.прочитать(b"RIFF" + struct.pack("<I", len(тело)) + тело)
        self.assertEqual([(1 << 22) - 1j * (1 << 23), ((1 << 23) - 1) - 1j], z[:2].tolist())
        # Описание int8 — без порядка байт; 16 отсчётов — можно, 15 — мало.
        self.assertEqual("int8, I и Q чередованием", iq.прочитать(bytes(32), "int8")[1])
        with self.assertRaises(ValueError):
            iq.прочитать(bytes(30), "int8")


# -- до 1024 точек: вход I/Q, разметка вслепую, анализ ------------------------------------------------

def облако_кам(имя: str, n: int, оср: float, поворот: float, инверсия: bool, сид: int = 5):
    """Отсчёты созвездия ``имя`` с ОСШ (Es/N0, дБ), поворотом и инверсией; (отсчёты, номера точек, точки, метки)."""
    с_ = мд.найти(имя)
    z0 = np.array([complex(x, y) for _, x, y in с_.точки])
    z0 = (z0 - z0.mean()) / math.sqrt(np.mean(np.abs(z0 - z0.mean()) ** 2))
    метки = np.array([int(м, 2) for м, _, _ in с_.точки])
    rng = np.random.default_rng(сид)
    номера = rng.integers(0, len(z0), n)
    σ = math.sqrt(1 / (2 * 10 ** (оср / 10)))
    z = z0[номера] * cmath.exp(1j * math.radians(поворот))
    z = np.conj(z) if инверсия else z
    z = z + σ * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    return 3000 * z + (40 - 25j), номера, z0, метки


class До1024IQTests(unittest.TestCase):
    def test_ближайшие_по_ячейкам_точно(self):
        """Ячейки дают ровно то же, что полный перебор: и в облаке, и для выбросов далеко за краем, и у неровных точек."""
        rng = np.random.default_rng(1)
        for точки in (np.array([complex(x, y) for _, x, y in мд.найти("DOCSIS 3.1 КАМ512 (крест)").точки]),
                      rng.standard_normal(700) + 1j * rng.standard_normal(700),
                      np.exp(2j * np.pi * np.arange(256) / 256)):
            z = np.concatenate([точки[rng.integers(0, len(точки), 5000)] + 0.3 * (rng.standard_normal(5000) + 1j * rng.standard_normal(5000)),
                                40 * (rng.standard_normal(300) + 1j * rng.standard_normal(300)), точки[:50]])
            прямо = np.array([int(np.argmin(np.abs(точки - т))) for т in z])
            self.assertTrue(np.array_equal(прямо, iq._Ячейки(точки).ближайшие(z)))
            self.assertTrue(np.array_equal(прямо, iq._ближайшие(z, точки)))
        # Совпавшие точки (медиана расстояний — ноль) и одна точка — полный перебор.
        self.assertEqual([0, 0], iq._Ячейки(np.array([1 + 1j] * 200)).ближайшие(np.array([0j, 5 + 5j])).tolist())
        self.assertIsNone(iq._Ячейки(np.array([2j])).таблица)

    def test_облако_1024_и_512(self):
        for имя, оср, поворот, инверсия in (("DOCSIS 3.1 КАМ1024", 40, 17.3, False), ("КАМ1024 Грей", 40, 61.0, True),
                                             ("DOCSIS 3.1 КАМ512 (крест)", 37, 200.0, True)):
            with self.subTest(имя=имя):
                z, номера, z0, метки = облако_кам(имя, 150000, оср, поворот, инверсия)
                сырьё, _ = iq.прочитать(в_int16(z, 1.0), "int16")
                M = len(z0)
                начало = time.monotonic()
                о = iq.облако(сырьё, точек=M)
                self.assertLess(time.monotonic() - начало, 30)
                self.assertEqual(M, о["точек"])
                self.assertGreater(о["отделимость"], 4)
                п = мд.плоскость(мд.найти(имя))
                идеал = np.array([complex(x, y) for x, y in п.xy])
                в = iq.выровнять(о["центры"], о["веса"], идеал, шаг=п.шаг)
                # Квадрат и крест симметричны отражению: инверсия по облаку неотличима от поворота — сверяется то, что
                # центры легли на точки, и поворот при той инверсии, что совпала.
                self.assertLess(в["ошибка"], 0.01)
                if в["инверсия"] == инверсия:
                    φ = (-поворот if not инверсия else поворот) % 90
                    self.assertAlmostEqual(0, ((в["поворот"] - φ) + 45) % 90 - 45, delta=0.2)
                # Каждой точке созвездия — ровно один центр.
                ц = iq.повернуть(о["центры"], в["поворот"], в["инверсия"])
                self.assertEqual(M, len(set(np.abs(ц[:, None] - идеал[None, :]).argmin(axis=1).tolist())))
                # Жёсткие решения: при верной из 4 симметрий — все метки верны (почти: ОСШ конечно).
                приведённые = (сырьё - о["середина"]) / о["масштаб"]
                ждём = метки[номера]
                лучше = 0.0
                for r in range(4):
                    for отразить in (False, True):
                        у_ = iq.повернуть(приведённые, в["поворот"], в["инверсия"])
                        у_ = iq.повернуть(np.conj(у_) if отразить else у_, 90 * r, False)
                        лучше = max(лучше, float(np.mean(iq.по_плоскости(у_, идеал, np.array(п.метки)) == ждём)))
                self.assertGreater(лучше, 0.999)
                # По кластерам: номер кластера по порядку чтения — тот же, что номер ближайшего центра.
                кл = iq.по_кластерам(приведённые[:20000], о["центры"])
                номер = iq.порядок_чтения(о["центры"])
                self.assertTrue(np.array_equal(кл, номер[np.abs(приведённые[:20000, None] - о["центры"][None, :]).argmin(axis=1)]))

    def test_k_средних_большое_k_поправка(self):
        """Поправка пачкой: у центров, сдвоенных на одной точке, и двух точек под одним центром — слить и разделить."""
        rng = np.random.default_rng(4)
        точки = np.array([complex(x, y) for x in range(16) for y in range(16)]) * 4
        z = точки[rng.integers(0, 256, 40000)] + 0.2 * (rng.standard_normal(40000) + 1j * rng.standard_normal(40000))
        плохие = точки.copy().astype(complex)
        плохие[0], плохие[1] = 0.3j, -0.3j                          # два центра на точке 0, точку 4j не покрывает никто
        плохие[[2, 3]] = плохие[[2, 3]] + 0j
        плохие[1] = 0 - 0.3j
        плохие[17] = (точки[17] + точки[18]) / 2                    # один центр на двух точках 17 и 18 …
        плохие[18] = 0.25 + 0.1j                                    # … а его пара — у точки 0
        м = iq._ближайшие(z, плохие)
        разброс = float(np.mean(np.abs(z - плохие[м]) ** 2))
        ц, м2, р2 = iq._поправить_пачкой(z, плохие, м, разброс, np.random.default_rng(1), 60)
        self.assertLess(р2, разброс)
        self.assertEqual(256, len(set(np.abs(ц[:, None] - точки[None, :]).argmin(axis=1).tolist())))
        self.assertLess(float(np.max(np.min(np.abs(ц[:, None] - точки[None, :]), axis=0))), 0.1)
        # Хорошие центры поправка не трогает.
        м = iq._ближайшие(z, точки)
        ц3, _, _ = iq._поправить_пачкой(z, точки.astype(complex), м, float(np.mean(np.abs(z - точки[м]) ** 2)), np.random.default_rng(1), 60)
        self.assertTrue(np.array_equal(точки, ц3))
        # Быстрый путь — с БОЛЬШОЕ_K кластеров; отсчётов меньше, чем кластеров, — отказ.
        ц4, м4 = iq.k_средних(z[:20000], 256)
        self.assertEqual(256, len(set(np.abs(ц4[:, None] - точки[None, :]).argmin(axis=1).tolist())))
        self.assertTrue(np.array_equal(м4, np.abs(z[:20000, None] - ц4[None, :]).argmin(axis=1)))
        with self.assertRaises(ValueError):
            iq.k_средних(z[:100], 128)


    def test_поправка_пачкой_точно(self):
        """Один круг поправки без шагов Ллойда — прямым счётом: «лишняя» пара (два центра на точке 0) сливается в
        средневзвешенный, «двойной» кластер (один центр на точках 20j и 10 + 20j) делится по главной оси разброса."""
        rng = np.random.default_rng(8)
        точки = np.array([0, 10, 20j, 10 + 20j])
        z = точки[np.repeat(np.arange(4), 500)] + 0.1 * (rng.standard_normal(2000) + 1j * rng.standard_normal(2000))
        ц = np.array([-0.2 + 0j, 0.2 + 0j, 5 + 20j, 10 + 0j])
        м = iq._ближайшие(z, ц)
        разброс = float(np.mean(np.abs(z - ц[м]) ** 2))
        счёт = np.bincount(м, minlength=4)
        ширина = np.bincount(м, weights=np.abs(z - ц[м]) ** 2, minlength=4) / счёт
        слито = (ц[0] * счёт[0] + ц[1] * счёт[1]) / (счёт[0] + счёт[1])
        d = z[м == 2] - z[м == 2].mean()
        угол = 0.5 * math.atan2(2 * float(np.mean(d.real * d.imag)), float(np.mean(d.real ** 2 - d.imag ** 2)))
        сдвиг = math.sqrt(ширина[2]) * cmath.exp(1j * угол)
        with unittest.mock.patch.object(iq, "КРУГОВ_ПОПРАВКИ", 1):
            новые, м2, р2 = iq._поправить_пачкой(z, ц, м, разброс, np.random.default_rng(1), 0)
        np.testing.assert_allclose([слито, ц[2] - сдвиг, ц[2] + сдвиг, ц[3]], новые, atol=1e-9)
        self.assertLess(р2, разброс)
        self.assertTrue(np.array_equal(м2, iq._ближайшие(z, новые)))
        # Хуже не стало бы — не принимается: при уже хороших центрах — те же центры.
        с_, _, _ = iq._поправить_пачкой(z, точки.astype(complex), iq._ближайшие(z, точки), 0.02, np.random.default_rng(1), 0)
        self.assertTrue(np.array_equal(точки, с_))
        # Полный путь (с Ллойдом и кругами) — к точкам.
        итог, _, _ = iq._поправить_пачкой(z, ц, м, разброс, np.random.default_rng(1), 60)
        self.assertLess(float(np.max(np.min(np.abs(итог[:, None] - точки[None, :]), axis=0))), 0.05)

    def test_ячейки_почти_без_полного_расчёта(self):
        """Полным расчётом — только сомнительные отсчёты (за краем облака): у облака КАМ-1024 таких доли процента."""
        с_ = мд.плоскость(мд.найти("DOCSIS 3.1 КАМ1024"))
        точки = np.array([complex(x, y) for x, y in с_.xy])
        rng = np.random.default_rng(3)
        z = точки[rng.integers(0, 1024, 50000)] + 0.01 * (rng.standard_normal(50000) + 1j * rng.standard_normal(50000))
        полных = []
        прежняя = iq._ближайшие_все

        def считать(z_, ц_):
            полных.append(len(z_))
            return прежняя(z_, ц_)

        with unittest.mock.patch.object(iq, "_ближайшие_все", считать):
            итог = iq._Ячейки(точки).ближайшие(z)
        self.assertLess(sum(полных), 0.01 * len(z))
        self.assertTrue(np.array_equal(итог, прежняя(z, точки)))

    def test_облако_края_числа_точек(self):
        rng = np.random.default_rng(2)
        z = np.array([-1.0, 1.0])[rng.integers(0, 2, 4000)] + 0.05 * rng.standard_normal(4000) + 3 + 2j
        о = iq.облако(z, точек=2)
        self.assertEqual(2, о["точек"])
        self.assertAlmostEqual(0.0, abs(np.mean(о["показ"])), delta=0.05)          # показ — приведён к центру
        for плохо in (3, 6, 2048, 1):
            with self.assertRaises(ValueError):
                iq.облако(z, точек=плохо)
        ц, м = iq.k_средних(z[:5], 5)                                          # кластеров столько же, сколько отсчётов
        self.assertEqual(sorted(z[:5].tolist(), key=lambda c: (c.real, c.imag)), sorted(ц.tolist(), key=lambda c: (c.real, c.imag)))
        self.assertEqual(1, len(iq.k_средних(z, 1)[0]))
        with self.assertRaises(ValueError):
            iq.k_средних(z, 0)


class До1024ВслепуюTests(unittest.TestCase):
    def test_старты_соседи_перестановки(self):
        п = р.перестановки_бит(10)
        self.assertEqual(len(п), len(set(п)))
        self.assertTrue(all(sorted(x) == list(range(10)) for x in п))
        self.assertIn(tuple(range(10)), п)
        self.assertIn(tuple(range(9, -1, -1)), п)
        self.assertIn((5, 6, 7, 8, 9, 0, 1, 2, 3, 4), п)
        self.assertIn((0, 5, 1, 6, 2, 7, 3, 8, 4, 9), п)                   # вперемешку ↔ половинами
        self.assertIn((0, 2, 4, 6, 8, 1, 3, 5, 7, 9), п)
        self.assertEqual(22, len(п))
        начало = time.monotonic()
        с_ = р.старты_группы(10, р.основы_плоскостей(10), до=4096)
        self.assertLess(time.monotonic() - начало, 20)
        self.assertLessEqual(len(с_), 4096)
        self.assertTrue(all(sorted(т) == list(range(1024)) for т in с_[:50].tolist()))
        self.assertIn(list(range(1024)), с_.tolist())
        # Соседи: выборка пар без повторов — каждая таблица отличается от исходной ровно в двух местах.
        т = np.random.default_rng(2).permutation(1024)
        для = р.соседи(т, до=500, сид=3)
        self.assertEqual(500, len(для))
        пары = {tuple(np.flatnonzero(x != т).tolist()) for x in для}
        self.assertEqual(500, len(пары))
        self.assertTrue(all(len(x) == 2 for x in пары))
        частые = list(range(100, 140))
        для = р.соседи(т, до=100, частые=частые)
        пары = [tuple(np.flatnonzero(x != т).tolist()) for x in для]
        self.assertEqual([(a, b) for a, b in itertools.combinations(range(100, 114), 2)], пары)   # 14·13/2 = 91 ≤ 100
        self.assertEqual(16 * 15 // 2, len(р.соседи(np.arange(16), до=1000)))         # до больше всех пар — все
        self.assertEqual((0, 2), р.соседи(np.arange(2), до=0, частые=[0, 1]).shape)

    def test_старты_края_и_соседи_точно(self):
        # 7 бит (одна основа): все сочетания 7! × 128 (меньше миллиона); 8 бит (57 основ × 40 320 × 256) —
        # устроенные перестановки, классами (c = 0).
        с7 = р.старты_группы(7, р.основы_плоскостей(7), до=10 ** 6)
        self.assertEqual(5040 * 128, len(с7))
        с8 = р.старты_группы(8, р.основы_плоскостей(8), до=10 ** 6)
        self.assertLessEqual(len(с8), len(р.основы_плоскостей(8)) * len(р.перестановки_бит(8)))
        self.assertGreater(len(с8), 100)
        часть = р.старты_группы(8, р.основы_плоскостей(8), до=300, сид=2)
        self.assertEqual(300, len({tuple(т) for т in часть.tolist()}))                  # выборка без повторов
        self.assertTrue({tuple(т) for т in часть.tolist()} <= {tuple(т) for т in с8.tolist()})
        for k in (8, 9):
            п = р.перестановки_бит(k)
            self.assertTrue(all(sorted(x) == list(range(k)) for x in п), k)
        self.assertEqual(18, len(р.перестановки_бит(9)))                             # 9 сдвигов × 2 и половины уже среди них
        # Выборка пар: ровно те номера, что дал генератор, — пары по строкам (i < j).
        т = np.arange(64)
        для = р.соседи(т, до=2000, сид=5)
        номера = np.sort(np.random.default_rng(5).choice(2016, 2000, replace=False))
        все_пары = [(i, j) for i in range(64) for j in range(i + 1, 64)]
        self.assertEqual([все_пары[n] for n in номера], [tuple(np.flatnonzero(x != т).tolist()) for x in для])
        # Пределы бит.
        with self.assertRaises(ValueError):
            р.искать(np.zeros(100, np.uint8), 0)
        self.assertIsNone(р.искать(np.zeros(100, np.uint8), 10, мера="байты", срок=1))
        # Мера окон: выборка короче окна при части сдвигов — нули, без деления на ноль.
        self.assertEqual([0.0], р.МераОкон(np.array([5]), 10).оценить(np.arange(1024)[None, :]).tolist())

    def test_мера_окон_как_прямой_счёт(self):
        rng = np.random.default_rng(6)
        k = 10
        символы = rng.integers(0, 1 << k, 300)
        таблицы = np.array([rng.permutation(1 << k) for _ in range(20)])
        м = р.МераОкон(символы, k)
        for т, значение in zip(таблицы, м.оценить(таблицы), strict=True):
            биты = "".join(format(int(т[v]), f"0{k}b") for v in символы)
            итог = 0.0
            for r in range(k):
                окна = [int(биты[i:i + 8], 2) for i in range(r, len(биты) - 7, k)]
                h = np.bincount(окна, minlength=256)
                итог += 256 * (h.astype(float) ** 2).sum() / len(окна) ** 2 - 1
            self.assertAlmostEqual(итог / k, значение, places=9)
        # Инверсия бит метки (x ⊕ c) меры не меняет.
        np.testing.assert_allclose(м.оценить(таблицы), м.оценить(таблицы ^ 0x2A5), atol=1e-9)
        self.assertIsInstance(р.дешёвая_мера(символы, 10), р.МераОкон)
        self.assertIsInstance(р.дешёвая_мера(символы % 256, 8), р.МераБайт)
        self.assertEqual([0.0], р.МераОкон(символы[:0], k).оценить(таблицы[:1]).tolist())

    def test_вслепую_1024_симметрия_и_срок(self):
        """КАМ-1024: принятые метки — симметрия квадрата (поворот 90° в коде Грея по осям); находится в срок."""
        k, M = 10, 1024
        кадры = [с.ethernet(п) for п in с.пакеты_ip(200, сид=2)]
        x = np.unpackbits(np.frombuffer(с.hdlc(кадры, флагов_между=20), np.uint8))
        x = x[:len(x) // k * k]
        метки = р.в_биты(np.arange(M), k)
        т = np.asarray(р.в_символы(ploskost.преобразовать(метки, k, порядок="старший", раскладка="половинами",
                                                         код="Грей", симметрия="поворот 90°"), k))
        принятые = р.в_биты(т[р.в_символы(x, k)], k)
        ходы = []
        начало = time.monotonic()
        ит = р.искать(принятые, k, мера="байты", срок=60, ход=lambda д, т_: ходы.append(д))
        self.assertLess(time.monotonic() - начало, 75)
        self.assertIsNotNone(ит)
        обратная_т = np.argsort(т).tolist()
        self.assertIn(обратная_т, ит.равноценные)
        self.assertLessEqual(len(ит.равноценные), р.РАВНОЦЕННЫХ_ДО)
        self.assertTrue(ходы and max(ходы) <= 1.0)
        self.assertIn("среди самых частых меток", " ".join(ит.подробно))
        # Срок соблюдается и на случайном потоке (структуры нет — None), КАМ-512.
        шум = np.random.default_rng(3).integers(0, 2, 9 * 20000).astype(np.uint8)
        начало = time.monotonic()
        self.assertIsNone(р.искать(шум, 9, мера="байты", срок=8))
        self.assertLess(time.monotonic() - начало, 8 + 15)
        with self.assertRaises(ValueError):
            р.искать(шум, 11, мера="байты", срок=1)

    def test_анализ_1024_быстро(self):
        rng = np.random.default_rng(1)
        n, P = 1 << 16, 500
        символы = rng.integers(0, 1024, n)
        места = np.arange(n) % P < 6
        символы[места] = np.array([3, 1000, 517, 64, 64, 900])[np.arange(n)[места] % P]
        начало = time.monotonic()
        а = р.анализ(р.в_биты(символы, 10), 10, фаза=0)
        self.assertLess(time.monotonic() - начало, 20)
        self.assertEqual(P, а["период"]["символов"])
        self.assertEqual(P, а["кадр"])
        self.assertEqual([3, 1000, 517, 64, 64, 900], а["места"][0]["символы"])
        self.assertIsNone(а["переходы"])
        self.assertEqual(1 << 16, а["символов"])
        self.assertEqual(1024, len(а["частоты"]))
        # Граница «больших»: у 64 точек — прежняя выборка (до 2^18 символов) и матрица переходов, у 128 — 2^16 и без неё.
        поток = rng.integers(0, 2, 7 * 100000).astype(np.uint8)
        а64, а128 = р.анализ(поток[:6 * 100000], 6, фаза=0), р.анализ(поток, 7, фаза=0)
        self.assertEqual((100000, 64), (а64["символов"], len(а64["переходы"])))
        self.assertEqual(((1 << 16) + 1, None), (а128["символов"], а128["переходы"]))   # выборка и ещё символ на фазу
        self.assertEqual(8, len(а["частые_переходы"]))
        # Период по группам младших бит — тот же, что прямым счётом совпадений символов.
        прямо = период_эталон(символы[:20000], 1024, 1000)
        self.assertEqual(P, прямо["символов"])
        self.assertEqual(P, р.период(символы[:20000], 1024, до=1000)["символов"])
        # Отрыв — ровно тот, что прямым счётом по группам шести младших бит метки (64 группы).
        по_группам = период_эталон(символы[:20000] & 63, 64, 1000)
        self.assertEqual(по_группам, р.период(символы[:20000], 1024, до=1000))
        self.assertEqual(р.период(символы[:20000] & 63, 64, до=1000), р.период(символы[:20000], 1024, до=1000))
