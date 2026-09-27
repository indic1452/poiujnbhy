"""TETRA, нисходящий канал — как у osmo-tetra: пачки (tetra_burst.c), скремблер (tetra_scramb.c),
перемежение (tetra_interleave.c), выкалывание и материнский код (tetra_conv_enc.c), CRC (crc_simple.c,
TETRA_CRC_OK), RM(30, 14) (tetra_rm3014.c), SYNC и MAC-PDU (tetra_lower_mac.c, tetra_mac_pdu.c)."""

import unittest
import zlib

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import crc_katalog, sinhro, svyortka, tetra
from reportgen.potok.razbor import разобрать as разобрать_поток

СЕТЬ = tetra.Сеть(mcc=250, mnc=1234, цвет=17, код_системы=2)


def поле(v, n):
    return [(v >> (n - 1 - i)) & 1 for i in range(n)]


def sysinfo(несущая=3601, диапазон=4, смещение=1, la=1234, службы=(1 << 11) | (1 << 5) | (1 << 1), длина=124):
    б = [1, 0, 0, 0] + поле(несущая, 12) + поле(диапазон, 4) + поле(смещение, 2) + [0] * (длина - 22)
    б[82:96] = поле(la, 14)
    б[112:124] = поле(службы, 12)
    return б


def resource(шифр, тип_адреса, адрес, длина=124):
    б = [0, 0, 0, 0] + поле(шифр, 2) + [0] + поле(10, 6) + поле(тип_адреса, 3)
    б += поле(адрес, tetra.АДРЕСА[тип_адреса][1])
    return (б + [0] * длина)[:длина]


def поток(*пачки, сид=1, до=1000, после=700):
    г = np.random.default_rng(сид)
    return np.concatenate([г.integers(0, 2, до)] + list(пачки) + [г.integers(0, 2, после)]).astype(np.uint8)


def ячейка(сеть=СЕТЬ, tn=2, fn=5, mn=9):
    return [tetra.пачка_sb(сеть, sysinfo(), (3 << 12) | (5 << 6), tn=tn, fn=fn, mn=mn),
            tetra.пачка_ndb(сеть, [resource(1, 1, 0x12345, 268)], (2 << 12) | (4 << 6) | 0x21),
            tetra.пачка_ndb(сеть, [resource(2, 1, 777), sysinfo()], 0)]


