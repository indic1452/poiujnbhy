"""GSM Abis поверх LAPD: RSL (3GPP TS 48.058) на SAPI 0 и OML (TS 12.21) на SAPI 62.

RSL: дискриминатор (группа и бит T прозрачности), тип сообщения, элементы по таблице длин
rsl_att_tlvdef (libosmocore src/gsm/rsl.c): TV — тег и октет, FIXED n — тег и n октетов,
TLV — тег, длина, значение, TL16V — длина двумя октетами (L3 Information). Сообщение уровня 3
(L3 Information, Full BCCH / Imm Assign Info) передаётся разбору 04.08 (gsm.py) — так через
Abis видны вызовы, SMS, пейджинг и назначения каналов. Номера сообщений и элементов — как в
Wireshark packet-rsl.c и libosmocore gsm_08_58.h.

OML: дискриминатор (0x80 Formatted O&M, 0x40 MMI, 0x20 TRAU O&M, 0x10 изготовителя), размещение,
номер, длина, затем у FOM — тип сообщения, класс объекта и экземпляр (BTS, TRX, TS) — gsm_12_21.h.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..pole import u16
from ..razbor import Разбор, данные

#: Группы сообщений RSL (дискриминатор без бита T): gsm_08_58.h ABIS_RSL_MDISC_*.
RSL_ГРУППЫ = {0x02: "управление радиоканалом (RLL)", 0x08: "выделенный канал", 0x0C: "общий канал",
              0x10: "управление TRX", 0x20: "местоопределение"}
#: Какие типы бывают в группе (48.058 9.2): RLL 01–0B, общий канал 11–1F, TRX 19–1C, выделенный 21–3F.
RSL_ТИПЫ_ГРУППЫ = {0x02: range(0x01, 0x0C), 0x0C: range(0x11, 0x20), 0x10: range(0x19, 0x1D),
                   0x08: range(0x21, 0x40), 0x20: range(0x41, 0x42)}

#: Типы сообщений RSL (48.058 9.2; Wireshark rsl_msg_type_vals).
RSL_ТИПЫ = {
    0x01: "DATA REQuest", 0x02: "DATA INDication", 0x03: "ERROR INDication", 0x04: "ESTablish REQuest",
    0x05: "ESTablish CONFirm", 0x06: "ESTablish INDication", 0x07: "RELease REQuest", 0x08: "RELease CONFirm",
    0x09: "RELease INDication", 0x0A: "UNIT DATA REQuest", 0x0B: "UNIT DATA INDication",
    0x11: "BCCH INFOrmation", 0x12: "CCCH LOAD INDication", 0x13: "CHANnel ReQuireD", 0x14: "DELETE INDication",
    0x15: "PAGING CoMmanD", 0x16: "IMMEDIATE ASSIGN COMMAND", 0x17: "SMS BroadCast REQuest",
    0x19: "RF RESource INDication", 0x1A: "SACCH FILLing", 0x1B: "OVERLOAD", 0x1C: "ERROR REPORT",
    0x1D: "SMS BroadCast CoMmanD", 0x1E: "CBCH LOAD INDication", 0x1F: "NOTification CoMmanD",
    0x21: "CHANnel ACTIVation", 0x22: "CHANnel ACTIVation ACKnowledge", 0x23: "CHANnel ACTIVation Negative ACK",
    0x24: "CONNection FAILure", 0x25: "DEACTIVATE SACCH", 0x26: "ENCRyption CoMmanD", 0x27: "HANDOver DETection",
    0x28: "MEASurement RESult", 0x29: "MODE MODIFY REQuest", 0x2A: "MODE MODIFY ACKnowledge",
    0x2B: "MODE MODIFY Negative ACKnowledge", 0x2C: "PHYsical CONTEXT REQuest", 0x2D: "PHYsical CONTEXT CONFirm",
    0x2E: "RF CHANnel RELease", 0x2F: "MS POWER CONTROL", 0x30: "BS POWER CONTROL", 0x31: "PREPROCess CONFIGure",
    0x32: "PREPROCessed MEASurement RESult", 0x33: "RF CHANnel RELease ACKnowledge", 0x34: "SACCH INFO MODIFY",
    0x35: "TALKER DETection", 0x36: "LISTENER DETection", 0x37: "REMOTE CODEC CONFiguration REPort",
    0x38: "Round Trip Delay REPort", 0x39: "PRE-HANDOver NOTIFication", 0x3A: "MultiRate CODEC MODification REQuest",
    0x3B: "MultiRate CODEC MOD ACKnowledge", 0x3C: "MultiRate CODEC MOD Negative ACKnowledge",
    0x3D: "MultiRate CODEC MOD PERformed", 0x3E: "TFO REPort", 0x3F: "TFO MODification REQuest",
    0x41: "Location Information",
}

#: Элементы RSL: тег → (имя, вид, длина) — вид «TV», «F» (FIXED), «TLV», «TL16V» (rsl_att_tlvdef).
RSL_IE: Dict[int, Tuple[str, str, int]] = {
    0x01: ("Channel Number", "TV", 1), 0x02: ("Link Identifier", "TV", 1), 0x03: ("Activation Type", "TV", 1),
    0x04: ("BS Power", "TV", 1), 0x05: ("Channel Identification", "TLV", 0), 0x06: ("Channel Mode", "TLV", 0),
    0x07: ("Encryption Information", "TLV", 0), 0x08: ("Frame Number", "F", 2), 0x09: ("Handover Reference", "TV", 1),
    0x0A: ("L1 Information", "F", 2), 0x0B: ("L3 Information", "TL16V", 0), 0x0C: ("MS Identity", "TLV", 0),
    0x0D: ("MS Power", "TV", 1), 0x0E: ("Paging Group", "TV", 1), 0x0F: ("Paging Load", "F", 2),
    0x10: ("Physical Context", "TLV", 0), 0x11: ("Access Delay", "TV", 1), 0x12: ("RACH Load", "TLV", 0),
    0x13: ("Request Reference", "F", 3), 0x14: ("Release Mode", "TV", 1), 0x15: ("Resource Information", "TLV", 0),
    0x16: ("RLM Cause", "TLV", 0), 0x17: ("Starting Time", "F", 2), 0x18: ("Timing Advance", "TV", 1),
    0x19: ("Uplink Measurements", "TLV", 0), 0x1A: ("Cause", "TLV", 0), 0x1B: ("Measurement Result Number", "TV", 1),
    0x1C: ("Message Identifier", "TV", 1), 0x1E: ("System Info Type", "TV", 1), 0x1F: ("MS Power Parameters", "TLV", 0),
    0x20: ("BS Power Parameters", "TLV", 0), 0x21: ("Pre-processing Parameters", "TLV", 0),
    0x22: ("Pre-processed Measurements", "TLV", 0), 0x23: ("Immediate Assign Info", "TLV", 0),
    0x24: ("SMSCB Information", "F", 23), 0x25: ("MS Timing Offset", "TV", 1), 0x26: ("Erroneous Message", "TLV", 0),
    0x27: ("Full BCCH Information", "TLV", 0), 0x28: ("Channel Needed", "TV", 1), 0x29: ("CB Command type", "TV", 1),
    0x2A: ("SMSCB Message", "TLV", 0), 0x2B: ("Full Immediate Assign Info", "TLV", 0),
    0x2C: ("SACCH Information", "TLV", 0), 0x2D: ("CBCH Load Information", "TV", 1),
    0x2E: ("SMSCB Channel Indicator", "TV", 1), 0x2F: ("Group Call Reference", "TLV", 0),
    0x30: ("Channel Description", "TLV", 0), 0x31: ("NCH DRX Information", "TLV", 0),
    0x32: ("Command Indicator", "TLV", 0), 0x33: ("eMLPP Priority", "TV", 1), 0x34: ("UIC", "TLV", 0),
    0x35: ("Main Channel Reference", "TV", 1), 0x36: ("MultiRate Configuration", "TLV", 0),
    0x37: ("MultiRate Control", "TV", 1), 0x38: ("Supported Codec Types", "TLV", 0),
    0x39: ("Codec Configuration", "TLV", 0), 0x3A: ("Round Trip Delay", "TV", 1), 0x3B: ("TFO Status", "TV", 1),
    0x3C: ("LLP APDU", "TLV", 0), 0x3D: ("TFO Transparent Container", "TLV", 0),
}

#: Биты C номера канала (48.058 9.3.1; gsm_08_58.h ABIS_RSL_CHAN_NR_CBITS_*).
def канал(октет: int) -> str:
    c, tn = октет >> 3, октет & 7
    if c == 0x01:
        вид = "TCH/F (Bm + ACCH)"
    elif c >> 1 == 0x01:
        вид = f"TCH/H (Lm + ACCH), подканал {c & 1}"
    elif c >> 2 == 0x01:
        вид = f"SDCCH/4, подканал {c & 3}"
    elif c >> 3 == 0x01:
        вид = f"SDCCH/8, подканал {c & 7}"
    else:
        вид = {0x10: "BCCH", 0x11: "RACH", 0x12: "PCH/AGCH"}.get(c, f"C-биты {c:05b}")
    return f"{вид}, TS {tn}"


def _ie(д: bytes, м: int, конец: int) -> Optional[Tuple[int, int, int]]:
    """(тег, начало значения, конец значения) одного элемента или None — не по таблице длин."""
    тег = д[м]
    описание = RSL_IE.get(тег)
    if описание is None:
        return None
    _, вид, длина = описание
    if вид in ("TV", "F"):
        начало, конец_ = м + 1, м + 1 + длина
    elif вид == "TLV":
        if м + 2 > конец:
            return None
        начало, конец_ = м + 2, м + 2 + д[м + 1]
    else:
        if м + 3 > конец:
            return None
        начало, конец_ = м + 3, м + 3 + u16(д, м + 1)
    return (тег, начало, конец_) if конец_ <= конец else None


def _проверка_rsl(д: bytes, м: int, конец: int) -> bool:
    """RSL: известная группа и тип, элементы укладываются в сообщение ровно."""
    if конец - м < 2 or (д[м] & 0xFE) not in RSL_ГРУППЫ or (д[м + 1] & 0x7F) not in RSL_ТИПЫ:
        return False
    if (д[м + 1] & 0x7F) not in RSL_ТИПЫ_ГРУППЫ[д[м] & 0xFE]:
        return False
    место = м + 2
    while место < конец:
        элемент = _ie(д, место, конец)
        if элемент is None:
            return False
        место = элемент[2]
    return место == конец


def rsl(р: Разбор, м: int, конец: int) -> bool:
    """Сообщение RSL (48.058): группа, тип, элементы; L3 Information — разбору 04.08."""
    from . import gsm  # noqa: PLC0415
    д = р.д
    конец = min(конец, len(д))              # обрезанный захват — не RSL, а обрыв
    if not _проверка_rsl(д, м, конец):
        return False
    группа, прозрачное, тип = д[м] & 0xFE, д[м] & 1, д[м + 1] & 0x7F
    у = р.уровень("RSL", "GSM Abis RSL (TS 48.058)", м)
    у.поле("Дискриминатор", "gsm_abis_rsl.msg_dsc", RSL_ГРУППЫ[группа], м, 1, группа)
    у.поле("T (прозрачное для BTS)", "gsm_abis_rsl.T_bit", прозрачное, м, 1)
    у.поле("Тип сообщения", "gsm_abis_rsl.msg_type", f"0x{тип:02x} ({RSL_ТИПЫ[тип]})", м + 1, 1, тип)
    сведения = []
    l3 = []
    место = м + 2
    while место < конец:
        тег, начало, конец_ = _ie(д, место, конец)
        имя = RSL_IE[тег][0]
        значение = д[начало:конец_]
        if тег == 0x01:
            текст = канал(значение[0])
            сведения.append(текст)
        elif тег == 0x02:
            текст = f"{'SACCH' if значение[0] >> 6 == 1 else 'FACCH/SDCCH'}, SAPI {значение[0] & 7}"
        elif тег in (0x11, 0x18):
            текст = str(значение[0])
        elif тег == 0x13:
            текст = f"RA 0x{значение[0]:02x}"
        else:
            текст = значение.hex() if len(значение) <= 16 else f"{len(значение)} байт"
        у.поле(имя, "gsm_abis_rsl.ie_id", текст, место, конец_ - место, тег)
        if тег in (0x0B, 0x27, 0x2B):
            l3.append((тег, начало, конец_))
        место = конец_
    у.длина = конец - м
    у.итог = RSL_ТИПЫ[тип] + (f", {', '.join(сведения)}" if сведения else "")
    р.п.инфо = "RSL " + у.итог
    for тег, начало, конец_ in l3[:1]:
        # Full BCCH / Immediate Assign Info — сообщение CCCH с октетом псевдодлины; L3 Information —
        # сообщение уровня 3 как есть (на SACCH в UNIT DATA — тоже с псевдодлиной).
        if тег in (0x27, 0x2B) or тип in (0x0A, 0x0B):
            if gsm.ccch(р, начало, конец_) or gsm.dtap(р, начало, конец_):
                continue
        elif gsm.dtap(р, начало, конец_):
            continue
        данные(р, начало, "сообщение уровня 3", конец_)
    return True


#: OML: дискриминаторы и размещение (12.21 8; gsm_12_21.h ABIS_OM_*).
OML_ДИСКР = {0x80: "Formatted O&M", 0x40: "MMI", 0x20: "TRAU O&M", 0x10: "изготовителя"}
OML_РАЗМЕЩЕНИЕ = {0x80: "единственное", 0x40: "первое", 0x20: "среднее", 0x10: "последнее"}
#: Типы сообщений FOM (12.21 9.1; gsm_12_21.h abis_nm_msgtype).
OML_ТИПЫ = {
    0x01: "Load Data Initiate", 0x02: "Load Data Initiate ACK", 0x03: "Load Data Initiate NACK",
    0x04: "Load Data Segment", 0x05: "Load Data Segment ACK", 0x06: "Load Data Abort", 0x07: "Load Data End",
    0x08: "Load Data End ACK", 0x09: "Load Data End NACK", 0x0A: "SW Activate Request", 0x0B: "SW Activate Request ACK",
    0x0C: "SW Activate Request NACK", 0x0D: "Activate SW", 0x0E: "Activate SW ACK", 0x0F: "Activate SW NACK",
    0x10: "SW Activated Report", 0x21: "Establish TEI", 0x22: "Establish TEI ACK", 0x23: "Establish TEI NACK",
    0x24: "Connect Terrestrial Signalling", 0x25: "Connect Terrestrial Signalling ACK",
    0x26: "Connect Terrestrial Signalling NACK", 0x27: "Disconnect Terrestrial Signalling",
    0x28: "Disconnect Terrestrial Signalling ACK", 0x29: "Disconnect Terrestrial Signalling NACK",
    0x2A: "Connect Terrestrial Traffic", 0x2B: "Connect Terrestrial Traffic ACK", 0x2C: "Connect Terrestrial Traffic NACK",
    0x2D: "Disconnect Terrestrial Traffic", 0x2E: "Disconnect Terrestrial Traffic ACK",
    0x2F: "Disconnect Terrestrial Traffic NACK", 0x31: "Connect Multi-Drop Link", 0x32: "Connect Multi-Drop Link ACK",
    0x33: "Connect Multi-Drop Link NACK", 0x34: "Disconnect Multi-Drop Link", 0x35: "Disconnect Multi-Drop Link ACK",
    0x36: "Disconnect Multi-Drop Link NACK", 0x41: "Set BTS Attributes", 0x42: "Set BTS Attributes ACK",
    0x43: "Set BTS Attributes NACK", 0x44: "Set Radio Carrier Attributes", 0x45: "Set Radio Carrier Attributes ACK",
    0x46: "Set Radio Carrier Attributes NACK", 0x47: "Set Channel Attributes", 0x48: "Set Channel Attributes ACK",
    0x49: "Set Channel Attributes NACK", 0x51: "Perform Test", 0x52: "Perform Test ACK", 0x53: "Perform Test NACK",
    0x54: "Test Report", 0x55: "Send Test Report", 0x56: "Send Test Report ACK", 0x57: "Send Test Report NACK",
    0x58: "Stop Test", 0x59: "Stop Test ACK", 0x5A: "Stop Test NACK", 0x61: "State Changed Event Report",
    0x62: "Failure Event Report", 0x63: "Stop Sending Event Reports", 0x64: "Stop Sending Event Reports ACK",
    0x65: "Stop Sending Event Reports NACK", 0x66: "Restart Sending Event Reports",
    0x67: "Restart Sending Event Reports ACK", 0x68: "Restart Sending Event Reports NACK",
    0x69: "Change Administrative State", 0x6A: "Change Administrative State ACK",
    0x6B: "Change Administrative State NACK", 0x6C: "Change Administrative State Request",
    0x6D: "Change Administrative State Request ACK", 0x6E: "Change Administrative State Request NACK",
    0x71: "Changeover", 0x72: "Changeover ACK", 0x73: "Changeover NACK", 0x74: "Opstart", 0x75: "Opstart ACK",
    0x76: "Opstart NACK", 0x77: "Reinitialize", 0x78: "Reinitialize ACK", 0x79: "Reinitialize NACK",
    0x7A: "Set Site Outputs", 0x7B: "Set Site Outputs ACK", 0x7C: "Set Site Outputs NACK",
    0x81: "Get Attributes", 0x82: "Get Attribute Response", 0x83: "Get Attributes NACK",
    0x84: "Set Alarm Threshold", 0x85: "Set Alarm Threshold ACK", 0x86: "Set Alarm Threshold NACK",
    0x8A: "Measurement Result Request", 0x8B: "Measurement Result Response", 0x8C: "Stop Measurement",
    0x8D: "Start Measurement", 0x90: "Change HW Configuration", 0x91: "Change HW Configuration ACK",
    0x92: "Change HW Configuration NACK", 0x93: "Report Outstanding Alarms", 0x94: "Report Outstanding Alarms ACK",
    0x95: "Report Outstanding Alarms NACK",
}
#: Классы объектов (12.21 9.2; gsm_12_21.h abis_nm_obj_class, стандартные).
OML_КЛАССЫ = {0x00: "Site Manager", 0x01: "BTS", 0x02: "Radio Carrier", 0x03: "Channel", 0x04: "Baseband Transceiver",
              0xFF: "NULL"}


def oml(р: Разбор, м: int, конец: int) -> bool:
    """Сообщение OML (12.21): заголовок и у Formatted O&M — тип, класс и экземпляр объекта."""
    д = р.д
    конец = min(конец, len(д))
    if конец - м < 4 or д[м] not in OML_ДИСКР or д[м + 1] not in OML_РАЗМЕЩЕНИЕ:
        return False
    длина = д[м + 3]
    if м + 4 + длина != конец:
        return False
    дискр = д[м]
    if дискр == 0x80 and (длина < 5 or д[м + 4] not in OML_ТИПЫ):
        return False
    у = р.уровень("OML", "GSM Abis OML (TS 12.21)", м)
    у.поле("Дискриминатор", "gsm_abis_oml.mdisc", OML_ДИСКР[дискр], м, 1, дискр)
    у.поле("Размещение", "gsm_abis_oml.placement", OML_РАЗМЕЩЕНИЕ[д[м + 1]], м + 1, 1, д[м + 1])
    у.поле("Номер", "gsm_abis_oml.sequence", д[м + 2], м + 2, 1)
    у.поле("Длина", "gsm_abis_oml.length", длина, м + 3, 1)
    у.длина = конец - м
    if дискр == 0x80:
        тип, класс = д[м + 4], д[м + 5]
        bts, trx, ts = д[м + 6], д[м + 7], д[м + 8]
        у.поле("Тип сообщения", "gsm_abis_oml.fom.msg_type", f"0x{тип:02x} ({OML_ТИПЫ[тип]})", м + 4, 1, тип)
        у.поле("Класс объекта", "gsm_abis_oml.fom.objclass", OML_КЛАССЫ.get(класс, f"0x{класс:02x}"), м + 5, 1, класс)
        у.поле("Экземпляр", "gsm_abis_oml.fom.objinst", f"BTS {bts}, TRX {trx}, TS {ts}", м + 6, 3)
        if м + 9 < конец:
            у.поле("Атрибуты", "gsm_abis_oml.fom.attr", f"{конец - м - 9} байт", м + 9, конец - м - 9)
        у.итог = f"{OML_ТИПЫ[тип]}: {OML_КЛАССЫ.get(класс, f'класс 0x{класс:02x}')} ({bts}, {trx}, {ts})"
    else:
        у.итог = f"{OML_ДИСКР[дискр]}, {длина} байт"
    р.п.инфо = "OML " + у.итог
    return True
