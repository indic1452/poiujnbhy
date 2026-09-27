"""CAMEL (CAP, 3GPP TS 29.078) поверх TCAP: операции собираются в BER (X.690) по ASN.1 стандарта — InitialDPArg,
InitialDPSMSArg, ConnectArg, EventReportBCSMArg, ReleaseCallArg; номера — ISUP (Q.763 3.9/3.10) и TBCD."""

import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy.protokoly import camel
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет
from test_setevoy_oks7 import IMSI, bcd, ber, sccp_udt, адрес_pc, диалог

CAP_V2 = bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x32, 0x01])
CAP_V4 = bytes([0x04, 0x00, 0x00, 0x01, 0x16, 0x03, 0x04])
MAP_UL = bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x01, 0x03])


def тег2(номер, значение, составной=False):
    """Тег контекста больше 30 (X.690 8.1.2.4): 0x9F/0xBF и номер в следующем октете."""
    return bytes([0xBF if составной else 0x9F, номер, len(значение)]) + значение


def isup(цифры, np_байт=0x10, nai=4):
    return bytes([(0x80 if len(цифры) % 2 else 0) | nai, np_байт]) + bcd(цифры, 0)


def вызов(код, аргумент, контекст=CAP_V2, компонент=0xA1):
    invoke = ber(компонент, ber(0x02, b"\x01") + ber(0x02, bytes([код])) + аргумент)
    части = ber(0x48, b"\x01\x02\x03\x04") + (диалог(контекст) if контекст else b"") + ber(0x6C, invoke)
    return ber(0x62, части)


def через_udt(tcap, ssn=146):
    return разобрать_пакет(sccp_udt(адрес_pc(1, ssn), адрес_pc(2, ssn), tcap), "SCCP")


def поля(п):
    у = [x for x in п.уровни if x.протокол == "CAMEL"][0]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


INITIAL_DP = ber(0x30, ber(0x80, b"\x64") + ber(0x82, isup("79161234567")) + ber(0x83, isup("74951112233", 0x13))
                 + ber(0x9C, b"\x02") + тег2(50, bcd(IMSI, 0xF)) + тег2(56, b"\x91" + bcd("79161234567", 0xF)))


