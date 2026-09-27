"""Североамериканская PDH: T1 (SF, ESF), DS2 (M12), DS3 (M23, C-bit parity).

Потоки собираются здесь же, простым мультиплексором, написанным заново по
описанию стандарта — разметку у анализатора он не берёт, CRC-6 считает
делением «в столбик», а не матрицей вкладов, как анализатор. Совпадение
притоков бит в бит поэтому проверяет разметку, а не повторяет её.
"""

import re
import time
import unittest
from functools import cache

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import pdh, pdh_na
from reportgen.potok.bity import в_биты

# -- независимый мультиплексор ---------------------------------------------------

FT = [1, 0, 1, 0, 1, 0]            # F-биты нечётных циклов SF (1, 3, …, 11)
FS = [0, 0, 1, 1, 1, 0]            # F-биты чётных циклов SF (2, 4, …, 12)
FPS = [0, 0, 1, 0, 1, 1]           # ESF: F-биты циклов 4, 8, …, 24


def crc6_столбиком(биты):
    """Остаток M(x)·x⁶ mod x⁶+x+1 делением в столбик; первый бит — старший, C1 — старший."""
    рег = 0
    for б in list(биты) + [0] * 6:
        рег = (рег << 1) | int(б)
        if рег & 0x40:
            рег ^= 0x43
    return [(рег >> (5 - i)) & 1 for i in range(6)]


