# -*- coding: utf-8 -*-
"""LDPC по матрице из стандарта: загрузка H, перфорация, укорочение, снятие слоем."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import gf2, ldpc
from reportgen.potok.razbor import снять_вручную

Z = 32


def прототип(сид=1, полный_первый=False):
    """Базовая 6 × 12: информационная часть случайная, проверочная — двухдиагональная."""
    rng = np.random.default_rng(сид)
    P = np.full((6, 12), -1)
    for i in range(6):
        for j in range(6):
            if rng.random() < 0.5 or (полный_первый and j == 0):
                P[i, j] = rng.integers(0, Z)
        P[i, 6 + i] = 0
        if i:
            P[i, 5 + i] = 0
    return "\n".join(" ".join(str(x) for x in ряд) for ряд in P)


def в_alist(H):
    """Независимая запись alist (Маккей): по столбцам и по строкам, с дополнением нулями."""
    m, n = H.shape
    столбцы = [list(np.flatnonzero(H[:, j]) + 1) for j in range(n)]
    строки = [list(np.flatnonzero(H[i]) + 1) for i in range(m)]
    dc, dr = max(map(len, столбцы)), max(map(len, строки))
    линии = [f"{n} {m}", f"{dc} {dr}", " ".join(str(len(с)) for с in столбцы),
             " ".join(str(len(с)) for с in строки)]
    линии += [" ".join(map(str, с + [0] * (dc - len(с)))) for с in столбцы]
    линии += [" ".join(map(str, с + [0] * (dr - len(с)))) for с in строки]
    return "\n".join(линии)


def кодовые(матрица, слов, сид=2):
    H = матрица.плотная()
    G = gf2.ядро(H)
    данные = np.random.default_rng(сид).integers(0, 2, (слов, len(G)))
    return (данные @ G % 2).astype(np.uint8)


def передать(слова, схема, доля=0.01, сдвиг=37, сид=3):
    rng = np.random.default_rng(сид)
    переданные = слова[:, схема.переданы]
    ошибки = (rng.random(переданные.shape) < доля).astype(np.uint8)
    return np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8),
                           (переданные ^ ошибки).reshape(-1)])


class ЗагрузкаTests(unittest.TestCase):
    def test_базовая_сдвиг_вправо(self):
        м = ldpc.из_прототипа("0 -1 1\n- 2 -1", 4)
        H = м.плотная()
        # Блок (0, 0): единичная; блок (0, 2): сдвиг вправо на 1 — строка r, столбец 8 + (r+1) mod 4.
        self.assertEqual([0, 9], np.flatnonzero(H[0]).tolist())
        self.assertEqual([3, 8], np.flatnonzero(H[3]).tolist())
        self.assertEqual([6], np.flatnonzero(H[4]).tolist())
        with self.assertRaises(ValueError):
            ldpc.из_прототипа("0 1\n0", 4)

    def test_alist_туда_и_обратно(self):
        м = ldpc.из_прототипа(прототип(), Z)
        H = м.плотная()
        self.assertTrue(np.array_equal(H, ldpc.из_alist(в_alist(H)).плотная()))
        # Без дополнения нулями (номера ровно по весу) — тоже читается.
        m, n = H.shape
        строки = [" ".join(str(x + 1) for x in np.flatnonzero(H[:, j])) for j in range(n)]
        плотно = "\n".join([f"{n} {m}", "1 1", " ".join(str(int(H[:, j].sum())) for j in range(n)),
                            " ".join(str(int(H[i].sum())) for i in range(m))] + строки)
        self.assertTrue(np.array_equal(H, ldpc.из_alist(плотно).плотная()))
        with self.assertRaises(ValueError):
            ldpc.из_alist(в_alist(H).replace(f"{n} {m}", f"{n} {m + 5}", 1))

    def test_адреса_накопитель(self):
        # Независимое кодирование по описанию таблицы адресов: бит m группы i
        # идёт в проверки (x + (m mod G)·q) mod (n − k), затем накопитель.
        G_, n, k = 8, 64, 32
        m, q = n - k, (n - k) // G_
        таблица = [[1, 7, 20], [3, 30], [0, 9, 15, 26], [5, 11]]
        м = ldpc.из_адресов("\n".join(" ".join(map(str, р)) for р in таблица), n, k, группа=G_)
        rng = np.random.default_rng(4)
        for _ in range(20):
            данные = rng.integers(0, 2, k)
            p = np.zeros(m, dtype=np.int64)
            for i, адреса in enumerate(таблица):
                for t in range(G_):
                    for x in адреса:
                        p[(x + t * q) % m] ^= данные[i * G_ + t]
            for i in range(1, m):
                p[i] ^= p[i - 1]
            слово = np.concatenate([данные, p])
            self.assertFalse((м.плотная() @ слово % 2).any())
        self.assertEqual(k, м.сводка()["k"])
        with self.assertRaises(ValueError):
            ldpc.из_адресов("1 2", n, k, группа=G_)

    def test_информационные_и_позиции(self):
        м = ldpc.из_прототипа(прототип(), Z)
        self.assertEqual("первые k позиций (проверочная часть — накопитель)", м.сводка()["данные"])
        self.assertEqual(list(range(192)), ldpc.информационные(м).tolist())
        self.assertEqual([0, 1, 2, 7], ldpc.позиции("0-2, 7", 10).tolist())
        with self.assertRaises(ValueError):
            ldpc.позиции("5-20", 10)
        with self.assertRaises(ValueError):
            ldpc.Схема(м, ldpc.позиции("0-9", 384), ldpc.позиции("5-6", 384))


class СнятиеTests(unittest.TestCase):
    def test_перфорация_ошибки_и_сдвиг(self):
        м = ldpc.из_прототипа(прототип(), Z)
        слова = кодовые(м, 200)
        схема = ldpc.Схема(м, np.arange(2 * Z), np.array([], dtype=np.int64))
        данные, подробно = ldpc.снять(передать(слова, схема, доля=0.005), схема)
        self.assertIn("начало слова — бит 37", подробно[0])
        self.assertIn("синдром обнулился у 200 (100.0 %)", подробно[2])
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), данные))

    def test_укорочение(self):
        # Слова с нулями на укороченных позициях: ядро H, дополненной строками
        # «эта позиция — ноль».
        м = ldpc.из_прототипа(прототип(), Z)
        укорочены = np.arange(100, 140)
        H = м.плотная()
        нули = np.zeros((len(укорочены), м.n), dtype=np.uint8)
        нули[np.arange(len(укорочены)), укорочены] = 1
        G = gf2.ядро(np.vstack([H, нули]))
        слова = (np.random.default_rng(5).integers(0, 2, (150, len(G))) @ G % 2).astype(np.uint8)
        схема = ldpc.Схема(м, np.arange(2 * Z), укорочены)
        self.assertEqual(384 - 64 - 40, схема.длина)
        данные, подробно = ldpc.снять(передать(слова, схема, доля=0.01, сдвиг=5), схема)
        self.assertIn("выколото 64, укорочено 40", подробно[1])
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), данные))

    def test_без_вычислимых_проверок_пробным_декодированием(self):
        # Первый блок-столбец — во всех строках: выколов его, ни одной проверки
        # по принятым битам не посчитать; начало ищется декодированием.
        м = ldpc.из_прототипа(прототип(полный_первый=True), Z)
        схема = ldpc.Схема(м, np.arange(Z), np.array([], dtype=np.int64))
        self.assertEqual([], ldpc.вычислимые(схема))
        слова = кодовые(м, 40)
        данные, подробно = ldpc.снять(передать(слова, схема, доля=0.003, сдвиг=11), схема)
        self.assertIn("начало слова — бит 11, найдено пробным декодированием", подробно[0])
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), данные))

    def test_слоем_по_имени(self):
        with tempfile.TemporaryDirectory() as папка:
            прежний, ldpc.КАТАЛОГ = ldpc.КАТАЛОГ, Path(папка)
            try:
                м = ldpc.из_прототипа(прототип(), Z)
                ldpc.сохранить("тест-z32", м, 7)
                self.assertEqual(7, ldpc.список()[0]["владелец"])
                слова = кодовые(м, 60)
                схема = ldpc.Схема(м, np.arange(2 * Z), np.array([], dtype=np.int64))
                ряд, запись = снять_вручную(передать(слова, схема, сдвиг=0),
                                            "ldpc тест-z32 выколоты 0-63 начало 0 итераций 40")
                self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), ряд))
                self.assertIn("выколото 64, укорочено 0", запись.подробно[0])
                for плохое in ("ldpc", "ldpc нет-такой", "ldpc тест-z32 выколоты 0-999",
                               "ldpc ../x"):
                    with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                        снять_вручную(ряд, плохое)
            finally:
                ldpc.КАТАЛОГ = прежний


class МатрицыЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def test_загрузка_список_удаление(self):
        к = self.сеть.client
        self.сеть.login("engineer")
        ответ = к.post("/api/potok-matrices", json={"name": "qc-z32", "kind": "базовая",
                                                    "text": прототип(), "z": Z})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual((384, 192, 0.5), tuple(ответ.json()["matrix"][x]
                                                 for x in ("n", "k", "скорость")))
        self.assertEqual(["qc-z32"], [м["имя"] for м in к.get("/api/potok-matrices").json()["items"]])
        self.assertEqual(400, к.post("/api/potok-matrices", json={
            "name": "плохая", "kind": "базовая", "text": "0 1\n0", "z": 4}).status_code)
        self.assertEqual(400, к.post("/api/potok-matrices", json={
            "name": "../x", "kind": "базовая", "text": прототип(), "z": Z}).status_code)
        # Чужую — ни перезаписать, ни удалить.
        self.сеть.login("gruppa")
        self.assertEqual(409, к.post("/api/potok-matrices", json={
            "name": "qc-z32", "kind": "базовая", "text": прототип(), "z": Z}).status_code)
        self.assertEqual(403, к.delete("/api/potok-matrices/qc-z32").status_code)
        self.сеть.login("engineer")
        self.assertEqual(200, к.delete("/api/potok-matrices/qc-z32").status_code)
        self.assertEqual(404, к.delete("/api/potok-matrices/qc-z32").status_code)


if __name__ == "__main__":
    unittest.main()
