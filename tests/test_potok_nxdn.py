"""NXDN: скремблер (SCRAMBLER MMDVMHost — PN9 x⁹+x⁵+1 по знаку символов после FSW), LICH (getParity),
SACCH (INTERLEAVE_TABLE, PUNCTURE_LIST, свёртка K = 5, CRC-6), вызов из сверхкадра (NXDNLayer3.cpp)."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import nxdn
from reportgen.potok.razbor import разобрать as разобрать_поток


def поток(*кадры, сид=1, промежуток=0):
    """Кадры подряд (как в эфире) между случайными битами; ``промежуток`` — случайные биты между кадрами."""
    г = np.random.default_rng(сид)
    return np.concatenate([г.integers(0, 2, 100).astype(np.uint8)]
                          + [np.concatenate([г.integers(0, 2, промежуток).astype(np.uint8), к]) for к in кадры]
                          + [г.integers(0, 2, 200).astype(np.uint8)])


class СоставTests(unittest.TestCase):
    def test_скремблер_это_pn9(self):
        """Меняет только первый бит дибита (знак символа); после FSW (10 символов) — PN9 x⁹+x⁵+1."""
        б = nxdn.СКРЕМБЛЕР
        self.assertEqual(0, int(б[1::2].sum()))
        s, pn = 0b001001110, []
        for _ in range(182):
            pn.append((s >> 8) & 1)
            s = ((s << 1) | (((s >> 8) ^ (s >> 4)) & 1)) & 0x1FF
        self.assertEqual(pn, б[0::2][10:].tolist())
        self.assertEqual(0, int(б[:20].sum()))

    def test_чётность_lich(self):
        self.assertEqual([1, 1, 0, 0, 0], [nxdn.чётность_lich(x) for x in (0x80, 0xB2, 0x90, 0xA0, 0x00)])

    def test_выколотые_как_puncture_list(self):
        self.assertEqual([5, 11, 17, 23, 29, 35, 41, 47, 53, 59, 65, 71], nxdn.ВЫКОЛОТЫ)

    def test_хвост_и_конечное_состояние(self):
        """Три ошибки у конца SACCH исправляются, только если путь кончается в нуле и хвост — 8 нулей."""
        для_проверки = nxdn.кадр(sr=1, ran=33, данные18=0x3FFFF) ^ nxdn.СКРЕМБЛЕР
        for места in ((40, 50, 70), (60, 65, 75)):
            with self.subTest(места=места):
                x = для_проверки.copy()
                x[list(места)] ^= 1
                с = nxdn.sacch(x)
                self.assertEqual((33, 0x3FFFF), (с["ran"], с["данные"]))

    def test_места_sacch(self):
        self.assertEqual([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 1, 6], nxdn.МЕСТА_SACCH[:14])

    def test_crc6(self):
        """CRC-6 (0x27, начальное 0x3F): 6 бит, любая одиночная и любая соседняя двойная ошибка видна."""
        ядро = np.array([int(с) for с in format(0x2ABCDEF, "026b")], np.uint8)
        crc = nxdn.crc6(ядро)
        self.assertLess(crc, 64)
        for i in range(26):
            for j in (i, min(i + 1, 25)):
                испорчено = ядро.copy()
                испорчено[[i, j] if i != j else [i]] ^= 1
                self.assertNotEqual(crc, nxdn.crc6(испорчено), (i, j))
        # Нули: регистр 0x3F сдвигается с обратной связью 0x27 — 26 шагов.
        c = 0x3F
        for _ in range(26):
            c = ((c << 1) ^ (0x27 if c & 0x20 else 0)) & 0x3F
        self.assertEqual(c, nxdn.crc6(np.zeros(26, np.uint8)))


class КадрыTests(unittest.TestCase):
    def test_lich_и_sacch(self):
        к = nxdn.кадры(поток(nxdn.кадр(rfct=1, fct=2, опция=1, направление=1, sr=2, ran=37, данные18=0x2ABCD)))
        self.assertEqual((1, 2, 1, 1, 2, 37, 0x2ABCD),
                         tuple(к[0][x] for x in ("rfct", "fct", "опция", "направление", "sr", "ran", "данные")))

    def test_вызов_из_сверхкадра(self):
        н = nxdn.найти(поток(*nxdn.вызов(1234, 20, True), *nxdn.вызов(501, 7, False, тип=0x08)))
        текст = "\n".join(н.подробно)
        self.assertIn("VCALL (речевой вызов): 1234 → группа 20", текст)
        self.assertIn("TX_REL (конец передачи): 501 → 7", текст)
        self.assertIn("RAN: 1 × 8", текст)

    def test_sacch_и_без_суперкадра(self):
        """FCT 0 (без суперкадра) и 3 (простой) — SACCH тоже разбирается; UDCH (1) — нет."""
        for fct, есть in ((0, True), (3, True), (1, False)):
            к = nxdn.кадры(поток(nxdn.кадр(fct=fct, ran=9)))
            self.assertEqual(есть, "ran" in к[0], fct)

    def test_сверхкадр_только_по_порядку(self):
        """Четыре SACCH не в порядке SR 3, 2, 1, 0 — вызова нет."""
        кадры = nxdn.вызов(1234, 20)
        н = nxdn.найти(поток(кадры[1], кадры[0], кадры[2], кадры[3]))
        self.assertEqual(0, н.свойства["вызовов"])

    def test_ошибки_sacch_исправляются(self):
        кадр = nxdn.кадр(sr=0, ran=5, данные18=0x12345)
        кадр[[40, 60, 80]] ^= 1
        к = nxdn.кадры(поток(кадр))
        self.assertEqual((5, 0x12345), (к[0]["ran"], к[0]["данные"]))

    def test_неверная_чётность_lich(self):
        кадр = nxdn.кадр()
        кадр[34] ^= 1                                          # бит чётности LICH (8-й)
        self.assertEqual([], nxdn.кадры(поток(кадр)))

    def test_обратная_полярность(self):
        б = поток(*nxdn.вызов(1234, 20))
        б ^= np.resize(np.array([1, 0], np.uint8), len(б))
        self.assertIn("полярность обратная", nxdn.найти(б).подробно[0])

    def test_не_nxdn(self):
        self.assertIsNone(nxdn.найти(np.random.default_rng(5).integers(0, 2, 100_000).astype(np.uint8)))
        self.assertIsNone(nxdn.найти(поток(nxdn.кадр(), nxdn.кадр())))

    def test_одиночные_совпадения_не_nxdn(self):
        """Кадры не через 384 бита (случайные совпадения FSW с верной чётностью) — не NXDN;
        те же кадры подряд — NXDN, и сосед засчитывается и через кадр."""
        кадры = nxdn.вызов(1234, 20) * 2
        self.assertEqual(8, len(nxdn.кадры(поток(*кадры, промежуток=100))))
        self.assertIsNone(nxdn.найти(поток(*кадры, промежуток=100)))
        self.assertEqual(8, nxdn.найти(поток(*кадры)).свойства["кадров"])
        испорчены = [к.copy() for к in кадры]
        for к in испорчены[1::2]:
            к[34] ^= 1                                     # у каждого второго — неверная чётность LICH
        self.assertEqual(4, nxdn.найти(поток(*испорчены)).свойства["кадров"])

    def test_автомат(self):
        б = поток(*nxdn.вызов(1234, 20) * 2)
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("NXDN") for н in р.находки), [н.что for н in р.находки])


if __name__ == "__main__":
    unittest.main()
