"""BSSAP — интерфейс A GSM (BSC ↔ MSC) поверх SCCP: BSSMAP и DTAP (3GPP TS 48.008).

Документы (структуры — строго по ним):

* TS 48.008, 3.1 и 3.2: дискриминатор (0x00 — BSSMAP, 0x01 — DTAP), у BSSMAP — длина и тип
  сообщения (3.2.2.1), затем элементы: TV постоянной длины или TLV (3.2.2: формат каждого элемента);
  у DTAP — DLCI (3.1.2: C2, C1, SAPI) и длина, затем сообщение уровня 3 (TS 24.008, разбор GSM).
* Элементы: Circuit Identity Code (3.2.2.2: мультиплексор PCM — 11 бит, канальный интервал — 5),
  Cause (3.2.2.5), IMSI (3.2.2.6: Mobile Identity 24.008 10.5.1.4), TMSI (3.2.2.7), Encryption
  Information (3.2.2.10: допустимые A5/0…A5/7 битами и ключ Kc), Channel Type (3.2.2.11),
  Cell Identifier (3.2.2.17: CGI, LAC+CI, CI, LAI, LAC), Layer 3 Information (3.2.2.24) и Layer 3
  Message Contents (3.2.2.35) — сообщение 24.008, Chosen Encryption Algorithm (3.2.2.44).
* Таблицы типов сообщений, имён элементов (номер элемента — его место в перечислении) и причин —
  из Wireshark (packet-gsm_a_bssmap.c) программно; какие элементы — TV, видно там же
  (ELEM_MAND_TV/ELEM_OPT_TV), длины — по 3.2.2.
* На SCCP: по SSN 254 (BSSAP, TS 23.003) в UDT/XUDT и по признакам — в данных соединений (CR, CC,
  DT1): длина обязана совпасть с данными, элементы — уложиться в точности.
"""

from __future__ import annotations

from ..pole import u16
from ..razbor import ДОП_УРОВНИ, Разбор
from . import gsm, oks7
from .nas import _plmn

BSSMAP = {0x01: "Assignment Request", 0x02: "Assignment Complete", 0x03: "Assignment Failure",
          0x04: "VGCS/VBS Setup", 0x05: "VGCS/VBS Setup Ack", 0x06: "VGCS/VBS Setup Refuse",
          0x07: "VGCS/VBS Assignment Request", 0x08: "Channel Modify request", 0x10: "Handover Request",
          0x11: "Handover Required", 0x12: "Handover Request Acknowledge", 0x13: "Handover Command",
          0x14: "Handover Complete", 0x15: "Handover Succeeded", 0x16: "Handover Failure",
          0x17: "Handover Performed", 0x18: "Handover Candidate Enquire", 0x19: "Handover Candidate Response",
          0x1A: "Handover Required Reject", 0x1B: "Handover Detect", 0x1C: "VGCS/VBS Assignment Result",
          0x1D: "VGCS/VBS Assignment Failure", 0x1E: "VGCS/VBS Queuing Indication", 0x1F: "Uplink Request",
          0x20: "Clear Command", 0x21: "Clear Complete", 0x22: "Clear Request", 0x25: "SAPI 'n' Reject",
          0x26: "Confusion", 0x27: "Uplink Request Acknowledge", 0x28: "Suspend", 0x29: "Resume",
          0x2A: "Connection Oriented Information(Obsolete)", 0x2B: "Perform Location Request",
          0x2C: "LSA Information", 0x2D: "Perform Location Response", 0x2E: "Perform Location Abort",
          0x2F: "Common Id", 0x30: "Reset", 0x31: "Reset Acknowledge", 0x32: "Overload", 0x34: "Reset Circuit",
          0x35: "Reset Circuit Acknowledge", 0x36: "MSC Invoke Trace", 0x37: "BSS Invoke Trace",
          0x3A: "Connectionless Information", 0x3B: "VGCS/VBS Assignment Status",
          0x3C: "VGCS/VBS Area Cell Info", 0x3D: "Reset IP Resource", 0x3E: "Reset IP Resource Acknowledge",
          0x40: "Block", 0x41: "Blocking Acknowledge", 0x42: "Unblock", 0x43: "Unblocking Acknowledge",
          0x44: "Circuit Group Block", 0x45: "Circuit Group Blocking Acknowledge",
          0x46: "Circuit Group Unblock", 0x47: "Circuit Group Unblocking Acknowledge",
          0x48: "Unequipped Circuit", 0x49: "Uplink Request Confirmation", 0x4A: "Uplink Release Indication",
          0x4B: "Uplink Reject Command", 0x4C: "Uplink Release Command", 0x4D: "Uplink Seized Command",
          0x4E: "Change Circuit", 0x4F: "Change Circuit Acknowledge", 0x50: "Resource Request",
          0x51: "Resource Indication", 0x52: "Paging", 0x53: "Cipher Mode Command", 0x54: "Classmark Update",
          0x55: "Cipher Mode Complete", 0x56: "Queuing Indication", 0x57: "Complete Layer 3 Information",
          0x58: "Classmark Request", 0x59: "Cipher Mode Reject", 0x5A: "Load Indication",
          0x60: "VGCS Additional Information", 0x61: "VGCS SMS", 0x62: "Notification Data",
          0x63: "Uplink Application Data", 0x70: "Internal Handover Required",
          0x71: "Internal Handover Required Reject", 0x72: "Internal Handover Command",
          0x73: "Internal Handover Enquiry", 0x74: "LCLS-Connect-Control", 0x75: "LCLS-Connect-Control-Ack",
          0x76: "LCLS-Notification", 0x78: "Reroute Command", 0x79: "Reroute Complete"}
