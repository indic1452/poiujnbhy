"""CCSDS 131.2-B-2 (SCCC): перемежители прил. B (19 штук, α/β из табл. B-1/B-2), параметры 27 форматов ACM (табл. 3-1, 4-1, 4-3),
шаблон выкалывания систематических бит (табл. 4-2). Сборка из текста стандарта со сверками (assert)."""
import json, os, re

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'ccsds')
VYH = os.path.join(KOR, 'tablicy', 'ccsds')
T = open(os.path.join(IST, '131x2b2.pdf.txt'), encoding='utf8').read()


def str_(n):
    return re.search(r'=====PAGE %d=====\n(.*?)(?======PAGE|\Z)' % n, T, re.S).group(1)


def tokens_tabl(pages):
    """Числа после последней строки заголовка «α β …» на каждой странице."""
    out = []
    for p in pages:
        t = str_(p)
        i = t.rfind('β')
        body = t[i + 1:]
        out += [int(x) for x in re.findall(r'\d+', body)]
    return out


def zagolovok(p):
    t = ' '.join(str_(p).split())
    return [(int(a), int(b)) for a, b in re.findall(r'I=(\d+) W=(\d+)', t)]


# страницы таблиц
str_B1 = [p for p in range(54, 70) if 'I=8640' in str_(p)]
str_B2 = [p for p in range(54, 70) if 'I=35280' in str_(p)]
tabl = {}
for pages in (str_B1, str_B2):
    IW = zagolovok(pages[0])
    for p in pages:
        assert zagolovok(p) == IW
    for I, W in IW:
        assert I == 120 * W
    tok = tokens_tabl(pages)
    alpha = {I: [] for I, _ in IW}
    beta = {I: [] for I, _ in IW}
    pos = 0
    r = 0
    maxW = max(W for _, W in IW)
    while r < maxW:
        assert tok[pos] == r, ('номер строки', r, tok[pos:pos + 5])
        pos += 1
        for I, W in IW:
            if W > r:
                a, b = tok[pos], tok[pos + 1]
                pos += 2
                assert 0 <= a < W and 0 <= b < 120, (I, r, a, b)
                alpha[I].append(a); beta[I].append(b)
        r += 1
    assert pos == len(tok), ('лишние числа', len(tok) - pos)
    for I, W in IW:
        assert sorted(alpha[I]) == list(range(W)), ('α — перестановка', I)
        tabl[I] = {'W': W, 'alpha': alpha[I], 'beta': beta[I]}
assert len(tabl) == 19


