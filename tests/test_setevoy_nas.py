"""NAS LTE (TS 24.301) и 5G (TS 24.501) внутри S1AP (IE 26) и NGAP (IE 38): сообщения собираются по таблицам
8.2/8.3 стандартов; идентификаторы — BCD по 24.008 10.5.1.4, 24.301 9.9.3.12 и 24.501 9.11.3.4 (кодирует
вспомогательная функция здесь же, независимо от разбора)."""

import struct
import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy.protokoly import mobilnye, nas
from reportgen.setevoy.razbor import ДОП_УРОВНИ, разобрать_пакет
from test_setevoy_mobilnye import aper_pdu, aper_длина, на_sctp


def bcd(цифры):
    """Пары цифр: младший полубайт — первая, F — заполнитель."""
    цифры = цифры + ("F" if len(цифры) % 2 else "")
    return bytes(int(цифры[i + 1], 16) << 4 | int(цифры[i], 16) for i in range(0, len(цифры), 2))


def личность(цифры, вид):
    """Mobile identity 24.008 10.5.1.4: первая цифра — старший полубайт первого октета, бит 4 — нечётность."""
    return bytes([int(цифры[0]) << 4 | (8 if len(цифры) % 2 else 0) | вид]) + bcd(цифры[1:])


PLMN_25001 = bytes([0x52, 0xF0, 0x10])
IMSI = "250011234567890"


def lv(б):
    return bytes([len(б)]) + б


def lve(б):
    return struct.pack(">H", len(б)) + б


def apn(текст):
    return b"".join(bytes([len(ч)]) + ч.encode() for ч in текст.split("."))


def s1ap(nas_pdu, процедура=12):
    """initialUEMessage (процедура 12) с eNB-UE-S1AP-ID (8) и NAS-PDU (26)."""
    return на_sctp(aper_pdu(0, процедура, 1, [(8, 0, b"\x00\x01"), (26, 0, aper_длина(len(nas_pdu)) + nas_pdu)]), 18)


def ngap(nas_pdu, процедура=15):
    """InitialUEMessage NGAP (процедура 15) с RAN-UE-NGAP-ID (85) и NAS-PDU (38)."""
    return на_sctp(aper_pdu(0, процедура, 1, [(85, 0, b"\x00\x01"), (38, 0, aper_длина(len(nas_pdu)) + nas_pdu)]),
                   60, 38412, 38412)


def поля(п, протокол):
    у = [x for x in п.уровни if x.протокол == протокол][0]
    return {x.ключ: x for x in у.поля}


def все(п, ключ):
    return [x for у in п.уровни for x in у.поля if x.ключ == ключ]


PDN_CONN = bytes([0x02, 0x01, 0xD0, 0x11]) + b"\x28" + lv(apn("internet"))
ATTACH_REQ = (b"\x07\x41\x72" + lv(личность(IMSI, 1)) + lv(b"\xe0\xe0") + lve(PDN_CONN))
GUTI = b"\xf6" + PLMN_25001 + struct.pack(">HBI", 0x8001, 0x0A, 0xC0DE1234)
DEF_BEARER = (bytes([0x52, 0x01, 0xC1]) + lv(b"\x09") + lv(apn("internet")) + lv(b"\x01" + bytes([10, 45, 0, 2])))
ATTACH_ACC = (b"\x07\x42\x02\x21" + lv(b"\x00" + PLMN_25001 + b"\x00\x01") + lve(DEF_BEARER) + b"\x50" + lv(GUTI))


