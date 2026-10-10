"""GFP (G.7041) с любой маской основного заголовка: слепой поиск маски, выделение кадров
HUNT → PRESYNC → SYNC по всему потоку, нагрузка (x⁴³ + 1, tHEC, CID, pFCS, CMF, GFP-T),
выходы (кадры клиентов в pcap, поток, вложенный GFP), встраивание (ручной слой, автомат,
синхрослова, конфигурации, анализатор пакетов, окно стола).

Потоки строит эталонный генератор ``gfp_sintez`` — CRC и скремблер там свои, побитно.
"""

import json
import random
import re
import shutil
import struct
import subprocess
import time
import unittest
import zlib

import numpy as np

import _bootstrap  # noqa: F401
import gfp_koder as к_
import gfp_sintez as г
import potok_sintez as с
from gfp_zahvat import КАДРЫ as ЗАХВАТ
from reportgen.potok import crc as crc_
from reportgen.potok import crc_katalog, gfp, gfp_tablicy, kanal, konfig, rastr, sdh, sinhro
from reportgen.potok.nahodka import Находка
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


def бит_массива(бит_линии: int, порядок: str, от: int = 0) -> int:
    """Где бит линии в массиве (файл читается старшим битом вперёд): при файле младшим битом
    вперёд бит линии L лежит в байте L // 8 файла, в разряде L % 8 от младшего. ``от`` — массив
    взят с этого бита файла."""
    return (бит_линии if порядок == "старший" else 8 * (бит_линии // 8) + 7 - бит_линии % 8) - от


def поток_кодера(маска: int, сид: int, **смесь) -> tuple[bytes, dict, list[int]]:
    """Поток независимого кодера рецензента: байты, ожидание приёмника, места заголовков."""
    st = к_.Stream(mask=маска, scramble=смесь.pop("scramble", True), x43_seed=сид)
    к_.smes(random.Random(сид), st, **смесь)
    байты, места = st.build()
    return байты, к_.ozhidanie(st), места


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
                    self.assertEqual([бит_массива(сдвиг + 8 * м, порядок) for м in г.места_заголовков(кадры)],
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
        # И заданной маске нужен пик: в коротком ряду — не меньше ПОДРЯД мест с её синдромом.
        self.assertEqual(8, len(gfp.разобрать(г.в_ряд(г.поток([None] * 8)), маска=0xB6AB31E0).выделение.места))
        self.assertIsNone(gfp.разобрать(г.в_ряд(г.поток([None] * 7)), маска=0xB6AB31E0))

    def test_другой_многочлен_crc16(self):
        кадры = [None, None] + [г.нагрузка(п, многочлен=0x8005) for п in ETHERNET[:30]]
        ряд = г.в_ряд(г.поток(кадры, 0x12345678, многочлен=0x8005), сдвиг=1)
        self.assertIsNone(gfp.разобрать(ряд))
        р = gfp.разобрать(ряд, многочлен="авто")
        self.assertEqual((0x8005, False, "big", 0x12345678), (р.модель.P, р.модель.refin, р.модель.порядок, р.маска))
        self.assertEqual(ETHERNET[1:30], клиенты(р))
        self.assertIn("многочлен авто", gfp.слой(р))
        self.assertTrue(all(м.P != 0x1021 or м.refin for м in gfp.модели_каталога()))

    def test_cHEC_младшим_байтом_вперёд(self):
        # Нестандартная аппаратура: HEC младшим байтом вперёд. Маска — как на линии (пустой кадр — она сама),
        # и слой по найденной маске снимает тот же GFP.
        кадры = [None, None] + [г.нагрузка(п, многочлен=0x8005, cid=1, порядок_hec="little") for п in ETHERNET[:30]]
        ряд = г.в_ряд(г.поток(кадры, 0x12345678, многочлен=0x8005, порядок_hec="little"))
        р = gfp.разобрать(ряд, многочлен="авто")
        self.assertEqual((0x8005, "little", 0x12345678), (р.модель.P, р.модель.порядок, р.маска))
        self.assertEqual(ETHERNET[1:30], клиенты(р))
        self.assertEqual({"верен": 29}, р.счёт()["ehec"])
        self.assertTrue(gfp.пустой_на_линии(р.маска, р.порядок).startswith("12 34 56 78"))
        ряд_, _ = gfp.снять(ряд, gfp.слой(р, "клиенты"))
        self.assertEqual(b"".join(ETHERNET[1:30]), np.packbits(ряд_).tobytes())

    def test_маска_по_местам_большинством(self):
        б = np.frombuffer(г.поток(кадры_эталона(10), 0x000119D8), dtype=np.uint8)
        с_ = gfp.синдромы(б)
        self.assertEqual(0x000119D8, gfp.маска_по_местам(б, с_, gfp.синдром_маски(0x000119D8)))
        self.assertIsNone(gfp.маска_по_местам(б, с_, 0x1234))
        # Хватает трёх заголовков (двух пар соседей, согласных между собой).
        б = np.frombuffer(г.поток([г.нагрузка(e) for e in ETHERNET[:3]], 0x000119D8), dtype=np.uint8)
        self.assertEqual(0x000119D8, gfp.маска_по_местам(б, gfp.синдромы(б), gfp.синдром_маски(0x000119D8)))
        # Места ближе 4 байт (PLI < 0) не голосуют.
        с_ = np.full(40, 7, dtype=np.uint16)
        с_[::3] = 0x55
        self.assertIsNone(gfp.маска_по_местам(np.zeros(40, dtype=np.uint8), с_, 0x55))
        # Пары соседей голосуют: одна пара — мало, разные PLI — нет большинства.
        с_ = np.zeros(200, dtype=np.uint16)
        с_[[0, 10, 30]] = 0x55
        self.assertIsNone(gfp.маска_по_местам(np.zeros(200, dtype=np.uint8), с_, 0x55))
        # Соседи дальше всего на 4 + FFFF байт (PLI = FFFF), дальше — уже не соседи.
        for d, ждём in ((0xFFFF + 4, 0xFFFF), (0xFFFF + 5, None)):
            с_ = np.zeros(3 * d, dtype=np.uint16)
            с_[[0, d, 2 * d]] = 0x55
            м = gfp.маска_по_местам(np.zeros(3 * d, dtype=np.uint8), с_, 0x55)
            self.assertEqual(ждём, None if м is None else м >> 16)
        # Короче заголовка — синдромов нет.
        self.assertEqual(0, len(gfp.синдромы(np.zeros(3, dtype=np.uint8))))

    def test_пики_выше_случайного(self):
        # λ = мест / 65 536; пик — не меньше λ + 6·√λ + 4 (при λ = 16 — 44), не больше четырёх лучших.
        def пики(особые, лишних=0):
            счёт = np.full(65536, 16, dtype=np.int64)
            for s_, n in особые.items():
                счёт[s_] = n
            счёт[1000:1000 + sum(n - 16 for n in особые.values()) - лишних] -= 1
            return gfp._пики(np.repeat(np.arange(65536), счёт).astype(np.uint16), 4)
        self.assertEqual([(5, 44)], пики({5: 44, 9: 43}))
        self.assertEqual([(5, 50), (9, 45), (3, 44)], пики({9: 45, 5: 50, 3: 44, 7: 43}))
        # На одно место больше — порог чуть выше 44, и 44 уже не пик.
        self.assertEqual([(5, 45)], пики({5: 45, 9: 44}, лишних=1))
        self.assertEqual([], gfp._пики(np.zeros(0, dtype=np.uint16), 4))


class ПорядокБитTests(unittest.TestCase):
    """Файл младшим битом вперёд — биты линии развернуты в каждом байте файла; граница байта
    GFP от границы байта файла не зависит (сдвиг 0–7). Потоки — независимый кодер рецензента."""

    @classmethod
    def setUpClass(cls):
        cls.байты, cls.ждём, cls.места = поток_кодера(0x000119D8, 12, n_data=40)

    def test_все_сдвиги_при_обоих_порядках(self):
        for порядок in ("старший", "младший"):
            for сдвиг in range(8):
                файл = к_.file_bytes(self.байты, shift=сдвиг, lsb_first=порядок == "младший", seed=сдвиг)
                ряд = np.unpackbits(np.frombuffer(файл, dtype=np.uint8))
                for задан in ("авто", порядок):
                    with self.subTest(порядок=порядок, сдвиг=сдвиг, задан=задан):
                        р = gfp.разобрать(ряд, порядок=задан)
                        self.assertEqual((0x000119D8, порядок, сдвиг), (р.маска, р.порядок, р.сдвиг))
                        self.assertEqual(self.ждём["kadrov"], р.счёт()["кадров"])
                        self.assertEqual(self.ждём["klienty"], клиенты(р))
                        self.assertEqual([бит_массива(сдвиг + 8 * м, порядок) for м in self.места],
                                         [р.бит(м) for м in р.выделение.места])
                н = gfp.gfp(ряд)
                self.assertEqual(0x000119D8, н.свойства["маска"])

    def test_участок_не_с_границы_байта_файла(self):
        # Окно берёт участок с бита «от»: байты файла в нём начинаются с бита (−от) mod 8.
        файл = к_.file_bytes(self.байты, shift=3, lsb_first=True)
        ряд = np.unpackbits(np.frombuffer(файл, dtype=np.uint8))
        for от in (1, 5, 8, 13):
            with self.subTest(от=от):
                р = gfp.разобрать(ряд[от:], от_бита=от)
                self.assertEqual((0x000119D8, "младший"), (р.маска, р.порядок))
                места = [бит_массива(3 + 8 * м, "младший") for м in self.места]
                места = [м for м in места if м >= от + 8]
                self.assertEqual(места[-len(р.выделение.места):], [р.бит(м) for м in р.выделение.места])
                self.assertEqual(self.ждём["klienty"][-5:], клиенты(р)[-5:])

    def test_генератор_тестов_по_модели_кодера(self):
        # gfp_sintez.в_ряд с «младший» — то же, что файл кодера рецензента младшим битом вперёд.
        for сдвиг in (0, 3):
            ряд = г.в_ряд(self.байты, сдвиг=сдвиг, порядок="младший")
            эталон = np.unpackbits(np.frombuffer(к_.file_bytes(self.байты, shift=сдвиг, lsb_first=True),
                                                 dtype=np.uint8))
            self.assertEqual(len(эталон), len(ряд))
            self.assertTrue(np.array_equal(эталон[сдвиг:], ряд[сдвиг:]) if сдвиг == 0 else
                            np.array_equal(эталон[8:], ряд[8:]))


class БезСкремблераTests(unittest.TestCase):
    """Без скремблера с линейным заголовком: поле типа и CID с верными tHEC/eHEC дают свой пик
    синдрома (s = 0) выше пика настоящих заголовков — маска всё равно находится верно.
    Варианты таблицы рецензента, по 20 сидов, поток — независимый кодер."""

    def test_маска_верная_во_всех_вариантах(self):
        for cid, pfcs in ((3, 0.0), (3, 0.4), (None, 0.0)):
            for сид in range(20):
                with self.subTest(cid=cid, pfcs=pfcs, сид=сид):
                    с_ = random.Random(900 + сид)
                    маска = 0x000119D8 if сид % 2 else с_.getrandbits(32)
                    st = к_.Stream(mask=маска, scramble=False)
                    к_.smes(с_, st, n_data=76, idle_max=0, cmf_every=0, upi_mix=(1, 0x10), cid=cid,
                            pfcs_share=pfcs)
                    байты, _ = st.build()
                    ряд = np.unpackbits(np.frombuffer(к_.file_bytes(байты, shift=с_.randrange(8)),
                                                      dtype=np.uint8))
                    р = gfp.разобрать(ряд)
                    self.assertEqual((маска, "нет"), (р.маска, р.скремблер))
                    self.assertEqual(к_.ozhidanie(st)["klienty"], клиенты(р))
                    self.assertEqual(маска, gfp.gfp(ряд).свойства["маска"])


class НеGFPTests(unittest.TestCase):
    """Потоки без GFP (генераторы рецензента: случайные, HDLC, ATM, SDH STM-1): слепой поиск
    находит в них «заголовки» с нестандартной маской, но окно и ручной слой не называют их GFP —
    то же условие, что у автомата (маска из предустановок или 8 осмысленных кадров с tHEC)."""

    ГЕНЕРАТОРЫ = (("случайные", к_.sluch), ("HDLC", к_.hdlc), ("ATM", к_.atm), ("SDH STM-1", к_.sdh))

    def test_не_gfp_в_окне_и_слое(self):
        for имя, ген in self.ГЕНЕРАТОРЫ:
            for сид in range(5):
                with self.subTest(имя=имя, сид=сид):
                    ряд = np.unpackbits(np.frombuffer(ген(random.Random(1000 + сид), 1 << 18), dtype=np.uint8))
                    р, причина = gfp.разобрать_с_причиной(ряд)
                    self.assertIsNone(р)
                    if причина:
                        self.assertRegex(причина, "не GFP")
                    if сид == 0:
                        self.assertIsNone(gfp.gfp(ряд))
                        with self.assertRaisesRegex(ValueError, "^GFP: "):
                            снять_вручную(ряд, "gfp маска авто выход клиенты")

    def test_повтор_случайных_верных_thec(self):
        # SDH рецензента (сид 18, 1 МБ): кадры VC-4 повторяются — 10 случайно верных tHEC из 3733.
        ряд = np.unpackbits(np.frombuffer(к_.sdh(random.Random(1018), 1 << 20), dtype=np.uint8))
        р, причина = gfp.разобрать_с_причиной(ряд)
        self.assertIsNone(р)
        self.assertRegex(причина, r"кадров лишь \d+ из \d+ с верным tHEC")
        self.assertIsNone(gfp.gfp(ряд))

    def test_причина_и_заданная_маска(self):
        # SDH: «заголовки» с верным cHEC подряд есть, кадров с верным tHEC нет.
        ряд = np.unpackbits(np.frombuffer(к_.sdh(random.Random(1000), 1 << 18), dtype=np.uint8))
        р, причина = gfp.разобрать_с_причиной(ряд)
        м = re.fullmatch(r"заголовки с верным cHEC есть \(маска ([0-9A-F]{8}) не из предустановок, кадров (\d+)\), "
                         r"но ни одного кадра с верным tHEC и известным типом \(нужно не меньше 8 и не меньше "
                         r"четверти\) — не GFP; "
                         r"с заданной маской кадры разбираются без этой проверки", причина)
        self.assertIsNotNone(м, причина)
        with self.assertRaisesRegex(ValueError, "^GFP: заголовки с верным cHEC есть"):
            снять_вручную(ряд, "gfp")
        # Заданную маску выбрал человек — разбор без проверки.
        заданная = gfp.разобрать(ряд, маска=int(м.group(1), 16))
        self.assertEqual(int(м.group(2)), len(заданная.выделение.места))
        self.assertEqual("", gfp.сомнение(заданная) if gfp.осмысленных(заданная) >= 8 else "")

    def test_сомнение(self):
        р = gfp.разобрать(г.в_ряд(г.поток(кадры_эталона(10), 0x5A17C3E9)))
        self.assertEqual("", gfp.сомнение(р))                         # 9 осмысленных кадров
        р = gfp.разобрать(г.в_ряд(г.поток(кадры_эталона(9), 0x5A17C3E9)))
        self.assertEqual("", gfp.сомнение(р))                         # ровно 8 — достаточно
        р, причина = gfp.разобрать_с_причиной(г.в_ряд(г.поток(кадры_эталона(8), 0x5A17C3E9)))
        self.assertIsNone(р)
        self.assertIn("кадров лишь 7 из 7 с верным tHEC и известным типом (нужно не меньше 8 и не меньше "
                      "четверти)", причина)
        р = gfp.разобрать(г.в_ряд(г.поток(кадры_эталона(8), 0x5A17C3E9)), маска=0x5A17C3E9)
        self.assertIn("кадров лишь 7 из 7", gfp.сомнение(р))
        # Не меньше четверти проверенных tHEC: 9 верных из 36 — GFP, из 37 — нет (оборванный не в счёт).
        for плохих, gfp_ in ((27, True), (28, False)):
            кадры = кадры_эталона(10) + [г.нагрузка(п, плохой_thec=True) for п in ETHERNET[10:10 + плохих]]
            данные = г.поток(кадры, 0x5A17C3E9)
            р = gfp.разобрать(г.в_ряд(данные + данные[:30]), маска=0x5A17C3E9)
            self.assertEqual((9, 9 + плохих), (gfp.осмысленных(р), р.счёт()["thec"]["верен"] + р.счёт()["thec"]["ошибка"]))
            self.assertEqual(gfp_, not gfp.сомнение(р), gfp.сомнение(р))
        # Предустановка и её инверсия — без кадров с нагрузкой; меньше 8 кадров — не GFP.
        for маска in (0xB6AB3325, 0xB6AB3325 ^ 0xFFFFFFFF):
            self.assertEqual("", gfp.сомнение(gfp.разобрать(г.в_ряд(г.поток([None] * 8, маска)), маска=маска)))
        р = gfp.разобрать(г.в_ряд(г.поток([None] * 9)), маска=0xB6AB31E0)
        р.выделение.места = р.выделение.места[:7]
        self.assertEqual("заголовков с верным cHEC подряд — 7 кадров, меньше 8: не GFP", gfp.сомнение(р))


class ВыделениеTests(unittest.TestCase):
    КАДРЫ = кадры_эталона(30)

    def разобрать(self, данные, **к):
        return gfp.разобрать(г.в_ряд(данные), маска=0xB6AB31E0, **к)

    def test_одиночные_ошибки_cHEC_исправляются_в_sync(self):
        места = г.места_заголовков(self.КАДРЫ)
        # Бит PLI у кадра с нагрузкой (5), последний бит PLI (7), бит cHEC у пустого (9), первый бит cHEC (12).
        данные = г.испортить(г.поток(self.КАДРЫ), 8 * места[5] + 13, 8 * места[7] + 15, 8 * места[9] + 20,
                             8 * места[12] + 16)
        р = self.разобрать(данные)
        с_ = р.счёт()
        self.assertEqual((len(self.КАДРЫ), 4, 0), (с_["кадров"], с_["исправлено_chec"], с_["потерь"]))
        self.assertEqual([5, 7, 9, 12], [i for i, и in enumerate(р.выделение.исправлен) if и])
        self.assertEqual(len(ETHERNET[1]) + 4, р.выделение.pli[5])
        self.assertEqual(len(ETHERNET[2]) + 4, р.выделение.pli[8])
        self.assertEqual(г.места_заголовков(self.КАДРЫ), р.выделение.места)
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
        # Доля байт в синхронизме — от первого заголовка: всё, кроме потерянного кадра.
        потерян = 4 + len(self.КАДРЫ[11] or b"")
        self.assertEqual((len(данные) - потерян) / len(данные), р.доля)
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

    def выделить(self, данные):
        б = np.frombuffer(данные, dtype=np.uint8)
        return gfp.выделить(б, gfp.синдромы(б), gfp.синдром_маски(0xB6AB31E0), 0xB6AB, gfp.одиночные())

    def test_край_ряда_и_байты_в_синхронизме(self):
        # Второй заголовок — ровно последние 4 байта ряда: PRESYNC подтверждён, все байты в синхронизме.
        в = self.выделить(г.поток([None, None]))
        self.assertEqual(([0, 4], 8), (в.места, в.байт_в_синхронизме))
        в = self.выделить(г.поток(self.КАДРЫ))
        self.assertEqual(len(г.поток(self.КАДРЫ)), в.байт_в_синхронизме)
        # Следующий заголовок начинался бы за 3 байта до конца — его нет, и ряд не читается за концом.
        в = self.выделить(г.поток([bytes(5)]) + bytes(3))
        self.assertEqual(([], 0), (в.места, в.байт_в_синхронизме))
        # Кадр, чей конец за концом ряда, в синхронизме, но его байты не засчитываются.
        данные = г.поток(self.КАДРЫ)
        в = self.выделить(данные[:-1])
        self.assertEqual((len(self.КАДРЫ), г.места_заголовков(self.КАДРЫ)[-1]),
                         (len(в.места), в.байт_в_синхронизме))

    def test_байт_в_сериях_не_короче_подряд(self):
        # Серии 1 (8 кадров), 2 (7 кадров), 3 (8 кадров, последний кончается за концом ряда из 200 байт).
        в = gfp.Выделение(места=list(range(0, 80, 10)) + list(range(80, 150, 10)) + [150, 160, 170, 180, 190, 195, 196, 197],
                          pli=[6] * 15 + [6, 6, 6, 6, 1, 0, 0, 0], серия=[1] * 8 + [2] * 7 + [3] * 8)
        self.assertEqual(8 * 10 + (4 * 10 + 5 + 2 * 4), в.байт_в_сериях(200))
        self.assertEqual(8 * 10 + 7 * 10 + (4 * 10 + 5 + 2 * 4), в.байт_в_сериях(200, 7))
        self.assertEqual(0, gfp.Выделение().байт_в_сериях(100))
        self.assertEqual(8 * 10 + (4 * 10 + 5 + 4), в.байт_в_сериях(200 - 1))

    def test_сдвиг_на_байт_захват_со_следующего_байта(self):
        # Лишний байт перед заголовком 11: там потеря, а HUNT находит заголовок сразу на следующем байте.
        места = г.места_заголовков(self.КАДРЫ)
        данные = г.поток(self.КАДРЫ)
        в = self.выделить(данные[:места[11]] + b"\x00" + данные[места[11]:])
        self.assertEqual([места[11]], в.потери)
        self.assertEqual(места[:11] + [м + 1 for м in места[11:]], в.места)

    def test_служебные_кадры_pli_1_3(self):
        # PLI 1–3: кадр в синхронизме, но нагрузки (поля типа) у него нет — не разбирается.
        кадры = кадры_эталона(10)
        кадры[4:4] = [b"\x01\x02", b"\x03", b"\x04\x05\x06"]
        # И кадры других PTI: два управляющих (101), один резервный (011), один с PLI 4 (только поле типа).
        кадры += [г.нагрузка(b"ab", pti=5, upi=0x10), г.нагрузка(b"cd", pti=5), г.нагрузка(b"ef", pti=3),
                  г.нагрузка(b"", upi=0x10)]
        р = self.разобрать(г.поток(кадры))
        с_ = р.счёт()
        self.assertEqual((len(кадры), 3, 14, 0), (с_["кадров"], с_["служебных"], с_["с_нагрузкой"], с_["thec"]["мало"]))
        self.assertEqual((2, 1, 0, 10), (с_["управляющих"], с_["прочих_pti"], с_["cmf"], с_["данных_клиента"]))
        self.assertEqual(4, с_["длины"]["от"])
        self.assertEqual(ETHERNET[1:10] + [b""], клиенты(р))
        # Прогрев — первые 43 бита серии: поле типа с 6-го байта (бит 48) уже восстановлено, с 5-го — нет.
        for служебные, первый in (([b"abc", b"def"], 0), ([b"abc", b"de"], 1)):
            р = self.разобрать(г.поток([None, None, *служебные] + кадры_эталона(5)[2:]))
            self.assertEqual(ETHERNET[первый:5], клиенты(р))
            self.assertEqual((первый, 0), (р.счёт()["thec"]["прогрев"], р.счёт()["thec"]["ошибка"]))

    def test_оборванный_последний_кадр(self):
        данные = г.поток(self.КАДРЫ)[:-20]
        р = self.разобрать(данные)
        self.assertEqual(len(self.КАДРЫ), len(р.выделение.места))
        self.assertTrue(р.кадры[len(self.КАДРЫ) - 1].оборван)
        self.assertEqual(ETHERNET[1:29], клиенты(р))

    def test_оборванный_не_в_счёте_pfcs_и_thec(self):
        # Кадр, оборванный концом участка: pFCS по неполным данным не «неверна», в счёт не идёт.
        кадры = кадры_эталона(12, pfi=True)
        for обрезать, thec_оборванного in ((20, "верен"), (len(кадры[-1]) - 2, "мало байт")):
            with self.subTest(обрезать=обрезать):
                р = self.разобрать(г.поток(кадры)[:-обрезать])
                последний = р.кадры[len(кадры) - 1]
                self.assertEqual((True, thec_оборванного), (последний.оборван, последний.thec))
                с_ = р.счёт()
                self.assertEqual({"верна": 10}, с_["pfcs"])            # первый — в прогреве
                self.assertEqual({"верен": 10, "исправлен": 0, "ошибка": 0, "прогрев": 1, "мало": 0}, с_["thec"])
                строка = gfp.таблица(р, len(кадры) - 1, 1)["строки"][0]
                self.assertEqual((True, ""), (строка["оборван"], строка["pfcs"]))
        строка = gfp.таблица(self.разобрать(г.поток(кадры)), len(кадры) - 1, 1)["строки"][0]
        self.assertEqual((False, "верна"), (строка["оборван"], строка["pfcs"]))


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
        области[2][1] ^= 0x01          # последний бит UPI (бит 15)
        области[4][2] ^= 0x80          # первый бит tHEC (бит 16)
        области[6][0] ^= 0x80          # первый бит PTI (бит 0): 000 → 100 (CMF), исправляется обратно
        области[7] = bytearray(г.нагрузка(ETHERNET[7], плохой_thec=True))
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, bytes(о))])))
        self.assertEqual({"верен": 3, "исправлен": 5, "ошибка": 1, "прогрев": 1, "мало": 0}, р.счёт()["thec"])
        кадры = [р.кадры[i] for i in sorted(р.кадры)]
        self.assertEqual([(0, 0, 0, 1)] * 5, [(к.pti, к.pfi, к.exi, к.upi) for к in (кадры[2], кадры[3], кадры[4],
                                                                                    кадры[5], кадры[6])])
        self.assertEqual([п for i, п in enumerate(ETHERNET[:10]) if i not in (0, 7)], клиенты(р))

    def test_короткие_области(self):
        # Линейный заголовок — только если в области есть все 8 байт; короче 4 байт — поля типа нет.
        тип = bytes([0x01, 0x01])                     # PTI 000, PFI 0, EXI 1, UPI 1
        заголовок = тип + г.crc16(тип).to_bytes(2, "big")
        доп = bytes([5, 0])
        линейный = заголовок + доп + г.crc16(доп).to_bytes(2, "big")
        к = gfp.разобрать_нагрузку(0, линейный)
        self.assertEqual((5, "верен", b""), (к.cid, к.ehec, к.данные))
        к = gfp.разобрать_нагрузку(0, линейный[:7])
        self.assertEqual((None, "", b"\x05\x00" + линейный[6:7]), (к.cid, к.ehec, к.данные))
        к = gfp.разобрать_нагрузку(0, линейный[:8] + b"xyz")
        self.assertEqual((5, b"xyz"), (к.cid, к.данные))
        self.assertEqual("ошибка", gfp.разобрать_нагрузку(0, заголовок + доп + b"\x00\x01").ehec)
        self.assertEqual(("мало байт", None), (gfp.разобрать_нагрузку(0, заголовок[:3]).thec,
                                               gfp.разобрать_нагрузку(0, заголовок[:3]).pti))
        self.assertEqual(("верен", 1), (gfp.разобрать_нагрузку(0, заголовок).thec, gfp.разобрать_нагрузку(0, заголовок).upi))
        # pFCS и при пустом поле данных; трёх байт на pFCS мало — это данные.
        к = gfp.разобрать_нагрузку(0, г.нагрузка(b"", pfi=True))
        self.assertEqual(("верна", b""), (к.pfcs, к.данные))
        к = gfp.разобрать_нагрузку(0, г.нагрузка(b"", pfi=True)[:7])
        self.assertEqual(("", 3), (к.pfcs, len(к.данные)))
        # Доля верных tHEC для выбора скремблера: без прогрева, и области из одного поля типа — тоже.
        р = gfp.разобрать(г.в_ряд(г.поток([x for _ in range(30) for x in (None, г.нагрузка(b""))])))
        self.assertEqual(("x43", 1.0), (р.скремблер, р.доли_thec["x43"]))
        self.assertEqual((4, 4), (р.счёт()["длины"]["от"], р.счёт()["длины"]["до"]))
        р = gfp.разобрать(г.в_ряд(г.поток(кадры_эталона(20))))
        self.assertEqual(1.0, р.доли_thec["x43"])
        self.assertEqual(0.0, gfp.доля_thec({1: b"abc"}, set()))

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
        области, флаги = [], []
        for н in range(12):
            блоки = b""
            for б in range(3):
                флаг = int(g.integers(0, 256))
                тело = g.integers(0, 256, 64, dtype=np.uint8).tobytes() + bytes([флаг])
                crc = crc941f(тело) ^ (1 if (н, б) == (5, 1) else 0)          # один суперблок с плохой CRC
                блоки += тело + crc.to_bytes(2, "big")
                if н:
                    флаги.append(флаг)
            области.append(г.нагрузка(блоки, upi=6))
        # Не суперблоки: кадровое отображение (UPI 1) и CMF той же длины, данные не кратные 67 байтам, пустые.
        области += [г.нагрузка(bytes(134)), г.нагрузка(bytes(67), pti=4, upi=3), г.нагрузка(bytes(100), upi=6),
                    г.нагрузка(b"", upi=6)]
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, о)])))
        self.assertEqual(33, р.gfp_t["суперблоков"])
        self.assertEqual((16, 0x941F, None, 0), (р.gfp_t["crc"]["w"], р.gfp_t["crc"]["P"], р.gfp_t["crc"]["init"],
                                                 р.gfp_t["crc"]["вклад"]))
        self.assertEqual(32 / 33, р.gfp_t["верных_crc"])
        self.assertEqual(float(np.unpackbits(np.array(флаги, dtype=np.uint8)).mean()), р.gfp_t["доля_управляющих"])
        self.assertIn("GFP-T: суперблоков 64B/65B — 33; CRC-16 суперблока найдена вслепую: многочлен 0x941F",
                      "\n".join(gfp.строки_сводки(р)))
        self.assertEqual("0x941F", gfp.сводка(р)["gfp_t"]["многочлен"])

    def test_gfp_t_отражённая_crc_суперблока(self):
        # CRC суперблока ищется вслепую — и отражённая с ненулевым начальным (CRC-16/MODBUS), младшим байтом.
        def modbus(данные):
            рег = 0xFFFF
            for байт in данные:
                рег ^= байт
                for _ in range(8):
                    рег = (рег >> 1) ^ 0xA001 if рег & 1 else рег >> 1
            return рег
        g = np.random.default_rng(4)
        области = []
        for _ in range(10):
            тела = [g.integers(0, 256, 65, dtype=np.uint8).tobytes() for _ in range(2)]
            области.append(г.нагрузка(b"".join(т + modbus(т).to_bytes(2, "little") for т in тела), upi=6))
        р = gfp.разобрать(г.в_ряд(г.поток([x for о in области for x in (None, о)])))
        self.assertEqual((0x8005, True, "little", 1.0), (р.gfp_t["crc"]["P"], р.gfp_t["crc"]["refout"],
                                                        р.gfp_t["crc"]["порядок"], р.gfp_t["верных_crc"]))


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
                          "с_бита": 0, "канал": None, "выход": "поток"}, п)

    def test_слой_с_бита(self):
        # Два потока подряд: слой окна, открытого с начала второго, снимает только второй.
        первый = г.поток(кадры_эталона(12), 0x000119D8)
        второй_кадры = [None, None] + [г.нагрузка(п, upi=0x10, cid=3) for п in ПАКЕТЫ[:12]]
        второй = г.поток(второй_кадры, 0x000119D8)
        ряд = г.в_ряд(первый + второй, сдвиг=5)
        от = 5 + 8 * len(первый)
        р = gfp.разобрать(ряд[от:], от_бита=от)
        self.assertEqual(ПАКЕТЫ[1:12], клиенты(р))
        self.assertEqual({"от": от, "до": len(ряд), "всего": len(ряд)}, р.участок())
        self.assertEqual({"от": от, "до": len(ряд), "всего": len(ряд) + 1},
                         gfp.разобрать(ряд[от:], от_бита=от, всего_бит=len(ряд) + 1).участок())
        слой = gfp.слой(р, "клиенты")
        self.assertEqual(f"gfp маска 000119D8 скремблер да с бита {от} выход клиенты", слой)
        # «Поток нагрузки», «По каналам CID», «В журнал» окна — слои сводки, с того же места.
        св = gfp.сводка(р)
        self.assertEqual({"3": f"gfp маска 000119D8 скремблер да с бита {от} канал 3 выход клиенты"}, св["слои_cid"])
        self.assertEqual(f"gfp маска 000119D8 скремблер да с бита {от} выход поток", св["слой_поток"])
        self.assertEqual({"с_бита": от}, {"с_бита": gfp.разобрать_указание(слой)["с_бита"]})
        биты, запись = снять_вручную(ряд, слой)
        self.assertEqual(b"".join(ПАКЕТЫ[1:12]), gfp.в_байты(биты))
        self.assertIn(f"разобраны биты {от}…{len(ряд) - 1} из {len(ряд)}", запись.подробно)
        # Без «с бита» — оба потока (первый клиент второго — после чужого состояния скремблера).
        биты, запись = снять_вручную(ряд, "gfp маска 000119D8 выход клиенты")
        self.assertEqual(b"".join(ETHERNET[1:12] + ПАКЕТЫ[1:12]), gfp.в_байты(биты))
        self.assertFalse(any(с_.startswith("разобраны биты") for с_ in запись.подробно))
        # Байты файла младшим битом вперёд отсчитываются от начала массива — и с бита не кратного 8.
        файл = г.в_ряд(первый + второй, сдвиг=3, порядок="младший")
        биты, _ = снять_вручную(файл, f"gfp с бита {от + 1} выход клиенты")
        self.assertEqual(b"".join(ПАКЕТЫ[1:12]), gfp.в_байты(биты))
        with self.assertRaisesRegex(ValueError, "с бита — номер бита"):
            gfp.разобрать_указание("gfp с бита x")
        with self.assertRaisesRegex(ValueError, f"с бита {len(ряд)} — за концом массива \\({len(ряд)} бит\\)"):
            снять_вручную(ряд, f"gfp с бита {len(ряд)}")
        self.assertEqual(0, gfp.разобрать_указание("gfp с бита 0")["с_бита"])
        with self.assertRaisesRegex(ValueError, "не найдено"):
            снять_вручную(ряд, f"gfp маска 000119D8 с бита {len(ряд) - 1}")

    def test_весь_массив_окнами(self):
        """Массив длиннее БИТ_ДО — окнами до конца: кадры, выходы и сводка те же, что у разбора разом
        (скремблер x⁴³ сквозной, исправленный cHEC, потеря синхронизации, младший бит первым, «с бита»)."""
        кадры = []
        for i, п in enumerate(ETHERNET * 12):
            кадры += [None] * (i % 3) + ([b"\x00\x01"] if i % 17 == 5 else []) + [г.нагрузка(п, cid=i % 2)]
        данные = г.поток(кадры, 0x000119D8)
        места = г.места_заголовков(кадры)
        данные = г.испортить(данные, 8 * места[300] + 5, *(8 * места[700] + б for б in (0, 3, 9, 20)))
        for порядок, сдвиг, от in (("старший", 3, 0), ("младший", 6, 0), ("младший", 6, 77), ("старший", 0, 8001)):
            ряд = г.в_ряд(данные, сдвиг=сдвиг, порядок=порядок)
            с_бита = f" с бита {от}" if от else ""
            разом = {вых: снять_вручную(ряд, f"gfp маска 000119D8{с_бита} {вых}")
                     for вых in ("выход поток", "выход клиенты", "выход кадры", "канал 1 выход клиенты")}
            р_разом = gfp.разобрать_окнами(ряд[от:] if False else ряд, маска=0x000119D8, от_бита=от)[0]
            with mock.patch.object(gfp, "БИТ_ДО", 1 << 16):
                р, _ = gfp.разобрать_окнами(ряд, маска=0x000119D8, от_бита=от)
                self.assertGreater(р.окон, 10)
                self.assertEqual(gfp.строки_сводки(р_разом), gfp.строки_сводки(р))
                self.assertEqual(1, len(р.выделение.потери))
                for вых, (биты, запись) in разом.items():
                    with self.subTest(порядок=порядок, сдвиг=сдвиг, от=от, вых=вых):
                        окнами, з = снять_вручную(ряд, f"gfp маска 000119D8{с_бита} {вых}")
                        self.assertTrue(np.array_equal(биты, окнами))
                        self.assertIn(f"разобран весь массив окнами по {1 << 16} бит", "\n".join(з.подробно))
                        self.assertEqual(запись.подробно[:3], з.подробно[:3])
        with mock.patch.object(gfp, "БИТ_ДО", 1 << 16):
            биты, _ = снять_вручную(г.в_ряд(данные, сдвиг=3), "gfp маска 000119D8 выход клиенты")
        клиентов = [п for i, п in enumerate(ETHERNET * 12)]
        self.assertGreater(len(gfp.в_байты(биты)), 0.95 * len(b"".join(клиентов)))

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
        # Без данных клиентов (одни пустые кадры) и не GFP — вложенного нет.
        self.assertFalse(_вложенный_gfp(Ветвь(), gfp.gfp(г.в_ряд(г.поток([None] * 30))), ""))
        self.assertFalse(_вложенный_gfp(Ветвь(), Находка("канальный", "HDLC", 1.0, "", дальше=[b"x"]), ""))
        разбор = разобрать(данные=внешний, имя="вложенный.bin")
        self.assertEqual(["канальный", "канальный", "сетевой"], [н.уровень for н in разбор.находки])
        self.assertIn("данные клиентов GFP", разбор.находки[1].путь)

    def test_автомат_смешанные_клиенты_и_не_ip(self):
        # Клиенты разных UPI: для выгрузки — кадры GFP целиком (171), а IP ищется в данных клиентов.
        кадры = []
        for i, e in enumerate(ETHERNET[:40]):
            кадры += [None, г.нагрузка(e) if i % 5 else г.нагрузка(bytes(range(60)), upi=0xF0)]
        разбор = разобрать(данные=г.поток(кадры), имя="смесь.bin")
        self.assertEqual(["канальный", "сетевой"], [н.уровень for н in разбор.находки])
        self.assertEqual(171, разбор.находки[0].дальше.канал)
        # Не IP и не вложенный GFP — клиенты идут к опознанию канального протокола и формата полей.
        rng = np.random.default_rng(1)
        кадры = [x for _ in range(40) for x in (None, г.нагрузка(rng.integers(0, 256, 90, dtype=np.uint8).tobytes(),
                                                                upi=0xF0))]
        разбор = разобрать(данные=г.поток(кадры), имя="не-ip.bin")
        self.assertEqual(["GFP (G.7041): для нужд производителей"], [н.что for н in разбор.находки])
        self.assertIn("IP и известный формат в кадрах: нет", разбор.не_найдено)

    def test_вложений_до_трёх(self):
        """GFP в GFP в GFP в GFP: три вложения ищутся (и IP за третьим), четвёртое — уже нет."""
        def вложить(поток_, маска):
            куски = [поток_[i:i + 100] for i in range(0, len(поток_), 100)]
            return г.поток([x for к in куски for x in (None, г.нагрузка(к, upi=0xF0))], маска)
        поток_ = г.поток(кадры_эталона(12), 0x000119D8)
        for маска in (0xB6AB3325, 0x5A17C3E9, 0xB6AB31E0):
            поток_ = вложить(поток_, маска)
        ветвь = Ветвь()
        self.assertTrue(_вложенный_gfp(ветвь, gfp.gfp(г.в_ряд(поток_)), ""))
        self.assertEqual(["канальный"] * 3 + ["сетевой"], [н.уровень for н in ветвь.находки])
        self.assertEqual([0x5A17C3E9, 0xB6AB3325, 0x000119D8], [н.свойства["маска"] for н in ветвь.находки[:3]])
        ветвь = Ветвь()
        self.assertTrue(_вложенный_gfp(ветвь, gfp.gfp(г.в_ряд(вложить(поток_, 0x000119D8))), ""))
        self.assertEqual(["канальный"] * 3, [н.уровень for н in ветвь.находки])

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


