"""Сверка (проход 2, независимый код): CCSDS турбо-перемежитель, рандомизаторы TM/TC, SCCC-X BCH(8100,8048),
RSM-A RS(244,220)/(236,216) и Хэмминг (12,8), CCSDS TC BCH(63,56).
Все значения читаются из текста первоисточника (…pdf.txt) регулярными выражениями; при несовпадении — assert.
Итог — tablicy/_itog_sverka2_a.json."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf import GF, pmul, pmod, is_primitive
from pokaz import stranica, KOR

itog = {}
P = lambda *a: os.path.join(KOR, *a)

# ---------------------------------------------------------------- 1. CCSDS турбо: π(s) по формуле 131.0-B-6 п. 7.3.7 (стр. PDF 46)
t46 = stranica('istochniki/ccsds/131x0b6ec1.pdf', 46)
pr = [int(x) for x in re.findall(r'p\d = (\d+)', t46)]
assert pr == [31, 37, 43, 47, 53, 59, 61, 67], pr
for k, I in ((1784, 1), (3568, 2), (7136, 4), (8920, 5)):
    k1, k2 = 8, 223 * I
    assert k1 * k2 == k
    pi = []
    for s in range(1, k + 1):
        m = (s - 1) % 2
        i = (s - 1) // (2 * k2)
        j = (s - 1) // 2 - i * k2
        t = (19 * i + 1) % (k1 // 2)
        q = t % 8 + 1
        c = (pr[q - 1] * j + 21 * m) % k2
        pi.append(2 * (t + c * k1 // 2 + 1) - m)
    assert sorted(pi) == list(range(1, k + 1))
    tab = json.load(open(P('tablicy/ccsds/turbo_perm_%d.json' % k)))
    assert tab == pi, k
itog['ccsds_turbo_perm'] = 'π(s) для k=1784/3568/7136/8920 по формуле стр. 46 == tablicy/ccsds/turbo_perm_*.json (все элементы)'


# ---------------------------------------------------------------- 2. Рандомизаторы: первые 40 бит из текста → генератор → .hex
def bity_hex(h, n):
    return [int(b) for b in bin(int(h[:(n + 3) // 4 * 4 // 4 * 1] if False else h, 16))[2:].zfill(len(h) * 4)][:n]


def lfsr(poly_deg, init, n):
    """Фибоначчи по характеристическому многочлену h(x)=Σ x^d: a[t+m] = Σ_{d<m, коэф.=1} a[t+d]."""
    m = max(poly_deg)
    a = list(init)
    while len(a) < n:
        t = len(a) - m
        v = 0
        for d in poly_deg:
            if d < m:
                v ^= a[t + d]
        a.append(v)
    return a


t = open(P('istochniki/ccsds/131x0b6ec1.pdf.txt')).read()
for imya, deg, init_txt, fayl, per in (('TM255', (8, 7, 5, 3, 0), '1' * 8, 'randomizer_tm_255.hex', 255),
                                       ('TM131071', (17, 14, 0), '11000111000111000', 'randomizer_tm_131071.hex', 131071)):
    hexs = open(P('tablicy/ccsds', fayl)).read().strip()
    b = bity_hex(hexs, len(hexs) * 4)
    # первые m бит выхода — начальное состояние регистра (в стандарте записано от старшей ячейки: развёрнутое)
    m = deg[0]
    ok = None
    for vid, init in (('прямой', [int(c) for c in init_txt]), ('развёрнутый', [int(c) for c in init_txt[::-1]])):
        for d in (deg, tuple(sorted({m - x for x in deg}, reverse=True))):  # h(x) и взаимный
            s = lfsr(d, init, min(len(b), per))
            if s == b[:len(s)] and all(x == 0 for x in b[per:]):  # в файле один период, дополненный нулями до байта
                ok = (vid, d)
    assert ok, imya
    itog['ccsds_rand_' + imya] = 'hex-таблица (%d бит) воспроизведена ЛРС h(x)=%s, начальное %s (%s порядок записи)' % (len(b), '+'.join('x^%d' % x for x in deg), init_txt, ok[0])
# 40 бит примечания TM 131071
m40 = re.search(r'first 40 bits[^:]*?:?\s*((?:[01]{4}\s*){10})', t.replace('\n', ' '))
tc = open(P('istochniki/ccsds/231x0b4e1c1.pdf.txt')).read().replace('\n', ' ')
m40tc = re.search(r'The first 40 bits of the sequence\s+are\s+((?:[01]{4}\s*){10})', tc)
assert m40tc
b40 = [int(c) for c in re.sub(r'\s', '', m40tc.group(1))]
s = lfsr((8, 6, 4, 3, 2, 1, 0), [1] * 8, 255 * 2)
s2 = lfsr((8, 7, 6, 5, 4, 2, 0), [1] * 8, 255 * 2)  # взаимный
assert s[:40] == b40 or s2[:40] == b40
hexs = open(P('tablicy/ccsds/randomizer_tc_255.hex')).read().strip()
bt = bity_hex(hexs, len(hexs) * 4)
assert (s if s[:40] == b40 else s2)[:min(len(bt), 255)] == bt[:255] and not any(bt[255:])
itog['ccsds_rand_TC'] = '40 бит п. 6.2 231.0-B-4 и randomizer_tc_255.hex воспроизведены ЛРС x^8+x^6+x^4+x^3+x^2+x+1 из единиц'

# ---------------------------------------------------------------- 3. SCCC-X BCH (8191,8139), g в hex (стр. PDF 24), g0 — старший разряд
t24 = stranica('istochniki/ccsds/131x21o1c1.pdf', 24)
gh = re.search(r'hexadecimal representation\s+([0-9A-F]+)', t24).group(1)
gb = re.search(r'binary representation\s+([01 ]+)', t24).group(1).replace(' ', '')
assert int(gh, 16) == int(gb, 2) and gh == '1AAC3AB8418945'
g_msb_g0 = int(gb, 2)
assert g_msb_g0.bit_length() == 53
g = int(format(g_msb_g0, '053b')[::-1], 2)  # переворот: бит i = g_i
# g — произведение 4 минимальных многочленов степени 13 от β, β^3, β^5, β^7 для примитивного β?
x8191 = (1 << 8191) | 1
assert pmod(x8191, g) == 0, 'g не делит x^8191+1'
# найти примитивный p(x) степени 13, делящий g, и проверить корни α^1,3,5,7
naideno = None
for p in range((1 << 13) | 1, 1 << 14, 2):
    if pmod(g, p) == 0 and is_primitive(p):
        F = GF(13, p)
        pr_ = 1
        for e in (1, 3, 5, 7):
            pr_ = pmul(pr_, F.minpoly(e))
        if pr_ == g:
            naideno = p
            break
assert naideno, 'g не является БЧХ-произведением m1·m3·m5·m7'
g_sym = g == int(format(g, '053b')[::-1], 2)
itog['sccc_x_bch'] = ('g(x)=1AAC3AB8418945 (g0 — старший разряд записи) = m1·m3·m5·m7 над GF(2^13) с p(x)=%s (0x%X): t=4, d≥9; g делит x^8191+1; '
                      'g %s' % ('+'.join('x^%d' % i for i in range(13, -1, -1) if naideno >> i & 1), naideno, 'самовзаимный' if g_sym else 'не самовзаимный — порядок записи существенен'))

# таблицы 3-1 и 4-1 131.21-O-1: K=K1+K2, K2 = 3·8048 (28–32) / 4·8048 (33–37); N1=S+P=32400, 8100 символов
t20 = stranica('istochniki/ccsds/131x21o1c1.pdf', 20).replace(',', '')
rows = re.findall(r'\n(2[89]|3[0-7]) \n(\d+) \n(\d+) \n(\d+) ', t20)
assert len(rows) == 10, rows
t23 = stranica('istochniki/ccsds/131x21o1c1.pdf', 23)
rows4 = re.findall(r'\n(2[89]|3[0-7]) \n(\d+) \n(\d+) \n(\d+) \n(\d+) \n(\d+) \n(\d+) \n(\d+) ', t23)
assert len(rows4) == 10
sccc_x = []
for (a, k1, k2, k), (a4, ssur, k1b, I, S, Pp, N1, D) in zip(rows, rows4):
    k1, k2, k, k1b, I, S, Pp, N1 = map(int, (k1, k2, k, k1b, I, S, Pp, N1))
    assert a == a4 and k1 == k1b and k1 + k2 == k and N1 == 32400 and S + Pp == N1
    mm = 7 if int(a) <= 32 else 8
    assert k2 == (mm - 4) * 8048 and (mm - 4) * 8100 + N1 == 8100 * mm
    sccc_x.append({'ACM': int(a), 'm': mm, 'K1': k1, 'K2': k2, 'K': k, 'Ssur': int(ssur), 'I': I, 'S': S, 'P': Pp, 'N1': N1, 'Δ': int(D), 'N': 8100 * mm})
json.dump({'источник': 'istochniki/ccsds/131x21o1c1.pdf стр. PDF 20 (табл. 3-1), 23 (табл. 4-1), 24 (g BCH)', 'g_hex_g0_старший': gh,
           'p_x_поля': hex(naideno), 'форматы': sccc_x}, open(P('tablicy/ccsds/sccc_x_131_21.json'), 'w'), ensure_ascii=False, indent=1)
itog['sccc_x_tabl'] = '10 форматов ACM#28–37: K1+K2=K, K2=(m−4)·8048, N1=S+P=32400, N=8100·m (assert) → tablicy/ccsds/sccc_x_131_21.json'


# ---------------------------------------------------------------- 4. RSM-A RS(244,220) и RS(236,216): G(X) из табл. 5.3.1 / 6.3.1
def rs_tabl(p, n_par):
    tx = stranica('istochniki/rsma/ts_10218803v010102p.pdf', p)
    r = re.findall(r'\n(\d+) \n([01](?:\s+[01]){7}) \n(\d+) ', tx)
    assert len(r) == n_par + 1, (p, len(r))
    return [(int(i), [int(c) for c in b.split()], int(e)) for i, b, e in r]


F = GF(8, 0b100011101)  # x^8+x^4+x^3+x^2+1 (восьм. 435)
assert int('435', 8) == 0b100011101
rez = {}
for p, npar, imya in ((9, 24, 'RS(244,220)'), (14, 20, 'RS(236,216)')):
    tab = rs_tabl(p, npar)
    kort = [sum(b << j for j, b in enumerate(bits)) for _, bits, _ in tab]  # 8-кортеж (α0…α7), индекс 0 = x^0
    exps = [e for _, _, e in tab]
    best = None
    for b0 in (0, 1):
        g = F.poly_from_roots([F.a(b0 + j) for j in range(npar)])[::-1]
        if g == kort:
            best = b0
    assert best is not None, imya
    sogl = all(F.a(e) == v for e, v in zip(exps, kort))
    if imya == 'RS(244,220)':
        assert sogl
    else:
        # столбец «Exponent» табл. 6.3.1 НЕ соответствует 8-кортежам (перебраны все примитивные p(x), b0 и шаги корней — совпадения нет)
        assert not sogl and sum(F.a(e) == v for e, v in zip(exps, kort)) == 1  # совпадает только старший (α^0)
    rez[imya] = 'корни α^%d…α^%d%s' % (best, best + npar - 1, '' if sogl else '; столбец «Exponent» табл. 6.3.1 ОШИБОЧЕН (не равен log 8-кортежа ни при каком примитивном p(x)) — верны только 8-кортежи')
itog['rsma_rs'] = 'G(X) из табл. 5.3.1 и 6.3.1 TS 102 188-3 (8-кортежи = α^exp) == ∏(X−α^i): ' + '; '.join('%s — %s' % kv for kv in rez.items()) + \
    ' (в ПРИМЕЧАНИИ таблиц сказано «n от 0 до 24/20» — неточность; формула п. 5.3.1 i=1…24 верна)'

# Хэмминг (12,8): уравнения стр. 11 (восстановлены из вёрстки) — все 8 столбцов различны и не единичны → d=3
H = {0: 0b0111, 1: 0b1001, 2: 0b1010, 3: 0b1011, 4: 0b1100, 5: 0b1101, 6: 0b1110, 7: 0b1111}  # (p3p2p1p0) для i0..i7
t11 = stranica('istochniki/rsma/ts_10218803v010102p.pdf', 11)
cif = re.findall(r'^\s*(\d)\s*$', t11.split('In the equations')[0], re.M)
# вёрстка: индексы i для p0: 7 5 3 1 0 | p0 ; p1: 7 6 3 2 0 | 1 ; p2: 7 6 5 4 0 | 2 ; p3: 7 6 5 4 3 2 1 | 3
assert ''.join(cif) == '75310076320176540276543213', ''.join(cif)
urav = {0: {7, 5, 3, 1, 0}, 1: {7, 6, 3, 2, 0}, 2: {7, 6, 5, 4, 0}, 3: {7, 6, 5, 4, 3, 2, 1}}
for i in range(8):
    assert H[i] == sum(1 << pp for pp in range(4) if i in urav[pp])
stolb = list(H.values())
assert len(set(stolb)) == 8 and all(bin(c).count('1') >= 2 for c in stolb)
itog['rsma_hamming'] = 'p0=i0⊕i1⊕i3⊕i5⊕i7, p1=i0⊕i2⊕i3⊕i6⊕i7, p2=i0⊕i4⊕i5⊕i6⊕i7, p3=i1⊕…⊕i7 (стр. 11): столбцы H различны, вес ≥2 → d=3'

# ---------------------------------------------------------------- 5. CCSDS TC BCH(63,56): g(x)=x^7+x^6+x^2+1
tt = open(P('istochniki/ccsds/231x0b4e1c1.pdf.txt')).read().replace('\n', ' ')
assert re.search(r'g\(x\)\s*=\s*x7\s*\+\s*x6\s*\+\s*x2\s*\+\s*1', tt)
g = 0b11000101
assert pmul(0b11, 0b1000011) == g  # (x+1)(x^6+x+1)
assert is_primitive(0b1000011)
sind = set()
for i in range(63):
    sind.add(pmod(1 << i, g))
assert len(sind) == 63 and 0 not in sind
itog['ccsds_tc_bch'] = 'g=x^7+x^6+x^2+1=(x+1)(x^6+x+1), 63 одиночных ошибки → 63 различных синдрома'

json.dump(itog, open(P('tablicy/_itog_sverka2_a.json'), 'w'), ensure_ascii=False, indent=1)
for k, v in itog.items():
    print(k, ':', v)