class ТестEPS(unittest.TestCase):
    def test_attach_request(self):
        п = разобрать_пакет(s1ap(ATTACH_REQ))
        self.assertEqual([у.протокол for у in п.уровни][-2:], ["S1AP", "NAS-EPS"])
        ф = поля(п, "NAS-EPS")
        self.assertEqual(ф["nas_eps.nas_msg_emm_type"].сырое, 0x41)
        self.assertEqual(ф["nas_eps.emm.eps_att_type"].текст, "Combined EPS/IMSI attach")
        self.assertEqual(ф["nas_eps.emm.nas_key_set_id"].сырое, 7)
        self.assertEqual(ф["e212.imsi"].текст, IMSI)
        self.assertEqual(ф["nas_eps.mobile_identity"].текст, f"IMSI {IMSI}")
        self.assertEqual(ф["nas_eps.nas_msg_esm_type"].сырое, 0xD0)
        self.assertEqual(ф["nas_eps.esm.apn"].текст, "internet")
        итог = f"Attach request, IMSI {IMSI}, PDN connectivity request, APN internet"
        self.assertEqual(п.уровни[-1].итог, итог)
        self.assertEqual(п.инфо, f"S1AP initialUEMessage (initiatingMessage); NAS: {итог}")
        # Место NAS — сразу за определителем длины значения IE.
        у = п.уровни[-1]
        self.assertEqual(п.данные[у.смещение:у.смещение + len(ATTACH_REQ)], ATTACH_REQ)
        self.assertEqual(у.длина, len(ATTACH_REQ))
        self.assertEqual(ф["e212.imsi"].смещение, у.смещение + 4)
        self.assertFalse(п.ошибки)

    def test_attach_accept_нулевой_шифр_и_шифр(self):
        защищённое = b"\x27" + b"\x11\x22\x33\x44" + b"\x05" + ATTACH_ACC
        п = разобрать_пакет(s1ap(защищённое, 9))
        ф = поля(п, "NAS-EPS")
        self.assertEqual(ф["nas_eps.security_header_type"].текст, "целостность и шифр")
        self.assertEqual((ф["nas_eps.msg_auth_code"].сырое, ф["nas_eps.seq_no"].сырое), (0x11223344, 5))
        self.assertEqual(ф["nas_eps.esm.pdn_addr"].текст, "10.45.0.2")
        гuti = [x.текст for x in все(п, "nas_eps.mobile_identity")]
        self.assertEqual(гuti, ["GUTI 250-01, MMEGI 32769, MMEC 10, M-TMSI 0xC0DE1234"])
        self.assertEqual(п.уровни[-1].итог, "нулевой шифр, Attach accept, Activate default EPS bearer context request, "
                                            "APN internet, адрес 10.45.0.2, "
                                            "GUTI 250-01, MMEGI 32769, MMEC 10, M-TMSI 0xC0DE1234")
        п = разобрать_пакет(s1ap(b"\x27" + bytes(5) + b"\x9a\x31\x77" + bytes(20), 9))
        ф = поля(п, "NAS-EPS")
        self.assertEqual(ф["nas_eps.ciphered_msg"].текст, "23 байт")
        self.assertEqual(п.уровни[-1].итог, "зашифровано")
        п = разобрать_пакет(s1ap(b"\x47" + bytes(5) + ATTACH_ACC, 9))
        self.assertTrue(п.уровни[-1].итог.startswith("нулевой шифр, Attach accept"))
        п = разобрать_пакет(s1ap(b"\x57" + bytes(5) + ATTACH_ACC, 9))
        self.assertEqual(п.уровни[-1].итог, "зашифровано")

    def test_целостность_без_шифра(self):
        сообщение = b"\x07\x5d\x02\x00" + lv(b"\xe0\xe0")
        п = разобрать_пакет(s1ap(b"\x37" + b"\x01\x02\x03\x04\x00" + сообщение, 11))
        ф = поля(п, "NAS-EPS")
        self.assertEqual((ф["nas_eps.emm.toc"].текст, ф["nas_eps.emm.toi"].текст),
                         ("EEA0 (без шифра)", "128-EIA2 (AES)"))
        self.assertEqual(п.уровни[-1].итог, "Security mode command, EEA0/EIA2")
        п = разобрать_пакет(s1ap(b"\x17" + bytes(5) + b"\x07\x5d\x71\x00" + lv(b"\xe0"), 11))
        self.assertEqual(п.уровни[-1].итог, "Security mode command, EEA7/EIA1")

    def test_прочие_emm(self):
        п = разобрать_пакет(s1ap(b"\x07\x44\x03", 13))
        self.assertEqual(поля(п, "NAS-EPS")["nas_eps.emm.cause"].текст, "Illegal UE")
        self.assertEqual(п.уровни[-1].итог, "Attach reject, причина: Illegal UE")
        self.assertEqual(разобрать_пакет(s1ap(b"\x07\x44\xfe", 13)).уровни[-1].итог, "Attach reject, причина: 254")
        п = разобрать_пакет(s1ap(b"\x07\x55\x01", 11))
        self.assertEqual(п.уровни[-1].итог, "Identity request, IMSI")
        п = разобрать_пакет(s1ap(b"\x07\x56" + lv(личность("35693803564380", 2)), 13))
        self.assertEqual(п.уровни[-1].итог, "Identity response, IMEI 35693803564380")
        п = разобрать_пакет(s1ap(b"\x07\x56" + lv(личность(IMSI, 1)), 13))
        self.assertEqual(поля(п, "NAS-EPS")["e212.imsi"].текст, IMSI)
        п = разобрать_пакет(s1ap(b"\x07\x48\x01" + lv(GUTI), 12))
        ф = поля(п, "NAS-EPS")
        self.assertEqual(ф["nas_eps.emm.update_type"].текст, "combined TA/LA updating")
        self.assertIn("GUTI 250-01", п.уровни[-1].итог)
        п = разобрать_пакет(s1ap(b"\x07\x45\x09" + lv(GUTI), 12))
        self.assertIn("M-TMSI 0xC0DE1234", п.уровни[-1].итог)
        п = разобрать_пакет(s1ap(b"\x07\x50" + lv(GUTI), 11))
        self.assertEqual(поля(п, "NAS-EPS")["nas_eps.mobile_identity"].имя, "Новый GUTI")
        rand = bytes(range(16))
        п = разобрать_пакет(s1ap(b"\x07\x52\x00" + rand + lv(bytes(range(16, 32))), 11))
        ф = поля(п, "NAS-EPS")
        self.assertEqual((ф["nas_eps.emm.rand"].текст, ф["nas_eps.emm.autn"].текст),
                         (rand.hex(), bytes(range(16, 32)).hex()))
        п = разобрать_пакет(s1ap(b"\x07\x49\x00\x50" + lv(GUTI), 11))
        self.assertIn("M-TMSI", п.уровни[-1].итог)

    def test_sms_в_nas_transport(self):
        cp_data = b"\x09\x01" + lv(b"\x01\x02\x00\x07\x91\x97\x21\x43\x65\xf7")
        п = разобрать_пакет(s1ap(b"\x07\x62" + lv(cp_data), 11))
        self.assertEqual([у.протокол for у in п.уровни][-3:], ["NAS-EPS", "GSM SMS", "Данные"])
        self.assertEqual(п.инфо, "S1AP downlinkNASTransport (initiatingMessage); NAS: Downlink NAS transport")

    def test_esm_отдельно_и_service_request(self):
        п = разобрать_пакет(s1ap(bytes([0x02, 0x05, 0xD1, 0x1B]), 13))
        ф = поля(п, "NAS-EPS")
        self.assertEqual(ф["nas_eps.esm.cause"].текст, "Missing or unknown APN")
        self.assertEqual(ф["nas_eps.esm.proc_trans_id"].сырое, 5)
        self.assertEqual(п.уровни[-1].итог, "PDN connectivity reject, причина: Missing or unknown APN")
        п = разобрать_пакет(s1ap(b"\xc7\x25\xab\xcd", 12))
        ф = поля(п, "NAS-EPS")
        self.assertEqual((ф["nas_eps.emm.ksi_and_seq"].сырое, ф["nas_eps.emm.short_mac"].сырое), (0x25, 0xABCD))
        self.assertEqual(п.уровни[-1].итог, "Service request")

    def test_оборванный_и_чужой(self):
        п = разобрать_пакет(s1ap(ATTACH_REQ[:10]))
        self.assertIn("NAS EPS: сообщение оборвано", п.ошибки)
        self.assertTrue(п.уровни[-1].итог.endswith("оборвано"))
        п = разобрать_пакет(s1ap(b"\x05\x41"))
        self.assertEqual(п.уровни[-1].протокол, "S1AP")
        # Определитель длины OCTET STRING не сходится со значением IE — не NAS.
        pdu = aper_pdu(0, 12, 1, [(26, 0, b"\x05" + ATTACH_REQ)])
        self.assertEqual(разобрать_пакет(на_sctp(pdu, 18)).уровни[-1].протокол, "S1AP")


