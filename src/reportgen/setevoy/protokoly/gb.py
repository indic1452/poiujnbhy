"""Интерфейс Gb GPRS: NS (3GPP TS 48.016) поверх UDP или Frame Relay, BSSGP (48.018), LLC (44.064),
SNDCP (44.065) — и внутри сообщения GMM/SM/SMS (24.008, 24.011) и IP-пакеты абонента.

Документы (структуры — строго по ним):

* NS — 48.016, 9.2 (PDU) и 10.3 (элементы): тип PDU (октет); NS-UNITDATA — биты управления (R, C),
  BVCI (16 бит), NS SDU (BSSGP); прочие — элементы TLV (длина: бит 8 первого октета 1 — длина 7 бит,
  0 — 15 бит в двух октетах, 10.3.1), в SNS-SIZE — TV (Reset Flag, Maximum Number of NS-VCs, Number of
  IP4/IP6 Endpoints), IP Address — TV (вид адреса, адрес), в SNS-ACK/ADD/CHANGEWEIGHT/DELETE после NSEI —
  номер транзакции (V), в SNS-CONFIG первым — End Flag (V); списки IP4/IP6 Elements — адрес, порт UDP,
  веса сигнализации и данных. Раскладка PDU, имена и причины — Wireshark packet-nsip.c. Поверх UDP —
  порты 2157 и 19999 (как в Wireshark) и 23000 (так по умолчанию у Osmocom); поверх FR — без
  инкапсуляции RFC 2427 (48.016, 6.2), по признакам.
* BSSGP — 48.018, 10 (PDU) и 11 (элементы): DL-UNITDATA и UL-UNITDATA — TLLI (V, 4) и QoS Profile
  (V, 3: пиковая скорость, шаг скорости — биты 8–7, 11.3.28), затем TLV; прочие PDU — только TLV (длина —
  как в NS, 11.1). Элементы: BVCI, NSEI, Cause, Cell Identifier (RAI + CI, 11.3.9), IMSI и Mobile Id
  (24.008 10.5.1.4), TLLI, TMSI, Routeing Area (10.5.5.15), Location Area, PDU Lifetime (сотые доли
  секунды, 0xFFFF — бесконечно), размеры и скорости «ведра» (100 октетов, 100 бит/с), LLC-PDU. Имена
  PDU, всех 133 элементов и причин — таблицы Wireshark packet-bssgp.c (имена элементов, заимствованных из
  24.008/48.008/49.031, — из их таблиц *_elem_strings).
* TLLI — 23.003, 2.6: вид по старшим битам (11 — местный, 10 — чужой, 01111 — случайный, 01110 —
  вспомогательный).
* LLC — 44.064, 6: адрес (PD, C/R, SAPI), управление — I (3 октета), S (2), UI (2: N(U) 9 бит, E —
  шифрование, PM — FCS на всё поле информации), U (1: P/F и команда); FCS — CRC-24 (6.1.2: порождающий
  многочлен X^24+X^23+X^21+X^20+X^19+X^17+X^16+X^15+X^13+X^8+X^7+X^5+X^4+X^2+1, начальное 0xFFFFFF,
  дополнение, младший октет первым) на заголовок и информацию, у UI с PM=0 — на заголовок и первые
  N202 = 4 октета; XID — параметры (8.5.2). SAPI 1 — GMM/SM, 7 — SMS, 3/5/9/11 — SNDCP (Wireshark
  packet-gprs-llc.c, packet-sndcp.c); зашифрованное (E=1) не разбирается.
* SNDCP — 44.065, 7: X, F (первый сегмент), T (SN-UNITDATA), M (ещё сегменты), NSAPI; у первого
  сегмента — DCOMP/PCOMP, в подтверждаемом режиме — номер N-PDU (8 бит), в неподтверждаемом — номер
  сегмента (4 бита) и N-PDU (12 бит); целый несжатый N-PDU — пакет IP.
"""

from __future__ import annotations

import ipaddress

from .. import prilozh
from ..pole import u16, u32
from ..razbor import ДОП_УРОВНИ, Разбор, голый_ip, данные
from . import gsm, kanalnye
from .nas import _plmn

#: Таблицы — из Wireshark (packet-nsip.c, packet-bssgp.c), собраны программой без пропусков.
NS_PDU = {0x0: 'NS_UNITDATA', 0x2: 'NS_RESET', 0x3: 'NS_RESET_ACK', 0x4: 'NS_BLOCK', 0x5: 'NS_BLOCK_ACK',
          0x6: 'NS_UNBLOCK', 0x7: 'NS_UNBLOCK_ACK', 0x8: 'NS_STATUS', 0xa: 'NS_ALIVE', 0xb: 'NS_ALIVE_ACK',
          0xc: 'SNS_ACK', 0xd: 'SNS_ADD', 0xe: 'SNS_CHANGEWEIGHT', 0xf: 'SNS_CONFIG', 0x10: 'SNS_CONFIG_ACK',
          0x11: 'SNS_DELETE', 0x12: 'SNS_SIZE', 0x13: 'SNS_SIZE_ACK'}
NS_ПРИЧИНЫ = {0: 'Transit network failure', 1: 'O&M intervention', 2: 'Equipment failure', 3: 'NS-VC blocked',
              4: 'NS-VC unknown', 5: 'BVCI unknown on that NSE', 8: 'Semantically incorrect PDU',
              10: 'PDU not compatible with the protocol state', 11: 'Protocol error - unspecified',
              12: 'Invalid essential IE', 13: 'Missing essential IE', 14: 'Invalid number of IP4 endpoints',
              15: 'Invalid number of IP6 endpoints', 16: 'Invalid number of NS-VCs', 17: 'Invalid weights',
              18: 'Unknown IP endpoint', 19: 'Unknown IP address', 20: 'IP test failed'}
