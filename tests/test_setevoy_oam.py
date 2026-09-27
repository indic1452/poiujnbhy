"""CFM/Y.1731 (IEEE 802.1Q-2018, разд. 21; ITU-T G.8013), ERSPAN (draft-foschiano-erspan) и Juniper Packet
Mirror — PDU собираются struct.pack'ом по документам и разбору Wireshark."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy.protokoly import oam
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет


def поля(п, протокол):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


def mac(текст):
    return bytes(int(x, 16) for x in текст.split(":"))


def tlv(вид, значение):
    return struct.pack(">BH", вид, len(значение)) + значение


def pdu(код, тело, флаги=0, уровень=5, смещение=None, tlvs=b"\x00"):
    return bytes([уровень << 5, код, флаги, len(тело) if смещение is None else смещение]) + тело + tlvs


def maid(md_формат, md_имя, ma_формат, ma_имя):
    б = bytes([md_формат]) + (b"" if md_формат == 1 else bytes([len(md_имя)]) + md_имя)
    б += bytes([ma_формат, len(ma_имя)]) + ma_имя
    return б + bytes(48 - len(б)) if len(б) <= 48 else б[:48]


def ccm(мep=12, флаги=0x84, имя=None, счётчики=(1, 2, 3), tlvs=b"\x00"):
    имя = имя or maid(4, b"OPERATOR", 2, b"VPN-77")
    тело = struct.pack(">IH", 1000, мep) + имя + struct.pack(">IIII", *счётчики, 0)
    return pdu(1, тело, флаги, tlvs=tlvs)


def кадр(pdu_):
    return разобрать_пакет(с.eth(pdu_, 0x8902))


def время(секунды):
    return struct.pack(">II", int(секунды), round((секунды - int(секунды)) * 1e9))


class ТестCCM(unittest.TestCase):
    def test_полный_ccm(self):
        п = кадр(ccm(мep=0xE00C, tlvs=tlv(2, b"\x02") + tlv(4, b"\x01") + b"\x00"))
        ф = поля(п, "CFM")
        self.assertEqual(ф["cfm.md.level"].сырое, 5)
        self.assertEqual(ф["cfm.version"].сырое, 0)
        self.assertEqual(ф["cfm.opcode"].сырое, 1)
        self.assertEqual(ф["cfm.first.tlv.offset"].сырое, 70)
        self.assertEqual(ф["cfm.flags.RDI"].сырое, 1)
        self.assertEqual(ф["cfm.flags.Interval"].текст, "1 с")
        self.assertEqual(ф["cfm.ccm.seq.num"].сырое, 1000)
        self.assertEqual(ф["cfm.ccm.mep.id"].сырое, 12)          # 3 старших бита — резерв
        self.assertEqual(ф["cfm.maid.md.name"].текст, "OPERATOR")
        self.assertEqual(ф["cfm.maid.ma.name"].текст, "VPN-77")
        self.assertEqual(ф["cfm.ccm.maid"].сырое, "OPERATOR/VPN-77")
        self.assertEqual(ф["cfm.ccm.itu.t.y1731.txfcf"].сырое, 1)
        self.assertEqual(ф["cfm.ccm.itu.t.y1731.rxfcb"].сырое, 2)
        self.assertEqual(ф["cfm.ccm.itu.t.y1731.txfcb"].сырое, 3)
        self.assertEqual(ф["cfm.ccm.itu.t.y1731.txfcb"].смещение, 14 + 4 + 62)
        self.assertEqual(ф["cfm.tlv.port.status.value"].текст, "psUp")
        self.assertEqual(ф["cfm.tlv.interface.status.value"].текст, "isUp")
        self.assertEqual(п.инфо, "CFM CCM, уровень MD 5, MEP 12, MA OPERATOR/VPN-77, 1 с, RDI; "
                                 "TLV: Port Status, Interface Status")
        self.assertFalse(п.ошибки)
        у = п.уровни[-1]
        self.assertEqual(у.протокол, "CFM")
        self.assertEqual(у.длина, len(п.данные) - 14)

    def test_без_rdi_и_интервалы(self):
        for код, текст in ((1, "3,33 мс"), (3, "100 мс"), (7, "10 мин"), (0, "неверный")):
            п = кадр(ccm(флаги=код))
            self.assertEqual(поля(п, "CFM")["cfm.flags.Interval"].текст, текст)
            self.assertEqual(поля(п, "CFM")["cfm.flags.RDI"].сырое, 0)
            self.assertNotIn("RDI", п.инфо)

    def test_имена_md_и_ma_по_форматам(self):
        случаи = (
            (maid(1, b"", 1, b"\x00\x64"), "100", None),
            (maid(1, b"", 3, b"\x01\x02"), "258", None),
            (maid(1, b"", 4, b"\x00\x00\x5e\x00\x00\x00\x07"), "OUI 00:00:5e, индекс 7", None),
            (maid(1, b"", 32, b"ABCDEF1234567"), "ABCDEF1234567", None),
            (maid(2, b"example.net", 33, b"XYZ"), "XYZ", "example.net"),
            (maid(3, mac("00:11:22:33:44:55") + b"\x00\x09", 2, b"MA"), "MA", "00:11:22:33:44:55 / 9"),
            (maid(4, b"D\x00", 5, b"\xab\xcd"), "abcd", "D"),
            (maid(9, b"\x01\x02", 1, b"\x00\x01\x02"), "000102", "0102"),
            (maid(2, b"x", 3, b"\x01\x02\x03"), "010203", "x"),
        )
        for имя, ma, md in случаи:
            ф = поля(кадр(ccm(имя=имя)), "CFM")
            self.assertEqual(ф["cfm.maid.ma.name"].текст, ma)
            if md is None:
                self.assertNotIn("cfm.maid.md.name", ф)
                self.assertEqual(ф["cfm.ccm.maid"].сырое, ma)
            else:
                self.assertEqual(ф["cfm.maid.md.name"].текст, md)
                self.assertEqual(ф["cfm.ccm.maid"].сырое, f"{md}/{ma}")

    def test_места_имён(self):
        ф = поля(кадр(ccm()), "CFM")
        начало = 14 + 4 + 6
        self.assertEqual((ф["cfm.maid.md.name.format"].смещение, ф["cfm.maid.md.name.format"].сырое), (начало, 4))
        self.assertEqual((ф["cfm.maid.md.name"].смещение, ф["cfm.maid.md.name"].длина), (начало + 1, 9))
        self.assertEqual((ф["cfm.maid.ma.name.format"].смещение, ф["cfm.maid.ma.name.format"].сырое),
                         (начало + 10, 2))
        self.assertEqual((ф["cfm.maid.ma.name"].смещение, ф["cfm.maid.ma.name"].длина), (начало + 11, 7))

    def test_имена_длиннее_maid(self):
        б = bytes([4, 30]) + b"A" * 30 + bytes([2, 20]) + b"B" * 20
        ф = поля(кадр(ccm(имя=б[:48])), "CFM")
        self.assertTrue(ф["cfm.maid.bad"].плохо)
        б = bytes([4, 30]) + b"A" * 30 + bytes([2, 14]) + b"B" * 14
        self.assertEqual(len(б), 48)
        self.assertNotIn("cfm.maid.bad", поля(кадр(ccm(имя=б)), "CFM"))

    def test_оборванный_ccm(self):
        п = кадр(ccm()[:60])
        self.assertTrue(any("оборван" in о for о in п.ошибки))


class ТестПрочиеPDU(unittest.TestCase):
    def test_lbm_и_tlv_данных(self):
        п = кадр(pdu(3, struct.pack(">I", 77), tlvs=tlv(3, b"\xaa" * 10) + b"\x00"))
        ф = поля(п, "CFM")
        self.assertEqual(ф["cfm.lb.transaction.id"].сырое, 77)
        self.assertEqual(ф["cfm.tlv.data.value"].текст, "10 байт")
        self.assertEqual(ф["cfm.tlv.data.value"].смещение, 14 + 8 + 3)
        self.assertEqual(п.инфо, "CFM LBM, уровень MD 5, транзакция 77; TLV: Data")
        self.assertIn("LBR", кадр(pdu(2, struct.pack(">I", 1))).инфо)

    def test_ltm(self):
        тело = struct.pack(">IB", 5, 64) + mac("00:00:00:00:00:01") + mac("00:00:00:00:00:02")
        п = кадр(pdu(0, тело, флаги=0x80, tlvs=tlv(7, b"\x00\x03" + mac("00:00:00:00:00:09")) + b"\x00"))
        ф = поля(п, "CFM")
        self.assertEqual(ф["cfm.flags.UseFDBonly"].сырое, 1)
        self.assertEqual(ф["cfm.lt.ttl"].сырое, 64)
        self.assertEqual(ф["cfm.ltm.orig.addr"].текст, "00:00:00:00:00:01")
        self.assertEqual(ф["cfm.ltm.targ.addr"].текст, "00:00:00:00:00:02")
        self.assertEqual(ф["cfm.tlv.egress.id"].текст, "3 / 00:00:00:00:00:09")
        self.assertEqual(п.инфо, "CFM LTM, уровень MD 5, к 00:00:00:00:00:02, TTL 64; TLV: LTM Egress Identifier")

    def test_ltr(self):
        тело = struct.pack(">IBB", 5, 63, 2)
        tlvs = (tlv(8, b"\x00\x01" + mac("00:00:00:00:00:0a") + b"\x00\x02" + mac("00:00:00:00:00:0b"))
                + tlv(5, b"\x03" + mac("00:00:00:00:00:0c")) + tlv(6, b"\x01" + mac("00:00:00:00:00:0d")) + b"\x00")
        п = кадр(pdu(4, тело, флаги=0x60, tlvs=tlvs))
        ф = поля(п, "CFM")
        self.assertEqual((ф["cfm.flags.FwdYes"].сырое, ф["cfm.flags.TerminalMEP"].сырое), (1, 1))
        self.assertEqual(ф["cfm.ltr.relay.action"].текст, "RlyFDB")
        self.assertEqual(ф["cfm.tlv.egress.last"].текст, "1 / 00:00:00:00:00:0a")
        self.assertEqual(ф["cfm.tlv.egress.next"].текст, "2 / 00:00:00:00:00:0b")
        self.assertEqual(ф["cfm.tlv.reply.action"].текст, "IngBlocked")
        self.assertEqual(ф["cfm.tlv.reply.mac"].текст, "00:00:00:00:00:0c")
        выход = [x for x in п.уровни[-1].поля if x.ключ == "cfm.tlv.type" and x.сырое == 6][0]
        self.assertEqual(выход.дети[1].текст, "EgrOK")
        self.assertEqual(п.инфо, "CFM LTR, уровень MD 5, TTL 63, RlyFDB; TLV: LTR Egress Identifier, "
                                 "Reply Ingress, Reply Egress")
        ф = поля(кадр(pdu(4, тело, флаги=0x40)), "CFM")
        self.assertEqual((ф["cfm.flags.FwdYes"].сырое, ф["cfm.flags.TerminalMEP"].сырое), (1, 0))

    def test_ais_lck_csf(self):
        п = кадр(pdu(33, b"", флаги=4))
        self.assertEqual(поля(п, "CFM")["cfm.flags.Period"].текст, "1 кадр в секунду")
        self.assertEqual(п.инфо, "CFM AIS, уровень MD 5, 1 кадр в секунду")
        п = кадр(pdu(35, b"", флаги=6))
        self.assertEqual(п.инфо, "CFM LCK, уровень MD 5, 1 кадр в минуту")
        п = кадр(pdu(33, b"", флаги=5))
        self.assertEqual(поля(п, "CFM")["cfm.flags.Period"].текст, "5 (неверный)")
        self.assertEqual(п.инфо, "CFM AIS, уровень MD 5")
        п = кадр(pdu(52, b"", флаги=(2 << 3) | 4))
        self.assertEqual(поля(п, "CFM")["cfm.csf.flags.Type"].текст, "RDI")
        self.assertEqual(п.инфо, "CFM CSF, уровень MD 5, RDI")

    def test_tst(self):
        п = кадр(pdu(37, struct.pack(">I", 9), tlvs=tlv(32, b"\x03" + bytes(8)) + b"\x00"))
        ф = поля(п, "CFM")
        self.assertEqual(ф["cfm.tst.sequence.num"].сырое, 9)
        self.assertEqual(ф["cfm.tlv.tst.test.pattern.type"].текст, "ПСП 2^31−1 с CRC-32")

    def test_aps(self):
        п = кадр(pdu(39, bytes([0xB5, 1, 1, 0x80])))
        ф = поля(п, "CFM")
        self.assertEqual(ф["cfm.aps.req_st"].текст, "отказ сигнала рабочего")
        self.assertEqual((ф["cfm.aps.protec.type"].текст, ф["cfm.aps.protec.type"].сырое), ("0101", 5))
        self.assertEqual((ф["cfm.aps.req_signal"].сырое, ф["cfm.aps.bridged_signal"].сырое), (1, 1))
        self.assertEqual(ф["cfm.aps.bridge_type"].текст, "широковещательный")
        self.assertEqual(ф["cfm.aps.bridge_type"].сырое, 1)
        self.assertEqual(п.инфо, "CFM APS, уровень MD 5, отказ сигнала рабочего")
        ф = поля(кадр(pdu(39, bytes([0x00, 0, 2, 0x7F]))), "CFM")
        self.assertEqual(ф["cfm.aps.bridge_type"].текст, "селектор")
        self.assertEqual(ф["cfm.aps.bridged_signal"].сырое, 2)

    def test_r_aps(self):
        тело = bytes([0xB0, 0xA0]) + mac("00:1b:21:00:00:07") + bytes(24)
        п = кадр(pdu(40, тело, смещение=32))
        ф = поля(п, "CFM")
        self.assertEqual(ф["cfm.raps.req_st"].текст, "отказ сигнала")
        self.assertEqual(ф["cfm.raps.status.rb"].сырое, 1)
        self.assertEqual(ф["cfm.raps.status.dnf"].сырое, 0)
        self.assertEqual(ф["cfm.raps.status.bpr"].сырое, 1)
        self.assertEqual(ф["cfm.raps.node_id"].текст, "00:1b:21:00:00:07")
        self.assertEqual(п.инфо, "CFM R-APS, уровень MD 5, отказ сигнала, узел 00:1b:21:00:00:07, RB")
        тело = bytes([0xE0, 0x40]) + mac("00:1b:21:00:00:07") + bytes(24)
        п = кадр(pdu(40, тело, смещение=32))
        ф = поля(п, "CFM")
        self.assertEqual((ф["cfm.raps.req_st"].текст, ф["cfm.raps.event.subcode"].сырое), ("событие", 0))
        self.assertEqual((ф["cfm.raps.status.rb"].сырое, ф["cfm.raps.status.dnf"].сырое), (0, 1))
        self.assertNotIn("RB", п.инфо)
        ф = поля(кадр(pdu(40, bytes([0xE3, 0]) + bytes(30), смещение=32)), "CFM")
        self.assertEqual(ф["cfm.raps.event.subcode"].сырое, 3)

    def test_dmr_задержка(self):
        тело = время(10.0) + время(10.0001) + время(10.00015) + время(10.0003)
        п = кадр(pdu(46, тело, смещение=32))
        ф = поля(п, "CFM")
        self.assertAlmostEqual(ф["cfm.dmm.dmr.txtimestampf"].сырое, 10.0)
        self.assertAlmostEqual(ф["cfm.dmm.dmr.rxtimestampb"].сырое, 10.0003)
        self.assertEqual(ф["cfm.dmm.dmr.rxtimestampf"].текст, "10.000100000 с")
        self.assertEqual(ф["cfm.dmr.delay"].текст, "250.000 мкс")
        self.assertEqual(п.инфо, "CFM DMR, уровень MD 5, задержка 250.000 мкс")
        # DMM и DMR с неполными отметками — задержки нет.
        self.assertNotIn("cfm.dmr.delay", поля(кадр(pdu(47, тело, смещение=32)), "CFM"))
        for i in range(4):
            тело_ = bytearray(тело)
            тело_[8 * i:8 * i + 8] = bytes(8)
            self.assertNotIn("cfm.dmr.delay", поля(кадр(pdu(46, bytes(тело_), смещение=32)), "CFM"))

    def test_1dm_lmm_slm(self):
        ф = поля(кадр(pdu(45, время(3.5) + bytes(8))), "CFM")
        self.assertAlmostEqual(ф["cfm.1dm.txtimestampf"].сырое, 3.5)
        п = кадр(pdu(43, struct.pack(">III", 7, 8, 9)))
        ф = поля(п, "CFM")
        self.assertEqual((ф["cfm.lmm.lmr.txfcf"].сырое, ф["cfm.lmm.lmr.rxfcf"].сырое, ф["cfm.lmm.lmr.txfcb"].сырое),
                         (7, 8, 9))
        self.assertEqual(п.инфо, "CFM LMM, уровень MD 5, TxFCf 7")
        self.assertIn("LMR", кадр(pdu(42, struct.pack(">III", 7, 8, 9))).инфо)
        п = кадр(pdu(55, struct.pack(">HHIII", 3, 0, 44, 100, 0)))
        ф = поля(п, "CFM")
        self.assertEqual((ф["cfm.slm.src_mep"].сырое, ф["cfm.slm.testid"].сырое, ф["cfm.slm.txfcf"].сырое),
                         (3, 44, 100))
        self.assertNotIn("cfm.slm.resp_mep", ф)
        self.assertNotIn("cfm.slm.txfcb", ф)
        self.assertEqual(п.инфо, "CFM SLM, уровень MD 5, MEP 3, тест 44")
        ф = поля(кадр(pdu(54, struct.pack(">HHIII", 3, 4, 44, 100, 90))), "CFM")
        self.assertEqual((ф["cfm.slm.resp_mep"].сырое, ф["cfm.slm.txfcb"].сырое), (4, 90))
        ф = поля(кадр(pdu(53, struct.pack(">HHIII", 3, 0, 44, 100, 0))), "CFM")
        self.assertEqual(ф["cfm.slm.txfcf"].сырое, 100)

    def test_прочие_tlv(self):
        tlvs = (tlv(1, bytes([6, 4]) + mac("00:aa:bb:cc:dd:ee")) + tlv(1, bytes([3, 7]) + b"sw1")
                + tlv(1, b"\x00") + tlv(31, b"\x00\x19\xa7\x01xx") + tlv(36, b"\x00\x00\x00\x05") + b"\x00")
        п = кадр(pdu(3, struct.pack(">I", 1), tlvs=tlvs))
        ф = поля(п, "CFM")
        шасси = [x for x in п.уровни[-1].поля if x.ключ == "cfm.tlv.type" and x.сырое == 1]
        self.assertEqual(шасси[0].дети[1].текст, "MAC: 00:aa:bb:cc:dd:ee")
        self.assertEqual(шасси[1].дети[1].текст, "местный: sw1")
        self.assertEqual(len(шасси[2].дети), 1)
        self.assertEqual(ф["cfm.tlv.org.oui"].текст, "00:19:a7")
        self.assertEqual(ф["cfm.tlv.org.subtype"].сырое, 1)
        self.assertIn("TLV: Sender ID, Sender ID, Sender ID, Organization-Specific, Test ID", п.инфо)

    def test_tlv_длиннее_pdu(self):
        п = кадр(pdu(3, struct.pack(">I", 1), tlvs=struct.pack(">BH", 3, 50) + b"\x00" * 5))
        self.assertIn("CFM: TLV Data длиннее PDU", п.ошибки)
        # TLV без поля длины — просто конец.
        п = кадр(pdu(3, struct.pack(">I", 1), tlvs=b"\x03\x00"))
        self.assertFalse(п.ошибки)
        self.assertNotIn("TLV", п.инфо)

    def test_смещение_первого_tlv(self):
        # Смещение больше тела: неизвестные байты пропускаются, TLV — после них.
        п = кадр(pdu(3, struct.pack(">I", 1) + b"\xee\xee", смещение=6, tlvs=tlv(2, b"\x01") + b"\x00"))
        self.assertEqual(поля(п, "CFM")["cfm.tlv.port.status.value"].текст, "psBlocked")

    def test_неизвестный_код(self):
        п = кадр(pdu(60, b"\x01\x02", смещение=2))
        self.assertEqual(п.инфо, "CFM код 60, уровень MD 5")
        self.assertEqual(поля(п, "CFM")["cfm.opcode"].текст, "60 (код 60)")

    def test_по_g_ach_mpls_tp(self):
        метка = struct.pack(">I", (13 << 12) | 0x100 | 255)
        п = разобрать_пакет(с.eth(метка + b"\x10\x00\x89\x02" + pdu(33, b"", флаги=4, уровень=7), 0x8847))
        self.assertEqual([у.протокол for у in п.уровни][-1], "CFM")
        self.assertEqual(п.инфо, "CFM AIS, уровень MD 7, 1 кадр в секунду")


def раскладка(п, протокол):
    """Все поля уровня (и вложенные) — ключ, место, длина; места сверены вручную с документами."""
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = []

    def обойти(список):
        for x in список:
            итог.append((x.ключ, x.смещение, x.длина))
            обойти(x.дети)
    обойти(у.поля)
    return итог


#: Места полей CFM (кадр Ethernet — 14 байт, PDU с 14): заголовок 14–17, тело с 18, TLV — с 18 + смещение.
РАСКЛАДКИ_CFM = {
    "ccm": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.flags.RDI", 16, 1), ("cfm.flags.Interval", 16, 1),
        ("cfm.first.tlv.offset", 17, 1), ("cfm.ccm.seq.num", 18, 4), ("cfm.ccm.mep.id", 22, 2),
        ("cfm.ccm.maid", 24, 48), ("cfm.maid.md.name.format", 24, 1), ("cfm.maid.md.name", 25, 9),
        ("cfm.maid.ma.name.format", 34, 1), ("cfm.maid.ma.name", 35, 7),
        ("cfm.ccm.itu.t.y1731.txfcf", 72, 4), ("cfm.ccm.itu.t.y1731.rxfcb", 76, 4),
        ("cfm.ccm.itu.t.y1731.txfcb", 80, 4), ("cfm.tlv.type", 88, 4), ("cfm.tlv.len", 89, 2),
        ("cfm.tlv.port.status.value", 91, 1), ("cfm.tlv.type", 92, 4), ("cfm.tlv.len", 93, 2),
        ("cfm.tlv.interface.status.value", 95, 1), ("cfm.tlv.type", 96, 1)
    ],
    "ltm": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.flags.UseFDBonly", 16, 1), ("cfm.first.tlv.offset", 17, 1),
        ("cfm.lt.transaction.id", 18, 4), ("cfm.lt.ttl", 22, 1), ("cfm.ltm.orig.addr", 23, 6),
        ("cfm.ltm.targ.addr", 29, 6), ("cfm.tlv.type", 35, 11), ("cfm.tlv.len", 36, 2),
        ("cfm.tlv.egress.id", 38, 8), ("cfm.tlv.type", 46, 1)
    ],
    "ltr": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.flags.FwdYes", 16, 1), ("cfm.flags.TerminalMEP", 16, 1),
        ("cfm.first.tlv.offset", 17, 1), ("cfm.lt.transaction.id", 18, 4), ("cfm.lt.ttl", 22, 1),
        ("cfm.ltr.relay.action", 23, 1), ("cfm.tlv.type", 24, 19), ("cfm.tlv.len", 25, 2),
        ("cfm.tlv.egress.last", 27, 8), ("cfm.tlv.egress.next", 35, 8), ("cfm.tlv.type", 43, 10),
        ("cfm.tlv.len", 44, 2), ("cfm.tlv.reply.action", 46, 1), ("cfm.tlv.reply.mac", 47, 6),
        ("cfm.tlv.type", 53, 10), ("cfm.tlv.len", 54, 2), ("cfm.tlv.reply.action", 56, 1),
        ("cfm.tlv.reply.mac", 57, 6), ("cfm.tlv.type", 63, 1)
    ],
    "lbm": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.lb.transaction.id", 18, 4),
        ("cfm.tlv.type", 22, 8), ("cfm.tlv.len", 23, 2), ("cfm.tlv.chassis.id", 25, 5),
        ("cfm.tlv.type", 30, 9), ("cfm.tlv.len", 31, 2), ("cfm.tlv.org.oui", 33, 3),
        ("cfm.tlv.org.subtype", 36, 1), ("cfm.tlv.type", 39, 12), ("cfm.tlv.len", 40, 2),
        ("cfm.tlv.tst.test.pattern.type", 42, 1), ("cfm.tlv.type", 51, 5), ("cfm.tlv.len", 52, 2),
        ("cfm.tlv.data.value", 54, 2), ("cfm.tlv.type", 56, 1)
    ],
    "aps": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.aps.req_st", 18, 1),
        ("cfm.aps.protec.type", 18, 1), ("cfm.aps.req_signal", 19, 1),
        ("cfm.aps.bridged_signal", 20, 1), ("cfm.aps.bridge_type", 21, 1), ("cfm.tlv.type", 22, 1)
    ],
    "raps": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.raps.req_st", 18, 1),
        ("cfm.raps.event.subcode", 18, 1), ("cfm.raps.status", 19, 1),
        ("cfm.raps.status.rb", 19, 1), ("cfm.raps.status.dnf", 19, 1),
        ("cfm.raps.status.bpr", 19, 1), ("cfm.raps.node_id", 20, 6), ("cfm.tlv.type", 50, 1)
    ],
    "dmr": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.dmm.dmr.txtimestampf", 18, 8),
        ("cfm.dmm.dmr.rxtimestampf", 26, 8), ("cfm.dmm.dmr.txtimestampb", 34, 8),
        ("cfm.dmm.dmr.rxtimestampb", 42, 8), ("cfm.dmr.delay", 18, 32), ("cfm.tlv.type", 50, 1)
    ],
    "1dm": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.1dm.txtimestampf", 18, 8),
        ("cfm.1dm.rxtimestampf", 26, 8), ("cfm.tlv.type", 34, 1)
    ],
    "lmm": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.lmm.lmr.txfcf", 18, 4),
        ("cfm.lmm.lmr.rxfcf", 22, 4), ("cfm.lmm.lmr.txfcb", 26, 4), ("cfm.tlv.type", 30, 1)
    ],
    "slr": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.slm.src_mep", 18, 2),
        ("cfm.slm.resp_mep", 20, 2), ("cfm.slm.testid", 22, 4), ("cfm.slm.txfcf", 26, 4),
        ("cfm.slm.txfcb", 30, 4), ("cfm.tlv.type", 34, 1)
    ],
    "ais": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.flags.Period", 16, 1), ("cfm.first.tlv.offset", 17, 1),
        ("cfm.tlv.type", 18, 1)
    ],
    "csf": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.csf.flags.Type", 16, 1), ("cfm.first.tlv.offset", 17, 1),
        ("cfm.tlv.type", 18, 1)
    ],
    "tst": [
        ("cfm.md.level", 14, 1), ("cfm.version", 14, 1), ("cfm.opcode", 15, 1),
        ("cfm.flags", 16, 1), ("cfm.first.tlv.offset", 17, 1), ("cfm.tst.sequence.num", 18, 4),
        ("cfm.tlv.type", 22, 1)
    ],
}


class ТестРаскладкиCFM(unittest.TestCase):
    def пакеты(self):
        return {
            "ccm": кадр(ccm(tlvs=tlv(2, b"\x02") + tlv(4, b"\x01") + b"\x00")),
            "ltm": кадр(pdu(0, struct.pack(">IB", 5, 64) + mac("00:00:00:00:00:01") + mac("00:00:00:00:00:02"),
                            tlvs=tlv(7, b"\x00\x03" + mac("00:00:00:00:00:09")) + b"\x00")),
            "ltr": кадр(pdu(4, struct.pack(">IBB", 5, 63, 2), флаги=0x60,
                            tlvs=tlv(8, bytes(16)) + tlv(5, b"\x03" + bytes(6)) + tlv(6, b"\x01" + bytes(6)) + b"\x00")),
            "lbm": кадр(pdu(3, struct.pack(">I", 1), tlvs=tlv(1, bytes([3, 7]) + b"sw1") + tlv(31, b"\x00\x19\xa7\x01xx")
                            + tlv(32, b"\x03" + bytes(8)) + tlv(3, b"ab") + b"\x00")),
            "aps": кадр(pdu(39, bytes([0xB5, 1, 1, 0x80]))),
            "raps": кадр(pdu(40, bytes([0xB0, 0xA0]) + bytes(30), смещение=32)),
            "dmr": кадр(pdu(46, время(10.0) + время(10.0001) + время(10.00015) + время(10.0003), смещение=32)),
            "1dm": кадр(pdu(45, время(3.5) + bytes(8))),
            "lmm": кадр(pdu(43, struct.pack(">III", 7, 8, 9))),
            "slr": кадр(pdu(54, struct.pack(">HHIII", 3, 4, 44, 100, 90))),
            "ais": кадр(pdu(33, b"", флаги=4)),
            "csf": кадр(pdu(52, b"", флаги=(2 << 3) | 4)),
            "tst": кадр(pdu(37, struct.pack(">I", 9))),
        }

    def test_места_всех_полей(self):
        for имя, п in self.пакеты().items():
            self.assertEqual(раскладка(п, "CFM"), РАСКЛАДКИ_CFM[имя], имя)


class ТестГраницCFM(unittest.TestCase):
    """Тело каждого кода — ровно по документу: на байт короче — «оборван», ровно — без ошибок."""

    ТЕЛА = {1: 70, 2: 4, 3: 4, 0: 17, 4: 6, 37: 4, 39: 4, 40: 8, 42: 12, 43: 12, 45: 16, 46: 32, 47: 32,
            53: 16, 54: 16, 55: 16}

    def test_длины_тел(self):
        for код, n in self.ТЕЛА.items():
            тело = bytes(range(1, n + 1))
            целый = кадр(pdu(код, тело, tlvs=b""))
            self.assertFalse(целый.ошибки, код)
            обрыв = кадр(pdu(код, тело[:-1], смещение=n - 1, tlvs=b""))
            self.assertTrue(any("оборван" in о for о in обрыв.ошибки), код)

    def test_заголовок(self):
        self.assertTrue(any("оборван" in о for о in кадр(b"\xa0\x01\x00").ошибки))
        п = кадр(b"\xa0\x21\x04\x00")
        self.assertFalse(п.ошибки)
        self.assertEqual(п.инфо, "CFM AIS, уровень MD 5, 1 кадр в секунду")

    def test_смещение_tlv_за_концом(self):
        self.assertTrue(any("оборван" in о for о in кадр(pdu(33, b"", смещение=3, tlvs=b"ab")).ошибки))
        self.assertFalse(кадр(pdu(33, b"", смещение=2, tlvs=b"ab")).ошибки)

    def test_версия_и_сырые_значения(self):
        ф = поля(кадр(bytes([(3 << 5) | 1, 33, 0xF6, 0])), "CFM")
        self.assertEqual((ф["cfm.md.level"].сырое, ф["cfm.version"].сырое), (3, 1))
        self.assertEqual((ф["cfm.flags.Period"].сырое, ф["cfm.flags.Period"].текст), (6, "1 кадр в минуту"))
        ф = поля(кадр(bytes([0xA0, 35, 0xF5, 0])), "CFM")
        self.assertEqual((ф["cfm.flags.Period"].сырое, ф["cfm.flags.Period"].текст), (5, "5 (неверный)"))
        ф = поля(кадр(ccm(мep=13, флаги=0x7B)), "CFM")
        self.assertEqual(ф["cfm.ccm.mep.id"].сырое, 13)
        self.assertEqual((ф["cfm.flags.Interval"].сырое, ф["cfm.flags.Interval"].текст), (3, "100 мс"))
        ф = поля(кадр(pdu(52, b"", флаги=0xDD)), "CFM")
        self.assertEqual((ф["cfm.csf.flags.Type"].сырое, ф["cfm.csf.flags.Type"].текст), (3, "DCI"))

    def test_ltr_сырые(self):
        п = кадр(pdu(4, struct.pack(">IBB", 0x0A0B0C0D, 63, 9), флаги=0x20))
        ф = поля(п, "CFM")
        self.assertEqual((ф["cfm.flags.FwdYes"].сырое, ф["cfm.flags.TerminalMEP"].сырое), (0, 1))
        self.assertEqual(ф["cfm.lt.transaction.id"].сырое, 0x0A0B0C0D)
        self.assertEqual(ф["cfm.lt.ttl"].сырое, 63)
        self.assertEqual((ф["cfm.ltr.relay.action"].текст, ф["cfm.ltr.relay.action"].сырое), ("9", 9))
        self.assertEqual(п.инфо, "CFM LTR, уровень MD 5, TTL 63, 9")
        ф = поля(кадр(pdu(4, struct.pack(">IBB", 1, 7, 3), флаги=0)), "CFM")
        self.assertEqual((ф["cfm.flags.FwdYes"].сырое, ф["cfm.ltr.relay.action"].текст), (0, "RlyMPDB"))

    def test_aps_сырые(self):
        ф = поля(кадр(pdu(39, bytes([0xDA, 3, 7, 0x00]))), "CFM")
        self.assertEqual((ф["cfm.aps.req_st"].сырое, ф["cfm.aps.req_st"].текст), (13, "принудительное"))
        self.assertEqual((ф["cfm.aps.protec.type"].сырое, ф["cfm.aps.protec.type"].текст), (10, "1010"))
        self.assertEqual((ф["cfm.aps.req_signal"].сырое, ф["cfm.aps.bridged_signal"].сырое), (3, 7))
        self.assertEqual(ф["cfm.aps.bridge_type"].сырое, 0)

    def test_r_aps_резервные_биты(self):
        ф = поля(кадр(pdu(40, bytes([0x00, 0x1F]) + bytes(30), смещение=32)), "CFM")
        self.assertEqual([ф[f"cfm.raps.status.{к}"].сырое for к in ("rb", "dnf", "bpr")], [0, 0, 0])
        п = кадр(pdu(40, bytes([0x00, 0x7F]) + bytes(30), смещение=32))
        self.assertNotIn("RB", п.инфо)
        ф = поля(кадр(pdu(40, bytes([0x00, 0x20]) + bytes(30), смещение=32)), "CFM")
        self.assertEqual([ф[f"cfm.raps.status.{к}"].сырое for к in ("rb", "dnf", "bpr")], [0, 0, 1])

    def test_коды_ответов(self):
        self.assertEqual(поля(кадр(pdu(2, struct.pack(">I", 8))), "CFM")["cfm.lb.transaction.id"].сырое, 8)
        ф = поля(кадр(pdu(42, struct.pack(">III", 1, 2, 3))), "CFM")
        self.assertEqual(ф["cfm.lmm.lmr.txfcb"].сырое, 3)
        тело = время(1.0) + время(2.0) + время(3.0) + время(4.0)
        ф = поля(кадр(pdu(47, тело)), "CFM")
        self.assertAlmostEqual(ф["cfm.dmm.dmr.rxtimestampb"].сырое, 4.0)
        ф = поля(кадр(pdu(53, struct.pack(">HHIII", 5, 0, 6, 7, 0))), "CFM")
        self.assertEqual((ф["cfm.slm.src_mep"].сырое, ф["cfm.slm.testid"].сырое), (5, 6))


class ТестTLVГраниц(unittest.TestCase):
    def tlvs(self, tlvs):
        return кадр(pdu(3, struct.pack(">I", 1), tlvs=tlvs))

    def test_без_end_и_пустой_tlv(self):
        п = self.tlvs(tlv(2, b"\x01"))
        self.assertFalse(п.ошибки)
        self.assertIn("TLV: Port Status", п.инфо)
        п = self.tlvs(tlv(3, b""))
        self.assertFalse(п.ошибки)
        self.assertIn("TLV: Data", п.инфо)
        п = self.tlvs(tlv(3, b"") + b"\x02\x00")
        self.assertFalse(п.ошибки)
        self.assertTrue(п.инфо.endswith("TLV: Data"), п.инфо)

    def test_tlv_впритык_и_на_байт_длиннее(self):
        п = self.tlvs(struct.pack(">BH", 3, 4) + b"abcd")
        self.assertFalse(п.ошибки)
        self.assertEqual(поля(п, "CFM")["cfm.tlv.data.value"].сырое, "61626364")
        п = self.tlvs(struct.pack(">BH", 3, 5) + b"abcd")
        self.assertIn("CFM: TLV Data длиннее PDU", п.ошибки)
        # Данные TLV — ровно свои байты, без соседнего TLV.
        п = self.tlvs(tlv(3, b"ab") + tlv(2, b"\x02") + b"\x00")
        self.assertEqual(поля(п, "CFM")["cfm.tlv.data.value"].сырое, "6162")

    def test_end_tlv(self):
        п = self.tlvs(b"\x00")
        end = [x for x in п.уровни[-1].поля if x.имя == "TLV End"][0]
        self.assertEqual((end.сырое, end.смещение, end.длина), (0, 22, 1))

    def test_короткие_tlv_без_полей(self):
        п = self.tlvs(tlv(5, b"\x01" + bytes(5)) + tlv(7, bytes(7)) + tlv(8, bytes(15)) + tlv(31, b"\x00\x19\xa7")
                      + tlv(32, b"") + tlv(1, b"") + b"\x00")
        ф = поля(п, "CFM")
        for ключ in ("cfm.tlv.reply.action", "cfm.tlv.egress.id", "cfm.tlv.egress.last", "cfm.tlv.org.oui",
                     "cfm.tlv.tst.test.pattern.type", "cfm.tlv.chassis.id"):
            self.assertNotIn(ключ, ф)
        self.assertFalse(п.ошибки)

    def test_впритык_по_длине(self):
        п = self.tlvs(tlv(5, b"\x09" + mac("00:00:00:00:00:0c")) + tlv(7, b"\x00\x01" + bytes(6))
                      + tlv(8, bytes(16)) + tlv(31, b"\x00\x19\xa7\x05") + tlv(32, b"\x09") + b"\x00")
        ф = поля(п, "CFM")
        self.assertEqual((ф["cfm.tlv.reply.action"].текст, ф["cfm.tlv.reply.action"].сырое), ("9", 9))
        self.assertEqual(ф["cfm.tlv.egress.id"].текст, "1 / 00:00:00:00:00:00")
        self.assertIn("cfm.tlv.egress.last", ф)
        self.assertEqual(ф["cfm.tlv.org.subtype"].сырое, 5)
        self.assertEqual((ф["cfm.tlv.tst.test.pattern.type"].текст, ф["cfm.tlv.tst.test.pattern.type"].сырое),
                         ("9", 9))

    def test_данные_16_байт_не_egress(self):
        п = self.tlvs(tlv(3, bytes(16)) + b"\x00")
        ф = поля(п, "CFM")
        self.assertNotIn("cfm.tlv.egress.last", ф)
        self.assertEqual(ф["cfm.tlv.data.value"].текст, "16 байт")

    def test_sender_id_шасси(self):
        def шасси(значение):
            п = self.tlvs(tlv(1, значение) + b"\x00")
            return [x.текст for x in п.уровни[-1].поля if x.ключ == "cfm.tlv.type"][0:0] or \
                [д.текст for x in п.уровни[-1].поля for д in x.дети if д.ключ == "cfm.tlv.chassis.id"]
        self.assertEqual(шасси(bytes([3, 7]) + b"sw1" + b"\x02\x01\x02"), ["местный: sw1"])
        self.assertEqual(шасси(bytes([3, 7]) + b"sw"), [])
        self.assertEqual(шасси(bytes([10, 7]) + b"abc"), [])
        self.assertEqual(шасси(b"\x00\x00"), [])
        self.assertEqual(шасси(b"\x00\x05\x01\x02\x03\x04\x05"), [])

    def test_имя_md_из_7_знаков(self):
        ф = поля(кадр(ccm(имя=maid(2, b"abc.com", 2, b"MA"))), "CFM")
        self.assertEqual(ф["cfm.maid.md.name"].текст, "abc.com")
        ф = поля(кадр(ccm(имя=maid(1, b"", 2, b"ABCDEFG"))), "CFM")
        self.assertEqual(ф["cfm.maid.ma.name"].текст, "ABCDEFG")

    def test_maid_на_байт_длиннее(self):
        б = bytes([4, 30]) + b"A" * 30 + bytes([2, 15]) + b"B" * 15
        ф = поля(кадр(ccm(имя=б[:48])), "CFM")
        self.assertTrue(ф["cfm.maid.bad"].плохо)
        self.assertEqual((ф["cfm.maid.bad"].смещение, ф["cfm.maid.bad"].длина), (24, 48))


def gre(нагрузка, протокол, номер=None):
    флаги = 0x1000 if номер is not None else 0
    return struct.pack(">HH", флаги, протокол) + (struct.pack(">I", номер) if номер is not None else b"") + нагрузка


ВНУТРИ = с.eth(с.ip(с.udp(b"x", 1, 2), 17), dst="00:00:00:00:00:01")


class ТестERSPAN(unittest.TestCase):
    def test_тип_i(self):
        п = разобрать_пакет(с.eth(с.ip(gre(ВНУТРИ, 0x88BE), 47)))
        self.assertEqual([у.протокол for у in п.уровни],
                         ["Ethernet", "IPv4", "GRE", "ERSPAN", "Ethernet", "IPv4", "UDP", "Данные"])
        self.assertEqual(п.уровни[3].итог, "тип I (без заголовка)")

    def test_тип_ii(self):
        заголовок = struct.pack(">HHI", 0x1000 | 100, (5 << 13) | (2 << 11) | 0x0400 | 17, 0xFFF00000 | 0x12345)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + ВНУТРИ, 0x88BE, номер=1), 47)))
        ф = поля(п, "ERSPAN")
        self.assertEqual(ф["erspan.version"].сырое, 1)
        self.assertEqual(ф["erspan.vlan"].сырое, 100)
        self.assertEqual(ф["erspan.cos"].сырое, 5)
        self.assertEqual(ф["erspan.encap"].текст, "была 802.1Q")
        self.assertEqual(ф["erspan.truncated"].сырое, 1)
        self.assertEqual(ф["erspan.spanid"].сырое, 17)
        self.assertEqual(ф["erspan.index"].сырое, 0x12345)
        у = [x for x in п.уровни if x.протокол == "ERSPAN"][0]
        self.assertEqual((у.длина, у.итог), (8, "тип II, сеанс 17, VLAN 100, усечён"))
        self.assertEqual([x.протокол for x in п.уровни][4:], ["Ethernet", "IPv4", "UDP", "Данные"])
        self.assertEqual(п.уровни[4].смещение, 14 + 20 + 8 + 8)

    def test_тип_iii(self):
        флаги = 0x8000 | (0 << 10) | (5 << 4) | 0x0008 | (1 << 1)
        заголовок = struct.pack(">HHIHH", 0x2000 | 7, (1 << 13) | (3 << 11) | 99, 123456, 42, флаги)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + ВНУТРИ, 0x22EB, номер=1), 47)))
        ф = поля(п, "ERSPAN")
        self.assertEqual(ф["erspan.version"].сырое, 2)
        self.assertEqual(ф["erspan.vlan"].сырое, 7)
        self.assertEqual(ф["erspan.bso"].текст, "ошибка CRC или выравнивания")
        self.assertEqual(ф["erspan.truncated"].сырое, 0)
        self.assertEqual(ф["erspan.spanid"].сырое, 99)
        self.assertEqual(ф["erspan.timestamp"].текст, "123456 × 100 нс")
        self.assertEqual(ф["erspan.sgt"].сырое, 42)
        self.assertEqual(ф["erspan.p"].сырое, 1)
        self.assertEqual(ф["erspan.ft"].текст, "Ethernet")
        self.assertEqual(ф["erspan.hw"].сырое, 5)
        self.assertEqual(ф["erspan.direction"].текст, "выход")
        self.assertEqual(ф["erspan.granularity"].сырое, 1)
        у = [x for x in п.уровни if x.протокол == "ERSPAN"][0]
        self.assertEqual((у.длина, у.итог), (12, "тип III, сеанс 99, VLAN 7, выход"))
        self.assertEqual(п.уровни[4].протокол, "Ethernet")

    def test_тип_iii_ip_и_подзаголовок(self):
        флаги = (2 << 10) | (3 << 1) | 1
        заголовок = struct.pack(">HHIHH", 0x2000, 1, 0, 0, флаги) + bytes([3 << 2]) + bytes(7)
        внутри = с.ip(с.udp(b"y", 1, 2), 17)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + внутри, 0x22EB, номер=1), 47)))
        ф = поля(п, "ERSPAN")
        self.assertEqual(ф["erspan.platid"].сырое, 3)
        self.assertEqual(ф["erspan.ft"].текст, "IP")
        self.assertEqual(ф["erspan.direction"].текст, "вход")
        self.assertEqual(ф["erspan.granularity"].текст, "своя")
        self.assertEqual([x.протокол for x in п.уровни][3:], ["ERSPAN", "IPv4", "UDP", "Данные"])
        у = [x for x in п.уровни if x.протокол == "ERSPAN"][0]
        self.assertEqual(у.длина, 20)
        внутри6 = с.ip6(с.udp(b"y", 1, 2, src="2001:db8::1", dst="2001:db8::2", v6=True), 17)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + внутри6, 0x22EB, номер=1), 47)))
        self.assertEqual([x.протокол for x in п.уровни][3:5], ["ERSPAN", "IPv6"])

    def test_тип_iii_иной_вид_кадра(self):
        заголовок = struct.pack(">HHIHH", 0x2000, 1, 0, 0, 3 << 10)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + b"abc", 0x22EB, номер=1), 47)))
        self.assertEqual(поля(п, "ERSPAN")["erspan.ft"].текст, "3")
        self.assertEqual(п.уровни[-1].протокол, "Данные")

    def test_неизвестная_версия(self):
        п = разобрать_пакет(с.eth(с.ip(gre(struct.pack(">HHI", 0x3000, 0, 0) + b"zz", 0x88BE, номер=1), 47)))
        self.assertIn("ERSPAN: неизвестная версия 3", п.ошибки)
        self.assertEqual(п.уровни[-1].протокол, "Данные")
        self.assertEqual(п.уровни[-1].смещение, 14 + 20 + 8 + 8)

    def test_места_полей(self):
        з2 = struct.pack(">HHI", 0x1000 | 100, (5 << 13) | (2 << 11) | 0x0400 | 17, 0xFFF00000 | 0x12345)
        п = разобрать_пакет(с.eth(с.ip(gre(з2 + ВНУТРИ, 0x88BE, номер=1), 47)))
        self.assertEqual(раскладка(п, "ERSPAN"), [
            ("erspan.version", 42, 2), ("erspan.vlan", 42, 2), ("erspan.cos", 44, 2),
            ("erspan.encap", 44, 2), ("erspan.truncated", 44, 2), ("erspan.spanid", 44, 2),
            ("erspan.index", 46, 4)
        ])
        флаги = (2 << 10) | (3 << 1) | 1
        з3 = struct.pack(">HHIHH", 0x2000, 1, 0, 0, флаги) + bytes([3 << 2]) + bytes(7)
        п = разобрать_пакет(с.eth(с.ip(gre(з3 + с.ip(с.udp(b"y", 1, 2), 17), 0x22EB, номер=1), 47)))
        self.assertEqual(раскладка(п, "ERSPAN"), [
            ("erspan.version", 42, 2), ("erspan.vlan", 42, 2), ("erspan.cos", 44, 2),
            ("erspan.bso", 44, 2), ("erspan.truncated", 44, 2), ("erspan.spanid", 44, 2),
            ("erspan.timestamp", 46, 4), ("erspan.sgt", 50, 2), ("erspan.p", 52, 2),
            ("erspan.ft", 52, 2), ("erspan.hw", 52, 2), ("erspan.direction", 52, 2),
            ("erspan.granularity", 52, 2), ("erspan.platid", 54, 8)
        ])
        п = разобрать_пакет(с.eth(с.ip(с.udp(struct.pack(">II", 0xDEAD, 7) + ВНУТРИ[14:], 5000, 30030), 17)))
        self.assertEqual(раскладка(п, "Jmirror"), [
            ("jmirror.mid", 42, 4), ("jmirror.sid", 46, 4)
        ])

    def test_сырые_и_точные_значения(self):
        заголовок = struct.pack(">HHI", 0x1000 | 5, (3 << 11) | 0x03FF, 0x000ABCDE)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + ВНУТРИ, 0x88BE, номер=1), 47)))
        ф = поля(п, "ERSPAN")
        self.assertEqual((ф["erspan.version"].текст, ф["erspan.encap"].сырое), ("1 (тип II)", 3))
        self.assertEqual(ф["erspan.encap"].текст, "метка VLAN сохранена в кадре")
        self.assertEqual((ф["erspan.spanid"].сырое, ф["erspan.index"].сырое), (1023, 0xABCDE))
        флаги = 0x0008 | 0x0002
        заголовок = struct.pack(">HHIHH", 0x2000, (1 << 11) | 0x0400, 0x01020304, 0, флаги)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + ВНУТРИ, 0x22EB, номер=1), 47)))
        ф = поля(п, "ERSPAN")
        self.assertEqual(ф["erspan.version"].текст, "2 (тип III)")
        self.assertEqual((ф["erspan.bso"].сырое, ф["erspan.bso"].текст), (1, "короткий кадр"))
        self.assertEqual(ф["erspan.timestamp"].сырое, 0x01020304)
        self.assertEqual((ф["erspan.direction"].сырое, ф["erspan.granularity"].сырое), (1, 1))
        self.assertEqual(ф["erspan.p"].сырое, 0)
        заголовок = struct.pack(">HHIHH", 0x2000, 0, 0, 0, 0x0004)
        ф = поля(разобрать_пакет(с.eth(с.ip(gre(заголовок + ВНУТРИ, 0x22EB, номер=1), 47))), "ERSPAN")
        self.assertEqual((ф["erspan.direction"].сырое, ф["erspan.granularity"].текст), (0, "IEEE 1588"))

    def test_обрывы(self):
        заголовок = struct.pack(">HHI", 0x1000, 0, 0)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок[:7], 0x88BE, номер=1), 47)))
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + ВНУТРИ, 0x88BE, номер=1), 47)))
        self.assertFalse(п.ошибки)
        заголовок = struct.pack(">HHIHH", 0x2000, 0, 0, 0, (3 << 10) | 1)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок[:11], 0x22EB, номер=1), 47)))
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + bytes(7), 0x22EB, номер=1), 47)))
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок + bytes(8), 0x22EB, номер=1), 47)))
        self.assertFalse(п.ошибки)
        self.assertEqual([у.протокол for у in п.уровни][-1], "ERSPAN")

    def test_gre_версии_1_без_номера(self):
        # Бит версии GRE (младший) не путается с битом S.
        п = разобрать_пакет(с.eth(с.ip(struct.pack(">HH", 0x0001, 0x88BE) + ВНУТРИ, 47)))
        self.assertEqual(п.уровни[3].итог, "тип I (без заголовка)")

    def test_заголовок_без_кадра(self):
        п = разобрать_пакет(с.eth(с.ip(gre(struct.pack(">HHI", 0x1000, 0, 7), 0x88BE, номер=1), 47)))
        self.assertEqual(поля(п, "ERSPAN")["erspan.index"].сырое, 7)
        self.assertEqual(п.уровни[-1].протокол, "Ethernet")
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        заголовок = struct.pack(">HHIHH", 0x2000, 0, 0, 0, 3 << 10)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок, 0x22EB, номер=1), 47)))
        self.assertFalse(п.ошибки)
        заголовок = struct.pack(">HHIHH", 0x2000, 0, 0, 0, 2 << 10)
        п = разобрать_пакет(с.eth(с.ip(gre(заголовок, 0x22EB, номер=1), 47)))
        self.assertEqual(п.уровни[-1].протокол, "IPv4")
        self.assertTrue(any("оборван" in о for о in п.ошибки), п.ошибки)

    def test_без_gre(self):
        # Напрямую по EtherType (не под GRE) — заголовок считается присутствующим.
        заголовок = struct.pack(">HHI", 0x1000 | 5, 3, 0)
        п = разобрать_пакет(с.eth(заголовок + ВНУТРИ, 0x88BE))
        self.assertEqual(поля(п, "ERSPAN")["erspan.vlan"].сырое, 5)


class ТестJmirror(unittest.TestCase):
    def test_ipv4_ipv6_ppp(self):
        внутри = с.ip(с.udp(b"q", 1, 2), 17)
        п = разобрать_пакет(с.eth(с.ip(с.udp(struct.pack(">II", 0xDEAD, 7) + внутри, 5000, 30030), 17)))
        ф = поля(п, "Jmirror")
        self.assertEqual(ф["jmirror.mid"].текст, "0x0000dead")
        self.assertEqual(ф["jmirror.sid"].сырое, 7)
        self.assertEqual(п.уровни[3].длина, 8)
        self.assertEqual([у.протокол for у in п.уровни][3:6], ["Jmirror", "IPv4", "UDP"])
        внутри6 = с.ip6(с.udp(b"q", 1, 2, src="2001:db8::1", dst="2001:db8::2", v6=True), 17)
        п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(8) + внутри6, 5000, 30030), 17)))
        self.assertEqual([у.протокол for у in п.уровни][3:5], ["Jmirror", "IPv6"])
        for ppp, дальше in ((b"\xff\x03\x00\x21", внутри), (b"\xff\x03\x40\x21", внутри),
                            (b"\xff\x03\x00\x57", внутри6)):
            п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(8) + ppp + дальше, 5000, 30030), 17)))
            self.assertEqual([у.протокол for у in п.уровни][3:5], ["Jmirror", "PPP"], ppp)

    def test_не_jmirror(self):
        for хвост in (b"\x46" + bytes(30), b"\xff\x03\x01\x21", b"\xff\x03\x40\x57", b"\xff\x02\x00\x21",
                      b"\xfe\x03\x00\x21", b"\xff\x03\x00\x22", b"\x45\x00\x00"):
            п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(8) + хвост, 5000, 30030), 17)))
            self.assertNotIn("Jmirror", [у.протокол for у in п.уровни], хвост)

    def test_границы_и_возврат(self):
        # 12 байт — заголовок и начало IPv4: IPv4 оборван, разбор откатывается к данным.
        п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(8) + b"\x45\x00\x00\x14", 5000, 30030), 17)))
        self.assertEqual([у.протокол for у in п.уровни][3:], ["Данные"])
        п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(8) + b"\x45\x00\x00", 5000, 30030), 17)))
        self.assertEqual([у.протокол for у in п.уровни][3:], ["Данные"])
        п = разобрать_пакет(с.eth(с.ip(с.udp(bytes(8) + b"\x99" * 12, 5000, 30030), 17)))
        self.assertEqual([у.протокол for у in п.уровни][3:], ["Данные"])
        # Похожее на RTP начало (версия 2) не отдаётся RTP, раз Jmirror свой пакет узнал.
        внутри = с.ip(с.udp(b"q", 1, 2), 17)
        п = разобрать_пакет(с.eth(с.ip(с.udp(struct.pack(">II", 0x80080001, 7) + внутри, 5000, 30030), 17)))
        self.assertEqual([у.протокол for у in п.уровни][3:], ["Jmirror", "IPv4", "UDP", "Данные"])

    def test_уровни_дерева(self):
        self.assertEqual((ДОП_УРОВНИ["CFM"], ДОП_УРОВНИ["ERSPAN"], ДОП_УРОВНИ["Jmirror"]),
                         ("канальный", "канальный", "прикладной"))
        self.assertIs(oam.jmirror, oam.prilozh.ПОРТЫ_UDP[30030])


if __name__ == "__main__":
    unittest.main()