# -- 5GS ----------------------------------------------------------------------------------

SUCI_NULL = b"\x01" + PLMN_25001 + b"\xf0\xff" + b"\x00\x00" + bcd("1234567890")
GUTI_5G = b"\xf2" + PLMN_25001 + b"\x01" + struct.pack(">H", (4 << 6) | 3) + struct.pack(">I", 0x11223344)


class Тест5GS(unittest.TestCase):
    def test_registration_request_suci(self):
        п = разобрать_пакет(ngap(b"\x7e\x00\x41\x71" + lve(SUCI_NULL)))
        self.assertEqual([у.протокол for у in п.уровни][-2:], ["NGAP", "NAS-5GS"])
        ф = поля(п, "NAS-5GS")
        self.assertEqual(ф["nas_5gs.epd"].текст, "5GMM")
        self.assertEqual(ф["nas_5gs.mm.5gs_reg_type"].текст, "initial registration")
        self.assertEqual(ф["nas_5gs.mm.ngksi"].сырое, 7)
        self.assertEqual(ф["e212.imsi"].текст, IMSI)
        итог = f"Registration request, initial registration, SUCI: IMSI {IMSI} (нулевая схема), маршрут 0"
        self.assertEqual(п.уровни[-1].итог, итог)
        self.assertEqual(п.инфо, f"NGAP InitialUEMessage (initiatingMessage); NAS: {итог}")

    def test_suci_схема_ecies_и_прочие_идентификаторы(self):
        suci = b"\x01" + PLMN_25001 + b"\x21\xf3" + b"\x01\x05" + bytes(40)
        п = разобрать_пакет(ngap(b"\x7e\x00\x5c" + lve(suci), 46))
        self.assertEqual(п.уровни[-1].итог, "Identity response, SUCI 250-01, маршрут 123, схема ECIES профиль A, "
                                            "ключ сети 5, выход 40 байт")
        self.assertNotIn("e212.imsi", поля(п, "NAS-5GS"))
        п = разобрать_пакет(ngap(b"\x7e\x00\x5c" + lve(b"\x11" + bytes(10)), 46))
        self.assertEqual(п.уровни[-1].итог, "Identity response, SUCI (формат SUPI 1)")
        п = разобрать_пакет(ngap(b"\x7e\x00\x5c" + lve(личность("3569380356438091", 5)), 46))
        self.assertEqual(п.уровни[-1].итог, "Identity response, IMEISV 3569380356438091")
        s_tmsi = b"\xf4" + struct.pack(">H", (4 << 6) | 3) + struct.pack(">I", 0xAABBCCDD)
        п = разобрать_пакет(ngap(b"\x7e\x00\x4c\x01" + lve(s_tmsi)))
        self.assertEqual(п.уровни[-1].итог, "Service request, 5G-S-TMSI: набор 4, указатель 3, 5G-TMSI 0xAABBCCDD")

    def test_registration_accept_защищённый(self):
        сообщение = b"\x7e\x00\x42" + lv(b"\x01") + b"\x77" + lve(GUTI_5G)
        п = разобрать_пакет(ngap(b"\x7e\x02\xde\xad\xbe\xef\x01" + сообщение, 4))
        ф = поля(п, "NAS-5GS")
        self.assertEqual((ф["nas_5gs.msg_auth_code"].сырое, ф["nas_5gs.seq_no"].сырое), (0xDEADBEEF, 1))
        self.assertEqual(п.уровни[-1].итог, "нулевой шифр, Registration accept, 5G-GUTI 250-01, AMF регион 1, "
                                            "набор 4, указатель 3, 5G-TMSI 0x11223344")
        п = разобрать_пакет(ngap(b"\x7e\x02" + bytes(5) + b"\x55\x66\x77\x88", 4))
        self.assertEqual(п.уровни[-1].итог, "зашифровано")
        self.assertEqual(поля(п, "NAS-5GS")["nas_5gs.ciphered_msg"].текст, "4 байт")

    def test_security_mode_и_причины(self):
        п = разобрать_пакет(ngap(b"\x7e\x03" + bytes(5) + b"\x7e\x00\x5d\x02\x00" + lv(b"\x80\x20"), 4))
        ф = поля(п, "NAS-5GS")
        self.assertEqual((ф["nas_5gs.mm.nas_sec_algo_enc"].текст, ф["nas_5gs.mm.nas_sec_algo_ip"].текст),
                         ("NEA0 (без шифра)", "128-NIA2 (AES)"))
        self.assertEqual(п.уровни[-1].итог, "Security mode command, NEA0/NIA2")
        п = разобрать_пакет(ngap(b"\x7e\x00\x44\x03", 4))
        self.assertEqual(п.уровни[-1].итог, "Registration reject, причина: Illegal UE")
        п = разобрать_пакет(ngap(b"\x7e\x00\x5b\x01", 4))
        self.assertEqual(п.уровни[-1].итог, "Identity request, SUCI")
        rand = bytes(range(16))
        п = разобрать_пакет(ngap(b"\x7e\x00\x56\x00" + lv(b"\x00\x00") + b"\x21" + rand + b"\x20" + lv(bytes(16)), 4))
        self.assertEqual(поля(п, "NAS-5GS")["nas_5gs.mm.rand"].текст, rand.hex())

    def test_pdu_сеанс_в_nas_transport(self):
        запрос = b"\x2e\x05\x01\xc1\xff\xff"
        п = разобрать_пакет(ngap(b"\x7e\x00\x67\x01" + lve(запрос) + b"\x12\x05" + b"\x25" + lv(apn("ims")), 46))
        ф = поля(п, "NAS-5GS")
        self.assertEqual(ф["nas_5gs.mm.pld_cont_type"].текст, "N1 SM")
        self.assertEqual(ф["nas_5gs.pdu_session_id"].сырое, 5)
        self.assertEqual(ф["nas_5gs.sm.message_type"].сырое, 0xC1)
        self.assertEqual(п.уровни[-1].итог, "UL NAS transport, DNN ims, PDU session establishment request")
        принят = (b"\x2e\x05\x01\xc2\x11" + lve(b"\x01\x00\x03\x31\x01\x09") + lv(bytes(6))
                  + b"\x29" + lv(b"\x01" + bytes([10, 60, 0, 1])) + b"\x25" + lv(apn("internet")))
        п = разобрать_пакет(ngap(b"\x7e\x00\x68\x01" + lve(принят), 4))
        ф = поля(п, "NAS-5GS")
        self.assertEqual(ф["nas_5gs.sm.pdu_addr"].текст, "10.60.0.1")
        self.assertIn("PDU session establishment accept, адрес 10.60.0.1, DNN internet", п.уровни[-1].итог)
        п = разобрать_пакет(ngap(b"\x2e\x05\x01\xc3\x1b", 4))
        self.assertEqual(п.уровни[-1].итог, "PDU session establishment reject, причина: Missing or unknown DNN")

    def test_ipv6_адреса(self):
        self.assertEqual(nas._адрес(b"\x02" + bytes(7) + b"\x01"), "IPv6 ::1")
        self.assertEqual(nas._адрес(b"\x03" + bytes([0, 0, 0, 0, 0, 0, 0xab, 0xcd]) + bytes([10, 0, 0, 7])),
                         "10.0.0.7, IPv6 ::abcd")
        self.assertIsNone(nas._адрес(b"\x01\x0a"))
        self.assertIsNone(nas._адрес(b""))

    def test_регистрация(self):
        self.assertIn(("S1AP", 26), mobilnye.APER_IE_РАЗБОР)
        self.assertIn(("NGAP", 38), mobilnye.APER_IE_РАЗБОР)
        self.assertEqual((ДОП_УРОВНИ["NAS-EPS"], ДОП_УРОВНИ["NAS-5GS"]), ("прикладной", "прикладной"))


