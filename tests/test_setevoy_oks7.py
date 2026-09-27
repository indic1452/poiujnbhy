"""ОКС-7 и SIGTRAN: пакеты собраны здесь по RFC 4666/3331/4165/3868/4233 и ITU-T
Q.703/Q.704/Q.713/Q.773/Q.763/Q.931/Q.921 — без функций разборщика."""

import random
import struct
import threading
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import прочитать_захват, разобрать_пакет
from reportgen.setevoy.filtr import отобрать
from reportgen.setevoy.pole import Пакет
from reportgen.setevoy.protokoly import oks7
from reportgen.setevoy.razbor import Разбор
from reportgen.setevoy.statistika import уровень_протокола

# -- сборка -------------------------------------------------------------------------------------


def crc32c(данные):
    crc = 0xFFFFFFFF
    for б in данные:
        crc ^= б
        for _ in range(8):
            crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def crc16_x25(данные):
    """FCS Q.703 2.2.8 / HDLC: CRC-16 x^16+x^12+x^5+1, отражённая, с дополнением."""
    crc = 0xFFFF
    for б in данные:
        crc ^= б
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def sctp(нагрузка, ppid, sport=40000, dport=2905):
    """SCTP (RFC 4960): общий заголовок + кусок DATA (флаги B/E), CRC32c."""
    кусок = struct.pack(">BBHIHHI", 0, 3, 16 + len(нагрузка), 1, 0, 0, ppid) + нагрузка
    кусок += b"\0" * (-len(кусок) % 4)
    заголовок = struct.pack(">HHII", sport, dport, 0x1234ABCD, 0)
    сумма = crc32c(заголовок + кусок)
    return заголовок[:8] + struct.pack("<I", сумма) + кусок


def поверх_sctp(нагрузка, ppid, sport=40000, dport=2905):
    return с.eth(с.ip(sctp(нагрузка, ppid, sport, dport), 132))


НАЧАЛО = 14 + 20 + 12 + 16         # Ethernet + IPv4 + SCTP + заголовок DATA


def параметр(тег, значение):
    """TLV SIGTRAN (RFC 4666 3.2): длина без выравнивания, выравнивание нулями."""
    return struct.pack(">HH", тег, 4 + len(значение)) + значение + b"\0" * (-len(значение) % 4)


def сигтран(класс, тип, *параметры):
    тело = b"".join(параметры)
    return struct.pack(">BBBBI", 1, 0, класс, тип, 8 + len(тело)) + тело


def bcd(цифры, заполнитель=0):
    """Цифры BCD, первая — в младшем полубайте; нечётное число — заполнитель в старшем."""
    полубайты = [int(ц, 16) for ц in цифры] + ([заполнитель] if len(цифры) % 2 else [])
    return bytes(полубайты[i] | (полубайты[i + 1] << 4) for i in range(0, len(полубайты), 2))


def ber(тег, значение):
    дл = len(значение)
    if дл < 0x80:
        return bytes([тег, дл]) + значение
    if дл < 0x100:
        return bytes([тег, 0x81, дл]) + значение
    return bytes([тег, 0x82]) + struct.pack(">H", дл) + значение


def метка(dpc, opc, sls):
    """Метка маршрутизации ITU (Q.704 2.2): DPC 14, OPC 14, SLS 4 бита, младшим вперёд."""
    return struct.pack("<I", dpc | (opc << 14) | (sls << 28))


def sccp_udt(вызываемый, вызывающий, данные_, класс=0x80, тип=0x09, возврат=None):
    """UDT (Q.713 4.10): тип, класс, три указателя, переменные параметры."""
    перем = [вызываемый, вызывающий, данные_]
    фикс = bytes([класс]) if возврат is None else bytes([возврат])
    указатели, тело = b"", b""
    for i, п in enumerate(перем):
        # Указатель i стоит на месте 1 + len(фикс) + i; цель — после всех указателей.
        до_цели = (len(перем) - i) + len(тело)
        указатели += bytes([до_цели])
        тело += bytes([len(п)]) + п
    return bytes([тип]) + фикс + указатели + тело


def адрес_gt(ssn, цифры, tt=0, np=1, nai=4):
    """Адрес (Q.713 3.4): маршрут по GT, GTI 4, есть SSN; GT: TT, NP/ES, NAI, цифры BCD."""
    es = 1 if len(цифры) % 2 else 2
    return bytes([(4 << 2) | 0x02, ssn, tt, (np << 4) | es, nai]) + bcd(цифры)


def адрес_pc(пк, ssn):
    """Адрес: маршрут по SSN, GTI 0, есть PC (2 октета, младший вперёд) и SSN."""
    return bytes([0x40 | 0x02 | 0x01]) + struct.pack("<H", пк) + bytes([ssn])


IMSI = "250011234567890"
AC_UPDATE_LOCATION_V3 = bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x01, 0x03])


def диалог(контекст):
    """Диалог (Q.773 4.2.3): EXTERNAL { dialogue-as-id, [0] AARQ { версия, [1] контекст } }."""
    aarq = ber(0x60, ber(0x80, b"\x07\x80") + ber(0xA1, ber(0x06, контекст)))
    внешний = ber(0x28, ber(0x06, bytes([0x00, 0x11, 0x86, 0x05, 0x01, 0x01, 0x01])) + ber(0xA0, aarq))
    return ber(0x6B, внешний)


def tcap_begin_update_location(otid=b"\x0a\x0b\x0c\x0d", контекст=AC_UPDATE_LOCATION_V3):
    """Begin (Q.773) с Invoke updateLocation (TS 29.002: opCode 2, UpdateLocationArg)."""
    аргумент = ber(0x30, ber(0x04, bcd(IMSI, 0xF)) + ber(0x81, b"\x91" + bcd("79160000001", 0xF))
                   + ber(0x04, b"\x91" + bcd("79160000002", 0xF)))
    invoke = ber(0xA1, ber(0x02, b"\x01") + ber(0x02, b"\x02") + аргумент)
    части = ber(0x48, otid) + (диалог(контекст) if контекст else b"") + ber(0x6C, invoke)
    return ber(0x62, части)


def tcap_end_result(dtid=b"\x0a\x0b\x0c\x0d"):
    результат = ber(0xA2, ber(0x02, b"\x01") + ber(0x30, ber(0x02, b"\x02") + ber(0x30, b"")))
    return ber(0x64, ber(0x49, dtid) + ber(0x6C, результат))


def m3ua_data(opc, dpc, si, пользователь, ni=2, sls=5):
    """M3UA DATA (RFC 4666 3.3.1): Routing Context + Protocol Data (OPC, DPC, SI, NI, MP, SLS)."""
    pd = struct.pack(">IIBBBB", opc, dpc, si, ni, 0, sls) + пользователь
    return сигтран(1, 1, параметр(0x0006, struct.pack(">I", 7)), параметр(0x0210, pd))


