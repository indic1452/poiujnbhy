"""Канальный протокол в кадрах HDLC: вид по признакам стандартов и передача в разборщики пакетов.

MTP2 (ОКС-7), LAPD (ISDN), Frame Relay, LAPB (X.25), AX.25 опознаются по всей
массе кадров; кадры разбирают разборщики анализатора пакетов, этап
выгружается pcap с типом канала. Случайные кадры не опознаются ни как
один вид.
"""

import random
import struct
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import kanal_sintez as к
import potok_sintez as с
from reportgen.potok import kanal, разобрать
from reportgen.potok.bity import в_байты, в_биты
from reportgen.potok.zadaniya import выгрузка
from reportgen.setevoy import vid_kadra, прочитать_захват, разобрать_пакет
from test_setevoy_oks7 import q931_setup

ПОТОКИ = {"MTP2": к.mtp2, "LAPD": к.lapd, "Frame Relay": к.fr, "LAPB": к.lapb, "AX.25": к.ax25}


class ВидКадраTests(unittest.TestCase):
    def test_каждый_вид_по_своему_потоку(self):
        for вид, построить in ПОТОКИ.items():
            with self.subTest(вид=вид):
                кадры = построить()
                решение = vid_kadra.вид_потока(кадры)
                self.assertEqual((вид, 1.0), решение[:2])
                # У остальных видов доля далека от порога.
                self.assertTrue(all(д < 0.2 for и, д in решение[2].items() if и != вид), решение[2])

    def test_случайные_кадры_не_опознаются(self):
        кадры = к.случайные()
        self.assertIsNone(vid_kadra.вид_потока(кадры))
        # И по одному: ложных срабатываний меньше процента у каждого вида.
        for имя, проверка in vid_kadra.ПРОВЕРКИ:
            with self.subTest(вид=имя):
                self.assertLess(sum(map(проверка, кадры)) / len(кадры), 0.01)

    def test_мало_кадров_вид_не_решается(self):
        self.assertIsNone(vid_kadra.вид_потока(к.mtp2()[:7]))
        self.assertEqual("MTP2", vid_kadra.вид_потока(к.mtp2()[:8])[0])

    def test_порог_доли(self):
        # 80 % кадров MTP2 — ещё MTP2; меньше — вид не решается.
        кадры = к.mtp2(80) + к.случайные(20, от_=1, до=1)
        self.assertEqual("MTP2", vid_kadra.вид_потока(кадры)[0])
        self.assertIsNone(vid_kadra.вид_потока(к.mtp2(79) + к.случайные(21, от_=1, до=1)))

    def test_mtp2_по_q703(self):
        self.assertTrue(vid_kadra.mtp2(bytes([0x81, 0x82, 0])))                    # FISU
        self.assertTrue(vid_kadra.mtp2(bytes([0x81, 0x82, 1, 0x02])))              # LSSU SIE
        self.assertFalse(vid_kadra.mtp2(bytes([0x81, 0x82, 1, 0x09])))             # запас поля состояния
        self.assertFalse(vid_kadra.mtp2(bytes([0x81, 0x82, 0x40])))                # запас октета LI
        self.assertFalse(vid_kadra.mtp2(bytes([0x81, 0x82, 1])))                   # LI не сходится
        msu = bytes([0x85]) + bytes(8)
        self.assertTrue(vid_kadra.mtp2(bytes([1, 1, len(msu)]) + msu))
        self.assertFalse(vid_kadra.mtp2(bytes([1, 1, len(msu)]) + bytes([0x8B]) + msu[1:]))   # SI 11 — запас
        self.assertFalse(vid_kadra.mtp2(bytes([1, 1, 5]) + bytes([0x85, 0, 0, 0, 0])))        # без сведений
        # LI 63 — «63 и больше» (Q.703 2.3.3).
        длинный = bytes([0x85]) + bytes(99)
        self.assertTrue(vid_kadra.mtp2(bytes([1, 1, 63]) + длинный))
        self.assertFalse(vid_kadra.mtp2(bytes([1, 1, 62]) + длинный))
        self.assertTrue(vid_kadra.mtp2(bytes([1, 1, 62]) + длинный[:62]))
        self.assertFalse(vid_kadra.mtp2(b"\x01\x01"))

    def test_lapd_по_q921(self):
        адрес = bytes([0 << 2, (5 << 1) | 1])
        self.assertTrue(vid_kadra.lapd(адрес + b"\x7f"))                           # SABME, P
        self.assertFalse(vid_kadra.lapd(адрес + b"\x7f\x00"))                      # SABME со сведениями
        self.assertTrue(vid_kadra.lapd(адрес + b"\x01\x0b"))                       # RR
        self.assertFalse(vid_kadra.lapd(адрес + b"\x0d\x0b"))                      # SREJ — не в Q.921
        self.assertFalse(vid_kadra.lapd(адрес + b"\x01"))                          # RR без N(R)
        self.assertFalse(vid_kadra.lapd(адрес + b"\x01\x0b\x00"))                  # у кадра S нет сведений
        self.assertTrue(vid_kadra.lapd(адрес + b"\x00\x00" + q931_setup()))        # I
        self.assertFalse(vid_kadra.lapd(адрес + b"\x00\x00"))                      # I без сведений
        self.assertTrue(vid_kadra.lapd(адрес + b"\x03" + q931_setup()))            # UI
        self.assertFalse(vid_kadra.lapd(адрес + b"\x03"))                          # UI без сведений
        self.assertTrue(vid_kadra.lapd(адрес + b"\x87" + bytes(5)))                # FRMR: 5 октетов
        self.assertFalse(vid_kadra.lapd(адрес + b"\x87" + bytes(3)))
        self.assertTrue(vid_kadra.lapd(адрес + b"\xaf\x82"))                       # XID
        self.assertFalse(vid_kadra.lapd(адрес + b"\xe3"))                          # U не из Q.921
        for sapi, годен in ((0, True), (1, True), (16, True), (62, True), (63, True), (2, False), (61, False)):
            with self.subTest(sapi=sapi):
                self.assertEqual(годен, vid_kadra.lapd(bytes([sapi << 2, 0xFF]) + b"\x73"))
        self.assertFalse(vid_kadra.lapd(bytes([0x01, 0xFF, 0x73])))               # EA0 = 1
        self.assertFalse(vid_kadra.lapd(bytes([0x00, 0xFE, 0x73])))               # EA1 = 0

    def test_fr_по_rfc2427(self):
        адрес = к.fr_адрес(100)
        self.assertEqual(100, vid_kadra.dlci(адрес))
        self.assertEqual(1023, vid_kadra.dlci(к.fr_адрес(1023)))
        for nlpid in (0x80, 0x81, 0x82, 0x83, 0x8E, 0xB0, 0xCC, 0xCF):
            with self.subTest(nlpid=nlpid):
                self.assertTrue(vid_kadra.fr(адрес + bytes([0x03, nlpid])))
        self.assertTrue(vid_kadra.fr(адрес + b"\x03\x00\x80"))                    # выравнивание 0x00
        self.assertFalse(vid_kadra.fr(адрес + b"\x03\x00\x00"))
        self.assertFalse(vid_kadra.fr(адрес + b"\x03\x00"))
        self.assertFalse(vid_kadra.fr(адрес + b"\x03\x47"))
        lmi = b"\x03\x08\x00\x75"
        self.assertTrue(vid_kadra.fr(к.fr_адрес(0) + lmi))
        self.assertTrue(vid_kadra.fr(к.fr_адрес(1023) + b"\x03\x08\x00\x7d"))
        self.assertFalse(vid_kadra.fr(к.fr_адрес(100) + lmi))                    # LMI — только DLCI 0/1023
        self.assertFalse(vid_kadra.fr(к.fr_адрес(0) + b"\x03\x08\x01\x75"))       # ссылка вызова не пустая
        self.assertFalse(vid_kadra.fr(к.fr_адрес(0) + b"\x03\x08\x00\x05"))       # не STATUS ENQUIRY/STATUS
        self.assertFalse(vid_kadra.fr(к.fr_адрес(0) + b"\x03\x08\x00"))
        # Инкапсуляция Cisco: EtherType вслед за адресом.
        self.assertTrue(vid_kadra.fr(адрес + b"\x08\x00" + bytes(20)))
        self.assertFalse(vid_kadra.fr(адрес + b"\x08\x00" + bytes(19)))
        self.assertFalse(vid_kadra.fr(адрес + b"\x88\x47" + bytes(20)))

    def test_lapb_по_x25(self):
        self.assertTrue(vid_kadra.lapb(b"\x03\x3f"))                               # SABM, P
        self.assertTrue(vid_kadra.lapb(b"\x01\x73"))                               # UA, F
        self.assertFalse(vid_kadra.lapb(b"\x01\x73\x00"))
        self.assertTrue(vid_kadra.lapb(b"\x01\x87\x00\x00\x00"))                   # FRMR
        self.assertFalse(vid_kadra.lapb(b"\x01\x87\x00"))
        self.assertTrue(vid_kadra.lapb(b"\x01\x21"))                               # RR
        self.assertFalse(vid_kadra.lapb(b"\x01\x2d"))                              # SREJ
        self.assertTrue(vid_kadra.lapb(b"\x03\x00\x10\x01\x00"))                   # I, X.25 модуль 8
        self.assertTrue(vid_kadra.lapb(b"\x03\x00\x20\x01\x00"))                   # модуль 128
        self.assertFalse(vid_kadra.lapb(b"\x03\x00\x30\x01\x00"))                  # GFI 11 — запас
        self.assertFalse(vid_kadra.lapb(b"\x03\x00\x10\x01"))
        for адрес in (0x07, 0x0F):
            self.assertTrue(vid_kadra.lapb(bytes([адрес, 0x63])))
        self.assertFalse(vid_kadra.lapb(b"\x05\x63"))
        self.assertFalse(vid_kadra.lapb(b"\x03"))

    def test_ax25(self):
        кадр = к.ax25(1)[0]
        self.assertTrue(vid_kadra.ax25(кадр))
        self.assertFalse(vid_kadra.ax25(кадр[:14]))                               # только адреса
        self.assertFalse(vid_kadra.ax25(bytes([кадр[0] | 1]) + кадр[1:]))         # нечётный знак
        self.assertFalse(vid_kadra.ax25(bytes([ord("a") << 1]) + кадр[1:]))       # строчная буква
        # Бит расширения у первого адреса — отправителя нет.
        self.assertFalse(vid_kadra.ax25(кадр[:6] + bytes([кадр[6] | 1]) + кадр[7:]))
        # Ретрансляторы: бит расширения — у последнего.
        ретранслятор = bytes(ord(ч) << 1 for ч in "WIDE1 ") + b"\x63"
        с_ретранслятором = кадр[:13] + bytes([кадр[13] & 0xFE]) + ретранслятор + кадр[14:]
        self.assertTrue(vid_kadra.ax25(с_ретранслятором))
        self.assertFalse(vid_kadra.ax25(bytes([0x40] * 6 + [0x60]) + кадр[7:]))   # пустой позывной

    def test_вид_одного_кадра(self):
        self.assertEqual("Frame Relay", vid_kadra.вид(к.fr()[1]))
        self.assertEqual("LAPD", vid_kadra.вид(к.lapd()[0]))
        self.assertEqual("LAPB", vid_kadra.вид(b"\x03\x3f"))
        self.assertEqual("AX.25", vid_kadra.вид(к.ax25(1)[0]))
        self.assertEqual("MTP2", vid_kadra.вид(bytes([1, 1, 0])))
        self.assertIsNone(vid_kadra.вид(b"\xff\xff\xff\xff"))

    def test_fr_на_малых_dlci_не_путается_с_lapd(self):
        # DLCI 16–31: первый октет адреса — как у SAPI 1, и UI 0x03 — как у LAPD.
        кадры = к.fr(dlci=20)
        self.assertEqual("Frame Relay", vid_kadra.вид_потока(кадры)[0])
        self.assertEqual("Frame Relay", vid_kadra.вид(кадры[1]))


