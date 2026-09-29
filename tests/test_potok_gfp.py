"""GFP (G.7041) с любой маской основного заголовка: слепой поиск маски, выделение кадров
HUNT → PRESYNC → SYNC по всему потоку, нагрузка (x⁴³ + 1, tHEC, CID, pFCS, CMF, GFP-T),
выходы (кадры клиентов в pcap, поток, вложенный GFP), встраивание (ручной слой, автомат,
синхрослова, конфигурации, анализатор пакетов, окно стола).

Потоки строит эталонный генератор ``gfp_sintez`` — CRC и скремблер там свои, побитно.
"""

import json
import shutil
import struct
import subprocess
import time
import unittest
import zlib

import numpy as np

import _bootstrap  # noqa: F401
import gfp_sintez as г
import potok_sintez as с
from gfp_zahvat import КАДРЫ as ЗАХВАТ
from reportgen.potok import crc as crc_
from reportgen.potok import crc_katalog, gfp, gfp_tablicy, kanal, konfig, rastr, sdh, sinhro
from reportgen.potok.razbor import Ветвь, _вложенный_gfp, разобрать, снять_вручную
from reportgen.potok.zadaniya import выгрузка
from reportgen.setevoy import прочитать_захват, разобрать_пакет

ПАКЕТЫ = с.пакеты_ip(80)
ETHERNET = [г.ethernet_с_fcs(с.ethernet(п)) for п in ПАКЕТЫ]
МАСКИ = (0xB6AB31E0, 0x000119D8, 0xB6AB3325, 0x5A17C3E9)


def кадры_эталона(сколько: int = 40, пустых: int = 2, **нагрузка) -> list:
    итог = []
    for п in ETHERNET[:сколько]:
        итог += [None] * пустых + [г.нагрузка(п, **нагрузка)]
    return итог


def клиенты(р) -> list[bytes]:
    return [к.данные for к in р.клиенты()]


class МаскаTests(unittest.TestCase):
    def test_синдром_одинаков_у_всех_заголовков(self):
        # s = cHEC ⊕ CRC0(PLI) на линии — Mc ⊕ CRC0(Mp) у любого заголовка любой маски.
        for маска in МАСКИ:
            with self.subTest(маска=hex(маска)):
                кадры = кадры_эталона(12)
                данные = г.поток(кадры, маска)
                с_ = gfp.синдромы(np.frombuffer(данные, dtype=np.uint8))
                ждём = (маска & 0xFFFF) ^ г.crc16((маска >> 16).to_bytes(2, "big"))
                self.assertEqual({ждём}, {int(с_[м]) for м in г.места_заголовков(кадры)})
                self.assertEqual(ждём, gfp.синдром_маски(маска))
        # Синдромы трёх масок из сводки источников — пересчитаны генератором выше.
        self.assertEqual([0x81CA, 0x09F9, 0x830F], [gfp.синдром_маски(м) for м in МАСКИ[:3]])

    def test_маска_любая_при_любом_сдвиге_порядке_и_инверсии(self):
        кадры = кадры_эталона(40)
        for маска in МАСКИ:
            данные = г.поток(кадры, маска)
            for сдвиг, порядок, инверсия in ((0, "старший", False), (3, "старший", False), (5, "младший", False),
                                             (6, "старший", True)):
                with self.subTest(маска=hex(маска), сдвиг=сдвиг, порядок=порядок, инверсия=инверсия):
                    р = gfp.разобрать(г.в_ряд(данные, сдвиг=сдвиг, порядок=порядок, инверсия=инверсия))
                    self.assertEqual(маска ^ (0xFFFFFFFF if инверсия else 0), р.маска)
                    self.assertEqual((сдвиг, порядок, "x43"), (р.сдвиг, р.порядок, р.скремблер))
                    self.assertEqual(len(кадры), len(р.выделение.места))
                    self.assertEqual([сдвиг + 8 * м for м in г.места_заголовков(кадры)],
                                     [р.бит(м) for м in р.выделение.места])
                    # Первый кадр с нагрузкой — в прогреве x⁴³ (начало регистра неизвестно).
                    self.assertEqual(ETHERNET[1:40], клиенты(р))
                    self.assertEqual(1.0, р.доля)

    def test_имя_маски_и_пустой_кадр_на_линии(self):
        self.assertEqual("стандарт G.7041", gfp.имя_маски(0xB6AB31E0))
        self.assertEqual("как в обработке отдела GFP_nest_000119D8", gfp.имя_маски(0x000119D8))
        self.assertEqual("поток инвертирован: это B6AB3325 (как в обработке отдела GFP_nest_B6AB3325) ⊕ FFFFFFFF",
                         gfp.имя_маски(0xB6AB3325 ^ 0xFFFFFFFF))
        self.assertEqual("нестандартная", gfp.имя_маски(0x5A17C3E9))
        self.assertEqual("B6 AB 31 E0 (биты на линии: 10110110 10101011 00110001 11100000)",
                         gfp.пустой_на_линии(0xB6AB31E0, "старший"))
        self.assertEqual("00 01 19 D8 (биты на линии: 00000000 10000000 10011000 00011011)",
                         gfp.пустой_на_линии(0x000119D8, "младший"))

    def test_только_пустые_кадры(self):
        for маска, найдётся in ((0xB6AB31E0, True), (0x000119D8, True), (0x5A17C3E9, False)):
            with self.subTest(маска=hex(маска)):
                ряд = г.в_ряд(г.поток([None] * 300, маска), сдвиг=2)
                self.assertEqual(маска, gfp.разобрать(ряд, маска=маска).маска)
                н = gfp.gfp(ряд)
                # Пустые с нестандартной маской неотличимы от повтора любого слова — автомат
                # их не берёт; с предустановленной — берёт.
                self.assertEqual(найдётся, н is not None)
                if найдётся:
                    self.assertEqual("GFP (G.7041)", н.что)
                    self.assertIn("пустых — 300", " ".join(н.подробно))

    def test_не_gfp(self):
        self.assertIsNone(gfp.gfp(с.случайные_биты(400_000)))
        слово = np.tile(np.unpackbits(np.frombuffer(bytes.fromhex("DEADBEEF"), dtype=np.uint8)), 3000)
        self.assertIsNone(gfp.gfp(слово))
        self.assertIsNone(gfp.gfp(np.zeros(100_000, dtype=np.uint8)))
        self.assertIsNone(gfp.разобрать(с.случайные_биты(100_000), маска=0xB6AB31E0))

    def test_заданная_маска_ищет_сдвиг_и_порядок(self):
        данные = г.поток(кадры_эталона(20), 0x000119D8)
        р = gfp.разобрать(г.в_ряд(данные, сдвиг=7, порядок="младший"), маска=0x000119D8)
        self.assertEqual((7, "младший"), (р.сдвиг, р.порядок))
        self.assertIsNone(gfp.разобрать(г.в_ряд(данные, сдвиг=7, порядок="младший"), маска=0x000119D8,
                                        порядок="старший"))
        self.assertIsNone(gfp.разобрать(г.в_ряд(данные), маска=0xB6AB31E0))

    def test_другой_многочлен_crc16(self):
        кадры = [None, None] + [г.нагрузка(п, многочлен=0x8005) for п in ETHERNET[:30]]
        ряд = г.в_ряд(г.поток(кадры, 0x12345678, многочлен=0x8005), сдвиг=1)
        self.assertIsNone(gfp.разобрать(ряд))
        р = gfp.разобрать(ряд, многочлен="авто")
        self.assertEqual((0x8005, False, "big", 0x12345678), (р.модель.P, р.модель.refin, р.модель.порядок, р.маска))
        self.assertEqual(ETHERNET[1:30], клиенты(р))
        self.assertIn("многочлен авто", gfp.слой(р))
        self.assertTrue(all(м.P != 0x1021 or м.refin for м in gfp.модели_каталога()))

    def test_маска_по_местам_большинством(self):
        б = np.frombuffer(г.поток(кадры_эталона(10), 0x000119D8), dtype=np.uint8)
        с_ = gfp.синдромы(б)
        self.assertEqual(0x000119D8, gfp.маска_по_местам(б, с_, gfp.синдром_маски(0x000119D8)))
        self.assertIsNone(gfp.маска_по_местам(б, с_, 0x1234))


