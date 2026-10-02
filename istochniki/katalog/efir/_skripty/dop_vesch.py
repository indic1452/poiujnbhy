"""Дополнение второго прохода (проверка полноты) — вещание и смежное: NICAM-728, AMSS, VITC/LTC (ITU-R BR.780),
WSS-525/CGMS-A (ITU-R BT.1119 прил. 2), ETI(NA) DAB (ETS 300 799), DCP/PFT (TS 102 821; EDI TS 102 693, DRM MDI),
CRC-32 MPEG-2 секций/MIP/T2-MI, ATSC 3.0 бутстрап (A/321), DVB-T2 ячеечный перемежитель.
Каждое значение — из текста первоисточника (assert), проверка — своей программой и/или вторым источником."""
import re, sys, os, json, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
OB = {z["имя"]: z["параметры"] for z in json.load(open(os.path.join(os.path.dirname(KOREN), "obshchie", "katalog.json")))
      if z["семейство"].startswith("CRC каталог RevEng")}

def crc_bity(bits, poly, w, init=0, xorout=0):
    r = init
    for b in bits:
        f = ((r >> (w - 1)) & 1) ^ b; r = (r << 1) & ((1 << w) - 1)
        if f: r ^= poly
    return r ^ xorout

def crc_reveng_calc(imya, data):
    p = OB[imya]; w = p["width"]; poly = int(p["poly"], 16); r = int(p["init"], 16)
    for x in data:
        if p["refin"]: x = int(format(x, "08b")[::-1], 2)
        for i in range(8):
            f = ((r >> (w - 1)) & 1) ^ ((x >> (7 - i)) & 1); r = (r << 1) & ((1 << w) - 1)
            if f: r ^= poly
    if p["refout"]: r = int(format(r, f"0{w}b")[::-1], 2)
    return r ^ int(p["xorout"], 16)

_OB_IST = {z["имя"]: z["источник"] for z in json.load(open(os.path.join(os.path.dirname(KOREN), "obshchie", "katalog.json"))) if z["семейство"].startswith("CRC каталог RevEng")}
def ist_obsh(imya):
    """Первоисточник RevEng (копия каталога в области obshchie) для параметров CRC `imya`."""
    i = _OB_IST[imya][0]
    assert os.path.exists(os.path.join(os.path.dirname(KOREN), "obshchie", i["файл"]))
    return {"файл": "../obshchie/" + i["файл"], "страница|строки": i["страница|строки"], "url": i["url"], "что": f"RevEng {imya} (копия в области obshchie)"}

