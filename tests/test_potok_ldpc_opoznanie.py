"""Опознаватель LDPC по синдрому и снятие — на реалистичных потоках с известным ответом.

Эталоны независимые (tests/ldpc_potoki.py): свой систематический кодер через H (прямая подстановка у
накопителя DVB, исключение Гаусса у прочих) с проверкой H·c = 0 по строкам H; свои перемежение бит
DVB-S2, разметка и поворот точек QPSK/8PSK для скремблера PL, последовательность Голда, рандомизатор
CCSDS, согласование скорости 5G NR (круговой буфер, k0 = 0). Данные сверяются с информационными
позициями закодированных слов.

Быстрые — малые коды и короткие записи (10–60 слов). Медленные (полный разбор длинных кодов, все
486 кодов на случайном потоке, вслепую по сдвигам) — только с REPORTGEN_LDPC_MEDLENNO=1.
"""

import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import ldpc_potoki as g
from reportgen.potok import dlinnye, dvbs2_pl, ldpc, ldpc_okno, ldpc_std, razbor
from reportgen.potok import ldpc_opoznanie as lo
from reportgen.potok.bity import в_байты

МЕДЛЕННО = os.environ.get("REPORTGEN_LDPC_MEDLENNO") == "1"
медленно = unittest.skipUnless(МЕДЛЕННО, "медленный: REPORTGEN_LDPC_MEDLENNO=1")


def данные_слов(имя, c):
    return c[:, ldpc.информационные(ldpc_std.матрица(имя))]


def лучший(биты, имена, **кв):
    кандидаты, сведения = lo.опознать(биты, имена=имена, **кв)
    к, _ = lo.выбрать(биты, кандидаты)
    return к, кандидаты, сведения


class Кодер(unittest.TestCase):
    """Свой кодер даёт кодовые слова (H·c = 0) и не совпадает с кодом проекта случайно."""

    def test_слова_кодовые(self):
        for имя in ("wifi-648-324", "nr-bg2-z16", "dvb-s2-16200-7200", "ar4ja-2560-1024", "wimax-576-480"):
            with self.subTest(имя=имя):
                c = g.кодовые(имя, 3)
                self.assertTrue(g.синдром_нулевой(ldpc_std.матрица(имя), c).all())
                испорчено = c.copy()
                испорчено[:, 5] ^= 1
                self.assertFalse(g.синдром_нулевой(ldpc_std.матрица(имя), испорчено).any())

    def test_накопитель_dvb_прямой_подстановкой(self):
        self.assertEqual("накопитель", g.кодер("dvb-s2-16200-7200").вид)
        self.assertEqual("Гаусс", g.кодер("nr-bg2-z16").вид)