def t1(сверхциклов, вид="SF", сид=1, crc="G.704", dl=None):
    """Поток T1 и его нагрузка (циклы × 192 бита): каналы 1…22 случайны, 23 — 0x7F, 24 — 0xFF.

    ``crc``: «G.704» — F-биты при расчёте равны 1; «как есть» — F-биты как
    переданы; «испорчена» — C1 инвертирован.
    """
    rng = np.random.default_rng(сид)
    длина = 12 if вид == "SF" else 24
    циклов = сверхциклов * длина
    нагрузка = rng.integers(0, 2, (циклов, 192)).astype(np.uint8)
    нагрузка[:, 176:184] = [0, 1, 1, 1, 1, 1, 1, 1]
    нагрузка[:, 184:192] = 1
    f = np.zeros(циклов, np.uint8)
    if вид == "SF":
        for цикл in range(циклов):
            номер = цикл % 12 + 1                 # номер цикла в сверхцикле, с 1
            f[цикл] = FT[номер // 2] if номер % 2 else FS[номер // 2 - 1]
        return np.hstack([f[:, None], нагрузка]).reshape(-1), нагрузка
    предыдущий = None
    for сц in range(сверхциклов):
        for k in range(24):
            номер = k + 1
            цикл = сц * 24 + k
            if номер % 4 == 0:
                f[цикл] = FPS[номер // 4 - 1]
            elif номер % 2 == 1:                      # DL: заданные биты по кругу или случайные
                f[цикл] = dl[(цикл // 2) % len(dl)] if dl is not None else rng.integers(0, 2)
        if предыдущий is not None:
            блок = предыдущий.copy()
            if crc != "как есть":
                блок[:, 0] = 1
            контроль = crc6_столбиком(блок.reshape(-1))
            if crc == "испорчена":
                контроль[0] ^= 1
            for i, номер in enumerate(range(2, 24, 4)):
                f[сц * 24 + номер - 1] = контроль[i]
        предыдущий = np.hstack([f[сц * 24:сц * 24 + 24, None], нагрузка[сц * 24:сц * 24 + 24]])
    return np.hstack([f[:, None], нагрузка]).reshape(-1), нагрузка


def ds2(притоки, циклов, доли, инверсные=(2, 4), сид=7, испортить_c=None):
    """M12: M-цикл = 4 подцикла × 6 блоков × (служебный бит + 48 бит притоков по очереди).

    Служебные биты блоков подцикла п: M, C, F0=0, C, C, F1=1; M-биты 0, 1, 1, X=1.
    C-биты подцикла п — стаффинг притока п; пустышка (случайный бит) — первый
    слот притока п в 6-м блоке подцикла п. ``испортить_c`` = (приток, повтор, шаг):
    инвертировать один C-бит притока в каждом шаг-м цикле.
    """
    rng = np.random.default_rng(сид)
    источники = [1 - п if номер in инверсные else п
                 for номер, п in enumerate(притоки, start=1)]
    место, накоплено, выход = [0] * 4, [0.0] * 4, []
    for цикл in range(циклов):
        стафф = []
        for t in range(4):
            накоплено[t] += доли[t]
            стафф.append(накоплено[t] >= 1)
            if стафф[t]:
                накоплено[t] -= 1
        for п in range(4):
            повтор_c = 0
            for б in range(6):
                if б == 0:
                    служебный = [0, 1, 1, 1][п]
                elif б in (1, 3, 4):
                    служебный = int(стафф[п])
                    if испортить_c and испортить_c[0] == п and испортить_c[1] == повтор_c \
                            and цикл % испортить_c[2] == 0:
                        служебный ^= 1
                    повтор_c += 1
                else:
                    служебный = 0 if б == 2 else 1
                блок = np.empty(48, np.uint8)
                for t in range(4):
                    if б == 5 and t == п and стафф[п]:
                        кусок = np.concatenate([[rng.integers(0, 2)],
                                                источники[t][место[t]:место[t] + 11]])
                        место[t] += 11
                    else:
                        кусок = источники[t][место[t]:место[t] + 12]
                        место[t] += 12
                    assert len(кусок) == 12, "не хватило бит притока"
                    блок[t::4] = кусок
                выход.append(np.array([служебный], np.uint8))
                выход.append(блок)
    return np.concatenate(выход)


def ds3(притоки, циклов, доли, режим="M23", сид=9, испортить_p=False):
    """DS3: M-цикл = 7 подциклов × 8 блоков × (служебный бит + 84 бита притоков по очереди).

    Служебные биты блоков подцикла: X/P/M, F1=1, C, F2=0, C, F3=0, C, F4=1; первые
    биты подциклов — X1=X2=1, P1=P2 (чётность данных предыдущего цикла), M 010.
    M23: C-биты подцикла п — стаффинг притока п, пустышка — первый слот притока п
    в 8-м блоке подцикла п. C-bit parity: стаффинг всегда; C-биты — AIC=1, Na=1,
    FEAC; подцикл 3 — CP = P; остальные — служебные каналы.
    """
    rng = np.random.default_rng(сид)
    место, накоплено, чётность, выход = [0] * 7, [0.0] * 7, 0, []
    for _ in range(циклов):
        стафф = []
        for t in range(7):
            накоплено[t] += доли[t] if режим == "M23" else 1.0
            стафф.append(накоплено[t] >= 1)
            if стафф[t]:
                накоплено[t] -= 1
        цикл = []
        данные_цикла = 0
        p = int(rng.integers(0, 2)) if испортить_p else чётность
        for п in range(7):
            if режим == "M23":
                c_биты = [int(стафф[п])] * 3
            elif п == 0:
                c_биты = [1, 1, int(rng.integers(0, 2))]
            elif п == 2:
                c_биты = [p] * 3
            elif п == 4:
                c_биты = list(rng.integers(0, 2, 3))
            else:
                c_биты = [1, 1, 1]
            for б in range(8):
                if б == 0:
                    служебный = [1, 1, p, p, 0, 1, 0][п]
                elif б % 2:
                    служебный = {1: 1, 3: 0, 5: 0, 7: 1}[б]
                else:
                    служебный = c_биты[б // 2 - 1]
                блок = np.empty(84, np.uint8)
                for t in range(7):
                    if б == 7 and t == п and стафф[п]:
                        кусок = np.concatenate([[rng.integers(0, 2)],
                                                притоки[t][место[t]:место[t] + 11]])
                        место[t] += 11
                    else:
                        кусок = притоки[t][место[t]:место[t] + 12]
                        место[t] += 12
                    assert len(кусок) == 12, "не хватило бит притока"
                    блок[t::7] = кусок
                данные_цикла ^= int(блок.sum() % 2)
                цикл += [np.array([служебный], np.uint8), блок]
        чётность = данные_цикла
        выход += цикл
    return np.concatenate(выход)


# -- наборы потоков (собираются один раз) ------------------------------------------

ДОЛИ_DS2 = (0.20, 0.34, 0.50, 0.70)
ДОЛИ_DS3 = (0.15, 0.25, 0.39, 0.45, 0.55, 0.65, 0.80)


@cache
def набор_t1(сверхциклов_esf, сид):
    """Четыре T1 для DS2: SF, ESF, SF, ESF."""
    return tuple(t1(сверхциклов_esf * (2 if k % 2 == 0 else 1), "SF" if k % 2 == 0 else "ESF",
                    сид=сид * 10 + k)[0] for k in range(4))


@cache
def поток_ds2(циклов=1500, сид=1, инверсные=(2, 4), все_esf=False):
    сверхциклов = циклов * 288 // 4632 + 2
    if все_esf:
        притоки = tuple(t1(сверхциклов, "ESF", сид=сид * 10 + k)[0] for k in range(4))
    else:
        притоки = набор_t1(сверхциклов, сид)
    return ds2(list(притоки), циклов, ДОЛИ_DS2, инверсные=инверсные, сид=сид), притоки


@cache
def поток_ds3(режим="M23", циклов=1300):
    нужно_ds2 = циклов * 672 // 1176 + 2
    притоки_ds2 = []
    источники_t1 = []
    for k in range(7):
        агрегат, t1_ = поток_ds2(нужно_ds2, сид=20 + k)
        притоки_ds2.append(агрегат)
        источники_t1.append(t1_)
    return ds3(притоки_ds2, циклов, ДОЛИ_DS3, режим=режим), притоки_ds2, источники_t1


def ошибки(биты, доля, сид=3):
    return биты ^ (np.random.default_rng(сид).random(len(биты)) < доля).astype(np.uint8)


def вхождение(часть, целое, образец=96):
    """Где в ``целое`` начинается ``часть`` (по первым битам) — или −1."""
    окна = np.lib.stride_tricks.sliding_window_view(целое, образец)
    места = np.flatnonzero(np.all(окна == часть[:образец], axis=1))
    return int(места[0]) if len(места) else -1


def доли_стаффинга(находка):
    """Доли стаффинга притоков из отчёта: {номер: доля}."""
    return {int(н): float(д) / 100 for н, д in re.findall(
        r"приток (\d+): стаффинг в ([0-9.]+) % циклов", " ".join(находка.подробно))}


СЛУЧАЙНЫЕ = np.random.default_rng(11).integers(0, 2, 3_000_000).astype(np.uint8)


# -- T1 ---------------------------------------------------------------------------

class CrcTests(unittest.TestCase):
    def test_crc6_совпадает_с_делением_в_столбик(self):
        rng = np.random.default_rng(5)
        for длина in (1, 7, 64, 193, 4632):
            блоки = rng.integers(0, 2, (6, длина)).astype(np.uint8)
            with self.subTest(длина=длина):
                self.assertEqual([crc6_столбиком(б) for б in блоки],
                                 pdh_na.crc6(блоки).tolist())

    def test_эталон_стандарта(self):
        # x⁶ mod (x⁶+x+1) = x+1: одиночная единица в конце блока даёт 000011.
        self.assertEqual([0, 0, 0, 0, 1, 1], pdh_na.crc6(np.array([[0, 0, 1]]))[0].tolist())
        self.assertEqual([0, 0, 0, 0, 1, 1], crc6_столбиком([1]))
        # x⁷ mod g = x²+x; x¹² mod g = (x+1)² = x²+1.
        self.assertEqual([0, 0, 0, 1, 1, 0], pdh_na.crc6(np.array([[1, 0]]))[0].tolist())
        self.assertEqual([0, 0, 0, 1, 0, 1], pdh_na.crc6(np.array([[1] + [0] * 6]))[0].tolist())


class T1Tests(unittest.TestCase):
    SF, SF_НАГРУЗКА = t1(200, "SF", сид=1)
    ESF, ESF_НАГРУЗКА = t1(100, "ESF", сид=2)

    def проверить_каналы(self, каналы, нагрузка):
        self.assertEqual(list(range(1, 25)), sorted(каналы))
        for номер, ряд in каналы.items():
            исходный = нагрузка[:, 8 * (номер - 1):8 * номер].reshape(-1)
            место = вхождение(ряд, исходный, 64)
            self.assertEqual(0, место % 8, f"КИ{номер}: не с начала байта")
            self.assertGreater(len(ряд), 0.9 * len(исходный) - 8 * 24)
            self.assertTrue(np.array_equal(исходный[место:место + len(ряд)], ряд), f"КИ{номер}")

    def test_sf_при_любом_сдвиге(self):
        rng = np.random.default_rng(4)
        for сдвиг in (0, 1, 100, 192, 193 * 7 + 5, 3000):
            with self.subTest(сдвиг=сдвиг):
                поток = np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8), self.SF])
                найдено = pdh_na.найти(поток)
                self.assertIn("сверхцикл SF", найдено.что)
                self.assertEqual("цикл", найдено.уровень)
                self.assertEqual("каналы", найдено.вид_дальше)
                self.assertEqual(1.0, найдено.уверенность)
                self.проверить_каналы(найдено.дальше, self.SF_НАГРУЗКА)
                if сдвиг < 12 * 193:
                    # Начало — с первого сверхцикла: КИ1 совпадает с самого начала.
                    self.assertTrue(np.array_equal(self.SF_НАГРУЗКА[:, :8].reshape(-1)[:800],
                                                   найдено.дальше[1][:800]))

    def test_sf_с_обрезанным_началом(self):
        for срез in (1, 57, 2316 * 3 + 999):
            with self.subTest(срез=срез):
                найдено = pdh_na.t1(self.SF[срез:])
                self.assertEqual(1.0, найдено.уверенность)
                self.проверить_каналы(найдено.дальше, self.SF_НАГРУЗКА)

    def test_esf_crc6_и_каналы(self):
        rng = np.random.default_rng(6)
        for сдвиг in (0, 5, 4632 + 17):
            with self.subTest(сдвиг=сдвиг):
                поток = np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8), self.ESF])
                найдено = pdh_na.найти(поток)
                self.assertIn("сверхцикл ESF", найдено.что)
                self.assertEqual(1.0, найдено.уверенность)
                self.assertEqual(1.0, найдено.сверка, "CRC-6 должна сходиться во всех сверхциклах")
                текст = " ".join(найдено.подробно)
                self.assertIn("F-биты при расчёте приняты за 1, C1 — старший бит остатка", текст)
                self.assertIn("CRC-6 верна в 100.0 %", найдено.мера)
                self.проверить_каналы(найдено.дальше, self.ESF_НАГРУЗКА)
                self.assertIn("КИ23: постоянное заполнение 0x7F", текст)

    def test_испорченная_crc6_видна(self):
        поток, _ = t1(60, "ESF", сид=3, crc="испорчена")
        найдено = pdh_na.t1(поток)
        self.assertEqual(1.0, найдено.уверенность, "цикл держится на FPS")
        self.assertLess(найдено.сверка, 0.1)
        self.assertIn("CRC-6 не сходится", " ".join(найдено.подробно))

    def test_crc6_по_переданным_f_битам_опознаётся_как_вариант(self):
        поток, _ = t1(60, "ESF", сид=3, crc="как есть")
        найдено = pdh_na.t1(поток)
        self.assertEqual(1.0, найдено.сверка)
        self.assertIn("F-биты при расчёте взяты как переданы", " ".join(найдено.подробно))

    def test_ошибки_линии(self):
        for поток, вид in ((self.SF, "SF"), (self.ESF, "ESF")):
            with self.subTest(вид=вид):
                найдено = pdh_na.найти(ошибки(поток, 1e-4))
                self.assertIn(f"сверхцикл {вид}", найдено.что)
                self.assertGreater(найдено.уверенность, 0.95)
        найдено = pdh_na.t1(ошибки(self.ESF, 1e-4))
        # Сверхцикл 4632 бита: при 1e-4 ошибка бывает в трети сверхциклов.
        self.assertTrue(0.3 < найдено.сверка < 0.95, найдено.сверка)

    def test_инверсия_sf_неотличима_а_esf_нет(self):
        найдено = pdh_na.t1(1 - self.SF)
        self.assertIsNotNone(найдено)
        self.assertEqual(1.0, найдено.уверенность)
        self.assertIsNone(pdh_na.t1(1 - self.ESF))

    def test_в_случайном_и_чужих_потоках_нет(self):
        e1 = в_биты(с.e1(3000))
        e2 = с.pdh([в_биты(с.e1(2100, сид=k)) for k in range(4)], pdh.ИЕРАРХИИ[0], 2400,
                   [0.4, 0.3, 0.5, 0.4])
        for имя, поток in (("случайный", СЛУЧАЙНЫЕ), ("E1", e1), ("E2", e2),
                           ("нули", np.zeros(500_000, np.uint8)),
                           ("единицы", np.ones(500_000, np.uint8))):
            with self.subTest(имя):
                self.assertIsNone(pdh_na.найти(поток))
                self.assertIsNone(pdh_na.t1(поток))
                self.assertIsNone(pdh_na.ds2(поток))
                self.assertIsNone(pdh_na.ds3(поток))


