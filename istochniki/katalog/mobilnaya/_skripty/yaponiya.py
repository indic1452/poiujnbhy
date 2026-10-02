"""Япония: PDC (ARIB RCR STD-27 L) — укороченные БЧХ(15,11), БЧХ(8,4), CRC-16/8/7, свёрточный K=6 9/17 речи VSELP, скремблер PN(9,5);
PHS (ARIB RCR STD-28 v5.3) — CRC-16/CRC-12, уникальные слова, скремблер PN(10,3)."""
import re, sys, os
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
P1 = Tekst("istochniki/yaponiya/ARIB_RCR_STD-27L_1of3.pdf"); P2 = Tekst("istochniki/yaponiya/ARIB_RCR_STD-27L_2of3.pdf")
s_bch = P1.odna(r"3-bit shortened BCH \(12, 8\) of BCH \(15, 11\)")[0]
s_g4 = P1.odna(r"^1 \+ X \+ X4$")[0]; s_c16 = P1.odna(r"^1 \+ X5 \+ X12 \+ X16$")[0]; s_il = P1.odna(r"The depth of Interleaving shall be the number of BCH \(12, 8\) blocks")[0]
assert ostatok((1 << 15) | 1, 16, 0b10011) == 0                                      # 1+X+X4 делит x^15+1 (Хэмминг (15,11))
s_t = P1.odna(r"Table 4.1.5.1-1 : Relationship between information bits and interleaving")[0]
kus, _ = P1.kusok(r"Table 4.1.5.1-1 : Relationship between information bits and interleaving", r"\(4\)|Fig\. 4\.1\.5\.1-2|=====СТР")
ch = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]
assert ch[:2] == [136, 16] and ch[2:4] == [104, 13], ch                            # 136 бит → 16 блоков (14,10)? 136+16=152 → 16×… проверка ниже
rec("PDC каналы управления (BCCH/PCH/SCCH): укороченный БЧХ(15,11) → (14,10)/(12,8) + CRC-16 + перемежение в слоте", "Япония: PDC (ARIB RCR STD-27)", "каскадный: БЧХ + CRC + блочный перемежитель",
    {"БЧХ": "g = 1 + X + X4 (Хэмминг (15,11)), укорочение нулями перед информацией: (14,10) — вниз и 1-й блок вверх, (12,8) — остальные блоки вверх; исправляет 1",
     "CRC": "16 бит, 1 + X5 + X12 + X16", "перемежение": "в пределах слота, глубина = число блоков БЧХ (вниз: 136 бит → 16; вверх 1-й: 104 → 13 — табл. 4.1.5.1-1)",
     "синхро": "20-бит (суперкадр, 12 видов) и 32-бит слова, вверх — инверсия вниз (рис. 4.1.4.3-7)", "модуляция": "π/4-DQPSK 42 кбит/с, TDMA 3 слота (полноскоростной)"},
    "сотовая PDC (NTT DoCoMo, до 2012)", [P1.ist(s_bch, "4.1.5.1 (1)"), P1.ist(s_g4, "g(X)"), P1.ist(s_c16, "CRC-16"), P1.ist(s_il, "перемежение"), P1.ist(s_t, "табл. 4.1.5.1-1")],
    "1+X+X4 делит x^15+1 (циклический (15,11)); числа табл. 4.1.5.1-1 разобраны из текста", "нет в проекте; Хэмминг/CRC общего вида есть — нужен кадр PDC (средне)")
s_f = P1.odna(r"1-bit extended BCH \(8,4\) for BCH \(7,4\)")[0]; s_g3 = P1.odna(r"^1 \+ X \+ X3$")[0]
assert ostatok((1 << 7) | 1, 8, 0b1011) == 0
s_c8 = P1.odna(r"^1 \+ X \+ X3 \+ X4 \+ X7 \+ X8$")[0]
rec("PDC FACCH: расширенный БЧХ(8,4) из (7,4) + CRC-16; RCH: БЧХ(14,10) + CRC-8 с перемежением на 2 слота", "Япония: PDC (ARIB RCR STD-27)", "Хэмминг",
    {"FACCH": "(8,4): g = 1 + X + X3 + общая чётность", "RCH": "CRC-8 = 1 + X + X3 + X4 + X7 + X8, перемежение на 2 слота", "CRC FACCH": "1 + X5 + X12 + X16"},
    "PDC", [P1.ist(s_f, "4.1.5.2.2 FACCH"), P1.ist(s_g3, "g(X)"), P1.ist(s_c8, "CRC-8 RCH")], "1+X+X3 делит x^7+1", "Хэмминг (8,4) есть в проекте (dmr/общие) — просто")
