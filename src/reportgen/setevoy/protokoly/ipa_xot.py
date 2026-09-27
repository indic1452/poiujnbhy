"""Abis поверх IP (ip.access IPA) и X.25 поверх TCP (XOT).

Документы:

* IPA — открытого стандарта нет; формат — разбор Wireshark packet-gsm_ipa.c (сверено по нему; им же
  пользуется Osmocom): сообщения подряд, у каждого — длина (2 байта, без заголовка), протокол (1 байт),
  по UDP — ещё байт, если длина + 4 равна остатку. Протокол: 0x00–0x1F — RSL (номер — поток TRX),
  0xFF OML, 0xFE IPA CCM (PING/PONG, IDENTITY REQUEST/RESPONSE/ACK/NACK, PROXY, SSL INFO; у запроса
  и ответа — атрибуты: 0x01 и метка — просьба, 0x00, длина, метка, строка — ответ), 0xFD SCCP,
  0xFC MGCP, 0xEE расширение Osmocom (байт протокола: 0 CTRL, 1 MGCP, 2 LAC, 3 SMSC, 4 ORC, 5 GSUP,
  6 OAP), 0xDD отладка HSL (текст). Порты TCP — IPA_TCP_PORTS Wireshark: 3002, 3003, 3006, 4222,
  4249, 4250 (5000 не занимаем — на нём много иного). Разбор RSL (TS 48.058) и OML (TS 12.21) —
  модуль abis, SCCP — oks7, MGCP — promyshlennye.
* XOT — RFC 1613 (Cisco Systems X.25 over TCP): заголовок 4 байта — версия (0) и длина пакета X.25;
  пакеты подряд; порт TCP 1998. Пакет PVC Setup (тип 0xF5) — расширение Cisco, поля по
  packet-xot.c: версия, состояние, длины имён интерфейсов и LCN обеих сторон, окна и размеры
  пакетов (2^n), имена.
"""

from __future__ import annotations

from .. import prilozh
from ..pole import u16
from ..razbor import ДОП_УРОВНИ, Разбор, данные
from .abis import oml, rsl
from .kanalnye import x25
from .oks7 import sccp
from .promyshlennye import mgcp

ПРОТОКОЛЫ_IPA = {0xDD: "HSL Debug", 0xEE: "Osmocom", 0xFC: "MGCP", 0xFD: "SCCP", 0xFE: "IPA CCM", 0xFF: "OML"}
CCM = {0: "PING", 1: "PONG", 4: "IDENTITY REQUEST", 5: "IDENTITY RESPONSE", 6: "IDENTITY ACK",
       7: "IDENTITY NACK", 8: "PROXY REQUEST", 9: "PROXY ACK", 10: "PROXY NACK", 11: "SSL INFO"}
МЕТКИ = {0: "Serial Number", 1: "Unit Name", 2: "Location", 3: "Unit Type", 4: "Equipment Version",
         5: "Software Version", 6: "IP Address", 7: "MAC Address", 8: "Unit ID", 9: "User Name",
         10: "Password", 11: "Access Class", 12: "Application Protocol Version"}
OSMO = {0: "CTRL", 1: "MGCP", 2: "LAC", 3: "SMSC", 4: "ORC", 5: "GSUP", 6: "OAP"}


def _свой(протокол: int) -> bool:
    """Протокол IPA: 0x00–0x1F — RSL потока TRX, прочие — только из таблицы."""
    return протокол < 0x20 or протокол in ПРОТОКОЛЫ_IPA


def _имя_протокола(протокол: int) -> str:
    """Имя своего протокола (_свой): известный — из таблицы, остальные — RSL с номером TRX."""
    return ПРОТОКОЛЫ_IPA.get(протокол) or f"RSL (TRX {протокол})"


