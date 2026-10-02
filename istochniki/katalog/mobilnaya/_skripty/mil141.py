"""MIL-STD-188-141B/C (ALE): 2G — Голей (24,12) + перемежение + тройной повтор; 3G (прил. C 141B = STANAG 4538) —
BW0…BW4: свёрточные K=7/8/9, Уолш, PN-расширение, CRC-32/16/12/8/4. Сверка Голея с LinuxALE (GPL-2.0)."""
import json, re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/mil/MIL-STD-188-141B.pdf")
TC = Tekst("istochniki/mil/MIL-STD-188_141C_CHG-1.055407.pdf")
TF = Tekst("istochniki/mil/FED-STD-1045A.pdf")
ZAPISI = []
def s_(TT, pat, i=0): return [s for s, n, l in TT.naiti(pat)][i]
# Голей: g(x) и рис. A-6
s_g = s_(T, r"g\(x\) = x11 \+ x9 \+ x7 \+ x6 \+ x5 \+ x \+ 1")
gst = [11, 9, 7, 6, 5, 1, 0]; g = mnogochlen(gst)
def pmod(a, b):
    while a.bit_length() >= b.bit_length(): a ^= b << (a.bit_length() - b.bit_length())
    return a
assert pmod((1 << 23) | 1, g) == 0                                   # g(x) делит x^23 + 1 → циклический (23,12)
kus, s_a6 = T.kusok(r"A\.5\.2\.2\.2\.2", r"FIGURE A-6\.\s+Generator matrix for \(24, 12\) extended Golay code\.", posl=True)
tri = re.findall(r"(?m)^\s*([01]{3})\s*$", kus)
assert len(tri) == 96, len(tri)
rows = ["".join(tri[8 * r:8 * r + 8]) for r in range(12)]
for r, w in enumerate(rows): assert w[:12] == format(1 << (11 - r), "012b"), (r, w)
P = [int(w[12:], 2) for w in rows]
kod_slova = lambda u: (u << 12) | __import__("functools").reduce(lambda a, i: a ^ (P[i] if (u >> (11 - i)) & 1 else 0), range(12), 0)
w = [bin(kod_slova(u)).count("1") for u in range(1, 4096)]
assert min(w) == 8 and all(x % 4 == 0 for x in w)                    # расширенный Голей: d=8, вес кратен 4
# расширенный код из циклического g(x) — тот же спектр весов (проверка, что рис. A-6 — это Голей по g(x))
def cyc_ext(u):
    c = 0
    for i in range(12):
        if (u >> i) & 1: c ^= g << i
    return (c << 1) | (bin(c).count("1") & 1)
from collections import Counter
assert Counter(bin(cyc_ext(u)).count("1") for u in range(4096)) == Counter([0] + w)
lg = kod("LinuxALE", "golay.c")
enc = c_massiv(lg, "encode_table"); assert len(enc) == 4096
assert all(enc[u] == (kod_slova(u) & 0xFFF) for u in range(4096))
# 141C и FED-STD-1045A — тот же многочлен
assert TC.naiti(r"g\(x\) = x11 \+ x9 \+ x7 \+ x6 \+ x5 \+ x \+ 1") and TF.naiti(r"x11 \+ x9 \+ x7 \+ x6 \+ x5 \+ x \+ 1|X11 \+ X9 \+ X7 \+ X6 \+ X5 \+ X \+ 1")
s_il = s_(T, r"^A\.5\.2\.2\.3\s+Interleaving and deinterleaving\.$", -1)
s_crc = s_(T, r"^A\.5\.6\.1\s+CRC\.$", -1)
# 3G: многочлены BW0…BW3, CRC
def okt(v): st = {0 if t == "1" else 1 if t.lower() in ("x", "x1") else int(t[1:]) for t in re.findall(r"[xX]\d*|\b1\b", v)}; return format(sum(1 << s for s in st), "o")
BW = {}
for s, n, l in T.naiti(r"^\s*(b[01]|Bitout[0-3]):?\s*=?\s*[xX]\d"):
    m = re.match(r"\(?(b[01]|Bitout[0-3])\)?:?\s*=?\s*([xX\d +]+)", l.strip()); BW.setdefault(s, []).append((m.group(1), m.group(2).strip(" ,"), okt(m.group(2))))
for s, n, l in T.naiti(r"^\s*\d\.\s+Bitout\d:\s+X"):
    m = re.match(r"\d\.\s+(Bitout\d):\s+([X\d +]+)", l.strip()); BW.setdefault(s, []).append((m.group(1), m.group(2).strip(" ,"), okt(m.group(2))))
