"""5G NR (3GPP TS 38.212): полярный код — все таблицы и параметры, CRC, малые блочные коды; сверка с srsRAN_4G."""
import re, sys, json
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/nr/ts_138212v190400p.pdf")
ZAPISI = []

# --- CRC (5.1): в тексте PDF степени идут перед именем, «D» и «1» — отдельно; число букв D выдаёт член D¹ ----------
kus, s_crc = T.kusok(r"cyclic generator polynomials:", r"\n5\.2\s*\n")
kus1 = " ".join(kus.split())
CRC = {}
for m in re.finditer(r"\]1 \[((?: \d+)+) (CRC\w+) ((?:\+ )+)= ((?:D )+)g", kus1):
    st = [int(x) for x in m.group(1).split()]; nD = m.group(4).count("D")
    assert nD in (len(st) + 1, len(st) + 2), m.group(0)
    st = st + ([1] if nD == len(st) + 2 else []) + [0]
    assert len(st) == m.group(3).count("+") + 1
    CRC[m.group(2)] = mnogochlen(st), sorted(st, reverse=True)
assert set(CRC) == {"CRC24A", "CRC24B", "CRC24C", "CRC16", "CRC11", "CRC6"}, CRC.keys()
srs_crc = kod("srsRAN_4G", "lib/include/srsran/phy/common/phy_common.h")
t = open(os.path.join(KOREN, srs_crc)).read()
for k, (v, _) in CRC.items():
    m = re.search(r"#define\s+SRSRAN_LTE_" + k + r"\s+(0[xX][0-9A-Fa-f]+)", t); assert m and int(m.group(1), 16) == v, k
lte = json.load(open(os.path.join(KOREN, "tablicy/lte_bazisy_rm_36212.json")))

# --- Π_IL^max (табл. 5.3.1.1-1), 164 ------------------------------------------------
kus, s_il = T.kusok(r"Table 5\.3\.1\.1-1: Interleaving pattern", r"\n5\.3\.1\.2\s*\n")
PI = pary_po_poryadku(chisla_bez_kolontitulov(kus), klyuchi_strokami(164, 6)); PI = [PI[m] for m in range(164)]
assert sorted(PI) == list(range(164))
# --- Q_0^{Nmax-1} (табл. 5.3.1.2-1), 1024 -----------------------------------------------
kus, s_q = T.kusok(r"Table 5\.3\.1\.2-1: Polar sequence", r"\n5\.3\.2\s*\n")
Q = pary_po_poryadku(chisla_bez_kolontitulov(kus), klyuchi_strokami(1024, 8)); Q = [Q[i] for i in range(1024)]
assert sorted(Q) == list(range(1024)) and Q[0] == 0 and Q[-1] == 1023
# --- P(i) подблочного перемежителя (табл. 5.4.1.1-1), 32 ---------------------------------
kus, s_p = T.kusok(r"Table 5\.4\.1\.1-1: Sub-block interleaver pattern", r"\n5\.4\.1\.2\s*\n")
P = pary_po_poryadku(chisla_bez_kolontitulov(kus), klyuchi_strokami(32, 8)); P = [P[i] for i in range(32)]
assert sorted(P) == list(range(32))
# --- перемежитель полезной нагрузки PBCH (табл. 7.1.1-1), 32 ------------------------------
kus, s_g = T.kusok(r"Table 7\.1\.1-1: Value of PBCH payload interleaver pattern", r"\n7\.1\.2\s*\n")
G = pary_po_poryadku(chisla_bez_kolontitulov(kus), klyuchi_strokami(32, 8)); G = [G[i] for i in range(32)]
assert sorted(G) == list(range(32))
# --- базис (32,K) (табл. 5.3.3.3-1) -------------------------------------------------------
kus, s_b = T.kusok(r"Table 5\.3\.3\.3-1: Basis sequences for \(32, K ?\) code", r"\n5\.4\s*\n")
ch = chisla_bez_kolontitulov(kus); rows = {}; i = 0
while i < len(ch) and len(rows) < 32:
    if ch[i] == len(rows) and all(v in (0, 1) for v in ch[i + 1:i + 12]): rows[ch[i]] = ch[i + 1:i + 12]; i += 12
    else: i += 1