BSSGP_PDU = {0x0: 'DL-UNITDATA', 0x1: 'UL-UNITDATA', 0x2: 'RA-CAPABILITY', 0x3: 'Reserved', 0x4: 'DL-MBMS-UNITDATA',
             0x5: 'UL-MBMS-UNITDATA', 0x6: 'PAGING-PS', 0x7: 'PAGING-CS', 0x8: 'RA-CAPABILITY-UPDATE',
             0x9: 'RA-CAPABILITY-UPDATE-ACK', 0xa: 'RADIO-STATUS', 0xb: 'SUSPEND', 0xc: 'SUSPEND-ACK',
             0xd: 'SUSPEND-NACK', 0xe: 'RESUME', 0xf: 'RESUME-ACK', 0x10: 'RESUME-NACK', 0x11: 'PAGING-PS-REJECT',
             0x12: 'DUMMY-PAGING-PS', 0x13: 'DUMMY-PAGING-PS-RESPONSE', 0x14: 'MS-REGISTRATION-ENQUIRY',
             0x15: 'MS-REGISTRATION-ENQUIRY-RESPONSE', 0x16: 'Reserved', 0x17: 'Reserved', 0x18: 'Reserved',
             0x19: 'Reserved', 0x1a: 'Reserved', 0x1b: 'Reserved', 0x1c: 'Reserved', 0x1d: 'Reserved',
             0x1e: 'Reserved', 0x1f: 'Reserved', 0x20: 'BVC-BLOCK', 0x21: 'BVC-BLOCK-ACK', 0x22: 'BVC-RESET',
             0x23: 'BVC-RESET-ACK', 0x24: 'UNBLOCK', 0x25: 'UNBLOCK-ACK', 0x26: 'FLOW-CONTROL-BVC',
             0x27: 'FLOW-CONTROL-BVC-ACK', 0x28: 'FLOW-CONTROL-MS', 0x29: 'FLOW-CONTROL-MS-ACK', 0x2a: 'FLUSH-LL',
             0x2b: 'FLUSH_LL_ACK', 0x2c: 'LLC-DISCARDED', 0x2d: 'FLOW-CONTROL-PFC', 0x2e: 'FLOW-CONTROL-PFC-ACK',
             0x2f: 'Reserved', 0x30: 'Reserved', 0x31: 'Reserved', 0x32: 'Reserved', 0x33: 'Reserved',
             0x34: 'Reserved', 0x35: 'Reserved', 0x36: 'Reserved', 0x37: 'Reserved', 0x38: 'Reserved',
             0x39: 'Reserved', 0x3a: 'Reserved', 0x3b: 'Reserved', 0x3c: 'Reserved', 0x3d: 'Reserved',
             0x3e: 'Reserved', 0x3f: 'Reserved', 0x40: 'SGSN-INVOKE-TRACE', 0x41: 'STATUS', 0x42: 'OVERLOAD',
             0x43: 'Reserved', 0x44: 'Reserved', 0x45: 'Reserved', 0x46: 'Reserved', 0x47: 'Reserved',
             0x48: 'Reserved', 0x49: 'Reserved', 0x4a: 'Reserved', 0x4b: 'Reserved', 0x4c: 'Reserved',
             0x4d: 'Reserved', 0x4e: 'Reserved', 0x4f: 'Reserved', 0x50: 'DOWNLOAD-BSS-PFC', 0x51: 'CREATE-BSS-PFC',
             0x52: 'CREATE-BSS-PFC-ACK', 0x53: 'CREATE-BSS-PFC-NACK', 0x54: 'MODIFY-BSS-PFC',
             0x55: 'MODIFY-BSS-PFC-ACK', 0x56: 'DELETE-BSS-PFC', 0x57: 'DELETE-BSS-PFC-ACK',
             0x58: 'DELETE-BSS-PFC-REQ', 0x59: 'PS-HANDOVER-REQUIRED', 0x5a: 'PS-HANDOVER-REQUIRED-ACK',
             0x5b: 'PS-HANDOVER-REQUIRED-NACK', 0x5c: 'PS-HANDOVER-REQUEST', 0x5d: 'PS-HANDOVER-REQUEST-ACK',
             0x5e: 'PS-HANDOVER-REQUEST-NACK', 0x5f: 'Reserved', 0x60: 'PERFORM-LOCATION-REQUEST',
             0x61: 'PERFORM-LOCATION-RESPONSE', 0x62: 'PERFORM-LOCATION-ABORT', 0x63: 'POSITION-COMMAND',
             0x64: 'POSITION-RESPONSE', 0x65: 'Reserved', 0x66: 'Reserved', 0x67: 'Reserved', 0x68: 'Reserved',
             0x69: 'Reserved', 0x6a: 'Reserved', 0x6b: 'Reserved', 0x6c: 'Reserved', 0x6d: 'Reserved',
             0x6e: 'Reserved', 0x6f: 'Reserved', 0x70: 'RAN-INFORMATION', 0x71: 'RAN-INFORMATION-REQUEST',
             0x72: 'RAN-INFORMATION-ACK', 0x73: 'RAN-INFORMATION-ERROR', 0x74: 'RAN-INFORMATION-APPLICATION-ERROR',
             0x75: 'Reserved', 0x76: 'Reserved', 0x77: 'Reserved', 0x78: 'Reserved', 0x79: 'Reserved',
             0x7a: 'Reserved', 0x7b: 'Reserved', 0x7c: 'Reserved', 0x7d: 'Reserved', 0x7e: 'Reserved',
             0x7f: 'Reserved', 0x80: 'MBMS-SESSION-START-REQUEST', 0x81: 'MBMS-SESSION-START-RESPONSE',
             0x82: 'MBMS-SESSION-STOP-REQUEST', 0x83: 'MBMS-SESSION-STOP-RESPONSE',
             0x84: 'MBMS-SESSION-UPDATE-REQUEST', 0x85: 'MBMS-SESSION-UPDATE-RESPONSE', 0x86: 'Reserved',
             0x87: 'Reserved', 0x88: 'Reserved', 0x89: 'Reserved', 0x8a: 'Reserved', 0x8b: 'Reserved',
             0x8c: 'Reserved', 0x8d: 'Reserved', 0x8e: 'Reserved', 0x8f: 'Reserved', 0x90: 'Reserved',
             0x91: 'PS-HANDOVER-COMPLETE', 0x92: 'PS-HANDOVER-CANCEL', 0x93: 'PS-HANDOVER-COMPLETE-ACK'}
