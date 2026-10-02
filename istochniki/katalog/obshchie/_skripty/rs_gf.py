"""РС над GF(2^m): таблицы поля, порождающий многочлен ∏(x − α^(prim·(fcr+i))), систематический кодер (старшая степень первой)."""


class GF:
    def __init__(self, m, poly, alpha=2):
        self.m = m; self.q = 1 << m; self.poly = poly
        self.exp = [0] * (2 * self.q); self.log = [0] * self.q
        x = 1
        for i in range(self.q - 1):
            self.exp[i] = x; self.log[x] = i
            # x *= alpha
            y = 0; a = alpha; b = x
            while a:
                if a & 1: y ^= b
                a >>= 1; b <<= 1
                if b & self.q: b ^= poly
            x = y
        assert x == 1 and len(set(self.exp[:self.q - 1])) == self.q - 1, 'не примитивный'
        for i in range(self.q - 1, 2 * self.q): self.exp[i] = self.exp[i - (self.q - 1)]

    def mul(self, a, b):
        return 0 if a == 0 or b == 0 else self.exp[self.log[a] + self.log[b]]

    def pw(self, e): return self.exp[e % (self.q - 1)]


def genpoly(gf, nroots, fcr, prim=1):
    g = [1]
    for i in range(nroots):
        r = gf.pw(prim * (fcr + i))
        ng = g + [0]
        for j in range(len(g)):
            ng[j + 1] ^= gf.mul(g[j], r)
        g = ng
    return g  # g[0] — старший коэффициент (=1)


def encode(gf, data, g):
    nr = len(g) - 1
    rem = [0] * nr
    for d in data:
        fb = d ^ rem[0]
        rem = rem[1:] + [0]
        if fb:
            for j in range(nr): rem[j] ^= gf.mul(fb, g[j + 1])
    return rem
