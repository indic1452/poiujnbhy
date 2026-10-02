"""Независимая сверка записей оптического транспорта (G.975, G.709, G.975.1, G.709.2/.3, G.707, OIF)."""
import json, re
from pv import *

I = "istochniki/itu/"
G975 = I + "T-REC-G.975-200010-I.pdf"; G9751 = I + "T-REC-G.975.1-200402-I.pdf"; COR2 = I + "T-REC-G.975.1-201307-I_Cor2.pdf"
P11D = 0x11D

z = zapis("ITU-T G.975 RS(255,239)")
str_ok(z, G975, 10, "6.2: корни α^0…α^15, поле x^8+x^4+x^3+x^2+1, отображение байта", "binary primitive polynomial x8 + x4 + x3 + x2 + 1", "d7 ⋅ α7 + d6 ⋅ α6")
ok(z["имя"], "x^8+x^4+x^3+x^2+1 примитивен (свой расчёт)", primitiven(P11D))

z = zapis("ITU-T G.709 OTUk FEC")
G709 = I + "T-REC-G.709-202006-I.pdf"
str_ok(z, G709, 44, "11.2: скремблер 1 + x + x^3 + x^12 + x^16, сброс FFFF на MSB MFAS, после FEC", "The generating polynomial shall be 1 + x + x3 + x12 + x16", 'reset to "FFFF"', "MSB of the MFAS byte", "Scrambling is performed after FEC computation")
str_ok(z, G709, 188, "Annex A: 16 подстрок, RS(255,239)", "RS(255,239)", sosedi=1)

z = zapis("G.975.1 I.3:")
str_ok(z, G9751, 20, "I.3: BCH(3860,3824) внешний, BCH(2040,1930) внутренний", "BCH(3860,3824) is used as the outer code", "BCH(2040,1930) is used as", sosedi=0)
p12 = st(12, 11, 8, 6, 0); p11 = st(11, 2, 0)
def bch_g(m, prim, t):
    g = 1; s_ = set()
    for i in range(1, 2 * t, 2):
        mp = min_mnogochlen(i, m, prim)
        if mp not in s_: s_.add(mp); g = pmul(g, mp)
    return g
ok(z["имя"], "поля x^12+x^11+x^8+x^6+1 и x^11+x^2+1 примитивны; deg M1M3M5 = 36, deg M1…M19 = 110 (свой расчёт)",
   primitiven(p12) and primitiven(p11) and bch_g(12, p12, 3).bit_length() - 1 == 36 and bch_g(11, p11, 10).bit_length() - 1 == 110)
str_ok(z, COR2, 5, "Cor.2 п. 1–2: исправленные M_i и поля", "Corrigendum 2", "Clause I.3.2.1", "Clause I.3.2.2")
ispr(z["имя"], "источник Cor.2: страница с исправленными M_i(x)", "стр. 3", "стр. 5", "на стр. 3 PDF Cor.2 — только перечень изменений (Summary), формулы I.3.2.1/I.3.2.2 — на стр. 5")

z = zapis("G.975.1 I.4:")
str_ok(z, G9751, 25, "I.4.1/I.4.2.1: RS(1023,1007) m=10 T=8; 15×RS(781,765) + RS(778,762)", "RS(1023,1007) parent outer code, m = 10, T = 8", "15 RS(781,765) and one RS(778,762)", "= 0xf6")
str_ok(z, G9751, 26, "I.4.2.2/I.4.2.3: 88 проверочных = T·m", "The 88 bits result from the product of T = 8 and m = 11")
ok(z["имя"], "deg(M1·M3·…·M15) над x^11+x^2+1 = 88 (свой расчёт)", bch_g(11, p11, 8).bit_length() - 1 == 88)
str_ok(z, COR2, 8, "Cor.2: укорочение до BCH(2040,1952)", "shortened to BCH(2040,1952)")

z = zapis("G.975.1 I.7:")
t = stranica(G9751, 36)
def razbor_mn(s):
    return sum(1 << int(e or 1) if e != "0" else 1 for e in re.findall(r"x(\d*)|(?<=\+ )1(?!\d)", s))
row = re.findall(r"G(\d+)\(x\) = (x10[^\n]*)", t); col = re.findall(r"G(\d+)\(x\) = (x9[^\n]*)", t)
def mn(s):
    v = 0
    for term in s.split("+"):
        term = term.strip()
        if term == "1": v |= 1
        elif term == "x": v |= 2
        elif term.startswith("x"): v |= 1 << int(term[1:])
    return v
r_ok = all(mn(s) == min_mnogochlen(int(i), 10, mn(row[0][1])) for i, s in row)
c_ok = all(mn(s) == min_mnogochlen(int(i), 9, mn(col[0][1])) for i, s in col)
ok(z["имя"], f"табл. I.9: {len(row)} многочленов строк и {len(col)} столбцов = минимальные многочлены α^i (свой расчёт)", r_ok and c_ok and len(row) == 11 and len(col) == 4)
str_ok(z, G9751, 45, "табл. I.16: режимы 7/11/25 %", "900", "960", "884", "500", "510")

z = zapis("G.975.1 I.9:")
str_ok(z, G9751, 51, "I.9.2.1: поле, m3, m5, блок (522240,489472)", "(522240,489472)", "512 × 1020", "16 × 32 sub-blocks")
p10 = st(10, 3, 0)
ok(z["имя"], "m3 = x^10+x^3+x^2+x+1, m5 = x^10+x^8+x^3+x^2+1 — минимальные α^3, α^5; deg g_H = 32", min_mnogochlen(3, 10, p10) == st(10, 3, 2, 1, 0) and min_mnogochlen(5, 10, p10) == st(10, 8, 3, 2, 0) and (pmul(bch_g(10, p10, 3), st(2, 0))).bit_length() - 1 == 32)

