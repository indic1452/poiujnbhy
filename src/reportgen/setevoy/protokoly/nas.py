"""NAS LTE и 5G внутри S1AP и NGAP: EPS NAS (TS 24.301) и 5GS NAS (TS 24.501).

Документы (структуры — строго по ним):

* S1AP (TS 36.413) — IE id-NAS-PDU = 26, NGAP (TS 38.413) — IE id-NAS-PDU = 38: значение — OCTET STRING
  в APER (X.691, 10.9: определитель длины и байты).
* EPS NAS — TS 24.301: 9.1–9.3 (заголовок: тип защиты и дискриминатор; у ESM — идентификатор
  носителя и PTI), 9.2 (защищённое сообщение: MAC 4 байта, номер 1 байт, затем простое сообщение;
  тип 12 — укороченный SERVICE REQUEST), 9.8 (типы сообщений), 8.2 (EMM: Attach request/accept/reject,
  Detach, TAU, GUTI reallocation, Authentication, Identity, Security mode, NAS transport),
  8.3 (ESM: PDN connectivity, Activate default EPS bearer context …), 9.9.3.12 (EPS mobile identity:
  IMSI, IMEI, GUTI = PLMN, MMEGI, MMEC, M-TMSI), 9.9.3.9 и 9.9.4.4 (причины EMM и ESM),
  9.9.3.23 (выбранные алгоритмы: EEA — биты 7–5, EIA — биты 3–1), 9.9.4.9 (PDN address),
  24.008 10.5.6.1 (APN — метки с длиной); сообщения SMS в NAS transport — TS 24.011 (разбор GSM).
* 5GS NAS — TS 24.501: 9.1–9.3 (расширенный дискриминатор 0x7E — 5GMM, 0x2E — 5GSM; тип защиты;
  защищённое: MAC, номер, простое сообщение), 9.7 (типы), 8.2 (5GMM: Registration, Deregistration,
  Service, Authentication, Identity, Security mode, UL/DL NAS transport), 8.3 (5GSM: PDU session
  establishment …), 9.11.3.4 (5GS mobile identity: SUCI — формат SUPI, PLMN, индикатор
  маршрутизации, схема защиты, ключ сети, выход схемы; при нулевой схеме выход — MSIN, так что IMSI
  виден целиком; 5G-GUTI; 5G-S-TMSI; IMEI/IMEISV), 9.11.3.34 (алгоритмы 5G-EA/5G-IA),
  9.11.3.2 и 9.11.4.2 (причины), 9.11.4.10 (PDU address), 9.11.3.40 (тип контейнера нагрузки).
* Таблицы типов сообщений, причин и видов регистрации/присоединения — из Wireshark
  (packet-nas_eps.c, packet-nas_5gs.c) программно, без переписывания руками.

Зашифрованное сообщение (тип защиты 2 или 4) показывается как зашифрованное; если под заголовком —
правильное простое сообщение (нулевой шифр EEA0/NEA0, частый в испытательных сетях), оно
разбирается с пометкой.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable

from ..pole import u16, u32
from ..razbor import ДОП_УРОВНИ, Разбор
from . import gsm, mobilnye

EMM = {0x41: "Attach request", 0x42: "Attach accept", 0x43: "Attach complete", 0x44: "Attach reject",
       0x45: "Detach request", 0x46: "Detach accept", 0x48: "Tracking area update request",
       0x49: "Tracking area update accept", 0x4A: "Tracking area update complete",
       0x4B: "Tracking area update reject", 0x4C: "Extended service request",
       0x4D: "Control plane service request", 0x4E: "Service reject", 0x4F: "Service accept",
       0x50: "GUTI reallocation command", 0x51: "GUTI reallocation complete", 0x52: "Authentication request",
       0x53: "Authentication response", 0x54: "Authentication reject", 0x55: "Identity request",
       0x56: "Identity response", 0x5C: "Authentication failure", 0x5D: "Security mode command",
       0x5E: "Security mode complete", 0x5F: "Security mode reject", 0x60: "EMM status",
       0x61: "EMM information", 0x62: "Downlink NAS transport", 0x63: "Uplink NAS transport",
       0x64: "CS service notification", 0x68: "Downlink generic NAS transport",
       0x69: "Uplink generic NAS transport"}
ESM = {0xC1: "Activate default EPS bearer context request", 0xC2: "Activate default EPS bearer context accept",
       0xC3: "Activate default EPS bearer context reject",
       0xC5: "Activate dedicated EPS bearer context request",
       0xC6: "Activate dedicated EPS bearer context accept",
       0xC7: "Activate dedicated EPS bearer context reject", 0xC9: "Modify EPS bearer context request",
       0xCA: "Modify EPS bearer context accept", 0xCB: "Modify EPS bearer context reject",
       0xCD: "Deactivate EPS bearer context request", 0xCE: "Deactivate EPS bearer context accept",
       0xD0: "PDN connectivity request", 0xD1: "PDN connectivity reject", 0xD2: "PDN disconnect request",
       0xD3: "PDN disconnect reject", 0xD4: "Bearer resource allocation request",
       0xD5: "Bearer resource allocation reject", 0xD6: "Bearer resource modification request",
       0xD7: "Bearer resource modification reject", 0xD9: "ESM information request",
       0xDA: "ESM information response", 0xDB: "Notification", 0xDC: "ESM dummy message", 0xE8: "ESM status",
       0xE9: "Remote UE report", 0xEA: "Remote UE report response", 0xEB: "ESM data transport"}
ПРИЧИНЫ_EMM = {0x02: "IMSI unknown in HSS", 0x03: "Illegal UE", 0x05: "IMEI not accepted", 0x06: "Illegal ME",
               0x07: "EPS services not allowed", 0x08: "EPS services and non-EPS services not allowed",
               0x09: "UE identity cannot be derived by the network", 0x0A: "Implicitly detached",
               0x0B: "PLMN not allowed", 0x0C: "Tracking Area not allowed",
               0x0D: "Roaming not allowed in this tracking area",
               0x0E: "EPS services not allowed in this PLMN", 0x0F: "No Suitable Cells In tracking area",
               0x10: "MSC temporarily not reachable", 0x11: "Network failure", 0x12: "CS domain not available",
               0x13: "ESM failure", 0x14: "MAC failure", 0x15: "Synch failure", 0x16: "Congestion",
               0x17: "UE security capabilities mismatch", 0x18: "Security mode rejected, unspecified",
               0x19: "Not authorized for this CSG", 0x1A: "Non-EPS authentication unacceptable",
               0x1F: "Redirection to 5GCN required",
               0x23: "Requested service option not authorized in this PLMN",
               0x24: "IAB-node operation not authorized", 0x27: "CS service temporarily not available",
               0x28: "No EPS bearer context activated", 0x2A: "Severe network failure",
               0x4E: "PLMN not allowed to operate at the present UE location",
               0x50: "Disaster roaming for the determined PLMN with disaster condition not allowed",
               0x53: "Procedure cannot be completed due to unavailable feeder link while MME is operating in S&F mode",
               0x5F: "Semantically incorrect message", 0x60: "Invalid mandatory information",
               0x61: "Message type non-existent or not implemented",
               0x62: "Message type not compatible with the protocol state",
               0x63: "Information element non-existent or not implemented", 0x64: "Conditional IE error",
               0x65: "Message not compatible with the protocol state", 0x6F: "Protocol error, unspecified"}
ПРИЧИНЫ_ESM = {0x08: "Operator Determined Barring", 0x1A: "Insufficient resources",
               0x1B: "Missing or unknown APN", 0x1C: "Unknown PDN type",
               0x1D: "User authentication or authorization failed",
               0x1E: "Request rejected by Serving GW or PDN GW", 0x1F: "Request rejected, unspecified",
               0x20: "Service option not supported", 0x21: "Requested service option not subscribed",
               0x22: "Service option temporarily out of order", 0x23: "PTI already in use",
               0x24: "Regular deactivation", 0x25: "EPS QoS not accepted", 0x26: "Network failure",
               0x27: "Reactivation requested", 0x29: "Semantic error in the TFT operation",
               0x2A: "Syntactical error in the TFT operation", 0x2B: "Invalid EPS bearer identity",
               0x2C: "Semantic errors in packet filter(s)", 0x2D: "Syntactical errors in packet filter(s)",
               0x2E: "Unused", 0x2F: "PTI mismatch", 0x31: "Last PDN disconnection not allowed",
               0x32: "PDN type IPv4 only allowed", 0x33: "PDN type IPv6 only allowed",
               0x34: "Single address bearers only allowed", 0x35: "ESM information not received",
               0x36: "PDN connection does not exist",
               0x37: "Multiple PDN connections for a given APN not allowed",
               0x38: "Collision with network initiated request", 0x39: "PDN type IPv4v6 only allowed",
               0x3A: "PDN type non IP only allowed", 0x3B: "Unsupported QCI value",
               0x3C: "Bearer handling not supported", 0x3D: "PDN type Ethernet only allowed",
               0x41: "Maximum number of EPS bearers reached",
               0x42: "Requested APN not supported in current RAT and PLMN combination",
               0x51: "Invalid PTI value", 0x5F: "Semantically incorrect message",
               0x60: "Invalid mandatory information", 0x61: "Message type non-existent or not implemented",
               0x62: "Message type not compatible with the protocol state",
               0x63: "Information element non-existent or not implemented", 0x64: "Conditional IE error",
               0x65: "Message not compatible with the protocol state", 0x6F: "Protocol error, unspecified",
               0x70: "APN restriction value incompatible with active EPS bearer context",
               0x71: "Multiple accesses to a PDN connection not allowed"}
MM5G = {0x41: "Registration request", 0x42: "Registration accept", 0x43: "Registration complete",
        0x44: "Registration reject", 0x45: "Deregistration request (UE originating)",
        0x46: "Deregistration accept (UE originating)", 0x47: "Deregistration request (UE terminated)",
        0x48: "Deregistration accept (UE terminated)", 0x49: "Not used in current version",
        0x4A: "Not used in current version", 0x4B: "Not used in current version", 0x4C: "Service request",
        0x4D: "Service reject", 0x4E: "Service accept", 0x4F: "Control plane service request",
        0x50: "Network slice-specific authentication command",
        0x51: "Network slice-specific authentication complete",
        0x52: "Network slice-specific authentication result", 0x53: "Not used in current version",
        0x54: "Configuration update command", 0x55: "Configuration update complete",
        0x56: "Authentication request", 0x57: "Authentication response", 0x58: "Authentication reject",
        0x59: "Authentication failure", 0x5A: "Authentication result", 0x5B: "Identity request",
        0x5C: "Identity response", 0x5D: "Security mode command", 0x5E: "Security mode complete",
        0x5F: "Security mode reject", 0x60: "Not used in current version", 0x61: "Not used in current version",
        0x62: "Not used in current version", 0x63: "Not used in current version", 0x64: "5GMM status",
        0x65: "Notification", 0x66: "Notification response", 0x67: "UL NAS transport",
        0x68: "DL NAS transport", 0x69: "Relay key request", 0x6A: "Relay key accept",
        0x6B: "Relay key reject", 0x6C: "Relay authentication request", 0x6D: "Relay authentication response"}
SM5G = {0xC1: "PDU session establishment request", 0xC2: "PDU session establishment accept",
        0xC3: "PDU session establishment reject", 0xC4: "Not used in current version",
        0xC5: "PDU session authentication command", 0xC6: "PDU session authentication complete",
        0xC7: "PDU session authentication result", 0xC8: "Not used in current version",
        0xC9: "PDU session modification request", 0xCA: "PDU session modification reject",
        0xCB: "PDU session modification command", 0xCC: "PDU session modification complete",
        0xCD: "PDU session modification command reject", 0xCE: "Not used in current version",
        0xCF: "Not used in current version", 0xD0: "Not used in current version",
        0xD1: "PDU session release request", 0xD2: "PDU session release reject",
        0xD3: "PDU session release command", 0xD4: "PDU session release complete",
        0xD5: "Not used in current version", 0xD6: "5GSM status", 0xD7: "Not used in current version",
        0xD8: "Service-level authentication command", 0xD9: "Service-level authentication complete",
        0xDA: "Remote UE report", 0xDB: "Remote UE report response"}
ПРИЧИНЫ_5GMM = {0x03: "Illegal UE", 0x05: "PEI not accepted", 0x06: "Illegal ME",
                0x07: "5GS services not allowed", 0x09: "UE identity cannot be derived by the network",
                0x0A: "Implicitly deregistered", 0x0B: "PLMN not allowed", 0x0C: "Tracking area not allowed",
                0x0D: "Roaming not allowed in this tracking area", 0x0F: "No suitable cells in tracking area",
                0x14: "MAC failure", 0x15: "Synch failure", 0x16: "Congestion",
                0x17: "UE security capabilities mismatch", 0x18: "Security mode rejected, unspecified",
                0x1A: "Non-5G authentication unacceptable", 0x1B: "N1 mode not allowed",
                0x1C: "Restricted service area", 0x1F: "Redirection to EPC required",
                0x24: "IAB-node operation not authorized", 0x2B: "LADN not available",
                0x3E: "No network slices available", 0x41: "Maximum number of PDU sessions reached",
                0x43: "Insufficient resources for specific slice and DNN",
                0x45: "Insufficient resources for specific slice", 0x47: "ngKSI already in use",
                0x48: "Non-3GPP access to 5GCN not allowed", 0x49: "Serving network not authorized",
                0x4A: "Temporarily not authorized for this SNPN",
                0x4B: "Permanently not authorized for this SNPN",
                0x4C: "Not authorized for this CAG or authorized for CAG cells only",
                0x4D: "Wireline access area not allowed",
                0x4E: "PLMN not allowed to operate at the present UE location",
                0x4F: "UAS services not allowed",
                0x50: "Disaster roaming for the determined PLMN with disaster condition not allowed",
                0x51: "Selected N3IWF is not compatible with the allowed NSSAI",
                0x52: "Selected TNGF is not compatible with the allowed NSSAI",
                0x5A: "Payload was not forwarded", 0x5B: "DNN not supported or not subscribed in the slice",
                0x5C: "Insufficient user-plane resources for the PDU session",
                0x5D: "Onboarding services terminated", 0x5E: "User plane positioning not authorized",
                0x5F: "Semantically incorrect message", 0x60: "Invalid mandatory information",
                0x61: "Message type non-existent or not implemented",
                0x62: "Message type not compatible with the protocol state",
                0x63: "Information element non-existent or not implemented", 0x64: "Conditional IE error",
                0x65: "Message not compatible with the protocol state", 0x6F: "Protocol error, unspecified"}
ПРИЧИНЫ_5GSM = {0x08: "Operator determined barring", 0x1A: "Insufficient resources",
                0x1B: "Missing or unknown DNN", 0x1C: "Unknown PDU session type",
                0x1D: "User authentication or authorization failed", 0x1F: "Request rejected, unspecified",
                0x20: "Service option not supported", 0x21: "Requested service option not subscribed",
                0x22: "Service option temporarily out of order", 0x23: "PTI already in use",
                0x24: "Regular deactivation", 0x25: "5GS QoS not accepted", 0x26: "Network failure",
                0x27: "Reactivation requested", 0x29: "Semantic error in the TFT operation",
                0x2A: "Syntactical error in the TFT operation", 0x2B: "Invalid PDU session identity",
                0x2C: "Semantic errors in packet filter(s)", 0x2D: "Syntactical error in packet filter(s)",
                0x2E: "Out of LADN service area", 0x2F: "PTI mismatch",
                0x32: "PDU session type IPv4 only allowed", 0x33: "PDU session type IPv6 only allowed",
                0x36: "PDU session does not exist", 0x39: "PDU session type IPv4v6 only allowed",
                0x3A: "PDU session type Unstructured only allowed", 0x3B: "Unsupported 5QI value",
                0x3D: "PDU session type Ethernet only allowed",
                0x43: "Insufficient resources for specific slice and DNN", 0x44: "Not supported SSC mode",
                0x45: "Insufficient resources for specific slice", 0x46: "Missing or unknown DNN in a slice",
                0x51: "Invalid PTI value",
                0x52: "Maximum data rate per UE for user-plane integrity protection is too low",
                0x53: "Semantic error in the QoS operation", 0x54: "Syntactical error in the QoS operation",
                0x55: "Invalid mapped EPS bearer identity", 0x56: "UAS services not allowed",
                0x57: "QoS differentiation for non-3GPP device identifier(s) not available",
                0x5F: "Semantically incorrect message", 0x60: "Invalid mandatory information",
                0x61: "Message type non-existent or not implemented",
                0x62: "Message type not compatible with the protocol state",
                0x63: "Information element non-existent or not implemented", 0x64: "Conditional IE error",
                0x65: "Message not compatible with the protocol state", 0x6F: "Protocol error, unspecified"}
РЕГИСТРАЦИЯ_5GS = {0x01: "initial registration", 0x02: "mobility registration updating",
                   0x03: "periodic registration updating", 0x04: "emergency registration",
                   0x05: "SNPN onboarding registration",
                   0x06: "disaster roaming mobility registration updating",
                   0x07: "disaster roaming initial registration"}
ПРИСОЕДИНЕНИЕ_EPS = {0x00: "EPS attach(unused)", 0x01: "EPS attach", 0x02: "Combined EPS/IMSI attach",
                     0x03: "EPS RLOS attach", 0x04: "EPS attach(unused)", 0x05: "EPS attach(unused)",
                     0x06: "EPS emergency attach", 0x07: "Disaster roaming attach"}

ТИП_ЗАЩИТЫ = {0: "без защиты", 1: "целостность", 2: "целостность и шифр", 3: "целостность, новый контекст",
              4: "целостность и шифр, новый контекст", 5: "целостность и частичный шифр",
              12: "заголовок SERVICE REQUEST"}
EEA = {0: "EEA0 (без шифра)", 1: "128-EEA1 (SNOW 3G)", 2: "128-EEA2 (AES)", 3: "128-EEA3 (ZUC)"}
EIA = {0: "EIA0 (без защиты)", 1: "128-EIA1 (SNOW 3G)", 2: "128-EIA2 (AES)", 3: "128-EIA3 (ZUC)"}
NEA = {0: "NEA0 (без шифра)", 1: "128-NEA1 (SNOW 3G)", 2: "128-NEA2 (AES)", 3: "128-NEA3 (ZUC)"}
NIA = {0: "NIA0 (без защиты)", 1: "128-NIA1 (SNOW 3G)", 2: "128-NIA2 (AES)", 3: "128-NIA3 (ZUC)"}
ВИДЫ_ИДЕНТ_ЗАПРОСА = {1: "IMSI", 2: "IMEI", 3: "IMEISV", 4: "TMSI"}
ВИДЫ_ИДЕНТ_5GS = {1: "SUCI", 2: "5G-GUTI", 3: "IMEI", 4: "5G-S-TMSI", 5: "IMEISV"}
СХЕМЫ = {0: "нулевая", 1: "ECIES профиль A", 2: "ECIES профиль B"}
КОНТЕЙНЕРЫ_5GS = {1: "N1 SM", 2: "SMS", 3: "LPP", 4: "SOR", 5: "UE policy", 6: "UE parameters update",
                  7: "location services", 8: "CIoT user data", 15: "несколько"}
ОБНОВЛЕНИЕ_EPS = {0: "TA updating", 1: "combined TA/LA updating", 2: "combined TA/LA updating with IMSI attach",
                  3: "periodic updating"}
#: Необязательные элементы TV с постоянной длиной (вместе с IEI); прочие ниже 0x80 — TLV, 0x7x — TLV-E,
#: от 0x80 — TV из одного октета (TS 24.301, 8.2 и 24.501, 8.2–8.3: таблицы сообщений).
TV_EPS = {0x13: 6, 0x53: 2, 0x17: 2, 0x59: 2}
TV_5GS = {0x12: 2, 0x21: 17, 0x56: 2, 0x58: 2, 0x59: 2}


def _plmn(б: bytes) -> str:
    """PLMN (24.008 10.5.1.3): MCC и MNC цифрами BCD, MNC из двух цифр — при 0xF на месте третьей."""
    mcc = f"{б[0] & 0xF}{б[0] >> 4}{б[1] & 0xF}"
    mnc = f"{б[2] & 0xF}{б[2] >> 4}" + ("" if б[1] >> 4 == 0xF else f"{б[1] >> 4}")
    return f"{mcc}-{mnc}"


def _метки(б: bytes) -> str:
    """APN/DNN (24.008 10.5.6.1): метки, каждая с байтом длины."""
    части, x = [], 0
    while x < len(б):
        части.append(б[x + 1:x + 1 + б[x]].decode("latin-1"))
        x += 1 + б[x]
    return ".".join(части)


def _адрес(б: bytes) -> str | None:
    """PDN address (24.301 9.9.4.9) / PDU address (24.501 9.11.4.10): тип и адрес; у IPv6 — идентификатор
    интерфейса (8 байт)."""
    if not б:
        return None
    вид = б[0] & 7
    iid = (lambda x: "IPv6 ::" + str(ipaddress.IPv6Address(bytes(8) + x)).lstrip(":")) if len(б) >= 9 else None
    if вид == 1 and len(б) >= 5:
        return str(ipaddress.IPv4Address(б[1:5]))
    if вид == 2 and iid:
        return iid(б[1:9])
    if вид == 3 and len(б) >= 13:
        return f"{ipaddress.IPv4Address(б[9:13])}, {iid(б[1:9])}"
    return None


def _eps_идентификатор(б: bytes) -> str | None:
    """EPS mobile identity (24.301 9.9.3.12): IMSI, IMEI или GUTI."""
    if not б:
        return None
    вид = б[0] & 7
    if вид == 6 and len(б) >= 11:
        return f"GUTI {_plmn(б[1:])}, MMEGI {u16(б, 4)}, MMEC {б[6]}, M-TMSI 0x{u32(б, 7):08X}"
    if вид in (1, 3):
        return f"{'IMSI' if вид == 1 else 'IMEI'} {gsm._цифры(б[1:], б[0] >> 4)}"
    return None


def _личность_24008(б: bytes) -> str | None:
    """Mobile identity 24.008 10.5.1.4 (IMEISV в Security mode complete, M-TMSI в Extended service request)."""
    и = gsm.идентификатор(б)
    return f"{и[0]} {и[1]}" if и else None


def _5gs_идентификатор(б: bytes) -> str | None:
    """5GS mobile identity (24.501 9.11.3.4)."""
    if not б:
        return None
    вид = б[0] & 7
    if вид == 1:
        формат = (б[0] >> 4) & 7
        if формат != 0 or len(б) < 8:
            return f"SUCI (формат SUPI {формат})"
        plmn, маршрут, схема = _plmn(б[1:]), gsm._цифры(б[4:6]) or "—", б[6] & 0x0F
        if схема == 0:
            return f"SUCI: IMSI {plmn.replace('-', '')}{gsm._цифры(б[8:])} (нулевая схема), маршрут {маршрут}"
        return (f"SUCI {plmn}, маршрут {маршрут}, схема {СХЕМЫ.get(схема, схема)}, ключ сети {б[7]}, "
                f"выход {len(б) - 8} байт")
    if вид == 2 and len(б) >= 11:
        return (f"5G-GUTI {_plmn(б[1:])}, AMF регион {б[4]}, набор {u16(б, 5) >> 6}, указатель {б[6] & 0x3F}, "
                f"5G-TMSI 0x{u32(б, 7):08X}")
    if вид == 4 and len(б) >= 7:
        return f"5G-S-TMSI: набор {u16(б, 1) >> 6}, указатель {б[2] & 0x3F}, 5G-TMSI 0x{u32(б, 3):08X}"
    if вид in (3, 5):
        return f"{ВИДЫ_ИДЕНТ_5GS[вид]} {gsm._цифры(б[1:], б[0] >> 4)}"
    return None


def _необязательные(д: bytes, x: int, край: int, tv: dict) -> dict:
    """Необязательные элементы: {IEI: (место значения, длина)}; у TV из одного октета ключ — старший
    полубайт (0x80…0xF0), значение — младший полубайт на месте IEI."""
    итог = {}
    while x < край:
        iei = ключ = д[x]
        if iei >= 0x80:                                     # тип 1: IEI — старший полубайт
            начало, n, ключ = x, 1, iei & 0xF0
        elif iei in tv:
            начало, n = x + 1, tv[iei] - 1
        elif iei & 0xF0 == 0x70:
            if x + 3 > край:
                break
            начало, n = x + 3, u16(д, x + 1)
        else:
            if x + 2 > край:
                break
            начало, n = x + 2, д[x + 1]
        if начало + n > край:                               # значение за концом сообщения — дальше не читать
            break
        итог.setdefault(ключ, (начало, n))
        x = начало + n
    return итог


class _Сообщение:
    """Разбор одного сообщения NAS в уровне: поля с местами, главное — в сводку."""

    def __init__(self, р: Разбор, у, край: int, ключ: str):
        self.р, self.у, self.д, self.край, self.ключ = р, у, р.д, край, ключ
        self.сводка: list = []

    def поле(self, имя, ключ, текст, место, длина, сырое=None, родитель=None):
        return self.у.поле(имя, f"{self.ключ}.{ключ}" if not ключ.startswith("e212") else ключ, текст, место,
                           длина, сырое, родитель=родитель)

    def lv(self, x: int, длинная: bool = False):
        """LV или LV-E: (место значения, длина, следующее место); None — не помещается."""
        n = u16(self.д, x) if длинная else self.д[x]
        начало = x + (2 if длинная else 1)
        if начало + n > self.край:
            raise IndexError("LV длиннее сообщения")
        return начало, n, начало + n

    def идентификатор(self, x: int, n: int, разбор: Callable, имя: str = "Идентификатор"):
        текст = разбор(self.д[x:x + n])
        if текст:
            self.поле(имя, "mobile_identity", текст, x, n)
            if "IMSI " in текст:
                imsi = текст.partition("IMSI ")[2].split()[0]
                self.поле("IMSI", "e212.imsi", imsi, x, n)
            self.сводка.append(текст)


# -- EPS ----------------------------------------------------------------------------------

def _esm(с: _Сообщение, x: int, край: int) -> None:
    д = с.д
    с.поле("Идентификатор носителя EPS", "bearer_id", д[x] >> 4, x, 1)
    с.поле("PTI", "esm.proc_trans_id", д[x + 1], x + 1, 1)
    тип = д[x + 2]
    с.поле("Тип сообщения ESM", "nas_msg_esm_type", f"0x{тип:02x} ({ESM.get(тип, 'неизвестный')})", x + 2, 1, тип)
    с.сводка.append(ESM.get(тип, f"ESM 0x{тип:02x}"))
    т = x + 3
    # Первый элемент — причина ESM (24.301 8.3): отказы активации и изменения, Deactivate EPS bearer
    # context request, отказы PDN connectivity/disconnect и ресурсов носителя, ESM status.
    if тип in (0xC3, 0xC7, 0xCB, 0xCD, 0xD1, 0xD3, 0xD5, 0xD7, 0xE8) and т < край:
        с.поле("Причина ESM", "esm.cause", ПРИЧИНЫ_ESM.get(д[т], д[т]), т, 1, д[т])
        с.сводка.append(f"причина: {ПРИЧИНЫ_ESM.get(д[т], д[т])}")
    elif тип == 0xDA:                                       # ESM information response: APN (0x28)
        ieis = _необязательные(д, т, край, TV_EPS)
        if 0x28 in ieis:
            м, n = ieis[0x28]
            apn = _метки(д[м:м + n])
            с.поле("APN", "esm.apn", apn, м, n)
            с.сводка.append(f"APN {apn}")
    elif тип == 0xD0 and т < край:
        с.поле("Тип PDN", "esm.pdn_type", (д[т] >> 4) & 7, т, 1)
        ieis = _необязательные(д, т + 1, край, TV_EPS)
        if 0x28 in ieis:
            м, n = ieis[0x28]
            apn = _метки(д[м:м + n])
            с.поле("APN", "esm.apn", apn, м, n)
            с.сводка.append(f"APN {apn}")
    elif тип == 0xC1:
        м, n, т = с.lv(т)                                   # EPS QoS
        м, n, т = с.lv(т)                                   # Access point name
        apn = _метки(д[м:м + n])
        с.поле("APN", "esm.apn", apn, м, n)
        м, n, т = с.lv(т)                                   # PDN address
        адрес = _адрес(д[м:м + n])
        if адрес:
            с.поле("Адрес PDN", "esm.pdn_addr", адрес, м, n)
        с.сводка.append(f"APN {apn}" + (f", адрес {адрес}" if адрес else ""))


def _emm(с: _Сообщение, x: int, край: int) -> None:
    """Простое сообщение EMM с x (байт заголовка и тип)."""
    д = с.д
    тип = д[x + 1]
    с.поле("Тип сообщения EMM", "nas_msg_emm_type", f"0x{тип:02x} ({EMM.get(тип, 'неизвестный')})", x + 1, 1, тип)
    с.сводка.append(EMM.get(тип, f"EMM 0x{тип:02x}"))
    т = x + 2
    if тип == 0x41:                                         # Attach request
        с.поле("Вид присоединения", "emm.eps_att_type", ПРИСОЕДИНЕНИЕ_EPS[д[т] & 7], т, 1, д[т] & 7)
        с.поле("NAS KSI", "emm.nas_key_set_id", д[т] >> 4, т, 1)
        м, n, т = с.lv(т + 1)
        с.идентификатор(м, n, _eps_идентификатор)
        м, n, т = с.lv(т)                                   # UE network capability
        м, n, т = с.lv(т, длинная=True)                     # ESM message container
        if n >= 3:
            _esm(с, м, м + n)
    elif тип in (0x42, 0x49):                               # Attach accept, TAU accept
        if тип == 0x42:
            м, n, т2 = с.lv(т + 2)                          # результат, T3412, TAI list
            м, n, т2 = с.lv(т2, длинная=True)               # ESM message container
            if n >= 3:
                _esm(с, м, м + n)
        else:
            т2 = т + 1
        ieis = _необязательные(д, т2, край, TV_EPS)
        if 0x50 in ieis:
            м, n = ieis[0x50]
            с.идентификатор(м, n, _eps_идентификатор, "Новый GUTI")
    elif тип in (0x44, 0x4B, 0x4E, 0x5C, 0x5F, 0x60) and т < край:
        с.поле("Причина EMM", "emm.cause", ПРИЧИНЫ_EMM.get(д[т], д[т]), т, 1, д[т])
        с.сводка.append(f"причина: {ПРИЧИНЫ_EMM.get(д[т], д[т])}")
    elif тип in (0x45, 0x48):                               # Detach request (от UE), TAU request
        if тип == 0x48:
            с.поле("Вид обновления", "emm.update_type", ОБНОВЛЕНИЕ_EPS.get(д[т] & 7, д[т] & 7), т, 1, д[т] & 7)
        if т + 1 < край:
            м, n, _ = с.lv(т + 1)
            с.идентификатор(м, n, _eps_идентификатор)
    elif тип == 0x5E:                                       # Security mode complete: IMEISV (0x23)
        ieis = _необязательные(д, т, край, TV_EPS)
        if 0x23 in ieis:
            м, n = ieis[0x23]
            с.идентификатор(м, n, _личность_24008)
    elif тип == 0x4C:                                       # Extended service request: вид, KSI, M-TMSI LV
        м, n, _ = с.lv(т + 1)
        с.идентификатор(м, n, _личность_24008)
    elif тип == 0x50:                                       # GUTI reallocation command
        м, n, _ = с.lv(т)
        с.идентификатор(м, n, _eps_идентификатор, "Новый GUTI")
    elif тип == 0x52 and т + 17 <= край:                    # Authentication request
        с.поле("RAND", "emm.rand", д[т + 1:т + 17].hex(), т + 1, 16)
        м, n, _ = с.lv(т + 17)
        с.поле("AUTN", "emm.autn", д[м:м + n].hex(), м, n)
    elif тип == 0x55 and т < край:                          # Identity request
        с.поле("Запрошено", "emm.id_type2", ВИДЫ_ИДЕНТ_ЗАПРОСА.get(д[т] & 7, д[т] & 7), т, 1, д[т] & 7)
        с.сводка.append(ВИДЫ_ИДЕНТ_ЗАПРОСА.get(д[т] & 7, str(д[т] & 7)))
    elif тип == 0x56:                                       # Identity response (24.008 10.5.1.4)
        м, n, _ = с.lv(т)
        и = gsm.идентификатор(д[м:м + n])
        if и:
            с.поле("Идентификатор", "mobile_identity", f"{и[0]} {и[1]}", м, n)
            if и[0] == "IMSI":
                с.поле("IMSI", "e212.imsi", и[1], м, n)
            с.сводка.append(f"{и[0]} {и[1]}")
    elif тип == 0x5D and т < край:                          # Security mode command
        шифр, целостность = (д[т] >> 4) & 7, д[т] & 7
        с.поле("Шифрование", "emm.toc", EEA.get(шифр, f"EEA{шифр}"), т, 1, шифр)
        с.поле("Целостность", "emm.toi", EIA.get(целостность, f"EIA{целостность}"), т, 1, целостность)
        с.сводка.append(f"EEA{шифр}/EIA{целостность}")
    elif тип in (0x62, 0x63):                               # NAS transport: SMS (24.011)
        м, n, _ = с.lv(т)
        с.вложенное = (м, м + n)


def nas_eps(р: Разбор, м: int, конец: int) -> str | None:
    д = р.д
    if м >= конец or д[м] & 0x0F not in (2, 7):
        return None
    у = р.уровень("NAS-EPS", "NAS EPS (TS 24.301)", м)
    с = _Сообщение(р, у, конец, "nas_eps")
    с.вложенное = None
    заг, pd = д[м] >> 4, д[м] & 0x0F
    if pd == 7:                                             # у ESM старший полубайт — носитель, его покажет _esm
        с.поле("Тип защиты", "security_header_type", ТИП_ЗАЩИТЫ.get(заг, заг), м, 1, заг)
    с.поле("Дискриминатор", "protocol_discriminator", "EMM" if pd == 7 else "ESM", м, 1, pd)
    try:
        if pd == 2:
            _esm(с, м, конец)
        elif заг == 12:
            с.поле("KSI и номер", "emm.ksi_and_seq", д[м + 1], м + 1, 1)
            с.поле("Короткий MAC", "emm.short_mac", f"0x{u16(д, м + 2):04x}", м + 2, 2, u16(д, м + 2))
            с.сводка.append("Service request")
        elif заг:
            с.поле("MAC", "msg_auth_code", f"0x{u32(д, м + 1):08x}", м + 1, 4, u32(д, м + 1))
            с.поле("Номер", "seq_no", д[м + 5], м + 5, 1)
            x = м + 6
            # Внутри — простое сообщение EMM или ESM (TS 24.301, 9.1: ESM без EMM-обёртки
            # защищается заголовком EMM, дискриминатор внутреннего — ESM).
            внутри = д[x:конец]
            esm = len(внутри) >= 3 and внутри[0] & 0x0F == 2
            простое = esm and внутри[2] in ESM or x + 2 <= конец and д[x] == 0x07 and д[x + 1] in EMM
            if заг in (1, 3) or простое and заг in (2, 4):
                if заг in (2, 4):
                    с.сводка.append("нулевой шифр")
                if esm:
                    _esm(с, x, конец)
                else:
                    _emm(с, x, конец)
            else:
                с.поле("Зашифрованное сообщение", "ciphered_msg", f"{конец - x} байт", x, конец - x)
                с.сводка.append("зашифровано")
        else:
            _emm(с, м, конец)
    except IndexError:
        р.ошибка("NAS EPS: сообщение оборвано")
        с.сводка.append("оборвано")
    у.длина = конец - м
    у.итог = ", ".join(с.сводка)
    if с.вложенное:
        gsm.dtap(р, *с.вложенное)
    return у.итог


# -- 5GS ----------------------------------------------------------------------------------

def _5gsm(с: _Сообщение, x: int, край: int) -> None:
    д = с.д
    с.поле("Сеанс PDU", "pdu_session_id", д[x + 1], x + 1, 1)
    с.поле("PTI", "pti", д[x + 2], x + 2, 1)
    тип = д[x + 3]
    с.поле("Тип сообщения 5GSM", "sm.message_type", f"0x{тип:02x} ({SM5G.get(тип, 'неизвестный')})", x + 3, 1, тип)
    с.сводка.append(SM5G.get(тип, f"5GSM 0x{тип:02x}"))
    т = x + 4
    # Первый элемент — причина 5GSM (24.501 8.3): establishment reject, modification reject и command
    # reject, release reject и command, 5GSM status.
    if тип in (0xC3, 0xCA, 0xCD, 0xD2, 0xD3, 0xD6) and т < край:
        с.поле("Причина 5GSM", "sm.5gsm_cause", ПРИЧИНЫ_5GSM.get(д[т], д[т]), т, 1, д[т])
        с.сводка.append(f"причина: {ПРИЧИНЫ_5GSM.get(д[т], д[т])}")
    elif тип == 0xC2:                                       # PDU session establishment accept
        _, _, т = с.lv(т + 1, длинная=True)                 # вид сеанса и SSC; разрешённые правила QoS
        _, _, т = с.lv(т)                                   # Session-AMBR
        ieis = _необязательные(д, т, край, TV_5GS)
        if 0x29 in ieis:
            м, n = ieis[0x29]
            адрес = _адрес(д[м:м + n])
            if адрес:
                с.поле("Адрес PDU", "sm.pdu_addr", адрес, м, n)
                с.сводка.append(f"адрес {адрес}")
        if 0x25 in ieis:
            м, n = ieis[0x25]
            dnn = _метки(д[м:м + n])
            с.поле("DNN", "dnn", dnn, м, n)
            с.сводка.append(f"DNN {dnn}")


def _5gmm(с: _Сообщение, x: int, край: int) -> None:
    д = с.д
    тип = д[x + 2]
    с.поле("Тип сообщения 5GMM", "mm.message_type", f"0x{тип:02x} ({MM5G.get(тип, 'неизвестный')})", x + 2, 1, тип)
    с.сводка.append(MM5G.get(тип, f"5GMM 0x{тип:02x}"))
    т = x + 3
    if тип in (0x41, 0x45, 0x4C):                           # Registration / Deregistration / Service request
        if тип == 0x41:
            вид = д[т] & 7
            с.поле("Вид регистрации", "mm.5gs_reg_type", РЕГИСТРАЦИЯ_5GS.get(вид, вид), т, 1, вид)
            с.сводка.append(РЕГИСТРАЦИЯ_5GS.get(вид, str(вид)))
        с.поле("ngKSI", "mm.ngksi", (д[т] >> 4) & 7, т, 1)
        м, n, _ = с.lv(т + 1, длинная=True)
        с.идентификатор(м, n, _5gs_идентификатор)
    elif тип == 0x5C:                                       # Identity response
        м, n, _ = с.lv(т, длинная=True)
        с.идентификатор(м, n, _5gs_идентификатор)
    elif тип == 0x5E:                                       # Security mode complete: IMEISV (0x77) и
        ieis = _необязательные(д, т, край, TV_5GS)          # повторённое сообщение NAS (0x71, 24.501 8.2.26)
        if 0x77 in ieis:
            м, n = ieis[0x77]
            с.идентификатор(м, n, _5gs_идентификатор)
        if 0x71 in ieis:
            м, n = ieis[0x71]
            с.контейнер = (м, м + n)
    elif тип in (0x42, 0x54):                               # Registration accept, Configuration update command
        т2 = с.lv(т)[2] if тип == 0x42 else т
        ieis = _необязательные(д, т2, край, TV_5GS)
        if 0x77 in ieis:
            м, n = ieis[0x77]
            с.идентификатор(м, n, _5gs_идентификатор, "Новый 5G-GUTI")
    elif тип in (0x44, 0x4D, 0x59, 0x5F, 0x64) and т < край:
        с.поле("Причина 5GMM", "mm.5gmm_cause", ПРИЧИНЫ_5GMM.get(д[т], д[т]), т, 1, д[т])
        с.сводка.append(f"причина: {ПРИЧИНЫ_5GMM.get(д[т], д[т])}")
    elif тип == 0x5B and т < край:
        с.поле("Запрошено", "mm.type_id", ВИДЫ_ИДЕНТ_5GS.get(д[т] & 7, д[т] & 7), т, 1, д[т] & 7)
        с.сводка.append(ВИДЫ_ИДЕНТ_5GS.get(д[т] & 7, str(д[т] & 7)))
    elif тип == 0x5D and т < край:
        шифр, целостность = д[т] >> 4, д[т] & 0x0F
        с.поле("Шифрование", "mm.nas_sec_algo_enc", NEA.get(шифр, f"NEA{шифр}"), т, 1, шифр)
        с.поле("Целостность", "mm.nas_sec_algo_ip", NIA.get(целостность, f"NIA{целостность}"), т, 1, целостность)
        с.сводка.append(f"NEA{шифр}/NIA{целостность}")
    elif тип == 0x56:                                       # Authentication request
        _, _, т2 = с.lv(т + 1)                              # ngKSI; ABBA
        ieis = _необязательные(д, т2, край, TV_5GS)
        if 0x21 in ieis:
            м, n = ieis[0x21]
            с.поле("RAND", "mm.rand", д[м:м + n].hex(), м, n)
    elif тип in (0x67, 0x68):                               # UL/DL NAS transport
        вид = д[т] & 0x0F
        с.поле("Тип контейнера", "mm.pld_cont_type", КОНТЕЙНЕРЫ_5GS.get(вид, вид), т, 1, вид)
        м, n, т2 = с.lv(т + 1, длинная=True)
        ieis = _необязательные(д, т2, край, TV_5GS)
        if 0x12 in ieis:
            с.поле("Сеанс PDU", "pdu_session_id", д[ieis[0x12][0]], ieis[0x12][0], 1)
        if 0x25 in ieis:
            dnn = _метки(д[ieis[0x25][0]:ieis[0x25][0] + ieis[0x25][1]])
            с.поле("DNN", "dnn", dnn, ieis[0x25][0], ieis[0x25][1])
            с.сводка.append(f"DNN {dnn}")
        if вид == 1 and n >= 4 and д[м] == 0x2E:
            _5gsm(с, м, м + n)
        elif вид == 2:
            с.вложенное = (м, м + n)


def nas_5gs(р: Разбор, м: int, конец: int) -> str | None:
    д = р.д
    if м + 3 > конец or д[м] not in (0x7E, 0x2E):
        return None
    у = р.уровень("NAS-5GS", "NAS 5GS (TS 24.501)", м)
    с = _Сообщение(р, у, конец, "nas_5gs")
    с.вложенное = с.контейнер = None
    с.поле("Расширенный дискриминатор", "epd", "5GMM" if д[м] == 0x7E else "5GSM", м, 1, д[м])
    try:
        if д[м] == 0x2E:
            _5gsm(с, м, конец)
        else:
            заг = д[м + 1] & 0x0F
            с.поле("Тип защиты", "security_header_type", ТИП_ЗАЩИТЫ.get(заг, заг), м + 1, 1, заг)
            if заг:
                с.поле("MAC", "msg_auth_code", f"0x{u32(д, м + 2):08x}", м + 2, 4, u32(д, м + 2))
                с.поле("Номер", "seq_no", д[м + 6], м + 6, 1)
                x = м + 7
                простое = x + 3 <= конец and д[x] == 0x7E and д[x + 1] == 0 and д[x + 2] in MM5G
                if заг in (1, 3) or простое and заг in (2, 4):
                    if заг in (2, 4):
                        с.сводка.append("нулевой шифр")
                    _5gmm(с, x, конец)
                else:
                    с.поле("Зашифрованное сообщение", "ciphered_msg", f"{конец - x} байт", x, конец - x)
                    с.сводка.append("зашифровано")
            else:
                _5gmm(с, м, конец)
    except IndexError:
        р.ошибка("NAS 5GS: сообщение оборвано")
        с.сводка.append("оборвано")
    у.длина = конец - м
    у.итог = ", ".join(с.сводка)
    if с.вложенное:
        gsm.dtap(р, *с.вложенное)
    if с.контейнер:
        nas_5gs(р, *с.контейнер)
    return у.итог


def _nas_pdu(разбор: Callable) -> Callable:
    """IE NAS-PDU (OCTET STRING в APER): определитель длины, затем сообщение NAS."""
    def разобрать(р: Разбор, м: int, конец: int):
        дл = mobilnye._aper_длина(р.д, м) if м < конец else None
        if дл is None or м + дл[1] + дл[0] != конец:
            return None
        return разбор(р, м + дл[1], конец)
    return разобрать


mobilnye.APER_IE_РАЗБОР[("S1AP", 26)] = _nas_pdu(nas_eps)
mobilnye.APER_IE_РАЗБОР[("NGAP", 38)] = _nas_pdu(nas_5gs)
ДОП_УРОВНИ["NAS-EPS"] = "прикладной"
ДОП_УРОВНИ["NAS-5GS"] = "прикладной"
