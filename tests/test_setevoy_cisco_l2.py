"""ISL, DTP, VTP, UDLD, PVST+ — кадры 802.3 с LLC/SNAP (OUI Cisco 00-00-0C) собираются struct.pack'ом по разбору
Wireshark (packet-isl.c, packet-dtp.c, packet-vtp.c, packet-udld.c, packet-bpdu.c)."""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy.protokoly import cisco_l2  # noqa: F401
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет


def mac(текст):
    return bytes(int(x, 16) for x in текст.split(":"))


def snap(pid, нагрузка, dst="01:00:0c:cc:cc:cc", добивка=True):
    llc = b"\xaa\xaa\x03\x00\x00\x0c" + struct.pack(">H", pid) + нагрузка
    кадр = mac(dst) + mac("00:11:22:33:44:55") + struct.pack(">H", len(llc)) + llc
    return кадр + bytes(max(0, 60 - len(кадр))) if добивка else кадр


def поля(п, протокол):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = {}

    def обойти(список):
        for x in список:
            итог.setdefault(x.ключ, x)
            обойти(x.дети)
    обойти(у.поля)
    return итог


def стек(п):
    return [у.протокол for у in п.уровни]


def tlv2(вид, значение):
    """TLV DTP/UDLD: вид и длина по 2 байта, длина — вместе с заголовком."""
    return struct.pack(">HH", вид, 4 + len(значение)) + значение


class ТестISL(unittest.TestCase):
    def кадр(self, вид=0, пользователь=3, vlan=20, bpdu=1, длина=None, внутри=None, dst0=0x01):
        внутри = внутри if внутри is not None else с.eth(с.ip(с.udp(b"x", 1, 2), 17)) + b"\xde\xad\xbe\xef"
        длина = 12 + len(внутри) if длина is None else длина
        return (bytes([dst0, 0, 0x0C, 0, 0, (вид << 4) | пользователь]) + mac("00:0c:0c:00:00:07")
                + struct.pack(">H", длина) + b"\xaa\xaa\x03\x00\x00\x0c"
                + struct.pack(">HHH", (vlan << 1) | bpdu, 9, 0) + внутри + b"\x01\x02\x03\x04")

    def test_ethernet_внутри(self):
        п = разобрать_пакет(self.кадр())
        self.assertEqual(стек(п), ["ISL", "Ethernet", "IPv4", "UDP", "Данные"])
        ф = поля(п, "ISL")
        self.assertEqual(ф["isl.dst"].текст, "01:00:0c:00:00:03")
        self.assertEqual(ф["isl.type"].текст, "Ethernet")
        self.assertEqual(ф["isl.user_eth"].текст, "наивысший")
        self.assertEqual(ф["isl.src"].текст, "00:0c:0c:00:00:07")
        self.assertEqual(ф["isl.len"].сырое, 12 + 14 + 20 + 9 + 4)
        self.assertEqual(ф["isl.hsa"].текст, "00:00:0c")
        self.assertEqual((ф["isl.vlan_id"].сырое, ф["isl.bpdu"].сырое, ф["isl.index"].сырое), (20, 1, 9))
        self.assertEqual(п.уровни[0].длина, 26)
        self.assertEqual(п.уровни[1].смещение, 26)
        self.assertEqual(п.уровни[0].итог, "VLAN 20, Ethernet, BPDU")
        self.assertIn("ISL", ДОП_УРОВНИ)
        п = разобрать_пакет(self.кадр(вид=1))
        self.assertEqual((п.источник, п.получатель), ("00:0c:0c:00:00:07", "01:00:0c:00:00:13"))

    def test_приоритет_и_без_bpdu(self):
        п = разобрать_пакет(self.кадр(пользователь=0x5, bpdu=0, dst0=0x0C, vlan=4000))
        ф = поля(п, "ISL")
        self.assertEqual(ф["isl.user_eth"].текст, "приоритет 1")
        self.assertEqual(п.уровни[0].итог, "VLAN 4000, Ethernet")

    def test_длина_ноль_и_малая(self):
        self.assertEqual(стек(разобрать_пакет(self.кадр(длина=0)))[:2], ["ISL", "Ethernet"])
        п = разобрать_пакет(self.кадр(длина=11))
        self.assertEqual(стек(п), ["ISL", "Данные"])
        self.assertEqual(стек(разобрать_пакет(self.кадр(длина=12)))[:2], ["ISL", "Ethernet"])

    def test_token_ring(self):
        п = разобрать_пакет(self.кадр(вид=1, пользователь=7))
        ф = поля(п, "ISL")
        self.assertEqual((ф["isl.type"].текст, ф["isl.user"].сырое), ("Token-Ring", 7))
        self.assertNotIn("isl.user_eth", ф)
        self.assertEqual(стек(п), ["ISL", "Данные"])
        self.assertEqual(п.уровни[0].итог, "VLAN 20, Token-Ring, BPDU")
        self.assertEqual(разобрать_пакет(self.кадр(вид=9)).уровни[0].итог, "VLAN 20, вид 9, BPDU")

    def test_не_isl(self):
        кадр = self.кадр()
        for место, байт in ((0, 0x03), (1, 1), (2, 0x0D), (3, 1), (4, 1)):
            к = bytearray(кадр)
            к[место] = байт
            self.assertEqual(стек(разобрать_пакет(bytes(к)))[0], "Ethernet", (место, байт))
        # EtherType, а не длина 802.3.
        к = bytearray(кадр)
        к[12:14] = b"\x05\xdd"
        self.assertEqual(стек(разобрать_пакет(bytes(к)))[0], "Ethernet")
        к[12:14] = b"\x05\xdc"
        self.assertEqual(стек(разобрать_пакет(bytes(к)))[0], "ISL")
        self.assertEqual(стек(разобрать_пакет(кадр[:25]))[0], "Ethernet")
        self.assertEqual(стек(разобрать_пакет(кадр[:26]))[0], "ISL")