def isup_iam(cic=17, вызываемый="4951234567", вызывающий="74951112233"):
    """IAM (Q.763 табл. 32): NCI, FCI, CPC, TMR; указатель на номер вызываемого, на необяз. часть."""
    номер_к = bytes([(0x80 if len(вызываемый) % 2 else 0) | 0x03, 0x10]) + bcd(вызываемый)
    номер_от = bytes([(0x80 if len(вызывающий) % 2 else 0) | 0x04, 0x11]) + bcd(вызывающий)
    необяз = bytes([0x0A, len(номер_от)]) + номер_от + b"\x00"
    ук_номер = 2                                      # от своего места: через указатель необяз. части
    ук_необяз = 1 + 1 + len(номер_к)
    return struct.pack("<H", cic) + bytes([0x01, 0x00, 0x60, 0x01, 0x0A, 0x00, ук_номер, ук_необяз]) \
        + bytes([len(номер_к)]) + номер_к + необяз


def isup_rel(cic=17, причина=16):
    """REL (Q.763 табл. 33): указатель на причину (Q.850: расширение, ITU, место; причина)."""
    return struct.pack("<H", cic) + bytes([0x0C, 2, 0, 2, 0x80, 0x80 | причина])


def q931_setup():
    """SETUP (Q.931 3.1.14): ссылка вызова 2 октета, Bearer capability, номера (IA5)."""
    return bytes([0x08, 0x02, 0x00, 0x01, 0x05]) \
        + bytes([0x04, 0x03, 0x80, 0x90, 0xA3]) \
        + bytes([0x18, 0x03, 0xA9, 0x83, 0x81]) \
        + bytes([0x6C, 2 + 5, 0x21, 0x83]) + b"12345" \
        + bytes([0x70, 1 + 7, 0xA1]) + b"4951234"


def q931_release_complete():
    return bytes([0x08, 0x01, 0x81, 0x5A, 0x08, 0x02, 0x80, 0x90])


def поля(пакет):
    return пакет.поля_фильтра()


def поле(пакет, ключ):
    """Первое поле с ключом (в глубину) — для проверки места в байтах."""
    def обойти(список):
        for п in список:
            if п.ключ == ключ:
                return п
            найдено = обойти(п.дети)
            if найдено:
                return найдено
        return None
    for у in пакет.уровни:
        найдено = обойти(у.поля)
        if найдено:
            return найдено
    raise AssertionError(f"нет поля {ключ}")


def места(пакет, ключ):
    """Смещения всех полей с ключом."""
    итог = []

    def обойти(список):
        for п in список:
            if п.ключ == ключ:
                итог.append(п.смещение)
            обойти(п.дети)
    for у in пакет.уровни:
        обойти(у.поля)
    return итог


def все_обрывы(тест, кадр, канал="Ethernet"):
    """Каждая длина обрыва: разбор не падает, а с начала своих данных обрыв
    отмечается как обрыв (не «разбор прерван»). Над SCTP свои данные начинаются
    за заголовком куска DATA: кусок, оборванный внутри заголовка, — дело SCTP."""
    свои = НАЧАЛО if канал == "Ethernet" else 0
    for длина in range(1, len(кадр)):
        with тест.subTest(длина=длина):
            п = разобрать_пакет(кадр[:длина], канал)
            тест.assertTrue(п.уровни or п.ошибки)
            if длина >= свои:
                тест.assertFalse(any("разбор прерван" in о for о in п.ошибки), п.ошибки)


# -- SIGTRAN -----------------------------------------------------------------------------------------


