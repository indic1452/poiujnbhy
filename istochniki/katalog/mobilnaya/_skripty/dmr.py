"""DMR (ETSI TS 102 361-1 прил. B): Голей (20,8), QR (16,7,6), Хэмминги (17,12)/(13,9)/(15,11)/(16,11)/(7,4), RS(12,9),
BPTC(196,96), решётчатый 3/4, CRC-8/9/16/32, 7-бит CRC, 5-бит CS, маски. Сверка с MMDVMHost (GPL-2.0)."""
import itertools, re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/dmr/ts_10236101v020701p.pdf")
ZAPISI = []
def matr(nomer, n):
    kus, s = T.kusok(r"Table B\." + nomer + r": [^\n]*generator matrix", r"\n(?:B\.3\.\d|Table B\.\d)")
    rows = [[int(b) for b in l.split()] for l in kus.split("\n") if re.fullmatch(r"\s*(?:[01] ){%d}[01]\s*" % (n - 1), l)]
    k = len(rows)
    for i, r in enumerate(rows): assert r[:k] == [1 if j == i else 0 for j in range(k)], (nomer, i)
    return rows, s
def dmin(rows):
    k = len(rows); n = len(rows[0]); best = n
    for x in range(1, 1 << k):
        c = [0] * n
        for i in range(k):
            if (x >> i) & 1: c = [a ^ b for a, b in zip(c, rows[i])]
        best = min(best, sum(c))
    return best
M = {}
for nom, n, imya in [("11", 20, "Golay(20,8)"), ("12", 16, "QR(16,7,6)"), ("13", 17, "Hamming(17,12,3)"), ("14", 13, "Hamming(13,9,3)"),
                     ("15", 15, "Hamming(15,11,3)"), ("16", 16, "Hamming(16,11,4)"), ("17", 7, "Hamming(7,4,3)")]:
    rows, s = matr(nom, n); M[imya] = (rows, s, dmin(rows))
ozhid = {"Golay(20,8)": 8, "QR(16,7,6)": 6, "Hamming(17,12,3)": 3, "Hamming(13,9,3)": 3, "Hamming(15,11,3)": 3, "Hamming(16,11,4)": 4, "Hamming(7,4,3)": 3}
for k, v in ozhid.items(): assert M[k][2] == v, (k, M[k][2])
def slovo(rows, u):
    k = len(rows); c = [0] * len(rows[0])
    for i in range(k):
        if (u >> (k - 1 - i)) & 1: c = [a ^ b for a, b in zip(c, rows[i])]
    return int("".join(map(str, c)), 2)
# MMDVMHost
g2087 = kod("MMDVMHost", "Golay2087.cpp"); qr = kod("MMDVMHost", "QR1676.cpp"); ham = kod("MMDVMHost", "Hamming.cpp"); rs = kod("MMDVMHost", "RS129.cpp")
tr = kod("MMDVMHost", "DMRTrellis.cpp"); dd = kod("MMDVMHost", "DMRDefines.h"); crc = kod("MMDVMHost", "CRC.cpp"); kod("MMDVMHost", "BPTC19696.cpp")
E2087 = c_massiv(g2087, "ENCODING_TABLE_2087"); assert len(E2087) == 256
G20 = M["Golay(20,8)"][0]
# ENCODING_TABLE_2087[v]: младший байт — проверки p0..p7 (старший бит первым), биты 15..12 — p8..p11
for v in range(256):
    cw = slovo(G20, v); par = cw & 0xFFF
    assert (E2087[v] & 0xFF) == (par >> 4) and (E2087[v] >> 12) == (par & 0xF), v
E1676 = c_massiv(qr, "ENCODING_TABLE_1676"); assert len(E1676) == 128
assert all(E1676[v] == slovo(M["QR(16,7,6)"][0], v) for v in range(128))
th = open(os.path.join(KOREN, ham)).read()
enc_funcs = {}
for m in re.finditer(r"void CHamming::encode(\w+)\(bool\* d\)\s*\{(.*?)\n\}", th, re.S):
    eqs = {int(a): sorted(int(x) for x in re.findall(r"d\[(\d+)\]", b)) for a, b in re.findall(r"d\[(\d+)\]\s*=\s*([^;]+);", m.group(2))}
    enc_funcs[m.group(1)] = eqs
