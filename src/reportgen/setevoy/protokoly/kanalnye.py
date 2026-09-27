"""Канальный уровень и глобальные сети: разбор по EtherType, LLC, типу канала pcap и порту UDP.

По EtherType (``razbor.ДОП_ETHERTYPE``):
  0x888E EAPOL — IEEE 802.1X-2010, 11.3 (EAPOL PDU); вложенный EAP — RFC 3748, 4 (пакет) и 5 (типы);
  0x8809 Slow Protocols — IEEE 802.3-2018, прил. 43B и 57A: LACP и Marker — IEEE 802.1AX-2014,
         6.4.2.3 (LACPDU) и 6.5.3.3 (Marker PDU); OAM — IEEE 802.3-2018, 57.4.2 (OAMPDU) и
         57.5.2 (TLV Information OAMPDU); OSSP (подтип 0x0A) — IEEE 802.3, прил. 57B: у OUI ITU-T
         00-19-A7 подтип 1 — ESMC, ITU-T G.8264, 11.3.1 (TLV QL и расширенный QL; уровни качества —
         G.781, 5.5.1, варианты I–III; сверено с разбором Wireshark packet-ossp.c);
  0x88F7 PTP — IEEE 1588-2008, 13 (сообщения; 13.3 — общий заголовок, 5.3.3 — отметка времени),
         прил. F (Ethernet); по UDP 319/320 — прил. D;
  0x88E5 MACsec — IEEE 802.1AE-2018, 9 (SecTAG: 9.5 TCI, 9.6 AN, 9.7 SL, 9.8 PN, 9.9 SCI) и
         10.6 (проверка принятого кадра);
  0x88E7 PBB — IEEE 802.1Q-2018, 9.7 (I-TAG; прежде IEEE 802.1ah-2008), за ним — кадр клиента;
  0x8906 FCoE — INCITS 462-2009 (FC-BB-5), разд. 7 (FC-BB_E: кадр FCoE); коды SOF/EOF — RFC 3643, 3.1;
         заголовок кадра Fibre Channel — INCITS FC-FS-3, разд. 9 (Frame header);
  0x8914 FIP — FC-BB-5, разд. 7 (FIP: заголовок и дескрипторы);
  0x8892 PROFINET RT — IEC 61158-6-10 (FrameID, APDU-статус циклических кадров);
         DCP (FrameID 0xFEFC–0xFEFF) — IEC 61158-6-10, DCP-PDU (сервис, тип, блоки);
  0x88A4 EtherCAT — IEC 61158-4-12 (ETG.1000.4): заголовок кадра и датаграммы DLPDU;
  0x88B8 GOOSE — IEC 61850-8-1:2011, прил. C (заголовок) и разд. 18 (ASN.1 IECGoosePdu);
  0x88BA SV — IEC 61850-9-2:2011, разд. 8 (ASN.1 SavPdu, ASDU);
  0x0842 Wake-on-LAN — AMD «Magic Packet Technology», White Paper 20213 (1995); он же по UDP (эвристика).
По LLC (``razbor.ДОП_LLC``): DSAP 0xFE (OSI) → IS-IS (NLPID 0x83) — ISO/IEC 10589:2002, 9 (форматы PDU),
  контрольная сумма LSP — 7.3.11 и ISO 8473-1, прил. C (Флетчер). Тот же разбор — по PPP, протокол
  0x0023 «OSI Network Layer» (RFC 1377; ``razbor.ДОП_PPP``): IS-IS на каналах PPP.
По SNAP (OUI 00-00-0C, PID 0x2000 → ``ДОП_ETHERTYPE[0x2000]``): CDP — формат Cisco Discovery Protocol
  (документация Cisco; открытого стандарта нет): версия, TTL, сумма RFC 1071, TLV.
Каналы pcap (``chtenie.КАНАЛЫ``, ``razbor.КАНАЛ_В_РАЗБОРЩИК``; номера — tcpdump.org, LINKTYPE_*):
  107 LINKTYPE_FRELAY — Frame Relay: ITU-T Q.922, 3.3 (поле адреса), RFC 2427, 2–4 (NLPID и SNAP),
      LMI на DLCI 0 — ITU-T Q.933, прил. A, и ANSI T1.617, прил. D;
  3 LINKTYPE_AX25 — AX.25 v2.2 (TAPR/ARRL, 1998): 3.12 (адреса), 4.2–4.3 (управление), 3.4 (PID);
  207 LINKTYPE_LAPB_WITH_DIR — байт направления, LAPB — ITU-T X.25 (10/96), 2.3–2.4;
      пакетный уровень X.25 — ITU-T X.25, 5 (GFI, LCI, идентификатор типа пакета).
По порту UDP 1985: HSRP — RFC 2281, 5 (версия 0).

Всё, что стандарт делает проверяемым (версии, зарезервированные биты, длины, суммы, магические
значения), проверяется до того, как уровень появится в дереве: не сошлось — данные не наши.
"""

from __future__ import annotations

import datetime
import struct
import zlib
from collections.abc import Callable

from ..chtenie import КАНАЛЫ
from ..pole import Мало, ip4, ip6, mac, u16, u24, u32, печатное, сумма16
from ..prilozh import КАК, ПОРТЫ_UDP, ЭВРИСТИКИ_UDP
from ..razbor import (
    ETHERTYPE,
    PPP_ПРОТОКОЛЫ,
    ДОП_ETHERTYPE,
    ДОП_LLC,
    ДОП_PPP,
    ДОП_УРОВНИ,
    КАНАЛ_В_РАЗБОРЩИК,
    Разбор,
    arp,
    ethernet,
    ipv4,
    ipv6,
    данные,
    по_типу,
)

Разборщик = Callable[[Разбор, int, int], bool]


# -- общее ---------------------------------------------------------------------------------------

def _le16(д: bytes, м: int) -> int:
    return struct.unpack_from("<H", д, м)[0]


def _le32(д: bytes, м: int) -> int:
    return struct.unpack_from("<I", д, м)[0]


def _предел(р: Разбор, конец: int) -> int:
    """Конец данных, каким он был в линии: захват мог обрезать пакет (snaplen)."""
    недостаёт = р.п.исходная_длина - len(р.д)
    return конец + недостаёт if недостаёт > 0 and конец >= len(р.д) else конец


def _снимок(р: Разбор):
    п = р.п
    return len(п.уровни), п.инфо, len(п.ошибки), п.нагрузка, п.нагрузка_смещение, п.источник, п.получатель


def _откат(р: Разбор, снимок) -> None:
    п = р.п
    del п.уровни[снимок[0]:]
    del п.ошибки[снимок[2]:]
    п.инфо, п.нагрузка, п.нагрузка_смещение, п.источник, п.получатель = (снимок[1],) + снимок[3:]


def _надёжно(разборщик: Разборщик) -> Разборщик:
    """Чтение за концом данных (struct.error) — это оборванный пакет, как Мало."""
    def обёртка(р: Разбор, м: int, конец: int) -> bool:
        try:
            return разборщик(р, м, конец)
        except struct.error:
            raise Мало() from None
    обёртка.__name__, обёртка.__doc__ = разборщик.__name__, разборщик.__doc__
    return обёртка


def _по_ethertype(тип: int, разборщик: Разборщик) -> None:
    """EtherType → разборщик(р, м, конец) → bool; не его данные — остаются «данными»."""
    def вход(р: Разбор, м: int) -> None:
        снимок = _снимок(р)
        try:
            if разборщик(р, м, len(р.д)):
                return
        except (IndexError, ValueError, struct.error):
            pass
        _откат(р, снимок)
        данные(р, м, f"EtherType 0x{тип:04x}")
    вход.__name__ = f"{разборщик.__name__}_ethertype"
    ДОП_ETHERTYPE.setdefault(тип, вход)


def _биты(у, родитель, место: int, длина: int, значение: int, биты, префикс: str) -> list[str]:
    """Флаги по маскам: дочерние поля 0/1, возвращает имена поднятых."""
    поднятые = []
    for маска, имя, ключ in биты:
        бит = 1 if значение & маска else 0
        у.поле(имя, f"{префикс}.{ключ}", бит, место, длина, родитель=родитель)
        if бит:
            поднятые.append(имя)
    return поднятые


def _текст(данные_: bytes) -> str:
    return данные_.split(b"\0", 1)[0].decode("utf-8", "replace")


# -- EAPOL и EAP: IEEE 802.1X-2010, 11.3; RFC 3748 --------------------------------------------------

EAPOL_ВЕРСИИ = {1: "802.1X-2001", 2: "802.1X-2004", 3: "802.1X-2010"}
EAPOL_ТИПЫ = {0: "EAP-Packet", 1: "EAPOL-Start", 2: "EAPOL-Logoff", 3: "EAPOL-Key",
              4: "EAPOL-Encapsulated-ASF-Alert", 5: "EAPOL-MKA", 6: "EAPOL-Announcement (Generic)",
              7: "EAPOL-Announcement (Specific)", 8: "EAPOL-Announcement-Req"}
EAPOL_КЛЮЧИ = {1: "RC4", 2: "IEEE 802.11 (RSN)", 254: "WPA"}
EAP_КОДЫ = {1: "Request", 2: "Response", 3: "Success", 4: "Failure"}
EAP_ТИПЫ = {1: "Identity", 2: "Notification", 3: "Nak", 4: "MD5-Challenge", 5: "OTP", 6: "GTC",
            13: "EAP-TLS", 21: "EAP-TTLS", 25: "PEAP", 254: "Expanded", 255: "Experimental"}


def _eap_годен(д: bytes, м: int, длина: int, конец: int) -> bool:
    """RFC 3748, 4: код 1–4; длина EAP равна длине тела EAPOL; Success/Failure — ровно 4 байта."""
    if длина < 4 or м + 4 > конец:
        return False
    код, дл = д[м], u16(д, м + 2)
    if код not in EAP_КОДЫ or дл != длина:
        return False
    return дл == 4 if код in (3, 4) else дл >= 5


@_надёжно
def eapol(р: Разбор, м: int, конец: int) -> bool:
    """EAPOL PDU (IEEE 802.1X-2010, 11.3): версия, тип пакета, длина тела; EAP-Packet → EAP."""
    д = р.д
    if м + 4 > конец:
        return False
    версия, тип, длина = д[м], д[м + 1], u16(д, м + 2)
    if версия not in EAPOL_ВЕРСИИ or тип not in EAPOL_ТИПЫ or м + 4 + длина > _предел(р, конец):
        return False
    if тип == 0 and not _eap_годен(д, м + 4, длина, конец):
        return False
    у = р.уровень("EAPOL", "IEEE 802.1X EAP over LAN", м)
    у.поле("Версия", "eapol.version", f"{версия} ({EAPOL_ВЕРСИИ[версия]})", м, 1, версия)
    у.поле("Тип пакета", "eapol.type", f"{тип} ({EAPOL_ТИПЫ[тип]})", м + 1, 1, тип)
    у.поле("Длина тела", "eapol.len", длина, м + 2, 2)
    у.длина = 4
    у.итог = EAPOL_ТИПЫ[тип]
    р.п.инфо = "EAPOL " + у.итог
    if тип == 0:
        eap(р, м + 4, м + 4 + длина)
        return True
    у.длина = 4 + длина
    if тип == 3 and длина >= 1:
        р.нужно(м + 4, 1)
        дескр = д[м + 4]
        у.поле("Тип дескриптора ключа", "eapol.keydes.type",
               f"{дескр} ({EAPOL_КЛЮЧИ.get(дескр, 'неизвестный')})", м + 4, 1, дескр)
        if дескр in (2, 254) and длина >= 13:
            р.нужно(м + 5, 12)
            сведения = u16(д, м + 5)
            у.поле("Сведения о ключе", "eapol.keydes.key_info", f"0x{сведения:04x}", м + 5, 2, сведения)
            у.поле("Длина ключа", "eapol.keydes.key_len", u16(д, м + 7), м + 7, 2)
            у.поле("Счётчик повторов", "eapol.keydes.replay_counter", int.from_bytes(д[м + 9:м + 17], "big"),
                   м + 9, 8)
        у.итог += f", дескриптор {EAPOL_КЛЮЧИ.get(дескр, дескр)}"
        р.п.инфо = "EAPOL " + у.итог
    elif длина:
        у.поле("Тело", "eapol.body", д[м + 4:м + 4 + длина].hex(), м + 4, длина)
    return True


def eap(р: Разбор, м: int, конец: int) -> None:
    """EAP (RFC 3748, 4): код, идентификатор, длина, тип (5) и данные типа."""
    д = р.д
    у = р.уровень("EAP", "Extensible Authentication Protocol", м)
    р.нужно(м, 4)
    код, ид, дл = д[м], д[м + 1], u16(д, м + 2)
    у.поле("Код", "eap.code", f"{код} ({EAP_КОДЫ.get(код, 'неизвестный')})", м, 1, код)
    у.поле("Идентификатор", "eap.id", ид, м + 1, 1)
    у.поле("Длина", "eap.len", дл, м + 2, 2)
    у.длина = дл
    у.итог = f"{EAP_КОДЫ.get(код, код)}, id {ид}"
    if код in (1, 2):
        р.нужно(м + 4, 1)
        тип = д[м + 4]
        у.поле("Тип", "eap.type", f"{тип} ({EAP_ТИПЫ.get(тип, 'неизвестный')})", м + 4, 1, тип)
        у.итог += f", {EAP_ТИПЫ.get(тип, f'тип {тип}')}"
        тело_до = min(конец, м + дл, len(д))
        тело = д[м + 5:тело_до]
        if тип == 1:
            текст = тело.decode("utf-8", "replace")
            у.поле("Идентичность", "eap.identity", текст, м + 5, len(тело))
            у.итог += f" «{текст}»"
        elif тип == 2:
            у.поле("Уведомление", "eap.notification", тело.decode("utf-8", "replace"), м + 5, len(тело))
        elif тип == 3:
            for i, желаемый in enumerate(тело):
                у.поле("Желаемый тип", "eap.desired_type",
                       f"{желаемый} ({EAP_ТИПЫ.get(желаемый, 'неизвестный')})", м + 5 + i, 1, желаемый)
        elif тип == 4 and тело:
            размер = тело[0]
            у.поле("Длина значения", "eap.md5.value_size", размер, м + 5, 1)
            у.поле("Значение", "eap.md5.value", тело[1:1 + размер].hex(), м + 6, min(размер, len(тело) - 1))
        elif тело:
            у.поле("Данные типа", "eap.data", тело.hex(), м + 5, len(тело))
        if м + дл > len(д):
            raise Мало()
    р.п.инфо = "EAP " + у.итог


# -- Slow Protocols: LACP, Marker (IEEE 802.1AX-2014), OAM (IEEE 802.3-2018, 57) ---------------------

LACP_СОСТОЯНИЕ = ((0x01, "LACP_Activity", "activity"), (0x02, "LACP_Timeout", "timeout"),
                  (0x04, "Aggregation", "aggregation"), (0x08, "Synchronization", "synchronization"),
                  (0x10, "Collecting", "collecting"), (0x20, "Distributing", "distributing"),
                  (0x40, "Defaulted", "defaulted"), (0x80, "Expired", "expired"))
OAM_КОДЫ = {0x00: "Information", 0x01: "Event Notification", 0x02: "Variable Request",
            0x03: "Variable Response", 0x04: "Loopback Control", 0xFE: "Organization Specific"}
OAM_ФЛАГИ = ((0x01, "Link Fault", "link_fault"), (0x02, "Dying Gasp", "dying_gasp"),
             (0x04, "Critical Event", "critical_event"), (0x08, "Local Evaluating", "local_evaluating"),
             (0x10, "Local Stable", "local_stable"), (0x20, "Remote Evaluating", "remote_evaluating"),
             (0x40, "Remote Stable", "remote_stable"))
OAM_TLV = {0x00: "конец", 0x01: "Local Information", 0x02: "Remote Information",
           0xFE: "Organization Specific Information"}


@_надёжно
def медленные(р: Разбор, м: int, конец: int) -> bool:
    """Slow Protocols (IEEE 802.3-2018, прил. 43B/57A): 1 — LACP, 2 — Marker, 3 — OAM, 0x0A — OSSP."""
    if м >= конец:
        return False
    разборщик = {1: lacp, 2: marker, 3: oam, 0x0A: esmc}.get(р.д[м])
    return разборщик is not None and разборщик(р, м, конец)


#: Уровни качества по коду SSM (G.781, 5.5.1): вариант I (сети SDH по G.707), II (SONET), III.
ESMC_QL = {
    "I": {2: "QL-PRC", 4: "QL-SSU-A", 8: "QL-SSU-B", 11: "QL-EEC1", 15: "QL-DNU"},
    "II": {0: "QL-STU", 1: "QL-PRS", 4: "QL-TNC", 7: "QL-ST2", 10: "QL-ST3", 13: "QL-ST3E",
           14: "QL-PROV", 15: "QL-DUS"},
    "III": {0: "QL-UNK", 11: "QL-EEC1"},
}
#: Расширенные уровни (G.8264, табл. 11-8): (eSSM, SSM) → уровень, по вариантам; eSSM 0xFF — как SSM.
ESMC_РАСШИРЕННЫЕ = {
    "I": {(0x20, 2): "QL-PRTC", (0x21, 2): "QL-ePRTC", (0x22, 11): "QL-eEEC", (0x23, 2): "QL-ePRC"},
    "II": {(0x20, 1): "QL-PRTC", (0x21, 1): "QL-ePRTC", (0x22, 10): "QL-eEEC", (0x23, 1): "QL-ePRC"},
    "III": {},
}


