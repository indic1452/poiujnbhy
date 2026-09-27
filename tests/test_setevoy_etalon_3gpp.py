"""Эталон 3GPP: реальные сообщения NAS 2G/3G/4G/5G из тестов pycrate (github.com/pycrate-org/pycrate,
test/test_mobile.py; LGPL-2.1 — данные захватов, не код). Ожидаемые сводки сверены с декодером pycrate:
вид сообщения и идентификаторы (IMSI, IMEISV, TMSI/P-TMSI, GUTI, 5G-GUTI, SUCI), APN, адреса, LAI/RAI.
Если pycrate установлен (офлайн-комплект разработчика), идентификаторы сверяются с ним заново."""

import unittest

import _bootstrap  # noqa: F401
from reportgen.setevoy.pole import Пакет
from reportgen.setevoy.protokoly import gsm, nas
from reportgen.setevoy.razbor import Разбор

try:
    from pycrate_mobile.NAS import parse_NAS5G, parse_NAS_MO, parse_NAS_MT
except ImportError:                                   # pragma: no cover — эталон есть только у разработчика
    parse_NAS_MO = parse_NAS_MT = parse_NAS5G = None

С = 8

#: (сообщение, вид по pycrate, первая сводка нашего разбора)
СООБЩЕНИЯ = (
    ("05080200f11040005705f44c6a94c033035758a6", "MMLocationUpdatingRequest",
     'GSM MM: Location Updating Request: MCC 001, MNC 01, LAC 16384; TMSI 4C6A94C0'),
    ("052401035758a605f4345b7129c2", "MMCMServiceRequest",
     'GSM MM: CM Service Request: TMSI 345B7129'),
    ("0514a3c729e021042a92f637", "MMAuthenticationResponse",
     'GSM MM: Authentication Response'),
    ("034504066004020005815e068160000000001502010040080402600400021f00", "CCSetupMO",
     'GSM CC: Setup: номер 0600000000'),
    ("8381", "CCAlertingMO",
     'GSM CC: Alerting'),
    ("834804066004020005811502010040080402600400021f00", "CCCallConfirmed",
     'GSM CC: Call Confirmed'),
    ("83c7", "CCConnectMO",
     'GSM CC: Connect'),
    ("03cf", "CCConnectAcknowledge",
     'GSM CC: Connect Acknowledge'),
    ("036502e090", "CCDisconnectMO",
     'GSM CC: Disconnect'),
    ("032d", "CCReleaseMO",
     'GSM CC: Release'),
    ("03aa", "CCReleaseCompleteMO",
     'GSM CC: Release Complete'),
    ("8904", "CP_ACK",
     'GSM SMS: CP-ACK'),
    ("890106020141020000", "CP_DATA",
     'GSM SMS: CP-DATA'),
    ("19011c00020007913386094000f01001840a816000000000000004d4f29c0e", "CP_DATA",
     'GSM SMS: CP-DATA'),
    ("0b7b1c14a11202010002013b300a04010f0405a3986c36027f0100", "SSRegisterMO",
     'GSM SS: Register'),
    ("0bfa12a210020180300b02013c300604010f040131", "SSFacility",
     'GSM SS: Facility'),
    ("0baa", "SSReleaseComplete",
     'GSM SS: Release Complete'),
    ("080103e5e004010a0005f4fffa01f700f1104000100c0a53432b259ef989004000081705", "GMMAttachRequest",
     'GSM GMM: Attach Request: TMSI FFFA01F7'),
    ("0803", "GMMAttachComplete",
     'GSM GMM: Attach Complete'),
    ("08086002f8108003c81c1a53432b259ef9890040009dd9c633120080013a332c66240100026019e6e82017051805f4c2c85e9a3103e5e034320220005804e060c0401a05f4c3e0732f1b0602f8107500015d0100", "GMMRoutingAreaUpdateRequest",
     'GSM GMM: Routing Area Update Request: MCC 208, MNC 01, LAC 32771, RAC 200; TMSI C2C85E9A; доп. TMSI C3E0732F'),
    ("081300224b1e647b290457a2f017", "GMMAuthenticationCipheringResponse",
     'GSM GMM: Authentication and Ciphering Resp'),
    ("080a", "GMMRoutingAreaUpdateComplete",
     'GSM GMM: Routing Area Update Complete'),
    ("080c2605f4f1c8e8bf32022000", "GMMServiceRequest",
     'GSM GMM: Service Request: TMSI F1C8E8BF'),
    ("8a49", "SMModifyPDPContextAcceptMO",
     'GSM SM: Modify PDP Context Accept (MS to network direction)'),
    ("17d2eba20a020741020bf602f8107500e0c301732f04e060c04000240202d011d1271d8080211001000010810600000000830600000000000d00000a000010005c0a003103e5e0341302f810040511035758a65d0100c1", "EMMSecProtNASMessage",
     'NAS-EPS: Attach request, GUTI 208-01, MMEGI 29952, MMEC 224, M-TMSI 0xC301732F, PDN connectivity request'),
    ("170d22f6f1030756080900000000000000", "EMMSecProtNASMessage",
     'NAS-EPS: Identity response, IMSI 000000000000000'),
    ("17450740e3040753083ec3a476f829b414", "EMMSecProtNASMessage",
     'NAS-EPS: Authentication response'),
    ("075e23093395684292874145f0", "EMMSecurityModeComplete",
     'NAS-EPS: Security mode complete, IMEISV 3598624297814540'),
    ("0202da2807066f72616e6765", "ESMInformationResponse",
     'NAS-EPS: ESM information response, APN orange'),
    ("074300035200c2", "EMMAttachComplete",
     'NAS-EPS: Attach complete'),
    ("0748610bf602f8108003c8c2e65e9a5804e060c0405202f810c4c25c0a00570220003103e5e0341302f810040511035758a65d0100c1", "EMMTrackingAreaUpdateRequest",
     'NAS-EPS: Tracking area update request, GUTI 208-01, MMEGI 32771, MMEC 200, M-TMSI 0xC2E65E9A'),
    ("c7060500", "EMMServiceRequest",
     'NAS-EPS: Service request'),
    ("074c6005f4c2e65e9a57022000", "EMMExtServiceRequest",
     'NAS-EPS: Extended service request, TMSI C2E65E9A'),
    ("074a", "EMMTrackingAreaUpdateComplete",
     'NAS-EPS: Tracking area update complete'),
    ("07632009011d00010007913386094000f01101830a816000000000000005d4f29cae00", "EMMULNASTransport",
     'NAS-EPS: Uplink NAS transport'),
    ("0745630bf602f8108003c8c2e65e9a", "EMMDetachRequestMO",
     'NAS-EPS: Detach request, GUTI 208-01, MMEGI 32771, MMEC 200, M-TMSI 0xC2E65E9A'),
    ("074d707800040200e86f6703091011570233c9d1", "EMMCPServiceRequest",
     'NAS-EPS: Control plane service request'),
    ("062e09006400634103022080", "RRAssignmentCmd",
     'GSM RR: Assignment Command'),
    ("051201f6e3c095753f23a9194291c86395f4782010a322f1689dc5000030dcb7d5eaafafe3", "MMAuthenticationRequest",
     'GSM MM: Authentication Request'),
    ("0521", "MMCMServiceAccept",
     'GSM MM: CM Service Accept'),
    ("050202f8100404", "MMLocationUpdatingAccept",
     'GSM MM: Location Updating Accept'),
    ("83011e02e2a0", "CCAlertingMT",
     'GSM CC: Alerting'),
    ("8302", "CCCallProceeding",
     'GSM CC: Call Proceeding'),
    ("83071e02e281", "CCConnectMT",
     'GSM CC: Connect'),
    ("030f", "CCConnectAcknowledge",
     'GSM CC: Connect Acknowledge'),
    ("832502e090", "CCDisconnectMT",
     'GSM CC: Disconnect'),
    ("830302e2a0", "CCProgress",
     'GSM CC: Progress'),
    ("832d0802e090", "CCReleaseMT",
     'GSM CC: Release'),
    ("032a0802e090", "CCReleaseCompleteMT",
     'GSM CC: Release Complete'),
    ("03050401a05c0811833306000000f0", "CCSetupMT",
     'GSM CC: Setup'),
    ("090123010107913386094000f00017040b913306000000f000007101911172758004d4f29c0e", "CP_DATA",
     'GSM SMS: CP-DATA'),
    ("0904", "CP_ACK",
     'GSM SMS: CP-ACK'),
    ("9901020302", "CP_DATA",
     'GSM SMS: CP-DATA'),
    ("8b3a97a1819402018002013c30818b04010f048185c13a28867bc5602d180c0d8329866ff7fcdd6e17403a500c3d83b561b5b9c2181ed3ebf202885d06c164af584ca118a2dfe9797a3e2feb413a45ac472cd3c36936685e4fdbd3a0f1db3d7f2b64bde6db0d2acfe1e1715931ebc58e6fd00a1486c3cbecf96bda9c82d26cb60b14a381d4f239885c86d7d37350751a7c0dc3ee30390c92e58a", "SSFacility",
     'GSM SS: Facility'),
    ("8b3a9fa1819c02018102013c30819304010f04818dc4023d9c6683c86590fd4d979741f37ada9e068ddfeef91b047fd7e5209d22d60bc2e165f65c21eb4d9bd357b33955cc7a4937bd2c7797e9a0f65b9c669715b45e959e66a7e7653dc8fea6cbcba0b7d92c2f83c6ef76bb0c2abb414679d83d2e83c865783d3d07b14fc5bafc0d2f2b5aad96e25907e914b05ef3ed0695e7f0f0b8ac68b55a0a5c4f5aa6bfeb72", "SSFacility",
     'GSM SS: Facility'),
    ("0802095e0102f8100405011805f4ffc856602a012c3801e0", "GMMAttachAccept",
     'GSM GMM: Attach Accept: MCC 208, MNC 01, LAC 1029, RAC 1; TMSI FFC85660'),
    ("08120000211f12d433eac66f821ce2dfaf54c2c43b802810ac537cb6940c00006a1ec8ee4e0c7c8e", "GMMAuthenticationCipheringRequest",
     'GSM GMM: Authentication and Ciphering Req'),
    ("08214308804f79d87d2e838c4508804f79d87d2e838c4771019190727480490101", "GMMInformation",
     'GSM GMM: GMM Information'),
    ("081503", "GMMIdentityRequest",
     'GSM GMM: Identity Request'),
    ("0809805e02f8100404011805f4d4cbf2852a012c320220003801e0", "GMMRoutingAreaUpdateAccept",
     'GSM GMM: Routing Area Update Accept: MCC 208, MNC 01, LAC 1028, RAC 1; TMSI D4CBF285'),
    ("0a4804030e1c921f7396d2fe7343ffff006400340101", "SMModifyPDPContextRequestMT",
     'GSM SM: Modify PDP Context Request(Network to MS direction)'),
    ("075501", "EMMIdentityRequest",
     'NAS-EPS: Identity request, IMSI'),
    ("075206905ada1e7da557ada1e72650e21ee5e3104bfb73f6b4558000b1903ab88a27237f", "EMMAuthenticationRequest",
     'NAS-EPS: Authentication request'),
    ("37e8a14bcf00075d220605e060c04070c1", "EMMSecProtNASMessage",
     'NAS-EPS: Security mode command, EEA2/EIA2'),
    ("27807d6aa1016b8354", "EMMSecProtNASMessage",
     'NAS-EPS: зашифровано'),
    ("0202d9", "ESMInformationRequest",
     'NAS-EPS: ESM information request'),
    ("07614308004f79d87d2e838c4508004f79d87d2e838c4771019190616180490101", "EMMInformation",
     'NAS-EPS: EMM information'),
    ("07420249062302f810c4c000725202c101081a066f72616e6765066d6e63303031066d6363323038046770727305010a7456415d010030101c911f7396fefe734bffff00fa00fa003203843401005e06fefedddd1010272780000d04c0a80a6e80210a0300000a8106c0a80a6e80210a0400000a83060000000000100205dc500bf602f8108003c8c2e65e9a1302f81004055949640103f05e0106", "EMMAttachAccept",
     'NAS-EPS: Attach accept, Activate default EPS bearer context request, APN orange.mnc001.mcc208.gprs, адрес 10.116.86.65, GUTI 208-01, MMEGI 32771, MMEC 200, M-TMSI 0xC2E65E9A'),
    ("0749015a4954062202f810c4a0570220001302f81004045949640103f05e0106", "EMMTrackingAreaUpdateAccept",
     'NAS-EPS: Tracking area update accept'),
    ("0762028904", "EMMDLNASTransport",
     'NAS-EPS: Downlink NAS transport'),
    ("0746", "EMMDetachAccept",
     'NAS-EPS: Detach accept'),
    ("7e004179000d0100f1100000000022222222222e02e0e0", "FGMMRegistrationRequest",
     'NAS-5GS: Registration request, initial registration, SUCI: IMSI 001012222222222 (нулевая схема), маршрут 0000'),
    ("7e0056000200002198a600000000000098a600000000000020105c717acfe29180001fb3117a0f18c3ab", "FGMMAuthenticationRequest",
     'NAS-5GS: Authentication request'),
    ("7e00572d1034f95b9d3826fc095c9d9232f4d182c5", "FGMMAuthenticationResponse",
     'NAS-5GS: Authentication response'),
    ("7e038f2b564d007e005d010002e0e0", "FGMMSecProtNASMessage",
     'NAS-5GS: Security mode command, NEA0/NIA1'),
    ("7e0300000000007e005d000602f0f0e1360102", "FGMMSecProtNASMessage",
     'NAS-5GS: Security mode command, NEA0/NIA0'),
    ("7e04fd5a6e42007e005e", "FGMMSecProtNASMessage",
     'NAS-5GS: нулевой шифр, Security mode complete'),
    ("7e005e", "FGMMSecurityModeComplete",
     'NAS-5GS: Security mode complete'),
    ("7e005e7700091530014100002100f07100217e004169000d010302460fff000000000000f11001072e02f0f02f05040aabcdef", "FGMMSecurityModeComplete",
     'NAS-5GS: Security mode complete, IMEISV 1031014000012000'),
    ("7e004407", "FGMMRegistrationReject",
     'NAS-5GS: Registration reject, причина: 5GS services not allowed'),
    ("7e0100000000037e004561000bf2030246010041c0e00010", "FGMMSecProtNASMessage",
     'NAS-5GS: Deregistration request (UE originating), 5G-GUTI 302-640, AMF регион 1, набор 1, указатель 1, 5G-TMSI 0xC0E00010'),
    ("7e0046", "FGMMMODeregistrationAccept",
     'NAS-5GS: Deregistration accept (UE originating)'),
    ("7e0042010177000bf2030246010041c0e000105407200302460000641505040aabcdef2101005e016516012c", "FGMMRegistrationAccept",
     'NAS-5GS: Registration accept, 5G-GUTI 302-640, AMF регион 1, набор 1, указатель 1, 5G-TMSI 0xC0E00010'),
    ("7e0043", "FGMMRegistrationComplete",
     'NAS-5GS: Registration complete'),
    ("7e0054d0430989cef73a1d2696db6f450989cef73a1d2696db6f46694791501391446069490101", "FGMMConfigurationUpdateCommand",
     'NAS-5GS: Configuration update command'),
    ("2e0501c1ffff91a1", "FGSMPDUSessionEstabRequest",
     'NAS-5GS: PDU session establishment request'),
    ("2e0501c211000901000631310101ff0506060001060001290501ac115f012506056461746131", "FGSMPDUSessionEstabAccept",
     'NAS-5GS: PDU session establishment accept, адрес 172.17.95.1, DNN data1'),
    ("7e00670100072e0602c1000091120681220401000001250706766973696f6e", "FGMMULNASTransport",
     'NAS-5GS: UL NAS transport, DNN vision, PDU session establishment request'),
    ("7e0100000000067e006801002d2e0602c2110009ff000631310101ff050603f42403f4242905010b000033220401000001250706766973696f6e1206", "FGMMSecProtNASMessage",
     'NAS-5GS: DL NAS transport, PDU session establishment accept, адрес 11.0.0.51, DNN vision'),
    ("7e00670500020002", "FGMMULNASTransport",
     'NAS-5GS: UL NAS transport'),
)


