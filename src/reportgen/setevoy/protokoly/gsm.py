"""GSM: сообщения уровня 3 (3GPP TS 44.018 / 24.008) поверх LAPDm (44.006) — из GSMTAP.

- **BCCH/CCCH** (подтипы GSMTAP BCCH, CCCH, AGCH, PCH): октет псевдодлины
  (L2 pseudo length: длина × 4 + 0b01), затем сообщение RR — системная
  информация, вызовы (Paging Request), назначения (Immediate Assignment);
- **SDCCH/SACCH/FACCH**: кадр LAPDm — адрес (LPD, SAPI 0 — RR/MM/CC, 3 — SMS,
  C/R, EA), управление (I/S/U по модулю 8), указатель длины (L, M, EL), в
  кадре SACCH — ещё два байта заголовка L1 (мощность и опережение);
- **сообщение**: дискриминатор протокола (младший полубайт первого байта: 3 — CC,
  5 — MM, 6 — RR, 8 — GMM, 9 — SMS, 10 — SM, 11 — SS), тип сообщения (у MM, CC и SS
  два старших бита — порядковый номер N(SD), в тип не входят; у GMM и SM тип — весь
  октет, 24.007 11.2.3.2.3).

Из сообщений читается главное: идентификатор абонента (IMSI, TMSI, IMEI —
24.008 10.5.1.4), LAI (MCC, MNC, LAC — 10.5.1.3), идентификатор соты,
набранный номер CC SETUP (10.5.4.7); у GPRS — личность из Attach Request и Identity
Response (9.4.1, 9.4.13), точка доступа (APN, 10.5.6.1) из Activate PDP Context
Request (9.5.1), адрес PDP (10.5.6.4) из Activate PDP Context Accept (9.5.2), причина
SM (10.5.6.6) из Deactivate PDP Context Request (9.5.14). Названия сообщений — по таблицам Wireshark
(packet-gsm_a_rr.c gsm_a_dtap_msg_rr_strings, packet-gsm_a_dtap.c
gsm_a_dtap_msg_mm/cc/sms/ss_strings, protocol_discriminator_vals).
"""

from __future__ import annotations

import ipaddress

from ..razbor import ДОП_УРОВНИ, Разбор, данные

ДИСКРИМИНАТОРЫ = {0x0: "GCC", 0x1: "BCC", 0x3: "CC", 0x4: "GTTP", 0x5: "MM", 0x6: "RR", 0x8: "GMM",
                  0x9: "SMS", 0xA: "SM", 0xB: "SS", 0xC: "LS"}

RR = {0x3B: "Additional Assignment", 0x3F: "Immediate Assignment", 0x39: "Immediate Assignment Extended",
      0x3A: "Immediate Assignment Reject", 0x35: "Ciphering Mode Command", 0x32: "Ciphering Mode Complete",
      0x2E: "Assignment Command", 0x29: "Assignment Complete", 0x2F: "Assignment Failure",
      0x2B: "Handover Command", 0x2C: "Handover Complete", 0x28: "Handover Failure",
      0x2D: "Physical Information", 0x0D: "Channel Release", 0x0A: "Partial Release",
      0x0F: "Partial Release Complete", 0x21: "Paging Request Type 1", 0x22: "Paging Request Type 2",
      0x24: "Paging Request Type 3", 0x27: "Paging Response", 0x20: "Notification/NCH",
      0x26: "Notification/Response", 0x18: "System Information Type 8", 0x19: "System Information Type 1",
      0x1A: "System Information Type 2", 0x1B: "System Information Type 3", 0x1C: "System Information Type 4",
      0x1D: "System Information Type 5", 0x1E: "System Information Type 6", 0x1F: "System Information Type 7",
      0x02: "System Information Type 2bis", 0x03: "System Information Type 2ter",
      0x07: "System Information Type 2quater", 0x05: "System Information Type 5bis",
      0x06: "System Information Type 5ter", 0x04: "System Information Type 9",
      0x00: "System Information Type 13", 0x3D: "System Information Type 16",
      0x3E: "System Information Type 17", 0x10: "Channel Mode Modify", 0x12: "RR Status",
      0x17: "Channel Mode Modify Acknowledge", 0x14: "Frequency Redefinition",
      0x15: "Measurement Report", 0x16: "Classmark Change", 0x13: "Classmark Enquiry"}
