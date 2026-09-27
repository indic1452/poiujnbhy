"""IPA (ip.access, Abis поверх IP: packet-gsm_ipa.c) и XOT (RFC 1613, X.25 поверх TCP) — сообщения собираются
struct.pack'ом; вложенные RSL/OML/SCCP/MGCP/X.25 — те же, что в тестах своих разборщиков."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy.protokoly import ipa_xot
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет
from test_setevoy_abis import LU, l3_info
from test_setevoy_oks7 import sccp_udt, tcap_begin_update_location, адрес_gt, адрес_pc
from test_setevoy_promyshlennye import CRCX

RSL = bytes([0x02, 0x02, 0x01, (0x08 | 2) << 3 | 1, 0x02, 0x00]) + l3_info(LU)
FOM = bytes([0x74, 0x02, 0x00, 0x01, 0xFF])
OML = bytes([0x80, 0x80, 0x00, len(FOM)]) + FOM
ВЫЗОВ_X25 = b"\x10\x01\x0b\x45\x12\x34\x56\x78\x90\x00\xcc\x00\x00\x00"


def ipa(протокол, тело):
    return struct.pack(">HB", len(тело), протокол) + тело


def по_tcp(данные, порт=3002):
    return разобрать_пакет(с.eth(с.ip(с.tcp(данные, 40000, порт), 6)))


def стек(п):
    return [у.протокол for у in п.уровни][3:]


def поля(п, протокол, номер=0):
    у = [x for x in п.уровни if x.протокол == протокол][номер]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


class ТестIPA(unittest.TestCase):
    def test_rsl(self):
        п = по_tcp(ipa(0x00, RSL))
        self.assertEqual(стек(п)[:3], ["IPA", "RSL", "GSM MM"])
        ф = поля(п, "IPA")
        self.assertEqual((ф["gsm_ipa.data_len"].сырое, ф["gsm_ipa.protocol"].текст), (len(RSL), "RSL (TRX 0)"))
        self.assertEqual((ф["gsm_ipa.data_len"].смещение, ф["gsm_ipa.protocol"].смещение), (54, 56))
        у = п.уровни[3]
        self.assertEqual((у.длина, у.итог), (3, "RSL (TRX 0)"))
        self.assertEqual(п.уровни[4].смещение, 57)
        п = по_tcp(ipa(0x1F, RSL))
        self.assertEqual(поля(п, "IPA")["gsm_ipa.protocol"].текст, "RSL (TRX 31)")
        self.assertEqual(стек(п)[1], "RSL")

    def test_oml_sccp_mgcp(self):
        п = по_tcp(ipa(0xFF, OML), 3006)
        self.assertEqual(стек(п), ["IPA", "OML"])
        self.assertEqual(п.инфо, "OML Opstart: Radio Carrier (0, 1, 255)")
        udt = sccp_udt(адрес_gt(6, "79161234567"), адрес_pc(1234, 7), tcap_begin_update_location())
        п = по_tcp(ipa(0xFD, udt), 3003)
        self.assertEqual(стек(п)[:2], ["IPA", "SCCP"])
        п = по_tcp(ipa(0xFC, CRCX), 4249)
        self.assertEqual(стек(п)[:2], ["IPA", "MGCP"])
        self.assertTrue(п.инфо.startswith("MGCP"), п.инфо)
        self.assertEqual(п.уровни[3].итог, "MGCP")
        п = по_tcp(ipa(0xFF, b"\x01\x02"))
        self.assertEqual((п.инфо, п.уровни[3].итог), ("IPA OML", "OML"))

    def test_ccm(self):
        п = по_tcp(ipa(0xFE, b"\x00"))
        self.assertEqual(стек(п), ["IPA"])
        self.assertEqual(п.инфо, "IPA PING")
        self.assertEqual(п.уровни[3].длина, 4)
        запрос = b"\x04" + b"\x01\x08" + b"\x01\x01" + b"\x01\x00"
        п = по_tcp(ipa(0xFE, запрос))
        self.assertEqual(п.инфо, "IPA IDENTITY REQUEST: Unit ID, Unit Name, Serial Number")
        метки = [x for x in п.уровни[3].поля if x.ключ == "ipaccess.attr_tag"]
        self.assertEqual([(x.смещение, x.длина, x.сырое) for x in метки], [(58, 2, 8), (60, 2, 1), (62, 2, 0)])
        ответ = b"\x05" + b"\x00\x0a\x08" + b"1801/0/0\x00" + b"\x00\x06\x01BTS1\x00"
        п = по_tcp(ipa(0xFE, ответ))
        self.assertEqual(п.инфо, "IPA IDENTITY RESPONSE: Unit ID=1801/0/0, Unit Name=BTS1")
        строки = [x for x in п.уровни[3].поля if x.ключ == "ipaccess.attr_string"]
        self.assertEqual([(x.смещение, x.длина, x.сырое, x.имя) for x in строки],
                         [(58, 12, 8, "Unit ID"), (70, 8, 1, "Unit Name")])
        self.assertEqual(поля(п, "IPA")["ipaccess.msg_type"].смещение, 57)
        self.assertEqual(по_tcp(ipa(0xFE, b"\x06")).инфо, "IPA IDENTITY ACK")
        self.assertEqual(по_tcp(ipa(0xFE, b"\x30")).инфо, "IPA CCM 0x30")
        self.assertEqual(по_tcp(ipa(0xFE, b"\x00") + ipa(0xFE, b"")).инфо, "IPA пусто")
        self.assertNotIn("IPA", стек(по_tcp(ipa(0xFE, b""))))

    def test_ccm_необычные_атрибуты(self):
        п = по_tcp(ipa(0xFE, b"\x04\x07\x01\x01\x63"))
        ф = поля(п, "IPA")
        self.assertEqual((ф["ipaccess.attr_unk"].сырое, ф["ipaccess.attr_unk"].смещение), (7, 58))
        self.assertEqual(п.инфо, "IPA IDENTITY REQUEST: метка 99".replace("метка 99", "99"))
        п = по_tcp(ipa(0xFE, b"\x05\x00\x09\x01ab"))
        self.assertIn("IPA: атрибут длиннее сообщения", п.ошибки)
        п = по_tcp(ipa(0xFE, b"\x05\x00\x00\x01"))
        self.assertIn("IPA: атрибут длиннее сообщения", п.ошибки)
        п = по_tcp(ipa(0xFE, b"\x05\x00\x01\x02"))
        self.assertEqual(п.инфо, "IPA IDENTITY RESPONSE: Location=")
        п = по_tcp(ipa(0xFE, b"\x05\x00\x03\x63xy"))
        self.assertEqual(п.инфо, "IPA IDENTITY RESPONSE: 99=xy")
        self.assertEqual(поля(п, "IPA")["ipaccess.attr_string"].имя, "метка 99")

    def test_osmocom_и_hsl(self):
        п = по_tcp(ipa(0xEE, b"\x00GET 1 bts.0.location"))
        ф = поля(п, "IPA")
        self.assertEqual(ф["gsm_ipa.osmo.protocol"].текст, "CTRL")
        self.assertEqual((ф["gsm_ipa.osmo.ctrl_data"].текст, ф["gsm_ipa.osmo.ctrl_data"].смещение),
                         ("GET 1 bts.0.location", 58))
        self.assertEqual(п.инфо, "IPA Osmocom CTRL: GET")
        self.assertEqual(стек(п), ["IPA"])
        self.assertEqual(по_tcp(ipa(0xEE, b"\x00")).инфо, "IPA Osmocom CTRL")
        п = по_tcp(ipa(0xEE, b"\x01" + CRCX))
        self.assertEqual(стек(п)[:2], ["IPA", "MGCP"])
        self.assertTrue(п.инфо.startswith("MGCP"), п.инфо)
        self.assertEqual(п.уровни[3].итог, "Osmocom MGCP")
        self.assertEqual(п.уровни[3].длина, 4)
        п = по_tcp(ipa(0xEE, b"\x05\x0a\x01\x08"))
        self.assertEqual(стек(п), ["IPA", "Данные"])
        self.assertEqual(п.уровни[3].итог, "Osmocom GSUP")
        self.assertEqual(п.уровни[4].смещение, 58)
        п = по_tcp(ipa(0xEE, b"\x01not mgcp"))
        self.assertEqual(стек(п), ["IPA", "Данные"])
        self.assertEqual(по_tcp(ipa(0xEE, b"\x09zz")).уровни[3].итог, "Osmocom 0x09")
        п = по_tcp(ipa(0xDD, b"trx 0 up\x00junk"))
        self.assertEqual(поля(п, "IPA")["gsm_ipa.hsl_debug"].текст, "trx 0 up")
        self.assertEqual(п.инфо, "IPA HSL: trx 0 up")

    def test_несколько_сообщений(self):
        п = по_tcp(ipa(0xFE, b"\x00") + ipa(0xFF, OML) + ipa(0xFE, b"\x01"))
        self.assertEqual(стек(п), ["IPA", "IPA", "OML", "IPA"])
        self.assertEqual([у.смещение for у in п.уровни[3:]], [54, 58, 61, 61 + len(OML)])
        self.assertEqual(п.инфо, "IPA PONG")
        # За сообщениями — не IPA: остаток — данные.
        п = по_tcp(ipa(0xFE, b"\x00") + b"\x00\x02\x77ab")
        self.assertEqual(стек(п), ["IPA", "Данные"])
        self.assertEqual(п.уровни[4].смещение, 58)
        п = по_tcp(ipa(0xFE, b"\x00") + b"\x00\x02\x1fab")
        self.assertEqual(стек(п), ["IPA", "IPA", "Данные"])

    def test_оборванное_и_чужое(self):
        п = по_tcp(struct.pack(">HB", 40, 0xFF) + OML)
        self.assertIn("IPA: сообщение длиннее данных (продолжение — в следующем сегменте)", п.ошибки)
        п = по_tcp(ipa(0xFF, OML))
        self.assertFalse(п.ошибки)
        self.assertNotIn("IPA", стек(по_tcp(b"\x00\x05\x50hello")))
        self.assertNotIn("IPA", стек(по_tcp(b"\x00\x00\xfe")))
        self.assertEqual(стек(по_tcp(b"\x00\x01\xfe\x00"))[0], "IPA")
        п = по_tcp(ipa(0x00, b"\x99\x99"))
        self.assertEqual(стек(п), ["IPA", "Данные"])
        self.assertEqual(п.уровни[4].смещение, 57)

    def test_udp_с_лишним_байтом(self):
        данные = struct.pack(">HBB", len(OML), 0xFF, 0) + OML
        п = разобрать_пакет(с.eth(с.ip(с.udp(данные, 1, 2), 17)), как={"udp:2": "IPA"})
        self.assertEqual(стек(п), ["IPA", "OML"])
        self.assertEqual(п.уровни[3].длина, 4)
        п = разобрать_пакет(с.eth(с.ip(с.udp(ipa(0xFF, OML), 1, 2), 17)), как={"udp:2": "IPA"})
        self.assertEqual(стек(п), ["IPA", "OML"])
        self.assertEqual(п.уровни[3].длина, 3)

    def test_раскладка_и_длины(self):
        def раскладка(п, номер=3):
            return [(x.ключ, x.смещение, x.длина) for x in п.уровни[номер].поля]
        п = по_tcp(ipa(0xFE, b"\x04\x01\x08\x07\x01"))
        self.assertEqual(раскладка(п), [
            ("gsm_ipa.data_len", 54, 2), ("gsm_ipa.protocol", 56, 1), ("ipaccess.msg_type", 57, 1),
            ("ipaccess.attr_tag", 58, 2), ("ipaccess.attr_unk", 60, 2)])
        п = по_tcp(ipa(0xDD, b"trx up"))
        self.assertEqual(раскладка(п)[2:], [("gsm_ipa.hsl_debug", 57, 6)])
        self.assertEqual(п.уровни[3].длина, 9)
        п = по_tcp(ipa(0xEE, b"\x00GET 1 x"))
        self.assertEqual(раскладка(п)[2:], [("gsm_ipa.osmo.protocol", 57, 1), ("gsm_ipa.osmo.ctrl_data", 58, 7)])
        self.assertEqual(п.уровни[3].длина, 11)

    def test_атрибуты_на_границе(self):
        # Последний байт после атрибутов — не атрибут (нужно два байта).
        п = по_tcp(ipa(0xFE, b"\x04\x01\x08\x01"))
        self.assertFalse(п.ошибки)
        self.assertEqual(п.инфо, "IPA IDENTITY REQUEST: Unit ID")
        # Строка длиннее сообщения на один байт.
        п = по_tcp(ipa(0xFE, b"\x05\x00\x04\x01ab"))
        self.assertIn("IPA: атрибут длиннее сообщения", п.ошибки)
        # Строка без нуля — ровно своих байт, без соседнего атрибута.
        п = по_tcp(ipa(0xFE, b"\x05\x00\x03\x01ab\x01\x08"))
        self.assertEqual(п.инфо, "IPA IDENTITY RESPONSE: Unit Name=ab, Unit ID")

    def test_граница_протоколов(self):
        self.assertNotIn("IPA", стек(по_tcp(b"\x00\x01\x20\x00")))
        self.assertEqual(стек(по_tcp(b"\x00\x01\x1f\x00"))[0], "IPA")
        п = по_tcp(ipa(0xFE, b"\x00") + b"\x00\x01\x20\x00")
        self.assertEqual(стек(п), ["IPA", "Данные"])
        п = по_tcp(ipa(0xFE, b"\x00") + ipa(0xEE, b"") + ipa(0xFE, b"\x01"))
        self.assertEqual(стек(п), ["IPA", "IPA", "IPA"])
        self.assertEqual(п.уровни[4].итог, "Osmocom")
        self.assertEqual(п.инфо, "IPA PONG")

    def test_остаток_и_обрыв_на_байт(self):
        п = по_tcp(ipa(0xFE, b"\x00") + b"\x00\x01")
        self.assertEqual(стек(п), ["IPA", "Данные"])
        self.assertEqual(п.уровни[4].смещение, 58)
        п = по_tcp(ipa(0xFE, b"\x00") + b"\x07")
        self.assertEqual(стек(п), ["IPA", "Данные"])
        self.assertEqual(п.уровни[3].длина, 4)
        п = по_tcp(struct.pack(">HB", len(OML) + 1, 0xFF) + OML)
        self.assertIn("IPA: сообщение длиннее данных (продолжение — в следующем сегменте)", п.ошибки)

    def test_возврат_при_выборе_аналитика(self):
        п = разобрать_пакет(с.eth(с.ip(с.tcp(ipa(0xFE, b"\x00"), 1, 2), 6)), как={"tcp:2": "IPA"})
        self.assertEqual((стек(п), п.ошибки), (["IPA"], []))
        п = разобрать_пакет(с.eth(с.ip(с.udp(ipa(0xFE, b"\x00"), 1, 2), 17)), как={"udp:2": "IPA"})
        self.assertEqual((стек(п), п.ошибки), (["IPA"], []))
        п = разобрать_пакет(с.eth(с.ip(с.tcp(xot(ВЫЗОВ_X25), 1, 2), 6)), как={"tcp:2": "XOT"})
        self.assertEqual((стек(п)[:2], п.ошибки), (["XOT", "X.25"], []))
        п = разобрать_пакет(с.eth(с.ip(с.tcp(b"hello world", 1, 2), 6)), как={"tcp:2": "IPA"})
        self.assertEqual((стек(п), п.ошибки), (["Данные"], ["разбирать как IPA (порт 2): данные не подошли"]))
        п = разобрать_пакет(с.eth(с.ip(с.tcp(b"hello world", 1, 2), 6)), как={"tcp:2": "XOT"})
        self.assertEqual(стек(п), ["Данные"])

    def test_tcp_без_лишнего_байта(self):
        # По TCP заголовок всегда 3 байта, даже когда длина + 4 равна остатку.
        п = по_tcp(ipa(0xFE, b"\x00") + b"\x09")
        self.assertEqual(п.уровни[3].поля[2].сырое, 0)
        self.assertEqual(п.инфо, "IPA PING")

    def test_регистрация(self):
        from reportgen.setevoy import prilozh  # noqa: PLC0415
        for порт in (3002, 3003, 3006, 4222, 4249, 4250):
            self.assertIs(prilozh.ПОРТЫ_TCP[порт], ipa_xot.ipa)
        self.assertIsNot(prilozh.ПОРТЫ_TCP.get(5000), ipa_xot.ipa)
        self.assertIs(prilozh.ПОРТЫ_TCP[1998], ipa_xot.xot)
        self.assertEqual((ДОП_УРОВНИ["IPA"], ДОП_УРОВНИ["XOT"]), ("прикладной", "прикладной"))


def xot(пакет, версия=0, длина=None):
    return struct.pack(">HH", версия, len(пакет) if длина is None else длина) + пакет


class ТестXOT(unittest.TestCase):
    def test_вызов(self):
        п = по_tcp(xot(ВЫЗОВ_X25), 1998)
        self.assertEqual(стек(п)[:2], ["XOT", "X.25"])
        ф = поля(п, "XOT")
        self.assertEqual((ф["xot.version"].сырое, ф["xot.length"].сырое), (0, 14))
        self.assertEqual((ф["xot.version"].смещение, ф["xot.length"].смещение), (54, 56))
        self.assertEqual(п.уровни[3].длина, 4)
        self.assertEqual(п.уровни[3].итог, "пакет X.25, 14 байт")
        self.assertEqual(п.уровни[4].смещение, 58)
        self.assertEqual(поля(п, "X.25")["x25.called_address"].текст, "12345")
        self.assertFalse(п.ошибки)

    def test_несколько_пакетов(self):
        рестарт = b"\x10\x00\xfb\x00\x00"
        п = по_tcp(xot(ВЫЗОВ_X25) + xot(рестарт), 1998)
        self.assertEqual([x for x in стек(п) if x in ("XOT", "X.25")], ["XOT", "X.25", "XOT", "X.25"])
        второй = [у for у in п.уровни if у.протокол == "XOT"][1]
        self.assertEqual(второй.смещение, 54 + 4 + 14)
        п = по_tcp(xot(рестарт) + b"\x00\x01\x00\x03abc", 1998)
        self.assertEqual(стек(п)[-1], "Данные")
        self.assertEqual(п.уровни[-1].смещение, 54 + 9)

    def test_pvc_setup(self):
        pvc = (b"\x10\x05\xf5" + bytes([2, 0x12, 5]) + struct.pack(">H", 0x0102) + bytes([6]) + struct.pack(">H", 9)
               + bytes([2, 3, 7, 8]) + b"Seria0" + b"Serial1")
        pvc = pvc[:5] + bytes([6]) + pvc[6:8] + bytes([7]) + pvc[9:]
        п = по_tcp(xot(pvc), 1998)
        ф = поля(п, "XOT")
        self.assertEqual(ф["x25.lcn"].сырое, 5)
        self.assertEqual(ф["xot.pvc.version"].сырое, 2)
        self.assertEqual(ф["xot.pvc.status"].текст, "соединено")
        self.assertEqual((ф["xot.pvc.init_lcn"].сырое, ф["xot.pvc.resp_lcn"].сырое), (0x0102, 9))
        self.assertEqual(ф["xot.pvc.send_window"].текст, "2/3")
        self.assertEqual(ф["xot.pvc.send_pkt_size"].текст, "2^7/2^8")
        self.assertEqual((ф["xot.pvc.init_itf_name"].текст, ф["xot.pvc.init_itf_name"].смещение), ("Seria0", 73))
        self.assertEqual((ф["xot.pvc.resp_itf_name"].текст, ф["xot.pvc.resp_itf_name"].длина), ("Serial1", 7))
        self.assertEqual(п.инфо, "XOT PVC Setup Seria0 → Serial1: соединено")
        self.assertEqual(стек(п), ["XOT"])
        self.assertEqual(п.уровни[3].длина, 4 + len(pvc))
        раскладка = [(x.ключ, x.смещение, x.длина) for x in п.уровни[3].поля]
        self.assertEqual(раскладка, [
            ("xot.version", 54, 2), ("xot.length", 56, 2), ("x25.lcn", 58, 2), ("x25.type", 60, 1),
            ("xot.pvc.version", 61, 1), ("xot.pvc.status", 62, 1), ("xot.pvc.init_lcn", 64, 2),
            ("xot.pvc.resp_lcn", 67, 2), ("xot.pvc.send_window", 69, 2), ("xot.pvc.send_pkt_size", 71, 2),
            ("xot.pvc.init_itf_name", 73, 6), ("xot.pvc.resp_itf_name", 79, 7)])
        п = по_tcp(xot(pvc[:3] + bytes([2, 0x77]) + pvc[5:]), 1998)
        self.assertEqual(поля(п, "XOT")["xot.pvc.status"].текст, "0x77")
        # Короткий «PVC Setup» — это просто пакет X.25 (или данные).
        п = по_tcp(xot(pvc[:14]), 1998)
        self.assertNotIn("xot.pvc.status", поля(п, "XOT"))

    def test_границы_xot(self):
        # Короче семи байт — не XOT (даже при верных версии и длине).
        self.assertEqual(стек(по_tcp(b"\x00\x00\x00\x03\x10\x01", 1998)), ["Данные"])
        # После пакетов — остаток короче заголовка.
        рестарт = b"\x10\x00\xfb\x00\x00"
        п = по_tcp(xot(рестарт) + b"\x00\x00\x00", 1998)
        self.assertEqual(стек(п)[-1], "Данные")
        self.assertEqual(п.уровни[-1].смещение, 54 + 9)
        п = по_tcp(xot(рестарт) + xot(b"", длина=0), 1998)
        self.assertEqual([у.протокол for у in п.уровни].count("XOT"), 2)
        # X.25 первого пакета — ровно его байты.
        п = по_tcp(xot(ВЫЗОВ_X25) + xot(рестарт), 1998)
        x25 = [у for у in п.уровни if у.протокол == "X.25"][0]
        self.assertLessEqual(x25.смещение + x25.длина, 54 + 4 + 14)
        п = по_tcp(xot(ВЫЗОВ_X25, длина=15), 1998)
        self.assertIn("XOT: пакет длиннее данных (продолжение — в следующем сегменте)", п.ошибки)
        self.assertFalse(по_tcp(xot(ВЫЗОВ_X25, длина=14), 1998).ошибки)

    def test_pvc_setup_границы(self):
        голый = b"\x10\x05\xf5" + bytes([2, 0x12, 0]) + struct.pack(">H", 0x0105) + bytes([0]) + \
            struct.pack(">H", 9) + bytes([2, 3, 7, 8])
        self.assertEqual(len(голый), 15)
        п = по_tcp(xot(голый), 1998)
        ф = поля(п, "XOT")
        self.assertEqual((ф["x25.type"].сырое, ф["xot.pvc.init_lcn"].сырое), (0xF5, 0x0105))
        self.assertEqual(п.инфо, "XOT PVC Setup  → : соединено")
        п = по_tcp(xot(голый[:14]), 1998)
        self.assertNotIn("xot.pvc.status", поля(п, "XOT"))
        pvc = голый[:5] + bytes([2]) + голый[6:8] + bytes([3]) + голый[9:] + b"S0" + b"S1x"
        п = по_tcp(xot(pvc), 1998)
        ф = поля(п, "XOT")
        self.assertEqual((ф["xot.pvc.init_itf_name"].текст, ф["xot.pvc.resp_itf_name"].текст), ("S0", "S1x"))

    def test_оборванный_и_чужой(self):
        п = по_tcp(xot(ВЫЗОВ_X25, длина=40), 1998)
        self.assertIn("XOT: пакет длиннее данных (продолжение — в следующем сегменте)", п.ошибки)
        self.assertNotIn("XOT", стек(по_tcp(xot(ВЫЗОВ_X25, версия=1), 1998)))
        self.assertNotIn("XOT", стек(по_tcp(xot(b"\x10\x01", длина=2) + b"\x0b", 1998)))
        self.assertNotIn("XOT", стек(по_tcp(b"\x00\x00\x00\x03\x10\x01", 1998)))
        self.assertEqual(стек(по_tcp(b"\x00\x00\x00\x03\x10\x01\x0b", 1998))[0], "XOT")
        п = по_tcp(xot(b"\xff\xff\xff"), 1998)
        self.assertEqual(стек(п), ["XOT", "Данные"])
        self.assertEqual(п.уровни[4].смещение, 58)


if __name__ == "__main__":
    unittest.main()
