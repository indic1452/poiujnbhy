"""Турбокоды стандартов (TCC): свой кодер по тексту стандарта → ошибки → данные бит в бит.

Кодер тестов — ``turbo_koder`` (побитовые циклы по формулам первоисточников, без общих
таблиц с анализатором); таблицы анализатора сверяются с текстом самих стандартов
(istochniki/turbo/…*.pdf.txt). Вслепую: выколотый 1/2 UMTS и CCSDS (с маркером и без),
1/3 CCSDS; ложных находок нет на случайном потоке, свёрточном коде и RSC 1/2; турбокод
с перемежителем вне каталога — честно «схема не опознана», без данных, и автомат на нём
не уходит в цепочку ложных блочных кодов (предел времени).
"""

import json
import re
import shutil
import subprocess
import time
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
import turbo_koder as тк
from reportgen.potok import razbor, svyortka
from reportgen.potok import turbo_std as ts


def данные(блоков: int, K: int, сид: int = 1) -> np.ndarray:
    return np.random.default_rng(сид).integers(0, 2, (блоков, K), dtype=np.uint8)


def поток(кодер, u: np.ndarray, маркер: str = "") -> np.ndarray:
    м = list(np.unpackbits(np.frombuffer(bytes.fromhex(маркер), dtype=np.uint8))) if маркер else []
    return np.concatenate([м + list(кодер([int(b) for b in ряд])) for ряд in u]).astype(np.uint8)


class КодерыСовпадают(unittest.TestCase):
    """Кодер анализатора (по решётке и раскладке) = свой кодер теста по тексту стандарта."""

    def сверить(self, схема, кодер, u):
        свой = np.array([кодер([int(b) for b in ряд]) for ряд in u], dtype=np.uint8)
        self.assertTrue(np.array_equal(свой, схема.закодировать(u)), схема.имя)

    def test_umts_все_виды_матрицы(self):
        # R = 5, 10, 20; C = p − 1, p, p + 1; K = R·C при C = p + 1 (обмен U); 481…530 (p = 53);
        # особые T для 2281…2480 и 3161…3210; предельные 40 и 5114.
        for K in (40, 159, 160, 200, 201, 481, 530, 531, 1060, 1080, 2281, 2480, 2481, 3161, 3210, 3211,
                  3200, 5114, 997):
            with self.subTest(K=K):
                self.сверить(ts.umts(K), тк.umts_кодировать, данные(2, K, K))

    def test_umts_согласование_скорости(self):
        for K, E in ((40, 88), (40, 131), (40, 130), (40, 132), (200, 300), (1000, 2008), (1000, 2500),
                     (531, 1100)):
            with self.subTest(K=K, E=E):
                self.сверить(ts.umts(K, E), lambda u, E=E: тк.umts_согласовать(тк.umts_кодировать(u), E),
                             данные(2, K, E))

    def test_umts_по_шаблону(self):
        for шаблон in ("11/10/01", "111/100/011", "1/1/0"):
            with self.subTest(шаблон=шаблон):
                x, y1, y2 = шаблон.split("/")

                def кодер(u, x=x, y1=y1, y2=y2):
                    c = тк.umts_кодировать(u)
                    K = len(u)
                    return [c[3 * k + b] for k in range(K) for b, строка in enumerate((x, y1, y2))
                            if строка[k % len(строка)] == "1"] + c[3 * K:]
                self.сверить(ts.umts_шаблон(200, шаблон), кодер, данные(2, 200, 5))
        for плохой in ("11/10", "11/1/01", "12/10/01", "11/10/01/1"):
            with self.assertRaises(ValueError):
                ts.umts_шаблон(40, плохой)

    def test_lte_длина_по_умолчанию(self):
        self.assertEqual(3 * 40 + 12, ts.lte(40).длина)
        self.assertEqual(1, ts.lte(40, 1).длина)
        with self.assertRaises(ValueError):
            ts.lte(40, 0)

    def test_lte_кольцевой_буфер(self):
        таблица = тк.lte_таблица()
        for K, E, rv in ((40, 132, 0), (40, 500, 3), (512, 1000, 1), (1024, 2048, 2), (6144, 9000, 0)):
            with self.subTest(K=K, E=E, rv=rv):
                self.сверить(ts.lte(K, E, rv),
                             lambda u, K=K, E=E, rv=rv: тк.lte_согласовать(тк.lte_кодировать(u, *таблица[K]), E, rv),
                             данные(1, K, K))

    def test_ccsds_все_скорости(self):
        for k in (1784, 8920):
            for скорость in ("1/2", "1/3", "1/4", "1/6"):
                with self.subTest(k=k, скорость=скорость):
                    self.сверить(ts.ccsds(k, скорость), lambda u, r=скорость: тк.ccsds_кодировать(u, r),
                                 данные(1, k, k))

    def test_rcs_все_размеры_и_скорости(self):
        for N in ts.RCS_ПАРАМЕТРЫ:
            for скорость in ts.RCS_ВЫКАЛЫВАНИЕ:
                with self.subTest(N=N, скорость=скорость):
                    self.сверить(ts.rcs(N, скорость), lambda u, r=скорость: тк.rcs_кодировать(u, r),
                                 данные(1, 2 * N, N))

    def test_rcs_обратный_порядок(self):
        u = данные(1, 424)
        прямой = тк.rcs_кодировать([int(b) for b in u[0]], "1/2")
        обратный = ts.rcs(212, "1/2", "обратный").закодировать(u)[0]
        # п. 6.4.4.4: сначала Y1,Y2 (и W), затем A,B.
        self.assertEqual(прямой[424:] + прямой[:424], обратный.tolist())


