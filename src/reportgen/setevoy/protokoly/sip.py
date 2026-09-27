"""SIP (RFC 3261): строка запроса или состояния, заголовки (и краткие формы), тело по Content-Length —
SDP, ISUP (SIP-I/SIP-T) и multipart.

Документы (структуры — строго по ним):

* RFC 3261, 7 (сообщение: стартовая строка, заголовки до пустой строки, тело), 7.3.1 (продолжение
  заголовка строкой с пробела или табуляции), 7.3.3 (краткие формы: i, m, e, l, c, f, s, k, t, v — и
  добавленные позже a, u, o, y, n, r, b, j, d, x по таблице Wireshark), 18.3 (по TCP границу сообщения
  задаёт Content-Length, в одном сегменте их может быть несколько; по UDP лишние октеты тела
  отбрасываются), 20 (заголовки), 25.1 (метод — token, версия — «SIP/2.0»), 21 (коды ответов).
* From/To/Contact/P-Asserted-Identity — name-addr или addr-spec (20.10, 20.20): отображаемое имя, URI
  в «<>», параметры (tag); пользователь — часть URI до «@» (19.1.1). Via (20.42): протокол/транспорт,
  sent-by, branch. CSeq (20.16): номер и метод. Authorization и …-Authenticate (22.4, RFC 7616):
  схема и параметры Digest — username, realm, nonce, uri. Reason (RFC 3326): Q.850;cause=N —
  причина Q.850 (Q.850 табл. 1).
* Тело: Content-Type (20.15) application/sdp — SDP (RFC 8866); application/isup — сообщение ISUP без
  CIC (ITU-T Q.1912.5, RFC 3204); multipart/mixed (RFC 2046, 5.1: граница из параметра boundary,
  у частей свои заголовки) — части по отдельности.
* Методы, имена заголовков и коды ответов — таблицы Wireshark packet-sip.c (методы, sip_headers,
  sip_response_code_vals).
"""

from __future__ import annotations

import re

from .. import prilozh
from ..pole import печатное
from ..razbor import ДОП_УРОВНИ, Разбор
from . import oks7, promyshlennye

#: Таблицы — из Wireshark packet-sip.c (методы, sip_headers, sip_response_code_vals), собраны программой.
МЕТОДЫ = frozenset({'ACK', 'BYE', 'CANCEL', 'DO', 'INFO', 'INVITE', 'MESSAGE', 'NOTIFY', 'OPTIONS', 'PRACK',
          'QAUTH', 'REFER', 'REGISTER', 'SPRACK', 'SUBSCRIBE', 'UPDATE', 'PUBLISH'})
ЗАГОЛОВКИ = ('Unknown-header', 'Accept', 'Accept-Contact', 'Accept-Encoding', 'Accept-Language',
             'Accept-Resource-Priority', 'Additional-Identity', 'Alert-Info', 'Allow', 'Allow-Events',
             'Answer-Mode', 'Attestation-Info', 'Authentication-Info', 'Authorization', 'Call-ID', 'Call-Info',
             'Cellular-Network-Info', 'Contact', 'Content-Disposition', 'Content-Encoding', 'Content-Language',
             'Content-Length', 'Content-Type', 'CSeq', 'Date', 'Error-Info', 'Event', 'Expires', 'Feature-Caps',
             'Flow-Timer', 'From', 'Geolocation', 'Geolocation-Error', 'Geolocation-Routing', 'History-Info',
             'Identity', 'Identity-Info', 'Info-Package', 'In-Reply-To', 'Join', 'Max-Breadth', 'Max-Forwards',
             'MIME-Version', 'Min-Expires', 'Min-SE', 'Organization', 'Origination-Id', 'P-Access-Network-Info',
             'P-Answer-State', 'P-Asserted-Identity', 'P-Asserted-Service', 'P-Associated-URI', 'P-Called-Party-ID',
             'P-Charge-Info', 'P-Charging-Function-Addresses', 'P-Charging-Vector', 'P-DCS-Trace-Party-ID',
             'P-DCS-OSPS', 'P-DCS-Billing-Info', 'P-DCS-LAES', 'P-DCS-Redirect', 'P-Early-Media',
             'P-Media-Authorization', 'P-Preferred-Identity', 'P-Preferred-Service', 'P-Profile-Key',
             'P-Refused-URI-List', 'P-Served-User', 'P-User-Database', 'P-Visited-Network-ID', 'Path',
             'Permission-Missing', 'Policy-Contact', 'Policy-ID', 'Priority', 'Priority-Share', 'Priv-Answer-Mode',
             'Privacy', 'Proxy-Authenticate', 'Proxy-Authorization', 'Proxy-Require', 'RAck', 'Reason',
             'Reason-Phrase', 'Record-Route', 'Recv-Info', 'Refer-Sub', 'Refer-To', 'Referred-By', 'Reject-Contact',
             'Relayed-Charge', 'Replaces', 'Reply-To', 'Request-Disposition', 'Require', 'Resource-Priority',
             'Resource-Share', 'Response-Source', 'Restoration-Info', 'Retry-After', 'Route', 'RSeq',
             'Security-Client', 'Security-Server', 'Security-Verify', 'Server', 'Service-Interact-Info',
             'Service-Route', 'Session-Expires', 'Session-ID', 'SIP-ETag', 'SIP-If-Match', 'Subject',
             'Subscription-State', 'Supported', 'Suppress-If-Match', 'Target-Dialog', 'Timestamp', 'To',
             'Trigger-Consent', 'Unsupported', 'User-Agent', 'Via', 'Warning', 'WWW-Authenticate', 'Diversion',
             'User-to-User')
