"""LDPC по матрице из стандарта: загрузка H, перфорация, укорочение, снятие слоем."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        # Строки — по возрастанию столбцов (накопитель дописан к адресам не по порядку).
        self.assertTrue(all(np.all(np.diff(с) > 0) for с in м.строки))
        with self.assertRaises(ValueError):
            ldpc.из_адресов("1 2", n, k, группа=G_)

    def test_ошибки_таблиц(self):
        """Ошибка в таблице — понятное сообщение, а не молча неверная матрица."""
        H = ldpc.из_прототипа(прототип(), Z).плотная()
        m = H.shape[0]
        линии = в_alist(H).splitlines()
        первый_столбец = линии[4].split()
        первый_столбец[0] = str(m + 1)
        линии[4] = " ".join(первый_столбец)
        with self.assertRaisesRegex(ValueError, f"alist: строка {m + 1} вне 1…{m} в столбце 1"):
            ldpc.из_alist("\n".join(линии))
        with self.assertRaisesRegex(ValueError, "есть строка без единого блока"):
            ldpc.из_прототипа("0 1\n-1 -1", 4)
        with self.assertRaisesRegex(ValueError, "адреса: 40 вне 0…31"):
            ldpc.из_адресов("1\n2\n40\n3", 64, 32, группа=8)

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

    def test_укороченные_как_известные_нули(self):
        # Выколото 64 и укорочено 128 из 384: стёртых было бы 192 при 192 проверках —
        # декодер справляется, только если укороченные — известные нули, а не стирания.
        м = ldpc.из_прототипа(прототип(), Z)
        укорочены = np.arange(64, 192)
        H = м.плотная()
        нули = np.zeros((len(укорочены), м.n), dtype=np.uint8)
        нули[np.arange(len(укорочены)), укорочены] = 1
        G = gf2.ядро(np.vstack([H, нули]))
        слова = (np.random.default_rng(9).integers(0, 2, (60, len(G))) @ G % 2).astype(np.uint8)
        схема = ldpc.Схема(м, np.arange(2 * Z), укорочены)
        данные, подробно = ldpc.снять(передать(слова, схема, доля=0.002, сдвиг=0), схема, начало=0)
        self.assertIn("синдром обнулился у 60 (100.0 %)", подробно[1])
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), данные))

    def test_без_вычислимых_проверок_сложенными_парами(self):
        # Первый блок-столбец — во всех строках: выколов его, ни одной одиночной проверки
        # по принятым битам не посчитать; но строки разных блоков, задевающие один и тот
        # же выколотый столбец, в сумме его не содержат — начало ищется по суммам пар.
        м = ldpc.из_прототипа(прототип(полный_первый=True), Z)
        схема = ldpc.Схема(м, np.arange(Z), np.array([], dtype=np.int64))
        self.assertEqual([], ldpc.вычислимые(схема))
        парами = ldpc.сложенные(схема, 1000)
        self.assertEqual(5 * Z, len(парами))                # шесть строк на столбец — пять пар
        H = м.плотная()
        for проверка in парами[:20]:
            # Сумма пары — в пространстве строк H и без выколотых: слово её выполняет.
            слово = кодовые(м, 1, сид=len(проверка))[0][схема.переданы]
            self.assertEqual(0, int(слово[проверка].sum()) % 2)
        self.assertTrue(all(len(п) <= 2 * max(H.sum(axis=1)) - 2 for п in парами))
        # С укороченными позициями: они — известные нули, из сумм выброшены.
        укорочены = np.arange(100, 120)
        с_укорочением = ldpc.Схема(м, np.arange(Z), укорочены)
        нули = np.zeros((len(укорочены), м.n), np.uint8)
        нули[np.arange(len(укорочены)), укорочены] = 1
        G = gf2.ядро(np.vstack([H, нули]))
        укороченные = (np.random.default_rng(5).integers(0, 2, (4, len(G))) @ G % 2).astype(np.uint8)
        переданные = укороченные[:, с_укорочением.переданы]
        for проверка in ldpc.сложенные(с_укорочением, 1000):
            self.assertTrue(0 <= проверка.min() and проверка.max() < с_укорочением.длина)
            self.assertTrue(np.all(переданные[:, проверка].sum(axis=1) % 2 == 0))
        слова = кодовые(м, 40)
        данные, подробно = ldpc.снять(передать(слова, схема, доля=0.003, сдвиг=11), схема)
        self.assertIn("начало слова — бит 11, найдено по 160 вычислимым проверкам (из них 160 — суммы "
                      "строк, в которых выколотые позиции сокращаются)", подробно[0])
        self.assertTrue(np.array_equal(слова[:, :192].reshape(-1), данные))

    def test_сложенные_с_опорными_строками(self):
        """Две выколотые позиции в строке и нет второй строки с теми же: к ней прибавляются
        опорные — строки, где выколота ровно одна позиция, по одной на каждую (так у AR4JA)."""
        м = ldpc.из_прототипа("0 3 1 -1 0 -1 -1 -1\n2 -1 5 1 0 0 -1 -1\n"
                              "-1 4 -1 2 -1 0 0 -1\n6 2 -1 3 -1 -1 0 0", 8)
        схема = ldpc.Схема(м, np.arange(16), np.array([], np.int64))
        self.assertEqual([], ldpc.вычислимые(схема))
        сложено = ldpc.сложенные(схема, 1000)
        self.assertEqual(16, len(сложено))            # строки блоков 0 и 3, каждая с двумя опорными
        слова = кодовые(м, 5)[:, схема.переданы]
        for проверка in сложено:
            self.assertTrue(np.all(слова[:, проверка].sum(axis=1) % 2 == 0))
        with mock.patch.object(ldpc, "СЛОЖЕННЫХ_СТРОК_ДО", 2):
            self.assertEqual([], ldpc.сложенные(схема, 1000))          # только пары — их нет

    def test_без_вычислимых_проверок_пробным_декодированием(self):
        # Нет и сумм пар — начало ищется декодированием.
        м = ldpc.из_прототипа(прототип(полный_первый=True), Z)
        схема = ldpc.Схема(м, np.arange(Z), np.array([], dtype=np.int64))
        слова = кодовые(м, 40)
        with mock.patch.object(ldpc, "сложенные", return_value=[]):
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