class M3UATests(unittest.TestCase):
    UDT = sccp_udt(адрес_gt(6, "79161234567"), адрес_pc(1234, 7), tcap_begin_update_location())
    КАДР = поверх_sctp(m3ua_data(200, 100, 3, UDT), 3)

    def test_data_sccp_tcap_map(self):
        п = разобрать_пакет(self.КАДР)
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "M3UA", "SCCP", "TCAP", "MAP"], п.стек)
        self.assertEqual([], п.ошибки)
        ф = поля(п)
        self.assertEqual(([1], [0], [1], [200], [100], [3], [2], [5], [7]),
                         (ф["m3ua.version"], ф["m3ua.reserved"], ф["m3ua.message_class"], ф["m3ua.protocol_data_opc"],
                          ф["m3ua.protocol_data_dpc"], ф["m3ua.protocol_data_si"], ф["m3ua.protocol_data_ni"],
                          ф["m3ua.protocol_data_sls"], ф["m3ua.routing_context"]))
        self.assertEqual(([200], [100]), (ф["mtp3.opc"], ф["mtp3.dpc"]))
        # Места в байтах: заголовок M3UA сразу за куском DATA.
        self.assertEqual((НАЧАЛО + 2, 1), (поле(п, "m3ua.message_class").смещение, поле(п, "m3ua.message_class").длина))
        self.assertEqual((НАЧАЛО + 4, 4), (поле(п, "m3ua.message_length").смещение, поле(п, "m3ua.message_length").длина))
        # Routing Context: 8 байт заголовка + 4 байта TLV; Protocol Data — следом.
        self.assertEqual(НАЧАЛО + 12, поле(п, "m3ua.routing_context").смещение)
        self.assertEqual([НАЧАЛО + 8, НАЧАЛО + 16], места(п, "m3ua.parameter_tag"))
        pd = НАЧАЛО + 16 + 4
        self.assertEqual((pd, 4), (поле(п, "m3ua.protocol_data_opc").смещение, поле(п, "m3ua.protocol_data_opc").длина))
        self.assertEqual(pd + 8, поле(п, "m3ua.protocol_data_si").смещение)
        # SCCP: адреса, цифры GT, SSN.
        self.assertEqual(["79161234567"], ф["sccp.called.digits"])
        self.assertEqual(([6], [7], [1234]), (ф["sccp.called.ssn"], ф["sccp.calling.ssn"], ф["sccp.calling.pc"]))
        self.assertEqual(([4], [1], [4]), (ф["sccp.called.gti"], ф["sccp.called.np"], ф["sccp.called.nai"]))
        sccp = pd + 12
        self.assertEqual(sccp, поле(п, "sccp.message_type").смещение)
        # Вызываемый адрес: указатель 3 от места 2 → длина на 5, индикатор на 6, SSN на 7.
        self.assertEqual((sccp + 7, 1), (поле(п, "sccp.called.ssn").смещение, поле(п, "sccp.called.ssn").длина))
        self.assertEqual(sccp + 11, поле(п, "sccp.called.digits").смещение)
        # TCAP и MAP.
        self.assertEqual((["0a0b0c0d"], [1], [2]), (ф["tcap.otid"], ф["tcap.invokeID"], ф["tcap.opCode"]))
        self.assertEqual(["0.4.0.0.1.0.1.3"], ф["tcap.application_context"])
        self.assertEqual(([2], [IMSI]), (ф["gsm_map.opcode"], ф["gsm_map.imsi"]))
        self.assertIn("updateLocation", п.инфо)

    def test_фильтр(self):
        пакеты = [поля(разобрать_пакет(к)) for к in (
            self.КАДР, поверх_sctp(m3ua_data(300, 400, 5, isup_rel()), 3),
            с.eth(с.ip(с.udp(b"x" * 20, 1000, 2000), 17)))]
        for текст, ждём in (("m3ua", [0, 1]), ("sccp", [0]), ("isup", [1]), ("tcap and map", [0]),
                            ("m3ua.protocol_data_si == 5", [1]), ("mtp3.opc == 300", [1]),
                            ("sccp.called.ssn == 6", [0]), ("gsm_map.opcode == 2", [0]),
                            ('gsm_map.imsi == "250011234567890"', [0]), ("m3ua.message_class == 1", [0, 1]),
                            ("m3ua.routing_context == 7", [0, 1]), ("isup.cause_indicator == 16", [1])):
            with self.subTest(текст=текст):
                self.assertEqual(ждём, отобрать(пакеты, текст))

    def test_управление_asp_и_ошибка(self):
        п = разобрать_пакет(поверх_sctp(сигтран(3, 1, параметр(0x0011, struct.pack(">I", 42)),
                                                  параметр(0x0004, b"sgw-1")), 3))
        self.assertEqual("M3UA", п.протокол)
        self.assertEqual(([3], [1], [42], ["sgw-1"]), (поля(п)["m3ua.message_class"], поля(п)["m3ua.message_type"],
                                                       поля(п)["m3ua.asp_identifier"], поля(п)["m3ua.info_string"]))
        self.assertIn("ASPSM ASPUP", п.инфо)
        # Info String «sgw-1» — 5 байт: выравнивание тремя нулями, длина параметра 9.
        self.assertEqual([8, 9], поля(п)["m3ua.parameter_length"])
        ошибка = разобрать_пакет(поверх_sctp(сигтран(0, 0, параметр(0x000C, struct.pack(">I", 0x19))), 3))
        self.assertEqual(["0x19 (Invalid Routing Context)"], поля(ошибка)["m3ua.error_code"][:0] +
                         [поле(ошибка, "m3ua.error_code").текст])
        состояние = разобрать_пакет(поверх_sctp(сигтран(0, 1, параметр(0x000D, struct.pack(">HH", 1, 3))), 3))
        self.assertIn("AS-ACTIVE", поле(состояние, "m3ua.status_info").текст)

    def test_по_порту_без_ppid(self):
        п = разобрать_пакет(поверх_sctp(m3ua_data(1, 2, 5, isup_rel()), 0, 2905, 2905))
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "M3UA", "ISUP"], п.стек)

    def test_не_m3ua(self):
        хороший = m3ua_data(1, 2, 5, isup_rel())
        for испорчено in (b"\x02" + хороший[1:],                          # версия 2
                          хороший[:1] + b"\x01" + хороший[2:],            # резерв не 0
                          хороший[:2] + b"\x05" + хороший[3:],            # класс QPTM — не M3UA
                          хороший[:3] + b"\x02" + хороший[4:],            # Transfer: тип 2 не бывает
                          хороший[:4] + struct.pack(">I", len(хороший) + 4) + хороший[8:],   # длина
                          хороший[:10] + struct.pack(">H", 3) + хороший[12:],             # длина TLV < 4
                          хороший + b"\0\0\0\0",                           # хвост за длиной
                          ):
            with self.subTest(испорчено=испорчено[:12].hex()):
                п = разобрать_пакет(поверх_sctp(испорчено, 3))
                self.assertNotIn("M3UA", п.стек)
        # Ненулевое выравнивание параметра.
        кривой = сигтран(3, 1, struct.pack(">HH", 0x0004, 9) + b"abcde" + b"\x01\0\0")
        self.assertNotIn("M3UA", разобрать_пакет(поверх_sctp(кривой, 3)).стек)
        ровный = сигтран(3, 1, struct.pack(">HH", 0x0004, 9) + b"abcde" + b"\0\0\0")
        self.assertIn("M3UA", разобрать_пакет(поверх_sctp(ровный, 3)).стек)
        # Последний параметр без выравнивания допустим (длина сообщения — ровно до конца).
        без = сигтран(3, 1, struct.pack(">HH", 0x0004, 9) + b"abcde")
        self.assertIn("M3UA", разобрать_пакет(поверх_sctp(без, 3)).стек)

    def test_обрыв(self):
        все_обрывы(self, self.КАДР)
        # Оборван внутри TCAP: M3UA и SCCP остаются, обрыв отмечен.
        п = разобрать_пакет(self.КАДР[:-10])
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "M3UA", "SCCP"], п.стек[:5])
        self.assertTrue(any("оборван" in о for о in п.ошибки))
        # Оборван внутри параметров M3UA: заголовок M3UA всё равно разобран.
        п = разобрать_пакет(self.КАДР[:НАЧАЛО + 14])
        self.assertIn("M3UA", п.стек)


class M2PATests(unittest.TestCase):
    MTP3 = bytes([0x83]) + метка(100, 200, 3) + sccp_udt(адрес_gt(6, "79161234567"), адрес_pc(1234, 7),
                                                          tcap_begin_update_location())

    @staticmethod
    def m2pa(тип, тело, bsn=0x000102, fsn=0x000304):
        return struct.pack(">BBBBI", 1, 0, 11, тип, 16 + len(тело)) + struct.pack(">II", bsn, fsn) + тело

    def test_user_data_и_link_status(self):
        п = разобрать_пакет(поверх_sctp(self.m2pa(1, b"\x00" + self.MTP3), 5, 3565, 3565))
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "M2PA", "MTP3", "SCCP", "TCAP", "MAP"], п.стек)
        ф = поля(п)
        self.assertEqual(([0x102], [0x304], [3], [200], [100], [3], [2]),
                         (ф["m2pa.bsn"], ф["m2pa.fsn"], ф["mtp3.service_indicator"], ф["mtp3.opc"], ф["mtp3.dpc"],
                          ф["mtp3.sls"], ф["mtp3.network_indicator"]))
        self.assertEqual((НАЧАЛО + 9, 3), (поле(п, "m2pa.bsn").смещение, поле(п, "m2pa.bsn").длина))
        self.assertEqual(НАЧАЛО + 17, поле(п, "mtp3.sio").смещение)
        статус = разобрать_пакет(поверх_sctp(self.m2pa(2, struct.pack(">I", 4)), 5))
        self.assertEqual("M2PA", статус.протокол)
        self.assertEqual([4], поля(статус)["m2pa.status"])
        self.assertIn("Ready", статус.инфо)
        self.assertEqual([0, 1], отобрать([поля(статус), поля(п)], "m2pa"))
        self.assertEqual([1], отобрать([поля(статус), поля(п)], "mtp3.dpc == 100"))

    def test_не_m2pa(self):
        for испорчено in (self.m2pa(3, b""), self.m2pa(2, struct.pack(">I", 10)), self.m2pa(1, b"\x00\x83\x01"),
                          self.m2pa(1, b"", bsn=0x01000000), b"\x01\x00\x0a\x01" + self.m2pa(1, b"")[4:]):
            with self.subTest(испорчено=испорчено.hex()):
                self.assertNotIn("M2PA", разобрать_пакет(поверх_sctp(испорчено, 5, 3565, 3565)).стек)

    def test_обрыв(self):
        все_обрывы(self, поверх_sctp(self.m2pa(1, b"\x00" + self.MTP3), 5))