СОКРАЩЕНИЯ = {'a': 'Accept-Contact', 'b': 'Referred-By', 'c': 'Content-Type', 'd': 'Request-Disposition',
              'e': 'Content-Encoding', 'f': 'From', 'i': 'Call-ID', 'j': 'Reject-Contact', 'k': 'Supported',
              'l': 'Content-Length', 'm': 'Contact', 'n': 'Identity-Info', 'o': 'Event', 'r': 'Refer-To',
              's': 'Subject', 't': 'To', 'u': 'Allow-Events', 'v': 'Via', 'x': 'Session-Expires', 'y': 'Identity'}
КОДЫ = {100: 'Trying', 180: 'Ringing', 181: 'Call Is Being Forwarded', 182: 'Queued', 183: 'Session Progress',
        199: 'Informational - Others', 200: 'OK', 202: 'Accepted', 204: 'No Notification', 299: 'Success - Others',
        300: 'Multiple Choices', 301: 'Moved Permanently', 302: 'Moved Temporarily', 305: 'Use Proxy',
        380: 'Alternative Service', 399: 'Redirection - Others', 400: 'Bad Request', 401: 'Unauthorized',
        402: 'Payment Required', 403: 'Forbidden', 404: 'Not Found', 405: 'Method Not Allowed',
        406: 'Not Acceptable', 407: 'Proxy Authentication Required', 408: 'Request Timeout', 410: 'Gone',
        412: 'Conditional Request Failed', 413: 'Request Entity Too Large', 414: 'Request-URI Too Long',
        415: 'Unsupported Media Type', 416: 'Unsupported URI Scheme', 420: 'Bad Extension',
        421: 'Extension Required', 422: 'Session Timer Too Small', 423: 'Interval Too Brief',
        428: 'Use Identity Header', 429: 'Provide Referrer Identity', 430: 'Flow Failed',
        433: 'Anonymity Disallowed', 436: 'Bad Identity-Info', 437: 'Unsupported Certificate',
        438: 'Invalid Identity Header', 439: 'First Hop Lacks Outbound Support', 440: 'Max-Breadth Exceeded',
        470: 'Consent Needed', 480: 'Temporarily Unavailable', 481: 'Call/Transaction Does Not Exist',
        482: 'Loop Detected', 483: 'Too Many Hops', 484: 'Address Incomplete', 485: 'Ambiguous', 486: 'Busy Here',
        487: 'Request Terminated', 488: 'Not Acceptable Here', 489: 'Bad Event', 491: 'Request Pending',
        493: 'Undecipherable', 494: 'Security Agreement Required', 499: 'Client Error - Others',
        500: 'Server Internal Error', 501: 'Not Implemented', 502: 'Bad Gateway', 503: 'Service Unavailable',
        504: 'Server Time-out', 505: 'Version Not Supported', 513: 'Message Too Large',
        599: 'Server Error - Others', 600: 'Busy Everywhere', 603: 'Decline', 604: 'Does Not Exist Anywhere',
        606: 'Not Acceptable', 607: 'Unwanted', 608: 'Rejected', 699: 'Global Failure - Others',
        999: 'Unknown response'}

