"""Разбор по этапам: дерево гипотез, ручное снятие слоёв, карта файла, pcap,
задания со страницы «Разбор потока» и сама страница."""

import struct
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import karta, lineynye, podskazki, rastr, sinhro, skrembler, разобрать
from reportgen.potok.bity import в_байты, в_биты
from reportgen.potok.razbor import снять_вручную, этапы
from reportgen.potok.zadaniya import Задания, выгрузка

КОРЕНЬ = Path(__file__).resolve().parents[1]


def цепочка(доля_ошибок=0.005):
    """IP → HDLC → скремблер V.35 → свёрточный 171/133 → ошибки линии."""
    x = в_биты(с.hdlc(с.пакеты_ip(160), флагов_между=4))
    код = с.свёрточный(с.скремблировать(x, (3, 20)))
    return в_байты(код ^ (np.random.default_rng(2).random(len(код)) < доля_ошибок)
                   .astype(np.uint8))


class ДеревоTests(unittest.TestCase):
    def test_этапы_и_альтернативы(self):
        разбор = разобрать(данные=цепочка(), имя="запись.bin")
        список = этапы(разбор)
        self.assertEqual(["код", "скремблер", "канальный", "сетевой"],
                         [э["уровень"] for э in список])
        # Декодируется весь поток (а не первые 2¹⁸ бит): все 160 кадров и пакетов.
        бит = len(в_биты(с.hdlc(с.пакеты_ip(160), флагов_между=4)))
        self.assertEqual(f"{бит} бит", список[0]["выход"])
        self.assertIn("160 кадров", список[2]["выход"])
        self.assertIn("160 пакетов", список[3]["выход"])
        # У уровня кода, кроме выбранного, проверена и другая гипотеза.
        отчёт = разбор.отчёт()
        self.assertIn("ДРУГИЕ ГИПОТЕЗЫ", отчёт)

    def test_ход_и_этапы_сообщаются(self):
        строки, найдено = [], []
        разобрать(данные=цепочка(), имя="запись.bin", ход=строки.append, этап=найдено.append)
        self.assertTrue(any("пробую — скремблер" in с_ for с_ in строки))
        self.assertTrue(any(с_.startswith("найдено: пакеты IP") for с_ in строки))
        self.assertEqual(["код", "скремблер", "канальный", "сетевой"],
                         [н.уровень for н in найдено])


class РучноеСнятиеTests(unittest.TestCase):
    def test_скремблер_и_код_вручную(self):
        разбор = разобрать(данные=цепочка(0.0), имя="запись.bin",
                           снять=["свёрточный 171/133 K=7", "скремблер 3,20"])
        self.assertEqual(["вручную", "вручную", "канальный", "сетевой"],
                         [н.уровень for н in разбор.находки])
        self.assertIn("снято по указанию: свёрточный 171/133 K=7", разбор.находки[0].что)

    def test_простые_слои(self):
        биты = с.случайные_биты(1000)
        self.assertTrue(np.array_equal(1 - биты, снять_вручную(биты, "инверсия")[0]))
        self.assertTrue(np.array_equal(биты[5:], снять_вручную(биты, "сдвиг 5")[0]))
        # Манчестер из инструмента убран: такого слоя больше нет.
        d = с.случайные_биты(20_000)
        with self.assertRaises(ValueError):
            снять_вручную(np.column_stack([1 - d, d]).reshape(-1), "манчестер")

    def test_накопление_обратно_nrzi(self):
        """Дифференциальный кодер (накопление по модулю 2) и NRZI взаимно обратны: снятое
        NRZI после накопления даёт исходные биты (кроме первого, у которого нет соседа)."""
        биты = с.случайные_биты(5000)
        накоплено, запись = снять_вручную(биты, "накопление")
        self.assertEqual(int(биты[0]), int(накоплено[0]))
        self.assertTrue(np.array_equal(np.bitwise_xor.accumulate(биты), накоплено))
        self.assertTrue(np.array_equal(биты[1:], lineynye.nrzi(накоплено)[1:]))
        self.assertTrue(np.array_equal(накоплено, снять_вручную(биты, "дифкодер")[0]))
        self.assertTrue(запись.подробно[0].startswith("накопление по модулю 2 (дифференциальный кодер)"))

    def test_4b5b_и_8b10b_вручную(self):
        rng = np.random.default_rng(5)
        полубайты = rng.integers(0, 16, 20_000)
        символы = [lineynye.ДАННЫЕ_4B5B[x] for x in полубайты]
        поток = np.array([(v >> (4 - i)) & 1 for v in символы for i in range(5)], np.uint8)
        ряд, запись = снять_вручную(поток, "4b5b")
        ожидалось = np.unpackbits(полубайты.astype(np.uint8)[:, None], axis=1)[:, 4:].reshape(-1)
        self.assertTrue(np.array_equal(ожидалось, ряд))
        self.assertIn("4B/5B", запись.подробно[0])
        данные = bytes(rng.integers(0, 256, 5000).tolist())
        ряд, запись = снять_вручную(lineynye.закодировать_8b10b(данные)[3:], "8b/10b")
        self.assertEqual(данные[:1000], в_байты(ряд)[:1000])
        self.assertIn("8B/10B", запись.подробно[0])
        # Запятые и баланс есть, а символы не из таблицы (другая разновидность кода) —
        # слой не снимается, а не отдаёт пустой ряд.
        чужие = [с_ for с_ in range(1024) if bin(с_).count("1") == 5
                 and с_ not in lineynye.ТАБЛИЦА_8B10B and с_ not in lineynye.K28_5]
        символы = [lineynye.K28_5[0] if н % 8 == 0 else чужие[rng.integers(len(чужие))] for н in range(4000)]
        чужой = np.array([(v >> (9 - i)) & 1 for v in символы for i in range(10)], np.uint8)
        self.assertIsNone(lineynye.код_8b10b(чужой).дальше)
        with self.assertRaisesRegex(ValueError, "таблица кода с потоком не сошлась"):
            снять_вручную(чужой, "8b10b")
        # Не код в линии — внятная ошибка, а не пустой слой.
        for вид in ("4b5b", "8b10b"):
            with self.subTest(вид=вид), self.assertRaisesRegex(ValueError, вид[:2].upper()):
                снять_вручную(с.случайные_биты(50_000), вид)

    def test_непонятное_указание(self):
        with self.assertRaises(ValueError):
            снять_вручную(с.случайные_биты(1000), "расшифровать всё")