# -- DS2 --------------------------------------------------------------------------

class Ds2Tests(unittest.TestCase):
    def проверить_притоки(self, найдено, источники, точно=True):
        self.assertEqual([1, 2, 3, 4], sorted(найдено.дальше))
        for номер, приток in найдено.дальше.items():
            with self.subTest(приток=номер):
                исходный = источники[номер - 1]
                self.assertGreater(len(приток), 350_000)
                if точно:
                    self.assertTrue(np.array_equal(исходный[:len(приток)], приток))
                else:
                    расхождение = np.mean(исходный[:len(приток)] != приток)
                    self.assertLess(расхождение, 1e-3, "проскальзывание стаффинга")

    def test_притоки_бит_в_бит_и_стаффинг(self):
        агрегат, источники = поток_ds2()
        поток = np.concatenate([СЛУЧАЙНЫЕ[:777], агрегат])
        найдено = pdh_na.найти(поток)
        self.assertEqual("PDH", найдено.уровень)
        self.assertIn("PDH DS2", найдено.что)
        self.assertEqual("притоки", найдено.вид_дальше)
        self.assertEqual(1.0, найдено.уверенность)
        self.assertEqual(1.0, найдено.сверка)
        self.проверить_притоки(найдено, источники)
        текст = " ".join(найдено.подробно)
        доли = доли_стаффинга(найдено)
        for номер, доля in enumerate(ДОЛИ_DS2, start=1):
            self.assertAlmostEqual(доля, доли[номер], delta=0.002)
        # Инверсия 2 и 4: у ESF (приток 4) — по данным, у SF (приток 1, 3) — неотличима.
        self.assertIn("приток 4: инвертирован (по циклу T1)", текст)
        self.assertIn("приток 2: инвертирован (по циклу T1)", текст)
        self.assertIn("приток 1: прямой (цикл T1 держится в обоих видах — по стандарту)", текст)
        self.assertIn("цикл T1 найден в 4 из 4 притоков", найдено.мера)
        for приток in найдено.дальше.values():
            self.assertIn("T1/DS1", pdh_na.найти(приток).что)

    def test_мусор_перед_записью_длиннее_циклов(self):
        # Несинхронные циклы в начале не разбираются: приток идёт с первого бита.
        агрегат, источники = поток_ds2()
        найдено = pdh_na.ds2(np.concatenate([СЛУЧАЙНЫЕ[:1176 * 3 + 50], агрегат]))
        self.assertEqual(1.0, найдено.уверенность)
        self.проверить_притоки(найдено, источники)

    def test_скорость_притока(self):
        найдено = pdh_na.ds2(поток_ds2()[0])
        текст = " ".join(найдено.подробно)
        # 6312000 / 1176 × (288 − 0,2) = 5367,347 × 287,8 = 1544,722 кбит/с, +468 ppm.
        self.assertIn("приток 1: стаффинг в 20.0 % циклов → скорость 1544.722 кбит/с", текст)
        self.assertIn("(+468 ppm от номинала T1)", текст)

    def test_инверсия_не_по_стандарту_решается_данными(self):
        агрегат, источники = поток_ds2(инверсные=(1, 3), все_esf=True)
        найдено = pdh_na.ds2(агрегат)
        self.проверить_притоки(найдено, источники)
        текст = " ".join(найдено.подробно)
        self.assertIn("приток 1: инвертирован (по циклу T1, НЕ как в стандарте)", текст)
        self.assertIn("приток 2: прямой (по циклу T1, НЕ как в стандарте)", текст)
        self.assertIn("приток 4: прямой (по циклу T1, НЕ как в стандарте)", текст)

    def test_ошибка_в_одном_c_бите_не_сбивает(self):
        _, источники = поток_ds2()
        for повтор in range(3):
            with self.subTest(испорчен=f"C{повтор + 1}"):
                агрегат = ds2(list(источники), 1500, ДОЛИ_DS2, сид=1,
                              испортить_c=(2, повтор, 3))
                найдено = pdh_na.ds2(агрегат)
                self.assertTrue(np.array_equal(источники[2][:len(найдено.дальше[3])],
                                               найдено.дальше[3]))

    def test_обрезанное_начало(self):
        агрегат, источники = поток_ds2()
        for срез in (1, 600, 1176 * 5 + 333):
            with self.subTest(срез=срез):
                найдено = pdh_na.ds2(агрегат[срез:])
                self.assertEqual(1.0, найдено.уверенность)
                for номер, приток in найдено.дальше.items():
                    место = вхождение(приток, источники[номер - 1])
                    self.assertGreaterEqual(место, 0)
                    self.assertTrue(np.array_equal(
                        источники[номер - 1][место:место + len(приток)], приток))

    def test_ошибки_линии(self):
        агрегат, источники = поток_ds2()
        найдено = pdh_na.найти(ошибки(агрегат, 1e-4))
        self.assertIn("PDH DS2", найдено.что)
        self.assertGreater(найдено.уверенность, 0.8)
        self.assertGreater(найдено.сверка, 0.9)
        self.проверить_притоки(найдено, источники, точно=False)


