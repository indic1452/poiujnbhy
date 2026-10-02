"""Проверочные вычисления для каталога «provod»: поля GF(2^m), РС, БЧХ, циклические коды, свёрточные коды.
Всё строится из параметров, прочитанных в источнике, и сверяется с тем, что источник утверждает
(n − k, число исправляемых ошибок, тестовые векторы приложений)."""
from functools import lru_cache


def stepen(p):
    return p.bit_length() - 1


def mul_mod(a, b, p):
    m = stepen(p); r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if a >> m & 1: a ^= p
    return r


def primitivnyj(p):
    """p — неприводимый примитивный: порядок x по модулю p = 2^m − 1."""
    m = stepen(p); N = (1 << m) - 1
    if not p & 1: return False
    # порядок делит N и не делит N/q для простых q | N
    def pw(e):
        r, a = 1, 2
        while e:
            if e & 1: r = mul_mod(r, a, p)
            a = mul_mod(a, a, p); e >>= 1
        return r
    if pw(N) != 1: return False
    n, q, prost = N, 2, set()
    while q * q <= n:
        while n % q == 0: prost.add(q); n //= q
        q += 1
    if n > 1: prost.add(n)
    return all(pw(N // q) != 1 for q in prost)


@lru_cache(maxsize=64)
def pole(p):
    m = stepen(p); q = 1 << m
    exp = [0] * (2 * q); log = [0] * q; x = 1
    for i in range(q - 1):
        exp[i] = x; log[x] = i; x = mul_mod(x, 2, p)
    for i in range(q - 1, 2 * q): exp[i] = exp[i - (q - 1)]
    return exp, log


def gmul(a, b, p):
    if a == 0 or b == 0: return 0
    exp, log = pole(p); return exp[log[a] + log[b]]


def rs_g(p, fcr, nroots, gen=1):
    """Порождающий многочлен РС ∏(x − α^(gen·(fcr+i))), коэффициенты от старшего к младшему."""
    exp, log = pole(p); N = (1 << stepen(p)) - 1
    g = [1]
    for i in range(nroots):
        r = exp[(gen * (fcr + i)) % N]
        ng = g + [0]
        for j in range(len(g)): ng[j + 1] ^= gmul(g[j], r, p)
        g = ng
    return g


def rs_kodirovat(dannye, p, fcr, nroots, gen=1):
    """Систематический РС: проверочные символы (остаток dannye·x^nroots mod g), старший символ первым."""
    g = rs_g(p, fcr, nroots, gen)
    reg = [0] * nroots
    for d in dannye:
        fb = d ^ reg[0]
        reg = reg[1:] + [0]
        if fb:
            for j in range(nroots): reg[j] ^= gmul(fb, g[j + 1], p)
    return reg


def rs_sindromy(slovo, p, fcr, nroots, gen=1):
    exp, log = pole(p); N = (1 << stepen(p)) - 1; S = []
    for i in range(nroots):
        r = exp[(gen * (fcr + i)) % N]; s = 0
        for c in slovo: s = gmul(s, r, p) ^ c
        S.append(s)
    return S


def poly_mul2(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
    return r


def poly_mod2(a, b):
    db = stepen(b)
    while a and stepen(a) >= db: a ^= b << (stepen(a) - db)
    return a


def minimalnyj(p, i):
    """Минимальный многочлен α^i над GF(2) (целое, бит k — коэффициент x^k)."""
    exp, log = pole(p); m = stepen(p); N = (1 << m) - 1
    kl = []; j = i % N
    while j not in kl: kl.append(j); j = (2 * j) % N
    # ∏ (x − α^j) в GF(2^m) → коэффициенты 0/1
    g = [1]
    for j in kl:
        r = exp[j]; ng = g + [0]
        for t in range(len(g)): ng[t + 1] ^= gmul(g[t], r, p)
        g = ng
    assert all(c in (0, 1) for c in g)
    return int("".join(map(str, g)), 2), tuple(sorted(kl))


def bch_g(p, korni):
    """НОК минимальных многочленов α^i, i ∈ korni (например 1,3,5,…) → порождающий многочлен БЧХ."""
    g = 1; bylo = set()
    for i in korni:
        mp, kl = minimalnyj(p, i)
        if kl in bylo: continue
        bylo.add(kl); g = poly_mul2(g, mp)
    return g


def iz_stepenej(st):
    return sum(1 << s for s in st)


def v_stepeni(g):
    return [i for i in range(g.bit_length() - 1, -1, -1) if g >> i & 1]


def zapis_x(g):
    return " + ".join("1" if s == 0 else "x" if s == 1 else f"x^{s}" for s in v_stepeni(g))


def cikl_kodirovat(bity, g):
    """Систематический циклический код: проверочные биты = остаток bity(x)·x^r mod g(x), первый бит — старший."""
    r = stepen(g); reg = 0; mask = (1 << r) - 1
    for b in bity:
        fb = ((reg >> (r - 1)) & 1) ^ b
        reg = (reg << 1) & mask
        if fb: reg ^= g & mask
    return [(reg >> (r - 1 - i)) & 1 for i in range(r)]


def poryadok_mnogochlena(g):
    """Период (порядок) многочлена g: наименьшее e, при котором g | x^e − 1 (для длины циклического кода)."""
    r = stepen(g); x = 2; e = 1
    x = poly_mod2(x, g)
    while x != 1:
        x = poly_mod2(x << 1, g); e += 1
        if e > (1 << (r + 1)): return None
    return e


def svyortka(bity, gens, K):
    """Несистематический свёрточный код: gens — целые многочлены (старший бит — текущий вход)."""
    s = 0; out = []
    for b in bity:
        s = ((s << 1) | b) & ((1 << K) - 1)
        for g in gens: out.append(bin(s & g).count("1") & 1)
    return out


def otrazit(g, K):
    return int(format(g, f"0{K}b")[::-1], 2)