class ТестCAP(unittest.TestCase):
    def test_initial_dp(self):
        п = через_udt(вызов(0, INITIAL_DP))
        self.assertEqual(["SCCP", "TCAP", "CAMEL"], [у.протокол for у in п.уровни])
        ф = поля(п)
        self.assertEqual(ф["camel.opcode"].текст, "0 (initialDP)")
        self.assertEqual(ф["camel.serviceKey"].сырое, 100)
        self.assertEqual(ф["camel.callingPartyNumber"].текст, "74951112233")
        self.assertEqual(ф["camel.calledPartyNumber"].текст, "79161234567")
        self.assertEqual(ф["camel.calledPartyBCDNumber"].текст, "79161234567")
        self.assertEqual(ф["e212.imsi"].текст, IMSI)
        self.assertEqual(ф["camel.eventTypeBCSM"].текст, "collectedInfo")
        self.assertEqual(ф["camel.application_context"].текст, "0.4.0.0.1.0.50.1")
        self.assertEqual(п.инфо, "CAMEL initialDP: ключ услуги 100, вызывающий 74951112233, вызываемый 79161234567, "
                                 f"вызываемый (BCD) 79161234567, IMSI {IMSI}, событие collectedInfo")
        у = п.уровни[-1]
        ключ = ф["camel.serviceKey"]
        self.assertEqual(п.данные[ключ.смещение:ключ.смещение + ключ.длина], b"\x64")
        imsi = ф["e212.imsi"]
        self.assertEqual(п.данные[imsi.смещение:imsi.смещение + imsi.длина], bcd(IMSI, 0xF))
        self.assertGreater(у.длина, 0)

    def test_узнавание(self):
        for контекст, ssn, есть in ((CAP_V2, 6, True), (CAP_V4, 6, True), (None, 146, True), (None, 6, False),
                                    (MAP_UL, 146, False), (bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x35, 0x01]), 146, False),
                                    (bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x31, 0x01]), 146, False),
                                    (bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x34, 0x01]), 146, True),
                                    (bytes([0x04, 0x00, 0x00, 0x01, 0x15, 0x03, 0x3D]), 6, True),
                                    (bytes([0x04, 0x00, 0x00, 0x02, 0x16, 0x03, 0x04]), 146, False),
                                    (bytes([0x04, 0x00, 0x00, 0x01, 0x17, 0x03, 0x04]), 146, False),
                                    (bytes([0x04, 0x00, 0x00, 0x01, 0x16]), 146, False)):
            with self.subTest(контекст=контекст, ssn=ssn):
                п = через_udt(вызов(0, INITIAL_DP, контекст), ssn)
                self.assertEqual("CAMEL" in [у.протокол for у in п.уровни], есть)

    def test_initial_dp_sms(self):
        аргумент = ber(0x30, ber(0x80, b"\x05") + ber(0x81, b"\x91" + bcd("79160000001", 0xF))
                       + ber(0x82, b"\x91" + bcd("79160000002", 0xF)) + ber(0x83, b"\x03")
                       + ber(0x84, bcd(IMSI, 0xF)) + ber(0x87, b"\x91" + bcd("79168999999", 0xF)))
        п = через_udt(вызов(60, аргумент, CAP_V4))
        ф = поля(п)
        self.assertEqual(ф["camel.serviceKey"].сырое, 5)
        self.assertEqual(ф["camel.destinationSubscriberNumber"].текст, "79160000001")
        self.assertEqual(ф["camel.callingPartyNumber"].текст, "79160000002")
        self.assertEqual(ф["camel.eventTypeSMS"].текст, "o-smsSubmission")
        self.assertEqual(ф["camel.sMSCAddress"].текст, "79168999999")
        self.assertEqual(ф["e212.imsi"].текст, IMSI)
        self.assertEqual(п.инфо, "CAMEL initialDPSMS: ключ услуги 5, отправитель 79160000002, получатель 79160000001, "
                                 f"SMSC 79168999999, IMSI {IMSI}, событие o-smsSubmission")

    def test_connect_release_event(self):
        п = через_udt(вызов(20, ber(0x30, ber(0xA0, ber(0x04, isup("74950000000"))))))
        self.assertEqual(поля(п)["camel.destinationRoutingAddress"].текст, "74950000000")
        self.assertEqual(п.инфо, "CAMEL connect: на номер 74950000000")
        п = через_udt(вызов(22, ber(0x04, b"\x80\x90")))
        self.assertEqual(поля(п)["camel.cause"].текст, "16 (нормальное освобождение)")
        self.assertEqual(через_udt(вызов(22, ber(0x04, b"\x80\xe6"))).инфо, "CAMEL releaseCall: причина 102 (неизвестная)")
        self.assertEqual(через_udt(вызов(22, ber(0x04, b"\x80"))).инфо, "CAMEL releaseCall")
        п = через_udt(вызов(24, ber(0x30, ber(0x80, b"\x07"))))
        self.assertEqual(п.инфо, "CAMEL eventReportBCSM: событие oAnswer")
        self.assertEqual(через_udt(вызов(24, ber(0x30, ber(0x80, b"\x63")))).инфо, "CAMEL eventReportBCSM: событие 99")

    def test_результаты_ошибки_неизвестные(self):
        результат = ber(0xA2, ber(0x02, b"\x01") + ber(0x30, ber(0x02, b"\x00") + ber(0x30, b"")))
        п = через_udt(ber(0x64, ber(0x49, b"\x01") + диалог(CAP_V2) + ber(0x6C, результат)))
        self.assertEqual(п.инфо, "CAMEL initialDP (результат)")
        п = через_udt(вызов(31, b""))
        self.assertEqual(п.инфо, "CAMEL continue")
        п = через_udt(вызов(99, b""))
        self.assertEqual((поля(п)["camel.opcode"].текст, п.инфо), ("99", "CAMEL 99"))
        ошибка = ber(0x64, ber(0x49, b"\x01") + ber(0x6C, ber(0xA3, ber(0x02, b"\x01") + ber(0x02, b"\x0d"))))
        п = через_udt(ошибка)
        self.assertEqual((поля(п)["camel.error_code"].сырое, п.инфо), (13, "CAMEL ошибка 13"))
        # Аргумент не SEQUENCE — только имя операции.
        self.assertEqual(через_udt(вызов(0, ber(0x04, b"\x01\x02"))).инфо, "CAMEL initialDP")
        # Номера без цифр не показываются.
        аргумент = ber(0x30, ber(0x80, b"\x01") + ber(0x83, b"\x84\x13") + тег2(56, b"\x91"))
        п = через_udt(вызов(0, аргумент))
        self.assertEqual(п.инфо, "CAMEL initialDP: ключ услуги 1")

    def test_ber(self):
        self.assertEqual(camel._ber(b"\x9f\x32\x02ab", 0, 5), (0x80, 50, 3, 5))
        self.assertEqual(camel._ber(b"\xbf\x81\x00\x01x", 0, 5), (0xA0, 128, 4, 5))
        self.assertEqual(camel._ber(b"\x04\x81\x02ab", 0, 5), (0x00, 4, 3, 5))
        self.assertEqual(camel._ber(b"\x04\x82\x00\x02ab", 0, 6), (0x00, 4, 4, 6))
        self.assertIsNone(camel._ber(b"\x04\x84\x00\x00\x00\x01a", 0, 7))
        self.assertIsNone(camel._ber(b"\x04\x80", 0, 2))
        self.assertIsNone(camel._ber(b"\x04\x03ab", 0, 4))
        self.assertIsNone(camel._ber(b"\x04", 0, 1))
        self.assertIsNone(camel._ber(b"\x9f\xb2", 0, 2))
        self.assertIsNone(camel._ber(b"\x04\x82\x00", 0, 3))
        self.assertEqual(camel._ber(b"\x04\x00", 0, 2), (0x00, 4, 2, 2))
        self.assertEqual(camel._элементы(b"\x80\x01\x05\x04\x01\x07\x81\x00\x80\x01\x09", 0, 11),
                         {0: (2, 3), 1: (8, 8)})
        self.assertEqual(camel._элементы(b"\x80\x05\x01", 0, 3), {})
        self.assertEqual(ДОП_УРОВНИ["CAMEL"], "прикладной")


if __name__ == "__main__":
    unittest.main()