IE_BSSMAP = {0x01: "Circuit Identity Code", 0x03: "Resource Available", 0x04: "Cause", 0x05: "Cell Identifier",
             0x06: "Priority", 0x07: "Layer 3 Header Information", 0x08: "IMSI", 0x09: "TMSI",
             0x0A: "Encryption Information", 0x0B: "Channel Type", 0x0C: "Periodicity",
             0x0D: "Extended Resource Indicator", 0x0E: "Number Of MSs", 0x12: "Classmark Information Type 2",
             0x13: "Classmark Information Type 3", 0x14: "Interference Band To Be Used", 0x15: "RR Cause",
             0x17: "Layer 3 Information", 0x18: "DLCI", 0x19: "Downlink DTX Flag",
             0x1A: "Cell Identifier List", 0x1B: "Response Request", 0x1C: "Resource Indication Method",
             0x1D: "Classmark Information Type 1", 0x1E: "Circuit Identity Code List", 0x1F: "Diagnostic",
             0x20: "Layer 3 Message Contents", 0x21: "Chosen Channel", 0x22: "Total Resource Accessible",
             0x23: "Cipher Response Mode", 0x24: "Channel Needed", 0x25: "Trace Type", 0x26: "TriggerID",
             0x27: "Trace Reference", 0x28: "TransactionID", 0x29: "Mobile Identity", 0x2A: "OMCID",
             0x2B: "Forward Indicator", 0x2C: "Chosen Encryption Algorithm", 0x2D: "Circuit Pool",
             0x2E: "Circuit Pool List", 0x2F: "Time Indication", 0x30: "Resource Situation",
             0x31: "Current Channel Type 1", 0x32: "Queuing Indicator", 0x33: "Assignment Requirement",
             0x35: "Talker Flag", 0x36: "Connection Release Requested", 0x37: "Group Call Reference",
             0x38: "eMLPP Priority", 0x39: "Configuration Evolution Indication",
             0x3A: "Old BSS to New BSS Information", 0x3B: "LSA Identifier", 0x3C: "LSA Identifier List",
             0x3D: "LSA Information", 0x3E: "LCS QoS", 0x3F: "LSA access control suppression",
             0x40: "Speech Version", 0x43: "LCS Priority", 0x44: "Location Type", 0x45: "Location Estimate",
             0x46: "Positioning Data", 0x47: "LCS Cause", 0x48: "LCS Client Type", 0x49: "APDU",
             0x4A: "Network Element Identity", 0x4B: "GPS Assistance Data", 0x4C: "Deciphering Keys",
             0x4D: "Return Error Request", 0x4E: "Return Error Cause", 0x4F: "Segmentation",
             0x50: "Service Handover", 0x51: "Source RNC to target RNC transparent information (UMTS)",
             0x52: "Source RNC to target RNC transparent information (cdma2000)", 0x53: "GERAN Classmark",
             0x54: "GERAN BSC Container", 0x55: "Velocity Estimate"}
