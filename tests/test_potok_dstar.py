"""D-STAR: таблицы скремблера и перемежения — как в прошивке MMDVM (SHA-256 таблиц DStarRX.cpp,
посчитан по исходнику), свёрточный код TX (DStarTX.cpp: 1+D+D², 1+D²), CRC X.25, поля заголовка,
медленные данные (DStarSlowData.cpp MMDVMHost)."""

import hashlib
import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import dstar as d
from reportgen.potok.razbor import разобрать as разобрать_поток

ЗАГОЛОВОК = d.заголовок_из_полей("R3ABC", "CQCQCQ", "R3XYZ  B", "R3XYZ  G", "ID51", 0x40)


class ТаблицыTests(unittest.TestCase):
    def test_перемежение_как_interleave_table_rx(self):
        """INTERLEAVE_TABLE_RX: пары (байт, бит старшим вперёд) места в последовательности кодера."""
        таблица = bytes(x for м in d.ПЕРЕМЕЖЕНИЕ for x in (int(м) // 8, int(м) % 8))
        self.assertEqual("19bb057c97555e04ddbc2e4afa06518d7385666b6fa67ce90a9f825c56e9106a",
                         hashlib.sha256(таблица).hexdigest())
        self.assertEqual(list(range(660)), sorted(d.ПЕРЕМЕЖЕНИЕ.tolist()))

    def test_скремблер_как_scramble_table_rx(self):
        """SCRAMBLE_TABLE_RX: первые 660 бит (младшим битом вперёд в байте); начало — 70 4F 93."""
        б = np.concatenate([d.СКРЕМБЛЕР, np.zeros(4, np.uint8)])
        байты = bytes(int("".join(map(str, б[i:i + 8][::-1])), 2) for i in range(0, 664, 8))
        self.assertEqual(bytes([0x70, 0x4F, 0x93]), байты[:3])
        self.assertEqual("1bfc5a50c9ae717420289530b0adfdba2fb361f0ffae2563c9740718a21c641d",
                         hashlib.sha256(байты).hexdigest())

    def test_crc_x25(self):
        self.assertEqual(bytes.fromhex("6e90"), d.crc_заголовка(b"123456789"))     # 0x906E — CRC-16/X-25


class ЗаголовокTests(unittest.TestCase):
    def test_туда_и_обратно(self):
        заголовок, верна, ошибок = d.декодировать_заголовок(d.закодировать_заголовок(ЗАГОЛОВОК))
        self.assertEqual((ЗАГОЛОВОК, True, 0), (заголовок, верна, ошибок))

    def test_свёртка(self):
        """1+D+D² первым, 1+D² вторым: единица на входе → 11 10 11."""
        self.assertEqual([1, 1, 1, 0, 1, 1, 0, 0], d.закодировать_свёрткой(np.array([1, 0, 0, 0], np.uint8)).tolist())

    def test_исправление_ошибок(self):
        э = d.закодировать_заголовок(ЗАГОЛОВОК)
        г = np.random.default_rng(4)
        э[г.choice(660, 8, replace=False)] ^= 1
        заголовок, верна, ошибок = d.декодировать_заголовок(э)
        self.assertEqual((ЗАГОЛОВОК, True, 8), (заголовок, верна, ошибок))

    def test_поля(self):
        п = d.поля(ЗАГОЛОВОК)
        # Места полей — как DStarHeader.cpp MMDVMHost: RPT2 — байты 3–10, RPT1 — 11–18, UR — 19, MY — 27.
        self.assertEqual(("R3ABC", "ID51", "CQCQCQ", "R3XYZ  B", "R3XYZ  G", ["через ретранслятор"]),
                         (п["my"], п["суффикс"], п["ur"], п["rpt1"], п["rpt2"], п["флаги"]))
        self.assertEqual(b"R3XYZ  G", ЗАГОЛОВОК[3:11])
        self.assertEqual("R3ABC/ID51 → CQCQCQ через R3XYZ  B / R3XYZ  G (через ретранслятор)", d.описание(п))
        п = d.поля(d.заголовок_из_полей("R3ABC", флаг=0x88 | 7))
        self.assertEqual((["данные", "срочно"], 7), (п["флаги"], п["управление"]))


class ПотокTests(unittest.TestCase):
    def поток(self, *части, сид=1):
        г = np.random.default_rng(сид)
        return np.concatenate([г.integers(0, 2, 400).astype(np.uint8), *части, г.integers(0, 2, 400).astype(np.uint8)])

    def test_передача(self):
        н = d.найти(self.поток(d.передача(ЗАГОЛОВОК, "Hello from R3ABC", 3)))
        self.assertEqual("D-STAR: заголовки передач и речь", н.что)
        текст = "\n".join(н.подробно)
        self.assertIn("R3ABC/ID51 → CQCQCQ через R3XYZ  B / R3XYZ  G", текст)
        self.assertIn("текст медленных данных: «Hello from R3ABC» × 3", текст)
        self.assertIn("синхронизаций данных с шагом сверхкадра (21 × 96 бит): 2; концов передачи: 1", текст)

    def test_обратная_полярность(self):
        н = d.найти(1 - self.поток(d.передача(ЗАГОЛОВОК, "ABCDEFGHIJKLMNOPQRST", 2)))
        self.assertIn("R3ABC/ID51", н.подробно[1])
        self.assertIn("«ABCDEFGHIJKLMNOPQRST» × 2", "\n".join(н.подробно))

    def test_без_заголовка_по_сверхкадрам(self):
        """Заголовок не принят (испорчен), но речь со сверхкадрами есть — это D-STAR."""
        б = d.передача(ЗАГОЛОВОК, "X", 5)
        б[64 + 24:64 + 24 + 200] ^= 1
        н = d.найти(self.поток(б))
        self.assertEqual(0, н.свойства["заголовков"])
        self.assertIn("синхронизаций данных с шагом сверхкадра (21 × 96 бит): 4", н.подробно[0])

    def test_текст_только_из_четырёх_частей_0_3(self):
        """Вместо части 3 (0x43) — блок 0x44: текст не собран (как loadText MMDVMHost)."""
        б = d.передача(ЗАГОЛОВОК, "ABCDEFGHIJKLMNOPQRST", 2)
        for сверх in range(2):
            кадр = 64 + 24 + 660 + (сверх * 21 + 7) * 96 + 72              # кадр 7 — начало блока 3
            б[кадр:кадр + 24] = d._из_байт_младшим(bytes(x ^ y for x, y in zip(b"\x44PQ", d.МЕДЛЕННЫЕ_XOR, strict=False)))
        н = d.найти(self.поток(б))
        self.assertFalse(any("текст" in с for с in н.подробно))
        self.assertEqual([], d.тексты(б, [(64 + 24 + 660 + 72, False)]))

    def test_шаг_синхронизаций(self):
        """Синхронизации данных с шагом, кратным кадру, но не сверхкадру, — не речь D-STAR;
        два шага сверхкадра без заголовка — мало, три — достаточно."""
        ряд = np.zeros(96 * 5 * 8, np.uint8)
        for k in range(8):
            ряд[96 * 5 * k:96 * 5 * k + 24] = d._биты(d.СИНХРО_ДАННЫХ, 24)
        self.assertIsNone(d.найти(ряд))
        for синхро, ожидается in ((3, None), (4, 3)):
            ряд = np.zeros(2016 * синхро, np.uint8)
            for k in range(синхро):
                ряд[2016 * k:2016 * k + 24] = d._биты(d.СИНХРО_ДАННЫХ, 24)
            н = d.найти(ряд)
            self.assertEqual(ожидается, н and int(н.подробно[0].split("(21 × 96 бит): ")[1].split(";")[0]))

    def test_не_dstar(self):
        self.assertIsNone(d.найти(np.random.default_rng(9).integers(0, 2, 100_000).astype(np.uint8)))
        б = d.передача(ЗАГОЛОВОК, "X", 2)
        б[64 + 24:64 + 24 + 200] ^= 1                                  # заголовка нет, сверхкадров мало
        self.assertIsNone(d.найти(self.поток(б)))

    def test_автомат(self):
        б = self.поток(d.передача(ЗАГОЛОВОК, "Hello from R3ABC", 2))
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("D-STAR") for н in р.находки), [н.что for н in р.находки])


if __name__ == "__main__":
    unittest.main()