class РешёткиTests(unittest.TestCase):
    def test_векторы_проверяются(self):
        with self.assertRaises(AssertionError):
            ts.решётка_rsc("0011", ["1101"], "без текущего входа в обратной связи")
        with self.assertRaises(AssertionError):
            ts.решётка_rsc("1011", ["11011"], "прямой длиннее обратной")

    def test_rcs_как_на_рис_16(self):
        # Состояние 0, вход (A, B) = (1, 0): в s1 — 1, Y = W = 1; (0, 1): B ещё и в s2, s3.
        р = ts.RCS_РЕШЁТКА
        self.assertEqual((4, [1, 1]), (int(р.следующее[0, 2]), р.выходы[0, 2].tolist()))
        self.assertEqual((7, [1, 1]), (int(р.следующее[0, 1]), р.выходы[0, 1].tolist()))


class MapTests(unittest.TestCase):
    """Один проход max-log-MAP: края решётки (кольцо, обнулённый хвост) и перемежение метрик."""

    def test_перемежение_метрик_обратимо(self):
        x = np.random.default_rng(1).random((2, 212, 4)).astype(np.float32)
        схема = ts.rcs(212, "1/2")
        self.assertTrue(np.array_equal(x, схема._обратно(схема._перемежить(x.copy()))))
        # Уровень 1 EN 301 790: на чётных j метрика (A, B) берётся у (B, A).
        y = схема._перемежить(x.copy())
        self.assertTrue(np.array_equal(y[:, 0], x[:, схема.π[0]][:, [0, 2, 1, 3]]))
        self.assertTrue(np.array_equal(y[:, 1], x[:, схема.π[1]]))

    def проход(self, схема, стереть):
        """Первый кодер, чистое слово, стёртые шаги ``стереть``: решения на всех шагах."""
        u = данные(12, схема.K, 3)
        llr = 1.0 - 2.0 * схема.закодировать(u).astype(np.float32)
        с1, п1, _, _ = схема.разложить(llr)
        с1[:, стереть] = 0
        п1[:, стереть] = 0
        б = схема.р1.бит_входа
        край = "кольцо" if схема.кольцо else "ноль"
        а = ts._мап(ts._метрики(с1, б), п1, схема.р1, край, край)
        return схема._в_биты(а.argmax(axis=2)[:, :схема.символов], б).reshape(len(u), -1), u

    def test_кольцо_восстанавливает_стёртые_края(self):
        # Стёрта первая (последняя) пара: состояние на краю известно только через кольцо —
        # разгон по концу блока вперёд (по началу — назад).
        схема = ts.rcs(212, "1/3")
        for край in (0, 211):
            решения, u = self.проход(схема, [край])
            self.assertTrue(np.array_equal(u, решения), край)

    def test_хвост_обнуляет_регистр(self):
        # Стёрт последний бит данных: его даёт только путь в нулевое состояние через хвост.
        схема = ts.umts(200)
        решения, u = self.проход(схема, [199])
        self.assertTrue(np.array_equal(u, решения))