class ТестDTP(unittest.TestCase):
    def test_полный(self):
        тело = (b"\x01" + tlv2(1, b"lab\x00") + tlv2(2, b"\x81") + tlv2(3, b"\xa5")
                + tlv2(4, mac("00:aa:bb:cc:dd:ee")))
        п = разобрать_пакет(snap(0x2004, тело, dst="01:00:0c:cc:cc:cc"))
        self.assertEqual(стек(п), ["Ethernet", "LLC", "DTP"])
        ф = поля(п, "DTP")
        self.assertEqual(ф["dtp.version"].сырое, 1)
        self.assertEqual(ф["dtp.domain"].текст, "lab")
        self.assertEqual((ф["dtp.tos"].текст, ф["dtp.tas"].текст), ("trunk", "on"))
        self.assertEqual((ф["dtp.tot"].текст, ф["dtp.tat"].текст), ("802.1Q", "802.1Q"))
        self.assertEqual(ф["dtp.senderid"].текст, "00:aa:bb:cc:dd:ee")
        self.assertEqual(ф["dtp.senderid"].смещение, 14 + 8 + 1 + 8 + 5 + 5 + 4)
        self.assertEqual(п.инфо, "DTP домен «lab», trunk/on, 802.1Q/802.1Q")
        self.assertFalse(п.ошибки)
        self.assertEqual(п.уровни[-1].длина, len(тело))

    def test_значения(self):
        тело = b"\x01" + tlv2(2, b"\x04") + tlv2(3, b"\x42")
        п = разобрать_пакет(snap(0x2004, тело))
        ф = поля(п, "DTP")
        self.assertEqual((ф["dtp.tos"].текст, ф["dtp.tas"].текст), ("access", "auto"))
        self.assertEqual((ф["dtp.tot"].текст, ф["dtp.tat"].текст), ("ISL", "ISL"))
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(2, b"\x07") + tlv2(3, b"\xe0")))
        self.assertEqual(п.инфо, "DTP access/7, 7/negotiated")

    def test_ошибки(self):
        п = разобрать_пакет(snap(0x2004, b"\x01" + struct.pack(">HH", 2, 4) + b"\x81"))
        self.assertIn("DTP: длина TLV не больше 4", п.ошибки)
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(2, b"\x81\x00")))
        self.assertIn("DTP: неверная длина TLV состояние транка", п.ошибки)
        self.assertTrue(поля(п, "DTP")["dtp.tlv_type"].плохо)
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(1, b"a" * 34)))
        self.assertIn("DTP: неверная длина TLV домен", п.ошибки)
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(1, b"a" * 32 + b"\x00")))
        self.assertEqual(поля(п, "DTP")["dtp.domain"].текст, "a" * 32)
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(9, b"zz")))
        self.assertFalse(п.ошибки)
        self.assertEqual(п.инфо, "DTP без TLV")
        п = разобрать_пакет(snap(0x2004, b"\x01\x00\x02\x00", добивка=False))
        self.assertIn("DTP: TLV оборван", п.ошибки)

    def test_добивка_не_разбирается(self):
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(2, b"\x81")))
        self.assertGreater(len(п.данные), 14 + 8 + 6)
        self.assertFalse(п.ошибки)

    def test_ethertype_не_snap(self):
        п = разобрать_пакет(с.eth(b"\x01" + tlv2(2, b"\x81"), 0x2004))
        self.assertEqual(стек(п), ["Ethernet", "Данные"])


def домен(имя):
    return bytes([len(имя)]) + имя + bytes(32 - len(имя))


