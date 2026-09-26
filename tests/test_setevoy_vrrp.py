# -*- coding: utf-8 -*-
"""VRRP версий 2 (RFC 3768) и 3 (RFC 5798): поля, адреса IPv4/IPv6, контрольная сумма.

Сумма вписывается здесь же по RFC 1071 — у версии 2 по сообщению, у версии 3
с псевдозаголовком IP (и без него — так считают часть реализаций VRRPv3
поверх IPv4).
"""

import struct
import unittest

import _bootstrap  # noqa: F401
import setevoy_sintez as с
from reportgen.setevoy import разобрать_пакет


def с_суммой(данные, заголовок=b""):
    данные = данные[:6] + b"\0\0" + данные[8:]
    return данные[:6] + struct.pack(">H", с.сумма(заголовок + данные)) + данные[8:]


def псевдо4(длина, src="10.0.0.1", dst="224.0.0.18"):
    return с.a4(src) + с.a4(dst) + struct.pack(">BBH", 0, 112, длина)


def псевдо6(длина, src="fe80::1", dst="ff02::12"):
    return с.a6(src) + с.a6(dst) + struct.pack(">IxxxB", длина, 112)


def vrrp2(адреса, vrid=7, приоритет=100, интервал=1):
    тело = struct.pack(">BBBBBBH", 0x21, vrid, приоритет, len(адреса), 0, интервал, 0)
    return с_суммой(тело + b"".join(с.a4(а) for а in адреса) + bytes(8))


def vrrp3(адреса, v6=False, vrid=9, приоритет=200, интервал=100, псевдо=True):
    тело = struct.pack(">BBBBHH", 0x31, vrid, приоритет, len(адреса), интервал & 0x0FFF, 0)
    тело += b"".join((с.a6 if v6 else с.a4)(а) for а in адреса)
    заголовок = (псевдо6 if v6 else псевдо4)(len(тело)) if псевдо else b""
    return с_суммой(тело, заголовок)


def поля(п):
    итог = {}

    def обойти(список):
        for ф in список:
            итог.setdefault(ф.ключ, []).append(ф)
            обойти(ф.дети)

    for у in п.уровни:
        обойти(у.поля)
    return итог


def пакет4(сообщение):
    return разобрать_пакет(с.eth(с.ip(сообщение, 112, dst="224.0.0.18")))


def пакет6(сообщение):
    return разобрать_пакет(с.eth(с.ip6(сообщение, 112, src="fe80::1", dst="ff02::12"), тип=0x86DD))


class VrrpTests(unittest.TestCase):
    def test_версия_2(self):
        п = пакет4(vrrp2(["10.0.0.254", "10.0.0.253"]))
        ф = поля(п)
        self.assertEqual(ф["vrrp.version"][0].сырое, 2)
        self.assertEqual([x.текст for x in ф["vrrp.ip_addr"]], ["10.0.0.254", "10.0.0.253"])
        self.assertEqual(ф["vrrp.auth_type"][0].текст, "нет")
        self.assertEqual(ф["vrrp.adver_int"][0].сырое, 1)
        self.assertIn("vrrp.auth_data", ф)
        self.assertIn("верна, по сообщению", ф["vrrp.checksum"][0].текст)
        self.assertFalse(ф["vrrp.checksum"][0].плохо)
        self.assertIn("VRID 7, приоритет 100", п.инфо)

    def test_версия_3_ipv4_с_псевдозаголовком_и_без(self):
        for псевдо, способ in ((True, "с псевдозаголовком"), (False, "без псевдозаголовка")):
            with self.subTest(псевдо=псевдо):
                ф = поля(пакет4(vrrp3(["192.0.2.1"], псевдо=псевдо)))
                self.assertEqual(ф["vrrp.max_adver_int"][0].сырое, 100)
                self.assertEqual([x.текст for x in ф["vrrp.ip_addr"]], ["192.0.2.1"])
                self.assertIn(f"верна, {способ}", ф["vrrp.checksum"][0].текст)
                self.assertNotIn("vrrp.auth_type", ф)

    def test_версия_3_ipv6_адреса_по_16_байт(self):
        п = пакет6(vrrp3(["fe80::1", "2001:db8::10"], v6=True))
        ф = поля(п)
        self.assertEqual([x.текст for x in ф["vrrp.ip_addr"]], ["fe80::1", "2001:db8::10"])
        self.assertEqual([x.длина for x in ф["vrrp.ip_addr"]], [16, 16])
        self.assertIn("верна, с псевдозаголовком", ф["vrrp.checksum"][0].текст)

    def test_испорченная_сумма(self):
        for сообщение in (bytearray(vrrp3(["192.0.2.1"])), bytearray(vrrp2(["10.0.0.254"]))):
            with self.subTest(версия=сообщение[0] >> 4):
                сообщение[2] ^= 1
                п = пакет4(bytes(сообщение))
                ф = поля(п)
                self.assertIn("не сошлась", ф["vrrp.checksum"][0].текст)
                self.assertTrue(ф["vrrp.checksum"][0].плохо)
                self.assertTrue(any("VRRP" in о for о in п.ошибки))


if __name__ == "__main__":
    unittest.main()
