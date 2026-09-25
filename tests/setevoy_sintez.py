# -*- coding: utf-8 -*-
"""Сборка пакетов для тестов разборщика — по структурам RFC, с верными суммами.

Сборка намеренно простая и независимая от разборщика: поля кладутся
struct.pack'ом в порядке из RFC, контрольные суммы считаются здесь же.
"""

import ipaddress
import struct


def сумма(данные: bytes) -> int:
    if len(данные) % 2:
        данные += b"\0"
    s = sum(struct.unpack(f">{len(данные) // 2}H", данные))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def a4(текст):
    return ipaddress.IPv4Address(текст).packed


def a6(текст):
    return ipaddress.IPv6Address(текст).packed


def eth(нагрузка, тип=0x0800, dst="00:11:22:33:44:55", src="66:77:88:99:aa:bb"):
    return bytes.fromhex(dst.replace(":", "")) + bytes.fromhex(src.replace(":", "")) + struct.pack(">H", тип) + нагрузка


def ip(нагрузка, протокол, src="10.0.0.1", dst="10.0.0.2", ttl=64, ид=0x1234, флаги=0x4000):
    заголовок = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(нагрузка), ид, флаги, ttl, протокол, 0,
                            a4(src), a4(dst))
    заголовок = заголовок[:10] + struct.pack(">H", сумма(заголовок)) + заголовок[12:]
    return заголовок + нагрузка


def ip6(нагрузка, следующий, src="2001:db8::1", dst="2001:db8::2"):
    return struct.pack(">IHBB16s16s", 6 << 28, len(нагрузка), следующий, 64, a6(src), a6(dst)) + нагрузка


def _псевдо(src, dst, протокол, длина, v6=False):
    if v6:
        return a6(src) + a6(dst) + struct.pack(">IxxxB", длина, протокол)
    return a4(src) + a4(dst) + struct.pack(">BBH", 0, протокол, длина)


def udp(нагрузка, sport, dport, src="10.0.0.1", dst="10.0.0.2", v6=False):
    сег = struct.pack(">HHHH", sport, dport, 8 + len(нагрузка), 0) + нагрузка
    s = сумма(_псевдо(src, dst, 17, len(сег), v6) + сег) or 0xFFFF
    return сег[:6] + struct.pack(">H", s) + сег[8:]


def tcp(нагрузка, sport, dport, seq=1, ack=0, флаги=0x18, src="10.0.0.1", dst="10.0.0.2", опции=b"", v6=False):
    дл = 20 + len(опции)
    сег = struct.pack(">HHIIBBHHH", sport, dport, seq, ack, (дл // 4) << 4, флаги, 65535, 0, 0) + опции + нагрузка
    s = сумма(_псевдо(src, dst, 6, len(сег), v6) + сег)
    return сег[:16] + struct.pack(">H", s) + сег[18:]


def icmp(тип, код, тело):
    сообщение = struct.pack(">BBH", тип, код, 0) + тело
    return сообщение[:2] + struct.pack(">H", сумма(сообщение)) + сообщение[4:]


def dns_имя(имя):
    return b"".join(bytes([len(ч)]) + ч.encode() for ч in имя.split(".")) + b"\0"


def dns_запрос(имя, тип=1, ид=0xBEEF):
    return struct.pack(">HHHHHH", ид, 0x0100, 1, 0, 0, 0) + dns_имя(имя) + struct.pack(">HH", тип, 1)


def dns_ответ(имя, адреса, ид=0xBEEF):
    """Ответ A: имя в ответах — указателем на вопрос (0xC00C), как делают серверы."""
    тело = dns_имя(имя) + struct.pack(">HH", 1, 1)
    for адрес in адреса:
        тело += struct.pack(">HHHIH", 0xC00C, 1, 1, 300, 4) + a4(адрес)
    return struct.pack(">HHHHHH", ид, 0x8180, 1, len(адреса), 0, 0) + тело


def pcap(пакеты, канал=1, времена=None):
    файл = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, канал)
    for i, п in enumerate(пакеты):
        t = (времена[i] if времена else i * 0.5)
        файл += struct.pack("<IIII", int(t), int(round((t % 1) * 1e6)), len(п), len(п)) + п
    return файл


def pcapng(пакеты, канал=1, tsresol=9, времена=None):
    """SHB + IDB (с if_tsresol) + EPB — порядок байт «от младшего»."""
    def блок(тип, тело):
        тело += b"\0" * (-len(тело) % 4)
        дл = 12 + len(тело)
        return struct.pack("<II", тип, дл) + тело + struct.pack("<I", дл)
    файл = блок(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
    опции = struct.pack("<HHB3x", 9, 1, tsresol) + struct.pack("<HH", 0, 0)
    файл += блок(1, struct.pack("<HHI", канал, 0, 65535) + опции)
    файл += блок(5, b"\0" * 8)                        # статистика интерфейса — должна пропускаться
    for i, п in enumerate(пакеты):
        единиц = 2 ** (tsresol & 0x7F) if tsresol & 0x80 else 10 ** tsresol
        t = int(round((времена[i] if времена else i * 0.25) * единиц))
        файл += блок(6, struct.pack("<IIIII", 0, t >> 32, t & 0xFFFFFFFF, len(п), len(п)) + п)
    return файл
