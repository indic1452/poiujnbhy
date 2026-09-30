"""Встроенные коды LDPC КНР: ABS-S, CMMB, DTMB, BeiDou — состав, свой кодер по источнику, снятие.

Кодеры здесь свои и не берут ничего из ldpc/ldpc_kitay: H строится прямо из записей
data/ldpc_kitay.json (тройки базовой матрицы ABS-S, строки H CMMB/DTMB), проверочные биты —
своим исключением Гаусса на упакованных строках; у BeiDou — кодер над GF(64) по приложению
ICD B1C (p = H2⁻¹·H1·m), сверенный с примером кодирования из того же ICD. Слова идут в поток
по схеме стандарта с ошибками 1e-3, проект снимает их слоем «ldpc ИМЯ» и автоматом —
данные должны совпасть бит в бит.
"""

import json
import re
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import gf2, ldpc, ldpc_kitay, ldpc_std
from reportgen.potok.razbor import снять_вручную

КОРЕНЬ = Path(__file__).resolve().parents[1]
ЗАПИСИ = json.loads((КОРЕНЬ / "src" / "reportgen" / "potok" / "data" / "ldpc_kitay.json").read_text(encoding="utf-8"))


# -- свой кодер над GF(2) ------------------------------------------------------------------------

def плотная_из_записи(з: dict) -> np.ndarray:
    """H из записи источника — без модулей проекта: тройки (строка, столбец, сдвиг) или строки."""
    n, k = з["n"], з["k"]
    H = np.zeros((n - k, n), np.uint8)
    if "тройки" in з:
        Z = з["Z"]
        for r, c, s in з["тройки"]:
            for i in range(Z):
                H[r * Z + i, c * Z + (i + s) % Z] = 1   # единичная, сдвинутая вправо на s
    else:
        for i, строка in enumerate(з["строки"]):
            H[i, строка] = 1
    return H


def кодер(H: np.ndarray, данные_поз: np.ndarray):
    """Проверочные биты из данных: Гаусс — Жордан над [H_проверочные | H_данные] на байтах."""
    m, n = H.shape
    проверочные = np.setdiff1d(np.arange(n), данные_поз)
    A = np.packbits(np.concatenate([H[:, проверочные], H[:, данные_поз]], axis=1), axis=1)
    for c in range(m):
        байт, бит = c // 8, 7 - c % 8
        есть = (A[c:, байт] >> бит) & 1
        опорная = c + int(np.argmax(есть))
        assert есть.any(), "проверочная часть H вырождена"
        A[[c, опорная]] = A[[опорная, c]]
        прочие = np.flatnonzero((A[:, байт] >> бит) & 1)
        прочие = прочие[прочие != c]
        A[прочие] ^= A[c]
    D = np.unpackbits(A, axis=1)[:, m:m + len(данные_поз)]

    def кодировать(данные: np.ndarray) -> np.ndarray:
        слова = np.zeros((len(данные), n), np.uint8)
        слова[:, данные_поз] = данные
        слова[:, проверочные] = (данные.astype(np.int64) @ D.T.astype(np.int64)) % 2
        return слова
    return кодировать


def в_поток(слова: np.ndarray, переданы: np.ndarray, доля: float, сдвиг: int, сид: int = 3) -> np.ndarray:
    rng = np.random.default_rng(сид)
    п = слова[:, переданы]
    п = п ^ (rng.random(п.shape) < доля).astype(np.uint8)
    return np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8), п.reshape(-1)])


# -- GF(64) по ICD B1C (p(x) = 1 + x + x⁶) — для кодера BeiDou ----------------------------------

СТЕПЕНЬ = [0] * 63
ЛОГ = [0] * 64
_x = 1
for _i in range(63):
    СТЕПЕНЬ[_i] = _x
    ЛОГ[_x] = _i
    _x <<= 1
    if _x & 64:
        _x ^= 0b1000011


