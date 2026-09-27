"""Поток E1 из псевдопроводов в захвате: SAToP поверх MPLS, CESoPSN поверх UDP с RTP, MEF 8.

Порядок заголовков — как в Wireshark packet-pw-satop.c: в MPLS за меткой — слово, затем (если
есть) RTP; в UDP — RTP, затем слово. Слово — RFC 4385/4553: 0000 L R RSV, FRG, LEN, номер 16 бит.
"""

import random
import struct
import unittest

import numpy as np

import _bootstrap  # noqa: F401
import kanal_sintez as к
import potok_sintez as с
import setevoy_sintez as сс
from reportgen.potok import pw_tdm, разобрать
from reportgen.setevoy.chtenie import прочитать_захват


def mpls(метка):
    return struct.pack(">I", (метка << 12) | (1 << 8) | 64)


def cw(номер):
    return struct.pack(">HH", 0, номер & 0xFFFF)


def rtp(номер):
    return bytes([0x80, 96]) + struct.pack(">HII", номер & 0xFFFF, номер * 8, 0x1234)


def e1_с_окс7(циклов=6000):
    окс7 = к.поток_hdlc(к.mtp2(600))
    г = random.Random(1)

    def байт(цикл, ки):
        if ки == 16:
            return окс7[цикл % len(окс7)]
        return г.randrange(256)
    return с.e1(циклов, каналы=байт)


