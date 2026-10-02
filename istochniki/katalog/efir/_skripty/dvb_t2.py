"""DVB-T2 / T2-Lite (EN 302 755 V1.4.1): БЧХ, LDPC (Annex A/B), перемежение бит (скручивание столбцов, демультиплексор),
частотный перемежитель, P1 (S1/S2), защита L1 (укорочение/выкалывание), CRC-8/CRC-32, скремблер ББ.
Сверка — построчно с gr-dtv (GNU Radio, GPL-3)."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from functools import reduce

T = Tekst("istochniki/dvb_t2/en_302755v010401p.pdf")
KOL = r"ETSI"
GR = "gnuradio"; ZAPISI = []
g_ldpc = kod(GR, "gr-dtv/lib/dvb/dvb_ldpc_bb_impl.cc"); g_bch = kod(GR, "gr-dtv/lib/dvb/dvb_bch_bb_impl.cc")
g_bi = kod(GR, "gr-dtv/lib/dvbt2/dvbt2_interleaver_bb_impl.cc"); g_fi = kod(GR, "gr-dtv/lib/dvbt2/dvbt2_freqinterleaver_cc_impl.cc")
g_p1 = kod(GR, "gr-dtv/lib/dvbt2/dvbt2_p1insertion_cc_impl.cc"); g_fm = kod(GR, "gr-dtv/lib/dvbt2/dvbt2_framemapper_cc_impl.cc")
g_bbs = kod(GR, "gr-dtv/lib/dvb/dvb_bbscrambler_bb_impl.cc")

# --- скремблер ББ ---------------------------------------------------------------
s_s, _, _ = T.odna(r"^1 \+ X14 \+ X15")
s_si, _, _ = T.odna(r"Loading of the sequence \(100101010000000\) into the PRBS register")
psp = prbs((14, 15), 16, [1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0])
assert int("".join(map(str, psp[:8])), 2) == 0x03
ZAPISI.append(Z("DVB-T2 скремблер ББ-кадра и L1 (1+X^14+X^15, 100101010000000)", "DVB-T2 (EN 302 755)", "скремблер (рандомизатор)",
  {"многочлен": "1 + X^14 + X^15", "начало": "загрузка 100101010000000 в начале каждого BBFRAME (и каждого блока L1-post при L1_REPETITION/ скремблировании L1)",
   "первые биты ПСП": "0000 0011 … (0x03)", "примечание": "та же ПСП, что у DVB-S2/C2 и энергодисперсии DVB-T (там — сброс раз в 8 пакетов)"},
  "DVB-T2, T2-Lite, DVB-C2, DVB-S2", [T.ist(s_s, "5.2.2 ПСП"), T.ist(s_si, "загрузка"), ist_kod(g_bbs, r"0x4A80|init_bb_randomi[sz]", "gr-dtv bbscrambler")],
  "первый байт ПСП 0x03 вычислен из загрузки (как у DVB-T); та же схема в gr-dtv dvb_bbscrambler", "есть: dvbs2.py (снятие скремблера BBFRAME DVB-S2) — применимо"))

# --- БЧХ ---------------------------------------------------------------------------
kus, s_b = T.kusok(r"Table 7\(a\): BCH polynomials \(for normal FECFRAME", r"The bits of the baseband frame form", posl=True)
a, b = kus.split("Table 7(b)")
pa = [stepeni(x.replace("+", " + ")) for x in re.findall(r"\n(1\+[x\d+]+)\s*\n", a)]
pb = [stepeni(x.replace("+", " + ")) for x in re.findall(r"\n(1\+[x\d+]+)\s*\n", b)]
assert len(pa) == 12 and len(pb) == 12
# сверка с ATSC 3.0 (A/322 табл. 6.3) — тот же набор
import json
atsc = json.load(open(os.path.join(KOREN, "tablicy", "atsc3_bch_mnogochleny.json")))["данные"]
zap = lambda st: " + ".join(("1" if s == 0 else "x" if s == 1 else f"x{s}") for s in sorted(st))
assert [zap(s) for s in pa] == atsc["64800"] and [zap(s) for s in pb] == atsc["16200"]
kus6, s6 = T.kusok(r"Table 6\(a\): Coding parameters \(for normal FECFRAME", r"Table 7\(a\)", posl=True)
ZAPISI.append(Z("DVB-T2 внешний БЧХ t=12/10 (normal 64800) и t=12 (short 16200)", "DVB-T2 (EN 302 755)", "БЧХ",
  {"normal": "g(x) = g1…g12 (табл. 7a), GF(2^16); t=12 (192 бита чётности)", "short": "g1…g12 (табл. 7b), GF(2^14), t=12 (168 бит)",
   "Kbch/Nbch": "табл. 6a/6b", "многочлены": {"64800": [zap(s) for s in pa], "16200": [zap(s) for s in pb]}},
  "DVB-T2, T2-Lite, DVB-C2 (те же), DVB-S2 (те же многочлены), ATSC 3.0 (те же)",
  [T.ist(s_b, "Table 7(a)/(b)"), T.ist(s6, "Table 6(a)"), ist_kod(g_bch, r"bch_poly|poly", "gr-dtv dvb_bch")],
  "12+12 многочленов разобраны из текста и совпали с табл. 6.3 ATSC A/322 (независимый документ)", "есть: dvbs2.py (внешний БЧХ DVB-S2) — применим к T2/C2"))

# --- LDPC ------------------------------------------------------------------------------
t_ldpc = open(os.path.join(KOREN, g_ldpc)).read()
SPIS = [("A.1", "1/2", 64800, "ldpc_tab_1_2N"), ("A.2", "3/5", 64800, "ldpc_tab_3_5N"), ("A.3", "2/3", 64800, "ldpc_tab_2_3N_DVBT2"),
        ("A.4", "3/4", 64800, "ldpc_tab_3_4N"), ("A.5", "4/5", 64800, "ldpc_tab_4_5N"), ("A.6", "5/6", 64800, "ldpc_tab_5_6N"),
        ("B.1", "1/4", 16200, "ldpc_tab_1_4S"), ("B.2", "1/2", 16200, "ldpc_tab_1_2S"), ("B.3", "3/5", 16200, "ldpc_tab_3_5S_DVBT2"),
        ("B.4", "2/3", 16200, "ldpc_tab_2_3S"), ("B.5", "3/4", 16200, "ldpc_tab_3_4S"), ("B.6", "4/5", 16200, "ldpc_tab_4_5S"),
        ("B.7", "5/6", 16200, "ldpc_tab_5_6S"), ("B.8", "1/3", 16200, "ldpc_tab_1_3S"), ("B.9", "2/5", 16200, "ldpc_tab_2_5S")]
PROEKT = json.load(open("/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_dvb.json")) if True else {}
v_proekte = []
tabl = {}; otlichie = []
for tn, r, Nn, gname in SPIS:
    kus, s = T.kusok(rf"Table {re.escape(tn)}: Rate {r} \(Nldpc = {Nn // 1000} {Nn % 1000:03d}\)[^\n]*", r"Table [AB]\.\d+:|Annex C \(normative\)", posl=True)
    rows = stroki_chisel(bez_kolontitulov(kus, KOL))
    g = c_massiv_2d(g_ldpc, gname); grows = [x[1:1 + x[0]] for x in g]
    sravnit_tablicy(rows, grows, f"T2 LDPC {r} {Nn}")
    k, n = map(int, r.split("/"))
    # Kldpc: для short 1/4, 1/3, 2/5 и т.п. — номинальная скорость; строки = Kldpc/360
    Kldpc = len(rows) * 360
    assert max(max(x) for x in rows) < Nn - Kldpc
    if gname.endswith("_DVBT2"):
        g_s2 = [x[1:1 + x[0]] for x in c_massiv_2d(g_ldpc, gname.replace("_DVBT2", "_DVBS2"))]
        razn = sum(1 for x, y in zip(rows, g_s2) if sorted(x) != sorted(y))
        otlichie.append(f"{r} {Nn}: {razn} строк из {len(rows)} отличаются от варианта DVB-S2 в gr-dtv даже без учёта порядка адресов в строке")
    put = tablica(f"dvbt2_ldpc_{r.replace('/', '_')}_{Nn}", {"строки": rows, "Nldpc": Nn, "Kldpc": Kldpc},
                  f"Адреса накопителей чётности LDPC DVB-T2, R = {r}, Nldpc = {Nn} (табл. {tn}); q по табл. 8(a)/(b): q = (Nldpc − Kldpc)/360",
                  [T.ist(s, f"Table {tn}")], f"разобрано из текста; построчно совпало с gr-dtv {gname} ({len(rows)} строк)")
    tabl[f"{r} ({Nn})"] = put
    kp = f"dvb-t2-{Nn}-{Kldpc}"
    if kp in PROEKT:
        assert PROEKT[kp]["строки"] == rows, kp
        v_proekte.append(kp)
ZAPISI.append(Z("DVB-T2 / T2-Lite LDPC 64800 (1/2…5/6) и 16200 (1/4…5/6, 1/3, 2/5)", "DVB-T2 (EN 302 755)", "LDPC",
  {"структура": "IRA (как DVB-S2): группы по 360, q = (Nldpc − Kldpc)/360, накопитель pi = pi ⊕ pi−1",
   "таблицы": tabl, "особенности": "2/3 (64800) и 3/5 (16200) T2 отличаются от одноимённых кодов DVB-S2: " + "; ".join(otlichie),
   "T2-Lite": "только 16200; дополнительно 1/3 и 2/5 (табл. B.8, B.9)", "short 1/4": "номинально 1/4 (Kldpc 3240), по сигнализации — «1/4», эффективная ≈ 1/5"},
  "DVB-T2, T2-Lite, DVB-C2 (подмножество), DVB-NGH (часть)",
  [T.ist(T.odna(r"^Table A\.1: Rate 1/2")[0], "Annex A"), T.ist(T.odna(r"^Table B\.1: Rate 1/4")[0], "Annex B"), ist_kod(g_ldpc, r"ldpc_tab_2_3N_DVBT2\[", "gr-dtv")],
  "все 15 таблиц разобраны из текста и построчно совпали с gr-dtv; различия с DVB-S2 подсчитаны; табличные коды DVB-S2 — в области модемов (kody2)",
  f"есть: data/ldpc_dvb.json + ldpc_std.py — {len(v_proekte)} из 15 таблиц есть в проекте и построчно совпали с разобранными из стандарта ({', '.join(v_proekte)})"))

# --- битовый перемежитель --------------------------------------------------------------
kus10, s10 = T.kusok(r"Table 10: Column twisting parameter tc", r"Table 11:", posl=True)
t_bi = open(os.path.join(KOREN, g_bi)).read()
for nm in ("twist16n", "twist64n", "twist256n", "twist16s", "twist64s", "twist256s"):
    assert est_podposl(kus10, c_massiv(g_bi, nm)), nm
kus13, s13 = T.kusok(r"Table 13\(a\): Parameters for de-multiplexing", r"Table 14\(a\)", posl=True)
for nm in ("mux16", "mux64", "mux256"):
    assert est_podposl(kus13, c_massiv(g_bi, nm)), nm
kus13b = T.kusok(r"Table 13\(b\)", r"Table 13\(c\)", posl=True)[0]
assert est_podposl(kus13b, c_massiv(g_bi, "mux16_35")) and est_podposl(kus13b, c_massiv(g_bi, "mux64_35"))
ZAPISI.append(Z("DVB-T2 битовый перемежитель: чётность + столбцовый со скручиванием + демультиплексор бит в ячейки", "DVB-T2 (EN 302 755)", "перемежитель",
  {"чётность": "u(Kldpc + 360·t + s) = λ(Kldpc + Qldpc·s + t)", "столбцовый": "Nc столбцов (табл. 9), запись по столбцам со сдвигом tc (табл. 10), чтение по строкам; для QPSK — не применяется",
   "tc": {nm: c_massiv(g_bi, nm) for nm in ("twist16n", "twist64n", "twist256n", "twist16s", "twist64s", "twist256s")},
   "демультиплексор": {"13a (1/2,3/4,4/5,5/6)": {nm: c_massiv(g_bi, nm) for nm in ("mux16", "mux64", "mux256")}, "13b (3/5)": {"16": c_massiv(g_bi, "mux16_35"), "64": c_massiv(g_bi, "mux64_35")}}},
  "DVB-T2, T2-Lite (табл. I.2), DVB-C2 (аналогично)", [T.ist(s10, "Table 10"), T.ist(s13, "Table 13(a)"), ist_kod(g_bi, r"twist16n\[8\]", "gr-dtv")],
  "каждая строка tc и перестановки демультиплексора gr-dtv найдены подряд в тексте табл. 10/13", "частично: перемежение бит DVB-S2 есть; T2-скручивание — нет"))

# --- частотный перемежитель ----------------------------------------------------------------
kus53, s53 = T.kusok(r"Table 53\(a\): Bit permutations for the 1k mode", r"Table 54", posl=True)
okfi = []
for mode, nb in (("1k", 9), ("2k", 10), ("4k", 11), ("8k", 12), ("16k", 13)):
    for par in ("even", "odd"):
        g = c_massiv(g_fi, f"bitperm{mode}{par}")
        # в стандарте строка «R'i: nb−1 … 0» и строка «Ri (H0/H1)»: gr хранит g[j] = позиция R для R'[j]
        # в тексте H0/H1 перечислены для R' = nb−1…0 → это g[::-1]
        assert est_podposl(kus53, g[::-1]), (mode, par)
        okfi.append(f"{mode}/{par}")
g32 = c_massiv(g_fi, "bitperm32k"); assert est_podposl(kus53, g32[::-1])
ZAPISI.append(Z("DVB-T2 частотный перемежитель: ГПСП с перестановкой бит H0/H1 (1K…32K)", "DVB-T2 (EN 302 755)", "перемежитель",
  {"вид": "H(q) из ГПСП R' длины Nr = log2 Mmax с перестановкой бит (табл. 53a–f); для чётных/нечётных символов разные перестановки H0/H1 (кроме 32K)",
   "Mmax": "табл. 52 (1024…32768)"}, "DVB-T2, T2-Lite", [T.ist(s53, "Table 53"), ist_kod(g_fi, r"bitperm1keven", "gr-dtv")],
  "перестановки бит всех режимов (" + ", ".join(okfi) + ", 32k) из gr-dtv найдены в тексте табл. 53", "нет"))

# --- P1 S1/S2 ----------------------------------------------------------------------------------
kus69, s69 = T.kusok(r"Table 69: S1 and S2 Modulation patterns", r"The bit sequences CSSS1", posl=True)
hx = re.findall(r"\b[0-9A-F]{16,64}\b", kus69)
s1 = [h for h in hx if len(h) == 16]; s2 = [h for h in hx if len(h) == 64]
assert len(s1) == 8 and len(s2) == 16, (len(s1), len(s2))
gs1 = c_massiv_2d(g_p1, "s1_modulation_patterns"); gs2 = c_massiv_2d(g_p1, "s2_modulation_patterns")
assert ["".join(f"{b:02X}" for b in r) for r in gs1] == s1 and ["".join(f"{b:02X}" for b in r) for r in gs2] == s2
ZAPISI.append(Z("DVB-T2 преамбула P1: последовательности S1 (8×64 бит) и S2 (16×256 бит)", "DVB-T2 (EN 302 755)", "блочный код (набор кодовых слов, дополнительные последовательности)",
  {"S1": s1, "S2": s2, "порядок бит": "слева направо, от старшего бита первой шестнадцатеричной цифры", "модуляция": "DBPSK на 384 активных несущих из 1K + скремблирование ПСП"},
  "DVB-T2 (обнаружение сигнала, тип преамбулы, FFT, SISO/MISO, FEF), T2-Lite",
  [T.ist(s69, "Table 69"), ist_kod(g_p1, r"s1_modulation_patterns\[8\]\[8\]", "gr-dtv")], "8 + 16 шестнадцатеричных последовательностей из текста совпали с массивами gr-dtv", "нет"))

# --- L1 -----------------------------------------------------------------------------------------
kus39, s39 = T.kusok(r"Table 39: Code parameters \(Kbch, Kldpc\) for L1-pre and L1-post", r"For 0", posl=True)
assert est_podposl(kus39, [3, 72, 3, 240]) or ("3 072" in kus39 and "7 032" in kus39)
kus42, s42 = T.kusok(r"Table 42: Permutation sequence of parity group to be punctured for L1-pre", r"Table 43", posl=True)
assert est_podposl(kus42, c_massiv(g_fm, "pre_puncture"))
kus40, s40 = T.kusok(r"Table 41: Permutation sequence of information bit group to be padded for L1-post", r"Table 42", posl=True)
for nm in ("post_padding_bqpsk", "post_padding_16qam", "post_padding_64qam"):
    assert est_podposl(kus40, c_massiv(g_fm, nm)), nm
kus43, s43 = T.kusok(r"Table 43: Permutation sequence of parity group to be punctured for L1-post", r"Table 44", posl=True)
for nm in ("post_puncture_bqpsk", "post_puncture_16qam", "post_puncture_64qam"):
    assert est_podposl(kus43, c_massiv(g_fm, nm)), nm
ZAPISI.append(Z("DVB-T2 защита L1-pre/L1-post: БЧХ укороченный + LDPC 16200 R=1/4 (L1-pre) / 1/2 (L1-post) с укорочением и выкалыванием", "DVB-T2 (EN 302 755)", "каскадный: БЧХ + LDPC с укорочением/выкалыванием",
  {"L1-pre": "Kbch = 3072, Kldpc = 3240 (код 16K 1/4), BPSK", "L1-post": "Kbch = 7032, Kldpc = 7200 (код 16K 1/2), BPSK/QPSK/16/64-QAM",
   "укорочение": "группы информационных бит, заполняемые нулями — по перестановке πS (табл. 40, 41)", "выкалывание": "группы чётности — по перестановке πP (табл. 42, 43)",
   "перестановки": {"pre_puncture": c_massiv(g_fm, "pre_puncture"), **{nm: c_massiv(g_fm, nm) for nm in ("post_padding_bqpsk", "post_padding_16qam", "post_padding_64qam", "post_puncture_bqpsk", "post_puncture_16qam", "post_puncture_64qam")}},
   "CRC": "CRC-32 в конце L1-pre и L1-post (Annex F)"},
  "DVB-T2 P2-символы", [T.ist(s39, "Table 39"), T.ist(s40, "Table 41"), T.ist(s42, "Table 42"), T.ist(s43, "Table 43"), ist_kod(g_fm, r"pre_puncture\[36\]", "gr-dtv")],
  "перестановки укорочения и выкалывания gr-dtv найдены подряд в тексте табл. 41–43", "нет"))

kusF, sF = T.kusok(r"The CRC codes used in the DVB-T2 system are based on the following polynomials", r"Annex G", posl=True)
chisla = [int(x) for x in re.findall(r"\d+", kusF)]
assert est_podposl(kusF, [2, 4, 5, 7, 8, 10, 11, 12, 16, 22, 23, 26, 32])
ZAPISI.append(Z("DVB-T2 CRC-8 (UP, BBHEADER) и CRC-32 (L1)", "DVB-T2 (EN 302 755)", "CRC-подобный",
  {"CRC-32": "x^32+x^26+x^23+x^22+x^16+x^12+x^11+x^10+x^8+x^7+x^5+x^4+x^2+x+1, начальное все единицы, MSB первым, без инверсии",
   "CRC-8": "x^8+x^7+x^6+x^4+x^2+1 (0xD5, как DVB-S2), начальное нули", "BBHEADER": "поле CRC-8 XOR MODE (признак NM/HEM)"},
  "DVB-T2 UP (пакеты TS/GFPS), BBHEADER, L1-pre/L1-post", [T.ist(sF, "Annex F")],
  "степени CRC-32 найдены подряд в тексте Annex F; CRC-8 — как DVB-S2 (EN 302 307)", "есть: crc_katalog (CRC-32/MPEG-2 с init 0xFFFFFFFF; CRC-8 DVB-S2); dvbs2.py проверяет CRC-8 BBHEADER"))