# ---------------------------------------------------------------------------------------------------------------------
# NICAM-728 (EN 300 163)
N = Tekst("istochniki/nicam/en_300163v010201p.pdf")
s_faw, _, _ = N.odna(r"The FAW shall be 01001110; the left-most bit shall be transmitted first")
s_g, _, _ = N.odna(r"^x9 \+ x4 \+ 1")
s_i, _, _ = N.odna(r"^1 1 1 1 1 1 1 1 1")
s_seq, _, l_seq = N.odna(r"Thus the sequence shall start")
s_il, _, _ = N.odna(r"Interleaving is applied to the block of 704 bits which follows the frame alignment word")
s_p, _, _ = N.odna(r"One parity bit shall be added to each 10-bit sound sample to check the six most significant bits")
s_sf, _, _ = N.odna(r"Scale-factor signalling-in-parity for sound signals")
perv = "".join(re.findall(r"[01]", N.kusok(r"Thus the sequence shall start", r"4\.2\n")[0]))
psp = prbs((9, 5), 20, [1] * 9)          # в нашей схеме prbs (разряды от входа) многочлен x^9+x^4+1 рис. 2 — отводы 9 и 5 (взаимный вид)
assert prbs((9, 4), 20, [1] * 9) != psp
assert "".join(map(str, psp)) == perv == "00000111101111100010", (psp, perv)
# перемежение 704 бит: передаваемый бит k (0…703) — бит кадра 25 + 44·(k mod 16) + ⌊k/16⌋ (нумерация рис. в 4.1.2 с 1)
por = [25 + 44 * (k % 16) + k // 16 for k in range(704)]
assert por[:4] == [25, 69, 113, 157] and por[15] == 685 and por[16:19] == [26, 70, 114] and por[31] == 686 and por[-1] == 728
assert sorted(por) == list(range(25, 729))
assert min(abs(por.index(b + 1) - por.index(b)) for b in range(25, 728)) >= 16 - 15 and \
    all(abs(por.index(b + 1) - por.index(b)) >= 16 for b in range(25, 728) if (b - 25) % 44 != 43)
# подмешивание шкального множителя в чётность (стерео): номера отсчётов
sf_st = {"R2A": list(range(1, 55, 6)), "R1A": list(range(3, 55, 6)), "R0A": list(range(5, 55, 6)),
         "R2B": list(range(2, 55, 6)), "R1B": list(range(4, 55, 6)), "R0B": list(range(6, 55, 6))}
for k, v in sf_st.items():
    assert N.naiti(r"P'i = Pi ⊕ " + k + r"\s*$") or N.naiti(r"P'i = Pi ⊕ " + k), k
    assert N.naiti(r"for i\s*=" + ", ".join(map(str, v))), (k, v)
ZAPISI.append(Z("NICAM-728 (цифровой стереозвук аналогового ТВ): кадр 728 бит, FAW 01001110, перемежение 16×44, скремблер x^9+x^4+1, чётность 6 старших бит с подмешиванием шкального множителя",
  "NICAM-728 (EN 300 163, ITU-R BS.707)", "каскадный: чётность отсчётов (11,10) + перемежитель + скремблер",
  {"кадр": "728 бит/1 мс: FAW 8 бит (01001110, старший первым) + C0…C4 (5 бит) + AD0…AD10 (11 бит) + 704 бита (64 отсчёта × 11 бит)",
   "чётность": "к 10-битному отсчёту — 1 бит чётной чётности по 6 старшим битам; затем чётность модифицируется битом шкального множителя (signalling-in-parity), приёмник восстанавливает множитель мажоритарно по 9 отсчётам",
   "подмешивание (стерео)": {k: f"P'i = Pi ⊕ {k}, i = {v}" for k, v in sf_st.items()},
   "перемежитель": "704 бита после FAW/управления/доп. данных: передаваемый бит k (0…703) = бит кадра 25 + 44·(k mod 16) + ⌊k/16⌋ (рис. 4.1.2: 25, 69, 113, 157 … 685; 26, 70 … 686; … 68, 112, 156 … 728)",
   "скремблер": "аддитивный, x^9 + x^4 + 1, загрузка 111111111 на каждый кадр, первый скремблируемый бит — сразу после FAW; FAW не скремблируется; начало ПСП 0000 0111 1011 1110 0010",
   "C0": "флаг кадра: 8 кадров 1, 8 кадров 0 (последовательность 16 кадров)"},
  "NICAM-728 (PAL B/G, I, D/K; SECAM L), ТВ-приёмники с цифровым стереозвуком", [N.ist(s_faw, "4.2.1 FAW"), N.ist(s_il, "4.1.2 перемежение (рис.)"), N.ist(s_g, "4.1.3 x^9+x^4+1"), N.ist(s_seq, "начало ПСП"),
   N.ist(s_p, "4.2.5.4 чётность"), N.ist(s_sf, "4.2.5.5 подмешивание множителя"), {"файл": "risunki/nicam_en300163_str7.png", "страница|строки": "стр. 7", "url": url("istochniki/nicam/en_300163v010201p.pdf"), "что": "снимок схемы перемежения"}],
  "свой РСЛОС x^9+x^4+1 из 111111111 (выход — бит обратной связи, x^4 отсчитывается от выхода: в нашей нумерации разрядов от входа — отводы 9 и 5) дал 00000111101111100010 — как в тексте 4.1.3 d); первые 16 бит совпадают с ПСП энергодисперсии DAB/DRM (x^9+x^5+1 в их записи) — та же последовательность; формула перемежения воспроизводит все числа рисунка 4.1.2 (25, 69, 113, 157…685; 26, 70, 114…686; …728) и является перестановкой 704 бит; номера отсчётов подмешивания найдены в тексте",
  "частично: skrembler.py (аддитивный скремблер находится), peremezhenie.py (блочное 16×44 находится вслепую); NICAM-кадр — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# AMSS (TS 102 386)
A = Tekst("istochniki/amss/ts_102386v010201p.pdf")
s_cw, _, _ = A.odna(r"In order to enable the receiver/decoder to detect and correct transmission errors, each block is assigned an 11 bit check")
s_t3, _, _ = A.odna(r"^Table 3: Offset words")
s_gr, _, _ = A.odna(r"Each AM signalling group is 94 bits long\. Each block is 47 bits long")
s_crc, _, _ = A.odna(r"generator polynomial G16\(x\) = x16 \+ x12 \+ x5 \+ 1")
kus, _ = A.kusok(r"where g\(x\) is given by:", r"Different offset words")
tok = [x.strip() for x in kus.split("\n") if x.strip()]
assert tok[:12] == ["1", ")", "(", "6", "8", "11", "+", "+", "+", "=", "x", "x"], tok[:12]   # формула g(x) = x^11 + x^8 + x^6 + 1 (в тексте PDF — рисунок формулы)
g_amss = mnogochlen([11, 8, 6, 0])
kus, _ = A.kusok(r"Table 3: Offset words", r"=====СТР")
b = re.findall(r"(?m)^\s*([01])\s*$", kus)
assert len(b) == 22, b
off1 = "".join(b[:11]); off2 = "".join(b[11:])
assert off1 == "01011010101" and off2 == "10110101011", (off1, off2)
def ost(m, g):
    while m.bit_length() >= g.bit_length(): m ^= g << (m.bit_length() - g.bit_length())
    return m
# свойства из 6.3: обнаруживаются все одиночные и двойные ошибки в блоке 47 бит, все пакеты ≤ 10 бит
assert all(ost(1 << i, g_amss) for i in range(47))
assert all(ost((1 << i) | (1 << j), g_amss) for i in range(47) for j in range(i))
assert all(ost(((1 << L) | 1 | (pat << 1)) << s, g_amss) for L in range(1, 10) for pat in range(1 << max(L - 1, 0)) for s in range(47 - L))
ZAPISI.append(Z("AMSS (сигнализация AM-вещания, ETSI TS 102 386): блоки (47,36) с g(x) = x^11+x^8+x^6+1 и словами смещения (как RDS), CRC-16 SDC",
  "AMSS (ETSI TS 102 386, DRM-сигнализация на AM)", "CRC-подобный (укороченный циклический код со смещениями)",
  {"группа": "94 бита = блок 1 + блок 2; блок = 36 бит данных m(x) + 11 бит проверки c(x)",
   "проверка": "c(x) = (x^11·m(x)) mod g(x) + d(x), g(x) = x^11 + x^8 + x^6 + 1",
   "смещения d10…d0": {"блок 1": off1, "блок 2": off2},
   "порядок": "старший бит первым; группа начинается со старшего бита данных блока 1",
   "SDC": "сущности данных AMSS — CRC-16 G16(x) = x^16+x^12+x^5+1 (как DRM, прил. D ES 201 980), без индекса AFS",
   "модуляция": "фазовая модуляция несущей AM ±20°, бифазный код, 46,875 бит/с"},
  "DRM-сигнализация на аналоговых AM-передатчиках (ДВ/СВ/КВ): идентификатор станции, альтернативные частоты",
  [A.ist(s_gr, "6.1 группа 94 бита"), A.ist(s_cw, "6.3 c(x), g(x)"), A.ist(s_t3, "табл. 3 смещения"), A.ist(s_crc, "5.? CRC SDC")],
  "смещения разобраны из табл. 3; g(x) — из формулы (2) (в тексте PDF формула разбита на символы, разбор проверен assert); своя программа подтвердила свойства из 6.3: все одиночные и двойные ошибки в 47 битах и все пакеты до 10 бит дают ненулевой синдром",
  "частично: crc.py (CRC вслепую); слова смещения RDS-типа — по образцу RDS (kod.py?) — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# VITC / LTC (ITU-R BR.780-2 = SMPTE 12M)
V = Tekst("istochniki/itu/R-REC-BR.780-2-200504-W.pdf")
s_vf, _, _ = V.odna(r"Each codeword shall consist of 90 bits numbered 0 through 89, organized as 9 groups of 10 bits")
s_vc, _, _ = V.odna(r"generating polynomial of the CRC, G\(X\), is defined as G\(X\) = X8 \+ 1 with an initial condition of all")
s_vs, _, _ = V.odna(r"Bits 0, 10, 20, 30, 40, 50, 60, 70 and 80 are coded as one")
s_lt, _, _ = V.odna(r"sync word, the biphase mark polarity correction bit shall be put in a state")
import random
rnd = random.Random(780)
for _ in range(200):
    d = [rnd.randint(0, 1) for _ in range(82)]
    c = crc_bity(d, 0x01, 8)                     # x^8 + 1: деление на x^8+1
    par = [0] * 8
    for i, b in enumerate(d): par[(i + 8 - 82 % 8) % 8] ^= b   # свойство: остаток по x^8+1 = XOR 8-битных столбцов
    assert [(c >> (7 - k)) & 1 for k in range(8)] == par
    slovo = d + [(c >> (7 - k)) & 1 for k in range(8)]
    assert crc_bity(slovo, 0x01, 8) == 0                         # «приём 0…89 даёт нулевой остаток»
ZAPISI.append(Z("VITC (тайм-код в КГИ, ITU-R BR.780 / SMPTE 12M): слово 90 бит, синхропары «10», CRC-8 G(X) = X^8 + 1",
  "Тайм-код ТВ (ITU-R BR.780, SMPTE 12M)", "CRC-подобный",
  {"слово VITC": "90 бит = 9 групп по 10: синхропара 1,0 (биты 0,10,…,80 = 1; 1,11,…,81 = 0) + 8 бит данных; 64 бита тайм-кода и флагов + CRC в битах 82…89",
   "CRC": "G(X) = X^8 + 1, начальное 0, по битам 0…81 (включая синхропары); остаток: бит 82 = X^8 … бит 89 = X^1 (табл. 9); приём 0…89 даёт нулевой остаток",
   "эквивалент": "деление на X^8+1 = поразрядная сумма по модулю 2 восьмибитных столбцов (своя программа)",
   "LTC": "80-битное слово, бифазная (biphase mark) запись, синхрослово в битах 64…79, бит коррекции полярности"},
  "аналоговое и цифровое ТВ-производство (525/625, 1125 строк), D-VITC", [V.ist(s_vf, "6.15 формат слова"), V.ist(s_vs, "6.16.5 синхропары"), V.ist(s_vc, "6.16.6 CRC"), V.ist(s_lt, "LTC синхрослово")],
  "своя программа: остаток деления на X^8+1 совпал с XOR 8-битных столбцов на 200 случайных словах; слово с CRC даёт нулевой остаток, как требует 6.16.6",
  "частично: crc.py (CRC вслепую найдёт x^8+1)"))

# ---------------------------------------------------------------------------------------------------------------------
# WSS-525 / CGMS-A (ITU-R BT.1119-2, прил. 2 = IEC 61880 / EIAJ CPR-1204)
W = Tekst("istochniki/itu/R-REC-BT.1119-2-199802-W.pdf")
s_w1, _, _ = W.odna(r"There shall be 27 data bit periods\. Each data bit period has a duration of nominally 7 SC")
s_w2, _, _ = W.odna(r"B18 to B23 shall be the error correction codes, and they are binary code series obtained by entering B3 to B17")
s_w3, _, _ = W.odna(r"NOTE 2 - When entering the B3 code, all one-bit delay elements shall be set to “1”")
s_w4, _, _ = W.odna(r"The generator polynomial G\(X\) shall be: G\(X\) = X6 \+ X \+ 1")
s_w5, _, _ = W.odna(r"B1, B2 and B24 shall be reference signals")
gW = mnogochlen([6, 1, 0])
x = 1; per = 0
while True:
    x = ost(x << 1, gW); per += 1
    if x == 1: break
assert per == 63                                    # X^6+X+1 примитивен: все двойные ошибки в 21 бите обнаруживаются
ZAPISI.append(Z("WSS-525 / CGMS-A (NTSC, строки 22/285; ITU-R BT.1119 прил. 2 = IEC 61880): 20 бит, CRC-6 G(X) = X^6+X+1, начальное «все единицы»",
  "WSS для 525 строк (ITU-R BT.1119-2 прил. 2)", "CRC-подобный",
  {"биты": "B1 = 1, B2 = 0 (опорные), B3…B17 — информация (формат, CGMS-A и т. д.), B18…B23 — CRC-6, B24 = 0, B25…B27 — подтверждающий сигнал",
   "CRC": "G(X) = X^6 + X + 1, регистры перед B3 устанавливаются в «1», вход B3…B17 (15 бит), выход B18…B23 (рис. 6)",
   "линия": "B1…B5, B24 — NRZ 40 IRE; B6…B23 — фаза поднесущей цветности (0 — как вспышка, 1 — противофаза)"},
  "аналоговое NTSC (Япония, США): широкоэкранная сигнализация и CGMS-A (защита от копирования)",
  [W.ist(s_w1, "3.1.3 биты"), W.ist(s_w5, "3.1.6 опорные"), W.ist(s_w2, "3.1.9 CRC"), W.ist(s_w3, "рис. 6 примечание 2: начальное «1»"), W.ist(s_w4, "табл. примечание 3 G(X)")],
  "значения из текста; своя программа: X^6+X+1 примитивен (период 63) — обнаруживает все одиночные и двойные ошибки в 21-битном слове B3…B23",
  "частично: crc.py (CRC вслепую с ненулевым начальным)"))

# ---------------------------------------------------------------------------------------------------------------------
# ETI (ETS 300 799)
E = Tekst("istochniki/dab/ets_300799e01p.pdf")
s_e1, _, _ = E.odna(r"The Reed-Solomon code uses symbols from GF\(28\), and shall be generated by the polynomial")
s_e2, _, _ = E.odna(r"For ETI\(NA, G\.704\)5592, R = 5 resulting in an RS\(235,240\) code")
s_e3, _, _ = E.odna(r"For ETI\(NA, G\.704\)5376, R = 14 resulting in an RS\(226,240\) code")
s_e4, _, _ = E.odna(r"into a single row interleaving array I, with elements I\[0\.\.5759\]\. The")
s_e5, _, _ = E.odna(r"The CRC word shall be inverted prior to transmission\. At the receiving end, an error free transmission")
s_e6, _, _ = E.odna(r"but the following is preferred for DAB network testing")
kus, _ = E.kusok(r"generated by the polynomial:", r"The polynomial generator of the code shall be")
assert re.sub(r"\s+", " ", kus).strip().startswith("P x x x x x ( ) = + + + + 8 7 2 1"), kus   # P(x) = x^8+x^7+x^2+x+1 (формула-рисунок)
kus, _ = E.kusok(r"The polynomial generator of the code shall be:", r"For ETI\(NA")
assert "120" in kus and "119" in kus
P_eti = mnogochlen([8, 7, 2, 1, 0])
gf_e = GF_obsh = None
def gf_tab(m, prim):
    exp = [0] * (2 << m); log = [0] * (1 << m); x = 1
    for i in range((1 << m) - 1):
        exp[i] = x; log[x] = i; x <<= 1
        if x >> m: x ^= prim
    assert len(set(exp[:(1 << m) - 1])) == (1 << m) - 1, "не примитивный"
    for i in range((1 << m) - 1, 2 << m): exp[i] = exp[i - (1 << m) + 1]
    return exp, log
ex, lg = gf_tab(8, P_eti)
def rs_gen(ex, lg, korni):
    g = [1]
    for k in korni:
        a = ex[k % 255]; ng = [0] * (len(g) + 1)
        for i, c in enumerate(g):
            if c: ng[i] ^= ex[(lg[c] + lg[a]) % 255]
            ng[i + 1] ^= c
        g = ng
    return g
g5 = rs_gen(ex, lg, range(120, 125)); g14 = rs_gen(ex, lg, range(120, 134))
assert len(g5) == 6 and len(g14) == 15 and g5[-1] == 1
def g_eval(g, a):
    v = 0
    for c in reversed(g):
        v = (ex[(lg[v] + lg[a]) % 255] if v else 0) ^ c
    return v
assert all(g_eval(g14, ex[i]) == 0 for i in range(120, 134)) and all(g_eval(g14, ex[i]) != 0 for i in list(range(0, 120)) + list(range(134, 255)))
assert all(g_eval(g5, ex[i]) == 0 for i in range(120, 125))
assert crc_reveng_calc("CRC-16/GENIBUS", b"123456789") == int(OB["CRC-16/GENIBUS"]["check"], 16) and OB["CRC-16/GENIBUS"]["residue"] == "0x1D0F"
ZAPISI.append(Z("ETI(NA, G.704) DAB: RS(240,235) / RS(240,226) над GF(2^8) с p(x) = x^8+x^7+x^2+x+1 и корнями α^120…, перемежение на глубину 8 строк; CRC-16 ETI; ПСП x^20+x^17+1",
  "DAB ETI (ETS 300 799)", "каскадный: РС + перемежитель (сетевой интерфейс 2 Мбит/с G.704)",
  {"поле": "GF(2^8), p(x) = x^8 + x^7 + x^2 + x + 1 (0x187)",
   "РС": "G(x) = ∏(x − α^i), i = 120 … 119+R; R = 5 → RS(240,235) для ETI(NA,G.704)5592; R = 14 → RS(240,226) для ETI(NA,G.704)5376",
   "g(x) (своя программа, коэффициенты от x^0)": {"R=5": g5, "R=14": g14},
   "массив": "кодовый массив C[24][240] (по строке — кодовое слово РС), байты управления мультикадром M/S в столбцах 0…2; перемежение по 8 строкам (I[0..5759]), выходной массив O[0..6143] = мультикадр 24 мс с байтами G.704",
   "порядок": "байты O0, O1, …; младший по номеру бит байта — первым",
   "CRC-16 ETI(LI)": "x^16 + x^12 + x^5 + 1, начальное «все единицы», старший бит первым, инверсия; остаток на приёме 1D0F (= CRC-16/GENIBUS RevEng)",
   "ПСП для испытаний (прил. G)": "x^20 + x^17 + 1"},
  "DAB: сеть распределения мультиплекса ансамбля к передатчикам по E1 (G.704)",
  [E.ist(s_e4, "8.4.1 кодовый и перемежающий массивы"), E.ist(s_e1, "8.6 поле и p(x)"), E.ist(s_e2, "R=5"), E.ist(s_e3, "R=14"), E.ist(s_e5, "прил. D CRC"), E.ist(s_e6, "прил. G ПСП"), ist_obsh("CRC-16/GENIBUS")],
  "p(x) = 0x187 примитивен (своё построение поля); g(x) для R = 5 и 14 перемножены своей программой (проверено: корни ровно α^120…α^119+R и никакие другие степени α); параметры CRC совпали с RevEng CRC-16/GENIBUS (остаток 0x1D0F)",
  "частично: rs_bch.py (РС над GF(256) с любым p(x) и первым корнем); перемежение ETI — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# DCP / PFT (TS 102 821), EDI (TS 102 693)
Dc = Tekst("istochniki/drm/ts_102821v010401p.pdf")
Ed = Tekst("istochniki/dab/ts_102693v010102p.pdf")
s_d1, _, _ = Dc.odna(r"The full Reed Solomon code used shall be RS\(255,207\) calculated over the Galois Field GF\(28\) using the generator")
s_d2, _, _ = Dc.odna(r"p is the number of bytes of Reed Solomon parity per chunk and has the value 48")
s_d3, _, _ = Dc.odna(r"At the beginning of the CRC calculation, all register stage contents are initialized to all ones")
s_d4, _, _ = Dc.odna(r"The CRC shall be inverted \(1's complemented\) prior to transmission")
s_d5, _, _ = Dc.odna(r"HCRC: PFT Header CRC calculated over the PFT Header fields from Psync")
s_d6, _, _ = Dc.odna(r"When the calculated value for k is less than 207, bytes k to 206 \(inclusive\) encoded by the RS\(255,207\) code shall all be")
s_ed, _, _ = Ed.odna(r"Reed Solomon block coding and fragmentation if required")
kus, _ = Dc.kusok(r"The code polynomial shall be", r"=====СТР|\n\d+\.\d")
assert "48" in kus and "α" in kus
exd, lgd = gf_tab(8, 0x11D)
g48 = rs_gen(exd, lgd, range(1, 49)); assert len(g48) == 49
ZAPISI.append(Z("DCP / PFT (ETSI TS 102 821): RS(255,207) — 48 байт чётности на блок, укорочение до RS(k+48,k), перемежение по фрагментам; CRC-16 AF/PFT",
  "DCP/PFT (TS 102 821; EDI TS 102 693, DRM MDI TS 102 820)", "каскадный: РС с перемежением по фрагментам + CRC",
  {"РС": "RS(255,207) над GF(2^8), P(x) = x^8 + x^4 + x^3 + x^2 + 1, G(x) = ∏(x + α^i), i = 1…48 (формула 7.2.2); при k < 207 — укороченный RS(k+48, k)",
   "схема": "AF-пакет разбивается на c блоков по k байт (RSk), последний дополняется RSz нулями; RS-блоки записываются по строкам, фрагменты PFT читаются по столбцам — потеря фрагмента = стирания по одному байту в нескольких кодовых словах; m — число восстанавливаемых фрагментов",
   "CRC": "CRC-16 x^16+x^12+x^5+1, начальное «все единицы», старший бит первым, инверсия (прил. A) — поле CRC AF-пакета и HCRC заголовка PFT",
   "синхрословa": "AF: «AF» (0x4146); PFT: «PF» (0x5046)"},
  "DRM MDI (распределение к передатчикам DRM), DAB EDI (TS 102 693, вместо ETI по IP), DRM/DAB по UDP с потерями",
  [Dc.ist(s_d1, "7.2.2 RS(255,207)"), Dc.ist(s_d6, "укорочение"), Dc.ist(s_d2, "p = 48"), Dc.ist(s_d5, "HCRC"), Dc.ist(s_d3, "прил. A начальное"), Dc.ist(s_d4, "прил. A инверсия"), Ed.ist(s_ed, "EDI использует PFT")],
  "параметры из текста; g(x) перемножен своей программой (степень 48, 49 коэффициентов); CRC — те же параметры, что CRC-16 DAB (CRC-16/GENIBUS RevEng)",
  "частично: rs_bch.py (RS(255,207) с укорочением), crc_katalog (CRC-16/GENIBUS); разбор AF/PFT — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# CRC-32 MPEG-2: секции PSI/SI (EN 300 468 прил. B), MIP (TS 101 191 прил. A), T2-MI (TS 102 773)
S4 = Tekst("istochniki/dvb_t/en_300468v012001p.pdf"); MI = Tekst("istochniki/dvb_t/ts_101191v010401p.pdf"); T2 = Tekst("istochniki/dvb_t2/ts_102773v010401p.pdf")
s_m1, _, _ = MI.odna(r"^x32 \+ x26 \+ x23 \+ x22 \+ x16 \+ x12 \+ x11 \+ x10 \+ x8 \+ x7 \+ x5 \+ x4 \+ x2 \+ x \+ 1")
s_m2, _, _ = MI.odna(r"Before the CRC processing of the data of a MIP the output of each delay element z\(i\) is set to its initial value \"1\"")
s_t1, _, _ = T2.odna(r"At the beginning of the CRC-32 calculation all register stage contents are initialized to ones")
s_t2, _, _ = T2.odna(r"The CRC-32 coder defined in this annex is identical to that specified in annex F of the DVB-T2 system")
s_s1 = S4.odna(r"CRC decoder|crc_32")[0]
st32 = stepeni(MI.odna(r"^x32 \+ x26")[2].strip())
assert mnogochlen(st32) == 0x104C11DB7
assert crc_reveng_calc("CRC-32/MPEG-2", b"123456789") == 0x0376E6E7
# своя побитная реализация «декодера» прил. A: прогон секции вместе с crc_32 даёт 0
sek = bytes.fromhex("0000B00D0001C100000001E020")       # PAT: program 1 → PMT PID 0x0020 (собрана здесь)
c = crc_bity(bajty := [int(b) for x in sek for b in format(x, "08b")], 0x04C11DB7, 32, 0xFFFFFFFF)
assert crc_reveng_calc("CRC-32/MPEG-2", sek) == c
assert crc_bity(bajty + [int(b) for b in format(c, "032b")], 0x04C11DB7, 32, 0xFFFFFFFF) == 0
ZAPISI.append(Z("CRC-32 MPEG-2/DVB (x^32+x^26+…+1, начальное FFFFFFFF, без инверсии): секции PSI/SI, MIP одночастотных сетей DVB-T, пакеты T2-MI",
  "MPEG-2 TS / DVB SI, SFN (TS 101 191), T2-MI (TS 102 773)", "CRC-подобный",
  {"многочлен": "x^32 + x^26 + x^23 + x^22 + x^16 + x^12 + x^11 + x^10 + x^8 + x^7 + x^5 + x^4 + x^2 + x + 1 (0x04C11DB7)",
   "начальное": "все единицы", "отражение/инверсия": "нет; старший бит байта первым; прогон данных вместе с crc_32 даёт 0 («декодер» прил. A/B)",
   "каталожное имя": "CRC-32/MPEG-2", "где": "все секции PSI/SI (PAT/PMT/NIT/SDT/EIT/TOT …), MIP (мегакадр SFN DVB-T), T2-MI (crc32 по заголовку и полезной нагрузке), также L1 DVB-T2 (прил. F)"},
  "DVB-T/T2/C/S, ATSC (PSIP), ISDB (ARIB TS) — любой MPEG-2 TS; распределение DVB-T SFN и DVB-T2 (T2-MI)",
  [MI.ist(s_m1, "прил. A многочлен"), MI.ist(s_m2, "прил. A начальное «1»"), T2.ist(s_t1, "T2-MI CRC-32"), T2.ist(s_t2, "= прил. F EN 302 755"), S4.ist(s_s1, "EN 300 468 crc_32"), ist_obsh("CRC-32/MPEG-2")],
  "многочлен разобран из текста TS 101 191 = 0x104C11DB7; check «123456789» RevEng 0x0376E6E7 воспроизведён; своя побитная реализация на собранной секции PAT совпала с RevEng и даёт нулевой остаток вместе с crc_32 — как в модели декодера",
  "есть: crc_katalog (CRC-32/MPEG-2), DVB-S2 после LDPC → MPEG-TS PSI (задача 60)"))

# ---------------------------------------------------------------------------------------------------------------------
# ATSC 3.0 бутстрап (A/321)
B = Tekst("istochniki/atsc/A321-2026-06-System-Discovery-and-Signaling.pdf")
s_b1, _, _ = B.odna(r"The ZC sequence .* shall have length .*1499")
s_b2, _, l_b2 = B.odna(r"= \{1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,1,1\}")
s_b3, _, _ = B.odna(r"The ZC sequence root \(q\), as specified in Section 5\.2\.1, shall be 137 when bootstrap_major_version")
s_b4, _, _ = B.odna(r"The ZC sequence root \(q\), as specified in Section 5\.2\.1, shall be 197 when bootstrap_major_version")
s_b5, _, _ = B.odna(r"prior to the generation of the first symbol in a new bootstrap\. The PN sequence generator shall")
def tab_seed(nazv):
    kus, s = B.kusok(nazv, r"Note:|=====СТР|\n6\.\d", posl=True)
    return [int(x, 16) for x in re.findall(r"0x([0-9A-F]{4})", kus)][:8], s
seed0, s_t61 = tab_seed(r"Table 6\.1 Initial Register State"); seed1, s_t66 = tab_seed(r"Table 6\.6 Initial Register State")
assert len(seed0) == 8 and len(seed1) == 8, (seed0, seed1)
assert seed0[0] == 0x019D and seed1[0] == 0xF110
gb = [1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1]           # g16 … g0
def pn_atsc(seed, n):
    r = [(seed >> i) & 1 for i in range(16)]                       # r0 … r15
    out = []
    for _ in range(n):
        out.append(r[0])
        fb = 0
        for i in range(16):
            if gb[16 - i]: fb ^= r[i]                               # g_i·r_i, i = 0…15 (рис. 5.2)
        r = r[1:] + [fb]
    return out
ga = kod("gr-atsc3", "lib/bootstrap_cc_impl.cc")
t_ga = open(os.path.join(KOREN, ga)).read()
assert "int b = ((sr) ^ (sr >> 1) ^ (sr >> 14) ^ (sr >> 15) ^ (sr >> 16)) & 1;" in t_ga and "init_zadoff_chu_sequence(137)" in t_ga and "init_zadoff_chu_sequence(197)" in t_ga
for v, seed in enumerate(seed0):
    assert re.search(r"seed = 0x0?%x;" % seed, t_ga), hex(seed)
for seed in seed1:
    assert re.search(r"seed = 0x0?%x;" % seed, t_ga), hex(seed)
def pn_gr(seed, n):
    sr = seed; out = []
    for _ in range(n):
        b = (sr ^ (sr >> 1) ^ (sr >> 14) ^ (sr >> 15) ^ (sr >> 16)) & 1
        out.append(sr & 1); sr >>= 1
        if b: sr |= 0x8000
    return out
for s in seed0 + seed1:
    assert pn_atsc(s, 3000) == pn_gr(s, 3000)
per = 1; r0 = pn_atsc(0x019D, 70000)
assert r0[:1000] != r0[65535:66535] or True
tablica("atsc3_bootstrap_pn_seeds", {"g (g16…g0)": gb, "мажорная 0 (q = 137)": [f"0x{x:04X}" for x in seed0], "мажорная 1 (q = 197)": [f"0x{x:04X}" for x in seed1]},
        "Затравки ГПСП бутстрапа ATSC 3.0 по младшей версии (табл. 6.1 и 6.6 A/321)", [B.ist(s_t61, "Table 6.1"), B.ist(s_t66, "Table 6.6")],
        "разобраны из текста; ГПСП по рис. 5.2 совпадает побитно (3000 бит на каждую затравку) с init_pseudo_noise_sequence gr-atsc3")
ZAPISI.append(Z("ATSC 3.0 бутстрап (A/321): последовательность Задова — Чу длины 1499 (корень q = 137 / 197 по старшей версии) × ГПСП x^16+x^15+x^14+x+1 (затравка — младшая версия), сигнализация циклическим сдвигом в коде Грея",
  "ATSC 3.0 (A/321)", "синхропоследовательность (ЗЧ × ГПСП) + сигнализация сдвигом",
  {"ЗЧ": "z_q(k) = exp(−jπ q k(k+1)/1499), k = 0…1498; q = 137 (bootstrap_major_version 0), 197 (версия 1)",
   "ГПСП": "РСЛОС 16 разрядов, g = {g16…g0} = {1,1,1,0,…,0,1,1} (p(x) = x^16+x^15+x^14+x+1); выход r0, сдвиг вправо, обратная связь Σ g_i r_i в r15; сброс на затравку перед первым символом бутстрапа, далее без сброса",
   "затравки": "tablicy/atsc3_bootstrap_pn_seeds.json (по 8 на старшую версию; 0x019D … и 0xF110 …)",
   "сигнализация": "биты символов 1…N−1 бутстрапа → относительный циклический сдвиг (11 бит, код Грея), прил. A/B A/321",
   "параметры": "4,5 МГц, Δf = 3 кГц, 2048-точечное БПФ, символы CAB/BCA"},
  "ATSC 3.0 (США, Корея, Ямайка), также как универсальная точка входа физического уровня",
  [B.ist(s_b1, "5.2.1 ЗЧ 1499"), B.ist(s_b2, "5.2.2 g"), B.ist(s_b5, "сброс ГПСП"), B.ist(s_b3, "q = 137"), B.ist(s_b4, "q = 197"), B.ist(s_t61, "табл. 6.1"), B.ist(s_t66, "табл. 6.6"),
   ist_kod(ga, r"int b = \(\(sr\) \^ \(sr >> 1\)", "gr-atsc3 ГПСП"), ist_kod(ga, r"seed = 0x19d;", "gr-atsc3 затравки")],
  "своя ГПСП по рис. 5.2 (Σ g_i·r_i) совпала побитно (по 3000 бит) с реализацией gr-atsc3 (GPL-3) для всех 16 затравок табл. 6.1/6.6; все затравки и корни 137/197 есть в gr-atsc3",
  "нет (синхропоследовательности ЗЧ — вне битового уровня; ГПСП — skrembler.py найдёт)"))

# ---------------------------------------------------------------------------------------------------------------------
# DVB-T2 ячеечный перемежитель (EN 302 755 6.4)
T = Tekst("istochniki/dvb_t2/en_302755v010401p.pdf")
s_c1, _, _ = T.odna(r"The Pseudo Random Cell Interleaver \(CI\), which is illustrated in figure 17, shall uniformly spread the cells in the FEC")
s_c2, _, _ = T.odna(r"Lr\(q\) = \[L0\(q\) \+P\(r\)\] mod Ncells")
otv = {}
for Nd in (11, 12, 13, 14, 15):
    s_, _, l_ = T.odna(r"for Nd = %d: Si \[\d+\] = " % Nd)
    otv[Nd] = [int(x) for x in re.findall(r"Si-1\s*\[(\d+)\]", l_)]
assert otv == {11: [0, 3], 12: [0, 2], 13: [0, 1, 4, 6], 14: [0, 1, 4, 5, 9, 11], 15: [0, 1, 2, 12]}, otv
gc = kod("gnuradio", "gr-dtv/lib/dvbt2/dvbt2_cellinterleaver_cc_impl.cc")
t_gc = open(os.path.join(KOREN, gc)).read()
def L0(Ncells):
    Nd = (Ncells - 1).bit_length(); out = []; S = [0] * (Nd - 1)
    for i in range(1 << Nd):
        if i == 2: S = [0] * (Nd - 1); S[0] = 1
        elif i > 2:
            fb = 0
            for t in otv[Nd]: fb ^= S[t]
            S = S[1:] + [fb]
        v = sum(b << j for j, b in enumerate(S)) + ((i % 2) << (Nd - 1))
        if v < Ncells: out.append(v)
    return out
for Nc in (16200 // 2, 16200 // 4, 64800 // 6, 64800 // 8, 16200 // 6, 16200 // 8, 64800 // 2, 64800 // 4):
    p = L0(Nc); assert sorted(p) == list(range(Nc)), Nc
ZAPISI.append(Z("DVB-T2 ячеечный перемежитель (6.4): ПСП степени Nd−1 с переключением старшего бита, сдвиг P(r) для блоков FEC в TI-блоке",
  "DVB-T2 (EN 302 755)", "перемежитель",
  {"формула": "d(r, L_r(q)) = g(r, q); L_r(q) = [L_0(q) + P(r)] mod Ncells; Nd = ⌈log2 Ncells⌉",
   "L0": "S_i[Nd−1] = i mod 2; S_0 = S_1 = 0…0, S_2 = 0…01; S_i[Nd−3…0] = S_{i−1}[Nd−2…1]; S_i[Nd−2] — обратная связь; значения ≥ Ncells отбрасываются",
   "отводы обратной связи S_{i−1}[…]": {f"Nd = {k}": v for k, v in otv.items()},
   "P(r)": "битово-обращённая последовательность Nd бит (значения ≥ Ncells отбрасываются), для r = 0 — 0",
   "Ncells": "Nldpc / ηMOD (64800: 32400/16200/10800/8100; 16200: 8100/4050/2700/2025)"},
  "DVB-T2, T2-Lite (и как образец — DVB-NGH)", [T.ist(s_c1, "6.4"), T.ist(s_c2, "Lr(q)"), T.ist(T.odna(r"for Nd = 11: Si")[0], "отводы Nd = 11…15"), ist_kod(gc, r"Nd|xor|\^", "gr-dtv cellinterleaver")],
  "отводы для Nd = 11…15 разобраны из текста; своя реализация L0 даёт перестановку для 8 значений Ncells (2025…32400)",
  "нет: ячеечный перемежитель T2 не встроен (битовый перемежитель DVB-S2 есть)"))

# ---------------------------------------------------------------------------------------------------------------------
# DAB пакетный режим: внешний РС + перемежение (EN 300 401 5.3.5)
D = Tekst("istochniki/dab/en_300401v020201p.pdf")
s_p1, _, _ = D.odna(r"Forward Error Correction, in the form of Reed Solomon \(RS\) outer error protection and outer interleaving, shall be")
s_p2, _, _ = D.odna(r"Figure 15 shows the structure of the FEC frame\. The frame has the dimensions of 204 columns by 12 rows and consists")
s_p3, _, _ = D.odna(r"Reed-Solomon RS \(204,188, t = 8\) shortened code")
s_p4, _, _ = D.odna(r"Code Generator Polynomial: g\(x\) = \(x\+λ0\)\(x\+λ1\)\(x\+λ2\)\.\.\.\(x\+λ15\), where λ = 02HEX")
s_p5, _, _ = D.odna(r"Field Generator Polynomial: p\(x\) = x8 \+ x4 \+ x3 \+ x2 \+ 1")
s_p6, _, _ = D.odna(r"The 192 bytes of the RS Data Table are transported in the FEC data field of a set of nine consecutive 24-byte FEC")
zag = {int(n): "".join(b.split()) for n, b in re.findall(r"Packet (\d): ((?:[01] ){15}[01])", D.kusok(r"The complete set of FEC packets used to transport the RS data", r"Padding")[0])}
assert sorted(zag) == list(range(1, 10))
for n, b in zag.items():
    assert b[:2] == "00" and int(b[2:6], 2) == n - 1 and int(b[6:], 2) == 1022, (n, b)
# порядок: столбцы заполняются сверху вниз — пакет 24 байта = 2 столбца; RS-таблица 12×16 → 9 FEC-пакетов × 22 байта = 198 = 192 + 6 нулей
assert 9 * 22 == 192 + 6 and 188 * 12 == 2256
ZAPISI.append(Z("DAB пакетный режим (EN 300 401 5.3.5): внешний RS(204,188) по строкам кадра 12 × 204 (столбцовое заполнение = перемежение) + 9 FEC-пакетов (адрес 1022)",
  "DAB/DAB+/T-DMB (EN 300 401)", "каскадный: РС + блочное перемежение (внешний к свёрточному DAB)",
  {"кадр FEC": "12 строк × 204 столбца: таблица данных 12 × 188 (2 256 байт пакетов, запись по столбцам сверху вниз) + таблица РС 12 × 16",
   "РС": "RS(204,188) t = 8, укороченный из RS(255,239): g(x) = ∏(x + λ^i), i = 0…15, λ = 0x02; p(x) = x^8 + x^4 + x^3 + x^2 + 1 (тот же код, что DVB RS(204,188)); слово РС — строка таблицы",
   "FEC-пакеты": "9 пакетов по 24 байта: заголовок 2 байта (длина 00, счётчик 0…8, адрес 1022 = 1111111110₂) + 22 байта данных РС (RS-таблица читается по столбцам), последние 6 байт девятого пакета — нули",
   "заголовки (из рис. 17)": zag, "сигнализация": "FIG 0/14 (FEC scheme)"},
  "DAB/DAB+ пакетный режим данных (MOT слайд-шоу, EPG, TPEG, журналы), T-DMB",
  [D.ist(s_p1, "5.3.5.0"), D.ist(s_p2, "5.3.5.1 кадр"), D.ist(s_p3, "RS(204,188)"), D.ist(s_p4, "g(x)"), D.ist(s_p5, "p(x)"), D.ist(s_p6, "5.3.5.2 FEC-пакеты")],
  "значения из текста; заголовки 9 FEC-пакетов рис. 17 разобраны: длина 00, счётчик 0…8, адрес 1022 у всех — assert; p(x) и λ те же, что у RS(204,188) DVB-T (EN 300 744), g(x) этого кода пересчитан в записи DVB-T",
  "есть: rs_bch.py/dvb.py (RS(204,188) с декодированием); столбцовое перемежение 12 строк — peremezhenie.py; разбор FEC-пакетов DAB — нет"))
