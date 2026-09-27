"""Сетевой и транспортный уровни OSI — то, что идёт по DCC SDH (G.784, стек сетевого управления) и по
LLC 0xFE/PPP 0x0023/Frame Relay: CLNP (ISO 8473-1), ES-IS (ISO 9542), транспорт ISO 8073 (классы 0–4,
в DCC — TP4) и текст TL1 над ним.

Документы (структуры — строго по ним):

* CLNP — ISO 8473-1, 7.2–7.5: NLPID 0x81, длина заголовка, версия 1, время жизни (в 500 мс), SP/MS/E/R
  и тип (DT 0x1C, MD 0x1D, ER 0x01, ERQ 0x1E, ERP 0x1F), длина сегмента, контрольная сумма (ISO 8473
  7.2.9: Флетчер по модулю 255 по заголовку, 0 — не считается), адреса назначения и источника (длина и
  NSAP), часть сегментации (идентификатор, смещение, полная длина — при SP), параметры до конца
  заголовка (код, длина, значение; причина отбрасывания в ER — класс и код по таблицам Wireshark
  packet-osi-options.c). Данные DT — транспорт ISO 8073.
* ES-IS — ISO 9542, 9: NLPID 0x82, длина, версия 1, тип (ESH 2, ISH 4, RD 6), время удержания (с),
  контрольная сумма; ESH — число адресов и NSAP, ISH — NET, RD — адрес назначения, SNPA, NET.
* NSAP — ISO 8348, прил. A: AFI и далее по два октета; у адреса от 8 октетов последние 7 —
  системный идентификатор (6) и селектор NSEL — так их пишут в NET (IS-IS, ISO 10589).
* Транспорт — ISO 8073 / X.224, 13: LI, код TPDU (старшая тетрада; у CR/CC/AK/RJ младшая — CDT),
  постоянная часть по виду (ссылки, класс, номер и EOT, причина), переменная — параметры (имена и причины
  разъединения/отказа — таблицы Wireshark packet-ositp.c); несколько TPDU подряд (13.2.2, сцепление);
  параметр 0xC3 — контрольная сумма всего TPDU тем же Флетчером (6.17). Данные DT с EOT — сеанс ISO
  (X.225, разбор модуля promyshlennye) или текст TL1.
* TL1 (Telcordia GR-831) — строки ASCII: команда «ГЛАГОЛ-МОД:TID:AID:CTAG…;», ответ с «M CTAG код»,
  автономное сообщение с «*C/**/*/A ATAG»; здесь — только эти части, без таблиц кодов.
"""

from __future__ import annotations

import re

from ..razbor import ДОП_УРОВНИ, Разбор, данные
from . import kanalnye, promyshlennye

#: Таблицы — из Wireshark (packet-osi-options.c, packet-ositp.c), собраны программой без пропусков.
КЛАССЫ_ОТБРАСЫВАНИЯ = {0: 'General', 8: 'Address', 9: 'Source Routing', 10: 'Lifetime', 11: 'PDU discarded', 12: 'Reassembly'}
ПРИЧИНЫ_ОТБРАСЫВАНИЯ = {0: {0: 'Reason not specified',
     1: 'Protocol procedure error',
     2: 'Incorrect checksum',
     3: 'PDU discarded due to congestion',
     4: 'Header syntax error ( cannot be parsed )',
     5: 'Segmentation needed but not permitted',
     6: 'Incomplete PDU received',
     7: 'Duplicate option'},
 8: {0: 'Destination Address unreachable', 1: 'Destination Address unknown'},
 9: {0: 'Unspecified source routing error',
     1: 'Syntax error in source routing field',
     2: 'Unknown address in source routing field',
     3: 'Path not acceptable'},
 10: {0: 'Lifetime expired while data unit in transit', 1: 'Lifetime expired during reassembly'},
 11: {0: 'Unsupported option not specified',
      1: 'Unsupported protocol version',
      2: 'Unsupported security option',
      3: 'Unsupported source routing option',
      4: 'Unsupported recording of route option'},
 12: {0: 'Reassembly interference'}}