k3 = {s: v for s, v in BW.items()}
okts = {s: [x[2] for x in v] for s, v in k3.items()}
assert okts[298] == ["133", "171"] and okts[304] == ["711", "663", "557"] and okts[311] == ["235", "275", "313", "357"] and okts.get(316, []) + okts.get(317, []) == ["133", "171"], okts
crc32_s = s_(T, r"X32 \+ X26 \+ X23 \+ X22 \+ X16 \+ X12 \+ X11 \+ X10 \+ X8 \+ X7 \+ X5 \+ X4 \+ X2 \+ X1 \+ 1")
assert mnogochlen([32, 26, 23, 22, 16, 12, 11, 10, 8, 7, 5, 4, 2, 1, 0]) == 0x104C11DB7
s_c48 = s_(T, r"x4 \+ x3 \+ x \+ 1"); s_c12 = s_(T, r"X12 \+ X11 \+ X9 \+ X8 \+ X7 \+ X6 \+ X3 \+ X2 \+ X1 \+ 1"); s_c16 = s_(T, r"X16 \+ X15 \+ X11 \+ X8 \+ X6 \+ X5 \+ X4 \+ X3 \+ X1 \+ 1")
def pn(t):
    m = list(re.finditer(t, T.ves))[-1]; kus = T.ves[m.end():m.end() + 40000]; out = []
    for line in kus.split("\n"):
        if re.search(r"[A-Za-z]", line) and not re.search(r"MIL-STD|APPENDIX|СТР|Downloaded", line):
            if out: break
            continue
        if "," in line: out += [int(x) for x in re.findall(r"[0-7]", line)]
    return out, T.stranica_pozicii(m.start())
PN = {k: pn(t) for k, t in {"BW0": r"TABLE C-IX\.\s+BW0 PN spreading sequence\.", "BW1": r"TABLE C-XIII\.\s+BW1 PN spreading sequence\.",
                              "BW4": r"TABLE C-XIX\.\s+BW4 PN spreading sequence\."}.items()}
assert len(PN["BW0"][0]) == 256 and len(PN["BW1"][0]) == 256 and len(PN["BW4"][0]) == 1280
# BW0 PN начинается теми же 64 трибитами, что и рандомизатор данных 110 (рис. 6 110C: 0xBAD, x12+x6+x4+x+1, 8 сдвигов)
rnd = json.load(open(os.path.join(KOREN, "tablicy/mil_188_110.json")))["данные"]["randomizator_160"]
sovp64 = PN["BW0"][0][:64] == rnd[:64]
assert sovp64
tablica("mil_188_141", {"golay_g": D_zapis(gst), "golay_P_A-6": [format(p, "012b") for p in P], "3G_mnogochleny": {str(s): v for s, v in k3.items()},
    "CRC": {"FCS16_ALE": "x16+x12+x5+1 (FED-STD-1003)", "3G_CRC32": "0x04C11DB7", "3G_CRC4": "x4+x3+x+1", "3G_CRC8": "x8+x7+x4+x3+x+1",
            "3G_CRC12_TM": "x12+x11+x9+x8+x7+x6+x3+x2+x+1", "3G_CRC16": "x16+x15+x11+x8+x6+x5+x4+x3+x+1"},
    "PN_BW0": PN["BW0"][0], "PN_BW1": PN["BW1"][0], "PN_BW4": PN["BW4"][0]},
    "MIL-STD-188-141B: Голей ALE (матрица рис. A-6), многочлены 3G BW0–BW3, CRC, PN-последовательности BW0/BW1/BW4",
    [T.ist(s_g, "A.5.2.2.2"), T.ist(s_a6, "рис. A-6"), T.ist(298, "C.5.1.3.3 BW0"), T.ist(304, "BW1"), T.ist(311, "BW2"), T.ist(316, "BW3"),
     T.ist(PN["BW0"][1], "табл. C-IX"), ist_kod(lg, "encode_table")],
    "матрица рис. A-6 разобрана (12 строк, единичная часть проверена), код имеет d=8 и веса кратны 4; спектр весов совпал с расширенным циклическим кодом по g(x) "
    "(g(x) делит x^23+1); все 4096 проверочных частей совпали с encode_table LinuxALE; тот же g(x) в 141C и FED-STD-1045A; "
    "многочлены 3G: BW0/BW3 = 133/171, BW1 = 711/663/557 (K=9, как у 1/3 UMTS), BW2 = 235/275/313/357 (K=8); CRC-32 3G = 0x04C11DB7; "
    f"первые 64 трибита PN BW0 совпали с рандомизатором данных MIL-STD-188-110 (независимое подтверждение прочтения рис. 6 110C)")
