"""Сети по проводам в доме и ЛЭП: HomePlug AV 1.1 / AV2 2.1 (ETSI docbox, публичные), ITU-T G.9960/G.9963/G.9991 (G.hn),
G.9902 (G.hnem), G.9903 (G3-PLC), G.9904 (PRIME), G.9954 (HomePNA 3).
Проверки: матрицы кольцевого завершения HomePlug (прочитанный кодер → (I + A^N)^−1 = M текста), перемежитель турбокода —
перестановка для всех длин, ПСП PRIME — m-последовательность (Берлекэмп–Мэсси), многочлены 171/133 и RS-корни."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "Домашние сети и PLC (HomePlug, G.hn, G3-PLC, PRIME, HomePNA)"


def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": SEM, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


def bm(s):
    """Берлекэмп–Мэсси над GF(2): (L, многочлен связи как список коэффициентов c0..cL)."""
    C = [1]; B = [1]; L = 0; m = 1
    for n in range(len(s)):
        d = s[n]
        for i in range(1, L + 1): d ^= C[i] & s[n - i]
        if d == 0: m += 1; continue
        T = C[:]; C = C + [0] * (len(B) + m - len(C))
        for i, b in enumerate(B): C[i + m] ^= b
        if 2 * L <= n: L = n + 1 - L; B = T; m = 1
        else: m += 1
    return L, C[:L + 1]


H1 = Tekst("istochniki/plc/homeplug_av11_specification_final_public.pdf")
H2 = Tekst("istochniki/plc/homeplug_av21_specification_final_public.pdf")

# ---- HomePlug AV: турбокод ----
def shag(S, u1, u2):
    s1, s2, s3 = S; x0 = s3 ^ u1 ^ u2
    return (x0 ^ u1 ^ u2, s1 ^ u1 ^ u2, s2 ^ u2 ^ x0), x0
def obr_gf2(M):
    n = len(M); A = [row[:] + [1 if i == j else 0 for j in range(n)] for i, row in enumerate(M)]
    for c in range(n):
        p = next(r for r in range(c, n) if A[r][c]); A[c], A[p] = A[p], A[c]
        for r in range(n):
            if r != c and A[r][c]: A[r] = [a ^ b for a, b in zip(A[r], A[c])]
    return [row[n:] for row in A]
def M_kolco(N):
    T = []
    for e in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
        S = e
        for _ in range(N): S, _ = shag(S, 0, 0)
        T.append(list(S))
    return obr_gf2([[T[i][j] ^ (1 if i == j else 0) for j in range(3)] for i in range(3)])
M_teksta = {"PB520/PB16": [[0, 0, 1], [1, 0, 1], [1, 1, 1]], "PB136": [[0, 1, 1], [1, 0, 0], [0, 1, 0]]}     # снимок risunki/hpav_M.png
assert M_kolco(2080) == M_kolco(64) == M_teksta["PB520/PB16"] and M_kolco(544) == M_teksta["PB136"]
sM = H1.gde("where the state S is a 1x3 row vector with components as in the diagram")
# сиды перемежителя
seeds = {}
for tab, octets in (("Table 3-8: Interleaver Seed Table for FEC Block Size of 16 Octets", 16),
                    ("Table 3-9: Interleaver Seed Table for FEC Block Size of 136 Octets", 136),
                    ("Table 3-10: Interleaver Seed Table for FEC Block Size of 520 Octets", 520)):
    k, s = H1.kusok(re.escape(tab), r"Table 3-\d+:|3\.4\.3", posl=True)
    chis = []
    for blok in re.split(r"(?m)^x\s*$", k)[1:]:
        m = re.split(r"S\(x\)", blok)
        if len(m) < 2: continue
        xs = [int(v) for v in re.findall(r"\d+", m[0])]; vs = [int(v) for v in re.findall(r"\d+", m[1])][:len(xs)]
        chis += list(zip(xs, vs))
    seeds[octets] = (s, dict(chis))
PAR = {16: (8, 64), 136: (34, 544), 520: (40, 2080)}       # табл. 3-7: N, L
sP = H1.gde("I(x) = [S(x mod N) – (x div N)*N + L] mod L for x = 0, 1, …, (L-1)")
for octets, (N, L) in PAR.items():
    S = seeds[octets][1]
    assert sorted(S) == list(range(N)), (octets, len(S))
    I = [(S[x % N] - (x // N) * N + L) % L for x in range(L)]
    assert sorted(I) == list(range(L)), octets                  # отображение — перестановка
tablica("homeplug_av_turbo", {"сиды S(x)": {str(o): [seeds[o][1][i] for i in range(PAR[o][0])] for o in PAR}, "N, L": {str(o): PAR[o] for o in PAR},
                              "матрицы кольцевого завершения M (S'0 = S_N·M)": M_teksta,
                              "выкалывание p/q": {"1/2": ["1111111111111111"] * 2, "16/21": ["1001001001001000"] * 2, "16/18 (AV2)": ["10000000 10000000"] * 2}},
        "Турбокод HomePlug AV (дуобинарный, 8 состояний): сиды перемежителя (табл. 3-8…3-10), матрицы завершения, шаблоны выкалывания",
        [H1.ist(seeds[16][0], "Table 3-8"), H1.ist(seeds[136][0], "Table 3-9"), H1.ist(seeds[520][0], "Table 3-10"), H1.ist(sM, "M")],
        "сиды разобраны из текста; I(x) = [S(x mod N) − (x div N)·N + L] mod L — перестановка 0…L−1 для всех трёх длин; "
        "матрицы M вычислены из кодера рис. 3-11 ((I + A^N)^−1) и совпали с текстом для N = 64, 544, 2080")
sT = H1.gde("Two rate 2/3 Recursive Systematic Convolutional (RSC) constituent codes and one")
sS = H1.gde("The bits in the scrambler shall be initialized to all ones at the start of")
rec("HomePlug AV / AV2 / IEEE 1901 FFT-PHY / Green PHY: дуобинарный турбокод (2 × RSC 2/3, 8 состояний, кольцевое завершение)", "турбо дуобинарный",
    {"RSC (рис. 3-11)": "x0 = s3⊕u1⊕u2; s1' = x0⊕u1⊕u2; s2' = s1⊕u1⊕u2; s3' = s2⊕u2⊕x0 (u1 — первый бит пары)",
     "завершение": "кольцевое (tail-biting): 2 прохода, S'0 = S_N·M, M — tablicy/homeplug_av_turbo.json",
     "перемежитель": "дибитовый: I(x) = [S(x mod N) − (x div N)·N + L] mod L; при чётном x пара переставляется; PB16/136/520: N = 8/34/40, L = 64/544/2080; AV2 — ещё PB32",
     "выкалывание": "1/2 (без), 16/21: p,q = 1001001001001000; AV2: 16/18: p,q = 10000000 10000000",
     "скремблер": "S(x) = x^10 + x^3 + 1, все единицы в начале MPDU", "далее": "канальный перемежитель, ROBO (повторение), FC — PB16 с отдельным перемежителем"},
    "HomePlug AV/AV2, IEEE 1901 (FFT-OFDM), HomePlug Green PHY (ROBO)",
    [H1.ist(sT, "3.4.2"), H1.ist(sM, "3.4.2.2 завершение"), H1.ist(sP, "3.4.2.4 перемежитель"), H1.ist(sS, "3.4.1 скремблер"),
     H2.ist(H2.gde("Table 3-8: Rate 16/18 Puncture Pattern"), "AV2 16/18")],
    "кодер прочитан по рисунку (risunki/hpav_rsc.png) и подтверждён: матрицы кольцевого завершения, вычисленные из него для N = 64, 544, 2080, совпали с текстом; перемежитель — перестановка",
    "частично: PCCC-турбо в проекте есть (turbo.py), дуобинарный с кольцевым завершением и этим перемежителем — нет")

# ---- G.hn ----
G60 = Tekst("istochniki/itu/T-REC-G.9960-202306-I.pdf")
G60a = Tekst("istochniki/itu/T-REC-G.9960-200910-T.pdf")
rec("G.hn (G.9960/G.9963/G.9991): QC-LDPC-BC (материнские 1/2, 2/3, 5/6; выкалывание до 16/18, 20/21) + повторение заголовка", "LDPC (QC)",
    {"коды": "11 кодов в проекте (табл. 7-18/7-19 G.9960 2009)", "G.9963": "MIMO — тот же FEC", "G.9991": "VLC (Li-Fi) — FEC G.hn"},
    "G.hn по питанию, коаксу, телефонной паре, POF; Li-Fi", [G60.ist(G60.gde("QC-LDPC-BC"), "G.9960 (2023)"), G60a.ist(G60a.gde("LDPC"), "G.9960 (2009)")],
    "матрицы проекта взяты из G.9960 (10/2009) (docs/20-potok.md)", "ЕСТЬ в проекте: ldpc_std ghn-* (11 кодов)")

# ---- G3-PLC ----
G3 = Tekst("istochniki/itu/T-REC-G.9903-201708-I.pdf")
s3 = G3.gde("The tap connections are defined as x = 0b1111001 and y = 0b1011011")
assert int("1111001", 2) == 0o171 and int("1011011", 2) == 0o133
s3r = G3.gde("Normal mode: RS(N = 255, K = 239, T = 8)")
s3s = G3.gde("The bits in the scrambler are initialized to all-ones at the start of processing the FCH")
rec("G3-PLC (G.9903 / IEEE 1901.2): RS(255,239)/(255,247) + свёрточный 1/2 K=7 (171,133) + повторение ×4/×6 + частотно-временное перемежение", "каскадный: РС + свёрточный",
    {"RS": "укороченный, GF(256) x^8+x^4+x^3+x^2+1 (435 восьм.), g(x) = ∏_{i=1}^{2T}(x − α^i); T = 8 (обычный) или 4 (робастный); 1 или 2 блока на кадр",
     "свёрточный": "K = 7, x = 1111001 (171), y = 1011011 (133), 6 нулевых хвостовых", "повторение": "×4 (робастный), ×6 (суперробастный)",
     "скремблер": "S(x) = x^7 + x^4 + 1, все единицы в начале FCH и полезной нагрузки", "модуляция": "OFDM DBPSK/DQPSK/D8PSK или когерентная (7.16)"},
    "G3-PLC (умный учёт, CENELEC/FCC/ARIB)", [G3.ist(s3r, "7.9.1"), G3.ist(s3, "7.9.2"), G3.ist(s3s, "7.8")],
    "двоичные отводы 1111001/1011011 = 171/133 восьм. (проверено); корни RS α^1…α^2T по тексту 7.9.1", "частично: свёрточные коды 171/133 и RS есть; повторение/перемежение G3 — нет")

# ---- PRIME ----
G4 = Tekst("istochniki/itu/T-REC-G.9904-201210-I.pdf")
k, s4 = G4.kusok(r"obtained by a cyclic extension of the 127 element sequence given by:", r"The PRBS sequence can be generated")
pn = [int(x) for x in re.findall(r"[01]", k[k.index("{"):k.index("}")])]
assert len(pn) == 127
L, C = bm(pn + pn)
pmn = sum(c << (L - i) for i, c in enumerate(C))              # многочлен связи в обычной записи
assert L == 7 and primitivnyj(pmn)
s4c = G4.gde("rate ½ convolutional encoder with constraint length K = 7 and code generator")
rec("PRIME (G.9904): свёрточный 1/2 K=7 (1111001, 1011011) + перемежение + скремблер ПСП-127", "свёрточный",
    {"свёрточный": "K = 7, многочлены 1111001 и 1011011 (171/133 восьм.), сброс в 0; хвост 8 нулей (заголовок) / 6 (нагрузка); может отключаться",
     "скремблер": f"циклическое продолжение 127-элементной ПСП (все единицы в начале); ПСП — m-последовательность, многочлен связи {zapis_x(pmn)} (взаимный {zapis_x(otrazit(pmn, 8))}; Берлекэмп–Мэсси)",
     "CRC": "CRC-8-ATM заголовка", "модуляция": "OFDM DBPSK/DQPSK/D8PSK"},
    "PRIME (умный учёт)", [G4.ist(s4c, "7.5"), G4.ist(s4, "7.6 скремблер")],
    f"127 элементов ПСП разобраны из текста; линейная сложность 7, многочлен {zapis_x(pmn)} примитивен → m-последовательность", "частично: 171/133 есть; ПСП-скремблер — аддитивный, находится слепо")

# ---- G.hnem ----
G2 = Tekst("istochniki/itu/T-REC-G.9902-201210-I.pdf")
s2 = G2.gde("The outer code shall use a standard byte-oriented Reed-Solomon code")
s2c = G2.gde("G1=11110012 = 1718 and G2=10110112 = 1338")
rec("G.hnem (G.9902): внешний RS (корни α^1…α^R) + внутренний свёрточный 1/2 или 2/3 (K=7, 171/133, выкалывание [1 1; 0 1])", "каскадный: РС + свёрточный",
    {"RS": "GF(256) x^8+x^4+x^3+x^2+1, G(D) = ∏_{i=1}^{R}(D ⊕ α^i) (снимок risunki/g9902_rs.png)", "свёрточный": "K = 7, G1 = 171, G2 = 133; 2/3 — каждый второй X выкалывается (X0Y0Y1 X2Y2Y3…)",
     "HCS": "CRC-12 заголовка", "повторение": "REP, перемежение INTM"}, "узкополосные PLC для умных сетей (G.hnem)",
    [G2.ist(s2, "8.x RS"), G2.ist(s2c, "свёрточный")], "многочлены совпадают с G3-PLC; корни RS — по снимку формулы", "частично: как G3-PLC")

# ---- HomePNA ----
G54 = Tekst("istochniki/itu/T-REC-G.9954-200701-I.pdf")
s54 = G54.gde("The primitive polynomial and generator polynomials are identical to those used for ITU-T Rec. G.992.1")
rec("HomePNA 3 (G.9954): необязательный RS над GF(256) как G.992.1, встроенный в пакет (TLV), с внутрикадровым перемежением байт", "РС",
    {"код": "G(X) = ∏_{i=0}^{R−1}(X + α^i), GF(256) x^8+x^4+x^3+x^2+1", "раскладка": "проверочные байты — в заголовке-расширении TLV перед неизменной нагрузкой; несколько слов на пакет"},
    "HomePNA 3.x по телефонной проводке/коаксу", [G54.ist(s54, "11.11.2–11.11.3")], "код = DMT-DSL RS (сверено текстом)", "частично: RS GF(256) есть")

if __name__ == "__main__":
    print(len(ZAPISI), "записей", zapis_x(pmn))
