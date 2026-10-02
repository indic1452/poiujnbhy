"""Транковые и ведомственные сети передачи данных: MPT1327 (циклический (63,48) + инверсия + чётность, SYNC/SYNT),
RD-LAP (Motorola: ТКМ 3/4 K=6 на парах 4FSK, перемежение 4 строки, CRC-6/16/32 с тестовыми векторами), MOBITEX (Хэмминг (12,8), перемежение, скремблер V.52)."""
import re, sys, os, binascii
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

M = Tekst("istochniki/trank/MPT1327.pdf")
s_g = M.odna(r"^X15 \+ X14 \+ X13 \+ X11 \+ X4 \+ X2 \+ 1")[0]
G = sum(1 << s for s in (15, 14, 13, 11, 4, 2, 0))
assert ostatok((1 << 63) | 1, 64, G) == 0                                            # g делит x^63+1 — циклический (63,48)
s_inv = M.odna(r"The final check bit of the \(63,48\) cyclic")[0]
s_sc = M.odna(r"^1 1 0 0 0 1 0 0 1 1 0 1 0 1 1 1")[0]; s_st = M.odna(r"^0 0 1 1 1 0 1 1 0 0 1 0 1 0 0 0")[0]
SYNC, SYNT = 0xC4D7, 0x3B28; assert SYNC ^ SYNT == 0xFFFF
s_ff = M.odna(r"Signalling transmissions shall employ Fast Frequency Shift Keying \(FFSK\) at a bit")[0]
def mpt_slovo(info48):
    r = ostatok(info48 << 15, 63, G); cw = ((info48 << 15) | r) ^ 1                     # инверсия бита 63
    return (cw << 1) | (bin(cw).count("1") & 1)
w = mpt_slovo(0x800000000000); assert bin(w).count("1") % 2 == 0
rec("MPT1327: циклический (63,48) с инверсией последнего проверочного + общая чётность = (64,48)", "Транкинг: MPT1327", "БЧХ (циклический)",
    {"g(X)": "X15+X14+X13+X11+X4+X2+1", "слово": "64 бита: A (1 — адресное, 0 — данные), 47 бит информации, 15 проверочных (последний инвертирован), бит чётности (чётная по всему слову)",
     "синхро": "SYNC 1100010011010111 (0xC4D7, канал управления), SYNT = инверсия (0x3B28, разговорный/данные); преамбула 1010…",
     "слот": "2 слова: CCSC (системное слово с P + SYNC) + адресное", "модуляция": "FFSK 1200 бит/с (1200/1800 Гц)", "порядок": "бит 1 первым"},
    "аналоговые транковые сети MPT1327 (Алтай-подобные, Taxi, ведомственные)", [M.ist(s_g, "3.2.3"), M.ist(s_inv, "инверсия бита 63"), M.ist(s_sc, "SYNC"), M.ist(s_st, "SYNT"), M.ist(s_ff, "FFSK")],
    "g делит x^63+1 (циклический код длины 63 — проверено); SYNT = ~SYNC; кодер по тексту даёт слова с чётной чётностью", "нет в проекте; простой систематический код — внедрение простое")
R = Tekst("istochniki/trank/RD-LAP_protocol_sigidwiki.pdf")
def crc(data, w, poly, init, xorout):
    r = init; top = 1 << (w - 1); mask = (1 << w) - 1
    for b in data:
        for i in range(7, -1, -1):
            fb = ((r & top) != 0) ^ ((b >> i) & 1); r = ((r << 1) & mask) ^ (poly if fb else 0)
    return r ^ xorout
T = b"123456789"
assert crc(T, 16, 0x1021, 0, 0xFFFF) == 0xCE3C and crc(T, 32, 0x04C11DB7, 0, 0xFFFFFFFF) == 0x765E7680
c6 = crc(T, 6, 0x19, 0, 0x3F)
def crc_refl(data, w, poly, init, xorout):
    rp = int(bin(poly)[2:].zfill(w)[::-1], 2); r = init
    for b in data:
        r ^= b
        for _ in range(8): r = (r >> 1) ^ rp if r & 1 else r >> 1
    return r ^ xorout