def раскладка(п, протокол):
    """Поля уровня: ключ, место от начала уровня, длина, сырое значение."""
    у = [x for x in п.уровни if x.протокол == протокол][0]
    return [(x.ключ, x.смещение - у.смещение, x.длина, x.сырое) for x in у.поля]


class ТестГраницEPS(unittest.TestCase):
    def test_причины_по_типам(self):
        for тип in (0x44, 0x4B, 0x4E, 0x5C, 0x5F, 0x60):
            п = разобрать_пакет(s1ap(bytes([0x07, тип, 0x03]), 13))
            self.assertEqual(поля(п, "NAS-EPS")["nas_eps.emm.cause"].сырое, 3, hex(тип))
        for тип in (0xC3, 0xC7, 0xCB, 0xCD, 0xD1, 0xD3, 0xD5, 0xD7, 0xE8):
            п = разобрать_пакет(s1ap(bytes([0x02, 0x01, тип, 0x1A]), 13))
            self.assertEqual(поля(п, "NAS-EPS")["nas_eps.esm.cause"].сырое, 0x1A, hex(тип))
        for тип in (0xCF, 0xD2, 0xC1):
            п = разобрать_пакет(s1ap(bytes([0x02, 0x01, тип, 0x1A]), 13))
            self.assertNotIn("nas_eps.esm.cause", поля(п, "NAS-EPS"), hex(тип))
        self.assertNotIn("nas_eps.emm.cause", поля(разобрать_пакет(s1ap(b"\x07\x44", 13)), "NAS-EPS"))

    def test_раскладка_attach_request(self):
        п = разобрать_пакет(s1ap(ATTACH_REQ))
        imsi = личность(IMSI, 1)
        esm = 3 + 1 + len(imsi) + 3 + 2
        self.assertEqual(раскладка(п, "NAS-EPS"), [
            ("nas_eps.security_header_type", 0, 1, 0), ("nas_eps.protocol_discriminator", 0, 1, 7),
            ("nas_eps.nas_msg_emm_type", 1, 1, 0x41), ("nas_eps.emm.eps_att_type", 2, 1, 2),
            ("nas_eps.emm.nas_key_set_id", 2, 1, 7), ("nas_eps.mobile_identity", 4, len(imsi), f"IMSI {IMSI}"),
            ("e212.imsi", 4, len(imsi), IMSI), ("nas_eps.bearer_id", esm, 1, 0),
            ("nas_eps.esm.proc_trans_id", esm + 1, 1, 1), ("nas_eps.nas_msg_esm_type", esm + 2, 1, 0xD0),
            ("nas_eps.esm.pdn_type", esm + 3, 1, 1), ("nas_eps.esm.apn", esm + 6, 9, "internet")])

    def test_раскладка_защищённого_и_esm(self):
        сообщение = b"\x07\x5d\x02\x00" + lv(b"\xe0\xe0")
        п = разобрать_пакет(s1ap(b"\x37" + b"\x01\x02\x03\x04\x09" + сообщение, 11))
        self.assertEqual(раскладка(п, "NAS-EPS"), [
            ("nas_eps.security_header_type", 0, 1, 3), ("nas_eps.protocol_discriminator", 0, 1, 7),
            ("nas_eps.msg_auth_code", 1, 4, 0x01020304), ("nas_eps.seq_no", 5, 1, 9),
            ("nas_eps.nas_msg_emm_type", 7, 1, 0x5D), ("nas_eps.emm.toc", 8, 1, 0), ("nas_eps.emm.toi", 8, 1, 2)])
        п = разобрать_пакет(s1ap(bytes([0x62, 0x07, 0xD1, 0x1B]), 13))
        self.assertEqual(раскладка(п, "NAS-EPS"), [
            ("nas_eps.protocol_discriminator", 0, 1, 2), ("nas_eps.bearer_id", 0, 1, 6),
            ("nas_eps.esm.proc_trans_id", 1, 1, 7), ("nas_eps.nas_msg_esm_type", 2, 1, 0xD1),
            ("nas_eps.esm.cause", 3, 1, 0x1B)])
        п = разобрать_пакет(s1ap(b"\xc7\x25\xab\xcd", 12))
        self.assertEqual(раскладка(п, "NAS-EPS")[2:], [("nas_eps.emm.ksi_and_seq", 1, 1, 0x25),
                                                        ("nas_eps.emm.short_mac", 2, 2, 0xABCD)])
        п = разобрать_пакет(s1ap(b"\x27" + bytes(5) + b"\x9a\x31\x77", 9))
        self.assertEqual(раскладка(п, "NAS-EPS")[-1], ("nas_eps.ciphered_msg", 6, 3, "3 байт"))
        self.assertEqual(п.уровни[-1].длина, 9)

    def test_сырые_значения(self):
        п = разобрать_пакет(s1ap(b"\x07\x41\x7e" + lv(личность(IMSI, 1)) + lv(b"\xe0") + lve(b"")))
        ф = поля(п, "NAS-EPS")
        self.assertEqual((ф["nas_eps.emm.eps_att_type"].сырое, ф["nas_eps.emm.nas_key_set_id"].сырое), (6, 7))
        п = разобрать_пакет(s1ap(b"\x07\x48\xf2" + lv(GUTI), 12))
        self.assertEqual(поля(п, "NAS-EPS")["nas_eps.emm.update_type"].сырое, 2)
        п = разобрать_пакет(s1ap(b"\x07\x55\xf3", 11))
        ф = поля(п, "NAS-EPS")
        self.assertEqual((ф["nas_eps.emm.id_type2"].сырое, п.уровни[-1].итог), (3, "Identity request, IMEISV"))
        self.assertEqual(разобрать_пакет(s1ap(b"\x07\x55\xf6", 11)).уровни[-1].итог, "Identity request, 6")
        п = разобрать_пакет(s1ap(bytes([0x02, 0x01, 0xD0, 0xF1]), 13))
        self.assertEqual(поля(п, "NAS-EPS")["nas_eps.esm.pdn_type"].сырое, 7)

    def test_короткие_и_граничные(self):
        # Тип сообщения — последний байт: разбор не падает.
        for nas_pdu in (b"\x07\x55", b"\x07\x5d", b"\x07\x52\x00" + bytes(15), b"\x07\x48\x00",
                        bytes([0x02, 0x01, 0xD0]), b"\x07\x62"):
            п = разобрать_пакет(s1ap(nas_pdu, 11))
            self.assertEqual(п.уровни[-1].протокол, "NAS-EPS", nas_pdu.hex())
        п = разобрать_пакет(s1ap(b"\x07\x52\x00" + bytes(16) + lv(bytes(16)), 11))
        self.assertIn("nas_eps.emm.rand", поля(п, "NAS-EPS"))
        self.assertIn("NAS EPS: сообщение оборвано", разобрать_пакет(s1ap(b"\x07\x52\x00" + bytes(16), 11)).ошибки)
        # Защищённое простое под заголовком «шифр»: нужен и тип сообщения.
        п = разобрать_пакет(s1ap(b"\x27" + bytes(5) + b"\x07", 9))
        self.assertEqual(п.уровни[-1].итог, "зашифровано")
        # ESM-контейнер из двух байт — не сообщение ESM.
        п = разобрать_пакет(s1ap(b"\x07\x41\x72" + lv(личность(IMSI, 1)) + lv(b"\xe0") + lve(b"\x02\x01")))
        self.assertNotIn("nas_eps.nas_msg_esm_type", поля(п, "NAS-EPS"))
        п = разобрать_пакет(s1ap(b"\x07\x42\x02\x21" + lv(b"\x00") + lve(b"\x02\x01")))
        self.assertNotIn("nas_eps.nas_msg_esm_type", поля(п, "NAS-EPS"))
        п = разобрать_пакет(s1ap(b"\x07\x42\x02\x21" + lv(b"\x00") + lve(bytes([0x52, 0x01, 0xC2]))))
        self.assertEqual(поля(п, "NAS-EPS")["nas_eps.nas_msg_esm_type"].сырое, 0xC2)
        # TAU accept: GUTI сразу за результатом обновления.
        п = разобрать_пакет(s1ap(b"\x07\x49\x00\x50" + lv(GUTI), 11))
        self.assertEqual(поля(п, "NAS-EPS")["nas_eps.mobile_identity"].смещение - п.уровни[-1].смещение, 5)
        # Detach request из одного байта — без идентификатора.
        self.assertNotIn("nas_eps.mobile_identity", поля(разобрать_пакет(s1ap(b"\x07\x45\x09", 11)), "NAS-EPS"))
        self.assertNotIn("nas_eps.emm.update_type", поля(разобрать_пакет(s1ap(b"\x07\x45\x09", 11)), "NAS-EPS"))

    def test_uplink_nas_transport(self):
        cp = b"\x09\x04"
        п = разобрать_пакет(s1ap(b"\x07\x63" + lv(cp), 13))
        self.assertEqual([у.протокол for у in п.уровни][-2:], ["NAS-EPS", "GSM SMS"])
        self.assertEqual(п.уровни[-1].смещение, п.уровни[-2].смещение + 3)
        self.assertEqual(п.уровни[-1].итог, "CP-ACK")
        п = разобрать_пакет(s1ap(b"\x07\x64" + lv(cp), 13))
        self.assertEqual(п.уровни[-1].протокол, "NAS-EPS")


