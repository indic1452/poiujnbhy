"""TETRA, TETRAPOL, DMR, dPMR, NXDN, P25, D-STAR, YSF, DECT: построчная сверка и вычисления."""
import json, os
from pv import *
from p_3gpp import dmin, transp, T as TT
T = lambda f: json.load(open(os.path.join(KOREN, "tablicy", f)))["данные"]
TE = "istochniki/tetra/en_30039202v030801p.pdf"; TS = "istochniki/tetra/en_30039502v010303p.pdf"
TP = "istochniki/tetrapol/TETRAPOL_PAS0001-2_Radio_Air_Interface_v300.pdf"
DM = "istochniki/dmr/ts_10236101v020701p.pdf"; DP = "istochniki/dpmr/ts_102658v020601p.pdf"
NA = "istochniki/nxdn/NXDN-TS-1-A_v0103.pdf"; NE = "istochniki/nxdn/NXDN-TS-1-E_v0101.pdf"
P1 = "istochniki/p25/TIA-102-BAAA-A_Project_25_FDMA_CAI.pdf"; P2 = "istochniki/p25/TIA-102.BBAC_TDMA_MAC_Layer.pdf"
DS = "istochniki/dstar/JARL_STD6_0_E.pdf"; YS = "istochniki/ysf/Yaesu_Amateur_Radio_Digital_Specs_1V02.pdf"
DE3 = "istochniki/dect/en_30017503v020901p.pdf"; DE2 = "istochniki/dect/en_30017502v020901p.pdf"

def gf(prim, m):
    E = [0] * (2 * (1 << m)); Lg = [0] * (1 << m); v = 1
    for i in range((1 << m) - 1):
        E[i] = E[i + (1 << m) - 1] = v; Lg[v] = i; v <<= 1
        if v >> m: v ^= prim
    mul = lambda a, b: 0 if a == 0 or b == 0 else E[Lg[a] + Lg[b]]
    return E, Lg, mul
def rs_g(prim, m, korni):
    E, Lg, mul = gf(prim, m); g = [1]
    for j in korni:
        r = E[j % ((1 << m) - 1)]; ng = [0] * (len(g) + 1)
        for i, c in enumerate(g): ng[i] ^= c; ng[i + 1] ^= mul(c, r)
        g = ng
    return g
def minpoly(prim, m, e):
    """Минимальный многочлен α^e над GF(2) (целое, бит i = коэффициент x^i)."""
    E, Lg, mul = gf(prim, m); n = (1 << m) - 1
    cyc = []; x = e % n
    while x not in cyc: cyc.append(x); x = 2 * x % n
    p = [1]
    for c in cyc:
        r = E[c]; q = [0] * (len(p) + 1)
        for i, a in enumerate(p): q[i] ^= a; q[i + 1] ^= mul(a, r)
        p = q
    return sum(1 << (len(p) - 1 - i) for i, a in enumerate(p) if a)   # коэффициенты ∈ {0,1}

# ---------- TETRA ----------
z = zapis("TETRA RCPC 16 состояний")
str_ok(z, TE, 129, "8.2.3.1.1 многочлены", "G1(D) = 1 + D + D4", "G2(D) = 1 + D2 + D3+ D4", "G3(D) = 1 + D + D2 + D4", "G4(D) = 1 + D + D3 + D4")
str_ok(z, TE, 129, "8.2.3.1.3–6 выкалывание", "P(1) = 1, P(2) = 2, P(3) = 5, and i = j", "P(1) = 1, P(2) = 2, P(3) = 3, P(4) = 5, P(5) = 6, P(6) = 7, and i = j", "i = j + (j - 1) div 65", "i = j +(j -1) div 35")
g = [st(0, 1, 4), st(0, 2, 3, 4), st(0, 1, 2, 4), st(0, 1, 3, 4)]
okt_mlad = [oct(x)[2:] for x in g]                      # D0 — младший разряд
okt_star = [oct(int(format(x, "05b")[::-1], 2))[2:] for x in g]   # D0 — старший разряд
zap = z["параметры"]["мать"]
ok(z["имя"], "пометка восьмеричной записи «23, 35, 27, 33 при записи старший = D0»", "старший = D0" not in zap or okt_star == ["23", "35", "27", "33"],
   f"по многочленам текста: D0 — младший разряд → {okt_mlad}; D0 — старший → {okt_star}; запись в каталоге: «{zap[zap.find('(восьм'):]}»")