class КартаTests(unittest.TestCase):
    def test_участки_и_сигнатуры_и_zlib(self):
        import zlib
        текст = ("Проверка связи. " * 3000).encode()
        данные = (bytes(20_000) + np.random.default_rng(1).bytes(60_000) + текст
                  + zlib.compress(текст))
        найдено = karta.описать(данные)
        подробно = " ".join(найдено.подробно)
        self.assertIn("заполнение 0x00", подробно)
        self.assertIn("похоже на случайный", подробно)
        self.assertIn("упорядоченный", подробно)
        self.assertIn("распаковались куски zlib", подробно)

    def test_колебание_около_порога_не_дробит(self):
        # SLIP с IP — энтропия около 7,5: без слияния карта дробилась на десятки
        # участков «случайный/смешанный».
        участки = karta.участки(с.slip(с.пакеты_ip(400)))
        self.assertLessEqual(len(участки), 2)

    def test_pcap(self):
        пакеты = с.пакеты_ip(30)
        данные = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 101) + b"".join(
            struct.pack("<IIII", 0, 0, len(п), len(п)) + п for п in пакеты)
        канал, прочитано = karta.pcap(данные)
        self.assertEqual(("IP", пакеты), (канал, прочитано))
        разбор = разобрать(данные=данные, имя="захват.pcap")
        self.assertEqual("сетевой", разбор.находки[0].уровень)
        self.assertIn("30 из 30", разбор.находки[0].мера)


class ЗаданияTests(unittest.TestCase):
    def setUp(self):
        self.папка = Path(tempfile.mkdtemp())
        self.задания = Задания(self.папка)

    def дождаться(self, ид):
        for _ in range(600):
            состояние = self.задания.прочитать(ид)
            if состояние["состояние"] not in ("ждёт", "идёт"):
                return состояние
            time.sleep(0.2)
        self.fail("задание не закончилось")

    def test_этапы_выгрузки_и_продолжение(self):
        ид = self.задания.создать(владелец=1, имя="запись.bin", данные=цепочка(),
                                  профиль="быстро")
        состояние = self.дождаться(ид)
        self.assertEqual("готово", состояние["состояние"], состояние.get("ошибка"))
        self.assertEqual(["bin", "bin", "sig", "pcap"],
                         [э.get("выгрузка") for э in состояние["этапы"]])
        self.assertTrue(any("пробую" in строка for строка in состояние["журнал"]))
        # Выгрузка кадров — в формате .Sig: два байта длины и кадр.
        sig = self.задания.файл_этапа(ид, 3).read_bytes()
        длина = struct.unpack(">H", sig[:2])[0]
        self.assertGreater(длина, 20)
        # pcap с сырыми IP открывается как захват.
        канал, пакеты = karta.pcap(self.задания.файл_этапа(ид, 4).read_bytes())
        self.assertEqual(("IP", 160), (канал, len(пакеты)))          # весь поток, все пакеты
        # Продолжение с этапа 1 со снятием скремблера вручную.
        ид2 = self.задания.продолжить(ид, 1, владелец=1, снять=["скремблер 3,20"],
                                      профиль="быстро")
        продолжение = self.дождаться(ид2)
        self.assertEqual(["вручную", "канальный", "сетевой"],
                         [э["уровень"] for э in продолжение["этапы"]])
        self.assertEqual(f"{ид}#1", продолжение["от"])

    def test_продолжать_не_с_чего(self):
        ид = self.задания.создать(владелец=1, имя="захват.sig", данные=с.sig(с.пакеты_ip(20)),
                                  профиль="быстро")
        self.дождаться(ид)
        with self.assertRaises(ValueError):
            self.задания.продолжить(ид, 1, владелец=1)

    def test_выгрузка_по_виду(self):
        self.assertEqual("bin", выгрузка(np.array([1, 0, 1], np.uint8), "биты")[1])
        self.assertIsNone(выгрузка(None, "биты"))


