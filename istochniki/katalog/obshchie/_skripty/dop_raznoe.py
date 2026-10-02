"""Дополнение (проверка полноты): RAID-6 P+Q (Anvin, Linux), SCCC Бенедетто–Дивсалара–Монторси–Поллары (JPL 42-126),
код Galileo S-диапазона (14,1/4) + РС переменной избыточности (DESCANSO), Classic McEliece (коды Гоппы), HQC
(сцепленный дублированный RM(1,7) + укороченный РС). Проверки — своим кодом с assert."""
import heapq, itertools, json, os, random, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka, gde
os.makedirs(os.path.join(KOR, 'tablicy', 'dop'), exist_ok=True)
tabl = {}

# ---------- GF(2^8) ----------
def gf_tab(p, m=8):
    N = (1 << m) - 1; exp = [0] * (2 * N); a = 1
    for i in range(N):
        exp[i] = exp[i + N] = a; a <<= 1
        if a >> m: a ^= p
    log = {exp[i]: i for i in range(N)}
    assert len(log) == N
    return N, exp, log
N8, EXP, LOG = gf_tab(0x11D)
mul = lambda a, b: 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]
div = lambda a, b: 0 if a == 0 else EXP[(LOG[a] - LOG[b]) % N8]

# ---------- RAID-6 ----------
mk = open(os.path.join(KOR, 'istochniki/dop/raid/linux_lib_raid6_mktables.c')).read()
assert 'a = (a << 1) ^ (a & 0x80 ? 0x1d : 0);' in mk  # умножение на {02} по модулю x^8+x^4+x^3+x^2+1
anv = open(os.path.join(KOR, 'istochniki/dop/raid/hpa_raid6.pdf.txt')).read()
assert '{02}8 = {1d}' in anv and 'x8 + x4 + x3 + x2 + 1' in anv
rnd = random.Random(6)
for _ in range(200):
    nd = rnd.randrange(2, 30); D = [rnd.randrange(256) for _ in range(nd)]
    P = 0; Q = 0
    for i, d in enumerate(D): P ^= d; Q ^= mul(EXP[i], d)
    x, y = sorted(rnd.sample(range(nd), 2))  # потеряны два диска данных: формулы Anvin (разд. 4)
    Pxy = P; Qxy = Q
    for i, d in enumerate(D):
        if i not in (x, y): Pxy ^= d; Qxy ^= mul(EXP[i], d)
    gyx = EXP[(y - x) % N8]
    A = div(gyx, gyx ^ 1); B = div(EXP[(N8 - x) % N8], gyx ^ 1)
    Dx = mul(A, Pxy) ^ mul(B, Qxy); Dy = Pxy ^ Dx
    assert (Dx, Dy) == (D[x], D[y])

# ---------- свёрточные: dfree по решётке (общая реализация, состояние — история входов и выходов обратной связи) ----------
def dfree_obshchiy(k, step, nach):
    best = set(); h = []
    for u in itertools.product((0, 1), repeat=k):
        if any(u):
            s, w = step(nach, u); heapq.heappush(h, (w, s))
    while h:
        d, s = heapq.heappop(h)
        if s == nach: return d
        if s in best: continue
        best.add(s)
        for u in itertools.product((0, 1), repeat=k):
            ns, w = step(s, u)
            if ns not in best: heapq.heappush(h, (d + w, ns))
def koder(sist, chisl, znam=None, pam=2):
    """k входов; выходы: систематические (если sist) + для каждого выхода j: p_j·znam = Σ_i u_i·chisl[i][j] (многочлены — список коэф. D^0…)."""
    k = len(chisl); n_p = len(chisl[0])
    def step(s, u):
        hist_u, hist_p = s  # hist_u[i] — кортеж последних pam входов i; hist_p[j] — последних pam выходов j
        out_w = sum(u) if sist else 0; nu = []; np_ = []
        for j in range(n_p):
            v = 0
            for i in range(k):
                g = chisl[i][j]; seq = (u[i],) + hist_u[i]
                v ^= sum(g[t] & seq[t] for t in range(len(g))) & 1
            if znam:
                v ^= sum(znam[t] & hist_p[j][t - 1] for t in range(1, len(znam))) & 1
            out_w += v; np_.append(((v,) + hist_p[j])[:pam])
        nu = tuple(((u[i],) + hist_u[i])[:pam] for i in range(k))
        return (nu, tuple(np_)), out_w
    nach = (tuple((0,) * pam for _ in range(k)), tuple((0,) * pam for _ in range(n_p)))
    return lambda: dfree_obshchiy(k, step, nach)
