"""UWB: ECMA-368 (WiMedia MB-OFDM, = ETSI TS 102 455): заголовок PLCP — RS(23,17) + HCS CRC-16; скремблер 1+D^14+D^15 с 4 затравками;
свёрточный 1/3 K=7 (133,165,171) с выкалыванием 1/2, 5/8, 3/4; трёхступенчатый битовый перемежитель.
Проверка — векторы табл. 29 (первые 16 бит ГПСП для каждой затравки), g(x) RS пересчитан из корней, dfree вычислен;
шаблоны выкалывания — по рисункам 20–22 (снимки страниц risunki/ecma368_str72.png, …73.png)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

C = Tekst("istochniki/uwb/ECMA-368.pdf"); ET = Tekst("istochniki/uwb/ts_102455v010101p.pdf"); ZAPISI = []
SL = "UWB ECMA-368 / WiMedia MB-OFDM (Wireless USB, Bluetooth 3.0 AMP UWB)"
kus, s_t = C.kusok(r"Table 29 - Scrambler Seed Selection\s*\nSeed", r"=====СТР")
kus2, _ = C.kusok(r"Table 29 - Scrambler Seed Selection \(concluded\)", r"PRBS Output")
pary_ = re.findall(r"\n(00|01|10|11)\n([01]{4} [01]{4} [01]{4} [01]{3})\n([01]{4} [01]{4} [01]{4} [01]{4})", kus)
m11 = re.search(r"\n11\s*\n([01]{4} [01]{4} [01]{4} [01]{3})\s*\n([01]{4} [01]{4} [01]{4} [01]{4})", C.ves)
pary_.append(("11", m11.group(1), m11.group(2)))
assert [p[0] for p in pary_] == ["00", "01", "10", "11"], pary_
def prbs_uwb(xinit, n):
    x = {-(k + 1): int(b) for k, b in enumerate(xinit.replace(" ", ""))}     # x[-1] … x[-15]
    for i in range(n): x[i] = x[i - 14] ^ x[i - 15]
    return "".join(str(x[i]) for i in range(n))
for sid, xi, out in pary_:
    assert prbs_uwb(xi, 16) == out.replace(" ", ""), sid
s_s = C.odna(r"g\(D\) = 1 \+ D14 \+ D15, where D is a single bit delay eleme|The polynomial generator, g\(D\), for the pseudo-random binary sequence")[0]
ZAPISI.append(Z("ECMA-368 скремблер: боковой (аддитивный) 1 + D^14 + D^15, 4 затравки по идентификатору (S1,S2), сброс на заголовок MAC и на PSDU", SL, "скремблер (рандомизатор)",
  {"многочлен": "x[n] = x[n−14] ⊕ x[n−15]", "затравки x[−1]…x[−15] → первые 16 бит": {sid: [xi, out] for sid, xi, out in pary_},
   "область": "заголовок MAC + HCS и весь PSDU (заголовок PHY не скремблируется); идентификатор затравки — 2-битный счётчик кадров"},
  "Wireless USB, WiMedia UWB (3,1–10,6 ГГц)", [C.ist(s_s, "10.5"), C.ist(s_t, "табл. 29")],
  "для всех 4 затравок первые 16 бит ГПСП вычислены и совпали с табл. 29", "есть: skrembler.py / dvb.py (та же ГПСП 1+x^14+x^15; затравки — перебором)"))

# RS(23,17)
exp = [0] * 512; log = [0] * 256; x = 1
for i in range(255):
    exp[i] = x; log[x] = i; x <<= 1
    if x & 0x100: x ^= 0x11D
for i in range(255, 512): exp[i] = exp[i - 255]
def umn(a, b): return 0 if a == 0 or b == 0 else exp[log[a] + log[b]]
g = [1]
for i in range(1, 7):
    ng = [0] * (len(g) + 1)
    for j, c in enumerate(g): ng[j] ^= c; ng[j + 1] ^= umn(c, exp[i])
    g = ng
s_g, _, l_g = C.odna(r"\(x − αi\) = x6 \+ 126x5 \+ 4x4 \+ 158x3 \+ 58x2 \+ 49x \+ 117")
koef = [1] + [int(v) for v in re.findall(r"\+ (\d+)x", l_g)] + [int(re.search(r"\+ (\d+),?\s*$", l_g.strip()).group(1))]
assert koef == g == [1, 126, 4, 158, 58, 49, 117], (koef, g)
s_rs = C.odna(r"The PLCP header shall use a systematic \(23, 17\) Reed-Solomon outer code to improve")[0]
s_hcs = C.odna(r"CCITT CRC-16 header check sequence \(HCS\)\. The CCITT CRC-16 HCS shall be the ones")[0]
ZAPISI.append(Z("ECMA-368 заголовок PLCP: RS(23,17) над GF(2^8) (укороченный RS(255,249), корни α^1…α^6) + HCS CRC-16 CCITT (init FFFF, дополнение)", SL, "РС",
  {"RS": "p(z) = z^8 + z^4 + z^3 + z^2 + 1, g(x) = ∏_{i=1..6}(x − α^i) = x^6 + 126x^5 + 4x^4 + 158x^3 + 58x^2 + 49x + 117; 17 октетов = 5 (заголовок PHY) + 12 (скремблированные заголовок MAC + HCS); 232 нуля впереди; биты — младшим вперёд; r5 первым",
   "HCS": "CRC-16 x^16 + x^12 + x^5 + 1, регистр в единицы, передаётся дополнение; по заголовкам PHY+MAC до скремблирования",
   "кадр заголовка": "PHY(40) ∥ 6 хвостовых ∥ MAC+HCS (скрембл.) ∥ 6 хвостовых ∥ RS-чётность 48 ∥ 4 хвостовых; далее CC 1/3"},
  "WiMedia UWB", [C.ist(s_rs, "10.3.2"), C.ist(s_g, "формула (7)"), C.ist(s_hcs, "10.3.3")],
  "g(x) пересчитан из корней α^1…α^6 над GF(256) с p = 0x11D и совпал с формулой (7) стандарта",
  "есть: rs_bch.py (RS над GF(256) любой длины, укороченный); crc_katalog (CRC-16/X-25 — init FFFF, дополнение, отражённый)"))

s_cc = C.odna(r"The convolutional encoder shall use the rate R = 1/3 code with generator polynomials, g0 =")[0]
assert dfree([0o133, 0o165, 0o171], 7) == 15
VYK = {"1/2": ["A0", "C0"], "5/8": ["A0", "B0", "C1", "A2", "B2", "C3", "A4", "B4"], "3/4": ["A0", "B0", "C1", "C2"]}
s_20 = C.odna(r"Figure 20 - An example of bit-stealing and bit-insertion for R = 1/2 code")[0]
s_21 = C.odna(r"Figure 21 - An example of bit-stealing and bit-insertion for R = 5/8 code")[0]
s_22 = C.odna(r"Figure 22 - An example of bit-stealing and bit-insertion for R = 3/4 code")[0]
kus_i, s_i = C.kusok(r"Table 30 - Parameters for the Interleaver", r"=====СТР")
s_il = C.odna(r"^Bit interleaving\s*$")[0]
js = tablica("ecma368_vykalyvanie", VYK, "ECMA-368: переданные биты за период выкалывания материнского кода 1/3 (A = g0 133, B = g1 165, C = g2 171)",
   [C.ist(s_20, "рис. 20"), C.ist(s_21, "рис. 21"), C.ist(s_22, "рис. 22"), {"файл": "risunki/ecma368_str72.png", "страница|строки": "снимок стр. 72"}, {"файл": "risunki/ecma368_str73.png", "страница|строки": "снимок стр. 73"}],
   "прочитано с рисунков (снимки страниц приложены); число переданных бит 2/8/4 соответствует скоростям 1/2, 5/8, 3/4")
for r, s in VYK.items():
    k = int(r.split("/")[0]); n = int(r.split("/")[1]); per = len({int(v[1:]) for v in s})
    assert len(s) * k == n * per or len(s) / per == n / k
ZAPISI.append(Z("ECMA-368 свёрточный 1/3 K=7 (g0 = 133, g1 = 165, g2 = 171) с выкалыванием 1/2, 5/8, 3/4; перемежитель: символьный (6 символов), тоновый (NTint × 10), циклический сдвиг", SL, "свёрточный",
  {"многочлены": "133, 165, 171 (восьм.), выход A, B, C; нулевой старт и хвост 6 нулей", "dfree 1/3": 15, "выкалывание": VYK, "таблица": js,
   "перемежитель": "a_S[i] = a[⌊i/N_CBPS⌋ + (6/N_TDS)·mod(i, N_CBPS)]-типа (формула 15), тоновый a_T[j] = a_S[⌊j/N_Tint⌋ + 10·mod(j, N_Tint)], затем циклический сдвиг по символу (табл. 30)",
   "скорости": "заголовок PLCP — 1/3; PSDU — 1/3, 1/2, 5/8, 3/4 (53,3…480 Мбит/с, QPSK/DCM)"},
  "WiMedia UWB", [C.ist(s_cc, "10.7"), C.ist(s_20, "рис. 20"), C.ist(s_21, "рис. 21"), C.ist(s_22, "рис. 22"), C.ist(s_il, "10.8"), C.ist(s_i, "табл. 30"), ET.ist(1, "ETSI TS 102 455 — та же спецификация")],
  "dfree(133,165,171) = 15 вычислено; шаблоны выкалывания — прочитаны с рисунков стандарта (текст рисунка в PDF неоднозначен — приложены снимки страниц)",
  "частично: svyortka.py (1/3 K=7 находится вслепую), vykalyvanie.py; перемежитель ECMA-368 — нет"))

# второй проход (проверка полноты): вместо пустого источника — руководство Decawave DW1000 и обзор Coppens et al. (IEEE Access 2022, CC BY)
DW = Tekst("istochniki/uwb/dw1000_user_manual_2.09.pdf"); CO = Tekst("istochniki/uwb/arxiv_2202.02190_Coppens_UWB_overview.pdf")
s_h1, _, _ = DW.odna(r"The SECDED \(single error correct, double error detect\) field, S5–S0, is a set of six parity check bits")
s_h2, _, _ = DW.odna(r"Both SECDED and RS codes are systematic meaning that the data can be")
s_h3, _, l_h3 = DW.odna(r"The length-8 SFD sequence is:")
s_h4, _, _ = DW.odna(r"The length-64 SFD sequence is:")
s_h5, _, _ = DW.odna(r"IEEE 802\.15\.4 standard polynomial, x16 \+ x12 \+ x5 \+ 1")
s_h6, _, _ = DW.odna(r"Figure 33 above shows the bits of the PHR\. These are transmitted bit-0 first in time")
s_c1, _, _ = CO.odna(r"using systematic Reed-Solomon block code\. Figure 7 shows")
s_c2, _, _ = CO.odna(r"rate convolutional encoder with K = 3\. The IEEE 802\.15\.4z")
kus64, _ = DW.kusok(r"The length-64 SFD sequence is:", r"The DW1000 has the capability")
sfd64 = [int(x) for x in re.findall(r"[+-]?\d", kus64.replace("+", ""))]
sfd8 = [int(x) for x in re.findall(r"[+-]?\d", l_h3.split("is:")[1].replace("+", ""))]
assert sfd8 == [0, 1, 0, -1, 1, 0, 0, -1] and len(sfd64) == 64 and sfd64[:8] == sfd8, (sfd8, len(sfd64))
ZAPISI.append(Z("IEEE 802.15.4a/4z HRP UWB: PHR 19 бит с SECDED (6 бит), данные — систематический РС, затем свёрточный 1/2 K=3 (4z — необязательный K=7); SFD 8/64; FCS CRC-16 (параметры РС и многочлены — не найдены в открытом доступе)",
  "UWB IEEE 802.15.4a/z (HRP)", "каскадный: РС + свёрточный 1/2 (многочлены — не найдены в открытом доступе)",
  {"PHR": "19 бит, передаются битом 0 вперёд: R1 R0 (скорость), L6…L0 (длина), RNG, EXT, P1 P0 (длительность преамбулы), C5…C0 — SECDED (исправляет 1, обнаруживает 2 ошибки)",
   "данные": "систематический код Рида — Соломона (только для данных, не для PHR), затем свёрточный 1/2 K=3 (BPM-BPSK: систематический бит — позиция, проверочный — фаза); 802.15.4z — необязательный K=7",
   "SFD": {"8 символов": sfd8, "64 символа (110 кбит/с)": sfd64},
   "преамбула": "троичные коды Ипатова длины 31 (PRF 16 МГц, 8 кодов) и 127 (PRF 64 МГц, 16 кодов), идеальная периодическая АКФ",
   "FCS": "CRC-16 x^16+x^12+x^5+1 (как 802.15.4, CRC-16/KERMIT)",
   "не найдено": "длина и порождающий многочлен РС, многочлены свёрточного кода K=3/K=7, матрица SECDED — только в IEEE 802.15.4-2020/4z (IEEE GET, учётная запись); открытых реализаций с лицензией не найдено"},
  "UWB-метки и RTLS (Decawave/Qorvo DW1000/DW3000), Apple U1, FiRa, цифровые ключи CCC",
  [DW.ist(s_h6, "рис. 33 PHR"), DW.ist(s_h1, "SECDED"), DW.ist(s_h2, "РС систематический"), DW.ist(s_h3, "SFD 8"), DW.ist(s_h4, "SFD 64"), DW.ist(s_h5, "FCS"),
   CO.ist(s_c1, "РС → SECDED, систематический"), CO.ist(s_c2, "K = 3 / K = 7")],
  "структура и SFD разобраны из руководства DW1000 (SFD 64 начинается с SFD 8 — проверено); упоминание K=3/K=7 и систематического РС подтверждено вторым документом (обзор Coppens et al.); параметры кодов не проверены — первоисточник недоступен",
  "частично: crc_katalog (CRC-16/KERMIT); РС/свёрточный HRP — нет"))