class ПередачаTests(unittest.TestCase):
    def test_mtp2_до_isup_и_tcap(self):
        найдено = kanal.найти(к.mtp2())
        self.assertEqual("ОКС-7, MTP2 (Q.703)", найдено.что)
        self.assertEqual("канальный", найдено.уровень)
        текст = "\n".join(найдено.подробно)
        self.assertIn("MTP2 → MTP3 → ISUP", текст)
        self.assertIn("MTP2 → MTP3 → SCCP → TCAP", текст)
        self.assertIn("pcap с типом канала 140", текст)
        self.assertEqual(140, найдено.дальше.канал)
        self.assertEqual(300, len(найдено.дальше))
        self.assertIn("признаки MTP2 сошлись у 300 из 300 кадрах", найдено.мера)

    def test_каналы_и_сводки(self):
        for вид, построить, ждать in (("LAPD", к.lapd, "LAPD → Q.931"), ("Frame Relay", к.fr, "FR → IPv4 → UDP"),
                                      ("AX.25", к.ax25, "AX.25"), ("LAPB", к.lapb, "LAPB → X.25")):
            with self.subTest(вид=вид):
                найдено = kanal.найти(построить())
                self.assertEqual(kanal.КАНАЛЫ[вид][2], найдено.что)
                self.assertIn(ждать, "\n".join(найдено.подробно))
        self.assertIn("DLCI: 100×180, 0×20", "\n".join(kanal.найти(к.fr()).подробно))
        self.assertIn("SAPI/TEI: 0/0×", "\n".join(kanal.найти(к.lapd()).подробно))
        self.assertIn("SAPI/TEI: 16/5×", "\n".join(kanal.найти(к.lapd(sapi=16, tei=5)).подробно))

    def test_выгрузка_pcap_с_типом_канала(self):
        найдено = kanal.найти(к.lapd())
        данные, расширение = выгрузка(найдено.дальше, найдено.вид_дальше)
        self.assertEqual("pcap", расширение)
        self.assertEqual(203, struct.unpack("<I", данные[20:24])[0])
        кадры = к.lapd()
        первый_i = next(н for н, кадр in enumerate(кадры) if len(кадр) > 5)   # кадр I с Q.931 SETUP
        запись = прочитать_захват(данные=данные).записи[первый_i]
        self.assertEqual("LAPD", запись.канал)
        self.assertIn("Q.931", разобрать_пакет(запись.данные, запись.канал).стек)
        # LAPB без типа канала pcap — .Sig, и анализатор пакетов узнаёт вид по признакам.
        найдено = kanal.найти(к.lapb())
        данные, расширение = выгрузка(найдено.дальше, найдено.вид_дальше)
        self.assertEqual("sig", расширение)
        # Обычные кадры (без вида) — по-прежнему .Sig.
        self.assertEqual("sig", выгрузка([b"\x01\x02"], "кадры")[1])

    def test_случайные_не_находка(self):
        self.assertIsNone(kanal.найти(к.случайные()))

    def test_авто_анализатора_пакетов(self):
        """Кадры .Sig без типа канала: вид — по признакам, дальше свой разборщик."""
        for кадр, стек in ((к.mtp2()[4], "MTP2"), (к.lapd()[0], "LAPD"), (к.fr()[1], "FR"),
                           (к.lapb()[2], "LAPB"), (к.ax25(1)[0], "AX.25")):
            with self.subTest(стек=стек):
                self.assertEqual(стек, разобрать_пакет(кадр, "авто").стек[0])
        self.assertEqual("X.25", разобрать_пакет(b"\x03\x00\x10\x01\x00" + bytes(8), "авто").стек[1])
        self.assertEqual(["Данные"], разобрать_пакет(b"\xff\xff\xff\xff", "авто").стек)


