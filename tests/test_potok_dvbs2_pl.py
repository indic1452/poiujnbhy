"""PLHEADER DVB-S2: SOF и PLS → MODCOD, тип кадра, пилоты; длина PLFRAME.

Эталонный кодер PLS — GNU Radio gr-dtv dvbs2_physical_cc_impl.cc (b_64_8_code, pl_header_encode,
ph_scram_tab, ph_sync_seq), переписанный здесь построчно; длины PLFRAME сверены со значениями
стандарта (QPSK с пилотами, нормальный кадр — 33282 символа; 8PSK без пилотов — 21690; короткий
QPSK с пилотами — 8370).
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import dvbs2_pl, разобрать

G = [0x90AC2DDD, 0x55555555, 0x33333333, 0x0F0F0F0F, 0x00FF00FF, 0x0000FFFF, 0xFFFFFFFF]
PH_SCRAM_TAB = [0, 1, 1, 1, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0, 0, 0, 0,
                1, 1, 1, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 1, 0, 0,
                0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0]
PH_SYNC_SEQ = [0, 1, 1, 0, 0, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0, 1, 0]


def b_64_8_code(вход):
    temp = 0
    for i, маска in enumerate((0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02)):
        if вход & маска:
            temp ^= G[i]
    out = [0] * 64
    bit = 0x80000000
    for m in range(32):
        out[m * 2] = 1 if temp & bit else 0
        out[m * 2 + 1] = out[m * 2] ^ (вход & 1)
        bit >>= 1
    return [o ^ s for o, s in zip(out, PH_SCRAM_TAB, strict=True)]


def pl_header(modcod, тип):
    код = (modcod | (тип & 1)) if modcod & 0x80 else ((modcod << 2) | тип)
    return np.array(PH_SYNC_SEQ + b_64_8_code(код), np.uint8)


def поток(modcod, тип, кадров, длина, ошибок=0, сид=1):
    г = np.random.default_rng(сид)
    части = [г.integers(0, 2, 137).astype(np.uint8)]
    for _ in range(кадров):
        части.append(pl_header(modcod, тип))
        части.append(г.integers(0, 2, длина - 90).astype(np.uint8))
    итог = np.concatenate(части)
    if ошибок:
        итог ^= (г.random(len(итог)) < ошибок).astype(np.uint8)
    return итог


class КодTests(unittest.TestCase):
    def test_слова_как_gnu_radio(self):
        for код in range(256):
            self.assertEqual(b_64_8_code(код), dvbs2_pl.слово(код).tolist(), код)

    def test_sof(self):
        self.assertEqual(PH_SYNC_SEQ, dvbs2_pl.SOF_БИТЫ.tolist())

    def test_расстояния(self):
        """128 слов DVB-S2 — расстояние 32; все 256 (с DVB-S2X) — 24."""
        с = dvbs2_pl.СЛОВА.astype(int)

        def наименьшее(номера):
            return min(int((с[i] != с[j]).sum()) for n, i in enumerate(номера) for j in номера[n + 1:])
        self.assertEqual(32, наименьшее(list(range(128))))
        self.assertEqual(24, наименьшее(list(range(256))))

    def test_декодирование_с_ошибками(self):
        г = np.random.default_rng(2)
        for код, ошибок in ((dvbs2_pl.код_s2(4, False, True), 15), (dvbs2_pl.код_s2(14, True, False), 15),
                            (0x84, 11), (0x85, 11)):
            with self.subTest(код=код):
                биты = dvbs2_pl.слово(код).copy()
                биты[г.choice(64, ошибок, replace=False)] ^= 1
                self.assertEqual((код, ошибок), dvbs2_pl.декодировать(биты))

    def test_слишком_много_ошибок(self):
        """Случайные 64 бита — не слово PLS."""
        г = np.random.default_rng(5)
        отказов = sum(dvbs2_pl.декодировать(г.integers(0, 2, 64).astype(np.uint8)) is None for _ in range(200))
        self.assertGreater(отказов, 150)

    def test_ближайшее_s2x_дальше_предела(self):
        """Ближе всех слово S2X (12 ошибок — больше 11), но слово S2 в 14 — берётся S2 (до 15)."""
        принято = dvbs2_pl.СЛОВА[4].copy()
        принято[[0, 1, 6, 7, 16, 17, 20, 21, 24, 25, 26, 27, 32, 2]] ^= 1
        расстояния = (dvbs2_pl.СЛОВА != принято).sum(axis=1)
        self.assertEqual((128, 12, 14), (int(расстояния.argmin()), int(расстояния.min()), int(расстояния[4])))
        self.assertEqual((4, 14), dvbs2_pl.декодировать(принято))
        # Слово S2X с 12 ошибками, далёкое от всех S2, — не принимается.
        принято = dvbs2_pl.СЛОВА[128].copy()
        одинаковые = np.flatnonzero(dvbs2_pl.СЛОВА[128] == dvbs2_pl.СЛОВА[4])
        принято[одинаковые[:12]] ^= 1
        расстояния = (dvbs2_pl.СЛОВА != принято).sum(axis=1)
        self.assertEqual((128, 12), (int(расстояния.argmin()), int(расстояния.min())))
        self.assertGreater(int(расстояния[:128].min()), 15)
        self.assertIsNone(dvbs2_pl.декодировать(принято))

    def test_длины_plframe(self):
        self.assertEqual(33282, dvbs2_pl.длина_plframe(4, False, True))
        self.assertEqual(21690, dvbs2_pl.длина_plframe(14, False, False))
        self.assertEqual(8370, dvbs2_pl.длина_plframe(4, True, True))
        self.assertEqual(90 + 36 * 90, dvbs2_pl.длина_plframe(0, False, False))
        # 16APSK 2/3, нормальный, с пилотами: 16200 символов, 180 слотов — 11 блоков пилотов.
        self.assertEqual(90 + 16200 + 11 * 36, dvbs2_pl.длина_plframe(18, False, True))
        # 8PSK короткий: 5400 символов = 60 слотов → 3 блока пилотов.
        self.assertEqual(90 + 5400 + 3 * 36, dvbs2_pl.длина_plframe(13, True, True))
        # 8PSK нормальный: 240 слотов, кратно 16 — за последними 16 слотами пилотов нет: 14 блоков, 22194.
        self.assertEqual(22194, dvbs2_pl.длина_plframe(12, False, True))
        self.assertEqual(90 + 12960 + 8 * 36, dvbs2_pl.длина_plframe(24, False, True))

    def test_описание(self):
        self.assertEqual("MODCOD 4: QPSK 1/2, кадр нормальный (64800), пилоты",
                         dvbs2_pl.описание(dvbs2_pl.код_s2(4, False, True)))
        self.assertEqual("MODCOD 26: 32APSK 5/6, кадр короткий (16200), без пилотов",
                         dvbs2_pl.описание(dvbs2_pl.код_s2(26, True, False)))
        self.assertEqual("DVB-S2X, MODCOD 132, пилоты", dvbs2_pl.описание(0x85))


class ПотокTests(unittest.TestCase):
    def test_кадры_qpsk_с_пилотами(self):
        длина = dvbs2_pl.длина_plframe(4, True, True)
        биты = поток(4, 3, 12, длина)
        найдено = dvbs2_pl.найти(биты)
        self.assertEqual("DVB-S2: PLHEADER, MODCOD 4: QPSK 1/2, кадр короткий (16200), пилоты", найдено.что)
        self.assertEqual(длина, найдено.свойства["шаг"])
        self.assertIn(f"длина PLFRAME по MODCOD — {длина} символов; расстояние между заголовками чаще всего {длина} — "
                      "совпадает", найдено.подробно)
        self.assertIn("код LDPC: DVB-S2 1/2, кадр 16200", "\n".join(найдено.подробно))

    def test_данные_без_заголовков_и_пилотов(self):
        """Кадр: PLHEADER, затем 16 слотов данных и 36 символов пилотов, … (EN 302 307-1 5.5.3)."""
        г = np.random.default_rng(7)
        длина = dvbs2_pl.длина_plframe(4, True, True)          # 90 слотов данных: 5 блоков пилотов
        данные, части = [], [г.integers(0, 2, 50).astype(np.uint8)]
        for _ in range(10):
            части.append(pl_header(4, 3))
            кадр = г.integers(0, 2, 90 * 90).astype(np.uint8)
            данные.append(кадр)
            for k in range(0, 90 * 90, 16 * 90):
                части.append(кадр[k:k + 16 * 90])
                if k + 16 * 90 < 90 * 90:
                    части.append(np.ones(36, np.uint8))
        биты = np.concatenate(части)
        self.assertEqual(50 + 10 * длина, len(биты))
        найдено = dvbs2_pl.найти(биты)
        self.assertTrue(np.array_equal(np.concatenate(данные), найдено.дальше))

    def test_ошибки_канала(self):
        длина = dvbs2_pl.длина_plframe(13, True, False)
        найдено = dvbs2_pl.найти(поток(13, 2, 20, длина, ошибок=0.01))
        self.assertIsNotNone(найдено)
        self.assertEqual(dvbs2_pl.код_s2(13, True, False), найдено.свойства["код"])

    def test_sof_с_ошибками(self):
        биты = поток(4, 3, 6, 8370)
        for м, *_ in dvbs2_pl.заголовки(биты):
            биты[[м + 3, м + 11, м + 20]] ^= 1
        self.assertEqual(6, len(dvbs2_pl.заголовки(биты)))
        for м, *_ in dvbs2_pl.заголовки(биты):
            биты[м + 7] ^= 1
        self.assertEqual([], dvbs2_pl.заголовки(биты))

    def test_шаг_не_по_modcod(self):
        """Заголовки через 9000 символов при MODCOD, требующем 8370, — данные дальше не выдаются."""
        найдено = dvbs2_pl.найти(поток(4, 3, 8, 9000))
        self.assertEqual(9000, найдено.свойства["шаг"])
        self.assertIsNone(найдено.дальше)
        self.assertIn("поток не посимвольный или с пропусками", "\n".join(найдено.подробно))

    def test_два_заголовка_мало(self):
        self.assertIsNone(dvbs2_pl.найти(поток(4, 3, 2, 8370)))
        self.assertIsNotNone(dvbs2_pl.найти(поток(4, 3, 3, 8370)))

    def test_не_dvbs2(self):
        self.assertIsNone(dvbs2_pl.найти(np.random.default_rng(3).integers(0, 2, 300_000).astype(np.uint8)))

    def test_автомат(self):
        биты = поток(4, 3, 30, 8370)
        биты = биты[:len(биты) // 8 * 8]
        разбор = разобрать(данные=np.packbits(биты).tobytes(), профиль="быстро")
        self.assertIn("DVB-S2: PLHEADER, MODCOD 4", " | ".join(н.что for н in разбор.находки))


if __name__ == "__main__":
    unittest.main()
