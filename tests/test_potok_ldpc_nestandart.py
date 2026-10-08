"""LDPC «нестандарт» файлов отдела: 5G NR по длине (16512 = 43·384) и слова под постоянной маской.

Эталоны независимы от проекта: кодер NR по строению базового графа (``ldpc_potoki.КодерNR``, слово
проверяется H·c = 0 по строкам H), скремблер Голда — прямо по формулам 38.211, 5.2.1
(``ldpc_potoki.голд_nr``), ПСП кадрового скремблера — своим регистром.
"""

import unittest

import numpy as np

import _bootstrap  # noqa: F401
import ldpc_potoki as лп
from reportgen.potok import ldpc_opoznanie as оп
from reportgen.potok import ldpc_std
from reportgen.potok.razbor import слои_находки, снять_вручную

Z = 384
E = 43 * Z                       # 16512: BG1 без первых 2Z, первые 45 столбцов (rv0, без заполнителей)
K = 22 * Z


def _данные_совпали(данные: np.ndarray, c: np.ndarray, k: int) -> int:
    вышло = np.asarray(данные).reshape(-1, k)
    return int((вышло == c[:len(вышло), :k]).all(axis=1).sum())


def _псп(отводы, начало, длина):
    """Своя ПСП: s[n] = ⊕ s[n − t], первые биты — начало."""
    s = list(начало) + [0] * (длина - len(начало))
    for n in range(len(начало), длина):
        b = 0
        for t in отводы:
            b ^= s[n - t]
        s[n] = b
    return np.array(s[:длина], dtype=np.uint8)


class ГолдNR(unittest.TestCase):
    def test_как_по_формулам(self):
        for c_init in (0, 1, 0x1234 * 2**15 + 2**14 + 77, 2**31 - 1):
            np.testing.assert_array_equal(лп.голд_nr(c_init, 3000), оп.голд_nr(c_init, 3000))

    def test_кодер_nr_даёт_слова(self):
        к = лп.кодер_nr(1, Z)
        c = к.закодировать(np.random.default_rng(3).integers(0, 2, (3, K)).astype(np.uint8))
        self.assertTrue(лп.синдром_нулевой(к.м, c).all())


class NR16512(unittest.TestCase):
    """Файл «LDPC_16512»: BG1, Z = 384, переданы позиции 2Z … 45Z − 1 (хвост выколот согласованием скорости)."""

    @classmethod
    def setUpClass(cls):
        cls.биты, cls.c = лп.nr(1, Z, 40, E=E, сид=4)

    def test_без_скремблера(self):
        шум = лп.ошибки(лп.сдвинуть(self.биты, 777), 1e-3, сид=5)
        кк, _ = оп.опознать(шум, имена=["nr-bg1-z384", "nr-bg1-z352", "nr-bg2-z384"], срок=60)
        self.assertTrue(кк)
        к = кк[0]
        self.assertEqual(("nr-bg1-z384", E, E, 0, 777 % E), (к.имя, к.шаг, к.длина_слова, к.маска, к.начало % E))
        данные, _, сошлось = оп.снять(шум, к)
        self.assertEqual(1.0, сошлось)
        self.assertEqual(40, _данные_совпали(данные, self.c, K))

    def test_голд_один_блок(self):
        c_init = 0x1234 * 2**15 + 2**14 + 77
        поток, c = лп.nr_со_скремблером(1, Z, 40, E=E, c_init=c_init, сид=4)
        шум = лп.ошибки(поток, 1e-3, сид=6)
        кк, _ = оп.опознать(шум, имена=["nr-bg1-z384"], срок=60)
        self.assertTrue(кк)
        к = кк[0]
        self.assertEqual((E, 1), (к.шаг, к.маска))
        данные, подробно, сошлось = оп.снять(шум, к)
        self.assertEqual(1.0, сошлось)
        self.assertEqual(40, _данные_совпали(данные, c, K))
        self.assertIn(f"c_init = {c_init}", " ".join(подробно))

    def test_голд_два_блока_в_транспортном(self):
        c_init = 0x0BEE * 2**15 + 5
        поток, c = лп.nr_со_скремблером(1, Z, 40, E=E, c_init=c_init, блоков=2, сид=4)
        шум = лп.ошибки(лп.сдвинуть(поток, E), 1e-3, сид=7)      # перед словами — кусок записи длиной в слово
        кк, _ = оп.опознать(шум, имена=["nr-bg1-z384"], срок=60)
        self.assertTrue(кк)
        к = кк[0]
        self.assertEqual((E, 2), (к.шаг, к.маска))
        данные, подробно, сошлось = оп.снять(шум, к)
        self.assertEqual(40 / 41, сошлось)                       # чужое первое «слово» не сходится
        self.assertEqual(40, _данные_совпали(данные.reshape(-1, K)[1:], c, K))
        self.assertIn(f"c_init = {c_init}", " ".join(подробно))

    def test_слой_стола_как_автомат(self):
        c_init = 4242
        поток, c = лп.nr_со_скремблером(1, Z, 24, E=E, c_init=c_init, сид=4)
        шум = лп.ошибки(поток, 1e-3, сид=8)
        кк, св = оп.опознать(шум, имена=["nr-bg1-z384"], срок=60)
        находка = оп.находка(шум, кк[0], сведения=св)
        слои = слои_находки(находка)
        self.assertEqual(1, len(слои))
        self.assertIn("маска 1", слои[0])
        ряд, н = снять_вручную(шум, слои[0])
        np.testing.assert_array_equal(np.asarray(находка.дальше), ряд)
        self.assertEqual(24, _данные_совпали(ряд, c, K))


