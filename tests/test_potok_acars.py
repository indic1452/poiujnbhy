"""ACARS (ARINC 618): CRC — как у acarsdec (таблица crc_ccitt_table), знаки с нечётной чётностью младшим
битом вперёд, поля блока — как output.c acarsdec (режим, адрес, ACK/NAK, метка, номер блока, STX,
номер сообщения и рейс у блока с борта)."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import acars as a
from reportgen.potok.razbor import разобрать as разобрать_поток


def поток(*блоки, сид=1):
    г = np.random.default_rng(сид)
    части = []
    for б in блоки:
        части += [г.integers(0, 2, 300).astype(np.uint8), б]
    return np.concatenate(части + [г.integers(0, 2, 300).astype(np.uint8)])


С_БОРТА = a.закодировать("2", "N12345", "\x15", "H1", "1", "#DFBA320 ENGINE DATA", сообщение="M01A", рейс="BA0123")
НА_БОРТ = a.закодировать("2", "G-ABCD", "A", "_\x7f", "A")


class CrcTests(unittest.TestCase):
    def test_как_acarsdec(self):
        self.assertEqual([0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
                          0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7], a.ТАБЛИЦА[:16])
        self.assertEqual(0x2189, a.crc(b"123456789"))                   # CRC-16/KERMIT, каталог

    def test_чётность(self):
        self.assertEqual((0x16, 0x01, 0x83, 0x97, 0x02, 0xAB, 0x7F),
                         (a.с_чётностью(0x16), a.с_чётностью(0x01), a.с_чётностью(0x03), a.с_чётностью(0x17),
                          a.с_чётностью(0x02), a.с_чётностью(0x2B), a.с_чётностью(0x7F)))


class БлокиTests(unittest.TestCase):
    def test_поля(self):
        б = a.блоки(поток(С_БОРТА, НА_БОРТ))
        self.assertEqual(2, len(б))
        self.assertTrue(all(х.crc_верна and not х.ошибок_чётности for х in б))
        п = б[0].поля()
        self.assertEqual(("2", "N12345", "NAK", "H1", "1", True, "M01A", "BA0123", "#DFBA320 ENGINE DATA"),
                         (п["режим"], п["адрес"], п["ack"], п["метка"], п["блок"], п["с_борта"], п["сообщение"],
                          п["рейс"], п["текст"]))
        п = б[1].поля()
        self.assertEqual(("G-ABCD", "A", "_d", "A", False, ""), (п["адрес"], п["ack"], п["метка"], п["блок"],
                                                                п["с_борта"], п["текст"]))

    def test_текст_на_борт_без_рейса(self):
        б = a.блоки(поток(a.закодировать("2", "N1", "A", "SA", "B", "CLIMB TO FL350")))
        п = б[0].поля()
        self.assertEqual(("CLIMB TO FL350", None), (п["текст"], п.get("рейс")))

    def test_с_борта_без_текста(self):
        """Блок с борта без STX: номера сообщения и рейса нет."""
        п = a.блоки(поток(a.закодировать("2", "N1", "A", "Q0", "5")))[0].поля()
        self.assertEqual((True, None, None, ""), (п["с_борта"], п.get("сообщение"), п.get("рейс"), п["текст"]))

    def test_без_конца_не_блок(self):
        """ETX испорчен, дальше — нули (ETX среди них нет): до 240 знаков конца нет — блока нет."""
        испорчено = С_БОРТА.copy()
        конец = 128 + 8 * 5 + 8 * (len(a.блоки(С_БОРТА)[0].знаки) - 1)
        испорчено[конец:конец + 8] = 0
        self.assertEqual([], a.блоки(np.concatenate([испорчено[:конец + 8], np.zeros(3000, np.uint8)])))

    def test_верная_crc_но_чётность(self):
        """Знак с чётной чётностью, а CRC посчитана по нему — блок не засчитывается."""
        знаки = bytes(a.с_чётностью(ord(с)) for с in "2.N12345AH11") + bytes([0x02, ord("A"), 0x83])  # «A» 0x41 — чётная
        c = a.crc(знаки)
        кадр = bytes([0x16, 0x16, 0x01]) + знаки + bytes([c & 0xFF, c >> 8])
        биты = np.array([1] * 64 + [(б >> k) & 1 for б in кадр for k in range(8)], np.uint8)
        б = a.блоки(поток(биты))
        self.assertEqual((True, 1), (б[0].crc_верна, б[0].ошибок_чётности))
        self.assertIsNone(a.найти(поток(биты, НА_БОРТ)))

    def test_короткий_блок(self):
        """Меньше 13 знаков (как у acarsdec: blk->len < 13) — не блок, даже с верной CRC."""
        for начало in ("2.N1", "2.N12345AH1"):                          # 5 и 12 знаков с ETX
            знаки = bytes(a.с_чётностью(ord(с)) for с in начало) + bytes([0x83])
            c = a.crc(знаки)
            кадр = bytes([0x16, 0x16, 0x01]) + знаки + bytes([c & 0xFF, c >> 8])
            биты = np.array([1] * 64 + [(б >> k) & 1 for б in кадр for k in range(8)], np.uint8)
            self.assertEqual([], a.блоки(поток(биты)), начало)

    def test_etb_и_обратная_полярность(self):
        н = a.найти(1 - поток(a.закодировать("2", "N1", "A", "H1", "1", "PART 1", сообщение="M02A", рейс="XX0001",
                                             последний=False), С_БОРТА))
        self.assertIn("полярность обратная", н.подробно[0])
        self.assertEqual(2, н.свойства["блоков"])

    def test_ошибки_отсекаются(self):
        испорчено = С_БОРТА.copy()
        испорчено[128 + 8 * 12 + 3] ^= 1                                 # один бит в тексте: чётность и CRC
        self.assertIsNone(a.найти(поток(испорчено, НА_БОРТ)))
        двойная = С_БОРТА.copy()
        двойная[128 + 8 * 12 + 3] ^= 1
        двойная[128 + 8 * 12 + 4] ^= 1                                   # чётность цела, CRC — нет
        б = a.блоки(поток(двойная))
        self.assertEqual((False, 0), (б[0].crc_верна, б[0].ошибок_чётности))
        self.assertIsNone(a.найти(поток(двойная, НА_БОРТ)))

    def test_шум_и_один_блок(self):
        self.assertIsNone(a.найти(np.random.default_rng(3).integers(0, 2, 200_000).astype(np.uint8)))
        self.assertIsNone(a.найти(поток(С_БОРТА)))

    def test_автомат(self):
        б = поток(С_БОРТА, НА_БОРТ, С_БОРТА)
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        найдено = [н for н in р.находки if н.что.startswith("ACARS")]
        self.assertTrue(найдено, [н.что for н in р.находки])
        self.assertIn("N12345 [H1] блок 1, сообщение M01A, рейс BA0123: #DFBA320 ENGINE DATA", найдено[0].подробно)


if __name__ == "__main__":
    unittest.main()