def раскладка_gfp(п) -> list[tuple]:
    """(ключ, смещение, длина, сырое, плохо) всех полей уровня GFP с вложенными."""
    итог = []

    def обойти(поля_):
        for x in поля_:
            итог.append((x.ключ, x.смещение, x.длина, x.сырое, x.плохо))
            обойти(x.дети)
    обойти(п.уровни[0].поля)
    return итог


def кадр_gfp(область: bytes) -> bytes:
    """Кадр GFP для pcap: основной заголовок без маски (эталонная CRC) и область нагрузки."""
    pli = len(область).to_bytes(2, "big")
    return pli + г.crc16(pli).to_bytes(2, "big") + область


class РазборщикРаскладкаTests(unittest.TestCase):
    """Раскладка полей GFP-F по G.7041 (места, длины, сырые значения) и края разборщика."""

    def test_раскладка_линейного_кадра_с_pfcs(self):
        eth = ETHERNET[0]
        кадр = кадр_gfp(г.нагрузка(eth, pfi=True, cid=7))
        п = разобрать_пакет(кадр, "GFP-F")
        pli = len(кадр) - 4
        конец = len(кадр)
        chec, thec, ehec = (int.from_bytes(кадр[м:м + 2], "big") for м in (2, 6, 10))
        pfcs = int.from_bytes(кадр[-4:], "big")
        fcs = int.from_bytes(кадр[-8:-4], "little")
        self.assertEqual([("gfp.pli", 0, 2, pli, False), ("gfp.chec", 2, 2, chec, False),
                          ("gfp.type", 4, 2, 0x1101, False), ("gfp.pti", 4, 1, 0, False), ("gfp.pfi", 4, 1, 1, False),
                          ("gfp.exi", 4, 1, 1, False), ("gfp.upi", 5, 1, 1, False), ("gfp.thec", 6, 2, thec, False),
                          ("gfp.cid", 8, 1, 7, False), ("gfp.spare", 9, 1, 0, False), ("gfp.ehec", 10, 2, ehec, False),
                          ("gfp.fcs", конец - 4, 4, pfcs, False), ("eth.fcs", конец - 8, 4, fcs, False)],
                         раскладка_gfp(п))
        у = п.уровни[0]
        self.assertEqual((0, 12), (у.смещение, у.длина))
        self.assertEqual(["GFP", "Ethernet", "IPv4"], [x.протокол for x in п.уровни][:3])
        self.assertEqual(12, п.уровни[1].смещение)
        поля = {x.ключ: x.текст for x in у.поля}
        self.assertEqual(f"0x{pfcs:08x} [верна]", поля["gfp.fcs"])
        self.assertEqual(f"0x{thec:04x} [верен]", поля["gfp.thec"])
        # (Сумма TCP синтетического пакета не считана — это не GFP.)
        self.assertEqual([], [о for о in п.ошибки if not о.startswith("TCP")])
        # Запас не нуль — eHEC неверен и говорит, каким должен быть.
        плохой = bytearray(кадр)
        плохой[9] = 0x5A
        п = разобрать_пакет(bytes(плохой), "GFP-F")
        поле = [x for x in п.уровни[0].поля if x.ключ == "gfp.ehec"][0]
        self.assertEqual((True, f"0x{ehec:04x} [неверен, должен быть 0x{г.crc16(bytes([7, 0x5A])):04x}]"),
                         (поле.плохо, поле.текст))
        self.assertEqual(("gfp.spare", 9, 1, 0x5A, False), раскладка_gfp(п)[9])

    def test_без_pfcs_и_без_расширенного(self):
        кадр = кадр_gfp(г.нагрузка(ETHERNET[1]))
        п = разобрать_пакет(кадр, "GFP-F")
        self.assertEqual(["gfp.pli", "gfp.chec", "gfp.type", "gfp.pti", "gfp.pfi", "gfp.exi", "gfp.upi", "gfp.thec", "eth.fcs"],
                         [x[0] for x in раскладка_gfp(п)])
        self.assertEqual(("gfp.pfi", 4, 1, 0, False), раскладка_gfp(п)[4])
        self.assertEqual(8, п.уровни[0].длина)
        # pFCS над пустым полем данных: 4 байта после заголовков — это pFCS; 3 — не pFCS.
        п = разобрать_пакет(кадр_gfp(г.нагрузка(b"", pfi=True, upi=0xF0)), "GFP-F")
        self.assertEqual(("gfp.fcs", 8, 4, г.crc32_старшим(b""), False), раскладка_gfp(п)[-1])
        п = разобрать_пакет(кадр_gfp(г.нагрузка(b"", upi=0xF0)[:4] + b"\x01\x02\x03"), "GFP-F")
        self.assertNotIn("gfp.fcs", [x[0] for x in раскладка_gfp(п)])
        п = разобрать_пакет(кадр_gfp(г.нагрузка(b"", pfi=True, upi=0xF0)[:4] + b"\x01\x02\x03"), "GFP-F")
        self.assertNotIn("gfp.fcs", [x[0] for x in раскладка_gfp(п)])

    def test_хвост_за_кадром_не_входит(self):
        # Запись длиннее PLI: pFCS и FCS Ethernet — в конце кадра по PLI, а не записи.
        кадр = кадр_gfp(г.нагрузка(ETHERNET[2], pfi=True)) + b"\xAA\xBB\xCC\xDD\xEE"
        п = разобрать_пакет(кадр, "GFP-F")
        р = {x[0]: x for x in раскладка_gfp(п)}
        конец = len(кадр) - 5
        self.assertEqual(("gfp.fcs", конец - 4, 4, int.from_bytes(кадр[конец - 4:конец], "big"), False), р["gfp.fcs"])
        self.assertEqual(("eth.fcs", конец - 8, 4, int.from_bytes(кадр[конец - 8:конец - 4], "little"), False), р["eth.fcs"])
        # Запись короче PLI — ошибка «PLI больше записанного»; ровно по PLI — без ошибок.
        кадр = кадр_gfp(г.нагрузка(ETHERNET[2]))
        self.assertEqual([], [о for о in разобрать_пакет(кадр, "GFP-F").ошибки if not о.startswith("TCP")])
        self.assertIn(f"PLI {len(кадр) - 4} больше записанного ({len(кадр) - 5} байт)",
                      разобрать_пакет(кадр[:-1], "GFP-F").ошибки)

    def test_пустой_служебный_и_граница_кадра_клиента(self):
        п = разобрать_пакет(bytes(4), "GFP-F")
        self.assertEqual(("пустой кадр", 4, []), (п.уровни[0].итог, п.уровни[0].длина, п.ошибки))
        п = разобрать_пакет(bytes(5), "GFP-F")
        self.assertEqual(["в пустом кадре есть нагрузка"], п.ошибки)
        for pli in (1, 3):
            with self.subTest(pli=pli):
                п = разобрать_пакет(кадр_gfp(bytes(pli)), "GFP-F")
                self.assertEqual(("служебный кадр (резерв)", 4), (п.уровни[0].итог, п.уровни[0].длина))
        п = разобрать_пакет(кадр_gfp(г.нагрузка(b"", upi=0xF0)), "GFP-F")
        self.assertEqual("данные клиента: для нужд производителей, PLI 4", п.уровни[0].итог)

    def test_оборванные_заголовки(self):
        полный = кадр_gfp(г.нагрузка(ETHERNET[0], cid=1))
        for длина in (3, 7, 11):
            with self.subTest(длина=длина):
                п = разобрать_пакет(полный[:длина], "GFP-F")
                self.assertIn("пакет оборван: заголовок длиннее записанных байт", п.ошибки)
                self.assertFalse(any(о.startswith("разбор прерван") for о in п.ошибки), п.ошибки)
        п = разобрать_пакет(полный[:5], "GFP-F")
        self.assertIn("пакет оборван: заголовок длиннее записанных байт", п.ошибки)
        # Линейный заголовок без данных клиента — ровно 12 байт, не оборван.
        п = разобрать_пакет(кадр_gfp(г.нагрузка(b"", upi=0xF0, cid=3)), "GFP-F")
        self.assertEqual(([], 12), (п.ошибки, п.уровни[0].длина))
        self.assertEqual(("gfp.ehec", 10, 2, г.crc16(bytes([3, 0])), False), раскладка_gfp(п)[-1])

    def test_ethernet_от_18_байт_и_pti_101(self):
        # Самый короткий Ethernet с FCS — 18 байт (заголовок 14 + FCS 4); 17 — уже не Ethernet.
        короткий = г.ethernet_с_fcs(bytes(6) + bytes(6) + b"\x88\xb5")
        п = разобрать_пакет(кадр_gfp(г.нагрузка(короткий)), "GFP-F")
        self.assertEqual(["GFP", "Ethernet"], [x.протокол for x in п.уровни][:2])
        self.assertEqual(("eth.fcs", 22, 4, int.from_bytes(короткий[-4:], "little"), False), раскладка_gfp(п)[-1])
        п = разобрать_пакет(кадр_gfp(г.нагрузка(короткий[1:])), "GFP-F")
        self.assertNotIn("Ethernet", [x.протокол for x in п.уровни])
        # Управляющая связь (PTI 101) — тем же разбором клиента, что и данные.
        п = разобрать_пакет(кадр_gfp(г.нагрузка(ETHERNET[0], pti=5)), "GFP-F")
        self.assertEqual(["GFP", "Ethernet", "IPv4"], [x.протокол for x in п.уровни][:3])
        п = разобрать_пакет(кадр_gfp(г.нагрузка(ПАКЕТЫ[0], pti=5, upi=16)), "GFP-F")
        self.assertEqual(["GFP", "IPv4"], [x.протокол for x in п.уровни][:2])
        п = разобрать_пакет(кадр_gfp(г.нагрузка(ПАКЕТЫ[0], pti=6, upi=16)), "GFP-F")
        self.assertEqual(["GFP", "Данные"], [x.протокол for x in п.уровни][:2])

    def test_не_с_начала_записи(self):
        from reportgen.setevoy.protokoly import gfp as разборщик  # noqa: PLC0415
        from reportgen.setevoy.razbor import Пакет, Разбор  # noqa: PLC0415
        кадр = кадр_gfp(г.нагрузка(ETHERNET[0], cid=2))
        пакет = Пакет(1, 0.0, b"\x00\x00\x00" + кадр, len(кадр) + 3, "GFP-F")
        разборщик.gfp(Разбор(пакет), 3)
        у = пакет.уровни[0]
        self.assertEqual((3, 12), (у.смещение, у.длина))
        self.assertEqual("данные клиента: Ethernet (кадровое отображение), канал 2, PLI " + str(len(кадр) - 4), у.итог)

    def test_hec_и_pfcs_по_эталону(self):
        from reportgen.setevoy.protokoly import gfp as разборщик  # noqa: PLC0415
        g = np.random.default_rng(5)
        for _ in range(40):
            д = g.integers(0, 256, 2, dtype=np.uint8).tobytes()
            self.assertEqual(г.crc16(д), разборщик.hec(д))
            д = g.integers(0, 256, int(g.integers(0, 90)), dtype=np.uint8).tobytes()
            self.assertEqual(г.crc32_старшим(д), разборщик.pfcs(д))