def esmc_уровень(ssm: int, essm: int | None = None) -> str:
    """Уровень качества по SSM (и eSSM) во всех вариантах сети, где код определён."""
    имена = []
    for вариант, таблица in ESMC_QL.items():
        if essm is None or essm == 0xFF:
            имя = таблица.get(ssm)
        else:
            имя = ESMC_РАСШИРЕННЫЕ[вариант].get((essm, ssm))
        if имя:
            имена.append(f"{имя} (вариант {вариант})")
    return "; ".join(имена) or "неизвестный уровень"


def esmc(р: Разбор, м: int, конец: int) -> bool:
    """OSSP (IEEE 802.3, прил. 57B) с OUI ITU-T и подтипом 1 — ESMC (ITU-T G.8264, 11.3.1).

    За подтипом ITU-T — версия (1) с флагом события, 3 байта резерва, TLV QL (тип 1,
    длина 4, код SSM в младших 4 битах) и необязательный TLV расширенного QL (тип 2,
    длина 20: eSSM, clockIdentity SyncE, флаги, число eEEC и EEC в цепочке, резерв).
    """
    д = р.д
    if м + 14 > конец or д[м + 1:м + 4] != b"\x00\x19\xa7" or u16(д, м + 4) != 1:
        return False
    if д[м + 6] >> 4 != 1 or д[м + 10] != 1 or u16(д, м + 11) != 4:
        return False
    событие = bool(д[м + 6] & 0x08)
    ssm = д[м + 13] & 0x0F
    расширенный = м + 34 <= конец and д[м + 14] == 2 and u16(д, м + 15) == 20
    essm = д[м + 17] if расширенный else None
    у = р.уровень("ESMC", "Ethernet Synchronization Messaging Channel (ITU-T G.8264)", м)
    у.поле("Подтип", "slow.subtype", "10 (OSSP)", м, 1, 0x0A)
    у.поле("OUI", "ossp.oui", "00:19:a7 (ITU-T)", м + 1, 3, 0x0019A7)
    у.поле("Подтип ITU-T", "ossp.itu.subtype", "0x0001 (ESMC)", м + 4, 2, 1)
    у.поле("Версия", "ossp.esmc.version", 1, м + 6, 1)
    у.поле("Флаг события", "ossp.esmc.event_flag",
           "1 (срочное сообщение о событии)" if событие else "0 (информационное)", м + 6, 1, int(событие))
    резерв = [(м + 6, 1, д[м + 6] & 0x07, "ossp.esmc.reserved_bits"),
              (м + 7, 3, int.from_bytes(д[м + 7:м + 10], "big"), "ossp.esmc.reserved")]
    for место, дл, значение, ключ in резерв:
        п = у.поле("Резерв", ключ, f"0x{значение:0{2 * дл}x}", место, дл, значение)
        if значение:
            п.плохо = True
            р.ошибка("ESMC: резерв не нулевой (G.8264: передатчик ставит нули)")
    т = у.поле("TLV QL", "ossp.esmc.tlv", "Quality Level", м + 10, 4)
    у.поле("Тип TLV", "ossp.esmc.tlv_type", "1 (Quality Level)", м + 10, 1, 1, родитель=т)
    у.поле("Длина TLV", "ossp.esmc.tlv_length", 4, м + 11, 2, родитель=т)
    у.поле("Код SSM", "ossp.esmc.tlv_ql_ssm", f"0x{ssm:x}", м + 13, 1, ssm, родитель=т)
    if д[м + 13] & 0xF0:
        р.ошибка("ESMC: старшие биты байта SSM не нулевые")
    длина = 14
    if расширенный:
        т = у.поле("TLV расширенного QL", "ossp.esmc.tlv", "Extended Quality Level", м + 14, 20)
        у.поле("Тип TLV", "ossp.esmc.tlv_type", "2 (Extended Quality Level)", м + 14, 1, 2, родитель=т)
        у.поле("Длина TLV", "ossp.esmc.tlv_length", 20, м + 15, 2, родитель=т)
        у.поле("Код eSSM", "ossp.esmc.tlv_ext_ql_essm", f"0x{essm:02x}", м + 17, 1, essm, родитель=т)
        у.поле("clockIdentity SyncE", "ossp.esmc.tlv_ext_ql_clockid", д[м + 18:м + 26].hex(":"), м + 18, 8,
               родитель=т)
        флаги = д[м + 26]
        у.поле("Неполная цепочка", "ossp.esmc.tlv_ext_ql_flag_chain", "да" if флаги & 0x02 else "нет",
               м + 26, 1, int(bool(флаги & 0x02)), родитель=т)
        у.поле("Смешанные EEC/eEEC", "ossp.esmc.tlv_ext_ql_flag_mixed", "да" if флаги & 0x01 else "нет",
               м + 26, 1, int(bool(флаги & 0x01)), родитель=т)
        у.поле("eEEC в цепочке", "ossp.esmc.tlv_ext_ql_eeec", д[м + 27], м + 27, 1, родитель=т)
        у.поле("EEC в цепочке", "ossp.esmc.tlv_ext_ql_eec", д[м + 28], м + 28, 1, родитель=т)
        длина = 34
    уровень = esmc_уровень(ssm, essm)
    у.поле("Уровень качества", "ossp.esmc.ql", уровень, м + 13, 1, ssm)
    у.длина = длина
    у.итог = ("событие" if событие else "информационное") + f", SSM 0x{ssm:x}: {уровень}"
    р.п.инфо = "ESMC " + у.итог
    return True


def lacp(р: Разбор, м: int, конец: int) -> bool:
    """LACPDU (IEEE 802.1AX-2014, 6.4.2.3): 110 байт — TLV актора, партнёра, коллектора, терминатор."""
    д = р.д
    if м + 110 > _предел(р, конец) or м + 60 > конец:
        return False
    версия = д[м + 1]
    if версия not in (1, 2):
        return False
    if (д[м + 2], д[м + 3], д[м + 22], д[м + 23], д[м + 42], д[м + 43]) != (1, 20, 2, 20, 3, 16):
        return False
    if версия == 1 and (д[м + 58], д[м + 59]) != (0, 0):
        return False
    у = р.уровень("LACP", "Link Aggregation Control Protocol", м)
    у.поле("Подтип", "slow.subtype", "1 (LACP)", м, 1, 1)
    у.поле("Версия", "lacp.version", версия, м + 1, 1)
    итоги = []
    for имя, ключ, место in (("Актор", "actor", м + 2), ("Партнёр", "partner", м + 22)):
        система, порт, ключ_агр = mac(д, место + 4), u16(д, место + 14), u16(д, место + 10)
        п = у.поле(f"{имя}: система {u16(д, место + 2)}/{система}, ключ {ключ_агр}, порт {порт}",
                   f"lacp.{ключ}", система, место, 20)
        у.поле("Тип TLV", "lacp.tlv_type", д[место], место, 1, родитель=п)
        у.поле("Длина TLV", "lacp.tlv_length", д[место + 1], место + 1, 1, родитель=п)
        у.поле("Приоритет системы", f"lacp.{ключ}.sys_priority", u16(д, место + 2), место + 2, 2, родитель=п)
        у.поле("Система", f"lacp.{ключ}.sysid", система, место + 4, 6, родитель=п)
        у.поле("Ключ", f"lacp.{ключ}.key", ключ_агр, место + 10, 2, родитель=п)
        у.поле("Приоритет порта", f"lacp.{ключ}.port_priority", u16(д, место + 12), место + 12, 2, родитель=п)
        у.поле("Порт", f"lacp.{ключ}.port", порт, место + 14, 2, родитель=п)
        состояние = д[место + 16]
        с = у.поле("Состояние", f"lacp.{ключ}.state", f"0x{состояние:02x}", место + 16, 1, состояние, родитель=п)
        поднятые = _биты(у, с, место + 16, 1, состояние, LACP_СОСТОЯНИЕ, f"lacp.{ключ}.state")
        с.текст = f"0x{состояние:02x} ({', '.join(поднятые) or 'нет'})"
        у.поле("Резерв", "", д[место + 17:место + 20].hex(), место + 17, 3, родитель=п)
        итоги.append(f"{имя.lower()} {система} порт {порт} ключ {ключ_агр}")
    п = у.поле("Коллектор", "lacp.collector", u16(д, м + 44), м + 42, 16)
    у.поле("Наибольшая задержка, ×10 мкс", "lacp.collector.max_delay", u16(д, м + 44), м + 44, 2, родитель=п)
    у.поле("Терминатор", "lacp.terminator", f"{д[м + 58]}/{д[м + 59]}", м + 58, 2)
    у.длина = 110
    у.итог = "; ".join(итоги)
    р.п.инфо = "LACP " + у.итог
    return True


def marker(р: Разбор, м: int, конец: int) -> bool:
    """Marker PDU (IEEE 802.1AX-2014, 6.5.3.3): TLV Marker Information/Response, 110 байт."""
    д = р.д
    if м + 110 > _предел(р, конец) or м + 20 > конец:
        return False
    if д[м + 1] != 1 or д[м + 2] not in (1, 2) or д[м + 3] != 16 or (д[м + 18], д[м + 19]) != (0, 0):
        return False
    вид = {1: "Marker Information", 2: "Marker Response"}[д[м + 2]]
    у = р.уровень("Marker", "Link Aggregation Marker Protocol", м)
    у.поле("Подтип", "slow.subtype", "2 (Marker)", м, 1, 2)
    у.поле("Версия", "marker.version", 1, м + 1, 1)
    у.поле("Тип TLV", "marker.tlv_type", f"{д[м + 2]} ({вид})", м + 2, 1, д[м + 2])
    у.поле("Длина TLV", "marker.tlv_length", 16, м + 3, 1)
    у.поле("Порт запросившего", "marker.requester_port", u16(д, м + 4), м + 4, 2)
    у.поле("Система запросившего", "marker.requester_system", mac(д, м + 6), м + 6, 6)
    у.поле("Номер транзакции", "marker.requester_transaction_id", u32(д, м + 12), м + 12, 4)
    у.длина = 110
    у.итог = f"{вид}, порт {u16(д, м + 4)}, транзакция {u32(д, м + 12)}"
    р.п.инфо = "Marker " + у.итог
    return True


def oam(р: Разбор, м: int, конец: int) -> bool:
    """OAMPDU (IEEE 802.3-2018, 57.4.2): флаги (биты 7–15 — резерв), код; Information — TLV (57.5.2)."""
    д = р.д
    if м + 4 > конец:
        return False
    флаги, код = u16(д, м + 1), д[м + 3]
    # Local/Remote Stable и Evaluating вместе — значение 0x3, передавать запрещено (57.4.2.1).
    if флаги & 0xFF80 or код not in OAM_КОДЫ or флаги & 0x18 == 0x18 or флаги & 0x60 == 0x60:
        return False
    tlv = []
    if код == 0:
        место = м + 4
        while место + 2 <= конец:
            тип, дл = д[место], д[место + 1]
            if тип == 0:
                break
            if тип not in OAM_TLV or дл < 2 or место + дл > конец or (тип in (1, 2) and дл != 16):
                return False
            tlv.append((место, тип, дл))
            место += дл
    у = р.уровень("OAM", "IEEE 802.3 Ethernet OAM", м)
    у.поле("Подтип", "slow.subtype", "3 (OAM)", м, 1, 3)
    п = у.поле("Флаги", "oampdu.flags", f"0x{флаги:04x}", м + 1, 2, флаги)
    поднятые = _биты(у, п, м + 1, 2, флаги, OAM_ФЛАГИ, "oampdu.flags")
    п.текст = f"0x{флаги:04x} ({', '.join(поднятые) or 'нет'})"
    у.поле("Код", "oampdu.code", f"0x{код:02x} ({OAM_КОДЫ[код]})", м + 3, 1, код)
    for место, тип, дл in tlv:
        т = у.поле(OAM_TLV[тип], "oampdu.tlv.type", тип, место, дл)
        у.поле("Длина", "oampdu.tlv.length", дл, место + 1, 1, родитель=т)
        if тип in (1, 2):
            у.поле("Версия OAM", "oampdu.info.version", д[место + 2], место + 2, 1, родитель=т)
            у.поле("Ревизия", "oampdu.info.revision", u16(д, место + 3), место + 3, 2, родитель=т)
            у.поле("Состояние", "oampdu.info.state", f"0x{д[место + 5]:02x}", место + 5, 1, д[место + 5], родитель=т)
            у.поле("Конфигурация OAM", "oampdu.info.oamconfig", f"0x{д[место + 6]:02x}", место + 6, 1,
                   д[место + 6], родитель=т)
            у.поле("Наибольший OAMPDU", "oampdu.info.oampduconfig", u16(д, место + 7) & 0x7FF, место + 7, 2,
                   родитель=т)
            у.поле("OUI", "oampdu.info.oui", д[место + 9:место + 12].hex(":"), место + 9, 3, родитель=т)
    у.длина = 4
    у.итог = OAM_КОДЫ[код] + (f" [{', '.join(поднятые)}]" if поднятые else "")
    р.п.инфо = "OAM " + у.итог
    return True


# -- PTP: IEEE 1588-2008, 13 ------------------------------------------------------------------------

PTP_ТИПЫ = {0x0: "Sync", 0x1: "Delay_Req", 0x2: "Pdelay_Req", 0x3: "Pdelay_Resp", 0x8: "Follow_Up",
            0x9: "Delay_Resp", 0xA: "Pdelay_Resp_Follow_Up", 0xB: "Announce", 0xC: "Signaling",
            0xD: "Management"}
#: Наименьшая длина сообщения (13.6–13.13): заголовок 34 байта и тело.
PTP_МИН = {0x0: 44, 0x1: 44, 0x2: 54, 0x3: 54, 0x8: 44, 0x9: 54, 0xA: 54, 0xB: 64, 0xC: 44, 0xD: 48}
PTP_СОБЫТИЯ = {0x0, 0x1, 0x2, 0x3}
PTP_ФЛАГИ = ((0x0100, "alternateMasterFlag", "alternatemaster"), (0x0200, "twoStepFlag", "twostep"),
             (0x0400, "unicastFlag", "unicast"), (0x0001, "leap61", "li61"), (0x0002, "leap59", "li59"),
             (0x0004, "currentUtcOffsetValid", "utcoffsetvalid"), (0x0008, "ptpTimescale", "ptptimescale"),
             (0x0010, "timeTraceable", "timetraceable"), (0x0020, "frequencyTraceable", "frequencytraceable"))
PTP_ИСТОЧНИКИ = {0x10: "ATOMIC_CLOCK", 0x20: "GPS", 0x30: "TERRESTRIAL_RADIO", 0x40: "PTP", 0x50: "NTP",
                 0x60: "HAND_SET", 0x90: "OTHER", 0xA0: "INTERNAL_OSCILLATOR"}
PTP_ДЕЙСТВИЯ = {0: "GET", 1: "SET", 2: "RESPONSE", 3: "COMMAND", 4: "ACKNOWLEDGE"}


def _ptp_метка(р: Разбор, у, место: int, имя: str, ключ: str) -> str:
    """Timestamp (5.3.3): секунды — 48 бит, наносекунды — 32 бита."""
    р.нужно(место, 10)
    сек, нс = (u16(р.д, место) << 32) | u32(р.д, место + 2), u32(р.д, место + 6)
    текст = f"{сек}.{нс:09d}"
    п = у.поле(имя, ключ, текст + " с", место, 10, текст)
    у.поле("Секунды", ключ + ".seconds", сек, место, 6, родитель=п)
    у.поле("Наносекунды", ключ + ".nanoseconds", нс, место + 6, 4, родитель=п)
    return текст


def _ptp_порт(р: Разбор, у, место: int, имя: str, ключ: str) -> str:
    """PortIdentity (5.3.5): clockIdentity (8 байт) и portNumber."""
    р.нужно(место, 10)
    часы, порт = р.д[место:место + 8].hex(":"), u16(р.д, место + 8)
    п = у.поле(имя, ключ, f"{часы} / {порт}", место, 10)
    у.поле("clockIdentity", ключ + ".clockidentity", часы, место, 8, родитель=п)
    у.поле("portNumber", ключ + ".portnumber", порт, место + 8, 2, родитель=п)
    return часы


