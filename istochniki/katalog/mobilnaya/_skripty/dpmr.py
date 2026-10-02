"""dPMR (ETSI TS 102 658 V2.6.1; dPMR446 — TS 102 490 V1.9.1): CRC7/CRC8, укороченный Хэмминг (12,8), скремблер X9+X5+1,
перемежение 6×12 и 10×12, синхрослова FS1–FS4, канальные коды CC (64). Сверка: dsd-fme (dpmr_voice.c, dpmr_data.c, fec.c, dsd.h)."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/dpmr/ts_102658v020601p.pdf")
T4 = Tekst("istochniki/dpmr/ts_102490v010901p.pdf")
ZAPISI = []
dv = kod("dsd-fme", "src/dpmr_voice.c"); dd = kod("dsd-fme", "src/dpmr_data.c"); fec = kod("dsd-fme", "src/fec.c"); dh = kod("dsd-fme", "include/dsd.h")
tx = lambda p: open(os.path.join(KOREN, p)).read()

# CRC (табл. 7.1)
kus, s_crc = T.kusok(r"Table 7\.1: CRC coding", r"\n7\.2\s*\n", posl=True)
k1 = " ".join(kus.split())
assert re.search(r"CRC7 X7 \+ X3 \+ 1", k1) and re.search(r"CRC8 X8 \+ X2 \+ X1 \+ 1", k1), k1
assert "Polynome = 0x09;      /* X^7 + X^3 + 1 */" in tx(dv) and "Polynome = 0x07;" in tx(dv)
# Хэмминг (12,8) — табл. 7.2
kus, s_h = T.kusok(r"Table 7\.2: Generator matrix", r"Shortened Hamming code \(12,8\) Polynomial: X4 \+ X \+ 1", posl=True)
ch = [int(x) for x in re.findall(r"\b\d+\b", kus)]
G = []; i = next(k for k in range(len(ch)) if ch[k] == 1 and ch[k + 1:k + 9] == [1, 0, 0, 0, 0, 0, 0, 0])   # строка 1 (после заголовка, где метка столбца «1»)
while i < len(ch) and len(G) < 8:
    if ch[i] == len(G) + 1 and all(v in (0, 1) for v in ch[i + 1:i + 13]) and len(ch[i + 1:i + 13]) == 12: G.append(ch[i + 1:i + 13]); i += 13
    else: i += 1
assert len(G) == 8 and all(G[r][:8] == [1 if c == r else 0 for c in range(8)] for r in range(8)), G
def pmod(a, b):
    while a and a.bit_length() >= b.bit_length(): a ^= b << (a.bit_length() - b.bit_length())
    return a
for r in range(8):
    assert int("".join(map(str, G[r][8:])), 2) == pmod(1 << (11 - r), 0b10011), r        # строка = x^(11−r) mod (x^4+x+1)
dG = c_massiv(fec, "Hamming_12_8_m_G"); assert [dG[12 * r:12 * r + 12] for r in range(8)] == G
dmin = min(bin(sum(int("".join(map(str, G[r])), 2) * ((u >> r) & 1) for r in range(8)) if False else 0).count("1") for u in [0]) if False else None
slova = []
for u in range(1, 256):
    w = 0
    for r in range(8):
        if (u >> r) & 1: w ^= int("".join(map(str, G[r])), 2)
    slova.append(bin(w).count("1"))
DMIN = min(slova); assert DMIN == 3
s_h2 = T4.odna(r"Shortened Hamming code \(12,8\) Polynomial: X\^4 \+ X \+ 1")[0]
# скремблер
s_scr = T.odna(r"The scrambling polynomial is X9 \+ X5 \+ 1 with an initial preset value of all")[0]
assert "Temp = S[4] ^ S[0];" in tx(dv)
# перемежение
kus, s_il = T.kusok(r"Table 7\.3: TCH Interleaving matrix", r"The Interleave Structure Matrix Map", posl=True)
chi = chisla_bez_kolontitulov(kus) or [int(x) for x in re.findall(r"\d+", kus)]
chi = [int(x) for x in re.findall(r"\d+", kus)][6:]
M73 = {}
j = 0
while j + 6 < len(chi) and len(M73) < 12:
    if chi[j] == len(M73) + 1: M73[chi[j]] = chi[j + 1:j + 7]; j += 7
    else: j += 1
assert all(M73[r] == [r + 12 * c for c in range(6)] for r in range(1, 13)), M73
s_il_t = T.odna(r"output is a vector ordered as \[1,13,25,37,49,61")[0]
s_il_r = T.odna(r"output is a vector ordered as \[1,7,13,19,25,31")[0]
TX = [M73[r][c] - 1 for r in range(1, 13) for c in range(6)]      # передаётся строками таблицы
deil = [0] * 72
for k in range(72): deil[k] = k            # вход приёмника — принятый порядок
rx = [TX[(k % 12) * 6 + k // 12] for k in range(72)]               # dsd-fme: запись строками по 6, чтение столбцами
assert rx == list(range(72))
assert "Matrix[12][6]" in tx(dv)
kus, s_il2 = T.kusok(r"Table 7\.4: MI and HI field Interleaving matrix", r"NOTE:", posl=True)
s_opech = T.odna(r"output is a vector ordered as \[1,11,22,33,44,55")[0]      # опечатка стандарта: при 10 столбцах чтение столбцами даёт 1,11,21,31…
ch2 = [int(x) for x in re.findall(r"\d+", kus)][10:]
M74 = {}; j = 0
while j + 10 < len(ch2) and len(M74) < 12:
    if ch2[j] == len(M74) + 1: M74[ch2[j]] = ch2[j + 1:j + 11]; j += 11
    else: j += 1
assert len(M74) == 12 and all(M74[r] == [r + 12 * c for c in range(10)] for r in range(1, 13)), M74
# синхрослова
FS = {}
for n in range(1, 5):
    m = re.search(r"6\.1\.%d\s*\nFS%d\s*\n.*?Hex:\s*\n?\s*((?:[0-9A-F]{2} )+[0-9A-F]{2})16" % (n, n), T.ves, re.S)
    FS[n] = (m.group(1).replace(" ", ""), T.stranica_pozicii(m.start()))
sym = lambda hx: "".join({"01": "1", "11": "3", "00": "?", "10": "?"}[format(int(hx, 16), f"0{4 * len(hx)}b")[2 * k:2 * k + 2]] for k in range(2 * len(hx)))
th = tx(dh)
for n in range(1, 5):
    m = re.search(r'#define DPMR_FRAME_SYNC_%d\s+"(\d+)"' % n, th)
    assert sym(FS[n][0]) == m.group(1), (n, sym(FS[n][0]), m.group(1))
assert int(FS[4][0], 16) ^ int(FS[1][0], 16) == int("AA" * 6, 16)                      # FS4 — посимвольное дополнение FS1
# канальные коды CC 0…63
kus, s_cc = T.kusok(r"6\.1\.5\.2\.2", r"\n6\.1\.6\s*\n", posl=True)
CC = {}; CC_hex = {}
for a, b, h in re.findall(r"(?m)^\s*(\d{1,2})\s*\n\s*((?:[01]{4} ){5}[01]{4})2\s*\n\s*([0-9A-F ]+?)16", kus):
    CC[int(a)] = int(b.replace(" ", ""), 2); CC_hex[int(a)] = h.replace(" ", "")
# шестнадцатеричная строка — сверка с двоичной (у № 54 в текстовом слое «F5 D D5F»: пробел внутри октета — сверяем без пробелов)
assert all(int(CC_hex[k], 16) == v for k, v in CC.items())
assert sorted(CC) == list(range(64)), sorted(set(range(64)) - set(CC))
dcc = {int(a, 16): int(b) for a, b in re.findall(r"case (0x[0-9A-F]{6}): \{ColorCode =\s*(\d+);", tx(dd))}
assert all(dcc[v] == k for k, v in CC.items()), [k for k, v in CC.items() if dcc.get(v) != k]
assert all((v & 0x555555) == 0x555555 for v in CC.values())                          # младшие биты всех дибитов = 1 (символы ±3)
tablica("dpmr_102658", {"Hamming_12_8_G": G, "TCH_perem_t7.3": [M73[r] for r in range(1, 13)], "MI_HI_perem_t7.4": [M74[r] for r in range(1, 13)],
                        "FS": {f"FS{n}": v[0] for n, v in FS.items()}, "CC": {k: format(v, "06X") for k, v in sorted(CC.items())}},
        "dPMR: порождающая матрица Хэмминга (12,8), матрицы перемежения, синхрослова, 64 канальных кода",
        [T.ist(s_h, "Table 7.2"), T.ist(s_il, "Table 7.3"), T.ist(s_il2, "Table 7.4"), T.ist(FS[1][1], "6.1.1–6.1.4"), T.ist(s_cc, "6.1.5.2.2"), ist_kod(fec, "Hamming_12_8_m_G"), ist_kod(dd, "GetdPmrColorCode")],
        "G: строки = x^(11−r) mod (x^4+x+1), = Hamming_12_8_m_G dsd-fme, d_min = 3; матрицы 7.3/7.4 = формула r + 12c; FS1–FS4 = DPMR_FRAME_SYNC_1…4 dsd-fme (01→+3, 11→−3); все 64 CC = GetdPmrColorCode dsd-fme")

def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "dPMR (ETSI TS 102 658 / 102 490)", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
NET = "нет в проекте dPMR; простая: CRC — crc_katalog, Хэмминг (12,8) — таблица, перемежение — формула, синхрослова — sinhro.py"
rec("dPMR CCH: Хэмминг (12,8) ×6 + CRC7 + перемежение 6×12 + скремблер", "каскадный: укороченный Хэмминг (12,8) + CRC",
    {"CRC7": "X7+X3+1 по кадру CCH (48 бит данных → 41+7)", "Хэмминг": "(12,8,3), g(x) = X4+X+1, систематический, G — tablicy/dpmr_102658.json", "блок": "6 × (12,8) = 72 бита",
     "перемежение": "72 бита: запись столбцами в матрицу 12×6, чтение строками (передатчик: 1,13,25,37,49,61,2,…)", "скремблер": "X9+X5+1, начальное все «1», на 72 бита CCH и полезную нагрузку"},
    "dPMR голосовые суперкадры (CCH каждого кадра)", [T.ist(s_crc, "Table 7.1"), T.ist(s_h, "7.2"), T.ist(s_il, "7.4 TCH"), T.ist(s_scr, "7.3"), T4.ist(s_h2, "dPMR446: тот же код")],
    "CRC7 = CRC7BitdPMR (0x09, начальное 0), Хэмминг = fec.c, перемежение = DeInterleave6x12DPmrBit, скремблер = ScrambledPMRBit (dsd-fme); текст TS 102 490 — те же многочлены", NET)
rec("dPMR заголовки MI/HI: CRC8 + Хэмминг (12,8) ×10 + перемежение 10×12", "каскадный: укороченный Хэмминг (12,8) + CRC8",
    {"CRC8": "X8+X2+X+1, начальное все «1» (у dsd-fme 0xFF)", "перемежение": "120 бит: матрица 12×10 (табл. 7.4), запись столбцами, чтение строками",
     "опечатка стандарта": "7.4 для MI/HI: «выход деперемежителя [1,11,22,33,44,55…]» и «вектор длины 72» — по таблице 12×10 верно [1,11,21,31,…] и длина 120"},
    "dPMR заголовки (Header 1/2) и сообщения", [T.ist(s_crc, "Table 7.1"), T.ist(s_il2, "Table 7.4"), T.ist(s_opech, "опечатка в примере деперемежителя")], "CRC8 = CRC8BitdPMR dsd-fme; табл. 7.4 = формула r+12c (пример в тексте с опечаткой)", NET)
rec("dPMR синхрослова FS1–FS4 и канальные коды CC", "синхрослово",
    {"FS1": FS[1][0], "FS2": FS[2][0], "FS3": FS[3][0], "FS4": FS[4][0] + " (посимвольное дополнение FS1)", "CC": "64 кода по 24 бита — tablicy/dpmr_102658.json; все символы ±3",
     "преамбула": "5F повторяется ≥ 72 бита", "модуляция": "4FSK 2400 симв/с (4800 бит/с), 6,25 кГц"},
    "поиск кадров dPMR, определение канального кода", [T.ist(FS[1][1], "6.1.1–6.1.4"), T.ist(s_cc, "6.1.5.2.2")], "= DPMR_FRAME_SYNC_1…4 и GetdPmrColorCode dsd-fme", NET)

if __name__ == "__main__":
    print(len(ZAPISI), "записей", FS, len(CC), DMIN)