s_rate = P2.odna(r"The forward error correction \(FEC\) is a rate 9/17 convolutional code with memory length 5")[0]
s_g = P2.odna(r"The polynomials are defined as\s+g0 = 65 octal and g1= 57 octal")[0]; s_crc7 = P2.odna(r"gcrc\(X\) = 1 \+ X4 \+ X5 \+X6 \+ X7")[0]
s_pun = P2.odna(r"punctures\s+of the output array cc1\(i\) occur at i=8,17,26,35,44,53,62,71,80")[0]
g0 = sum(1 << s for s in (0, 1, 3, 5)); g1 = sum(1 << s for s in (0, 2, 3, 4, 5))
assert (oct(int(bin(g0)[2:].zfill(6)[::-1], 2)), oct(int(bin(g1)[2:].zfill(6)[::-1], 2))) == ("0o65", "0o57")   # D^0 — старший разряд восьмеричной записи
VYK = [8 + 9 * k for k in range(9)]; assert VYK == [8, 17, 26, 35, 44, 53, 62, 71, 80] and 2 * 87 - 9 == 165
rec("PDC речь полноскоростная (VSELP 6,7 кбит/с): свёрточный K=6 (65, 57) с выкалыванием до 9/17 + CRC-7", "Япония: PDC (ARIB RCR STD-27)", "свёрточный с выкалыванием + CRC",
    {"свёрточный": "m = 5 (K = 6), g0 = 1+D+D3+D5 (65₈), g1 = 1+D2+D3+D4+D5 (57₈), выход g0, g1 поочерёдно, начальное состояние 0", "вход": "87 бит = 75 бит класса 1 + 7 CRC + 5 хвостовых",
     "выкалывание": "каждый 18-й выходной бит (только из g1): cc1[i], i = 8,17,26,35,44,53,62,71,80 → 165 бит", "CRC": "7 бит gcrc = 1 + X4 + X5 + X6 + X7 по важнейшим битам", "класс 2": "134 − 75 = 59 бит без защиты"},
    "PDC TCH (речь)", [P2.ist(s_rate, "5.1.2 FEC"), P2.ist(s_g, "g0/g1"), P2.ist(s_crc7, "CRC-7"), P2.ist(s_pun, "выкалывание")],
    "восьмеричная запись 65/57 воспроизведена из степеней D (D^0 — старший разряд); позиции выкалывания — арифметическая прогрессия шага 9, 174−9 = 165", "свёрточные K≤9 с выкалыванием есть (kod.py, vykalyvanie.py) — нужна раскладка классов (средне)")
s_pn = P1.odna(r"The scrambling pattern is the output of the PN\(9,5\) structure")[0]
rec("PDC скремблер PN(9,5): x9 + x5 + 1, начальное значение = номер образца (цветовой код)", "Япония: PDC (ARIB RCR STD-27)", "скремблер",
    {"ПСП": "PN(9,5) (регистр 9, отвод 5), сброс в начале каждого слота", "область": "все физические каналы, кроме SW и CC; синхропачка не скремблируется"},
    "PDC", [P1.ist(s_pn, "4.1.7 (2)")], "по тексту 4.1.7", "есть: skrembler.py (аддитивные любого вида) — просто")
H = Tekst("istochniki/yaponiya/ARIB_RCR_STD-28v5.3_1of2.pdf")
s_c = H.odna(r"Generator polynomial:\s+1 \+ X5 \+ X12 \+ X16")[0]; s_c12 = H.odna(r"Generator polynomial: 1 \+ X \+ X2 \+ X3 \+ X11 \+ X12")[0]
s_uw = H.odna(r"^0110 1011 1000 1001 1001 1010 1111 0000 32-bit pattern")[0]; s_uwd = H.odna(r"^0011 1101 0100 1100 16-bit pattern")[0]
s_pn10 = H.odna(r"The PN pattern used is PN \(10,3\)")[0]; s_ini = H.odna(r"The scramble pattern initial register value for control physical slots is '1111111111'")[0]
assert ostatok((1 << 2047) | 1, 2048, sum(1 << s for s in (12, 11, 3, 2, 1, 0))) == 0   # CRC-12 = (x+1)·примитивный степени 11 → делит x^2047+1
rec("PHS: CRC-16 (1+X5+X12+X16) / CRC-12 (BPSK), уникальные слова 32/16/10 бит, скремблер PN(10,3)", "Япония: PHS (ARIB RCR STD-28)", "CRC-подобный + скремблер",
    {"CRC": "π/4-QPSK, D8PSK, 16QAM — ITU-T CRC-16; π/2-BPSK — CRC-12 1+X+X2+X3+X11+X12; исправления нет",
     "UW": "управляющий слот π/4-QPSK: вверх 0110 1011 1000 1001 1001 1010 1111 0000, вниз 0101 0000 1110 1111 0010 1001 1001 0011; разговорный: вверх 1110 0001 0100 1001, вниз 0011 1101 0100 1100; допуск 1 ошибка",
     "преамбула": "SS 10 + PR 1001… (π/4-QPSK)", "скремблер": "PN(10,3), сброс каждый кадр; начальное 1111111111 (управление) / '1' + 9 младших бит CS-ID (разговор)",
     "модуляция": "π/4-DQPSK 384 кбит/с (также π/2-BPSK, D8PSK, 12/16QAM), TDMA-TDD 4 слота"},
    "PHS (Япония, Китай «Сяолинтун», Таиланд)", [H.ist(s_c, "4.2.10.1 CRC-16"), H.ist(s_c12, "CRC-12"), H.ist(s_uw, "UW"), H.ist(s_uwd, "UW разговорный"), H.ist(s_pn10, "PN(10,3)"), H.ist(s_ini, "начальное")],
    "по тексту STD-28 v5.3; CRC-12 делит x^2047+1 (период 2047 — как у CRC-12 с множителем (x+1))", "CRC и скремблер — общими средствами проекта (просто)")
