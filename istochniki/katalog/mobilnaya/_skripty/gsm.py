"""GSM/GPRS/EDGE (3GPP TS 45.003, 45.002): многочлены свёрточных кодов, CRC/Файр, выкалывание, перемежение,
синхропоследовательности. Всё — из текста стандарта с проверками; сверка с libosmocore (conv_codes_gsm.py,
gsm0503_parity.c, gsm0503_interleaving.c через ctypes; сборка прошла tests/coding: вывод = coding_test.ok)."""
import ctypes, json, re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/gsm/ts_145003v190000p.pdf")
T2 = Tekst("istochniki/gsm/ts_145002v190000p.pdf")
ZAPISI = []
LIB = os.path.join(REPOS, "libosmocore")

def stepeni(vyr):
    """«1 + D + D3+ D4» → [0,1,3,4]."""
    st = []
    for t in re.findall(r"D\d*|\b1\b", vyr):
        st.append(0 if t == "1" else 1 if t == "D" else int(t[1:]))
    return sorted(set(st))

# --- 1. Приложение B: G0…G9 --------------------------------------------------------------------------
kus, s_B = T.kusok(r"Annex B \(informative\):", r"Annex C \(informative\)", posl=True)
G = {}
for m in re.finditer(r"G(\d)\s*=\s*([1D\d\s+]+?)(?=\s*(?:TCH|PDTCH|O-TCH|\n\s*[A-Z]))", kus):
    G[int(m.group(1))] = stepeni(m.group(2))
assert sorted(G) == list(range(10)), G
isp = {}
for m in re.finditer(r"G(\d)\s*=\s*[1D\d\s+]+?\s*((?:[A-Z(]).*?)(?=\n\s*G\d\s*=|$)", kus, re.S):
    isp[int(m.group(1))] = re.sub(r"^(?:D\d*\s*\+?\s*)+", "", " ".join(m.group(2).split()))
sys.path.insert(0, os.path.join(LIB, "utils"))
import conv_codes_gsm as ccg                     # libosmocore utils/conv_codes_gsm.py (GPL-2.0+)
put_ccg = kod("libosmocore", "utils/conv_codes_gsm.py"); kod("libosmocore", "utils/conv_gen.py")
for i in range(8):
    assert getattr(ccg, f"G{i}") == ccg.poly(*G[i]), i
# восьмеричная запись (старший разряд — текущий вход, как в 36.212): бит (K−1−d) для члена D^d
def okt(st, K): return format(sum(1 << (K - 1 - d) for d in st), "o")

# --- 2. CRC / коды чётности -----------------------------------------------------------------------------
parnye = []
for m in re.finditer(r"((?:D\d+\s*\+\s*)+(?:D\d*\s*\+\s*)*1)\s*,\s*yields a remainder equal to:\s*\n?\s*((?:D\d*\s*\+\s*)*(?:D\s*\+\s*)?1|D\d*[^\n]*?)\.", T.ves):
    g = stepeni(m.group(1)); r = stepeni(m.group(2))
    parnye.append((T.stranica_pozicii(m.start()), tuple(g), tuple(r)))
for m in re.finditer(r"when divided by ((?:D\d*\s*\+\s*)+1) yields a remainder\s*(?:\n\s*)?equal to ((?:D\d*\s*\+\s*)+1)", T.ves):
    parnye.append((T.stranica_pozicii(m.start()), tuple(stepeni(m.group(1))), tuple(stepeni(m.group(2)))))
for m in re.finditer(r"g\d?\(D\)\s*=\s*((?:D\d*\s*\+\s*)+1)", T.ves):
    parnye.append((T.stranica_pozicii(m.start()), tuple(stepeni(m.group(1))), None))
s_fire = T.odna(r"g\(D\) = \(D23 \+ 1\)\*\(D17 \+ D3 \+ 1\)")[0]
fire = [a ^ b for a, b in [(0, 0)]]  # заглушка для ясности ниже
def umn(a, b):
    r = 0
    for i in range(64):
        if (b >> i) & 1: r ^= a << i
    return r
