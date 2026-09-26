"""ATM: нагрузка ячеек под скремблером x⁴³ + 1 (I.432.1) и инкапсуляция RFC 2684 (LLC/SNAP).

Ячейки собираются построителем potok_sintez.atm (AAL5, HEC), нагрузки — скремблером
x⁴³ + 1 сплошь по ячейкам (заголовок не скремблируется, состояние сохраняется), как в
I.432.1; IP — за заголовком LLC/SNAP AA-AA-03-00-00-00-08-00 (RFC 2684, 4.1).
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import gfp, pakety
from reportgen.potok.bity import в_биты

LLC_IP = bytes.fromhex("aaaa030000000800")
МОСТ = bytes.fromhex("aaaa030080c20007") + b"\0\0"


def ip(н):
    return с.udp("10.9.0.1", "10.9.0.2", 7000 + н, 53, bytes([н]) * (40 + н))


def скремблировать_нагрузки(ячейки: bytes) -> bytes:
    массив = np.frombuffer(ячейки, dtype=np.uint8).reshape(-1, 53).copy()
    нагрузки = np.unpackbits(массив[:, 5:].reshape(-1))
    массив[:, 5:] = np.packbits(с.скремблировать(нагрузки, (43,))).reshape(-1, 48)
    return массив.tobytes()


class AtmTests(unittest.TestCase):
    def test_llc_snap_без_скремблера(self):
        найдено = gfp.найти(в_биты(b"\0" * 7 + с.atm([LLC_IP + ip(н) for н in range(40)])))
        текст = "\n".join(найдено.подробно)
        self.assertIn("CRC-32 сошлась у 40", текст)
        self.assertIn("инкапсуляция (RFC 2684): LLC/SNAP, IPv4×40", текст)
        self.assertNotIn("x⁴³", текст)
        внутри = pakety.найти_в_кадрах(найдено.дальше)
        self.assertEqual({"LLC/SNAP"}, {п.обёртка for п in внутри.дальше})
        self.assertEqual(40, len(внутри.дальше))

    def test_нагрузка_под_x43(self):
        ячейки = скремблировать_нагрузки(с.atm([LLC_IP + ip(н) for н in range(40)]))
        найдено = gfp.найти(в_биты(b"\0" * 3 + ячейки))
        текст = "\n".join(найдено.подробно)
        self.assertIn("нагрузка ячеек — самосинхронизирующийся скремблер x⁴³ + 1 снят (I.432.1)", текст)
        # Первая ячейка — без начала регистра: её PDU может не сойтись, остальные — сходятся.
        self.assertRegex(текст, r"CRC-32 сошлась у (39|40)")
        self.assertEqual(ip(5), pakety.в_кадре(найдено.дальше[5 - (40 - len(найдено.дальше))]).сырые)

    def test_испорченные_pdu_без_скремблера_не_снимают_x43(self):
        # CRC-32 не сходится у большинства PDU, и снятие x⁴³ этого не лечит — нагрузка остаётся как есть.
        ячейки = bytearray(с.atm([LLC_IP + ip(н) for н in range(30)]))
        for м in range(5 + 20, len(ячейки), 53 * 3):
            ячейки[м] ^= 0xFF
        текст = "\n".join(gfp.найти(в_биты(bytes(ячейки))).подробно)
        self.assertNotIn("x⁴³", текст)

    def test_мост_и_vc_mux(self):
        eth = bytes.fromhex("0011223344550066778899aa0800")
        найдено = gfp.найти(в_биты(с.atm([МОСТ + eth + ip(1), ip(2), b"\x01\x02\x03"] * 12)))
        текст = "\n".join(найдено.подробно)
        self.assertIn("LLC/SNAP, мост (Ethernet)×12", текст)
        self.assertIn("VC-mux, IP без заголовка×12", текст)
        self.assertIn("другая×12", текст)
        self.assertEqual("LLC/SNAP, мост", pakety.в_кадре(МОСТ + eth + ip(1)).обёртка)
        # Мостовой кадр не IPv4 — не пакет.
        self.assertIsNone(pakety.в_кадре(МОСТ + eth[:12] + b"\x88\x47" + ip(1)))
        self.assertIsNone(pakety.в_кадре(bytes.fromhex("aaaa030000000806") + ip(1)))


if __name__ == "__main__":
    unittest.main()
