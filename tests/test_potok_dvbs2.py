"""DVB-S2 после LDPC: внешний БЧХ, скремблер BB, BBHEADER, пакеты TS и GSE.

Эталоны: GNU Radio gr-dtv (dvb_bch_bb_impl.cc, dvb_bbheader_bb_impl.cc,
dvb_bbscrambler_bb_impl.cc) и leansdr (SDRangel, dvbs2.h) — многочлены БЧХ;
Wireshark packet-dvb-s2-bb.c — GSE. Кадры собираются здесь так, как их
собирает передатчик gr-dtv, и разбираются обратно.
"""

import random
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import dvbs2, разобрать
from reportgen.potok.razbor import снять_вручную
from test_potok_mpeg_ts import поток as мультиплекс

#: Минимальные многочлены БЧХ по leansdr (SDRangel, plugins/channelrx/demoddatv/leansdr/dvbs2.h).
LEANSDR_НОРМАЛЬНЫЕ = (0x1002D, 0x10173, 0x10FBD, 0x15A55, 0x11F2F, 0x1F7B5, 0x1AF65, 0x17367,
                      0x10EA1, 0x175A7, 0x13A2D, 0x11AE3)
LEANSDR_КОРОТКИЕ = (0x402B, 0x4941, 0x4647, 0x5591, 0x6B55, 0x6389, 0x6CE5, 0x4F21, 0x460F,
                    0x5A49, 0x5811, 0x65EF)


def код(имя):
    return next(к for к in dvbs2.КОДЫ if к.имя == имя)


def crc8_gr_dtv(биты):
    """add_crc8_bits из gr-dtv дословно: отражённый 0xAB по битам, вывод младшим битом вперёд."""
    crc = 0
    for б in биты:
        b = б ^ (crc & 1)
        crc >>= 1
        if b:
            crc ^= 0xAB
    return [(crc >> n) & 1 for n in range(8)]


def в_биты(данные):
    return np.unpackbits(np.frombuffer(bytes(данные), dtype=np.uint8))


def bbheader(ts_gs, upl, dfl, sync, syncd, *, sis=True, ccm=True, ro=0, isi=0, hem=False):
    m1 = (ts_gs << 6) | (sis << 5) | (ccm << 4) | ro
    тело = bytes([m1, isi]) + upl.to_bytes(2, "big") + dfl.to_bytes(2, "big") + bytes([sync]) \
        + syncd.to_bytes(2, "big")
    return тело + bytes([dvbs2.crc8(тело) ^ (0x80 if hem else 0)])


def поток_ts(пакетов, сид=1):
    г = random.Random(сид)
    return [b"\x47" + bytes([0x01, 0x00, 0x10 | (н & 15)]) + bytes(г.getrandbits(8) for _ in range(184))
            for н in range(пакетов)]