z = zapis("TETRA RM(30,14)")
P = T("tetra_392-2_395-2.json")["RM_30_14_P"]
G = [[1 if j == i else 0 for j in range(14)] + P[i] for i in range(14)]
ok(z["имя"], "d_min RM(30,14) = 8 (перебор 16383 слов)", dmin(G) == 8, f"{dmin(G)}")
tok = [int(x) for x in __import__("re").findall(r"\b[01]\b", stranica(TE, 130))]
ok(z["имя"], "матрица P (8.13) — все 14 строк по 16 бит подряд в тексте стр. 130", all(any(tok[i:i + 16] == r for i in range(len(tok) - 15)) for r in P))
z = zapis("TETRA RM(16,5)")
P = T("tetra_392-2_395-2.json")["RM_16_5_P"]
ok(z["имя"], "d_min RM(16,5) = 8", dmin([[1 if j == i else 0 for j in range(5)] + P[i] for i in range(5)]) == 8)
z = zapis("TETRA CRC-16 (K1+16")
str_ok(z, TE, 130, "8.2.3.3", "G(X) = X16 + X12 + X5 + 1 (8.16)", "ITU-T X.25")
z = zapis("TETRA (K,a)-блочный")
str_ok(z, TE, 134, "8.2.4.1 формула", "k = 1 + ((a × i) mod K)")
for K_, a_, p_ in ((120, 11, 140), (168, 13, 144), (216, 101, 142), (252, 17, 144), (324, 19, 143), (432, 103, 145), (648, 23, 145)):
    str_ok(z, TE, p_, f"пара ({K_},{a_}) в 8.3", f"({K_}, {a_})")
ok(z["имя"], "все (K,a) фазовой модуляции — перестановки", all(len({(a * i) % K for i in range(1, K + 1)}) == K for K, a in z["параметры"]["фазовая модуляция (K,a)"]))
z = zapis("TETRA скремблер 32 бита")
str_ok(z, TE, 136, "8.2.5.2 c(x)", "ci = 1 for i = 0, 1, 2, 4, 5, 7, 8, 10, 11, 12, 16, 22, 23, 26 and 32")
import re as _re
ok(z["имя"], "многочлен записи = отводы текста", sorted([int(x) for x in _re.findall(r"x(\d+)", z["параметры"]["многочлен"])] + [0, 1]) == sorted([0, 1, 2, 4, 5, 7, 8, 10, 11, 12, 16, 22, 23, 26, 32]))
z = zapis("TETRA TEDS PCCC")
str_ok(z, TE, 132, "8.2.3.4.1 RSC", "G0(D) = 1 + D2 + D3 (8.23)", "G1(D) = 1 + D + D3 (8.24)")
z = zapis("TETRA речевой канал ACELP")
ok(z["имя"], "5.4.3.1 (48): многочлены по снимку risunki/tetra_speech_s31.png (формула графикой) — просмотрено: G1 = 1+D+D2+D3+D4, G2 = 1+D+D3+D4, G3 = 1+D2+D4; классы 30/56/51 (примечание табл.)", z["параметры"]["мать"] == "1/3: G1 = 1+D+D2+D3+D4, G2 = 1+D+D3+D4, G3 = 1+D2+D4")
str_ok(z, TS, 31, "примечание классов", "class 2 = 30 bits, class 1 = 56 bits, class 0 = 51 bits")

# ---------- TETRAPOL ----------
z = zapis("TETRAPOL речевой кадр")
str_ok(z, TP, 17, "6.1.2 кодирование", "C2j = (b\"j + b\"j-1 + b\"j-2) modulo 2", "C2j+1 = (b\"j + b\"j-2) modulo 2", "b\" -2 = b\"24 and b\"-1 = b\"25")
z = zapis("TETRAPOL высокоскоростные")
g = sum(1 << s for s in T("tetrapol_pas0001-2.json")["BCH_g28_stepeni"])
pr = pmul(pmul(st(0, 3, 7), st(0, 1, 2, 3, 7)), pmul(st(0, 2, 3, 4, 7), st(0, 1, 2, 4, 5, 6, 7)))
ok(z["имя"], "g(D) = произведению 4 множителей записи и делит x^127+1", g == pr and pmod((1 << 127) | 1, g) == 0, f"deg g = {g.bit_length() - 1}")
z = zapis("TETRAPOL RACH")
str_ok(z, TP, 26, "6.4.3 перемежение", "j = (k x 31) modulo 75", "{s0 ...s7} = {10111110}")
ok(z["имя"], "перемежение 31k mod 75 — перестановка; таблица = формуле", T("tetrapol_pas0001-2.json")["RACH_perem"] == [(31 * k) % 75 for k in range(75)])

