"""Raptor R10 (IETF RFC 5053 = 3GPP TS 26.346 MBMS = DVB-H IPDC TS 102 472 / MPE-IFEC) и RaptorQ (RFC 6330, ATSC 3.0 AL-FEC, 3GPP).
Проверка R10: таблицы V0, V1, Deg и J(K) разобраны из текста RFC; для K = 4…160 и выборки больших K построена матрица A
(LDPC + Half + LT) и проверен полный ранг над GF(2) — именно для этого свойства J(K) и подобраны (5.7). Контроль чувствительности:
при J(K)+1 ранг хотя бы для части K неполный."""
import re, sys, os, math
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

RF = "istochniki/raptor/rfc5053.txt"; ZAPISI = []
tekst = open(os.path.join(KOREN, RF)).read()
chist = re.sub(r"\n\n\n\nLuby, et al\.\s+Standards Track\s+\[Page \d+\]\n\x0c?\nRFC 5053\s+Raptor FEC Scheme\s+October 2007\n", "\n", tekst)
chist = re.sub(r"Luby, et al\.\s+Standards Track\s+\[Page \d+\]|RFC 5053\s+Raptor FEC Scheme\s+October 2007", " ", tekst)
def blok(ot, do):
    a = chist.rindex(ot); b = chist.index(do, a)
    return [int(v) for v in re.findall(r"\d+", chist[a + len(ot):b])]
V0 = blok("5.6.1.  The Table V0", "5.6.2.  The Table V1"); V1 = blok("5.6.2.  The Table V1", "5.7.  Systematic Indices J(K)")
JK = blok("between 4 and 8192 inclusive.", "6.  Security Considerations")
assert len(V0) == 256 and len(V1) == 256 and all(v < 2 ** 32 for v in V0 + V1), (len(V0), len(V1))
assert len(JK) == 8192 - 4 + 1, len(JK)
DEG = [(0, None), (10241, 1), (491582, 2), (712794, 3), (831695, 4), (948446, 10), (1032189, 11), (1048576, 40)]
assert all(re.search(rf"\|\s*{j}\s*\|\s*{f}\s*\|\s*{d if d else '--'}\s*\|", tekst) for j, (f, d) in enumerate(DEG))
def stroka(pat):
    for i, l in enumerate(tekst.split("\n")):
        if re.search(pat, l): return i + 1
    raise AssertionError(pat)
def ist(pat, chto): n = stroka(pat); return {"файл": RF, "строки": str(n), "url": url(RF), "что": chto}
def prost(n):
    return n > 1 and all(n % p for p in range(2, int(n ** .5) + 1))
def parametry(K):
    X = 1
    while X * (X - 1) < 2 * K: X += 1
    S = math.ceil(0.01 * K) + X
    while not prost(S): S += 1
    H = 1
    while math.comb(H, math.ceil(H / 2)) < K + S: H += 1
    return X, S, H
