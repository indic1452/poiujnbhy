"""Эталон ОКС-7 и мобильных сетей: реальные кадры и сообщения из набора pycrate
(github.com/pycrate-org/pycrate: test/test_mobile.py и test/res/*_pcapr.json — выгрузка tshark
публичных захватов pcapr.net; LGPL-2.1 — данные захватов, не код). Ожидаемые итоги сверены с
pycrate (TCAP_MAP/TCAP_RAW, S1AP, SCCP, GTP) и со стеком Wireshark; расхождения со стеком
Wireshark разобраны: метка MTP3 ANSI (Wireshark разобрал как ITU), сегменты XUDT, чужой контекст.
Если pycrate установлен (офлайн-комплект разработчика), таблицы типов сообщений сверяются с ним."""

import re
import tempfile
import time
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy.pole import Пакет
from reportgen.setevoy.protokoly import mobilnye, oks7
from reportgen.setevoy.razbor import Разбор, разобрать_пакет
from reportgen.setevoy.zahvaty import Захваты, СборщикSCCP

try:
    from pycrate_mobile.SCCP import _RetCause_dict, _SCCPType_dict, _SCMGType_dict
    from pycrate_mobile.TS29060_GTP import GTPMsgType
    from pycrate_mobile.TS29244_PFCP import PFCPMsgType_dict
    from pycrate_mobile.TS29274_GTPC import GTPCMsgType_dict
    from pycrate_mobile.TS29281_GTPU import GTPUNextExtHeader_dict, GTPUType
except ImportError:                                   # pragma: no cover — эталон есть только у разработчика
    GTPMsgType = None