FIRE = umn(mnogochlen([23, 0]), mnogochlen([17, 3, 0]))
assert FIRE == (1 << 40) | 0x0004820009
kody_ch = {}
for s, g, r in parnye:
    w = max(g); key = (w, mnogochlen(g) & ((1 << w) - 1))
    zap = kody_ch.setdefault(key, {"степени": list(g), "остатки": set(), "страницы": set()})
    zap["страницы"].add(s)
    if r is not None: zap["остатки"].add(mnogochlen(r))
kody_ch[(40, FIRE & ((1 << 40) - 1))] = {"степени": [40, 26, 23, 17, 3, 0], "остатки": {(1 << 40) - 1}, "страницы": {s_fire}}
# сверка с libosmocore gsm0503_parity.c
put_par = kod("libosmocore", "src/coding/gsm0503_parity.c")
tp = open(os.path.join(KOREN, put_par)).read()
osmo = {m.group(1): (int(m.group(2)), int(m.group(3), 16), int(m.group(4), 16)) for m in re.finditer(
    r"gsm0503_(\w+)\s*=\s*\{\s*\.bits\s*=\s*(\d+),\s*\.poly\s*=\s*(0x[0-9a-fA-F]+)\w*,\s*\.init\s*=\s*0x[0-9a-fA-F]+\w*,\s*\.remainder\s*=\s*(0x[0-9a-fA-F]+)", tp)}
assert len(osmo) >= 9, osmo
for imya, (w, p, rem) in osmo.items():
    assert (w, p) in kody_ch, (imya, w, hex(p))
    ost = kody_ch[(w, p)]["остатки"]
    assert not ost or rem in ost or (rem == 0 and not ost), (imya, hex(rem), [hex(x) for x in ost])
# сверка с каталогом CRC проекта (RevEng): poly и xorout
PROJ = "/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py"
reveng = {m.group(1): (int(m.group(2)), int(m.group(3), 16), int(m.group(4), 16)) for m in re.finditer(
    r'\("(CRC-\d+/GSM[^"]*)", (\d+), (0x[0-9A-Fa-f]+), 0x[0-9A-Fa-f]+, \w+, \w+, (0x[0-9A-Fa-f]+)', open(PROJ).read())}
v_proekte = {}
for k, (w, p, x) in reveng.items():
    if (w, p) in kody_ch: v_proekte[(w, p)] = k; assert x in kody_ch[(w, p)]["остатки"] or not kody_ch[(w, p)]["остатки"], k
tab_crc = [{"ширина": w, "многочлен": D_zapis(z["степени"]), "hex_без_старшего": hex(p),
            "остаток_(xorout)": sorted(hex(x) for x in z["остатки"]), "страницы_45.003": sorted(z["страницы"]),
            "libosmocore": [k for k, v in osmo.items() if (v[0], v[1]) == (w, p)], "каталог_проекта": v_proekte.get((w, p))}
           for (w, p), z in sorted(kody_ch.items())]

# --- 3. Выкалывание -------------------------------------------------------------------------------------------
VYK = {}
for m in re.finditer(r"punctured in such a way that the following (\d+) (?:coded )?bits:(.*?)(?:are|is) not transmitted", T.ves, re.S):
    n = int(m.group(1)); body = re.sub(r"=====СТР \d+=====.*?Release 19", "", m.group(2), flags=re.S)
    nums = [int(x) for x in re.findall(r"C\((\d+)\)", body.replace("C(C(", "C("))]
    if len(nums) != n: continue
    lab = re.findall(r"\n\s*((?:TCH|O-TCH)/[A-Z]+[\d.]+):", T.ves[max(0, m.start() - 8000):m.start()])
    VYK[lab[-1]] = (T.stranica_pozicii(m.start()), sorted(nums))
def formula(start_pat, gen, n=None):
    s = T.odna(start_pat)[0]; v = sorted(gen)
    if n is not None: assert len(v) == n
    return s, v