class ТестПомощников(unittest.TestCase):
    def test_метки_и_адреса(self):
        self.assertEqual(nas._метки(b"\x03ims\x03net"), "ims.net")
        self.assertEqual(nas._метки(b""), "")
        self.assertEqual(nas._адрес(b"\x01\x0a\x00\x00\x01"), "10.0.0.1")
        self.assertIsNone(nas._адрес(b"\x01\x0a\x00\x00"))
        self.assertIsNone(nas._адрес(b"\x02" + bytes(7)))
        self.assertEqual(nas._адрес(b"\x02" + bytes(8)), "IPv6 ::")
        self.assertIsNone(nas._адрес(b"\x03" + bytes(11)))
        self.assertIsNone(nas._адрес(b"\x07" + bytes(12)))
        self.assertEqual(nas._адрес(b"\x09\x0a\x00\x00\x01"), "10.0.0.1")

    def test_идентификаторы(self):
        guti = GUTI
        self.assertIsNone(nas._eps_идентификатор(guti[:10]))
        self.assertIsNone(nas._eps_идентификатор(b""))
        self.assertIsNone(nas._eps_идентификатор(b"\x02\x01"))
        self.assertEqual(nas._eps_идентификатор(личность("123456789012345", 3)), "IMEI 123456789012345")
        self.assertIsNone(nas._5gs_идентификатор(b""))
        self.assertEqual(nas._5gs_идентификатор(SUCI_NULL[:7]), "SUCI (формат SUPI 0)")
        self.assertIn("IMSI", nas._5gs_идентификатор(SUCI_NULL[:8]))
        self.assertIsNone(nas._5gs_идентификатор(GUTI_5G[:10]))
        s_tmsi = b"\xf4\x01\x03\xaa\xbb\xcc\xdd"
        self.assertIsNotNone(nas._5gs_идентификатор(s_tmsi))
        self.assertIsNone(nas._5gs_идентификатор(s_tmsi[:6]))
        self.assertEqual(nas._5gs_идентификатор(личность("123456789012345", 3)), "IMEI 123456789012345")
        self.assertIsNone(nas._5gs_идентификатор(b"\x06\x00"))
        # PLMN с трёхзначным MNC и маршрут из двух байт.
        suci = b"\x01" + bytes([0x13, 0x00, 0x62]) + b"\x21\x43" + b"\x00\x00" + bcd("0000000001")
        self.assertEqual(nas._5gs_идентификатор(suci), "SUCI: IMSI 3102600000000001 (нулевая схема), маршрут 1234")

    def test_необязательные(self):
        д = bytes([0x93, 0x13]) + bytes(5) + bytes([0x77, 0x00, 0x02, 0xAA, 0xBB, 0x28, 0x01, 0xCC, 0x9F])
        self.assertEqual(nas._необязательные(д, 0, len(д), nas.TV_EPS),
                         {0x90: (0, 1), 0x13: (2, 5), 0x77: (10, 2), 0x28: (14, 1)})
        self.assertEqual(nas._необязательные(b"\x77\x00", 0, 2, nas.TV_EPS), {})
        self.assertEqual(nas._необязательные(b"\x28", 0, 1, nas.TV_EPS), {})
        self.assertEqual(nas._необязательные(b"\x7f", 0, 1, nas.TV_EPS), {})
        self.assertEqual(nas._необязательные(b"\x80", 0, 1, nas.TV_EPS), {0x80: (0, 1)})