BSSGP_ЭЛЕМЕНТЫ = {0x0: 'Alignment Octets', 0x1: 'Bmax default MS', 0x2: 'BSS Area Indication',
                  0x3: 'Bucket Leak Rate (R)', 0x4: 'BVCI (BSSGP Virtual Connection Identifier)',
                  0x5: 'BVC Bucket size', 0x6: 'BVC Measurement', 0x7: 'Cause', 0x8: 'Cell Identifier',
                  0x9: 'Channel needed', 0xa: 'DRX Parameter', 0xb: 'eMLPP Priority', 0xc: 'Flush Action',
                  0xd: 'IMSI', 0xe: 'LLC-PDU', 0xf: 'LLC Frames Discarded',
                  0x10: 'Location Area Identification (LAI)', 0x11: 'Mobile Identity', 0x12: 'MS Bucket Size',
                  0x13: 'MS Radio Access Capability', 0x14: 'OMC Id', 0x15: 'PDU In Error', 0x16: 'PDU Lifetime',
                  0x17: 'Priority', 0x18: 'QoS Profile', 0x19: 'Radio Cause', 0x1a: 'RA-Cap-UPD-Cause',
                  0x1b: 'Routing Area Identification', 0x1c: 'R_default_MS', 0x1d: 'Suspend Reference Number',
                  0x1e: 'Tag', 0x1f: 'TLLI', 0x20: 'TMSI/P-TMSI', 0x21: 'Trace Reference', 0x22: 'Trace Type',
                  0x23: 'Transaction Id', 0x24: 'Trigger Id', 0x25: 'Number of octets affected',
                  0x26: 'LSA Identifier List', 0x27: 'LSA Information', 0x28: 'Packet Flow Identifier',
                  0x29: 'GPRS Timer', 0x3a: 'Quality Of Service', 0x3b: 'Feature Bitmap', 0x3c: 'Bucket Full Ratio',
                  0x3d: 'Service UTRAN CCO', 0x3e: 'NSEI (Network Service Entity Identifier)', 0x3f: 'RRLP APDU',
                  0x40: 'LCS QoS', 0x41: 'LCS Client Type', 0x42: 'Requested GPS Assistance Data',
                  0x43: 'Location Type', 0x44: 'Location Estimate', 0x45: 'Positioning Data',
                  0x46: 'Deciphering Keys', 0x47: 'LCS Priority', 0x48: 'LCS Cause', 0x49: 'PS LCS Capability',
                  0x4a: 'RRLP Flags', 0x4b: 'RIM Application Identity', 0x4c: 'RIM Sequence Number',
                  0x4d: 'RAN-INFORMATION-REQUEST Application Container',
                  0x4e: 'RAN-INFORMATION Application Container Unit', 0x4f: 'RIM PDU Indications',
                  0x52: 'PFC Flow Control parameters', 0x53: 'Global CN-Id', 0x54: 'RIM Routing Information',
                  0x55: 'RIM Protocol Version Number', 0x56: 'Application Error Container',
                  0x57: 'RAN-INFORMATION-REQUEST RIM Container', 0x58: 'RAN-INFORMATION RIM Container',
                  0x59: 'RAN-INFORMATION-APPLICATION-ERROR RIM Container',
                  0x5a: 'RAN-INFORMATION-ACK RIM Container', 0x5b: 'RAN-INFORMATION-ERROR RIM Container',
                  0x5c: 'Temporary Mobile Group Identity (TMGI)', 0x5d: 'MBMS Session Identity',
                  0x5e: 'MBMS Session Duration', 0x5f: 'MBMS Service Area Identity List', 0x60: 'MBMS Response',
                  0x61: 'MBMS Routing Area List', 0x62: 'MBMS Session Information', 0x63: 'MBMS Stop Cause',
                  0x64: 'Source BSS to Target BSS Transparent Container',
                  0x65: 'Target BSS to Source BSS Transparent Container', 0x66: 'NAS container for PS HO',
                  0x67: 'PFCs to be set-up list', 0x68: 'List of set-up PFCs', 0x69: 'Extended Feature Bitmap',
                  0x6a: 'Source to Target Transparent Container', 0x6b: 'Target to Source Transparent Container',
                  0x6c: 'RNC Identifier', 0x6d: 'Page Mode', 0x6e: 'Container ID', 0x6f: 'Global TFI',
                  0x70: 'Mobile Identity', 0x71: 'Time to MBMS Data Transfer',
                  0x72: 'MBMS Session Repetition Number', 0x73: 'Inter RAT Handover Info',
                  0x74: 'PS Handover Command', 0x75: 'PS Handover Indications', 0x76: 'SI/PSI Container',
                  0x77: 'Active PFCs List', 0x78: 'Velocity Data', 0x79: 'DTM Handover Command',
                  0x7a: 'CS Indication', 0x7b: 'GANSS Assistance Data', 0x7c: 'GANSS Location Type',
                  0x7d: 'GANSS Positioning Data', 0x7e: 'Flow Control Granularity', 0x7f: 'eNB Identifier',
                  0x80: 'E-UTRAN Inter RAT Handover Info', 0x81: 'Subscriber Profile ID for RAT/Frequency priority',
                  0x82: 'Request for Inter-RAT Handover Info', 0x83: 'Reliable Inter-RAT Handover Info',
                  0x84: 'Son transfer application identity', 0x85: 'CSG Identifier', 0x86: 'Tracking area identity',
                  0x87: 'Redirect Attempt Flag', 0x88: 'Redirection Indication', 0x89: 'Redirection Completed',
                  0x8a: 'Unconfirmed Send State Variable', 0x8c: 'SCI', 0x8d: 'GGSN / P - GW location',
                  0x8e: 'PLMN Identity', 0x8f: 'Priority Class Indicator', 0x92: 'eDRX Parameters',
                  0x93: 'Time Until Next Paging Occasion', 0x98: 'Coverage Class',
                  0x99: 'Paging Attempt Information', 0x9a: 'Exception Report Flag',
                  0x9b: 'Routing Area Identification', 0x9c: 'Attach Indicator', 0x9d: 'PLMN Identity'}
