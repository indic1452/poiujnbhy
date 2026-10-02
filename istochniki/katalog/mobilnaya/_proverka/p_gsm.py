"""GSM: сверка записей с TS 45.003/45.002 построчно + свой кодер xCCH/SCH против эталонного вывода libosmocore coding_test.ok."""
import os, re
from pv import *
G3 = "istochniki/gsm/ts_145003v190000p.pdf"; G2 = "istochniki/gsm/ts_145002v190000p.pdf"
OK_FILE = os.path.join(KOREN, "../../repos_m/libosmocore/tests/coding/coding_test.ok")

z = zapis("GSM xCCH")
str_ok(z, G3, 148, "Файр, остаток, хвост", "g(D) = (D23 + 1)*(D17 + D3 + 1)", "yields a remainder equal to: 1 + D + D2 +...+ D39", "u(k) = 0 for k = 224,225,226,227")
str_ok(z, G3, 148, "свёрточный G0/G1", "G0 = 1 + D3 + D4", "G1 = 1 + D + D3 + D4")
str_ok(z, G3, 149, "перемежение 4.1.4", "B = B0 + 4n + (k mod 4)", "j = 2((49k) mod 57) + ((k mod 8) div 4)")

def conv(u, gens):  # gens: списки степеней D
    return [sum(u[k - s] for s in g if k - s >= 0) % 2 for k in range(len(u)) for g in gens]
def fire_parity(d):
    g = pmul(st(23, 0), st(17, 3, 0))      # (D23+1)(D17+D3+1)
    m = int("".join(map(str, d)), 2) << 40
    r = pmod(m, g) ^ ((1 << 40) - 1)        # остаток = все единицы → p = rem ⊕ 1…1
    return [int(c) for c in format(r, "040b")]
def xcch(l2):
    d = [(l2[i // 8] >> (i % 8)) & 1 for i in range(184)]   # 44.004: младший бит октета первым (как libosmocore)
    u = d + fire_parity(d) + [0] * 4
    c = conv(u, [[0, 3, 4], [0, 1, 3, 4]])
    bursts = [[None] * 116 for _ in range(4)]
    for k in range(456):
        B = k % 4; j = 2 * ((49 * k) % 57) + ((k % 8) // 4)
        pos = j if j < 57 else j + 2           # e(B,59+j) = i(B,57+j)
        bursts[B][pos] = c[k]
    return bursts
txt = open(OK_FILE).read()
bloki = re.findall(r"Encoding: ((?:[0-9a-f]{2} ){23})\nU-Bits:\n((?:[01]{57} [0-9a-f]{2}  [0-9a-f]{2}  [01]{57}\n){4})", txt)
sovp = 0
for hexs, ub in bloki:
    l2 = bytes.fromhex(hexs.replace(" ", ""))
    ref = [l.split() for l in ub.strip().split("\n")]
    my = xcch(l2)
    sovp += all("".join(map(str, my[b][:57])) == ref[b][0] and "".join(map(str, my[b][59:])) == ref[b][3] for b in range(4))
ok(z["имя"], "свой кодер xCCH (Файр по 4.1.2 + свёрточный 4.1.3 + перемежение 4.1.4 + отображение 4.1.5) против libosmocore coding_test.ok", sovp == len(bloki) and sovp >= 3, f"{sovp} из {len(bloki)} тестовых кадров совпали побитно (4 пачки × 114 бит)")

z = zapis("GSM SCH")
str_ok(z, G3, 154, "SCH 25+10, g, остаток", "25 information bits", "D10 + D8 + D6 + D5 + D4 + D2 + 1", "yields a remainder equal to: D9 + D8 + D7 + D6 + D5 + D4 + D3 + D2 + D+ 1")
sch = re.findall(r"Encoding: ((?:[0-9a-f]{2} ){4})\nU-Bits: ([01]{78})", txt)
def sch_enc(b4):
    d = [(b4[i // 8] >> (i % 8)) & 1 for i in range(25)]
    m = int("".join(map(str, d)), 2) << 10
    r = pmod(m, st(10, 8, 6, 5, 4, 2, 0)) ^ 0x3FF
    u = d + [int(c) for c in format(r, "010b")] + [0] * 4
    return "".join(map(str, conv(u, [[0, 3, 4], [0, 1, 3, 4]])))
n = sum(sch_enc(bytes.fromhex(h.replace(" ", ""))) == ref for h, ref in sch)
ok(z["имя"], "свой кодер SCH (4.7) против libosmocore coding_test.ok", n == len(sch) and n >= 2, f"{n} из {len(sch)} совпали (78 бит)")

z = zapis("GSM TCH/FS")
str_ok(z, G3, 45, "CRC-3 класса 1a", "g(D) = D3 + D + 1", "yields a remainder equal to: 1 + D + D2", "u(k) = d(2k) and u(184-k) = d(2k+1)")
str_ok(z, G3, 44, "CRC-8 EFR", "g(D) = D8 + D4 + D3 + D2 + 1", "65 most important bits")
z = zapis("GSM TCH/HS")
str_ok(z, G3, 47, "мать 1/3 K=7 и выкалывание", "G4 = 1 + D2 + D3 + D5 + D6", "G5 = 1 + D + D4 + D6", "G6 = 1 + D + D2 + D3 + D4 + D6", "(1,0,1) for", "(1,1,1) for")
z = zapis("GSM RACH")
str_ok(z, G3, 152, "RACH 8 бит", "D6 + D5 + D3 + D2 + D + 1", "remainder equal to D5 + D4 + D3 + D2 + D + 1", "u(k) = 0 for k = 14,15,16,17")
z = zapis("GPRS PDTCH CS-1")
str_ok(z, G3, 165, "CS-3 CRC-16", "D16 + D12 + D5 + 1", "315 information bits", sosedi=1)
z = zapis("GSM ECSD")
str_ok(z, G3, 82, "ECSD RS 3.11.2.2", "RS8 (85,73)", "RS8(255,243)", "p(x)=x8+x4+x3+x2+1")
str_ok(z, G3, 83, "ECSD g(x)", "g(x)= x12 +18x11 + 157x10 + 162x9 + 134x8 + 157x7 + 253x6 + 157x5 + 134x4 + 162x3 + 157x2 + 18x + 1")
# вычисление g(x) = ∏ (x − α^(122+i)), i = 0…11 над GF(256), p = 0x11D
E = [0] * 512; L = [0] * 256; v = 1
for i in range(255):
    E[i] = E[i + 255] = v; L[v] = i; v <<= 1
    if v & 256: v ^= 0x11D
def gm(a, b): return 0 if a == 0 or b == 0 else E[L[a] + L[b]]
g = [1]
for i in range(12):
    r = E[(122 + i) % 255]; ng = [0] * (len(g) + 1)
    for j, c in enumerate(g): ng[j] ^= c; ng[j + 1] ^= gm(c, r)
    g = ng
ok(z["имя"], "вычислено g(x) = ∏(x+α^(122+i)), i=0..11, GF(256)/0x11D", g == [1, 18, 157, 162, 134, 157, 253, 157, 134, 162, 157, 18, 1], f"g = {g}")
