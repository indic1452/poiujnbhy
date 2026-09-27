"""CAMEL Application Part (CAP) поверх TCAP: интеллектуальная сеть GSM/UMTS (предоплата, переадресация).

Документы (структуры — строго по ним):

* 3GPP TS 29.078 (CAP фаз 2–4): операции (разд. 6 и 8, коды — opcode-…), InitialDPArg,
  InitialDPSMSArg, ConnectArg, EventReportBCSMArg, ReleaseCallArg (ASN.1, BER — X.690, неявные
  теги контекста); EventTypeBCSM и EventTypeSMS; номера — ISUP (Q.763 3.9 и 3.10: признак
  нечётности, характер адреса, план, цифры BCD), CalledPartyBCDNumber (24.008 10.5.4.7),
  AddressString и IMSI — TBCD (29.002 17.7.8); причина освобождения — Q.850 (второй октет).
* Узнаётся по контексту приложения TCAP: {0 4 0 0 1 0 50…52 …} — фаза 1/2 (тот же корень, что у MAP,
  но номер контекста 50–52), {0 4 0 0 1 21 …} и {0 4 0 0 1 22 …} — фазы 3 и 4; без диалога — по SSN
  146 (gsmSCF; так же по умолчанию и у Wireshark packet-camel.c).
* Коды операций и значения EventTypeBCSM/EventTypeSMS — из Wireshark (packet-camel.c) программно.
"""

from __future__ import annotations

from ..razbor import ДОП_УРОВНИ, Разбор
from . import oks7

CAP_ОПЕРАЦИИ = {0: "initialDP", 16: "assistRequestInstructions", 17: "establishTemporaryConnection",
                18: "disconnectForwardConnection", 19: "connectToResource", 20: "connect", 22: "releaseCall",
                23: "requestReportBCSMEvent", 24: "eventReportBCSM", 27: "collectInformation",
                31: "continue", 32: "initiateCallAttempt", 33: "resetTimer",
                34: "furnishChargingInformation", 35: "applyCharging", 36: "applyChargingReport",
                41: "callGap", 44: "callInformationReport", 45: "callInformationRequest",
                46: "sendChargingInformation", 47: "playAnnouncement", 48: "promptAndCollectUserInformation",
                49: "specializedResourceReport", 53: "cancel", 55: "activityTest", 60: "initialDPSMS",
                61: "furnishChargingInformationSMS", 62: "connectSMS", 63: "requestReportSMSEvent",
                64: "eventReportSMS", 65: "continueSMS", 66: "releaseSMS", 67: "resetTimerSMS",
                70: "activityTestGPRS", 71: "applyChargingGPRS", 72: "applyChargingReportGPRS",
                73: "cancelGPRS", 74: "connectGPRS", 75: "continueGPRS", 76: "entityReleasedGPRS",
                77: "furnishChargingInformationGPRS", 78: "initialDPGPRS", 79: "releaseGPRS",
                80: "eventReportGPRS", 81: "requestReportGPRSEvent", 82: "resetTimerGPRS",
                83: "sendChargingInformationGPRS", 86: "disconnectForwardConnectionWithArgument",
                88: "continueWithArgument", 90: "disconnectLeg", 93: "moveLeg", 95: "splitLeg",
                96: "entityReleased", 97: "playTone"}
СОБЫТИЯ_BCSM = {2: "collectedInfo", 3: "analyzedInformation", 4: "routeSelectFailure", 5: "oCalledPartyBusy",
                6: "oNoAnswer", 7: "oAnswer", 8: "oMidCall", 9: "oDisconnect", 10: "oAbandon",
                12: "termAttemptAuthorized", 13: "tBusy", 14: "tNoAnswer", 15: "tAnswer", 16: "tMidCall",
                17: "tDisconnect", 18: "tAbandon", 19: "oTermSeized", 27: "callAccepted", 50: "oChangeOfPosition",
                51: "tChangeOfPosition", 52: "oServiceChange", 53: "tServiceChange"}
СОБЫТИЯ_SMS = {1: "sms-CollectedInfo", 2: "o-smsFailure", 3: "o-smsSubmission", 11: "sms-DeliveryRequested",
               12: "t-smsFailure", 13: "t-smsDelivery"}
CAP_SSN = frozenset({146})


def _ber(д: bytes, м: int, конец: int):
    """Элемент BER (X.690 8.1): (класс, номер тега, начало значения, конец значения) или None;
    теги больше 30 — в нескольких октетах, длина — до трёх октетов."""
    if м >= конец:
        return None
    первый, x = д[м], м + 1
    номер = первый & 0x1F
    if номер == 0x1F:
        номер = 0
        while x < конец:
            б = д[x]
            x += 1
            номер = (номер << 7) | (б & 0x7F)
            if not б & 0x80:
                break
    if x >= конец:
        return None
    дл = д[x]
    x += 1
    if дл & 0x80:
        n = дл & 0x7F
        if not 1 <= n <= 3:                                 # что октеты длины влезают — проверка ниже
            return None
        дл = int.from_bytes(д[x:x + n], "big")
        x += n
    if x + дл > конец:
        return None
    return первый & 0xE0, номер, x, x + дл


def _элементы(д: bytes, н: int, к: int) -> dict:
    """Элементы SEQUENCE с тегами контекста: {номер: (начало, конец)} — первое вхождение."""
    итог = {}
    while (э := _ber(д, н, к)) is not None:
        if э[0] >> 6 == 2:                                  # класс «контекст»
            итог.setdefault(э[1], (э[2], э[3]))
        н = э[3]
    return итог


