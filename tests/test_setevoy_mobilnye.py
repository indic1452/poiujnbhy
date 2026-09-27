"""Мобильные сети и AAA: Diameter, GTPv1-C, GTPv2-C, GTP', PFCP, S1AP, NGAP, X2AP, SGsAP,
GSMTAP, TACACS+, Kerberos, LDAP.

Пакеты собираются здесь же по документам (RFC 6733, TS 29.060, TS 29.274, TS 32.295,
TS 29.244, X.691 для S1AP/NGAP/X2AP, TS 29.118, gsmtap.h, RFC 8907, RFC 4120, RFC 4511) —
struct.pack'ом и BER вручную, без функций разборщика.
"""

import random
import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет
from reportgen.setevoy.filtr import отобрать
from reportgen.setevoy.statistika import уровень_протокола

СВОИ = {"Diameter", "GTP", "GTPv2", "GTP'", "PFCP", "S1AP", "NGAP", "X2AP", "SGsAP", "GSMTAP", "TACACS+",
        "Kerberos", "LDAP"}
UDP_НАГРУЗКА = 14 + 20 + 8          # Ethernet + IPv4 + UDP
TCP_НАГРУЗКА = 14 + 20 + 20
SCTP_НАГРУЗКА = 14 + 20 + 12 + 16   # + общий заголовок SCTP и заголовок куска DATA


# -- сборка ------------------------------------------------------------------------------------

def на_udp(нагрузка, dport, sport=40000):
    return с.eth(с.ip(с.udp(нагрузка, sport, dport), 17))


def оборвать(пакет, длина):
    """Запись обрезана (snaplen): байт меньше, исходная длина кадра — полная."""
    return разобрать_пакет(пакет[:длина], исходная_длина=len(пакет))


def на_tcp(нагрузка, dport, sport=40000):
    return с.eth(с.ip(с.tcp(нагрузка, sport, dport), 6))


def crc32c(данные):
    """CRC-32C (Castagnoli, RFC 9260, приложение B) — побитно, независимо от разборщика."""
    crc = 0xFFFFFFFF
    for б in данные:
        crc ^= б
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def на_sctp(нагрузка, ppid, sport=36412, dport=36412, длина_куска=None):
    """SCTP (RFC 9260, 3.1, 3.3.1) с одним куском DATA; CRC32c — в порядке байт «младший первым»."""
    дл = 16 + len(нагрузка) if длина_куска is None else длина_куска
    кусок = struct.pack(">BBHIHHI", 0, 3, дл, 1, 0, 0, ppid) + нагрузка + b"\0" * (-len(нагрузка) % 4)
    заголовок = struct.pack(">HHII", sport, dport, 0x01020304, 0)
    crc = crc32c(заголовок + кусок)
    return с.eth(с.ip(заголовок[:8] + struct.pack("<I", crc) + кусок, 132))


def tbcd(цифры):
    """TBCD: младший полубайт — первая цифра пары, F — заполнитель."""
    цифры = цифры + ("f" if len(цифры) % 2 else "")
    return bytes(int(цифры[i + 1], 16) << 4 | int(цифры[i], 16) for i in range(0, len(цифры), 2))


def метки(имя):
    return b"".join(bytes([len(ч)]) + ч.encode() for ч in имя.split("."))


def avp(код, данные, флаги=0x40, вендор=None):
    """AVP (RFC 6733, 4.1): код, флаги, длина (без выравнивания), Vendor-ID при V, данные, нули до 4."""
    заг = 12 if вендор is not None else 8
    дл = заг + len(данные)
    б = struct.pack(">IB", код, флаги | (0x80 if вендор is not None else 0)) + дл.to_bytes(3, "big")
    if вендор is not None:
        б += struct.pack(">I", вендор)
    return б + данные + b"\0" * (-дл % 4)


def diameter(код, avps, флаги=0x80, прил=0, длина=None, версия=1):
    """Заголовок Diameter (RFC 6733, 3): версия, длина (3), флаги, код (3), приложение, h2h, e2e."""
    дл = 20 + len(avps) if длина is None else длина
    return bytes([версия]) + дл.to_bytes(3, "big") + bytes([флаги]) + код.to_bytes(3, "big") \
        + struct.pack(">III", прил, 0x0A0B0C0D, 0x11223344) + avps


CER = diameter(257, avp(264, b"mme.example.org") + avp(296, b"example.org")
               + avp(257, struct.pack(">H4s", 1, с.a4("10.1.2.3"))) + avp(266, struct.pack(">I", 10415))
               + avp(269, b"test", 0)
               + avp(260, avp(266, struct.pack(">I", 10415)) + avp(258, struct.pack(">I", 16777251)))
               + avp(1407, b"\x52\xf0\x10", 0x40, вендор=10415))
CCA = diameter(272, avp(263, b"gw;1;2") + avp(268, struct.pack(">I", 2001)) + avp(264, b"ocs.example.org")
               + avp(296, b"example.org") + avp(258, struct.pack(">I", 4)) + avp(416, struct.pack(">I", 1))
               + avp(415, struct.pack(">I", 0)), флаги=0x40, прил=4)


def gtp1(тип, ies, teid=0, номер=0x1234, флаги=0x32, длина=None, расширения=b""):
    """GTPv1-C (TS 29.060, 6): флаги, тип, длина (после 8 байт), TEID, номер, N-PDU, след. расширение."""
    хвост = struct.pack(">HBB", номер, 0, 0) if not расширения else struct.pack(">HB", номер, 0) + расширения
    тело = хвост + ies
    return struct.pack(">BBHI", флаги, тип, len(тело) if длина is None else длина, teid) + тело


def tv(тип, значение):
    return bytes([тип]) + значение


def tlv1(тип, значение):
    return bytes([тип]) + struct.pack(">H", len(значение)) + значение


IMSI = "250011234567890"
CREATE_PDP = gtp1(16, tv(2, tbcd(IMSI)) + tv(14, b"\x05") + tv(15, b"\xfc") + tv(16, struct.pack(">I", 0x11111111))
                  + tv(17, struct.pack(">I", 0x22222222)) + tv(20, b"\x05") + tlv1(128, b"\xf1\x21")
                  + tlv1(131, метки("internet")) + tlv1(133, с.a4("192.0.2.1")) + tlv1(133, с.a4("192.0.2.2"))
                  + tlv1(134, b"\x91" + tbcd("79161234567")) + tlv1(135, bytes.fromhex("0b921f")))


def ie2(тип, значение, instance=0):
    """IE GTPv2 (TS 29.274, 8.2.1): тип, длина, запасные 4 бита + instance, значение."""
    return struct.pack(">BHB", тип, len(значение), instance) + значение


def gtp2(тип, ies, teid=None, номер=0x000102, p=0, флаги=None, длина=None):
    """GTPv2-C (TS 29.274, 5.1): флаги (версия 2, P, T, MP), тип, длина (после 4 байт), [TEID], номер, запас."""
    ф = 0x40 | (0x10 if p else 0) | (0x08 if teid is not None else 0) if флаги is None else флаги
    тело = (struct.pack(">I", teid) if teid is not None else b"") + номер.to_bytes(3, "big") + b"\0" + ies
    return struct.pack(">BBH", ф, тип, len(тело) if длина is None else длина) + тело


PLMN_25001 = b"\x52\xf0\x10"
CREATE_SESSION = gtp2(32, ie2(1, tbcd(IMSI)) + ie2(76, tbcd("79161234567")) + ie2(75, tbcd("3534900698733190"))
                      + ie2(83, PLMN_25001) + ie2(82, b"\x06")
                      + ie2(87, bytes([0x80 | 10]) + struct.pack(">I", 0xABCDEF01) + с.a4("198.51.100.7"))
                      + ie2(71, метки("internet")) + ie2(128, b"\x00") + ie2(99, b"\x01")
                      + ie2(79, b"\x01" + с.a4("0.0.0.0")) + ie2(72, struct.pack(">II", 50000, 100000))
                      + ie2(93, ie2(73, b"\x05") + ie2(80, bytes([0x45, 9]) + bytes(20))), teid=0)
ECHO2 = gtp2(1, ie2(3, b"\x07"))


def gtpp(тип, ies, номер=7, флаги=0x4F, длина=None, заголовок20=False):
    """GTP' (TS 32.295, 6.1): флаги (версия, PT=0, «111», тип заголовка), тип, длина, номер."""
    запас = b"\xff" * 14 if заголовок20 else b""
    return struct.pack(">BBHH", флаги, тип, len(ies) if длина is None else длина, номер) + запас + ies


DRT = gtpp(240, tv(126, b"\x01") + tlv1(252, b"\x01\x01\x00\x07" + struct.pack(">H", 6) + b"record"))


def ie_pfcp(тип, значение):
    """IE PFCP (TS 29.244, 8.1.1): тип (2), длина (2), значение."""
    return struct.pack(">HH", тип, len(значение)) + значение


def pfcp(тип, ies, seid=None, номер=0x000A0B, флаги=None, длина=None):
    """PFCP (TS 29.244, 7.2.2): флаги (версия 1, запас, FO, MP, S), тип, длина, [SEID], номер, запас."""
    ф = 0x20 | (1 if seid is not None else 0) if флаги is None else флаги
    тело = (struct.pack(">Q", seid) if seid is not None else b"") + номер.to_bytes(3, "big") + b"\0" + ies
    return struct.pack(">BBH", ф, тип, len(тело) if длина is None else длина) + тело


HEARTBEAT = pfcp(1, ie_pfcp(96, struct.pack(">I", 3913056000)))
SESSION_EST = pfcp(50, ie_pfcp(60, b"\x00" + с.a4("10.10.0.1"))
                   + ie_pfcp(57, b"\x02" + struct.pack(">Q", 0x1122334455667788) + с.a4("10.10.0.1"))
                   + ie_pfcp(1, ie_pfcp(56, struct.pack(">H", 1)) + ie_pfcp(29, struct.pack(">I", 255))
                             + ie_pfcp(2, ie_pfcp(20, b"\x00") + ie_pfcp(21, b"\x01" + struct.pack(">I", 0x5555)
                                                                          + с.a4("10.20.0.1")))
                             + ie_pfcp(108, struct.pack(">I", 1)))
                   + ie_pfcp(3, ie_pfcp(108, struct.pack(">I", 1)) + ie_pfcp(44, b"\x02")), seid=0)