@_надёжно
def ptp(р: Разбор, м: int, конец: int) -> bool:
    """Сообщение PTPv2 (IEEE 1588-2008, 13): общий заголовок 34 байта (13.3) и тело по типу."""
    д = р.д
    if м + 34 > конец:
        return False
    тип, версия, длина = д[м] & 0x0F, д[м + 1] & 0x0F, u16(д, м + 2)
    if версия != 2 or тип not in PTP_ТИПЫ or длина < PTP_МИН[тип] or м + длина > _предел(р, конец):
        return False
    # Прил. D: события — на порт 319, общие сообщения — на 320.
    if р.п.уровни and р.п.уровни[-1].протокол == "UDP":
        if (р.п.порт_к == 319 and тип not in PTP_СОБЫТИЯ) or (р.п.порт_к == 320 and тип in PTP_СОБЫТИЯ):
            return False
    # Наносекунды отметки времени меньше 10^9 (5.3.3).
    if тип not in (0xC, 0xD) and м + 44 <= конец and u32(д, м + 40) >= 10 ** 9:
        return False
    имя = PTP_ТИПЫ[тип]
    у = р.уровень("PTP", "Precision Time Protocol (IEEE 1588-2008)", м)
    у.поле("transportSpecific", "ptp.v2.transportspecific", д[м] >> 4, м, 1)
    у.поле("Тип сообщения", "ptp.v2.messagetype", f"0x{тип:x} ({имя})", м, 1, тип)
    у.поле("Версия PTP", "ptp.v2.versionptp", версия, м + 1, 1)
    у.поле("Длина сообщения", "ptp.v2.messagelength", длина, м + 2, 2)
    у.поле("Домен", "ptp.v2.domainnumber", д[м + 4], м + 4, 1)
    флаги = u16(д, м + 6)
    п = у.поле("Флаги", "ptp.v2.flags", f"0x{флаги:04x}", м + 6, 2, флаги)
    поднятые = _биты(у, п, м + 6, 2, флаги, PTP_ФЛАГИ, "ptp.v2.flags")
    п.текст = f"0x{флаги:04x} ({', '.join(поднятые) or 'нет'})"
    поправка = struct.unpack_from(">q", д, м + 8)[0] / 65536
    у.поле("Поправка, нс", "ptp.v2.correction.ns", f"{поправка:g}", м + 8, 8, поправка)
    часы = _ptp_порт(р, у, м + 20, "Порт источника", "ptp.v2.sourceportidentity")
    у.поле("", "ptp.v2.clockidentity", часы, м + 20, 0)
    номер = u16(д, м + 30)
    у.поле("sequenceId", "ptp.v2.sequenceid", номер, м + 30, 2)
    у.поле("controlField", "ptp.v2.control", д[м + 32], м + 32, 1)
    у.поле("logMessageInterval", "ptp.v2.logmessageperiod", struct.unpack_from(">b", д, м + 33)[0], м + 33, 1)
    у.длина = длина
    у.итог = f"{имя}, seq {номер}, домен {д[м + 4]}"
    р.п.инфо = "PTP " + у.итог
    т = м + 34
    if тип in (0x0, 0x1):
        у.итог += ", " + _ptp_метка(р, у, т, "originTimestamp", "ptp.v2.sdr.origintimestamp")
    elif тип == 0x8:
        у.итог += ", " + _ptp_метка(р, у, т, "preciseOriginTimestamp", "ptp.v2.fu.preciseorigintimestamp")
    elif тип == 0x9:
        у.итог += ", " + _ptp_метка(р, у, т, "receiveTimestamp", "ptp.v2.dr.receivetimestamp")
        _ptp_порт(р, у, т + 10, "requestingPortIdentity", "ptp.v2.dr.requestingportidentity")
    elif тип == 0x2:
        _ptp_метка(р, у, т, "originTimestamp", "ptp.v2.pdrq.origintimestamp")
    elif тип == 0x3:
        _ptp_метка(р, у, т, "requestReceiptTimestamp", "ptp.v2.pdrs.requestreceipttimestamp")
        _ptp_порт(р, у, т + 10, "requestingPortIdentity", "ptp.v2.pdrs.requestingportidentity")
    elif тип == 0xA:
        _ptp_метка(р, у, т, "responseOriginTimestamp", "ptp.v2.pdfu.responseorigintimestamp")
        _ptp_порт(р, у, т + 10, "requestingPortIdentity", "ptp.v2.pdfu.requestingportidentity")
    elif тип == 0xB:
        _ptp_метка(р, у, т, "originTimestamp", "ptp.v2.an.origintimestamp")
        р.нужно(т, 30)
        у.поле("currentUtcOffset", "ptp.v2.an.origincurrentutcoffset", struct.unpack_from(">h", д, т + 10)[0],
               т + 10, 2)
        у.поле("grandmasterPriority1", "ptp.v2.an.priority1", д[т + 13], т + 13, 1)
        к = у.поле("grandmasterClockQuality", "ptp.v2.an.grandmasterclockquality",
                   f"класс {д[т + 14]}, точность 0x{д[т + 15]:02x}, дисперсия 0x{u16(д, т + 16):04x}", т + 14, 4)
        у.поле("clockClass", "ptp.v2.an.grandmasterclockclass", д[т + 14], т + 14, 1, родитель=к)
        у.поле("clockAccuracy", "ptp.v2.an.grandmasterclockaccuracy", f"0x{д[т + 15]:02x}", т + 15, 1,
               д[т + 15], родитель=к)
        у.поле("offsetScaledLogVariance", "ptp.v2.an.grandmasterclockvariance", u16(д, т + 16), т + 16, 2,
               родитель=к)
        у.поле("grandmasterPriority2", "ptp.v2.an.priority2", д[т + 18], т + 18, 1)
        гм = д[т + 19:т + 27].hex(":")
        у.поле("grandmasterIdentity", "ptp.v2.an.grandmasterclockidentity", гм, т + 19, 8)
        у.поле("stepsRemoved", "ptp.v2.an.localstepsremoved", u16(д, т + 27), т + 27, 2)
        у.поле("timeSource", "ptp.v2.timesource", f"0x{д[т + 29]:02x} ({PTP_ИСТОЧНИКИ.get(д[т + 29], '?')})",
               т + 29, 1, д[т + 29])
        у.итог += f", GM {гм}, приоритеты {д[т + 13]}/{д[т + 18]}, класс {д[т + 14]}"
    elif тип == 0xC:
        _ptp_порт(р, у, т, "targetPortIdentity", "ptp.v2.sig.targetportidentity")
    elif тип == 0xD:
        _ptp_порт(р, у, т, "targetPortIdentity", "ptp.v2.mm.targetportidentity")
        р.нужно(т + 10, 4)
        у.поле("startingBoundaryHops", "ptp.v2.mm.startingboundaryhops", д[т + 10], т + 10, 1)
        у.поле("boundaryHops", "ptp.v2.mm.boundaryhops", д[т + 11], т + 11, 1)
        действие = д[т + 12] & 0x0F
        у.поле("actionField", "ptp.v2.mm.action", f"{действие} ({PTP_ДЕЙСТВИЯ.get(действие, '?')})", т + 12, 1,
               действие)
    р.п.инфо = "PTP " + у.итог
    return True


# -- MACsec: IEEE 802.1AE-2018, 9 -------------------------------------------------------------------

MACSEC_ICV = 16          # ICV набора шифров по умолчанию (GCM-AES-128/256), 14.5–14.6


@_надёжно
def macsec(р: Разбор, м: int, конец: int) -> bool:
    """SecTAG (802.1AE, 9.3): TCI/AN, SL, PN, SCI (при SC=1); проверки — по 10.6 (приём)."""
    д = р.д
    if м + 6 > конец:
        return False
    tci, sl_байт = д[м], д[м + 1]
    v, es, sc, scb, e, c, an = tci >> 7, (tci >> 6) & 1, (tci >> 5) & 1, (tci >> 4) & 1, (tci >> 3) & 1, \
        (tci >> 2) & 1, tci & 3
    sl = sl_байт & 0x3F
    # V=0; биты 7–8 октета SL — нули; при SC=1 ES и SCB сброшены; SL < 48.
    if v or sl_байт & 0xC0 or (sc and (es or scb)) or sl >= 48:
        return False
    метка = 14 if sc else 6
    всего = _предел(р, конец) - м
    if sl:
        # Короткий кадр: за SecTAG ровно SL байт данных и ICV — или добивка до 46 байт кадра Ethernet.
        нужно = метка + sl + MACSEC_ICV
        if всего != нужно and not (нужно < 46 and всего == 46):
            return False
    elif всего < метка + 48 + MACSEC_ICV:
        return False
    у = р.уровень("MACsec", "IEEE 802.1AE MAC Security", м)
    п = у.поле("TCI", "macsec.tci", f"0x{tci & 0xFC:02x}", м, 1, tci & 0xFC)
    for имя, ключ, бит in (("Версия (V)", "v", v), ("ES (конечная станция)", "es", es), ("SC (есть SCI)", "sc", sc),
                           ("SCB (EPON)", "scb", scb), ("E (зашифровано)", "e", e), ("C (текст изменён)", "c", c)):
        у.поле(имя, f"macsec.tci.{ключ}", бит, м, 1, родитель=п)
    у.поле("AN", "macsec.an", an, м, 1)
    у.поле("Короткая длина (SL)", "macsec.sl", sl, м + 1, 1)
    pn = u32(д, м + 2)
    у.поле("Номер пакета (PN)", "macsec.pn", pn, м + 2, 4)
    у.итог = f"AN {an}, PN {pn}"
    if sc:
        р.нужно(м + 6, 8)
        система, порт = mac(д, м + 6), u16(д, м + 12)
        s = у.поле("SCI", "macsec.sci", f"{система}/{порт}", м + 6, 8)
        у.поле("Система", "macsec.sci.system_identifier", система, м + 6, 6, родитель=s)
        у.поле("Порт", "macsec.sci.port_identifier", порт, м + 12, 2, родитель=s)
        у.итог += f", SCI {система}/{порт}"
    у.длина = метка
    начало = м + метка
    конец_данных = начало + sl if sl else _предел(р, конец) - MACSEC_ICV
    if конец_данных + MACSEC_ICV <= len(д):
        у.поле("ICV", "macsec.icv", д[конец_данных:конец_данных + MACSEC_ICV].hex(), конец_данных, MACSEC_ICV)
    if not e and not c:
        # Только целостность: данные открыты и начинаются с исходного EtherType (8.1).
        р.нужно(начало, 2)
        тип = u16(д, начало)
        у.поле("Тип (открытые данные)", "macsec.etype", f"0x{тип:04x}", начало, 2, тип)
        у.итог += ", только целостность"
        по_типу(р, тип, начало + 2)
        return True
    дл = max(0, min(конец_данных, len(д)) - начало)
    у.поле("Зашифрованные данные", "macsec.encrypted_data", f"{конец_данных - начало} байт", начало, дл,
           конец_данных - начало)
    у.итог += ", зашифровано"
    р.п.инфо = "MACsec " + у.итог
    р.нагрузка(начало, min(конец_данных, len(д)))
    if конец_данных + MACSEC_ICV > len(д):
        raise Мало()
    return True


# -- PBB: IEEE 802.1Q-2018, 9.7 ---------------------------------------------------------------------

@_надёжно
def pbb(р: Разбор, м: int, конец: int) -> bool:
    """I-TAG (802.1Q-2018, 9.7): I-PCP, I-DEI, UCA, Res1, Res2, I-SID; за ним — кадр клиента (C-DA, C-SA)."""
    д = р.д
    if м + 4 + 14 > конец:
        return False
    tci = u32(д, м)
    if (tci >> 24) & 0x03:                               # Res2 — нули
        return False
    if д[м + 10] & 0x01:                                 # C-SA — индивидуальный адрес (IEEE 802, 8.2)
        return False
    внутр = u16(д, м + 16)
    # Полей для проверки у I-TAG почти нет: кадр клиента должен нести известный EtherType
    # или верную длину 802.3 — иначе это не PBB, а случайные байты.
    длина_802_3 = 3 <= внутр <= 1500 and м + 18 + внутр <= _предел(р, конец)
    if not (внутр in ETHERTYPE or внутр in ДОП_ETHERTYPE or длина_802_3):
        return False
    у = р.уровень("PBB", "IEEE 802.1ah Provider Backbone Bridge (I-TAG)", м)
    isid = tci & 0xFFFFFF
    п = у.поле("I-TAG TCI", "ieee8021ah.tci", f"0x{tci:08x}", м, 4, tci)
    у.поле("I-PCP", "ieee8021ah.priority", tci >> 29, м, 1, родитель=п)
    у.поле("I-DEI", "ieee8021ah.drop", (tci >> 28) & 1, м, 1, родитель=п)
    у.поле("UCA", "ieee8021ah.nca", (tci >> 27) & 1, м, 1, родитель=п)
    у.поле("Res1", "ieee8021ah.res1", (tci >> 26) & 1, м, 1, родитель=п)
    у.поле("Res2", "ieee8021ah.res2", (tci >> 24) & 3, м, 1, родитель=п)
    у.поле("I-SID", "ieee8021ah.isid", isid, м + 1, 3)
    у.поле("", "ieee8021ah.c_daddr", mac(д, м + 4), м + 4, 0)
    у.поле("", "ieee8021ah.c_saddr", mac(д, м + 10), м + 10, 0)
    у.длина = 4
    у.итог = f"I-SID {isid}, приоритет {tci >> 29}"
    ethernet(р, м + 4)
    return True


# -- FCoE и FIP: FC-BB-5; кадр Fibre Channel — FC-FS-3 ---------------------------------------------

FC_SOF = {0x28: "SOFf", 0x2D: "SOFi2", 0x35: "SOFn2", 0x2E: "SOFi3", 0x36: "SOFn3", 0x29: "SOFi4",
          0x31: "SOFn4", 0x39: "SOFc4"}
FC_EOF = {0x41: "EOFn", 0x42: "EOFt", 0x44: "EOFrt", 0x46: "EOFdt", 0x49: "EOFni", 0x4E: "EOFdti",
          0x4F: "EOFrti", 0x50: "EOFa"}
FC_ТИПЫ = {0x00: "BLS", 0x01: "ELS", 0x08: "FCP (SCSI)", 0x20: "FC-CT", 0x22: "SW_ILS"}
FC_МАРШРУТ = {0x0: "Device_Data", 0x2: "Extended_Link_Data", 0x8: "Basic_Link_Data", 0xC: "Link_Control"}


def _fc_id(д: bytes, м: int) -> str:
    return ".".join(f"{б:02x}" for б in д[м:м + 3])


@_надёжно
def fcoe(р: Разбор, м: int, конец: int) -> bool:
    """Кадр FCoE (FC-BB-5, 7): версия 0 и 100 бит резерва, SOF, кадр FC, CRC, EOF и 3 байта резерва."""
    д = р.д
    if м + 14 + 24 > конец:
        return False
    if д[м] != 0 or any(д[м + 1:м + 13]) or д[м + 13] not in FC_SOF:
        return False
    обрезан = _предел(р, конец) > конец
    if not обрезан and (конец - м < 14 + 24 + 4 + 4 or д[конец - 4] not in FC_EOF or any(д[конец - 3:конец])):
        return False
    sof = д[м + 13]
    у = р.уровень("FCoE", "Fibre Channel over Ethernet", м)
    у.поле("Версия", "fcoe.ver", 0, м, 1)
    у.поле("SOF", "fcoe.sof", f"0x{sof:02x} ({FC_SOF[sof]})", м + 13, 1, sof)
    у.длина = 14
    у.итог = FC_SOF[sof]
    if not обрезан:
        crc_место, eof = конец - 8, д[конец - 4]
        crc = _le32(д, crc_место)
        верна = zlib.crc32(д[м + 14:crc_место]) & 0xFFFFFFFF == crc
        у.поле("CRC кадра FC", "fcoe.crc", f"0x{u32(д, crc_место):08x} [{'верна' if верна else 'НЕВЕРНА'}]",
               crc_место, 4, u32(д, crc_место), плохо=not верна)
        if not верна:
            р.ошибка("FCoE: неверная CRC кадра Fibre Channel")
        у.поле("EOF", "fcoe.eof", f"0x{eof:02x} ({FC_EOF[eof]})", конец - 4, 1, eof)
        у.итог += f" … {FC_EOF[eof]}"
    fc(р, м + 14, конец - 8 if not обрезан else len(д))
    if обрезан:
        raise Мало()
    return True


def fc(р: Разбор, м: int, конец: int) -> None:
    """Заголовок кадра Fibre Channel (FC-FS-3, 9): 24 байта."""
    д = р.д
    у = р.уровень("FC", "Fibre Channel", м)
    р.нужно(м, 24)
    r_ctl, тип = д[м], д[м + 8]
    d_id, s_id = _fc_id(д, м + 1), _fc_id(д, м + 5)
    п = у.поле("R_CTL", "fc.r_ctl", f"0x{r_ctl:02x} ({FC_МАРШРУТ.get(r_ctl >> 4, 'маршрут ' + hex(r_ctl >> 4))})",
               м, 1, r_ctl)
    у.поле("Маршрутизация", "fc.r_ctl.routing", r_ctl >> 4, м, 1, родитель=п)
    у.поле("Сведения", "fc.r_ctl.information", r_ctl & 15, м, 1, родитель=п)
    у.поле("D_ID", "fc.d_id", d_id, м + 1, 3)
    у.поле("CS_CTL/приоритет", "fc.cs_ctl", f"0x{д[м + 4]:02x}", м + 4, 1, д[м + 4])
    у.поле("S_ID", "fc.s_id", s_id, м + 5, 3)
    у.поле("TYPE", "fc.type", f"0x{тип:02x} ({FC_ТИПЫ.get(тип, 'неизвестный')})", м + 8, 1, тип)
    у.поле("F_CTL", "fc.f_ctl", f"0x{u24(д, м + 9):06x}", м + 9, 3, u24(д, м + 9))
    у.поле("SEQ_ID", "fc.seq_id", д[м + 12], м + 12, 1)
    у.поле("DF_CTL", "fc.df_ctl", f"0x{д[м + 13]:02x}", м + 13, 1, д[м + 13])
    у.поле("SEQ_CNT", "fc.seq_cnt", u16(д, м + 14), м + 14, 2)
    у.поле("OX_ID", "fc.ox_id", f"0x{u16(д, м + 16):04x}", м + 16, 2, u16(д, м + 16))
    у.поле("RX_ID", "fc.rx_id", f"0x{u16(д, м + 18):04x}", м + 18, 2, u16(д, м + 18))
    у.поле("Параметр", "fc.parameter", f"0x{u32(д, м + 20):08x}", м + 20, 4, u32(д, м + 20))
    у.длина = 24
    у.итог = f"{s_id} → {d_id}, {FC_ТИПЫ.get(тип, f'тип 0x{тип:02x}')}, OX_ID 0x{u16(д, м + 16):04x}"
    р.п.инфо = "FC " + у.итог
    if конец > м + 24:
        данные(р, м + 24, "нагрузка кадра FC", конец)
        р.п.инфо = "FC " + у.итог