class M2UATests(unittest.TestCase):
    MTP3 = bytes([0x85]) + метка(10, 20, 1) + isup_iam()
    СООБЩЕНИЕ = сигтран(6, 1, параметр(0x0001, struct.pack(">I", 3)), параметр(0x0300, MTP3))

    def test_data(self):
        п = разобрать_пакет(поверх_sctp(self.СООБЩЕНИЕ, 2, 2904, 2904))
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "M2UA", "MTP3", "ISUP"], п.стек)
        ф = поля(п)
        self.assertEqual(([6], [1], [3], [5]), (ф["m2ua.message_class"], ф["m2ua.message_type"],
                                                ф["m2ua.interface_identifier_int"], ф["mtp3.service_indicator"]))
        self.assertEqual(НАЧАЛО + 12, поле(п, "m2ua.interface_identifier_int").смещение)
        self.assertEqual(НАЧАЛО + 20, поле(п, "mtp3.sio").смещение)
        self.assertEqual(["4951234567"], ф["isup.called"])
        self.assertEqual([0], отобрать([ф], "m2ua and isup.cic == 17"))
        # Класс SSNM у M2UA не бывает.
        self.assertNotIn("M2UA", разобрать_пакет(поверх_sctp(сигтран(2, 1), 2)).стек)

    def test_обрыв(self):
        все_обрывы(self, поверх_sctp(self.СООБЩЕНИЕ, 2))


class SUATests(unittest.TestCase):
    @staticmethod
    def адрес(ri, *под):
        return struct.pack(">HH", ri, 0x0007) + b"".join(под)

    def сообщение(self):
        gt = struct.pack(">BBBBBBBB", 0, 0, 0, 4, 11, 0, 1, 4) + bcd("79161234567", 0)
        источник = self.адрес(2, параметр(0x8002, struct.pack(">I", 1234)), параметр(0x8003, b"\0\0\0\x07"))
        получатель = self.адрес(1, параметр(0x8001, gt), параметр(0x8003, b"\0\0\0\x06"))
        return сигтран(7, 1, параметр(0x0006, struct.pack(">I", 1)), параметр(0x0115, b"\0\0\0\x80"),
                       параметр(0x0102, источник), параметр(0x0103, получатель),
                       параметр(0x0116, b"\0\0\0\0"), параметр(0x010B, tcap_begin_update_location(контекст=None)))

    def test_cldt_tcap_map(self):
        п = разобрать_пакет(поверх_sctp(self.сообщение(), 4, 14001, 14001))
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "SUA", "TCAP", "MAP"], п.стек)
        ф = поля(п)
        self.assertEqual(([7], [1], [0], [1234], [7], [6], ["79161234567"]),
                         (ф["sua.message_class"], ф["sua.message_type"], ф["sua.protocol_class"], ф["sua.source.pc"],
                          ф["sua.source.ssn"], ф["sua.destination.ssn"], ф["sua.destination.gt_digits"]))
        # Без контекста приложения MAP узнаётся по SSN вызываемого (6 — HLR).
        self.assertEqual([2], ф["gsm_map.opcode"])
        self.assertEqual(НАЧАЛО + 12, поле(п, "sua.routing_context").смещение)
        self.assertEqual([0], отобрать([ф], "sua and sua.destination.ssn == 6"))

    def test_обрыв(self):
        все_обрывы(self, поверх_sctp(self.сообщение(), 4))


class IUATests(unittest.TestCase):
    СООБЩЕНИЕ = сигтран(5, 1, параметр(0x0001, struct.pack(">I", 1)), параметр(0x0005, bytes([0 << 2, (0 << 1) | 1, 0, 0])),
                        параметр(0x000E, q931_setup()))

    def test_data_request_q931(self):
        п = разобрать_пакет(поверх_sctp(self.СООБЩЕНИЕ, 1, 9900, 9900))
        self.assertEqual(["Ethernet", "IPv4", "SCTP", "IUA", "Q.931"], п.стек)
        ф = поля(п)
        self.assertEqual(([5], [1], [0], [0]), (ф["iua.message_class"], ф["iua.message_type"], ф["iua.dlci.sapi"],
                                                ф["iua.dlci.tei"]))
        self.assertEqual(([5], [1], ["12345"], ["4951234"]),
                         (ф["q931.message_type"], ф["q931.call_ref"], ф["q931.calling_party_number.digits"],
                          ф["q931.called_party_number.digits"]))
        q = НАЧАЛО + 8 + 8 + 8 + 4
        self.assertEqual((q + 4, 1), (поле(п, "q931.message_type").смещение, поле(п, "q931.message_type").длина))
        self.assertEqual("Q.931 SETUP, ссылка 0x1, от 12345, → 4951234", п.инфо)
        for текст in ("q931", "q.931", "iua", "q931.called_party_number.digits == 4951234"):
            self.assertEqual([0], отобрать([ф], текст), текст)

    def test_обрыв(self):
        все_обрывы(self, поверх_sctp(self.СООБЩЕНИЕ, 1))


# -- MTP2 / MTP3 / SCCP / LAPD как каналы ------------------------------------------------------------------


class MTP2Tests(unittest.TestCase):
    MTP3 = bytes([0x83]) + метка(100, 200, 3) + sccp_udt(адрес_gt(6, "79161234567"), адрес_pc(1234, 7),
                                                          tcap_begin_update_location())

    @staticmethod
    def единица(bsn, bib, fsn, fib, тело):
        li = min(len(тело), 63)
        return bytes([bsn | (bib << 7), fsn | (fib << 7), li]) + тело

    def test_msu_fisu_lssu(self):
        п = разобрать_пакет(self.единица(10, 1, 20, 1, self.MTP3), "MTP2")
        self.assertEqual(["MTP2", "MTP3", "SCCP", "TCAP", "MAP"], п.стек)
        self.assertEqual([], п.ошибки)
        ф = поля(п)
        self.assertEqual(([10], [1], [20], [1], [63]), (ф["mtp2.bsn"], ф["mtp2.bib"], ф["mtp2.fsn"], ф["mtp2.fib"],
                                                       ф["mtp2.li"]))
        self.assertEqual((3, 1), (поле(п, "mtp3.sio").смещение, поле(п, "mtp3.sio").длина))
        self.assertEqual(("200", "100"), (п.источник, п.получатель))
        fisu = разобрать_пакет(self.единица(1, 0, 2, 0, b""), "MTP2")
        self.assertEqual((["MTP2"], "MTP2 FISU, BSN 1/0, FSN 2/0"), (fisu.стек, fisu.инфо))
        lssu = разобрать_пакет(self.единица(1, 0, 2, 0, b"\x01"), "MTP2")
        self.assertIn("LSSU SIN", lssu.инфо)
        self.assertEqual([1], поля(lssu)["mtp2.sf"])

    def test_fcs_и_несходящийся_li(self):
        единица = self.единица(3, 0, 4, 0, bytes([0x85]) + метка(1, 2, 0) + isup_rel())
        с_fcs = единица + struct.pack("<H", crc16_x25(единица))
        п = разобрать_пакет(с_fcs, "MTP2")
        self.assertEqual(["MTP2", "MTP3", "ISUP"], п.стек)
        self.assertEqual([crc16_x25(единица)], поля(п)["mtp2.fcs_16"])
        испорчено = bytearray(с_fcs)
        испорчено[-1] ^= 1
        п = разобрать_пакет(bytes(испорчено), "MTP2")
        self.assertNotIn("MTP3", п.стек)
        self.assertTrue(any("LI" in о for о in п.ошибки))
        # Запасные биты октета LI в ITU — нули.
        запас = bytearray(единица)
        запас[2] |= 0x40
        self.assertNotIn("MTP3", разобрать_пакет(bytes(запас), "MTP2").стек)
        # Захват обрезан по snaplen: LI сверяется с исходной длиной.
        п = разобрать_пакет(единица[:8], "MTP2", исходная_длина=len(единица))
        self.assertEqual(["MTP2", "MTP3"], п.стек)
        self.assertTrue(any("оборван" in о for о in п.ошибки))

    def test_pcap_каналы(self):
        файл = с.pcap([self.единица(1, 0, 1, 0, self.MTP3)], канал=140)
        захват = прочитать_захват(данные=файл)
        self.assertEqual("MTP2", захват.записи[0].канал)
        for номер, канал in ((141, "MTP3"), (142, "SCCP"), (203, "LAPD"), (177, "Linux LAPD")):
            self.assertEqual(канал, прочитать_захват(данные=с.pcap([b"\0" * 8], канал=номер)).записи[0].канал)

    def test_обрыв(self):
        все_обрывы(self, self.единица(10, 1, 20, 1, self.MTP3), "MTP2")


