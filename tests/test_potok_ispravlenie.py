# -*- coding: utf-8 -*-
"""Исправление ошибок блочных кодов: Рида — Соломона, БЧХ (алгебраически), линейные (по синдрому).

Коды собираются здесь же, независимо от анализатора: поле GF(2^m) — сдвигами с
приведением по многочлену, порождающий многочлен РС — произведение (x − β^j),
БЧХ — произведение минимальных многочленов корней (по циклотомическим классам),
кодер — систематический, делением «в столбик».
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import rs_bch


# -- поле и коды — независимо от анализатора -------------------------------------------------

class GF:
    def __init__(self, p):
        self.m = p.bit_length() - 1
        self.q = 1 << self.m
        self.exp = [0] * (2 * self.q)
        self.log = [0] * self.q
        x = 1
        for i in range(self.q - 1):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & self.q:
                x ^= p
        for i in range(self.q - 1, 2 * self.q):
            self.exp[i] = self.exp[i - (self.q - 1)]

    def mul(self, a, b):
        return 0 if a == 0 or b == 0 else self.exp[self.log[a] + self.log[b]]

    def pow_a(self, k):
        return self.exp[k % (self.q - 1)]


def умножить_многочлены(gf, a, b):
    """Коэффициенты от старшей степени."""
    итог = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            итог[i + j] ^= gf.mul(x, y)
    return итог


def порождающий_рс(gf, fcr, корней, шаг=1):
    g = [1]
    for j in range(корней):
        g = умножить_многочлены(gf, g, [1, gf.pow_a(шаг * (fcr + j))])
    return g


def кодировать(gf, g, данные, n):
    """Систематический кодер: данные (k символов) + остаток от деления x^(n−k)·d(x) на g(x)."""
    k = n - (len(g) - 1)
    регистр = list(данные) + [0] * (len(g) - 1)
    for i in range(k):
        c = регистр[i]
        if c:
            for j in range(1, len(g)):
                регистр[i + j] ^= gf.mul(c, g[j])
    return list(данные) + регистр[k:]


def порождающий_бчх(gf, fcr, корней):
    """Двоичный порождающий многочлен: НОК минимальных многочленов α^fcr … α^(fcr+корней−1)."""
    Q = gf.q - 1
    степени = set()
    for j in range(fcr, fcr + корней):
        s = j % Q
        while s not in степени:
            степени.add(s)
            s = (2 * s) % Q
    g = [1]
    for s in sorted(степени):
        g = умножить_многочлены(gf, g, [1, gf.pow_a(s)])
    assert all(c in (0, 1) for c in g), "не двоичный"
    return g


def случайные_ошибки(rng, слова, сколько, символ_бит):
    """В каждом слове — ровно ``сколько`` ошибок в случайных местах, значение ≠ 0."""
    испорчено = слова.copy()
    for w in range(len(слова)):
        места = rng.choice(слова.shape[1], сколько, replace=False)
        for м in места:
            испорчено[w, м] ^= int(rng.integers(1, 1 << символ_бит))
    return испорчено


class РидСоломон(unittest.TestCase):
    def проверить(self, p, n, k, fcr, шаг=1, слов=60, сид=1):
        gf = GF(p)
        g = порождающий_рс(gf, fcr, n - k, шаг)
        rng = np.random.default_rng(сид)
        данные = rng.integers(0, gf.q, (слов, k))
        слова = np.array([кодировать(gf, g, d, n) for d in данные])
        self.assertFalse(rs_bch.синдромы(слова, p, fcr, n - k, шаг).any(), "кодер и синдромы")
        t = (n - k) // 2
        for ошибок in range(0, t + 1):
            with self.subTest(n=n, k=k, fcr=fcr, шаг=шаг, ошибок=ошибок):
                испорчено = случайные_ошибки(rng, слова, ошибок, gf.m)
                исправленные, исправлено = rs_bch.исправить(испорчено, p, fcr, n - k, шаг)
                self.assertTrue(np.array_equal(исправленные, слова))
                self.assertEqual(исправлено.tolist(), [ошибок] * слов)

    def test_полные_и_укороченные(self):
        self.проверить(0x11D, 255, 239, 0)          # как OTN и DVB (полный)
        self.проверить(0x11D, 204, 188, 0)          # DVB — укороченный
        self.проверить(0x187, 255, 223, 112, шаг=11, слов=20)   # корни — степени α^11 (как CCSDS)
        self.проверить(0x11D, 64, 56, 1)
        self.проверить(0x13, 15, 9, 1)              # GF(16)
        self.проверить(0x25, 31, 25, 3)             # GF(32), fcr = 3

    def test_больше_t_ошибок_не_портит(self):
        """t + 1 ошибок: слово либо признано неисправимым и оставлено как есть, либо (редко)
        исправлено в другое кодовое слово — но синдромы результата нулевые."""
        p, n, k, fcr = 0x11D, 255, 239, 0
        gf = GF(p)
        g = порождающий_рс(gf, fcr, n - k)
        rng = np.random.default_rng(5)
        слова = np.array([кодировать(gf, g, d, n) for d in rng.integers(0, 256, (40, k))])
        испорчено = случайные_ошибки(rng, слова, 9, 8)
        исправленные, исправлено = rs_bch.исправить(испорчено, p, fcr, n - k)
        for w in range(40):
            if исправлено[w] == -1:
                self.assertTrue(np.array_equal(исправленные[w], испорчено[w]))
            else:
                self.assertFalse(rs_bch.синдромы(исправленные[w:w + 1], p, fcr, n - k).any())
        self.assertGreaterEqual(int((исправлено == -1).sum()), 38)

    def test_берлекэмп_мэсси_одна_ошибка(self):
        """Одна ошибка значения Y в месте с локатором X: S_j = Y·X^(fcr+j), Λ = 1 + X·x."""
        p = 0x11D
        gf = GF(p)
        X, Y = gf.pow_a(17), 0x5A
        S = [gf.mul(Y, gf.pow_a(17 * j)) for j in range(4)]
        Λ, L = rs_bch.берлекэмп_мэсси_поле(S, p)
        self.assertEqual((Λ, L), ([1, X], 1))


class БЧХ(unittest.TestCase):
    def test_двоичные_коды(self):
        for p, n, fcr, корней, сколько_бит in ((0x13, 15, 1, 4, 7), (0x25, 31, 1, 6, 16), (0x43, 63, 1, 8, 45),
                                               (0x43, 50, 1, 8, 45)):
            gf = GF(p)
            g = порождающий_бчх(gf, fcr, корней)
            k = n - (len(g) - 1)
            rng = np.random.default_rng(n)
            данные = rng.integers(0, 2, (50, k))
            слова = np.array([кодировать(gf, g, d, n) for d in данные])
            t = корней // 2
            for ошибок in range(0, t + 1):
                with self.subTest(n=n, k=k, ошибок=ошибок):
                    испорчено = слова.copy()
                    for w in range(50):
                        испорчено[w, rng.choice(n, ошибок, replace=False)] ^= 1
                    исправленные, исправлено = rs_bch.исправить(испорчено, p, fcr, корней, двоичный=True)
                    self.assertTrue(np.array_equal(исправленные, слова))
                    self.assertEqual(исправлено.tolist(), [ошибок] * 50)


if __name__ == "__main__":
    unittest.main()


# -- линейные коды по синдрому ----------------------------------------------------------------

def проверочная_хэмминга(m):
    """H кода Хэмминга (2^m − 1, 2^m − 1 − m): столбец j — двоичная запись j + 1."""
    n = (1 << m) - 1
    return np.array([[(j + 1) >> i & 1 for j in range(n)] for i in range(m)], np.uint8)


def проверочная_циклического(g, n):
    """H циклического кода по порождающему g (двоичный, коэффициенты от старшей): ядро G."""
    k = n - (len(g) - 1)
    G = np.zeros((k, n), np.uint8)
    for i in range(k):
        G[i, i:i + len(g)] = g
    # H — базис ядра G над GF(2): решаем G·hᵀ = 0 перебором ступенчатого вида.
    from reportgen.potok import gf2
    ред, опорные = gf2.привести(gf2.упаковать(G), n)
    R = gf2.распаковать(ред, n)[:len(опорные)]
    свободные = [j for j in range(n) if j not in опорные]
    H = []
    for f in свободные:
        h = np.zeros(n, np.uint8)
        h[f] = 1
        for строка, p in zip(R, опорные):
            h[p] = строка[f]
        H.append(h)
    H = np.array(H, np.uint8)
    assert not ((G.astype(int) @ H.T) % 2).any()
    return G, H


class Синдромный(unittest.TestCase):
    def test_хэмминг_и_голей(self):
        from reportgen.potok import ispravlenie
        голей = [1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1]   # x¹¹ + x¹⁰ + x⁶ + x⁵ + x⁴ + x² + 1
        случаи = [("Хэмминг (7, 4)", None, проверочная_хэмминга(3), 1),
                  ("Хэмминг (15, 11)", None, проверочная_хэмминга(4), 1)]
        G, H = проверочная_циклического(голей, 23)
        случаи.append(("Голей (23, 12)", G, H, 3))
        for имя, G, H, t in случаи:
            with self.subTest(код=имя):
                т = ispravlenie.таблица(H)
                self.assertEqual(т.t, t)
                n = H.shape[1]
                if G is None:                                      # слова — ядро H
                    from reportgen.potok import gf2
                    ред, опорные = gf2.привести(gf2.упаковать(H), n)
                    R = gf2.распаковать(ред, n)[:len(опорные)]
                    свободные = [j for j in range(n) if j not in опорные]
                    G = []
                    for f in свободные:
                        c = np.zeros(n, np.uint8)
                        c[f] = 1
                        for строка, p in zip(R, опорные):
                            c[p] = строка[f]
                        G.append(c)
                    G = np.array(G, np.uint8)
                rng = np.random.default_rng(n)
                данные = rng.integers(0, 2, (200, len(G)))
                слова = (данные @ G % 2).astype(np.uint8)
                for ошибок in range(t + 1):
                    испорчено = слова.copy()
                    for w in range(200):
                        испорчено[w, rng.choice(n, ошибок, replace=False)] ^= 1
                    исправленные, исправлено, _ = ispravlenie.исправить(испорчено, H, т)
                    self.assertTrue(np.array_equal(исправленные, слова), (имя, ошибок))
                    self.assertEqual(set(исправлено.tolist()), {ошибок})

    def test_расширенный_хэмминг_обнаруживает_две(self):
        """(8, 4): t = 1, а две ошибки дают синдром, общий у нескольких пар, — не исправляются."""
        from reportgen.potok import ispravlenie
        H = np.vstack([np.hstack([проверочная_хэмминга(3), np.zeros((3, 1), np.uint8)]),
                       np.ones((1, 8), np.uint8)])
        т = ispravlenie.таблица(H)
        self.assertEqual(т.t, 1)
        слово = np.zeros((1, 8), np.uint8)
        слово[0, [1, 5]] = 1
        _, исправлено, _ = ispravlenie.исправить(слово, H, т)
        self.assertEqual(исправлено.tolist(), [-1])

    def test_много_проверок_без_таблицы(self):
        from reportgen.potok import ispravlenie
        self.assertIsNone(ispravlenie.таблица(np.eye(70, 100, dtype=np.uint8)))


# -- вслепую: опознание и исправление -----------------------------------------------------------

def систематический_двоичный(g, данные):
    """Слова двоичного циклического кода: данные, затем остаток x^(n−k)·d(x) mod g(x)."""
    g = np.array(g, np.uint8)
    r = len(g) - 1
    слов, k = данные.shape
    регистр = np.concatenate([данные, np.zeros((слов, r), np.uint8)], axis=1)
    for i in range(k):
        есть = регистр[:, i] == 1
        регистр[np.ix_(есть, np.arange(i, i + r + 1))] ^= g
    return np.concatenate([данные, регистр[:, k:]], axis=1).astype(np.uint8)


class Вслепую(unittest.TestCase):
    """Код опознаётся по потоку, и его ошибки исправляются — данные выходят чистыми."""

    def test_голей_опознан_и_исправлен(self):
        from reportgen.potok import kod
        голей = [1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1]
        rng = np.random.default_rng(31)
        данные = rng.integers(0, 2, (6000, 12)).astype(np.uint8)
        поток = систематический_двоичный(голей, данные).reshape(-1)
        испорчено = поток ^ (rng.random(len(поток)) < 1e-3).astype(np.uint8)
        найдено = kod.найти(испорчено)
        self.assertIsNotNone(найдено)
        self.assertIn("(23, 12)", найдено.что)
        self.assertNotIn("(16, 13)", найдено.что)              # не ложный короткий по выравниванию
        с = найдено.свойства
        self.assertEqual(с["t"], 3)
        self.assertEqual(с["исправлено"], int((испорчено != поток).sum()))
        self.assertTrue(np.array_equal(найдено.дальше[:len(данные) * 12], данные.reshape(-1)))

    def test_бчх_31_21_опознан_и_исправлен(self):
        from reportgen.potok import dlinnye
        gf = GF(0b100101)
        self.assertEqual(len(set(gf.exp[:31])), 31)             # многочлен поля примитивный
        g = порождающий_бчх(gf, 1, 4)
        self.assertEqual(len(g) - 1, 10)
        rng = np.random.default_rng(32)
        данные = rng.integers(0, 2, (3000, 21)).astype(np.uint8)
        поток = систематический_двоичный(g, данные).reshape(-1)
        испорчено = поток ^ (rng.random(len(поток)) < 5e-4).astype(np.uint8)
        найдено = dlinnye.найти(испорчено, до=512)
        self.assertIsNotNone(найдено)
        self.assertIn("(31, 21)", найдено.что)
        self.assertEqual(найдено.свойства["неисправимых"], 0)
        self.assertEqual(найдено.свойства["исправлено"], int((испорчено != поток).sum()))
        self.assertTrue(np.array_equal(найдено.дальше[:len(данные) * 21], данные.reshape(-1)))

    def test_dvb_исправление(self):
        """RS (204, 188) DVB, кодер свой: ошибки до 8 байт исправляются, 12 — неисправимы."""
        from reportgen.potok import dvb
        gf = GF(0x11D)
        g = порождающий_рс(gf, 0, 16)
        rng = np.random.default_rng(33)
        пакеты = rng.integers(0, 256, (300, 188))
        пакеты[:, 0] = 0x47
        слова = np.array([кодировать(gf, g, list(п), 204) for п in пакеты], np.uint8)
        испорчено = слова.copy()
        исправимых, байт = [], 0
        for w in range(0, 300, 5):
            ошибок = 12 if w % 50 == 0 else 1 + w % 8
            места = rng.choice(204, ошибок, replace=False)
            испорчено[w, места] ^= rng.integers(1, 256, ошибок).astype(np.uint8)
            if ошибок <= 8:
                исправимых.append(w)
                байт += ошибок
        найдено = dvb.найти(испорчено.tobytes(), 0)
        self.assertIsNotNone(найдено)
        текст = "\n".join(найдено.подробно)
        self.assertIn(f"исправлено {байт} байт в {len(исправимых)} пакетах из 300", текст)
        self.assertIn("неисправимых (ошибка обнаружена, но не исправлена) — 6", текст)
        чистые = [w for w in range(300) if w % 50]
        self.assertTrue(np.array_equal(найдено.дальше[чистые], пакеты[чистые].astype(np.uint8)))

    def test_dvb_без_ошибок(self):
        from reportgen.potok import dvb
        gf = GF(0x11D)
        g = порождающий_рс(gf, 0, 16)
        пакеты = np.random.default_rng(34).integers(0, 256, (40, 188))
        пакеты[:, 0] = 0x47
        слова = np.array([кодировать(gf, g, list(п), 204) for п in пакеты], np.uint8)
        найдено = dvb.найти(слова.tobytes(), 0)
        self.assertIn("ошибок нет: синдромы нулевые у всех 40 пакетов — исправлять нечего",
                      "\n".join(найдено.подробно))


# -- код РС в блоке: чередование, двойной базис ------------------------------------------------

# Двойной базис Берлекэмпа (CCSDS): по libfec (Ph. Karn, gen_ccsds_tal.c) — бит k входа
# добавляет tal[7 − k]. Здесь — своя копия описания, таблица строится заново.
TAL = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)


def в_двойной(x):
    v = 0
    for k in range(8):
        if x >> k & 1:
            v ^= TAL[7 - k]
    return v


def блоки_рс(p, n, k, fcr, шаг, I, блоков, *, двойной=False, ошибок=0, доля=1.0, сид=1):
    """Блоки по I·n байт: I слов РС, перемешанных по байтам; данные (блоков × I·k) и блоки.

    ``ошибок`` байт портится в каждом слове с вероятностью ``доля``.
    """
    gf = GF(p)
    g = порождающий_рс(gf, fcr, n - k, шаг)
    rng = np.random.default_rng(сид)
    данные = rng.integers(0, 256, (блоков, I * k))
    итог = np.zeros((блоков, I * n), np.int64)
    обратно = {в_двойной(x): x for x in range(256)}
    for b in range(блоков):
        # данные блока — в порядке передачи: байт j·I + i — i-го слова
        по_словам = данные[b].reshape(k, I).T
        слова = []
        for i in range(I):
            d = по_словам[i].tolist()
            if двойной:                                     # данные передаются в двойном базисе
                d = [обратно[x] for x in d]
            c = кодировать(gf, g, d, n)
            if двойной:
                c = [в_двойной(x) for x in c]
            слова.append(c)
        итог[b] = np.array(слова).T.reshape(-1)
    for b in range(блоков):
        for i in range(I):
            if rng.random() >= доля:
                continue
            места = rng.choice(n, ошибок, replace=False)
            for м in места:
                итог[b, м * I + i] ^= int(rng.integers(1, 256))
    return данные, итог


class РСвБлоке(unittest.TestCase):
    def test_ccsds_двойной_базис_чередование_4(self):
        """(255, 223) CCSDS: поле 0x187, корни β^112…β^143 при β = α^11, двойной базис, I = 4;
        в трети слов по 16 ошибок (предел) — все исправляются. Корни видны по словам без
        ошибок: где испорчены все слова, код не опознать."""
        from reportgen.potok import dlinnye
        данные, блоки = блоки_рс(0x187, 255, 223, 112, 11, 4, 12, двойной=True, ошибок=16, доля=1 / 3,
                                 сид=2)
        биты = np.unpackbits(блоки.astype(np.uint8).reshape(-1))
        найдено = dlinnye.рс_в_блоке(биты, 4 * 255 * 8)
        self.assertIsNotNone(найдено)
        с = найдено.свойства
        self.assertEqual((с["N"], с["k"], с["глубина"], с["базис"]), (255, 223, 4, "двойной"))
        self.assertEqual((с["многочлен"], с["fcr"], с["шаг"]), (0x187, 112, 11))
        self.assertEqual(с["исправлено"] % 16, 0)
        self.assertGreater(с["исправлено"], 0)
        self.assertEqual(с["неисправимых"], 0)
        выход = np.packbits(найдено.дальше).reshape(12, -1)
        self.assertTrue(np.array_equal(выход, данные.astype(np.uint8)))
        self.assertIn("двойной базис (как у CCSDS)", найдено.что)

    def test_обычный_базис_без_чередования(self):
        from reportgen.potok import dlinnye
        данные, блоки = блоки_рс(0x11D, 204, 188, 0, 1, 1, 30, ошибок=3, доля=0.7, сид=3)
        биты = np.unpackbits(блоки.astype(np.uint8).reshape(-1))
        найдено = dlinnye.рс_в_блоке(биты, 204 * 8)
        с = найдено.свойства
        self.assertEqual((с["N"], с["k"], с["глубина"], с["базис"]), (204, 188, 1, "обычный"))
        self.assertTrue(np.array_equal(np.packbits(найдено.дальше).reshape(30, -1), данные.astype(np.uint8)))

    def test_случайные_блоки_не_рс(self):
        from reportgen.potok import dlinnye
        шум = np.random.default_rng(4).integers(0, 2, 1020 * 8 * 12).astype(np.uint8)
        self.assertIsNone(dlinnye.рс_в_блоке(шум, 1020 * 8))

    def test_двойной_базис_таблицы(self):
        from reportgen.potok import dlinnye
        self.assertEqual([int(dlinnye.В_ДВОЙНОЙ[x]) for x in range(256)], [в_двойной(x) for x in range(256)])
        self.assertTrue(np.array_equal(dlinnye.ИЗ_ДВОЙНОГО[dlinnye.В_ДВОЙНОЙ], np.arange(256)))


# -- сквозной разбор: кадры CCSDS (ASM + блок РС) ---------------------------------------------

ASM = bytes.fromhex("1ACFFC1D")


def кадры_ccsds(блоков=160, *, ошибок=4, доля=0.3, сид=5):
    """HDLC с IP → блоки по 4·223 байт → РС (255, 223) CCSDS, I = 4, двойной базис → ASM + блок."""
    import potok_sintez as с
    поток = с.hdlc(с.пакеты_ip(блоков * 4, сид=сид), флагов_между=40)
    нужно = блоков * 4 * 223
    поток = (поток * (нужно // len(поток) + 1))[:нужно]
    данные = np.frombuffer(поток, np.uint8).reshape(блоков, 4 * 223)
    gf = GF(0x187)
    g = порождающий_рс(gf, 112, 32, 11)
    обратно = {в_двойной(x): x for x in range(256)}
    rng = np.random.default_rng(сид)
    кадры = []
    for b in range(блоков):
        слова = []
        for i in range(4):
            d = [обратно[int(x)] for x in данные[b].reshape(223, 4)[:, i]]
            c = [в_двойной(x) for x in кодировать(gf, g, d, 255)]
            if rng.random() < доля:
                for м in rng.choice(255, ошибок, replace=False):
                    c[м] ^= int(rng.integers(1, 256))
            слова.append(c)
        кадры.append(ASM + bytes(np.array(слова).T.reshape(-1).tolist()))
    return b"".join(кадры), поток


def ccsds_псп(бит):
    """Рандомизатор CCSDS так, как его делает GNU Radio (gr-satellites, ccsds_descrambler:
    additive_scrambler_bb(0xA9, 0xFF, 7), сброс на каждом кадре; lfsr.h): регистр Фибоначчи —
    выход — младший бит, новый бит — чётность (регистр & 0xA9), вдвигается в разряд 7."""
    r, выход = 0xFF, np.zeros(бит, np.uint8)
    for i in range(бит):
        выход[i] = r & 1
        r = (r >> 1) | ((bin(r & 0xA9).count("1") & 1) << 7)
    return выход


def ccsds_полный(блоков=64, *, ошибок=4e-3, сид=6):
    """Вся цепочка CCSDS TM: HDLC с IP → РС (255, 223), I = 4, двойной базис → рандомизатор
    (сброс на каждом кадре) → ASM + блок → свёрточный K = 7, 171/133, вторая ветвь
    инвертирована → ошибки канала (жёсткие решения) с вероятностью ``ошибок``."""
    import potok_sintez as с
    поток = с.hdlc(с.пакеты_ip(блоков * 4, сид=сид), флагов_между=40)
    нужно = блоков * 4 * 223
    поток = (поток * (нужно // len(поток) + 1))[:нужно]
    данные = np.frombuffer(поток, np.uint8).reshape(блоков, 4 * 223)
    gf = GF(0x187)
    g = порождающий_рс(gf, 112, 32, 11)
    обратно = {в_двойной(x): x for x in range(256)}
    псп = ccsds_псп(8160)
    asm = np.unpackbits(np.frombuffer(ASM, np.uint8))
    кадры = []
    for b in range(блоков):
        слова = [[в_двойной(x) for x in кодировать(gf, g, [обратно[int(y)] for y in данные[b].reshape(223, 4)[:, i]], 255)]
                 for i in range(4)]
        блок = np.unpackbits(np.array(слова, np.uint8).T.reshape(-1)) ^ псп
        кадры.append(np.concatenate([asm, блок]))
    закодировано = с.свёрточный(np.concatenate(кадры))
    закодировано[1::2] ^= 1
    rng = np.random.default_rng(сид)
    return закодировано ^ (rng.random(len(закодировано)) < ошибок).astype(np.uint8), поток


class ИтогИсправления(unittest.TestCase):
    def test_словами(self):
        итог, доля = rs_bch.итог_словами(np.array([0, 2, 0, 1, -1]), "байт", "байт", 1000)
        self.assertEqual(итог, "исправлено 3 байт в 2 словах из 5; неисправимых (ошибка обнаружена, "
                               "но не исправлена) — 1; ошибок в линии до декодирования ≈ 3.0e-03 на "
                               "байт (по исправленным — оценка снизу)")
        self.assertAlmostEqual(доля, 3e-3)

    def test_без_ошибок(self):
        итог, доля = rs_bch.итог_словами(np.zeros(7, int), "бит", "бит", 70, в_чём="пакетах", чего="пакетов")
        self.assertEqual(итог, "ошибок нет: синдромы нулевые у всех 7 пакетов — исправлять нечего")
        self.assertEqual(доля, 0.0)

    def test_только_неисправимые(self):
        """Исправленных нет, но неисправимые есть — это не «ошибок нет»."""
        итог, _ = rs_bch.итог_словами(np.array([0, -1, 0]), "байт", "байт", 30)
        self.assertIn("исправлено 0 байт в 0 словах из 3", итог)
        self.assertIn("— 1;", итог)


class РандомизаторCcsds(unittest.TestCase):
    def test_назван_у_псп_блока(self):
        """Блоки HDLC с флагами, сложенные с ПСП CCSDS (сброс на каждом блоке): найдена та самая ПСП
        и названа; начальное состояние — единицы."""
        from reportgen.potok import skrembler
        import potok_sintez as с
        поток = np.unpackbits(np.frombuffer(с.hdlc(с.пакеты_ip(400, сид=3), флагов_между=40), np.uint8))
        блоков = len(поток) // 8160
        блоки = поток[:блоков * 8160].reshape(блоков, 8160) ^ ccsds_псп(8160)
        найдено = skrembler.по_блоку(блоки.reshape(-1), 8160)
        self.assertIsNotNone(найдено)
        self.assertIn("рандомизатор CCSDS (131.0-B)", найдено.что)
        self.assertEqual(найдено.свойства["начальное"], "11111111")
        # Та же ПСП, но с другим начальным состоянием — не CCSDS, и так не названа.
        иная = np.roll(ccsds_псп(8160 + 255), -7)[:8160]
        найдено = skrembler.по_блоку((поток[:блоков * 8160].reshape(блоков, 8160) ^ иная).reshape(-1), 8160)
        self.assertNotIn("CCSDS", найдено.что)

    def test_начало_последовательности(self):
        """По GNU Radio: первые байты — FF 48 0E C0 9A 0D 70 BC (так же начинается
        последовательность в CCSDS 131.0-B), период — 255."""
        псп = ccsds_псп(8 * 300)
        self.assertEqual(np.packbits(псп[:64]).tobytes().hex(), "ff480ec09a0d70bc")
        self.assertTrue(np.array_equal(псп[:255], псп[255:510]))
        self.assertFalse(any(np.array_equal(псп[:255], псп[p:p + 255]) for p in range(1, 255)))


class СквознойCcsds(unittest.TestCase):
    def test_кадры_asm_и_рс_в_двойном_базисе(self):
        from reportgen.potok import razbor
        данные, исходные = кадры_ccsds()
        р = razbor.разобрать(данные=b"\x55" * 37 + данные, имя="ccsds.bin", профиль="обычно")
        отчёт = razbor.собрать_отчёт(р, 20000)
        уровни = [н.уровень for н in р.находки]
        self.assertIn("код", уровни, отчёт)
        код = [н for н in р.находки if н.уровень == "код"][0]
        self.assertIn("(255, 223)", код.что, отчёт)
        self.assertIn("двойной базис", код.что)
        self.assertEqual(код.свойства["неисправимых"], 0)
        кадры = [н for н in р.находки if н.уровень == "канальный"]
        self.assertTrue(кадры and "FCS" in кадры[0].что, отчёт)
        self.assertGreaterEqual(кадры[0].уверенность, 0.95, отчёт)


class ПолнаяЦепочкаCcsds(unittest.TestCase):
    def test_свёрточный_asm_рандомизатор_рс_hdlc(self):
        """Свёрточный код с инвертированной ветвью → ASM → рандомизатор блока → РС в двойном
        базисе → HDLC с IP: всё снимается вслепую, остаточные ошибки Витерби исправляет РС."""
        from reportgen.potok import razbor
        биты, исходные = ccsds_полный()
        р = razbor.разобрать(данные=np.packbits(биты).tobytes(), имя="ccsds_tm.bin", профиль="обычно")
        отчёт = razbor.собрать_отчёт(р, 30000)
        уровни = [н.уровень for н in р.находки]
        коды = [н for н in р.находки if н.уровень == "код"]
        self.assertTrue(any("171" in н.что and "133" in н.что for н in коды), отчёт)
        self.assertTrue(any("CCSDS" in "\n".join(н.подробно) + н.что for н in коды), отчёт)
        self.assertIn("цикл", уровни, отчёт)
        self.assertIn("скремблер", уровни, отчёт)
        скремблер = [н for н in р.находки if н.уровень == "скремблер"][0]
        self.assertIn("рандомизатор CCSDS (131.0-B)", скремблер.что, отчёт)
        self.assertEqual(скремблер.свойства["начальное"], "11111111")
        рс = [н for н in коды if "(255, 223)" in н.что]
        self.assertTrue(рс, отчёт)
        self.assertEqual(рс[0].свойства["неисправимых"], 0, отчёт)
        кадры = [н for н in р.находки if н.уровень == "канальный"]
        self.assertTrue(кадры, отчёт)
        self.assertGreaterEqual(кадры[0].уверенность, 0.99, отчёт)