BSSGP_ПРИЧИНЫ = {0: 'Processor overload', 1: 'Equipment failure', 2: 'Transit network service failure',
                 3: 'Network service transmission capacity modified from zero kbps to greater than zero kbps',
                 4: 'Unknown MS', 5: 'BVCI unknown', 6: 'Cell traffic congestion', 7: 'SGSN congestion',
                 8: 'O&M intervention', 9: 'BVCI blocked', 10: 'PFC create failure', 11: 'PFC preempted',
                 12: 'ABQP no more supported', 13: 'Undefined - protocol error - unspecified',
                 14: 'Undefined - protocol error - unspecified', 15: 'Undefined - protocol error - unspecified',
                 16: 'Undefined - protocol error - unspecified', 17: 'Undefined - protocol error - unspecified',
                 18: 'Undefined - protocol error - unspecified', 19: 'Undefined - protocol error - unspecified',
                 20: 'Undefined - protocol error - unspecified', 21: 'Undefined - protocol error - unspecified',
                 22: 'Undefined - protocol error - unspecified', 23: 'Undefined - protocol error - unspecified',
                 24: 'Undefined - protocol error - unspecified', 25: 'Undefined - protocol error - unspecified',
                 26: 'Undefined - protocol error - unspecified', 27: 'Undefined - protocol error - unspecified',
                 28: 'Undefined - protocol error - unspecified', 29: 'Undefined - protocol error - unspecified',
                 30: 'Undefined - protocol error - unspecified', 31: 'Undefined - protocol error - unspecified',
                 32: 'Semantically incorrect PDU', 33: 'Invalid mandatory information', 34: 'Missing mandatory IE',
                 35: 'Missing conditional IE', 36: 'Unexpected conditional IE', 37: 'Conditional IE error',
                 38: 'PDU not compatible with the protocol state', 39: 'Protocol error - unspecified',
                 40: 'PDU not compatible with the feature set', 41: 'Requested information not available',
                 42: 'Unknown destination address', 43: 'Unknown RIM application identity',
                 44: 'Invalid container unit information', 45: 'PFC queuing', 46: 'PFC created successfully',
                 47: 'T12 expiry', 48: 'MS under PS Handover treatment', 49: 'Uplink quality',
                 50: 'Uplink strength', 51: 'Downlink quality', 52: 'Downlink strength', 53: 'Distance',
                 54: 'Better cell', 55: 'Traffic', 56: 'Radio contact lost with MS', 57: 'MS back on old channel',
                 58: 'T13 expiry', 59: 'T14 expiry', 60: 'Not all requested PFCs created', 61: 'CS cause',
                 62: 'Requested ciphering and/or integrity protection algorithms not supported',
                 63: 'Relocation failure in target system', 64: 'Directed Retry', 65: 'Time critical relocation',
                 66: 'PS Handover Target not allowed',
                 67: 'PS Handover not Supported in Target BSS or Target System',
                 68: 'Incoming relocation not supported due to PUESBINE feature',
                 69: 'DTM Handover - No CS resource', 70: 'DTM Handover - PS Allocation failure',
                 71: 'DTM Handover - T24 expiry', 72: 'DTM Handover - Invalid CS Indication IE',
                 73: 'DTM Handover - T23 expiry', 74: 'DTM Handover - MSC Error', 75: 'Invalid CSG cell'}

#: Элементы NS (48.016 10.3, табл. 10.3.1) и те, что в SNS-SIZE идут как TV (полная длина вместе с IEI).
NS_ЭЛЕМЕНТЫ = {0x00: "Cause", 0x01: "NS-VCI", 0x02: "NS PDU", 0x03: "BVCI", 0x04: "NSEI",
               0x05: "List of IP4 Elements", 0x06: "List of IP6 Elements", 0x07: "Maximum Number of NS-VCs",
               0x08: "Number of IP4 Endpoints", 0x09: "Number of IP6 Endpoints", 0x0A: "Reset Flag",
               0x0B: "IP Address"}
NS_TV = {0x07: 3, 0x08: 3, 0x09: 3, 0x0A: 2, 0x0B: None}
#: IP Address (10.3.2b) — TV, длина по виду адреса: 1 — IPv4, 2 — IPv6.
NS_АДРЕС = {1: 6, 2: 18}
#: После какого числа элементов идёт поле V в один октет: SNS-ACK, ADD, CHANGEWEIGHT, DELETE — номер
#: транзакции после NSEI; SNS-CONFIG — End Flag первым.
NS_V = {0x0C: (1, "Номер транзакции", "nsip.transaction_id"), 0x0D: (1, "Номер транзакции", "nsip.transaction_id"),
        0x0E: (1, "Номер транзакции", "nsip.transaction_id"), 0x11: (1, "Номер транзакции", "nsip.transaction_id"),
        0x0F: (0, "End Flag", "nsip.end_flag")}
#: Вид TLLI по старшим битам (23.003, 2.6).
TLLI_ВИДЫ = ((0xC0000000, 0xC0000000, "местный"), (0xC0000000, 0x80000000, "чужой"),
             (0xF8000000, 0x78000000, "случайный"), (0xF8000000, 0x70000000, "вспомогательный"))