class MTP3SCCPTests(unittest.TestCase):
    def test_mtp3_канал_isup(self):
        п = разобрать_пакет(bytes([0x85]) + метка(5, 6, 9) + isup_iam(cic=300), "MTP3")
        self.assertEqual(["MTP3", "ISUP"], п.стек)
        ф = поля(п)
        self.assertEqual(([6], [5], [9], [300], [1]), (ф["mtp3.opc"], ф["mtp3.dpc"], ф["mtp3.sls"], ф["isup.cic"],
                                                       ф["isup.message_type"]))
        self.assertEqual(["4951234567"], ф["isup.called"])
        self.assertEqual(["74951112233"], ф["isup.calling"])
        self.assertEqual([0x0A], ф["isup.calling_partys_category"])
        # Номер вызываемого: CIC 2 + тип 1 + фикс. 5 + указатели 2 → длина на 10, признаки на 11.
        self.assertEqual((5 + 11 + 2, 5), (поле(п, "isup.called").смещение, поле(п, "isup.called").длина))
        self.assertEqual("ISUP IAM, CIC 300, → 4951234567, от 74951112233", п.инфо)

    def test_mtp3_управление(self):
        # TFP (Q.704 15.2: H0=4, H1=1) — заголовок в одном октете, H0 в младших битах.
        п = разобрать_пакет(bytes([0x80]) + метка(1, 2, 0) + bytes([0x14]) + struct.pack("<H", 77), "MTP3")
        self.assertEqual(([4], [1]), (поля(п)["mtp3mg.h0"], поля(п)["mtp3mg.h1"]))
        self.assertIn("(TFP)", п.инфо)

    def test_isup_сообщения(self):
        for тело, тип, ждём in ((isup_rel(), "REL", "причина 16"),
                                (struct.pack("<H", 5) + bytes([0x10, 0]), "RLC", "CIC 5"),
                                (struct.pack("<H", 5) + bytes([0x09, 0]), "ANM", "CIC 5"),
                                (struct.pack("<H", 5) + bytes([0x06, 0x16, 0x14, 0]), "ACM", "CIC 5"),
                                (struct.pack("<H", 5) + bytes([0x2C, 0x01, 0]), "CPG", "ALERTING"),
                                (struct.pack("<H", 5) + bytes([0x13]), "BLO", "CIC 5")):
            with self.subTest(тип=тип):
                п = разобрать_пакет(bytes([0x85]) + метка(1, 2, 0) + тело, "MTP3")
                self.assertEqual("ISUP", п.протокол)
                self.assertIn(тип, п.инфо)
                self.assertIn(ждём, п.инфо)
        # Нарушения строения: лишний байт, указатель мимо, нет конца необязательных, неизвестный тип.
        for тело in (isup_rel() + b"\0", struct.pack("<H", 5) + bytes([0x0C, 9, 0, 2, 0x80, 0x90]),
                     struct.pack("<H", 5) + bytes([0x10, 1, 0x0A, 1, 5]), struct.pack("<H", 5) + bytes([0x13, 0]),
                     struct.pack("<H", 5) + bytes([0x7F, 0]), isup_iam()[:-1]):
            with self.subTest(тело=тело.hex()):
                п = разобрать_пакет(bytes([0x85]) + метка(1, 2, 0) + тело, "MTP3")
                self.assertEqual(["MTP3", "Данные"], п.стек)
                self.assertEqual([], п.ошибки)

    def test_sccp_канал_xudt_и_соединения(self):
        xudt = sccp_udt(адрес_pc(10, 146), адрес_pc(20, 146), tcap_end_result(), тип=0x11)
        # XUDT (Q.713 4.18): класс, счётчик переходов, 4 указателя (необяз. часть — 0).
        xudt = bytes([0x11, 0x01, 0x0F]) + bytes([4, 4 + 5 - 1, 4 + 10 - 2, 0]) + xudt[5:]
        п = разобрать_пакет(xudt, "SCCP")
        self.assertEqual(["SCCP", "TCAP", "CAMEL"], п.стек)                # SSN 146 — CAP, не MAP
        self.assertEqual(([15], [1], ["0a0b0c0d"]), (поля(п)["sccp.hops"], поля(п)["sccp.class"], поля(п)["tcap.dtid"]))
        self.assertEqual(["ReturnResultLast id 1 оп. 2"], [ф.текст for ф in п.уровни[-2].поля if ф.ключ == "tcap.component"])
        # CR (4.2): SLR, класс 2, указатель на вызываемого, на необязательную часть (вызывающий, конец).
        вызываемый, вызывающий = адрес_pc(10, 254), адрес_pc(20, 254)
        cr = bytes([0x01, 0x01, 0x02, 0x03, 0x02, 2, 1 + 1 + len(вызываемый)]) + bytes([len(вызываемый)]) \
            + вызываемый + bytes([0x04, len(вызывающий)]) + вызывающий + b"\x00"
        п = разобрать_пакет(cr, "SCCP")
        self.assertEqual("SCCP", п.протокол)
        self.assertEqual(([0x030201], [2], [254, 254]), (поля(п)["sccp.slr"], поля(п)["sccp.class"], поля(п)["sccp.ssn"]))
        self.assertEqual((1, 3), (поле(п, "sccp.slr").смещение, поле(п, "sccp.slr").длина))
        cc = bytes([0x02, 1, 2, 3, 4, 5, 6, 0x02, 0])
        self.assertEqual([0x060504], поля(разобрать_пакет(cc, "SCCP"))["sccp.slr"])
        rlsd = bytes([0x04, 1, 2, 3, 4, 5, 6, 0x03, 0])
        self.assertEqual([3], поля(разобрать_пакет(rlsd, "SCCP"))["sccp.release_cause"])
        rlc = bytes([0x05, 1, 2, 3, 4, 5, 6])
        self.assertIn("RLC", разобрать_пакет(rlc, "SCCP").инфо)
        dt1 = bytes([0x06, 1, 2, 3, 0, 1, 3]) + b"abc"
        п = разобрать_пакет(dt1, "SCCP")
        self.assertEqual(["SCCP", "Данные"], п.стек)
        cref = bytes([0x03, 1, 2, 3, 0x01, 0])
        self.assertEqual([1], поля(разобрать_пакет(cref, "SCCP"))["sccp.refusal_cause"])
        # Нарушения: класс 2 в UDT, класс 0 в CR, лишний байт, указатель в никуда, GT без цифр.
        udt = sccp_udt(адрес_pc(10, 6), адрес_pc(20, 6), b"\x01")
        for плохо in (udt[:1] + b"\x02" + udt[2:], cr[:4] + b"\x00" + cr[5:], udt + b"\0", rlc + b"\0",
                      udt[:2] + b"\x40" + udt[3:], sccp_udt(bytes([0x12, 6, 0, 0x11, 4]), адрес_pc(1, 6), b"\1"),
                      sccp_udt(bytes([0x02]), адрес_pc(1, 6), b"\1"), bytes([0x09, 0, 3, 3, 3, 0, 0, 0])):
            with self.subTest(плохо=плохо.hex()):
                self.assertEqual(["Данные"], разобрать_пакет(плохо, "SCCP").стек)
        все_обрывы(self, xudt, "SCCP")
        все_обрывы(self, cr, "SCCP")

    def test_tcap_варианты(self):
        def через_udt(tcap, ssn=6):
            return разобрать_пакет(sccp_udt(адрес_pc(1, ssn), адрес_pc(2, ssn), tcap), "SCCP")

        продолжение = ber(0x65, ber(0x48, b"\x01") + ber(0x49, b"\x02\x03") + ber(0x6C, ber(
            0xA3, ber(0x02, b"\x05") + ber(0x02, b"\x1b"))))
        п = через_udt(продолжение)
        self.assertEqual((["01"], ["0203"], [27]), (поля(п)["tcap.otid"], поля(п)["tcap.dtid"], поля(п)["gsm_map.error_code"]))
        отказ = ber(0x67, ber(0x49, b"\x02\x03") + ber(0x4A, b"\x01"))
        self.assertEqual([1], поля(через_udt(отказ))["tcap.p_abortCause"])
        reject = ber(0x64, ber(0x49, b"\x09") + ber(0x6C, ber(0xA4, ber(0x05, b"") + ber(0x81, b"\x02"))))
        self.assertEqual([2], поля(через_udt(reject))["tcap.reject_problem"])
        # Длинная форма длины BER и linkedID.
        связанный = ber(0x62, ber(0x48, b"\x07") + ber(0x6C, ber(0xA1, ber(0x02, b"\x02") + ber(0x80, b"\x01")
                                                                 + ber(0x02, b"\x2e") + ber(0x04, b"\xaa" * 200))))
        п = через_udt(связанный)
        self.assertEqual(([1], [46]), (поля(п)["tcap.linkedID"], поля(п)["gsm_map.opcode"]))
        self.assertIn("mo-forwardSM", п.инфо)
        # Нарушения: Begin без OTID, DTID из 5 байт, лишний элемент, компонент без invokeID.
        for плохо in (ber(0x62, ber(0x6C, b"")), ber(0x64, ber(0x49, b"\1\2\3\4\5")),
                      ber(0x64, ber(0x49, b"\1") + ber(0x04, b"")),
                      ber(0x62, ber(0x48, b"\1") + ber(0x6C, ber(0xA1, ber(0x02, b"\x02")))),
                      tcap_begin_update_location()[:-1] + b"\0"[:0], ber(0x62, ber(0x48, b"\1"))[:-1] + b"\x01\x02"):
            with self.subTest(плохо=плохо.hex()):
                self.assertNotIn("TCAP", через_udt(плохо).стек)
        # SSN 146 и контекст CAP фазы 4 {0 4 0 0 1 22 3 4} — CAMEL, не MAP (контекст важнее SSN).
        self.assertNotIn("MAP", через_udt(tcap_end_result(), ssn=146).стек)
        cap4 = tcap_begin_update_location(контекст=bytes([0x04, 0x00, 0x00, 0x01, 0x16, 0x03, 0x04]))
        self.assertEqual(["SCCP", "TCAP", "CAMEL"], через_udt(cap4, ssn=146).стек)
        self.assertEqual(["SCCP", "TCAP", "CAMEL"], через_udt(cap4, ssn=6).стек)
        # Чужой контекст (не MAP и не CAP) — только TCAP.
        чужой = tcap_begin_update_location(контекст=bytes([0x04, 0x00, 0x00, 0x01, 0x30, 0x03, 0x04]))
        self.assertEqual(["SCCP", "TCAP"], через_udt(чужой, ssn=146).стек)
        self.assertEqual(["SCCP", "TCAP"], через_udt(чужой, ssn=6).стек)


