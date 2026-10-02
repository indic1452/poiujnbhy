"""Независимая сверка записей семейства Ethernet (IEEE 802.3, консорциумы) с первоисточниками и тестовыми векторами."""
import re
from pv import *

S5 = "istochniki/ieee8023/standart/802.3-2012_section5.pdf"
S3 = "istochniki/ieee8023/standart/802.3-2012_section3.pdf"

def hex_posle(t, metka, n, dlina=None):
    """n шестнадцатеричных слов после метки (каждое — отдельный токен)."""
    i = t.index(metka) + len(metka)
    tok = re.findall(r"\b[0-9a-fA-F]+\b", t[i:])
    if dlina: tok = [x for x in tok if len(x) == dlina]
    return tok[:n]

# ---------- Cl.74 FireCode ----------
z = zapis("Clause 74")
str_ok(z, S5, 551, "ур. 74–1 и T = SH.1 XOR S1.0", "x32", "x23", "x21", "x11", "x2", "Transcode bit T = SH.1 XOR S1.0")
str_ok(z, S5, 552, "PN-2112: многочлен 49.2.6, S57 = 1, Si–1 = Si XOR 1", "initial state S57 = 1, Si–1 = Si XOR 1", "101010")
g74 = st(32, 23, 21, 11, 2, 0)
ok(z["имя"], "период g(x) = 42987 (своё вычисление)", period(g74) == 42987, f"период {period(g74)}")
t1 = stranica(S5, 825); t2 = stranica(S5, 826)
# табл. 74A–1: пары (sync, payload); табл. 74A–2: (T, payload) + чётность
p1 = re.findall(r"\n(10|01)\n([0-9a-f]{16})", t1)
k2 = t2.index("Table 74A–2"); k3 = t2.index("Table 74A–3", k2 + 20)
p2 = re.findall(r"\n([01])\n([0-9a-f]{16})", t2[k2:k3])
par = re.search(r"Parity hex \[0:31\]\s*\n\s*([0-9a-f]{8})", t2).group(1)
ok(z["имя"], "74A: 32 блока в табл. 74A–1 и 74A–2", len(p1) == 32 and len(p2) == 32, f"{len(p1)}, {len(p2)}")
# T = SH.1 XOR S1.0; S1.0 — бит 8 полезной нагрузки (в порядке передачи)
T_vych = [int(sh[1]) ^ bity_hex(pl)[8] for sh, pl in p1]
ispr(z["имя"], "формулировка бита транскодирования", "T = SH.1 XOR бит 8 блока 64B/66B", "T = SH.1 XOR S1.0 (бит 0 второго октета нагрузки = бит 8 нагрузки, бит 10 блока 66b)", "802.3-2012 рис. 74–5, стр. 551; проверено по табл. 74A–1/74A–2")
ok(z["имя"], "74A: T-биты = SH.1 XOR S1.0 (бит 8 нагрузки в порядке передачи)", T_vych == [int(t) for t, _ in p2] and [p for _, p in p1] == [p for _, p in p2])
blok = []
for t, pl in p2: blok += [int(t)] + bity_hex(pl)
m = int("".join(map(str, blok)), 2)
parity = pmod(m << 32, g74)
ok(z["имя"], "74A: свой кодер p(x) = x^32·m(x) mod g(x) против «d96e7685»", parity == int(par, 16), f"{parity:08x} / {par}")
# PN-2112: регистр S0…S57, S57=1, далее чередование; выход S38 ⊕ S57 сдвигается в S0
s = [0] * 58; s[57] = 1
for i in range(56, -1, -1): s[i] = s[i + 1] ^ 1
pn = []
for _ in range(2112):
    o = s[38] ^ s[57]; pn.append(o); s = [o] + s[:-1]
kod = blok + [int(b) for b in format(parity, "032b")]
skr = [a ^ b for a, b in zip(kod, pn)]
t3 = t2[k3:] + stranica(S5, 827)
h3 = re.findall(r"\b[0-9a-f]{16}\b", t3)[:33]
moi = ["%016x" % int("".join(map(str, skr[64 * i:64 * i + 64])), 2) for i in range(33)]
ok(z["имя"], "74A–3: скремблированный блок (свой PN-2112) — 33 слова по 64 бит", moi[:len(h3)] == h3 and len(h3) >= 30, f"совпало {sum(a == b for a, b in zip(moi, h3))} из {len(h3)}")

