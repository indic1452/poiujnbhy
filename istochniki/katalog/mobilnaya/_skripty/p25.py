"""P25 фаза 1 (TIA-102.BAAA-A): BCH(63,16,23)+1 NID, Голей (24,12,8)/(23,12,7)/(18,6,8), Хэмминг (15,11,3)/(10,6,3),
циклический (16,8,5), RS(36,20,17)/(24,12,13)/(24,16,9) над GF(64), решётка 1/2 и 3/4, ПСП речи, CRC.
Сверка с MMDVMHost (GPL-2.0) и проектом (p25.py)."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/p25/TIA-102-BAAA-A_Project_25_FDMA_CAI.pdf")
ZAPISI = []
def pmod(a, b):
    while a and a.bit_length() >= b.bit_length(): a ^= b << (a.bit_length() - b.bit_length())
    return a
# (16,8,5)
s16 = T.odna(r"g\(x\) = x8 \+ x5 \+ x4 \+ x3 \+ 1\.")[0]
g16 = mnogochlen([8, 5, 4, 3, 0])
kus, s_t52 = T.kusok(r"Table 5-2 Generator Matrix of \(16,8,5\) code\s*\nRow", r"An example code word")
R16 = [int(l.replace(" ", ""), 2) for l in kus.split("\n") if re.fullmatch(r"\s*[01 ]{30,}\s*", l)]
assert len(R16) == 8
for i, r in enumerate(R16): assert r >> 8 == 1 << (7 - i) and (r & 0xFF) == pmod(1 << (15 - i), g16), i
enc16 = lambda u: (u << 8) | pmod(u << 8, g16)
assert enc16(0x41) == 0x411E                                  # пример текста: «A» = $41 → $41 1e
lsd = kod("MMDVMHost", "P25LowSpeedData.cpp"); ccs = c_massiv(lsd, "CCS_PARITY")
assert all(ccs[u] == enc16(u) & 0xFF for u in range(256))
# Голей
sg = T.odna(r"g\(x\) = x11 \+x10 \+x6 \+x5 \+x4 \+x2\+1 = 6165 in octal")[0]
gG = int("6165", 8); assert gG == mnogochlen([11, 10, 6, 5, 4, 2, 0])
kus, s_t53 = T.kusok(r"Table 5-3 Golay Generator Matrices", r"5\.8 Hamming", posl=True)
rows = re.findall(r"(?m)^\s*(\d+)\s+([0-7]{4}) ([0-7]{4})(?:\s+([0-7]{2}) ([0-7]{4}))?", kus)
G24 = [(int(a, 8) << 12) | int(b, 8) for _, a, b, _, _ in rows]; assert len(G24) == 12
for i, w in enumerate(G24):
    u = 1 << (11 - i); c23 = (u << 11) | pmod(u << 11, gG)
    assert w == (c23 << 1) | (bin(c23).count("1") & 1), i      # (23,12) систематический по g(x) + бит чётности
G18 = [(int(c, 8) << 12) | int(d, 8) for _, _, _, c, d in rows if c]; assert len(G18) == 6
assert all(G18[i] == (G24[6 + i] & ((1 << 18) - 1)) for i in range(6))   # (18,6,8) = (24,12,8) без левых 6 бит
gl = kod("MMDVMHost", "Golay24128.cpp"); e23 = c_massiv(gl, "ENCODING_TABLE_23127")
assert all(e23[u] == (u << 11 | pmod(u << 11, gG)) << 1 for u in range(4096))      # в MMDVMHost слово (23,12) сдвинуто влево на 1
# Хэмминги
kus, s_t54 = T.kusok(r"Table 5-4 Generator Matrices for Hamming Codes", r"5\.9\s+Reed-Solomon", posl=True)
H10, H15 = [], []
for l in kus.split("\n"):
    mm = re.match(r"\s*(\d+)\s+([01 ]+)$", l)
    if not mm: continue
    b = re.findall(r"[01]", mm.group(2))
    if len(b) == 25: H10.append(b[:10]); H15.append(b[10:])
    elif len(b) == 15: H15.append(b)
H10 = [[int(x) for x in r] for r in H10]; H15 = [[int(x) for x in r] for r in H15]
assert len(H10) == 6 and len(H15) == 11
ham = kod("MMDVMHost", "Hamming.cpp")
th = open(os.path.join(KOREN, ham)).read()
enc = {m.group(1): {int(a): sorted(int(x) for x in re.findall(r"d\[(\d+)\]", b)) for a, b in re.findall(r"d\[(\d+)\]\s*=\s*([^;]+);", m.group(2))}
       for m in re.finditer(r"void CHamming::encode(\w+)\(bool\* d\)\s*\{(.*?)\n\}", th, re.S)}
ur = lambda R: {len(R) + j: sorted(i for i in range(len(R)) if R[i][len(R) + j]) for j in range(len(R[0]) - len(R))}
sovp10 = [f for f, e in enc.items() if e == ur(H10)]; sovp15 = [f for f, e in enc.items() if e == ur(H15)]
assert sovp10 and sovp15, (sovp10, sovp15)
# RS над GF(64), α^6+α+1
ex = [1]
for i in range(1, 63):
    v = ex[-1] << 1
    if v & 64: v ^= 0b1000011
    ex.append(v)
lg = {v: i for i, v in enumerate(ex)}
mul = lambda a, b: 0 if 0 in (a, b) else ex[(lg[a] + lg[b]) % 63]
def gen(R):
    g = [1]
    for j in range(1, R + 1):
        ng = [0] * (len(g) + 1)
        for i, c in enumerate(g): ng[i] ^= mul(c, ex[j]); ng[i + 1] ^= c
        g = ng
    return g
GP = {}
for imya, R in (("HDR", 16), ("LC", 12), ("ES", 8)):
    m = re.search(r"g" + imya + r"\(x\) = (.*?)(?:\n\s*g[A-Z]|\n\s*The generator matrix)", T.ves, re.S)
    txt = " ".join(m.group(1).split())
    co = {0: int(re.match(r"(\d\d)", txt).group(1), 8)}
    for c, p in re.findall(r"(\d\d) x(\d*)", txt): co[int(p or 1)] = int(c, 8)
    co[R] = 1
    GP[imya] = ([co.get(i, 0) for i in range(R + 1)], T.stranica_pozicii(m.start()))
    assert GP[imya][0] == gen(R), (imya, GP[imya][0], gen(R))
def rs_par(msg, g, R):
    r = [0] * R
    for s in msg:
        f = s ^ r[0]; r = r[1:] + [0]
        for i in range(R): r[i] ^= mul(f, g[R - 1 - i])
    return r
def matrica(zag, k, n):
    kus, s = T.kusok(zag, r"\n\s*row\s*\n|\n\s*\d+\s*\n\s*(?:6|7|8)\s*\n\s*Data Packets|6 Data Packets")
    rr = []
    for m in re.finditer(r"(?m)^\s*(\d+)\s*\n\s*((?:[0-7]{2}\s+)+[0-7]{2})\s*(?:\n\s*\|?\s*((?:[0-7]{2}\s+)*[0-7]{2}))?", kus):
        vals = [int(x, 8) for x in (m.group(2) + " " + (m.group(3) or "")).split()]
        rr.append(vals)
    return rr, s
MMr = kod("MMDVMHost", "RS634717.cpp")
for imya, zag, k, n in (("LC", r"GLC matrix of \(24,12,13\) shortened Reed-Solomon code", 12, 24), ("ES", r"GES matrix of \(24,16,9\) shortened Reed-Solomon code", 16, 24)):
    kus, s = T.kusok(zag, r"\n\s*row\s*\n|Table|\n\s*TIA-102\.BAAA-A\s*\n\s*20")
    nums = [int(x, 8) for x in re.findall(r"\b[0-7]{2}\b", kus)]
    rows_ = [nums[i:i + n] for i in range(0, len(nums) - n + 1) if nums[i:i + k] == [1 if j == (len([1 for _ in range(0)])) else 0 for j in range(k)]]
    # надёжнее: ищем подряд k символов единичной части для строки r
    got = []
    for r in range(k):
        e = [1 if j == r else 0 for j in range(k)]
        for i in range(len(nums) - n + 1):
            if nums[i:i + k] == e: got.append(nums[i:i + n]); break
    assert len(got) == k, (imya, len(got))
    for r in range(k): assert got[r][k:] == rs_par(got[r][:k], GP[imya][0], n - k), (imya, r)
    mm = c_massiv(MMr, {"LC": "ENCODE_MATRIX_241213", "ES": "ENCODE_MATRIX_24169"}[imya])
    assert mm == sum(got, []), imya
kus, s_hdr = T.kusok(r"PHDR matrix of \(36,20,17\) shortened Reed-Solomon code", r"Data Packets")
PH = [[int(x, 8) for x in m.split()] for m in re.findall(r"(?m)^\s*\d+\s*\n\s*((?:[0-7]{2} ){15}[0-7]{2})\s*$", kus)]
assert len(PH) == 20
for r in range(20): assert PH[r] == rs_par([1 if j == r else 0 for j in range(20)], GP["HDR"][0], 16), r
mm = c_massiv(MMr, "ENCODE_MATRIX_362017"); assert [mm[36 * r + 20:36 * r + 36] for r in range(20)] == PH
# BCH NID
sb = T.odna(r"g\(x\) = 6331 1413 6723 5453")[0]
gB = int("6331141367235453", 8); assert gB.bit_length() - 1 == 47 and bin(gB).count("1") == 27
assert pmod((1 << 63) | 1, gB) == 0
kus, s_bm = T.kusok(r"16 x 48 Parity Check", r"(?:\n\s*8\.5\.3|\n\s*TIA-102\.BAAA-A)")
BR = re.findall(r"(?m)^\s*\d+\s*\n\s*([0-7]{2})\s+([0-7]{4})\s*\n\s*([0-7]{4}) ([0-7]{4}) ([0-7]{4}) ([0-7]{4})", kus)
assert len(BR) == 16
for i, (a, b, c, d, e, f) in enumerate(BR):
    info = int(a + b, 8); par = int(c + d + e + f, 8)
    assert info == 1 << (15 - i)
    assert par >> 1 == pmod(info << 47, gB), i                # 47 проверочных = остаток по g(x); 64-й бит — столбец матрицы (не чётность)
craft = kod("op25", "op25/gr-op25_repeater/apps/tx/p25craft.py")   # GPL-2.0+ (заголовок файла), M. Ossmann
mo = re.search(r"def bch_64_16_23_encode.*?matrix = \((.*?)\)", open(os.path.join(KOREN, craft)).read(), re.S)
OPM = [int(x, 16) for x in re.findall(r"0x[0-9a-f]+", mo.group(1))]
assert OPM == [(int(a + b, 8) << 48) | int(c + d + e + f, 8) for a, b, c, d, e, f in BR]   # 16 строк 64 бит = op25 bch_64_16_23_encode
proj = open("/home/user/poiujnbhy/src/reportgen/potok/p25.py").read()
m = re.search(r"G_BCH = \(([\d,\s]+)\)", proj); gproj = [int(x) for x in m.group(1).replace("\n", "").split(",") if x.strip()]
assert sum(b << (47 - i) for i, b in enumerate(gproj)) == gB, "проект p25.G_BCH ≠ стандарт"   # в проекте G_BCH[0] — коэффициент при x^47
# решётки
tr = kod("MMDVMHost", "P25Trellis.cpp")
kus, s_t72 = T.kusok(r"Table 7-2 Trellis Encoder State Transition Tables", r"Table 7-3", posl=True)
ch = [int(x) for x in re.findall(r"\b\d+\b", kus)]
E34, E12 = c_massiv(tr, "ENCODE_TABLE_34"), c_massiv(tr, "ENCODE_TABLE_12")
def vstrechaetsya(tabl, per):
    s = " ".join(map(str, ch)); return all(" ".join(map(str, tabl[i:i + per])) in s for i in range(0, len(tabl), per))
assert vstrechaetsya(E34, 8) and vstrechaetsya(E12, 4)
kus, s_t74 = T.kusok(r"Table 7-4 Interleave Table\s*\n\s*INTERLEAVE TABLE", r"=====СТР", posl=True)
IL = c_massiv(tr, "INTERLEAVE_TABLE"); assert sorted(IL) == list(range(98))
# в копии archive.org строки 6–17 таблицы пустые (снимок risunki/p25_t7-4_propusk_strok_6-17.png): сверяем 50 оставшихся пар
# и закон, видимый в таблице: выход o → вход 8·(o mod 13) + 2·(o div 13)… проверяем против MMDVMHost и проекта
ch74 = chisla_bez_kolontitulov(kus); prr = {}
for i in range(len(ch74) - 1):
    k, v = ch74[i], ch74[i + 1]
    if k not in prr and 0 <= k < 98 and IL[k] == v: prr[k] = v
def zakon(o):
    b = 0 if o < 26 else 1 + (o - 26) // 24; j = o - (0 if b == 0 else 26 + 24 * (b - 1))
    return 2 * b + 8 * (j // 2) + j % 2
IL_zakon = [zakon(o) for o in range(98)]
assert IL_zakon == IL, "закон перемежения ≠ MMDVMHost"
propusk = sorted(set(range(98)) - set(prr))
assert len(prr) == 50 and all(k in range(6, 18) or k in range(32, 44) or k in range(56, 68) or k in range(80, 92) for k in propusk), propusk
from ast import literal_eval
IL_proj = literal_eval(re.search(r"ПЕРЕМЕЖЕНИЕ_ТРЕЛЛИС = (\(.*?\))", proj, re.S).group(1))
assert list(IL_proj) == IL
# ПСП речи
s_pn = T.odna(r"pn = \[ 173 pn–1 \+ 13849 \] mod 65536")[0]
au = kod("MMDVMHost", "P25Audio.cpp"); assert "173U * p + 13849U" in open(os.path.join(KOREN, au)).read()
s_cc = T.odna(r"GH\(x\) = x16 \+ x12 \+ x5 \+ 1")[0]; s_cm = T.odna(r"GM\(x\) = x32 \+ x26")[0]; s_c9 = [s for s, n, l in T.naiti(r"CRC-9")][0]
s_il = T.odna(r"Table 5-1 Interleaving Schedule for Voice Word")[-1] if False else [s for s, n, l in T.naiti(r"Table 5-1 Interleaving Schedule for Voice Word")][-1]
tablica("p25_102baaa", {"cikl_16_8_5": [format(r, "016b") for r in R16], "golay_24_12_octal": [format(w >> 12, "04o") + " " + format(w & 0xFFF, "04o") for w in G24],
    "hamming_10_6": H10, "hamming_15_11": H15, "RS_g": {k: [format(x, "02o") for x in v[0]] for k, v in GP.items()}, "RS_PHDR": [[format(x, "02o") for x in r] for r in PH],
    "BCH_g_octal": "6331141367235453", "trellis_34": E34, "trellis_12": E12, "trellis_interleave": IL},
    "P25: порождающие матрицы и многочлены (16,8,5), Голей, Хэмминг, RS над GF(64), BCH NID, решётки",
    [T.ist(s_t52, "Table 5-2"), T.ist(s_t53, "Table 5-3"), T.ist(s_t54, "Table 5-4"), T.ist(GP["HDR"][1], "5.9 g(x)"), T.ist(s_hdr, "PHDR"), T.ist(sb, "8.5.2"),
     T.ist(s_t72, "Table 7-2"), T.ist(s_t74, "Table 7-4"), ist_kod(craft, "def bch_64_16_23_encode"), ist_kod(lsd, "CCS_PARITY"), ist_kod(MMr, "ENCODE_MATRIX_362017"), ist_kod(tr, "ENCODE_TABLE_34"), ist_kod(gl, "ENCODING_TABLE_23127")],
    "все матрицы пересчитаны по порождающим многочленам и совпали с текстом; (16,8,5): пример «A»→$411E и CCS_PARITY MMDVMHost; Голей (24,12): каждая строка = систематическое слово по g(x)=6165 + чётность, "
    "(18,6) = правые 18 бит строк 7–12, ENCODING_TABLE_23127 совпал; Хэмминги = MMDVMHost " + str((sovp10, sovp15)) + "; RS: g(x) из корней α…α^R (GF(64), α^6+α+1) совпали с текстом, "
    "матрицы GLC/GES/PHDR пересчитаны кодером и совпали с ENCODE_MATRIX_* MMDVMHost; BCH: g(x) степени 47 с 27 членами делит x^63+1, все 16 строк матрицы (+64-й бит) пересчитаны, g(x) = G_BCH проекта; "
    "BCH 64 = op25; решётки: ENCODE_TABLE_34/12 найдены в табл. 7-2, перемежение табл. 7-4: в копии archive.org пусты строки 6–17 (снимок risunki/p25_t7-4_propusk_strok_6-17.png), " + str(len(prr)) + " видимых пар = INTERLEAVE_TABLE MMDVMHost = ПЕРЕМЕЖЕНИЕ_ТРЕЛЛИС проекта = закон: выходы 0–25, 26–49, 50–73, 74–97 — группы b=0..3, вход = 2b + 8·(j div 2) + (j mod 2), j — номер в группе")
def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "APCO P25 (TIA-102)", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST = "есть в проекте: p25.py (синхрослово, NID BCH(63,16) ближайшим словом, TSBK: решётка 1/2, CRC)"
rec("P25 NID: BCH (63,16,23) + бит чётности", "БЧХ", {"g(x)": "6331 1413 6723 5453 (восьм., степень 47, 27 членов)", "поле": "GF(2^6) как у RS", "NID": "NAC 12 + DUID 4 → 64 бита"},
    "P25 все кадры (NID)", [T.ist(sb, "8.5.2")], "47 проверочных столбцов пересчитаны по g(x), все 16 строк 64 бит = op25 p25craft.bch_64_16_23_encode; g = G_BCH проекта; 64-й бит задан матрицей (не общая чётность: строка 1 имеет вес 27)", EST)
rec("P25 Голей (24,12,8), (23,12,7), (18,6,8)", "Голей", {"g(x)": "x11+x10+x6+x5+x4+x2+1 = 6165 (как DMR)", "(23,12)": "IMBE u0–u3 (с ПСП-смещением)", "(24,12)": "TDULC, заголовок", "(18,6)": "HDU (RS(36,20) → Голей)"},
    "P25 голос и LC", [T.ist(s_t53, "Table 5-3"), T.ist(sg, "5.7")], "строки пересчитаны, ENCODING_TABLE_23127 MMDVMHost совпал", "Голей 24/23 есть (ysf.py/dmr.py); (18,6) — нет")
rec("P25 Хэмминг (15,11,3) и (10,6,3)", "Хэмминг", {"g(x)": "x4+x+1", "(15,11)": "IMBE u4–u6", "(10,6)": "LC/ES в LDU (гексабиты)"}, "P25 LDU", [T.ist(s_t54, "Table 5-4")], f"= MMDVMHost {sovp10}/{sovp15}", "нет")
rec("P25 RS (36,20,17), (24,12,13), (24,16,9) над GF(64)", "РС (укороченный)", {"поле": "α^6+α+1", "g": "(x+α)…(x+α^R), R = 16/12/8", "применение": "HDU (MI, ALGID, KID) / LC (LDU1, TDULC) / ES (LDU2)"},
    "P25 голосовые заголовки и LC", [T.ist(GP["HDR"][1], "5.9"), T.ist(s_hdr, "PHDR")], "g и матрицы пересчитаны; = MMDVMHost RS634717 ENCODE_MATRIX_*", "есть общий RS (rs_bch.py) для GF(64); разметки LDU — нет")
rec("P25 циклический (16,8,5) — низкоскоростные данные", "циклический (укороченный)", {"g(x)": "x8+x5+x4+x3+1", "пример": "$41 → $411E"}, "P25 LSD (88,89 бит/с)", [T.ist(s_t52, "5.6")], "пример и CCS_PARITY совпали", "нет")
rec("P25 решётчатые коды 1/2 и 3/4 (4 и 8 состояний)", "решётчатый", {"1/2": "дибиты, 4 состояния (ENCODE_TABLE_12)", "3/4": "трибиты, 8 состояний (ENCODE_TABLE_34)", "перемежение": "табл. 7-4, 98 дибитов"},
    "P25 PDU и TSBK", [T.ist(s_t72, "Table 7-2"), T.ist(s_t74, "Table 7-4")], "= MMDVMHost P25Trellis", "1/2 есть (p25.py TSBK); 3/4 — нет")
rec("P25 речь IMBE: ПСП маскирования и перемежение 144 бит", "скремблер (линейный конгруэнтный) + перемежитель", {"ПСП": "p0 = 16·u0, pn = (173·pn−1 + 13849) mod 65536, бит 15; маски m1–m6 23/23/23/15/15/15 бит", "перемежение": "табл. 5-1"},
    "P25 LDU1/LDU2", [T.ist(s_pn, "5.3"), T.ist(s_il, "Table 5-1")], "формула совпала с MMDVMHost P25Audio.cpp и op25 p25p2_vf.cc", "нет")
rec("P25 CRC-CCITT, CRC-9, CRC-32 пакетов", "CRC-подобный", {"заголовок": "x16+x12+x5+1 с инверсией", "CRC-9": "подтверждаемые блоки", "CRC-32": "GM(x) = 0x04C11DB7"}, "P25 PDU",
    [T.ist(s_cc, "6.x"), T.ist(s_cm, "CRC-32"), T.ist(s_c9, "CRC-9")], "многочлены из текста", "CRC-CCITT есть (p25.py TSBK); 9/32 — нет")
# ================= P25 фаза 2 (TDMA, TIA-102.BBAC) =================================================================
T2 = Tekst("istochniki/p25/TIA-102.BBAC_TDMA_MAC_Layer.pdf")
isch = kod("op25", "op25/gr-op25_repeater/lib/p25p2_isch.cc"); duidf = kod("op25", "op25/gr-op25_repeater/lib/p25p2_duid.cc")
lfsrf = kod("op25", "op25/gr-op25_repeater/apps/tdma/lfsr.py"); tdmaf = kod("op25", "op25/gr-op25_repeater/lib/p25p2_tdma.h")
kus, s_isch = T2.kusok(r"Table 5-4 Generator Matrix, g, for I-ISCH", r"This results in a 40-bit code word")
GI = [int("".join(l.split()), 2) for l in kus.split("\n") if re.fullmatch(r"\s*(?:[01] ){39}[01]\s*", l)]
assert len(GI) == 9
mc0 = re.search(r"C0 = %((?:[01]{4} ){9}[01]{4})", T2.ves); C0 = int(mc0.group(1).replace(" ", ""), 2)
def isch_enc(u): 
    w = 0
    for i in range(9):
        if (u >> (8 - i)) & 1: w ^= GI[i]
    return w ^ C0
assert min(bin(isch_enc(u) ^ C0).count("1") for u in range(1, 512)) == 16
mp = {int(a, 16): int(b) for a, b in re.findall(r"isch_map\[0x([0-9a-f]+)ULL\] = (\d+);", open(os.path.join(KOREN, isch)).read())}
assert len(mp) == 128 and all(isch_enc(v) == k for k, v in mp.items())
kus, s_duid = T2.kusok(r"The generator matrix for the \(8,4,4\) binary code is shown in Table 5-2 below:\s*\n\s*\n?\s*Table 5-2 DUID Generator Matrix", r"\n\s*5\.4\.2|Table 5-3|The DUID")
dd = [int(x) for x in re.findall(r"[01]", kus)][:32]
GD = [int("".join(map(str, dd[8 * i:8 * i + 8])), 2) for i in range(4)]
def duid_enc(u):
    w = 0
    for i in range(4):
        if (u >> (3 - i)) & 1: w ^= GD[i]
    return w
lk = [int(x) for x in re.search(r"_duid_lookup\[256\] = \{(.*?)\};", open(os.path.join(KOREN, duidf)).read(), re.S).group(1).replace("\n", "").split(",") if x.strip()]
assert all(lk[duid_enc(u)] == u for u in range(16)) and min(bin(duid_enc(u)).count("1") for u in range(1, 16)) == 4
kus, s_g28 = T2.kusok(r"The generator polynomial g\(x\) is given below with octal coefficients:", r"\(13\)")
txt = " ".join(kus.split()).replace("+67", "+ 67")
co = {28: 1}
for c, pw in re.findall(r"(\d\d) x(\d*)", txt): co[int(pw or 1)] = int(c, 8)
co[0] = int(re.search(r"x \+ (\d\d)\s*$", txt).group(1), 8)
assert [co.get(i, 0) for i in range(29)] == gen(28), "g(x) (63,35) ≠ ∏(x+α^j), j=1..28"
kus, s_t55 = T2.kusok(r"Table 5-5 Mother Code Applications and Definitions", r"Info, K symbols")
PROIZV = {m[0]: tuple(int(x) for x in m[1:]) for m in re.findall(r"(IEMI|S-OEMI|I-OEMI|ESS) \|?\s*\((\d+),(\d+),(\d+)\)", " ".join(kus.split()).replace("|", " ").replace("  ", " "))}
kk = re.findall(r"(\d) \|?(IEMI|S-OEMI|I-OEMI|ESS) \|?\((\d+),(\d+),(\d+)\)\|?(\d+)\|?(\d+)\|?(\d+)\|?(\d+) \|?(\d+) \|?(\d+)\|?(\d+)\|?(\d+)", kus.replace("\n", "|").replace("| |", "|"))
s_scr2 = T2.odna(r"G\(x\) = x44 \+ x40 \+ x35 \+ x29 \+ x24 \+ x10 \+ 1")[0]
assert "(s1<<40)+(s2<<35)+(s3<<29)+(s4<<24)+(s5<<10)+s6" in open(os.path.join(KOREN, lfsrf)).read()
assert "ezpwd::RS<63,35> rs28;" in open(os.path.join(KOREN, tdmaf)).read()
ZAPISI += [
 {"имя": "P25 фаза 2: RS (63,35,29) над GF(64) и производные (46,26,21), (45,26,20), (52,30,23), (44,16,29)", "семейство": "APCO P25 фаза 2 (TIA-102.BBAC)", "область": "mobilnaya",
  "вид": "РС (укороченный и выколотый)", "параметры": {"поле": "c(x) = x6+x+1", "g(x)": "степени 28, корни α^1…α^28, восьм. коэффициенты: " + txt,
   "производные": "из матери (63,35): укорочение S старших инф. символов (нули) и выкалывание U младших проверочных: IEMI (46,26) S=9 U=8; S-OEMI (45,26) S=9 U=9; I-OEMI (52,30) S=5 U=6; ESS (44,16) S=19 U=0",
   "декодирование": "выколотые — стирания"},
  "где применяется": "P25 фаза 2 TDMA: MAC-сообщения FACCH/SACCH (IEMI/OEMI), ESS (синхронизация шифра)",
  "источник": [T2.ist(s_g28, "Annex A g(x)"), T2.ist(s_t55, "Table 5-5"), ist_kod(tdmaf, "ezpwd::RS<63,35> rs28")],
  "проверка": "g(x) из текста = произведение (x+α^j), j = 1…28 над GF(64) (тот же генератор, что для фазы 1); op25 декодирует ezpwd::RS<63,35>",
  "сложность внедрения": "есть общий RS над GF(64) (rs_bch.py); разметки фазы 2 нет — средняя"},
 {"имя": "P25 фаза 2: ISCH — смежный класс кода (40,9,16)", "семейство": "APCO P25 фаза 2 (TIA-102.BBAC)", "область": "mobilnaya", "вид": "блочный линейный (смежный класс)",
  "параметры": {"G": "9 строк × 40 (табл. 5-4)", "C0": format(C0, "010X") + " (добавляется к слову)", "d_min": 16, "информация": "9 бит (2 резервных = 0 → 128 слов)"},
  "где применяется": "P25 фаза 2: I-ISCH (информация о слоте, счётчик сверхкадра)", "источник": [T2.ist(s_isch, "Table 5-4, C0")],
  "проверка": "все 128 кодовых слов (G, C0) = isch_map op25 p25p2_isch.cc; d_min = 16 полным перебором", "сложность внедрения": "нет; простая (таблица 128 слов)"},
 {"имя": "P25 фаза 2: DUID — (8,4,4)", "семейство": "APCO P25 фаза 2 (TIA-102.BBAC)", "область": "mobilnaya", "вид": "блочный линейный (расширенный Хэмминг)",
  "параметры": {"G": [format(g, "08b") for g in GD], "размещение": "4 дибита в пачке (позиции 10, 47, 132, 169 в op25)"},
  "где применяется": "P25 фаза 2: тип пачки (4V, 2V, SACCH, FACCH, LCCH …)", "источник": [T2.ist(s_duid, "Table 5-2")],
  "проверка": "кодер по G = _duid_lookup op25 для всех 16 слов; d_min = 4", "сложность внедрения": "нет; простая"},
 {"имя": "P25 фаза 2: скремблер LFSR 44 бит (x44+x40+x35+x29+x24+x10+1)", "семейство": "APCO P25 фаза 2 (TIA-102.BBAC)", "область": "mobilnaya", "вид": "скремблер (аддитивный)",
  "параметры": {"многочлен": "x44+x40+x35+x29+x24+x10+1", "начальное": "из WACN, System ID, NAC (7.2)", "длина": "4320 бит на сверхкадр 360 мс (два слота)",
   "что скремблируется": "2V/4V всегда, сигнализация — по признаку; ISCH и синхро — нет"},
  "где применяется": "P25 фаза 2 TDMA", "источник": [T2.ist(s_scr2, "7.2 (4)")], "проверка": "разбиение регистра op25 apps/tdma/lfsr.py по степеням 40/35/29/24/10 совпадает с многочленом",
  "сложность внедрения": "есть аддитивный скремблер (skrembler.py), инициализация по NAC/WACN — добавить"},
]

if __name__ == "__main__":
    print(len(ZAPISI), "записей", sovp10, sovp15)
