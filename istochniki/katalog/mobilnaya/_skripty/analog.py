"""Аналоговые сотовые 1G (каналы сигнализации): AMPS/TACS/JTACS (укороченный БЧХ (63,51) → (40,28)/(48,36), d = 5, Баркер-11,
повтор ×5 слов A/B), NMT-450/900 и Radiocom 2000 (свёрточный код Хагельбаргера (6,19)), C-Netz/C-450 (блочный (15,7), d = 5,
перемежение 10×15, 3 × Баркер-11). Первоисточник значений — открытый код osmocom-analog (GPL-3, реализация по FTZ 171 TR 60,
NMT Doc., EIA/TIA-553), сверка — ITU-R Report M.742-4 (табл. 1: «Error protection coding») и вычислением свойств кода."""
import os, re, sys, random, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
RP = "osmocom-analog"
k_amps = kod(RP, "src/amps/frame.c"); k_hag = kod(RP, "src/libhagelbarger/hagelbarger.c"); k_nmt = kod(RP, "src/nmt/frame.c")
k_nmtd = kod(RP, "src/nmt/dsp.c"); k_r2k = kod(RP, "src/r2000/frame.c"); k_cn = kod(RP, "src/cnetz/telegramm.c")
M = Tekst("istochniki/analog/R-REP-M.742-4-1995.pdf")
s_bch = M.odna(r"^\(40:28\) BCH")[0]; s_hg = M.odna(r"^code \(6:19\)")[0]; s_157 = M.odna(r"^\(15:7\) BCH")[0]
s_nmt = M.odna(r"The Nordic mobile telephone system \(NMT-450\) was put into operation")[0]
tx = lambda put: open(os.path.join(KOREN, put), errors="replace").read()

def ost(v, g):
    gl = g.bit_length() - 1
    for i in range(v.bit_length() - 1, gl - 1, -1):
        if v >> i & 1: v ^= g << (i - gl)
    return v
def d_ge5(H):
    """Все суммы ≤ 2 различных столбцов ненулевые и попарно различны ⇔ любые ≤ 4 столбца независимы ⇔ d ≥ 5."""
    s = set(H)
    if 0 in s or len(s) != len(H): return False
    pary = [a ^ b for a, b in itertools.combinations(H, 2)]
    return len(set(pary)) == len(pary) and not (set(pary) & s) and 0 not in pary

# ---------------- AMPS / TACS / JTACS ---------------------------------------------------------------------------------------------
gp = c_massiv(k_amps, "gp"); assert len(gp) == 12
def amps_bch(bity):                                           # перенос encode_bch() osmocom-analog (MSB первым)
    r = [0] * 12
    for b in bity:
        fb = b ^ r[0]
        r = r[1:] + [0]
        if fb: r = [x ^ g for x, g in zip(r, gp)]
    return r
G_AMPS = (1 << 12) | int("".join(map(str, gp)), 2)
assert ost((1 << 63) | 1, G_AMPS) == 0                       # g | x^63 + 1 — циклический код длины 63 (51 информационный)
random.seed(1)
for n_k in (28, 36):
    for _ in range(200):
        d = [random.randint(0, 1) for _ in range(n_k)]
        v = int("".join(map(str, d)), 2)
        assert int("".join(map(str, amps_bch(d))), 2) == ost(v << 12, G_AMPS)          # кодер = остаток от деления на g
    H = [ost(1 << i, G_AMPS) for i in range(n_k + 12)]
    assert d_ge5(H), n_k                                                              # d ≥ 5 для (40,28) и (48,36)
st_g = D_zapis([i for i in range(13) if G_AMPS >> i & 1]).replace("D", "x")
s_dot = stroka_koda(k_amps, r'dotting = "1010'); s_sw = stroka_koda(k_amps, r'sync_word = "11100010010"')
assert "11100010010" in tx(k_amps)
rec("AMPS/TACS/JTACS: укороченный БЧХ (63,51) → (40,28) прямой / (48,36) обратный канал управления, d = 5; точечная + Баркер-11; повтор ×5",
    "Аналоговые сотовые (1G): AMPS/TACS", "БЧХ + повторение",
    {"n,k": "FOCC (от БС): (40,28); RECC (от МС): (48,36); укорочение циклического (63,51)", "g(x)": st_g + f" (0x{G_AMPS:X}) — из массива gp[] кодера; делит x^63+1",
     "порядок": "старший бит первым; 12 проверочных после данных", "повтор": "FOCC: слова A и B по 5 раз вперемежку (A,B,A,B…), через каждые 10 бит — бит занятости/свободы (B/I); FVC: 11 повторов слова",
     "синхро": "точечная последовательность 1010… (10 бит FOCC, 101/37 бит FVC) + слово синхронизации 11100010010 (Баркер-11)",
     "модуляция": "ЧМн ±8 кГц, Манчестер 10 кбит/с (AMPS); TACS/JTACS — те же коды (8 кбит/с)"},
    "сотовые сети 1G США (AMPS), Великобритания/Италия (TACS), Япония (JTACS)",
    [ist_kod(k_amps, r"static char gp\[12\]", "gp — многочлен"), ist_kod(k_amps, r"do BCH\(length\+12,length,5\)", "кодер"), ist_kod(k_amps, r'sync_word = "11100010010"', "синхрослово"),
     ist_kod(k_amps, r"static void amps_encode_focc_bits", "FOCC: 5 × (A,B)"), M.ist(s_bch, "M.742 табл. 1: (40:28)/(48:36) BCH, укороченный (63:51)")],
    "кодер osmocom-analog перенесён и совпал с делением на g для 400 случайных слов; g | x^63+1; d ≥ 5 для (40,28) и (48,36) проверено по столбцам H (assert); "
    "длины и укорочение (63:51) совпадают с ITU-R M.742-4 табл. 1",
    "нет в проекте: БЧХ-кодер/декодер есть (rs_bch.py) — добавить кадр FOCC/RECC (просто)")

