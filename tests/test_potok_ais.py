"""AIS: поля сообщений по образцам pyais (tests/test_decode.py) и поток в эфирном виде (NRZI + HDLC).

Образцы: тип 1 — 366053209, 37.802118 / −122.341618, курс 219.3, «ограничен в маневре»; тип 5 (две
части) — EVER DIADEM, 3FOF8, 225/70/1/31, осадка 12.2, NEW YORK; тип 18 — 367430530, 37.79 / −122.27;
тип 21 (первая часть) — SIMPSON ROCK, −36.0075 / 175.119987.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import ais
from reportgen.potok.bity import в_биты
from reportgen.potok.razbor import разобрать as разобрать_поток

ТИП1 = ("15M67FC000G?ufbE`FepT@3n00Sa", 0)
ТИП5 = ("55?MbV02;H;s<HtKR20EHE:0@T4@Dn2222222216L961O5Gf0NSQEp6ClRp8" + "88888888880", 2)
ТИП18 = ("B5NJ;PP005l4ot5Isbl03wsUkP06", 0)
ТИП21 = ("E>m1c1>9TV`9WW@97QUP0000000F@lEpmdceP00003b", 0)


def биты(образец):
    return ais.из_nmea(*образец)


class СообщенияTests(unittest.TestCase):
    def test_тип_1(self):
        п = ais.разобрать(биты(ТИП1))
        self.assertEqual((1, 366053209, "ограничен в маневре", 0.0, (37.802118, -122.341618), 219.3, 1, 59),
                         (п["тип"], п["mmsi"], п["состояние"], п["скорость"], п["координаты"], п["курс"],
                          п["истинный_курс"], п["секунда"]))

    def test_тип_5(self):
        п = ais.разобрать(биты(ТИП5))
        self.assertEqual(424, len(биты(ТИП5)))
        self.assertEqual(("3FOF8", "EVER DIADEM", "грузовое", (225, 70, 1, 31), 12.2, "NEW YORK"),
                         (п["позывной"], п["название"], п["тип_судна"], п["размеры"], п["осадка"], п["назначение"]))

    def test_тип_18_и_21(self):
        п = ais.разобрать(биты(ТИП18))
        self.assertEqual((18, 367430530, 0.0, None), (п["тип"], п["mmsi"], п["скорость"], п["истинный_курс"]))
        self.assertEqual((37.79, -122.27), tuple(round(к, 2) for к in п["координаты"]))
        п = ais.разобрать(биты(ТИП21))
        self.assertEqual((21, 995126020, "SIMPSON ROCK", (-36.0075, 175.119987)),
                         (п["тип"], п["mmsi"], п["название"], п["координаты"]))

    def test_броня_туда_и_обратно(self):
        for образец in (ТИП1, ТИП5, ТИП18):
            б = биты(образец)
            self.assertEqual(б.tolist(), ais.из_nmea(*ais.в_nmea(б)).tolist())
        # Знаки с 0x60 — со сдвигом 0x38: «`» = 40, «w» = 63; «W» (0x57) = 39.
        self.assertEqual([1, 0, 1, 0, 0, 0] + [1] * 6 + [1, 0, 0, 1, 1, 1], ais.из_nmea("`wW").tolist())
        self.assertEqual(("`W", 0), ais.в_nmea(np.array([1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 1, 1], np.uint8)))

    def test_шестибитовый_текст(self):
        """Значения меньше 32 — плюс 64 (1 → A), 31 → «_», 32 → пробел; 0 («@») — конец."""
        биты = np.array([int(ч) for n in (1, 31, 32, 48, 0, 2) for ч in format(n, "06b")], np.uint8)
        self.assertEqual("A_ 0", ais._текст(биты, 0, len(биты)))

    def test_нет_данных_о_положении(self):
        б = биты(ТИП1).copy()
        б[61:89] = [int(ч) for ч in format(181 * 600000, "028b")]
        п = ais.разобрать(б)
        self.assertIsNone(п["координаты"])
        б[50:60] = [1] * 10                                   # 1023 — скорости нет
        б[116:128] = [int(ч) for ч in format(3600, "012b")]    # 3600 — курса нет
        б[128:137] = [1] * 9                                  # 511 — истинного курса нет
        п = ais.разобрать(б)
        self.assertEqual((None, None, None), (п["скорость"], п["курс"], п["истинный_курс"]))
        б[116:128] = [int(ч) for ч in format(3599, "012b")]
        б[128:137] = [int(ч) for ч in format(510, "09b")]
        п = ais.разобрать(б)
        self.assertEqual((359.9, 510), (п["курс"], п["истинный_курс"]))

    def test_длины_типов(self):
        self.assertTrue(ais.годное_по_длине(168, 1))
        self.assertTrue(ais.годное_по_длине(424, 5))
        self.assertFalse(ais.годное_по_длине(176, 1))
        self.assertTrue(ais.годное_по_длине(360, 21))
        self.assertFalse(ais.годное_по_длине(368, 21))
        self.assertFalse(ais.годное_по_длине(168, 9))
        self.assertTrue(ais.годное_по_длине(160, 24))                   # часть A бывает и без запаса
        self.assertTrue(ais.годное_по_длине(168, 24))
        self.assertIsNone(ais.разобрать(биты(ТИП5)[:400]))       # тип 5 без конца — не разобрать


def эфир(образцы, повторов=4):
    """Сообщения → кадры HDLC (байты — биты сообщения старшим вперёд) → NRZI-S (0 — переход)."""
    кадры = []
    for образец in образцы:
        б = биты(образец)
        б = np.concatenate([б, np.zeros(-len(б) % 8, np.uint8)])
        кадры.append(np.packbits(б).tobytes())
    ряд = в_биты(с.hdlc(кадры * повторов, флагов_между=3))
    уровень, итог = 1, []
    for бит in ряд:
        if not бит:
            уровень ^= 1
        итог.append(уровень)
    return np.array(итог, dtype=np.uint8)


class ПотокTests(unittest.TestCase):
    def test_nrzi_hdlc_ais(self):
        б = эфир([ТИП1, ТИП5, ТИП18])
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        найдено = [н for н in р.находки if н.что.startswith("AIS")]
        self.assertTrue(найдено, [н.что for н in р.находки])
        текст = "\n".join(найдено[0].подробно)
        self.assertIn("MMSI 351759000, статические и рейсовые данные, EVER DIADEM, позывной 3FOF8, грузовое", текст)
        self.assertIn("MMSI 366053209, положение (класс A), 37.80212, -122.34162", текст)
        кадры = [н for н in р.находки if н.что.startswith("NRZI → ")]
        self.assertTrue(кадры, [н.что for н in р.находки])

    def test_без_nrzi_тоже(self):
        """Те же кадры без NRZI — HDLC находится сразу, вид — AIS."""
        кадры = [np.packbits(np.concatenate([биты(о), np.zeros(-len(биты(о)) % 8, np.uint8)])).tobytes()
                 for о in (ТИП1, ТИП18)]
        from reportgen.potok import kanal
        н = kanal.найти(кадры * 5)
        self.assertEqual("AIS (ITU-R M.1371): сообщения судов и станций", н.что)
        self.assertIn("сообщений AIS: 10, судов и станций (MMSI): 2", н.подробно)

    def test_вид_кадра_ais(self):
        from reportgen.setevoy import vid_kadra
        б = биты(ТИП18)
        self.assertTrue(vid_kadra.ais(np.packbits(б).tobytes()))
        б = б.copy()
        б[8:38] = 0                                                          # MMSI 0 — не AIS
        self.assertFalse(vid_kadra.ais(np.packbits(б).tobytes()))
        self.assertFalse(vid_kadra.ais(np.packbits(биты(ТИП18)).tobytes() + b"\0"))    # длина не та


if __name__ == "__main__":
    unittest.main()
