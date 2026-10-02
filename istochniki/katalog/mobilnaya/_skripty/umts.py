"""UMTS (3GPP TS 25.212): CRC, свёрточные K=9, турбокод с перемежителем (простые числа), перемежители 1/2, TFCI (32,10).
Перемежитель турбокода реализован по тексту 4.2.3.2.3 (формулы сверены по снимкам risunki/umts_turbo_il_s2*.png)
и сравнен со srsRAN_4G tc_interl_umts.c (собран заглушками skripty/c/) для всех K = 40…5114."""
import math, re, subprocess, sys, json
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/umts/ts_125212v190000p.pdf")
ZAPISI = []
# CRC
CRC = {}
for imya in ("CRC24", "CRC16", "CRC12", "CRC8"):
    s, n, l = T.odna(r"g" + imya + r"\(D\) = ")
    st = [0 if t == "1" else 1 if t == "D" else int(t[1:]) for t in re.findall(r"D\d*|\b1\b", l.split("=")[1])]
    CRC[imya] = (s, st, mnogochlen(st))
assert CRC["CRC24"][2] == 0x1800063 and CRC["CRC16"][2] == 0x11021 and CRC["CRC12"][2] == 0x180F and CRC["CRC8"][2] == 0x19B
# свёрточные
sv = {}
for s, n, l in T.naiti(r"G\d = \d{3} \(octal\)"):
    m = re.search(r"G(\d) = (\d{3})", l); sv.setdefault("1/3" if len(sv.get("1/3", {})) < 3 else "1/2", {})["G" + m.group(1)] = m.group(2)