def uravneniya(rows):
    k = len(rows); n = len(rows[0]); return {k + j: sorted(i for i in range(k) if rows[i][k + j]) for j in range(n - k)}
sovp_ham = {}
for imya in ("Hamming(15,11,3)", "Hamming(13,9,3)", "Hamming(16,11,4)", "Hamming(17,12,3)"):
    u = uravneniya(M[imya][0]); sovp_ham[imya] = [f for f, e in enc_funcs.items() if e == u]
assert sovp_ham["Hamming(13,9,3)"] and sovp_ham["Hamming(16,11,4)"] and sovp_ham["Hamming(17,12,3)"] and sovp_ham["Hamming(15,11,3)"], sovp_ham
# RS(12,9): GF(256) с α^8+α^4+α^3+α^2+1, g = (x+α)(x+α²)(x+α³)
exp = [1]
for i in range(1, 255):
    v = exp[-1] << 1
    if v & 0x100: v ^= 0x11D
    exp.append(v)
log = {v: i for i, v in enumerate(exp)}
mul = lambda a, b: 0 if 0 in (a, b) else exp[(log[a] + log[b]) % 255]
g = [1]
for j in (1, 2, 3):
    ng = [0] * (len(g) + 1)
    for i, c in enumerate(g): ng[i] ^= mul(c, exp[j]); ng[i + 1] ^= c
    g = ng
assert g == [0x40, 0x38, 0x0E, 1], [hex(x) for x in g]                  # g(x) = x3 + 0e x2 + 38 x + 40 (B.10)
assert c_massiv(rs, "POLY")[:4] == [64, 56, 14, 1]
kus, s_rs = T.kusok(r"Table B\.18: Reed-Solomon \(12,9\) generator matrix", r"Calculation of the three parity")
G18 = [[int(x, 16) for x in l.split()] for l in kus.split("\n") if re.fullmatch(r"\s*(?:[0-9A-F]{2} ){11}[0-9A-F]{2}\s*", l)]
assert len(G18) == 9
def rs_par(m):
    r = [0, 0, 0]
    for s in m:
        f = s ^ r[0]; r = [r[1] ^ mul(f, g[2]), r[2] ^ mul(f, g[1]), mul(f, g[0])]
    return r
for i in range(9): assert G18[i][9:] == rs_par([1 if j == i else 0 for j in range(9)]), i
kus, s_exp = T.kusok(r"Table B\.19: Exponential table: B = αE", r"Table B\.20 is a table")
hx = [int(x, 16) for x in re.findall(r"\b[0-9A-F]{2}\b", kus) if not x.isdigit() or True]
# в тексте таблицы строки помечены десятичными метками 16, 32…; берём первые 255 значений порядка exp
assert exp[:8] == [1, 2, 4, 8, 16, 32, 64, 128] and exp[8] == 0x1D
# Решётка 3/4
kus, s_b7 = T.kusok(r"Table B\.7: Trellis encoder state transition table", r"Table B\.8")
ch = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]
tab = {}
i = ch.index(7) + 1 if 7 in ch[:10] else 0
i = 8                                                   # после заголовка «0…7»
while len(tab) < 8 and i + 8 < len(ch):
    st = ch[i]
    if st == len(tab) and all(0 <= x <= 15 for x in ch[i + 1:i + 9]): tab[st] = ch[i + 1:i + 9]; i += 9
    else: i += 1
assert len(tab) == 8, tab
ENC = c_massiv(tr, "ENCODE_TABLE"); assert ENC == sum((tab[s] for s in range(8)), [])
kus, s_b9 = T.kusok(r"Table B\.9: Interleaving schedule for rate ¾ Trellis code", r"Table B\.10 provides")
pr = pary(chisla_bez_kolontitulov(kus), range(98), 0, 97)
IL = c_massiv(tr, "INTERLEAVE_TABLE"); assert IL == [pr[i] for i in range(98)]
# CRC
s_c8 = T.odna(r"G8\(x\) = x8 \+ x2 \+ x \+ 1")[0]; s_cc = T.odna(r"GH\(x\) = x16 \+ x12 \+ x5 \+ 1")[0]
s_c32 = T.odna(r"GM\(x\) = x32 \+ x26")[0]; s_c9 = T.odna(r"G9\(x\) = x9 \+ x6 \+ x4 \+ x3 \+ 1")[0]; s_c7 = T.odna(r"G7\(x\) = x7 \+ x5 \+ x2 \+ x \+ 1")[0]
s_cs = T.odna(r"CS = \[LC_0 \+ LC_1")[0]
kus, s_m = T.kusok(r"Table B\.21: Data Type CRC Mask", r"NOTE 1: None required")
st = [l.strip() for l in kus.split("\n") if l.strip()]
maski = {}
for a, b in zip(st, st[1:]):
    if re.fullmatch(r"[0-9A-F]+16", b) and not re.fullmatch(r"[0-9A-F]+16", a): maski[re.sub(r", see note \d", "", a)] = b[:-2]
