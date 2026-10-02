"""LTE (3GPP TS 36.212): таблицы из текста стандарта с проверками и сверкой с srsRAN_4G."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/lte/ts_136212v190300p.pdf")
ZAPISI = []

# --- CRC (5.1.1) --------------------------------------------------------------
def crc_iz_teksta(imya):
    s, n, l = T.odna(r"g" + imya + r"\(D\)\s*=\s*\[")
    stepeni = [0 if t == "1" else 1 if t == "D" else int(t[1:]) for t in re.findall(r"D\d*|\b1\b", l.split("=")[1].split("]")[0])]
    return s, stepeni
CRC = {}
for imya in ("CRC24A", "CRC24B", "CRC16", "CRC8"):
    s, st = crc_iz_teksta(imya)
    CRC[imya] = (s, st, mnogochlen(st))
assert CRC["CRC24A"][2] == 0x1864CFB and CRC["CRC24B"][2] == 0x1800063 and CRC["CRC16"][2] == 0x11021 and CRC["CRC8"][2] == 0x19B
srs_crc = kod("srsRAN_4G", "lib/include/srsran/phy/common/phy_common.h")
t = open(os.path.join(KOREN, srs_crc)).read()
for imya, znach in (("CRC24A", 0x1864CFB), ("CRC24B", 0x1800063), ("CRC16", 0x11021), ("CRC8", 0x19B)):
    m = re.search(r"#define\s+SRSRAN_LTE_" + imya + r"\s+(0[xX][0-9A-Fa-f]+)", t)
    assert m and int(m.group(1), 16) == znach, imya

# --- QPP (табл. 5.1.3-3) -------------------------------------------------------
kus, s_qpp = T.kusok(r"Table 5\.1\.3-3: Turbo code internal interleaver parameters", r"\n5\.1\.4 ?\n")
chisla = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", re.sub(r"=====СТР \d+=====", "", kus))]
# в тексте четвёрки i, K, f1, f2 идут построчно; служебные номера страниц/версии выкидываем по согласованности
qpp = {}
i = 0
while i + 3 < len(chisla):
    a, K, f1, f2 = chisla[i:i + 4]
    if 1 <= a <= 188 and a not in qpp and 40 <= K <= 6144 and K % 8 == 0 and f1 < K and f2 < K:
        qpp[a] = (K, f1, f2); i += 4
    else:
        i += 1
assert sorted(qpp) == list(range(1, 189)), (len(qpp), sorted(set(range(1, 189)) - set(qpp))[:10])
Kz = [qpp[a][0] for a in range(1, 189)]
ozhid = list(range(40, 513, 8)) + list(range(528, 1025, 16)) + list(range(1056, 2049, 32)) + list(range(2112, 6145, 64))
assert Kz == ozhid
for a, (K, f1, f2) in qpp.items():
    assert len({(f1 * x + f2 * x * x) % K for x in range(K)}) == K, f"QPP не перестановка при K={K}"
srs_qpp = kod("srsRAN_4G", "lib/src/phy/fec/turbo/tc_interl_lte.c")
assert c_massiv(srs_qpp, "f1_list") == [qpp[a][1] for a in range(1, 189)]
assert c_massiv(srs_qpp, "f2_list") == [qpp[a][2] for a in range(1, 189)]
tab_qpp = tablica("lte_qpp_36212_t5.1.3-3", [{"i": a, "K": qpp[a][0], "f1": qpp[a][1], "f2": qpp[a][2]} for a in range(1, 189)],
    "Параметры QPP-перемежителя турбокода LTE: Π(i) = (f1·i + f2·i²) mod K",
    [T.ist(s_qpp, "Table 5.1.3-3"), ist_kod(srs_qpp, r"f1_list\[", "f1_list/f2_list")],
    "188 строк разобраны из текста PDF; K совпали с рядом 40..512/8, 528..1024/16, 1056..2048/32, 2112..6144/64; "
    "для каждого K перестановка биективна; f1_list и f2_list srsRAN_4G совпали поэлементно")

# --- Перестановки столбцов подблочного перемежителя (5.1.4-1, 5.1.4-2) ----------
def perm(tab):
    kus, s = T.kusok(r"Table " + re.escape(tab) + r" Inter-column permutation pattern", r"5\.1\.4\.\d\.\d")
    m = re.search(r"<\s*([\d,\s]+?)\s*>", kus.replace("\n", " ")); p = [int(x) for x in m.group(1).split(",")]
    assert sorted(p) == list(range(32)); return p, s
P_TC, s_tc = perm("5.1.4-1"); P_CC, s_cc = perm("5.1.4-2")
srs_rmt = kod("srsRAN_4G", "lib/src/phy/fec/turbo/rm_turbo.c"); srs_rmc = kod("srsRAN_4G", "lib/src/phy/fec/turbo/rm_conv.c")
assert c_massiv(srs_rmt, "RM_PERM_TC") == P_TC and c_massiv(srs_rmc, "RM_PERM_CC") == P_CC
assert P_TC == [int(f"{x:05b}"[::-1], 2) for x in range(32)]      # обращение бит 5-битового номера
tab_perm = tablica("lte_podblok_perm_36212", {"turbo_5.1.4-1": P_TC, "svertka_5.1.4-2": P_CC},
    "Межстолбцовые перестановки подблочного перемежителя (32 столбца) при согласовании скорости LTE",
    [T.ist(s_tc, "Table 5.1.4-1"), T.ist(s_cc, "Table 5.1.4-2"), ist_kod(srs_rmt, "RM_PERM_TC"), ist_kod(srs_rmc, "RM_PERM_CC")],
    "перестановки 0..31; для турбо — обращение 5 бит номера; совпали с RM_PERM_TC/RM_PERM_CC srsRAN_4G")

# --- Базисы блочных кодов (32,O) и (20,A) ---------------------------------------
def bazis(tab, stolbcov, strok):
    kus, s = T.kusok(r"Table " + re.escape(tab) + r": Basis sequences", r"(?:5\.2\.\d\.\d|Table 5\.2\.\d)")
    kus = re.sub(r"=====СТР \d+=====.*?Release 19", "", kus, flags=re.S)
    ch = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]
    rows = {}
    i = 0
    while i < len(ch) and len(rows) < strok:
        if ch[i] == len(rows) and all(v in (0, 1) for v in ch[i + 1:i + 1 + stolbcov]):
            rows[ch[i]] = ch[i + 1:i + 1 + stolbcov]; i += 1 + stolbcov
        else:
            i += 1
    assert len(rows) == strok, (tab, len(rows))
    return [rows[r] for r in range(strok)], s
M32, s32 = bazis("5.2.2.6.4-1", 11, 32)
M20, s20 = bazis("5.2.3.3-1", 13, 20)
srs_blk = kod("srsRAN_4G", "lib/src/phy/fec/block/block.c")
srs_b = c_massiv(srs_blk, "M_basis_seq_b")
assert srs_b == [sum(b << n for n, b in enumerate(r)) for r in M32], "srsRAN M_basis_seq_b не совпал"
def min_ves(M, k):
    w = 99
    for x in range(1, 1 << k):
        c = [sum(M[i][n] & (x >> n) & 1 for n in range(k)) & 1 for i in range(len(M))]
        w = min(w, sum(c))
    return w
d32 = min_ves(M32, 11); d20 = min_ves(M20, 13)
tab_rm = tablica("lte_bazisy_rm_36212", {"M32x11_t5.2.2.6.4-1": M32, "M20x13_t5.2.3.3-1": M20, "d_min": {"(32,11)": d32, "(20,13)": d20}},
    "Базисные последовательности блочных кодов УКИ LTE (подкоды Рида — Маллера): b_i = Σ a_n·M_i,n mod 2",
    [T.ist(s32, "Table 5.2.2.6.4-1"), T.ist(s20, "Table 5.2.3.3-1"), ist_kod(srs_blk, "M_basis_seq_b")],
    f"32×11 и 20×13 разобраны из текста с проверкой номеров строк; (32,11) совпал с M_basis_seq_b srsRAN_4G (Mi,0 — младший бит слова); "
    f"минимальное расстояние полных кодов: (32,11) d={d32}, (20,13) d={d20}")

# --- CFI (табл. 5.3.4-1) ---------------------------------------------------------
kus, s_cfi = T.kusok(r"Table 5\.3\.4-1: CFI code words", r"5\.3\.5")
cfi = [[int(b) for b in m.split(",")] for m in re.findall(r"<([01,]{63})>", kus.replace(" ", ""))]
assert len(cfi) == 4 and all(len(c) == 32 for c in cfi)
tab_cfi = tablica("lte_cfi_36212_t5.3.4-1", {"CFI1": cfi[0], "CFI2": cfi[1], "CFI3": cfi[2], "CFI4_rezerv": cfi[3]},
    "Кодовые слова CFI (PCFICH), 32 бита", [T.ist(s_cfi, "Table 5.3.4-1")],
    "4 слова по 32 бита разобраны из текста; слова 1–3 — периодические с периодом 3 (проверено программно)")
for c in cfi[:3]: assert all(c[i] == c[i % 3] for i in range(32))

s_tbcc = T.odna(r"tail biting convolutional code with constraint length 7")[0]
s_turbo = T.odna(r"g0\(D\) = 1 \+ D2 \+ D3")[0]
s_cb = T.odna(r"Z = 6144|maximum code block size Z")[0]

L = "https://www.etsi.org/deliver/etsi_ts/136200_136299/136212/19.03.00_60/ts_136212v190300p.pdf"
ZAPISI += [
 {"имя": "LTE CRC24A/CRC24B/CRC16/CRC8", "семейство": "LTE", "область": "mobilnaya", "вид": "CRC-подобный",
  "параметры": {k: {"многочлен": D_zapis(v[1]), "hex_с_старшим": hex(v[2])} for k, v in CRC.items()} | {
      "порядок бит": "старший первым, начальное 0, без инверсии (5.1.1)", "применение": "24A — транспортный блок; 24B — кодовые блоки; 16 — PBCH/DCI (маскируется RNTI/антеннами); 8 — CQI на PUSCH"},
  "где применяется": "LTE, NB-IoT, LTE-M (DL-SCH, UL-SCH, PBCH, DCI, UCI)",
  "источник": [T.ist(CRC[k][0], "5.1.1 " + k) for k in CRC] + [ist_kod(srs_crc, "SRSRAN_LTE_CRC24A")],
  "проверка": "многочлены прочитаны из текста; совпали с SRSRAN_LTE_CRC* srsRAN_4G; в каталоге CRC проекта есть CRC-24/LTE-A, CRC-24/LTE-B, CRC-8/LTE",
  "сложность внедрения": "есть в проекте: crc_katalog.py (CRC-24/LTE-A/B, CRC-8/LTE, CRC-16 X25-вида как CRC-16/XMODEM); маски RNTI на CRC16 DCI — нет"},
 {"имя": "LTE TBCC 1/3 K=7 (133,171,165)", "семейство": "LTE", "область": "mobilnaya", "вид": "свёрточный (кольцевой, tail-biting)",
  "параметры": {"K": 7, "скорость": "1/3", "многочлены": "G0=133, G1=171, G2=165 (восьмерично, как в стандарте; старший разряд — текущий вход)",
                "хвост": "кольцевой: начальное состояние = последние 6 информационных бит (s_i = c_(K−1−i))",
                "согласование скорости": "5.1.4.2: три подблочных перемежителя по 32 столбца (перестановка tablicy/lte_podblok_perm_36212.json, svertka), кольцевой буфер",
                "порядок выхода": "d(0) — G0, d(1) — G1, d(2) — G2"},
  "где применяется": "LTE PBCH, PDCCH (DCI), PUSCH UCI (CQI > 11 бит), NB-IoT NPBCH/NPDCCH, SL-BCH",
  "источник": [T.ist(s_tbcc, "5.1.3.1, рис. 5.1.3-1 (многочлены — на рисунке)")],
  "проверка": "многочлены сняты с рисунка 5.1.3-1 (растр страницы 15 просмотрен: «G0 = 133 (octal)», «G1 = 171», «G2 = 165»); в проекте тот же код — «кольцо» svyortka.py (задача 65)",
  "сложность внедрения": "есть в проекте: svyortka.кодировать_кольцо/витерби_кольцо; нет согласования скорости 5.1.4.2 и дескремблирования PBCH/PDCCH"},
 {"имя": "LTE турбокод PCCC 1/3 с QPP", "семейство": "LTE", "область": "mobilnaya", "вид": "турбо PCCC",
  "параметры": {"составляющие": "два 8-состояния RSC, G(D) = [1, g1/g0], g0 = 1 + D2 + D3 (13 восьм.), g1 = 1 + D + D3 (15 восьм.)",
                "перемежитель": "QPP Π(i) = (f1·i + f2·i²) mod K, 188 размеров K = 40…6144 — tablicy/lte_qpp_36212_t5.1.3-3.json",
                "хвост": "12 бит завершения (по 3 систематических и проверочных на каждый кодер, 5.1.3.2.2)",
                "начальное состояние": "нули", "сегментация": "Z = 6144, CRC24B на каждый блок при C > 1, заполнители F (5.1.2)",
                "согласование скорости": "5.1.4.1: подблочный перемежитель 32 столбца (tablicy/lte_podblok_perm_36212.json, turbo), кольцевой буфер, rv 0–3, N_cb"},
  "где применяется": "LTE DL-SCH, UL-SCH, PCH, MCH; NB-IoT NPDSCH/NPUSCH; DECT-2020 NR (TS 103 636-3 ссылается на тот же код)",
  "источник": [T.ist(s_turbo, "5.1.3.2"), T.ist(s_qpp, "Table 5.1.3-3"), T.ist(s_tc, "Table 5.1.4-1"), ist_kod(srs_qpp, r"f1_list\[")],
  "проверка": "таблица QPP разобрана программно (188 строк), каждая перестановка биективна, совпала с srsRAN tc_interl_lte.c",
  "сложность внедрения": "частично есть: turbo.py (BCJR-декодер PCCC, поиск QPP f1,f2 вслепую — turbo.qpp); нет таблицы 188 размеров, сегментации и согласования скорости — таблица готова в tablicy/"},
 {"имя": "LTE блочный код (32,O) для УКИ", "семейство": "LTE", "область": "mobilnaya", "вид": "РМ (подкод Рида — Маллера)",
  "параметры": {"n": 32, "k": "O ≤ 11", "базис": "tablicy/lte_bazisy_rm_36212.json (M32x11)", "d_min полного (32,11)": d32,
                "то же в UMTS": "TFCI (32,10) 25.212 4.3.3 — те же принципы", "в NR": "38.212 табл. 5.3.3.3-1"},
  "где применяется": "LTE CQI/PMI/RI на PUSCH, HARQ-ACK/RI 3–11 бит, PUCCH формат 3",
  "источник": [T.ist(s32, "Table 5.2.2.6.4-1"), ist_kod(srs_blk, "M_basis_seq_b")],
  "проверка": "совпадение с srsRAN block.c; d_min посчитан перебором 2^11",
  "сложность внедрения": "нет; простой — кодирование матрицей 11×32, декодирование перебором/быстрым преобразованием Адамара (kod.уолш есть в проекте)"},
 {"имя": "LTE блочный код (20,A) для PUCCH", "семейство": "LTE", "область": "mobilnaya", "вид": "РМ (подкод Рида — Маллера)",
  "параметры": {"n": 20, "k": "A ≤ 13", "базис": "tablicy/lte_bazisy_rm_36212.json (M20x13)", "d_min полного (20,13)": d20},
  "где применяется": "LTE CQI/PMI на PUCCH форматы 2/2a/2b",
  "источник": [T.ist(s20, "Table 5.2.3.3-1")],
  "проверка": "20 строк × 13 разобраны с проверкой номеров строк и значений 0/1; расстояние посчитано перебором",
  "сложность внедрения": "нет; простой (то же, что (32,O))"},
 {"имя": "LTE CFI (32,2)", "семейство": "LTE", "область": "mobilnaya", "вид": "повторение/блочный",
  "параметры": {"кодовые слова": "tablicy/lte_cfi_36212_t5.3.4-1.json", "n": 32, "k": 2},
  "где применяется": "LTE PCFICH", "источник": [T.ist(s_cfi, "Table 5.3.4-1")],
  "проверка": "слова разобраны из текста; периодичность 3 проверена", "сложность внедрения": "нет; тривиально (сравнение с 3 словами)"},
]
for z in ZAPISI:
    pass  # url_стандарта убран при проверке: URL стандарта уже есть в каждом источнике записи
if __name__ == "__main__":
    print(len(ZAPISI), "записей; d32", d32, "d20", d20)
