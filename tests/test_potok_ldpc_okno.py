"""Окно «LDPC» стола и папка матриц отдела: типы кода, файлы матриц, кадры модема, постобработка, API.

Эталоны независимые: слова F-LDPC — кодер патента из test_potok_ldpc_semeystva (по тексту патента),
ПСП DVB — по описанию EN 302 307-1 5.2.2 (регистр 15 ячеек, загрузка 100101010000000, выход x14 ⊕ x15),
кадры модема и распределённое синхрослово — собраны здесь же по описанию окна.
"""

import base64
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import ldpc, ldpc_flex, ldpc_katalog, ldpc_okno, ldpc_std, sinhro
from reportgen.potok.razbor import снять_вручную
from test_potok_ldpc_semeystva import ИСТОЧНИКИ, ПатентFLDPC, есть_источники

СЛОВО = "0x1ACFFC1D"


def псп_dvb(n: int) -> np.ndarray:
    """ПСП DVB по тексту стандарта: 1 + X14 + X15, в регистр грузится 100101010000000 (EN 302 307-1, 5.2.2)."""
    текст = (ИСТОЧНИКИ / "standarty" / "ETSI_EN_302_307-1_v1.4.1_DVB-S2.pdf.txt").read_text(encoding="utf-8")
    загрузка = текст[текст.index("Loading of the sequence (") + 25:][:15]
    assert "1 + X14 + X15" in текст and set(загрузка) <= {"0", "1"}
    r = [int(ч) for ч in загрузка]
    итог = []
    for _ in range(n):
        b = r[13] ^ r[14]
        итог.append(b)
        r = [b] + r[:-1]
    return np.array(итог, dtype=np.uint8)


