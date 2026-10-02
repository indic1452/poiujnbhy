"""Носители: ECMA-130 (CD-ROM: CIRC, RSPC, EDC), ECMA-267 (DVD: RS-PC), ECMA-365 (UMD), ECMA-139 (DDS, скан → OCR),
ECMA-319 (LTO-1), ECMA-380 (UDO), Blu-ray (по патенту US 2006/0282614 — структура LDC/BIS). Проверки: коэффициенты
порождающих многочленов, напечатанные в стандартах (LTO, UDO), вычислены заново; EDC CD — разложение многочлена."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "Носители информации (CD/DVD/UMD/BD/лента/UDO)"


def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": SEM, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


CD = Tekst("istochniki/ecma/ECMA-130_2nd_edition_june_1996.pdf")
DVD = Tekst("istochniki/ecma/ECMA-267_3rd_edition_april_2001.pdf")
UMD = Tekst("istochniki/ecma/ECMA-365_1st_edition_june_2005.pdf")
LTO = Tekst("istochniki/ecma/ECMA-319_1st_edition_june_2001.pdf")
UDO = Tekst("istochniki/ecma/ECMA-380_1st_edition_december_2007.pdf")
DDS = Tekst("istochniki/ecma/ECMA-139_1st_edition_june_1990.pdf", ocr=True)
BD = Tekst("istochniki/nositeli/US20060282614A1.pdf")
exp, log = pole(0x11D)
RS_PROJ = "частично: RS над GF(256) и исправление (rs_bch.py); перемежения/кадры носителя нет"

# ---- CD ----
sC = CD.gde("The error correction encoding of the F1-frames is carried out by a Cross Interleaved Reed-Solomon Code (CIRC)")
sC1 = CD.gde("The error correction encoder C1 generates a (32,28) Reed-Solomon code")
sE = CD.gde("P(x) = (x16 + x15 + x2 + 1) . (x16 + x2 + x + 1)")
edc = poly_mul2(iz_stepenej([16, 15, 2, 0]), iz_stepenej([16, 2, 1, 0]))
assert edc == iz_stepenej([32, 31, 16, 15, 4, 3, 1, 0])
sR = CD.gde("The RSPC is a product code over GF(28) producing P- and Q-parity bytes")
sQ = CD.gde("which are (26, 24) Reed-Solomon code words over GF(28)")
rec("CD (ECMA-130 / IEC 908): CIRC — C2 RS(28,24) и C1 RS(32,28) с перекрёстным перемежением", "каскадный: РС + РС (перекрёстное перемежение)",
    {"поле": "GF(2^8), x^8 + x^4 + x^3 + x^2 + 1, α = 00000010", "C1/C2": "проверочные матрицы H_P, H_Q со строками α^0…α^3 → корни α^0, α^1, α^2, α^3 (снимок risunki/ecma130_HP_HQ.png)",
     "перемежение": "1-я секция: половина слов задерживается на 2 кадра F1; 2-я: 28 задержек 0…27·D, D = 4 кадра; 3-я: каждый второй байт после C1 задержан на 1 кадр; все биты P и Q инвертируются (C.8–C.9)",
     "кадр F1": "24 байта (12 слов A/B)", "далее": "EFM-модуляция"},
    "CD-DA, CD-ROM, CD-R/RW", [CD.ist(sC, "Annex C"), CD.ist(sC1, "C.6")], "по тексту и снимку матриц; корни α^0…α^3 одинаковы у C1 и C2 (различаются длины)", RS_PROJ)
rec("CD-ROM сектор (ECMA-130 Mode 1): EDC CRC-32 (x^16+x^15+x^2+1)(x^16+x^2+x+1) + RSPC P(26,24) × Q(45,43) + скремблер", "каскадный: CRC + произведение РС",
    {"EDC": "P(x) = (x^16+x^15+x^2+1)(x^16+x^2+x+1) = x^32+x^31+x^16+x^15+x^4+x^3+x+1 над байтами 0…2063, младший бит первым",
     "RSPC": "2 × (MSB/LSB) матрица 43 столбца × 24: P-векторы (26,24) по столбцам, Q-векторы (45,43) по диагоналям; GF(2^8) x^8+x^4+x^3+x^2+1",
     "скремблер": "байты 12…2351, 15-разрядный регистр x^15 + x + 1, предустановка 0000 0000 0000 0001 после синхро (Annex B)", "подкод Q": "CRC G(x) = x^16 + x^12 + x^5 + 1"},
    "CD-ROM Mode 1 / CD-ROM XA Form 1", [CD.ist(sE, "14.3 EDC"), CD.ist(sR, "Annex A RSPC"), CD.ist(sQ, "P-векторы"), CD.ist(CD.gde("G(x) = x16 + x12 + x5 + 1"), "подкод Q"), CD.ist(CD.gde("fed back according to polynomial x15 + x + 1"), "Annex B скремблер")],
    "произведение двух множителей EDC раскрыто: x^32+x^31+x^16+x^15+x^4+x^3+x+1", "частично: CRC-32 любые (crc.py), RS — есть; RSPC-раскладки нет")

# ---- DVD / UMD ----
sD = DVD.gde("form the outer code RS (208,192,17)")
sDe = DVD.gde("G(x) = x32 + x31 + x4 + 1")
rec("DVD-ROM (ECMA-267): произведение RS-PC — внешний RS(208,192,17) по столбцам + внутренний RS(182,172,11) по строкам; EDC x^32+x^31+x^4+1", "произведение РС",
    {"поле": "GF(2^8), x^8 + x^4 + x^3 + x^2 + 1", "PO": "G_PO(x) = ∏_{k=0}^{15}(x + α^k), 16 байт на каждый из 172 столбцов", "PI": "G_PI(x) = ∏_{k=0}^{9}(x + α^k), 10 байт на каждую из 208 строк",
     "блок ECC": "16 секторов × (12 × 172) + PO/PI = 208 × 182", "EDC": "G(x) = x^32 + x^31 + x^4 + 1 по ID + данным", "ID": "IED: RS(6,4), G_E(x) = ∏_{k=0}^{1}(x + α^k)", "скремблер": "15-разрядный регистр, r0 ← r14 ⊕ r10 (x^15 + x^11 + 1, взаимный x^15 + x^4 + 1), 16 предустановок табл. 3 по битам b7…b4 ID; сдвиг на 8 бит на байт (рис. 19, снимок risunki/ecma267_skr.png)"},
    "DVD-ROM/R/RW/RAM", [DVD.ist(sD, "ECC блок"), DVD.ist(sDe, "EDC"), DVD.ist(DVD.gde("ID Error Detection Code (IED)"), "IED"), DVD.ist(DVD.gde("Table 3 - Initial values of the shift register"), "скремблер")], "по тексту (формулы G_PO, G_PI в тексте)", RS_PROJ)
rec("UMD (ECMA-365): RS-PC как DVD — RS(208,192,17) × RS(182,172,11)", "произведение РС",
    {"код": "идентичен DVD (G_PO, G_PI по ∏(x + α^k))"}, "Universal Media Disc (PSP)", [UMD.ist(UMD.gde("form the outer code RS (208,192,17)"), "ECC"), UMD.ist(UMD.gde("the inner code RS (182,172,11)"))],
    "совпадение с ECMA-267 по тексту", RS_PROJ)

# ---- LTO-1 ----
def koef(nroots, fcr): return [log[c] for c in rs_g(0x11D, fcr, nroots)[1:]]
assert koef(4, 126) == [201, 246, 201, 0]
assert koef(6, 125) == [36, 250, 254, 250, 36, 0]
assert koef(10, 123) == [119, 58, 160, 43, 223, 43, 160, 58, 119, 0]
sL1 = LTO.gde("First, a (240, 234, 7) Reed-Solomon code shall be applied to the 234 even numbered bytes")
sL2 = LTO.gde("A (64, 54, 11) Reed-Solomon shall be applied to each 54-byte column")
sL0 = LTO.gde("A 4-byte CRC shall be appended to each User Data Record to create a Protected Record")
rec("LTO-1 Ultrium (ECMA-319): C1 RS(240,234) (корни α^125…α^130) × C2 RS(64,54) (α^123…α^132); CRC записи — RS(N,N−4) (α^126…α^129)", "произведение РС",
    {"C1": "G = ∏_{i=125}^{130}(x+α^i) = x^6+α^36x^5+α^250x^4+α^254x^3+α^250x^2+α^36x+1; 2 перемеженных слова в строке 480 байт",
     "C2": "G = ∏_{i=123}^{132}(x+α^i) = x^10+α^119x^9+α^58x^8+α^160x^7+α^43x^6+α^223x^5+…+1; 54 → 64 строки", "CRC": "G = ∏_{i=126}^{129}(x+α^i) = x^4+α^201x^3+α^246x^2+α^201x+1",
     "структура": "Sub Data Set 64 × 480, 16 на Data Set"}, "ленточные накопители LTO-1",
    [LTO.ist(sL0, "13.2"), LTO.ist(sL1, "13.6.2"), LTO.ist(sL2, "13.6.3")],
    "все три записи коэффициентов из стандарта совпали с вычисленными по корням (симметричные корни → «палиндромные» коэффициенты)", RS_PROJ)

# ---- UDO ----
g_udo = rs_g(0x11D, 32, 4)
assert g_udo == [1, 0x68, 0x69, 0xF1, 0xDA]
sU = UDO.gde("x4 + (68) x3 + (69) x2 + (F1) x + (DA)")
sU2 = UDO.gde("The 1216 check bytes of the ECC shall be computed over the user bytes, the Control bytes, and the")
rec("UDO (ECMA-380): 38 перемеженных RS(248,216) (корни α^0…α^31) + CRC — RS 4 байта (α^32…α^35)", "РС (перемеженный)",
    {"ECC": "G_E(x) = ∏_{i=0}^{31}(x + α^i), 38 слов (216 инф. + 32 провер.) на сектор 8192 байт; 1216 проверочных", "CRC": "G_C(x) = ∏_{i=32}^{35}(x + α^i) = x^4 + 68x^3 + 69x^2 + F1x + DA (hex)",
     "перемежение": "38-кратное (Annex G.1)"}, "Ultra Density Optical 60 ГБ", [UDO.ist(sU, "G.2 CRC"), UDO.ist(sU2, "G.3 ECC")],
    "коэффициенты G_C (68, 69, F1, DA) из стандарта совпали с вычисленными", RS_PROJ)

# ---- DDS ----
sDD = DDS.gde("Cl shall be a GF (28) Reed-Solomon Code (32, 28, 5)")
sDD3 = DDS.gde("GF(28) (46, 44, 3)")
rec("DDS (ECMA-139, DAT-лента): C1 RS(32,28) (α^0…α^3), C2 RS(32,26) (α^0…α^5); ECC3 RS(46,44) по дорожкам", "произведение РС",
    {"C1": "перемежение 2 байта, G_P = ∏_{i=0}^{3}(x − α^i)", "C2": "перемежение 4 блока, G_Q = ∏_{i=0}^{5}(x − α^i)", "ECC3": "GF(2^8) (46,44,3): исправление 2 любых дорожек группы",
     "поле": "x^8 + x^4 + x^3 + x^2 + 1"}, "DDS/DAT-ленты (3.81 мм)", [DDS.ist(sDD, "9.x C1/C2 (OCR)"), DDS.ist(sDD3, "ECC3 (OCR)")],
    "PDF — скан; текст получен OCR (tekst/ecma/…ocr.txt), формулы сверены со снимком risunki/ecma139_c1c2.png", RS_PROJ)

# ---- Blu-ray ----
sB = BD.gde("long distance code")
rec("Blu-ray: LDC RS(248,216,33) (304 столбца) + BIS RS(62,30,33) (24 столбца) — «пикетный» код", "каскадный РС (LDC + BIS, стирания по BIS)",
    {"LDC": "блок 304 столбца × 216 строк данных + 32 строки проверки = RS(248,216) по столбцам; кластер 152 × 496", "BIS": "адрес/управление 24 × 30 + 32 = RS(62,30); кластер 3 × 496",
     "физический кластер": "синхро + 4 секции ECC + 3 секции BIS («пикеты»)", "поле и корни": "спецификация BDA закрыта; в открытом патенте не даны"},
    "BD-ROM/R/RE", [BD.ist(sB, "описание уровня техники, рис. 1")], "только структура по патенту; многочлены не найдены в открытом доступе", "нет")

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
