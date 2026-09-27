"""Медиа по SDP: поток из SIP INVITE (RFC 8866) — пакеты RTP (RFC 3550) на его адрес и порт с кодеком из
a=rtpmap или RFC 3551, события DTMF (RFC 4733, 2.3), RTCP на порту + 1, факс T.38 (прил. A, выровненный PER)
поверх UDPTL и кадры T.30 (5.3.6) внутри hdlc-data. Байты собираются по стандартам вручную."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import prilozh
from reportgen.setevoy.protokoly import media, promyshlennye
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет

SDP = (b"v=0\r\no=- 1 1 IN IP4 10.0.0.1\r\ns=-\r\nc=IN IP4 10.0.0.1\r\nt=0 0\r\n"
       b"m=audio 40000 RTP/AVP 8 101\r\na=rtpmap:101 telephone-event/8000\r\n"
       b"m=image 40010 udptl t38\r\nc=IN IP4 10.0.0.9\r\n"
       b"m=video 0 RTP/AVP 31\r\n")


def invite(sdp=SDP):
    return (b"INVITE sip:bob@example.com SIP/2.0\r\nVia: SIP/2.0/UDP 10.0.0.1:5060\r\nCall-ID: a1\r\n"
            b"CSeq: 1 INVITE\r\nContent-Type: application/sdp\r\nContent-Length: %d\r\n\r\n" % len(sdp)) + sdp


def по_udp(данные, от, к, src="10.0.0.2", dst="10.0.0.1", номер=2, шаблоны=None, как=None):
    return разобрать_пакет(с.eth(с.ip(с.udp(данные, от, к, src=src, dst=dst), 17, src=src, dst=dst)), номер=номер,
                           шаблоны=шаблоны, как=как)


def по_udp6(данные, от, к, src, dst, номер=2, шаблоны=None):
    return разобрать_пакет(с.eth(с.ip6(с.udp(данные, от, к, src=src, dst=dst, v6=True), 17, src=src, dst=dst),
                                 0x86DD), номер=номер, шаблоны=шаблоны)


def общий(sdp=SDP):
    шаблоны = {}
    п = по_udp(invite(sdp), 5060, 5060, src="10.0.0.1", dst="10.0.0.2", номер=1, шаблоны=шаблоны)
    assert "SDP" in п.стек, п.стек
    return шаблоны


def rtp(тип, нагрузка=b"\x00" * 4, первый=0x80, маркер=0, номер=7, отметка=160, ssrc=0x11223344, доп=b""):
    return bytes([первый, маркер << 7 | тип]) + struct.pack(">HII", номер, отметка, ssrc) + доп + нагрузка


def уровень(п, протокол):
    return [x for x in п.уровни if x.протокол == протокол][0]


def поля(п, протокол):
    у = уровень(п, протокол)
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


def обратный(б):
    return int(f"{б:08b}"[::-1], 2)


def номер_t30(текст):
    """Номер T.30 (5.3.6.2.4): 20 знаков, передаются с последнего, биты каждого байта — в обратном порядке."""
    return bytes(обратный(ord(x)) for x in reversed(текст.rjust(20)))


def кадр_t30(fcf, fif=b"", управление=0xC0):
    return bytes([0xFF, управление, fcf]) + fif


def элемент(вид, данные=None):
    """Элемент data-field, начинающийся с границы октета: бит наличия field-data, 3 бита вида, добивка."""
    if данные is None:
        return bytes([вид << 4])
    return bytes([0x80 | вид << 4]) + struct.pack(">H", len(данные) - 1) + данные


def udptl(ifp, номер=5, восстановление=b"\x00\x00"):
    return struct.pack(">HB", номер, len(ifp)) + ifp + восстановление


class ТестRTP(unittest.TestCase):
    def test_поток_из_sdp_и_кодек(self):
        ш = общий()
        self.assertEqual(set(ш[media.ПОТОКИ]), {"10.0.0.1|40000", "10.0.0.1|40001", "10.0.0.9|40010"})
        self.assertTrue(ш[media.ПОТОКИ]["10.0.0.1|40001"][1]["rtcp"])
        п = по_udp(rtp(8, b"\xd5" * 160), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.стек[-1], "RTP")
        у = п.уровни[-1]
        self.assertEqual(у.полное, "Real-time Transport Protocol (по SDP)")
        ф = поля(п, "RTP")
        self.assertEqual((ф["rtp.codec"].текст, ф["rtp.codec"].сырое), ("PCMA/8000", 8))
        self.assertEqual((ф["rtp.seq"].сырое, ф["rtp.timestamp"].сырое, ф["rtp.ssrc"].текст), (7, 160, "0x11223344"))
        self.assertEqual(п.инфо, "RTP PCMA/8000, тип 8, SSRC 0x11223344, номер 7")
        self.assertEqual((у.длина, п.нагрузка), (12, b"\xd5" * 160))
        # Ответный поток (из порта SDP) тоже узнаётся.
        п = по_udp(rtp(8), 40000, 30000, src="10.0.0.1", dst="10.0.0.2", шаблоны=ш)
        self.assertEqual(поля(п, "RTP")["rtp.codec"].текст, "PCMA/8000")

    def test_событие_dtmf(self):
        ш = общий()
        п = по_udp(rtp(101, bytes([5, 0x8A]) + struct.pack(">H", 800), маркер=1), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.стек[-2:], ["RTP", "RTPEvent"])
        ф = поля(п, "RTPEvent")
        self.assertEqual((ф["rtpevent.event_id"].текст, ф["rtpevent.event_id"].сырое), ("DTMF 5", 5))
        self.assertEqual((ф["rtpevent.end_of_event"].сырое, ф["rtpevent.reserved"].сырое), (1, 0))
        self.assertEqual((ф["rtpevent.volume"].сырое, ф["rtpevent.duration"].сырое), (10, 800))
        self.assertEqual([(x.ключ, x.смещение, x.длина) for x in п.уровни[-1].поля],
                         [("rtpevent.event_id", 54, 1), ("rtpevent.end_of_event", 55, 1), ("rtpevent.reserved", 55, 1),
                          ("rtpevent.volume", 55, 1), ("rtpevent.duration", 56, 2)])
        self.assertEqual(п.уровни[-1].смещение, 54)
        self.assertEqual(п.инфо, "RTP Event DTMF 5, конец, громкость 10, длительность 800")
        self.assertEqual(поля(п, "RTP")["rtp.marker"].сырое, 1)
        for событие, имя in ((10, "DTMF *"), (11, "DTMF #"), (15, "DTMF D"), (16, "flash"), (0, "DTMF 0"), (40, "40")):
            with self.subTest(событие):
                п = по_udp(rtp(101, bytes([событие, 0x4A]) + struct.pack(">H", 160)), 30000, 40000, шаблоны=ш)
                self.assertEqual(п.инфо, f"RTP Event {имя}, громкость 10, длительность 160")
                self.assertEqual(поля(п, "RTPEvent")["rtpevent.reserved"].сырое, 1)
        # Меньше 4 байт нагрузки — только RTP.
        п = по_udp(rtp(101, b"\x05\x0a\x03"), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.стек[-1], "RTP")
        self.assertTrue(п.инфо.startswith("RTP telephone-event/8000, тип 101"), п.инфо)

    def test_добивка_расширение_csrc(self):
        ш = общий()
        доп = struct.pack(">II", 0xA, 0xB) + struct.pack(">HH", 0xBEDE, 1) + b"\x10\x20\x30\x40"
        нагрузка = bytes([1, 0x05]) + struct.pack(">H", 320) + b"\x00\x00\x03"
        п = по_udp(rtp(101, нагрузка, первый=0x80 | 0x20 | 0x10 | 2, доп=доп), 30000, 40000, шаблоны=ш)
        ф = поля(п, "RTP")
        self.assertEqual((ф["rtp.padding"].сырое, ф["rtp.ext"].сырое, ф["rtp.cc"].сырое), (1, 1, 2))
        self.assertEqual([x.текст for x in п.уровни[-2].поля if x.ключ == "rtp.csrc.item"], ["0x0000000a", "0x0000000b"])
        self.assertEqual((ф["rtp.ext.profile"].текст, ф["rtp.ext.profile"].смещение), ("0xbede", 42 + 20))
        self.assertEqual((ф["rtp.ext.len"].сырое, ф["rtp.padding.count"].сырое), (8, 3))
        self.assertEqual(ф["rtp.padding.count"].смещение, 42 + 12 + 8 + 8 + 7 - 1)
        self.assertEqual(п.уровни[-2].длина, 28)
        self.assertEqual(п.уровни[-1].смещение, 42 + 28)
        self.assertEqual(п.инфо, "RTP Event DTMF 1, громкость 5, длительность 320")
        self.assertEqual(п.нагрузка, нагрузка[:4])
        # Без кодека в SDP и RFC 3551 — только тип; добивка 0 и больше данных — не RTP.
        п = по_udp(rtp(99), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.инфо, "RTP тип 99, SSRC 0x11223344, номер 7")
        self.assertNotIn("rtp.codec", поля(п, "RTP"))
        for плохой, что in ((rtp(8, b"\x00\x00", первый=0xA0), "добивка 0"),
                            (rtp(8, b"\x00\x09", первый=0xA0), "добивка длиннее"),
                            (rtp(8, b"", первый=0x81), "CSRC не помещается"),
                            (rtp(8, b"\xbe\xde\x00", первый=0x90), "нет заголовка расширения"),
                            (rtp(8, b"\xbe\xde\x00\x02\x00\x00\x00\x00", первый=0x90), "расширение длиннее"),
                            (rtp(8, первый=0x40), "версия 1"), (rtp(8)[:11], "короче 12")):
            with self.subTest(что):
                п = по_udp(плохой, 30000, 40000, шаблоны=ш)
                self.assertNotIn("по SDP", " ".join(у.полное for у in п.уровни))
        п = по_udp(rtp(8, b"\x00\x00\x00\x04", первый=0xA0), 30000, 40000, шаблоны=ш)
        self.assertEqual((поля(п, "RTP")["rtp.padding.count"].сырое, п.нагрузка), (4, b""))
        п = по_udp(rtp(8, b"\xbe\xde\x00\x01\x00\x00\x00\x00", первый=0x90), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.уровни[-1].длина, 20)
        п = по_udp(rtp(8, b"", первый=0x81, доп=b"\x00\x00\x00\x01"), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.уровни[-1].длина, 16)

    def test_rtcp(self):
        ш = общий()
        sr = bytes([0x80, 200]) + struct.pack(">HI", 6, 0x11223344) + bytes(20)
        п = по_udp(sr, 30001, 40001, шаблоны=ш)
        self.assertEqual((п.стек[-1], п.инфо), ("RTCP", "RTCP отчёт отправителя (SR)"))
        # RTCP на порту RTP (RFC 5761).
        п = по_udp(bytes([0x81, 201]) + struct.pack(">HI", 1, 5), 30000, 40000, шаблоны=ш)
        self.assertEqual((п.стек[-1], п.инфо), ("RTCP", "RTCP отчёт получателя (RR)"))
        п = по_udp(bytes([0x81, 204]) + struct.pack(">HI", 1, 5), 30000, 40000, шаблоны=ш)
        self.assertEqual(п.стек[-1], "RTCP")
        # Длина RTCP (слова минус один) не больше пакета; 8 байт — наименьший RR; RTP на порту RTCP — от 12 байт.
        rr = bytes([0x80, 201]) + struct.pack(">HI", 1, 5)
        self.assertEqual(по_udp(rr, 30001, 40001, шаблоны=ш).стек[-1], "RTCP")
        self.assertEqual(по_udp(rr + bytes(4), 30001, 40001, шаблоны=ш).стек[-1], "RTCP")
        for плохой in (bytes([0x80, 201]) + struct.pack(">HI", 2, 5), rr[:7], bytes([0x40]) + rr[1:], rtp(8)[:11]):
            with self.subTest(плохой=плохой.hex()):
                self.assertNotIn("RTCP", по_udp(плохой, 30001, 40001, шаблоны=ш).стек)
                self.assertNotIn("RTP", по_udp(плохой, 30001, 40001, шаблоны=ш).стек)
        # Тип 199 и 205 — не RTCP: на порту RTP это RTP с типом 71/77.
        for тип in (199, 205):
            п = по_udp(bytes([0x80, тип]) + bytes(10), 30000, 40000, шаблоны=ш)
            self.assertEqual((п.стек[-1], поля(п, "RTP")["rtp.p_type"].сырое), ("RTP", тип & 0x7F))

    def test_порядок_адрес_порт(self):
        ш = общий()
        # Пакет раньше описания (или тот же номер) не связывается с потоком.
        for номер in (1, 0):
            п = по_udp(rtp(101, bytes([5, 0x8A, 0, 1])), 30000, 40000, номер=номер, шаблоны=ш)
            self.assertNotIn("RTPEvent", п.стек)
        for src, dst, к in (("10.0.0.2", "10.0.0.3", 40000), ("10.0.0.2", "10.0.0.1", 40002)):
            п = по_udp(rtp(101, bytes([5, 0x8A, 0, 1])), 30000, к, src=src, dst=dst, шаблоны=ш)
            self.assertNotIn("RTPEvent", п.стек)
        # Без SDP в захвате и без общего словаря — обычный разбор.
        self.assertNotIn("RTPEvent", по_udp(rtp(101, bytes([5, 0x8A, 0, 1])), 30000, 40000, шаблоны={}).стек)
        self.assertNotIn("RTPEvent", по_udp(rtp(101, bytes([5, 0x8A, 0, 1])), 30000, 40000).стек)
        # Поток с портом 0 (отклонён) не запоминается.
        self.assertNotIn("10.0.0.1|0", ш[media.ПОТОКИ])
        self.assertNotIn("10.0.0.1|1", ш[media.ПОТОКИ])

    def test_адреса_sdp(self):
        self.assertEqual(media._адрес("2001:0db8:0:0::5"), "2001:db8::5")
        self.assertEqual(media._адрес("224.2.1.1/127/3"), "224.2.1.1")
        self.assertEqual(media._адрес("host.example"), "host.example")
        sdp = b"v=0\r\no=- 1 1 IN IP6 2001:db8::1\r\ns=-\r\nc=IN IP6 2001:0db8:0:0::5\r\nt=0 0\r\nm=audio 5004 RTP/AVP 0\r\n"
        ш = общий(sdp)
        п = по_udp6(rtp(0), 30000, 5004, "2001:db8::7", "2001:db8::5", шаблоны=ш)
        self.assertEqual(поля(п, "RTP")["rtp.codec"].текст, "PCMU/8000")
        # Поток без адреса (нет c= ни у сеанса, ни у потока) не запоминается.
        ш = {}
        по_udp(invite(b"v=0\r\no=- 1 1 IN IP4 10.0.0.1\r\ns=-\r\nm=audio 5004 RTP/AVP 0\r\n"), 5060, 5060, номер=1,
               шаблоны=ш)
        self.assertEqual(ш[media.ПОТОКИ], {})


# -- T.38 ------------------------------------------------------------------------------------------

CSI = кадр_t30(0x02, номер_t30("+7 495 1234567"))


class ТестT38(unittest.TestCase):
    def разбор(self, данные, **кв):
        ш = общий()
        return по_udp(данные, 30000, 40010, dst="10.0.0.9", шаблоны=ш, **кв)

    def test_индикатор(self):
        п = self.разбор(udptl(b"\x04"))
        self.assertEqual(п.стек[-2:], ["UDPTL", "T.38"])
        ф = поля(п, "UDPTL")
        self.assertEqual((ф["udptl.seqnum"].сырое, ф["udptl.primary_ifp_packet_length"].сырое), (5, 1))
        self.assertEqual((ф["udptl.error_recovery"].текст, ф["udptl.error_recovery"].сырое), ("предыдущих IFP: 0", 0))
        self.assertEqual([(x.ключ, x.смещение, x.длина) for x in п.уровни[-2].поля],
                         [("udptl.seqnum", 42, 2), ("udptl.primary_ifp_packet_length", 44, 1),
                          ("udptl.error_recovery", 46, 2)])
        т = поля(п, "T.38")
        self.assertEqual((т["t38.Type_of_msg"].текст, т["t38.t30_indicator"].текст), ("t30-indicator", "ced"))
        self.assertEqual((т["t38.t30_indicator"].сырое, т["t38.t30_indicator"].длина), (2, 1))
        self.assertEqual(п.инфо, "T.38 ced, номер 5")
        self.assertEqual((п.уровни[-2].длина, п.уровни[-1].смещение, п.уровни[-1].длина), (3, 45, 1))
        for ifp, имя in ((b"\x02", "cng"), (b"\x1e", "v17-14400-long-training"), (b"\x20\x00", "v8-ansam"),
                         (b"\x21\x00", "v34-CC-retrain"), (b"\x21\x80", "v33-14400-training"),
                         (b"\x2f\xc0", "79")):
            with self.subTest(имя):
                self.assertEqual(self.разбор(udptl(ifp)).инфо, f"T.38 {имя}, номер 5")
        т = поля(self.разбор(udptl(b"\x21\x40")), "T.38")
        self.assertEqual((т["t38.t30_indicator"].сырое, т["t38.t30_indicator"].длина), (21, 2))

    def test_скорости_и_расширение(self):
        for ifp, имя in ((b"\x48", "v29-9600"), (b"\x50", "v17-14400"), (b"\x60\x00", "v8"), (b"\x61\x40", "v33-14400"),
                         (b"\x60\x40", "v34-pri-rate")):
            with self.subTest(имя):
                п = self.разбор(udptl(ifp))
                т = поля(п, "T.38")
                self.assertEqual((т["t38.Type_of_msg"].текст, т["t38.t30_data"].текст), ("t30-data", имя))
                self.assertEqual(п.инфо, f"T.38 {имя}, номер 5")
        п = self.разбор(udptl(b"\x20", восстановление=b""))
        self.assertEqual(п.ошибки, ["T.38: расширенное значение оборвано"])
        self.assertEqual(п.инфо, "T.38 оборван, номер 5")

    def test_csi_в_hdlc_data(self):
        ifp = b"\xc0\x01" + элемент(0, CSI)
        п = self.разбор(udptl(ifp))
        self.assertEqual(п.стек[-3:], ["UDPTL", "T.38", "T.30"])
        т = поля(п, "T.38")
        self.assertEqual((т["t38.field_type"].текст, т["t38.field_type"].смещение), ("hdlc-data", 47))
        self.assertEqual((т["t38.field_data"].текст, т["t38.field_data"].смещение, т["t38.field_data"].длина),
                         ("23 байт", 50, 23))
        ф = поля(п, "T.30")
        self.assertEqual((ф["t30.Facsimile_Control"].текст, ф["t30.Facsimile_Control"].сырое), ("CSI", 2))
        self.assertEqual((ф["t30.fif.number"].текст, ф["t30.fif.number"].смещение), ("+7 495 1234567", 53))
        self.assertEqual(ф["t30.Control"].текст, "не последний")
        self.assertEqual((п.уровни[-1].смещение, п.уровни[-1].длина), (50, 23))
        self.assertEqual(п.инфо, "T.38 v21, hdlc-data (CSI +7 495 1234567), номер 5")
        # TSI с битом X, CIG, DTC (группа 1000 XXXX) и DCS; последний кадр — 0xC8.
        for fcf, имя in ((0xC2, "TSI"), (0x42, "TSI"), (0x82, "CIG"), (0x83, "PWD"), (0x85, "SEP"), (0x86, "PSA"),
                         (0xC3, "SUB"), (0x45, "SID")):
            with self.subTest(имя):
                п = self.разбор(udptl(b"\xc0\x01" + элемент(0, кадр_t30(fcf, номер_t30("123"), 0xC8))))
                self.assertEqual(п.инфо, f"T.38 v21, hdlc-data ({имя} 123), номер 5")
                self.assertEqual(поля(п, "T.30")["t30.Control"].текст, "последний кадр")
        for fcf, имя in ((0x81, "DTC"), (0xC1, "DCS"), (0x01, "DIS"), (0xDF, "DCN"), (0x5F, "DCN"), (0xFF, "0xff"), (0x84, "NSC"), (0x80, "0x80"),
                         (0x04, "NSF")):
            with self.subTest(имя):
                п = self.разбор(udptl(b"\xc0\x01" + элемент(0, кадр_t30(fcf, номер_t30("123")))))
                self.assertEqual(п.инфо, f"T.38 v21, hdlc-data ({имя}), номер 5")
        # Номер не из 20 знаков — только имя кадра.
        п = self.разбор(udptl(b"\xc0\x01" + элемент(0, CSI[:-1])))
        self.assertEqual(п.инфо, "T.38 v21, hdlc-data (CSI), номер 5")
        self.assertNotIn("t30.fif.number", поля(п, "T.30"))
        п = self.разбор(udptl(b"\xc0\x01" + элемент(0, CSI + b"\x00")))
        self.assertEqual(п.инфо, "T.38 v21, hdlc-data (CSI), номер 5")

    def test_не_t30_и_виды(self):
        for кадр in (b"\xff\xc4\x02", b"\xfe\xc0\x02", b"\xff\xc0"):
            with self.subTest(кадр=кадр.hex()):
                п = self.разбор(udptl(b"\xc0\x01" + элемент(0, кадр)))
                self.assertNotIn("T.30", п.стек)
                self.assertEqual(п.инфо, "T.38 v21, hdlc-data, номер 5")
        # T.30 разбирается в hdlc-data, hdlc-fcs-OK и hdlc-fcs-OK-sig-end; в прочих — нет.
        for вид, есть in ((0, True), (1, False), (2, True), (3, False), (4, True), (5, False), (6, False), (7, False)):
            with self.subTest(вид=вид):
                п = self.разбор(udptl(b"\xc0\x01" + элемент(вид, кадр_t30(0x21))))
                self.assertEqual("T.30" in п.стек, есть)
                self.assertEqual(поля(п, "T.38")["t38.field_type"].текст, media.ВИДЫ_ПОЛЕЙ[вид])

    def test_несколько_полей_по_битам(self):
        # Два элемента без field-data — в одном октете (0 010 0 001): выровненный PER не добивает их до октета.
        п = self.разбор(udptl(b"\xc0\x02\x21"))
        т = [x for x in уровень(п, "T.38").поля if x.ключ == "t38.field_type"]
        self.assertEqual([(x.текст, x.смещение) for x in т], [("hdlc-fcs-OK", 47), ("hdlc-sig-end", 47)])
        self.assertEqual(п.инфо, "T.38 v21, hdlc-fcs-OK, hdlc-sig-end, номер 5")
        self.assertEqual(п.ошибки, [])
        # Без данных, затем с данными: 0 010 1 000, выравнивание, длина.
        п = self.разбор(udptl(b"\xc0\x02\x28\x00\x02" + кадр_t30(0x31)))
        т = [x for x in уровень(п, "T.38").поля if x.ключ == "t38.field_type"]
        self.assertEqual([(x.текст, x.смещение) for x in т], [("hdlc-fcs-OK", 47), ("hdlc-data", 47)])
        self.assertEqual(поля(п, "T.38")["t38.field_data"].смещение, 50)
        self.assertEqual(п.инфо, "T.38 v21, hdlc-fcs-OK, hdlc-data (MCF), номер 5")
        # С данными, затем без: второй элемент — со следующего октета.
        п = self.разбор(udptl(b"\xc0\x02" + элемент(0, кадр_t30(0x31)) + b"\x40"))
        т = [x for x in уровень(п, "T.38").поля if x.ключ == "t38.field_type"]
        self.assertEqual([(x.текст, x.смещение) for x in т], [("hdlc-data", 47), ("hdlc-fcs-OK-sig-end", 53)])
        self.assertEqual(п.инфо, "T.38 v21, hdlc-data (MCF), hdlc-fcs-OK-sig-end, номер 5")

    def test_оборванные_поля(self):
        for ifp, что in ((b"\xc0\x03\x20", "третьего элемента нет"), (b"\xc0\x01\x80\x00", "нет длины"),
                         (b"\xc0\x01\x80\x00\x03\xff\xc0\x21", "данные короче"), (b"\xc0\x01", "нет элемента")):
            with self.subTest(что):
                п = self.разбор(udptl(ifp))
                self.assertEqual(п.ошибки, ["T.38: поле данных оборвано"])
        # Ровно по границе — без ошибок.
        п = self.разбор(udptl(b"\xc0\x01\x80\x00\x02\xff\xc0\x21"))
        self.assertEqual((п.ошибки, п.инфо), ([], "T.38 v21, hdlc-data (CFR), номер 5"))
        п = self.разбор(udptl(b"\xc0\x01\x80\x00\x01\xff\xc0"))
        self.assertEqual((п.ошибки, поля(п, "T.38")["t38.field_data"].длина), ([], 2))
        # Бит наличия data-field при пустом остатке IFP — нет определителя длины, полей нет.
        п = self.разбор(udptl(b"\xc0"))
        self.assertEqual((п.ошибки, п.инфо), ([], "T.38 v21, номер 5"))
        # Без бита наличия data-field следующие байты не разбираются.
        п = self.разбор(udptl(b"\x40\x01\x80\x00\x02\xff\xc0\x21"))
        self.assertEqual((п.инфо, п.стек[-1]), ("T.38 v21, номер 5", "T.38"))

    def test_длина_и_восстановление(self):
        ifp = b"\xc0\x01" + элемент(6, bytes(200))
        данные = struct.pack(">H", 9) + struct.pack(">H", 0x8000 | len(ifp)) + ifp
        п = self.разбор(данные)
        ф = поля(п, "UDPTL")
        self.assertEqual((ф["udptl.primary_ifp_packet_length"].сырое, ф["udptl.primary_ifp_packet_length"].длина),
                         (len(ifp), 2))
        self.assertEqual(п.уровни[-2].длина, 4)
        self.assertEqual(п.уровни[-1].смещение, 46)
        self.assertEqual(п.инфо, "T.38 v21, t4-non-ecm-data, номер 9")
        self.assertNotIn("udptl.error_recovery", ф)
        п = self.разбор(udptl(b"\x04", восстановление=b"\x80\x01\x02\x03"))
        ф = поля(п, "UDPTL")
        self.assertEqual((ф["udptl.error_recovery"].текст, ф["udptl.error_recovery"].длина), ("FEC", 4))
        п = self.разбор(udptl(b"\x04", восстановление=b"\x00\x02\x01\x04\x01\x02"))
        self.assertEqual(поля(п, "UDPTL")["udptl.error_recovery"].текст, "предыдущих IFP: 2")
        п = self.разбор(udptl(b"\x04", восстановление=b"\x00"))
        self.assertEqual(поля(п, "UDPTL")["udptl.error_recovery"].текст, "предыдущие IFP")
        for плохое, что in ((struct.pack(">HB", 1, 0) + b"\x04", "длина 0"), (struct.pack(">HB", 1, 5) + b"\x04", "длиннее"),
                            (b"\x00\x01\xc1\x04", "фрагмент"), (b"\x00\x01\x80", "короче 4"),
                            (b"\x00\x01\x81\x04", "длина в 2 байта без второго")):
            with self.subTest(что):
                self.assertNotIn("UDPTL", self.разбор(плохое).стек)

    def test_как(self):
        п = по_udp(udptl(b"\x04"), 30000, 4000, как={"udp:4000": "UDPTL (T.38)"})
        self.assertEqual((п.стек[-2:], п.инфо), (["UDPTL", "T.38"], "T.38 ced, номер 5"))

    def test_длина_per(self):
        self.assertEqual(media._длина_per(b"\x05", 0, 1), (5, 1))
        self.assertEqual(media._длина_per(b"\x7f", 0, 1), (127, 1))
        self.assertEqual(media._длина_per(b"\x81\x02", 0, 2), (0x102, 2))
        self.assertEqual(media._длина_per(b"\xbf\xff", 0, 2), (0x3FFF, 2))
        self.assertIsNone(media._длина_per(b"\xc1\x00", 0, 2))
        self.assertIsNone(media._длина_per(b"\x81", 0, 1))
        self.assertIsNone(media._длина_per(b"", 0, 0))
        self.assertEqual(media._биты(b"\x5a\xc3", 4, 8), 0xAC)
        self.assertEqual(media._биты(b"\x80", 0, 1), 1)
        self.assertEqual(media._t30_номер(номер_t30("  12 ")), "12")


class ТестРегистрация(unittest.TestCase):
    def test_регистрация(self):
        self.assertIn(media._запомнить, promyshlennye.SDP_ОПИСАНИЯ)
        self.assertIn(media.медиа, prilozh.ПЕРЕД_ПОРТАМИ)
        self.assertIs(prilozh.КАК["udp"]["UDPTL (T.38)"], media.udptl)
        for имя in ("RTPEvent", "UDPTL", "T.38", "T.30"):
            self.assertEqual(ДОП_УРОВНИ[имя], "прикладной")
        self.assertEqual(len(media.T30_FCF), 50)
        self.assertEqual((len(media.T30_ИНДИКАТОРЫ), len(media.T30_ДАННЫЕ), len(media.ВИДЫ_ПОЛЕЙ)), (23, 15, 8))
        self.assertEqual(len(media.СТАТИЧЕСКИЕ), 24)


if __name__ == "__main__":
    unittest.main()