class ВыделениеTests(unittest.TestCase):
    КАДРЫ = кадры_эталона(30)

    def разобрать(self, данные, **к):
        return gfp.разобрать(г.в_ряд(данные), маска=0xB6AB31E0, **к)

    def test_одиночные_ошибки_cHEC_исправляются_в_sync(self):
        места = г.места_заголовков(self.КАДРЫ)
        # Бит PLI у кадра с нагрузкой (5) и бит cHEC у пустого (9).
        данные = г.испортить(г.поток(self.КАДРЫ), 8 * места[5] + 13, 8 * места[9] + 20)
        р = self.разобрать(данные)
        с_ = р.счёт()
        self.assertEqual((len(self.КАДРЫ), 2, 0), (с_["кадров"], с_["исправлено_chec"], с_["потерь"]))
        self.assertEqual([5, 9], [i for i, и in enumerate(р.выделение.исправлен) if и])
        self.assertEqual(len(ETHERNET[1]) + 4, р.выделение.pli[5])
        self.assertEqual(ETHERNET[1:30], клиенты(р))

    def test_таблица_одиночных_синдромов(self):
        одиночные = gfp.одиночные()
        self.assertEqual(list(range(32)), sorted(одиночные.values()))
        for бит, синдром in ((0, int(gfp.синдром_маски(1 << 31))), (31, 1), (16, 0x8000)):
            self.assertEqual(бит, одиночные[синдром])

    def test_многобитовая_ошибка_потеря_и_новый_захват(self):
        места = г.места_заголовков(self.КАДРЫ)
        данные = г.испортить(г.поток(self.КАДРЫ), 8 * места[11] + 1, 8 * места[11] + 2)
        р = self.разобрать(данные)
        с_ = р.счёт()
        self.assertEqual([8 * места[11]], с_["потери_бит"])
        self.assertEqual(len(self.КАДРЫ) - 1, с_["кадров"])
        self.assertEqual([м for i, м in enumerate(места) if i != 11], р.выделение.места)
        self.assertEqual([1] * 11 + [2] * (len(места) - 12), р.выделение.серия)
        # Кадр 11 (клиент 3) потерян; первый кадр с нагрузкой новой серии (клиент 4) — прогрев.
        self.assertEqual(ETHERNET[1:3] + ETHERNET[5:30], клиенты(р))
        self.assertEqual(2, с_["thec"]["прогрев"])
        self.assertIn(f"потерь синхронизации: 1 (с бита {8 * места[11]})", "\n".join(gfp.строки_сводки(р)))

    def test_в_hunt_ошибки_не_исправляются(self):
        места = г.места_заголовков(self.КАДРЫ)
        р = self.разобрать(г.испортить(г.поток(self.КАДРЫ), 3))
        self.assertEqual(места[1:], р.выделение.места)
        self.assertEqual(0, р.счёт()["исправлено_chec"])

    def test_presync_требует_второго_верного(self):
        # Одинокий верный заголовок в мусоре: следующего через 4 + PLI нет — синхронизма нет.
        мусор = bytearray(np.random.default_rng(1).integers(0, 256, 200, dtype=np.uint8).tobytes())
        pli = (40).to_bytes(2, "big")
        мусор[10:14] = bytes(a ^ b for a, b in zip(pli + г.crc16(pli).to_bytes(2, "big"), bytes.fromhex("B6AB31E0"),
                                                    strict=True))
        р = self.разобрать(bytes(мусор) + г.поток(self.КАДРЫ))
        self.assertEqual(200, р.выделение.места[0])
        в = gfp.выделить(np.frombuffer(bytes(мусор) + г.поток(self.КАДРЫ), dtype=np.uint8),
                         gfp.синдромы(np.frombuffer(bytes(мусор) + г.поток(self.КАДРЫ), dtype=np.uint8)),
                         gfp.синдром_маски(0xB6AB31E0), 0xB6AB, gfp.одиночные(), delta=0)
        self.assertEqual(10, в.места[0])           # без PRESYNC мусорный заголовок принят бы

    def test_оборванный_последний_кадр(self):
        данные = г.поток(self.КАДРЫ)[:-20]
        р = self.разобрать(данные)
        self.assertEqual(len(self.КАДРЫ), len(р.выделение.места))
        self.assertTrue(р.кадры[len(self.КАДРЫ) - 1].оборван)
        self.assertEqual(ETHERNET[1:29], клиенты(р))


