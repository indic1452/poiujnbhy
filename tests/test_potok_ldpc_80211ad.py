"""IEEE 802.11ad (672; 7/8 — 624) и 802.11ay (1344) — свой кодер по документам рабочей группы.

Эталон — не JSON проекта, а таблицы самих документов IEEE 802.11 (istochniki/ldpc/standarty):
11-10/0433r2 (TGad CP specification, табл. 69–72, «Pi — столбцы единичной, сдвинутые вправо на i»,
систематический кодер c = (b, p), H·cᵀ = 0), 11-16/0692r0 (правило подъёма 1344), 11-17/1061r1
(табл. 31 — 7/8 1344), 11-16/1495r1 (7/8 = 13/16 без первых 48 бит чётности). Кодирование —
решением H_p·p = H_d·b своим методом Гаусса (без модулей проекта). Ошибки линии → данные бит в бит.
"""

import re
import unittest
import zipfile
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import ldpc, ldpc_std

ИСТОЧНИКИ = Path(__file__).resolve().parents[1] / "istochniki" / "ldpc" / "standarty"
есть_источники = unittest.skipUnless(ИСТОЧНИКИ.exists(), "нет istochniki/ldpc")
Z = 42


def таблицы_docx(имя: str) -> list[list[list[str]]]:
    x = zipfile.ZipFile(ИСТОЧНИКИ / имя).read("word/document.xml").decode()
    return [[[
        "".join(re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>", c)).strip() for c in re.findall(r"<w:tc>.*?</w:tc>", р, re.S)]
        for р in re.findall(r"<w:tr[ >].*?</w:tr>", т, re.S)] for т in re.findall(r"<w:tbl>.*?</w:tbl>", x, re.S)]


def числа(т):
    return [[int(c) if c not in ("", "-") else -1 for c in р] for р in т]


def матрицы_11ad() -> dict[str, list[list[int]]]:
    т = [т for т in таблицы_docx("IEEE_802.11-10-0433-02_TGad_CP_specification.docx")
         if т and all(len(р) == 16 for р in т) and all(re.fullmatch(r"\d*", c) for р in т for c in р)]
    return dict(zip(("1/2", "5/8", "3/4", "13/16"), map(числа, т)))


def подъём(база, под):
    """11-16/0692r0: «0» → [[Pi, −], [−, Pi]], «1» → [[−, Pi], [Pi, −]]."""
    нов = [[-1] * (2 * len(база[0])) for _ in range(2 * len(база))]
    for r, ряд in enumerate(база):
        for c, s in enumerate(ряд):
            if s < 0:
                continue
            a = под[r][c]
            нов[2 * r][2 * c + a] = нов[2 * r + 1][2 * c + 1 - a] = s
    return нов


def H_по(база) -> np.ndarray:
    """Pi — единичная Z×Z, столбцы сдвинуты вправо на i (21.3.8)."""
    H = np.zeros((len(база) * Z, len(база[0]) * Z), np.uint8)
    i = np.arange(Z)
    for r, ряд in enumerate(база):
        for c, s in enumerate(ряд):
            if s >= 0:
                H[r * Z + i, c * Z + (i + s) % Z] = 1
    return H


def кодировать(H: np.ndarray, b: np.ndarray) -> np.ndarray:
    """c = (b, p), H·cᵀ = 0: p из H_p·p = H_d·b — Гаусс по GF(2)."""
    m, n = H.shape
    k = n - m
    A = np.concatenate([H[:, k:], (H[:, :k].astype(np.int64) @ b % 2).astype(np.uint8)[:, None]], axis=1)
    for c in range(m):
        строка = c + int(np.flatnonzero(A[c:, c])[0])
        A[[c, строка]] = A[[строка, c]]
        лишние = np.flatnonzero(A[:, c])
        лишние = лишние[лишние != c]
        A[лишние] ^= A[c]
    return np.concatenate([b, A[:, m]])


def передать(слова: list[np.ndarray], убрать: list[int], ошибок: int, сдвиг: int, сид: int) -> np.ndarray:
    rng = np.random.default_rng(сид)
    поток = np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8)] + [np.delete(с, убрать) for с in слова])
    поток[сдвиг + rng.choice(len(поток) - сдвиг, ошибок, replace=False)] ^= 1
    return поток


