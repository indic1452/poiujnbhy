"""Независимая проверка выколотых свёрточных записей своим перечислением путей по решётке с фазой выкалывания:
RCPC Hagenauer (40: c_d — сумма информационных весов путей по всем P фазам, как в Table II), Yasuda (6: dfree),
комплементарные US6768778 (13: dfree и c_dfree/P), MIL-STD-188-141B (3: dfree). Обе ориентации многочленов
проверяются; засчитывается совпадение хотя бы одной (ориентация выводится)."""
import json, os, re, sys
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))

def spektr(polys, M, A, Dmax, shagov=400):
    """polys — строки '1101…' (длина M+1, символ 0 — коэффициент при текущем бите); A — матрица выкалывания n×P.
    Возвращает {d: [число путей, сумма инф. весов]} по всем фазам, d ≤ Dmax."""
    n = len(polys); P = len(A[0])
    G = [int(p, 2) for p in polys]  # бит M — текущий вход (символ 0 строки)
    res = {}
    for f0 in range(P):
        front = {}
        reg = (1 << M); w = sum(A[i][f0] * (bin(G[i] & reg).count('1') & 1) for i in range(n))
        front[(reg >> 1, (f0 + 1) % P)] = {w: [1, 1]}
        for _ in range(shagov):
            if not front: break
            nf = {}
            for (s, f), dd in front.items():
                for u in (0, 1):
                    reg = (u << M) | s
                    bw = sum(A[i][f] * (bin(G[i] & reg).count('1') & 1) for i in range(n))
                    ns = reg >> 1; key = (ns, (f + 1) % P)
                    for w, (c, iw) in dd.items():
                        W = w + bw
                        if W > Dmax: continue
                        if ns == 0:
                            r = res.setdefault(W, [0, 0]); r[0] += c; r[1] += iw + u
                        else:
                            t = nf.setdefault(key, {}).setdefault(W, [0, 0]); t[0] += c; t[1] += iw + u * c
            front = nf
        assert not front, 'не сошлось за %d шагов' % shagov
    return res
# исправление: сумма инф. весов при ветвлении — iw + u*c (для res тоже)
def spektr2(polys, M, A, Dmax):
    n = len(polys); P = len(A[0]); G = [int(p, 2) for p in polys]; res = {}
    for f0 in range(P):
        reg = 1 << M; w = sum(A[i][f0] * (bin(G[i] & reg).count('1') & 1) for i in range(n))
        front = {(reg >> 1, (f0 + 1) % P): {w: [1, 1]}}
        for _ in range(600):
            if not front: break
            nf = {}
            for (s, f), dd in front.items():
                for u in (0, 1):
                    reg = (u << M) | s
                    bw = sum(A[i][f] * (bin(G[i] & reg).count('1') & 1) for i in range(n))
                    ns = reg >> 1
                    tgt = res if ns == 0 else nf.setdefault((ns, (f + 1) % P), {})
                    for w, (c, iw) in dd.items():
                        W = w + bw
                        if W > Dmax: continue
                        t = tgt.setdefault(W, [0, 0]); t[0] += c; t[1] += iw + u * c
            front = {k: v for k, v in nf.items() if v}
        if front: res.setdefault('незамкнуто', []).append(f0)
    return res
def obr(p): return p[::-1]
osh = []; ok = 0; vsego = 0; orient = {}
def zasch(nm, good, info):
    global ok
    if good: ok += 1
    else: osh.append((nm, info))
for r in kat:
    f = r['семейство']; P_ = r['параметры']; nm = r['имя']
    if f.startswith('RCPC'):
        vsego += 1
        polys = re.match(r'([01, ]+)\(', P_['многочлены']).group(1).replace(' ', '').split(',')
        polys = [p for p in polys if p]
        M = len(polys[0]) - 1
        rows = re.search(r': ([01 /]+)$', P_['выкалывание']).group(1).split('/')
        A = [[int(ch) for ch in row.replace(' ', '')] for row in rows]
        cd = {int(k): v for k, v in P_.get('c_d (исправлено пересчётом)', P_['c_d']).items()}  # где статья с опечаткой — исправленные значения записи
        Dmax = max(cd)
        hit = None
        for name, pp in (('как в статье', polys), ('обратная', [obr(p) for p in polys])):
            s = spektr2(pp, M, A, Dmax)
            mine = {d: s.get(d, [0, 0])[1] for d in cd}
            if mine == cd and min(d for d in s if d != 'незамкнуто' and s[d][0]) == P_['dfree'] and 'незамкнуто' not in s: hit = name; break
        orient[hit] = orient.get(hit, 0) + 1
        zasch(nm, hit, (cd, mine))
    elif f.startswith('Лучшие шаблоны выкалывания'):
        vsego += 1
        rows = re.search(r'строка 1: (\d+), строка 2: (\d+)', P_['выкалывание']).groups()
        A = [[int(c) for c in x] for x in rows]
        polys = [format(0o133, '07b'), format(0o171, '07b')]
        hit = None
        for name, pp in (('прямая', polys), ('обратная', [obr(p) for p in polys])):
            s = spektr2(pp, 6, A, P_['dfree'] + 1)
            d = min((d for d in s if d != 'незамкнуто' and s[d][0]), default=None)
            if d == P_['dfree']: hit = name; break
        zasch(nm, hit, (d, P_['dfree']))
    elif f.startswith('Комплементарные'):
        vsego += 1
        g = re.findall(r'g\d=([01]{7})', P_['материнский'])
        rows = re.search(r'g1/g2/g3: ([01]+), ([01]+), ([01]+)', P_['выкалывание']).groups()
        Pp = len(rows[0]); A = [[int(c) for c in x] for x in rows]
        hit = None
        for name, pp in (('прямая', g), ('обратная', [obr(p) for p in g])):
            s = spektr2(pp, 6, A, P_['dfree'])
            d = min((d for d in s if d != 'незамкнуто' and s[d][0]), default=None)
            if d == P_['dfree'] and abs(s[d][1] / Pp - P_['c_d/P']) < 1e-9: hit = name; break
        zasch(nm, hit, (d, s.get(d), P_['dfree'], P_['c_d/P']))
    elif f.startswith('MIL-STD-188-141') and 'dfree' in P_:
        vsego += 1
        octs = re.search(r'восьм\. ([\d, ]+)\)', P_['многочлены']).group(1).split(',')
        K = int(re.search(r'K=(\d+)', nm).group(1))
        polys = [format(int(o, 8), '0%db' % K) for o in octs]
        s = spektr2(polys, K - 1, [[1]] * len(polys), P_['dfree'])
        d = min((d for d in s if d != 'незамкнуто' and s[d][0]), default=None)
        zasch(nm, d == P_['dfree'], (d, P_['dfree']))
print(json.dumps(dict(всего=vsego, сошлось=ok, ориентация_RCPC=orient, ошибок=osh[:20]), ensure_ascii=False, indent=0, default=str))