class Проверки(unittest.TestCase):
    def test_ранние_по_началу_и_кодовые(self):
        """Ранние проверки — по возрастанию наибольшей позиции, все выполнены на кодовых словах (и суммы
        строк у 5G NR, где выколотые позиции — почти во всех строках)."""
        for имя in ("nr-bg2-z16", "wifi-1296-864", "ar4ja-2560-1024"):
            with self.subTest(имя=имя):
                схема = ldpc_std.схема(имя)
                ран = lo.ранние(схема, 40)
                self.assertEqual(40, len(ран))
                края = [int(с.max()) for с in ран]
                self.assertEqual(sorted(края), края)
                слова = g.кодовые(имя, 4)[:, схема.переданы]
                for с in ран:
                    self.assertFalse((слова[:, с].sum(axis=1) % 2).any())

    def test_сверка_не_повторяет_отбор(self):
        н = lo.набор("wifi-648-324")
        отбор = {с.tobytes() for с in н.ранние}
        self.assertFalse(any(с.tobytes() in отбор for с in н.сверка))
        self.assertFalse(any(с.tobytes() in отбор for с in н.ранние_сверка))
        self.assertEqual(lo.ПРОВЕРОК_ОТБОР, len(н.ранние))

    def test_случайно_как_в_ldpc(self):
        self.assertEqual(0.5, lo.случайно(0.0, 1, 1))
        self.assertEqual(1.0, lo.случайно(0.0, 0, 5))
        for нарушено, отсчётов, начал in ((0.1, 64, 648), (0.3, 768, 16200), (0.0, 32, 1), (0.45, 200, 3)):
            self.assertAlmostEqual(math.log(ldpc.случайно(нарушено, отсчётов, начал) + 1e-300),
                                   math.log(lo.случайно(нарушено, отсчётов, начал) + 1e-300), places=6)

    def test_порог_начала(self):
        k = lo.порог_начала(64)
        self.assertLessEqual(lo.случайно(k / 64, 64, 1), lo.НАЧАЛО_СЛУЧАЙНО_ДО)
        self.assertGreater(lo.случайно((k + 1) / 64, 64, 1), lo.НАЧАЛО_СЛУЧАЙНО_ДО)
        self.assertEqual(-1, lo.порог_начала(0))

    def test_по_сдвигам_как_в_лоб(self):
        """s(t) одним проходом с переворотами = прямой счёт по каждому сдвигу."""
        rng = np.random.default_rng(3)
        биты = rng.integers(0, 2, 500).astype(np.uint8)
        проверки = [np.array([0, 3, 7]), np.array([1, 2]), np.array([4, 9, 11, 20])]
        f = np.array([1, 0, 1], np.uint8)
        s, s_f = lo.нарушения_по_сдвигам(биты, проверки, 400, [f])
        for t in (0, 17, 399):
            ч = [int(биты[t + с].sum() % 2) for с in проверки]
            self.assertEqual(sum(ч), s[t])
            self.assertEqual(sum(a ^ b for a, b in zip(ч, f.tolist(), strict=True)), s_f[t])
        в = lo.нарушения_в(биты, проверки, np.array([5, 9]), f)
        self.assertEqual([int((биты[5 + проверки[i]].sum() + f[i]) % 2) for i in range(3)], в[0].tolist())

    def test_вид_потока_словами(self):
        """Описание кандидата: вставка, повтор, выколотый хвост, слова в периоде, перемежение, загруженная схема."""
        def к(**кв):
            return lo.Кандидат(**{"имя": "dvb-s2-16200-7200", "вид": "", "L": 100, "начало": 0, "шаг": 100,
                                  "нарушено": 0.0, "медиана": 0.5, "случайно": 0.0, **кв})
        self.assertEqual("слово передаётся целиком", к().вариант())
        self.assertEqual("слово передаётся целиком; между словами вставки по 1 бит", к(шаг=101).вариант())
        self.assertEqual("слово передаётся целиком; за словом повторены его первые 1 бит (согласование скорости)",
                         к(шаг=101, повтор=True).вариант())
        self.assertEqual("слово передаётся целиком; выколоты последние 1 переданных позиций (согласование скорости)",
                         к(шаг=99, слово=99).вариант())
        self.assertEqual("слово передаётся целиком; в периоде 201 бит слов — 2 (смещения 0, 100), вставка 1 бит",
                         к(шаг=201, смещения=(0, 100)).вариант())
        self.assertEqual("слово передаётся целиком; в периоде 200 бит слов — 2 (смещения 0, 100)",
                         к(шаг=200, смещения=(0, 100)).вариант())
        self.assertEqual("слово передаётся целиком; перемежение бит 8PSK (012)", к(вид="8PSK").вариант())
        self.assertEqual("слово передаётся целиком; перемежение 1367·i mod M",
                         к(имя="fldpc-k1024-j2-p16", вид="1367").вариант())
        self.assertEqual("перемежение 8PSK, выколот хвост 1, слов в периоде 2, вставка 2, инверсия, рандомизатор CCSDS, "
                         "младший бит первым", к(вид="8PSK", слово=99, шаг=200, смещения=(0, 99), инверсия=True,
                                                 рандомизатор="CCSDS", младший=True).кратко())
        self.assertEqual("повтор 5", к(шаг=105, повтор=True).кратко())
        self.assertEqual("", к().кратко())
        self.assertEqual("по схеме стандарта", к(имя="nr-bg2-z16").вариант())
        м = g.случайная_qc(6, 24, 16)
        пусто = np.array([], np.int64)
        for выколоты, укорочены, текст in ((пусто, пусто, "слово передаётся целиком"),
                                           (пусто, np.array([0]), "по схеме, сохранённой с матрицей"),
                                           (np.array([1]), пусто, "по схеме, сохранённой с матрицей")):
            self.assertEqual(текст, к(схема=ldpc.Схема(м, выколоты, укорочены)).вариант())
        self.assertAlmostEqual(0.75, к(нарушено=0.25).доля)

    def test_ядро_слова_подряд(self):
        """Сложение s(t) по словам и решение: найдено сложением (не по позициям); правила отбора и сверки."""
        import dataclasses  # noqa: PLC0415
        c = g.кодовые("wifi-648-324", 30)
        биты = g.сдвинуть(g.ошибки(c.reshape(-1), 1e-2), 50)
        н = lo.набор("wifi-648-324")

        def подряд(н_, биты_=биты, sv=None):
            выборка, сдвигов, слов = lo._выборка(биты_, н_.L, н_.край)
            if sv is None:
                sv = lo.нарушения_по_сдвигам(выборка, н_.ранние, сдвигов)[0]
            return lo._подряд(выборка, н_, sv, слов, False, "", False)

        к = подряд(н)
        self.assertEqual((50, []), (к.начало, к.начала))                  # сложением по словам
        self.assertLess(к.нарушено, 0.35)
        self.assertEqual(lo.случайно(к.нарушено, к.отсчётов, 648), к.случайно)
        self.assertIsNone(подряд(н, np.random.default_rng(2).integers(0, 2, len(биты)).astype(np.uint8)))
        # Сверка: равномерных нет — следующими ранними; трёх проверок хватает, двух — нет.
        self.assertIsNotNone(подряд(dataclasses.replace(н, ранние_сверка=[])))
        self.assertEqual(50, подряд(dataclasses.replace(н, сверка=[], ранние_сверка=н.ранние_сверка[:3])).начало)
        чисто = g.сдвинуть(c.reshape(-1), 50)
        self.assertEqual(50, подряд(dataclasses.replace(н, сверка=н.сверка[:3], ранние_сверка=[]), чисто).начало)
        self.assertIsNone(подряд(dataclasses.replace(н, сверка=н.сверка[:2], ранние_сверка=[]), чисто))
        # Отбор: медиана по началам ровно 0,4 — ещё случайные биты; лучшее начало — не больше половины медианы.
        выборка, сдвигов, слов = lo._выборка(биты, 648, н.край)
        н10 = dataclasses.replace(н, ранние=н.ранние[:10])
        sv = np.full(сдвигов, 4, dtype=np.int16)
        sv[50::648] = 0
        self.assertIsNotNone(подряд(н10, sv=sv))
        sv[sv == 4] = 3
        self.assertIsNone(подряд(н10, sv=sv))                             # медиана 0,3 < 0,4
        шумно = g.сдвинуть(g.ошибки(c.reshape(-1), 0.05), 50)
        sv = np.full(сдвигов, 4, dtype=np.int16)
        sv[50::648] = 0
        к = подряд(н10, шумно, sv)
        self.assertIsNone(к)                                              # нарушено ~0,26 > 0,5 · 0,4
        sv[sv == 4] = 6
        self.assertEqual(50, подряд(н10, шумно, sv).начало)               # медиана 0,6: 0,26 ≤ 0,3

    def test_период_по_началам(self):
        self.assertEqual(4000, lo.период([0, 8000, 12000, 20000, 28000], 500))   # слова пропущены
        self.assertEqual(2080, lo.период([32, 2112, 4192, 7, 8352], 1000))       # одно ложное
        self.assertIsNone(lo.период([5], 10))