class ДекодерИсправляет(unittest.TestCase):
    def проверить(self, схема, кодер, u, доля):
        кодовые = np.array([кодер([int(b) for b in ряд]) for ряд in u], dtype=np.uint8)
        принято = тк.ошибки(кодовые, доля)
        self.assertGreater(int((принято != кодовые).sum()), 0)
        итог, св = схема.декодировать(1.0 - 2.0 * принято)
        self.assertTrue(np.array_equal(итог, u), f"{схема.имя}: ошибок {int((итог != u).sum())}")

    def test_umts_1_3_и_1_2(self):
        self.проверить(ts.umts(1000), тк.umts_кодировать, данные(4, 1000), 0.04)
        self.проверить(ts.umts(1000, 2008), lambda u: тк.umts_согласовать(тк.umts_кодировать(u), 2008),
                       данные(4, 1000, 2), 0.01)

    def test_umts_на_пределе(self):
        # 11 % ошибок в жёстких решениях: верный обмен внешней информацией справляется,
        # а с ошибкой в ней (апостериорная + входная вместо разности) — нет.
        u = данные(8, 1000, 1)
        кодовые = np.array([тк.umts_кодировать([int(b) for b in р]) for р in u], dtype=np.uint8)
        итог, _ = ts.umts(1000).декодировать(1.0 - 2.0 * тк.ошибки(кодовые, 0.11, сид=2))
        self.assertTrue(np.array_equal(u, итог))

    def test_размеры_вне_стандарта(self):
        for вызов in (lambda: ts.umts(39), lambda: ts.umts(5115), lambda: ts.lte(41), lambda: ts.ccsds(1785, "1/2"),
                      lambda: ts.ccsds(1784, "2/3"), lambda: ts.rcs(100, "1/2"), lambda: ts.rcs(212, "5/6"),
                      lambda: ts.umts(40, 200), lambda: ts.lte(40, 100, 4), lambda: ts.umts_шаблон(40, "11/10")):
            with self.assertRaises(ValueError):
                вызов()
        self.assertEqual(40, ts.umts(40).K)
        self.assertEqual(5114, len(ts.umts_перемежитель(5114)))

    def test_lte(self):
        f = тк.lte_таблица()[1024]
        self.проверить(ts.lte(1024, 2048, 0), lambda u: тк.lte_согласовать(тк.lte_кодировать(u, *f), 2048, 0),
                       данные(3, 1024), 0.01)

    def test_ccsds(self):
        for скорость, доля in (("1/2", 0.015), ("1/6", 0.06)):
            self.проверить(ts.ccsds(1784, скорость), lambda u, r=скорость: тк.ccsds_кодировать(u, r),
                           данные(2, 1784), доля)

    def test_rcs(self):
        for скорость, доля in (("1/3", 0.04), ("2/3", 0.005)):
            self.проверить(ts.rcs(212, скорость), lambda u, r=скорость: тк.rcs_кодировать(u, r),
                           данные(4, 424), доля)