TP_ПАРАМЕТРЫ = {8: 'ATN extended checksum - 32 bit',
 9: 'ATN extended checksum - 16 bit',
 133: 'ack time',
 134: 'res error',
 135: 'priority',
 136: 'transit delay',
 137: 'throughput',
 138: 'seq number',
 139: 'reassignment',
 140: 'flow control',
 192: 'tpdu-size',
 193: 'src-tsap',
 194: 'dst-tsap',
 195: 'checksum',
 196: 'version',
 197: 'protection',
 198: 'options',
 199: 'proto class',
 224: 'additional connection clearing info',
 240: 'preferred max TPDU size',
 242: 'inactivity timer'}
TP_РАЗЪЕДИНЕНИЕ = {0: 'Reason not specified', 1: 'Congestion at TSAP', 2: 'Session entity not attached to TSAP', 3: 'Address unknown'}
TP_ОТКАЗ = {0: 'Reason not specified', 1: 'Invalid parameter code', 2: 'Invalid TPDU type', 3: 'Invalid parameter value'}
TPDU = {1: 'ED Expedited Data',
 2: 'EA Expedited Data Acknowledgement',
 5: 'RJ Reject',
 6: 'AK Data Acknowledgement',
 7: 'ER TPDU Error',
 8: 'DR Disconnect Request',
 12: 'DC Disconnect Confirm',
 13: 'CC Connect Confirm',
 14: 'CR Connect Request',
 15: 'DT Data'}

CLNP_ТИПЫ = {0x1C: "DT (данные)", 0x1D: "MD (групповые данные)", 0x01: "ER (ошибка)", 0x1E: "ERQ (эхо-запрос)",
             0x1F: "ERP (эхо-ответ)"}
CLNP_ПАРАМЕТРЫ = {0xC5: "Защита", 0xC3: "Качество обслуживания", 0xCD: "Приоритет", 0xCC: "Заполнение",
                  0xC8: "Маршрут от источника", 0xCB: "Запись маршрута", 0xC1: "Причина отбрасывания"}
ESIS_ТИПЫ = {2: "ESH (приветствие ES)", 4: "ISH (приветствие IS)", 6: "RD (перенаправление)"}


def флетчер(б: bytes) -> tuple[int, int]:
    """Суммы Флетчера по модулю 255 (ISO 8473 7.2.9, ISO 8073 6.17): у верного PDU обе равны 0."""
    c0 = c1 = 0
    for x in б:
        c0 = (c0 + x) % 255
        c1 = (c1 + c0) % 255
    return c0, c1


def nsap(б: bytes) -> str:
    """NSAP: AFI, затем пары октетов; от 9 октетов — «область.системный идентификатор.NSEL»
    (у 8 октетов обе записи совпадают)."""
    if not б:
        return "—"
    if len(б) >= 9:
        область, система, nsel = б[:-7], б[-7:-1], б[-1]
    else:
        область, система, nsel = б, b"", None
    части = [область[:1].hex()] + [область[i:i + 2].hex() for i in range(1, len(область), 2)]
    if система:
        части += [система[i:i + 2].hex() for i in range(0, len(система), 2)]
    return ".".join(части) + (f".{nsel:02x}" if nsel is not None else "")


def _сумма(у, д: bytes, м: int, дл: int, место: int, ключ: str, р: Разбор, что: str) -> None:
    """Поле контрольной суммы Флетчера: 0 — не считается, иначе обе суммы по PDU должны быть 0."""
    значение = int.from_bytes(д[место:место + 2], "big")
    if значение == 0:
        у.поле("Контрольная сумма", ключ, "0x0000 (не считается)", место, 2, 0)
        return
    верна = флетчер(д[м:м + дл]) == (0, 0)
    у.поле("Контрольная сумма", ключ, f"0x{значение:04x} " + ("(верна)" if верна else "(не сходится)"), место, 2,
           значение, плохо=not верна)
    if not верна:
        р.ошибка(f"{что}: контрольная сумма не сходится")


# -- CLNP ---------------------------------------------------------------------------------------

