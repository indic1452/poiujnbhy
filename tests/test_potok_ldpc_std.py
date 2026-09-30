"""Встроенные коды LDPC стандартов: состав, размеры и ранг, кодирование, снятие автоматом.

Таблицы сверены при сборке данных (DVB-S2 — xdsopl против AFF3CT, 5G NR — AFF3CT
против srsRAN_4G); здесь — что из них строятся коды нужных размеров и ранга,
что слово DVB, закодированное накопителем прямо по описанию таблицы, проходит
все проверки, и что автомат находит и снимает код сам.
"""

import time
import unittest
from unittest import mock

import numpy as np

import _bootstrap  # noqa: F401
import potok_sintez as с
from reportgen.potok import gf2, ldpc, ldpc_std, razbor
from reportgen.potok.bity import в_байты, в_биты
from reportgen.potok.razbor import снять_вручную


def передать(слова, схема, доля, сдвиг, сид=3):
    rng = np.random.default_rng(сид)
    переданные = слова[:, схема.переданы]
    ошибки = (rng.random(переданные.shape) < доля).astype(np.uint8)
    return np.concatenate([rng.integers(0, 2, сдвиг).astype(np.uint8), (переданные ^ ошибки).reshape(-1)])


def кодовые(матрица, слов, сид=2):
    G = gf2.ядро(матрица.плотная())
    данные = np.random.default_rng(сид).integers(0, 2, (слов, len(G)))
    return (данные @ G % 2).astype(np.uint8)


def систематически(матрица, данные):
    """Кодирование по H: данные — на позициях ldpc.информационные, проверочные — из H.

    Базис ядра H с проверочными столбцами впереди — порождающая матрица, единичная
    на информационных позициях (свободные столбцы при приведении — они).
    """
    инфо = ldpc.информационные(матрица)
    порядок = np.concatenate([np.setdiff1d(np.arange(матрица.n), инфо), инфо])
    G = gf2.ядро(матрица.плотная()[:, порядок])
    слова = np.zeros((len(данные), матрица.n), np.uint8)
    слова[:, порядок] = (данные.astype(np.int64) @ G % 2).astype(np.uint8)
    return слова


def dvb_кодировать(м, данные):
    """Кодирование DVB по описанию таблицы адресов: чётность групп проверок, затем накопитель."""
    k = м.n - м.m
    столбцы, границы = м.подряд()
    строки = np.repeat(np.arange(м.m), np.diff(границы))
    свои = столбцы < k
    слова = []
    for d in данные:
        s = np.bincount(строки[свои], weights=d[столбцы[свои]], minlength=м.m).astype(np.int64) % 2
        слова.append(np.concatenate([d, np.bitwise_xor.accumulate(s.astype(np.uint8))]))
    return np.array(слова, dtype=np.uint8)


def по_gnuradio(слово, столбцы):
    """Перемежитель DVB-S2 так, как его пишет GNU Radio (gr-dtv, dvbs2_interleaver_bb): для
    каждой строки j — биты in[rowaddr_t + j], старший бит символа первым."""
    строк = len(слово) // len(столбцы)
    адреса = [int(с) * строк for с in столбцы]
    return np.array([слово[a + j] for j in range(строк) for a in адреса], dtype=слово.dtype)


