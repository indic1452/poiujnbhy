"""WiMAX IEEE 802.16-2004 / 802.16e-2005: рандомизатор, RS-CC (OFDM), CC с циклическим хвостом (OFDMA), перемежитель,
CTC (дуобинарный турбокод OFDM и OFDMA), LDPC 802.16e (6 базовых матриц × 19 длин), HCS/CRC, коды ранжирования.
Проверка — тестовые векторы 8.3.3.5 стандарта (рандомизация → RS → CC → перемежение), вычисление таблицы циклических
состояний CTC, сверка LDPC и CTC с CML (Iterative Solutions, LGPL 2.1) и с data/ldpc_wimax.json проекта.
BTC (ТКБ 802.16) — в модемной области (kody2), здесь не повторяется."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

T4 = Tekst("istochniki/wimax/802.16-2004_usf.pdf"); T5 = Tekst("istochniki/wimax/80216e-2005_berkeley.pdf"); ZAPISI = []
cml_ldpc = kod("CML", "mat/InitializeWiMaxLDPC.m"); cml_ctc = kod("CML", "mat/CreateWimaxInterleaver.m")
SL = "802.16-2004 — 802.16 «WiMAX»"

def hx(s): return bytes(int(x, 16) for x in s.split())
def bity(b): return [(x >> (7 - i)) & 1 for x in b for i in range(8)]

# ---------- тестовые векторы 8.3.3.5 ----------
kus, s_pr = T4.kusok(r"8\.3\.3\.5\.1 Full bandwidth \(16 subchannels\)", r"Subcarrier Mapping")
def pole(kus, imya):
    m = re.search(re.escape(imya) + r"\s*\(Hex\)\s*\n((?:[ \t]*(?:[0-9A-F]{2}[ \t]*)+\n)+)", kus + "\n")
    return hx(m.group(1))
vh, rnd, rsd, ccd, ild = (pole(kus, n) for n in ("Input Data", "Randomized Data", "Reed–Solomon encoded Data", "Convolutionally Encoded Data", "Interleaved Data"))
assert len(vh) == 35 and len(rsd) == 40 and len(ccd) == 48 and len(ild) == 48
kus2, s_pr2 = T4.kusok(r"8\.3\.3\.5\.2 Subchannelization \(2 subchannels\)", r"Subcarrier Mapping")
vh2, rnd2, cc2, il2 = (pole(kus2, n) for n in ("Input Data", "Randomized Data", "Convolutionally Encoded Data", "Interleaved Data"))
kus3, s_pr3 = T4.kusok(r"8\.3\.3\.5\.3 Subchannelization \(1 subchannel\)", r"Subcarrier Mapping")
vh3, rnd3, cc3, il3 = (pole(kus3, n) for n in ("Input Data", "Randomized Data", "Convolutionally Encoded Data", "Interleaved Data"))

# рандомизатор: гамма = вход ⊕ выход удовлетворяет 1 + X^14 + X^15
gam = [a ^ b for a, b in zip(bity(vh), bity(rnd))]
assert all(gam[n] == gam[n - 14] ^ gam[n - 15] for n in range(15, len(gam)))
assert rnd2[:26] == rnd[:26] and rnd3[:10] == rnd[:10]      # одна и та же гамма (UIUC 7, BSID 1, кадр 1)
s_r = T4.odna(r"characteristic polynomial 1 \+ X14 \+ X15\. The LFSR shall be preset")[0]
s_rdl = T5.odna(r"zone only in the case a FCH-STC is present, with the sequence: 1 0 0 1 0 1 0 1 0 0 0 0 0 0 0")[0]
s_ra = T5.odna(r"Figure 254a—Creation of the OFDMA randomizer initialization vector for HARQ")[0]
s_ro = T4.odna(r"^8\.4\.9\.1 Randomization")[0]
ZAPISI.append(Z("WiMAX 802.16 рандомизатор данных: ГПСП 1 + X^14 + X^15, сброс на пакет (burst) / блок FEC", "WiMAX (IEEE 802.16-2004/16e)", "скремблер (рандомизатор)",
  {"многочлен": "1 + X^14 + X^15", "OFDM DL": "в начале кадра и зоны STC: 1 0 0 1 0 1 0 1 0 0 0 0 0 0 0; последующие пакеты — вектор из UIUC/DIUC, BSID, номера кадра (рис. 198/199)",
   "OFDMA": "на каждый блок FEC — вектор из смещения подканала (5 мл. бит) и смещения символа OFDMA (10 мл. бит) (рис. 254); HARQ — рис. 254a (16e)",
   "порядок": "байт входит старшим битом вперёд; заполнение 0xFF; хвостовой байт 0x00 RS-CC не рандомизируется; преамбулы не рандомизируются"},
  "WiMAX фиксированный (802.16d OFDM-256) и мобильный (802.16e OFDMA), WiBro", [T4.ist(s_r, "8.3.3.1"), T5.ist(s_rdl, "начальное DL (16e)"), T4.ist(s_ro, "8.4.9.1 OFDMA"), T5.ist(s_ra, "HARQ 16e"), T4.ist(s_pr, "пример 8.3.3.5.1")],
  "гамма = (вход ⊕ рандомизированные данные) примера 8.3.3.5.1 (35 байт) удовлетворяет рекуррентности s[n] = s[n−14] ⊕ s[n−15]; три примера дают одну и ту же гамму",
  "есть: dvb.py / skrembler.py (та же ГПСП 1+x^14+x^15; сброс на пакет — через «ПСП блока»)"))

# RS(255,239) укороченный/выколотый: чётность 2T' первых из 16, передаётся ПЕРЕД данными
exp = [0] * 512; log = [0] * 256; x = 1
for i in range(255):
    exp[i] = x; log[x] = i; x <<= 1
    if x & 0x100: x ^= 0x11D
for i in range(255, 512): exp[i] = exp[i - 255]
def umn(a, b): return 0 if a == 0 or b == 0 else exp[log[a] + log[b]]
g = [1]
for i in range(16):
    ng = [0] * (len(g) + 1)
    for j, c in enumerate(g): ng[j] ^= c; ng[j + 1] ^= umn(c, exp[i])
    g = ng
def rs_ch(d):
    r = [0] * 16
    for b in d:
        f = b ^ r[0]; r = r[1:] + [0]
        for j in range(16): r[j] ^= umn(f, g[j + 1])
    return r
dannye = list(rnd) + [0]                      # 35 байт + хвостовой 0x00 → K' = 36
assert bytes(dannye) == rsd[4:] and bytes(rs_ch(bytes(239 - 36) + bytes(dannye))[:4]) == rsd[:4]
s_rs = T4.odna(r"The Reed–Solomon encoding shall be derived from a systematic RS \(N = 255, K = 239, T =")[0]
s_rp = T4.odna(r"When a codeword is punctured to permit T' bytes to be corrected, only the first 2T' of the")[0]

# свёрточный 171/133, K=7, регистр: новый бит — старший разряд; X = 171, Y = 133
def svk(b):
    reg = 0; out = []
    for v in b:
        reg = (reg >> 1) | (v << 6)
        out.append((bin(reg & 0o171).count("1") & 1, bin(reg & 0o133).count("1") & 1))
    return out
kus_t, s_t = T4.kusok(r"Table 214—The inner convolutional code with puncturing configuration\s*\n", r"Figure 200")
m = re.search(r"dfree\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+X\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+Y\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+XY\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", kus_t)
VYK = {r: {"dfree": int(m.group(1 + i)), "X": m.group(5 + i), "Y": m.group(9 + i), "порядок": m.group(13 + i)} for i, r in enumerate(("1/2", "2/3", "3/4", "5/6"))}
assert VYK["3/4"] == {"dfree": 5, "X": "101", "Y": "110", "порядок": "X1Y1Y2X3"} and VYK["5/6"]["порядок"] == "X1Y1Y2X3Y4X5"
assert dfree([0o171, 0o133], 7) == VYK["1/2"]["dfree"] == 10
def vykol(xy, r):
    X, Y, por = VYK[r]["X"], VYK[r]["Y"], re.findall(r"([XY])(\d)", VYK[r]["порядок"]); P = len(X); out = []
    for i in range(0, len(xy), P):
        blk = xy[i:i + P]
        for t, k in por:
            k = int(k) - 1
            if k < len(blk): out.append(blk[k][0 if t == "X" else 1])
    return out
assert vykol(svk(bity(rsd)), "5/6") == bity(ccd)                   # QPSK 3/4 = RS(40,36,2) + CC 5/6
assert vykol(svk(bity(rnd2)), "3/4") == bity(cc2)                  # подканалы: без RS, CC 3/4
assert vykol(svk(bity(rnd3)[:90]), "3/4") == bity(cc3)[:120]
# перемежитель OFDM: d = 12, s = ceil(Ncpc/2)
def perem(b, Ncpc, d):
    N = len(b); s = max(1, -(-Ncpc // 2)); out = [0] * N
    for k in range(N):
        mk = (N // d) * (k % d) + k // d
        out[s * (mk // s) + (mk + N - (d * mk // N)) % s] = b[k]
    return out
assert perem(bity(ccd), 2, 12) == bity(ild)
assert sum((perem(bity(cc2)[i:i + 96], 4, 12) for i in range(0, 288, 96)), []) == bity(il2)
assert sum((perem(bity(cc3)[i:i + 24], 2, 12) for i in range(0, 120, 24)), []) == bity(il3)
json_vyk = tablica("wimax_cc_vykalyvanie", VYK, "WiMAX 802.16: выкалывание свёрточного кода 171/133 (табл. 214 OFDM = табл. 319 OFDMA)",
                   [T4.ist(s_t, "Table 214")], "dfree 1/2 = 10 вычислено; кодирование векторов 8.3.3.5.1–3 с этими шаблонами совпало побитно")
s_tb = T4.odna(r"^Table 319—The inner convolutional code with puncturing configuration$")[0]
s_cc = T4.odna(r"^8\.4\.9\.2\.1 Convolutional coding \(CC\)")[0]
s_il = T4.odna(r"^8\.3\.3\.3 Interleaving")[0]; s_il2 = T4.odna(r"k = 0,1,…,Ncbps – 1 ,   d = 16|d = 16 \(130\)|, d = 16")[0]
ZAPISI.append(Z("WiMAX 802.16 OFDM (256): RS-CC — RS(255,239) T=8 укороченный/выколотый + свёрточный 171/133 K=7 (1/2, 2/3, 3/4, 5/6) с хвостовым байтом 0x00", "WiMAX (IEEE 802.16-2004/16e)", "каскадный (РС + свёрточный)",
  {"РС": "RS(255,239,8) над GF(2^8), p(x) = x^8 + x^4 + x^3 + x^2 + 1, g(x) = ∏(x + λ^i), i = 0…2T−1, λ = 0x02; укорочение — 239−K' нулевых байт впереди; выкалывание — берутся ПЕРВЫЕ 2T' из 16 байт чётности; чётность передаётся ПЕРЕД данными",
   "свёрточный": "K = 7, G1 = 171 (X), G2 = 133 (Y), восьмерично; новый бит — старший разряд регистра; нулевой хвост (байт 0x00 после рандомизации)",
   "выкалывание": VYK, "таблица": json_vyk, "перемежитель": "двухступенчатый блочный на Ncbps: m_k = (Ncbps/12)(k mod 12) + ⌊k/12⌋, j_k = s⌊m_k/s⌋ + (m_k + Ncbps − ⌊12 m_k/Ncbps⌋) mod s, s = ⌈Ncpc/2⌉",
   "пример": "QPSK 3/4 = RS(40,36,2) + CC 5/6; при подканализации RS не применяется"},
  "WiMAX фиксированный OFDM-256 (802.16d), HiperMAN", [T4.ist(s_rs, "8.3.3.2.1 RS"), T4.ist(s_rp, "выкалывание РС"), T4.ist(s_t, "Table 214"), T4.ist(s_il, "8.3.3.3"), T4.ist(s_pr, "8.3.3.5.1"), T4.ist(s_pr2, "8.3.3.5.2"), T4.ist(s_pr3, "8.3.3.5.3")],
  "три примера 8.3.3.5 воспроизведены побитно: RS(40,36) — 4 байта чётности 49 31 40 BF; свёрточное кодирование 5/6 и 3/4; перемежение QPSK и 16-QAM (s = 2)",
  "есть: rs_bch.py (РС любой длины, в т.ч. выколотый), kod.СВЁРТОЧНЫЕ (171,133) и vykalyvanie.py; порядок «чётность перед данными» — учитывать вручную"))
ZAPISI.append(Z("WiMAX 802.16 OFDMA: свёрточный 171/133 K=7 с циклическим хвостом (tail-biting), выкалывание 1/2…5/6, перемежитель d = 16", "WiMAX (IEEE 802.16-2004/16e)", "свёрточный",
  {"код": "как RS-CC, но без РС и без хвостового байта: регистр инициализируется последними 6 битами блока FEC (tail-biting)", "выкалывание": "табл. 319 = табл. 214",
   "перемежитель": "m_k = (Ncbps/16)(k mod 16) + ⌊k/16⌋; j_k = s⌊m_k/s⌋ + (m_k + Ncbps − ⌊16 m_k/Ncbps⌋) mod s, s = Ncpc/2", "склейка слотов": "табл. 317/318 (j по модуляции и скорости)", "повторение": "R = 2, 4, 6 для QPSK (повторение слотов)"},
  "Mobile WiMAX (802.16e OFDMA), WiBro, FCH/DL-MAP", [T4.ist(s_cc, "8.4.9.2.1"), T4.ist(s_tb, "Table 319"), T4.ist(s_il2, "8.4.9.3")],
  "многочлены и выкалывание совпадают с OFDM-вариантом, проверенным векторами 8.3.3.5; перемежитель — формула той же формы, что проверена векторами (с d = 16)",
  "есть: kod.py (свёрточный с циклическим хвостом LTE — тот же приём), vykalyvanie.py"))

# ---------- CTC ----------
kus_326, s_326 = T4.kusok(r"Table 326—Optimal CTC channel coding per modulation\s*\nModulation", r"8\.4\.9\.2\.3\.2 CTC interleaver")
kus_326b, _ = T4.kusok(r"case 0:", r"Table 327—Optimal CTC channel coding per modulation when supporting H-ARQ")
str326 = re.findall(r"(QPSK|16-QAM|64-QAM)\s+(\d+)\s+(\d+)\s+(\d/\d)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", kus_326 + "\n" + kus_326b)
P2004 = {}
for mod, db, eb, r, N, p0, p1, p2, p3 in str326:
    P2004.setdefault(int(N), set()).add((int(p0), int(p1), int(p2), int(p3)))
t_cml = open(os.path.join(KOREN, cml_ctc)).read()
NN = [int(v) for v in re.findall(r"\d+", t_cml.split("NN = [")[1].split("]")[0])]
PP = [tuple(int(v) for v in r) for r in re.findall(r"(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*[;\]]", t_cml.split("PP=[")[1].split("subblk_mJ")[0])]
MJ = [tuple(int(v) for v in r) for r in re.findall(r"(\d+)\s*,\s*(\d+)\s*[;\]]", t_cml.split("subblk_mJ = [")[1].split("Nindex")[0])]
assert len(NN) == len(PP) == len(MJ) == 17
CML = dict(zip(NN, PP))
# 802.16-2004 и CML совпадают для N ≤ 240 (кроме исправлений 16e); 16e исправляет HARQ-строки N = 480…2400
for N, ps in P2004.items():
    if N in CML: assert CML[N] in ps, (N, ps, CML[N])
kus_16e, s_16e = T5.kusok(r"Table 327—Optimal CTC channel coding per modulation when supporting H-ARQ\s*HARQ", r"=====СТР")
ch16 = [int(v) for v in re.findall(r"\d+", kus_16e)]
for N in (480, 960, 1920, 2400):
    assert est_podposl(kus_16e, [N]) and all(p in ch16 for p in CML[N]), N
assert est_podposl(kus_16e, [1440, 17, 43, 720, 360, 540])
# таблица циклических состояний: вычисление (обратная связь 1 + D + D^3) и текст табл. 328
def shag(S):
    s1, s2, s3 = (S >> 2) & 1, (S >> 1) & 1, S & 1
    return ((s1 ^ s3) << 2) | (s1 << 1) | s2
def AN(S, N):
    for _ in range(N): S = shag(S)
    return S
SC = {N: [[Sc for Sc in range(8) if AN(Sc, N) ^ Sc == Sf][0] for Sf in range(8)] for N in range(1, 7)}
kus_sc, s_sc = T4.kusok(r"Table 328—Circulation state lookup table \(Sc\)\s*\nS0N–1", r"Nmod7")
ch_sc = [int(v) for v in re.findall(r"\d+", kus_sc)]
for N in range(1, 7): assert est_podposl(kus_sc, [N] + SC[N]), N
kus_329, s_329 = T4.kusok(r"^Table 329—Parameters for the subblock interleavers\s*\nBlock size", r"Tk", flags=re.M | re.S)
t329 = {int(a) // 2: (int(c), int(d)) for a, b, c, d in re.findall(r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", kus_329)}
for N, (m_, J) in zip(NN, MJ):
    if N in t329 and N != 108: assert t329[N] == (m_, J), (N, t329[N], (m_, J))
assert t329[108] == (6, 3) and dict(zip(NN, MJ))[108] == (5, 4)     # 16e исправил 216 бит: (6,3) → (5,4)
assert est_podposl(T5.kusok(r"Change relevant entries in Table 329 as indicated", r"Tk")[0], [216, 108, 65, 34])
def peremezh_ctc(N):
    p0, p1, p2, p3 = CML[N]; out = []
    for j in range(N):
        P = (0, N // 2 + p1, p2, N // 2 + p3)[j % 4]
        out.append((p0 * j + P + 1) % N)
    return out
for N in NN: assert sorted(peremezh_ctc(N)) == list(range(N)), N
def bro(y, m): return int(format(y, f"0{m}b")[::-1], 2)
def podblok(N, m_, J):
    out = []; k = 0
    while len(out) < N:
        Tk = (2 ** m_) * (k % J) + bro(k // J, m_)
        if Tk < N: out.append(Tk)
        k += 1
    return out
for N, (m_, J) in zip(NN, MJ): assert sorted(podblok(N, m_, J)) == list(range(N))
json_ctc = tablica("wimax_ctc_ofdma", {"N_пар": NN, "P0_P1_P2_P3": {str(N): CML[N] for N in NN}, "подблочный_m_J": {str(N): mj for N, mj in zip(NN, MJ)},
   "перестановка_P(j)": {str(N): peremezh_ctc(N) for N in NN}, "подблочная_перестановка": {str(N): podblok(N, *mj) for N, mj in zip(NN, MJ)},
   "циклические_состояния_Sc[N mod 7][S0N-1]": {str(N): SC[N] for N in SC}},
   "WiMAX OFDMA CTC: параметры перемежителя (16e), таблица циклических состояний, параметры подблочных перемежителей и готовые перестановки",
   [T4.ist(s_326, "Table 326"), T5.ist(s_16e, "Table 327 (16e)"), T4.ist(s_sc, "Table 328"), T4.ist(s_329, "Table 329"), {"файл": cml_ctc, "url": url(cml_ctc)}],
   "P0…P3: 802.16-2004 табл. 326 ∋ значения CML для всех N ≤ 240; HARQ-строки 16e (480…2400) найдены в тексте поправок 16e; Sc вычислены из 1+D+D^3 и совпали с табл. 328; все перестановки — биекции")
s_ctc = T4.odna(r"^8\.4\.9\.2\.3\.1 CTC encoder")[0]
s_pol = T4.odna(r"For the W parity bit: 0x9, equivalently 1 \+ D3")[0]
s_sep = T4.odna(r"^8\.4\.9\.2\.3\.4\.1 Symbol separation")[0]
s_grp = T4.odna(r"^8\.4\.9\.2\.3\.4\.3 Symbol grouping")[0]
s_crc = T5.odna(r"The size of the CRC is 16 bits\. CRC16-CCITT, as defined in ITU-T Recommendation X\.25")[0]
ZAPISI.append(Z("WiMAX 802.16e OFDMA CTC: дуобинарный циклический RSC-турбокод 1/3 (8 состояний), перемежитель P0…P3, подблочное перемежение и выбор символов (HARQ IR)", "WiMAX (IEEE 802.16-2004/16e)", "турбо PCCC дуобинарный (CRSC)",
  {"составной код": "дуобинарный, 8 состояний: обратная связь 0xB = 1 + D + D^3, чётность Y 0xD = 1 + D^2 + D^3, чётность W 0x9 = 1 + D^3; вход — пары (A, B), A — старший бит первого байта",
   "хвост": "циклический (circular): начальные состояния Sc1/Sc2 по табл. 328 от N mod 7 и конечного состояния после прогона из нуля",
   "перемежитель": "шаг 1: в нечётных парах (A,B) → (B,A); шаг 2: u2(j) = u1(P(j)), P(j) = (P0·j + P + 1) mod N, P = 0, N/2+P1, P2, N/2+P3 для j mod 4 = 0,1,2,3",
   "N пар": NN, "подпакеты": "6 подблоков A, B, Y1, Y2, W1, W2; подблочный перемежитель T_k = 2^m (k mod J) + BRO_m(⌊k/J⌋); выход: A, B, затем Y1/Y2 поочерёдно, затем W1/W2 поочерёдно; выбор символов — HARQ IR",
   "таблица": json_ctc, "CRC HARQ": "CRC-16 CCITT (X.25) на пакет кодера HARQ"},
  "Mobile WiMAX (802.16e OFDMA), WiBro; дуобинарная схема та же, что DVB-RCS", [T4.ist(s_ctc, "8.4.9.2.3.1"), T4.ist(s_pol, "многочлены"), T4.ist(s_326, "Table 326"), T5.ist(s_16e, "16e Table 327"), T4.ist(s_sc, "Table 328"), T4.ist(s_sep, "подблоки"), T4.ist(s_grp, "группировка"), T5.ist(s_crc, "CRC HARQ"),
   ist_kod(cml_ctc, r"PP=\[", "CML P0…P3"), ist_kod(cml_ctc, r"subblk_mJ", "CML m, J")],
  "P0…P3 стандарта = CML (Valenti, LGPL); таблица Sc вычислена по 1+D+D^3 и совпала с табл. 328; исправления 16e (N=108: m,J = 5,4; HARQ N=480…2400) подтверждены текстом поправок и CML",
  "частично: turbo.py (PCCC, признаки перемежителя) — дуобинарный CRSC и перемежитель WiMAX не встроены"))
kus_220, s_220 = T4.kusok(r"Table 220—Optional CTC channel coding per modulation\s*\nModulation", r"8\.3\.3\.2\.3\.4 CTC puncturing")
t220 = re.findall(r"(QPSK|16-QAM|64-QAM)\s+(\d+)\s*[×*]\s*Nsub\s+(\d/\d)\s+(\d+)", kus_220)
assert len(t220) == 7, t220
s_221 = T4.odna(r"^Table 221—Circulation state lookup table \(Sc\)")[0]
s_ofdm = T4.odna(r"^8\.3\.3\.2\.3\.1 CTC encoder")[0]
ZAPISI.append(Z("WiMAX 802.16 OFDM (256) CTC: дуобинарный CRSC 8 состояний, только чётность Y, N = 6…27 × Nsub, P0 по табл. 220, P1 = 3N/4", "WiMAX (IEEE 802.16-2004/16e)", "турбо PCCC дуобинарный (CRSC)",
  {"многочлены": "обратная связь 1 + D + D^3, Y: 1 + D^2 + D^3", "N и P0": sorted({(a, int(b), c, int(d)) for a, b, c, d in t220}), "перемежитель": "i = (P0·j + 1) mod N, (+N/4, +N/2+P1, … по j mod 4); P1 = 3N/4",
   "выкалывание": "одинаковое для C1 и C2 (табл. 222)", "порядок выхода": "A0,B0…AN−1,BN−1, Y1,0…Y1,M, Y2,0…Y2,M → перемежитель 8.3.3.3"},
  "WiMAX фиксированный OFDM-256 (необязательный режим)", [T4.ist(s_ofdm, "8.3.3.2.3.1"), T4.ist(s_220, "Table 220"), T4.ist(s_221, "Table 221")],
  "табл. 221 совпадает с табл. 328 (тот же составной кодер — Sc проверены вычислением); P0 — из текста табл. 220", "частично: turbo.py"))

# ---------- LDPC 802.16e ----------
def chisla_m(kus):
    kus = re.sub(r"=====СТР \d+=====.*?All rights reserved\.", " ", kus, flags=re.S)
    return [int(x) for x in re.findall(r"-?\d+", kus)]
t_cl = open(os.path.join(KOREN, cml_ldpc)).read()
def cml_matr(ot):
    b = t_cl[t_cl.index(ot):]; b = b[b.index("[") + 1:b.index("];")]
    return [int(v) for v in re.findall(r"-?\d+", b)]
BLK = [t_cl.split("Hbm")[i] for i in range(len(t_cl.split("Hbm")))]
cml_bloki = re.findall(r"Hbm\s*=\s*\[(.*?)\];", t_cl, re.S)
cml_bloki = [[int(v) for v in re.findall(r"-?\d+", b)] for b in cml_bloki]
PRO = json.load(open("/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_wimax.json"))["матрицы"]
MOT = Tekst("istochniki/wimax/C80216e-05_066r3.pdf")      # вклад Motorola/LG/Samsung, принятый в 16e (без 5/6)
RAZN_CML = {}
MATR = {}; ist_l = []
for (imya, ot, do, m_, cml_i) in [("1/2", r"Rate 1/2:", r"Note that the R=1/2", 12, 0), ("2/3A", r"Rate 2/3 A code:", r"Rate 2/3 B code:", 8, 1), ("2/3B", r"Rate 2/3 B code:", r"Rate 3/4 A code:", 8, 2),
                                  ("3/4A", r"Rate 3/4 A code:", r"Rate 3/4 B code:", 6, 3), ("3/4B", r"Rate 3/4 B code:", r"Rate 5/6 code:", 6, 4), ("5/6", r"Rate 5/6 code:", r"Insert new subclause 8\.4\.9\.2\.5\.2", 4, 5)]:
    k_, s_ = T5.kusok(ot, do); c = chisla_m(k_)[:m_ * 24]
    M = [c[i * 24:(i + 1) * 24] for i in range(m_)]
    razn = [(i // 24, i % 24, a, b) for i, (a, b) in enumerate(zip(c, cml_bloki[cml_i])) if a != b]
    if razn: RAZN_CML[imya] = razn
    if imya != "5/6":
        k_m, s_m = MOT.kusok(ot.replace(":", r":\s*\n"), r"Rate \d/\d|Table|<"); k_m = re.sub(r"=====СТР \d+=====.*?IEEE C802\.16e-05/0066r3\s*\n\s*\d+", " ", k_m, flags=re.S)
        cm = [int(v) for v in re.findall(r"-?\d+", k_m)][:m_ * 24]
        assert cm == c, ("вклад C80216e-05/066r3", imya); ist_l.append(MOT.ist(s_m, f"вклад: {imya}"))
    klyuch = {"1/2": "12", "2/3A": "23A", "2/3B": "23B", "3/4A": "34A", "3/4B": "34B", "5/6": "56"}[imya]
    assert PRO[klyuch] == M, imya                                       # проект совпадает поэлементно
    # структура: дуальная диагональ Hb2, hb нечётного веса
    hb = [r[24 - m_] for r in M]
    assert hb[0] >= 0 and hb[-1] >= 0 and sum(v >= 0 for v in hb) == 3
    for i in range(m_):
        for j in range(m_ - 1):
            v = M[i][24 - m_ + 1 + j]; assert (v == 0) == (i == j or i == j + 1) and (v < 0) == (not (i == j or i == j + 1))
    MATR[imya] = M; ist_l.append(T5.ist(s_, f"базовая {imya}"))
assert MATR["5/6"][3][0] == 50 and PRO["56-v68"][3][0] == 68
assert RAZN_CML == {"3/4B": [(5, 7, 35, 38)]}, RAZN_CML        # единственное расхождение CML: 38 вместо 35
kus_a, s_a = T5.kusok(r"Table 333a—LDPC block sizes and code rates\s*\nn", r"Table 333b—Parameter")
DL = [n for n in range(576, 2305, 96)]
for n in DL: assert est_podposl(kus_a, [n, n // 8, n // 24]), n
def razvernut(M, n, imya):
    z = n // 24; sh = [[(-1 if v < 0 else (v % z if imya == "2/3A" else v * z // 96)) for v in r] for r in M]
    return qc_v_H(sh, z)
for imya, M in MATR.items():
    for n in (576, 1152, 2304):
        H = razvernut(M, n, imya); m_ = len(M) * (n // 24)
        assert rang_gf2(H) == m_, (imya, n)
json_ldpc = tablica("wimax_ldpc_16e", {"базовые_матрицы_z0=96": MATR, "длины_n": DL, "z": [n // 24 for n in DL],
   "правило_сдвига": "p(f,i,j) = p(i,j) при p ≤ 0; иначе ⌊p(i,j)·z_f/96⌋; для 2/3A — p(i,j) mod z_f"},
   "IEEE 802.16e-2005 8.4.9.2.5: модельные матрицы Hbm (24 столбца) для z0 = 96, n = 576…2304 с шагом 96", ist_l + [T5.ist(s_a, "Table 333a")],
   "поэлементно совпали со вкладом C80216e-05/066r3 (кроме 5/6 — его там нет) и data/ldpc_wimax.json проекта; с CML InitializeWiMaxLDPC.m — все, кроме 3/4B строка 5 столбец 7 (CML 38, стандарт/вклад/проект 35); дуально-диагональная структура Hb2 и hb веса 3 проверены; для n = 576, 1152, 2304 ранг H = m (все 6 кодов)")
s_l = T5.odna(r"^8\.4\.9\.2\.5\.1 Code description")[0]
s_lr = T5.odna(r"^8\.4\.9\.2\.5\.2 Code rate and block size adjustment|8\.4\.9\.2\.5\.2 Code rate and block size adjustment")[0]
ZAPISI.append(Z("WiMAX 802.16e LDPC: 6 квазициклических кодов (1/2, 2/3A, 2/3B, 3/4A, 3/4B, 5/6) × 19 длин n = 576…2304 (z = 24…96) — 114 кодов", "WiMAX (IEEE 802.16-2004/16e)", "LDPC",
  {"структура": "H = [Hb1 | hb | H'b2], H'b2 — дуальная диагональ, hb веса 3 (кодирование без обращения матриц)", "z": "n/24", "сдвиги": "⌊p·z/96⌋ (2/3A — p mod z)",
   "таблица": json_ldpc, "5/6, блок (3,0)": "50 в 802.16e-2005 и CML; в некоторых открытых реализациях (yaldpc, FEC) — 68 (в проекте — вариант «-v68»)",
   "склейка": "табл. 333b (j по модуляции); блоки короче 576 бит — сверточным кодом"},
  "Mobile WiMAX (необязательный режим); те же базовые матрицы — ITU-T G.9960 (G.hn) для 1/2, 2/3B, 5/6", [T5.ist(s_l, "8.4.9.2.5.1"), T5.ist(s_lr, "8.4.9.2.5.2")] + ist_l + [ist_kod(cml_ldpc, r"Hbm=\[-1 94 73", "CML 1/2"), ist_kod(cml_ldpc, r"Hbm= \[1 25 55", "CML 5/6")],
  "стандарт, вклад C80216e-05/066r3 и проект совпали поэлементно; CML отличается одним элементом 3/4B (5,7): 38 вместо 35 — ошибка CML (3 источника против 1); ранг развёрнутых H полный; спор 50/68 разрешён текстом стандарта 2005 г. — 50",
  "есть: data/ldpc_wimax.json + ldpc_std.py — все 114 (вариант 5/6 с 50 — основной)"))

# ---------- HCS, CRC-32, CRC-16 HARQ ----------
s_h, _, l_h = T4.odna(r"generator polynomial g\(D = D8 \+ D2 \+ D \+ 1 of the polynomial D8 multiplied by the content")
s_hv, _, l_hv = T4.odna(r"CID=0x0F0F; HCS should then be set to 0xD5")
def crc8(d):
    c = 0
    for b in d:
        c ^= b
        for _ in range(8): c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c
assert crc8(bytes([0x80, 0xAA, 0xAA, 0x0F, 0x0F])) == 0xD5
s_c32 = T4.odna(r"When a PDU is received, its integrity is determined based on the CRC-32 checksum")[0]
ZAPISI.append(Z("WiMAX 802.16 MAC: HCS — CRC-8 D^8 + D^2 + D + 1 по 5 байтам заголовка; CRC-32 PDU (IEEE 802.3); CRC-16 CCITT HARQ", "WiMAX (IEEE 802.16-2004/16e)", "CRC-подобный",
  {"HCS": "CRC-8, g = D^8 + D^2 + D + 1 (0x07), начальное 0, без инверсии, старший бит первым; по первым 5 байтам 6-байтового заголовка MAC",
   "CRC-32": "как IEEE 802.3 (Ethernet FCS) по PDU MAC, если CI = 1", "CRC-16": "CRC16-CCITT (X.25) в конце пакета кодера HARQ (16e)",
   "HCS DL Frame Prefix (OFDM)": "8 бит, тот же многочлен"},
  "WiMAX MAC (все PHY)", [T4.ist(s_h, "HCS"), T4.ist(s_hv, "пример HCS"), T4.ist(s_c32, "CRC-32"), T5.ist(s_crc, "CRC-16 HARQ")],
  "пример стандарта: [80 AA AA 0F 0F] → HCS = 0xD5 — воспроизведён (CRC-8/0x07, init 0)",
  "есть: crc_katalog (CRC-8/SMBUS = 0x07 init 0; CRC-32/ISO-HDLC; CRC-16/X-25)"))

s_rg = T4.odna(r"seed b0\.\.\.b15 = 0,0,1,0,1,0,1,1,s0")[0]
s_rv4, _, l_rv4 = T4.odna(r"the first code shall be 011110000011111")
s_rg5 = T5.odna(r"The PRBS generator shall be initialized by the seed b0\.\.\.b15b14\.\.\.b0 = 0,0,1,0,1,0,1,1")[0]
s_rv, _, l_rv = T5.odna(r"UL_IDcellPermBase = 0, the first code shall be 011110000011111\.\.\.00110000010001")
vek = "00110000010001"; vek4 = "011110000011111"
st_rg = [15, 7, 4, 1, 0]
nach = "000000011010100"                      # b0…b14 при b14…b0 = 0,0,1,0,1,0,1,1,s0…s6 и UL_IDcellPermBase = 0
opis = lfsr_poisk(st_rg, nach, vek)
assert opis, "ранжирующий код не воспроизведён"
assert lfsr_poisk(st_rg, nach, vek4) is None and lfsr_poisk(st_rg, nach[::-1], vek4) is None   # пример 2004 г. ошибочен
ZAPISI.append(Z("WiMAX 802.16 OFDMA коды ранжирования: ГПСП 1 + X + X^4 + X^7 + X^15, затравка b14…b0 = 0,0,1,0,1,0,1,1,s0…s6 (UL_IDcellPermBase), коды по 144 бита", "WiMAX (IEEE 802.16-2004/16e)", "синхропоследовательность (ПСП)",
  {"многочлен": "1 + X^1 + X^4 + X^7 + X^15", "затравка (16e)": "b14…b0 = 0,0,1,0,1,0,1,1,s0,…,s6; s6 — младший разряд затравки и старший бит UL_IDcellPermBase",
   "длина кода": "144 бита (6 или 8 подканалов); коды 0…255, группа BS — от S до (S+O+N+M+L) mod 256",
   "пример (16e)": f"UL_IDcellPermBase = 0 → код начинается с {vek}", "схема, воспроизводящая пример": opis,
   "ошибка 2004": f"в 802.16-2004 затравка b0…b15 и пример {vek4}… — исправлены в 16e"},
  "WiMAX OFDMA: начальное/периодическое ранжирование, запрос полосы, передача обслуживания", [T4.ist(s_rg, "8.4.7.3 (2004)"), T4.ist(s_rv4, "пример 2004"), T5.ist(s_rg5, "затравка 16e"), T5.ist(s_rv, "пример 16e")],
  f"пример 16e воспроизведён регистром ({opis}); пример 2004 г. ни при какой схеме этого регистра не получается — исправлен поправкой 16e", "нет (синхропоследовательность — общим поиском)"))