c6r = crc_refl(T, 6, 0x19, 0, 0x3F)
assert 0x3B in (c6, c6r), (hex(c6), hex(c6r))
c6_otr = c6 != 0x3B
s_c0 = R.odna(r"CRC0\sSpecification")[0]; s_c1 = R.odna(r"CRC1\sSpecification")[0]; s_c2 = R.odna(r"Note:\sCRC.32/POSIX")[0]
# пример перемежения: 33 пары символов, 4 строки 9/8/8/8, запись по строкам, чтение по столбцам
kus, s_il = R.kusok(r"For greater clarity we highlight the first micro-slot symbol pairs in different colors:", r"\n\s*10 We like")
vh = re.findall(r"[+-][13][+-][13]", kus)
kus2, _ = R.kusok(r"We read out the following de-interleaved data stream:", r"It is readily apparent")
vyh = re.findall(r"[+-][13][+-][13]", kus2)
assert len(vh) == 33 and len(vyh) == 33
stroki = [vh[0:9], vh[9:17], vh[17:25], vh[25:33]]
chten = [stroki[r][c] for c in range(9) for r in range(4) if c < len(stroki[r])]
assert chten == vyh, "перемежение RD-LAP не сошлось с примером"
s_sy = R.odna(r"^-1 \+1 -1 \+1 -1 \+3 -3 \+3 -3 -1 \+1 -3 \+3 \+3 -1 \+1 -3 -3 \+1 \+3 -1 -3 \+1 \+3")[0]; s_tcm = R.odna(r"The error correction method\s+is a standard constraint length six Trellis Coded Modulation")[0]
rec("RD-LAP (Motorola, 19,2 кбит/с): ТКМ 3/4 (K=6) на парах символов 4FSK + перемежение по микрослотам", "Транкинг/данные: RD-LAP (Motorola)", "решётчатый (ТКМ 3/4)",
    {"ТКМ": "3 бита данных + 1 избыточный на пару символов (16 точек), K = 6, начальное состояние 0, завершающие нулевые трибиты; таблица переходов — табл. 2/5 источника",
     "перемежение": "33 пары символов блока (3 микрослота, без символов состояния) записываются по строкам 9/8/8/8 и читаются по столбцам (пример источника воспроизведён)",
     "кадр": "символьная синхронизация 24 (+3+3−3−3…), кадровая синхро 24: −1+1−1+1−1+3−3+3−3−1+1−3+3+3−1+1−3−3+1+3−1−3+1+3; символ состояния каждые 22 символа; ИД станции 22 символа",
     "модуляция": "4FSK 9600 Бод", "родство": "ТКМ 3/4 APCO P25 (данные) ведёт начало от RD-LAP"},
    "мобильные терминалы данных полиции/скорой (США), Motorola DataTAC", [R.ist(s_sy, "кадровая синхро"), R.ist(s_il, "пример перемежения"), R.ist(s_tcm, "ТКМ")],
    "пример источника: 33 пары символов после перестановки по строкам 9/8/8/8 точно дали приведённый деперемеженный поток (assert)",
    "частично: решётчатый 3/4 P25 в проекте (p25.py/тrellis MMDVMHost) — сравнить таблицы; кадр RD-LAP внедрить (средне)")
rec("RD-LAP CRC0/CRC1/CRC2: CRC-6 (x6+x4+x3+1), CRC-16/GSM, CRC-32/POSIX", "Транкинг/данные: RD-LAP (Motorola)", "CRC-подобный",
    {"CRC0": "6 бит, 0x19, начальное 0, итог инвертирован" + (" (отражённый вариант даёт вектор)" if c6_otr else "") + ", проверка «123456789» = 0x3B — ИД станции",
     "CRC1": "16 бит, 0x1021, начальное 0, итог инвертирован (= CRC-16/GSM), «123456789» = 0xCE3C — заголовок",
     "CRC2": "32 бита, 0x04C11DB7, начальное 0, итог инвертирован (= CRC-32/POSIX, cksum), «123456789» = 0x765E7680 — блоки PDU"},
    "RD-LAP", [R.ist(s_c0, "CRC0"), R.ist(s_c1, "CRC1"), R.ist(s_c2, "CRC2")],
    "все три тестовых вектора источника воспроизведены вычислением (CRC0: " + ("отражённый" if c6_otr else "прямой") + " алгоритм)", "есть: crc_katalog.py (CRC-16/GSM, CRC-32/POSIX); CRC-6 — добавить параметры (просто)")
