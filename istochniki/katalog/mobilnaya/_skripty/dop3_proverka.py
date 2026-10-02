"""Дополнения третьего прохода проверки полноты области «mobilnaya».

Найдено пропущенным и добавлено (каждое значение снято программно с первоисточника и проверено assert-ами):
  * M17 (открытый стандарт цифровой речи/данных, спецификация M17_spec, GPL-2) — сверка со второй реализацией libm17 и её тестовыми векторами;
  * FEC речевых кадров AMBE+2 3600x2450 (DMR, NXDN, YSF, P25 фаза 2) и AMBE 3600x2400 (D-STAR): Голей + маскирующая ПСП +
    перемежение 72 бит — три независимые открытые реализации (MMDVMHost GPL-2, dsd ISC, op25 GPL-3) сверены между собой;
  * TFTS (наземная авиационная телефонная связь, ETS 300 326-2): Голей (24,12) + перемежитель 8×24 + ПСП 1+X+X4+X6+X12;
  * CDPD (сотовые пакетные данные AMPS): РС (63,47) над GF(64) — по патенту US 6,694,146 (спецификация CDPD Forum 1.1 не открыта)."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})

def pmod(a, g):
    dg = g.bit_length() - 1
    while a and a.bit_length() - 1 >= dg:
        a ^= g << (a.bit_length() - 1 - dg)
    return a
def pmulmod(a, b, g):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if a.bit_length() == g.bit_length(): a ^= g
    return r
def ppow(x, e, g):
    r = 1
    while e:
        if e & 1: r = pmulmod(r, x, g)
        x = pmulmod(x, x, g); e >>= 1
    return r
def prost_deliteli(n):
    d, r = 2, set()
    while d * d <= n:
        while n % d == 0: r.add(d); n //= d
        d += 1
    if n > 1: r.add(n)
    return r
def primitiven(g):
    """Многочлен g степени m примитивен: x^(2^m−1) = 1 и x^((2^m−1)/p) ≠ 1 для простых p | 2^m−1."""
    m = g.bit_length() - 1; n = (1 << m) - 1
    return ppow(2, n, g) == 1 and all(ppow(2, n // p, g) != 1 for p in prost_deliteli(n))
def stepeni(s):
    """'x^{16} + x^{14} + … + 1' / 'x16 + x12 + x5 + 1' / '1 + D^3 + D^4' → множество степеней."""
    r = set()
    for t in re.split(r"\+", s.replace(" ", "")):
        t = t.strip("$")
        if t in ("1",): r.add(0); continue
        m = re.fullmatch(r"[xXD](?:\^?\{?(\d+)\}?)?", t)
        assert m, (s, t)
        r.add(int(m.group(1)) if m.group(1) else 1)
    return r
def golay_sist(d, g=0xC75):
    """Систематическое слово (23,12): d·x^11 + (d·x^11 mod g)."""
    return (d << 11) | pmod(d << 11, g)
def chet(v): return bin(v).count("1") & 1
def c_tabl(put, imya):
    t = open(os.path.join(KOREN, put), errors="replace").read()
    m = re.search(r"\b" + re.escape(imya) + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", t, re.S)
    assert m, (put, imya)
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", m.group(1), flags=re.S)
    return [int(x.rstrip("uU"), 0) for x in re.findall(r"0[xX][0-9a-fA-F]+[uU]?|\d+[uU]?", body)]
def stroka(put, pat): return stroka_koda(put, pat)

# ======================================================================================================================
# M17
# ======================================================================================================================
SP = kod("M17_spec", "M17_spec.tex")                      # LICENSE (GPL-2) копируется вместе
LM = {f: kod("libm17", f) for f in ("payload/crc.c", "math/golay.c", "encode/convol.c", "phy/interleave.c", "phy/randomize.c",
                                    "phy/sync.c", "unit_tests/unit_tests.c")}
T = open(os.path.join(KOREN, SP)).read()
assert "GNU GENERAL PUBLIC LICENSE" in open(os.path.join(KOREN, "istochniki/kod/M17_spec/LICENSE")).read()
ver = re.findall(r"\\vhEntry\{([\d.]+)\}\{([^}]*)\}", T)[-1]

# CRC
m = re.search(r"polynomial \$([^$]+)\$ or \\texttt\{0\$\\times\$([0-9A-F]+)\} and initial value of \\texttt\{0\$\\times\$([0-9A-F]+)\}", T)
crc_poly = sum(1 << s for s in stepeni(m.group(1)))
assert crc_poly == 0x10000 | int(m.group(2), 16) == 0x15935 and int(m.group(3), 16) == 0xFFFF
assert "neither the input nor the output of the CRC algorithm gets reflected" in T
def crc_m17(b):
    r = 0xFFFF
    for x in b:
        r ^= x << 8
        for _ in range(8):
            r = ((r << 1) ^ 0x5935) & 0xFFFF if r & 0x8000 else (r << 1) & 0xFFFF
    return r
tv = dict(re.findall(r"\n\s*(\(empty string\)|ASCII string ``[^']*''|Bytes \\texttt\{0x00\} to \\texttt\{0xFF\}) & \\texttt\{0x([0-9A-F]{4})\}", T))
vh = {"(empty string)": b"", "ASCII string ``A''": b"A", "ASCII string ``123456789''": b"123456789", r"Bytes \texttt{0x00} to \texttt{0xFF}": bytes(range(256))}
assert len(tv) == 4 and all(crc_m17(vh[k]) == int(v, 16) for k, v in tv.items()), tv
ut = open(os.path.join(KOREN, LM["unit_tests/unit_tests.c"])).read()
assert "TEST_ASSERT_EQUAL_UINT16(0x772B, CRC_M17((uint8_t*)\"123456789\", 9));" in ut
d = bytes(range(256)); c = crc_m17(d); assert crc_m17(d + bytes([c >> 8, c & 0xFF])) == 0      # «CRC по всему LSF с CRC = 0»
assert "const uint16_t M17_CRC_POLY = 0x5935;" in open(os.path.join(KOREN, LM["payload/crc.c"])).read()

# свёрточный код
g1 = stepeni(re.search(r"G_1\(D\) =& ([^\\]+)\\\\", T).group(1)); g2 = stepeni(re.search(r"G_2\(D\) =& ([^\n]+)", T).group(1))
assert g1 == {0, 3, 4} and g2 == {0, 1, 2, 4}
cv = open(os.path.join(KOREN, LM["encode/convol.c"])).read()
mg1 = re.search(r"uint8_t G1=\(([^)]*)\)&1;", cv).group(1); mg2 = re.search(r"uint8_t G2=\(([^)]*)\)&1;", cv).group(1)
# ud[i+4] — текущий бит (D^0), ud[i+4−k] — задержка D^k
assert {4 - int(k) for k in re.findall(r"ud\[i\+(\d)\]", mg1)} == g1 and {4 - int(k) for k in re.findall(r"ud\[i\+(\d)\]", mg2)} == g2
G1i = sum(1 << (4 - s) for s in g1); G2i = sum(1 << (4 - s) for s in g2)       # старший разряд — текущий вход
df = dfree([G1i, G2i], 5); assert df == 7

# Голей (24,12)
gx = stepeni(re.search(r"\$g\(x\) = ([^$]+)\$", T).group(1)); g_m = sum(1 << s for s in gx); assert g_m == 0xC75 and "0xC75" in T
blk = re.search(r"G = \[I_\{12\} \\mid P\].*?\\begin\{matrix\}(.*?)\\end\{matrix\}", T, re.S).group(1)
P_tex = [int("".join(re.findall(r"[01]", r)), 2) for r in blk.split("\\\\") if re.search(r"[01]", r)]
assert len(P_tex) == 12
P_kod = [((golay_sist(1 << (11 - i)) & 0x7FF) << 1) | chet(golay_sist(1 << (11 - i))) for i in range(12)]
enc_lib = c_tabl(LM["math/golay.c"], "encode_matrix")
# libm17: encode_matrix[i] — строка для бита данных i (младший первым)
assert P_tex == P_kod == enc_lib[::-1], ([hex(x) for x in P_tex], [hex(x) for x in P_kod], [hex(x) for x in enc_lib])
def golay24(dd): c = golay_sist(dd); return (c << 1) | chet(c)
assert golay24(0x0D78) == 0x0D7880F and "TEST_ASSERT_EQUAL(0x0D7880FU, golay24_encode(data));" in ut
assert min(bin(golay24(dd)).count("1") for dd in range(1, 4096)) == 8

# выкалывание
P1 = [int(x) for x in re.findall(r"[01]", re.search(r"P1 = \[(.*?)\]", T, re.S).group(1))]
P2 = [int(x) for x in re.findall(r"[01]", re.search(r"P2 = \[(.*?)\]", T, re.S).group(1))]
P3 = [int(x) for x in re.findall(r"[01]", re.search(r"P3 = \[(.*?)\]", T, re.S).group(1))]
assert (len(P1), sum(P1), len(P2), sum(P2), len(P3), sum(P3)) == (61, 46, 12, 11, 8, 7)
assert P1 == [1] + [1, 0, 1, 1] * 15                                              # «1, затем M = [1 0 1 1] 15 раз»
assert P1 == c_tabl(LM["encode/convol.c"], "puncture_pattern_1") and P2 == c_tabl(LM["encode/convol.c"], "puncture_pattern_2") \
    and P3 == c_tabl(LM["encode/convol.c"], "puncture_pattern_3")
def ostalos(n, P): return sum(P[i % len(P)] for i in range(n))
assert ostalos(2 * (240 + 4), P1) == 368 and ostalos(2 * (144 + 4), P2) == 272 and ostalos(2 * (206 + 4), P3) == 368
assert "201 bits total are encoded resulting in 402 Type 2 bits" in T and "These bits are $P_2$ punctured to generate 368 Type 3 bits." in T
BERT_P2 = ostalos(2 * (197 + 4), P2); assert BERT_P2 == 369                     # расхождение текста: 402 бита через P2 дают 369, не 368
assert 96 + 272 == 368 and 16 + 368 == 384 and 384 / 9600 == 0.04

# перемежитель QPP
assert "$\\pi(x)=(45x+92x^2)\\mod 368$" in T
qpp = [(45 * x + 92 * x * x) % 368 for x in range(368)]; assert sorted(qpp) == list(range(368))
tab = {}
for a, b in re.findall(r"(?m)^\s*(\d+) & (\d+)(?= &| \\\\)", T): pass
for strok in re.findall(r"(?m)^\s*((?:\d+ & \d+ & ){3}\d+ & \d+) \\\\", T[T.index("\\chapter{Interleaving}"):T.index("\\chapter{BERT Details}")]):
    ch = [int(x) for x in strok.split("&")]
    for k in range(0, 8, 2): tab[ch[k]] = ch[k + 1]
assert len(tab) == 368 and all(tab[x] == qpp[x] for x in range(368))
assert c_tabl(LM["phy/interleave.c"], "intrl_seq") == qpp

# рандомизатор
rnd_t = {}
for a, b, c_, d_ in re.findall(r"(\d\d) & \\texttt\{0x([0-9A-F]{2})\} & (\d\d) & \\texttt\{0x([0-9A-F]{2})\}", T):
    rnd_t[int(a)] = int(b, 16); rnd_t[int(c_)] = int(d_, 16)
RND = [rnd_t[i] for i in range(46)]
assert RND == c_tabl(LM["phy/randomize.c"], "rand_seq")

# синхрослова и символы
sim = {"01": +3, "00": +1, "10": -1, "11": -3}
assert "byte \\texttt{0xB4} (\\texttt{10 11 01 00}) would be sent as the symbols (-1, -3, +3, +1)" in T
assert [sim[f"{0xB4:08b}"[i:i + 2]] for i in range(0, 8, 2)] == [-1, -3, +3, +1]
SYNC = {}
for imya_, slovo, simv in re.findall(r"(LSF|BERT|Stream|Packet) & (?:\+3, -3|-3, \+3|None) & \\texttt\{0x([0-9A-F]{4})\} & ([-+3, ]+) \\\\", T):
    SYNC[imya_] = int(slovo, 16)
    b = f"{int(slovo, 16):016b}"
    assert [sim[b[i:i + 2]] for i in range(0, 16, 2)] == [int(x) for x in simv.replace(" ", "").split(",")], imya_
sc = open(os.path.join(KOREN, LM["phy/sync.c"])).read()
assert SYNC == {"LSF": 0x55F7, "BERT": 0xDF55, "Stream": 0xFF5D, "Packet": 0x75FF}
assert all(f"0x{v:04X};" in sc for v in SYNC.values()) and "EOT_MRKR = 0x555D;" in sc and "repeating \\texttt{0x555D}" in T

# скремблер (шифрование тип 01) и ПСП BERT
skr = re.findall(r"\$(\d\d)_2\$ & \$([^$]+)\$ & (\d+) bits &\s*([\d,]+)", T)
assert len(skr) == 3
for _, mn, dl_, per in skr:
    g = sum(1 << s for s in stepeni(mn)); assert g.bit_length() - 1 == int(dl_) and (1 << int(dl_)) - 1 == int(per.replace(",", "")) and primitiven(g), mn
assert "PRBS9 polynomial: $x^{9}+x^{5}+1$" in T and primitiven((1 << 9) | (1 << 5) | 1)

tm17 = tablica("m17_spec", {"перемежитель_QPP_368": qpp, "рандомизатор_46_байт": RND, "P1": P1, "P2": P2, "P3": P3,
                            "Голей_P_строки_12бит": P_tex, "синхрослова": {k: f"0x{v:04X}" for k, v in SYNC.items()}, "EoT": "0x555D"},
               "M17: перемежитель π(x) = (45x + 92x²) mod 368, рандомизатор 368 бит, выкалывание P1/P2/P3, матрица P кода Голея (24,12), синхрослова",
               [{"файл": SP, "строки": f"{stroka(SP, r'chapter{Interleaving}')}–{stroka(SP, r'chapter{BERT Details}')}"},
                {"файл": SP, "строки": str(stroka(SP, r'chapter{Randomizer Sequence}'))}],
               "таблицы сняты с LaTeX-текста спецификации и совпали с libm17 (intrl_seq, rand_seq, puncture_pattern_1..3, encode_matrix, sync.c); "
               "таблица перемежителя совпала с формулой QPP во всех 368 позициях")

rec(f"M17 (открытый стандарт цифровой речи/данных, ред. {ver[0]}): LSF/LICH/поток/пакет — свёрточный K=5 1/2 (1+D3+D4, 1+D+D2+D4) с выкалыванием "
    "P1/P2/P3 + Голей (24,12) для LICH + QPP-перемежитель 368 + рандомизатор + CRC-16 0x5935",
    "Любительская цифровая речь: M17", "каскадный",
    {"кадр": "синхрослово 16 бит (8 символов) + 368 бит нагрузки = 384 бита = 40 мс при 9600 бит/с (4FSK 4800 Бод, дибит 01→+3, 00→+1, 10→−1, 11→−3)",
     "синхрослова": "LSF 0x55F7 (преамбула +3,−3), BERT 0xDF55 (преамбула −3,+3), поток 0xFF5D, пакет 0x75FF; конец передачи — 0x555D повторяется 40 мс",
     "свёрточный код": "K = 5, R = 1/2, G1 = 1 + D^3 + D^4, G2 = 1 + D + D^2 + D^4, выход поочерёдно G1, G2; 4 нулевых бита хвоста; d_free = 7 "
                       "(те же многочлены, что у YSF)",
     "выкалывание": "P1 (LSF): 61 элемент, 46 единиц = [1] + [1,0,1,1]×15, применяется 8 раз: 488 → 368; P2 (поток, BERT): 11 из 12 "
                    "[1×11, 0]: 296 → 272; P3 (пакет): [1,1,1,1,1,1,1,0]: 420 → 368",
     "LSF": "240 бит (DST 48, SRC 48, TYPE 16, META 112) + CRC-16 = 30 байт → +4 хвоста → 488 → P1 → 368 → перемежение → рандомизатор",
     "поток": "LICH 48 бит (40-битный кусок LSF + LICH_CNT 3 бита по модулю 6 + 5 резерв) → 4 × Голей (24,12) = 96 бит; содержимое "
              "FN 16 бит (старший бит — конец) + 128 бит (Codec 2 3200 или 1600 + 64 бита данных) → +4 → 296 → P2 → 272; 96 + 272 = 368 → перемежение",
     "пакет": "25 байт данных + 6 бит (EOF 1 + счётчик кадров/байтов 5) = 206 → +4 → 420 → P3 → 368; CRC-16 M17 по всему пакету (до 823 байт + CRC, до 33 кадров)",
     "BERT": "197 бит ПСП PRBS9 x^9 + x^5 + 1 (состояние 1) → +4 → 402 → P2 → по арифметике 369 бит, в тексте спецификации — 368 (см. «проверка»)",
     "Голей (24,12)": "g(x) = x^11 + x^10 + x^6 + x^5 + x^4 + x^2 + 1 (0xC75), систематический: данные 23..12, проверка 11..1, бит чётности 0; d = 8; "
                      "матрица P — tablicy/m17_spec.json",
     "перемежитель": "QPP π(x) = (45x + 92x²) mod 368 — таблица: tablicy/m17_spec.json",
     "рандомизатор": "XOR с фиксированной последовательностью 46 байт (368 бит) 0xD6 0xB5 0xE2 0x30 … 0xC3, старший бит первым — tablicy/m17_spec.json",
     "CRC": "CRC-16, многочлен x16+x14+x12+x11+x8+x5+x4+x2+1 (0x5935), начальное 0xFFFF, без отражения и без инверсии; CRC('123456789') = 0x772B",
     "шифрование-скремблер (тип 01)": "LFSR Фибоначчи 8/16/24 бит: x^8+x^6+x^5+x^4+1, x^16+x^15+x^13+x^4+1, x^24+x^23+x^22+x^17+1 (ключ = начальное состояние)",
     "порядок бит": "старший бит первым (байты и поля)"},
    "любительская цифровая радиосвязь УКВ/ДМВ (M17 Project: OpenRTX, модули MMDVM, ретрансляторы и шлюзы M17)",
    [{"файл": SP, "строки": str(stroka(SP, r"section\{CRC\} \\label\{crc\}")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'section{CRC}')}", "что": "CRC и тестовые векторы"},
     {"файл": SP, "строки": str(stroka(SP, r"chapter\{Convolutional Encoder\}")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'chapter{Convolutional Encoder}')}", "что": "свёрточный код"},
     {"файл": SP, "строки": str(stroka(SP, r"chapter\{Golay Encoder\}")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'chapter{Golay Encoder}')}", "что": "Голей"},
     {"файл": SP, "строки": str(stroka(SP, r"chapter\{Code Puncturing\}")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'chapter{Code Puncturing}')}", "что": "выкалывание"},
     {"файл": SP, "строки": str(stroka(SP, r"chapter\{Interleaving\}")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'chapter{Interleaving}')}", "что": "QPP"},
     {"файл": SP, "строки": str(stroka(SP, r"chapter\{Randomizer Sequence\}")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'chapter{Randomizer Sequence}')}", "что": "рандомизатор"},
     {"файл": SP, "строки": str(stroka(SP, r"Frame Specific Sync Bursts")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'Frame Specific Sync Bursts')}", "что": "синхрослова"},
     {"файл": SP, "строки": str(stroka(SP, r"201 bits total are encoded resulting in 402")), "url": manifest()[SP]["url"] + f"#L{stroka(SP, r'201 bits total are encoded resulting in 402')}", "что": "BERT"},
     ist_kod(LM["encode/convol.c"], r"uint8_t G1=", "libm17: кодер"), ist_kod(LM["math/golay.c"], r"encode_matrix\[12\]", "libm17: матрица Голея"),
     ist_kod(LM["phy/interleave.c"], r"intrl_seq", "libm17: перемежитель"), ist_kod(LM["phy/randomize.c"], r"rand_seq\[46\]", "libm17: рандомизатор"),
     ist_kod(LM["phy/sync.c"], r"SYNC_LSF", "libm17: синхрослова"), ist_kod(LM["unit_tests/unit_tests.c"], r"0x0D7880FU", "libm17: тестовый вектор Голея")],
    "CRC: 4 тестовых вектора спецификации (пустая строка → 0xFFFF, 'A' → 0x206E, '123456789' → 0x772B, байты 00…FF → 0x1C31) воспроизведены своим "
    "кодом, CRC по сообщению с дописанным CRC = 0; Голей: матрица P из текста = вычисленная из g(x) = encode_matrix libm17, вектор libm17 "
    "0x0D78 → 0x0D7880F воспроизведён, d = 8 перебором 4095 слов; свёрточный: многочлены текста = выражения кодера libm17, d_free = 7; P1/P2/P3 "
    "совпали с libm17, длины 488→368, 296→272, 420→368 подтверждены; QPP: 368 строк таблицы = формула = intrl_seq libm17; рандомизатор 46 байт = "
    f"rand_seq; синхрослова = sync.c и символы таблицы; многочлены скремблера примитивны (периоды 255/65535/16777215). РАСХОЖДЕНИЕ в тексте: BERT "
    f"402 бита через P2 дают {BERT_P2}, а не 368 (кодер libm17 conv_encode_bert_frame пишет {BERT_P2}-й бит за границу массива out[368], т. е. "
    "фактически передаются первые 368)",
    "частично: в проекте есть свёрточный K=5 с теми же многочленами (ysf.py), Голей (24,12) 0xC75 (ysf.py), QPP (turbo.py), CRC-16/M17 "
    "(crc_katalog.py); нужно: синхрослова, выкалывание P1/P2/P3, рандомизатор 46 байт и разбор LSF/LICH — просто")

# ======================================================================================================================
# FEC речевых кадров AMBE/AMBE+2 (вокодеры DVSI): три открытые реализации
# ======================================================================================================================
MA = kod("MMDVMHost", "AMBEFEC.cpp"); MG = kod("MMDVMHost", "Golay24128.cpp")
DC = kod("dsd", "include/dmr_const.h", licenziya="COPYRIGHT"); DS = kod("dsd", "include/dstar_const.h", licenziya="COPYRIGHT")
OV = kod("op25", "op25/gr-op25_repeater/lib/p25p2_vf.cc")
assert "GNU General Public License" in open(os.path.join(KOREN, OV)).read()[:1200]
assert "ISC" in open(os.path.join(KOREN, DC)).read()[:1500]
PR_M = c_tabl(MA, "PRNG_TABLE"); PR_O = c_tabl(OV, "pr_n")
def lcg(u, n=24):
    pr, bity = 16 * u, []
    for _ in range(n):
        pr = (173 * pr + 13849) % 65536; bity.append(pr >> 15)
    return int("".join(map(str, bity)), 2)
ov = open(os.path.join(KOREN, OV)).read()
assert "pr[0] = 16 * u[0];" in ov and "pr[n] = (173*pr[n-1] + 13849) - 65536 * int((173*pr[n-1]+13849)/65536);" in ov and "_m1[n-1] = (pr[n] / 32768) & 1;" in ov
assert len(PR_M) == len(PR_O) == 4096 and PR_M == PR_O and all(PR_M[u] == lcg(u) for u in range(4096))
E23 = c_tabl(MG, "ENCODING_TABLE_23127"); E24 = c_tabl(MG, "ENCODING_TABLE_24128")
assert all(E23[u] == golay_sist(u) << 1 and E24[u] == golay24(u) for u in range(4096))     # g = 0xC75: (23,12) выровнен влево, (24,12) + чётность
ma = open(os.path.join(KOREN, MA)).read()
assert "unsigned int p = PRNG_TABLE[data] >> 1;" in ma and "b = CGolay24128::encode23127(datb) >> 1;" in ma           # AMBE+2: маска 23 бита
assert re.search(r"regenerateDStar\(unsigned int& a, unsigned int& b\) const\s*\{.*?unsigned int p = PRNG_TABLE\[data\];\s*b \^= p;", ma, re.S)
assert "int m1 = pr_n[u0] >> 1;" in ov and "c1 = golay_23_encode(u1) ^ m1;" in ov                                       # op25 фаза 2 = то же

# DMR/NXDN/YSF: таблица MMDVMHost против dsd (rW/rX/rY/rZ, ISC)
A_, B_, C_ = c_tabl(MA, "DMR_A_TABLE"), c_tabl(MA, "DMR_B_TABLE"), c_tabl(MA, "DMR_C_TABLE")
assert (len(A_), len(B_), len(C_)) == (24, 23, 25) and sorted(A_ + B_ + C_) == list(range(72))
rW, rX, rY, rZ = (c_tabl(DC, n) for n in ("rW", "rX", "rY", "rZ"))
def dsd_v_mmdvm(r, x):   # строки ambe_fr dsd: 0 — C0 (24, старший 23), 1 — C1 (23), 2 — C2 (11), 3 — C3 (14) → (поле, индекс от старшего)
    return ("a", 23 - x) if r == 0 else ("b", 22 - x) if r == 1 else ("c", 10 - x) if r == 2 else ("c", 24 - x)
mm = {p: ("a", i) for i, p in enumerate(A_)} | {p: ("b", i) for i, p in enumerate(B_)} | {p: ("c", i) for i, p in enumerate(C_)}
for j in range(36):
    assert dsd_v_mmdvm(rW[j], rX[j]) == mm[2 * j] and dsd_v_mmdvm(rY[j], rZ[j]) == mm[2 * j + 1], j
assert "if (a2Pos >= 108U)\n\t\t\ta2Pos += 48U;" in ma and "unsigned int a3Pos = a1Pos + 192U;" in ma
# P25 фаза 2 (op25 extract_vcw): своё перемежение 72 бит
vcw = re.search(r"void p25p2_vf::extract_vcw\(.*?\n\t\}", ov, re.S).group(0)
VCW = {int(i): (int(c), int(b)) for c, b, i in re.findall(r"c(\d)\[(\d+)\] = vf\[(\d+)\];", vcw)}
assert sorted(VCW) == list(range(72)) and sorted(VCW.values()) == sorted([(0, i) for i in range(24)] + [(1, i) for i in range(23)] +
                                                                          [(2, i) for i in range(11)] + [(3, i) for i in range(14)])
# D-STAR: MMDVMHost (байтовый буфер, старший бит байта первым) против op25 d_list (эфирный порядок) и op25 alt_d_list
DA, DB, DCt = c_tabl(MA, "DSTAR_A_TABLE"), c_tabl(MA, "DSTAR_B_TABLE"), c_tabl(MA, "DSTAR_C_TABLE")
assert sorted(DA + DB + DCt) == list(range(72))
d_list, alt = c_tabl(OV, "d_list"), c_tabl(OV, "alt_d_list")
efir = lambda p: (p // 8) * 8 + 7 - p % 8            # D-STAR передаёт байты младшим битом вперёд
assert [efir(p) for p in DA + DB + DCt] == d_list
inv = [0] * 72
for i, p in enumerate(alt): inv[p] = i
assert inv == DA + DB + DCt
dW, dX = c_tabl(DS, "dW"), c_tabl(DS, "dX")
st0 = sorted(((dX[p], p) for p in range(72) if dW[p] == 0), reverse=True)
assert [p for _, p in st0] == d_list[:24]                 # строка C0 dsd = первые 24 позиции op25 d_list
DSTAR_EFIR = [efir(p) for p in DA + DB + DCt]

ta = tablica("ambe_fec_peremezhenie", {
    "DMR_NXDN_YSF_AMBE2_72": {"a_C0_golay24_pozicii": A_, "b_C1_golay23_pozicii": B_, "c_C2C3_25bit_pozicii": C_,
                              "primechanie": "позиция бита в 72-битном кадре в эфирном порядке (старший бит поля первым); в пакете DMR "
                                             "3 кадра: 0–71, 72–107 + 156–191 (через 48 бит синхро/EMB), 192–263"},
    "P25_faza2_AMBE2_72": {"poziciya_v_kadre→(pole,bit)": {str(k): v for k, v in sorted(VCW.items())}},
    "DSTAR_AMBE_72": {"efir_pozicii_a_b_c_po_24": DSTAR_EFIR, "MMDVMHost_bayt_MSB": DA + DB + DCt},
    "PRNG_C1_maska_24bit": PR_M},
    "FEC речевых кадров AMBE+2/AMBE: позиции полей C0/C1/C2–C3 в 72-битных кадрах DMR/NXDN/YSF, P25 фазы 2, D-STAR и маска ПСП для C1 (4096 значений)",
    [ist_kod(MA, r"DMR_A_TABLE", "MMDVMHost"), ist_kod(DC, r"const int rW\[36\]", "dsd"), ist_kod(OV, r"void p25p2_vf::extract_vcw", "op25"),
     ist_kod(OV, r"static const int d_list", "op25 D-STAR"), ist_kod(MA, r"PRNG_TABLE\[\] =", "MMDVMHost ПСП")],
    "DMR: 72 позиции MMDVMHost = dsd rW/rX/rY/rZ; D-STAR: MMDVMHost (с учётом младшего бита первым) = op25 d_list = обратная alt_d_list = строка C0 dsd dW/dX; "
    "ПСП: PRNG_TABLE MMDVMHost = pr_n op25 = формула LCG pr(n) = (173·pr(n−1) + 13849) mod 65536, pr(0) = 16·u0 для всех 4096 u0")

rec("Речевые кадры AMBE+2 3600x2450 (DMR, NXDN, YSF V/D, P25 фаза 2): 72 бита = Голей (24,12) C0 + Голей (23,12) C1 ⊕ ПСП от C0 + 25 бит без защиты, "
    "перемежение 72 бит",
    "Вокодеры: AMBE+2 (DVSI) — FEC кадра", "Голей + маскирующая ПСП",
    {"кадр": "20 мс: 49 бит речи (C0 12 + C1 12 + C2 11 + C3 14) → 72 бита (3600 бит/с с FEC, 2450 бит/с речь)",
     "C0": "12 старших бит → Голей (24,12), g = x^11+x^10+x^6+x^5+x^4+x^2+1 (0xC75) + общая чётность; d = 8",
     "C1": "12 бит → Голей (23,12) (тот же g), затем XOR с 23-битной маской m1: pr(0) = 16·u0 (u0 — 12 бит данных C0), "
           "pr(n) = (173·pr(n−1) + 13849) mod 65536, m1 = старшие биты pr(1)…pr(23) (pr(n) div 32768), первый — старший",
     "C2, C3": "11 + 14 бит без защиты",
     "перемежение DMR/NXDN/YSF": "позиция в кадре (эфирный порядок, старший бит поля первым): C0 — 0,4,8,…,68,1,5,…,21; C1 — 25,29,…,69,2,6,…,42; "
                                 "C2C3 — 46,50,…,70,3,7,…,71 (таблица tablicy/ambe_fec_peremezhenie.json); в пакете DMR 3 кадра: 0–71, 72–107 + "
                                 "156–191 (вокруг 48 бит синхро/EMB), 192–263",
     "перемежение P25 фаза 2": "другая перестановка 72 бит (op25 extract_vcw) — в той же таблице",
     "NXDN": "кадры 72 бит в VCH, после снятия скремблера PN9 NXDN (запись «NXDN скремблер PN9»)",
     "ошибочный кадр": "MMDVMHost подставляет «тишину» a = 0xF00292, b = 0x0E0B20, c = 0 при ≥ 4 ошибок в C0 или ≥ 6 в C0+C1 при ≥ 2 в C0"},
    "цифровая речь DMR (ETSI TS 102 361 — вокодер вне стандарта), NXDN, Yaesu System Fusion (V/D), P25 фаза 2 (TDMA)",
    [ist_kod(MA, r"unsigned int CAMBEFEC::regenerateDMR\(unsigned int& a", "MMDVMHost: Голей + ПСП"),
     ist_kod(MA, r"DMR_A_TABLE", "MMDVMHost: перемежение"), ist_kod(MA, r"PRNG_TABLE\[\] =", "MMDVMHost: маски"),
     ist_kod(MG, r"ENCODING_TABLE_23127\[\]", "MMDVMHost: Голей (23,12)"),
     ist_kod(DC, r"const int rW\[36\]", "dsd (ISC): перемежение DMR"),
     ist_kod(OV, r"pr\[0\] = 16 \* u\[0\];", "op25: ПСП"), ist_kod(OV, r"void p25p2_vf::extract_vcw", "op25: перемежение фазы 2")],
    "три независимые открытые реализации сверены программно: 72 позиции перемежения MMDVMHost (GPL-2) = dsd (ISC); 4096 масок PRNG_TABLE MMDVMHost = "
    "pr_n op25 (GPL-3) = формула LCG из op25; таблицы Голея MMDVMHost = систематический код с g = 0xC75 для всех 4096 слов; кодер op25 фазы 2 "
    "(m1 = pr_n[u0] >> 1, C1 = Голей(23,12) ⊕ m1) = регенератор MMDVMHost (PRNG_TABLE >> 1). Официального описания DVSI нет (вокодер закрытый) — "
    "значения из открытого кода, полученного обратной разработкой",
    "частично: Голей (24,12)/(23,12) 0xC75 есть (ysf.py, dmr.py); нужно: маска LCG и таблицы перемежения — просто (таблицы готовы)")

rec("Речевые кадры D-STAR AMBE 3600x2400: 72 бита = Голей (24,12) C0 + Голей (24,12) C1 ⊕ 24-битная ПСП от C0 + 24 бита без защиты, перемежение 72 бит",
    "D-STAR (JARL)", "Голей + маскирующая ПСП",
    {"кадр": "20 мс: 48 бит речи (12 + 12 + 24) → 72 бита речи + 24 бита медленных данных = 96 бит",
     "C0": "Голей (24,12), g = 0xC75 + чётность", "C1": "Голей (24,12) ⊕ 24-битная маска: та же LCG (pr(0) = 16·u0, pr(n) = (173·pr(n−1)+13849) mod 65536), биты pr(1)…pr(24)",
     "C2": "24 бита без защиты",
     "перемежение": "эфирный порядок (старший бит поля первым): C0 → 7,1,11,21,31,25,35,45,55,49,59,69,6,0,10,20,30,24,34,44,54,48,58,68; C1 → 5,15,9,…; "
                    "C2 → 3,13,23,… (tablicy/ambe_fec_peremezhenie.json, «DSTAR_AMBE_72»); в байтовом буфере MMDVMHost (старший бит байта первым) — "
                    "0,6,12,…,66,1,7,…,67 / 2,8,… / 4,10,…, т. к. D-STAR передаёт байты младшим битом вперёд"},
    "радиолюбительская цифровая речь D-STAR (JARL), режим DV",
    [ist_kod(MA, r"unsigned int CAMBEFEC::regenerateDStar\(unsigned int& a", "MMDVMHost: Голей + ПСП"),
     ist_kod(MA, r"DSTAR_A_TABLE", "MMDVMHost: перемежение"), ist_kod(OV, r"static const int d_list", "op25: d_list / alt_d_list"),
     ist_kod(OV, r"void p25p2_vf::encode_dstar", "op25: кодер D-STAR"), ist_kod(DS, r"const int dW\[72\]", "dsd: dW/dX")],
    "MMDVMHost DSTAR_A/B/C с переворотом бит в байте = op25 d_list (все 72) = обратная перестановка op25 alt_d_list без переворота; строка C0 dsd dW/dX = "
    "d_list[0..23]; маска = PRNG_TABLE (24 бита) = LCG op25 для всех 4096 u0. Остальные строки dsd (помечены автором как «interleave experiments») "
    "делят C1–C3 как 23/11/14 и для D-STAR не используются",
    "частично: кадр D-STAR 96 бит есть (dstar.py), Голей 0xC75 есть; нужно: маска и перемежение AMBE — просто")

# ======================================================================================================================
# TFTS (ETS 300 326-2)
# ======================================================================================================================
TF = Tekst("istochniki/aviaciya/ets_30032602e02p.pdf")
s_g = TF.odna(r"g\(X\) = 1 \+ x2 \+ x4 \+ x5 \+ x6 \+ x10 \+ x11")[0]
assert sum(1 << s for s in stepeni(TF.odna(r"g\(X\) = 1 \+ x2")[2].split("=")[1])) == 0xC75
s_e = TF.odna(r"c\(X\) = x11 m\(x\) \+ \{x11 m\(x\) modulo g\(x\)\}")[0]
kus, s_i = TF.kusok(r"Figure 17: Output of Golay coder", r"Figure 18: Block interleaver")
chisla = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]
for ryad in ([23, 47, 71, 95, 119, 143, 167, 191], [22, 46, 70, 94, 118, 142, 166, 190], [21, 45, 69, 93, 117, 141, 165, 189], [0, 24, 48, 72, 96, 120, 144, 168]):
    assert any(chisla[i:i + 8] == ryad for i in range(len(chisla))), ryad
TF_PEREM = [b * 24 + (23 - r) for r in range(24) for b in range(8)]          # k-й передаваемый бит поля C (бит 16 слота — первый)
assert sorted(TF_PEREM) == list(range(192)) and TF_PEREM[:8] == [23, 47, 71, 95, 119, 143, 167, 191] and TF_PEREM[-8:] == [0, 24, 48, 72, 96, 120, 144, 168]
s_pn = TF.odna(r"PN\(X\) = 1 \+ X \+ X4 \+ X6 \+ X12")[0]; s_pn0 = TF.odna(r"initialized at the beginning of every transmitted frame to state octal 0115")[0]
g_pn = sum(1 << s for s in (0, 1, 4, 6, 12)); assert primitiven(g_pn)
kus_s, s_s = TF.kusok(r"Table 4\.b: Synchronization word coding", r"Table 4\.c")
sinh = "".join(re.findall(r"(?m)^([01]{11,20})$", kus_s)); assert len(sinh) == 71 and sinh.startswith("110010110011")
kus_b, s_b = TF.kusok(r"Table 3\.b: Coding of field B", r"NOTE:")
bb = re.findall(r"Slot type (G\d)\n((?:[01]\n){11})", kus_b)
POLE_B = {k: "".join(v.split()) for k, v in bb}; assert set(POLE_B) == {"G1", "G2", "G3"} and all(len(v) == 11 for v in POLE_B.values())
rasst_B = min(sum(a != b for a, b in zip(POLE_B[x], POLE_B[y])) for x, y in (("G1", "G2"), ("G1", "G3"), ("G2", "G3")))
s_g1 = TF.odna(r"mapped directly onto the G1 slot without additional")[0]; s_g3 = TF.odna(r"shall be repeated twice producing")[0]
s_s1 = TF.odna(r"The 26 bits of data defined in subclause 8\.6\.1 shall be repeated four times")[0]
s_fcs = TF.odna(r"x16 \+ x12 \+ x5 \+ 1 of the product of x16")[0]; s_mod = TF.odna(r"The channel transmission rate shall 44 200 bits/s")[0]
assert 5 * 26 + 2 == 207 - 76 + 1                                                  # S1: данные 76–207 = 5 копий по 26 + 2 запасных
tt = tablica("tfts_ets300326", {"peremezhitel_192": TF_PEREM, "sinhroslovo_S_71": sinh, "pole_B": POLE_B, "PN": "1+X+X4+X6+X12, нач. 0115 (восьм.)"},
             "TFTS: перемежитель 8 слов Голея × 24 (порядок передачи поля C слотов G2/G3), синхрослово слотов S1/S2 (71 бит), коды поля B",
             [TF.ist(s_i, "рис. 17–18"), TF.ist(s_s, "табл. 4.b"), TF.ist(s_b, "табл. 3.b")],
             "строки 1–3 и последняя рис. 18 найдены в тексте и совпали с формулой; синхрослово собрано из 4 строк табл. 4.b (71 бит = позиции 5–75)")
rec("TFTS (наземная авиационная телефонная связь, ETS 300 326-2): слоты G2/G3 — 8 × Голей (24,12) с перемежением 8×24; ПСП 1+X+X4+X6+X12; "
    "речь G1 без FEC; S1 — 5-кратный повтор",
    "Авиация: TFTS (ETSI ETS 300 326)", "Голей",
    {"слот": "208 бит: A 0–4 охрана, B 5–15 тип слота (11 бит), C 16–207 (192 бита); π/4-DQPSK (Грей), 44,2 кбит/с, 17 слотов в кадре",
     "поле B": f"G1 {POLE_B['G1']}, G2 {POLE_B['G2']}, G3 {POLE_B['G3']} (мин. расстояние между ними {rasst_B})",
     "G1 (речь)": "192 бита кодека 9,6 кбит/с без дополнительной защиты",
     "G2 (BCCH(D)/DCCH)": "12 октетов кадра уровня 2 → 8 слов расширенного Голея (24,12): g(X) = 1 + x2 + x4 + x5 + x6 + x10 + x11 (0xC75), "
                          "c(X) = x11·m(x) + {x11·m(x) mod g(x)} + чётный бит; в тексте записан как «(24,12,3)» (3 — число исправляемых ошибок)",
     "G3 (IRCCH/RCCH/ICH)": "4 октета (смещение 11 + мощность 5 + ID 12 + резерв 4) × 3 → 12 октетов → Голей как G2 → мажоритарное решение 2 из 3",
     "перемежитель": "8 слов по 24 бита записываются столбцами, передаются строками начиная со старших бит: передаваемый бит k (0–191) = "
                     "бит 24·(k mod 8) + 23 − ⌊k/8⌋ кодера (рис. 18; tablicy/tfts_ets300326.json)",
     "S1/S2": "синхрослово 71 бит (позиции 5–75): " + sinh + "; S1: 26 бит данных × 5 копий (130) + 2 запасных, решение 3 из 5; S2: ID 12 бит без защиты",
     "скремблер": "аддитивный, PN(X) = 1 + X + X4 + X6 + X12 (примитивный, период 4095), начальное состояние 0115 (восьм.) в начале каждого кадра; "
                  "только поле C слотов G1/G2/G3, после кодирования Голея",
     "FCS уровня 2": "CRC-16 x16 + x12 + x5 + 1 с предустановкой единицами и инверсией (как HDLC/X.25)"},
    "TFTS — европейская наземная система телефонии для пассажиров самолётов (1670–1675 / 1800–1805 МГц), выведена из эксплуатации",
    [TF.ist(s_g, "8.6.5.4: g(X)"), TF.ist(s_e, "кодирование"), TF.ist(s_i, "рис. 17–18: перемежитель"), TF.ist(s_pn, "8.7.2.4: ПСП"),
     TF.ist(s_pn0, "нач. состояние"), TF.ist(s_s, "табл. 4.b: синхрослово"), TF.ist(s_b, "табл. 3.b: поле B"), TF.ist(s_g1, "G1"), TF.ist(s_g3, "G3"),
     TF.ist(s_s1, "S1"), TF.ist(s_fcs, "FCS"), TF.ist(s_mod, "скорость")],
    "g(X) из текста = 0xC75 (тот же код, что Голей DMR/P25/M17); d = 8 у расширенного кода — перебором (см. запись M17, тот же g); перемежитель: формула "
    "воспроизводит 4 строки рис. 18, найденные в тексте, и является перестановкой 192 позиций; ПСП примитивна (x^4095 = 1, порядок полный); "
    "синхрослово 71 бит = длине поля B слота S (5–75); 5·26 + 2 = 132 = длине поля C слота S1",
    "частично: Голей 0xC75 и аддитивный скремблер есть; нужно: перемежитель 8×24 и разметка слотов — просто")

# ======================================================================================================================
# CDPD (патент US 6,694,146)
# ======================================================================================================================
CP = Tekst("istochniki/prochee/US6694146_CDPD_patent.pdf", ocr=True)
s_rs = CP.odna(r"This encoding is based upon a \(63,47\) Reed")[0]
s_gf = CP.odna(r"Solomon code generated over the Galois field GF \(64\)")[0]
s_378 = CP.odna(r"are encoded into a block of 378 bits")[0]
s_obe = CP.odna(r"Reed-Solomon encoding is common to both the forward")[0]
assert 47 * 6 == 282 and 63 * 6 == 378 and (63 - 47) == 16 and (63 - 47) // 2 == 8
rec("CDPD (Cellular Digital Packet Data, AMPS): РС (63,47) над GF(64), блок 378 бит (282 бита данных + 96 проверочных), прямой и обратный канал",
    "Аналоговые сотовые (1G): CDPD", "РС",
    {"код": "систематический РС (63,47) над GF(2^6), символ 6 бит: 47 информационных (282 бита) + 16 проверочных (96 бит) = 378 бит; исправляет до 8 символов",
     "где в стандарте": "CDPD System Specification 1.1, Part 402, § 4.3.1, рис. 402-4 (CDPD Forum, 1995) — в открытом доступе не найден",
     "не указано в доступном источнике": "примитивный многочлен GF(64), корни порождающего многочлена, перемежение, цветовой код и флаги декодирования в "
                                         "прямом канале (19,2 кбит/с GMSK)"},
    "пакетная передача данных в каналах AMPS (США, 1990-е — 2000-е)",
    [CP.ist(s_rs, "РС (63,47)"), CP.ist(s_gf, "GF(64), 6-битные символы"), CP.ist(s_378, "блок 378 бит"), CP.ist(s_obe, "прямой и обратный канал")],
    "только по тексту патента (OCR) и арифметике 47·6 = 282, 63·6 = 378; многочлены поля и кода не приведены — для реализации нужна спецификация CDPD "
    "или запись сигнала (слепой поиск корней РС в проекте применим)",
    "частично: общий РС над GF(2^m) и слепое определение корней есть (rs_bch.py); конкретные многочлены CDPD неизвестны")

if __name__ == "__main__":
    for z in ZAPISI: print(z["имя"][:110])