#: Кадры захватов (канал: s1ap_* — Linux SLL, tcapmap_* — Ethernet).
КАДРЫ = {
    "s1ap_3": (
        "000003040006000000000000000008004502006c843440004084e317c0a8a8b7c0a8a8b78e3c95c557bcab2db9f63a320300"
        "00100764eae70001a0000000000000030039bc2a21630001000100000012000b40250000030000000200d300080002000100"
        "1a001211377b99f3e300075d010005e060c04070c1000000"),
    "s1ap_5": (
        "0000030400060000000000000000080045020064843540004084e31ec0a8a8b7c0a8a8b78e3c95c557bcab2d5e62fcc90300"
        "00100764eae80001a0000000000000030031bc2a21640001000200000012000b401d0000030000000200d300080002000100"
        "1a000a092795789852010204d9000000"),
    "s1ap_11": (
        "00000304000600000000000000000800450200980020400040846700c0a8a8b7c0a8a8b795c58e3cef2717d9b8864b910003"
        "00760764eaed0001000700000012000d40620000050000000200d3000800020001001a00393827d0f44064030205d0312804"
        "03696d7327268080211001000010810600000000830600000000000d00000300000100000c00000a00001000006440080013"
        "40011a2d0010004340060013400100010000"),
    "s1ap_30": (
        "00040001000600e052c2303d00000800450200ac00004000408466e5c0a8a8b7c0a8a8de120411a0f3b23efad3a7b0430003"
        "008996d5d3a6000100220000000501000b0100000079000000150000001500830100000d0000040900030f170c8506006113"
        "400110001000020885070011880210034462424804000000026b1e281c060700118605010101a011600f80020780a1090607"
        "04000001000e036c1aa1180201010201383010800813400110001000f2020103830102000000"),
    "s1ap_31": (
        "000000010006080027ee68b300000800450201480000400040846649c0a8a8dec0a8a8b711a01204c6bd0529f5905ff50003"
        "01276c900ada000100170000000501000b0100000117000000150000001600830d00000100000811010e040c18f108850700"
        "11880210030c850600611340011000100002d9648201464904000000026b2a2828060700118605010101a01d611b80020780"
        "a109060704000001000e03a203020100a305a1030201016c820110a282010c02010130820105020138a381ffa181fc305204"
        "103d42857872e6ff5d451f74d6709424340408f987ceb6e3b022b804106f071d9c8e55312a0d60614b054dcedc041004d09a"
        "2362b3ed3f99413b60a3655f4c0410ee1fbc48050d9001f611a99eff2d5a2f3052041004ac2d19176e49823726fb9009f929"
        "d0040813a6e5f0bcbd668f0410cd1fa581aacccb55fcca6363188800230410484f94de371004c15d87230000"),
    "s1ap_32": (
        "000000010006080027ee68b300000800450200e000004000408466b1c0a8a8dec0a8a8b711a01204c6bd0529a5469fe40003"
        "00bf6c900adb000100180000000501000b01000000af000000150000001700830d00000100000811010e040c188908850700"
        "11880210030c85060061134001100010000271e33a51bd95e5b2c7c8c8fd04101c70edb25a6a900177b590a32094afb23052"
        "0410035b416f21fd832b5af63fdd26a53351040822af0e473603e5900410579eef8497ef73412d869cdb57e319750410a926"
        "a441326c07e48d4766ada40c3a640410baddf57e0b909001435cfc52f62c39f51004405d87230000"),
    "tcapmap_3": (
        "000cf1db0f5b000cf1e29f6c08004500005c043a40004084e982c0a8658cc0a865840ded0dedf8f85149fdf78eb900030039"
        "91c01e56000100050000000501000b0100000029000000040000000300038483e1a009000305090242010443860301050307"
        "840300000000"),
    "tcapmap_24": (
        "000393b5e95400016301100008004500004ceb440000ff84a43e55c3c01455c3c00f07d607d636024c51745dd27a0003002a"
        "b1f55241000100020000000501000b010000001a0000000000000001090103006e041120111200005dfeb292"),
    "tcapmap_25": (
        "000163011000000393b5e95408004500005cbb924000408452e155c3c00f55c3c01407d607d6b1f55234c20c594403000010"
        "b1f5524100038e38000000000003002a18fd9c95000100010000000501000b010000001a00000001000000010901b8d10000"
        "212011120000"),
    "tcapmap_32": (
        "000163011000000393b5e954080045000140bba54000408451ea55c3c00f55c3c01407d607d6b1f5523499fc666a0003011d"
        "18fd9c97000100030000000501000b010000010d00000002000000030103b8d1001011810f040f1aea0b1208001104149794"
        "7400000b1208001104149779790800d06281ec4804000000026b1e281c060700118605010101a011600f80020780a1090607"
        "040000010019026c81c3a181c00201ff02012e3081b7800822082121109058f68407911497797908f00481a1200f91214365"
        "87092143f5000080101121901040a031d98c56b3dd7039584c36a3d56c375c0e1693cd6835db0d9783c564335acd76c3e560"
        "31d98c56b3dd7039584c36a3d56c375c0e1693cd6835db0d9783c564335acd76c3e56031d98c56b3dd7039584c36a3d56c37"
        "5c0e1693cd6835db0d9783c564335acd76c3e56031d98c561004c100000200000000"),
    "tcapmap_33": (
        "000163011000000393b5e95408004500008cbba640004084529d55c3c00f55c3c01407d607d6b1f552345dd93dc80003006c"
        "18fd9c98000100040000000501000b010000005c00000002000000040103b8d1002011810f040f1a390b1208001104149794"
        "7400000b12080011041497797908001fb3dd7039584c36a3d56c375c0e1693cd6835db0d97c3c664335acd76c3e5b4100440"
        "00000200"),
    "tcapmap_74": (
        "001edf2d828d00049634bf34080045000128479500003e840048141400010a4015210b590b5903013ffe61df8d9503000010"
        "030140890000200000000000000300f80000407e000e00070000000301000101000000e8021000df0001283c000128620302"
        "000d11800f040e18c50a129500110468310708000a12060012046851014060ad6581aa4804840001ff4904a50500016b2a28"
        "28060700118605010101a01d611b80020780a109060704000001000e03a203020100a305a1030201006c80a26c0201013067"
        "020138a380a180305a04104b9d6191107536658cfe59880cd2ac2704104b8c43a2542050120467f333c00f42d804108c43a2"
        "542050120467f333c00f42d84b041043a2542050120467f333c00f42d84b8c0410a2551a058cdb00004b8d79f7caff501200"
        "00000000001201050000"),
    "tcapmap_104": (
        "d4ca6d013cff005056917a220800450200e816bd400040843f7fb9416bfb50556dc20b590b591641a040cbe6227603000010"
        "518c1f070001a00000000000000300b8bb4ed7240001000d0000000301000101000000a80006000800000065021000950000"
        "20ca0000210d030200000a01030e190b12060012041909145905400b129300110453964901250567646549040000080e6b26"
        "2824060700118605010101a0196117a109060704000001001d03a203020100a305a1030201006c35a233020101302e020147"
        "30293027a02102010280081000000000000000810791190982500500a30980070475301b5d7a57a1028000000000"),
}
#: Сообщения без нижних уровней (test_mobile.py): GTP-U, GTPv2, SCCP, SUA.
СООБЩЕНИЯ = {
    "gtpu_0": (
        "30ff003c04cec0bb4500003c22cb000080019bad0aa002ff481e268c0800995a0300b1016162636465666768696a6b6c6d6e"
        "6f7071727374757677616263646566676869"),
    "gtpu_1": (
        "361a00200000000000000040010868001004cec0bb85001022222222000000000000000000000002"),
    "gtpu_2": (
        "34ff004400800035000000850110010000000000000000000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000"),
    "gtpu_3": (
        "34ff003c00000001000000850100010000000000000000000000000000000000000000000000000000000000000000000000"
        "000000000000000000000000000000000000"),
    "gtpc_2": (
        "4844004deeffc00080001800490001000564000100025100150001000000abe0000000abe0000000abe0000000abe0520001"
        "00065500190022208009100a989a81ffffffff108109100a989a81ffffffff"),
    "sccp_2": (
        "090003050902420e04434324077ee27cc70461060390e874e972cf0101d102092ff26995033940018805011890002789048d"
        "2ad4fe8107394001011c30009f6204000000009f7b020c719f21021004840a0100210b403480000102820201049f5d090000"
        "210a33135009279f50090200210a33135009279f82170124bf82180c9f8215037d7b1f9f8219010f"),
    "sccp_3": (
        "090003050702c20102c20105018e560400"),
    "sccp_4": (
        "090003070b04435604010443430a0105018e430a00"),
    "sigtran_0": (
        "01000701000000d4000600080000000c01150008000000010102001800020000800200080000000180030008000000010116"
        "0008000000010101000800000001011300080000000101140008000000010013000800000001011700080000000c010b0072"
        "626a4804000000106c62a16002010102012e3058840791198996909949820791198996000033044411330a81899610839931"
        "00a73ee8329bfd6681e8e8f41c949e83d4f5391d1406b1dfee73590ea297e774d03d4d4783e2f534bd0c0a83cce53be8fe96"
        "93e7a0b41b94a60300000000"),
}


