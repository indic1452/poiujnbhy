"""Примитивные многочлены над GF(2) → tablicy/primitivnye/*.json.

1) Koopman, «Maximal Length LFSR Feedback Terms» (CMU): N.txt — первые 100 для степени N (4…64),
   N.dat.gz — полный список (4…22). Запись Koopman — «неявная +1»: явная = (v << 1) | 1.
   Проверки: (а) число многочленов в полном списке = φ(2^N − 1)/N (формула числа примитивных);
   (б) каждый из первых 100 (4…64) — примитивен (порядок x = 2^N − 1, разложение 2^N − 1 — sympy).
2) Živković, «A table of primitive binary polynomials» (Math. Comp. 62 (1994) 385–386; полный текст
   primpol1.pdf): для n < 5000 — первый примитивный трёхчлен, случайный 5- и 7-член.
   Проверки: n ≤ 22 — входит в полный список Koopman (второй источник); n ≤ 100 — примитивен (расчёт);
   n ≤ 300 — неприводим (расчёт).
"""
import gzip, json, os, re, sys, time
import pymupdf, sympy
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf2 import primitive, irreducible, reverse, deg, poly_str
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'primitivnye')
OUT = os.path.join(KOR, 'tablicy', 'primitivnye'); os.makedirs(OUT, exist_ok=True)

polnye = {}
koop = {}
for N in range(4, 65):
    f = os.path.join(IST, 'koopman_lfsr', '%d.txt' % N)
    vals = [int(l.strip(), 16) for l in open(f) if re.fullmatch(r'[0-9A-Fa-f]+', l.strip())]
    ex = [(v << 1) | 1 for v in vals]
    for e in ex:
        assert deg(e) == N and primitive(e), (N, hex(e))
    koop[N] = dict(файл='istochniki/primitivnye/koopman_lfsr/%d.txt' % N, koopman=['0x%X' % v for v in vals], явная=['0x%X' % e for e in ex])
    g = os.path.join(IST, 'koopman_lfsr', '%d.dat.gz' % N)
    if os.path.exists(g) and N <= 22:
        allv = [int(l.strip(), 16) for l in gzip.open(g, 'rt') if re.fullmatch(r'[0-9A-Fa-f]+', l.strip())]
        want = sympy.totient((1 << N) - 1) // N
        assert len(allv) == want, (N, len(allv), want)
        polnye[N] = {(v << 1) | 1 for v in allv}
        koop[N]['полный_список'] = 'istochniki/primitivnye/koopman_lfsr/%d.dat.gz' % N
        koop[N]['всего'] = len(allv)
json.dump(koop, open(os.path.join(OUT, 'koopman_lfsr_pervye100.json'), 'w'), ensure_ascii=False, indent=0)
print('Koopman: степени 4..64 проверены; полные списки', sorted(polnye))

# Živković
d = pymupdf.open(os.path.join(IST, 'zivkovic_primpol1.pdf'))
ziv = {}
# поток чисел таблицы 1 (стр. 3–19): строка = n, затем показатели (< n) до следующего n; 9 чисел — трёхчлен(1)+5-член(3)+7-член(5),
# 8 — 5-член+7-член; для n = 2…6 — укороченные строки
for pi in range(2, len(d)):
    t = d[pi].get_text()
    if 'Table 1:' not in t: continue
    i = t.find('\nn\n'); body = t[i + 3:]; body = body[:body.find('Table')]
    toks = [int(x) for x in re.findall(r'\d+', body)]
    k = 0
    while k < len(toks):
        n = toks[k]; k += 1; r = []
        while k < len(toks) and toks[k] < n: r.append(toks[k]); k += 1
        if n < 2 or n >= 5000 or n in ziv: continue
        if len(r) == 9: tri, pen, sep = r[:1], r[1:4], r[4:]
        elif len(r) == 8: tri, pen, sep = None, r[:3], r[3:]
        elif len(r) == 4: tri, pen, sep = r[:1], r[1:], None
        elif len(r) == 1: tri, pen, sep = r, None, None
        else: raise AssertionError((n, r))
        ziv[n] = dict(n=n, трёхчлен=tri, пятичлен=pen, семичлен=sep, стр_pdf=pi + 1)
assert all(ziv[a]['n'] < ziv[b]['n'] for a, b in zip(sorted(ziv), sorted(ziv)[1:]))


def mk(n, mid):
    v = (1 << n) | 1
    for a in mid: v |= 1 << a
    return v


t0 = time.time(); stat = dict(koopman=0, прим=0, неприв=0)
for n, z in sorted(ziv.items()):
    z['проверка'] = []
    for key in ('трёхчлен', 'пятичлен', 'семичлен'):
        if not z[key]: continue
        v = mk(n, z[key]); z[key + '_hex'] = '0x%X' % v; z[key + '_запись'] = poly_str(v)
        if n in polnye:
            assert v in polnye[n] or reverse(v) in polnye[n], (n, key, z[key]); stat['koopman'] += 1
            z['проверка'].append(key + ': есть в полном списке Koopman')
        if n <= 100:
            assert primitive(v), (n, key); stat['прим'] += 1
            z['проверка'].append(key + ': примитивен (расчёт)')
        elif n <= 400:
            assert irreducible(v), (n, key); stat['неприв'] += 1
            z['проверка'].append(key + ': неприводим (расчёт)')
json.dump([ziv[n] for n in sorted(ziv)], open(os.path.join(OUT, 'zivkovic1994.json'), 'w'), ensure_ascii=False, indent=0)
print('Živković: степеней', len(ziv), 'от', min(ziv), 'до', max(ziv), stat, '%.0f c' % (time.time() - t0))
print('степеней в таблице Živković (n < 5000, разложение 2^n−1 известно):', len(ziv))