@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноСтолаTests(unittest.TestCase):
    """Чистая логика окна GFP стола — прямо из app.js в node; слой окна понимает сервер."""

    def выполнить(self, случаи):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js(["маскаGFP", "слойGFP", "видКадраGFP", "проверкиКадраGFP", "ошибкаКадраGFP", "плиткиGFP",
                          "участокGFP", "дальшеGFP", "параметрыGFP"], [])
        код += """
const БИТ_УЧАСТКА_GFP = 1 << 25, БИТ_ДО_GFP = 1 << 26;
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(случаи.map(([ф, ...а]) => ({маскаGFP, слойGFP, видКадраGFP, проверкиКадраGFP, ошибкаКадраGFP, плиткиGFP,
    участокGFP, дальшеGFP, параметрыGFP})[ф](...а))));
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
            ["слойGFP", {"маска": "авто", "скремблер": "да", "сБита": 4096, "канал": 2, "выход": "клиенты"}],
            ["слойGFP", {"сБита": 0}], ["слойGFP", {"сБита": 1}],
        ])
        self.assertEqual(["авто", "000119D8", "B6AB31E0", None, None, "авто",
                          "gfp маска 000119D8 скремблер нет порядок младший многочлен авто канал 0 выход клиенты",
                          "gfp маска авто скремблер авто выход поток", "gfp маска авто скремблер авто выход поток",
                          "gfp маска авто скремблер да с бита 4096 канал 2 выход клиенты",
                          "gfp маска авто скремблер авто выход поток", "gfp маска авто скремблер авто с бита 1 выход поток"], итог)
        self.assertEqual(4096, gfp.разобрать_указание(итог[9])["с_бита"])
        # Слой окна понимает ручной слой сервера.
        self.assertEqual({"маска": 0x000119D8, "скремблер": "нет", "порядок": "младший", "многочлен": "авто",
                          "с_бита": 0, "канал": 0, "выход": "клиенты"}, gfp.разобрать_указание(итог[6]))

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
        # Поле типа кадра в прогреве не прочитано — ни PTI, ни UPI.
        self.assertEqual([None] * 4, [строки[1][к] for к in ("pti", "pfi", "exi", "upi")])
        виды, проверки, ошибки = итог[1::3], итог[2::3], итог[3::3]
        self.assertEqual(["пустой", "прогрев x⁴³", "пустой", "пустой", "прогрев x⁴³", "пустой"], виды[:6])
        self.assertEqual("данные · Ethernet (кадровое отображение)", виды[6])
        self.assertEqual("cHEC верен · tHEC прогрев", проверки[4])
        self.assertEqual("cHEC верен · tHEC верен · eHEC верен · pFCS верна", проверки[6])
        self.assertEqual("cHEC верен · tHEC верен · eHEC верен · pFCS неверна", проверки[8])
        self.assertEqual([False] * 8 + [True], ошибки)

    def test_плитки_каждая_строкой(self):
        # Сводка, собранная вручную: у каждой плитки — точный текст (числа по-русски, с пробелами).
        св = {"маска": "5A17C3E9", "маска_словами": "нестандартная", "пустой": "5A 17 C3 E9", "доля": 0.98765,
              "скремблер": "нет",
              "счёт": {"кадров": 12345, "пустых": 1000, "служебных": 2, "данных_клиента": 3000, "cmf": 7,
                       "исправлено_chec": 4, "thec": {"верен": 2990, "исправлен": 3, "ошибка": 5, "прогрев": 2},
                       "pfcs": {"неверна": 2}, "cid": {"0": 1500, "5": 1500}, "длины": {"от": 64, "до": 1522},
                       "потерь": 6, "потери_бит": [8, 16000, 24, 32, 40, 48],
                       "типы": [{"pti": 0, "upi": 16, "upi_словами": "IPv4", "кадров": 2000},
                                {"pti": 0, "upi": 16, "upi_словами": "IPv4", "кадров": 1000},
                                {"pti": 4, "upi": 240, "upi_словами": "свой", "кадров": 7},
                                {"pti": 0, "upi": 3, "upi_словами": "FC"}]}}
        пустая = {"маска": "B6AB31E0", "маска_словами": "стандарт", "пустой": "B6", "скремблер": "x43",
                  "счёт": {"потерь": 5, "потери_бит": [1, 2, 3, 4, 5], "thec": {}, "pfcs": {"верна": 3}}}
        голая = {"маска": "1", "маска_словами": "м", "пустой": "п", "скремблер": "инверсия", "счёт": {}}
        итог = self.выполнить([["плиткиGFP", св], ["плиткиGFP", пустая], ["плиткиGFP", голая]])
        self.assertEqual([
            ["Маска", "5A17C3E9 — нестандартная"], ["Пустой кадр на линии", "5A 17 C3 E9"],
            ["Кадров", "12\xa0345 · в синхронизме 98,8 % байт"], ["Пустых / служебных", "1\xa0000 / 2"],
            ["С данными клиента / CMF", "3\xa0000 / 7"], ["Исправлено cHEC", "4"],
            ["tHEC", "2\xa0990 верен, 3 исправлен, 5 ошибка, 2 прогрев"], ["pFCS", "0 верна из 2"],
            ["UPI", "0x10 IPv4 × 3\xa0000; CMF 0xF0 свой × 7; 0x03 FC × 0"], ["CID", "0 × 1\xa0500, 5 × 1\xa0500"],
            ["Длины (PLI)", "64…1522"], ["Потери синхронизации", "6 — с бита 8, 16\xa0000, 24, 32, 40…"],
            ["Скремблер", "нет"]], итог[0])
        self.assertEqual([
            ["Маска", "B6AB31E0 — стандарт"], ["Пустой кадр на линии", "B6"], ["Кадров", "0 · в синхронизме 0,0 % байт"],
            ["Пустых / служебных", "0 / 0"], ["С данными клиента / CMF", "0 / 0"], ["Исправлено cHEC", "0"],
            ["tHEC", "0 верен"], ["pFCS", "3 верна из 3"], ["Потери синхронизации", "5 — с бита 1, 2, 3, 4, 5"],
            ["Скремблер", "x⁴³ + 1 снят"]], итог[1])
        self.assertEqual(["Потери синхронизации", "нет"], итог[2][-2])
        self.assertEqual(["Скремблер", "нет, нагрузка инвертирована"], итог[2][-1])

    def test_участок_и_дальше(self):
        случаи = [{"от": 0, "до": 8192, "всего": 19200}, {"от": 8192, "до": 16384, "всего": 19200},
                  {"от": 16384, "до": 19200, "всего": 19200}, {"от": 0, "до": 19200, "всего": 19200}, None,
                  {"от": 1, "до": 19200, "всего": 19200}]
        итог = self.выполнить([["участокGFP", {"участок": у}] for у in случаи] + [["участокGFP", None]]
                              + [["плиткиGFP", {"маска": "1", "пустой": "п", "счёт": {}, "участок": у}] for у in случаи[:5]])
        self.assertEqual([{"текст": "биты 0…8\xa0191 из 19\xa0200", "дальше": 8192},
                          {"текст": "биты 8\xa0192…16\xa0383 из 19\xa0200", "дальше": 16384},
                          {"текст": "биты 16\xa0384…19\xa0199 из 19\xa0200", "дальше": None}, None, None,
                          {"текст": "биты 1…19\xa0199 из 19\xa0200", "дальше": None}, None], итог[:7])
        self.assertEqual([["Участок", "биты 0…8\xa0191 из 19\xa0200"], ["Участок", "биты 8\xa0192…16\xa0383 из 19\xa0200"],
                          ["Участок", "биты 16\xa0384…19\xa0199 из 19\xa0200"]], [п[-1] for п in итог[7:10]])
        self.assertEqual(["Скремблер", "Скремблер"], [п[-1][0] for п in итог[10:]])
        итог = self.выполнить([["дальшеGFP", {"участок": у}] for у in случаи] + [["дальшеGFP", None]])
        self.assertEqual(["Дальше — с бита 8\xa0192", "Дальше — с бита 16\xa0384", None, None, None, None, None], итог)

    def test_параметры_запроса(self):
        поля = {"stage": 2, "маска": "авто", "скремблер": "да", "порядок": "младший", "многочлен": "авто"}
        итог = self.выполнить([["параметрыGFP", {**поля, "первый": п, "бит": б}] for п, б in
                               ((8192.4, 33554432), (-5, 0), (None, 100), (7, 1 << 30), (0, 5000.6), (0, 4096))])
        self.assertEqual({"stage": 2, "mask": "авто", "scrambler": "да", "order": "младший", "poly": "авто",
                          "first": 8192, "bits": 1 << 25}, итог[0])
        self.assertEqual([(8192, 1 << 25), (0, 1 << 25), (0, 4096), (7, 1 << 26), (0, 5001), (0, 4096)],
                         [(д["first"], д["bits"]) for д in итог])

    def test_окно_участок_и_дальше_в_коде(self):
        from test_potok_sessii import APP_JS  # noqa: PLC0415
        текст = APP_JS.read_text(encoding="utf-8")
        for кусок in ("const БИТ_УЧАСТКА_GFP = 1 << 25, БИТ_ДО_GFP = 1 << 26;", "первый.узел, длина.узел,",
                      "первый: первый.значение(), бит: длина.значение() });",
                      "плитки, дальше, h('div', { class: 'stol-results-wrap' }, таблица), ещё)",
                      "первый.поле.value = String(участокGFP(св).дальше); найти(false);",
                      "дальше.hidden = надпись === null;\n                дальше.textContent = надпись || 'Дальше';",
                      "[своя, первый.поле, длина.поле].forEach((п) => п.addEventListener('keydown', (e) => { if (e.key === 'Enter') {",
                      "[первый, длина].forEach((п) => п.узел.classList.add('field--stack'));"):
            self.assertIn(кусок, текст)

    def test_уведомления_над_подвалом_окна(self):
        # Уведомление не закрывает кнопки подвала модального окна: пока окно открыто, стопка
        # уведомлений стоит над его подвалом (глазами — снимок сценария рецензента).
        from test_potok_sessii import APP_JS, функции_js  # noqa: PLC0415
        код = функции_js(["местоУведомлений"], []) + """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const box = { style: { bottom: 'было' } };