def кадр(имя):
    return разобрать_пакет(bytes.fromhex("".join(КАДРЫ[имя])), "Linux SLL" if имя.startswith("s1ap") else "Ethernet")


def сообщение(разборщик, имя):
    pdu = bytes.fromhex("".join(СООБЩЕНИЯ[имя]))
    р = Разбор(Пакет(1, 0.0, bytes(8) + pdu + b"\xee" * 4, 8 + len(pdu) + 4, "RAW"))
    return разборщик(р, 8, 8 + len(pdu)), р.п


class КадрыTests(unittest.TestCase):
    #: имя → (стек над IP, инфо)
    ЖДЁМ = {
        "s1ap_3": (["SCTP", "S1AP", "NAS-EPS"],
                   "S1AP downlinkNASTransport (initiatingMessage); NAS: Security mode command, EEA0/EIA1"),
        # Нулевой шифр EEA0: внутри — ESM без обёртки EMM (TS 24.301 9.1), pycrate: ESMInformationRequest.
        "s1ap_5": (["SCTP", "S1AP", "NAS-EPS"],
                   "S1AP downlinkNASTransport (initiatingMessage); NAS: нулевой шифр, ESM information request"),
        "s1ap_11": (["SCTP", "S1AP", "NAS-EPS"], "S1AP uplinkNASTransport (initiatingMessage); NAS: нулевой шифр, "
                    "PDN connectivity request, APN ims"),
        # Метка MTP3 ANSI (24-битные пункты), SCCP ITU. Wireshark с настройкой ITU здесь SCCP не разобрал.
        "s1ap_30": (["SCTP", "M2PA", "MTP3", "SCCP", "TCAP", "MAP"],
                    "GSM MAP sendAuthenticationInfo IMSI 310410010001002"),
        "s1ap_31": (["SCTP", "M2PA", "MTP3", "SCCP", "Данные"], "SCCP XUDT, класс 1, → PC 7, GT 88200130, "
                    "от PC 6, GT 3104100100010020, первый сегмент, осталось 1"),
        "s1ap_32": (["SCTP", "M2PA", "MTP3", "SCCP", "Данные"], "SCCP XUDT, класс 1, → PC 7, GT 88200130, "
                    "от PC 6, GT 3104100100010020, очередной сегмент, осталось 0"),
        # pycrate: SCMG SST, затронута подсистема 7, пункт 900.
        "tcapmap_3": (["SCTP", "M2PA", "MTP3", "SCCP", "SCCPMG"], "SCMG SST SSN 7 (VLR), PC 900"),
        "tcapmap_24": (["SCTP", "M2PA", "MTP3"],
                       "MTP3 OPC 4536 → DPC 3, SNTM (обслуживание), SLS 0, H0=1 H1=1 (SLTM), образец 1112"),
        "tcapmap_25": (["SCTP", "M2PA", "MTP3"],
                       "MTP3 OPC 3 → DPC 4536, SNTM (обслуживание), SLS 0, H0=1 H1=2 (SLTA), образец 1112"),
        # Компоненты неопределённой длины (X.690 8.1.3.6: 6c 80 … 00 00).
        "tcapmap_74": (["SCTP", "M3UA", "SCCP", "TCAP", "MAP"], "GSM MAP sendAuthenticationInfo (результат)"),
        # UDTS: возвращённое сообщение — тоже TCAP.
        "tcapmap_104": (["SCTP", "M3UA", "SCCP", "TCAP", "MAP"], "GSM MAP anyTimeInterrogation (результат)"),
    }

    def test_кадры(self):
        for имя, (стек, инфо) in self.ЖДЁМ.items():
            with self.subTest(имя):
                п = кадр(имя)
                self.assertEqual(стек, п.стек[п.стек.index("SCTP"):])
                self.assertEqual(инфо, п.инфо)
                self.assertEqual([], п.ошибки)

    def test_mtp3_ansi(self):
        п = кадр("s1ap_30")
        mtp3 = next(у for у in п.уровни if у.протокол == "MTP3")
        self.assertEqual("Message Transfer Part Level 3 (ANSI T1.111)", mtp3.полное)
        self.assertEqual(8, mtp3.длина)
        # 83 | 01 00 00 | 0d 00 00 | 04: DPC и OPC участником вперёд, SLS — октет.
        self.assertEqual("ANSI, OPC 0-0-13 → DPC 0-0-1, SCCP, SLS 4", mtp3.итог)
        ф = п.поля_фильтра()
        self.assertEqual(([13], [1], [4], [2]), (ф["mtp3.opc"], ф["mtp3.dpc"], ф["mtp3.sls"], ф["mtp3.network_indicator"]))

    def test_сборка_сегментов(self):
        """Q.714 4.1.1.2: сегменты XUDT собираются в данные пользователя, те — в TCAP/MAP."""
        for первый, второй, канал, инфо in (
                ("s1ap_31", "s1ap_32", "TCAP", "GSM MAP sendAuthenticationInfo (результат)"),
                ("tcapmap_32", "tcapmap_33", "TCAP (SSN 8)", "GSM MAP mo-forwardSM")):
            with self.subTest(первый):
                с = СборщикSCCP()
                п1, п2 = кадр(первый), кадр(второй)
                п1.номер, п2.номер = 7, 9
                self.assertIsNone(с.добавить(п1, п1.поля_фильтра()))
                собрано = с.добавить(п2, п2.поля_фильтра())
                self.assertEqual(([7, 9], канал), (с.последние_номера, с.канал))
                куски = [п.данные[у.смещение:у.смещение + у.длина] for п in (п1, п2) for у in п.уровни
                         if у.протокол == "Данные"]
                self.assertEqual(b"".join(куски), собрано)
                сп = разобрать_пакет(собрано, канал)
                self.assertEqual(["TCAP", "MAP"], сп.стек)
                self.assertEqual(инфо, сп.инфо)