class ОсновыTests(unittest.TestCase):
    def test_обучающие_как_в_каталоге_синхрослов(self):
        биты = {format(значение, f"0{длина}b") for имя, значение, длина in sinhro.ДРУГИЕ_СЛОВА if "TETRA" in имя}
        for имя, посл in (("n", tetra.N_БИТЫ), ("p", tetra.P_БИТЫ), ("q", tetra.Q_БИТЫ), ("y", tetra.Y_БИТЫ)):
            self.assertIn("".join(map(str, посл)), биты, имя)
        self.assertEqual((22, 22, 22, 38), tuple(map(len, (tetra.N_БИТЫ, tetra.P_БИТЫ, tetra.Q_БИТЫ, tetra.Y_БИТЫ))))

    def test_места_в_пачке_как_в_osmo(self):
        """Смещения в символах из tetra_burst.c (×2 бита) и длины полей build_*_burst — 510 бит."""
        self.assertEqual(((6 + 1 + 40) * 2, (6 + 1 + 40 + 60 + 19) * 2, (6 + 1 + 40 + 60 + 19 + 15) * 2),
                         (tetra.SB_БЛОК1, tetra.SB_BB, tetra.SB_БЛОК2))
        self.assertEqual(tetra.SB_БЛОК1 + 120, tetra.SB_Y)
        self.assertEqual(tetra.SB_Y + 38, tetra.SB_BB)
        self.assertEqual(510, tetra.SB_БЛОК2 + 216 + 2 + 10)
        self.assertEqual(((5 + 1 + 1) * 2, (5 + 1 + 1 + 108) * 2, (5 + 1 + 1 + 108 + 7 + 11) * 2,
                          (5 + 1 + 1 + 108 + 7 + 11 + 8) * 2),
                         (tetra.NDB_БЛОК1, tetra.NDB_BB1, tetra.NDB_BB2, tetra.NDB_БЛОК2))
        self.assertEqual(tetra.NDB_BB1 + 14, tetra.NDB_ОБУЧ)
        self.assertEqual(tetra.NDB_ОБУЧ + 22, tetra.NDB_BB2)
        self.assertEqual(510, tetra.NDB_БЛОК2 + 216 + 2 + 10)

    def test_параметры_блоков_как_tetra_blk_param(self):
        """type345, type2, type1, interleave_a — tetra_lower_mac.c: SB1, SB2/NDB, SCH/F."""
        self.assertEqual((120, 80, 60, 11), tetra.SB1)
        self.assertEqual((216, 144, 124, 101), tetra.ПОЛБЛОКА)
        self.assertEqual((432, 288, 268, 103), tetra.SCH_F)

    def test_скремблер_это_многочлен_crc32(self):
        """Отводы 32 26 23 … 1 — показатели порождающего многочлена CRC-32 (0x04C11DB7, из zlib)."""
        отражённый = 0xEDB88320
        self.assertEqual(zlib.crc32(b"a"), self._crc32(b"a", отражённый))
        прямой = int(format(отражённый, "032b")[::-1], 2)
        показатели = {32} | {i for i in range(1, 32) if прямой >> i & 1}
        self.assertEqual(показатели, set(tetra.ОТВОДЫ))
        self.assertEqual(1, прямой & 1)

    @staticmethod
    def _crc32(данные, отражённый):
        c = 0xFFFFFFFF
        for б in данные:
            c ^= б
            for _ in range(8):
                c = (c >> 1) ^ отражённый if c & 1 else c >> 1
        return c ^ 0xFFFFFFFF

    def test_скремблер_по_правилу(self):
        """Первые биты — прямо по next_lfsr_bit (бит = xor битов ST(lfsr, отвод) = lfsr >> (32 − отвод))."""
        р, ожидаемые = 3, []
        for _ in range(40):
            бит = 0
            for отвод in (32, 26, 23, 22, 16, 12, 11, 10, 8, 7, 5, 4, 2, 1):
                бит ^= (р >> (32 - отвод)) & 1
            р = (р >> 1) | (бит << 31)
            ожидаемые.append(бит)
        self.assertEqual(ожидаемые, tetra.гамма(3, 40).tolist())
        self.assertEqual(((17 | (1234 << 6) | (250 << 20)) << 2) | 3, tetra.код_скремблера(250, 1234, 17))
        self.assertEqual(3, tetra.код_скремблера(0, 0, 0))

    def test_crc_как_genibus(self):
        """CRC-16 ITU-T с 0xFFFF без отражений; с инверсией — CRC-16/GENIBUS (проверка 0xD64E), остаток 0x1D0F."""
        genibus = [x for x in crc_katalog.REVENG if x[0] == "CRC-16/GENIBUS"][0]
        биты = [int(c) for ch in b"123456789" for c in format(ch, "08b")]
        self.assertEqual(genibus[-1], tetra.crc16(биты) ^ 0xFFFF)
        данные = list(np.random.default_rng(1).integers(0, 2, 60))
        crc = tetra.crc16(данные) ^ 0xFFFF
        self.assertEqual(tetra.CRC_ОСТАТОК, tetra.crc16(данные + поле(crc, 16)))

    def test_перемежение_перестановка(self):
        for K, a in ((120, 11), (216, 101), (432, 103)):
            б = np.random.default_rng(K).integers(0, 2, K).astype(np.uint8)
            self.assertTrue(np.array_equal(б, tetra.разперемежить(tetra.перемежить(б, a), a)))
            self.assertEqual(K, len(set(((a * np.arange(1, K + 1)) % K).tolist())))
        б = np.arange(120) % 2
        self.assertEqual(б[11 % 120], tetra.разперемежить(б.astype(np.uint8), 11)[0])     # out[i−1] = in[k−1]

    def test_выкалывание_2_3(self):
        """Из каждых 8 бит материнского кода 1/4 остаются 1-й, 2-й, 3-й и 6-й … но по 3 на период: P = {1, 2, 5}."""
        места = tetra.места_выкалывания(12)
        self.assertEqual([0, 1, 4, 8, 9, 12, 16, 17, 20, 24, 25, 28], места.tolist())
        self.assertEqual(len(set(tetra.места_выкалывания(432).tolist())), 432)

    def test_материнский_код(self):
        """G1 = 1 + D + D⁴ … как conv_enc_in_bit: на единичный импульс — столбцы многочленов."""
        выход = svyortka.кодировать(np.array([1, 0, 0, 0, 0], np.uint8), list(tetra.МНОГОЧЛЕНЫ), 5).reshape(5, 4)
        self.assertEqual([[1, 1, 1, 1], [1, 0, 1, 1], [0, 1, 1, 0], [0, 1, 0, 1], [1, 1, 1, 1]], выход.tolist())

    def test_rm_30_14(self):
        """Расстояние 8: три ошибки исправляются, у каждого синдрома — одна ошибка веса ≤ 3."""
        self.assertEqual(8, min(bin(tetra.rm_кодировать(x)).count("1") for x in range(1, 1 << 14)))
        self.assertEqual(1 + 30 + 435 + 4060, len(tetra.RM_ТАБЛИЦА))
        с = tetra.rm_кодировать(0x2ABC)
        self.assertEqual(0x2ABC << 16, с & 0x3FFF0000)                    # данные — старшие 14 бит
        self.assertEqual((0x2ABC, 3), tetra.rm_декодировать(с ^ 0b10000000000001000000000000001))
        self.assertEqual((0x2ABC, 0), tetra.rm_декодировать(с))
        четыре = с ^ 0b1111
        декод = tetra.rm_декодировать(четыре)
        self.assertTrue(декод is None or декод[0] != 0x2ABC)

    def test_блок_туда_и_обратно(self):
        г = np.random.default_rng(3)
        for параметры in (tetra.SB1, tetra.ПОЛБЛОКА, tetra.SCH_F):
            данные = г.integers(0, 2, параметры[2]).tolist()
            блок = tetra.закодировать_блок(данные, 0x12345677, параметры)
            self.assertEqual(параметры[0], len(блок))
            self.assertEqual(данные, tetra.декодировать_блок(блок, 0x12345677, параметры).tolist())
            испорчено = блок.copy()
            испорчено[[5, 40, 90]] ^= 1
            self.assertEqual(данные, tetra.декодировать_блок(испорчено, 0x12345677, параметры).tolist())
            self.assertIsNone(tetra.декодировать_блок(блок, 0x12345673, параметры))       # чужой скремблер