let подвалы = [], запрос = null;
const $ = (с) => (с === '#toasts' ? box : null);
const window = { innerHeight: 900 };
const document = { querySelectorAll: (с) => { запрос = с; return подвалы; } };
process.stdout.write(JSON.stringify(случаи.map((верхи) => {
    подвалы = верхи.map((в) => ({ childElementCount: в === null ? 0 : 3, getBoundingClientRect: () => ({ top: в }) }));
    местоУведомлений();
    return [box.style.bottom, запрос];
})));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps([[], [800], [700, 800.4], [890], [899], [None]]),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(0, готово.returncode, готово.stderr)
        итог = json.loads(готово.stdout)
        # Плавающие окна (поиск, декодеры) уведомления не сдвигают: под ними страница, а не затемнение.
        self.assertEqual({"#modal-root > .modal-backdrop:not(.modal-backdrop--float) > .modal > footer"}, {з for _, з in итог})
        # Без окна — место из CSS; над подвалом верхнего окна на 8 пикселей, не ниже 16; пустой подвал — как без окна.
        self.assertEqual(["", "108px", "108px", "18px", "16px", ""], [б for б, _ in итог])
        текст = APP_JS.read_text(encoding="utf-8")
        for где in ("box.appendChild(node);\n        местоУведомлений();", "root.appendChild(backdrop);\n        местоУведомлений();",
                    "backdrop.remove();\n            местоУведомлений();", "window.addEventListener('resize', () => местоУведомлений());"):
            self.assertIn(где, текст)
        self.assertEqual((1 << 25, 1 << 26), (gfp.БИТ_ДО // 2, gfp.БИТ_ДО))

    def test_маска_вид_и_ошибка_строки(self):
        итог = self.выполнить([
            ["маскаGFP", "своя", "99999999"], ["маскаGFP", "своя", "0x0001 19d8"],
            ["видКадраGFP", {"вид": "служебный", "upi_словами": "x"}], ["видКадраGFP", {"вид": "пустой", "оборван": True}],
            ["видКадраGFP", {"вид": "CMF", "upi_словами": "RDI", "оборван": True}], ["видКадраGFP", {"вид": "данные"}],
            ["ошибкаКадраGFP", {"ehec": "ошибка"}], ["ошибкаКадраGFP", {"thec": "ошибка"}], ["ошибкаКадраGFP", {"pfcs": "неверна"}],
            ["ошибкаКадраGFP", {"оборван": True}], ["ошибкаКадраGFP", {"thec": "исправлен", "ehec": "верен", "pfcs": "верна", "оборван": False}],
            ["проверкиКадраGFP", {"chec": "исправлен"}]])
        self.assertEqual(["99999999", "000119D8", "служебный", "пустой", "CMF · RDI · оборван", "данные",
                          True, True, True, True, False, "cHEC исправлен"], итог)

    def test_справка_окна(self):
        from test_potok_sessii import функции_js  # noqa: PLC0415
        код = функции_js([], ["СПРАВКА_GFP"]) + "process.stdout.write(JSON.stringify(СПРАВКА_GFP));"
        готово = subprocess.run(["node", "-e", код], capture_output=True, text=True, timeout=20)
        self.assertEqual(0, готово.returncode, готово.stderr)
        справка = dict(json.loads(готово.stdout))
        for что, текст in справка.items():
            self.assertNotIn("NaN", текст, что)
        маска = справка["Маска"]
        self.assertIn("одинаков при любой маске", маска)
        self.assertIn("бит — места заголовков", маска)
        self.assertIn("Предустановки: B6AB31E0 (G.7041)", маска)
        self.assertIn("HUNT → PRESYNC → SYNC", справка["Выделение кадров"])
        self.assertIn("F1", справка)
        self.assertIn("«разобраны биты X…Y из Z», «Дальше» — следующий участок", справка["Участок"])

    def test_окно_в_меню_и_клавишах(self):
        from test_potok_sessii import APP_JS  # noqa: PLC0415
        текст = APP_JS.read_text(encoding="utf-8")
        for кусок in ("{ id: 'k-gfp', раздел: 'Кадры', имя: 'GFP (G.7041)…', вид: 'действие', сделать: 'gfp-окно' }",
                      "case 'gfp-окно': окноGFP(у); return null;", "else if (код === 'KeyG') окноGFP(у);",
                      "['G', 'GFP (G.7041)", "сохранитьСтола('stol-gfp'", "'/gfp/pakety'", "if (e.key === 'F1') { справка();"):
            self.assertIn(кусок, текст)
        # Слои GFP шпаргалки понимает ручной слой, «с бита» — тоже.
        слои = re.findall(r"\['GFP[^']*', '(gfp [^']*)'\]", текст)
        self.assertEqual(3, len(слои))
        self.assertEqual([0, 0, 4096], [gfp.разобрать_указание(с_)["с_бита"] for с_ in слои])


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
        нет = к.post(f"/api/potok/{ид}/gfp/pakety", json={"mask": "авто", "cid": 7})
        self.assertEqual((400, "кадров с данными нет в канале CID 7"), (нет.status_code, нет.json()["error"]))
        self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp/pakety", json={"cid": "два"}).status_code)
        # Имя захвата: с каналом CID и без.
        имена = []
        for тело in ({"cid": 2}, {}):
            ответ = к.post(f"/api/potok/{ид}/gfp/pakety", json=тело).json()
            имена.append(self.дождаться(ответ["id"], "pakety")["имя"])
        self.assertEqual(["gfp — GFP клиенты, CID 2, массив 0.pcap", "gfp — GFP клиенты, массив 0.pcap"], имена)
        self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp/pakety", json={"mask": "B6AB31E0"}).status_code)

    def test_участок_в_сводке_и_дальше(self):
        # Окно разбирает участок: сводка пишет, какие биты разобраны, и откуда дальше; слои — с того же места.
        from unittest import mock  # noqa: PLC0415

        к = self.к
        self.сеть.login("engineer")
        сид = к.post("/api/sessions", json={"name": "gfp-у"}).json()["id"]
        ид = self.загрузить(сид, "пустые.bin", г.поток([None] * 600))          # 19 200 бит
        with mock.patch.object(gfp, "БИТ_ДО", 1 << 14):
            св = к.post(f"/api/potok/{ид}/gfp", json={}).json()["сводка"]
            self.assertEqual({"от": 0, "до": 8192, "всего": 19200}, св["участок"])
            self.assertEqual("разобраны биты 0…8191 из 19200; дальше — с бита 8192", св["строки"][-1])
            self.assertEqual("gfp маска B6AB31E0 скремблер да выход поток", св["слой_поток"])
            св = к.post(f"/api/potok/{ид}/gfp", json={"first": 8192, "bits": 8192}).json()["сводка"]
            self.assertEqual({"от": 8192, "до": 16384, "всего": 19200}, св["участок"])
            self.assertEqual("разобраны биты 8192…16383 из 19200; дальше — с бита 16384", св["строки"][-1])
            self.assertEqual("gfp маска B6AB31E0 скремблер да с бита 8192 выход поток", св["слой_поток"])
            св = к.post(f"/api/potok/{ид}/gfp", json={"first": 16384, "bits": 8192}).json()["сводка"]
            self.assertEqual({"от": 16384, "до": 19200, "всего": 19200}, св["участок"])
            self.assertEqual("разобраны биты 16384…19199 из 19200", св["строки"][-1])
            св = к.post(f"/api/potok/{ид}/gfp", json={"bits": 1 << 20}).json()["сводка"]
            self.assertEqual({"от": 0, "до": 16384, "всего": 19200}, св["участок"])
        св = к.post(f"/api/potok/{ид}/gfp", json={}).json()["сводка"]
        self.assertEqual({"от": 0, "до": 19200, "всего": 19200}, св["участок"])
        self.assertFalse(any(с_.startswith("разобраны биты") for с_ in св["строки"]))

    def test_не_gfp_найдено_нет_с_причиной(self):
        # SDH без GFP: «заголовки» есть, осмысленных кадров нет — окно пишет «найдено: нет» и почему.
        к = self.к
        self.сеть.login("engineer")
        сид = к.post("/api/sessions", json={"name": "sdh"}).json()["id"]
        ид = self.загрузить(сид, "sdh.bin", к_.sdh(random.Random(1000), 1 << 18))
        d = к.post(f"/api/potok/{ид}/gfp", json={"mask": "авто"}).json()
        self.assertFalse(d["найдено"])
        self.assertRegex(d["причина"], "^заголовки с верным cHEC есть .* — не GFP")
        ответ = к.post(f"/api/potok/{ид}/gfp/pakety", json={"mask": "авто"})
        self.assertEqual(400, ответ.status_code)
        self.assertRegex(ответ.json()["error"], "^GFP не найден: заголовки с верным cHEC есть")

    def загрузить(self, сид, имя, данные):
        ид = self.к.post(f"/api/sessions/{сид}/files", data={"bit_order": "msb"},
                         files={"file": (имя, данные, "application/octet-stream")}).json()["id"]
        self.дождаться(ид)
        return ид

    def test_параметры_участок_таблица_и_кэш(self):
        from unittest import mock  # noqa: PLC0415

        к = self.к
        self.сеть.login("engineer")
        сид = к.post("/api/sessions", json={"name": "gfp-п"}).json()["id"]
        пустые = г.поток([None] * 600)                       # 2400 байт — 600 пустых кадров
        ид = self.загрузить(сид, "пустые.bin", пустые)
        сдвинутые = np.packbits(np.concatenate([[1], г.в_ряд(пустые)[:-1]]).astype(np.uint8)).tobytes()
        ид1 = self.загрузить(сид, "сдвиг.bin", сдвинутые)
        def всего(ид_, **тело):
            d = к.post(f"/api/potok/{ид_}/gfp", json={"idle": True, **тело}).json()
            return d["таблица"]["всего"] if d.get("найдено") else d
        with mock.patch.object(gfp, "БИТ_ДО", 1 << 14):
            # Участок: по умолчанию половина предела (8192 бит = 256 кадров), не меньше 4096 бит.
            self.assertEqual(256, всего(ид))
            self.assertEqual(128, всего(ид, bits=1))
            self.assertEqual(127, всего(ид1, bits=1))     # с бита 1 — 4095 бит, 511 байт
            self.assertEqual(512, всего(ид, bits=1 << 20))
            # Страница таблицы: с нулевого по умолчанию, 200 строк, не больше 500 и не меньше 1.
            def строки(**тело):
                т = к.post(f"/api/potok/{ид}/gfp", json={"idle": True, "bits": 1 << 14, **тело}).json()["таблица"]
                return т["от"], len(т["строки"]), т["строки"][0]["№"]
            self.assertEqual((0, 200, 0), строки())
            self.assertEqual((0, 500, 0), строки(limit=10000))
            self.assertEqual((0, 1, 0), строки(limit=-3, offset=-5))
            self.assertEqual(400, к.post(f"/api/potok/{ид}/gfp", json={"offset": "abc"}).status_code)
            # Маска с 0x, причина «не найдено» с заданной маской.
            self.assertEqual("B6AB31E0", к.post(f"/api/potok/{ид}/gfp", json={"mask": "0xB6AB31E0"}).json()["сводка"]["маска"])
            d = к.post(f"/api/potok/{ид}/gfp", json={"mask": "000119D8"}).json()
            self.assertFalse(d["найдено"])
            self.assertIn("с этой маской", d["причина"])
            # Разборы помнятся (три последних): повтор первого из трёх — без нового расчёта.
            настоящий = gfp.разобрать_с_причиной
            with mock.patch.object(gfp, "разобрать_с_причиной", side_effect=настоящий) as разбор:
                for бит in (4096, 5000, 6000, 4096):
                    всего(ид, bits=бит)
                self.assertEqual(3, разбор.call_count)
        # Другой многочлен CRC-16 — только с «poly: авто».
        кадры = [None, None] + [г.нагрузка(п, многочлен=0x8005) for п in ETHERNET[:20]]
        ид2 = self.загрузить(сид, "8005.bin", г.поток(кадры, 0x12345678, многочлен=0x8005))
        self.assertFalse(к.post(f"/api/potok/{ид2}/gfp", json={}).json()["найдено"])
        self.assertEqual("12345678", к.post(f"/api/potok/{ид2}/gfp", json={"poly": "авто"}).json()["сводка"]["маска"])


if __name__ == "__main__":
    unittest.main()