class РастрTests(unittest.TestCase):
    def test_периоды_e1_без_гармоник(self):
        # У E1 с заполнением 0xD5 честных периодов три: байт (8), цикл (256) и
        # пара циклов с FAS/NFAS (512). Кратные 768, 1024 — гармоники, их нет.
        ряд = np.frombuffer(с.hdlc(с.пакеты_ip(60)), dtype=np.uint8)
        поток = с.e1(len(ряд), каналы=lambda ц, ки: int(ряд[ц]) if ки == 1 else 0xD5)
        периоды = rastr.периоды(в_биты(поток))
        self.assertEqual([512, 256, 8], [п["период"] for п in периоды])
        self.assertEqual(["сверхцикл: 2 × 256", "сверхцикл: 32 × 8", ""],
                         [п["заметка"] for п in периоды])

    def test_период_синхрослова_и_шум(self):
        x = с.случайные_биты(100 * 3000).reshape(3000, 100)
        x[:, :12] = [1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 0]
        периоды = rastr.периоды(x.reshape(-1))
        # Кратные — гармоники; отзвуки синхрослова на почти кратных (5094…) — тоже не кандидаты.
        self.assertEqual([100], [п["период"] for п in периоды])
        self.assertEqual([], rastr.периоды(с.случайные_биты(400_000)))

    def test_маска_и_столбцы(self):
        x = np.arange(40, dtype=np.uint8) % 2
        канал = rastr.по_маске(x, {"период": 10, "сдвиг": 1, "позиции": [2, 0, 2]})
        # Со сдвигом 1 цикл — биты 1…10: позиции 0 и 2 — это биты 1 и 3 (единицы).
        self.assertEqual([1, 1] * 3, канал.tolist())
        self.assertEqual([1.0, 0.0, 1.0], rastr.столбцы(x, 10, 1)[:3])
        # Позиции идут в порядке цикла, как бы их ни перечислили.
        y = np.arange(80, dtype=np.uint8) % 3 == 0
        self.assertEqual([y[1], y[33], y[41], y[73]],
                         rastr.по_маске(y.astype(np.uint8), {"период": 40, "позиции": [33, 1]}).tolist())
        with self.assertRaises(ValueError):
            rastr.по_маске(x, {"период": 10, "позиции": [10, 11]})
        self.assertEqual("0–2, 5, 7–8", rastr._диапазоны([8, 0, 1, 2, 5, 7]))


class ПодсказкиTests(unittest.TestCase):
    """Подсказки «что дальше» по приметам: каждое правило — на своём потоке."""

    @staticmethod
    def кнопки(биты, состояние=None, этап=0):
        приметы = podskazki.приметы(биты)
        return приметы, {д["кнопка"]: д for х in podskazki.подсказки(состояние or {}, этап, приметы)
                         for д in х["действия"]}

    def test_случайный_поток(self):
        приметы, кнопки = self.кнопки(с.случайные_биты(1 << 20))
        self.assertEqual([], приметы["периоды"])
        self.assertEqual({"Найти скремблер", "Снять nrzi и разобрать", "Подобрать плоскость",
                          "Спросить помощника"}, set(кнопки))

    def test_e1_период_и_перекос(self):
        ряд = np.frombuffer(с.hdlc(с.пакеты_ip(60)), dtype=np.uint8)
        поток = в_биты(с.e1(len(ряд), каналы=lambda ц, ки: int(ряд[ц]) if ки == 1 else 0xD5))
        _, кнопки = self.кнопки(поток)
        self.assertEqual(256, кнопки["Растр по 256"]["период"])
        self.assertIn("Найти кадры", кнопки)
        self.assertNotIn("Найти код", кнопки, "связи байтового заполнения — не код")
        # Цикл уже найден на этапе 1 — растр по периоду после него не советуем.
        _, кнопки = self.кнопки(поток, {"этапы": [{"номер": 1, "уровень": "цикл", "что": "E1"}]}, 1)
        self.assertNotIn("Растр по 256", кнопки)

    def test_манчестера_нет(self):
        # Манчестер из инструмента убран: ни подсказки, ни кнопки снять его.
        x = с.случайные_биты(200_000)
        _, кнопки = self.кнопки(np.stack([x ^ 1, x], 1).reshape(-1))
        self.assertFalse([к for к in кнопки if "анчестер" in к])

    def test_код_и_псп_на_паузах(self):
        _, кнопки = self.кнопки(с.свёрточный(с.случайные_биты(300_000)))
        self.assertIn("Найти код", кнопки)
        паузы = в_биты(с.hdlc(с.пакеты_ip(260), флагов_между=40))
        _, кнопки = self.кнопки(паузы ^ skrembler.псп((14, 15), np.ones(15, np.uint8), len(паузы)))
        self.assertEqual({"Найти код", "Найти скремблер", "Спросить помощника про LDPC",
                          "Спросить помощника"}, set(кнопки))
        self.assertEqual("ldpc", кнопки["Спросить помощника про LDPC"]["тема"])

    def test_флаги_hdlc_и_сдвиг_байта(self):
        приметы, кнопки = self.кнопки(в_биты(с.hdlc(с.пакеты_ip(260), флагов_между=40)))
        self.assertGreater(приметы["флагов_к_случайному"], 3)
        self.assertIn("Найти кадры", кнопки)
        # Текст: байты неравновероятны, и при верном выравнивании это видно.
        текст = ("Акт проверки канала связи № 12. " * 3000).encode("cp1251")
        _, кнопки = self.кнопки(np.concatenate([np.ones(3, np.uint8), в_биты(текст)]))
        self.assertEqual("сдвиг 3", кнопки["Сдвинуть и разобрать"]["слой"])

    def test_время_вышло_и_короткий(self):
        _, кнопки = self.кнопки(с.случайные_биты(1 << 16),
                                {"ограничения": ["время разбора исчерпано в потоке"]})
        self.assertEqual("глубоко", кнопки["Разобрать с профилем «глубоко»"]["профиль"])
        self.assertEqual("Слишком короткий поток", podskazki.подсказки(
            {}, 0, podskazki.приметы(с.случайные_биты(1000)))[0]["что"])

    def test_контекст_для_помощника(self):
        состояние = {"имя": "ррл.bin", "байт": 100, "профиль": "обычно",
                     "этапы": [{"номер": 1, "уровень": "скремблер", "что": "скремблер V.35",
                                "мера": "100 %", "подробно": ["полином 1 + x^-3 + x^-20"],
                                "альтернативы": ["аддитивная ПСП x^15"]}],
                     "не_найдено": ["HDLC: нет"], "ограничения": ["время разбора исчерпано"]}
        приметы = podskazki.приметы(с.случайные_биты(1 << 18))
        текст = podskazki.контекст(состояние, 1, приметы,
                                   podskazki.подсказки(состояние, 1, приметы))
        for кусок in ("1. [скремблер] скремблер V.35 — 100 %", "другие гипотезы: аддитивная ПСП x^15",
                      "Проверено и не найдено: HDLC: нет", "Приметы (поток после этапа 1):",
                      "- Разобрать глубже:", "снять слой вручную"):
            self.assertIn(кусок, текст)
        self.assertNotIn("- Спросить помощника", текст)
        self.assertIn("после этапа 1 (скремблер V.35)", podskazki.вопрос(состояние, 1))