# ---------- Cl.76 RS(255,223) ----------
z = zapis("10G-EPON RS(255,223)")
str_ok(z, S5, 618, "76.3.2.4.1: RS(255,223) над GF(2^8), α = 0x02", "Reed-Solomon code (255,", "223) over the Galois Field", "represented as 0x02")
t = stranica(S5, 843) + stranica(S5, 844)
i4 = t.index("Table 76A–4—223 octet input buffer"); i5 = t.rindex("Table 76A–5—32 parity"); i6 = t.index("Table 76A–6", i5)
def oktety(k):
    return [int(x, 16) for x in re.findall(r"(?m)^([0-9A-F]{2})$", k[k.index("n="):])]
D = oktety(t[i4:t.index("Table 76A–5", i4)])   # D222 … D0 (старший первым)
P_tab = oktety(t[i5:i6])                          # P31 … P0
ok(z["имя"], "76A–4: 223 октета данных, 76A–5: 32 проверочных", len(D) == 223 and len(P_tab) == 32, f"{len(D)}, {len(P_tab)}")
g = rs_g(8, 0x11D, range(32))
P = rs_proverochnye(D, g, 8, 0x11D)
ok(z["имя"], "76A: свой кодер (x^8+x^4+x^3+x^2+1, корни α^0…α^31) → 32 проверочных = табл. 76A–5", P == P_tab, " ".join("%02X" % x for x in P[:8]) + "…")

# ---------- Cl.65 RS(255,239) ----------
z = zapis("1G-EPON FEC RS(255,239")
str_ok(z, S5, 353, "65.2.3: RS(255,239), /S_FEC/ /T_FEC/", "255", "239", "S_FEC", "T_FEC", sosedi=1)

# ---------- Cl.40 1000BASE-T ----------
z = zapis("1000BASE-T: 4D-PAM5")
str_ok(z, S3, 198, "40.3.1.3.1 скремблеры ведущий/ведомый", "x33", "x13", "x20", sosedi=1)

# ---------- KR4 RS(528,514) ----------
z = zapis("RS(528,514) «KR4 FEC»")
I = "istochniki/ieee8023/bj/"
str_ok(z, I + "langhammer_01_0512.pdf", 3, "91.4.2.9: поле x^10+x^3+1, первый корень α^0", "be x10+ x3+1", "first root of the generator polynomial shall be 1")
g528 = rs_g(10, 0x409, range(14))
t = stranica(I + "gustlin_01_0312.pdf", 20)
gk = [int(x) for x in re.search(r"decimal and hex\)\s*\n([\d ]+)\n", t).group(1).split()]
ok(z["имя"], "g(x) RS(528,514): 15 коэффициентов (Gustlin 03/12, обратный порядок) = свои", gk == g528[::-1], f"{g528[::-1][:5]}…")
dannye = list(range(1023, 509, -1))
pk = [int(x) for x in re.search(r"check symbols\s*\n(?:Followed[^\n]*\n)?([\d ]+) decimal", t).group(1).split()]
ok(z["имя"], "пример Gustlin: 1023…510 → 14 проверочных (свой кодер)", rs_proverochnye(dannye, g528, 10, 0x409) == pk, str(pk[:4]))
t = stranica(I + "anslow_01a_0712.pdf", 7)
pa = [int(x) for x in re.search(r"would be:\s*\n([\d, ]+) decimal", t).group(1).replace(",", " ").split()]
ok(z["имя"], "пример Anslow: 513×0x000 + 0x100 → 14 проверочных (свой кодер)", rs_proverochnye([0] * 513 + [0x100], g528, 10, 0x409) == pa, str(pa[:4]))

# ---------- KP4 RS(544,514) ----------
z = zapis("RS(544,514) «KP4 FEC»")
B = "istochniki/ieee8023/bs/gustlin_3bs_03_0317.pdf"
t = stranica(B, 17)
tab = t[t.index("Table 119–3"):]
nums = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", tab)]
# строки таблицы: тройки пар (i, gi)
gi = {}
for j in range(0, len(nums) - 1, 2):
    if nums[j] <= 30 and nums[j] not in gi: gi[nums[j]] = nums[j + 1]
    if len(gi) == 31: break
