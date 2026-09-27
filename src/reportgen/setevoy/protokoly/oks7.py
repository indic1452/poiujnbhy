"""ОКС-7 (SS7) и SIGTRAN: от канала сигнализации до MAP и Q.931.

Документы и разделы:

* M3UA — RFC 4666: общий заголовок (3.1), параметры (3.2, 3.3), DATA и
  Protocol Data (3.3.1), коды ошибок (3.8.1);
* M2UA — RFC 3331: заголовок (3.1), сообщения MAUP и Protocol Data 1/2 (3.3.1);
* M2PA — RFC 4165: заголовок (2.1, 2.2), User Data (2.3.2), Link Status (2.3.1);
* SUA — RFC 3868: заголовок (3.1), параметры и адреса (3.10);
* IUA — RFC 4233: заголовок (3.1), DLCI и Protocol Data (3.2, 3.3);
* MTP2 — ITU-T Q.703, 2.2–2.3 (BSN/BIB/FSN/FIB/LI), 11.1.2 (поле состояния LSSU),
  проверочные биты — CRC-16 (2.2.8);
* MTP3 — ITU-T Q.704, 2.2 (метка маршрутизации ITU, 14-битные пункты), 14.2 (SIO),
  15 (заголовки H0/H1 сообщений управления сетью);
* SCCP — ITU-T Q.713, 2 (указатели), 3 (параметры, 3.4 — адрес), 4 (форматы сообщений);
* TCAP — ITU-T Q.773, 4.2 (сообщения транзакций), 4.2.2 (компоненты), BER — X.690;
* MAP — 3GPP TS 29.002, 17.5 (коды операций), 17.7 (типы аргументов);
* ISUP — ITU-T Q.763, 1 (формат), 3 (параметры), 4 (таблицы сообщений); причина — Q.850;
* Q.931 — ITU-T Q.931, 4.2–4.5 (дискриминатор, ссылка вызова, тип, элементы);
* LAPD — ITU-T Q.921, 3.2–3.6 (адрес SAPI/TEI, поле управления I/S/U).

Разбирается вариант ITU (пункты сигнализации — 14 бит, CIC — 12 бит, TCAP — ITU).
ANSI: метка MTP3 с 24-битными пунктами — когда за ней сходится SCCP (а за меткой ITU
нет); TCAP ANSI (T1.114) — по тегу пакета. Японский TTC не поддерживается: в нём иначе
устроены метка маршрутизации и запасные биты. Незнакомые коды показываются числом.

Регистрация: PPID SCTP (1 IUA, 2 M2UA, 3 M3UA, 4 SUA, 5 M2PA), порты SCTP
(9900, 2904, 2905, 14001, 3565), каналы pcap (140 MTP2, 141 MTP3, 142 SCCP,
177 LAPD с псевдозаголовком Linux, 203 LAPD).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence

from ..chtenie import КАНАЛЫ
from ..pole import Мало, u16, u24, u32
from ..razbor import ДОП_SCTP_PPID, ДОП_SCTP_ПОРТ, ДОП_УРОВНИ, КАНАЛ_В_РАЗБОРЩИК, Разбор, данные

ОБОРВАН = "пакет оборван: заголовок длиннее записанных байт"


# -- общее ----------------------------------------------------------------------------------

def _дальше(р: Разбор, разборщик: Callable[..., bool], м: int, конец: int, что: str, *арг) -> None:
    """Вложенный разбор. Не его данные — «Данные»; обрыв — отметка, уровни выше остаются.

    Обрыв ловится здесь, а не выше: над SIGTRAN стоит SCTP, который при исключении
    убирает все уровни своей нагрузки, — а разобранный заголовок M3UA ценен и без хвоста.
    """
    уровней = len(р.п.уровни)
    try:
        if разборщик(р, м, конец, *арг):
            return
    except (Мало, IndexError):
        if len(р.п.уровни) > уровней:
            р.ошибка(ОБОРВАН)
            р.п.уровни[-1].итог += " [оборван]"
            return
        del р.п.уровни[уровней:]
        р.ошибка(f"{что}: {ОБОРВАН}")
    del р.п.уровни[уровней:]
    _данные(р, м, что, min(конец, len(р.д)))


def _данные(р: Разбор, м: int, что: str, конец: int | None = None) -> None:
    """«Данные» в пределах записанных байт (объявленный конец бывает дальше — при обрыве)."""
    данные(р, м, что, len(р.д) if конец is None else min(конец, len(р.д)))


def _цифры(байты: bytes, нечётно: bool | None = None) -> str:
    """Цифры BCD: первая — в младшем полубайте (Q.713 3.4.2.3, Q.763 3.9, TS 29.002 TBCD).

    ``нечётно`` — признак из заголовка: True — старший полубайт последнего октета —
    заполнитель; None — признака нет, заполнитель 0xF в конце отбрасывается.
    """
    знаки = "0123456789ABCDEF"
    итог = []
    for б in байты:
        итог.append(знаки[б & 15])
        итог.append(знаки[б >> 4])
    if нечётно is True and итог:
        итог.pop()
    elif нечётно is None and итог and итог[-1] == "F":
        итог.pop()
    return "".join(итог)


def _конец_кадра(р: Разбор) -> int:
    """Конец кадра в линии: захват мог записать меньше (snaplen) — тогда вложенные
    разборщики видят обрыв, а не «короткое, но целое» сообщение."""
    return max(len(р.д), р.п.исходная_длина)


def _пункт(пк: int) -> str:
    """Пункт сигнализации ITU (14 бит): число и зона-сеть-пункт 3-8-3 (Q.708)."""
    return f"{пк} ({пк >> 11}-{(пк >> 3) & 0xFF}-{пк & 7})"


# -- SIGTRAN: общий заголовок и параметры -------------------------------------------------------

КЛАССЫ = {0: "MGMT", 1: "Transfer", 2: "SSNM", 3: "ASPSM", 4: "ASPTM", 5: "QPTM", 6: "MAUP",
          7: "CL", 8: "CO", 9: "RKM", 10: "IIM", 11: "M2PA"}
ТИПЫ = {
    (0, 0): "ERR", (0, 1): "NTFY",
    (1, 1): "DATA",
    (2, 1): "DUNA", (2, 2): "DAVA", (2, 3): "DAUD", (2, 4): "SCON", (2, 5): "DUPU", (2, 6): "DRST",
    (3, 1): "ASPUP", (3, 2): "ASPDN", (3, 3): "BEAT", (3, 4): "ASPUP ACK", (3, 5): "ASPDN ACK",
    (3, 6): "BEAT ACK",
    (4, 1): "ASPAC", (4, 2): "ASPIA", (4, 3): "ASPAC ACK", (4, 4): "ASPIA ACK",
    (5, 1): "Data Request", (5, 2): "Data Indication", (5, 3): "Unit Data Request",
    (5, 4): "Unit Data Indication", (5, 5): "Establish Request", (5, 6): "Establish Confirm",
    (5, 7): "Establish Indication", (5, 8): "Release Request", (5, 9): "Release Confirm",
    (5, 10): "Release Indication",
    (6, 1): "Data", (6, 2): "Establish Request", (6, 3): "Establish Confirm", (6, 4): "Release Request",
    (6, 5): "Release Confirm", (6, 6): "Release Indication", (6, 7): "State Request",
    (6, 8): "State Confirm", (6, 9): "State Indication", (6, 10): "Data Retrieval Request",
    (6, 11): "Data Retrieval Confirm", (6, 12): "Data Retrieval Indication",
    (6, 13): "Data Retrieval Complete Indication", (6, 14): "Congestion Indication",
    (6, 15): "Data Acknowledge",
    (7, 1): "CLDT", (7, 2): "CLDR",
    (8, 1): "CORE", (8, 2): "COAK", (8, 3): "COREF", (8, 4): "RELRE", (8, 5): "RELCO", (8, 6): "RESCO",
    (8, 7): "RESRE", (8, 8): "CODT", (8, 9): "CODA", (8, 10): "COERR", (8, 11): "COIT",
    (9, 1): "REG REQ", (9, 2): "REG RSP", (9, 3): "DEREG REQ", (9, 4): "DEREG RSP",
    (10, 1): "REG REQ", (10, 2): "REG RSP", (10, 3): "DEREG REQ", (10, 4): "DEREG RSP",
}
ТИПЫ_IUA_MGMT = {2: "TEI Status Request", 3: "TEI Status Confirm", 4: "TEI Status Indication"}

#: Какие классы и типы сообщений есть у протокола (RFC 4666 3.1.2, RFC 3331 3.1.3,
#: RFC 3868 3.1.2, RFC 4233 3.1.3).
def _ДО(n: int) -> frozenset:
    return frozenset(range(1, n + 1))


КЛАССЫ_ПРОТОКОЛА = {
    "M3UA": {0: frozenset({0, 1}), 1: _ДО(1), 2: _ДО(6), 3: _ДО(6), 4: _ДО(4), 9: _ДО(4)},
    "M2UA": {0: frozenset({0, 1}), 3: _ДО(6), 4: _ДО(4), 6: _ДО(15), 10: _ДО(4)},
    "SUA": {0: frozenset({0, 1}), 2: _ДО(6), 3: _ДО(6), 4: _ДО(4), 7: _ДО(2), 8: _ДО(11), 9: _ДО(4)},
    "IUA": {0: frozenset({0, 1, 2, 3, 4}), 3: _ДО(6), 4: _ДО(4), 5: _ДО(10)},
}
ПОЛНЫЕ = {"M3UA": "MTP3 User Adaptation Layer (RFC 4666)",
          "M2UA": "MTP2 User Adaptation Layer (RFC 3331)",
          "SUA": "SCCP User Adaptation Layer (RFC 3868)",
          "IUA": "ISDN Q.921-User Adaptation Layer (RFC 4233)",
          "M2PA": "MTP2 Peer-to-Peer Adaptation Layer (RFC 4165)"}

#: Теги параметров: тег → (подпись, окончание ключа фильтра, вид значения).
_ОБЩИЕ = {0x0004: ("Info String", "info_string", "текст"),
          0x0007: ("Diagnostic Information", "diagnostic_information", "байты"),
          0x0009: ("Heartbeat Data", "heartbeat_data", "байты"),
          0x000B: ("Traffic Mode Type", "traffic_mode_type", "режим"),
          0x000C: ("Error Code", "error_code", "ошибка"),
          0x000D: ("Status", "status", "состояние"),
          0x0011: ("ASP Identifier", "asp_identifier", "число")}
_M3UA_SUA = {0x0006: ("Routing Context", "routing_context", "числа"),
             0x0012: ("Affected Point Code", "affected_point_code", "пункты"),
             0x0013: ("Correlation ID", "correlation_identifier", "число")}
_ИНТЕРФЕЙС = {0x0001: ("Interface Identifier (Integer)", "interface_identifier_int", "число"),
              0x0003: ("Interface Identifier (Text)", "interface_identifier_text", "текст"),
              0x0008: ("Interface Identifier (Integer Range)", "interface_identifier_range", "байты")}
ПАРАМЕТРЫ = {
    "M3UA": {**_ОБЩИЕ, **_M3UA_SUA,
             0x0200: ("Network Appearance", "network_appearance", "число"),
             0x0204: ("User/Cause", "user_cause", "байты"),
             0x0205: ("Congestion Indications", "congestion_level", "число"),
             0x0206: ("Concerned Destination", "concerned_dpc", "пункт"),
             0x0207: ("Routing Key", "routing_key", "байты"),
             0x0208: ("Registration Result", "registration_result", "байты"),
             0x0209: ("Deregistration Result", "deregistration_result", "байты"),
             0x020A: ("Local Routing Key Identifier", "local_rk_identifier", "число"),
             0x020B: ("Destination Point Code", "dpc", "пункт"),
             0x020C: ("Service Indicators", "service_indicators", "байты"),
             0x020E: ("Originating Point Code List", "opc_list", "пункты"),
             0x0210: ("Protocol Data", "protocol_data", "данные"),
             0x0212: ("Registration Status", "registration_status", "число"),
             0x0213: ("Deregistration Status", "deregistration_status", "число")},
    "M2UA": {**_ОБЩИЕ, **_ИНТЕРФЕЙС,
             0x0013: ("Correlation ID", "correlation_identifier", "число"),
             0x0300: ("Protocol Data 1", "protocol_data_1", "данные"),
             0x0301: ("Protocol Data 2 (TTC)", "protocol_data_2", "данные"),
             0x0302: ("State Request", "state", "число"),
             0x0303: ("State Event", "event", "число"),
             0x0304: ("Congestion Status", "congestion_status", "число"),
             0x0305: ("Discard Status", "discard_status", "число"),
             0x0306: ("Action", "action", "число"),
             0x0307: ("Sequence Number", "sequence_number", "число"),
             0x0308: ("Retrieval Result", "retrieval_result", "число")},
    "SUA": {**_ОБЩИЕ, **_M3UA_SUA,
            0x0101: ("Hop Counter", "hop_counter", "число"),
            0x0102: ("Source Address", "source_address", "адрес"),
            0x0103: ("Destination Address", "destination_address", "адрес"),
            0x0104: ("Source Reference Number", "source_reference_number", "число"),
            0x0105: ("Destination Reference Number", "destination_reference_number", "число"),
            0x0106: ("SCCP Cause", "cause", "байты"),
            0x0107: ("Sequence Number", "sequence_number", "байты"),
            0x0108: ("Receive Sequence Number", "receive_sequence_number", "байты"),
            0x0109: ("ASP Capabilities", "asp_capabilities", "байты"),
            0x010A: ("Credit", "credit", "число"),
            0x010B: ("Data", "data", "данные"),
            0x010C: ("User/Cause", "user_cause", "байты"),
            0x010D: ("Network Appearance", "network_appearance", "число"),
            0x010E: ("Routing Key", "routing_key", "байты"),
            0x0113: ("Importance", "importance", "число"),
            0x0114: ("Message Priority", "message_priority", "число"),
            0x0115: ("Protocol Class", "protocol_class", "класс"),
            0x0116: ("Sequence Control", "sequence_control", "число"),
            0x0117: ("Segmentation", "segmentation", "байты")},
    "IUA": {**_ОБЩИЕ, **_ИНТЕРФЕЙС,
            0x0005: ("DLCI", "dlci", "dlci"),
            0x000E: ("Protocol Data", "protocol_data", "данные"),
            0x000F: ("Release Reason", "release_reason", "число"),
            0x0010: ("TEI Status", "tei_status", "число")},
}
ОШИБКИ_ОБЩИЕ = {0x01: "Invalid Version", 0x03: "Unsupported Message Class",
                0x04: "Unsupported Message Type", 0x06: "Unexpected Message", 0x07: "Protocol Error"}
ОШИБКИ_M3UA = {**ОШИБКИ_ОБЩИЕ, 0x05: "Unsupported Traffic Mode Type", 0x09: "Invalid Stream Identifier",
               0x0D: "Refused - Management Blocking", 0x0E: "ASP Identifier Required",
               0x0F: "Invalid ASP Identifier", 0x11: "Invalid Parameter Value",
               0x12: "Parameter Field Error", 0x13: "Unexpected Parameter",
               0x14: "Destination Status Unknown", 0x15: "Invalid Network Appearance",
               0x16: "Missing Parameter", 0x19: "Invalid Routing Context",
               0x1A: "No Configured AS for ASP"}
РЕЖИМЫ = {1: "Override", 2: "Loadshare", 3: "Broadcast"}
СОСТОЯНИЯ = {(1, 2): "AS-INACTIVE", (1, 3): "AS-ACTIVE", (1, 4): "AS-PENDING",
             (2, 1): "Insufficient ASP resources active in AS", (2, 2): "Alternate ASP Active",
             (2, 3): "ASP Failure"}
#: Индикатор службы (Q.704 14.2.1) — только несомненные значения.
СЛУЖБЫ = {0: "SNM (управление сетью)", 1: "SNTM (обслуживание)", 3: "SCCP", 4: "TUP", 5: "ISUP",
          9: "B-ISUP", 13: "BICC"}
СЕТИ = {0: "международная", 1: "международная (запас)", 2: "национальная", 3: "национальная (запас)"}


def _параметры(д: bytes, м: int, конец: int) -> tuple[list[tuple[int, int, int]], bool] | None:
    """Параметры TLV SIGTRAN (RFC 4666 3.2): [(тег, начало, длина)] и признак обрыва записи.

    Длина параметра — без выравнивания, выравнивание нулями до 4 байт; параметры
    заполняют сообщение ровно до его конца. Не сошлось — None (данные не наши).
    """
    доступно = min(конец, len(д))
    итог = []
    место = м
    while место < конец:
        if место + 4 > доступно:
            return итог, True
        тег, дл = u16(д, место), u16(д, место + 2)
        if дл < 4 or место + дл > конец:
            return None
        итог.append((тег, место, дл))
        шаг = (дл + 3) // 4 * 4
        if место + шаг > конец:          # последний параметр без выравнивания
            шаг = дл
        if any(д[место + дл:min(место + шаг, доступно)]):
            return None                  # выравнивание — только нули
        место += шаг
    return итог, False


def _заголовок_sigtran(д: bytes, м: int, конец: int, протокол: str):
    """Проверка общего заголовка (RFC 4666 3.1): версия 1, резерв 0, класс и тип
    протокола, длина — ровно до конца куска DATA SCTP. Итог — (класс, тип, параметры, обрыв)."""
    if конец - м < 8 or м + 8 > len(д):
        return None
    версия, резерв, класс, тип, длина = д[м], д[м + 1], д[м + 2], д[м + 3], u32(д, м + 4)
    if версия != 1 or резерв != 0 or длина != конец - м:
        return None
    if тип not in КЛАССЫ_ПРОТОКОЛА[протокол].get(класс, ()):
        return None
    разбор = _параметры(д, м + 8, конец)
    if разбор is None:
        return None
    return класс, тип, разбор[0], разбор[1]


def _имя_типа(протокол: str, класс: int, тип: int) -> str:
    if протокол == "IUA" and класс == 0 and тип in ТИПЫ_IUA_MGMT:
        return ТИПЫ_IUA_MGMT[тип]
    return ТИПЫ.get((класс, тип), str(тип))


def _sigtran(р: Разбор, м: int, конец: int, протокол: str) -> bool:
    """M3UA, M2UA, SUA, IUA: общий заголовок, параметры TLV деревом, вложенные данные."""
    проверка = _заголовок_sigtran(р.д, м, конец, протокол)
    if проверка is None:
        return False
    класс, тип, параметры, оборван = проверка
    д = р.д
    к = протокол.lower()
    у = р.уровень(протокол, ПОЛНЫЕ[протокол], м)
    у.поле("Версия", f"{к}.version", д[м], м, 1)
    у.поле("Резерв", f"{к}.reserved", д[м + 1], м + 1, 1)
    у.поле("Класс сообщения", f"{к}.message_class", f"{класс} ({КЛАССЫ[класс]})", м + 2, 1, класс)
    имя = _имя_типа(протокол, класс, тип)
    у.поле("Тип сообщения", f"{к}.message_type", f"{тип} ({имя})", м + 3, 1, тип)
    у.поле("Длина сообщения", f"{к}.message_length", конец - м, м + 4, 4)
    у.длина = конец - м
    у.итог = f"{КЛАССЫ[класс]} {имя}"
    р.п.инфо = f"{протокол} {у.итог}"
    таблица = ПАРАМЕТРЫ[протокол]
    вложенные = []
    доступно = len(д)
    сводка = []
    for тег, место, дл in параметры:
        подпись, окончание, вид = таблица.get(тег, (f"Параметр 0x{тег:04x}", "parameter_value", "байты"))
        значение_м, значение_дл = место + 4, дл - 4
        есть = min(значение_дл, max(0, доступно - значение_м))
        п = у.поле(подпись, f"{к}.parameter_tag", f"0x{тег:04x}", место, дл, тег)
        у.поле("Длина параметра", f"{к}.parameter_length", дл, место + 2, 2, родитель=п)
        if есть < значение_дл:
            п.текст = "(оборван)"
            оборван = True
            if вид == "данные":
                вложенные.append((тег, значение_м, значение_м + значение_дл, п))
            continue
        значение = д[значение_м:значение_м + значение_дл]
        ключ = f"{к}.{окончание}"
        текст = _значение_параметра(у, п, протокол, ключ, вид, значение, значение_м)
        if текст is not None:
            п.текст = текст
            if тег in (0x0006, 0x0001, 0x0003, 0x000C, 0x0011, 0x0302, 0x0303):
                сводка.append(f"{подпись} {текст}")
        if вид in ("данные", "адрес"):
            вложенные.append((тег, значение_м, значение_м + значение_дл, п))
    if сводка:
        у.итог += " (" + ", ".join(сводка[:2]) + ")"
        р.п.инфо = f"{протокол} {у.итог}"
    if оборван:
        р.ошибка(f"{протокол}: {ОБОРВАН}")
        у.итог += " [оборван]"
    _нагрузка_sigtran(р, у, протокол, класс, тип, вложенные)
    return True


def _значение_параметра(у, п, протокол: str, ключ: str, вид: str, значение: bytes, м: int) -> str | None:
    """Значение параметра полями под ним; итог — текст для подписи параметра."""
    дл = len(значение)
    if вид == "текст":
        текст = значение.decode("utf-8", "replace")
        у.поле("Значение", ключ, текст, м, дл, родитель=п)
        return f"«{текст}»"
    if вид in ("число", "режим", "ошибка") and дл == 4:
        x = u32(значение, 0)
        if вид == "режим":
            текст = f"{x} ({РЕЖИМЫ.get(x, 'неизвестный')})"
        elif вид == "ошибка":
            имена = ОШИБКИ_M3UA if протокол in ("M3UA", "SUA") else ОШИБКИ_ОБЩИЕ
            текст = f"0x{x:02x} ({имена.get(x, 'неизвестная')})"
        else:
            текст = str(x)
        у.поле("Значение", ключ, текст, м, 4, x, родитель=п)
        return текст
    if вид == "числа" and дл % 4 == 0 and дл:
        числа = [u32(значение, i) for i in range(0, дл, 4)]
        for i, x in enumerate(числа):
            у.поле("Значение", ключ, x, м + 4 * i, 4, родитель=п)
        return ", ".join(map(str, числа))
    if вид in ("пункт", "пункты") and дл % 4 == 0 and дл:
        пункты = []
        for i in range(0, дл, 4):
            маска, пк = значение[i], u24(значение, i + 1)
            if вид == "пункты":
                у.поле("Маска", ключ + "_mask", маска, м + i, 1, родитель=п)
            у.поле("Пункт", ключ, _пункт(пк), м + i + 1, 3, пк, родитель=п)
            пункты.append(str(пк))
        return ", ".join(пункты)
    if вид == "состояние" and дл == 4:
        тип, инфо = u16(значение, 0), u16(значение, 2)
        у.поле("Тип состояния", ключ + "_type", тип, м, 2, родитель=п)
        у.поле("Состояние", ключ + "_info", f"{инфо} ({СОСТОЯНИЯ.get((тип, инфо), 'неизвестное')})",
               м + 2, 2, инфо, родитель=п)
        return СОСТОЯНИЯ.get((тип, инфо), f"{тип}/{инфо}")
    if вид == "dlci" and дл == 4:
        # Как адрес Q.921: SAPI — биты 8–3 первого октета, TEI — биты 8–2 второго (RFC 4233 3.2).
        sapi, tei = значение[0] >> 2, значение[1] >> 1
        у.поле("SAPI", ключ + ".sapi", sapi, м, 1, родитель=п)
        у.поле("TEI", ключ + ".tei", tei, м + 1, 1, родитель=п)
        return f"SAPI {sapi}, TEI {tei}"
    if вид == "класс" and дл == 4:
        у.поле("Класс", ключ, значение[3] & 0x0F, м + 3, 1, родитель=п)
        у.поле("Возврат при ошибке", ключ + ".return_on_error", значение[3] >> 7, м + 3, 1, родитель=п)
        return f"класс {значение[3] & 0x0F}"
    if вид == "адрес":
        return None
    if вид == "данные":
        return f"{дл} байт"
    у.поле("Значение", ключ, значение.hex() or "(пусто)", м, дл, родитель=п)
    return None


def _нагрузка_sigtran(р: Разбор, у, протокол: str, класс: int, тип: int, вложенные) -> None:
    """Protocol Data (M3UA, M2UA, IUA) и Data (SUA) — дальше тем же механизмом."""
    к = протокол.lower()
    адреса: dict[int, int] = {}
    for тег, начало, конец, п in вложенные:
        if протокол == "SUA" and тег in (0x0102, 0x0103):
            ssn = _sua_адрес(р, у, п, начало, конец, "sua.source" if тег == 0x0102 else "sua.destination")
            if ssn is not None:
                адреса[тег] = ssn
    for тег, начало, конец, п in вложенные:
        if протокол == "M3UA" and тег == 0x0210 and (класс, тип) == (1, 1):
            if конец - начало < 12:
                р.ошибка("M3UA: Protocol Data короче 12 байт")
                continue
            д = р.д
            if начало + 12 > len(д):
                return
            opc, dpc, si, ni, mp, sls = u32(д, начало), u32(д, начало + 4), д[начало + 8], д[начало + 9], \
                д[начало + 10], д[начало + 11]
            у.поле("OPC", "m3ua.protocol_data_opc", _пункт(opc), начало, 4, opc, родитель=п)
            у.поле("DPC", "m3ua.protocol_data_dpc", _пункт(dpc), начало + 4, 4, dpc, родитель=п)
            у.поле("SI", "m3ua.protocol_data_si", f"{si} ({СЛУЖБЫ.get(si, 'неизвестная')})", начало + 8, 1, si,
                   родитель=п)
            у.поле("NI", "m3ua.protocol_data_ni", f"{ni} ({СЕТИ.get(ni, 'неизвестная')})", начало + 9, 1, ni,
                   родитель=п)
            у.поле("MP", "m3ua.protocol_data_mp", mp, начало + 10, 1, родитель=п)
            у.поле("SLS", "m3ua.protocol_data_sls", sls, начало + 11, 1, родитель=п)
            у.поле("", "mtp3.opc", opc, у.смещение, 0)
            у.поле("", "mtp3.dpc", dpc, у.смещение, 0)
            у.поле("", "mtp3.service_indicator", si, у.смещение, 0)
            у.итог += f", OPC {opc} → DPC {dpc}, {СЛУЖБЫ.get(si, f'SI {si}')}"
            р.п.инфо = f"M3UA {у.итог}"
            _пользователь_mtp(р, si, начало + 12, конец)
            return
        if протокол == "M2UA" and тег in (0x0300, 0x0301) and (класс, тип) == (6, 1):
            if тег == 0x0301 and начало < len(р.д):
                # TTC: первый октет — LI с приоритетом, дальше сообщение MTP3.
                у.поле("LI (приоритет TTC)", "m2ua.protocol_data_2_li", р.д[начало], начало, 1, родитель=п)
                начало += 1
            _дальше(р, mtp3, начало, конец, "Protocol Data M2UA")
            return
        if протокол == "IUA" and тег == 0x000E and класс == 5 and тип in (1, 2, 3, 4):
            _дальше(р, q931, начало, конец, "Protocol Data IUA")
            return
        if протокол == "SUA" and тег == 0x010B:
            if (класс, тип) == (7, 1):
                ssn = (адреса.get(0x0103), адреса.get(0x0102))
                _дальше(р, tcap, начало, конец, "данные SUA", ssn)
            else:
                _данные(р, начало, "данные SUA", конец)
            return
        if тег in (0x0210, 0x0300, 0x0301, 0x000E, 0x010B):
            _данные(р, начало, f"данные {к.upper()}", конец)
            return


def _sua_адрес(р: Разбор, у, п, м: int, конец: int, ключ: str) -> int | None:
    """Адрес SUA (RFC 3868 3.10.2): указатель маршрутизации, индикатор, подпараметры TLV."""
    д = р.д
    if конец - м < 4:
        return None
    ri, ai = u16(д, м), u16(д, м + 2)
    у.поле("Указатель маршрутизации", ключ + ".routing_indicator",
           {1: "по GT", 2: "по SSN и PC", 3: "по имени узла", 4: "по SSN и IP"}.get(ri, str(ri)), м, 2, ri,
           родитель=п)
    у.поле("Индикатор адреса", ключ + ".address_indicator", f"0x{ai:04x}", м + 2, 2, ai, родитель=п)
    разбор = _параметры(д, м + 4, конец)
    ssn = None
    части = []
    for тег, место, дл in (разбор[0] if разбор else []):
        значение = д[место + 4:место + дл]
        if тег == 0x8003 and len(значение) == 4:
            ssn = значение[3]
            у.поле("SSN", ключ + ".ssn", ssn, место + 7, 1, родитель=п)
            части.append(f"SSN {ssn}")
        elif тег == 0x8002 and len(значение) == 4:
            пк = u32(значение, 0)
            у.поле("Пункт", ключ + ".pc", _пункт(пк), место + 4, 4, пк, родитель=п)
            части.append(f"PC {пк}")
        elif тег == 0x8001 and len(значение) >= 8:
            gti, цифр, tt, np, nai = значение[3], значение[4], значение[5], значение[6], значение[7]
            гт = у.поле("Глобальный заголовок", ключ + ".gt", f"GTI {gti}", место, дл, родитель=п)
            у.поле("GTI", ключ + ".gti", gti, место + 7, 1, родитель=гт)
            у.поле("Цифр", ключ + ".number_of_digits", цифр, место + 8, 1, родитель=гт)
            у.поле("Тип трансляции", ключ + ".translation_type", tt, место + 9, 1, родитель=гт)
            у.поле("План нумерации", ключ + ".numbering_plan", np, место + 10, 1, родитель=гт)
            у.поле("Характер адреса", ключ + ".nature_of_address", nai, место + 11, 1, родитель=гт)
            цифры = _цифры(значение[8:], None)[:цифр]
            у.поле("Цифры", ключ + ".gt_digits", цифры, место + 12, дл - 12, родитель=гт)
            гт.текст = f"GT {цифры}"
            части.append(f"GT {цифры}")
    п.текст = ", ".join(части) or f"RI {ri}"
    return ssn


# -- SIGTRAN: входы по PPID и порту ------------------------------------------------------------

def m3ua(р: Разбор, м: int, конец: int) -> bool:
    """M3UA (RFC 4666): заголовок 3.1, параметры 3.2/3.3, DATA → MTP3-пользователь по SI."""
    return _sigtran(р, м, конец, "M3UA")


def m2ua(р: Разбор, м: int, конец: int) -> bool:
    """M2UA (RFC 3331): заголовок 3.1, MAUP Data → Protocol Data 1/2 (3.3.1.1) → MTP3."""
    return _sigtran(р, м, конец, "M2UA")


def sua(р: Разбор, м: int, конец: int) -> bool:
    """SUA (RFC 3868): заголовок 3.1, адреса 3.10.2, CLDT → Data → TCAP."""
    return _sigtran(р, м, конец, "SUA")


def iua(р: Разбор, м: int, конец: int) -> bool:
    """IUA (RFC 4233): заголовок 3.1, DLCI (3.2), QPTM Data/Unit Data → Q.931."""
    return _sigtran(р, м, конец, "IUA")


M2PA_СОСТОЯНИЯ = {1: "Alignment", 2: "Proving Normal", 3: "Proving Emergency", 4: "Ready",
                  5: "Processor Outage", 6: "Processor Recovered", 7: "Busy", 8: "Busy Ended",
                  9: "Out of Service"}


def m2pa(р: Разбор, м: int, конец: int) -> bool:
    """M2PA (RFC 4165): общий заголовок (2.1: версия 1, класс 11), заголовок M2PA (2.2:
    BSN/FSN по 24 бита, неиспользуемые октеты — нули), User Data (2.3.2: октет LI с
    приоритетом, затем MTP3) и Link Status (2.3.1)."""
    д = р.д
    if конец - м < 16 or м + 16 > len(д):
        return False
    версия, запас, класс, тип, длина = д[м], д[м + 1], д[м + 2], д[м + 3], u32(д, м + 4)
    if версия != 1 or запас != 0 or класс != 11 or тип not in (1, 2) or длина != конец - м:
        return False
    if д[м + 8] or д[м + 12]:
        return False
    if тип == 2:
        if длина < 20 or м + 20 > len(д) or u32(д, м + 16) not in M2PA_СОСТОЯНИЯ:
            return False
    elif 16 < длина < 16 + 6:            # данные есть, но короче LI + SIO + метки
        return False
    у = р.уровень("M2PA", ПОЛНЫЕ["M2PA"], м)
    у.поле("Версия", "m2pa.version", версия, м, 1)
    у.поле("Запас", "m2pa.spare", запас, м + 1, 1)
    у.поле("Класс сообщения", "m2pa.class", "11 (M2PA)", м + 2, 1, класс)
    у.поле("Тип сообщения", "m2pa.type", "User Data" if тип == 1 else "Link Status", м + 3, 1, тип)
    у.поле("Длина сообщения", "m2pa.length", длина, м + 4, 4)
    bsn, fsn = u24(д, м + 9), u24(д, м + 13)
    у.поле("Не используется", "m2pa.unused", 0, м + 8, 1)
    у.поле("BSN", "m2pa.bsn", bsn, м + 9, 3)
    у.поле("Не используется", "m2pa.unused", 0, м + 12, 1)
    у.поле("FSN", "m2pa.fsn", fsn, м + 13, 3)
    у.длина = 16
    if тип == 2:
        состояние = u32(д, м + 16)
        у.поле("Состояние", "m2pa.status", f"{состояние} ({M2PA_СОСТОЯНИЯ[состояние]})", м + 16, 4, состояние)
        if длина > 20:
            у.поле("Заполнитель", "m2pa.filler", f"{длина - 20} байт", м + 20, длина - 20)
        у.длина = длина
        у.итог = f"Link Status {M2PA_СОСТОЯНИЯ[состояние]}, BSN {bsn}, FSN {fsn}"
        р.п.инфо = "M2PA " + у.итог
        return True
    у.итог = f"User Data, BSN {bsn}, FSN {fsn}"
    р.п.инфо = "M2PA " + у.итог
    if длина > 16:
        if м + 16 >= len(д):
            р.ошибка(f"M2PA: {ОБОРВАН}")
            у.итог += " [оборван]"
            return True
        li = д[м + 16]
        у.поле("Приоритет", "m2pa.li_priority", li >> 6, м + 16, 1)
        у.поле("Запас LI", "m2pa.li_spare", li & 0x3F, м + 16, 1)
        у.длина = 17
        _дальше(р, mtp3, м + 17, конец, "данные M2PA")
    return True


# -- MTP2 (Q.703) --------------------------------------------------------------------------------

СОСТОЯНИЯ_LSSU = {0: "SIO (вне фазирования)", 1: "SIN (нормальное)", 2: "SIE (аварийное)",
                  3: "SIOS (не в работе)", 4: "SIPO (отказ процессора)", 5: "SIB (занято)"}


def _crc16_x25(данные: bytes) -> int:
    """Проверочные биты MTP2 (Q.703 2.2.8): CRC-16 x^16+x^12+x^5+1, как FCS HDLC."""
    crc = 0xFFFF
    for б in данные:
        crc ^= б
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def mtp2(р: Разбор, м: int) -> None:
    """Сигнальная единица MTP2 (Q.703 2.2): BSN/BIB, FSN/FIB, LI; FISU, LSSU, MSU → MTP3.

    LI (2.3.3) — число октетов после LI до проверочных битов (63 — «63 и больше»).
    Захват LINKTYPE_MTP2 обычно без FCS; если LI сходится только с FCS и CRC
    верна — FCS показывается. Запасные биты октета LI в ITU — нули.
    """
    д = р.д
    у = р.уровень("MTP2", "Message Transfer Part Level 2 (Q.703)", м)
    р.нужно(м, 3)
    bsn, bib, fsn, fib = д[м] & 0x7F, д[м] >> 7, д[м + 1] & 0x7F, д[м + 1] >> 7
    li, запас = д[м + 2] & 0x3F, д[м + 2] >> 6
    у.поле("BSN", "mtp2.bsn", bsn, м, 1)
    у.поле("BIB", "mtp2.bib", bib, м, 1)
    у.поле("FSN", "mtp2.fsn", fsn, м + 1, 1)
    у.поле("FIB", "mtp2.fib", fib, м + 1, 1)
    у.поле("LI", "mtp2.li", li, м + 2, 1)
    у.поле("Запас", "mtp2.spare", запас, м + 2, 1, плохо=bool(запас))
    у.длина = 3
    # Длина в линии: захват мог записать не всё — LI сверяется с исходной длиной.
    всего = _конец_кадра(р) - м

    def сходится(n: int) -> bool:
        return n == li if li < 63 else n >= 63

    fcs = False
    if not сходится(всего - 3) and сходится(всего - 5) and len(д) - м == всего:
        fcs = _crc16_x25(д[м:len(д) - 2]) == (д[-2] | (д[-1] << 8))
    верно = (сходится(всего - 3) or fcs) and not запас
    if верно and li in (1, 2) and м + 3 < len(д) and д[м + 3] >> 3:
        верно = False                    # запасные биты 8–4 поля состояния (11.1.2)
    конец = м + всего - (2 if fcs else 0)
    if fcs:
        у.поле("FCS", "mtp2.fcs_16", f"0x{д[-2] | (д[-1] << 8):04x} [верна]", len(д) - 2, 2,
               д[-2] | (д[-1] << 8))
    вид = "FISU" if li == 0 else ("LSSU" if li in (1, 2) else "MSU")
    у.итог = f"{вид}, BSN {bsn}/{bib}, FSN {fsn}/{fib}"
    if not верно:
        у.поле("", "mtp2.li_mismatch", 1, м + 2, 0)
        р.ошибка("MTP2: LI не сходится с длиной сигнальной единицы (или запасные биты не нули)")
        у.итог += " [LI не сходится]"
        р.п.инфо = "MTP2 " + у.итог
        _данные(р, м + 3, "не сигнальная единица MTP2", конец)
        return
    р.п.инфо = "MTP2 " + у.итог
    if вид == "LSSU":
        р.нужно(м + 3, 1)
        sf = д[м + 3] & 7
        у.поле("Поле состояния", "mtp2.sf", СОСТОЯНИЯ_LSSU.get(sf, str(sf)), м + 3, li, sf)
        у.длина = 3 + li
        у.итог = f"LSSU {СОСТОЯНИЯ_LSSU.get(sf, f'SF {sf}').split(' ')[0]}, BSN {bsn}/{bib}, FSN {fsn}/{fib}"
        р.п.инфо = "MTP2 " + у.итог
    elif вид == "MSU":
        mtp3(р, м + 3, конец)


# -- MTP3 (Q.704) --------------------------------------------------------------------------------

#: Заголовки H0/H1 (Q.704 15.2; Q.707 для SLTM/SLTA) — только несомненные.
MTP3_УПРАВЛЕНИЕ = {(0, 1, 1): "COO", (0, 1, 2): "COA", (0, 1, 5): "CBD", (0, 1, 6): "CBA",
                   (0, 4, 1): "TFP", (0, 4, 5): "TFA", (0, 7, 1): "TRA", (0, 10, 1): "UPU",
                   (1, 1, 1): "SLTM", (1, 1, 2): "SLTA"}


def mtp3(р: Разбор, м: int, конец: int) -> bool:
    """MTP3 (Q.704): SIO (14.2), метка маршрутизации ITU (2.2: DPC 14, OPC 14, SLS 4 бита,
    передаётся младшим битом вперёд), дальше — пользователь по индикатору службы.

    Своих проверяемых полей у MTP3 почти нет, поэтому его признаёт только
    содержащий уровень (MTP2, M2UA, M2PA, канал pcap); здесь проверяется длина.
    """
    д = р.д
    if конец - м < 5:
        return False
    р.нужно(м, 5)
    sio = д[м]
    si, запас, ni = sio & 15, (sio >> 4) & 3, sio >> 6
    if si == 3 and not _годен_sccp(д, м + 5, конец) and _годен_sccp(д, м + 8, конец):
        return _mtp3_ansi(р, м, конец)
    у = р.уровень("MTP3", "Message Transfer Part Level 3 (Q.704)", м)
    п = у.поле("SIO", "mtp3.sio", f"0x{sio:02x}", м, 1, sio)
    у.поле("Индикатор службы", "mtp3.service_indicator", f"{si} ({СЛУЖБЫ.get(si, 'неизвестная')})", м, 1, si,
           родитель=п)
    у.поле("Индикатор сети", "mtp3.network_indicator", f"{ni} ({СЕТИ[ni]})", м, 1, ni, родитель=п)
    у.поле("Запас", "mtp3.spare", запас, м, 1, родитель=п)
    метка = int.from_bytes(д[м + 1:м + 5], "little")
    dpc, opc, sls = метка & 0x3FFF, (метка >> 14) & 0x3FFF, метка >> 28
    у.поле("DPC", "mtp3.dpc", _пункт(dpc), м + 1, 4, dpc)
    у.поле("OPC", "mtp3.opc", _пункт(opc), м + 1, 4, opc)
    у.поле("SLS", "mtp3.sls", sls, м + 1, 4)
    у.длина = 5
    у.итог = f"OPC {opc} → DPC {dpc}, {СЛУЖБЫ.get(si, f'SI {si}')}, SLS {sls}"
    if not р.п.источник:
        р.адреса(str(opc), str(dpc))
    р.п.инфо = "MTP3 " + у.итог
    if si in (0, 1) and м + 6 <= min(конец, len(д)):
        h0, h1 = д[м + 5] & 15, д[м + 5] >> 4
        имя = MTP3_УПРАВЛЕНИЕ.get((si, h0, h1))
        у.поле("H0", "mtp3mg.h0", h0, м + 5, 1)
        у.поле("H1", "mtp3mg.h1", h1, м + 5, 1)
        у.длина = 6
        у.итог += f", H0={h0} H1={h1}" + (f" ({имя})" if имя else "")
        р.п.инфо = "MTP3 " + у.итог
        # SLTM/SLTA (Q.707 5.8): октет с длиной образца в старшем полубайте, затем образец.
        if имя in ("SLTM", "SLTA") and м + 7 <= конец <= len(д) and м + 7 + (д[м + 6] >> 4) == конец:
            дл, образец = д[м + 6] >> 4, д[м + 7:конец].hex()
            у.поле("Длина образца", "mtp3mg.test.length", дл, м + 6, 1)
            у.поле("Тестовый образец", "mtp3mg.test.pattern", образец, м + 7, дл)
            у.длина = 7 + дл
            у.итог += f", образец {образец}"
            р.п.инфо = "MTP3 " + у.итог
            return True
        if м + 6 < конец:
            _данные(р, м + 6, "сообщение управления MTP3", min(конец, len(д)))
        return True
    _пользователь_mtp(р, si, м + 5, конец)
    return True


def _годен_sccp(д: bytes, м: int, конец: int) -> bool:
    """Сходится ли строение SCCP (без добавления уровней); обрыв — не сходится."""
    try:
        return _проверка_sccp(д, м, конец) is not None
    except (Мало, IndexError):
        return False


def _пункт_ansi(пк: int) -> str:
    """Пункт сигнализации ANSI (24 бита): сеть-кластер-участник."""
    return f"{пк >> 16}-{(пк >> 8) & 0xFF}-{пк & 0xFF}"


def _mtp3_ansi(р: Разбор, м: int, конец: int) -> bool:
    """MTP3 ANSI (T1.111; раскладка MTP3_ANSI из pycrate): SIO с приоритетом в запасных битах,
    DPC и OPC по 24 бита (участник передаётся первым), SLS — октет. Признаётся, только когда
    за 5-байтовой меткой ITU SCCP не сходится, а за 8-байтовой ANSI — сходится."""
    д = р.д
    sio = д[м]
    si, приоритет, ni = sio & 15, (sio >> 4) & 3, sio >> 6
    dpc, opc, sls = int.from_bytes(д[м + 1:м + 4], "little"), int.from_bytes(д[м + 4:м + 7], "little"), д[м + 7]
    у = р.уровень("MTP3", "Message Transfer Part Level 3 (ANSI T1.111)", м)
    п = у.поле("SIO", "mtp3.sio", f"0x{sio:02x}", м, 1, sio)
    у.поле("Индикатор службы", "mtp3.service_indicator", f"{si} ({СЛУЖБЫ.get(si, 'неизвестная')})", м, 1, si,
           родитель=п)
    у.поле("Индикатор сети", "mtp3.network_indicator", f"{ni} ({СЕТИ[ni]})", м, 1, ni, родитель=п)
    у.поле("Приоритет", "mtp3.priority", приоритет, м, 1, родитель=п)
    у.поле("DPC", "mtp3.dpc", _пункт_ansi(dpc), м + 1, 3, dpc)
    у.поле("OPC", "mtp3.opc", _пункт_ansi(opc), м + 4, 3, opc)
    у.поле("SLS", "mtp3.sls", sls, м + 7, 1)
    у.длина = 8
    у.итог = f"ANSI, OPC {_пункт_ansi(opc)} → DPC {_пункт_ansi(dpc)}, {СЛУЖБЫ.get(si, f'SI {si}')}, SLS {sls}"
    if not р.п.источник:
        р.адреса(_пункт_ansi(opc), _пункт_ansi(dpc))
    р.п.инфо = "MTP3 " + у.итог
    _пользователь_mtp(р, si, м + 8, конец)
    return True


def _пользователь_mtp(р: Разбор, si: int, м: int, конец: int) -> None:
    """Пользователь MTP по индикатору службы: 3 — SCCP, 5 — ISUP, прочее — данные."""
    if м >= конец:
        return
    if si == 3:
        _дальше(р, sccp, м, конец, "не SCCP")
    elif si == 5:
        _дальше(р, isup, м, конец, "не ISUP")
    else:
        _данные(р, м, f"пользователь MTP, SI {si}", min(конец, len(р.д)))


# -- SCCP (Q.713) --------------------------------------------------------------------------------

SCCP_ТИПЫ = {0x01: "CR", 0x02: "CC", 0x03: "CREF", 0x04: "RLSD", 0x05: "RLC", 0x06: "DT1", 0x07: "DT2",
             0x08: "AK", 0x09: "UDT", 0x0A: "UDTS", 0x0B: "ED", 0x0C: "EA", 0x0D: "RSR", 0x0E: "RSC",
             0x0F: "ERR", 0x10: "IT", 0x11: "XUDT", 0x12: "XUDTS", 0x13: "LUDT", 0x14: "LUDTS"}
#: Строение сообщений (Q.713 4): фиксированные поля [(имя, ключ, длина)], обязательные
#: переменные [(код параметра)], есть ли необязательная часть.
_ФИКС = {"slr": ("Исходная местная ссылка", "sccp.slr", 3), "dlr": ("Местная ссылка получателя", "sccp.dlr", 3),
         "класс": ("Класс протокола", "sccp.class", 1), "переходы": ("Счётчик переходов", "sccp.hops", 1),
         "возврат": ("Причина возврата", "sccp.return_cause", 1),
         "отказ": ("Причина отказа", "sccp.refusal_cause", 1),
         "освобождение": ("Причина освобождения", "sccp.release_cause", 1),
         "ошибка": ("Причина ошибки", "sccp.error_cause", 1),
         "сегм": ("Сегментация/сборка", "sccp.more", 1),
         "посл": ("Нумерация/сегментация", "sccp.sequencing_segmenting", 2),
         "кредит": ("Кредит", "sccp.credit", 1)}
SCCP_СТРОЕНИЕ = {
    0x09: (("класс",), (0x03, 0x04, 0x0F), False),
    0x0A: (("возврат",), (0x03, 0x04, 0x0F), False),
    0x11: (("класс", "переходы"), (0x03, 0x04, 0x0F), True),
    0x12: (("возврат", "переходы"), (0x03, 0x04, 0x0F), True),
    0x01: (("slr", "класс"), (0x03,), True),
    0x02: (("dlr", "slr", "класс"), (), True),
    0x03: (("dlr", "отказ"), (), True),
    0x04: (("dlr", "slr", "освобождение"), (), True),
    0x05: (("dlr", "slr"), (), False),
    0x06: (("dlr", "сегм"), (0x0F,), False),
    0x0F: (("dlr", "ошибка"), (), False),
    0x10: (("dlr", "slr", "класс", "посл", "кредит"), (), False),
}
#: Пользователи SCCP по номеру подсистемы (254 — BSSAP): разборщик(р, м, конец) → bool.
SCCP_ПОДСИСТЕМЫ: dict[int, Callable[..., bool]] = {}
#: Пользователи данных соединений SCCP (CR, CC, DT1 несут данные без SSN) — по признакам.
SCCP_СОЕДИНЕНИЯ: list[Callable[..., bool]] = []
SCCP_ПАРАМЕТРЫ = {0x03: "Адрес вызываемого", 0x04: "Адрес вызывающего", 0x09: "Кредит",
                  0x0F: "Данные", 0x10: "Сегментация", 0x11: "Счётчик переходов", 0x12: "Важность"}
#: Причины возврата (Q.713 3.12); полнота сверена с pycrate (SCCP.py, _RetCause_dict).
SCCP_ВОЗВРАТ = {0: "нет трансляции для адреса такого вида", 1: "нет трансляции для этого адреса",
                2: "перегрузка подсистемы", 3: "отказ подсистемы", 4: "пользователь не оборудован",
                5: "отказ MTP", 6: "перегрузка сети", 7: "без уточнения", 8: "ошибка при переносе сообщения",
                9: "ошибка местной обработки", 10: "получатель не может собрать сегменты", 11: "отказ SCCP",
                12: "нарушение счётчика переходов", 13: "сегментация не поддерживается", 14: "сбой сегментации"}
#: Номера подсистем (Q.713 3.4.2.2; 3GPP TS 23.003 для 6–10, 142–150, 254).
SSN = {1: "управление SCCP", 3: "ISUP", 4: "OMAP", 5: "MAP", 6: "HLR", 7: "VLR", 8: "MSC", 9: "EIR",
       10: "AuC", 142: "RANAP", 146: "CAP", 147: "gsmSCF", 149: "SGSN", 150: "GGSN", 254: "BSSAP"}
ХАРАКТЕР_АДРЕСА = {1: "абонентский номер", 3: "национальный значащий номер", 4: "международный номер"}
ПЛАНЫ = {1: "ISDN/телефония (E.164)", 3: "данные (X.121)", 4: "телекс (F.69)", 6: "подвижная связь (E.212)",
         7: "ISDN/подвижная (E.214)"}


def _разбор_адреса_sccp(д: bytes, м: int, дл: int) -> dict | None:
    """Адрес SCCP (Q.713 3.4): индикатор, PC, SSN, глобальный заголовок. Не сошлось — None."""
    if дл < 2:
        return None
    ai = д[м]
    pci, ssni, gti, ri = ai & 1, (ai >> 1) & 1, (ai >> 2) & 15, (ai >> 6) & 1
    if gti > 4 or not (pci or ssni or gti):
        return None
    шапка = {0: 0, 1: 1, 2: 1, 3: 2, 4: 3}[gti]
    нужно = 1 + 2 * pci + ssni + шапка
    if нужно > дл or (gti == 0 and нужно != дл) or (gti and нужно == дл):
        return None                      # GT без цифр или лишнее после PC/SSN
    место = м + 1
    итог = {"ai": ai, "pci": pci, "ssni": ssni, "gti": gti, "ri": ri, "pc": None, "ssn": None}
    if pci:
        итог["pc"] = (место, (д[место] | (д[место + 1] << 8)) & 0x3FFF, д[место + 1] >> 6)
        место += 2
    if ssni:
        итог["ssn"] = (место, д[место])
        место += 1
    итог["gt"] = (место, шапка, м + дл)
    if gti in (3, 4):
        es = д[место + 1] & 15
        if es not in (0, 1, 2):         # 0 — неизвестно, 1 — BCD нечётное, 2 — BCD чётное
            return None
    return итог


def _адрес_sccp(у, р: Разбор, п, м: int, дл: int, ключ: str) -> tuple[int | None, str]:
    """Поля адреса под параметром; итог — SSN и строка для сводки."""
    д = р.д
    а = _разбор_адреса_sccp(д, м, дл)
    ai = а["ai"]
    инд = у.поле("Индикатор адреса", ключ + ".ai", f"0x{ai:02x}", м, 1, ai, родитель=п)
    у.поле("Маршрутизация", ключ + ".ri", "по SSN" if а["ri"] else "по GT", м, 1, а["ri"], родитель=инд)
    у.поле("GTI", ключ + ".gti", а["gti"], м, 1, родитель=инд)
    у.поле("SSN есть", ключ + ".ssni", а["ssni"], м, 1, родитель=инд)
    у.поле("PC есть", ключ + ".pci", а["pci"], м, 1, родитель=инд)
    части = []
    if а["pc"]:
        место, пк, _ = а["pc"]
        у.поле("Пункт сигнализации", ключ + ".pc", _пункт(пк), место, 2, пк, родитель=п)
        части.append(f"PC {пк}")
    ssn = None
    if а["ssn"]:
        место, ssn = а["ssn"]
        у.поле("Номер подсистемы", ключ + ".ssn", f"{ssn} ({SSN.get(ssn, 'неизвестная')})", место, 1, ssn,
               родитель=п)
        у.поле("", "sccp.ssn", ssn, место, 0)
        части.append(f"SSN {ssn}" + (f" ({SSN[ssn]})" if ssn in SSN else ""))
    место, шапка, конец = а["gt"]
    gti = а["gti"]
    if gti:
        гт = у.поле("Глобальный заголовок", ключ + ".gt", f"GTI {gti}", место, конец - место, родитель=п)
        нечётно = None
        if gti == 1:
            нечётно = bool(д[место] >> 7)
            у.поле("Нечётное число цифр", ключ + ".oe", д[место] >> 7, место, 1, родитель=гт)
            nai = д[место] & 0x7F
            у.поле("Характер адреса", ключ + ".nai", f"{nai} ({ХАРАКТЕР_АДРЕСА.get(nai, 'неизвестный')})",
                   место, 1, nai, родитель=гт)
        if gti in (2, 3, 4):
            у.поле("Тип трансляции", ключ + ".tt", д[место], место, 1, родитель=гт)
        if gti in (3, 4):
            np, es = д[место + 1] >> 4, д[место + 1] & 15
            у.поле("План нумерации", ключ + ".np", f"{np} ({ПЛАНЫ.get(np, 'неизвестный')})", место + 1, 1, np,
                   родитель=гт)
            у.поле("Схема кодирования", ключ + ".es",
                   {0: "неизвестна", 1: "BCD, нечётное", 2: "BCD, чётное"}[es], место + 1, 1, es, родитель=гт)
            нечётно = {1: True, 2: False}.get(es)
        if gti == 4:
            nai = д[место + 2] & 0x7F
            у.поле("Характер адреса", ключ + ".nai", f"{nai} ({ХАРАКТЕР_АДРЕСА.get(nai, 'неизвестный')})",
                   место + 2, 1, nai, родитель=гт)
        цифры = _цифры(д[место + шапка:конец], нечётно if нечётно is not None else (None if gti == 1 else False))
        у.поле("Цифры", ключ + ".digits", цифры, место + шапка, конец - место - шапка, родитель=гт)
        у.поле("", "sccp.digits", цифры, место, 0)
        гт.текст = f"GT {цифры}"
        части.append(f"GT {цифры}")
    п.текст = ", ".join(части)
    return ssn, п.текст


def _проверка_sccp(д: bytes, м: int, конец: int):
    """Строение SCCP по Q.713: указатели ведут внутрь, параметры переменной части
    лежат впритык и кончаются там же, где сообщение; адреса и класс — в пределах.
    Итог — (тип, [(имя поля, место, длина)], [(код, место длины, длина)], место указателей) или None."""
    # Проверка идёт по объявленному концу; чтение за записанными байтами — IndexError,
    # то есть обрыв (данные SCCP при этом не признаются, а помечаются оборванными).
    if конец - м < 2:
        return None
    тип = д[м]
    if тип not in SCCP_СТРОЕНИЕ:
        return None
    фикс, переменные, есть_необяз = SCCP_СТРОЕНИЕ[тип]
    место = м + 1
    поля = []
    for имя in фикс:
        дл = _ФИКС[имя][2]
        поля.append((имя, место, дл))
        место += дл
    указатели = место
    место += len(переменные) + (1 if есть_необяз else 0)
    if место > конец:
        return None
    for имя, где, _ in поля:
        if имя == "класс":
            класс, обработка = д[где] & 15, д[где] >> 4
            if тип in (0x09, 0x11) and (класс > 1 or обработка not in (0, 8)):
                return None
            if тип in (0x01, 0x02, 0x10) and (класс not in (2, 3) or обработка):
                return None
    куски = []                           # (начало, конец) занятых частей после указателей
    параметры = []
    for i, код in enumerate(переменные):
        ук = указатели + i
        цель = ук + д[ук]
        if д[ук] == 0 or цель >= конец:
            return None
        дл = д[цель]
        if цель + 1 + дл > конец or дл < 1:
            return None
        if код in (0x03, 0x04) and _разбор_адреса_sccp(д, цель + 1, дл) is None:
            return None
        куски.append((цель, цель + 1 + дл))
        параметры.append((код, цель, дл))
    необяз = None
    if есть_необяз:
        ук = указатели + len(переменные)
        if д[ук]:
            необяз = ук + д[ук]
            if необяз >= конец:
                return None
            где = необяз
            while True:
                код = д[где]
                if код == 0:
                    где += 1
                    break
                if где + 2 > конец or где + 2 + д[где + 1] > конец:
                    return None
                дл = д[где + 1]
                if код in (0x03, 0x04) and _разбор_адреса_sccp(д, где + 2, дл) is None:
                    return None
                параметры.append((код, где + 1, дл))
                где += 2 + дл
                if где >= конец:
                    return None          # нет «конца необязательных параметров»
            куски.append((необяз, где))
    # Части лежат впритык от конца указателей до конца сообщения.
    край = место
    for начало, конец_ in sorted(куски):
        if начало != край:
            return None
        край = конец_
    if край != конец:
        return None
    return тип, поля, параметры, указатели, переменные, есть_необяз


def sccp(р: Разбор, м: int, конец: int) -> bool:
    """SCCP (Q.713): тип (Табл. 1), фиксированная часть, указатели (2.3), переменная и
    необязательная части (4), адреса (3.4). UDT/XUDT и возвращённые UDTS/XUDTS → TCAP;
    сегменты (3.17) и прочие данные — байтами."""
    проверка = _проверка_sccp(р.д, м, конец)
    if проверка is None:
        return False
    тип, поля, параметры, указатели, переменные, есть_необяз = проверка
    д = р.д
    у = р.уровень("SCCP", "Signalling Connection Control Part (Q.713)", м)
    у.поле("Тип сообщения", "sccp.message_type", f"0x{тип:02x} ({SCCP_ТИПЫ[тип]})", м, 1, тип)
    итог = [SCCP_ТИПЫ[тип]]
    for имя, где, дл in поля:
        подпись, ключ, _ = _ФИКС[имя]
        if имя == "класс":
            класс, обработка = д[где] & 15, д[где] >> 4
            п = у.поле(подпись, ключ, класс, где, 1)
            у.поле("Обработка сообщения", "sccp.handling", обработка, где, 1, родитель=п)
            итог.append(f"класс {класс}")
        elif имя in ("slr", "dlr"):
            ссылка = д[где] | (д[где + 1] << 8) | (д[где + 2] << 16)
            у.поле(подпись, ключ, f"0x{ссылка:06x}", где, 3, ссылка)
            итог.append(f"{имя.upper()} 0x{ссылка:06x}")
        elif имя == "возврат":
            у.поле(подпись, ключ, f"{д[где]} ({SCCP_ВОЗВРАТ.get(д[где], 'неизвестная')})", где, 1, д[где])
            итог.append(SCCP_ВОЗВРАТ.get(д[где], f"причина {д[где]}"))
        elif имя == "посл":
            у.поле(подпись, ключ, f"0x{u16(д, где):04x}", где, 2, u16(д, где))
        else:
            у.поле(подпись, ключ, д[где], где, дл)
    for i in range(len(переменные)):
        у.поле(f"Указатель {i + 1}", f"sccp.variable_pointer{i + 1}", д[указатели + i], указатели + i, 1)
    if есть_необяз:
        ук = указатели + len(переменные)
        у.поле("Указатель на необязательную часть", "sccp.optional_pointer", д[ук], ук, 1)
    ssn = {0x03: None, 0x04: None}
    данные_ = None
    сегмент = ""
    for код, где, дл in параметры:
        имя = SCCP_ПАРАМЕТРЫ.get(код, f"Параметр 0x{код:02x}")
        п = у.поле(имя, "sccp.parameter", "", где, 1 + дл, код)
        у.поле("Длина", "sccp.parameter_length", дл, где, 1, родитель=п)
        if код in (0x03, 0x04):
            ключ = "sccp.called" if код == 0x03 else "sccp.calling"
            ssn[код], текст = _адрес_sccp(у, р, п, где + 1, дл, ключ)
            п.текст = текст
            итог.append(("→ " if код == 0x03 else "от ") + текст)
        elif код == 0x0F:
            п.текст = f"{дл} байт"
            данные_ = (где + 1, где + 1 + дл)
        elif код == 0x11 and дл == 1:
            у.поле("Значение", "sccp.hops", д[где + 1], где + 1, 1, родитель=п)
            п.текст = str(д[где + 1])
        elif код == 0x10 and дл == 4:                    # Q.713 3.17: F, C, осталось сегментов, ссылка
            первый, осталось = д[где + 1] >> 7, д[где + 1] & 0x0F
            у.поле("Первый сегмент (F)", "sccp.segmentation.first", первый, где + 1, 1, родитель=п)
            у.поле("Класс (C)", "sccp.segmentation.class", д[где + 1] >> 6 & 1, где + 1, 1, родитель=п)
            у.поле("Осталось сегментов", "sccp.segmentation.remaining", осталось, где + 1, 1, родитель=п)
            ссылка = int.from_bytes(д[где + 2:где + 5], "little")    # как SLR/DLR: непрозрачное число
            у.поле("Местная ссылка", "sccp.segmentation.slr", f"0x{ссылка:06x}", где + 2, 3, ссылка, родитель=п)
            if not (первый and осталось == 0):           # иначе сообщение целое
                сегмент = ("первый" if первый else "очередной") + f" сегмент, осталось {осталось}"
            п.текст = сегмент or "сообщение целое"
        else:
            у.поле("Значение", "sccp.parameter_value", д[где + 1:где + 1 + дл].hex(), где + 1, дл, родитель=п)
            п.текст = д[где + 1:где + 1 + дл].hex()
    у.длина = конец - м
    у.итог = ", ".join(итог[:4]) + (f", {сегмент}" if сегмент else "")
    р.п.инфо = "SCCP " + у.итог
    if данные_ is not None and сегмент:
        _данные(р, данные_[0], f"данные SCCP ({сегмент}; сборка по пакетам не выполняется)", данные_[1])
    elif данные_ is not None:
        подсистема = SCCP_ПОДСИСТЕМЫ.get(ssn[0x03]) or SCCP_ПОДСИСТЕМЫ.get(ssn[0x04])
        if подсистема is not None:
            _дальше(р, подсистема, данные_[0], данные_[1], "данные SCCP")
        elif тип in (0x09, 0x0A, 0x11, 0x12):             # UDT/XUDT и возвращённые UDTS/XUDTS
            _дальше(р, tcap, данные_[0], данные_[1], "данные SCCP", (ssn[0x03], ssn[0x04]))
        elif SCCP_СОЕДИНЕНИЯ:
            _дальше(р, _соединение, данные_[0], данные_[1], "данные SCCP")
        else:
            _данные(р, данные_[0], "данные SCCP", данные_[1])
    return True


#: Сообщения управления SCCP (Q.713, 5.1, таблица 23); сверено с pycrate (SCCP.py, _SCMGType_dict).
SCMG_ТИПЫ = {1: "SSA", 2: "SSP", 3: "SST", 4: "SOR", 5: "SOG", 6: "SSC"}
SCMG_ОПИСАНИЯ = {1: "подсистема доступна", 2: "подсистема запрещена", 3: "проверка состояния подсистемы",
                 4: "запрос вывода из обслуживания", 5: "разрешение вывода из обслуживания",
                 6: "перегрузка SCCP/подсистемы"}


def scmg(р: Разбор, м: int, конец: int) -> bool:
    """Управление SCCP (SSN 1; Q.713, 5): формат, затронутая подсистема, её пункт (14 бит, младший
    октет первым), указатель множественности (2 бита); у SSC ещё уровень перегрузки (4 бита)."""
    д = р.д
    длина = конец - м
    if длина not in (5, 6) or д[м] not in SCMG_ТИПЫ or (д[м] == 6) != (длина == 6):
        return False
    тип = д[м]
    ssn, пункт, smi = д[м + 1], (д[м + 2] | д[м + 3] << 8) & 0x3FFF, д[м + 4] & 3
    у = р.уровень("SCCPMG", "SCCP Management (Q.713, 5)", м)
    у.поле("Тип сообщения", "sccpmg.message_type", f"{SCMG_ТИПЫ[тип]} ({SCMG_ОПИСАНИЯ[тип]})", м, 1, тип)
    у.поле("Затронутая подсистема", "sccpmg.ssn", f"{ssn} ({SSN[ssn]})" if ssn in SSN else ssn, м + 1, 1, ssn)
    у.поле("Затронутый пункт", "sccpmg.pc", пункт, м + 2, 2)
    у.поле("Множественность (SMI)", "sccpmg.smi", smi, м + 4, 1)
    итог = f"{SCMG_ТИПЫ[тип]} SSN {ssn}" + (f" ({SSN[ssn]})" if ssn in SSN else "") + f", PC {пункт}"
    if тип == 6:
        уровень = д[м + 5] & 0x0F
        у.поле("Уровень перегрузки", "sccpmg.congestion", уровень, м + 5, 1)
        итог += f", перегрузка {уровень}"
    у.длина = конец - м
    у.итог = итог
    р.п.инфо = "SCMG " + итог
    return True


SCCP_ПОДСИСТЕМЫ[1] = scmg


def _соединение(р: Разбор, м: int, конец: int) -> bool:
    """Данные соединения SCCP (CR, CC, DT1 — без SSN): пользователи пробуются по очереди."""
    return any(разбор(р, м, конец) for разбор in SCCP_СОЕДИНЕНИЯ)


def sccp_канал(р: Разбор, м: int) -> None:
    """Канал LINKTYPE_SCCP: SCCP без MTP3 перед ним."""
    _дальше(р, sccp, м, _конец_кадра(р), "не SCCP")


def tcap_канал(р: Разбор, м: int, ssn: int | None = None) -> None:
    """Канал «TCAP»: данные пользователя SCCP, собранные из сегментов XUDT (Q.714 4.1.1.2).
    Канал «TCAP (SSN n)» — с номером подсистемы получателя (по нему узнаётся MAP без контекста)."""
    _дальше(р, tcap, м, _конец_кадра(р), "не TCAP", (ssn, None))


# -- TCAP (Q.773) и MAP (TS 29.002) ------------------------------------------------------------------

TCAP_СООБЩЕНИЯ = {0x61: "Unidirectional", 0x62: "Begin", 0x64: "End", 0x65: "Continue", 0x67: "Abort"}
TCAP_КОМПОНЕНТЫ = {0xA1: "Invoke", 0xA2: "ReturnResultLast", 0xA3: "ReturnError", 0xA4: "Reject",
                   0xA7: "ReturnResultNotLast"}
#: Порядок элементов сообщения (Q.773 4.2.1): (тег, обязателен).
TCAP_СТРОЕНИЕ = {0x61: ((0x6B, False), (0x6C, True)),
                 0x62: ((0x48, True), (0x6B, False), (0x6C, False)),
                 0x64: ((0x49, True), (0x6B, False), (0x6C, False)),
                 0x65: ((0x48, True), (0x49, True), (0x6B, False), (0x6C, False)),
                 0x67: ((0x49, True), (0x4A, False), (0x6B, False))}
#: Коды операций MAP (TS 29.002 17.5) — все, по таблице Wireshark (packet-gsm_map.c,
#: gsm_old_GSMMAPOperationLocalvalue_vals), перенесённой программно.
MAP_ОПЕРАЦИИ = {2: "updateLocation", 3: "cancelLocation", 4: "provideRoamingNumber",
                5: "noteSubscriberDataModified", 6: "resumeCallHandling", 7: "insertSubscriberData",
                8: "deleteSubscriberData", 9: "sendParameters", 10: "registerSS", 11: "eraseSS",
                12: "activateSS", 13: "deactivateSS", 14: "interrogateSS", 15: "authenticationFailureReport",
                16: "notifySS", 17: "registerPassword", 18: "getPassword", 19: "processUnstructuredSS-Data",
                20: "releaseResources", 21: "mt-ForwardSM-VGCS", 22: "sendRoutingInfo",
                23: "updateGprsLocation", 24: "sendRoutingInfoForGprs", 25: "failureReport",
                26: "noteMsPresentForGprs", 27: "unAllocated", 28: "performHandover", 29: "sendEndSignal",
                30: "performSubsequentHandover", 31: "provideSIWFSNumber", 32: "sIWFSSignallingModify",
                33: "processAccessSignalling", 34: "forwardAccessSignalling", 35: "noteInternalHandover",
                36: "cancelVcsgLocation", 37: "reset", 38: "forwardCheckSS", 39: "prepareGroupCall",
                40: "sendGroupCallEndSignal", 41: "processGroupCallSignalling",
                42: "forwardGroupCallSignalling", 43: "checkIMEI", 44: "mt-forwardSM",
                45: "sendRoutingInfoForSM", 46: "mo-forwardSM", 47: "reportSM-DeliveryStatus",
                48: "noteSubscriberPresent", 49: "alertServiceCentreWithoutResult", 50: "activateTraceMode",
                51: "deactivateTraceMode", 52: "traceSubscriberActivity", 53: "updateVcsgLocation",
                54: "beginSubscriberActivity", 55: "sendIdentification", 56: "sendAuthenticationInfo",
                57: "restoreData", 58: "sendIMSI", 59: "processUnstructuredSS-Request",
                60: "unstructuredSS-Request", 61: "unstructuredSS-Notify",
                62: "anyTimeSubscriptionInterrogation", 63: "informServiceCentre", 64: "alertServiceCentre",
                65: "anyTimeModification", 66: "readyForSM", 67: "purgeMS", 68: "prepareHandover",
                69: "prepareSubsequentHandover", 70: "provideSubscriberInfo", 71: "anyTimeInterrogation",
                72: "ss-InvocationNotification", 73: "setReportingState", 74: "statusReport",
                75: "remoteUserFree", 76: "registerCC-Entry", 77: "eraseCC-Entry",
                78: "secureTransportClass1", 79: "secureTransportClass2", 80: "secureTransportClass3",
                81: "secureTransportClass4", 82: "unAllocated", 83: "provideSubscriberLocation",
                84: "sendGroupCallInfo", 85: "sendRoutingInfoForLCS", 86: "subscriberLocationReport",
                87: "ist-Alert", 88: "ist-Command", 89: "noteMM-Event", 90: "unAllocated", 91: "unAllocated",
                92: "unAllocated", 93: "unAllocated", 94: "unAllocated", 95: "unAllocated",
                96: "unAllocated", 97: "unAllocated", 98: "unAllocated", 99: "unAllocated",
                100: "unAllocated", 101: "unAllocated", 102: "unAllocated", 103: "unAllocated",
                104: "unAllocated", 105: "unAllocated", 106: "unAllocated", 107: "unAllocated",
                108: "unAllocated", 109: "lcs-PeriodicLocationCancellation", 110: "lcs-LocationUpdate",
                111: "lcs-PeriodicLocationRequest", 112: "lcs-AreaEventCancellation",
                113: "lcs-AreaEventReport", 114: "lcs-AreaEventRequest", 115: "lcs-MOLR",
                116: "lcs-LocationNotification", 117: "callDeflection", 118: "userUserService",
                119: "accessRegisterCCEntry", 120: "forwardCUG-Info", 121: "splitMPTY", 122: "retrieveMPTY",
                123: "holdMPTY", 124: "buildMPTY", 125: "forwardChargeAdvice", 126: "explicitCT"}
#: Номера подсистем MAP (TS 23.003): MAP, HLR, VLR, MSC, EIR, AuC.
MAP_SSN = frozenset({5, 6, 7, 8, 9, 10})
#: Контекст приложения MAP: {itu-t(0) identified-organization(4) etsi(0) mobileDomain(0)
#: gsm-Network(1) ac-Id(0) …} — первые пять октетов OID.
MAP_AC = bytes([0x04, 0x00, 0x00, 0x01, 0x00])


#: Предел вложенности элементов неопределённой длины (защита от зацикливания на мусоре).
_BER_ГЛУБИНА = 16


def _tlv(д: bytes, м: int, конец: int, глубина: int = 0) -> tuple[int, int, int, int] | None:
    """Элемент BER (X.690 8.1): тег — один октет или многооктетный (8.1.2.4: младшие 5 бит 11111,
    дальше номер по 7 бит, старший бит — «ещё октет»; такой тег — число из всех его октетов),
    длина определённая (до 3 октетов) или неопределённая (8.1.3.6: только у составного; содержимое —
    элементы до октетов 00 00). Итог — (тег, начало значения, конец значения, конец элемента) или
    None, если не укладывается."""
    н = м + 1
    if д[м] & 0x1F == 0x1F:
        while н < конец and д[н] & 0x80:
            н += 1
        н += 1
    if н >= конец:
        return None
    тег, дл = int.from_bytes(д[м:н], "big"), д[н]
    н += 1
    if дл == 0x80:
        if not д[м] & 0x20 or глубина >= _BER_ГЛУБИНА:
            return None
        к = н
        while к + 2 <= конец and (д[к] or д[к + 1]):
            э = _tlv(д, к, конец, глубина + 1)
            if э is None:
                return None
            к = э[3]
        if к + 2 > конец:
            return None
        return тег, н, к, к + 2
    if дл & 0x80:
        n = дл & 0x7F
        if n > 3:
            return None
        дл = int.from_bytes(д[н:н + n], "big")
        н += n
    if н + дл > конец:
        return None
    return тег, н, н + дл, н + дл


def _элементы(д: bytes, м: int, конец: int) -> list[tuple[int, int, int, int]] | None:
    """Последовательность элементов BER, ровно заполняющая [м, конец): [(тег, м, н, к)]."""
    итог = []
    while м < конец:
        э = _tlv(д, м, конец)
        if э is None:
            return None
        итог.append((э[0], м, э[1], э[2]))
        м = э[3]
    return итог


def _целое(д: bytes, н: int, к: int) -> int:
    return int.from_bytes(д[н:к], "big", signed=True)


def _компонент(д: bytes, тег: int, н: int, к: int) -> dict | None:
    """Компонент (Q.773 4.2.2): invokeID, linkedID, код операции/ошибки, параметр."""
    э = _элементы(д, н, к)
    if not э:
        return None
    итог = {"тег": тег, "invoke": None, "linked": None, "код": None, "параметр": None, "проблема": None}
    т, м, вн, вк = э[0]
    if т == 0x02 and вк - вн == 1:        # InvokeIdType ::= INTEGER (-128..127)
        итог["invoke"] = (м, вн, вк)
    elif not (тег == 0xA4 and т == 0x05 and вк == вн):
        return None
    хвост = э[1:]
    if тег == 0xA1:
        if хвост and хвост[0][0] == 0x80:
            if хвост[0][3] - хвост[0][2] != 1:
                return None
            итог["linked"] = хвост[0][1:]
            хвост = хвост[1:]
        if not хвост or хвост[0][0] not in (0x02, 0x06) or хвост[0][3] == хвост[0][2]:
            return None
        итог["код"] = хвост[0]
        хвост = хвост[1:]
        if len(хвост) > 1:
            return None
        итог["параметр"] = хвост[0] if хвост else None
    elif тег in (0xA2, 0xA7):
        if len(хвост) > 1 or (хвост and хвост[0][0] != 0x30):
            return None
        if хвост:
            вн_ = _элементы(д, хвост[0][2], хвост[0][3])
            if not вн_ or вн_[0][0] not in (0x02, 0x06) or len(вн_) > 2:
                return None
            итог["код"] = вн_[0]
            итог["параметр"] = вн_[1] if len(вн_) > 1 else None
    elif тег == 0xA3:
        if not хвост or хвост[0][0] not in (0x02, 0x06) or хвост[0][3] == хвост[0][2] or len(хвост) > 2:
            return None
        итог["код"] = хвост[0]
        итог["параметр"] = хвост[1] if len(хвост) > 1 else None
    elif тег == 0xA4:
        if len(хвост) != 1 or хвост[0][0] not in (0x80, 0x81, 0x82, 0x83) or хвост[0][3] - хвост[0][2] != 1:
            return None
        итог["проблема"] = хвост[0]
    return итог


def _проверка_tcap(д: bytes, м: int, конец: int):
    if конец > len(д):
        raise Мало()
    внешний = _tlv(д, м, конец)
    if внешний is None or внешний[0] not in TCAP_СТРОЕНИЕ or внешний[3] != конец:
        return None
    тег, н, к, _ = внешний
    э = _элементы(д, н, к)
    if э is None:
        return None
    порядок = TCAP_СТРОЕНИЕ[тег]
    i = 0
    части = {}
    for т, обязателен in порядок:
        if i < len(э) and э[i][0] == т:
            части[т] = э[i]
            i += 1
        elif обязателен:
            return None
    if i != len(э):
        return None
    for т in (0x48, 0x49):
        if т in части and not 1 <= части[т][3] - части[т][2] <= 4:
            return None
    if 0x4A in части and части[0x4A][3] - части[0x4A][2] != 1:
        return None
    if 0x6B in части:
        диалог = _элементы(д, части[0x6B][2], части[0x6B][3])
        if not диалог or len(диалог) != 1 or диалог[0][0] != 0x28:
            return None
    компоненты = []
    if 0x6C in части:
        ком = _элементы(д, части[0x6C][2], части[0x6C][3])
        if not ком:
            return None
        for т, км, кн, кк in ком:
            if т not in TCAP_КОМПОНЕНТЫ:
                return None
            разбор = _компонент(д, т, кн, кк)
            if разбор is None:
                return None
            разбор["место"] = (км, кк)
            компоненты.append(разбор)
    return тег, части, компоненты


def _oid(байты: bytes) -> str:
    if not байты:
        return ""
    части = [str(min(байты[0] // 40, 2)), str(байты[0] - 40 * min(байты[0] // 40, 2))]
    x = 0
    for б in байты[1:]:
        x = (x << 7) | (б & 0x7F)
        if not б & 0x80:
            части.append(str(x))
            x = 0
    return ".".join(части)


def _контекст(д: bytes, н: int, к: int) -> tuple[int, int] | None:
    """OID контекста приложения из диалога: первый [1] → OBJECT IDENTIFIER (Q.773 4.2.3)."""
    м = н
    while м < к:
        э = _tlv(д, м, к)
        if э is None:
            return None
        тег, вн, вк, ве = э
        if тег == 0xA1:
            внутри = _tlv(д, вн, вк)
            if внутри and внутри[0] == 0x06:
                return внутри[1], внутри[2]
        if тег & 0x20:                   # составной — внутрь
            найдено = _контекст(д, вн, вк)
            if найдено:
                return найдено
        м = ве
    return None


#: Приложения поверх TCAP, узнаваемые раньше MAP (CAP делит с MAP корень контекста):
#: функция(р, компоненты, контекст, (SSN вызываемого, SSN вызывающего)) → bool.
TCAP_ПРИЛОЖЕНИЯ: list[Callable] = []


# -- ANSI TCAP (T1.114; ASN.1 TCAPPackage из pycrate) ---------------------------------------------
# Теги — класс PRIVATE: составной 0xE0 | n, простой 0xC0 | n.

TCAP_ANSI_ПАКЕТЫ = {0xE1: "Unidirectional", 0xE2: "Query With Permission", 0xE3: "Query Without Permission",
                    0xE4: "Response", 0xE5: "Conversation With Permission",
                    0xE6: "Conversation Without Permission", 0xF6: "Abort"}
_ANSI_ПЕРВЫЙ = {bytes([т]) for т in TCAP_ANSI_ПАКЕТЫ}
#: Длина TransactionID [PRIVATE 7]: 0 — Unidirectional, 4 — Query, Response, Abort; 8 — Conversation.
_ANSI_TID = {0xE1: 0, 0xE2: 4, 0xE3: 4, 0xE4: 4, 0xE5: 8, 0xE6: 8, 0xF6: 4}
TCAP_ANSI_КОМПОНЕНТЫ = {0xE9: "Invoke Last", 0xEA: "Return Result Last", 0xEB: "Return Error", 0xEC: "Reject",
                        0xED: "Invoke Not Last", 0xEE: "Return Result Not Last"}
#: componentIDs [PRIVATE 15]: наибольшая длина (у Invoke — invoke и correlation ID).
_ANSI_ИД = {0xE9: 2, 0xED: 2, 0xEA: 1, 0xEE: 1, 0xEB: 1, 0xEC: 1}
#: Коды операции [PRIVATE 16/17] IMPLICIT INTEGER; коды ошибки [PRIVATE 19/20] — явные (EXPLICIT).
_ANSI_ОПЕРАЦИИ = {0xD0: "national", 0xD1: "private"}
_ANSI_ОШИБКИ = {0xF3: "national", 0xF4: "private"}


def _ansi_компонент(д: bytes, тег: int, н: int, к: int) -> dict | None:
    """Компонент ANSI TCAP: componentIDs, код операции/ошибки, проблема Reject, параметр."""
    э = _элементы(д, н, к)
    if э is None:
        return None
    итог = {"тег": тег, "ид": None, "код": None, "проблема": None, "параметр": None}
    if э and э[0][0] == 0xCF:
        if э[0][3] - э[0][2] > _ANSI_ИД[тег]:
            return None
        итог["ид"] = э.pop(0)
    elif тег not in (0xE9, 0xED):                    # необязателен componentIDs только у Invoke
        return None
    if тег in (0xE9, 0xED):
        if not э or э[0][0] not in _ANSI_ОПЕРАЦИИ:
            return None
        итог["код"] = э.pop(0)
    elif тег == 0xEB:
        if not э or э[0][0] not in _ANSI_ОШИБКИ and э[0][0] != 0x02:
            return None
        т, _, вн, вк = э[0]
        if т in _ANSI_ОШИБКИ and [в[0] for в in _элементы(д, вн, вк) or ()] != [0x02]:
            return None                                  # явный тег: внутри ровно один INTEGER
        итог["код"] = э.pop(0)
    elif тег == 0xEC:
        # Reject: проблема [PRIVATE 21] и параметр — пустая SEQUENCE [PRIVATE 16] или SET [PRIVATE 18].
        if [в[0] for в in э] not in ([0xD5, 0xF0], [0xD5, 0xF2]):
            return None
        итог["проблема"] = э.pop(0)
    if len(э) > 1:
        return None
    if э:
        итог["параметр"] = э[0]
    return итог


def _проверка_ansi(д: bytes, м: int, конец: int):
    """Строение пакета ANSI TCAP: TID нужной длины, диалог, компоненты либо причина Abort."""
    внешний = _tlv(д, м, конец)
    if внешний is None or внешний[3] != конец:
        return None
    тег, н, к, _ = внешний
    э = _элементы(д, н, к)
    if not э or э[0][0] != 0xC7 or э[0][3] - э[0][2] != _ANSI_TID[тег]:
        return None
    части = {0xC7: э[0]}
    допустимые = (0xF9, 0xD7, 0xF8) if тег == 0xF6 else (0xF9, 0xE8)
    for элемент in э[1:]:
        if элемент[0] not in допустимые:                 # порядок: каждый следующий — правее прежнего
            return None
        допустимые = допустимые[допустимые.index(элемент[0]) + 1:]
        части[элемент[0]] = элемент
    if 0xD7 in части and 0xF8 in части:
        return None
    if тег == 0xE1 and 0xE8 not in части:
        return None
    компоненты = []
    if 0xE8 in части:
        _, _, сн, ск = части[0xE8]
        список = _элементы(д, сн, ск)
        if список is None:
            return None
        for т, км, кн, кк in список:
            if т not in TCAP_ANSI_КОМПОНЕНТЫ:
                return None
            к_ = _ansi_компонент(д, т, кн, кк)
            if к_ is None:
                return None
            к_["место"] = (км, кк)
            компоненты.append(к_)
    return тег, части, компоненты


def tcap_ansi(р: Разбор, м: int, конец: int) -> bool:
    """ANSI TCAP (T1.114): пакет, TransactionID, диалог, компоненты (коды операций national/
    private, ошибки, Reject), причина Abort."""
    д = р.д
    if конец > len(д):
        raise Мало()
    проверка = _проверка_ansi(д, м, конец)
    if проверка is None:
        return False
    тег, части, компоненты = проверка
    у = р.уровень("ANSI TCAP", "ANSI Transaction Capabilities Application Part (T1.114)", м)
    у.поле("Тип пакета", "ansi_tcap.package_type", TCAP_ANSI_ПАКЕТЫ[тег], м, 1, тег)
    у.длина = конец - м
    итог = [TCAP_ANSI_ПАКЕТЫ[тег]]
    _, эм, н, к = части[0xC7]
    if к > н:
        tid = д[н:к].hex()
        у.поле("Идентификатор транзакции", "ansi_tcap.identifier", tid, эм, к - эм)
        итог.append(f"TID {tid}")
    if 0xF9 in части:
        _, эм, н, к = части[0xF9]
        у.поле("Диалог", "ansi_tcap.dialoguePortion", f"{к - н} байт", эм, к - эм)
    if 0xD7 in части:
        _, эм, н, к = части[0xD7]
        причина = _целое(д, н, к)
        у.поле("Причина P-Abort", "ansi_tcap.abortCause", причина, эм, к - эм)
        итог.append(f"причина {причина}")
    if 0xF8 in части:
        _, эм, н, к = части[0xF8]
        у.поле("Причина пользователя", "ansi_tcap.userInformation", f"{к - н} байт", эм, к - эм)
    виды = []
    for к_ in компоненты:
        км, кк = к_["место"]
        вид = TCAP_ANSI_КОМПОНЕНТЫ[к_["тег"]]
        п = у.поле(вид, "ansi_tcap.component", вид, км, кк - км, к_["тег"])
        описание = вид
        if к_["ид"]:
            _, эм, н, к = к_["ид"]
            у.поле("Идентификаторы компонента", "ansi_tcap.componentIDs", д[н:к].hex(), эм, к - эм, родитель=п)
            if к > н:
                описание += f" id {д[н]}"
        if к_["код"]:
            т, эм, н, к = к_["код"]
            if т in _ANSI_ОШИБКИ:
                н = _tlv(д, н, к)[1]
            код = _целое(д, н, к)
            вид_кода = _ANSI_ОПЕРАЦИИ.get(т) or _ANSI_ОШИБКИ.get(т, "local")
            if т in _ANSI_ОПЕРАЦИИ:
                у.поле(f"Код операции ({вид_кода})", f"ansi_tcap.{вид_кода}", код, эм, к - эм, родитель=п)
                описание += f" оп. {вид_кода} {код}"
            else:
                у.поле(f"Код ошибки ({вид_кода})", "ansi_tcap.errorCode", код, эм, к - эм, родитель=п)
                описание += f" ошибка {код}"
        if к_["проблема"]:
            _, эм, н, к = к_["проблема"]
            у.поле("Проблема", "ansi_tcap.rejectProblem", _целое(д, н, к), эм, к - эм, родитель=п)
            описание += f" проблема {_целое(д, н, к)}"
        if к_["параметр"]:
            т, эм, н, к = к_["параметр"]
            у.поле("Параметр", "ansi_tcap.parameter", f"тег 0x{т:02x}, {к - н} байт", эм, к - эм, т, родитель=п)
        п.текст = описание
        виды.append(описание)
    if виды:
        итог.append("; ".join(виды[:3]))
    у.итог = ", ".join(итог)
    р.п.инфо = "ANSI TCAP " + у.итог
    return True


def tcap(р: Разбор, м: int, конец: int, ssn: Sequence[int | None] = (None, None)) -> bool:
    """TCAP (Q.773 4.2): сообщение (Begin/Continue/End/Abort/Unidirectional), OTID/DTID,
    диалог (контекст приложения), компоненты с invokeID и кодом операции.

    Проверяется всё строение BER: длины вложены без остатка, порядок и
    обязательность элементов, размеры идентификаторов транзакций (1–4 октета).
    """
    д = р.д
    if д[м:м + 1] in _ANSI_ПЕРВЫЙ:
        return tcap_ansi(р, м, конец)
    проверка = _проверка_tcap(д, м, конец)
    хвост = 0
    if проверка is None:
        # Нули после целого сообщения (меньше слова выравнивания) — встречаются у SUA, где
        # в длину Data попало выравнивание. Сообщение берётся по своей длине BER.
        внешний = _tlv(д, м, конец)
        if внешний is None or конец - внешний[3] >= 4 or any(д[внешний[3]:конец]):
            return False
        проверка = _проверка_tcap(д, м, внешний[3])
        if проверка is None:
            return False
        хвост, конец = конец - внешний[3], внешний[3]
    тег, части, компоненты = проверка
    у = р.уровень("TCAP", "Transaction Capabilities Application Part (Q.773)", м)
    у.поле("Тип сообщения", "tcap.message_type", TCAP_СООБЩЕНИЯ[тег], м, 1, тег)
    у.длина = конец - м
    if хвост:
        р.ошибка(f"TCAP: после сообщения {хвост} нулевых байт (выравнивание внутри длины данных)")
    итог = [TCAP_СООБЩЕНИЯ[тег]]
    for т, ключ, имя in ((0x48, "tcap.otid", "OTID"), (0x49, "tcap.dtid", "DTID")):
        if т in части:
            _, эм, н, к = части[т]
            tid = д[н:к].hex()
            у.поле(имя, ключ, tid, эм, к - эм)
            у.поле("", "tcap.tid", tid, эм, 0)
            итог.append(f"{имя} {tid}")
    if 0x4A in части:
        _, эм, н, к = части[0x4A]
        у.поле("Причина P-Abort", "tcap.p_abortCause", д[н], эм, к - эм)
    контекст = None
    if 0x6B in части:
        _, эм, н, к = части[0x6B]
        п = у.поле("Диалог", "tcap.dialogue", f"{к - эм} байт", эм, к - эм)
        найдено = _контекст(д, н, к)
        if найдено:
            контекст = д[найдено[0]:найдено[1]]
            у.поле("Контекст приложения", "tcap.application_context", _oid(контекст), найдено[0],
                   найдено[1] - найдено[0], родитель=п)
            п.текст = f"контекст {_oid(контекст)}"
    виды = []
    for к_ in компоненты:
        км, кк = к_["место"]
        вид = TCAP_КОМПОНЕНТЫ[к_["тег"]]
        п = у.поле(вид, "tcap.component", вид, км, кк - км, к_["тег"])
        описание = вид
        if к_["invoke"]:
            эм, н, к = к_["invoke"]
            у.поле("invokeID", "tcap.invokeID", _целое(д, н, к), эм, к - эм, родитель=п)
            описание += f" id {_целое(д, н, к)}"
        if к_["linked"]:
            эм, н, к = к_["linked"]
            у.поле("linkedID", "tcap.linkedID", _целое(д, н, к), эм, к - эм, родитель=п)
        if к_["код"]:
            т, эм, н, к = к_["код"]
            if т == 0x02:
                код = _целое(д, н, к)
                ключ = "tcap.errorCode" if к_["тег"] == 0xA3 else "tcap.opCode"
                у.поле("Код ошибки" if к_["тег"] == 0xA3 else "Код операции", ключ, код, эм, к - эм, родитель=п)
                описание += f" {'ошибка' if к_['тег'] == 0xA3 else 'оп.'} {код}"
            else:
                у.поле("Глобальный код", "tcap.globalValue", _oid(д[н:к]), эм, к - эм, родитель=п)
        if к_["параметр"]:
            т, эм, н, к = к_["параметр"]
            у.поле("Параметр", "tcap.parameter", f"тег 0x{т:02x}, {к - н} байт", эм, к - эм, т, родитель=п)
        if к_["проблема"]:
            т, эм, н, к = к_["проблема"]
            у.поле("Проблема", "tcap.reject_problem", f"[{т & 0x1F}] {д[н]}", эм, к - эм, д[н], родитель=п)
        п.текст = описание
        виды.append(описание)
    if виды:
        итог.append("; ".join(виды[:3]))
    у.итог = ", ".join(итог)
    р.п.инфо = "TCAP " + у.итог
    for приложение in TCAP_ПРИЛОЖЕНИЯ:
        if компоненты and приложение(р, компоненты, контекст, ssn):
            return True
    это_map = (контекст is not None and контекст.startswith(MAP_AC)) or \
        (контекст is None and any(s in MAP_SSN for s in ssn if s is not None))
    if это_map and компоненты:
        _map(р, компоненты, контекст)
    return True


def _map(р: Разбор, компоненты, контекст: bytes | None) -> None:
    """MAP (TS 29.002): операции компонентов; для нескольких — IMSI/MSISDN из аргумента."""
    д = р.д
    начало = компоненты[0]["место"][0]
    у = р.уровень("MAP", "GSM Mobile Application Part (3GPP TS 29.002)", начало)
    у.поле("", "gsm_map", True, начало, 0)
    у.длина = компоненты[-1]["место"][1] - начало
    if контекст is not None and контекст.startswith(MAP_AC) and len(контекст) >= 7:
        у.поле("Контекст приложения (ac-Id, версия)", "gsm_map.application_context",
               f"{контекст[5]}, версия {контекст[-1]}", начало, 0)
    итог = []
    for к_ in компоненты:
        км, кк = к_["место"]
        if not к_["код"] or к_["код"][0] != 0x02:
            continue
        т, эм, н, к = к_["код"]
        код = _целое(д, н, к)
        if к_["тег"] == 0xA3:
            у.поле("Ошибка MAP", "gsm_map.error_code", код, эм, к - эм)
            итог.append(f"ошибка {код}")
            continue
        имя = MAP_ОПЕРАЦИИ.get(код, str(код))
        п = у.поле("Операция", "gsm_map.opcode", f"{код} ({имя})" if код in MAP_ОПЕРАЦИИ else str(код), эм, к - эм,
                   код)
        запись = имя if к_["тег"] == 0xA1 else f"{имя} (результат)"
        if к_["тег"] == 0xA1 and к_["параметр"]:
            номер = _map_номер(д, код, к_["параметр"])
            if номер:
                ключ, подпись, значение, нм, нк = номер
                у.поле(подпись, ключ, значение, нм, нк - нм, родитель=п)
                запись += f" {подпись} {значение}"
        итог.append(запись)
    у.итог = "; ".join(итог) or "компоненты без кода операции"
    р.п.инфо = "GSM MAP " + у.итог


def _map_номер(д: bytes, код: int, параметр) -> tuple[str, str, str, int, int] | None:
    """IMSI/MSISDN из аргумента (TS 29.002 17.7): UpdateLocationArg { imsi IMSI, … },
    SendAuthenticationInfoArg { imsi [0] IMSI, … } (v3) или IMSI (v2),
    SendRoutingInfoArg { msisdn [0] ISDN-AddressString, … }."""
    т, эм, н, к = параметр
    if код == 56 and т == 0x04 and 3 <= к - н <= 8:
        return "gsm_map.imsi", "IMSI", _цифры(д[н:к]), н, к
    if т != 0x30:
        return None
    первый = _tlv(д, н, к)
    if первый is None:
        return None
    тег, вн, вк, _ = первый
    if код == 2 and тег == 0x04 and 3 <= вк - вн <= 8:
        return "gsm_map.imsi", "IMSI", _цифры(д[вн:вк]), вн, вк
    if код == 56 and тег == 0x80 and 3 <= вк - вн <= 8:
        return "gsm_map.imsi", "IMSI", _цифры(д[вн:вк]), вн, вк
    if код == 22 and тег == 0x80 and 2 <= вк - вн <= 20:
        # ISDN-AddressString: октет признаков (расширение, характер, план), затем TBCD.
        return "gsm_map.msisdn", "MSISDN", _цифры(д[вн + 1:вк]), вн, вк
    return None


# -- ISUP (Q.763) -------------------------------------------------------------------------------------

ISUP_ТИПЫ = {0x01: "IAM", 0x02: "SAM", 0x05: "COT", 0x06: "ACM", 0x07: "CON", 0x09: "ANM", 0x0C: "REL",
             0x0D: "SUS", 0x0E: "RES", 0x10: "RLC", 0x11: "CCR", 0x12: "RSC", 0x13: "BLO", 0x14: "UBL",
             0x15: "BLA", 0x16: "UBA", 0x17: "GRS", 0x18: "CGB", 0x19: "CGU", 0x1A: "CGBA", 0x1B: "CGUA",
             0x29: "GRA", 0x2C: "CPG", 0x2E: "UCIC"}
#: Строение сообщений (Q.763 табл. 21–50): фиксированные [(подпись, ключ, длина)],
#: обязательные переменные [(код параметра, наименьшая длина)], есть ли необязательная часть.
_ОБР = [("Обратные признаки вызова", "isup.backw_call_ind", 2)]
_ДИАПАЗОН = [(0x16, 1)]
_ГРУППА = [("Тип сообщения контроля группы", "isup.cgs_message_type", 1)]
ISUP_СТРОЕНИЕ = {
    0x01: ([("Характер соединения", "isup.nature_of_conn_ind", 1),
            ("Прямые признаки вызова", "isup.forw_call_ind", 2),
            ("Категория вызывающего", "isup.calling_partys_category", 1),
            ("Требование к среде передачи", "isup.transmission_medium_requirement", 1)], [(0x04, 2)], True),
    0x02: ([], [(0x05, 1)], True),
    0x05: ([("Признаки непрерывности", "isup.continuity_indicators", 1)], [], False),
    0x06: (_ОБР, [], True), 0x07: (_ОБР, [], True), 0x09: ([], [], True),
    0x0C: ([], [(0x12, 2)], True),
    0x0D: ([("Признаки приостановки/возобновления", "isup.suspend_resume_indicators", 1)], [], True),
    0x0E: ([("Признаки приостановки/возобновления", "isup.suspend_resume_indicators", 1)], [], True),
    0x10: ([], [], True),
    0x11: ([], [], False), 0x12: ([], [], False), 0x13: ([], [], False), 0x14: ([], [], False),
    0x15: ([], [], False), 0x16: ([], [], False), 0x2E: ([], [], False),
    0x17: ([], _ДИАПАЗОН, False), 0x29: ([], _ДИАПАЗОН, False),
    0x18: (_ГРУППА, _ДИАПАЗОН, False), 0x19: (_ГРУППА, _ДИАПАЗОН, False),
    0x1A: (_ГРУППА, _ДИАПАЗОН, False), 0x1B: (_ГРУППА, _ДИАПАЗОН, False),
    0x2C: ([("Информация о событии", "isup.event_ind", 1)], [], True),
}
ISUP_ПАРАМЕТРЫ = {0x03: "Access transport", 0x04: "Номер вызываемого", 0x05: "Последующий номер",
                  0x08: "Необязательные прямые признаки", 0x0A: "Номер вызывающего",
                  0x0B: "Номер переадресующего", 0x0C: "Номер переадресации",
                  0x12: "Причина", 0x13: "Сведения о переадресации", 0x16: "Диапазон и состояние",
                  0x1D: "Сведения о службе пользователя", 0x20: "Сведения пользователь-пользователь",
                  0x21: "Подключённый номер", 0x28: "Исходный вызываемый номер",
                  0x29: "Необязательные обратные признаки", 0x39: "Совместимость параметров",
                  0x3F: "Номер местоположения", 0xC0: "Обобщённый номер"}
КАТЕГОРИИ = {0x00: "неизвестна", 0x0A: "обычный абонент", 0x0B: "абонент с приоритетом",
             0x0C: "передача данных", 0x0D: "испытательный вызов", 0x0F: "таксофон"}
ХАРАКТЕР_НОМЕРА = {1: "абонентский номер", 3: "национальный номер", 4: "международный номер"}
ПЛАНЫ_ISUP = {1: "ISDN (E.164)", 3: "данные (X.121)", 4: "телекс (F.69)"}
СОБЫТИЯ = {1: "ALERTING", 2: "PROGRESS", 3: "есть внутриполосная информация",
           4: "переадресация при занятости", 5: "переадресация при неответе",
           6: "безусловная переадресация"}
#: Причины Q.850 — только общеизвестные.
ПРИЧИНЫ = {1: "номер не существует", 16: "нормальное освобождение", 17: "абонент занят",
           18: "пользователь не отвечает", 19: "нет ответа", 21: "вызов отклонён",
           27: "получатель не работает", 28: "неверный формат номера", 31: "нормально, без уточнения",
           34: "нет свободного канала", 41: "временный отказ", 127: "межсетевое взаимодействие"}


def _проверка_isup(д: bytes, м: int, конец: int, cic: bool = True):
    """Строение ISUP (Q.763 1.3–1.8): указатели ведут внутрь, параметры впритык,
    необязательная часть кончается октетом 0 ровно в конце сообщения. Без CIC — сообщение
    с октета типа (тело SIP-I/SIP-T, Q.1912.5 и RFC 3204)."""
    заголовок = 3 if cic else 1
    if конец - м < заголовок:
        return None
    тип = д[м + заголовок - 1]
    if тип not in ISUP_СТРОЕНИЕ:
        return None
    фикс, переменные, есть_необяз = ISUP_СТРОЕНИЕ[тип]
    место = м + заголовок + sum(дл for _, _, дл in фикс)
    указатели = место
    место += len(переменные) + (1 if есть_необяз else 0)
    if место > конец:
        return None
    куски = []
    параметры = []
    for i, (код, наим) in enumerate(переменные):
        ук = указатели + i
        цель = ук + д[ук]
        if д[ук] == 0 or цель >= конец or цель + 1 + д[цель] > конец or д[цель] < наим:
            return None
        куски.append((цель, цель + 1 + д[цель]))
        параметры.append((код, цель, д[цель], True))
    if есть_необяз and д[указатели + len(переменные)]:
        ук = указатели + len(переменные)
        где = ук + д[ук]
        начало = где
        while True:
            if где >= конец:
                return None
            код = д[где]
            if код == 0:
                где += 1
                break
            if где + 2 > конец or где + 2 + д[где + 1] > конец:
                return None
            параметры.append((код, где + 1, д[где + 1], False))
            где += 2 + д[где + 1]
        куски.append((начало, где))
    край = место
    for н, к in sorted(куски):
        if н != край:
            return None
        край = к
    if край != конец:
        return None
    return тип, фикс, переменные, есть_необяз, указатели, параметры


def _номер_isup(у, р: Разбор, п, м: int, дл: int, код: int) -> str:
    """Номер вызываемого/вызывающего (Q.763 3.9, 3.10): признак нечётности, характер
    адреса, план нумерации, цифры BCD (первая — в младшем полубайте)."""
    д = р.д
    нечётно, nai = д[м] >> 7, д[м] & 0x7F
    np = (д[м + 1] >> 4) & 7
    ключ = {0x04: "isup.called", 0x0A: "isup.calling"}.get(код, "isup.number")
    у.поле("Нечётное число цифр", "isup.odd_even_indicator", нечётно, м, 1, родитель=п)
    у.поле("Характер адреса", "isup.nature_of_address_indicator",
           f"{nai} ({ХАРАКТЕР_НОМЕРА.get(nai, 'неизвестный')})", м, 1, nai, родитель=п)
    у.поле("План нумерации", "isup.numbering_plan_indicator", f"{np} ({ПЛАНЫ_ISUP.get(np, 'неизвестный')})",
           м + 1, 1, np, родитель=п)
    if код == 0x0A:
        у.поле("Ограничение показа", "isup.address_presentation_restricted_indicator",
               (д[м + 1] >> 2) & 3, м + 1, 1, родитель=п)
        у.поле("Проверка", "isup.screening_indicator", д[м + 1] & 3, м + 1, 1, родитель=п)
    цифры = _цифры(д[м + 2:м + дл], bool(нечётно))
    у.поле("Цифры", ключ, цифры, м + 2, дл - 2, родитель=п)
    return цифры


def isup(р: Разбор, м: int, конец: int, cic: bool = True) -> bool:
    """ISUP (Q.763): CIC (1.2, 12 бит ITU), тип сообщения (табл. 4), фиксированная,
    обязательная переменная и необязательная части; номера, причина (Q.850), событие.
    ``cic=False`` — без CIC (тело SIP-I/SIP-T)."""
    проверка = _проверка_isup(р.д, м, конец, cic)
    if проверка is None:
        return False
    тип, фикс, переменные, есть_необяз, указатели, параметры = проверка
    д = р.д
    у = р.уровень("ISUP", "ISDN User Part (Q.763)", м)
    место = м
    итог = [ISUP_ТИПЫ[тип]]
    if cic:
        номер = (д[м] | (д[м + 1] << 8)) & 0x0FFF
        у.поле("CIC", "isup.cic", номер, м, 2)
        у.поле("Запас CIC", "isup.cic_spare", д[м + 1] >> 4, м + 1, 1)
        итог.append(f"CIC {номер}")
        место += 2
    у.поле("Тип сообщения", "isup.message_type", f"0x{тип:02x} ({ISUP_ТИПЫ[тип]})", место, 1, тип)
    место += 1
    for подпись, ключ, дл in фикс:
        значение = int.from_bytes(д[место:место + дл], "big")
        if ключ == "isup.calling_partys_category":
            у.поле(подпись, ключ, f"0x{значение:02x} ({КАТЕГОРИИ.get(значение, 'неизвестна')})", место, 1, значение)
        elif ключ == "isup.event_ind":
            событие = значение & 0x7F
            у.поле(подпись, ключ, f"{событие} ({СОБЫТИЯ.get(событие, 'неизвестное')})", место, 1, событие)
            итог.append(СОБЫТИЯ.get(событие, f"событие {событие}"))
        else:
            у.поле(подпись, ключ, д[место:место + дл].hex(" "), место, дл, значение)
        место += дл
    for i in range(len(переменные) + (1 if есть_необяз else 0)):
        у.поле(f"Указатель {i + 1}", "isup.pointer", д[указатели + i], указатели + i, 1)
    for код, где, дл, обязателен in параметры:
        имя = ISUP_ПАРАМЕТРЫ.get(код, f"Параметр 0x{код:02x}")
        начало = где if обязателен else где - 1
        п = у.поле(имя, "isup.parameter_type", "", начало, где + 1 + дл - начало, код)
        у.поле("Длина", "isup.parameter_length", дл, где, 1, родитель=п)
        знач = где + 1
        if код in (0x04, 0x0A) and дл >= 2:
            цифры = _номер_isup(у, р, п, знач, дл, код)
            п.текст = цифры
            итог.append(("→ " if код == 0x04 else "от ") + цифры)
        elif код == 0x12 and дл >= 2:
            место_причины = знач + (1 if not д[знач] & 0x80 and дл >= 3 else 0)
            причина = д[место_причины + 1] & 0x7F
            у.поле("Стандарт кодирования", "isup.coding_standard", (д[знач] >> 5) & 3, знач, 1, родитель=п)
            у.поле("Место", "isup.location", д[знач] & 15, знач, 1, родитель=п)
            у.поле("Причина", "isup.cause_indicator", f"{причина} ({ПРИЧИНЫ.get(причина, 'неизвестная')})",
                   место_причины + 1, 1, причина, родитель=п)
            п.текст = f"{причина} ({ПРИЧИНЫ.get(причина, 'неизвестная')})"
            итог.append(f"причина {причина}")
        else:
            п.текст = д[знач:знач + дл].hex()
            у.поле("Значение", "isup.parameter_value", п.текст, знач, дл, родитель=п)
    у.длина = конец - м
    у.итог = ", ".join(итог)
    р.п.инфо = "ISUP " + у.итог
    return True


# -- Q.931 ---------------------------------------------------------------------------------------------

Q931_ТИПЫ = {0x01: "ALERTING", 0x02: "CALL PROCEEDING", 0x03: "PROGRESS", 0x05: "SETUP",
             0x07: "CONNECT", 0x0D: "SETUP ACKNOWLEDGE", 0x0F: "CONNECT ACKNOWLEDGE",
             0x45: "DISCONNECT", 0x46: "RESTART", 0x4D: "RELEASE", 0x4E: "RESTART ACKNOWLEDGE",
             0x5A: "RELEASE COMPLETE", 0x6E: "NOTIFY", 0x75: "STATUS ENQUIRY", 0x7B: "INFORMATION",
             0x7D: "STATUS"}
Q931_ЭЛЕМЕНТЫ = {0x04: "Bearer capability", 0x08: "Cause", 0x14: "Call state", 0x18: "Channel identification",
                 0x1C: "Facility", 0x1E: "Progress indicator", 0x27: "Notification indicator", 0x28: "Display",
                 0x29: "Date/time", 0x2C: "Keypad facility", 0x34: "Signal", 0x6C: "Calling party number",
                 0x6D: "Calling party subaddress", 0x70: "Called party number",
                 0x71: "Called party subaddress", 0x79: "Restart indicator",
                 0x7C: "Low layer compatibility", 0x7D: "High layer compatibility", 0x7E: "User-user"}
ВОЗМОЖНОСТИ = {0x00: "речь", 0x08: "цифровая без ограничений", 0x09: "цифровая с ограничениями",
               0x10: "аудио 3,1 кГц", 0x11: "цифровая без ограничений с тонами", 0x18: "видео"}
ТИП_НОМЕРА = {0: "неизвестный", 1: "международный", 2: "национальный", 4: "абонентский"}
ПЛАНЫ_Q931 = {0: "неизвестный", 1: "ISDN/телефония (E.164)", 9: "частный"}


def _проверка_q931(д: bytes, м: int, конец: int):
    """Q.931 4.2–4.5: дискриминатор 0x08, длина ссылки вызова (биты 8–5 — нули, не больше
    двух октетов), известный тип (бит 8 — 0), элементы укладываются ровно до конца."""
    if конец - м < 3 or д[м] != 0x08 or д[м + 1] & 0xF0 or д[м + 1] > 2:
        return None
    дл_ссылки = д[м + 1]
    место = м + 2 + дл_ссылки
    if место >= конец or д[место] not in Q931_ТИПЫ:
        return None
    элементы = []
    где = место + 1
    while где < конец:
        ид = д[где]
        if ид & 0x80:                    # одноктетный элемент (4.5.1)
            элементы.append((ид, где, 1))
            где += 1
            continue
        if где + 2 > конец or где + 2 + д[где + 1] > конец:
            return None
        элементы.append((ид, где, 2 + д[где + 1]))
        где += 2 + д[где + 1]
    return дл_ссылки, место, элементы


def q931(р: Разбор, м: int, конец: int) -> bool:
    """Q.931 (4.2 дискриминатор, 4.3 ссылка вызова, 4.4 тип сообщения, 4.5 элементы:
    Bearer capability 4.5.5, Cause 4.5.12, Called/Calling party number 4.5.8/4.5.10)."""
    проверка = _проверка_q931(р.д, м, конец)
    if проверка is None:
        return False
    дл_ссылки, место_типа, элементы = проверка
    д = р.д
    у = р.уровень("Q.931", "ISDN Q.931", м)
    у.поле("", "q931", True, м, 0)
    у.поле("Дискриминатор протокола", "q931.disc", "0x08 (Q.931)", м, 1, 8)
    у.поле("Длина ссылки вызова", "q931.call_ref_len", дл_ссылки, м + 1, 1)
    итог = []
    if дл_ссылки:
        флаг = д[м + 2] >> 7
        ссылка = int.from_bytes(д[м + 2:м + 2 + дл_ссылки], "big") & ((1 << (8 * дл_ссылки - 1)) - 1)
        у.поле("Флаг ссылки", "q931.call_ref_flag", f"{флаг} ({'к исходящей стороне' if флаг else 'от исходящей стороны'})",
               м + 2, 1, флаг)
        у.поле("Ссылка вызова", "q931.call_ref", f"0x{ссылка:x}", м + 2, дл_ссылки, ссылка)
    тип = д[место_типа]
    у.поле("Тип сообщения", "q931.message_type", f"0x{тип:02x} ({Q931_ТИПЫ[тип]})", место_типа, 1, тип)
    итог.append(Q931_ТИПЫ[тип])
    if дл_ссылки:
        итог.append(f"ссылка 0x{ссылка:x}")
    for ид, где, дл in элементы:
        if ид & 0x80:
            у.поле(f"Одноктетный элемент 0x{ид:02x}", "q931.ie.single", f"0x{ид:02x}", где, 1, ид)
            continue
        имя = Q931_ЭЛЕМЕНТЫ.get(ид, f"Элемент 0x{ид:02x}")
        п = у.поле(имя, "q931.ie", "", где, дл, ид)
        у.поле("Длина", "q931.ie.len", дл - 2, где + 1, 1, родитель=п)
        значение = где + 2
        конец_эл = где + дл
        текст = _элемент_q931(у, п, д, ид, значение, конец_эл)
        п.текст = текст
        if ид in (0x70, 0x6C) and текст:
            итог.append(("→ " if ид == 0x70 else "от ") + текст)
        elif ид == 0x08 and текст:
            итог.append("причина " + текст)
    у.длина = конец - м
    у.итог = ", ".join(итог)
    р.п.инфо = "Q.931 " + у.итог
    return True


def _элемент_q931(у, п, д: bytes, ид: int, м: int, конец: int) -> str:
    if ид == 0x04 and конец - м >= 2:
        стандарт, возможность = (д[м] >> 5) & 3, д[м] & 0x1F
        режим, скорость = (д[м + 1] >> 5) & 3, д[м + 1] & 0x1F
        у.поле("Стандарт кодирования", "q931.coding_standard", стандарт, м, 1, родитель=п)
        у.поле("Возможность передачи", "q931.information_transfer_capability",
               f"0x{возможность:02x} ({ВОЗМОЖНОСТИ.get(возможность, 'неизвестная')})", м, 1, возможность, родитель=п)
        у.поле("Режим передачи", "q931.transfer_mode", режим, м + 1, 1, родитель=п)
        у.поле("Скорость", "q931.information_transfer_rate",
               f"0x{скорость:02x}" + (" (64 кбит/с)" if скорость == 0x10 else ""), м + 1, 1, скорость, родитель=п)
        return ВОЗМОЖНОСТИ.get(возможность, f"0x{возможность:02x}")
    if ид == 0x08 and конец - м >= 2:
        место = м + 1 if д[м] & 0x80 else м + 2      # октет 3a — при нулевом бите расширения
        if место >= конец:
            return ""
        причина = д[место] & 0x7F
        у.поле("Стандарт кодирования", "q931.coding_standard", (д[м] >> 5) & 3, м, 1, родитель=п)
        у.поле("Место", "q931.cause_location", д[м] & 15, м, 1, родитель=п)
        у.поле("Причина", "q931.cause_value", f"{причина} ({ПРИЧИНЫ.get(причина, 'неизвестная')})", место, 1,
               причина, родитель=п)
        return f"{причина} ({ПРИЧИНЫ.get(причина, 'неизвестная')})"
    if ид in (0x70, 0x6C) and конец - м >= 1:
        ключ = "q931.called_party_number.digits" if ид == 0x70 else "q931.calling_party_number.digits"
        тон, план = (д[м] >> 4) & 7, д[м] & 15
        у.поле("Тип номера", "q931.type_of_number", f"{тон} ({ТИП_НОМЕРА.get(тон, 'неизвестный')})", м, 1, тон,
               родитель=п)
        у.поле("План нумерации", "q931.numbering_plan", f"{план} ({ПЛАНЫ_Q931.get(план, 'неизвестный')})", м, 1,
               план, родитель=п)
        место = м + 1
        if ид == 0x6C and not д[м] & 0x80 and место < конец:
            у.поле("Показ", "q931.presentation_indicator", (д[место] >> 5) & 3, место, 1, родитель=п)
            у.поле("Проверка", "q931.screening_indicator", д[место] & 3, место, 1, родитель=п)
            место += 1
        цифры = "".join(chr(б & 0x7F) for б in д[место:конец])
        у.поле("Цифры", ключ, цифры, место, конец - место, родитель=п)
        return цифры
    у.поле("Значение", "q931.ie.data", д[м:конец].hex() or "(пусто)", м, конец - м, родитель=п)
    return д[м:конец].hex()


# -- LAPD (Q.921) --------------------------------------------------------------------------------------

LAPD_S = {0x01: "RR", 0x05: "RNR", 0x09: "REJ"}
LAPD_U = {0x6F: "SABME", 0x0F: "DM", 0x03: "UI", 0x43: "DISC", 0x63: "UA", 0x87: "FRMR", 0xAF: "XID"}


def lapd(р: Разбор, м: int) -> None:
    """LAPD (Q.921): адрес (3.2: SAPI, C/R, EA0=0; TEI, EA1=1), поле управления
    (3.4–3.6: I — N(S)/N(R)/P, S — RR/RNR/REJ, U — SABME/DM/UI/DISC/UA/FRMR/XID).
    Информация кадров I и UI с SAPI 0 — Q.931."""
    д = р.д
    у = р.уровень("LAPD", "Link Access Procedure, D-channel (Q.921)", м)
    р.нужно(м, 3)
    sapi, cr, ea0 = д[м] >> 2, (д[м] >> 1) & 1, д[м] & 1
    tei, ea1 = д[м + 1] >> 1, д[м + 1] & 1
    п = у.поле("Адрес", "lapd.address", f"SAPI {sapi}, TEI {tei}", м, 2, (д[м] << 8) | д[м + 1])
    у.поле("SAPI", "lapd.sapi", sapi, м, 1, родитель=п)
    у.поле("C/R", "lapd.cr", cr, м, 1, родитель=п)
    у.поле("EA0", "lapd.ea1", ea0, м, 1, родитель=п, плохо=bool(ea0))
    у.поле("TEI", "lapd.tei", tei, м + 1, 1, родитель=п)
    у.поле("EA1", "lapd.ea2", ea1, м + 1, 1, родитель=п, плохо=not ea1)
    if ea0 or not ea1:
        р.ошибка("LAPD: биты расширения адреса не по Q.921 (EA0=0, EA1=1)")
    упр = д[м + 2]
    итог = f"SAPI {sapi}, TEI {tei}"
    информация = None
    if упр & 1 == 0:                          # I-кадр: 2 октета
        р.нужно(м + 2, 2)
        ns, nr, pf = упр >> 1, д[м + 3] >> 1, д[м + 3] & 1
        у.поле("Управление", "lapd.control", f"I, N(S)={ns}, N(R)={nr}, P={pf}", м + 2, 2, u16(д, м + 2))
        у.поле("N(S)", "lapd.control.n_s", ns, м + 2, 1)
        у.поле("N(R)", "lapd.control.n_r", nr, м + 3, 1)
        у.поле("P", "lapd.control.p", pf, м + 3, 1)
        у.поле("", "lapd.control.ftype", "I", м + 2, 0)
        у.длина = 4
        итог = f"I, {итог}, N(S)={ns}, N(R)={nr}"
        информация = м + 4
    elif упр & 3 == 1:                        # S-кадр: 2 октета
        р.нужно(м + 2, 2)
        вид = LAPD_S.get(упр)
        nr, pf = д[м + 3] >> 1, д[м + 3] & 1
        у.поле("Управление", "lapd.control", f"{вид or f'0x{упр:02x}'}, N(R)={nr}, P/F={pf}", м + 2, 2,
               u16(д, м + 2))
        у.поле("Вид S", "lapd.control.s_ftype", вид or f"0x{упр:02x}", м + 2, 1, упр, плохо=вид is None)
        у.поле("N(R)", "lapd.control.n_r", nr, м + 3, 1)
        у.поле("P/F", "lapd.control.pf", pf, м + 3, 1)
        у.длина = 4
        if вид is None:
            р.ошибка(f"LAPD: неизвестный S-кадр 0x{упр:02x}")
        итог = f"{вид or 'S?'}, {итог}, N(R)={nr}"
        if м + 4 < len(д):
            _данные(р, м + 4, "лишнее в S-кадре")
    else:                                     # U-кадр: 1 октет
        вид = LAPD_U.get(упр & 0xEF)
        pf = (упр >> 4) & 1
        у.поле("Управление", "lapd.control", f"{вид or f'0x{упр:02x}'}, P/F={pf}", м + 2, 1, упр)
        у.поле("Вид U", "lapd.control.u_modifier", вид or f"0x{упр:02x}", м + 2, 1, упр & 0xEF, плохо=вид is None)
        у.поле("P/F", "lapd.control.pf", pf, м + 2, 1)
        у.длина = 3
        if вид is None:
            р.ошибка(f"LAPD: неизвестный U-кадр 0x{упр:02x}")
        итог = f"{вид or 'U?'}, {итог}"
        if вид in ("UI", "XID", "FRMR"):
            информация = м + 3
    у.итог = итог
    р.п.инфо = "LAPD " + итог
    if информация is not None and информация < len(д):
        from . import abis  # noqa: PLC0415
        с_данными = not (ea0 or not ea1) and (упр & 1 == 0 or LAPD_U.get(упр & 0xEF) == "UI")
        конец = _конец_кадра(р)
        # DCC SDH (G.784): в информации — PDU OSI с NLPID (CLNP, ES-IS, IS-IS); разборщик проверяет себя сам.
        from .kanalnye import osi_pdu  # noqa: PLC0415
        if с_данными and osi_pdu(р, информация, конец):
            return
        # Abis (GSM 08.56): SAPI 0 — RSL (48.058), SAPI 62 — OML (12.21); иначе SAPI 0 — Q.931.
        if sapi == 0 and с_данными and abis.rsl(р, информация, конец):
            return
        if sapi == 62 and с_данными and abis.oml(р, информация, конец):
            return
        if sapi == 0 and с_данными:
            _дальше(р, q931, информация, конец, "информация LAPD, не Q.931")
        else:
            _данные(р, информация, "управление TEI" if sapi == 63 else f"информация LAPD, SAPI {sapi}")


def lapd_linux(р: Разбор, м: int) -> None:
    """LINKTYPE_LINUX_LAPD (177): псевдозаголовок vISDN из 16 октетов — тип пакета,
    тип оборудования, длина адреса, адрес (8), протокол — затем кадр Q.921."""
    д = р.д
    у = р.уровень("SLL", "Linux LAPD (псевдозаголовок vISDN)", м)
    р.нужно(м, 16)
    у.поле("Тип пакета", "sll.pkttype", u16(д, м), м, 2)
    у.поле("Тип оборудования", "sll.hatype", u16(д, м + 2), м + 2, 2)
    у.поле("Длина адреса", "sll.halen", u16(д, м + 4), м + 4, 2)
    у.поле("Адрес", "sll.src.eth", д[м + 6:м + 14].hex(":"), м + 6, 8)
    у.поле("Протокол", "sll.etype", f"0x{u16(д, м + 14):04x}", м + 14, 2, u16(д, м + 14))
    у.длина = 16
    у.итог = "псевдозаголовок LAPD"
    lapd(р, м + 16)


def mtp3_канал(р: Разбор, м: int) -> None:
    """Канал LINKTYPE_MTP3: сообщение MTP3 без MTP2."""
    р.нужно(м, 5)
    mtp3(р, м, _конец_кадра(р))


# -- регистрация ---------------------------------------------------------------------------------------

for _ppid, _разборщик in ((1, iua), (2, m2ua), (3, m3ua), (4, sua), (5, m2pa)):
    ДОП_SCTP_PPID.setdefault(_ppid, _разборщик)
for _порт, _разборщик in ((9900, iua), (2904, m2ua), (2905, m3ua), (14001, sua), (3565, m2pa)):
    ДОП_SCTP_ПОРТ.setdefault(_порт, _разборщик)
for _номер, _канал, _разборщик in ((140, "MTP2", mtp2), (141, "MTP3", mtp3_канал), (142, "SCCP", sccp_канал),
                                   (177, "Linux LAPD", lapd_linux), (203, "LAPD", lapd)):
    КАНАЛЫ.setdefault(_номер, _канал)
    КАНАЛ_В_РАЗБОРЩИК.setdefault(_канал, _разборщик)
КАНАЛ_В_РАЗБОРЩИК.setdefault("TCAP", tcap_канал)
for _ssn in range(256):
    КАНАЛ_В_РАЗБОРЩИК.setdefault(f"TCAP (SSN {_ssn})", functools.partial(tcap_канал, ssn=_ssn))
for _имя, _уровень in (("M3UA", "транспортный"), ("M2UA", "транспортный"), ("M2PA", "транспортный"),
                       ("SUA", "транспортный"), ("IUA", "транспортный"), ("MTP2", "канальный"),
                       ("LAPD", "канальный"), ("MTP3", "сетевой"), ("SCCP", "сетевой"),
                       ("TCAP", "прикладной"), ("ISUP", "прикладной"), ("MAP", "прикладной"),
                       ("Q.931", "прикладной")):
    ДОП_УРОВНИ.setdefault(_имя, _уровень)
