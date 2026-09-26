# -*- coding: utf-8 -*-
"""Мобильные сети и AAA: Diameter, GTPv1-C, GTPv2-C, GTP', PFCP, S1AP, NGAP, X2AP, SGsAP,
GSMTAP, TACACS+, Kerberos, LDAP.

Документы (структуры — строго по ним; таблицы кодов короткие, незнакомое — числом):

- Diameter — IETF RFC 6733: разд. 3 (заголовок), 3.1 (коды команд базового протокола),
  4.1 (заголовок AVP), 4.2–4.4 (типы данных, Grouped), 4.5 (коды AVP базового протокола),
  7.1 (Result-Code); кредитный контроль — RFC 4006, разд. 3 и 8; NASREQ — RFC 7155, разд. 3;
  S6a/S6d — 3GPP TS 29.272, разд. 7.2; Cx — TS 29.229, разд. 6.1. Порт TCP и SCTP 3868,
  PPID SCTP 46 (RFC 6733, разд. 2.1 и 11.4). TLS-порт 5868 не разбирается (там шифр).
- GTPv1-C — 3GPP TS 29.060: разд. 6 (заголовок), 7.1 (типы сообщений), 7.7 (IE: TV и TLV),
  UDP 2123.
- GTPv2-C — 3GPP TS 29.274: разд. 5.1 (заголовок), 5.5 (флаг T), 6.1 (типы), 8.1–8.2 (IE),
  UDP 2123.
- GTP' — 3GPP TS 32.295: разд. 6.1 (заголовок), 6.2 (типы сообщений), UDP 3386.
- PFCP — 3GPP TS 29.244: разд. 7.2.2 (заголовок), 7.3 (типы), 8.1.1 (формат IE), 8.1.2 (типы IE),
  UDP 8805.
- S1AP — 3GPP TS 36.413, разд. 9.3 (ASN.1); NGAP — TS 38.413, разд. 9.4; X2AP — TS 36.423,
  разд. 9.3. Кодирование — ITU-T X.691 (выровненный PER). PPID SCTP 18/60/27, порты SCTP
  36412 (TS 36.412), 38412 (TS 38.412), 36422 (TS 36.422). Разбирается только верхний
  уровень, однозначно читаемый без ASN.1-компилятора.
- SGsAP — 3GPP TS 29.118: разд. 8 (сообщения), 9.2 (типы сообщений), 9.3 (IEI), 9.4 (IE),
  SCTP 29118; идентификатор абонента — TS 24.008, разд. 10.5.1.4.
- GSMTAP — заголовок версии 2 по osmocom (libosmocore, include/osmocom/core/gsmtap.h), UDP 4729.
- TACACS+ — IETF RFC 8907: разд. 4.1 (заголовок), 5.1–5.3 (аутентификация), 6.1–6.2
  (авторизация), 7.1–7.2 (учёт); TCP 49.
- Kerberos — IETF RFC 4120: разд. 5.2–5.9 (ASN.1), 7.2.1–7.2.2 (UDP/TCP 88, длина на TCP),
  типы шифров — RFC 3961/3962/4757/8009.
- LDAP — IETF RFC 4511: разд. 4.1.1 (LDAPMessage), 4.2 (bind), 4.5.1 (search), 5.1 (BER);
  TCP/UDP 389.

Каждый разборщик проверяет всё, что стандарт делает проверяемым (версии, длины, зарезервированные
биты, согласованность полей), и возвращает False, если данные не его, — ничего не оставляя.
Оборванное сообщение (длина больше записанного) признаётся своим, только если признаки сильные,
и помечается «оборван»; на UDP — только если и сам кадр записан не целиком (snaplen).
"""

from __future__ import annotations

import datetime
import functools
import ipaddress
import struct
from typing import Callable, Dict, List, Optional, Tuple

from .. import prilozh, razbor
from ..pole import Мало, Поле, Уровень, u16, u24, u32, u64
from ..razbor import Разбор, данные


class _НеМоё(Exception):
    """Данные не этого протокола — разборщик откатывается и отвечает False."""


def _свой(функция: Callable) -> Callable:
    """Обёртка разборщика: False или ошибка разбора — всё, что он успел добавить, убирается."""
    @functools.wraps(функция)
    def обёртка(р: Разбор, м: int, конец: int) -> bool:
        п = р.п
        снимок = (len(п.уровни), len(п.ошибки), п.инфо, п.нагрузка, п.нагрузка_смещение)
        try:
            if функция(р, м, конец):
                return True
        except (_НеМоё, Мало, IndexError, struct.error):
            pass
        del п.уровни[снимок[0]:]
        del п.ошибки[снимок[1]:]
        п.инфо, п.нагрузка, п.нагрузка_смещение = снимок[2:]
        return False
    return обёртка


def _скрытое(у: Уровень, ключ: str) -> None:
    """Имя для фильтра, когда имя протокола в фильтре не пишется (tacplus, gtpprime)."""
    у.поле("", ключ, True, у.смещение, 0)


def _оборван(р: Разбор, у: Уровень, имя: str, объявлено: int, есть: int) -> None:
    у.итог += " [оборван]"
    р.ошибка(f"{имя}: сообщение оборвано (длина {объявлено}, в пакете {есть}; "
             "продолжение не записано или в другом сегменте)")


def _захват_оборван(р: Разбор, конец: int) -> bool:
    """Кадр записан не целиком (snaplen: исходная длина больше записанной) и данные доходят до
    конца записанного. Только так сообщение в датаграмме UDP может быть длиннее её данных:
    иначе «длина больше данных» — признак чужих данных, а не обрыва."""
    return р.п.исходная_длина > len(р.д) and конец >= len(р.д)


def _по_очереди(р: Разбор, м: int, конец: int, одно, имя: str) -> bool:
    """Несколько сообщений подряд (TCP): первое обязано быть своим, дальше — пока сходится.

    ``одно(р, м, конец)`` → (конец сообщения, итог) или None. Остаток сегмента (начало
    следующего сообщения в другом сегменте) — данными.
    """
    конец = min(конец, len(р.д))
    итоги: List[str] = []
    место = м
    while место < конец and len(итоги) < 64:
        уровней, ошибок = len(р.п.уровни), len(р.п.ошибки)
        try:
            вышло = одно(р, место, конец)
        except (_НеМоё, Мало, IndexError, struct.error):
            вышло = None
        if вышло is None:                                  # это сообщение не сошлось — убрать его след
            del р.п.уровни[уровней:]
            del р.п.ошибки[ошибок:]
            break
        место, итог = вышло
        итоги.append(итог)
    if not итоги:
        return False
    инфо = f"{имя} " + "; ".join(итоги)
    if место < конец:
        данные(р, место, f"{имя}: остаток сегмента", конец)
    р.п.инфо = инфо
    return True


# -- общие декодеры ----------------------------------------------------------------------------

def _tbcd(байты: bytes) -> str:
    """Цифры TBCD (3GPP TS 29.002, TBCD-STRING): младший полубайт первым, F — заполнитель."""
    знаки = "0123456789*#abc"
    итог = []
    for б in байты:
        for н in (б & 15, б >> 4):
            if н == 15:
                return "".join(итог)
            итог.append(знаки[н])
    return "".join(итог)


def _plmn(д: bytes, м: int) -> str:
    """MCC-MNC из трёх октетов (3GPP TS 24.008, 10.5.1.3; TS 29.274, 8.18)."""
    mcc = f"{д[м] & 15}{д[м] >> 4}{д[м + 1] & 15}"
    mnc3 = д[м + 1] >> 4
    mnc = f"{д[м + 2] & 15}{д[м + 2] >> 4}" + ("" if mnc3 == 15 else str(mnc3))
    return f"{mcc}-{mnc}"


def _метки(байты: bytes) -> Optional[str]:
    """Имя из меток с длиной впереди (APN, FQDN — 3GPP TS 23.003, 9.1; RFC 1035, 3.1)."""
    части, i = [], 0
    while i < len(байты):
        n = байты[i]
        if n == 0 or i + 1 + n > len(байты):
            return None
        части.append(байты[i + 1:i + 1 + n].decode("ascii", "replace"))
        i += 1 + n
    return ".".join(части) if части else None


def _ip(байты: bytes) -> str:
    if len(байты) in (4, 16):
        return str(ipaddress.ip_address(байты))
    return байты.hex()