class СборкаВЗахватеTests(unittest.TestCase):
    def test_захват(self):
        with tempfile.TemporaryDirectory() as папка:
            захваты = Захваты(Path(папка))
            кадры = [bytes.fromhex("".join(КАДРЫ[и])) for и in ("tcapmap_24", "tcapmap_32", "tcapmap_33")]
            ид = захваты.создать(владелец=1, имя="sms.pcap", данные=с.pcap(кадры, канал=1))
            for _ in range(200):
                if захваты.прочитать(ид)["состояние"] in ("готово", "ошибка"):
                    break
                time.sleep(0.05)
            self.assertEqual("готово", захваты.прочитать(ид)["состояние"], захваты.прочитать(ид))
            сводки = захваты.сводки(ид)
            self.assertEqual("GSM MAP mo-forwardSM [собран из 2 сегментов SCCP]", сводки[2]["инфо"])
            self.assertEqual(["собран" in с_["инфо"] for с_ in сводки], [False, False, True])
            пакет = захваты.пакет(ид, 3)
            self.assertEqual(["TCAP", "MAP"], [у["протокол"] for у in пакет["собранный"]["уровни"]])
            self.assertEqual([3], [i + 1 for i in захваты.отобрать(ид, "gsm_map")])
            self.assertEqual("TCAP (SSN 8)", захваты.хранилище(ид).собранный_с_каналом(3)[1])