def hdlc_в_словах(имя, *, выколоть_последние=0, сид=3):
    """Поток HDLC с пакетами IP, закодированный встроенным кодом: (слова в потоке, схема, данные)."""
    м = ldpc_std.матрица(имя)
    k = len(ldpc.информационные(м))
    база = ldpc_std.схема(имя)
    схема = ldpc.Схема(м, np.union1d(база.выколоты, np.arange(м.n - выколоть_последние, м.n)),
                       np.array([], np.int64))
    поток = в_биты(с.hdlc(с.пакеты_ip(500, сид=8), флагов_между=4))
    данные = поток[:len(поток) // k * k].reshape(-1, k)
    переданы = систематически(м, данные)[:, схема.переданы]
    rng = np.random.default_rng(сид)
    return переданы ^ (rng.random(переданы.shape) < 0.003).astype(np.uint8), схема, данные


class Состав(unittest.TestCase):
    def test_список(self):
        коды = ldpc_std.список()
        имена = [к["имя"] for к in коды]
        self.assertEqual(len(имена), len(set(имена)))
        self.assertEqual(sum(1 for и in имена if и.startswith("nr-")), 102)
        self.assertEqual(sum(1 for и in имена if и.startswith("dvb-")), 70)
        self.assertEqual(sum(1 for и in имена if и.startswith("wifi-")), 12)
        self.assertEqual(len([и for и in имена if not ldpc_std.ldpc_kitay.есть(и)]), 192)  # и коды КНР
        for и in ("nr-bg1-z384", "nr-bg2-z2", "dvb-s2-64800-32400", "dvb-s2-16200-7200",
                  "10gbase-t-2048-1723", "wifi-648-540", "ar4ja-10240-4096"):
            self.assertTrue(ldpc_std.есть(и), и)
        for и in ("nr-bg1-z385", "nr-bg3-z8", "dvb-s2-1-1", "nr-bg1", ""):
            self.assertFalse(ldpc_std.есть(и), и)

    def test_nr_размеры_и_ранг(self):
        for bg, Z in ((1, 2), (1, 5), (2, 8), (2, 15)):
            with self.subTest(bg=bg, Z=Z):
                м = ldpc_std.матрица(f"nr-bg{bg}-z{Z}")
                n, строк = {1: (68, 46), 2: (52, 42)}[bg]
                self.assertEqual((м.n, м.m), (n * Z, строк * Z))
                self.assertEqual(gf2.ранг(м.плотная()), строк * Z)
                self.assertEqual(len(ldpc.информационные(м)), {1: 22, 2: 10}[bg] * Z)
                # Данные — первые kb·Z позиций: столбцы H вне них — невырожденная часть.
                H = м.плотная()
                self.assertEqual(gf2.ранг(H[:, len(ldpc.информационные(м)):]), строк * Z)
                схема = ldpc_std.схема(f"nr-bg{bg}-z{Z}")
                self.assertEqual(схема.выколоты.tolist(), list(range(2 * Z)))

    def test_nr_сдвиги_по_модулю_z(self):
        """Сдвиги размеров одного набора — V mod Z: у Z = 8 и Z = 16 (набор 0) одинаковый узор блоков."""
        a, b = ldpc_std.матрица("nr-bg2-z8"), ldpc_std.матрица("nr-bg2-z16")
        узор = lambda м, Z: {(i // Z, int(с) // Z) for i, стр in enumerate(м.строки) for с in стр}  # noqa: E731
        self.assertEqual(узор(a, 8), узор(b, 16))

    def test_dvb_накопитель(self):
        """Слово DVB-S2 (16200, 7200), закодированное накопителем: чётность группы проверок
        по таблице адресов, затем p_i = p_(i−1) ⊕ s_i — проходит все проверки H."""
        м = ldpc_std.матрица("dvb-s2-16200-7200")
        n, k = 16200, 7200
        self.assertEqual((м.n, м.m), (n, n - k))
        rng = np.random.default_rng(5)
        d = rng.integers(0, 2, k).astype(np.uint8)
        s = np.array([int(np.bitwise_xor.reduce(d[с[с < k]])) if len(с[с < k]) else 0 for с in м.строки],
                     np.uint8)
        p = np.bitwise_xor.accumulate(s)
        слово = np.concatenate([d, p])
        self.assertTrue(all(int(слово[с].sum()) % 2 == 0 for с in м.строки))
        self.assertEqual(ldpc.информационные(м).tolist()[:3], [0, 1, 2])

    def test_прочие_ранг(self):
        for имя, k in (("wifi-648-540", 540), ("wimax-576-288", 288), ("ccsds-128-64", 64)):
            with self.subTest(имя=имя):
                м = ldpc_std.матрица(имя)
                self.assertEqual(м.n - gf2.ранг(м.плотная()), k)

    def test_данные_где_по_стандарту_и_где_по_гауссу(self):
        """У NR и Wi-Fi позиции данных задаёт стандарт; у DVB — первые k (накопитель); у прочих
        проверочная часть не накопитель — позиции данных по опорным столбцам, и это правда
        информационное множество (остальные столбцы H невырождены)."""
        self.assertEqual("первые 160 позиций (по стандарту)", ldpc.информационные_сводка(ldpc_std.матрица("nr-bg2-z16"))[1])
        self.assertEqual("первые 540 позиций (по стандарту)", ldpc.информационные_сводка(ldpc_std.матрица("wifi-648-540"))[1])
        self.assertEqual("первые k позиций (проверочная часть — накопитель)",
                         ldpc.информационные_сводка(ldpc_std.матрица("dvb-s2-16200-7200"))[1])
        for имя in ("ccsds-128-64", "wimax-576-288", "wran-480-360"):
            with self.subTest(имя=имя):
                м = ldpc_std.матрица(имя)
                self.assertFalse(ldpc._треугольная_справа(м))
                self.assertEqual("по опорным столбцам (исключение Гаусса)", ldpc.информационные_сводка(м)[1])
                данные = ldpc.информационные(м)
                H = м.плотная()
                self.assertEqual(gf2.ранг(np.delete(H, данные, axis=1)), м.m)

    def test_wifi_802_11n(self):
        """12 кодов 802.11n: ранг даёт скорость, проверочная часть — столбец со сдвигами 1…0…1 и
        двухдиагональная, данные — первые k позиций."""
        for n, Z in ((648, 27), (1296, 54), (1944, 81)):
            for a, b in ((1, 2), (2, 3), (3, 4), (5, 6)):
                k = n * a // b
                with self.subTest(n=n, k=k):
                    м = ldpc_std.матрица(f"wifi-{n}-{k}")
                    H = м.плотная()
                    self.assertEqual((м.n, м.m), (n, n - k))
                    self.assertEqual(gf2.ранг(H), n - k)
                    self.assertEqual(gf2.ранг(H[:, k:]), n - k)
                    self.assertEqual(ldpc.информационные(м).tolist(), list(range(k)))
                    kb, mb = k // Z, (n - k) // Z
                    # Столбец kb: единичные блоки со сдвигом 1 в первой и последней строке блоков.
                    self.assertEqual(H[0, kb * Z + 1], 1)
                    self.assertEqual(H[(mb - 1) * Z, kb * Z + 1], 1)
                    self.assertEqual(ldpc_std.схема(f"wifi-{n}-{k}").длина, n)

    def test_wifi_сплошным_потоком(self):
        переданы, схема, данные = hdlc_в_словах("wifi-1944-1620")
        поток = np.concatenate([np.zeros(77, np.uint8), переданы.reshape(-1)])
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIn("по матрице «wifi-1944-1620»", найдено.что)
        self.assertEqual(найдено.свойства["начало"], 77)
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))

    def test_перемежитель_dvb_s2(self):
        """Запись по столбцам, чтение по строкам — как в GNU Radio; порядок столбцов по стандарту."""
        self.assertEqual(ldpc.перемежитель(12, "012").tolist(), [0, 4, 8, 1, 5, 9, 2, 6, 10, 3, 7, 11])
        слово = np.random.default_rng(2).integers(0, 2, 16200).astype(np.uint8)
        for столбцы in ("012", "210", "0123", "01234"):
            with self.subTest(столбцы=столбцы):
                self.assertTrue(np.array_equal(слово[ldpc.перемежитель(16200, столбцы)],
                                               по_gnuradio(слово, столбцы)))
        self.assertEqual(ldpc_std.перемежения("dvb-s2-16200-9720")[0], ("8PSK", "210"))      # 3/5
        self.assertEqual(ldpc_std.перемежения("dvb-s2-64800-48600")[0], ("8PSK", "012"))
        self.assertEqual([в for в, _ in ldpc_std.перемежения("dvb-s2-64800-48600")], ["8PSK", "16APSK", "32APSK"])
        self.assertEqual([], ldpc_std.перемежения("dvb-t2-16200-7200"))
        self.assertEqual([], ldpc_std.перемежения("nr-bg2-z16"))
        for n, плохое in ((13, "01"), (12, "013"), (12, "0012"), (12, "0")):
            with self.subTest(плохое=плохое), self.assertRaises(ValueError):
                ldpc.перемежитель(n, плохое)
        # Перемеженная схема согласованием скорости не дробится: слово DVB-S2 целое.
        перемеженная = ldpc_std.схема("dvb-s2-16200-9720", "8PSK")
        self.assertEqual(1, len(ldpc.варианты_схемы(перемеженная, 4 * 15000)))
        self.assertGreater(len(ldpc.варианты_схемы(ldpc_std.схема("dvb-s2-16200-9720"), 4 * 15000)), 1)
        self.assertEqual("0123", ldpc_std.столбцы_перемежения("wifi-648-540", "16APSK"))
        self.assertEqual("210", ldpc_std.столбцы_перемежения("dvb-s2-64800-38880", "8PSK"))
        м = ldpc_std.матрица("wifi-648-540")
        with self.assertRaises(ValueError):
            ldpc.Схема(м, np.array([], np.int64), np.array([], np.int64), np.arange(647))
        with self.assertRaises(ValueError):
            ldpc_std.схема("wifi-648-540", "8PSK")

    def test_неизвестный(self):
        with self.assertRaises(ValueError):
            ldpc_std.матрица("нет-такого")

    def test_подходящие_по_длине_потока_и_блоку(self):
        """Слово должно уложиться в блок целое число раз (от половины до целого слова стандарта),
        а в поток — хотя бы трижды."""
        коротко = ldpc_std.подходящие(None, бит=60_000)
        self.assertIn("nr-bg2-z16", коротко)
        self.assertNotIn("dvb-s2-64800-32400", коротко)                   # 3 · 32400 > 60 000
        self.assertIn("dvb-s2-64800-32400", ldpc_std.подходящие(None, бит=100_000))
        # У NR слово в потоке — без первых 2Z: 832 − 32 = 800; блок 821 (простое) — не уложится.
        self.assertNotIn("nr-bg2-z16", ldpc_std.подходящие(821))
        self.assertIn("nr-bg2-z16", ldpc_std.подходящие(800))
        self.assertIn("nr-bg2-z16", ldpc_std.подходящие(3 * 400))

    def test_alist_длинного_кода(self):
        """alist кода DVB-S2 (64 800 столбцов) читается в ту же матрицу и быстро: номера разбираются
        одним проходом (раньше хвост списка копировался на каждом столбце — почти две минуты)."""
        м = ldpc_std.матрица("dvb-s2-64800-32400")
        столбцы = [[] for _ in range(м.n)]
        for i, строка in enumerate(м.строки):
            for j in строка.tolist():
                столбцы[j].append(i + 1)
        dc, dr = max(map(len, столбцы)), max(len(с) for с in м.строки)
        линии = [f"{м.n} {м.m}", f"{dc} {dr}", " ".join(str(len(с)) for с in столбцы),
                 " ".join(str(len(с)) for с in м.строки)]
        линии += [" ".join(map(str, с + [0] * (dc - len(с)))) for с in столбцы]
        линии += [" ".join(str(j + 1) for j in с.tolist()) + " 0" * (dr - len(с)) for с in м.строки]
        начало = time.monotonic()
        прочитано = ldpc.из_alist("\n".join(линии))
        self.assertLess(time.monotonic() - начало, 10.0)
        self.assertEqual(len(прочитано.строки), len(м.строки))
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(прочитано.строки, м.строки, strict=False)))

    def test_ar4ja_схема_из_файла(self):
        """AR4JA (CCSDS): последние 4 блок-столбца не передаются — по шаблону из файла источника;
        в потоке слово 8192 бит, скорость 1/2."""
        схема = ldpc_std.схема("ar4ja-10240-4096")
        self.assertEqual((схема.длина, схема.выколоты[0], схема.выколоты[-1]), (8192, 8192, 10239))
        э = next(к for к in ldpc_std.список() if к["имя"] == "ar4ja-10240-4096")
        self.assertEqual((э["выколоты"], э["скорость"]), ("8192-10239", "1/2"))
        self.assertTrue(ldpc_std.выколоты_по_стандарту("ar4ja-10240-4096"))
        self.assertFalse(ldpc_std.выколоты_по_стандарту("dvb-s2-16200-7200"))
        # Выколотый блок-столбец — во всех строках: для пробы — суммы с опорными строками.
        self.assertEqual([], ldpc.вычислимые(схема))
        L, проверки = ldpc_std.проба("ar4ja-10240-4096", 64)
        self.assertEqual((L, len(проверки)), (8192, 64))

    def test_проверки_для_пробы_равномерно(self):
        """Для пробы — не первые вычислимые проверки, а равномерно по всей матрице."""
        схема = ldpc_std.схема("dvb-s2-16200-7200")
        все = ldpc.вычислимые(схема)
        self.assertEqual([x.tolist() for x in все[::len(все) // 64][:64]],
                         [x.tolist() for x in ldpc.вычислимые(схема, 64)])

    def test_граф_1_только_суммы_пар(self):
        """У графа 1 первые 2Z позиций — во всех строках: одиночных вычислимых проверок нет,
        для пробы — суммы пар строк; готовые проверки кода выполняет любое его слово."""
        схема = ldpc_std.схема("nr-bg1-z8")
        self.assertEqual([], ldpc.вычислимые(схема))
        L, проверки = ldpc_std.проба("nr-bg1-z8", 64)
        self.assertEqual((L, len(проверки)), (68 * 8 - 16, 64))
        слова = кодовые(схема.матрица, 3)[:, схема.переданы]
        for проверка in проверки:
            self.assertTrue(np.all(слова[:, проверка].sum(axis=1) % 2 == 0))


class Снятие(unittest.TestCase):
    ИМЯ = "nr-bg2-z16"

    @classmethod
    def setUpClass(cls):
        cls.м = ldpc_std.матрица(cls.ИМЯ)
        cls.слова = кодовые(cls.м, 60)
        cls.данные = cls.слова[:, :160]

    def test_автомат_по_блоку(self):
        """Кадр — 4 слова NR по схеме стандарта (первые 2Z не передаются): код и начало — сами."""
        схема = ldpc_std.схема(self.ИМЯ)
        поток = передать(self.слова, схема, доля=0.004, сдвиг=0)
        найдено = ldpc.найти_по_матрицам(поток, блок=4 * схема.длина, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn(f"по матрице «{self.ИМЯ}»", найдено.что)
        self.assertIn("по схеме стандарта", найдено.что)
        self.assertTrue(np.array_equal(self.данные.reshape(-1), найдено.дальше))
        self.assertRegex(найдено.подробно[0], r"загруженных матриц \d+, встроенных кодов стандартов \d+")

    def test_согласование_скорости(self):
        """Переданы не все проверочные: слово короче на 100 бит — «выколоты последние 100»."""
        база = ldpc_std.схема(self.ИМЯ)
        схема = ldpc.Схема(self.м, np.union1d(база.выколоты, np.arange(self.м.n - 100, self.м.n)),
                           np.array([], np.int64))
        поток = передать(self.слова, схема, доля=0.002, сдвиг=0)
        найдено = ldpc.найти_по_матрицам(поток, блок=4 * схема.длина, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("по схеме стандарта; выколоты последние 100 переданных позиций", найдено.что)
        self.assertTrue(np.array_equal(self.данные.reshape(-1), найдено.дальше))

    def test_слоем_без_загрузки(self):
        схема = ldpc_std.схема(self.ИМЯ)
        поток = передать(self.слова, схема, доля=0.003, сдвиг=0)
        ряд, запись = снять_вручную(поток, f"ldpc {self.ИМЯ} начало 0")
        self.assertTrue(np.array_equal(self.данные.reshape(-1), ряд))
        self.assertIn("выколото 32, укорочено 0", запись.подробно[0])

    def test_без_встроенных_не_ищется(self):
        схема = ldpc_std.схема(self.ИМЯ)
        поток = передать(self.слова, схема, доля=0.0, сдвиг=0)
        self.assertIsNone(ldpc.найти_по_матрицам(поток, блок=4 * схема.длина, встроенные=False, схемы=[]))

    def test_чужой_поток_без_блока(self):
        """Случайный поток: ни один из встроенных кодов не совпал — находки нет (лучший из
        несовпавших кодом не считается)."""
        поток = np.random.default_rng(8).integers(0, 2, 300_000).astype(np.uint8)
        self.assertIsNone(ldpc.найти_по_матрицам(поток, встроенные=True))

    def test_нули_не_код(self):
        """Заполнение нулями выполняет любую проверку при любом начале — это не код."""
        нули = np.zeros(40 * 800, np.uint8)
        self.assertIsNone(ldpc.найти_по_матрицам(нули, блок=4 * 800, встроенные=True))

    def test_подходящие(self):
        подходящие = ldpc_std.подходящие(4 * 800)
        self.assertIn(self.ИМЯ, подходящие)
        self.assertNotIn("dvb-s2-64800-32400", подходящие)
        self.assertEqual(len(ldpc_std.подходящие(None)), len(ldpc_std.список()))

    def test_граф_1_без_блока(self):
        """Граф 1 5G NR, слово по схеме стандарта, поток с произвольного бита: код, начало и
        данные — сами (проба — суммами пар строк)."""
        м = ldpc_std.матрица("nr-bg1-z8")
        слова = кодовые(м, 80, сид=4)
        поток = передать(слова, ldpc_std.схема("nr-bg1-z8"), доля=0.002, сдвиг=77)
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("по матрице «nr-bg1-z8»", найдено.что)
        self.assertEqual(найдено.свойства["начало"], 77)
        сошлось = len(найдено.дальше) // 176
        self.assertTrue(np.array_equal(слова[:сошлось, :176].reshape(-1), найдено.дальше))

    def test_ar4ja_сплошным_потоком(self):
        """CCSDS AR4JA (8192, 4096) с произвольного бита: код, схема стандарта, данные — сами."""
        м = ldpc_std.матрица("ar4ja-10240-4096")
        схема = ldpc_std.схема("ar4ja-10240-4096")
        данные = np.random.default_rng(1).integers(0, 2, (10, 4096)).astype(np.uint8)
        слова = систематически(м, данные)
        поток = передать(слова, схема, доля=0.003, сдвиг=555)
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("по матрице «ar4ja-10240-4096»", найдено.что)
        self.assertIn("по схеме стандарта", найдено.что)
        self.assertEqual(найдено.свойства["начало"], 555)
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))

    def test_dvb_s2_8psk_с_перемежением(self):
        """DVB-S2 (16200, 9720) — скорость 3/5, у 8PSK биты перемежены в порядке 210: без блока, с
        произвольного бита — код, перемежение и данные находятся сами; вручную — слоем."""
        м = ldpc_std.матрица("dvb-s2-16200-9720")
        данные = np.random.default_rng(6).integers(0, 2, (14, 9720)).astype(np.uint8)
        слова = dvb_кодировать(м, данные)
        схема = ldpc_std.схема("dvb-s2-16200-9720", "8PSK")
        поток = передать(слова, схема, доля=0.002, сдвиг=321)
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("«dvb-s2-16200-9720»", найдено.что)
        self.assertIn("перемежение бит 8PSK (210)", найдено.что)
        self.assertEqual(найдено.свойства["начало"], 321)
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))
        # Без перемежения такой поток — не этот код.
        with mock.patch.object(ldpc_std, "перемежения", return_value=[]):
            иное = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertTrue(иное is None or "dvb-s2-16200-9720" not in иное.что)
        ряд, запись = снять_вручную(поток[321:], "ldpc dvb-s2-16200-9720 перемежение 8PSK начало 0")
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(ряд)], ряд))

    def test_почти_равные_схемы_решает_декодирование(self):
        """Граф 1, Z = 24, не переданы ещё последние 200 позиций (согласование скорости), по два
        слова в блоке. «Выколоты первые 200» с началом на 200 бит позже по пробе почти так же
        хороша: сдвинутое слово кладёт 200 чужих бит лишь на проверочные позиции степени 1.
        Решает пробное декодирование: у верной схемы исправлены только ошибки линии."""
        переданы, схема, данные = hdlc_в_словах("nr-bg1-z24", выколоть_последние=200)
        найдено = ldpc.найти_по_матрицам(переданы.reshape(-1), блок=2 * схема.длина, встроенные=True)
        self.assertIn("по схеме стандарта; выколоты последние 200 переданных позиций", найдено.что)
        self.assertEqual(найдено.свойства["начало"], 0)
        выбор = найдено.подробно[1]
        self.assertIn("почти равные по проверкам схемы различены пробным декодированием", выбор)
        self.assertIn("выколоты последние 200 переданных позиций, начало 0 — 100 %", выбор)
        self.assertTrue(выбор.endswith("← выбрана"))
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))


