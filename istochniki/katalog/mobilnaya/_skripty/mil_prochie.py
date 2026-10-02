"""Прочие военные: MIL-STD-188-181A (УВЧ SATCOM одиночный доступ), 188-165B (СВЧ SATCOM модемы), 188-203-1A (Link-11: Хэмминг (30,24)
с нечётной чётностью, управляющие коды — отрезки М-последовательности x6+x+1), 188-220D (боевые радиосети: Голей (24,12) + TDC 16×24,
БЧХ (15,7), свёрточные K=3/5/7 1/3, CRC-32, синхро 64 бит, V.36), Link-16/JTIDS (RS(31,15) + CCSK — по диссертации NPS)."""
import re, sys, os, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
def ostatok(v, nbit, g):
    gl = g.bit_length() - 1
    for i in range(nbit - 1, gl - 1, -1):
        if v >> i & 1: v ^= g << (i - gl)
    return v
def pmul(a, b):
    r = 0
    for i in range(b.bit_length()):
        if b >> i & 1: r ^= a << i
    return r
st = lambda *s: sum(1 << x for x in s)

# --- 188-181A --------------------------------------------------------------------------------------------------------------------
A = Tekst("istochniki/mil/MIL-STD-188-181A.004683.PDF")
kus, s_k = A.kusok(r"5\.1\.9\.1\s+FEC characteristics\.", r"NOTE:")
p1, p2 = re.findall(r"P[12]\s+([01]{7})", kus)
assert (oct(int(p1, 2)), oct(int(p2, 2))) == ("0o171", "0o133")
kus, s_v = A.kusok(r"TABLE V\.\s+Puncture code patterns\.", r"Downloaded from")
v1, v2 = re.findall(r"P[12]:\s*([01]{3})", kus); assert (v1, v2) == ("101", "110")
s_som = A.odna(r"For BPSK/SBPSK, the SOM shall be the")[0]
SOM = re.search(r"37-bit sequence, ([01]{37})", A.ves.replace("\n", " ")).group(1); assert len(SOM) == 37
kus, _ = A.kusok(r"where the I channel sequence is", r"The SOM shall be")
I, Q = re.findall(r"[01]{21}", kus); assert len(I) == len(Q) == 21
rec("MIL-STD-188-181A (УВЧ SATCOM 5/25 кГц): свёрточный K=7 171/133, выкалывание 3/4, SOM 37 бит / 2×21 бит", "MIL-STD-188-181 (УВЧ SATCOM)", "свёрточный с выкалыванием + синхрослово",
    {"свёрточный": "K=7 1/2: P1 1111001 (171₈), P2 1011011 (133₈), новый бит — в левую позицию; необязательный", "выкалывание 3/4": "P1: 101, P2: 110 (передаются парами слева направо)",
     "SOM": "BPSK/SBPSK: 37 бит " + SOM + "; OQPSK/SOQPSK: I " + I + ", Q " + Q + " (Q сдвинут на пол-символа)",
     "модуляция": "BPSK/SBPSK/OQPSK/SOQPSK, 75–56 000 бит/с, каналы 5 и 25 кГц"},
    "УВЧ спутниковая связь США (FLTSATCOM, UFO, MUOS legacy)", [A.ist(s_k, "5.1.9.1"), A.ist(s_v, "табл. V"), A.ist(s_som, "5.1.9 SOM")],
    "двоичные записи P1/P2 из текста = 171/133₈; маска табл. V разобрана; SOM 37 бит и I/Q по 21 биту разобраны из текста",
    "частично: свёрточный 171/133 и выкалывание есть (kod.py, vykalyvanie.py); поиск SOM — sinhro.py (просто)")
# --- 188-165B --------------------------------------------------------------------------------------------------------------------
B = Tekst("istochniki/mil/MIL-STD-188_165B.055806.pdf")
s_rs = B.odna(r"RS\(126,112\) for data rates below 512 kilobits-per-second")[0]; s_il = B.odna(r"Interleaver depth shall be selectable, either 4 or 8")[0]
s_cc = B.odna(r"constraint-length convolutional encoding polynomials P1 = 1718 and P2 = 1338")[0]; s_tcm = B.odna(r"rate 2/3 pragmatic trellis FEC coding and signal mapping shall be")[0]
rec("MIL-STD-188-165B (СВЧ SATCOM модемы): внешний RS(126,112)/(219,201)/(225,205) + перемежение 4/8 + свёрточный K=7 (1/2, 3/4, 7/8) или ТКМ 2/3 8PSK", "MIL-STD-188-165 (СВЧ SATCOM)", "каскадный: РС + перемежитель + свёрточный",
    {"внешний": "нет / RS(126,112) (< 512 кбит/с) / RS(219,201) (≥ 512 кбит/с) / RS(225,205) — эмуляция IESS-308/309", "перемежение": "глубина 4 или 8 (по IESS-308/309)",
     "внутренний": "K=7 P1 = 171₈, P2 = 133₈; выкалывание табл. III (3/4 и 7/8, как IESS-308/309)", "8PSK": "прагматическая ТКМ 2/3 по IESS-310", "модуляция": "BPSK/QPSK/OQPSK/8PSK"},
    "СВЧ военная спутниковая связь (DSCS, WGS)", [B.ist(s_rs, "5.4.3 c(1)"), B.ist(s_il, "перемежение"), B.ist(s_cc, "свёрточный"), B.ist(s_tcm, "ТКМ")],
    "по тексту; поле и корни RS и таблица выкалывания заданы ссылкой на IESS-308/309/310 — их собирает модемный процесс (kody2)", "см. модемную ветку (IESS); свёрточный и RS в проекте есть")