MM = {0x01: "IMSI Detach Indication", 0x02: "Location Updating Accept", 0x04: "Location Updating Reject",
      0x08: "Location Updating Request", 0x11: "Authentication Reject", 0x12: "Authentication Request",
      0x14: "Authentication Response", 0x1C: "Authentication Failure", 0x18: "Identity Request",
      0x19: "Identity Response", 0x1A: "TMSI Reallocation Command", 0x1B: "TMSI Reallocation Complete",
      0x21: "CM Service Accept", 0x22: "CM Service Reject", 0x23: "CM Service Abort",
      0x24: "CM Service Request", 0x25: "CM Service Prompt", 0x28: "CM Re-establishment Request",
      0x29: "Abort", 0x30: "MM Null", 0x31: "MM Status", 0x32: "MM Information"}
CC = {0x01: "Alerting", 0x08: "Call Confirmed", 0x02: "Call Proceeding", 0x07: "Connect",
      0x0F: "Connect Acknowledge", 0x0E: "Emergency Setup", 0x03: "Progress", 0x05: "Setup",
      0x17: "Modify", 0x1F: "Modify Complete", 0x13: "Modify Reject", 0x10: "User Information",
      0x18: "Hold", 0x19: "Hold Acknowledge", 0x1A: "Hold Reject", 0x1C: "Retrieve",
      0x1D: "Retrieve Acknowledge", 0x1E: "Retrieve Reject", 0x25: "Disconnect", 0x2D: "Release",
      0x2A: "Release Complete", 0x39: "Congestion Control", 0x3E: "Notify", 0x3D: "Status",
      0x34: "Status Enquiry", 0x35: "Start DTMF", 0x31: "Stop DTMF", 0x32: "Stop DTMF Acknowledge",
      0x36: "Start DTMF Acknowledge", 0x37: "Start DTMF Reject", 0x3A: "Facility"}
SMS = {0x01: "CP-DATA", 0x04: "CP-ACK", 0x10: "CP-ERROR"}
SS = {0x2A: "Release Complete", 0x3A: "Facility", 0x3B: "Register"}
#: GPRS (24.008 10.4, таблицы 10.4 и 10.4a; Wireshark packet-gsm_a_gm.c gsm_a_dtap_msg_gmm/sm_strings).
GMM = {1: 'Attach Request',
 2: 'Attach Accept',
 3: 'Attach Complete',
 4: 'Attach Reject',
 5: 'Detach Request',
 6: 'Detach Accept',
 8: 'Routing Area Update Request',
 9: 'Routing Area Update Accept',
 10: 'Routing Area Update Complete',
 11: 'Routing Area Update Reject',
 12: 'Service Request',
 13: 'Service Accept',
 14: 'Service Reject',
 16: 'P-TMSI Reallocation Command',
 17: 'P-TMSI Reallocation Complete',
 18: 'Authentication and Ciphering Req',
 19: 'Authentication and Ciphering Resp',
 20: 'Authentication and Ciphering Rej',
 21: 'Identity Request',
 22: 'Identity Response',
 28: 'Authentication and Ciphering Failure',
 32: 'GMM Status',
 33: 'GMM Information'}