def _ccm(р: Разбор, у, м: int, конец: int) -> str:
    """Сообщение CCM: вид и у запроса/ответа личности — атрибуты."""
    д = р.д
    if м >= конец:
        return "пусто"
    вид = д[м]
    у.поле("Сообщение CCM", "ipaccess.msg_type", CCM.get(вид, f"0x{вид:02x}"), м, 1, вид)
    сведения = []
    if вид in (4, 5):
        x = м + 1
        while x + 2 <= конец:
            признак = д[x]
            if признак == 0x00:
                дл = д[x + 1]
                if дл < 1 or x + 2 + дл > конец:
                    р.ошибка("IPA: атрибут длиннее сообщения")
                    break
                метка = д[x + 2]
                знач = д[x + 3:x + 2 + дл].partition(b"\x00")[0].decode("latin-1")
                у.поле(МЕТКИ.get(метка, f"метка {метка}"), "ipaccess.attr_string", знач, x, 2 + дл, метка)
                сведения.append(f"{МЕТКИ.get(метка, метка)}={знач}")
                x += 2 + дл
            elif признак == 0x01:
                метка = д[x + 1]
                у.поле("Запрошено", "ipaccess.attr_tag", МЕТКИ.get(метка, f"метка {метка}"), x, 2, метка)
                сведения.append(МЕТКИ.get(метка, str(метка)))
                x += 2
            else:
                у.поле("Неизвестный атрибут", "ipaccess.attr_unk", признак, x, 2)
                x += 2
    return CCM.get(вид, f"CCM 0x{вид:02x}") + (f": {', '.join(сведения)}" if сведения else "")


def _ipa(р: Разбор, м: int, конец: int, udp: bool) -> bool:
    д = р.д
    if конец - м < 4 or not _свой(д[м + 2]):
        return False
    while м + 3 <= конец:
        дл, протокол = u16(д, м), д[м + 2]
        if not _свой(протокол):
            данные(р, м, "IPA: не сообщение", конец)
            break
        заголовок = 4 if udp and дл + 4 == конец - м else 3
        у = р.уровень("IPA", "ip.access (Abis поверх IP)", м)
        у.поле("Длина", "gsm_ipa.data_len", дл, м, 2)
        у.поле("Протокол", "gsm_ipa.protocol", _имя_протокола(протокол), м + 2, 1, протокол)
        x, край = м + заголовок, min(м + заголовок + дл, конец)
        у.длина = заголовок
        итог = _имя_протокола(протокол)
        if м + заголовок + дл > конец:
            р.ошибка("IPA: сообщение длиннее данных (продолжение — в следующем сегменте)")
        if протокол == 0xFE:
            у.длина = край - м
            итог = _ccm(р, у, x, край)
        elif протокол == 0xDD:
            у.длина = край - м
            текст = д[x:край].partition(b"\x00")[0].decode("latin-1")
            у.поле("Отладка", "gsm_ipa.hsl_debug", текст, x, край - x)
            итог = f"HSL: {текст}"
        elif протокол == 0xEE and x < край:
            osmo = д[x]
            у.поле("Протокол Osmocom", "gsm_ipa.osmo.protocol", OSMO.get(osmo, f"0x{osmo:02x}"), x, 1, osmo)
            у.длина = заголовок + 1
            итог = f"Osmocom {OSMO.get(osmo, f'0x{osmo:02x}')}"
            if osmo == 0:
                у.длина = край - м
                текст = д[x + 1:край].decode("latin-1")
                у.поле("CTRL", "gsm_ipa.osmo.ctrl_data", текст, x + 1, край - x - 1)
                итог += f": {текст.split(' ', 2)[0]}" if текст else ""
            else:
                у.итог, р.п.инфо = итог, "IPA " + итог
                if not (osmo == 1 and mgcp(р, x + 1, край)):
                    данные(р, x + 1, итог, край)
                итог = ""
        else:
            # Итог — до вложенного разбора: его сводка точнее и заменит нашу.
            у.итог, р.п.инфо = итог, "IPA " + итог
            разборщик = oml if протокол == 0xFF else sccp if протокол == 0xFD else mgcp if протокол == 0xFC else rsl
            if not разборщик(р, x, край):
                данные(р, x, f"IPA: {итог}", край)
            итог = ""
        if итог:
            у.итог, р.п.инфо = итог, "IPA " + итог
        м = м + заголовок + дл
    else:
        if м < конец:
            данные(р, м, "IPA: остаток короче заголовка", конец)
    return True


def ipa(р: Разбор, м: int, конец: int) -> bool:
    return _ipa(р, м, конец, False)


def ipa_udp(р: Разбор, м: int, конец: int) -> bool:
    return _ipa(р, м, конец, True)


# -- XOT ----------------------------------------------------------------------------------