class НагрузкаTests(unittest.TestCase):
    def test_без_скремблера_и_инверсия(self):
        кадры = кадры_эталона(20)
        данные = г.поток(кадры, скремблер=False)
        р = gfp.разобрать(г.в_ряд(данные))
        self.assertEqual("нет", р.скремблер)
        self.assertEqual(ETHERNET[:20], клиенты(р))            # без скремблера прогрева нет
        р = gfp.разобрать(г.в_ряд(данные, инверсия=True))
        self.assertEqual(("инверсия", 0xB6AB31E0 ^ 0xFFFFFFFF), (р.скремблер, р.маска))
        self.assertEqual(ETHERNET[:20], клиенты(р))
        self.assertEqual(1.0, р.доли_thec["инверсия"])
        self.assertLess(р.доли_thec["x43"], 0.2)
        # Режим можно задать: «да» у нескремблированного потока ломает tHEC.
        р = gfp.разобрать(г.в_ряд(данные), скремблер="да")
        self.assertEqual("x43", р.скремблер)
        self.assertLess(р.счёт()["thec"]["верен"], 3)

    def test_tHEC_одиночная_исправлена_двойная_ошибка(self):
        области = [bytearray(г.нагрузка(п)) for п in ETHERNET[:10]]
        области[3][1] ^= 0x04          # бит UPI: 1 → 5, исправляется обратно
        области[5][3] ^= 0x01          # бит tHEC
        области[7] = bytearray(г.нагрузка(ETHERNET[7], плохой_thec=True))
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, bytes(о))])))
        self.assertEqual({"верен": 6, "исправлен": 2, "ошибка": 1, "прогрев": 1, "мало": 0}, р.счёт()["thec"])
        self.assertEqual(1, [р.кадры[i] for i in sorted(р.кадры)][3].upi)
        self.assertEqual([п for i, п in enumerate(ETHERNET[:10]) if i not in (0, 7)], клиенты(р))

    def test_cid_ehec_pfcs(self):
        области = [г.нагрузка(п, cid=i % 3, pfi=i % 2 == 1) for i, п in enumerate(ETHERNET[:30])]
        области[10] = bytearray(области[10])
        области[10][5] ^= 0x10         # запас после eHEC — eHEC неверен
        области[11] = bytearray(области[11])
        области[11][20] ^= 0x01        # данные после pFCS — pFCS неверна
        р = gfp.разобрать(г.в_ряд(г.поток([None] + [bytes(о) for о in области])))
        с_ = р.счёт()
        self.assertEqual({0: 9, 1: 10, 2: 10}, с_["cid"])
        self.assertEqual({"верен": 28, "ошибка": 1}, с_["ehec"])
        self.assertEqual({"верна": 14, "неверна": 1}, с_["pfcs"])
        self.assertEqual(ETHERNET[1:30:3], [к.данные for к in р.клиенты(1)])
        self.assertEqual({"верна": 28, "всего": 29}, с_["fcs_ethernet"])
        текст = "\n".join(gfp.строки_сводки(р))
        self.assertIn("каналы CID (линейный заголовок): 0×9, 1×10, 2×10; eHEC верен у 28 из 29", текст)
        self.assertIn("pFCS (CRC-32): верна у 14 из 15", текст)

    def test_cmf_и_таблицы_upi(self):
        области = [г.нагрузка(п) for п in ETHERNET[:5]] + [г.нагрузка(b"", pti=4, upi=u) for u in (1, 2, 3, 4, 5)]
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, None, о)])))
        с_ = р.счёт()
        self.assertEqual((5, 4), (с_["cmf"], с_["данных_клиента"]))
        имена = {т["upi"]: т["upi_словами"] for т in с_["типы"] if т["pti"] == 4}
        self.assertEqual("отказ сигнала клиента: потеря сигнала (CSF)", имена[1])
        self.assertEqual("признак дефекта назад (RDI)", имена[5])
        self.assertEqual("Ethernet (кадровое отображение)", gfp.имя_upi(0, 1))
        self.assertEqual("для нужд производителей", gfp.имя_upi(0, 0xF0))
        self.assertEqual("резерв", gfp.имя_upi(4, 100))

    def test_crc32_bzip2(self):
        имя = next(з for з in crc_katalog.REVENG if з[0] == "CRC-32/BZIP2")
        self.assertEqual(имя[7], gfp.crc32_bzip2(b"123456789"))
        for данные in (b"", b"\x00", bytes(range(200))):
            self.assertEqual(crc_.crc(данные, 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0xFFFFFFFF), gfp.crc32_bzip2(данные))
            self.assertEqual(г.crc32_старшим(данные), gfp.crc32_bzip2(данные))

    def test_открытый_захват_wireshark(self):
        # Кадры реального прибора (Wireshark, ошибка 11961): маска и скремблер — генератором.
        области = [к[4:] for к in ЗАХВАТ]
        р = gfp.разобрать(г.в_ряд(г.поток([None, *области] * 3, 0x000119D8), сдвиг=4))
        с_ = р.счёт()
        self.assertEqual((0x000119D8, 4), (р.маска, р.сдвиг))
        self.assertEqual({"верен": 11, "исправлен": 0, "ошибка": 0, "прогрев": 1, "мало": 0}, с_["thec"])
        self.assertEqual(({"верен": 11}, {"верна": 11}, {4: 11}), (с_["ehec"], с_["pfcs"], с_["cid"]))
        self.assertEqual({"верна": 11, "всего": 11}, с_["fcs_ethernet"])
        self.assertEqual([bytes(к) for к in (ЗАХВАТ * 3)[1:]], [gfp.кадр_gfp(р, i) for i in sorted(р.кадры)][1:])

    def test_gfp_t_суперблоки(self):
        def crc941f(данные):
            рег = 0
            for байт in данные:
                рег ^= байт << 8
                for _ in range(8):
                    рег = ((рег << 1) ^ 0x941F) & 0xFFFF if рег & 0x8000 else (рег << 1) & 0xFFFF
            return рег
        g = np.random.default_rng(3)
        области = []
        for _ in range(12):
            блоки = b""
            for _ in range(2):
                тело = g.integers(0, 256, 64, dtype=np.uint8).tobytes() + bytes([0b10010000])
                блоки += тело + crc941f(тело).to_bytes(2, "big")
            области.append(г.нагрузка(блоки, upi=6))
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, о)])))
        self.assertEqual(22, р.gfp_t["суперблоков"])
        self.assertEqual((16, 0x941F, None, 0, 1.0), (р.gfp_t["crc"]["w"], р.gfp_t["crc"]["P"], р.gfp_t["crc"]["init"],
                                                     р.gfp_t["crc"]["вклад"], р.gfp_t["верных_crc"]))
        self.assertEqual(0.25, р.gfp_t["доля_управляющих"])
        self.assertIn("GFP-T: суперблоков 64B/65B — 22; CRC-16 суперблока найдена вслепую: многочлен 0x941F",
                      "\n".join(gfp.строки_сводки(р)))
        self.assertEqual("0x941F", gfp.сводка(р)["gfp_t"]["многочлен"])