def aper_длина(n):
    """Определитель длины X.691, 10.9.3.6–10.9.3.7."""
    return bytes([n]) if n < 128 else struct.pack(">H", 0x8000 | n)


def aper_pdu(вид, процедура, важность, ies):
    """PDU S1AP/NGAP/X2AP (APER): CHOICE (бит расширения + индекс), procedureCode, criticality,
    открытый тип; значение — SEQUENCE {protocolIEs} с числом IE (2 байта) и IE (id, crit, длина, байты)."""
    значение = b"\x00" + struct.pack(">H", len(ies)) + b"".join(
        struct.pack(">HB", ид, в << 6) + aper_длина(len(з)) + з for ид, в, з in ies)
    return bytes([вид << 5, процедура, важность << 6]) + aper_длина(len(значение)) + значение


S1_SETUP = aper_pdu(0, 17, 0, [(59, 0, bytes.fromhex("0052f010000001")), (60, 1, b"\x04enb1"),
                               (64, 0, bytes.fromhex("000001" "52f010")), (137, 1, b"\x40")])
NG_SETUP_OK = aper_pdu(1, 21, 0, [(96, 1, b"\x03amf"), (86, 1, bytes(200))])
X2_SETUP = aper_pdu(0, 6, 0, [(21, 0, bytes(12))])


def imsi_личность(цифры):
    """Mobile Identity (TS 24.008, 10.5.1.4) для IMSI: первая цифра, признак нечётности, тип 1."""
    ц = [int(x) for x in цифры]
    первый = ц[0] << 4 | (8 if len(ц) % 2 else 0) | 1
    остаток = ц[1:] + ([15] if len(ц) % 2 == 0 else [])
    return bytes([первый]) + bytes(остаток[i + 1] << 4 | остаток[i] for i in range(0, len(остаток), 2))


def sg_ie(iei, значение):
    return bytes([iei, len(значение)]) + значение


MME_ИМЯ = "mmec01.mmegi8001.mme.epc.mnc001.mcc250.3gppnetwork.org"
LU_REQUEST = bytes([0x09]) + sg_ie(0x01, imsi_личность(IMSI)) + sg_ie(0x09, метки(MME_ИМЯ)) \
    + sg_ie(0x0A, b"\x01") + sg_ie(0x04, PLMN_25001 + b"\x00\x01")
RESET_IND = bytes([0x15]) + sg_ie(0x09, метки(MME_ИМЯ))


def gsmtap(версия=2, длина=4, тип=1, arfcn=0x4000 | 62, сигнал=-70, snr=10, кадр=123456, подтип=1,
           нагрузка=bytes(23)):
    """struct gsmtap_hdr (osmocom gsmtap.h): версия, длина в словах, тип, слот, ARFCN, сигнал, SNR,
    номер кадра, подтип, антенна, подслот, резерв."""
    return struct.pack(">BBBBHbbIBBBB", версия, длина, тип, 3, arfcn, сигнал, snr, кадр, подтип, 0, 0, 0) + нагрузка


def tacacs(тип, seq, тело, флаги=0x01, версия=0xC1, сеанс=0x12345678, длина=None):
    """Заголовок TACACS+ (RFC 8907, 4.1): версия, тип, seq_no, флаги, session_id, длина тела."""
    return struct.pack(">BBBBII", версия, тип, seq, флаги, сеанс, len(тело) if длина is None else длина) + тело


def tac_start(пользователь=b"alice", порт=b"tty0", адрес=b"10.0.0.5", данные_=b"secret"):
    """Authentication START (RFC 8907, 5.1)."""
    return struct.pack(">8B", 1, 1, 2, 1, len(пользователь), len(порт), len(адрес), len(данные_)) \
        + пользователь + порт + адрес + данные_


АРГУМЕНТЫ = [b"service=shell", b"task_id=7"]
TAC_ACCT = tacacs(3, 1, struct.pack(">9B", 0x02, 6, 1, 1, 1, 3, 4, 0, len(АРГУМЕНТЫ))
                  + bytes(len(а) for а in АРГУМЕНТЫ) + b"bob" + b"tty1" + b"".join(АРГУМЕНТЫ))


def ber(тег, значение):
    дл = len(значение)
    if дл < 128:
        return bytes([тег, дл]) + значение
    if дл < 256:
        return bytes([тег, 0x81, дл]) + значение
    return bytes([тег, 0x82]) + struct.pack(">H", дл) + значение


