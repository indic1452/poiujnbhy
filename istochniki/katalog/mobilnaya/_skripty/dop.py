"""Дополнения (продолжение сбора): STANAG 4529 (узкополосный 4285: синхро x5+x2+1, скремблер x9+x4+1, K=7, перемежитель 32 строки —
по тексту процедур соответствия 2004 г.), STANAG 4415 и 4538 (ссылки MIL-STD), DCS — цифровой кодовый шумоподавитель
(ETSI TS 103 236: Голей (23,12), 83 кодовых слова таблицы проверены делимостью), RTCM SC-104 (морские DGPS: чётность GPS (30,24),
сверена с IS-GPS-200N)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})

def ostatok(v, g):
    gl = g.bit_length() - 1
    for i in range(v.bit_length() - 1, gl - 1, -1):
        if v >> i & 1: v ^= g << (i - gl)
    return v

# ---------------- STANAG 4529 -------------------------------------------------------------------------------------------------
A = Tekst("istochniki/mil/STANAG_4529_conformance_2004.pdf")
s_rate = A.odna(r"75, 150, 300, 600, or 1200 bits per second \(bps\)\.")[0]
s_baud = A.odna(r"Modulation speed is 1200 bauds")[0]
s_1240 = A.odna(r"within 1240 Hz")[0]
s_fr = A.odna(r"A frame consists of 256 symbols")[0]
s_syn = A.odna(r"The generator polynomial is:\s+x5\s+\+\s+x2\s+\+1")[0]
s_ini = A.odna(r"generator is initially set to the following value:\s+11010")[0]
s_scr = A.odna(r"random code of length 511, the generator polynomial of which is:\s+x9\s+\+ x4\s+\+ 1")[0]
s_scr1 = A.odna(r"The generator is initialized to 1 at the start of each frame")[0]
s_n = A.odna(r"n = 4x2\s+\+ 2x1\s+\+ x0")[0]
s_k7 = A.odna(r"rate ½, constraint length 7 convolutional coding as")[0]
kus, s_t15 = A.kusok(r"Table 1\.5\.\s+Error Correction Coding", r"Legend")
KOD = re.findall(r"(\d{2,4}) bps\s+(\d) phase \((\d{3,4}) bps\)\s+(\d/\d)\s+([^\n]+)", kus)
assert [(a, c, d) for a, _, c, d, _ in KOD] == [("1200", "1800", "2/3"), ("600", "1200", "1/2"), ("300", "600", "1/2"), ("150", "600", "1/4"), ("75", "600", "1/8")], KOD
kus, s_j = A.kusok(r"Number of rows: I = 32 for all data rates", r"Interleaver synchronization")
J = {a: (int(b), int(c)) for a, b, c in re.findall(r"(1200|600|300, 150 and 75) bps\s+(\d+)\s+(\d+)", kus)}
assert J == {"1200": (48, 4), "600": (24, 2), "300, 150 and 75": (12, 1)}, J
kus, s_fl = A.kusok(r"The number of flush zeros for each of the data rates and the two interleaver", r"\n\s*k\.")
FL = {int(a): (int(b), int(c)) for a, b, c in re.findall(r"(\d{2,4}) bps\s+(\d+)\s+(\d+)", kus)}
assert set(FL) == {1200, 600, 300, 150, 75}
# Проверка согласованности таблиц: задержка перемежителя I²·j бит на выходе кода 1/2 (выкалывание — после перемежителя, как в 5066 прил. G),
# число «флеш»-нулей = I²·j·R + 102 (R — отношение информационных бит к битам перемежителя: 1/2, повтор ×2 → 1/4, ×4 → 1/8).
I = 32
R = {1200: 1 / 2, 600: 1 / 2, 300: 1 / 2, 150: 1 / 4, 75: 1 / 8}
jj = lambda s: J["1200"] if s == 1200 else J["600"] if s == 600 else J["300, 150 and 75"]
kanal = {1200: 2400, 600: 1200, 300: 600, 150: 600, 75: 600}                    # бит/с на входе перемежителя (до выкалывания 2/3)
for s in FL:
    for k in (0, 1):
        assert FL[s][k] == int(I * I * jj(s)[k] * R[s]) + 102, (s, k)
        assert abs(I * I * jj(s)[k] / kanal[s] - (20.48, 1.70667)[k]) < 1e-3, (s, k)          # 20,48 с и 1,706 с из заголовка таблицы
# синхро: x5+x2+1, начальное 11010 (X4..X0), первый символ = младший разряд начального, далее 79 тактов
def sinhro():
    r = [1, 1, 0, 1, 0]                     # X4 X3 X2 X1 X0
    out = []
    for _ in range(80):
        out.append(r[4]); fb = r[4] ^ r[2]  # x5 + x2 + 1: выход X0, обратная связь X0 ⊕ X2 → X4
        r = [fb] + r[:4]
    return out
SYN = sinhro()
assert SYN[:31] == SYN[31:62] and SYN[62:80] == SYN[:18] and sum(SYN[:31]) == 16           # период 31 (m-последовательность), 2 периода + 18
def skrembler():
    r = [1] * 9                             # X8..X0 = 1
    out = []
    for _ in range(176):
        n = 4 * r[6] + 2 * r[7] + r[8]      # x2, x1, x0
        out.append(n)
        for _ in range(3):
            fb = r[8] ^ r[4]; r = [fb] + r[:8]
    return out
SCR = skrembler()
assert len(SCR) == 176 and SCR[0] == 7                                                   # все единицы → первый символ n = 7
t = tablica("stanag4529_sinhro_skrembler", {"sinhro_80_2PSK": SYN, "skrembler_176_8PSK_n": SCR,
             "otobrazhenie": {"600 бит/с (бит→символ)": {"0": 0, "1": 4}, "1200 (дибит)": {"00": 0, "01": 2, "11": 4, "10": 6},
                              "1800 (трибит)": {"000": 1, "001": 0, "010": 2, "011": 3, "100": 6, "101": 7, "110": 5, "111": 4}},
             "perem_j": {"1200": [48, 4], "600": [24, 2], "300/150/75": [12, 1]}, "flush": {str(k): v for k, v in FL.items()}},
             "STANAG 4529: 80 символов синхро (2PSK, x5+x2+1, нач. 11010), 176 символов скремблера 8PSK (x9+x4+1, нач. единицы, 3 сдвига на символ), "
             "таблицы 1.2–1.4 отображения, приращения j перемежителя и число нулей сброса",
             [A.ist(s_syn, "рис. 1.3"), A.ist(s_scr, "рис. 1.4"), A.ist(s_j, "перемежитель"), A.ist(s_fl, "нули сброса")],
             "генераторы по тексту; синхро — период 31 (2 периода + 18, как сказано), табл. нулей сброса = I²·j·R + 102 для всех 10 клеток, "
             "задержки 20,48/1,706 с = I²·j / скорость — assert")
rec("STANAG 4529 (узкополосный ВЧ модем НАТО, 1240 Гц): 4285-кадр 1200 Бод + свёрточный K=7 1/2 (→2/3, повтор ×2/×4) + свёрточный перемежитель 32 строки",
    "НАТО: STANAG 4529", "каскадный: свёрточный + свёрточный перемежитель + скремблер 8PSK",
    {"скорости": "75/150/300 → канал 600 бит/с (2PSK), 600 → 1200 (4PSK), 1200 → 1800 (8PSK); без кодирования 600/1200/1800",
     "модуляция": "ФМ 1200 Бод, поднесущая 800–2400 Гц шаг 100 (по умолч. 1700), 99 % мощности в 1240 Гц",
     "кадр": "256 символов = 213,3 мс: 80 синхро (2PSK) + 3 × (32 данных + 16 опорных) + 32 данных; опорные — символ 0",
     "синхро": "x5 + x2 + 1, начальное 11010, первый символ — младший разряд начального; период 31 (2 периода + 18)",
     "скремблер": "x9 + x4 + 1, начальное — единицы в начале каждого кадра, символ n = 4x2 + 2x1 + x0, сдвиг на 3 разряда; сложение по модулю 8 только данных и опорных",
     "свёрточный": "K = 7, 1/2 как в STANAG 4285 прил. E (многочлены 133/171₈ — см. запись 5066 прил. G), 1200 бит/с — выкалывание до 2/3; 150 — повтор ×2, 75 — ×4",
     "перемежитель": "как 4285 прил. E разд. 3: I = 32 строки, приращение j: 1200 — 48/4, 600 — 24/2, 300–75 — 12/1 (длинный 20,48 с / короткий 1,706 с)",
     "нули сброса": "1200: 24678/2150, 600: 12390/1126, 300: 6246/614, 150: 3174/358, 75: 1638/230", "таблицы": t},
    "морская ВЧ связь НАТО в узких каналах (1240 Гц), совместимость с STANAG 4285",
    [A.ist(s_rate, "1.2 a"), A.ist(s_baud, "1.2 b"), A.ist(s_1240, "1.2 d"), A.ist(s_fr, "1.2 e кадр"), A.ist(s_syn, "1.2 f синхро"), A.ist(s_ini, "начальное 11010"),
     A.ist(s_scr, "1.2 g скремблер"), A.ist(s_scr1, "начальное"), A.ist(s_n, "n = 4x2+2x1+x0"), A.ist(s_k7, "1.2 j K=7"), A.ist(s_t15, "табл. 1.5"),
     A.ist(s_j, "перемежитель"), A.ist(s_fl, "нули сброса")],
    "генераторы построены по тексту (tablicy); самосогласованность: 10 значений числа нулей сброса = I²·j·R + 102 и задержки 20,48/1,706 с — assert; "
    "многочлены K=7 из STANAG 5066 прил. G («тот же, что прил. E 4285»); таблица 1.2–1.4 = 4285 (обзоры)",
    "частично: свёрточный K=7 133/171 и x9+x4+1 есть в проекте (kod.py, скремблеры); кадр 256 символов и перемежитель 32 строки — внедрить (средне)")
S = Tekst("istochniki/mil/STANAG_5066_v2.pdf")
s_k7g = S.odna(r"The constraint length 7, rate 1/2 convolutional codec employed is the same as is used")[0]
ZAPISI[-1]["источник"].append(S.ist(s_k7g, "5066 G: K=7 тот же, что 4285 прил. E"))
# Синхро 4529 = синхро 4285 (80 символов): ссылка на 4481/4285 в stanag.py; сверяем с преамбулой? — у 4285 те же 80 символов (обзор MT 2009)

# ---------------- STANAG 4415, 4538 — соответствие MIL-STD ----------------------------------------------------------------------
C = Tekst("istochniki/mil/MIL-STD-188_110C.037889.PDF")
s_4415 = C.odna(r"Robust 75 bps mode\. If included, this mode shall be in accordance with STANAG 4415")[0]
s_4415b = C.odna(r"The optional robust serial tone mode shall employ the waveform specified above for 75 bps")[0]
s_4529c = C.odna(r"it shall be in accordance with STANAG 4529")[0]
JO = Tekst("istochniki/mil/HF_modems_explained_stanag_Jorgenson.pdf")
s_jo = JO.odna(r"^4415 and\s*$")[0]
rec("STANAG 4415 (робастный ВЧ 75 бит/с) = волновая форма 75 бит/с MIL-STD-188-110 (Уолш-модуляция 8PSK, K=7 1/2)",
    "НАТО: STANAG 4415", "свёрточный K=7 1/2 + Уолш-модуляция (ссылка)",
    {"соответствие": "MIL-STD-188-110C 5.3.4: робастный режим использует волновую форму 75 бит/с последовательного модема и требования STANAG 4415",
     "параметры": "см. запись «MIL-STD-188-110 последовательный (single-tone) 75–4800 бит/с» (свёрточный K=7 1/2, перемежитель, 75 бит/с — Уолш 4 → 32 символа 8PSK)"},
    "ВЧ связь НАТО в тяжёлых условиях (робастный канал)", [C.ist(s_4415, "5.3.1.3 f"), C.ist(s_4415b, "5.3.4"), JO.ist(s_jo, "4415 и 110A: 75 бит/с с расширением спектра")],
    "два источника: MIL-STD-188-110C и обзор Йоргенсона; сам STANAG 4415 закрыт", "см. запись MIL-STD-188-110 (75 бит/с)")
G = Tekst("istochniki/mil/MIL-STD-188_141C.035868.pdf")
s_4538 = G.odna(r"the protocols shown in shaded box in Figure C-2, as specified in STANAG 4538")[0]
s_lsu = G.odna(r"Third-generation ALE is termed Link Set-Up \(LSU\) in STANAG 4538")[0]
rec("STANAG 4538 (3G ALE: FLSU/RLSU, HDL/LDL) = MIL-STD-188-141 прил. C (BW0–BW5)", "НАТО: STANAG 4538", "каскадный (ссылка)",
    {"соответствие": "MIL-STD-188-141C прил. C: 3G-ALE (в 4538 — LSU: быстрый FLSU и робастный RLSU), TM, HDL, LDL, CLC — «как указано в STANAG 4538»",
     "параметры": "см. записи «ALE 3G BW0…BW5» и «ALE 3G CRC-4/8/12/16/32» (семейство MIL-STD-188-141)"},
    "ВЧ 3G ALE НАТО", [G.ist(s_4538, "C.4"), G.ist(s_lsu, "C.5.1")],
    "по MIL-STD-188-141C прил. C (открытый); RLSU — только в самом STANAG 4538 (закрыт)", "см. записи MIL-STD-188-141 ALE 3G")

# ---------------- DCS (ETSI TS 103 236) -------------------------------------------------------------------------------------------
D = Tekst("istochniki/pmr/ts_103236v010101p.pdf")
s_d = D.odna(r"codework consists of a 23 bit frame which is transmitted at 134,4 bit/s")[0]
s_st = D.odna(r"Bits 12 to 10 are fixed at 1002")[0]
s_gl = D.odna(r"by a \(23,12\) cyclic Golay code")[0]
s_lsb = D.odna(r"The LSB is transmitted first \(bit 1\)")[0]
kus, s_t2 = D.kusok(r"Table 2: DCS Codewords", r"=====СТР 11=====")
PAT = re.findall(r"(?m)^\s*([01]{23})\s*$", kus); KODY = re.findall(r"(?m)^\s*([0-7]{3})\s*$", kus)
assert len(PAT) == len(KODY) == 83
assert {int(k, 8) for k in KODY} == {int(p[-9:], 2) for p in PAT}                       # младшие 9 бит слова = восьмеричный код
assert all(p[11:14] == "100" for p in PAT)                                              # биты 12..10 = 100
# Строка таблицы — «MSB…LSB» = биты 23…1. Как многочлен c(x) = Σ b_i x^(i−1) все 83 слова делятся на g = 0xC75
G_DCS = 0xC75
assert all(ostatok(int(p, 2), G_DCS) == 0 for p in PAT)
assert not all(ostatok(int(p, 2), 0xAE3) == 0 for p in PAT)                              # обратный многочлен не подходит
assert ostatok((1 << 23) | 1, G_DCS) == 0                                               # g | x^23 + 1 — циклический код длины 23
def dcs(kod8):
    info = (0b100 << 9) | kod8                                                           # биты 12..1
    for chk in range(2048):                                                              # проверочные биты 23..13
        c = (chk << 12) | info
        if ostatok(c, G_DCS) == 0: return c
slova = {k: format(dcs(int(k, 8)), "023b") for k in KODY}
assert all(slova[k] == p for k, p in zip(sorted(KODY, key=lambda x: int(x, 8)), sorted(PAT, key=lambda p: int(p[-9:], 2))))
t_dcs = tablica("dcs_ts103236_kodovye_slova", {"g_x": "x11+x10+x6+x5+x4+x2+1 (0xC75), c(x)=Σ b_i·x^(i−1), бит 1 — младший, передаётся первым",
                "slova_MSB_LSB": {k: slova[k] for k in sorted(KODY, key=lambda x: int(x, 8))}},
                "DCS (TS 103 236 табл. 2): 83 кодовых слова, построенных кодером Голея по g = 0xC75 и совпавших с таблицей стандарта",
                [D.ist(s_t2, "табл. 2")], "каждое из 83 слов таблицы восстановлено кодером (assert)")
rec("DCS (цифровой кодовый шумоподавитель, CDCSS): Голей (23,12), g = x11+x10+x6+x5+x4+x2+1, 134,4 бит/с", "PMR: CTCSS/DCS (ETSI TS 103 236)", "Голей",
    {"n,k": "(23,12), циклический, d = 7", "слово": "биты 1–9 — код (3 восьмеричные цифры, бит 1 — младший), биты 10–12 = 001 (в записи «12 to 10» = 100), биты 13–23 — проверочные",
     "g(x)": "x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1 (0xC75) при c(x) = Σ b_i·x^(i−1) — выведено из таблицы стандарта (в тексте многочлен не указан)",
     "порядок бит": "младший (бит 1) первым, слово передаётся непрерывно по кругу", "скорость": "134,4 бит/с, NRZ под речью (субтональный)",
     "таблица": t_dcs, "кодов": "83 (группы 1–4 по длине серий)"},
    "аналоговые ЧМ радиостанции PMR/любительские (DCS/DPL, «цифровой шумоподавитель»)", [D.ist(s_d, "4.2.1"), D.ist(s_st, "биты 12–10"), D.ist(s_gl, "Голей (23,12)"), D.ist(s_lsb, "порядок"), D.ist(s_t2, "табл. 2")],
    "все 83 слова табл. 2 делятся на 0xC75 и восстановлены кодером (assert); тот же g = 0xC75 — в проекте ysf.py (Голей (24,12) MMDVMHost)",
    "частично: Голей по 0xC75 есть (ysf.py, kod.py распознаёт (23,12)); поиск 23-битного циклического слова DCS — добавить (просто)")

# ---------------- RTCM SC-104 (морские DGPS) ---------------------------------------------------------------------------------------
B = Tekst("istochniki/morskaya/RTCM_SC104_DGPS_Betke2001.pdf")
s_pa = B.odna(r"Bit 25 through 30 of each message word are parity bits")[0]
def uravneniya(txt):
    d = {}
    for m in re.finditer(r"D(2[5-9]|30)\s*=\s*D(29|30)'?\s*\*?\s*((?:d\d+\s*\*?\s*)+)", txt):
        d[int(m.group(1))] = (int(m.group(2)), sorted(int(x) for x in re.findall(r"d(\d+)", m.group(3))))
    return d
UB = uravneniya(B.ves)
assert set(UB) == set(range(25, 31)), UB
GPS = "/tmp/claude-0/-home-user-poiujnbhy/da6b639e-7ecd-5b07-9c36-bf7a3fd312ff/scratchpad/katalog/kosmos/istochniki/nav/IS-GPS-200N.pdf.txt"
sverka_gps = ""
if os.path.exists(GPS):
    tg = re.sub(r"\s+", " ", re.sub(r"[^\x00-\x7f]", " ", open(GPS, errors="replace").read()))   # знаки ⊕ и штрихи — символы частной области шрифта
    UG = {}
    for m in re.finditer(r"D(2[5-9]|30) = D(29|30) ((?:d\d+ )+)", tg.replace("  ", " ")):
        UG.setdefault(int(m.group(1)), (int(m.group(2)), sorted(int(x) for x in re.findall(r"d(\d+)", m.group(3)))))
    if set(UG) == set(range(25, 31)):
        assert UG == UB, (UG, UB); sverka_gps = "совпадает с IS-GPS-200N табл. 20-XIV (katalog/kosmos/istochniki/nav/IS-GPS-200N.pdf, navcen.uscg.gov) — assert; "
# Свойства кода: 6 проверочных на 24 бита (без учёта D29'/D30'): столбцы H различны и ненулевые → d ≥ 3; нет столбца = сумме двух → d ≥ 4
H = []
for i in range(1, 25):
    H.append(sum(1 << (p - 25) for p in range(25, 31) if i in UB[p][1]))
H += [1 << k for k in range(6)]
assert len(set(H)) == 30 and 0 not in H
sH = set(H); d4 = all((H[a] ^ H[b]) not in sH for a in range(30) for b in range(a + 1, 30))
s_w = B.odna(r"30 bits and employs the same parity algorithm")[0]
rec("RTCM SC-104 (морские DGPS-маяки 283,5–325 кГц): слова 30 бит с чётностью GPS (30,24) + инверсия по D30*", "Морская радионавигация: DGPS (RTCM SC-104 v2)", "Хэмминг (укороченный, чётность GPS)",
    {"n,k": "(30,24): 24 бита данных + 6 проверочных D25–D30, участвуют D29*/D30* предыдущего слова", "уравнения": {f"D{k}": f"D{v[0]}* ⊕ " + " ⊕ ".join(f"d{x}" for x in v[1]) for k, v in sorted(UB.items())},
     "инверсия": "D1–D24 = d_i ⊕ D30* (данные инвертируются, если последний бит предыдущего слова = 1)",
     "d_min": "≥ 4" if d4 else "≥ 3", "сообщение": "N + 2 слов: заголовок (преамбула 01100110, тип, ИД станции; Z-счёт, номер, длина, здоровье) + данные",
     "модуляция": "MSK 25/50/100/200 бит/с"},
    "морские дифференциальные GPS-маяки (МАРС/IALA), речные и прибрежные станции", [B.ist(s_pa, "3.3 чётность"), B.ist(s_w, "формат слова")],
    sverka_gps + ("столбцы H различны, ненулевые и ни один не равен сумме двух → d ≥ 4 (assert)" if d4 else "столбцы H различны → d ≥ 3 (assert)"),
    "частично: чётность GPS описана в области kosmos (IS-GPS-200N); синхронизация по преамбуле 0x66 и проверка чётности — внедрить (просто)")

# ---------------- UTRA TDD (3.84/1.28 Мчип/с = TD-SCDMA, 7.68) — 3GPP TS 25.222 ------------------------------------------------------
import pymupdf, json as _json
def g_vektor(pdf, stranica):
    """Вектор g = {…} формулы (набран символами в формуле — собираем строку по координатам знаков)."""
    p = pymupdf.open(os.path.join(KOREN, pdf))[stranica - 1]; ch = []
    for b in p.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            for sp in l["spans"]:
                for c in sp["chars"]: ch.append((round(c["bbox"][1]), c["bbox"][0], c["c"]))
    for y in sorted(set(c[0] for c in ch if c[2] == "{")):
        row = "".join(c[2] for c in sorted([c for c in ch if abs(c[0] - y) <= 5], key=lambda c: c[1])).replace(" ", "")
        m = re.search(r"=\{([01](?:,[01])+)\}", row)
        if m: return [int(x) for x in m.group(1).split(",")]
TD = Tekst("istochniki/umts/ts_125222v190000p.pdf"); FD = Tekst("istochniki/umts/ts_125212v190000p.pdf")
s_bs = TD.odna(r"^4\.2\.9\s+Bit Scrambling")[0] if TD.naiti(r"^4\.2\.9\s+Bit Scrambling") else TD.naiti(r"Bit Scrambling")[-1][0]
s_bs = [s for s, _, l in TD.naiti(r"The bits output from the TrCH multiplexer are scrambled in the bit scrambler")][0]
s_hs = [s for s, _, l in FD.naiti(r"The bits output from the HS-DSCH CRC attachment are scrambled in the bit scrambler")][0]
G_TDD = g_vektor("istochniki/umts/ts_125222v190000p.pdf", s_bs); G_HS = g_vektor("istochniki/umts/ts_125212v190000p.pdf", s_hs)
assert G_TDD == G_HS == [0] * 10 + [1, 0, 1, 1, 0, 1], (G_TDD, G_HS)
otv = [i + 1 for i, v in enumerate(G_TDD) if v]                                       # p_k = ⊕ p_(k−i), i ∈ {11,13,14,16}
def ps(n):
    p = [0] * 16 + [1]; out = [1]                                                     # p_1 = 1, p_k = 0 при k < 1
    for _ in range(n - 1):
        v = 0
        for i in otv: v ^= p[-i]
        p.append(v); out.append(v)
    return out
seq = ps(65535 + 64)
per = min(T for T in (65535, 21845, 13107, 4369, 3855, 1285, 771, 257, 255, 85, 51, 17, 15, 5, 3, 1) if seq[:64] == seq[T:T + 64])
assert per == 65535                                                                    # период 2^16 − 1, ни один делитель не период → примитивный
s_cc = TD.odna(r"Convolutional codes with constraint length 9 and coding rates 1/3 and 1/2 are defined")[0]
g8 = {k: TD.odna(rf"^{k} = (\d+) \(octal\)")[2] for k in ("G0", "G1", "G2")}
oct_td = sorted(set(re.findall(r"\d{3}", " ".join(l for _, _, l in TD.naiti(r"^G[012] = \d+ \(octal\)")))))
assert oct_td == sorted(["557", "663", "711", "561", "753"]), oct_td
T_U = _json.load(open(os.path.join(KOREN, "tablicy/umts_25212.json")))["данные"]
kus, s_t7 = TD.kusok(r"Table 7 Inter-column permutation pattern for 2nd interleaving", r"4\.2\.11A")
P2 = [int(x) for x in re.search(r"<(\d[\d,\s]+)>", kus).group(1).replace(" ", "").replace("\n", "").split(",")]
assert P2 == T_U["vtoroi_peremezhitel_t7"] and len(P2) == 30                               # = 25.212 табл. 7 (FDD)
s_tf = TD.odna(r"The TFCI is encoded using a \(32, 10\) sub-code of the second order Reed-Muller code")[0]
s_16 = TD.odna(r"is encoded using a \(16, 5\) bi-orthogonal")[0]
s_48 = TD.odna(r"The code words of the punctured \(48,10\) sub-code of the second order Reed-Muller codes")[0]
s_sf = TD.odna(r"it is needed to add a sub-frame segmentation unit between 2nd interleaving unit")[0]
rec("UTRA TDD / TD-SCDMA (3GPP TS 25.222): свёрточные K=9, турбо 1/3 как FDD + бит-скремблер 1+D11+D13+D14+D16 + 2-й перемежитель 30 столбцов (кадр/слот), TFCI (32,10)/(16,5)/(48,10)",
    "UMTS/WCDMA", "каскадный: CRC + свёрточный/турбо + согласование скорости + перемежение + скремблер",
    {"свёрточный": "K = 9: 1/2 — 561, 753₈; 1/3 — 557, 663, 711₈ (как 25.212)", "турбо": "PCCC 1/3, 8 состояний, перемежитель на простых числах — как 25.212 (см. запись «UMTS турбокод PCCC»)",
     "бит-скремблер": "s_k = h_k ⊕ p_k, p_k = (Σ g_i·p_(k−i)) mod 2, g = {0,0,0,0,0,0,0,0,0,0,1,0,1,1,0,1} (i = 1…16) → отводы 11, 13, 14, 16; p_1 = 1, p_k = 0 при k < 1; период 65535 (примитивный); тот же g — HS-DSCH (25.212 4.5.1A)",
     "2-й перемежитель": "30 столбцов, перестановка табл. 7 = FDD; на весь кадр или по каждому слоту (выбор верхнего уровня); 1,28 Мчип/с: сегментация на 2 подкадра по 5 мс",
     "TFCI": "(32,10) подкод РМ 2-го порядка (≥ 6 бит), (16,5) биортогональный (3–5 бит), повторение (1–2 бит); 1,28 Мчип/с 8PSK — выколотый (48,10)",
     "CRC": "24/16/12/8 как FDD"},
    "UMTS TDD (3,84/7,68 Мчип/с), TD-SCDMA (1,28 Мчип/с, Китай), HSDPA (скремблер HS-DSCH)",
    [TD.ist(s_bs, "4.2.9 бит-скремблер (g в формуле)"), FD.ist(s_hs, "25.212 4.5.1A HS-DSCH: тот же g"), TD.ist(s_cc, "4.2.3.1 K=9"), TD.ist(s_t7, "табл. 7"),
     TD.ist(s_tf, "4.3.1.1 (32,10)"), TD.ist(s_16, "(16,5)"), TD.ist(s_48, "4.4.2.1 (48,10)"), TD.ist(s_sf, "4.2.11A подкадры")],
    "g снят с формулы по координатам знаков в обоих документах и совпал (assert), снимки risunki/umts_tdd_skrembler_25222_4.2.9.png и umts_hsdsch_skrembler_25212_4.5.1A.png; "
    "период 65535 и примитивность — вычислением; многочлены K=9 и табл. 7 совпали с 25.212 (tablicy/umts_25212.json)",
    "частично: свёрточные K=9, турбо UMTS, CRC и (32,10) — как в записях UMTS FDD; бит-скремблер TDD/HS-DSCH (LFSR 16) — добавить (просто)")
ZAPISI[-1]["источник"] += [{"файл": "risunki/umts_tdd_skrembler_25222_4.2.9.png", "страница": "снимок", "url": ""},
                          {"файл": "risunki/umts_hsdsch_skrembler_25212_4.5.1a.png", "страница": "снимок", "url": ""}]

# ---------------- ARQ-M (ITU-R F.342-2): 7-элементный код ИТА-3 (3Z/4A) ---------------------------------------------------------------
Q = Tekst("istochniki/morskaya/ARQ-M_342_sigidwiki.pdf")
kus, s_q = Q.kusok(r"Table of conversion", r"=====СТР 3=====")
p5 = re.findall(r"(?m)^\s*([AZ]{5})\s*$", kus); p7 = re.findall(r"(?m)^\s*([AZ]{7})\s*$", kus)
assert len(p5) == 32 and len(p7) == 35, (len(p5), len(p7))
assert len(set(p5)) == 32 and len(set(p7)) == 35 and all(w.count("Z") == 3 for w in p7)   # все C(7,3) = 35 слов веса 3
ITA2 = {"A": "ZZAAA", "E": "ZAAAA", "T": "AAAAZ"}                                        # сверка порядка с ИТА-2 (Z — стартовая «работа» = 1)
assert p5[0] == ITA2["A"] and p5[4] == ITA2["E"] and p5[19] == ITA2["T"]
sim = [chr(65 + i) for i in range(26)] + ["ВК", "ПС", "Цифры", "Буквы", "Пробел", "Пустая лента"]
ita3 = {sim[i]: p7[i] for i in range(32)}; ita3.update({"Повтор сигнала (RQ)": p7[32], "α": p7[33], "β": p7[34]})
t_q = tablica("arqm_ita3_f342", {"ИТА2→ИТА3": {sim[i]: [p5[i], p7[i]] for i in range(32)}, "служебные": {"RQ": p7[32], "α": p7[33], "β": p7[34]}},
              "ITU-R F.342-2 прил. I табл. I: ИТА-2 (5 эл.) → ИТА-3 (7 эл., 3Z/4A)", [Q.ist(s_q, "табл. I")],
              "35 слов, все веса 3 и различны (= все C(7,3)); порядок ИТА-2 сверен по A, E, T")
s_rc = Q.odna(r"comprise one .signal repetition. and three stored characters")[0]
s_ch = Q.odna(r"one character inverted followed by three")[0]
s_il = Q.odna(r"Elements of Channel C are interleaved with those of Channel A")[0]
kus4, s_t4 = Q.kusok(r"TABLE\s+IV", r"=====СТР 5=====", posl=True)
assert re.findall(r"(?m)^(145 5/6|163 1/3|140|096|192|085 5/7|171 3/7|100|200)\s*$", kus4) == ["145 5/6", "096", "192", "163 1/3", "140", "085 5/7", "100", "171 3/7", "200"]
from fractions import Fraction as Fr
for cikl, b2, b4 in ((Fr(875, 6), 96, 192), (Fr(490, 3), Fr(600, 7), Fr(1200, 7)), (Fr(140), 100, 200)):
    assert cikl * b2 / 1000 == 14 and cikl * b4 / 1000 == 28                               # цикл = 2 (4) знака по 7 элементов
rec("ARQ-M (ITU-R F.342, TDM-342/ARQ-M2/M4): 7-элементный код ИТА-3 постоянного веса 3Z/4A + ARQ с циклом 4/8 знаков, 2/4 канала с поэлементным перемежением",
    "КВ телеграфия: ARQ-M (ITU-R F.342)", "код постоянного веса + ARQ",
    {"код": "7 элементов, ровно 3 Z (работа) и 4 A (пауза) — 35 комбинаций: 32 знака ИТА-2 + повтор сигнала (RQ), α, β; таблица " + t_q,
     "обнаружение": "нарушение отношения 3Z/4A → запрос повтора (RQ); повтор цикла 4 знаков (RQ + 3 хранимых) или 8",
     "каналы": "A/B (знаки последовательно), C/D поэлементно перемежаются с A/B; в цикле 1 знак инвертирован + 3 прямых (канал A), 1 прямой + 3 инвертированных (канал B)",
     "подканалы": "4 подканала по 1/4 скорости знаков; подканал 1 — с обратной полярностью", "скорость": "табл. IV: цикл 145 5/6 мс — 96/192 Бод (2/4 канала, предпочтительный, стык с сетью 50 Бод), 163 1/3 мс — 85 5/7/171 3/7 Бод, 140 мс — 100/200 Бод"},
    "фиксированная КВ телеграфия (посольские, метео, морские береговые каналы), TDM-342", [Q.ist(s_q, "табл. I"), Q.ist(s_rc, "2.1 цикл"), Q.ist(s_ch, "3.1.1"), Q.ist(s_il, "3.5.2"), Q.ist(s_t4, "табл. IV скорости")],
    "35 кодовых слов таблицы: все разные и веса 3 (исчерпывают C(7,3)) — assert; порядок ИТА-2 сверен по A/E/T; табл. IV: цикл × скорость = 14/28 элементов (2/4 знака по 7) для всех трёх циклов — assert", "нет в проекте; просто — как SITOR 4B/3Y (navtex.py) с другой таблицей")

# ---------------- TETRA DMO (EN 300 396-2) ----------------------------------------------------------------------------------------
DM = Tekst("istochniki/tetra/en_30039602v010401p.pdf")
TT = _json.load(open(os.path.join(KOREN, "tablicy/tetra_392-2_395-2.json")))["данные"]
kus, s_ci = DM.kusok(r"with ci = 1 for i =", r"and ci = 0 elsewhere", posl=True)
ci = [int(x) for x in re.findall(r"\d+", kus)]
assert ci == TT["skrembler_ci"], ci                                                      # тот же многочлен, что V+D (EN 300 392-2)
s_dm0 = DM.stranica_pozicii(re.search(r"For the scrambling of\s+SCH/S and SCH/H of the DSB, all bits e\(1\), e\(2\),\.\., e\(30\) shall be set equal to zero", DM.ves).start())
s_dmc = DM.stranica_pozicii(re.search(r"shall be generated from the 30 bits of the DM\s+colour code", DM.ves).start())
bk = [(int(a), int(b)) for a, b in re.findall(r"A \((\d+),\s?(\d+)\) block code shall encode", DM.ves)]
il = [(int(a), int(b)) for a, b in re.findall(r"A \((\d+),\s?(\d+)\) block interleav", DM.ves)]
assert bk[:3] == [(76, 60), (140, 124), (284, 268)] and il[:3] == [(120, 11), (216, 101), (432, 103)], (bk, il)
assert all(n - k == 16 for n, k in bk[:3])                                               # CRC-16 (K1+16, K1) как V+D
for K, a in il[:3]:                                                                       # (K,a)-перемежитель: i = 1 + (a·k mod K) — перестановка
    assert sorted(1 + (a * k) % K for k in range(1, K + 1)) == list(range(1, K + 1))
s_sch = DM.odna(r"One type-1 block shall contain 60 type-1 bits")[0]; s_rc = DM.odna(r"encoding by a 16-state mother code of rate 1/4")[0]
s_dsb = DM.stranica_pozicii(re.search(r"38 bits\s+synchronize\s+training seq", DM.ves).start())
rec("TETRA DMO (прямой режим, EN 300 396-2): коды V+D (RCPC 16 состояний, CRC-16, (K,a)-перемежение) + скремблер от 30-битного цветового кода DM",
    "TETRA", "каскадный: CRC-16 + RCPC + блочный перемежитель + скремблер",
    {"SCH/S (DSB)": "60 → (76,60) + 4 хвост → RCPC 2/3 → 120 → перемежение (120,11) → скремблер с нулевым кодом",
     "SCH/H, STCH": "124 → (140,124) + 4 → RCPC 2/3 → 216 → (216,101)", "SCH/F": "268 → (284,268) + 4 → RCPC 2/3 → 432 → (432,103)",
     "скремблер": "c(x) = 1+x+x2+x4+x5+x7+x8+x10+x11+x12+x16+x22+x23+x26+x32 (как V+D), начальное p(k) = e(1−k) — 30 бит цветового кода DM, p(−31) = p(−30) = 1; для SCH/S и SCH/H в DSB e = 0",
     "пакеты": "DNB: 216 + 22 (обучающая) + 216; DSB: 80 частотной коррекции… 120 (блок 1) + 38 синхро + 216 (блок 2)", "речь": "EN 300 395-2 (как V+D)"},
    "TETRA прямой режим (радиостанция — радиостанция, ретрансляторы DM-REP, шлюзы)",
    [DM.ist(s_rc, "8.2.3.1 RCPC"), DM.ist(s_ci, "8.2.5.2 c(x)"), DM.ist(s_dmc, "цветовой код DM"), DM.ist(s_dm0, "DSB — нули"), DM.ist(s_sch, "8.3.1.1 SCH/S"), DM.ist(s_dsb, "рис. 12 пакеты")],
    "c_i совпали с таблицей V+D (tablicy/tetra_392-2_395-2.json) — assert; размеры блоков (76,60)/(140,124)/(284,268) = K1+16; (K,a) = (120,11)/(216,101)/(432,103) дают перестановки — assert",
    "есть: TETRA RCPC/CRC-16/(K,a)/скремблер в проекте (записи TETRA); DMO — другой источник цветового кода и нули для DSB (просто)")

# ---------------- Motorola SmartNet/SmartZone (Type II) — канал управления OSW ------------------------------------------------------------
k_op = kod("op25", "op25/gr-op25_repeater/lib/rx_smartnet.cc"); k_oph = kod("op25", "op25/gr-op25_repeater/lib/rx_smartnet.h")
k_ops = kod("op25", "op25/gr-op25_repeater/lib/frame_sync_magics.h")
k_gs = kod("gr-smartnet", "src/lib/smartnet_crc.cc"); k_gd = kod("gr-smartnet", "src/lib/smartnet_deinterleave.cc"); k_gp = kod("gr-smartnet", "src/lib/smartnet_parity.cc")
k_in = kod("indri", "lib/indri/smartnet/deframer.py", licenziya="README")
t_op, t_gs, t_in = (open(os.path.join(KOREN, p), errors="replace").read() for p in (k_op, k_gs, k_in))
for t_ in (t_op, t_gs, t_in):                                                     # три реализации: одинаковые константы CRC
    assert re.search(r"crcaccum\s*=\s*0x0393", t_, re.I) and re.search(r"crcop\s*=\s*0x036e", t_, re.I) and re.search(r"0x0225", t_, re.I)
assert "0x33C7" in open(os.path.join(KOREN, k_oph)).read() and "0x33C7" in t_gs and "0x33C7" in t_in
assert re.search(r"SMARTNET_SYNC_MAGIC\s*=\s*0xACLL", open(os.path.join(KOREN, k_ops)).read())
def sn_crc(d27):
    acc, op = 0x393, 0x36E
    for b in d27:
        op = (op >> 1) ^ 0x225 if op & 1 else op >> 1
        if b: acc ^= op
    return acc
st, seen = 0x36E, set()
while st not in seen:
    seen.add(st); st = (st >> 1) ^ 0x225 if st & 1 else st >> 1
per_sn = len(seen)
import random as _r
_r.seed(3)
for _ in range(100):                                                                 # аффинность: crc(a⊕b) = crc(a)⊕crc(b)⊕crc(0)
    a = [_r.randint(0, 1) for _ in range(27)]; b = [_r.randint(0, 1) for _ in range(27)]
    assert sn_crc([x ^ y for x, y in zip(a, b)]) == sn_crc(a) ^ sn_crc(b) ^ sn_crc([0] * 27)
def sn_kod(inf):                                                                      # свёрточный 1/2: (i_k, i_k ⊕ i_(k−1)) — из формулы синдрома
    out = []; pr = 0
    for b in inf: out += [b, b ^ pr]; pr = b
    return out
def sn_ispr(raw):                                                                     # перенос error_correction() op25/gr-smartnet
    exp = [0] * 76; exp[0] = raw[0]; exp[1] = raw[0]
    for k in range(2, 76, 2): exp[k] = raw[k]; exp[k + 1] = raw[k] ^ raw[k - 2]
    syn = [e ^ r for e, r in zip(exp, raw)]
    return [(1 - raw[2 * k]) if (syn[2 * k + 1] and syn[2 * k + 3]) else raw[2 * k] for k in range(37)]
for _ in range(200):
    inf = [_r.randint(0, 1) for _ in range(38)]; c = sn_kod(inf)
    assert sn_ispr(c) == inf[:37]
    j = _r.randrange(0, 36); c2 = c[:]; c2[2 * j] ^= 1                                 # одиночная ошибка в бите данных исправляется
    assert sn_ispr(c2) == inf[:37]
perm = [k + l * 19 for k in range(19) for l in range(4)]                             # d_raw[k*4+l] = buf[k + l*19]
assert sorted(perm) == list(range(76))
rec("Motorola SmartNet/SmartZone (Type II): OSW 84 бита = синхро 0xAC + 76: перемежение 19×4, свёрточный 1/2 (i, i⊕i₋₁), CRC-10 (0x393/0x36E/0x225), маски 0x33C7/0x32A",
    "Транкинг: Motorola SmartNet/SmartZone", "каскадный: CRC + свёрточный 1/2 + блочный перемежитель",
    {"кадр": "3600 бит/с, 84 бита: синхро 8 бит 10101100 (0xAC) + 76 бит полезной части", "перемежение": "76 бит, запись по 4 столбцам длиной 19: d[4k+l] = r[k+19l]",
     "свёрточный": "1/2, пары (i_k, i_k ⊕ i_(k−1)); декодирование по синдрому: две соседние ошибки проверки → инвертировать бит данных",
     "поля": "38 бит: 16 адрес (ИД, XOR 0x33C7) + 1 группа + 10 команда (XOR 0x32A) + 10 CRC (+1); данные в буфере инверсны",
     "CRC": f"аффинный: acc = 0x393; для каждого из 27 бит op ← (op>>1) ⊕ (0x225 при op&1), нач. op = 0x36E; при бите 1 acc ^= op; сравнение с инверсией 10 принятых; период op = {per_sn}",
     "стандарт": "не опубликован Motorola; значения — по открытым реализациям (обратная разработка)"},
    "транковые сети Motorola SmartNet II / SmartZone (аналоговые и с речью P25/ASTRO)",
    [ist_kod(k_op, r"rx_smartnet::deinterleave", "op25: перемежение"), ist_kod(k_op, r"rx_smartnet::error_correction", "op25: синдром"), ist_kod(k_op, r"crcaccum = 0x0393", "op25: CRC"),
     ist_kod(k_oph, r"SMARTNET_ID_XOR", "маски"), ist_kod(k_ops, r"SMARTNET_SYNC_MAGIC", "синхро 0xAC"), ist_kod(k_gs, r"crcaccum = 0x0393", "gr-smartnet: CRC"), ist_kod(k_in, r"crcaccum = 0x0393", "indri: CRC (Python)")],
    f"константы CRC и маски совпали в трёх открытых реализациях (op25, gr-smartnet, indri) — assert; CRC аффинен (100 проб); декодер синдрома исправляет одиночную ошибку в данных (200 проб); "
    f"перестановка 19×4 — биекция; период регистра op = {per_sn}. Официальной спецификации нет",
    "нет в проекте; внедрение простое (84 бита, синхро 0xAC)")
