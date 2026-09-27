"""VDL Mode 2 — как у dumpvdl2: преамбула из фаз pr_phase и кода Грея demod.c, скремблер
x¹⁵ + x + 1 с 0x6959, заголовок (H и syndtable), RS(255, 249) с 0x187 и первым корнем 120
(rs.c: init_rs_char(8, 0x187, 120, 1, 6, 0)), перемежение deinterleave, кадры AVLC."""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import rs_bch, vdl2
from reportgen.potok.razbor import разобрать as разобрать_поток

ЗЕМЛЯ = vdl2.адрес(0x10A3F2, 4)
БОРТ = vdl2.адрес(0xABCDEF, 1, 1, последний=True)


def с_чётностью(текст: bytes) -> bytes:
    return bytes(б | 0x80 if bin(б).count("1") % 2 == 0 else б for б in текст)


ACARS = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x10, b"\xff\xff\x01" + с_чётностью(b"2.N12345\x15H14\x02M01AAB1234HELLO\x03"))
XID = vdl2.кадр_avlc(vdl2.адрес(0xFFFFFF, 7), vdl2.адрес(0x10A3F2, 4, 0, True), 0xAF, bytes(range(40)))


def поток(биты, сид=1, до=333, после=400):
    г = np.random.default_rng(сид)
    return np.concatenate([г.integers(0, 2, до), биты, г.integers(0, 2, после)]).astype(np.uint8)