class LAPDTests(unittest.TestCase):
    I_КАДР = bytes([0 << 2, (64 << 1) | 1, 5 << 1, 3 << 1]) + q931_setup()

    def test_i_кадр_q931(self):
        п = разобрать_пакет(self.I_КАДР, "LAPD")
        self.assertEqual(["LAPD", "Q.931"], п.стек)
        ф = поля(п)
        self.assertEqual(([0], [64], [5], [3], [0], [1]), (ф["lapd.sapi"], ф["lapd.tei"], ф["lapd.control.n_s"],
                                                         ф["lapd.control.n_r"], ф["lapd.ea1"], ф["lapd.ea2"]))
        self.assertEqual((1, 1), (поле(п, "lapd.tei").смещение, поле(п, "lapd.tei").длина))
        self.assertEqual(4, поле(п, "q931.disc").смещение)
        self.assertEqual([0], отобрать([ф], "lapd.sapi == 0 and q931.message_type == 5"))

    def test_s_u_и_linux(self):
        rr = разобрать_пакет(bytes([0x02, 0x81, 0x01, 0x07]), "LAPD")
        self.assertEqual(("RR", [3], [1]), (поле(rr, "lapd.control.s_ftype").текст, поля(rr)["lapd.control.n_r"],
                                            поля(rr)["lapd.control.pf"]))
        ua = разобрать_пакет(bytes([0x00, 0x81, 0x73]), "LAPD")
        self.assertIn("UA", ua.инфо)
        self.assertEqual([1], поля(ua)["lapd.control.pf"])
        ui = разобрать_пакет(bytes([0x00, 0xFF, 0x03]) + q931_release_complete(), "LAPD")
        self.assertEqual(["LAPD", "Q.931"], ui.стек)
        self.assertEqual([16], поля(ui)["q931.cause_value"])
        # TEI-управление (SAPI 63) — данными.
        self.assertEqual(["LAPD", "Данные"], разобрать_пакет(bytes([0xFC, 0xFF, 0x03, 0x0F, 1, 2, 1, 0xFF]), "LAPD").стек)
        # Биты расширения адреса не по Q.921 — ошибка, Q.931 не ищется.
        плохо = разобрать_пакет(bytes([0x01, 0x80]) + self.I_КАДР[2:], "LAPD")
        self.assertNotIn("Q.931", плохо.стек)
        self.assertTrue(any("EA" in о for о in плохо.ошибки))
        linux = разобрать_пакет(struct.pack(">HHH8sH", 4, 8445, 1, b"\x01" + b"\0" * 7, 0x0030) + self.I_КАДР,
                                "Linux LAPD")
        self.assertEqual(["SLL", "LAPD", "Q.931"], linux.стек)
        self.assertEqual(16, поле(linux, "lapd.sapi").смещение)

    def test_q931_нарушения(self):
        for плохо in (b"\x09" + q931_setup()[1:], b"\x08\x13\x00\x01\x05", b"\x08\x01\x01\x7f",
                      q931_setup() + b"\x70\x05ab", b"\x08\x03\x00\x00\x01\x05"):
            with self.subTest(плохо=плохо.hex()):
                п = разобрать_пакет(self.I_КАДР[:4] + плохо, "LAPD")
                self.assertEqual(["LAPD", "Данные"], п.стек)

    def test_обрыв(self):
        все_обрывы(self, self.I_КАДР, "LAPD")