class РазборTests(unittest.TestCase):
    def test_ячейка(self):
        р = tetra.разобрать(поток(*ячейка()))
        self.assertEqual([СЕТЬ], р.сети)
        self.assertEqual([(1000, "SB"), (1510, "NDB-1"), (2020, "NDB-2")], [(п.бит, п.вид) for п in р.пачки])
        sb, ndb1, ndb2 = р.пачки
        self.assertEqual((2, 5, 9), sb.время)
        self.assertEqual({"заголовок": "3", "вниз": "трафик (метка 5)", "вверх": "не назначен"}, sb.aach)
        self.assertEqual({"заголовок": "2", "вниз": "трафик (метка 4)", "поле 2": "доступ C, базовый кадр 1"},
                         ndb1.aach)
        self.assertEqual({"заголовок": "0: вниз — общее управление", "поле 1": "доступ A, базовый кадр 0",
                          "поле 2": "доступ A, базовый кадр 0"}, ndb2.aach)
        self.assertEqual([{"PDU": "SYSINFO", "несущая": "3601", "частота": "490.03125 МГц", "LA": "1234",
                           "службы": "регистрация обязательна, речь, шифрование эфира"}], sb.pdu)
        self.assertEqual([{"PDU": "MAC-RESOURCE", "шифрование": "класс 2 (SCK)", "адрес": "SSI", "SSI": "74565"}],
                         ndb1.pdu)
        self.assertEqual(["MAC-RESOURCE", "SYSINFO"], [x["PDU"] for x in ndb2.pdu])
        self.assertEqual("класс 3 (DCK/CCK)", ndb2.pdu[0]["шифрование"])

    def test_пачки_до_sb_тоже(self):
        """Ячейка найдена по SB, пачки перед ним — назад через 510 бит."""
        с = ячейка()
        р = tetra.разобрать(поток(с[1], с[2], с[0], с[2]))
        self.assertEqual(["NDB-1", "NDB-2", "SB", "NDB-2"], [п.вид for п in р.пачки])
        self.assertTrue(all(п.pdu for п in р.пачки))

    def test_кадр_18_у_aach(self):
        р = tetra.разобрать(поток(tetra.пачка_sb(СЕТЬ, sysinfo(), (1 << 12) | (0x25 << 6) | 0x13, fn=18)))
        self.assertEqual({"заголовок": "1", "поле 1": "доступ C, базовый кадр 5", "поле 2": "доступ B, базовый кадр 3"},
                         р.пачки[0].aach)

    def test_длина_базового_кадра_четыре_бита(self):
        р = tetra.разобрать(поток(tetra.пачка_sb(СЕТЬ, sysinfo(), (0 << 12) | (0x1C << 6) | 0x2F)))
        self.assertEqual(("доступ B, базовый кадр 12", "доступ C, базовый кадр 15"),
                         (р.пачки[0].aach["поле 1"], р.пачки[0].aach["поле 2"]))

    def test_пачка_до_конца_потока(self):
        self.assertEqual(3, len(tetra.разобрать(поток(*ячейка(), после=0)).пачки))

    def test_одна_сеть_в_двух_передачах(self):
        б = np.concatenate([поток(*ячейка()), поток(*ячейка(), сид=2)])
        р = tetra.разобрать(б)
        self.assertEqual(([СЕТЬ], 6), (р.сети, len(р.пачки)))

    def test_адреса_resource(self):
        for тип, адрес, ключ, ожидаем in ((2, 0x3FF, "метка", "1023"), (3, 5, "SSI", "5"), (0, 0, None, None),
                                          (5, (99 << 10) | 7, "SSI", "99")):
            пачка = tetra.пачка_ndb(СЕТЬ, [resource(0, тип, адрес, 268)], 0)
            п = tetra.разобрать(поток(ячейка()[0], пачка)).пачки[1].pdu[0]
            with self.subTest(тип=тип):
                self.assertEqual(tetra.АДРЕСА[тип][0], п["адрес"])
                if ключ:
                    self.assertEqual(ожидаем, п[ключ])
                self.assertEqual("нет", п["шифрование"])

    def test_виды_pdu(self):
        блоки = [[0, 1] + [0] * 122, [1, 0, 0, 1] + [0] * 120]
        р = tetra.разобрать(поток(ячейка()[0], tetra.пачка_ndb(СЕТЬ, блоки, 0)))
        self.assertEqual([{"PDU": "MAC-FRAG/END"}, {"PDU": "ACCESS-DEFINE"}], р.пачки[1].pdu)
        р = tetra.разобрать(поток(ячейка()[0], tetra.пачка_ndb(СЕТЬ, [[1, 1] + [0] * 266], 0)))
        self.assertEqual([{"PDU": "SUPPLEMENTARY"}], р.пачки[1].pdu)

    def test_частоты_и_службы(self):
        for смещение, мгц in ((0, "490.025 МГц"), (2, "490.01875 МГц"), (3, "490.0375 МГц")):
            п = tetra.разобрать_pdu(np.array(sysinfo(смещение=смещение)))
            self.assertEqual(мгц, п["частота"])
        п = tetra.разобрать_pdu(np.array(sysinfo(несущая=0, диапазон=3, смещение=0, службы=0)))
        self.assertEqual(("300 МГц", "нет"), (п["частота"], п["службы"]))
        п = tetra.разобрать_pdu(np.array(sysinfo(службы=0xFFF)))
        self.assertEqual(11, len(п["службы"].split(", ")))

    def test_ошибки_исправляются(self):
        б = поток(*ячейка())
        for база in (1000, 1510, 2020):
            б[база + np.array([20, 100, 300, 400])] ^= 1                 # по блокам
        б[1000 + tetra.SB_BB + np.array([0, 10, 20])] ^= 1              # три ошибки AACH
        р = tetra.разобрать(б)
        self.assertEqual([1, 1, 2], [len(п.pdu) for п in р.пачки])
        self.assertEqual("трафик (метка 5)", р.пачки[0].aach["вниз"])

    def test_обучающие_с_ошибками(self):
        for ошибок, пачек in ((2, 3), (3, 0)):
            б = поток(*ячейка())
            б[1000 + tetra.SB_Y + np.arange(ошибок) * 5] ^= 1
            with self.subTest(ошибок=ошибок):
                self.assertEqual(пачек, len(tetra.разобрать(б).пачки))
        б = поток(*ячейка())
        б[1510 + tetra.NDB_ОБУЧ + np.arange(3) * 5] ^= 1                # NDB с испорченной обучающей — конец ячейки
        self.assertEqual(["SB"], [п.вид for п in tetra.разобрать(б).пачки])

    def test_sync_с_неверной_crc(self):
        б = поток(*ячейка())
        б[1000 + tetra.SB_БЛОК1 + np.arange(0, 120, 6)] ^= 1
        self.assertEqual([], tetra.разобрать(б).сети)

    def test_две_ячейки(self):
        другая = tetra.Сеть(mcc=262, mnc=99, цвет=1, код_системы=0)
        б = np.concatenate([поток(*ячейка()), поток(*ячейка(другая), сид=2)])
        н = tetra.найти(б)
        self.assertEqual({"пачек": 6, "сетей": 2, "pdu": 8}, н.свойства)
        self.assertTrue(any("MCC 262, MNC 99, цвет 1" in с for с in н.подробно))

    def test_сводка(self):
        н = tetra.найти(поток(*ячейка()))
        текст = "\n".join(н.подробно)
        for часть in ("сеть: MCC 250, MNC 1234, цвет 17, код системы 2; скремблер ячейки 0x3E84D247",
                      "пачек: NDB-1 × 1, NDB-2 × 1, SB × 1; AACH (RM(30, 14)) разобрано: 3",
                      "время SYNC (TN, FN, MN): 2/5/9", "MAC-PDU с верной CRC: SYSINFO × 2, MAC-RESOURCE × 2",
                      "адреса (SSI): 74565 × 1, 777 × 1",
                      "SYSINFO: несущая 3601 (490.03125 МГц), LA 1234; службы: регистрация обязательна, речь",
                      "AACH, вниз: трафик (метка 5) × 1, трафик (метка 4) × 1"):
            self.assertIn(часть, текст)

    def test_случайные_биты(self):
        self.assertIsNone(tetra.найти(np.random.default_rng(5).integers(0, 2, 1 << 20).astype(np.uint8)))

    def test_автомат(self):
        б = поток(*ячейка())
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("TETRA") for н in р.находки), [н.что for н in р.находки])