# --- 188-203-1A Link-11 -----------------------------------------------------------------------------------------------------------
L = Tekst("istochniki/mil/MIL-STD-188_203-1A.024841.pdf", ocr=True)
kus, s_h = L.kusok(r"5\.2\.4\.1 Error detection code generation\.", r"5\.2\.4\.2 Error detection\.")
kus = re.sub(r"\s+", " ", kus)
URAV = {}
for m in re.finditer(r"Bit (\d\d) \(column.*?\) is set such that when (?:added to bits in locations (.*?)|all the bits in the frame are added), the number of ones", kus):
    b = int(m.group(1))
    if m.group(2) is None: URAV[b] = set(range(30)) - {b}
    else:
        s = set()
        for a, c in re.findall(r"(\d+) through (\d+)", m.group(2)): s |= set(range(int(a), int(c) + 1))
        s |= {int(x) for x in re.findall(r"(?<!through )\b(\d+)\b(?! through)", m.group(2))}
        URAV[b] = s
assert sorted(URAV) == [24, 25, 26, 27, 28, 29], URAV.keys()
H = [(sorted(URAV[b] | {b})) for b in (29, 28, 27, 26, 25, 24)]
kol = [tuple(1 if j in row else 0 for row in H) for j in range(30)]
assert len(set(kol)) == 30 and all(any(c) for c in kol)                              # d ≥ 3
assert all(any(a ^ b ^ c for a, b, c in zip(*tr)) for tr in itertools.combinations(kol, 3))   # d ≥ 4 (SEC-DED)
stroki = re.findall(r"\b\d\d ([01]{30})\b", L.ves)                                   # все читаемые строки табл. III (обе части)
s_c = L.odna(r"TABLE III\. Library of control codes")[0]
def mposl(stepen, otvod, n):
    r = [1] + [0] * (stepen - 1); out = []
    for _ in range(n): out.append(r[-1]); fb = r[-1] ^ r[otvod]; r = [fb] + r[:-1]
    return "".join(map(str, out))
ms6 = mposl(6, 0, 126)                                                                   # x^6 + x + 1, период 63
assert len(stroki) >= 90 and all(s in ms6 or s in ms6[::-1] for s in stroki)
ms5 = mposl(5, 3, 62)                                                                    # x^5 + x^2 + 1 / x^5+x+1 не примитивен — период 31 не вмещает 60 бит
assert not all(s in ms5 or s in ms5[::-1] for s in stroki)
s_g = L.odna(r"shift register sequence with generator polynomial: G\(X\) = x5 \+X \+1")[0]
pt = tablica("link11_hamming_30_24", {"proverki": {str(b): sorted(URAV[b]) for b in URAV}, "nechetnaya": True}, "Link-11 (MIL-STD-188-203-1A 5.2.4.1): проверочные биты 24–29 и охватываемые ими позиции (нечётная чётность)",
             [L.ist(s_h, "5.2.4.1 a–f")], "уравнения разобраны из текста (OCR); столбцы H попарно различны и никакие три не дают 0 — код SEC-DED (d = 4)")