class ВыходыTests(unittest.TestCase):
    def test_кадры_клиентов_с_типом_канала_pcap(self):
        ip = ПАКЕТЫ[:12]
        for upi, данные, канал, ждём in ((1, ETHERNET[:12], 1, [с.ethernet(п) for п in ip]),
                                          (2, [b"\xff\x03\x00\x21" + п for п in ip], 9, None),
                                          (13, [b"\x00\x01\x01\x40" + п for п in ip], 219, None),
                                          (16, ip, 228, None), (17, ip, 229, None)):
            with self.subTest(upi=upi):
                р = gfp.разобрать(г.в_ряд(г.поток([x for д in данные for x in (None, г.нагрузка(д, upi=upi))])))
                к = gfp.клиентские_кадры(р)
                self.assertEqual(канал, к.канал)
                self.assertEqual((ждём or данные)[1:], list(к))
        # Разные UPI — кадры GFP целиком, LINKTYPE_GFP_F: заголовок без маски, нагрузка без скремблера.
        области = [г.нагрузка(ETHERNET[i], upi=1 if i % 2 else 16) for i in range(10)]
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, о)])))
        к = gfp.клиентские_кадры(р)
        self.assertEqual(171, к.канал)
        self.assertEqual([len(о).to_bytes(2, "big") + г.crc16(len(о).to_bytes(2, "big")).to_bytes(2, "big") + о
                          for о in области[1:]], list(к))
        pcap, расширение = выгрузка(к, "кадры")
        self.assertEqual(("pcap", 171), (расширение, struct.unpack_from("<I", pcap, 20)[0]))
        self.assertIsNone(gfp.клиентские_кадры(gfp.разобрать(г.в_ряд(г.поток([None] * 50)))))

    def test_выходы_и_ручной_слой(self):
        области = [г.нагрузка(п, cid=i % 2) for i, п in enumerate(ETHERNET[:20])]
        данные = г.поток([x for о in области for x in (None, о)], 0x000119D8)
        ряд = г.в_ряд(данные, сдвиг=3)
        р = gfp.разобрать(ряд)
        self.assertEqual(b"".join(ETHERNET[1:20:2]), gfp.в_байты(gfp.выход(р, "клиенты", 1)))
        self.assertEqual(b"".join(о for о in области[1:]), gfp.в_байты(gfp.выход(р, "поток")))
        self.assertEqual(b"".join(gfp.кадр_gfp(р, i) for i in range(len(р.выделение.места))),
                         gfp.в_байты(gfp.выход(р, "кадры")))
        self.assertEqual("gfp маска 000119D8 скремблер да канал 1 выход клиенты", gfp.слой(р, "клиенты", 1))
        биты, запись = снять_вручную(ряд, "gfp маска 000119D8 скремблер да канал 1 выход клиенты")
        self.assertEqual(b"".join(ETHERNET[1:20:2]), gfp.в_байты(биты))
        self.assertIn("маска основного заголовка 000119D8 (как в обработке отдела GFP_nest_000119D8)",
                      "\n".join(запись.подробно))
        биты, _ = снять_вручную(ряд, "GFP")
        self.assertEqual(b"".join(области[1:]), gfp.в_байты(биты))
        for плохое, ошибка in (("gfp маска 12", "маска"), ("gfp скремблер может", "скремблер"),
                               ("gfp порядок средний", "порядок"), ("gfp выход всё", "выход"),
                               ("gfp канал x", "канал"), ("gfp маска B6AB31E0", "не найдено")):
            with self.subTest(плохое), self.assertRaisesRegex(ValueError, ошибка):
                снять_вручную(ряд, плохое)
        п = gfp.разобрать_указание("gfp маска 0xb6ab3325 скремблер нет порядок младший многочлен авто")
        self.assertEqual({"маска": 0xB6AB3325, "скремблер": "нет", "порядок": "младший", "многочлен": "авто",
                          "канал": None, "выход": "поток"}, п)

    def test_находка_автомата(self):
        н = gfp.gfp(г.в_ряд(г.поток(кадры_эталона(30), 0xB6AB3325), сдвиг=2))
        self.assertEqual("GFP (G.7041): Ethernet (кадровое отображение)", н.что)
        self.assertEqual(1.0, н.уверенность)
        self.assertEqual(1, н.дальше.канал)
        self.assertEqual([с.ethernet(п) for п in ПАКЕТЫ[1:30]], list(н.дальше))
        self.assertEqual(ETHERNET[1:30], н.свойства["клиенты"])
        self.assertIn("слой для снятия: gfp маска B6AB3325 скремблер да выход поток", н.подробно)
        self.assertTrue(н.мера.startswith("90 кадров в синхронизме"))

    def test_вложенный_gfp(self):
        внутренний = г.поток(кадры_эталона(40), 0x000119D8)
        куски = [внутренний[i:i + 300] for i in range(0, len(внутренний), 300)]
        внешний = г.поток([x for к in куски for x in (None, г.нагрузка(к, upi=0xF0))])
        ветвь = Ветвь()
        внешняя = gfp.gfp(г.в_ряд(внешний))
        self.assertTrue(_вложенный_gfp(ветвь, внешняя, "поток"))
        self.assertEqual(["канальный", "сетевой"], [н.уровень for н in ветвь.находки])
        self.assertEqual("GFP (G.7041): Ethernet (кадровое отображение)", ветвь.находки[0].суть)
        self.assertEqual(0x000119D8, ветвь.находки[0].свойства["маска"])
        self.assertFalse(_вложенный_gfp(Ветвь(), gfp.gfp(г.в_ряд(г.поток(кадры_эталона(20)))), ""))
        разбор = разобрать(данные=внешний, имя="вложенный.bin")
        self.assertEqual(["канальный", "канальный", "сетевой"], [н.уровень for н in разбор.находки])
        self.assertIn("данные клиентов GFP", разбор.находки[1].путь)

    def test_gfp_в_vc4_sdh(self):
        from test_potok_sdh import stm, vc4_кадры  # noqa: PLC0415
        кадров = 12
        поток_ = г.поток(кадры_эталона(80, пустых=3))
        нагрузка = np.resize(np.frombuffer(поток_, dtype=np.uint8), (кадров, 9, 260))
        н = sdh.найти(stm([vc4_кадры(кадров, {}, нагрузка)], [0]))
        self.assertIn("0x1B (GFP (G.7041))", "\n".join(н.подробно))
        внутри = gfp.gfp(н.дальше["VC-4 №1 нагрузка"])
        self.assertEqual("GFP (G.7041): Ethernet (кадровое отображение)", внутри.что)
        self.assertGreater(len(внутри.дальше), 20)