def кадры_модема(слова: list[np.ndarray], N: int, *, распределённое=False, сдвиг=0, сид=1):
    """Кадры по N бит: синхрослово (в начале или по биту через N/s) и слова кода подряд, хвост — нули."""
    rng = np.random.default_rng(сид)
    с = sinhro.слово(СЛОВО)
    s = len(с)
    места = np.arange(s) * (N // s) if распределённое else np.arange(s)
    на_кадр = (N - s) // len(слова[0])
    итог = [rng.integers(0, 2, сдвиг).astype(np.uint8)]
    for i in range(0, len(слова) - на_кадр + 1, на_кадр):
        нагрузка = np.concatenate(слова[i:i + на_кадр] + [np.zeros(N - s - на_кадр * len(слова[0]), np.uint8)])
        кадр = np.zeros(N, np.uint8)
        маска = np.zeros(N, bool)
        маска[места] = True
        кадр[места] = с
        кадр[~маска] = нагрузка
        итог.append(кадр)
    return np.concatenate(итог)


class ПапкаОтдела(unittest.TestCase):
    def setUp(self):
        self._было = ldpc.КАТАЛОГ
        self.папка = Path(tempfile.mkdtemp()) / "ldpc"
        ldpc.подготовить_каталог(self.папка)

    def tearDown(self):
        ldpc.КАТАЛОГ = self._было

    def test_создаётся_с_памяткой(self):
        self.assertTrue(self.папка.is_dir())
        self.assertIn("alist", (self.папка / "_ПРОЧТИ.txt").read_text(encoding="utf-8"))
        self.assertEqual([], ldpc.список())                 # памятка с «_» — не матрица

    def test_виды_файлов_и_ошибка(self):
        # Базовая с Z и схемой в примечаниях; H в тексте; адреса DVB; битый alist.
        (self.папка / "comtech 16k.txt").write_text(
            "# откуда: руководство отдела\n# выколоты: 0-3\nZ = 8\n0 -1 3 0 -\n- 5 0 0 0\n2 1 - 0 0\n", encoding="utf-8")
        (self.папка / "h.h").write_text("110100\n011010\n001101\n", encoding="utf-8")
        (self.папка / "dvb.adr").write_text("n = 1440\nk = 720\n0 5 100\n7 300 600\n", encoding="utf-8")
        (self.папка / "плохой.alist").write_text("ерунда", encoding="utf-8")
        (self.папка / "_черновик.txt").write_text("1 0", encoding="utf-8")
        список = {э["имя"]: э for э in ldpc.список()}
        self.assertEqual({"comtech_16k", "h", "dvb", "плохой"}, set(список))
        self.assertEqual(("базовая", 40, "0-3", "руководство отдела"),
                         (список["comtech_16k"]["вид"], список["comtech_16k"]["n"], список["comtech_16k"]["выколоты"],
                          список["comtech_16k"]["откуда"]))
        self.assertEqual(("H в тексте", 6, 3), (список["h"]["вид"], список["h"]["n"], список["h"]["k"]))
        self.assertEqual(("адреса", 1440, 720), (список["dvb"]["вид"], список["dvb"]["n"], список["dvb"]["k"]))
        self.assertIn("ошибка", список["плохой"])
        self.assertEqual(36, ldpc.схема_сохранённая("comtech_16k").длина)
        with self.assertRaises(ValueError):
            ldpc.прочитать("плохой")
        # Та же матрица, что строит ldpc напрямую — файл читается как написан.
        self.assertTrue(np.array_equal(ldpc.прочитать("h").плотная(),
                                       np.array([[1, 1, 0, 1, 0, 0], [0, 1, 1, 0, 1, 0], [0, 0, 1, 1, 0, 1]])))
        # Имя из «_z8» в имени файла — тоже базовая.
        (self.папка / "код_z4.qc").write_text("0 1 -\n2 - 0\n", encoding="utf-8")
        self.assertEqual("базовая", next(э for э in ldpc.список() if э["имя"] == "код_z4")["вид"])

    def test_файл_меняется_без_перезапуска(self):
        путь = self.папка / "m.h"
        путь.write_text("1100\n0110\n", encoding="utf-8")
        self.assertEqual(4, ldpc.прочитать("m").n)
        путь.write_text("110000\n011000\n001100\n", encoding="utf-8")
        import os  # noqa: PLC0415
        os.utime(путь, ns=(1, 10**18))
        self.assertEqual(6, ldpc.прочитать("m").n)

    @есть_источники
    def test_автомат_пробует_файл_отдела(self):
        """Файл отдела с перемежением 1367 в примечании: автомат находит код сам (без встроенных)."""
        патент = ПатентFLDPC()
        м = ldpc_std.матрица("fldpc-k256-j4-p16")
        строки = "\n".join("".join("1" if j in set(с.tolist()) else "0" for j in range(м.n)) for с in м.строки)
        (self.папка / "datum-256-23.h").write_text("# перемежение: 1367\n" + строки, encoding="utf-8")
        rng = np.random.default_rng(3)
        u = rng.integers(0, 2, (30, 256)).astype(np.uint8)
        поток = np.concatenate([rng.integers(0, 2, 41).astype(np.uint8)]
                               + [np.array(патент.кодировать(x.tolist(), 4, 16, True), np.uint8) for x in u])
        найдено = ldpc.найти_по_матрицам(поток)
        self.assertEqual(("datum-256-23", 41), (найдено.свойства["матрица"], найдено.свойства["начало"]))
        self.assertTrue(np.array_equal(найдено.дальше.reshape(-1, 256)[:29], u[:29]))
        # Тип «Datum 256» окна видит файл «datum…» у каждой скорости.
        датум = next(т for т in ldpc_katalog.с_файлами(ldpc.список()) if т["тип"] == "Datum 256")
        self.assertTrue(all("datum-256-23" in с["коды"] for с in датум["скорости"]))
        нестандарт = next(т for т in ldpc_katalog.с_файлами(ldpc.список()) if т["тип"] == "Нестандарт")
        self.assertEqual(["datum-256-23"], [к for с in нестандарт["скорости"] for к in с["коды"]])


class Каталог(unittest.TestCase):
    def test_список_отдела_по_порядку(self):
        типы = [т["тип"] for т in ldpc_katalog.типы()]
        отдел = ["Comtech", "Versa FEC BPSK", "Versa FEC QPSK", "Versa FEC 8QAM", "Versa FEC 16QAM", "Versa FEC ULL BPSK",
                 "Versa FEC ULL QPSK", "Datum 256", "Datum 512", "Datum 1K", "Datum 2K", "Datum 4K", "Datum 8K", "Datum 16K",
                 "DVB-S2 normal", "DVB-S2 short", "Paradise 256", "Paradise 512", "Paradise 1K", "Paradise 2K",
                 "Paradise 4K", "Paradise 8K", "Paradise 16K"]
        self.assertEqual(отдел, типы[:len(отдел)])
        self.assertEqual("Нестандарт", типы[-1])
        for т in ldpc_katalog.типы():
            with self.subTest(тип=т["тип"]):
                self.assertTrue(т["источник"])
                if т["закрыт"]:
                    self.assertTrue(all(not с["коды"] for с in т["скорости"]))
                    self.assertIn("не опубликована", т["пометка"])
                for с in т["скорости"]:
                    for и in с["коды"]:
                        self.assertTrue(ldpc_std.есть(и), и)

    def test_режимы_по_руководствам(self):
        по = {т["тип"]: [с["подпись"] for с in т["скорости"]] for т in ldpc_katalog.типы()}
        self.assertEqual(["1/2", "2/3", "3/4"], по["Comtech"])
        self.assertEqual(["0.533", "0.631", "0.706", "0.803"], по["Versa FEC QPSK"])
        self.assertEqual(["0.576", "0.642", "0.711", "0.780"], по["Versa FEC 8QAM"])
        self.assertEqual(["0.493", "0.654", "0.734"], по["Versa FEC ULL QPSK"])
        # FlexLDPC (M7): 7 скоростей (White Paper стр. 3); у 2k…16k ещё LDPC-Gen2 (M7XC MIB) — 12, три общие.
        флекс = ["1/2", "2/3", "3/4", "14/17", "7/8", "10/11", "16/17"]
        ген2 = ["1/2", "8/15", "4/7", "8/13", "2/3", "16/23", "8/11", "16/21", "4/5", "16/19", "8/9", "16/17"]
        self.assertEqual(флекс, по["Datum 1K"])
        self.assertEqual(флекс + [с for с in ген2 if с not in флекс], по["Datum 16K"])
        датум16 = {с["подпись"]: с for с in next(т for т in ldpc_katalog.типы() if т["тип"] == "Datum 16K")["скорости"]}
        # 2/3: у FlexLDPC — J = 4 без выкалывания; у Gen2 — все (J, P) патента с этой скоростью.
        self.assertEqual(ldpc_flex.варианты_скорости("2/3", K=16384)[0], (4, 16))
        self.assertEqual([ldpc_flex.имя(16384, J, P) for J, P in ldpc_flex.варианты_скорости("2/3", K=16384)],
                         датум16["2/3"]["коды"])
        self.assertIn("Datum M7XC LDPC-Gen2", датум16["2/3"]["примечание"])
        self.assertIn("Datum FlexLDPC (M7, PSM-500)", датум16["2/3"]["примечание"])
        self.assertEqual(11, len(по["DVB-S2 normal"]))
        self.assertEqual(10, len(по["DVB-S2 short"]))
        датум = {с["подпись"]: с["коды"] for с in next(т for т in ldpc_katalog.типы() if т["тип"] == "Datum 1K")["скорости"]}
        self.assertEqual({"1/2": ["fldpc-k1024-j2-p16"], "3/4": ["fldpc-k1024-j6-p16"], "14/17": []},
                         {к: датум[к] for к in ("1/2", "3/4", "14/17")})
        fl = next(т for т in ldpc_katalog.типы() if т["тип"] == "F-LDPC TrellisWare (патент)")
        # 8 длин × (45 скоростей патента − 4 равносильных (J, 8/16) = (2J, 16/16) + J = 6, 14, 20).
        self.assertEqual(8 * (45 - 4 + 3), len(fl["скорости"]))

    @есть_источники
    def test_режимы_versafec_в_руководстве(self):
        """Скорости VersaFEC каталога стоят в руководстве CDM-625A (текст PDF, стр. 553 и 555)."""
        import pymupdf  # noqa: PLC0415
        d = pymupdf.open(ИСТОЧНИКИ / "comtech" / "MN-CDM625A_rev4_skybrokers.pdf")
        текст = d[552].get_text() + d[554].get_text()
        for т in ldpc_katalog.типы():
            if т["тип"].startswith("Versa FEC"):
                for с in т["скорости"]:
                    self.assertIn(с["подпись"], текст, (т["тип"], с["подпись"]))


@есть_источники
class КадрыИПостобработка(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.патент = ПатентFLDPC()
        rng = np.random.default_rng(21)
        cls.данные = rng.integers(0, 2, (24, 512)).astype(np.uint8)

    def слова(self, dvb=False):
        П = псп_dvb(512) if dvb else np.zeros(512, np.uint8)
        return [np.array(self.патент.кодировать((d ^ П).tolist(), 2, 12), np.uint8) for d in self.данные]

    def test_dvb_первые_биты_по_стандарту(self):
        self.assertEqual("".join(map(str, псп_dvb(15).tolist())), ldpc_okno.dvb_первые())
        from reportgen.potok import dvbs2  # noqa: PLC0415
        self.assertTrue(np.array_equal(псп_dvb(3000), dvbs2._псп()[:3000]))

    def test_сосредоточенное_с_хвостом_и_dvb(self):
        слова = self.слова(dvb=True)
        L = len(слова[0])                                  # 512 + 384 = 896
        поток = кадры_модема(слова, 32 + 2 * L + 13, сдвиг=77)
        п = {"код": "fldpc-k512-j2-p12", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": 32 + 2 * L + 13,
                                                     "длина_слова": 32}, "аддитивный_dvb": True}
        итог = ldpc_okno.просмотр(поток, п)
        self.assertEqual(100.0, итог["сошлось"])
        # ПСП DVB сбрасывается в начале каждого BBFRAME — данных одного слова (512 бит), не кадра модема.
        self.assertEqual(f"аддитивный отводы 14,15 кадр 512 начальное {ldpc_okno.dvb_первые()}", итог["слои"][1])
        self.assertTrue(np.array_equal(итог["биты"].reshape(-1, 512), self.данные[:len(итог["биты"]) // 512]))
        self.assertIn("хвост 13 бит (заполнение) отброшен", " ".join(итог["подробно"]))

    def test_dvb_с_синхрословом_на_выходе(self):
        слова = self.слова(dvb=True)
        L = len(слова[0])
        N = 32 + L + 5                                     # одно слово в кадре и хвост
        поток = кадры_модема(слова, N, сдвиг=3)
        п = {"код": "fldpc-k512-j2-p12", "аддитивный_dvb": True,
             "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N, "выводить": True}}
        итог = ldpc_okno.просмотр(поток, п)
        self.assertEqual(f"аддитивный отводы 14,15 кадр 544 начальное {ldpc_okno.dvb_первые()} пропуск 32", итог["слои"][1])
        блоки = итог["биты"].reshape(-1, 544)
        self.assertTrue(np.array_equal(блоки[:, :32], np.tile(sinhro.слово(СЛОВО), (len(блоки), 1))))
        self.assertTrue(np.array_equal(блоки[:, 32:], self.данные[:len(блоки)]))
        # Два слова в кадре и синхрослово на выходе: ПСП сбрасывается у каждого слова — одним слоем не снять.
        with self.assertRaisesRegex(ValueError, "одном слове"):
            ldpc_okno.постобработка({**п, "синхро": {**п["синхро"], "длина": 32 + 2 * L}})
        self.assertEqual(f"аддитивный отводы 14,15 кадр 512 начальное {ldpc_okno.dvb_первые()}",
                         ldpc_okno.постобработка({**п, "синхро": {**п["синхро"], "длина": 32 + 2 * L, "выводить": False}})[0])

    def test_распределённое_инверсное_с_синхрословом(self):
        слова = self.слова()
        s, L = 32, len(слова[0])
        N = s * ((s + 3 * L) // s + 1)                    # кадр кратен длине слова
        поток = 1 - кадры_модема(слова, N, распределённое=True, сдвиг=5)
        ряд, находка = снять_вручную(поток, f"ldpc fldpc-k512-j2-p12 синхро {СЛОВО} длина {N} распределённое с синхрословом")
        блок = ряд.reshape(-1, s + 3 * 512)
        self.assertTrue(np.array_equal(блок[:, :s], np.tile(sinhro.слово(СЛОВО), (len(блок), 1))))
        self.assertTrue(np.array_equal(блок[:, s:].reshape(-1, 512), self.данные[:3 * len(блок)]))
        self.assertIn("поток инверсный", находка.подробно[0])
        self.assertIn("распределённое, бит через", находка.подробно[0])

    def test_мультипликативный_и_инверсия(self):
        п = {"код": "fldpc-k512-j2-p12", "мультипликативный": "18,23", "инверсия": True}
        self.assertEqual(["скремблер 18,23", "инверсия"], ldpc_okno.постобработка(п))
        поток = np.concatenate(self.слова())
        итог = ldpc_okno.просмотр(поток, п)
        # Эталон: самосинхронизирующийся дескремблер y[i] = x[i] ⊕ x[i−18] ⊕ x[i−23], затем инверсия;
        # первые 23 бита (регистр ещё не заполнен) слой «скремблер» не выдаёт.
        x = self.данные.reshape(-1)[:len(итог["биты"]) + 23].astype(np.uint8)
        y = x.copy()
        y[18:] ^= x[:-18]
        y[23:] ^= x[:-23]
        self.assertGreater(len(итог["биты"]), 4000)
        self.assertTrue(np.array_equal(итог["биты"], (1 - y)[23:]))

    def test_проверка_параметров(self):
        for п, слово in (({"код": "нет-такого"}, "каталог матриц LDPC не задан|«нет-такого» нет"), ({"код": "нет такого"}, "выберите код"), ({"код": "a b"}, "выберите код"),
                         ({"код": "fldpc-k512-j2-p12", "синхро": {"вид": "распределённое", "слово": СЛОВО, "длина": 1000}},
                          "не делится"),
                         ({"код": "fldpc-k512-j2-p12", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": 900,
                                                                "длина_слова": 16}}, "длина синхрослова 16"),
                         ({"код": "fldpc-k512-j2-p12", "мультипликативный": "abc"}, "отводы"),
                         ({"код": "fldpc-k512-j2-p12", "перемежение": "QPSK"}, "перемежение")):
            with self.subTest(п=п), self.assertRaisesRegex((ValueError, KeyError), слово):
                ldpc_okno.просмотр(np.zeros(20000, np.uint8), п) if "нет-такого" in п["код"] else \
                    (ldpc_okno.слой(п), ldpc_okno.постобработка(п))

    def test_автомат_типа_с_синхрословом(self):
        слова = self.слова()
        N = 32 + 2 * len(слова[0])
        поток = кадры_модема(слова, N, сдвиг=300)
        типы = ldpc_katalog.с_файлами([])
        # Datum 512: LDPC-Gen2 у 512 нет, J = 2 и P = 12 — только в типе «F-LDPC (патент)».
        нет = ldpc_okno.автомат(поток, {"тип": "Datum 512", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N}}, типы)
        self.assertIsNone(нет["найдено"])
        self.assertIn("вслепую по нагрузке кадров (слово до 2048 бит) — тоже нет", нет["почему"])
        есть = ldpc_okno.автомат(поток, {"тип": "F-LDPC TrellisWare (патент)", "блок": 512,
                                          "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N}}, типы)
        self.assertEqual("fldpc-k512-j2-p12", есть["найдено"]["код"])
        self.assertEqual(("", "", ""), (есть["найдено"]["выколоты"], есть["найдено"]["укорочены"], есть["найдено"]["перемежение"]))
        закрыт = ldpc_okno.автомат(поток, {"тип": "Comtech"}, типы)
        self.assertIn("закрыта", закрыт["почему"])


@есть_источники
class Вслепую(unittest.TestCase):
    """Тип без матрицы (Comtech), кадры с синхрословом: код ищется вслепую по нагрузке кадров, H кладётся
    в папку ldpc файлом alist «вслепую-n-k», слоем с этим именем данные снимаются бит в бит."""

    def setUp(self):
        self._было = ldpc.КАТАЛОГ
        self.папка = Path(tempfile.mkdtemp()) / "ldpc"
        ldpc.подготовить_каталог(self.папка)

    def tearDown(self):
        ldpc.КАТАЛОГ = self._было

    def test_comtech_без_файла_вслепую_в_кадрах(self):
        патент = ПатентFLDPC()
        rng = np.random.default_rng(33)
        данные = rng.integers(0, 2, (1100, 256)).astype(np.uint8)
        слова = [np.array(патент.кодировать(d.tolist(), 2, 16), np.uint8) for d in данные]
        N = 32 + 2 * 512 + 40                                   # два слова в кадре и 40 бит заполнения
        поток = кадры_модема(слова, N, сдвиг=123)
        # Ошибки линии (2·10⁻⁴) и инверсия всего потока: синхрослово находится инверсным.
        поток ^= (rng.random(len(поток)) < 2e-4).astype(np.uint8)
        поток = (1 - поток).astype(np.uint8)
        п = {"тип": "Comtech", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N}}
        итог = ldpc_okno.автомат(поток, п, ldpc_katalog.с_файлами([]), бюджет=120)
        н = итог["найдено"]
        self.assertIsNotNone(н, итог)
        self.assertTrue(н["вслепую"])
        self.assertEqual(0, н["начало"])
        self.assertTrue(90 < н["сошлось"] < 100, н["сошлось"])
        self.assertEqual(н["сошлось"], round(н["сошлось"], 1))
        self.assertNotEqual(н["сошлось"], round(н["сошлось"]))          # есть десятые
        self.assertEqual("вслепую-512-256", н["код"])
        self.assertIn("LDPC (512, 256)", н["что"])
        файл = self.папка / "вслепую-512-256.alist"
        self.assertTrue(файл.exists())
        м = ldpc.прочитать("вслепую-512-256")
        self.assertEqual((512, 256), (м.n, м.m))
        # Файл — полноценный alist (Маккей): номера по столбцам и по строкам (с 1, добиты нулями) — одна и та же H.
        ч = [int(x) for x in файл.read_text(encoding="utf-8").split("\n", 1)[1].split()]
        n_, m_, вс, вр = ч[:4]
        вес_ст, вес_стр = ч[4:4 + n_], ч[4 + n_:4 + n_ + m_]
        по_столбцам = np.array(ч[4 + n_ + m_:4 + n_ + m_ + n_ * вс]).reshape(n_, вс)
        по_строкам = np.array(ч[4 + n_ + m_ + n_ * вс:]).reshape(m_, вр)
        пары = {(i, c) for i, с in enumerate(м.строки) for c in с.tolist()}
        self.assertEqual(пары, {(r - 1, c) for c in range(n_) for r in по_столбцам[c] if r})
        self.assertEqual(пары, {(i, c - 1) for i in range(m_) for c in по_строкам[i] if c})
        self.assertEqual(вес_ст, [int((р > 0).sum()) for р in по_столбцам])
        self.assertEqual(вес_стр, [int((р > 0).sum()) for р in по_строкам])
        self.assertEqual((вс, вр), (max(вес_ст), max(вес_стр)))
        self.assertFalse((по_строкам[по_строкам > 0] > n_).any() or (по_столбцам[по_столбцам > 0] > m_).any())
        # Каждая проверка найденной H выполняется на всех словах (это проверки кода, а не случайные).
        for h in м.строки[:64]:
            self.assertFalse(any(int(с[h].sum()) % 2 for с in слова[:50]))
        # Слоем окна (как «Декодирование») — данные бит в бит.
        слой = ldpc_okno.слой({"код": "вслепую-512-256", "синхро": п["синхро"]})
        ряд, _ = снять_вручную(поток, слой)
        кадров = len(ряд) // 512
        np.testing.assert_array_equal(данные[:кадров * 2].reshape(-1)[:len(ряд)], ряд)
        # Файл отдела теперь в «Нестандарт» и находится обычным автоматом (по матрице), а не вслепую.
        типы = ldpc_katalog.с_файлами(ldpc.список())
        нест = next(т for т in типы if т["тип"] == "Нестандарт")
        self.assertIn("вслепую-512-256", [к for с in нест["скорости"] for к in с["коды"]])
        снова = ldpc_okno.автомат(поток, {**п, "тип": "Нестандарт"}, типы, бюджет=120)["найдено"]
        self.assertEqual("вслепую-512-256", снова["код"])
        self.assertNotIn("вслепую", снова)

    def test_бюджет_вслепую_остаток(self):
        """Вслепую получает остаток бюджета автомата (не меньше 10 с)."""
        from unittest import mock
        from reportgen.potok import dlinnye
        слова = [np.zeros(512, np.uint8)] * 8
        поток = кадры_модема(слова, 32 + 1024, сдвиг=0)
        п = {"тип": "Comtech", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": 32 + 1024}}
        with mock.patch.object(dlinnye, "найти_в_кадрах", return_value=None) as найти:
            итог = ldpc_okno.автомат(поток, п, ldpc_katalog.с_файлами([]), бюджет=100)
        бюджет = найти.call_args.kwargs["бюджет"]
        self.assertTrue(95 < бюджет <= 100, бюджет)
        self.assertIn("вслепую по нагрузке кадров", итог["почему"])
        with mock.patch.object(dlinnye, "найти_в_кадрах", return_value=None) as найти:
            ldpc_okno.автомат(поток, п, ldpc_katalog.с_файлами([]), бюджет=3)
        self.assertEqual(10.0, найти.call_args.kwargs["бюджет"])
        # Без кадров вслепую не ищется, и в ответе об этом ни слова.
        без = ldpc_okno.автомат(поток, {"тип": "Comtech"}, ldpc_katalog.с_файлами([]))
        self.assertNotIn("вслепую по нагрузке", без["почему"])


def малый_код(слов: int, сид: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Свой код (64, 32): H = [A | I], у A в каждом столбце три единицы; слова систематические, p = A·d."""
    rng = np.random.default_rng(сид)
    A = np.zeros((32, 32), np.uint8)
    for c in range(32):
        A[rng.choice(32, 3, replace=False), c] = 1
    данные = rng.integers(0, 2, (слов, 32)).astype(np.uint8)
    return данные, np.concatenate([данные, (данные.astype(int) @ A.T % 2).astype(np.uint8)], axis=1)


class ВслепуюВКадрах(unittest.TestCase):
    """dlinnye.найти_в_кадрах: длина слова — из нагрузки кадров, без перебора окна."""

    def test_хвост_нагрузки(self):
        from reportgen.potok import dlinnye
        rng = np.random.default_rng(1)
        н = rng.integers(0, 2, (20, 100)).astype(np.uint8)
        н[:, 60:] = 0
        self.assertEqual(40, dlinnye.хвост_нагрузки(н))
        н[:, 59] = 1                                            # постоянный последний бит слова
        self.assertEqual(41, dlinnye.хвост_нагрузки(н))
        self.assertEqual(0, dlinnye.хвост_нагрузки(н[:1]))       # один кадр — не видно
        self.assertEqual(0, dlinnye.хвост_нагрузки(rng.integers(0, 2, (2, 50)).astype(np.uint8)))
        self.assertEqual(50, dlinnye.хвост_нагрузки(np.ones((3, 50), np.uint8)))
        # Ошибки линии в заполнении: при 20 кадрах и больше столбец постоянен, если одно значение — в 95 %.
        н = rng.integers(0, 2, (40, 100)).astype(np.uint8)
        н[:, 60:] = 1
        н[3, 99] = н[17, 99] = 0                                 # 2 из 40 — 5 %
        self.assertEqual(40, dlinnye.хвост_нагрузки(н))
        н[5, 99] = 0                                             # 3 из 40 — 7,5 %: уже не заполнение
        self.assertEqual(0, dlinnye.хвост_нагрузки(н))
        мало = н[:19].copy()
        мало[:, 60:] = 1
        мало[0, 99] = 0                                          # 19 кадров — только строго одинаковые
        self.assertEqual(0, dlinnye.хвост_нагрузки(мало))

    def test_мин_сумма_с_проверками_веса_1(self):
        """Проверка веса 1 («бит = 0», постоянный бит слова) не ломает декодер: бит обнуляется."""
        from reportgen.potok import dlinnye
        H = np.zeros((2, 6), np.uint8)
        H[0, 2] = 1
        H[1, [0, 1, 3]] = 1
        W = np.array([[1, 1, 1, 0, 0, 0], [0, 0, 0, 0, 1, 1]], np.uint8)
        слова, чисто = dlinnye.мин_сумма(W, H)
        np.testing.assert_array_equal([[1, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 1]], слова)
        self.assertTrue(чисто.all())

    def test_длины_из_нагрузки(self):
        from reportgen.potok import dlinnye
        _, слова = малый_код(400)
        # Четыре слова по 64 в кадре, без хвоста: n = 64 (не 128 и не 256).
        н, H = dlinnye.найти_в_кадрах(слова.reshape(100, 256))
        self.assertEqual(64, н.свойства["n"])
        self.assertEqual(32, н.свойства["k"])
        self.assertFalse(any(int(с @ h) % 2 for с in слова[:50] for h in H))
        # Одно слово в кадре (m = 1), хвоста нет.
        н1, _ = dlinnye.найти_в_кадрах(слова.reshape(400, 64))
        self.assertEqual(64, н1.свойства["n"])
        # Хвост ровно в половину нагрузки — ещё не «почти вся постоянна».
        половина = np.concatenate([слова.reshape(400, 64), np.zeros((400, 64), np.uint8)], axis=1)
        self.assertEqual(64, dlinnye.найти_в_кадрах(половина)[0].свойства["n"])
        # Нагрузка вся постоянна — кода нет.
        self.assertIsNone(dlinnye.найти_в_кадрах(np.zeros((400, 256), np.uint8)))
        # Предел длины: n = до — ещё пробуется, n > до — нет.
        два = слова.reshape(200, 128)
        self.assertEqual(64, dlinnye.найти_в_кадрах(два, до=64)[0].свойства["n"])
        self.assertIsNone(dlinnye.найти_в_кадрах(два, до=63))


@есть_источники
class ЧерезСервер(unittest.TestCase):
    """API окна: типы, загрузка файла матрицы, просмотр и автомат по массиву задания."""

    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415 — общая заготовка веб-тестов

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.старый = ldpc.КАТАЛОГ
        self.addCleanup(setattr, ldpc, "КАТАЛОГ", self.старый)
        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client
        self.assertEqual(200, self.к.get("/api/potok-ldpc-types").status_code)   # задания и папка ldpc

    def задание(self, поток: np.ndarray) -> str:
        з = self.сеть.app.state.potok
        ид = з.создать(владелец=self.сеть.repos.users.by_login("engineer").id, имя="ldpc.bin",
                       данные=np.packbits(поток).tobytes(), разбирать=False, бит=len(поток))
        for _ in range(200):
            if з.прочитать(ид)["состояние"] == "готово":
                break
            time.sleep(0.05)
        return ид

    def test_папка_создаётся_при_старте_сервера(self):
        from fastapi.testclient import TestClient  # noqa: PLC0415

        from test_web import make_app  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as папка:
            приложение, *_ = make_app(Path(папка), with_library=False)
            self.assertFalse((Path(папка) / "ldpc").exists())
            with TestClient(приложение):
                self.assertTrue((Path(папка) / "ldpc" / "_ПРОЧТИ.txt").is_file())

    def test_типы_и_файлы(self):
        к = self.к
        типы = к.get("/api/potok-ldpc-types").json()
        self.assertTrue(Path(типы["folder"]).is_dir())
        self.assertEqual(str(self.сеть.tmp / "ldpc"), типы["folder"])
        self.assertEqual("Comtech", типы["types"][0]["тип"])
        self.assertEqual([], типы["files"])
        # Файл матрицы: битый — 400, чужое расширение — 400, годный — в папке и в «Нестандарт».
        плохой = к.post("/api/potok-ldpc-files", files={"file": ("x.alist", "ерунда".encode())})
        self.assertEqual(400, плохой.status_code)
        self.assertFalse((self.сеть.tmp / "ldpc" / "x.alist").exists())
        не_тот = к.post("/api/potok-ldpc-files", files={"file": ("x.exe", b"1 0")})
        self.assertEqual(400, не_тот.status_code)
        ответ = к.post("/api/potok-ldpc-files", files={"file": ("мой код.h", b"1101\n0111\n")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual({"имя": "мой_код", "вид": "H в тексте", "n": 4, "m": 2}, ответ.json()["file"])
        self.assertTrue((self.сеть.tmp / "ldpc" / "мой_код.h").is_file())
        self.assertEqual(409, к.post("/api/potok-ldpc-files", files={"file": ("мой код.h", b"1101\n0111\n")}).status_code)
        типы = к.get("/api/potok-ldpc-types").json()
        нестандарт = next(т for т in типы["types"] if т["тип"] == "Нестандарт")
        self.assertEqual(["мой_код"], [и for с in нестандарт["скорости"] for и in с["коды"]])
        self.assertEqual(["мой_код"], [ф["имя"] for ф in типы["files"]])
        # Файл «comtech…» — к каждой скорости типа Comtech.
        self.assertEqual(200, к.post("/api/potok-ldpc-files", files={"file": ("comtech_16k.h", b"1101\n0111\n")}).status_code)
        comtech = к.get("/api/potok-ldpc-types").json()["types"][0]
        self.assertEqual([["comtech_16k"]] * 3, [с["коды"] for с in comtech["скорости"]])

    def test_просмотр_и_автомат(self):
        патент = ПатентFLDPC()
        rng = np.random.default_rng(4)
        данные = rng.integers(0, 2, (40, 256)).astype(np.uint8)
        слова = [np.array(патент.кодировать(d.tolist(), 4, 16), np.uint8) for d in данные]
        N = 32 + 2 * 384 + 3
        поток = кадры_модема(слова, N, сдвиг=9)
        ид = self.задание(поток)
        п = {"тип": "Datum 256", "код": "fldpc-k256-j4-p16",
             "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N, "длина_слова": 32}}
        просмотр = self.к.post(f"/api/potok/{ид}/ldpc/preview", json={"stage": 0, "параметры": п})
        self.assertEqual(200, просмотр.status_code, просмотр.text)
        о = просмотр.json()
        self.assertEqual(100.0, о["сошлось"])
        self.assertEqual((384, 256, 384), (о["n"], о["k"], о["в_потоке"]))   # J = 4: 2K/J = 128 бит чётности
        self.assertEqual(["ldpc fldpc-k256-j4-p16 синхро " + "".join(map(str, sinhro.слово(СЛОВО).tolist()))
                          + f" длина {N}"], о["слои"])
        выход = np.unpackbits(np.frombuffer(base64.b64decode(о["биты"]), np.uint8))[:о["показано"]]
        np.testing.assert_array_equal(данные[:len(выход) // 256].reshape(-1), выход[:len(выход) // 256 * 256])
        авто = self.к.post(f"/api/potok/{ид}/ldpc/auto", json={"stage": 0, "параметры": {**п, "код": "", "скорость": "1/2"}})
        self.assertEqual(200, авто.status_code, авто.text)
        # У Datum 256 скорость 1/2 — J = 2; поток — J = 4 (2/3): в «1/2» не найдено, в «2/3» — найдено.
        self.assertIsNone(авто.json()["найдено"])
        авто = self.к.post(f"/api/potok/{ид}/ldpc/auto", json={"stage": 0, "параметры": {**п, "код": "", "скорость": "2/3"}})
        self.assertEqual("fldpc-k256-j4-p16", авто.json()["найдено"]["код"])
        self.assertEqual(100.0, авто.json()["найдено"]["сошлось"])
        плохо = self.к.post(f"/api/potok/{ид}/ldpc/preview", json={"stage": 0, "параметры": {"код": "?"}})
        self.assertEqual(400, плохо.status_code)
        self.assertEqual(400, self.к.post(f"/api/potok/{ид}/ldpc/preview", json={"stage": 0, "параметры": "x"}).status_code)
        self.assertEqual(400, self.к.post(f"/api/potok/{ид}/ldpc/auto", json={"stage": "а", "параметры": п}).status_code)
        self.assertEqual(404, self.к.post("/api/potok/20200101-000000-abcdef/ldpc/preview",
                                          json={"stage": 0, "параметры": п}).status_code)


# -- окно в браузере: функции app.js в node --------------------------------------------------

@unittest.skipUnless(shutil.which("node"), "нужен node")
class ОкноВБраузере(unittest.TestCase):
    """Проверка полей окна до запроса, скорости типа с блоками, поиск кода в каталоге — функции app.js в node."""

    ФУНКЦИИ = ["битСинхрослова", "ошибкаОкнаLDPC", "скоростиТипаLDPC", "гдеКодLDPC"]

    def выполнить(self, случаи: list[dict]) -> list:
        from test_potok_sessii import функции_js  # noqa: PLC0415

        код = функции_js(self.ФУНКЦИИ, []) + """
const случаи = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const итог = случаи.map((с) => {
    switch (с.что) {
    case 'бит': return битСинхрослова(с.т);
    case 'ошибка': return ошибкаОкнаLDPC(с.п, с.закрыт);
    case 'скорости': return скоростиТипаLDPC(с.т, с.блок).map((x) => x.подпись);
    case 'где': { const г = гдеКодLDPC(с.типы, с.текущий, с.код); return г ? [г.т.тип, г.с.подпись] : null; }
    default: return null;
    }
});
process.stdout.write(JSON.stringify(итог));
"""
        готово = subprocess.run(["node", "-e", код], input=json.dumps(случаи), capture_output=True, text=True, timeout=60)
        self.assertEqual(0, готово.returncode, готово.stderr)
        return json.loads(готово.stdout)

    def test_длина_синхрослова_как_на_сервере(self):
        тексты = ["0x1ACFFC1D", "1ACFFC1Dh", "0b0110", "0110 1011", "1_0_1_1", "", None, "0xZZ", "12", "0x", "01", "0x0990"]
        итоги = self.выполнить([{"что": "бит", "т": т} for т in тексты])
        for т, итог in zip(тексты, итоги, strict=True):
            try:
                ожидание = len(sinhro.слово(str(т or "")))
            except ValueError:
                ожидание = None
            if ожидание is not None:
                self.assertEqual(ожидание, итог, т)
        self.assertEqual([32, 32, 4, 8, 4, 0, 0, 0, 8, 0, 2, 16], итоги)

    def test_ошибки_окна_как_у_сервера(self):
        годное = {"тип": "Datum 256", "код": "fldpc-k256-j4-p16",
                  "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": 803, "длина_слова": 32}}
        случаи = [
            (годное, False, ""),
            ({**годное, "код": ""}, True, "матрица типа «Datum 256» закрыта: положите файл отдела в папку ldpc («Загрузить…»)"),
            ({**годное, "код": ""}, False, "нет кода для этой скорости"),
            ({**годное, "синхро": {**годное["синхро"], "слово": "101"}}, False, "синхрослово — биты или HEX, не короче 4 бит"),
            ({**годное, "синхро": {**годное["синхро"], "слово": "1011"}}, False, "длина синхрослова 32, а в поле 4 бит"),
            ({**годное, "синхро": {**годное["синхро"], "длина": 32}}, False, "длина кадра — больше синхрослова"),
            ({**годное, "синхро": {**годное["синхро"], "длина": 33}}, False, ""),
            ({**годное, "синхро": {**годное["синхро"], "вид": "распределённое", "длина": 800}}, False, ""),
            ({**годное, "синхро": {**годное["синхро"], "вид": "распределённое", "длина": 801}}, False,
             "распределённое: длина кадра не делится на длину синхрослова"),
            ({**годное, "синхро": {"вид": "нет", "слово": "", "длина": 0}}, False, ""),
            ({**годное, "мультипликативный": "18,23"}, False, ""),
            ({**годное, "мультипликативный": ""}, False, ""),
            ({**годное, "мультипликативный": "а,б"}, False, "отводы мультипликативного дескремблера — «18,23»"),
            ({**годное, "синхро": {**годное["синхро"], "длина_слова": 0}}, False, "")]
        итоги = self.выполнить([{"что": "ошибка", "п": п, "закрыт": з} for п, з, _ in случаи])
        self.assertEqual([о for _, _, о in случаи], итоги)
        # Что окно пропускает, сервер собирает в слой (и наоборот — отказ сервера окно ловит заранее).
        for (п, _, о), итог in zip(случаи, итоги, strict=True):
            if итог == "" and п.get("код"):
                ldpc_okno.слой(п)
            elif п.get("код") and "мультипликатив" not in о:
                with self.assertRaises(ValueError):
                    ldpc_okno.слой(п)

    def test_скорости_типа_и_поиск_кода(self):
        типы = ldpc_katalog.типы()
        flex = next(т for т in типы if т["ключ"] == "fldpc")
        по_блоку = self.выполнить([{"что": "скорости", "т": flex, "блок": K} for K in (256, "1024", 99)])
        for K, подписи in zip((256, 1024, 99), по_блоку, strict=True):
            self.assertEqual([с["подпись"] for с in flex["скорости"] if с["блок"] == K], подписи)
        self.assertTrue(по_блоку[0] and по_блоку[1] and not по_блоку[2])
        datum = next(т for т in типы if т["тип"] == "Datum 1K")
        [все] = self.выполнить([{"что": "скорости", "т": datum, "блок": 256}])
        self.assertEqual([с["подпись"] for с in datum["скорости"]], все)       # без блоков — все скорости
        self.assertEqual([[]], self.выполнить([{"что": "скорости", "т": None, "блок": 1}]))
        где = self.выполнить([
            {"что": "где", "типы": типы, "текущий": "F-LDPC TrellisWare (патент)", "код": "fldpc-k1024-j6-p16"},
            {"что": "где", "типы": типы, "текущий": "Comtech", "код": "fldpc-k1024-j6-p16"},
            {"что": "где", "типы": типы, "текущий": "Comtech", "код": "нет-такого"}])
        self.assertEqual("F-LDPC TrellisWare (патент)", где[0][0])
        self.assertEqual(["Datum 1K", "3/4"], где[1])
        self.assertIsNone(где[2])


if __name__ == "__main__":
    unittest.main()
