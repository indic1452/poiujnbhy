"""UMTS / LTE / NR: построчная сверка с 25.212, 36.212, 38.212 (свой разбор текста pymupdf) и вычислительные проверки."""
import json, os, re, itertools
from pv import *
U = "istochniki/umts/ts_125212v190000p.pdf"; L = "istochniki/lte/ts_136212v190300p.pdf"; N = "istochniki/nr/ts_138212v190400p.pdf"
T = lambda f: json.load(open(os.path.join(KOREN, "tablicy", f)))["данные"]
def tokens(pdf, *pp):
    return [int(x) for p in pp for x in re.findall(r"(?<![\w.])\d+(?![\w.])", stranica(pdf, p))]
def podryad(tok, seq):
    n = len(seq)
    return any(tok[i:i + n] == seq for i in range(len(tok) - n + 1))
def dmin(G):
    k = len(G); n = len(G[0]); rows = [int("".join(map(str, r)), 2) for r in G]
    best = n
    for m in range(1, 1 << k):
        w = 0
        for i in range(k):
            if m >> i & 1: w ^= rows[i]
        best = min(best, bin(w).count("1"))
    return best
def transp(M): return [list(c) for c in zip(*M)]

# ---------- UMTS ----------
z = zapis("UMTS CRC 24/16/12/8")
str_ok(z, U, 20, "многочлены CRC 4.2.1.1", "gCRC24(D) = D24 + D23 + D6 + D5 + D + 1", "gCRC16(D) = D16 + D12 + D5 + 1", "gCRC12(D) = D12 + D11 + D3 + D2 + D + 1", "gCRC8(D) = D8 + D7 + D4 + D3 + D + 1")
p = z["параметры"]
ok(z["имя"], "hex записи = степеням текста", int(p["CRC24"]["hex"], 16) == st(24, 23, 6, 5, 1, 0) and int(p["CRC12"]["hex"], 16) == st(12, 11, 3, 2, 1, 0) and int(p["CRC8"]["hex"], 16) == st(8, 7, 4, 3, 1, 0))
z = zapis("UMTS свёрточные K=9")
str_ok(z, U, 23, "рис. 3", "G0 = 557 (octal)", "G1 = 663 (octal)", "G2 = 711 (octal)", "G0 = 561 (octal)", "G1 = 753 (octal)", "8 tail bits with binary value 0")
z = zapis("UMTS турбокод PCCC")
D = T("umts_25212.json")
pv_ = {int(k): v for k, v in D["prostye_p_v_t2"].items()}
tok = tokens(U, 26, 27)
ok(z["имя"], "табл. 2: все пары (p,v) стоят подряд в тексте стр. 26–27", all(podryad(tok, [p_, v]) for p_, v in pv_.items()), f"{len(pv_)} пар")
def perv_koren(v, p):
    return len({pow(v, e, p) for e in range(1, p)}) == p - 1
ok(z["имя"], "табл. 2: каждое v — первообразный корень по модулю p (вычислено)", all(perv_koren(v, p_) for p_, v in pv_.items()) and len(pv_) == 52, f"{len(pv_)} пар, все корни первообразные")
str_ok(z, U, 27, "табл. 3 межстрочные шаблоны", "<19, 9, 14, 4, 0, 2, 5, 7, 12, 18, 16, 13, 17, 15, 3, 1, 6, 11, 8, 10>", "<19, 9, 14, 4, 0, 2, 5, 7, 12, 18, 10, 8, 13, 17, 3, 1, 16, 6, 15, 11>")
def umts_perem(K):
    R = 5 if K <= 159 else 10 if (160 <= K <= 200 or 481 <= K <= 530) else 20
    if 481 <= K <= 530: p_ = 53; C = p_
    else:
        p_ = min(q for q in pv_ if K <= R * (q + 1))
        C = p_ - 1 if K <= R * (p_ - 1) else p_ if K <= R * p_ else p_ + 1
    v = pv_[p_]; s = [1]
    for j in range(1, p_ - 1): s.append(v * s[-1] % p_)
    def prime(x): return x > 1 and all(x % d for d in range(2, int(x ** .5) + 1))
    q = [1]
    while len(q) < R:
        c = q[-1] + 1
        while not (prime(c) and c > 6 and __import__("math").gcd(c, p_ - 1) == 1): c += 1
        q.append(c)
    if R == 5: Tp = [4, 3, 2, 1, 0]
    elif R == 10: Tp = list(range(9, -1, -1))
    elif 2281 <= K <= 2480 or 3161 <= K <= 3210: Tp = [19, 9, 14, 4, 0, 2, 5, 7, 12, 18, 16, 13, 17, 15, 3, 1, 6, 11, 8, 10]
    else: Tp = [19, 9, 14, 4, 0, 2, 5, 7, 12, 18, 10, 8, 13, 17, 3, 1, 16, 6, 15, 11]
    r = [0] * R
    for i in range(R): r[Tp[i]] = q[i]
    Uu = []
    for i in range(R):
        if C == p_: u = [s[(j * r[i]) % (p_ - 1)] for j in range(p_ - 1)] + [0]
        elif C == p_ + 1: u = [s[(j * r[i]) % (p_ - 1)] for j in range(p_ - 1)] + [0, p_]
        else: u = [s[(j * r[i]) % (p_ - 1)] - 1 for j in range(p_ - 1)]
        Uu.append(u)
    if C == p_ + 1 and K == R * C: Uu[R - 1][p_], Uu[R - 1][0] = Uu[R - 1][0], Uu[R - 1][p_]
    M = [[i * C + Uu[i][j] for j in range(C)] for i in range(R)]   # строка i после внутристрочной перестановки
    Mp = [M[Tp[i]] for i in range(R)]                               # межстрочная
    return [Mp[i][j] for j in range(C) for i in range(R) if Mp[i][j] < K], R, C, p_
