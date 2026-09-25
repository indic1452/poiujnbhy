# -*- coding: utf-8 -*-
"""Фильтр пакетов — язык в духе Wireshark, но свой и безопасный (без eval).

    tcp                              есть уровень TCP
    ip.addr == 10.0.0.0/8            адрес в подсети (любой из двух)
    tcp.port == 443 or udp.port == 53
    dns.qry.name contains "example"
    frame.len > 1000 and not arp
    port in {53 123 161}             порт TCP, UDP или SCTP — из списка
    http.request.method == "POST"
    expert                           пакеты с ошибками (суммы, обрывы)
    frame[12:2] == 0800              байты 12–13 кадра (HEX: 0800, 08:00, 0x0800)
    payload[0].hi == 0xA             старший полубайт первого байта нагрузки
    frame contains ff:d8:ff          последовательность байт в кадре (или «текст»)

Сравнение истинно, если ему удовлетворяет хоть одно значение поля (как в
Wireshark: у пакета два адреса, ip.addr — оба). Строки сравниваются без
учёта регистра, ``matches`` — регулярное выражение.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Callable, Dict, List, Sequence

Поля = Dict[str, List[Any]]
Условие = Callable[[Поля], bool]

#: Псевдонимы: одно имя — несколько полей.
ПСЕВДОНИМЫ = {
    "port": ("tcp.port", "udp.port", "sctp.port"),
    "host": ("ip.addr", "ipv6.addr"),
    "addr": ("ip.addr", "ipv6.addr"),
    "ip.addr": ("ip.addr", "ipv6.addr"),
    "mac": ("eth.addr",),
    "ip": ("ipv4",),
    "eth": ("ethernet",),
    "expert": ("_ws.expert",),
    "ошибки": ("_ws.expert",),
    "data": ("данные",),
}
ЛЕКСЕМЫ = re.compile(r"""\s*(?:
    (?P<str>"(?:[^"\\]|\\.)*")|
    (?P<op>==|!=|>=|<=|>|<|&&|\|\||!|~|\(|\)|\{|\}|,)|
    (?P<word>[^\s()!=<>{}",~&|]+)
)""", re.X)


class ОшибкаФильтра(ValueError):
    pass


def _лексемы(текст: str) -> List[str]:
    итог, место = [], 0
    while место < len(текст):
        м = ЛЕКСЕМЫ.match(текст, место)
        if not м or м.end() == место:
            if текст[место:].strip():
                raise ОшибкаФильтра(f"непонятно в позиции {место + 1}: «{текст[место:место + 10]}»")
            break
        итог.append(м.group("str") or м.group("op") or м.group("word"))
        место = м.end()
    return итог


class _Разбор:
    def __init__(self, лексемы: List[str]):
        self.л = лексемы
        self.i = 0

    def смотреть(self) -> str:
        return self.л[self.i] if self.i < len(self.л) else ""

    def взять(self) -> str:
        if self.i >= len(self.л):
            raise ОшибкаФильтра("выражение оборвано")
        self.i += 1
        return self.л[self.i - 1]

    def выражение(self) -> Условие:
        левое = self.и()
        while self.смотреть().lower() in ("or", "||", "или"):
            self.взять()
            правое = self.и()
            левое = (lambda а, б: lambda п: а(п) or б(п))(левое, правое)
        return левое

    def и(self) -> Условие:
        левое = self.не()
        while self.смотреть().lower() in ("and", "&&", "и"):
            self.взять()
            правое = self.не()
            левое = (lambda а, б: lambda п: а(п) and б(п))(левое, правое)
        return левое

    def не(self) -> Условие:
        if self.смотреть().lower() in ("not", "!", "не"):
            self.взять()
            внутри = self.не()
            return lambda п: not внутри(п)
        return self.атом()

    def по_байтам(self, откуда: str, место, длина, полубайт) -> Условие:
        """frame[i], frame[i:n], payload[i].hi — байты кадра или нагрузки как число."""
        ключ = "_frame" if откуда == "frame" else "_payload"
        оп = self.смотреть().lower()
        if место is None:
            if оп not in ("contains", "matches"):
                raise ОшибкаФильтра(f"{откуда} без [место] — только contains или matches")
            self.взять()
            сырое = self.взять()
            значение = _значение(сырое)
            if оп == "contains":
                # В кавычках — текст; без кавычек — HEX (ff:d8:ff, 0xffd8, ffd8ff).
                образец = значение.encode("utf-8") if сырое.startswith('"') else _байты_значения(значение)
                return lambda п: образец in (п.get(ключ) or b"")
            регулярка = re.compile(значение.encode("utf-8"), re.S)
            return lambda п: bool(регулярка.search(п.get(ключ) or b""))
        место, длина = int(место), int(длина or 1)
        if not 1 <= длина <= 8:
            raise ОшибкаФильтра("байт в срезе — от 1 до 8")

        def число(п):
            д = п.get(ключ) or b""
            if место + длина > len(д):
                return None
            x = int.from_bytes(д[место:место + длина], "big")
            if полубайт == "hi":
                return x >> 4 if длина == 1 else None
            if полубайт == "lo":
                return x & 15 if длина == 1 else None
            return x

        if оп not in ("==", "!=", ">", "<", ">=", "<=", "eq", "ne", "gt", "lt", "ge", "le", "in"):
            return lambda п: число(п) is not None
        self.взять()
        оп = {"eq": "==", "ne": "!=", "gt": ">", "lt": "<", "ge": ">=", "le": "<="}.get(оп, оп)
        if оп == "in":
            if self.взять() != "{":
                raise ОшибкаФильтра("после in — список в фигурных скобках")
            значения = set()
            while self.смотреть() != "}":
                л = self.взять()
                if л != ",":
                    значения.add(_число_байт(л))
            self.взять()
            return lambda п: число(п) in значения
        цель = _число_байт(self.взять())
        return lambda п: (lambda x: x is not None and {"==": x == цель, "!=": x != цель, ">": x > цель,
                                                         "<": x < цель, ">=": x >= цель, "<=": x <= цель}[оп])(число(п))

    def атом(self) -> Условие:
        лекс = self.взять()
        if лекс == "(":
            внутри = self.выражение()
            if self.взять() != ")":
                raise ОшибкаФильтра("не закрыта скобка")
            return внутри
        байты = re.fullmatch(r"(frame|payload)(?:\[(\d+)(?::(\d+))?\])?(?:\.(hi|lo))?", лекс.lower())
        if байты:
            return self.по_байтам(*байты.groups())
        # Имена уровней бывают и русскими («данные»): первая — любая буква.
        if not re.fullmatch(r"[^\W\d][\w.\-]*", лекс):
            raise ОшибкаФильтра(f"ожидалось имя поля, а не «{лекс}»")
        ключи = ПСЕВДОНИМЫ.get(лекс.lower(), (лекс.lower(),))
        оп = self.смотреть().lower()
        if оп in ("==", "!=", ">", "<", ">=", "<=", "eq", "ne", "gt", "lt", "ge", "le", "contains",
                  "matches", "~", "in"):
            self.взять()
            оп = {"eq": "==", "ne": "!=", "gt": ">", "lt": "<", "ge": ">=", "le": "<=",
                  "~": "matches"}.get(оп, оп)
            if оп == "in":
                if self.взять() != "{":
                    raise ОшибкаФильтра("после in — список в фигурных скобках")
                значения = []
                while self.смотреть() != "}":
                    лекс_ = self.взять()
                    if лекс_ != ",":
                        значения.append(_значение(лекс_))
                self.взять()
                проверки = [_сравнение("==", з) for з in значения]
                return lambda п: any(any(пр(в) for пр in проверки) for к in ключи for в in п.get(к, ()))
            проверка = _сравнение(оп, _значение(self.взять()))
            if оп == "!=":
                # «!=» — ни одно значение не равно (а не «хоть одно не равно»).
                равно = _сравнение("==", проверка.значение)
                return lambda п: any(к in п for к in ключи) and not any(
                    равно(в) for к in ключи for в in п.get(к, ()))
            return lambda п: any(проверка(в) for к in ключи for в in п.get(к, ()))
        return lambda п: any(к in п for к in ключи)


def _байты_значения(текст: str) -> bytes:
    """«0800», «08:00», «0x0800», «ff d8» → байты; иначе — текст в UTF-8."""
    чистый = re.sub(r"[\s:]|^0x", "", текст.lower())
    if re.fullmatch(r"(?:[0-9a-f]{2})+", чистый) and (":" in текст or текст.lower().startswith("0x")
                                                     or re.fullmatch(r"[0-9a-f]+", текст.lower())):
        return bytes.fromhex(чистый)
    return текст.encode("utf-8")


def _значение(лекс: str) -> Any:
    if лекс.startswith('"'):
        return лекс[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return лекс


def _число_байт(лекс: str) -> int:
    """Значение для frame[i:n]: 10, 0x0a, 0800 (как HEX, если есть буквы или ведущий 0), 08:00."""
    т = _значение(лекс).strip().lower()
    try:
        if т.startswith("0x"):
            return int(т, 16)
        if ":" in т or re.search(r"[a-f]", т) or (len(т) > 1 and т.startswith("0")):
            return int(т.replace(":", ""), 16)
        return int(т)
    except ValueError:
        raise ОшибкаФильтра(f"не число: «{лекс}»") from None


def нужны_байты(текст: str) -> bool:
    return bool(re.search(r"\b(frame|payload)\b", текст or "", re.I))


def _число(x: Any):
    if isinstance(x, bool):
        return int(x)
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        т = x.strip().lower()
        try:
            return int(т, 16) if т.startswith("0x") else (float(т) if "." in т else int(т))
        except ValueError:
            return None
    return None


def _сравнение(оп: str, значение: Any):
    сеть = None
    if isinstance(значение, str) and "/" in значение:
        try:
            сеть = ipaddress.ip_network(значение, strict=False)
        except ValueError:
            сеть = None
    число = _число(значение)
    регулярка = re.compile(значение, re.I) if оп == "matches" else None

    def проверка(поле: Any) -> bool:
        if поле is True and оп == "==":
            return str(значение).lower() in ("1", "true", "да")
        if сеть is not None and оп in ("==", "!="):
            try:
                return ipaddress.ip_address(str(поле)) in сеть
            except ValueError:
                return False
        if оп == "contains":
            return str(значение).lower() in str(поле).lower()
        if оп == "matches":
            return bool(регулярка.search(str(поле)))
        ч = _число(поле)
        if ч is not None and число is not None:
            return {"==": ч == число, ">": ч > число, "<": ч < число, ">=": ч >= число,
                    "<=": ч <= число, "!=": ч != число}[оп]
        if оп in ("==", "!="):
            return str(поле).lower() == str(значение).lower()
        return False

    проверка.значение = значение
    return проверка


def собрать(текст: str) -> Условие:
    """Текст фильтра → функция «пакет подходит» над полями пакета. Пустой — все."""
    текст = (текст or "").strip()
    if not текст:
        return lambda п: True
    р = _Разбор(_лексемы(текст))
    условие = р.выражение()
    if р.i != len(р.л):
        raise ОшибкаФильтра(f"лишнее в конце: «{' '.join(р.л[р.i:])}»")
    return условие


def отобрать(поля: Sequence[Поля], текст: str) -> List[int]:
    условие = собрать(текст)
    return [i for i, п in enumerate(поля) if условие(п)]