# Таблица 1 JPL 42-126 (многочлены — коэффициенты при D^0, D^1, D^2)
P1DD2 = (1, 1, 1); P1D2 = (1, 0, 1); P1D = (1, 1)
d_vnesh_rek = koder(True, [[P1D2]], P1DD2)()                 # [1, (1+D^2)/(1+D+D^2)]
d_vnesh_nerek = koder(False, [[P1DD2, P1D2]])()               # [1+D+D^2, 1+D^2]
d_vnutr_rek = koder(True, [[P1D2], [P1D]], P1DD2)()           # [[1,0,(1+D^2)/(1+D+D^2)],[0,1,(1+D)/(1+D+D^2)]]
d_vnutr_nerek = koder(False, [[P1D, (0, 1), (1,)], [P1D, (1,), P1D]])()  # [[1+D, D, 1],[1+D, 1, 1+D]]
assert (d_vnesh_rek, d_vnesh_nerek, d_vnutr_rek, d_vnutr_nerek) == (5, 5, 3, 3), (d_vnesh_rek, d_vnesh_nerek, d_vnutr_rek, d_vnutr_nerek)
# Galileo (14,1/4)
def dfree_1n(G, K):
    m = K - 1
    step = lambda s, u: ((((u[0] << m) | s) >> 1), sum(bin(g & ((u[0] << m) | s)).count('1') & 1 for g in G))
    return dfree_obshchiy(1, step, 0)
GAL = [0o26042, 0o36575, 0o25715, 0o16723]
assert max(g.bit_length() for g in GAL) == 14
d_gal = dfree_1n(GAL, 14)
d_gal_rev = dfree_1n([int(format(g, '014b')[::-1], 2) for g in GAL], 14)
assert d_gal == d_gal_rev
tabl['Galileo (14,1/4)'] = dict(многочлены_восьм=['26042', '36575', '25715', '16723'], dfree_расчёт=d_gal)

# ---------- HQC: порождающие многочлены РС ----------
hq = open(os.path.join(KOR, 'istochniki/dop/pqc/hqc_specifications_2025_08_22.pdf.txt'), encoding='utf-8', errors='replace').read().replace('\x00', '')
def g_hqc(nazv, d2):
    i = hq.index('Generator polynomial of %s. ' % nazv); j = hq.index('x%d.' % d2, i)
    t = hq[i:j + len('x%d.' % d2)]
    t = re.sub(r'=====PAGE \d+=====', ' ', t); t = re.sub(r'\n\d+\n', '\n', t)
    t = t.split('=', 1)[1].replace('\n', ' ')
    co = {}
    for c, e in re.findall(r'(\d*)\s*x(\d*)', t):
        co[int(e) if e else 1] = int(c) if c else 1
    c0 = int(re.match(r'\s*(\d+)', t).group(1)); co[0] = c0
    return [co.get(e, 0) for e in range(d2 + 1)]
def g_rs(d2, fcr=1):
    g = [1]
    for r in range(fcr, fcr + d2):
        a = EXP[r % N8]; ng = [0] * (len(g) + 1)
        for i, c in enumerate(g): ng[i + 1] ^= c; ng[i] ^= mul(c, a)
        g = ng
    return g
assert '1 + α2 + α3 + α4 + α8' in hq
hqc = {}
for nazv, n, k, dl in (('RS-S1', 46, 16, 15), ('RS-S2', 56, 24, 16), ('RS-S3', 90, 32, 29)):
    gs = g_hqc(nazv, 2 * dl); gr = g_rs(2 * dl)
    assert gs == gr, (nazv, gs[:5], gr[:5])
    hqc[nazv] = dict(n=n, k=k, delta=dl, g_по_возрастанию_степеней=gs)
tabl['HQC РС'] = hqc

# ---------- Classic McEliece ----------
mc = open(os.path.join(KOR, 'istochniki/dop/pqc/mceliece-spec-20221023.pdf.txt')).read()
nab = re.findall(r'Parameter set (mceliece\d+)\nKEM with m = (\d+), n = (\d+), t = (\d+)\. Field polynomials f\(z\) = (.*?) and\s*F\(y\) =\s*(.*?)\.', mc, re.S)
assert len(nab) == 5, len(nab)
def poly_z(s):
    v = 0
    for t in s.replace(' ', '').replace('\n', '').split('+'):
        v |= 1 if t == '1' else 2 if t == 'z' else 1 << int(t[1:])
    return v