VYK["TCH/F9.6"] = formula(r"\{C\(11\+15j\) for j = 0,1,\.\.\.,31\}", [11 + 15 * j for j in range(32)], 32)
VYK["TCH/F14.4"] = formula(r"\{C\(18\*j\+1\), C\(18\*j\+6\)", [18 * j + d for j in range(32) for d in (1, 6, 11, 15)] + [577, 582, 584, 587], 132)
VYK["CS-2"] = formula(r"\{C\(3\+4j\) for j = 3,4,\.\.\.,146 except", [3 + 4 * j for j in range(3, 147) if j not in (9, 21, 33, 45, 57, 69, 81, 93, 105, 117, 129, 141)])
VYK["CS-3"] = formula(r"\{C\(3\+6j\) and C\(5\+6j\) for j = 2,3,\.\.\.,111\}", [d + 6 * j for j in range(2, 112) for d in (3, 5)])
VYK["TCH/HS"] = None
imena = {"TCH/AFS12.2": "tch_afs_12_2", "TCH/AFS10.2": "tch_afs_10_2", "TCH/AFS7.95": "tch_afs_7_95", "TCH/AFS7.4": "tch_afs_7_4",
         "TCH/AFS6.7": "tch_afs_6_7", "TCH/AFS5.9": "tch_afs_5_9", "TCH/AFS5.15": "tch_afs_5_15", "TCH/AFS4.75": "tch_afs_4_75",
         "TCH/AHS7.95": "tch_ahs_7_95", "TCH/AHS7.4": "tch_ahs_7_4", "TCH/AHS6.7": "tch_ahs_6_7", "TCH/AHS5.9": "tch_ahs_5_9",
         "TCH/AHS5.15": "tch_ahs_5_15", "TCH/AHS4.75": "tch_ahs_4_75", "TCH/F9.6": "tch_f96", "TCH/F14.4": "tch_f144",
         "CS-2": "cs2", "CS-3": "cs3"}
kody = {c.name: c for c in ccg.conv_codes}
sovp_vyk = []
for std, osm in imena.items():
    s, v = VYK[std]; p = [x for x in kody[osm].puncture if x >= 0]
    assert sorted(p) == v, (std, len(p), len(v), sorted(set(p) ^ set(v))[:10])
    sovp_vyk.append(std)
wfs = {k: v for k, v in VYK.items() if v and k.startswith("TCH/WFS")}
assert set(wfs) == {"TCH/WFS12.65", "TCH/WFS8.85", "TCH/WFS6.60"}

# --- 4. Перемежение xCCH (4.1.4) и TCH/FS (3.1.3) — формулы → сверка с libosmocore через ctypes ---------------
s_il_x = [s for s, n, l in T.naiti(r"^4\.1\.4\s*$")][-1]; s_il_f = [s for s, n, l in T.naiti(r"^3\.1\.3\s*$")][-1]
assert T.naiti(r"B = B0 \+ 4n \+ \(k mod 4\)") and T.naiti(r"B = B0 \+ 4n \+ \(k mod 8\)") and T.naiti(r"j\s+= 2\(\(49k\) mod 57\) \+ \(\(k mod 8\) div 4\)")
os.environ["LD_LIBRARY_PATH"] = ""
ctypes.CDLL(os.path.join(LIB, "src/core/.libs/libosmocore.so"), mode=ctypes.RTLD_GLOBAL)
for d in ("gsm", "isdn", "codec"):
    try: ctypes.CDLL(os.path.join(LIB, f"src/{d}/.libs/libosmo{d}.so"), mode=ctypes.RTLD_GLOBAL)
    except OSError: pass
oc = ctypes.CDLL(os.path.join(LIB, "src/coding/.libs/libosmocoding.so"))
def osmo_il(fn, nvyh):
    cb = (ctypes.c_uint8 * 456)(*[0] * 456); ib = (ctypes.c_uint8 * nvyh)()
    karta = {}
    for k in range(456):
        for i in range(456): cb[i] = 1 if i == k else 0
        for i in range(nvyh): ib[i] = 0
        fn(cb, ib)
        pos = [i for i in range(nvyh) if ib[i]]; assert len(pos) == 1; karta[k] = pos[0]
    return karta