z = zapis("G.975.1 I.5:")
ok(z["имя"], "RS(1901,1855): 46 корней α^1001…α^1046 = n − k; x^11+x^2+1 примитивен", 1046 - 1001 + 1 == 1901 - 1855 and primitiven(p11))
z = zapis("G.975.1 I.8:")
ok(z["имя"], "поле x^12+x^9+x^8+x^6+x^3+x^2+1 примитивно; n − k = 170 = 2t, t = 85", primitiven(st(12, 9, 8, 6, 3, 2, 0)) and 2720 - 2550 == 170)

# ---------- G.709.2 SC-FEC ----------
z = zapis("G.709.2 OTU4-SC")
G7092 = I + "T-REC-G.709.2-201807-I.pdf"
str_ok(z, G7092, 25, "A.6: поле 1 + x^3 + x^10", "p(x) = 1 + x3 + x10")
t = stranica(G7092, 25) + stranica(G7092, 26)
pi = {}
for a, b, c, d in re.findall(r"Π𝑑\((\d+)(?:\s*:\s*(\d+))?\)\s*=\s*(\d+)(?:\s*:\s*(\d+))?", t):
    a = int(a); b = int(b) if b else a; c = int(c); d = int(d) if d else c
    assert b - a == d - c
    for k in range(b - a + 1): pi[a + k] = c + k
tab = json.load(open(os.path.join(KOREN, "tablicy/g7092_sc_pi_d.json")))["данные"]
ok(z["имя"], "табл. A.2 разобрана заново: Π_d — перестановка 0…509 и совпадает с tablicy/g7092_sc_pi_d.json", sorted(pi) == list(range(510)) and sorted(pi.values()) == list(range(510)) and [pi[i] for i in range(510)] == tab, f"{len(pi)} значений")
# H по A.7.2 (своя сборка) и проверка всех 990 строк P: H·[e_j | p_j]^T = 0
exp, log = gf(10, st(10, 3, 0))
def b3(l): return [l >> i & 1 for i in range(3)]
def F(l):
    b0, b1, b2 = b3(l); n = lambda x: 1 - x
    return (b2 & n(b1) & n(b0)) | (n(b2) & b1) | (n(b2) & n(b1) & b0)
def stolb(i):
    b = i; b3_ = exp[(log[b] * 3) % 1023]; b5_ = exp[(log[b] * 5) % 1023]
    return b | (b3_ << 10) | (b5_ << 20) | (F(b) << 30) | ((1 - F(b)) << 31)
inv = {v: k for k, v in pi.items()}
H = [stolb(1021), stolb(1022)] + [stolb(i) for i in range(1, 511)] + [stolb(511 + inv[k]) for k in range(510)]
P = [int(x, 16) for x in json.load(open(os.path.join(KOREN, "tablicy/g7092_sc_P.json")))["данные"]]
plohih = 0
for j in range(990):
    s = H[j]
    for b in range(32):
        if P[j] >> (31 - b) & 1: s ^= H[990 + b]
    plohih += s != 0
ok(z["имя"], "своя H (A.7.2, F прочитана со снимка стр. 26) × каждое из 990 базисных слов [e_j | P_j] = 0 (tablicy/g7092_sc_P.json)", plohih == 0, f"ненулевых синдромов: {plohih}")

z = zapis("SDH/SONET in-band FEC")
G707 = I + "T-REC-G.707-200701-I.pdf"
str_ok(z, G707, 134, "A.2.1/A.2.2: (8191,8152), k = 4320, n = 4359, t = 3, G1/G3/G5", "(8191, 8152) parent code", "k = 4320 information bits plus 39 parity bits", "G1(x) = x13 + x4 + x3 + x + 1", "G3(x) = x13+ x10 + x9 + x7 + x5 + x4 + 1", "G5(x) = x13 + x11 + x8 + x7 + x4 + x + 1")
g1 = st(13, 4, 3, 1, 0)
ok(z["имя"], "G1 примитивен; G3, G5 — минимальные многочлены α^3, α^5 (свой расчёт)", primitiven(g1) and min_mnogochlen(3, 13, g1) == st(13, 10, 9, 7, 5, 4, 0) and min_mnogochlen(5, 13, g1) == st(13, 11, 8, 7, 4, 1, 0))

z = zapis("OIF 800LR")
p7 = st(7, 3, 0)
g = pmul(pmul(p7, min_mnogochlen(3, 7, p7)), st(2, 0))
ok(z["имя"], "g(x) = M1·M3·(x^2+1) над x^7+x^3+1 = x^16+x^14+x^11+x^10+x^9+x^7+x^5+x^3+x+1 (свой расчёт)", g == st(16, 14, 11, 10, 9, 7, 5, 3, 1, 0) and min_mnogochlen(3, 7, p7) == st(7, 3, 2, 1, 0), poly_str(g))
str_ok(z, "istochniki/oif/OIF-800LR-01.0.pdf", 20, "5.3: BCH(127,113) → (124,110) → (126,110), бит 0 → x^109, передача с x^125", "BCH (127,113) code that is", "shortened to a (124,110) code", "starting with the coefficient of 𝑥125")

z = zapis("OIF 400ZR C-FEC")
str_ok(z, "istochniki/oif/OIF-400ZR-03.0.1.pdf", 50, "10.3: скремблер 65535, сброс 0xFFFF в начале 5 блоков SC", "sequence 65535", "resets to 0xFFFF on row 1, column 1 of the five SC-FEC block")
