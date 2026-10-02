"""Алгебраические блочные коды общего вида: Хэмминг (циклический, n=2^m−1), расширенный Хэмминг, Рида — Маллера RM(r,m),
квадратично-вычетные (QR) — определения из открытого кода Sage (GPL) и программ bch3.c / Linux lib/bch.c (p(x)).
→ tablicy/algebr/*.json, zapisi/algebr.json.
Проверки (assert): Хэмминг — g = примитивный p(x) делит x^n+1, d=3 (все одиночные синдромы различны и ненулевые);
RM — k = Σ C(m,i), двойственность RM(r,m)⊥ = RM(m−r−1,m) (G·G'ᵀ = 0, сумма размерностей = n), d = 2^(m−r) перебором при k ≤ 16;
QR — g(x) = ∏(x − β^i) по квадратичным вычетам двоичен и делит x^p − 1, k = (p+1)/2, d перебором при k ≤ 16."""
import itertools, json, os, re, sys
from math import comb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf2 import mod, deg, poly_str, primitive, mul
from rs_gf import GF
from zap import KOR, I, Z, sohranit, stroka
OUT = os.path.join(KOR, 'tablicy', 'algebr')
SG = 'istochniki/kod/sage/src/sage/coding/'
b3 = open(os.path.join(KOR, 'istochniki/bch/eccpage_bch3.c')).read()
P = {}
for m, body in re.findall(r'if \(m == (\d+)\)\s*(p\[[^;]*= 1;)', b3):
    m = int(m); v = (1 << m) | 1
    for i in re.findall(r'p\[(\d+)\]', body): v |= 1 << int(i)
    P[m] = v
zap = []; res = {'hamming': [], 'rm': [], 'qr': []}
# --- Хэмминг ---
for m in range(3, 11):
    p = P[m]; n = (1 << m) - 1
    assert primitive(p) and mod((1 << n) | 1, p) == 0
    # столбцы H циклического кода — x^j mod p, j=0…n−1: все различны и ненулевы → d = 3
    cols = [mod(1 << j, p) for j in range(n)]
    assert len(set(cols)) == n and 0 not in cols
    res['hamming'].append(dict(m=m, n=n, k=n - m, g_hex=hex(p), g=poly_str(p)))
    zap.append(Z('Хэмминг циклический (%d,%d), g(x) = %s' % (n, n - m, poly_str(p)), 'Коды Хэмминга', 'Хэмминг',
                 {'n,k': '%d,%d' % (n, n - m), 'd': 3, 'многочлен': '%s (0x%X) — примитивный, как p(x) в bch3.c' % (poly_str(p), p),
                  'расширенный': '(%d,%d), d=4 — добавлением общего бита чётности' % (n + 1, n - m), 'замечание': 'любой примитивный многочлен степени m даёт эквивалентный код (таблица — tablicy/primitivnye)'},
                 'память (SEC), ТКБ (строки/столбцы расширенного Хэмминга), BPTC DMR, коды заголовков; кандидаты слепого опознания',
                 [I('istochniki/bch/eccpage_bch3.c', 'строки %d–%d (p(x) по m)' % (stroka('istochniki/bch/eccpage_bch3.c', 'if (m == 2)'), stroka('istochniki/bch/eccpage_bch3.c', 'if (m == 20)'))),
                  I(SG + 'hamming_code.py', 'класс HammingCode (определение через проверочную матрицу)', 'https://github.com/sagemath/sage/blob/develop/src/sage/coding/hamming_code.py')],
                 'p(x) примитивен и делит x^n+1; все n столбцов H = x^j mod p(x) различны и ненулевы ⇒ d = 3 (расчёт)',
                 'ЕСТЬ в проекте: kod.ИЗВЕСТНЫЕ (7,4), (15,11), (31,26), (63,57), (127,120); ispravlenie.py; tpc.py (расширенные (16,11)…(256,247))'))
# --- RM ---
def rm_gen(r, m):
    pts = list(itertools.product((0, 1), repeat=m))
    rows = []
    for d in range(r + 1):
        for S in itertools.combinations(range(m), d):
            rows.append([int(all(p[i] for i in S)) for p in pts])
    return rows
def rank2(rows):
    piv = {}; r = 0
    for row in rows:
        v = int(''.join(map(str, row)), 2)
        while v:
            h = v.bit_length() - 1
            if h in piv: v ^= piv[h]
            else: piv[h] = v; r += 1; break
    return r
def mind(rows):
    n = len(rows[0]); vec = [int(''.join(map(str, r)), 2) for r in rows]; best = n
    for c in range(1, 1 << len(vec)):
        w = 0
        for i in range(len(vec)):
            if c >> i & 1: w ^= vec[i]
        best = min(best, bin(w).count('1'))
    return best
for m in range(1, 11):
    for r in range(0, m + 1):
        n = 1 << m; k = sum(comb(m, i) for i in range(r + 1)); d = 1 << (m - r)
        z = dict(r=r, m=m, n=n, k=k, d=d)
        if m <= 7:
            G = rm_gen(r, m); assert len(G) == k and rank2(G) == k
            if r < m:
                Gd = rm_gen(m - r - 1, m)
                assert all(sum(a & b for a, b in zip(x, y)) % 2 == 0 for x in G for y in Gd) and k + len(Gd) == n
                z['двойственность'] = 'RM(%d,%d)' % (m - r - 1, m)
            if k <= 16: assert mind(G) == d, (r, m); z['d_перебором'] = True
        res['rm'].append(z)
        if m <= 10 and 1 <= r <= m - 1 or (r == 1 and m <= 10):
            pass