ПРИЧИНЫ_BSSMAP = {0x00: "Radio interface message failure", 0x01: "Radio interface failure",
                  0x02: "Uplink quality", 0x03: "Uplink strength", 0x04: "Downlink quality",
                  0x05: "Downlink strength", 0x06: "Distance", 0x07: "O and M intervention",
                  0x08: "Response to MSC invocation", 0x09: "Call control",
                  0x0A: "Radio interface failure, reversion to old channel", 0x0B: "Handover successful",
                  0x0C: "Better Cell", 0x0D: "Directed Retry", 0x0E: "Joined group call channel",
                  0x0F: "Traffic", 0x10: "Reduce load in serving cell",
                  0x11: "Traffic load in target cell higher than in source cell", 0x12: "Relocation triggered",
                  0x14: "Requested option not authorised", 0x15: "Alternative channel configuration requested",
                  0x16: "Response to an INTERNAL HANDOVER ENQUIRY message",
                  0x17: "INTERNAL HANDOVER ENQUIRY reject", 0x18: "Redundancy Level not adequate",
                  0x20: "Equipment failure", 0x21: "No radio resource available",
                  0x22: "Requested terrestrial resource unavailable", 0x23: "CCCH overload",
                  0x24: "Processor overload", 0x25: "BSS not equipped", 0x26: "MS not equipped",
                  0x27: "Invalid cell", 0x28: "Traffic Load", 0x29: "Preemption",
                  0x2A: "DTM Handover - SGSN Failure", 0x2B: "DTM Handover - PS Allocation failure",
                  0x30: "Requested transcoding/rate adaption unavailable", 0x31: "Circuit pool mismatch",
                  0x32: "Switch circuit pool", 0x33: "Requested speech version unavailable",
                  0x34: "LSA not allowed", 0x35: "Requested Codec Type or Codec Configuration unavailable",
                  0x36: "Requested A-Interface Type unavailable", 0x37: "Invalid CSG cell",
                  0x3F: "Requested Redundancy Level not available", 0x40: "Ciphering algorithm not supported",
                  0x41: "GERAN Iu-mode failure",
                  0x42: "Incoming Relocation Not Supported Due To PUESBINE Feature",
                  0x43: "Access Restricted Due to Shared Networks",
                  0x44: "Requested Codec Type or Codec Configuration not supported",
                  0x45: "Requested A-Interface Type not supported",
                  0x46: "Requested Redundancy Level not supported", 0x47: "Reserved for international use",
                  0x50: "Terrestrial circuit already allocated", 0x51: "Invalid message contents",
                  0x52: "Information element or field missing", 0x53: "Incorrect value",
                  0x54: "Unknown Message type", 0x55: "Unknown Information Element",
                  0x56: "DTM Handover - Invalid PS Indication", 0x57: "Call Identifier already allocated",
                  0x60: "Protocol Error between BSS and MSC", 0x61: "VGCS/VBS call non existent",
                  0x62: "DTM Handover - Timer Expiry"}
#: Элементы TV: полная длина вместе с IEI (48.008 3.2.2; TV — по Wireshark, длины — по разделам 3.2.2.x).
TV_BSSMAP = {0x01: 3, 0x03: 21, 0x0C: 2, 0x0D: 2, 0x0E: 2, 0x14: 2, 0x15: 2, 0x18: 2, 0x19: 2, 0x1C: 2,
             0x1D: 2, 0x21: 2, 0x22: 5, 0x23: 2, 0x24: 2, 0x25: 2, 0x27: 3, 0x2B: 2, 0x2C: 2, 0x2D: 2,
             0x2F: 2, 0x31: 2, 0x32: 2, 0x33: 2, 0x38: 2, 0x39: 2, 0x3F: 2, 0x40: 2}
