"""Пейджинг: POCSAG (ITU-R M.584 RPC 1) — БЧХ(31,21)+чётность; FLEX — тот же код + перемежение 32×8 (по диссертации, протокол
закрыт); ERMES (ETS 300 133-4) — укороченный циклический (30,18) = БЧХ(31,21)·(x+1)², синхрослово; GSC Голей (Motorola) — (23,12)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
PR = "/home/user/poiujnbhy/src/reportgen/potok/"
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

P = Tekst("istochniki/peydzhing/R-REC-M.584-2.pdf")
s_g = P.odna(r"generating polynomial x10 \+ x9 \+ x8 \+ x6 \+ x5 \+ x3 \+ 1")[0]
G31 = sum(1 << s for s in (10, 9, 8, 6, 5, 3, 0)); assert G31 == 0o3551
s_par = P.odna(r"To the 31 bits of the block is added one additional bit to provide an even bit parity")[0]
s_bat = P.odna(r"Codewords are structured in batches which comprise a synchronization codeword followed by 8 frames")[0]
s_adr = P.odna(r"Bits 2-19 are address bits")[0]
poc = open(PR + "pocsag.py").read()
SIN = int(re.search(r"СИНХРО = (0x[0-9A-F]+)", poc).group(1), 16); IDL = int(re.search(r"ПРОСТОЙ = (0x[0-9A-F]+)", poc).group(1), 16)
for w in (SIN, IDL):
    assert ostatok(w >> 1, 31, G31) == 0 and bin(w).count("1") % 2 == 0          # синхро и простой проекта — кодовые слова БЧХ(31,21) с чётностью
# минимальное расстояние кода (31,21): по всем 2^21 словам долго — проверим, что g делит x^31+1 (циклический)
assert ostatok((1 << 31) | 1, 32, G31) == 0
rec("POCSAG (RPC 1): БЧХ(31,21) + бит чётности = (32,21), пакеты по 8 кадров", "Пейджинг: POCSAG (ITU-R M.584)", "БЧХ",
    {"g(x)": "x10+x9+x8+x6+x5+x3+1 (3551 восьм.)", "слово": "32 бита: флаг (0 — адрес, 1 — сообщение), 20 бит информации (адрес 18 + функция 2), 10 проверочных, чётность (чётная)",
     "пакет": "синхрослово 0x7CD215D8 + 8 кадров × 2 слова = 544 бита; простой 0x7A89C197; преамбула ≥ 576 бит 1010…",
     "исправление": "d = 6 (с чётностью): 2 ошибки/обнаружение 3+", "модуляция": "FSK ±4,5 кГц, 512/1200/2400 бит/с, 1 = нижняя частота", "порядок": "старший первым"},
    "пейджинговые сети, служебная связь, телеметрия", [P.ist(s_g, "1.4 g(x)"), P.ist(s_par, "чётность"), P.ist(s_bat, "пакет"), P.ist(s_adr, "адрес")],
    "g(x) из текста M.584 = 3551₈; делит x^31+1; синхро 0x7CD215D8 и простой 0x7A89C197 проекта (pocsag.py) — кодовые слова этого кода с чётной чётностью",
    "ЕСТЬ в проекте: pocsag.py")
F = Tekst("istochniki/peydzhing/FLEX_Thesis_sigidwiki.pdf")
s_fg = F.odna(r"^g\(x\)=x10\+x9\+x8\+x6\+x5\+x3\+1")[0]; s_fi = F.odna(r"performed such that the first data bit in all the codewords are transmitted first")[0]
s_fs = F.odna(r"Sync1 contains two 32-bit synchronization words that are separated by 16 dotting")[0]; s_fb = F.odna(r"Each block contains 8, 16, or 32 codewords of data for 1600 bps")[0]
assert sum(1 << s for s in (10, 9, 8, 6, 5, 3, 0)) == G31
rec("FLEX (Motorola): БЧХ(31,21)+чётность, блочное перемежение 8×32 бит, кадр 11 блоков", "Пейджинг: FLEX", "каскадный: БЧХ + блочный перемежитель",
    {"слово": "32 бита как в POCSAG (g = x10+x9+x8+x6+x5+x3+1, чётная чётность), 21 бит информации — младшим первым в FLEX",
     "перемежение": "блок 8 слов (1600 бит/с) / 16 / 32: передаются первые биты всех слов, затем вторые … (матрица 8×32 по столбцам); 4-FSK: пара бит из одного столбца соседних слов",
     "кадр": "Sync1 (2 × 32-бит синхрослова + 16 бит точек) + Frame Info, всё 1600 бит/с 2-FSK; Sync2 25 мс; 11 блоков по 160 мс = 1,875 с; 128 кадров в цикле 4 мин",
     "модуляция": "2-FSK/4-FSK 1600/3200 Бод (1600/3200/6400 бит/с)"},
    "пейджинг FLEX (США, Азия)", [F.ist(s_fg, "2.3.3 g(x)"), F.ist(s_fi, "2.3.2 перемежение"), F.ist(s_fs, "Sync1"), F.ist(s_fb, "блоки")],
    "g(x) совпадает с M.584 (POCSAG); спецификация FLEX (Motorola) закрыта — синхрослова Sync1 и точная раскладка Frame Info в открытом источнике не приведены (есть в открытом коде multimon-ng, не скачан)",
    "нет в проекте; БЧХ как в POCSAG есть (pocsag.py) — нужны перемежение и кадр (средне)")
E = Tekst("istochniki/peydzhing/ets_30013304e02p.pdf")
s_eg = E.odna(r"g\(x\) = x12 \+ x11 \+ x9 \+ x7 \+ x6 \+ x3 \+ x2 \+ 1 = 15315")[0]
G30 = sum(1 << s for s in (12, 11, 9, 7, 6, 3, 2, 0))
assert G30 == 0o15315 and pmul(pmul(G31, 0b11), 0b11) == G30                         # g = g(31,21)·(x+1)(x+1)
s_ex = E.odna(r"c\(x\) = 000 000 000 000 000 001 101 011 001 101 = g\(x\)")[0]
c = (1 << 12) | ostatok(1 << 12, 30, G30); assert c == int("000000000000000001101011001101", 2)   # пример: m = 1 → c = g
s_sw = E.odna(r"^10 00 10 10 00 10 00 00 10 10 00 00 10 10 10 \(30 bits\)")[0]; s_pr = E.odna(r"^00 10 00 10 00 10 00 10 00 10 00 10 00 10 00 \(30 bits\)")[0]
s_ei = E.odna(r"codeword interleaving to a depth of nine")[0]; s_em = E.odna(r"four level Pulse Amplitude Modulated Frequency Modulation")[0]
SW = int("100010100010000010100000101010", 2)
rec("ERMES: укороченный циклический (30,18), d = 6, перемежение глубиной 9, синхрослово SW1", "Пейджинг: ERMES (ETS 300 133-4)", "БЧХ (укороченный циклический)",
    {"g(x)": "x12+x11+x9+x7+x6+x3+x2+1 = 15315₈ = g_БЧХ(31,21)·(x+1)²", "слово": "18 информационных + 12 проверочных; c(x) = m(x)x^12 + (m(x)x^12 mod g(x))",
     "перемежение": "кодовые слова сообщений — блоками по 9 (глубина 9)", "синхро": "преамбула 001000100010…(30 бит), SW1 = 10 00 10 10 00 10 00 00 10 10 00 00 10 10 10",
     "модуляция": "4-PAM/FM 3125 Бод (6250 бит/с), пары бит — дибиты"},
    "общеевропейский пейджинг ERMES", [E.ist(s_eg, "g(x)"), E.ist(s_ex, "пример c(x)"), E.ist(s_sw, "SW1"), E.ist(s_pr, "преамбула"), E.ist(s_ei, "перемежение"), E.ist(s_em, "модуляция")],
    "g(x) = 15315₈ и равен произведению g(31,21)·(x+1)² (оба из текстов); пример стандарта c(x) для m = 1 воспроизведён", "нет в проекте; простой систематический циклический код — внедрение простое")
G = Tekst("istochniki/peydzhing/Guide_to_Golay_GSC.pdf", ocr=True)
s_gs = G.odna(r"sequential binary \(23.12 Golay\) words")[0]; s_gw = G.odna(r"23 binary bits where each bit")[0]; s_gp = G.odna(r"PREAMBLE — A preamble consists of a repeated GSC")[0]
rec("GSC (Golay Sequential Code, Motorola): два слова Голея (23,12) + преамбула и стартовый код", "Пейджинг: GSC (Motorola)", "Голей",
    {"слово": "23 бита (код Голея (23,12)); адрес = слово 1 + слово 2 (и их инверсии — 4 адреса пейджера)", "преамбула": "повторяемое слово GSC (10 вариантов для экономии батареи), инверсная преамбула + стартовый код — пакетный режим до 16 адресов",
     "скорость": "300 бит/с адреса, 600 бит/с данные"},
    "пейджеры Motorola (устар.)", [G.ist(s_gs, "23,12 Golay"), G.ist(s_gw, "23 бита"), G.ist(s_gp, "преамбула")],
    "только по руководству Motorola (OCR); многочлен Голея в документе не приведён — стандартный (23,12) есть в проекте (dmr/p25 Голей 24,12)", "частично: Голей (23,12)/(24,12) в проекте; кадра GSC нет")