MB = Tekst("istochniki/trank/MOBITEX_Architecture.pdf")
s_h = MB.odna(r"dently encoded using a shortened \(12, 8\) Hamming code")[0]; s_sc = MB.odna(r"stage scrambler, which generates a")[0]
s_pr = MB.odna(r"tion or from another mobile station|pattern starts with a couple of 1s")[0]; s_crc = MB.odna(r"with a 16-bit CRC \(cyclic redundancy check\) appended at the")[0]
rec("MOBITEX: укороченный Хэмминг (12,8) на каждый байт + перемежение по столбцам + скремблер V.52 (9 разрядов)", "Транкинг/данные: MOBITEX (Ericsson)", "каскадный: Хэмминг + перемежитель + скремблер + CRC",
    {"блок": "20 байт + CRC-16 (канальный уровень)", "Хэмминг": "(12,8) укороченный, исправляет 1 ошибку в байте; матрица блока передаётся по столбцам с (1,1)",
     "скремблер": "9-разрядный, последовательность = тестовая V.52 (x9+x5+1, ПСП 511), кроме заголовка кадра", "преамбула": "1100110011001100 (база) / 0011001100110011 (мобильный) + SYNC (свой у сети) + Base ID/Area ID",
     "модуляция": "GMSK 8 кбит/с, девиация 2 кГц"},
    "MOBITEX (Ericsson), сети передачи данных 1990-х, телеметрия", [MB.ist(s_h, "Хэмминг (12,8)"), MB.ist(s_sc, "скремблер V.52"), MB.ist(s_pr, "преамбула"), MB.ist(s_crc, "блок+CRC")],
    "только обзорная статья IEEE Pers. Comm. 1997 (спецификация MOBITEX Interface Specification закрыта): матрица H кода (12,8) и многочлен CRC в ней не приведены",
    "нет в проекте; Хэмминг и скремблер V.52 (x9+x5+1) есть в общих модулях; точная H неизвестна — нужен подбор по записи (средне)")
# --- EDACS (Ericsson/GE) — по открытому коду dsd-fme --------------------------------------------------------------------------
eb = kod("dsd-fme", "src/edacs-bch3.c"); ef = kod("dsd-fme", "src/edacs-fme.c"); eh = kod("dsd-fme", "include/dsd.h")
tb = open(os.path.join(KOREN, eb)).read(); tf = open(os.path.join(KOREN, ef)).read(); th = open(os.path.join(KOREN, eh)).read()
assert "m = 6;" in tb and "length = 40;" in tb and "else if (m == 6)	p[1] = 1;" in tb
assert "two 40-bit (28-bit data, 12-bit BCH) messages" in tf and "fr_2 and fr_5 are transmitted inverted" in tf
ES = re.search(r'#define EDACS_SYNC\s+"([13]+)"', th).group(1); assert len(ES) == 48
# БЧХ t=2 над GF(64), p = x^6+x+1: g = m1·m3 степени 12 → (63,51) → укороченный (40,28)
def pmul2(a, b):
    r = 0
    for i in range(b.bit_length()):
        if b >> i & 1: r ^= a << i
    return r
EXP = [0] * 63; x = 1
for i in range(63):
    EXP[i] = x; x <<= 1
    if x & 64: x ^= 0b1000011
def minpoly(e):
    kor = sorted({(e * 2 ** j) % 63 for j in range(6)}); p = [1]
    for r in kor:                                                                  # ∏(x − α^r) над GF(64)
        a = EXP[r]; np_ = [0] * (len(p) + 1)
        for i, c in enumerate(p):
            np_[i + 1] ^= c
            if c: np_[i] ^= EXP[(EXP.index(c) + r) % 63]
        p = np_
    assert all(c in (0, 1) for c in p); return sum(c << i for i, c in enumerate(p))
GE = pmul2(minpoly(1), minpoly(3)); assert GE.bit_length() - 1 == 12 and minpoly(1) == 0b1000011
assert GE == int("1010100111001", 2)                                              # = g БЧХ-2 КОСПАС-САРСАТ (C/S T.001 прил. B2)
rec("EDACS (Ericsson/GE): БЧХ (40,28) t=2 — укороченный (63,51) над GF(64), тройной повтор сообщения (средний инвертирован)", "Транкинг: EDACS", "БЧХ + повторение",
    {"БЧХ": "GF(2^6), p(x) = x6+x+1, t = 2, g = m1·m3 = " + bin(GE)[2:] + " (степень 12), укорочение 63 → 40", "кадр": "синхро 48 бит (24 дибита: " + ES + " в символах ±1/±3) + 2 сообщения × 3 копии по 40 бит (вторая копия инвертирована), голосование по битам",
     "модуляция": "FSK 9600 бит/с (EDACS narrowband 4800)"},
    "транковые сети EDACS/ProVoice (ГИБДД США, энергетика)", [ist_kod(eb, r"length = 40;", "dsd-fme БЧХ"), ist_kod(ef, r"two 40-bit \(28-bit data, 12-bit BCH\)", "кадр"), ist_kod(eh, r"#define EDACS_SYNC", "синхро")],
    "спецификация Ericsson закрыта — по открытому коду dsd-fme (GPL) с assert; g вычислен программно (минимальные многочлены α и α³), степень 12 = 40 − 28; g совпал с g(x) БЧХ-2 КОСПАС-САРСАТ (C/S T.001, 1010100111001) — второй источник",
    "нет в проекте; БЧХ общего вида есть — внедрение простое")
