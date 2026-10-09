"""Скремблер со сбросом на блоке, маска блока, цикл по гребёнке, Витерби с ограниченной памятью.

Как в образце Comtech 8PSK (TPC (64, 57) × (46, 39)): за декодером — блоки
по 2223 бит данных, каждый сложен с одной и той же ПСП (регистр
сбрасывается в начале блока), а в паузах между кадрами HDLC идут флаги
01111110 — их фаза в каждом блоке своя, и столбцы растра не постоянны.
ПСП и маски строятся здесь же, независимо от анализатора.
"""

import unittest
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import cikl, hdlc, kod, skrembler, svyortka

БЛОК = 2223
ОТВОДЫ = (2, 3, 9, 12)                        # 1 + x^-2 + x^-3 + x^-9 + x^-12
НАЧАЛЬНОЕ = "101000011100"


def лрп(отводы, начальное, длина):
    """s[n] = ⊕ s[n − t] — от заданных первых бит (независимо от анализатора)."""
    s = [int(x) for x in начальное]
    while len(s) < длина:
        v = 0
        for t in отводы:
            v ^= s[len(s) - t]
        s.append(v)
    return np.array(s[:длина], np.uint8)


def hdlc_с_паузами(кадров=80, флагов=160, сид=1):
    """Кадры HDLC с IP и длинными паузами из флагов — биты в порядке передачи."""
    байты = с.hdlc(с.пакеты_ip(кадров, сид=сид), флагов_между=флагов)
    return np.unpackbits(np.frombuffer(байты, np.uint8))


def по_блокам(биты, маска):
    """Сложить каждый блок (и хвост) с маской."""
    n = len(биты) // len(маска) * len(маска)
    итог = биты.copy()
    итог[:n] = (биты[:n].reshape(-1, len(маска)) ^ маска).reshape(-1)
    итог[n:] ^= маска[:len(биты) - n]
    return итог


