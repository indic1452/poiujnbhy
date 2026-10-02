"""DVB-NGH (DVB BlueBook A160, = проект ETSI EN 303 105, 2012-11): LDPC 16K (R = 3/15…11/15, прил. E) и 4K (R = 1/5, 1/2 — прил. F).
Проверка: все 9 кодов 16K совпали с таблицами DVB-S2/S2X/T2 проекта (data/ldpc_dvb.json — из открытых реализаций);
4K R = 1/5 — полный ранг H; 4K R = 1/2 (с IR-расширением до 8640) — диапазоны адресов и число групп."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

N = Tekst("istochniki/dvb_ngh/A160_DVB-NGH_Spec.pdf"); ZAPISI = []
SL = "DVB-NGH (Next Generation Handheld)"
PRO = json.load(open("/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_dvb.json"))
kol = r"=====СТР \d+=====\s*\nETSI\s*\nDraft ETSI EN 303 105 V1\.1\.1 \(2012-11\)\s*\n\d+"
def tabl(ot, do):
    kus, s = N.kusok(ot, do, posl=True)
    kus = re.sub(kol, "\n", kus)
    rows = [[int(v) for v in l.split()] for l in kus.split("\n") if re.fullmatch(r"\s*\d+(?:\s+\d+)*\s*", l) and l.strip()]
    return rows, s
SOVP = {}; ist = []
spisok = [(1, "3/15"), (2, "4/15"), (3, "5/15"), (4, "6/15"), (5, "7/15"), (6, "8/15"), (7, "9/15"), (8, "10/15"), (9, "11/15")]
TAB = {}
for i, r in spisok:
    do = rf"Table E\.{i + 1}:" if i < 9 else r"Annex F"
    rows, s = tabl(rf"Table E\.{i}: Rate {r} \(Nldpc = 16 200\)", do)
    k = int(r.split("/")[0]) * 16200 // 15
    assert len(rows) == k // 360, (r, len(rows), k)
    assert all(0 <= v < 16200 - k for row in rows for v in row)
    kand = [n for n, v in PRO.items() if isinstance(v, dict) and v.get("n") == 16200 and v.get("k") == k]
    sovp = [n for n in kand if PRO[n]["строки"] == rows]
    assert sovp, (r, kand)
    SOVP[r] = sovp; TAB[r] = rows; ist.append(N.ist(s, f"табл. E.{i}"))
rows15, s15 = tabl(r"Table F\.1: Rate 1/5 \(Nldpc = 4320\)", r"Table F\.2:")
rows12, s12 = tabl(r"Table F\.2: Rate 1/2 \(Nldpc = 4320\)", r"Annex G|=====СТР")
assert len(rows15) == 864 // 72 and all(v < 3456 for r in rows15 for v in r)
assert len(rows12) == 2160 // 72 and all(v < 8640 - 2160 for r in rows12 for v in r)
# H для 4K R = 1/5: бит m группы g → чётности (x + (m mod 72)·Q) mod (N−K), Q = 48; накопитель (двойная диагональ)
Nn, K, Q = 4320, 864, 48; M = Nn - K
stroki = [0] * M
for g, row in enumerate(rows15):
    for m in range(72):
        i = g * 72 + m
        for x in row:
            stroki[(x + m * Q) % M] ^= 1 << (Nn - 1 - i)
for j in range(M):
    stroki[j] ^= 1 << (Nn - 1 - (K + j))
    if j: stroki[j] ^= 1 << (Nn - 1 - (K + j - 1))
assert rang_gf2(stroki) == M
s_q = N.odna(r"accumulator corresponding to the first bit i0, and Qldpc is 48")[0]
s_ir = N.odna(r"Nldpc\+ MIR where Nldpc\(=4320\) is the length of a 4K LDPC codeword and MIR \(=4320\) is the number of IR")[0]
js = tablica("dvbngh_ldpc", {"16200": TAB, "4320_1_5": rows15, "4320_1_2_IR": rows12, "совпадения_с_проектом": SOVP},
   "DVB-NGH: адреса накопителей чётности LDPC 16K (прил. E) и 4K (прил. F)", ist + [N.ist(s15, "табл. F.1"), N.ist(s12, "табл. F.2")],
   "16K: поэлементно = таблицам DVB-S2/S2X/T2 проекта; 4K 1/5: ранг H = 3456; 4K 1/2: 30 групп по 72, адреса < 6480")
ZAPISI.append(Z("DVB-NGH LDPC 16K: R = 3/15…11/15 — те же коды, что DVB-S2/S2X/T2 16K (1/4, 4/15, 1/3, 2/5, 7/15, 8/15, 3/5, 2/3, 3/4)", SL, "LDPC",
  {"совпадения": SOVP, "таблица": js, "BCH": "внешний БЧХ t = 12, многочлены табл. 5 (как DVB-T2 16K)"},
  "DVB-NGH (не внедрён массово; решения вошли в ATSC 3.0/DVB-T2 Lite)", ist, "каждая из 9 таблиц прил. E поэлементно совпала с таблицей проекта с тем же k (источники проекта — открытые реализации DVB-S2/S2X/T2)",
  "есть: data/ldpc_dvb.json — все 9 (" + ", ".join(sorted({v for vs in SOVP.values() for v in vs})) + ")"))
ZAPISI.append(Z("DVB-NGH LDPC 4K (Nldpc = 4320): R = 1/5 (Q = 48, группы по 72) для L1-PRE и R = 1/2 с IR-расширением до 8640 (Q1 = 30, Q2 = 60) для L1-POST", SL, "LDPC",
  {"1/5": "K = 864, 12 строк табл. F.1, адрес (x + (m mod 72)·48) mod 3456, накопитель", "1/2 IR": "K = 2160, 30 строк табл. F.2, N = 4320 + MIR 4320 (инкрементная избыточность)", "таблица": js},
  "DVB-NGH: защита сигнализации L1", [N.ist(s15, "табл. F.1"), N.ist(s12, "табл. F.2"), N.ist(s_q, "Qldpc = 48"), N.ist(s_ir, "IR")],
  "1/5: развёрнутая H (3456 × 4320) имеет полный ранг; 1/2: число групп и диапазон адресов согласованы с N2 − K = 6480; открытой реализации для сверки не найдено",
  "нет (коды 4K NGH в проекте отсутствуют; можно добавить из tablicy/dvbngh_ldpc.json)"))