FIP_ОПЕРАЦИИ = {1: ("Discovery", {1: "Solicitation", 2: "Advertisement"}),
                2: ("Virtual Link Instantiation", {1: "Request", 2: "Reply"}),
                3: ("Keep Alive / Clear Virtual Links", {1: "Keep Alive", 2: "Clear Virtual Links"}),
                4: ("VLAN", {1: "Request", 2: "Notification"})}
FIP_ФЛАГИ = ((0x8000, "FP", "fpma"), (0x4000, "SP", "spma"), (0x0004, "A (доступен)", "available"),
             (0x0002, "S (по запросу)", "solicited"), (0x0001, "F (FCF)", "fport"))
FIP_ДЕСКРИПТОРЫ = {1: "Priority", 2: "MAC Address", 3: "FC-MAP", 4: "Name_Identifier", 5: "Fabric",
                   6: "Max FCoE Size", 7: "FLOGI", 8: "FDISC", 9: "LOGO", 10: "ELP",
                   11: "Vx_Port Identification", 12: "FKA_ADV_Period", 13: "Vendor_ID", 14: "VLAN"}


@_надёжно
def fip(р: Разбор, м: int, конец: int) -> bool:
    """FIP (FC-BB-5, 7): версия 1, код операции, подкод, длина списка (слова), флаги, дескрипторы TLV."""
    д = р.д
    if м + 10 > конец:
        return False
    код, подкод, слов = u16(д, м + 2), д[м + 5], u16(д, м + 6)
    if д[м] != 0x10 or д[м + 1] != 0 or д[м + 4] != 0:
        return False
    if код in FIP_ОПЕРАЦИИ:
        if подкод not in FIP_ОПЕРАЦИИ[код][1]:
            return False
    elif not 0xFFF8 <= код <= 0xFFFE:
        return False
    край = м + 10 + 4 * слов
    if край > _предел(р, конец):
        return False
    дескрипторы = []
    место = м + 10
    while место < min(край, конец):
        if место + 2 > конец:
            break
        тип, дл = д[место], д[место + 1] * 4
        if дл == 0 or место + дл > край:
            return False
        дескрипторы.append((место, тип, дл))
        место += дл
    у = р.уровень("FIP", "FCoE Initialization Protocol", м)
    имя_оп, подкоды = FIP_ОПЕРАЦИИ.get(код, ("по заказу производителя", {}))
    у.поле("Версия", "fip.ver", 1, м, 1)
    у.поле("Код операции", "fip.opcode", f"0x{код:04x} ({имя_оп})", м + 2, 2, код)
    у.поле("Подкод", "fip.subcode", f"{подкод} ({подкоды.get(подкод, '?')})", м + 5, 1, подкод)
    у.поле("Длина дескрипторов, слов", "fip.dl_len", слов, м + 6, 2)
    флаги = u16(д, м + 8)
    п = у.поле("Флаги", "fip.flags", f"0x{флаги:04x}", м + 8, 2, флаги)
    поднятые = _биты(у, п, м + 8, 2, флаги, FIP_ФЛАГИ, "fip.flags")
    п.текст = f"0x{флаги:04x} ({', '.join(поднятые) or 'нет'})"
    for место, тип, дл in дескрипторы:
        тело = д[место + 2:место + дл]
        if тип == 1 and len(тело) >= 2:
            текст, ключ, сырое = f"приоритет {тело[1]}", "fip.pri", тело[1]
        elif тип == 2 and len(тело) >= 6:
            текст, ключ, сырое = mac(тело, 0), "fip.mac", mac(тело, 0)
        elif тип == 4 and len(тело) >= 10:
            текст, ключ, сырое = тело[2:10].hex(":"), "fip.name", тело[2:10].hex(":")
        elif тип == 6 and len(тело) >= 2:
            текст, ключ, сырое = f"{u16(тело, 0)} байт", "fip.maxsize", u16(тело, 0)
        elif тип == 12 and len(тело) >= 6:
            текст, ключ, сырое = f"{u32(тело, 2)} мс", "fip.fka_adv_period", u32(тело, 2)
        elif тип == 14 and len(тело) >= 2:
            текст, ключ, сырое = f"VLAN {u16(тело, 0) & 0xFFF}", "fip.vlan", u16(тело, 0) & 0xFFF
        else:
            текст, ключ, сырое = тело.hex() or "—", "", None
        т = у.поле(f"{FIP_ДЕСКРИПТОРЫ.get(тип, f'дескриптор {тип}')}: {текст}", "fip.desc_type", тип, место, дл)
        у.поле("Длина, слов", "fip.desc_len", дл // 4, место + 1, 1, родитель=т)
        if ключ:
            у.поле("Значение", ключ, текст, место + 2, дл - 2, сырое, родитель=т)
    у.длина = край - м
    у.итог = f"{имя_оп}: {подкоды.get(подкод, подкод)}"
    р.п.инфо = "FIP " + у.итог
    if край > len(д):
        raise Мало()
    return True


# -- PROFINET: IEC 61158-6-10 -----------------------------------------------------------------------

PN_DCP_ПО_FRAMEID = {0xFEFC: (6,), 0xFEFD: (3, 4), 0xFEFE: (5,), 0xFEFF: (5,)}
PN_FRAMEID = {0xFEFC: "DCP Hello", 0xFEFD: "DCP Get/Set", 0xFEFE: "DCP Identify (запрос)",
              0xFEFF: "DCP Identify (ответ)"}
PN_DCP_СЕРВИСЫ = {3: "Get", 4: "Set", 5: "Identify", 6: "Hello"}
PN_DCP_ТИПЫ = {0: "запрос", 1: "ответ", 5: "ответ: не поддержано"}
PN_DCP_ОПЦИИ = {1: "IP", 2: "Device properties", 3: "DHCP", 5: "Control", 6: "Device Initiative",
                0xFF: "All Selector"}
PN_DCP_ПОДОПЦИИ = {(1, 1): "MAC address", (1, 2): "IP parameter", (1, 3): "Full IP suite",
                   (2, 1): "Type of Station", (2, 2): "NameOfStation", (2, 3): "Device ID", (2, 4): "Device Role",
                   (2, 5): "Device Options", (2, 6): "Alias Name", (5, 1): "Start", (5, 2): "Stop",
                   (5, 3): "Signal", (5, 4): "Response", (0xFF, 0xFF): "All"}
PN_СТАТУС = ((0x01, "State (основной)", "state"), (0x02, "Redundancy", "redundancy"),
             (0x04, "DataValid", "datavalid"), (0x10, "ProviderState (работа)", "providerstate"),
             (0x20, "StationProblemIndicator (норма)", "problemindicator"), (0x80, "Ignore", "ignore"))


def _dcp_годен(р: Разбор, м: int, конец: int, фид: int) -> bool:
    """DCP-PDU: сервис подходит к FrameID, тип известен, блоки ровно заполняют DCPDataLength."""
    д = р.д
    if м + 10 > конец:
        return False
    сервис, тип, дл = д[м], д[м + 1], u16(д, м + 8)
    if сервис not in PN_DCP_ПО_FRAMEID[фид] or тип not in PN_DCP_ТИПЫ or м + 10 + дл > _предел(р, конец):
        return False
    место, край = м + 10, м + 10 + дл
    if сервис == 3 and тип == 0:                        # Get.req — список пар «опция, подопция»
        return дл % 2 == 0
    if край > конец:
        return True                                     # захват оборван — блоки дальше не видны
    while место < край:
        if место + 4 > край:
            return False
        бдл = u16(д, место + 2)
        место += 4 + бдл
        if место > край:
            return False
        if бдл & 1 and место < край:                    # добивка нечётного блока до чётной длины
            место += 1
    return True


@_надёжно
def pn_rt(р: Разбор, м: int, конец: int) -> bool:
    """PROFINET RT: FrameID; DCP (0xFEFC–0xFEFF) или циклические данные RT_CLASS_1/UDP (0x8000–0xFBFF)."""
    д = р.д
    if м + 2 > конец:
        return False
    фид = u16(д, м)
    if фид in PN_DCP_ПО_FRAMEID:
        if not _dcp_годен(р, м + 2, конец, фид):
            return False
        у = р.уровень("PN-RT", "PROFINET Real-Time", м)
        у.поле("FrameID", "pn_rt.frame_id", f"0x{фид:04x} ({PN_FRAMEID[фид]})", м, 2, фид)
        у.длина = 2
        у.итог = PN_FRAMEID[фид]
        pn_dcp(р, м + 2, конец)
        return True
    if not 0x8000 <= фид <= 0xFBFF or _предел(р, конец) > конец:
        return False
    # Циклический кадр: C_SDU 40–1440 байт, затем APDU-статус — CycleCounter, DataStatus,
    # TransferStatus (0 — ошибок нет). Резервные биты DataStatus (3 и 6) — нули.
    n = конец - м
    if not 2 + 40 + 4 <= n <= 2 + 1440 + 4:
        return False
    статус, передача = д[конец - 2], д[конец - 1]
    if передача != 0 or статус & 0x48:
        return False
    у = р.уровень("PN-RT", "PROFINET Real-Time", м)
    у.поле("FrameID", "pn_rt.frame_id", f"0x{фид:04x} (циклические данные RT)", м, 2, фид)
    у.поле("Данные (C_SDU)", "pn_rt.data", f"{n - 6} байт", м + 2, n - 6, д[м + 2:конец - 4].hex())
    счётчик = u16(д, конец - 4)
    у.поле("CycleCounter", "pn_rt.cycle_counter", счётчик, конец - 4, 2)
    п = у.поле("DataStatus", "pn_rt.ds", f"0x{статус:02x}", конец - 2, 1, статус)
    поднятые = _биты(у, п, конец - 2, 1, статус, PN_СТАТУС, "pn_rt.ds")
    п.текст = f"0x{статус:02x} ({', '.join(поднятые) or 'нет'})"
    у.поле("TransferStatus", "pn_rt.transfer_status", передача, конец - 1, 1)
    у.длина = n
    у.итог = f"FrameID 0x{фид:04x}, цикл {счётчик}, {n - 6} байт данных"
    р.п.инфо = "PN-RT " + у.итог
    р.нагрузка(м + 2, конец - 4)
    return True


def pn_dcp(р: Разбор, м: int, конец: int) -> None:
    """DCP-PDU: ServiceID, ServiceType, Xid, ResponseDelay, DCPDataLength и блоки (опция, подопция, длина)."""
    д = р.д
    у = р.уровень("PN-DCP", "PROFINET Discovery and basic Configuration Protocol", м)
    сервис, тип, xid, дл = д[м], д[м + 1], u32(д, м + 2), u16(д, м + 8)
    у.поле("ServiceID", "pn_dcp.service_id", f"{сервис} ({PN_DCP_СЕРВИСЫ[сервис]})", м, 1, сервис)
    у.поле("ServiceType", "pn_dcp.service_type", f"{тип} ({PN_DCP_ТИПЫ[тип]})", м + 1, 1, тип)
    у.поле("Xid", "pn_dcp.xid", f"0x{xid:08x}", м + 2, 4, xid)
    ответ = тип != 0
    if сервис == 5 and not ответ:
        у.поле("ResponseDelay", "pn_dcp.response_delay", u16(д, м + 6), м + 6, 2)
    else:
        у.поле("Резерв", "pn_dcp.reserved16", u16(д, м + 6), м + 6, 2)
    у.поле("DCPDataLength", "pn_dcp.data_length", дл, м + 8, 2)
    у.длина = 10 + дл
    у.итог = f"{PN_DCP_СЕРВИСЫ[сервис]} {PN_DCP_ТИПЫ[тип]}, xid 0x{xid:08x}"
    место, край = м + 10, min(м + 10 + дл, len(д))
    имя_станции = ""
    if сервис == 3 and not ответ:
        while место + 2 <= край:
            опц, подопц = д[место], д[место + 1]
            у.поле(f"Запрошено: {PN_DCP_ПОДОПЦИИ.get((опц, подопц), f'{опц}/{подопц}')}", "pn_dcp.option", опц,
                   место, 2)
            место += 2
    else:
        # BlockInfo — в ответах Get/Identify и в Hello; BlockQualifier — в запросе Set.
        сведения = (ответ and сервис in (3, 5)) or (сервис == 6 and not ответ)
        уточнение = сервис == 4 and not ответ
        while место + 4 <= край:
            опц, подопц, бдл = д[место], д[место + 1], u16(д, место + 2)
            имя = PN_DCP_ПОДОПЦИИ.get((опц, подопц), f"{PN_DCP_ОПЦИИ.get(опц, опц)}/{подопц}")
            б = у.поле(f"Блок {имя}", "pn_dcp.block", имя, место, 4 + бдл)
            у.поле("Опция", "pn_dcp.option", f"{опц} ({PN_DCP_ОПЦИИ.get(опц, '?')})", место, 1, опц, родитель=б)
            у.поле("Подопция", "pn_dcp.suboption", подопц, место + 1, 1, родитель=б)
            у.поле("DCPBlockLength", "pn_dcp.block_length", бдл, место + 2, 2, родитель=б)
            значение = место + 4
            if опц != 5 and (сведения or уточнение) and бдл >= 2:
                у.поле("BlockInfo" if сведения else "BlockQualifier",
                       "pn_dcp.block_info" if сведения else "pn_dcp.block_qualifier", u16(д, значение), значение, 2,
                       родитель=б)
                значение += 2
            тело = д[значение:место + 4 + бдл]
            if (опц, подопц) in ((2, 1), (2, 2), (2, 6)):
                текст = тело.decode("utf-8", "replace")
                ключ = {1: "pn_dcp.suboption_device_typeofstation", 2: "pn_dcp.suboption_device_nameofstation",
                        6: "pn_dcp.suboption_device_aliasname"}[подопц]
                у.поле(PN_DCP_ПОДОПЦИИ[(опц, подопц)], ключ, текст, значение, len(тело), родитель=б)
                б.текст = f"{имя}: {текст}"
                if подопц == 2:
                    имя_станции = текст
            elif (опц, подопц) == (2, 3) and len(тело) >= 4:
                у.поле("VendorID", "pn_dcp.suboption_vendor_id", f"0x{u16(тело, 0):04x}", значение, 2,
                       u16(тело, 0), родитель=б)
                у.поле("DeviceID", "pn_dcp.suboption_device_id", f"0x{u16(тело, 2):04x}", значение + 2, 2,
                       u16(тело, 2), родитель=б)
            elif (опц, подопц) == (1, 1) and len(тело) >= 6:
                у.поле("MAC", "pn_dcp.suboption_ip_mac_address", mac(тело, 0), значение, 6, родитель=б)
            elif (опц, подопц) == (1, 2) and len(тело) >= 12:
                for i, (подпись, ключ) in enumerate((("IP", "ip"), ("Маска", "subnetmask"),
                                                     ("Шлюз", "standard_gateway"))):
                    у.поле(подпись, f"pn_dcp.suboption_ip_{ключ}", ip4(тело, 4 * i), значение + 4 * i, 4, родитель=б)
                б.текст = f"{имя}: {ip4(тело, 0)}/{ip4(тело, 4)}, шлюз {ip4(тело, 8)}"
            elif (опц, подопц) == (5, 4) and len(тело) >= 3:
                у.поле("Ответ на", "pn_dcp.suboption_control_response",
                       f"{тело[0]}/{тело[1]}, ошибка {тело[2]}", значение, 3, родитель=б)
            elif тело:
                у.поле("Данные", "pn_dcp.block_data", тело.hex(), значение, len(тело), родитель=б)
            место += 4 + бдл + (бдл & 1)
    if имя_станции:
        у.итог += f", «{имя_станции}»"
    р.п.инфо = "PN-DCP " + у.итог
    if м + 10 + дл > len(д):
        raise Мало()


# -- EtherCAT: IEC 61158-4-12 (ETG.1000.4) ---------------------------------------------------------

ECAT_КОМАНДЫ = {0: "NOP", 1: "APRD", 2: "APWR", 3: "APRW", 4: "FPRD", 5: "FPWR", 6: "FPRW", 7: "BRD",
                8: "BWR", 9: "BRW", 10: "LRD", 11: "LWR", 12: "LRW", 13: "ARMW", 14: "FRMW"}


@_надёжно
def ethercat(р: Разбор, м: int, конец: int) -> bool:
    """Заголовок EtherCAT (длина 11 бит, резерв, тип 1) и датаграммы: команда, индекс, адрес, длина,
    IRQ, данные, счётчик обработки (WKC). Поля — от младшего байта."""
    д = р.д
    if м + 2 > конец:
        return False
    заголовок = _le16(д, м)
    длина, резерв, тип = заголовок & 0x7FF, (заголовок >> 11) & 1, заголовок >> 12
    if резерв or тип != 1 or длина < 12 or м + 2 + длина > конец:
        return False
    край = м + 2 + длина
    датаграммы = []
    место = м + 2
    while True:
        if место + 10 > край:
            return False
        команда, дл_флаги = д[место], _le16(д, место + 6)
        дл = дл_флаги & 0x7FF
        if команда not in ECAT_КОМАНДЫ or дл_флаги & 0x3800 or место + 12 + дл > край:
            return False
        датаграммы.append((место, команда, дл, дл_флаги))
        место += 12 + дл
        if not дл_флаги >> 15:
            break
    if место != край:
        return False
    у = р.уровень("EtherCAT", "EtherCAT (IEC 61158-4-12)", м)
    п = у.поле("Заголовок", "ecatf", f"длина {длина}, тип {тип}", м, 2, заголовок)
    у.поле("Длина", "ecatf.length", длина, м, 2, родитель=п)
    у.поле("Резерв", "ecatf.reserved", резерв, м, 2, родитель=п)
    у.поле("Тип", "ecatf.type", f"{тип} (датаграммы EtherCAT)", м, 2, тип, родитель=п)
    имена = []
    for место, команда, дл, дл_флаги in датаграммы:
        имя = ECAT_КОМАНДЫ[команда]
        if команда in (10, 11, 12):
            адрес = f"логический 0x{_le32(д, место + 2):08x}"
        else:
            адрес = f"ADP 0x{_le16(д, место + 2):04x}, ADO 0x{_le16(д, место + 4):04x}"
        wkc = _le16(д, место + 10 + дл)
        г = у.поле(f"{имя}: {адрес}, {дл} байт, WKC {wkc}", "ecat.sub", имя, место, 12 + дл)
        у.поле("Команда", "ecat.cmd", f"{команда} ({имя})", место, 1, команда, родитель=г)
        у.поле("Индекс", "ecat.idx", д[место + 1], место + 1, 1, родитель=г)
        if команда in (10, 11, 12):
            у.поле("Логический адрес", "ecat.lad", f"0x{_le32(д, место + 2):08x}", место + 2, 4,
                   _le32(д, место + 2), родитель=г)
        else:
            у.поле("ADP", "ecat.adp", f"0x{_le16(д, место + 2):04x}", место + 2, 2, _le16(д, место + 2), родитель=г)
            у.поле("ADO", "ecat.ado", f"0x{_le16(д, место + 4):04x}", место + 4, 2, _le16(д, место + 4), родитель=г)
        у.поле("Длина", "ecat.len", дл, место + 6, 2, родитель=г)
        у.поле("Циркулирует (C)", "ecat.subframe.circulating", (дл_флаги >> 14) & 1, место + 6, 2, родитель=г)
        у.поле("Есть ещё (M)", "ecat.subframe.more", дл_флаги >> 15, место + 6, 2, родитель=г)
        у.поле("IRQ", "ecat.int", f"0x{_le16(д, место + 8):04x}", место + 8, 2, _le16(д, место + 8), родитель=г)
        if дл:
            у.поле("Данные", "ecat.data", печатное(д[место + 10:место + 10 + дл], 32), место + 10, дл,
                   д[место + 10:место + 10 + дл].hex(), родитель=г)
        у.поле("WKC", "ecat.cnt", wkc, место + 10 + дл, 2, родитель=г)
        имена.append(имя)
    у.длина = 2 + длина
    у.итог = f"{len(датаграммы)} датаграмм: {', '.join(имена)}"
    р.п.инфо = "EtherCAT " + у.итог
    return True


# -- GOOSE и SV: IEC 61850-8-1, 61850-9-2 -----------------------------------------------------------

def _ber(д: bytes, м: int, край: int) -> tuple[int, int, int] | None:
    """Тег (однобайтовый), длина (краткая или длинная до 3 байт), начало значения; None — не BER.
    Значение может выходить за ``край`` — это проверяет вызывающий."""
    if м + 2 > край:
        return None
    тег, дл, место = д[м], д[м + 1], м + 2
    if тег & 0x1F == 0x1F:
        return None
    if дл & 0x80:
        n = дл & 0x7F
        if not 1 <= n <= 3 or место + n > край:
            return None
        дл = int.from_bytes(д[место:место + n], "big")
        место += n
    return тег, дл, место


def _ber_список(д: bytes, м: int, край: int, есть: int):
    """Элементы подряд от ``м`` до ``край``: [(тег, длина, начало значения, начало элемента)] и признак
    «все видны» (захват мог оборваться раньше ``край``); None — элементы не заполняют край ровно."""
    итог, место = [], м
    while место < край:
        if место + 2 > есть:
            return итог, False
        э = _ber(д, место, min(край, есть))
        if э is None:
            return (итог, False) if есть < край and место + 6 > есть else None
        тег, дл, начало = э
        if начало + дл > край:
            return None
        итог.append((тег, дл, начало, место))
        if начало + дл > есть:
            return итог, False
        место = начало + дл
    return итог, True


def _целое(данные_: bytes) -> int:
    return int.from_bytes(данные_, "big", signed=True) if данные_ else 0


def _utc(данные_: bytes) -> str:
    """UtcTime (IEC 61850-8-1, 8.1.3.7): секунды (4), доля секунды (3, двоичная), качество (1)."""
    сек, доля = u32(данные_, 0), u24(данные_, 4) / (1 << 24)
    момент = datetime.datetime.fromtimestamp(сек, datetime.UTC)
    return f"{момент:%Y-%m-%d %H:%M:%S}.{int(доля * 1e6):06d} UTC, качество 0x{данные_[7]:02x}"


MMS_ДАННЫЕ = {0xA1: "array", 0xA2: "structure", 0x83: "boolean", 0x84: "bit-string", 0x85: "integer",
              0x86: "unsigned", 0x87: "floating-point", 0x89: "octet-string", 0x8A: "visible-string",
              0x8C: "binary-time", 0x91: "utc-time"}


def _mms(д: bytes, тег: int, начало: int, дл: int) -> str:
    """Значение Data (ISO 9506-2, MMS) — словами для простых типов."""
    тело = д[начало:начало + дл]
    if тег == 0x83 and дл == 1:
        return "true" if тело[0] else "false"
    if тег in (0x85, 0x86):
        return str(_целое(тело) if тег == 0x85 else int.from_bytes(тело, "big"))
    if тег == 0x84 and тело:
        return f"{тело[1:].hex() or '—'} (неисп. бит {тело[0]})"
    if тег == 0x87 and дл == 5 and тело[0] == 8:
        return f"{struct.unpack('>f', тело[1:])[0]:g}"
    if тег == 0x8A:
        return тело.decode("ascii", "replace")
    if тег == 0x91 and дл == 8:
        return _utc(тело)
    if тег in (0xA1, 0xA2):
        return f"{дл} байт"
    return тело.hex()


GOOSE_ПОЛЯ = {0x80: ("gocbRef", "goose.gocbref"), 0x81: ("timeAllowedtoLive, мс", "goose.timeallowedtolive"),
              0x82: ("datSet", "goose.datset"), 0x83: ("goID", "goose.goid"), 0x84: ("t", "goose.t"),
              0x85: ("stNum", "goose.stnum"), 0x86: ("sqNum", "goose.sqnum"),
              0x87: ("simulation", "goose.simulation"), 0x88: ("confRev", "goose.confrev"),
              0x89: ("ndsCom", "goose.ndscom"), 0x8A: ("numDatSetEntries", "goose.numdatsetentries"),
              0xAB: ("allData", "goose.alldata"), 0x8C: ("security", "goose.security"),
              0xAC: ("security", "goose.security")}
GOOSE_ОБЯЗАТЕЛЬНЫЕ = {0x80, 0x81, 0x82, 0x84, 0x85, 0x86, 0x88, 0x8A, 0xAB}
GOOSE_ЦЕЛЫЕ = {0x81, 0x85, 0x86, 0x88, 0x8A}
SV_ПОЛЯ = {0x80: ("svID", "sv.svid"), 0x81: ("datSet", "sv.datset"), 0x82: ("smpCnt", "sv.smpcnt"),
           0x83: ("confRev", "sv.confrev"), 0x84: ("refrTm", "sv.refrtm"), 0x85: ("smpSynch", "sv.smpsynch"),
           0x86: ("smpRate", "sv.smprate"), 0x87: ("sample", "sv.seqdata"), 0x88: ("smpMod", "sv.smpmod"),
           0x89: ("gmIdentity", "sv.gmidentity")}
SV_РАЗМЕРЫ = {0x82: 2, 0x83: 4, 0x84: 8, 0x85: 1, 0x86: 2, 0x88: 2, 0x89: 8}
SV_ОБЯЗАТЕЛЬНЫЕ = {0x80, 0x82, 0x83, 0x85, 0x87}


def _порядок_тегов(теги: list[int]) -> bool:
    """Элементы SEQUENCE — по возрастанию номера тега, без повторов."""
    номера = [т & 0x1F for т in теги]
    return номера == sorted(set(номера))


def _заголовок_61850(р: Разбор, м: int, конец: int, тег_apdu: int):
    """APPID, Length (от APPID), Reserved1, Reserved2 (IEC 61850-8-1, прил. C); затем APDU BER."""
    д = р.д
    if м + 10 > конец:
        return None
    длина = u16(д, м + 2)
    if м + длина > _предел(р, конец):                   # Length < 10 отсечёт разбор BER ниже
        return None
    apdu = _ber(д, м + 8, м + длина)
    if apdu is None or apdu[0] != тег_apdu or apdu[2] + apdu[1] != м + длина:
        return None
    return длина, apdu


def _уровень_61850(р: Разбор, м: int, протокол: str, полное: str):
    д = р.д
    у = р.уровень(протокол, полное, м)
    ключ = протокол.lower()
    у.поле("APPID", f"{ключ}.appid", f"0x{u16(д, м):04x}", м, 2, u16(д, м))
    у.поле("Длина", f"{ключ}.length", u16(д, м + 2), м + 2, 2)
    р1 = u16(д, м + 4)
    п = у.поле("Резерв 1", f"{ключ}.reserve1", f"0x{р1:04x}", м + 4, 2, р1)
    у.поле("Simulated (IEC 61850-8-1 ред. 2)", f"{ключ}.reserve1.s_bit", р1 >> 15, м + 4, 2, родитель=п)
    у.поле("Резерв 2", f"{ключ}.reserve2", f"0x{u16(д, м + 6):04x}", м + 6, 2, u16(д, м + 6))
    у.длина = 8
    return у


@_надёжно
def goose(р: Разбор, м: int, конец: int) -> bool:
    """GOOSE (IEC 61850-8-1): заголовок прил. C и IECGoosePdu [APPLICATION 1] — поля [0]…[12]."""
    д = р.д
    заголовок = _заголовок_61850(р, м, конец, 0x61)
    if заголовок is None:
        return False
    длина, (_, дл_apdu, начало) = заголовок
    список = _ber_список(д, начало, м + длина, min(конец, len(д)))
    if список is None:
        return False
    элементы, все = список
    теги = [э[0] for э in элементы]
    if any(т not in GOOSE_ПОЛЯ for т in теги) or not _порядок_тегов(теги):
        return False
    if все and not GOOSE_ОБЯЗАТЕЛЬНЫЕ <= set(теги):
        return False
    for тег, дл, _, _ in элементы:
        if (тег == 0x84 and дл != 8) or (тег in (0x87, 0x89) and дл != 1) or (тег in GOOSE_ЦЕЛЫЕ and not 1 <= дл <= 5):
            return False
    у = _уровень_61850(р, м, "GOOSE", "IEC 61850 GOOSE")
    а = у.поле("goosePdu", "goose.goosepdu", f"{дл_apdu} байт", м + 8, начало + дл_apdu - м - 8)
    сведения = {}
    for тег, дл, нач, место in элементы:
        имя, ключ = GOOSE_ПОЛЯ[тег]
        тело = д[нач:нач + дл]
        if тег in (0x80, 0x82, 0x83):
            значение = тело.decode("ascii", "replace")
        elif тег in GOOSE_ЦЕЛЫЕ:
            значение = _целое(тело)
        elif тег in (0x87, 0x89):
            значение = bool(тело[0])
        elif тег == 0x84:
            значение = _utc(тело)
        elif тег == 0xAB:
            значение = f"{дл} байт"
        else:
            значение = тело.hex()
        сведения[тег] = значение
        п = у.поле(имя, ключ, значение, место, нач + дл - место, родитель=а)
        if тег == 0xAB:
            записи = _ber_список(д, нач, нач + дл, len(д))
            if записи is not None:
                for n, (т, дл_, нач_, место_) in enumerate(записи[0]):
                    if n < 64:
                        у.поле(f"{MMS_ДАННЫЕ.get(т, f'тег 0x{т:02x}')}: {_mms(д, т, нач_, дл_)}", "goose.data",
                               _mms(д, т, нач_, дл_), место_, нач_ + дл_ - место_, родитель=п)
                п.текст = f"{len(записи[0])} значений"
                if записи[1] and 0x8A in сведения and сведения[0x8A] != len(записи[0]):
                    п.плохо = True
                    р.ошибка(f"GOOSE: numDatSetEntries {сведения[0x8A]}, а значений {len(записи[0])}")
    у.итог = (f"{сведения.get(0x80, '?')}, stNum {сведения.get(0x85, '?')}, sqNum {сведения.get(0x86, '?')}")
    р.п.инфо = "GOOSE " + у.итог
    if not все:
        raise Мало()
    return True


@_надёжно
def sv(р: Разбор, м: int, конец: int) -> bool:
    """SV (IEC 61850-9-2, 8): заголовок как у GOOSE; savPdu [APPLICATION 0]: noASDU, security, ASDU."""
    д = р.д
    заголовок = _заголовок_61850(р, м, конец, 0x60)
    if заголовок is None:
        return False
    длина, (_, дл_apdu, начало) = заголовок
    есть = min(конец, len(д))
    список = _ber_список(д, начало, м + длина, есть)
    if список is None:
        return False
    элементы, все = список
    теги = [э[0] for э in элементы]
    if not теги or теги[0] != 0x80 or any(т not in (0x80, 0x81, 0xA1, 0xA2) for т in теги) or \
            not _порядок_тегов(теги):
        return False
    if not 1 <= элементы[0][1] <= 3:
        return False
    число = int.from_bytes(д[элементы[0][2]:элементы[0][2] + элементы[0][1]], "big")
    if все and (0xA2 not in теги):
        return False
    asdu = []
    for тег, дл, нач, _ in элементы:
        if тег != 0xA2:
            continue
        внутри = _ber_список(д, нач, нач + дл, есть)
        if внутри is None:
            return False
        for т, дл_, нач_, место_ in внутри[0]:
            if т != 0x30:
                return False
            поля = _ber_список(д, нач_, нач_ + дл_, есть)
            if поля is None:
                return False
            т_поля = [э[0] for э in поля[0]]
            if any(т not in SV_ПОЛЯ for т in т_поля) or not _порядок_тегов(т_поля):
                return False
            if поля[1] and not SV_ОБЯЗАТЕЛЬНЫЕ <= set(т_поля):
                return False
            if any(т in SV_РАЗМЕРЫ and дл__ != SV_РАЗМЕРЫ[т] for т, дл__, _, _ in поля[0]):
                return False
            asdu.append((место_, нач_ + дл_ - место_, поля[0]))
        if внутри[1] and len(внутри[0]) != число:
            return False
    у = _уровень_61850(р, м, "SV", "IEC 61850 Sampled Values")
    а = у.поле("savPdu", "sv.savpdu", f"{дл_apdu} байт", м + 8, начало + дл_apdu - м - 8)
    у.поле("noASDU", "sv.noasdu", число, элементы[0][3], элементы[0][2] + элементы[0][1] - элементы[0][3],
           родитель=а)
    итог = []
    for место, дл, поля in asdu:
        сведения = {}
        г = у.поле("ASDU", "sv.asdu", "", место, дл, родитель=а)
        for тег, дл_, нач_, место_ in поля:
            имя, ключ = SV_ПОЛЯ[тег]
            тело = д[нач_:нач_ + дл_]
            if тег in (0x80, 0x81):
                значение = тело.decode("ascii", "replace")
            elif тег in (0x82, 0x83, 0x85, 0x86, 0x88):
                значение = int.from_bytes(тело, "big")
            elif тег == 0x84:
                значение = _utc(тело)
            elif тег == 0x87:
                значение = f"{дл_} байт"
            else:
                значение = тело.hex()
            сведения[тег] = значение
            у.поле(имя, ключ, значение, место_, нач_ + дл_ - место_, тело.hex() if тег == 0x87 else None, родитель=г)
        г.текст = f"{сведения.get(0x80, '?')}, smpCnt {сведения.get(0x82, '?')}"
        итог.append(г.текст)
    у.итог = f"{число} ASDU: " + "; ".join(итог[:4])
    р.п.инфо = "SV " + у.итог
    if not все:
        raise Мало()
    return True


# -- Wake-on-LAN: AMD Magic Packet ------------------------------------------------------------------

def _wol_годен(д: bytes, м: int, конец: int) -> bool:
    return конец - м >= 102 and д[м:м + 6] == b"\xff" * 6 and д[м + 6:м + 102] == д[м + 6:м + 12] * 16


def _wol_уровень(р: Разбор, м: int, конец: int) -> None:
    д = р.д
    у = р.уровень("WOL", "Wake-on-LAN (Magic Packet)", м)
    у.поле("Синхронизация", "wol.sync", д[м:м + 6].hex(":"), м, 6)
    цель = mac(д, м + 6)
    у.поле("MAC цели", "wol.mac", цель, м + 6, 96)
    у.длина = 102
    лишнее = конец - м - 102
    if лишнее in (4, 6):
        у.поле("Пароль SecureOn", "wol.passwd", д[м + 102:конец].hex(":"), м + 102, лишнее)
        у.длина += лишнее
    у.итог = f"разбудить {цель}"
    р.п.инфо = "WOL: " + у.итог


@_надёжно
def wol(р: Разбор, м: int, конец: int) -> bool:
    """Magic Packet (EtherType 0x0842): 6×0xFF, затем 16 раз MAC цели; пароль SecureOn — 4 или 6 байт."""
    if not _wol_годен(р.д, м, конец):
        return False
    _wol_уровень(р, м, конец)
    return True


@_надёжно
def wol_udp(р: Разбор, м: int, конец: int) -> bool:
    """Magic Packet в UDP (эвристика): нагрузка ровно 102, 106 или 108 байт — сигнатура сильная."""
    if конец - м not in (102, 106, 108) or not _wol_годен(р.д, м, конец):
        return False
    _wol_уровень(р, м, конец)
    return True


# -- IS-IS: ISO/IEC 10589:2002, 9 -------------------------------------------------------------------

ISIS_ТИПЫ = {15: "L1 LAN IIH", 16: "L2 LAN IIH", 17: "P2P IIH", 18: "L1 LSP", 20: "L2 LSP", 24: "L1 CSNP",
             25: "L2 CSNP", 26: "L1 PSNP", 27: "L2 PSNP"}
ISIS_TLV = {1: "Area Addresses", 2: "IS Reachability", 6: "IS Neighbors", 8: "Padding", 9: "LSP Entries",
            10: "Authentication", 22: "Extended IS Reachability", 128: "IP Internal Reachability",
            129: "Protocols Supported", 130: "IP External Reachability", 132: "IP Interface Address",
            134: "TE Router ID", 135: "Extended IP Reachability", 137: "Dynamic Hostname",
            229: "Multi-Topology", 232: "IPv6 Interface Address", 236: "IPv6 Reachability",
            240: "Point-to-Point Three-Way Adjacency", 242: "Router Capability"}
ISIS_УРОВНИ_КАНАЛА = {1: "L1", 2: "L2", 3: "L1L2"}
NLPID = {0xCC: "IPv4", 0x8E: "IPv6", 0x81: "CLNP", 0x82: "ES-IS", 0x83: "IS-IS", 0x80: "SNAP",
         0x08: "Q.933", 0xCF: "PPP"}


def _isis_фикс(тип: int, ид: int) -> int:
    """Длина фиксированной части (без общих 8 байт) по 9.5–9.13 при длине ID системы ``ид``."""
    if тип in (15, 16):
        return 1 + ид + 2 + 2 + 1 + ид + 1
    if тип == 17:
        return 1 + ид + 2 + 2 + 1
    if тип in (18, 20):
        return 2 + 2 + ид + 2 + 4 + 2 + 1
    if тип in (24, 25):
        return 2 + ид + 1 + 2 * (ид + 2)
    return 2 + ид + 1


def _sysid(данные_: bytes) -> str:
    return ".".join(данные_[i:i + 2].hex() for i in range(0, len(данные_), 2))


def _lsp_id(данные_: bytes) -> str:
    """ID системы, псевдоузел и номер фрагмента: 1921.6800.1001.00-00."""
    return f"{_sysid(данные_[:-2])}.{данные_[-2]:02x}-{данные_[-1]:02x}"


def _флетчер_верен(данные_: bytes) -> bool:
    """ISO 8473-1, прил. C: при верной сумме обе частичные суммы по модулю 255 — нули."""
    c0 = c1 = 0
    for б in данные_:
        c0 = (c0 + б) % 255
        c1 = (c1 + c0) % 255
    return c0 == 0 and c1 == 0


@_надёжно
def isis(р: Разбор, м: int, конец: int) -> bool:
    """IS-IS PDU (ISO/IEC 10589, 9): общий заголовок 8 байт, фиксированная часть по типу, TLV."""
    д = р.д
    if м + 8 > конец:
        return False
    irpd, li, вер1, дл_ид, тип_байт, вер2, резерв, областей = д[м:м + 8]
    тип = тип_байт & 0x1F
    if irpd != 0x83 or вер1 != 1 or вер2 != 1 or резерв != 0 or тип_байт & 0xE0 or тип not in ISIS_ТИПЫ:
        return False
    if дл_ид not in (0, 255) and not 1 <= дл_ид <= 8:
        return False
    ид = 6 if дл_ид == 0 else (0 if дл_ид == 255 else дл_ид)
    if li != 8 + _isis_фикс(тип, ид) or м + li > конец:
        return False
    р.нужно(м, li)
    место_длины = м + 8 + 1 + ид + 2 if тип in (15, 16, 17) else м + 8
    длина = u16(д, место_длины)
    предел = _предел(р, конец)
    if длина < li or м + длина > предел:
        return False
    if тип in (15, 16, 17) and not 1 <= д[м + 8] <= 3:  # тип канала: резерв — нули, 0 не бывает
        return False
    if тип in (18, 20) and д[м + li - 1] & 0x03 not in (1, 3):  # IS Type: 1 — L1, 3 — L2
        return False
    край = min(м + длина, len(д))
    место = м + li
    while место < край:
        if место + 2 > край:
            if край < м + длина:
                break
            return False
        if место + 2 + д[место + 1] > м + длина:
            return False
        место += 2 + д[место + 1]
    if край == м + длина and место != край:
        return False
    у = р.уровень("ISIS", "IS-IS (ISO/IEC 10589)", м)
    имя = ISIS_ТИПЫ[тип]
    у.поле("Дискриминатор", "isis.irpd", f"0x{irpd:02x}", м, 1, irpd)
    у.поле("Длина заголовка", "isis.len", li, м + 1, 1)
    у.поле("Версия/расширение", "isis.version", вер1, м + 2, 1)
    у.поле("Длина ID системы", "isis.sysid_len", f"{дл_ид} ({ид} байт)", м + 3, 1, дл_ид)
    у.поле("Тип PDU", "isis.type", f"{тип} ({имя})", м + 4, 1, тип)
    у.поле("Версия", "isis.version2", вер2, м + 5, 1)
    у.поле("Резерв", "isis.reserved", резерв, м + 6, 1)
    у.поле("Наибольшее число областей", "isis.max_area_adr", f"{областей} ({областей or 3})", м + 7, 1, областей)
    ф = м + 8
    у.итог = имя
    if тип in (15, 16, 17):
        канал = д[ф] & 3
        у.поле("Тип канала", "isis.hello.circuit_type", f"{канал} ({ISIS_УРОВНИ_КАНАЛА[канал]})", ф, 1, канал)
        источник = _sysid(д[ф + 1:ф + 1 + ид])
        у.поле("ID источника", "isis.hello.source_id", источник, ф + 1, ид)
        у.поле("Время удержания, с", "isis.hello.holding_timer", u16(д, ф + 1 + ид), ф + 1 + ид, 2)
        у.поле("Длина PDU", "isis.hello.pdu_length", длина, ф + 3 + ид, 2)
        if тип == 17:
            у.поле("Локальный ID канала", "isis.hello.local_circuit_id", д[ф + 5 + ид], ф + 5 + ид, 1)
        else:
            у.поле("Приоритет", "isis.hello.priority", д[ф + 5 + ид] & 0x7F, ф + 5 + ид, 1)
            у.поле("LAN ID", "isis.hello.lan_id", f"{_sysid(д[ф + 6 + ид:ф + 6 + 2 * ид])}.{д[ф + 6 + 2 * ид]:02x}",
                   ф + 6 + ид, ид + 1)
        у.итог += f", источник {источник}"
    elif тип in (18, 20):
        у.поле("Длина PDU", "isis.lsp.pdu_length", длина, ф, 2)
        у.поле("Остаток жизни, с", "isis.lsp.remaining_life", u16(д, ф + 2), ф + 2, 2)
        lsp = _lsp_id(д[ф + 4:ф + 6 + ид])
        у.поле("LSP ID", "isis.lsp.lsp_id", lsp, ф + 4, ид + 2)
        номер = u32(д, ф + 6 + ид)
        у.поле("Номер", "isis.lsp.sequence_number", f"0x{номер:08x}", ф + 6 + ид, 4, номер)
        сумма = u16(д, ф + 10 + ид)
        if длина <= len(д) - м:
            верна = _флетчер_верен(д[м + 12:м + длина])
            очищен = u16(д, ф + 2) == 0                  # у очищенного LSP сумма не проверяется
            у.поле("Контрольная сумма", "isis.lsp.checksum",
                   f"0x{сумма:04x} [{'верна' if верна else 'не проверяется (очищен)' if очищен else 'НЕВЕРНА'}]",
                   ф + 10 + ид, 2, сумма, плохо=not верна and not очищен)
            if not верна and not очищен:
                р.ошибка("IS-IS: неверная контрольная сумма LSP")
        else:
            у.поле("Контрольная сумма", "isis.lsp.checksum", f"0x{сумма:04x}", ф + 10 + ид, 2, сумма)
        флаги = д[ф + 12 + ид]
        п = у.поле("Флаги", "isis.lsp.flags", f"0x{флаги:02x}", ф + 12 + ид, 1, флаги)
        у.поле("Partition repair (P)", "isis.lsp.partition_repair", флаги >> 7, ф + 12 + ид, 1, родитель=п)
        у.поле("Attached (ATT)", "isis.lsp.attached", (флаги >> 3) & 0x0F, ф + 12 + ид, 1, родитель=п)
        у.поле("Overload (OL)", "isis.lsp.overload", (флаги >> 2) & 1, ф + 12 + ид, 1, родитель=п)
        у.поле("IS Type", "isis.lsp.is_type", флаги & 3, ф + 12 + ид, 1, родитель=п)
        у.итог += f" {lsp}, номер 0x{номер:08x}"
    else:
        у.поле("Длина PDU", f"isis.{'csnp' if тип in (24, 25) else 'psnp'}.pdu_length", длина, ф, 2)
        источник = f"{_sysid(д[ф + 2:ф + 2 + ид])}.{д[ф + 2 + ид]:02x}"
        вид = "csnp" if тип in (24, 25) else "psnp"
        у.поле("ID источника", f"isis.{вид}.source_id", источник, ф + 2, ид + 1)
        if вид == "csnp":
            у.поле("Первый LSP ID", "isis.csnp.start_lsp_id", _lsp_id(д[ф + 3 + ид:ф + 5 + 2 * ид]), ф + 3 + ид, ид + 2)
            у.поле("Последний LSP ID", "isis.csnp.end_lsp_id", _lsp_id(д[ф + 5 + 2 * ид:ф + 7 + 3 * ид]),
                   ф + 5 + 2 * ид, ид + 2)
        у.итог += f", источник {источник}"
    у.длина = длина
    _isis_tlv(р, у, м + li, край, ид)
    р.п.инфо = "IS-IS " + у.итог
    if край < м + длина:
        raise Мало()
    return True


def _isis_tlv(р: Разбор, у, м: int, край: int, ид: int) -> None:
    д = р.д
    место = м
    while место + 2 <= край:
        код, дл = д[место], д[место + 1]
        тело = д[место + 2:min(место + 2 + дл, край)]
        имя = ISIS_TLV.get(код, f"TLV {код}")
        т = у.поле(f"{имя} ({дл} байт)", "isis.tlv.type", код, место, 2 + дл)
        у.поле("Длина", "isis.tlv.length", дл, место + 1, 1, родитель=т)
        з = место + 2
        if код == 1:
            i = 0
            while i < len(тело) and i + 1 + тело[i] <= len(тело):
                адрес = тело[i + 1:i + 1 + тело[i]]
                текст = адрес[:1].hex() + ("." + _sysid(адрес[1:]) if len(адрес) > 1 else "")
                у.поле(f"Область {текст}", "isis.area_address", текст, з + i, 1 + тело[i], родитель=т)
                i += 1 + тело[i]
        elif код == 6:
            for i in range(0, len(тело) - 5, 6):
                у.поле(f"Сосед {mac(тело, i)}", "isis.is_neighbor", mac(тело, i), з + i, 6, родитель=т)
        elif код == 129:
            for i, б in enumerate(тело):
                у.поле(f"NLPID 0x{б:02x} ({NLPID.get(б, '?')})", "isis.nlpid", б, з + i, 1, родитель=т)
        elif код == 132:
            for i in range(0, len(тело) - 3, 4):
                у.поле(f"Адрес {ip4(тело, i)}", "isis.ipv4_interface_address", ip4(тело, i), з + i, 4, родитель=т)
        elif код == 232:
            for i in range(0, len(тело) - 15, 16):
                у.поле(f"Адрес {ip6(тело, i)}", "isis.ipv6_interface_address", ip6(тело, i), з + i, 16, родитель=т)
        elif код == 137:
            имя_узла = тело.decode("utf-8", "replace")
            у.поле("Имя узла", "isis.hostname", имя_узла, з, len(тело), родитель=т)
            т.имя = f"{имя}: {имя_узла}"
            у.итог += f" ({имя_узла})"
        elif код == 240 and тело:
            у.поле("Состояние смежности", "isis.adjacency_state",
                   {0: "Up", 1: "Initializing", 2: "Down"}.get(тело[0], str(тело[0])), з, 1, тело[0], родитель=т)
        elif код == 9:
            шаг = ид + 10
            for i in range(0, len(тело) - шаг + 1, шаг):
                lsp = _lsp_id(тело[i + 2:i + 4 + ид])
                з_ = у.поле(f"LSP {lsp}", "isis.lsp_entry.lsp_id", lsp, з + i, шаг, родитель=т)
                у.поле("Остаток жизни", "isis.lsp_entry.remaining_life", u16(тело, i), з + i, 2, родитель=з_)
                у.поле("Номер", "isis.lsp_entry.sequence_number", u32(тело, i + 4 + ид), з + i + 4 + ид, 4,
                       родитель=з_)
        elif код == 10 and тело:
            у.поле("Тип аутентификации", "isis.auth.type",
                   {1: "открытый текст", 54: "HMAC-MD5"}.get(тело[0], str(тело[0])), з, 1, тело[0], родитель=т)
        elif код != 8 and тело:
            у.поле("Значение", "isis.tlv.value", тело.hex(), з, len(тело), родитель=т)
        место += 2 + дл


#: Разборщики PDU OSI по первому октету — NLPID (ISO/IEC TR 9577): IS-IS здесь, CLNP и ES-IS — модуль osi.
OSI_NLPID: dict = {}


def osi_pdu(р: Разбор, м: int, конец: int) -> bool:
    """PDU OSI по NLPID; разборщик проверяет своё сам, чужое откатывается."""
    разбор = OSI_NLPID.get(р.д[м]) if м < min(конец, len(р.д)) else None     # оборванный кадр: конца может не быть
    if разбор is None:
        return False
    снимок = _снимок(р)
    try:
        if разбор(р, м, конец):
            return True
    except (IndexError, ValueError, struct.error):
        pass
    _откат(р, снимок)
    return False


def _llc_osi(р: Разбор, м: int, конец: int) -> None:
    """DSAP 0xFE (OSI) и PPP 0x0023: первый байт — NLPID; не разобранное — данные."""
    if osi_pdu(р, м, конец):
        return
    nlpid = р.д[м] if м < len(р.д) else None
    данные(р, м, "OSI" + (f", NLPID 0x{nlpid:02x} ({NLPID.get(nlpid, '?')})" if nlpid is not None else ""), конец)


# -- CDP: формат Cisco, SNAP OUI 00-00-0C, PID 0x2000 -----------------------------------------------

CDP_TLV = {1: "Device ID", 2: "Addresses", 3: "Port ID", 4: "Capabilities", 5: "Software Version",
           6: "Platform", 9: "VTP Management Domain", 10: "Native VLAN", 11: "Duplex", 22: "Management Addresses"}
CDP_КЛЮЧИ = {1: "cdp.deviceid", 3: "cdp.portid", 5: "cdp.software_version", 6: "cdp.platform",
             9: "cdp.vtp_management_domain"}
CDP_ВОЗМОЖНОСТИ = ((0x01, "Router", "router"), (0x02, "Transparent Bridge", "trans_bridge"),
                   (0x04, "Source Route Bridge", "src_route_bridge"), (0x08, "Switch", "switch"),
                   (0x10, "Host", "host"), (0x20, "IGMP", "igmp_capable"), (0x40, "Repeater", "repeater"))


def _snap_cisco(р: Разбор, м: int) -> bool:
    """Перед нами — SNAP с OUI Cisco и PID 0x2000 (в LLC или в Frame Relay), а не EtherType 0x2000."""
    return (м >= 5 and р.д[м - 5:м] == b"\x00\x00\x0c\x20\x00" and bool(р.п.уровни)
            and р.п.уровни[-1].протокол in ("LLC", "FR"))


def _конец_802_3(р: Разбор, конец: int) -> int:
    """Кадр 802.3: конец данных — по полю длины Ethernet (за ним — добивка до 60 байт)."""
    уровни = р.п.уровни
    if len(уровни) >= 2 and уровни[-1].протокол == "LLC" and уровни[-2].протокол == "Ethernet":
        for п in уровни[-2].поля:
            if п.ключ == "eth.len":
                return min(конец, уровни[-1].смещение + п.сырое)
    return конец


def _cdp_адреса(у, р: Разбор, т, м: int, край: int, ключ: str) -> list[str]:
    """Адреса CDP: число (4), затем тип протокола (1: NLPID, 2: 802.2), длина, протокол, длина адреса, адрес."""
    д = р.д
    итог = []
    if м + 4 > край:
        return итог
    число, место = u32(д, м), м + 4
    for _ in range(min(число, 32)):
        if место + 2 > край:
            break
        тип, дл = д[место], д[место + 1]
        if место + 2 + дл + 2 > край:
            break
        протокол = д[место + 2:место + 2 + дл]
        дл_адр = u16(д, место + 2 + дл)
        адрес = д[место + 4 + дл:место + 4 + дл + дл_адр]
        if тип == 1 and протокол == b"\xcc" and len(адрес) == 4:
            текст = ip4(адрес, 0)
        elif тип == 2 and len(адрес) == 16:
            текст = ip6(адрес, 0)
        else:
            текст = адрес.hex()
        у.поле(f"Адрес {текст}", ключ, текст, место, 4 + дл + дл_адр, родитель=т)
        итог.append(текст)
        место += 4 + дл + дл_адр
    return итог


@_надёжно
def cdp(р: Разбор, м: int, конец: int) -> bool:
    """CDP: версия (1 или 2), TTL, контрольная сумма (RFC 1071 по всему сообщению), TLV (тип, длина, значение)."""
    д = р.д
    if not _snap_cisco(р, м):
        return False
    конец = _конец_802_3(р, конец)
    if м + 4 > конец or д[м] not in (1, 2):
        return False
    tlv, место = [], м + 4
    while место < конец:
        if место + 4 > конец:
            return False
        тип, дл = u16(д, место), u16(д, место + 2)
        if дл < 4 or место + дл > конец:
            return False
        tlv.append((место, тип, дл))
        место += дл
    у = р.уровень("CDP", "Cisco Discovery Protocol", м)
    у.поле("Версия", "cdp.version", д[м], м + 0, 1)
    у.поле("TTL, с", "cdp.ttl", д[м + 1], м + 1, 1)
    сообщение = д[м:конец]
    if len(сообщение) % 2:
        # У Cisco нечётный последний байт идёт в младшую половину слова, старшая — его знак.
        сообщение = сообщение[:-1] + bytes([0xFF if сообщение[-1] & 0x80 else 0, сообщение[-1]])
    верна = сумма16(сообщение) == 0
    у.поле("Контрольная сумма", "cdp.checksum", f"0x{u16(д, м + 2):04x} [{'верна' if верна else 'НЕВЕРНА'}]",
           м + 2, 2, u16(д, м + 2), плохо=not верна)
    if not верна:
        р.ошибка("CDP: неверная контрольная сумма")
    устройство = порт = платформа = ""
    for место, тип, дл in tlv:
        тело = д[место + 4:место + дл]
        т = у.поле(CDP_TLV.get(тип, f"TLV 0x{тип:04x}"), "cdp.tlv.type", тип, место, дл)
        у.поле("Длина", "cdp.tlv.len", дл, место + 2, 2, родитель=т)
        if тип in CDP_КЛЮЧИ:
            текст = тело.decode("utf-8", "replace")
            у.поле("Значение", CDP_КЛЮЧИ[тип], текст, место + 4, дл - 4, родитель=т)
            т.имя = f"{CDP_TLV[тип]}: {текст.splitlines()[0] if текст else ''}"
            if тип == 1:
                устройство = текст
            elif тип == 3:
                порт = текст
            elif тип == 6:
                платформа = текст
        elif тип == 4 and len(тело) == 4:
            в = u32(тело, 0)
            п = у.поле("Возможности", "cdp.capabilities", f"0x{в:08x}", место + 4, 4, в, родитель=т)
            поднятые = _биты(у, п, место + 4, 4, в, CDP_ВОЗМОЖНОСТИ, "cdp.capabilities")
            т.имя = f"Capabilities: {', '.join(поднятые) or 'нет'}"
        elif тип == 10 and len(тело) == 2:
            у.поле("Native VLAN", "cdp.native_vlan", u16(тело, 0), место + 4, 2, родитель=т)
            т.имя = f"Native VLAN: {u16(тело, 0)}"
        elif тип == 11 and len(тело) == 1:
            у.поле("Дуплекс", "cdp.duplex", "полный" if тело[0] else "полудуплекс", место + 4, 1, тело[0], родитель=т)
        elif тип in (2, 22):
            адреса = _cdp_адреса(у, р, т, место + 4, место + дл,
                                 "cdp.nrgyz.ip_address" if тип == 2 else "cdp.mgmt_address")
            т.имя = f"{CDP_TLV[тип]}: {', '.join(адреса) or '—'}"
        elif тело:
            у.поле("Значение", "cdp.tlv.value", тело.hex(), место + 4, дл - 4, родитель=т)
    у.длина = конец - м
    у.итог = f"устройство «{устройство}», порт «{порт}»" + (f", {платформа}" if платформа else "")
    р.п.инфо = "CDP " + у.итог
    return True


# -- Frame Relay: Q.922, RFC 2427; LMI — Q.933 прил. A, ANSI T1.617 прил. D -------------------------

Q933_СООБЩЕНИЯ = {0x75: "STATUS ENQUIRY", 0x7D: "STATUS"}
Q933_ОТЧЁТЫ = {0: "полный статус", 1: "проверка целостности канала", 2: "асинхронный статус PVC"}
#: Элементы LMI: Q.933 прил. A и ANSI T1.617 прил. D (после сдвига на кодовый набор 5, 0x95).
Q933_ЭЛЕМЕНТЫ = {0x51: "report", 0x53: "liv", 0x57: "pvc", 0x01: "report", 0x03: "liv", 0x07: "pvc"}


#: Нагрузка FR без инкапсуляции RFC 2427 (так идёт, например, NS интерфейса Gb — 3GPP TS 48.016, 6.2):
#: пробуются, если после адреса нет «0x03 + известный NLPID»; разборщик проверяет свои признаки сам.
FR_БЕЗ_NLPID: list = []


def _без_nlpid(р: Разбор, м: int) -> bool:
    for разбор in FR_БЕЗ_NLPID:
        снимок = _снимок(р)
        try:
            if разбор(р, м, len(р.д)):
                return True
        except (IndexError, ValueError, struct.error):
            pass
        _откат(р, снимок)
    return False


def fr(р: Разбор, м: int) -> None:
    """Кадр Frame Relay (LINKTYPE_FRELAY): адрес Q.922 (2–4 байта), затем инкапсуляция RFC 2427."""
    д = р.д
    у = р.уровень("FR", "Frame Relay", м)
    р.нужно(м, 2)
    дл = 2
    while not д[м + дл - 1] & 1:                        # EA=0 — адрес продолжается
        дл += 1
        if дл > 4:
            у.поле("Адрес", "fr.address", д[м:м + 4].hex(), м, 4, плохо=True)
            р.ошибка("FR: поле адреса длиннее 4 байт (бит EA)")
            данные(р, м + 4, "кадр FR с неверным адресом")
            return
        р.нужно(м, дл)
    if д[м] & 1:
        у.поле("Адрес", "fr.address", д[м:м + дл].hex(), м, дл, плохо=True)
        р.ошибка("FR: первый байт адреса с EA=1")
        данные(р, м + дл, "кадр FR с неверным адресом")
        return
    б0, б1 = д[м], д[м + 1]
    dlci = ((б0 >> 2) << 4) | (б1 >> 4)
    dc = None
    if дл == 3:
        dc = (д[м + 2] >> 1) & 1
        dlci = (dlci << 6) | (д[м + 2] >> 2)
    elif дл == 4:
        dc = (д[м + 3] >> 1) & 1
        dlci = (((dlci << 7) | (д[м + 2] >> 1)) << 6) | (д[м + 3] >> 2)
    а = у.поле("Адрес Q.922", "fr.address", д[м:м + дл].hex(), м, дл)
    у.поле("DLCI", "fr.dlci", dlci, м, дл, родитель=а)
    у.поле("C/R", "fr.cr", (б0 >> 1) & 1, м, 1, родитель=а)
    у.поле("FECN", "fr.fecn", (б1 >> 3) & 1, м + 1, 1, родитель=а)
    у.поле("BECN", "fr.becn", (б1 >> 2) & 1, м + 1, 1, родитель=а)
    у.поле("DE", "fr.de", (б1 >> 1) & 1, м + 1, 1, родитель=а)
    if dc is not None:
        у.поле("D/C", "fr.dc", dc, м + дл - 1, 1, родитель=а)
    у.длина = дл
    флаги = [и for и, б in (("FECN", б1 & 8), ("BECN", б1 & 4), ("DE", б1 & 2)) if б]
    у.итог = f"DLCI {dlci}" + (f" [{', '.join(флаги)}]" if флаги else "")
    место = м + дл
    if место >= len(д):
        return
    упр = д[место]
    x = место + 2 if упр == 0x03 and место + 1 < len(д) and д[место + 1] == 0x00 else место + 1
    известно = упр == 0x03 and x < len(д) and (д[x] in (0xCC, 0x8E, 0x80) or д[x] in OSI_NLPID
                                                       or (dlci == 0 and д[x] == 0x08))
    if not известно and _без_nlpid(р, место):
        return
    у.поле("Управление", "fr.control", f"0x{упр:02x}" + (" (UI)" if упр == 3 else ""), место, 1, упр)
    у.длина += 1
    if упр != 0x03:
        данные(р, место + 1, f"FR DLCI {dlci}, управление 0x{упр:02x}")
        return
    место += 1
    if место < len(д) and д[место] == 0x00:              # добивка перед NLPID (RFC 2427, 3)
        у.поле("Добивка", "fr.pad", 0, место, 1)
        место += 1
        у.длина += 1
    р.нужно(место, 1)
    nlpid = д[место]
    у.поле("NLPID", "fr.nlpid", f"0x{nlpid:02x} ({NLPID.get(nlpid, 'неизвестный')})", место, 1, nlpid)
    у.длина += 1
    if dlci == 0 and nlpid == 0x08:
        снимок = _снимок(р)
        try:
            if lmi(р, место, len(д)):
                return
        except (IndexError, ValueError, struct.error):
            pass
        _откат(р, снимок)
        данные(р, место, "Q.933 (не LMI)")
        return
    if nlpid == 0xCC:
        ipv4(р, место + 1)
    elif nlpid == 0x8E:
        ipv6(р, место + 1)
    elif nlpid in OSI_NLPID:
        if osi_pdu(р, место, len(д)):
            return
        данные(р, место, f"{NLPID.get(nlpid, 'OSI')} (не разобран)")
    elif nlpid == 0x80:
        р.нужно(место + 1, 5)
        oui, pid = д[место + 1:место + 4], u16(д, место + 4)
        у.поле("OUI (SNAP)", "fr.snap.oui", oui.hex(":"), место + 1, 3)
        у.поле("PID (SNAP)", "fr.snap.pid", f"0x{pid:04x}", место + 4, 2, pid)
        у.длина += 5
        дальше = место + 6
        if oui == b"\x00\x00\x00" or (oui == b"\x00\x00\x0c" and pid == 0x2000):
            по_типу(р, pid, дальше)
        elif oui == b"\x00\x80\xc2" and pid in (0x0001, 0x0007):   # мост 802.3 (с FCS / без FCS)
            ethernet(р, дальше)
        else:
            данные(р, дальше, f"SNAP {oui.hex(':')}/0x{pid:04x}")
    else:
        данные(р, место + 1, f"FR NLPID 0x{nlpid:02x}")


@_надёжно
def lmi(р: Разбор, м: int, конец: int) -> bool:
    """LMI на DLCI 0: дискриминатор 0x08, пустая ссылка вызова, STATUS ENQUIRY/STATUS и элементы
    Report type, Link integrity verification, PVC status (Q.933 прил. A / T1.617 прил. D)."""
    д = р.д
    if м + 3 > конец or д[м] != 0x08 or д[м + 1] != 0x00 or д[м + 2] not in Q933_СООБЩЕНИЯ:
        return False
    элементы, место, сдвиг = [], м + 3, None
    while место < конец:
        ид = д[место]
        if ид == 0x95:                                   # блокирующий сдвиг на кодовый набор 5 (ANSI)
            сдвиг = место
            место += 1
            continue
        if место + 2 > конец:
            return False
        дл = д[место + 1]
        вид = Q933_ЭЛЕМЕНТЫ.get(ид)
        if вид is None or место + 2 + дл > конец:
            return False
        if (вид == "report" and дл != 1) or (вид == "liv" and дл != 2) or (вид == "pvc" and дл < 3):
            return False
        элементы.append((место, вид, ид, дл))
        место += 2 + дл
    длина = место - м
    виды = [э[1] for э in элементы]
    if "report" not in виды or ("liv" not in виды and д[м + 2] == 0x75):
        return False
    у = р.уровень("LMI", "Frame Relay LMI (Q.933 прил. A / T1.617 прил. D)", м)
    у.поле("Дискриминатор протокола", "q933.disc", "0x08 (Q.933)", м, 1, 8)
    у.поле("Ссылка вызова", "q933.call_ref_len", "пустая (0)", м + 1, 1, 0)
    тип = д[м + 2]
    у.поле("Тип сообщения", "q933.message_type", f"0x{тип:02x} ({Q933_СООБЩЕНИЯ[тип]})", м + 2, 1, тип)
    if сдвиг is not None:
        у.поле("Сдвиг на кодовый набор 5 (ANSI T1.617 прил. D)", "q933.shift", "0x95", сдвиг, 1, 0x95)
    итог = [Q933_СООБЩЕНИЯ[тип]]
    pvc = []
    for место, вид, ид, дл in элементы:
        if вид == "report":
            отчёт = д[место + 2]
            э = у.поле(f"Report type: {Q933_ОТЧЁТЫ.get(отчёт, отчёт)}", "q933.ie", ид, место, 2 + дл)
            у.поле("Тип отчёта", "q933.report_type", Q933_ОТЧЁТЫ.get(отчёт, str(отчёт)), место + 2, 1, отчёт,
                   родитель=э)
            итог.append(Q933_ОТЧЁТЫ.get(отчёт, f"отчёт {отчёт}"))
        elif вид == "liv":
            э = у.поле(f"Link integrity verification: {д[место + 2]}/{д[место + 3]}", "q933.ie", ид, место, 4)
            у.поле("Номер передачи", "q933.link_verf.txseq", д[место + 2], место + 2, 1, родитель=э)
            у.поле("Номер приёма", "q933.link_verf.rxseq", д[место + 3], место + 3, 1, родитель=э)
            итог.append(f"посл. {д[место + 2]}/{д[место + 3]}")
        else:
            б3, б3a, б4 = д[место + 2], д[место + 3], д[место + 2 + дл - 1]
            dlci = ((б3 & 0x3F) << 4) | ((б3a >> 3) & 0x0F)
            э = у.поле(f"PVC status: DLCI {dlci}{', активен' if б4 & 0x02 else ''}{', новый' if б4 & 0x08 else ''}",
                       "q933.ie", ид, место, 2 + дл)
            у.поле("DLCI", "q933.pvc_status.dlci", dlci, место + 2, 2, родитель=э)
            у.поле("Новый", "q933.pvc_status.new", (б4 >> 3) & 1, место + 1 + дл, 1, родитель=э)
            у.поле("Активен", "q933.pvc_status.active", (б4 >> 1) & 1, место + 1 + дл, 1, родитель=э)
            pvc.append(str(dlci))
    if pvc:
        итог.append("PVC " + ", ".join(pvc))
    у.длина = длина
    у.итог = ", ".join(итог)
    р.п.инфо = "LMI " + у.итог
    return True


# -- AX.25 v2.2 ------------------------------------------------------------------------------------

AX25_U = {0x6F: "SABME", 0x2F: "SABM", 0x43: "DISC", 0x0F: "DM", 0x63: "UA", 0x87: "FRMR", 0x03: "UI",
          0xAF: "XID", 0xE3: "TEST"}
AX25_S = {0x01: "RR", 0x05: "RNR", 0x09: "REJ", 0x0D: "SREJ"}
AX25_PID = {0x01: "X.25 PLP (ISO 8208)", 0x08: "фрагмент", 0xCC: "IPv4 (ARPA)", 0xCD: "ARP (ARPA)",
            0xCF: "NET/ROM", 0xF0: "без уровня 3"}
_ПОЗЫВНОЙ = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ")


def _ax25_адрес(д: bytes, м: int) -> str | None:
    """Позывной (6 знаков, сдвинутых на бит влево) и SSID; None — не адрес AX.25 (3.12)."""
    if any(б & 1 or (б >> 1) not in _ПОЗЫВНОЙ for б in д[м:м + 6]):
        return None
    позывной = bytes(б >> 1 for б in д[м:м + 6]).decode("ascii").rstrip()
    if not позывной:
        return None
    ssid = (д[м + 6] >> 1) & 0x0F
    return f"{позывной}-{ssid}" if ssid else позывной


def _hdlc_управление(упр: int, u_коды, s_коды) -> tuple[str, str]:
    """Кадр I / S / U по полю управления (модуль 8): вид и описание."""
    pf = (упр >> 4) & 1
    if not упр & 1:
        return "I", f"I, N(S)={(упр >> 1) & 7}, N(R)={упр >> 5}" + (", P" if pf else "")
    if упр & 3 == 1:
        имя = s_коды.get(упр & 0x0F, f"S 0x{упр & 0x0F:x}")
        return "S", f"{имя}, N(R)={упр >> 5}" + (", P/F" if pf else "")
    имя = u_коды.get(упр & ~0x10 & 0xFF, f"U 0x{упр & 0xEF:02x}")
    return "U", имя + (", P/F" if pf else "")


def ax25(р: Разбор, м: int) -> None:
    """Кадр AX.25 (LINKTYPE_AX25): адреса получателя, отправителя и до 8 ретрансляторов, управление, PID."""
    д = р.д
    у = р.уровень("AX.25", "Amateur X.25 (AX.25 v2.2)", м)
    р.нужно(м, 15)
    адреса, место = [], м
    while True:
        р.нужно(место, 7)
        адрес = _ax25_адрес(д, место)
        if адрес is None or len(адреса) >= 10:
            у.поле("Адрес", "ax25.address", д[место:место + 7].hex(), место, 7, плохо=True)
            р.ошибка("AX.25: поле адреса не по 3.12 (позывной, SSID, бит расширения)")
            у.итог = "неверный адрес"
            данные(р, место, "кадр AX.25 с неверным адресом")
            return
        адреса.append((место, адрес))
        место += 7
        if д[место - 1] & 1:
            break
    if len(адреса) < 2:
        у.итог = "неверный адрес"
        р.ошибка("AX.25: меньше двух адресов")
        данные(р, место, "кадр AX.25 с неверным адресом")
        return
    (м_к, к), (м_от, от) = адреса[0], адреса[1]
    c_к, c_от = д[м_к + 6] >> 7, д[м_от + 6] >> 7
    у.поле("Получатель", "ax25.dst", к, м_к, 7)
    у.поле("Отправитель", "ax25.src", от, м_от, 7)
    вид_команды = "команда" if (c_к, c_от) == (1, 0) else "ответ" if (c_к, c_от) == (0, 1) else "v1"
    у.поле("Команда/ответ", "ax25.cr", вид_команды, м_к + 6, 1, (c_к << 1) | c_от)
    через = []
    for место_, адрес in адреса[2:]:
        повторён = д[место_ + 6] >> 7
        у.поле(f"Через {адрес}{' *' if повторён else ''}", "ax25.via", адрес, место_, 7)
        через.append(адрес + ("*" if повторён else ""))
    р.адреса(от, к)
    р.нужно(место, 1)
    упр = д[место]
    вид, текст = _hdlc_управление(упр, AX25_U, AX25_S)
    у.поле("Управление", "ax25.ctl", f"0x{упр:02x} ({текст})", место, 1, упр)
    у.длина = место + 1 - м
    у.итог = f"{от} → {к}" + (f" через {','.join(через)}" if через else "") + f", {текст}"
    р.п.инфо = "AX.25 " + у.итог
    место += 1
    if вид == "I" or (вид == "U" and упр & 0xEF == 0x03):
        р.нужно(место, 1)
        pid = д[место]
        у.поле("PID", "ax25.pid", f"0x{pid:02x} ({AX25_PID.get(pid, 'неизвестный')})", место, 1, pid)
        у.длина += 1
        if pid == 0xCC:
            ipv4(р, место + 1)
        elif pid == 0xCD:
            arp(р, место + 1)
        else:
            данные(р, место + 1, f"AX.25 PID 0x{pid:02x}")
            р.п.инфо = "AX.25 " + у.итог
    elif место < len(д):
        данные(р, место, "поле сведений AX.25")
        р.п.инфо = "AX.25 " + у.итог


# -- LAPB и X.25: ITU-T X.25 (10/96) ---------------------------------------------------------------

LAPB_АДРЕСА = {0x01: "B", 0x03: "A", 0x07: "D (многоканальный)", 0x0F: "C (многоканальный)"}
LAPB_U = {0x2F: "SABM", 0x6F: "SABME", 0x43: "DISC", 0x0F: "DM", 0x63: "UA", 0x87: "FRMR"}
LAPB_S = {0x01: "RR", 0x05: "RNR", 0x09: "REJ"}
X25_ТИПЫ = {0x0B: "Call request", 0x0F: "Call accepted", 0x13: "Clear request", 0x17: "Clear confirmation",
            0x23: "Interrupt", 0x27: "Interrupt confirmation", 0x1B: "Reset request", 0x1F: "Reset confirmation",
            0xFB: "Restart request", 0xFF: "Restart confirmation", 0xF1: "Diagnostic",
            0xF3: "Registration request", 0xF7: "Registration confirmation"}
X25_КАНАЛ_0 = {0xFB, 0xFF, 0xF1, 0xF3, 0xF7}           # только на логическом канале 0
X25_ПОТОК = {0x01: "RR", 0x05: "RNR", 0x09: "REJ"}


def lapb(р: Разбор, м: int, *, с_направлением: bool = True) -> None:
    """LINKTYPE_LAPB_WITH_DIR: байт направления (0 — принят этим узлом, иначе — отправлен), затем кадр LAPB
    (X.25, 2.3): адрес, управление (модуль 8), поле сведений — пакет X.25.

    ``с_направлением=False`` — кадр без псевдозаголовка (из потока после HDLC, из .Sig)."""
    д = р.д
    у = р.уровень("LAPB", "Link Access Procedure, Balanced (X.25)", м)
    if с_направлением:
        р.нужно(м, 3)
        направление = "принят (DCE → DTE)" if д[м] == 0 else "отправлен (DTE → DCE)"
        у.поле("Направление (псевдозаголовок)", "lapb.direction", направление, м, 1, д[м])
    else:
        р.нужно(м, 2)
        м -= 1                                           # адрес — с первого октета кадра
    адрес, упр = д[м + 1], д[м + 2]
    if адрес not in LAPB_АДРЕСА:
        у.поле("Адрес", "lapb.address", f"0x{адрес:02x}", м + 1, 1, адрес, плохо=True)
        р.ошибка("LAPB: адрес не A/B/C/D (X.25, 2.4.2)")
        у.итог = f"неверный адрес 0x{адрес:02x}"
        данные(р, м + 2, "кадр LAPB с неверным адресом")
        return
    у.поле("Адрес", "lapb.address", f"0x{адрес:02x} ({LAPB_АДРЕСА[адрес]})", м + 1, 1, адрес)
    вид, текст = _hdlc_управление(упр, LAPB_U, LAPB_S)
    у.поле("Управление", "lapb.control", f"0x{упр:02x} ({текст})", м + 2, 1, упр)
    у.длина = 3
    у.итог = f"{LAPB_АДРЕСА[адрес][0]}, {текст}"
    р.п.инфо = "LAPB " + у.итог
    if вид == "I" and м + 3 < len(д):
        снимок = _снимок(р)
        try:
            if x25(р, м + 3, len(д)):
                return
        except (IndexError, ValueError, struct.error):
            pass
        _откат(р, снимок)
        данные(р, м + 3, "поле сведений LAPB (не пакет X.25)")
    elif м + 3 < len(д):
        данные(р, м + 3, "поле сведений LAPB")
        р.п.инфо = "LAPB " + у.итог


def _bcd(д: bytes, м: int, цифр: int, сдвиг: int) -> str:
    """Цифры адреса DTE по полубайтам, начиная с полубайта ``сдвиг``."""
    итог = []
    for i in range(сдвиг, сдвиг + цифр):
        б = д[м + i // 2]
        итог.append(str((б >> 4) if i % 2 == 0 else (б & 15)))
    return "".join(итог)


@_надёжно
def x25(р: Разбор, м: int, конец: int) -> bool:
    """Пакет X.25 (X.25, 5): GFI (Q/A, D, модуль), LCGN+LCN, идентификатор типа пакета."""
    д = р.д
    if м + 3 > конец:
        return False
    gfi, модуль_биты = д[м] >> 4, (д[м] >> 4) & 3
    if модуль_биты not in (1, 2):                        # 01 — модуль 8, 10 — модуль 128
        return False
    lci = ((д[м] & 0x0F) << 8) | д[м + 1]
    тип = д[м + 2]
    модуль = 8 if модуль_биты == 1 else 128
    if not тип & 1:
        вид = "Data"
        if модуль == 128 and м + 4 > конец:
            return False
    elif тип in X25_ТИПЫ:
        вид = X25_ТИПЫ[тип]
    elif (тип & 0x1F if модуль == 8 else тип) in X25_ПОТОК:
        вид = X25_ПОТОК[тип & 0x1F if модуль == 8 else тип]
        if модуль == 128 and м + 4 > конец:
            return False
    else:
        return False
    if (тип in X25_КАНАЛ_0) != (lci == 0):
        return False
    у = р.уровень("X.25", "X.25 Packet Layer", м)
    п = у.поле("GFI", "x25.gfi", f"0x{gfi:x} (модуль {модуль})", м, 1, gfi)
    у.поле("Q (A)", "x25.q", gfi >> 3, м, 1, родитель=п)
    у.поле("D", "x25.d", (gfi >> 2) & 1, м, 1, родитель=п)
    у.поле("Модуль", "x25.mod", модуль, м, 1, родитель=п)
    у.поле("LCN", "x25.lcn", lci, м, 2)
    у.поле("Тип пакета", "x25.type", f"0x{тип:02x} ({вид})", м + 2, 1, тип)
    у.длина = 3
    у.итог = f"{вид}, LCN {lci}"
    if вид == "Data":
        if модуль == 8:
            ps, pr, more = (тип >> 1) & 7, тип >> 5, (тип >> 4) & 1
        else:
            ps, pr, more = тип >> 1, д[м + 3] >> 1, д[м + 3] & 1
            у.длина = 4
        у.поле("P(S)", "x25.p_s", ps, м + 2, 1)
        у.поле("P(R)", "x25.p_r", pr, м + 2 if модуль == 8 else м + 3, 1)
        у.поле("M", "x25.m", more, м + 2 if модуль == 8 else м + 3, 1)
        у.итог += f", P(S)={ps} P(R)={pr}" + (" M" if more else "")
        р.п.инфо = "X.25 " + у.итог
        if м + у.длина < конец:
            данные(р, м + у.длина, "данные X.25" + (" (Q)" if gfi >> 3 else ""), конец)
            р.п.инфо = "X.25 " + у.итог
        return True
    if вид in X25_ПОТОК.values():
        pr = тип >> 5 if модуль == 8 else д[м + 3] >> 1
        у.поле("P(R)", "x25.p_r", pr, м + 2 if модуль == 8 else м + 3, 1)
        у.длина = 3 if модуль == 8 else 4
        у.итог += f", P(R)={pr}"
    elif тип in (0x0B, 0x0F) and м + 3 < конец:
        длины = д[м + 3]
        вызывающий, вызываемый = длины >> 4, длины & 15
        байт = (вызывающий + вызываемый + 1) // 2
        р.нужно(м + 4, байт)
        куда = _bcd(д, м + 4, вызываемый, 0)
        откуда = _bcd(д, м + 4, вызывающий, вызываемый)
        у.поле("Длины адресов", "x25.address_lengths", f"вызывающий {вызывающий}, вызываемый {вызываемый}",
               м + 3, 1, длины)
        у.поле("Вызываемый DTE", "x25.called_address", куда, м + 4, байт)
        у.поле("Вызывающий DTE", "x25.calling_address", откуда, м + 4, байт)
        место = м + 4 + байт
        у.длина = место - м
        if место < конец:
            дл_уд = д[место]
            у.поле("Длина услуг", "x25.facilities_length", дл_уд, место, 1)
            if дл_уд:
                у.поле("Услуги", "x25.facilities", д[место + 1:место + 1 + дл_уд].hex(), место + 1, дл_уд)
            место += 1 + дл_уд
            у.длина = место - м
            if место < конец:
                у.поле("Данные пользователя вызова", "x25.call_user_data", д[место:конец].hex(), место, конец - место)
                у.длина = конец - м
        у.итог += (f", {откуда or '—'} → {куда or '—'}")
    elif тип in (0x13, 0x1B, 0xFB) and м + 4 < конец:
        у.поле("Причина", "x25.cause", д[м + 3], м + 3, 1)
        у.поле("Диагностика", "x25.diagnostic", д[м + 4], м + 4, 1)
        у.длина = 5
        у.итог += f", причина {д[м + 3]}, диагностика {д[м + 4]}"
    р.п.инфо = "X.25 " + у.итог
    return True


# -- HSRP: RFC 2281, 5 ------------------------------------------------------------------------------

HSRP_ОПЕРАЦИИ = {0: "Hello", 1: "Coup", 2: "Resign"}
HSRP_СОСТОЯНИЯ = {0: "Initial", 1: "Learn", 2: "Listen", 4: "Speak", 8: "Standby", 16: "Active"}


@_надёжно
def hsrp(р: Разбор, м: int, конец: int) -> bool:
    """HSRP версии 0 (RFC 2281, 5): 20 байт — версия, код, состояние, таймеры, приоритет, группа,
    аутентификация (8 байт), виртуальный IP."""
    д = р.д
    if конец - м < 20:
        return False
    версия, код, состояние = д[м], д[м + 1], д[м + 2]
    if версия != 0 or код not in HSRP_ОПЕРАЦИИ or состояние not in HSRP_СОСТОЯНИЯ:
        return False
    у = р.уровень("HSRP", "Hot Standby Router Protocol", м)
    у.поле("Версия", "hsrp.version", версия, м, 1)
    у.поле("Код операции", "hsrp.opcode", f"{код} ({HSRP_ОПЕРАЦИИ[код]})", м + 1, 1, код)
    у.поле("Состояние", "hsrp.state", f"{состояние} ({HSRP_СОСТОЯНИЯ[состояние]})", м + 2, 1, состояние)
    у.поле("Hellotime, с", "hsrp.hellotime", д[м + 3], м + 3, 1)
    у.поле("Holdtime, с", "hsrp.holdtime", д[м + 4], м + 4, 1)
    у.поле("Приоритет", "hsrp.priority", д[м + 5], м + 5, 1)
    у.поле("Группа", "hsrp.group", д[м + 6], м + 6, 1)
    у.поле("Резерв", "hsrp.reserved", д[м + 7], м + 7, 1)
    у.поле("Аутентификация", "hsrp.auth_data", _текст(д[м + 8:м + 16]), м + 8, 8)
    виртуальный = ip4(д, м + 16)
    у.поле("Виртуальный IP", "hsrp.virt_ip", виртуальный, м + 16, 4)
    у.длина = 20
    у.итог = (f"{HSRP_ОПЕРАЦИИ[код]} ({HSRP_СОСТОЯНИЯ[состояние]}), группа {д[м + 6]}, "
              f"приоритет {д[м + 5]}, {виртуальный}")
    р.п.инфо = "HSRP " + у.итог
    if конец > м + 20:
        данные(р, м + 20, "продолжение HSRP", конец)
        р.п.инфо = "HSRP " + у.итог
    return True


# -- регистрация ------------------------------------------------------------------------------------

for _тип, _разборщик in ((0x888E, eapol), (0x8809, медленные), (0x88F7, ptp), (0x88E5, macsec), (0x88E7, pbb),
                         (0x8906, fcoe), (0x8914, fip), (0x8892, pn_rt), (0x88A4, ethercat), (0x88B8, goose),
                         (0x88BA, sv), (0x0842, wol), (0x2000, cdp)):
    _по_ethertype(_тип, _разборщик)

OSI_NLPID.setdefault(0x83, isis)
ДОП_LLC.setdefault(0xFE, _llc_osi)
# PPP, протокол 0x0023 «OSI Network Layer» (RFC 1377): в поле данных — PDU OSI, первый
# байт — NLPID (так и у Wireshark: packet-ppp.h PPP_OSI, packet-osi.c dissect_osi).
ДОП_PPP.setdefault(0x0023, _llc_osi)
PPP_ПРОТОКОЛЫ.setdefault(0x0023, "OSI")

for _номер, _канал, _вход in ((107, "Frame Relay", fr), (3, "AX.25", ax25), (207, "LAPB", lapb)):
    КАНАЛЫ.setdefault(_номер, _канал)
    КАНАЛ_В_РАЗБОРЩИК.setdefault(_канал, _вход)
# Кадр LAPB без байта направления — типа канала pcap у него нет (кадры из потока и .Sig).
КАНАЛ_В_РАЗБОРЩИК.setdefault("LAPB без направления", lambda р, м: lapb(р, м, с_направлением=False))

for _порт, _разборщик in ((319, ptp), (320, ptp), (1985, hsrp)):
    ПОРТЫ_UDP.setdefault(_порт, _разборщик)
for _имя, _разборщик in (("PTP", ptp), ("HSRP", hsrp), ("WOL", wol_udp)):
    КАК["udp"].setdefault(_имя, _разборщик)
if wol_udp not in ЭВРИСТИКИ_UDP:
    ЭВРИСТИКИ_UDP.append(wol_udp)

for _имя in ("EAPOL", "EAP", "LACP", "Marker", "OAM", "MACsec", "PBB", "FCoE", "FC", "FIP", "PN-RT", "EtherCAT",
             "WOL", "CDP", "FR", "LMI", "AX.25", "LAPB", "X.25"):
    ДОП_УРОВНИ.setdefault(_имя, "канальный")
for _имя in ("PTP", "HSRP", "GOOSE", "SV", "PN-DCP"):
    ДОП_УРОВНИ.setdefault(_имя, "прикладной")
ДОП_УРОВНИ.setdefault("ISIS", "сетевой")
