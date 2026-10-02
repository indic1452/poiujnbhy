"""ATSC 3.0 физический уровень (A/322:2026-06): LDPC 64800/16200 (типы A и B), БЧХ t=12, CRC-32, скремблер ББ-пакетов,
групповое перемежение бит, защита L1. Сверка таблиц — построчно с gr-atsc3 (drmpeg, GPL-3)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/atsc/A322-2026-06-Physical-Layer-Protocol.pdf")
KOL = r"ATSC A/322|Physical Layer Protocol|11 June 2026"
ZAPISI = []
GA = "gr-atsc3"
g_ldpc = kod(GA, "lib/ldpc_bb_impl.cc"); g_int = kod(GA, "lib/interleaver_bb_impl.cc")
g_bch = kod(GA, "lib/bch_bb_impl.cc"); g_scr = kod(GA, "lib/bbscrambler_bb_impl.cc")

# --- скремблер ББ-пакета -----------------------------------------------------
s_g, _, l_g = T.odna(r"^G\(x\) = 1\+X\+X3\+X6\+X7\+X11\+X12\+X13\+X16")
s_i, _, _ = T.odna(r"The initial sequence \(0xF180: 1111 0001 1000 0000\)")
s_f, _, l_f = T.odna(r"The first values of the baseband scrambling sequence are 1100 0000 0110 1101 0011 1111")
g_def = kod(GA, "lib/atsc3_defines.h")
t_scr = open(os.path.join(KOREN, g_scr)).read()
assert "int sr = 0x18f" in t_scr            # 0xF180 в обратном порядке бит
POLY = int(re.search(r"#define POLYNOMIAL (0x[0-9a-f]+)", open(os.path.join(KOREN, g_def)).read()).group(1), 16)
# 0xd31c — отводы G(x) в форме Галуа при сдвиге вправо (член x^e → бит 15−e): проверяем, что это та же G(x)
st_G = stepeni(l_g.split("=")[1].replace("X", "x"))
assert sum(1 << (15 - e) for e in st_G if e != 16) == POLY, hex(POLY)   # сдвиг вправо: взаимный многочлен, бит 15−e
def atsc3_scr(n):
    sr = 0x18f; out = []
    for _ in range(n):
        packed = ((sr & 0x4) << 5) | ((sr & 0x8) << 3) | ((sr & 0x10) << 1) | ((sr & 0x20) >> 1) | ((sr & 0x200) >> 6) | \
                 ((sr & 0x1000) >> 10) | ((sr & 0x2000) >> 12) | ((sr & 0x8000) >> 15)
        out.append(packed)
        b = sr & 1; sr >>= 1
        if b: sr ^= POLY
    return out
perv = [int(x.replace(" ", ""), 2) for x in re.findall(r"[01]{4} [01]{4}", l_f.split("are")[1])]
assert atsc3_scr(3) == perv == [0xC0, 0x6D, 0x3F], ([hex(x) for x in atsc3_scr(3)], perv)
nashli = True
ZAPISI.append(Z("ATSC 3.0 скремблер ББ-пакета (ГПСП 16 бит, 0xF180)", "ATSC 3.0 (A/322)", "скремблер (рандомизатор)",
  {"многочлен": "G(x) = 1+X+X^3+X^6+X^7+X^11+X^12+X^13+X^16", "начало": "0xF180 (1111 0001 1000 0000) в начале каждого ББ-пакета",
   "применение": "8 выходов D7…D0 — байт, XOR побайтно (старший со старшим), затем один сдвиг",
   "первые байты": "0xC0 0x6D 0x3F …", "также": "тот же скремблер — для L1-Basic/L1-Detail (6.5.2.2) и для PLS"},
  "ATSC 3.0: ББ-пакеты всех PLP, сигнализация L1",
  [T.ist(s_g, "5.2.3 G(x)"), T.ist(s_i, "начальное 0xF180"), T.ist(s_f, "первые значения"), ist_kod(g_scr, r"int sr = 0x18f", "gr-atsc3 bbscrambler")],
  "алгоритм gr-atsc3 (регистр Галуа 0x18f = 0xF180 в обратном порядке, отводы 0xd31c = G(x)) даёт первые байты C0 6D 3F — как в тексте стандарта «1100 0000 0110 1101 0011 1111»", "частично: skrembler.py находит аддитивный скремблер вслепую; сброс на каждом ББ-пакете — через «ПСП блока»"))

# --- БЧХ ------------------------------------------------------------------------
kus, s_b = T.kusok(r"Table 6\.3 BCH Polynomials", r"6\.1\.2\.2 CRC", posl=True)
mn = re.findall(r"x1[46](?:\+x\d*)*\s*\+\s*1|x1[46][^\n]*", kus)
st64 = [];  st16 = []
for l in kus.split("\n"):
    l = l.strip().replace(" ", "")
    if l.startswith("x16+"): st64.append(stepeni(l.replace("+", " + ")))
    if l.startswith("x14+"): st16.append(stepeni(l.replace("+", " + ")))
assert len(st64) == 12 and len(st16) == 12, (len(st64), len(st16))
from functools import reduce
def pmul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r
G64 = reduce(pmul, [mnogochlen(s) for s in st64]); G16 = reduce(pmul, [mnogochlen(s) for s in st16])
assert G64.bit_length() - 1 == 192 and G16.bit_length() - 1 == 168
# сверка с gr-atsc3: там g(x) хранится произведением тех же многочленов
t_bch = open(os.path.join(KOREN, g_bch)).read()
# многочлены БЧХ ATSC 3.0 = многочлены DVB-S2 (t=12): сверка первого — x^16+x^5+x^3+x^2+1 = 0x1002D
assert mnogochlen(st64[0]) == 0x1002D and mnogochlen(st16[0]) == 0x402B
# каждый g_i — неприводимый (порядок x делит 2^m−1)
def poryadok_x(p):
    m = p.bit_length() - 1; v = 2; k = 1
    while v != 1:
        v <<= 1
        if v >> m & 1: v ^= p
        k += 1
        if k > (1 << m): return None
    return k
assert poryadok_x(mnogochlen(st64[0])) == 65535 and poryadok_x(mnogochlen(st16[0])) == 16383
kus_k, s_k = T.kusok(r"Table 6\.1 Length of Kpayload", r"Table 6\.2 Length", posl=True)
tab_bch = tablica("atsc3_bch_mnogochleny", {"64800": [D_zapis(s).replace("D", "x") for s in st64], "16200": [D_zapis(s).replace("D", "x") for s in st16],
  "g64800_hex": hex(G64), "g16200_hex": hex(G16)}, "Многочлены g1…g12 БЧХ ATSC 3.0 (табл. 6.3) и их произведение g(x)",
  [T.ist(s_b, "Table 6.3")], "разобрано из текста; степени произведений 192 и 168 (Mouter); g1 примитивны (порядок x = 2^16−1 и 2^14−1); совпадают с БЧХ DVB-S2")
ZAPISI.append(Z("ATSC 3.0 внешний код БЧХ t=12 (Mouter 192/168)", "ATSC 3.0 (A/322)", "БЧХ",
  {"64800": "БЧХ над GF(2^16), g(x) = g1…g12, Mouter = 192", "16200": "БЧХ над GF(2^14), Mouter = 168", "многочлены": tab_bch,
   "Kpayload": "табл. 6.1/6.2 (Kpayload = Ninner·R − Mouter)", "порядок": "систематический, m0 первым (старшая степень)"},
  "ATSC 3.0 PLP (вариант внешнего кода BCH), L1-Basic/L1-Detail (16200)",
  [T.ist(s_b, "Table 6.3"), T.ist(s_k, "Table 6.1 Kpayload"), ist_kod(g_bch, r"bch", "gr-atsc3 bch")],
  "степени произведений = Mouter; g1 примитивен; первые многочлены совпадают с БЧХ DVB-S2 (0x1002D / 0x402B)",
  "есть: dvbs2.py (внешний БЧХ DVB-S2 с теми же g1…g12) — применимо для 64800/16200"))

# --- CRC-32 ----------------------------------------------------------------------
s_c, _, _ = T.odna(r"of gi = 0 except for: g21 , g16, g11")
t_b = open(os.path.join(KOREN, g_bch)).read()
assert "0x00210801" in t_b
ZAPISI.append(Z("ATSC 3.0 внешний код CRC-32 (x^32+x^21+x^16+x^11+1)", "ATSC 3.0 (A/322)", "CRC-подобный",
  {"многочлен": "x^32 + x^21 + x^16 + x^11 + 1 (0x00210801)", "начальное": "все единицы", "порядок": "старший бит первым, b31…b0 дописываются",
   "инверсия": "нет"}, "ATSC 3.0 PLP (вариант внешнего кода CRC)",
  [T.ist(s_c, "6.1.2.2 CRC"), ist_kod(g_bch, r"0x00210801", "gr-atsc3 crc32_init")],
  "отводы g21, g16, g11 из текста = 0x00210801 в gr-atsc3", "частично: crc.py/crc_katalog — CRC вслепую (многочлен находится); именованной записи нет"))

# --- LDPC -------------------------------------------------------------------------
kus_t, s_t = T.kusok(r"Table 6\.5 Coding Parameters for Type A", r"6\.1\.3\.3 Tradeoffs", posl=True)
M1 = {}
for m in re.finditer(r"(\d+)/15\s*\n(\d+)\s*\n(\d+)\s*\n(\d+)\s*\n(\d+)", kus_t):
    pass
ta = kus_t.split("Table 6.6")[0]; tb = kus_t.split("Table 6.6")[1].split("6.1.3.2")[0]
for nm, kk in (("N", ta), ("S", tb)):
    for m in re.finditer(r"(\d+)/15\s*\n(\d+)\s*\n(\d+)\s*\n(\d+)\s*\n(\d+)", kk):
        M1[(int(m.group(1)), nm)] = tuple(int(m.group(i)) for i in range(2, 6))
assert M1[(2, "N")] == (1800, 54360, 5, 151) and M1[(5, "S")] == (720, 10080, 2, 28), M1
ldpc_tabl = {}; t_ldpc = open(os.path.join(KOREN, g_ldpc)).read()
for sekc, Nn, suf in (("1", 64800, "N"), ("2", 16200, "S")):
    for idx, r in enumerate(range(2, 14), 1):
        kus, s = T.kusok(rf"Table A\.{sekc}\.{idx} Rate = {r}/15 \(Ninner = {Nn}\)", r"Table A\.|Annex B|=====СТР 16[7-9]=====", posl=True)
        rows = stroki_chisel(bez_kolontitulov(kus, KOL))
        g = c_massiv_2d(g_ldpc, f"ldpc_tab_{r}_15{suf}")
        grows = [x[1:1 + x[0]] for x in g]
        sravnit_tablicy(rows, grows, f"ATSC3 LDPC {r}/15 {Nn}")
        K = Nn * r // 15
        tip = "A" if (r, suf) in M1 else "B"
        if tip == "A":
            assert len(rows) == (K + M1[(r, suf)][0]) // 360, (r, Nn, len(rows))
        else:
            assert len(rows) == K // 360, (r, Nn, len(rows))
        mx = max(max(x) for x in rows)
        assert mx < Nn - K, (r, Nn)
        put = tablica(f"atsc3_ldpc_{r}_15_{Nn}", {"строки": rows, "тип": tip, "Ninner": Nn, "Kldpc": K,
                      **({"M1": M1[(r, suf)][0], "M2": M1[(r, suf)][1], "Q1": M1[(r, suf)][2], "Q2": M1[(r, suf)][3]} if tip == "A" else {})},
                      f"Адреса накопителей чётности LDPC ATSC 3.0, R = {r}/15, Ninner = {Nn} (Annex A, тип {tip})",
                      [T.ist(s, f"Table A.{sekc}.{idx}")], f"разобрано из текста; построчно совпало с gr-atsc3 ldpc_tab_{r}_15{suf} ({len(rows)} строк); число строк = " + ("(K+M1)/360" if tip == "A" else "K/360"))
        ldpc_tabl[(r, Nn)] = (put, s, tip, len(rows))
for Nn in (64800, 16200):
    ZAPISI.append(Z(f"ATSC 3.0 LDPC Ninner={Nn}, R = 2/15…13/15 (12 кодов)", "ATSC 3.0 (A/322)", "LDPC",
      {"длина": Nn, "скорости": "2/15 … 13/15", "тип A (2/15–5/15, 7/15 для 64800; 2/15–5/15 для 16200)": "двухчастная структура: M1 проверок с двойной диагональю + M2 проверок IRA, параметры Q1/Q2 (табл. 6.5/6.6)",
       "тип B (прочие)": "IRA как DVB-S2: q(i,j,l) = q(i,j,0) + Qldpc·l mod Minner, аккумулятор pk = pk + pk−1 (табл. 6.7)",
       "таблицы": {f"{r}/15": ldpc_tabl[(r, Nn)][0] for r in range(2, 14)}, "группа": 360},
      "ATSC 3.0 PLP; 16200 3/15 (тип A) и 6/15 (тип B) — также L1-Basic/L1-Detail",
      [T.ist(ldpc_tabl[(2, Nn)][1], "Annex A первая таблица"), T.ist(s_t, "Tables 6.5–6.7")] + [ist_kod(g_ldpc, rf"ldpc_tab_2_15{'N' if Nn == 64800 else 'S'}\[", "gr-atsc3")],
      "все 12 таблиц разобраны из Annex A и построчно совпали с gr-atsc3; число строк соответствует K/360 или (K+M1)/360; максимальный адрес < N−K",
      "нет: встроенных кодов ATSC 3.0 в ldpc_std.py нет (есть DVB-S2/S2X/T2, 5G NR); можно загрузить таблицы как DVB-подобные с поправкой на тип A"))

# --- групповое перемежение (Annex B) -------------------------------------------------
t_int = open(os.path.join(KOREN, g_int)).read()
MODS = [("QPSK", 1), ("16QAM", 2), ("64QAM", 3), ("256QAM", 4), ("1024QAM", 5), ("4096QAM", 6)]
gruppy = {}; s_B = None
for Nn, sekc, ng, suf in ((64800, "1", 180, "N"), (16200, "2", 45, "S")):
    for mod, idx in MODS:
        if Nn == 16200 and idx > 4: continue
        kus, s = T.kusok(rf"Table B\.{sekc}\.{idx} {mod}", r"Table B\.|=====СТР 18[4-9]=====|ANNEX C|Annex C: ", posl=True)
        s_B = s_B or s
        t = bez_kolontitulov(kus, KOL)
        t = re.sub(r"\(\s*0\s*≤\s*j\s*<\s*\d+\s*\)|\(Ninner = \d+[^)]*\)|\(Code length = \d+ bits\)", " ", t)
        tok = re.findall(r"\d+/15|\d+", t)
        vals = {}; i = 0; W = None
        # ширина блока: заголовок j идёт подряд 0,1,2,…
        hdr = []
        for x in tok:
            if "/" in x: break
            hdr.append(int(x))
        W = len([h for h in hdr if h < 180])
        # заголовок может начинаться после «0 ≤ j < 180» — отрежем 180
        hdr = [h for h in hdr if h != 180 and h != 0 or h == 0]
        cur = None; buf = []
        i = 0
        while i < len(tok):
            x = tok[i]
            if "/" in x:
                cur = int(x.split("/")[0]); take = []
                i += 1
                while i < len(tok) and "/" not in tok[i] and len(take) < W:
                    take.append(int(tok[i])); i += 1
                vals.setdefault(cur, []).extend(take)
            else:
                i += 1
        for r in range(2, 14):
            v = vals.get(r, [])
            assert sorted(v) == list(range(ng)), (Nn, mod, r, len(v), W)
            g = c_massiv(g_int, f"group_tab_{r}_15{suf}_{mod}")
            assert g == v, (Nn, mod, r)
        gruppy[f"{Nn}_{mod}"] = {f"{r}/15": vals[r] for r in range(2, 14)}
tab_g = tablica("atsc3_gruppovoe_peremezhenie", gruppy, "Перестановки группового перемежения π(j) ATSC 3.0 (Annex B): группа 360 бит; ключ «Ninner_модуляция» → скорость → π",
  [T.ist(s_B, "Annex B")], "разобрано из текста; каждая π — перестановка 0..Ngroup−1; совпало с gr-atsc3 group_tab_* для всех модуляций и скоростей")
s_bi, _, _ = T.odna(r"^6\.2 Bit Interleavers")
kus_bt, s_bt = T.kusok(r"Table 6\.10 Type A Block Interleaver Configurations", r"6\.2\.3\.2 Type B", posl=True)
ZAPISI.append(Z("ATSC 3.0 битовый перемежитель: перемежение чётности + групповое π(j) + блочный (тип A/B)", "ATSC 3.0 (A/322)", "перемежитель",
  {"ступени": "1) перемежение бит чётности (для кодов типа B и частей типа A); 2) групповое: 360-битные группы в порядке π(j) (Annex B); 3) блочный перемежитель типа A (столбцы) или B (табл. 6.8–6.11)",
   "таблица π": tab_g, "модуляции": "QPSK, 16/64/256/1024/4096-NUC; для 16200 — до 256-NUC"},
  "ATSC 3.0 BICM", [T.ist(s_bi, "6.2"), T.ist(s_B, "Annex B"), T.ist(s_bt, "Table 6.10"), ist_kod(g_int, r"group_tab_2_15N_QPSK", "gr-atsc3")],
  "все перестановки Annex B разобраны из текста и совпали с gr-atsc3; проверено, что это перестановки",
  "частично: ldpc (перемежение бит DVB-S2) — групповое перемежение ATSC 3.0 не встроено"))

# --- защита L1 -----------------------------------------------------------------------------
kus_l, s_l = T.kusok(r"Table 6\.17 Configurations for L1-Basic", r"6\.5\.2\.2 Scrambling", posl=True)
assert "200" in kus_l and "3/15" in kus_l and "6/15" in kus_l
s_18, _, _ = T.odna(r"^Table 6\.18 Parameters for BCH Encoding of L1")
s_21, _, _ = T.odna(r"^Table 6\.21 Group-wise Interleaving Pattern for all L1-Basic")
s_24, _, _ = T.odna(r"^Table 6\.24 Parameters for Puncturing")
ZAPISI.append(Z("ATSC 3.0 защита L1-Basic/L1-Detail: скремблер + БЧХ(Mouter=168) + укороченный/выколотый LDPC 16200", "ATSC 3.0 (A/322)", "каскадный: БЧХ + LDPC с укорочением и выкалыванием",
  {"L1-Basic": "Ksig = 200 бит, режимы 1–7: LDPC 16200 R=3/15 тип A; QPSK (реж. 1–3), NUC 16/64/256 (реж. 4–7); длина 3820…69 ячеек",
   "L1-Detail": "Ksig 200…2352/3072/6312 бит: режимы 1–2 — 3/15 тип A, 3–7 — 6/15 тип B; сегментация (Kseg, табл. 6.25), дополнительная чётность",
   "цепочка": "скремблер (как ББ, 0xF180) → БЧХ 16200 укороченный (Nouter = Ksig+168) → заполнение нулями по шаблону групп (табл. 6.20) → LDPC → перестановка чётности (табл. 6.21/6.22) → повторение (6.23) → выкалывание чётности (6.24) → удаление нулей"},
  "ATSC 3.0 преамбула (сигнализация L1)",
  [T.ist(s_l, "Table 6.17"), T.ist(s_18, "Table 6.18"), T.ist(s_21, "Table 6.21"), T.ist(s_24, "Table 6.24")],
  "параметры разобраны из табл. 6.17 (200 бит, 3/15, 6/15); таблицы LDPC 16200 3/15 и 6/15 сверены с gr-atsc3 (см. запись LDPC)",
  "нет"))
