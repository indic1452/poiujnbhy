"""Кабельные сети: ITU-T J.83 (Annex A–D), J.112 Annex B / J.122 / J.222 (DOCSIS 1.x–3.0), CableLabs DOCSIS 3.0/3.1/4.0 PHY.
Проверки: коэффициенты g(x) RS(128,122) J.83B (α^52, α^116, α^119, α^61, α^15), свободное расстояние BCC (25,37),
ранги малых LDPC DOCSIS 3.1 (160,80) и (480,288), разобранных из таблиц."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "Кабельное ТВ и DOCSIS (J.83, J.112/J.122, CableLabs)"


def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": SEM, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


J = Tekst("istochniki/itu/T-REC-J.83-200712-I.pdf")
D30 = Tekst("istochniki/cablelabs/CM-SP-PHYv3.0-C01-171207.pdf")
D31 = Tekst("istochniki/cablelabs/CM-SP-PHYv3.1-I15-180926.pdf")
D40 = Tekst("istochniki/cablelabs/CM-SP-PHYv4.0-I02-200429.pdf")
J112 = Tekst("istochniki/itu/T-REC-J.112-200403-I_AnnB.pdf")
J122 = Tekst("istochniki/itu/T-REC-J.122-200712-I.pdf")

# ---- J.83 Annex B ----
p7 = iz_stepenej([7, 3, 0]); assert primitivnyj(p7)
exp, log = pole(p7)
g = rs_g(p7, 1, 5)
assert [log[c] for c in g[1:]] == [52, 116, 119, 61, 15]
sRS = J.gde("The MPEG-2 transport stream is Reed-Solomon (RS) encoded using a (128, 122) code over GF(128)")
sRS2 = J.gde("A valid code word will have roots at the first through fifth powers")
rec("J.83 Annex B (США, DOCSIS нисходящий): RS(128,122) над GF(128) расширенный, t = 3", "РС (расширенный)",
    {"поле": "GF(2^7), p(x) = x^7 + x^3 + 1", "g(x)": "(x+α)(x+α^2)…(x+α^5) = x^5 + α^52x^4 + α^116x^3 + α^119x^2 + α^61x + α^15",
     "расширение": "127 символов + c_ = c(α^6) (128-й символ)", "символ": "7 бит", "кадр FEC": "64-QAM: 60 блоков + 42-бит трейлер 0x752C0D6C…; 256-QAM: 88 блоков + 40 бит 0x71E84DD4…"},
    "цифровое кабельное ТВ США (ANSI/SCTE 07), DOCSIS 1.x–3.0 нисходящий", [J.ist(sRS, "B.5.1"), J.ist(sRS2, "расширенный символ (снимок risunki/j83b_rs.png)")],
    "коэффициенты g(x), напечатанные в B.5.1, совпали с вычисленными по x^7+x^3+1 и корням α^1…α^5; многочлен поля примитивен",
    "частично: слепой РС (m = 7) есть; расширенный символ и кадр FEC — нет")
sI = J.gde("(I,J) = (128,1), (64,2), (32,4), (16,8), (8,16)")
sSy = J.gde("The first 4 7-bit symbols of the frame sync trailer contain the 28-bit \"unique\" synchronization pattern")
assert [int(x, 2) for x in ("1110101", "0101100", "0001101", "1101100")] == [0x75, 0x2C, 0x0D, 0x6C]   # 4 семибитных символа = 75 2C 0D 6C
assert int("01110001111010000100110111010100", 2) == 0x71E84DD4
rec("J.83 Annex B: кадр FEC, свёрточное перемежение (I,J) и рандомизатор GF(128)", "кадровая структура + перемежитель + рандомизатор",
    {"синхро 64-QAM": "28 бит 1110101 0101100 0001101 1101100 (75 2C 0D 6C как 4 семибитных символа) + 4 бита режима перемежения + 10 нулей",
     "синхро 256-QAM": "32 бита 0111 0001 1110 1000 0100 1101 1101 0100 (71E84DD4) + 4 бита управления + 4 нуля",
     "перемежение": "свёрточное, символы 7 бит; уровень 1: I = 128, J = 1; уровень 2: (128,1…8), (64,2), (32,4), (16,8), (8,16) по 4-бит слову (табл. B.2)",
     "рандомизатор": "символьный ЛРС над GF(128): f(x) = x^3 + x + α^3, начальное «все единицы», старт с первого символа после трейлера"},
    "J.83B", [J.ist(sI, "B.5.2 рис. B.8"), J.ist(sSy, "B.5.3"), J.ist(J.gde("f(x) = x3 + x + α3"), "B.5.4")],
    "по тексту; синхрослова даны и двоичной, и шестнадцатеричной записью (сверены вручную по тексту)", "частично: свёрточные перемежители (peremezhenie.py); символьный рандомизатор GF(128) — нет")
gB = (0o25, 0o37)
df = dfree(list(gB), 5)
sT = J.gde("16-state non-systematic rate 1/2 encoder with the generator: G1 = 010 101, G2 = 011 111")
rec("J.83 Annex B: ТКМ — выколотый свёрточный (25,37)8 K = 5, 4/5, [P1;P2] = [0001;1111], дифференциальное прекодирование 90°", "свёрточный (выколотый) + ТКМ",
    {"BCC": "16 состояний, G1 = 25 (1+D^2+D^4), G2 = 37 (1+D+D^2+D^3+D^4), начало группы — G1", "выкалывание": "[P1, P2] = [0001; 1111]: из 8 бит оставлены 5 → 4/5",
     "64-QAM": "группа 28 бит → 5 символов, 2 × BCC по 4 бита (МЗР A/B), общая скорость 14/15", "256-QAM": "38 бит (или 30 + 8 синхро) → 5 символов, 19/20",
     "прекодер": "X_J = W_J + X_{J−1} + Z_J(X_{J−1}+Y_{J−1}); Y_J = Z_J + W_J + Y_{J−1} + Z_J(X_{J−1}+Y_{J−1})"},
    "J.83B 64/256-QAM", [J.ist(sT, "B.5.5.4"), J.ist(J.gde("Differential pre-coder equations"), "B.5.5.3")],
    f"свободное расстояние невыколотого (25,37) K=5 вычислено: d_free = {df}", "частично: свёрточные коды с выкалыванием (svyortka.py/vykalyvanie.py); ТКМ-разметка — нет")
sA = J.gde("Annex A")
rec("J.83 Annex A/C (DVB-C, Япония): RS(204,188) t = 8 (укороч. RS(255,239)) + байтовое перемежение I = 12 + рандомизатор 1+x^14+x^15", "РС",
    {"код": "как DVB-C (ETSI EN 300 429) — определение и проверка в области «efir»", "Annex C": "то же, α = 0.13 roll-off"},
    "кабельное ТВ Европы/Японии", [J.ist(J.gde("The shortened Reed-Solomon (204, 188) code shall be used for the forward error correction"), "RS(204,188)"), J.ist(J.gde("1 + x14 + x15"), "Annex A рандомизатор"), J.ist(J.gde("I = 12."), "Annex A перемежение")], "код совпадает с DVB-C (ссылка J.83 Annex A на EN 300 429)", "ЕСТЬ в проекте: dvb.py — RS(204,188) и перемежение DVB")

# ---- DOCSIS 1.x–3.0 восходящий ----
sU = D30.gde("The following Reed-Solomon generator polynomial MUST be supported")
sUs = D30.gde("The polynomial MUST be x15 + x14 + 1")
sTC = D30.gde("Figure 6–9 shows the employed 8-state TCM encoder")
rec("DOCSIS 1.x–3.0 восходящий: RS над GF(256), T = 1…16 (корни α^0…α^(2T−1)), скремблер x^15 + x^14 + 1", "РС (переменный) + скремблер",
    {"поле": "p(x) = x^8 + x^4 + x^3 + x^2 + 1, α = 0x02", "g(x)": "(x+α^0)(x+α^1)…(x+α^(2T−1))", "длины": "18…255 байт; режим укороченного последнего слова",
     "порядок бит": "первый бит MAC → старший бит первого символа РС", "скремблер": "x^15 + x^14 + 1, 15-бит начальное из UCD, сброс в начале пачки",
     "перемежение": "байтовый перемежитель TDMA (табл. 6–2), кадровщик/перемежитель S-CDMA"},
    "кабельные модемы DOCSIS 1.0–3.0 (J.112 Annex B, J.122, J.222)",
    [D30.ist(sU, "6.2.4.1"), D30.ist(sUs, "6.2.x скремблер"), J112.ist(J112.gde("Reed-Solomon"), "J.112 Annex B"), J122.ist(J122.gde("Reed-Solomon"), "J.122")],
    "многочлен поля = G.975/ADSL (примитивен, проверено); формула g совпадает с DMT-DSL", "частично: RS GF(256) и аддитивные скремблеры есть; UCD-параметры — нет")
rec("DOCSIS 2.0/3.0 S-CDMA: 8-позиционная ТКМ (систематический свёрточный, x1 = s0), m = 1…6 бит/символ", "свёрточный (ТКМ)",
    {"кодер": "систематический, 3 элемента задержки (s2, s1, s0); добавляет x1 = s0 к входным i_m…i1 (рис. 6-9)", "хвост": "m = 1: 3 хвостовых символа с i1 = s1; m ≥ 2: 2 символа",
     "созвездия": "QPSK0, 8-, 16-, 32-, 64-, 128-QAM TCM"}, "DOCSIS S-CDMA восходящий", [D30.ist(sTC, "6.2.9")],
    "по тексту; схема обратных связей — рисунок 6-9 (в текстовом слое нет)", "нет")

# ---- DOCSIS 3.1 ----
k19, s19 = D31.kusok(r"Table 19 - \(160,80\) LDPC code Parity Check Matrix\s*\n", r"Let the information bits sent to the mother code encoder")
k21, s21 = D31.kusok(r"Table 21 - \(480, 288\) LDPC Code Parity Check Matrix\s*\n", r"Denote the information bits sent to the mother code encoder")
def razbor(k, strok, stolb):
    t = re.findall(r"(?m)^\s*(-|\d+)\s*$", k)
    assert len(t) == strok * stolb, (len(t), strok, stolb)
    return [[-1 if x == "-" else int(x) for x in t[r * stolb:(r + 1) * stolb]] for r in range(strok)]
def rang_qc(Hc, L):
    m, n = len(Hc), len(Hc[0]); rows = []
    for i in range(m):
        for r in range(L):
            v = 0
            for j in range(n):
                if Hc[i][j] >= 0: v |= 1 << (n * L - 1 - (j * L + (r + Hc[i][j]) % L))
            rows.append(v)
    basis = {}; rk = 0
    for x in rows:
        while x:
            h = x.bit_length() - 1
            if h in basis: x ^= basis[h]
            else: basis[h] = x; rk += 1; break
    return rk
H19 = razbor(k19, 5, 10); H21 = razbor(k21, 4, 10)
assert rang_qc(H19, 16) == 80 and rang_qc(H21, 48) == 192
tablica("docsis31_ldpc_maly", {"(160,80) L=16, 5×10": H19, "(480,288) L=48, 4×10": H21},
        "Малые QC-LDPC DOCSIS 3.1: материнские коды начального/точного ранжирования, NCP и PLC (−1 — нулевой блок, иначе сдвиг вправо)",
        [D31.ist(s19, "Table 19"), D31.ist(s21, "Table 21")], "разобрано из текста; ранги развёрнутых H: 80 и 192 (полные) → k = 80 и 288, как в тексте")
rec("DOCSIS 3.1: малые LDPC — (160,80) L=16 → (128,80) ранжирование, (48,24) NCP; (480,288) L=48 → (384,288) PLC, точное ранжирование", "LDPC (QC, укорочение/выкалывание)",
    {"(128,80)": "выкалываются a0…a15 и b144…b159", "NCP (48,24)": "укорочение a24…a79 = 0; выкалываются b80…b103, b112…b127, b144…b159",
     "PLC (384,288)": "выкалываются a48…a95 и b384…b431", "точное ранжирование": "укорочение a272…a287, выкалываются a0…a53 и b432…b479",
     "матрицы": "tablicy/docsis31_ldpc_maly.json"},
    "DOCSIS 3.1/4.0 (OFDM): NCP, PLC, ранжирование", [D31.ist(s19), D31.ist(s21), D31.ist(D31.gde("7.5.14.2 Forward Error Correction Code for the NCP"), "NCP"),
                                                       D31.ist(D31.gde("7.5.13.6 Forward Error Correction Code for the PLC"), "PLC")],
    "матрицы собраны из таблиц 19 и 21, полный ранг", "частично: LDPC-движок есть; добавить 2 матрицы и схемы выкалывания")
sL = D31.gde("Rate= 89% (16200, 14400) code, m=5 rows x n=45 columns, L=360")
rec("DOCSIS 3.1 восходящий OFDMA: QC-LDPC (16200,14400) L=360, (5940,5040) L=180, (1120,840) L=56", "LDPC (QC)",
    {"коды": "длинный/средний/короткий; выбор по размеру гранта (алгоритм MATLAB 7.4.3.1.1), укорочение последнего", "рандомизатор": "7.4.4"},
    "DOCSIS 3.1/4.0 восходящий", [D31.ist(sL, "7.4.3.2"), D40.ist(D40.gde("QC-LDPC"), "DOCSIS 4.0")],
    "матрицы проекта сверены с черновиком IEEE 802.3bn (docs/20-potok.md, одно расхождение ячейки в пользу DOCSIS)", "ЕСТЬ в проекте: ldpc_std docsis31-16200-14400/5940-5040/1120-840")
sD = D31.gde("It is based on [DVB- C2] section 6.1, FEC Encoding")
rec("DOCSIS 3.1 нисходящий OFDM: БЧХ + LDPC (16200,14400) по DVB-C2 6.1 (короткий кадр, 8/9) с укорочением и смешанной модуляцией", "каскадный: БЧХ + LDPC",
    {"основа": "DVB-C2 (ETSI EN 302 769) 6.1: короткий FEC-кадр 16200, скорость 8/9", "изменения": "только 8/9; укорочение слов; неквадратные КАМ 128/512/2048; смешанная модуляция; своё битовое перемежение",
     "Kbch/Nbch": "по DVB-C2 (короткий 8/9)"}, "DOCSIS 3.1/4.0 нисходящий", [D31.ist(sD, "7.5.4")],
    "ссылочное определение; сами БЧХ/LDPC — в DVB-C2 (область «efir»)",
    "частично: в проекте есть DVB-S2 короткий 8/9 (dvb-s2-16200-14400) и внешний БЧХ DVB-S2; совпадение с C2 сверить в области efir")

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
