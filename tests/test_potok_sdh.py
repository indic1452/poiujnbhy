"""SDH/SONET: кадр, скремблер по B1, AU-4/AU-3, VC-4 по B3, TU-12 → VC-12 по V5 → E1 по FAS.

Поток строится здесь же простым генератором по G.707: E1 асинхронно в VC-12
(C1 C2, S1 S2), TU-12 с указателем V1 V2 в сверхцикле из четырёх кадров,
TUG-2 → TUG-3 → VC-4 с POH (J1, B3, C2, H4), AU-4 с указателем H1 H2, STM-N с
A1 A2, J0, B1 и кадровым скремблером 1 + x⁶ + x⁷.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import kanal_sintez as кс
import potok_sintez as с
from reportgen.potok import cikl, hdlc, kanal, sdh
from reportgen.potok.bity import в_биты
from test_potok_e1_crc4 import e1_crc4


def псп_байты(длина: int) -> np.ndarray:
    """Кадровый скремблер SDH: 1 + x⁶ + x⁷ от 1111111, байтами."""
    рег = [1] * 7
    биты = []
    for _ in range(длина * 8):
        биты.append(рег[0])
        новый = рег[0] ^ рег[1]            # s[n+7] = s[n] ⊕ s[n+1]
        рег = рег[1:] + [новый]
    return np.packbits(np.array(биты, dtype=np.uint8))


def bip2(байты: np.ndarray) -> int:
    б = np.unpackbits(байты).reshape(-1, 8)
    return ((int(б[:, 0::2].sum()) & 1) << 1) | (int(б[:, 1::2].sum()) & 1)


def vc12_из_e1(e1: np.ndarray, сколько: int, rng, плохой_v5: bool = False) -> list:
    """VC-12 по 140 байт с асинхронным E1: C1 = 0 (S1 — данные), C2 — иногда стаффинг."""
    итог, место, прежний = [], 0, None
    for _ in range(сколько):
        v = np.zeros(140, dtype=np.uint8)
        s2_пустой = rng.random() < 0.7
        for начало in (2, 37, 72):
            v[начало:начало + 32] = np.packbits(e1[место:место + 256])
            место += 256
        c = 0b01000000 if s2_пустой else 0          # C1 = 0, C2 = стаффинг S2
        for к in (36, 71):
            v[к] = c
        s1 = int(e1[место])
        место += 1
        v[106] = c | s1                              # … R R R R R S1
        s2 = 0
        if not s2_пустой:
            s2 = int(e1[место])
            место += 1
        семь = e1[место:место + 7]
        место += 7
        v[107] = (s2 << 7) | int(np.packbits(np.concatenate([[0], семь]))[0])
        v[108:139] = np.packbits(e1[место:место + 248])
        место += 248
        v[0] = 0b00000100                            # V5: метка сигнала, BIP-2 — ниже
        if прежний is not None:
            v[0] |= (bip2(прежний) ^ (3 if плохой_v5 else 0)) << 6
        прежний = v
        итог.append(v)
    return итог, место


def tu12_кадры(vc12: list, p: int, кадров: int) -> np.ndarray:
    """VC-12 → байты TU-12 по кадрам (кадры × 36) с указателем p в V1 V2."""
    поток = np.concatenate(vc12)
    циклов = кадров // 4 + 2
    s = np.zeros((циклов, 144), dtype=np.uint8)
    for k in range(циклов):
        q = np.zeros(140, dtype=np.uint8)
        for j in range(140):
            x = 140 * k + j - p
            if 0 <= x < len(поток):
                q[j] = поток[x]
        s[k, 37:72], s[k, 73:108], s[k, 109:144] = q[0:35], q[35:70], q[70:105]
        if k + 1 < циклов:
            s[k + 1, 1:36] = q[105:140]
        s[k, 0] = 0x68 | (p >> 8)                    # V1: NDF 0110, размер 10
        s[k, 36] = p & 0xFF                          # V2
    return s.reshape(-1, 36)[:кадров]


def чередовать(части: list) -> np.ndarray:
    """Побайтное чередование столбцов: столбец i·k + j — столбец i части j."""
    k = len(части)
    итог = np.zeros((9, k * части[0].shape[1]), dtype=np.uint8)
    for j, ч in enumerate(части):
        итог[:, j::k] = ч
    return итог


def tug_структура(tu12_кадра: dict) -> np.ndarray:
    """TU-12 (9 × 4) → TUG-2 (3 вперемежку) → TUG-3 (2 служебных + 7 TUG-2) → 258 столбцов VC-4."""
    пусто = np.zeros((9, 4), dtype=np.uint8)
    tug3 = []
    for t in range(3):
        tug2 = [чередовать([tu12_кадра.get((t, m, n), пусто) for n in range(3)]) for m in range(7)]
        tug3.append(np.concatenate([np.zeros((9, 2), np.uint8), чередовать(tug2)], axis=1))
    return чередовать(tug3)


def crc7_трассы(кусок: list) -> int:
    """CRC-7 x⁷ + x³ + 1 по 16 байтам трассы с обнулённым полем CRC (MSB первого байта — 1)."""
    r = 0
    for б in [0x80] + кусок[1:]:
        for i in range(7, -1, -1):
            обратная = ((r >> 6) & 1) ^ ((б >> i) & 1)
            r = (r << 1) & 0x7F
            if обратная:
                r ^= 0b0001001
    return r


def vc4_кадры(кадров: int, tu12: dict, нагрузка=None, трасса=b"SDH-TEST-ROUTE") -> list:
    """VC-4 (9 × 261) по кадрам: POH J1 (16 байт с CRC-7), B3, C2, H4; TU-12 или нагрузка C-4."""
    j1 = [0x80] + list(трасса.ljust(15, b" "))
    j1[0] |= crc7_трассы(j1)
    итог, прежний = [], None
    for f in range(кадров):
        v = np.zeros((9, 261), dtype=np.uint8)
        if нагрузка is not None:
            v[:, 1:] = нагрузка[f]
        if tu12:
            v[:, 3:] = tug_структура({адрес: байты[f].reshape(9, 4) for адрес, байты in tu12.items()})
        v[0, 0] = j1[f % 16]
        v[2, 0] = 0x02 if tu12 else 0x1B
        v[5, 0] = f % 4                               # H4: номер кадра в сверхцикле TU
        if прежний is not None:
            v[1, 0] = np.bitwise_xor.reduce(прежний.reshape(-1))
        прежний = v
        итог.append(v)
    return итог


def stm(vc4_списки: list, указатели: list, N: int = 1, скремблер: bool = True,
        сцепка: bool = False, служебные=None) -> np.ndarray:
    """Кадры STM-N из N VC-4 (или одного VC-4-Nc) с указателями AU-4; биты подряд."""
    кадров = len(vc4_списки[0])
    столбцов = 270 * N
    область = []            # по AU-4: линейная область нагрузки (кадры × 2349)
    for vc4, p in zip(vc4_списки, указатели, strict=True):
        поток = np.concatenate([v.reshape(-1) for v in vc4])
        g = np.zeros(кадров * 2349, dtype=np.uint8)
        начало = 783 + 3 * p
        g[начало:] = поток[:len(g) - начало]
        область.append(g.reshape(кадров, 9, 261))
    маска = псп_байты(9 * столбцов - 9 * N)
    итог, прежний = [], None
    for f in range(кадров):
        к = np.zeros((9, столбцов), dtype=np.uint8)
        к[0, :3 * N] = 0xF6
        к[0, 3 * N:6 * N] = 0x28
        к[0, 6 * N] = 0x01                            # J0
        for a in range(N):
            p = указатели[a] if a < len(указатели) else 0
            if сцепка and a > 0:
                к[3, a], к[3, 3 * N + a] = 0x9B, 0xFF
            else:
                к[3, a], к[3, 3 * N + a] = 0x68 | (p >> 8), p & 0xFF
            к[3, N + a], к[3, 2 * N + a] = 0x9B, 0x9B   # Y
            к[3, 4 * N + a], к[3, 5 * N + a] = 0xFF, 0xFF
            if a < len(область):
                # Столбцы нагрузки AU-4 №a: STM-1 №a, местные столбцы 9…269.
                к[:, 9 * N + a::N] = область[a][f]
        if служебные is not None:
            служебные(f, к)
        if прежний is not None:
            к[1, 0] = np.bitwise_xor.reduce(прежний)
        плоский = к.reshape(-1).copy()
        if скремблер:
            плоский[9 * N:] ^= маска
        прежний = плоский
        итог.append(плоский)
    return np.unpackbits(np.concatenate(итог))


def sts1(spe: list, p: int) -> np.ndarray:
    """STS-1 / STM-0: кадр 9 × 90, TOH 3 столбца, SPE 9 × 87 (POH, заполнение 29 и 58), указатель p."""
    кадров = len(spe)
    поток = np.concatenate([v.reshape(-1) for v in spe])
    g = np.zeros(кадров * 783, dtype=np.uint8)
    g[261 + p:] = поток[:len(g) - 261 - p]
    область = g.reshape(кадров, 9, 87)
    маска = псп_байты(810 - 3)
    итог, прежний = [], None
    for f in range(кадров):
        к = np.zeros((9, 90), dtype=np.uint8)
        к[0, 0], к[0, 1], к[0, 2] = 0xF6, 0x28, 0x01
        к[3, 0], к[3, 1] = 0x68 | (p >> 8), p & 0xFF
        к[:, 3:] = область[f]
        if прежний is not None:
            к[1, 0] = np.bitwise_xor.reduce(прежний)
        плоский = к.reshape(-1).copy()
        плоский[3:] ^= маска
        прежний = плоский
        итог.append(плоский)
    return np.unpackbits(np.concatenate(итог))


def spe_кадры(нагрузка: np.ndarray) -> list:
    """SPE / VC-3 (9 × 87): POH (J1, B3, C2), заполнение 29 и 58, нагрузка — остальные 84 столбца."""
    итог, прежний = [], None
    столбцы = [c for c in range(1, 87) if c not in (29, 58)]
    for f in range(len(нагрузка)):
        v = np.zeros((9, 87), dtype=np.uint8)
        v[:, столбцы] = нагрузка[f]
        v[0, 0], v[2, 0] = 0x01, 0x04
        if прежний is not None:
            v[1, 0] = np.bitwise_xor.reduce(прежний.reshape(-1))
        прежний = v
        итог.append(v)
    return итог


class SdhTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(3)
        cls.кадров = 200
        cls.e1 = {}
        tu = {}
        for адрес, p, сид in (((0, 0, 0), 5, 1), ((1, 2, 1), 77, 2), ((2, 6, 2), 139, 3)):
            e1 = в_биты(с.e1(400, сид=сид))
            vc, _ = vc12_из_e1(e1, cls.кадров // 4 + 2, rng)
            tu[адрес] = tu12_кадры(vc, p, cls.кадров)
            cls.e1[адрес] = e1
        cls.поток = stm([vc4_кадры(cls.кадров, tu)], [522])
        cls.найдено = sdh.найти(np.concatenate([rng.integers(0, 2, 1234).astype(np.uint8), cls.поток]))

    def test_кадр_скремблер_b1_b3(self):
        н = self.найдено
        self.assertIsNotNone(н)
        self.assertTrue(н.что.startswith("SDH STM-1 (STS-3) (G.707): VC-4"), н.что)
        текст = " ".join(н.подробно)
        self.assertIn("B1 (BIP-8 по предыдущему кадру) сошёлся в 100.0 %", текст)
        self.assertIn("1 + x^-6 + x^-7", текст)
        self.assertIn("начальное 1111111", текст)
        self.assertIn("VC-4 №1: указатель 522", текст)
        self.assertIn("B3 сошёлся в 100.0 %", текст)
        self.assertIn("J1: 16 байт: «SDH-TEST-ROUTE», CRC-7 (x⁷ + x³ + 1) сошлась", текст)
        self.assertGreater(н.уверенность, 0.95)

    def test_dcc_k1k2_s1(self):
        """DCC D1–D3 с LAPD (HDLC по 3 байта на кадр), K2 — MS-RDI (биты 6–8 = 110), S1 — PRC."""
        dcc = np.frombuffer(кс.поток_hdlc(кс.lapd(80)), dtype=np.uint8)
        кадров = len(dcc) // 3 + 8

        def служебные(f, к):
            к[2, [0, 3, 6]] = dcc[(3 * f + np.arange(3)) % len(dcc)]
            к[4, 3], к[4, 6], к[8, 0] = 0x00, 0x06, 0x02
        rng = np.random.default_rng(5)
        нагрузка = [rng.integers(0, 256, (9, 261)).astype(np.uint8) for _ in range(кадров)]
        найдено = sdh.найти(stm([нагрузка], [0], служебные=служебные))
        текст = "\n".join(найдено.подробно)
        self.assertIn("K1 0x00, K2 0x06 (APS: запрос 0, канал 0; 1+1; MS-RDI)", текст)
        self.assertIn("S1: SSM 0010 — QL-PRC (вариант I)", текст)
        self.assertRegex(текст, r"DCC D1–D3 \(192 кбит/с\): HDLC")
        self.assertIn("DCC D4–D12 (576 кбит/с): заполнен 0x00", текст)
        кадры = hdlc.найти(найдено.дальше["DCC D1–D3 (192 кбит/с)"])
        self.assertEqual("ISDN, LAPD (Q.921)", kanal.найти(кадры.дальше).что)

    def test_dcc_d4_d12(self):
        """DCC D4–D12 с LAPD (HDLC по 9 байт на кадр: строки 5–7, столбцы 0, 3, 6), D1–D3 — не HDLC."""
        dcc = np.frombuffer(кс.поток_hdlc(кс.lapd(200, сид=7)), dtype=np.uint8)
        кадров = len(dcc) // 9 + 8
        rng = np.random.default_rng(6)

        def служебные(f, к):
            к[5:8, :][:, [0, 3, 6]] = dcc[(9 * f + np.arange(9)) % len(dcc)].reshape(3, 3)
            к[2, [0, 3, 6]] = rng.integers(0, 256, 3)
        нагрузка = [rng.integers(0, 256, (9, 261)).astype(np.uint8) for _ in range(кадров)]
        найдено = sdh.найти(stm([нагрузка], [0], служебные=служебные))
        текст = "\n".join(найдено.подробно)
        self.assertIn("DCC D1–D3 (192 кбит/с): HDLC не найден", текст)
        self.assertRegex(текст, r"DCC D4–D12 \(576 кбит/с\): HDLC")
        self.assertNotIn("DCC D1–D3 (192 кбит/с)", найдено.дальше)
        кадры = hdlc.найти(найдено.дальше["DCC D4–D12 (576 кбит/с)"])
        self.assertEqual("ISDN, LAPD (Q.921)", kanal.найти(кадры.дальше).что)
        self.assertGreaterEqual(кадры.уверенность, 0.95)

    def test_три_e1_бит_в_бит(self):
        дальше = self.найдено.дальше
        self.assertEqual(3, sum(1 for к in дальше if к.endswith("E1")), list(дальше))
        for (t, m, n), e1 in self.e1.items():
            ключ = f"VC-4 №1 TU-12 {t + 1}-{m + 1}-{n + 1} E1"
            with self.subTest(ключ=ключ):
                ряд = дальше[ключ]
                self.assertGreaterEqual(cikl.e1(ряд).уверенность, 0.99)
                # Выделенный приток — непрерывный кусок исходного E1.
                исходный = e1.tobytes()
                кусок = ряд[:4096].tobytes()
                self.assertIn(кусок, исходный)
        текст = " ".join(self.найдено.подробно)
        self.assertIn("TU-12 с годным VC-12 — 3, из них E1 с FAS — 3", текст)
        self.assertIn("TU-12 2-3-2: указатель 77, V5 (BIP-2) сошёлся в 100 %, метка V5 010 (асинхронное)", текст)
        self.assertIn("C2 = 0x02 (структура TUG)", текст)

    def test_e1_с_crc4_в_tu12(self):
        """E1 со сверхциклом CRC-4 в TU-12: CRC-4 проверяется у выделенного притока — выделение
        (указатель, стаффинг C1/C2) подтверждено до бита."""
        rng = np.random.default_rng(12)
        кадров = 200
        vc, _ = vc12_из_e1(e1_crc4(40, сид=21), кадров // 4 + 2, rng)
        поток = stm([vc4_кадры(кадров, {(0, 3, 1): tu12_кадры(vc, 40, кадров)})], [100])
        найдено = sdh.найти(поток)
        текст = " ".join(найдено.подробно)
        self.assertIn("TU-12 1-4-2: указатель 40", текст)
        self.assertIn("CRC-4 сошлась в 100 % подсверхциклов", текст)
        self.assertNotIn("CRC-4", " ".join(self.найдено.подробно))       # у E1 без CRC-4 — нет

    def test_нагрузка_c4_как_есть(self):
        rng = np.random.default_rng(9)
        кадров = 40
        нагрузка = rng.integers(0, 256, (кадров, 9, 260)).astype(np.uint8)
        поток = stm([vc4_кадры(кадров, {}, нагрузка)], [0])
        н = sdh.найти(поток)
        self.assertIsNotNone(н)
        ряд = н.дальше["VC-4 №1 нагрузка"]
        ждём = np.unpackbits(нагрузка.reshape(-1))
        # Первый VC-4 начинается в кадре 1 (указатель 0 — строка 4 кадра 0), последний неполный.
        self.assertTrue(np.array_equal(ждём[:len(ряд)], ряд))
        self.assertGreaterEqual(len(ряд), (кадров - 2) * 9 * 260 * 8)

    def test_stm4_четыре_au4_и_сцепка(self):
        rng = np.random.default_rng(4)
        кадров = 12
        vc = [vc4_кадры(кадров, {}, rng.integers(0, 256, (кадров, 9, 260)).astype(np.uint8)) for _ in range(4)]
        н = sdh.найти(stm(vc, [0, 100, 200, 782], N=4))
        self.assertIsNotNone(н)
        self.assertEqual("SDH STM-4 (STS-12) (G.707): VC-4, VC-4, VC-4, VC-4", н.что)
        self.assertEqual(4, len(н.дальше))
        сцеплен = sdh.найти(stm([vc[0]], [10], N=4, сцепка=True))
        self.assertEqual("SDH STM-4 (STS-12) (G.707): VC-4-4c", сцеплен.что)

    def test_мусор_в_tu12_не_приток(self):
        # Указатель TU-12 годен, а VC-12 — случайный, без BIP-2 и без E1: такой TU не приток.
        rng = np.random.default_rng(12)
        кадров = 120
        vc = [rng.integers(0, 256, 140).astype(np.uint8) for _ in range(кадров // 4 + 2)]
        e1 = в_биты(с.e1(400, сид=4))
        годный, _ = vc12_из_e1(e1, кадров // 4 + 2, rng)
        tu = {(0, 1, 0): tu12_кадры(vc, 10, кадров), (0, 2, 0): tu12_кадры(годный, 20, кадров)}
        н = sdh.найти(stm([vc4_кадры(кадров, tu)], [5]))
        self.assertEqual(["VC-4 №1 TU-12 1-3-1 E1"], list(н.дальше))

    def test_e1_при_испорченном_v5(self):
        # BIP-2 испорчен (линия или иное прочтение стандарта), а E1 с FAS внутри есть: приток берём.
        rng = np.random.default_rng(13)
        кадров = 120
        vc, _ = vc12_из_e1(в_биты(с.e1(400, сид=6)), кадров // 4 + 2, rng, плохой_v5=True)
        н = sdh.найти(stm([vc4_кадры(кадров, {(1, 0, 2): tu12_кадры(vc, 33, кадров)})], [5]))
        self.assertEqual(["VC-4 №1 TU-12 2-1-3 E1"], list(н.дальше))
        self.assertRegex(" ".join(н.подробно), r"V5 \(BIP-2\) сошёлся в [0-4]?\d % — не сошёлся, приток подтверждён FAS")

    def test_sts1_vc3(self):
        rng = np.random.default_rng(6)
        нагрузка = rng.integers(0, 256, (120, 9, 84)).astype(np.uint8)
        н = sdh.найти(sts1(spe_кадры(нагрузка), 400))
        self.assertIsNotNone(н)
        self.assertEqual("SDH STM-0 (STS-1) (G.707): VC-3", н.что)
        ряд = н.дальше["VC-3 №1 нагрузка"]
        ждём = np.unpackbits(нагрузка.reshape(-1))
        self.assertTrue(np.array_equal(ждём[:len(ряд)], ряд))
        self.assertIn("VC-3 №1: указатель 400, B3 сошёлся в 100.0 %, C2 = 0x04 (асинхронное отображение "
                      "34 368 / 44 736 кбит/с в C-3)", " ".join(н.подробно))

    def test_tu3_в_vc4(self):
        # TUG-3 №2 несёт TU-3: указатель в первом столбце, VC-3 (85 столбцов) со смещением от байта после H3.
        rng = np.random.default_rng(8)
        кадров, p = 60, 300
        нагрузка = rng.integers(0, 256, (кадров, 9, 84)).astype(np.uint8)
        vc3, прежний = [], None
        for f in range(кадров):
            v = np.zeros((9, 85), dtype=np.uint8)
            v[:, 1:] = нагрузка[f]
            v[0, 0], v[2, 0] = 0x05, 0x04
            if прежний is not None:
                v[1, 0] = np.bitwise_xor.reduce(прежний.reshape(-1))
            прежний = v
            vc3.append(v)
        поток = np.concatenate([v.reshape(-1) for v in vc3])
        g = np.zeros(кадров * 765, dtype=np.uint8)
        g[2 * 85 + p:] = поток[:len(g) - 2 * 85 - p]
        область = g.reshape(кадров, 9, 85)
        vc4 = []
        for f in range(кадров):
            v = np.zeros((9, 261), dtype=np.uint8)
            столбцы = [3 + 1 + 3 * i for i in range(86)]
            tug3 = np.zeros((9, 86), dtype=np.uint8)
            tug3[0, 0], tug3[1, 0] = 0x68 | (p >> 8), p & 0xFF
            tug3[:, 1:] = область[f]
            v[:, столбцы] = tug3
            v[2, 0] = 0x02
            vc4.append(v)
        for f in range(1, кадров):
            vc4[f][1, 0] = np.bitwise_xor.reduce(vc4[f - 1].reshape(-1))
        н = sdh.найти(stm([vc4], [17]))
        self.assertIsNotNone(н)
        ряд = н.дальше["VC-4 №1 TU-3 2 нагрузка"]
        ждём = np.unpackbits(нагрузка.reshape(-1))
        self.assertTrue(np.array_equal(ждём[:len(ряд)], ряд))
        self.assertIn("TU-3 2: указатель 300, B3 VC-3 сошёлся в 100 %", " ".join(н.подробно))

    def test_без_скремблера_и_шум(self):
        н = sdh.найти(stm([vc4_кадры(20, {}, np.zeros((20, 9, 260), np.uint8))], [3], скремблер=False))
        self.assertIn("кадр не скремблирован", н.подробно[1])
        шумный = self.поток ^ (np.random.default_rng(1).random(len(self.поток)) < 2e-6).astype(np.uint8)
        н = sdh.найти(шумный)
        self.assertIsNotNone(н)
        self.assertGreater(н.уверенность, 0.8)

    def test_случайный_и_указатели(self):
        self.assertIsNone(sdh.найти(с.случайные_биты(2_000_000)))
        # A1 A2 на месте, а кадры — случайные: B1 не сходится — не SDH.
        rng = np.random.default_rng(2)
        кадры = rng.integers(0, 256, (30, 2430)).astype(np.uint8)
        кадры[:, :6] = [0xF6, 0xF6, 0xF6, 0x28, 0x28, 0x28]
        self.assertIsNone(sdh.найти(np.unpackbits(кадры.reshape(-1))))
        # Слово через не кратное кадру расстояние — не кадр SDH.
        кадры = rng.integers(0, 2, (30, 19448)).astype(np.uint8)
        кадры[:, :48] = np.unpackbits(np.array([0xF6] * 3 + [0x28] * 3, np.uint8))
        self.assertIsNone(sdh.выравнивание(кадры.reshape(-1)))
        self.assertEqual(("норма", 522), sdh.указатель(0x6A, 0x0A))
        self.assertEqual(("сцепка", -1), sdh.указатель(0x9B, 0xFF))
        self.assertEqual(("AIS", -1), sdh.указатель(0xFF, 0xFF))
        self.assertEqual("ошибка", sdh.указатель(0x00, 0x00)[0])


if __name__ == "__main__":
    unittest.main()


class МеткиТрассаTests(unittest.TestCase):
    def test_c2_словами(self):
        self.assertEqual(sdh.метка_c2(0x1B), "0x1B (GFP (G.7041))")
        self.assertEqual(sdh.метка_c2(0x16), "0x16 (HDLC/PPP (RFC 2615: со скремблером x⁴³ + 1))")
        for код in (0xE1, 0xEE, 0xFC):
            self.assertEqual(sdh.метка_c2(код), f"0x{код:02X} (для национального использования)")
        for код in (0xE0, 0xFD, 0x77):
            self.assertEqual(sdh.метка_c2(код), f"0x{код:02X} (не определена)")
        self.assertEqual(len(sdh.C2_МЕТКИ), 19)
        self.assertEqual(len(sdh.V5_МЕТКИ), 8)

    def test_crc7_по_вектору_rfc3637(self):
        """RFC 3637: неиспользуемая трасса — 0x89 и 15 нулей; оба многочлена дают этот вектор."""
        for многочлен in sdh.CRC7_МНОГОЧЛЕНЫ:
            self.assertEqual(sdh.crc7([0x80] + [0] * 15, многочлен), 0x09)
        кусок = [0x80] + list(b"NODE-A/PORT-7   "[:15])
        кусок[0] |= crc7_трассы(кусок)
        self.assertEqual(sdh.crc7([0x80] + кусок[1:], 0x09), кусок[0] & 0x7F)

    def test_трасса_с_crc7_и_без(self):
        кусок = [0x80] + list(b"NODE-A/PORT-7".ljust(15, b" "))
        кусок[0] |= crc7_трассы(кусок)
        сдвинутый = np.array((кусок[5:] + кусок[:5]) * 3, dtype=np.uint8)       # с середины трассы
        self.assertEqual(sdh._трасса(сдвинутый), "16 байт: «NODE-A/PORT-7», CRC-7 (x⁷ + x³ + 1) сошлась")
        испорчен = list(кусок)
        испорчен[3] ^= 1
        self.assertEqual(sdh._трасса(np.array(испорчен * 3, dtype=np.uint8)),
                         "16 байт: «NOEE-A/PORT-7», CRC-7 не сошлась")
        self.assertEqual(sdh._трасса(np.array(([0x89] + [0] * 15) * 3, dtype=np.uint8)),
                         "16 байт: «», CRC-7 (x⁷ + x³ + 1) сошлась")


class ТрассаГраницыTests(unittest.TestCase):
    def test_метка_v5(self):
        self.assertEqual([sdh.метка_v5(v) for v in (0b00000010, 0b00001110, 0b11110001, 0b00000110)], [1, 7, 0, 3])

    def test_crc7_как_остаток_деления(self):
        """CRC-7 = M(x)·x⁷ mod P(x) — считается здесь делением столбиком, независимо от разбора."""
        def остаток(байты, многочлен):
            m = int.from_bytes(bytes(байты), "big") << 7
            p = 0x80 | многочлен
            for сдвиг in range(m.bit_length() - 8, -1, -1):
                if m >> (сдвиг + 7) & 1:
                    m ^= p << сдвиг
            return m
        for многочлен in sdh.CRC7_МНОГОЧЛЕНЫ:
            for байты in ([0x01], [0x40], [0x80], [0xFF, 0x00, 0x5A], list(b"SDH")):
                self.assertEqual(sdh.crc7(байты, многочлен), остаток(байты, многочлен), (hex(многочлен), байты))

    def test_трасса_границы(self):
        т = sdh._трасса
        # Ровно две трассы по 16 байт — трасса; за ними чужое — всё равно трасса по первым двум.
        кусок = [0x80] + list(b"ABCDEFGHIJKLMNO")
        кусок[0] |= crc7_трассы(кусок)
        self.assertTrue(т(np.array(кусок * 2, np.uint8)).startswith("16 байт: «ABCDEFGHIJKLMNO»"))
        self.assertTrue(т(np.array(кусок * 2 + list(range(16)), np.uint8)).startswith("16 байт: «ABCDEFGHIJKLMNO»"))
        self.assertTrue(т(np.array(кусок + кусок[:15], np.uint8)).startswith("переменная"))
        # Старший бит — у последнего байта окна; без старшего бита — трасса с первого байта.
        сдвинутый = кусок[1:] + кусок[:1]
        self.assertTrue(т(np.array(сдвинутый * 2, np.uint8)).startswith("16 байт: «ABCDEFGHIJKLMNO»"))
        без_msb = list(b"PQRSTUVWXYZabcde")
        self.assertEqual(т(np.array(без_msb * 2, np.uint8)), "16 байт: «QRSTUVWXYZabcde», CRC-7 не сошлась")
        # Печатное: пробел и тильда — знаки, 0x1F и 0x7F — точки.
        кусок = [0x80] + [0x41, 0x20, 0x7E, 0x1F, 0x7F] + [0x42] * 10
        self.assertTrue(т(np.array(кусок * 2, np.uint8)).startswith("16 байт: «A ~··BBBBBBBBBB»"))
        # 64 байта с CR LF: нужно ровно 128 байт.
        длинная = list(b"TRACE-64".ljust(62, b"-")) + [13, 10]
        self.assertEqual(т(np.array(длинная * 2, np.uint8)), "64 байт: «TRACE-64" + "-" * 54 + "»")
        self.assertTrue(т(np.array(длинная * 2, np.uint8)[:127]).startswith("переменная"))
        # Один байт — по первым 128; 129-й уже не смотрится.
        self.assertEqual(т(np.array([0x41] * 128 + [0x42], np.uint8)), "один байт 0x41")
        # Переменная: первые 8 байт.
        self.assertEqual(т(np.arange(40, dtype=np.uint8) * 7), "переменная (00 07 0E 15 1C 23 2A 31 …)")