SM = {65: 'Activate PDP Context Request',
 66: 'Activate PDP Context Accept',
 67: 'Activate PDP Context Reject',
 68: 'Request PDP Context Activation',
 69: 'Request PDP Context Activation rej.',
 70: 'Deactivate PDP Context Request',
 71: 'Deactivate PDP Context Accept',
 72: 'Modify PDP Context Request(Network to MS direction)',
 73: 'Modify PDP Context Accept (MS to network direction)',
 74: 'Modify PDP Context Request(MS to network direction)',
 75: 'Modify PDP Context Accept (Network to MS direction)',
 76: 'Modify PDP Context Reject',
 77: 'Activate Secondary PDP Context Request',
 78: 'Activate Secondary PDP Context Accept',
 79: 'Activate Secondary PDP Context Reject',
 80: 'Reserved: was allocated in earlier phases of the protocol',
 81: 'Reserved: was allocated in earlier phases of the protocol',
 82: 'Reserved: was allocated in earlier phases of the protocol',
 83: 'Reserved: was allocated in earlier phases of the protocol',
 84: 'Reserved: was allocated in earlier phases of the protocol',
 85: 'SM Status',
 86: 'Activate MBMS Context Request',
 87: 'Activate MBMS Context Accept',
 88: 'Activate MBMS Context Reject',
 89: 'Request MBMS Context Activation',
 90: 'Request MBMS Context Activation Reject',
 91: 'Request Secondary PDP Context Activation',
 92: 'Request Secondary PDP Context Activation Reject',
 93: 'Notification'}
ТИПЫ = {0x6: RR, 0x5: MM, 0x3: CC, 0x9: SMS, 0xB: SS, 0x8: GMM, 0xA: SM}
#: У MM, CC и SS биты 8–7 типа — N(SD) (24.007 11.2.3.2.3), в тип не входят.
С_НОМЕРОМ = (0x5, 0x3, 0xB)

ВИДЫ_ИДЕНТ = {1: "IMSI", 2: "IMEI", 3: "IMEISV", 4: "TMSI/P-TMSI", 0: "нет"}


def _цифры(байты: bytes, первая_старшая: int | None = None) -> str:
    """BCD: младший полубайт — раньше; 0xF — заполнитель."""
    итог = [] if первая_старшая is None else [первая_старшая]
    for б in байты:
        итог += [б & 0x0F, б >> 4]
    return "".join(str(ц) for ц in итог if ц < 10)


def идентификатор(тело: bytes) -> tuple[str, str] | None:
    """Mobile Identity (24.008 10.5.1.4) без длины: (вид, значение)."""
    if not тело:
        return None
    вид = тело[0] & 7
    if вид == 4:
        return "TMSI", тело[1:5].hex().upper() if len(тело) >= 5 else тело[1:].hex().upper()
    if вид in (1, 2, 3):
        # Первая цифра — в старшем полубайте первого октета; при чётном числе цифр последний
        # полубайт — заполнитель 0xF (он в цифры не попадает).
        return ВИДЫ_ИДЕНТ[вид], _цифры(тело[1:], тело[0] >> 4)
    return None


def lai(тело: bytes) -> str | None:
    """LAI (10.5.1.3): MCC и MNC цифрами BCD, LAC — 16 бит."""
    if len(тело) < 5:
        return None
    mcc = f"{тело[0] & 0xF}{тело[0] >> 4}{тело[1] & 0xF}"
    mnc = f"{тело[2] & 0xF}{тело[2] >> 4}" + ("" if тело[1] >> 4 == 0xF else f"{тело[1] >> 4}")
    return f"MCC {mcc}, MNC {mnc}, LAC {int.from_bytes(тело[3:5], 'big')}"


def _tlv(тело: bytes, место: int):
    """Необязательные элементы 24.008 (11.2.1.1): старший бит IEI — элемент в один октет, иначе TLV."""
    while место < len(тело):
        iei = тело[место]
        if iei & 0x80:
            место += 1
            continue
        if место + 2 > len(тело) or место + 2 + тело[место + 1] > len(тело):
            return
        yield iei, место + 2, тело[место + 1]
        место += 2 + тело[место + 1]


