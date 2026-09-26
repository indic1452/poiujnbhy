"""CCSDS: кадры TM и AOS по счётчику кадров, FECF, CLCW, Space Packet через границы кадров.

Кадры собираются по CCSDS 132.0-B / 732.0-B / 133.0-B (как их описывают
gr-satellites и Wireshark) и разбираются обратно.
"""

import random
import unittest

import _bootstrap  # noqa: F401
from reportgen.potok import ccsds, разобрать


def space_packet(apid, счёт, данные, тип=0):
    return ((тип << 12) | apid).to_bytes(2, "big") + ((0b11 << 14) | (счёт & 0x3FFF)).to_bytes(2, "big") \
        + (len(данные) - 1).to_bytes(2, "big") + данные


def кадры_tm(пакеты, *, длина=223, scid=0x2A5, vc=(1,), ocf=True, fecf=True, clcw=0x01040000, начать=0,
             пропуск=(), вторичный=b"", scid_кадра=None, состояние=0b11 << 11):
    """Кадры TM подряд: пакеты — через границы кадров, FHP — место первого заголовка (0x7FF — нет).

    ``вторичный`` — тело вторичного заголовка (первый байт — версия и длина − 1 — добавляется),
    ``scid_кадра(номер)`` — свой SCID кадра, ``состояние`` — флаги слова состояния без FHP."""
    поток = b"".join(пакеты)
    вторичный = bytes([len(вторичный)]) + вторичный if вторичный else b""
    полезных = длина - 6 - len(вторичный) - (4 if ocf else 0) - (2 if fecf else 0)
    начала, м = set(), 0
    for п in пакеты:
        начала.add(м)
        м += len(п)
    итог, место, mcfc = [], 0, начать
    номер = 0
    while место < len(поток):
        кусок = поток[место:место + полезных]
        кусок += b"\x55" * (полезных - len(кусок))
        fhp = next((н - место for н in sorted(начала) if место <= н < место + полезных), 0x7FF)
        вк = vc[номер % len(vc)]
        свой = scid_кадра(номер) if scid_кадра else scid
        заголовок = ((свой << 4) | (вк << 1) | int(ocf)).to_bytes(2, "big") + bytes([mcfc & 0xFF, номер & 0xFF]) \
            + ((0x8000 if вторичный else 0) | состояние | fhp).to_bytes(2, "big") + вторичный
        кадр = заголовок + кусок + (clcw.to_bytes(4, "big") if ocf else b"")
        if fecf:
            кадр += ccsds.crc16_ccitt(кадр).to_bytes(2, "big")
        if номер not in пропуск:
            итог.append(кадр)
        место += полезных
        mcfc += 1
        номер += 1
    return итог


def пакеты(сколько, сид=1):
    г = random.Random(сид)
    return [space_packet(г.choice((100, 200, 0x7FF)), н, bytes(г.getrandbits(8) for _ in range(г.randint(20, 400))))
            for н in range(сколько)]


