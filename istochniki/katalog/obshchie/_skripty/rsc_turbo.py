"""Лучшие составляющие (RSC) коды для турбокодов: Divsalar & Pollara, «On the design of turbo codes», TDA PR 42-123 (1995),
табл. 1–6 → tablicy/turbo/rsc_jpl42-123.json, zapisi/rsc.json.
Проверка (assert): d2, d3 (наименьший вес кодового слова при весе входа 2 и 3) и dmin пересчитаны поиском по решётке
(Дейкстра по (состояние, вес входа)); для табл. 4 — с выкалыванием проверочного бита по P=[10]. Многочлены — восьм., k = m+1."""
import heapq, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stranica
F = 'istochniki/turbo_ra/jpl_42-123D_design_turbo.pdf.txt'
t = open(os.path.join(KOR, F)).read()
flat = re.sub(r'\s+', ' ', t)


def bits(v, m): return [(v >> j) & 1 for j in range(m + 1)]   # коэффициенты при D^0…D^m (старший бит восьм. записи — D^m? см. ниже)


def search_1n(g, m, punct=None, maxin=3):
    """RSC (1, g1/g0, …): контроллерная форма. g — [g0, g1, …] целые, бит j — коэффициент при D^j (g0 бит 0 = 1).
    punct — шаблон для проверочных битов по времени (список 0/1), систематический передаётся всегда."""
    g0 = g[0]; S = 1 << m
    def step(s, u, t):
        # s: биты w_{t-1}…w_{t-m} (бит j-1 = w_{t-j})
        fb = u
        for j in range(1, m + 1):
            if (g0 >> j) & 1: fb ^= (s >> (j - 1)) & 1
        w = fb
        full = (s << 1) | w   # бит0 = w_t, бит j = w_{t-j}
        out = u
        for gi in g[1:]:
            x = bin(full & gi).count('1') & 1
            if punct is None or punct[t % len(punct)]: out += x
        return full & (S - 1), out
    best = {}
    P = len(punct) if punct else 1
    for ph in range(P):
        pq = []
        s, w = step(0, 1, ph)
        heapq.heappush(pq, (w, 1, s, ph + 1))
        seen = {}
        while pq:
            w, win, s, tt = heapq.heappop(pq)
            if s == 0:
                if win not in best or w < best[win]: best[win] = w
                continue
            key = (s, win, tt % P)
            if seen.get(key, 1 << 30) <= w: continue
            seen[key] = w
            if w > 60: continue
            for u in (0, 1):
                if win + u > maxin: continue
                ns, dw = step(s, u, tt)
                heapq.heappush(pq, (w + dw, win + u, ns, tt + 1))
    return best


def search_bn(h, m, maxin=3):
    """Систематический b/(b+1): h0·x = Σ h_i·u_i (наблюдательная форма). h — [h0…hb], бит j — коэффициент при D^j."""
    b = len(h) - 1; S = 1 << m
    def step(s, us):
        x = (s & 1)
        for i in range(b):
            if us[i] and (h[i + 1] & 1): x ^= 1
        ns = 0
        for j in range(m):
            v = (s >> (j + 1)) & 1 if j + 1 < m else 0
            for i in range(b):
                if us[i] and (h[i + 1] >> (j + 1)) & 1: v ^= 1
            if x and (h[0] >> (j + 1)) & 1: v ^= 1
            ns |= v << j
        return ns, sum(us) + x
    import itertools
    inputs = list(itertools.product((0, 1), repeat=b))
    best = {}
    pq = []
    for us in inputs:
        if not any(us): continue
        s, w = step(0, us)
        heapq.heappush(pq, (w, sum(us), s))
    seen = {}
    while pq:
        w, win, s = heapq.heappop(pq)
        if s == 0:
            if win not in best or w < best[win]: best[win] = w
            continue
        if seen.get((s, win), 1 << 30) <= w: continue
        seen[(s, win)] = w
        if w > 60: continue
        for us in inputs:
            if win + sum(us) > maxin: continue
            ns, dw = step(s, us)
            heapq.heappush(pq, (w + dw, win + sum(us), ns))
    return best


def rev(v, m):  # восьмеричная запись: старший бит — D^0? проверяем обе ориентации
    return int(format(v, '0%db' % (m + 1))[::-1], 2)


