"""Восстановление матрицы нестандартного LDPC, когда слов меньше длины (ldpc_vosst).

Эталоны независимы от проекта: свой квазициклический код (база со всеми столбцами данных хотя бы в двух
строках, лестница чётности) и свой систематический кодер ``ldpc_potoki.Кодер`` (исключением Гаусса по H, слово
проверяется H·c = 0 по строкам H). Каждая восстановленная проверка сверяется со всеми истинными кодовыми
словами (ортогональна каждому), ранг — с рангом истинной H, данные после снятия — с закодированными.

Быстрые — код (768, 576) при 300 словах (слов меньше длины). Медленный (окна сдвинуты на 100 бит, срок 90 с)
— только с REPORTGEN_LDPC_MEDLENNO=1.
"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import ldpc_potoki as лп
from reportgen.potok import gf2, ldpc, razbor
from reportgen.potok import ldpc_vosst as лв
from reportgen.potok.razbor import снять_вручную

МЕДЛЕННО = os.environ.get("REPORTGEN_LDPC_MEDLENNO") == "1"
медленно = unittest.skipUnless(МЕДЛЕННО, "медленный: REPORTGEN_LDPC_MEDLENNO=1")

Z = 32


def свой_qc(mb: int = 6, nb: int = 24, Z: int = Z, сид: int = 11):
    """Свой QC-LDPC: каждый столбец данных — в двух строках базы со случайным сдвигом, чётность — лестница
    с замыканием (вес последнего столбца 2)."""
    rng = np.random.default_rng(сид)
    kb = nb - mb
    P = np.full((mb, nb), -1)
    for j in range(kb):
        for i in rng.choice(mb, size=2, replace=False):
            P[i, j] = rng.integers(0, Z)
    for i in range(mb):
        P[i, kb + i] = 0
        if i:
            P[i, kb + i - 1] = 0
    P[0, kb + mb - 1] = rng.integers(1, Z)
    return ldpc.из_прототипа("\n".join(" ".join(map(str, р)) for р in P), Z)


def _плотная(м) -> np.ndarray:
    H = np.zeros((м.m, м.n), dtype=np.uint8)
    for i, с in enumerate(м.строки):
        H[i, с] = 1
    return H


def _ортогональна(c: np.ndarray, h: np.ndarray) -> bool:
    return not np.bitwise_xor.reduce(c[:, h], axis=1).any()


class Части(unittest.TestCase):
    """Ядро по кусочкам — против прямых расчётов."""

    def test_совпадения_как_перебор(self):
        rng = np.random.default_rng(3)
        а = rng.integers(0, 64, 300).astype(np.uint64)
        б = rng.integers(0, 64, 200).astype(np.uint64)
        i, j = лв._совпадения(а, б, до=1 << 20)
        найдено = set(zip(i.tolist(), j.tolist(), strict=True))
        перебор = {(x, y) for x in range(len(а)) for y in range(len(б)) if а[x] == б[y]}
        self.assertEqual(перебор, найдено)

    def test_совпадения_вырожденных_ключей(self):
        """Все ключи равны (у C мало строк, слова зависимы): пар — до предела, без развёртки всех пар в памяти."""
        а = np.zeros(300_000, dtype=np.uint64)
        i, j = лв._совпадения(а, а, до=1000)
        self.assertEqual(1000, len(i))
        self.assertTrue((а[i] == а[j]).all())

    def test_частичный_гаусс_опорные(self):
        rng = np.random.default_rng(5)
        n, r, t = 200, 60, 40
        A = rng.integers(0, 2, (r, n)).astype(np.uint8)
        приведено, опорные, строки = лв.частичный_гаусс(gf2.упаковать(A), n, t, rng.permutation(n))
        B = gf2.распаковать(приведено, n)
        self.assertEqual(t, len(опорные))
        # Опорные столбцы — единичные: в своей строке 1, в остальных 0; строки — та же линейная оболочка.
        for s, j in enumerate(опорные):
            self.assertEqual(1, int(B[s, j]))
            self.assertEqual(1, int(B[:, j].sum()))
        self.assertEqual(gf2.ранг(A), gf2.ранг(B))
        self.assertEqual(gf2.ранг(A), gf2.ранг(np.concatenate([A, B])))

    def test_базис_ранг_как_у_плотной(self):
        rng = np.random.default_rng(7)
        n = 300
        строки = [np.sort(rng.choice(n, size=rng.integers(2, 9), replace=False)) for _ in range(250)]
        строки += [np.sort(np.flatnonzero(np.bitwise_xor.reduce(
            np.stack([np.isin(np.arange(n), строки[a]) for a in (i, i + 1, i + 2)]), axis=0))) for i in range(40)]
        б = лв.Базис(n)
        for h in строки:
            б.добавить(h)
        A = np.zeros((len(строки), n), dtype=np.uint8)
        for i, h in enumerate(строки):
            A[i, h] = 1
        self.assertEqual(gf2.ранг(A), б.ранг)

    def test_сдвиг_qc_даёт_проверку(self):
        м = свой_qc()
        c = лп.кодовые(м, 64, сид=2)
        for h in м.строки[::17]:
            for s in (1, 5, Z - 1):
                self.assertTrue(_ортогональна(c, лв.сдвиг_qc(np.asarray(h), Z, s)))

    def test_сдвиг_со_смещением_блоков(self):
        """Окна сдвинуты на δ: проверка в окне — h − δ; сдвиг с o = δ mod Z — та же проверка, сдвинутая в слове."""
        м = свой_qc()
        n = м.n
        δ = 100
        for h in м.строки[::23]:
            h = np.asarray(h)
            if h.min() < δ:
                continue
            g = лв.сдвиг_qc_смещ(h - δ, Z, 3, δ % Z, n)
            self.assertIsNotNone(g)
            np.testing.assert_array_equal(np.sort(лв.сдвиг_qc(h, Z, 3) - δ), g)

    def test_граница_по_пересечению_и_краю(self):
        """Проверки по обе стороны бита 400 (и ни одна его не пересекает) — «пересечение», 400 в промежутке;
        проверки своего слова только правее 100 при плотном покрытии — «край», граница в 1…100."""
        rng = np.random.default_rng(9)
        n = 768
        левые = [np.sort(rng.choice(np.arange(0, 400), 6, replace=False)) for _ in range(120)]
        правые = [np.sort(rng.choice(np.arange(400, n), 6, replace=False)) for _ in range(120)]
        b, вид, от, до = лв.граница(левые + правые, n)
        self.assertEqual("пересечение", вид)
        self.assertTrue(от <= 400 <= до, (b, от, до))
        свои = [np.sort(rng.choice(np.arange(100, n), 6, replace=False)) for _ in range(200)]
        b, вид, от, до = лв.граница(свои, n)
        self.assertEqual("край", вид)
        self.assertEqual(1, от)
        self.assertGreaterEqual(до, 100)
        self.assertEqual((0, ""), лв.граница([np.sort(rng.choice(n, 6, replace=False)) for _ in range(200)], n)[:2])

    def test_снимок_туда_и_обратно(self):
        м = свой_qc()
        проверки = [np.asarray(h, dtype=np.int64) for h in м.строки[:50]]
        with tempfile.TemporaryDirectory() as папка:
            путь = str(Path(папка) / "снимок.npz")
            лв.записать_снимок(путь, проверки, {"n": м.n, "начало": 7, "шаг": м.n, "Z": Z, "оператор": "x"})
            итог = лв.прочитать_снимок(путь)
        self.assertEqual((м.n, 7, Z), (итог.n, итог.начало, итог.Z))
        A = _плотная(м)[:50]
        self.assertEqual(gf2.ранг(A), итог.ранг)


class ВосстановлениеQC(unittest.TestCase):
    """Слов (300) меньше длины (768): ранговый поиск не годится, частичный Гаусс + коллизии — да."""

    @classmethod
    def setUpClass(cls):
        cls.м = свой_qc()
        cls.кодер = лп.Кодер(cls.м)
        rng = np.random.default_rng(3)
        cls.u = rng.integers(0, 2, (300, len(cls.кодер.данные))).astype(np.uint8)
        cls.c = cls.кодер.закодировать(cls.u)
        assert лп.синдром_нулевой(cls.м, cls.c).all()
        cls.поток = лп.ошибки(cls.c.reshape(-1), 1e-3, сид=5)
        cls.итог = лв.восстановить_по_потоку(cls.поток, cls.м.n, срок=40, сид=1)

    def test_все_проверки_верны_и_найдены(self):
        итог = self.итог
        self.assertEqual(0, итог.начало)
        self.assertTrue(all(_ортогональна(self.c, h) for h in итог.проверки))
        self.assertEqual(gf2.ранг(_плотная(self.м)), итог.ранг)
        self.assertTrue(итог.точно)
        self.assertEqual(Z, итог.Z)
        self.assertEqual(self.м.n - gf2.ранг(_плотная(self.м)), итог.k)

    def test_alist_в_папку_и_снятие_слоем(self):
        """Сохранённая матрица — обычный файл отдела: слой «ldpc ИМЯ начало 0» снимает поток; данные слов
        (позиции данных — из примечания «# данные») совпадают с закодированными, где декодер сошёлся."""
        with tempfile.TemporaryDirectory() as папка:
            ldpc.подготовить_каталог(Path(папка))
            try:
                имя = лв.сохранить(self.итог, откуда="проверка")
                self.assertIn(имя, {м["имя"] for м in ldpc.список()})
                позиции = ldpc.информационные(ldpc.прочитать(имя))
                ряд, н = снять_вручную(self.поток, f"ldpc {имя} начало 0")
            finally:
                ldpc.КАТАЛОГ = None
        k = len(позиции)
        self.assertEqual(self.итог.k, k)
        вышло = ряд.reshape(-1, k)
        совпало = (вышло == self.c[:len(вышло)][:, позиции]).all(axis=1)
        self.assertGreaterEqual(совпало.mean(), 0.95, н.подробно[:3])

    def test_стоп_возвращает_найденное(self):
        вызовов = [0]

        def стоп():
            вызовов[0] += 1
            return вызовов[0] > 3

        итог = лв.восстановить(лв.слова(self.поток, self.м.n), срок=60, стоп=стоп)
        self.assertEqual("остановлено", итог.почему)
        self.assertTrue(all(_ортогональна(self.c, h) for h in итог.проверки))


class Автомат(unittest.TestCase):
    def test_путь_только_при_известном_длинном_блоке(self):
        б = razbor.Бюджет(time.monotonic() + 600, dict(razbor.ПРОФИЛИ["обычно"]))
        выборка = np.zeros(1 << 20, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as папка:
            ldpc.подготовить_каталог(Path(папка))
            try:
                self.assertEqual([], razbor._восстановить_ldpc(выборка, б, None))
                self.assertEqual([], razbor._восстановить_ldpc(выборка, б, 512))         # ранговый поиск
                self.assertEqual(1, len(razbor._восстановить_ldpc(выборка, б, 3936)))
                быстро = razbor.Бюджет(time.monotonic() + 600, dict(razbor.ПРОФИЛИ["быстро"]))
                self.assertEqual([], razbor._восстановить_ldpc(выборка, быстро, 3936))   # профиль без него
            finally:
                ldpc.КАТАЛОГ = None

    def test_находка_автомата_сохраняет_и_снимает(self):
        м = свой_qc()
        c = лп.кодовые(м, 300, сид=4)
        поток = лп.ошибки(c.reshape(-1), 1e-3, сид=6)
        б = razbor.Бюджет(time.monotonic() + 600, dict(razbor.ПРОФИЛИ["обычно"]))
        with tempfile.TemporaryDirectory() as папка:
            ldpc.подготовить_каталог(Path(папка))
            try:
                with mock.patch.object(razbor, "ВОССТ_СЛОВ_ОТ", 16), \
                        mock.patch.dict(б.профиль, {"длинные": {"до": 256, "бюджет": 8.0}, "ldpc_восст": 40.0}):
                    (_, вызов, _), = razbor._восстановить_ldpc(поток, б, м.n)
                    находка = вызов()
                    self.assertIsNotNone(находка)
                    self.assertIn("почему этот путь", находка.подробно[0])
                    имя = находка.свойства["матрица"]
                    self.assertTrue((Path(папка) / f"{имя}.alist").exists())
                    слои = razbor.слои_находки(находка)
                    self.assertTrue(any(с.startswith(f"ldpc {имя}") for с in слои), слои)
            finally:
                ldpc.КАТАЛОГ = None


class ЧерезСервер(unittest.TestCase):
    """Окно LDPC: «Восстановить матрицу» в фоне (ход, итог), «Стоп», «Сохранить матрицу», просмотр по ней."""

    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — общая заготовка веб-тестов

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.старый = ldpc.КАТАЛОГ
        self.addCleanup(setattr, ldpc, "КАТАЛОГ", self.старый)
        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client
        self.assertEqual(200, self.к.get("/api/potok-ldpc-types").status_code)

    def задание(self, поток: np.ndarray) -> str:
        з = self.сеть.app.state.potok
        ид = з.создать(владелец=self.сеть.repos.users.by_login("engineer").id, имя="ldpc.bin",
                       данные=np.packbits(поток).tobytes(), разбирать=False, бит=len(поток))
        for _ in range(200):
            if з.прочитать(ид)["состояние"] == "готово":
                break
            time.sleep(0.05)
        return ид

    def дождаться(self, поиск: str) -> dict:
        for _ in range(1200):
            с_ = self.к.get(f"/api/potok-ldpc-recover/{поиск}").json()
            if с_["готово"]:
                return с_
            time.sleep(0.1)
        self.fail("восстановление не закончилось")

    def test_восстановить_сохранить_и_снять(self):
        м = свой_qc()
        c = лп.кодовые(м, 300, сид=3)
        поток = лп.ошибки(c.reshape(-1), 1e-3, сид=5)
        ид = self.задание(поток)
        о = self.к.post(f"/api/potok/{ид}/ldpc/recover", json={"stage": 0, "n": м.n, "срок": 40})
        self.assertEqual(200, о.status_code, о.text)
        с_ = self.дождаться(о.json()["поиск"])
        self.assertEqual("", с_["ошибка"])
        итог = с_["итог"]
        self.assertEqual((gf2.ранг(_плотная(м)), Z, 0), (итог["ранг"], итог["Z"], итог["начало"]))
        сохр = self.к.post(f"/api/potok-ldpc-recover/{о.json()['поиск']}/save", json={"имя": "мой-восст"})
        self.assertEqual(200, сохр.status_code, сохр.text)
        self.assertEqual(("мой-восст", "ldpc мой-восст начало 0"), (сохр.json()["имя"], сохр.json()["слой"]))
        self.assertTrue((self.сеть.tmp / "ldpc" / "мой-восст.alist").is_file())
        п = {"тип": "Нестандарт", "код": "мой-восст", "начало": "0"}
        просмотр = self.к.post(f"/api/potok/{ид}/ldpc/preview", json={"stage": 0, "параметры": п})
        self.assertEqual(200, просмотр.status_code, просмотр.text)
        self.assertGreaterEqual(просмотр.json()["сошлось"], 95)
        # Ошибки запроса.
        for тело, ждём in (({"n": 8}, "n — от 16 до 131072"), ({"n": м.n, "шаг": 10}, "шаг — не меньше длины слова"),
                           ({"n": м.n, "срок": 1}, "срок — от 5 до 7200 с")):
            with self.subTest(тело):
                о_ = self.к.post(f"/api/potok/{ид}/ldpc/recover", json={"stage": 0, **тело})
                self.assertEqual((400, ждём), (о_.status_code, о_.json()["error"]))
        self.assertEqual(404, self.к.get("/api/potok-ldpc-recover/нет").status_code)
        self.assertEqual(404, self.к.post("/api/potok-ldpc-recover/нет/stop", json={}).status_code)
        # Чужое — не видно.
        self.сеть.login("admin")
        self.assertEqual(404, self.к.get(f"/api/potok-ldpc-recover/{о.json()['поиск']}").status_code)

    def test_стоп_оставляет_найденное(self):
        м = свой_qc()
        c = лп.кодовые(м, 300, сид=3)
        поток = лп.ошибки(c.reshape(-1), 3e-3, сид=5)     # шум побольше — дольше
        ид = self.задание(поток)
        поиск = self.к.post(f"/api/potok/{ид}/ldpc/recover", json={"stage": 0, "n": м.n, "срок": 600}).json()["поиск"]
        for _ in range(300):
            if "раунд" in self.к.get(f"/api/potok-ldpc-recover/{поиск}").json()["ход"]:
                break
            time.sleep(0.1)
        time.sleep(4)                       # снимок пишется не чаще раза в 3 с
        self.assertEqual({"ok": True}, self.к.post(f"/api/potok-ldpc-recover/{поиск}/stop", json={}).json())
        с_ = self.дождаться(поиск)
        # Остановлено (найденное — из снимка) или успело кончиться само раньше «Стоп».
        if с_["итог"]["ранг"] == 0:
            self.assertIn(с_["итог"]["почему"], ("остановлено", "остановлено до первого раунда"))
        if с_["итог"]["ранг"]:
            сохр = self.к.post(f"/api/potok-ldpc-recover/{поиск}/save", json={})
            self.assertEqual(200, сохр.status_code, сохр.text)
            self.assertTrue((self.сеть.tmp / "ldpc" / f"{сохр.json()['имя']}.alist").is_file())
            файл = ldpc.прочитать(сохр.json()["имя"])
            self.assertTrue(all(_ортогональна(c, np.asarray(h)) for h in файл.строки))


class СдвинутыеОкна(unittest.TestCase):
    @медленно
    def test_начало_слова_находится(self):
        м = свой_qc()
        c = лп.кодовые(м, 400, сид=3)
        поток = лп.сдвинуть(лп.ошибки(c.reshape(-1), 1e-3, сид=5), 100)
        итог = лв.восстановить_по_потоку(поток, м.n, срок=90)
        self.assertEqual(100, итог.начало)
        self.assertEqual(gf2.ранг(_плотная(м)), итог.ранг)
        self.assertTrue(all(_ортогональна(c, h) for h in итог.проверки))


if __name__ == "__main__":
    unittest.main()
