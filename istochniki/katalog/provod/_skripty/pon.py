"""PON: ITU-T G.984.3 (GPON), G.987.3 (XG-PON), G.9807.1 (XGS-PON), G.989.3 (NG-PON2), G.9804.2/.3 (HSP, 50G-PON), G.9806 (PtP).
Проверки: таблица синдромов HEC (все 63 позиции), пример скремблера (256 бит), ранг матрицы LDPC(17280,14592), совпадение RS(248,216)
с 10G-EPON RS(255,223) (вектор 802.3 Annex 76A)."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "PON ITU-T (GPON/XG-PON/XGS-PON/NG-PON2/HSP)"


def rec(imya, vid, par, gde, ist, prov, sl, sem=SEM):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


G984 = Tekst("istochniki/itu/T-REC-G.984.3-201401-I.pdf")
G987 = Tekst("istochniki/itu/T-REC-G.987.3-202505-I.pdf")
G9807 = Tekst("istochniki/itu/T-REC-G.9807.1-202302-I.pdf")
G989 = Tekst("istochniki/itu/T-REC-G.989.3-202105-I.pdf")
G98042 = Tekst("istochniki/itu/T-REC-G.9804.2-202109-I.pdf")
G98043 = Tekst("istochniki/itu/T-REC-G.9804.3-202109-I.pdf")
G9806 = Tekst("istochniki/itu/T-REC-G.9806-202006-I.pdf")

# ---- GPON ----
s1 = G984.odna(r"The most common RS code is RS\(255,239\), where a 255-byte codeword consists of 239 data bytes")[0]
s1s = G984.odna(r"polynomial used is x7 \+ x6 \+ 1\. This pattern is added modulo two to the downstream data")[0]
rec("GPON FEC RS(255,239) + скремблер x^7 + x^6 + 1 (G.984.3)", "РС",
    {"код": "RS(255,239) как G.975/G.709 (GF(2^8), x^8+x^4+x^3+x^2+1, корни α^0…α^15)", "раскладка": "нисходящий: кадр 125 мкс делится на слова по 255 (последнее укорочено), проверочные после каждых 239 байт; восходящий — по пачке, флаг Use_FEC",
     "скремблер": "кадровый x^7 + x^6 + 1, сброс в «все единицы» на первом бите после поля PSync блока PCBd (8.1.2)", "индикатор": "бит FEC поля Ident"},
    "GPON 2.5G/1.25G", [G984.ist(s1, "13.1.1"), G984.ist(s1s, "скремблер")], "код = G.975 (тот же RS(255,239)); в G.984.3 (2014) сам RS описан ссылкой", "частично: RS(255,239) есть (otn.py), кадр GTC — нет")
sH = G984.odna(r"used is a combination of the BCH \(39, 12, 2\) code and a single parity bit")[0]
gH = iz_stepenej([12, 10, 8, 5, 4, 3, 0])
assert poryadok_mnogochlena(gH) == 63                                    # код BCH(63,51) — укорачивается до 39
rec("GPON GEM заголовок: HEC = укороч. БЧХ(39,12,2) + чётность, маска 0xB6AB31E055", "БЧХ (укороченный) + чётность",
    {"g(x)": "x^12 + x^10 + x^8 + x^5 + x^4 + x^3 + 1", "поле": "заголовок 40 бит: PLI 12, Port-ID 12, PTI 3, HEC 13", "маска": "заголовок XOR 0xB6AB31E055", "начальное": "регистр делителя — нули"},
    "GPON GEM", [G984.ist(sH, "8.3.1")], "период g(x) = 63 (проверено) → исходный БЧХ(63,51), укороченный до 39 бит; то же g, что XG-PON HEC (таблица синдромов сверена)",
    "нет (в проекте GPON/GEM не найдено); просто добавить: укороченный циклический код + маска")

# ---- XG-PON ----
sB = G987.odna(r"In the downstream direction, the FEC code is RS \(248, 216\), which is a truncated form of RS\(255,")[0]
sB2 = G987.gde("B.3 Construction of RS(248, 216) codeword")
rec("XG-PON/XGS-PON/NG-PON2: RS(248,216) (укороч. RS(255,223)) и RS(248,232) (укороч. RS(255,239))", "РС (укороченный)",
    {"поле": "GF(2^8), f(x) = x^8+x^4+x^3+x^2+1", "RS(248,232)": "G(z) = ∏_{i=0}^{15}(z − α^i), 16 проверочных", "RS(248,216)": "G(z) = ∏_{i=0}^{31}(z − α^i), 32 проверочных",
     "применение": "XG-PON: нисходящий RS(248,216), восходящий RS(248,232); XGS-PON (G.9807.1): 9.95 Гбит/с в обе стороны RS(248,216); NG-PON2 (G.989.3): 2.49G — RS(248,232), 9.95G — RS(248,216)",
     "раскладка": "PHY-кадр 125 мкс: PSBd 24 байта вне FEC; далее 627 слов по 248 байт (нисходящий)", "порядок": "D231/D215 первым, биты старшим первым (рис. B.1)"},
    "XG-PON, XGS-PON, NG-PON2 (TWDM), 10G-EPON-совместимый код",
    [G987.ist(sB, "10.1.3"), G987.ist(sB2, "Annex B"), G9807.ist(G9807.odna(r"For 9\.95328 Gbit/s nominal line rate, the downstream FEC code is RS\(248, 216\)")[0], "XGS-PON"),
     G989.ist(G989.odna(r"For 2\.48832 Gbit/s nominal line rate, the downstream FEC code is RS\(248, 232\)")[0], "NG-PON2")],
    "RS(248,216) — укорочение того же кода, что 10G-EPON RS(255,223) IEEE 802.3 Cl.76 (поле и корни α^0…α^31 совпадают), который проверен тестовым вектором Annex 76A; снимки risunki/g9873_annexB1/2.png",
    "частично: RS над GF(256) есть (rs_bch.py, укорочение поддерживается как короткое слово), кадр XGTC/FS — нет")
k, sT = G987.kusok(r"Table A\.1 – HEC error syndromes", r"A\.2 ")
pary_ = re.findall(r"\b(\d{1,2})\s+([0-9A-F]{3})\b", " ".join(k.split()))
sind = {int(a): int(b, 16) for a, b in pary_ if 1 <= int(a) <= 63}
assert len(sind) == 63 and all(sind[p] == poly_mod2(1 << (p - 1), gH) for p in sind)
tablica("xgpon_hec_sindromy", {str(p): format(sind[p], "03X") for p in sorted(sind, reverse=True)},
        "Синдромы одиночной ошибки HEC XG-PON (BCH(63,12,2), g = x^12+x^10+x^8+x^5+x^4+x^3+1): позиция 63…1 → 12-бит синдром (hex)",
        [G987.ist(sT, "Table A.1")], "все 63 значения табл. A.1 совпали с x^(p−1) mod g(x)")
rec("XG-PON HEC: БЧХ(63,12,2) + бит чётности (заголовок XGTC, BWmap, XGEM)", "БЧХ + чётность",
    {"g(x)": "x^12 + x^10 + x^8 + x^5 + x^4 + x^3 + 1", "длины": "защищаемое поле 51 бит → 64-бит структура; 19 бит (+32 нуля) → 32 бита (HLend)",
     "исправление": "2 ошибки, обнаружение 3 (с чётностью)", "синдромы": "tablicy/xgpon_hec_sindromy.json"},
    "XG-PON/XGS-PON/NG-PON2/HSP (заголовки кадров)", [G987.ist(G987.odna(r"The HEC is a double error correcting, triple error detecting code")[0], "Annex A.1"), G987.ist(sT, "табл. A.1")],
    "все 63 синдрома табл. A.1 вычислены из g(x) и совпали", "нет (просто: таблица синдромов готова)")
sS = G987.odna(r"polynomial used is x58 \+ x39 \+ 1\. This pattern is added modulo two to the downstream data")[0]
k5, s5 = G987.kusok(r"Table A\.5 – Scrambler sequence example", r"Annex B|=====СТР 11[89]", posl=True)
obr = [int(c) for c in "".join(re.findall(r"\b[01]{4}\b", k5))[:256]]
S = [1] * 7 + [0] * 51; out = []
for _ in range(256):
    o = S[38] ^ S[57]; out.append(S[57]); S = [o] + S[:-1]
assert out == obr
rec("XG-PON/XGS-PON/HSP скремблер кадра x^58 + x^39 + 1 с предзагрузкой от счётчика сверхкадров", "скремблер",
    {"многочлен": "x^58 + x^39 + 1, аддитивный, кадрово-синхронный", "предзагрузка": "51 старший бит = счётчик сверхкадров (из PSBd), 7 младших = 1; сброс после PSBd",
     "восходящий": "то же, предзагрузка от номера кадра/пачки"}, "XG-PON, XGS-PON, NG-PON2, 50G-PON",
    [G987.ist(sS, "10.4.1"), G987.ist(s5, "Table A.5 пример")], "пример табл. A.5 (256 бит при счётчике 0) воспроизведён регистром: состояние S0…S6 = 1, выход S57, обратная связь S38 xor S57",
    "частично: многоотводные аддитивные скремблеры есть (skrembler.py), предзагрузка от счётчика — нет")

# ---- HSP (50G-PON) LDPC ----
k, sL = G98042.kusok(r"The compact form of the parity-check matrix", r"\nB\.1\.3")
k = re.sub(r"=====СТР \d+=====.*?Rec\. ITU-T G\.9804\.2 \(09/2021\)\s*\n(\d+\s*\n)?", "\n", k, flags=re.S)
k = re.sub(r"\n\d+ \n?Rec\. ITU-T G\.9804\.2 \(09/2021\)", "\n", k)          # колонтитулы страниц
tok = [t.replace("−", "-") for t in re.findall(r"C\d+|[−-]?\d+", k)]
vals = []; i = 0
while i < len(tok):
    if tok[i].startswith("C"):
        while i < len(tok) and tok[i].startswith("C"): i += 1
        while i < len(tok) and not tok[i].startswith("C"): vals.append(int(tok[i])); i += 1
    else: i += 1
assert len(vals) == 69 * 12
Hc = [[vals[j * 12 + r] for j in range(69)] for r in range(12)]
Z = 256; rows = []
for r_ in range(12):
    for rr in range(Z):
        v = 0
        for j in range(69):
            a = Hc[r_][j]
            if a >= 0: v |= 1 << (69 * Z - 1 - (j * Z + (rr + a) % Z))
        rows.append(v)
basis = {}; rk = 0
for s_ in rows:
    while s_:
        h = s_.bit_length() - 1
        if h in basis: s_ ^= basis[h]
        else: basis[h] = s_; rk += 1; break
assert rk == 3072 and 69 * Z - rk == 14592
sL3 = G98042.odna(r"The LDPC code adopts the LDPC code matrix of clause B\.1\.2, with 384-bit puncturing and no")[0]
tablica("hsp_ldpc_17280_14592_Hc", Hc, "Компактная проверочная матрица LDPC 50G-PON/25G-EPON: 12 × 69 циркулянтов 256×256, a(i,j) = сдвиг вправо, −1 — нулевой блок",
        [G98042.ist(sL, "B.1.2 (матрица = IEEE 802.3ca 142.2.4)")],
        "828 значений разобраны из текста (печатная таблица транспонирована: строки таблицы — столбцы матрицы); развёрнутая H 3072 × 17664 имеет полный ранг 3072 → k = 14592, как в B.1.3")
rec("50G-PON (HSP, G.9804.2) и 25G/50G-EPON (IEEE 802.3ca): QC-LDPC(17280,14592) — выкалывание 384 проверочных бит материнского кода", "LDPC (QC)",
    {"материнский": "H 3072 × 17664, 12 × 69 циркулянтов Z = 256 → tablicy/hsp_ldpc_17280_14592_Hc.json", "K,S,P,M,N": "14592, 0, 384, 2688, 17280 (R = 0.8444)",
     "кодирование": "u* = [u | 0…0 (S)], p (M+P бит) из H, последние P проверочных отбрасываются", "раскладка": "нисходящий 50G: 360 слов в PHY-кадре; восходящий 12.5G/25G — то же по умолчанию, последний укорочен",
     "скремблер": "x^58 + x^39 + 1 после FEC"},
    "50G-PON (ITU-T G.9804.x), 25G/50G-EPON (IEEE 802.3ca)", [G98042.ist(sL, "B.1.2"), G98042.ist(sL3, "B.1.3"),
     G98042.ist(G98042.odna(r"For 49\.7664 Gbit/s nominal line rate, the downstream FEC code is LDPC\(17280, 14592\)")[0], "10.3.1.1")],
    "полный ранг развёрнутой матрицы (3072) → размерность 14592 совпадает с текстом; матрица идентична 802.3ca по утверждению текста (сам 802.3ca закрыт)",
    "частично: LDPC-движок проекта принимает QC-матрицы (ldpc_std/ldpc_flex) — добавить матрицу и выкалывание")
rec("50G-PON PMD (G.9804.3): тот же LDPC(17280,14592)", "LDPC (QC)", {"ссылка": "PMD-требования к BER даны для LDPC(17280,14592)"}, "50G-PON",
    [G98043.ist(G98043.odna(r"^LDPC \(17280, 14592\)")[0])], "код тот же, что G.9804.2 (проверен выше)", "см. запись G.9804.2")
rec("HS-PtP (G.9806): RS(528,514) по IEEE 802.3 91.5.2.7 поверх 10GBASE-R", "РС",
    {"код": "RS(528,514) Cl.91", "линия": "64B/66B 10GBASE-R PCS"}, "высокоскоростной точка-точка по одному волокну",
    [G9806.ist(G9806.odna(r"To meet the bit error ratio requirements, the use of frame error rate: RS\(528, 514\) as specified in")[0])],
    "код тот же, что IEEE 802.3 Cl.91 (проверен двумя примерами кодовых слов)", "нет")

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