ВИДЫ_ЯЧЕЙКИ = {3: "без ячейки", 6: "все ячейки BSS"}
РЕЧЬ_ДАННЫЕ = {1: "речь", 2: "данные", 3: "сигнализация"}


def _ячейка(б: bytes) -> str:
    """Cell Identifier (3.2.2.17): дискриминатор в младшем полубайте, затем идентификатор."""
    вид = б[0] & 0x0F if б else -1
    if вид == 0 and len(б) >= 8:
        return f"CGI {_plmn(б[1:4])}, LAC {u16(б, 4)}, CI {u16(б, 6)}"
    if вид == 1 and len(б) >= 5:
        return f"LAC {u16(б, 1)}, CI {u16(б, 3)}"
    if вид == 2 and len(б) >= 3:
        return f"CI {u16(б, 1)}"
    if вид == 4 and len(б) >= 6:
        return f"LAI {_plmn(б[1:4])}, LAC {u16(б, 4)}"
    if вид == 5 and len(б) >= 3:
        return f"LAC {u16(б, 1)}"
    return ВИДЫ_ЯЧЕЙКИ.get(вид, f"вид {вид}")


def _элементы(д: bytes, м: int, конец: int):
    """[(IEI, начало значения, длина значения, начало элемента)] или None, если не укладываются точно."""
    итог = []
    while м < конец:
        iei = д[м]
        if iei in TV_BSSMAP:
            n, начало = TV_BSSMAP[iei] - 1, м + 1
        else:
            if м + 2 > конец:
                return None
            n, начало = д[м + 1], м + 2
        if начало + n > конец:
            return None
        итог.append((iei, начало, n, м))
        м = начало + n
    return итог