class Опознание(unittest.TestCase):
    """Встроенные коды: граница, ошибки линии, инверсия, порядок бит, вставки, иная длина слова."""

    ИМЕНА = ["wifi-648-324", "wifi-1296-864", "wimax-576-288", "nr-bg2-z16", "nr-bg2-z32", "ar4ja-2560-1024",
             "dvb-s2-16200-7200"]

    def снять(self, биты, к, имя, c, начало_слова=0):
        данные, подробно, сошлось = lo.снять(биты, к)
        ждём = данные_слов(имя, c)
        k = ждём.shape[1]
        слов = len(данные) // k
        np.testing.assert_array_equal(ждём[начало_слова:начало_слова + слов].reshape(-1), данные[:слов * k])
        return сошлось

    def test_слова_подряд_со_сдвигом_и_ошибками(self):
        for ber in (0.0, 1e-3, 1e-2):
            with self.subTest(ber=ber):
                c = g.кодовые("wifi-648-324", 40)
                биты = g.сдвинуть(g.ошибки(c.reshape(-1), ber), 123)
                к, _, _ = лучший(биты, self.ИМЕНА)
                self.assertEqual(("wifi-648-324", 123, 648), (к.имя, к.начало, к.шаг))
                self.assertGreater(к.доля, 0.8)
                self.assertEqual(ber == 0, к.доля == 1.0)
                self.assertEqual("слово передаётся целиком", к.вариант())
                self.assertGreaterEqual(self.снять(биты, к, "wifi-648-324", c), 0.9)

    def test_инверсия_и_младший_бит(self):
        c = g.кодовые("wifi-1296-864", 30)
        биты = g.сдвинуть(c.reshape(-1), 40)
        инв = 1 - биты
        к, _, _ = лучший(инв, self.ИМЕНА)
        self.assertTrue(к.инверсия)
        self.assertIn("поток инверсный", к.вариант())
        self.снять(инв, к, "wifi-1296-864", c)
        младший = биты[:len(биты) // 8 * 8].reshape(-1, 8)[:, ::-1].reshape(-1)
        к, _, сведения = лучший(младший, self.ИМЕНА)
        self.assertTrue(к.младший)
        self.assertEqual(["старший", "младший"], сведения["порядки"])
        self.снять(младший, к, "wifi-1296-864", c)

    def test_asm_и_рандомизатор_ccsds(self):
        for ранд in (True, False):
            with self.subTest(рандомизатор=ранд):
                биты, c = g.ccsds("ar4ja-2560-1024", 30, рандомизатор=ранд)
                биты = g.сдвинуть(g.ошибки(биты, 1e-3), 77)
                к, _, _ = лучший(биты, self.ИМЕНА)
                self.assertEqual(("ar4ja-2560-1024", 2080, 0), (к.имя, к.шаг, (к.начало - 77 - 32) % 2080))
                self.assertEqual("CCSDS" if ранд else "", к.рандомизатор)
                self.assertIn("вставки по 32 бит", к.вариант())
                self.снять(биты, к, "ar4ja-2560-1024", c)

    def test_nr_согласование_скорости(self):
        """E < N — выколот хвост; E > N — повтор начала буфера (мягко складывается)."""
        for E, слово in ((1000, 1000), (2200, 1600)):
            with self.subTest(E=E):
                биты, c = g.nr(2, 32, 40, E=E)
                биты = g.ошибки(биты, 2e-3)
                к, _, _ = лучший(биты, self.ИМЕНА)
                self.assertEqual(("nr-bg2-z32", E, слово), (к.имя, к.шаг, к.длина_слова))
                if E > 1600:
                    self.assertTrue(к.повтор)
                    self.assertIn("повторены его первые 600 бит", к.вариант())
                else:
                    self.assertIn("выколоты последние 600 переданных позиций", к.вариант())
                self.assertEqual(1.0, self.снять(биты, к, "nr-bg2-z32", c))

    def test_несколько_слов_в_кадре(self):
        """ASM и четыре слова NR с выколотым хвостом в кадре: период — кадр, смещения слов в нём."""
        _, c = g.nr(2, 16, 40, E=700)
        схема = ldpc_std.схема("nr-bg2-z16")
        переданы = c[:, схема.переданы][:, :700]
        биты = g.в_кадрах(переданы, g.ASM, 4)
        к, _, _ = лучший(биты, self.ИМЕНА)
        self.assertEqual((2832, (0, 700, 1400, 2100), 700), (к.шаг, к.смещения, к.длина_слова))
        self.снять(биты, к, "nr-bg2-z16", c)
        # Слой стола повторяет то же.
        н = lo.находка(биты, к)
        слои = razbor.слои_находки(н)
        self.assertEqual(["ldpc nr-bg2-z16 выколоты 0-31, 732-831 начало 32 шаг 2832 смещения 0,700,1400,2100"], слои)
        ряд, _ = razbor.снять_вручную(биты, слои[0])
        np.testing.assert_array_equal(н.дальше[:len(ряд)], ряд)

    def test_вставки_при_ошибках_период_не_кратный(self):
        """При 2·10⁻² начала по позициям находятся не у всех слов, и период по ним кратен настоящему —
        берётся доля, у промежуточных начал проверки выполнены так же: снимаются все слова."""
        c = g.кодовые("dvb-s2-16200-7200", 10, сид=5)
        схема = ldpc_std.схема("dvb-s2-16200-7200")
        биты = g.сдвинуть(g.ошибки(g.в_кадрах(c[:, схема.переданы], g.ASM, 1), 0.02, сид=11), 333)
        к, _, _ = лучший(биты, ["dvb-s2-16200-7200"], младший=False)
        self.assertEqual((365, 16232), (к.начало, к.шаг))
        self.assertEqual(1.0, self.снять(биты, к, "dvb-s2-16200-7200", c))

    def test_dvb_s2_plheader_без_пилотов(self):
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-7200", 6, бчх=True, pl=True)
        к, _, _ = лучший(g.сдвинуть(биты, 10), ["dvb-s2-16200-7200", "dvb-s2-16200-9720"])
        self.assertEqual((16290, 100), (к.шаг, к.начало))

    def test_чужой_поток_не_опознаётся(self):
        rng = np.random.default_rng(9)
        for биты in (rng.integers(0, 2, 200_000).astype(np.uint8), np.zeros(100_000, np.uint8),
                     np.tile(rng.integers(0, 2, 648).astype(np.uint8), 100)):
            кандидаты, _ = lo.опознать(биты, имена=self.ИМЕНА)
            self.assertEqual([], кандидаты)

    def test_код_другой_матрицы_не_наш(self):
        чужая = g.случайная_qc(6, 24, 27)          # n = 648, как у wifi-648, но другая H
        c = g.кодовые(чужая, 40)
        кандидаты, _ = lo.опознать(c.reshape(-1), имена=self.ИМЕНА)
        self.assertEqual([], кандидаты)

    def test_мало_слов(self):
        """Две записи по слову не хватает; три слова — уже да (длина записи 10 слов — с запасом)."""
        c = g.кодовые("wifi-648-324", 3)
        к, _, _ = лучший(g.сдвинуть(c.reshape(-1), 5), self.ИМЕНА)
        self.assertEqual(5, к.начало)
        self.assertEqual([], lo.опознать(c[:1].reshape(-1), имена=self.ИМЕНА)[0])

    def test_срок(self):
        c = g.кодовые("wifi-648-324", 20)
        with mock.patch.object(lo.time, "monotonic", side_effect=[0.0] + [1e9] * 100000):
            кандидаты, сведения = lo.опознать(c.reshape(-1), имена=self.ИМЕНА, срок=1.0)
        self.assertTrue(сведения["срок_вышел"])

    def test_загруженная_матрица(self):
        """Своя (загруженная) матрица — набором своей схемы; встроенный с тем же именем не пробуется."""
        м = g.случайная_qc(6, 24, 16)
        c = g.кодовые(м, 40)
        схема = ldpc.Схема(м, np.array([], np.int64), np.array([], np.int64))
        н = lo.набор_своей("своя", схема)
        self.assertIs(н, lo.набор_своей("своя", схема))
        к, _, _ = лучший(g.сдвинуть(c.reshape(-1), 9), ["wifi-648-324"], наборы=[н])
        self.assertEqual(("своя", 9), (к.имя, к.начало))
        self.assertIn("слово передаётся целиком", к.вариант())
        данные, _, сошлось = lo.снять(g.сдвинуть(c.reshape(-1), 9), к)
        self.assertEqual(1.0, сошлось)


class КэшНаборов(unittest.TestCase):
    def test_на_диске(self):
        with tempfile.TemporaryDirectory() as папка, mock.patch.object(lo.tempfile, "gettempdir", return_value=папка):
            было = dict(lo._НАБОРЫ), dict(lo._С_ДИСКА)
            try:
                lo._НАБОРЫ.clear()
                lo._С_ДИСКА.update({"прочитано": True, "изменено": False})
                н = lo.набор("wifi-648-324")
                lo.сохранить_кэш()
                self.assertTrue(lo._путь_кэша().exists())
                lo._НАБОРЫ.clear()
                lo._С_ДИСКА.update({"прочитано": False, "изменено": False})
                with mock.patch.object(lo, "набор_схемы", side_effect=AssertionError("строится заново")):
                    снова = lo.набор("wifi-648-324")
                self.assertEqual(н.L, снова.L)
                self.assertTrue(all(np.array_equal(a, b) for a, b in zip(н.ранние, снова.ранние, strict=True)))
                for поле in ("сверка", "ранние_сверка"):
                    self.assertTrue(all(np.array_equal(a, b) for a, b in
                                        zip(getattr(н, поле), getattr(снова, поле), strict=True)), поле)
                self.assertEqual((н.край, н.рандомизатор), (снова.край, снова.рандомизатор))
                # Файл в общей временной папке читается без pickle: подложенный pickle не выполняется,
                # набор строится заново.
                import pickle  # noqa: PLC0415
                class Ловушка:
                    def __reduce__(self):
                        return (exec, ("raise SystemExit('pickle выполнен')",))
                Path(lo._путь_кэша()).write_bytes(pickle.dumps({"x": Ловушка()}))
                lo._НАБОРЫ.clear()
                lo._С_ДИСКА.update({"прочитано": False, "изменено": False})
                self.assertEqual(н.L, lo.набор("wifi-648-324").L)
                Path(lo._путь_кэша()).write_bytes(b"\x80\x04 broken")
                lo._НАБОРЫ.clear()
                lo._С_ДИСКА.update({"прочитано": False, "изменено": False})
                self.assertEqual(н.L, lo.набор("wifi-648-324").L)      # битый файл — строится заново
                with mock.patch.dict(os.environ, {lo.ПЕРЕМЕННАЯ_НАБОРОВ: "нет"}):
                    self.assertIsNone(lo._путь_кэша())
                    lo._С_ДИСКА.update({"прочитано": False, "изменено": True})
                    lo.сохранить_кэш()                                    # без диска — молча ничего
                    self.assertTrue(lo._С_ДИСКА["изменено"])
                with mock.patch.dict(os.environ, {lo.ПЕРЕМЕННАЯ_НАБОРОВ: str(Path(папка) / "свой.npz")}):
                    self.assertEqual(Path(папка) / "свой.npz", lo._путь_кэша())
            finally:
                lo._НАБОРЫ.clear()
                lo._НАБОРЫ.update(было[0])
                lo._С_ДИСКА.update(было[1])


class PLFRAME(unittest.TestCase):
    """PLHEADER вида «биты символа», пилоты, скремблер PL: FECFRAME и код по MODCOD."""

    def test_код_по_modcod(self):
        self.assertEqual(("dvb-s2-16200-7200", ""), dvbs2_pl.код_ldpc(4, True))
        self.assertEqual(("dvb-s2-64800-43200", "8PSK"), dvbs2_pl.код_ldpc(13, False))
        self.assertEqual(("dvb-s2-64800-58320", "32APSK"), dvbs2_pl.код_ldpc(28, False))
        self.assertIsNone(dvbs2_pl.код_ldpc(99, False))
        for modcod in dvbs2_pl.MODCOD:
            имя, вид = dvbs2_pl.код_ldpc(modcod, False)
            self.assertTrue(ldpc_std.есть(имя), имя)
            if вид:
                self.assertIn(вид, [в for в, _ in ldpc_std.перемежения(имя)])

    def test_голд_как_в_генераторе(self):
        np.testing.assert_array_equal(g._голд(5000), dvbs2_pl.голд(5000))

    def test_повороты_qpsk_8psk_как_разметка_генератора(self):
        for бит, повернуть in ((2, g.повернуть_qpsk), (3, g.повернуть_8psk)):
            символы = np.arange(1 << бит)
            биты = ((np.repeat(символы, 4)[:, None] >> np.arange(бит - 1, -1, -1)) & 1).reshape(-1).astype(np.uint8)
            повёрнуто = повернуть(биты, np.tile(np.arange(4), 1 << бит)).reshape(-1, бит)
            метки = повёрнуто @ (1 << np.arange(бит - 1, -1, -1))
            np.testing.assert_array_equal(dvbs2_pl.повороты(бит)[np.tile(np.arange(4), 1 << бит), np.repeat(символы, 4)], метки)
        self.assertEqual((4, 16), dvbs2_pl.повороты(4).shape)
        for бит in (2, 3, 4, 5):                       # перестановки, поворот на 4·π/2 — тождество
            т = dvbs2_pl.повороты(бит)
            self.assertTrue(all(sorted(р.tolist()) == list(range(1 << бит)) for р in т))
            np.testing.assert_array_equal(т[1][т[1][т[1][т[1]]]], np.arange(1 << бит))

    def test_раскладка_пилоты_скремблер(self):
        for вид, имя, пилоты, скр in (("", "dvb-s2-16200-7200", True, True), ("8PSK", "dvb-s2-16200-10800", True, True),
                                       ("", "dvb-s2-16200-7200", False, False)):
            with self.subTest(вид=вид, пилоты=пилоты):
                биты, c, _ = g.поток_dvbs2(имя, 5, бчх=True, pl=True, пилоты=пилоты, скремблер_pl=скр, вид=вид)
                н = dvbs2_pl.найти(g.сдвинуть(биты, 33))
                с = н.свойства
                self.assertEqual((имя, вид), (с["ldpc"], с["перемежение"]))
                кадры = с["fec_кадры"]
                if скр:
                    кадры = dvbs2_pl.снять_скремблер(кадры, с["номер_символа"], с["раскладка"]["бит_на_символ"])
                столбцы = ldpc_std.столбцы_перемежения(имя, вид) if вид else ""
                ждём = np.array([g.перемежить(сл, столбцы) for сл in c]) if вид else c
                np.testing.assert_array_equal(ждём[:len(кадры)], кадры)

    def test_раскладка_не_по_modcod(self):
        self.assertIsNone(dvbs2_pl.раскладка(4, True, False, 16291))
        self.assertEqual(36, dvbs2_pl.раскладка(4, True, True, 90 + 16200 + 5 * 36)["пилот"])
        self.assertEqual(72, dvbs2_pl.раскладка(4, True, True, 90 + 16200 + 5 * 72)["пилот"])
        self.assertIsNone(dvbs2_pl.раскладка(0, True, True, 100))


class ВАвтомате(unittest.TestCase):
    def разобрать(self, биты, профиль="быстро", имя="поток.bin"):
        return razbor.разобрать(данные=в_байты(биты), имя=имя, профиль=профиль)

    def test_plframe_пилоты_скремблер_pl(self):
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-7200", 8, бчх=True, pl=True, пилоты=True, скремблер_pl=True)
        р = self.разобрать(g.ошибки(g.сдвинуть(биты, 50), 1e-3))
        уровни = [н.уровень for н in р.находки]
        self.assertEqual(["цикл", "код", "канальный"], уровни[:3], р.отчёт(20000))
        self.assertIn("скремблер PL снят", р.находки[1].что)
        np.testing.assert_array_equal(данные_слов("dvb-s2-16200-7200", c).reshape(-1)[:len(р.находки[1].дальше)],
                                      р.находки[1].дальше)

    def test_цикл_bbheader_не_мешает(self):
        """Заголовок BBFRAME под скремблером BB — одинаковый в начале каждого слова: цикл с периодом слова
        не принимается, снимается код, дальше — BBFRAME."""
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-7200", 20, бчх=True)
        р = self.разобрать(g.сдвинуть(биты, 700))
        self.assertEqual(["код", "канальный"], [н.уровень for н in р.находки][:2], р.отчёт(20000))
        self.assertIn("по матрице «dvb-s2-16200-7200»", р.находки[0].что)
        self.assertIn("BBFRAME", р.находки[1].что)

    def test_слои_как_в_автомате(self):
        c = g.кодовые("wifi-648-324", 30)
        биты = g.сдвинуть(1 - c.reshape(-1), 9)
        итог = razbor.шаг_как_автомат(биты, "ldpc", срок=60)
        self.assertEqual(["инверсия", "ldpc wifi-648-324 начало 9"], итог["слои"])
        self.assertEqual("wifi-648-324", итог["сведения"]["поля"]["код"])
        self.assertTrue(итог["сведения"]["поля"]["вход_инверсия"])
        пусто = razbor.шаг_как_автомат(np.random.default_rng(1).integers(0, 2, 20000).astype(np.uint8), "ldpc",
                                       фм=[3], срок=60)
        self.assertEqual([], пусто["найдено"])
        self.assertIn("Моддекодер", пусто["подсказка"])

    def test_подсказка_моддекодер_после_8psk(self):
        """Метки 8PSK в чужой разметке (линейная перестановка меток): LDPC не опознаётся, и подсказка ведёт к
        «Моддекодер» → «Дек. всех»; со своей разметкой — опознаётся."""
        from reportgen.potok import podskazki, rastr  # noqa: PLC0415
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-7200", 6, бчх=False, вид="")
        тройки = биты[:len(биты) // 3 * 3].reshape(-1, 3)
        чужая = np.array([0, 1, 3, 2, 7, 6, 4, 5])[тройки @ [4, 2, 1]]
        чужие = ((чужая[:, None] >> np.array([2, 1, 0])) & 1).reshape(-1).astype(np.uint8)
        self.assertIsNone(rastr.инструмент(чужие, "ldpc"))
        приметы = podskazki.приметы(чужие)
        подсказки = podskazki.подсказки({"имя": "zapis_8PSK.bin"}, 0, приметы)
        моддек = [п for п in подсказки if "ФМ-8" in п["что"]]
        self.assertEqual(1, len(моддек))
        self.assertIn("«Дек. всех»", моддек[0]["почему"])
        self.assertEqual("ldpc", моддек[0]["действия"][0]["имя"])
        self.assertFalse([п for п in podskazki.подсказки({"имя": "zapis.bin"}, 0, приметы) if "ФМ-8" in п["что"]])
        найдено = rastr.инструмент(биты, "ldpc")
        self.assertIn("по матрице «dvb-s2-16200-7200»", найдено.что)

    def test_все_слова_ряда(self):
        """Находка снимает все слова ряда, не первые 4000: выход этапа покрывает всю выборку."""
        c = g.кодовые("wifi-648-324", 4500)
        биты = g.сдвинуть(g.ошибки(c.reshape(-1), 1e-3), 123)
        к, _, _ = лучший(биты, ["wifi-648-324"])
        н = lo.находка(биты, к)
        np.testing.assert_array_equal(данные_слов("wifi-648-324", c).reshape(-1), н.дальше)

    def test_ко_всему_файлу_по_матрице(self):
        """LDPC по матрице, найденный по выборке (начало файла), пересчитывается по всему файлу слоем «ldpc …
        начало N»: выход этапа по выборке — все слова выборки и начало пересчитанного."""
        from unittest import mock  # noqa: PLC0415

        from test_potok_bolshie import сверить_этапы  # noqa: PLC0415
        c = g.кодовые("wifi-648-324", 1500)
        биты = g.сдвинуть(g.ошибки(c.reshape(-1), 1e-3), 137)
        выборка = len(биты) // 2 // 8 * 8
        with mock.patch.object(razbor, "ВЫБОРКА_БИТ", выборка):
            р = self.разобрать(биты)
        self.assertEqual("код", р.находки[0].уровень, р.отчёт(20000))
        self.assertEqual((выборка - 137) // 648 * 324, len(р.находки[0].дальше))
        self.assertEqual([["ldpc wifi-648-324 начало 137"]], сверить_этапы(self, биты, р.находки[:1], 1))

    def test_plframe_ко_всему_файлу(self):
        """PLFRAME + LDPC по MODCOD, найденные по выборке: этап заголовков выдаёт FECFRAME слоем «plframe
        скремблер», код — «ldpc … начало 0» от него (младшим битом байта первым — «реверс 8» первым шагом)."""
        from unittest import mock  # noqa: PLC0415

        from test_potok_bolshie import сверить_этапы  # noqa: PLC0415
        биты, _, _ = g.поток_dvbs2("dvb-s2-16200-7200", 40, бчх=True, pl=True, пилоты=True, скремблер_pl=True)
        биты = g.ошибки(g.сдвинуть(биты, 50), 1e-3)[:len(биты) // 8 * 8]
        for младший in (False, True):
            with self.subTest(младший=младший):
                ряд = биты.reshape(-1, 8)[:, ::-1].reshape(-1) if младший else биты
                with mock.patch.object(razbor, "ВЫБОРКА_БИТ", len(ряд) // 2 // 8 * 8):
                    р = self.разобрать(ряд)
                self.assertEqual(["цикл", "код"], [н.уровень for н in р.находки[:2]], р.отчёт(20000))
                слои = сверить_этапы(self, ряд, р.находки[:2], 2)
                self.assertEqual([["реверс 8"] * младший + ["plframe скремблер"], ["ldpc dvb-s2-16200-7200 начало 0"]],
                                 слои)

    def test_слой_со_вставками_вручную(self):
        биты, c = g.ccsds("ar4ja-2560-1024", 12)
        ряд, запись = razbor.снять_вручную(биты, "ldpc ar4ja-2560-1024 рандомизатор ccsds начало 32 шаг 2080")
        np.testing.assert_array_equal(данные_слов("ar4ja-2560-1024", c).reshape(-1), ряд)
        with self.assertRaises(ValueError):
            razbor.снять_вручную(биты, "ldpc ar4ja-2560-1024 шаг 2080")             # без начала
        with self.assertRaises(ValueError):
            razbor.снять_вручную(биты, "ldpc ar4ja-2560-1024 начало 0 шаг 100")      # короче слова


class ОкноLDPC(unittest.TestCase):
    def test_найти_по_встроенным_и_просмотр(self):
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-7200", 8, бчх=True, pl=True)
        биты = g.сдвинуть(1 - биты, 77)
        итог = ldpc_okno.найти_встроенные(биты, {})
        к = итог["кандидаты"][0]
        self.assertEqual(("dvb-s2-16200-7200", 167, 16290), (к["код"], к["начало"], к["шаг"]))
        self.assertTrue(к["поля"]["вход_инверсия"])
        self.assertGreater(к["доля"], 0.99)
        просмотр = ldpc_okno.просмотр(биты, к["поля"])
        self.assertEqual(100.0, просмотр["сошлось"])
        self.assertEqual(["инверсия", "ldpc dvb-s2-16200-7200 начало 167 шаг 16290"], просмотр["слои"])
        np.testing.assert_array_equal(данные_слов("dvb-s2-16200-7200", c).reshape(-1)[:len(просмотр["биты"])],
                                      просмотр["биты"])

    def test_plframe_с_пилотами_и_скремблером(self):
        """PLHEADER «биты символа», пилоты, скремблер PL не снят: кандидат по MODCOD, слои «plframe скремблер» и
        «ldpc …» — тот же результат и «Просмотром», и шагами стола."""
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-10800", 5, бчх=True, pl=True, пилоты=True, скремблер_pl=True, вид="8PSK")
        биты = g.сдвинуть(биты, 45)
        к = ldpc_okno.найти_встроенные(биты, {})["кандидаты"][0]
        self.assertEqual(["plframe скремблер", "ldpc dvb-s2-16200-10800 перемежение 8PSK начало 0"], к["слои"])
        self.assertEqual("скремблер", к["поля"]["plframe"])
        ждём = данные_слов("dvb-s2-16200-10800", c).reshape(-1)
        просмотр = ldpc_okno.просмотр(биты, к["поля"])
        np.testing.assert_array_equal(ждём[:len(просмотр["биты"])], просмотр["биты"])
        ряд = биты
        for слой in к["слои"]:
            ряд, _ = razbor.снять_вручную(ряд, слой)
        np.testing.assert_array_equal(ждём[:len(ряд)], ряд)
        with self.assertRaisesRegex(ValueError, "plframe"):
            razbor.снять_вручную(c.reshape(-1), "plframe")

    def test_автомат_окна_по_типу(self):
        """«Автомат» окна ищет среди кодов выбранного типа — опознавателем (вставки, инверсия), поля окна —
        в ответе; чужой тип — не находит."""
        from reportgen.potok import ldpc_katalog  # noqa: PLC0415
        биты, c, _ = g.поток_dvbs2("dvb-s2-16200-7200", 6, бчх=True, pl=True)
        биты = g.сдвинуть(1 - биты, 20)
        типы = ldpc_katalog.с_файлами([])
        итог = ldpc_okno.автомат(биты, {"тип": "DVB-S2 short"}, типы, бюджет=60)["найдено"]
        self.assertEqual(("dvb-s2-16200-7200", 110), (итог["код"], итог["начало"]))
        self.assertEqual({"шаг": 16290, "вход_инверсия": True}, {к: итог["поля"][к] for к in ("шаг", "вход_инверсия")})
        просмотр = ldpc_okno.просмотр(биты, итог["поля"])
        self.assertEqual(100.0, просмотр["сошлось"])
        self.assertIsNone(ldpc_okno.автомат(биты, {"тип": "Wi-Fi 802.11n/ac"}, типы, бюджет=30)["найдено"])

    def test_поля_окна(self):
        self.assertEqual("ldpc wifi-648-324 рандомизатор ccsds начало 5 шаг 700 смещения 0,10",
                         ldpc_okno.слой({"код": "wifi-648-324", "начало": 5, "шаг": 700, "смещения": "0, 10",
                                         "рандомизатор": True}))
        for п, слово in (({"шаг": 700}, "началом"), ({"начало": "x"}, "целое"), ({"начало": 1, "смещения": "0,1"}, "шагом"),
                         ({"начало": 1, "шаг": 9, "смещения": "а"}, "через запятую")):
            with self.subTest(п=п), self.assertRaisesRegex(ValueError, слово):
                ldpc_okno.слой({"код": "wifi-648-324", **п})
        self.assertEqual(["реверс 8", "инверсия", "plframe скремблер"],
                         ldpc_okno.предобработка({"вход_реверс": True, "вход_инверсия": True, "plframe": "скремблер"}))
        self.assertEqual(["plframe"], ldpc_okno.предобработка({"plframe": "да"}))

    def test_ничего(self):
        итог = ldpc_okno.найти_встроенные(np.zeros(5000, np.uint8), {})
        self.assertEqual([], итог["кандидаты"])
        self.assertIn("Ни один код не совпал", итог["почему"])


class ВслепуюСдвигами(unittest.TestCase):
    def test_циркулянты_и_сдвиги(self):
        self.assertEqual([16, 24, 32, 48, 64, 96, 128, 192, 256, 384], dlinnye.циркулянты(768, 60))
        W = np.array([[1, 0, 0, 0, 0, 1, 0, 0]], np.uint8)
        np.testing.assert_array_equal([[1, 0, 0, 0, 0, 1, 0, 0], [0, 1, 0, 0, 0, 0, 1, 0]],
                                      dlinnye.со_сдвигами(W, 4, 2))

    @медленно
    def test_qc_по_60_словам(self):
        м = g.случайная_qc(6, 24, 32)
        c = g.кодовые(м, 60)
        н, H = dlinnye.найти_в_кадрах(c, бюджет=300)
        self.assertEqual((768, 576, 32), (н.свойства["n"], н.свойства["k"], н.свойства["сдвигами_z"]))
        np.testing.assert_array_equal(c[:, ldpc.информационные(м)].reshape(-1), н.дальше)


@медленно
class Медленные(unittest.TestCase):
    """Длинные коды и все 486 кодов — полный перебор."""

    def test_все_коды_на_случайном(self):
        кандидаты, сведения = lo.опознать(np.random.default_rng(4).integers(0, 2, 1_500_000).astype(np.uint8), срок=600)
        self.assertEqual([], кандидаты)
        self.assertFalse(сведения["срок_вышел"])
        self.assertEqual(["старший", "младший"], сведения["порядки"])

    def test_dvb_s2_нормальный_8psk_ber(self):
        for имя, вид, ber in (("dvb-s2-64800-43200", "8PSK", 1e-3), ("dvb-s2-64800-32400", "", 1e-2),
                              ("dvb-s2-16200-11880", "16APSK", 3e-3)):
            with self.subTest(имя=имя, ber=ber):
                биты, c, _ = g.поток_dvbs2(имя, 6, бчх=True, вид=вид)
                биты = g.сдвинуть(g.ошибки(биты, ber), 1001)
                к, _, _ = лучший(биты, None)
                self.assertEqual((имя, вид, 1001 % c.shape[1]), (к.имя, к.вид, к.начало))
                данные, _, сошлось = lo.снять(биты, к)
                self.assertEqual(1.0, сошлось)

    def test_разбор_по_профилям(self):
        биты, c, _ = g.поток_dvbs2("dvb-s2-64800-48600", 6, бчх=True)
        for профиль in ("быстро", "обычно"):
            with self.subTest(профиль=профиль):
                р = razbor.разобрать(данные=в_байты(g.ошибки(g.сдвинуть(биты, 3), 1e-3)), профиль=профиль)
                self.assertEqual(["код", "канальный"], [н.уровень for н in р.находки][:2], р.отчёт(20000))


if __name__ == "__main__":
    unittest.main()