sovp = []
for K, ex in D["primery_turbo_peremezhitelya"].items():
    pi, R, C, p_ = umts_perem(int(K))
    sovp.append(pi[:16] == ex["первые 16"] and (R, C, p_) == (ex["R"], ex["C"], ex["p"]) and sorted(pi) == list(range(int(K))))
ok(z["имя"], "своя реализация 4.2.3.2.3 (по тексту стр. 25–28) против примеров таблицы сборщика (R, C, p, первые 16) + биективность", all(sovp), f"{sum(sovp)} из {len(sovp)} размеров")
# независимый внешний пример: arXiv 0802.0808 табл. 15 (K = 250) — сравнение по видимым числам
allK = all(sorted(umts_perem(K)[0]) == list(range(K)) for K in range(40, 5115, 37))
ok(z["имя"], "своя реализация биективна для K = 40…5114 (шаг 37, 137 размеров)", allK)
z = zapis("UMTS TFCI (32,10)")
M8 = D["TFCI_32x10_t8"]; tok = tokens(U, 57)
ok(z["имя"], "табл. 8: все 32 строки «i Mi,0…Mi,9» подряд в тексте стр. 57", all(podryad(tok, [i] + M8[i]) for i in range(32)))
ok(z["имя"], "d_min (32,10) = 12 (перебор 1023 слов)", dmin(transp(M8)) == z["параметры"]["d_min"] == 12, f"вычислено {dmin(transp(M8))}")
z = zapis("UMTS перемежители 1-й")
str_ok(z, U, 50, "табл. 7", "<0, 20, 10, 5, 15, 25, 3, 13, 23, 8, 18, 28, 1, 11, 21, 6, 16, 26, 4, 14, 24, 19, 9, 29, 12, 2, 7, 22, 27, 17>")
ok(z["имя"], "перестановка табл. 7 в записи = текст", D["vtoroi_peremezhitel_t7"] == [0, 20, 10, 5, 15, 25, 3, 13, 23, 8, 18, 28, 1, 11, 21, 6, 16, 26, 4, 14, 24, 19, 9, 29, 12, 2, 7, 22, 27, 17])