def irreducible2(p):  # Rabin над GF(2)
    n = p.bit_length() - 1
    def mm(a, b):
        r = 0
        while b:
            if b & 1: r ^= a
            b >>= 1; a <<= 1
            if a >> n & 1: a ^= p
        return r
    from sympy import factorint
    def xpow2k(kk):
        x = 2
        for _ in range(kk): x = mm(x, x)
        return x
    def gcd(a, b):
        while b:
            while a and a.bit_length() >= b.bit_length(): a ^= b << (a.bit_length() - b.bit_length())
            a, b = b, a
        return a
    if xpow2k(n) != 2: return False
    return all(gcd(xpow2k(n // q) ^ 2, p) == 1 for q in factorint(n))
import galois
mcz = []
PROVERYAT_F = os.environ.get('MCELIECE_F', '1') == '1'
for imya, m, n, t, fz, Fy in nab:
    m, n, t = int(m), int(n), int(t); f = poly_z(fz)
    assert f.bit_length() - 1 == m and irreducible2(f)
    Fy = ' '.join(Fy.split())
    ok_F = None
    if PROVERYAT_F:
        GF = galois.GF(2 ** m, irreducible_poly=f)
        c = [0] * (t + 1)  # целые (элементы GF(2^m) как числа); сложение — XOR
        for term in Fy.replace(' ', '').split('+'):
            if term == 'z': c[t] ^= 2
            elif term == '1': c[t] ^= 1
            elif term == 'y': c[t - 1] ^= 1
            else: c[t - int(term[1:])] ^= 1
        P = galois.Poly(c, field=GF); assert P.degree == t
        ok_F = bool(P.is_irreducible()); assert ok_F, imya
    mcz.append(dict(набор=imya, m=m, n=n, t=t, k=n - m * t, f_z=' '.join(fz.split()), F_y=Fy, F_неприводим=ok_F))
tabl['Classic McEliece'] = mcz
json.dump(tabl, open(os.path.join(KOR, 'tablicy', 'dop', 'raznoe.json'), 'w'), ensure_ascii=False, indent=1)

# ---------- записи ----------
zap = []
RD = 'istochniki/dop/raid/'
zap.append(Z('RAID-6 P+Q: РС-подобный код стирания двух дисков над GF(2^8) (Anvin; Linux md/raid6)', 'Коды стирания хранилищ', 'РС',
             {'поле': 'GF(2^8) по x^8+x^4+x^3+x^2+1 (0x11D), g = {02}', 'P': 'P = D_0 ⊕ D_1 ⊕ … ⊕ D_{n−1} (побайтно)', 'Q': 'Q = g^0·D_0 ⊕ g^1·D_1 ⊕ … ⊕ g^{n−1}·D_{n−1}',
              'восстановление': 'один диск — через P; два диска x<y — D_x = A·(P⊕P_xy) ⊕ B·(Q⊕Q_xy), A = g^(y−x)/(g^(y−x)⊕1), B = g^(−x)/(g^(y−x)⊕1), D_y = (P⊕P_xy) ⊕ D_x',
              'ограничение': 'до 255 дисков данных', 'порядок': 'страйп: диски данных, затем P, затем Q (Linux)'},
             'программный RAID Linux md (raid6), многие аппаратные RAID-6; образы дисков/массивов',
             [I(RD + 'hpa_raid6.pdf', 'стр. 1–3 PDF (разд. 2–4: поле, P, Q, восстановление двух дисков)'),
              I(RD + 'linux_lib_raid6_mktables.c', 'строки %d–%d (gfmul, приведение 0x1d)' % (stroka(RD + 'linux_lib_raid6_mktables.c', 'static uint8_t gfmul'), stroka(RD + 'linux_lib_raid6_mktables.c', '0x1d'))),
              I(RD + 'LINUX_COPYING', 'GPL-2.0 (ядро Linux)')],
             'своя реализация: 200 случайных страйпов (2…29 дисков), стёрты 2 диска — восстановлены по формулам Anvin (assert); приведение 0x1d в mktables.c = многочлен 0x11D',
             'НЕТ: в проекте нет разбора RAID; арифметика GF(2^8) 0x11D есть (rs_bch.py)'))
J = 'istochniki/dop/jpl/jpl_42-126D_SCCC.pdf'
src_sccc = [I(J, 'стр. 7 PDF, Table 1 (порождающие матрицы), пример 3'), I(J, 'стр. 16–17 PDF (SCCC1…SCCC4, Table 3)')]
ver = 'd_f составляющих пересчитаны по решётке своим кодом: внешний рекурсивный 1/2 = %d, нерекурсивный 1/2 = %d, внутренний рекурсивный 2/3 = %d, нерекурсивный 2/3 = %d — совпали с Table 3 (do_f = 5, di_f = 3)' % (d_vnesh_rek, d_vnesh_nerek, d_vnutr_rek, d_vnutr_nerek)
for nazv, vn, vt, R in (('SCCC1', 'рекурсивный систематический 1/2, 4 состояния: G = [1, (1+D²)/(1+D+D²)]', 'рекурсивный систематический 2/3: G = [[1, 0, (1+D²)/(1+D+D²)], [0, 1, (1+D)/(1+D+D²)]]', '1/3'),
                        ('SCCC2', 'рекурсивный систематический 1/2: G = [1, (1+D²)/(1+D+D²)]', 'нерекурсивный 2/3: G = [[1+D, D, 1], [1+D, 1, 1+D]]', '1/3'),
                        ('SCCC3', 'нерекурсивный 1/2: G = [1+D+D², 1+D²]', 'рекурсивный систематический 2/3 (как SCCC1)', '1/3'),
                        ('SCCC4', 'нерекурсивный 2/3, 4 состояния: G = [[1+D, D, 1], [1+D, 1, 1+D]]', 'рекурсивный систематический 3/6 = три кодера [1, (1+D²)/(1+D+D²)] параллельно', '2/6 = 1/3')):
    zap.append(Z('%s (Benedetto–Divsalar–Montorsi–Pollara, JPL TDA 42-126): последовательный каскад свёрточных кодов' % nazv, 'Последовательные турбокоды SCCC (общие)', 'турбо SCCC',
                 {'скорость': R, 'внешний код': vn, 'внутренний код': vt, 'перемежитель': 'равномерный (uniform) длины N кратной длине внешнего слова; в моделировании — псевдослучайный',
                  'правила': 'внутренний — рекурсивный (выигрыш перемежения ~N^−⌊(do_f+1)/2⌋), внешний — с большим и нечётным do_f',
                  'декодирование': 'итеративное SISO (рис. 14 отчёта)', 'd_f составляющих (расчёт)': {'внешний': d_vnesh_rek if 'рекурс' in vn and 'нерек' not in vn else d_vnesh_nerek if '1/2' in vn else None}},
                 'общая конструкция SCCC (на ней основаны CCSDS 131.2 SCCC, ATSC-M/H SCCC — области kosmos/efir)', src_sccc, ver,
                 'ЧАСТИЧНО: турбо PCCC/RSC и свёрточные k/n есть (turbo.py, svyortka.py); SCCC как каскад — не распознаётся'))
zap[-1]['параметры'].pop('d_f составляющих (расчёт)')
for z in zap[1:4]: z['параметры']['d_f составляющих (расчёт)'] = {'внешний': d_vnesh_rek if 'нерекурсивный' not in z['параметры']['внешний код'] else d_vnesh_nerek, 'внутренний': d_vnutr_rek if 'нерекурсивный' not in z['параметры']['внутренний код'] else d_vnutr_nerek}
G5 = 'istochniki/dop/descanso/Descanso5--Galileo_new.pdf.txt'
zap.append(Z('Galileo, миссия S-диапазона: свёрточный (14,1/4) (26042, 36575, 25715, 16723) + РС(255,k) переменной избыточности, перемежение 8', 'Коды дальнего космоса JPL (миссии)', 'каскадный (свёрточный + РС)',
             {'n,k': '4,1', 'K': 14, 'многочлены (восьм., как в источнике)': '26042, 36575, 25715, 16723', 'dfree (расчёт)': d_gal,
              'построение': 'сцепление программного (11,1/2) и аппаратного (7,1/2) кодера TMU', 'внешний': 'РС(255,k), профиль избыточности (94, 10, 30, 10, 60, 10, 30, 10) по 8 перемежаемым словам кадра: РС(255,161), (255,245), (255,225), (255,195)',
              'декодирование': 'декодер с обратной связью (FCD): 4 прохода Витерби ↔ РС', 'прочее': 'на борту также экспериментальный (15,1/4) кодер (не использован; многочлены в источнике не приведены)'},
             'Galileo (1995–2003) после отказа ОНА; учебный пример каскада с переменной избыточностью',
             [I('istochniki/dop/descanso/Descanso5--Galileo_new.pdf', gde(G5, 'The generator polynomial, in octal, of the (14,1/4) code is (26042, 36575, 25715,')),
              I('istochniki/dop/descanso/Descanso5--Galileo_new.pdf', gde(G5, 'The (14,1/4) convolutional code used for the Galileo mission is the concatenation of a'))],
             'dfree пересчитан своим кодом (Дейкстра, 8192 состояния) = %d, одинаков при обеих ориентациях многочленов; в источнике dfree не дан; k РС = 255 − избыточность (сверено со списком кодов в сноске 14)' % d_gal,
             'ЧАСТИЧНО: kod.витерби_n общий (K до 14 — 8192 состояния, медленно); svyortka.py ищет 1/n только до n=4 и меньшие K'))
MC = 'istochniki/dop/pqc/mceliece-spec-20221023.pdf'
for e in mcz:
    zap.append(Z('Classic McEliece %s: двоичный код Гоппы (n=%d, t=%d, m=%d)' % (e['набор'], e['n'], e['t'], e['m']), 'Коды Гоппы (Classic McEliece)', 'Гоппа (двоичный)',
                 {'n,k': '%d,%d' % (e['n'], e['k']), 't (исправляет)': e['t'], 'поле': 'GF(2^%d), f(z) = %s' % (e['m'], e['f_z']), 'многочлен Гоппы': 'g(x) — случайный неприводимый степени t, задаётся через F(y) = %s (минимальный многочлен)' % e['F_y'],
                  'вариант f': 'набор %sf — то же с полусистематической формой (µ, ν) = (32, 64)' % e['набор'], 'назначение': 'криптосистема с открытым ключом (KEM); код — проверочная матрица H (m·t × n) систематическая'},
                 'постквантовая криптография (кандидат NIST 4-го тура, ISO/IEC 18033-2 доп. в работе); как помехоустойчивый код — справочно',
                 [I(MC, 'стр. 15–16 PDF (разд. 7 «Selected parameter sets»)'), I(MC + '.txt', 'строка %d' % stroka(MC + '.txt', 'Parameter set %s\n' % e['набор'] if False else 'Parameter set %s' % e['набор'], 600))],
                 'f(z) неприводим (тест Рабина, свой код); F(y) неприводим над GF(2^m) (galois.Poly.is_irreducible); k = n − m·t' if e['F_неприводим'] else 'f(z) неприводим (тест Рабина); k = n − m·t',
                 'НЕТ (справочно: криптографический код)'))
HQ = 'istochniki/dop/pqc/hqc_specifications_2025_08_22.pdf'
for nab_, rs, dup, n_ in (('HQC-1', 'RS-S1', 3, 46 * 384), ('HQC-3', 'RS-S2', 5, 56 * 640), ('HQC-5', 'RS-S3', 5, 90 * 640)):
    h = hqc[rs]
    zap.append(Z('%s: сцепленный код — внешний укороченный РС %s [%d,%d] над GF(2^8) + внутренний RM(1,7) [128,8,64], дублированный ×%d' % (nab_, rs, h['n'], h['k'], dup), 'Сцепленные коды HQC', 'каскадный (РС + РМ)',
                 {'внешний': 'РС[%d,%d], δ=%d: укорочение РС[255,%d], g(x) = ∏(x+α^i), i=1…%d, поле 1+α²+α³+α⁴+α⁸ (0x11D); коэффициенты g — tablicy/dop/raznoe.json' % (h['n'], h['k'], h['delta'], h['k'] + 255 - h['n'], 2 * h['delta']),
                  'внутренний': 'RM(1,7) [128, 8, 64], каждый бит повторён %d раз → [%d, 8, %d]' % (dup, 128 * dup, 64 * dup), 'длина сцепленного слова': '%d бит' % n_,
                  'кодирование РС': 'систематическое: остаток x^(n−k)·u(x) mod g(x)', 'декодирование': 'быстрое преобразование Адамара (RM) → алгебраический РС'},
                 'постквантовая криптография (HQC выбран NIST в 2025 г.); как помехоустойчивый код — справочно',
                 [I(HQ, 'стр. 17–19 PDF (разд. 3.4: РС, табл. 3, g1…g3), стр. 21 (табл. 4, дублированный RM)'), I(HQ + '.txt', 'строка %d' % stroka(HQ + '.txt', 'Generator polynomial of %s.' % rs))],
                 'g(x) %s пересчитан своим кодом (∏(x+α^i), α=2 в GF(256)/0x11D) — все %d коэффициентов совпали с напечатанными (assert)' % (rs, 2 * h['delta'] + 1),
                 'ЧАСТИЧНО: РС над GF(2^8) и РМ первого порядка есть (rs_bch.py, kod.py); сцепление с дублированием — нет (справочно)'))
sohranit('dop_raznoe', zap)
