"""Третий проход проверки (записи, не проверенные p_gsm/p_3gpp/p_pmr/p_prochie): cdma2000/EV-DO, DMR BPTC и решётка, P25 (Хэмминг, РС GF(64),
фаза 2: DUID, скремблер), PDC, PHS, UMTS скремблирующие коды, LTE/NR синхросигналы, CT2 (тестовый пример прил. G), RTCM SC-104,
STANAG 4529, NXDN SACCH, GSM TCH/AFS, NR LDPC, D-STAR, DECT B-CRC — построчно по тексту PDF (pymupdf) и вычислениями.
Также проверяются добавленные третьим проходом записи (M17, AMBE, TFTS, CDPD) — их собственные assert в skripty/dop3_proverka.py."""
import re, glob, os, json
from pv import *
from p_3gpp import dmin, tokens, podryad

def f(pat): return [x.replace(KOREN + "/", "") for x in glob.glob(os.path.join(KOREN, "istochniki/*/" + pat))][0]
def tx(pdf, p): return re.sub(r"\s+", " ", stranica(pdf, p))
def pmulmod(a, b, g):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if a.bit_length() == g.bit_length(): a ^= g
    return r
def ppow(x, e, g):
    r = 1
    while e:
        if e & 1: r = pmulmod(r, x, g)
        x = pmulmod(x, x, g); e >>= 1
    return r
def prost(n):
    d, r = 2, set()
    while d * d <= n:
        while n % d == 0: r.add(d); n //= d
        d += 1 if d == 2 else 2
    if n > 1: r.add(n)
    return r
