"""Независимая сверка записей кабельного ТВ и DOCSIS (J.83 Annex B, CableLabs PHY)."""
import json
from pv import *
J = "istochniki/itu/T-REC-J.83-200712-I.pdf"
P7 = st(7, 3, 0)

z = zapis("J.83 Annex B (США, DOCSIS нисходящий)")
str_ok(z, J, 19, "B.5.1: RS(128,122) над GF(128), t = 3, корни α^1…α^5, расширенный символ c(α^6)", "(128, 122) code over", "GF(128)", "t = 3", "first through fifth powers of α", "evaluating the code word at the sixth power of α")
g = rs_g(7, P7, range(1, 6))
exp, log = gf(7, P7)
st_ = [log[c] for c in g[1:]]   # показатели α коэффициентов x^4 … x^0
ok(z["имя"], "g(x) = x^5 + α^52x^4 + α^116x^3 + α^119x^2 + α^61x + α^15 (свой расчёт над x^7+x^3+1)", st_ == [52, 116, 119, 61, 15], str(st_))
t = stranica(J, 19)
ok(z["имя"], "в тексте B.5.1 те же показатели 15, 61, 119, 116, 52", all(f" {e} " in " " + " ".join(t.split()) + " " for e in ("15", "61", "119", "116", "52")))

z = zapis("J.83 Annex B: кадр FEC")
str_ok(z, J, 24, "B.5.4: рандомизатор f(x) = x^3 + x + α^3 над GF(128)", "f(x) = x3 + x + α3")
str_ok(z, J, 22, "B.5.3: синхрохвост 64-QAM (28 бит) и 256-QAM (32 бита)", "(1110101", "0101100 0001101 1101100) or (75 2C 0D 6C)HEX", "(0111 0001 1110 1000")
ok(z["имя"], "семибитные группы 1110101 0101100 0001101 1101100 = 75 2C 0D 6C (свой перевод)", [int(b, 2) for b in "1110101 0101100 0001101 1101100".split()] == [0x75, 0x2C, 0x0D, 0x6C])

z = zapis("J.83 Annex B: ТКМ")
str_ok(z, J, 29, "B.5.5.4: 16 состояний, G1 = 010 101, G2 = 011 111 (25,37), коммутатор с G1, [P1,P2] = [0001;1111], 4/5", "16-state non-systematic rate 1/2 encoder", "G1 = 010 101, G2 = 011 111", "(25,37octal)", "[P1, P2] = [0001;1111]", "rate 4/5", "in the G1 position")
str_ok(z, J, 29, "рис. B.16: уравнения дифференциального прекодера", "WJ + xJ–1 + ZJ(xJ–1 + YJ–1)", "ZJ + WJ + YJ–1 + ZJ(xJ–1 + YJ–1)")
# свой расчёт d_free (25, 37), K = 5
def dfree(gens, K):
    import heapq
    m = K - 1
    def shag(s, b):
        reg = (b << m) | s
        return reg >> 1, sum(bin(reg & g).count("1") & 1 for g in gens)
    s0, w0 = shag(0, 1); best = {s0: w0}; q = [(w0, s0)]
    while q:
        w, s = heapq.heappop(q)
        if s == 0: return w
        for b in (0, 1):
            ns, dw = shag(s, b)
            if w + dw < best.get(ns, 99): best[ns] = w + dw; heapq.heappush(q, (w + dw, ns))
import itertools
def dfree_perebor(gens, K, L=12):
    best = 99
    for n in range(1, L + 1):
        for t in itertools.product((0, 1), repeat=n - 1):
            s = 0; w = 0
            for b in [1, *t] + [0] * (K - 1):
                s = ((s << 1) | b) & ((1 << K) - 1); w += sum(bin(s & g).count("1") & 1 for g in gens)
            best = min(best, w)
    return best
ok(z["имя"], "d_free невыколотого (25,37)₈ K=5 = 6 — два своих метода (Дейкстра по решётке и перебор входов до 12 бит)", dfree([0o25, 0o37], 5) == 6 == dfree_perebor([0o25, 0o37], 5))

z = zapis("DOCSIS 1.x–3.0 восходящий")
C = "istochniki/cablelabs/CM-SP-PHYv3.0-C01-171207.pdf"
str_ok(z, C, 36, "6.2.4: g(x) = (x+α^0)…(x+α^(2T−1)), α = 0x02, p(x) = x^8+x^4+x^3+x^2+1, 18…255 байт", "g(x) = (x+α0) (x+α1)...(x+α2T-1)", "alpha is 0x02 hex", "p(x) = x8 + x4 + x3+ x2 + 1", "minimum size of 18 bytes")
str_ok(z, C, 41, "скремблер x^15 + x^14 + 1, затравка из UCD", "The polynomial MUST be x15 + x14 + 1", "seed value MUST be configured in response to the Upstream Channel Descriptor")
ok(z["имя"], "x^15 + x^14 + 1 примитивен → период 32767 (свой расчёт)", primitiven(st(15, 14, 0)))

z = zapis("DOCSIS 3.1: малые LDPC")
C31 = "istochniki/cablelabs/CM-SP-PHYv3.1-I15-180926.pdf"
tab = json.load(open(os.path.join(KOREN, "tablicy/docsis31_ldpc_maly.json")))["данные"]
def rang(Hc, Z):
    piv = {}
    for r in Hc:
        for s in range(Z):
            v = 0
            for j, a in enumerate(r):
                if a >= 0: v |= 1 << (j * Z + (s + a) % Z)
            while v:
                h = v.bit_length() - 1
                if h in piv: v ^= piv[h]
                else: piv[h] = v; break
    return len(piv)
r1 = rang(tab["(160,80) L=16, 5×10"], 16); r2 = rang(tab["(480,288) L=48, 4×10"], 48)
ok(z["имя"], "свой ранг развёрнутых H: (160,80) → 80, (480,288) → 192", r1 == 80 and r2 == 192, f"{r1}, {r2}")