class СообщенияTests(unittest.TestCase):
    def test_gtp_u(self):
        for имя, стек, инфо in (
                ("gtpu_0", ["GTP", "IPv4", "ICMP"], "ICMP эхо-запрос, id 768, номер 45313"),
                # Error Indication: заголовок расширения «порт UDP», IE TEID Data I и Peer Address (IPv6).
                ("gtpu_1", ["GTP"], "GTP Error Indication, TEID 0x00000000, TEID Data I 0x04cec0bb, "
                                    "GTP-U Peer Address 2222:2222::2"),
                ("gtpu_2", ["GTP", "Данные"], "не IP, 64 байт"),
                ("gtpu_3", ["GTP", "Данные"], "не IP, 56 байт")):
            with self.subTest(имя):
                ок, п = сообщение(mobilnye.gtp_u, имя)
                self.assertTrue(ок)
                self.assertEqual((стек, инфо, []), (п.стек, п.инфо, п.ошибки))
        # PDU Session Container (TS 38.415): UL и DL, QFI 1 — как у pycrate.
        for имя, итог in (("gtpu_2", "TEID 0x00800035, UL QFI 1"), ("gtpu_3", "TEID 0x00000001, DL QFI 1")):
            self.assertEqual(итог, сообщение(mobilnye.gtp_u, имя)[1].уровни[0].итог)

    def test_gtpv2_команда(self):
        ок, п = сообщение(mobilnye.gtp_c, "gtpc_2")
        self.assertEqual("GTPv2 Bearer Resource Command, TEID 0xeeffc000, номер 8388632", п.инфо)

    def test_sccp(self):
        for имя, стек, инфо in (
                ("sccp_2", ["SCCP", "ANSI TCAP"],
                 "ANSI TCAP Query With Permission, TID 61060390, Invoke Last id 1 оп. private 2351"),
                ("sccp_3", ["SCCP", "SCCPMG"], "SCMG SSA SSN 142 (RANAP), PC 1110"),
                ("sccp_4", ["SCCP", "SCCPMG"], "SCMG SSA SSN 142 (RANAP), PC 2627")):
            with self.subTest(имя):
                ок, п = сообщение(oks7.sccp, имя)
                self.assertTrue(ок)
                self.assertEqual((стек, инфо, []), (п.стек, п.инфо, п.ошибки))

    def test_sua_нули_после_tcap(self):
        ок, п = сообщение(oks7.sua, "sigtran_0")
        self.assertEqual(["SUA", "TCAP"], п.стек)
        self.assertEqual("TCAP Begin, OTID 00000010, Invoke id 1 оп. 46", п.инфо)
        self.assertEqual(["TCAP: после сообщения 2 нулевых байт (выравнивание внутри длины данных)"], п.ошибки)


