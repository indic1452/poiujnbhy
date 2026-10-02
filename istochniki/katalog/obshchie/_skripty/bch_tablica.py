"""Порождающие многочлены примитивных узкосмысловых БЧХ n = 2^m − 1, m = 3…10 → tablicy/bch/.

g(x) = НОК минимальных многочленов α^1…α^(2t) над GF(2), α — корень примитивного p(x).
p(x) — как в открытых программах: bch3.c (R. Morelos-Zaragoza, по Lin–Costello; строки 104–123) и
lib/bch.c ядра Linux (prim_poly_tab, строка 1287; GPL-2.0) — они различаются только при m = 8
(bch3.c: x^8+x^6+x^5+x^4+1; Linux: x^8+x^4+x^3+x^2+1 = 0x11D), поэтому для m = 8 — обе таблицы.
Проверки (assert): g | x^n + 1; g(α^i) = 0, i = 1…2t; deg g = n − k; совпадение с galois.BCH (MIT) при том же
p(x); n = 63 — совпадение с таблицей Y. S. Han (NTPU, BCH_code.pdf стр. 14, разложение на мин. многочлены).
"""
import json, os, sys
import galois, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf2 import mul, mod, deg, mulmod, poly_str, primitive
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(KOR, 'tablicy', 'bch'); os.makedirs(OUT, exist_ok=True)
SRC = os.path.join(KOR, 'istochniki', 'bch')
b3 = open(os.path.join(SRC, 'eccpage_bch3.c')).read()
lx = open(os.path.join(SRC, 'linux_lib_bch.c')).read()
# p(x) из bch3.c
import re
P_B3 = {}
for m, body in re.findall(r'if \(m == (\d+)\)\s*(p\[[^;]*= 1;)', b3):
    m = int(m); v = (1 << m) | 1
    for i in re.findall(r'p\[(\d+)\]', body): v |= 1 << int(i)
    P_B3[m] = v
tab = [int(x, 16) for x in re.search(r'prim_poly_tab\[\] = \{([^}]*)\}', lx).group(1).replace('\n', '').split(',') if x.strip()]
P_LX = {5 + i: v for i, v in enumerate(tab)}
assert P_B3[3] == 0b1011 and all(primitive(v) for v in list(P_B3.values()) + list(P_LX.values()))
HAN63 = {1: [[0, 1, 6]], 2: [[0, 1, 2, 4, 6]], 3: [[0, 1, 2, 5, 6]], 4: [[0, 3, 6]], 5: [[0, 2, 3]], 6: [[0, 2, 3, 5, 6]],
         7: [[0, 1, 3, 4, 6]], 10: [[0, 2, 4, 5, 6]], 11: [[0, 1, 2]], 13: [[0, 1, 4, 5, 6]], 15: [[0, 1, 3]]}  # BCH_code.pdf стр. 14


from rs_gf import GF as _GF
_GFC = {}


def minpoly(i, m, p):
    """Минимальный многочлен α^i: произведение (x + α^(i·2^j)) по циклотомическому классу (таблицы rs_gf.GF)."""
    n = (1 << m) - 1
    if (m, p) not in _GFC: _GFC[(m, p)] = _GF(m, p)
    gf = _GFC[(m, p)]
    cls = []; j = i % n
    while j not in cls: cls.append(j); j = (2 * j) % n
    poly = [1]
    for c in cls:
        r = gf.pw(c); ng = poly + [0]
        for k in range(len(poly)): ng[k + 1] ^= gf.mul(poly[k], r)
        poly = ng
    assert set(poly) <= {0, 1}
    v = 0
    for x in poly: v = (v << 1) | x
    return v, cls


