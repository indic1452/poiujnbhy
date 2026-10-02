"""Военные, НАТО, авиация, морская, пейджинг, транкинг, Япония, PMR-сигнализация: сверка и вычисления."""
import json, os, re
from pv import *
from p_pmr import gf, rs_g, minpoly
from p_3gpp import dmin
T = lambda f: json.load(open(os.path.join(KOREN, "tablicy", f)))["данные"]
M = "istochniki/mil/"; A = "istochniki/aviaciya/"; MO = "istochniki/morskaya/"; PG = "istochniki/peydzhing/"
C110 = M + "MIL-STD-188_110C_CHG_NOTICE-1.047598.pdf"; B141 = M + "MIL-STD-188-141B.pdf"; S5066 = M + "STANAG_5066_v2.pdf"

z = zapis("MIL-STD-188-110 последовательный")
str_ok(z, C110, 33, "рис. 4", "FOR T1 X6+X4+X3+X+1", "FOR T2 X6+X5+X4+X3+1")
str_ok(z, C110, 34, "5.3.2.3.4 (в записи — стр. 6, это перечень рисунков)", "5.3.2.3.4 Interleave load")
str_ok(z, C110, 40, "табл. XI (в записи — стр. 6)", "TABLE XI. Assignment of designation symbols D1 and D2")
z = zapis("MIL-STD-188-110 прил. B")
str_ok(z, C110, 87, "B.5.2", "x4", sosedi=1)
E, Lg, mul = gf(0x13, 4); g = rs_g(0x13, 4, [1, 2, 3, 4])
ok(z["имя"], "вычислено ∏(x+α^i), i=1..4 над GF(16)/x^4+x+1 = x4 + α13x3 + α6x2 + α3x + α10", g == [1, E[13], E[6], E[3], E[10]], f"{[('α^%d' % Lg[c]) if c else 0 for c in g]}")
z = zapis("MIL-STD-188-110 прил. C")
str_ok(z, C110, 135, "табл. C-XV/C-XVI (в записи — стр. 113, это оглавление)", "TABLE C- XV. Interleaver size in bits", "TABLE C- XVI. Interleaver increment value")
str_ok(z, C110, 131, "C.5.3 (в записи — стр. 113)", "C.5.3 Coding and interleaving.")
z = zapis("MIL-STD-188-110 прил. D")
str_ok(z, C110, 206, "D.5.3.2.2 K=9", "(b0) T1 = x8+x6 + x5 + x4 + 1", "(b1) T2 = x8+x7 + x6 + x5 + x3 + x1+1")
str_ok(z, C110, 208, "табл. D-XLII (в записи — стр. 146, перечень таблиц)", "TABLE D-XLII. Puncture patterns.")
ok(z["имя"], "восьм. 561/753 = степеням T1/T2", int("561", 8) == st(8, 6, 5, 4, 0) and int("753", 8) == st(8, 7, 6, 5, 3, 1, 0))
z = zapis("ALE 2G: Голей (24,12)")
str_ok(z, B141, 74, "A.5.2.2.2", "g(x) = x11 + x9 + x7 + x6 + x5 + x + 1")
P = T("mil_188_141.json")["golay_P_A-6"]
G = [[1 if j == i else 0 for j in range(12)] + [int(c) for c in P[i]] for i in range(12)]
ok(z["имя"], "матрица P рис. A-6: d_min (24,12) = 8; каждая строка — кодовое слово g(x) (систематическая циклическая форма + чётность)", dmin(G) == 8, f"d_min = {dmin(G)}")
z = zapis("ALE 3G BW1")
str_ok(z, B141, 304, "C.5.1.4.3", "Bitout0 = x8+x7+x6+x3+1", "Bitout1 = x8+x7+x5+x4+x1+1", "Bitout2 = x8+x6+x5+x3+x2+x1+1")
z = zapis("ALE 3G BW2")
str_ok(z, B141, 311, "рис. C-11", "Bitout0: X7 + X4 + X3 + X2 + 1", "Bitout1: X7 + X5 + X4 + X3 + X2 +1", "Bitout2: X7 + X6 + X3 + X1 + 1", "Bitout3: X7 + X6 + X5 + X3 + X2 + X1 +1")
ok(z["имя"], "восьм. 235/275/313/357 = степеням", [int(x, 8) for x in ("235", "275", "313", "357")] == [st(7, 4, 3, 2, 0), st(7, 5, 4, 3, 2, 0), st(7, 6, 3, 1, 0), st(7, 6, 5, 3, 2, 1, 0)])
z = zapis("ALE 3G CRC-4/8/12/16/32")
str_ok(z, B141, 329, "C.5.2.2.6", "polynomial x4 + x3 + x + 1", "polynomial x8 + x7 + x4 + x3 + x + 1")
z = zapis("MIL-STD-188-181A")
str_ok(z, M + "MIL-STD-188-181A.004683.PDF", 24, "5.1.9.1", "P1 1111001 P2 1011011")
str_ok(z, M + "MIL-STD-188-181A.004683.PDF", 23, "5.1.9 SOM", "1110001000010001111010011011101100101", "000000101110100111001", "001101100001000010101")
ok(z["имя"], "1111001₂ = 171₈, 1011011₂ = 133₈", int("1111001", 2) == 0o171 and int("1011011", 2) == 0o133)
z = zapis("MIL-STD-188-220D (боевые")
str_ok(z, M + "MIL-STD-188-220D.024816.pdf", 102, "5.3.14.1", "g(x) = x11 + x10 + x6 + x5 + x4 + x2 + 1")
ok(z["имя"], "g делит x^23+1", pmod((1 << 23) | 1, st(11, 10, 6, 5, 4, 2, 0)) == 0)
z = zapis("MIL-STD-188-220D прил. K")
str_ok(z, M + "MIL-STD-188-220D.024816.pdf", 519, "K.3", "g(x) = 1 + X4 + X6 + X7 + X8")
str_ok(z, M + "MIL-STD-188-220D.024816.pdf", 489, "табл. J-I", "3 5 7 7 5 52 66 76 7 554 624 764")
gk = st(0, 4, 6, 7, 8)
ok(z["имя"], "g делит x^15+1; g = m1·m3 GF(16)/x^4+x+1 (БЧХ t=2, d=5)", pmod((1 << 15) | 1, gk) == 0 and gk == pmul(minpoly(0x13, 4, 1), minpoly(0x13, 4, 3)),
   f"m1·m3 = {bin(pmul(minpoly(0x13, 4, 1), minpoly(0x13, 4, 3)))}")
