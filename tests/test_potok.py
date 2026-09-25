# -*- coding: utf-8 -*-
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
from reportgen.potok import cikl, hdlc, kod, pakety, skrembler, разобрать
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

    def test_самосинхронизирующиеся_и_снятие(self):
        for отводы in ((3, 20), (43,), (14, 17)):
            with self.subTest(отводы=отводы):
                найдено = skrembler.найти(с.скремблировать(self.ИСХОДНЫЙ, отводы))
                self.assertIn("самосинхронизирующийся", найдено.что)
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