def satop_mpls(поток, кадров=8, метка=1001, начало=65500, пропуск=(), перестановка=True):
    """Пакеты SAToP: по ``кадров`` × 32 байта E1; номера с переходом через 65535."""
    размер = 32 * кадров
    пакеты = []
    for i in range(len(поток) // размер):
        if i in пропуск:
            continue
        кусок = поток[i * размер:(i + 1) * размер]
        пакеты.append(сс.eth(mpls(метка) + cw(начало + i) + кусок, тип=0x8847))
    if перестановка:
        пакеты[10], пакеты[11] = пакеты[11], пакеты[10]
    return пакеты


class СборкаTests(unittest.TestCase):
    def test_переход_номера_и_пропуск(self):
        куски = [(65534, b"a"), (65535, b"b"), (1, b"d"), (0, b"c"), (3, b"f")]
        поток, пропущено, повторов = pw_tdm.собрать(куски)
        self.assertEqual(b"abcd\xfff", поток)
        self.assertEqual((1, 0), (пропущено, повторов))

    def test_повтор_не_удваивает(self):
        поток, пропущено, повторов = pw_tdm.собрать([(5, b"x"), (6, b"y"), (6, b"y"), (7, b"z")])
        self.assertEqual((b"xyz", 0, 1), (поток, пропущено, повторов))


class SatopTests(unittest.TestCase):
    def test_e1_бит_в_бит(self):
        поток = с.e1(2000)
        записи = прочитать_захват(данные=сс.pcap(satop_mpls(поток))).записи
        найдено = pw_tdm.цепи(записи)
        self.assertEqual(1, len(найдено))
        цепь, собрано, пропущено, повторов = найдено[0]
        self.assertEqual("MPLS, метки 1001", цепь.имя)
        self.assertEqual((0, 0), (пропущено, повторов))
        self.assertEqual(поток[:len(собрано)], собрано)

    def test_пропуск_заполняется_единицами(self):
        поток = с.e1(2000)
        записи = прочитать_захват(данные=сс.pcap(satop_mpls(поток, пропуск=(20,)))).записи
        _, собрано, пропущено, _ = pw_tdm.цепи(записи)[0]
        self.assertEqual(1, пропущено)
        self.assertEqual(b"\xff" * 256, собрано[20 * 256:21 * 256])
        self.assertEqual(поток[21 * 256:len(собрано)], собрано[21 * 256:])

    def test_разбор_потока_доходит_до_окс7(self):
        """pcap с SAToP → поток E1 → FAS → КИ16 → HDLC → MTP2."""
        поток = e1_с_окс7()
        разбор = разобрать(данные=сс.pcap(satop_mpls(поток)), профиль="быстро")
        текст = " | ".join(f"{н.что} @ {н.путь}" for н in разбор.находки)
        self.assertIn("псевдопроводы TDM (SAToP / CESoPSN / MEF 8): цепей 1", текст)
        self.assertIn("ОКС-7, MTP2 (Q.703)", текст)
        self.assertIn("MPLS, метки 1001", текст)


class ГраницыTests(unittest.TestCase):
    def test_байт_0x80_в_данных_не_rtp(self):
        """Нагрузка начинается с 0x80 0x00, но номер «RTP» не совпадает со словом — это данные."""
        г = random.Random(4)
        куски = [bytes([0x80, 0x00]) + bytes(г.randrange(256) for _ in range(254)) for _ in range(40)]
        пакеты = [сс.eth(mpls(7) + cw(i) + к_, тип=0x8847) for i, к_ in enumerate(куски)]
        _, собрано, _, _ = pw_tdm.цепи(прочитать_захват(данные=сс.pcap(пакеты)).записи)[0]
        self.assertEqual(b"".join(куски), собрано)

    def test_длина_в_слове_отсекает_набивку(self):
        """CESoPSN 5 КИ × 4 кадра = 20 байт < 64: LEN = 24 (слово + нагрузка), дальше — набивка кадра."""
        г = random.Random(5)
        куски = [bytes(г.randrange(256) for _ in range(20)) for _ in range(40)]
        пакеты = [сс.eth(mpls(8) + struct.pack(">BBH", 0, 24, i) + к_ + bytes(22), тип=0x8847)
                  for i, к_ in enumerate(куски)]
        _, собрано, _, _ = pw_tdm.цепи(прочитать_захват(данные=сс.pcap(пакеты)).записи)[0]
        self.assertEqual(b"".join(куски), собрано)

    def test_повтор_номера_берётся_первый(self):
        поток, _, повторов = pw_tdm.собрать([(1, b"a"), (2, b"b"), (2, b"X"), (3, b"c")])
        self.assertEqual((b"abc", 1), (поток, повторов))

    def test_udp_номера_вразброс_не_tdm(self):
        """Слово с нулями в битах 0–3 и одна длина, но номера не подряд — не псевдопровод."""
        г = random.Random(6)
        пакеты = [сс.eth(сс.ip(сс.udp(struct.pack(">HH", 0, г.randrange(65536)) + bytes(64), 7000, 7001), 17))
                  for _ in range(100)]
        self.assertEqual([], pw_tdm.цепи(прочитать_захват(данные=сс.pcap(пакеты)).записи))


class UdpTests(unittest.TestCase):
    def test_cesopsn_udp_с_rtp(self):
        """CESoPSN 4 КИ × 8 кадров = 32 байта; в UDP сначала RTP, затем слово (номера совпадают)."""
        г = random.Random(2)
        данные = bytes(г.randrange(256) for _ in range(32 * 300))
        пакеты = [сс.eth(сс.ip(сс.udp(rtp(i) + cw(i) + данные[32 * i:32 * (i + 1)], 49152, 49153), 17))
                  for i in range(300)]
        найдено = pw_tdm.цепи(прочитать_захват(данные=сс.pcap(пакеты)).записи)
        self.assertEqual(1, len(найдено))
        цепь, собрано, пропущено, _ = найдено[0]
        self.assertTrue(цепь.rtp)
        self.assertEqual("UDP 10.0.0.1:49152 → 10.0.0.2:49153", цепь.имя)
        self.assertEqual(данные, собрано)

    def test_обычный_udp_не_tdm(self):
        """Датаграммы одной длины, но номера не подряд и старшие биты не нули — не псевдопровод."""
        г = random.Random(3)
        пакеты = [сс.eth(сс.ip(сс.udp(bytes(г.randrange(256) for _ in range(100)), 5000, 6000), 17))
                  for _ in range(200)]
        self.assertEqual([], pw_tdm.цепи(прочитать_захват(данные=сс.pcap(пакеты)).записи))

    def test_мало_пакетов(self):
        поток = с.e1(100)
        записи = прочитать_захват(данные=сс.pcap(satop_mpls(поток, перестановка=False)[:10])).записи
        self.assertEqual([], pw_tdm.цепи(записи))


class Mef8Tests(unittest.TestCase):
    def test_cesoeth(self):
        поток = с.e1(1000)
        пакеты = [сс.eth(struct.pack(">I", (0xABCDE << 12) | 0x102) + cw(i) + поток[64 * i:64 * (i + 1)], тип=0x88D8)
                  for i in range(len(поток) // 64)]
        найдено = pw_tdm.найти(прочитать_захват(данные=сс.pcap(пакеты)).записи)
        self.assertIn("MEF 8, ECID 0xabcde", найдено.дальше)
        ряд = найдено.дальше["MEF 8, ECID 0xabcde"]
        self.assertTrue(np.array_equal(np.unpackbits(np.frombuffer(поток[:len(ряд) // 8], np.uint8)), ряд))
        self.assertIn("кратно 32 байтам — E1 целиком (SAToP), 2 кадров G.704 на пакет", найдено.подробно[0])


if __name__ == "__main__":
    unittest.main()
