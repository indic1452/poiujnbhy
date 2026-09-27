# -*- coding: utf-8 -*-
"""Учёт трафика: NetFlow v9 и IPFIX с шаблонами, sFlow v5 — по портам UDP.

Документы (структуры полей — строго по ним):

* NetFlow v9 — RFC 3954, разд. 5 (заголовок 20 байт), 5.2–5.3 (FlowSet: 0 — шаблоны, 1 — шаблоны
  опций, ≥ 256 — данные по шаблону), 6 (опции); выравнивание FlowSet до 4 байт.
* IPFIX — RFC 7011, разд. 3.1 (заголовок 16 байт), 3.3 (Set: 2 — шаблоны, 3 — шаблоны опций,
  ≥ 256 — данные), 3.2 (элемент: бит предприятия и номер предприятия), 7 (переменная длина:
  байт длины или 255 и два байта).
* Имена элементов 1–255 — таблица v9_v10_template_types Wireshark (packet-netflow.c);
  самые частые — по-русски.
* sFlow v5 — sflow_version_5.txt (sFlow.org), раскладка как у Wireshark (packet-sflow.c):
  заголовок датаграммы, образцы (формат = предприятие << 12 | формат: 1 — поток, 2 — счётчики,
  3 и 4 — расширенные), записи потока (1 — заголовок пакета: разбирается дальше как Ethernet,
  IPv4, IPv6 …; 2 — Ethernet, 3 — IPv4, 1001 — коммутатор, 1002 — маршрутизатор), записи
  счётчиков (1 — общие счётчики интерфейса).

Шаблоны живут между пакетами: разбор захвата передаёт общий словарь ``шаблоны``; ключ — экспортёр,
версия, домен наблюдения и номер шаблона, значение — определения с номером пакета, где они пришли
(подробный разбор пакета берёт то, что было известно к нему).
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..pole import ip4, ip6, mac, u16, u32, u64
from ..razbor import IP_ПРОТОКОЛЫ, ДОП_УРОВНИ, Разбор, данные
from .. import prilozh

ИМЕНА_IE = {
    1: 'BYTES', 2: 'PKTS', 3: 'FLOWS', 4: 'PROTOCOL', 5: 'IP_TOS', 6: 'TCP_FLAGS', 7: 'L4_SRC_PORT',
    8: 'IP_SRC_ADDR', 9: 'SRC_MASK', 10: 'INPUT_SNMP', 11: 'L4_DST_PORT', 12: 'IP_DST_ADDR', 13: 'DST_MASK',
    14: 'OUTPUT_SNMP', 15: 'IP_NEXT_HOP', 16: 'SRC_AS', 17: 'DST_AS', 18: 'BGP_NEXT_HOP', 19: 'MUL_DPKTS',
    20: 'MUL_DOCTETS', 21: 'LAST_SWITCHED', 22: 'FIRST_SWITCHED', 23: 'OUT_BYTES', 24: 'OUT_PKTS',
    25: 'IP LENGTH MINIMUM', 26: 'IP LENGTH MAXIMUM', 27: 'IPV6_SRC_ADDR', 28: 'IPV6_DST_ADDR', 29: 'IPV6_SRC_MASK',
    30: 'IPV6_DST_MASK', 31: 'FLOW_LABEL', 32: 'ICMP_TYPE', 33: 'IGMP_TYPE', 34: 'SAMPLING_INTERVAL',
    35: 'SAMPLING_ALGORITHM', 36: 'FLOW_ACTIVE_TIMEOUT', 37: 'FLOW_INACTIVE_TIMEOUT', 38: 'ENGINE_TYPE',
    39: 'ENGINE_ID', 40: 'TOTAL_BYTES_EXP', 41: 'TOTAL_PKTS_EXP', 42: 'TOTAL_FLOWS_EXP', 43: 'IPV4_ROUTER_SC',
    44: 'IP_SRC_PREFIX', 45: 'IP_DST_PREFIX', 46: 'MPLS_TOP_LABEL_TYPE', 47: 'MPLS_TOP_LABEL_ADDR',
    48: 'FLOW_SAMPLER_ID', 49: 'FLOW_SAMPLER_MODE', 50: 'FLOW_SAMPLER_RANDOM_INTERVAL', 51: 'FLOW_CLASS',
    52: 'IP TTL MINIMUM', 53: 'IP TTL MAXIMUM', 54: 'IPv4 ID', 55: 'DST_TOS', 56: 'SRC_MAC', 57: 'DST_MAC',
    58: 'SRC_VLAN', 59: 'DST_VLAN', 60: 'IP_PROTOCOL_VERSION', 61: 'DIRECTION', 62: 'IPV6_NEXT_HOP',
    63: 'BGP_IPV6_NEXT_HOP', 64: 'IPV6_OPTION_HEADERS', 70: 'MPLS_LABEL_1', 71: 'MPLS_LABEL_2', 72: 'MPLS_LABEL_3',
    73: 'MPLS_LABEL_4', 74: 'MPLS_LABEL_5', 75: 'MPLS_LABEL_6', 76: 'MPLS_LABEL_7', 77: 'MPLS_LABEL_8',
    78: 'MPLS_LABEL_9', 79: 'MPLS_LABEL_10', 80: 'DESTINATION_MAC', 81: 'SOURCE_MAC', 82: 'IF_NAME', 83: 'IF_DESC',
    84: 'SAMPLER_NAME', 85: 'BYTES_TOTAL', 86: 'PACKETS_TOTAL', 88: 'FRAGMENT_OFFSET', 89: 'FORWARDING_STATUS',
    90: 'VPN_ROUTE_DISTINGUISHER', 91: 'mplsTopLabelPrefixLength', 92: 'SRC_TRAFFIC_INDEX', 93: 'DST_TRAFFIC_INDEX',
    94: 'APPLICATION_DESC', 95: 'APPLICATION_ID', 96: 'APPLICATION_NAME', 98: 'postIpDiffServCodePoint',
    99: 'multicastReplicationFactor', 101: 'classificationEngineId', 128: 'DST_AS_PEER', 129: 'SRC_AS_PEER',
    130: 'exporterIPv4Address', 131: 'exporterIPv6Address', 132: 'DROPPED_BYTES', 133: 'DROPPED_PACKETS',
    134: 'DROPPED_BYTES_TOTAL', 135: 'DROPPED_PACKETS_TOTAL', 136: 'flowEndReason', 137: 'commonPropertiesId',
    138: 'observationPointId', 139: 'icmpTypeCodeIPv6', 140: 'MPLS_TOP_LABEL_IPv6_ADDRESS', 141: 'lineCardId',
    142: 'portId', 143: 'meteringProcessId', 144: 'FLOW_EXPORTER', 145: 'templateId', 146: 'wlanChannelId',
    147: 'wlanSSID', 148: 'flowId', 149: 'observationDomainId', 150: 'flowStartSeconds', 151: 'flowEndSeconds',
    152: 'flowStartMilliseconds', 153: 'flowEndMilliseconds', 154: 'flowStartMicroseconds',
    155: 'flowEndMicroseconds', 156: 'flowStartNanoseconds', 157: 'flowEndNanoseconds',
    158: 'flowStartDeltaMicroseconds', 159: 'flowEndDeltaMicroseconds', 160: 'systemInitTimeMilliseconds',
    161: 'flowDurationMilliseconds', 162: 'flowDurationMicroseconds', 163: 'observedFlowTotalCount',
    164: 'ignoredPacketTotalCount', 165: 'ignoredOctetTotalCount', 166: 'notSentFlowTotalCount',
    167: 'notSentPacketTotalCount', 168: 'notSentOctetTotalCount', 169: 'destinationIPv6Prefix',
    170: 'sourceIPv6Prefix', 171: 'postOctetTotalCount', 172: 'postPacketTotalCount', 173: 'flowKeyIndicator',
    174: 'postMCastPacketTotalCount', 175: 'postMCastOctetTotalCount', 176: 'ICMP_IPv4_TYPE', 177: 'ICMP_IPv4_CODE',
    178: 'ICMP_IPv6_TYPE', 179: 'ICMP_IPv6_CODE', 180: 'UDP_SRC_PORT', 181: 'UDP_DST_PORT', 182: 'TCP_SRC_PORT',
    183: 'TCP_DST_PORT', 184: 'TCP_SEQ_NUM', 185: 'TCP_ACK_NUM', 186: 'TCP_WINDOW_SIZE', 187: 'TCP_URGENT_PTR',
    188: 'TCP_HEADER_LEN', 189: 'IP_HEADER_LEN', 190: 'IP_TOTAL_LEN', 191: 'payloadLengthIPv6', 192: 'IP_TTL',
    193: 'nextHeaderIPv6', 194: 'mplsPayloadLength', 195: 'IP_DSCP', 196: 'IP_PRECEDENCE', 197: 'IP_FRAGMENT_FLAGS',
    198: 'DELTA_BYTES_SQUARED', 199: 'TOTAL_BYTES_SQUARED', 200: 'MPLS_TOP_LABEL_TTL',
    201: 'MPLS_LABEL_STACK_OCTETS', 202: 'MPLS_LABEL_STACK_DEPTH', 203: 'MPLS_TOP_LABEL_EXP',
    204: 'IP_PAYLOAD_LENGTH', 205: 'UDP_LENGTH', 206: 'IS_MULTICAST', 207: 'IP_HEADER_WORDS', 208: 'IP_OPTION_MAP',
    209: 'TCP_OPTION_MAP', 210: 'paddingOctets', 211: 'collectorIPv4Address', 212: 'collectorIPv6Address',
    213: 'collectorInterface', 214: 'collectorProtocolVersion', 215: 'collectorTransportProtocol',
    216: 'collectorTransportPort', 217: 'exporterTransportPort', 218: 'tcpSynTotalCount', 219: 'tcpFinTotalCount',
    220: 'tcpRstTotalCount', 221: 'tcpPshTotalCount', 222: 'tcpAckTotalCount', 223: 'tcpUrgTotalCount',
    224: 'ipTotalLength', 225: 'postNATSourceIPv4Address', 226: 'postNATDestinationIPv4Address',
    227: 'postNAPTSourceTransportPort', 228: 'postNAPTDestinationTransportPort', 229: 'natOriginatingAddressRealm',
    230: 'natEvent', 231: 'initiatorOctets', 232: 'responderOctets', 233: 'firewallEvent', 234: 'ingressVRFID',
    235: 'egressVRFID', 236: 'VRFname', 237: 'postMplsTopLabelExp', 238: 'tcpWindowScale', 239: 'biflowDirection',
    240: 'ethernetHeaderLength', 241: 'ethernetPayloadLength', 242: 'ethernetTotalLength', 243: 'dot1qVlanId',
    244: 'dot1qPriority', 245: 'dot1qCustomerVlanId', 246: 'dot1qCustomerPriority', 247: 'metroEvcId',
    248: 'metroEvcType', 249: 'pseudoWireId', 250: 'pseudoWireType', 251: 'pseudoWireControlWord',
    252: 'ingressPhysicalInterface', 253: 'egressPhysicalInterface', 254: 'postDot1qVlanId',
    255: 'postDot1qCustomerVlanId',
}

ИМЕНА_RU = {
    1: "байт", 2: "пакетов", 3: "потоков", 4: "протокол", 5: "ToS", 6: "флаги TCP", 7: "порт источника",
    8: "адрес источника", 9: "маска источника", 10: "входной интерфейс", 11: "порт получателя",
    12: "адрес получателя", 13: "маска получателя", 14: "выходной интерфейс", 15: "следующий узел",
    16: "AS источника", 17: "AS получателя", 18: "следующий узел BGP", 21: "конец (uptime, мс)",
    22: "начало (uptime, мс)", 27: "адрес источника IPv6", 28: "адрес получателя IPv6", 32: "тип и код ICMP",
    56: "MAC источника", 57: "MAC получателя", 58: "VLAN", 60: "версия IP", 61: "направление",
    80: "MAC получателя (выход)", 81: "MAC источника (выход)", 82: "имя интерфейса", 89: "пересылка",
    136: "причина окончания", 148: "номер потока", 150: "начало (с)", 151: "конец (с)", 152: "начало (мс)",
    153: "конец (мс)"}
IPV4_IE = {8, 12, 15, 18, 130}
IPV6_IE = {27, 28, 62, 63, 131}
MAC_IE = {56, 57, 80, 81}
ТЕКСТ_IE = {82, 83, 84, 94, 96}
КЛЮЧИ_IE = {1: "cflow.octets", 2: "cflow.packets", 4: "cflow.protocol", 7: "cflow.srcport", 8: "cflow.srcaddr",
            11: "cflow.dstport", 12: "cflow.dstaddr", 27: "cflow.srcaddrv6", 28: "cflow.dstaddrv6"}
ПЕРЕМЕННАЯ = 65535


def имя_ie(номер: int, предприятие: int = 0) -> str:
    if предприятие:
        return f"элемент {номер} предприятия {предприятие}"
    return ИМЕНА_RU.get(номер) or ИМЕНА_IE.get(номер) or f"элемент {номер}"


def значение_ie(номер: int, байты: bytes, предприятие: int = 0) -> str:
    """Значение элемента для показа: адреса, протокол, время, строки; иначе число или HEX."""
    if not предприятие:
        if номер in IPV4_IE and len(байты) == 4:
            return ip4(байты, 0)
        if номер in IPV6_IE and len(байты) == 16:
            return ip6(байты, 0)
        if номер in MAC_IE and len(байты) == 6:
            return mac(байты, 0)
        if номер in ТЕКСТ_IE:
            return байты.rstrip(b"\0").decode("utf-8", "replace")
        if номер == 4 and len(байты) == 1:
            return f"{байты[0]} ({IP_ПРОТОКОЛЫ.get(байты[0], '?')})"
        if номер in (150, 151) and len(байты) == 4:
            return datetime.fromtimestamp(u32(байты, 0), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if номер in (152, 153) and len(байты) == 8:
            мс = u64(байты, 0)
            return datetime.fromtimestamp(мс / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"
        if номер == 6 and len(байты) in (1, 2):
            return f"0x{int.from_bytes(байты, 'big'):02x}"
    if 0 < len(байты) <= 8:
        return str(int.from_bytes(байты, "big"))
    return байты.hex()


Шаблон = List[Tuple[int, int, int]]                     # (номер элемента, длина, предприятие)


def _ключ(р: Разбор, версия: int, домен: int, номер: int) -> str:
    return f"{р.п.источник}|{версия}|{домен}|{номер}"


def _запомнить(р: Разбор, ключ: str, поля: Шаблон) -> None:
    версии = р.шаблоны.setdefault(ключ, [])
    поля_ = [list(x) for x in поля]
    if not версии or версии[-1][1] != поля_:
        версии.append([р.п.номер, поля_])


def _найти(р: Разбор, ключ: str) -> Optional[Шаблон]:
    """Последнее определение, пришедшее не позже этого пакета."""
    годные = [поля for номер, поля in р.шаблоны.get(ключ, []) if номер <= р.п.номер]
    return [tuple(x) for x in годные[-1]] if годные else None


def _поля_шаблона(д: bytes, место: int, конец: int, число: int, ipfix: bool) -> Tuple[Шаблон, int]:
    поля: Шаблон = []
    for _ in range(число):
        if место + 4 > конец:
            raise ValueError("шаблон длиннее набора")
        номер, длина = u16(д, место), u16(д, место + 2)
        место += 4
        предприятие = 0
        if ipfix and номер & 0x8000:
            if место + 4 > конец:
                raise ValueError("шаблон длиннее набора")
            номер &= 0x7FFF
            предприятие = u32(д, место)
            место += 4
        поля.append((номер, длина, предприятие))
    return поля, место


def _запись(р: Разбор, у, д: bytes, место: int, конец: int, поля: Шаблон, родитель) -> Optional[int]:
    """Одна запись данных по шаблону; None — не помещается."""
    сводка = []
    for номер, длина, предприятие in поля:
        if длина == ПЕРЕМЕННАЯ:
            if место >= конец:
                return None
            длина, место = д[место], место + 1
            if длина == 255:
                if место + 2 > конец:
                    return None
                длина, место = u16(д, место), место + 2
        if место + длина > конец:
            return None
        байты = д[место:место + длина]
        показ = значение_ie(номер, байты, предприятие)
        ключ = КЛЮЧИ_IE.get(номер) if not предприятие else None
        у.поле(имя_ie(номер, предприятие), ключ or f"cflow.ie.{номер}", показ, место, длина,
               int.from_bytes(байты, "big") if 0 < длина <= 8 and номер not in IPV4_IE | IPV6_IE | MAC_IE else показ,
               родитель=родитель)
        # в сводку записи — только адреса своей длины (IPv4 — 4 байта, IPv6 — 16), порты и протокол
        if not предприятие and (номер in (7, 11, 4) or (номер in (8, 12) and длина == 4)
                                or (номер in (27, 28) and длина == 16)):
            сводка.append((номер, показ))
        место += длина
    родитель.текст = _сводка_записи(dict(сводка)) or родитель.текст
    return место


def _сводка_записи(с: Dict[int, str]) -> str:
    от = с.get(8) or с.get(27)
    к = с.get(12) or с.get(28)
    if not от or not к:
        return ""
    return f"{от}:{с.get(7, '?')} → {к}:{с.get(11, '?')}" + (f", {с[4]}" if 4 in с else "")


def netflow_шаблоны(р: Разбор, м: int, конец: int) -> bool:
    """NetFlow v5 — как прежде (prilozh); v9 и IPFIX — наборы, шаблоны и данные по ним."""
    д = р.д
    if конец - м < 16:
        return False
    версия = u16(д, м)
    if версия not in (9, 10):
        return prilozh.netflow(р, м, конец)
    ipfix = версия == 10
    if ipfix:
        if u16(д, м + 2) != конец - м:
            return False
        заголовок, домен = 16, u32(д, м + 12)
    else:
        if конец - м < 20:
            return False
        заголовок, домен = 20, u32(д, м + 16)
    у = р.уровень("NetFlow", "IPFIX" if ipfix else "NetFlow v9", м)
    у.поле("Версия", "cflow.version", версия, м, 2)
    if ipfix:
        у.поле("Длина", "cflow.len", u16(д, м + 2), м + 2, 2)
        у.поле("Время экспорта", "cflow.timestamp",
               datetime.fromtimestamp(u32(д, м + 4), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), м + 4, 4,
               u32(д, м + 4))
        у.поле("Номер", "cflow.sequence", u32(д, м + 8), м + 8, 4)
        у.поле("Домен наблюдения", "cflow.od_id", домен, м + 12, 4)
    else:
        у.поле("Записей", "cflow.count", u16(д, м + 2), м + 2, 2)
        у.поле("Время работы, мс", "cflow.sysuptime", u32(д, м + 4), м + 4, 4)
        у.поле("Время экспорта", "cflow.timestamp",
               datetime.fromtimestamp(u32(д, м + 8), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), м + 8, 4,
               u32(д, м + 8))
        у.поле("Номер", "cflow.sequence", u32(д, м + 12), м + 12, 4)
        у.поле("Источник", "cflow.source_id", домен, м + 16, 4)
    место = м + заголовок
    шаблонов = данных = неизвестных = 0
    while место + 4 <= конец:
        набор, длина = u16(д, место), u16(д, место + 2)
        if длина < 4 or место + длина > конец:
            р.ошибка(f"NetFlow: набор {набор} длиной {длина} не помещается")
            break
        тело, край = место + 4, место + длина
        if набор in ((2, 3) if ipfix else (0, 1)):
            опции = набор in (1, 3)
            п = у.поле(f"{'шаблоны опций' if опции else 'шаблоны'}, {длина} байт", "cflow.flowset_id", набор,
                       место, длина)
            while тело + 4 <= край:
                номер = u16(д, тело)
                if опции and not ipfix:
                    if тело + 6 > край:
                        break
                    область, опций = u16(д, тело + 2), u16(д, тело + 4)
                    поля, тело = _поля_шаблона(д, тело + 6, край, (область + опций) // 4, False)
                elif опции:
                    число = u16(д, тело + 2)
                    поля, тело = _поля_шаблона(д, тело + 6, край, число, True)
                else:
                    число = u16(д, тело + 2)
                    if номер < 256:                        # набор выровнен нулями
                        break
                    поля, тело = _поля_шаблона(д, тело + 4, край, число, ipfix)
                _запомнить(р, _ключ(р, версия, домен, номер), поля)
                шаблонов += 1
                ш = у.поле(f"шаблон {номер}: {len(поля)} элементов", "cflow.template_id", номер, тело, 0, родитель=п)
                for н, дл, пр in поля:
                    у.поле(f"{имя_ie(н, пр)}, {'перем.' if дл == ПЕРЕМЕННАЯ else дл} байт", "cflow.template_field",
                           н, тело, 0, родитель=ш)
        elif набор >= 256:
            поля = _найти(р, _ключ(р, версия, домен, набор))
            п = у.поле(f"данные по шаблону {набор}, {длина} байт", "cflow.flowset_id", набор, место, длина)
            if поля is None:
                неизвестных += 1
                у.поле("шаблон ещё не приходил — записи не разобрать", "cflow.template_missing", набор, тело,
                       длина - 4, родитель=п)
            elif not sum(1 if дл == ПЕРЕМЕННАЯ else дл for _, дл, _ in поля):
                у.поле("шаблон нулевой длины — записи не разобрать", "cflow.template_empty", набор, тело,
                       длина - 4, родитель=п)
            else:
                while тело < край:                                # запись — не меньше байта: цикл конечен
                    з = у.поле(f"запись {данных + 1}", "cflow.record", "", тело, 0, данных, родитель=п)
                    конец_записи = _запись(р, у, д, тело, край, поля, з)
                    if конец_записи is None:
                        п.дети.remove(з)                          # хвост — выравнивание
                        break
                    з.длина = конец_записи - тело
                    тело = конец_записи
                    данных += 1
        else:
            у.поле(f"набор {набор} (резерв), {длина} байт", "cflow.flowset_id", набор, место, длина)
        место = край
    у.длина = место - м
    у.итог = (f"{'IPFIX' if ipfix else 'v9'}: шаблонов {шаблонов}, записей {данных}"
              + (f", без шаблона наборов {неизвестных}" if неизвестных else ""))
    р.п.инфо = "NetFlow " + у.итог
    return True


# == sFlow v5 ==============================================================================

SFLOW_ОБРАЗЦЫ = {1: "образец потока", 2: "образец счётчиков", 3: "расширенный образец потока",
                 4: "расширенный образец счётчиков"}
SFLOW_ЗАПИСИ_ПОТОКА = {1: "заголовок пакета", 2: "Ethernet", 3: "IPv4", 4: "IPv6", 1001: "коммутатор",
                       1002: "маршрутизатор", 1003: "шлюз", 1004: "пользователь", 1005: "URL", 1006: "MPLS"}
SFLOW_ЗАПИСИ_СЧЁТЧИКОВ = {1: "общие счётчики интерфейса", 2: "счётчики Ethernet", 3: "Token Ring",
                          4: "100BaseVG", 5: "VLAN", 7: "LAG", 1001: "процессор"}
# протокол заголовка → канал разборщика пакетов (packet-sflow.h: 1 Ethernet … 11 IPv4, 12 IPv6, 13 MPLS)
SFLOW_ЗАГОЛОВКИ = {1: "Ethernet", 7: "PPP", 11: "IPv4", 12: "IPv6", 13: "MPLS"}
SFLOW_ЗАГОЛОВКИ_ИМЕНА = {1: "Ethernet", 2: "Token Bus", 3: "Token Ring", 4: "FDDI", 5: "Frame Relay", 6: "X.25",
                         7: "PPP", 8: "SMDS", 9: "ATM AAL5", 10: "ATM AAL5-IP", 11: "IPv4", 12: "IPv6", 13: "MPLS",
                         14: "PPP в SONET/SDH", 15: "802.11 MAC", 16: "802.11n A-MPDU", 17: "A-MSDU"}


def _вложенный(канал: str, байты: bytes) -> str:
    """Сводка вложенного (выборочного) пакета: разбирается отдельно, как самостоятельный."""
    from ..razbor import разобрать_пакет  # noqa: PLC0415 — круговой импорт
    п = разобрать_пакет(байты, канал)
    return " / ".join(п.стек) + (f": {п.инфо}" if п.инфо else "")


def sflow(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if конец - м < 28 or u32(д, м) != 5 or u32(д, м + 4) not in (1, 2):
        return False
    адрес_дл = 4 if u32(д, м + 4) == 1 else 16
    место = м + 8 + адрес_дл
    if место + 16 > конец:
        return False
    образцов = u32(д, место + 12)
    у = р.уровень("sFlow", "sFlow v5", м)
    у.поле("Версия", "sflow.version", 5, м, 4)
    агент = ip4(д, м + 8) if адрес_дл == 4 else ip6(д, м + 8)
    у.поле("Агент", "sflow.agent", агент, м + 8, адрес_дл)
    у.поле("Подагент", "sflow.sub_agent_id", u32(д, место), место, 4)
    у.поле("Номер", "sflow.sequence_number", u32(д, место + 4), место + 4, 4)
    у.поле("Время работы, мс", "sflow.sysuptime", u32(д, место + 8), место + 8, 4)
    у.поле("Образцов", "sflow.numsamples", образцов, место + 12, 4)
    место += 16
    виды = []
    for _ in range(min(образцов, 256)):
        if место + 8 > конец:
            break
        формат, длина = u32(д, место), u32(д, место + 4)
        предприятие, вид = формат >> 12, формат & 0xFFF
        тело, край = место + 8, min(место + 8 + длина, конец)
        имя = SFLOW_ОБРАЗЦЫ.get(вид, f"образец {вид}") if not предприятие else f"образец предприятия {предприятие}"
        п = у.поле(f"{имя}, {длина} байт", "sflow.sampletype", вид, место, 8 + длина)
        виды.append(имя)
        if not предприятие and вид in (1, 3):
            _образец_потока(р, у, д, тело, край, вид == 3, п)
        elif not предприятие and вид in (2, 4):
            _образец_счётчиков(у, д, тело, край, вид == 4, п)
        место = место + 8 + длина
    у.длина = min(место, конец) - м
    у.итог = f"агент {агент}, образцов {образцов}"
    р.п.инфо = "sFlow " + у.итог
    return True


def _образец_потока(р: Разбор, у, д: bytes, м: int, конец: int, расширенный: bool, родитель) -> None:
    у.поле("Номер", "sflow.flow_sample.sequence_number", u32(д, м), м, 4, родитель=родитель)
    if расширенный:
        у.поле("Источник", "sflow.flow_sample.source_id_index", f"{u32(д, м + 4)}:{u32(д, м + 8)}", м + 4, 8,
               родитель=родитель)
        место = м + 12
    else:
        у.поле("Источник", "sflow.flow_sample.index", f"{д[м + 4]}:{u32(д, м + 4) & 0xFFFFFF}", м + 4, 4,
               родитель=родитель)
        место = м + 8
    у.поле("Частота выборки", "sflow.flow_sample.sampling_rate", f"1 из {u32(д, место)}", место, 4,
           u32(д, место), родитель=родитель)
    у.поле("Пул", "sflow.flow_sample.sample_pool", u32(д, место + 4), место + 4, 4, родитель=родитель)
    у.поле("Потеряно", "sflow.flow_sample.dropped_packets", u32(д, место + 8), место + 8, 4, родитель=родитель)
    if расширенный:
        у.поле("Вход", "sflow.flow_sample.input_interface", u32(д, место + 16), место + 12, 8, родитель=родитель)
        у.поле("Выход", "sflow.flow_sample.output_interface", u32(д, место + 24), место + 20, 8, родитель=родитель)
        место += 28
    else:
        у.поле("Вход", "sflow.flow_sample.input_interface", u32(д, место + 12), место + 12, 4, родитель=родитель)
        у.поле("Выход", "sflow.flow_sample.output_interface", u32(д, место + 16) & 0x3FFFFFFF, место + 16, 4,
               родитель=родитель)
        место += 20
    записей = u32(д, место)
    место += 4
    for _ in range(min(записей, 255)):
        if место + 8 > конец:
            break
        формат, длина = u32(д, место), u32(д, место + 4)
        предприятие, вид = формат >> 12, формат & 0xFFF
        тело = место + 8
        имя = SFLOW_ЗАПИСИ_ПОТОКА.get(вид, f"запись {вид}") if not предприятие else f"запись предприятия {предприятие}"
        п = у.поле(f"{имя}, {длина} байт", "sflow.flow_record", вид, место, 8 + длина, родитель=родитель)
        if not предприятие and вид == 1 and тело + 16 <= конец:
            протокол, кадр, срезано, дл = (u32(д, тело), u32(д, тело + 4), u32(д, тело + 8), u32(д, тело + 12))
            у.поле("Протокол заголовка", "sflow.header_protocol",
                   f"{протокол} ({SFLOW_ЗАГОЛОВКИ_ИМЕНА.get(протокол, '?')})", тело, 4, протокол, родитель=п)
            у.поле("Длина кадра", "sflow.header.frame_length", кадр, тело + 4, 4, родитель=п)
            у.поле("Срезано", "sflow.header.payload_removed", срезано, тело + 8, 4, родитель=п)
            байты = д[тело + 16:min(тело + 16 + дл, конец)]
            канал = SFLOW_ЗАГОЛОВКИ.get(протокол)
            у.поле("Заголовок пакета", "sflow.header",
                   _вложенный(канал, байты) if канал else байты.hex(), тело + 16, len(байты), родитель=п)
        elif not предприятие and вид == 2 and тело + 24 <= конец:
            у.поле("Длина", "sflow.ethernet.length", u32(д, тело), тело, 4, родитель=п)
            у.поле("MAC источника", "sflow.ethernet.src", mac(д, тело + 4), тело + 4, 6, родитель=п)
            у.поле("MAC получателя", "sflow.ethernet.dst", mac(д, тело + 12), тело + 12, 6, родитель=п)
            у.поле("Тип", "sflow.ethernet.type", f"0x{u32(д, тело + 20):04x}", тело + 20, 4, родитель=п)
        elif not предприятие and вид == 3 and тело + 32 <= конец:
            протокол = u32(д, тело + 4)
            у.поле("Длина", "sflow.ip.length", u32(д, тело), тело, 4, родитель=п)
            у.поле("Поток", "sflow.ipv4",
                   f"{ip4(д, тело + 8)}:{u32(д, тело + 16)} → {ip4(д, тело + 12)}:{u32(д, тело + 20)}, "
                   f"{IP_ПРОТОКОЛЫ.get(протокол, протокол)}", тело + 4, 20, протокол, родитель=п)
        elif not предприятие and вид == 1001 and тело + 16 <= конец:
            у.поле("Коммутатор", "sflow.vlan_in", f"VLAN {u32(д, тело)} (приоритет {u32(д, тело + 4)}) → "
                   f"VLAN {u32(д, тело + 8)} (приоритет {u32(д, тело + 12)})", тело, 16, u32(д, тело), родитель=п)
        elif not предприятие and вид == 1002 and тело + 12 <= конец:
            вид_адреса = u32(д, тело)
            дл_адреса = 4 if вид_адреса == 1 else 16
            адрес = ip4(д, тело + 4) if вид_адреса == 1 else ip6(д, тело + 4)
            у.поле("Маршрутизатор", "sflow.nexthop", f"следующий узел {адрес}, маски /{u32(д, тело + 4 + дл_адреса)}"
                   f" → /{u32(д, тело + 8 + дл_адреса)}", тело, 12 + дл_адреса, адрес, родитель=п)
        место = тело + длина


def _образец_счётчиков(у, д: bytes, м: int, конец: int, расширенный: bool, родитель) -> None:
    у.поле("Номер", "sflow.counters_sample.sequence_number", u32(д, м), м, 4, родитель=родитель)
    место = м + (12 if расширенный else 8)
    записей = u32(д, место)
    место += 4
    for _ in range(min(записей, 255)):
        if место + 8 > конец:
            break
        формат, длина = u32(д, место), u32(д, место + 4)
        предприятие, вид = формат >> 12, формат & 0xFFF
        тело = место + 8
        имя = (SFLOW_ЗАПИСИ_СЧЁТЧИКОВ.get(вид, f"счётчики {вид}") if not предприятие
               else f"счётчики предприятия {предприятие}")
        п = у.поле(f"{имя}, {длина} байт", "sflow.counters_record", вид, место, 8 + длина, родитель=родитель)
        if not предприятие and вид == 1 and тело + 88 <= конец:
            состояние = u32(д, тело + 20)
            # ifIndex, ifType, ifSpeed (8), ifDirection, ifStatus, затем вход: октеты (8), unicast, multicast,
            # broadcast, отброшено, ошибок, неизвестных; выход: октеты (8), unicast, multicast, broadcast,
            # отброшено, ошибок, promiscuous (dissect_sflow_5_generic_interface)
            у.поле("Интерфейс", "sflow.ifindex", f"интерфейс {u32(д, тело)}, тип {u32(д, тело + 4)}, скорость "
                   f"{u64(д, тело + 8)} бит/с, {'включён' if состояние & 1 else 'выключен'} администратором, "
                   f"{'работает' if состояние & 2 else 'не работает'}", тело, 24, u32(д, тело), родитель=п)
            у.поле("Вход", "sflow.ifinoct", f"{u64(д, тело + 24)} байт, ошибок {u32(д, тело + 48)}, "
                   f"отброшено {u32(д, тело + 44)}", тело + 24, 32, u64(д, тело + 24), родитель=п)
            у.поле("Выход", "sflow.ifoutoct", f"{u64(д, тело + 56)} байт, ошибок {u32(д, тело + 80)}, "
                   f"отброшено {u32(д, тело + 76)}", тело + 56, 32, u64(д, тело + 56), родитель=п)
        место = тело + длина


for _порт in (2055, 9995, 9996, 4739):
    prilozh.ПОРТЫ_UDP[_порт] = netflow_шаблоны
prilozh.ПОРТЫ_UDP[6343] = sflow
prilozh.КАК["udp"]["NetFlow/IPFIX"] = netflow_шаблоны
prilozh.КАК["udp"].setdefault("sFlow", sflow)
ДОП_УРОВНИ.setdefault("sFlow", "прикладной")
