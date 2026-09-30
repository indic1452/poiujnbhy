"""DVB-S2X целиком: VL-SNR (укорочение и выкалывание) и перемежение бит MODCOD S2X — по первоисточнику.

Эталон — не модули проекта, а свой кодер по тексту стандарта:

- таблицы адресов — из gr-dvbs2 (lib/ldpc_bb_impl.cc, строки «число, адреса…»; встроенные коды
  проекта построены по xdsopl/LDPC — другой источник), кодирование — по EN 302 307-1, 5.3.2
  (p_{(x + (m mod 360)·q) mod (n−k)} ^= i_m, затем p_i ^= p_{i−1});
- VL-SNR — по 5.5.2.6 EN 302 307-2: Xs, P, Xp читаются из текста табл. 19a; первые Xs бит данных —
  нули и не передаются, не передаются p0, pP, p2P, … — всего Xp;
- перемежение — по 5.3.3 EN 302 307-1 и табл. 9a/9b EN 302 307-2 (порядок столбцов — из текста):
  запись по столбцам, чтение по строкам в порядке столбцов шаблона.

Ошибки линии → данные бит в бит. Нет папки источников — тесты пропускаются.
"""

import re
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import ldpc, ldpc_std
from reportgen.potok.razbor import снять_вручную

ИСТОЧНИКИ = Path(__file__).resolve().parents[1] / "istochniki" / "ldpc"
есть_источники = unittest.skipUnless(ИСТОЧНИКИ.exists(), "нет istochniki/ldpc")


def текст_s2x() -> str:
    return (ИСТОЧНИКИ / "standarty" / "ETSI_EN_302_307-2_v1.4.1_DVB-S2X.pdf.txt").read_text(
        encoding="utf-8", errors="replace")


def таблица_gr(n: int, k: int, вид: str = "") -> list[list[int]]:
    """Таблица адресов gr-dvbs2 для (n, k): ряды адресов по группам из 360 бит данных."""
    т = (ИСТОЧНИКИ / "otkrytyj_kod" / "gr-dvbs2" / "lib" / "ldpc_bb_impl.cc").read_text()
    буква = {64800: "N", 16200: "S", 32400: "M"}[n]
    for м in re.finditer(r"ldpc_tab_(\w+?)" + буква + r"\[(\d+)\]\[(\d+)\]=\s*\{(.*?)\n    \};", т, re.S):
        if int(м.group(2)) * 360 != k or (вид and м.group(1) != вид):
            continue
        ряды = []
        for с in re.findall(r"\{([^{}]*)\}", м.group(4)):
            v = [int(x) for x in с.split(",") if x.strip()]
            ряды.append(v[1:1 + v[0]])
        return ряды
    raise KeyError((n, k, вид))