def _ntp_секунды(сек: int) -> str:
    момент = datetime.datetime(1900, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(seconds=сек)
    return момент.strftime("%Y-%m-%d %H:%M:%S UTC")


def _текст(байты: bytes) -> str:
    return байты.decode("utf-8", "replace")


def _личность(байты: bytes) -> Tuple[int, str]:
    """Mobile Identity (3GPP TS 24.008, 10.5.1.4): тип и значение (IMSI/IMEI цифрами)."""
    тип = байты[0] & 7
    if тип in (1, 2, 3):                                  # IMSI, IMEI, IMEISV — цифры BCD
        цифры = [байты[0] >> 4]
        for б in байты[1:]:
            цифры += [б & 15, б >> 4]
        if not байты[0] & 8 and цифры and цифры[-1] == 15:   # чётное число цифр: заполнитель F
            цифры.pop()
        return тип, "".join("0123456789"[ц] if ц < 10 else "?" for ц in цифры)
    return тип, байты[1:].hex()


# ==============================================================================================
# Diameter — RFC 6733
# ==============================================================================================

#: Код команды → (имя, сокращение без R/A). RFC 6733, 3.1; RFC 4006, 3; RFC 7155, 3;
#: TS 29.272, 7.2.1; TS 29.229, 6.1.
DIAMETER_КОМАНДЫ = {
    257: ("Capabilities-Exchange", "CE"), 258: ("Re-Auth", "RA"), 271: ("Accounting", "AC"),
    274: ("Abort-Session", "AS"), 275: ("Session-Termination", "ST"), 280: ("Device-Watchdog", "DW"),
    282: ("Disconnect-Peer", "DP"), 272: ("Credit-Control", "CC"), 265: ("AA", "AA"),
    268: ("Diameter-EAP", "DE"), 316: ("Update-Location", "UL"), 317: ("Cancel-Location", "CL"),
    318: ("Authentication-Information", "AI"), 319: ("Insert-Subscriber-Data", "ID"),
    320: ("Delete-Subscriber-Data", "DS"), 321: ("Purge-UE", "PU"), 323: ("Notify", "NO"),
    300: ("User-Authorization", "UA"), 301: ("Server-Assignment", "SA"), 302: ("Location-Info", "LI"),
    303: ("Multimedia-Auth", "MA"), 304: ("Registration-Termination", "RT"), 305: ("Push-Profile", "PP"),
}
#: Application-ID (RFC 6733, 2.4 и 11.3; 3GPP TS 29.272/29.212/29.214/29.229/29.329).
DIAMETER_ПРИЛОЖЕНИЯ = {0: "Diameter common messages", 1: "NASREQ", 3: "Diameter base accounting",
                       4: "Credit Control", 5: "EAP", 16777216: "3GPP Cx", 16777217: "3GPP Sh",
                       16777236: "3GPP Rx", 16777238: "3GPP Gx", 16777251: "3GPP S6a/S6d",
                       0xFFFFFFFF: "Relay"}
#: Код AVP → (имя, тип). Типы: utf8, ident (DiameterIdentity), u32, u64, i32 (и Enumerated),
#: addr (Address), time (Time), octets, plmn, group (Grouped).
DIAMETER_AVP: Dict[int, Tuple[str, str]] = {
    1: ("User-Name", "utf8"), 25: ("Class", "octets"), 27: ("Session-Timeout", "u32"),
    33: ("Proxy-State", "octets"), 44: ("Accounting-Session-Id", "octets"),
    50: ("Acct-Multi-Session-Id", "utf8"), 55: ("Event-Timestamp", "time"),
    85: ("Acct-Interim-Interval", "u32"), 257: ("Host-IP-Address", "addr"),
    258: ("Auth-Application-Id", "u32"), 259: ("Acct-Application-Id", "u32"),
    260: ("Vendor-Specific-Application-Id", "group"), 261: ("Redirect-Host-Usage", "i32"),
    262: ("Redirect-Max-Cache-Time", "u32"), 263: ("Session-Id", "utf8"), 264: ("Origin-Host", "ident"),
    265: ("Supported-Vendor-Id", "u32"), 266: ("Vendor-Id", "u32"), 267: ("Firmware-Revision", "u32"),
    268: ("Result-Code", "u32"), 269: ("Product-Name", "utf8"), 270: ("Session-Binding", "u32"),
    271: ("Session-Server-Failover", "i32"), 272: ("Multi-Round-Time-Out", "u32"),
    273: ("Disconnect-Cause", "i32"), 274: ("Auth-Request-Type", "i32"), 276: ("Auth-Grace-Period", "u32"),
    277: ("Auth-Session-State", "i32"), 278: ("Origin-State-Id", "u32"), 279: ("Failed-AVP", "group"),
    280: ("Proxy-Host", "ident"), 281: ("Error-Message", "utf8"), 282: ("Route-Record", "ident"),
    283: ("Destination-Realm", "ident"), 284: ("Proxy-Info", "group"), 285: ("Re-Auth-Request-Type", "i32"),
    287: ("Accounting-Sub-Session-Id", "u64"), 291: ("Authorization-Lifetime", "u32"),
    292: ("Redirect-Host", "utf8"), 293: ("Destination-Host", "ident"),
    294: ("Error-Reporting-Host", "ident"), 295: ("Termination-Cause", "i32"),
    296: ("Origin-Realm", "ident"), 297: ("Experimental-Result", "group"),
    298: ("Experimental-Result-Code", "u32"), 299: ("Inband-Security-Id", "u32"),
    480: ("Accounting-Record-Type", "i32"), 483: ("Accounting-Realtime-Required", "i32"),
    485: ("Accounting-Record-Number", "u32"),
    # RFC 4006, 8.
    412: ("CC-Input-Octets", "u64"), 414: ("CC-Output-Octets", "u64"), 415: ("CC-Request-Number", "u32"),
    416: ("CC-Request-Type", "i32"), 420: ("CC-Time", "u32"), 421: ("CC-Total-Octets", "u64"),
    431: ("Granted-Service-Unit", "group"), 432: ("Rating-Group", "u32"),
    437: ("Requested-Service-Unit", "group"), 439: ("Service-Identifier", "u32"),
    443: ("Subscription-Id", "group"), 444: ("Subscription-Id-Data", "utf8"),
    446: ("Used-Service-Unit", "group"), 450: ("Subscription-Id-Type", "i32"),
    456: ("Multiple-Services-Credit-Control", "group"), 461: ("Service-Context-Id", "utf8"),
}
#: AVP 3GPP (Vendor-ID 10415): TS 29.272, 7.3; TS 29.212, 5.3.
DIAMETER_AVP_3GPP: Dict[int, Tuple[str, str]] = {
    1407: ("Visited-PLMN-Id", "plmn"), 1032: ("RAT-Type", "i32"),
    1400: ("Subscription-Data", "group"), 1413: ("Authentication-Info", "group"),
}
DIAMETER_ВЕНДОРЫ = {10415: "3GPP", 5535: "3GPP2", 13019: "ETSI"}
DIAMETER_РЕЗУЛЬТАТ = {
    2001: "DIAMETER_SUCCESS", 2002: "DIAMETER_LIMITED_SUCCESS", 3001: "DIAMETER_COMMAND_UNSUPPORTED",
    3002: "DIAMETER_UNABLE_TO_DELIVER", 3003: "DIAMETER_REALM_NOT_SERVED", 3004: "DIAMETER_TOO_BUSY",
    3005: "DIAMETER_LOOP_DETECTED", 3006: "DIAMETER_REDIRECT_INDICATION",
    3007: "DIAMETER_APPLICATION_UNSUPPORTED", 4001: "DIAMETER_AUTHENTICATION_REJECTED",
    5001: "DIAMETER_AVP_UNSUPPORTED", 5002: "DIAMETER_UNKNOWN_SESSION_ID",
    5003: "DIAMETER_AUTHORIZATION_REJECTED", 5004: "DIAMETER_INVALID_AVP_VALUE",
    5005: "DIAMETER_MISSING_AVP", 5012: "DIAMETER_UNABLE_TO_COMPLY",
}
_DIAMETER_ЗНАЧЕНИЯ = {
    268: DIAMETER_РЕЗУЛЬТАТ, 258: DIAMETER_ПРИЛОЖЕНИЯ, 259: DIAMETER_ПРИЛОЖЕНИЯ,
    416: {1: "INITIAL_REQUEST", 2: "UPDATE_REQUEST", 3: "TERMINATION_REQUEST", 4: "EVENT_REQUEST"},
    273: {0: "REBOOTING", 1: "BUSY", 2: "DO_NOT_WANT_TO_TALK_TO_YOU"},
    277: {0: "STATE_MAINTAINED", 1: "NO_STATE_MAINTAINED"},
    450: {0: "END_USER_E164", 1: "END_USER_IMSI", 2: "END_USER_SIP_URI", 3: "END_USER_NAI",
          4: "END_USER_PRIVATE"},
    480: {1: "EVENT_RECORD", 2: "START_RECORD", 3: "INTERIM_RECORD", 4: "STOP_RECORD"},
}
_DIAMETER_ЗНАЧЕНИЯ_3GPP = {1032: {1000: "UTRAN", 1001: "GERAN", 1004: "EUTRAN"}}


def _diameter_имя(код: int, запрос: bool) -> Tuple[str, str]:
    """(сокращение, полное имя) команды."""
    if код in DIAMETER_КОМАНДЫ:
        имя, сокр = DIAMETER_КОМАНДЫ[код]
        return сокр + ("R" if запрос else "A"), f"{имя}-{'Request' if запрос else 'Answer'}"
    вид = "запрос" if запрос else "ответ"
    return f"команда {код} ({вид})", f"команда {код} ({вид})"


def _avp_цепочка(д: bytes, м: int, конец: int, до_конца: bool) -> bool:
    """Цепочка AVP (RFC 6733, 4.1): зарезервированные биты флагов — нули, длина не меньше
    заголовка, каждый AVP выровнен на 4 байта. ``до_конца`` — цепочка обязана кончиться
    ровно на ``конец``; иначе (сообщение оборвано) последний AVP может уходить за край."""
    while м < конец:
        if м + 8 > конец:
            return not до_конца
        флаги, дл = д[м + 4], u24(д, м + 5)
        if флаги & 0x1F or дл < (12 if флаги & 0x80 else 8):
            return False
        м += (дл + 3) & ~3
    return м == конец or not до_конца


def _diameter_заголовок(д: bytes, м: int, конец: int) -> Optional[int]:
    """Длина сообщения, если с ``м`` начинается сообщение Diameter (RFC 6733, 3), иначе None.

    Проверяется: версия 1; длина кратна 4 и не меньше 20; зарезервированные биты флагов —
    нули; флаг E не стоит в запросе (3: «MUST NOT be set in request messages»); цепочка AVP
    сходится с длиной. Оборванное сообщение — только с известным кодом команды.
    """
    if конец - м < 20 or д[м] != 1:
        return None
    длина, флаги, код = u24(д, м + 1), д[м + 4], u24(д, м + 5)
    if длина < 20 or длина % 4 or флаги & 0x0F or (флаги & 0x80 and флаги & 0x20):
        return None
    полное = м + длина <= конец
    if not полное and код not in DIAMETER_КОМАНДЫ:
        return None
    if not _avp_цепочка(д, м + 20, min(конец, м + длина), полное):
        return None
    return длина


def _avp_значение(д: bytes, вид: str, код: int, таблица: Dict[int, Dict[int, str]]):
    """(текст, сырое, верна ли длина) значения AVP по его типу (RFC 6733, 4.2–4.3)."""
    размер = {"u32": 4, "i32": 4, "u64": 8, "time": 4}.get(вид)
    if размер is not None and len(д) != размер:
        return д.hex() or "(пусто)", д.hex(), False
    if вид in ("utf8", "ident"):
        return _текст(д), _текст(д), True
    if вид in ("u32", "u64"):
        ч = int.from_bytes(д, "big")
        имя = таблица.get(код, {}).get(ч)
        return (f"{ч} ({имя})" if имя else str(ч)), ч, True
    if вид == "i32":
        ч = int.from_bytes(д, "big", signed=True)
        имя = таблица.get(код, {}).get(ч)
        return (f"{ч} ({имя})" if имя else str(ч)), ч, True
    if вид == "time":
        return _ntp_секунды(u32(д, 0)), u32(д, 0), True
    if вид == "addr":
        if len(д) >= 2 and ((u16(д, 0) == 1 and len(д) == 6) or (u16(д, 0) == 2 and len(д) == 18)):
            return _ip(д[2:]), _ip(д[2:]), True
        return д.hex(), д.hex(), False
    if вид == "plmn" and len(д) == 3:
        return _plmn(д, 0), _plmn(д, 0), True
    return (д.hex() or "(пусто)"), д.hex(), True


def _avp_дерево(р: Разбор, у: Уровень, м: int, конец: int, родитель: Optional[Поле], глубина: int,
                сводка: Optional[Dict[str, str]]) -> None:
    """AVP деревом (RFC 6733, 4.1; Grouped — 4.4). ``конец`` — не дальше записанного."""
    д = р.д
    место = м
    while место + 8 <= конец:
        код, флаги, дл = u32(д, место), д[место + 4], u24(д, место + 5)
        v = флаги & 0x80
        заг = 12 if v else 8
        if дл < заг or (v and место + 12 > конец):
            break
        вендор = u32(д, место + 8) if v else 0
        if v:
            имя, вид = DIAMETER_AVP_3GPP.get(код, (None, "octets")) if вендор == 10415 else (None, "octets")
            таблица = _DIAMETER_ЗНАЧЕНИЯ_3GPP if вендор == 10415 else {}
        else:
            имя, вид = DIAMETER_AVP.get(код, (None, "octets"))
            таблица = _DIAMETER_ЗНАЧЕНИЯ
        подпись = имя or f"AVP {код}"
        полон = место + дл <= конец
        выровнено = min((дл + 3) & ~3, конец - место)
        if вид == "group" or not полон:
            текст, сырое, верна = ("[оборван]" if not полон else ""), None, полон
        else:
            текст, сырое, верна = _avp_значение(д[место + заг:место + дл], вид, код, таблица)
        п = у.поле(f"AVP: {подпись}({код})" + (f" = {текст}" if текст else ""), "diameter.avp", подпись,
                   место, выровнено, код, плохо=not верна, родитель=родитель)
        у.поле("Код AVP", "diameter.avp.code", код, место, 4, родитель=п)
        пф = у.поле("Флаги AVP", "diameter.avp.flags",
                    f"0x{флаги:02x} ({', '.join(б for б, м_ in (('V', 0x80), ('M', 0x40), ('P', 0x20)) if флаги & м_) or 'нет'})",
                    место + 4, 1, флаги, родитель=п)
        у.поле("Вендор (V)", "diameter.avp.flags.v", флаги >> 7 & 1, место + 4, 1, родитель=пф)
        у.поле("Обязательный (M)", "diameter.avp.flags.m", флаги >> 6 & 1, место + 4, 1, родитель=пф)
        у.поле("Защищённый (P)", "diameter.avp.flags.p", флаги >> 5 & 1, место + 4, 1, родитель=пф)
        у.поле("Длина AVP", "diameter.avp.len", дл, место + 5, 3, родитель=п)
        if v:
            у.поле("Vendor-ID", "diameter.avp.vendorid",
                   f"{вендор} ({DIAMETER_ВЕНДОРЫ.get(вендор, 'неизвестный')})", место + 8, 4, вендор, родитель=п)
        if вид == "group" and полон and глубина < 8 and _avp_цепочка(д, место + заг, место + дл, True):
            _avp_дерево(р, у, место + заг, место + дл, п, глубина + 1, None)
        elif вид == "group" and полон:
            п.плохо = True
            у.поле("Содержимое (AVP внутри не сошлись)", "diameter.avp.data", д[место + заг:место + дл].hex(),
                   место + заг, дл - заг, родитель=п)
        elif полон:
            ключ = f"diameter.{имя.lower()}" if имя else "diameter.avp.data"
            у.поле("Значение", ключ, текст or д[место + заг:место + дл].hex(), место + заг, дл - заг,
                   сырое if сырое is not None else д[место + заг:место + дл].hex(), плохо=not верна, родитель=п)
            if сводка is not None and имя and имя not in сводка:
                сводка[имя] = текст
        if полон and выровнено > дл:
            у.поле("Выравнивание", "diameter.avp.pad", выровнено - дл, место + дл, выровнено - дл, родитель=п)
        место += (дл + 3) & ~3


def _diameter_одно(р: Разбор, м: int, конец: int, *, только_известные: bool = False):
    д = р.д
    длина = _diameter_заголовок(д, м, конец)
    if длина is None:
        return None
    флаги, код = д[м + 4], u24(д, м + 5)
    if только_известные and (код not in DIAMETER_КОМАНДЫ or м + длина > конец):
        return None
    запрос = bool(флаги & 0x80)
    сокр, полное = _diameter_имя(код, запрос)
    у = р.уровень("Diameter", "Diameter (RFC 6733)", м)
    у.поле("Версия", "diameter.version", 1, м, 1)
    у.поле("Длина", "diameter.length", длина, м + 1, 3)
    имена = [б for б, бит in (("R", 0x80), ("P", 0x40), ("E", 0x20), ("T", 0x10)) if флаги & бит]
    пф = у.поле("Флаги", "diameter.flags", f"0x{флаги:02x} ({', '.join(имена) or 'нет'})", м + 4, 1, флаги)
    for подпись, ключ, бит in (("Запрос (R)", "diameter.flags.request", 7), ("Пересылаемый (P)",
                               "diameter.flags.proxyable", 6), ("Ошибка (E)", "diameter.flags.error", 5),
                              ("Повтор (T)", "diameter.flags.t", 4)):
        у.поле(подпись, ключ, флаги >> бит & 1, м + 4, 1, родитель=пф)
    у.поле("Код команды", "diameter.cmd.code", f"{код} {полное}" + (f" ({сокр})" if код in DIAMETER_КОМАНДЫ else ""),
           м + 5, 3, код)
    прил = u32(д, м + 8)
    у.поле("Application-ID", "diameter.applicationid",
           f"{прил} ({DIAMETER_ПРИЛОЖЕНИЯ.get(прил, 'неизвестное')})", м + 8, 4, прил)
    у.поле("Hop-by-Hop", "diameter.hopbyhopid", f"0x{u32(д, м + 12):08x}", м + 12, 4, u32(д, м + 12))
    у.поле("End-to-End", "diameter.endtoendid", f"0x{u32(д, м + 16):08x}", м + 16, 4, u32(д, м + 16))
    есть = min(конец, м + длина)
    сводка: Dict[str, str] = {}
    _avp_дерево(р, у, м + 20, есть, None, 0, сводка)
    у.длина = есть - м
    итог = сокр
    if "Result-Code" in сводка:
        итог += f", Result-Code {сводка['Result-Code']}"
    elif флаги & 0x20:
        итог += ", ошибка (E)"
    if "Session-Id" in сводка:
        итог += f", сеанс {сводка['Session-Id']}"
    у.итог = итог
    if есть < м + длина:
        _оборван(р, у, "Diameter", длина, есть - м)
    return есть, у.итог


@_свой
def diameter(р: Разбор, м: int, конец: int) -> bool:
    """Diameter (RFC 6733): сообщения подряд — на TCP их бывает несколько в сегменте."""
    return _по_очереди(р, м, конец, _diameter_одно, "Diameter")


@_свой
def diameter_эвристика(р: Разбор, м: int, конец: int) -> bool:
    """Без порта — только целые сообщения с известным кодом команды и сошедшейся цепочкой AVP."""
    конец = min(конец, len(р.д))
    if _diameter_заголовок(р.д, м, конец) is None:
        return False
    return _по_очереди(р, м, конец, lambda р_, м_, к_: _diameter_одно(р_, м_, к_, только_известные=True),
                       "Diameter")


# ==============================================================================================
# GTPv1-C — 3GPP TS 29.060
# ==============================================================================================

GTP1_ТИПЫ = {1: "Echo Request", 2: "Echo Response", 3: "Version Not Supported",
             16: "Create PDP Context Request", 17: "Create PDP Context Response",
             18: "Update PDP Context Request", 19: "Update PDP Context Response",
             20: "Delete PDP Context Request", 21: "Delete PDP Context Response",
             31: "Supported Extension Headers Notification"}
#: TV-элементы (тип < 128): тип → (имя, длина значения). TS 29.060, 7.7 и таблица 37.
GTP1_TV: Dict[int, Tuple[str, int]] = {
    1: ("Cause", 1), 2: ("IMSI", 8), 3: ("Routeing Area Identity", 6), 4: ("TLLI", 4), 5: ("P-TMSI", 4),
    8: ("Reordering Required", 1), 9: ("Authentication Triplet", 28), 11: ("MAP Cause", 1),
    12: ("P-TMSI Signature", 3), 13: ("MS Validated", 1), 14: ("Recovery", 1), 15: ("Selection Mode", 1),
    16: ("TEID Data I", 4), 17: ("TEID Control Plane", 4), 18: ("TEID Data II", 5), 19: ("Teardown Ind", 1),
    20: ("NSAPI", 1), 21: ("RANAP Cause", 1), 22: ("RAB Context", 9), 23: ("Radio Priority SMS", 1),
    24: ("Radio Priority", 1), 25: ("Packet Flow Id", 2), 26: ("Charging Characteristics", 2),
    27: ("Trace Reference", 2), 28: ("Trace Type", 2), 29: ("MS Not Reachable Reason", 1),
    127: ("Charging ID", 4),
}
#: TLV-элементы (тип ≥ 128): TS 29.060, 7.7.
GTP1_TLV = {128: "End User Address", 131: "Access Point Name", 132: "Protocol Configuration Options",
            133: "GSN Address", 134: "MSISDN", 135: "Quality of Service Profile", 151: "RAT Type",
            152: "User Location Information", 153: "MS Time Zone", 154: "IMEI(SV)",
            251: "Charging Gateway Address", 255: "Private Extension"}
_GTP1_КЛЮЧИ = {1: "gtp.cause", 2: "gtp.imsi", 14: "gtp.recovery", 16: "gtp.teid_data", 17: "gtp.teid_cp",
               20: "gtp.nsapi", 127: "gtp.chrg_id", 128: "gtp.user_addr", 131: "gtp.apn", 133: "gtp.gsn_addr",
               134: "gtp.msisdn", 15: "gtp.sel_mode"}


def _gtp1_значение(тип: int, з: bytes) -> str:
    if тип == 1:
        return f"{з[0]}" + (" (Request accepted)" if з[0] == 128 else "")
    if тип == 2:
        return _tbcd(з)
    if тип == 3:
        return f"{_plmn(з, 0)}, LAC 0x{u16(з, 3):04x}, RAC 0x{з[5]:02x}"
    if тип in (14, 13, 11, 21, 29, 8, 19, 23, 24):
        return str(з[0])
    if тип == 15:
        return str(з[0] & 3)
    if тип == 20:
        return str(з[0] & 15)
    if тип in (16, 17, 4, 5, 127):
        return f"0x{u32(з, 0):08x}"
    if тип == 131:
        return _метки(з) or з.hex()
    if тип in (133, 251):
        return _ip(з)
    if тип == 134 and з:
        return _tbcd(з[1:])
    if тип == 128 and len(з) >= 2:
        вид = {0x21: "IPv4", 0x57: "IPv6", 0x8D: "IPv4v6"}.get(з[1], f"тип 0x{з[1]:02x}")
        return вид + (f" {_ip(з[2:])}" if len(з) in (6, 18) else "")
    if тип == 151 and len(з) == 1:
        return {1: "UTRAN", 2: "GERAN", 3: "WLAN", 4: "GAN", 5: "HSPA Evolution", 6: "EUTRAN"}.get(з[0], str(з[0]))
    return з.hex() or "(пусто)"


def _gtp1_ie(р: Разбор, у: Уровень, м: int, конец: int, оборван: bool) -> Optional[int]:
    """IE GTPv1 (TS 29.060, 7.7): TV — длина по типу, TLV — 2 байта длины. Незнакомый TV-тип —
    остановка (длину не угадываем). Возвращает, где кончили; None — не сошлось (не наше)."""
    д = р.д
    место = м
    while место < конец:
        тип = д[место]
        if тип < 128:
            if тип not in GTP1_TV:
                у.поле(f"Незнакомый TV-элемент {тип}: дальше не разбирается", "gtp.ie.unknown", тип, место,
                       конец - место)
                return конец
            имя, дл = GTP1_TV[тип]
            заг = 1
        else:
            if место + 3 > конец:
                return конец if оборван else None
            имя, дл, заг = GTP1_TLV.get(тип, f"IE {тип}"), u16(д, место + 1), 3
        if место + заг + дл > конец:
            if оборван:
                return конец
            return None
        з = д[место + заг:место + заг + дл]
        текст = _gtp1_значение(тип, з)
        п = у.поле(f"{имя}: {текст}", "gtp.ie.type", имя, место, заг + дл, тип)
        у.поле("Тип", "gtp.ie.type.value", тип, место, 1, родитель=п)
        if заг == 3:
            у.поле("Длина", "gtp.ie.len", дл, место + 1, 2, родитель=п)
        у.поле("Значение", _GTP1_КЛЮЧИ.get(тип, "gtp.ie.data"), текст, место + заг, дл,
               родитель=п)
        место += заг + дл
    return место


def _gtpv1_c(р: Разбор, м: int, конец: int) -> bool:
    """GTPv1-C (TS 29.060, 6): версия 1, PT=1, запасной бит 0, S=1 (в GTP-C номер обязателен)."""
    д = р.д
    if конец - м < 12:
        return False
    флаги, тип, длина, teid = д[м], д[м + 1], u16(д, м + 2), u32(д, м + 4)
    if флаги >> 5 != 1 or not флаги & 0x10 or флаги & 0x08 or not флаги & 0x02 or длина < 4:
        return False
    объявлено = 8 + длина
    оборван = объявлено > конец - м
    if оборван and (тип not in GTP1_ТИПЫ or not _захват_оборван(р, конец)) or \
            not оборван and объявлено != конец - м:
        return False
    есть = min(конец, м + объявлено)
    место = м + 12
    расширения = []
    следующий = д[м + 11] if флаги & 0x04 else 0
    while следующий:                                       # заголовки расширения: длина в 4 байтах
        if место >= есть:
            if оборван:
                break
            return False
        дл = д[место] * 4
        if дл == 0 or место + дл > есть:
            if оборван:
                break
            return False
        расширения.append((следующий, место, дл))
        следующий = д[место + дл - 1]
        место += дл
    у = р.уровень("GTP", "GPRS Tunnelling Protocol v1 (C)", м)
    пф = у.поле("Флаги", "gtp.flags", f"0x{флаги:02x}", м, 1, флаги)
    у.поле("Версия", "gtp.flags.version", 1, м, 1, родитель=пф)
    у.поле("Тип протокола (PT)", "gtp.flags.payload", 1, м, 1, родитель=пф)
    у.поле("Расширение (E)", "gtp.flags.e", флаги >> 2 & 1, м, 1, родитель=пф)
    у.поле("Номер (S)", "gtp.flags.s", флаги >> 1 & 1, м, 1, родитель=пф)
    у.поле("N-PDU (PN)", "gtp.flags.pn", флаги & 1, м, 1, родитель=пф)
    имя = GTP1_ТИПЫ.get(тип, f"тип {тип}")
    у.поле("Тип сообщения", "gtp.message", имя, м + 1, 1, тип)
    у.поле("Длина", "gtp.length", длина, м + 2, 2)
    у.поле("TEID", "gtp.teid", f"0x{teid:08x}", м + 4, 4, teid)
    у.поле("Номер", "gtp.seq_number", u16(д, м + 8), м + 8, 2)
    у.поле("Номер N-PDU", "gtp.npdu_number", д[м + 10], м + 10, 1)
    у.поле("Следующее расширение", "gtp.next", д[м + 11], м + 11, 1)
    for вид, место_р, дл in расширения:
        у.поле(f"Заголовок расширения 0x{вид:02x}", "gtp.ext_hdr", д[место_р:место_р + дл].hex(), место_р, дл, вид)
    if _gtp1_ie(р, у, место, есть, оборван) is None:     # IE ровно до конца сообщения
        return False
    у.длина = есть - м
    у.итог = f"{имя}, TEID 0x{teid:08x}, номер {u16(д, м + 8)}"
    if оборван:
        _оборван(р, у, "GTPv1-C", объявлено, есть - м)
    р.п.инфо = "GTP " + у.итог
    return True


# ==============================================================================================
# GTPv2-C — 3GPP TS 29.274
# ==============================================================================================

GTP2_ТИПЫ = {1: "Echo Request", 2: "Echo Response", 3: "Version Not Supported Indication",
             32: "Create Session Request", 33: "Create Session Response", 34: "Modify Bearer Request",
             35: "Modify Bearer Response", 36: "Delete Session Request", 37: "Delete Session Response",
             95: "Create Bearer Request", 96: "Create Bearer Response", 97: "Update Bearer Request",
             98: "Update Bearer Response", 99: "Delete Bearer Request", 100: "Delete Bearer Response",
             170: "Release Access Bearers Request", 171: "Release Access Bearers Response",
             176: "Downlink Data Notification", 177: "Downlink Data Notification Acknowledge"}
#: Типы IE (TS 29.274, 8.1, таблица 8.1-1).
GTP2_IE = {1: "IMSI", 2: "Cause", 3: "Recovery", 71: "APN", 72: "AMBR", 73: "EBI", 74: "IP Address",
           75: "MEI", 76: "MSISDN", 77: "Indication", 78: "PCO", 79: "PAA", 80: "Bearer QoS", 82: "RAT Type",
           83: "Serving Network", 84: "Bearer TFT", 86: "ULI", 87: "F-TEID", 93: "Bearer Context",
           94: "Charging ID", 95: "Charging Characteristics", 99: "PDN Type", 109: "PDN Connection",
           114: "UE Time Zone", 127: "APN Restriction", 128: "Selection Mode", 255: "Private Extension"}
GTP2_СГРУППИРОВАННЫЕ = {93, 109}
GTP2_ПРИЧИНЫ = {16: "Request accepted", 17: "Request accepted partially", 64: "Context Not Found",
                72: "System failure", 73: "No resources available"}
GTP2_ИНТЕРФЕЙСЫ = {0: "S1-U eNodeB GTP-U", 1: "S1-U SGW GTP-U", 4: "S5/S8 SGW GTP-U", 5: "S5/S8 PGW GTP-U",
                   6: "S5/S8 SGW GTP-C", 7: "S5/S8 PGW GTP-C", 10: "S11 MME GTP-C", 11: "S11/S4 SGW GTP-C"}
_GTP2_КЛЮЧИ = {1: "gtpv2.imsi", 2: "gtpv2.cause", 3: "gtpv2.recovery", 71: "gtpv2.apn", 73: "gtpv2.ebi",
               74: "gtpv2.ip_address", 75: "gtpv2.mei", 76: "gtpv2.msisdn", 82: "gtpv2.rat_type",
               83: "gtpv2.serving_network", 87: "gtpv2.f_teid", 94: "gtpv2.charging_id",
               99: "gtpv2.pdn_type", 79: "gtpv2.paa", 128: "gtpv2.selec_mode"}


def _gtp2_значение(тип: int, з: bytes):
    """(текст, сырое) значения IE GTPv2 (TS 29.274, 8.3–8.)."""
    if тип in (1, 75, 76):
        return _tbcd(з), _tbcd(з)
    if тип == 2 and з:
        return f"{з[0]} ({GTP2_ПРИЧИНЫ.get(з[0], 'неизвестная')})", з[0]
    if тип == 3 and len(з) == 1:
        return str(з[0]), з[0]
    if тип == 71:
        return (_метки(з) or з.hex()), (_метки(з) or з.hex())
    if тип == 73 and len(з) == 1:
        return str(з[0] & 15), з[0] & 15
    if тип == 74:
        return _ip(з), _ip(з)
    if тип == 82 and len(з) == 1:
        return {1: "UTRAN", 2: "GERAN", 6: "EUTRAN"}.get(з[0], str(з[0])), з[0]
    if тип == 83 and len(з) == 3:
        return _plmn(з, 0), _plmn(з, 0)
    if тип == 94 and len(з) == 4:
        return str(u32(з, 0)), u32(з, 0)
    if тип == 99 and len(з) == 1:
        return {1: "IPv4", 2: "IPv6", 3: "IPv4v6"}.get(з[0] & 7, str(з[0] & 7)), з[0] & 7
    if тип == 128 and len(з) == 1:
        return str(з[0] & 3), з[0] & 3
    if тип == 72 and len(з) == 8:
        return f"вверх {u32(з, 0)} кбит/с, вниз {u32(з, 4)} кбит/с", u32(з, 0)
    if тип == 80 and len(з) >= 2:
        return f"QCI {з[1]}", з[1]
    if тип == 79 and з:
        вид = з[0] & 7
        if вид == 1 and len(з) == 5:
            return f"IPv4 {_ip(з[1:5])}", _ip(з[1:5])
        if вид == 2 and len(з) == 18:
            return f"IPv6 {_ip(з[2:18])}/{з[1]}", _ip(з[2:18])
        if вид == 3 and len(з) == 22:
            return f"IPv6 {_ip(з[2:18])}/{з[1]}, IPv4 {_ip(з[18:22])}", _ip(з[18:22])
    if тип == 87 and len(з) >= 5:
        v4, v6, интерфейс = з[0] >> 7, з[0] >> 6 & 1, з[0] & 0x3F
        if len(з) == 5 + 4 * v4 + 16 * v6:
            части = [f"{GTP2_ИНТЕРФЕЙСЫ.get(интерфейс, f'интерфейс {интерфейс}')}", f"TEID 0x{u32(з, 1):08x}"]
            if v4:
                части.append(_ip(з[5:9]))
            if v6:
                части.append(_ip(з[5 + 4 * v4:21 + 4 * v4]))
            return ", ".join(части), u32(з, 1)
    return (з.hex() or "(пусто)"), з.hex()


def _gtp2_ie(р: Разбор, у: Уровень, м: int, конец: int, родитель: Optional[Поле], глубина: int,
             оборван: bool, сводка: Dict[int, str]) -> Optional[int]:
    """IE GTPv2 (TS 29.274, 8.2.1): тип, длина значения, запасные 4 бита и instance."""
    д = р.д
    место = м
    while место < конец:
        if место + 4 > конец:
            return конец if оборван else None
        тип, дл, инст = д[место], u16(д, место + 1), д[место + 3]
        if место + 4 + дл > конец:
            return конец if оборван else None
        имя = GTP2_IE.get(тип, f"IE {тип}")
        з = д[место + 4:место + 4 + дл]
        сгруппирован = тип in GTP2_СГРУППИРОВАННЫЕ and глубина < 8
        текст, сырое = ("", None) if сгруппирован else _gtp2_значение(тип, з)
        п = у.поле(f"{имя}" + (f": {текст}" if текст else "") + (f" [{инст & 15}]" if инст & 15 else ""),
                   "gtpv2.ie_type", имя, место, 4 + дл, тип, родитель=родитель)
        у.поле("Тип", "gtpv2.ie_type.value", тип, место, 1, родитель=п)
        у.поле("Длина", "gtpv2.ie_len", дл, место + 1, 2, родитель=п)
        у.поле("Instance", "gtpv2.instance", инст & 15, место + 3, 1, родитель=п)
        if сгруппирован:
            if _gtp2_ie(р, у, место + 4, место + 4 + дл, п, глубина + 1, False, {}) is None:
                п.плохо = True
                у.поле("Содержимое (не сошлось)", "gtpv2.ie.data", з.hex(), место + 4, дл, родитель=п)
        else:
            у.поле("Значение", _GTP2_КЛЮЧИ.get(тип, "gtpv2.ie.data"), текст, место + 4, дл, сырое, родитель=п)
            сводка.setdefault(тип, текст)
        место += 4 + дл
    return место


def _gtpv2_одно(р: Разбор, м: int, конец: int, первое: bool):
    """Одно сообщение GTPv2-C (TS 29.274, 5.1). → (конец, итог, пиггибэк) или None."""
    д = р.д
    if конец - м < 8:
        return None
    флаги, тип, длина = д[м], д[м + 1], u16(д, м + 2)
    p, t, mp = флаги >> 4 & 1, флаги >> 3 & 1, флаги >> 2 & 1
    if флаги >> 5 != 2 or флаги & 0x03:
        return None
    заг = 12 if t else 8
    объявлено = 4 + длина
    if объявлено < заг or (тип in (1, 2, 3) and t):         # 5.5.1: Echo и VNS — без TEID
        return None
    оборван = м + объявлено > конец
    if оборван and (тип not in GTP2_ТИПЫ or not _захват_оборван(р, конец)):
        return None
    if конец - м < заг:
        return None
    есть = min(конец, м + объявлено)
    сводка: Dict[int, str] = {}
    у = р.уровень("GTPv2", "GPRS Tunnelling Protocol v2 (C)", м)
    пф = у.поле("Флаги", "gtpv2.flags", f"0x{флаги:02x}", м, 1, флаги)
    у.поле("Версия", "gtpv2.version", 2, м, 1, родитель=пф)
    у.поле("Пиггибэк (P)", "gtpv2.p", p, м, 1, родитель=пф)
    у.поле("TEID есть (T)", "gtpv2.t", t, м, 1, родитель=пф)
    у.поле("Приоритет (MP)", "gtpv2.mp", mp, м, 1, родитель=пф)
    имя = GTP2_ТИПЫ.get(тип, f"тип {тип}")
    у.поле("Тип сообщения", "gtpv2.message_type", имя, м + 1, 1, тип)
    у.поле("Длина", "gtpv2.msg_length", длина, м + 2, 2)
    место = м + 4
    teid = None
    if t:
        teid = u32(д, место)
        у.поле("TEID", "gtpv2.teid", f"0x{teid:08x}", место, 4, teid)
        место += 4
    номер = u24(д, место)
    у.поле("Номер", "gtpv2.seq", номер, место, 3)
    if mp:
        у.поле("Приоритет сообщения", "gtpv2.message_priority", д[место + 3] >> 4, место + 3, 1)
    конец_ie = _gtp2_ie(р, у, м + заг, есть, None, 0, оборван, сводка)
    if конец_ie is None or (not оборван and конец_ie != есть):
        del р.п.уровни[р.п.уровни.index(у):]
        return None
    у.длина = есть - м
    итог = имя + (f", TEID 0x{teid:08x}" if teid is not None else "") + f", номер {номер}"
    if 1 in сводка:
        итог += f", IMSI {сводка[1]}"
    if 2 in сводка:
        итог += f", причина {сводка[2]}"
    у.итог = итог
    if оборван:
        _оборван(р, у, "GTPv2-C", объявлено, есть - м)
    return есть, у.итог, p


def _gtpv2_c(р: Разбор, м: int, конец: int) -> bool:
    итоги = []
    место, p = м, 1
    while p and место < конец and len(итоги) < 8:
        вышло = _gtpv2_одно(р, место, конец, not итоги)
        if вышло is None:
            break
        место, итог, p = вышло
        итоги.append(итог)
    if not итоги or место < конец:          # после сообщения без P (или за P — не GTPv2) — ничего
        return False
    р.п.инфо = "GTPv2 " + "; ".join(итоги)
    return True


@_свой
def gtp_c(р: Разбор, м: int, конец: int) -> bool:
    """UDP 2123: GTPv1-C или GTPv2-C — по полю версии (TS 29.060, 6; TS 29.274, 5.1)."""
    конец = min(конец, len(р.д))
    if конец - м < 8:
        return False
    версия = р.д[м] >> 5
    if версия == 1:
        return _gtpv1_c(р, м, конец)
    if версия == 2:
        return _gtpv2_c(р, м, конец)
    return False


# ==============================================================================================
# GTP' — 3GPP TS 32.295
# ==============================================================================================

GTPP_ТИПЫ = {1: "Echo Request", 2: "Echo Response", 3: "Version Not Supported", 4: "Node Alive Request",
             5: "Node Alive Response", 6: "Redirection Request", 7: "Redirection Response",
             240: "Data Record Transfer Request", 241: "Data Record Transfer Response"}
GTPP_TV = {1: ("Cause", 1), 14: ("Recovery", 1), 126: ("Packet Transfer Command", 1)}
GTPP_КОМАНДЫ = {1: "Send Data Record Packet", 2: "Send possibly duplicated Data Record Packet",
                3: "Cancel Data Record Packet", 4: "Release Data Record Packet"}


@_свой
def gtp_prime(р: Разбор, м: int, конец: int) -> bool:
    """GTP' (TS 32.295, 6.1): версия 0–2, PT=0, запасные биты «111», тип заголовка: 1 — 6 байт,
    0 — 20 байт (только версия 0). Длина — нагрузка после заголовка, сходится с датаграммой."""
    д = р.д
    конец = min(конец, len(р.д))
    if конец - м < 6:
        return False
    флаги, тип, длина, номер = д[м], д[м + 1], u16(д, м + 2), u16(д, м + 4)
    версия, pt, запас, короткий = флаги >> 5, флаги >> 4 & 1, флаги >> 1 & 7, флаги & 1
    if версия > 2 or pt or запас != 7 or тип not in GTPP_ТИПЫ or (not короткий and версия != 0):
        return False
    заг = 6 if короткий else 20
    if конец - м < заг or заг + длина != конец - м:
        return False
    у = р.уровень("GTP'", "GTP' (GTP prime, TS 32.295)", м)
    _скрытое(у, "gtpprime")
    пф = у.поле("Флаги", "gtpprime.flags", f"0x{флаги:02x}", м, 1, флаги)
    у.поле("Версия", "gtpprime.flags.version", версия, м, 1, родитель=пф)
    у.поле("Тип протокола (PT)", "gtpprime.flags.pt", 0, м, 1, родитель=пф)
    у.поле("Тип заголовка", "gtpprime.flags.hdr_length", "6 байт" if короткий else "20 байт", м, 1, короткий,
           родитель=пф)
    у.поле("Тип сообщения", "gtpprime.message", GTPP_ТИПЫ[тип], м + 1, 1, тип)
    у.поле("Длина", "gtpprime.length", длина, м + 2, 2)
    у.поле("Номер", "gtpprime.seq_number", номер, м + 4, 2)
    if not короткий:
        у.поле("Запасные октеты заголовка", "gtpprime.spare", д[м + 6:м + 20].hex(), м + 6, 14)
    место, конец_ie = м + заг, конец
    while место < конец_ie:
        вид = д[место]
        if вид < 128:
            if вид not in GTPP_TV:
                у.поле(f"Незнакомый TV-элемент {вид}: дальше не разбирается", "gtpprime.ie.unknown", вид, место,
                       конец_ie - место)
                break
            имя, дл = GTPP_TV[вид]
            заг_ie = 1
        else:
            if место + 3 > конец_ie:
                return False
            имя, дл, заг_ie = {252: "Data Record Packet", 255: "Private Extension"}.get(вид, f"IE {вид}"), \
                u16(д, место + 1), 3
        if место + заг_ie + дл > конец_ie:
            return False
        з = д[место + заг_ie:место + заг_ie + дл]
        if вид == 126:
            текст = GTPP_КОМАНДЫ.get(з[0], str(з[0]))
        elif вид in (1, 14):
            текст = str(з[0])
        else:
            текст = f"{дл} байт"
        п = у.поле(f"{имя}: {текст}", "gtpprime.ie_type", имя, место, заг_ie + дл, вид)
        у.поле("Значение", {1: "gtpprime.cause", 14: "gtpprime.recovery", 126: "gtpprime.ptc"}.get(
            вид, "gtpprime.ie.data"), текст, место + заг_ie, дл, з[0] if вид in GTPP_TV else з.hex(), родитель=п)
        место += заг_ie + дл
    у.длина = конец - м
    у.итог = f"{GTPP_ТИПЫ[тип]}, версия {версия}, номер {номер}"
    р.п.инфо = "GTP' " + у.итог
    return True


# ==============================================================================================
# PFCP — 3GPP TS 29.244
# ==============================================================================================

PFCP_УЗЛОВЫЕ = {1: "Heartbeat Request", 2: "Heartbeat Response", 3: "PFD Management Request",
                4: "PFD Management Response", 5: "Association Setup Request", 6: "Association Setup Response",
                7: "Association Update Request", 8: "Association Update Response",
                9: "Association Release Request", 10: "Association Release Response",
                11: "Version Not Supported Response", 12: "Node Report Request", 13: "Node Report Response",
                14: "Session Set Deletion Request", 15: "Session Set Deletion Response"}
PFCP_СЕАНСОВЫЕ = {50: "Session Establishment Request", 51: "Session Establishment Response",
                  52: "Session Modification Request", 53: "Session Modification Response",
                  54: "Session Deletion Request", 55: "Session Deletion Response",
                  56: "Session Report Request", 57: "Session Report Response"}
PFCP_ТИПЫ = {**PFCP_УЗЛОВЫЕ, **PFCP_СЕАНСОВЫЕ}
#: Типы IE (TS 29.244, 8.1.2, таблица 8.1.2-1); 1–11 и 13–18 — сгруппированные.
PFCP_IE = {1: "Create PDR", 2: "PDI", 3: "Create FAR", 4: "Forwarding Parameters", 5: "Duplicating Parameters",
           6: "Create URR", 7: "Create QER", 8: "Created PDR", 9: "Update PDR", 10: "Update FAR",
           11: "Update Forwarding Parameters", 13: "Update URR", 14: "Update QER", 15: "Remove PDR",
           16: "Remove FAR", 17: "Remove URR", 18: "Remove QER", 19: "Cause", 20: "Source Interface",
           21: "F-TEID", 22: "Network Instance", 29: "Precedence", 42: "Destination Interface",
           44: "Apply Action", 56: "PDR ID", 57: "F-SEID", 60: "Node ID", 81: "URR ID",
           84: "Outer Header Creation", 93: "UE IP Address", 95: "Outer Header Removal",
           96: "Recovery Time Stamp", 108: "FAR ID", 109: "QER ID"}
PFCP_СГРУППИРОВАННЫЕ = set(range(1, 12)) | set(range(13, 19))
PFCP_ПРИЧИНЫ = {1: "Request accepted", 64: "Request rejected", 65: "Session context not found",
                66: "Mandatory IE missing"}
PFCP_ИНТЕРФЕЙСЫ = {0: "Access", 1: "Core", 2: "SGi-LAN/N6-LAN", 3: "CP-function"}
_PFCP_КЛЮЧИ = {19: "pfcp.cause", 20: "pfcp.source_interface", 21: "pfcp.f_teid", 22: "pfcp.network_instance",
               29: "pfcp.precedence", 42: "pfcp.dst_interface", 44: "pfcp.apply_action", 56: "pfcp.pdr_id",
               57: "pfcp.f_seid", 60: "pfcp.node_id", 81: "pfcp.urr_id", 93: "pfcp.ue_ip_address",
               96: "pfcp.recovery_time_stamp", 108: "pfcp.far_id", 109: "pfcp.qer_id"}


def _pfcp_значение(тип: int, з: bytes):
    if тип == 19 and len(з) == 1:
        return f"{з[0]} ({PFCP_ПРИЧИНЫ.get(з[0], 'неизвестная')})", з[0]
    if тип in (20, 42) and len(з) >= 1:
        return PFCP_ИНТЕРФЕЙСЫ.get(з[0] & 15, str(з[0] & 15)), з[0] & 15
    if тип == 56 and len(з) == 2:
        return str(u16(з, 0)), u16(з, 0)
    if тип in (29, 81, 108, 109) and len(з) == 4:
        return str(u32(з, 0)), u32(з, 0)
    if тип == 96 and len(з) == 4:
        return _ntp_секунды(u32(з, 0)), u32(з, 0)
    if тип == 60 and з:
        вид, тело = з[0] & 15, з[1:]
        if вид == 0 and len(тело) == 4 or вид == 1 and len(тело) == 16:
            return _ip(тело), _ip(тело)
        if вид == 2:
            имя = _метки(тело) or _текст(тело)
            return имя, имя
    if тип == 57 and len(з) >= 9:                           # 8.2.37: бит 2 — V4, бит 1 — V6
        v4, v6 = з[0] >> 1 & 1, з[0] & 1
        if len(з) == 9 + 4 * v4 + 16 * v6:
            части = [f"SEID 0x{u64(з, 1):016x}"] + ([_ip(з[9:13])] if v4 else []) + \
                ([_ip(з[9 + 4 * v4:25 + 4 * v4])] if v6 else [])
            return ", ".join(части), u64(з, 1)
    if тип == 21 and len(з) >= 1:                           # 8.2.3: бит 1 — V4, бит 2 — V6, бит 3 — CH
        v4, v6, ch = з[0] & 1, з[0] >> 1 & 1, з[0] >> 2 & 1
        if ch:
            return "CHOOSE (выбирает UP)", "choose"
        if len(з) == 5 + 4 * v4 + 16 * v6:
            части = [f"TEID 0x{u32(з, 1):08x}"] + ([_ip(з[5:9])] if v4 else []) + \
                ([_ip(з[5 + 4 * v4:21 + 4 * v4])] if v6 else [])
            return ", ".join(части), u32(з, 1)
    if тип == 44 and з:
        флаги = [и for и, б in (("DROP", 1), ("FORW", 2), ("BUFF", 4), ("NOCP", 8), ("DUPL", 16)) if з[0] & б]
        return ", ".join(флаги) or "нет", з[0]
    if тип == 22:
        имя = _метки(з) or _текст(з)
        return имя, имя
    return (з.hex() or "(пусто)"), з.hex()


def _pfcp_ie(р: Разбор, у: Уровень, м: int, конец: int, родитель: Optional[Поле], глубина: int,
             оборван: bool, сводка: Dict[int, str]) -> Optional[int]:
    """IE PFCP (TS 29.244, 8.1.1): тип (2), длина (2); тип ≥ 32768 — с Enterprise ID (2) в длине."""
    д = р.д
    место = м
    while место < конец:
        if место + 4 > конец:
            return конец if оборван else None
        тип, дл = u16(д, место), u16(д, место + 2)
        if место + 4 + дл > конец:
            return конец if оборван else None
        вендорский = тип & 0x8000
        if вендорский and дл < 2:
            return None
        имя = PFCP_IE.get(тип, f"IE {тип}")
        начало = место + 4 + (2 if вендорский else 0)
        з = д[начало:место + 4 + дл]
        сгруппирован = тип in PFCP_СГРУППИРОВАННЫЕ and глубина < 8
        текст, сырое = ("", None) if сгруппирован else _pfcp_значение(тип, з)
        п = у.поле(имя + (f": {текст}" if текст else ""), "pfcp.ie_type", имя, место, 4 + дл, тип,
                   родитель=родитель)
        у.поле("Тип", "pfcp.ie_type.value", тип, место, 2, родитель=п)
        у.поле("Длина", "pfcp.ie_len", дл, место + 2, 2, родитель=п)
        if вендорский:
            у.поле("Enterprise ID", "pfcp.enterprise_id", u16(д, место + 4), место + 4, 2, родитель=п)
        if сгруппирован:
            if _pfcp_ie(р, у, начало, место + 4 + дл, п, глубина + 1, False, {}) is None:
                п.плохо = True
                у.поле("Содержимое (не сошлось)", "pfcp.ie.data", з.hex(), начало, len(з), родитель=п)
        else:
            у.поле("Значение", _PFCP_КЛЮЧИ.get(тип, "pfcp.ie.data"), текст, начало, len(з), сырое, родитель=п)
            сводка.setdefault(тип, текст)
        место += 4 + дл
    return место


def _pfcp_одно(р: Разбор, м: int, конец: int):
    """Одно сообщение PFCP (TS 29.244, 7.2.2). → (конец, итог, FO) или None."""
    д = р.д
    if конец - м < 8:
        return None
    флаги, тип, длина = д[м], д[м + 1], u16(д, м + 2)
    fo, mp, s = флаги >> 2 & 1, флаги >> 1 & 1, флаги & 1
    if флаги >> 5 != 1 or флаги & 0x18:
        return None
    if тип in PFCP_УЗЛОВЫЕ and s or тип in PFCP_СЕАНСОВЫЕ and not s:   # 7.2.2.1: S — у сеансовых
        return None
    заг = 16 if s else 8
    объявлено = 4 + длина
    if объявлено < заг or конец - м < заг:
        return None
    оборван = м + объявлено > конец
    if оборван and (тип not in PFCP_ТИПЫ or not _захват_оборван(р, конец)):
        return None
    есть = min(конец, м + объявлено)
    у = р.уровень("PFCP", "Packet Forwarding Control Protocol", м)
    пф = у.поле("Флаги", "pfcp.flags", f"0x{флаги:02x}", м, 1, флаги)
    у.поле("Версия", "pfcp.version", 1, м, 1, родитель=пф)
    у.поле("Продолжение (FO)", "pfcp.fo_flag", fo, м, 1, родитель=пф)
    у.поле("Приоритет (MP)", "pfcp.mp_flag", mp, м, 1, родитель=пф)
    у.поле("SEID есть (S)", "pfcp.s", s, м, 1, родитель=пф)
    имя = PFCP_ТИПЫ.get(тип, f"тип {тип}")
    у.поле("Тип сообщения", "pfcp.msg_type", имя, м + 1, 1, тип)
    у.поле("Длина", "pfcp.length", длина, м + 2, 2)
    место = м + 4
    seid = None
    if s:
        seid = u64(д, место)
        у.поле("SEID", "pfcp.seid", f"0x{seid:016x}", место, 8, seid)
        место += 8
    номер = u24(д, место)
    у.поле("Номер", "pfcp.seqno", номер, место, 3)
    if mp:
        у.поле("Приоритет сообщения", "pfcp.mp", д[место + 3] >> 4, место + 3, 1)
    сводка: Dict[int, str] = {}
    конец_ie = _pfcp_ie(р, у, м + заг, есть, None, 0, оборван, сводка)
    if конец_ie is None or (not оборван and конец_ie != есть):
        del р.п.уровни[р.п.уровни.index(у):]
        return None
    у.длина = есть - м
    итог = имя + (f", SEID 0x{seid:x}" if seid is not None else "") + f", номер {номер}"
    if 19 in сводка:
        итог += f", причина {сводка[19]}"
    if 60 in сводка:
        итог += f", узел {сводка[60]}"
    у.итог = итог
    if оборван:
        _оборван(р, у, "PFCP", объявлено, есть - м)
    return есть, у.итог, fo


@_свой
def pfcp(р: Разбор, м: int, конец: int) -> bool:
    конец = min(конец, len(р.д))
    итоги = []
    место, fo = м, 1
    while fo and место < конец and len(итоги) < 8:
        вышло = _pfcp_одно(р, место, конец)
        if вышло is None:
            break
        место, итог, fo = вышло
        итоги.append(итог)
    if not итоги or место < конец:          # после сообщения без FO (или за FO — не PFCP) — ничего
        return False
    р.п.инфо = "PFCP " + "; ".join(итоги)
    return True


# ==============================================================================================
# S1AP / NGAP / X2AP — верхний уровень APER (ITU-T X.691)
# ==============================================================================================

S1AP_ПРОЦЕДУРЫ = {0: "HandoverPreparation", 1: "HandoverResourceAllocation", 2: "HandoverNotification",
                  3: "PathSwitchRequest", 4: "HandoverCancel", 5: "E-RABSetup", 6: "E-RABModify",
                  7: "E-RABRelease", 8: "E-RABReleaseIndication", 9: "InitialContextSetup", 10: "Paging",
                  11: "downlinkNASTransport", 12: "initialUEMessage", 13: "uplinkNASTransport", 14: "Reset",
                  15: "ErrorIndication", 16: "NASNonDeliveryIndication", 17: "S1Setup",
                  18: "UEContextReleaseRequest", 21: "UEContextModification",
                  22: "UECapabilityInfoIndication", 23: "UEContextRelease", 29: "ENBConfigurationUpdate",
                  30: "MMEConfigurationUpdate"}
NGAP_ПРОЦЕДУРЫ = {4: "DownlinkNASTransport", 9: "ErrorIndication", 14: "InitialContextSetup",
                  15: "InitialUEMessage", 20: "NGReset", 21: "NGSetup", 24: "Paging", 25: "PathSwitchRequest",
                  28: "PDUSessionResourceRelease", 29: "PDUSessionResourceSetup", 41: "UEContextRelease",
                  42: "UEContextReleaseRequest", 46: "UplinkNASTransport"}
X2AP_ПРОЦЕДУРЫ = {0: "handoverPreparation", 1: "handoverCancel", 2: "loadIndication", 3: "errorIndication",
                  4: "snStatusTransfer", 5: "uEContextRelease", 6: "x2Setup", 7: "reset",
                  8: "eNBConfigurationUpdate"}
APER_ВИДЫ = {0: "initiatingMessage", 1: "successfulOutcome", 2: "unsuccessfulOutcome"}
APER_ВАЖНОСТЬ = {0: "reject", 1: "ignore", 2: "notify"}


def _aper_длина(д: bytes, м: int) -> Optional[Tuple[int, int]]:
    """Определитель длины X.691, 10.9.3.6–10.9.3.7: (длина, байт определителя); фрагменты — None."""
    б = д[м]
    if not б & 0x80:
        return б, 1
    if б & 0xC0 == 0x80:
        return ((б & 0x3F) << 8) | д[м + 1], 2
    return None


def _aper_ie(д: bytes, м: int, конец: int) -> Optional[List[Tuple[int, int, int, int]]]:
    """ProtocolIE-Container, если значение — SEQUENCE {protocolIEs, ...} (так устроены все
    сообщения S1AP/NGAP/X2AP): бит расширения и 7 бит выравнивания (0x00), число IE
    (0..65535, 2 байта), затем IE: id (2), criticality (2 бита + выравнивание), открытый тип.
    Возвращает [(место, id, важность, длина значения)] или None, если не сошлось в точности."""
    if конец - м < 3 or д[м] != 0:
        return None
    число, место, итог = u16(д, м + 1), м + 3, []
    for _ in range(число):
        if место + 4 > конец or д[место + 2] & 0x3F or д[место + 2] >> 6 > 2:
            return None
        дл = _aper_длина(д, место + 3)
        if дл is None:
            return None
        конец_ie = место + 3 + дл[1] + дл[0]
        if конец_ie > конец:
            return None
        итог.append((место, u16(д, место), д[место + 2] >> 6, дл[0]))
        место = конец_ie
    return итог if место == конец else None


def _aper_pdu(р: Разбор, м: int, конец: int, имя: str, полное: str, процедуры: Dict[int, str]) -> bool:
    """S1AP-PDU / NGAP-PDU / X2AP-PDU ::= CHOICE {initiatingMessage, successfulOutcome,
    unsuccessfulOutcome, ...}: бит расширения 0 и индекс 0–2 (байт 0x00/0x20/0x40), затем
    SEQUENCE {procedureCode INTEGER (0..255) — байт, criticality ENUMERATED (3) — 2 бита и
    выравнивание, value — открытый тип (определитель длины + байты)}. Длина PDU обязана
    совпасть с длиной куска DATA SCTP."""
    д = р.д
    есть = min(конец, len(д))
    if есть - м < 4:
        return False
    б0, код, б2 = д[м], д[м + 1], д[м + 2]
    if б0 not in (0x00, 0x20, 0x40) or б2 not in (0x00, 0x40, 0x80):
        return False
    дл = _aper_длина(д, м + 3)
    if дл is None:
        return False
    значение = м + 3 + дл[1]
    объявлено = значение + дл[0] - м
    if объявлено != конец - м:                                  # конец — объявленная длина куска DATA
        return False
    оборван = есть < конец
    if оборван and код not in процедуры:
        return False
    ключ = имя.lower()
    вид = APER_ВИДЫ[б0 >> 5]
    процедура = процедуры.get(код, f"процедура {код}")
    у = р.уровень(имя, полное, м)
    у.поле("Тип PDU", f"{ключ}.pdu_type", вид, м, 1, б0 >> 5)
    у.поле("Процедура (procedureCode)", f"{ключ}.procedurecode", f"{код} ({процедура})", м + 1, 1, код)
    у.поле("Важность (criticality)", f"{ключ}.criticality", APER_ВАЖНОСТЬ[б2 >> 6], м + 2, 1, б2 >> 6)
    у.поле("Длина значения", f"{ключ}.value_length", дл[0], м + 3, дл[1])
    пз = у.поле("Значение", f"{ключ}.value", f"{дл[0]} байт", значение, min(есть, значение + дл[0]) - значение)
    список = None if оборван else _aper_ie(д, значение, значение + дл[0])
    if список is not None:
        у.поле("Число IE (protocolIEs)", f"{ключ}.protocolies", len(список), значение + 1, 2, родитель=пз)
        for место, ид, важн, дл_ie in список:
            у.поле(f"IE id {ид}, {APER_ВАЖНОСТЬ[важн]}, {дл_ie} байт", f"{ключ}.id", ид, место,
                   3 + (1 if дл_ie < 128 else 2) + дл_ie, родитель=пз)
    у.длина = min(есть, м + объявлено) - м
    у.итог = f"{процедура} ({вид})"
    if оборван:
        _оборван(р, у, имя, объявлено, есть - м)
    р.п.инфо = f"{имя} {у.итог}"
    return True


@_свой
def s1ap(р: Разбор, м: int, конец: int) -> bool:
    return _aper_pdu(р, м, конец, "S1AP", "S1 Application Protocol (TS 36.413)", S1AP_ПРОЦЕДУРЫ)


@_свой
def ngap(р: Разбор, м: int, конец: int) -> bool:
    return _aper_pdu(р, м, конец, "NGAP", "NG Application Protocol (TS 38.413)", NGAP_ПРОЦЕДУРЫ)


@_свой
def x2ap(р: Разбор, м: int, конец: int) -> bool:
    return _aper_pdu(р, м, конец, "X2AP", "X2 Application Protocol (TS 36.423)", X2AP_ПРОЦЕДУРЫ)


# ==============================================================================================
# SGsAP — 3GPP TS 29.118
# ==============================================================================================

SGSAP_ТИПЫ = {0x01: "SGsAP-PAGING-REQUEST", 0x02: "SGsAP-PAGING-REJECT", 0x06: "SGsAP-SERVICE-REQUEST",
              0x07: "SGsAP-DOWNLINK-UNITDATA", 0x08: "SGsAP-UPLINK-UNITDATA",
              0x09: "SGsAP-LOCATION-UPDATE-REQUEST", 0x0A: "SGsAP-LOCATION-UPDATE-ACCEPT",
              0x0B: "SGsAP-LOCATION-UPDATE-REJECT", 0x0C: "SGsAP-TMSI-REALLOCATION-COMPLETE",
              0x0D: "SGsAP-ALERT-REQUEST", 0x0E: "SGsAP-ALERT-ACK", 0x0F: "SGsAP-ALERT-REJECT",
              0x11: "SGsAP-EPS-DETACH-INDICATION", 0x12: "SGsAP-EPS-DETACH-ACK",
              0x13: "SGsAP-IMSI-DETACH-INDICATION", 0x14: "SGsAP-IMSI-DETACH-ACK",
              0x15: "SGsAP-RESET-INDICATION", 0x16: "SGsAP-RESET-ACK", 0x17: "SGsAP-SERVICE-ABORT-REQUEST",
              0x18: "SGsAP-MO-CSFB-INDICATION"}
SGSAP_IE = {0x01: "IMSI", 0x02: "VLR name", 0x03: "TMSI", 0x04: "Location area identifier",
            0x08: "SGs cause", 0x09: "MME name", 0x0A: "EPS location update type", 0x0E: "Mobile identity",
            0x0F: "Reject cause", 0x16: "NAS message container", 0x20: "Service indicator",
            0x23: "Tracking Area Identity", 0x24: "E-UTRAN Cell Global Identity"}
_SGSAP_КЛЮЧИ = {0x01: "sgsap.imsi", 0x02: "sgsap.vlr_name", 0x03: "sgsap.tmsi", 0x04: "sgsap.lai",
                0x08: "sgsap.sgs_cause", 0x09: "sgsap.mme_name", 0x0A: "sgsap.eps_location_update_type",
                0x0E: "sgsap.mobile_identity", 0x0F: "sgsap.reject_cause", 0x16: "sgsap.nas_msg_container",
                0x20: "sgsap.service_indicator", 0x23: "sgsap.tai", 0x24: "sgsap.ecgi"}


def _sgsap_значение(iei: int, з: bytes):
    if iei == 0x01:
        тип, цифры = _личность(з)
        return цифры, цифры
    if iei in (0x02, 0x09):
        имя = _метки(з) or _текст(з)
        return имя, имя
    if iei == 0x03 and len(з) == 4:
        return f"0x{u32(з, 0):08x}", u32(з, 0)
    if iei == 0x04 and len(з) == 5:
        return f"{_plmn(з, 0)}, LAC 0x{u16(з, 3):04x}", u16(з, 3)
    if iei == 0x0A and len(з) == 1:
        return {1: "IMSI attach", 2: "Normal location update"}.get(з[0], str(з[0])), з[0]
    if iei == 0x20 and len(з) == 1:
        return {1: "CS call indicator", 2: "SMS indicator"}.get(з[0], str(з[0])), з[0]
    if iei in (0x08, 0x0F) and len(з) == 1:
        return str(з[0]), з[0]
    if iei == 0x0E and з:
        тип, значение = _личность(з)
        return f"{ {1: 'IMSI', 2: 'IMEI', 3: 'IMEISV', 4: 'TMSI'}.get(тип, f'тип {тип}')} {значение}", значение
    if iei == 0x23 and len(з) == 5:
        return f"{_plmn(з, 0)}, TAC 0x{u16(з, 3):04x}", u16(з, 3)
    if iei == 0x24 and len(з) == 7:
        return f"{_plmn(з, 0)}, ячейка 0x{u32(з, 3) & 0x0FFFFFFF:07x}", u32(з, 3) & 0x0FFFFFFF
    if iei == 0x16:
        return f"{len(з)} байт", з.hex()
    return (з.hex() or "(пусто)"), з.hex()


@_свой
def sgsap(р: Разбор, м: int, конец: int) -> bool:
    """SGsAP (TS 29.118): тип сообщения (9.2) и IE TLV — IEI, длина, значение (9.1). Длины в
    заголовке нет, поэтому проверяется всё остальное: тип известен, цепочка IE ровно
    заполняет кусок DATA, первым идёт IMSI (4–8 байт, тип личности 1) — во всех сообщениях,
    кроме RESET-INDICATION/ACK, где первым — MME name или VLR name (разд. 8)."""
    д = р.д
    есть = min(конец, len(д))
    оборван = есть < конец
    if есть - м < 3:
        return False
    тип = д[м]
    if тип not in SGSAP_ТИПЫ:
        return False
    первый, дл_первого = д[м + 1], д[м + 2]
    if тип in (0x15, 0x16):
        if первый not in (0x02, 0x09):
            return False
    elif первый != 0x01 or not 4 <= дл_первого <= 8 or м + 4 > есть or д[м + 3] & 7 != 1:
        return False
    у = р.уровень("SGsAP", "SGs Application Part (TS 29.118)", м)
    у.поле("Тип сообщения", "sgsap.msg_type", f"0x{тип:02x} ({SGSAP_ТИПЫ[тип]})", м, 1, тип)
    место, сводка = м + 1, {}
    while место < есть:
        if место + 2 > есть or место + 2 + д[место + 1] > есть:
            if оборван:
                break
            return False
        iei, дл = д[место], д[место + 1]
        з = д[место + 2:место + 2 + дл]
        имя = SGSAP_IE.get(iei, f"IE 0x{iei:02x}")
        текст, сырое = _sgsap_значение(iei, з)
        п = у.поле(f"{имя}: {текст}", "sgsap.iei", имя, место, 2 + дл, iei)
        у.поле("IEI", "sgsap.iei.value", f"0x{iei:02x}", место, 1, iei, родитель=п)
        у.поле("Длина", "sgsap.len", дл, место + 1, 1, родитель=п)
        у.поле("Значение", _SGSAP_КЛЮЧИ.get(iei, "sgsap.ie.data"), текст, место + 2, дл, сырое, родитель=п)
        сводка.setdefault(iei, текст)
        место += 2 + дл
    у.длина = место - м
    итог = SGSAP_ТИПЫ[тип]
    if 0x01 in сводка:
        итог += f", IMSI {сводка[0x01]}"
    for iei in (0x09, 0x02):
        if iei in сводка:
            итог += f", {SGSAP_IE[iei]} {сводка[iei]}"
    у.итог = итог
    if оборван:
        _оборван(р, у, "SGsAP", конец - м, есть - м)
    р.п.инфо = "SGsAP " + у.итог
    return True


# ==============================================================================================
# GSMTAP — osmocom, заголовок версии 2
# ==============================================================================================

GSMTAP_ТИПЫ = {0x01: "GSM Um", 0x02: "GSM Abis", 0x03: "GSM Um, пачка (burst)", 0x04: "SIM",
               0x08: "GPRS Gb LLC", 0x0C: "UMTS RRC", 0x0D: "LTE RRC", 0x0E: "LTE MAC"}
GSMTAP_КАНАЛЫ_UM = {1: "BCCH", 2: "CCCH", 3: "RACH", 4: "AGCH", 5: "PCH", 6: "SDCCH", 7: "SDCCH/4",
                    8: "SDCCH/8", 9: "TCH/F", 10: "TCH/H"}


@_свой
def gsmtap(р: Разбор, м: int, конец: int) -> bool:
    """GSMTAP v2 (gsmtap.h, struct gsmtap_hdr): версия 2, длина заголовка 4 слова (16 байт),
    тип из определённых (1–0x13), тайм-слот, ARFCN с флагами PCS (0x8000) и «вверх» (0x4000),
    уровень сигнала и SNR (со знаком), номер кадра, подтип, антенна, подслот, резерв (RFU —
    нулевой: иначе у заголовка без длины и суммы слишком мало признаков). Нагрузка — данными."""
    д = р.д
    конец = min(конец, len(д))
    if конец - м < 16 or д[м] != 2 or д[м + 1] != 4 or not 1 <= д[м + 2] <= 0x13 or д[м + 15]:
        return False
    тип, arfcn_поле = д[м + 2], u16(д, м + 4)
    arfcn, вверх, pcs = arfcn_поле & 0x3FFF, arfcn_поле >> 14 & 1, arfcn_поле >> 15
    сигнал, snr = struct.unpack_from(">bb", д, м + 6)
    подтип = д[м + 12]
    у = р.уровень("GSMTAP", "GSMTAP (osmocom)", м)
    у.поле("Версия", "gsmtap.version", 2, м, 1)
    у.поле("Длина заголовка", "gsmtap.hdr_len", "16 байт", м + 1, 1, 4)
    у.поле("Тип", "gsmtap.type", f"{тип} ({GSMTAP_ТИПЫ.get(тип, 'неизвестный')})", м + 2, 1, тип)
    у.поле("Тайм-слот", "gsmtap.ts", д[м + 3], м + 3, 1)
    па = у.поле("ARFCN", "gsmtap.arfcn", arfcn, м + 4, 2)
    у.поле("Направление вверх", "gsmtap.uplink", вверх, м + 4, 2, родитель=па)
    у.поле("Диапазон PCS", "gsmtap.pcs_band", pcs, м + 4, 2, родитель=па)
    у.поле("Уровень сигнала, дБм", "gsmtap.signal_dbm", сигнал, м + 6, 1)
    у.поле("SNR, дБ", "gsmtap.snr_db", snr, м + 7, 1)
    у.поле("Номер кадра", "gsmtap.frame_nr", u32(д, м + 8), м + 8, 4)
    if тип == 1:
        текст = GSMTAP_КАНАЛЫ_UM.get(подтип & 0x7F, str(подтип & 0x7F)) + (" (ACCH)" if подтип & 0x80 else "")
    else:
        текст = str(подтип)
    у.поле("Подтип (канал)", "gsmtap.sub_type", текст, м + 12, 1, подтип)
    у.поле("Антенна", "gsmtap.antenna", д[м + 13], м + 13, 1)
    у.поле("Подслот", "gsmtap.sub_slot", д[м + 14], м + 14, 1)
    у.поле("Резерв", "gsmtap.res", д[м + 15], м + 15, 1)
    у.длина = 16
    у.итог = (f"{GSMTAP_ТИПЫ.get(тип, f'тип {тип}')}, {текст}, ARFCN {arfcn}{' (вверх)' if вверх else ''}, "
              f"{сигнал} дБм, кадр {u32(д, м + 8)}")
    данные(р, м + 16, f"GSMTAP: нагрузка ({GSMTAP_ТИПЫ.get(тип, f'тип {тип}')})", конец)
    р.п.инфо = "GSMTAP " + у.итог
    return True


# ==============================================================================================
# TACACS+ — RFC 8907
# ==============================================================================================

TACACS_ТИПЫ = {1: "Authentication", 2: "Authorization", 3: "Accounting"}
TACACS_ДЕЙСТВИЯ = {1: "LOGIN", 2: "CHPASS", 4: "SENDAUTH"}
TACACS_АУТЕНТ = {1: "ASCII", 2: "PAP", 3: "CHAP", 5: "MSCHAP", 6: "MSCHAPV2"}
TACACS_СТАТУС_АУТЕНТ = {1: "PASS", 2: "FAIL", 3: "GETDATA", 4: "GETUSER", 5: "GETPASS", 6: "RESTART",
                        7: "ERROR", 0x21: "FOLLOW"}
TACACS_СТАТУС_АВТОР = {1: "PASS_ADD", 2: "PASS_REPL", 0x10: "FAIL", 0x11: "ERROR", 0x21: "FOLLOW"}
TACACS_СТАТУС_УЧЁТ = {1: "SUCCESS", 2: "ERROR", 0x21: "FOLLOW"}


def _tacacs_тело(р: Разбор, у: Уровень, м: int, дл: int, тип: int, seq: int, родитель: Поле) -> str:
    """Тело без шифрования (RFC 8907, 5.1–5.3, 6.1–6.2, 7.1–7.2); не сошлось — пометка, не ошибка."""
    д = р.д
    конец = м + дл

    def строки(место: int, пары) -> int:
        for подпись, ключ, длина in пары:
            if длина:
                у.поле(подпись, ключ, _текст(д[место:место + длина]), место, длина, родитель=родитель)
            место += длина
        return место

    запрос = seq % 2 == 1
    if тип == 1 and seq == 1 and дл >= 8:                   # START
        действие, привилегии, вид, служба, *длины = д[м:м + 8]
        if 8 + sum(длины) != дл:
            return ""
        у.поле("Действие", "tacplus.authen.action", TACACS_ДЕЙСТВИЯ.get(действие, действие), м, 1, действие,
               родитель=родитель)
        у.поле("Уровень привилегий", "tacplus.priv_lvl", привилегии, м + 1, 1, родитель=родитель)
        у.поле("Тип аутентификации", "tacplus.authen.type", TACACS_АУТЕНТ.get(вид, вид), м + 2, 1, вид,
               родитель=родитель)
        у.поле("Служба", "tacplus.authen.service", служба, м + 3, 1, родитель=родитель)
        строки(м + 8, (("Пользователь", "tacplus.user", длины[0]), ("Порт", "tacplus.port", длины[1]),
                       ("Удалённый адрес", "tacplus.remote_address", длины[2])))
        if длины[3]:
            у.поле("Данные", "tacplus.data", f"{длины[3]} байт", конец - длины[3], длины[3], родитель=родитель)
        пользователь = _текст(д[м + 8:м + 8 + длины[0]])
        return f"START {TACACS_ДЕЙСТВИЯ.get(действие, действие)}" + (f", «{пользователь}»" if пользователь else "")
    if тип == 1 and запрос and дл >= 5:                     # CONTINUE
        дл_с, дл_д = u16(д, м), u16(д, м + 2)
        if 5 + дл_с + дл_д != дл:
            return ""
        у.поле("Флаги", "tacplus.authen.continue.flags", f"0x{д[м + 4]:02x}", м + 4, 1, д[м + 4], родитель=родитель)
        if дл_с:
            у.поле("Сообщение пользователя", "tacplus.user_msg", f"{дл_с} байт", м + 5, дл_с, родитель=родитель)
        return "CONTINUE"
    if тип == 1 and дл >= 6:                                # REPLY
        статус, флаги, дл_с, дл_д = д[м], д[м + 1], u16(д, м + 2), u16(д, м + 4)
        if 6 + дл_с + дл_д != дл:
            return ""
        имя = TACACS_СТАТУС_АУТЕНТ.get(статус, str(статус))
        у.поле("Статус", "tacplus.status", имя, м, 1, статус, родитель=родитель)
        у.поле("Флаги", "tacplus.authen.reply.flags", f"0x{флаги:02x}", м + 1, 1, флаги, родитель=родитель)
        строки(м + 6, (("Сообщение сервера", "tacplus.server_msg", дл_с),))
        return f"REPLY {имя}"
    if тип in (2, 3) and запрос:                            # REQUEST авторизации/учёта
        сдвиг = 1 if тип == 3 else 0
        if дл < 8 + сдвиг:
            return ""
        заг = д[м + сдвиг:м + сдвиг + 8]
        метод, привилегии, вид, служба, дл_п, дл_порт, дл_адр, аргументов = заг
        длины_арг = list(д[м + сдвиг + 8:м + сдвиг + 8 + аргументов])
        if len(длины_арг) != аргументов or 8 + сдвиг + аргументов + дл_п + дл_порт + дл_адр + sum(длины_арг) != дл:
            return ""
        if тип == 3:
            у.поле("Флаги учёта", "tacplus.acct.flags",
                   ", ".join(и for и, б in (("START", 2), ("STOP", 4), ("WATCHDOG", 8)) if д[м] & б) or f"0x{д[м]:02x}",
                   м, 1, д[м], родитель=родитель)
        у.поле("Метод аутентификации", "tacplus.authen_method", метод, м + сдвиг, 1, родитель=родитель)
        у.поле("Уровень привилегий", "tacplus.priv_lvl", привилегии, м + сдвиг + 1, 1, родитель=родитель)
        у.поле("Тип аутентификации", "tacplus.authen.type", TACACS_АУТЕНТ.get(вид, вид), м + сдвиг + 2, 1, вид,
               родитель=родитель)
        место = строки(м + сдвиг + 8 + аргументов, (("Пользователь", "tacplus.user", дл_п),
                                                    ("Порт", "tacplus.port", дл_порт),
                                                    ("Удалённый адрес", "tacplus.remote_address", дл_адр)))
        аргументы = []
        for длина in длины_арг:
            аргументы.append(_текст(д[место:место + длина]))
            у.поле("Аргумент", "tacplus.arg", аргументы[-1], место, длина, родитель=родитель)
            место += длина
        пользователь = _текст(д[м + сдвиг + 8 + аргументов:м + сдвиг + 8 + аргументов + дл_п])
        return "REQUEST" + (f", «{пользователь}»" if пользователь else "") + \
            (f", {' '.join(аргументы[:3])}" if аргументы else "")
    if тип == 2 and дл >= 6:                                # REPLY авторизации
        статус, аргументов, дл_с, дл_д = д[м], д[м + 1], u16(д, м + 2), u16(д, м + 4)
        длины_арг = list(д[м + 6:м + 6 + аргументов])
        if len(длины_арг) != аргументов or 6 + аргументов + дл_с + дл_д + sum(длины_арг) != дл:
            return ""
        имя = TACACS_СТАТУС_АВТОР.get(статус, str(статус))
        у.поле("Статус", "tacplus.status", имя, м, 1, статус, родитель=родитель)
        строки(м + 6 + аргументов, (("Сообщение сервера", "tacplus.server_msg", дл_с),))
        return f"REPLY {имя}"
    if тип == 3 and дл >= 5:                                # REPLY учёта
        дл_с, дл_д, статус = u16(д, м), u16(д, м + 2), д[м + 4]
        if 5 + дл_с + дл_д != дл:
            return ""
        имя = TACACS_СТАТУС_УЧЁТ.get(статус, str(статус))
        у.поле("Статус", "tacplus.status", имя, м + 4, 1, статус, родитель=родитель)
        строки(м + 5, (("Сообщение сервера", "tacplus.server_msg", дл_с),))
        return f"REPLY {имя}"
    return ""


def _tacacs_одно(р: Разбор, м: int, конец: int):
    """Заголовок (RFC 8907, 4.1): major 0xC, minor 0/1, тип 1–3, seq ≥ 1, флаги, сеанс, длина."""
    д = р.д
    if конец - м < 12:
        return None
    версия, тип, seq, флаги, сеанс, дл = д[м], д[м + 1], д[м + 2], д[м + 3], u32(д, м + 4), u32(д, м + 8)
    if версия >> 4 != 0xC or версия & 15 > 1 or тип not in TACACS_ТИПЫ or seq == 0:
        return None
    объявлено = 12 + дл
    оборван = м + объявлено > конец
    if оборван and дл > 0x10000:                         # 4.1: рекомендуемый предел пакета 2^16
        return None
    есть = min(конец, м + объявлено)
    у = р.уровень("TACACS+", "TACACS+ (RFC 8907)", м)
    _скрытое(у, "tacplus")
    у.поле("Версия", "tacplus.majvers", f"{версия >> 4}.{версия & 15}", м, 1, версия)
    у.поле("Тип", "tacplus.type", TACACS_ТИПЫ[тип], м + 1, 1, тип)
    у.поле("Номер (seq_no)", "tacplus.seqno", seq, м + 2, 1)
    пф = у.поле("Флаги", "tacplus.flags", f"0x{флаги:02x}", м + 3, 1, флаги)
    у.поле("Без шифрования (UNENCRYPTED)", "tacplus.flags.unencrypted", флаги & 1, м + 3, 1, родитель=пф)
    у.поле("Одно соединение (SINGLE_CONNECT)", "tacplus.flags.singleconn", флаги >> 2 & 1, м + 3, 1, родитель=пф)
    у.поле("Сеанс", "tacplus.session_id", f"0x{сеанс:08x}", м + 4, 4, сеанс)
    у.поле("Длина тела", "tacplus.packet_len", дл, м + 8, 4)
    вид = "запрос" if seq % 2 else "ответ"
    итог = f"{TACACS_ТИПЫ[тип]} {вид}, seq {seq}"
    if флаги & 1 and not оборван:
        пт = у.поле("Тело", "tacplus.body", f"{дл} байт", м + 12, дл)
        сказано = _tacacs_тело(р, у, м + 12, дл, тип, seq, пт)
        if сказано:
            итог = f"{TACACS_ТИПЫ[тип]} {сказано}"
        else:
            пт.плохо = True
            пт.текст += " (поля тела не сходятся с его длиной)"
    elif флаги & 1:
        у.поле("Тело (оборвано)", "tacplus.body", f"{есть - м - 12} из {дл} байт", м + 12, есть - м - 12)
    elif дл:
        у.поле("Тело (зашифровано)", "tacplus.body", f"зашифровано, {дл} байт", м + 12, есть - м - 12)
        итог += ", зашифровано"
    у.длина = есть - м
    у.итог = итог
    if оборван:
        _оборван(р, у, "TACACS+", объявлено, есть - м)
    return есть, у.итог


@_свой
def tacacs(р: Разбор, м: int, конец: int) -> bool:
    return _по_очереди(р, м, конец, _tacacs_одно, "TACACS+")


# ==============================================================================================
# BER (ITU-T X.690) для Kerberos и LDAP
# ==============================================================================================

def _ber(д: bytes, м: int, конец: int) -> Tuple[int, int, int]:
    """Тег (однобайтовый), начало и конец значения. Заголовок за краем — Мало; неопределённая
    длина, длинный тег, больше 4 байт длины — не DER, значит не наше. Конец значения может
    быть за ``конец`` — это решает вызывающий (оборван или не наше)."""
    if м + 2 > конец:
        raise Мало()
    тег, дл = д[м], д[м + 1]
    if тег & 0x1F == 0x1F:
        raise _НеМоё()
    м += 2
    if дл & 0x80:
        n = дл & 0x7F
        if n == 0 or n > 4:
            raise _НеМоё()
        if м + n > конец:
            raise Мало()
        дл = int.from_bytes(д[м:м + n], "big")
        м += n
    return тег, м, м + дл


def _ber_дети(д: bytes, м: int, конец: int, есть: int) -> List[Tuple[int, int, int, int]]:
    """Элементы внутри составного значения [м, конец): (тег, место, начало, конец значения).
    Не дальше записанного (``есть``): элемент за краем — последний; за пределом родителя — не наше."""
    итог = []
    место = м
    граница = min(конец, есть)
    while место < граница:
        тег, нач, кон = _ber(д, место, граница)
        if кон > конец:
            raise _НеМоё()
        итог.append((тег, место, нач, кон))
        if кон > есть:
            break
        место = кон
    return итог


def _ber_целое(д: bytes, нач: int, кон: int) -> int:
    if not 1 <= кон - нач <= 8:
        raise _НеМоё()
    return int.from_bytes(д[нач:кон], "big", signed=True)


def _ber_найти(дети, тег: int):
    for элемент in дети:
        if элемент[0] == тег:
            return элемент
    return None


# ==============================================================================================
# Kerberos — RFC 4120
# ==============================================================================================

KRB_ТИПЫ = {10: "AS-REQ", 11: "AS-REP", 12: "TGS-REQ", 13: "TGS-REP", 14: "AP-REQ", 15: "AP-REP",
            20: "KRB-SAFE", 21: "KRB-PRIV", 22: "KRB-CRED", 30: "KRB-ERROR"}
KRB_ШИФРЫ = {1: "des-cbc-crc", 3: "des-cbc-md5", 16: "des3-cbc-sha1", 17: "aes128-cts-hmac-sha1-96",
             18: "aes256-cts-hmac-sha1-96", 19: "aes128-cts-hmac-sha256-128",
             20: "aes256-cts-hmac-sha384-192", 23: "rc4-hmac"}
KRB_ОШИБКИ = {6: "KDC_ERR_C_PRINCIPAL_UNKNOWN", 7: "KDC_ERR_S_PRINCIPAL_UNKNOWN", 14: "KDC_ERR_ETYPE_NOSUPP",
              18: "KDC_ERR_CLIENT_REVOKED", 24: "KDC_ERR_PREAUTH_FAILED", 25: "KDC_ERR_PREAUTH_REQUIRED",
              32: "KRB_AP_ERR_TKT_EXPIRED", 37: "KRB_AP_ERR_SKEW", 52: "KRB_ERR_RESPONSE_TOO_BIG"}
KRB_PA = {1: "PA-TGS-REQ", 2: "PA-ENC-TIMESTAMP", 3: "PA-PW-SALT", 11: "PA-ETYPE-INFO", 19: "PA-ETYPE-INFO2",
          128: "PA-PAC-REQUEST"}


class _Kerberos:
    """Разбор одного сообщения Kerberos деревом; ``есть`` — конец записанного."""

    def __init__(self, р: Разбор, у: Уровень, есть: int):
        self.р, self.у, self.д, self.есть = р, у, р.д, есть
        self.сводка: Dict[str, str] = {}

    def дети(self, нач: int, кон: int):
        return _ber_дети(self.д, нач, кон, self.есть)

    def полон(self, элемент) -> bool:
        return элемент[3] <= self.есть

    def внутри(self, элемент, тег: int):
        """Содержимое явного тега [n]: единственный элемент нужного универсального типа."""
        if not self.полон(элемент):
            raise Мало()
        т, м, нач, кон = элемент
        вн = self.дети(нач, кон)
        if len(вн) != 1 or вн[0][0] != тег or вн[0][3] != кон:
            raise _НеМоё()
        return вн[0]

    def целое(self, элемент) -> int:
        т, м, нач, кон = self.внутри(элемент, 0x02)
        return _ber_целое(self.д, нач, кон)

    def строка(self, элемент) -> str:
        т, м, нач, кон = self.внутри(элемент, 0x1B)       # KerberosString ::= GeneralString
        return _текст(self.д[нач:кон])

    def поле(self, подпись, ключ, текст, элемент, сырое=None, родитель=None):
        return self.у.поле(подпись, ключ, текст, элемент[1], min(элемент[3], self.есть) - элемент[1], сырое,
                           родитель=родитель)

    def имя(self, элемент, ключ: str, подпись: str, родитель=None) -> str:
        """PrincipalName ::= SEQUENCE {name-type [0] Int32, name-string [1] SEQUENCE OF KerberosString}."""
        т, м, нач, кон = self.внутри(элемент, 0x30)
        части = self.дети(нач, кон)
        тип_имени = _ber_найти(части, 0xA0)
        строки = _ber_найти(части, 0xA1)
        if тип_имени is None or строки is None:
            raise _НеМоё()
        т_, м_, нач_, кон_ = self.внутри(строки, 0x30)
        слова = []
        for тег, _, н2, к2 in self.дети(нач_, кон_):
            if тег != 0x1B:
                raise _НеМоё()
            слова.append(_текст(self.д[н2:к2]))
        текст = "/".join(слова)
        п = self.поле(подпись, ключ, текст, элемент, родитель=родитель)
        self.поле("Тип имени", "kerberos.name_type", self.целое(тип_имени), тип_имени, родитель=п)
        return текст

    def шифр(self, элемент, подпись: str, родитель=None) -> None:
        """EncryptedData ::= SEQUENCE {etype [0] Int32, kvno [1] UInt32 OPTIONAL, cipher [2] OCTET STRING}."""
        т, м, нач, кон = self.внутри(элемент, 0x30)
        части = self.дети(нач, кон)
        п = self.поле(подпись, "kerberos.enc_part", "", элемент, родитель=родитель)
        вид = _ber_найти(части, 0xA0)
        if вид is not None:
            e = self.целое(вид)
            п.текст = f"etype {e} ({KRB_ШИФРЫ.get(e, 'неизвестный')})"
            self.поле("Тип шифра", "kerberos.etype", KRB_ШИФРЫ.get(e, e), вид, e, родитель=п)
        kvno = _ber_найти(части, 0xA1)
        if kvno is not None:
            self.поле("Версия ключа (kvno)", "kerberos.kvno", self.целое(kvno), kvno, родитель=п)
        шифр = _ber_найти(части, 0xA2)
        if шифр is not None:
            self.поле("Шифртекст", "kerberos.cipher", f"{шифр[3] - шифр[2]} байт", шифр, родитель=п)

    def билет(self, элемент, родитель=None) -> None:
        """Ticket ::= [APPLICATION 1] SEQUENCE {tkt-vno [0], realm [1], sname [2], enc-part [3]}."""
        т, м, нач, кон = self.внутри(элемент, 0x61)
        if not self.полон((т, м, нач, кон)):
            raise Мало()
        т2, м2, н2, к2 = self.дети(нач, кон)[0]
        if т2 != 0x30:
            raise _НеМоё()
        части = self.дети(н2, к2)
        п = self.поле("Билет (Ticket)", "kerberos.ticket", "", элемент, родитель=родитель)
        область = _ber_найти(части, 0xA1)
        if область is not None:
            self.поле("Область", "kerberos.realm", self.строка(область), область, родитель=п)
        имя = _ber_найти(части, 0xA2)
        if имя is not None:
            п.текст = self.имя(имя, "kerberos.snamestring", "Служба (sname)", п)
        enc = _ber_найти(части, 0xA3)
        if enc is not None:
            self.шифр(enc, "Зашифрованная часть билета", п)

    def тело_запроса(self, элемент) -> None:
        """KDC-REQ-BODY (RFC 4120, 5.4.1)."""
        т, м, нач, кон = self.внутри(элемент, 0x30) if self.полон(элемент) else (None,) * 4
        if т is None:
            т, м, нач, кон = self.дети(элемент[2], элемент[3])[0]
            if т != 0x30:
                raise _НеМоё()
        п = self.поле("Тело запроса (req-body)", "kerberos.req_body", "", элемент)
        for тег, м2, н2, к2 in self.дети(нач, кон):
            эл = (тег, м2, н2, к2)
            if к2 > self.есть:
                break
            if тег == 0xA0:
                т3, _, н3, к3 = self.внутри(эл, 0x03)
                self.поле("Параметры KDC", "kerberos.kdc_options", self.д[н3:к3].hex(), эл, родитель=п)
            elif тег == 0xA1:
                self.сводка["cname"] = self.имя(эл, "kerberos.cnamestring", "Клиент (cname)", п)
            elif тег == 0xA2:
                self.сводка["realm"] = self.строка(эл)
                self.поле("Область", "kerberos.realm", self.сводка["realm"], эл, родитель=п)
            elif тег == 0xA3:
                self.сводка["sname"] = self.имя(эл, "kerberos.snamestring", "Служба (sname)", п)
            elif тег == 0xA5:
                т3, _, н3, к3 = self.внутри(эл, 0x18)
                self.поле("До (till)", "kerberos.till", _текст(self.д[н3:к3]), эл, родитель=п)
            elif тег == 0xA7:
                self.поле("Nonce", "kerberos.nonce", self.целое(эл), эл, родитель=п)
            elif тег == 0xA8:
                т3, _, н3, к3 = self.внутри(эл, 0x30)
                виды = []
                пe = self.поле("Типы шифров (etype)", "kerberos.etypes", "", эл, родитель=п)
                for т4, м4, н4, к4 in self.дети(н3, к3):
                    if т4 != 0x02:
                        raise _НеМоё()
                    e = _ber_целое(self.д, н4, к4)
                    виды.append(KRB_ШИФРЫ.get(e, str(e)))
                    self.у.поле(f"{e} ({KRB_ШИФРЫ.get(e, 'неизвестный')})", "kerberos.etype", KRB_ШИФРЫ.get(e, e),
                                м4, к4 - м4, e, родитель=пe)
                пe.текст = ", ".join(виды)
                self.сводка["etype"] = пe.текст

    def padata(self, элемент) -> None:
        """padata: SEQUENCE OF PA-DATA {padata-type [1] Int32, padata-value [2] OCTET STRING}."""
        т, м, нач, кон = self.внутри(элемент, 0x30)
        п = self.поле("Предаутентификация (padata)", "kerberos.padata", "", элемент)
        виды = []
        for тег, м2, н2, к2 in self.дети(нач, кон):
            if тег != 0x30:
                raise _НеМоё()
            вид = _ber_найти(self.дети(н2, к2), 0xA1)
            if вид is None:
                raise _НеМоё()
            номер = self.целое(вид)
            виды.append(KRB_PA.get(номер, str(номер)))
            self.у.поле(KRB_PA.get(номер, f"PA {номер}"), "kerberos.padata_type", номер, м2, к2 - м2,
                        родитель=п)
        п.текст = ", ".join(виды)


def _kerberos_сообщение(р: Разбор, м: int, есть: int, объявлено_до: Optional[int], начало_уровня: int,
                        tcp_длина: Optional[int]) -> bool:
    """Проверка и разбор сообщения [APPLICATION n] (RFC 4120, 5.4.1, 5.4.2, 5.5.1, 5.5.2, 5.9.1)."""
    д = р.д
    тег, нач, кон = _ber(д, м, есть)
    номер = тег & 0x1F
    if тег & 0xE0 != 0x60 or номер not in KRB_ТИПЫ:
        return False
    # Длина BER сходится с длиной записи TCP; на UDP — с датаграммой (длиннее — только если
    # датаграмма записана не целиком).
    if объявлено_до is not None and кон != объявлено_до or \
            объявлено_до is None and (кон < есть or кон > есть and not _захват_оборван(р, есть)):
        return False
    т, нач2, кон2 = _ber(д, нач, есть)
    if т != 0x30 or кон2 != кон:
        return False
    дети = _ber_дети(д, нач2, кон2, есть)
    первый = 0xA1 if номер in (10, 12) else 0xA0          # KDC-REQ: pvno [1], msg-type [2]
    if len(дети) < 2 or дети[0][0] != первый or дети[1][0] != первый + 1:
        return False
    у = р.уровень("Kerberos", "Kerberos 5 (RFC 4120)", начало_уровня)
    к = _Kerberos(р, у, есть)
    if к.целое(дети[0]) != 5 or к.целое(дети[1]) != номер:  # pvno = 5, msg-type = номеру тега
        return False
    if tcp_длина is not None:
        у.поле("Длина записи (TCP)", "kerberos.rm.length", tcp_длина, начало_уровня, 4)
    имя = KRB_ТИПЫ[номер]
    у.поле("Сообщение", "kerberos.application", f"[APPLICATION {номер}] {имя}", м, нач - м, номер)
    к.поле("Версия протокола (pvno)", "kerberos.pvno", 5, дети[0])
    к.поле("Тип сообщения", "kerberos.msg_type", f"{номер} ({имя})", дети[1], номер)
    оборван = кон > есть
    try:
        по_тегу = {э[0]: э for э in дети}
        if номер in (10, 12):
            if 0xA3 in по_тегу:
                к.padata(по_тегу[0xA3])
            if 0xA4 in по_тегу:
                к.тело_запроса(по_тегу[0xA4])
        elif номер in (11, 13):
            if 0xA2 in по_тегу:
                к.padata(по_тегу[0xA2])
            if 0xA3 in по_тегу:
                к.сводка["realm"] = к.строка(по_тегу[0xA3])
                к.поле("Область клиента (crealm)", "kerberos.crealm", к.сводка["realm"], по_тегу[0xA3])
            if 0xA4 in по_тегу:
                к.сводка["cname"] = к.имя(по_тегу[0xA4], "kerberos.cnamestring", "Клиент (cname)")
            if 0xA5 in по_тегу:
                к.билет(по_тегу[0xA5])
            if 0xA6 in по_тегу:
                к.шифр(по_тегу[0xA6], "Зашифрованная часть ответа")
        elif номер == 14:
            if 0xA2 in по_тегу:
                т3, _, н3, к3 = к.внутри(по_тегу[0xA2], 0x03)
                к.поле("Параметры AP", "kerberos.ap_options", д[н3:к3].hex(), по_тегу[0xA2])
            if 0xA3 in по_тегу:
                к.билет(по_тегу[0xA3])
            if 0xA4 in по_тегу:
                к.шифр(по_тегу[0xA4], "Аутентификатор")
        elif номер == 15 and 0xA2 in по_тегу:
            к.шифр(по_тегу[0xA2], "Зашифрованная часть")
        elif номер == 30:
            if 0xA4 in по_тегу:
                т3, _, н3, к3 = к.внутри(по_тегу[0xA4], 0x18)
                к.поле("Время сервера (stime)", "kerberos.stime", _текст(д[н3:к3]), по_тегу[0xA4])
            if 0xA6 in по_тегу:
                код = к.целое(по_тегу[0xA6])
                к.сводка["error"] = f"{код} ({KRB_ОШИБКИ.get(код, 'неизвестная')})"
                к.поле("Код ошибки", "kerberos.error_code", к.сводка["error"], по_тегу[0xA6], код)
            if 0xA7 in по_тегу:
                к.поле("Область клиента (crealm)", "kerberos.crealm", к.строка(по_тегу[0xA7]), по_тегу[0xA7])
            if 0xA8 in по_тегу:
                к.сводка["cname"] = к.имя(по_тегу[0xA8], "kerberos.cnamestring", "Клиент (cname)")
            if 0xA9 in по_тегу:
                к.сводка["realm"] = к.строка(по_тегу[0xA9])
                к.поле("Область", "kerberos.realm", к.сводка["realm"], по_тегу[0xA9])
            if 0xAA in по_тегу:
                к.сводка["sname"] = к.имя(по_тегу[0xAA], "kerberos.snamestring", "Служба (sname)")
            if 0xAB in по_тегу:
                к.поле("Текст ошибки", "kerberos.e_text", к.строка(по_тегу[0xAB]), по_тегу[0xAB])
    except Мало:
        if not оборван:
            return False
    у.длина = min(кон, есть) - начало_уровня
    итог = имя
    с = к.сводка
    if "error" in с:
        итог += f" {с['error']}"
    if "cname" in с:
        итог += f", клиент {с['cname']}" + (f"@{с['realm']}" if "realm" in с else "")
    if "sname" in с:
        итог += f", служба {с['sname']}"
    if "etype" in с:
        итог += f", шифры {с['etype']}"
    у.итог = итог
    if оборван:
        _оборван(р, у, "Kerberos", кон - начало_уровня, есть - начало_уровня)
    р.п.инфо = "Kerberos " + у.итог
    return True


@_свой
def kerberos_udp(р: Разбор, м: int, конец: int) -> bool:
    """UDP 88 (RFC 4120, 7.2.1): одно сообщение на датаграмму."""
    конец = min(конец, len(р.д))
    return _kerberos_сообщение(р, м, конец, None, м, None)


@_свой
def kerberos_tcp(р: Разбор, м: int, конец: int) -> bool:
    """TCP 88 (RFC 4120, 7.2.2): 4 байта длины (старший бит зарезервирован — 0), затем сообщение."""
    конец = min(конец, len(р.д))
    if конец - м < 6:
        return False
    длина = u32(р.д, м)
    if длина & 0x80000000 or длина < 2:
        return False
    объявлено_до = м + 4 + длина
    if объявлено_до < конец:
        return False
    return _kerberos_сообщение(р, м + 4, конец, объявлено_до, м, длина)


# ==============================================================================================
# LDAP — RFC 4511
# ==============================================================================================

LDAP_ОПЕРАЦИИ = {0x60: "bindRequest", 0x61: "bindResponse", 0x42: "unbindRequest", 0x63: "searchRequest",
                 0x64: "searchResEntry", 0x65: "searchResDone", 0x73: "searchResRef", 0x66: "modifyRequest",
                 0x67: "modifyResponse", 0x68: "addRequest", 0x69: "addResponse", 0x4A: "delRequest",
                 0x6B: "delResponse", 0x6C: "modDNRequest", 0x6D: "modDNResponse", 0x6E: "compareRequest",
                 0x6F: "compareResponse", 0x50: "abandonRequest", 0x77: "extendedReq", 0x78: "extendedResp",
                 0x79: "intermediateResponse"}
LDAP_ОТВЕТЫ = {0x61, 0x65, 0x67, 0x69, 0x6B, 0x6D, 0x6F, 0x78}
LDAP_РЕЗУЛЬТАТЫ = {0: "success", 1: "operationsError", 2: "protocolError", 4: "sizeLimitExceeded",
                   10: "referral", 32: "noSuchObject", 49: "invalidCredentials", 50: "insufficientAccessRights",
                   53: "unwillingToPerform"}
LDAP_ОБЛАСТЬ = {0: "baseObject", 1: "singleLevel", 2: "wholeSubtree"}


def _ldap_фильтр(д: bytes, тег: int, нач: int, кон: int, глубина: int = 0) -> str:
    """Filter (RFC 4511, 4.5.1.7) строкой в духе RFC 4515; незнакомое — «?»."""
    if глубина > 16:
        return "…"
    if тег in (0xA0, 0xA1):
        части = [_ldap_фильтр(д, т, н, к, глубина + 1) for т, _, н, к in _ber_дети(д, нач, кон, кон)]
        return "(" + ("&" if тег == 0xA0 else "|") + "".join(части) + ")"
    if тег == 0xA2:
        вн = _ber_дети(д, нач, кон, кон)
        return "(!" + (_ldap_фильтр(д, вн[0][0], вн[0][2], вн[0][3], глубина + 1) if вн else "") + ")"
    if тег == 0x87:
        return f"({_текст(д[нач:кон])}=*)"
    if тег in (0xA3, 0xA5, 0xA6, 0xA8):
        вн = _ber_дети(д, нач, кон, кон)
        if len(вн) == 2:
            знак = {0xA3: "=", 0xA5: ">=", 0xA6: "<=", 0xA8: "~="}[тег]
            return f"({_текст(д[вн[0][2]:вн[0][3]])}{знак}{_текст(д[вн[1][2]:вн[1][3]])})"
    if тег == 0xA4:
        вн = _ber_дети(д, нач, кон, кон)
        if len(вн) == 2:
            куски = {0x80: "", 0x81: "", 0x82: ""}
            текст = ["", [], ""]
            for т, _, н, к in _ber_дети(д, вн[1][2], вн[1][3], вн[1][3]):
                if т in куски:
                    if т == 0x81:
                        текст[1].append(_текст(д[н:к]))
                    else:
                        текст[0 if т == 0x80 else 2] = _текст(д[н:к])
            return f"({_текст(д[вн[0][2]:вн[0][3]])}={текст[0]}*" + "".join(ч + "*" for ч in текст[1]) + f"{текст[2]})"
    return "(?)"


def _ldap_одно(р: Разбор, м: int, конец: int):
    """LDAPMessage ::= SEQUENCE {messageID INTEGER (0..2^31-1), protocolOp CHOICE, controls [0]
    OPTIONAL} (RFC 4511, 4.1.1). Проверяется: SEQUENCE, messageID — целое 1–4 байта без знака,
    тег операции из известных, после операции — только controls, и всё сходится с длиной."""
    д = р.д
    тег, нач, кон = _ber(д, м, конец)
    if тег != 0x30:
        return None
    оборван = кон > конец
    т_ид, н_ид, к_ид = _ber(д, нач, конец)
    if т_ид != 0x02 or not 1 <= к_ид - н_ид <= 4 or к_ид > конец or д[н_ид] & 0x80:
        return None
    ид = int.from_bytes(д[н_ид:к_ид], "big")
    т_оп, н_оп, к_оп = _ber(д, к_ид, конец)
    if т_оп not in LDAP_ОПЕРАЦИИ or к_оп > кон:
        return None
    if т_оп == 0x42 and к_оп != н_оп:                       # unbindRequest ::= NULL
        return None
    if not оборван:
        место = к_оп
        if место < кон:
            т_к, н_к, к_к = _ber(д, место, кон)
            if т_к != 0xA0 or к_к != кон:
                return None
    есть = min(кон, конец)
    операция = LDAP_ОПЕРАЦИИ[т_оп]
    у = р.уровень("LDAP", "Lightweight Directory Access Protocol", м)
    у.поле("Идентификатор сообщения", "ldap.messageid", ид, нач, к_ид - нач)
    по = у.поле("Операция", "ldap.protocolop", операция, к_ид, min(к_оп, конец) - к_ид, т_оп & 0x1F)
    итог = f"{операция}({ид})"
    try:
        if к_оп > конец:
            raise Мало()
        части = _ber_дети(д, н_оп, к_оп, к_оп) if т_оп & 0x20 else []
        if т_оп == 0x60:
            if len(части) < 3 or части[0][0] != 0x02 or части[1][0] != 0x04:
                return _ldap_отказ(р, у)
            версия = _ber_целое(д, части[0][2], части[0][3])
            if not 1 <= версия <= 127:
                return _ldap_отказ(р, у)
            имя = _текст(д[части[1][2]:части[1][3]])
            у.поле("Версия", "ldap.version", версия, части[0][1], части[0][3] - части[0][1], родитель=по)
            у.поле("Имя", "ldap.name", имя, части[1][1], части[1][3] - части[1][1], родитель=по)
            т_а, м_а, н_а, к_а = части[2]
            if т_а == 0x80:
                у.поле("Простая аутентификация", "ldap.simple", f"пароль, {к_а - н_а} байт", м_а, к_а - м_а,
                       к_а - н_а, родитель=по)
                итог += f", «{имя}», простая"
            elif т_а == 0xA3:
                мех = _ber_дети(д, н_а, к_а, к_а)
                механизм = _текст(д[мех[0][2]:мех[0][3]]) if мех and мех[0][0] == 0x04 else "?"
                у.поле("SASL", "ldap.mechanism", механизм, м_а, к_а - м_а, родитель=по)
                итог += f", «{имя}», SASL {механизм}"
            else:
                return _ldap_отказ(р, у)
        elif т_оп == 0x63:
            if len(части) < 8 or [ч[0] for ч in части[:6]] != [0x04, 0x0A, 0x0A, 0x02, 0x02, 0x01]:
                return _ldap_отказ(р, у)
            база = _текст(д[части[0][2]:части[0][3]])
            область = _ber_целое(д, части[1][2], части[1][3])
            if область not in LDAP_ОБЛАСТЬ:
                return _ldap_отказ(р, у)
            у.поле("Базовый объект", "ldap.baseobject", база, части[0][1], части[0][3] - части[0][1], родитель=по)
            у.поле("Область (scope)", "ldap.scope", f"{область} ({LDAP_ОБЛАСТЬ[область]})", части[1][1],
                   части[1][3] - части[1][1], область, родитель=по)
            у.поле("Разыменование", "ldap.derefaliases", _ber_целое(д, части[2][2], части[2][3]), части[2][1],
                   части[2][3] - части[2][1], родитель=по)
            у.поле("Предел числа", "ldap.sizelimit", _ber_целое(д, части[3][2], части[3][3]), части[3][1],
                   части[3][3] - части[3][1], родитель=по)
            у.поле("Предел времени", "ldap.timelimit", _ber_целое(д, части[4][2], части[4][3]), части[4][1],
                   части[4][3] - части[4][1], родитель=по)
            т_ф, м_ф, н_ф, к_ф = части[6]
            фильтр = _ldap_фильтр(д, т_ф, н_ф, к_ф)
            у.поле("Фильтр", "ldap.filter", фильтр, м_ф, к_ф - м_ф, родитель=по)
            атрибуты = [_текст(д[н:к]) for т, _, н, к in _ber_дети(д, части[7][2], части[7][3], части[7][3])]
            у.поле("Атрибуты", "ldap.attributes", ", ".join(атрибуты) or "(все)", части[7][1],
                   части[7][3] - части[7][1], родитель=по)
            итог += f", «{база}», {LDAP_ОБЛАСТЬ[область]}, {фильтр}"
        elif т_оп in LDAP_ОТВЕТЫ:
            if len(части) < 3 or [ч[0] for ч in части[:3]] != [0x0A, 0x04, 0x04]:
                return _ldap_отказ(р, у)
            код = _ber_целое(д, части[0][2], части[0][3])
            у.поле("Код результата", "ldap.resultcode", f"{код} ({LDAP_РЕЗУЛЬТАТЫ.get(код, 'неизвестный')})",
                   части[0][1], части[0][3] - части[0][1], код, родитель=по)
            у.поле("Совпавшее имя", "ldap.matcheddn", _текст(д[части[1][2]:части[1][3]]), части[1][1],
                   части[1][3] - части[1][1], родитель=по)
            сообщение = _текст(д[части[2][2]:части[2][3]])
            у.поле("Диагностика", "ldap.errormessage", сообщение, части[2][1], части[2][3] - части[2][1],
                   родитель=по)
            итог += f" {LDAP_РЕЗУЛЬТАТЫ.get(код, код)}" + (f" «{сообщение}»" if сообщение else "")
        elif т_оп in (0x64, 0x66, 0x68, 0x6C, 0x6E):
            if not части or части[0][0] != 0x04:
                return _ldap_отказ(р, у)
            объект = _текст(д[части[0][2]:части[0][3]])
            у.поле("Объект", "ldap.objectname", объект, части[0][1], части[0][3] - части[0][1], родитель=по)
            итог += f", «{объект}»"
        elif т_оп == 0x4A:
            объект = _текст(д[н_оп:к_оп])
            у.поле("Объект", "ldap.objectname", объект, н_оп, к_оп - н_оп, родитель=по)
            итог += f", «{объект}»"
        elif т_оп == 0x50:
            у.поле("Отменяемый messageID", "ldap.abandonrequest", _ber_целое(д, н_оп, к_оп), н_оп, к_оп - н_оп,
                   родитель=по)
        elif т_оп == 0x77 and части and части[0][0] == 0x80:
            oid = _текст(д[части[0][2]:части[0][3]])
            у.поле("Имя запроса (OID)", "ldap.requestname", oid, части[0][1], части[0][3] - части[0][1], родитель=по)
            итог += f", {oid}"
    except Мало:
        if not оборван:
            return _ldap_отказ(р, у)
    у.длина = есть - м
    у.итог = итог
    if оборван:
        _оборван(р, у, "LDAP", кон - м, есть - м)
    return есть, у.итог


def _ldap_отказ(р: Разбор, у: Уровень):
    del р.п.уровни[р.п.уровни.index(у):]
    return None


@_свой
def ldap(р: Разбор, м: int, конец: int) -> bool:
    return _по_очереди(р, м, конец, _ldap_одно, "LDAP")


# ==============================================================================================
# Регистрация
# ==============================================================================================

prilozh.ПОРТЫ_TCP.update({3868: diameter, 49: tacacs, 88: kerberos_tcp, 389: ldap})
prilozh.ПОРТЫ_UDP.update({2123: gtp_c, 3386: gtp_prime, 8805: pfcp, 4729: gsmtap, 88: kerberos_udp, 389: ldap})
prilozh.КАК["tcp"].update({"Diameter": diameter, "TACACS+": tacacs, "Kerberos": kerberos_tcp, "LDAP": ldap})
prilozh.КАК["udp"].update({"GTP-C": gtp_c, "GTP'": gtp_prime, "PFCP": pfcp, "GSMTAP": gsmtap,
                           "Kerberos": kerberos_udp, "CLDAP": ldap})
prilozh.ЭВРИСТИКИ_TCP.append(diameter_эвристика)
razbor.ДОП_SCTP_PPID.update({46: diameter, 18: s1ap, 60: ngap, 27: x2ap})
razbor.ДОП_SCTP_ПОРТ.update({3868: diameter, 36412: s1ap, 38412: ngap, 36422: x2ap, 29118: sgsap})
razbor.ДОП_УРОВНИ.update({имя: "прикладной" for имя in (
    "Diameter", "GTP", "GTPv2", "GTP'", "PFCP", "S1AP", "NGAP", "X2AP", "SGsAP", "GSMTAP", "TACACS+",
    "Kerberos", "LDAP")})
