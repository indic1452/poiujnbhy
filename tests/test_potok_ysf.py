"""System Fusion: Golay (24, 12) — как ENCODING_TABLE_24128 MMDVMHost (SHA-256 таблицы по исходнику), места
перемежения FICH и канала данных — как INTERLEAVE_TABLE и INTERLEAVE_TABLE_9_20 (формулы совпали с таблицами
дословно), свёртка K = 5 (YSFConvolution.cpp), CRC (CRC-16/GSM), WHITENING_DATA, поля FICH (YSFFICH.cpp)."""

import hashlib
import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import ysf
from reportgen.potok.razbor import разобрать as разобрать_поток


def поток(*кадры, сид=1):
    г = np.random.default_rng(сид)
    return np.concatenate([np.concatenate([г.integers(0, 2, 138).astype(np.uint8), к]) for к in кадры]
                          + [г.integers(0, 2, 200).astype(np.uint8)])


ВЫЗОВ = [ysf.кадр(0, получатель="ALL", отправитель="R3ABC")] + [ysf.кадр(1, fn=i) for i in range(5)] \
    + [ysf.кадр(2, получатель="ALL", отправитель="R3ABC")]


class КодыTests(unittest.TestCase):
    def test_голей_как_encoding_table_24128(self):
        self.assertEqual([0x000000, 0x0018EB, 0x00293E, 0x0031D5, 0x004A97], [ysf.golay24(д) for д in range(5)])
        таблица = b"".join(int(с).to_bytes(3, "big") for с in ysf.СЛОВА_ГОЛЕЯ)
        self.assertEqual("3e6ed79f415bceb6ae45cbfc0e10622fc123a7d55b854a2b9ea3d6c5125df80c",
                         hashlib.sha256(таблица).hexdigest())

    def test_голей_исправляет_три(self):
        слово = ysf.golay24(0xABC)
        self.assertEqual((0xABC, 3), ysf.голей_декодировать(слово ^ 0b100000010000000000000001))

    def test_места_перемежения(self):
        self.assertEqual([0, 40, 80, 120, 160, 2, 42], ysf.МЕСТА_FICH[:7])
        self.assertEqual([0, 40, 80, 120, 160, 200, 240, 280, 320, 2], ysf.МЕСТА_ДАННЫХ[:10])

    def test_свёртка(self):
        """1+D³+D⁴ первым, 1+D+D²+D⁴ вторым: единица → 11 01 01 10 11 (отклик по степеням D⁰…D⁴)."""
        self.assertEqual([1, 1, 0, 1, 0, 1, 1, 0, 1, 1], ysf.свёртка(np.array([1, 0, 0, 0, 0], np.uint8)).tolist())

    def test_crc(self):
        self.assertEqual(bytes.fromhex("ce3c"), ysf.crc16(b"123456789"))


class КадрыTests(unittest.TestCase):
    def test_fich_и_позывные(self):
        к = ysf.кадры(поток(*ВЫЗОВ))
        self.assertEqual([0, 1, 1, 1, 1, 1, 2], [x["fi"] for x in к])
        self.assertEqual(("ALL", "R3ABC"), к[0]["позывные"])
        self.assertEqual([0, 1, 2, 3, 4], [x["fn"] for x in к[1:6]])
        self.assertEqual((2, 6, 0), (к[0]["dt"], к[0]["ft"], к[0]["cm"]))

    def test_voip_не_в_dt(self):
        к = ysf.fich(ysf.кадр(1, dt=2, voip=True))
        self.assertEqual((2, True), (к["dt"], к["voip"]))

    def test_хвост_свёртки_в_нуле(self):
        """Путь Витерби кончается в нулевом состоянии (4 бита хвоста): ошибки у конца FICH исправляются."""
        к = ysf.кадр(1, fn=3)
        к[[76, 77, 116]] ^= 1
        self.assertEqual(3, ysf.fich(к)["fn"])

    def test_испорченные_позывные(self):
        к = ysf.кадр(0, получатель="ALL", отправитель="R3ABC")
        к[240:240 + 72] = np.random.default_rng(8).integers(0, 2, 72)
        self.assertIsNotNone(ysf.fich(к))
        self.assertIsNone(ysf.позывные(к))

    def test_найти(self):
        н = ysf.найти(поток(*ВЫЗОВ))
        текст = "\n".join(н.подробно)
        self.assertIn("виды кадров (FI): связь × 5, заголовок × 1, конец × 1", текст)
        self.assertIn("позывные (отправитель → получатель): R3ABC → ALL × 2", текст)
        self.assertIn("тип данных (DT): речь и данные, режим 2 × 7", текст)

    def test_ошибки_и_полярность(self):
        б = поток(*ВЫЗОВ)
        г = np.random.default_rng(3)
        for м in [x["место"] for x in ysf.кадры(б)]:
            б[м + 40 + г.choice(200, 3, replace=False)] ^= 1                  # 3 ошибки в FICH
        к = ysf.кадры(б)
        self.assertEqual(7, len(к))
        б ^= np.resize(np.array([1, 0], np.uint8), len(б))                  # +3 ↔ −3 (кадры с чётных мест)
        н = ysf.найти(б)
        self.assertIn("кадров YSF с верным FICH (Golay + свёртка + CRC): 7 — полярность обратная", н.подробно[0])

    def test_не_ysf(self):
        self.assertIsNone(ysf.найти(np.random.default_rng(5).integers(0, 2, 100_000).astype(np.uint8)))
        self.assertIsNone(ysf.найти(поток(*ВЫЗОВ[:2])))
        г = np.random.default_rng(6)
        мусор = [np.concatenate([ysf._бит_числа(ysf.СИНХРО, 40), г.integers(0, 2, 920).astype(np.uint8)])
                 for _ in range(5)]
        self.assertIsNone(ysf.найти(поток(*мусор)))

    def test_автомат(self):
        б = поток(*ВЫЗОВ)
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("System Fusion") for н in р.находки), [н.что for н in р.находки])


if __name__ == "__main__":
    unittest.main()