# ---------- DMR ----------
D = T("dmr_102361-1.json")
z = zapis("DMR Голей (20,8,8)")
ok(z["имя"], "d_min (20,8) = 8 по матрице табл. B.11", dmin(D["Golay(20,8)"]["G"]) == 8)
tok = [int(x) for x in _re.findall(r"\b[01]\b", stranica(DM, 137))]
ok(z["имя"], "строки табл. B.11 подряд в тексте стр. 137", all(any(tok[i:i + 20] == r for i in range(len(tok))) for r in D["Golay(20,8)"]["G"]))
z = zapis("DMR QR (16,7,6)")
str_ok(z, DM, 137, "B.3.2", "G(x) = x8 + x5 + x4 + x3 + 1 = 4718")
ok(z["имя"], "d_min (16,7) = 6", dmin(D["QR(16,7,6)"]["G"]) == 6)
z = zapis("DMR Хэмминги")
ok(z["имя"], "d_min всех Хэммингов = заявленным (3/3/3/4/3)", [dmin(D[k]["G"]) for k in ("Hamming(17,12,3)", "Hamming(13,9,3)", "Hamming(15,11,3)", "Hamming(16,11,4)", "Hamming(7,4,3)")] == [3, 3, 3, 4, 3])
z = zapis("DMR RS(12,9)")
str_ok(z, DM, 139, "B.3.6", "G(x) = (x + α) (x + α2) (x + α3)", "g(x) = x3 + 0e x2 + 38 x + 40")
gg = rs_g(0x11D, 8, [1, 2, 3])
ok(z["имя"], "вычислено ∏(x+α^i), i=1..3 над GF(256)/0x11D", gg == [1, 0x0E, 0x38, 0x40], f"{[hex(c) for c in gg]}")
z = zapis("DMR CRC: CRC-CCITT")
str_ok(z, DM, 141, "B.3.7/B.3.8", "G8(x) = x8 + x2 + x + 1", "GH(x) = x16 + x12 + x5 + 1")
m = z["параметры"]["маски"]
str_ok(z, DM, 145, "табл. B.21 маски", *[f"{k} {v}16" for k, v in m.items() if k in ("PI Header", "Voice LC Header", "Terminator with LC", "CSBK", "MBC Header", "Data Header", "Unified Single Block Data")], "Reverse Channel, see note 3 7A16")

# ---------- dPMR ----------
z = zapis("dPMR CCH")
str_ok(z, DP, 95, "7.1/7.2", "CRC7 X7 + X3 + 1", "A shortened Hamming code (12,8)")
Gd = T("dpmr_102658.json")["Hamming_12_8_G"]
ok(z["имя"], "Хэмминг (12,8): d_min = 3, проверочная часть = x^(4+i) mod (x4+x+1) (циклический укороченный)", dmin(Gd) == 3 and all(int("".join(map(str, r[8:])), 2) == pmod(1 << (4 + 7 - i), st(4, 1, 0)) for i, r in enumerate(Gd)))
z = zapis("dPMR синхрослова")
str_ok(z, DP, 91, "6.1.1/6.1.2", "57 FF 5F 75 D5 7716", "5F F7 7D16")
fs1 = int("57FF5F75D577", 16); fs4 = int("FD55F5DF7FDD", 16)
ok(z["имя"], "FS4 = посимвольное дополнение FS1 (дибиты 01↔11, 11↔01)", all(((fs1 >> (2 * i)) & 3) ^ ((fs4 >> (2 * i)) & 3) == 2 for i in range(24)))

# ---------- NXDN ----------
z = zapis("NXDN Type-C/конв. CAC (Outbound)")
str_ok(z, NA, 43, "CAC: свёрточный, перемежение", "G1(D) = 1 + D 3 + D 4", "G2 (D) = 1 + D + D 2 + D 4", "the number of information bits is 155 bits and the interleaving depth is 25")
str_ok(z, NA, 43, "CAC: матрица выкалывания (строки подряд)", "1 1 1 1 1 1 1 1 0 1 1 1 0 1")
z = zapis("NXDN скремблер PN9")
str_ok(z, NA, 61, "4.6", "0 1 1 1 0 0 1 0 0", "X9 ＋ X4 ＋ 1")
s = 0b011100100; seq = set()
for i in range(600):
    seq.add(s); fb = ((s >> 8) ^ (s >> 3)) & 1; s = ((s << 1) | fb) & 0x1FF
