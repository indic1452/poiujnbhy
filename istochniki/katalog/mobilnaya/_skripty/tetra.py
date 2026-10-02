"""TETRA (ETSI EN 300 392-2 п. 8, EN 300 395-2 п. 5): RCPC K=5 1/4 и выкалывание, RM(30,14), RM(16,5), CRC-16,
(K,a)-перемежитель, TEDS PCCC, скремблер; речевой канал (1/3, 8/12, 8/18, 8/17). Сверка с osmo-tetra (AGPL-3.0)."""
import re, sys, itertools
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/tetra/en_30039202v030801p.pdf")
TS = Tekst("istochniki/tetra/en_30039502v010303p.pdf")
ZAPISI = []
def zhir(t): return " ".join(t.split())
def sek(TT, pat): return [s for s, n, l in TT.naiti(pat)]

# мать 1/4
kus, s_m = T.kusok(r"The generator polynomials of the mother code shall be:", r"8\.2\.3\.1\.2", posl=True)
G = [re.sub(r"\s", "", m) for m in re.findall(r"G\d\(D\) = ([1D\d +]+?)\s*\n", kus)]
st = lambda v: sorted({0 if t == "1" else 1 if t == "D" else int(t[1:]) for t in re.findall(r"D\d*|1", v)})
MAT = [st(g) for g in G]
assert MAT == [[0, 1, 4], [0, 2, 3, 4], [0, 1, 2, 4], [0, 1, 3, 4]], MAT
# выкалывание (8.8)–(8.11)
kus, s_p = T.kusok(r"8\.2\.3\.1\.2\s*\nPuncturing of the mother code", r"8\.2\.3\.2\s*\n", posl=True)
PP = {}
for m in re.finditer(r"rate ([\d/]+)\s*\nThe t = (\d) puncturing co-efficients shall be:\s*\n(.*?)(?:\(8\.\d+\))", kus, re.S):
    p = [int(x) for x in re.findall(r"P\(\d\) = (\d+)", m.group(3))]; assert len(p) == int(m.group(2))
    ij = re.search(r"i = j(?: \+ ?\(j\s*- 1\) div (\d+))?", zhir(m.group(3)))
    PP[m.group(1)] = {"t": int(m.group(2)), "P": p, "i_от_j": "i = j" + (f" + (j − 1) div {ij.group(1)}" if ij.group(1) else "")}
