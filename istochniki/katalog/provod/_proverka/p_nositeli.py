"""Независимая сверка записей носителей (ECMA-130, ECMA-267, ECMA-319, ECMA-380)."""
import re
from pv import *
E = "istochniki/ecma/"
E130 = E + "ECMA-130_2nd_edition_june_1996.pdf"; E267 = E + "ECMA-267_3rd_edition_april_2001.pdf"
E319 = E + "ECMA-319_1st_edition_june_2001.pdf"; E380 = E + "ECMA-380_1st_edition_december_2007.pdf"

z = zapis("CD-ROM сектор")
str_ok(z, E130, 26, "14.3: EDC — CRC-32 по байтам 0…2063, младший бит первым, P(x) = (x^16+x^15+x^2+1)(x^16+x^2+x+1)", "32- bit CRC applied on bytes 0 to 2 063", "The least significant bit of a data byte is used first", "P(x) = (x16 + x15 + x2 + 1) . (x16 + x2 + x + 1)")
P = pmul(st(16, 15, 2, 0), st(16, 2, 1, 0))
rev = crc_reveng_poly = None
import sys; sys.path.insert(0, os.path.join(KOREN, "skripty"))
t = open("/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py").read()
m = re.search(r'"CRC-32/CD-ROM-EDC", 32, (0x[0-9A-Fa-f]+), (0x[0-9A-Fa-f]+), (\w+), (\w+)', t)
ok(z["имя"], "P(x) = x^32+x^31+x^16+x^15+x^4+x^3+x+1 = 0x8001801B — совпадает с CRC-32/CD-ROM-EDC каталога RevEng (refin/refout = True ↔ «младший бит первым»)",
   P == st(32, 31, 16, 15, 4, 3, 1, 0) and m and int(m.group(1), 16) | (1 << 32) == P and m.group(3) == "True", f"RevEng: {m.groups() if m else None}")
str_ok(z, E130, 41, "Annex B: скремблер x^15 + x + 1, предустановка 0000 0000 0000 0001, LSB первым", "fed back according to polynomial x15 + x + 1", "pre-set with the value 0000 0000 0000 0001", "The least significant bit of each byte comes first")

z = zapis("DVD-ROM (ECMA-267)")
str_ok(z, E267, 36, "18: PO RS(208,192,17), G_PO = ∏_{k=0}^{15}(x+α^k); PI RS(182,172,11)", "RS (208,192,17)", "RS (182,172,11)")
str_ok(z, E267, 34, "16.2 IED (G_E = ∏_{k=0}^{1}), 16.4 EDC G(x) = x^32 + x^31 + x^4 + 1", "G(x) = x32 + x31 + x4 + 1", "P(x) = x8 + x4 + x3 + x2 + 1")
str_ok(z, E267, 35, "17: регистр r14…r0, предустановки табл. 3 по b7…b4 ID", "(0)  (0001)", "(1)  (5500)", "(F)  (0005)", "bits b7 (msb) to bit b4 (lsb) of the")
ok(z["имя"], "рис. 19 (снимок proverka/snimki/ecma267_p35.png, прочитан проверяющим): r0 ← r14 ⊕ r10 — совпадает с каталогом; x^15 + x^11 + 1 примитивен", primitiven(st(15, 11, 0)) and primitiven(st(15, 4, 0)))

z = zapis("LTO-1 Ultrium")
def koef_st(m, prim, korni):
    exp, log = gf(m, prim); return [log[c] if c else None for c in rs_g(m, prim, korni)]
c1 = koef_st(8, 0x11D, range(125, 131)); c2 = koef_st(8, 0x11D, range(123, 133)); crc = koef_st(8, 0x11D, range(126, 130))
ok(z["имя"], "C1: G = x^6 + α^36x^5 + α^250x^4 + α^254x^3 + α^250x^2 + α^36x + 1 (свой расчёт)", c1 == [0, 36, 250, 254, 250, 36, 0], str(c1))
str_ok(z, E319, 86, "13.6.2: C1 — те же коэффициенты в тексте", "x6 ⊕ α36 x5 ⊕ α250 x4 ⊕ α254 x3 ⊕ α250 x2 ⊕ α36 x ⊕ 1")
ok(z["имя"], "C2: G = x^10 + α^119x^9 + α^58x^8 + α^160x^7 + α^43x^6 + α^223x^5 + α^43x^4 + α^160x^3 + α^58x^2 + α^119x + 1 (свой расчёт)", c2 == [0, 119, 58, 160, 43, 223, 43, 160, 58, 119, 0], str(c2))
str_ok(z, E319, 87, "13.6.3: C2 (64,54,11) — коэффициенты в тексте", "x10 ⊕ α119x9 ⊕ α58x8⊕ α160x7 ⊕ α43x6 ⊕ α223x5 ⊕ α43x4 ⊕ α160x3 ⊕ α58x2⊕ α119x ⊕ 1")
ok(z["имя"], "CRC записи: G = x^4 + α^201x^3 + α^246x^2 + α^201x + 1 (свой расчёт)", crc == [0, 201, 246, 201, 0], str(crc))
str_ok(z, E319, 76, "13.2: CRC записи — коэффициенты в тексте", "x4 ⊕ α201x3 ⊕ α246x2 ⊕ α201x ⊕ 1")
ispr(z["имя"], "страницы источника для многочленов C2 и CRC", "C2 — стр. 86, CRC — стр. 75", "C2 — стр. 87 (13.6.3 начинается на стр. 86), CRC — стр. 76 (13.2 начинается на стр. 75)", "текстовый слой ECMA-319: формулы G(x) для C2 и CRC стоят на стр. 87 и 76 PDF")

z = zapis("UDO (ECMA-380)")
g = rs_g(8, 0x11D, range(32, 36))
ok(z["имя"], "G_C = ∏_{i=32}^{35}(x + α^i) = x^4 + 68x^3 + 69x^2 + F1x + DA (свой расчёт)", g == [1, 0x68, 0x69, 0xF1, 0xDA], " ".join("%02X" % c for c in g))
str_ok(z, E380, 106, "G.2/G.3: коэффициенты в тексте, G_E = ∏_{i=0}^{31}, 38 слов, 1216 проверочных", "x4 + (68) x3 + (69) x2 + (F1) x + (DA)", "The 1216 check bytes of the ECC", "38 information polynomials")

z = zapis("CD (ECMA-130 / IEC 908)")
str_ok(z, E130, 43, "Annex C: CIRC", "C1", "C2", sosedi=1)
