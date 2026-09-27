"""P25 фаза 1: BCH (63, 16) NID (многочлен BCH.cpp MMDVMHost — делит x⁶³ + 1, расстояние 23), символы
состояния, решётчатый код 1/2 (P25Trellis.cpp), CRC TSBK (CRC-16/GSM), поля TSBK (op25 tk_p25.py)."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import p25
from reportgen.potok.razbor import разобрать as разобрать_поток

СЕТЬ = bytes([0x3B, 0x00, 0x00, 0xBE, 0xE0, 0x00, 0x12, 0x31, 0x00, 0x01])       # WACN BEE00, SYSID 012
ВЫЗОВ = bytes([0x00, 0x00, 0x00, 0x10, 0x01, 0x23, 0x45, 0x67, 0x89, 0xAB])      # группа 0x2345, от 0x6789AB


class BchTests(unittest.TestCase):
    def test_многочлен_делит_x63_плюс_1(self):
        g = int("".join(map(str, reversed(p25.G_BCH))), 2)                         # старшая степень слева
        остаток = (1 << 63) | 1
        while остаток.bit_length() >= g.bit_length():
            остаток ^= g << (остаток.bit_length() - g.bit_length())
        self.assertEqual(0, остаток)
        self.assertEqual(47, g.bit_length() - 1)

    def test_расстояние_23(self):
        веса = np.bitwise_count(p25.СЛОВА_NID[1:])
        self.assertEqual(23, int(веса.min()))

    def test_nid_делится_на_g(self):
        """Систематический BCH (encode BCH.cpp): бит NID i (i < 16) — коэффициент при x^(47 + i),
        бит 16 + j — при x^j; кодовый многочлен делится на g(x)."""
        g = int("".join(map(str, reversed(p25.G_BCH))), 2)
        for nac, duid in ((0x293, 7), (0x001, 0), (0xFFF, 15), (0x5A5, 10)):
            nid = p25.закодировать_nid(nac, duid)
            многочлен = sum(int(nid[i]) << (47 + i) for i in range(16)) + sum(int(nid[16 + j]) << j for j in range(47))
            while многочлен and многочлен.bit_length() >= g.bit_length():
                многочлен ^= g << (многочлен.bit_length() - g.bit_length())
            self.assertEqual(0, многочлен, (nac, duid))

    def test_nid_и_исправление(self):
        nid = p25.закодировать_nid(0x293, 7)
        self.assertEqual((0x293, 7, 0), p25.декодировать_nid(nid))
        self.assertEqual(0, nid[63])
        self.assertEqual(1, p25.закодировать_nid(0x293, 5)[63])            # как P25NID.cpp: у LDU бит 1
        г = np.random.default_rng(2)
        испорчено = nid.copy()
        испорчено[г.choice(63, 11, replace=False)] ^= 1
        self.assertEqual((0x293, 7, 11), p25.декодировать_nid(испорчено))


class ТреллисTests(unittest.TestCase):
    def test_crc_как_gsm(self):
        self.assertEqual(bytes.fromhex("ce3c"), p25.crc_tsbk(b"123456789"))

    def test_туда_обратно_и_ошибки(self):
        данные = СЕТЬ + p25.crc_tsbk(СЕТЬ)
        э = p25.закодировать_треллис(данные)
        self.assertEqual((данные, 0), p25.декодировать_треллис(э))
        э[[3, 50, 120, 190]] ^= 1
        self.assertEqual((данные, 4), p25.декодировать_треллис(э))

    def test_таблицы_как_p25trellis(self):
        """ENCODE_TABLE_12 и pointsToDibits из P25Trellis.cpp — дословно."""
        self.assertEqual((0, 15, 12, 3, 4, 11, 8, 7, 13, 2, 1, 14, 9, 6, 5, 10), p25.КОД_12)
        self.assertEqual(((+1, -1), (-1, -1), (+3, -3), (-3, -3), (-3, -1), (+3, -1), (-1, -3), (+1, -3),
                          (-3, +3), (+3, +3), (-1, +1), (+1, +1), (+1, +3), (-1, +3), (+3, +1), (-3, +1)), p25.ТОЧКИ)

    def test_хвост_решётки_в_нуле(self):
        """Путь Витерби кончается в состоянии 0 (хвостовой дибит — нуль): две ошибки в последней точке
        исправляются, а «лучшее конечное состояние» здесь ошиблось бы."""
        данные = СЕТЬ + p25.crc_tsbk(СЕТЬ)
        э = p25.закодировать_треллис(данные)
        э[[194, 195]] ^= 1
        self.assertEqual(данные, p25.декодировать_треллис(э)[0])

    def test_первые_точки_как_encode_table_12(self):
        """Нулевые данные: состояние 0, дибит 0 → точка 0 → символы (+1, −1) → биты 00 10."""
        э = p25.закодировать_треллис(bytes(12))
        # Бит эфира 0–1 — символ с номером ПЕРЕМЕЖЕНИЕ[0] = 0: +1 → 00; биты 2–3 — символ 1: −1 → 10.
        self.assertEqual([0, 0, 1, 0], э[:4].tolist())


class КадрыTests(unittest.TestCase):
    def поток(self, *кадры, сид=1):
        г = np.random.default_rng(сид)
        return np.concatenate([np.concatenate([г.integers(0, 2, 300).astype(np.uint8), к]) for к in кадры]
                              + [г.integers(0, 2, 300).astype(np.uint8)])

    def test_символы_состояния(self):
        """Через 72 бита, начиная с 70-го, — по два бита состояния; NID занимает биты 48–113."""
        к = p25.кадр_tsdu(0x293, [СЕТЬ])
        self.assertEqual([0, 1], к[70:72].tolist())
        self.assertEqual([0, 1], к[142:144].tolist())
        self.assertEqual(p25.закодировать_nid(0x293, 7).tolist(), np.delete(к[48:114], [22, 23]).tolist())

    def test_управляющий_канал(self):
        н = p25.найти(self.поток(*[p25.кадр_tsdu(0x293, [СЕТЬ, ВЫЗОВ])] * 3, p25.кадр_голоса(0x293, 5)))
        self.assertEqual("P25 фаза 1 (TIA-102): кадры, управляющий канал", н.что)
        текст = "\n".join(н.подробно)
        self.assertIn("NAC: 0x293 × 4", текст)
        self.assertIn("NET_STS_BCST (состояние сети), WACN 0xBEE00, SYSID 0x12, канал 0x3100 × 3", текст)
        self.assertIn("GRP_V_CH_GRANT (групповой вызов: канал), группа 9029, от 6785451, канал 0x1001 × 3", текст)
        self.assertIn("TSDU (управление) × 3, LDU1 (речь) × 1", текст)

    def test_последний_tsbk(self):
        """Бит LB у первого TSBK — второй не читается."""
        к = p25.кадры(self.поток(p25.кадр_tsdu(0x293, [СЕТЬ])))
        self.assertEqual(1, len(к[0]["tsbk"]))
        self.assertTrue(к[0]["tsbk"][0]["последний"])

    def test_последний_уже_у_первого(self):
        """Два верных TSBK подряд, но бит LB — у первого: второй не читается."""
        первый = bytes([СЕТЬ[0] | 0x80]) + СЕТЬ[1:]
        второй = bytes([ВЫЗОВ[0] | 0x80]) + ВЫЗОВ[1:]
        тело = np.concatenate([p25.закодировать_nid(0x293, 7), p25.закодировать_треллис(первый + p25.crc_tsbk(первый)),
                               p25.закодировать_треллис(второй + p25.crc_tsbk(второй))])
        кадр = np.concatenate([p25._бит_числа(p25.СИНХРО, 48), p25._со_состоянием(тело, 48)])
        к = p25.кадры(self.поток(кадр))
        self.assertEqual(["NET_STS_BCST (состояние сети)"], [п["операция"] for п in к[0]["tsbk"]])

    def test_бит_p_и_испорченный_tsbk(self):
        """Бит P (0x40) не входит в код операции; TSBK с неверной CRC не засчитывается."""
        с_p = bytes([СЕТЬ[0] | 0x40]) + СЕТЬ[1:]
        к = p25.кадры(self.поток(p25.кадр_tsdu(0x293, [с_p])))
        self.assertEqual((0x3B, "NET_STS_BCST (состояние сети)"), (к[0]["tsbk"][0]["код"], к[0]["tsbk"][0]["операция"]))
        кадр = p25.кадр_tsdu(0x293, [СЕТЬ])
        кадр[120:300] = np.random.default_rng(3).integers(0, 2, 180)
        к = p25.кадры(self.поток(кадр))
        self.assertEqual((7, []), (к[0]["duid"], к[0]["tsbk"]))

    def test_обратная_полярность_и_ошибки(self):
        б = self.поток(*[p25.кадр_tsdu(0x1A5, [ВЫЗОВ])] * 4)
        б ^= np.resize(np.array([1, 0], np.uint8), len(б))
        н = p25.найти(б)
        self.assertIn("полярность обратная", н.подробно[0])
        self.assertIn("NAC: 0x1A5 × 4", н.подробно[1])

    def test_не_p25(self):
        self.assertIsNone(p25.найти(np.random.default_rng(5).integers(0, 2, 200_000).astype(np.uint8)))
        self.assertIsNone(p25.найти(self.поток(p25.кадр_голоса(0x293), p25.кадр_голоса(0x293))))
        # Синхрослово есть, а NID — случайный: не кадры P25.
        г = np.random.default_rng(6)
        мусор = [np.concatenate([p25._бит_числа(p25.СИНХРО, 48), г.integers(0, 2, 400).astype(np.uint8)])
                 for _ in range(5)]
        self.assertIsNone(p25.найти(self.поток(*мусор)))

    def test_автомат(self):
        б = self.поток(*[p25.кадр_tsdu(0x293, [СЕТЬ, ВЫЗОВ])] * 4)
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("P25") for н in р.находки), [н.что for н in р.находки])


if __name__ == "__main__":
    unittest.main()