def pi(I, i):
    W = tabl[I]['W']
    iw = i % W
    return W * ((i // W + tabl[I]['beta'][iw]) % 120) + tabl[I]['alpha'][iw]


for I in tabl:
    p = [pi(I, i) for i in range(I)]
    assert sorted(p) == list(range(I)), I
# табл. 4-1 (размеры перемежителя по формату), 3-1 (K), 4-3 (параметры)
t43 = ' '.join(str_(29).split())
rows = re.findall(r'\b(\d{1,2}) (\d) (\d{3}) (\d{4,5}) (\d{4,5}) (\d{4,5}) (\d{3,5}) (\d{5}) (\d{3,5})\b', t43)
assert len(rows) == 27
fmt = {}
for f, m, ssur, K, I, S, P, N, D in rows:
    f, m, ssur, K, I, S, P, N, D = map(int, (f, m, ssur, K, I, S, P, N, D))
    assert N == 8100 * m and S + P == N and D == I - (P - 2)
    assert I == 3 * (K + 2) // 2, (f, K, I)  # выход внешнего кода после выкалывания до 2/3 с хвостом
    assert I in tabl
    fmt[f] = {'m': m, 'Ssur': ssur, 'K': K, 'I': I, 'S': S, 'P': P, 'N': N, 'Delta': D}
# табл. 4-2: позиции выкалывания систематических бит
t42 = ' '.join(str_(27).split())
q = re.findall(r'\b(\d{1,3}) (\d{3}) (0\.\d{4}|1\.0000) (\d{1,3})\b', t42)
assert len(q) == 100
poz = {}
for idx, ss, rate, pp in q:
    poz[int(idx)] = (int(ss), int(pp))
assert [poz[i][0] for i in range(1, 101)] == list(range(299, 199, -1))
punct = [poz[i][1] for i in range(1, 101)]
assert len(set(punct)) == 100
assert punct[:8] == [76, 1, 145, 214, 256, 37, 109, 181]  # пример п. 4.4.3.2 а) для Ssur=292


def S_vyzhiv(f):
    """Число переданных систематических бит: считаем по алгоритму п. 4.4.3.2 и сверяем с S из табл. 4-3."""
    d = fmt[f]
    I, ss = d['I'], d['Ssur']
    shab = [1] * 300
    for j in punct[:300 - ss]:
        shab[j] = 0
    return sum(shab[pi(I, i) % 300] for i in range(I)) + 2


for f in fmt:
    assert S_vyzhiv(f) == fmt[f]['S'], (f, S_vyzhiv(f), fmt[f]['S'])
json.dump({'перемежители': {str(I): v for I, v in sorted(tabl.items())}, 'форматы_ACM': fmt, 'позиции_выкалывания_C1': punct,
           'формула': 'π(i) = W·[(⌊i/W⌋ + β(i mod W)) mod 120] + α(i mod W), W = I/120'},
          open(os.path.join(VYH, 'sccc_131_2.json'), 'w'), ensure_ascii=False)
print('SCCC: 19 перемежителей, 27 форматов — все проверки пройдены')

# ---------------------------------------------------------------- Маркер кадра (Gold, 256 бит) и дескриптор (стр. 37-39)
s37 = ' '.join(str_(37).split())
assert 'g1(x) = x8 + x6 + x5 + x4 + 1' in s37 and 'g2(x) = x8 + x6 + x5 + x4 + x3 + x + 1' in s37
fm40 = [int(c) for c in re.search(r'Marker: ((?:[01]{4} ){9}[01]{4})', s37).group(1).replace(' ', '')]


def mseq(taps, init, n):
    a = list(init)
    while len(a) < n:
        t = len(a) - 8
        a.append(sum(a[t + 8 - d] for d in taps) & 1)
    return a[:n]


# рекуррентности по g1, g2 (прямая и взаимная), все 2^8×2^8 начальных состояний: ищем единственное совпадение 40 бит
g1v = {'g1': (2, 3, 4, 8), 'g1*': (4, 5, 6, 8)}
g2v = {'g2': (2, 3, 4, 5, 7, 8), 'g2*': (1, 3, 4, 5, 6, 8)}
import itertools
nahod = []
tab1 = {(n, s): mseq(t, [(s >> (7 - k)) & 1 for k in range(8)], 40) for n, t in g1v.items() for s in range(256)}
tab2 = {(n, s): mseq(t, [(s >> (7 - k)) & 1 for k in range(8)], 40) for n, t in g2v.items() for s in range(256)}
idx2 = {}
for key, v in tab2.items():
    idx2.setdefault(tuple(v), []).append(key)
for key, v in tab1.items():
    need = tuple(a ^ b for a, b in zip(v, fm40))
    for k2 in idx2.get(need, []):
        nahod.append((key, k2))
# одно и то же 256-битное слово при всех найденных вариантах?
fm256 = set()
for (n1, s1), (n2, s2) in nahod:
    a = mseq(g1v[n1], [(s1 >> (7 - k)) & 1 for k in range(8)], 256)
    b = mseq(g2v[n2], [(s2 >> (7 - k)) & 1 for k in range(8)], 256)
    fm256.add(tuple(x ^ y for x, y in zip(a, b)))
assert len(fm256) == 1, (len(nahod), len(fm256))
fm = list(fm256.pop())
assert fm[:40] == fm40
fm_hex = ''.join('%X' % int(''.join(map(str, fm[i:i + 4])), 2) for i in range(0, 256, 4))
# дескриптор: (32,6) по G рис. 5-5, повтор (y1 y1 ⊕ b7…), XOR с 64-битной последовательностью
s39 = ' '.join(str_(39).split())
Grows = re.findall(r'[01]{32}', s39)[:6]
assert len(Grows) == 6
scr = re.search(r'sequence: ([01]{64})', s39).group(1)


def fd(fmt_nomer, piloty):
    b = [(fmt_nomer >> (4 - i)) & 1 for i in range(5)] + [piloty]
    y = [sum(b[r] * int(Grows[r][c]) for r in range(6)) & 1 for c in range(32)]
    out = []
    for v in y:
        out += [v, v]  # b7 = 0
    return ''.join(str(o ^ int(s)) for o, s in zip(out, scr))


fd_tab = {f: {'без_пилотов': fd(f, 0), 'с_пилотами': fd(f, 1)} for f in range(1, 28)}
# минимальное расстояние (32,6) = 16 (биортогональный), кода 64 — 32
slova = {tuple(int(c) for c in fd(f, p)) for f in range(32) for p in (0, 1)}
dmin = min(sum(a != b for a, b in zip(x, y)) for x in slova for y in slova if x != y)
assert dmin == 32, dmin
d = json.load(open(os.path.join(VYH, 'sccc_131_2.json')))
d['маркер_кадра_256_hex'] = fm_hex
d['маркер_варианты_регистров'] = [list(map(list, v)) for v in nahod]
d['дескриптор_кадра_64'] = fd_tab
d['дескриптор_скремблер'] = scr
d['PL_рандомизатор'] = 'Gold, x: 1+x^7+x^18 (x(0)=1, прочие 0), y: 1+y^5+y^7+y^10+y^18 (все 1); z_n(i)=x((i+n) mod (2^18-1))+y(i); R_n(i)=2z_n((i+131072) mod (2^18-1))+z_n(i), i=0..133440 — стр. 69-70'
d['ASM_вложенного_потока'] = '352EF853'
json.dump(d, open(os.path.join(VYH, 'sccc_131_2.json'), 'w'), ensure_ascii=False)
print('маркер кадра', fm_hex, 'вариантов регистров', len(nahod), '; дескриптор dmin =', dmin)