def Rand(X, i, m): return (V0[(X + i) % 256] ^ V1[(X // 256 + i) % 256]) % m
def Deg(v):
    for j in range(1, 8):
        if DEG[j - 1][0] <= v < DEG[j][0]: return DEG[j][1]
def matrica(K, J):
    X, S, H = parametry(K); L = K + S + H; Lp = L
    while not prost(Lp): Lp += 1
    rows = []
    for s in range(S): rows.append(0)
    for i in range(K):
        a = 1 + ((i // S) % (S - 1)); b = i % S
        for _ in range(3):
            rows[b] |= 1 << i; b = (b + a) % S
    for s in range(S): rows[s] |= 1 << (K + s)
    Hp = math.ceil(H / 2); m = []; g = 0
    while len(m) < K + S:
        g += 1; gg = g ^ (g >> 1)
        if bin(gg).count("1") == Hp: m.append(gg)
    for h in range(H):
        r = 1 << (K + S + h)
        for j in range(K + S):
            if (m[j] >> h) & 1: r |= 1 << j
        rows.append(r)
    Q = 65521; A = (53591 + J * 997) % Q; B = 10267 * (J + 1) % Q
    for x in range(K):
        Y = (B + x * A) % Q; d = Deg(Rand(Y, 0, 2 ** 20)); a = 1 + Rand(Y, 1, Lp - 1); b = Rand(Y, 2, Lp)
        while b >= L: b = (b + a) % Lp
        r = 1 << b
        for _ in range(1, min(d - 1, L - 1) + 1):
            b = (b + a) % Lp
            while b >= L: b = (b + a) % Lp
            r ^= 1 << b
        rows.append(r)
    return rows, L
PROVERKA = list(range(4, 161)) + [200, 256, 333, 500, 777, 1024, 2048, 4000, 6000, 8192]
for K in PROVERKA:
    rows, L = matrica(K, JK[K - 4])
    assert rang_gf2(rows) == L, K
plohie = [K for K in range(4, 161) if rang_gf2(matrica(K, JK[K - 4] + 1)[0]) < matrica(K, JK[K - 4] + 1)[1]]
assert len(plohie) > 0
js = tablica("raptor_r10", {"V0": V0, "V1": V1, "Deg (f[j], d[j])": DEG, "J(K), K=4…8192": JK},
   "Raptor R10 (RFC 5053): таблицы генератора случайных чисел V0/V1, распределение степеней и систематические индексы J(K)",
   [ist(r"5\.6\.1\.  The Table V0", "V0"), ist(r"5\.6\.2\.  The Table V1", "V1"), ist(r"^5\.7\.  Systematic Indices J\(K\)", "J(K)"), ist(r"Table 1: Defines the degree distribution", "Deg")],
   f"матрица A (L×L) полного ранга для K = 4…160, 200, 256, 333, 500, 777, 1024, 2048, 4000, 6000, 8192 (всего {len(PROVERKA)}); при J(K)+1 ранг неполный для {len(plohie)} из 157 малых K")
ZAPISI.append(Z("Raptor R10 (RFC 5053 / 3GPP MBMS / DVB-H IPDC): систематический фонтанный код — предкод LDPC (S) + Half (H, коды Грея) + LT с распределением степеней (1…40), K = 4…8192", "Raptor (AL-FEC вещания: DVB-H IPDC, MBMS, DVB-IPTV)", "фонтанный (LT + предкод LDPC/Half)",
  {"S, H": "X: X(X−1) ≥ 2K; S — наименьшее простое ≥ ⌈0,01K⌉ + X; H: C(H, ⌈H/2⌉) ≥ K + S; L = K + S + H", "LDPC": "a = 1 + (⌊i/S⌋ mod (S−1)), b = i mod S, три единицы в столбце",
   "Half": "строка h — бит h последовательности кодов Грея веса ⌈H/2⌉", "Rand": "(V0[(X+i) mod 256] ⊕ V1[(⌊X/256⌋+i) mod 256]) mod m",
   "Trip": "A = (53591 + 997·J(K)) mod 65521, B = 10267·(J(K)+1) mod 65521, Y = (B + X·A) mod 65521; d = Deg(Rand(Y,0,2^20)), a = 1+Rand(Y,1,L'−1), b = Rand(Y,2,L')",
   "таблица": js},
  "DVB-H IPDC (TS 102 472), MPE-IFEC, 3GPP MBMS (TS 26.346), DVB-IPTV AL-FEC", [ist(r"^5\.4\.2\.3\.", "предкод"), ist(r"^5\.4\.4\.1\.  Random Generator", "Rand"), ist(r"^5\.4\.4\.4\.  Triple Generator", "Trip"), ist(r"5\.6\.1\.  The Table V0", "V0"), ist(r"^5\.7\.  Systematic Indices", "J(K)")],
  f"полный ранг A для {len(PROVERKA)} значений K подтверждает V0, V1, Deg и J(K) одновременно (свойство из 5.7); контроль: при J(K)+1 — неполный ранг для {len(plohie)} K",
  "нет (прикладной FEC над IP-пакетами, не битовый поток)"))

R6 = "istochniki/raptor/rfc6330.txt"; t6 = open(os.path.join(KOREN, R6)).read()
def ist6(pat, chto):
    for i, l in enumerate(t6.split("\n")):
        if re.search(pat, l): return {"файл": R6, "строки": str(i + 1), "url": url(R6), "что": chto}
    raise AssertionError(pat)
tab2 = [tuple(int(v) for v in m.groups()) for m in re.finditer(r"\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", t6)]
assert len(tab2) == 477 and tab2[0][0] == 10 and tab2[-1][0] == 56403, (len(tab2), tab2[:2], tab2[-1:])
assert all(prost(S) for _, _, S, _, _ in tab2) and all(prost(W) for *_, W in tab2)
V6 = []
for q in range(4):
    a = t6.rindex(f"5.5.{q + 1}.  The Table V{q}")
    b = t6.rindex(f"5.5.{q + 2}.  The Table V{q + 1}") if q < 3 else t6.rindex("5.6.  Systematic Indices and Other Parameters")
    kus = re.sub(r"Luby, et al\.\s+Standards Track\s+\[Page \d+\]|RFC 6330\s+RaptorQ FEC Scheme\s+August 2011", " ", t6[a:b])
    kus = kus.split("\n", 1)[1]
    V6.append([int(v) for v in re.findall(r"\b\d+\b", kus)])
assert [len(v) for v in V6] == [256] * 4, [len(v) for v in V6]
js6 = tablica("raptorq_rfc6330", {"K'_J_S_H_W": tab2, "V0": V6[0], "V1": V6[1], "V2": V6[2], "V3": V6[3]},
   "RaptorQ (RFC 6330): табл. 2 (K', J(K'), S, H, W) и таблицы V0…V3", [ist6(r"Table 2: Systematic Indices and Other Parameters", "табл. 2"), ist6(r"5\.5\.1\.  The Table V0", "V0")],
   "477 строк табл. 2 (K' = 10…56 403); все S и W — простые (как требует 5.3.3.3); V0…V3 — по 256 значений; полной сверки кодированием не проводилось")
ZAPISI.append(Z("RaptorQ (RFC 6330): систематический фонтанный код над GF(2)/GF(256) — LDPC (S) + HDPC над GF(256) (H) + LT с инактивацией (W), K' = 10…56 403", "Raptor (AL-FEC вещания: ATSC 3.0, 3GPP MBMS/eMBMS)", "фонтанный (LT + предкод LDPC/HDPC)",
  {"параметры": "табл. 2: K', J(K'), S, H, W (477 строк)", "генератор": "Rand с таблицами V0…V3", "таблица": js6},
  "ATSC 3.0 (AL-FEC, A/331), 3GPP MBMS Rel-10+, DVB-I", [ist6(r"Table 2: Systematic Indices and Other Parameters", "табл. 2"), ist6(r"5\.5\.1\.  The Table V0", "V0…V3")],
  "проверка согласованности таблиц (число строк, простота S и W, размеры V0…V3); кодирование с проверкой ранга не выполнялось", "нет"))