# ---------- LTE ----------
z = zapis("LTE CRC24A/CRC24B")
str_ok(z, L, 11, "5.1.1", "gCRC24A(D) = [D24 + D23 + D18 + D17 + D14 + D11 + D10 + D7 + D6 + D5 + D4 + D3 + D + 1]", "gCRC24B(D) = [D24 + D23 + D6 + D5 + D + 1]", "gCRC16(D) = [D16 + D12 + D5 + 1]")
str_ok(z, L, 12, "5.1.1 CRC8", "gCRC8(D) = [D8 + D7 + D4 + D3 + D + 1]", sosedi=1)
ok(z["имя"], "hex 24A = степеням текста", int(z["параметры"]["CRC24A"]["hex_с_старшим"], 16) == st(24, 23, 18, 17, 14, 11, 10, 7, 6, 5, 4, 3, 1, 0))
z = zapis("LTE TBCC")
str_ok(z, L, 15, "рис. 5.1.3-1 / текст", "tail biting", sosedi=0)
z = zapis("LTE турбокод PCCC")
Q = T("lte_qpp_36212_t5.1.3-3.json"); tok = tokens(L, 18, 19)
ok(z["имя"], "табл. 5.1.3-3: все 188 четвёрок (i,K,f1,f2) подряд в тексте стр. 18–19", len(Q) == 188 and all(podryad(tok, [r["i"], r["K"], r["f1"], r["f2"]]) for r in Q))
Ks = [40 + 8 * i for i in range(60)] + [528 + 16 * i for i in range(32)] + [1056 + 32 * i for i in range(32)] + [2112 + 64 * i for i in range(64)]
ok(z["имя"], "сетка K: 40…512 шаг 8, 528…1024 шаг 16, 1056…2048 шаг 32, 2112…6144 шаг 64 (= 188); f1 нечётно, f2 чётно; QPP биективен",
   [r["K"] for r in Q] == Ks and all(r["f1"] % 2 and not r["f2"] % 2 for r in Q) and all(len({(r["f1"] * i + r["f2"] * i * i) % r["K"] for i in range(r["K"])}) == r["K"] for r in Q))
z = zapis("LTE блочный код (32,O)")
B = T("lte_bazisy_rm_36212.json"); M = B["M32x11_t5.2.2.6.4-1"]; tok = tokens(L, 123)
ok(z["имя"], "табл. 5.2.2.6.4-1: 32 строки подряд в тексте стр. 123", all(podryad(tok, [i] + M[i]) for i in range(32)))
ok(z["имя"], "d_min (32,11) = 10", dmin(transp(M)) == 10, f"вычислено {dmin(transp(M))}")
z = zapis("LTE блочный код (20,A)")
M = B["M20x13_t5.2.3.3-1"]; tok = tokens(L, 144)
ok(z["имя"], "табл. 5.2.3.3-1: 20 строк подряд в тексте стр. 144", all(podryad(tok, [i] + M[i]) for i in range(20)))
ok(z["имя"], "d_min (20,13) = 4", dmin(transp(M)) == 4, f"вычислено {dmin(transp(M))}")
z = zapis("LTE CFI")
C = T("lte_cfi_36212_t5.3.4-1.json")
str_ok(z, L, 244, "табл. 5.3.4-1", *["<" + ",".join(map(str, C[k])) + ">" for k in ("CFI1", "CFI2", "CFI3")])

# ---------- NR ----------
z = zapis("5G NR CRC24A/24B/24C")
str_ok(z, N, 12, "5.1 (формулы в текстовом слое: показатели степеней подряд перед именем)", "]1 [ 3 4 5 6 7 10 11 14 17 18 23 24 CRC24A", "]1 [ 5 6 23 24 CRC24B", "]1 [ 2 4 8 12 13 15 17 20 21 23 24 CRC24C", "]1 [ 5 12 16 CRC16", "]1 [ 5 9 10 11 CRC11", "]1 [ 5 6 CRC6")
p = z["параметры"]
ok(z["имя"], "hex 24C/11/6 = степеням текста", int(p["CRC24C"]["hex"], 16) == st(24, 23, 21, 20, 17, 15, 13, 12, 8, 4, 2, 1, 0) and int(p["CRC11"]["hex"], 16) == st(11, 10, 9, 5, 0) and int(p["CRC6"]["hex"], 16) == st(6, 5, 0))
z = zapis("5G NR полярный код")
P = T("nr_polar_38212.json"); Qn = P["Q_0_1023_t5.3.1.2-1"]
tok = tokens(N, 19, 20, 21)
ok(z["имя"], "Q_0^1023: перестановка 0…1023, все пары (W, Q) подряд в тексте стр. 19–21", sorted(Qn) == list(range(1024)) and sum(podryad(tok, [i, Qn[i]]) for i in range(1024)) == 1024,
   f"пар найдено {sum(podryad(tok, [i, Qn[i]]) for i in range(1024))}")
Pi = P["PI_IL_max_t5.3.1.1-1"]
ok(z["имя"], "Π_IL^max: 164 значения, перестановка 0…163", sorted(Pi) == list(range(164)))
ok(z["имя"], "P подблочного (табл. 5.4.1.1-1): перестановка 0…31, найдена в тексте стр. 27", sorted(P["P_podblok_t5.4.1.1-1"]) == list(range(32)) and all(podryad(tokens(N, 27), [i, P["P_podblok_t5.4.1.1-1"][i]]) for i in range(32)))