@есть_источники
class IEEE80211ad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ad = матрицы_11ad()

    def test_матрицы_равны_документу(self):
        for k, скор in ((336, "1/2"), (420, "5/8"), (504, "3/4"), (546, "13/16")):
            with self.subTest(скор=скор):
                м = ldpc_std.матрица(f"80211ad-672-{k}")
                np.testing.assert_array_equal(H_по(self.ad[скор]), м.плотная())
                self.assertEqual(list(range(k)), ldpc.информационные(м).tolist())
        с = ldpc_std.схема("80211ad-624-546")
        self.assertEqual(list(range(546, 594)), с.выколоты.tolist())
        self.assertEqual(624, с.длина)

    def test_снятие_каждого_с_ошибками(self):
        rng = np.random.default_rng(1)
        for имя, скор, убрать in (("80211ad-672-336", "1/2", []), ("80211ad-672-420", "5/8", []),
                                  ("80211ad-672-504", "3/4", []), ("80211ad-672-546", "13/16", []),
                                  ("80211ad-624-546", "13/16", list(range(546, 594)))):
            with self.subTest(имя=имя):
                H = H_по(self.ad[скор])
                k = H.shape[1] - H.shape[0]
                данные = rng.integers(0, 2, (6, k)).astype(np.uint8)
                слова = [кодировать(H, b) for b in данные]
                self.assertFalse((H.astype(int) @ слова[0] % 2).any())
                поток = передать(слова, убрать, 6, 0, 2)
                ряд, подробно = ldpc.снять(поток, ldpc_std.схема(имя), начало=0)
                np.testing.assert_array_equal(данные.reshape(-1), ряд, err_msg=str(подробно))

    def test_автомат_находит_5_8_со_сдвигом(self):
        H = H_по(self.ad["5/8"])
        rng = np.random.default_rng(4)
        данные = rng.integers(0, 2, (12, 420)).astype(np.uint8)
        поток = передать([кодировать(H, b) for b in данные], [], 10, 37, 5)
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("«80211ad-672-420»", найдено.что)
        self.assertEqual(37, найдено.свойства["начало"])
        np.testing.assert_array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше)


@есть_источники
class IEEE80211ay(unittest.TestCase):
    def test_1344_подъём_и_снятие(self):
        ad = матрицы_11ad()
        под = dict(zip(("1/2", "5/8", "3/4", "13/16"),
                       (числа(т) for т in таблицы_docx("IEEE_802.11-16-0692-00_TGay_SFD_LDPC_1344.docx")
                        if т and len(т[0]) == 16 and all(re.fullmatch(r"-?[01]", c) for р in т for c in р))))
        rng = np.random.default_rng(7)
        for скор, k in (("1/2", 672), ("5/8", 840), ("3/4", 1008), ("13/16", 1092)):
            with self.subTest(скор=скор):
                H = H_по(подъём(ad[скор], под[скор]))
                имя = f"80211ay-1344-{k}"
                np.testing.assert_array_equal(H, ldpc_std.матрица(имя).плотная())
                данные = rng.integers(0, 2, (3, k)).astype(np.uint8)
                поток = передать([кодировать(H, b) for b in данные], [], 8, 0, 8)
                ряд, _ = ldpc.снять(поток, ldpc_std.схема(имя), начало=0)
                np.testing.assert_array_equal(данные.reshape(-1), ряд)

    def test_7_8_1344_по_табл_31(self):
        т31 = [числа(т) for т in таблицы_docx("IEEE_802.11-17-1061-01_TGay_LDPC_1344_7-8.docx")
               if т and len(т[0]) == 32][0]
        H = H_по(т31)
        np.testing.assert_array_equal(H, ldpc_std.матрица("80211ay-1344-1176").плотная())
        rng = np.random.default_rng(9)
        данные = rng.integers(0, 2, (3, 1176)).astype(np.uint8)
        поток = передать([кодировать(H, b) for b in данные], [], 3, 0, 10)
        ряд, _ = ldpc.снять(поток, ldpc_std.схема("80211ay-1344-1176"), начало=0)
        np.testing.assert_array_equal(данные.reshape(-1), ряд)


if __name__ == "__main__":
    unittest.main()