class СтрогостьTests(unittest.TestCase):
    """Каждая проверка строения — отдельным нарушением; «не наше» — без ложной отметки обрыва."""

    def не_признано(self, кадр, канал, протокол):
        п = разобрать_пакет(кадр, канал)
        self.assertNotIn(протокол, п.стек)
        self.assertFalse(any("оборван" in о for о in п.ошибки), п.ошибки)
        return п

    def test_sigtran_tlv(self):
        # Параметр длины 0 (меньше заголовка TLV) — не M3UA, и разбор не зацикливается.
        нуль = сигтран(3, 1, struct.pack(">HH", 0x0004, 0))
        итог = []
        нить = threading.Thread(target=lambda: итог.append(разобрать_пакет(поверх_sctp(нуль, 3)).стек), daemon=True)
        нить.start()
        нить.join(10)
        self.assertEqual([["Ethernet", "IPv4", "SCTP"]], итог)
        # Параметр длиннее сообщения.
        длинный = сигтран(3, 1, struct.pack(">HH", 0x0011, 12) + struct.pack(">I", 42))
        self.assertNotIn("M3UA", разобрать_пакет(поверх_sctp(длинный, 3)).стек)
        # Protocol Data вне DATA (в ERR) — пользователь MTP3 не ищется.
        pd = struct.pack(">IIBBBB", 1, 2, 3, 2, 0, 0) + sccp_udt(адрес_pc(1, 6), адрес_pc(2, 6), b"\x01")
        п = разобрать_пакет(поверх_sctp(сигтран(0, 0, параметр(0x0210, pd)), 3))
        self.assertEqual(["M3UA", "Данные"], п.стек[-2:])

    def test_m2pa_и_mtp2(self):
        # Длина в заголовке (100) не равна длине куска DATA (16 + 7).
        неверная = struct.pack(">BBBBI", 1, 0, 11, 1, 100) + struct.pack(">II", 1, 2) + b"\x00\x83" \
            + метка(1, 2, 0) + b"\x01"
        self.assertNotIn("M2PA", разобрать_пакет(поверх_sctp(неверная, 5)).стек)
        # LSSU: биты 8–4 поля состояния — запасные.
        п = разобрать_пакет(bytes([1, 2, 1, 0x09]), "MTP2")
        self.assertTrue(any("LI" in о for о in п.ошибки))

    def test_sccp(self):
        udt = sccp_udt(адрес_gt(6, "79161234567"), адрес_pc(1, 7), b"\x01\x02")
        # Указатель за конец сообщения.
        self.не_признано(udt[:4] + b"\xf0" + udt[5:], "SCCP", "SCCP")
        # Схема кодирования GT 3 (не BCD).
        кривой = bytes([(4 << 2) | 2, 6, 0, (1 << 4) | 3, 4]) + bcd("1234")
        self.не_признано(sccp_udt(кривой, адрес_pc(1, 7), b"\x01"), "SCCP", "SCCP")
        # Промежуток между указателями и параметрами.
        с_дырой = udt[:2] + bytes([udt[2] + 1, udt[3] + 1, udt[4] + 1]) + b"\x00" + udt[5:]
        self.не_признано(с_дырой, "SCCP", "SCCP")
        # Необязательная часть без октета «конец необязательных параметров».
        вызываемый, вызывающий = адрес_pc(10, 254), адрес_pc(20, 254)
        cr = bytes([0x01, 1, 2, 3, 0x02, 2, 1 + 1 + len(вызываемый)]) + bytes([len(вызываемый)]) + вызываемый \
            + bytes([0x04, len(вызывающий)]) + вызывающий
        self.не_признано(cr, "SCCP", "SCCP")
        # DT1 несёт данные соединения — TCAP в нём не ищется.
        tcap = tcap_end_result()
        dt1 = bytes([0x06, 1, 2, 3, 0, 1, len(tcap)]) + tcap
        self.assertEqual(["SCCP", "Данные"], разобрать_пакет(dt1, "SCCP").стек)

    def test_tcap(self):
        invoke = ber(0x6C, ber(0xA1, ber(0x02, b"\x01") + ber(0x02, b"\x02")))
        for плохо in (ber(0x62, invoke),                                               # Begin без OTID
                      ber(0x62, ber(0x48, b"\1") + ber(0x6C, ber(0xA1, ber(0x02, b"\x00\x01") + ber(0x02, b"\x02")))),
                      ber(0x62, ber(0x48, b"\1") + ber(0x6C, ber(0xA5, ber(0x02, b"\x01") + ber(0x02, b"\x02")))),
                      ber(0x62, ber(0x48, b"\1") + ber(0x6B, ber(0x04, b"x")) + invoke)):
            with self.subTest(плохо=плохо.hex()):
                self.не_признано(sccp_udt(адрес_pc(1, 6), адрес_pc(2, 6), плохо), "SCCP", "TCAP")
        self.assertIn("TCAP", разобрать_пакет(sccp_udt(адрес_pc(1, 6), адрес_pc(2, 6),
                                                      ber(0x62, ber(0x48, b"\1") + invoke)), "SCCP").стек)

    def test_isup(self):
        def mtp3(тело):
            return bytes([0x85]) + метка(1, 2, 0) + тело
        for тело in (struct.pack("<H", 5) + bytes([0x0C, 9, 0, 2, 0x80, 0x90]),        # указатель за конец
                     struct.pack("<H", 5) + bytes([0x0C, 2, 0, 1, 0x90]),              # причина короче 2
                     struct.pack("<H", 5) + bytes([0x10, 1, 0x0A, 1, 5])):             # нет конца необяз.
            with self.subTest(тело=тело.hex()):
                self.не_признано(mtp3(тело), "MTP3", "ISUP")
        iam = isup_iam()
        с_дырой = iam[:8] + bytes([iam[8] + 1, iam[9] + 1]) + b"\x00" + iam[10:]
        self.не_признано(mtp3(с_дырой), "MTP3", "ISUP")
        # CIC — 12 бит (Q.763 1.2): запасные биты второго октета в номер не входят.
        п = разобрать_пакет(mtp3(bytes([0x2C, 0xF1, 0x13])), "MTP3")
        self.assertEqual(([300], [15]), (поля(п)["isup.cic"], поля(п)["isup.cic_spare"]))

    def test_lapd_sapi(self):
        # Q.931 — только на SAPI 0 (Q.921 3.3.3); SAPI 16 (X.25) — данными.
        кадр = bytes([16 << 2, (1 << 1) | 1, 0x0A, 0x06]) + q931_setup()
        self.assertEqual(["LAPD", "Данные"], разобрать_пакет(кадр, "LAPD").стек)