def table(m, p, istochnik):
    n = (1 << m) - 1
    GF = galois.GF(2 ** m, irreducible_poly=galois.Poly.Int(p))
    a = GF(2)
    rows = []; g = 1; used = set(); t = 0; mins = []
    _mp = {}
    def MP(c):  # минимальный многочлен α^c средствами galois (кэш по циклотомическому классу)
        if c not in _mp: _mp[c] = (a ** c).minimal_poly()
        return _mp[c]
    def cyc(i, n):
        cl = []; j = i % n
        while j not in cl: cl.append(j); j = 2 * j % n
        return cl
    while True:
        t += 1
        for i in (2 * t - 1, 2 * t):
            mp, cls = minpoly(i, m, p)
            if min(cls) not in used:
                used.add(min(cls)); g = mul(g, mp); mins.append((min(cls), mp))
        k = n - deg(g)
        if k <= 0: break
        # закрепляем только наибольшее t при данном k
        if rows and rows[-1]['k'] == k:
            rows[-1]['t'] = t; rows[-1]['d_конструктивное'] = 2 * t + 1
        else:
            rows.append(dict(n=n, k=k, t=t, d_конструктивное=2 * t + 1))
        rows[-1].update(g_octal='%o' % g, g_hex='0x%X' % g, степень=deg(g), g_запись=poly_str(g) if deg(g) <= 40 else '',
                        мин_многочлены=['m%d=%o' % (c, mp) for c, mp in mins])
        rows[-1]['_g'] = g
    for r in rows:
        g = r.pop('_g')
        assert mod((1 << n) | 1, g) == 0
        co = [(g >> i) & 1 for i in range(deg(g) + 1)]
        gfo = _GFC[(m, p)]
        for i in range(1, 2 * r['t'] + 1):  # g(α^i) = 0 по Горнеру в таблицах поля
            v = 0
            for c in co[::-1]: v = gfo.mul(v, gfo.pw(i)) ^ c
            assert v == 0, (n, r['k'], i)
        # независимая сверка: НОК минимальных многочленов α^1…α^2t, вычисленных galois (MIT)
        L = galois.lcm(*[MP(min(cyc(i, n))) for i in range(1, 2 * r['t'] + 1)])
        gl = 0
        for x in L.coeffs: gl = (gl << 1) | int(x)
        assert gl == g, (n, r['k'])
        r['проверка'] = 'g | x^n+1; g(α^i)=0, i=1…2t; = НОК minimal_poly(α^i) библиотеки galois'
        if rows.index(r) in (0, len(rows) // 2) and m <= 8:
            B = galois.BCH(n, r['k'], extension_field=GF)
            gg = 0
            for x in B.generator_poly.coeffs: gg = (gg << 1) | int(x)
            assert gg == g and B.t == r['t'], (n, r['k'], B.t, r['t'])
            r['проверка'] += '; galois.BCH(%d,%d) — тот же g, t=%d' % (n, r['k'], B.t)
        if n == 63 and r['t'] in HAN63:
            pass
    if n == 63:
        # таблица Han: g_t = f · g_(t-1)
        gg = 1
        for t_, facs in sorted(HAN63.items()):
            for f in facs:
                v = 0
                for e in f: v |= 1 << e
                gg = mul(gg, v)
            r = next((r for r in rows if r['t'] == t_), None)
            assert r is not None and int(r['g_octal'], 8) == gg, t_
            r['проверка'] += '; = табл. Han (NTPU) стр. 14'
    return dict(m=m, n=n, p_hex='0x%X' % p, p_запись=poly_str(p), источник_p=istochnik, коды=rows)


res = []
for m in range(3, 11):
    res.append(table(m, P_B3[m], 'bch3.c (eccpage.com), строки 104–113'))
    if m in P_LX and P_LX[m] != P_B3[m]:
        res.append(table(m, P_LX[m], 'Linux lib/bch.c prim_poly_tab, строка 1287'))
json.dump(res, open(os.path.join(OUT, 'bch_primitivnye_m3_10.json'), 'w'), ensure_ascii=False, indent=0)
print('таблиц', len(res), 'кодов', sum(len(x['коды']) for x in res), [(x['m'], x['p_hex'], len(x['коды'])) for x in res])
print('P_B3', {k: hex(v) for k, v in P_B3.items()}, 'P_LX', {k: hex(v) for k, v in P_LX.items()})
