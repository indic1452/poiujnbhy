"""RANAP — интерфейс Iu UMTS (RNC ↔ MSC/SGSN) поверх SCCP (3GPP TS 25.413).

Документы (структуры — строго по ним):

* TS 25.413, 9.3 (ASN.1; кодирование — ITU-T X.691, выровненный PER): RANAP-PDU ::= CHOICE
  {initiatingMessage, successfulOutcome, unsuccessfulOutcome, outcome, ...} — байт 0x00/0x20/0x40/0x60,
  затем procedureCode (байт), criticality и открытый тип с ProtocolIE-Container (как у S1AP).
* IE id-NAS-PDU = 16 (OCTET STRING): сообщение уровня 3 TS 24.008 (MM, CC, SMS, GMM, SM) — разбор GSM.
* На SCCP: SSN 142 (RANAP, TS 25.410 и 23.003) в UDT/XUDT и по признакам — в данных соединений
  (CR, CC, DT1; на Iu почти всё идёт в соединениях): длина PDU обязана совпасть с данными.
* Названия процедур — таблица ranap_ProcedureCode_vals Wireshark (packet-ranap.c) программно.
"""

from __future__ import annotations

from ..razbor import ДОП_УРОВНИ, Разбор
from . import gsm, oks7
from .mobilnye import APER_IE_РАЗБОР, _aper_pdu, _aper_длина, _свой

RANAP_ПРОЦЕДУРЫ = {0: "RAB-Assignment", 1: "Iu-Release", 2: "RelocationPreparation",
                   3: "RelocationResourceAllocation", 4: "RelocationCancel", 5: "SRNS-ContextTransfer",
                   6: "SecurityModeControl", 7: "DataVolumeReport", 8: "Not-Used-8", 9: "Reset",
                   10: "RAB-ReleaseRequest", 11: "Iu-ReleaseRequest", 12: "RelocationDetect",
                   13: "RelocationComplete", 14: "Paging", 15: "CommonID", 16: "CN-InvokeTrace",
                   17: "LocationReportingControl", 18: "LocationReport", 19: "InitialUE-Message",
                   20: "DirectTransfer", 21: "OverloadControl", 22: "ErrorIndication",
                   23: "SRNS-DataForward", 24: "ForwardSRNS-Context", 25: "privateMessage",
                   26: "CN-DeactivateTrace", 27: "ResetResource", 28: "RANAP-Relocation",
                   29: "RAB-ModifyRequest", 30: "LocationRelatedData", 31: "InformationTransfer",
                   32: "UESpecificInformation", 33: "UplinkInformationExchange",
                   34: "DirectInformationTransfer", 35: "MBMSSessionStart", 36: "MBMSSessionUpdate",
                   37: "MBMSSessionStop", 38: "MBMSUELinking", 39: "MBMSRegistration",
                   40: "MBMSCNDe-Registration-Procedure", 41: "MBMSRABEstablishmentIndication",
                   42: "MBMSRABRelease", 43: "enhancedRelocationComplete",
                   44: "enhancedRelocationCompleteConfirm", 45: "RANAPenhancedRelocation",
                   46: "SRVCCPreparation", 47: "UeRadioCapabilityMatch", 48: "UeRegistrationQuery",
                   49: "RerouteNASRequest"}


@_свой
def ranap(р: Разбор, м: int, конец: int) -> bool:
    return _aper_pdu(р, м, конец, "RANAP", "Radio Access Network Application Part (TS 25.413)", RANAP_ПРОЦЕДУРЫ,
                     вариантов=4)


def _nas_pdu(р: Разбор, м: int, конец: int) -> str | None:
    """IE NAS-PDU: определитель длины APER и сообщение 24.008; сводка — итог его уровня."""
    дл = _aper_длина(р.д, м) if м < конец else None
    if дл is None or м + дл[1] + дл[0] != конец:
        return None
    уровней = len(р.п.уровни)
    if not gsm.dtap(р, м + дл[1], конец):
        return None
    return р.п.уровни[уровней].итог


APER_IE_РАЗБОР[("RANAP", 16)] = _nas_pdu
oks7.SCCP_ПОДСИСТЕМЫ[142] = ranap
oks7.SCCP_СОЕДИНЕНИЯ.append(ranap)
ДОП_УРОВНИ["RANAP"] = "прикладной"