class ТаблицыИВстраиваниеTests(unittest.TestCase):
    def test_таблицы_из_источника(self):
        for таблица in (gfp_tablicy.UPI_ДАННЫЕ, gfp_tablicy.UPI_УПРАВЛЕНИЕ):
            места = [x for от, до, *_ in таблица for x in range(от, до + 1)]
            self.assertEqual(list(range(256)), места)
        self.assertEqual({1: 1, 2: 9, 13: 219, 16: 228, 17: 229}, gfp_tablicy.UPI_LINKTYPE)
        self.assertEqual((170, 171, 219), (gfp_tablicy.LINKTYPE_GFP_T, gfp_tablicy.LINKTYPE_GFP_F, gfp_tablicy.LINKTYPE_MPLS))
        self.assertEqual((3, 4, 5, 6, 9, 12, 21), gfp_tablicy.UPI_ПРОЗРАЧНЫЕ)
        self.assertEqual(("User Data", "Client Management", "Management Communications"),
                         tuple(в[0] for в in gfp_tablicy.PTI.values()))

    def test_синхрослова_пустых_кадров(self):
        имена = [и for и, _ in sinhro.известные()]
        self.assertIn("пустой кадр GFP (G.7041) после маски: B6AB31E0", имена)
        self.assertIn("пустой кадр GFP (G.7041) после маски: 000119D8 — как в обработке отдела GFP_nest_000119D8", имена)
        слово = np.unpackbits(np.frombuffer(bytes.fromhex("B6AB3325"), dtype=np.uint8))
        self.assertIn("пустой кадр GFP (G.7041) после маски: B6AB3325 — как в обработке отдела GFP_nest_B6AB3325",
                      sinhro.известная(слово))

    def test_инструмент_кадры(self):
        н = rastr.инструмент(г.в_ряд(г.поток(кадры_эталона(20), 0x000119D8)), "кадры")
        self.assertIn("слой для снятия: gfp маска 000119D8 скремблер да выход поток", н.подробно)

    def test_встроенные_конфигурации(self):
        import tempfile  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as папка:
            к = konfig.Конфигурации(Path(папка))
            список = к.список(1)
            self.assertEqual(["GFP 000119D8", "GFP B6AB3325", "GFP авто (любая маска)", "GFP стандарт (B6AB31E0)"],
                             [з["имя"] for з in список])
            self.assertEqual([False] * 4, [з["своя"] for з in список])
            ид = список[0]["ид"]
            self.assertEqual("gfp маска 000119D8 выход клиенты", к.прочитать(ид, 7)["шаги"][0]["слой"])
            with self.assertRaises(konfig.НетДоступа):
                к.удалить(ид, 1)
            with self.assertRaises(konfig.НетДоступа):
                к.сохранить(1, имя="захват", шаги=список[0]["шаги"], ид=ид)
            своя = к.сохранить(1, имя="копия", шаги=список[0]["шаги"])
            self.assertEqual("копия", к.список(1)[0]["имя"])
            self.assertTrue(своя["своя"])
            ряд = г.в_ряд(г.поток(кадры_эталона(10), 0x000119D8))
            биты, _ = rastr.применить(ряд, к.прочитать(ид, 1)["шаги"])
            self.assertEqual(b"".join(ETHERNET[1:10]), gfp.в_байты(биты))