class МаскаКадра(unittest.TestCase):
    """Скремблер кадра модема поверх слов (заново на каждом слове) — маска одна на все слова."""

    ИМЯ = "wifi-1944-972"

    @classmethod
    def setUpClass(cls):
        cls.c = лп.кодовые(cls.ИМЯ, 120, сид=9)

    def test_пс_п_известного_многочлена(self):
        маска = _псп((14, 15), [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0], 1944)
        шум = лп.ошибки(лп.сдвинуть((self.c ^ маска).reshape(-1), 300), 1e-3, сид=10)
        кк, _ = оп.опознать(шум, имена=[self.ИМЯ, "wifi-1944-1296", "wimax-1920-960"], срок=60)
        self.assertTrue(кк)
        к = кк[0]
        self.assertEqual((self.ИМЯ, 1), (к.имя, к.маска))
        данные, подробно, сошлось = оп.снять(шум, к)
        self.assertGreater(сошлось, 0.98)
        вышло = данные.reshape(-1, 972)
        self.assertEqual(120, len(вышло))
        self.assertGreater(int((вышло == self.c[:, :972]).all(axis=1).sum()), 117)
        self.assertIn("1 + x⁻¹⁴ + x⁻¹⁵", " ".join(подробно))

    def test_неизвестная_маска_данные_относительно_первого(self):
        маска = np.random.default_rng(11).integers(0, 2, 1944).astype(np.uint8)
        шум = лп.ошибки((self.c ^ маска).reshape(-1), 1e-3, сид=12)
        кк, _ = оп.опознать(шум, имена=[self.ИМЯ], срок=60)
        self.assertTrue(кк and кк[0].маска == 1)
        данные, подробно, _ = оп.снять(шум, кк[0])
        вышло = данные.reshape(-1, 972)
        относительно = self.c[:len(вышло), :972] ^ self.c[0, :972]
        self.assertGreater(int((вышло == относительно).all(axis=1).sum()), len(вышло) - 3)
        self.assertIn("с точностью до кодового слова", " ".join(подробно))

    def test_повтор_одного_слова_не_код(self):
        слово = np.random.default_rng(13).integers(0, 2, 20000).astype(np.uint8)
        кк, _ = оп.опознать(np.tile(слово, 40), имена=[self.ИМЯ, "nr-bg1-z384", "dvb-s2-16200-7200"], срок=60)
        self.assertEqual([], кк)

    def test_случайные_биты_не_код(self):
        б = np.random.default_rng(14).integers(0, 2, 1 << 20).astype(np.uint8)
        кк, _ = оп.опознать(б, имена=[self.ИМЯ, "nr-bg1-z384", "nr-bg2-z384", "dvb-s2-64800-32400"], срок=60)
        self.assertEqual([], кк)