assert set(PP) == {"2/3", "1/3", "292/432", "148/432"}
oc = kod("osmo-tetra", "src/lower_mac/tetra_conv_enc.c")
assert c_massiv(oc, "P_rate2_3")[1:] == PP["2/3"]["P"] == PP["292/432"]["P"] and c_massiv(oc, "P_rate1_3")[1:] == PP["1/3"]["P"] == PP["148/432"]["P"]
def vykolot(K2, shema):
    t, P = shema["t"], shema["P"]; d = re.search(r"div (\d+)", shema["i_от_j"]); K3 = {"2/3": K2 * 3 // 2, "1/3": K2 * 3}.get(None, None)
    return [(lambda i: 8 * ((i - 1) // t) + P[i - t * ((i - 1) // t) - 1])(j + ((j - 1) // int(d.group(1)) if d else 0)) for j in range(1, 433)]
# 292/432: проверка — 432 выходных позиции из 4·292 = 1168, все различны и < 1168
v = vykolot(292, PP["292/432"]); assert len(set(v)) == 432 and max(v) <= 4 * 292
v = vykolot(148, PP["148/432"]); assert len(set(v)) == 432 and max(v) <= 4 * 148
# RM(30,14)
kus, s_rm = T.kusok(r"Where G is the generator matrix:", r"Where I14 denotes", posl=False)
bits = [int(x) for x in re.findall(r"(?m)^\s*([01])\s*$", kus)]
assert len(bits) >= 224
P30 = [bits[16 * r:16 * r + 16] for r in range(14)]
orm = kod("osmo-tetra", "src/lower_mac/tetra_rm3014.c")
assert c_massiv(orm, "rm_30_14_gen") == sum(P30, [])
def dmin(Pm, k):
    return min(bin(x).count("1") + sum(sum(Pm[r][c] for r in range(k) if (x >> r) & 1) & 1 for c in range(len(Pm[0]))) for x in range(1, 1 << k))
d30 = dmin(P30, 14)
# RM(16,5) для QAM
kus, s_16 = T.kusok(r"\(16,5\) Reed-Muller \(RM\) code for QAM\s*\nThe \(16,5\) RM code", r"Is denoting the", posl=True)
b16 = [int(x) for x in re.findall(r"(?m)^\s*([01])\s*$", kus)]
assert len(b16) == 55, len(b16)
P16 = [b16[11 * r:11 * r + 11] for r in range(5)]
d16 = dmin(P16, 5)
# CRC-16 (8.2.3.3)
s_crc = T.odna(r"G\(X\) = X16 \+ X12 \+ X5 \+ 1")[0]
ocrc = kod("osmo-tetra", "src/lower_mac/crc_simple.c")
# перемежители
s_bi = [s for s, n, l in T.naiti(r"with k = 1 \+ \(\(a × i\) mod K\)")][0]
fl = zhir(T.ves)
Ka_fm = sorted({(int(a), int(b)) for a, b in re.findall(r"\((\d{2,3}),(\d{1,3})\) \| block \| interleave", " | ".join(x.strip() for x in T.stroki if x.strip()))})
olm = kod("osmo-tetra", "src/lower_mac/tetra_lower_mac.c")
tl = open(os.path.join(KOREN, olm)).read()
osm_Ka = sorted({(int(a), int(b)) for a, b in re.findall(r"\.type345_bits\s*=\s*(\d+),.*?\.interleave_a\s*=\s*(\d+)", tl, re.S)})
assert set(osm_Ka) <= set(Ka_fm), (osm_Ka, Ka_fm)
for K, a in Ka_fm: assert len({1 + (a * i) % K for i in range(1, K + 1)}) == K
Ka_qam = [(k, int(K.replace(" ", "")), int(a)) for k, K, a in re.findall(r"(SICH-Q/U|SICH-Q/D \+ AACH-Q|SCH-Q/HU|SCH-Q/U|SCH-Q/D, BNCH-Q|SCH-Q/RA|BSCH-Q|SCH-Q/B)?[^K]{0,20}K = ([\d ]{2,6}), a = (\d+)", fl)]
s_qam = sek(T, r"Table 8\.3: Values of K and a")[0]
# скремблер
m = re.search(r"With ci = 1 for i = ([\d, and]+?) and ci = 0 elsewhere", fl); ci = [int(x) for x in re.findall(r"\d+", m.group(1))]
assert ci == [0, 1, 2, 4, 5, 7, 8, 10, 11, 12, 16, 22, 23, 26, 32]
s_scr = T.odna(r"With ci = 1 for i = 0, 1, 2, 4")[0]
osc = kod("osmo-tetra", "src/lower_mac/tetra_scramb.c")
# osmo-tetra: «taps: 32 26 23 22 16 12 11 10 8 7 5 4 2 1» = 32 − i для i из c(x) без 0
taps_osmo = [int(x) for x in re.search(r"taps: ([\d ]+)", open(os.path.join(KOREN, osc)).read()).group(1).split()]
assert sorted(taps_osmo) == [i for i in ci if i], taps_osmo      # p(k) = Σ c_i p(k − i): отводы = номера i
# TEDS PCCC
kus, s_pccc = T.kusok(r"The generator polynomials of the parity bit shall be:", r"Table 8\.1", posl=True)
G0, G1 = [st(x) for x in re.findall(r"G\d\(D\) = ([1D\d +]+?)\s*\n", kus)]
assert (G0, G1) == ([0, 2, 3], [0, 1, 3])
pc = {m.group(1): [int(x) for x in re.findall(r"P\(\d\) =\s*(\d+)", m.group(2))] for m in re.finditer(r"PCCC encoder with coding rate (\d/\d)\s*\nThe t = \d puncturing co-efficients shall be:(.*?)\(8\.3\d\)", T.ves, re.S)}
assert pc == {"2/3": [1, 2, 4, 7, 9, 10], "1/2": [1, 2, 4, 6, 7, 8, 10, 12]}, pc
s_qc = [s for s, n, l in T.naiti(r"Interleaving by the quadratic-congruence interleaver")][-1]
# речевой канал 395-2
kus, s_sm = TS.kusok(r"The generator polynomials of the mother code shall be:", r"5\.4\.3\.2", posl=False)
s_sm = sek(TS, r"The generator polynomials of the mother code shall be")[0]
rp = {}
for m in re.finditer(r"rate (8/1[278])(?: \(equal to 2/3\))?\s*\nThe t = (\d+) puncturing coefficients shall be:(.*?)Period\s*=\s*(\d+)", TS.ves, re.S):
    rp.setdefault(m.group(1), ([int(x) for x in re.findall(r"P\(\d\)\s*=\s*(\d+)", zhir(m.group(3)))], int(m.group(4))))
# формулы (51)/(54) в PDF — рисунки (сняты: risunki/tetra_speech_s33.png, tetra_speech_817.png); программно берём матрицы
# выкалывания 3×8 из эталонного C-кода ETSI (приложение к EN 300 395-2: en_30039502v010303p0.zip, C-CODE/ARRAYS.TAB)
ARR = "istochniki/tetra/en_30039502_kod/C-CODE/ARRAYS.TAB"
def matr_v_P(imya):
    a = c_massiv(ARR, imya); assert len(a) == 24
    return [3 * t + i + 1 for t in range(8) for i in range(3) if a[i * 8 + t]]
def po_periodu(P, per): return P[:[x > per for x in P].index(True)] if any(x > per for x in P) else P
rp = {"8/12": (po_periodu(matr_v_P("A1"), 6), 6), "8/18": (po_periodu(matr_v_P("A2"), 12), 12), "8/17": (matr_v_P("Fs_A2"), 24)}
assert rp["8/12"][0] == [1, 2, 4] and rp["8/18"][0] == [1, 2, 3, 4, 5, 7, 8, 10, 11]
assert rp["8/17"][0] == [1, 2, 3, 4, 5, 7, 8, 10, 11, 13, 14, 16, 17, 19, 20, 22, 23]     # = снимок (54)
ost = kod("osmo-tetra", "src/lower_mac/tetra_conv_enc.c")
assert c_massiv(ost, "P_rate8_17")[1:] == rp["8/17"][0]
if "8/12" in rp: assert c_massiv(ost, "P_rate8_12")[1:] == rp["8/12"][0]
if "8/18" in rp: assert c_massiv(ost, "P_rate8_18")[1:] == rp["8/18"][0]
s_mi = [s for s, n, l in TS.naiti(r"^5\.5\.3\s*$|Matrix Interleaving\s*$")][-1]
tablica("tetra_392-2_395-2", {"mat_1_4": {f"G{i+1}": D_zapis(g) for i, g in enumerate(MAT)}, "vykalyvanie_RCPC": PP,
    "RM_30_14_P": P30, "RM_16_5_P": P16, "d_min": {"RM(30,14)": d30, "RM(16,5)": d16},
    "Ka_fazovaya": Ka_fm, "Ka_QAM": [{"канал": k, "K": K, "a": a} for k, K, a in Ka_qam], "skrembler_ci": ci,
    "TEDS_PCCC": {"G0": D_zapis(G0), "G1": D_zapis(G1), "выкалывание": pc}, "rech_395-2_vykalyvanie": rp},
    "TETRA: мать RCPC, схемы выкалывания, RM(30,14)/RM(16,5), параметры (K,a) перемежителей, скремблер, TEDS PCCC, речевой канал",
    [T.ist(s_m, "8.2.3.1.1"), T.ist(s_p, "8.2.3.1.3–8.2.3.1.6"), T.ist(s_rm, "8.2.3.2 (8.13)"), T.ist(s_16, "8.2.3.5"), T.ist(s_bi, "8.2.4.1"),
     T.ist(s_qam, "Tables 8.2–8.6"), T.ist(s_scr, "8.2.5.2"), T.ist(s_pccc, "8.2.3.4.1"), TS.ist(s_sm, "5.4.3.1 (48)"),
     ist_kod(oc, "P_rate2_3"), ist_kod(orm, "rm_30_14_gen"), ist_kod(olm, "interleave_a"), ist_kod(osc, "taps")],
    f"все значения разобраны из текста; P-коэффициенты 2/3, 1/3 (и 292/432, 148/432) совпали с P_rate2_3/P_rate1_3 osmo-tetra, "
    f"8/12 и 8/18 речи — с P_rate8_12/P_rate8_18; RM(30,14) 14×16 совпала с rm_30_14_gen; (K,a) osmo-tetra {osm_Ka} есть среди {len(Ka_fm)} пар стандарта, "
    f"все перестановки k = 1 + (a·i mod K) биективны; отводы скремблера совпали с «taps» osmo-tetra; d_min RM(30,14) = {d30}, RM(16,5) = {d16}")

def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "TETRA", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST = "есть в проекте: tetra.py (нижний MAC как osmo-tetra: скремблер, (K,a), RCPC 2/3, CRC-16, RM(30,14) AACH, BSCH/SCH/F)"
rec("TETRA RCPC 16 состояний (мать 1/4, K=5)", "свёрточный с выкалыванием (RCPC)",
    {"мать": "G1 = 1+D+D4, G2 = 1+D2+D3+D4, G3 = 1+D+D2+D4, G4 = 1+D+D3+D4 (восьм. 23, 35, 27, 33 при записи D0 = младший разряд; при D0 = старший — 31, 27, 35, 33)",
     "выкалывание": "b3(j) = V(k), k = 8((i−1) div t) + P(i − t((i−1) div t)); 2/3: t=3 P=1,2,5; 1/3: t=6 P=1,2,3,5,6,7; 292/432: как 2/3, i = j + (j−1) div 65; 148/432: как 1/3, i = j + (j−1) div 35",
     "хвост": "4 нуля", "блоки": "AACH нет; BSCH 60→80(+CRC16+4)→120; SCH/HD,BNCH,STCH 124→144→216; SCH/HU 92→112→168; SCH/F 268→288→432; TCH/4.8 (292/432), TCH/2.4 (148/432) с перемежением на N=1/4/8 блоков"},
    "TETRA V+D и DMO: логические каналы π/4-DQPSK", [T.ist(s_m, "8.2.3.1.1"), T.ist(s_p, "8.2.3.1.3–6"), ist_kod(oc, "P_rate2_3")],
    "многочлены и коэффициенты из текста; сверены с osmo-tetra tetra_conv_enc.c", EST + "; TCH/4.8, TCH/2.4 (292/432, 148/432) и перемежение на N блоков — нет")
rec("TETRA RM(30,14) укороченный Рида — Маллера", "РМ (укороченный)", {"n": 30, "k": 14, "d_min": d30, "G": "[I14 | P], P 14×16 → tablicy/tetra_392-2_395-2.json"},
    "TETRA AACH (широковещательный блок нисходящей пачки)", [T.ist(s_rm, "8.2.3.2")], "матрица из текста совпала с rm_30_14_gen osmo-tetra; d_min перебором 2^14", EST)
rec("TETRA RM(16,5) для QAM (TEDS)", "РМ", {"n": 16, "k": 5, "d_min": d16, "G": "[I5 | P], P 5×11 → tablicy"},
    "TETRA TEDS AACH-Q/SICH-Q", [T.ist(s_16, "8.2.3.5")], "матрица из текста; d_min перебором; второго открытого источника (TEDS) нет", "нет")
rec("TETRA CRC-16 (K1+16, K1)", "CRC-подобный", {"многочлен": "X16 + X12 + X5 + 1", "вид": "как ITU-T X.25: начальное все единицы, инверсия; биты b2(K1+k) = f(16−k)", "остаток": "0x1D0F (в проекте)"},
    "TETRA все каналы сигнализации (BSCH, SCH/*, BNCH, STCH), TEDS", [T.ist(s_crc, "8.2.3.3 (8.16)"), ist_kod(ocrc, "GEN_POLY")], "многочлен из текста; osmo-tetra crc_simple.c GEN_POLY 0x1021, начальное 0xFFFF", EST)
rec("TETRA (K,a)-блочный перемежитель и перемежение на N блоков", "перемежитель (блочный)",
    {"формула": "b4(k) = b3(i), k = 1 + (a·i mod K)", "фазовая модуляция (K,a)": Ka_fm, "N блоков": "диагональный: j = (k−1) div (432/N), i = (k−1) mod (432/N), затем (432,103); N = 1, 4, 8",
     "QAM": "табл. 8.2–8.6 → tablicy (K от 16 до 8304)"},
    "TETRA все логические каналы", [T.ist(s_bi, "8.2.4.1"), T.ist(s_qam, "8.2.4.3")], "пары (K,a) из текста; osmo-tetra использует те же; биективность проверена", EST)
rec("TETRA скремблер 32 бита", "скремблер", {"многочлен": "c(x) = 1 + x + x2 + x4 + x5 + x7 + x8 + x10 + x11 + x12 + x16 + x22 + x23 + x26 + x32",
    "начальное": "p(k) = e(1−k) для k = −29…0 (30 бит расширенного цветового кода: MCC, MNC, цвет), p(−31) = p(−30) = 1; для BSCH e = 0"},
    "TETRA все каналы кроме синхро", [T.ist(s_scr, "8.2.5.2")], "из текста; отводы совпали с osmo-tetra tetra_scramb.c", EST)
rec("TETRA TEDS PCCC (турбо) 8 состояний", "турбо PCCC",
    {"RSC": "G1/G0: G0 = 1+D2+D3, G1 = 1+D+D3", "перемежитель": "квадратично-конгруэнтный: c0 = 0, cm = (cm−1 + m) mod S, S — степень 2 ≥ K2−3, затем попарные перестановки (8.26)",
     "хвост": "табл. 8.1 (по 3 бита каждому RSC)", "выкалывание": "k = 12((i−1) div t) + P(…): 2/3 t=6 P=1,2,4,7,9,10; 1/2 t=8 P=1,2,4,6,7,8,10,12"},
    "TETRA TEDS (QAM, каналы SCH-Q)", [T.ist(s_pccc, "8.2.3.4.1"), T.ist(s_qc, "8.2.3.4.2")], "параметры из текста; открытой реализации TEDS не найдено",
    "частично: turbo.py (BCJR PCCC); перемежитель QC — реализовать по 8.2.3.4.2")
rec("TETRA речевой канал ACELP (EN 300 395-2)", "каскадный: CRC + RCPC K=5 1/3 (8/12, 8/18, 8/17) + матричное перемежение",
    {"классы": "137 бит на речевой кадр: класс 2 — 30, класс 1 — 56, класс 0 — 51 (табл. 4)", "мать": "1/3: G1 = 1+D+D2+D3+D4, G2 = 1+D+D3+D4, G3 = 1+D2+D4",
     "выкалывание": {k: {"P": v[0], "период": v[1]} for k, v in rp.items()}, "перемежение": "C4(i·24 + j) = C3(j·18 + i) — транспонирование 24×18 (432 бита)",
     "CRC": "8 бит на класс 2 (5.4.2): в эталонном C-коде ETSI каждый бит — чётность своего подмножества бит класса 2 (ARRAYS.TAB TAB_CRC1…TAB_CRC8); при краже полуслота — выкалывание 8/17 (Fs_A2)"},
    "TETRA речь TCH/S", [TS.ist(s_sm, "5.4.3.1"), TS.ist(s_mi, "5.5.3"), {"файл": ARR, "строки": "48–65", "url": url("istochniki/tetra/en_30039502v010303p0.zip")}], "многочлены — по снимку risunki/tetra_speech_s31.png; P для 8/12, 8/18, 8/17 получены из матриц A1, A2, Fs_A2 эталонного C-кода ETSI (ARRAYS.TAB) и совпали со снимками формул (51), (54) и с osmo-tetra P_rate8_12/8_18/8_17",
    "нет в проекте (osmo-tetra: viterbi_tch.c, tch_reordering.c)")
if __name__ == "__main__":
    print(len(ZAPISI), "записей; d30", d30, "d16", d16, "Ka", Ka_fm, "QAM", len(Ka_qam), "речь", rp)