rec("Link-11 (TADIL-A, MIL-STD-188-203-1A): Хэмминг (30,24) с общей нечётной чётностью (SEC-DED); управляющие коды — М-последовательность x6+x+1", "Link-11 (MIL-STD-188-203-1A)", "Хэмминг",
    {"код": "24 бита данных + 6 проверочных (биты 24–29), все проверки — нечётная чётность; бит 24 — общая; исправление 1, обнаружение 2 (таблица " + pt + ")",
     "управляющие коды": "начало/конец/адрес — 2 кадра × 30 бит = отрезки 60 бит М-последовательности G(X) = x6+x+1 (период 63; в OCR текста читается «x5 +X +1» — опечатка распознавания, проверено по таблице), кроме стоповых; первые 6 бит — восьмеричный адрес PU",
     "модуляция": "CLEW: 16 тонов (15 данных DQPSK + доплеровский 605 Гц), кадр 30 бит на 15 тонах, 75 кадр/с (2250 бит/с)", "без Хэмминга": "коды начала, конца, адреса"},
    "тактический обмен данными ВМС НАТО (Link-11, ВЧ/УВЧ)", [L.ist(s_h, "5.2.4.1"), L.ist(s_c, "табл. III"), L.ist(s_g, "G(X)")],
    "уравнения из текста дают SEC-DED (проверено перебором троек столбцов); все читаемые строки табл. III (" + str(len(stroki)) + ") — окна М-последовательности степени 6 (рекуррентность a(n) = a(n−1) ⊕ a(n−6), т. е. x^6+x+1 / взаимный x^6+x^5+1, период 63); x^5+x+1 и любая степень 5 не подходят (период 31 < 60 бит) — OCR-текст «x5 +X +1» на стр. 30 — ошибка распознавания/опечатки скана",
    "нет в проекте; Хэмминг общего вида есть — просто (нужна раскладка по тонам для сигнала)")
# --- 188-220D --------------------------------------------------------------------------------------------------------------------
C = Tekst("istochniki/mil/MIL-STD-188-220D.024816.pdf")
s_gol = C.odna(r"When FEC is selected, the Golay \(24,12\) cyclic block code")[0]
G23 = st(11, 10, 6, 5, 4, 2, 0); assert ostatok((1 << 23) | 1, 24, G23) == 0
s_fak = C.odna(r"x23\+1=\(x11\+x10\+x6\+x5\+x4\+x2\+1\)\(x11\+x9\+x7\+x6\+x5\+x\+1\)\(x\+1\)")[0]
assert pmul(pmul(G23, st(11, 9, 7, 6, 5, 1, 0)), 0b11) == (1 << 23) | 1
s_tdc = C.odna(r"formatted into a sequence of TDC blocks composed of sixteen 24-bit Golay")[0]; s_rot = C.odna(r"Each TDC block matrix shall be rotated to form a 24 x 16 matrix")[0]
TDC = [r * 24 + c for c in range(24) for r in range(16)]                            # передача по строкам матрицы 24×16 = по столбцам 16×24
assert sorted(TDC) == list(range(384))
s_fs = C.odna(r"^1001101110110101011110100000100101101001010011110100111100100110")[0]; s_rfs = C.odna(r"^0001110001111010101101100100000011111101101101110011001110010010")[0]
FS = "1001101110110101011110100000100101101001010011110100111100100110"
s_crc = C.odna(r"P\(x\) = x32 \+x26 \+x23 \+x22 \+x16 \+x12 \+x11 \+x10 \+x8 \+x7 \+x5 \+x4 \+x2 \+x \+1")[0]
s_bch = C.odna(r"g\(x\) = 1 \+ X4 \+ X6\s+\+ X7 \+ X8")[0]; GB = st(8, 7, 6, 4, 0); assert ostatok((1 << 15) | 1, 16, GB) == 0
kus, s_j = C.kusok(r"TABLE J-I\.\s+Convolutional coding generator polynomials \(octal\)\.\s*\n\s*\n", r"\+\s*\n\s*\+", posl=True)
ch = re.findall(r"\b\d+\b", kus); assert ch[:12] == ["3", "5", "7", "7", "5", "52", "66", "76", "7", "554", "624", "764"], ch[:12]
s_v36 = C.odna(r"the contents of the 20-state shift register shall")[0]
pt2 = tablica("mil188_220D_tdc", {"tdc_porjadok_peredachi": TDC, "sinhro_64": FS, "sinhro_robust_64": "0001110001111010101101100100000011111101101101110011001110010010",
               "svertochnye_1_3_oct_msb": {"K3": ["5", "7", "7"], "K5": ["52", "66", "76"], "K7": ["554", "624", "764"]}},
              "MIL-STD-188-220D: порядок передачи бит блока TDC (16 слов Голея × 24 → по столбцам), синхрослова 64 бит, свёрточные коды прил. J",
              [C.ist(s_tdc, "5.3.14.3"), C.ist(s_fs, "рис. 6"), C.ist(s_j, "табл. J-I")], "перестановка — биекция 384; числа табл. J-I разобраны из текста")
