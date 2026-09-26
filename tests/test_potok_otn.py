# -*- coding: utf-8 -*-
"""OTN (G.709): кадр OTUk, скремблер вслепую, MFAS, BIP-8, RS(255,239), нагрузка OPUk.

Синтетический OTU строится здесь же простой независимой реализацией: своё
поле GF(256), свой кодер RS(255,239) (деление на порождающий многочлен),
свой скремблер в виде регистра из 16 ячеек, свой BIP-8. От анализатора —
только проверяемые функции.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import gfp, otn

СТРОК, СТОЛБЦОВ = 4, 4080
КАДР_БИТ = СТРОК * СТОЛБЦОВ * 8
НАГРУЗКА_БАЙТ = СТРОК * 3808

# -- своё поле GF(256) по x⁸ + x⁴ + x³ + x² + 1 ------------------------------------------

ЭКСП = [0] * 512
ЛОГ = [0] * 256
_x = 1
for _i in range(255):
    ЭКСП[_i] = _x
    ЛОГ[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    ЭКСП[_i] = ЭКСП[_i - 255]
УМН = np.zeros((256, 256), dtype=np.uint8)
for _a in range(1, 256):
    for _b in range(1, 256):
        УМН[_a, _b] = ЭКСП[ЛОГ[_a] + ЛОГ[_b]]


def _порождающий():
    """g(x) = ∏ (x − α^i), i = 0…15; коэффициенты от старшего."""
    g = [1]
    for i in range(16):
        новый = g + [0]
        for j, c in enumerate(g):
            новый[j + 1] ^= int(УМН[c, ЭКСП[i]])
        g = новый
    return g


G = _порождающий()


def rs_проверочные(сообщения):
    """Остаток от деления m(x)·x¹⁶ на g(x) для всех сообщений сразу: M × 239 → M × 16."""
    остаток = np.zeros((len(сообщения), 16), dtype=np.uint8)
    for i in range(сообщения.shape[1]):
        обр = сообщения[:, i] ^ остаток[:, 0]
        остаток = np.concatenate([остаток[:, 1:], np.zeros((len(сообщения), 1), np.uint8)],
                                 axis=1)
        for j in range(16):
            остаток[:, j] ^= УМН[G[j + 1], обр]
    return остаток


# -- свой скремблер: регистр 16 ячеек, сброс в FFFF ------------------------------------

def псп_регистром(отводы, длина, начальное=0xFFFF):
    """Регистр: ячейка 0 — новый бит, выход — ячейка 15; обратная связь — ячейки t−1."""
    регистр = [(начальное >> (15 - k)) & 1 for k in range(16)]   # ячейка 15 выходит первой
    регистр = регистр[::-1]
    выход = np.zeros(длина, dtype=np.uint8)
    for n in range(длина):
        выход[n] = регистр[15]
        новый = 0
        for t in отводы:
            новый ^= регистр[t - 1]
        регистр = [новый] + регистр[:15]
    return выход


_ПСП = {}


def псп(отводы):
    if отводы not in _ПСП:
        _ПСП[отводы] = псп_регистром(отводы, КАДР_БИТ - 48)
    return _ПСП[отводы]


G709 = (1, 3, 12, 16)


def tti_байты(sapi: bytes, dapi: bytes) -> np.ndarray:
    т = np.zeros(64, dtype=np.uint8)
    т[1:1 + len(sapi)] = list(sapi)
    т[17:17 + len(dapi)] = list(dapi)
    return т


def otu(нагрузка: np.ndarray, *, mfas0=250, pt=0x05, fec=True, отводы=G709,
        bip_задержка=2, jc=None, njo=None, mfas_шаг=1, скремблер=True):
    """Кадры OTUk (биты подряд) из нагрузки: кадры × 15232 байт (или байт подряд)."""
    нагрузка = np.asarray(нагрузка, dtype=np.uint8).reshape(-1, СТРОК, 3808)
    F = len(нагрузка)
    к = np.zeros((F, СТРОК, СТОЛБЦОВ), dtype=np.uint8)
    к[:, 0, 0:6] = [0xF6, 0xF6, 0xF6, 0x28, 0x28, 0x28]
    mfas = (mfas0 + mfas_шаг * np.arange(F)) % 256
    к[:, 0, 6] = mfas
    sm_tti = tti_байты(b"SAPI-TEST", b"DAPI-XYZ")
    pm_tti = tti_байты(b"PM-TRAIL", b"")
    к[:, 0, 7] = sm_tti[mfas % 64]
    к[:, 2, 9] = pm_tti[mfas % 64]
    к[:, 3, 14] = np.where(mfas == 0, pt, 0)
    if jc is not None:
        к[:, 0:3, 15] = np.asarray(jc, np.uint8)[:, None]
    if njo is not None:
        к[:, 3, 15] = njo
    к[:, :, 16:3824] = нагрузка
    # BIP-8: XOR всех байт OPUk (столбцы 15–3824) кадра i — в кадр i + задержка.
    чётность = np.zeros(F, dtype=np.uint8)
    for f in range(F):
        x = 0
        for b in к[f, :, 14:3824].reshape(-1).tobytes():
            x ^= b
        чётность[f] = x
    for f in range(F):
        прежний = чётность[f - bip_задержка] if f >= bip_задержка else 0x5A
        к[f, 0, 8] = прежний
        к[f, 2, 10] = прежний
    if fec:
        строки = к.reshape(F * СТРОК, СТОЛБЦОВ)
        сообщения = строки[:, :3824].reshape(-1, 239, 16).transpose(0, 2, 1).reshape(-1, 239)
        провер = rs_проверочные(сообщения).reshape(-1, 16, 16).transpose(0, 2, 1)
        строки[:, 3824:] = провер.reshape(-1, 256)
    биты = np.unpackbits(к.reshape(F, -1), axis=1)
    if скремблер:
        биты[:, 48:] ^= псп(отводы)
    return биты.reshape(-1), к


def нагрузка_случайная(кадров, сид=7):
    return np.random.default_rng(сид).integers(0, 256, (кадров, НАГРУЗКА_БАЙТ), dtype=np.uint8)


class OtnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.нагрузка = нагрузка_случайная(16)
        cls.линия, cls.кадры = otu(cls.нагрузка)
        cls.разбор = otn.разобрать(cls.линия)

    def test_опознание_и_проверки(self):
        р = self.разбор
        self.assertEqual((0, False, 16, 1.0), (р["начало"], р["инверсия"], р["кадров"], р["fas"]))
        п = р["проверки"]
        self.assertEqual(1.0, п["mfas"])
        self.assertEqual((1.0, 1.0), п["bip_sm"])
        self.assertEqual((1.0, 1.0), п["bip_pm"])
        self.assertTrue(п["fec"]["используется"])
        self.assertEqual(1.0, п["fec"]["доля"])
        self.assertEqual(16 * 64, п["fec"]["слов"])
        self.assertEqual(250, р["mfas_первый"])
        self.assertEqual(0x05, р["psi"]["pt"])
        н = otn.найти(self.линия)
        self.assertEqual("OTN", н.уровень)
        self.assertEqual("биты", н.вид_дальше)
        self.assertGreater(н.уверенность, 0.99)
        self.assertIn("OTUk", н.что)
        self.assertIn("PT 0x05", н.что)
        текст = "\n".join(н.подробно)
        self.assertIn("стаффинга нет", текст)
        self.assertIn("GFP", текст)

    def test_скремблер_найден_вслепую(self):
        скр = self.разбор["скремблер"]
        self.assertTrue(скр["вслепую"], скр["способ"])
        self.assertEqual(G709, tuple(скр["отводы"]))
        self.assertEqual([1] * 16, list(скр["начальное"]))
        self.assertIn("совпадает с G.709", "\n".join(otn.найти(self.линия).подробно))

    def test_нагрузка_бит_в_бит(self):
        self.assertTrue(np.array_equal(np.unpackbits(self.нагрузка.reshape(-1)),
                                       self.разбор["нагрузка"]))

    def test_начало_с_произвольного_бита_и_инверсия(self):
        шум = с.случайные_биты(12345, сид=9)
        for сдвиг, инверсия in ((12345, False), (3, True)):
            with self.subTest(сдвиг=сдвиг, инверсия=инверсия):
                поток = np.concatenate([шум[:сдвиг], self.линия])
                if инверсия:
                    поток = 1 - поток
                р = otn.разобрать(поток)
                self.assertEqual((сдвиг, инверсия), (р["начало"], р["инверсия"]))
                # FAS — на месте во всех кадрах: инверсия снята до скремблера
                # (иначе её «поглотила» бы ПСП с дополненным состоянием).
                self.assertEqual(1.0, р["fas"])
                self.assertEqual([1] * 16, list(р["скремблер"]["начальное"]))
                self.assertTrue(np.array_equal(np.unpackbits(self.нагрузка.reshape(-1)),
                                               р["нагрузка"]))

    def test_начало_внутри_кадра(self):
        р = otn.разобрать(self.линия[777:])
        self.assertEqual(КАДР_БИТ - 777, р["начало"])
        self.assertEqual(15, р["кадров"])
        self.assertEqual(251, р["mfas_первый"])
        self.assertTrue(np.array_equal(np.unpackbits(self.нагрузка[1:].reshape(-1)),
                                       р["нагрузка"]))

    def test_случайный_поток_не_otn(self):
        self.assertIsNone(otn.найти(с.случайные_биты(20 * КАДР_БИТ, сид=11)))
        self.assertIsNone(otn.найти(self.линия[:2 * КАДР_БИТ]), "меньше трёх кадров")

    def test_редкие_ошибки(self):
        поток = self.линия.copy()
        rng = np.random.default_rng(3)
        места = np.flatnonzero(rng.random(len(поток)) < 1e-5)
        поток[места] ^= 1
        н = otn.найти(поток)
        self.assertIsNotNone(н)
        self.assertGreater(н.уверенность, 0.8)
        р = otn.разобрать(поток)
        self.assertGreaterEqual(р["проверки"]["mfas"], 0.9)
        self.assertGreaterEqual(р["проверки"]["fec"]["доля"], 0.9)
        self.assertGreater(р["проверки"]["bip_sm"][1], 0.75)
        разница = int(np.sum(р["нагрузка"] != np.unpackbits(self.нагрузка.reshape(-1))))
        self.assertLessEqual(разница, len(места))

    def test_fec_исправляет_ошибки_линии(self):
        """Ошибки 1e-4 (около 13 бит на кадр): FEC RS(255, 239) их исправляет — нагрузка бит в бит."""
        поток = self.линия.copy()
        rng = np.random.default_rng(5)
        места = np.flatnonzero(rng.random(len(поток)) < 1e-4)
        поток[места] ^= 1
        р = otn.разобрать(поток)
        и = р["проверки"]["fec"]["исправление"]
        self.assertGreater(и["байт"], 0)
        self.assertEqual(и["неисправимых"], 0)
        self.assertTrue(np.array_equal(р["нагрузка"], np.unpackbits(self.нагрузка.reshape(-1))))
        текст = "\n".join(otn.найти(поток).подробно)
        self.assertIn(f"FEC исправил {и['байт']} байт", текст)

    def test_fec_без_ошибок(self):
        текст = "\n".join(otn.найти(self.линия).подробно)
        self.assertIn("FEC: ошибок нет — синдромы нулевые у всех", текст)

    def test_ошибка_в_одном_слове_rs(self):
        # Байт 5 строки 2 кадра 3 — в слове 5 этой строки: ненулевой синдром ровно у одного слова.
        кадры = otn.в_кадры(np.unpackbits(self.кадры.reshape(len(self.кадры), -1), axis=1)
                            .reshape(-1))
        self.assertEqual(1.0, otn.fec(кадры)["доля"])
        кадры[3, 1, 5] ^= 0x10
        слова = otn.rs_слова(кадры)
        плохие = np.flatnonzero(otn.rs_синдромы(слова).any(axis=1))
        self.assertEqual([3 * 64 + 16 + 5], плохие.tolist())
        self.assertEqual(1 - 1 / (16 * 64), otn.fec(кадры)["доля"])

    def test_два_одинаковых_искажения_в_слове(self):
        # Байты 5 и 21 строки 1 — оба в слове 5; одинаковое искажение гасит
        # синдром в α⁰ (e + e = 0), но не в α¹: слово всё равно не кодовое.
        кадры = self.кадры.copy()
        кадры[2, 0, 5] ^= 0x33
        кадры[2, 0, 21] ^= 0x33
        синдромы = otn.rs_синдромы(otn.rs_слова(кадры))[2 * 64 + 5]
        self.assertEqual(0, синдромы[0])
        self.assertNotEqual(0, синдромы[1])
        self.assertEqual(1 - 1 / (16 * 64), otn.fec(кадры)["доля"])

    def test_bip8_проверяет_место_и_задержку(self):
        линия, _ = otu(self.нагрузка, bip_задержка=1)
        р = otn.разобрать(линия)
        # Скремблер подтверждён через RS; BIP-8 на месте G.709 не сходится.
        self.assertIsNotNone(р["скремблер"])
        кадров, бит = р["проверки"]["bip_sm"]
        self.assertLess(кадров, 0.2)
        self.assertLess(бит, 0.7)
        self.assertEqual(((0, 8), 1), р["проверки"]["bip_sm_где"][:2])
        self.assertIn("столбце 9 с задержкой 1", "\n".join(otn.найти(линия).подробно))

    def test_mfas_не_подряд_не_подтверждает(self):
        # MFAS стоит на месте: скремблер не подтверждён ни одной гипотезой.
        линия, _ = otu(self.нагрузка[:10], mfas_шаг=0, fec=False)
        р = otn.разобрать(линия)
        self.assertIsNone(р["скремблер"])
        н = otn.найти(линия)
        self.assertIn("скремблер не снят", н.что)
        self.assertLess(н.уверенность, 0.9)
        self.assertIsNone(н.дальше)


class OtnВариантыTests(unittest.TestCase):
    def test_без_fec(self):
        нагрузка = нагрузка_случайная(10, сид=2)
        линия, _ = otu(нагрузка, fec=False)
        р = otn.разобрать(линия)
        self.assertFalse(р["проверки"]["fec"]["используется"])
        self.assertEqual(1.0, р["проверки"]["fec"]["нулевых_байт"])
        self.assertTrue(np.array_equal(np.unpackbits(нагрузка.reshape(-1)), р["нагрузка"]))
        н = otn.найти(линия)
        self.assertIn("FEC не используется", н.мера)
        self.assertGreater(н.уверенность, 0.99)

    def test_другой_полином_находится_вслепую(self):
        # Не полином G.709: запасная гипотеза не подошла бы — решает поиск вслепую.
        нагрузка = нагрузка_случайная(12, сид=4)
        for отводы in ((4, 13, 15, 16), (2, 3, 5, 16)):
            with self.subTest(отводы=отводы):
                линия, _ = otu(нагрузка, отводы=отводы)
                р = otn.разобрать(линия)
                self.assertTrue(р["скремблер"]["вслепую"])
                self.assertEqual(отводы, tuple(р["скремблер"]["отводы"]))
                self.assertTrue(np.array_equal(np.unpackbits(нагрузка.reshape(-1)),
                                               р["нагрузка"]))

    def test_без_скремблера(self):
        нагрузка = нагрузка_случайная(8, сид=6)
        линия, _ = otu(нагрузка, скремблер=False)
        р = otn.разобрать(линия)
        self.assertIsNone(р["скремблер"]["отводы"])
        self.assertTrue(np.array_equal(np.unpackbits(нагрузка.reshape(-1)), р["нагрузка"]))

    def test_gfp_в_нагрузке_находит_основной_разбор(self):
        # Кадры GFP с пустыми между ними: у многих столбцов нагрузки данные
        # постоянны — BM вслепую может дать чужой полином; подтверждается
        # проверками тот, что снимает скремблер верно.
        поток = с.gfp([с.ethernet(п) for п in с.пакеты_ip(300)], пустых_между=3)
        кадров = 12
        байты = np.resize(np.frombuffer(поток, np.uint8), кадров * НАГРУЗКА_БАЙТ)
        линия, _ = otu(байты, mfas0=0)
        н = otn.найти(линия)
        self.assertGreater(н.уверенность, 0.99)
        self.assertTrue(np.array_equal(np.unpackbits(байты), н.дальше))
        найдено = gfp.gfp(н.дальше)
        self.assertIsNotNone(найдено)
        self.assertIn("Ethernet", найдено.что)

    def test_заполнение_запасная_гипотеза_g709(self):
        # Нагрузка — сплошь пустые кадры GFP (B6AB31E0), одинаковые в каждом
        # кадре: самый длинный устойчивый участок — ПСП ⊕ заполнение, и полином
        # по нему вслепую неверен. Принимается запасная гипотеза G.709 — та из
        # двух записей, что подтверждается BIP-8 и синдромами.
        кадров = 10
        байты = np.resize(np.frombuffer(bytes.fromhex("B6AB31E0"), np.uint8),
                          кадров * НАГРУЗКА_БАЙТ)
        for отводы in (G709, (4, 13, 15, 16)):
            with self.subTest(отводы=отводы):
                линия, _ = otu(байты, отводы=отводы)
                р = otn.разобрать(линия)
                скр = р["скремблер"]
                self.assertFalse(скр["вслепую"])
                self.assertEqual(отводы, tuple(скр["отводы"]))
                self.assertEqual((1.0, 1.0), р["проверки"]["bip_sm"])
                self.assertEqual(1.0, р["проверки"]["fec"]["доля"])
                self.assertTrue(np.array_equal(np.unpackbits(байты), р["нагрузка"]))
                текст = "\n".join(otn.найти(линия).подробно)
                self.assertIn("запасная гипотеза по G.709", текст)
                if отводы != G709:
                    self.assertIn("отвергнуты проверками: 1 + x^-1 + x^-3 + x^-12 + x^-16",
                                  текст)

    def test_чужой_полином_при_заполнении_не_подтверждается(self):
        кадров = 10
        байты = np.resize(np.frombuffer(bytes.fromhex("B6AB31E0"), np.uint8),
                          кадров * НАГРУЗКА_БАЙТ)
        линия, _ = otu(байты, отводы=(2, 3, 5, 16))
        р = otn.разобрать(линия)
        self.assertIsNone(р["скремблер"])
        self.assertIn("скремблер не снят", otn.найти(линия).что)

    def test_tti_частично(self):
        # 16 кадров с MFAS 250…265: байты TTI 58…63 и 0…9 — SAPI до десятого байта.
        линия, _ = otu(нагрузка_случайная(16), fec=False)
        т = otn.разобрать(линия)["tti_sm"]
        self.assertEqual(16, т["собрано"])
        self.assertEqual({"SAPI": "«SAPI-TEST······»"}, т["части"])

    def test_tti_и_pt_за_сверхцикл(self):
        нагрузка = нагрузка_случайная(66, сид=8)
        линия, _ = otu(нагрузка, mfas0=200, pt=0x03, fec=False)
        р = otn.разобрать(линия)
        self.assertEqual(64, р["tti_sm"]["собрано"])
        self.assertEqual({"SAPI": "«SAPI-TEST»", "DAPI": "«DAPI-XYZ»"}, р["tti_sm"]["части"])
        self.assertEqual({"SAPI": "«PM-TRAIL»"}, р["tti_pm"]["части"])
        self.assertEqual(0x03, р["psi"]["pt"])
        текст = "\n".join(otn.найти(линия).подробно)
        self.assertIn("SM TTI (собрано 64 из 64 байт): SAPI «SAPI-TEST»; DAPI «DAPI-XYZ»", текст)
        self.assertIn("PT = 0x03 — бит-синхронное", текст)

    def test_pt_не_попал(self):
        линия, _ = otu(нагрузка_случайная(8, сид=1), mfas0=10, fec=False)
        р = otn.разобрать(линия)
        self.assertIsNone(р["psi"]["pt"])
        self.assertIn("нет кадра с MFAS = 0", "\n".join(otn.найти(линия).подробно))

    def test_стаффинг_amp(self):
        # Клиент CBR: 00 — без выравнивания, 01 — NJO несёт байт клиента,
        # 11 — PJO пустой. Нагрузка после разбора == поток клиента.
        кадров = 10
        решения = [0, 1, 3, 0, 3, 1, 0, 0, 3, 0]
        rng = np.random.default_rng(12)
        клиент = rng.integers(0, 256, кадров * НАГРУЗКА_БАЙТ + 50, dtype=np.uint8)
        нагрузка = np.zeros((кадров, СТРОК, 3808), np.uint8)
        njo = np.zeros(кадров, np.uint8)
        место = 0
        for f, jc in enumerate(решения):
            нагрузка[f, :3] = клиент[место:место + 3 * 3808].reshape(3, 3808)
            место += 3 * 3808
            if jc == 1:
                njo[f] = клиент[место]
                место += 1
            if jc == 3:
                нагрузка[f, 3, 0] = 0xAA            # PJO — пустышка
                нагрузка[f, 3, 1:] = клиент[место:место + 3807]
                место += 3807
            else:
                нагрузка[f, 3] = клиент[место:место + 3808]
                место += 3808
        jc_байты = np.array(решения, np.uint8)
        линия, _ = otu(нагрузка, mfas0=250, pt=0x02, jc=jc_байты, njo=njo)
        р = otn.разобрать(линия)
        self.assertTrue(р["amp"])
        self.assertEqual({0: 5, 1: 2, 2: 0, 3: 3}, р["jc"]["счёт"])
        self.assertTrue(np.array_equal(np.unpackbits(клиент[:место]), р["нагрузка"]))
        текст = "\n".join(otn.найти(линия).подробно)
        self.assertIn("стаффинг есть", текст)
        self.assertIn("учтён по правилам AMP", текст)

    def test_k_по_скорости(self):
        линия, _ = otu(нагрузка_случайная(8, сид=3), fec=False)
        self.assertIn("OTU2,", otn.найти(линия, скорость=10.7092253e9).что)
        self.assertIn("OTU4,", otn.найти(линия, скорость=111.80997e9).что)
        self.assertIn("OTUk,", otn.найти(линия).что)
        н = otn.найти(линия, скорость=10.0e9)
        self.assertIn("OTUk,", н.что)
        self.assertIn("k не указывается", "\n".join(н.подробно))


if __name__ == "__main__":
    unittest.main()