class АнализаторПакетовTests(unittest.TestCase):
    def pcap(self, кадры, канал=171):
        return выгрузка(kanal.Кадры(кадры, канал), "кадры")[0]

    def test_gfp_f_дерево_и_проверки(self):
        области = [г.нагрузка(ETHERNET[0], pfi=True, cid=3), г.нагрузка(ПАКЕТЫ[1], upi=16),
                   г.нагрузка(b"\x00\x21" + ПАКЕТЫ[2], upi=2), г.нагрузка(b"", pti=4, upi=1),
                   г.нагрузка(ETHERNET[3], upi=0xF0)]
        кадры = [len(о).to_bytes(2, "big") + г.crc16(len(о).to_bytes(2, "big")).to_bytes(2, "big") + о for о in области]
        плохой = bytearray(кадры[0])
        плохой[30] ^= 1                                  # внутри Ethernet: pFCS и FCS Ethernet неверны
        кадры += [bytes(плохой), bytes.fromhex("00000000")]
        захват = прочитать_захват(данные=self.pcap(кадры))
        self.assertEqual("GFP-F", захват.записи[0].канал)
        пакеты = [разобрать_пакет(з.данные, з.канал) for з in захват.записи]
        self.assertEqual(["GFP", "Ethernet", "IPv4"], [у.протокол for у in пакеты[0].уровни][:3])
        self.assertEqual(["GFP", "IPv4"], [у.протокол for у in пакеты[1].уровни][:2])
        self.assertEqual(["GFP", "PPP", "IPv4"], [у.протокол for у in пакеты[2].уровни][:3])
        self.assertEqual("GFP CMF: отказ сигнала клиента: потеря сигнала (CSF)", пакеты[3].инфо)
        self.assertEqual(["GFP", "Данные"], [у.протокол for у in пакеты[4].уровни])
        self.assertEqual("пустой кадр", пакеты[6].уровни[0].итог)
        поля = {п.имя: п for п in пакеты[0].уровни[0].поля}
        self.assertEqual("3", поля["CID (канал)"].текст)
        self.assertEqual([False] * 5, [поля[и].плохо for и in ("cHEC", "tHEC", "eHEC", "pFCS", "FCS Ethernet")])
        self.assertEqual("данные клиента: Ethernet (кадровое отображение), канал 3, PLI " + str(len(области[0])),
                         пакеты[0].уровни[0].итог)
        плохие = {п.имя: п.плохо for п in пакеты[5].уровни[0].поля}
        self.assertEqual((False, True, True), (плохие["tHEC"], плохие["pFCS"], плохие["FCS Ethernet"]))
        # Испорченный cHEC.
        п = разобрать_пакет(bytes([0, 0, 0, 1]), "GFP-F")
        self.assertTrue(п.уровни[0].поля[1].плохо)
        self.assertIn("должен быть 0x0000", п.уровни[0].поля[1].текст)

    def test_захват_wireshark_как_у_wireshark(self):
        for кадр in ЗАХВАТ:
            п = разобрать_пакет(кадр, "GFP-F")
            self.assertEqual(["GFP", "Ethernet", "LLC"], [у.протокол for у in п.уровни][:3])
            self.assertFalse(any(поле.плохо for у in п.уровни for поле in у.поля))
            self.assertTrue(zlib.crc32(кадр[12:-8]) == int.from_bytes(кадр[-8:-4], "little"))

    def test_mpls_канал_219(self):
        захват = прочитать_захват(данные=self.pcap([b"\x00\x01\x01\x40" + ПАКЕТЫ[0]], 219))
        п = разобрать_пакет(захват.записи[0].данные, захват.записи[0].канал)
        self.assertEqual(["MPLS", "IPv4"], [у.протокол for у in п.уровни][:2])


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноСтолаTests(unittest.TestCase):
    """Чистая логика окна GFP стола — прямо из app.js в node; слой окна понимает сервер."""

    def выполнить(self, случаи):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js(["маскаGFP", "слойGFP", "видКадраGFP", "проверкиКадраGFP", "ошибкаКадраGFP", "плиткиGFP"], [])
        код += """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(случаи.map(([ф, ...а]) => ({маскаGFP, слойGFP, видКадраGFP, проверкиКадраGFP, ошибкаКадраGFP, плиткиGFP})[ф](...а))));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=20)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_маска_и_слой(self):
        итог = self.выполнить([
            ["маскаGFP", "авто", ""], ["маскаGFP", "000119D8", "zz"], ["маскаGFP", "своя", " 0xb6ab 31e0 "],
            ["маскаGFP", "своя", "12345"], ["маскаGFP", "своя", "B6AB31E0FF"], ["маскаGFP", "", ""],
            ["слойGFP", {"маска": "000119D8", "скремблер": "нет", "порядок": "младший", "многочлен": "авто", "канал": 0, "выход": "клиенты"}],
            ["слойGFP", {"маска": "авто", "скремблер": "авто", "порядок": "авто", "канал": ""}],
            ["слойGFP", {}],
        ])
        self.assertEqual(["авто", "000119D8", "B6AB31E0", None, None, "авто",
                          "gfp маска 000119D8 скремблер нет порядок младший многочлен авто канал 0 выход клиенты",
                          "gfp маска авто скремблер авто выход поток", "gfp маска авто скремблер авто выход поток"], итог)
        # Слой окна понимает ручной слой сервера.
        self.assertEqual({"маска": 0x000119D8, "скремблер": "нет", "порядок": "младший", "многочлен": "авто",
                          "канал": 0, "выход": "клиенты"}, gfp.разобрать_указание(итог[6]))

    def test_строки_и_плитки_по_ответу_сервера(self):
        области = [г.нагрузка(п, cid=i % 2, pfi=True) for i, п in enumerate(ETHERNET[:12])]
        области[4] = bytearray(области[4])
        области[4][-1] ^= 1
        места = г.места_заголовков([None, *[x for о in области for x in (о, None)]])
        данные = г.испортить(г.поток([None, *[x for о in области for x in (bytes(о), None)]]), 8 * места[3] + 1, 8 * места[3] + 5)
        р = gfp.разобрать(г.в_ряд(данные))
        св = json.loads(json.dumps(gfp.сводка(р)))
        строки = json.loads(json.dumps(gfp.таблица(р, 0, 50, пустые=True)))["строки"]
        итог = self.выполнить([["плиткиGFP", св]] + [[ф, к] for к in строки[:9] for ф in ("видКадраGFP", "проверкиКадраGFP", "ошибкаКадраGFP")])
        плитки = dict(итог[0])
        self.assertEqual("B6AB31E0 — стандарт G.7041", плитки["Маска"])
        self.assertTrue(плитки["Пустой кадр на линии"].startswith("B6 AB 31 E0"))
        self.assertEqual("1 — с бита " + f"{8 * места[3]:,}".replace(",", "\xa0"), плитки["Потери синхронизации"])
        self.assertEqual("8 верна из 9", плитки["pFCS"])
        self.assertEqual("0 × 4, 1 × 5", плитки["CID"])
        self.assertEqual("x⁴³ + 1 снят", плитки["Скремблер"])
        self.assertEqual("0x01 Ethernet (кадровое отображение) × 9", плитки["UPI"])
        self.assertEqual("9 верен, 2 прогрев", плитки["tHEC"])
        self.assertLess(р.доля, 0.95)
        self.assertEqual(f"24 · в синхронизме {р.доля * 100:.1f} % байт".replace(".", ","), плитки["Кадров"])
        # Строки: пустой, прогрев, пустой, (кадр 3 потерян) пустой, прогрев, пустой, данные, пустой, данные с плохой pFCS.
        виды, проверки, ошибки = итог[1::3], итог[2::3], итог[3::3]
        self.assertEqual(["пустой", "прогрев x⁴³", "пустой", "пустой", "прогрев x⁴³", "пустой"], виды[:6])
        self.assertEqual("данные · Ethernet (кадровое отображение)", виды[6])
        self.assertEqual("cHEC верен · tHEC прогрев", проверки[4])
        self.assertEqual("cHEC верен · tHEC верен · eHEC верен · pFCS верна", проверки[6])
        self.assertEqual("cHEC верен · tHEC верен · eHEC верен · pFCS неверна", проверки[8])
        self.assertEqual([False] * 8 + [True], ошибки)

    def test_окно_в_меню_и_клавишах(self):
        from test_potok_sessii import APP_JS  # noqa: PLC0415
        текст = APP_JS.read_text(encoding="utf-8")
        for кусок in ("{ id: 'k-gfp', раздел: 'Кадры', имя: 'GFP (G.7041)…', вид: 'действие', сделать: 'gfp-окно' }",
                      "case 'gfp-окно': окноGFP(у); return null;", "else if (код === 'KeyG') окноGFP(у);",
                      "['G', 'GFP (G.7041)", "сохранитьСтола('stol-gfp'", "'/gfp/pakety'", "if (e.key === 'F1') { справка();"):
            self.assertIn(кусок, текст)


class ОкноЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.к = self.сеть.client

    def дождаться(self, ид, путь="potok"):
        for _ in range(900):
            с_ = self.к.get(f"/api/{путь}/{ид}").json()
            if с_.get("состояние") in ("готово", "ошибка"):
                return с_
            time.sleep(0.05)
        self.fail("не дождались")

    def test_поиск_таблица_и_анализатор_пакетов(self):
        к = self.к
        self.сеть.login("engineer")
        сид = к.post("/api/sessions", json={"name": "gfp"}).json()["id"]
        данные = г.поток(кадры_эталона(30, cid=2), 0x000119D8)
        ид = к.post(f"/api/sessions/{сид}/files", data={"bit_order": "msb"},
                    files={"file": ("gfp.bin", данные, "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        d = к.post(f"/api/potok/{ид}/gfp", json={"mask": "авто", "limit": 5, "idle": True}).json()
        self.assertTrue(d["найдено"])
        self.assertEqual(("000119D8", 90, 5), (d["сводка"]["маска"], d["таблица"]["всего"], len(d["таблица"]["строки"])))
        self.assertEqual({"2": "gfp маска 000119D8 скремблер да канал 2 выход клиенты"}, d["сводка"]["слои_cid"])
        # Без пустых кадров — только кадры с нагрузкой; со смещением — дальше.
        d = к.post(f"/api/potok/{ид}/gfp", json={"mask": "000119D8", "offset": 2, "limit": 3}).json()
        self.assertEqual((30, [2, 3, 4]), (d["таблица"]["всего"], [с_["№"] // 3 for с_ in d["таблица"]["строки"]]))
        self.assertFalse(к.post(f"/api/potok/{ид}/gfp", json={"mask": "B6AB31E0"}).json()["найдено"])
        self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp", json={"mask": "xyz"}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp", json={"scrambler": "может"}).status_code)
        for что, канал in (("клиенты", 1), ("кадры", 171)):
            ответ = к.post(f"/api/potok/{ид}/gfp/pakety", json={"mask": "авто", "what": что}).json()
            self.assertEqual((29, канал), (ответ["кадров"], ответ["канал"]))
            захват = self.дождаться(ответ["id"], "pakety")
            self.assertEqual("готово", захват["состояние"])
        self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp/pakety", json={"mask": "авто", "cid": 7}).status_code)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp/pakety", json={"mask": "B6AB31E0"}).status_code)


if __name__ == "__main__":
    unittest.main()
