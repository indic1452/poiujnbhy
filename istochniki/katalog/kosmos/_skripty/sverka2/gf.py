"""Собственная (независимая от проекта и от сборщика) арифметика GF(2^m) и GF(2)[x] для сверки."""


class GF:
    def __init__(self, m, prim):
        self.m, self.prim, self.n = m, prim, (1 << m) - 1
        self.exp = [0] * (2 * self.n)
        self.log = [None] * (1 << m)
        x = 1
        for i in range(self.n):
            self.exp[i] = x
            assert self.log[x] is None, 'многочлен не примитивен'
            self.log[x] = i
            x <<= 1
            if x >> m:
                x ^= prim
        for i in range(self.n, 2 * self.n):
            self.exp[i] = self.exp[i - self.n]

    def mul(self, a, b):
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def a(self, i):
        return self.exp[i % self.n]

    def poly_from_roots(self, kornі):
        """∏(x − r) ; коэффициенты от старшего к младшему."""
        g = [1]
        for r in kornі:
            ng = g + [0]
            for i in range(len(g)):
                ng[i + 1] ^= self.mul(g[i], r)
            g = ng
        return g

    def minpoly(self, e):
        """минимальный многочлен α^e над GF(2) (целое, бит i = коэффициент x^i)."""
        sopr, k = [], e % self.n
        while k not in sopr:
            sopr.append(k)
            k = (2 * k) % self.n
        g = self.poly_from_roots([self.a(k) for k in sopr])
        assert all(c in (0, 1) for c in g)
        v = 0
        for c in g:
            v = (v << 1) | c
        return v


def pmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        b >>= 1
    return r


def pmod(a, b):
    db = b.bit_length()
    while a.bit_length() >= db:
        a ^= b << (a.bit_length() - db)
    return a


def is_primitive(p):
    m = p.bit_length() - 1
    try:
        GF(m, p)
        return True
    except AssertionError:
        return False


def lfsr_fib(taps_deg, deg, init_bits, n):
    """Фибоначчи: a[t+deg] = XOR a[t+deg-d] для d в taps_deg (многочлен x^deg + Σ x^(deg-d)...)."""
    a = list(init_bits)
    while len(a) < n:
        t = len(a) - deg
        v = 0
        for d in taps_deg:
            v ^= a[t + d]
        a.append(v)
    return a[:n]