def целое(n):
    return ber(0x02, n.to_bytes((n.bit_length() + 8) // 8, "big", signed=True))


def кт(n, внутри):
    """Явный контекстный тег [n] (составной)."""
    return ber(0xA0 | n, внутри)


def строка_krb(т):
    return ber(0x1B, т.encode())


def principal(тип, *части):
    return ber(0x30, кт(0, целое(тип)) + кт(1, ber(0x30, b"".join(строка_krb(ч) for ч in части))))


def enc_data(etype, kvno, шифр):
    return ber(0x30, кт(0, целое(etype)) + (кт(1, целое(kvno)) if kvno is not None else b"")
               + кт(2, ber(0x04, шифр)))


def as_req(pvno=5, тип=10, тег=0x6A):
    """AS-REQ (RFC 4120, 5.4.1): KDC-REQ с pvno [1], msg-type [2], padata [3], req-body [4]."""
    тело = ber(0x30, кт(0, ber(0x03, b"\x00\x40\x81\x00\x10")) + кт(1, principal(1, "alice"))
               + кт(2, строка_krb("EXAMPLE.COM")) + кт(3, principal(2, "krbtgt", "EXAMPLE.COM"))
               + кт(5, ber(0x18, b"20370913024805Z")) + кт(7, целое(123456789))
               + кт(8, ber(0x30, целое(18) + целое(17) + целое(23))))
    padata = ber(0x30, ber(0x30, кт(1, целое(128)) + кт(2, ber(0x04, bytes.fromhex("3005a0030101ff")))))
    return ber(тег, ber(0x30, кт(1, целое(pvno)) + кт(2, целое(тип)) + кт(3, padata) + кт(4, тело)))


AS_REP = ber(0x6B, ber(0x30, кт(0, целое(5)) + кт(1, целое(11)) + кт(3, строка_krb("EXAMPLE.COM"))
                       + кт(4, principal(1, "alice"))
                       + кт(5, ber(0x61, ber(0x30, кт(0, целое(5)) + кт(1, строка_krb("EXAMPLE.COM"))
                                             + кт(2, principal(2, "krbtgt", "EXAMPLE.COM"))
                                             + кт(3, enc_data(18, 2, bytes(40))))))
                       + кт(6, enc_data(18, None, bytes(60)))))
KRB_ERROR = ber(0x7E, ber(0x30, кт(0, целое(5)) + кт(1, целое(30)) + кт(4, ber(0x18, b"20240101000000Z"))
                          + кт(5, целое(0)) + кт(6, целое(25)) + кт(9, строка_krb("EXAMPLE.COM"))
                          + кт(10, principal(2, "krbtgt", "EXAMPLE.COM")) + кт(12, ber(0x04, b"\x30\x00"))))


def на_tcp_krb(сообщение):
    """RFC 4120, 7.2.2: 4 байта длины перед сообщением."""
    return struct.pack(">I", len(сообщение)) + сообщение


def ldap(ид, операция):
    """LDAPMessage (RFC 4511, 4.1.1): SEQUENCE {messageID, protocolOp}."""
    return ber(0x30, целое(ид) + операция)


DN = b"cn=admin,dc=example,dc=org"
BIND = ldap(1, ber(0x60, целое(3) + ber(0x04, DN) + ber(0x80, b"secret")))
SEARCH = ldap(2, ber(0x63, ber(0x04, b"dc=example,dc=org") + ber(0x0A, b"\x02") + ber(0x0A, b"\x00")
                     + целое(0) + целое(0) + ber(0x01, b"\x00")
                     + ber(0xA0, ber(0x87, b"objectClass") + ber(0xA3, ber(0x04, b"uid") + ber(0x04, b"alice")))
                     + ber(0x30, ber(0x04, b"cn") + ber(0x04, b"mail"))))
BIND_OK = ldap(1, ber(0x61, ber(0x0A, b"\x00") + ber(0x04, b"") + ber(0x04, b"")))
RES_ENTRY = ldap(2, ber(0x64, ber(0x04, b"uid=alice,dc=example,dc=org")
                        + ber(0x30, ber(0x30, ber(0x04, b"cn") + ber(0x31, ber(0x04, b"alice"))))))
RES_DONE = ldap(2, ber(0x65, ber(0x0A, b"\x00") + ber(0x04, b"") + ber(0x04, b"")))


# -- проверки -----------------------------------------------------------------------------------

def найти(пакет, ключ, n=0):
    """n-е поле с ключом (обход дерева всех уровней)."""
    найдено = []

    def обойти(поля):
        for п in поля:
            if п.ключ == ключ:
                найдено.append(п)
            обойти(п.дети)
    for у in пакет.уровни:
        обойти(у.поля)
    return найдено[n]


def значения(пакет, ключ):
    return пакет.поля_фильтра().get(ключ, [])


class Общее(unittest.TestCase):
    def место(self, пакет, ключ, смещение, длина, n=0):
        п = найти(пакет, ключ, n)
        self.assertEqual((смещение, длина), (п.смещение, п.длина), ключ)
        return п

    def чисто(self, пакет, стек):
        self.assertEqual(стек, пакет.стек)
        self.assertEqual([], [о for о in пакет.ошибки if "CRC32c" not in о], пакет.ошибки)


class DiameterTests(Общее):
    def test_cer_поля_и_места(self):
        п = разобрать_пакет(на_tcp(CER, 3868))
        self.чисто(п, ["Ethernet", "IPv4", "TCP", "Diameter"])
        м = TCP_НАГРУЗКА
        self.assertEqual("Diameter CER", п.инфо)
        self.assertEqual([257], значения(п, "diameter.cmd.code"))
        self.assertEqual([len(CER)], значения(п, "diameter.length"))
        self.assertEqual([1], значения(п, "diameter.flags.request"))
        self.место(п, "diameter.cmd.code", м + 5, 3)
        self.место(п, "diameter.hopbyhopid", м + 12, 4)
        self.assertEqual([0x0A0B0C0D], значения(п, "diameter.hopbyhopid"))
        # Первый AVP — Origin-Host: заголовок 8, строка 15, выравнивание 1.
        self.место(п, "diameter.avp", м + 20, 24)
        self.место(п, "diameter.origin-host", м + 28, 15)
        self.assertEqual(["mme.example.org"], значения(п, "diameter.origin-host"))
        self.место(п, "diameter.avp.pad", м + 43, 1)
        self.assertEqual(["10.1.2.3"], значения(п, "diameter.host-ip-address"))
        # Grouped: Vendor-Specific-Application-Id → Vendor-Id и Auth-Application-Id детьми.
        группа = [ф for ф in п.уровни[-1].поля if ф.сырое == 260][0]
        self.assertEqual([266, 258], [д.сырое for д in группа.дети if д.ключ == "diameter.avp"])
        self.assertEqual([16777251], значения(п, "diameter.auth-application-id"))
        # AVP 3GPP с флагом V: Vendor-ID и значение по таблице 3GPP.
        self.assertEqual([10415], значения(п, "diameter.avp.vendorid"))
        self.assertEqual(["250-01"], значения(п, "diameter.visited-plmn-id"))

    def test_cca_на_sctp_по_ppid_и_порту(self):
        п = разобрать_пакет(на_sctp(CCA, 46, 3868, 3868))
        self.чисто(п, ["Ethernet", "IPv4", "SCTP", "Diameter"])
        self.assertEqual("Diameter CCA, Result-Code 2001 (DIAMETER_SUCCESS), сеанс gw;1;2", п.инфо)
        self.assertEqual([2001], значения(п, "diameter.result-code"))
        self.assertEqual([1], значения(п, "diameter.cc-request-type"))
        self.место(п, "diameter.result-code", SCTP_НАГРУЗКА + 20 + 16 + 8, 4)
        п = разобрать_пакет(на_sctp(CCA, 0, 3868, 50000))
        self.assertEqual("Diameter", п.протокол)

    def test_два_сообщения_в_сегменте_и_хвост(self):
        п = разобрать_пакет(на_tcp(CER + CCA + CER[:10], 3868))
        self.assertEqual(["Ethernet", "IPv4", "TCP", "Diameter", "Diameter", "Данные"], п.стек)
        self.assertEqual(TCP_НАГРУЗКА + len(CER), п.уровни[4].смещение)

    def test_оборван(self):
        п = разобрать_пакет(на_tcp(CER[:60], 3868))
        self.assertEqual("Diameter", п.протокол)
        self.assertIn("[оборван]", п.инфо)
        self.assertTrue(any("Diameter: сообщение оборвано" in о for о in п.ошибки))
        # Оборванное с незнакомым кодом команды — не признаём.
        чужой = diameter(9999, avp(264, b"host") * 5)
        self.assertNotIn("Diameter", разобрать_пакет(на_tcp(чужой[:30], 3868)).стек)
        self.assertEqual("Diameter", разобрать_пакет(на_tcp(чужой, 3868)).протокол)
        # Длина длиннее сегмента (продолжение дальше), но не кратна 4 — не Diameter.
        тело = avp(264, b"host.example")
        self.assertNotIn("Diameter", разобрать_пакет(на_tcp(diameter(280, тело, длина=20 + len(тело) + 2), 3868)).стек)
        self.assertEqual("Diameter", разобрать_пакет(на_tcp(diameter(280, тело, длина=20 + len(тело) + 4), 3868)).протокол)

    def test_проверки_заголовка(self):
        тело = avp(264, b"host.example") + avp(296, b"example")
        for имя, сообщение in (
                ("версия 2", diameter(280, тело, версия=2)),
                ("длина не кратна 4", diameter(280, тело, длина=20 + len(тело) - 2)),
                ("длина меньше заголовка", diameter(280, b"", длина=16)),
                ("длина короче цепочки AVP", diameter(280, тело, длина=20 + len(тело) - 4)),
                ("зарезервированный флаг", diameter(280, тело, флаги=0x88)),
                ("E в запросе", diameter(280, тело, флаги=0xA0)),
                ("зарезервированный флаг AVP", diameter(280, avp(264, b"host", 0x50))),
                ("длина AVP меньше заголовка", diameter(280, avp(264, b"host")[:5] + b"\x00\x00\x04" + b"\0" * 8)),
                ("V без места под Vendor-ID", diameter(280, struct.pack(">IB", 264, 0xC0) + (10).to_bytes(3, "big")
                                                        + b"\0" * 4)),
        ):
            with self.subTest(имя):
                self.assertNotIn("Diameter", разобрать_пакет(на_tcp(сообщение, 3868)).стек)
        # Ответ с E — законен.
        self.assertEqual("Diameter", разобрать_пакет(на_tcp(diameter(280, тело, флаги=0x20), 3868)).протокол)

    def test_эвристика_без_порта(self):
        self.assertEqual("Diameter", разобрать_пакет(на_tcp(CER, 5555)).протокол)
        # Без порта: незнакомый код команды или обрыв — не Diameter.
        self.assertNotIn("Diameter", разобрать_пакет(на_tcp(diameter(9999, avp(264, b"h")), 5555)).стек)
        self.assertNotIn("Diameter", разобрать_пакет(на_tcp(CER[:60], 5555)).стек)


class GtpTests(Общее):
    def test_gtpv1_create_pdp(self):
        п = разобрать_пакет(на_udp(CREATE_PDP, 2123))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "GTP"])
        м = UDP_НАГРУЗКА
        self.assertEqual("GTP Create PDP Context Request, TEID 0x00000000, номер 4660", п.инфо)
        self.assertEqual([16], значения(п, "gtp.message"))
        self.место(п, "gtp.length", м + 2, 2)
        self.assertEqual([len(CREATE_PDP) - 8], значения(п, "gtp.length"))
        self.место(п, "gtp.seq_number", м + 8, 2)
        self.место(п, "gtp.imsi", м + 13, 8)
        self.assertEqual([IMSI], значения(п, "gtp.imsi"))
        self.assertEqual(["0x22222222"], значения(п, "gtp.teid_cp"))
        self.assertEqual(["internet"], значения(п, "gtp.apn"))
        self.assertEqual(["192.0.2.1", "192.0.2.2"], значения(п, "gtp.gsn_addr"))
        self.assertEqual(["79161234567"], значения(п, "gtp.msisdn"))
        self.assertEqual(["5"], значения(п, "gtp.nsapi"))

    def test_gtpv1_незнакомый_tv_останавливает(self):
        сообщение = gtp1(1, tv(14, b"\x01") + tv(6, b"\x01\x02") + tlv1(255, b"xx"))
        п = разобрать_пакет(на_udp(сообщение, 2123))
        self.assertEqual("GTP", п.протокол)
        self.assertEqual(["1"], значения(п, "gtp.recovery"))
        self.assertEqual([6], значения(п, "gtp.ie.unknown"))
        self.место(п, "gtp.ie.unknown", UDP_НАГРУЗКА + 14, 3 + 5)

    def test_gtpv1_расширения(self):
        сообщение = gtp1(1, b"", флаги=0x36, расширения=bytes([0xC0]) + bytes([1, 0xAB, 0xCD, 0]))
        п = разобрать_пакет(на_udp(сообщение, 2123))
        self.assertEqual([0xC0], значения(п, "gtp.ext_hdr"))
        нулевое = gtp1(1, b"", флаги=0x36, расширения=bytes([0xC0]) + bytes([0, 0xAB, 0xCD, 0]))
        self.assertNotIn("GTP", разобрать_пакет(на_udp(нулевое, 2123)).стек)

    def test_gtpv1_проверки(self):
        ies = tv(14, b"\x01")
        for имя, сообщение in (("версия 3", gtp1(1, ies, флаги=0x72)), ("PT=0 (GTP')", gtp1(1, ies, флаги=0x22)),
                               ("запасной бит", gtp1(1, ies, флаги=0x3A)), ("нет S", gtp1(1, ies, флаги=0x30)),
                               ("длина короче", gtp1(1, ies, длина=4)),
                               ("длина длиннее, тип незнаком", gtp1(200, ies, длина=7)),
                               ("TLV за концом", gtp1(1, tlv1(255, b"xx")[:-1]))):
            with self.subTest(имя):
                self.assertNotIn("GTP", разобрать_пакет(на_udp(сообщение, 2123)).стек)
        п = оборвать(на_udp(CREATE_PDP, 2123), UDP_НАГРУЗКА + 30)
        self.assertEqual("GTP", п.протокол)
        self.assertIn("[оборван]", п.инфо)
        # Датаграмма целая, а сообщение длиннее её — не обрыв, а чужие данные.
        self.assertNotIn("GTP", разобрать_пакет(на_udp(CREATE_PDP[:30], 2123)).стек)
        # Оборванное с незнакомым типом — не признаём.
        self.assertNotIn("GTP", оборвать(на_udp(bytes([0x32, 200]) + CREATE_PDP[2:], 2123), UDP_НАГРУЗКА + 30).стек)

    def test_gtpv2_create_session(self):
        п = разобрать_пакет(на_udp(CREATE_SESSION, 2123))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "GTPv2"])
        м = UDP_НАГРУЗКА
        self.assertEqual("GTPv2 Create Session Request, TEID 0x00000000, номер 258, IMSI 250011234567890", п.инфо)
        self.assertEqual([32], значения(п, "gtpv2.message_type"))
        self.место(п, "gtpv2.teid", м + 4, 4)
        self.место(п, "gtpv2.seq", м + 8, 3)
        self.место(п, "gtpv2.imsi", м + 16, 8)
        self.assertEqual([IMSI], значения(п, "gtpv2.imsi"))
        self.assertEqual(["79161234567"], значения(п, "gtpv2.msisdn"))
        self.assertEqual(["250-01"], значения(п, "gtpv2.serving_network"))
        self.assertEqual([0xABCDEF01], значения(п, "gtpv2.f_teid"))
        self.assertEqual(["internet"], значения(п, "gtpv2.apn"))
        self.assertEqual([6], значения(п, "gtpv2.rat_type"))
        # Bearer Context — деревом: EBI и Bearer QoS внутри.
        контекст = найти(п, "gtpv2.ie_type", 11)
        self.assertEqual(93, контекст.сырое)
        self.assertEqual([73, 80], [д.сырое for д in контекст.дети if д.ключ == "gtpv2.ie_type"])
        self.assertEqual([5], значения(п, "gtpv2.ebi"))

    def test_gtpv2_echo_и_пиггибэк(self):
        п = разобрать_пакет(на_udp(ECHO2, 2123))
        self.assertEqual("GTPv2 Echo Request, номер 258", п.инфо)
        self.assertEqual([7], значения(п, "gtpv2.recovery"))
        первое = gtp2(33, ie2(2, b"\x10\x00"), teid=5, p=1)
        п = разобрать_пакет(на_udp(первое + gtp2(95, ie2(73, b"\x06"), teid=5), 2123))
        self.assertEqual(["GTPv2", "GTPv2"], п.стек[3:])
        self.assertEqual(["16 (Request accepted)"], [ф.текст for ф in [найти(п, "gtpv2.cause")]])

    def test_gtpv2_проверки(self):
        ies = ie2(3, b"\x07")
        for имя, сообщение in (("запасные биты", gtp2(1, ies, флаги=0x41)), ("Echo с TEID", gtp2(1, ies, teid=1)),
                               ("длина короче", gtp2(1, ies, длина=8)),
                               ("длина меньше заголовка", gtp2(1, b"", длина=2)),
                               ("хвост без P", gtp2(1, ies) + b"\x00"),
                               ("два сообщения без P", ECHO2 + ECHO2),
                               ("P, а за ним мусор", gtp2(1, ies, p=1) + b"\x01\x02\x03\x04\x05\x06\x07\x08"),
                               ("IE за концом", gtp2(1, ie2(3, b"\x07")[:4] + b"")),
                               ("оборван, тип незнаком", gtp2(250, ies, длина=40))):
            with self.subTest(имя):
                self.assertNotIn("GTPv2", разобрать_пакет(на_udp(сообщение, 2123)).стек)
        п = оборвать(на_udp(CREATE_SESSION, 2123), UDP_НАГРУЗКА + 40)
        self.assertEqual("GTPv2", п.протокол)
        self.assertIn("[оборван]", п.инфо)
        self.assertEqual([IMSI], значения(п, "gtpv2.imsi"))
        self.assertNotIn("GTPv2", разобрать_пакет(на_udp(CREATE_SESSION[:40], 2123)).стек)
        self.assertNotIn("GTPv2", оборвать(на_udp(bytes([0x48, 250]) + CREATE_SESSION[2:], 2123),
                                           UDP_НАГРУЗКА + 40).стек)
        # Группа, что не сошлась внутри, помечается, но сообщение — наше.
        кривая = gtp2(95, ie2(93, b"\x49\x00\x05\x00"), teid=1)
        п = разобрать_пакет(на_udp(кривая, 2123))
        self.assertTrue(найти(п, "gtpv2.ie_type").плохо)

    def test_gtp_prime(self):
        п = разобрать_пакет(на_udp(DRT, 3386))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "GTP'"])
        self.assertEqual("GTP' Data Record Transfer Request, версия 2, номер 7", п.инфо)
        self.место(п, "gtpprime.seq_number", UDP_НАГРУЗКА + 4, 2)
        self.assertEqual([1], значения(п, "gtpprime.ptc"))
        self.место(п, "gtpprime.ie_type", UDP_НАГРУЗКА + 8, 15, 1)
        длинный = gtpp(2, tv(14, b"\x03"), флаги=0x0E, заголовок20=True)
        п = разобрать_пакет(на_udp(длинный, 3386))
        self.assertEqual([3], значения(п, "gtpprime.recovery"))
        self.место(п, "gtpprime.recovery", UDP_НАГРУЗКА + 21, 1)
        for имя, сообщение in (("PT=1", gtpp(1, b"", флаги=0x5F)), ("запас не 111", gtpp(1, b"", флаги=0x4D)),
                               ("версия 3", gtpp(1, b"", флаги=0x6F)), ("тип незнаком", gtpp(99, b"")),
                               ("20 байт при версии 2", gtpp(1, b"", флаги=0x4E, заголовок20=True)),
                               ("длина не сходится", gtpp(1, tv(14, b"\x01"), длина=3)),
                               ("TLV за концом", gtpp(240, tlv1(252, b"abc")[:-1]))):
            with self.subTest(имя):
                self.assertNotIn("GTP'", разобрать_пакет(на_udp(сообщение, 3386)).стек)


