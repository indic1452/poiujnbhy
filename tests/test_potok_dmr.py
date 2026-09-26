"""DMR: пакеты слотов, тип слота, BPTC (196, 96), LC с RS (12, 9), CSBK и заголовки данных, EMB.

Эталон — MMDVMHost (g4klx), переписанный здесь побайтно и независимо от модуля: раскладка
пакета в 33 байтах (DMRSlotType::getData, CBPTC19696::encodeExtractBinary, CDMREMB::getData,
CSync::addDMRDataSync — синхрослово по маске SYNC_MASK), кодеры Hamming::encode15113_2 и
encode1393, CRS129::encode, CCRC::addCCITT162, маски из DMRDefines.h.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import dmr, разобрать

BS_DATA = bytes([0x0D, 0xFF, 0x57, 0xD7, 0x5D, 0xF5, 0xD0])
BS_AUDIO = bytes([0x07, 0x55, 0xFD, 0x7D, 0xF7, 0x5F, 0x70])
SYNC_MASK = bytes([0x0F, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xF0])


def биты_байта(б):
    return [(б >> (7 - i)) & 1 for i in range(8)]


def байт_бит(б):
    return sum(v << (7 - i) for i, v in enumerate(б))


def hamming15113(d):
    d[11] = d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8]
    d[12] = d[1] ^ d[2] ^ d[3] ^ d[4] ^ d[6] ^ d[8] ^ d[9]
    d[13] = d[2] ^ d[3] ^ d[4] ^ d[5] ^ d[7] ^ d[9] ^ d[10]
    d[14] = d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7] ^ d[10]


def hamming1393(d):
    d[9] = d[0] ^ d[1] ^ d[3] ^ d[5] ^ d[6]
    d[10] = d[0] ^ d[1] ^ d[2] ^ d[4] ^ d[6] ^ d[7]
    d[11] = d[0] ^ d[1] ^ d[2] ^ d[3] ^ d[5] ^ d[7] ^ d[8]
    d[12] = d[0] ^ d[2] ^ d[4] ^ d[5] ^ d[8]


def bptc_encode(вход, пакет):
    """CBPTC19696::encode: 12 байт → байты 0–12 (два бита в 12) и 20–32 (два бита в 20)."""
    bData = [b for б in вход for b in биты_байта(б)]
    d = [0] * 196
    pos = 0
    for a0, a1 in ((4, 11), (16, 26), (31, 41), (46, 56), (61, 71), (76, 86), (91, 101), (106, 116), (121, 131)):
        for a in range(a0, a1 + 1):
            d[a] = bData[pos]
            pos += 1
    for r in range(9):
        строка = d[r * 15 + 1:r * 15 + 16]
        hamming15113(строка)
        d[r * 15 + 1:r * 15 + 16] = строка
    for c in range(15):
        col = [d[c + 1 + 15 * a] for a in range(13)]
        hamming1393(col)
        for a in range(13):
            d[c + 1 + 15 * a] = col[a]
    raw = [0] * 196
    for a in range(196):
        raw[(a * 181) % 196] = d[a]
    for i in range(12):
        пакет[i] = байт_бит(raw[i * 8:i * 8 + 8])
    б = байт_бит(raw[96:104])
    пакет[12] = (пакет[12] & 0x3F) | (б & 0xC0)
    пакет[20] = (пакет[20] & 0xFC) | ((б >> 4) & 0x03)
    for i in range(12):
        пакет[21 + i] = байт_бит(raw[100 + i * 8:108 + i * 8])


def slot_type(пакет, цвет, тип):
    """CDMRSlotType::getData с CGolay2087::encode."""
    s0 = ((цвет << 4) & 0xF0) | (тип & 0x0F)
    cksum = dmr._GOLAY[s0]
    s1, s2 = cksum & 0xFF, cksum >> 8
    пакет[12] = (пакет[12] & 0xC0) | ((s0 >> 2) & 0x3F)
    пакет[13] = (пакет[13] & 0x0F) | ((s0 << 6) & 0xC0) | ((s1 >> 2) & 0x30)
    пакет[19] = (пакет[19] & 0xF0) | ((s1 >> 2) & 0x0F)
    пакет[20] = (пакет[20] & 0x03) | ((s1 << 6) & 0xC0) | ((s2 >> 2) & 0x3C)


def синхро(пакет, слово):
    for i in range(7):
        пакет[13 + i] = (пакет[13 + i] & ~SYNC_MASK[i] & 0xFF) | слово[i]


def emb(пакет, цвет, pi=False, lcss=0):
    """CDMREMB::getData с CQR1676::encode."""
    e0 = ((цвет << 4) & 0xF0) | (0x08 if pi else 0) | ((lcss << 1) & 0x06)
    cksum = dmr._QR[(e0 >> 1) & 0x7F]
    e0, e1 = cksum >> 8, cksum & 0xFF
    пакет[13] = (пакет[13] & 0xF0) | ((e0 >> 4) & 0x0F)
    пакет[14] = (пакет[14] & 0x0F) | ((e0 << 4) & 0xF0)
    пакет[18] = (пакет[18] & 0xF0) | ((e1 >> 4) & 0x0F)
    пакет[19] = (пакет[19] & 0x0F) | ((e1 << 4) & 0xF0)


def gmult(a, b):
    return dmr._умн(a, b)


def rs129_encode(msg):
    """CRS129::encode (POLY 64, 56, 14)."""
    POLY = (64, 56, 14)
    parity = [0, 0, 0, 0]
    for i in range(9):
        dbyte = msg[i] ^ parity[2]
        for j in (2, 1):
            parity[j] = parity[j - 1] ^ gmult(POLY[j], dbyte)
        parity[0] = gmult(POLY[0], dbyte)
    return parity


def ccitt162(данные):
    """CCRC::addCCITT162: таблица 0x1021, начальное 0, инверсия; старший байт — первым."""
    crc = 0
    for б in данные:
        crc ^= б << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return (~crc) & 0xFFFF


def lc(flco, src, dst, тип, fid=0, параметры=0):
    б = [flco, fid, параметры] + list(dst.to_bytes(3, "big")) + list(src.to_bytes(3, "big"))
    p = rs129_encode(б)
    маска = {1: 0x96, 2: 0x99}[тип]
    return bytes(б + [p[2] ^ маска, p[1] ^ маска, p[0] ^ маска])


def csbk(код, src, dst, fid=0, маска=0xA5A5, байт2=0, байт3=0):
    б = bytes([код, fid, байт2, байт3]) + dst.to_bytes(3, "big") + src.to_bytes(3, "big")
    crc = ccitt162(б) ^ маска
    return б + crc.to_bytes(2, "big")


def пакет_данных(содержимое, тип, цвет=1):
    п = bytearray(33)
    bptc_encode(содержимое, п)
    slot_type(п, цвет, тип)
    синхро(п, BS_DATA)
    return п


def пакет_речи(rng, цвет=1, a=False):
    п = bytearray(rng.integers(0, 256, 33).astype(np.uint8).tobytes())
    if a:
        синхро(п, BS_AUDIO)
    else:
        emb(п, цвет)
    return п


def поток_бс(пакеты, сид=1):
    """Базовая станция: CACH (24 бита, здесь случайные) перед каждым пакетом."""
    rng = np.random.default_rng(сид)
    итог = []
    for п in пакеты:
        итог.append(rng.integers(0, 2, 24).astype(np.uint8))
        итог.append(np.unpackbits(np.frombuffer(bytes(п), dtype=np.uint8)))
    return np.concatenate(итог)


def эфир(сид=3):
    """Слот 1: вызов 2001 → разговорная группа 9, сверхкадр речи, окончание; слот 2 — CSBK и пустые."""
    rng = np.random.default_rng(сид)
    слот1 = [пакет_данных(lc(0, 2001, 9, 1), 1), пакет_данных(lc(0, 2001, 9, 1), 1)]
    for _ in range(2):
        слот1 += [пакет_речи(rng, a=True)] + [пакет_речи(rng) for _ in range(5)]
    слот1 += [пакет_данных(lc(0, 2001, 9, 2), 2)]
    слот2 = [пакет_данных(csbk(0x3D, 3003, 4004, байт2=0x40), 3)]
    слот2 += [пакет_данных(csbk(0x24, 5005, 6006, байт3=0x80), 3)]
    слот2 += [пакет_данных(bytes(rng.integers(0, 256, 12).astype(np.uint8)), 9) for _ in range(len(слот1) - 2)]
    return [п for пара in zip(слот1, слот2, strict=True) for п in пара]


class КодыTests(unittest.TestCase):
    def test_bptc_как_mmdvmhost(self):
        rng = np.random.default_rng(1)
        for _ in range(20):
            вход = bytes(rng.integers(0, 256, 12).astype(np.uint8))
            п = bytearray(33)
            bptc_encode(вход, п)
            биты = np.unpackbits(np.frombuffer(bytes(п), dtype=np.uint8))
            сырые = np.concatenate([биты[:98], биты[166:]])
            self.assertTrue(np.array_equal(dmr.bptc_кодировать(np.unpackbits(np.frombuffer(вход, np.uint8))), сырые))
            данные, исправлено = dmr.bptc(сырые[None])
            self.assertEqual(вход, np.packbits(данные[0]).tobytes())
            self.assertEqual(0, исправлено)

    def test_bptc_исправляет(self):
        rng = np.random.default_rng(2)
        вход = np.unpackbits(np.frombuffer(bytes(rng.integers(0, 256, 12).astype(np.uint8)), np.uint8))
        сырые = dmr.bptc_кодировать(вход)
        # Бит 0 — R(3), ни в строки, ни в столбцы не входит: исправлять нечего.
        for места, ждём in (([0], 0), ([10, 100], 2), ([5, 77, 150], 3)):
            with self.subTest(места=места):
                испорчено = сырые.copy()
                испорчено[места] ^= 1
                данные, исправлено = dmr.bptc(испорчено[None])
                self.assertTrue(np.array_equal(вход, данные[0]))
                self.assertEqual(ждём, исправлено)

    def test_golay_и_qr(self):
        """Таблицы кодов: у Golay (20, 8) расстояние 8, у QR (16, 7) — 6."""
        def наименьшее(слова):
            return min(bin(int(a) ^ int(b)).count("1") for i, a in enumerate(слова) for b in слова[i + 1:])
        self.assertEqual(8, наименьшее(dmr.СЛОВА_ТИПА))
        self.assertEqual(6, наименьшее(dmr.СЛОВА_EMB))

    def test_синхрослова_dsd_и_mmdvmhost(self):
        """Синхрослова модуля (MMDVMHost) — те же, что в каталоге синхрослов (DSD, дибитами)."""
        from reportgen.potok import sinhro
        каталог = {имя: значение for имя, значение, _ in sinhro.ДРУГИЕ_СЛОВА}
        for вид, имя in (("БС, данные", "DMR, БС — данные (48 бит)"), ("БС, речь", "DMR, БС — речь (48 бит)"),
                         ("АС, данные", "DMR, АС — данные (48 бит)"), ("АС, речь", "DMR, АС — речь (48 бит)"),
                         ("прямой режим, слот 1, речь", "DMR, прямой режим, слот 1 — речь (48 бит)"),
                         ("прямой режим, слот 2, данные", "DMR, прямой режим, слот 2 — данные (48 бит)")):
            self.assertEqual(каталог[имя], dmr.СИНХРО[вид], вид)
        # Байты MMDVMHost (сдвинуты на тетраду в пакете) — те же слова.
        for байты, вид in ((BS_DATA, "БС, данные"), (BS_AUDIO, "БС, речь")):
            self.assertEqual(dmr.СИНХРО[вид], int.from_bytes(байты, "big") >> 4 & ((1 << 48) - 1))

    def test_тип_слота_как_mmdvmhost(self):
        for цвет, тип in ((0, 0), (1, 3), (15, 10), (7, 9)):
            п = пакет_данных(bytes(12), тип, цвет)
            р = dmr.разобрать_пакеты(np.unpackbits(np.frombuffer(bytes(п), np.uint8)), [0])
            self.assertEqual((цвет, тип, 0), (int(р["цвет"][0]), int(р["тип"][0]), int(р["golay"][0])))

    def test_rs129_и_crc(self):
        б = lc(0, 2001, 9, 1)
        маска = bytes([0x96] * 3)
        self.assertEqual(dmr.rs129_проверка(б[:9]), bytes(x ^ y for x, y in zip(б[9:], маска, strict=True)))
        с = csbk(0x3D, 1, 2)
        self.assertEqual(dmr.crc_ccitt(с[:10]) ^ 0xA5A5, int.from_bytes(с[10:], "big"))
        # CRC-16/GSM — «123456789» → 0xCE3C (каталог RevEng).
        self.assertEqual(0xCE3C, dmr.crc_ccitt(b"123456789"))

    def test_поля(self):
        данные = np.unpackbits(np.frombuffer(lc(3, 7, 8, 2), np.uint8))
        self.assertEqual(("LC", "индивидуальный вызов: 7 → 8 (FID 0, параметры 0x00)"), dmr.поля(2, данные))
        # Не та маска — проверка не сходится.
        self.assertIsNone(dmr.поля(1, данные))
        данные = np.unpackbits(np.frombuffer(csbk(0x26, 11, 12), np.uint8))
        self.assertEqual(("CSBK", "NACK_Rsp (отказ): 12 → 11 (FID 0)"), dmr.поля(3, данные))
        # Заголовок данных: DPF 2 (без подтверждения), групповой; кому — байты 2–4, от кого — 5–7.
        б = bytes([0x80 | 2, 0]) + (1).to_bytes(3, "big") + (11).to_bytes(3, "big") + bytes(2)
        б += (ccitt162(б) ^ 0xCCCC).to_bytes(2, "big")
        self.assertEqual(("данные", "заголовок данных (без подтверждения, групповой): 11 → 1"),
                         dmr.поля(6, np.unpackbits(np.frombuffer(б, np.uint8))))


class ПотокTests(unittest.TestCase):
    def test_эфир_бс(self):
        поток = поток_бс(эфир())[100:]
        найдено = dmr.найти(поток)
        self.assertIsNotNone(найдено)
        текст = "\n".join(найдено.подробно)
        self.assertIn("расстояния между ними кратны 288 бит (CACH 24 + пакет 264", текст)
        # Первый заголовок срезан сдвигом записи: второй заголовок и окончание.
        self.assertIn("LC: групповой вызов: 2001 → 9 (FID 0, параметры 0x00) — × 2", текст)
        self.assertIn("CSBK: Preamble CSBK (преамбула): 3003 → 4004 (FID 0)", текст)
        self.assertIn("CSBK: Radio Check (проверка связи): 5005 → 6006 (FID 0)", текст)
        self.assertIn("цветовой код: 1 × ", текст)
        self.assertIn("сверхкадров речи (синхрослово речи — пакет A): 2", текст)
        self.assertIn("EMB пакетов B–F (QR (16, 7)): верны 10 из 10; цветовой код 1 × 10", текст)
        self.assertIn("пустой пакет × 13, CSBK × 2, заголовок речи (LC) × 1, окончание с LC × 1", текст)
        self.assertEqual(288, найдено.свойства["шаг"])

    def test_ошибки_в_пакетах(self):
        поток = поток_бс(эфир()).copy()
        rng = np.random.default_rng(4)
        поток ^= (rng.random(len(поток)) < 2e-3).astype(np.uint8)
        найдено = dmr.найти(поток)
        self.assertIsNotNone(найдено)
        self.assertTrue(any("BPTC (196, 96): исправлено бит" in п for п in найдено.подробно))

    def test_не_dmr(self):
        rng = np.random.default_rng(5)
        self.assertIsNone(dmr.найти(rng.integers(0, 2, 200_000).astype(np.uint8)))
        # Синхрослова без шага (вразброс) — не DMR.
        поток = rng.integers(0, 2, 200_000).astype(np.uint8)
        слово = np.array([(dmr.СИНХРО["БС, данные"] >> (47 - i)) & 1 for i in range(48)], np.uint8)
        for м in rng.choice(190_000, 12, replace=False):
            поток[м:м + 48] = слово
        self.assertIsNone(dmr.найти(поток))

    def test_инструмент_стола(self):
        from reportgen.potok import rastr
        найдено = rastr.инструмент(поток_бс(эфир()), "dmr")
        self.assertTrue(найдено.что.startswith("DMR (ETSI TS 102 361): пакеты слотов"))

    def test_автомат(self):
        поток = поток_бс(эфир() * 3)
        поток = поток[:len(поток) // 8 * 8]
        разбор = разобрать(данные=np.packbits(поток).tobytes(), профиль="быстро")
        что = " | ".join(н.что for н in разбор.находки)
        self.assertIn("DMR (ETSI TS 102 361): пакеты слотов", что)


if __name__ == "__main__":
    unittest.main()