def clnp(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if конец - м < 10 or д[м] != 0x81 or д[м + 2] != 1:
        return False
    # Заголовок короче 11 октетов отвергают проверки адресов ниже.
    li, тип, длина = д[м + 1], д[м + 4], int.from_bytes(д[м + 5:м + 7], "big")
    код_вида = тип & 0x1F
    if длина < li or м + длина > конец or код_вида not in CLNP_ТИПЫ:
        return False
    край = м + li
    x = м + 9
    назначение = (x + 1, д[x])
    x += 1 + д[x]
    if x >= край:
        return False
    источник = (x + 1, д[x])
    x += 1 + д[x]
    сегменты = тип & 0x80
    if x + (6 if сегменты else 0) > край:
        return False
    у = р.уровень("CLNP", "ISO 8473 CLNP", м)
    у.поле("NLPID", "clnp.nlpi", "0x81 (CLNP)", м, 1, 0x81)
    у.поле("Длина заголовка", "clnp.len", li, м + 1, 1)
    у.поле("Версия", "clnp.version", 1, м + 2, 1)
    у.поле("Время жизни", "clnp.ttl", f"{д[м + 3]} ({д[м + 3] / 2:g} с)", м + 3, 1, д[м + 3])
    вид = CLNP_ТИПЫ[код_вида]
    т = у.поле("Тип PDU", "clnp.type", вид, м + 4, 1, тип)
    for имя, ключ, бит in (("Сегментация разрешена (SP)", "clnp.cnf.segmentation", 0x80),
                           ("Ещё сегменты (MS)", "clnp.cnf.more_segments", 0x40),
                           ("Сообщать об отбрасывании (E/R)", "clnp.cnf.report_error", 0x20)):
        у.поле(имя, ключ, int(bool(тип & бит)), м + 4, 1, родитель=т)
    у.поле("Длина сегмента", "clnp.pdu.len", длина, м + 5, 2)
    _сумма(у, д, м, li, м + 7, "clnp.checksum", р, "CLNP")
    куда, откуда = nsap(д[назначение[0]:sum(назначение)]), nsap(д[источник[0]:sum(источник)])
    у.поле("Длина адреса назначения", "clnp.dsap.len", назначение[1], назначение[0] - 1, 1)
    у.поле("Адрес назначения", "clnp.dsap", куда, назначение[0], назначение[1])
    у.поле("Длина адреса источника", "clnp.ssap.len", источник[1], источник[0] - 1, 1)
    у.поле("Адрес источника", "clnp.ssap", откуда, источник[0], источник[1])
    смещение = 0
    if сегменты:
        смещение = int.from_bytes(д[x + 2:x + 4], "big")
        у.поле("Идентификатор данных", "clnp.data_unit_identifier", int.from_bytes(д[x:x + 2], "big"), x, 2)
        у.поле("Смещение сегмента", "clnp.segment_offset", смещение, x + 2, 2)
        у.поле("Полная длина", "clnp.total_length", int.from_bytes(д[x + 4:x + 6], "big"), x + 4, 2)
        x += 6
    причина = _параметры(р, у, x, край)
    у.длина = li
    у.итог = f"{вид.split()[0]} {откуда} → {куда}" + (f", {причина}" if причина else "")
    р.п.инфо = "CLNP " + у.итог
    данные_ = м + li                                          # данных нет — ниже ничего не добавится
    if код_вида == 0x01:                                      # ER: заголовок отброшенного PDU
        if not clnp(р, данные_, м + длина):
            данные(р, данные_, "CLNP: отброшенный PDU", м + длина)
        р.п.инфо = "CLNP " + у.итог
        return True
    if тип & 0x40 or смещение:
        данные(р, данные_, "CLNP: сегмент", м + длина)
        return True
    if код_вида in (0x1C, 0x1D) and tp(р, данные_, м + длина):
        return True
    данные(р, данные_, "CLNP: данные", м + длина)
    return True


def _параметры(р: Разбор, у, x: int, край: int) -> str:
    """Параметры CLNP/ES-IS (ISO 8473 7.5): код, длина, значение; причина отбрасывания — строкой."""
    д, итог = р.д, ""
    while x + 2 <= край:
        код, дл = д[x], д[x + 1]
        if x + 2 + дл > край:
            р.ошибка("CLNP: параметр длиннее заголовка")
            break
        значение = д[x + 2:x + 2 + дл]
        имя = CLNP_ПАРАМЕТРЫ.get(код, f"Параметр 0x{код:02x}")
        текст = значение.hex()
        if код == 0xC1 and дл >= 1:
            # Причина отбрасывания (ISO 8473 7.5.5): класс — старшая тетрада, код — младшая; второй октет —
            # место ошибки в заголовке.
            класс, подкод = значение[0] >> 4, значение[0] & 0x0F
            текст = (f"{КЛАССЫ_ОТБРАСЫВАНИЯ.get(класс, f'класс {класс}')}: "
                     f"{ПРИЧИНЫ_ОТБРАСЫВАНИЯ.get(класс, {}).get(подкод, f'код {подкод}')}"
                     + (f", октет {значение[1]}" if дл >= 2 else ""))
            итог = f"причина: {текст}"
        у.поле(f"{имя}: {текст}", "clnp.option", текст, x, 2 + дл, код)
        x += 2 + дл
    return итог


# -- ES-IS --------------------------------------------------------------------------------------

def esis(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if конец - м < 10 or д[м] != 0x82 or д[м + 2] != 1:
        return False
    li, тип = д[м + 1], д[м + 4] & 0x1F
    if тип not in ESIS_ТИПЫ or м + li > конец:                # заголовок без адресов отвергается ниже
        return False
    край = м + li
    адреса, x = [], м + 9
    число = 1
    if тип == 2:
        число, x = д[x], x + 1
    elif тип == 6:
        число = 3
    for i in range(число):
        if x >= край or x + 1 + д[x] > край:
            if тип == 6 and i == 2:                            # NET в RD может отсутствовать
                break
            return False
        адреса.append((x, д[x]))
        x += 1 + д[x]
    у = р.уровень("ES-IS", "ISO 9542 ES-IS", м)
    у.поле("NLPID", "esis.nlpi", "0x82 (ES-IS)", м, 1, 0x82)
    у.поле("Длина", "esis.length", li, м + 1, 1)
    у.поле("Версия", "esis.ver", 1, м + 2, 1)
    вид = ESIS_ТИПЫ[тип]
    у.поле("Тип", "esis.type", вид, м + 4, 1, тип)
    удержание = int.from_bytes(д[м + 5:м + 7], "big")
    у.поле("Время удержания", "esis.htime", f"{удержание} с", м + 5, 2, удержание)
    _сумма(у, д, м, li, м + 7, "esis.chksum", р, "ES-IS")
    подписи = {2: ["NSAP"] * len(адреса), 4: ["NET"], 6: ["Назначение", "SNPA", "NET"]}[тип]
    тексты = []
    for (место, дл), подпись in zip(адреса, подписи, strict=False):
        текст = д[место + 1:место + 1 + дл].hex(":") if подпись == "SNPA" else nsap(д[место + 1:место + 1 + дл])
        у.поле(f"{подпись}: {текст}", "esis.nsap" if подпись != "SNPA" else "esis.bsnpa", текст, место, 1 + дл)
        тексты.append(f"{подпись} {текст}")
    _параметры(р, у, x, край)
    у.длина = li
    у.итог = f"{вид.split()[0]}, " + ", ".join(тексты) + f", удержание {удержание} с"
    р.п.инфо = "ES-IS " + у.итог
    данные(р, край, "ES-IS: после PDU", конец)
    return True


# -- транспорт ISO 8073 -------------------------------------------------------------------------

#: Постоянная часть после LI (октетов, вместе с кодом) — нормальный формат (13.3–13.12).
TP_ПОСТОЯННАЯ = {0xE: 6, 0xD: 6, 0x8: 6, 0xC: 5, 0xF: 4, 0x1: 4, 0x6: 4, 0x2: 4, 0x5: 4, 0x7: 4}


def _tpdu(д: bytes, м: int, конец: int):
    """(вид, LI, конец TPDU) или None: заголовок укладывается, постоянная часть по виду, параметры впритык."""
    if конец - м < 2:
        return None
    li, вид = д[м], д[м + 1] >> 4
    if вид not in TP_ПОСТОЯННАЯ or li == 255 or м + 1 + li > конец:
        return None
    постоянная = 2 if вид == 0xF and li == 2 else TP_ПОСТОЯННАЯ[вид]        # DT классов 0/1: без ссылки
    if li < постоянная:
        return None
    x, край = м + 1 + постоянная, м + 1 + li
    while x < край:
        if x + 2 > край or x + 2 + д[x + 1] > край:
            return None
        x += 2 + д[x + 1]
    # Данные несут только DT, ED, CR, CC, DR (13.3.4, 13.7, 13.9) — у прочих TPDU граница — конец заголовка.
    return вид, li, (конец if вид in (0xF, 0x1, 0xE, 0xD, 0x8) else край)


def tp(р: Разбор, м: int, конец: int) -> bool:
    """TPDU подряд (сцепление): все, кроме последнего, — без данных пользователя."""
    д = р.д
    первый = _tpdu(д, м, конец)
    if первый is None:
        return False
    итоги = []
    while True:                                               # после последнего TPDU _tpdu даёт None
        разбор = _tpdu(д, м, конец)
        if разбор is None:
            данные(р, м, "ISO 8073: не TPDU", конец)
            break
        вид, li, край = разбор
        итог, наверху = _tp_один(р, м, вид, li, край)
        итоги.append(итог)
        м = край
    if not наверху:                                           # сводку даёт узнанный прикладной уровень
        р.п.инфо = "TP " + "; ".join(итоги)
    return True


def _tp_один(р: Разбор, м: int, вид: int, li: int, край: int) -> tuple[str, bool]:
    """Один TPDU; (сводка, узнан ли прикладной уровень в данных)."""
    д = р.д
    у = р.уровень("TP", "ISO 8073 / X.224 (транспорт)", м)
    имя = TPDU[вид].split()[0]
    у.поле("Длина заголовка (LI)", "cotp.li", li, м, 1)
    у.поле("Вид TPDU", "cotp.type", TPDU[вид], м + 1, 1, вид)
    итог = [имя]
    x = м + 2
    if вид in (0xE, 0xD, 0x6, 0x5):
        у.поле("Кредит (CDT)", "cotp.cdt", д[м + 1] & 0x0F, м + 1, 1)
    if not (вид == 0xF and li == 2):
        ссылка = int.from_bytes(д[x:x + 2], "big")
        у.поле("Ссылка получателя", "cotp.destref", f"0x{ссылка:04x}", x, 2, ссылка)
        x += 2
    if вид in (0xE, 0xD, 0x8, 0xC):
        ссылка = int.from_bytes(д[x:x + 2], "big")
        у.поле("Ссылка отправителя", "cotp.srcref", f"0x{ссылка:04x}", x, 2, ссылка)
        x += 2
    if вид in (0xE, 0xD):
        у.поле("Класс", "cotp.class", д[x] >> 4, x, 1)
        у.поле("Опции", "cotp.opts", д[x] & 0x0F, x, 1)
        итог.append(f"класс {д[x] >> 4}")
        x += 1
    elif вид == 0x8:
        причина = TP_РАЗЪЕДИНЕНИЕ.get(д[x], f"{д[x]}")
        у.поле("Причина", "cotp.dr_reason", причина, x, 1, д[x])
        итог.append(f"причина: {причина}")
        x += 1
    elif вид == 0x7:
        причина = TP_ОТКАЗ.get(д[x], f"{д[x]}")
        у.поле("Причина отказа", "cotp.reject_cause", причина, x, 1, д[x])
        итог.append(f"причина: {причина}")
        x += 1
    elif вид in (0xF, 0x1):
        eot = д[x] >> 7
        у.поле("Номер TPDU", "cotp.tpdu-number", д[x] & 0x7F, x, 1)
        у.поле("Последний (EOT)", "cotp.eot", eot, x, 1)
        итог.append(f"N {д[x] & 0x7F}" + ("" if eot else ", не последний"))
        x += 1
    elif вид in (0x6, 0x2, 0x5):
        у.поле("Ожидаемый номер (YR-TU-NR)", "cotp.next-tpdu-number", д[x] & 0x7F, x, 1)
        итог.append(f"YR {д[x] & 0x7F}")
        x += 1
    конец_заг = м + 1 + li
    while x < конец_заг:
        код, дл = д[x], д[x + 1]
        значение = д[x + 2:x + 2 + дл]
        подпись = TP_ПАРАМЕТРЫ.get(код, f"параметр 0x{код:02x}")
        if код == 0xC3 and дл == 2:
            _сумма(у, д, м, край - м, x + 2, "cotp.checksum", р, "ISO 8073")
        elif код in (0xC1, 0xC2):
            текст = значение.decode("latin-1") if значение and all(32 <= б < 127 for б in значение) else значение.hex()
            у.поле(f"{подпись}: {текст}", "cotp.src-tsap" if код == 0xC1 else "cotp.dst-tsap", текст, x, 2 + дл)
            итог.append(f"{'TSAP вызывающего' if код == 0xC1 else 'TSAP вызываемого'} {текст}")
        elif код == 0xC0 and дл == 1:
            у.поле(f"{подпись}: {1 << значение[0]} байт", "cotp.tpdu-size", 1 << значение[0], x, 3)
        else:
            у.поле(f"{подпись}: {значение.hex() or '—'}", "cotp.parameter", значение.hex(), x, 2 + дл, код)
        x += 2 + дл
    у.длина = li + 1
    у.итог = ", ".join(итог)
    if вид == 0xF and eot:                                    # пустые данные не узнаются и не показываются
        return у.итог, _пользователь(р, конец_заг, край)
    данные(р, конец_заг, f"ISO 8073: данные {имя}", край)
    return у.итог, False


def _пользователь(р: Разбор, м: int, конец: int) -> bool:
    """Данные транспорта: сеанс ISO (X.225) → представление → …; иначе TL1; иначе байты. True — узнан."""
    уровней = len(р.п.уровни)
    try:
        if promyshlennye.ses(р, м, конец):
            return True
    except (IndexError, ValueError):
        pass
    del р.п.уровни[уровней:]
    if tl1(р, м, конец):
        return True
    данные(р, м, "ISO 8073: данные пользователя", конец)
    return False


# -- TL1 ----------------------------------------------------------------------------------------

_TL1_КОМАНДА = re.compile(r"([A-Z]{2,}(?:-[A-Z0-9]+){0,2}):([^:;]*):([^:;]*):([^:;]*)")
_TL1_ОТВЕТ = re.compile(r"^M\s+(\S+)\s+(\w+)", re.MULTILINE)
_TL1_АВТОНОМНОЕ = re.compile(r"^(\*C|\*\*|\*|A)\s+(\d+)\s+(\S+)", re.MULTILINE)


def tl1(р: Разбор, м: int, конец: int) -> bool:
    д = р.д[м:конец]
    if not д or not all(32 <= б < 127 or б in (9, 10, 13) for б in д) or not д.rstrip().endswith((b";", b">", b"<")):
        return False
    текст = д.decode("ascii")
    команда, ответ, автономное = _TL1_КОМАНДА.match(текст.lstrip()), _TL1_ОТВЕТ.search(текст), _TL1_АВТОНОМНОЕ.search(текст)
    if not (команда or ответ or автономное):
        return False
    у = р.уровень("TL1", "TL1 (Telcordia GR-831)", м)
    if команда:
        у.поле("Код команды", "tl1.cmd_code", команда.group(1), м, len(команда.group(1)))
        for i, (подпись, ключ) in enumerate((("TID", "tl1.tid"), ("AID", "tl1.aid"), ("CTAG", "tl1.ctag")), 2):
            у.поле(подпись, ключ, команда.group(i), м, len(д))
        у.итог = f"команда {команда.group(1)}, TID {команда.group(2) or '—'}, CTAG {команда.group(4) or '—'}"
    elif ответ:
        у.поле("CTAG", "tl1.ctag", ответ.group(1), м, len(д))
        у.поле("Код завершения", "tl1.comp_code", ответ.group(2), м, len(д))
        у.итог = f"ответ CTAG {ответ.group(1)}: {ответ.group(2)}"
    else:
        у.поле("Важность", "tl1.alarm_code", автономное.group(1), м, len(д))
        у.поле("ATAG", "tl1.atag", автономное.group(2), м, len(д))
        у.итог = f"автономное {автономное.group(1)} ATAG {автономное.group(2)} {автономное.group(3)}"
    for строка in текст.splitlines():
        if строка.strip():
            у.поле(строка.strip(), "tl1.line", строка.strip(), м, len(д))
    у.длина = len(д)
    р.п.инфо = "TL1 " + у.итог
    return True


kanalnye.OSI_NLPID.setdefault(0x81, clnp)
kanalnye.OSI_NLPID.setdefault(0x82, esis)
ДОП_УРОВНИ.update({"CLNP": "сетевой", "ES-IS": "сетевой", "TP": "транспортный", "TL1": "прикладной"})
