"""Независимая сверка записей PLC и домашних сетей (HomePlug AV, G3-PLC, PRIME)."""
import json, re
from pv import *
HP = "istochniki/plc/homeplug_av11_specification_final_public.pdf"
z = zapis("HomePlug AV / AV2")
str_ok(z, HP, 70, "табл. 3-6 (16/21) и 3-7, формула I(x)", "p  1001001001001000", "q  1001001001001000", "I(x) = [S(x mod N) – (x div N)*N + L] mod L", "When the output index x is even")
t = stranica(HP, 70)
par = re.search(r"Interleaver Length L\s+16\s+8\s+8\s+64\s+136\s+34\s+16\s+544\s+520\s+40\s+52\s+2080", " ".join(t.split()))
ok(z["имя"], "табл. 3-7: (PB, N, M, L) = (16, 8, 8, 64), (136, 34, 16, 544), (520, 40, 52, 2080); M = L/N", bool(par))
sidy = {}
tt = " ".join((stranica(HP, 71) + stranica(HP, 72)).split())
for pb, a, b in (("16", "Table 3-8", "Table 3-9"), ("136", "Table 3-9", "Table 3-10"), ("520", "Table 3-10", "3.4.3")):
    k = tt[tt.index(a):tt.index(b, tt.index(a) + 5)]
    vals = []
    for blok in re.findall(r"S\(x\)((?:\s+\d+)+)", k): vals += [int(v) for v in blok.split()]
    sidy[pb] = vals
tab = json.load(open(os.path.join(KOREN, "tablicy/homeplug_av_turbo.json")))["данные"]["сиды S(x)"]
ok(z["имя"], "сиды табл. 3-8…3-10 разобраны заново (8 + 34 + 40) и совпали с tablicy/homeplug_av_turbo.json", all(sidy[k] == tab[k] for k in tab) and [len(sidy[k]) for k in ("16", "136", "520")] == [8, 34, 40])
perest = all(sorted((sidy[pb][x % N] - (x // N) * N + L) % L for x in range(L)) == list(range(L)) for pb, N, L in (("16", 8, 64), ("136", 34, 544), ("520", 40, 2080)))
ok(z["имя"], "I(x) по формуле текста — перестановка 0…L−1 для всех трёх длин (свой расчёт)", perest)

z = zapis("G3-PLC (G.9903")
G3 = "istochniki/itu/T-REC-G.9903-201708-I.pdf"
str_ok(z, G3, 31, "7.9.1: GF(2^8) x^8+x^4+x^3+x^2+1 (435₈), T = 4 / 8, 2T проверочных", "p (x) = x8 + x4 + x3 + x2 + 1 (435 octal)", "can be either 4 or 8", "For the robust mode, the code with T=4 is used")
str_ok(z, G3, 32, "7.9.2: K = 7, x = 0b1111001, y = 0b1011011 (171/133)", "x = 0b1111001 and y = 0b1011011", "K=7")
ok(z["имя"], "0b1111001 = 171₈, 0b1011011 = 133₈; 0x11D ↔ 435₈ (свой перевод)", int("1111001", 2) == 0o171 and int("1011011", 2) == 0o133 and 0o435 == 0x11D)

z = zapis("PRIME (G.9904)")
PR = "istochniki/itu/T-REC-G.9904-201210-I.pdf"
t = " ".join(stranica(PR, 18).split())
seq = [int(x) for x in re.search(r"Pref0\.\.126 = \{([01, ]+)\}", t).group(1).replace(" ", "").split(",")]
# свой ЛРС: x_n = x_{n−4} ⊕ x_{n−7}, начальное «все единицы» (запись S(x) = x^7 + x^4 + 1, как скремблер IEEE 802.11)
reg = [1] * 7; out = []
for _ in range(127):
    b = reg[3] ^ reg[6]; out.append(b); reg = [b] + reg[:6]
ok(z["имя"], f"Pref0..126 ({len(seq)} эл.) = выход своего ЛРС x^7 + x^4 + 1 из «все единицы» (та же ПСП, что скремблер IEEE 802.11)", seq == out and len(seq) == 127)
str_ok(z, PR, 18, "7.6: ПСП 127, начальное «все единицы», сброс в начале PPDU", "cyclic extension of the 127 element sequence", "all-ones", "initial state is used")