s_sv = T.odna(r"G0 = 557 \(octal\)")[0]
assert sv == {"1/3": {"G0": "557", "G1": "663", "G2": "711"}, "1/2": {"G0": "561", "G1": "753"}}, sv
# таблица 2: простые и первообразные корни
kus, s_t2 = T.kusok(r"Table 2: List of prime number p and associated primitive root v", r"\(3\) Write the input")
ch = chisla_bez_kolontitulov(kus)
pv = {}
for i in range(len(ch) - 1):
    p, v = ch[i], ch[i + 1]
    if p >= 7 and p not in pv and all(p % d for d in range(2, int(p ** .5) + 1)) and 2 <= v < p and len(pv) == sum(1 for q in pv):
        if pow(v, (p - 1), p) == 1 and all(pow(v, (p - 1) // f, p) != 1 for f in {f for f in range(2, p) if (p - 1) % f == 0 and all(f % d for d in range(2, int(f ** .5) + 1))}):
            pv[p] = v
PRIMES = sorted(pv)
assert len(PRIMES) == 52 and PRIMES[0] == 7 and PRIMES[-1] == 257
srs = kod("srsRAN_4G", "lib/src/phy/fec/turbo/tc_interl_umts.c")
assert c_massiv(srs, "table_p") == PRIMES and c_massiv(srs, "table_v") == [pv[p] for p in PRIMES]
# таблица 3
kus, s_t3 = T.kusok(r"Table 3: Inter-row permutation patterns", r"\(5\) Perform")
pat = [[int(x) for x in m.split(",")] for m in re.findall(r"<([\d,\s]+)>", kus)]
assert [len(x) for x in pat] == [5, 10, 20, 20] and all(sorted(x) == list(range(len(x))) for x in pat)
def T_pattern(K):
    if 40 <= K <= 159: return pat[0]
    if 160 <= K <= 200 or 481 <= K <= 530: return pat[1]
    if 2281 <= K <= 2480 or 3161 <= K <= 3210: return pat[2]
    return pat[3]
def peremezhitel(K, s_nulyami=False, kak_srsran=False):
    R = 5 if K <= 159 else 10 if (K <= 200 or 481 <= K <= 530) else 20
    if 481 <= K <= 530 and not kak_srsran: p = 53; C = p
    elif 481 <= K <= 530:        # srsRAN: p = 53, но C затем пересчитывается общим правилом (C = 52 при K ≤ 520)
        p = 53; C = p - 1 if K <= R * (p - 1) else p
    else:
        p = min(q for q in PRIMES if K <= R * (q + 1))
        C = p - 1 if K <= R * (p - 1) else p if K <= R * p else p + 1
    v = pv[p]; s = [1]
    for j in range(1, p - 1): s.append((v * s[-1]) % p)
    q = [1]
    while len(q) < R:
        c = q[-1] + 1
        while not (c > 6 and (kak_srsran or all(c % d for d in range(2, int(c ** .5) + 1))) and math.gcd(c, p - 1) == 1): c += 1
        q.append(c)
    Tp = T_pattern(K); r = [0] * R
    for i in range(R): r[Tp[i]] = q[i]
    U = []
    for i in range(R):
        if C == p: u = [s[(j * r[i]) % (p - 1)] for j in range(p - 1)] + [0]
        elif C == p + 1: u = [s[(j * r[i]) % (p - 1)] for j in range(p - 1)] + [0, p]
        else: u = [s[(j * r[i]) % (p - 1)] - 1 for j in range(p - 1)]
        U.append(u)
    if C == p + 1 and K == R * C: U[R - 1][p], U[R - 1][0] = U[R - 1][0], U[R - 1][p]
    vyh = []
    for j in range(C):
        for i in range(R):
            orig = Tp[i] * C + U[i if kak_srsran else Tp[i]][j]
            if s_nulyami: vyh.append(orig + 1 if orig < K else 0)
            elif orig < K: vyh.append(orig)
    if not s_nulyami: assert sorted(vyh) == list(range(K))
    return vyh, R, C, p
# --- проверка 1: пример K = 250 из arXiv:0802.0808 (табл. 15: видимые 4 + 5 значений в каждой из 13 строк) -------
def matrica_vyhoda(K):
    """Выход до выкидывания: по столбцам, в каждом R значений (номер входа + 1 или 0 для заполнителя)."""
    vyh, R, C, p = peremezhitel(K, s_nulyami=True)
    return [vyh[j * R:(j + 1) * R] for j in range(C)]
AR = "istochniki/umts/arxiv_0802.0808_turbo_interleaving_cdma2000_wcdma.pdf"
TA = Tekst(AR)
i0 = [n for s, n, l in TA.naiti(r"Table 15\.")][0]
vid = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", "\n".join(TA.stroki[i0 - 1:i0 + 400]))][:117]
M250 = matrica_vyhoda(250)
rashozh = [(j, k, a, b) for j in range(13) for k, (a, b) in enumerate(zip(vid[9 * j:9 * j + 9], M250[j][:4] + M250[j][-5:])) if a != b]
# единственное расхождение — опечатка статьи: в столбце значений 183…195 напечатано «94» вместо 194 (снимок risunki/arxiv_wcdma_t15.png)
assert rashozh == [(5, 2, 94, 194)], rashozh
s_ar = TA.odna(r"Table 15\.")[0]
# --- проверка 2: srsRAN_4G (tc_interl_umts.c) на всех K; его отличия объяснены моделью ---------------------------
out = subprocess.run([os.path.join(os.path.dirname(os.path.dirname(KOREN)), "umts_il")], capture_output=True, text=True).stdout.split("\n")
srs_perm = {int(l.split()[0]): list(map(int, l.split()[1:])) for l in out if l}
assert len(srs_perm) == 5075
sovp = [K for K in srs_perm if srs_perm[K] == peremezhitel(K)[0]]
model = [K for K in srs_perm if srs_perm[K] == peremezhitel(K, kak_srsran=True)[0]]
assert len(model) == 5075, len(model)          # отличия srsRAN полностью объяснены тремя отступлениями
svereno = len(srs_perm)
primery = {K: dict(zip(("R", "C", "p"), peremezhitel(K)[1:])) | {"первые 16": peremezhitel(K)[0][:16]} for K in (40, 41, 160, 481, 530, 2281, 3161, 5114)}
# перемежители 1 и 2
kus, s_t4 = T.kusok(r"Table 4: Inter-column permutation patterns for 1st interleaving", r"4\.2\.5\.\d|Table 5")
tti = re.findall(r"(?m)^(\d+) ms\s*$", kus); pp = re.findall(r"(?m)^<([\d,]+)>\s*$", kus)
assert len(tti) == len(pp) == 7                 # табл. 4 (4 строки) и табл. 4A (UL_DPCH_10ms_Mode, 3 строки)
p1 = {a: [int(x) for x in b.split(",")] for a, b in zip(tti[:4], pp[:4])}
p1A = {a: [int(x) for x in b.split(",")] for a, b in zip(tti[4:], pp[4:])}
assert p1A == {"20": [0], "40": [0, 1], "80": [0, 2, 1, 3]}
assert p1 == {"10": [0], "20": [0, 1], "40": [0, 2, 1, 3], "80": [0, 4, 2, 6, 1, 5, 3, 7]}, p1
kus, s_t7 = T.kusok(r"Table 7 Inter-column permutation pattern for 2nd interleaving", r"4\.2\.1\d|4\.3")
m = re.search(r"<([\d,\s]+)>", kus.replace("\n", " ")); p2 = [int(x) for x in m.group(1).split(",")]
assert len(p2) == 30 and sorted(p2) == list(range(30))
# TFCI (32,10)
kus, s_t8 = T.kusok(r"Table 8: Basis sequences for \(32,10\) TFCI code", r"4\.3\.3\.\d|\n4\.3\.4")
ch = chisla_bez_kolontitulov(kus); rows = {}; i = 0
while i < len(ch) and len(rows) < 32:
    if ch[i] == len(rows) and all(v in (0, 1) for v in ch[i + 1:i + 11]): rows[ch[i]] = ch[i + 1:i + 11]; i += 11
    else: i += 1
M = [rows[r] for r in range(32)]
def dmin(M, k):
    return min(sum(sum(M[i][n] & (x >> n) & 1 for n in range(k)) & 1 for i in range(32)) for x in range(1, 1 << k))
d_tfci = dmin(M, 10)
lte = json.load(open(os.path.join(KOREN, "tablicy/lte_bazisy_rm_36212.json")))["данные"]["M32x11_t5.2.2.6.4-1"]
sovp_lte = sum(1 for n in range(10) if [r[n] for r in M] in [[r[k] for r in lte] for k in range(11)])
tablica("umts_25212", {"CRC": {k: {"многочлен": D_zapis(v[1]), "hex": hex(v[2])} for k, v in CRC.items()},
    "svertka": sv, "prostye_p_v_t2": {str(p): pv[p] for p in PRIMES}, "mezhstrochnye_t3": pat,
    "pervyi_peremezhitel_t4": p1, "pervyi_peremezhitel_t4A_UL_DPCH_10ms": p1A, "vtoroi_peremezhitel_t7": p2, "TFCI_32x10_t8": M, "primery_turbo_peremezhitelya": primery},
    "UMTS 25.212: CRC, свёрточные коды K=9, простые/корни и межстрочные шаблоны турбоперемежителя, 1-й и 2-й перемежители, базис TFCI",
    [T.ist(CRC["CRC24"][0], "4.2.1.1"), T.ist(s_sv, "Figure 3"), T.ist(s_t2, "Table 2"), T.ist(s_t3, "Table 3"),
     T.ist(s_t4, "Table 4"), T.ist(s_t7, "Table 7"), T.ist(s_t8, "Table 8"), ist_kod(srs, "table_p")],
    f"простые проверены на простоту, корни — на первообразность; table_p/table_v srsRAN совпали; турбоперемежитель по тексту "
    f"совпал с примером K=250 статьи arXiv:0802.0808 (табл. 15: 116 из 117 видимых значений, одно — опечатка статьи «94» вместо 194); srsRAN_4G tc_interl_umts.c совпадает лишь для "
    f"{len(sovp)} из {svereno} K: у него q_i берутся без проверки простоты при чтении строки берётся U_i вместо U_T(i), при 481 ≤ K ≤ 520 C = 52 вместо 53 — модель с этими "
    f"тремя отступлениями воспроизводит srsRAN на всех {svereno} K (ошибка srsRAN, в LTE не используется); перестановки 1-го/2-го перемежителей биективны; TFCI d_min = {d_tfci}; "
    f"{sovp_lte} из 10 столбцов TFCI встречаются среди столбцов базиса LTE (32,O)")
L = [T.ist(CRC["CRC24"][0], "4.2.1.1")]
ZAPISI += [
 {"имя": "UMTS CRC 24/16/12/8", "семейство": "UMTS/WCDMA", "область": "mobilnaya", "вид": "CRC-подобный",
  "параметры": {k: {"многочлен": D_zapis(v[1]), "hex": hex(v[2])} for k, v in CRC.items()} | {"порядок": "CRC-биты передаются в обратном порядке (p_L … p_1) — 4.2.1.2; начальное 0"},
  "где применяется": "UMTS FDD/TDD транспортные блоки (длина CRC задаётся верхним уровнем: 0, 8, 12, 16, 24)",
  "источник": L, "проверка": "многочлены прочитаны из текста; CRC-24 UMTS = LTE CRC24B, CRC-16 = CCITT", 
  "сложность внедрения": "частично есть: crc_katalog.py — CRC-8/WCDMA, CRC-12/UMTS, CRC-16/UMTS (0x8005 — это другой!), CRC-24/LTE-B; нужна запись «обратный порядок бит CRC»"},
 {"имя": "UMTS свёрточные K=9 1/2 (561,753) и 1/3 (557,663,711)", "семейство": "UMTS/WCDMA", "область": "mobilnaya", "вид": "свёрточный",
  "параметры": {"K": 9, "1/2": "G0=561, G1=753 (восьм.)", "1/3": "G0=557, G1=663, G2=711 (восьм.)", "хвост": "8 нулей, начальное состояние 0",
                "порядок выхода": "output0, output1(, output2) поочерёдно", "согласование": "повторение/выкалывание алгоритмом 4.2.7 (e_ini, e_plus, e_minus)"},
  "где применяется": "UMTS BCH, PCH, RACH, FACH, DCH (речь AMR, сигнализация); cdma2000/IS-95 используют ту же пару 1/2 (561/753) — см. C.S0002",
  "источник": [T.ist(s_sv, "4.2.3.1 Figure 3")], "проверка": "многочлены прочитаны из текста рисунка; 561/753 уже есть в kod.py проекта как «CDMA IS-95»",
  "сложность внедрения": "есть: витерби для любых K (kod.витерби_n, svyortka.витерби) — K=9 (256 состояний) поддерживается; нет согласования скорости UMTS"},
 {"имя": "UMTS турбокод PCCC 1/3 (перемежитель на простых числах)", "семейство": "UMTS/WCDMA", "область": "mobilnaya", "вид": "турбо PCCC",
  "параметры": {"составляющие": "8-состояний RSC g0 = 1 + D2 + D3, g1 = 1 + D + D3 (как LTE)", "K": "40…5114",
                "перемежитель": "4.2.3.2.3: R = 5/10/20 строк, простое p и первообразный корень v (табл. 2, 52 пары), C ∈ {p−1, p, p+1}, "
                                "s(j) = v·s(j−1) mod p, q_i — простые > 6 взаимно простые с p−1, межстрочные шаблоны (табл. 3), выход по столбцам с выкидыванием пустых",
                "хвост": "12 бит (как LTE), начальное 0", "таблицы": "tablicy/umts_25212.json"},
  "где применяется": "UMTS DCH/DSCH/HS-DSCH/E-DCH (данные), TD-SCDMA (25.222)",
  "источник": [T.ist(s_t2, "Table 2"), T.ist(s_t3, "Table 3"), TA.ist(s_ar, "пример K=250, Table 15"), ist_kod(srs, "table_p")],
  "проверка": f"реализация по тексту стандарта (skripty/umts.py) совпала с независимым примером K=250 (arXiv:0802.0808, табл. 15: 116 из 117 видимых значений, 117-е — опечатка статьи 94/194); srsRAN_4G tc_interl_umts.c отступает от стандарта (q_i без проверки простоты, U_i вместо U_T(i), C = 52 вместо 53 при 481 ≤ K ≤ 520) — совпадает только на {len(sovp)} из 5075 K, отличия полностью объяснены",
  "сложность внедрения": "частично есть: turbo.py (BCJR PCCC, поиск перестановки перемежителя вслепую); генератор перемежителя UMTS — готов в skripty/umts.py, перенести ~40 строк"},
 {"имя": "UMTS TFCI (32,10) — подкод РМ 2-го порядка", "семейство": "UMTS/WCDMA", "область": "mobilnaya", "вид": "РМ (подкод Рида — Маллера)",
  "параметры": {"n": 32, "k": 10, "d_min": d_tfci, "базис": "Table 8 → tablicy/umts_25212.json", "в режиме DSCH split": "(16,5) 1-го порядка"},
  "где применяется": "UMTS DPCCH/S-CCPCH/PRACH TFCI", "источник": [T.ist(s_t8, "Table 8")],
  "проверка": "32×10 разобраны из текста с проверкой номеров строк; d_min перебором", "сложность внедрения": "нет; простая (Адамар/перебор 1024)"},
 {"имя": "UMTS перемежители 1-й (TTI) и 2-й (30 столбцов)", "семейство": "UMTS/WCDMA", "область": "mobilnaya", "вид": "перемежитель (блочный)",
  "параметры": {"1-й": "столбцов C1 = TTI/10 мс, перестановки табл. 4: 10 мс <0>, 20 <0,1>, 40 <0,2,1,3>, 80 <0,4,2,6,1,5,3,7>",
                "2-й": f"30 столбцов, перестановка табл. 7 {p2}"},
  "где применяется": "UMTS DCH после согласования скорости", "источник": [T.ist(s_t4, "Table 4"), T.ist(s_t7, "Table 7")],
  "проверка": "разобраны из текста, биективность проверена", "сложность внедрения": "частично: peremezhenie.py (блочное вслепую); шаблон 2-го — готов"},
]
if __name__ == "__main__":
    print(len(ZAPISI), "записей; TFCI d", d_tfci, "совп LTE", sovp_lte, "K сверено", svereno)