class PLFRAMEвРазметкеДанных(unittest.TestCase):
    """Файлы «LDPC_65070» и «LDPC_16740»: демодулятор решил и заголовок, и пилоты по созвездию данных."""

    def _снять(self, имя, вид, пилоты, поворот, отражение, *, кадров=6, сдвиг=1001, скремблер=True):
        import time
        from reportgen.potok import dvbs2_pl, razbor
        б, c, u = лп.поток_dvbs2_разметка(имя, кадров, вид=вид, пилоты=пилоты, поворот=поворот, отражение=отражение,
                                          скремблер_pl=скремблер)
        б = лп.ошибки(лп.сдвинуть(б, сдвиг), 1e-3, сид=3)
        з = dvbs2_pl.найти_с_кадрами(б)
        self.assertIsNotNone(з)
        н = razbor._ldpc_plframe(з, razbor.Бюджет(конец=time.monotonic() + 300, профиль=razbor.ПРОФИЛИ["обычно"]))
        self.assertIsNotNone(н)
        вышло = np.asarray(н.дальше).reshape(-1, u.shape[1])
        self.assertEqual(кадров, len(вышло))
        self.assertEqual(кадров, int((вышло == u).all(axis=1).sum()))
        return б, з, н

    def test_65070_8psk_со_скремблером_поворот_и_отражение(self):
        б, з, н = self._снять("dvb-s2-64800-38880", "8PSK", False, 3, True)
        self.assertEqual(65070, з.свойства["шаг"])
        self.assertTrue(н.свойства["перед"][0].endswith("скремблер"))
        слои = слои_находки(н)
        ряд = б
        for слой in слои:
            ряд, _ = снять_вручную(ряд, слой)
        np.testing.assert_array_equal(np.asarray(н.дальше), ряд)

    def test_65070_без_скремблера(self):
        _, з, н = self._снять("dvb-s2-64800-38880", "8PSK", False, 0, False, скремблер=False)
        self.assertFalse(н.свойства["перед"][0].endswith("скремблер"))

    def test_16740_qpsk_с_пилотами(self):
        _, з, н = self._снять("dvb-s2-16200-7200", "", True, 2, False, кадров=8)
        self.assertEqual(16740, з.свойства["шаг"])
        self.assertIn("пилоты — 5 блоков по 72 бит", " ".join(з.подробно))

    def test_8psk_с_пилотами(self):
        _, з, _ = self._снять("dvb-s2-64800-38880", "8PSK", True, 5, False, кадров=4)
        self.assertEqual(3 * (90 + 21600 + 14 * 36), з.свойства["шаг"])

    def test_сбой_синхронизации_и_потерянный_заголовок(self):
        from reportgen.potok import dvbs2_pl
        б, c, u = лп.поток_dvbs2_разметка("dvb-s2-16200-7200", 10, пилоты=True, поворот=0)
        б = б.copy()
        б[16740 * 3:16740 * 3 + 180] ^= 1                         # заголовок четвёртого кадра испорчен
        б = np.concatenate([б[:16740 * 6], б[16740 * 6 + 2:]])      # сбой: два бита (символ QPSK) потеряны
        з = dvbs2_pl.найти_с_кадрами(б)
        кадры = з.свойства["варианты_кадров"][0]["кадров"]
        self.assertGreaterEqual(кадры, 9)                          # кадр со сбоем выпадает, остальные — на месте

    def test_случайные_биты_не_заголовок(self):
        from reportgen.potok import dvbs2_pl
        б = np.random.default_rng(1).integers(0, 2, 1 << 22).astype(np.uint8)
        self.assertIsNone(dvbs2_pl.найти_в_разметке(б))


