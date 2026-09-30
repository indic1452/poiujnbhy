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
        окна = [int("".join(map(str, биты[k * i + r:k * i + r + 8])), 2) for i in range(n)]
        h = np.bincount(окна, minlength=256)
        итог.append(256 * (h.astype(float) ** 2).sum() / n ** 2 - 1)
    return float(np.mean(итог))


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
        for k, n in ((3, 900), (4, 700), (5, 500), (2, 800)):
            M = 1 << k
            символы = rng.integers(0, M, n)
            символы[100:300] = np.tile(rng.integers(0, M, 5), 40)          # повторы — чтобы мера была не нулевой
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
            р.искать(x, 9)
        ит = р.искать(x, 1)
        self.assertEqual([[0, 1], [1, 0]], ит.равноценные)

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
        self.assertEqual("поворот 45° не переводит созвездие в себя; для этой плоскости -16 шаг 90°", str(о.exception))
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
        найдено = ploskost.найти_фм(передать(x, 3, π), 3, лучших=1, бюджет=2)
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
                                 ("iq", {"формат": "int16", "точек": 3}, "число точек — степень двойки от 2 до 256"),
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