# ---------------- Хагельбаргер (NMT, Radiocom 2000) ------------------------------------------------------------------------------
t_h = tx(k_hag)
assert "check = (reg + (reg >> 3) + 1) & 1;" in t_h and "data = (reg >> 6) & 1;" in t_h
def hag_kod(d):                                                   # перенос hagelbarger_encode: выход (проверочный, данные) парами
    reg = 0; out = []
    for b in d:
        reg = ((reg << 1) | b) & 0xFF
        out += [(reg ^ (reg >> 3) ^ 1) & 1, (reg >> 6) & 1]
    return out
def hag_dekod(c, n):                                              # перенос hagelbarger_decode
    rd, rc = 0, 0xFF; out = []
    for i in range(n + 10):
        chk = c[2 * i] if 2 * i < len(c) else 0; dat = c[2 * i + 1] if 2 * i + 1 < len(c) else 0
        rc = ((rc << 1) | chk) & 0xFFFF; rd = ((rd << 1) | dat) & 0xFFFF
        r = (rd ^ (rd >> 3) ^ (rc >> 6) ^ 1) & 1; s = ((rd >> 3) ^ (rd >> 6) ^ (rc >> 9) ^ 1) & 1
        if r and s: rd ^= 8
        if i >= 10: out.append((rd >> 4) & 1)
    return out
random.seed(2); ok = 0
for _ in range(300):
    m = [random.randint(0, 1) for _ in range(64)]
    c = hag_kod(m + [0] * 6) + [0] * 12
    assert hag_dekod(c, 64) == m
    L = random.randint(1, 6); p = random.randint(0, 139 - L - 19)                   # пачка ≤ 6 кодовых бит, далее ≥ 19 верных
    c2 = c[:];
    for j in range(p, p + L): c2[j] ^= 1
    assert hag_dekod(c2, 64) == m, (L, p); ok += 1
s_nm = stroka_koda(k_nmt, r'memcpy\(bits, "10101010101010111100010010", 26\)'); s_r2 = stroka_koda(k_r2k, r'memcpy\(bits, "10101010101010101010111100010010", 32\)')
assert "0xaf12" in tx(k_nmtd)
rec("NMT-450/900 и Radiocom 2000: свёрточный код Хагельбаргера (6,19), 1/2 — исправление пачек до 6 бит при ≥ 19 верных после",
    "Аналоговые сотовые (1G): NMT / Radiocom 2000", "свёрточный (Хагельбаргер, пачечный)",
    {"кодер": "регистр входа; проверочный c_i = d_i ⊕ d_(i−3) ⊕ 1 (инвертирован), бит данных задержан на 6; выход парами (c, d) старшим вперёд",
     "декодер": "синдромы r = d_i⊕d_(i−3)⊕c_(i−6)⊕1, s = d_(i−3)⊕d_(i−6)⊕c_(i−9)⊕1; при r = s = 1 инвертируется бит данных; задержка 10",
     "NMT кадр": "166 бит: 15 бит синхро по битам 101010… + 11 бит кадровой 11100010010 (приём ищет 1010111100010010 = 0xAF12) + 140 кодовых (64 информ. = 16 тетрад + 6 хвост)",
     "Radiocom 2000 кадр": "32 бита синхро 10101010101010101010111100010010 + 176 кодовых (88 бит, из них 80 информ.)",
     "модуляция": "ЧМн (FFSK) 1200 бит/с (NMT), контроль ошибок — повтором цифр в сообщении (нет CRC)"},
    "сотовые сети NMT-450 (в т.ч. «Дельта Телеком»/«Сотел» в России), NMT-900, Radiocom 2000 (Франция)",
    [ist_kod(k_hag, r"Hagelbarger \(6,19\) code", "описание кода"), ist_kod(k_hag, r"check = \(reg \+ \(reg >> 3\) \+ 1\) & 1", "кодер"), ist_kod(k_hag, r"r_parity = ", "декодер"),
     ist_kod(k_nmt, r'memcpy\(bits, "10101010101010111100010010", 26\)', "NMT синхро"), ist_kod(k_nmtd, r"0xaf12", "NMT поиск синхро"), ist_kod(k_r2k, r'memcpy\(bits, "10101010101010101010111100010010", 32\)', "R2000 синхро"),
     M.ist(s_hg, "M.742 табл. 1: Hagelbarger code (6:19)"), M.ist(s_nmt, "M.742: NMT")],
    f"кодер/декодер osmocom-analog перенесены; {ok} случайных сообщений с одной пачкой 1–6 ошибок (дальше ≥ 19 верных) исправлены полностью — assert; параметр (6:19) совпал с ITU-R M.742-4",
    "нет в проекте; внедрение простое (регистр 8 бит, синхро 0xAF12)")