def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "MIL-STD-188-141 (ALE)", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
rec("ALE 2G: Голей (24,12) + перемежение + тройной повтор", "Голей (расширенный) + повторение",
    {"g(x)": "x11 + x9 + x7 + x6 + x5 + x + 1", "G": "[I12 | P], P — рис. A-6 → tablicy/mil_188_141.json", "слово": "24 бита W1…W24 → два слова Голея (A: W1–W12, B: W13–W24), биты Голея G13–G24 инвертируются",
     "перемежение": "A1, B1, A2, B2, … A24, B24 + стаффинг-бит S49 = 0 (49 бит)", "повтор": "слово ×3, мажоритарное 2 из 3 по битам", "модуляция": "8-FSK 750…2500 Гц, 125 симв/с, 375 бит/с",
     "CRC сообщений DTM/DBM": "FCS-16 x16+x12+x5+1 (FED-STD-1003)"},
    "HF ALE 2G (MIL-STD-188-141 прил. A, FED-STD-1045A, STANAG 4538 прил.), любительская ALE", [T.ist(s_g, "A.5.2.2.2"), T.ist(s_a6, "рис. A-6"), T.ist(s_il, "A.5.2.2.3–4"), T.ist(s_crc, "A.5.6.1"), ist_kod(lg, "encode_table")],
    "матрица и многочлен совпали; 4096 слов = LinuxALE encode_table", "Голей (24,12) есть в проекте (dmr.py/ysf.py: Golay24128 MMDVMHost) — другая систематическая форма; ALE-перемежения и 8-FSK — нет")
rec("ALE 3G (STANAG 4538): BW0 — Уолш + свёрточный K=7", "свёрточный K=7 1/2 + Уолш-модуляция + PN-расширение",
    {"код": "133/171 (b0 = x6+x4+x3+x+1, b1 = x6+x5+x4+x3+1), без хвоста/с хвостом по C.5.1.3.3", "перемежитель": "табл. C-VII", "Уолш": "табл. C-VIII (дибиты → 16 трибит)", "PN": "256 трибит табл. C-IX → tablicy"},
    "3G ALE: вызов/подтверждение (LE_Call)", [T.ist(298, "C.5.1.3.3"), T.ist(PN["BW0"][1], "табл. C-IX")], "многочлены из текста = 133/171", "витерби K=7 есть; Уолш/PN — нет")
rec("ALE 3G BW1 — свёрточный K=9 1/3 + Уолш", "свёрточный K=9 1/3", {"код": "x8+x7+x6+x3+1 (711), x8+x7+x5+x4+x+1 (663), x8+x6+x5+x3+x2+x+1 (557) — те же, что UMTS 1/3", "перемежитель": "табл. C-XII", "PN": "табл. C-XIII (256)"},
    "3G ALE: управление трафиком (TM)", [T.ist(304, "C.5.1.4.3")], "восьм. запись совпала с UMTS 25.212 G0/G1/G2 (557/663/711)", "витерби K=9 есть")
rec("ALE 3G BW2 — свёрточный K=8 1/4 + CRC-32 (HDL)", "свёрточный K=8 1/4 + CRC-32",
    {"код": "x7+x4+x3+x2+1 (235), x7+x5+x4+x3+x2+1 (275), x7+x6+x3+x+1 (313), x7+x6+x5+x3+x2+x+1 (357)", "CRC": "32 бита, X32+X26+…+1 = 0x04C11DB7 на пакет"},
    "3G HDL (высокоскоростной канал данных)", [T.ist(311, "C.5.1.5"), T.ist(crc32_s, "CRC-32")], "многочлены из текста", "витерби для K=8 1/4 — есть (общий); нет разметки HDL")
rec("ALE 3G BW3 — свёрточный K=7 1/2 + CRC-32 (LDL)", "свёрточный K=7 1/2 + CRC-32", {"код": "133/171", "перемежитель": "табл. C-XVI", "PN": "табл. C-XVII"},
    "3G LDL (низкоскоростной канал данных)", [T.ist(316, "C.5.1.6")], "многочлены = 133/171", "есть витерби")
rec("ALE 3G BW4 — Уолш (ACK)", "Уолш-модуляция + PN", {"PN": "1280 трибит табл. C-XIX → tablicy", "Уолш": "табл. C-XVIII"}, "3G подтверждения HDL/LDL",
    [T.ist(PN["BW4"][1], "табл. C-XIX")], "таблица разобрана (1280 значений 0–7)", "нет")
rec("ALE 3G CRC-4/8/12/16/32", "CRC-подобный", {"CRC-4": "x4+x3+x+1", "CRC-8": "x8+x7+x4+x3+x+1", "CRC-12": "x12+x11+x9+x8+x7+x6+x3+x2+x+1 (TM PDU)", "CRC-16": "x16+x15+x11+x8+x6+x5+x4+x3+x+1", "CRC-32": "0x04C11DB7"},
    "3G ALE PDU (C.4.12), TM, HDL/LDL", [T.ist(s_c48, "CRC-4/8"), T.ist(s_c12, "CRC-12"), T.ist(s_c16, "CRC-16"), T.ist(crc32_s, "CRC-32")], "многочлены из текста",
    "CRC-32 и CRC-8 разных видов есть в crc_katalog.py; эти 4/8/12/16 — добавить (поиск CRC вслепую их найдёт по многочлену)")
if __name__ == "__main__":
    print(len(ZAPISI), "записей", okts)
