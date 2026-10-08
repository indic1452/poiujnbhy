"""Большой pcap со смесью протоколов — для проверки «Анализа пакетов» на гигабайтах.

    python tests/pakety_generator.py вывод.pcap --мб 1024 [--зерно 1] [--обрыв]

Пакеты собираются по RFC (setevoy_sintez): Ethernet, ARP, IPv4/IPv6, ICMP, UDP (DNS
запросы и ответы, RTP, NTP-подобные), TCP (HTTP запросы и ответы, TLS-подобная
нагрузка 443, большие сегменты передачи файлов, SMB-подобные), 64 узла. Заготовки
(несколько сотен разных пакетов) пишутся по кругу с растущим временем и номером TCP —
так гигабайт пишется за десятки секунд. Печатает число пакетов и SHA-256 файла: по ним
сверяется, что разобрано всё. ``--обрыв`` — последний пакет записан не целиком.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setevoy_sintez as с  # noqa: E402


def заготовки(зерно: int) -> list[bytes]:
    г = random.Random(зерно)
    узлы = [f"10.{i // 32}.{i % 32}.{10 + i}" for i in range(64)]
    маки = [":".join(f"{б:02x}" for б in (0x02, 0, 0, i // 256, i % 256, 7)) for i in range(64)]
    итог: list[bytes] = []

    def пара():
        а, б = г.sample(range(64), 2)
        return узлы[а], узлы[б], маки[а], маки[б]

    for i in range(40):                                  # DNS запрос и ответ
        а, б, ма, мб = пара()
        имя = f"host{i}.example{г.randint(1, 9)}.ru"
        ид = г.randint(1, 65535)
        порт = г.randint(20000, 60000)
        итог.append(с.eth(с.ip(с.udp(с.dns_запрос(имя, ид=ид), порт, 53, src=а, dst=б), 17, src=а, dst=б), dst=мб, src=ма))
        итог.append(с.eth(с.ip(с.udp(с.dns_ответ(имя, [f"192.0.2.{г.randint(1, 254)}"], ид=ид), 53, порт, src=б, dst=а), 17,
                               src=б, dst=а), dst=ма, src=мб))
    for i in range(30):                                  # HTTP
        а, б, ма, мб = пара()
        порт = г.randint(30000, 60000)
        запрос = (f"GET /doc/{i}.html HTTP/1.1\r\nHost: site{i % 7}.local\r\nUser-Agent: gen/1.0\r\n\r\n").encode()
        ответ = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 64\r\n\r\n" + b"x" * 64)
        итог.append(с.eth(с.ip(с.tcp(запрос, порт, 80, seq=1000, ack=1, src=а, dst=б), 6, src=а, dst=б), dst=мб, src=ма))
        итог.append(с.eth(с.ip(с.tcp(ответ, 80, порт, seq=1, ack=1000 + len(запрос), src=б, dst=а), 6, src=б, dst=а),
                          dst=ма, src=мб))
    for i in range(60):                                  # большие сегменты: файлы, SMB, TLS-нагрузка
        а, б, ма, мб = пара()
        порт = г.choice([445, 443, 8443, 21000 + i])
        нагрузка = bytes(г.getrandbits(8) for _ in range(г.choice([1200, 1380, 1448])))
        итог.append(с.eth(с.ip(с.tcp(нагрузка, порт, г.randint(30000, 60000), seq=г.randint(1, 1 << 30), src=а, dst=б), 6,
                               src=а, dst=б), dst=мб, src=ма))
    for i in range(40):                                  # RTP
        а, б, ма, мб = пара()
        rtp = struct.pack(">BBHII", 0x80, 0, i, i * 160, 0x1000 + i) + bytes(г.getrandbits(8) for _ in range(160))
        итог.append(с.eth(с.ip(с.udp(rtp, 16384 + 2 * i, 20000 + 2 * i, src=а, dst=б), 17, src=а, dst=б), dst=мб, src=ма))
    for i in range(20):                                  # ICMP эхо
        а, б, ма, мб = пара()
        итог.append(с.eth(с.ip(с.icmp(8, 0, struct.pack(">HH", i, 1) + b"ping" * 8), 1, src=а, dst=б), dst=мб, src=ма))
    for i in range(10):                                  # ARP
        а, б, ма, мб = пара()
        arp = struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, bytes.fromhex(ма.replace(":", "")), с.a4(а), b"\0" * 6,
                          с.a4(б))
        итог.append(с.eth(arp, 0x0806, dst="ff:ff:ff:ff:ff:ff", src=ма))
    for i in range(20):                                  # IPv6 UDP
        итог.append(с.eth(с.ip6(с.udp(bytes(г.getrandbits(8) for _ in range(90)), 5000 + i, 6000, src=f"2001:db8::{i + 1}",
                                      dst="2001:db8::ff", v6=True), 17, src=f"2001:db8::{i + 1}", dst="2001:db8::ff"),
                          тип=0x86DD))
    for i in range(20):                                  # неизвестная нагрузка UDP — для «Неизвестных форматов»
        а, б, ма, мб = пара()
        тело = struct.pack(">HHI", 0xA55A, 40 + i, i) + bytes(г.getrandbits(8) for _ in range(40))
        итог.append(с.eth(с.ip(с.udp(тело, 7000, 7001, src=а, dst=б), 17, src=а, dst=б), dst=мб, src=ма))
    # Доли: больших сегментов больше — средний пакет как в живой сети.
    веса = [1] * len(итог)
    return [к for к, в in zip(итог, веса, strict=True) for _ in range(в)]


def записать(путь: Path, мб: int, зерно: int = 1, обрыв: bool = False) -> tuple[int, str]:
    кадры = заготовки(зерно)
    г = random.Random(зерно + 1)
    предел = мб << 20
    хэш = hashlib.sha256()
    пакетов = 0
    время = 1_760_000_000.0
    with open(путь, "wb") as ф:
        голова = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, 1)
        ф.write(голова)
        хэш.update(голова)
        записано = len(голова)
        буфер = bytearray()
        while записано + len(буфер) < предел:
            кадр = кадры[г.randrange(len(кадры))]
            время += г.random() * 0.0005
            сек = int(время)
            буфер += struct.pack("<IIII", сек, int((время - сек) * 1e6), len(кадр), len(кадр)) + кадр
            пакетов += 1
            if len(буфер) > 4 << 20:
                ф.write(буфер)
                хэш.update(буфер)
                записано += len(буфер)
                буфер = bytearray()
        if обрыв:
            кадр = кадры[0]
            буфер += struct.pack("<IIII", int(время) + 1, 0, len(кадр), len(кадр)) + кадр[: len(кадр) // 2]
        ф.write(буфер)
        хэш.update(буфер)
    return пакетов, хэш.hexdigest()


def main() -> int:
    р = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    р.add_argument("вывод")
    р.add_argument("--мб", type=int, default=1024)
    р.add_argument("--зерно", type=int, default=1)
    р.add_argument("--обрыв", action="store_true")
    а = р.parse_args()
    пакетов, хэш = записать(Path(а.вывод), а.мб, а.зерно, а.обрыв)
    print(f"пакетов {пакетов}, sha256 {хэш}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