def _apn(б: bytes) -> str:
    """APN (10.5.6.1; RFC 1035 3.1): метки с байтом длины — через точку."""
    части, x = [], 0
    while x < len(б):
        части.append(б[x + 1:x + 1 + б[x]].decode("latin-1"))
        x += 1 + б[x]
    return ".".join(части)


def _адрес_pdp(б: bytes) -> str:
    """Адрес PDP (10.5.6.4): организация (1 — IETF), номер вида (0x21 IPv4, 0x57 IPv6, 0x8D IPv4v6), адрес."""
    if len(б) < 2 or б[0] & 0x0F != 1:
        return ""
    вид, адрес = б[1], б[2:]
    if вид == 0x21 and len(адрес) == 4:
        return str(ipaddress.IPv4Address(адрес))
    if вид == 0x57 and len(адрес) == 16:
        return str(ipaddress.IPv6Address(адрес))
    if вид == 0x8D and len(адрес) == 20:
        return f"{ipaddress.IPv4Address(адрес[:4])}, {ipaddress.IPv6Address(адрес[4:])}"
    return ""


def _сведения(pd: int, тип: int, д: bytes, м: int, конец: int) -> str:
    """Главное из сообщения — одной строкой (для итога)."""
    тело = д[м:конец]
    try:
        if pd == 6 and тип == 0x21 and len(тело) >= 3:           # Paging Request Type 1
            дл = тело[1]
            и = идентификатор(тело[2:2 + дл])
            return f"{и[0]} {и[1]}" if и else ""
        if pd == 6 and тип in (0x1B,) and len(тело) >= 7:         # SI 3: Cell Identity, LAI
            return f"CI {int.from_bytes(тело[0:2], 'big')}, {lai(тело[2:7])}"
        if pd == 6 and тип == 0x1C and len(тело) >= 5:            # SI 4: LAI
            return lai(тело[0:5]) or ""
        if pd == 6 and тип == 0x27 and len(тело) >= 6:            # Paging Response: CKSN, classmark 2, ident
            дл_к = тело[1]
            место = 2 + дл_к
            и = идентификатор(тело[место + 1:место + 1 + тело[место]])
            return f"{и[0]} {и[1]}" if и else ""
        if pd == 5 and тип == 0x08 and len(тело) >= 8:            # LU Request: тип, LAI, classmark 1, ident
            и = идентификатор(тело[8:8 + тело[7]])
            return f"{lai(тело[1:6])}; {и[0]} {и[1]}" if и else (lai(тело[1:6]) or "")
        if pd == 5 and тип in (0x19,) and len(тело) >= 2:         # Identity Response
            и = идентификатор(тело[1:1 + тело[0]])
            return f"{и[0]} {и[1]}" if и else ""
        if pd == 5 and тип == 0x24 and len(тело) >= 5:            # CM Service Request: тип, classmark 2, ident
            место = 1 + 1 + тело[1]
            и = идентификатор(тело[место + 1:место + 1 + тело[место]])
            return f"{и[0]} {и[1]}" if и else ""
        if pd == 8 and тип == 0x01 and len(тело) >= 1:            # Attach Request: сеть MS LV, вид+CKSN, DRX
            место = 1 + тело[0] + 1 + 2
            и = идентификатор(тело[место + 1:место + 1 + тело[место]])
            return f"{и[0]} {и[1]}" if и else ""
        if pd == 8 and тип == 0x16 and len(тело) >= 1:            # Identity Response (GMM)
            и = идентификатор(тело[1:1 + тело[0]])
            return f"{и[0]} {и[1]}" if и else ""
        if pd == 0xA and тип == 0x41 and len(тело) >= 3:          # Activate PDP Context Request
            место = 2 + 1 + тело[2]                               # NSAPI, LLC SAPI, QoS LV
            место += 1 + тело[место]                              # адрес PDP LV
            for iei, начало, дл in _tlv(тело, место):
                if iei == 0x28:
                    return f"APN {_apn(тело[начало:начало + дл])}"
            return ""
        if pd == 0xA and тип == 0x42 and len(тело) >= 2:          # Activate PDP Context Accept
            место = 1 + 1 + тело[1] + 1                           # LLC SAPI, QoS LV, приоритет
            for iei, начало, дл in _tlv(тело, место):
                if iei == 0x2B:
                    адрес = _адрес_pdp(тело[начало:начало + дл])
                    return f"адрес {адрес}" if адрес else ""
            return ""
        if pd == 0xA and тип in (0x43, 0x46) and len(тело) >= 1:  # Reject / Deactivate Request: причина SM
            return f"причина SM {тело[0]}"
        if pd == 3 and тип in (0x05, 0x0E):                       # Setup: номер вызываемого (IEI 0x5E)
            место = 0
            while место + 2 <= len(тело):
                iei = тело[место]
                if iei == 0x5E:
                    дл = тело[место + 1]
                    номер = _цифры(тело[место + 3:место + 2 + дл])
                    return f"номер {номер}"
                if iei & 0x80:                                    # однобайтовый элемент (тип 1/2)
                    место += 1
                else:
                    место += 2 + тело[место + 1]
    except IndexError:
        return ""
    return ""


