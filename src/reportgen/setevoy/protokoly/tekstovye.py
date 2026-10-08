"""Текстовые службы TCP подробно: SSH, FTP с каналами данных, SMTP, POP3, IMAP и переход на TLS.

Документы (структуры — строго по ним):

* SSH — RFC 4253: 4.2 (строка версии «SSH-протокол-программа комментарий»), 6 (двоичный пакет:
  длина, длина добивки ≥ 4, нагрузка, добивка; длина всего пакета кратна 8 — пока нет шифра),
  7.1 (SSH_MSG_KEXINIT: cookie 16 байт, десять списков имён, first_kex_packet_follows, резерв),
  11.1 (DISCONNECT: код причины, описание), 10 (SERVICE_REQUEST/ACCEPT); RFC 4250, 4.1 — номера
  сообщений, 4.2.2 — коды причин разрыва; RFC 8308 — EXT_INFO. Отпечаток HASSH (Salesforce, как у
  Wireshark packet-ssh.c): MD5 от «kex;шифры;MAC;сжатие» своего направления — клиента или сервера.
* FTP — RFC 959, 4.1 (команды), 4.2 (ответы: три цифры, пробел или дефис), PORT и ответ 227
  (h1,h2,h3,h4,p1,p2: порт = p1·256 + p2); RFC 2428 — EPRT |в|адрес|порт| и ответ 229 (|||порт|);
  RFC 4217 — AUTH TLS/SSL. Канал данных, объявленный в PORT/PASV/EPRT/EPSV, запоминается для захвата
  (общий словарь ``р.шаблоны``, с номером пакета объявления), и пакеты на этот адрес и порт
  показываются как FTP-DATA с командой, ради которой канал открыт (RETR, STOR, LIST …).
* SMTP — RFC 5321, 4.1 (команды), 4.2 (ответы); RFC 3207 — STARTTLS; RFC 4954 и 4616 — AUTH PLAIN
  (base64 «authzid NUL authcid NUL пароль»); заголовки письма — RFC 5322, 3.6.
* POP3 — RFC 1939 (USER, PASS, RETR, ответы +OK/-ERR); RFC 2595 — STLS.
* IMAP — RFC 9051 (метка, команда: LOGIN, AUTHENTICATE, SELECT …; ответы с меткой и «*»);
  RFC 2595 — STARTTLS.
После STARTTLS/STLS/AUTH TLS и ответа сервера записи TLS в том же соединении узнаются общим
разбором TLS — здесь отмечается сам переход.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import re

from .. import obshee, prilozh
from ..pole import u32
from ..prilozh import строковый
from ..razbor import ДОП_УРОВНИ, Разбор

# -- общее --------------------------------------------------------------------------------


def _порты(р: Разбор):
    """Порты источника и получателя последнего уровня TCP."""
    for у in reversed(р.п.уровни):
        if у.протокол == "TCP":
            ф = {п.ключ: п.сырое for п in у.поля}
            return ф.get("tcp.srcport"), ф.get("tcp.dstport")
    return None, None


def _строки(р: Разбор, м: int, конец: int):
    """(место, строка без CR LF) для каждой строки данных."""
    место = м
    for строка in р.д[м:конец].split(b"\n"):
        yield место, строка.rstrip(b"\r").decode("utf-8", "replace")
        место += len(строка) + 1


# -- SSH ----------------------------------------------------------------------------------

SSH_СООБЩЕНИЯ = {1: "Disconnect", 2: "Ignore", 3: "Unimplemented", 4: "Debug", 5: "Service Request",
                 6: "Service Accept", 7: "Extension Information", 8: "New Compression", 20: "Key Exchange Init",
                 21: "New Keys", 30: "Key Exchange Init (DH/ECDH)", 31: "Key Exchange Reply (DH/ECDH)",
                 32: "DH Group Exchange Init", 33: "DH Group Exchange Reply", 34: "DH Group Exchange Request",
                 50: "User Authentication Request", 51: "User Authentication Failure",
                 52: "User Authentication Success", 53: "User Authentication Banner", 60: "Public Key OK",
                 80: "Global Request", 81: "Request Success", 82: "Request Failure", 90: "Channel Open",
                 91: "Channel Open Confirmation", 92: "Channel Open Failure", 93: "Window Adjust",
                 94: "Channel Data", 95: "Channel Extended Data", 96: "Channel EOF", 97: "Channel Close",
                 98: "Channel Request", 99: "Channel Success", 100: "Channel Failure"}
SSH_РАЗРЫВ = {1: "HOST_NOT_ALLOWED_TO_CONNECT", 2: "PROTOCOL_ERROR", 3: "KEY_EXCHANGE_FAILED", 4: "RESERVED",
              5: "MAC_ERROR", 6: "COMPRESSION_ERROR", 7: "SERVICE_NOT_AVAILABLE",
              8: "PROTOCOL_VERSION_NOT_SUPPORTED", 9: "HOST_KEY_NOT_VERIFIABLE", 10: "CONNECTION_LOST",
              11: "BY_APPLICATION", 12: "TOO_MANY_CONNECTIONS", 13: "AUTH_CANCELLED_BY_USER",
              14: "NO_MORE_AUTH_METHODS_AVAILABLE", 15: "ILLEGAL_USER_NAME"}
#: Списки имён SSH_MSG_KEXINIT (RFC 4253, 7.1) — по порядку; ключи — как в Wireshark.
KEXINIT = (("Обмен ключами", "ssh.kex_algorithms"), ("Ключ сервера", "ssh.server_host_key_algorithms"),
           ("Шифры клиент→сервер", "ssh.encryption_algorithms_client_to_server"),
           ("Шифры сервер→клиент", "ssh.encryption_algorithms_server_to_client"),
           ("MAC клиент→сервер", "ssh.mac_algorithms_client_to_server"),
           ("MAC сервер→клиент", "ssh.mac_algorithms_server_to_client"),
           ("Сжатие клиент→сервер", "ssh.compression_algorithms_client_to_server"),
           ("Сжатие сервер→клиент", "ssh.compression_algorithms_server_to_client"),
           ("Языки клиент→сервер", "ssh.languages_client_to_server"),
           ("Языки сервер→клиент", "ssh.languages_server_to_client"))


def _ssh_строка(д: bytes, x: int, край: int):
    """string SSH (RFC 4251, 5): длина uint32 и байты; None — не помещается."""
    if x + 4 > край:
        return None
    дл = u32(д, x)
    if x + 4 + дл > край:
        return None
    return д[x + 4:x + 4 + дл], x + 4 + дл


def _клиент(р: Разбор) -> bool:
    """Отправитель — клиент SSH: пишет на порт 22 (или, без 22, с большего порта)."""
    от, к = _порты(р)
    if к == 22 or от == 22:
        return к == 22
    return (от or 0) > (к or 0)


def _kexinit(р: Разбор, у, п, x: int, край: int) -> str:
    д = р.д
    у.поле("Cookie", "ssh.cookie", д[x:x + 16].hex(), x, 16, родитель=п)
    x += 16
    списки = []
    for имя, ключ in KEXINIT:
        с = _ssh_строка(д, x, край)
        if с is None:
            р.ошибка("SSH: KEXINIT оборван")
            return "KEXINIT (оборван)"
        значение = с[0].decode("latin-1")
        у.поле(имя, ключ, значение, x, с[1] - x, родитель=п)
        списки.append(значение)
        x = с[1]
    if x + 5 <= край:
        у.поле("Следом — первый пакет обмена", "ssh.first_kex_packet_follows", д[x], x, 1, родитель=п)
    клиент = _клиент(р)
    свои = (списки[0], списки[2], списки[4], списки[6]) if клиент else (списки[0], списки[3], списки[5], списки[7])
    строка = ";".join(свои)
    отпечаток = hashlib.md5(строка.encode("latin-1"), usedforsecurity=False).hexdigest()
    ключ = "ssh.kex.hassh" if клиент else "ssh.kex.hasshserver"
    у.поле("HASSH (алгоритмы)" if клиент else "HASSH сервера (алгоритмы)", ключ + "_algorithms", строка, x, 0,
           родитель=п)
    у.поле("HASSH" if клиент else "HASSH сервера", ключ, отпечаток, x, 0, родитель=п)
    return f"KEXINIT ({'клиент' if клиент else 'сервер'}, HASSH {отпечаток})"


def _правдоподобен(д: bytes, x: int, конец: int) -> bool:
    """Незашифрованный двоичный пакет (RFC 4253, 6 и 6.1): добивка ≥ 4 и меньше длины, весь пакет
    (с полем длины) кратен 8 и не больше 35000 байт, известный номер сообщения."""
    if x + 6 > конец:
        return False
    дл, добивка, код = u32(д, x), д[x + 4], д[x + 5]
    return 4 <= добивка < дл and 4 + дл <= 35000 and (4 + дл) % 8 == 0 and код in SSH_СООБЩЕНИЯ


def _ssh_пакеты(р: Разбор, у, x: int, конец: int) -> list:
    """Двоичные пакеты без шифра (RFC 4253, 6); первый неправдоподобный — начало шифрования."""
    д, итоги = р.д, []
    while x < конец:
        if not _правдоподобен(д, x, конец):
            у.поле("Зашифровано", "ssh.encrypted_packet", f"{конец - x} байт", x, конец - x)
            итоги.append("зашифрованные данные")
            break
        дл, добивка, код = u32(д, x), д[x + 4], д[x + 5]
        край = min(x + 4 + дл - добивка, конец)
        п = у.поле(f"Пакет: {SSH_СООБЩЕНИЯ[код]}", "ssh.message_code", код, x, min(4 + дл, конец - x))
        у.поле("Длина пакета", "ssh.packet_length", дл, x, 4, родитель=п)
        у.поле("Длина добивки", "ssh.padding_length", добивка, x + 4, 1, родитель=п)
        т = x + 6
        итог = SSH_СООБЩЕНИЯ[код]
        if код == 20:
            итог = _kexinit(р, у, п, т, край)
        elif код == 1 and т + 4 <= край:
            причина = u32(д, т)
            у.поле("Причина", "ssh.disconnect_reason", SSH_РАЗРЫВ.get(причина, причина), т, 4, причина, родитель=п)
            с = _ssh_строка(д, т + 4, край)
            if с is not None:
                у.поле("Описание", "ssh.disconnect_description", с[0].decode("utf-8", "replace"), т + 4,
                       с[1] - т - 4, родитель=п)
            итог = f"Disconnect: {SSH_РАЗРЫВ.get(причина, причина)}"
        elif код in (5, 6):
            с = _ssh_строка(д, т, край)
            if с is not None:
                служба = с[0].decode("latin-1")
                у.поле("Служба", "ssh.service_name", служба, т, с[1] - т, родитель=п)
                итог = f"{SSH_СООБЩЕНИЯ[код]}: {служба}"
        elif код == 31:
            с = _ssh_строка(д, т, край)
            if с is not None:
                вид = _ssh_строка(с[0], 0, len(с[0]))
                if вид is not None:
                    у.поле("Вид ключа сервера", "ssh.host_key.type", вид[0].decode("latin-1"), т + 8,
                           len(вид[0]), родитель=п)
                    итог += f": ключ {вид[0].decode('latin-1')}"
        elif код == 21:
            итог = "New Keys — дальше шифрование"
        итоги.append(итог)
        x += 4 + дл
    return итоги


def ssh(р: Разбор, м: int, конец: int) -> bool:
    д = р.д
    итоги = []
    if д[м:м + 4] == b"SSH-":
        строка = д[м:конец].partition(b"\n")[0]
        текст = строка.rstrip(b"\r").decode("latin-1")
        части = re.match(r"SSH-([^-\s]+)-(\S+)(?: (.*))?$", текст)
        if части is None:
            return False
        у = р.уровень("SSH", "Secure Shell", м)
        п = у.поле("Строка версии", "ssh.protocol", текст, м, len(строка))
        у.поле("Версия протокола", "ssh.protoversion", части.group(1), м + 4, len(части.group(1)), родитель=п)
        у.поле("Программа", "ssh.softwareversion", части.group(2), м + 5 + len(части.group(1)),
               len(части.group(2)), родитель=п)
        if части.group(3):
            у.поле("Комментарий", "ssh.comments", части.group(3), м + len(текст) - len(части.group(3)),
                   len(части.group(3)), родитель=п)
        итоги.append(f"версия {части.group(1)}, {части.group(2)}")
        x = м + len(строка) + 1
    else:
        # Без строки версии — незашифрованный пакет, а на порту 22 — и зашифрованный.
        if not _правдоподобен(д, м, конец) and 22 not in _порты(р):
            return False
        у = р.уровень("SSH", "Secure Shell", м)
        x = м
    итоги += _ssh_пакеты(р, у, x, конец)
    у.длина = конец - м
    у.итог = "; ".join(итоги)
    р.п.инфо = "SSH " + у.итог
    return True


def ssh_признаки(р: Разбор, м: int, конец: int) -> bool:
    return р.д[м:м + 8] in (b"SSH-2.0-", b"SSH-1.99") and ssh(р, м, конец)


# -- FTP ----------------------------------------------------------------------------------

КАНАЛЫ = obshee.КАНАЛЫ_FTP      # ключ общего словаря захвата: «адрес|порт» → [номер пакета, описание]
ПОСЛЕДНИЙ = obshee.ПОСЛЕДНИЙ_FTP
ПЕРЕДАЧА = ("RETR", "STOR", "STOU", "APPE", "LIST", "NLST", "MLSD")


def _канал(р: Разбор, у, адрес: str, порт: int, откуда: str, место: int, длина: int) -> None:
    ключ = f"{адрес}|{порт}"
    obshee.сделать(р, "ftp+", ключ, р.п.номер, откуда)
    у.поле("Канал данных", "ftp.data_channel", f"{адрес}:{порт} ({откуда})", место, длина, ключ)


def _адрес_и_порт(числа: str):
    """«h1,h2,h3,h4,p1,p2» → (адрес, порт) или None."""
    ч = [int(x) for x in re.findall(r"\d+", числа)]
    if len(ч) != 6 or any(x > 255 for x in ч):
        return None
    return ".".join(map(str, ч[:4])), ч[4] * 256 + ч[5]


def _eprt(арг: str):
    """EPRT |1|адрес IPv4|порт| или |2|адрес IPv6|порт| (RFC 2428, 2: разделитель и после порта)
    → (адрес как у разбора IP, порт)."""
    части = арг[1:].split(арг[:1]) if арг else []
    if len(части) != 4 or части[3] or части[0] not in ("1", "2") or not части[2].isdigit():
        return None
    try:
        адрес = ipaddress.ip_address(части[1])
    except ValueError:
        return None
    if адрес.version != {"1": 4, "2": 6}[части[0]]:
        return None
    return str(адрес), int(части[2])


def ftp(р: Разбор, м: int, конец: int) -> bool:
    if not строковый(р, м, конец, "FTP"):
        return False
    у = р.п.уровни[-1]
    сводка = []
    for место, строка in _строки(р, м, конец):
        ответ = re.match(r"(\d{3})([ -])(.*)", строка)
        if ответ:
            код, текст = int(ответ.group(1)), ответ.group(3)
            у.поле("Код ответа", "ftp.response.code", код, место, 3)
            у.поле("Текст ответа", "ftp.response.arg", текст, место + 4, len(текст))
            сводка.append(f"{код} {текст}")
            if код == 227:
                числа = re.search(r"\d+,\d+,\d+,\d+,\d+,\d+", текст)
                пара = _адрес_и_порт(числа.group(0)) if числа else None
                if пара:
                    _канал(р, у, пара[0], пара[1], "PASV", место, len(строка))
            elif код == 229:
                порт = re.search(r"\((.)\1\1(\d+)\1\)", текст)
                if порт and р.п.источник:
                    _канал(р, у, р.п.источник, int(порт.group(2)), "EPSV", место, len(строка))
            continue
        команда = re.match(r"([A-Za-z]{3,4})(?: (.*))?$", строка)
        if not команда:
            continue
        имя, арг = команда.group(1).upper(), команда.group(2) or ""
        у.поле("Команда", "ftp.request.command", имя, место, len(команда.group(1)))
        if арг:
            у.поле("Аргумент", "ftp.request.arg", арг, место + len(имя) + 1, len(арг))
        сводка.append(f"{имя} {арг}".strip() if имя != "PASS" else "PASS ***")
        if имя == "USER":
            у.поле("Пользователь", "ftp.user", арг, место + 5, len(арг))
        elif имя == "PASS":
            у.поле("Пароль", "ftp.password", арг, место + 5, len(арг))
        elif имя == "PORT":
            пара = _адрес_и_порт(арг)
            if пара:
                _канал(р, у, пара[0], пара[1], "PORT", место, len(строка))
        elif имя == "EPRT":
            адрес = _eprt(арг)
            if адрес:
                _канал(р, у, адрес[0], адрес[1], "EPRT", место, len(строка))
        elif имя == "AUTH" and арг.upper() in ("TLS", "SSL", "TLS-C", "TLS-P"):
            у.поле("Переход на TLS", "ftp.starttls", f"AUTH {арг.upper()} (RFC 4217)", место, len(строка))
        elif имя in ПЕРЕДАЧА:
            obshee.сделать(р, "ftp=", f"{имя} {арг}".strip())
    if сводка:
        у.итог = "; ".join(сводка)[:120]
        р.п.инфо = "FTP " + у.итог
    return True


def ftp_data(р: Разбор, м: int, конец: int) -> bool:
    """Пакет на адрес и порт, объявленные раньше в PORT/PASV/EPRT/EPSV, — канал данных FTP."""
    if not р.шаблоны.get(КАНАЛЫ) and type(р.шаблоны) is not obshee.Журнал:
        return False                                    # каналов в захвате нет (пустой нагрузки TCP сюда не отдаёт)
    от, к = _порты(р)
    for адрес, порт in ((р.п.получатель, к), (р.п.источник, от)):
        назначение = obshee.сделать(р, "ftp?", f"{адрес}|{порт}", р.п.номер)
        if назначение is not None:
            у = р.уровень("FTP-DATA", "FTP Data", м)
            у.поле("Канал", "ftp-data.channel", f"{адрес}:{порт}", м, 0)
            у.поле("Назначение", "ftp-data.command", назначение, м, 0)
            у.поле("Данные", "ftp-data.data", f"{конец - м} байт", м, конец - м)
            у.длина = конец - м
            у.итог = f"{назначение}, {конец - м} байт"
            р.п.инфо = "FTP-DATA " + у.итог
            return True
    return False


# -- почта --------------------------------------------------------------------------------

ЗАГОЛОВКИ_ПИСЬМА = ("From", "To", "Cc", "Subject", "Date", "Message-ID")


def _auth_plain(значение: str):
    """AUTH PLAIN (RFC 4616): base64 «authzid NUL authcid NUL пароль» → (пользователь, пароль)."""
    try:
        части = base64.b64decode(значение, validate=True).split(b"\x00")
    except (binascii.Error, ValueError):
        return None
    if len(части) != 3:
        return None
    return части[1].decode("utf-8", "replace"), части[2].decode("utf-8", "replace")


def smtp(р: Разбор, м: int, конец: int) -> bool:
    if not строковый(р, м, конец, "SMTP"):
        return False
    у = р.п.уровни[-1]
    сводка = []
    for место, строка in _строки(р, м, конец):
        ответ = re.match(r"(\d{3})([ -])(.*)", строка)
        заголовок = re.match(r"(" + "|".join(ЗАГОЛОВКИ_ПИСЬМА) + r"):\s*(.*)", строка, re.IGNORECASE)
        команда = re.match(r"(EHLO|HELO|MAIL FROM|RCPT TO|DATA|QUIT|STARTTLS|AUTH|RSET|NOOP|VRFY|EXPN|BDAT)\b:?\s*(.*)",
                           строка, re.IGNORECASE)
        if ответ:
            у.поле("Код ответа", "smtp.response.code", int(ответ.group(1)), место, 3)
            сводка.append(f"{ответ.group(1)} {ответ.group(3)}")
        elif команда:
            имя, арг = команда.group(1).upper(), команда.group(2)
            у.поле("Команда", "smtp.req.command", имя, место, len(команда.group(1)))
            if арг:
                у.поле("Параметр", "smtp.req.parameter", арг, место + len(строка) - len(арг), len(арг))
            if имя == "STARTTLS":
                у.поле("Переход на TLS", "smtp.starttls", "STARTTLS (RFC 3207)", место, len(строка))
            elif имя in ("MAIL FROM", "RCPT TO"):
                адрес = re.search(r"<([^>]*)>", арг)
                у.поле("Отправитель" if имя == "MAIL FROM" else "Получатель",
                       "smtp.req.mail_from" if имя == "MAIL FROM" else "smtp.req.rcpt_to",
                       адрес.group(1) if адрес else арг, место, len(строка))
            elif имя == "AUTH":
                вид = арг.split()
                if len(вид) == 2 and вид[0].upper() == "PLAIN":
                    пара = _auth_plain(вид[1])
                    if пара:
                        у.поле("Пользователь", "smtp.auth.username", пара[0], место, len(строка))
                        у.поле("Пароль", "smtp.auth.password", пара[1], место, len(строка))
            сводка.append(f"{имя} {арг}".strip() if имя != "AUTH" else "AUTH " + (арг.split() or [""])[0])
        elif заголовок:
            имя = заголовок.group(1).lower()
            у.поле(заголовок.group(1).capitalize(), f"imf.{имя.replace('-', '_')}", заголовок.group(2), место,
                   len(строка))
            if имя == "subject":
                сводка.append(f"тема «{заголовок.group(2)}»")
    if сводка:
        у.итог = "; ".join(сводка)[:120]
        р.п.инфо = "SMTP " + у.итог
    return True


def pop3(р: Разбор, м: int, конец: int) -> bool:
    if not строковый(р, м, конец, "POP3"):
        return False
    у = р.п.уровни[-1]
    сводка = []
    for место, строка in _строки(р, м, конец):
        ответ = re.match(r"(\+OK|-ERR)\s?(.*)", строка)
        команда = re.match(r"(USER|PASS|STLS|RETR|LIST|STAT|DELE|QUIT|TOP|UIDL|APOP|CAPA|AUTH)\b\s*(.*)", строка,
                           re.IGNORECASE)
        if ответ:
            у.поле("Ответ", "pop.response.indicator", ответ.group(1), место, len(ответ.group(1)))
            сводка.append(строка)
        elif команда:
            имя, арг = команда.group(1).upper(), команда.group(2)
            у.поле("Команда", "pop.request.command", имя, место, len(команда.group(1)))
            if имя == "USER":
                у.поле("Пользователь", "pop.user", арг, место + 5, len(арг))
            elif имя == "PASS":
                у.поле("Пароль", "pop.password", арг, место + 5, len(арг))
            elif имя == "STLS":
                у.поле("Переход на TLS", "pop.starttls", "STLS (RFC 2595)", место, len(строка))
            сводка.append(имя if имя == "PASS" else f"{имя} {арг}".strip())
    if сводка:
        у.итог = "; ".join(сводка)[:120]
        р.п.инфо = "POP3 " + у.итог
    return True


def imap(р: Разбор, м: int, конец: int) -> bool:
    if not строковый(р, м, конец, "IMAP"):
        return False
    у = р.п.уровни[-1]
    сводка = []
    for место, строка in _строки(р, м, конец):
        запрос = re.match(r"([A-Za-z0-9.]+) (LOGIN|STARTTLS|AUTHENTICATE|SELECT|EXAMINE|FETCH|UID|LOGOUT|CAPABILITY|"
                          r"LIST|SEARCH|STORE|APPEND|IDLE|NOOP)\b ?(.*)", строка, re.IGNORECASE)
        ответ = re.match(r"(\*|[A-Za-z0-9.]+) (OK|NO|BAD|BYE|PREAUTH)\b ?(.*)", строка)
        if ответ:
            у.поле("Ответ", "imap.response.status", ответ.group(2), место, len(строка))
            сводка.append(строка)
        elif запрос:
            метка, имя, арг = запрос.group(1), запрос.group(2).upper(), запрос.group(3)
            у.поле("Метка", "imap.request_tag", метка, место, len(метка))
            у.поле("Команда", "imap.request.command", имя, место + len(метка) + 1, len(имя))
            if имя == "LOGIN":
                части = re.findall(r'"[^"]*"|\S+', арг)
                if части:
                    у.поле("Пользователь", "imap.request.username", части[0].strip('"'), место, len(строка))
                if len(части) > 1:
                    у.поле("Пароль", "imap.request.password", части[1].strip('"'), место, len(строка))
            elif имя == "STARTTLS":
                у.поле("Переход на TLS", "imap.starttls", "STARTTLS (RFC 2595)", место, len(строка))
            сводка.append(f"{метка} {имя}" if имя == "LOGIN" else f"{метка} {имя} {арг}".strip())
    if сводка:
        у.итог = "; ".join(сводка)[:120]
        р.п.инфо = "IMAP " + у.итог
    return True


prilozh.ПОРТЫ_TCP[22] = ssh
prilozh.ПОРТЫ_TCP[21] = ftp
prilozh.ПОРТЫ_TCP[25] = smtp
prilozh.ПОРТЫ_TCP[587] = smtp
prilozh.ПОРТЫ_TCP[110] = pop3
prilozh.ПОРТЫ_TCP[143] = imap
prilozh.ЭВРИСТИКИ_TCP.append(ssh_признаки)
prilozh.ПЕРЕД_ПОРТАМИ.append(ftp_data)
for _имя, _разборщик in (("SSH", ssh), ("FTP", ftp), ("FTP-DATA", ftp_data), ("SMTP", smtp), ("POP3", pop3),
                         ("IMAP", imap)):
    prilozh.КАК["tcp"][_имя] = _разборщик
    ДОП_УРОВНИ[_имя] = "прикладной"