#: Шаг пиковой скорости QoS Profile (11.3.28, биты 8–7 октета 5), бит/с.
ШАГ_СКОРОСТИ = (100, 1000, 10000, 100000)
#: LLC (Wireshark packet-gprs-llc.c: sapi_abrv, sapi_t, cr_formats_unnumb, cr_formats_ipluss, xid_param_type_str).
SAPI = {0: "Reserved 0", 1: "LLGMM", 2: "TOM2", 3: "LL3", 4: "Reserved 4", 5: "LL5", 6: "Reserved 6", 7: "LLSMS",
        8: "TOM8", 9: "LL9", 10: "Reserved 10", 11: "LL11", 12: "Reserved 12", 13: "Reserved 13",
        14: "Reserved 14", 15: "Reserved 15"}
SAPI_НАЗНАЧЕНИЕ = {1: "GPRS Mobility Management", 2: "Tunneling of messages 2", 3: "User data 3", 5: "User data 5",
                   7: "SMS", 8: "Tunneling of messages 8", 9: "User data 9", 11: "User data 11"}
U_КОМАНДЫ = {0x1: "DM-response", 0x4: "DISC-command", 0x6: "UA-response", 0x7: "SABM", 0x8: "FRMR", 0xB: "XID"}
S_КОМАНДЫ = {0x0: "RR", 0x1: "ACK", 0x2: "RNR", 0x3: "SACK"}
XID = {0x0: "Version (LLC version number)", 0x1: "IOV-UI (ciphering Input offset value for UI frames)",
       0x2: "IOV-I (ciphering Input offset value for I frames)", 0x3: "T200 (retransmission timeout)",
       0x4: "N200 (max number of retransmissions)", 0x5: "N201-U (max info field length for U and UI frames)",
       0x6: "N201-I (max info field length for I frames)", 0x7: "mD (I frame buffer size in the DL direction)",
       0x8: "mU (I frame buffer size in the UL direction)", 0x9: "kD (window size in the DL direction)",
       0xA: "kU (window size in the UL direction)", 0xB: "Layer-3 Parameters", 0xC: "Reset"}
PME = {0: "без защиты, без шифра", 1: "защищено, без шифра", 2: "без защиты, зашифровано", 3: "защищено, зашифровано"}
N202 = 4
SNDCP_SAPI = frozenset({3, 5, 9, 11})


def _crc24_таблица() -> tuple:
    таблица = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xAD85DD if c & 1 else c >> 1           # 0xAD85DD — отражённый многочлен 44.064 6.1.2
        таблица.append(c)
    return tuple(таблица)


CRC24 = _crc24_таблица()


def crc24(данные: bytes) -> int:
    """FCS LLC: CRC-24 с начальным 0xFFFFFF и дополнением (как crc_calc в Wireshark packet-gprs-llc.c)."""
    c = 0xFFFFFF
    for б in данные:
        c = (c >> 8) ^ CRC24[(c ^ б) & 0xFF]
    return ~c & 0xFFFFFF


# -- общие для NS и BSSGP -------------------------------------------------------------------------

def _элемент(д: bytes, м: int, конец: int, tv: dict | None = None):
    """Один элемент: (IEI, начало значения, длина значения, начало элемента) или None, если не укладывается.
    Длина (48.016 10.3.1, 48.018 11.1): бит 8 = 1 — 7 бит в одном октете, 0 — 15 бит в двух."""
    iei = д[м]
    if tv and iei in tv:
        полная = tv[iei] if tv[iei] is not None else NS_АДРЕС.get(д[м + 1] if м + 1 < конец else -1)
        if полная is None:
            return None
        начало, n = м + 1, полная - 1
    else:
        if м + 2 > конец:
            return None
        if д[м + 1] & 0x80:
            начало, n = м + 2, д[м + 1] & 0x7F
        else:
            if м + 3 > конец:
                return None
            начало, n = м + 3, (д[м + 1] << 8) | д[м + 2]
    if начало + n > конец:
        return None
    return iei, начало, n, м


def _tlv(д: bytes, м: int, конец: int, tv: dict | None = None):
    """Все элементы до конца или None, если не укладываются точно."""
    итог = []
    while м < конец:
        э = _элемент(д, м, конец, tv)
        if э is None:
            return None
        итог.append(э)
        м = э[1] + э[2]
    return итог


def _rai(б: bytes) -> str:
    """Routeing Area Identification (24.008 10.5.5.15): PLMN, LAC, RAC."""
    return f"RAI {_plmn(б[0:3])}, LAC {u16(б, 3)}, RAC {б[5]}"


def _tlli(значение: int) -> str:
    вид = next((имя for маска, образец, имя in TLLI_ВИДЫ if значение & маска == образец), "")
    return f"0x{значение:08x}" + (f" ({вид})" if вид else "")


# -- NS -------------------------------------------------------------------------------------------

def _ip_элементы(р: Разбор, у, родитель, м: int, конец: int, v6: bool) -> str:
    размер = 20 if v6 else 8
    адреса = []
    for x in range(м, конец - размер + 1, размер):
        адрес = str(ipaddress.IPv6Address(р.д[x:x + 16]) if v6 else ipaddress.IPv4Address(р.д[x:x + 4]))
        порт = u16(р.д, x + размер - 4)
        э = у.поле(f"IP Element: {адрес}, порт {порт}", "nsip.ip_element", f"{адрес}:{порт}", x, размер,
                   родитель=родитель)
        у.поле("Вес сигнализации", "nsip.ip_element.signalling_weight", р.д[x + размер - 2], x + размер - 2, 1,
               родитель=э)
        у.поле("Вес данных", "nsip.ip_element.data_weight", р.д[x + размер - 1], x + размер - 1, 1, родитель=э)
        адреса.append(f"{адрес}:{порт}")
    return ", ".join(адреса)