_КАНОН = {имя.lower(): имя for имя in ЗАГОЛОВКИ}
_ЗАПРОС = re.compile(rb"([A-Za-z][A-Za-z0-9.!%*_+`'~-]*) (\S+) SIP/2\.0")
_ОТВЕТ = re.compile(rb"SIP/2\.0 ([1-6]\d\d) ([^\r\n]*)")
_ПАРАМЕТР = re.compile(r'([\w.!%*+`\'~-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^\s,;]+)')
_ПОЛЬЗОВАТЕЛЬ = re.compile(r"^(?:sips?|tel):([^@;>]+)@?", re.IGNORECASE)
ВЫЗОВЫ = "sip-вызовы"                  # общий словарь захвата: Call-ID → [номер первого пакета, от, к]


def _строки(д: bytes, м: int, конец: int):
    """Строки заголовка (с продолжениями, 7.3.1) до пустой строки: [(место, октеты)], место тела или None."""
    итог, место = [], м
    while место < конец:
        перевод = д.find(b"\n", место, конец)
        if перевод < 0:
            return None, None
        строка = д[место:перевод]
        if строка.endswith(b"\r"):
            строка = строка[:-1]
        if not строка:
            return итог, перевод + 1
        if строка[:1] in (b" ", b"\t") and итог:
            начало, прежняя = итог[-1]
            итог[-1] = (начало, прежняя + b" " + строка.strip())
        else:
            итог.append((место, строка))
        место = перевод + 1
    return None, None


def _адрес(значение: str) -> tuple[str, str, dict]:
    """name-addr / addr-spec: (отображаемое имя, URI, параметры заголовка)."""
    имя, uri, хвост = "", значение.strip(), ""
    if "<" in значение and ">" in значение.split("<", 1)[1]:
        имя = значение.split("<", 1)[0].strip().strip('"')
        uri, хвост = значение.split("<", 1)[1].split(">", 1)
    elif ";" in значение:
        uri, хвост = значение.split(";", 1)
        хвост = ";" + хвост
    параметры = {к.lower(): з.strip('"') for к, з in _ПАРАМЕТР.findall(хвост)}
    return имя, uri.strip(), параметры


def _пользователь(uri: str) -> str:
    сов = _ПОЛЬЗОВАТЕЛЬ.match(uri)
    return сов.group(1) if сов else uri


def _разбор_заголовка(у, имя: str, значение: str, место: int, длина: int, сводка: dict) -> None:
    """Главное из заголовка — дочерними полями (с местом всей строки: значение могло быть продолжено)."""
    п = у.поле(f"{имя}: {значение}", f"sip.{имя}", значение, место, длина)
    ключ = имя.lower()
    if ключ in ("from", "to", "contact", "p-asserted-identity", "refer-to", "referred-by", "diversion"):
        отображаемое, uri, параметры = _адрес(значение.split(",")[0] if ключ == "contact" else значение)
        кратко = {"from": "from", "to": "to", "contact": "contact"}.get(ключ, ключ.replace("-", "_"))
        у.поле("URI", f"sip.{кратко}.addr", uri, место, длина, родитель=п)
        у.поле("Пользователь", f"sip.{кратко}.user", _пользователь(uri), место, длина, родитель=п)
        if отображаемое:
            у.поле("Отображаемое имя", f"sip.{кратко}.display.info", отображаемое, место, длина, родитель=п)
        if "tag" in параметры:
            у.поле("Метка", f"sip.{кратко}.tag", параметры["tag"], место, длина, родитель=п)
        сводка.setdefault(ключ, (отображаемое, _пользователь(uri)))
    elif ключ == "cseq":
        части = значение.split()
        if len(части) == 2 and части[0].isdigit():
            у.поле("Номер", "sip.CSeq.seq", int(части[0]), место, длина, родитель=п)
            у.поле("Метод", "sip.CSeq.method", части[1], место, длина, родитель=п)
            сводка["cseq"] = части[1]
    elif ключ == "via":
        первый = значение.split(",")[0].strip()
        части = первый.split(None, 1)
        if len(части) == 2 and части[0].upper().startswith("SIP/2.0/"):
            у.поле("Транспорт", "sip.Via.transport", части[0].split("/")[2], место, длина, родитель=п)
            у.поле("Отправитель", "sip.Via.sent-by.address", части[1].split(";")[0].strip(), место, длина,
                   родитель=п)
            параметры = {к.lower(): з for к, з in _ПАРАМЕТР.findall(части[1])}
            if "branch" in параметры:
                у.поле("Ветвь", "sip.Via.branch", параметры["branch"], место, длина, родитель=п)
    elif ключ in ("authorization", "proxy-authorization", "www-authenticate", "proxy-authenticate"):
        схема = значение.split(None, 1)[0] if значение else ""
        у.поле("Схема", "sip.auth.scheme", схема, место, длина, родитель=п)
        for к, з in _ПАРАМЕТР.findall(значение[len(схема):]):
            if к.lower() in ("username", "realm", "nonce", "uri", "algorithm", "qop"):
                у.поле(к, f"sip.auth.{к.lower()}", з.strip('"'), место, длина, родитель=п)
                if к.lower() == "username":
                    сводка["username"] = з.strip('"')
    elif ключ == "reason":
        сов = re.search(r"Q\.850\s*;.*?cause\s*=\s*(\d+)", значение, re.IGNORECASE)
        if сов:
            причина = int(сов.group(1))
            текст = f"{причина} ({oks7.ПРИЧИНЫ.get(причина, 'неизвестная')})"
            у.поле("Причина Q.850", "sip.reason_cause_q850", текст, место, длина, причина, родитель=п)
            сводка["причина"] = текст
    elif ключ in ("user-agent", "server"):
        сводка.setdefault("программа", значение)