class CcsdsTests(unittest.TestCase):
    def test_crc16_контрольное(self):
        self.assertEqual(0x29B1, ccsds.crc16_ccitt(b"123456789"))      # CRC-16/IBM-3740 (CCITT-FALSE)

    def test_tm_кадры_и_пакеты(self):
        п = пакеты(60)
        данные = b"\xa5" * 37 + b"".join(кадры_tm(п))                  # поток начинается не с кадра
        р = ccsds.разобрать(данные)
        self.assertEqual(("TM", 223, 37), (р.вид, р.длина, р.начало))
        self.assertTrue(р.fecf)
        self.assertEqual(len(р.кадры), р.fecf_верна)
        self.assertEqual({0x2A5}, {к.scid for к in р.кадры})
        self.assertEqual(0, р.пропущено)
        self.assertEqual(п[:len(р.пакеты)], р.пакеты)
        self.assertGreater(len(р.пакеты), 50)
        self.assertEqual(0x01040000, р.кадры[0].clcw)

    def test_находка(self):
        н = ccsds.найти(b"".join(кадры_tm(пакеты(60), vc=(1, 3))))
        текст = "\n".join(н.подробно)
        # MCFC общий для всех виртуальных каналов: чередование VC — не пропуски.
        self.assertIn("пропущено по счётчику MCFC: 0", текст)
        self.assertEqual("CCSDS TM: кадры канала передачи данных и Space Packet", н.что)
        self.assertIn("кадр 223 байт с байта 0; SCID 677 (0x2A5); виртуальные каналы: VC 1×", текст)
        self.assertIn("VC 3×", текст)
        self.assertIn("FECF (CRC-16 CCITT) верна у", текст)
        self.assertIn("OCF — CLCW: VC 1", текст)
        self.assertRegex(текст, r"APID: .*100×")
        self.assertIn("2047 (заполнение)×", текст)

    def test_пропуски_и_без_fecf(self):
        п = пакеты(40)
        р = ccsds.разобрать(b"".join(кадры_tm(п, fecf=False, ocf=False, пропуск=(5, 6, 20))))
        self.assertFalse(р.fecf)
        self.assertEqual(3, р.пропущено)
        self.assertIsNone(р.кадры[0].clcw)
        # После пропуска пакеты ловятся снова по FHP; целые — те же, что посланы.
        self.assertTrue(set(р.пакеты) <= set(п))
        self.assertGreater(len(р.пакеты), 25)
        self.assertEqual(0, р.неполных)

    def test_вторичный_заголовок(self):
        п = пакеты(50)
        р = ccsds.разобрать(b"".join(кадры_tm(п, вторичный=b"\x01\x02\x03")))
        self.assertGreater(len(р.пакеты), 40)
        self.assertEqual(п[:len(р.пакеты)], р.пакеты)

    def test_кадр_только_с_заполнением_не_рвёт_пакет(self):
        п = пакеты(50)
        кадры = кадры_tm(п, fecf=False)
        # Между кадрами 5 и 6 — кадр заполнения того же VC (FHP 0x7FE): VCFC и MCFC идут подряд.
        def перенумеровать(кадр, номер):
            return кадр[:2] + bytes([номер & 0xFF, номер & 0xFF]) + кадр[4:]
        заполнение = кадры[5][:4] + (0x1800 | 0x7FE).to_bytes(2, "big") + b"\x55" * (223 - 6 - 4) + кадры[5][-4:]
        кадры = кадры[:6] + [заполнение] + кадры[6:]
        кадры = [перенумеровать(к, н) for н, к in enumerate(кадры)]
        р = ccsds.разобрать(b"".join(кадры))
        self.assertEqual(0, р.пропущено)
        self.assertEqual(п[:len(р.пакеты)], р.пакеты)
        self.assertGreater(len(р.пакеты), 40)

    def test_неверный_fhp_ловится_по_версии(self):
        п = пакеты(50)
        кадры = кадры_tm(п, fecf=False)
        # FHP кадра 0 указывает в заполнение 0x55: версия 010 — разметка потеряна, пакет не выдаётся.
        кадры[0] = кадры[0][:4] + ((0b11 << 11) | 150).to_bytes(2, "big") + кадры[0][6:156] + b"\x55" * 6 + кадры[0][162:]
        р = ccsds.разобрать(b"".join(кадры))
        self.assertGreaterEqual(р.неполных, 1)
        self.assertTrue(set(р.пакеты) <= set(п))
        self.assertGreater(len(р.пакеты), 30)

    def test_fecf_верна_у_большинства(self):
        кадры = кадры_tm(пакеты(60))
        for н in range(0, len(кадры), 3):                       # треть кадров с ошибкой
            кадры[н] = кадры[н][:-1] + bytes([кадры[н][-1] ^ 1])
        р = ccsds.разобрать(b"".join(кадры))
        self.assertTrue(р.fecf)
        self.assertEqual(len(кадры) - len(range(0, len(кадры), 3)), р.fecf_верна)

    def test_без_fecf_заголовки_должны_быть_как_у_ccsds(self):
        п = пакеты(60)
        # SCID у каждого кадра свой — не CCSDS.
        self.assertIsNone(ccsds.разобрать(b"".join(кадры_tm(п, fecf=False, scid_кадра=lambda н: (н * 37) & 0x3FF))))
        # У трети кадров чужой SCID — меньше 90 % годных — не CCSDS.
        self.assertIsNone(ccsds.разобрать(b"".join(кадры_tm(
            п, fecf=False, scid_кадра=lambda н: 0x100 + н if н % 3 == 0 else 0x2A5))))
        # Флаг синхронизации 0, а длина сегмента не 11 — не TM по 132.0-B.
        self.assertIsNone(ccsds.разобрать(b"".join(кадры_tm(п, fecf=False, состояние=0))))
        self.assertIsNotNone(ccsds.разобрать(b"".join(кадры_tm(п, fecf=False, состояние=0x4000))))

    def test_порог_счётчика(self):
        г = random.Random(8)

        def поток(доля):
            счёт, кадры = 0, []
            for _ in range(200):
                счёт += 1 if г.random() < доля else 0
                кадры.append(bytes([0x0A, 0x52, счёт & 0xFF, 0, 0x1F, 0xFF]) + bytes(г.getrandbits(8) for _ in range(122)))
            return b"".join(кадры)
        self.assertIsNone(ccsds.счётчик_кадров(поток(0.6)))
        self.assertEqual(128, ccsds.счётчик_кадров(поток(0.97))[0])

    def test_счётчик_переходит_через_255(self):
        р = ccsds.разобрать(b"".join(кадры_tm(пакеты(40), длина=128, начать=250)))
        self.assertEqual((128, 0), (р.длина, р.пропущено))

    def test_aos(self):
        # AOS: версия 01, SCID 8 бит, VCID 6, VCFC 24 бита, сигнализация, M_PDU с FHP.
        п = пакеты(40)
        поток = b"".join(п)
        полезных = 256 - 8 - 2
        кадры, место, счёт = [], 0, 0x00FFFE
        начала = set()
        м = 0
        for пакет in п:
            начала.add(м)
            м += len(пакет)
        while место < len(поток):
            кусок = поток[место:место + полезных].ljust(полезных, b"\x55")
            fhp = next((н - место for н in sorted(начала) if место <= н < место + полезных), 0x7FF)
            к = ((1 << 14) | (0x7B << 6) | 45).to_bytes(2, "big") + (счёт & 0xFFFFFF).to_bytes(3, "big") + b"\x00" \
                + (fhp & 0x7FF).to_bytes(2, "big") + кусок
            кадры.append(к + ccsds.crc16_ccitt(к).to_bytes(2, "big"))
            место += полезных
            счёт += 1
        р = ccsds.разобрать(b"".join(кадры))
        self.assertEqual(("AOS", 256), (р.вид, р.длина))
        self.assertEqual({(0x7B, 45)}, {(к.scid, к.vcid) for к in р.кадры})
        self.assertEqual(0, р.пропущено)                               # 24-битный счётчик через 0xFFFFFF
        self.assertGreater(len(р.пакеты), 30)
        self.assertEqual(п[:len(р.пакеты)], р.пакеты)
        # Счётчик прибавил 257 лишних — пропущено 257 кадров (у 24-битного счётчика — не по модулю 256).
        прыжок = [к[:2] + ((int.from_bytes(к[2:5], "big") + (257 if н >= 10 else 0)) & 0xFFFFFF).to_bytes(3, "big")
                  + к[5:-2] for н, к in enumerate(кадры)]
        прыжок = [к + ccsds.crc16_ccitt(к).to_bytes(2, "big") for к in прыжок]
        self.assertEqual(257, ccsds.разобрать(b"".join(прыжок)).пропущено)

    def test_случайные_не_ccsds(self):
        self.assertIsNone(ccsds.найти(random.Random(3).randbytes(1 << 15)))

    def test_кадры_со_счётчиком_но_не_ccsds(self):
        # Любые кадры со счётчиком (без FECF): заголовок не как у CCSDS — не CCSDS.
        г = random.Random(4)
        кадры = [bytes([г.getrandbits(8) & 0x3F, г.getrandbits(8), н & 0xFF]) + bytes(г.getrandbits(8) for _ in range(197))
                 for н in range(150)]
        self.assertIsNone(ccsds.разобрать(b"".join(кадры)))
        # Тот же поток, но SCID один и слово состояния как у TM, — CCSDS TM.
        кадры = [bytes([0x0A, 0x52, н & 0xFF, н & 0xFF, 0x1F, 0xFF]) + bytes(г.getrandbits(8) for _ in range(194))
                 for н in range(150)]
        self.assertEqual("TM", ccsds.разобрать(b"".join(кадры)).вид)


class АвтоматTests(unittest.TestCase):
    def test_разбор_потока_доходит_до_space_packet(self):
        данные = b"".join(кадры_tm(пакеты(120)))
        разбор = разобрать(данные=данные, профиль="быстро")
        self.assertIn("CCSDS TM: кадры канала передачи данных и Space Packet",
                      " | ".join(н.что for н in разбор.находки))


if __name__ == "__main__":
    unittest.main()