def gtpu(тип, тело=b"", teid=0x01020304, флаги=0x30, опц=b"", длина=None):
    """GTP-U (TS 29.281, 5.1): флаги, тип, длина (после первых 8 байт), TEID, [номер, N-PDU, след.], тело."""
    всё = опц + тело
    return struct.pack(">BBHI", флаги, тип, len(всё) if длина is None else длина, teid) + всё


def расш(содержимое, следующее=0):
    """Заголовок расширения (TS 29.281, 5.2.1): длина в 4 байтах, содержимое, тип следующего."""
    assert (len(содержимое) + 2) % 4 == 0
    return bytes([(len(содержимое) + 2) // 4]) + содержимое + bytes([следующее])


ВНУТРИ = с.ip(с.udp(b"hello", 1111, 2222, src="10.9.9.1", dst="10.9.9.2"), 17, src="10.9.9.1", dst="10.9.9.2")


class GtpUTests(Общее):
    def разбор(self, сообщение):
        return разобрать_пакет(на_udp(сообщение, 2152))

    def test_g_pdu(self):
        п = self.разбор(gtpu(255, ВНУТРИ))
        self.assertEqual(["Ethernet", "IPv4", "UDP", "GTP", "IPv4", "UDP"], п.стек[:6])
        м = UDP_НАГРУЗКА
        self.место(п, "gtp.teid", м + 4, 4)
        self.assertEqual(([255], [0x01020304], [len(ВНУТРИ)]),
                         (значения(п, "gtp.message"), значения(п, "gtp.teid"), значения(п, "gtp.length")))
        self.assertEqual("TEID 0x01020304", п.уровни[3].итог)
        self.assertEqual(8, п.уровни[3].длина)
        self.assertEqual(["10.0.0.1", "10.9.9.1"], значения(п, "ip.src"))
        self.assertEqual([], п.ошибки)
        # Без необязательных полей нет и их отображения.
        for ключ in ("gtp.seq_number", "gtp.npdu_number", "gtp.next", "gtp.ext_hdr"):
            self.assertEqual([], значения(п, ключ), ключ)

    def test_не_gtp_u(self):
        for имя, сообщение in (("PT=0 (GTP')", gtpu(255, ВНУТРИ, флаги=0x20)), ("версия 2", gtpu(255, ВНУТРИ, флаги=0x50)),
                               ("версия 0", gtpu(255, ВНУТРИ, флаги=0x10)), ("7 байт", gtpu(1)[:7])):
            with self.subTest(имя):
                self.assertNotIn("GTP", self.разбор(сообщение).стек)

    def test_служебные_сообщения(self):
        for тип, имя in ((254, "End Marker"), (253, "Tunnel Status"), (1, "Echo Request"), (99, "тип 99")):
            with self.subTest(тип):
                п = self.разбор(gtpu(тип))
                self.assertEqual(["Ethernet", "IPv4", "UDP", "GTP"], п.стек)
                self.assertEqual(f"GTP {имя}, TEID 0x01020304", п.инфо)
                self.assertEqual([], п.ошибки)

    def test_необязательные_поля(self):
        опц = struct.pack(">HBB", 0x1234, 0x56, 0)
        м = UDP_НАГРУЗКА
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x32, опц=опц))        # S: только номер
        self.assertEqual(([0x1234], [], []), (значения(п, "gtp.seq_number"), значения(п, "gtp.npdu_number"),
                                              значения(п, "gtp.next")))
        self.место(п, "gtp.seq_number", м + 8, 2)
        self.assertEqual([0, 1, 0], [значения(п, к)[0] for к in ("gtp.flags.e", "gtp.flags.s", "gtp.flags.pn")])
        self.assertEqual(12, п.уровни[3].длина)
        self.assertIn("IPv4", п.стек[4:])
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x31, опц=опц))        # PN: только N-PDU
        self.assertEqual(([], [0x56]), (значения(п, "gtp.seq_number"), значения(п, "gtp.npdu_number")))
        self.место(п, "gtp.npdu_number", м + 10, 1)
        self.assertEqual([1], значения(п, "gtp.flags.pn"))
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x34, опц=опц))        # E: следующее расширение = 0
        self.assertEqual(([0], []), (значения(п, "gtp.next"), значения(п, "gtp.ext_hdr")))
        self.место(п, "gtp.next", м + 11, 1)
        self.assertEqual([1], значения(п, "gtp.flags.e"))
        self.assertIn("IPv4", п.стек[4:])

    def test_нет_необязательных(self):
        for тип, инфо in ((1, "GTP Echo Request, TEID 0x01020304"), (255, "GTP TEID 0x01020304")):
            with self.subTest(тип):
                п = self.разбор(gtpu(тип, флаги=0x32) + b"\x00\x01\x02")      # длина 0: полей нет
                self.assertEqual(["Ethernet", "IPv4", "UDP", "GTP"], п.стек)
                self.assertEqual(инфо, п.инфо)
                self.assertEqual(["GTP-U: нет необязательных полей заголовка (номер, N-PDU, расширение)"], п.ошибки)
                self.assertEqual(8, п.уровни[3].длина)

    def test_pdu_session_container(self):
        # DL (TS 38.415, 5.5.2.1): PPP=1, RQI=1, QFI 5; октет PPI (3 старших бита) = 5.
        dl = расш(bytes([0x00, 0x80 | 0x40 | 5, 0xA0, 0, 0, 0]))
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x34, опц=b"\0\0\0\x85" + dl))
        м = UDP_НАГРУЗКА + 12
        self.assertEqual("TEID 0x01020304, DL QFI 5", п.уровни[3].итог)
        self.assertEqual(([133], [0], [5], [1], [5]), tuple(значения(п, к) for к in (
            "gtp.ext_hdr", "gtp.ext_hdr.pdu_ses_con.pdu_type", "gtp.ext_hdr.pdu_ses_con.qos_flow_id",
            "gtp.ext_hdr.pdu_ses_con.reflec_qos_ind", "gtp.ext_hdr.pdu_ses_con.paging_policy_ind")))
        self.место(п, "gtp.ext_hdr", м, 8)
        self.место(п, "gtp.ext_hdr.length", м, 1)
        self.assertEqual([2], значения(п, "gtp.ext_hdr.length"))
        self.место(п, "gtp.ext_hdr.pdu_ses_con.qos_flow_id", м + 2, 1)
        self.место(п, "gtp.ext_hdr.pdu_ses_con.paging_policy_ind", м + 3, 1)
        self.assertEqual("Заголовок расширения: PDU Session Container", найти(п, "gtp.ext_hdr").имя)
        self.assertEqual(20, п.уровни[3].длина)
        self.assertIn("IPv4", п.стек[4:])
        # DL без PPP: RQI=0, PPI нет; с PPP, но без октета PPI (длина 1) — PPI тоже нет.
        for содержимое, rqi in ((bytes([0x00, 0x05]), 0), (bytes([0x00, 0x80 | 0x05]), 0)):
            п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x34, опц=b"\0\0\0\x85" + расш(содержимое)))
            self.assertEqual(([rqi], []), (значения(п, "gtp.ext_hdr.pdu_ses_con.reflec_qos_ind"),
                                           значения(п, "gtp.ext_hdr.pdu_ses_con.paging_policy_ind")))
        # UL (5.5.2.2): QFI в 6 младших битах второго октета; RQI и PPI нет.
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x34, опц=b"\0\0\0\x85" + расш(bytes([0x10, 0xC0 | 9]))))
        self.assertEqual("TEID 0x01020304, UL QFI 9", п.уровни[3].итог)
        self.assertEqual(([1], [9], []), (значения(п, "gtp.ext_hdr.pdu_ses_con.pdu_type"),
                                          значения(п, "gtp.ext_hdr.pdu_ses_con.qos_flow_id"),
                                          значения(п, "gtp.ext_hdr.pdu_ses_con.reflec_qos_ind")))
        # Незнакомый вид PDU — числом.
        п = self.разбор(gtpu(1, флаги=0x34, опц=b"\0\0\0\x85" + расш(bytes([0x20, 0x03]))))
        self.assertEqual("GTP Echo Request, TEID 0x01020304, вид 2 QFI 3", п.инфо)
        self.assertEqual("Вид PDU: 2", найти(п, "gtp.ext_hdr.pdu_ses_con.pdu_type").имя)

    def test_цепочка_расширений(self):
        цепочка = (расш(b"\x08\x68", 0x85) + расш(bytes([0x10, 0x07]), 0x20) + расш(bytes([0x2A, 0]), 0x82)
                   + расш(bytes([0x03, 0xFF, 0xFE, 0, 0, 0]), 0xC0) + расш(b"\x12\x34", 0x99) + расш(b"\0\0", 0x03)
                   + расш(b"\x01\x02", 0))
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x34, опц=b"\0\0\0\x40" + цепочка))
        self.assertEqual([0x40, 0x85, 0x20, 0x82, 0xC0, 0x99, 0x03], значения(п, "gtp.ext_hdr"))
        self.assertEqual(([2152], [0x2A], [0x3FFFE]), (значения(п, "gtp.ext_hdr.udp_port"), значения(п, "gtp.ext_hdr.sci"),
                                                       значения(п, "gtp.ext_hdr.pdcp_sn")))
        м = UDP_НАГРУЗКА + 12
        self.место(п, "gtp.ext_hdr.udp_port", м + 1, 2)
        self.место(п, "gtp.ext_hdr.sci", м + 9, 1)
        self.место(п, "gtp.ext_hdr.pdcp_sn", м + 13, 3)
        self.assertEqual(["UDP source port of the triggering message", "PDU Session Container",
                          "Service Class Indicator", "Long PDCP PDU Number", "PDCP PDU Number", "тип 0x99",
                          "Long PDCP PDU Number"], [ф.текст for ф in п.уровни[3].поля if ф.ключ == "gtp.ext_hdr"])
        self.assertEqual("TEID 0x01020304, UL QFI 7", п.уровни[3].итог)
        self.assertEqual(12 + len(цепочка), п.уровни[3].длина)
        self.assertIn("IPv4", п.стек[4:])
        self.assertEqual([], п.ошибки)
        # Long PDCP длиной в одно слово — номера (3 октета) нет.
        п = self.разбор(gtpu(255, ВНУТРИ, флаги=0x34, опц=b"\0\0\0\x82" + расш(b"\x03\xff")))
        self.assertEqual([], значения(п, "gtp.ext_hdr.pdcp_sn"))

    def test_расширение_ошибочное(self):
        for имя, хвост in (("нулевая длина", b"\x00\x00\x00\x00"), ("за концом", b"\x02\x00\x00\x00"),
                           ("нет байт", b"")):
            with self.subTest(имя):
                сообщение = gtpu(255, хвост + ВНУТРИ[:0], флаги=0x34, опц=b"\0\0\0\x85")
                п = self.разбор(сообщение)
                self.assertEqual(["Ethernet", "IPv4", "UDP", "GTP"], п.стек)
                self.assertEqual(["GTP-U: заголовок расширения 0x85 ошибочной длины или обрезан"], п.ошибки)
                self.assertEqual(len(сообщение), п.уровни[3].длина)
                self.assertEqual("GTP TEID 0x01020304", п.инфо)
        # Не G-PDU: IE после ошибки не разбираются.
        п = self.разбор(gtpu(2, b"\x00\x00\x00\x00" + tv(14, b"\x07"), флаги=0x34, опц=b"\0\0\0\x85"))
        self.assertEqual("GTP Echo Response, TEID 0x01020304", п.инфо)
        self.assertEqual([], значения(п, "gtp.ie.type"))

    def test_ie(self):
        п = self.разбор(gtpu(2, tv(14, b"\x07")))
        self.assertEqual("GTP Echo Response, TEID 0x01020304, Recovery 7", п.инфо)
        self.место(п, "gtp.ie.type", UDP_НАГРУЗКА + 8, 2)
        self.assertEqual([14], [ф.сырое for ф in п.уровни[3].поля if ф.ключ == "gtp.ie.type"])
        п = self.разбор(gtpu(26, tv(16, b"\x0a\x0b\x0c\x0d") + tlv1(133, с.a4("192.0.2.7"))))
        self.assertEqual("GTP Error Indication, TEID 0x01020304, TEID Data I 0x0a0b0c0d, "
                         "GTP-U Peer Address 192.0.2.7", п.инфо)
        self.место(п, "gtp.ie.type", UDP_НАГРУЗКА + 13, 7, n=1)
        # Список заголовков расширения (8.5): число типов — один октет.
        п = self.разбор(gtpu(31, bytes([141, 3, 0x85, 0x40, 0x07]) + tlv1(255, b"\x00\x01ab")))
        self.assertEqual("GTP Supported Extension Headers Notification, TEID 0x01020304, Extension Header Type "
                         "List PDU Session Container, UDP source port of the triggering message, 0x07, "
                         "Private Extension 00016162", п.инфо)
        self.место(п, "gtp.ie.type", UDP_НАГРУЗКА + 8, 5)
        п = self.разбор(gtpu(31, bytes([141, 0]) + tlv1(255, b"") + tlv1(200, b"\x01")))
        self.assertEqual("GTP Supported Extension Headers Notification, TEID 0x01020304, "
                         "Extension Header Type List (пусто), Private Extension (пусто), IE 200 01", п.инфо)
        # Незнакомый TV — стоп; обрезанные TV, TLV, список, заголовок TLV.
        п = self.разбор(gtpu(2, tv(14, b"\x07") + tv(5, b"\x01\x02") + tv(14, b"\x08")))
        self.assertEqual("GTP Echo Response, TEID 0x01020304, Recovery 7", п.инфо)
        self.assertEqual([5], значения(п, "gtp.ie.unknown"))
        self.место(п, "gtp.ie.unknown", UDP_НАГРУЗКА + 10, 5)
        for имя, тело, место in (("TV", tv(14, b"\x07") + tv(16, b"\x01\x02"), 2), ("TLV", tlv1(133, b"\x01\x02")[:-1], 0),
                                 ("список", bytes([141, 4, 1, 2]), 0), ("заголовок TLV", tv(14, b"\x07") + b"\x85\x00", 2),
                                 ("список без числа", bytes([141]), 0)):
            with self.subTest(имя):
                п = self.разбор(gtpu(26, тело))
                обрезан = [ф for ф in п.уровни[3].поля if ф.имя.endswith(": обрезан")]
                self.assertEqual(1, len(обрезан), [ф.имя for ф in п.уровни[3].поля])
                self.assertEqual((UDP_НАГРУЗКА + 8 + место, len(тело) - место), (обрезан[0].смещение, обрезан[0].длина))
        # IE — только в пределах объявленной длины.
        п = self.разбор(gtpu(2, tv(14, b"\x07") + b"\xff\xff", длина=2))
        self.assertEqual(("GTP Echo Response, TEID 0x01020304, Recovery 7", []), (п.инфо, значения(п, "gtp.ie.unknown")))
        self.assertEqual(10, п.уровни[3].длина)

    def test_расширения_gtpv1_c(self):
        for вид, имя in ((0xC1, "Suspend Request"), (0x01, "MBMS support indication"),
                         (0x02, "MS Info Change Reporting support indication"), (0xC2, "Suspend Response"),
                         (0x40, "UDP source port of the triggering message"), (0xEE, "тип 0xee")):
            with self.subTest(вид):
                сообщение = gtp1(1, b"", флаги=0x36, расширения=bytes([вид]) + bytes([1, 0x08, 0x68, 0]))
                п = разобрать_пакет(на_udp(сообщение, 2123))
                self.assertEqual(f"Заголовок расширения: {имя}", найти(п, "gtp.ext_hdr").имя)
                self.assertEqual([вид], значения(п, "gtp.ext_hdr"))