class ВыводВсехСлов(unittest.TestCase):
    """Новый массив — все слова записи (не первые 4000), исправленные слова и ошибки, большие массивы кусками."""

    def test_все_слова_и_виды_вывода(self):
        c = лп.кодовые("wifi-648-324", 4500, сид=21)
        принято = лп.ошибки(c.reshape(-1), 2e-3, сид=22)
        ряд, н = снять_вручную(принято, "ldpc wifi-648-324 начало 0")
        self.assertEqual(4500 * 324, len(ряд))
        np.testing.assert_array_equal(c[:, :324].reshape(-1), ряд)
        слова, _ = снять_вручную(принято, "ldpc wifi-648-324 начало 0 вывод слова")
        np.testing.assert_array_equal(c.reshape(-1), слова)
        ошибки, н = снять_вручную(принято, "ldpc wifi-648-324 начало 0 вывод ошибки")
        np.testing.assert_array_equal(принято ^ c.reshape(-1), ошибки)
        self.assertIn("ошибки линии", " ".join(н.подробно))

    def test_ход_слоя(self):
        from reportgen.potok import potokovo, rastr
        from reportgen.potok.hranenie import Источник, Приёмник
        import tempfile
        from pathlib import Path
        c = лп.кодовые("wifi-648-324", 400, сид=23)
        with tempfile.TemporaryDirectory() as папка:
            путь = Path(папка) / "в.bin"
            with Приёмник(путь) as п:
                п.записать(c.reshape(-1))
                п.закрыть()
            ход = []
            шаги = rastr.проверить_шаги([{"вид": "слой", "слой": "ldpc wifi-648-324 начало 0"}])
            with mock_время():
                potokovo.выполнить(Источник(путь, бит=c.size), шаги, Path(папка) / "вых.bin",
                                   ход=lambda д, т: ход.append((д, т)))
            self.assertTrue(any("LDPC: слов" in т for _, т in ход))

    def _кусками(self, поток, слои, в_памяти_до):
        from reportgen.potok import potokovo, rastr
        from reportgen.potok.hranenie import Источник, Приёмник
        import tempfile
        from pathlib import Path
        шаги = rastr.проверить_шаги([{"вид": "слой", "слой": с} for с in слои])
        эталон, _ = rastr.применить(поток.copy(), шаги)
        with tempfile.TemporaryDirectory() as папка:
            путь = Path(папка) / "в.bin"
            with Приёмник(путь) as п:
                п.записать(поток)
                п.закрыть()
            бит, описание = potokovo.выполнить(Источник(путь, бит=len(поток)), шаги, Path(папка) / "вых.bin",
                                               в_памяти_до=в_памяти_до)
            вышло = Источник(Path(папка) / "вых.bin", бит=бит).все()
        return эталон, вышло, описание

    def test_кусками_по_сетке_слов(self):
        c = лп.кодовые("wifi-1944-972", 60, сид=24)
        поток = лп.ошибки(лп.сдвинуть(c.reshape(-1), 300), 1e-3, сид=25)
        эталон, вышло, описание = self._кусками(поток, ["ldpc wifi-1944-972 начало 300"], 1944 * 7 + 500)
        np.testing.assert_array_equal(эталон, вышло)
        self.assertEqual(60 * 972, len(вышло))
        self.assertIn("по сетке слов", описание[-1])

    def test_кусками_plframe_в_разметке_данных(self):
        from reportgen.potok import dvbs2_pl, razbor
        import time
        б, c, u = лп.поток_dvbs2_разметка("dvb-s2-16200-7200", 12, пилоты=True, поворот=2)
        б = лп.ошибки(лп.сдвинуть(б, 777), 1e-3, сид=26)
        з = dvbs2_pl.найти_с_кадрами(б)
        н = razbor._ldpc_plframe(з, razbor.Бюджет(конец=time.monotonic() + 300, профиль=razbor.ПРОФИЛИ["обычно"]))
        слои = слои_находки(н)
        эталон, вышло, описание = self._кусками(б, слои, 16740 * 3 + 1000)
        np.testing.assert_array_equal(эталон, вышло)
        self.assertEqual(12 * 7200, len(вышло))
        np.testing.assert_array_equal(u.reshape(-1), вышло)