class ОсновыTests(unittest.TestCase):
    def test_преамбула_из_фаз(self):
        """Приращения фаз pr_phase (в π/4) через код Грея dumpvdl2 — 15 символов по 3 бита."""
        self.assertEqual("010 011 110 000 001 101 110 001 100 011 111 101 111 100 010".replace(" ", ""),
                         "".join(map(str, vdl2.ПРЕАМБУЛА.tolist())))

    def test_скремблер_максимальной_длины(self):
        """x¹⁵ + x + 1 примитивен: период 32767 = 7·31·151, не меньше."""
        г = vdl2.гамма(2 * 32767 + 10)
        self.assertTrue(np.array_equal(г[:32767 + 10], г[32767:]))
        for делитель in (32767 // 7, 32767 // 31, 32767 // 151):
            self.assertFalse(np.array_equal(г[:делитель], г[делитель:2 * делитель]), делитель)
        self.assertEqual(16384, int(г[:32767].sum()))
        lfsr, первые = 0x6959, []
        for _ in range(16):                           # первые биты — прямо по правилу bitstream_descramble
            бит = (lfsr ^ (lfsr >> 14)) & 1
            lfsr = (lfsr >> 1) | (бит << 14)
            первые.append(бит)
        self.assertEqual(первые, г[:16].tolist())

    def test_таблица_синдромов(self):
        """У каждой записи синдром — её номер; все 25 одиночных ошибок исправляются."""
        for с, ошибка in enumerate(vdl2.ТАБЛИЦА_СИНДРОМОВ):
            self.assertEqual(с, vdl2.синдром(ошибка))
        for i in range(25):
            self.assertEqual(1 << i, vdl2.ТАБЛИЦА_СИНДРОМОВ[vdl2.синдром(1 << i)], i)

    def test_проверочных_и_раскладка(self):
        self.assertEqual([0, 0, 2, 2, 4, 4, 6], [vdl2.проверочных(x) for x in (1, 2, 3, 30, 31, 67, 68)])
        self.assertEqual((249, 1, 249, 6), vdl2.раскладка(8 * 249))
        self.assertEqual((250, 2, 1, 6), vdl2.раскладка(8 * 249 + 1))
        self.assertEqual((40, 1, 40, 4), vdl2.раскладка(313))
        self.assertEqual((0, 0, 249, 0), vdl2.раскладка(0))

    def test_порядок_перемежения(self):
        self.assertEqual([(0, 0), (1, 0), (0, 1), (1, 1), (0, 2)], vdl2._места(2, 3, 5, 0))
        self.assertEqual([(0, 10), (1, 10), (0, 11), (1, 11), (0, 12), (1, 12)], vdl2._места(2, 3, 6, 10))

    def test_адрес_как_parse_dlc_addr(self):
        for номер, вид, сост in ((0xABCDEF, 1, 1), (0x10A3F2, 4, 0), (0xFFFFFF, 7, 1), (1, 5, 0)):
            б = vdl2.адрес(номер, вид, сост)
            v = (б[0] >> 1) | (б[1] << 6) | (б[2] << 13) | ((б[3] & 0xFE) << 20)
            v = int(format(v & ((1 << 28) - 1), "028b")[::-1], 2)
            self.assertEqual((номер, вид, сост), (v & 0xFFFFFF, (v >> 24) & 7, v >> 27))
        self.assertEqual((0, 1), (vdl2.адрес(1, 1)[3] & 1, vdl2.адрес(1, 1, последний=True)[3] & 1))

    def test_fcs(self):
        self.assertEqual(0x906E ^ 0xFFFF, vdl2.fcs(b"123456789"))           # CRC-16/X.25 «check» = 0x906E
        self.assertEqual(vdl2.FCS_ВЕРНА, vdl2.fcs(ACARS))


class ПачкиTests(unittest.TestCase):
    def test_кадры(self):
        пп = vdl2.пачки(поток(vdl2.закодировать([ACARS, XID])))
        self.assertEqual(1, len(пп))
        п = пп[0]
        self.assertEqual((333 + 45, False, 0, 0), (п.бит, п.сопряжение, п.синдром, п.исправлено))
        self.assertEqual([ACARS, XID], [к.октеты for к in п.кадры])
        self.assertEqual({"от": "ABCDEF (борт)", "кому": "10A3F2 (наземная станция)", "состояние": "в воздухе",
                          "вид": "ответ", "кадр": "I (N(S) 0, N(R) 0)",
                          "ACARS": "N12345 [H1] блок 4, сообщение M01A, рейс AB1234: HELLO"}, п.кадры[0].поля)
        self.assertEqual(("10A3F2 (наземная станция)", "FFFFFF (всем)", "U XID", "команда"),
                         tuple(п.кадры[1].поля[x] for x in ("от", "кому", "кадр", "вид")))
        self.assertTrue(п.кадры[1].поля["данные"].startswith("00 01 02") and п.кадры[1].поля["данные"].endswith("…"))

    def test_длина_из_заголовка_отсекает_дополнение(self):
        """Дополнение до октета единицами (7 бит): без отсечения по длине — 7 единиц подряд, ошибка."""
        for добавить in range(20):
            кадр = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x10, bytes([0x5A]) * добавить)
            б = vdl2.закодировать([кадр], заполнение=1)
            if vdl2.заголовок(б[45:70] ^ vdl2.гамма(25))[0] % 8 == 1:
                break
        self.assertEqual([кадр], [к.октеты for п in vdl2.пачки(поток(б)) for к in п.кадры])

    def test_пачка_до_конца_потока(self):
        б = vdl2.закодировать([ACARS])
        self.assertEqual(1, len(vdl2.пачки(поток(б, после=0))))
        self.assertEqual([], vdl2.пачки(поток(б[:-1], после=0)))

    def test_виды_управления(self):
        """S: RR, RNR, REJ, SREJ и N(R); U: команда по (поле >> 2) & 0x3B — бит P/F не мешает."""
        управления = [0x01 | (1 << 5), 0x05 | (2 << 5), 0x09 | (7 << 5), 0x0D, 0x43, 0x53, 0x13, 0x0F, 0x1F,
                      0x63, 0x87, 0xAF, 0xE3, 0xFF, 0x0E | (3 << 5)]
        кадры = [vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, у) for у in управления]
        п = vdl2.пачки(поток(vdl2.закодировать(кадры)))[0]
        self.assertEqual(["S RR (N(R) 1)", "S RNR (N(R) 2)", "S REJ (N(R) 7)", "S SREJ (N(R) 0)", "U DISC",
                          "U DISC", "U UI", "U DM", "U DM", "U UA", "U FRMR", "U XID", "U TEST", "U 0x3B",
                          "I (N(S) 7, N(R) 3)"],
                         [к.поля["кадр"] for к in п.кадры])

    def test_несколько_блоков_rs(self):
        """Больше 249 октетов — два блока; ошибки в обоих блоках исправляются."""
        кадры = [vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x10, bytes([i]) * 150) for i in range(3)]
        б = vdl2.закодировать(кадры)
        длина = vdl2.заголовок(б[45:70] ^ vdl2.гамма(25))[0]
        октетов, блоков, последний, _ = vdl2.раскладка(длина)
        self.assertEqual(2, блоков)
        б = поток(б)
        for i in (0, 1, 2 * 100, 2 * 100 + 1, 2 * 110 + 1):         # октеты данных по столбцам: блок = i % 2
            б[место(i) + 3] ^= 1
        п = vdl2.пачки(б)[0]
        self.assertEqual((кадры, 5), ([к.октеты for к in п.кадры], п.исправлено))

    def test_неполный_блок_с_двумя_проверочными(self):
        """Последний блок 3–30 октетов: 2 проверочных, 4 стёрты — одна ошибка исправима, две — нет."""
        кадр = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x13)
        б = vdl2.закодировать([кадр])
        self.assertEqual(2, vdl2.раскладка(vdl2.заголовок(б[45:70] ^ vdl2.гамма(25))[0])[3])
        for ошибок, есть in ((1, 1), (2, 0)):
            испорчено = поток(б)
            for i in range(ошибок):
                испорчено[место(2 + 3 * i) + 1] ^= 1
            with self.subTest(ошибок=ошибок):
                пп = vdl2.пачки(испорчено)
                self.assertEqual(есть, len(пп))
                if пп:
                    self.assertEqual(1, пп[0].исправлено)                  # стёртые проверочные не в счёт

    def test_хвост_короче_трёх_октетов_без_проверочных(self):
        """Октетов 249·k + 1…2: последний блок без проверочных, строк проверочных на одну меньше."""
        for добавить in range(0, 40):
            кадр = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x10, bytes(range(200)) + bytes([0x55]) * (30 + добавить))
            б = vdl2.закодировать([кадр])
            октетов, блоков, последний, проверочных = vdl2.раскладка(vdl2.заголовок(б[45:70] ^ vdl2.гамма(25))[0])
            if последний < 3:
                break
        self.assertEqual((2, 6), (блоков, проверочных))
        self.assertEqual([кадр], [к.октеты for п in vdl2.пачки(поток(б)) for к in п.кадры])

    def test_ошибка_заголовка_исправляется(self):
        """Ошибка в любом бите длины или проверки исправляется; резервные биты обнуляются до проверки."""
        for i in range(25):
            б = поток(vdl2.закодировать([ACARS]))
            б[333 + 45 + i] ^= 1
            with self.subTest(i=i):
                п = vdl2.пачки(б)
                self.assertEqual(1, len(п))
                self.assertEqual(0 if i < 3 else vdl2.синдром(1 << (24 - i)), п[0].синдром)

    def test_заголовок_отбраковка(self):
        def слово(длина, резерв=0):
            """Проверочные — при нулевых резервных (как у передатчика); затем резервные искажаются."""
            с = int(format(длина, "017b")[::-1], 2) << 5
            с |= next(x for x in range(32) if vdl2.синдром(с | x) == 0)
            return np.array([int(b) for b in format(с | (резерв << 22), "025b")], dtype=np.uint8)

        self.assertEqual((0x3FFF, 0), vdl2.заголовок(слово(0x3FFF)))
        self.assertIsNone(vdl2.заголовок(слово(0x4000)))
        с = слово(0x2000)
        self.assertEqual((0x2000, 0), vdl2.заголовок(с))
        с[24] ^= 1
        self.assertIsNone(vdl2.заголовок(с))                         # исправленный и длиннее 0x1FFF
        с = слово(0x1FFF)
        с[24] ^= 1
        self.assertEqual(0x1FFF, vdl2.заголовок(с)[0])
        self.assertEqual((100, 0), vdl2.заголовок(слово(100, резерв=5)))  # резервные обнуляются до проверки
        # две ошибки в длине и проверке, синдром которых указывает на резервный бит, — отказ
        for резервный in (22, 23, 24):
            i, j = next((i, j) for i in range(22) for j in range(i + 1, 22)
                        if vdl2.синдром((1 << i) | (1 << j)) == vdl2.синдром(1 << резервный))
            с = слово(100)
            с[[24 - i, 24 - j]] ^= 1
            self.assertIsNone(vdl2.заголовок(с), резервный)

    def test_преамбула_с_ошибками(self):
        """До трёх ошибок — в любых местах: 5, 20, 30 портят по куску при делении и на 11, и на 12 бит,
        цел лишь четвёртый кусок 33–43."""
        for места, есть in (([0, 7, 14], 1), ([5, 20, 30], 1), ([33, 38, 44], 1), ([0, 7, 14, 21], 0)):
            б = поток(vdl2.закодировать([ACARS]))
            б[333 + np.array(места)] ^= 1
            with self.subTest(места=места):
                self.assertEqual(есть, len(vdl2.пачки(б)))

    def test_сопряжённый_спектр(self):
        for до in (333, 334, 335):
            б = поток(vdl2.сопрячь(np.concatenate([vdl2.закодировать([ACARS]), np.zeros(1, np.uint8)])), до=до)
            with self.subTest(до=до):
                пп = vdl2.пачки(б)
                self.assertEqual([(True, до + 45)], [(п.сопряжение, п.бит) for п in пп])
        н = vdl2.найти(поток(vdl2.сопрячь(np.concatenate([vdl2.закодировать([ACARS]), [0]]))))
        self.assertIn("спектр сопряжён", н.подробно[0])

    def test_сопряжение_по_символам(self):
        """Символ с приращением k (биты — код Грея) → приращение 8 − k."""
        for k in range(8):
            прямо = [(vdl2.ГРЕЙ[k] >> 2) & 1, (vdl2.ГРЕЙ[k] >> 1) & 1, vdl2.ГРЕЙ[k] & 1]
            обратно = [(vdl2.ГРЕЙ[(8 - k) % 8] >> 2) & 1, (vdl2.ГРЕЙ[(8 - k) % 8] >> 1) & 1, vdl2.ГРЕЙ[(8 - k) % 8] & 1]
            self.assertEqual(обратно, vdl2.сопрячь(np.array(прямо, np.uint8)).tolist(), k)

    def test_сопряжение_дважды_тождественно(self):
        б = np.random.default_rng(3).integers(0, 2, 300).astype(np.uint8)
        self.assertTrue(np.array_equal(б, vdl2.сопрячь(vdl2.сопрячь(б))))
        self.assertFalse(np.array_equal(б, vdl2.сопрячь(б)))

    def test_короткий_acars(self):
        """Меньше 12 знаков до ETX — полей нет, текст как есть."""
        кадр = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x10, b"\xff\xff\x01" + с_чётностью(b"2.N12345\x15H1\x03"))
        п = vdl2.пачки(поток(vdl2.закодировать([кадр])))[0]
        self.assertEqual("2.N12345\x15H1", п.кадры[0].поля["ACARS"])

    def test_неверная_fcs(self):
        плохой = ACARS[:-1] + bytes([ACARS[-1] ^ 1])
        п = vdl2.пачки(поток(vdl2.закодировать([плохой, XID])))
        self.assertEqual([XID], [к.октеты for к in п[0].кадры])
        self.assertEqual([], vdl2.пачки(поток(vdl2.закодировать([плохой]))))

    def test_короткий_кадр(self):
        короткий = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ[:3], 0x13)
        self.assertEqual(10, len(короткий))
        self.assertEqual([], vdl2.пачки(поток(vdl2.закодировать([короткий]))))

    def test_битстаффинг(self):
        кадр = vdl2.кадр_avlc(ЗЕМЛЯ, БОРТ, 0x10, b"\xff" * 20 + b"\x7e\x7e")
        self.assertEqual([кадр], [к.октеты for к in vdl2.пачки(поток(vdl2.закодировать([кадр])))[0].кадры])

    def test_кадры_hdlc(self):
        флаг = [0, 1, 1, 1, 1, 1, 1, 0]
        self.assertIsNone(vdl2.кадры_hdlc(np.array(флаг + [1] * 7 + флаг, np.uint8)))
        self.assertIsNone(vdl2.кадры_hdlc(np.array(флаг + [1, 0, 1] + флаг, np.uint8)))      # не октеты
        # ровно семь единиц — ошибка, даже если дальше всё складно
        self.assertIsNone(vdl2.кадры_hdlc(np.array(флаг + [1] * 7 + [0] * 9 + флаг, np.uint8)))
        байт = [1, 0, 1, 1, 0, 0, 0, 0]                                                      # 0x0D
        self.assertEqual([b"\x0d"], vdl2.кадры_hdlc(np.array(флаг + байт + флаг + флаг, np.uint8)))

    def test_случайные_биты(self):
        self.assertIsNone(vdl2.найти(np.random.default_rng(5).integers(0, 2, 300_000).astype(np.uint8)))

    def test_сводка(self):
        б = np.concatenate([поток(vdl2.закодировать([ACARS, XID])), поток(vdl2.закодировать([ACARS]), сид=2)])
        н = vdl2.найти(б)
        self.assertEqual({"пачек": 2, "кадров": 3, "отправителей": 2}, н.свойства)
        self.assertIn("отправители: ABCDEF (борт) × 2, 10A3F2 (наземная станция) × 1", н.подробно)
        self.assertIn("кадры: I × 2, U XID × 1", н.подробно)
        self.assertTrue(any("ACARS: N12345 [H1]" in с for с in н.подробно))

    def test_автомат(self):
        б = поток(vdl2.закодировать([ACARS, XID]))
        б = б[:len(б) // 8 * 8]
        р = разобрать_поток(данные=np.packbits(б).tobytes(), профиль="быстро")
        self.assertTrue(any(н.что.startswith("VDL Mode 2") for н in р.находки), [н.что for н in р.находки])


def место(i):
    """Бит начала октета данных i после заголовка в ``поток(закодировать(…))``."""
    return 333 + 45 + 25 + 8 * i


if __name__ == "__main__":
    unittest.main()
