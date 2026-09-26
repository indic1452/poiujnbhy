# -*- coding: utf-8 -*-
"""Коды вслепую: LDPC, Рида — Соломона, БЧХ, свёрточные 1/n, выколотые, турбо.

Как и в test_potok, всё — на потоках с ИЗВЕСТНЫМ ответом, и в каждом
классе — ложные находки на случайном потоке. Где можно, сверка идёт не
с генератором, а с тем, что известно независимо: число примитивных
многочленов, корни кода DVB, свойства перестановки QPP.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import (crc, dlinnye, gf2, lineynye, polya, rs_bch, svyortka, turbo,
                             vykalyvanie)
from reportgen.potok.bity import в_биты

СЛУЧАЙНЫЕ = с.случайные_биты(400_000, сид=21)


def ошибки(биты, доля, сид=4):
    return биты ^ (np.random.default_rng(сид).random(биты.shape) < доля).astype(np.uint8)


class Gf2Tests(unittest.TestCase):
    def test_упаковка_туда_и_обратно(self):
        биты = np.random.default_rng(1).integers(0, 2, (37, 200), dtype=np.uint8)
        self.assertTrue(np.array_equal(биты, gf2.распаковать(gf2.упаковать(биты), 200)))

    def test_ранг_и_ядро(self):
        случай = np.random.default_rng(2)
        G = случай.integers(0, 2, (100, 130), dtype=np.uint8)
        слова = ((случай.integers(0, 2, (400, 100)) @ G) & 1).astype(np.uint8)
        self.assertEqual(gf2.ранг(G), gf2.ранг(слова))
        H = gf2.ядро(слова)
        self.assertEqual(130 - gf2.ранг(G), len(H))
        self.assertEqual(0, int(((слова.astype(np.int64) @ H.T) & 1).sum()))


class РидСоломонБчхTests(unittest.TestCase):
    def test_примитивные_многочлены(self):
        # Число примитивных многочленов степени m — φ(2^m − 1)/m: 2, 6, 16.
        self.assertEqual((0x13, 0x19), rs_bch.примитивные(4))
        self.assertEqual(6, len(rs_bch.примитивные(5)))
        self.assertEqual(16, len(rs_bch.примитивные(8)))
        self.assertIn(0x11D, rs_bch.примитивные(8))

    def test_код_dvb_узнаётся_по_корням(self):
        from reportgen.potok import dvb
        пакеты = dvb.закодировать(np.random.default_rng(1).integers(0, 256, (80, 188),
                                                                     dtype=np.uint8))
        найдено = rs_bch.опознать(пакеты, 8, двоичный=False)
        self.assertEqual((0x11D, 0, 16, 188, 8),
                         (найдено["многочлен"], найдено["fcr"], найдено["подряд"],
                          найдено["k"], найдено["t"]))

    def test_другой_многочлен_и_первый_корень_и_ошибки(self):
        слова = с.рс(120, 60, 50, многочлен=0x12B, первый=1)
        слова[:30, 7] ^= 0x5A                       # четверть слов с ошибкой
        найдено = rs_bch.опознать(слова, 8, двоичный=False)
        self.assertEqual((0x12B, 1, 10, 50), (найдено["многочлен"], найдено["fcr"],
                                              найдено["подряд"], найдено["k"]))

    def test_бчх_15_7(self):
        # g(x) = x⁸ + x⁷ + x⁶ + x⁴ + 1, несистематически c = d·g.
        g = 0b111010001
        случай = np.random.default_rng(3)
        слова = []
        for _ in range(200):
            c = 0
            for i, b in enumerate(reversed(случай.integers(0, 2, 7).tolist())):
                if b:
                    c ^= g << i
            слова.append([(c >> (14 - i)) & 1 for i in range(15)])
        найдено = rs_bch.опознать(np.array(слова), 4, двоичный=True)
        self.assertEqual(("БЧХ", 0x13, 7, 2, 1), (найдено["вид"], найдено["многочлен"],
                                                  найдено["k"], найдено["t"], найдено["fcr"]))

    def test_случайные_слова_не_код(self):
        слова = np.random.default_rng(4).integers(0, 256, (80, 204))
        self.assertIsNone(rs_bch.опознать(слова, 8, двоичный=False))


class LdpcTests(unittest.TestCase):
    H = с.qc_ldpc()
    G, ОПОРНЫЕ = с.систематический(H)
    ДАННЫЕ = np.random.default_rng(5).integers(0, 2, (1500, len(G)), dtype=np.uint8)
    СЛОВА = ((ДАННЫЕ.astype(np.int32) @ G.astype(np.int32)) & 1).astype(np.uint8)

    def test_вслепую_без_ошибок_с_любого_бита(self):
        for сдвиг in (0, 300):
            with self.subTest(сдвиг=сдвиг):
                найдено = dlinnye.найти(self.СЛОВА.reshape(-1)[сдвиг:], до=700, бюджет=300)
                self.assertIn("LDPC (648, 324), квазициклический, циркулянт Z = 27",
                              найдено.что)
                self.assertIn("вес строк 7×324", " ".join(найдено.подробно))
                начало = (648 - сдвиг) % 648
                блок = (сдвиг + начало) // 648
                self.assertTrue(np.array_equal(
                    self.ДАННЫЕ[блок:блок + 40].reshape(-1), найдено.дальше[:324 * 40]))

    def test_при_ошибках_проверки_и_декодер(self):
        # 10⁻³: половина слов с ошибками. Разреженные проверки — по случайным
        # наборам слов, остальное — сдвигами циркулянта; декодер исправляет всё.
        W = ошибки(self.СЛОВА, 1e-3)
        найдено = dlinnye.проверки_с_шумом(W, попыток=400, бюджет=120)
        редкие = найдено[найдено.sum(axis=1) <= 7]
        Z = dlinnye.размер_циркулянта(редкие, W)
        self.assertEqual(27, Z)
        полные = dlinnye.дополнить_сдвигами(редкие, Z, W)
        self.assertEqual(324, gf2.ранг(полные))
        исправлено, чисто = dlinnye.мин_сумма(W[:500], полные)
        self.assertTrue(чисто.all())
        self.assertTrue(np.array_equal(self.СЛОВА[:500], исправлено))
        # С восстановленной H декодируются и гораздо более шумные слова.
        шумные = ошибки(self.СЛОВА[:500], 0.01, сид=6)
        исправлено, _ = dlinnye.мин_сумма(шумные, полные)
        self.assertGreater(np.mean(np.all(исправлено == self.СЛОВА[:500], axis=1)), 0.98)

    def test_двоичный_образ_кода_рида_соломона(self):
        слова = с.рс(700, 60, 52)
        биты = np.unpackbits(слова.reshape(-1))[37:]
        найдено = dlinnye.найти(биты, до=1024, бюджет=300)
        self.assertIn("код Рида — Соломона (60, 52), t = 4", найдено.что)

    def test_случайный_не_код(self):
        self.assertIsNone(dlinnye.найти(СЛУЧАЙНЫЕ, до=200, бюджет=60))


class СвёрточныеTests(unittest.TestCase):
    U = с.случайные_биты(40_000, сид=7)

    def test_скорости_1_3_и_1_4_и_общий_делитель(self):
        for многочлены, K, доля in (([0o133, 0o171, 0o165], 7, 0.02),
                                    ([0o13, 0o15, 0o17], 4, 0.0),
                                    ([0o171, 0o133, 0o165, 0o117], 7, 0.01)):
            with self.subTest(многочлены=[oct(g) for g in многочлены]):
                поток = ошибки(svyortka.кодировать(self.U, многочлены, K), доля)[1:]
                о = svyortka.опознать(поток)
                n = len(многочлены)
                self.assertEqual((n, n - 1, K), (о["n"], о["сдвиг"], о["K"]))
                self.assertEqual(многочлены, [о["многочлены"][j] for j in range(n)])
                self.assertEqual([], о["несвязанные"])

    def test_находка_и_данные(self):
        поток = svyortka.кодировать(self.U, [0o133, 0o171, 0o165], 7)
        найдено = svyortka.найти(ошибки(поток, 0.02))
        self.assertEqual("свёрточный код скорости 1/3, K=7, 133/171/165", найдено.что)
        self.assertEqual(0, int((найдено.дальше[:30_000] != self.U[:30_000]).sum()))

    def test_код_лишь_в_части_выборки_не_засчитывается(self):
        код = svyortka.кодировать(self.U[:12_000], [0o133, 0o171, 0o165], 7)
        self.assertIsNone(svyortka.найти(np.concatenate([код, с.случайные_биты(150_000, 8)])))

    def test_случайный_не_код(self):
        self.assertIsNone(svyortka.опознать(СЛУЧАЙНЫЕ))


class ЛожныеСвязиTests(unittest.TestCase):
    """Связи, которые выполняются не из-за кода, а из-за самих данных (заполнение, ПСП)."""

    def test_расхождение_больше_чем_позволяет_связь(self):
        """Связь при ошибках 0,1 % выполнена в 99 % окон — у кода и перекодированное расходится
        с принятым не больше; расходится на 10 % — связь из данных, а не код."""
        from unittest import mock
        from reportgen.potok import kod
        rng = np.random.default_rng(31)
        данные = rng.integers(0, 2, 1 << 17).astype(np.uint8)
        код = ошибки(svyortka.кодировать(данные, [0o171, 0o133], 7), 1e-3)
        self.assertIsNotNone(kod.свёрточный(код))
        настоящий = kod.витерби

        def плохой(*а):
            return настоящий(*а) ^ (rng.random(len(а[0]) // 2) < 0.1).astype(np.uint8)

        with mock.patch.object(kod, "витерби", плохой):
            self.assertIsNone(kod.свёрточный(код))

    def test_постоянные_связи_заполнения_не_блочный_код(self):
        """Два 8-битных слова по очереди: 7 связей выполнены по отдельности (одинаковы у обоих
        слов), но все сразу — почти нигде: это «(8, 1)» из заполнения, не код."""
        from reportgen.potok import kod
        rng = np.random.default_rng(32)
        слова = np.array([[0, 1, 1, 1, 1, 1, 1, 0], [1, 0, 1, 1, 0, 1, 0, 0]], np.uint8)
        поток = np.tile(слова.reshape(-1), 1 << 14)
        поток = ошибки(поток, 5e-3, сид=32)
        self.assertIsNone(kod.блочный_устойчивый(поток))

    def test_одно_заполнение_без_данных_не_код_и_без_памяти(self):
        """Слова все одинаковые — выполнены все 2ⁿ − 1 связей: это не код (k = 0), и проверка
        всех связей сразу не должна строить матрицу 2ⁿ × слов (раньше — 16 ГБ)."""
        from reportgen.potok import kod
        поток = np.tile(np.array([0, 1, 1, 1, 1, 1, 1, 0], np.uint8), 1 << 15)
        self.assertIsNone(kod.блочный_устойчивый(поток))

    def test_настоящий_код_при_ошибках_остаётся(self):
        from reportgen.potok import kod
        rng = np.random.default_rng(33)
        for доля in (1e-3, 1e-2, 5e-2):
            with self.subTest(доля=доля):
                данные = rng.integers(0, 2, 1 << 17).astype(np.uint8)
                код = ошибки(svyortka.кодировать(данные, [0o171, 0o133], 7), доля, сид=int(доля * 1e4))
                найдено = kod.найти(код)
                self.assertIsNotNone(найдено)
                self.assertIn("171/133", найдено.что)


class ВыколотыеTests(unittest.TestCase):
    U = с.случайные_биты(60_000, сид=2)
    МАТЕРИНСКИЙ = svyortka.кодировать(U, [0o171, 0o133], 7)

    def test_шаблон_скорость_и_начало_при_ошибках(self):
        for шаблон, сдвиг in (((1, 1, 0, 1), 1), ((1, 1, 0, 1, 1, 0), 2),
                              ((1, 1, 0, 1, 0, 1, 0, 1, 1, 0), 3)):
            with self.subTest(шаблон=шаблон):
                поток = ошибки(vykalyvanie.выколоть(self.МАТЕРИНСКИЙ, шаблон), 0.03)[сдвиг:]
                о = vykalyvanie.опознать(поток)
                n = sum(шаблон)
                self.assertEqual((0o171, 0o133, 7, шаблон, (n - сдвиг) % n),
                                 (о["g1"], о["g2"], о["K"], о["шаблон"], о["начало"]))

    def test_данные_после_витерби(self):
        шаблон = (1, 1, 0, 1, 1, 0)
        найдено = vykalyvanie.найти(vykalyvanie.выколоть(self.МАТЕРИНСКИЙ, шаблон))
        self.assertIn("скорости 3/4: материнский K=7, 171/133, шаблон X 101 / Y 110",
                      найдено.что)
        self.assertEqual(0, int((найдено.дальше[:20_000] != self.U[:20_000]).sum()))

    def test_случайный_не_код(self):
        self.assertIsNone(vykalyvanie.опознать(СЛУЧАЙНЫЕ[:60_000]))


class ТурбоTests(unittest.TestCase):
    ПОТОК, ДАННЫЕ, π = с.турбо(80)

    def test_qpp_это_перестановка(self):
        self.assertEqual(160, len(set(self.π.tolist())))

    def test_ветви_блок_перестановка_при_ошибках(self):
        for доля in (0.0, 0.005):
            with self.subTest(доля=доля):
                поток = ошибки(self.ПОТОК, доля)
                о = svyortka.опознать(поток)
                self.assertEqual((3, [2], 0o13, 0o15),
                                 (о["n"], о["несвязанные"], о["многочлены"][0],
                                  о["многочлены"][1]))
                тройки = поток.reshape(-1, 3)
                нарушения = turbo.связь_u_p1(тройки[:, 0], тройки[:, 1], 0o13, 0o15)
                период, _ = turbo.длина_блока(нарушения)
                вскрыто = turbo.вскрыть(тройки, 0o13, 0o15, 4, период, нарушения)
                self.assertEqual((0, 4, 160), (вскрыто["начало"], вскрыто["хвост"],
                                               вскрыто["длина"]))
                self.assertTrue(np.array_equal(self.π, вскрыто["π"]))
                f1, f2, _ = вскрыто["qpp"]
                i = np.arange(160)
                self.assertTrue(np.array_equal(self.π, (f1 * i + f2 * i * i) % 160))

    def test_декодер_исправляет(self):
        поток = ошибки(self.ПОТОК, 0.05)
        блоки = поток.reshape(80, 164 * 3)[:, :480].reshape(80, 160, 3)
        данные = turbo.декодировать(блоки[:, :, 0], блоки[:, :, 1], блоки[:, :, 2], self.π,
                                    0o13, 0o15, 4, 0.05)
        self.assertGreater(float((блоки[:, :, 0] != self.ДАННЫЕ).mean()), 0.03)
        self.assertTrue(np.array_equal(self.ДАННЫЕ, данные))

    def test_находка_целиком(self):
        найдено = turbo.найти(ошибки(self.ПОТОК, 0.01)[3:])
        self.assertIn("турбокод (PCCC) скорости 1/3, RSC 13/15, блок 160, перемежитель QPP",
                      найдено.что)
        данные = найдено.дальше.reshape(-1, 160)
        # Сдвиг на тройку: первый целый блок — второй блок источника.
        self.assertTrue(np.array_equal(self.ДАННЫЕ[1:1 + len(данные)], данные))

    def test_свёрточный_1_3_не_турбо(self):
        поток = svyortka.кодировать(с.случайные_биты(30_000), [0o133, 0o171, 0o165], 7)
        self.assertIsNone(turbo.найти(поток))


class CrcTests(unittest.TestCase):
    СЛУЧАЙ = np.random.default_rng(1)

    def _кадры(self, w, P, init, refin, refout, xorout, порядок, одной_длины=False):
        кадры = []
        for _ in range(60):
            длина = 30 if одной_длины else int(self.СЛУЧАЙ.integers(10, 60))
            тело = bytes(self.СЛУЧАЙ.integers(0, 256, длина).tolist())
            кадры.append(тело + crc.crc(тело, w, P, init, refin, refout, xorout)
                         .to_bytes(w // 8, порядок))
        return кадры

    def test_контрольные_значения_каталога(self):
        # Независимая сверка расчёта: CRC строки «123456789» — из каталога.
        for имя, w, P, init, refin, refout, xorout, контроль in crc.КАТАЛОГ:
            with self.subTest(имя):
                self.assertEqual(контроль, crc.crc(b"123456789", w, P, init, refin, refout,
                                                   xorout))

    def test_каталог_и_свои_вслепую(self):
        варианты = [м[1:7] for м in crc.КАТАЛОГ] + [(16, 0x3D65, 0x1234, False, True, 0x0F0F),
                                                   (32, 0x741B8CD7, 0, True, False, 0xFFFFFFFF)]
        for w, P, init, refin, refout, xorout in варианты:
            with self.subTest(w=w, P=hex(P)):
                порядок = "little" if refin else "big"
                найдено = crc.найти(self._кадры(w, P, init, refin, refout, xorout, порядок))
                self.assertEqual((w, P, init, refin, refout, xorout, порядок, 1.0),
                                 (найдено["w"], найдено["P"], найдено["init"], найдено["refin"],
                                  найдено["refout"], найдено["xorout"], найдено["порядок"],
                                  найдено["верно"]))

    def test_равносильные_модели_названы(self):
        # У X.25 в многочлене множитель (x + 1): есть другая пара (init, xorout),
        # дающая те же CRC, — её надо назвать, а выбрать — каталожную.
        найдено = crc.найти(self._кадры(16, 0x1021, 0xFFFF, True, True, 0xFFFF, "little"))
        self.assertEqual("CRC-16/IBM-SDLC (X.25, HDLC)", найдено["имя"])
        self.assertEqual(1, len(найдено["равносильные"]))

    def test_одной_длины_init_не_разделить(self):
        найдено = crc.найти(self._кадры(16, 0x8005, 0xFFFF, True, True, 0, "little",
                                        одной_длины=True))
        self.assertEqual(0x8005, найдено["P"])
        self.assertIsNone(найдено["init"])

    def test_без_crc_ничего(self):
        кадры = [bytes(self.СЛУЧАЙ.integers(0, 256, 40).tolist()) for _ in range(60)]
        self.assertIsNone(crc.найти(кадры))


class ПоляTests(unittest.TestCase):
    def test_заголовок_неизвестного_протокола(self):
        import struct
        случай = np.random.default_rng(3)
        кадры = []
        for i in range(300):
            тело = bytes(случай.integers(0, 256, int(случай.integers(8, 80))).tolist())
            заголовок = (b"\xa5\x5a" + struct.pack(">H", i) + bytes([[0x10, 0x10, 0x20, 0x31][i % 4]])
                         + struct.pack("<H", len(тело) + 9) + bytes([i % 256]))
            кадр = заголовок + тело
            кадры.append(кадр + crc.crc(кадр, 16, 0x1021, 0xFFFF, False, False, 0)
                         .to_bytes(2, "big"))
        найдено = polya.найти(кадры)
        текст = "\n".join(найдено.подробно)
        for ожидаемое in ("поле @0: постоянное — 0xA5", "поле @1: постоянное — 0x5A",
                          "поле @2–3: счётчик — 16 бит, порядок big-endian",
                          "поле @4: тип — 0x10×150, 0x20×75, 0x31×75",
                          "поле @5–6: длина — 16 бит, little-endian: длина кадра − 1",
                          "поле @7: счётчик — 8 бит",
                          "CRC-16 (CRC-16/CCITT-FALSE)"):
            self.assertIn(ожидаемое, текст)

    def test_случайные_кадры_без_полей(self):
        случай = np.random.default_rng(4)
        кадры = [bytes(случай.integers(0, 256, int(случай.integers(20, 60))).tolist())
                 for _ in range(200)]
        self.assertIsNone(polya.найти(кадры))


class ЛинейныеКодыTests(unittest.TestCase):
    def test_манчестер_при_любой_фазе(self):
        d = с.случайные_биты(100_000)
        поток = np.column_stack([1 - d, d]).reshape(-1)
        for сдвиг in (0, 1):
            with self.subTest(сдвиг=сдвиг):
                найдено = lineynye.манчестер(ошибки(поток, 0.002)[сдвиг:])
                данные = найдено.дальше["IEEE 802.3"]
                self.assertGreater(np.mean(данные[:5000] == d[сдвиг:сдвиг + 5000]), 0.99)

    def test_4b5b(self):
        случай = np.random.default_rng(1)
        полубайты = случай.integers(0, 16, 40_000)
        символы = [lineynye.ДАННЫЕ_4B5B[x] for x in полубайты]
        поток = np.array([(v >> (4 - i)) & 1 for v in символы for i in range(5)], np.uint8)[3:]
        найдено = lineynye.код_4b5b(поток)
        ожидалось = np.unpackbits(полубайты[1:].astype(np.uint8)[:, None], axis=1)[:, 4:]
        self.assertTrue(np.array_equal(ожидалось.reshape(-1)[:8000], найдено.дальше[:8000]))

    def test_8b10b_свойства_и_данные(self):
        данные = bytes(np.random.default_rng(1).integers(0, 256, 20_000).tolist())
        поток = lineynye.закодировать_8b10b(данные)[7:]
        # Свойства кода, не зависящие от таблицы: серии не длиннее 5.
        строка = "".join(map(str, поток[:200_000].tolist()))
        self.assertEqual(5, max(len(х) for х in строка.replace("1", " ").split()))
        найдено = lineynye.код_8b10b(поток)
        from reportgen.potok.bity import в_байты
        # Срезано 7 бит — первый целый символ после запятой несёт байт 0.
        self.assertEqual(данные[:2000], в_байты(найдено.дальше)[:2000])

    def test_случайный_не_код_в_линии(self):
        self.assertIsNone(lineynye.найти(СЛУЧАЙНЫЕ))


if __name__ == "__main__":
    unittest.main()