def dtap(р: Разбор, м: int, конец: int) -> bool:
    """Сообщение уровня 3: дискриминатор, тип, главное из тела."""
    д = р.д
    if м + 2 > конец:
        return False
    pd, верх = д[м] & 0x0F, д[м] >> 4
    сырой_тип = д[м + 1]
    тип = сырой_тип & 0x3F if pd in С_НОМЕРОМ else сырой_тип
    имена = ТИПЫ.get(pd)
    if имена is None or тип not in имена:
        return False
    имя_pd = ДИСКРИМИНАТОРЫ[pd]
    у = р.уровень(f"GSM {имя_pd}", f"GSM 04.08 / 24.008 — {имя_pd}", м)
    у.поле("Дискриминатор протокола", "gsm_a.L3_protocol_discriminator", f"{pd} ({имя_pd})", м, 1, pd)
    у.поле("Индикатор пропуска / TI" if pd != 6 else "Индикатор пропуска", "gsm_a.skip.ind", верх, м, 1)
    у.поле("Тип сообщения", f"gsm_a.dtap.msg_{имя_pd.lower()}_type", f"0x{тип:02x} ({имена[тип]})", м + 1, 1, тип)
    у.длина = 2
    сведения = _сведения(pd, тип, д, м + 2, конец)
    if сведения:
        у.поле("Главное", "gsm_a.summary", сведения, м + 2, конец - м - 2)
    у.итог = имена[тип] + (f": {сведения}" if сведения else "")
    р.п.инфо = f"GSM {имя_pd} " + у.итог
    if м + 2 < конец:
        данные(р, м + 2, "элементы сообщения", конец)
        р.п.инфо = f"GSM {имя_pd} " + у.итог
    return True


def ccch(р: Разбор, м: int, конец: int) -> bool:
    """BCCH/CCCH: октет псевдодлины (44.018 10.5.2.19: L × 4 + 0b01) и сообщение RR."""
    д = р.д
    if м + 3 > конец or д[м] & 0x03 != 0x01:
        return False
    длина = д[м] >> 2
    у = р.уровень("L2 pseudo", "Псевдодлина уровня 2 (CCCH/BCCH)", м)
    у.поле("Длина", "gsm_a.rr.l2_pseudo_len", длина, м, 1)
    у.длина = 1
    у.итог = f"{длина} байт"
    if not dtap(р, м + 1, min(конец, м + 1 + длина)):
        данные(р, м + 1, "сообщение RR", конец)
    return True


LAPDM_U = {0x2F: "SABM", 0x0F: "DM", 0x03: "UI", 0x43: "DISC", 0x63: "UA"}
LAPDM_S = {0x01: "RR", 0x05: "RNR", 0x09: "REJ"}