# -- DS3 --------------------------------------------------------------------------

class Ds3Tests(unittest.TestCase):
    def проверить_притоки(self, найдено, источники, точно=True):
        self.assertEqual(list(range(1, 8)), sorted(найдено.дальше))
        for номер, приток in найдено.дальше.items():
            with self.subTest(приток=номер):
                исходный = источники[номер - 1]
                self.assertGreater(len(приток), 800_000)
                if точно:
                    self.assertTrue(np.array_equal(исходный[:len(приток)], приток))
                else:
                    self.assertLess(np.mean(исходный[:len(приток)] != приток), 1e-3)

    def test_m23_притоки_бит_в_бит_и_до_t1(self):
        агрегат, притоки, источники_t1 = поток_ds3()
        поток = np.concatenate([СЛУЧАЙНЫЕ[:4000], агрегат])
        найдено = pdh_na.найти(поток)
        self.assertIn("PDH DS3", найдено.что)
        self.assertIn("M23", найдено.что)
        self.assertEqual("PDH", найдено.уровень)
        self.assertEqual(1.0, найдено.уверенность)
        self.assertEqual(1.0, найдено.сверка)
        self.assertIn("P-биты — в 100.0 %", найдено.мера)
        self.assertIn("цикл DS2 найден в 7 из 7 притоков", найдено.мера)
        self.проверить_притоки(найдено, притоки)
        доли = доли_стаффинга(найдено)
        for номер, доля in enumerate(ДОЛИ_DS3, start=1):
            self.assertAlmostEqual(доля, доли[номер], delta=0.002)
        # Цепочка: приток DS3 → DS2 → T1 бит в бит.
        второй = pdh_na.найти(найдено.дальше[5])
        self.assertIn("PDH DS2", второй.что)
        for номер, приток in второй.дальше.items():
            self.assertTrue(np.array_equal(источники_t1[4][номер - 1][:len(приток)], приток))

    def test_мусор_перед_записью_длиннее_циклов(self):
        агрегат, притоки, _ = поток_ds3()
        найдено = pdh_na.ds3(np.concatenate([СЛУЧАЙНЫЕ[:4760 * 2 + 100], агрегат]))
        self.assertEqual(1.0, найдено.уверенность)
        self.проверить_притоки(найдено, притоки)

    def test_c_bit_parity(self):
        агрегат, притоки, _ = поток_ds3("C-bit parity")
        найдено = pdh_na.найти(агрегат)
        self.assertIn("C-bit parity", найдено.что)
        self.assertEqual(1.0, найдено.уверенность)
        self.assertEqual(1.0, найдено.сверка)
        self.assertIn("P-биты — в 100.0 %", найдено.мера)
        текст = " ".join(найдено.подробно)
        self.assertIn("C-биты подцикла 3 повторяют P-биты в 100.0 %", текст)
        self.assertNotIn("вопреки C-битам", текст)
        self.assertIn("приток 1: стаффинг в 100.0 % циклов", текст)
        self.проверить_притоки(найдено, притоки)

    def test_p_биты_проверяются(self):
        _, притоки, _ = поток_ds3()
        агрегат = ds3(притоки, 1300, ДОЛИ_DS3, испортить_p=True)
        найдено = pdh_na.ds3(агрегат)
        self.assertEqual(1.0, найдено.уверенность)
        доля = float(найдено.мера.split("P-биты — в ")[1].split(" %")[0])
        self.assertLess(доля, 70.0)

    def test_обрезанное_начало(self):
        агрегат, притоки, _ = поток_ds3()
        for срез in (3, 4760 * 2 + 1234):
            with self.subTest(срез=срез):
                найдено = pdh_na.ds3(агрегат[срез:])
                self.assertEqual(1.0, найдено.уверенность)
                for номер, приток in найдено.дальше.items():
                    место = вхождение(приток, притоки[номер - 1])
                    self.assertGreaterEqual(место, 0)
                    self.assertTrue(np.array_equal(
                        притоки[номер - 1][место:место + len(приток)], приток))

    def test_ошибки_линии(self):
        агрегат, притоки, _ = поток_ds3()
        найдено = pdh_na.найти(ошибки(агрегат, 1e-4))
        self.assertIn("PDH DS3", найдено.что)
        self.assertIn("M23", найдено.что)
        self.assertGreater(найдено.уверенность, 0.9)
        self.assertGreater(найдено.сверка, 0.9)
        self.проверить_притоки(найдено, притоки, точно=False)

    def test_уровни_не_путаются(self):
        агрегат, притоки, _ = поток_ds3()
        self.assertIsNone(pdh_na.t1(агрегат))
        self.assertIsNone(pdh_na.ds2(агрегат))
        self.assertIsNone(pdh_na.ds3(притоки[0]))
        self.assertIsNone(pdh_na.t1(притоки[0]))

    def test_быстро_на_восьми_мегабитах(self):
        случайные = np.random.default_rng(12).integers(0, 2, 1 << 23).astype(np.uint8)
        начало = time.perf_counter()
        self.assertIsNone(pdh_na.найти(случайные))
        self.assertLess(time.perf_counter() - начало, 10.0)