def ns(р: Разбор, м: int, конец: int) -> bool:
    """PDU NS: тип и элементы должны уложиться точно; NS-UNITDATA несёт BSSGP."""
    д = р.д
    if м >= конец or д[м] not in NS_PDU:
        return False
    тип = д[м]
    if тип == 0x00:
        if конец - м < 5 or д[м + 1] & 0xFC or not bssgp_проба(р, м + 4, конец):
            return False
        у = р.уровень("NS", "GPRS Network Service", м)
        у.поле("Тип PDU", "nsip.pdu_type", f"0x00 ({NS_PDU[0]})", м, 1, 0)
        биты = у.поле("Биты управления", "nsip.control_bits", f"0x{д[м + 1]:02x}", м + 1, 1, д[м + 1])
        у.поле("Request change flow (R)", "nsip.control_bits.r", д[м + 1] & 1, м + 1, 1, родитель=биты)
        у.поле("Confirm change flow (C)", "nsip.control_bits.c", (д[м + 1] >> 1) & 1, м + 1, 1, родитель=биты)
        у.поле("BVCI", "nsip.bvci", u16(д, м + 2), м + 2, 2)
        у.длина = 4
        у.итог = f"{NS_PDU[0]}, BVCI {u16(д, м + 2)}"
        р.п.инфо = "NS " + у.итог
        bssgp(р, м + 4, конец)
        return True
    место, v = м + 1, NS_V.get(тип)
    if v is not None and v[0] == 0:
        место += 1
    if v is not None and v[0] == 1:
        # Номер транзакции (V) — после первого элемента, NSEI.
        первый = _элемент(д, место, конец, NS_TV) if место < конец else None
        if первый is None or первый[0] != 0x04 or первый[1] + первый[2] >= конец:
            return False
        хвост = _tlv(д, первый[1] + первый[2] + 1, конец, NS_TV)
        элементы = None if хвост is None else [первый] + хвост
    else:
        элементы = _tlv(д, место, конец, NS_TV)
    if элементы is None:
        return False
    у = р.уровень("NS", "GPRS Network Service", м)
    у.поле("Тип PDU", "nsip.pdu_type", f"0x{тип:02x} ({NS_PDU[тип]})", м, 1, тип)
    if v is not None:
        где = м + 1 if v[0] == 0 else элементы[0][1] + элементы[0][2]
        у.поле(v[1], v[2], д[где], где, 1)
    сводка = []
    for iei, начало, n, где in элементы:
        имя = NS_ЭЛЕМЕНТЫ.get(iei, f"IEI 0x{iei:02x}")
        значение = д[начало:начало + n]
        э = у.поле(имя, f"nsip.ie.{iei:02x}", значение.hex(), где, начало + n - где, iei)
        if iei == 0x00 and n == 1:
            э.имя = f"Причина: {NS_ПРИЧИНЫ.get(значение[0], f'0x{значение[0]:02x}')}"
            сводка.append(э.имя.lower())
        elif iei in (0x01, 0x03, 0x04) and n == 2:
            э.имя = f"{имя}: {u16(значение, 0)}"
            сводка.append(f"{имя} {u16(значение, 0)}")
        elif iei in (0x07, 0x08, 0x09) and n == 2:
            э.имя = f"{имя}: {u16(значение, 0)}"
        elif iei == 0x0A and n == 1:
            э.имя = f"Reset Flag: {значение[0] & 1}"
        elif iei == 0x0B:
            адрес = ipaddress.IPv4Address(значение[1:]) if значение[0] == 1 else ipaddress.IPv6Address(значение[1:])
            э.имя = f"IP Address: {адрес}"
            сводка.append(f"адрес {адрес}")
        elif iei in (0x05, 0x06):
            адреса = _ip_элементы(р, у, э, начало, начало + n, iei == 0x06)
            э.имя = f"{имя}: {адреса or 'пусто'}"
        elif iei == 0x02:
            э.имя = f"NS PDU ({n} байт)"
    у.длина = конец - м
    у.итог = NS_PDU[тип] + (f", {', '.join(сводка)}" if сводка else "")
    р.п.инфо = "NS " + у.итог
    return True


# -- BSSGP ----------------------------------------------------------------------------------------

def _qos(б: bytes) -> str:
    скорость, шаг = u16(б, 0), ШАГ_СКОРОСТИ[б[2] >> 6]
    пик = "наилучшая попытка" if скорость == 0 else f"{скорость * шаг} бит/с"
    return f"пиковая скорость {пик}, приоритет {б[2] & 7}"


def _значение(iei: int, б: bytes) -> str | None:
    """Показ значения элемента BSSGP; None — показать октетами."""
    n = len(б)
    if iei in (0x04, 0x3E) and n == 2 or iei == 0x1E and n == 1:
        return str(int.from_bytes(б, "big"))
    if iei == 0x07 and n == 1:
        return f"{BSSGP_ПРИЧИНЫ.get(б[0], f'0x{б[0]:02x}')}"
    if iei == 0x08 and n == 8:
        return f"{_rai(б)}, CI {u16(б, 6)}"
    if iei in (0x1B, 0x9B) and n == 6:
        return _rai(б)
    if iei == 0x10 and n == 5:
        return f"LAI {_plmn(б[0:3])}, LAC {u16(б, 3)}"
    if iei in (0x0D, 0x11, 0x70):
        и = gsm.идентификатор(б)
        return f"{и[0]} {и[1]}" if и else None
    if iei == 0x1F and n == 4:
        return _tlli(u32(б, 0))
    if iei == 0x20 and n == 4:
        return б.hex().upper()
    if iei == 0x16 and n == 2:
        return "бесконечно" if u16(б, 0) == 0xFFFF else f"{u16(б, 0) / 100:g} с"
    if iei in (0x01, 0x05, 0x12) and n == 2:
        return f"{u16(б, 0) * 100} октетов"
    if iei in (0x03, 0x1C) and n == 2:
        return f"{u16(б, 0) * 100} бит/с"
    return None