ok(z["имя"], "X9+X4+1 примитивен: период 511 (вычислено)", len(seq) == 511)
z = zapis("NXDN синхрослово FSW")
str_ok(z, NA, 41, "4.4.4", "-3, +1, -3, +3, -3, -3, +3, +3, -1, +3", "CDF59")
sym = {"+1": 0b00, "+3": 0b01, "-1": 0b10, "-3": 0b11}
v = 0
for t in "-3 +1 -3 +3 -3 -3 +3 +3 -1 +3".split(): v = (v << 2) | sym[t]
ok(z["имя"], "символы → 0xCDF59 при отображении дибитов NXDN (+1=00,+3=01,−1=10,−3=11)", v == 0xCDF59, hex(v))
z = zapis("NXDN CRC-32")
str_ok(z, NE, 124, "9.3", "X32", sosedi=0)

# ---------- P25 ----------
z = zapis("P25 NID: BCH")
str_ok(z, P1, 52, "8.5.2", "g(x) = 6331 1413 6723 5453 in octal", "47-th degree with 27 non-zero terms")
gb = int("6331141367235453", 8)
lcm = 1; seen = set()
for e in range(1, 23):
    mp = minpoly(0x43, 6, e)
    if mp not in seen: seen.add(mp); lcm = pmul(lcm, mp)
ok(z["имя"], "g(x) = НОК минимальных многочленов α^1…α^22 над GF(64)/x^6+x+1 (граница БЧХ d ≥ 23); степень 47, 27 членов",
   gb == lcm and gb.bit_length() - 1 == 47 and bin(gb).count("1") == 27, f"НОК = {oct(lcm)}")
z = zapis("P25 Голей (24,12,8)")
str_ok(z, P1, 29, "табл. 5-3", "1 4000 6165", "12 0001 4353")
z = zapis("P25 циклический (16,8,5)")
str_ok(z, P1, 28, "5.6 пример", "\"A\" = $41 encodes to $41 1e")
ok(z["имя"], "вычислено: 0x41·x^8 mod (x8+x5+x4+x3+1) = 0x1E", pmod(0x41 << 8, st(8, 5, 4, 3, 0)) == 0x1E)
z = zapis("P25 фаза 2: RS (63,35,29)")
gt = [int(x, 8) for x in "1 26 55 65 12 51 67 43 12 26 35 27 15 75 55 42 67 50 45 56 61 42 51 11 53 07 24 13 34".split()]
str_ok(z, P2, 149, "прил. A (13)", "G(x) = x28 + 26 x27 + 55 x26 + 65 x25 + 12 x24", "53 x4 + 07 x3 + 24 x2 + 13 x + 34")
ok(z["имя"], "вычислено ∏(x+α^j), j=1..28 над GF(64)/x^6+x+1 = коэффициентам текста", rs_g(0x43, 6, range(1, 29)) == gt)
z = zapis("P25 фаза 2: ISCH")
str_ok(z, P2, 49, "C0 (указана в записи стр. 12 — это оглавление)", "C0 = %0001 1000 0100 0010 0010 1001 1101 0100 0110 0001")
ok(z["имя"], "двоичная C0 = 0x184229D461", int("0001100001000010001010011101010001100001", 2) == 0x184229D461)

# ---------- D-STAR / YSF / DECT ----------
z = zapis("YSF синхрослово")
str_ok(z, YS, 14, "FS", "Synchronized signal (40 bit) D471C9634D")
z = zapis("DECT R-CRC")
str_ok(z, DE3, 104, "6.2.5.2", "g(x) = x16 + x10 + x8 + x7 + x3 + 1 = 202 611 (oct)")
ok(z["имя"], "202611₈ = степеням", int("202611", 8) == st(16, 10, 8, 7, 3, 0))
z = zapis("DECT X-CRC")
str_ok(z, DE3, 106, "6.2.5.4 (вкл. опечатку 14 016)", "x4 + 1 = 21 (oct)", "x8 + 1 = 401 (oct)", "x12 + x11 + x3 + x2 + x + 1 = 14 016 (oct)")
ok(z["имя"], "опечатка стандарта подтверждена: x12+x11+x3+x2+x+1 = 14017₈, а не 14016₈", oct(st(12, 11, 3, 2, 1, 0))[2:] == "14017")
z = zapis("DECT синхрослова S-поля")
str_ok(z, DE2, 23, "4.6", "1010 1010 1010 1010 1110 1001 1000 1010", "0101 0101 0101 0101 0001 0110 0111 0101")
z = zapis("D-STAR заголовок")
mtx = T("dstar_zagolovok.json")["matrica_24x28"]
str_ok(z, DS, 92, "Ap2.2 матрица перемежения (первые строки)", "0 0 24 48 72 96 120 144 168 192 216 240 264 288 312 336 360 384 408 432 456 480 504 528 552 576 600 624 648")