def кодировать(u: np.ndarray, ряды: list[list[int]], n: int) -> np.ndarray:
    """EN 302 307-1, 5.3.2: накопители чётности по таблице адресов, затем p_i ^= p_{i−1}."""
    k = len(u)
    q = (n - k) // 360
    p = np.zeros(n - k, np.uint8)
    for m in range(k):
        if u[m]:
            for x in ряды[m // 360]:
                p[(x + (m % 360) * q) % (n - k)] ^= 1
    return np.concatenate([u, np.bitwise_xor.accumulate(p)])


def таблица_19a() -> dict[str, tuple[int, int, int]]:
    """MODCOD VL-SNR → (Xs, P, Xp) из текста табл. 19a."""
    т = текст_s2x()
    кус = т[т.index("Table 19a: Shortening/Puncturing"):т.index("Table 19b")]
    итог = {}
    for мод, ид, кадр, sf2, xs, p, xp in re.findall(
            r"^(?:\S{1,3}/2 )?(BPSK|QPSK) (\d+/\d+) (normal|medium|short)( SF2)? *\n(\d+(?: \d+)?) *\n(\d+) *\n"
            r"(\d+(?: \d+)?) *$", кус, re.M):
        итог[f"{ид} {кадр}{sf2}"] = tuple(int(x.replace(" ", "")) for x in (xs, p, xp))
    return итог


def шаблон_9(модкод: str, кадр: str) -> str:
    """Порядок столбцов MODCOD из текста табл. 9a (normal) или 9b (short)."""
    т = текст_s2x()
    if кадр == "normal":
        кус = т[т.index("Table 9a: Bit Interleaver Patterns"):т.index("Table 9b: Bit Interleaver Patterns")]
    else:
        кус = т[т.index("Table 9b: Bit Interleaver Patterns"):]
        кус = кус[:кус.index("5.4")]
    вид, ид = модкод.rsplit(" ", 1)
    м = re.search(r"^" + re.escape(вид) + r"(?: APSK)?,? " + re.escape(ид) + r" *\n(\d{3,8}) *$", кус, re.M)
    return м.group(1)


def перемежить(слово: np.ndarray, столбцы: str) -> np.ndarray:
    """EN 302 307-1, 5.3.3: запись по столбцам (столбец j — n/c бит подряд), чтение по строкам;
    в строке — столбцы в порядке шаблона (EN 302 307-2, 5.3.3: «102» — сперва столбец 1)."""
    c = len(столбцы)
    строк = len(слово) // c
    таблица = слово.reshape(c, строк)                  # столбец j — строка массива j
    return таблица[[int(x) for x in столбцы], :].T.reshape(-1)


# (имя кода проекта, MODCOD в табл. 19a, n, k кода, вид таблицы gr-dvbs2 — если k повторяется)
VL = [("dvb-s2x-vl-64800-2-9", "2/9 normal", 64800, 14400, ""),
      ("dvb-s2x-vl-32400-1-5", "1/5 medium", 32400, 6480, ""),
      ("dvb-s2x-vl-32400-11-45", "11/45 medium", 32400, 7920, ""),
      ("dvb-s2x-vl-32400-1-3", "1/3 medium", 32400, 10800, ""),
      ("dvb-s2x-vl-16200-1-5-sf2", "1/5 short SF2", 16200, 3240, "1_4"),
      ("dvb-s2x-vl-16200-11-45-sf2", "11/45 short SF2", 16200, 3960, ""),
      ("dvb-s2x-vl-16200-1-5", "1/5 short", 16200, 3240, "1_4"),
      ("dvb-s2x-vl-16200-4-15", "4/15 short", 16200, 4320, ""),
      ("dvb-s2x-vl-16200-1-3", "1/3 short", 16200, 5400, "1_3")]


def vl_передать(u: np.ndarray, ряды, n: int, xs: int, P: int, xp: int) -> np.ndarray:
    """VL-SNR по 5.5.2.6: Xs нулей перед данными (не передаются), не передаются p0, pP, … (Xp)."""
    слово = кодировать(np.concatenate([np.zeros(xs, np.uint8), u]), ряды, n)
    k = len(u) + xs
    убрать = np.zeros(n, bool)
    убрать[:xs] = True
    убрать[k + P * np.arange(xp)] = True
    return слово[~убрать]


@есть_источники
class VLSNR(unittest.TestCase):
    def test_параметры_по_табл_19a(self):
        т19 = таблица_19a()
        self.assertEqual(9, len(т19))
        for имя, модкод, n, k, _ in VL:
            with self.subTest(имя=имя):
                xs, P, xp = т19[модкод]
                с = ldpc_std.схема(имя)
                self.assertEqual(n, с.матрица.n)
                self.assertEqual(list(range(xs)), с.укорочены.tolist())
                self.assertEqual((k + P * np.arange(xp)).tolist(), с.выколоты.tolist())
                self.assertEqual(n - xs - xp, с.длина)
                self.assertEqual(n - xs - xp, ldpc_std.длина_в_потоке(имя))
                # Данные — позиции Xs … k − 1 (первые Xs — известные нули).
                self.assertEqual(list(range(xs, k)), ldpc.информационные(с.матрица).tolist())
                э = next(э for э in ldpc_std.список() if э["имя"] == имя)
                self.assertEqual(str(Fraction(k - xs, n - xs - xp)), э["скорость"])
                self.assertEqual((n, k - xs), (э["n"], э["k"]))
        # 2/9 нормального кадра — 61 560 бит в канале (табл. 19b).
        self.assertIn("61 560", текст_s2x()[текст_s2x().index("Table 19b"):][:400])

    def test_тип_в_окне(self):
        """«DVB-S2X VL-SNR» — один тип, сразу за «DVB-S2X medium», девять MODCOD табл. 19a."""
        from reportgen.potok import ldpc_katalog
        типы = [т["тип"] for т in ldpc_katalog.типы()]
        self.assertEqual(1, типы.count("DVB-S2X VL-SNR"))
        self.assertEqual("DVB-S2X medium (32400)", типы[типы.index("DVB-S2X VL-SNR") - 1])
        т = next(т for т in ldpc_katalog.типы() if т["тип"] == "DVB-S2X VL-SNR")
        self.assertEqual([имя for имя, *_ in VL], [с["коды"][0] for с in т["скорости"]])
        self.assertIn("QPSK 2/9 normal", т["скорости"][0]["подпись"])

    def test_снятие_каждого_с_ошибками(self):
        rng = np.random.default_rng(19)
        т19 = таблица_19a()
        for имя, модкод, n, k, вид in VL:
            with self.subTest(имя=имя):
                xs, P, xp = т19[модкод]
                ряды = таблица_gr(n, k, вид)
                слов = 2 if n == 64800 else 3
                данные = rng.integers(0, 2, (слов, k - xs)).astype(np.uint8)
                поток = np.concatenate([vl_передать(u, ряды, n, xs, P, xp) for u in данные])
                поток[rng.choice(len(поток), 12 * слов, replace=False)] ^= 1
                ряд, подробно = ldpc.снять(поток, ldpc_std.схема(имя), начало=0)
                self.assertTrue(np.array_equal(данные.reshape(-1), ряд), (имя, подробно))

    def test_автомат_находит_1_3_short(self):
        rng = np.random.default_rng(20)
        xs, P, xp = таблица_19a()["1/3 short"]
        ряды = таблица_gr(16200, 5400, "1_3")
        данные = rng.integers(0, 2, (4, 5400)).astype(np.uint8)
        поток = np.concatenate([rng.integers(0, 2, 77).astype(np.uint8)]
                               + [vl_передать(u, ряды, 16200, xs, P, xp) for u in данные])
        поток[rng.choice(np.arange(77, len(поток)), 20, replace=False)] ^= 1
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("«dvb-s2x-vl-16200-1-3»", найдено.что)
        self.assertEqual(77, найдено.свойства["начало"])
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))