class РазборПотокаTests(unittest.TestCase):
    def test_e1_окс7_в_ки16_и_fr_на_группе_ки(self):
        """E1: в КИ16 — ОКС-7 (MTP2), на КИ1–15 — Frame Relay 15 × 64 кбит/с, КИ17–31 — речь."""
        циклов = 6000
        окс7 = к.поток_hdlc(к.mtp2(600))
        fr = к.поток_hdlc(к.fr(900))
        г = random.Random(1)

        def байт(цикл, ки):
            if ки == 16:
                return окс7[цикл % len(окс7)]
            if 1 <= ки <= 15:
                return fr[(цикл * 15 + ки - 1) % len(fr)]
            return г.randrange(256)
        разбор = разобрать(данные=с.e1(циклов, каналы=байт), профиль="быстро")
        находки = [(н.что, н.путь) for н in разбор.находки]
        текст = " | ".join(f"{что} @ {путь}" for что, путь in находки)
        self.assertIn("ОКС-7, MTP2 (Q.703)", текст)
        self.assertRegex(текст, r"Frame Relay \(Q\.922, RFC 2427\).*КИ1–15 \(15 × 64 кбит/с\)")


    def test_оксь7_в_битах_hdlc(self):
        """Поток бит HDLC с кадрами MTP2 — автомат доходит до ОКС-7, а не до «полей неизвестных кадров»."""
        биты = в_биты(к.поток_hdlc(к.mtp2(400)))
        разбор = разобрать(данные=в_байты(np.asarray(биты, dtype=np.uint8)), профиль="быстро")
        что = [н.что for н in разбор.находки]
        self.assertIn("ОКС-7, MTP2 (Q.703)", " | ".join(что))


if __name__ == "__main__":
    unittest.main()
