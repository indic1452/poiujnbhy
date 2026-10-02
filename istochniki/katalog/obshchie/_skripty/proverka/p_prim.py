"""Проверка записей «Примитивный многочлен степени n»: основной и «также» многочлены примитивны
(свой тест: x^(2^n−1) ≡ 1 и x^((2^n−1)/q) ≢ 1 для всех простых q | 2^n−1; разложение — sympy/перебор),
hex = запись; многочлен есть в таблице Živković (tablicy/primitivnye/zivkovic1994.json, собрана из OCR) или в полном
списке Koopman (степени ≤ 22, где он есть)."""
import json, os, re
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
from sympy import factorint
kat = json.load(open(os.path.join(KOR, 'katalog.json')))
def pmulmod(a, b, p, n):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if a >> n & 1: a ^= p
    return r
def ppow(e, p, n):
    r = 1; a = 2
    while e:
        if e & 1: r = pmulmod(r, a, p, n)
        a = pmulmod(a, a, p, n); e >>= 1
    return r
def primitive(p):
    n = p.bit_length() - 1; N = (1 << n) - 1
    if ppow(N, p, n) != 1: return False
    return all(ppow(N // q, p, n) != 1 for q in factorint(N))
def parse(s):
    v = 0
    for t in s.replace(' ', '').split('+'):
        if t == '1': v |= 1
        elif t == 'x': v |= 2
        else: v |= 1 << int(t.split('^')[1])
    return v
ziv = {e['n']: e for e in json.load(open(os.path.join(KOR, 'tablicy/primitivnye/zivkovic1994.json')))}
osh = []; ok = 0; n_pol = 0
for r in kat:
    if not r['имя'].startswith('Примитивный многочлен степени'): continue
    P = r['параметры']; n = int(re.search(r'степени (\d+)', r['имя']).group(1))
    vs = [P['многочлен']] + list(P.get('также', {}).values())
    good = True
    for s in vs:
        v = parse(s); n_pol += 1
        if v.bit_length() - 1 != n or not primitive(v): osh.append((r['имя'], s, 'не примитивен')); good = False
    if parse(P['многочлен']) != int(P['hex'], 16): osh.append((r['имя'], 'hex')); good = False
    e = ziv.get(n)
    if e:
        z = set()
        for kk in ('трёхчлен', 'пятичлен', 'семичлен'):
            if e.get(kk): z.add(sum(1 << i for i in e[kk]) | 1 | (1 << n))
        if parse(P['многочлен']) not in z: osh.append((r['имя'], 'нет в таблице Živković', [hex(x) for x in z]))
    ok += good
print(json.dumps(dict(записей=ok, многочленов=n_pol, ошибок=osh), ensure_ascii=False, indent=0))
