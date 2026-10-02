"""Общие операции над многочленами GF(2) (целое: бит i — коэффициент при x^i)."""
from functools import lru_cache
import sympy


def deg(a): return a.bit_length() - 1


def mulmod(a, b, m):
    r = 0; dm = deg(m)
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if (a >> dm) & 1: a ^= m
    return r


def mul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
    return r


def mod(a, m):
    dm = deg(m)
    while a and deg(a) >= dm:
        a ^= m << (deg(a) - dm)
    return a


def divmod2(a, m):
    q = 0; dm = deg(m)
    while a and deg(a) >= dm:
        s = deg(a) - dm; q |= 1 << s; a ^= m << s
    return q, a


def gcd(a, b):
    while b: a, b = b, mod(a, b)
    return a


def powmod(a, e, m):
    r = 1; a = mod(a, m)
    while e:
        if e & 1: r = mulmod(r, a, m)
        a = mulmod(a, a, m); e >>= 1
    return r


@lru_cache(None)
def factors_2m1(m):
    return tuple(sympy.factorint((1 << m) - 1).keys())


def irreducible(p):
    """Бен-Ор / Рабин: x^(2^i) mod p, gcd с x^(2^i)-x для i ≤ m/2."""
    m = deg(p)
    if m <= 0: return False
    x = 2; t = x
    for i in range(1, m // 2 + 1):
        t = mulmod(t, t, p)
        if gcd(p, t ^ x) != 1: return False
    return True


def primitive(p):
    m = deg(p)
    if not (p & 1) or not irreducible(p): return False
    n = (1 << m) - 1
    if powmod(2, n, p) != 1: return False
    return all(powmod(2, n // q, p) != 1 for q in factors_2m1(m))


def order_x(p, limit=1 << 20):
    """Порядок x по модулю p (p(0)=1), наименьшее e: p | x^e + 1; None — больше limit."""
    r = 2 % p if deg(p) > 1 else 0
    t = mod(2, p); e = 1
    while t != 1:
        t = mulmod(t, 2, p); e += 1
        if e > limit: return None
    return e


def reverse(p):
    m = deg(p); r = 0
    for i in range(m + 1):
        if (p >> i) & 1: r |= 1 << (m - i)
    return r


def poly_str(p):
    t = []
    for i in range(deg(p), -1, -1):
        if (p >> i) & 1:
            t.append('1' if i == 0 else 'x' if i == 1 else 'x^%d' % i)
    return ' + '.join(t) if t else '0'


def octal(p): return '%o' % p