def кадры_ts(пакеты, к, *, кадров):
    """BBFRAME режима NM (gr-dtv, INPUTMODE_NORMAL): синхробайт заменён CRC-8 предыдущего пакета."""
    байты, crc = bytearray(), 0
    for п in пакеты:
        байты.append(crc)
        байты += п[1:]
        crc = dvbs2.crc8(п[1:])
    dfl = к.kbch - 80
    слова, место = [], 0
    for _ in range(кадров):
        до_пакета = (-место) % 188
        syncd = до_пакета * 8
        поле = bytes(байты[место:место + dfl // 8])
        место += dfl // 8
        кадр = np.concatenate([в_биты(bbheader(0b11, 188 * 8, dfl, 0x47, syncd)), в_биты(поле)])
        кадр = np.concatenate([кадр, np.zeros(к.kbch - len(кадр), np.uint8)])
        слова.append(dvbs2.закодировать(dvbs2.скремблер(кадр), к))
    return np.concatenate(слова)


class МногочленыTests(unittest.TestCase):
    def test_как_у_leansdr(self):
        self.assertEqual(LEANSDR_НОРМАЛЬНЫЕ, tuple(dvbs2._многочлен(ст) for ст in dvbs2._НОРМАЛЬНЫЕ))
        self.assertEqual(LEANSDR_КОРОТКИЕ, tuple(dvbs2._многочлен(ст) for ст in dvbs2._КОРОТКИЕ))

    def test_степень_g_равна_n_минус_k(self):
        for к in dvbs2.КОДЫ:
            with self.subTest(к=к.имя):
                self.assertEqual(к.nbch - к.kbch, к.g.bit_length() - 1)
                self.assertEqual(16 * к.t if not к.короткий else 14 * к.t, к.nbch - к.kbch)

    def test_таблица_длин_как_в_gr_dtv(self):
        self.assertEqual((58320, 58192, 8), (код("нормальный кадр 9/10").nbch, код("нормальный кадр 9/10").kbch,
                                             код("нормальный кадр 9/10").t))
        self.assertEqual((43200, 43040, 10), (код("нормальный кадр 2/3").nbch, код("нормальный кадр 2/3").kbch,
                                              код("нормальный кадр 2/3").t))
        self.assertEqual((7200, 7032), (код("короткий кадр 1/2").nbch, код("короткий кадр 1/2").kbch))
        self.assertEqual(29 + 18, len(dvbs2.КОДЫ))

    def test_код_бчх_делится_и_исправляет(self):
        к = код("короткий кадр 1/2")
        rng = np.random.default_rng(1)
        слово = dvbs2.закодировать(rng.integers(0, 2, к.kbch).astype(np.uint8), к)
        self.assertEqual(0, dvbs2.остаток(слово, к.g))
        испорчено = слово.copy()
        места = rng.choice(к.nbch, 12, replace=False)
        испорчено[места] ^= 1
        self.assertNotEqual(0, dvbs2.остаток(испорчено, к.g))
        новое, число = dvbs2.исправить_бчх(испорчено, к)
        self.assertEqual(12, число)
        self.assertTrue(np.array_equal(слово, новое))
        # 13 ошибок — больше t: не исправляется (или исправляется в чужое слово — его ловит остаток).
        испорчено[rng.choice(np.setdiff1d(np.arange(к.nbch), места), 1)] ^= 1
        новое, число = dvbs2.исправить_бчх(испорчено, к)
        self.assertTrue(число == -1 or dvbs2.остаток(новое, к.g) == 0 and not np.array_equal(новое, слово))

    def test_нормальный_кадр_gf16(self):
        к = код("нормальный кадр 8/9")
        rng = np.random.default_rng(4)
        слово = dvbs2.закодировать(rng.integers(0, 2, к.kbch).astype(np.uint8), к)
        for ошибок in (1, 8):
            with self.subTest(ошибок=ошибок):
                испорчено = слово.copy()
                испорчено[rng.choice(к.nbch, ошибок, replace=False)] ^= 1
                новое, число = dvbs2.исправить_бчх(испорчено, к)
                self.assertEqual(ошибок, число)
                self.assertTrue(np.array_equal(слово, новое))
        self.assertEqual((0, True), (dvbs2.исправить_бчх(слово, к)[1], dvbs2.исправить_бчх(слово, к)[0] is слово))


class ЗаголовокTests(unittest.TestCase):
    def test_crc8_как_у_gr_dtv(self):
        rng = np.random.default_rng(2)
        for _ in range(200):
            тело = bytes(rng.integers(0, 256, 9).tolist())
            self.assertEqual(crc8_gr_dtv(в_биты(тело).tolist()), [(dvbs2.crc8(тело) >> (7 - n)) & 1 for n in range(8)])

    def test_crc32_mpeg2_контрольное(self):
        self.assertEqual(0x0376E6E7, dvbs2.crc32_mpeg2(b"123456789"))    # каталог CRC RevEng

    def test_скремблер_как_у_gr_dtv(self):
        # Первые 15 бит ПСП при начальном 100101010000000 (gr-dtv: sr = 0x4A80).
        псп = dvbs2.скремблер(np.zeros(20, np.uint8))
        sr, ждать = 0x4A80, []
        for _ in range(20):
            b = (sr ^ (sr >> 1)) & 1
            ждать.append(b)
            sr = (sr >> 1) | (0x4000 if b else 0)
        self.assertEqual(ждать, псп.tolist())
        self.assertEqual([0, 0, 0, 0, 0, 0, 1, 1], псп[:8].tolist())

    def test_поля_matype(self):
        з = dvbs2.заголовок(bbheader(0b01, 0, 12000, 0, 0, sis=False, ccm=False, ro=2, isi=7))
        self.assertEqual((0b01, True, True, 2, 7, 12000), (з.ts_gs, з.mis, з.acm, з.ro, з.isi, з.dfl))
        self.assertTrue(з.crc_верна)
        self.assertFalse(з.hem)
        з = dvbs2.заголовок(bbheader(0b11, 1504, 100, 0x47, 8, hem=True))
        self.assertEqual((0b11, False, False, 1504, 0x47, 8), (з.ts_gs, з.mis, з.acm, з.upl, з.sync, з.syncd))
        self.assertTrue(з.crc_верна and з.hem)
        плохой = bytearray(bbheader(0b11, 1504, 100, 0x47, 8))
        плохой[9] ^= 1
        self.assertFalse(dvbs2.заголовок(bytes(плохой)).crc_верна)


class TsTests(unittest.TestCase):
    def test_короткий_кадр_ts_с_ошибками(self):
        к = код("короткий кадр 1/2")
        пакеты = поток_ts(200)
        биты = кадры_ts(пакеты, к, кадров=6)
        rng = np.random.default_rng(3)
        for кадр in (1, 4):                                  # ошибки в двух словах БЧХ
            биты[кадр * к.nbch + rng.choice(к.nbch, 5, replace=False)] ^= 1
        найдено = dvbs2.найти(биты)
        self.assertEqual("DVB-S2: BBFRAME после внешнего кода БЧХ", найдено.что)
        текст = "\n".join(найдено.подробно)
        self.assertIn("код БЧХ 7200 → 7032 бит, t = 12 (короткий кадр 1/2; GF(2¹⁴))", текст)
        self.assertIn("исправлено бит 10, неисправимых слов 0", текст)
        self.assertIn("BBHEADER с верной CRC-8: 6 из 6", текст)
        self.assertIn("поток: транспортный поток (TS); один поток (SIS); CCM; скат 0,35", текст)
        # Первый пакет целиком — с SYNCD = 0; все пакеты восстановлены с 0x47.
        ts = bytes(найдено.дальше)
        self.assertEqual(b"".join(пакеты[:len(ts) // 188]), ts)
        self.assertGreater(len(ts) // 188, 25)
        self.assertEqual("пакеты TS", найдено.вид_дальше)
        self.assertRegex(текст, r"CRC-8 пакета \(вместо синхробайта\) верна у (\d+) из \1")

    def test_потерянный_кадр_и_crc8_пакетов(self):
        """Кадр с испорченным BBHEADER пропускается, поток ловится снова по SYNCD; байт, испорченный
        в поле данных (код БЧХ цел), виден по CRC-8 — она стоит вместо синхробайта следующего пакета."""
        к = код("короткий кадр 1/2")
        пакеты = поток_ts(200)
        слова = кадры_ts(пакеты, к, кадров=6).reshape(6, к.nbch)

        def переписать(номер, бит):
            кадр = dvbs2.скремблер(слова[номер][:к.kbch])
            кадр[бит] ^= 1
            слова[номер] = dvbs2.закодировать(dvbs2.скремблер(кадр), к)

        переписать(2, 73)                     # CRC-8 заголовка (не старший бит: тот — признак HEM)
        переписать(4, 80 + 8 * 400 + 3)       # байт в середине пакета поля данных пятого кадра
        кадры = dvbs2.снять(слова.reshape(-1), к)
        self.assertEqual([True, True, False, True, True, True], [з.crc_верна for з in кадры.заголовки])
        ts, верно, сверено = dvbs2.пакеты_ts(list(zip(кадры.заголовки, кадры.поля, strict=True)))
        чужие = [п for п in ts if п not in set(пакеты)]
        self.assertEqual(1, len(чужие), "после пропуска поток пойман по SYNCD; испорчен один пакет")
        self.assertEqual(сверено - 1, верно)
        # Пакет, начатый до пропуска, дальше не сверяется с пакетом после него.
        self.assertEqual(len(ts) - 2, сверено)
        self.assertNotEqual(0, кадры.заголовки[3].syncd)

    def test_мало_верных_заголовков_не_dvbs2(self):
        # Слова БЧХ целы, но CRC-8 верна лишь в одном заголовке из четырёх — не BBFRAME.
        к = код("короткий кадр 1/2")
        слова = кадры_ts(поток_ts(200), к, кадров=4).reshape(4, к.nbch)
        for i in (1, 2, 3):
            кадр = dvbs2.скремблер(слова[i][:к.kbch])
            кадр[75] ^= 1
            слова[i] = dvbs2.закодировать(dvbs2.скремблер(кадр), к)
        self.assertIsNone(dvbs2.длина_слова(слова.reshape(-1)))

    def test_нормальный_кадр_и_выбор_кода_по_остатку(self):
        # 43200 бит — у нормального 2/3 (t = 10) и у S2X 20/30 (t = 12): решает остаток.
        for имя in ("нормальный кадр 2/3", "нормальный кадр S2X 20/30"):
            with self.subTest(имя=имя):
                к = код(имя)
                биты = кадры_ts(поток_ts(120), к, кадров=2)
                self.assertEqual(к, dvbs2.длина_слова(биты)[0])

    def test_случайные_биты_не_dvbs2(self):
        self.assertIsNone(dvbs2.найти(с.случайные_биты(120_000)))
        with self.assertRaisesRegex(ValueError, "DVB-S2: BBHEADER"):
            снять_вручную(с.случайные_биты(60_000), "bbframe")

    def test_ручной_слой_bbframe(self):
        """Слой «bbframe» (ПКМ на столе → «ПУ код»): поля данных кадров подряд — поток TS с CRC-8 вместо 0x47."""
        к = код("короткий кадр 2/3")
        пакеты = поток_ts(150)
        биты = кадры_ts(пакеты, к, кадров=5)
        биты[к.nbch + 17] ^= 1
        ряд, запись = снять_вручную(биты, "bbframe")
        данные = np.packbits(ряд).tobytes()
        self.assertEqual(5 * (к.kbch - 80) // 8, len(данные))
        self.assertEqual(пакеты[0][1:], данные[1:188])              # первый байт — CRC-8 (здесь 0)
        self.assertEqual(dvbs2.crc8(пакеты[0][1:]), данные[188])      # вместо синхробайта второго пакета
        self.assertIn("исправлено бит 1, неисправимых слов 0", " ".join(запись.подробно))


def gse_пакет(s, e, lt, тело):
    return (((s << 15) | (e << 14) | (lt << 12) | len(тело)).to_bytes(2, "big")) + тело


class GseTests(unittest.TestCase):
    def ip(self, н):
        return с.udp("10.0.0.1", "10.0.0.2", 1000 + н, 2000, bytes([н]) * (30 + н))

    def test_целые_и_фрагменты(self):
        g = dvbs2.Gse()
        метка = bytes(range(6))
        целый = gse_пакет(1, 1, 0, b"\x08\x00" + метка + self.ip(1))
        # Фрагменты: первый — ID, полная длина, тип, метка (3 байта), начало PDU; последний — CRC-32.
        pdu = self.ip(2)
        собрано = (2 + 3 + len(pdu)).to_bytes(2, "big") + b"\x08\x00" + b"\xaa\xbb\xcc" + pdu
        crc = dvbs2.crc32_mpeg2(собрано).to_bytes(4, "big")
        первый = gse_пакет(1, 0, 1, bytes([5]) + собрано[:20])
        средний = gse_пакет(0, 0, 1, bytes([5]) + собрано[20:40])
        последний = gse_пакет(0, 1, 1, bytes([5]) + собрано[40:] + crc)
        набивка = b"\x00" * 10
        dvbs2.разобрать_gse(целый + первый + средний, g)
        dvbs2.разобрать_gse(последний + набивка, g)
        self.assertEqual([(0x0800, self.ip(1)), (0x0800, pdu)], g.pdu)
        self.assertEqual((3, 1, 0, 1), (g.фрагментов, g.crc_верно, g.crc_неверно, g.набивка))

    def test_полная_длина_фрагментов_сверяется(self):
        g = dvbs2.Gse()
        собрано = (99).to_bytes(2, "big") + b"\x08\x00" + self.ip(3)       # полная длина неверна
        crc = dvbs2.crc32_mpeg2(собрано).to_bytes(4, "big")
        dvbs2.разобрать_gse(gse_пакет(1, 0, 2, b"\x02" + собрано[:10]), g)
        dvbs2.разобрать_gse(gse_пакет(0, 1, 2, b"\x02" + собрано[10:] + crc), g)
        self.assertEqual((0, 0, 1), (len(g.pdu), g.crc_верно, g.crc_неверно))

    def test_неверная_crc_и_чужой_фрагмент(self):
        g = dvbs2.Gse()
        собрано = b"\x00\x08\x08\x00" + bytes(6)
        dvbs2.разобрать_gse(gse_пакет(1, 0, 2, b"\x07" + собрано[:4]), g)
        dvbs2.разобрать_gse(gse_пакет(0, 1, 2, b"\x07" + собрано[4:] + b"\x00\x00\x00\x00"), g)
        self.assertEqual((0, 1), (len(g.pdu), g.crc_неверно))
        # Продолжение без начала (ETSI TS 102 601-1 A.2) — отбрасывается.
        dvbs2.разобрать_gse(gse_пакет(0, 1, 2, b"\x09" + b"xx" + b"\x00" * 4), g)
        self.assertEqual((0, 1, 0), (len(g.pdu), g.crc_неверно, g.crc_верно))
        # Длина за пределы поля — ошибка, разбор поля прекращается.
        dvbs2.разобрать_gse(gse_пакет(1, 1, 2, b"\x08\x00abc")[:4], g)
        self.assertEqual(1, g.ошибок)

    def test_gse_в_bbframe_до_ip(self):
        к = код("короткий кадр 3/5")
        dfl = к.kbch - 80
        pdus = [self.ip(н) for н in range(40)]
        поля, текущее = [], b""
        for pdu in pdus:
            п = gse_пакет(1, 1, 2, b"\x08\x00" + pdu)
            if len(текущее) + len(п) > dfl // 8:
                поля.append(текущее)
                текущее = b""
            текущее += п
        поля.append(текущее)
        слова = []
        for поле in поля:
            тело = поле + b"\x00" * (dfl // 8 - len(поле))
            кадр = np.concatenate([в_биты(bbheader(0b01, 0, dfl, 0, 0)), в_биты(тело)])
            кадр = np.concatenate([кадр, np.zeros(к.kbch - len(кадр), np.uint8)])
            слова.append(dvbs2.закодировать(dvbs2.скремблер(кадр), к))
        найдено = dvbs2.найти(np.concatenate(слова))
        self.assertEqual(pdus, найдено.дальше)
        self.assertEqual("кадры", найдено.вид_дальше)
        self.assertIn("GSE: PDU 40 (IPv4×40)", "\n".join(найдено.подробно))


class АвтоматTests(unittest.TestCase):
    def test_bbframe_ts_до_таблиц_и_mpe(self):
        пакеты = мультиплекс().пакеты
        пакеты = пакеты + пакеты
        к = код("короткий кадр 3/4")
        биты = кадры_ts(пакеты, к, кадров=len(пакеты) * 188 * 8 // (к.kbch - 80))
        разбор = разобрать(данные=np.packbits(биты).tobytes(), профиль="быстро")
        что = " | ".join(н.что for н in разбор.находки)
        self.assertIn("DVB-S2: BBFRAME после внешнего кода БЧХ", что)
        self.assertIn("MPEG-TS: таблицы PSI/SI и IP в MPE", что)
        self.assertIn("пакеты IP", что)


if __name__ == "__main__":
    unittest.main()