def _isup_номер(б: bytes) -> str | None:
    """Номер ISUP (Q.763 3.9/3.10): признак нечётности — бит 8 первого октета, цифры — с третьего."""
    if len(б) < 3:
        return None
    return oks7._цифры(б[2:], bool(б[0] >> 7))


def _bcd_номер(б: bytes) -> str | None:
    """CalledPartyBCDNumber / AddressString: октет вида номера и плана, затем TBCD."""
    return oks7._цифры(б[1:]) if len(б) >= 2 else None


def _аргумент(р: Разбор, у, п, код: int, параметр) -> list:
    """Главное из аргумента операции: ключ услуги, номера, IMSI, событие, причина."""
    д = р.д
    т, эм, н, к = параметр
    сведения = []

    def поле(подпись, ключ, текст, место):
        у.поле(подпись, ключ, текст, место[0], место[1] - место[0], родитель=п)
        сведения.append(f"{подпись} {текст}")

    if код == 22 and т == 0x04 and к - н >= 2:                   # releaseCall: Cause (Q.850)
        причина = д[н + 1] & 0x7F
        поле("причина", "camel.cause", f"{причина} ({oks7.ПРИЧИНЫ.get(причина, 'неизвестная')})", (н, к))
        return сведения
    if т != 0x30:
        return сведения
    э = _элементы(д, н, к)
    if код in (0, 60) and 0 in э:
        поле("ключ услуги", "camel.serviceKey", oks7._целое(д, *э[0]), э[0])
    if код == 0:
        for номер, подпись, ключ, разбор in ((3, "вызывающий", "camel.callingPartyNumber", _isup_номер),
                                              (2, "вызываемый", "camel.calledPartyNumber", _isup_номер),
                                              (56, "вызываемый (BCD)", "camel.calledPartyBCDNumber", _bcd_номер)):
            if номер in э and (цифры := разбор(д[э[номер][0]:э[номер][1]])):
                поле(подпись, ключ, цифры, э[номер])
        if 50 in э:
            поле("IMSI", "e212.imsi", oks7._цифры(д[э[50][0]:э[50][1]]), э[50])
        if 28 in э:
            событие = oks7._целое(д, *э[28])
            поле("событие", "camel.eventTypeBCSM", СОБЫТИЯ_BCSM.get(событие, str(событие)), э[28])
    elif код == 60:
        for номер, подпись, ключ in ((2, "отправитель", "camel.callingPartyNumber"),
                                     (1, "получатель", "camel.destinationSubscriberNumber"),
                                     (7, "SMSC", "camel.sMSCAddress")):
            if номер in э and (цифры := _bcd_номер(д[э[номер][0]:э[номер][1]])):
                поле(подпись, ключ, цифры, э[номер])
        if 4 in э:
            поле("IMSI", "e212.imsi", oks7._цифры(д[э[4][0]:э[4][1]]), э[4])
        if 3 in э:
            событие = oks7._целое(д, *э[3])
            поле("событие", "camel.eventTypeSMS", СОБЫТИЯ_SMS.get(событие, str(событие)), э[3])
    elif код == 20 and 0 in э:                                    # connect: destinationRoutingAddress
        первый = _ber(д, *э[0])
        if первый and (цифры := _isup_номер(д[первый[2]:первый[3]])):
            поле("на номер", "camel.destinationRoutingAddress", цифры, (первый[2], первый[3]))
    elif код == 24 and 0 in э:                                    # eventReportBCSM
        событие = oks7._целое(д, *э[0])
        поле("событие", "camel.eventTypeBCSM", СОБЫТИЯ_BCSM.get(событие, str(событие)), э[0])
    return сведения


def _это_cap(контекст: bytes | None, ssn) -> bool:
    if контекст is not None:
        return len(контекст) >= 6 and контекст[:4] == b"\x04\x00\x00\x01" and (
            контекст[4] == 0 and 50 <= контекст[5] <= 52 or контекст[4] in (21, 22))
    return any(s in CAP_SSN for s in ssn if s is not None)


def cap(р: Разбор, компоненты, контекст: bytes | None, ssn) -> bool:
    if not _это_cap(контекст, ssn):
        return False
    д = р.д
    начало = компоненты[0]["место"][0]
    у = р.уровень("CAMEL", "CAMEL Application Part (3GPP TS 29.078)", начало)
    у.длина = компоненты[-1]["место"][1] - начало
    if контекст is not None:
        у.поле("Контекст приложения", "camel.application_context", oks7._oid(контекст), начало, 0)
    итог = []
    for к_ in компоненты:
        if not к_["код"] or к_["код"][0] != 0x02:
            continue
        _, эм, н, к = к_["код"]
        код = oks7._целое(д, н, к)
        if к_["тег"] == 0xA3:
            у.поле("Ошибка CAP", "camel.error_code", код, эм, к - эм)
            итог.append(f"ошибка {код}")
            continue
        имя = CAP_ОПЕРАЦИИ.get(код, str(код))
        п = у.поле("Операция", "camel.opcode", f"{код} ({имя})" if код in CAP_ОПЕРАЦИИ else str(код), эм, к - эм, код)
        запись = имя if к_["тег"] == 0xA1 else f"{имя} (результат)"
        if к_["тег"] == 0xA1 and к_["параметр"]:
            сведения = _аргумент(р, у, п, код, к_["параметр"])
            if сведения:
                запись += ": " + ", ".join(сведения)
        итог.append(запись)
    у.итог = "; ".join(итог) or "компоненты без кода операции"
    р.п.инфо = "CAMEL " + у.итог
    return True


oks7.TCAP_ПРИЛОЖЕНИЯ.append(cap)
ДОП_УРОВНИ["CAMEL"] = "прикладной"