@есть_источники
class ПеремежениеS2X(unittest.TestCase):
    # (код проекта, MODCOD табл. 9a/9b, кадр, n, k, вид таблицы gr-dvbs2)
    СЛУЧАИ = [("dvb-s2x-16200-9360", "4+12APSK 26/45", "short", 16200, 9360, ""),
              ("dvb-s2x-16200-11520", "4+12+16rbAPSK 32/45", "short", 16200, 11520, ""),
              ("dvb-s2-16200-9720", "4+12APSK 3/5", "short", 16200, 9720, "3_5"),
              ("dvb-s2x-16200-7560", "8PSK 7/15", "short", 16200, 7560, ""),
              ("dvb-s2x-64800-46080", "256APSK 128/180", "normal", 64800, 46080, "128_180"),
              ("dvb-s2x-64800-50400", "8+16+20+20APSK 7/9", "normal", 64800, 50400, "7_9")]

    def test_шаблоны_по_тексту_табл_9(self):
        for имя, модкод, кадр, *_ in self.СЛУЧАИ:
            with self.subTest(модкод=модкод):
                вид = модкод.rsplit(" ", 1)[0]
                self.assertEqual(шаблон_9(модкод, кадр), ldpc_std.столбцы_перемежения(имя, вид))
                self.assertIn((вид, шаблон_9(модкод, кадр)), ldpc_std.перемежения(имя))
        # Полный перечень: 44 MODCOD (46 в таблицах без двух 128APSK), вид у кода — один раз.
        все = ldpc_std._s2x()["перемежения"]
        self.assertEqual(44, len(все))
        self.assertEqual(["128APSK 135/180", "128APSK 140/180"], ldpc_std._s2x()["не_встроено"])
        for п in все:
            self.assertEqual(шаблон_9(п["модкод"], п["кадр"]), п["столбцы"], п)
            виды = [в for в, _ in ldpc_std.перемежения(п["код"])]
            self.assertEqual(len(виды), len(set(виды)), п["код"])
        # S2 — прежние: 8PSK 3/5 — 210, у кодов S2X их нет.
        self.assertEqual("210", ldpc_std.столбцы_перемежения("dvb-s2-64800-38880", "8PSK"))
        self.assertNotIn("16APSK", dict(ldpc_std.перемежения("dvb-s2x-16200-9360")))
        self.assertEqual([], ldpc_std.перемежения("dvb-t2-16200-9720"))

    def test_бит_на_символ(self):
        for вид, m in (("8PSK", 3), ("16APSK", 4), ("4+12APSK", 4), ("8+8APSK", 4), ("2+4+2APSK", 3),
                       ("32APSK", 5), ("4+12+16rbAPSK", 5), ("4+8+4+16APSK", 5), ("8+16+20+20APSK", 6),
                       ("16+16+16+16APSK", 6), ("4+12+20+28APSK", 6), ("256APSK", 8)):
            self.assertEqual(m, ldpc_std.бит_на_символ(вид), вид)
        for плохо in ("QPSK", "12APSK", "4+13APSK", "ABC"):
            with self.assertRaises(ValueError):
                ldpc_std.бит_на_символ(плохо)

    def test_снятие_с_перемежением_s2x(self):
        rng = np.random.default_rng(9)
        for имя, модкод, кадр, n, k, вид_gr in self.СЛУЧАИ:
            with self.subTest(модкод=модкод):
                столбцы = шаблон_9(модкод, кадр)
                ряды = таблица_gr(n, k, вид_gr)
                слов = 2 if n == 64800 else 3
                данные = rng.integers(0, 2, (слов, k)).astype(np.uint8)
                поток = np.concatenate([перемежить(кодировать(u, ряды, n), столбцы) for u in данные])
                поток[rng.choice(len(поток), 10 * слов, replace=False)] ^= 1
                вид = модкод.rsplit(" ", 1)[0]
                ряд, подробно = ldpc.снять(поток, ldpc_std.схема(имя, вид), начало=0)
                self.assertTrue(np.array_equal(данные.reshape(-1), ряд), (модкод, подробно))
                # Вручную слоем — вид в нижнем регистре, порядок — по стандарту кода.
                ряд2, _ = снять_вручную(поток, f"ldpc {имя} перемежение {вид.lower()} начало 0")
                self.assertTrue(np.array_equal(данные.reshape(-1), ряд2), модкод)

    def test_автомат_находит_перемежение_s2x(self):
        """Короткий кадр 4+12APSK 26/45 (порядок 2130): код, перемежение и начало — сами."""
        rng = np.random.default_rng(10)
        ряды = таблица_gr(16200, 9360)
        данные = rng.integers(0, 2, (4, 9360)).astype(np.uint8)
        столбцы = шаблон_9("4+12APSK 26/45", "short")
        поток = np.concatenate([rng.integers(0, 2, 150).astype(np.uint8)]
                               + [перемежить(кодировать(u, ряды, 16200), столбцы) for u in данные])
        поток[rng.choice(np.arange(150, len(поток)), 20, replace=False)] ^= 1
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True)
        self.assertIsNotNone(найдено)
        self.assertIn("«dvb-s2x-16200-9360»", найдено.что)
        self.assertIn("перемежение бит 4+12APSK (2130)", найдено.что)
        self.assertEqual(150, найдено.свойства["начало"])
        self.assertTrue(np.array_equal(данные.reshape(-1)[:len(найдено.дальше)], найдено.дальше))

    def test_файл_отдела_с_перемежением_s2x(self):
        """Примечание «# перемежение: 4+12APSK 2130» в файле папки ldpc задаёт схему."""
        import tempfile
        л = ldpc
        ряды = таблица_gr(16200, 9360)
        текст = "# перемежение: 4+12APSK 2130\nn = 16200\nk = 9360\n" + "\n".join(" ".join(map(str, р)) for р in ряды)
        with tempfile.TemporaryDirectory() as d:
            путь = Path(d) / "s2x_26_45.txt"
            путь.write_text(текст, encoding="utf-8")
            запись = л._из_файла(путь)
        self.assertNotIn("ошибка", запись, запись)
        rng = np.random.default_rng(12)
        данные = rng.integers(0, 2, (2, 9360)).astype(np.uint8)
        поток = np.concatenate([перемежить(кодировать(u, ряды, 16200), "2130") for u in данные])
        ряд, _ = ldpc.снять(поток, запись["схема"], начало=0)
        self.assertTrue(np.array_equal(данные.reshape(-1), ряд))
        # Без порядка столбцов — по порядку: у 4+12APSK четыре столбца «0123».
        with tempfile.TemporaryDirectory() as d:
            путь = Path(d) / "s2x_bez_poryadka.txt"
            путь.write_text(текст.replace("4+12APSK 2130", "4+12APSK"), encoding="utf-8")
            запись = л._из_файла(путь)
        self.assertNotIn("ошибка", запись, запись)
        поток = np.concatenate([перемежить(кодировать(u, ряды, 16200), "0123") for u in данные])
        ряд, _ = ldpc.снять(поток, запись["схема"], начало=0)
        self.assertTrue(np.array_equal(данные.reshape(-1), ряд))


if __name__ == "__main__":
    unittest.main()
