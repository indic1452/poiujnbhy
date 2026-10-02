"""Авиация: Mode S / ADS-B 1090ES (CRC-24 Касами), UAT 978 МГц (RS над GF(256), перемежение 6×92), КОСПАС-САРСАТ 406 МГц
(БЧХ (82,61)/(38,26), 2-е поколение БЧХ (250,202) + ПСП X23+X18+1), HFDL (свёрточный K=7, повтор, перемежитель 40 строк, скремблер),
VDL режимы 2 и 4, ACARS. Сверка: dump978, pyModeS, dumphfdl, acarsdec (открытый код), проект (adsb.py, vdl2.py, acars.py)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
PR = "/home/user/poiujnbhy/src/reportgen/potok/"
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})

def delenie(bits, g_bits):
    """Остаток деления многочлена (список бит, старший первым) на g (список бит, старший первым)."""
    r = list(bits); n = len(g_bits)
    for i in range(len(r) - n + 1):
        if r[i]:
            for j in range(n): r[i + j] ^= g_bits[j]
    return r[-(n - 1):]
def iz_stepeney(st):
    m = max(st); return [1 if (m - i) in st else 0 for i in range(m + 1)]

# --- Mode S: G(x) (Gertz, LL ATC-117, ф. 2-11) ------------------------------------------------------------------------------
MS = Tekst("istochniki/aviaciya/LL_ATC-117_Gertz_1984_Mode_S_parity.pdf")
kus, s_ms = MS.kusok(r"The code chosen for Mode S, for both the uplink and downlink, is a cyclic", r"\(2-11\)")
kus = kus.replace("xIS", "x15").replace("x lO", "x10")
st_ms = sorted({int(x) for x in re.findall(r"x\s*(\d+)", kus)} | ({0} if re.search(r"\+\s*1\s*$", kus.strip()) else set()), reverse=True)
G_MS = sum(1 << s for s in st_ms)
assert G_MS == 0x1FFF409, hex(G_MS)
pm = kod("pyModeS", "src/pyModeS/_bits.py")
assert "_CRC_POLY = 0xFFF409" in open(os.path.join(KOREN, pm)).read()
assert "ПОРОЖДАЮЩИЙ = 0xFFF409" in open(PR + "adsb.py").read()
msg = bin(int("8D406B902015A678D4D220AA4BDA", 16))[2:].zfill(112)
assert delenie([int(b) for b in msg], iz_stepeney(st_ms)) == [0] * 24                   # DF17 из тестов pyModeS: остаток 0
kod("pyModeS", "tests/test_callsign.py")
s_ms_ol = MS.naiti(r"[Aa]ddress|overlay|AP field")[0][0] if MS.naiti(r"[Aa]ddress") else s_ms
ORL = Tekst("istochniki/aviaciya/LL_ATC-42b_Orlando_1982_Mode_S.pdf")
rec("Mode S / ADS-B 1090ES: CRC-24 (код Касами) с наложением адреса (AP/PI)", "Авиация: Mode S / ADS-B 1090 МГц", "CRC-подобный",
    {"G(x)": "x24+x23+x22+x21+x20+x19+x18+x17+x16+x15+x14+x13+x12+x10+x3+1 (= 0x1FFF409; без старшего — 0xFFF409)",
     "длины": "56 бит (короткие: DF0/4/5/11) и 112 бит (длинные: DF16/17/18/20/21/24); паритет — последние 24 бита",
     "наложение": "AP = паритет ⊕ адрес ИКАО (запросы/ответы DF0/4/5/16/20/21); PI = паритет ⊕ код запросчика (DF11/17/18) — у DF17 PI = 0",
     "свойства": "обнаружение пакетов ошибок до 24 бит, d = 6; исправление 1–2 бит по синдрому (dump1090)", "модуляция": "PPM 1 Мбит/с, преамбула 8 мкс (4 импульса)",
     "порядок": "старший бит первым"},
    "вторичная радиолокация Mode S, ADS-B 1090ES, TIS-B, ADS-R, ACAS", [MS.ist(s_ms, "ф. (2-11) G(x)"), ist_kod(pm, r"_CRC_POLY = 0xFFF409", "pyModeS"), ist_kod("istochniki/kod/pyModeS/tests/test_callsign.py", "8D406B902015A678D4D220AA4BDA", "тестовое сообщение")],
    "степени G(x) разобраны из текста Gertz (ф. 2-11) = 0x1FFF409 = pyModeS _CRC_POLY | x24 = adsb.py проекта; сообщение DF17 8D406B902015A678D4D220AA4BDA из тестов pyModeS делится на G(x) без остатка",
    "ЕСТЬ в проекте: adsb.py (CRC-24, DF17/18, CPR, позывной); crc_katalog")

# --- UAT (Annex 10, поправка 82) ------------------------------------------------------------------------------------------------
U = Tekst("istochniki/aviaciya/ICAO_Annex10_Amdt82_UAT_SARP.pdf")
s_sd, _, _ = U.odna(r"^111010101100110111011010010011100010")
s_su, _, _ = U.odna(r"^000101010011001000100101101100011101")
SD = int("111010101100110111011010010011100010", 2); SU = int("000101010011001000100101101100011101", 2)
assert SD ^ SU == (1 << 36) - 1                                                  # восходящая синхро = инверсия нисходящей
up = kod("dump978", "uat_protocol.h"); fe = kod("dump978", "fec.cc")
t = open(os.path.join(KOREN, up)).read()
assert "DOWNLINK_SYNC_WORD = 0xEACDDA4E2" in t and SD == 0xEACDDA4E2 and "UPLINK_SYNC_WORD = 0x153225B1D" in t and SU == 0x153225B1D
assert t.count("= 0x187;") == 3 and "DOWNLINK_SHORT_ROOTS = 12" in t and "DOWNLINK_LONG_ROOTS = 14" in t and "UPLINK_BLOCK_ROOTS = 20" in t
assert open(os.path.join(KOREN, fe)).read().count("/* fcr */ 120") == 3
s_rs = U.odna(r"Parity shall be a RS \(30, 18\) code")[0]; s_rsl = U.odna(r"Parity shall be a RS \(48, 34\) code")[0]
s_p = U.odna(r"^P = 131 for RS \(30, 18\) code")[0]; s_up = U.odna(r"FEC parity generation for each of the six blocks shall be a RS \(92,72\) code")[0]
s_p139 = U.odna(r"^P = 139, and")[0]; s_il = U.odna(r"The interleaver is represented by a 6x92 matrix")[0]; s_t125 = U.odna(r"Table 12-5: Ground uplink interleaver matrix")[0]
# корни α^120…α^P: число корней = P − 119 = n − k
assert (131 - 119, 133 - 119, 139 - 119) == (30 - 18, 48 - 34, 92 - 72)
# RS: порождающий многочлен и самопроверка кодирования (систематический, корни α^120.., поле 0x187)
EXP = [0] * 512; LOG = [0] * 256; x = 1
for i in range(255):
    EXP[i] = x; LOG[x] = i; x <<= 1
    if x & 0x100: x ^= 0x187
for i in range(255, 512): EXP[i] = EXP[i - 255]
def gmul(a, b): return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]
def rs_gen(nr, fcr=120):
    g = [1]
    for i in range(nr):
        r = EXP[fcr + i]; g = [a ^ gmul(b, r) for a, b in zip(g + [0], [0] + g)]
    return g                                                                     # коэффициенты от старшего
def rs_enc(data, nr):
    g = rs_gen(nr); r = [0] * nr
    for d in data:
        fb = d ^ r[0]; r = r[1:] + [0]
        if fb: r = [a ^ gmul(fb, c) for a, c in zip(r, g[1:])]
    return list(data) + r
def rs_sindr(cw, nr):
    out = []
    for i in range(nr):
        a = EXP[120 + i]; s = 0
        for c in cw: s = gmul(s, a) ^ c
        out.append(s)
    return out
import random; random.seed(978)
for n, k in ((30, 18), (48, 34), (92, 72)):
    d = [random.randrange(256) for _ in range(k)]
    assert rs_sindr(rs_enc(d, n - k), n - k) == [0] * (n - k)
# перемежитель восходящей: матрица 6×92 (строка — блок A..F), передача по столбцам
PER = [r * 92 + c for c in range(92) for r in range(6)]                           # порядок передачи → индекс (блок·92 + байт)
assert sorted(PER) == list(range(552)) and PER[:7] == [0, 92, 184, 276, 368, 460, 1]
pt = tablica("uat_uplink_peremezhitel", {"porjadok_peredachi_v_indeks_blok92": PER, "sinhro_vniz": "0xEACDDA4E2", "sinhro_vverh": "0x153225B1D",
              "rs": {"pole": "0x187", "fcr": 120, "prim": 1, "(30,18)": rs_gen(12), "(48,34)": rs_gen(14), "(92,72)": rs_gen(20)}},
        "UAT: порядок передачи байт восходящего сообщения (6 блоков RS(92,72) по столбцам матрицы 6×92, табл. 12-5); порождающие многочлены RS (коэффициенты от старшего)",
        [U.ist(s_t125, "Table 12-5"), U.ist(s_il, "12.4.4.2.2.3")], "синхрослова и параметры RS совпали с dump978 (uat_protocol.h, fec.cc); кодирование случайных блоков даёт нулевые синдромы")
rec("UAT (978 МГц) ADS-B: RS(30,18) короткое / RS(48,34) длинное над GF(256)", "Авиация: UAT (ИКАО Прил. 10, DO-282)", "РС",
    {"поле": "p(x) = x8+x7+x2+x+1 (0x187)", "порождающий": "∏(x − α^i), i = 120…P; P = 131 (30,18), P = 133 (48,34)", "данные": "144 бит (18 байт) / 272 бит (34 байт)",
     "синхро": "36 бит 111010101100110111011010010011100010 (0xEACDDA4E2), левый первым", "порядок": "проверочные байты от старшего коэффициента, биты — старший первым; проверочные после данных",
     "модуляция": "CPFSK (h≈0,6), 1,041667 Мбит/с", "таблица": pt},
    "ADS-B UAT (США, 978 МГц), FIS-B/TIS-B", [U.ist(s_sd, "12.4.4.1.1 синхро"), U.ist(s_rs, "RS(30,18)"), U.ist(s_rsl, "RS(48,34), p(x)"), U.ist(s_p, "P"), ist_kod(up, "DOWNLINK_SYNC_WORD", "dump978"), ist_kod(fe, "fcr", "dump978")],
    "синхрослово и параметры (поле 0x187, fcr 120, prim 1, 12/14 корней) сверены с dump978; P−119 = n−k; кодер по формуле даёт кодовые слова с нулевыми синдромами",
    "нет разбора UAT; RS над GF(256) с произвольными полем/fcr есть в rs_bch.py — внедрение: синхрослово + RS + разбор сообщений (просто)")
rec("UAT восходящее (наземное) сообщение: 6 × RS(92,72) с перемежением по столбцам матрицы 6×92", "Авиация: UAT (ИКАО Прил. 10, DO-282)", "каскадный: РС + блочный перемежитель",
    {"данные": "3456 бит = 6 × 576 (72 байта)", "RS": "(92,72), P = 139 (20 корней α^120…α^139), поле 0x187", "перемежитель": "матрица 6×92 (строка = блок A–F), передача по столбцам с левого верхнего; таблица " + pt,
     "синхро": "36 бит 000101010011001000100101101100011101 (0x153225B1D, инверсия нисходящей)"},
    "UAT FIS-B (погода, NOTAM), TIS-B", [U.ist(s_su, "12.4.4.2.1"), U.ist(s_up, "RS(92,72)"), U.ist(s_p139, "P = 139"), U.ist(s_il, "перемежение"), ist_kod(up, "UPLINK_BLOCK_ROOTS", "dump978")],
    "как у нисходящего; перестановка построена программно и проверена (биекция 552, начало 0,92,184,…)", "нет в проекте; внедрение простое (rs_bch + перестановка)")

# --- КОСПАС-САРСАТ ---------------------------------------------------------------------------------------------------------------
C1 = Tekst("istochniki/aviaciya/CS_T001_mar2021_406MHz_beacons.pdf")
s_g1 = C1.odna(r"X21 \+ X18 \+ X17 \+ X15 \+ X14 \+ X12 \+ X11\+ X8 \+ X7 \+ X6 \+ X5 \+ X \+ 1")[0]
g1 = [int(c) for c in "1001101101100111100011"]
assert g1 == iz_stepeney({21, 18, 17, 15, 14, 12, 11, 8, 7, 6, 5, 1, 0})
m_line = C1.odna(r"^m\(X\)=0101011011")[2]
m_bits = [int(c) for c in m_line.split("=")[1].strip()]
assert len(m_bits) == 82
ost = delenie(m_bits, g1)
bch1 = C1.odna(r"BCH Error-Correcting Code:\s+001011001010101001001")
assert "".join(map(str, ost)) == "001011001010101001001", ost                       # пример прил. B1
g2 = iz_stepeney({12, 10, 8, 5, 4, 3, 0})
def pmul(a, b):
    r = 0
    for i in range(b.bit_length()):
        if b >> i & 1: r ^= a << i
    return r
assert pmul(0b1000011, 0b1010111) == int("".join(map(str, g2)), 2)                 # (1+x+x6)(1+x+x2+x4+x6)
s_g2 = C1.odna(r"\(1 \+ x \+ x6\) \(1 \+ x \+ x2 \+ x4\+ x6\)")[0]; s_fs = C1.odna(r"frame synchronization pattern in normal operation shall be 000101111")[0]
s_82 = C1.odna(r"BCH \(82,61\) error-correcting code")[0]
rec("КОСПАС-САРСАТ 406 МГц (1-е поколение): БЧХ(82,61) + БЧХ(38,26), укороченные из (127,106) и (63,51)", "Авиация/морская: КОСПАС-САРСАТ (C/S T.001)", "БЧХ",
    {"БЧХ-1": "g(X) = X21+X18+X17+X15+X14+X12+X11+X8+X7+X6+X5+X+1 (1001101101100111100011), биты 25–85 → проверочные 86–106, исправляет 3",
     "БЧХ-2": "g(X) = (1+x+x6)(1+x+x2+x4+x6) = 1+x3+x4+x5+x8+x10+x12, биты 107–132 → 133–144, исправляет 2",
     "кадр": "15 единиц битовой синхронизации + 9 бит кадровой 000101111 (самотест 011010000) + 88 (короткое) / 120 (длинное) бит",
     "модуляция": "двухфазная ФМ ±1,1 рад, манчестер, 400 бит/с"},
    "аварийные радиобуи ELT/EPIRB/PLB 406 МГц", [C1.ist(s_82, "BCH-1"), C1.ist(s_g1, "g(X) прил. B"), C1.ist(C1.odna(r"^m\(X\)=0101011011")[0], "пример B1"), C1.ist(s_g2, "g(x) БЧХ-2"), C1.ist(s_fs, "синхро")],
    "тестовый вектор прил. B1: деление m(X) из текста на g(X) дало ровно 001011001010101001001; g(X) по степеням = двоичной записи; g БЧХ-2 = произведению минимальных многочленов",
    "нет в проекте; БЧХ-декодер общего вида (rs_bch.py) подходит — внедрение простое")
C2 = Tekst("istochniki/aviaciya/CS_T018_mar2021_SGB.pdf")
s_g3 = C2.odna(r"1110001111110101110000101110111110011110010010111")[0]
g3 = int("1110001111110101110000101110111110011110010010111", 2)
mins = [iz_stepeney(s) for s in ({8, 4, 3, 2, 0}, {8, 6, 5, 4, 2, 1, 0}, {8, 7, 6, 5, 4, 1, 0}, {8, 6, 5, 3, 0}, {8, 7, 5, 4, 3, 2, 0}, {8, 7, 6, 5, 2, 1, 0})]
pr = 1
for m in mins: pr = pmul(pr, int("".join(map(str, m)), 2))
assert pr == g3, (bin(pr), bin(g3))
s_prn = C2.odna(r"G\(x\) = X23 \+ X18 \+1")[0]; s_250 = C2.odna(r"48 bits BCH\(250,202\)")[0]
rec("КОСПАС-САРСАТ 2-го поколения (SGB): БЧХ(250,202) + ПСП-расширение DSSS-OQPSK", "Авиация/морская: КОСПАС-САРСАТ (C/S T.018)", "БЧХ + расширение спектра",
    {"БЧХ": "(250,202), укороченный (255,207), t = 6; g(X) = НОК(m1,m3,m5,m7,m9,m11) = 1110001111110101110000101110111110011110010010111 (49 бит)",
     "ПСП": "G(x) = X23 + X18 + 1, два усечённых отрезка по 38 400 элементов (I и Q), начальные значения — табл. 2.2", "модуляция": "DSSS-OQPSK, 300 бит/с, 128 элементов/бит"},
    "радиобуи 2-го поколения (MEOSAR)", [C2.ist(s_250, "BCH(250,202)"), C2.ist(s_g3, "g(X) прил. B"), C2.ist(s_prn, "ПСП")],
    "g(X) из текста = произведению шести минимальных многочленов m1…m11 из того же текста", "нет в проекте; БЧХ длиной 255 — rs_bch.py; ПСП — skrembler.py (просто)")

# --- HFDL (ARINC 635) — по открытому коду dumphfdl ------------------------------------------------------------------------------
hf = kod("dumphfdl", "src/hfdl.c"); fh = kod("dumphfdl", "src/libfec/fec.h"); cr = kod("dumphfdl", "src/crc.c")
th = open(os.path.join(KOREN, hf)).read()
assert "#define DEINTERLEAVER_ROW_CNT 40" in th and "#define DEINTERLEAVER_POP_ROW_SHIFT 9" in th and "lfsr_init = 0x6959u" in th and "numbits = 15" in th
assert re.search(r"code_rate = 4,\s*\.deinterleaver_push_column_shift = 17", th) and re.search(r"code_rate = 2,\s*\.deinterleaver_push_column_shift = 23", th)
tf = open(os.path.join(KOREN, fh)).read(); assert "#define\tV27POLYA\t0x6d" in tf and "#define\tV27POLYB\t0x4f" in tf
assert "CRC-16-CCITT, poly: 0x1021" in open(os.path.join(KOREN, cr)).read()
rec("HFDL (ARINC 635): свёрточный K=7 1/2 + повтор ×2/×4 + перемежитель 40 строк + скремблер 15 бит", "Авиация: HFDL", "каскадный: свёрточный + повторение + перемежитель + скремблер",
    {"свёрточный": "K=7 1/2, многочлены 0x6D и 0x4F (libfec V27POLYA/B = 155/117 восьм. — те же, что у CCSDS 171/133 в обратной записи)",
     "повтор": "300 бит/с — ×4 (code_rate 4), 600 бит/с — ×2; 1200 (QPSK) и 1800 (8PSK) — без повтора",
     "перемежитель": "40 строк; запись по строкам со сдвигом столбца −17 (одиночный слот 1,8 с) / −23 (двойной 4,2 с), чтение с шагом строки 9",
     "скремблер": "15 бит, начальное 0x6959 (x15+x+1, период 120 бит на кадр)", "CRC": "CRC-16-CCITT (0x1021) в LPDU/SPDU", "модуляция": "BPSK/QPSK/8PSK 1800 Бод, кадр 72 сегмента данных + 1 тренировка"},
    "авиасвязь КВ-данных (HFDL, ARINC 635 / ICAO Doc 9741)", [ist_kod(hf, "DEINTERLEAVER_ROW_CNT 40", "перемежитель"), ist_kod(hf, "lfsr_init = 0x6959u", "скремблер"), ist_kod(hf, r"code_rate = 4", "повтор"), ist_kod(fh, "V27POLYA", "многочлены")],
    "первоисточник (ARINC 635) платный — параметры взяты из открытого кода dumphfdl (GPL-3) с assert по строкам; скремблер совпадает с VDL2 (0x6959, x15+x+1) проекта", "нет в проекте; свёрточный K=7 и скремблер есть — перемежитель и кадр внедрить (средне)")

# --- VDL Mode 2 / Mode 4, ACARS ----------------------------------------------------------------------------------------------
V2 = Tekst("istochniki/aviaciya/en_30184101v010401p.pdf")
s_v2m = V2.odna(r"Mode 2 shall use D8PSK, using a raised cosine filter")[0]; s_v2t = V2.odna(r"header FEC \(5 bits\)")[0]; s_v2r = V2.odna(r"forward error correction mechanism based on interleaving and Reed Solomon coding")[0]
vd = open(PR + "vdl2.py").read(); assert "RS_N, RS_K, RS_ПОЛЕ, RS_FCR = 255, 249, 0x187, 120" in vd and "LFSR_НАЧАЛО = 0x6959" in vd
rec("VDL Mode 2: RS(255,249) с перемежением + заголовок (25,20) + скремблер x15+x+1", "Авиация: VDL (ETSI EN 301 841)", "каскадный: РС + блочный код + скремблер",
    {"RS": "(255,249) над GF(256), 0x187, первый корень α^120; укороченный последний блок (2/4/6 проверочных)", "заголовок": "3 резервных + длина 17 бит + 5 проверочных (25,20)",
     "скремблер": "x15+x+1, начальное 0x6959", "модуляция": "D8PSK 10 500 Бод, RRC α=0,6, 3 бита на символ младшим первым", "кадр": "AVLC (HDLC, FCS CRC-16/X.25)"},
    "авиационная передача данных ОВЧ (ACARS over AVLC, CPDLC)", [V2.ist(s_v2m, "5.3"), V2.ist(s_v2t, "5.4"), V2.ist(s_v2r, "FEC")],
    "RS и скремблер — по ИКАО Doc 9776 через dumpvdl2 (сверено в проекте vdl2.py); EN 301 841-1 подтверждает структуру заголовка и D8PSK", "ЕСТЬ в проекте: vdl2.py")
V4 = Tekst("istochniki/aviaciya/en_30284201v010301p.pdf")
s_v4 = V4.odna(r"The training sequence shall be the 24 bit sequence 0101 0101 0101 0101 0101 0101")[0]; s_v4m = V4.odna(r"The modulation scheme shall be Gaussian Filtered Frequency Shift Keying")[0]
V42 = Tekst("istochniki/aviaciya/en_30284202v010401p.pdf"); s_v4c = V42.odna(r"The VSS sublayer shall compute a 16 bit CRC according to ISO/IEC 13239")[0]
rec("VDL Mode 4: GFSK 19,2 кбит/с, NRZI, обучающая 24 бита, CRC-16 ISO/IEC 13239, бит-стаффинг", "Авиация: VDL (ETSI EN 302 842)", "CRC-подобный + линейное кодирование",
    {"CRC": "16 бит ISO/IEC 13239 (= CRC-16/X-25)", "линия": "NRZI (0 → смена тона), бит-стаффинг HDLC", "обучающая": "0101…(24 бита)", "модуляция": "GFSK, девиация ±2400 Гц, 19 200 бит/с, слоты STDMA"},
    "ADS-B VDL4 (Швеция, Россия), навигационное вещание", [V4.ist(s_v4m, "GFSK/NRZI"), V4.ist(s_v4, "обучающая"), V42.ist(s_v4c, "CRC")], "по тексту EN 302 842-1/-2; CRC = hdlc.fcs16 проекта",
    "есть части: hdlc.py (FCS, стаффинг), NRZI в kanal; разбора VDL4 нет (просто)")
ac = kod("acarsdec", "acars.c"); sy = kod("acarsdec", "syndrom.h")
ta = open(os.path.join(KOREN, ac)).read(); assert "#define SYN 0x16" in ta and "#define ETX 0x83" in ta
assert "0x8408" in open(PR + "acars.py").read()
rec("ACARS (ARINC 618): 7 бит ASCII + нечётная чётность, CRC-16 CCITT (отражённый, начальное 0)", "Авиация: ACARS", "CRC-подобный + чётность",
    {"знак": "7 бит + бит нечётной чётности, младшим первым", "синхро": "преамбула единиц, '+', '*', SYN SYN (0x16) SOH", "CRC": "CRC-16/KERMIT (0x8408 отражённый, начальное 0) от режима до ETX/ETB",
     "исправление": "по таблице синдромов (acarsdec syndrom.h): 1–2 ошибки", "модуляция": "MSK 2400 бит/с (1200/2400 Гц) AM ОВЧ"},
    "ACARS ОВЧ 131,55 МГц и др.; поверх VDL2/HFDL/SATCOM", [ist_kod(ac, "#define SYN 0x16", "acarsdec"), ist_kod(ac, "fixprerr", "исправление"), ist_kod(sy, r"0x", "синдромы")],
    "ARINC 618 платный — по открытому коду acarsdec (GPL-2) и проекту acars.py (сверено ранее с живыми записями)", "ЕСТЬ в проекте: acars.py")