x_osmo = osmo_il(oc.gsm0503_xcch_interleave, 4 * 114)
x_std = {k: (k % 4) * 114 + 2 * ((49 * k) % 57) + ((k % 8) // 4) for k in range(456)}
assert x_osmo == x_std
f_osmo = osmo_il(oc.gsm0503_tch_fr_interleave, 8 * 114)
f_std = {k: (k % 8) * 114 + 2 * ((49 * k) % 57) + ((k % 8) // 4) for k in range(456)}
assert f_osmo == f_std
put_il = kod("libosmocore", "src/coding/gsm0503_interleaving.c")

# --- 5. Синхропоследовательности 45.002 ---------------------------------------------------------------------------
tup = [(T2.stranica_pozicii(m.start()), [int(b) for b in re.findall(r"[01]", m.group(1))]) for m in re.finditer(r"\(((?:\s*[01]\s*,){25,}\s*[01]\s*)\)", T2.ves)]
TSC1 = [t for s, t in tup if len(t) == 26][:8]; s_tsc = [s for s, t in tup if len(t) == 26][0]
SCH64 = [t for s, t in tup if len(t) == 64][0]; s_sch = [s for s, t in tup if len(t) == 64][0]
RACH41 = [t for s, t in tup if len(t) == 41]; s_rach = [s for s, t in tup if len(t) == 41][0]
gr = kod("gr-gsm", "include/grgsm/gsm_constants.h")
tg = open(os.path.join(KOREN, gr)).read()
g_train = c_massiv(gr, "train_seq"); g_sync = c_massiv(gr, "SYNC_BITS")
for t in TSC1: assert t[0:5] == t[16:21] and t[21:26] == t[5:10]      # циклическое продолжение 16-битового ядра
gr_tsc = [g_train[26 * i:26 * i + 26] for i in range(8)]
razn_gr = [(i, b) for i in range(8) for b in range(26) if gr_tsc[i][b] != TSC1[i][b]]
# gr-gsm: в TSC 1 бит 20 = 0 вместо 1 (у стандарта ядро циклически продолжено, у gr-gsm — нет); снимок risunki/gsm_tsc_set1.png
assert razn_gr == [(1, 20)], razn_gr
assert g_sync == SCH64
tablica("gsm_45003_45002", {
    "G_prilozhenie_B": {f"G{i}": {"многочлен": D_zapis(G[i]), "восьм_K": okt(G[i], max(G[i]) + 1), "каналы": isp.get(i, "")} for i in range(10)},
    "chetnost_CRC": tab_crc,
    "vykalyvanie": {k: {"страница": v[0], "выколотые_C": v[1], "число": len(v[1])} for k, v in VYK.items() if v},
    "peremezhenie": {"xCCH_4.1.4": "B = B0 + 4n + (k mod 4), j = 2((49k) mod 57) + ((k mod 8) div 4), 456 → 4×114",
                     "TCH/FS_3.1.3": "B = B0 + 4n + (k mod 8), j = та же, 456 → 8 полупачек (диагональное)"},
    "TSC_nabor1_GMSK_26bit": TSC1, "SCH_rasshirennaya_64bit": SCH64, "RACH_sinhro_41bit": RACH41},
    "GSM/GPRS/EDGE: многочлены G0–G9, коды чётности (CRC и Файр) с остатками, выколотые позиции, перемежение, синхропоследовательности",
    [T.ist(s_B, "Annex B"), T.ist(s_fire, "4.1.2 Fire"), T.ist(s_il_x, "4.1.4"), T.ist(s_il_f, "3.1.3"), T2.ist(s_tsc, "Table 5.2.3a"),
     T2.ist(s_sch, "Table 5.2.5-3"), T2.ist(s_rach, "Table 5.2.7-3"), ist_kod(put_ccg, "G0 = poly"), ist_kod(put_par, "gsm0503_fire_crc40"),
     ist_kod(put_il, "gsm0503_xcch_interleave"), ist_kod(gr, "train_seq")],
    f"G0–G7 совпали с libosmocore conv_codes_gsm.py; выколотые позиции {len(sovp_vyk)} кодов ({', '.join(sovp_vyk)}) — совпали поэлементно "
    f"с массивами puncture libosmocore; {len(osmo)} кодов чётности libosmocore найдены среди {len(kody_ch)} многочленов стандарта с тем же остатком; "
    f"перемежение xCCH и TCH/FS по формулам совпало с gsm0503_xcch_interleave/gsm0503_tch_fr_interleave (вызов через ctypes, 456 единичных векторов); "
    f"TSC набор 1 совпал с gr-gsm gsm_constants.h кроме TSC 1 бит 20 (у gr-gsm 0 — ошибка: нарушено циклическое продолжение, стандарт — 1, снимок risunki/gsm_tsc_set1.png); 64-битовая SCH совпала; сборка libosmocore прошла tests/coding (вывод = coding_test.ok)")

# --- 6. Записи каталога ------------------------------------------------------------------------------------------------
def sek(pat): return [s for s, n, l in T.naiti(pat)][-1]
def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "GSM/GPRS/EDGE", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
PR = "libosmocore conv_codes_gsm.py (многочлены, выкалывание), gsm0503_parity.c (CRC), coding_test (тестовые векторы) — сборка прошла"
EST = "есть частично: kod.витерби_n / svyortka (любые K, 1/n, выкалывание — vykalyvanie.py), CRC-3/6/8/10/12/14/16/40 GSM в crc_katalog.py; нет разметки каналов 45.003 (порядок бит, классы, перемежение по пачкам)"
def cc(n): c = kody[n]; return {"вход_бит": c.block_len, "многочлены": [("R" if r == 0 else "") + D_zapis([i for i in range(8) if (p >> i) & 1]) for p, r in c.polys]}
rec("GSM xCCH (SACCH/SDCCH/FACCH/BCCH/PCH/AGCH/CBCH, PDTCH CS-1)", "каскадный: Файр (224,184) + свёрточный K=5 1/2",
    {"блок": "184 бита + 40 бит Файра g(D) = (D23+1)(D17+D3+1), остаток — все единицы; 4 нулевых хвостовых", "свёрточный": "K=5, G0 = 1+D3+D4, G1 = 1+D+D3+D4 (восьм. 23, 33), 228→456",
     "перемежение": "4.1.4 блочно-прямоугольное на 4 пачки (формула в tablicy/gsm_45003_45002.json)", "пачка": "нормальная 148 бит: 3 хвоста, 57 данных, флаг, 26 TSC, флаг, 57 данных, 3 хвоста; флаги hl/hu = 1 для SACCH",
     "Файр": "исправляет пакеты ошибок до 11 бит (4.1.2)", "libosmocore": cc("xcch")},
    "GSM сигнализация, GPRS CS-1 (с 3 битами USF-предкодирования)", [T.ist(s_fire, "4.1.2"), T.ist(sek(r"^4\.1\.3\s*$"), "4.1.3"), T.ist(s_il_x, "4.1.4")],
    "Файр = CRC-40/GSM (RevEng); перемежение и многочлены сверены с libosmocore; " + PR, EST)
rec("GSM TCH/FS и TCH/EFS (полноскоростная речь)", "каскадный: CRC-3 (+CRC-8 EFR) + свёрточный K=5 1/2",
    {"классы": "260 бит: 182 класса 1 (из них 50 класса 1a), 78 класса 2 без защиты", "CRC": "3 бита g = D3+D+1 по 50 битам 1a, остаток 111 (3.1.2.1)",
     "EFR": "предварительно CRC-8 g = D8+D4+D3+D2+1 по 65 важнейшим битам и повтор 4 бит (3.1.1)", "свёрточный": "G0/G1 K=5 1/2, 189 → 378 + 78 = 456",
     "перемежение": "3.1.3 блочно-диагональное на 8 полупачек", "libosmocore": cc("tch_fr")},
    "GSM речь FR / EFR", [T.ist(sek(r"^3\.1\.2\.1\s*$"), "3.1.2.1"), T.ist(sek(r"^3\.1\.1\.1\s*$"), "3.1.1.1 CRC EFR"), T.ist(s_il_f, "3.1.3")], PR, EST)
rec("GSM TCH/HS (полускоростная речь)", "каскадный: CRC-3 + свёрточный K=7 1/3 с выкалыванием",
    {"классы": "112 бит: 95 класса 1 (22 класса 1a) + 17 класса 2", "CRC": "3 бита g = D3+D+1", "свёрточный": "материнский 1/3 K=7: G4, G5, G6; выкалывание (1,0,1) для класса 1 и хвоста, (1,1,1) для 3 бит чётности → 228",
     "перемежение": "3.2.3 диагональное на 4 полупачки по таблице 4", "libosmocore": cc("tch_hr")},
    "GSM речь HR", [T.ist(sek(r"^3\.2\.1\s*$"), "3.2.1"), T.ist(sek(r"^3\.2\.2\s*$"), "3.2.2 матрицы выкалывания"), T.ist(sek(r"^3\.2\.3\s*$"), "3.2.3")], PR, EST)
for std in ["TCH/AFS12.2", "TCH/AFS10.2", "TCH/AFS7.95", "TCH/AFS7.4", "TCH/AFS6.7", "TCH/AFS5.9", "TCH/AFS5.15", "TCH/AFS4.75"]:
    pass
rec("GSM TCH/AFS (AMR полноскоростной, 8 режимов 12.2…4.75)", "каскадный: CRC-6 + рекурсивный систематический свёрточный с выкалыванием",
    {"CRC": "6 бит g = D6+D5+D3+D2+D+1 по битам класса 1a, остаток — единицы", "свёрточный": "рекурсивные систематические: 12.2 — 1/2 G1/G0; 10.2 — 1/3 G1/G3,G2/G3; 7.95 — 1/3 G5/G4,G6/G4 (K=7); 7.4, 6.7, 5.9, 5.15, 4.75 — 1/3…1/5 (см. 3.9.4.4)",
     "выход": "448 бит + 8 бит внутриполосных (кодовые слова режима) = 456", "выкалывание": "списки C(k) для всех 8 режимов → tablicy/gsm_45003_45002.json",
     "SID/ONSET/RATSCCH": "SID_UPDATE: CRC-14 g = D14+D13+D5+D3+D2+1, код 1/4 (G1/G3, G2/G3); RATSCCH — то же", "перемежение": "как TCH/FS (8 полупачек)"},
    "GSM AMR FR", [T.ist(VYK[k][0], k) for k in imena if k.startswith("TCH/AFS")] + [T.ist(sek(r"^3\.9\.4\.4\s*$"), "3.9.4.4")],
    f"выкалывание всех 8 режимов разобрано из текста (число бит сверено со словесным), совпало с libosmocore; {PR}", EST)
rec("GSM TCH/AHS (AMR полускоростной, 6 режимов 7.95…4.75)", "каскадный: CRC-6 + рекурсивный свёрточный с выкалыванием",
    {"CRC": "6 бит (как AFS)", "свёрточный": "3.10.7.4: 7.95 — G1/G0 1/2 …; 5.9 и ниже — K=7 G5/G4, G6/G4 и т.п.", "выход": "224 + 4 внутриполосных = 228",
     "класс 2": "без кодирования", "выкалывание": "tablicy/gsm_45003_45002.json"},
    "GSM AMR HR", [T.ist(VYK[k][0], k) for k in imena if k.startswith("TCH/AHS")], f"6 режимов совпали с libosmocore; {PR}", EST)
rec("GSM TCH/WFS (AMR-WB 12.65/8.85/6.60)", "каскадный: CRC-6 + рекурсивный свёрточный с выкалыванием",
    {"выкалывание": "списки 78/113/128 бит → tablicy/gsm_45003_45002.json", "свёрточный": "3.14.4.4 (G1/G0; G2/G1, G3/G1 …)"},
    "GSM AMR-WB полноскоростной", [T.ist(v[0], k) for k, v in wfs.items()],
    "списки разобраны из текста с проверкой числа; в libosmocore нет TCH/WFS — второй источник не найден", "нет (кроме общего витерби)")
rec("GSM данные TCH/F9.6, F4.8, F2.4, H4.8, H2.4, F14.4", "свёрточный K=5 (1/2 … 1/6) с выкалыванием",
    {"F9.6/H4.8": "240 бит (4×60) + 4 хвоста, 1/2 G0/G1, выколото 32 C(11+15j) → 456; перемежение 19 пачек (3.3.4)",
     "F14.4": "290+4, 1/2, выколото 132 → 456", "F4.8": "1/3 G1,G2,G3 (60+16 нулей)", "F2.4": "1/6 G1 G2 G3 G1 G2 G3", "H2.4": "1/3",
     "libosmocore": {k: cc(k) for k in ("tch_f96", "tch_f144", "tch_f48", "tch_f24", "tch_h24")}},
    "GSM CSD (V.110 поверх TCH, см. v110.py/trau.py проекта)", [T.ist(VYK["TCH/F9.6"][0], "3.3.3"), T.ist(VYK["TCH/F14.4"][0], "3.8.3")], PR, EST)
rec("GSM ECSD E-TCH/F28.8, F32.0, F43.2 (8-PSK)", "каскадный: РС (для 28.8) + свёрточный K=7 1/3 G4,G7,G5 с выкалыванием",
    {"F28.8": "RS над GF(2^8)? — см. 3.11.2.2 (внешний РС), свёрточный 1/3 K=7, выколоты 4 бита", "F43.2": "выкалывание c(2+8(k−1)), c(4+16(k−1)), c(6+32(k−1))",
     "перемежение": "по 4 пачкам 8-PSK (3.11.4)"},
    "GSM ECSD (EDGE CSD)", [T.ist(sek(r"^3\.11\.2\.2\s*$"), "3.11.2.2 Reed Solomon encoder"), T.ist(sek(r"^3\.13\.2\.2\s*$"), "3.13.2.2")],
    "описание по тексту; второго открытого источника (кодера ECSD) не найдено", "нет")
rec("GSM SCH (синхроканал)", "каскадный: CRC-10 + свёрточный K=5 1/2",
    {"блок": "25 бит + 10 бит g = D10+D8+D6+D5+D4+D2+1 (остаток — единицы) + 4 хвоста → 78", "пачка": "SB: 39 + 64 расширенная обучающая (tablicy) + 39",
     "libosmocore": cc("sch")}, "GSM BCCH-синхронизация (BSIC, номер кадра)", [T.ist(sek(r"^4\.7\s*$"), "4.7"), T2.ist(s_sch, "Table 5.2.5-3")], PR + "; 64 бита SCH = SYNC_BITS gr-gsm", EST)
rec("GSM RACH (8 и 11 бит, EGPRS PRACH)", "каскадный: CRC-6 ⊕ BSIC + свёрточный K=5 1/2",
    {"8 бит": "6 бит чётности g = D6+D5+D3+D2+D+1, складываются с BSIC, 4 хвоста → 36 (4.6)", "11 бит": "то же, выколоты c(0),c(2),c(5),c(37),c(39),c(41) → 36",
     "пачка": "доступа: 8 хвостовых, 41 синхро (tablicy: RACH_sinhro_41bit, 3 варианта TS0…TS2 и др.), 36 данных, 3 хвоста", "libosmocore": {k: cc(k) for k in ("rach", "rach_ext")}},
    "GSM/GPRS/EGPRS случайный доступ", [T.ist(sek(r"^4\.6\s*$"), "4.6"), T2.ist(s_rach, "41-битовые последовательности")], PR, EST)
rec("GPRS PDTCH CS-1…CS-4", "каскадный: CRC-40 Файр (CS-1) / CRC-16 (CS-2…4) + свёрточный K=5 1/2 с выкалыванием (CS-4 без кода)",
    {"CS-1": "как xCCH (184+40+4)", "CS-2": "271 бит (USF 6 предкод.) + CRC-16 g = D16+D12+D5+1 (остаток единицы) + 4 хвоста → 588 → выколото до 456 (C(3+4j) кроме …)",
     "CS-3": "315 → 676 → выколото C(3+6j), C(5+6j) → 456", "CS-4": "431 бит + CRC-16, без свёрточного, USF 12 бит", "USF предкодирование": "3 бита → 6 (CS-2/3) или 12 (CS-4) по табл.",
     "libosmocore": {k: cc(k) for k in ("cs2", "cs3")}},
    "GPRS пакетные данные", [T.ist(VYK["CS-2"][0], "5.1.2 CS-2"), T.ist(VYK["CS-3"][0], "5.1.3 CS-3"), T.ist(sek(r"^5\.1\.4\s*$"), "5.1.4 CS-4")],
    "выкалывание CS-2/CS-3 по формулам текста совпало с libosmocore; " + PR, EST)
rec("EDGE PDTCH MCS-1…MCS-9 (EGPRS)", "каскадный: CRC-8 (заголовок) и CRC-12 (данные) + свёрточный K=7 1/3 (G4,G7,G5) с выкалыванием P1/P2/P3",
    {"заголовок": "CRC-8 g = D8+D6+D3+1 (остаток — единицы), кольцевой (tail-biting) свёрточный", "данные": "CRC-12 g = D12+D11+D10+D8+D5+D4+1, 6 хвостов",
     "выкалывание": "схемы P1–P3 (MCS-1..9) — формулы/таблицы 5.1.5…5.1.13", "модуляция": "GMSK (MCS-1..4), 8-PSK (MCS-5..9)",
     "перемежение": "MCS-1..6 — по 4 пачкам, MCS-7..9 — два блока RLC по 2 пачкам", "libosmocore": {k: cc(k) for k in ("mcs1", "mcs5", "mcs7", "mcs9")}},
    "EDGE / EGPRS", [T.ist(sek(r"^5\.1\.5\.1\.3\s*$"), "5.1.5.1.3 MCS-1 header"), T.ist(sek(r"^5\.1\.13\s*$"), "5.1.13 MCS-9")],
    PR + "; кодеры MCS-1..9 входят в coding_test libosmocore", EST)
rec("EGPRS2 (UAS/UBS/DAS/DBS) и EC-GSM-IoT", "свёрточный K=7 (UAS/UBS) / турбо G8,G9 (DAS/DBS) / РМ-подобные для EC",
    {"турбо": "G8 = 1+D2+D3, G9 = 1+D+D3 (приложение B) — 8 состояний как UMTS/LTE, перемежитель 45.003 5.1a", "EC-GSM-IoT": "EC-SCH/EC-BCCH/EC-CCCH/EC-PDTCH (5.5…)"},
    "EGPRS2-A/B (32QAM/16QAM), EC-GSM-IoT", [T.ist(s_B, "Annex B: G8, G9")], "по тексту приложения B; кодер в открытом коде не найден", "нет")
rec("GSM синхропоследовательности: TSC (8×26), SCH 64, RACH 41", "синхрослово",
    {"TSC набор 1 GMSK": "tablicy/gsm_45003_45002.json (8 последовательностей 26 бит)", "SCH": "64 бита расширенной обучающей", "RACH": "41 бит (варианты TS0/TS1/TS2 и EC)"},
    "поиск пачек GSM в битовом потоке", [T2.ist(s_tsc, "Table 5.2.3a"), T2.ist(s_sch, "Table 5.2.5-3"), T2.ist(s_rach, "5.2.7")],
    "разобраны из текста 45.002 (кортежи), у всех TSC проверено циклическое продолжение ядра; SCH совпала с gr-gsm SYNC_BITS; TSC 0,2–7 совпали с gr-gsm train_seq, в TSC 1 у gr-gsm ошибка в бите 20",
    "есть поиск синхрослов (sinhro.py); наборов GSM нет — добавить таблицу")
if __name__ == "__main__":
    print(len(ZAPISI), "записей; CRC", len(kody_ch), "выкалываний", len([v for v in VYK.values() if v]), "сверено", len(sovp_vyk))