def lapdm(р: Разбор, м: int, конец: int, *, sacch: bool = False) -> bool:
    """LAPDm (44.006): [SACCH — два байта L1], адрес, управление, указатель длины, сведения."""
    д = р.д
    if sacch:
        у1 = р.уровень("SACCH L1", "Заголовок L1 SACCH", м)
        у1.поле("Уровень мощности", "gsm_a.l1.power", д[м] & 0x1F, м, 1)
        у1.поле("Опережение (TA)", "gsm_a.l1.ta", д[м + 1] & 0x7F if м + 1 < конец else 0, м + 1, 1)
        у1.длина = 2
        у1.итог = f"мощность {д[м] & 0x1F}, TA {д[м + 1] & 0x7F if м + 1 < конец else 0}"
        м += 2
    if м + 3 > конец:
        return False
    адрес, упр, длина_поле = д[м], д[м + 1], д[м + 2]
    sapi, cr, lpd, ea = (адрес >> 2) & 7, (адрес >> 1) & 1, (адрес >> 5) & 3, адрес & 1
    if not ea or lpd not in (0, 1) or not длина_поле & 1:
        return False
    длина, ещё = длина_поле >> 2, (длина_поле >> 1) & 1
    у = р.уровень("LAPDm", "Link Access Procedure on the Dm channel (44.006)", м)
    у.поле("SAPI", "lapdm.sapi", f"{sapi} ({'RR/MM/CC' if sapi == 0 else 'SMS/SS' if sapi == 3 else '?'})", м, 1, sapi)
    у.поле("C/R", "lapdm.cr", cr, м, 1)
    if not упр & 1:
        вид = f"I, N(S)={(упр >> 1) & 7}, N(R)={упр >> 5}"
    elif упр & 3 == 1:
        вид = f"{LAPDM_S.get(упр & 0x0F, 'S')}, N(R)={упр >> 5}"
    else:
        вид = LAPDM_U.get(упр & 0xEF, f"U 0x{упр:02x}")
    у.поле("Управление", "lapdm.control", f"0x{упр:02x} ({вид})", м + 1, 1, упр)
    у.поле("Длина", "lapdm.length", длина, м + 2, 1)
    у.поле("Ещё (M)", "lapdm.m", ещё, м + 2, 1)
    у.длина = 3
    у.итог = f"SAPI {sapi}, {вид}, {длина} байт" + (", фрагмент" if ещё else "")
    р.п.инфо = "LAPDm " + у.итог
    if длина and not ещё:
        if not dtap(р, м + 3, min(конец, м + 3 + длина)):
            данные(р, м + 3, "сведения LAPDm", min(конец, м + 3 + длина))
    elif длина:
        данные(р, м + 3, "фрагмент сообщения (M = 1)", min(конец, м + 3 + длина))
    return True


#: Подтипы GSMTAP Um: BCCH, CCCH, AGCH, PCH — с псевдодлиной; SDCCH и TCH — LAPDm.
ПСЕВДОДЛИНА = frozenset({1, 2, 4, 5})
С_LAPDM = frozenset({6, 7, 8, 9, 10})


def um(р: Разбор, м: int, конец: int, подтип: int) -> bool:
    """Нагрузка GSMTAP типа Um по подтипу канала (0x80 — ACCH: у SACCH два байта L1)."""
    канал, acch = подтип & 0x7F, bool(подтип & 0x80)
    if канал in ПСЕВДОДЛИНА and not acch:
        return ccch(р, м, конец)
    if канал in С_LAPDM or acch:
        return lapdm(р, м, конец, sacch=acch)
    return False


for _имя, _уровень in (("LAPDm", "канальный"), ("SACCH L1", "канальный"), ("L2 pseudo", "канальный"),
                       ("GSM RR", "прикладной"), ("GSM MM", "прикладной"), ("GSM CC", "прикладной"),
                       ("GSM SMS", "прикладной"), ("GSM SS", "прикладной")):
    ДОП_УРОВНИ.setdefault(_имя, _уровень)