g544 = rs_g(10, 0x409, range(30))[::-1]   # g0 … g30
ok(z["имя"], "табл. 119–3: 31 коэффициент g_i = свои (x^10+x^3+1, корни α^0…α^29)", len(gi) == 31 and all(gi[i] == g544[i] for i in gi), f"совпало {sum(gi.get(i) == g544[i] for i in range(31))}/31")
str_ok(z, B, 17, "чередование cA/cB 400G", "tx_out<16k+2j> = cA<543-8k-j>", "tx_out<16k+2j+1> = cB<543-8k-j>")
if "119.2.4.8 Symbol distribution" in t:
    ispr(z["имя"], "номер пункта чередования cA/cB (черновик D3.1, стр. 17)", "119.2.4.6", "119.2.4.8", "заголовок «119.2.4.8 Symbol distribution» на стр. 17 gustlin_3bs_03_0317.pdf")

# ---------- 802.3bv БЧХ ----------
z = zapis("1000BASE-RH")
BV = "istochniki/ieee8023/bv/IEEE_P802d3bv_114A_perezaranda_280616.pdf"
def bv_slovo(t, a, b):
    k = t[t.index(a):t.index(b)] if b else t[t.index(a):]
    return "".join(re.findall(r"(?m)^([0-9a-f]+)\s*$", k))
t1 = stranica(BV, 1); t2 = stranica(BV, 2)
i1 = bv_slovo(t1, "Input information bits [0:719]", "Parity bits [720:895]")
p1 = bv_slovo(t1, "Parity bits [720:895]", None)
s1 = bity_hex(i1)[:720] + bity_hex(p1)[:176]
def bch_g(m, prim, t):
    g = 1; vzyato = set()
    for i in range(1, 2 * t + 1):
        mp = min_mnogochlen(i, m, prim)
        if mp not in vzyato: vzyato.add(mp); g = pmul(g, mp)
    return g
w1 = int("".join(map(str, s1)), 2)
prim11 = [p for p in range(1 << 11, 1 << 12) if p & 1 and primitiven(p)]
podh = [p for p in prim11 if pmod(w1, bch_g(11, p, 16)) == 0]
ok(z["имя"], f"114A.1: слово 896 бит делится на g(x) БЧХ t=16 только при x^11+x^2+1 (перебор {len(prim11)} примитивных)", podh == [st(11, 2, 0)], str([hex(p) for p in podh]))
try:
    k = t2.index("Input information bits")
    i2 = bv_slovo(t2, "Input information bits", "Parity bits"); p2 = bv_slovo(t2[t2.index("Parity bits"):], "Parity bits", None)
    s2 = bity_hex(i2)[:1668] + bity_hex(p2)[:308]
    ok(z["имя"], "114A.2: слово 1976 бит делится на g(x) БЧХ t=28 над x^11+x^2+1", pmod(int("".join(map(str, s2)), 2), bch_g(11, st(11, 2, 0), 28)) == 0, f"{len(s2)} бит")
except ValueError as e:
    ok(z["имя"], "114A.2 найдена", False, str(e))

# ---------- LL-FEC RS(272,258) ----------
z = zapis("LL-FEC RS(272")
LL = "istochniki/etc/LL-FEC-Specification-1.0.pdf"
str_ok(z, LL, 6, "3.1.1: g как ур. 91-1 RS(528,514)", "identical to the one used", "RS(528,514)")
def ll_slovo(t, metka):
    k = t[t.index(metka):]
    stroki = re.findall(r"<(\d+):(\d+)>\s*\n\s*([0-9a-f]+)", k)[:9]
    bits = ""; vosst = 0
    for a, b, h in stroki:
        nuzhno = (int(a) - int(b) + 1) // 4
        if len(h) < nuzhno: vosst += 1; h = "0" * (nuzhno - len(h)) + h
        bits += "".join(format(int(c, 16), "04b") for c in h)
    return bits, vosst
for metka, str_ in (("Table 4", 15), ("Table 6", 16)):
    bits, vosst = ll_slovo(stranica(LL, str_), metka)
    # биты <2719:0>: первый знак — старший индекс; символ i = биты <10i+9:10i>, старший бит символа — старший индекс
    sim = [int(bits[j:j + 10], 2) for j in range(0, 2720, 10)]   # sim[0] = символ 271
    s_ = sindromy(sim, 10, 0x409, range(14))
    ok(z["имя"], f"{metka}: кодовое слово RS(272,258) — 14 нулевых синдромов (корни α^0…α^13), ведущих нулей восстановлено: {vosst}", not any(s_), f"синдромы {s_[:3]}…")
