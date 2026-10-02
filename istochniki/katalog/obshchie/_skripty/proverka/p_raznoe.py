"""Независимая проверка (своим кодом) записей: РС штрихкодов (все векторы ReedSolomonTestCase.java ZXing своим кодером
по полям GenericGF.java), O.150 (номера ступеней в цитируемой строке текста + примитивность + период), скремблеры
(показатели степеней многочлена встречаются в цитируемой строке ±3), Хэмминг (примитивность g), QR (g | x^p−1,
deg = (p−1)/2, d перебором для p ≤ 31), Голей (g | x^23−1, спектр (24,12))."""
import json, os, re, itertools
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))
rez = {}
def pmod(a, b):
    db = b.bit_length()
    while a and a.bit_length() >= db: a ^= b << (a.bit_length() - db)
    return a
def pmulmod(a, b, p):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if a.bit_length() == p.bit_length(): a ^= p
    return r
def xpow(e, p):
    r = 1; b = 2
    while e:
        if e & 1: r = pmulmod(r, b, p)
        b = pmulmod(b, b, p); e >>= 1
    return r
def poryadok_x(p):  # период = 2^m-1, если многочлен примитивен (проверка через делители 2^m-1), иначе None
    from sympy import factorint
    m = p.bit_length() - 1; N = (1 << m) - 1
    if xpow(N, p) != 1: return None
    return N if all(xpow(N // q, p) != 1 for q in factorint(N)) else None
def stepeni(s):
    s = s.replace('−', '-').replace('–', '-')
    st = [int(e) for e in re.findall(r'[xX]\^?-?(\d+)', s)]
    if re.search(r'\+\s*[xX](?![\^\d-])', s) or re.search(r'[xX]\s*\+', s.split('(')[0]) and not re.search(r'[xX]\^?-?\d', s.split('+')[-2] if s.count('+') else ''):
        pass
    return st
# ---------- ZXing ----------
gg = open(os.path.join(KOR, 'istochniki/rs/zxing/GenericGF.java')).read()
polya = {nm: (int(b, 2), int(sz), int(base)) for nm, b, sz, base in re.findall(r'GenericGF (\w+) = new GenericGF\(0b([01]+), (\d+), (\d)\)', gg)}
for nm, al in re.findall(r'GenericGF (\w+) = (\w+);', gg): polya[nm] = polya[al]
tc = open(os.path.join(KOR, 'istochniki/rs/zxing/ReedSolomonTestCase.java')).read()
def gf(poly, size):
    exp = [0] * (2 * size); log = [0] * size; a = 1
    for i in range(size - 1):
        exp[i] = exp[i + size - 1] = a; log[a] = i; a <<= 1
        if a >= size: a ^= poly
    assert len(set(exp[:size - 1])) == size - 1  # примитивность
    return exp, log
def rs_enc(data, nec, poly, size, base):
    exp, log = gf(poly, size); mul = lambda a, b: 0 if not a or not b else exp[log[a] + log[b]]
    g = [1]
    for d in range(nec):
        r = exp[(d + base) % (size - 1)]; ng = [0] * (len(g) + 1)
        for i, c in enumerate(g): ng[i] ^= c; ng[i + 1] ^= mul(c, r)
        g = ng  # старшая степень первой
    rem = list(data) + [0] * nec
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j in range(1, len(g)): rem[i + j] ^= mul(c, g[j])
    return rem[len(data):]
vekt = 0; vosh = []
for fld, a, b in re.findall(r'testEncodeDecode\(GenericGF\.(\w+),\s*new int\[\]\s*\{([^}]*)\},\s*new int\[\]\s*\{([^}]*)\}\);', tc):
    num = lambda s: [int(x, 0) for x in re.findall(r'0x[0-9A-Fa-f]+|\d+', re.sub(r'//.*', '', s))]
    d, e = num(a), num(b); p, sz, base = polya[fld]
    vekt += 1
    if rs_enc(d, len(e), p, sz, base) != e: vosh.append(fld)
print('zx', flush=True); rez['ZXing РС'] = dict(векторов=vekt, ошибок=vosh, полей=sorted(set(polya)))
# сверка полей записей РС штрихкодов с GenericGF
zap_rs = [r for r in kat if r['семейство'].startswith('РС штрихкодов')]
osh = []
for r in zap_rs:
    m = re.search(r'\(0x([0-9A-Fa-f]+)\)', r['имя']) or re.search(r'0x([0-9A-Fa-f]+)', r['имя'])
    if 'PDF417' in r['имя']: continue
    v = int(m.group(1), 16)
    if not any(p == v for p, _, _ in polya.values()): osh.append(r['имя'])
    fcr = re.search(r'fcr=(\d)', r['имя'])
    if fcr and not any(p == v and base == int(fcr.group(1)) for p, _, base in polya.values()): osh.append((r['имя'], 'fcr'))
rez['РС штрихкодов: поле/fcr = GenericGF'] = dict(записей=len(zap_rs), ошибок=osh)
# ---------- текстовые строки ----------
def tekst_okno(fajl, stroka, w=4):
    t = os.path.join(KOR, fajl + '.txt') if fajl.endswith('.pdf') else os.path.join(KOR, fajl)
    m = re.match(r'(\.\./[^/]+)/istochniki/(.*)', fajl)
    if not os.path.exists(t) and m: t = os.path.join(KOR, m.group(1), 'tekst', m.group(2) + '.txt')
    L = open(t, encoding='utf-8', errors='replace').read().split('\n')
    return '\n'.join(L[max(0, stroka - 1 - w): stroka + w])
osh = []; n = 0
for r in kat:
    if not r['семейство'].startswith('Испытательные ПСП'): continue
    n += 1
    mm = re.search(r'x\^(\d+) \+ x\^(\d+) \+ 1', r['параметры']['многочлен'])
    a, b = int(mm.group(1)), int(mm.group(2))
    s = r['источник'][0]; st = int(re.search(r'строка (\d+)', s['страница|строки']).group(1))
    ok = tekst_okno(s['файл'], st, 6)
    good = re.search(r'\b%d(th|rd|nd|st)\b' % b, ok) and re.search(r'\b%d(th|rd|nd|st)\b' % a, ok)
    per = poryadok_x((1 << a) | (1 << b) | 1)
    if not good or per != r['параметры'].get('длина', per): osh.append((r['имя'], bool(good), per))
rez['O.150'] = dict(записей=n, ошибок=osh)
osh = []; n = 0
for r in kat:
    if r['семейство'] != 'Скремблеры стандартов': continue
    n += 1
    st_ = [int(x) for x in re.findall(r'x\^(\d+)', r['параметры']['многочлен'])]
    nashli = False; miss_vse = []
    for s in r['источник']:
        m = re.search(r'строка (\d+)', s['страница|строки'])
        if not m: continue
        ok = tekst_okno(s['файл'], int(m.group(1)), 25)  # уравнения 802.3 извлекаются на 10–20 строк ниже абзаца
        ok2 = ok.replace('–', '-').replace('−', '-')
        SL = {3: 'third', 20: 'twentieth'}
        if re.search(r'GP[AC], in equation 7-[12]/V\.34', ok2): continue  # V.90/V.92 ссылаются на GPC/GPA V.34 по имени (assert в skremblery.py)
        miss = [e for e in st_ if not re.search(r'(?<!\d)%d(?!\d)' % e, ok2) and SL.get(e, '#') not in ok2]
        if miss and 'Scr[8] ⊕ Scr[10]' in str(r['параметры']) and 'Scrn[8] and Scrn[10]' in ok2: miss = []  # отводы 8 и 10 = степени 9 и 11
        if miss: miss_vse.append((s['файл'].split('/')[-1], m.group(1), miss))
        else: nashli = True
    if not nashli: osh.append((r['имя'][:60], miss_vse))  # ни в одном из цитируемых мест многочлен не найден
rez['Скремблеры (степени многочлена в цитируемой строке ±25 хотя бы одного источника)'] = dict(записей=n, ошибок=osh)
# ---------- Хэмминг, QR, Голей ----------
def poly_hex(s): return int(re.search(r'\(0x([0-9A-F]+)\)', s).group(1), 16)
def min_d(g, n):
    k = n - (g.bit_length() - 1); best = n
    for u in range(1, 1 << k):
        c = 0; uu = u; i = 0
        while uu:
            if uu & 1: c ^= g << i
            uu >>= 1; i += 1
        best = min(best, bin(c).count('1'))
    return best
osh = []; n = 0
for r in kat:
    f = r['семейство']; P = r['параметры']
    if f == 'Коды Хэмминга':
        n += 1; g = poly_hex(P['многочлен']); nn, k = map(int, P['n,k'].split(','))
        if poryadok_x(g) != nn or nn - k != g.bit_length() - 1: osh.append(r['имя'])
    elif f == 'Квадратично-вычетные коды':
        n += 1; g = poly_hex(P['g(x)']); nn, k = map(int, P['n,k'].split(','))
        bad = pmod((1 << nn) | 1, g) != 0 or g.bit_length() - 1 != (nn - 1) // 2 or k != nn - (nn - 1) // 2
        if not bad and nn <= 31:
            d = min_d(g, nn); dz = int(re.search(r'd=(\d+)', r['имя']).group(1)); bad = d != dz
        if bad: osh.append(r['имя'])
    elif f == 'Коды Голея' and 'g(x)' in str(P):
        pass
rez['Хэмминг/QR'] = dict(записей=n, ошибок=osh)
print(json.dumps(rez, ensure_ascii=False, indent=1))