class СинхроTests(unittest.TestCase):
    """Синхрокомбинация: разбор записи, поиск, синхронизация, кадры с маховиком."""

    @classmethod
    def setUpClass(cls):
        # Кадры по 400 бит: синхрокомбинация 0x1ACFFC1D (32 бита) и случайные данные.
        данные = с.случайные_биты(400 * 2000, сид=7).reshape(2000, 400)
        данные[:, :32] = sinhro.слово("0x1ACFFC1D")
        cls.кадры = данные.reshape(-1)
        cls.поток = np.concatenate([с.случайные_биты(123, сид=8), cls.кадры])

    def test_запись_слова(self):
        self.assertEqual([0, 1, 0, 0, 0, 1, 1, 1], sinhro.слово("0x47").tolist())
        self.assertEqual([0, 1, 0, 0, 0, 1, 1, 1], sinhro.слово("47h").tolist())
        self.assertEqual([0, 0, 1, 1, 0, 1, 1], sinhro.слово("0011011").tolist())
        self.assertEqual([1, 0], sinhro.слово("0b10").tolist())
        self.assertEqual(32, len(sinhro.слово("1ACF FC1D")))
        self.assertEqual("0x47 (01000111)", sinhro.словами(sinhro.слово("0x47")))
        for плохое in ("", "0xZZ", "0b12", "1" * 200):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                sinhro.слово(плохое)

    def test_поиск_шаг_и_начало(self):
        н = sinhro.найти(self.поток, sinhro.слово("0x1ACFFC1D"))
        self.assertEqual((2000, 0, 400, 123, False), (н["прямых"], н["инверсных"], н["длина_кадра"],
                                                       н["начало"], н["инверсия"]))
        self.assertEqual({"шаг": 400, "раз": 1999, "доля": 1.0}, н["шаги"][0])
        # С ошибками: два бита в каждом слове испорчены — находится только с допуском.
        порча = self.поток.copy()
        for п in н["позиции"]:
            порча[п + 3] ^= 1
            порча[п + 20] ^= 1
        self.assertEqual(0, sinhro.найти(порча, sinhro.слово("0x1ACFFC1D"))["прямых"])
        self.assertEqual(2000, sinhro.найти(порча, sinhro.слово("0x1ACFFC1D"), 2)["прямых"])

    def test_начало_не_по_случайному_совпадению(self):
        # Слово стоит в данных до первого кадра — началом должно стать первое
        # вхождение, за которым слово повторяется с шагом кадра.
        слово = sinhro.слово("0x1ACFFC1D")
        поток = np.concatenate([с.случайные_биты(50, сид=9), слово, с.случайные_биты(77, сид=10),
                                self.кадры])
        н = sinhro.найти(поток, слово)
        self.assertEqual(50 + 32 + 77, н["начало"])
        self.assertEqual(50, н["позиции"][0])

    def test_инверсия_и_синхронизация(self):
        ряд, н = sinhro.синхронизировать(1 - self.поток, sinhro.слово("0x1ACFFC1D"))
        self.assertTrue(н["инверсия"])
        self.assertTrue(np.array_equal(self.кадры, ряд))

    def test_кадры_с_проскальзыванием(self):
        # Лишние 3 бита внутри 500-го кадра и потерянные 2 бита внутри 1200-го
        # (за синхрокомбинацией): следующие кадры находятся со сдвигом.
        к = self.кадры
        поток = np.concatenate([к[:500 * 400 + 100], [1, 0, 1], к[500 * 400 + 100:1200 * 400 + 50],
                                к[1200 * 400 + 52:]])
        ряд, сводка = sinhro.выровнять(поток, sinhro.слово("0x1ACFFC1D"), 400)
        self.assertEqual((2, 0), (сводка["проскальзываний"], сводка["нет_на_месте"]))
        кадры = ряд.reshape(-1, 400)
        self.assertTrue(np.all(кадры[:, :32] == sinhro.слово("0x1ACFFC1D")))
        # Кадры, не задетые проскальзыванием, — ровно исходные.
        исходные = к.reshape(-1, 400)
        целые = [i for i in range(len(кадры)) if i not in (500, 1200)]
        self.assertTrue(np.array_equal(исходные[целые], кадры[целые]))
        # Испорченная синхрокомбинация — кадр по маховику, на ожидаемом месте.
        порча = self.кадры.copy()
        порча[700 * 400 + 5] ^= 1
        _, сводка = sinhro.выровнять(порча, sinhro.слово("0x1ACFFC1D"), 400)
        self.assertEqual((1, 0, 2000), (сводка["нет_на_месте"], сводка["проскальзываний"],
                                        сводка["кадров"]))
        with self.assertRaises(ValueError):
            sinhro.выровнять(поток, sinhro.слово("0x1ACFFC1D"), 16)

    def test_проскальзывание_к_ближайшему(self):
        # После проскальзывания в окне поиска два вхождения: случайное в данных
        # (за 12 бит до ожидаемого места) и настоящее (через 3 бита). Кадр —
        # от ближайшего к ожидаемому месту.
        слово = sinhro.слово("0x47")
        кадры = с.случайные_биты(100 * 200, сид=11).reshape(200, 100)
        кадры[:, :8] = слово
        поток = кадры.reshape(-1).copy()
        поток = np.concatenate([поток[:5020], [0, 1, 1], поток[5020:]])
        поток[5088:5096] = слово
        ряд, сводка = sinhro.выровнять(поток, слово, 100)
        self.assertTrue(np.array_equal(кадры[51:], ряд.reshape(-1, 100)[51:]))
        self.assertEqual(1, сводка["проскальзываний"])

    def test_синхрослова_радиосистем_и_спутника(self):
        """Записи DSD (дибиты «1»/«3») дают значения стандартов: P25 — TIA-102.BAAA, DMR —
        ETSI TS 102 361-1 табл. 9.2; SOF DVB-S2 — как в leansdr; A1A2 — G.707."""
        for слово, имя in (("0x5575F5FF77FF", "синхрослово кадра P25 фазы 1 (48 бит, C4FM)"),
                           ("0xDFF57D75DF5D", "DMR, БС — данные (48 бит)"),
                           ("0x755FD7DF75F7", "DMR, БС — речь (48 бит)"),
                           ("0xD5D7F77FD757", "DMR, АС — данные (48 бит)"),
                           ("0x7F7D5DD57DFD", "DMR, АС — речь (48 бит)"),
                           ("0xF6F6F6282828", "A1A1A1 A2A2A2 (F6F6F6 282828) — кадр SDH/SONET (G.707) и FAS "
                                              "кадра OTU (G.709)")):
            with self.subTest(имя=имя):
                self.assertIn(имя, sinhro.известная(sinhro.слово(слово)))
        sof = np.array([(0x18D2E82 >> (25 - i)) & 1 for i in range(26)], dtype=np.uint8)
        self.assertIn("SOF DVB-S2 (PLHEADER, 26 бит, π/2-BPSK)", sinhro.известная(sof))
        self.assertEqual(0x5575F5FF77FF, sinhro._дибиты("111113113311333313133333"))

    def test_известные_и_из_столбцов(self):
        self.assertIn("FAS E1 (G.704), в КИ0 чётных циклов, цикл 256 бит",
                      sinhro.известная(sinhro.слово("0011011")))
        self.assertIn("инверсная: синхробайт MPEG-TS 0x47, пакет 188 байт (204 с RS)",
                      sinhro.известная(sinhro.слово("0xB8"))[1:] + sinhro.известная(sinhro.слово("0xB8"))[:1])
        # ASM CCSDS сверен по gr-satellites (ccsds_rs_deframer) — известен; инверсный — тоже.
        self.assertEqual(["ASM CCSDS 0x1ACFFC1D (кадр телеметрии: блок РС (255, 223), рандомизатор, "
                          "свёрточный код 171/133)"], sinhro.известная(sinhro.слово("0x1ACFFC1D")))
        self.assertTrue(sinhro.известная(sinhro.слово("0xE53003E2"))[0].startswith("инверсная: ASM CCSDS"))
        # Незнакомое слово: лишь частичные совпадения (короткие известные слова входят в любое).
        self.assertTrue(all("содержит" in з or "часть:" in з
                            for з in sinhro.известная(sinhro.слово("0xE4B51C27"))))
        self.assertIn("содержит FAS E1 (G.704), в КИ0 чётных циклов, цикл 256 бит",
                      sinhro.известная(sinhro.слово("10011011")))
        self.assertIn("часть: флаг HDLC/PPP 01111110", sinhro.известная(sinhro.слово("111111")))
        слово = sinhro.из_столбцов(self.кадры, 400, 0, list(range(0, 32)))
        self.assertEqual(sinhro.слово("0x1ACFFC1D").tolist(), слово.tolist())
        self.assertIsNone(sinhro.из_столбцов(self.кадры, 400, 0, list(range(30, 40))))
        with self.assertRaises(ValueError):
            sinhro.из_столбцов(self.кадры, 400, 0, [1, 5])

    def test_битовые_операции_слоями(self):
        x = np.array([1, 0, 0, 0, 1, 1, 1, 0, 0, 1], dtype=np.uint8)
        self.assertEqual([0, 0, 0, 1, 0, 1, 1, 1], снять_вручную(x, "реверс 4")[0].tolist())
        self.assertEqual([0, 1, 1, 1, 0, 0, 0, 1], снять_вручную(x[:8], "реверс")[0].tolist())
        self.assertEqual([0, 0, 1, 0, 0, 1, 0, 0, 1, 1], снять_вручную(x, "xor 0b10")[0].tolist())
        self.assertEqual((x[:8] ^ 1).tolist(), снять_вручную(x[:8], "xor 0xFF")[0].tolist())
        self.assertEqual(x[1::3].tolist(), снять_вручную(x, "прореживание 3 фаза 1")[0].tolist())
        ряд, запись = снять_вручную(1 - self.поток, "синхро 0x1ACFFC1D")
        self.assertTrue(np.array_equal(self.кадры, ряд))
        self.assertIn("поток начат с бита 123 и инвертирован", " ".join(запись.подробно))
        ряд, запись = снять_вручную(self.поток, "кадры 0x1ACFFC1D длина 400 ошибок 1")
        self.assertTrue(np.array_equal(self.кадры, ряд))
        self.assertIn("кадров 2000 по 400 бит", запись.подробно[0])
        for плохое in ("синхро", "кадры 0x47", "реверс 1", "прореживание 1", "синхро 0xFFFFFFFFFF"):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                снять_вручную(self.поток, плохое)