class PfcpTests(Общее):
    def test_heartbeat(self):
        п = разобрать_пакет(на_udp(HEARTBEAT, 8805))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "PFCP"])
        self.assertEqual("PFCP Heartbeat Request, номер 2571", п.инфо)
        self.место(п, "pfcp.seqno", UDP_НАГРУЗКА + 4, 3)
        self.место(п, "pfcp.recovery_time_stamp", UDP_НАГРУЗКА + 12, 4)
        self.assertIn("2024-01-01 00:00:00 UTC", найти(п, "pfcp.recovery_time_stamp").текст)

    def test_session_establishment_деревом(self):
        п = разобрать_пакет(на_udp(SESSION_EST, 8805))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "PFCP"])
        self.assertEqual([0], значения(п, "pfcp.seid"))
        self.место(п, "pfcp.seid", UDP_НАГРУЗКА + 4, 8)
        self.assertEqual(["10.10.0.1"], значения(п, "pfcp.node_id"))
        self.assertEqual([0x1122334455667788], значения(п, "pfcp.f_seid"))
        self.assertEqual([0x5555], значения(п, "pfcp.f_teid"))
        self.assertEqual(["Access"], [найти(п, "pfcp.source_interface").текст])
        self.assertEqual(["FORW"], [найти(п, "pfcp.apply_action").текст])
        pdr = найти(п, "pfcp.ie_type", 2)
        self.assertEqual(1, pdr.сырое)
        self.assertEqual([56, 29, 2, 108], [д.сырое for д in pdr.дети if д.ключ == "pfcp.ie_type"])
        self.assertIn("узел 10.10.0.1", п.инфо)

    def test_проверки(self):
        ies = ie_pfcp(96, bytes(4))
        for имя, сообщение in (("версия 2", pfcp(1, ies, флаги=0x40)), ("запасной бит", pfcp(1, ies, флаги=0x28)),
                               ("Heartbeat с SEID", pfcp(1, ies, seid=1)), ("сеанс без SEID", pfcp(50, ies)),
                               ("длина короче", pfcp(1, ies, длина=6)), ("длина меньше заголовка", pfcp(1, b"", длина=2)),
                               ("FO, а за ним мусор", pfcp(1, ies, флаги=0x24) + b"\x00" * 7),
                               ("хвост без FO", pfcp(1, ies) + b"\x00"),
                               ("вендорский IE короче Enterprise ID", pfcp(1, ie_pfcp(0x8001, b"\x01"))),
                               ("оборван, тип незнаком", pfcp(99, ies, длина=40))):
            with self.subTest(имя):
                self.assertNotIn("PFCP", разобрать_пакет(на_udp(сообщение, 8805)).стек)
        п = оборвать(на_udp(SESSION_EST, 8805), UDP_НАГРУЗКА + 50)
        self.assertEqual("PFCP", п.протокол)
        self.assertIn("[оборван]", п.инфо)
        self.assertNotIn("PFCP", разобрать_пакет(на_udp(SESSION_EST[:50], 8805)).стек)
        self.assertNotIn("PFCP", оборвать(на_udp(bytes([0x21, 99]) + SESSION_EST[2:], 8805), UDP_НАГРУЗКА + 50).стек)
        # Вендорский IE (тип ≥ 32768) короче своего Enterprise ID — даже если за ним есть байты.
        self.assertNotIn("PFCP", разобрать_пакет(на_udp(pfcp(1, ie_pfcp(0x8001, b"\x01") + ies), 8805)).стек)
        п = разобрать_пакет(на_udp(pfcp(1, ies, флаги=0x24) + HEARTBEAT, 8805))
        self.assertEqual(["PFCP", "PFCP"], п.стек[3:])
        п = разобрать_пакет(на_udp(pfcp(1, ie_pfcp(0x8001, b"\x00\x05ab")), 8805))
        self.assertEqual([5], значения(п, "pfcp.enterprise_id"))