def _тело(р: Разбор, у, тип: str, м: int, конец: int, итог: list) -> None:
    """Тело по Content-Type: SDP, ISUP без CIC, части multipart; прочее — полем в SIP."""
    вид = тип.split(";")[0].strip().lower()
    уровней = len(р.п.уровни)
    инфо = р.п.инфо
    try:
        if вид == "application/sdp" and promyshlennye.sdp(р, м, конец):
            итог.append("SDP")
            return
        if вид == "application/isup" and oks7.isup(р, м, конец, cic=False):
            итог.append("ISUP")
            return
    except (IndexError, ValueError):
        pass
    del р.п.уровни[уровней:]
    р.п.инфо = инфо
    if вид.startswith("multipart/"):
        граница = {к.lower(): з.strip('"') for к, з in _ПАРАМЕТР.findall(тип)}.get("boundary")
        if граница:
            _части(р, у, граница.encode("latin-1"), м, конец, итог)
            return
    у.поле("Тело", "sip.msg_body", f"{конец - м} байт: {печатное(р.д[м:конец], 60)}", м, конец - м)


def _части(р: Разбор, у, граница: bytes, м: int, конец: int, итог: list) -> None:
    """multipart (RFC 2046, 5.1.1): «--граница» в начале строки, у части — заголовки, пустая строка, тело."""
    д = р.д
    разделитель = b"--" + граница
    места = [н for н in (д.find(разделитель, м, конец),) if н >= 0]
    while места and (следующий := д.find(b"\r\n" + разделитель, места[-1] + len(разделитель), конец)) >= 0:
        места.append(следующий + 2)
    for начало, край in zip(места, места[1:], strict=False):
        заголовок_конец = д.find(b"\n", начало, край)
        строки, тело = _строки(д, заголовок_конец + 1, край) if заголовок_конец >= 0 else (None, None)
        if строки is None:
            continue
        тип = ""
        for _, строка in строки:
            имя, _, значение = строка.decode("utf-8", "replace").partition(":")
            if имя.strip().lower() in ("content-type", "c"):
                тип = значение.strip()
        у.поле(f"Часть: {тип or 'без типа'}", "sip.multipart.part", тип, начало, край - начало)
        конец_тела = край - 2 if д[край - 2:край] == b"\r\n" else край
        _тело(р, у, тип, тело, конец_тела, итог)


