"""Семейства LDPC из первоисточников: независимые кодеры по тексту источников против встроенных кодов.

Эталон в каждом тесте — не реализация проекта, а первоисточник из ``istochniki/ldpc/``:

- F-LDPC (Datum FlexLDPC): кодер собран прямо из таблиц патента US 7,673,213 (текст патента —
  решётки таблиц 37 и 41 как автоматы «состояние, вход → выход, состояние», перемежитель по
  абзацу (137) с таблицами 38–39, выкалывание по таблице 42, перемежение 1367 по абзацу (149));
- AR4JA и C2 CCSDS — alist, выгруженные программой ldpc-toolbox; TC — порождающая W из текста
  CCSDS 231.0-B-4; WiMAX — базовые матрицы и правило сдвига FEC (dshekhalev, RTL на SystemVerilog)
  и alist wimax_ldpc_lib; ATSC 3.0 — кодер по шагам A/322 6.1.3.1/6.1.3.2 с таблицами gr-atsc3;
  DTMB — alist dtmb-sdr.

Нет папки источников (пакет без репозитория) — такие тесты пропускаются.
"""

import re
import unittest
from pathlib import Path

import numpy as np

import _bootstrap  # noqa: F401
from reportgen.potok import gf2, ldpc, ldpc_flex, ldpc_std

ИСТОЧНИКИ = Path(__file__).resolve().parents[1] / "istochniki" / "ldpc"
есть_источники = unittest.skipUnless(ИСТОЧНИКИ.exists(), "нет istochniki/ldpc")


def таблица_патента(номер: int) -> str:
    текст = (ИСТОЧНИКИ / "patenty" / "US7673213.txt").read_text(encoding="utf-8")
    return next(с for с in текст.split("\n") if f"TABLE-US-{номер:05d}" in с)


def alist(путь: Path) -> tuple[int, int, set]:
    """Пары (строка, столбец) единиц H из файла alist (дополненного нулями или ровно по весу)."""
    ч = [int(x) for x in путь.read_text().split()]
    n, m, наиб = ч[0], ч[1], ч[2]
    веса = ч[4:4 + n]
    остаток = ч[4 + n + m:]
    дополнены = len(остаток) >= n * наиб + m * ч[3]
    пары, место = set(), 0
    for c in range(n):
        ширина = наиб if дополнены else веса[c]
        пары |= {(x - 1, c) for x in остаток[место:место + ширина] if x}
        место += ширина
    return n, m, пары


def пары_матрицы(м) -> set:
    столбцы, границы = м.подряд()
    return set(zip(np.repeat(np.arange(м.m), np.diff(границы)).tolist(), столбцы.tolist(), strict=True))


def синдром_ноль(м, слово) -> bool:
    return not any(int(слово[с].sum()) % 2 for с in м.строки)


# -- F-LDPC: кодер по тексту патента ------------------------------------------------------------