class УровниTests(unittest.TestCase):
    def test_дерево_протоколов(self):
        for имя, уровень in (("M3UA", "транспортный"), ("M2PA", "транспортный"), ("SUA", "транспортный"),
                             ("IUA", "транспортный"), ("M2UA", "транспортный"), ("MTP2", "канальный"),
                             ("LAPD", "канальный"), ("MTP3", "сетевой"), ("SCCP", "сетевой"),
                             ("TCAP", "прикладной"), ("ISUP", "прикладной"), ("MAP", "прикладной"),
                             ("Q.931", "прикладной")):
            self.assertEqual(уровень, уровень_протокола(имя), имя)


# -- отрицательные: случайные данные не признаются своими ------------------------------------------------


class СлучайныеTests(unittest.TestCase):
    ПАКЕТОВ = 2000

    def случайные(self, зерно, от_=0, до=200):
        г = random.Random(зерно)
        return [bytes(г.getrandbits(8) for _ in range(г.randint(от_, до))) for _ in range(self.ПАКЕТОВ)]

    def test_sigtran_по_ppid_и_порту(self):
        for ppid, порт, имя in ((3, 2905, "M3UA"), (2, 2904, "M2UA"), (5, 3565, "M2PA"), (4, 14001, "SUA"),
                                (1, 9900, "IUA")):
            with self.subTest(имя=имя):
                признано = sum(имя in разобрать_пакет(поверх_sctp(н, ppid, порт, порт)).стек
                               for н in self.случайные(ppid))
                self.assertEqual(0, признано)

    def test_sigtran_со_своей_версией(self):
        # Версия 1, резерв 0 и верный класс — остальное случайно: длина и TLV не сходятся.
        г = random.Random(7)
        признано = 0
        for н in self.случайные(8, 8):
            н = bytes([1, 0, г.choice([0, 1, 2, 3, 4, 9]), г.randint(0, 6)]) + н[4:]
            признано += "M3UA" in разобрать_пакет(поверх_sctp(н, 3)).стек
        self.assertEqual(0, признано)

    def test_sccp_isup_tcap_q931_внутри(self):
        # У SCCP RLC (тип + две местные ссылки) и ISUP CCR/RSC/BLO/UBL/BLA/UBA/UCIC (CIC + тип)
        # проверить нечего, кроме типа и точной длины: случайно совпадает ~1/256 от доли
        # пакетов нужной длины (на 20 000 пакетах — 0,02 %). Допуск — 0,5 %.
        for имя, зерно, допуск, как in (
                ("SCCP", 31, 0.005, lambda н: поверх_sctp(m3ua_data(1, 2, 3, н), 3)),
                ("ISUP", 32, 0.005, lambda н: поверх_sctp(m3ua_data(1, 2, 5, н), 3)),
                ("TCAP", 33, 0, lambda н: bytes([0x83]) + метка(1, 2, 0) + sccp_udt(адрес_pc(1, 6), адрес_pc(2, 6), н)),
                ("Q.931", 34, 0, lambda н: поверх_sctp(сигтран(5, 1, параметр(0x000E, н)), 1))):
            with self.subTest(имя=имя):
                канал = "MTP3" if имя == "TCAP" else "Ethernet"
                признано = sum(имя in разобрать_пакет(как(н), канал).стек for н in self.случайные(зерно, 1))
                self.assertLessEqual(признано, self.ПАКЕТОВ * допуск)

    def test_каналы(self):
        признано = sum("SCCP" in разобрать_пакет(н, "SCCP").стек for н in self.случайные(11, 1))
        self.assertLessEqual(признано, self.ПАКЕТОВ * 0.005)           # см. SCCP RLC выше
        # LAPD объявлен типом канала; Q.931 внутри — только при строгом совпадении.
        признано = sum("Q.931" in разобрать_пакет(н, "LAPD").стек for н in self.случайные(12, 3))
        self.assertEqual(0, признано)
        # MTP2 объявлен типом канала, а своих проверок у него две: LI сходится с
        # длиной (1/64) и запасные биты октета LI — нули (1/4). Ожидаемая доля
        # случайно сошедшихся — 1/256 ≈ 0,39 % (FCS ещё реже: CRC-16 даёт 2⁻¹⁶).
        сошлось = sum(not any("LI" in о for о in разобрать_пакет(н, "MTP2").ошибки)
                      for н in self.случайные(13, 3))
        self.assertLessEqual(сошлось, self.ПАКЕТОВ * 0.005)
        # MTP3 признаётся по типу канала; пользователи внутри — строго.
        признано = sum(bool({"SCCP", "ISUP"} & set(разобрать_пакет(н, "MTP3").стек)) for н in self.случайные(14, 5))
        self.assertEqual(0, признано)

    def test_isup_со_своим_типом(self):
        # Известный тип сообщения, остальное случайно: строение (указатели, длины, конец) не сходится.
        г = random.Random(21)
        типы = [0x01, 0x02, 0x06, 0x09, 0x0C, 0x10, 0x2C, 0x17, 0x18]
        признано = 0
        for н in self.случайные(22, 1, 60):
            тело = struct.pack("<H", 1) + bytes([г.choice(типы)]) + н
            признано += "ISUP" in разобрать_пакет(bytes([0x85]) + метка(1, 2, 0) + тело, "MTP3").стек
        self.assertLessEqual(признано, self.ПАКЕТОВ * 0.005)


if __name__ == "__main__":
    unittest.main()


class ОборванныйКусокSctp(unittest.TestCase):
    """Кусок DATA, оборванный внутри своего 16-байтного заголовка, — не «разбор прерван»."""

    def test_обрыв_в_заголовке_data(self):
        import struct as _struct

        import potok_sintez as _с
        from reportgen.setevoy import разобрать_пакет as _разобрать
        sctp = _struct.pack("!HHII", 2905, 2905, 1, 0) + _struct.pack("!BBH", 0, 3, 40) + bytes(4)
        пакет = _разобрать(_с.ethernet(_с.ipv4("10.0.0.1", "10.0.0.2", 132, sctp)))
        self.assertFalse(any("разбор прерван" in о for о in пакет.ошибки), пакет.ошибки)
        self.assertIn("SCTP", [у.протокол for у in пакет.уровни])


class ТестISUPбезCIC(unittest.TestCase):
    """Тело SIP-I (Q.1912.5): ISUP с октета типа, без CIC."""

    def test_раскладка(self):
        iam = isup_iam()[2:]
        р = Разбор(Пакет(1, 0.0, b"\xee" + iam, 1 + len(iam), "RAW"))
        self.assertTrue(oks7.isup(р, 1, 1 + len(iam), cic=False))
        у = р.п.уровни[0]
        ключи = [(x.ключ, x.смещение, x.длина) for x in у.поля[:2]]
        self.assertEqual(ключи[0], ("isup.message_type", 1, 1))
        self.assertNotIn("isup.cic", [x.ключ for x in у.поля])
        self.assertEqual(у.итог, "IAM, → 4951234567, от 74951112233")
        # С CIC те же байты — не ISUP (тип читается не там); без CIC пусто — тоже нет.
        self.assertFalse(oks7.isup(Разбор(Пакет(1, 0.0, iam, len(iam), "RAW")), 0, len(iam)))
        self.assertIsNone(oks7._проверка_isup(b"", 0, 0, cic=False))
        self.assertIsNotNone(oks7._проверка_isup(isup_iam(), 0, len(isup_iam())))