СОСТОЯНИЯ_PVC = {0x00: "ожидание соединения", 0x08: "получатель отключился", 0x09: "PVC/TCP: в соединении отказано",
                 0x0A: "PVC/TCP: ошибка маршрутизации", 0x0B: "PVC/TCP: время соединения вышло",
                 0x10: "соединение по TCP", 0x11: "ожидание ответа PVC-SETUP", 0x12: "соединено",
                 0x13: "нет такого интерфейса", 0x14: "интерфейс получателя не поднят",
                 0x15: "интерфейс получателя — не X.25", 0x16: "нет такого PVC", 0x17: "PVC настроен иначе",
                 0x18: "окна не совпадают", 0x19: "окна не поддерживаются", 0x1A: "ошибка протокола PVC-SETUP"}


def _pvc_setup(р: Разбор, у, м: int, край: int) -> str:
    д = р.д
    у.поле("LCN", "x25.lcn", u16(д, м) & 0x0FFF, м, 2)
    у.поле("Тип", "x25.type", "0xf5 (PVC Setup)", м + 2, 1, 0xF5)
    состояние, дл_от, дл_к = д[м + 4], д[м + 5], д[м + 8]
    у.поле("Версия PVC", "xot.pvc.version", д[м + 3], м + 3, 1)
    у.поле("Состояние", "xot.pvc.status", СОСТОЯНИЯ_PVC.get(состояние, f"0x{состояние:02x}"), м + 4, 1, состояние)
    у.поле("LCN инициатора", "xot.pvc.init_lcn", u16(д, м + 6), м + 6, 2)
    у.поле("LCN ответчика", "xot.pvc.resp_lcn", u16(д, м + 9), м + 9, 2)
    у.поле("Окно (вход/выход)", "xot.pvc.send_window", f"{д[м + 11]}/{д[м + 12]}", м + 11, 2)
    у.поле("Пакет (вход/выход)", "xot.pvc.send_pkt_size", f"2^{д[м + 13]}/2^{д[м + 14]}", м + 13, 2)
    x = м + 15
    от = д[x:x + дл_от].decode("latin-1")
    к = д[x + дл_от:x + дл_от + дл_к].decode("latin-1")
    у.поле("Интерфейс инициатора", "xot.pvc.init_itf_name", от, x, дл_от)
    у.поле("Интерфейс ответчика", "xot.pvc.resp_itf_name", к, x + дл_от, дл_к)
    return f"PVC Setup {от} → {к}: {СОСТОЯНИЯ_PVC.get(состояние, hex(состояние))}"


def xot(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    if конец - м < 7 or u16(д, м) != 0 or u16(д, м + 2) < 3:
        return False
    while м + 4 <= конец:
        версия, дл = u16(д, м), u16(д, м + 2)
        if версия != 0:
            данные(р, м, "XOT: не заголовок", конец)
            break
        у = р.уровень("XOT", "X.25 over TCP (RFC 1613)", м)
        у.поле("Версия", "xot.version", версия, м, 2)
        у.поле("Длина", "xot.length", дл, м + 2, 2)
        у.длина = 4
        край = min(м + 4 + дл, конец)
        if м + 4 + дл > конец:
            р.ошибка("XOT: пакет длиннее данных (продолжение — в следующем сегменте)")
        if край - м >= 19 and д[м + 6] == 0xF5:          # весь постоянный заголовок PVC Setup — 15 байт
            у.длина = край - м
            у.итог = _pvc_setup(р, у, м + 4, край)
        else:
            у.итог = f"пакет X.25, {дл} байт"
            if not x25(р, м + 4, край):
                данные(р, м + 4, "XOT: не X.25", край)
        if у.итог.startswith("PVC"):
            р.п.инфо = "XOT " + у.итог
        м += 4 + дл
    else:
        if м < конец:
            данные(р, м, "XOT: остаток короче заголовка", конец)
    return True


for _порт in (3002, 3003, 3006, 4222, 4249, 4250):
    prilozh.ПОРТЫ_TCP.setdefault(_порт, ipa)
prilozh.ПОРТЫ_TCP.setdefault(1998, xot)
prilozh.КАК["tcp"].setdefault("IPA", ipa)
prilozh.КАК["udp"].setdefault("IPA", ipa_udp)
prilozh.КАК["tcp"].setdefault("XOT", xot)
ДОП_УРОВНИ["IPA"] = "прикладной"
ДОП_УРОВНИ["XOT"] = "прикладной"
