"""Окно «LDPC» стола и папка матриц отдела: типы кода, файлы матриц, кадры модема, постобработка, API.

Эталоны независимые: слова F-LDPC — кодер патента из test_potok_ldpc_semeystva (по тексту патента),
ПСП DVB — по описанию EN 302 307-1 5.2.2 (регистр 15 ячеек, загрузка 100101010000000, выход x14 ⊕ x15),
кадры модема и распределённое синхрослово — собраны здесь же по описанию окна.
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import ldpc, ldpc_katalog, ldpc_okno, ldpc_std, sinhro
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
        self.assertEqual(["1/2", "2/3", "3/4", "14/17", "7/8", "10/11", "16/17"], по["Datum 16K"])
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
        self.assertEqual(f"аддитивный отводы 14,15 кадр 1024 начальное {ldpc_okno.dvb_первые()}", итог["слои"][1])
        self.assertTrue(np.array_equal(итог["биты"].reshape(-1, 512), self.данные[:len(итог["биты"]) // 512]))
        self.assertIn("хвост 13 бит (заполнение) отброшен", " ".join(итог["подробно"]))

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
        # Эталон: самосинхронизирующийся дескремблер y[i] = x[i] ⊕ x[i−18] ⊕ x[i−23], затем инверсия.
        x = self.данные.reshape(-1)[:len(итог["биты"])].astype(np.uint8)
        y = x.copy()
        y[18:] ^= x[:-18]
        y[23:] ^= x[:-23]
        self.assertTrue(np.array_equal(итог["биты"][23:], (1 - y)[23:]))

    def test_проверка_параметров(self):
        for п, слово in (({"код": "нет такого"}, "встроенного кода"), ({"код": "a b"}, "выберите код"),
                         ({"код": "fldpc-k512-j2-p12", "синхро": {"вид": "распределённое", "слово": СЛОВО, "длина": 1000}},
                          "не делится"),
                         ({"код": "fldpc-k512-j2-p12", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": 900,
                                                                "длина_слова": 16}}, "длина синхрослова 16"),
                         ({"код": "fldpc-k512-j2-p12", "мультипликативный": "abc"}, "отводы"),
                         ({"код": "fldpc-k512-j2-p12", "перемежение": "QPSK"}, "перемежение")):
            with self.subTest(п=п), self.assertRaisesRegex((ValueError, KeyError), слово):
                ldpc_okno.просмотр(np.zeros(20000, np.uint8), п) if "нет такого" in п["код"] else \
                    (ldpc_okno.слой(п), ldpc_okno.постобработка(п))

    def test_автомат_типа_с_синхрословом(self):
        слова = self.слова()
        N = 32 + 2 * len(слова[0])
        поток = кадры_модема(слова, N, сдвиг=300)
        типы = ldpc_katalog.с_файлами([])
        # Datum 512: LDPC-Gen2 у 512 нет, J = 2 и P = 12 — только в типе «F-LDPC (патент)».
        нет = ldpc_okno.автомат(поток, {"тип": "Datum 512", "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N}}, типы)
        self.assertIsNone(нет["найдено"])
        есть = ldpc_okno.автомат(поток, {"тип": "F-LDPC TrellisWare (патент)", "блок": 512,
                                          "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N}}, типы)
        self.assertEqual("fldpc-k512-j2-p12", есть["найдено"]["код"])
        self.assertEqual(("", "", ""), (есть["найдено"]["выколоты"], есть["найдено"]["укорочены"], есть["найдено"]["перемежение"]))
        закрыт = ldpc_okno.автомат(поток, {"тип": "Comtech"}, типы)
        self.assertIn("закрыта", закрыт["почему"])


@есть_источники
class ЧерезСервер(unittest.TestCase):
    """API окна: типы, загрузка файла матрицы, просмотр и автомат по массиву задания."""

    @classmethod
    def setUpClass(cls):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(self):
                pass

        cls.сеть = Сеть()
        cls.сеть.setUp()

    @classmethod
    def tearDownClass(cls):
        cls.сеть.tearDown()

    def test_окно_целиком(self):
        self.сеть.login("engineer")
        к = self.сеть.client
        типы = к.get("/api/potok-ldpc-types").json()
        self.assertTrue(Path(типы["folder"]).is_dir())
        self.assertEqual("Comtech", типы["types"][0]["тип"])
        # Файл матрицы: битый — 400, годный — в папке и в «Нестандарт».
        плохой = к.post("/api/potok-ldpc-files", files={"file": ("x.alist", b"ерунда")})
        self.assertEqual(400, плохой.status_code)
        не_тот = к.post("/api/potok-ldpc-files", files={"file": ("x.exe", b"1 0")})
        self.assertEqual(400, не_тот.status_code)
        ответ = к.post("/api/potok-ldpc-files", files={"file": ("мой код.h", b"1101\n0111\n")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        self.assertEqual("мой_код", ответ.json()["file"]["имя"])
        self.assertEqual(409, к.post("/api/potok-ldpc-files", files={"file": ("мой код.h", b"1101\n0111\n")}).status_code)
        нестандарт = next(т for т in к.get("/api/potok-ldpc-types").json()["types"] if т["тип"] == "Нестандарт")
        self.assertEqual(["мой_код"], [и for с in нестандарт["скорости"] for и in с["коды"]])
        # Массив с кадрами модема: просмотр и автомат.
        патент = ПатентFLDPC()
        rng = np.random.default_rng(4)
        данные = rng.integers(0, 2, (20, 256)).astype(np.uint8)
        слова = [np.array(патент.кодировать(d.tolist(), 4, 16), np.uint8) for d in данные]
        N = 32 + 2 * 384 + 3
        поток = кадры_модема(слова, N, сдвиг=9)
        job = self.сеть.задание_из_бит(поток) if hasattr(self.сеть, "задание_из_бит") else None
        if job is None:
            ответ = к.post("/api/potok", files={"file": ("ldpc.bin", np.packbits(поток).tobytes())},
                           data={"профиль": "быстро", "разбирать": "0"})
            self.assertIn(ответ.status_code, (200, 201), ответ.text)
            job = ответ.json().get("id") or ответ.json().get("job", {}).get("id")
        п = {"тип": "Datum 256", "код": "fldpc-k256-j4-p16",
             "синхро": {"вид": "сосредоточенное", "слово": СЛОВО, "длина": N, "длина_слова": 32}}
        просмотр = к.post(f"/api/potok/{job}/ldpc/preview", json={"stage": 0, "параметры": п})
        self.assertEqual(200, просмотр.status_code, просмотр.text)
        self.assertEqual(100.0, просмотр.json()["сошлось"])
        авто = к.post(f"/api/potok/{job}/ldpc/auto", json={"stage": 0, "параметры": {**п, "код": "", "скорость": ""}})
        self.assertEqual("fldpc-k256-j4-p16", авто.json()["найдено"]["код"])
        плохо = к.post(f"/api/potok/{job}/ldpc/preview", json={"stage": 0, "параметры": {"код": "?"}})
        self.assertEqual(400, плохо.status_code)


if __name__ == "__main__":
    unittest.main()