z = zapis("Link-11 (TADIL-A")
import re as _re
_t = open(os.path.join(KOREN, "tekst/mil/MIL-STD-188_203-1A.024841.pdf.ocr.txt")).read()
_pp = _re.split(r"=====СТР (\d+)=====", _t); _d = {int(_pp[i]): _pp[i + 1] for i in range(1, len(_pp), 2)}
ok(z["имя"], "OCR стр. 30: текст «G(X) = x5 +X +1» (растр — скан)", "G(X) = x5 +X +1" in _d[30])
_rows = [r for p in range(26, 31) for r in _re.findall(r"\b[01]{30}\b", _d.get(p, ""))]
def _fits(s, taps, deg):
    b = [int(c) for c in s]; return all(b[n + deg] == sum(b[n + deg - t] for t in taps) % 2 for n in range(len(b) - deg))
_n6 = sum(_fits(r, (1, 6), 6) for r in _rows); _n5 = sum(_fits(r, (4, 5), 5) or _fits(r, (1, 5), 5) for r in _rows)
ok(z["имя"], "строки табл. III (OCR стр. 26–30): рекуррентность a(n) = a(n−1) ⊕ a(n−6) (x^6+x^5+1 ↔ взаимный x^6+x+1); x^5+x+1 не подходит", _n6 == len(_rows) and _n5 < len(_rows) // 10, f"{_n6} из {len(_rows)} строк по степени 6, {_n5} — по степени 5")
ok(z["имя"], "поле «проверка» записи не противоречит параметрам (там не должно быть «x5+x+1» как подтверждённого)", "окна М-последовательности x5+x+1" not in z["проверка"], z["проверка"][-120:])
z = zapis("STANAG 5066 прил. G")
str_ok(z, S5066, 130, "G.5.1", "x6", sosedi=0)
z = zapis("STANAG 5066 D_PDU")
str_ok(z, S5066, 62, "C.3.1.5", "polynomial x16 + x12 + x5 + 1", "shift registers shall be initially set to all zeros", "excluding the sync bytes")
c = crc_bytes(bytes.fromhex("F0000047056402"), 0x1021, 16, init=0, refin=True, refout=True)
ok(z["имя"], "тестовый вектор V1.0.2: CRC(F0 00 00 47 05 64 02) = 0xCE5D (отражённый 0x1021, начальное 0)", c == 0xCE5D, hex(c))

# ---------- авиация ----------
z = zapis("Mode S / ADS-B 1090ES")
gm = 0x1FFF409
msg = int("8D406B902015A678D4D220AA4BDA", 16)
ok(z["имя"], "сообщение DF17 8D406B90…4BDA делится на G(x) = 0x1FFF409 (112 бит)", pmod(msg, gm) == 0)
ok(z["имя"], "0x1FFF409 = степеням записи", gm == st(24, 23, 22, 21, 20, 19, 18, 17, 16, 15, 14, 13, 12, 10, 3, 0))
z = zapis("UAT (978 МГц) ADS-B")
U = A + "ICAO_Annex10_Amdt82_UAT_SARP.pdf"
str_ok(z, U, 14, "12.4.4.1.1", "111010101100110111011010010011100010")
ok(z["имя"], "0xEACDDA4E2 = синхрослово текста", int("111010101100110111011010010011100010", 2) == 0xEACDDA4E2)
z = zapis("UAT восходящее")
str_ok(z, U, 16, "12.4.4.2.2.2", "P = 139")
ok(z["имя"], "синхро восходящего = инверсия нисходящего (36 бит)", int("000101010011001000100101101100011101", 2) == (~0xEACDDA4E2) & ((1 << 36) - 1))
z = zapis("КОСПАС-САРСАТ 406 МГц (1-е")
CS = A + "CS_T001_mar2021_406MHz_beacons.pdf"
str_ok(z, CS, 84, "пример B1", "22-bit remainder = 0001011001010101001001")
str_ok(z, CS, 85, "БЧХ-2", "g(x) = (1 + x + x6) (1 + x + x2 + x4+ x6) = (1 + x3 + x4 + x5 + x8 + x10 + x12)")
g1 = int("1001101101100111100011", 2)
ok(z["имя"], "g БЧХ-1 = НОК m1,m3,m5 над GF(128) (t=3) и делит x^127+1", pmod((1 << 127) | 1, g1) == 0 and g1 == pmul(pmul(minpoly(0x89, 7, 1), minpoly(0x89, 7, 3)), minpoly(0x89, 7, 5)))
ok(z["имя"], "БЧХ-2: (1+x+x6)(1+x+x2+x4+x6) = 1+x3+x4+x5+x8+x10+x12", pmul(st(0, 1, 6), st(0, 1, 2, 4, 6)) == st(0, 3, 4, 5, 8, 10, 12))
z = zapis("КОСПАС-САРСАТ 2-го")
str_ok(z, A + "CS_T018_mar2021_SGB.pdf", 62, "прил. B", "1110001111110101110000101110111110011110010010111")
gs = int("1110001111110101110000101110111110011110010010111", 2)
ok(z["имя"], "g(X) степени 48 делит x^255+1 (циклический код длины 255)", gs.bit_length() - 1 == 48 and pmod((1 << 255) | 1, gs) == 0)
z = zapis("VDL Mode 2")
str_ok(z, A + "en_30184101v010401p.pdf", 13, "5.3", "D8PSK", "3 bits per symbol", "least significant bit first")

# ---------- морская ----------
z = zapis("AIS: HDLC-кадр")
str_ok(z, MO + "R-REC-M.1371-6-202602.pdf", 27, "A2-3.2.2.6", "ISO/IEC 13239", "pre-set to one (1)")
z = zapis("ЦИВ (DSC)")
ok(z["имя"], "правило 10-элементного кода (проверочные = число нулей в 7 инф., старший первым): 128 разных слов, d_min ≥ 2", len({(s, bin(~s & 0x7F).count("1")) for s in range(128)}) == 128)
z = zapis("VDES (VDE-TER/SAT)")
V = MO + "R-REC-M.2092-2-202602.pdf"
str_ok(z, V, 22, "A2-1.2.5", "x32 + x26 + x23 + x22 + x16 + x12 + x11 + x10 + x8 + x7 + x5 + x4 + x2 + x + 1", "x16 + x15 + x2 + 1")
z = zapis("КВ морские данные M.1798 прил. 5")
str_ok(z, MO + "R-REC-M.1798-2-202102.pdf", 116, "4.2.3 скремблер", "x32+x31+x27+x26+1")
str_ok(z, MO + "R-REC-M.1798-2-202102.pdf", 118, "табл. 11", "RS (204,188)", "Convolutional code (K=7, r=1/2)", "Turbo-code (duo binary, r=1/2)")
ok(z["имя"], "в записи нет догадок «вероятно DVB»: многочлены RS/свёрточного в M.1798 прил. 5 не заданы (стр. 105–125 просмотрены)", "вероятно" not in z["проверка"], z["проверка"][:200])

# ---------- пейджинг / транкинг / Япония / PMR ----------
z = zapis("POCSAG (RPC 1)")
str_ok(z, PG + "R-REC-M.584-2.pdf", 4, "1.4", "x10 + x9 + x8 + x6 + x5 + x3 + 1")
gp = st(10, 9, 8, 6, 5, 3, 0)
def poc_ok(w): return pmod(w >> 1, gp) == 0 and bin(w).count("1") % 2 == 0
ok(z["имя"], "синхрослово 0x7CD215D8 и слово простоя 0x7A89C197 — кодовые слова (БЧХ + чётная чётность); 3551₈ = g", poc_ok(0x7CD215D8) and poc_ok(0x7A89C197) and int("3551", 8) == gp)
z = zapis("ERMES")
str_ok(z, PG + "ets_30013304e02p.pdf", 24, "6", "g(x) = x12 + x11 + x9 + x7 + x6 + x3 + x2 + 1", "The Hamming distance for the (30,18) code is 6")
ok(z["имя"], "g = g(31,21)·(x+1)² (вычислено)", st(12, 11, 9, 7, 6, 3, 2, 0) == pmul(gp, st(2, 0)))
z = zapis("MPT1327")
str_ok(z, "istochniki/trank/MPT1327.pdf", 29, "3.2.3", "X15 + X14 + X13 + X11 + X4 + X2 + 1", "is then inverted")
ok(z["имя"], "g делит x^63+1", pmod((1 << 63) | 1, st(15, 14, 13, 11, 4, 2, 0)) == 0)
z = zapis("EDACS (Ericsson/GE)")
ge = int("1010100111001", 2)
ok(z["имя"], "g = m1·m3 над GF(64)/x^6+x+1 (БЧХ (63,51) t=2)", ge == pmul(minpoly(0x43, 6, 1), minpoly(0x43, 6, 3)))
z = zapis("AMPS/TACS/JTACS")
ok(z["имя"], "g = 0x1539 = тот же БЧХ (63,51), что EDACS (m1·m3, GF(64))", 0x1539 == ge and 0x1539 == st(0, 3, 4, 5, 8, 10, 12))
z = zapis("C-Netz")
_m = pmul(minpoly(0x13, 4, 1), minpoly(0x13, 4, 3))
ok(z["имя"], "g = 0x117 = взаимный к m1·m3 над GF(16)/x^4+x+1 (= m1·m3 над x^4+x^3+1): БЧХ (15,7), d=5", 0x117 == int(format(_m, "09b")[::-1], 2), f"m1·m3 = {hex(_m)}")
z = zapis("PDC речь полноскоростная")
str_ok(z, "istochniki/yaponiya/ARIB_RCR_STD-27L_2of3.pdf", 519, "5.1.2.4", "g0 = 65 octal and g1= 57 octal", "g0(D) = 1 + D + D3 + D5", "g1(D) = 1 + D2 + D3 + D4 + D5")
z = zapis("DCS (цифровой кодовый")
str_ok(z, "istochniki/pmr/ts_103236v010101p.pdf", 8, "4.2.1", "Bits 12 to 10 are fixed at 1002", "(23,12) cyclic Golay code", "The LSB is transmitted first (bit 1)")
z = zapis("RD-LAP CRC0/CRC1/CRC2")
c1 = crc_bytes(b"123456789", 0x1021, 16, init=0, xorout=0xFFFF); c2 = crc_bytes(b"123456789", 0x04C11DB7, 32, init=0, xorout=0xFFFFFFFF)
c0 = crc_bytes(b"123456789", 0x19, 6, init=0, xorout=0x3F)
ok(z["имя"], "контрольные значения «123456789»: CRC1 = 0xCE3C, CRC2 = 0x765E7680, CRC0 = 0x3B (вычислено своим кодом)", (c1, c2, c0) == (0xCE3C, 0x765E7680, 0x3B), f"{hex(c1)} {hex(c2)} {hex(c0)}")
z = zapis("MDC-1200 (Motorola)")
c = crc_bytes(bytes([0x01, 0x80, 0x12, 0x34]), 0x1021, 16, init=0, refin=True, refout=True, xorout=0xFFFF)
c_b = crc_bytes(bytes([0x01, 0x00, 0x00, 0x23]), 0x1021, 16, init=0, refin=True, refout=True, xorout=0xFFFF)
ok(z["имя"], "CRC-16 (0x1021 отражённый, нач. 0, инверсия): 01 80 12 34 → байты 2E 3E; 01 00 00 23 → DD F0 (младший байт первым, вычислено)", (c & 0xFF, c >> 8, c_b & 0xFF, c_b >> 8) == (0x2E, 0x3E, 0xDD, 0xF0), f"{hex(c)} {hex(c_b)}")