def бюджет(профиль):
    return razbor.Бюджет(конец=time.monotonic() + 10 ** 6, профиль=razbor.ПРОФИЛИ[профиль])


class ВДереве(unittest.TestCase):
    def test_встроенные_по_профилю(self):
        """Встроенные — всегда при известном блоке, на самом потоке — в любом профиле, глубже —
        только в глубоком; загруженные — всегда."""
        x = np.zeros(10, np.uint8)

        def имена(профиль, блок, глубина):
            return [и for и, _, _ in razbor._по_матрицам(x, бюджет(профиль), блок, глубина)]
        оба, свои = ["LDPC по загруженным и встроенным матрицам"], ["LDPC по загруженным матрицам"]
        with mock.patch.object(ldpc, "список", return_value=[]):
            for профиль in ("быстро", "обычно", "глубоко"):
                with self.subTest(профиль=профиль):
                    self.assertEqual(оба, имена(профиль, None, 0))
                    self.assertEqual(оба, имена(профиль, 3200, 2))
            self.assertEqual([], имена("быстро", None, 1))
            self.assertEqual([], имена("обычно", None, 1))
            self.assertEqual(оба, имена("глубоко", None, 3))
        with mock.patch.object(ldpc, "список", return_value=[{"имя": "своя"}]):
            self.assertEqual(свои, имена("обычно", None, 1))

    def test_детектор_с_блоком_ищет_схему(self):
        """Детектор дерева отдаёт блок автомату: согласование скорости находится перебором схем."""
        переданы, схема, данные = hdlc_в_словах("nr-bg2-z16", выколоть_последние=100)
        поток = переданы.reshape(-1)
        with mock.patch.object(ldpc, "список", return_value=[]):
            (_, найти, шаг), = razbor._по_матрицам(поток, бюджет("быстро"), 4 * схема.длина, 1)
            найдено = найти()
        self.assertEqual(шаг, "после снятия LDPC")
        self.assertIn("«nr-bg2-z16»", найдено.что)
        self.assertIn("выколоты последние 100 переданных позиций", найдено.что)

    def test_сплошной_поток_наверху(self):
        """Слова 5G NR подряд, без кадра и синхрослова, с произвольного бита: код — сам, дальше —
        кадры HDLC и пакеты IP."""
        переданы, _, _ = hdlc_в_словах("nr-bg2-z16")
        поток = np.concatenate([np.random.default_rng(5).integers(0, 2, 123).astype(np.uint8),
                                переданы.reshape(-1)])
        р = razbor.разобрать(данные=в_байты(поток), имя="nr.bin", профиль="быстро")
        отчёт = р.отчёт(20000)
        self.assertEqual([н.уровень for н in р.находки][:3], ["код", "канальный", "сетевой"], отчёт)
        self.assertIn("по матрице «nr-bg2-z16»", р.находки[0].что)
        self.assertGreaterEqual(р.находки[1].уверенность, 0.99, отчёт)

    def test_в_кадре_модема(self):
        """Кадр: ASM и четыре слова 5G NR, у каждого не переданы ещё последние 100 позиций.
        Цикл даёт длину блока — и по ней находится схема согласования скорости."""
        переданы, схема, _ = hdlc_в_словах("nr-bg2-z16", выколоть_последние=100)
        синхро = np.unpackbits(np.frombuffer(bytes.fromhex("1ACFFC1D"), np.uint8))
        кадров = len(переданы) // 4
        биты = np.concatenate([np.concatenate([синхро, переданы[4 * i:4 * i + 4].reshape(-1)])
                               for i in range(кадров)])
        р = razbor.разобрать(данные=в_байты(биты), имя="nr-кадр.bin", профиль="обычно")
        отчёт = р.отчёт(20000)
        self.assertEqual([н.уровень for н in р.находки][:4], ["цикл", "код", "канальный", "сетевой"], отчёт)
        self.assertIn("«nr-bg2-z16»", р.находки[1].что)
        self.assertIn("выколоты последние 100 переданных позиций", р.находки[1].что)
        self.assertGreaterEqual(р.находки[2].уверенность, 0.99, отчёт)


if __name__ == "__main__":
    unittest.main()
