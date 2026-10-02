"""Независимая проверка LDPC-записей (MacKay, Gallager, AFF3CT): свой разбор alist (строки M списков проверок),
ранг H над GF(2) — гауссово исключение на целых-битсетах; сравнение с «ранг H», N, M, весами и n,k записи (N ≤ 20000)."""
import json, os, re, gzip
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))
def chitat(p):
    op = gzip.open if p.endswith('.gz') else open
    t = ' '.join(l for l in op(p, 'rt').read().split('\n') if not l.lstrip().startswith('#')).split()
    v = list(map(int, t)); i = 0
    N, M = v[0], v[1]; i = 2
    mc, mr = v[i], v[i + 1]; i += 2
    cw = v[i:i + N]; i += N; rw = v[i:i + M]; i += M
    cols = []
    for c in range(N):
        cols.append([x for x in v[i:i + mc] if x > 0]); i += mc
    rows = [0] * M
    for c, lst in enumerate(cols):
        for r in lst: rows[r - 1] |= 1 << c
    return N, M, cw, rw, rows
def rang(rows):
    piv = {}; r = 0
    for x in rows:
        while x:
            h = x.bit_length() - 1
            if h in piv: x ^= piv[h]
            else: piv[h] = x; r += 1; break
    return r
chuzhoy = []; ok = 0; osh = []; vsego = 0; propusk = 0
for z in kat:
    P = z['параметры']; f = P.get('матрица (alist)')
    if not f or z['вид'] != 'LDPC': continue
    fp = os.path.join(KOR, f)
    try: N, M, cw, rw, rows = chitat(fp)
    except Exception as e:
        # не классический alist (без нулевого дополнения списков и т. п.) — разбор кодом сборщика, ранг — свой
        import sys; sys.path.insert(0, os.path.join(KOR, 'skripty'))
        from ldpc_alist import read_alist
        N, M, cw, rw, rws, vid = read_alist(fp)
        rows = [sum(1 << c for c in r) for r in rws] if rws and isinstance(rws[0], (list, tuple, set)) else rws
        chuzhoy.append(z['имя'])
    if N > 20000: propusk += 1; continue
    vsego += 1
    rk = rang(rows); n, k = map(int, P['n,k'].split(','))
    bad = []
    if P.get('ранг H') is not None and rk != P['ранг H']: bad.append(('ранг', rk, P['ранг H']))
    if n != N or k != N - rk: bad.append(('n,k', N, N - rk, P['n,k']))
    if sorted(set(cw)) != sorted(P.get('веса столбцов', sorted(set(cw)))): bad.append(('веса столбцов', sorted(set(cw))))
    if sorted(set(rw)) != sorted(P.get('веса строк', sorted(set(rw)))): bad.append(('веса строк', sorted(set(rw))))
    if bad: osh.append((z['имя'], bad))
    else: ok += 1
print(json.dumps(dict(проверено=vsego, сошлось=ok, пропущено_N_больше_20000=propusk, разобрано_кодом_сборщика=len(chuzhoy), ошибок=osh), ensure_ascii=False, indent=0))