def _сообщение(р: Разбор, м: int, конец: int):
    """Одно сообщение SIP с м: конец сообщения или None, если это не SIP."""
    д = р.д
    перевод = д.find(b"\n", м, конец)
    первая = д[м:перевод if перевод >= 0 else конец].rstrip(b"\r")
    запрос, ответ = _ЗАПРОС.fullmatch(первая), _ОТВЕТ.fullmatch(первая)
    if not запрос and not ответ:
        return None
    строки, тело = _строки(д, перевод + 1, конец) if перевод >= 0 else (None, None)
    if строки is None:
        return None
    заголовки = []
    for место, строка in строки:
        имя, двоеточие, значение = строка.decode("utf-8", "replace").partition(":")
        имя = имя.strip()
        if not двоеточие or not имя or " " in имя:
            return None
        имя = СОКРАЩЕНИЯ.get(имя, имя) if len(имя) == 1 else имя
        заголовки.append((_КАНОН.get(имя.lower(), имя), значение.strip(), место, len(строка)))
    у = р.уровень("SIP", "Session Initiation Protocol", м)
    if запрос:
        метод, uri = запрос.group(1).decode(), запрос.group(2).decode("utf-8", "replace")
        у.поле("Строка запроса", "sip.Request-Line", первая.decode("utf-8", "replace"), м, len(первая))
        у.поле("Метод", "sip.Method", метод, м, len(запрос.group(1)))
        у.поле("URI запроса", "sip.r-uri", uri, м + запрос.start(2), len(запрос.group(2)))
        if метод not in МЕТОДЫ:
            у.поле("Метод вне RFC 3261 и расширений", "sip.unknown_method", метод, м, len(запрос.group(1)))
    else:
        код = int(ответ.group(1))
        у.поле("Строка состояния", "sip.Status-Line", первая.decode("utf-8", "replace"), м, len(первая))
        у.поле("Код ответа", "sip.Status-Code", f"{код} ({КОДЫ.get(код, 'неизвестный')})", м + 8, 3, код)
    сводка: dict = {}
    длина_тела, тип = None, ""
    for имя, значение, место, длина in заголовки:
        _разбор_заголовка(у, имя, значение, место, длина, сводка)
        if имя == "Content-Length" and значение.isdigit():
            длина_тела = int(значение)
        elif имя == "Content-Type":
            тип = значение
        elif имя == "Call-ID":
            сводка.setdefault("call-id", значение)
    if длина_тела is None:
        край = конец
    elif тело + длина_тела > конец:
        р.ошибка(f"SIP: тело короче Content-Length ({конец - тело} из {длина_тела})")
        край = конец
    else:
        край = тело + длина_тела
    итог_тела: list = []
    if тело < край:
        _тело(р, у, тип, тело, край, итог_тела)
    у.длина = край - м
    # Сводка: запрос — метод, URI, стороны; ответ — код, текст и метод из CSeq.
    if запрос:
        итог = f"{метод} {uri}"
        стороны = [сводка[к][1] for к in ("from", "to") if к in сводка]
        if len(стороны) == 2:
            итог += f" ({стороны[0]} → {стороны[1]})"
    else:
        итог = f"{код} {ответ.group(2).decode('utf-8', 'replace')}" + (f" ({сводка['cseq']})" if "cseq" in сводка else "")
    if "причина" in сводка:
        итог += f", причина {сводка['причина']}"
    if "username" in сводка:
        итог += f", пользователь {сводка['username']}"
    у.итог = итог + "".join(f" (с {ч})" for ч in итог_тела)
    if "call-id" in сводка and р.шаблоны is not None:
        вызовы = р.шаблоны.setdefault(ВЫЗОВЫ, {})
        вызовы.setdefault(сводка["call-id"], [р.п.номер, сводка.get("from", ("", ""))[1], сводка.get("to", ("", ""))[1]])
    return край


def sip(р: Разбор, м: int, конец: int, tcp: bool = False) -> bool:
    """Сообщения SIP с м; по TCP — подряд, по Content-Length (18.3); по UDP лишнее после тела отброшено."""
    край = _сообщение(р, м, конец)
    if край is None:
        return False
    while tcp and край < конец and (следующий := _сообщение(р, край, конец)) is not None:
        край = следующий
    уровни = [у for у in р.п.уровни if у.протокол == "SIP"]
    if край < конец:
        уровни[-1].поле("После сообщения (не SIP)" if tcp else "После тела (отброшено, RFC 3261 18.3)",
                        "sip.extra", f"{конец - край} байт", край, конец - край)
    р.п.инфо = "SIP " + "; ".join(у.итог for у in уровни)
    return True


def sip_tcp(р: Разбор, м: int, конец: int) -> bool:
    return sip(р, м, конец, tcp=True)


prilozh.ПОРТЫ_UDP[5060] = sip
prilozh.ПОРТЫ_TCP[5060] = sip_tcp
prilozh.КАК["udp"]["SIP"] = sip
prilozh.КАК["tcp"]["SIP"] = sip_tcp
ДОП_УРОВНИ["SIP"] = "прикладной"