def _элементы_bssgp(д: bytes, м: int, конец: int):
    тип = д[м]
    if тип in (0x00, 0x01):
        if конец - м < 8:
            return None
        return _tlv(д, м + 8, конец)
    return _tlv(д, м + 1, конец)


def bssgp_проба(р: Разбор, м: int, конец: int) -> bool:
    """Похоже ли на PDU BSSGP: тип назначен и элементы укладываются точно."""
    д = р.д
    if м >= конец or BSSGP_PDU.get(д[м], "Reserved") == "Reserved":
        return False
    return _элементы_bssgp(д, м, конец) is not None


def bssgp(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if not bssgp_проба(р, м, конец):
        данные(р, м, "NS SDU (не BSSGP)", конец)
        return False
    тип = д[м]
    у = р.уровень("BSSGP", "Base Station Subsystem GPRS Protocol", м)
    у.поле("Тип PDU", "bssgp.pdu_type", f"0x{тип:02x} ({BSSGP_PDU[тип]})", м, 1, тип)
    сводка = []
    if тип in (0x00, 0x01):
        tlli = u32(д, м + 1)
        у.поле("TLLI", "bssgp.tlli", _tlli(tlli), м + 1, 4, tlli)
        у.поле("QoS Profile", "bssgp.qos", _qos(д[м + 5:м + 8]), м + 5, 3, д[м + 5:м + 8].hex())
        сводка.append(f"TLLI 0x{tlli:08x}")
    llc_pdu = None
    for iei, начало, n, где in _элементы_bssgp(д, м, конец):
        имя = BSSGP_ЭЛЕМЕНТЫ.get(iei, f"IEI 0x{iei:02x}")
        значение = д[начало:начало + n]
        показ = _значение(iei, значение)
        if iei == 0x0E:
            у.поле(f"{имя} ({n} байт)", "bssgp.llc_pdu", f"{n} байт", где, начало + n - где, iei)
            llc_pdu = (начало, начало + n)
            continue
        текст = показ if показ is not None else (значение.hex() if n <= 32 else f"{n} байт")
        у.поле(f"{имя}: {текст}", f"bssgp.ie.{iei:02x}", текст, где, начало + n - где, iei)
        if iei in (0x0D, 0x11, 0x1F, 0x08, 0x1B, 0x07, 0x04, 0x3E) and показ is not None:
            сводка.append(f"{имя.split(' (')[0]} {показ}" if iei in (0x04, 0x3E, 0x07) else показ)
    у.длина = конец - м if llc_pdu is None else llc_pdu[0] - м
    у.итог = BSSGP_PDU[тип] + (f", {', '.join(сводка)}" if сводка else "")
    р.п.инфо = "BSSGP " + у.итог
    if llc_pdu is not None:
        итог_llc = llc(р, *llc_pdu)
        if итог_llc:
            р.п.инфо = f"BSSGP {у.итог}; {итог_llc}"
        if llc_pdu[1] < конец:
            данные(р, llc_pdu[1], "элементы BSSGP после LLC-PDU", конец)
    return True


# -- LLC ------------------------------------------------------------------------------------------

def _xid(р: Разбор, у, м: int, конец: int) -> str:
    """Параметры XID (44.064 8.5.2): XL, тип (5 бит), длина (2 бита или 6 при XL), значение."""
    д, имена = р.д, []
    while м < конец:
        б = д[м]
        тип = (б >> 2) & 0x1F
        if б & 0x80:
            if м + 1 >= конец:
                break
            дл, заголовок = ((б & 3) << 6) | (д[м + 1] >> 2), 2
        else:
            дл, заголовок = б & 3, 1
        if м + заголовок + дл > конец:
            р.ошибка("LLC: параметр XID длиннее кадра")
            break
        значение = int.from_bytes(д[м + заголовок:м + заголовок + дл], "big") if дл <= 4 else None
        имя = XID.get(тип, f"тип {тип}")
        текст = str(значение) if значение is not None else f"{дл} байт"
        у.поле(f"XID {имя}: {текст}", "llcgprs.xid.type", текст, м, заголовок + дл, тип)
        имена.append(имя.split(" (")[0] + (f"={значение}" if значение is not None else ""))
        м += заголовок + дл
    return ", ".join(имена)


def llc(р: Разбор, м: int, конец: int) -> str:
    """Кадр LLC: адрес, управление, информация, FCS. Итог для строки пакета или пусто."""
    д = р.д
    if конец - м < 5 or д[м] & 0x80:
        данные(р, м, "LLC-PDU (не LLC)", конец)
        return ""
    fcs_место = конец - 3
    sapi = д[м] & 0x0F
    у = р.уровень("LLC", "GPRS Logical Link Control", м)
    а = у.поле(f"Адрес: SAPI {sapi} ({SAPI[sapi]})", "llcgprs.sapi", SAPI[sapi], м, 1, sapi)
    у.поле("C/R", "llcgprs.cr", (д[м] >> 6) & 1, м, 1, родитель=а)
    б = д[м + 1]
    x = м + 1
    покрытие = fcs_место
    шифр = False
    if б < 0x80:                                          # I: 3 октета (+ SACK)
        ns_, nr = (u16(д, x) >> 4) & 0x1FF, (u16(д, x + 1) >> 2) & 0x1FF
        s = д[x + 2] & 3
        вид = f"I, N(S) {ns_}, N(R) {nr}, {S_КОМАНДЫ[s]}"
        у.поле("Управление", "llcgprs.control", вид, x, 3, б)
        x += 3
        if s == 3:
            k = (д[x] & 0x1F) + 1
            у.поле("Битовая карта SACK", "llcgprs.sack", f"{k} байт", x, 1 + k)
            x += 1 + k
        формат = "I"
    elif б < 0xC0:                                        # S: 2 октета
        nr, s = (u16(д, x) >> 2) & 0x1FF, д[x + 1] & 3
        вид = f"S, {S_КОМАНДЫ[s]}, N(R) {nr}"
        у.поле("Управление", "llcgprs.control", вид, x, 2, б)
        x += 2
        формат = "S"
    elif б < 0xE0:                                        # UI: 2 октета
        слово = u16(д, x)
        nu, e, pm = (слово >> 2) & 0x1FF, (слово >> 1) & 1, слово & 1
        вид = f"UI, N(U) {nu}"
        у.поле("Управление", "llcgprs.control", f"{вид}, {PME[e << 1 | pm]}", x, 2, слово)
        у.поле("N(U)", "llcgprs.nu", nu, x, 2)
        у.поле("Шифрование (E)", "llcgprs.e", e, x + 1, 1)
        у.поле("Защита всего поля (PM)", "llcgprs.pm", pm, x + 1, 1)
        x += 2
        шифр = bool(e)
        if not pm:
            покрытие = min(x + N202, fcs_место)
        формат = "UI"
    else:                                                 # U: 1 октет
        команда = б & 0x0F
        вид = f"U, {U_КОМАНДЫ.get(команда, f'команда 0x{команда:x}')}"
        у.поле("Управление", "llcgprs.control", f"{вид}, P/F {(б >> 4) & 1}", x, 1, б)
        x += 1
        формат = "U"
    if x > fcs_место:
        р.ошибка("LLC: кадр короче заголовка и FCS")
        у.длина = конец - м
        у.итог = вид
        return f"LLC {SAPI[sapi]} {вид}"
    fcs, расчёт = int.from_bytes(д[fcs_место:конец], "little"), crc24(д[м:покрытие])
    верна = fcs == расчёт
    у.поле("FCS", "llcgprs.fcs", f"0x{fcs:06x} " + ("(верна)" if верна else
                                                     f"(не сходится, должна быть 0x{расчёт:06x}"
                                                     + (" — возможно, из-за шифрования)" if шифр else ")")),
           fcs_место, 3, fcs, плохо=not верна and not шифр)
    if not верна and not шифр:
        р.ошибка(f"LLC: FCS не сходится (0x{fcs:06x}, должна быть 0x{расчёт:06x})")
    у.длина = x - м
    у.итог = f"SAPI {sapi} ({SAPI[sapi]}), {вид}"
    итог = f"LLC {SAPI[sapi]} {вид}"
    if формат == "U" and б & 0x0F == 0xB:
        параметры = _xid(р, у, x, fcs_место)
        у.длина = fcs_место - м
        return итог + (f": {параметры}" if параметры else "")
    if формат not in ("I", "UI") or x >= fcs_место:
        return итог
    if шифр:
        данные(р, x, "LLC: зашифровано (GEA)", fcs_место)
        return итог + ", зашифровано"
    if sapi in (1, 7) and gsm.dtap(р, x, fcs_место):
        return f"{итог}; {р.п.инфо}"
    if sapi in SNDCP_SAPI and sndcp(р, x, fcs_место):
        return f"{итог}; {р.п.инфо}"
    данные(р, x, f"LLC SAPI {sapi}", fcs_место)
    return итог


# -- SNDCP ----------------------------------------------------------------------------------------

def sndcp(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    б = д[м]
    первый, неподтв, ещё, nsapi = б & 0x40, б & 0x20, б & 0x10, б & 0x0F
    заголовок = 1 + (1 if первый else 0) + (1 if первый and not неподтв else 0) + (2 if неподтв else 0)
    if м + заголовок > конец:
        return False
    у = р.уровень("SNDCP", "Subnetwork Dependent Convergence Protocol", м)
    а = у.поле(f"Адрес: NSAPI {nsapi}", "sndcp.nsapi", nsapi, м, 1)
    у.поле("Первый сегмент (F)", "sndcp.f", первый >> 6, м, 1, родитель=а)
    у.поле("Неподтверждаемый режим (T)", "sndcp.t", неподтв >> 5, м, 1, родитель=а)
    у.поле("Ещё сегменты (M)", "sndcp.m", ещё >> 4, м, 1, родитель=а)
    x, сжатие = м + 1, (0, 0)
    if первый:
        сжатие = (д[x] >> 4, д[x] & 0x0F)
        у.поле(f"Сжатие: DCOMP {сжатие[0]}, PCOMP {сжатие[1]}", "sndcp.dcomp", f"{сжатие[0]}/{сжатие[1]}", x, 1)
        x += 1
    if первый and not неподтв:
        у.поле("Номер N-PDU", "sndcp.npduField1", д[x], x, 1)
        номер = f"N-PDU {д[x]}"
        x += 1
    if неподтв:
        сегмент, npdu = д[x] >> 4, u16(д, x) & 0x0FFF
        у.поле("Номер сегмента", "sndcp.segment", сегмент, x, 1)
        у.поле("Номер N-PDU", "sndcp.npduField2", npdu, x, 2)
        номер = f"N-PDU {npdu}, сегмент {сегмент}"
        x += 2
    у.длина = x - м
    у.итог = f"NSAPI {nsapi}, {'SN-UNITDATA' if неподтв else 'SN-DATA'}, {номер}"
    р.п.инфо = "SNDCP " + у.итог
    if x >= конец:
        return True
    if первый and not ещё and сжатие == (0, 0) and д[x] >> 4 in (4, 6):
        голый_ip(р, x)
    else:
        данные(р, x, "SNDCP: " + ("сжато" if сжатие != (0, 0) else "сегмент N-PDU"), конец)
    return True


for _порт in (2157, 19999, 23000):
    prilozh.ПОРТЫ_UDP.setdefault(_порт, ns)
prilozh.КАК["udp"].setdefault("NS (Gb)", ns)
kanalnye.FR_БЕЗ_NLPID.append(ns)
ДОП_УРОВНИ.update({"NS": "канальный", "BSSGP": "сетевой", "LLC": "канальный", "SNDCP": "сетевой"})