class ПатентFLDPC:
    """Кодер 1500 US 7,673,213, собранный из таблиц текста патента (без модулей проекта)."""

    def __init__(self):
        # Таблица 37: внешняя решётка «состояние, (b¹b²) → (c¹c²c³c⁴), новое состояние».
        self.внешняя = {(int(s), вх): (вых, int(нс)) for s, вх, вых, нс in
                        re.findall(r"(\d)\t(\d\d)\t(\d{4})\t(\d)", таблица_патента(37))}
        # Таблица 41: внутренняя решётка «состояние, (d¹d²) → (p¹p²), новое состояние».
        self.внутренняя = {(int(s), вх): (вых, int(нс)) for s, вх, вых, нс in
                           re.findall(r"(\d)\t(\d\d)\t(\d\d)\t(\d)", таблица_патента(41))}
        т38 = таблица_патента(38)
        self.дизер = {(k, в): [int(x) for x in ч.replace(",", " ").split()]
                      for k, в, ч in re.findall(r"(All|\d+)\s+([rw])\s+\(([\d,\s]+)\)", т38)}
        self.ps = {int(k): (int(p), int(s)) for k, p, s in re.findall(r"(\d+)\t(\d+)\t(\d+)", таблица_патента(39))}
        self.шаблоны = {int(a): ш for a, ш in re.findall(r"(\d+)/16\t([01]{32})", таблица_патента(42))}

    def кодировать(self, u: list[int], J: int, P: int, канал: bool = False) -> list[int]:
        K = len(u)
        пары = [f"{u[2 * i]}{u[2 * i + 1]}" for i in range(K // 2)]
        # Абзац (136): первая пара — начальное состояние (S = b² для 2 состояний), подаётся в конце.
        s = int(пары[0][1])
        c = []
        for пара in пары[1:] + пары[:1]:
            вых, s = self.внешняя[(s, пара)]
            c += [int(x) for x in вых]
        # Абзац (137): v_a(i) = v_in(I_a(i)), v_b(i) = v_a(I_b(i)), v_out(i) = v_b(I_c(i)).
        r, w = self.дизер[("All", "r")], self.дизер[(str(K), "w")]
        p, s0 = self.ps[K]
        n = 2 * K
        va = [c[64 * (i // 64) + r[i % 64]] for i in range(n)]
        vb = [va[(s0 + i * p) % n] for i in range(n)]
        v = [vb[64 * (i // 64) + w[i % 64]] for i in range(n)]
        # Абзац (141): XOR по J бит, остаток < J — одним битом.
        d = [sum(v[i:i + J]) % 2 for i in range(0, n, J)]
        if len(d) % 2:
            d.append(0)                            # внутренняя решётка берёт пары; лишний бит отброшу
            лишний = True
        else:
            лишний = False
        s, чётность = 0, []
        for i in range(0, len(d), 2):
            вых, s = self.внутренняя[(s, f"{d[i]}{d[i + 1]}")]
            чётность += [int(x) for x in вых]
        if лишний:
            чётность.pop()
        шаблон = self.шаблоны[P]
        чётность = [x for i, x in enumerate(чётность) if шаблон[i % 32] == "1"]
        vp = list(u) + чётность
        if канал:
            M = len(vp)
            vp = [vp[(1367 * i) % M] for i in range(M)]
        return vp


@есть_источники
class FLDPCПоПатенту(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.патент = ПатентFLDPC()

    def test_таблицы_данных_равны_тексту_патента(self):
        т = ldpc_flex.таблицы()
        self.assertEqual(т["r"], self.патент.дизер[("All", "r")])
        for K in ldpc_flex.РАЗМЕРЫ:
            self.assertEqual(т["w"][str(K)], self.патент.дизер[(str(K), "w")])
            self.assertEqual(tuple(т["p_s"][str(K)]), self.патент.ps[K])
        self.assertEqual({int(a): ш for a, ш in т["выкалывание"].items()}, self.патент.шаблоны)

    def test_кодер_равен_патентному_и_H_сходится(self):
        rng = np.random.default_rng(5)
        for K in ldpc_flex.РАЗМЕРЫ:
            for J, P in ((2, 16), (4, 8), (6, 16), (8, 12), (14, 16), (16, 8), (20, 16), (32, 16)):
                with self.subTest(K=K, J=J, P=P):
                    u = rng.integers(0, 2, K).astype(np.uint8)
                    for канал in (False, True):
                        эталон = np.array(self.патент.кодировать(u.tolist(), J, P, канал), dtype=np.uint8)
                        self.assertTrue(np.array_equal(ldpc_flex.кодировать(u, J, P, канал=канал), эталон))
                    # Полное слово (без выкалывания) удовлетворяет всем проверкам H.
                    полное = np.array(self.патент.кодировать(u.tolist(), J, 16), dtype=np.uint8)
                    self.assertTrue(синдром_ноль(ldpc_flex.матрица(K, J), полное))

    def test_все_режимы_datum(self):
        """Все 78 разных кодов режимов Datum: схема стандарта равна патентному кодеру по длине и месту бит."""
        rng = np.random.default_rng(6)
        имена = ldpc_flex.имена_datum()
        self.assertEqual(78, len(имена))
        for имя in имена:
            K, J, P = ldpc_flex.разобрать_имя(имя)
            with self.subTest(имя=имя):
                u = rng.integers(0, 2, K).astype(np.uint8)
                передано = np.array(self.патент.кодировать(u.tolist(), J, P), dtype=np.uint8)
                с = ldpc_std.схема(имя)
                self.assertEqual(с.длина, len(передано))
                полное = np.array(self.патент.кодировать(u.tolist(), J, 16), dtype=np.uint8)
                self.assertTrue(np.array_equal(полное[с.переданы], передано))
                self.assertEqual(ldpc_std.длина_в_потоке(имя), len(передано))

    def test_скорости_режимов_datum_по_документам(self):
        """Скорость каждого варианта — ровно скорость режима из руководств Datum и MIB M7XC."""
        for режим in ldpc_flex.datum():
            for имя in режим["варианты"]:
                K, J, P = ldpc_flex.разобрать_имя(имя)
                self.assertEqual(ldpc_flex.скорость(J, P), __import__("fractions").Fraction(режим["скорость"]), имя)
        # 14/17 не ложится ни в одно (J, P) патента — вариантов нет (честно пусто).
        self.assertFalse([р for р in ldpc_flex.datum() if р["скорость"] == "14/17" and р["варианты"]])
        mib = (ИСТОЧНИКИ / "datum" / "M7XC_MIB" / "DATUM-M7XC-MODEM-MIB").read_text(errors="ignore")
        блок = mib[mib.index("rxLdpcModcod OBJECT-TYPE"):]
        скорости = {f"{a}/{b}" for a, b in re.findall(r"(\d+)x(\d+)\(", блок[:блок.index("}")])}
        self.assertEqual(скорости, {str(__import__("fractions").Fraction(с)) for с in ldpc_flex.GEN2_СКОРОСТИ})

    def test_снятие_с_ошибками_и_перемежением(self):
        rng = np.random.default_rng(7)
        for K, J, P, канал, ber in ((256, 2, 16, False, 0.004), (1024, 4, 8, True, 0.004), (4096, 16, 8, False, 0.001),
                                    (2048, 4, 12, True, 0.003)):
            with self.subTest(K=K, J=J, P=P, канал=канал):
                u = rng.integers(0, 2, (6, K)).astype(np.uint8)
                поток = np.concatenate([rng.integers(0, 2, 91).astype(np.uint8)] + [
                    np.array(self.патент.кодировать(x.tolist(), J, P, канал), dtype=np.uint8) for x in u])
                поток ^= (rng.random(len(поток)) < ber).astype(np.uint8)
                данные, подробно = ldpc.снять(поток, ldpc_std.схема(ldpc_flex.имя(K, J, P), "1367" if канал else ""))
                self.assertTrue(np.array_equal(данные.reshape(-1, K)[:5], u[:5]), подробно)
                self.assertIn("начало слова — бит 91", подробно[0])

    def test_выкалывание_8_16_равносильно_двойному_J(self):
        """Абзац (175): при аккумуляторе SPC и выкалывание «обмениваются» — (J, 8/16) и (2J, 16/16)
        дают одно и то же слово; поэтому в режимах Datum такие варианты — один код."""
        rng = np.random.default_rng(10)
        for K, J in ((256, 2), (1024, 4), (2048, 8), (4096, 16)):
            u = rng.integers(0, 2, K).tolist()
            self.assertEqual(self.патент.кодировать(u, J, 8), self.патент.кодировать(u, 2 * J, 16))
            self.assertEqual((2 * J, 16), ldpc_flex.равносильный(K, J, 8))
        u = rng.integers(0, 2, 256).tolist()
        self.assertNotEqual(self.патент.кодировать(u, 2, 12), self.патент.кодировать(u, 4, 16)[:len(u) + 192])

    def test_автомат_находит_режим_datum(self):
        rng = np.random.default_rng(8)
        u = rng.integers(0, 2, (20, 2048)).astype(np.uint8)
        # M7XC LDPC-Gen2 8/13: J = 2, выкалывание 10/16.
        поток = np.concatenate([rng.integers(0, 2, 300).astype(np.uint8)] + [
            np.array(self.патент.кодировать(x.tolist(), 2, 10), dtype=np.uint8) for x in u])
        поток ^= (rng.random(len(поток)) < 0.002).astype(np.uint8)
        найдено = ldpc.найти_по_матрицам(поток, встроенные=True, бюджет=120)
        self.assertIsNotNone(найдено)
        self.assertEqual("fldpc-k2048-j2-p10", найдено.свойства["матрица"])
        self.assertEqual(300, найдено.свойства["начало"])
        self.assertTrue(np.array_equal(найдено.дальше.reshape(-1, 2048)[:19], u[:19]))

    def test_слой_с_перемежением_1367(self):
        rng = np.random.default_rng(9)
        u = rng.integers(0, 2, (5, 256)).astype(np.uint8)
        поток = np.concatenate([np.array(self.патент.кодировать(x.tolist(), 4, 16, True), dtype=np.uint8) for x in u])
        схема, начало, _ = ldpc.из_указания("ldpc fldpc-k256-j4-p16 перемежение 1367 начало 0")
        данные, _ = ldpc.снять(поток, схема, начало=начало)
        self.assertTrue(np.array_equal(данные.reshape(-1, 256), u))
        with self.assertRaises(ValueError):
            ldpc_std.схема("wifi-648-540", "1367")


# -- CCSDS ------------------------------------------------------------------------------------------

@есть_источники
class CCSDS(unittest.TestCase):
    def test_ar4ja_все_девять_равны_ldpc_toolbox(self):
        for k in (1024, 4096, 16384):
            for r, (a, b) in (("1_2", (1, 2)), ("2_3", (2, 3)), ("4_5", (4, 5))):
                with self.subTest(k=k, r=r):
                    n, m, пары = alist(ИСТОЧНИКИ / "postroeno" / "alist_ldpc_toolbox" / f"ar4ja_k{k}_r{r}.alist")
                    M = m // 3
                    имя = f"ar4ja-{n}-{k}"
                    м = ldpc_std.матрица(имя)
                    self.assertEqual((n, m), (м.n, м.m))
                    self.assertEqual(пары, пары_матрицы(м))
                    с = ldpc_std.схема(имя)
                    # 7.4.2.5: последние M позиций не передаются; скорость k/(n − M) = a/b.
                    self.assertEqual(с.выколоты.tolist(), list(range(n - M, n)))
                    self.assertEqual(k * b, с.длина * a)

    def test_ar4ja_данные_первые_k(self):
        м = ldpc_std.матрица("ar4ja-2560-1024")
        H = м.плотная()
        # Первые k столбцов — информационное множество: остальные n − k столбцов H полного ранга
        # (3M строк, ранг 3M), тогда слово однозначно достраивается по данным.
        self.assertEqual(gf2.ранг(H[:, 1024:]), gf2.ранг(H))
        self.assertEqual(1024, 2560 - gf2.ранг(H))

    def test_c2_равен_ldpc_toolbox_и_передача_8160(self):
        n, m, пары = alist(ИСТОЧНИКИ / "postroeno" / "alist_ldpc_toolbox" / "ccsds_c2_8176_7156.alist")
        self.assertEqual(пары, пары_матрицы(ldpc_std.матрица("ccsds-c2-8176-7156")))
        с = ldpc_std.схема("ccsds-c2-8160-7136")
        self.assertEqual(8160, с.длина)
        self.assertEqual(list(range(18)), с.укорочены.tolist())
        self.assertEqual(list(range(18, 7154)), ldpc.информационные(с.матрица).tolist())

    def test_c2_снятие(self):
        """Слово (8160, 7136) по 7.3.5: 18 нулей в начале не передаются, два нуля — в конце."""
        м = ldpc_std.матрица("ccsds-c2-8176-7156")
        H = м.плотная()
        G = gf2.ядро(H)                                        # базис кода (8176, 7156)
        rng = np.random.default_rng(3)
        слова = []
        for _ in range(3):
            с = (rng.integers(0, 2, len(G)) @ G % 2).astype(np.uint8)
            слова.append(с)
        # Слова с 18 нулями в начале: проекция на подпространство — решение по опорным столбцам.
        # Проще: берём коды, у которых первые 18 бит нулевые, из ядра H с первыми 18 столбцами.
        G18 = gf2.ядро(H[:, 18:])
        поток = []
        данные = []
        for _ in range(4):
            с = (rng.integers(0, 2, len(G18)) @ G18 % 2).astype(np.uint8)
            данные.append(с[:7136])
            поток.append(np.concatenate([с, [0, 0]]))
        поток = np.concatenate(поток).astype(np.uint8)
        ошибки = np.zeros(len(поток), np.uint8)
        ошибки[rng.choice(len(поток), 20, replace=False)] = 1
        итог, подробно = ldpc.снять(поток ^ ошибки, ldpc_std.схема("ccsds-c2-8160-7136"), начало=0)
        self.assertTrue(np.array_equal(итог.reshape(-1, 7136), np.array(данные)), подробно)

    def test_tc_по_порождающей_стандарта(self):
        текст = (ИСТОЧНИКИ / "standarty" / "CCSDS_231.0-B-4_TC_coding.pdf.txt").read_text(encoding="utf-8")
        for n, заголовок in ((128, "Table 4-1:  Generator Matrix for (n=128,k=64)"),
                             (512, "Table 4-2:  Generator Matrix for (n=512,k=256)")):
            with self.subTest(n=n):
                k, M = n // 2, n // 8
                кусок = текст[текст.index(заголовок):][:1200]
                ряды = re.findall(r"Row (\d+)\s*\n?\s*([0-9A-F ]+)", кусок)[:4]
                W = np.zeros((k, k), np.uint8)
                for b, (_, шестн) in enumerate(ряды):
                    v = np.array([int(x) for x in bin(int(шестн.replace(" ", ""), 16))[2:].zfill(k)], np.uint8)
                    for t in range(M):
                        W[b * M + t] = np.concatenate([np.roll(v[q * M:(q + 1) * M], t) for q in range(4)])
                G = np.concatenate([np.eye(k, dtype=np.uint8), W], axis=1)
                м = ldpc_std.матрица(f"ccsds-{n}-{k}")
                self.assertFalse(((G.astype(int) @ м.плотная().T.astype(int)) % 2).any())
                self.assertEqual(k, gf2.ранг(м.плотная()))
                self.assertEqual(list(range(k)), ldpc.информационные(м).tolist())


# -- WiMAX --------------------------------------------------------------------------------------------

@есть_источники
class WiMAX(unittest.TestCase):
    def test_все_114_по_fec_и_wimax_ldpc_lib(self):
        svh = (ИСТОЧНИКИ / "otkrytyj_kod" / "FEC" / "rtl" / "ldpc" / "ldpc_parameters.svh").read_text()
        базы = {}
        for вид in ("12", "23A", "23B", "34A", "34B", "56"):
            кус = svh[svh.index(f"Hc_{вид} = '{{"):]
            кус = кус[:кус.index("}};") + 3]
            базы[вид] = [[int(x) for x in re.findall(r"-?\d+", р)] for р in re.findall(r"'\{([-\d,\s]+)\}", кус)]
        файл = {"12": "0_5", "23A": "0_66A", "23B": "0_66B", "34A": "0_75A", "34B": "0_75B", "56": "0_83"}
        совпало = 0
        for n in range(576, 2305, 96):
            z = n // 24
            for вид, база in базы.items():
                k = n - len(база) * z
                имя = f"wimax-{n}-{k}{вид[2:].lower()}"
                with self.subTest(имя=имя):
                    # Правило FEC (get_Hb): у 2/3A — s % z, иначе (s·z)/96 целочисленно.
                    эталон = {(r * z + i, c * z + (i + (s % z if вид == "23A" else s * z // 96)) % z)
                              for r, ряд in enumerate(база) for c, s in enumerate(ряд) if s >= 0 for i in range(z)}
                    м = ldpc_std.матрица(имя)
                    self.assertEqual(эталон, пары_матрицы(м))
                    _, _, пары = alist(ИСТОЧНИКИ / "otkrytyj_kod" / "wimax_ldpc_lib" / "alist" / f"wimax_{n}_{файл[вид]}.alist")
                    if вид == "56":
                        # wimax_ldpc_lib расходится ровно в блоке (3, 0) — там 68 по yaldpc и FEC.
                        self.assertEqual({(a // z, b // z) for a, b in пары ^ эталон}, {(3, 0)})
                    else:
                        self.assertEqual(пары, эталон)
                        совпало += 1
        self.assertEqual(95, совпало)

    def test_снятие_576_288(self):
        м = ldpc_std.матрица("wimax-576-288")
        G = gf2.ядро(м.плотная())
        rng = np.random.default_rng(4)
        слова = (rng.integers(0, 2, (8, len(G))) @ G % 2).astype(np.uint8)
        поток = np.concatenate([rng.integers(0, 2, 33).astype(np.uint8), слова.reshape(-1)])
        поток[rng.choice(np.arange(33, len(поток)), 12, replace=False)] ^= 1
        _, подробно = ldpc.снять(поток, ldpc_std.схема("wimax-576-288"))
        self.assertIn("начало слова — бит 33", подробно[0])
        self.assertIn("синдром обнулился у 8 (100.0 %)", " ".join(подробно))


# -- ATSC 3.0 ------------------------------------------------------------------------------------------

def atsc_таблица(N: int, r: int) -> list[list[int]]:
    t = (ИСТОЧНИКИ / "otkrytyj_kod" / "gr-atsc3" / "lib" / "ldpc_bb_impl.cc").read_text()
    вид = "N" if N == 64800 else "S"
    м = re.search(rf"ldpc_tab_{r}_15{вид}\[(\d+)\]\[(\d+)\] = \{{(.*?)\n    \}};", t, re.S)
    ряды = []
    for с in re.findall(r"\{([^{}]*)\}", м.group(3)):
        v = [int(x) for x in с.split(",") if x.strip()]
        ряды.append(v[1:1 + v[0]])
    return ряды


def atsc_кодировать(s: np.ndarray, ряды, N: int, тип: str, M1=0, M2=0, Q=0) -> np.ndarray:
    """Кодер ATSC 3.0 по шагам A/322 6.1.3.1 (тип A) и 6.1.3.2 (тип B)."""
    K = len(s)
    lam = np.zeros(N, np.uint8)
    lam[:K] = s
    if тип == "B":
        M = N - K
        p = np.zeros(M, np.uint8)
        for k_ in range(K):
            i, l_ = k_ // 360, k_ % 360
            for x in ряды[i]:
                p[(x + Q * l_) % M] ^= s[k_]
        lam[K:] = np.bitwise_xor.accumulate(p)
        return lam
    Q1, Q2 = M1 // 360, M2 // 360
    p = np.zeros(M1 + M2, np.uint8)

    def адрес(x, m_):
        return (x + m_ * Q1) % M1 if x < M1 else M1 + (x - M1 + m_ * Q2) % M2

    for k_ in range(K):                                           # шаги i–iv
        for x in ряды[k_ // 360]:
            p[адрес(x, k_ % 360)] ^= s[k_]
    for i in range(1, M1):                                        # шаг v
        p[i] ^= p[i - 1]
    for t in range(Q1):                                           # шаг vi
        for s_ in range(360):
            lam[K + 360 * t + s_] = p[Q1 * s_ + t]
    for j in range(M1):                                           # шаг vii
        for x in ряды[K // 360 + j // 360]:
            p[адрес(x, j % 360)] ^= lam[K + j]
    for t in range(Q2):                                           # шаг viii
        for s_ in range(360):
            lam[K + M1 + 360 * t + s_] = p[M1 + Q2 * s_ + t]
    return lam


@есть_источники
class ATSC3(unittest.TestCase):
    ПАРАМЕТРЫ_A = {(64800, 2): (1800, 54360), (64800, 3): (1800, 50040), (64800, 4): (1800, 45720),
                   (64800, 5): (1440, 41760), (64800, 7): (1080, 33480), (16200, 2): (3240, 10800),
                   (16200, 3): (1080, 11880), (16200, 4): (1080, 10800), (16200, 5): (720, 10080)}   # табл. 6.5, 6.6
    Q_B = {6: (108, 27), 7: (None, 24), 8: (84, 21), 9: (72, 18), 10: (60, 15), 11: (48, 12), 12: (36, 9), 13: (24, 6)}

    def test_все_24_кода_по_шагам_стандарта(self):
        rng = np.random.default_rng(11)
        for N in (64800, 16200):
            for r in range(2, 14):
                with self.subTest(N=N, r=r):
                    K = N * r // 15
                    ряды = atsc_таблица(N, r)
                    s = rng.integers(0, 2, K).astype(np.uint8)
                    if (N, r) in self.ПАРАМЕТРЫ_A:
                        M1, M2 = self.ПАРАМЕТРЫ_A[(N, r)]
                        слово = atsc_кодировать(s, ряды, N, "A", M1, M2)
                    else:
                        слово = atsc_кодировать(s, ряды, N, "B", Q=self.Q_B[r][0 if N == 64800 else 1])
                    м = ldpc_std.матрица(f"atsc3-{N}-{K}")
                    self.assertTrue(синдром_ноль(м, слово))
                    self.assertEqual(K, len(ldpc.информационные(м)))


# -- DTMB -----------------------------------------------------------------------------------------------

@есть_источники
class DTMB(unittest.TestCase):
    def test_равен_alist_и_данные_после_чётности(self):
        for номер, k in ((1, 3048), (2, 4572), (3, 6096)):
            with self.subTest(k=k):
                n, m, пары = alist(ИСТОЧНИКИ / "otkrytyj_kod" / "dtmb-sdr" / "python" / "dtmb" / "data" / f"dtmb_ldpc_rate{номер}.alist")
                м = ldpc_std.матрица(f"dtmb-7488-{k}")
                self.assertEqual(пары, пары_матрицы(м))
                self.assertEqual(list(range(m, n)), ldpc.информационные(м).tolist())
                self.assertEqual(7488, ldpc_std.схема(f"dtmb-7488-{k}").длина)

    def test_снятие_rate3(self):
        м = ldpc_std.матрица("dtmb-7488-6096")
        H = м.плотная()
        # Данные — последние k позиций: проверочные достраиваются по опорным первым m столбцам.
        rng = np.random.default_rng(12)
        приведённая, опорные = gf2.привести(gf2.упаковать(H), м.n)
        self.assertEqual(list(range(м.m)), опорные)
        R = gf2.распаковать(приведённая, м.n)
        слова, данные = [], []
        for _ in range(3):
            d = rng.integers(0, 2, м.n - м.m).astype(np.uint8)
            слово = np.concatenate([(R[:, м.m:].astype(int) @ d % 2).astype(np.uint8), d])
            self.assertTrue(синдром_ноль(м, слово))
            слова.append(слово[5:])
            данные.append(d)
        поток = np.concatenate(слова)
        поток[rng.choice(len(поток), 15, replace=False)] ^= 1
        итог, _ = ldpc.снять(поток, ldpc_std.схема("dtmb-7488-6096"), начало=0)
        self.assertTrue(np.array_equal(итог.reshape(3, -1), np.array(данные)))


if __name__ == "__main__":
    unittest.main()