class AperTests(Общее):
    def test_s1ap_s1setup(self):
        п = разобрать_пакет(на_sctp(S1_SETUP, 18))
        self.чисто(п, ["Ethernet", "IPv4", "SCTP", "S1AP"])
        м = SCTP_НАГРУЗКА
        self.assertEqual("S1AP S1Setup (initiatingMessage)", п.инфо)
        self.assertEqual(([0], [17], [0]), (значения(п, "s1ap.pdu_type"), значения(п, "s1ap.procedurecode"),
                                           значения(п, "s1ap.criticality")))
        self.место(п, "s1ap.procedurecode", м + 1, 1)
        self.место(п, "s1ap.value_length", м + 3, 1)
        self.assertEqual([4], значения(п, "s1ap.protocolies"))
        self.assertEqual([59, 60, 64, 137], значения(п, "s1ap.id"))
        self.место(п, "s1ap.id", м + 7, 3 + 1 + 7)
        # По порту 36412 без PPID — тоже S1AP.
        self.assertEqual("S1AP", разобрать_пакет(на_sctp(S1_SETUP, 0)).протокол)

    def test_ngap_длинная_длина_и_x2ap(self):
        п = разобрать_пакет(на_sctp(NG_SETUP_OK, 60, 38412, 38412))
        self.чисто(п, ["Ethernet", "IPv4", "SCTP", "NGAP"])
        self.assertEqual("NGAP NGSetup (successfulOutcome)", п.инфо)
        self.место(п, "ngap.value_length", SCTP_НАГРУЗКА + 3, 2)
        self.assertEqual([96, 86], значения(п, "ngap.id"))
        п = разобрать_пакет(на_sctp(X2_SETUP, 27, 36422, 36422))
        self.assertEqual("X2AP x2Setup (initiatingMessage)", п.инфо)
        self.assertEqual([6], значения(п, "x2ap.procedurecode"))

    def test_проверки_и_обрыв(self):
        for имя, pdu in (("индекс 3", bytes([0x60]) + S1_SETUP[1:]), ("бит расширения", bytes([0x80]) + S1_SETUP[1:]),
                         ("важность 3", S1_SETUP[:2] + b"\xc0" + S1_SETUP[3:]),
                         ("биты выравнивания", S1_SETUP[:2] + b"\x01" + S1_SETUP[3:]),
                         ("байт выравнивания CHOICE", bytes([0x01]) + S1_SETUP[1:]),
                         ("длина короче куска", S1_SETUP + b"\x00"),
                         ("длина длиннее куска", S1_SETUP[:-1]),
                         ("фрагментированная длина", S1_SETUP[:3] + b"\xc1" + S1_SETUP[4:])):
            with self.subTest(имя):
                self.assertNotIn("S1AP", разобрать_пакет(на_sctp(pdu, 18)).стек)
        # Захват оборвал кусок DATA: длина PDU сходится с объявленной длиной куска — наше, оборвано.
        целый = на_sctp(S1_SETUP, 18)
        п = разобрать_пакет(целый[:SCTP_НАГРУЗКА + 12])
        self.assertEqual("S1AP", п.протокол)
        self.assertIn("[оборван]", п.инфо)
        # Незнакомая процедура в оборванном — не признаём; в целом — числом.
        чужая = aper_pdu(0, 200, 0, [(1, 0, b"\x00")])
        self.assertNotIn("S1AP", разобрать_пакет(на_sctp(чужая, 18)[:SCTP_НАГРУЗКА + 5]).стек)
        self.assertIn("процедура 200", разобрать_пакет(на_sctp(чужая, 18)).инфо)
        # Значение не SEQUENCE {protocolIEs} — верхний уровень есть, IE не выдумываются.
        странный = bytes([0, 17, 0, 3]) + b"\x80\x01\x02"
        п = разобрать_пакет(на_sctp(странный, 18))
        self.assertEqual("S1AP", п.протокол)
        self.assertEqual([], значения(п, "s1ap.id"))
        ie = struct.pack(">HB", 59, 0) + b"\x01\xaa"
        for имя, значение in (("бит расширения в преамбуле", b"\x80\x00\x01" + ie),
                              ("лишний байт после IE", b"\x00\x00\x01" + ie + b"\x00"),
                              ("criticality 3", b"\x00\x00\x01" + ie[:2] + b"\xc0" + ie[3:]),
                              ("биты выравнивания criticality", b"\x00\x00\x01" + ie[:2] + b"\x01" + ie[3:])):
            with self.subTest(имя):
                п = разобрать_пакет(на_sctp(bytes([0, 17, 0, len(значение)]) + значение, 18))
                self.assertEqual("S1AP", п.протокол)
                self.assertEqual([], значения(п, "s1ap.id"))
        # Длина с признаком фрагмента (11xxxxxx) — X.691 10.9.3.8: фрагменты не разбираем.
        self.assertNotIn("S1AP", разобрать_пакет(на_sctp(bytes([0, 17, 0, 0xC0, 3]) + b"\x00\x00\x00", 18)).стек)


class SgsapTests(Общее):
    def test_location_update_request(self):
        п = разобрать_пакет(на_sctp(LU_REQUEST, 0, 29118, 29118))
        self.чисто(п, ["Ethernet", "IPv4", "SCTP", "SGsAP"])
        м = SCTP_НАГРУЗКА
        self.assertEqual(f"SGsAP SGsAP-LOCATION-UPDATE-REQUEST, IMSI {IMSI}, MME name {MME_ИМЯ}", п.инфо)
        self.assertEqual([9], значения(п, "sgsap.msg_type"))
        self.место(п, "sgsap.imsi", м + 3, 8)
        self.assertEqual([IMSI], значения(п, "sgsap.imsi"))
        self.assertEqual([MME_ИМЯ], значения(п, "sgsap.mme_name"))
        self.assertEqual([1], значения(п, "sgsap.eps_location_update_type"))
        self.assertEqual([1], значения(п, "sgsap.lai"))
        self.место(п, "sgsap.lai", len(LU_REQUEST) - 5 + м, 5)

    def test_reset_и_чётный_imsi(self):
        п = разобрать_пакет(на_sctp(RESET_IND, 0, 29118, 29118))
        self.assertEqual(f"SGsAP SGsAP-RESET-INDICATION, MME name {MME_ИМЯ}", п.инфо)
        чётный = bytes([0x0C]) + sg_ie(0x01, imsi_личность("25001123456789"))
        self.assertEqual(["25001123456789"], значения(разобрать_пакет(на_sctp(чётный, 0, 29118, 29118)), "sgsap.imsi"))

    def test_проверки(self):
        imsi = sg_ie(0x01, imsi_личность(IMSI))
        for имя, сообщение in (("тип незнаком", bytes([0x03]) + imsi), ("первым не IMSI", bytes([0x0C]) + sg_ie(0x03, b"\x29\x02\x03\x04")),
                               ("IMSI не того типа", bytes([0x0C]) + sg_ie(0x01, b"\x2c" + bytes(7))),
                               ("IMSI короче 4", bytes([0x0C]) + sg_ie(0x01, b"\x29\x00\x00")),
                               ("RESET без имени", bytes([0x15]) + imsi),
                               ("IE за концом", LU_REQUEST[:-1]), ("лишний байт", LU_REQUEST + b"\x00")):
            with self.subTest(имя):
                self.assertNotIn("SGsAP", разобрать_пакет(на_sctp(сообщение, 0, 29118, 29118)).стек)
        п = разобрать_пакет(на_sctp(LU_REQUEST, 0, 29118, 29118)[:SCTP_НАГРУЗКА + 15])
        self.assertEqual("SGsAP", п.протокол)
        self.assertIn("[оборван]", п.инфо)