@unittest.skipUnless(тк.ИСТОЧНИКИ.is_dir(), "первоисточники не приложены (в репозитории только опись)")
class ТаблицыПоТекстуИсточника(unittest.TestCase):
    """Числа анализатора — те же, что в тексте стандартов (istochniki/turbo)."""

    def test_umts_табл_2(self):
        с_ = тк.страницы(тк.ИСТОЧНИКИ / "tcc/3gpp/ETSI_TS_125_212_v17.0.0_UMTS.pdf.txt")
        текст = "".join(с_.values())
        б = текст[текст.index("Table 2: List of prime number p and associated primitive root v"):]
        б = б[:б.index("4.2.3.2.3.2")]
        числа = [int(ч) for ч in re.findall(r"^\s*(\d+)\s*$", б, re.M)]
        self.assertEqual(ts.UMTS_ПРОСТЫЕ, dict(zip(числа[0::2], числа[1::2], strict=True)))
        # И это наименьшие первообразные корни (как их понимает свой кодер).
        self.assertTrue(all(тк._первообразный(p) == v for p, v in ts.UMTS_ПРОСТЫЕ.items()))

    def test_lte_qpp(self):
        self.assertEqual(ts.LTE_QPP, тк.lte_таблица())

    def test_lte_столбцы(self):
        с_ = тк.страницы(тк.ИСТОЧНИКИ / "tcc/3gpp/ETSI_TS_136_212_v17.1.0_LTE.pdf.txt")
        текст = " ".join(" ".join(т.split()) for т in с_.values())
        self.assertIn("< 0, 16, 8, 24, 4, 20, 12, 28, 2, 18, 10, 26, 6, 22, 14, 30, 1, 17, 9, 25, 5, 21, "
                      "13, 29, 3, 19, 11, 27, 7, 23, 15, 31 >", текст)
        self.assertEqual(list(ts.LTE_P), тк.P_LTE)

    def test_ccsds(self):
        с_ = тк.страницы(тк.ИСТОЧНИКИ / "tcc/ccsds/CCSDS_131.0-B-5.pdf.txt")
        с42 = " ".join(с_[42].split())
        self.assertEqual(list(ts.CCSDS_ПРОСТЫЕ), [int(x) for x in re.findall(r"p\d = (\d+)", с42)])
        с66 = " ".join(с_[66].split())
        for скорость, маркер in ts.CCSDS_ASM.items():
            м = re.search(rf"ASM for rate-{скорость} Turbo[^:]*: ([0-9A-F ]+?) (?:ASM|FIRST)", с66)
            self.assertEqual(маркер, м.group(1).replace(" ", ""))
        с44 = " ".join(с_[44].split()) + " ".join(с_[43].split())
        for g in ("G0 = 10011", "G1 = 11011", "G2 = 10101", "G3 = 11111"):
            self.assertIn(g, с44)

    def test_rcs_табл_9(self):
        т = (тк.ИСТОЧНИКИ / "tcc/dvb-rcs/ETSI_EN_301_790_v1.5.1_DVB-RCS.pdf.txt").read_text(errors="replace")
        б = т[т.index("Table 9: Turbo code permutation parameters"):]
        б = б[:б.index("6.4.4.2")]
        строки = {int(N): tuple(map(int, (a, b, c_, d))) for N, a, b, c_, d in
                  re.findall(r"N = (\d+) \(\d+ bytes\)\s*\n\s*(\d+)\s*\n\s*\{(\d+),(\d+),(\d+)\}", б)}
        self.assertEqual(ts.RCS_ПАРАМЕТРЫ, строки)
        # Правило чёт/нечет (стр. 31) — у перестановки по табл. 9.
        for N in строки:
            π = ts.rcs_перемежитель(N)
            self.assertEqual(sorted(π.tolist()), list(range(N)))
            self.assertTrue(all(i % 2 != j % 2 for j, i in enumerate(π.tolist())))