rec("MIL-STD-188-220D (боевые радиосети CNR): Голей (24,12) + TDC-перемежение 16×24 + синхро 64 бит + CRC-32 + V.36", "MIL-STD-188-220 (CNR, SINCGARS/EPLRS данные)", "каскадный: Голей + блочный перемежитель + CRC",
    {"Голей": "g(x) = x11+x10+x6+x5+x4+x2+1 (делитель x23+1) + общая чётность = (24,12); вход — 12-битные отрезки, дополнение нулями",
     "TDC": "блок 16 слов по 24 бита (384), матрица 16×24 поворачивается в 24×16 и передаётся по строкам, младший (A1) первым; заполнение — слова от 0xFFF/0x000 поочерёдно; таблица " + pt2,
     "синхро": "64 бит " + FS + " (допуск ≤13 ошибок; инверсия → инвертировать поток); робастная — рис. 7", "FCS": "CRC-32 (x32+x26+…+1, как Ethernet)",
     "скремблер": "V.36 (20 разрядов, начальное — единицы, детектор неблагоприятных состояний) внутри FEC", "модуляция": "по радиостанции (SINCGARS 16 кбит/с, EPLRS и др.)"},
    "тактический интернет армии США (VMF/K-сообщения по SINCGARS, ВЧ, EPLRS)", [C.ist(s_gol, "5.3.14.1"), C.ist(s_fak, "прил. F разложение x23+1"), C.ist(s_tdc, "5.3.14.3 TDC"), C.ist(s_fs, "рис. 6 синхро"), C.ist(s_crc, "FCS"), C.ist(s_v36, "V.36")],
    "g(x) делит x23+1 и произведение трёх множителей из прил. F = x23+1; перестановка TDC построена программно (биекция 384)", "частично: Голей (23,12)/(24,12) есть (dmr.py/p25.py); TDC и синхро — просто")
rec("MIL-STD-188-220D прил. K: БЧХ (15,7), t = 2 (робастный протокол прил. J) и свёрточные 1/3 K=3/5/7", "MIL-STD-188-220 (CNR, SINCGARS/EPLRS данные)", "БЧХ",
    {"БЧХ": "g(x) = 1 + X4 + X6 + X7 + X8 (делитель X15+1), систематический x8·m(x) + r(x), мажоритарное декодирование", "где": "RFF (Robust Frame Format) в асинхр./синхр. режимах: 1, 3 или 5 слов",
     "свёрточные (прил. J)": "1/3: K=3 — 5, 7, 7; K=5 — 52, 66, 76; K=7 — 554, 624, 764 (восьм., выравнивание по старшему; G2 при K=3 инвертирован)"},
    "MIL-STD-188-220 робастный режим (многопролётные ВЧ/УКВ каналы)", [C.ist(s_bch, "K.3"), C.ist(s_j, "табл. J-I")], "g делит x15+1; табл. J-I разобрана", "БЧХ общего вида и свёрточные 1/n есть (просто)")
# --- Link-16 / JTIDS ----------------------------------------------------------------------------------------------------------
J = Tekst("istochniki/archive_org/DTIC_ADA508987.pdf")
s_j1 = J.odna(r"spectrum \(DSSS\) system, that utilizes a \(31, 15\) Reed-Solomon \(RS\) code and cyclic")[0]; s_j2 = J.odna(r"sequences are derived by cyclically shifting a starting sequence")[0]
s_j3 = J.odna(r"each 32-chip CCSK sequence is\s*$|scrambled with a 32-chip pseudo-noise \(PN\) sequence")[0]; s_j4 = J.odna(r"interleaver is used to interleave both the header symbols and data symbols")[0]
rec("Link-16 / JTIDS / MIDS: RS(31,15) над GF(32) + перемежение символов + CCSK 32 элемента + ПСП, MSK 5 Мэлем/с со скачками", "Link-16 (JTIDS/MIDS)", "каскадный: РС + перемежитель + CCSK",
    {"RS": "(31,15) для данных (5-битные символы, GF(2^5), t = 8); заголовок — (16,7) по открытым публикациям", "CCSK": "каждый 5-битный символ → циклический сдвиг начальной 32-элементной последовательности S0 (рис. 4 источника)",
     "ПСП": "32-элементная ПСП поверх (TRANSEC) — закрыта", "модуляция": "MSK 5 Мэлем/с, 51 частота 969–1206 МГц, одинарный/двойной импульс"},
    "НАТО Link-16 (JTIDS/MIDS) — тактический обмен данными", [J.ist(s_j1, "RS(31,15)"), J.ist(s_j4, "перемежитель"), J.ist(s_j2, "CCSK"), J.ist(s_j3, "ПСП")],
    "первоисточник (MIL-STD-6016, STANAG 5516) закрыт/платный — по диссертации NPS (DTIC ADA508987); примитивный многочлен GF(32), S0, перемежитель и ПСП в открытом источнике не приведены",
    "нет; без S0/ПСП внедрение невозможно (засекреченный TRANSEC)")