class GsmtapTests(Общее):
    def test_um_bcch(self):
        п = разобрать_пакет(на_udp(gsmtap(), 4729))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "GSMTAP", "Данные"])
        м = UDP_НАГРУЗКА
        self.assertEqual("GSMTAP GSM Um, BCCH, ARFCN 62 (вверх), -70 дБм, кадр 123456", п.инфо)
        self.assertEqual("GSMTAP", п.протокол)
        self.assertEqual(([62], [1], [-70], [10]), (значения(п, "gsmtap.arfcn"), значения(п, "gsmtap.uplink"),
                                                    значения(п, "gsmtap.signal_dbm"), значения(п, "gsmtap.snr_db")))
        self.место(п, "gsmtap.frame_nr", м + 8, 4)
        self.место(п, "gsmtap.sub_type", м + 12, 1)
        self.assertEqual(м + 16, п.уровни[-1].смещение)
        self.assertEqual(23, п.уровни[-1].длина)

    def test_проверки(self):
        for имя, пакет in (("версия 1", gsmtap(версия=1)), ("версия 3", gsmtap(версия=3)),
                           ("длина заголовка 5", gsmtap(длина=5)), ("длина заголовка 3", gsmtap(длина=3)),
                           ("тип 0", gsmtap(тип=0)), ("тип 0x14", gsmtap(тип=0x14)),
                           ("короче заголовка", gsmtap()[:15]),
                           ("резерв не 0", gsmtap()[:15] + b"\x01" + gsmtap()[16:])):
            with self.subTest(имя):
                self.assertNotIn("GSMTAP", разобрать_пакет(на_udp(пакет, 4729)).стек)
        self.assertEqual("GSMTAP", разобрать_пакет(на_udp(gsmtap(тип=0x13, нагрузка=b""), 4729)).протокол)


class TacacsTests(Общее):
    def test_start_без_шифрования(self):
        сообщение = tacacs(1, 1, tac_start())
        п = разобрать_пакет(на_tcp(сообщение, 49))
        self.чисто(п, ["Ethernet", "IPv4", "TCP", "TACACS+"])
        м = TCP_НАГРУЗКА
        self.assertEqual("TACACS+ Authentication START LOGIN, «alice»", п.инфо)
        self.assertEqual([1], значения(п, "tacplus.flags.unencrypted"))
        self.место(п, "tacplus.session_id", м + 4, 4)
        self.место(п, "tacplus.packet_len", м + 8, 4)
        self.место(п, "tacplus.user", м + 20, 5)
        self.assertEqual(["alice"], значения(п, "tacplus.user"))
        self.assertEqual(["tty0"], значения(п, "tacplus.port"))
        self.assertEqual(["10.0.0.5"], значения(п, "tacplus.remote_address"))
        self.assertEqual([2], значения(п, "tacplus.authen.type"))

    def test_зашифровано_и_учёт(self):
        п = разобрать_пакет(на_tcp(tacacs(2, 2, bytes(range(20)), флаги=0), 49))
        self.assertEqual("TACACS+ Authorization ответ, seq 2, зашифровано", п.инфо)
        self.место(п, "tacplus.body", TCP_НАГРУЗКА + 12, 20)
        п = разобрать_пакет(на_tcp(TAC_ACCT, 49))
        self.assertEqual("TACACS+ Accounting REQUEST, «bob», service=shell task_id=7", п.инфо)
        self.assertEqual(["service=shell", "task_id=7"], значения(п, "tacplus.arg"))
        ответ = tacacs(1, 2, struct.pack(">BBHH", 1, 0, 2, 0) + b"ok")
        self.assertEqual("TACACS+ Authentication REPLY PASS", разобрать_пакет(на_tcp(ответ, 49)).инфо)
        # Длины полей не сходятся с длиной тела — тело помечено, заголовок остаётся.
        кривой = tacacs(1, 1, tac_start()[:-1])
        п = разобрать_пакет(на_tcp(кривой, 49))
        self.assertTrue(найти(п, "tacplus.body").плохо)

    def test_проверки(self):
        тело = tac_start()
        for имя, сообщение in (("major 0xB", tacacs(1, 1, тело, версия=0xB0)), ("minor 2", tacacs(1, 1, тело, версия=0xC2)),
                               ("тип 4", tacacs(4, 1, тело)), ("тип 0", tacacs(0, 1, тело)), ("seq 0", tacacs(1, 0, тело)),
                               ("оборван с длиной больше 2^16", tacacs(1, 1, тело, длина=0x10001))):
            with self.subTest(имя):
                self.assertNotIn("TACACS+", разобрать_пакет(на_tcp(сообщение, 49)).стек)
        п = разобрать_пакет(на_tcp(tacacs(1, 1, тело, длина=0x10000), 49))
        self.assertEqual("TACACS+", п.протокол)
        self.assertIn("[оборван]", п.инфо)


class KerberosTests(Общее):
    def test_as_req_udp(self):
        сообщение = as_req()
        п = разобрать_пакет(на_udp(сообщение, 88))
        self.чисто(п, ["Ethernet", "IPv4", "UDP", "Kerberos"])
        self.assertEqual("Kerberos AS-REQ, клиент alice@EXAMPLE.COM, служба krbtgt/EXAMPLE.COM, "
                         "шифры aes256-cts-hmac-sha1-96, aes128-cts-hmac-sha1-96, rc4-hmac", п.инфо)
        self.assertEqual([10], значения(п, "kerberos.msg_type"))
        self.assertEqual([5], значения(п, "kerberos.pvno"))
        сдвиг = сообщение.index(b"\xa2\x03\x02\x01\x0a")
        self.место(п, "kerberos.msg_type", UDP_НАГРУЗКА + сдвиг, 5)
        self.assertEqual(["EXAMPLE.COM"], значения(п, "kerberos.realm"))
        self.assertEqual(["alice"], значения(п, "kerberos.cnamestring"))
        self.assertEqual([18, 17, 23], значения(п, "kerberos.etype"))
        self.assertEqual([128], значения(п, "kerberos.padata_type"))
        self.assertEqual([123456789], значения(п, "kerberos.nonce"))

    def test_as_rep_и_ошибка_на_tcp(self):
        п = разобрать_пакет(на_udp(AS_REP, 88))
        self.assertEqual("Kerberos AS-REP, клиент alice@EXAMPLE.COM", п.инфо)
        self.assertEqual(["krbtgt/EXAMPLE.COM"], значения(п, "kerberos.snamestring"))
        self.assertEqual([18, 18], значения(п, "kerberos.etype"))
        self.assertEqual([2], значения(п, "kerberos.kvno"))
        п = разобрать_пакет(на_tcp(на_tcp_krb(KRB_ERROR), 88))
        self.чисто(п, ["Ethernet", "IPv4", "TCP", "Kerberos"])
        self.assertEqual("Kerberos KRB-ERROR 25 (KDC_ERR_PREAUTH_REQUIRED), служба krbtgt/EXAMPLE.COM", п.инфо)
        self.место(п, "kerberos.rm.length", TCP_НАГРУЗКА, 4)
        self.assertEqual([len(KRB_ERROR)], значения(п, "kerberos.rm.length"))
        self.assertEqual([25], значения(п, "kerberos.error_code"))

    def test_проверки(self):
        for имя, сообщение in (("pvno 4", as_req(pvno=4)), ("msg-type не по тегу", as_req(тип=12)),
                               ("тег не Kerberos", as_req(тег=0x70)), ("универсальный тег", as_req(тег=0x30)),
                               ("контекстный [10]", as_req(тег=0xAA)), ("универсальный номер 10", as_req(тег=0x2A)),
                               ("[APPLICATION 16] со своими pvno и msg-type",
                                ber(0x70, ber(0x30, кт(0, целое(5)) + кт(1, целое(16))))),
                               ("хвост за сообщением", as_req() + b"\x00"),
                               ("pvno не [1] в KDC-REQ", ber(0x6A, ber(0x30, кт(0, целое(5)) + кт(1, целое(10))))),
                               ("не SEQUENCE внутри", ber(0x6A, ber(0x31, кт(1, целое(5)) + кт(2, целое(10))))),
                               ("неопределённая длина", b"\x6a\x80" + as_req()[2:])):
            with self.subTest(имя):
                self.assertNotIn("Kerberos", разобрать_пакет(на_udp(сообщение, 88)).стек)
        for имя, запись in (("старший бит длины", struct.pack(">I", 0x80000000 | len(KRB_ERROR)) + KRB_ERROR),
                            ("длина записи короче", struct.pack(">I", len(KRB_ERROR) - 1) + KRB_ERROR),
                            ("длина записи длиннее", struct.pack(">I", len(KRB_ERROR) + 1) + KRB_ERROR),
                            ("хвост за записью", на_tcp_krb(KRB_ERROR) + b"\x00")):
            with self.subTest(имя):
                self.assertNotIn("Kerberos", разобрать_пакет(на_tcp(запись, 88)).стек)
        п = оборвать(на_udp(as_req(), 88), UDP_НАГРУЗКА + 60)
        self.assertEqual("Kerberos", п.протокол)
        self.assertIn("[оборван]", п.инфо)
        self.assertNotIn("Kerberos", разобрать_пакет(на_udp(as_req()[:60], 88)).стек)


