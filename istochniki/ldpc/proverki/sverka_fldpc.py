# Сверка описания кода F-LDPC (TrellisWare; основа Datum FlexLDPC) по первоисточникам:
#   патент US 7,958,425 B2 (кодер 600, таблицы 21–23, 34–36),
#   патент US 7,673,213 B2 (кодер 1500 с выкалыванием, таблицы 37–42),
#   доклад IEEE 802.11-04/0953r3 (Chugg и др., «Flexible Coding for 802.11n MIMO Systems»):
#     «Outer code polynomials: (1+D, 1+D); Inner code polynomial: 1/(1+D) [accumulator];
#      For K-bit frames the interleaver is fixed at 2K bits; Code rate = J/(J+2);
#      V variable nodes with dv=2, K variable nodes with dv=4, All checks have dc=2J+2».
# Всё строится только по тексту патентов (patenty/*.txt, полный текст из ppubs.uspto.gov).
import re, os, math, random
from fractions import Fraction as F

ZDES = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(os.path.dirname(ZDES), 'patenty')
t425 = open(os.path.join(P, 'US7958425.txt')).read()
t213 = open(os.path.join(P, 'US7673213.txt')).read()


def tablica(tekst, nomer):
    i = tekst.find('TABLE-US-%05d' % nomer)
    assert i >= 0, nomer
    j = tekst.find('\n', i)
    return tekst[i:j]


def dizery(stroka):
    """Таблица входного r и выходных w (по K) шаблонов «дизеринга» длины 64."""
    rez = {}
    for m in re.finditer(r'(All|\d+)\s+([rw])\s+\(([\d,\s]+)\)', stroka):
        chisla = [int(x) for x in m.group(3).replace(',', ' ').split()]
        rez[(m.group(1), m.group(2))] = chisla
    return rez


def prostye(stroka):
    return {int(k): (int(p), int(s)) for k, p, s in re.findall(r'(\d+)\t(\d+)\t(\d+)', stroka)}


# --- 1. Перемежитель DRP: таблицы двух патентов совпадают и корректны ---
d425, d213 = dizery(tablica(t425, 21)), dizery(tablica(t213, 38))
p425, p213 = prostye(tablica(t425, 22)), prostye(tablica(t213, 39))
assert d425 == d213 and len(d425) == 9, 'шаблоны дизеринга в двух патентах различаются'
assert p425 == p213 and sorted(p425) == [128, 256, 512, 1024, 2048, 4096, 8192, 16384]
for kl, v in d425.items():
    assert sorted(v) == list(range(64)), ('не перестановка', kl)
for K, (p, s) in p425.items():
    assert math.gcd(p, 2 * K) == 1, ('p не взаимно просто с 2K', K, p)


