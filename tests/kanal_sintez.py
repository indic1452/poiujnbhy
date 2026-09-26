"""Кадры канальных протоколов по стандартам — для проверок классификатора и передачи в разборщики.

MTP2 (Q.703), LAPD (Q.921) с Q.931, Frame Relay (Q.922, RFC 2427) с IP и LMI,
LAPB (X.25) с пакетами X.25, AX.25 (v2.2) с кадрами UI. Кадры без FCS —
как их отдаёт снятие битстаффинга.
"""

import random

import potok_sintez as с
from test_setevoy_oks7 import isup_iam, q931_setup, sccp_udt, tcap_end_result, адрес_pc, метка


def mtp2(сколько=300, сид=1):
    """FISU, LSSU и MSU (ISUP IAM и SCCP UDT с TCAP) с растущими FSN/BSN."""
    г = random.Random(сид)
    кадры = []
    for н in range(сколько):
        bsn, fsn = (н // 2) & 0x7F, н & 0x7F
        вид = г.choice(("fisu", "fisu", "lssu", "isup", "sccp"))
        if вид == "fisu":
            тело = b""
        elif вид == "lssu":
            тело = bytes([г.choice((0, 1, 2, 3))])
        elif вид == "isup":
            тело = bytes([0x85]) + метка(1234, 567, н & 15) + isup_iam(cic=н & 0xFFF)
        else:
            тело = bytes([0x83]) + метка(1234, 567, н & 15) + sccp_udt(адрес_pc(1234, 6), адрес_pc(567, 7),
                                                                       tcap_end_result())
        li = min(len(тело), 63)
        кадры.append(bytes([bsn | 0x80, fsn | 0x80, li]) + тело)
    return кадры


def _lapd_адрес(sapi, tei, команда=True):
    return bytes([(sapi << 2) | (0 if команда else 2), (tei << 1) | 1])


def lapd(сколько=200, сид=2, sapi=0, tei=0):
    """SABME/UA, затем кадры I с Q.931 SETUP и RR — как в D-канале ISDN PRI."""
    г = random.Random(сид)
    кадры = [_lapd_адрес(sapi, tei) + b"\x7f", _lapd_адрес(sapi, tei, False) + b"\x73"]
    ns = 0
    for _ in range(сколько - 2):
        if г.random() < 0.6:
            кадры.append(_lapd_адрес(sapi, tei) + bytes([(ns & 0x7F) << 1, 0]) + q931_setup())
            ns += 1
        else:
            кадры.append(_lapd_адрес(sapi, tei, False) + bytes([0x01, ((ns & 0x7F) << 1) | 1]))
    return кадры


def fr_адрес(dlci):
    return bytes([(dlci >> 4) << 2, ((dlci & 0x0F) << 4) | 1])


def fr(сколько=200, сид=3, dlci=100):
    """IP по RFC 2427 (UI 0x03, NLPID 0xCC) и LMI на DLCI 0 (STATUS ENQUIRY)."""
    г = random.Random(сид)
    кадры = []
    for н in range(сколько):
        if н % 10 == 0:
            кадры.append(fr_адрес(0) + bytes([0x03, 0x08, 0x00, 0x75, 0x51, 0x01, 0x01,
                                               0x53, 0x02, н & 0xFF, 0x00]))
        else:
            пакет = с.udp("10.1.0.1", "10.2.0.2", 5000 + н, 53, bytes(г.getrandbits(8) for _ in range(20)))
            кадры.append(fr_адрес(dlci) + b"\x03\xcc" + пакет)
    return кадры


def lapb(сколько=200, сид=4):
    """SABM/UA, кадры I с пакетами данных X.25 (модуль 8) и RR."""
    г = random.Random(сид)
    кадры = [b"\x03\x3f", b"\x03\x73"]
    ns = 0
    for _ in range(сколько - 2):
        if г.random() < 0.7:
            x25 = bytes([0x10, 0x01, ((ns & 7) << 1)]) + bytes(г.getrandbits(8) for _ in range(30))
            кадры.append(bytes([0x03, ((ns & 7) << 1)]) + x25)
            ns += 1
        else:
            кадры.append(bytes([0x01, 0x01 | ((ns & 7) << 5)]))
    return кадры


def _позывной(текст, ssid, последний):
    return bytes(ord(ч) << 1 for ч in текст.ljust(6)) + bytes([0x60 | (ssid << 1) | последний])


def ax25(сколько=100, сид=5):
    """UI (0x03), PID 0xF0 — пакеты APRS."""
    г = random.Random(сид)
    return [_позывной("APRS", 0, 0) + _позывной("N0CALL", н % 16, 1) + b"\x03\xf0"
            + f"!5545.00N/03737.00E-{г.randint(0, 999)}".encode() for н in range(сколько)]


def случайные(сколько=2000, сид=9, от_=3, до=120):
    г = random.Random(сид)
    return [bytes(г.getrandbits(8) for _ in range(г.randint(от_, до))) for _ in range(сколько)]


def поток_hdlc(кадры):
    """Кадры → биты HDLC с битстаффингом и FCS-16."""
    return с.hdlc(кадры)