def primitiven(g):
    m = g.bit_length() - 1; n = (1 << m) - 1
    return ppow(2, n, g) == 1 and all(ppow(2, n // p, g) != 1 for p in prost(n))
def stp(s):
    """'x16 + x15 + … + x + 1' (любые буквы x/X/D) → множество степеней."""
    r = set()
    for t in s.replace(" ", "").split("+"):
        m = re.fullmatch(r"[xXDα](\d*)", t)
        r.add(0 if t == "1" else int(m.group(1)) if m.group(1) else 1)
    return r
def dfree(gens, K):
    import heapq
    m = K - 1
    def shag(s, b):
        reg = (b << m) | s
        return reg >> 1, sum(bin(reg & g).count("1") & 1 for g in gens)
    s0, w0 = shag(0, 1); best = {s0: w0}; q = [(w0, s0)]
    while q:
        w, s = heapq.heappop(q)
        if s == 0: return w
        if w > best.get(s, 1 << 30): continue
        for b in (0, 1):
            ns, dw = shag(s, b)
            if w + dw < best.get(ns, 1 << 30): best[ns] = w + dw; heapq.heappush(q, (w + dw, ns))

C2 = "istochniki/cdma2000/ARIB_STD-T64-C.S0002-Ev2.0.pdf"; EV = f("ARIB_STD-T64-C.S0024-Av3.0.pdf")
# ---------- cdma2000 свёрточные ----------
z = zapis("cdma2000/IS-95 свёрточные K=9")
mn = z["параметры"]["многочлены (восьм.)"]
str_ok(z, C2, 188, "1/4", "g0 equals 765 (octal), g1 equals 671", "g2 equals 513 (octal), and g3 equals 473 (octal)")
str_ok(z, C2, 189, "1/3", "g0 equals 557 (octal), g1 equals 663 (octal)", "g2 equals 711 (octal)")
str_ok(z, C2, 190, "1/2", "g0 equals 753 (octal) and g1 equals 561")
str_ok(z, C2, 423, "1/6", "g0 equals 457 (octal), g1 equals 755", "g2 equals 551 (octal), g3 equals 637 (octal), g4 equals 625", "727")
ok(z["имя"], "многочлены записи = тексту", mn == {"1/4": ["765", "671", "513", "473"], "1/3": ["557", "663", "711"], "1/2": ["753", "561"],
                                                   "1/6": ["457", "755", "551", "637", "625", "727"]})
df = {k: dfree([int(x, 8) for x in v], 9) for k, v in mn.items()}
ok(z["имя"], "d_free пересчитаны (Дейкстра по решётке)", df == z["параметры"]["d_free"], f"вычислено {df}, в записи {z['параметры']['d_free']}")

# ---------- cdma2000 CRC ----------
z = zapis("cdma2000 CRC качества кадра")
P = z["параметры"]["многочлены"]
str_ok(z, C2, 415, "16", "g(x) = x16 + x15 + x14 + x11 + x6 + x5 + x2 + x + 1")
str_ok(z, C2, 416, "12 и 10", "g(x) = x12 + x11 + x10 + x9 + x8 + x4 + x + 1", "g(x) = x10 + x9 + x8 + x7 + x6 + x4 + x3 + 1")
str_ok(z, C2, 417, "8", "g(x) = x8 + x7 + x4 + x3 + x + 1")
str_ok(z, C2, 185, "6", "g(x) = x6 + x5 + x2 + x + 1"); str_ok(z, C2, 186, "6 (RC2)", "g(x) = x6 + x2 + x + 1")
ok(z["имя"], "степени многочленов записи = тексту", stp(P["16"]) == {16, 15, 14, 11, 6, 5, 2, 1, 0} and stp(P["12"]) == {12, 11, 10, 9, 8, 4, 1, 0}
   and stp(P["10"]) == {10, 9, 8, 7, 6, 4, 3, 0} and stp(P["8"]) == {8, 7, 4, 3, 1, 0} and stp(P["6"]) == {6, 5, 2, 1, 0} and stp(P["6-RC2"]) == {6, 2, 1, 0})
str_ok(z, C2, 183, "2.1.3.1.4.1: начальное состояние", "shift register 4 elements shall be set to logical one")
str_ok(z, C2, 415, "3.1.3.1.4.1: исключение", "set to logical one except for inner frame quality indicator of the 5 7 ms frame on the Forward Packet Data Control Channel", "register elements shall be set to logical zero")
ok(z["имя"], "исключение (F-PDCCH 5 мс — нули) отражено в записи", "F-PDCCH" in z["параметры"]["начальное"] and "нули" in z["параметры"]["начальное"],
   f"в записи: «{z['параметры']['начальное']}» (исправлено при проверке: раньше было только «все единицы»)")

# ---------- cdma2000 BRO ----------
z = zapis("cdma2000 перемежитель с обращением бит")
str_ok(z, C2, 206, "2.1.3.1.8", "BROm(y) indicates the bit-reversed m-bit value of y (i.e., BRO3(6) = 3)")
bro = lambda y, m: int(format(y, f"0{m}b")[::-1], 2)
ok(z["имя"], "BRO3(6) = 3 и формула A_i — перестановка (m = 6, J = 12, N = 768)", bro(6, 3) == 3 and
   sorted((1 << 6) * (i % 12) + bro(i // 12, 6) for i in range(768)) == list(range(768)))

# ---------- cdma2000 длинный код и PN ----------
z = zapis("cdma2000/IS-95 длинный код")
str_ok(z, C2, 237, "p(x) длинного кода", "p(x) = x42 + x35 + x33 + x31 + x27 + x26 + x25 + x22 + x21 + x19 +", "x18 + x17 + x16 + x10 + x7 + x6")
str_ok(z, C2, 242, "PI/PQ", "PI(x) = x15 + x13 + x9 + x8 + x7 + x5 + 1", "PQ(x) = x15 + x12 + x11+ x10 + x")
g42 = st(*stp(z["параметры"]["длинный код"].split(",")[0].split("=")[1]))
ok(z["имя"], "p(x) длинного кода примитивен (период 2^42 − 1)", g42.bit_length() == 43 and primitiven(g42))
ok(z["имя"], "PI(x), PQ(x) примитивны (период 2^15 − 1 до вставки нуля)", primitiven(st(*stp(z["параметры"]["PI(x)"]))) and primitiven(st(*stp(z["параметры"]["PQ(x)"]))))

z = zapis("1xEV-DO (IS-856) скремблер")
str_ok(z, EV, 995, "12.3.1.3.2.3.3", "generator sequence of h(D) = D17 + D14 + 1")
ok(z["имя"], "D17 + D14 + 1 примитивен (период 131071)", primitiven(st(17, 14, 0)))

# ---------- DMR ----------
DM = "istochniki/dmr/ts_10236101v020701p.pdf"
z = zapis("DMR BPTC(196,96)")
tr = re.findall(r"(?:R|I|H_R\d+|H_C\d+|H_Cn|H_Rn)\(\d+\) (\d+) (\d+)", tx(DM, 123) + " " + tx(DM, 124))
ok(z["имя"], "табл. B.2: все 196 строк = Index·181 mod 196", len({int(a) for a, b in tr}) == 196 and all(int(b) == int(a) * 181 % 196 for a, b in tr), f"разобрано {len(tr)} строк")
z = zapis("DMR решётчатый код 3/4")
t = tx(DM, 132); s = [w for w in t[t.index("Input Tribit 0 1 2 3 4 5 6 7") + len("Input Tribit 0 1 2 3 4 5 6 7"):].split() if w.isdigit()]
B7 = []
for r in range(8):
    grp = [int(x) for x in s[r * 9:(r + 1) * 9]]; assert grp[0] == r, grp; B7 += grp[1:]
src = open(os.path.join(KOREN, "istochniki/kod/MMDVMHost/DMRTrellis.cpp")).read()
ENC = [int(x) for x in re.findall(r"\d+", re.sub(r"//[^\n]*", "", re.search(r"ENCODE_TABLE\[\] = \{(.*?)\};", src, re.S).group(1)))]
ok(z["имя"], "табл. B.7 (8 состояний × 8 трибит) = ENCODE_TABLE MMDVMHost", B7 == ENC, f"{B7[:16]} …")
INT = [int(x) for x in re.findall(r"\d+", re.sub(r"//[^\n]*", "", re.search(r"INTERLEAVE_TABLE\[\] = \{(.*?)\};", src, re.S).group(1)))]
t = tx(DM, 133); tok = [int(x) for x in t[t.index("Enc Index Input Index Enc Index Input Index Enc Index Input Index Enc Index Input Index") + 87:].split() if x.isdigit()]
pary = {tok[i]: tok[i + 1] for i in range(0, 196, 2)}
pr = INT == [pary[e] for e in range(98)]; ob = all(INT[pary[e]] == e for e in range(98))
ok(z["имя"], "табл. B.9 (98 пар Enc → Input) = INTERLEAVE_TABLE MMDVMHost", len(pary) == 98 and (pr or ob), f"прямое {pr}, обратное {ob}")

# ---------- P25 ----------
P1 = "istochniki/p25/TIA-102-BAAA-A_Project_25_FDMA_CAI.pdf"; P2 = "istochniki/p25/TIA-102.BBAC_TDMA_MAC_Layer.pdf"
z = zapis("P25 Хэмминг (15,11,3)")
str_ok(z, P1, 29, "5.8", "(15,11,3) standard Hamming code and the (10,6,3) shortened Hamming code", "g(x) = x4 + x + 1")
ok(z["имя"], "x4 + x + 1 примитивен → циклический Хэмминг (15,11), d = 3", primitiven(st(4, 1, 0)))
z = zapis("P25 RS (36,20,17)")
str_ok(z, P1, 30, "5.9 поле GF(64)", "α+1 000 011 03 α6", "α5+α4+α3+α2+α 111 110 76 α57", "α5+α4+α3+α2+α+1 111 111 77 α58")
E = [1]
for _ in range(62): v = E[-1] << 1; E.append(v ^ 0x43 if v & 64 else v)
ok(z["имя"], "α^6+α+1 (0x43): α^6 = 03, α^57 = 76, α^58 = 77 (восьм.) — как в примерах текста", E[6] == 0o3 and E[57] == 0o76 and E[58] == 0o77 and primitiven(0x43))
z = zapis("P25 фаза 2: скремблер LFSR 44")
str_ok(z, P2, 65, "7.2 (4)", "G(x) = x44 + x40 + x35 + x29 + x24 + x10 + 1")
ok(z["имя"], "многочлен скремблера примитивен (период 2^44 − 1)", primitiven(st(44, 40, 35, 29, 24, 10, 0)))
z = zapis("P25 фаза 2: DUID")
t = tx(P2, 46); bits = re.findall(r"[01]", t[t.index("Table 5-2 DUID Generator Matrix") + 30:])[:32]
G = [[int(b) for b in bits[i * 8:(i + 1) * 8]] for i in range(4)]
ok(z["имя"], "табл. 5-2 = G записи; d_min = 4", ["".join(map(str, r)) for r in G] == z["параметры"]["G"] and dmin(G) == 4, f"{['' .join(map(str, r)) for r in G]}")

# ---------- PDC, PHS ----------
PD = f("ARIB_RCR_STD-27L_1of3.pdf"); PH = f("ARIB_RCR_STD-28v5.3_1of2.pdf")
z = zapis("PDC скремблер PN(9,5)")
str_ok(z, PD, 150, "4.1.7 (2)", "The scrambling pattern is the output of the PN(9,5) structure", "initial values of the registers have a one-to-one correspondence with the pattern numbers")
ok(z["имя"], "x9 + x5 + 1 примитивен (период 511)", primitiven(st(9, 5, 0)))
z = zapis("PHS: CRC-16")
str_ok(z, PH, 138, "4.2.10.1", "ITU-T 16 bit CRC Generator polynomial: 1 + X5 + X12 + X16", "12 bit CRC Generator polynomial: 1 + X + X2 + X3 + X11 + X12")
str_ok(z, PH, 137, "UW π/4-QPSK", "Uplink 0110 1011 1000 1001 1001 1010 1111 0000", "Downlink 0101 0000 1110 1111 0010 1001 1001 0011")
ok(z["имя"], "UW в записи = тексту", "0110 1011 1000 1001 1001 1010 1111 0000" in z["параметры"]["UW"] and "0101 0000 1110 1111 0010 1001 1001 0011" in z["параметры"]["UW"])
str_ok(z, PH, 177, "PN(10,3)", "The PN pattern used is PN (10,3)")
ok(z["имя"], "x10 + x3 + 1 примитивен (период 1023)", primitiven(st(10, 3, 0)))

# ---------- UMTS скремблирующие коды ----------
U3 = f("ts_125213v190000p.pdf")
z = zapis("UMTS скремблирующие коды")
str_ok(z, U3, 33, "4.3.2.2", "primitive (over GF(2)) polynomial X25+X3+1", "polynomial X25+X3+X2+X+1", "16777232 chip shifted")
ok(z["имя"], "X25+X3+1 примитивен; X18+X7+1 примитивен; сдвиг 16777232 = 2^24 + 16", primitiven(st(25, 3, 0)) and primitiven(st(18, 7, 0)) and 16777232 == (1 << 24) + 16)
dl = [p for p in range(36, 46) if "131072" in tx(U3, p)]
ok(z["имя"], "нисходящий: Q-сдвиг 131072 есть в тексте 5.2.2", bool(dl), f"страницы {dl}")

# ---------- LTE / NR синхросигналы ----------
L1 = f("ts_136211v190300p.pdf"); N1 = f("ts_138211v190500p.pdf")
z = zapis("LTE синхросигналы")
str_ok(z, L1, 183, "табл. 6.11.1.1-1", "Root index u 0 25 1 29 2 34")
ok(z["имя"], "SSS: x^5+x^2+1, x^5+x^3+1 примитивны; x^5+x^4+x^2+x+1 примитивен (период 31)", primitiven(st(5, 2, 0)) and primitiven(st(5, 3, 0)) and primitiven(st(5, 4, 2, 1, 0)))
def m01(N1_):
    qq = N1_ // 30; q = (N1_ + qq * (qq + 1) // 2) // 30; mp = N1_ + q * (q + 1) // 2; m0 = mp % 31
    return m0, (m0 + mp // 31 + 1) % 31
tok = tokens(L1, 185, 186)
ok(z["имя"], "m0/m1 по формуле = табл. 6.11.2.1-1 (тройки N1 m0 m1 подряд в тексте)", sum(podryad(tok, [n, *m01(n)]) for n in range(168)) >= 160,
   f"найдено троек {sum(podryad(tok, [n, *m01(n)]) for n in range(168))} из 168")
z = zapis("5G NR синхросигналы")
ok(z["имя"], "x^7+x^4+1 и x^7+x+1 примитивны (период 127); 1008 = 336·3", primitiven(st(7, 4, 0)) and primitiven(st(7, 1, 0)) and 336 * 3 == 1008)
str_ok(z, N1, 150, "7.4.2.2.1", "127 mod 43")

# ---------- CT2: пример кодового слова прил. G ----------
CT = f("iets_300131e02p.pdf")
z = zapis("CT2 / CAI (I-ETS 300 131): D-канал")
str_ok(z, CT, 53, "6.3.6", "x15 + x14 + x13 + x11 + x4 + x2 + 1", "Bit 7 of octet 8 is inverted", "Bit 8 of octet 8 is added such that the whole 64-bit code word has even parity")
str_ok(z, CT, 37, "CHMF/CHMP/SYNCF", "CHMF 1011 . 1110 . 0100 . 1110 . 0101 . 0000 (BE4E50H)", "CHMP 0100 . 0001 . 1011 . 0001 . 1010 . 1111 (41B1AFH)", "SYNCF 1110 . 1011 . 0001 . 1011 . 0000 . 0101 (EB1B05H)")
t = tx(CT, 213)
okt = [re.findall(r"[01]", o) for o in re.findall(r"³ ((?:[01] ){8})³ \d", t)]
assert len(okt) == 8
okt = [list(map(int, o)) for o in okt]                       # в таблице биты 8…1
dann = [b for o in okt[:6] for b in o[::-1]]                 # передача: бит 1 первым
g = st(15, 14, 13, 11, 4, 2, 0)
ost = pmod(int("".join(map(str, dann)), 2) << 15, g)
chk = (ost ^ 1) << 1
slovo = dann + [int(b) for b in format(ost ^ 1, "015b")]
chk |= sum(slovo) & 1
peredano = [b for o in okt[6:] for b in o[::-1]]
ok(z["имя"], "прил. G: остаток 000010101111010, слово проверки 0000101011110111 воспроизведены (порядок: бит 1 октета первым)",
   format(ost, "015b") == "000010101111010" and format(chk, "016b") == "0000101011110111" == "".join(map(str, peredano)) and "000010101111010" in t,
   f"остаток {format(ost, '015b')}, проверка {format(chk, '016b')}")

# ---------- RTCM SC-104 ----------
R = f("RTCM_SC104_DGPS_Betke2001.pdf")
z = zapis("RTCM SC-104")
t = tx(R, 6); ur = dict(re.findall(r"(D2[5-9]|D30) = (D(?:29|30)'(?:\*d\d+)+)", t))
def mn_(s): return set(re.findall(r"d(\d+)", s)) | {re.match(r"(D\d+)", s).group(1)}
zap = {k: set(re.findall(r"d(\d+)", v)) | {re.search(r"(D\d+)\*", v).group(1)} for k, v in z["параметры"]["уравнения"].items()}
ok(z["имя"], "6 уравнений чётности D25–D30 записи = тексту Betke 2001 стр. 6", len(ur) == 6 and all(zap[k] == mn_(v) for k, v in ur.items()), f"разобрано {len(ur)}")

# ---------- STANAG 4529 ----------
S = f("STANAG_4529_conformance_2004.pdf")
z = zapis("STANAG 4529")
str_ok(z, S, 12, "1.2 f", "The generator polynomial is: x5 + x2 +1", "initially set to the following value: 11010")
str_ok(z, S, 13, "1.2 g", "generator polynomial of which is: x9 + x4 + 1", "initialized to 1 at the start of each frame")

ok(z["имя"], "x5+x2+1 и x9+x4+1 примитивны (периоды 31 и 511); кадр 80 + 3·(32+16) + 32 = 256", primitiven(st(5, 2, 0)) and primitiven(st(9, 4, 0)) and 80 + 3 * 48 + 32 == 256)

# ---------- NXDN SACCH ----------
NA = f("NXDN-TS-1-A_v0103.pdf")
z = zapis("NXDN Type-C/конв. SACCH")
str_ok(z, NA, 49, "4.5.2.1", "6 bits CRC Generator Polynomial: X6 + X5 + X2 + X + 1", "Four tail bits, all set equal to zero")
pz = z["параметры"]["выкалывание (строки G1/G2)"]
t = tx(NA, 49)
mat = re.findall(r"[01]", t[t.index("the number of information bits is 26 bits and the interleaving depth is 5."):])[:12]
ok(z["имя"], "матрица выкалывания текста (6 столбцов × G1/G2) = записи", [list(map(int, mat[:6])), list(map(int, mat[6:]))] == pz, f"{mat}")
n_kod = 2 * (26 + 6 + 4)
ost_v = sum(pz[r][c % 6] for c in range(n_kod // 2) for r in range(2))
ok(z["имя"], "26 бит + CRC6 + 4 хвоста → 72 → выкалывание → 60 = 12 × 5 (глубина перемежения 5)", ost_v == 60 and "interleaving depth is 5" in t, f"после выкалывания {ost_v}")
T_ = [(i % 12) * 5 + i // 12 for i in range(60)]
ok(z["имя"], "формула перемежения записи — перестановка 60 позиций", sorted(T_) == list(range(60)))

# ---------- GSM TCH/AFS ----------
G4 = "istochniki/gsm/ts_145003v190000p.pdf"
z = zapis("GSM TCH/AFS")
str_ok(z, G4, 61, "TCH/AFS10.2", "G1/G3 = 1 + D + D3 + D4 / 1 + D + D2 + D3 + D4", "G2/G3 = 1 + D2 + D4 / 1 + D + D2 + D3 + D4", "resulting in 642 coded bits")
ok(z["имя"], "10.2: 210 бит → (210+4)·3 = 642", (210 + 4) * 3 == 642)

# ---------- NR LDPC ----------
N2 = "istochniki/nr/ts_138212v190400p.pdf"
z = zapis("5G NR LDPC")
Zs = {0: [2, 4, 8, 16, 32, 64, 128, 256], 1: [3, 6, 12, 24, 48, 96, 192, 384], 2: [5, 10, 20, 40, 80, 160, 320], 3: [7, 14, 28, 56, 112, 224],
      4: [9, 18, 36, 72, 144, 288], 5: [11, 22, 44, 88, 176, 352], 6: [13, 26, 52, 104, 208], 7: [15, 30, 60, 120, 240]}
ok(z["имя"], "наборы Z записи = a·2^j ≤ 384 (a = 2,3,5,…,15), 51 размер", all(Zs[i] == [a * 2 ** j for j in range(9) if a * 2 ** j <= 384] for i, a in enumerate([2, 3, 5, 7, 9, 11, 13, 15]))
   and sum(map(len, Zs.values())) == 51 and str(Zs[1]) in z["параметры"]["Z"])
tok = tokens(N2, 21)
ok(z["имя"], "табл. 5.3.2-1: каждый набор Z стоит подряд в тексте стр. 21", all(podryad(tok, [i] + v) or podryad(tok, v) for i, v in Zs.items()))
t = tx(N2, 33)
ok(z["имя"], "табл. 5.4.2.1-2: коэффициенты k0 17/33/56 (из 66) и 13/25/43 (из 50) есть в тексте", all(re.search(rf"\b{v}\b", t) for v in ("66", "17", "33", "56", "50", "13", "25", "43")))

# ---------- D-STAR, DECT ----------
z = zapis("D-STAR синхросигналы")
str_ok(z, f("JARL_STD6_0_E.pdf"), 25, "кадровая синхро", '"Frame synchronization" shall be 15 bits (111011001010000)')
ok(z["имя"], "0xAAB468 = 1010101010 1101000 1101000", format(0xAAB468, "024b") == "101010101011010001101000")
z = zapis("DECT B-CRC-32")
D3 = f("en_30017503v020901p.pdf")
str_ok(z, D3, 107, "6.2.5.5", "a single 32 bit CRC over the whole B-field", "the polynomial is built as")

# ---------- записи, добавленные третьим проходом (повторно, независимо от skripty/dop3_proverka.py) ----------
z = zapis("M17 (открытый стандарт")
ok(z["имя"], "CRC-16 M17 (0x5935, нач. 0xFFFF, без отражения): '123456789' → 0x772B, 'A' → 0x206E (crc_bytes из pv)",
   crc_bytes(b"123456789", 0x5935, 16, init=0xFFFF) == 0x772B and crc_bytes(b"A", 0x5935, 16, init=0xFFFF) == 0x206E and "0x5935" in z["параметры"]["CRC"])
Tm = json.load(open(os.path.join(KOREN, "tablicy", "m17_spec.json")))["данные"]
ok(z["имя"], "таблица QPP = (45x + 92x²) mod 368, рандомизатор 46 байт, P1 46/61", Tm["перемежитель_QPP_368"] == [(45 * x + 92 * x * x) % 368 for x in range(368)]
   and len(Tm["рандомизатор_46_байт"]) == 46 and sum(Tm["P1"]) == 46 and len(Tm["P1"]) == 61)
for imya_ in ("Речевые кадры AMBE+2", "Речевые кадры D-STAR AMBE"):
    z = zapis(imya_)
    Ta = json.load(open(os.path.join(KOREN, "tablicy", "ambe_fec_peremezhenie.json")))["данные"]
    def lcg(u):
        pr, r = 16 * u, 0
        for _ in range(24): pr = (173 * pr + 13849) % 65536; r = (r << 1) | (pr >> 15)
        return r
    ok(z["имя"], "маски C1 (4096) = LCG pr(n) = (173·pr(n−1) + 13849) mod 65536, pr(0) = 16·u0", all(Ta["PRNG_C1_maska_24bit"][u] == lcg(u) for u in range(4096)))
    ok(z["имя"], "перемежение — перестановка 72 позиций", sorted(Ta["DMR_NXDN_YSF_AMBE2_72"]["a_C0_golay24_pozicii"] + Ta["DMR_NXDN_YSF_AMBE2_72"]["b_C1_golay23_pozicii"]
       + Ta["DMR_NXDN_YSF_AMBE2_72"]["c_C2C3_25bit_pozicii"]) == list(range(72)) and sorted(Ta["DSTAR_AMBE_72"]["efir_pozicii_a_b_c_po_24"]) == list(range(72)))
TFp = "istochniki/aviaciya/ets_30032602e02p.pdf"
z = zapis("TFTS (наземная авиационная")
str_ok(z, TFp, 44, "8.6.5.4", "g(X) = 1 + x2 + x4 + x5 + x6 + x10 + x11"); str_ok(z, TFp, 51, "8.7.2.4", "PN(X) = 1 + X + X4 + X6 + X12", "state octal 0115")
ok(z["имя"], "1+X+X4+X6+X12 примитивен; g = 0xC75", primitiven(st(12, 6, 4, 1, 0)) and st(11, 10, 6, 5, 4, 2, 0) == 0xC75)
z = zapis("CDPD (Cellular Digital")
ok(z["имя"], "РС (63,47) над GF(64): 47·6 = 282, 63·6 = 378, t = 8 — в записи", "(63,47)" in z["имя"] and "378" in z["имя"] and "282" in z["имя"])