class LdapTests(Общее):
    def test_bind_и_search(self):
        п = разобрать_пакет(на_tcp(BIND, 389))
        self.чисто(п, ["Ethernet", "IPv4", "TCP", "LDAP"])
        self.assertEqual("LDAP bindRequest(1), «cn=admin,dc=example,dc=org», простая", п.инфо)
        self.место(п, "ldap.messageid", TCP_НАГРУЗКА + 2, 3)
        self.assertEqual([1], значения(п, "ldap.messageid"))
        self.assertEqual([3], значения(п, "ldap.version"))
        self.место(п, "ldap.name", TCP_НАГРУЗКА + 10, 2 + len(DN))
        self.assertNotIn("secret", str(п.поля_фильтра()))
        п = разобрать_пакет(на_tcp(SEARCH, 389))
        self.assertEqual("LDAP searchRequest(2), «dc=example,dc=org», wholeSubtree, (&(objectClass=*)(uid=alice))",
                         п.инфо)
        self.assertEqual(["dc=example,dc=org"], значения(п, "ldap.baseobject"))
        self.assertEqual([2], значения(п, "ldap.scope"))
        self.assertEqual(["cn, mail"], значения(п, "ldap.attributes"))

    def test_ответы_подряд_и_cldap(self):
        п = разобрать_пакет(на_tcp(RES_ENTRY + RES_DONE, 389, 40000))
        self.assertEqual(["LDAP", "LDAP"], п.стек[3:])
        self.assertEqual("LDAP searchResEntry(2), «uid=alice,dc=example,dc=org»; searchResDone(2) success", п.инфо)
        self.assertEqual(TCP_НАГРУЗКА + len(RES_ENTRY), п.уровни[4].смещение)
        self.assertEqual([0], значения(разобрать_пакет(на_tcp(BIND_OK, 389)), "ldap.resultcode"))
        # Второе сообщение ломается уже после начала разбора — его след убирается, остаток — данные.
        кривое = ldap(3, ber(0x64, b"\x04\x10abc"))
        п = разобрать_пакет(на_tcp(RES_ENTRY + кривое, 389, 40000))
        self.assertEqual(["LDAP", "Данные"], п.стек[3:])
        self.assertEqual("LDAP", разобрать_пакет(на_udp(SEARCH, 389)).протокол)

    def test_проверки(self):
        for имя, сообщение in (
                ("отрицательный messageID", ber(0x30, ber(0x02, b"\x80") + BIND[5:])),
                ("messageID длиннее 4 байт", ber(0x30, ber(0x02, b"\x00\x00\x00\x00\x01") + BIND[5:])),
                ("незнакомая операция", ldap(1, ber(0x71, b""))),
                ("версия bind 0", ldap(1, ber(0x60, целое(0) + ber(0x04, DN) + ber(0x80, b"")))),
                ("scope 3", SEARCH.replace(b"\x0a\x01\x02", b"\x0a\x01\x03")),
                ("лишнее после операции", ber(0x30, BIND[2:] + ber(0x04, b""))),
                ("unbind не NULL", ldap(1, ber(0x42, b"\x00"))),
                ("операция длиннее сообщения", ber(0x30, целое(1) + b"\x60\x7f" + BIND[7:])),
                ("операция заходит в следующее", ber(0x30, целое(1) + b"\x4a\x04") + b"abcd"),
                ("не SEQUENCE", ber(0x31, BIND[2:]))):
            with self.subTest(имя):
                self.assertNotIn("LDAP", разобрать_пакет(на_tcp(сообщение, 389)).стек)
        self.assertEqual("LDAP unbindRequest(3)", разобрать_пакет(на_tcp(ldap(3, ber(0x42, b"")), 389)).инфо)
        управление = ber(0x30, BIND[2:] + ber(0xA0, ber(0x30, ber(0x04, b"1.2.3"))))
        self.assertEqual("LDAP", разобрать_пакет(на_tcp(управление, 389)).протокол)
        п = разобрать_пакет(на_tcp(SEARCH[:30], 389))
        self.assertEqual("LDAP", п.протокол)
        self.assertIn("[оборван]", п.инфо)


ОБРАЗЦЫ = {
    "Diameter/TCP": на_tcp(CER, 3868), "Diameter/SCTP": на_sctp(CCA, 46, 3868, 3868),
    "GTPv1-C": на_udp(CREATE_PDP, 2123), "GTPv2-C": на_udp(CREATE_SESSION, 2123), "GTP'": на_udp(DRT, 3386),
    "PFCP": на_udp(SESSION_EST, 8805), "S1AP": на_sctp(S1_SETUP, 18), "NGAP": на_sctp(NG_SETUP_OK, 60),
    "X2AP": на_sctp(X2_SETUP, 27), "SGsAP": на_sctp(LU_REQUEST, 0, 29118, 29118), "GSMTAP": на_udp(gsmtap(), 4729),
    "TACACS+": на_tcp(tacacs(1, 1, tac_start()), 49), "Kerberos/UDP": на_udp(AS_REP, 88),
    "Kerberos/TCP": на_tcp(на_tcp_krb(KRB_ERROR), 88), "LDAP": на_tcp(SEARCH, 389),
}


class ОбщиеTests(unittest.TestCase):
    def test_обрыв_на_каждом_байте_не_роняет(self):
        # С начала своей нагрузки: обрыв внутри заголовка куска DATA SCTP роняет разбор
        # самого SCTP (razbor.sctp) — это вне этого модуля.
        for имя, пакет in ОБРАЗЦЫ.items():
            начало = SCTP_НАГРУЗКА if пакет[23] == 132 else 14
            for длина in range(начало, len(пакет)):
                with self.subTest(имя, длина=длина):
                    п = разобрать_пакет(пакет[:длина])
                    self.assertFalse([о for о in п.ошибки if "разбор прерван" in о], п.ошибки)
                    self.assertTrue(п.уровни)

    def test_фильтр_по_имени_и_полю(self):
        имена = list(ОБРАЗЦЫ)
        поля = [разобрать_пакет(ОБРАЗЦЫ[и], номер=i + 1).поля_фильтра() for i, и in enumerate(имена)]

        def выбрать(текст):
            return [имена[i] for i in отобрать(поля, текст)]
        for текст, ждём in (
                ("diameter", ["Diameter/TCP", "Diameter/SCTP"]), ("diameter.cmd.code == 272", ["Diameter/SCTP"]),
                ("diameter.result-code == 2001", ["Diameter/SCTP"]),
                ("diameter.origin-host contains \"mme\"", ["Diameter/TCP"]),
                ("gtp", ["GTPv1-C"]), ("gtp.imsi == 250011234567890", ["GTPv1-C"]),
                ("gtpv2", ["GTPv2-C"]), ("gtpv2.message_type == 32", ["GTPv2-C"]),
                ("gtpprime", ["GTP'"]), ("gtpprime.message == 240", ["GTP'"]),
                ("pfcp", ["PFCP"]), ("pfcp.msg_type == 50", ["PFCP"]),
                ("s1ap", ["S1AP"]), ("s1ap.procedurecode == 17", ["S1AP"]),
                ("ngap.procedurecode == 21", ["NGAP"]), ("x2ap", ["X2AP"]),
                ("sgsap", ["SGsAP"]), ("sgsap.imsi == 250011234567890", ["SGsAP"]),
                ("gsmtap", ["GSMTAP"]), ("gsmtap.arfcn == 62", ["GSMTAP"]),
                ("tacplus", ["TACACS+"]), ("tacplus.user == alice", ["TACACS+"]),
                ("kerberos", ["Kerberos/UDP", "Kerberos/TCP"]), ("kerberos.error_code == 25", ["Kerberos/TCP"]),
                ("ldap", ["LDAP"]), ("ldap.scope == 2", ["LDAP"]),
                ("sctp and not (s1ap or ngap or x2ap)", ["Diameter/SCTP", "SGsAP"])):
            with self.subTest(текст):
                self.assertEqual(ждём, выбрать(текст))

    def test_ключи_в_нижнем_регистре_и_уровни(self):
        """Фильтр приводит имя поля к нижнему регистру — ключи с заглавными не нашлись бы."""
        for имя, пакет in ОБРАЗЦЫ.items():
            п = разобрать_пакет(пакет)
            for ключ in п.поля_фильтра():
                self.assertEqual(ключ.lower(), ключ, (имя, ключ))
        for протокол in СВОИ:
            self.assertEqual("прикладной", уровень_протокола(протокол))

    def test_разбирать_как(self):
        п = разобрать_пакет(на_udp(CREATE_SESSION, 5000), как={"udp:5000": "GTP-C"})
        self.assertEqual("GTPv2", п.протокол)
        п = разобрать_пакет(на_tcp(BIND, 5000), как={"tcp:5000": "LDAP"})
        self.assertEqual("LDAP", п.протокол)
        п = разобрать_пакет(на_udp(b"\x00" * 30, 5000), как={"udp:5000": "PFCP"})
        self.assertIn("разбирать как PFCP (порт 5000): данные не подошли", п.ошибки)

    def test_случайные_данные_не_признаются(self):
        """По 2000 случайных нагрузок на порт/PPID каждого протокола и на порт без службы
        (эвристика Diameter) — ни одна не признана своей. Второй проход — случайные данные с
        «правильным» первым байтом (версия/тег) — ловит ослабление глубинных проверок."""
        сл = random.Random(20260926)
        случаи = [("tcp", 3868, None), ("udp", 2123, None), ("udp", 3386, None), ("udp", 8805, None),
                  ("udp", 4729, None), ("tcp", 49, None), ("tcp", 88, None), ("udp", 88, None), ("tcp", 389, None),
                  ("udp", 389, None), ("tcp", 5555, None), ("sctp", 46, 1), ("sctp", 18, 1), ("sctp", 60, 1),
                  ("sctp", 27, 1), ("sctp", 0, 29118), ("sctp", 0, 3868), ("sctp", 0, 36412)]
        первые = {3868: [1], 2123: [0x32, 0x48, 0x40], 3386: [0x4F, 0x0E], 8805: [0x20, 0x21], 4729: [2],
                  49: [0xC0, 0xC1], 88: [0x6A, 0x6B, 0x7E, 0], 389: [0x30], 5555: [1], 46: [1], 18: [0, 0x20],
                  60: [0x40], 27: [0], 29118: [0x09, 0x15], 36412: [0, 0x20, 0x40]}
        for транспорт, номер, порт in случаи:
            признано = []
            for проход in range(2):
                for _ in range(2000):
                    нагрузка = bytearray(сл.randbytes(сл.randint(1, 300)))
                    if проход:
                        нагрузка[0] = сл.choice(первые[номер if порт in (None, 1) else порт])
                    if транспорт == "tcp":
                        пакет = на_tcp(bytes(нагрузка), номер)
                    elif транспорт == "udp":
                        пакет = на_udp(bytes(нагрузка), номер)
                    else:
                        пакет = на_sctp(bytes(нагрузка), номер, порт, порт)
                    п = разобрать_пакет(пакет)
                    if СВОИ & set(п.стек):
                        признано.append((проход, п.стек[-1], bytes(нагрузка[:24]).hex()))
            with self.subTest(транспорт=транспорт, номер=номер, порт=порт):
                self.assertEqual([], признано)


if __name__ == "__main__":
    unittest.main()
