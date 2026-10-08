"""Смесь протоколов, где многое тянется через много пакетов, — .sig, pcapng и pcap для проверки разбора пачками.

    python tests/pakety_smes.py вывод.sig --мб 1024 [--pcapng вывод.pcapng] [--зерно 1]

Кадры .sig — без канального заголовка (как после HDLC): голый IPv4/IPv6, PPP (FF 03),
MLPPP (фрагменты MP; после Configure-Ack LCP с опцией 18 — короткие номера), Cisco HDLC,
Frame Relay, Ethernet, MTP2 (ОКС-7: SCCP UDT/XUDT с сегментацией → TCAP/MAP, ISUP), LAPD
(Q.921 + Q.931). Поверх IP: фрагменты IPv4 (UDP → GTP-U), фрагменты IPv6, TCP с объектами
HTTP (Content-Length) и FTP (PASV + канал данных), GTPv2-C, GTP-U, SIP + SDP → RTP на
заявленных портах, Diameter по TCP, M3UA и SUA по SCTP (с сегментацией SCCP), NetFlow v9
(шаблон, затем данные по нему). Сценарии идут вперемешку (до ``АКТИВНЫХ`` сразу), так что
сборка и общее состояние тянутся через тысячи кадров — через стыки любых пачек. Пишет
опись (вывод.опись.json): сколько чего записано. Только stdlib + numpy.

В тестах: ``кадры(зерно, n)`` — первые n кадров смеси; ``sig``, ``pcapng``, ``pcap`` — файл из них.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import struct
import sys
import time
from pathlib import Path

import numpy as np

АКТИВНЫХ = 48


# -- суммы ---------------------------------------------------------------------------------------

def сумма(данные: bytes) -> int:
    if len(данные) % 2:
        данные += b"\0"
    s = sum(struct.unpack(f">{len(данные) // 2}H", данные))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


_CRC32C = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0x82F63B78 if _c & 1 else _c >> 1
    _CRC32C.append(_c)


def crc32c(данные: bytes) -> int:
    crc = 0xFFFFFFFF
    т = _CRC32C
    for б in данные:
        crc = т[(crc ^ б) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


# -- уровни IP ----------------------------------------------------------------------------------

def a4(т: str) -> bytes:
    return bytes(int(x) for x in т.split("."))


def ip4(нагрузка: bytes, протокол: int, src: str, dst: str, ид: int = 0, флаги_смещение: int = 0x4000,
        всего: int | None = None) -> bytes:
    з = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(нагрузка) if всего is None else всего, ид & 0xFFFF,
                    флаги_смещение, 64, протокол, 0, a4(src), a4(dst))
    return з[:10] + struct.pack(">H", сумма(з)) + з[12:] + нагрузка


def a6(n: int) -> bytes:
    return bytes.fromhex("20010db8000000000000000000") + n.to_bytes(3, "big")


def ip6(нагрузка: bytes, следующий: int, src: bytes, dst: bytes) -> bytes:
    return struct.pack(">IHBB16s16s", 6 << 28, len(нагрузка), следующий, 64, src, dst) + нагрузка


def _псевдо4(src, dst, протокол, длина):
    return a4(src) + a4(dst) + struct.pack(">BBH", 0, протокол, длина)


def udp4(нагрузка: bytes, sport: int, dport: int, src: str, dst: str) -> bytes:
    сег = struct.pack(">HHHH", sport, dport, 8 + len(нагрузка), 0) + нагрузка
    s = сумма(_псевдо4(src, dst, 17, len(сег)) + сег) or 0xFFFF
    return сег[:6] + struct.pack(">H", s) + сег[8:]


def udp6(нагрузка: bytes, sport: int, dport: int, src: bytes, dst: bytes) -> bytes:
    сег = struct.pack(">HHHH", sport, dport, 8 + len(нагрузка), 0) + нагрузка
    s = сумма(src + dst + struct.pack(">IxxxB", len(сег), 17) + сег) or 0xFFFF
    return сег[:6] + struct.pack(">H", s) + сег[8:]


def tcp4(нагрузка: bytes, sport: int, dport: int, seq: int, ack: int, src: str, dst: str, флаги: int = 0x18) -> bytes:
    сег = struct.pack(">HHIIBBHHH", sport, dport, seq & 0xFFFFFFFF, ack & 0xFFFFFFFF, 5 << 4, флаги, 65535, 0, 0) \
        + нагрузка
    s = сумма(_псевдо4(src, dst, 6, len(сег)) + сег)
    return сег[:16] + struct.pack(">H", s) + сег[18:]


def sctp(нагрузка: bytes, ppid: int, sport: int, dport: int, tsn: int) -> bytes:
    кусок = struct.pack(">BBHIHHI", 0, 3, 16 + len(нагрузка), tsn & 0xFFFFFFFF, 0, 0, ppid) + нагрузка
    кусок += b"\0" * (-len(кусок) % 4)
    заголовок = struct.pack(">HHII", sport, dport, 0x1234ABCD, 0)
    return заголовок[:8] + struct.pack("<I", crc32c(заголовок + кусок)) + кусок


def eth(нагрузка: bytes, тип: int = 0x0800, dst: bytes = b"\x02\0\0\0\0\x01", src: bytes = b"\x02\0\0\0\0\x02") -> bytes:
    return dst + src + struct.pack(">H", тип) + нагрузка


# -- ОКС-7 ----------------------------------------------------------------------------------------

def bcd(цифры: str, заполнитель: int = 0) -> bytes:
    п = [int(ц, 16) for ц in цифры] + ([заполнитель] if len(цифры) % 2 else [])
    return bytes(п[i] | (п[i + 1] << 4) for i in range(0, len(п), 2))


def ber(тег: int, значение: bytes) -> bytes:
    дл = len(значение)
    if дл < 0x80:
        return bytes([тег, дл]) + значение
    if дл < 0x100:
        return bytes([тег, 0x81, дл]) + значение
    return bytes([тег, 0x82]) + struct.pack(">H", дл) + значение


def метка(dpc: int, opc: int, sls: int) -> bytes:
    return struct.pack("<I", dpc | (opc << 14) | (sls << 28))


def адрес_pc(пк: int, ssn: int) -> bytes:
    return bytes([0x40 | 0x02 | 0x01]) + struct.pack("<H", пк) + bytes([ssn])


def адрес_gt(ssn: int, цифры: str) -> bytes:
    es = 1 if len(цифры) % 2 else 2
    return bytes([(4 << 2) | 0x02, ssn, 0, (1 << 4) | es, 4]) + bcd(цифры)


def sccp_udt(вызываемый: bytes, вызывающий: bytes, данные_: bytes) -> bytes:
    перем = [вызываемый, вызывающий, данные_]
    указатели, тело = b"", b""
    for i, п in enumerate(перем):
        указатели += bytes([(len(перем) - i) + len(тело)])
        тело += bytes([len(п)]) + п
    return bytes([0x09, 0x80]) + указатели + тело


def sccp_xudt(вызываемый: bytes, вызывающий: bytes, данные_: bytes, необяз: bytes = b"") -> bytes:
    перем = bytes([len(вызываемый)]) + вызываемый + bytes([len(вызывающий)]) + вызывающий \
        + bytes([len(данные_)]) + данные_
    указатели = bytes([4, 3 + 1 + len(вызываемый), 2 + 2 + len(вызываемый) + len(вызывающий),
                       1 + len(перем) if необяз else 0])
    return bytes([0x11, 0x81, 15]) + указатели + перем + (необяз + b"\x00" if необяз else b"")


def сегментация(первый: int, осталось: int, ссылка: bytes) -> bytes:
    return bytes([0x10, 4, (первый << 7) | (1 << 6) | осталось]) + ссылка


AC_UPDATE_LOCATION_V3 = bytes([0x04, 0x00, 0x00, 0x01, 0x00, 0x01, 0x03])


def диалог(контекст: bytes) -> bytes:
    aarq = ber(0x60, ber(0x80, b"\x07\x80") + ber(0xA1, ber(0x06, контекст)))
    return ber(0x6B, ber(0x28, ber(0x06, bytes([0x00, 0x11, 0x86, 0x05, 0x01, 0x01, 0x01])) + ber(0xA0, aarq)))


def tcap_begin(otid: bytes, imsi: str, хвост: int = 0) -> bytes:
    """Begin с Invoke updateLocation; ``хвост`` — лишние байты в аргументе (большое сообщение → сегменты)."""
    аргумент = ber(0x30, ber(0x04, bcd(imsi, 0xF)) + ber(0x81, b"\x91" + bcd("79160000001", 0xF))
                   + ber(0x04, b"\x91" + bcd("79160000002", 0xF)) + (ber(0x8A, bytes(хвост)) if хвост else b""))
    invoke = ber(0xA1, ber(0x02, b"\x01") + ber(0x02, b"\x02") + аргумент)
    return ber(0x62, ber(0x48, otid) + диалог(AC_UPDATE_LOCATION_V3) + ber(0x6C, invoke))


def tcap_end(dtid: bytes) -> bytes:
    результат = ber(0xA2, ber(0x02, b"\x01") + ber(0x30, ber(0x02, b"\x02") + ber(0x30, b"")))
    return ber(0x64, ber(0x49, dtid) + ber(0x6C, результат))


def isup_iam(cic: int, вызываемый: str, вызывающий: str) -> bytes:
    номер_к = bytes([(0x80 if len(вызываемый) % 2 else 0) | 0x03, 0x10]) + bcd(вызываемый)
    номер_от = bytes([(0x80 if len(вызывающий) % 2 else 0) | 0x04, 0x11]) + bcd(вызывающий)
    необяз = bytes([0x0A, len(номер_от)]) + номер_от + b"\x00"
    return struct.pack("<H", cic) + bytes([0x01, 0x00, 0x60, 0x01, 0x0A, 0x00, 2, 1 + 1 + len(номер_к)]) \
        + bytes([len(номер_к)]) + номер_к + необяз


def isup_rel(cic: int) -> bytes:
    return struct.pack("<H", cic) + bytes([0x0C, 2, 0, 2, 0x80, 0x90])


def параметр(тег: int, значение: bytes) -> bytes:
    return struct.pack(">HH", тег, 4 + len(значение)) + значение + b"\0" * (-len(значение) % 4)


def сигтран(класс: int, тип: int, *параметры: bytes) -> bytes:
    тело = b"".join(параметры)
    return struct.pack(">BBBBI", 1, 0, класс, тип, 8 + len(тело)) + тело


def m3ua_data(opc: int, dpc: int, si: int, пользователь: bytes, sls: int = 5) -> bytes:
    pd = struct.pack(">IIBBBB", opc, dpc, si, 2, 0, sls) + пользователь
    return сигтран(1, 1, параметр(0x0006, struct.pack(">I", 7)), параметр(0x0210, pd))


def sua_cldt(tcap: bytes) -> bytes:
    def адрес(ri, *под):
        return struct.pack(">HH", ri, 0x0007) + b"".join(под)
    gt = struct.pack(">BBBBBBBB", 0, 0, 0, 4, 11, 0, 1, 4) + bcd("79161234567", 0)
    источник = адрес(2, параметр(0x8002, struct.pack(">I", 1234)), параметр(0x8003, b"\0\0\0\x07"))
    получатель = адрес(1, параметр(0x8001, gt), параметр(0x8003, b"\0\0\0\x06"))
    return сигтран(7, 1, параметр(0x0006, struct.pack(">I", 1)), параметр(0x0115, b"\0\0\0\x80"),
                   параметр(0x0102, источник), параметр(0x0103, получатель),
                   параметр(0x0116, b"\0\0\0\0"), параметр(0x010B, tcap))


def q931_setup(ссылка: int) -> bytes:
    return bytes([0x08, 0x02]) + struct.pack(">H", ссылка & 0x7FFF) + bytes([0x05]) \
        + bytes([0x04, 0x03, 0x80, 0x90, 0xA3]) + bytes([0x18, 0x03, 0xA9, 0x83, 0x81]) \
        + bytes([0x6C, 2 + 5, 0x21, 0x83]) + b"12345" + bytes([0x70, 1 + 7, 0xA1]) + b"4951234"


def q931_release_complete(ссылка: int) -> bytes:
    return bytes([0x08, 0x02]) + struct.pack(">H", 0x8000 | (ссылка & 0x7FFF)) + bytes([0x5A, 0x08, 0x02, 0x80, 0x90])


# -- мобильная связь --------------------------------------------------------------------------------

def tbcd(цифры: str) -> bytes:
    цифры = цифры + ("f" if len(цифры) % 2 else "")
    return bytes(int(цифры[i + 1], 16) << 4 | int(цифры[i], 16) for i in range(0, len(цифры), 2))


def ie2(тип: int, значение: bytes, instance: int = 0) -> bytes:
    return struct.pack(">BHB", тип, len(значение), instance) + значение


def gtp2(тип: int, ies: bytes, teid: int | None, номер: int) -> bytes:
    ф = 0x40 | (0x08 if teid is not None else 0)
    тело = (struct.pack(">I", teid) if teid is not None else b"") + (номер & 0xFFFFFF).to_bytes(3, "big") + b"\0" + ies
    return struct.pack(">BBH", ф, тип, len(тело)) + тело


def gtpu(тело: bytes, teid: int) -> bytes:
    return struct.pack(">BBHI", 0x30, 0xFF, len(тело), teid) + тело


def avp(код: int, данные: bytes, флаги: int = 0x40) -> bytes:
    дл = 8 + len(данные)
    return struct.pack(">IB", код, флаги) + дл.to_bytes(3, "big") + данные + b"\0" * (-дл % 4)


def diameter(код: int, avps: bytes, флаги: int, прил: int, h2h: int) -> bytes:
    return bytes([1]) + (20 + len(avps)).to_bytes(3, "big") + bytes([флаги]) + код.to_bytes(3, "big") \
        + struct.pack(">III", прил, h2h & 0xFFFFFFFF, h2h * 7 & 0xFFFFFFFF) + avps


# -- генератор -------------------------------------------------------------------------------------

class Генератор:
    """Сценарии — генераторы кадров (вид, байты); идут вперемешку."""

    def __init__(self, зерно: int, шум: int = 8 << 20):
        self.г = random.Random(зерно)
        self.короткие_mp = False                 # после LCP с опцией 18 — короткие номера MP
        self.ip_ид = 1
        self.mp_номер = 0
        self.tsn = 1
        self.mtp2_bsn = 0
        self.mtp2_fsn = 0
        self.lapd_ns = 0
        self.sccp_ссылка = 1
        self.otid = 1
        self.rtp_порт = 20000
        self.ftp_порт = 30000
        self.счёт: dict[str, int] = {}
        # Заготовки тел объектов (берутся срезами — быстро).
        self.шум = self.г.randbytes(шум)

    def узел(self) -> str:
        i = self.г.randrange(200)
        return f"10.{i // 50}.{i % 50}.{10 + i % 200}"

    def пара(self) -> tuple[str, str]:
        а = self.узел()
        б = self.узел()
        while б == а:
            б = self.узел()
        return а, б

    def учесть(self, ключ: str, n: int = 1) -> None:
        self.счёт[ключ] = self.счёт.get(ключ, 0) + n

    def ip_кадр(self, пакет: bytes) -> tuple[str, bytes]:
        """IPv4-пакет: голым, в Ethernet, в PPP, в Cisco HDLC или во Frame Relay."""
        р = self.г.random()
        if р < 0.55:
            return "ip", пакет
        if р < 0.85:
            return "eth", eth(пакет)
        if р < 0.93:
            return "ppp", b"\xff\x03\x00\x21" + пакет
        if р < 0.97:
            return "chdlc", b"\x0f\x00\x08\x00" + пакет
        return "fr", b"\x18\x41\x03\xcc" + пакет          # DLCI 100, UI, NLPID IP

    # -- сценарии --

    def с_ipv4_фрагменты(self):
        а, б = self.пара()
        ид = self.ip_ид = (self.ip_ид + 1) & 0xFFFF
        if self.г.random() < 0.5:
            внутри = ip4(udp4(self.шум[:self.г.randrange(2000, 4000)], 1111, 2222, "10.9.0.1", "10.9.0.2"), 17,
                         "10.9.0.1", "10.9.0.2")
            датаграмма = udp4(gtpu(внутри, self.г.randrange(1 << 32)), 2152, 2152, а, б)
            self.учесть("ipv4_фрагм_gtpu")
        else:
            датаграмма = udp4(self.шум[:self.г.randrange(2000, 6000)], 7000, 7001, а, б)
            self.учесть("ipv4_фрагм_udp")
        куски = [датаграмма[i:i + 1480] for i in range(0, len(датаграмма), 1480)]
        потерять = self.г.random() < 0.02
        self.учесть("ipv4_фрагм_датаграмм")
        self.учесть("ipv4_фрагм_неполных" if потерять else "ipv4_фрагм_полных")
        for k, кусок in enumerate(куски):
            последний = k == len(куски) - 1
            if последний and потерять:
                return
            фс = (0 if последний else 0x2000) | (k * 1480 // 8)
            self.учесть("ipv4_фрагментов")
            yield self.ip_кадр(ip4(кусок, 17, а, б, ид=ид, флаги_смещение=фс))

    def с_ipv6_фрагменты(self):
        src, dst = a6(self.г.randrange(1, 200)), a6(self.г.randrange(200, 400))
        ид = self.г.randrange(1 << 32)
        датаграмма = udp6(self.шум[:self.г.randrange(2000, 5000)], 5000, 6000, src, dst)
        куски = [датаграмма[i:i + 1432] for i in range(0, len(датаграмма), 1432)]
        self.учесть("ipv6_фрагм_датаграмм")
        for k, кусок in enumerate(куски):
            m = 0 if k == len(куски) - 1 else 1
            заг = struct.pack(">BBHI", 17, 0, (k * 1432 // 8) << 3 | m, ид)
            self.учесть("ipv6_фрагментов")
            пакет = ip6(заг + кусок, 44, src, dst)
            yield ("ip", пакет) if self.г.random() < 0.5 else ("eth", eth(пакет, 0x86DD))

    def _tcp_поток(self, кл, сер, пк, пс, seq_к, seq_с, куски):
        """куски: [(кто, данные)] — кадры TCP по порядку (номера растут)."""
        seq = {"к": seq_к, "с": seq_с}
        for кто, данные in куски:
            if кто == "к":
                yield self.ip_кадр(ip4(tcp4(данные, пк, пс, seq["к"], seq["с"], кл, сер), 6, кл, сер))
            else:
                yield self.ip_кадр(ip4(tcp4(данные, пс, пк, seq["с"], seq["к"], сер, кл), 6, сер, кл))
            seq[кто] = (seq[кто] + len(данные)) & 0xFFFFFFFF

    def _тело(self) -> tuple[bytes, str]:
        р = self.г.random()
        размер = int(min(4 << 20, len(self.шум) // 2, max(2000, self.г.lognormvariate(10.5, 1.3))))
        if р < 0.3:
            return b"%PDF-1.4\n" + self.шум[:размер] + b"\n%%EOF\n", "application/pdf"
        if р < 0.6:
            return b"\x89PNG\r\n\x1a\n" + self.шум[:размер], "image/png"
        return self.шум[:размер], "application/octet-stream"

    def с_http(self):
        кл, сер = self.пара()
        тело, вид = self._тело()
        n = self.otid = self.otid + 1
        запрос = (f"GET /files/f{n}.bin HTTP/1.1\r\nHost: srv{n % 13}.local\r\nUser-Agent: gen/1.0\r\n\r\n").encode()
        ответ = (f"HTTP/1.1 200 OK\r\nContent-Type: {вид}\r\nContent-Length: {len(тело)}\r\n"
                 f"Content-Disposition: attachment; filename=\"f{n}.bin\"\r\n\r\n").encode() + тело
        куски = [("к", запрос)] + [("с", ответ[i:i + 1400]) for i in range(0, len(ответ), 1400)]
        self.учесть("http_объектов")
        self.учесть("http_байт_тел", len(тело))
        yield from self._tcp_поток(кл, сер, self.г.randrange(30000, 60000), 80, self.г.randrange(1 << 31),
                                   self.г.randrange(1 << 31), куски)

    def с_ftp(self):
        кл, сер = self.пара()
        self.ftp_порт = 30000 + (self.ftp_порт - 30000 + 7) % 30000
        порт = self.ftp_порт
        тело, _ = self._тело()
        n = self.otid = self.otid + 1
        упр = [("с", b"220 ready\r\n"), ("к", b"USER a\r\n"), ("с", b"331 ok\r\n"), ("к", b"PASS b\r\n"),
               ("с", b"230 ok\r\n"), ("к", b"TYPE I\r\n"), ("с", b"200 ok\r\n"), ("к", b"PASV\r\n"),
               ("с", ("227 Entering Passive Mode (%s,%d,%d).\r\n" % (сер.replace(".", ","), порт >> 8, порт & 255)).encode()),
               ("к", f"RETR /pub/file{n}.dat\r\n".encode()), ("с", b"150 Opening\r\n")]
        пк = self.г.randrange(30000, 60000)
        yield from self._tcp_поток(кл, сер, пк, 21, 1000, 5000, упр)
        self.учесть("ftp_файлов")
        self.учесть("ftp_байт", len(тело))
        yield from self._tcp_поток(кл, сер, пк + 1, порт, 1, 100, [("с", тело[i:i + 1400]) for i in range(0, len(тело), 1400)])
        yield from self._tcp_поток(кл, сер, пк, 21, 2000, 6000, [("с", b"226 done\r\n")])

    def _mtp2(self, тело: bytes) -> tuple[str, bytes]:
        self.mtp2_bsn = (self.mtp2_bsn + 1) & 0x7F
        self.mtp2_fsn = (self.mtp2_fsn + 1) & 0x7F
        li = min(len(тело), 63)
        self.учесть("mtp2_кадров")
        return "mtp2", bytes([self.mtp2_bsn | 0x80, self.mtp2_fsn | 0x80, li]) + тело

    def с_ss7_сегменты(self):
        """Большой TCAP/MAP по MTP2: XUDT с сегментацией (2–4 сегмента), затем ответ End; изредка ISUP."""
        self.otid += 1
        otid = self.otid.to_bytes(4, "big")
        tcap = tcap_begin(otid, f"25001{self.г.randrange(10**10):010d}", хвост=self.г.randrange(300, 600))
        self.sccp_ссылка = (self.sccp_ссылка + 1) & 0xFFFFFF
        ссылка = self.sccp_ссылка.to_bytes(3, "big")
        вызываемый, вызывающий = адрес_gt(6, "79161234567"), адрес_pc(1234, 7)
        куски = [tcap[i:i + 200] for i in range(0, len(tcap), 200)]
        потерять = self.г.random() < 0.02
        self.учесть("sccp_сообщений_mtp2")
        self.учесть("sccp_неполных_mtp2" if потерять else "sccp_полных_mtp2")
        for k, кусок in enumerate(куски):
            if потерять and k == len(куски) - 1:
                break
            xudt = sccp_xudt(вызываемый, вызывающий, кусок, сегментация(int(k == 0), len(куски) - 1 - k, ссылка))
            self.учесть("sccp_сегментов")
            yield self._mtp2(bytes([0x83]) + метка(100, 200, k & 15) + xudt)
        yield self._mtp2(bytes([0x83]) + метка(200, 100, 3) + sccp_udt(адрес_pc(1234, 7), адрес_pc(100, 6), tcap_end(otid)))
        if self.г.random() < 0.3:
            cic = self.г.randrange(4096)
            yield self._mtp2(bytes([0x85]) + метка(100, 200, 1) + isup_iam(cic, "4951234567", "74951112233"))
            yield self._mtp2(bytes([0x85]) + метка(100, 200, 1) + isup_rel(cic))
        if self.г.random() < 0.5:
            self.учесть("mtp2_fisu")
            yield "mtp2", bytes([self.mtp2_bsn | 0x80, self.mtp2_fsn | 0x80, 0])

    def с_sigtran(self):
        """M3UA (с сегментами SCCP) и SUA по SCTP поверх IPv4."""
        а, б = self.пара()
        self.otid += 1
        otid = self.otid.to_bytes(4, "big")
        if self.г.random() < 0.5:
            tcap = tcap_begin(otid, f"25002{self.г.randrange(10**10):010d}", хвост=self.г.randrange(300, 500))
            self.sccp_ссылка = (self.sccp_ссылка + 1) & 0xFFFFFF
            ссылка = self.sccp_ссылка.to_bytes(3, "big")
            куски = [tcap[i:i + 180] for i in range(0, len(tcap), 180)]
            self.учесть("sccp_сообщений_m3ua")
            for k, кусок in enumerate(куски):
                xudt = sccp_xudt(адрес_gt(6, "79161234567"), адрес_pc(2345, 8), кусок,
                                 сегментация(int(k == 0), len(куски) - 1 - k, ссылка))
                self.tsn += 1
                self.учесть("m3ua")
                yield self.ip_кадр(ip4(sctp(m3ua_data(200, 100, 3, xudt), 3, 2905, 2905, self.tsn), 132, а, б))
        else:
            self.tsn += 1
            self.учесть("sua")
            yield self.ip_кадр(ip4(sctp(sua_cldt(tcap_begin(otid, "250031234567890")), 4, 14001, 14001, self.tsn), 132, а, б))

    def с_mlppp(self):
        """IPv4-пакет в MP (RFC 1990): 2–4 фрагмента с длинными номерами подряд."""
        а, б = self.пара()
        пакет = b"\x00\x21" + ip4(udp4(self.шум[:self.г.randrange(600, 1400)], 4000, 4001, а, б), 17, а, б)
        n = self.г.randrange(2, 5)
        шаг = -(-len(пакет) // n)
        куски = [пакет[i:i + шаг] for i in range(0, len(пакет), шаг)]
        номер = self.mp_номер
        self.mp_номер = (self.mp_номер + len(куски)) & 0xFFFFFF
        потерять = self.г.random() < 0.02
        self.учесть("mp_пакетов")
        self.учесть("mp_неполных" if потерять else "mp_полных")
        короткие = self.короткие_mp
        for k, кусок in enumerate(куски):
            if потерять and k == 1:
                continue
            ф = (0x80 if k == 0 else 0) | (0x40 if k == len(куски) - 1 else 0)
            self.учесть("mp_фрагментов")
            if короткие:                             # RFC 1990, 3.3: B E 0 0 и 12 бит номера
                заголовок = struct.pack(">H", (ф << 8) | ((номер + k) & 0xFFF))
            else:
                заголовок = bytes([ф]) + ((номер + k) & 0xFFFFFF).to_bytes(3, "big")
            yield "ppp", b"\xff\x03\x00\x3d" + заголовок + кусок

    def с_lcp(self):
        """Configure-Ack LCP с опцией 18 (SSNH, RFC 1990, 5.1.2): дальше номера MP короткие (12 бит)."""
        self.учесть("lcp")
        опции = bytes([17, 4, 0x05, 0xDC]) + bytes([18, 2])                # MRRU 1500, SSNH
        lcp = bytes([2, self.г.randrange(256)]) + struct.pack(">H", 4 + len(опции)) + опции
        self.короткие_mp = True
        yield "ppp", b"\xff\x03\xc0\x21" + lcp

    def с_netflow(self):
        """NetFlow v9 (RFC 3954): шаблон 256 (адреса, порты, протокол, байты), затем записи по нему."""
        а, б = self.пара()
        поля = [(8, 4), (12, 4), (7, 2), (11, 2), (4, 1), (1, 4)]
        шаблон = struct.pack(">HH", 256, len(поля)) + b"".join(struct.pack(">HH", т, д) for т, д in поля)
        набор_ш = struct.pack(">HH", 0, 4 + len(шаблон)) + шаблон

        def пакет(наборы: bytes, n: int) -> bytes:
            self.учесть("netflow")
            return struct.pack(">HHIIII", 9, n, 1000, 1_760_000_000, self.otid, 7) + наборы
        yield self.ip_кадр(ip4(udp4(пакет(набор_ш, 1), 2055, 2055, а, б), 17, а, б))
        for i in range(self.г.randrange(5, 30)):
            запись = (a4(f"10.1.{i % 250}.1") + a4("10.2.0.9") + struct.pack(">HHB", 1000 + i, 80, 6)
                      + struct.pack(">I", 1500 * i))
            данные = struct.pack(">HH", 256, 4 + len(запись) + 3) + запись + b"\0" * 3
            yield self.ip_кадр(ip4(udp4(пакет(данные, 1), 2055, 2055, а, б), 17, а, б))

    def с_gtp(self):
        а, б = self.пара()
        imsi = f"25001{self.г.randrange(10**10):010d}"
        ies = (ie2(1, tbcd(imsi)) + ie2(76, tbcd("79161234567")) + ie2(83, b"\x52\xf0\x10") + ie2(82, b"\x06")
               + ie2(87, bytes([0x80 | 10]) + struct.pack(">I", self.г.randrange(1 << 32)) + a4(а))
               + ie2(71, b"\x08internet") + ie2(99, b"\x01"))
        self.учесть("gtpv2")
        yield self.ip_кадр(ip4(udp4(gtp2(32, ies, 0, self.г.randrange(1 << 24)), 2123, 2123, а, б), 17, а, б))
        teid = self.г.randrange(1 << 32)
        for _ in range(self.г.randrange(5, 60)):
            внутри = ip4(udp4(self.шум[:self.г.randrange(100, 1300)], 5555, 6666, "10.200.0.1", "8.8.8.8"), 17,
                         "10.200.0.1", "8.8.8.8")
            self.учесть("gtpu")
            yield self.ip_кадр(ip4(udp4(gtpu(внутри, teid), 2152, 2152, а, б), 17, а, б))

    def с_sip_rtp(self):
        а, б = self.пара()
        self.rtp_порт = 20000 + (self.rtp_порт - 20000 + 4) % 20000
        па, пб = self.rtp_порт, self.rtp_порт + 2
        вызов = f"c{self.otid}@gen"
        self.otid += 1

        def sdp(адрес, порт):
            return (f"v=0\r\no=- 1 1 IN IP4 {адрес}\r\ns=-\r\nc=IN IP4 {адрес}\r\nt=0 0\r\n"
                    f"m=audio {порт} RTP/AVP 8 101\r\na=rtpmap:101 telephone-event/8000\r\n").encode()

        def sip(начало, sdp_=b""):
            return (f"{начало}\r\nVia: SIP/2.0/UDP {а}:5060\r\nCall-ID: {вызов}\r\nCSeq: 1 INVITE\r\n"
                    f"From: <sip:a@{а}>\r\nTo: <sip:b@{б}>\r\n"
                    + ("Content-Type: application/sdp\r\n" if sdp_ else "")
                    + f"Content-Length: {len(sdp_)}\r\n\r\n").encode() + sdp_
        self.учесть("sip_вызовов")
        yield self.ip_кадр(ip4(udp4(sip(f"INVITE sip:b@{б} SIP/2.0", sdp(а, па)), 5060, 5060, а, б), 17, а, б))
        yield self.ip_кадр(ip4(udp4(sip("SIP/2.0 200 OK", sdp(б, пб)), 5060, 5060, б, а), 17, б, а))
        ssrc_а, ssrc_б = self.г.randrange(1 << 32), self.г.randrange(1 << 32)
        for i in range(self.г.randrange(100, 1500)):
            for (от, к, по, пк, ssrc) in ((а, б, па, пб, ssrc_а), (б, а, пб, па, ssrc_б)):
                rtp = struct.pack(">BBHII", 0x80, 8, i & 0xFFFF, i * 160, ssrc) + self.шум[i * 7 % 100000:i * 7 % 100000 + 160]
                self.учесть("rtp")
                yield self.ip_кадр(ip4(udp4(rtp, по, пк, от, к), 17, от, к))
        yield self.ip_кадр(ip4(udp4(sip(f"BYE sip:b@{б} SIP/2.0"), 5060, 5060, а, б), 17, а, б))

    def с_diameter(self):
        а, б = self.пара()
        h = self.г.randrange(1 << 32)
        cer = diameter(257, avp(264, b"mme.example.org") + avp(296, b"example.org") + avp(266, struct.pack(">I", 10415)),
                       0x80, 0, h)
        cea = diameter(257, avp(268, struct.pack(">I", 2001)) + avp(264, b"hss.example.org"), 0x00, 0, h)
        куски = [("к", cer), ("с", cea)]
        for i in range(self.г.randrange(2, 20)):
            куски.append(("к", diameter(272, avp(263, f"gw;{h};{i}".encode()) + avp(258, struct.pack(">I", 4))
                                        + avp(416, struct.pack(">I", 2)) + avp(415, struct.pack(">I", i)), 0xC0, 4, h + i)))
            куски.append(("с", diameter(272, avp(263, f"gw;{h};{i}".encode()) + avp(268, struct.pack(">I", 2001)),
                                        0x40, 4, h + i)))
        self.учесть("diameter", len(куски))
        yield from self._tcp_поток(а, б, self.г.randrange(30000, 60000), 3868, 1, 1, куски)

    def с_lapd(self):
        ссылка = self.г.randrange(1, 32767)
        for сообщение in (q931_setup(ссылка), q931_release_complete(ссылка)):
            ns = self.lapd_ns = (self.lapd_ns + 1) & 0x7F
            self.учесть("lapd_q931")
            yield "lapd", bytes([0 << 2 | 0x02, (0 << 1) | 1, ns << 1, ns << 1]) + сообщение
            yield "lapd", bytes([0 << 2, 0x01, 0x01, ((ns + 1) & 0x7F) << 1])          # RR

    def с_прочее(self):
        """DNS, ICMP — в Ethernet и голым IP; IPv6 UDP."""
        а, б = self.пара()
        имя = f"host{self.г.randrange(1000)}.example.ru"
        q = struct.pack(">HHHHHH", 7, 0x0100, 1, 0, 0, 0) + b"".join(bytes([len(ч)]) + ч.encode() for ч in имя.split(".")) \
            + b"\0" + struct.pack(">HH", 1, 1)
        self.учесть("dns")
        yield self.ip_кадр(ip4(udp4(q, 40000, 53, а, б), 17, а, б))
        icmp = struct.pack(">BBHHH", 8, 0, 0, 1, 1) + b"ping" * 8
        icmp = icmp[:2] + struct.pack(">H", сумма(icmp)) + icmp[4:]
        yield self.ip_кадр(ip4(icmp, 1, а, б))
        src, dst = a6(1), a6(2)
        yield "ip", ip6(udp6(self.шум[:90], 5000, 6000, src, dst), 17, src, dst)

    СЦЕНАРИИ = (("с_http", 10), ("с_ftp", 4), ("с_ipv4_фрагменты", 30), ("с_ipv6_фрагменты", 12),
                ("с_ss7_сегменты", 60), ("с_sigtran", 30), ("с_mlppp", 60), ("с_gtp", 10), ("с_sip_rtp", 2),
                ("с_diameter", 6), ("с_lapd", 30), ("с_прочее", 40), ("с_netflow", 2), ("с_lcp", 0.2))

    def кадры(self):
        """Кадры вперемешку: (вид, байты, номер сценария, имя сценария)."""
        имена = [и for и, _ in self.СЦЕНАРИИ]
        веса = [в for _, в in self.СЦЕНАРИИ]
        активные: list = []
        номер = 0
        while True:
            while len(активные) < АКТИВНЫХ:
                имя = self.г.choices(имена, веса)[0]
                активные.append((getattr(self, имя)(), номер, имя))
                номер += 1
            i = self.г.randrange(len(активные))
            ген, н, имя = активные[i]
            try:
                вид, кадр = next(ген)
            except StopIteration:
                активные[i] = активные[-1]
                активные.pop()
                continue
            yield вид, кадр, н, имя


ТИПЫ_PCAPNG = {"ip": 101, "eth": 1, "ppp": 50, "chdlc": 104, "fr": 107, "mtp2": 140, "lapd": 203}


def кадры(зерно: int, n: int, шум: int = 1 << 20, lcp: int | None = None) -> list[tuple[str, bytes]]:
    """Первые ``n`` кадров смеси: (вид, байты); ``lcp`` — на этом месте договорить короткие номера MP."""
    итог = []
    г = Генератор(зерно, шум)
    for вид, кадр, _, _ in г.кадры():
        if len(итог) == lcp:
            итог.extend(next(г.с_lcp()) for _ in range(1))
        итог.append((вид, кадр))
        if len(итог) >= n:
            break
    return итог


def sig(кадры_: list[tuple[str, bytes]]) -> bytes:
    """.sig: двухбайтовая длина (старший байт первым) и кадр."""
    return b"".join(struct.pack(">H", len(к)) + к for _, к in кадры_)


def pcapng(кадры_: list[tuple[str, bytes]], шаг: int = 137) -> bytes:
    """pcapng: свой интерфейс на каждый вид кадра (IDB появляются по ходу записи), время растёт."""
    итог = bytearray(блок(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)))
    интерфейсы: dict[str, int] = {}
    время = 1_760_000_000_000_000
    for вид, к in кадры_:
        if вид not in интерфейсы:
            интерфейсы[вид] = len(интерфейсы)
            итог += блок(1, struct.pack("<HHI", ТИПЫ_PCAPNG[вид], 0, 262144))
        время += шаг
        итог += блок(6, struct.pack("<IIIII", интерфейсы[вид], время >> 32, время & 0xFFFFFFFF, len(к), len(к)) + к)
    return bytes(итог)


def pcap(кадры_: list[tuple[str, bytes]]) -> bytes:
    """pcap Ethernet: кадры Ethernet смеси, остальные IP — в Ethernet (прочее канального уровня — мимо)."""
    итог = bytearray(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, 1))
    for i, (вид, к) in enumerate(кадры_):
        if вид == "ip":
            к = eth(к, 0x86DD if к[0] >> 4 == 6 else 0x0800)
        elif вид != "eth":
            continue
        итог += struct.pack("<IIII", 1_760_000_000 + i // 1000, (i % 1000) * 1000, len(к), len(к)) + к
    return bytes(итог)


def блок(тип: int, тело: bytes) -> bytes:
    тело += b"\0" * (-len(тело) % 4)
    return struct.pack("<II", тип, 12 + len(тело)) + тело + struct.pack("<I", 12 + len(тело))


def записать(путь: Path, мб: int, зерно: int, pcapng: Path | None) -> dict:
    г = Генератор(зерно)
    предел = мб << 20
    хэш = hashlib.sha256()
    виды: dict[str, int] = {}
    сценарии_начало: dict[int, int] = {}
    сценарии_конец: dict[int, int] = {}
    сценарии_имя: dict[int, str] = {}
    записано = 0
    кадров = 0
    интерфейсы: dict[str, int] = {}
    t0 = time.time()
    г_времени = random.Random(зерно + 7)
    with open(путь, "wb") as ф, (open(pcapng, "wb") if pcapng else open("/dev/null", "wb")) as фп:
        буфер, буфер_п = bytearray(), bytearray()
        if pcapng:
            буфер_п += блок(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
        время = 1_760_000_000_000_000
        for вид, кадр, н, имя in г.кадры():
            if записано + len(буфер) + 2 + len(кадр) > предел:
                break
            буфер += struct.pack(">H", len(кадр)) + кадр
            if pcapng:
                if вид not in интерфейсы:
                    интерфейсы[вид] = len(интерфейсы)
                    буфер_п += блок(1, struct.pack("<HHI", ТИПЫ_PCAPNG[вид], 0, 262144))
                время += г_времени.randrange(1, 500)
                буфер_п += блок(6, struct.pack("<IIIII", интерфейсы[вид], время >> 32, время & 0xFFFFFFFF,
                                               len(кадр), len(кадр)) + кадр)
            виды[вид] = виды.get(вид, 0) + 1
            if н not in сценарии_начало:
                сценарии_начало[н] = кадров
                сценарии_имя[н] = имя
            сценарии_конец[н] = кадров
            кадров += 1
            if len(буфер) > 4 << 20:
                ф.write(буфер)
                хэш.update(буфер)
                записано += len(буфер)
                буфер = bytearray()
                if pcapng:
                    фп.write(буфер_п)
                    буфер_п = bytearray()
        ф.write(буфер)
        хэш.update(буфер)
        записано += len(буфер)
        if pcapng:
            фп.write(буфер_п)
    имена = sorted(set(сценарии_имя.values()))
    ключи = sorted(сценарии_начало)
    np.savez_compressed(str(путь) + ".опись.npz",
                        начало=np.array([сценарии_начало[к] for к in ключи], dtype=np.int64),
                        конец=np.array([сценарии_конец[к] for к in ключи], dtype=np.int64),
                        вид=np.array([имена.index(сценарии_имя[к]) for к in ключи], dtype=np.int16),
                        имена=np.array(имена))
    опись = {"файл": str(путь), "байт": записано, "кадров": кадров, "sha256": хэш.hexdigest(), "виды": виды,
             "счёт": г.счёт, "секунд": round(time.time() - t0, 1)}
    Path(str(путь) + ".опись.json").write_text(json.dumps(опись, ensure_ascii=False, indent=1), encoding="utf-8")
    return опись


def main() -> int:
    р = argparse.ArgumentParser()
    р.add_argument("вывод")
    р.add_argument("--мб", type=int, default=1024)
    р.add_argument("--зерно", type=int, default=1)
    р.add_argument("--pcapng", default="")
    а = р.parse_args()
    опись = записать(Path(а.вывод), а.мб, а.зерно, Path(а.pcapng) if а.pcapng else None)
    print(json.dumps(опись, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