class СтраницаTests(unittest.TestCase):
    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)

    def загрузить(self, данные=None, профиль="быстро", слои=""):
        return self.сеть.client.post(
            "/api/potok", data={"profile": профиль, "strip": слои},
            files={"file": ("запись.bin", данные or цепочка(), "application/octet-stream")})

    def дождаться(self, ид):
        for _ in range(600):
            состояние = self.сеть.client.get(f"/api/potok/{ид}").json()
            if состояние["состояние"] not in ("ждёт", "идёт"):
                return состояние
            time.sleep(0.2)
        self.fail("задание не закончилось")

    def test_весь_путь_через_сервер(self):
        self.сеть.login("engineer")
        ответ = self.загрузить()
        self.assertEqual(200, ответ.status_code, ответ.text)
        ид = ответ.json()["id"]
        состояние = self.дождаться(ид)
        self.assertEqual(4, len(состояние["этапы"]))
        файл = self.сеть.client.get(f"/api/potok/{ид}/stage/4")
        self.assertEqual(200, файл.status_code)
        from urllib.parse import unquote
        self.assertIn("запись-этап-4.pcap", unquote(файл.headers.get("content-disposition", "")))
        продолжение = self.сеть.client.post(f"/api/potok/{ид}/continue",
                                             json={"stage": 1, "strip": "скремблер 3,20",
                                                   "profile": "быстро"})
        self.assertEqual(200, продолжение.status_code, продолжение.text)
        список = self.сеть.client.get("/api/potok").json()["items"]
        self.assertEqual(2, len(список))

    def test_чужое_задание_не_видно_и_профиль_проверяется(self):
        self.сеть.login("engineer")
        ид = self.загрузить().json()["id"]
        self.assertEqual(400, self.загрузить(профиль="всё").status_code)
        self.сеть.login("zam")        # другой военнослужащий, не администратор
        self.assertEqual(404, self.сеть.client.get(f"/api/potok/{ид}").status_code)
        self.assertEqual(404, self.сеть.client.get("/api/potok/../etc").status_code)
        self.assertEqual([], self.сеть.client.get("/api/potok").json()["items"])

    def test_растр_маска_инструменты_производный(self):
        import base64
        self.сеть.login("engineer")
        # E1: цикл 256 бит, в КИ1 — HDLC с IP.
        ряд = np.frombuffer(с.hdlc(с.пакеты_ip(60)), dtype=np.uint8)
        поток = с.e1(len(ряд), каналы=lambda ц, ки: int(ряд[ц]) if ки == 1 else 0xD5)
        ид = self.сеть.client.post(
            "/api/potok", data={"profile": "быстро"},
            files={"file": ("e1.bin", поток, "application/octet-stream")}).json()["id"]
        # Окно бит — те же биты, что в файле.
        окно = self.сеть.client.get(f"/api/potok/{ид}/bits?stage=0&start=8&count=64").json()
        self.assertEqual((len(поток) * 8, 8, 64), (окно["всего"], окно["начало"], окно["бит"]))
        self.assertEqual(в_биты(поток)[8:72].tolist(),
                         np.unpackbits(np.frombuffer(base64.b64decode(окно["данные"]),
                                                     dtype=np.uint8)).tolist())
        # Период: среди кандидатов — цикл E1, 256 бит (и байтовый период
        # заполнения 0xD5 — тоже правда: решает аналитик).
        периоды = self.сеть.client.get(f"/api/potok/{ид}/periods?stage=0").json()["items"]
        self.assertIn(256, [п["период"] for п in периоды])
        # Канал КИ1 — позиции 8…15 цикла: в нём HDLC.
        маска = {"период": 256, "сдвиг": 0, "позиции": list(range(8, 16))}
        найдено = self.сеть.client.post(f"/api/potok/{ид}/tool",
                                        json={"stage": 0, "tool": "кадры", "mask": маска}).json()
        self.assertIn("HDLC", найдено["found"]["что"])
        # Разуплотнить без разбора — поток канала ровно из этих бит.
        новый = self.сеть.client.post(f"/api/potok/{ид}/derive",
                                      json={"stage": 0, "mask": маска}).json()["id"]
        состояние = self.дождаться(новый)
        self.assertEqual(("готово", False), (состояние["состояние"], состояние["разбирать"]))
        self.assertIn("канал по маске: период 256, сдвиг 0, позиции 8–15",
                      состояние["происхождение"][0])
        окно = self.сеть.client.get(f"/api/potok/{новый}/bits?stage=0&start=0&count=80").json()
        self.assertEqual(в_биты(bytes(ряд[:10])).tolist(),
                         np.unpackbits(np.frombuffer(base64.b64decode(окно["данные"]),
                                                     dtype=np.uint8)).tolist())
        # Плохая маска и неизвестный инструмент — 400.
        self.assertEqual(400, self.сеть.client.post(
            f"/api/potok/{ид}/tool", json={"stage": 0, "tool": "кадры",
                                          "mask": {"период": 256, "позиции": []}}).status_code)
        self.assertEqual(400, self.сеть.client.post(
            f"/api/potok/{ид}/tool", json={"stage": 0, "tool": "всё"}).status_code)

    def test_подсказки_и_вопрос_помощнику(self):
        self.сеть.login("engineer")
        ответ = self.загрузить(bytes(np.packbits(с.случайные_биты(1 << 18))))
        ид = ответ.json()["id"]
        self.дождаться(ид)
        данные = self.сеть.client.get(f"/api/potok/{ид}/hints?stage=5").json()
        self.assertEqual(0, данные["stage"], "этапов нет — приметы исходного потока")
        self.assertIn("Спросить помощника",
                      [д["кнопка"] for х in данные["items"] for д in х["действия"]])
        вопрос = self.сеть.client.post(f"/api/potok/{ид}/ask", json={"stage": 0}).json()
        self.assertIn("анализатор цепочки не нашёл", вопрос["question"])
        разговор = self.сеть.client.get(f"/api/chats/{вопрос['chat']['id']}").json()
        self.assertEqual("Разбор потока: запись.bin", разговор["chat"]["title"])
        вложение, = разговор["attachments"]
        self.assertEqual(("stream", f"разбор-потока-{ид}-этап-0.txt"),
                         (вложение["kind"], вложение["name"]))
        текст = self.сеть.repos.chats.attachments(вопрос["chat"]["id"])[0].text
        self.assertIn("Приметы (исходный поток):", текст)
        # Чужой разбор — не найден, и разговор о нём не заводится.
        self.сеть.login("gruppa")
        self.assertEqual(404, self.сеть.client.get(f"/api/potok/{ид}/hints").status_code)
        self.assertEqual(404, self.сеть.client.post(f"/api/potok/{ид}/ask", json={}).status_code)

    def test_вопрос_об_этапе(self):
        """У каждого этапа — свой вопрос помощнику: и у битового, и у кадров, и у пакетов."""
        self.сеть.login("engineer")
        ид = self.загрузить(цепочка()).json()["id"]
        состояние = self.дождаться(ид)
        self.assertEqual(["код", "скремблер", "канальный", "сетевой"],
                         [э["уровень"] for э in состояние["этапы"]])
        for номер, что in ((1, "свёрточный код"), (3, "HDLC"), (4, "пакеты IP")):
            with self.subTest(этап=номер):
                вопрос = self.сеть.client.post(f"/api/potok/{ид}/ask",
                                               json={"stage": номер, "topic": "этап"}).json()
                self.assertIn(f"На этапе {номер} разбора анализатор нашёл: {что}", вопрос["question"])
                текст = self.сеть.repos.chats.attachments(вопрос["chat"]["id"])[0].text
                self.assertIn(f"Этап {номер} целиком: [", текст)
                self.assertIn("Цепочка, найденная анализатором:", текст)
        # Все подробности этапа — во вложении, а не первые четыре.
        э = состояние["этапы"][2]
        вопрос = self.сеть.client.post(f"/api/potok/{ид}/ask", json={"stage": 3, "topic": "этап"}).json()
        текст = self.сеть.repos.chats.attachments(вопрос["chat"]["id"])[0].text
        for деталь in э["подробно"]:
            self.assertIn(деталь, текст)
        # Этапа нет — вопрос общий («разбор встал»).
        вопрос = self.сеть.client.post(f"/api/potok/{ид}/ask", json={"stage": 9, "topic": "этап"}).json()
        self.assertIn("Разбор неизвестного потока встал", вопрос["question"])

    def test_синхро_через_сервер(self):
        self.сеть.login("engineer")
        данные = с.случайные_биты(400 * 1500, сид=7).reshape(1500, 400)
        данные[:, :32] = sinhro.слово("0x1ACFFC1D")
        поток = bytes(np.packbits(np.concatenate([np.zeros(3, np.uint8), 1 - данные.reshape(-1)])))
        ид = self.загрузить(поток).json()["id"]
        self.дождаться(ид)
        н = self.сеть.client.post(f"/api/potok/{ид}/sync",
                                  json={"stage": 0, "word": "0x1ACFFC1D"}).json()
        self.assertEqual((True, 1500, 400, 3), (н["инверсия"], н["инверсных"], н["длина_кадра"],
                                                н["начало"]))
        # Из выделенных столбцов растра — то же слово (инверсное: поток перевёрнут).
        н = self.сеть.client.post(f"/api/potok/{ид}/sync", json={
            "stage": 0, "columns": {"период": 400, "сдвиг": 3, "позиции": list(range(32))}}).json()
        self.assertEqual("0xE53003E2 (11100101001100000000001111100010)", н["слово"])
        self.assertEqual(400, н["длина_кадра"])
        self.assertEqual(400, self.сеть.client.post(f"/api/potok/{ид}/sync",
                                                    json={"word": "0xZZ"}).status_code)
        # Вопрос помощнику — о синхрокомбинации, её описание — во вложении.
        вопрос = self.сеть.client.post(f"/api/potok/{ид}/ask",
                                       json={"stage": 0, "word": "0x1ACFFC1D"}).json()
        self.assertIn("синхрокомбинация 0x1ACFFC1D", вопрос["question"])
        self.assertIn("повторяется через 400 бит, приходит инверсной", вопрос["question"])
        текст = self.сеть.repos.chats.attachments(вопрос["chat"]["id"])[0].text
        self.assertIn("Синхрокомбинация:\nсинхрокомбинация 0x1ACFFC1D (000110101100111111111100"
                      "00011101), 32 бит, ошибок до 0: прямых 0, инверсных 1500", текст)
        # Новый поток от синхро — ровно кадры, уже не инверсные.
        новый = self.сеть.client.post(f"/api/potok/{ид}/derive", json={
            "stage": 0, "strip": "синхро 0x1ACFFC1D"}).json()["id"]
        self.дождаться(новый)
        import base64
        окно = self.сеть.client.get(f"/api/potok/{новый}/bits?stage=0&start=0&count=400").json()
        self.assertEqual(данные[0].tolist(), np.unpackbits(np.frombuffer(
            base64.b64decode(окно["данные"]), dtype=np.uint8)).tolist())

    def test_дерево_удаление_и_пересборка(self):
        import base64
        к = self.сеть.client
        self.сеть.login("engineer")
        данные = bytes(range(256)) * 40
        корень = self.загрузить(данные).json()["id"]
        self.дождаться(корень)

        def биты(ид):
            окно = к.get(f"/api/potok/{ид}/bits?stage=0&start=0&count=4096").json()
            return np.unpackbits(np.frombuffer(base64.b64decode(окно["данные"]), dtype=np.uint8))[:окно["бит"]]

        # Узел A: канал по маске; узел B из A: инверсия и сдвиг.
        маска = {"период": 16, "сдвиг": 0, "позиции": list(range(8))}
        а = к.post(f"/api/potok/{корень}/derive", json={"stage": 0, "steps": [
            {"вид": "маска", "маска": маска}]}).json()["id"]
        self.дождаться(а)
        б = к.post(f"/api/potok/{а}/derive", json={"stage": 0, "steps": [
            {"вид": "слой", "слой": "инверсия"}, {"вид": "слой", "слой": "сдвиг 3"}]}).json()["id"]
        состояние = self.дождаться(б)
        self.assertEqual("готово", состояние["состояние"])
        исходные = в_биты(данные)
        канал = исходные[:len(исходные) // 16 * 16].reshape(-1, 16)[:, :8].reshape(-1)
        self.assertEqual((1 - канал[3:])[:1000].tolist(), биты(б)[:1000].tolist())
        # Дерево видно из любого узла: корень → A → B.
        дерево = к.get(f"/api/potok/{б}/tree").json()["tree"]
        self.assertEqual(корень, дерево["ид"])
        self.assertEqual([а], [д["ид"] for д in дерево["дети"]])
        self.assertEqual(0, дерево["дети"][0]["этап_родителя"])
        self.assertEqual([б], [д["ид"] for д in дерево["дети"][0]["дети"]])
        # Пересобрать B: инверсию выключить, шаги переставить — рядом, без замены.
        в = к.post(f"/api/potok/{б}/rebuild", json={"steps": [
            {"вид": "слой", "слой": "сдвиг 3"},
            {"вид": "слой", "слой": "инверсия", "вкл": False}]}).json()["id"]
        self.дождаться(в)
        self.assertEqual(канал[3:1003].tolist(), биты(в)[:1000].tolist())
        дети_а = к.get(f"/api/potok/{корень}/tree").json()["tree"]["дети"][0]["дети"]
        self.assertEqual({б, в}, {д["ид"] for д in дети_а})
        # С заменой: старый узел уходит, новый встаёт под того же родителя.
        г = к.post(f"/api/potok/{в}/rebuild", json={"steps": [
            {"вид": "слой", "слой": "сдвиг 5"}], "replace": True}).json()["id"]
        self.дождаться(г)
        self.assertEqual(404, к.get(f"/api/potok/{в}").status_code)
        self.assertEqual(f"{а}#0", к.get(f"/api/potok/{г}").json()["от"])
        # Удалить A — уходит вся ветвь, корень остаётся.
        удалены = к.delete(f"/api/potok/{а}").json()["deleted"]
        self.assertEqual({а, б, г}, set(удалены))
        self.assertEqual([], к.get(f"/api/potok/{корень}/tree").json()["tree"]["дети"])
        # Плохой шаг и чужой узел.
        self.assertEqual(400, к.post(f"/api/potok/{корень}/derive", json={"steps": [
            {"вид": "маска", "маска": {"период": 8, "позиции": []}}]}).status_code)
        ошибка = к.post(f"/api/potok/{корень}/derive", json={"steps": [
            {"вид": "слой", "слой": "непонятно"}]}).json()["id"]
        self.assertEqual("ошибка", self.дождаться(ошибка)["состояние"])
        self.сеть.login("gruppa")
        self.assertEqual(404, к.delete(f"/api/potok/{корень}").status_code)
        self.assertEqual(404, к.get(f"/api/potok/{корень}/tree").status_code)

    def test_страница_в_интерфейсе(self):
        js = (КОРЕНЬ / "src" / "reportgen" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        # Своего пункта в меню у страницы разборов нет (автоанализ — на столе), адрес #/potok остался.
        self.assertNotIn("title: 'Разбор потока'", js)
        self.assertIn("href: '#/potok', title: 'Отдельные разборы без сессии'", js)
        self.assertIn("else if (route.name === 'potok') await renderPotok(view, route.id);", js)
        self.assertIn("остановитьОпросПотока();", js)
        self.assertIn("'/api/potok/' + encodeURIComponent(jobId) + '/continue'", js)
        # Растр: 0 — чёрный, 1 — зелёный; маски, инструменты, производный поток.
        self.assertIn("const РАСТР_0 = [0, 0, 0];", js)
        self.assertIn("const РАСТР_1 = [0, 200, 83];", js)
        for кусок in ("'Поиск периода'", "'Сохранить выделение как канал'", "'Демультиплексировать'",
                      "'/derive'", "'/tool'", "'/periods?stage='", "'HEX', 'DEC'",
                      "'/hints?stage='", "path + '/ask'".replace("path", "путь"),
                      "saveDraft(data.chat.id, data.question);", "'Что дальше?'",
                      "'/sync'", "'Засинхронизировать'", "'Выровнять кадры'", "'Из выделения'",
                      "'/tree'", "'/rebuild'", "'Пересобрать и заменить'", "'Дерево обработки — узлов: '",
                      "'Найти ПСП и начальное состояние'", "'/api/potok-matrices'",
                      "{ stage: этап.номер, topic: 'этап' }", "спроситьОбЭтапе(этап, event.currentTarget)"):
            self.assertIn(кусок, js)


if __name__ == "__main__":
    unittest.main()
