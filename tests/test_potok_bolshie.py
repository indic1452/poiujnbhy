"""Большие потоки: массивы на диске окнами, файлы сервера по ссылке, шаги по кускам,
загрузка кусками с докачкой, автоанализ по началу большого файла, ускорения разбора.

Быстрые тесты — на малых массивах с маленьким куском и пределом памяти: потоковый итог
обязан совпасть бит в бит с итогом в памяти. Медленный (файл в гигабайт) — только при
REPORTGEN_MEDLENNYE=1.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import chtenie, hdlc, kod, potokovo, rastr, razbor, sinhro, statistika
from reportgen.potok.bity import в_байты, в_биты
from reportgen.potok.hranenie import Источник, Окно, Приёмник, ФайлИзменён, отпечаток, сверить
from reportgen.potok.zagruzki import Загрузки, ОшибкаЗагрузки

МЕДЛЕННЫЕ = os.environ.get("REPORTGEN_MEDLENNYE") == "1"


def записать_биты(путь: Path, биты: np.ndarray) -> None:
    with Приёмник(путь) as п:
        п.записать(биты)
        п.закрыть()


def ряд_с_кадрами(сид: int = 1) -> np.ndarray:
    """Кадры по 1504 бит с синхрословом 00011011, мусор в начале, ошибка и проскальзывание."""
    rng = np.random.default_rng(сид)
    слово = np.array([0, 0, 0, 1, 1, 0, 1, 1], np.uint8)
    кадры = [np.concatenate([слово, rng.integers(0, 2, 1496).astype(np.uint8)]) for _ in range(60)]
    ряд = np.concatenate([rng.integers(0, 2, 333).astype(np.uint8)] + кадры)
    ряд[5000] ^= 1
    return np.concatenate([ряд[:9000], ряд[9003:]])


СЛОИ = ["инверсия", "сдвиг 13", "nrzi", "накопление", "скремблер 3,20", "обрезка начало 100 конец 37",
        "усечение от 1000 длина 50000", "усечение от 77", "усечение от 10 до 20000", "реверс 5",
        "прореживание 3 фаза 1", "выбросить 7 фаза 2", "вставить 9 фаза 4 бит 1", "вставить 2", "xor 0x5A3",
        "метки 3: 0 1 3 2 7 6 4 5", "биты символа 4: 1 0 3 2", "фм 3 поворот 2", "фм 2 отражение натуральный",
        "синхро 00011011", "кадры 00011011 длина 1504 ошибок 1",
        "аддитивный отводы 14,15 начальное 100101010000000",
        "аддитивный отводы 14,15 кадр 1000 начальное 100101010000000 пропуск 8 начало 3"]
МАСКА = {"вид": "маска", "вкл": True, "маска": {"период": 32, "сдвиг": 5, "позиции": [0, 1, 7, 30]}}


class ПотоковыеШагиTests(unittest.TestCase):
    """Каждый потоковый шаг по кускам любой длины = тот же шаг над всем рядом в памяти."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.папка = Path(cls._tmp.name)
        cls.ряд = ряд_с_кадрами()
        записать_биты(cls.папка / "в.bin", cls.ряд)
        cls.ист = Источник(cls.папка / "в.bin", бит=len(cls.ряд))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def сверить(self, шаги, куски=(7, 64, 1000, 8192, 1 << 20)):
        шаги = rastr.проверить_шаги(шаги)
        эталон, описание = rastr.применить(self.ряд.copy(), шаги)
        for кусок in куски:
            with self.subTest(шаги=[ш.get("слой") or ш["вид"] for ш in шаги], кусок=кусок):
                бит, о = potokovo.выполнить(self.ист, шаги, self.папка / "вых.bin", кусок_бит=кусок,
                                            в_памяти_до=10, окно_синхро=1 << 30)
                вых = Источник(self.папка / "вых.bin", бит=бит).все()
                self.assertEqual(len(эталон), бит)
                self.assertTrue(np.array_equal(эталон, вых))
                self.assertEqual(описание, о)

    def test_каждый_слой(self):
        for слой in СЛОИ:
            self.сверить([{"вид": "слой", "слой": слой}])

    def test_маска_разметка_и_цепочка(self):
        разметка = rastr.проверить_разметку({"отрезки": [[10, 50], [70000, 300]],
                                             "правила": [{"период": 64, "сдвиг": 3, "ширина": 5, "от": 1000,
                                                          "до": 60000}, {"период": 7, "сдвиг": 1, "ширина": 2,
                                                                         "от": 80000, "до": 0}]})
        for действие in ("взять", "убрать", "инверсия"):
            self.сверить([{"вид": "разметка", "действие": действие, "разметка": разметка}], куски=(7, 4096))
        self.сверить([МАСКА])
        self.сверить([{"вид": "слой", "слой": "скремблер 3,20"}, {"вид": "слой", "слой": "инверсия", "вкл": False},
                      {"вид": "слой", "слой": "обрезка начало 5 конец 5"}, МАСКА, {"вид": "слой", "слой": "nrzi"}])

    def test_ошибки_как_в_памяти(self):
        for слой, текст in (("обрезка начало 90000 конец 900", "не меньше всего потока"),
                            ("усечение от 10 длина 99999999", "усечение"), ("синхро 1111111111111111111111", "не найдена")):
            шаги = rastr.проверить_шаги([{"вид": "слой", "слой": слой}])
            with self.subTest(слой), self.assertRaisesRegex(ValueError, текст):
                potokovo.выполнить(self.ист, шаги, self.папка / "о.bin", кусок_бит=1000, в_памяти_до=10)
            self.assertFalse((self.папка / "о.bin").exists())          # недописанный итог не остаётся
            self.assertFalse((self.папка / "о.bin.part").exists())

    def test_синхро_ищется_в_окне_начала(self):
        """На большом ряду начало ищется в первом окне — то же место, что и в памяти."""
        шаги = rastr.проверить_шаги([{"вид": "слой", "слой": "синхро 00011011"}])
        эталон, _ = rastr.применить(self.ряд.copy(), шаги)
        бит, _ = potokovo.выполнить(self.ист, шаги, self.папка / "с.bin", кусок_бит=1000, в_памяти_до=10,
                                    окно_синхро=20000)
        self.assertTrue(np.array_equal(эталон, Источник(self.папка / "с.bin", бит=бит).все()))

    def test_слой_целиком_и_кусками(self):
        """Слою кода нужен весь ряд: влезает — целиком; нет — кусками с пометкой; переворот — отказ."""
        поток = с.перемежить_блочно(с.хэмминг74(с.случайные_биты(40_000, сид=3)), 10, 7)[11:]
        записать_биты(self.папка / "к.bin", поток)
        ист = Источник(self.папка / "к.bin", бит=len(поток))
        шаги = rastr.проверить_шаги([{"вид": "слой", "слой": "инверсия"}, {"вид": "слой", "слой": "перемежение 10 7"}])
        эталон, описание = rastr.применить(поток.copy(), шаги)
        бит, о = potokovo.выполнить(ист, шаги, self.папка / "к1.bin", кусок_бит=1000, в_памяти_до=len(поток))
        self.assertTrue(np.array_equal(эталон, Источник(self.папка / "к1.bin", бит=бит).все()))
        бит, о = potokovo.выполнить(ист, шаги, self.папка / "к2.bin", кусок_бит=1000, в_памяти_до=len(поток) // 4)
        self.assertIn("кусками", о[-1])
        self.assertGreater(бит, 0)
        for слой in ("переворот", "свёрточный 171/133 k=7", "плоскость 4"):
            with self.subTest(слой), self.assertRaisesRegex(ValueError, "нужен весь ряд"):
                potokovo.выполнить(ист, rastr.проверить_шаги([{"вид": "слой", "слой": слой}]),
                                   self.папка / "к3.bin", в_памяти_до=1000)

    def test_ход_и_отмена(self):
        доли = []
        шаги = rastr.проверить_шаги([{"вид": "слой", "слой": "инверсия"}])
        potokovo.выполнить(self.ист, шаги, self.папка / "х.bin", кусок_бит=8000, в_памяти_до=10,
                           ход=lambda д, т: доли.append(д))
        self.assertEqual(1.0, доли[-1])
        self.assertTrue(all(a <= b for a, b in zip(доли, доли[1:], strict=False)))
        счёт = iter(range(100))
        with self.assertRaises(potokovo.Отменено):
            potokovo.выполнить(self.ист, шаги, self.папка / "х2.bin", кусок_бит=8000, в_памяти_до=10,
                               отмена=lambda: next(счёт) >= 3)
        self.assertFalse((self.папка / "х2.bin").exists())

    def test_маска_окна_как_маска_разметки(self):
        разметка = rastr.проверить_разметку({"отрезки": [[3, 40], [500, 1]],
                                             "правила": [{"период": 13, "сдвиг": 12, "ширина": 3, "от": 17, "до": 900}]})
        вся = rastr.маска_разметки(1000, разметка)
        for от, до in ((0, 1000), (5, 6), (16, 18), (450, 999), (899, 901)):
            self.assertTrue(np.array_equal(вся[от:до], potokovo.маска_окна(от, до, разметка)[:len(вся[от:до])]))


class ИсточникTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.папка = Path(self._tmp.name)
        self.данные = np.random.default_rng(4).bytes(100_000)
        (self.папка / "ф.bin").write_bytes(self.данные)

    def test_окна_часть_и_разворот(self):
        ист = Источник(self.папка / "ф.bin", смещение=1000, байт=5000, бит=39_997)
        биты = в_биты(self.данные[1000:6000])[:39_997]
        self.assertEqual(39_997, len(ист))
        for от, до in ((0, 8), (3, 77), (39_000, 40_000), (12345, 23456), (5, 5)):
            self.assertTrue(np.array_equal(биты[от:до], ист.биты(от, до)))
        self.assertTrue(np.array_equal(биты, np.concatenate(list(ист.куски(999)))))
        окно = Окно(ист)
        self.assertTrue(np.array_equal(биты[10:30], окно[10:30]))
        self.assertEqual(int(биты[-1]), int(окно[-1]))
        развёрнутый = Источник(self.папка / "ф.bin", развернуть=True)
        self.assertEqual(rastr.развернуть_биты(self.данные[:100]), развёрнутый.байты(0, 100))
        self.assertTrue(np.array_equal(в_биты(self.данные[50:60], "младший"), развёрнутый.биты(400, 480)))

    def test_приёмник_хвост_и_отмена(self):
        биты = np.random.default_rng(1).integers(0, 2, 1001).astype(np.uint8)
        with Приёмник(self.папка / "п.bin") as п:
            for кусок in np.array_split(биты, 13):
                п.записать(кусок)
            self.assertEqual(1001, п.закрыть())
        self.assertTrue(np.array_equal(биты, Источник(self.папка / "п.bin", бит=1001).все()))
        with self.assertRaises(RuntimeError), Приёмник(self.папка / "п2.bin") as п:
            п.записать(биты)
            raise RuntimeError
        self.assertEqual([], [x.name for x in self.папка.iterdir() if x.name.startswith("п2")])

    def test_ссылка_тот_же_изменён_удалён(self):
        путь = self.папка / "ф.bin"
        ссылка = отпечаток(путь)
        self.assertIsNone(сверить(ссылка))
        os.utime(путь, ns=(ссылка["изменён_нс"] + 10**9, ссылка["изменён_нс"] + 10**9))
        новая = сверить(ссылка)                                  # время другое, содержимое то же
        self.assertEqual(ссылка["изменён_нс"] + 10**9, новая["изменён_нс"])
        запомнено = []
        ист = Источник(путь, ссылка=ссылка, сверено=запомнено.append)
        self.assertEqual(1, len(запомнено))
        путь.write_bytes(self.данные[:-1] + b"\x00" if self.данные[-1] else self.данные[:-1] + b"\x01")
        with self.assertRaisesRegex(ФайлИзменён, "переписан"):
            ист.байты(0, 10)
        путь.write_bytes(self.данные + b"x")
        with self.assertRaisesRegex(ФайлИзменён, "изменён"):
            ист.биты(0, 8)
        путь.unlink()
        with self.assertRaisesRegex(ФайлИзменён, "удалён"):
            ист.байты(0, 1)

    def test_умолчания_и_ключ(self):
        ист = Источник(self.папка / "ф.bin")
        self.assertEqual(self.данные, ист.байты())
        self.assertTrue(np.array_equal(в_биты(self.данные), ист.биты()))
        self.assertTrue(np.array_equal(в_биты(self.данные), np.concatenate(list(ист.куски()))))
        self.assertEqual(self.данные, b"".join(ист.байты_кусками(7777)))
        self.assertEqual((str(self.папка / "ф.bin"), 0, 800_000, False), (ист.ключ[0], ист.ключ[3], ист.ключ[4], ист.ключ[5]))
        по_ссылке = Источник(self.папка / "ф.bin", ссылка=отпечаток(self.папка / "ф.bin"), смещение=8)
        self.assertEqual(отпечаток(self.папка / "ф.bin")["хэш"], по_ссылке.ключ[1])
        self.assertEqual((8, 799_936), по_ссылке.ключ[2:4])

    def test_отпечаток_видит_хвост_большого_файла(self):
        """Файл больше мегабайта: переписанный конец (тот же объём) — не тот же файл; середина не в отпечатке."""
        путь = self.папка / "большой.bin"
        данные = bytearray(np.random.default_rng(9).bytes(3 << 20))
        путь.write_bytes(bytes(данные))
        ссылка = отпечаток(путь)
        данные[(3 << 20) // 2] ^= 1                         # середина — вне отпечатка, время то же
        путь.write_bytes(bytes(данные))
        os.utime(путь, ns=(ссылка["изменён_нс"] + 1, ссылка["изменён_нс"] + 1))
        self.assertIsNotNone(сверить(ссылка))
        данные[-1] ^= 1
        путь.write_bytes(bytes(данные))
        with self.assertRaisesRegex(ФайлИзменён, "переписан"):
            сверить(ссылка)
        данные[-1] ^= 1
        данные[5] ^= 1
        путь.write_bytes(bytes(данные))
        with self.assertRaisesRegex(ФайлИзменён, "переписан"):
            сверить(ссылка)

    def test_границы_окон(self):
        """Окна за краями урезаются, как срезы массива: не ошибка и не чужие байты."""
        д, путь = self.данные, self.папка / "ф.bin"
        ист = Источник(путь)
        биты = в_биты(д)
        self.assertEqual(д[:10], ист.байты(-5, 10))
        self.assertEqual(д[-3:], ист.байты(len(д) - 3, 100))
        self.assertEqual(b"", ист.байты(len(д) + 5, 10))
        self.assertEqual(b"", ист.байты(10, -1))
        self.assertEqual(д[7:], ист.байты(7))
        self.assertTrue(np.array_equal(биты[:5], ист.биты(-3, 5)))
        self.assertTrue(np.array_equal(биты[-4:], ист.биты(len(биты) - 4, len(биты) + 100)))
        self.assertEqual(0, len(ист.биты(50, 40)))
        self.assertEqual((0, 0, 0), (len(ист.биты(0, 0)), len(ист.биты(0, -5)), len(ист.биты(5, 0))))
        self.assertEqual(0, len(ист.биты(len(биты) + 9, len(биты) + 20)))
        self.assertTrue(np.array_equal(биты[100:], ист.биты(100)))
        self.assertTrue(np.array_equal(биты[3:1003], np.concatenate(list(ист.куски(77, 3, 1003)))))
        self.assertTrue(np.array_equal(биты[-20:], np.concatenate(list(ист.куски(7, len(биты) - 20, len(биты) + 50)))))
        self.assertEqual([1] * 10, [len(к) for к in ист.куски(0, 0, 10)])        # кусок не меньше бита
        self.assertEqual([], list(ист.куски(8, -10, 0)))
        за_концом = Источник(путь, смещение=len(д) + 10)
        self.assertEqual((len(д), 0, 0), (за_концом.смещение, за_концом.байт, за_концом.бит))
        self.assertEqual((0, len(д), len(д) * 8), (Источник(путь, смещение=-4).смещение, Источник(путь, смещение=-4).байт,
                                                    Источник(путь, смещение=-4).бит))
        часть = Источник(путь, смещение=10, байт=10**9, бит=10**12)
        self.assertEqual((len(д) - 10, (len(д) - 10) * 8), (часть.байт, часть.бит))
        self.assertEqual((0, 0), (Источник(путь, байт=-3).байт, Источник(путь, бит=-3).бит))
        self.assertEqual(13, Источник(путь, байт=2, бит=13).бит)
        окно = Окно(Источник(путь, бит=12))
        self.assertEqual(int(биты[11]), int(окно[-1]))
        self.assertEqual(int(биты[0]), int(окно[-12]))
        self.assertEqual((int(биты[0]), int(биты[5])), (int(окно[0]), int(окно[5])))
        for плохой in (12, -13):
            with self.assertRaises(IndexError):
                окно[плохой]
        with self.assertRaises(ValueError):
            окно[0:10:2]
        self.assertTrue(np.array_equal(биты[2:12], окно[2:100]))
        self.assertTrue(np.array_equal(биты[:12], окно[0:12:1]))
        self.assertTrue(Источник(путь).целый_файл())
        for не_целый in (Источник(путь, смещение=1), Источник(путь, байт=len(д) - 1), Источник(путь, развернуть=True),
                         Источник(путь, ссылка=отпечаток(путь))):
            self.assertFalse(не_целый.целый_файл())

    def test_целый_файл_связывается(self):
        ист = Источник(self.папка / "ф.bin")
        ист.в_файл(self.папка / "копия.bin")
        self.assertEqual(self.данные, (self.папка / "копия.bin").read_bytes())
        Источник(self.папка / "ф.bin", смещение=10, байт=20).в_файл(self.папка / "часть.bin")
        self.assertEqual(self.данные[10:30], (self.папка / "часть.bin").read_bytes())


class ЧтениеБольшихTests(unittest.TestCase):
    def test_sig_с_диска_как_в_памяти(self):
        with tempfile.TemporaryDirectory() as tmp:
            for порядок in ("big", "little"):
                for включает in (False, True):
                    данные = с.sig(с.пакеты_ip(3000), порядок=порядок, включает=включает)
                    путь = Path(tmp) / "a.sig"
                    путь.write_bytes(данные)
                    в_памяти = chtenie.прочитать(данные=данные, имя="a.sig")
                    with mock.patch.object(chtenie, "ГОЛОВА_SIG", 140_000), mock.patch.object(chtenie, "КУСОК_SIG", 1000):
                        начала, длины, формат, _ = chtenie.разобрать_sig_файл(путь)
                    self.assertEqual(в_памяти.формат, формат)
                    chtenie.тела_в_файл(путь, начала, длины, Path(tmp) / "т.bin", 700, 9000)
                    self.assertEqual(в_памяти.данные[700:9700], (Path(tmp) / "т.bin").read_bytes())
            путь.write_bytes(np.random.default_rng(1).bytes(300_000))
            self.assertIsNone(chtenie.разобрать_sig_файл(путь))

    def test_sig_с_диска_края(self):
        """Заголовок файла, пустой последний пакет, неоднозначные длины, обрыв, малый файл — как в памяти."""
        rng = np.random.default_rng(3)
        тела = [rng.bytes(int(n)) for n in rng.integers(20, 400, 900)]
        варианты = {
            "заголовок": rng.bytes(10) + с.sig(тела, порядок="big"),
            "пустой_последний": с.sig(тела + [b""], порядок="little"),
            "включает": с.sig(тела, порядок="big", включает=True),
            "неоднозначно": с.sig([rng.bytes(257) for _ in range(800)], порядок="big"),
            "обрыв": с.sig(тела, порядок="big")[:-7],
        }
        with tempfile.TemporaryDirectory() as tmp:
            for имя, данные in варианты.items():
                путь = Path(tmp) / f"{имя}.sig"
                путь.write_bytes(данные)
                в_памяти = chtenie.разобрать_sig(данные)
                # Голова не меньше пробы (64 КБ) с самым длинным пакетом — иначе проба по ней не та же.
                for голова, кусок in ((140_000, 1000), (1 << 20, 1 << 20), (140_000, 7)):
                    with self.subTest(имя, голова=голова, кусок=кусок), \
                            mock.patch.object(chtenie, "ГОЛОВА_SIG", голова), mock.patch.object(chtenie, "КУСОК_SIG", кусок):
                        с_диска = chtenie.разобрать_sig_файл(путь)
                        if в_памяти is None:
                            self.assertIsNone(с_диска)
                            continue
                        пакеты, формат, заметки = в_памяти
                        self.assertEqual((формат, заметки), (с_диска[2], с_диска[3]))
                        self.assertEqual([н for н, _ in пакеты], с_диска[0].tolist())
                        self.assertEqual([д for _, д in пакеты], с_диска[1].tolist())
            self.assertIsNone(chtenie.разобрать_sig(варианты["обрыв"]))
            self.assertIn("неоднозначна", " ".join(chtenie.разобрать_sig(варианты["неоднозначно"])[2]))

    def test_тела_в_файл_части(self):
        тела = [bytes([i]) * (10 + i) for i in range(30)]
        данные = с.sig(тела)
        поток = b"".join(тела)
        with tempfile.TemporaryDirectory() as tmp:
            путь = Path(tmp) / "т.sig"
            путь.write_bytes(данные)
            начала, длины, _, _ = chtenie.разобрать_sig_файл(путь)
            for от, сколько in ((0, 0), (0, 5), (3, 7), (10, 1), (11, 30), (100, 0), (len(поток) - 1, 0),
                                (len(поток) - 3, 100), (len(поток), 0), (5, 10**9)):
                with self.subTest(от=от, сколько=сколько):
                    записано = chtenie.тела_в_файл(путь, начала, длины, Path(tmp) / "о.bin", от, сколько)
                    ждём = поток[от:от + сколько] if сколько else поток[от:]
                    self.assertEqual(ждём, (Path(tmp) / "о.bin").read_bytes())
                    self.assertEqual(len(ждём), записано)

    def test_статистика_по_кускам(self):
        данные = с.e1(3000)
        целиком = statistika.посчитать(данные)
        частоты = statistika.частоты_кусками([данные[i:i + 777] for i in range(0, len(данные), 777)])
        по_кускам = statistika.посчитать(данные, частоты=частоты)
        self.assertEqual(целиком, по_кускам)


class СтатистикаКускамиTests(unittest.TestCase):
    def test_энтропия_и_частые_по_счёту(self):
        self.assertEqual(8.0, statistika.энтропия_частот(np.ones(256, dtype=np.int64) * 7))
        self.assertEqual(1.0, statistika.энтропия_частот(np.array([5, 5] + [0] * 254)))
        self.assertEqual(0.0, statistika.энтропия_частот(np.zeros(256, dtype=np.int64)))
        self.assertEqual(0.0, statistika.энтропия(np.zeros(0, dtype=np.uint8)))
        self.assertEqual(0.0, statistika.энтропия(np.zeros(10, dtype=np.uint8)))
        self.assertAlmostEqual(1.5, statistika.энтропия(np.array([0, 0, 1, 2], dtype=np.uint8)))
        частоты = statistika.частоты_кусками([bytes([1, 1, 2]), b"", bytes([2, 255])])
        self.assertEqual((256, 2, 2, 1, 5), (len(частоты), частоты[1], частоты[2], частоты[255], частоты.sum()))
        данные = bytes([7] * 50 + [9] * 30 + list(range(100, 120)))
        с_ = statistika.посчитать(данные)
        self.assertEqual(100, с_.байт)
        self.assertEqual([(7, 0.5), (9, 0.3)], с_.частые[:2])
        self.assertEqual(6, len(с_.частые))
        по_частотам = statistika.посчитать(данные[:10], частоты=np.bincount(np.frombuffer(данные, np.uint8), minlength=256))
        self.assertEqual((100, с_.энтропия, [(7, 0.5), (9, 0.3)]), (по_частотам.байт, по_частотам.энтропия, по_частотам.частые[:2]))
        # Битовая часть — по выборке: ровно выборка_бит // 8 байт.
        выборка = statistika.посчитать(bytes([0xFF] * 8 + [0] * 8), выборка_бит=64)
        self.assertEqual((1.0, 0, 64), (выборка.единиц, выборка.серия_нулей, выборка.серия_единиц))
        self.assertEqual(0.5, statistika.посчитать(bytes([0xFF] * 8 + [0] * 8)).единиц)


class АвтоанализБольшогоTests(unittest.TestCase):
    def test_по_источнику_то_же_что_по_байтам(self):
        """Большой путь (источник: начало в память, счёт байт кусками) находит ту же цепочку."""
        x = в_биты(с.hdlc(с.пакеты_ip(300), флагов_между=4))
        данные = в_байты(с.скремблировать(x, (3, 20)))
        with tempfile.TemporaryDirectory() as tmp:
            путь = Path(tmp) / "п.bin"
            путь.write_bytes(данные * 3)
            по_байтам = razbor.разобрать(данные=данные * 3, имя="п.bin")
            with mock.patch.object(razbor, "НАЧАЛО_БАЙТ", len(данные)), \
                    mock.patch.object(razbor, "ОКНО_СВЕРКИ", 4096):
                по_источнику = razbor.разобрать(имя="п.bin", источник=Источник(путь))
        self.assertEqual([(н.уровень, н.что) for н in по_байтам.находки],
                         [(н.уровень, н.что) for н in по_источнику.находки])
        self.assertEqual(по_байтам.статистика.энтропия, по_источнику.статистика.энтропия)
        self.assertEqual(len(данные) * 3, по_источнику.статистика.байт)
        текст = " ".join(по_источнику.ограничения)
        self.assertIn("окно в середине файла", текст)
        self.assertIn("окно в конце файла", текст)

    def test_неоднородный_файл_замечен(self):
        with tempfile.TemporaryDirectory() as tmp:
            путь = Path(tmp) / "н.bin"
            путь.write_bytes(np.random.default_rng(1).bytes(200_000) + bytes(200_000))
            with mock.patch.object(razbor, "НАЧАЛО_БАЙТ", 100_000), mock.patch.object(razbor, "ОКНО_СВЕРКИ", 4096), \
                    mock.patch.dict(razbor.ПРОФИЛИ["быстро"], {"время": 5.0}):
                р = razbor.разобрать(имя="н.bin", источник=Источник(путь))
        self.assertIn("в конце файла поток не такой, как в начале", " ".join(р.ограничения))

    def test_остановка_по_просьбе(self):
        флаг = {"стоп": False}

        def ход(строка):
            флаг["стоп"] = True                        # человек нажал «Остановить» на первой строке
        начало = time.monotonic()
        р = razbor.разобрать(данные=np.random.default_rng(5).bytes(200_000), имя="шум.bin",
                             профиль="обычно", ход=ход, отмена=lambda: флаг["стоп"])
        self.assertLess(time.monotonic() - начало, 60)
        self.assertIn("разбор остановлен по просьбе", " ".join(р.ограничения))


class ШагиЦепочкиTests(unittest.TestCase):
    def test_преобразования_с_начала_до_проверяемого(self):
        from reportgen.potok.nahodka import Находка  # noqa: PLC0415
        н = lambda уровень, что, **св: Находка(уровень=уровень, что=что, уверенность=1.0, мера="", свойства=св)  # noqa: E731
        цепочка = [н("скремблер", "самосинхронизирующийся скремблер 1 + x^-3 + x^-20"),
                   н("код", "ТКБ режима", слой="ткб режим Radyne-0.793 кадр 2964"),
                   н("канальный", "HDLC-подобное обрамление"),
                   н("скремблер", "самосинхронизирующийся скремблер 1 + x^-14 + x^-15")]
        self.assertEqual([{"вид": "слой", "вкл": True, "слой": "скремблер 3,20"},
                          {"вид": "слой", "вкл": True, "слой": "ткб режим Radyne-0.793 кадр 2964"}],
                         razbor.шаги_цепочки(цепочка))
        self.assertEqual([], razbor.шаги_цепочки([н("скремблер", "аддитивная ПСП 1 + x^-14 + x^-15"), *цепочка]))
        self.assertEqual([], razbor.шаги_цепочки([н("скремблер", "самосинхронизирующийся скремблер без полинома")]))
        self.assertEqual([], razbor.шаги_цепочки([н("код", "самосинхронизирующийся скремблер 1 + x^-3")]))
        self.assertEqual([], razbor.шаги_цепочки([]))


class УскоренияTests(unittest.TestCase):
    """Ускоренные места дают то же, что прежние."""

    def test_кадры_hdlc_разом_как_по_одному(self):
        rng = np.random.default_rng(3)
        x = в_биты(с.hdlc(с.пакеты_ip(300), флагов_между=2))
        for ряд in (rng.integers(0, 2, 500_000).astype(np.uint8), x, x ^ (rng.random(len(x)) < 0.003),
                    (rng.random(300_000) < 0.85).astype(np.uint8), np.zeros(10, np.uint8)):
            ряд = ряд.astype(np.uint8)
            self.assertEqual(hdlc._кадры_по_одному(ряд), hdlc.кадры(ряд))

    def test_несовпадения_прямо_как_бпф(self):
        rng = np.random.default_rng(1)
        x = rng.integers(0, 2, 50_000).astype(np.uint8)
        for L in (4, 9, 48, 256, 300):
            w = rng.integers(0, 2, L).astype(np.uint8)
            прямо = sinhro.несовпадения(x, w)
            with mock.patch.object(sinhro, "ПРЯМО_ДО", 0):
                бпф = sinhro.несовпадения(x, w)
            self.assertTrue(np.array_equal(прямо, бпф))
            self.assertEqual(np.int32, прямо.dtype)

    def test_уолш_float32_как_float64(self):
        rng = np.random.default_rng(2)
        for k in (1, 3, 10, 16):
            v = np.bincount(rng.integers(0, 1 << k, 1 << 15), minlength=1 << k).astype(np.float64)
            эталон = v.copy()
            h = 1
            while h < len(эталон):
                эталон = эталон.reshape(-1, 2 * h)
                a = эталон[:, :h].copy()
                эталон[:, :h] += эталон[:, h:]
                эталон[:, h:] = a - эталон[:, h:]
                эталон = эталон.reshape(-1)
                h *= 2
            итог = kod.уолш(v)
            self.assertTrue(np.array_equal(эталон, итог))
            self.assertEqual(np.float64, итог.dtype)
        дробный = rng.normal(size=64)
        self.assertTrue(np.allclose(kod.уолш(kod.уолш(дробный)) / 64, дробный))


class ЗагрузкиTests(unittest.TestCase):
    def test_по_порядку_докачка_и_отказ(self):
        with tempfile.TemporaryDirectory() as tmp:
            з = Загрузки(Path(tmp))
            данные = np.random.default_rng(1).bytes(10_000)
            запись = з.начать(владелец=1, имя="ф.bin", размер=len(данные), сведения={"сессия": "x"})
            ид = запись["ид"]
            self.assertEqual(4000, з.дописать(ид, 1, 0, данные[:4000]))
            with self.assertRaises(ОшибкаЗагрузки) as о:
                з.дописать(ид, 1, 6000, данные[6000:])              # пропуск — отказ с тем, сколько принято
            self.assertEqual(4000, о.exception.принято)
            with self.assertRaises(KeyError):
                з.прочитать(ид, 2)                                  # чужая
            self.assertEqual(4000, з.прочитать(ид, 1)["принято"])    # после «обрыва» — с 4000
            with self.assertRaises(ОшибкаЗагрузки):
                з.забрать(ид, 1)
            з.дописать(ид, 1, 4000, данные[4000:])
            with self.assertRaises(ОшибкаЗагрузки):
                з.дописать(ид, 1, 10_000, b"x")                     # за концом
            путь, _ = з.забрать(ид, 1)
            self.assertEqual(данные, путь.read_bytes())
            with self.assertRaises(KeyError):
                з.прочитать("../../etc/passwd", 1)
            другая = з.начать(владелец=1, имя="г.bin", размер=5, сведения={})["ид"]
            з.отменить(другая, 1)
            self.assertEqual([], [п.name for п in Path(tmp).iterdir() if п.name.startswith(другая)])


class ЗагрузкиГраницыTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.з = Загрузки(Path(self._tmp.name))

    def test_объём_ид_кусок(self):
        for размер in (0, -1):
            with self.assertRaises(ОшибкаЗагрузки) as о:
                self.з.начать(владелец=1, имя="п", размер=размер, сведения={})
            self.assertEqual(0, о.exception.принято)
        ид = self.з.начать(владелец=1, имя="п", размер=1, сведения={})["ид"]
        for плохой in ("g" * 16, ид[:15], ид + "0", ид.upper() if ид.upper() != ид else "A" * 16):
            with self.assertRaises(KeyError):
                self.з.прочитать(плохой, 1)
        большой = self.з.начать(владелец=1, имя="б", размер=(32 << 20) + 1, сведения={})["ид"]
        with self.assertRaisesRegex(ОшибкаЗагрузки, "кусок больше 32 МБ") as о:
            self.з.дописать(большой, 1, 0, bytes((32 << 20) + 1))
        self.assertEqual(0, о.exception.принято)
        self.assertEqual(32 << 20, self.з.дописать(большой, 1, 0, bytes(32 << 20)))

    def test_уборка_брошенных(self):
        старая = self.з.начать(владелец=1, имя="с", размер=5, сведения={})["ид"]
        with mock.patch("reportgen.potok.zagruzki.time.time", return_value=time.time() + 7 * 24 * 3600 - 60):
            self.з.прибрать()
        self.assertEqual(1, len(self.з.список(1)))                      # без минуты неделя — ещё лежит
        with mock.patch("reportgen.potok.zagruzki.time.time", return_value=time.time() + 7 * 24 * 3600 + 60):
            свежая = self.з.начать(владелец=1, имя="н", размер=5, сведения={})["ид"]   # уборка — при начале новой
        self.assertEqual([свежая], [з["ид"] for з in self.з.список(1)])
        self.assertFalse((Path(self._tmp.name) / f"{старая}.part").exists())
        (Path(self._tmp.name) / "битая.json").write_text("{")
        self.з.прибрать()
        self.assertFalse((Path(self._tmp.name) / "битая.json").exists())
        self.assertEqual([], self.з.список(2))


class ЧерезСерверTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase  # noqa: PLC0415

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.к = self.сеть.client
        from reportgen.web import api  # noqa: PLC0415
        запас = mock.patch.object(api, "ЗАПАС_ДИСКА", 0)        # тестовой машине гигабайт запаса не нужен
        запас.start()
        self.addCleanup(запас.stop)
        self.данные_папка = Path(self.сеть.tmp)
        настройки = self.сеть.app.state.settings
        настройки.potok_memory_mb = 1                       # массив больше 1 МБ — по кускам
        self.сессия = self.к.post("/api/sessions", json={"name": "большие"}).json()["id"]
        self.вход = self.данные_папка / "vhod"
        self.вход.mkdir(exist_ok=True)
        self.корень = self.к.get("/api/files/roots").json()["items"][0]["id"]

    def дождаться(self, ид):
        for _ in range(600):
            с = self.к.get(f"/api/potok/{ид}").json()
            if с["состояние"] not in ("ждёт", "идёт"):
                return с
            time.sleep(0.1)
        self.fail("не дождались")

    def сырые(self, ид, этап=0):
        части, место = [], 0
        while True:
            о = self.к.get(f"/api/potok/{ид}/raw?stage={этап}&offset={место}&length=1000000")
            self.assertEqual(200, о.status_code, о.text)
            части.append(о.content)
            место += len(о.content)
            if место >= int(о.headers["X-Total-Bytes"]):
                return b"".join(части)

    def test_по_ссылке_без_копии_окна_и_изменение(self):
        данные = np.random.default_rng(7).bytes(3_000_000)
        (self.вход / "под").mkdir()
        (self.вход / "под" / "запись.bin").write_bytes(данные)
        список = self.к.get("/api/files/list", params={"root": self.корень, "q": "запись"}).json()
        self.assertEqual(["под/запись.bin"], [э["путь"] for э in список["элементы"]])
        о = self.к.post(f"/api/sessions/{self.сессия}/files/link",
                        json={"root": self.корень, "path": "под/запись.bin", "bit_order": "lsb", "start": 1000,
                              "length": 2_000_000})
        self.assertEqual(200, о.status_code, о.text)
        ид = о.json()["id"]
        self.assertTrue(о.json()["link"])
        с_ = self.к.get(f"/api/potok/{ид}").json()
        self.assertIn("ссылка", с_)
        self.assertFalse((self.данные_папка / "potok" / ид / "вход.bin").exists())   # без копии
        self.assertEqual(rastr.развернуть_биты(данные[1000:2_001_000]), self.сырые(ид))
        окно = self.к.get(f"/api/potok/{ид}/bits?start=8&count=64").json()
        self.assertEqual(16_000_000, окно["всего"])
        # Производный узел над ссылкой — шаги по кускам (предел памяти 1 МБ), итог как в памяти.
        о = self.к.post(f"/api/potok/{ид}/derive", json={"stage": 0, "steps": [
            {"вид": "слой", "слой": "инверсия"}, {"вид": "слой", "слой": "обрезка начало 3 конец 5"}]})
        self.assertEqual(200, о.status_code, о.text)
        производный = о.json()["id"]
        self.assertEqual("готово", self.дождаться(производный)["состояние"])
        ждём = 1 - в_биты(rastr.развернуть_биты(данные[1000:2_001_000]))
        ждём = ждём[3:len(ждём) - 5]
        self.assertTrue(np.array_equal(ждём, в_биты(self.сырые(производный))[:len(ждём)]))
        # Большой массив целиком в память не берётся: инструмент — по началу, с пометкой.
        о = self.к.post(f"/api/potok/{ид}/search", json={"stage": 0, "pattern": "00", "kind": "hex"})
        self.assertIn("часть", о.json())
        # Файл переписали — узел говорит об этом словами.
        (self.вход / "под" / "запись.bin").write_bytes(данные[:-10])
        о = self.к.get(f"/api/potok/{ид}/raw?stage=0&offset=0&length=10")
        self.assertEqual(409, о.status_code)
        self.assertIn("изменён", о.json()["error"])

    def test_по_ссылке_нельзя_наружу(self):
        снаружи = self.данные_папка / "секрет.bin"
        снаружи.write_bytes(b"x" * 100)
        for путь in ("../секрет.bin", "/etc/passwd", "..\\секрет.bin", "C:/Windows/win.ini"):
            о = self.к.post(f"/api/sessions/{self.сессия}/files/link", json={"root": self.корень, "path": путь})
            self.assertIn(о.status_code, (400, 404), путь)
        (self.вход / "ссылка.bin").symlink_to(снаружи)
        о = self.к.post(f"/api/sessions/{self.сессия}/files/link", json={"root": self.корень, "path": "ссылка.bin"})
        self.assertEqual(400, о.status_code)
        о = self.к.post(f"/api/sessions/{self.сессия}/files/link", json={"root": "чужой", "path": "x"})
        self.assertEqual(404, о.status_code)
        self.сеть.login("admin")                                            # в чужую сессию — нельзя
        о = self.к.post(f"/api/sessions/{self.сессия}/files/link", json={"root": self.корень, "path": "x"})
        self.assertEqual(404, о.status_code)

    def test_загрузка_кусками_с_докачкой(self):
        данные = в_байты(с.скремблировать(в_биты(с.hdlc(с.пакеты_ip(300))), (3, 20)))
        о = self.к.post(f"/api/sessions/{self.сессия}/uploads",
                        json={"name": "п.bin", "size": len(данные), "bit_order": "msb"})
        self.assertEqual(200, о.status_code, о.text)
        ид = о.json()["id"]
        кусок = 30_000
        self.к.put(f"/api/potok-uploads/{ид}?offset=0", content=данные[:кусок])
        о = self.к.put(f"/api/potok-uploads/{ид}?offset={2 * кусок}", content=данные[2 * кусок:3 * кусок])
        self.assertEqual((409, кусок), (о.status_code, о.json()["received"]))     # пропуск — продолжить с 30000
        self.assertEqual(кусок, self.к.get(f"/api/potok-uploads/{ид}").json()["received"])
        self.assertEqual([ид], [з["id"] for з in self.к.get("/api/potok-uploads").json()["items"]])
        место = кусок
        while место < len(данные):
            о = self.к.put(f"/api/potok-uploads/{ид}?offset={место}", content=данные[место:место + кусок])
            место = о.json()["received"]
        о = self.к.post(f"/api/potok-uploads/{ид}/done")
        self.assertEqual(200, о.status_code, о.text)
        self.assertEqual(данные, self.сырые(о.json()["id"]))
        self.assertEqual([], self.к.get("/api/potok-uploads").json()["items"])

    def test_предел_до_отправки_и_отмена(self):
        self.сеть.app.state.settings.potok_max_mb = 1
        о = self.к.post(f"/api/sessions/{self.сессия}/uploads", json={"name": "б.bin", "size": 2 << 20})
        self.assertEqual(413, о.status_code)
        self.assertIn("по ссылке", о.json()["error"])
        ид = self.к.post(f"/api/sessions/{self.сессия}/uploads", json={"name": "м.bin", "size": 100}).json()["id"]
        self.assertEqual(200, self.к.delete(f"/api/potok-uploads/{ид}").status_code)
        self.assertEqual(404, self.к.get(f"/api/potok-uploads/{ид}").status_code)

    def test_отмена_шагов(self):
        данные = np.random.default_rng(2).bytes(3_000_000)
        (self.вход / "о.bin").write_bytes(данные)
        ид = self.к.post(f"/api/sessions/{self.сессия}/files/link",
                         json={"root": self.корень, "path": "о.bin", "bit_order": "msb"}).json()["id"]
        with mock.patch.object(potokovo, "КУСОК_БИТ", 1 << 12):
            производный = self.к.post(f"/api/potok/{ид}/derive", json={"stage": 0, "steps": [
                {"вид": "слой", "слой": "nrzi"}]}).json()["id"]
            self.assertIn(self.к.post(f"/api/potok/{производный}/cancel").status_code, (200, 409))
            с_ = self.дождаться(производный)
        self.assertIn(с_["состояние"], ("отменено", "готово"))
        if с_["состояние"] == "отменено":
            self.assertFalse((self.данные_папка / "potok" / производный / "вход.bin").exists())


@unittest.skipUnless(МЕДЛЕННЫЕ, "медленный: REPORTGEN_MEDLENNYE=1 (файл в гигабайт)")
class ГигабайтTests(unittest.TestCase):
    """Файл в 1 ГБ по ссылке: окно — сразу, слой — по кускам, автоанализ — по началу, память — в пределах."""

    def test_гигабайт(self):
        import resource  # noqa: PLC0415
        папка = Path(os.environ.get("REPORTGEN_БОЛЬШИЕ_ПАПКА") or tempfile.gettempdir())
        with tempfile.TemporaryDirectory(dir=папка) as tmp:
            путь = Path(tmp) / "гиг.bin"
            кусок = в_байты(с.скремблировать(в_биты(с.hdlc(с.пакеты_ip(2000), флагов_между=4)), (3, 20)))
            with open(путь, "wb") as ф:
                while ф.tell() < 1 << 30:
                    ф.write(кусок)
            ист = Источник(путь, ссылка=отпечаток(путь))
            t = time.monotonic()
            ист.биты(ист.бит // 2, ист.бит // 2 + (1 << 16))
            self.assertLess(time.monotonic() - t, 1.0)
            t = time.monotonic()
            бит, _ = potokovo.выполнить(ист, rastr.проверить_шаги([{"вид": "слой", "слой": "инверсия"}]),
                                        Path(tmp) / "инв.bin")
            секунд = time.monotonic() - t
            self.assertEqual(ист.бит, бит)
            Path(tmp, "инв.bin").unlink()
            t = time.monotonic()
            р = razbor.разобрать(имя="гиг.bin", источник=ист, профиль="быстро")
            self.assertTrue(any(н.уровень == "канальный" for н in р.находки), р.отчёт())
            print(json.dumps({"слой_с": round(секунд, 1), "МБ_с": round(1024 / секунд, 1),
                              "автоанализ_с": round(time.monotonic() - t, 1),
                              "пик_МБ": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss >> 10}, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