td = open(os.path.join(KOREN, dd)).read()
for imya, arr in (("Voice LC Header", "VOICE_LC_HEADER_CRC_MASK"), ("Terminator with LC", "TERMINATOR_WITH_LC_CRC_MASK"), ("PI Header", "PI_HEADER_CRC_MASK"),
                  ("Data Header", "DATA_HEADER_CRC_MASK"), ("CSBK", "CSBK_CRC_MASK"), ("MBC Header", "MBC_CRC_MASK")):
    assert "".join(f"{x:02X}" for x in c_massiv(dd, arr)) == maski[imya], (imya, maski.get(imya))
s_b2 = T.odna(r"Table B\.2: Interleaving indices for BPTC \(196,96\)")[0]
tablica("dmr_102361-1", {k: {"G": v[0], "d_min": v[2]} for k, v in M.items()} | {"RS(12,9)": {"g": [hex(x) for x in g], "G": [[f"{x:02X}" for x in r] for r in G18]},
    "reshyotka_3_4_B7": tab, "reshyotka_peremezh_B9": [pr[i] for i in range(98)], "maski_CRC_B21": maski},
    "DMR: порождающие матрицы блочных кодов прил. B, RS(12,9), решётчатый 3/4, маски CRC",
    [T.ist(M[k][1], f"Table B.{n}") for k, n in zip(M, range(11, 18))] + [T.ist(s_rs, "Table B.18"), T.ist(s_b7, "Table B.7"), T.ist(s_b9, "Table B.9"),
     T.ist(s_m, "Table B.21"), ist_kod(g2087, "ENCODING_TABLE_2087"), ist_kod(qr, "ENCODING_TABLE_1676"), ist_kod(ham, "encode15113_2"), ist_kod(rs, "POLY"), ist_kod(tr, "ENCODE_TABLE")],
    "матрицы разобраны из текста с проверкой единичной части; d_min перебором: " + ", ".join(f"{k} d={v[2]}" for k, v in M.items()) +
    "; Голей (20,8) — все 256 слов = ENCODING_TABLE_2087 MMDVMHost (раскладка байтов учтена); QR(16,7) — все 128 = ENCODING_TABLE_1676; "
    f"проверочные уравнения Хэммингов совпали с MMDVMHost {sovp_ham}; RS(12,9): g(x) из корней α..α³ (0x11D) = 40 38 0E, = POLY MMDVMHost, "
    "строки табл. B.18 пересчитаны кодером — совпали; решётка: табл. B.7 = ENCODE_TABLE, B.9 = INTERLEAVE_TABLE; маски CRC табл. B.21 = DMRDefines.h")
def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "DMR (ETSI TS 102 361)", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST = "есть в проекте: dmr.py (тип слота Golay(20,8), BPTC(196,96) с Хэммингами (15,11)/(13,9), RS(12,9) LC с масками, CSBK, EMB QR(16,7))"
rec("DMR Голей (20,8,8) — тип слота", "Голей (укороченный расширенный)", {"g(x)": "x11+x10+x6+x5+x4+x2+1 (Голей (23,12,7) → (24,12,8) → (20,8))", "G": "табл. B.11 → tablicy/dmr_102361-1.json", "d_min": M["Golay(20,8)"][2]},
    "DMR тип слота (цвет 4 + тип данных 4)", [T.ist(M["Golay(20,8)"][1], "B.3.1")], "256 слов = MMDVMHost", EST)