class ПспБлока(unittest.TestCase):
    def test_полином_и_состояние_при_флагах(self):
        данные = hdlc_с_паузами()
        поток = по_блокам(данные, лрп(ОТВОДЫ, НАЧАЛЬНОЕ, БЛОК))
        найдено = skrembler.блоковая_псп(поток, БЛОК)
        self.assertIsNotNone(найдено)
        self.assertEqual(найдено.свойства["отводы"], list(ОТВОДЫ))
        self.assertEqual(найдено.свойства["начальное"], НАЧАЛЬНОЕ)
        self.assertIn("со сбросом в начале каждого блока 2223 бит", найдено.что)
        self.assertTrue(any("периодом 8 бит" in п for п in найдено.подробно))
        self.assertTrue(np.array_equal(найдено.дальше, данные))
        кадры = hdlc.найти(найдено.дальше)
        self.assertIsNotNone(кадры)
        self.assertGreaterEqual(кадры.уверенность, 0.99)

    def test_постоянное_заполнение_тоже(self):
        """Нули в паузах — множитель периода пустой, ПСП та же."""
        rng = np.random.default_rng(3)
        данные = np.zeros(БЛОК * 40, np.uint8)
        данные[::7 * БЛОК // 3] = 1
        данные[5 * БЛОК:6 * БЛОК] = rng.integers(0, 2, БЛОК)
        поток = по_блокам(данные, лрп((5, 23), "10110011100011110000101", БЛОК))
        найдено = skrembler.блоковая_псп(поток, БЛОК)
        self.assertIsNotNone(найдено)
        self.assertEqual(найдено.свойства["отводы"], [5, 23])
        self.assertTrue(np.array_equal(найдено.дальше, данные))

    def test_сплошная_псп_не_блоковая(self):
        """ПСП без сброса: состояния в начале блоков разные — это не ПСП блока."""
        данные = hdlc_с_паузами()
        поток = данные ^ лрп(ОТВОДЫ, НАЧАЛЬНОЕ, len(данные))
        self.assertIsNone(skrembler.блоковая_псп(поток, БЛОК))
        self.assertIsNone(skrembler.маска_блока(поток, БЛОК))
        self.assertIsNone(skrembler.по_блоку(поток, БЛОК))

    def test_шум(self):
        шум = np.random.default_rng(4).integers(0, 2, БЛОК * 120).astype(np.uint8)
        self.assertIsNone(skrembler.блоковая_псп(шум, БЛОК))
        self.assertIsNone(skrembler.маска_блока(шум, БЛОК))

    def test_короткий_поток(self):
        self.assertIsNone(skrembler.блоковая_псп(np.zeros(БЛОК * 5, np.uint8), БЛОК))
        self.assertIsNone(skrembler.маска_блока(np.zeros(БЛОК * 5, np.uint8), БЛОК))


class СбросЧерезБлоки(unittest.TestCase):
    """ПСП сбрасывается не на каждом блоке, а через m блоков (сверхблок) — или не сбрасывается."""

    def test_сброс_через_три_блока(self):
        """ПСП идёт непрерывно три блока и сбрасывается: состояния в начале блоков — три по
        кругу, каждое — сдвиг предыдущего на длину блока. Поток начат со второго блока
        сверхблока: фаза не нулевая."""
        данные = hdlc_с_паузами(кадров=120, сид=4)
        сверх = лрп(ОТВОДЫ, НАЧАЛЬНОЕ, 3 * БЛОК)
        поток = данные ^ np.resize(np.roll(сверх, -БЛОК), len(данные))
        найдено = skrembler.блоковая_псп(поток, БЛОК)
        self.assertIsNotNone(найдено)
        self.assertEqual(найдено.свойства["блоков_в_сбросе"], 3)
        self.assertEqual(найдено.свойства["отводы"], list(ОТВОДЫ))
        self.assertEqual(найдено.свойства["начальное"], НАЧАЛЬНОЕ)
        self.assertEqual(найдено.свойства["длина_блока"], БЛОК)
        self.assertIn("со сбросом через каждые 3 блока по 2223 бит", найдено.что)
        self.assertTrue(any("первый блок потока — 2-й блок сверхблока" in п for п in найдено.подробно))
        self.assertTrue(np.array_equal(найдено.дальше, данные))

    def test_сброс_на_каждом_блоке_не_сверхблок(self):
        """Сброс на каждом блоке — m = 1, хотя сверхблоки из 2, 3, … блоков тоже «сходятся»."""
        данные = hdlc_с_паузами(сид=6)
        найдено = skrembler.блоковая_псп(по_блокам(данные, лрп(ОТВОДЫ, НАЧАЛЬНОЕ, БЛОК)), БЛОК)
        self.assertEqual(найдено.свойства["блоков_в_сбросе"], 1)

    def test_разные_состояния_по_кругу_не_сброс(self):
        """Три независимых состояния по кругу — не сдвиги одной ПСП: сброса через m блоков
        нет, а одно состояние покрывает лишь треть блоков — это не ПСП блока."""
        данные = hdlc_с_паузами(сид=5)
        маски = [лрп(ОТВОДЫ, н, БЛОК) for н in (НАЧАЛЬНОЕ, "110010101111", "011100010011")]
        блоков = -(-len(данные) // БЛОК)
        маска = np.concatenate([маски[b % 3] for b in range(блоков)])[:len(данные)]
        self.assertIsNone(skrembler.блоковая_псп(данные ^ маска, БЛОК))

    def test_решение_по_состояниям_блоков(self):
        """Сброс решается по состояниям блоков — по одному на блок, сколько бы окон заполнения
        в нём ни было (у сплошной ПСП все окна блока тоже дают одно состояние): три блока с
        разными состояниями — не сброс, одно состояние в трёх блоках — сброс на блоке, в двух —
        ещё не доказан."""
        отводы = ОТВОДЫ
        s = [лрп(отводы, н, 12) for н in (НАЧАЛЬНОЕ, "110010101111", "011100010011")]
        по_окнам = {0: s[0].tobytes(), 1: s[1].tobytes(), 2: s[2].tobytes()}
        self.assertIsNone(skrembler._сброс(по_окнам, отводы, БЛОК))
        одно = {b: s[0].tobytes() for b in (0, 3, 7)}
        self.assertEqual(skrembler._сброс(одно, отводы, БЛОК)[:1], (1,))
        # Одно и то же состояние, но лишь в двух блоках — сброс не доказан.
        self.assertIsNone(skrembler._сброс({0: s[0].tobytes(), 5: s[0].tobytes()}, отводы, БЛОК))


class МаскаБлока(unittest.TestCase):
    def test_любая_маска_по_флагам(self):
        """Маска — не ЛРП (ПСП ⊕ инверсия бита метки): находится по блокам с флагами."""
        данные = hdlc_с_паузами(сид=2)
        маска = np.random.default_rng(5).integers(0, 2, БЛОК).astype(np.uint8)
        поток = по_блокам(данные, маска)
        self.assertIsNone(skrembler.блоковая_псп(поток, БЛОК))
        найдено = skrembler.маска_блока(поток, БЛОК)
        self.assertIsNotNone(найдено)
        self.assertIn("постоянная маска на каждом блоке 2223 бит", найдено.что)
        self.assertTrue(np.array_equal(найдено.дальше, данные))
        self.assertEqual(skrembler.по_блоку(поток, БЛОК).что, найдено.что)
        self.assertGreaterEqual(hdlc.найти(найдено.дальше).уверенность, 0.99)

    def test_повтор_содержимого_не_маска(self):
        """Блоки повторяют прежний в 90 % случаев: «маска» из копий одного блока — само содержимое, не скремблер."""
        г = np.random.default_rng(2)
        блоки, прежний = [], г.integers(0, 2, БЛОК, dtype=np.uint8)
        for _ in range(40):
            if г.random() < 0.1:
                прежний = г.integers(0, 2, БЛОК, dtype=np.uint8)
            блоки.append(прежний)
        self.assertIsNone(skrembler.маска_блока(np.concatenate(блоки), БЛОК))

    def test_маска_лрп_называется_полиномом(self):
        данные = hdlc_с_паузами(сид=3)
        поток = по_блокам(данные, лрп(ОТВОДЫ, НАЧАЛЬНОЕ, БЛОК))
        найдено = skrembler.маска_блока(поток, БЛОК)
        self.assertIn("аддитивная ПСП 1 + x^-2 + x^-3 + x^-9 + x^-12", найдено.что)
        self.assertEqual(найдено.свойства["отводы"], list(ОТВОДЫ))


class ПериодЗаполнения(unittest.TestCase):
    def test_флаги_отделяются(self):
        от = svyortka.умножить
        q = 1 | sum(1 << t for t in ОТВОДЫ)
        множитель = 1
        for _ in range(7):
            множитель = от(множитель, 0b11)
        self.assertEqual(skrembler.разделить_период(от(множитель, q)), (множитель, q))
        self.assertEqual(skrembler.период_заполнения(множитель), 8)
        self.assertEqual(skrembler.период_заполнения(1), 1)
        self.assertEqual(skrembler.период_заполнения(0b11), 1)
        self.assertEqual(skrembler.период_заполнения(0b111), 3)


class ЗаписьПолинома(unittest.TestCase):
    def test_характеристический_и_период(self):
        """Одна ПСП в двух записях: задержки 1 + x⁻³ + x⁻²⁰ (V.35) — это x²⁰ + x¹⁷ + 1."""
        self.assertEqual(skrembler.характеристический((3, 20)), "x^20 + x^17 + 1")
        self.assertEqual(skrembler.характеристический(ОТВОДЫ), "x^12 + x^10 + x^9 + x^3 + 1")
        self.assertEqual(skrembler.период_лрп(ОТВОДЫ), 4095)
        self.assertEqual(skrembler.период_лрп((14, 15)), 32767)
        self.assertEqual(skrembler.период_лрп((1,)), 1)
        self.assertEqual(skrembler.период_лрп((2, 4)), 6)           # (x² + x + 1)²: не примитивный
        self.assertIsNone(skrembler.период_лрп((18, 23)))           # выше предела степени — не считается
        s = лрп((2, 4), "1010", 40)
        self.assertTrue(all(s[i] == s[i + 6] for i in range(30)))


class ЦиклПоГребёнке(unittest.TestCase):
    def test_короткий_файл_с_чередованием(self):
        """300 кадров: каждый пик ниже порога, их сумма на кратных — выше."""
        from test_potok_tpc3 import comtech
        rng = np.random.default_rng(10)
        поток, _ = comtech(rng.integers(0, 2, (300, 39, 57)).astype(np.uint8), приставка=777,
                           ошибок=1e-3)
        self.assertIsNone(cikl.длина_цикла(поток))
        self.assertEqual(cikl.гребёнка(поток)[0], 5928)
        найдено = cikl.найти(поток)
        self.assertEqual(найдено.свойства["кадр"], 2964)
        self.assertTrue(найдено.свойства["чередование"])

    def test_кадры_с_краёв_при_чередовании(self):
        """Синхрослово чередуется: каждый целый кадр — в нарезке, с какой бы половины цикла поток ни начался и ни кончился.

        Раньше брались только целые циклы (два кадра): первая половина последнего цикла и кадр перед
        первым циклом терялись — тот же файл без сессии давал на блок ТКБ меньше, чем в сессии.
        """
        from test_potok_tpc3 import comtech
        rng = np.random.default_rng(12)
        поток, начала = comtech(rng.integers(0, 2, (101, 39, 57)).astype(np.uint8), приставка=500, ошибок=1e-4)
        for первый in (0, 1):                     # поток начинается с кадра A или с кадра B
            for последний in (99, 100):          # и кончается целым циклом или его первой половиной
                with self.subTest(первый=первый, последний=последний):
                    от = начала[первый] - 300
                    ряд = поток[от:начала[последний] + 2964 + 1000]
                    ждём = [н - от for н in начала if н - от >= 0 and н - от + 2964 <= len(ряд)]
                    найдено, сведения = cikl.кадры(ряд, 5928)
                    self.assertTrue(сведения["чередование"])
                    self.assertEqual(2964, сведения["кадр"])
                    self.assertEqual(ждём, найдено)

    def test_шум_без_цикла(self):
        шум = np.random.default_rng(11).integers(0, 2, 900_000).astype(np.uint8)
        self.assertIsNone(cikl.гребёнка(шум))
        self.assertIsNone(cikl.найти(шум))


class ВитербиПамять(unittest.TestCase):
    def _код(self, K, g, n=4000, ошибок=0.02, сид=6):
        rng = np.random.default_rng(сид)
        данные = rng.integers(0, 2, n).astype(np.uint8)
        код = svyortka.кодировать(данные, g, K)
        return данные, код ^ (rng.random(len(код)) < ошибок).astype(np.uint8)

    def test_окнами_как_целиком(self):
        for K, g in [(7, [0o171, 0o133]), (5, [0o25, 0o33, 0o37])]:
            with self.subTest(K=K):
                данные, код = self._код(K, g)
                целиком = svyortka.витерби(код, g, K)
                with mock.patch.object(kod, "ЯЧЕЕК_ДО", (1 << (K - 1)) * (25 * K)):
                    окнами = svyortka.витерби(код, g, K)
                self.assertTrue(np.array_equal(окнами, целиком))
                self.assertLess(float((окнами != данные).mean()), 0.01)

    def test_таблица_не_больше_предела(self):
        """Таблица обратного хода — не больше ЯЧЕЕК_ДО ячеек (или двух глубин), а не шагов × состояний."""
        данные, код = self._код(7, [0o171, 0o133], n=20000)
        размеры = []
        настоящий = np.zeros

        def следить(форма, *а, **к):
            if isinstance(форма, tuple) and len(форма) == 2 and форма[1] == 64:
                размеры.append(форма[0] * форма[1])
            return настоящий(форма, *а, **к)

        with mock.patch.object(kod, "ЯЧЕЕК_ДО", 64 * 1000), mock.patch.object(kod.np, "zeros", следить):
            итог = kod.витерби(код, 0o171, 0o133, 7)
        self.assertTrue(размеры)
        self.assertLessEqual(max(размеры), 64 * 1000)
        self.assertLess(float((итог != данные).mean()), 0.01)

    def test_длинный_многочлен_из_шума_не_декодируется(self):
        """K больше ширины поиска связей получается только из НОК несогласных связей — это шум."""
        ложный = {"n": 3, "K": 19, "сдвиг": 0, "многочлены": {0: (1 << 18) | 1, 1: 7, 2: 5},
                  "несвязанные": [], "сила": 0.9}
        with mock.patch.object(svyortka, "опознать", return_value=ложный), \
                mock.patch.object(svyortka, "витерби", side_effect=AssertionError("Витерби не нужен")):
            self.assertIsNone(svyortka.найти(np.zeros(1 << 12, np.uint8)))


if __name__ == "__main__":
    unittest.main()
