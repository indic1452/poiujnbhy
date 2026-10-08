"""Анализатор цифровых потоков — на потоках с ИЗВЕСТНЫМ ответом.

Слепой разбор иначе не проверить: собираем поток, строение которого
известно до бита (potok_sintez), и сверяем, что анализатор нашёл ровно его
— и ничего не «нашёл» в случайном потоке. Отдел работает с неизвестными
потоками, и ложная находка там опаснее пропущенной: по ней строят вывод.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import (
    cikl,
    dvb,
    hdlc,
    kod,
    oktety,
    pakety,
    pdh,
    peremezhenie,
    skrembler,
    разобрать,
)
from reportgen.potok.bity import в_байты, в_биты, из_текста
from reportgen.potok.chtenie import прочитать, разобрать_sig
from reportgen.potok.statistika import посчитать

СЛУЧАЙНЫЕ = с.случайные_биты(600_000, сид=11)


def ошибки(биты, доля, сид=2):
    return биты ^ (np.random.default_rng(сид).random(len(биты)) < доля).astype(np.uint8)


class ЧтениеTests(unittest.TestCase):
    def test_sig_во_всех_толкованиях_заголовка(self):
        пакеты = [bytes([i % 256]) * (50 + i % 90) for i in range(200)]
        for порядок in ("big", "little"):
            for включает in (False, True):
                for шапка in (b"", b"SIG1"):
                    with self.subTest(порядок=порядок, включает=включает, шапка=шапка):
                        поток = прочитать(данные=с.sig(пакеты, порядок=порядок,
                                                        включает=включает,
                                                        заголовок_файла=шапка),
                                          имя="запись.Sig")
                        self.assertEqual("sig", поток.вид)
                        self.assertEqual(200, len(поток.пакеты))
                        self.assertEqual(пакеты[7], поток.пакет(7))
                        self.assertIn("старший" if порядок == "big" else "младший", поток.формат)
                        self.assertIn("включая" if включает else "без", поток.формат)

    def test_dpo_то_же_что_sig(self):
        # .dpo — тот же формат «двухбайтовая длина + пакет»; регистр расширения не важен.
        пакеты = [bytes([i % 256]) * (40 + i % 70) for i in range(120)]
        for имя in ("запись.dpo", "ЗАПИСЬ.DPO", "запись.Sig"):
            with self.subTest(имя=имя):
                поток = прочитать(данные=с.sig(пакеты), имя=имя)
                self.assertEqual(("sig", 120, пакеты[5]), (поток.вид, len(поток.пакеты), поток.пакет(5)))
        # Не проходится разметкой — сырой поток, и заметка называет расширение файла.
        поток = прочитать(данные=np.random.default_rng(3).bytes(50_000), имя="x.dpo")
        self.assertEqual("bin", поток.вид)
        self.assertTrue(поток.заметки[0].startswith("файл назван .dpo, но разметкой"), поток.заметки)
        # Прочие расширения разметкой .Sig не читаются.
        self.assertEqual("bin", прочитать(данные=с.sig(пакеты), имя="запись.dat").вид)

    def test_не_sig_читается_как_сырой_и_об_этом_сказано(self):
        поток = прочитать(данные=np.random.default_rng(3).bytes(50_000), имя="x.sig")
        self.assertEqual("bin", поток.вид)
        self.assertTrue(поток.заметки)
        self.assertIsNone(разобрать_sig(np.random.default_rng(4).bytes(20_000)))

    def test_биты_и_шестнадцатеричный_текстом(self):
        биты = с.случайные_биты(800)
        self.assertTrue(np.array_equal(биты, из_текста("".join(map(str, биты)))))
        self.assertTrue(np.array_equal(в_биты(bytes(range(40))),
                                       из_текста(bytes(range(40)).hex(" "))))
        self.assertIsNone(из_текста("Поток E1 передаётся со скоростью 2048 кбит/с " * 3))
        поток = прочитать(данные=("01" * 400).encode(), имя="поток.bits")
        self.assertEqual("текст", поток.вид)


class СтатистикаTests(unittest.TestCase):
    def test_случайный_и_упорядоченный(self):
        случайный = посчитать(np.random.default_rng(1).bytes(200_000))
        self.assertGreater(случайный.энтропия, 7.99)
        self.assertIn("случайный", случайный.вывод())
        e1 = посчитать(с.e1(2000))
        self.assertLess(e1.энтропия, 7.5)
        self.assertIn((0xD5 in dict(e1.частые)), (True,))

    def test_повторяющееся_слово_и_шаг(self):
        пакеты = b"".join(b"\x47\x1f\xff\x10" + bytes(184) for _ in range(300))
        слова = посчитать(пакеты).повторы
        self.assertIn(("471FFF10", 188), [(слово, шаг) for слово, шаг, _ in слова])


class HdlcTests(unittest.TestCase):
    def test_кадры_восстановлены_при_любом_порядке_и_fcs(self):
        кадры = с.пакеты_ip(120)
        for fcs in (16, 32):
            for порядок in ("старший", "младший"):
                with self.subTest(fcs=fcs, порядок=порядок):
                    найдено = hdlc.найти(в_биты(с.hdlc(кадры, fcs=fcs, порядок=порядок)))
                    self.assertIsNotNone(найдено)
                    self.assertEqual(f"FCS-{fcs}", найдено.что.rsplit(", ", 1)[1])
                    self.assertEqual(кадры, найдено.дальше)
                    self.assertEqual(1.0, найдено.уверенность)

    def test_обратная_полярность(self):
        биты = в_биты(с.hdlc(с.пакеты_ip(60)))
        найдено = hdlc.найти((1 - биты).astype(np.uint8))
        self.assertIsNotNone(найдено)
        self.assertIn("полярность обратная", " ".join(найдено.подробно))

    def test_снятие_вставки(self):
        ряд = np.array([1, 1, 1, 1, 1, 0, 1, 0, 1, 1, 1, 1, 1, 0, 0], dtype=np.uint8)
        self.assertEqual([1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0],
                         hdlc.снять_вставку(ряд).tolist())

    def test_в_случайном_hdlc_нет(self):
        self.assertIsNone(hdlc.найти(СЛУЧАЙНЫЕ))


class ПакетыTests(unittest.TestCase):
    def test_ip_в_кадрах_по_контрольной_сумме(self):
        найдено = pakety.найти_в_кадрах(с.пакеты_ip(90))
        self.assertEqual(1.0, найдено.уверенность)
        текст = " ".join(найдено.подробно)
        self.assertIn("UDP×60", текст)
        self.assertIn("53 (DNS)", текст)

    def test_испорченная_сумма_не_ip(self):
        пакет = bytearray(с.пакеты_ip(1)[0])
        пакет[11] ^= 1
        self.assertIsNone(pakety.ipv4(bytes(пакет)))

    def test_esp_spi_и_пропуски_номеров(self):
        """ESP (RFC 4303): SPI и номер последовательности открыты — по ним сеансы и пропуски."""
        import struct
        пакеты = [с.ipv4("92.0.1.85", "92.0.1.83", 50, struct.pack(">II", 0x0A0B0C0D, n) + bytes(40))
                  for n in (1, 2, 3, 5, 6)]
        пакеты += [с.ipv4("92.0.1.83", "92.0.1.85", 50, struct.pack(">II", 0xCAFEBABE, n) + bytes(24))
                   for n in (10, 11)]
        текст = " ".join(pakety.найти_в_кадрах(пакеты).подробно)
        self.assertIn("ESP×7", текст)
        self.assertIn("нагрузка зашифрована", текст)
        self.assertIn("SPI 0x0a0b0c0d — 5 пакетов, номера 1…6, пропущено 1", текст)
        self.assertIn("SPI 0xcafebabe — 2 пакетов, номера 10…11, без пропусков", текст)
        без = " ".join(pakety.найти_в_кадрах(с.пакеты_ip(20)).подробно)
        self.assertNotIn("ESP", без)

    def test_обёртки_ppp_и_ethernet(self):
        ip = с.пакеты_ip(30)
        ppp = pakety.найти_в_кадрах([b"\xff\x03\x00\x21" + x for x in ip])
        self.assertIn("PPP×30", " ".join(ppp.подробно))
        eth = pakety.найти_в_кадрах([bytes(12) + b"\x08\x00" + x for x in ip])
        self.assertIn("Ethernet×30", " ".join(eth.подробно))

    def test_цепочка_в_сплошном_потоке(self):
        найдено = pakety.найти_в_потоке(b"\x13" * 7 + b"".join(с.пакеты_ip(40)))
        self.assertEqual(40, len(найдено.дальше))

    def test_в_случайном_ip_нет(self):
        self.assertIsNone(pakety.найти_в_потоке(в_байты(СЛУЧАЙНЫЕ)))
        случайные = [np.random.default_rng(i).bytes(120) for i in range(200)]
        self.assertIsNone(pakety.найти_в_кадрах(случайные))


class ЦиклTests(unittest.TestCase):
    def test_e1_при_любом_сдвиге(self):
        биты = в_биты(с.e1(4000))
        for сдвиг in (0, 3, 117):
            with self.subTest(сдвиг=сдвиг):
                найдено = cikl.найти(биты[сдвиг:])
                self.assertIn("E1 по G.704", найдено.что)
                self.assertEqual(1.0, найдено.уверенность)
                self.assertEqual(32, len(найдено.дальше))

    def test_cas_и_каналы(self):
        найдено = cikl.найти(в_биты(с.e1(4000)))
        текст = "\n".join(найдено.подробно)
        self.assertIn("сверхцикл из 16 циклов", текст)
        self.assertIn("КИ25–КИ31: постоянное заполнение 0xD5", текст)
        self.assertIn("КИ1–КИ15, КИ17–КИ24", текст)

    def test_hdlc_в_канальном_интервале(self):
        ряд = np.frombuffer(с.hdlc(с.пакеты_ip(80)), dtype=np.uint8)
        поток = с.e1(len(ряд), каналы=lambda ц, ки: int(ряд[ц]) if ки == 1 else 0xD5)
        разбор = разобрать(данные=поток, имя="e1.bin")
        виды = [находка.что for находка in разбор.находки]
        self.assertTrue(any("HDLC" in что and "КИ1" in что for что in виды), виды)
        self.assertTrue(any("пакеты IP" in что and "КИ1" in что for что in виды), виды)

    def test_неизвестный_цикл_и_синхрослово(self):
        rng = np.random.default_rng(1)
        циклы = rng.integers(0, 2, (8000, 200), dtype=np.uint8)
        слово = [1, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0]
        циклы[:, 40:52] = слово
        найдено = cikl.найти(циклы.reshape(-1)[13:])
        self.assertIn("цикл 200 бит", найдено.что)
        self.assertIn("111010110010", " ".join(найдено.подробно))

    def test_в_случайном_цикла_нет(self):
        self.assertIsNone(cikl.найти(СЛУЧАЙНЫЕ))

    def test_редкие_единицы_не_цикл(self):
        # Простаивающая линия с редкими случайными единицами: при любой длине
        # «цикла» столбцы почти постоянны, но периода нет — пик автокорреляции
        # в пределах шума, и это решает порог в сигмах.
        редкие = (np.random.default_rng(5).random(1 << 22) < 0.01).astype(np.uint8)
        self.assertIsNone(cikl.найти(редкие))


class СкремблерTests(unittest.TestCase):
    ИСХОДНЫЙ = в_биты(с.hdlc(с.пакеты_ip(260), флагов_между=4))[:400_000]

    def test_мультипликативные_и_снятие(self):
        for отводы in ((3, 20), (43,), (14, 17)):
            with self.subTest(отводы=отводы):
                найдено = skrembler.найти(с.скремблировать(self.ИСХОДНЫЙ, отводы))
                self.assertIn("мультипликативный", найдено.что)
                self.assertIn(skrembler.полином_словами(отводы), найдено.что)
                self.assertEqual(1.0, hdlc.найти(найдено.дальше).уверенность)

    def test_аддитивная_псп_с_наименьшим_полиномом(self):
        исходный = в_биты(с.hdlc(с.пакеты_ip(120), флагов_между=60))[:400_000]
        ряд = skrembler.псп((14, 15), np.ones(15, dtype=np.uint8), len(исходный))
        найдено = skrembler.найти(исходный ^ ряд)
        self.assertEqual("аддитивная ПСП 1 + x^-14 + x^-15", найдено.что)
        self.assertEqual(1.0, hdlc.найти(найдено.дальше).уверенность)

    def test_берлекэмп_мэсси(self):
        ряд = skrembler.псп((3, 20), np.ones(20, dtype=np.uint8), 300)
        сложность, полином = skrembler.берлекэмп_мэсси(ряд.tolist())
        self.assertEqual(20, сложность)
        self.assertEqual((1 << 0) | (1 << 3) | (1 << 20), полином)

    def test_без_отрыва_от_толпы_не_засчитывается(self):
        # Лучший кандидат «улучшает» поток, но на малой выборке не отличается от
        # двух тысяч прочих — это шум, а не найденный полином.
        from unittest import mock
        шум = np.random.default_rng(7)
        полные = iter([5.0, 4.0, 4.0, 4.0, 4.0, 4.0] + [1.0] * 100)
        with mock.patch.object(skrembler, "мера_структуры",
                               side_effect=lambda ряд: float(шум.normal(1.0, 0.01))), \
                mock.patch.object(skrembler, "мера_полная",
                                  side_effect=lambda ряд: next(полные)), \
                mock.patch.object(skrembler, "аддитивный", return_value=None):
            self.assertIsNone(skrembler.найти(СЛУЧАЙНЫЕ))

    def test_ложных_находок_нет(self):
        self.assertIsNone(skrembler.найти(СЛУЧАЙНЫЕ))
        self.assertIsNone(skrembler.найти(self.ИСХОДНЫЙ),
                          "в нескремблированном потоке «нашёлся» скремблер")


class КодTests(unittest.TestCase):
    def test_хэмминг_при_любом_выравнивании_и_данные(self):
        данные = с.случайные_биты(40_000)
        код = с.хэмминг74(данные)
        for сдвиг in (0, 3):
            with self.subTest(сдвиг=сдвиг):
                найдено = kod.найти(код[сдвиг:])
                self.assertIn("(7, 4)", найдено.что)
                начало = (сдвиг + 6) // 7 * 4
                self.assertTrue(np.array_equal(найдено.дальше[:4000],
                                               данные[начало:начало + 4000]))

    def test_блочные_с_ошибками_и_без(self):
        rng = np.random.default_rng(9)
        for n, k in ((15, 11), (16, 8), (12, 6)):
            P = rng.integers(0, 2, (k, n - k), dtype=np.uint8)
            d = с.случайные_биты(k * 3000).reshape(-1, k)
            код = np.hstack([d, (d.astype(int) @ P % 2).astype(np.uint8)]).reshape(-1)
            for доля in (0, 0.01):
                with self.subTest(n=n, k=k, ошибки=доля):
                    найдено = kod.найти(ошибки(код[5:], доля))
                    self.assertIn(f"({n}, {k})", найдено.что)

    def test_длинный_блочный_без_ошибок(self):
        rng = np.random.default_rng(9)
        P = rng.integers(0, 2, (21, 10), dtype=np.uint8)
        d = с.случайные_биты(21 * 3000).reshape(-1, 21)
        код = np.hstack([d, (d.astype(int) @ P % 2).astype(np.uint8)]).reshape(-1)
        найдено = kod.найти(код)
        self.assertIn("(31, 21)", найдено.что)
        self.assertIn("параметры как у кода БЧХ (31, 21)", найдено.что)

    def test_свёрточные_с_ошибками_и_сдвигом(self):
        for g1, g2, K, имя in ((0o171, 0o133, 7, "171/133"), (0o7, 0o5, 3, "7/5")):
            данные = с.случайные_биты(40_000)
            код = с.свёрточный(данные, g1, g2, K)
            for сдвиг, доля in ((0, 0), (1, 0.02)):
                with self.subTest(код=имя, сдвиг=сдвиг, ошибки=доля):
                    найдено = kod.найти(ошибки(код[сдвиг:], доля))
                    self.assertIn(f"K={K}, {имя}", найдено.что)
                    начало = сдвиг
                    верно = np.mean(найдено.дальше[:10_000] == данные[начало:начало + 10_000])
                    self.assertGreater(верно, 0.99)

    def test_mpeg_ts(self):
        пакеты = b"".join((b"\xb8" if номер % 8 == 0 else b"\x47") + bytes(203)
                          for номер in range(400))
        найдено = kod.mpeg_ts(b"\x00" * 50 + пакеты)
        self.assertEqual("MPEG-TS, пакеты по 204 байт", найдено.что)
        текст = " ".join(найдено.подробно)
        self.assertIn("(204, 188", текст)
        self.assertIn("0xB8", текст)

    def test_в_случайном_кода_нет(self):
        self.assertIsNone(kod.найти(СЛУЧАЙНЫЕ))


class ОктетныйСтаффингTests(unittest.TestCase):
    ПАКЕТЫ = с.пакеты_ip(150)

    def test_снятие_экранирования(self):
        self.assertEqual((b"\x7e\x41\x7d\x03", 3),
                         oktety.снять_экранирование(b"\x7d\x5e\x41\x7d\x5d\x7d\x23"))
        self.assertIsNone(oktety.снять_экранирование(b"\x41\x7d"), "экран в конце — кадр оборван")

    def test_асинхронный_ppp_при_любом_сдвиге_и_fcs(self):
        кадры = [b"\xff\x03\x00\x21" + пакет for пакет in self.ПАКЕТЫ]
        for fcs in (16, 32):
            for сдвиг in (0, 5):
                with self.subTest(fcs=fcs, сдвиг=сдвиг):
                    биты = в_биты(b"\x00" * 3 + с.hdlc_асинхронный(кадры, fcs=fcs))
                    найдено = oktety.найти(np.concatenate([np.zeros(сдвиг, np.uint8), биты]))
                    self.assertIn(f"октетным стаффингом (0x7D), FCS-{fcs}", найдено.что)
                    self.assertEqual(1.0, найдено.уверенность)
                    self.assertEqual(кадры, найдено.дальше)
                    self.assertIn(f"с бита {сдвиг}", найдено.подробно[0])
                    self.assertIn("PPP", " ".join(найдено.подробно))

    def test_младший_бит_первым(self):
        биты = в_биты(с.hdlc_асинхронный(self.ПАКЕТЫ), "младший")
        найдено = oktety.найти(биты)
        self.assertIn("младший первым", найдено.подробно[0])
        self.assertEqual(list(self.ПАКЕТЫ), найдено.дальше)

    def test_slip_по_пакетам_ip(self):
        найдено = oktety.найти(в_биты(с.slip(self.ПАКЕТЫ)))
        self.assertIn("SLIP", найдено.что)
        self.assertEqual(list(self.ПАКЕТЫ), найдено.дальше)

    def test_в_случайном_и_в_синхронном_hdlc_нет(self):
        self.assertIsNone(oktety.найти(СЛУЧАЙНЫЕ))
        # Синхронное HDLC — битстаффинг, не октетный: FCS после «снятия 0x7D»
        # не сходится, и это решает.
        self.assertIsNone(oktety.найти(в_биты(с.hdlc(self.ПАКЕТЫ))))


class PdhTests(unittest.TestCase):
    E1 = [в_биты(с.e1(2100, сид=номер)) for номер in range(4)]
    ДОЛИ = [0.42, 0.30, 0.55, 0.43]

    def test_разметка_по_стандарту(self):
        # Число бит притока в цикле: данные + возможность стаффинга. У E2
        # 205 + 1, у E3 377 + 1, у E4 722 + 1 (G.742, G.751).
        for иерархия, данных in zip(pdh.ИЕРАРХИИ, (205, 377, 722), strict=False):
            with self.subTest(иерархия.имя):
                данные, cj, стаффинг = pdh.разметка(иерархия)
                self.assertEqual({данных}, {len(данные[t]) for t in range(4)})
                self.assertEqual({иерархия.повторов_cj}, {len(cj[t]) for t in range(4)})
                все = sum(данные.values(), []) + sum(cj.values(), []) + list(стаффинг.values())
                self.assertEqual(len(все), len(set(все)), "позиции пересекаются")
                self.assertEqual(иерархия.цикл - иерархия.служебных, len(все))
                self.assertEqual(set(range(иерархия.служебных, иерархия.цикл)), set(все))

    def test_e2_притоки_бит_в_бит_и_скорость(self):
        агрегат = с.pdh(self.E1, pdh.ИЕРАРХИИ[0], 2400, self.ДОЛИ)
        найдено = pdh.найти(np.concatenate([np.zeros(301, np.uint8), агрегат]))
        self.assertIn("PDH E2 (G.742): 4 × E1", найдено.что)
        self.assertEqual(1.0, найдено.уверенность)
        for номер, приток in найдено.дальше.items():
            with self.subTest(приток=номер):
                исходный = self.E1[номер - 1]
                self.assertGreater(len(приток), 480_000)
                self.assertTrue(np.array_equal(исходный[:len(приток)], приток))
                self.assertIn("E1 по G.704", cikl.найти(приток).что)
        текст = " ".join(найдено.подробно)
        for номер, доля in enumerate(self.ДОЛИ, start=1):
            self.assertIn(f"приток {номер}: стаффинг в {доля * 100:.1f} % циклов", текст)
        # 42 % стаффинга — почти номинал 2048 кбит/с (номинальная доля 0,423).
        self.assertIn("приток 1: стаффинг в 42.0 % циклов → скорость 2048.0", текст)

    def test_ошибка_в_одном_cj_не_сбивает(self):
        иерархия = pdh.ИЕРАРХИИ[0]
        агрегат = с.pdh(self.E1, иерархия, 2400, self.ДОЛИ)
        _, cj, _ = pdh.разметка(иерархия)
        for повтор in range(3):
            with self.subTest(испорчен=f"Cj{повтор + 1}"):
                испорченный = агрегат.copy()
                циклы = np.arange(повтор, 2400, 7)
                испорченный[циклы * иерархия.цикл + cj[2][повтор]] ^= 1   # приток 3
                приток = pdh.найти(испорченный).дальше[3]
                self.assertTrue(np.array_equal(self.E1[2][:len(приток)], приток),
                                "одиночная ошибка Cj должна исправляться большинством")

    def test_разметка_e2_по_номерам_бит_g742(self):
        # Сверка не с генератором (он берёт разметку у анализатора), а с
        # номерами бит стандарта (счёт с нуля): набор I — FAS и служебные
        # биты 0…11, с бита 12 — биты притоков 1, 2, 3, 4 по очереди; набор II
        # начинается с бита 212 битами Cj1 притоков 1…4; в наборе IV (с бита
        # 636) — Cj3 в битах 636…639 и биты стаффинга 640…643.
        данные, cj, стаффинг = pdh.разметка(pdh.ИЕРАРХИИ[0])
        self.assertEqual([12, 16, 20, 24], данные[0][:4])
        self.assertEqual([13, 17, 21, 25], данные[1][:4])
        self.assertEqual([212, 424, 636], cj[0])
        self.assertEqual([215, 427, 639], cj[3])
        self.assertEqual({0: 640, 1: 641, 2: 642, 3: 643}, стаффинг)
        self.assertEqual(644, min(п for п in данные[0] if п >= 636))

    def test_в_случайном_и_в_e1_нет(self):
        self.assertIsNone(pdh.найти(СЛУЧАЙНЫЕ))
        self.assertIsNone(pdh.найти(в_биты(с.e1(3000))))


class ПеремежениеTests(unittest.TestCase):
    ИСХОДНЫЙ = в_биты(с.hdlc(с.пакеты_ip(1200), флагов_между=4))
    КОД = с.хэмминг74(ИСХОДНЫЙ)

    def test_глубина_и_порядок_восстановлены(self):
        # 3 и 6: кратная глубина тоже даёт код (строка в переставленном
        # порядке); 10: делитель 5 даёт «код (14, 8)» — выбрать надо истинную.
        for R in (3, 6, 10, 23):
            with self.subTest(R=R):
                поток = с.перемежить_блочно(self.КОД, R, 7)[11:]
                найдено = peremezhenie.найти(поток)
                self.assertIn(f"блочное перемежение глубиной {R}, в строках — линейный "
                              f"блочный (7, 4)", найдено.что)
                self.assertEqual(1.0, hdlc.найти(найдено.дальше).уверенность,
                                 "после обратного перемежения кадры HDLC должны сойтись")

    def test_ложных_находок_нет(self):
        self.assertIsNone(peremezhenie.найти(СЛУЧАЙНЫЕ))
        self.assertIsNone(peremezhenie.найти(self.КОД[:900_000]), "код без перемежения")
        self.assertIsNone(peremezhenie.найти(self.ИСХОДНЫЙ), "данные без кода")
        текст = в_биты(("Проверка связи, приём. " * 40000).encode())
        self.assertIsNone(peremezhenie.найти(текст), "периодический поток — не код")
        self.assertIsNone(peremezhenie.найти(в_биты(с.e1(4000))))


class DvbTests(unittest.TestCase):
    def test_эталоны_стандарта(self):
        # Сверка не с собой, а со стандартом: первые байты ПСП рандомизации
        # DVB и порождающий многочлен RS(255, 239) с корнями λ⁰…λ¹⁵.
        self.assertEqual([0x03, 0xF6, 0x08], dvb.псп(3).tolist())
        self.assertEqual([1, 59, 13, 104, 189, 68, 209, 30, 8, 163, 65, 41, 229, 98, 50, 36, 59],
                         dvb.ПОРОЖДАЮЩИЙ)

    def test_синдромы(self):
        слово = dvb.закодировать(np.arange(188, dtype=np.uint8))
        self.assertTrue(dvb.синдромы_нулевые(слово)[0])
        испорченное = слово.copy()
        испорченное[0, 100] ^= 0x10
        self.assertFalse(dvb.синдромы_нулевые(испорченное)[0])

    def test_перемежение_снимается_и_данные_восстановлены(self):
        for перемежать in (True, False):
            with self.subTest(перемежать=перемежать):
                данные = b"\x11" * 37 + с.dvb(800, перемежать=перемежать)
                найдено = dvb.найти(данные, 37)
                self.assertEqual(перемежать, "перемежением I = 12, M = 17" in найдено.что)
                self.assertEqual(1.0, найдено.уверенность)
                пакеты = найдено.дальше
                self.assertTrue(np.all(пакеты[:, 0] == 0x47))
                # PID после снятия рандомизации — ровно те, что были заложены.
                pid = set((((пакеты[:, 1] & 0x1F).astype(int) << 8) | пакеты[:, 2]).tolist())
                self.assertEqual({0x100, 0x101, 0x1FFF}, pid)

    def test_запись_с_середины_группы_из_восьми(self):
        # Рандомизация сбрасывается на пакете с 0xB8; запись может начаться
        # с любого из восьми — снимать надо от первого инвертированного.
        данные = с.dvb(800, перемежать=False)[3 * dvb.ДЛИНА:]
        пакеты = dvb.найти(данные, 0).дальше
        pid = set((((пакеты[:, 1] & 0x1F).astype(int) << 8) | пакеты[:, 2]).tolist())
        self.assertEqual({0x100, 0x101, 0x1FFF}, pid)

    def test_не_dvb(self):
        пакеты = b"".join(b"\x47" + bytes(np.random.default_rng(н).integers(0, 256, 203,
                                                                            dtype=np.uint8))
                          for н in range(300))
        self.assertIsNone(dvb.найти(пакеты, 0))


class КодыГраницыTests(unittest.TestCase):
    def test_свёрточный_до_7_процентов_ошибок(self):
        данные = с.случайные_биты(60_000)
        код = с.свёрточный(данные)
        for доля in (0.05, 0.07):
            with self.subTest(доля=доля):
                найдено = kod.найти(ошибки(код, доля, сид=3))
                self.assertIn("171/133", найдено.что)

    def test_корреляция_текста_не_код(self):
        текст = в_биты(("Проверка связи, приём. " * 20000).encode())
        self.assertIsNone(kod.свёрточный(текст))

    def test_ветвь_из_нулей_не_код(self):
        # Вторая ветвь пуста: «связь» есть, перекодирование даже сходится, но
        # кода нет — многочлен ветви нулевой.
        пары = np.column_stack([с.случайные_биты(60_000), np.zeros(60_000, np.uint8)])
        self.assertIsNone(kod.свёрточный(пары.reshape(-1)))

    def test_код_лишь_в_части_выборки_не_засчитывается(self):
        # Связь сильна, но перекодированный поток сходится с принятым лишь на
        # закодированной трети: толкование не подтверждается потоком.
        код = с.свёрточный(с.случайные_биты(40_000))
        поток = np.concatenate([код, с.случайные_биты(180_000, сид=9)])
        self.assertIsNone(kod.свёрточный(поток))

    def test_систематический_свёрточный_признаётся(self):
        найдено = kod.свёрточный(с.свёрточный(с.случайные_биты(60_000), 0o4, 0o7, 3))
        self.assertIn("K=3, 4/7", найдено.что)

    def test_хэмминг_не_принят_за_свёрточный(self):
        код = с.хэмминг74(с.случайные_биты(60_000))
        self.assertIn("(7, 4)", kod.найти(код).что)


class ЦепочкаTests(unittest.TestCase):
    def test_код_скремблер_hdlc_ip_с_ошибками_линии(self):
        """Вся цепочка, вслепую: IP → HDLC → V.35 → свёрточный 171/133 → 0,5 % ошибок."""
        x = в_биты(с.hdlc(с.пакеты_ip(160), флагов_между=4))
        код = с.свёрточный(с.скремблировать(x, (3, 20)))
        разбор = разобрать(данные=в_байты(ошибки(код, 0.005)), имя="запись.bin")
        уровни = [находка.уровень for находка in разбор.находки]
        self.assertEqual(["код", "скремблер", "канальный", "сетевой"], уровни)
        self.assertIn("171/133", разбор.находки[0].что)
        self.assertIn("V.35", " ".join(разбор.находки[1].подробно))
        self.assertEqual(1.0, разбор.находки[3].уверенность)

    def test_перемежение_код_hdlc_ip(self):
        код = с.хэмминг74(в_биты(с.hdlc(с.пакеты_ip(1200), флагов_между=4)))
        разбор = разобрать(данные=в_байты(с.перемежить_блочно(код, 10, 7)[11:]), имя="п.bin")
        self.assertEqual(["перемежение", "канальный", "сетевой"],
                         [находка.уровень for находка in разбор.находки])

    def test_асинхронный_ppp_и_slip_до_ip(self):
        for имя, данные in (("ppp.bin", с.hdlc_асинхронный(
                [b"\xff\x03\x00\x21" + п for п in с.пакеты_ip(200)])),
                            ("slip.bin", с.slip(с.пакеты_ip(200)))):
            with self.subTest(имя):
                разбор = разобрать(данные=данные, имя=имя)
                self.assertEqual(["канальный", "сетевой"],
                                 [находка.уровень for находка in разбор.находки])

    def test_dvb_до_пакетов(self):
        разбор = разобрать(данные=с.dvb(600), имя="dvb.ts")
        self.assertEqual(["транспортный", "код"], [н.уровень for н in разбор.находки])
        self.assertIn("перемежением", разбор.находки[1].что)

    def test_e3_до_шестнадцати_e1_и_сводка_в_отчёте(self):
        иерархии = pdh.ИЕРАРХИИ
        e1 = [в_биты(с.e1(2100, сид=номер)) for номер in range(16)]
        e2 = [с.pdh(e1[4 * j:4 * j + 4], иерархии[0], 2450, [0.42, 0.40, 0.45, 0.43])
              for j in range(4)]
        e3 = с.pdh(e2, иерархии[1], 5300, [0.43, 0.44, 0.42, 0.45])
        разбор = разобрать(данные=в_байты(e3), имя="e3.bin")
        что = [находка.что for находка in разбор.находки]
        self.assertEqual(1, sum("PDH E3" in х for х in что))
        self.assertEqual(4, sum("PDH E2" in х for х in что))
        self.assertEqual(16, sum("E1 по G.704" in х for х in что))
        self.assertIn("приток 4 → приток 4", " ".join(что))
        отчёт = разбор.отчёт()
        self.assertIn("то же ещё в 15", отчёт)
        self.assertIn("то же ещё в 3", отчёт)

    def test_случайный_честно_пуст(self):
        разбор = разобрать(данные=np.random.default_rng(5).bytes(300_000), имя="шум.bin")
        self.assertEqual([], разбор.находки)
        отчёт = разбор.отчёт()
        self.assertIn("НАЙДЕНО: ничего", отчёт)
        self.assertIn("ПРОВЕРЕНО И НЕ НАЙДЕНО", отчёт)
        self.assertIn("младший бит первым", отчёт)

    def test_sig_с_ip_внутри(self):
        разбор = разобрать(данные=с.sig(с.пакеты_ip(100)), имя="сеть.sig")
        self.assertEqual("сетевой", разбор.находки[0].уровень)
        self.assertIn("пакетов файла", разбор.находки[0].мера)

    def test_отчёт_укладывается_в_предел(self):
        разбор = разобрать(данные=с.e1(4000), имя="e1.bin")
        отчёт = разбор.отчёт(предел=1200)
        self.assertLessEqual(len(отчёт), 1200)
        self.assertIn("отчёт сокращён", отчёт)
        self.assertIn("анализатор системы, не модель", разбор.отчёт())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


class ВстраиваниеTests(unittest.TestCase):
    """Поток, приложенный к вопросу, доходит до модели отчётом анализатора."""

    def setUp(self):
        from test_web import WebTestCase

        class Сеть(WebTestCase):
            def runTest(себя):
                pass

        self.сеть = Сеть()
        self.сеть.setUp()
        self.addCleanup(self.сеть.tearDown)
        self.сеть.login("engineer")
        self.разговор = self.сеть.client.post("/api/chats", json={}).json()["chat"]

    def приложить(self, имя, тело):
        ответ = self.сеть.client.post(
            f"/api/chats/{self.разговор['id']}/attachments",
            files={"file": (имя, тело, "application/octet-stream")})
        self.assertEqual(200, ответ.status_code, ответ.text)
        return ответ.json()["attachment"]

    def test_bin_разбирается_анализатором(self):
        вложение = self.приложить("запись.bin", с.e1(2000))
        self.assertEqual("stream", вложение["kind"])
        self.assertIn("разобран анализатором потоков", вложение["note"])
        текст = self.сеть.repos.chats.attachment(вложение["id"]).text
        self.assertIn("РАЗБОР ПОТОКА «запись.bin»", текст)
        self.assertIn("E1 по G.704", текст)

    def test_sig_тоже(self):
        вложение = self.приложить("сеанс.Sig", с.sig(с.пакеты_ip(50)))
        текст = self.сеть.repos.chats.attachment(вложение["id"]).text
        self.assertIn(".Sig: 50 пакетов", текст)
        self.assertIn("пакеты IP", текст)

    def test_модель_знает_как_читать_разбор(self):
        from reportgen.prompts import ASSISTANT_TASK

        self.assertIn("Если приложен «РАЗБОР ПОТОКА»", ASSISTANT_TASK)
        self.assertIn("уровней, которых в разборе нет, не домысливай", ASSISTANT_TASK)

    def test_значок_в_интерфейсе(self):
        from pathlib import Path

        js = (Path(__file__).resolve().parents[1] / "src" / "reportgen" / "web"
              / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("stream: 'цифровой поток — разобран анализатором'", js)


class КомандаTests(unittest.TestCase):
    def test_reportgen_potok(self):
        import subprocess
        import sys
        import tempfile
        from pathlib import Path

        корень = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as каталог:
            путь = Path(каталог) / "e1.bin"
            путь.write_bytes(с.e1(2000))
            итог = subprocess.run([sys.executable, "-m", "reportgen", "potok", str(путь)],
                                  capture_output=True, text=True, encoding="utf-8",
                                  env={"PYTHONPATH": str(корень / "src"), "PATH": ""},
                                  timeout=300)
            self.assertEqual(0, итог.returncode, итог.stderr)
            self.assertIn("E1 по G.704", итог.stdout)
            шум = Path(каталог) / "шум.bin"
            шум.write_bytes(np.random.default_rng(1).bytes(100_000))
            итог = subprocess.run([sys.executable, "-m", "reportgen", "potok", str(шум)],
                                  capture_output=True, text=True, encoding="utf-8",
                                  env={"PYTHONPATH": str(корень / "src"), "PATH": ""},
                                  timeout=300)
            self.assertEqual(1, итог.returncode, "в шуме «нашлось» что-то")