if __name__ == "__main__":
    unittest.main()


def boc_слова(код, слов):
    """Слово BOC T1.403: 0, код (6 бит, старший первым), 0, восемь единиц."""
    слово = [0] + [(код >> i) & 1 for i in range(5, -1, -1)] + [0] + [1] * 8
    return np.array(слово * слов, np.uint8)


def prm_кадр(секунды, sapi_байт=0x38):
    """PRM: SAPI 14, TEI 0, UI и 4 пары октетов; пара — 16 бит, первый октет младший."""
    return bytes([sapi_байт, 0x01, 0x03]) + b"".join(с.to_bytes(2, "little") for с in секунды)


class EsfDlTests(unittest.TestCase):
    """Канал данных ESF: коды BOC (FreeBSD if_lmc.h T1BOP_*) и отчёты PRM (T1PRM_*)."""

    def test_boc_в_потоке(self):
        dl = np.concatenate([boc_слова(0x07, 30), np.ones(40, np.uint8), boc_слова(0x00, 12)])
        поток, _ = t1(60, "ESF", сид=4, dl=dl)
        текст = " ".join(pdh_na.t1(поток).подробно)
        self.assertIn("в DL — BOC 0x07: включить шлейф линии (30 слов подряд с бита 0 DL)", текст)
        self.assertIn("в DL — BOC 0x00: жёлтая авария (RAI) (12 слов подряд с бита 520 DL)", текст)

    def test_boc_серии(self):
        # Слова идут с любой фазы; серия короче BOC_ПОВТОРОВ не код; неизвестный код — словами.
        dl = np.concatenate([np.zeros(5, np.uint8), boc_слова(0x1C, pdh_na.BOC_ПОВТОРОВ), boc_слова(0x2A, 11),
                             boc_слова(0x19, pdh_na.BOC_ПОВТОРОВ - 1), np.zeros(16, np.uint8)])
        self.assertEqual(pdh_na.boc(dl), [(5, 0x1C, pdh_na.BOC_ПОВТОРОВ), (165, 0x2A, 11)])
        self.assertEqual(pdh_na.boc(np.concatenate([boc_слова(0x12, 10), boc_слова(0x09, 10)])),
                         [(0, 0x12, 10), (160, 0x09, 10)])
        # Фаза 15, серия до самого конца ряда; серия после другой фазы — со своего бита.
        dl = np.concatenate([np.ones(15, np.uint8), boc_слова(0x33, 12)])
        self.assertEqual(pdh_na.boc(dl), [(15, 0x33, 12)])
        dl = np.concatenate([boc_слова(0x07, 10), np.ones(7, np.uint8), boc_слова(0x1C, 10), np.zeros(3, np.uint8)])
        self.assertEqual(pdh_na.boc(dl), [(0, 0x07, 10), (167, 0x1C, 10)])
        # Серия кода 1 сразу после негодных слов — своя, с первого годного слова.
        dl = np.concatenate([np.zeros(16 * 3, np.uint8), boc_слова(0x01, 10)])
        self.assertEqual(pdh_na.boc(dl), [(48, 0x01, 10)])
        # Не слова BOC: единица на месте первого или восьмого бита, ноль среди восьми единиц.
        for слово in ([1, 0, 0, 0, 1, 1, 1, 0] + [1] * 8, [0, 0, 0, 0, 1, 1, 1, 1] + [1] * 8,
                      [0, 0, 0, 0, 1, 1, 1, 0] + [0] + [1] * 7, [0, 0, 0, 0, 1, 1, 1, 0] + [1] * 7 + [0]):
            self.assertEqual(pdh_na.boc(np.array(слово * 20, np.uint8)), [], слово)
        self.assertEqual(pdh_na.boc(np.zeros(15, np.uint8)), [])
        self.assertEqual(pdh_na.boc(np.random.default_rng(1).integers(0, 2, 4000).astype(np.uint8)), [])
        self.assertEqual(pdh_na.BOC_КОДЫ[0x0A], "включить шлейф нагрузки")

    def test_prm(self):
        секунды = [0x1000 | 0x8000 | 0x0100, 0x0400 | 0x0040 | 0x0002, 0x0080 | 0x4000 | 0x0300, 0]
        self.assertEqual(pdh_na.prm(prm_кадр(секунды)), [
            "N 1: ошибок CRC 1, FE", "N 0: ошибок CRC 2–5, LV, SL", "N 3: ошибок CRC 6–10, SE", "N 0: ошибок CRC 0"])
        self.assertEqual(pdh_na.prm(prm_кадр([0x0020 | 0x2000, 0x0004, 0x0001, 0x1004]))[:4], [
            "N 0: ошибок CRC 11–100, LB", "N 0: ошибок CRC 101–319", "N 0: ошибок CRC 320 и больше",
            "N 0: ошибок CRC 1"])
        self.assertIsNotNone(pdh_na.prm(prm_кадр([0] * 4, 0x3A)))
        for плохой in (prm_кадр([0] * 4, 0x3C), prm_кадр([0] * 4)[:-1], b"\x38\x03\x03" + bytes(8),
                       b"\x38\x01\x13" + bytes(8), prm_кадр([0] * 4) + b"\x00"):
            self.assertIsNone(pdh_na.prm(плохой), плохой.hex())

    def test_prm_в_потоке(self):
        кадры = [prm_кадр([0x0100 * (i % 4) | 0x0002, 0, 0, 0]) for i in range(20)]
        dl = в_биты(с.hdlc(кадры, флагов_между=4), "старший")
        поток, _ = t1(400, "ESF", сид=5, dl=dl)
        текст = " ".join(pdh_na.t1(поток).подробно)
        self.assertIn("в DL — отчёты PRM (T1.403): ", текст)
        self.assertRegex(текст, r"последний — N \d: ошибок CRC 0, SL; N 0: ошибок CRC 0; N 0")