class Вслепую(unittest.TestCase):
    def test_umts_1_2_с_любого_бита(self):
        u = данные(40, 1000, 7)
        п = тк.ошибки(поток(lambda x: тк.umts_согласовать(тк.umts_кодировать(x), 2008), u), 0.003)
        for сдвиг in (0, 5, 777):
            with self.subTest(сдвиг=сдвиг):
                н = ts.найти(п[сдвиг:])
                self.assertIn("UMTS (TS 25.212) K = 1000, E = 2008", н.что)
                первый = -(-сдвиг // 2008)
                d = н.дальше.reshape(-1, 1000)
                self.assertTrue(np.array_equal(u[первый:первый + len(d)], d))
                self.assertEqual(f"tcc umts 1000 e 2008 начало {первый * 2008 - сдвиг}", н.свойства["указание"])

    def test_ccsds_1_2_и_1_3_с_маркером_и_без(self):
        u = данные(26, 1784, 8)
        for скорость in ("1/2", "1/3"):
            for маркер in ("", ts.CCSDS_ASM[скорость]):
                with self.subTest(скорость=скорость, маркер=bool(маркер)):
                    п = тк.ошибки(поток(lambda x, r=скорость: тк.ccsds_кодировать(x, r), u, маркер), 0.004)[1001:]
                    н = ts.найти(п)
                    self.assertIn(f"CCSDS (131.0-B-5) k = 1784, скорость {скорость}", н.что)
                    d = н.дальше.reshape(-1, 1784)
                    self.assertTrue(np.array_equal(u[1:1 + len(d)], d))

    def test_не_турбо(self):
        self.assertIsNone(ts.найти(с.случайные_биты(200_000, сид=3)))
        self.assertIsNone(ts.найти(svyortka.кодировать(с.случайные_биты(60_000), [0o171, 0o133], 7)))
        # RSC 1/2 (13/15) без второго кодера: связь держится на всех шагах — не турбокод.
        u = с.случайные_биты(60_000, сид=4)
        p, _ = с.rsc(u)
        self.assertIsNone(ts.найти(np.column_stack([u, p]).reshape(-1)))

    def test_перемежитель_вне_каталога_честно(self):
        u = данные(60, 1000, 9)
        схема = ts.umts(1000, 2008)
        схема.π = np.random.default_rng(5).permutation(1000)
        п = тк.ошибки(схема.закодировать(u).reshape(-1), 0.003)
        н = ts.найти(п[11:])
        self.assertIn("турбокод (PCCC) выколотый 1/2, RSC 13/15 (UMTS, LTE), блок 2008 бит — схема не опознана",
                      н.что)
        self.assertIsNone(н.дальше)
        self.assertTrue(н.свойства["турбо"])
        self.assertTrue(н.мера.startswith("составляющий код RSC 13/15 (UMTS, LTE): связь систематики"))

    def test_начало_по_второй_на_коротком_потоке(self):
        схема = ts.ccsds(1784, "1/2")
        с_ = ts.СОСТАВЛЯЮЩИЕ[1]
        self.assertIsNone(ts._начало_по_второй(с.случайные_биты(5000), схема, с_, 0, 2, time.monotonic() + 5))
        self.assertIsNone(ts._начало_по_второй(с.случайные_биты(20_000), схема, с_, 0, 2, time.monotonic() + 30))


class Автомат(unittest.TestCase):
    """Выколотый турбокод 1/2 автомат снимает быстро и без ложной цепочки блочных кодов."""

    def разобрать(self, п):
        начало = time.monotonic()
        р = razbor.разобрать(данные=np.packbits(п).tobytes(), имя="t.bin", профиль="быстро")
        return р, time.monotonic() - начало

    def test_снимает_до_ip(self):
        биты = np.unpackbits(np.frombuffer(с.hdlc(с.пакеты_ip(150)), dtype=np.uint8))
        u = биты[:len(биты) // 1000 * 1000].reshape(-1, 1000)
        п = тк.ошибки(поток(lambda x: тк.umts_согласовать(тк.umts_кодировать(x), 2008), u), 0.003)[333:]
        р, секунд = self.разобрать(п)
        что = [н.что for н in р.находки]
        self.assertIn("турбокод UMTS (TS 25.212) K = 1000, E = 2008 (согласование скорости)", что[0])
        self.assertTrue(any("пакеты IP" in ч for ч in что))
        self.assertFalse(any("линейный блочный" in ч for ч in что))
        self.assertLess(секунд, 60)

    def test_вне_каталога_без_ложной_цепочки(self):
        u = данные(120, 1000, 10)
        схема = ts.umts(1000, 2008)
        схема.π = np.random.default_rng(6).permutation(1000)
        п = тк.ошибки(схема.закодировать(u).reshape(-1), 0.003)
        р, секунд = self.разобрать(п)
        что = [н.что for н in р.находки]
        self.assertEqual(1, len(что))
        self.assertIn("схема не опознана", что[0])
        self.assertLess(секунд, 60)


class СлойВручную(unittest.TestCase):
    def test_umts_и_ccsds_asm(self):
        u = данные(10, 1000, 11)
        п = тк.ошибки(поток(lambda x: тк.umts_согласовать(тк.umts_кодировать(x), 2008), u), 0.003)[100:]
        ряд, находка = razbor.снять_вручную(п, "tcc umts 1000 e 2008 начало 1908")
        self.assertTrue(np.array_equal(u[1:].reshape(-1), ряд))
        self.assertTrue(any("TS 125 212" in с_ for с_ in находка.подробно))
        u = данные(6, 1784, 12)
        п = тк.ошибки(поток(lambda x: тк.ccsds_кодировать(x, "1/4"), u, ts.CCSDS_ASM["1/4"]), 0.01)[500:]
        ряд, _ = razbor.снять_вручную(п, "tcc ccsds 1784 1/4 asm")
        self.assertTrue(np.array_equal(u[1:].reshape(-1), ряд))

    def test_rcs_и_lte(self):
        u = данные(5, 424, 13)
        п = поток(lambda x: тк.rcs_кодировать(x, "1/2"), u)
        ряд, _ = razbor.снять_вручную(тк.ошибки(п, 0.01), "tcc rcs 212 1/2")
        self.assertTrue(np.array_equal(u.reshape(-1), ряд))
        f = тк.lte_таблица()[512]
        u = данные(4, 512, 14)
        п = поток(lambda x: тк.lte_согласовать(тк.lte_кодировать(x, *f), 1100, 1), u)
        ряд, _ = razbor.снять_вручную(тк.ошибки(п, 0.01), "tcc lte 512 e 1100 rv 1")
        self.assertTrue(np.array_equal(u.reshape(-1), ряд))

    def test_ошибка_указания(self):
        with self.assertRaises(ValueError):
            razbor.снять_вручную(с.случайные_биты(1000), "tcc что-то")
        with self.assertRaises(ValueError):
            razbor.снять_вручную(с.случайные_биты(1000), "tcc rcs 100 1/2")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноКодовTests(unittest.TestCase):
    """Вкладки окна «Коды» и пункты стола из app.js; слой каждого пункта понимает сервер."""

    @classmethod
    def setUpClass(cls):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js([], ["ВКЛАДКИ_КОДОВ", "МОДУЛЯЦИИ_ПОИСКА", "ОПЕРАЦИИ_СТОЛА"])
        код += "process.stdout.write(JSON.stringify({в: ВКЛАДКИ_КОДОВ, о: ОПЕРАЦИИ_СТОЛА}));"
        готово = subprocess.run(["node", "-e", код], capture_output=True, text=True, timeout=20)
        assert готово.returncode == 0, готово.stderr
        д = json.loads(готово.stdout)
        cls.вкладки = д["в"]
        cls.операции = {о["id"]: о for о in д["о"]}

    @staticmethod
    def заполнить(о, значения=None):
        """Как ``заполнить`` в app.js: флаг — его текст, приставка — перед непустым значением."""
        значения = значения or {}
        def поле(м):
            п = next(x for x in о.get("поля", []) if x["ключ"] == м.group(1))
            в = значения.get(п["ключ"], п.get("по", False if "флаг" in п else ""))
            if "флаг" in п:
                return п["флаг"] if в else ""
            if "приставка" in п:
                return п["приставка"] + str(в).strip() if str(в).strip() else ""
            return str(в).strip()
        return re.sub(r"\s+", " ", re.sub(r"\{([^}]+)\}", поле, о["слой"])).strip()

    def test_вкладки_как_у_отдела(self):
        self.assertEqual(["TCC", "НСК", "ССК", "RS", "Trellis", "КБК"], [в["имя"] for в in self.вкладки])
        for в in self.вкладки:
            for ид in в["пункты"]:
                self.assertIn(ид, self.операции, в["имя"])
        # Каждое пояснение — цельный текст с источником (склейка строк не сломана).
        источники = {"TCC": ("TS 25.212", "max-log-MAP", "так и пишется."), "НСК": ("EN 300 421", "(3/4, 7/8)."),
                     "ССК": ("IESS-309", "в открытых источниках их нет."), "RS": ("ETR 192", "(допущение)."),
                     "Trellis": ("EN 301 210", "гипотеза."), "КБК": ("RU2317641", "RS IESS.")}
        for в in self.вкладки:
            self.assertNotIn("NaN", в["суть"], в["имя"])
            for кусок in источники[в["имя"]]:
                self.assertIn(кусок, в["суть"], в["имя"])
        self.assertEqual([], next(в for в in self.вкладки if в["имя"] == "ССК")["пункты"])
        self.assertIn("IESS-309", next(в for в in self.вкладки if в["имя"] == "ССК")["суть"])

    def test_слои_по_умолчанию_понимает_сервер(self):
        о = self.операции
        self.assertEqual("tcc авто", self.заполнить(о["c-tcc"]))
        self.assertEqual("tcc ccsds 1784 1/2", self.заполнить(о["c-tcc-set"]))
        self.assertEqual("tcc umts 1000 1/2 e 2008 начало 5",
                         self.заполнить(о["c-tcc-set"], {"семья": "umts", "размер": 1000, "e": "2008", "начало": " 5 "}))
        self.assertEqual("tcc ccsds 1784 1/4 asm", self.заполнить(о["c-tcc-set"], {"скорость": "1/4", "asm": True}))
        self.assertEqual("нск 3/4", self.заполнить(о["c-nsk"]))
        self.assertEqual("нск 7/8 mil", self.заполнить(о["c-nsk"], {"скорость": "7/8", "mil": True}))
        self.assertEqual("рс iess idr e1 глубина 4", self.заполнить(о["c-rs-iess"]))
        self.assertEqual("trellis 8psk 2/3", self.заполнить(о["c-trellis"]))
        # Сервер снимает каждый такой слой на потоке своего кодера.
        u = данные(3, 1784, 21)
        ряд, _ = razbor.снять_вручную(поток(lambda x: тк.ccsds_кодировать(x, "1/2"), u),
                                      self.заполнить(о["c-tcc-set"]))
        self.assertTrue(np.array_equal(u.reshape(-1), ряд))
        биты, д = тк.iess_поток(24, 219, 201, 4, сид=3)
        ряд, _ = razbor.снять_вручную(биты, self.заполнить(о["c-rs-iess"]))
        self.assertTrue(np.array_equal(д.reshape(-1), np.packbits(ряд)))
        for ид in ("c-nsk", "c-trellis", "c-tcc"):
            try:
                razbor.снять_вручную(с.случайные_биты(30_000, сид=9), self.заполнить(о[ид]))
            except ValueError as ошибка:
                self.assertNotIn("не понимаю", str(ошибка))

    def test_окно_в_меню(self):
        from test_potok_sessii import APP_JS  # noqa: PLC0415
        текст = APP_JS.read_text(encoding="utf-8")
        # Окно «Коды» стало вкладками окна «Декодер»: пункт меню и «Инструменты ▾» открывают его.
        for кусок in ("case 'коды-окно': окноДекодера(у); return null;",
                      "['Декодер… (LDPC, ТКБ, TCC, НСК, RS, Trellis, КБК, DVB-S2)', () => окноДекодера(у)]",
                      "'c-codes': 'Декодер… (LDPC, ТКБ, TCC, НСК, RS, Trellis, КБК, DVB-S2)',",
                      "вид: 'действие', сделать: 'коды-окно' }", "function окноКодов(у, вкладка)"):
            self.assertIn(кусок, текст)
