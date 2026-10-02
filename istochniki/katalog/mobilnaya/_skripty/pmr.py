"""Сигнализация аналоговых PMR/транкинга по открытому коду: MDC-1200 (Motorola: CRC-16 + систематический свёрточный K=7 1/2 +
перемежение 16×7, синхро 0x07092A446F — два тестовых вектора из комментария прошивки воспроизведены), Fleetsync II (Kenwood:
блок 64 = 48 + CRC-15 + чётность), Passport (Trident: CRC-7 + чётность), LTR (E.F. Johnson: CRC-7 таблицей). Многочлены CRC
Fleetsync/Passport выведены из таблиц синдромов SDRTrunk (GPL-3) проверкой рекуррентности x·s mod g."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
tx = lambda put: open(os.path.join(KOREN, put), errors="replace").read()

# ---------------- MDC-1200 --------------------------------------------------------------------------------------------------------
k_m = kod("uvk5", "mdc1200.c"); k_mh = kod("uvk5", "mdc1200.h")
k_sp = kod("sdrtrunk", "src/main/java/io/github/dsheirer/bits/SyncPattern.java")
k_md = kod("sdrtrunk", "src/main/java/io/github/dsheirer/module/decode/mdc1200/MDCDecoder.java")
t = tx(k_m)
assert re.search(r"mdc1200_sync\[5\]\s*=\s*\{0x07, 0x09, 0x2a, 0x44, 0x6f\}", t)
assert "Sync (0x07092A446F)" in tx(k_sp)                                                  # второй источник синхрослова (SDRTrunk)
assert "(crc >> 1) ^ 0x8408" in t and "return crc ^ 0xffff;" in t and "uint16_t       crc = 0;" in t
assert "((shift_reg >> 6) ^ (shift_reg >> 5) ^ (shift_reg >> 2) ^ (shift_reg >> 0))" in t
def crc_mdc(b):
    c = 0
    for x in b:
        c ^= x
        for _ in range(8): c = (c >> 1) ^ 0x8408 if c & 1 else c >> 1
    return c ^ 0xFFFF
def fec_mdc(d):
    sr = 0; out = []
    for bi in d:
        bo = 0
        for k in range(8):
            sr = ((sr << 1) | ((bi >> k) & 1)) & 0xFF
            bo |= (((sr >> 6) ^ (sr >> 5) ^ (sr >> 2) ^ sr) & 1) << k
        out.append(bo)
    return out
vekt = re.findall(r"//\s*OP\s+ARG\s+ID\s+CRC\s+STATUS\s+FEC bits\s*\n\s*//\s*([0-9A-F]{2})\s+([0-9A-F]{2})\s+([0-9A-F]{4})\s+([0-9A-F]{4})\s+([0-9A-F]{2})\s+([0-9A-F]{14})", t)
vekt2 = re.findall(r"//\s*((?:[0-9A-F]{2}\s+){13}[0-9A-F]{2})[ \t]*\n", t)
assert vekt and vekt2, (vekt, vekt2)
n_v = 0
for op, arg, idn, crc, st, fb in set(vekt):
    d = [int(op, 16), int(arg, 16), int(idn[:2], 16), int(idn[2:], 16)]
    c = crc_mdc(d); assert "%02X%02X" % (c & 0xFF, c >> 8) == crc
    d7 = d + [c & 0xFF, c >> 8, int(st, 16)]; assert bytes(fec_mdc(d7)).hex().upper() == fb; n_v += 1
for s in vekt2:
    b = [int(x, 16) for x in s.split()]
    c = crc_mdc(b[:4]); assert (c & 0xFF, c >> 8) == (b[4], b[5]) and fec_mdc(b[:7]) == b[7:]; n_v += 1
rev = crc_reveng(r"CRC-16/KERMIT")
rec("MDC-1200 (Motorola): CRC-16 (0x1021 отражённый, нач. 0, инверсия) + систематический свёрточный K=7 1/2 (1+D²+D⁵+D⁶) + перемежение 16×7, синхро 0x07092A446F",
    "PMR сигнализация: MDC-1200 (Motorola)", "каскадный: CRC + свёрточный систематический + блочный перемежитель",
    {"пакет": "7 байт: OP, ARG, ID (2), CRC (2, младший первым), STATUS; + 7 байт проверочных FEC = 112 бит; двойной пакет — 2 × 14 байт",
     "CRC": "CRC-16, многочлен 0x1021 в отражённой записи 0x8408, начальное 0, выход инвертирован (как CRC-16/KERMIT с xorout 0xFFFF)",
     "свёрточный": "систематический 1/2, K = 7: p_n = d_n ⊕ d_(n−2) ⊕ d_(n−5) ⊕ d_(n−6), регистр с нуля, биты байта младшим вперёд",
     "перемежение": "112 бит: 16 строк × 7 столбцов (бит n = 16m + i)", "синхро": "преамбула ≥ 24 бит + 40 бит 0x07092A446F; передача — разностное кодирование (XOR соседних бит) с инверсией",
     "модуляция": "AFSK 1200 Бод, 2FSK (SDRTrunk MDCDecoder: «1200 baud 2FSK», инверсный выход)"},
    "ИД вызывающего (PTT ID), экстренный вызов, статус в аналоговых сетях Motorola; радиостанции Quansheng UV-K5 (открытая прошивка)",
    [ist_kod(k_md, r"1200 baud 2FSK", "SDRTrunk: 1200 Бод 2FSK"), ist_kod(k_m, r"mdc1200_sync\[5\]", "синхро"), ist_kod(k_m, r"\(crc >> 1\) \^ 0x8408", "CRC"), ist_kod(k_m, r"R=1/2 K=7 convolutional coder", "FEC + вектор"),
     ist_kod(k_m, r"interleave order", "перемежение 16×7"), ist_kod(k_sp, r"Sync \(0x07092A446F\)", "SDRTrunk: синхро")],
    f"{n_v} тестовых вектора из комментариев кода (01 80 1234 → CRC 2E3E, FEC 6580A862DD8808; 01 00 0023 → DDF0 … 1F21) воспроизведены переносом кодера — assert; синхро совпало в SDRTrunk",
    "частично: CRC-16 KERMIT есть в crc_katalog.py (" + ("есть" if rev else "нет") + "), нужен вариант с xorout 0xFFFF; свёрточный систематический — kod.py (просто)")

# ---------------- Fleetsync II / Passport / LTR (таблицы синдромов SDRTrunk) --------------------------------------------------------
E = "src/main/java/io/github/dsheirer/edac/"
k_fs = kod("sdrtrunk", E + "CRCFleetsync.java"); k_pp = kod("sdrtrunk", E + "CRCPassport.java"); k_lt = kod("sdrtrunk", E + "CRCLTR.java")
k_fd = kod("sdrtrunk", "src/main/java/io/github/dsheirer/module/decode/fleetsync2/Fleetsync2Decoder.java")
def tabl(put):
    b = tx(put); b = b[b.index("sCHECKSUMS"):]; b = b[b.index("{") + 1:b.index("};")]
    return [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", b)]
def rekurr(s, w):                                                     # s[i+1] = x·s[i] mod g ⇒ g = x^w + ((x·s[i]) ⊕ s[i+1]) при переносе
    gs = set()
    for a, b in zip(s, s[1:]):
        sh = a << 1
        if sh >> w & 1: gs.add((sh ^ b) | (1 << w))
        else: assert sh == b, "не CRC"
    assert len(gs) == 1; return gs.pop()
FS = tabl(k_fs); assert len(FS) == 63
g_fs = rekurr(FS[:48][::-1], 15); assert FS[48:] == [1 << (14 - i) for i in range(15)]
PP = tabl(k_pp); assert len(PP) == 51
g_pp = rekurr([v >> 1 for v in PP][::-1], 7)
LT = tabl(k_lt); assert len(LT) == 24
sfs = next(l for l in tx(k_sp).split("\n") if "Sync (0x23EB)" in l); spp = "Sync (0x158) = 101011000" in tx(k_sp)
assert spp and "Sync (0x23EB)" in sfs
gz = lambda g, w: " + ".join(["x^%d" % i if i > 1 else ("x" if i == 1 else "1") for i in range(w, -1, -1) if g >> i & 1])
rec(f"Fleetsync II (Kenwood): блоки 64 бита = 48 данных + CRC-15 (g = 0x{g_fs:X}) + общая чётность; синхро 0x23EB", "PMR сигнализация: Fleetsync (Kenwood)", "CRC-подобный + чётность",
    {"блок": "64 бита: 48 данных, 15 CRC (старший первым), чётность (чётная по 64)", "CRC": f"g(x) = {gz(g_fs, 15)}; аффинная: начальное значение синдрома 1",
     "кадр": "биты точек (0101…) + синхро 16 бит 0x23EB (Fleetsync II; I — 0x7650) + до 8 блоков по 64 → 537 бит", "модуляция": "AFSK 1200 (SDRTrunk Fleetsync2Decoder на AFSK1200Decoder)"},
    "ИД/статус/SMS в аналоговых сетях Kenwood (Fleetsync I/II)", [ist_kod(k_fs, r"0x740A", "таблица 48 столбцов"), ist_kod(k_fs, r"int calculated = 1", "нач. 1 и чётность"),
     ist_kod(k_sp, r"Sync \(0x23EB\)", "синхро"), ist_kod(k_fd, r"MESSAGE_LENGTH = 537", "длина")],
    f"таблица 48 столбцов синдрома SDRTrunk удовлетворяет рекуррентности s_(i+1) = x·s_i mod g ровно для одного g = 0x{g_fs:X} (assert) ⇒ это CRC-15; 15 последних столбцов — единичные (систематичность)",
    "нет в проекте; CRC по g и чётность — просто")
rec(f"Passport (Trident): OSW 68 бит — 51 бит + CRC-7 (g = 0x{g_pp:X}) + бит чётности; синхро 101011000", "Транкинг: Passport (Trident Micro)", "CRC-подобный + чётность",
    {"CRC": f"g(x) = {gz(g_pp, 7)} (старшие 7 бит проверочного байта), младший бит — чётность", "поля": "DCC 2, LCN 11, SITE 7, GROUP 16, TYPE 4, FREE 11 (биты 9–59), проверка — биты 60–67",
     "синхро": "9 бит 101011000 (0x158)"},
    "транковые системы Passport (США)", [ist_kod(k_pp, r"0x6E", "таблица"), ist_kod(k_sp, r"Sync \(0x158\)", "синхро")],
    f"старшие 7 бит 51 столбца таблицы удовлетворяют рекуррентности CRC ровно для g = 0x{g_pp:X} (assert)", "нет в проекте; просто")
rec("LTR (E.F. Johnson Logic Trunked Radio): 24 бита полей + CRC-7 таблицей, ISW — допускается инверсный CRC", "Транкинг: LTR (E.F. Johnson)", "CRC-подобный",
    {"поля": "Area 1, Channel 5, Home 5, Group 8, Free 5 (биты 9–32); проверка 7 бит", "CRC": "таблица 24 столбцов (SDRTrunk CRCLTR); порядок полей не совпадает с порядком степеней — единый g по таблице не выводится",
     "синхро": "OSW 101011000, ISW 010100111 (инверсия)", "модуляция": "субтональные данные 300 бит/с"},
    "транковые сети LTR/LTR-Net/MultiNet", [ist_kod(k_lt, r"0x38, //Area", "таблица"), ist_kod(k_lt, r"calculated \^ 127", "инверсный CRC ISW"), ist_kod(k_sp, r"LTR_STANDARD_OSW", "синхро")],
    "таблица перенесена в tablicy/pmr_crc_tablicy_sdrtrunk.json; проверка: перебором всех g степени 7 последовательной рекуррентности нет (поля идут не в порядке степеней) — только табличная проверка",
    "нет в проекте; табличная проверка — просто")
tablica("pmr_crc_tablicy_sdrtrunk", {"fleetsync_48x15": FS, "g_fleetsync": hex(g_fs), "passport_51x8": PP, "g_passport_crc7": hex(g_pp), "ltr_24x7": LT},
        "Таблицы синдромов CRC Fleetsync II, Passport, LTR из SDRTrunk (GPL-3)", [ist_kod(k_fs, r"0x740A"), ist_kod(k_pp, r"0x6E"), ist_kod(k_lt, r"0x38")],
        "Fleetsync/Passport — рекуррентность CRC (assert); LTR — только таблица")