tabl = []
for tn, rate in ((1, '2/3'), (2, '3/4'), (3, '4/5'), (4, '1/2 (выкол. P=[10])'), (5, '1/3'), (6, '1/4')):
    a = flat.find('Table %d. Best rate' % tn, flat.find('Table %d. Best rate' % tn) + 1) if flat.count('Table %d. Best rate' % tn) > 1 else flat.find('Table %d. Best rate' % tn)
    seg = flat[a:a + 900]
    seg = seg[:seg.find('Table %d' % (tn + 1), 10)] if ('Table %d' % (tn + 1)) in seg[10:] else seg
    seg = re.split(r'\s(?:[A-Z]\.|\d{3}\s+=====)', seg)[0]
    # строки: [k] h0 = .. h1 = .. … d2 d3 dmin
    for m_ in re.finditer(r'(?:(\d)\s+)?((?:[hg]\d\s*=\s*\d+\s*)+)(\d+|∞)\s+(\d+|∞)\s+(\d+)', seg):
        gens = [int(x, 8) for x in re.findall(r'[hg]\d\s*=\s*(\d+)', m_.group(2))]
        d2, d3, dmin = m_.group(3), m_.group(4), int(m_.group(5))
        tabl.append(dict(табл=tn, скорость=rate, k=None, gens=gens, d2=d2, d3=d3, dmin=dmin, стр=stranica(F, 'Table %d. Best rate' % tn)))
# k: по старшей степени
res = []
for r in tabl:
    m = max(g.bit_length() for g in r['gens']) - 1
    r['k'] = m + 1
    ok = None
    for orient in ('D^0 — младший бит', 'D^0 — старший бит'):
        G = r['gens'] if orient.endswith('младший бит') else [rev(g, m) for g in r['gens']]
        if r['табл'] in (1, 2, 3): best = search_bn(G, m); full = search_bn(G, m, maxin=8)
        elif r['табл'] == 4: best = search_1n(G, m, punct=[1, 0]); full = search_1n(G, m, punct=[1, 0], maxin=8)
        else: best = search_1n(G, m); full = search_1n(G, m, maxin=8)
        d2c = best.get(2); d3c = best.get(3); dm = min(full.values())
        fit = (str(d2c) == r['d2']) and (r['d3'] == '∞' and d3c is None or str(d3c) == r['d3']) and dm == r['dmin']
        if fit: ok = (orient, d2c, d3c, dm); break
        last = (d2c, d3c, dm)
    r['сверка'] = ok
    if not ok: r['пересчёт'] = dict(zip(('d2', 'd3', 'dmin'), last))
    res.append(r)
bad = [r for r in res if not r['сверка']]
for r in bad: print('НЕ СОШЛОСЬ', r)
print('кодов', len(res), 'сошлось', len(res) - len(bad))
json.dump(res, open(os.path.join(KOR, 'tablicy', 'turbo', 'rsc_jpl42-123.json'), 'w'), ensure_ascii=False, indent=1)
zap = []
for r in res:
    if not r['сверка']:
        r['сверка'] = ('—', ) + tuple(r['пересчёт'].values())
    nm = ('h' if r['табл'] <= 3 else 'g')
    gs = ', '.join('%s%d=%o' % (nm, i, g) for i, g in enumerate(r['gens']))
    zap.append(Z('RSC для турбокода R=%s, k=%d (%s)' % (r['скорость'], r['k'], gs), 'Лучшие составляющие RSC (Divsalar–Pollara, JPL 42-123)', 'рекурсивный систематический свёрточный (для турбо PCCC)',
                 {'n,k': r['скорость'], 'K': r['k'], 'многочлены': gs + ' (восьм.; g0/h0 — обратная связь)', 'd2': r['d2'], 'd3': r['d3'], 'dmin': r['dmin'],
                  'ориентация': r['сверка'][0], 'выкалывание': 'P=[10] по проверочному биту' if r['табл'] == 4 else 'нет', 'таблица': 'tablicy/turbo/rsc_jpl42-123.json'},
                 'составляющие коды турбокодов (CCSDS турбо — 23/33/25/37, JPL); выбор g0 примитивным',
                 [I(F[:-4], 'стр. %d PDF, Table %d' % (r['стр'], r['табл']), 'https://ipnpr.jpl.nasa.gov/progress_report/42-123/123D.pdf')],
                 ('d2=%s, d3=%s, dmin=%d пересчитаны поиском по решётке — совпали с таблицей' % (r['сверка'][1], r['сверка'][2], r['сверка'][3])) if 'пересчёт' not in r else
                 'РАСХОЖДЕНИЕ с таблицей: пересчёт d2=%s, d3=%s, dmin=%s (в таблице %s/%s/%s) — вероятная опечатка в строке таблицы или в её чтении; код не рекомендован без перепроверки' % (r['пересчёт']['d2'], r['пересчёт']['d3'], r['пересчёт']['dmin'], r['d2'], r['d3'], r['dmin']),
                 'ЕСТЬ в проекте: svyortka.кодировать_rsc, turbo.py (RSC/PCCC вслепую), turbo_std.py' if r['табл'] >= 4 else 'ЧАСТИЧНО: RSC b/(b+1) с несколькими входами в проекте не опознаются (turbo.py — двоичный и дуобинарный DVB-RCS)'))
sohranit('rsc', zap)