M32 = [rows[r] for r in range(32)]
assert M32 == lte["данные"]["M32x11_t5.2.2.6.4-1"], "NR (32,K) ≠ LTE (32,O)"

# --- сверка с srsRAN_4G -----------------------------------------------------------------------
srs_pc = kod("srsRAN_4G", "lib/include/srsran/phy/fec/polar/polar_code.h")
srs_il = kod("srsRAN_4G", "lib/src/phy/fec/polar/polar_interleaver.c")
assert c_massiv(srs_pc, "mother_code_10") == Q
for n in range(5, 10):
    assert c_massiv(srs_pc, f"mother_code_{n}") == [q for q in Q if q < (1 << n)], n
assert c_massiv(srs_il, "polar_interleaver_pattern") == PI
assert c_massiv(srs_pc, "blk_interleaver_5") == P          # при N = 32 J(n) = P(n)
def J(N):
    return [P[(32 * n) // N] * (N // 32) + n % (N // 32) for n in range(N)]
for n in range(6, 11):
    assert c_massiv(srs_pc, f"blk_interleaver_{n}") == J(1 << n), n

tablica("nr_polar_38212", {"Q_0_1023_t5.3.1.2-1": Q, "PI_IL_max_t5.3.1.1-1": PI, "P_podblok_t5.4.1.1-1": P,
    "PBCH_G_t7.1.1-1": G, "CRC": {k: {"hex": hex(v), "степени": st} for k, (v, st) in CRC.items()}},
    "5G NR полярный код: последовательность надёжности Q (1024), перемежитель Π_IL (164), подблочный перемежитель P (32), "
    "перемежитель полезной нагрузки PBCH G (32), многочлены CRC",
    [T.ist(s_q, "Table 5.3.1.2-1"), T.ist(s_il, "Table 5.3.1.1-1"), T.ist(s_p, "Table 5.4.1.1-1"), T.ist(s_g, "Table 7.1.1-1"),
     T.ist(s_crc, "5.1 CRC"), ist_kod(srs_pc, "mother_code_10"), ist_kod(srs_il, "polar_interleaver_pattern")],
    "все таблицы разобраны из текста PDF парами (номер, значение) с проверкой полноты и биективности; Q совпала с mother_code_10 "
    "srsRAN_4G, подпоследовательности Q<N — с mother_code_5..9; Π_IL — с polar_interleaver_pattern; P — с blk_interleaver_5, "
    "J(n) по формуле 5.4.1.1 для N=64..1024 — с blk_interleaver_6..10; CRC — с SRSRAN_LTE_CRC24A/B/C/16/11/6; "
    "многочлены CRC сверены и по снимку risunki/nr_crc_38212_s12.png")
tablica("nr_bazis_32K_38212", M32, "Базис (32,K) малых блочных кодов NR (K = 3..11)", [T.ist(s_b, "Table 5.3.3.3-1")],
        "совпал поэлементно с таблицей 5.2.2.6.4-1 36.212 (tablicy/lte_bazisy_rm_36212.json)")

s_pbch = T.odna(r"^7\.1\.4")[0]; s_pbch = [s for s, n, l in T.naiti(r"^7\.1\.4")][-1]
s_dci = [s for s, n, l in T.naiti(r"^7\.3\.3")][-1]
s_uci = [s for s, n, l in T.naiti(r"^6\.3\.1\.3\.1")][-1]
s_rm = [s for s, n, l in T.naiti(r"^5\.4\.1\.1")][-1]
s_chil = [s for s, n, l in T.naiti(r"^5\.4\.1\.3")][-1]
s_pe = [s for s, n, l in T.naiti(r"^5\.3\.1\.2")][-1]
s_sm = [s for s, n, l in T.naiti(r"^5\.3\.3\.1")][-1]
ZAPISI += [
 {"имя": "5G NR полярный код (PBCH, DCI, UCI)", "семейство": "5G NR", "область": "mobilnaya", "вид": "полярный",
  "параметры": {
    "N": "N = 2^n, n = max(min(n1, n2, n_max), n_min), n_min = 5; n_max = 9 (нисходящий: PBCH, DCI), 10 (восходящий: UCI) — 5.3.1",
    "последовательность надёжности": "Q_0^1023 (табл. 5.3.1.2-1) → tablicy/nr_polar_38212.json; для N < 1024 берутся Q_i < N в том же порядке",
    "перемежитель входа": "I_IL = 1 (PBCH, DCI): Π_IL^max 164 значения (табл. 5.3.1.1-1); I_IL = 0 для UCI",
    "биты проверки чётности PC": "n_PC = 0 (PBCH, DCI); UCI 12 ≤ K ≤ 19 (18 ≤ K_r ≤ 25 с CRC6): n_PC = 3, n_PC^wm = 1 при E − K + 3 > 192, иначе 0; регистр y[0..4] длины 5 (5.3.1.2)",
    "CRC": "PBCH и DCI — CRC24C (DCI: 24 единицы в начале + маска RNTI на последние 16 бит); UCI: 12–19 бит — CRC6, ≥ 20 — CRC11 (сегментация на 2 блока при K ≥ 360 и E ≥ 1088)",
    "кодирование": "d = u·G_N, G_N = n-я кронекерова степень [[1,0],[1,1]]; замороженные — первые N − K − n_PC по Q плюс учёт выколотых/укороченных при согласовании",
    "согласование скорости": "5.4.1.1: подблочный перемежитель 32 блока P(i) (табл. 5.4.1.1-1, J(n) = P(⌊32n/N⌋)·N/32 + n mod N/32); 5.4.1.2 — E ≥ N повторение, иначе K/E ≤ 7/16 выкалывание (с начала) либо укорочение (с конца)",
    "перемежитель канала": "5.4.1.3: треугольный, I_BIL = 1 только UCI (T·(T+1)/2 ≥ E)",
    "PBCH": "перемежение полезной нагрузки G(j) (табл. 7.1.1-1), скремблирование части бит (7.1.2), K = 56 (32+24), E = 864",
    "порядок бит": "c_0 первым; CRC добавляется в конец; выход после RM — e_0 первым"},
  "где применяется": "5G NR: PBCH (MIB), PDCCH (DCI), PUCCH/PUSCH UCI ≥ 12 бит; также сайдлинк PSBCH, SCI 1-й ступени",
  "источник": [T.ist(s_pe, "5.3.1.2 Polar encoding"), T.ist(s_q, "Table 5.3.1.2-1"), T.ist(s_il, "Table 5.3.1.1-1"),
               T.ist(s_rm, "5.4.1.1 sub-block interleaving"), T.ist(s_chil, "5.4.1.3 channel interleaving"),
               T.ist(s_pbch, "7.1.4: n_max = 9, I_IL = 1, n_PC = 0, n_PC^wm = 0"), T.ist(s_dci, "7.3.3: те же параметры для DCI"),
               T.ist(s_uci, "6.3.1.3.1: n_max = 10, I_IL = 0, n_PC = 3"), T.ist(s_g, "Table 7.1.1-1"),
               ist_kod(srs_pc, "mother_code_10"), ist_kod(srs_il, "polar_interleaver_pattern")],
  "проверка": "все таблицы — из текста PDF с проверкой полноты и биективности; поэлементно совпали с srsRAN_4G (Q, Q<N для N=32…512, Π_IL, P, J(n) для N=64…1024); параметры n_max/I_IL/n_PC прочитаны из 7.1.4, 7.3.3, 6.3.1.3.1",
  "сложность внедрения": "нет в проекте; средняя — кодер G_N и SC/SCL-декодер (~200 строк numpy), таблицы готовы в tablicy/nr_polar_38212.json; для слепого опознания: разрешённые позиции по Q, CRC24C с маской RNTI"},
 {"имя": "5G NR CRC24A/24B/24C/16/11/6", "семейство": "5G NR", "область": "mobilnaya", "вид": "CRC-подобный",
  "параметры": {k: {"многочлен": D_zapis(st), "hex": hex(v)} for k, (v, st) in CRC.items()} | {"порядок": "старший первым, начальное 0 (5.1); у DCI — 24 единицы впереди, маска RNTI"},
  "где применяется": "24A — TB LDPC; 24B — кодовые блоки LDPC; 24C — PBCH и DCI (полярный); 16 — TB ≤ 3824; 11 и 6 — UCI полярный",
  "источник": [T.ist(s_crc, "5.1"), ist_kod(srs_crc, "SRSRAN_LTE_CRC24C")],
  "проверка": "многочлены разобраны из текста с проверкой числа членов; сверены с srsRAN phy_common.h и снимком формул risunki/nr_crc_38212_s12.png",
  "сложность внедрения": "частично есть: crc_katalog.py (24A=LTE-A, 24B=LTE-B, 16/XMODEM-вид, CRC-6/CDMA2000-A — не тот); 24C, 11 (0xE21), 6 (0x21) добавить в каталог — тривиально"},
 {"имя": "5G NR малые блочные коды (1, 2 бита, (32,K) K=3..11)", "семейство": "5G NR", "область": "mobilnaya", "вид": "повторение/симплекс/РМ",
  "параметры": {"K=1": "повторение с «x»-заполнителями под модуляцию (5.3.3.1)", "K=2": "симплекс (3,2) c0 c1 c2=c0⊕c1 с заполнителями (5.3.3.2)",
                "3≤K≤11": "(32,K) — базис табл. 5.3.3.3-1 = tablicy/nr_bazis_32K_38212.json (тот же, что LTE (32,O))"},
  "где применяется": "NR UCI (HARQ-ACK, SR, CSI) до 11 бит на PUCCH/PUSCH", "источник": [T.ist(s_sm, "5.3.3.1"), T.ist(s_b, "Table 5.3.3.3-1")],
  "проверка": "базис совпал с LTE 36.212 табл. 5.2.2.6.4-1", "сложность внедрения": "нет; простая (как LTE (32,O))"},
]
# --- LDPC (5.3.2): наборы Z и сдвиги V базовых графов — сверка с ldpc_nr.json проекта ---------------------------------
kus, s_z = T.kusok(r"Table 5\.3\.2-1: Sets of LDPC lifting size Z", r"Table 5\.3\.2-2")
ZS = {int(a): [int(x) for x in b.split(",")] for a, b in re.findall(r"(\d)\s*\{([\d,\s]+)\}", kus)}
assert sorted(ZS) == list(range(8)) and sum(len(v) for v in ZS.values()) == 51
PROJ_NR = "/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_nr.json"
nrj = json.load(open(PROJ_NR))["графы"]
LDPC_N = {}
for bg, ot, do in (("1", r"Table 5\.3\.2-2: LDPC base graph 1", r"Table 5\.3\.2-3: LDPC base graph 2"), ("2", r"Table 5\.3\.2-3: LDPC base graph 2", r"\n5\.3\.3\s*\n")):
    kus, s_bg = T.kusok(ot, do)
    kus = re.sub(r"=====СТР \d+=====.*?Release \d+", " ", kus, flags=re.S)
    potok = " " + " ".join(re.findall(r"\d+", kus)) + " "
    V = [nrj[bg][str(l)]["V"] for l in range(8)]
    for l in range(8): assert nrj[bg][str(l)]["Z"] == ZS[l], (bg, l)
    n = 0
    for i in range(len(V[0])):
        for j in range(len(V[0][i])):
            if V[0][i][j] < 0: continue
            n += 1
            assert " " + " ".join(str(x) for x in [j] + [V[l][i][j] for l in range(8)]) + " " in potok, (bg, i, j)
    LDPC_N[bg] = (n, s_bg, len(V[0]), len(V[0][0]))
assert LDPC_N["1"][0] == 316 and LDPC_N["2"][0] == 197
s_k0 = T.odna(r"Table 5\.4\.2\.1-2: Starting position")[0]; s_bil = [s for s, n, l in T.naiti(r"^5\.4\.2\.2\s*$")][-1]
s_lbrm = T.odna(r"Table 5\.4\.2\.1-1: Value of")[0]; s_sel = [s for s, n, l in T.naiti(r"^5\.2\.2\s*$")][-1]
ZAPISI.append({"имя": "5G NR LDPC (базовые графы 1 и 2) и согласование скорости", "семейство": "5G NR", "область": "mobilnaya", "вид": "LDPC (квазициклический)",
  "параметры": {"BG1": "46×68, 22 столбца данных, 316 ненулевых сдвигов; K_cb ≤ 8448; скорость матери 1/3",
                "BG2": "42×52, 10 столбцов данных (K_b = 6…10), 197 ненулевых сдвигов; K_cb ≤ 3840; скорость матери 1/5",
                "Z": "51 размер в 8 наборах i_LS (табл. 5.3.2-1): " + "; ".join(f"{k}: {v}" for k, v in ZS.items()),
                "сдвиг": "P_ij = V_ij mod Z (5.3.2); V — tablicy проекта data/ldpc_nr.json",
                "выбор графа": "BG2 при A ≤ 292, или A ≤ 3824 и R ≤ 0,67, или R ≤ 0,25; иначе BG1 (7.2.2 / 6.2.2)",
                "сегментация": "CRC24A на TB (CRC16 при A ≤ 3824), CRC24B на кодовый блок при C > 1; заполнители <NULL> (5.2.2)",
                "выкалывание": "первые 2Z систематических бит не передаются",
                "согласование": "кольцевой буфер N_cb (LBRM: N_ref = ⌊TBS_LBRM/(C·R_LBRM)⌋, R_LBRM = 2/3); k0 по RV0..3: 0, ⌊17N_cb/66Z⌋Z, ⌊33N_cb/66Z⌋Z, ⌊56N_cb/66Z⌋Z (BG1), для BG2 — 13/50, 25/50, 43/50 (табл. 5.4.2.1-2); заполнители пропускаются",
                "перемежитель бит": "5.4.2.2: построчная запись в Q_m строк, чтение по столбцам (Q_m — порядок модуляции)"},
  "где применяется": "5G NR PDSCH/PUSCH (DL-SCH, UL-SCH, PCH), сайдлинк SL-SCH",
  "источник": [T.ist(s_z, "Table 5.3.2-1"), T.ist(LDPC_N["1"][1], "Table 5.3.2-2 (BG1)"), T.ist(LDPC_N["2"][1], "Table 5.3.2-3 (BG2)"),
               T.ist(s_lbrm, "Table 5.4.2.1-1 LBRM"), T.ist(s_k0, "Table 5.4.2.1-2 k0"), T.ist(s_bil, "5.4.2.2 bit interleaving"), T.ist(s_sel, "5.2.2 segmentation")],
  "проверка": "наборы Z (51) разобраны из текста = Z проекта; все 316 (BG1) и 197 (BG2) ненулевых сдвигов проекта (8 наборов i_LS каждый) найдены в тексте таблиц 5.3.2-2/-3 как подряд идущие «j V0…V7»; "
              "проект сам сверен с AFF3CT, srsRAN_4G и Sionna (data/ldpc_nr.json, поле «откуда»)",
  "сложность внедрения": "ЕСТЬ в проекте: ldpc_std.py (nr-bg1/bg2 × 51 Z, выкалывание 2Z), data/ldpc_nr.json; нет — согласования скорости (k0/RV, LBRM) и перемежителя бит 5.4.2.2 (простые, по формулам)"})

if __name__ == "__main__":
    print(len(ZAPISI), "записей", {k: hex(v) for k, (v, _) in CRC.items()})
