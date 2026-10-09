"""LDPC в кадрах модема (ldpc_kadr): данные — в последовательном порядке бит, а не в порядке файла.

Демодулятор пишет поток младшим битом байта вперёд; проверки кода разрежены в обоих порядках (перестановка в
байте — перенумерация позиций), и код находится и в порядке файла. Данные над кодом — под скремблером V.35
(1 + x⁻³ + x⁻²⁰) на заполнении: в чужом порядке скремблер виделся бы полиномом 1 + x⁻¹⁰ + x⁻⁴⁰, верным лишь на
6 местах из 8 (так было на образце ФМ2 R = 0,488). Порядок выбирается по окнам ЛРП (Берлекэмп — Мэсси).
"""

from __future__ import annotations

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import gf2, ldpc_kadr, ldpc_vosst, skrembler


def систематический_по(H: np.ndarray, D: np.ndarray) -> np.ndarray:
    """Порождающая матрица, у которой данные стоят на позициях D (k × n)."""
    G = gf2.ядро(H).astype(np.uint8)
    A = np.concatenate([G[:, D], np.eye(len(G), dtype=np.uint8)], axis=1)
    for r in range(len(G)):
        p = r + int(np.flatnonzero(A[r:, r])[0])
        A[[r, p]] = A[[p, r]]
        for i in np.flatnonzero(A[:, r]):
            if i != r:
                A[i] ^= A[r]
    return ((A[:, len(G):].astype(np.int64) @ G.astype(np.int64)) % 2).astype(np.uint8)


class ПорядокДанныхTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        H = с.qc_ldpc(12, 24, 27, 4, сид=3)
        n = H.shape[1]
        D = ldpc_vosst.позиции_данных([np.flatnonzero(h) for h in H], n)
        k = len(D)
        г = np.random.default_rng(5)
        кадров = 300
        куски, всего = [], 0
        while всего < кадров * k:
            длина = int(г.integers(500, 3000))
            куски.append(np.zeros(длина, np.uint8) if г.random() < 0.5 else г.integers(0, 2, длина, dtype=np.uint8))
            всего += длина
        cls.данные = с.скремблировать(np.concatenate(куски)[:кадров * k], (3, 20))[:кадров * k]
        слова = (cls.данные.reshape(кадров, k).astype(np.int64) @ систематический_по(H, D)) % 2
        синхро = np.unpackbits(np.array([0x1A, 0xCF, 0xFC, 0x1D], dtype=np.uint8))
        поток = np.concatenate([np.concatenate([синхро, w.astype(np.uint8)]) for w in слова])
        cls.файл = поток.reshape(-1, 8)[:, ::-1].reshape(-1)        # младшим битом байта вперёд
        cls.кадр = 32 + n
        cls.найдено, cls.почему = ldpc_kadr.найти(cls.файл, cls.кадр, срок=90, порядки=("", "младший"))

    def test_данные_в_последовательном_порядке(self):
        н = self.найдено
        self.assertIsNotNone(н, self.почему)
        self.assertEqual(ldpc_kadr.МЛАДШИЙ, н.свойства["порядок_данных"])
        вых = np.asarray(н.дальше)
        self.assertTrue(np.array_equal(вых, self.данные[:len(вых)]))
        self.assertTrue(any("окон ЛРП" in п for п in н.подробно))

    def test_скремблер_над_кодом_свой(self):
        найдено = skrembler.найти(np.asarray(self.найдено.дальше))
        self.assertIsNotNone(найдено)
        self.assertEqual("мультипликативный скремблер 1 + x^-3 + x^-20", найдено.что)

    def test_окна_лрп(self):
        шум = с.случайные_биты(1 << 16, 9)
        self.assertEqual(0, ldpc_kadr.окна_лрп(шум))
        # Скремблер на заполнении (после случайного начала регистр не нулевой) — ЛРП степени 20.
        лрп = с.скремблировать(np.concatenate([с.случайные_биты(64, 3), np.zeros(1 << 16, np.uint8)]), (3, 20))
        self.assertGreater(ldpc_kadr.окна_лрп(лрп), 200)


if __name__ == "__main__":
    unittest.main()