# ---------------- C-Netz (C-450) ---------------------------------------------------------------------------------------------------
t_c = tx(k_cn)
m = re.search(r"static char \*blockcode\[128\] = \{(.*?)\};", t_c, re.S)
BK = [a + b for a, b in re.findall(r'"([01]{7})"\s*"([01]{8})"', m.group(1))]; assert len(BK) == 128   # 7 полезных + 8 избыточных
slova = [int(s[::-1], 2) for s in BK]                            # как в init_coding: символ 14 — старший
assert all((w & 0x7F) == i for i, w in enumerate(slova))        # младшие 7 — данные (систематический)
lin = all(slova[a ^ b] == slova[a] ^ slova[b] for a in range(128) for b in range(128))
dmin = min(bin(w).count("1") for w in slova if w)
assert lin and dmin == 5, (lin, dmin)
# циклическое представление: ищем g степени 8, делящий x^15+1, на который делятся все слова (в одной из двух записей порядка бит)
kand = []
for g in range(1 << 8, 1 << 9):
    if ost((1 << 15) | 1, g) == 0:
        for rev in (0, 1):
            if all(ost(int(format(w, "015b")[::-1], 2) if rev else w, g) == 0 for w in slova): kand.append((g, rev))
assert kand, "не циклический"
g157, rev157 = kand[0]
s_bar = stroka_koda(k_cn, r'barker_string = "11100010010"')
rec("C-Netz (C-450, ФРГ/Португалия/ЮАР): блочный (15,7) d = 5 (циклический БЧХ) ×10 + перемежение 10×15 + синхро 3 × Баркер-11",
    "Аналоговые сотовые (1G): C-Netz", "БЧХ + блочный перемежитель",
    {"n,k": "(15,7), d = 5 (исправляет 2 ошибки), систематический: 7 младших бит — данные", "g(x)": D_zapis([i for i in range(9) if g157 >> i & 1]).replace("D", "x") + (" (в обратной записи бит слова)" if rev157 else "") + " — найден перебором из таблицы кодера",
     "таблица": "128 кодовых слов blockcode[] (osmocom-analog, по FTZ 171 TR 60 5.1.1.3)",
     "телеграмма": "70 бит данных → 10 слов (15,7) → перемежение: бит j слова i идёт на позицию i + 10·j (запись по строкам, чтение по столбцам 10×15) → 150 бит",
     "синхро": "3 × 11100010010 (Баркер-11) + бит 1 перед блоком (33 + 1 + 150 = 184 бита)", "стандарт": "FTZ 171 TR 60 (Deutsche Bundespost)"},
    "сотовая сеть C-Netz (C-450) 1985–2000",
    [ist_kod(k_cn, r"static char \*blockcode\[128\]", "таблица 128 слов"), ist_kod(k_cn, r"FTZ 171 TR 60 / 5\.1\.1\.3", "кодер"), ist_kod(k_cn, r"static char \*interleave", "перемежение"),
     ist_kod(k_cn, r'barker_string = "11100010010"', "синхро"), M.ist(s_157, "M.742 табл. 1: (15:7) BCH")],
    f"таблица из открытого кода: линейна, систематична, d_min = {dmin} (вычислено по всем 127 ненулевым словам), все слова делятся на g = 0x{g157:X} ⇒ циклический БЧХ (15,7); "
    "(15:7) BCH — в ITU-R M.742-4 табл. 1",
    "нет в проекте; БЧХ (15,7) и блочное перемежение есть в kod.py/rs_bch.py — добавить кадр (просто)")
tablica("cnetz_blok_15_7", {"slova_int_mladshie7_dannye": slova, "g": hex(g157), "obratnaya_zapis": bool(rev157)},
        "C-Netz: 128 слов блочного кода (15,7) из osmocom-analog/src/cnetz/telegramm.c", [ist_kod(k_cn, r"static char \*blockcode\[128\]")],
        f"линейность, систематичность, d_min = {dmin}, делимость на g — assert")