class ТестГраниц5GS(unittest.TestCase):
    def test_причины_по_типам(self):
        for тип in (0x44, 0x4D, 0x59, 0x5F, 0x64):
            п = разобрать_пакет(ngap(bytes([0x7E, 0x00, тип, 0x03]), 4))
            self.assertEqual(поля(п, "NAS-5GS")["nas_5gs.mm.5gmm_cause"].сырое, 3, hex(тип))
        for тип in (0xC3, 0xCA, 0xCD, 0xD2, 0xD3, 0xD6):
            п = разобрать_пакет(ngap(bytes([0x2E, 0x05, 0x01, тип, 0x1B]), 4))
            self.assertEqual(поля(п, "NAS-5GS")["nas_5gs.sm.5gsm_cause"].сырое, 0x1B, hex(тип))
        for тип in (0xC7, 0xCE, 0xC1):
            п = разобрать_пакет(ngap(bytes([0x2E, 0x05, 0x01, тип, 0x1B]), 4))
            self.assertNotIn("nas_5gs.sm.5gsm_cause", поля(п, "NAS-5GS"), hex(тип))
        self.assertNotIn("nas_5gs.mm.5gmm_cause", поля(разобрать_пакет(ngap(b"\x7e\x00\x44", 4)), "NAS-5GS"))

    def test_виды_с_идентификатором(self):
        for тип in (0x41, 0x45, 0x4C):
            п = разобрать_пакет(ngap(bytes([0x7E, 0x00, тип, 0x71]) + lve(SUCI_NULL)))
            self.assertEqual(поля(п, "NAS-5GS")["e212.imsi"].текст, IMSI, hex(тип))
        п = разобрать_пакет(ngap(b"\x7e\x00\x54\x77" + lve(GUTI_5G), 4))
        self.assertIn("5G-GUTI", п.уровни[-1].итог)

    def test_раскладка_регистрации(self):
        п = разобрать_пакет(ngap(b"\x7e\x00\x41\xf9" + lve(SUCI_NULL)))
        self.assertEqual(раскладка(п, "NAS-5GS"), [
            ("nas_5gs.epd", 0, 1, 0x7E), ("nas_5gs.security_header_type", 1, 1, 0),
            ("nas_5gs.mm.message_type", 2, 1, 0x41), ("nas_5gs.mm.5gs_reg_type", 3, 1, 1),
            ("nas_5gs.mm.ngksi", 3, 1, 7), ("nas_5gs.mobile_identity", 6, len(SUCI_NULL),
                                             f"SUCI: IMSI {IMSI} (нулевая схема), маршрут 0"),
            ("e212.imsi", 6, len(SUCI_NULL), IMSI)])

    def test_раскладка_защищённого_и_транспорта(self):
        п = разобрать_пакет(ngap(b"\x7e\x03\x01\x02\x03\x04\x07" + b"\x7e\x00\x5d\xa5\x00" + lv(b"\x80"), 4))
        self.assertEqual(раскладка(п, "NAS-5GS"), [
            ("nas_5gs.epd", 0, 1, 0x7E), ("nas_5gs.security_header_type", 1, 1, 3),
            ("nas_5gs.msg_auth_code", 2, 4, 0x01020304), ("nas_5gs.seq_no", 6, 1, 7),
            ("nas_5gs.mm.message_type", 9, 1, 0x5D), ("nas_5gs.mm.nas_sec_algo_enc", 10, 1, 0xA),
            ("nas_5gs.mm.nas_sec_algo_ip", 10, 1, 5)])
        запрос = b"\x2e\x05\x01\xc1\xff\xff"
        п = разобрать_пакет(ngap(b"\x7e\x00\x67\xf1" + lve(запрос) + b"\x12\x05" + b"\x25" + lv(apn("ims")), 46))
        р = раскладка(п, "NAS-5GS")
        self.assertIn(("nas_5gs.mm.pld_cont_type", 3, 1, 1), р)
        self.assertIn(("nas_5gs.pdu_session_id", 13, 1, 5), р)
        self.assertIn(("nas_5gs.dnn", 16, 4, "ims"), р)
        self.assertIn(("nas_5gs.pdu_session_id", 7, 1, 5), р)
        self.assertIn(("nas_5gs.pti", 8, 1, 1), р)
        self.assertIn(("nas_5gs.sm.message_type", 9, 1, 0xC1), р)
        п = разобрать_пакет(ngap(b"\x7e\x02" + bytes(5) + b"\x55", 4))
        self.assertEqual(раскладка(п, "NAS-5GS")[-1], ("nas_5gs.ciphered_msg", 7, 1, "1 байт"))
        self.assertEqual(п.уровни[-1].длина, 8)

    def test_сырые_значения_и_границы(self):
        п = разобрать_пакет(ngap(b"\x7e\x00\x5b\xf3", 4))
        ф = поля(п, "NAS-5GS")
        self.assertEqual((ф["nas_5gs.mm.type_id"].сырое, п.уровни[-1].итог), (3, "Identity request, IMEI"))
        self.assertEqual(разобрать_пакет(ngap(b"\x7e\x00\x5b\xf6", 4)).уровни[-1].итог, "Identity request, 6")
        for nas_pdu in (b"\x7e\x00\x5b", b"\x7e\x00\x5d", b"\x7e\x00\x44", b"\x2e\x05\x01\xc3"):
            п = разобрать_пакет(ngap(nas_pdu, 4))
            self.assertEqual(п.уровни[-1].протокол, "NAS-5GS", nas_pdu.hex())
        self.assertEqual(разобрать_пакет(ngap(b"\x7e\x00", 4)).уровни[-1].протокол, "NGAP")
        # Защищённое: внутри должно быть простое 5GMM (EPD, заголовок 0, известный тип).
        for внутри in (b"\x7e\x01\x41", b"\x2e\x00\x41", b"\x7e\x00\x99", b"\x7e\x00"):
            п = разобрать_пакет(ngap(b"\x7e\x02" + bytes(5) + внутри, 4))
            self.assertEqual(п.уровни[-1].итог, "зашифровано", внутри.hex())
        for заг in (1, 3):
            п = разобрать_пакет(ngap(bytes([0x7E, заг]) + bytes(5) + b"\x7e\x00\x5b\x01", 4))
            self.assertEqual(п.уровни[-1].итог, "Identity request, SUCI")
        п = разобрать_пакет(ngap(b"\x7e\x05" + bytes(5) + b"\x7e\x00\x5b\x01", 4))
        self.assertEqual(п.уровни[-1].итог, "зашифровано")
        # Контейнер N1 SM из трёх байт — не сообщение 5GSM; SMS — разбор GSM.
        п = разобрать_пакет(ngap(b"\x7e\x00\x68\x01" + lve(b"\x2e\x05\x01"), 4))
        self.assertNotIn("nas_5gs.sm.message_type", поля(п, "NAS-5GS"))
        п = разобрать_пакет(ngap(b"\x7e\x00\x68\x02" + lve(b"\x09\x04"), 4))
        self.assertEqual([у.протокол for у in п.уровни][-2:], ["NAS-5GS", "GSM SMS"])
        п = разобрать_пакет(ngap(b"\x7e\x00\x68\x03" + lve(b"\x09\x04"), 4))
        self.assertEqual(п.уровни[-1].протокол, "NAS-5GS")

    def test_nas_pdu_определитель(self):
        self.assertIsNone(nas._nas_pdu(nas.nas_5gs)(None, 3, 3))
        pdu = aper_pdu(0, 15, 1, [(38, 0, b"\x05\x7e\x00\x5b\x01")])
        self.assertEqual(разобрать_пакет(на_sctp(pdu, 60, 38412, 38412)).уровни[-1].протокол, "NGAP")


if __name__ == "__main__":
    unittest.main()