class ВитербиTests(unittest.TestCase):
    def test_начало_и_конец_в_нуле(self):
        """Две ошибки у начала (места 0, 16) и у конца (281, 288) материнского кода блока SB1: исправляются,
        только если путь начинается и кончается в нуле (найдено перебором)."""
        г = np.random.default_rng(0)
        данные = np.concatenate([г.integers(0, 2, 76), np.zeros(4)]).astype(np.uint8)
        мать = svyortka.кодировать(данные, list(tetra.МНОГОЧЛЕНЫ), 5)
        известно = np.zeros(320, np.uint8)
        известно[tetra.места_выкалывания(120)] = 1
        for места in ((0, 16), (281, 288)):
            x = мать.copy()
            x[list(места)] ^= 1
            with self.subTest(места=места):
                self.assertEqual(данные.tolist(), svyortka.витерби_с_нуля(x, tetra.МНОГОЧЛЕНЫ, 5, известно).tolist())

    def test_с_нуля_и_стирания(self):
        г = np.random.default_rng(7)
        данные = np.concatenate([г.integers(0, 2, 60), np.zeros(4)]).astype(np.uint8)
        код = svyortka.кодировать(данные, list(tetra.МНОГОЧЛЕНЫ), 5)
        известно = (г.random(len(код)) > 0.4).astype(np.uint8)
        принято = np.where(известно == 1, код, 1 - код)                 # стёртые — заведомо неверны
        self.assertEqual(данные.tolist(), svyortka.витерби_с_нуля(принято, tetra.МНОГОЧЛЕНЫ, 5, известно).tolist())


if __name__ == "__main__":
    unittest.main()