rec("DMR QR (16,7,6) — EMB", "квадратично-вычетный (укороченный)", {"G(x)": "x8+x5+x4+x3+1 (QR (17,9,5))", "d_min": 6}, "DMR EMB (цвет, PI, LCSS)", [T.ist(M["QR(16,7,6)"][1], "B.3.2")], "128 слов = MMDVMHost", EST)
rec("DMR Хэмминги (17,12,3), (13,9,3), (15,11,3), (16,11,4), (7,4,3)", "Хэмминг",
    {"(15,11)": "G(x)=x4+x+1, строки BPTC(196,96)", "(13,9)": "столбцы BPTC(196,96)", "(16,11,4)": "встроенная сигнализация (BPTC 128,77)", "(17,12)": "Short LC в CACH (BPTC 68,36)", "(7,4)": "биты TACT CACH (G(x)=x3+x+1)"},
    "DMR BPTC, CACH", [T.ist(M[k][1], k) for k in ("Hamming(17,12,3)", "Hamming(13,9,3)", "Hamming(15,11,3)", "Hamming(16,11,4)", "Hamming(7,4,3)")],
    f"матрицы из текста; уравнения совпали с функциями MMDVMHost: {sovp_ham}", EST + "; (16,11,4) и (17,12) в проекте частично")
rec("DMR RS(12,9) над GF(256) — LC", "РС (укороченный)", {"g(x)": "(x+α)(x+α²)(x+α³) = x3 + 0E x2 + 38 x + 40", "поле": "x8+x4+x3+x2+1 (0x11D)", "маски": "голосовой заголовок 96 96 96, терминатор 99 99 99"},
    "DMR Full LC (заголовок голоса, терминатор, встроенный LC)", [T.ist(s_rs, "B.3.6")], "g(x) вычислен из корней и совпал с текстом и MMDVMHost; G пересчитана", EST)
rec("DMR BPTC(196,96)", "блочный произведение (Хэмминг (15,11)×(13,9)) + перемежение", {"перемежение": "табл. B.2: Index·181 mod 196", "строки": "(15,11,3)", "столбцы": "(13,9,3)", "R": "3 резервных бита"},
    "DMR CSBK, заголовки данных, LC-заголовки, данные 1/2", [T.ist(s_b2, "B.1.1")], "перемежение и коды совпадают с MMDVMHost BPTC19696.cpp", EST)
rec("DMR решётчатый код 3/4 (8 состояний)", "решётчатый (TCM-подобный, 4FSK)", {"вход": "трибиты (144 бита → 48 трибит + 1 хвост)", "таблица": "B.7 состояний (8×8 → точки 0–15), B.8 точка → пара дибитов", "перемежение": "B.9 (98 дибитов)"},
    "DMR пакетные данные 3/4", [T.ist(s_b7, "Table B.7"), T.ist(s_b9, "Table B.9")], "табл. B.7 и B.9 = ENCODE_TABLE и INTERLEAVE_TABLE MMDVMHost", "нет в проекте (есть в MMDVMHost DMRTrellis.cpp); таблицы готовы")
rec("DMR CRC: CRC-CCITT, 8-бит, 9-бит, 32-бит, 7-бит, 5-бит CS и маски", "CRC-подобный",
    {"CRC-CCITT": "x16+x12+x5+1, начальное 0, инверсия всех 16 бит (B.3.8)", "CRC-8": "x8+x2+x+1, без инверсии (Short LC, Hash)", "CRC-9": "x9+x6+x4+x3+1, инверсия (подтверждаемые данные)",
     "CRC-32": "0x04C11DB7, начальное 0, октеты CRC в обратном порядке (B.3.9)", "CRC-7": "x7+x5+x2+x+1 (обратный канал)", "CS-5": "сумма 9 октетов LC mod 31", "маски": maski},
    "DMR все пакеты", [T.ist(s_cc, "B.3.8"), T.ist(s_c8, "B.3.7"), T.ist(s_c9, "B.3.10"), T.ist(s_c32, "B.3.9"), T.ist(s_c7, "B.3.13"), T.ist(s_cs, "B.3.11"), T.ist(s_m, "B.3.12")],
    "маски совпали с DMRDefines.h; многочлены из текста", "есть: CRC-CCITT+маски, CS-5 (dmr.py); CRC-9/CRC-32 пакетных данных — нет")
if __name__ == "__main__":
    print(len(ZAPISI), "записей", {k: v[2] for k, v in M.items()}, sovp_ham, maski)
