"""Независимая проверка записей БЧХ (вторая реализация, без кода сборщика и без galois).
1) p(x) записи = p(x) в тексте bch3.c (разбор строк `else if (m == M) p[..] = 1;`) или prim_poly_tab Linux lib/bch.c;
2) g(x) = произведение минимальных многочленов циклотомических классов, задетых 1…2t (своя арифметика GF(2^m));
3) t в записи — наибольший t, дающий этот g (как в таблицах Lin–Costello/Stenbit);
4) k = n − deg g; d констр. = 2t+1; восьм./hex g совпадают.
"""
import json, os, re, sys
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))
src = open(os.path.join(KOR, 'istochniki/bch/eccpage_bch3.c')).read()
p_bch3 = {}
for m, body in re.findall(r'if \(m == (\d+)\)\s*((?:p\[\d+\]\s*=\s*)+1);', src):
    m = int(m); v = (1 << m) | 1
    for i in re.findall(r'p\[(\d+)\]', body): v |= 1 << int(i)
    p_bch3[m] = v
lin = open(os.path.join(KOR, 'istochniki/bch/linux_lib_bch.c')).read()
tab = re.search(r'prim_poly_tab\[\] = \{([^}]*)\}', lin).group(1)
p_lin = {5 + i: int(x, 16) for i, x in enumerate(re.findall(r'0x[0-9a-f]+', tab))}

def g_bch(m, p, t):
    n = (1 << m) - 1
    exp = [0] * (2 * n); a = 1
    for i in range(n):
        exp[i] = exp[i + n] = a; a <<= 1
        if a >> m: a ^= p
    assert len(set(exp[:n])) == n, 'p не примитивен'
    def mul_poly_lin(P, r):  # P — список коэф. в GF(2^m) (int), умножить на (x + α^r)
        out = [0] * (len(P) + 1)
        for i, c in enumerate(P):
            out[i + 1] ^= c
            if c:  # c·α^r
                lc = log[c]; out[i] ^= exp[(lc + r) % n]
        return out
    log = {exp[i]: i for i in range(n)}
    seen = set(); g = 1  # g как двоичный многочлен int
    for i in range(1, 2 * t + 1):
        if i % n in seen: continue
        cl = []; j = i % n
        while j not in cl: cl.append(j); j = (2 * j) % n
        seen.update(cl)
        P = [1]
        for r in cl: P = mul_poly_lin(P, r)
        assert all(c in (0, 1) for c in P)
        mp = sum(c << k for k, c in enumerate(P))
        # g *= mp над GF(2)
        r = 0; a = g; b = mp; s = 0
        while b:
            if b & 1: r ^= a << s
            b >>= 1; s += 1
        g = r
    return g

def poly_from_field(s):
    h = re.search(r'\((0x[0-9A-Fa-f]+)\)', s).group(1); return int(h, 16)

bch = [r for r in kat if r['вид'] == 'БЧХ']
ok = 0; osh = []
cache = {}
for r in bch:
    P = r['параметры']; m = int(re.search(r'GF\(2\^(\d+)\)', P['поле']).group(1)); p = poly_from_field(P['поле'])
    n, k = map(int, P['n,k'].split(',')); t = P['t']
    if p != p_bch3[m] and p != p_lin.get(m): osh.append((r['имя'], 'p(x) не из bch3.c/Linux'))
    g = g_bch(m, p, t)
    if g.bit_length() - 1 != n - k: osh.append((r['имя'], 'deg g', g.bit_length() - 1, n - k)); continue
    if oct(g)[2:] != P['g(x) восьмеричная'] or hex(g)[2:].upper() != P['g(x) hex'][2:].upper():
        osh.append((r['имя'], 'g не совпал')); continue
    # t наибольший для этого g
    g2 = g_bch(m, p, t + 1) if 2 * (t + 1) <= n else None
    if g2 == g: osh.append((r['имя'], 't не наибольший'))
    if P['d конструктивное'] != 2 * t + 1: osh.append((r['имя'], 'd'))
    ok += 1
# таблица: вариант m=8 с 0x11D
T = json.load(open(os.path.join(KOR, 'tablicy/bch/bch_primitivnye_m3_10.json')))
tok = 0
for e in T:
    p = int(e['p_hex'], 16)
    for c in e['коды']:
        g = g_bch(e['m'], p, c['t'])
        assert oct(g)[2:] == c['g_octal'] and c['n'] - c['k'] == g.bit_length() - 1, (e['m'], c)
        tok += 1
print(json.dumps(dict(записей=len(bch), сошлось=ok, ошибок=osh, строк_таблицы=tok,
                      p_bch3={m: hex(v) for m, v in p_bch3.items()}), ensure_ascii=False))