def _имя_gtp1(имя):
    """Имя pycrate (NodeAliveReq) → имя таблицы 29.060 (Node Alive Request)."""
    сокращения = {"Req": "Request", "Resp": "Response", "Ctxt": "Context", "Notif": "Notification",
                  "Ind": "Indication", "Ack": "Acknowledge", "Ext": "Extension", "Info": "Information"}
    слова = re.findall(r"Info(?=for)|MBMS|SGSN|SRNS|GPRS|PDP|PDU|RAN|UE|MS|G(?=PDU)|[A-Z][a-z]*|[a-z]+", имя)
    итог = [с if с == "Info" and i and слова[i - 1] == "MS" else сокращения.get(с, с) for i, с in enumerate(слова)]
    return " ".join(итог).replace("G PDU", "G-PDU").replace("De Registration", "De-Registration")


@unittest.skipIf(GTPMsgType is None, "эталон pycrate не установлен")
class ТаблицыTests(unittest.TestCase):
    def test_gtp(self):
        self.assertEqual({e.value: _имя_gtp1(e.name) for e in GTPMsgType}, mobilnye.GTP1_ТИПЫ)
        self.assertEqual({e.value: _имя_gtp1(e.name) for e in GTPUType}, mobilnye.GTPU_ТИПЫ)
        self.assertEqual({к: з.removesuffix(" message") for к, з in GTPCMsgType_dict.items()
                          if not з.startswith("Reserved")}, mobilnye.GTP2_ТИПЫ)
        self.assertEqual({к: з for к, з in GTPUNextExtHeader_dict.items() if к}, mobilnye.GTPU_РАСШИРЕНИЯ)

    def test_pfcp(self):
        self.assertEqual({к: з.removeprefix("PFCP ") for к, з in PFCPMsgType_dict.items() if к}, mobilnye.PFCP_ТИПЫ)

    def test_sccp(self):
        self.assertEqual(_SCCPType_dict, oks7.SCCP_ТИПЫ)
        self.assertEqual({к: з.split()[0] for к, з in _SCMGType_dict.items()}, oks7.SCMG_ТИПЫ)
        self.assertEqual(set(_RetCause_dict), set(oks7.SCCP_ВОЗВРАТ))


if __name__ == "__main__":
    unittest.main()
