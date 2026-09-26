"""Gigabit Ethernet 1000BASE-X: K-символы, упорядоченные наборы, кадры между /S/ и /T/ с FCS.

K-символы сверены со значениями IEEE 802.3 табл. 36-2 (K28.5 001111 1010, K23.7 111010 1000,
K27.7 110110 1000, K29.7 101110 1000, K30.7 011110 1000 — при RD−; при RD+ — дополнения).
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import gbe, lineynye, разобрать


def кадр(н):
    return bytes.fromhex("0011223344550066778899aa0800") + с.udp("10.1.1.1", "10.1.1.2", 1000 + н, 53,
                                                                   bytes([н]) * (30 + н))


class GbeTests(unittest.TestCase):
    def test_k_символы_по_таблице_802_3(self):
        ждать = {("/R/", 0): 0b1110101000, ("/S/", 0): 0b1101101000, ("/T/", 0): 0b1011101000,
                 ("/V/", 0): 0b0111101000, ("K28.5", 0): 0b0011111010}
        for (имя, _), значение in ждать.items():
            with self.subTest(имя=имя):
                x, y = next(к for к, и in gbe.ИМЕНА_K.items() if и == имя)
                self.assertEqual(значение, gbe._символ_k(x, y, 0))
                self.assertEqual(значение ^ 0x3FF, gbe._символ_k(x, y, 1))
                self.assertEqual(имя, gbe.K_СИМВОЛЫ[значение])
        # K-символы не совпадают с символами данных.
        self.assertFalse(set(gbe.K_СИМВОЛЫ) & set(lineynye.ТАБЛИЦА_8B10B))

    def test_кадры_с_fcs_и_ip(self):
        кадры = [кадр(н) for н in range(30)]
        биты = gbe.закодировать(кадры)[7:]
        найдено = gbe.найти(биты)
        self.assertEqual(кадры, найдено.дальше[:len(кадры)])
        текст = "\n".join(найдено.подробно)
        self.assertIn("символы по 10 бит с бита 3", текст)
        self.assertIn("FCS (CRC-32) верна у 30", текст)
        self.assertIn("IP в кадрах: 30 пакетов", текст)
        self.assertRegex(текст, r"/I[12]/×\d+")
        self.assertIn("/S/×30", текст)
        self.assertIn("/T/×30", текст)

    def test_испорченный_кадр_отсеивается(self):
        кадры = [кадр(н) for н in range(20)]
        биты = gbe.закодировать(кадры)
        # Меняем символ данных в середине пятого кадра на другой допустимый — FCS не сходится.
        символы = lineynye._символы(биты, 10, 0)
        поток = gbe.декодировать(символы)
        начала = [i for i, (в, з) in enumerate(поток) if (в, з) == ("K", "/S/")]
        место = начала[4] + 30
        замена = next(с_ for с_, б in lineynye.ТАБЛИЦА_8B10B.items() if б != поток[место][1]
                      and bin(с_).count("1") == bin(int(символы[место])).count("1"))
        биты[место * 10:(место + 1) * 10] = [(замена >> (9 - i)) & 1 for i in range(10)]
        найдено = gbe.найти(биты)
        self.assertEqual(19, len(найдено.дальше))
        self.assertNotIn(кадры[4], найдено.дальше)

    def test_испорченный_sfd_отсеивается(self):
        кадры = [кадр(н) for н in range(20)]
        биты = gbe.закодировать(кадры)
        символы = lineynye._символы(биты, 10, 0)
        поток = gbe.декодировать(символы)
        начала = [i for i, (в, з) in enumerate(поток) if (в, з) == ("K", "/S/")]
        sfd = начала[3] + 7                                   # /S/, шесть 0x55, SFD
        self.assertEqual(("D", 0xD5), поток[sfd])
        замена = next(с_ for с_, б in lineynye.ТАБЛИЦА_8B10B.items() if б == 0xD4
                      and bin(с_).count("1") == bin(int(символы[sfd])).count("1"))
        биты[sfd * 10:(sfd + 1) * 10] = [(замена >> (9 - i)) & 1 for i in range(10)]
        найдено = gbe.найти(биты)
        self.assertIn("с преамбулой и SFD: 19", "\n".join(найдено.подробно))
        self.assertNotIn(кадры[3], найдено.дальше)

    def test_без_кадров_нет_находки(self):
        # Кадры без пауз — запятых K28.5 нет, выравнивание не найти: не 1000BASE-X.
        self.assertIsNone(gbe.найти(gbe.закодировать([кадр(н) for н in range(40)], пауз=0)))
        # Только паузы и 8B/10B без /S/ — не 1000BASE-X с кадрами.
        self.assertIsNone(gbe.найти(gbe.закодировать([], пауз=500)))
        self.assertIsNone(gbe.найти(с.случайные_биты(40_000)))


class АвтоматTests(unittest.TestCase):
    def test_разбор_доходит_до_ip(self):
        биты = gbe.закодировать([кадр(н) for н in range(60)])[5:]
        биты = биты[:len(биты) // 8 * 8]
        разбор = разобрать(данные=np.packbits(биты).tobytes(), профиль="быстро")
        что = " | ".join(н.что for н in разбор.находки)
        self.assertIn("Gigabit Ethernet 1000BASE-X: кадры Ethernet", что)
        self.assertIn("пакеты IP", что)


if __name__ == "__main__":
    unittest.main()