def умн(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else СТЕПЕНЬ[(ЛОГ[a] + ЛОГ[b]) % 63]


def обр(a: int) -> int:
    return СТЕПЕНЬ[(63 - ЛОГ[a]) % 63]


def кодер_gf64(з: dict):
    """ICD B1C, прил. 1: H = [H1, H2], p = (H2⁻¹·H1)·m — Гаусс над GF(64)."""
    m = len(з["индексы"])
    n = з["n"] // 6
    k = n - m
    H = [[0] * n for _ in range(m)]
    for i, (сим, эл) in enumerate(zip(з["индексы"], з["элементы"], strict=True)):
        for j, h in zip(сим, эл, strict=True):
            H[i][j] = h
    A = [H[i][k:] + H[i][:k] for i in range(m)]          # [H2 | H1]
    for c in range(m):
        r = next(r for r in range(c, m) if A[r][c])
        A[c], A[r] = A[r], A[c]
        и = обр(A[c][c])
        A[c] = [умн(и, v) for v in A[c]]
        for r in range(m):
            if r != c and A[r][c]:
                f = A[r][c]
                A[r] = [v ^ умн(f, w) for v, w in zip(A[r], A[c], strict=True)]

    def кодировать(символы: list[int]) -> list[int]:
        p = []
        for i in range(m):
            s = 0
            for j in range(k):
                s ^= умн(A[i][m + j], символы[j])
            p.append(s)
        return list(символы) + p
    return кодировать


def символы_в_биты(символы) -> np.ndarray:
    return np.array([(s >> (5 - b)) & 1 for s in символы for b in range(6)], np.uint8)


# -- тесты ------------------------------------------------------------------------------------

class Состав(unittest.TestCase):
    def test_коды_и_семейства(self):
        коды = [к for к in ldpc_std.список() if str(к["семейство"]).startswith("КНР")]
        по = {}
        for к in коды:
            по.setdefault(к["семейство"], []).append(к["имя"])
        self.assertEqual({с: len(и) for с, и in по.items()},
                         {"КНР: ABS-S": 10, "КНР: CMMB": 2, "КНР: DTMB": 3, "КНР: BeiDou": 4})
        for имя in ("abs-s-15360-7680", "abs-s-15360-13824", "cmmb-9216-4608", "dtmb-7493-6096",
                    "beidou-b1c-sf2-1200-600", "beidou-b2a-576-288"):
            self.assertTrue(ldpc_std.есть(имя), имя)
        self.assertFalse(ldpc_std.есть("abs-s-15360-1"))

    def test_abs_s_размеры_и_степени(self):
        """ABS-S: все 10 кодов — 15360, базовая (n − k)/32 × 480; степени бит — п. 1 формулы."""
        заявлено = {3840: [11264, 256, 3840], 7680: [7424, 2304, 3840, 1792], 13824: [1280, 3584, 10496]}
        for k in (3840, 6144, 7680, 9216, 10240, 11520, 12288, 12800, 13312, 13824):
            з = ЗАПИСИ[f"abs-s-15360-{k}"]
            строки = {r for r, _, _ in з["тройки"]}
            self.assertEqual(строки, set(range((15360 - k) // 32)))
            self.assertLess(max(c for _, c, _ in з["тройки"]), 480)
            if k in заявлено:
                степ = np.bincount(np.bincount([c for _, c, _ in з["тройки"]], minlength=480))
                self.assertEqual([int(v) * 32 for v in степ if v], заявлено[k])

    def test_cmmb_квазициклична(self):
        H = ЗАПИСИ["cmmb-9216-4608"]["строки"]
        self.assertEqual(H[0], [0, 6, 12, 18, 25, 30])          # прил. D1: «0: 0 6 12 18 25 30»
        self.assertTrue(all(sorted((c + 36) % 9216 for c in H[r]) == H[r + 18] for r in range(0, 4590, 97)))

    def test_схемы_стандарта(self):
        self.assertEqual(ldpc_std.схема("dtmb-7493-3048").выколоты.tolist(), [0, 1, 2, 3, 4])
        self.assertEqual(ldpc_std.схема("dtmb-7493-3048").длина, 7488)
        self.assertEqual(ldpc_std.схема("abs-s-15360-11520").длина, 15360)
        self.assertTrue(ldpc_std.выколоты_по_стандарту("dtmb-7493-4572"))
        self.assertFalse(ldpc_std.выколоты_по_стандарту("cmmb-9216-6912"))
        self.assertIn("dtmb-7493-6096", ldpc_std.подходящие(4 * 7488))
        self.assertIn("beidou-b2a-576-288", ldpc_std.подходящие(576 * 10))


class МатрицаПроекта(unittest.TestCase):
    """Матрица проекта совпадает с H, построенной прямо из записи, и данные — где в стандарте."""

    def test_совпадает_с_записью(self):
        for имя in ("abs-s-15360-13824", "cmmb-9216-6912", "dtmb-7493-6096"):
            with self.subTest(имя=имя):
                self.assertTrue(np.array_equal(ldpc_kitay.матрица(имя).плотная(), плотная_из_записи(ЗАПИСИ[имя])))
        self.assertEqual(ldpc_kitay.матрица("abs-s-15360-7680").данные.tolist()[-1], 7679)
        self.assertEqual(ldpc_kitay.матрица("dtmb-7493-3048").данные.tolist()[0], 4445)
        порядок = ЗАПИСИ["cmmb-9216-4608"]["данные"]
        self.assertEqual(ldpc_kitay.матрица("cmmb-9216-4608").данные.tolist(), порядок)

    def test_gf64_образ(self):
        """Двоичный образ BeiDou: проверка над GF(64) выполнена ⇔ выполнены её 6 двоичных."""
        з = ЗАПИСИ["beidou-b2a-576-288"]
        код = кодер_gf64(з)
        rng = np.random.default_rng(4)
        м = ldpc_kitay.матрица("beidou-b2a-576-288")
        H = м.плотная()
        for _ in range(5):
            слово = символы_в_биты(код(rng.integers(0, 64, 48).tolist()))
            self.assertFalse((H @ слово % 2).any())
        слово[7] ^= 1
        self.assertTrue((H @ слово % 2).any())
        self.assertEqual(ldpc_kitay.умножить_gf64(2, 32), 3)      # x·x⁵ = x⁶ = 1 + x
        for a in range(64):                                      # вся таблица — по своим степеням
            for b in range(64):
                self.assertEqual(ldpc_kitay.умножить_gf64(a, b), умн(a, b))

    def test_строк_n_минус_k_и_ранг_beidou(self):
        """У всех кодов строк H — n − k; у двоичного образа BeiDou ранг — n − k (ни одна из 6
        двоичных проверок каждой проверки над GF(64) не потеряна и не слита с соседней)."""
        for имя, з in ЗАПИСИ.items():
            м = ldpc_kitay.матрица(имя)
            self.assertEqual((м.n, м.m), (з["n"], з["n"] - з["k"]), имя)
            if з.get("gf64"):
                self.assertEqual(gf2.ранг(м.плотная()), з["n"] - з["k"], имя)


class ПримерICD(unittest.TestCase):
    """ICD B1C, прил. 1 (1): пример кодирования подкадра 2 — свой кодер над GF(64) даёт то же слово."""

    def test_пример_кодирования(self):
        путь = КОРЕНЬ / "istochniki" / "ldpc_kitay" / "beidou" / "BDS-SIS-ICD-B1C-1.0.txt"
        if not путь.exists():
            self.skipTest("первоисточники не приложены")
        т = путь.read_text(encoding="utf-8")
        т = т[т.index("(1) Encoding Examples"):]
        т = re.sub(r"=====PAGE \d+\n(?:.*\n){0,4}?\s*20\d\d-\d\d\s*\n", "\n", т)
        вход = re.search(r"input information is\s*\[([01\s]+)\]", т).group(1).split()
        выход = re.search(r"output codeword is\s*\[([01\s]+)\]", т).group(1).split()
        self.assertEqual((len(вход), len(выход)), (100, 200))
        код = кодер_gf64(ЗАПИСИ["beidou-b1c-sf2-1200-600"])
        self.assertEqual(код([int(s, 2) for s in вход]), [int(s, 2) for s in выход])
        # И двоичный образ проекта: слово примера проходит все проверки.
        H = ldpc_kitay.матрица("beidou-b1c-sf2-1200-600").плотная()
        слово = np.array([int(b) for s in выход for b in s], np.uint8)
        self.assertFalse((H @ слово % 2).any())


class Снятие(unittest.TestCase):
    """Свой кодер → поток по схеме стандарта с ошибками 1e-3 → снятие проектом бит в бит."""

    def проверить(self, имя: str, слов: int, сдвиг: int, автомат: bool = True, слой: str = ""):
        з = ЗАПИСИ[имя]
        м = ldpc_kitay.матрица(имя)
        rng = np.random.default_rng(len(имя))
        if з.get("gf64"):
            код = кодер_gf64(з)
            k = з["k"] // 6
            символы = rng.integers(0, 64, (слов, k))
            слова = np.array([символы_в_биты(код(с.tolist())) for с in символы])
            данные = np.array([символы_в_биты(с) for с in символы])
        else:
            if "тройки" in з:
                позиции = np.arange(з["k"])
            elif "данные" in з:
                позиции = np.array(з["данные"])
            else:
                позиции = np.arange(з["данные_с"], з["n"])
            данные = rng.integers(0, 2, (слов, з["k"])).astype(np.uint8)
            слова = кодер(плотная_из_записи(з), позиции)(данные)
        self.assertFalse((м.плотная() @ слова[0] % 2).any(), "свой кодер дал не кодовое слово")
        схема = ldpc_std.схема(имя)
        поток = в_поток(слова, схема.переданы, 1e-3, сдвиг)
        ряд, запись = снять_вручную(поток, f"ldpc {имя} {слой}".strip())
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(ряд)], ряд), имя)
        self.assertGreaterEqual(len(ряд), (слов - 1) * з["k"])
        if автомат:
            найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
            self.assertIsNotNone(найдено, имя)
            self.assertIn(f"по матрице «{имя}»", найдено.что)
            self.assertEqual(найдено.свойства["начало"], сдвиг)
            self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))
        return запись

    def test_abs_s_9_10(self):
        self.проверить("abs-s-15360-13824", 8, 777)

    def test_abs_s_13_15_вручную(self):
        self.проверить("abs-s-15360-13312", 5, 0, автомат=False)

    def test_cmmb_3_4(self):
        self.проверить("cmmb-9216-6912", 8, 1234)

    def test_dtmb_0_8_выколоты_первые_5(self):
        запись = self.проверить("dtmb-7493-6096", 8, 99)
        self.assertIn("выколото 5", " ".join(запись.подробно))

    def test_dtmb_0_6_вручную(self):
        self.проверить("dtmb-7493-4572", 5, 0, автомат=False)

    def test_beidou_b1c_подкадр_2(self):
        self.проверить("beidou-b1c-sf2-1200-600", 40, 321)

    def test_beidou_b2a(self):
        self.проверить("beidou-b2a-576-288", 60, 17)

    def test_beidou_b2b_и_подкадр_3_вручную(self):
        """Двоичный образ 64-ричного кода «минимум — сумма» правит хуже, чем декодер над GF(64):
        при 1e-3 за 50 итераций не сходится около 0,5 % слов B2b и B2a, за 200 — ни одно из 200."""
        self.проверить("beidou-b2b-972-486", 30, 5, автомат=False, слой="итераций 200")
        self.проверить("beidou-b1c-sf3-528-264", 40, 5, автомат=False)


if __name__ == "__main__":
    unittest.main()
