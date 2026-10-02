"""DAB / DAB+ / T-DMB (EN 300 401 V2.2.1, TS 102 563, TS 102 427): свёрточный код 1/4 K=7 с выкалыванием PI 1…24,
энергодисперсия x^9+x^5+1, временное и частотное перемежение, CRC FIB/пакетов, RS(120,110) и код Файра DAB+,
RS(204,188)+Форни T-DMB. Сверка — welle.io (GPL-2), вычисление тестовых векторов стандарта."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

D = Tekst("istochniki/dab/en_300401v020201p.pdf"); P = Tekst("istochniki/dab/ts_102563v020101p.pdf"); M = Tekst("istochniki/dab/ts_102427v010101p.pdf")
W = "welle.io"; ZAPISI = []
w_pt = kod(W, "src/backend/protTables.cpp"); w_ed = kod(W, "src/backend/energy_dispersal.h"); w_fic = kod(W, "src/backend/fic-handler.cpp")
w_vit = kod(W, "src/backend/viterbi.cpp"); w_fi = kod(W, "src/backend/freq-interleaver.cpp"); w_au = kod(W, "src/backend/dab-audio.cpp")
w_tl = kod(W, "src/backend/tools.cpp"); w_dp = kod(W, "src/backend/dabplus_decoder.cpp")

# --- свёрточный код и выкалывание -------------------------------------------------------
s_g, _, _ = D.odna(r"The octal forms of the generator polynomials are 133, 171, 145 and 133, respectively")
kus, s_t = D.kusok(r"Table 13: Puncturing vectors", r"The last 24 bits of the serial mother codeword", posl=True)
pi = {}
for m in re.finditer(r"PI=(\d+):\s*\ncode rate: 8/(\d+)\s*\n([01 ]+)\n", kus):
    v = m.group(3).replace(" ", "").strip(); pi[int(m.group(1))] = v
    assert len(v) == 32 and v.count("1") == int(m.group(2)), m.group(0)
assert sorted(pi) == list(range(1, 25))
wpt = c_massiv(w_pt, "p_codes"); assert len(wpt) == 24 * 32
assert all("".join(map(str, wpt[i * 32:(i + 1) * 32])) == pi[i + 1] for i in range(24))
wx = c_massiv(w_fic, "PI_X"); assert "".join(map(str, wx)) == "110" * 0 + "110011001100110011001100"
t_vit = open(os.path.join(KOREN, w_vit)).read(); assert "0133, 0171, 0145, 0133" in t_vit
tab = tablica("dab_vykalyvanie_PI", {"PI": pi, "PI_X (хвост 24 бита)": "".join(map(str, wx))},
  "Векторы выкалывания DAB (табл. 13): PI = 1…24, 32 бита на 4 блока по 8 бит × 4 ветви, скорость 8/(8+PI)",
  [D.ist(s_t, "Table 13")], "разобрано из текста (32 бита, число единиц = 8+PI) и совпало с p_codes welle.io; хвост 1100×6 = PI_X welle.io")
ZAPISI.append(Z("DAB свёрточный код 1/4 K=7 (133,171,145,133) с выкалыванием PI 1…24 (8/9…8/32)", "DAB/DAB+/T-DMB (EN 300 401)", "свёрточный с выкалыванием",
  {"многочлены": "g0=133, g1=171, g2=145, g3=133 (восьмерично)", "материнская скорость": "1/4", "хвост": "6 нулевых бит → 24 бита, выкалываются вектором 1100 1100 1100 1100 1100 1100 (12 бит)",
   "выкалывание": tab, "профили": "UEP (табл. 15–16: L1..L4 и PI1..PI4 для 32…384 кбит/с), EEP-A/B (табл. 18–20); FIC режим I: 21 блок PI=16 + 3 блока PI=15 (~1/3)",
   "порядок": "сериализация x0,x1,x2,x3 по битам"},
  "DAB, DAB+, T-DMB, DAB-IP (EN 300 401 — все подканалы MSC и FIC)", [D.ist(s_g, "11.1.1"), D.ist(s_t, "Table 13"), ist_kod(w_pt, r"p_codes\[24\]\[32\]", "welle.io"), ist_kod(w_vit, r"0133, 0171, 0145, 0133", "welle.io POLYS")],
  "24 вектора выкалывания разобраны из текста и совпали с welle.io; многочлены = POLYS welle.io",
  "частично: svyortka.py (1/n до 4, многочлены 133/171/145 находятся), vykalyvanie.py (k/(k+1)); 32-битные векторы PI DAB — нет"))

# --- энергодисперсия ---------------------------------------------------------------------------
s_e, _, _ = D.odna(r"The PRBS shall be defined as the output of the feedback shift register of figure 54")
kus12, s12 = D.kusok(r"Table 12: First 16 bits of the PRBS", r"10\.2", posl=True)
perv = "".join(re.findall(r"\b[01]\b", kus12.split("bit value")[1]))[:16]
sr = [1] * 9; out = ""
for _ in range(16):
    b = sr[8] ^ sr[4]; sr = [b] + sr[:-1]; out += str(b)
assert out == perv == "0000011110111110", (out, perv)
ZAPISI.append(Z("DAB энергодисперсия: ПСП x^9 + x^5 + 1, все единицы", "DAB/DAB+/T-DMB (EN 300 401)", "скремблер (рандомизатор)",
  {"многочлен": "P(x) = x^9 + x^5 + 1", "начало": "все разряды 1; сброс на каждый логический кадр (24 мс) каждого подканала и на каждые 3 FIB (768 бит) FIC",
   "первые 16 бит": perv}, "DAB, DAB+, T-DMB", [D.ist(s_e, "10.1"), D.ist(s12, "Table 12"), ist_kod(w_ed, r"shiftRegister\[8\] \^ shiftRegister\[4\]", "welle.io")],
  "первые 16 бит ПСП по табл. 12 воспроизведены алгоритмом welle.io (отводы 9 и 5, все единицы)", "частично: skrembler.py (аддитивный скремблер вслепую; x^9+x^5+1 находится)"))

# --- перемежение -----------------------------------------------------------------------------------
kus21, s21 = D.kusok(r"Table 21: Relationship between the indices r', r and ir", r"The following shall apply", posl=True)
zad = [int(x) for x in re.findall(r"r-(\d+)", kus21)]
imap = c_massiv(w_au, "interleaveMap")
assert [0] + zad == imap == [0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15], (zad, imap)
kus25, s25 = D.kusok(r"Table 25: Frequency interleaving for transmission mode I", r"=====СТР", posl=True)
pi_t = []
for m in re.finditer(r"(?m)^\s*(\d+)\s*\n\s*(\d[\d ]*?)\s*\n", kus25):
    pass
# Π(i+1) = (13·Π(i) + 511) mod 2048 (режим I) — проверка на значениях таблицы 25
Pi = [0]
for _ in range(20): Pi.append((13 * Pi[-1] + 511) % 2048)
t25 = re.sub(r"(\d) (\d{3})", r"\1\2", kus25)
assert all(str(Pi[i]) in t25 for i in (1, 2, 3, 4, 5, 6, 7, 8, 12)), Pi[:13]
opechatka = "1076" in t25 and Pi[13] == 1067 and "1067" in t25
t_fi = open(os.path.join(KOREN, w_fi)).read(); assert "(13 * tmp[i - 1] + V1) % T_u" in t_fi and "511, 256, 256 + param.K" in t_fi
s_fi, _, _ = D.odna(r"Let Π\(i\) be a permutation in the set of integers i = 0, 1, 2,\.\.\., 2047")
ZAPISI.append(Z("DAB временной (16 ветвей, задержки 0,8,4,12,…) и частотный (Π(i)=13Π(i−1)+V1 mod Tu) перемежители", "DAB/DAB+/T-DMB (EN 300 401)", "перемежитель",
  {"временной": "бит i логического кадра задерживается на 16 − 1 − … : индекс ir = (i mod 16) → задержка из {0,8,4,12,2,10,6,14,1,9,5,13,3,11,7,15} кадров (бит-реверс 4 бит); глубина 16 кадров = 384 мс; FIC не перемежается",
   "частотный": "Π(i) = (13·Π(i−1) + V1) mod Tu, Π(0) = 0; режим I: Tu = 2048, V1 = 511, отбор 256 ≤ Π ≤ 1792, Π ≠ 1024; режимы II–IV — Tu = 512/256/1024 (V1 = 127/63/255)",
   "примечание": "в табл. 25 EN 300 401 V2.2.1 напечатано Π(13) = 1 076, по формуле и по столбцу dn — 1 067 (опечатка в столбце Π)" if opechatka else ""},
  "DAB, DAB+, T-DMB", [D.ist(s21, "Table 21"), D.ist(s_fi, "14.6"), D.ist(s25, "Table 25"), ist_kod(w_au, r"interleaveMap\[\]", "welle.io"), ist_kod(w_fi, r"13 \* tmp", "welle.io")],
  "задержки из табл. 21 = interleaveMap welle.io; формула Π проверена на значениях табл. 25 (Π(1…8), Π(12))" + ("; найдена опечатка Π(13)" if opechatka else ""), "нет"))

# --- CRC --------------------------------------------------------------------------------------------
s_c, _, _ = D.odna(r"CRC: a 16-bit Cyclic Redundancy Check word is calculated on the FIB data field")
s_pc, _, _ = D.odna(r"The packet CRC shall be a 16-bit CRC word calculated on the packet header and the packet data field")
s_dg, _, _ = D.odna(r"The data group CRC shall be a 16-bit CRC word calculated on the data group header")
t_tl = open(os.path.join(KOREN, w_tl)).read(); assert "CalcCRC_CRC16_CCITT(true, true, 0x1021)" in t_tl
ZAPISI.append(Z("DAB CRC-16 (X.25): FIB, пакеты MSC, группы данных MSC, X-PAD, AU DAB+", "DAB/DAB+/T-DMB (EN 300 401)", "CRC-подобный",
  {"многочлен": "x^16 + x^12 + x^5 + 1", "начальное": "все единицы", "выход": "инверсия (дополнение до 1)", "порядок": "старший бит первым (CRC-16/GENIBUS = CRC-16/X-25 без отражения)",
   "где": "FIB (240 бит данных + 16 CRC), пакеты MSC, MSC data group, X-PAD data group, AU DAB+ (TS 102 563)"},
  "DAB, DAB+", [D.ist(s_c, "5.2.1 FIB CRC"), D.ist(s_pc, "5.3.2.? packet CRC"), D.ist(s_dg, "5.3.3.? data group CRC"), ist_kod(w_tl, r"CalcCRC_CRC16_CCITT\(true, true, 0x1021\)", "welle.io: init FFFF, инверсия")],
  "параметры из текста; welle.io: 0x1021, начальное 0xFFFF, инверсия результата", "есть: crc_katalog (CRC-16/GENIBUS — RevEng: 0x1021, init 0xFFFF, xorout 0xFFFF, без отражения)"))

# --- DAB+ ------------------------------------------------------------------------------------------
s_rs, _, _ = P.odna(r"Reed-Solomon RS\(120, 110, t = 5\) shortened code")
s_fc, _, _ = P.odna(r"The Fire code shall be generated using the polynomial")
s_fi0, _, _ = P.odna(r"At the beginning of each Fire code word calculation, all register stages shall be initialized to \"0\"")
def pmul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r
G = pmul((1 << 11) | 1, mnogochlen([5, 3, 2, 1, 0]))
assert G == 0x1782F, hex(G)
kus_f, _ = P.kusok(r"The Fire code shall be generated using the polynomial", r"The Fire code word shall be calculated")
assert est_podposl(kus_f, [2, 3, 5, 11, 12, 13, 14, 16])
t_dp = open(os.path.join(KOREN, w_dp)).read(); assert "init_rs_char(8, 0x11D, 0, 1, 10, 135)" in t_dp
assert "CalcCRC_FIRE_CODE(false, false, 0x782F)" in t_tl
ZAPISI.append(Z("DAB+ суперкадр: RS(120,110) t=5 с виртуальным перемежением + код Файра (x^11+1)(x^5+x^3+x^2+x+1)", "DAB+ (TS 102 563)", "каскадный: РС + код Файра",
  {"РС": "RS(120,110) укороченный из (255,245), G(x) = ∏_{i=0}^{9}(x + α^i), α = 2, GF(2^8) p(x) = x^8+x^4+x^3+x^2+1; байтовое «виртуальное» перемежение: матрица subch_index строк × 120 столбцов, RS по столбцам",
   "Файр": "G(x) = (x^11+1)(x^5+x^3+x^2+x+1) = x^16+x^14+x^13+x^12+x^11+x^5+x^3+x^2+x+1 (0x782F), по байтам 2…10 суперкадра, начальное 0; исправляет пакеты ошибок ≤ 6 бит — служит синхрословом суперкадра",
   "AU": "CRC-16 X.25 в конце каждого AU (HE-AAC v2)"},
  "DAB+ (аудио HE-AAC), DMB-аудио", [P.ist(s_rs, "6.1 RS"), P.ist(s_fc, "5.2 Fire"), P.ist(s_fi0, "начальное 0"), ist_kod(w_dp, r"init_rs_char\(8, 0x11D", "welle.io RS"), ist_kod(w_tl, r"CalcCRC_FIRE_CODE", "welle.io Файр")],
  "произведение (x^11+1)(x^5+x^3+x^2+x+1) = 0x1782F совпало с 0x782F welle.io и со степенями в тексте; RS: welle.io init_rs_char(8, 0x11D, fcr 0, prim 1, 10 корней, укорочение 135)",
  "частично: rs_bch.py (RS вслепую), crc.py (CRC 16 бит вслепую — Файр найдётся как CRC 0x782F)"))

s_t1, _, _ = M.odna(r"Reed-Solomon RS \(204,188, t = 8\) shortened code")
s_t2, _, _ = M.odna(r"with depth j ⋅ M cells where M = 17 = N/I, N = 204")
ZAPISI.append(Z("T-DMB внешний код: RS(204,188) + свёрточный перемежитель I=12 (поток MPEG-TS в подканале DAB)", "T-DMB (TS 102 427)", "каскадный: РС + перемежитель",
  {"РС": "(204,188) t=8, как DVB (GF(256), 0x11D)", "перемежитель": "Форни I = 12, M = 17", "далее": "поток в подканал DAB (EEP) → свёрточный код DAB"},
  "T-DMB (Корея), DAB-видео", [M.ist(s_t1, "RS(204,188)"), M.ist(s_t2, "Форни I=12, M=17")], "параметры из текста; код и перемежитель совпадают с DVB (запись DVB-T)", "есть: dvb.py (RS(204,188) и Форни I=12)"))
