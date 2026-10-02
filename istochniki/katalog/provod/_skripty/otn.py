"""Оптический транспорт: ITU-T G.975, G.975.1 (супер-FEC I.2–I.9), G.709 (OTUk), G.709.1/.2/.3/.4/.5 (FlexO, OTU4-SC, FlexO-LR),
G.707 (SDH in-band FEC), OIF 400ZR/800ZR/800LR/1600ZR. Все многочлены сверяются вычислением (минимальные многочлены, примитивность,
степени), таблицы (перестановки, проверочные матрицы) собираются программно из текста источника."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "Оптический транспорт ITU-T (G.975/G.709/G.707)"
SEMO = "OIF (400ZR/800ZR/800LR/1600ZR)"
OTN_PROJ = "ЕСТЬ в проекте: otn.py — кадр OTUk, скремблер, синдромы и исправление RS(255,239) по 16 подстрокам"


def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


def rang_gf2(stroki):
    basis = {}; r = 0
    for s in stroki:
        while s:
            h = s.bit_length() - 1
            if h in basis: s ^= basis[h]
            else: basis[h] = s; r += 1; break
    return r


def sistemnaya_P(H_stolbcy, nrow, prav):
    """H задана столбцами (целые по nrow бит). Столбцы `prav` (индексы) — проверочные. Возвращает P (для каждого
    информационного столбца — целое из nrow проверочных бит): c_prav = Σ c_info · P."""
    B = [H_stolbcy[j] for j in prav]
    # обратная к B над GF(2): решаем B·x = v
    n = nrow; M = [[(B[c] >> (n - 1 - r)) & 1 for c in range(n)] + [1 if r == k else 0 for k in range(n)] for r in range(n)]
    for c in range(n):
        piv = next(r for r in range(c, n) if M[r][c])
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[r][c]: M[r] = [a ^ b for a, b in zip(M[r], M[c])]
    Binv = [row[n:] for row in M]
    info = [j for j in range(len(H_stolbcy)) if j not in set(prav)]
    P = []
    for j in info:
        v = [(H_stolbcy[j] >> (n - 1 - r)) & 1 for r in range(n)]
        x = [sum(Binv[r][k] & v[k] for k in range(n)) & 1 for r in range(n)]
        P.append(int("".join(map(str, x)), 2))
    return info, P


# ================= G.975 =================
T975 = Tekst("istochniki/itu/T-REC-G.975-200010-I.pdf")
s975 = T975.odna(r"where α is a root of the binary primitive polynomial x8 \+ x4 \+ x3 \+ x2 \+ 1")[0]
s975i = T975.odna(r"Given the interleaving to depth \"n\" of RS\(255,239\) codes")[0]
assert primitivnyj(0x11D) and len(rs_g(0x11D, 0, 16)) == 17
rec("ITU-T G.975 RS(255,239) (подводные линии, перемежение глубины n)", SEM, "РС",
    {"поле": "GF(2^8), x^8 + x^4 + x^3 + x^2 + 1", "g(z)": "∏_{i=0}^{15} (z − α^i)", "n,k,t": "255, 239, 8", "перемежение": "n кодеков побайтно, кадр FEC 2040·n бит",
     "отображение байта": "(d7…d0) ↔ d7·α^7 + … + d0"},
    "подводные ВОЛС (STM-16), основа G.709 и 1G-EPON", [T975.ist(s975, "6.2"), T975.ist(s975i, "6.3 перемежение")],
    "многочлен примитивен (проверено); тот же код в G.709 Annex A, G.975.1 I.2.2.1, IEEE 802.3 Cl.65 — тексты совпадают", OTN_PROJ)

# ================= G.709 OTUk =================
T709 = Tekst("istochniki/itu/T-REC-G.709-202006-I.pdf")
sA = T709.odna(r"^Forward error correction using 16-byte interleaved RS\(255,239\) codecs")
sA = T709.naiti(r"^Forward error correction using 16-byte interleaved RS\(255,239\) codecs")[-1][0]
sX = T709.odna(r"The bytes in an OTU row belonging to FEC sub-row X are defined by: X \+ 16 × \(i − 1\)")[0]
sS = T709.odna(r"The scrambler shall be reset to \"FFFF\" \(HEX\) on the most significant bit")[0]
sSC = T709.odna(r"The OTU4 FEC area contains the Stair Case FEC codes as specified in \[ITU-T G.709.2\]")[0]
rec("ITU-T G.709 OTUk FEC: 16 перемеженных RS(255,239) на строку + кадровый скремблер", SEM, "РС (перемеженный)",
    {"код": "RS(255,239) как G.975", "перемежение": "строка OTU 4080 байт → 16 подстрок; подстрока X: байты X + 16·(i−1), i = 1…255; проверочные — байты 240…255 подстроки (столбцы 3825–4080)",
     "кадр": "4 строки × 4080 байт; FAS не скремблируется", "скремблер": "x^16 + x^12 + x^3 + x + 1 (рис. 11-3), сброс в FFFF на MSB байта MFAS, после FEC",
     "OTU4": "вместо RS допускается лестничный код G.709.2 (OTU4-SC)"},
    "OTN OTU1/OTU2/OTU3/OTU4 (IrDI)", [T709.ist(sA, "Annex A"), T709.ist(sX, "подстроки"), T709.ist(sS, "11.2 скремблер"), T709.ist(sSC, "OTU4 SC-FEC")],
    "код = G.975 (совпадение текстов); раскладка подстрок — по Annex A; g(x) в Annex A — рисунок-формула, взят из G.975", OTN_PROJ)

# ================= G.975.1 =================
T1 = Tekst("istochniki/itu/T-REC-G.975.1-200402-I.pdf")
C2 = Tekst("istochniki/itu/T-REC-G.975.1-201307-I_Cor2.pdf")
C1 = Tekst("istochniki/itu/T-REC-G.975.1-200602-I_Cor1.pdf")
SEM1 = "ITU-T G.975.1 супер-FEC (DWDM подводные/наземные)"
# I.2: RS + CSOC
k, sI2 = T1.kusok(r"The super-FEC code applies following", r"Figure I\.2 shows")
csoc = {}
for m in re.finditer(r"((?:\d+ ){7})\)\s*(?:\(\s*)?(\d)\s*\(", " ".join(k.split())):
    csoc[int(m.group(2))] = sorted(int(x) for x in m.group(1).split())
assert sorted(csoc) == list(range(6)) and all(len(v) == 7 for v in csoc.values()), csoc
assert csoc[5] == [35, 80, 119, 161, 193, 209, 269] and csoc[0] == [69, 95, 112, 142, 152, 210, 263]    # сверено с рисунком (I-2)
raz = []
for v in csoc.values():
    e = [0] + v
    raz += [a - b for a in e for b in e if a > b]
assert len(raz) == len(set(raz)) == 6 * 28           # самоортогональность: все разности показателей различны
tablica("g9751_I2_csoc", {f"G({i})": [0] + v for i, v in sorted(csoc.items())},
        "Показатели степеней D в порождающих многочленах CSOC (n0/k0 = 7/6, J = 8) G.975.1 I.2.2.2, ур. (I-2)",
        [T1.ist(sI2, "I.2.2.2, ур. (I-2)")], "разобрано из текстового слоя, сверено со снимком формулы (risunki/g9751_I2_csoc.png); "
        "свойство самоортогональности (все 168 попарных разностей различны) выполнено")
rec("G.975.1 I.2: RS(255,239) + CSOC (n0/k0 = 7/6, J = 8) с перемежителем", SEM1, "каскадный: РС + свёрточный самоортогональный (CSOC)",
    {"внешний": "RS(255,239) G.975", "внутренний": "CSOC 7/6, J = 8: 6 информационных потоков, 1 проверочный; G(0)…G(5) → tablicy/g9751_I2_csoc.json",
     "избыточность": "24.48 %", "NCG": "7.95 дБ при 1E-12 (3 итерации)"}, "10G/40G DWDM",
    [T1.ist(T1.odna(r"RS\(255,239\)/CSOC \(n0/k0 = 7/6, J = 8\) super FEC code")[0], "I.2"), T1.ist(sI2, "многочлены CSOC")],
    "многочлены — из текста и снимка; проверено свойство самоортогональности набора (J = 8 ортогональных проверок)", "нет")
# I.3
p12 = iz_stepenej([12, 11, 8, 6, 0]); p11 = iz_stepenej([11, 2, 0])
assert primitivnyj(p12) and primitivnyj(p11)
assert stepen(bch_g(p12, [1, 3, 5])) == 3860 - 3824 and stepen(bch_g(p11, range(1, 20, 2))) == 2040 - 1930
sI3 = T1.odna(r"The BCH\(3860,3824\) code is a binary code")[0]
sI3c = C2.odna(r"Corrections to the minimal polynomials of clauses I\.3\.2\.1 and I\.3\.2\.2")[0]
rec("G.975.1 I.3: БЧХ(3860,3824) внешний + БЧХ(2040,1930) внутренний", SEM1, "каскадный: БЧХ + БЧХ",
    {"внешний": "БЧХ(3860,3824), GF(2^12) x^12 + x^11 + x^8 + x^6 + 1, G = M1·M3·M5, t = 3", "внутренний": "БЧХ(2040,1930), GF(2^11) x^11 + x^2 + 1, G = M1·M3·…·M19, t = 10",
     "минимальные многочлены": "M_i(x) = ∏_j (x − α^(i·2^j)) — исправлено в Corrigendum 2 (2013)", "перемежитель": "между кодами (рис. I.9–I.11)"},
    "10G DWDM", [T1.ist(sI3, "I.3.2.1"), C2.ist(sI3c, "Cor.2: исправление минимальных многочленов")],
    "вычислено: deg(M1·M3·M5) над x^12+x^11+x^8+x^6+1 = 36 = 3860 − 3824; deg(M1…M19) над x^11+x^2+1 = 110 = 2040 − 1930; оба поля примитивны",
    "частично: слепой БЧХ (rs_bch.py) при известной длине")
# I.4
sI4 = T1.odna(r"RS\(1023,1007\) parent outer code, m = 10, T = 8")[0]
sI4c = C2.odna(r"BCH\(2047,1952\) parent inner code, m = 11, T = 8 shortened to BCH\(2040,1952\)")[0]
p10 = iz_stepenej([10, 3, 0])
assert len(rs_g(p10, 0, 16)) == 17 and stepen(bch_g(p11, range(1, 16, 2))) == 88 == 2040 - 1952
rec("G.975.1 I.4: RS(1023,1007) (укороч. RS(781,765)/(778,762)) + БЧХ(2047,…) укороч. до БЧХ(2040,1952)", SEM1, "каскадный: РС + БЧХ",
    {"внешний": "RS над GF(2^10) p(x) = x^10 + x^3 + 1, T = 8; 15 × RS(781,765) + 1 × RS(778,762) на кадр ODU", "внутренний": "БЧХ GF(2^11) p(x) = x^11 + x^2 + 1, T = 8, 88 проверочных; 64 × БЧХ(2040,1952)",
     "перемежение": "побитовое по odu[]/otu[] (I.4.2.1), первый байт OA1 = 0xF6", "примечание": "в тексте «BCH(2047,1952)» — родительский код с 88 проверочными битами должен быть (2047,1959); Cor.2 уточняет только укорочение до (2040,1952)"},
    "10G/40G OTUk с 7 % избыточности", [T1.ist(sI4, "I.4.1"), C2.ist(sI4c, "Cor.2 I.4")],
    "вычислено: deg(M1·M3·…·M15) над x^11+x^2+1 = 88 = 2040 − 1952 (8 × 11); примитивность обоих многочленов поля", "частично: слепой РС/БЧХ")
# I.5
sI5 = T1.odna(r"The outer RS code used is RS\(1901,1855\), with the generator polynomial given by")[0]
sI5h = T1.gde("Positions with power of 2 position number are occupied by Hamming check bits")
assert len(rs_g(p11, 1001, 46)) == 47
rec("G.975.1 I.5: RS(1901,1855) над GF(2^11) + произведение расширенных Хэмминга (512,502)×(510,500)", SEM1, "каскадный: РС + произведение кодов Хэмминга (мягкое решение)",
    {"внешний": "RS(1901,1855), GF(2^11) x^11 + x^2 + 1, g(z) = ∏_{i=0}^{45} (z − α^(i+1001)) (снимок risunki/g9751_I5_rs.png), 12 слов чередуются посимвольно, 124 бита — нули",
     "внутренний": "произведение: строки (512,502), столбцы (510,500) — расширенный Хэмминг: проверочные на позициях 2^k (номер позиции 511…0), бит чётности на позиции 0",
     "порядок бит": "в символе — старший первым; символы — старший первым"},
    "40G DWDM", [T1.ist(sI5, "I.5.2.1"), T1.ist(sI5h, "I.5.2.2")], "корни α^1001…α^1046 — по снимку формулы; многочлен поля примитивен", "нет")
# I.6
sI6 = T1.odna(r"specified by a binary two-dimensional matrix M with 112 rows and 293 columns")[0]
sI6s = T1.odna(r"by selecting seven different slopes s1, \.\.\. , s7")[0]
rec("G.975.1 I.6: LDPC (32640,30592) на прямых решётки 112×293 (7 наклонов)", SEM1, "LDPC (геометрический, «наклонные прямые» по модулю 293)",
    {"матрица": "M 112 × 293; прямая с наклоном s через (0,c): {(a, (a·s + c) mod 293)}; 7 наклонов → 2051 проверок, ранг 2045", "раскладка": "данные 239×16×8 + 3 нуля + 2045 проверочных; 173 нуля (0, 292−d) не передаются",
     "наклоны": "значения s1…s7 в G.975.1 (2004) и поправках НЕ ОПУБЛИКОВАНЫ — код определён не полностью"},
    "10G/40G DWDM", [T1.ist(sI6, "I.6.2"), T1.ist(sI6s, "определение прямых")], "не проверяемо: наклоны не заданы в открытом тексте", "нет (нужны наклоны s1…s7)")
# I.7
k, sI7 = T1.kusok(r"Table I\.9/G\.975\.1 – Basic code construction", r"I\.7\.2\.2")
row = [l for l in k.split("\n") if re.match(r"G\d+\(x\) = x10", l.strip())]; col = [l for l in k.split("\n") if re.match(r"G\d+\(x\) = x9", l.strip())]
def razbor(l):
    i = int(re.match(r"G(\d+)", l.strip()).group(1)); prav = l.split("=")[1]
    st = [int(x) if x else 1 for x in re.findall(r"x(\d*)", prav)] + ([0] if re.search(r"\+ 1\s*$", prav) else [])
    return i, iz_stepenej(st)
row = dict(map(razbor, row)); col = dict(map(razbor, col))
assert sorted(row) == list(range(1, 22, 2)) and sorted(col) == [1, 3, 5, 7]
p9 = iz_stepenej([9, 4, 0])
assert all(minimalnyj(row[1], i)[0] == row[i] for i in row) and all(minimalnyj(p9, i)[0] == col[i] for i in col) and row[1] == p10 and col[1] == p9
k2, sI7t = T1.kusok(r"Table I\.16/G\.975\.1 – Configuration summary", r"I\.7\.3", posl=True)
for zn in ("900", "960", "884", "500", "510"): assert zn in k2
rec("G.975.1 I.7: два ортогонально сцепленных БЧХ (строки GF(2^10), столбцы GF(2^9)); режимы 7 %, 11 %, 25 %", SEM1, "каскадный: произведение БЧХ (итеративный)",
    {"строки": "G(x) = ∏ G_i, i = 1,3,…,2tr−1; G1 = x^10+x^3+1 … G21 (табл. I.9)", "столбцы": "G1 = x^9+x^4+1, G3, G5, G7",
     "режимы (табл. I.16)": "7 %: x = 900, tr = 4; y = 500, tc = 1 | 11 %: x = 960, tr = 5; y = 510, tc = 2 | 25 %: x = 884, tr = 11; y = 510, tc = 4",
     "укорочение": "нули со стороны старших степеней", "поправка": "Cor.1 — рис. I.22 (r1,m+1), (r2,m+2)"},
    "10G/40G OTUk/OTUkV", [T1.ist(sI7, "табл. I.9"), T1.ist(sI7t, "табл. I.16"), C1.ist(C1.odna(r"Figure I\.22/G\.975\.1 appears as")[0], "Cor.1")],
    "все 11 многочленов строк и 4 многочлена столбцов из табл. I.9 совпали с вычисленными минимальными многочленами α^i над G1(x)", "частично: слепой БЧХ")
# I.8
sI8 = T1.odna(r"The proposed FEC code is an RS\(2720,2550\) code with 12-bit symbols")[0]
p12b = iz_stepenej([12, 9, 8, 6, 3, 2, 0]); assert primitivnyj(p12b)
rec("G.975.1 I.8: RS(2720,2550) над GF(2^12)", SEM1, "РС",
    {"поле": "GF(2^12), p(x) = x^12 + x^9 + x^8 + x^6 + x^3 + x^2 + 1 (снимок risunki/g9751_I8_pole.png)", "t": "85", "раскладка": "30588 бит → 2549 символов + 4 бита в 2550-м",
     "g(x)": "∏ (x − α^(b+i)), смещение b — «произвольное», в тексте не задано"},
    "10G/40G", [T1.ist(sI8, "I.8")], "многочлен поля примитивен (проверено); смещение корней не задано", "частично: слепой РС (m = 12) найдёт b")
# I.9
sI9 = T1.odna(r"The enhanced FEC scheme uses two interleaved extended BCH\(1020,988\) codes")[0]
m3, m5 = minimalnyj(p10, 3)[0], minimalnyj(p10, 5)[0]
assert m3 == iz_stepenej([10, 3, 2, 1, 0]) and m5 == iz_stepenej([10, 8, 3, 2, 0])
gH = poly_mul2(poly_mul2(poly_mul2(p10, m3), m5), iz_stepenej([2, 0])); assert stepen(gH) == 1020 - 988
rec("G.975.1 I.9: два перемеженных расширенных БЧХ(1020,988) — горизонтальный и «наклонный»", SEM1, "каскадный: БЧХ + БЧХ (итеративный, прообраз лестничного)",
    {"поле": "p(x) = x^10 + x^3 + 1", "g_H": "m1·m3·m5·(x^2 + 1), m3 = x^10+x^3+x^2+x+1, m5 = x^10+x^8+x^3+x^2+1", "g_S": "x^30·m1(x^−1)·m3(x^−1)·m5(x^−1)·(x^2 + x + 1)",
     "блок": "(522240, 489472): 512 × 1020, 16 ODU", "перемежение": "сдвиги подблоков 16 × 32 (I.9.2.3, формула)"},
    "10G/40G DWDM", [T1.ist(sI9, "I.9.2"), T1.ist(T1.odna(r"^Two interleaved extended BCH\(1020,988\) super FEC code")[0], "снимок risunki/g9751_I9_g_polno.png")],
    "m3, m5 из текста совпали с вычисленными минимальными многочленами α^3, α^5; deg g_H = 32 = 1020 − 988", "нет")

# ================= G.709.2 OTU4-SC (лестничный) =================
T2 = Tekst("istochniki/itu/T-REC-G.709.2-201807-I.pdf")
k, sPi = T2.kusok(r"Table A\.2 – Values of", r"A\.7\.2", posl=True)
pi = {}
for a, b, c, d in re.findall(r"Π𝑑\((\d+)\s*(?::\s*(\d+))?\)\s*=\s*(\d+)\s*(?::\s*(\d+))?", k):
    a, c = int(a), int(c); b = int(b) if b else a; d = int(d) if d else c
    assert b - a == d - c
    for i in range(b - a + 1): pi[a + i] = c + i
assert sorted(pi) == list(range(510)) and sorted(pi.values()) == list(range(510))
pinv = {v: k_ for k_, v in pi.items()}
sA6 = T2.odna(r"For a root α of the primitive polynomial 𝑝\(x\) = 1 \+ x3 \+ x10")[0]
sA72 = T2.odna(r"Consider the function 𝑓 which maps an integer 𝑖, 1 ≤𝑖≤1023")[0]
def F(l): b0, b1, b2 = l & 1, l >> 1 & 1, l >> 2 & 1; return (b2 & (1 - b1) & (1 - b0)) | ((1 - b2) & b1) | ((1 - b2) & (1 - b1) & b0)
def fcol(i):
    b = i; b3 = gmul(gmul(b, b, p10), b, p10); b5 = gmul(gmul(b3, b, p10), b, p10)
    return (b << 22) | (b3 << 12) | (b5 << 2) | (F(b) << 1) | (1 - F(b))
cols = [fcol(1021), fcol(1022)] + [fcol(i) for i in range(1, 511)] + [fcol(511 + pinv[j]) for j in range(510)]
assert len(cols) == 1022 and len(set(cols)) == 1022
Hrows = [sum(((c >> (31 - r)) & 1) << (1021 - j) for j, c in enumerate(cols)) for r in range(32)]
assert rang_gf2(Hrows) == 32
info, P = sistemnaya_P(cols, 32, list(range(990, 1022)))
tablica("g7092_sc_pi_d", [pi[i] for i in range(510)], "Перестановка Π_d (i = 0…509) лестничного кода 512×510 G.709.2 табл. A.2", [T2.ist(sPi, "Table A.2")],
        "собрано из диапазонов табл. A.2; проверено: биекция 0…509")
tablica("g7092_sc_P", [format(x, "08x") for x in P], "Матрица P (990 × 32) порождающих масок компонентного БЧХ(1022,990): строка j — 32 проверочных бита (hex, старший — первый проверочный), G = [I; P]",
        [T2.ist(sA72, "A.7.2–A.7.3"), T2.ist(sA6, "A.6 поле")],
        "H (32 × 1022) построена по A.7.2 (β, β^3, β^5, F(β), F̄(β); F — функция младших 3 бит, прочитана по снимку risunki/g7092_A72_H.png); ранг 32; "
        "P получена приведением к виду [P^T; I] (A.7.3)")
rec("G.709.2 OTU4-SC: лестничный код 512×510 с компонентным БЧХ(1022,990) (SC-FEC, ~6.7 %)", SEM, "лестничный (staircase) на БЧХ",
    {"блок": "B_i: 512 строк × 510 столбцов; информационные столбцы 0…477, проверочные 478…509", "компонентный код": "расширенный БЧХ(1022,990): левая часть 512 бит из B_{i−1} (столбец Π_d(j−2)), правая 510 — строка j блока B_i; строки 0–1 — укороченные",
     "поле": "GF(2^10), p(x) = 1 + x^3 + x^10", "H": "f(i) = [β, β^3, β^5, F(β), F̄(β)] → tablicy/g7092_sc_P.json", "Π_d": "tablicy/g7092_sc_pi_d.json",
     "декоррелятор": "перестановки P1/P2 (64 × 12, 5 вариантов, A.8)", "кадр": "два кадра OTU4 = 261120 бит на блок", "NCG": "8.35 дБ при ~6.7 % избыточности (G.709.2, 7)"},
    "OTU4 длинные пролёты (OTU4-SC), 100GBASE-ZR (802.3ct), FlexO-LR DSH (внешний), OIF 400ZR CFEC (внешний)",
    [T2.ist(T2.odna(r"The OTU4-SC FEC code is a generalized staircase code based on 512-bit × 510-bit blocks")[0], "8.3"), T2.ist(sPi, "Table A.2 Π_d"), T2.ist(sA72, "A.7 H и G")],
    "Π_d — биекция 0…509; H из формулы A.7.2 имеет полный ранг 32 → k = 990; все 1022 столбца различны", "нет")

# ================= G.709.3 FlexO-LR: DSH (Хэмминг 128,119) и DO (OFEC) =================
T3 = Tekst("istochniki/itu/T-REC-G.709.3-202403-I_SOFT/G.709.3_2024-03.pdf")
sD = T3.odna(r"The forward error correction for the FlexO-x-DSH uses a systematic \(128,119\) double-extended")[0]
sE = T3.odna(r"polynomial y16 \+ y14 \+ y13 \+ y11 \+ y10 \+ y9 \+ y8 \+ y6 \+y5 \+ y \+1")[0]
sG = T3.odna(r"Test vectors are provided in this Recommendation for FlexO-2-DO-QPSK and FlexO-4-DO-16QAM")[0]
def s7(i): s0, s1, s2 = i & 1, i >> 1 & 1, i >> 2 & 1; return (s0 & s2) | ((1 - s0) & (1 - s1) & (1 - s2)) | (s0 & s1 & (1 - s2))
def gcol(i): return sum(((i >> r) & 1) << (8 - r) for r in range(7)) | (s7(i) << 1) | 1     # [s0…s6, s7, 1] сверху вниз
poryadok = list(range(0, 63)) + list(range(64, 95)) + list(range(96, 111)) + list(range(112, 119)) + [120, 122, 124] + [63, 95, 111, 119, 121, 123, 125, 126, 127]
assert len(poryadok) == 128 and sorted(poryadok) == list(range(128))
hc = [gcol(i) for i in poryadok]
assert len(set(hc)) == 128 and rang_gf2([sum(((c >> (8 - r)) & 1) << (127 - j) for j, c in enumerate(hc)) for r in range(9)]) == 9
infoH, PH = sistemnaya_P(hc, 9, list(range(119, 128)))
# минимальное расстояние ≥ 4: столбцы различны и последняя строка — все единицы; проверим, что нет трёх столбцов с нулевой суммой
st = set(hc); assert not any((a ^ b) in st for a in hc for b in hc if a != b)
T4Z = Tekst("istochniki/oif/OIF-400ZR-03.0.1.pdf")
sZ = T4Z.odna(r"The systematic double-extended Hamming code is most naturally defined in terms of its parity-check")[0]
tablica("hamming_128_119_g7093_oif400zr", {"порядок столбцов H (i в g(i))": poryadok, "P (119 строк × 9 бит, hex)": [format(x, "03x") for x in PH]},
        "Двойной расширенный Хэмминг (128,119): H = [g(i)], g(i) = [s0…s6, s7, 1]^T, s7 = (s0∧s2) ∨ (¬s0∧¬s1∧¬s2) ∨ (s0∧s1∧¬s2); G = [I; P^T]",
        [T3.ist(sD, "Annex D"), T4Z.ist(sZ, "10.x Inner Hamming Code")],
        "формулы G.709.3 Annex D и OIF-400ZR совпадают (снимки risunki/g7093_hamming_s7.png, oif400zr_hamming_s7.png); 128 различных столбцов, ранг 9, "
        "никакие три столбца не дают нуль → d = 4")
g_ofec = iz_stepenej([16, 14, 13, 11, 10, 9, 8, 6, 5, 1, 0])
kand = [p for p in range(256, 512) if primitivnyj(p) and bch_g(p, [1, 3]) == g_ofec]
assert kand == [0x11D]
rec("G.709.3 FlexO-x-DSH: лестничный 512×510 (внешний, жёсткое) + двойной расширенный Хэмминг (128,119) (внутренний, мягкое)", SEM, "каскадный: лестничный БЧХ + Хэмминг",
    {"внешний": "как G.709.2 (Annex A/B/C)", "внутренний": "(128,119), 9 проверочных бит → tablicy/hamming_128_119_g7093_oif400zr.json",
     "перемежение": "свёрточное по 119-битовым блокам + перемежение символов 8 слов (QPSK) / 4 слова (16QAM)", "скремблер": "x^16 + x^12 + x^3 + x + 1"},
    "FlexO-LR 100G/200G/400G (ZR), OIF 400ZR (CFEC)", [T3.ist(sD, "Annex D")], "таблица H/P собрана и проверена (см. таблицу)", "нет")
rec("G.709.3 FlexO-x-DO / OIF 800ZR, 1600ZR: OFEC — пространственно-связанный код на расширенном БЧХ(256,239)", SEM, "OFEC (пространственно-связанный произведение-подобный, мягкое решение)",
    {"компонентный": "расширенный БЧХ(256,239), d = 6: первые 255 бит делятся на g(y) = y^16+y^14+y^13+y^11+y^10+y^9+y^8+y^6+y^5+y+1, плюс общая чётность",
     "поле (вычислено)": "g = m1·m3 над GF(2^8) с x^8 + x^4 + x^3 + x^2 + 1 — единственный примитивный многочлен степени 8, дающий такой g",
     "структура": "OFBG: 21·k блоков OFC по 7104 бит; слово W(t,P,p): 128 бит из V{t−1}/V{t}, 111 бит из U, 17 проверочных (ур. 16-2, 16-3)",
     "тестовые векторы": "Annex G: TP0 (вход), TP2 (скремблирован), TP5 (закодирован), TP6 (перемежён), TP7 (символы) для FlexO-2-DO-QPSK и FlexO-4-DO-16QAM — файлы электронного приложения"},
    "FlexO-LR 200G/400G, OpenROADM oFEC, OIF 800ZR/1600ZR", [T3.ist(sE, "Annex E"), T3.ist(sG, "Annex G тестовые векторы")],
    "g(y) = m1(y)·m3(y) над x^8+x^4+x^3+x^2+1 (проверено вычислением, других примитивных полей нет); сверка кодера по TP0→TP5 не выполнена (векторы приложены)", "нет")

# ================= G.709.1/.4/.5 RS =================
T5 = Tekst("istochniki/itu/T-REC-G.709.5-202403-I.pdf")
s5 = T5.odna(r"The FlexO-x-RS FEC scheme is the RS\(544,514\) from \[IEEE 802\.3\] for 100GBASE-R")[0]; s5p = T5.gde("is a root of the binary primitive polynomial 𝑥10 + 𝑥3 + 1")
T4 = Tekst("istochniki/itu/T-REC-G.709.4-202003-I.pdf")
s4a = T4.odna(r"Annex A – Forward error correction for OTU-RS using 10-bit interleaved RS\(544,514\)")[0]; s4c = T4.odna(r"Annex C – Forward error correction for OTU-RS using 10-bit interleaved RS\(528,514\)")[0]
rec("G.709.1/G.709.5 FlexO-x-RS и G.709.4 OTU25/50-RS: RS(544,514) и RS(528,514) IEEE 802.3", SEM, "РС",
    {"код": "RS(544,514) (t = 15) и RS(528,514) (t = 7) над GF(2^10), x^10 + x^3 + 1, корни α^0…", "перемежение": "10-битовое по 2/4/8 словам",
     "скремблер": "x^16 + x^12 + x^3 + x + 1", "интерфейсы": "FlexO-1/2/4/8-RS (короткие), OTU25-RS/OTU50-RS"},
    "OTN короткие межузловые (FlexO SR), OTUCn", [T5.ist(s5, "FlexO-x-RS"), T5.ist(s5p), T4.ist(s4a), T4.ist(s4c)],
    "код идентичен IEEE 802.3 Cl.91/119 — проверен в семействе Ethernet (табл. 119–3, примеры кодовых слов)", "нет (слепой РС — частично)")

# ================= G.707 SDH in-band FEC =================
T707 = Tekst("istochniki/itu/T-REC-G.707-200701-I.pdf")
s707 = T707.odna(r"The code is a shortened, systematic binary-BCH code derived from a \(8191, 8152\) parent code")[0]
k707, s707g = T707.kusok(r"The generator polynomial used is G\(x\) = G1\(x\) × G3\(x\) × G5\(x\) where", r"FEC encoding operates on a row-by-row basis")
p13 = iz_stepenej([13, 4, 3, 1, 0]); assert primitivnyj(p13)
assert minimalnyj(p13, 3)[0] == iz_stepenej([13, 10, 9, 7, 5, 4, 0]) and minimalnyj(p13, 5)[0] == iz_stepenej([13, 11, 8, 7, 4, 1, 0])
assert "x13 + x4 + x3 + x + 1" in k707 and stepen(bch_g(p13, [1, 3, 5])) == 39
rec("SDH/SONET in-band FEC (G.707 Annex A): укороченный БЧХ-3 (4359,4320) STM-64/256", SEM, "БЧХ",
    {"n,k,t": "4359, 4320, 3 (из (8191,8152))", "g(x)": "G1·G3·G5: G1 = x^13+x^4+x^3+x+1, G3 = x^13+x^10+x^9+x^7+x^5+x^4+1, G5 = x^13+x^11+x^8+x^7+x^4+x+1",
     "раскладка": "строка STM-N — 8 × N/16 «срезов» (bit-slice), слово = один срез; 8-битовое перемежение → пачки до 24 бит", "проверочные": "в байтах P1/Q1 MSOH/RSOH; RSOH (кроме Q1) не кодируется (нули)",
     "STM-16": "Appendix IX"},
    "SDH STM-16/64/256, SONET OC-48/192/768 (in-band FEC)", [T707.ist(s707, "A.2.1"), T707.ist(s707g, "A.2.2")],
    "G1 примитивен; G3, G5 совпали с минимальными многочленами α^3, α^5; deg G = 39 = 4359 − 4320", "нет (SDH-кадр есть — sdh.py; FEC не снимается)")

# ================= OIF =================
sZ1 = T4Z.odna(r"The 400ZR Forward Error Correction \(FEC\) algorithm is a Concatenated FEC \(C-FEC\)")[0]
sZs = T4Z.odna(r"of sequence 65535 and the generating polynomial shall be")[0]
rec("OIF 400ZR C-FEC: лестничный 512×510 (жёсткий, 255/239) + двойной расширенный Хэмминг (128,119) (мягкий)", SEMO, "каскадный: лестничный БЧХ + Хэмминг",
    {"внешний": "SC-FEC G.709.2 Annex A (компонентный БЧХ(1022,990), декоррелятор)", "внутренний": "Хэмминг (128,119) — как G.709.3 Annex D (таблица общая)",
     "CRC": "CRC-32 IEEE 802.3 по 244664 битам", "скремблер": "x^16 + x^12 + x^3 + x + 1, сброс в FFFF в начале 5 блоков SC", "перемежение": "свёрточное по 119 бит, затем 8 слов Хэмминга побайтно",
     "избыточность/NCG": "~14.8 %, ~10.8 дБ", "модуляция": "DP-16QAM"},
    "OIF 400ZR (DWDM), 400GBASE-ZR (802.3cw)", [T4Z.ist(sZ1, "10"), T4Z.ist(sZ, "Inner Hamming"), T4Z.ist(sZs, "скремблер")],
    "формула H Хэмминга совпадает с G.709.3 Annex D (два независимых текста), таблица собрана и проверена (d = 4)", "нет")
LR = Tekst("istochniki/oif/OIF-800LR-01.0.pdf")
sLR = LR.odna(r"The BCH\(126,110\) uses the following generator polynomial with systematic encoding")[0]
p7 = iz_stepenej([7, 3, 0]); assert primitivnyj(p7) and minimalnyj(p7, 3)[0] == iz_stepenej([7, 3, 2, 1, 0])
g126 = poly_mul2(bch_g(p7, [1, 3]), iz_stepenej([2, 0])); assert g126 == iz_stepenej([16, 14, 11, 10, 9, 7, 5, 3, 1, 0])
rec("OIF 800LR: RS(544,514) Ethernet (внешний) + БЧХ(126,110) мягкий (внутренний)", SEMO, "каскадный: РС + БЧХ",
    {"внутренний": "БЧХ(127,113) → укороч. (124,110) → +2 бита чётности (чёт/нечёт) = (126,110); g(x) = M1·M3·(x^2+1) = x^16+x^14+x^11+x^10+x^9+x^7+x^5+x^3+x+1, поле x^7+x^3+1",
     "порядок": "бит 0 входа → коэффициент x^109; передача с x^125", "внешний": "RS(544,514) 4 слова, 10-битовые символы", "NCG": "10.35 дБ, ~14.5 %"},
    "OIF 800LR", [LR.ist(sLR, "5.3")], "g(x) = (x^7+x^3+1)(x^7+x^3+x^2+x+1)(x^2+1) перемножено — совпало с развёрнутой записью; M3 = минимальный многочлен α^3", "нет")
Z8 = Tekst("istochniki/oif/OIF-800ZR-01.0.pdf"); s8 = Z8.odna(r"The 800ZR coherent interface uses OFEC for forward error correction")[0]
Z16 = Tekst("istochniki/oif/OIF-1600ZR-01.0.pdf"); s16 = Z16.odna(r"The 1600ZR coherent pluggable module uses OFEC for forward error correction")[0]
ZAPISI[[z["имя"].startswith("G.709.3 FlexO-x-DO") for z in ZAPISI].index(True)]["источник"] += [Z8.ist(s8, "800ZR OFEC"), Z16.ist(s16, "1600ZR OFEC")]
W = Tekst("istochniki/oif/OIF_FEC_100G-01.0.pdf"); sW = W.odna(r"Concatenated BCH codes: outer code: BCH\(3860,3824\), inner code")[0]
ZAPISI[[z["имя"].startswith("G.975.1 I.3") for z in ZAPISI].index(True)]["источник"].append(W.ist(sW, "OIF FEC 100G WP: обзор"))

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