class ПорядокБайт(unittest.TestCase):
    """Байтовый вид нового массива: порядок бит в байте — по маркерам следующего уровня."""

    def test_маркеры(self):
        from reportgen.potok import ldpc_okno
        _, u = лп.fecframes("dvb-s2-16200-7200", 6, бчх=True)
        bb = u.reshape(-1)                                    # данные LDPC — слова БЧХ с BBFRAME
        self.assertEqual({"порядок": "старший", "почему": "BBHEADER DVB-S2 с верной CRC-8"}, ldpc_okno.порядок_байт(bb))
        self.assertEqual("младший", ldpc_okno.порядок_байт(ldpc_okno._в_младший(bb))["порядок"])
        пакеты = np.random.default_rng(1).integers(0, 256, (60, 188)).astype(np.uint8)
        пакеты[:, 0] = 0x47
        ts = np.unpackbits(пакеты.reshape(-1))
        self.assertEqual("младший", ldpc_okno.порядок_байт(ldpc_okno._в_младший(лп.сдвинуть(ts, 8)))["порядок"])
        текст = np.unpackbits(np.frombuffer(b"LDPC decoded text, line by line.\n" * 200, dtype=np.uint8))
        self.assertEqual("младший", ldpc_okno.порядок_байт(ldpc_okno._в_младший(текст))["порядок"])
        случайные = np.random.default_rng(2).integers(0, 2, 1 << 16).astype(np.uint8)
        self.assertEqual("старший", ldpc_okno.порядок_байт(случайные)["порядок"])
        self.assertIn("нет", ldpc_okno.порядок_байт(случайные)["почему"])


def mock_время():
    """Ход слоя передаётся не чаще раза в 0,5 с — в тесте каждый."""
    from unittest import mock
    from reportgen.potok import potokovo
    return mock.patch.object(potokovo, "ХОД_СЛОЯ_КАЖДЫЕ", 0.0)


class Декодер(unittest.TestCase):
    """Декодер по рёбрам даёт то же, что прежняя запись по дополненным строкам."""

    def test_как_прежний(self):
        from reportgen.potok import ldpc
        for имя, ber in (("wifi-648-324", 0.04), ("nr-bg2-z16", 0.05), ("ar4ja-2560-1024", 0.05)):
            схема = ldpc_std.схема(имя)
            c = лп.кодовые(имя, 12, сид=2)
            пр = лп.ошибки(c[:, схема.переданы].reshape(-1), ber, сид=5).reshape(12, -1)
            связи = схема.матрица.связи()
            a, oa = ldpc.мин_сумма(схема.llr(пр), связи, схема.матрица.n, 50)
            b, ob = ldpc.мин_сумма_прежний(схема.llr(пр), связи, схема.матрица.n, 50)
            np.testing.assert_array_equal(a, b)
            np.testing.assert_array_equal(oa, ob)

    def test_без_висячих_выколотых_проверок(self):
        к = оп.Кандидат("nr-bg1-z384", "", 66 * Z, 0, E, 0.0, 0.5, 0.0, слово=E)
        схема = оп.схема_кандидата(к)
        self.assertEqual(23 * Z, len(схема.связи_декодера()))        # 23 блок-строки целиком внутри 45 столбцов


if __name__ == "__main__":
    unittest.main()