def _bssmap(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if конец - м < 3 or д[м + 1] != конец - м - 2 or д[м + 2] not in BSSMAP:
        return False
    элементы = _элементы(д, м + 3, конец)
    if элементы is None:
        return False
    тип = д[м + 2]
    у = р.уровень("BSSMAP", "BSS Management Application Part (TS 48.008)", м)
    у.поле("Дискриминатор", "gsm_a.bssmap.msgtype_discriminator", "BSSMAP", м, 1, 0)
    у.поле("Длина", "bssap.length", д[м + 1], м + 1, 1)
    у.поле("Тип сообщения", "gsm_a.bssmap.msgtype", f"0x{тип:02x} ({BSSMAP[тип]})", м + 2, 1, тип)
    сводка, вложенные = [BSSMAP[тип]], []
    for iei, x, n, начало in элементы:
        знач = д[x:x + n]
        п = у.поле(IE_BSSMAP.get(iei, f"Элемент 0x{iei:02x}"), "gsm_a.bssmap.elem_id", iei, начало, x + n - начало)
        текст = None
        if iei == 0x01 and n == 2:
            cic = u16(знач, 0)
            текст = f"мультиплексор {cic >> 5}, интервал {cic & 0x1F}"
            у.поле("CIC", "gsm_a.bssmap.cic", cic, x, 2, родитель=п)
        elif iei == 0x04 and n >= 1:
            причина = знач[0] & 0x7F
            текст = ПРИЧИНЫ_BSSMAP.get(причина, f"причина {причина}")
            у.поле("Причина", "gsm_a.bssmap.cause", текст, x, n, причина, родитель=п)
            сводка.append(f"причина: {текст}")
        elif iei == 0x05 and n >= 1:
            текст = _ячейка(знач)
            у.поле("Ячейка", "gsm_a.bssmap.cell_id", текст, x, n, родитель=п)
            сводка.append(текст)
        elif iei in (0x08, 0x29) and (и := gsm.идентификатор(знач)):
            текст = f"{и[0]} {и[1]}"
            у.поле(и[0], "e212.imsi" if и[0] == "IMSI" else "gsm_a.bssmap.mobile_identity", и[1], x, n, родитель=п)
            сводка.append(текст)
        elif iei == 0x09 and n == 4:
            текст = знач.hex().upper()
            у.поле("TMSI", "gsm_a.tmsi", текст, x, 4, родитель=п)
            сводка.append(f"TMSI {текст}")
        elif iei == 0x0A and n >= 1:
            разрешено = [f"A5/{i}" for i in range(8) if знач[0] >> i & 1]
            у.поле("Разрешённые алгоритмы", "gsm_a.bssmap.enc_info.permitted", ", ".join(разрешено) or "нет",
                   x, 1, знач[0], родитель=п)
            if n > 1:
                у.поле("Ключ Kc", "gsm_a.bssmap.enc_info_key", знач[1:].hex(), x + 1, n - 1, родитель=п)
            текст = ", ".join(разрешено) + (f"; Kc {знач[1:].hex()}" if n > 1 else "")
            сводка.append(f"шифры {', '.join(разрешено) or 'нет'}")
        elif iei == 0x0B and n >= 2:
            текст = f"{РЕЧЬ_ДАННЫЕ.get(знач[0] & 0x0F, знач[0] & 0x0F)}, вид канала 0x{знач[1]:02x}"
            у.поле("Речь/данные", "gsm_a.bssmap.speech_data_ind", РЕЧЬ_ДАННЫЕ.get(знач[0] & 0x0F, знач[0] & 0x0F),
                   x, 1, знач[0] & 0x0F, родитель=п)
        elif iei == 0x2C and n == 1:
            текст = "без шифра (A5/0)" if знач[0] == 1 else f"A5/{знач[0] - 1}"
            у.поле("Выбранный алгоритм", "gsm_a.bssmap.enc_alg", текст, x, 1, знач[0], родитель=п)
            сводка.append(f"выбран {текст}")
        elif iei in (0x17, 0x20) and n:
            вложенные.append((x, x + n))
            текст = f"{n} байт"
        п.текст = текст if текст is not None else знач.hex()
    у.длина = конец - м
    у.итог = ", ".join(сводка)
    р.п.инфо = "BSSMAP " + у.итог
    for начало, край in вложенные:
        oks7._дальше(р, gsm.dtap, начало, край, "сообщение уровня 3")
        if not р.п.инфо.startswith("BSSMAP"):                   # сводка сообщения уровня 3 — рядом со своей
            р.п.инфо = f"BSSMAP {у.итог}; {р.п.инфо}"
    return True


def _dtap(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if конец - м < 5 or д[м + 2] != конец - м - 3 or д[м + 1] & 0x38:
        return False
    уровней = len(р.п.уровни)
    у = р.уровень("DTAP", "BSSAP DTAP (TS 48.008, 3.1)", м)
    у.поле("Дискриминатор", "gsm_a.bssmap.msgtype_discriminator", "DTAP", м, 1, 1)
    у.поле("DLCI: SAPI", "gsm_a.dtap.dlci.sapi", д[м + 1] & 7, м + 1, 1)
    у.поле("DLCI: канал", "gsm_a.dtap.dlci.cc", д[м + 1] >> 6, м + 1, 1)
    у.поле("Длина", "bssap.length", д[м + 2], м + 2, 1)
    у.длина = 3
    у.итог = f"SAPI {д[м + 1] & 7}"
    if not gsm.dtap(р, м + 3, конец):
        del р.п.уровни[уровней:]
        return False
    return True


def bssap(р: Разбор, м: int, конец: int) -> bool:
    if м >= конец:
        return False
    if р.д[м] == 0x00:
        return _bssmap(р, м, конец)
    if р.д[м] == 0x01:
        return _dtap(р, м, конец)
    return False


oks7.SCCP_ПОДСИСТЕМЫ[254] = bssap
oks7.SCCP_СОЕДИНЕНИЯ.append(bssap)
for _протокол in ("BSSMAP", "DTAP"):
    ДОП_УРОВНИ[_протокол] = "прикладной"
