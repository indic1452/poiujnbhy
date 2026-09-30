"""Разметка вслепую, своя разметка, общие плоскости (APSK, крест), вход I/Q — против независимых эталонов.

Эталоны свои: модулятор со случайной перестановкой меток (метка → точка через
строки бит, не через таблицы модуля), регистр сдвига ПСП и Берлекэмп — Мэсси на
чистом Python, кадры с чётными строками, облако I/Q с шумом, поворотом и
инверсией (комплексные числа), WAV — через модуль ``wave`` и вручную. Мера
совпадений окон сверяется с прямым подсчётом окон по потоку.
"""

import itertools
import math
import os
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import razmetka as р

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