def drp(K):
    """Перемежитель длины 2K по US7673213, абзац (137): I_a, I_b=(s+ip) mod 2K, I_c."""
    r = d425[('All', 'r')]
    w = d425[(str(K), 'w')]
    p, s = p425[K]
    n = 2 * K
    assert n % 64 == 0
    Ia = [64 * (i // 64) + r[i % 64] for i in range(n)]
    Ib = [(s + i * p) % n for i in range(n)]
    Ic = [64 * (i // 64) + w[i % 64] for i in range(n)]
    # v_out(i) = v_b(Ic(i)) = v_a(Ib(Ic(i))) = v_in(Ia(Ib(Ic(i))))
    return [Ia[Ib[Ic[i]]] for i in range(n)]


for K in p425:
    pi = drp(K)
    assert sorted(pi) == list(range(2 * K))

# --- 2. Решётки внешнего и внутреннего кодов = (1+D,1+D) и аккумулятор ---
t37 = tablica(t213, 37)
zap = re.findall(r'(\d)\t(\d\d)\t(\d{4})\t(\d)', t37)
assert len(zap) == 8
for s, vx, vyx, sl in zap:
    s, b1, b2 = int(s), int(vx[0]), int(vx[1])
    # u = ... b1 b2 ...; c1=c2=b1^u_prev, c3=c4=b1^b2, новое состояние = b2
    ozh = '%d%d%d%d' % (b1 ^ s, b1 ^ s, b1 ^ b2, b1 ^ b2)
    assert vyx == ozh and int(sl) == b2, ('внешняя решётка', s, vx, vyx)
t41 = tablica(t213, 41)
zap = re.findall(r'(\d)\t(\d\d)\t(\d\d)\t(\d)', t41)
assert len(zap) == 8
for s, vx, vyx, sl in zap:
    s, d1, d2 = int(s), int(vx[0]), int(vx[1])
    p1 = s ^ d1
    p2 = p1 ^ d2
    assert vyx == '%d%d' % (p1, p2) and int(sl) == p2, ('внутренняя решётка', s, vx, vyx)

# --- 3. Шаблоны выкалывания (период 32) ---
t42 = tablica(t213, 42)
vykal = {}
for chisl, shabl in re.findall(r'(\d+)/16\t([01]{32})', t42):
    assert shabl.count('1') == 2 * int(chisl), (chisl, shabl)
    vykal[int(chisl)] = shabl
assert sorted(vykal) == list(range(8, 17))
J_1500 = {F(1, 2): 2, F(2, 3): 4, F(4, 5): 8, F(8, 9): 16, F(16, 17): 32}
t40 = tablica(t213, 40)
for r, J in J_1500.items():
    assert '%d/%d\t%d' % (r.numerator, r.denominator, J) in t40
    assert r == F(J, J + 2)

# --- 4. Кодер 1500 и проверочная матрица по его описанию ---
def koder(u, K, J, shabl=None):
    """Возвращает (систематика, чётность) по US7673213, абзацы (133)–(147)."""
    assert len(u) == K and K % 2 == 0
    # внешний код с «хвостом»: первая пара задаёт состояние, выдаётся в конце
    pary = [(u[2 * k], u[2 * k + 1]) for k in range(K // 2)]
    s = pary[0][1]
    c = []
    for b1, b2 in pary[1:] + pary[:1]:
        c += [b1 ^ s, b1 ^ s, b1 ^ b2, b1 ^ b2]
        s = b2
    pi = drp(K)
    v = [c[pi[i]] for i in range(2 * K)]
    L = -(-2 * K // J)
    d = [0] * L
    for i in range(2 * K):
        d[i // J] ^= v[i]
    p, acc = [], 0
    for x in d:
        acc ^= x
        p.append(acc)
    if shabl:
        p = [x for i, x in enumerate(p) if shabl[i % 32] == '1']
    return u, p


def proverki(K, J):
    """Проверки H: для группы m: p_m + p_{m-1} + сумма выходов внешнего кода группы."""
    # выход внешнего кода c[j] как сумма бит u (индексы)
    pary = list(range(1, K // 2)) + [0]
    c_ind = []
    for k in pary:
        a1, a2 = 2 * k, 2 * k + 1
        pred = 2 * k - 1 if k > 0 else K - 1  # u_prev = второй бит предыдущей пары (циклически)
        c_ind += [{a1, pred}, {a1, pred}, {a1, a2}, {a1, a2}]
    pi = drp(K)
    L = -(-2 * K // J)
    H = []
    for m in range(L):
        s = set()
        for i in range(m * J, min((m + 1) * J, 2 * K)):
            s ^= c_ind[pi[i]]
        stroka = {('u', x) for x in s} | {('p', m)}
        if m:
            stroka |= {('p', m - 1)}
        H.append(stroka)
    return H


random.seed(1)
for K in (256, 1024):
    for J in (2, 4, 8):
        H = proverki(K, J)
        for _ in range(5):
            u = [random.getrandbits(1) for _ in range(K)]
            _, p = koder(u, K, J)
            for stroka in H:
                x = 0
                for vid, i in stroka:
                    x ^= u[i] if vid == 'u' else p[i]
                assert x == 0, ('проверка не выполнена', K, J)
        # степени по докладу 802.11-04/0953r3: dv(u)=4, dv(p)=2, dc=2J+2 (при хорошем разносе)
        from collections import Counter
        st_u = Counter()
        for stroka in H:
            for vid, i in stroka:
                if vid == 'u':
                    st_u[i] += 1
        doli_u4 = sum(1 for i in range(K) if st_u[i] == 4) / K
        doli_dc = sum(1 for stroka in H[1:] if len(stroka) == 2 * J + 2) / (len(H) - 1)
        print(f'K={K} J={J}: проверок {len(H)}, доля dv(u)=4: {doli_u4:.3f}, доля dc=2J+2: {doli_dc:.3f}')
        assert doli_u4 > 0.95 and doli_dc > 0.9

# --- 5. Число выходных символов кодера 600 (таблицы 34–36 US7958425) из K и J ---
J_600 = [2, 4, 6, 8, 10, 14, 16, 38]       # таблица 23 (1/2 … 19/20; «10» — строка без названия, 5/6)
t23 = tablica(t425, 23)
jz = [int(x) for x in re.findall(r'(?:^|\t|\s)(\d+)(?=\s|$)', t23.split('Rate\tJ')[1])]
assert jz == J_600, ('таблица 23', jz)
for nomer, bit_na_simv in ((34, 2), (35, 3), (36, 4)):
    stroki = re.findall(r'(\d+)\t(\d+(?:\t\d+){7})', tablica(t425, nomer))
    assert len(stroki) == 8, nomer
    for K, ryad in stroki:
        K = int(K)
        ryad = [int(x) for x in ryad.split('\t')]
        for J, S in zip(J_600, ryad):
            bity = K + -(-2 * K // J)
            ozh = 2 * -(-bity // (2 * bit_na_simv))   # символов всегда чётное число (абзац 105)
            assert S == ozh, (nomer, K, J, S, ozh)
print('таблицы 34–36: число символов = 2*ceil((K+ceil(2K/J))/(2m)) — все 192 значения сошлись')

# --- 6. Скорости LDPC-Gen2 модема Datum M7XC (MIB) = r/(r+p(1-r)) при J∈{2,4,8,16,32}, p=k/16 ---
mib = open(os.path.join(os.path.dirname(ZDES), 'datum', 'M7XC_MIB', 'DATUM-M7XC-MODEM-MIB')).read()
blok = mib[mib.find('rxLdpcModcod OBJECT-TYPE'):]
blok = blok[:blok.find('}')]
skorosti = sorted({F(int(a), int(b)) for a, b in re.findall(r'(\d+)x(\d+)\(', blok)})
semeystvo = {}
for r in J_1500:
    for k in range(8, 17):
        pp = F(k, 16)
        semeystvo.setdefault(r / (r + pp * (1 - r)), (r, pp))
for s in skorosti:
    assert s in semeystvo, ('скорость M7XC вне семейства', s)
print('M7XC LDPC-Gen2: все', len(skorosti), 'скоростей из MIB лежат в семействе r/(r+p(1-r)):',
      ', '.join(f'{s} (J={J_1500[semeystvo[s][0]]}, p={semeystvo[s][1]})' for s in skorosti))

# --- 7. Скорости FlexLDPC M7/PSM-500 (1/2, 2/3, 3/4, 14/17, 7/8, 10/11, 16/17) ---
for s in (F(1, 2), F(2, 3), F(3, 4), F(14, 17), F(7, 8), F(10, 11), F(16, 17)):
    v1500 = s in semeystvo
    J = 2 * s / (1 - s)
    print(f'FlexLDPC {s}: в семействе кодера 1500 — {"да" if v1500 else "нет"}; '
          f'J/(J+2) без выкалывания: J={J}{" (целое)" if J.denominator == 1 else " (не целое)"}')
print('ВСЁ СОВПАЛО')