for z in res['rm']:
    if z['r'] in (0, z['m']): continue
    zap.append(Z('Рида — Маллера RM(%d,%d): (%d,%d), d=%d' % (z['r'], z['m'], z['n'], z['k'], z['d']), 'Коды Рида — Маллера', 'РМ',
                 {'n,k': '%d,%d' % (z['n'], z['k']), 'd': z['d'], 'построение': 'значения всех мономов степени ≤ r от m переменных во всех 2^m точках (Sage ReedMullerCode)',
                  'двойственный': z.get('двойственность', 'RM(%d,%d)' % (z['m'] - z['r'] - 1, z['m'])), 'особые': 'RM(1,m) — двоичный ортогональный/биортогональный; RM(m−2,m) — расширенный Хэмминг'},
                 ('5G NR/LTE: подкоды RM(1,5)/(32,11)/(32,O) для UCI/CFI/TFCI — записи mobilnaya; ' if (z['m'], z['r']) == (5, 1) else '') + 'общая теория; связь с полярными кодами (правило RM, Arikan разд. X)',
                 [I(SG + 'reed_muller_code.py', 'строки 272 (размерность), 306–310 (минимальное расстояние)', 'https://github.com/sagemath/sage/blob/develop/src/sage/coding/reed_muller_code.py')],
                 ('порождающая матрица построена; ранг = k; ' if z['m'] <= 7 else 'параметры по формулам (m > 7 — без построения); ') + ('двойственность проверена; ' if 'двойственность' in z else '') + ('d перебором = 2^(m−r)' if z.get('d_перебором') else 'd = 2^(m−r) (формула Sage)'),
                 'ЧАСТИЧНО: kod.блочный/dlinnye найдут линейный код вслепую (проверки), отдельного декодера РМ (мажоритарного/FHT) нет — есть kod.уолш'))
# --- QR ---
from math import gcd
for p in [x for x in range(7, 200) if all(x % d for d in range(2, int(x ** 0.5) + 1)) and x % 8 in (1, 7)]:
    m = 1
    while (2 ** m - 1) % p: m += 1
    if m > 16: continue
    gf = GF(m, next(v for v in range((1 << m) + 1, 1 << (m + 1)) if primitive(v)))
    beta = gf.pw(((1 << m) - 1) // p)
    QRs = sorted(set(i * i % p for i in range(1, p)))
    g = [1]
    for i in QRs:
        r = gf.exp[(gf.log[beta] * i) % ((1 << m) - 1)]
        ng = g + [0]
        for j in range(len(g)): ng[j + 1] ^= gf.mul(g[j], r)
        g = ng
    assert set(g) <= {0, 1}
    gv = int(''.join(map(str, g)), 2)
    assert mod((1 << p) | 1, gv) == 0 and deg(gv) == (p - 1) // 2
    k = (p + 1) // 2
    z = dict(p=p, n=p, k=k, g_hex=hex(gv), g=poly_str(gv), m_поля=m)
    if k <= 16:
        rows = [[(gv << s) >> j & 1 for j in range(p)] for s in range(k)]
        z['d'] = mind(rows); z['d_перебором'] = True
    res['qr'].append(z)
    zap.append(Z('Квадратично-вычетный QR(%d) (%d,%d)%s' % (p, p, k, ', d=%d' % z['d'] if 'd' in z else ''), 'Квадратично-вычетные коды', 'циклический (QR)',
                 {'n,k': '%d,%d' % (p, k), 'g(x)': '%s (0x%X)' % (z['g'], gv), 'построение': 'g = ∏(x − β^i), i — квадратичные вычеты mod %d, β — корень степени %d из 1 в GF(2^%d)' % (p, p, m),
                  'расширенный': '(%d,%d) добавлением общего бита чётности' % (p + 1, k), 'особые': {7: 'Хэмминг (7,4)', 23: 'Голей (23,12)', 17: 'QR(17) — в DMR/NXDN используется укороченный (16,7) из запись mobilnaya'}.get(p, '')},
                 'теория; QR(23) = Голей; QR(17)/(16,7) — DMR', [I(SG + 'code_constructions.py', 'строки %d–%d (QuadraticResidueCode)' % (stroka(SG + 'code_constructions.py', 'def QuadraticResidueCode(n, F):'), stroka(SG + 'code_constructions.py', 'def QuadraticResidueCode(n, F):') + 40), 'https://github.com/sagemath/sage/blob/develop/src/sage/coding/code_constructions.py')],
                 'g двоичен, deg g = (p−1)/2, g | x^p − 1 (расчёт)' + ('; d=%d перебором' % z['d'] if 'd' in z else ''),
                 'ЧАСТИЧНО: rs_bch.py опознаёт циклические коды по корням (QR — не БЧХ: корни не подряд, нужен общий режим); ispravlenie.py — синдромное исправление'))
json.dump(res, open(os.path.join(OUT, 'hamming_rm_qr.json'), 'w'), ensure_ascii=False, indent=1)
print('Хэмминг', len(res['hamming']), 'RM', len(res['rm']), 'QR', [(z['p'], z.get('d')) for z in res['qr']])
sohranit('algebr', zap)