class ТестVTP(unittest.TestCase):
    def test_summary(self):
        тело = (bytes([2, 1, 1]) + домен(b"CORP") + struct.pack(">I", 17) + с.a4("10.1.1.1") + b"240927101530"
                + bytes(range(16)))
        п = разобрать_пакет(snap(0x2003, тело))
        ф = поля(п, "VTP")
        self.assertEqual((ф["vtp.version"].сырое, ф["vtp.code"].текст), (2, "Summary Advertisement"))
        self.assertEqual((ф["vtp.followers"].сырое, ф["vtp.md_len"].сырое, ф["vtp.md"].текст), (1, 4, "CORP"))
        self.assertEqual(ф["vtp.conf_rev_num"].сырое, 17)
        self.assertEqual(ф["vtp.upd_id"].текст, "10.1.1.1")
        self.assertEqual(ф["vtp.upd_ts"].текст, "2024-09-27 10:15:30")
        self.assertEqual(ф["vtp.md5_digest"].текст, bytes(range(16)).hex())
        self.assertEqual(ф["vtp.md5_digest"].смещение, 14 + 8 + 56)
        self.assertEqual(п.инфо, "VTP Summary Advertisement, домен «CORP», ревизия 17, обновил 10.1.1.1")
        self.assertEqual(п.уровни[-1].длина, 72)
        тело2 = тело[:44] + b"notatimestmp" + тело[56:]
        self.assertEqual(поля(разобрать_пакет(snap(0x2003, тело2)), "VTP")["vtp.upd_ts"].текст, "notatimestmp")

    def test_subset(self):
        def запись(ид, имя, tlvs=b""):
            имя_в = имя + bytes(4 * ((len(имя) + 3) // 4) - len(имя))
            тело = bytes([0, 1, len(имя)]) + struct.pack(">HHI", ид, 1500, 0x100000 + ид) + имя_в + tlvs
            return bytes([1 + len(тело)]) + тело
        записи = запись(1, b"default") + запись(20, b"users", b"\x06\x01\x00\x01")
        тело = bytes([1, 2, 3]) + домен(b"CORP") + struct.pack(">I", 18) + записи
        п = разобрать_пакет(snap(0x2003, тело))
        ф = поля(п, "VTP")
        self.assertEqual(ф["vtp.seq_num"].сырое, 3)
        self.assertEqual(п.инфо, "VTP Subset Advertisement, домен «CORP», ревизия 18, VLAN: 1, 20")
        vlan = [x for x in п.уровни[-1].поля if x.ключ == "vtp.vlan_info"]
        self.assertEqual([x.имя for x in vlan], ["VLAN 1 «default»", "VLAN 20 «users»"])
        д = {x.ключ: x for x in vlan[1].дети}
        self.assertEqual(д["vtp.vlan.info_len"].сырое, 24)
        self.assertEqual(д["vtp.vlan.status.vlan_susp"].сырое, 0)
        self.assertEqual(д["vtp.vlan.type"].текст, "Ethernet")
        self.assertEqual((д["vtp.vlan.isl_vlan_id"].сырое, д["vtp.vlan.mtu_size"].сырое), (20, 1500))
        self.assertEqual(д["vtp.vlan.802_10_index"].сырое, 0x100014)
        self.assertEqual((д["vtp.vlan.vlan_name"].текст, д["vtp.vlan.vlan_name"].длина), ("users", 8))
        self.assertEqual(д["vtp.vlan.tlvtype"].имя, "TLV отсечение")
        self.assertEqual(д["vtp.vlan.tlvtype"].длина, 4)
        self.assertEqual(vlan[1].смещение, 14 + 8 + 40 + 20)
        self.assertFalse(п.ошибки)

    def test_subset_короткая_запись(self):
        тело = bytes([1, 2, 1]) + домен(b"X") + struct.pack(">I", 1) + bytes([4]) + bytes(11)
        п = разобрать_пакет(snap(0x2003, тело, добивка=False))
        self.assertIn("VTP: запись VLAN короче 12 байт", п.ошибки)

    def test_request_и_join(self):
        п = разобрать_пакет(snap(0x2003, bytes([1, 3, 0]) + домен(b"CORP") + struct.pack(">H", 5)))
        self.assertEqual(поля(п, "VTP")["vtp.start_value"].сырое, 5)
        self.assertEqual(п.инфо, "VTP Advertisement Request, домен «CORP»")
        # Длина домена больше 32 — показываются только 32 байта поля, следующий байт уже не домен.
        тело = bytes([1, 3, 0, 40]) + b"A" * 32 + b"BB"
        п = разобрать_пакет(snap(0x2003, тело))
        self.assertEqual(поля(п, "VTP")["vtp.md"].текст, "A" * 32)
        тело = bytes([1, 4, 0]) + домен(b"CORP") + struct.pack(">HH", 0, 15) + b"\xa0\x01"
        п = разобрать_пакет(snap(0x2003, тело, добивка=False))
        ф = поля(п, "VTP")
        self.assertEqual((ф["vtp.pruning.first"].сырое, ф["vtp.pruning.last"].сырое), (0, 15))
        self.assertEqual(ф["vtp.pruning.active"].сырое, [0, 2, 15])
        self.assertEqual(п.инфо, "VTP Join/Prune, домен «CORP», VLAN 0–15, активных 3")
        # Добивка до 60 байт не считается картой VLAN.
        п = разобрать_пакет(snap(0x2003, тело[:-2] + b"\x80"))
        self.assertEqual(поля(п, "VTP")["vtp.pruning.active"].сырое, [0])

    def test_неизвестный_код(self):
        п = разобрать_пакет(snap(0x2003, bytes([1, 9, 0]) + домен(b"Z")))
        self.assertEqual(п.инфо, "VTP код 9, домен «Z»")


class ТестUDLD(unittest.TestCase):
    def test_probe(self):
        тело = (bytes([(1 << 5) | 1, 0x03]) + b"\x12\x34" + tlv2(1, b"SW1\x00") + tlv2(2, b"Gi0/1")
                + tlv2(3, b"\x00\x00\x00\x01") + tlv2(4, b"\x07") + tlv2(5, b"\x05") + tlv2(6, b"sw1.corp")
                + tlv2(7, b"\x00\x00\x00\x2a"))
        п = разобрать_пакет(snap(0x0111, тело, dst="01:00:0c:cc:cc:cc"))
        ф = поля(п, "UDLD")
        self.assertEqual((ф["udld.version"].сырое, ф["udld.opcode"].текст), (1, "Probe"))
        self.assertEqual((ф["udld.flags.rt"].сырое, ф["udld.flags.rsy"].сырое), (1, 1))
        self.assertEqual(ф["udld.checksum"].сырое, 0x1234)
        self.assertEqual(ф["udld.device_id"].текст, "SW1")
        self.assertEqual(ф["udld.sent_through_interface"].текст, "Gi0/1")
        self.assertEqual(ф["udld.data"].текст, "00000001")
        self.assertEqual(ф["udld.msg_interval"].сырое, 7)
        self.assertEqual(ф["udld.timeout_interval"].сырое, 5)
        self.assertEqual(ф["udld.device_name"].текст, "sw1.corp")
        self.assertEqual(ф["udld.seq_num"].сырое, 42)
        self.assertEqual(п.инфо, "UDLD Probe, SW1 порт Gi0/1")
        self.assertEqual(п.уровни[-1].длина, len(тело))
        self.assertFalse(п.ошибки)

    def test_echo_flush_и_ошибки(self):
        п = разобрать_пакет(snap(0x0111, bytes([(1 << 5) | 3, 2, 0, 0])))
        self.assertEqual(п.инфо, "UDLD Flush")
        ф = поля(п, "UDLD")
        self.assertEqual((ф["udld.flags.rt"].сырое, ф["udld.flags.rsy"].сырое), (0, 1))
        п = разобрать_пакет(snap(0x0111, bytes([(1 << 5) | 2, 0, 0, 0]) + tlv2(2, b"Fa1")))
        self.assertEqual(п.инфо, "UDLD Echo порт Fa1")
        п = разобрать_пакет(snap(0x0111, bytes([0x3F, 0, 0, 0]) + struct.pack(">HH", 1, 3)))
        self.assertIn("UDLD: длина TLV 3 < 4", п.ошибки)
        self.assertEqual(п.инфо, "UDLD код 31")
        self.assertEqual(п.уровни[-1].длина, 8)
        п = разобрать_пакет(snap(0x0111, bytes([0x21, 0, 0, 0]) + tlv2(4, b"\x01\x02") + tlv2(7, b"\x01")
                                 + tlv2(3, b"")))
        ф = поля(п, "UDLD")
        self.assertNotIn("udld.msg_interval", ф)
        self.assertNotIn("udld.seq_num", ф)
        self.assertEqual(ф["udld.data"].текст, "0102")
        self.assertFalse(п.ошибки)


class ТестPVST(unittest.TestCase):
    def bpdu(self, rst=True):
        б = struct.pack(">HBBB", 0, 2 if rst else 0, 2 if rst else 0, 0x3C)
        б += struct.pack(">H", 0x8014) + mac("00:00:00:00:00:01") + struct.pack(">I", 4)
        б += struct.pack(">H", 0x8014) + mac("00:00:00:00:00:02") + struct.pack(">HHHHH", 0x8001, 0, 5120, 512, 3840)
        return б + (b"\x00" if rst else b"\x00")

    def test_pvst_plus(self):
        п = разобрать_пакет(snap(0x010B, self.bpdu() + struct.pack(">HHH", 0, 2, 20), dst="01:00:0c:cc:cc:cd"))
        self.assertEqual(стек(п), ["Ethernet", "LLC", "PVST+"])
        ф = поля(п, "PVST+")
        self.assertEqual(ф["stp.pvst.origvlan"].сырое, 20)
        self.assertEqual(ф["stp.pvst.origvlan"].смещение, 14 + 8 + 40)
        self.assertEqual(ф["stp.pvst.tlvlength"].сырое, 2)
        self.assertTrue(п.инфо.startswith("PVST+: VLAN 20, корень 32788 / 00:00:00:00:00:01"), п.инфо)
        self.assertEqual(п.уровни[-1].длина, 42)
        self.assertFalse(п.ошибки)
        self.assertEqual(ДОП_УРОВНИ["PVST+"], "канальный")

    def test_ошибки(self):
        п = разобрать_пакет(snap(0x010B, self.bpdu() + struct.pack(">HHB", 0, 1, 20)))
        self.assertIn("PVST+: Originating VLAN — не 2 байта", п.ошибки)
        self.assertIn("PVST+: нет TLV Originating VLAN", п.ошибки)
        п = разобрать_пакет(snap(0x010B, self.bpdu() + struct.pack(">HHH", 0, 6, 20), добивка=False))
        self.assertIn("PVST+: TLV оборван", п.ошибки)
        п = разобрать_пакет(snap(0x010B, self.bpdu() + struct.pack(">HHH", 5, 2, 20) + struct.pack(">HHH", 0, 2, 7)))
        ф = поля(п, "PVST+")
        self.assertEqual(ф["stp.pvst.tlvtype"].имя, "TLV 5")
        self.assertEqual(ф["stp.pvst.origvlan"].сырое, 7)
        п = разобрать_пакет(snap(0x010B, self.bpdu(), добивка=False))
        self.assertEqual(п.ошибки, ["PVST+: нет TLV Originating VLAN"])
        self.assertFalse(п.инфо.startswith("PVST+: VLAN"))

    def test_не_snap(self):
        for pid in (0x010B, 0x0111, 0x2003):
            п = разобрать_пакет(с.eth(bytes(60), pid if pid > 1500 else 0x9999))
            self.assertEqual(стек(п)[-1], "Данные")
        п = разобрать_пакет(с.eth(bytes(60), 0x2003))
        self.assertEqual(стек(п), ["Ethernet", "Данные"])


class ТестГраницCisco(unittest.TestCase):
    def test_snap_чужой_oui_и_isl_с_dtp(self):
        # SNAP с OUI 00-00-00 и «PID» 0x2004 — это EtherType, не DTP.
        llc = b"\xaa\xaa\x03\x00\x00\x00\x20\x04" + b"\x01" + tlv2(2, b"\x81")
        п = разобрать_пакет(mac("01:00:0c:cc:cc:cc") + mac("00:11:22:33:44:55") + struct.pack(">H", len(llc)) + llc)
        self.assertNotIn("DTP", стек(п))
        # DTP в ISL: уровни ISL, Ethernet, LLC — последний LLC, SNAP Cisco.
        внутри = snap(0x2004, b"\x01" + tlv2(2, b"\x81"), добивка=False)
        п = разобрать_пакет(ТестISL().кадр(внутри=внутри))
        self.assertEqual(стек(п), ["ISL", "Ethernet", "LLC", "DTP"])

    def test_isl_в_середине_кадра(self):
        # ISL под ERSPAN типа I: короче 26 байт — не ISL, а 802.3.
        кадр = ТестISL().кадр()[:25]
        gre = struct.pack(">HH", 0, 0x88BE) + кадр
        п = разобрать_пакет(с.eth(с.ip(gre, 47)))
        self.assertNotIn("ISL", стек(п))
        gre = struct.pack(">HH", 0, 0x88BE) + ТестISL().кадр()
        self.assertIn("ISL", стек(разобрать_пакет(с.eth(с.ip(gre, 47)))))

    def test_isl_сырые_и_места(self):
        п = разобрать_пакет(ТестISL().кадр(пользователь=0x7))
        ф = поля(п, "ISL")
        self.assertEqual((ф["isl.user_eth"].сырое, ф["isl.user_eth"].текст), (3, "наивысший"))
        п = разобрать_пакет(ТестISL().кадр(вид=2, пользователь=0xA))
        ф = поля(п, "ISL")
        self.assertEqual((ф["isl.user"].сырое, ф["isl.user"].смещение, ф["isl.user"].длина), (0xA, 5, 1))
        self.assertEqual(п.уровни[1].смещение, 26)

    def test_dtp_границы(self):
        п = разобрать_пакет(snap(0x2004, b"", добивка=False))
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        п = разобрать_пакет(snap(0x2004, b"\x01", добивка=False))
        self.assertFalse(п.ошибки)
        self.assertEqual(п.инфо, "DTP без TLV")
        п = разобрать_пакет(snap(0x2004, b"\x01" + struct.pack(">HH", 2, 4), добивка=False))
        self.assertEqual(п.ошибки, ["DTP: длина TLV не больше 4"])
        п = разобрать_пакет(snap(0x2004, b"\x01" + struct.pack(">HB", 2, 0), добивка=False))
        self.assertEqual(п.ошибки, ["DTP: TLV оборван"])
        п = разобрать_пакет(snap(0x2004, b"\x01" + struct.pack(">HH", 2, 2), добивка=False))
        т = поля(п, "DTP")["dtp.tlv_type"]
        self.assertEqual((т.смещение, т.длина), (23, 4))

    def test_dtp_чужие_длины(self):
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(3, b"\xa5\x00") + tlv2(4, bytes(5)) + tlv2(9, bytes(6))))
        ф = поля(п, "DTP")
        self.assertNotIn("dtp.tot", ф)
        self.assertNotIn("dtp.senderid", ф)
        self.assertEqual(п.ошибки, ["DTP: неверная длина TLV тип транка", "DTP: неверная длина TLV отправитель"])

    def test_vtp_заголовок_36(self):
        тело = bytes([1, 9, 0]) + домен(b"X")
        self.assertFalse(разобрать_пакет(snap(0x2003, тело, добивка=False)).ошибки)
        self.assertTrue(any("оборван" in о for о in разобрать_пакет(snap(0x2003, тело[:35], добивка=False)).ошибки))
        п = self.subset(b"")
        self.assertFalse(п.ошибки)
        self.assertIn("ревизия 6, VLAN: нет", п.инфо)

    def test_vtp_границы(self):
        тело = bytes([1, 3, 0]) + домен(b"X")
        self.assertTrue(any("оборван" in о for о in разобрать_пакет(snap(0x2003, тело[:35], добивка=False)).ошибки))
        п = разобрать_пакет(snap(0x2003, тело + b"\x00", добивка=False))
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        self.assertFalse(разобрать_пакет(snap(0x2003, тело + b"\x00\x05", добивка=False)).ошибки)
        тело = bytes([1, 4, 0]) + домен(b"X") + b"\x00\x00\x00"
        self.assertTrue(any("оборван" in о for о in разобрать_пакет(snap(0x2003, тело, добивка=False)).ошибки))
        тело = bytes([1, 2, 7]) + домен(b"X") + b"\x00\x00\x00"
        self.assertTrue(any("оборван" in о for о in разобрать_пакет(snap(0x2003, тело, добивка=False)).ошибки))
        тело = bytes([1, 1, 3]) + домен(b"X") + bytes(35)
        self.assertTrue(any("оборван" in о for о in разобрать_пакет(snap(0x2003, тело, добивка=False)).ошибки))

    def test_vtp_домен_32_и_md5_и_followers(self):
        тело = bytes([1, 1, 3]) + bytes([32]) + b"D" * 32 + b"\x41\x42" + bytes(18) + bytes(range(16)) + b"\xff"
        ф = поля(разобрать_пакет(snap(0x2003, тело, добивка=False)), "VTP")
        self.assertEqual(ф["vtp.md"].текст, "D" * 32)
        self.assertEqual(ф["vtp.md5_digest"].текст, bytes(range(16)).hex())
        self.assertEqual(ф["vtp.followers"].сырое, 3)

    def test_vtp_join_без_карты(self):
        п = разобрать_пакет(snap(0x2003, bytes([1, 4, 0]) + домен(b"X") + struct.pack(">HH", 1, 2), добивка=False))
        self.assertFalse(п.ошибки)
        self.assertEqual(поля(п, "VTP")["vtp.pruning.active"].текст, "нет")

    def test_vtp_join_пустая_карта(self):
        тело = bytes([1, 4, 0]) + домен(b"X") + struct.pack(">HH", 0, 7) + b"\x00"
        ф = поля(разобрать_пакет(snap(0x2003, тело, добивка=False)), "VTP")
        self.assertEqual((ф["vtp.pruning.active"].текст, ф["vtp.pruning.active"].сырое), ("нет", []))
        тело = bytes([1, 4, 0]) + домен(b"X") + struct.pack(">HH", 8, 15) + b"\x81"
        ф = поля(разобрать_пакет(snap(0x2003, тело, добивка=False)), "VTP")
        self.assertEqual(ф["vtp.pruning.active"].текст, "8, 15")

    @staticmethod
    def запись(ид, имя, tlvs=b"", состояние=0, вид=1, добивка=None, хвост=b""):
        выровнено = 4 * ((len(имя) + 3) // 4)
        имя_в = имя + (добивка if добивка is not None else bytes(выровнено - len(имя)))
        тело = bytes([состояние, вид, len(имя)]) + struct.pack(">HHI", ид, 1500, 0x100000 + ид) + имя_в + tlvs + хвост
        return bytes([1 + len(тело)]) + тело

    def subset(self, записи):
        return разобрать_пакет(snap(0x2003, bytes([1, 2, 9]) + домен(b"CORP") + struct.pack(">I", 6) + записи,
                                    добивка=False))

    def test_vtp_subset_раскладка(self):
        п = self.subset(self.запись(30, b"abcd", b"\x06\x01\x00\x01\x03\x02\x00\x01\x00\x02", состояние=1, вид=2))
        vlan = [x for x in п.уровни[-1].поля if x.ключ == "vtp.vlan_info"][0]
        раскладка = [(x.ключ, x.смещение, x.длина, x.сырое) for x in vlan.дети]
        н = 14 + 8 + 40
        self.assertEqual(раскладка, [
            ("vtp.vlan.info_len", н, 1, 26), ("vtp.vlan.status.vlan_susp", н + 1, 1, 1), ("vtp.vlan.type", н + 2, 1, 2),
            ("vtp.vlan.isl_vlan_id", н + 4, 2, 30), ("vtp.vlan.mtu_size", н + 6, 2, 1500),
            ("vtp.vlan.802_10_index", н + 8, 4, 0x10001E), ("vtp.vlan.vlan_name", н + 12, 4, "abcd"),
            ("vtp.vlan.tlvtype", н + 16, 4, 6), ("vtp.vlan.tlvtype", н + 20, 6, 3)])
        self.assertEqual((vlan.смещение, vlan.длина), (н, 26))
        ф = поля(п, "VTP")
        self.assertEqual((ф["vtp.seq_num"].сырое, ф["vtp.seq_num"].смещение, ф["vtp.seq_num"].длина), (9, 24, 1))
        self.assertEqual((ф["vtp.conf_rev_num"].смещение, ф["vtp.conf_rev_num"].длина), (58, 4))
        self.assertEqual(vlan.дети[2].текст, "FDDI")

    def test_vtp_subset_имя_и_хвосты(self):
        п = self.subset(self.запись(5, b"abcde", добивка=b"xyz"))
        vlan = [x for x in п.уровни[-1].поля if x.ключ == "vtp.vlan_info"][0]
        self.assertEqual(vlan.имя, "VLAN 5 «abcde»")
        п = self.subset(self.запись(5, b"ab", хвост=b"\x07"))
        self.assertFalse(п.ошибки)
        vlan = [x for x in п.уровни[-1].поля if x.ключ == "vtp.vlan_info"][0]
        self.assertEqual([x.ключ for x in vlan.дети].count("vtp.vlan.tlvtype"), 0)
        п = self.subset(self.запись(5, b"ab", tlvs=b"\x07\x00"))
        vlan = [x for x in п.уровни[-1].поля if x.ключ == "vtp.vlan_info"][0]
        self.assertEqual([(x.смещение, x.длина) for x in vlan.дети if x.ключ == "vtp.vlan.tlvtype"], [(78, 2)])

    def test_vtp_subset_короткие_записи(self):
        п = self.subset(bytes([12]) + bytes(11))
        self.assertFalse(п.ошибки)
        self.assertIn("VLAN: 0", п.инфо)
        п = self.subset(bytes(11))
        self.assertFalse(п.ошибки)
        self.assertIn("VLAN: нет", п.инфо)
        п = self.subset(bytes([11]) + bytes(11))
        self.assertEqual(п.ошибки, ["VTP: запись VLAN короче 12 байт"])

    def test_udld_границы(self):
        self.assertTrue(any("оборван" in о for о in разобрать_пакет(snap(0x0111, b"\x21\x00\x00",
                                                                           добивка=False)).ошибки))
        п = разобрать_пакет(snap(0x0111, b"\x21\x01\x00\x00", добивка=False))
        self.assertFalse(п.ошибки)
        ф = поля(п, "UDLD")
        self.assertEqual((ф["udld.flags.rt"].сырое, ф["udld.flags.rsy"].сырое), (1, 0))
        п = разобрать_пакет(snap(0x0111, b"\x21\x00\x00\x00" + tlv2(7, bytes(4)) + b"\x00\x01\x00",
                                 добивка=False))
        self.assertFalse(п.ошибки)
        п = разобрать_пакет(snap(0x0111, b"\x21\x00\x00\x00" + struct.pack(">HH", 1, 2), добивка=False))
        т = поля(п, "UDLD")["udld.tlv.type"]
        self.assertEqual((т.длина, т.плохо), (4, True))

    def test_pvst_границы(self):
        б = ТестPVST().bpdu()
        п = разобрать_пакет(snap(0x010B, б + struct.pack(">HHH", 0, 2, 20) + struct.pack(">HH", 7, 0),
                                 добивка=False))
        виды = [x for x in п.уровни[-1].поля if x.ключ == "stp.pvst.tlvtype"]
        self.assertEqual([(x.имя, x.смещение, x.длина) for x in виды],
                         [("TLV Originating VLAN", 58, 6), ("TLV 7", 64, 4)])
        п = разобрать_пакет(snap(0x010B, б + struct.pack(">HHH", 0, 2, 20) + b"\x00\x07\x00", добивка=False))
        self.assertFalse(п.ошибки)
        п = разобрать_пакет(snap(0x010B, б + struct.pack(">HHB", 0, 2, 20), добивка=False))
        self.assertIn("PVST+: TLV оборван", п.ошибки)
        п = разобрать_пакет(snap(0x010B, б + struct.pack(">HHB", 0, 1, 20)))
        self.assertTrue(поля(п, "PVST+")["stp.pvst.tlvtype"].плохо)


def раскладка(п, протокол):
    """Поля уровня (и вложенные): ключ, место, длина; места сверены вручную с разбором Wireshark."""
    у = [x for x in п.уровни if x.протокол == протокол][0]
    итог = []

    def обойти(список):
        for x in список:
            итог.append((x.ключ, x.смещение, x.длина))
            обойти(x.дети)
    обойти(у.поля)
    return итог, у.смещение, у.длина


class ТестРаскладки(unittest.TestCase):
    """Кадр 802.3 — 14 байт, LLC/SNAP — 8: данные Cisco начинаются с 22; ISL — с начала кадра."""

    def test_isl(self):
        п = разобрать_пакет(ТестISL().кадр())
        self.assertEqual(раскладка(п, "ISL"), ([
            ("isl.dst", 0, 6), ("isl.type", 5, 1), ("isl.user_eth", 5, 1), ("isl.src", 6, 6),
            ("isl.len", 12, 2), ("isl.hsa", 17, 3), ("isl.vlan_id", 20, 2), ("isl.bpdu", 20, 2),
            ("isl.index", 22, 2)
        ], 0, 26))

    def test_dtp(self):
        п = разобрать_пакет(snap(0x2004, b"\x01" + tlv2(1, b"lab\x00") + tlv2(2, b"\x81") + tlv2(3, b"\xa5")
                                 + tlv2(4, mac("00:aa:bb:cc:dd:ee"))))
        self.assertEqual(раскладка(п, "DTP"), ([
            ("dtp.version", 22, 1), ("dtp.tlv_type", 23, 8), ("dtp.tlv_len", 25, 2),
            ("dtp.domain", 27, 4), ("dtp.tlv_type", 31, 5), ("dtp.tlv_len", 33, 2),
            ("dtp.tos", 35, 1), ("dtp.tas", 35, 1), ("dtp.tlv_type", 36, 5),
            ("dtp.tlv_len", 38, 2), ("dtp.tot", 40, 1), ("dtp.tat", 40, 1),
            ("dtp.tlv_type", 41, 10), ("dtp.tlv_len", 43, 2), ("dtp.senderid", 45, 6)
        ], 22, 29))

    def test_vtp1(self):
        п = разобрать_пакет(snap(0x2003, bytes([2, 1, 1]) + домен(b"CORP") + struct.pack(">I", 17)
                                 + с.a4("10.1.1.1") + b"240927101530" + bytes(range(16))))
        self.assertEqual(раскладка(п, "VTP"), ([
            ("vtp.version", 22, 1), ("vtp.code", 23, 1), ("vtp.md_len", 25, 1), ("vtp.md", 26, 32),
            ("vtp.followers", 24, 1), ("vtp.conf_rev_num", 58, 4), ("vtp.upd_id", 62, 4),
            ("vtp.upd_ts", 66, 12), ("vtp.md5_digest", 78, 16)
        ], 22, 72))

    def test_vtp4(self):
        п = разобрать_пакет(snap(0x2003, bytes([1, 4, 0]) + домен(b"CORP") + struct.pack(">HH", 0, 15)
                                 + b"\xa0\x01", добивка=False))
        self.assertEqual(раскладка(п, "VTP"), ([
            ("vtp.version", 22, 1), ("vtp.code", 23, 1), ("vtp.md_len", 25, 1), ("vtp.md", 26, 32),
            ("vtp.pruning.first", 58, 2), ("vtp.pruning.last", 60, 2),
            ("vtp.pruning.active", 62, 2)
        ], 22, 42))

    def test_vtp3(self):
        п = разобрать_пакет(snap(0x2003, bytes([1, 3, 0]) + домен(b"CORP") + struct.pack(">H", 5)))
        self.assertEqual(раскладка(п, "VTP"), ([
            ("vtp.version", 22, 1), ("vtp.code", 23, 1), ("vtp.md_len", 25, 1), ("vtp.md", 26, 32),
            ("vtp.start_value", 58, 2)
        ], 22, 38))

    def test_udld(self):
        п = разобрать_пакет(snap(0x0111, bytes([0x21, 0x03]) + b"\x12\x34" + tlv2(1, b"SW1\x00")
                                 + tlv2(2, b"Gi0/1") + tlv2(3, b"\x00\x00\x00\x01") + tlv2(4, b"\x07")
                                 + tlv2(5, b"\x05") + tlv2(6, b"sw1.corp") + tlv2(7, b"\x00\x00\x00\x2a")))
        self.assertEqual(раскладка(п, "UDLD"), ([
            ("udld.version", 22, 1), ("udld.opcode", 22, 1), ("udld.flags", 23, 1),
            ("udld.flags.rt", 23, 1), ("udld.flags.rsy", 23, 1), ("udld.checksum", 24, 2),
            ("udld.tlv.type", 26, 8), ("udld.tlv.len", 28, 2), ("udld.device_id", 30, 4),
            ("udld.tlv.type", 34, 9), ("udld.tlv.len", 36, 2),
            ("udld.sent_through_interface", 38, 5), ("udld.tlv.type", 43, 8),
            ("udld.tlv.len", 45, 2), ("udld.data", 47, 4), ("udld.tlv.type", 51, 5),
            ("udld.tlv.len", 53, 2), ("udld.msg_interval", 55, 1), ("udld.tlv.type", 56, 5),
            ("udld.tlv.len", 58, 2), ("udld.timeout_interval", 60, 1), ("udld.tlv.type", 61, 12),
            ("udld.tlv.len", 63, 2), ("udld.device_name", 65, 8), ("udld.tlv.type", 73, 8),
            ("udld.tlv.len", 75, 2), ("udld.seq_num", 77, 4)
        ], 22, 59))

    def test_pvst(self):
        п = разобрать_пакет(snap(0x010B, ТестPVST().bpdu() + struct.pack(">HHH", 0, 2, 20)))
        self.assertEqual(раскладка(п, "PVST+")[0][-3:], [
            ("stp.pvst.tlvtype", 58, 6), ("stp.pvst.tlvlength", 60, 2),
            ("stp.pvst.origvlan", 62, 2)
        ])


if __name__ == "__main__":
    unittest.main()
