# -*- coding: utf-8 -*-
"""Мультиплекс со стаффингом общего вида: каналы управления, возможность, знак, разуплотнение."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import stafing
from reportgen.potok.razbor import снять_вручную

P = 200
CJ = {0: [20, 80, 140], 1: [21, 81, 141], 2: [22, 82, 142]}
ВОЗМОЖНОСТЬ = {0: 190, 1: 191, 2: 192}
ЗНАКИ = {0: "+", 1: "+", 2: "−"}
ДОЛИ = {0: 0.3, 1: 0.55, 2: 0.2}


def мультиплекс(кадров=3000, сид=5, ошибок=0.0, доли=None):
    """Кадр 200 бит: синхрослово 16 бит, три притока вперемешку по битам, у каждого —
    три бита управления и бит возможности; стаффинг — по накопителю расстройки."""
    rng = np.random.default_rng(сид)
    синхро = rng.integers(0, 2, 16).astype(np.uint8)
    служебные = set(range(16)) | {c for v in CJ.values() for c in v} | set(ВОЗМОЖНОСТЬ.values())
    данные = [c for c in range(P) if c not in служебные]
    разметка = {t: [c for i, c in enumerate(данные) if i % 3 == t] for t in range(3)}
    источники = {t: rng.integers(0, 2, кадров * 80).astype(np.uint8) for t in range(3)}
    место = {t: 0 for t in range(3)}
    накоплено = {t: 0.0 for t in range(3)}
    кадры = []
    for _ in range(кадров):
        к = np.zeros(P, np.uint8)
        к[:16] = синхро
        for t in range(3):
            накоплено[t] += (доли or ДОЛИ)[t]
            стаффинг = накоплено[t] >= 1
            if стаффинг:
                накоплено[t] -= 1
            к[CJ[t]] = 1 if стаффинг else 0
            n, s = len(разметка[t]), источники[t]
            порядок = sorted(разметка[t] + [ВОЗМОЖНОСТЬ[t]])
            if (not стаффинг) if ЗНАКИ[t] == "+" else стаффинг:
                к[порядок] = s[место[t]:место[t] + n + 1]
                место[t] += n + 1
            else:
                к[разметка[t]] = s[место[t]:место[t] + n]
                место[t] += n
                к[ВОЗМОЖНОСТЬ[t]] = 0 if ЗНАКИ[t] == "+" else 1
        кадры.append(к)
    поток = np.concatenate(кадры)
    if ошибок:
        поток = поток ^ (rng.random(len(поток)) < ошибок).astype(np.uint8)
    return поток, разметка, {t: источники[t][:место[t]] for t in range(3)}


class СтаффингTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.поток, cls.разметка, cls.притоки = мультиплекс()
        cls.шумный, _, _ = мультиплекс(ошибок=1e-3)

    def test_каналы_управления_возможность_знак(self):
        найдено = stafing.найти(self.шумный, P)
        self.assertEqual([CJ[0], CJ[1], CJ[2]], [г["столбцы"] for г in найдено["группы"]])
        self.assertEqual([0.3, 0.55, 0.2], [round(г["доля_стаффинга"], 2) for г in найдено["группы"]])
        self.assertEqual([(190, "+"), (191, "+"), (192, "−")],
                         [(г["возможность"][0]["столбец"], г["возможность"][0]["знак"]) for г in найдено["группы"]])

    def test_разуплотнение_бит_в_бит(self):
        for t in range(3):
            with self.subTest(приток=t):
                поток, сводка = stafing.разуплотнить(self.поток, P, 0, self.разметка[t], CJ[t], ВОЗМОЖНОСТЬ[t], ЗНАКИ[t])
                self.assertTrue(np.array_equal(self.притоки[t], поток))
                self.assertAlmostEqual(ДОЛИ[t], сводка["доля_стаффинга"], places=2)
        # Неверный знак — поток уже не тот.
        поток, _ = stafing.разуплотнить(self.поток, P, 0, self.разметка[0], CJ[0], ВОЗМОЖНОСТЬ[0], "−")
        self.assertFalse(np.array_equal(self.притоки[0][:len(поток)], поток))

    def test_слоем_и_сдвиг(self):
        сдвинутый = np.concatenate([np.ones(37, np.uint8), self.поток])
        данные = ",".join(map(str, self.разметка[2]))
        ряд, запись = снять_вручную(сдвинутый, f"стаффинг период {P} сдвиг 37 данные {данные} "
                                               f"управление 22,82,142 возможность 192 знак -")
        self.assertTrue(np.array_equal(self.притоки[2], ряд))
        self.assertIn("отрицательный в 20.0 % кадров", запись.подробно[0])
        for плохое in ("стаффинг", "стаффинг период 200 данные 1-5", "стаффинг период 200 данные 30-40 "
                       "управление 20,80 возможность 190 знак ?"):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                снять_вручную(self.поток, плохое)

    def test_без_стаффинга_групп_нет(self):
        rng = np.random.default_rng(1)
        кадры = rng.integers(0, 2, (2000, P)).astype(np.uint8)
        кадры[:, :16] = 1
        self.assertIsNone(stafing.найти(кадры.reshape(-1), P))
        with self.assertRaises(ValueError):
            stafing.найти(self.поток, 0)

    def test_автомат_видит_стаффинг_в_цикле(self):
        from reportgen.potok import cikl
        найдено = cikl.найти(self.поток)
        # Медленные биты управления дают пик и на 400 — цикл всё равно 200: синхрослово и через полцикла.
        self.assertEqual("цикл 200 бит, синхрослово 16 бит", найдено.что)
        текст = " ".join(найдено.подробно)
        self.assertIn("стаффинг: канал управления — столбцы 22, 82, 142", текст)
        self.assertIn("возможность — столбец 192, отрицательный", текст)


    def test_разные_синхрослова_цикл_не_делится(self):
        # Кадры по 200 бит, синхрослова двух видов через кадр (как FAS/NFAS): цикл — 400.
        from reportgen.potok import cikl
        rng = np.random.default_rng(8)
        кадры = rng.integers(0, 2, (3000, P)).astype(np.uint8)
        кадры[0::2, :16] = np.unpackbits(np.frombuffer(bytes.fromhex("1ACF"), np.uint8))
        кадры[1::2, :16] = np.unpackbits(np.frombuffer(bytes.fromhex("E530"), np.uint8))
        self.assertTrue(cikl.найти(кадры.reshape(-1)).что.startswith("цикл 400 бит"))

    def test_кратные_доли_чужое_управление_не_возможность(self):
        # 0,5 и 0,25: управление первого притока предсказуемо по управлению второго.
        поток, _, _ = мультиплекс(доли={0: 0.5, 1: 0.25, 2: 0.2})
        найдено = stafing.найти(поток, P)
        управляющие = {c for г in найдено["группы"] for c in г["столбцы"]}
        for г in найдено["группы"]:
            with self.subTest(группа=г["столбцы"]):
                self.assertFalse({в["столбец"] for в in г["возможность"]} & управляющие, г["возможность"])
        self.assertEqual([190, 191, 192], [г["возможность"][0]["столбец"] for г in найдено["группы"]])

    def test_только_явные_кандидаты(self):
        # У независимых притоков у каждой группы один явный кандидат; слабые (< 0,5 бита) не в списке.
        найдено = stafing.найти(self.поток, P)
        self.assertEqual([1, 1, 1], [len(г["возможность"]) for г in найдено["группы"]])
        # И до распределения по притокам в списке только столбцы с разностью энтропий от 0,5 бита.
        кандидаты = stafing.возможность(stafing.кадры(self.поток, P), CJ[0], лучших=10)
        self.assertTrue(кандидаты and all(в["мера"] >= 0.5 for в in кандидаты), кандидаты)

    def test_обратная_полярность_управления(self):
        кадры = self.поток[:len(self.поток) // P * P].reshape(-1, P).copy()
        кадры[:, CJ[1]] ^= 1                                   # «1» — нет стаффинга
        поток, сводка = stafing.разуплотнить(кадры.reshape(-1), P, 0, self.разметка[1], CJ[1], ВОЗМОЖНОСТЬ[1], "+",
                                             полярность=0)
        self.assertTrue(np.array_equal(self.притоки[1], поток))
        self.assertAlmostEqual(ДОЛИ[1], сводка["доля_стаффинга"], places=2)

    def test_постоянное_управление_возможности_не_ищет(self):
        таблица = stafing.кадры(self.поток, P)
        self.assertEqual([], stafing.возможность(таблица, [0, 1]))            # синхрослово — не управление
        единицы = np.ones_like(таблица)
        self.assertEqual([], stafing.возможность(единицы, [5]))

    def test_несогласные_копии_не_группа(self):
        # b и c согласны с a на 96 %, но друг с другом — лишь на ~92 %: это не копии одного бита.
        rng = np.random.default_rng(4)
        таблица = rng.integers(0, 2, (4000, 40)).astype(np.uint8)
        a = (rng.random(4000) < 0.3).astype(np.uint8)          # перекос: a — первая затравка группы
        таблица[:, 5] = a
        таблица[:, 15] = a ^ (rng.random(4000) < 0.04)
        таблица[:, 25] = a ^ (rng.random(4000) < 0.04)
        группы = stafing.группы_управления(таблица)
        self.assertNotIn([5, 15, 25], [г["столбцы"] for г in группы])
        self.assertTrue(all(г["согласие"] >= stafing.СОГЛАСИЕ for г in группы))


class СтаффингЧерезСерверTests(unittest.TestCase):
    def test_найти(self):
        import time
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        сеть = Сеть()
        сеть.setUp()
        self.addCleanup(сеть.tearDown)
        сеть.login("engineer")
        к = сеть.client
        поток, _, _ = мультиплекс(кадров=600)
        ид = к.post("/api/potok", data={"profile": "быстро"},
                    files={"file": ("m.bin", bytes(np.packbits(поток)), "application/octet-stream")}).json()["id"]
        for _ in range(600):
            if к.get(f"/api/potok/{ид}").json()["состояние"] in ("готово", "ошибка"):
                break
            time.sleep(0.2)
        найдено = к.post(f"/api/potok/{ид}/stuffing", json={"stage": 0, "period": P}).json()
        self.assertEqual([CJ[0], CJ[1], CJ[2]], [г["столбцы"] for г in найдено["группы"]])
        self.assertEqual(400, к.post(f"/api/potok/{ид}/stuffing", json={"stage": 0, "period": 0}).status_code)


if __name__ == "__main__":
    unittest.main()