def разобрать(pdu):
    р = Разбор(Пакет(1, 0.0, bytes(С) + pdu + b"\xee" * 4, С + len(pdu) + 4, "RAW"))
    if pdu[0] in (0x7E, 0x2E):
        nas.nas_5gs(р, С, С + len(pdu))
    elif pdu[0] & 0x0F in (2, 7):
        nas.nas_eps(р, С, С + len(pdu))
    else:
        gsm.dtap(р, С, С + len(pdu))
    return р


class ЭталонNAS(unittest.TestCase):
    def test_сводки(self):
        for шестн, вид, ждём in СООБЩЕНИЯ:
            with self.subTest(вид=вид, pdu=шестн[:16]):
                р = разобрать(bytes.fromhex(шестн))
                self.assertEqual(р.п.ошибки, [])
                итог = f"{р.п.уровни[0].протокол}: {р.п.уровни[0].итог}"
                self.assertEqual(итог, ждём)

    @unittest.skipIf(parse_NAS_MO is None, "нет pycrate")
    def test_идентификаторы_как_у_pycrate(self):
        from pycrate_core.elt import Element  # noqa: PLC0415 — только при эталоне

        def обход(e):
            yield e
            for д in getattr(e, "_content", None) or []:
                if isinstance(д, Element):
                    yield from обход(д)
        for шестн, вид, _ in СООБЩЕНИЯ:
            pdu = bytes.fromhex(шестн)
            разбор = parse_NAS5G if pdu[0] in (0x7E, 0x2E) else (parse_NAS_MT if вид.endswith("MT") else parse_NAS_MO)
            m, _ = разбор(pdu)
            if m.__class__.__name__ != вид:
                m, _ = (parse_NAS_MT if разбор is parse_NAS_MO else parse_NAS_MO)(pdu)
            р = разобрать(pdu)
            текст = " ".join(f"{у.итог}" for у in р.п.уровни)
            for e in обход(m):
                if e.__class__.__name__ == "ID":
                    вид_ид, значение = e.decode()[:2]
                    if вид_ид in (1, 2, 3, 5):                  # IMSI, IMEI, IMEISV — цифрами
                        self.assertIn(str(значение), текст, (вид, шестн[:16]))
                    elif вид_ид == 4:                           # TMSI / P-TMSI / M-TMSI
                        self.assertIn(f"{значение:08X}", текст, (вид, шестн[:16]))


if __name__ == "__main__":
    unittest.main()
