"""CCSDS 131.0-B-6 (TM Synchronization and Channel Coding): таблицы, собранные программно
из текста стандарта (istochniki/ccsds/131x0b6ec1.pdf.txt) со сверкой (assert).

Выход: tablicy/ccsds/*.json|*.txt. Запуск: python3 ccsds_tm.py
Каждая проверка ссылается на страницу PDF (номер физической страницы, с 1).
"""
import json, os, re, sys
import numpy as np

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'ccsds')
VYH = os.path.join(KOR, 'tablicy', 'ccsds')
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
os.makedirs(VYH, exist_ok=True)
TXT = open(os.path.join(IST, '131x0b6ec1.pdf.txt'), encoding='utf8').read()


def stranica(n):
    m = re.search(r'=====PAGE %d=====\n(.*?)(?======PAGE|\Z)' % n, TXT, re.S)
    t = m.group(1)
    # шрифт Symbol в PDF: цифры как U+F030..U+F039 — приводим к ASCII
    return ''.join(chr(ord(ch) - 0xF000) if 0xF030 <= ord(ch) <= 0xF039 else ch for ch in t)


def bity_hex(h):
    return [int(b) for c in h for b in format(int(c, 16), '04b')]


def v_hex(bits):
    bits = list(bits)
    bits = bits + [0] * (-len(bits) % 4)
    return ''.join('%X' % int(''.join(map(str, bits[i:i + 4])), 2) for i in range(0, len(bits), 4))


itog = {}

# ---------------------------------------------------------------- 1. ASM/CSM (стр. 67-68, п. 9.3)
s67 = stranica(67) + stranica(68)
csm_hex = {
    '32': '1ACFFC1D', 'turbo_1_2_ldpc': '034776C7272895B0', 'turbo_1_3': '25D5C0CE8990F6C9461BF79C',
    'turbo_1_4': '034776C7272895B0FCB88938D8D76A4F',
    'turbo_1_6': '25D5C0CE8990F6C9461BF79CDA2A3F31766F0936B9E40863'}
for k, h in csm_hex.items():
    assert h[:8] in s67.replace(' ', ''), k
# битовые рисунки 9-1..9-5 в тексте — сверяем с hex
risunki = re.findall(r'\(Bit 0\)(.*?)LAST TRANSMITTED BIT', s67, re.S)
risunki = [''.join(ch for ch in r if ch in '01') for r in risunki]
assert [v_hex(map(int, r)) for r in risunki] == [csm_hex[k] for k in ['32', 'turbo_1_2_ldpc', 'turbo_1_3', 'turbo_1_4', 'turbo_1_6']]
itog['csm'] = {'значения_hex': csm_hex, 'источник': '131x0b6ec1.pdf стр. 66-68 (п. 9.3, рис. 9-1…9-5)',
               'проверка': 'hex из примечания п. 9.3.4 совпал с битовыми рисунками 9-1…9-5 (assert)'}

# ---------------------------------------------------------------- 2. Рандомизаторы (стр. 70-71, п. 10.4)


def lfsr_fib(poly_deg_taps, seed_bits, n):
    """Последовательность a[t]: a[t+L] = XOR a[t+L-d] по d из taps (многочлен x^L + sum x^(L-d)...).
    seed_bits — первые L бит последовательности."""
    L = max(poly_deg_taps)
    a = list(seed_bits)
    while len(a) < n:
        t = len(a) - L
        a.append(sum(a[t + L - d] for d in poly_deg_taps) & 1)
    return a[:n]


def gen_seq(stepen, otvody, nachalo, n):
    """Генератор Фибоначчи: регистр X1..XL (X1 — выход), отводы по степеням многочлена h(x).
    Пробуем обе трактовки (h и взаимный) — выбирается та, что даёт 40 бит из стандарта."""
    raise NotImplementedError


def seq_from_40(first40, L, taps_sets):
    """Определить рекуррентность по 40 известным битам: проверить, какому многочлену они подчиняются."""
    ok = []
    for name, taps in taps_sets.items():
        a = first40
        if all(a[t + L] == (sum(a[t + L - d] for d in taps) & 1) for t in range(len(a) - L)):
            ok.append(name)
    return ok


s71 = stranica(71)
m40 = re.findall(r'((?:[01]{4} ){9}[01]{4}) ?\.', s71.replace('\n', ' '))
assert len(m40) == 2, m40
r17_40 = [int(c) for c in m40[0].replace(' ', '')]
r8_40 = [int(c) for c in m40[1].replace(' ', '')]
# h(x) = x^17 + x^14 + 1: рекуррентность a[t+17] = a[t+14] ^ a[t] (отводы 3 и 17) либо взаимная a[t+17]=a[t+3]^a[t]
w17 = seq_from_40(r17_40, 17, {'a[t+17]=a[t+14]^a[t]': (3, 17), 'a[t+17]=a[t+3]^a[t]': (14, 17)})
w8 = seq_from_40(r8_40, 8, {'h(x)=x8+x7+x5+x3+1 прямая': (1, 3, 5, 8), 'взаимная': (3, 5, 7, 8)})
assert len(w17) == 1 and len(w8) == 1, (w17, w8)
t17 = (3, 17) if w17[0].startswith('a[t+17]=a[t+14]') else (14, 17)
t8 = (1, 3, 5, 8) if 'прямая' in w8[0] else (3, 5, 7, 8)
seq17 = lfsr_fib(t17, r17_40[:17], 131071)
seq8 = lfsr_fib(t8, r8_40[:8], 255)
assert seq17[:40] == r17_40 and seq8[:40] == r8_40
# период
ext = lfsr_fib(t17, r17_40[:17], 131071 + 17)
assert ext[131071:131071 + 17] == seq17[:17]
assert lfsr_fib(t8, r8_40[:8], 263)[255:263] == seq8[:8]
# начальное состояние '11000111000111000': сверяем с первыми 17 битами (прямо или обратно)
seed = '11000111000111000'
first17 = ''.join(map(str, seq17[:17]))
sootv = 'первые 17 бит = состояние X1..X17' if first17 == seed else ('первые 17 бит = состояние в обратном порядке' if first17 == seed[::-1] else 'иное')
# сверка 255-бит с проектом (dlinnye/skrembler) и libfec/gr-satellites: первые байты FF 48 0E C0 9A 0D 70 BC
b8 = bytes(int(''.join(map(str, (seq8 * 8)[i:i + 8])), 2) for i in range(0, 255 * 8, 8))
assert b8[:8].hex().upper() == 'FF480EC09A0D70BC'
open(os.path.join(VYH, 'randomizer_tm_255.hex'), 'w').write(v_hex(seq8) + '\n')
open(os.path.join(VYH, 'randomizer_tm_131071.hex'), 'w').write(v_hex(seq17) + '\n')
itog['randomizer_tm'] = {
    '255': {'h': 'x^8+x^7+x^5+x^3+1', 'нач': 'все единицы', 'рекуррентность': w8[0], 'файл': 'tablicy/ccsds/randomizer_tm_255.hex (255 бит, дополнено нулём до 256)',
            'первые_байты_периода_8x255': b8[:8].hex().upper()},
    '131071': {'h': 'x^17+x^14+1', 'нач': seed, 'рекуррентность': w17[0], 'соответствие_нач': sootv,
               'файл': 'tablicy/ccsds/randomizer_tm_131071.hex (131071 бит, дополнено до кратного 4)'},
    'источник': '131x0b6ec1.pdf стр. 70-71 (п. 10.4.1-10.4.3, 40 первых бит обеих последовательностей)',
    'проверка': 'по 40 битам из примечания 2 п. 10.4.3 найдена рекуррентность, сгенерирована последовательность, период 131071/255 подтверждён (assert); первые байты 255-битной ПСП FF480EC09A0D70BC'}

# ---------------------------------------------------------------- 3. RS (стр. 36, 40, 94-99)
PRIM = 0x187  # F(x)=x^8+x^7+x^2+x+1 (стр. 36, п. 5.3.3)
assert 'x8 + x7 + x2 + x + 1' in stranica(36)
exp = [0] * 510
log = [0] * 256
x = 1
for i in range(255):
    exp[i] = x
    log[x] = i
    x <<= 1
    if x & 0x100:
        x ^= PRIM
for i in range(255, 510):
    exp[i] = exp[i - 255]
assert len(set(exp[:255])) == 255  # примитивность


def gmul(a, b):
    return 0 if a == 0 or b == 0 else exp[log[a] + log[b]]


def rs_gen(E):
    g = [1]
    for j in range(128 - E, 127 + E + 1):
        r = exp[(11 * j) % 255]
        ng = [0] * (len(g) + 1)
        for i, c in enumerate(g):
            ng[i] ^= gmul(c, r)
            ng[i + 1] ^= c
        g = ng
    return g  # g[i] — коэффициент при x^i


def annexG(txt):
    out = {}
    for gi, pw, bits in re.findall(r'G(\d+)\s*(?:=\s*G\d+\s*)?=\s*[^\d\s]?(\d+)((?:\s+[^\d\s]?[01]){8})', txt):
        out[int(gi)] = (int(pw), int(''.join(ch for ch in bits if ch in '01'), 2))
    return out


annexG16 = annexG(stranica(98))
annexG8 = annexG(stranica(99))
assert sorted(annexG16) == list(range(17)) and sorted(annexG8) == list(range(9)), (sorted(annexG16), sorted(annexG8))
rs = {}
for E, ann in ((16, annexG16), (8, annexG8)):
    g = rs_gen(E)
    assert g == g[::-1], 'самовзаимный'
    stepeni = [log[c] for c in g]
    # Annex G: G0=G2E=0 (alpha^0), далее степени
    for i in range(E + 1):
        assert stepeni[i] == ann[i][0] and g[i] == ann[i][1], (E, i, stepeni[i], ann[i])
    rs[E] = {'n': 255, 'k': 255 - 2 * E, 'g_степени_alpha_от_x0': stepeni, 'g_коэфф_обычный_базис_hex': [('%02X' % c) for c in g]}

# Двойной базис: матрица п. 5.3.9.3 (стр. 40)
nums = re.findall(r'[01]', stranica(40).split('[z0, . . ., z7] = [u7, . . ., u0]')[1].split('and')[0])
T = np.array([int(c) for c in nums[:64]]).reshape(8, 8)
nums2 = re.findall(r'[01]', stranica(40).split('[u7, . . ., u0] = [z0, . . ., z7]')[1].split('NOTES')[0])
Ti = np.array([int(c) for c in nums2[:64]]).reshape(8, 8)
assert ((T @ Ti) % 2 == np.eye(8, dtype=int)).all(), 'матрицы взаимно обратны'


def conv2dual(u):  # u — байт обычного базиса [u7..u0] (u7 — старший)
    ub = np.array([(u >> (7 - i)) & 1 for i in range(8)])
    z = ub @ T % 2
    return int(''.join(map(str, z)), 2)  # z0 — старший бит (передаётся первым)


tal = [conv2dual(u) for u in range(256)]
tal1 = [0] * 256
for u, z in enumerate(tal):
    tal1[z] = u
assert len(set(tal)) == 256
# Сверка с таблицей F-1 (стр. 94-97): степень альфа -> [обычный, двойной]
f1 = []
for p in range(94, 98):
    f1 += re.findall(r'(\d+|\*)\s+([01]{8})\s+([01]{8})', stranica(p))
assert len(f1) == 256, len(f1)
for pw, u, z in f1:
    assert tal[int(u, 2)] == int(z, 2), (pw, u, z)
    if pw != '*':
        assert exp[int(pw)] == int(u, 2)
# Сверка с проектом (dlinnye._TAL — строки матрицы) и libfec (Taltab)
proekt_tal = (0x8D, 0xEF, 0xEC, 0x86, 0xFA, 0x99, 0xAF, 0x7B)
assert tuple(int(''.join(map(str, r)), 2) for r in T) == proekt_tal
lf = os.path.join(VYH, 'libfec_gen_ccsds_tal_vyvod.c')  # вывод gen_ccsds_tal.c (libfec, Phil Karn, LGPL), собран gcc
lf = os.path.normpath(lf)
sverka_libfec = None
if os.path.exists(lf):
    src = open(lf).read()
    m = re.search(r'Taltab\[\]\s*=\s*\{(.*?)\}', src, re.S)
    if m:
        lt = [int(v, 16) for v in re.findall(r'0x[0-9a-fA-F]+', m.group(1))]
        assert lt == tal, 'libfec Taltab'
        sverka_libfec = lf
json.dump({'Tal_обычный_в_двойной': tal, 'Tal_двойной_в_обычный': tal1, 'T': T.tolist(), 'T_inv': Ti.tolist()},
          open(os.path.join(VYH, 'rs_dvoinoi_bazis.json'), 'w'))
itog['rs'] = {'поле': 'F(x)=x^8+x^7+x^2+x+1 (0x187), β=α^11, корни β^j, j=128-E…127+E', 'коды': rs,
              'двойной_базис': 'tablicy/ccsds/rs_dvoinoi_bazis.json',
              'источник': '131x0b6ec1.pdf стр. 35-42 (разд. 5), стр. 90-97 (прил. F, табл. F-1), стр. 98-99 (прил. G)',
              'проверка': 'g(x) вычислен и совпал со степенями α в прил. G для E=16 и E=8; самовзаимность; матрицы п.5.3.9.3 взаимно обратны; все 256 строк табл. F-1 совпали; строки T = _TAL проекта (dlinnye.py:568)' + ('; Taltab libfec совпал' if sverka_libfec else '')}

# ---------------------------------------------------------------- 4. Свёрточный код и выкалывание (стр. 32, 34)
s32, s34 = stranica(32), stranica(34)
assert '1111001 (171 octal)' in s32 and '1011011(133 octal)' in s32.replace(' (133', '(133')
assert int('1111001', 2) == 0o171 and int('1011011', 2) == 0o133
vyk = {}
for cr, c1, c2 in re.findall(r'C1: ([01 ]+?)\s+C2: ([01 ]+?)\s+(\d/\d)', s34.replace('\n', ' ')) and [] or []:
    pass
pat = re.findall(r'C1:\s*([01](?: [01])*)\s+C2:\s*([01](?: [01])*)\s+(\d/\d)', ' '.join(s34.split()))
for c1, c2, cr in pat:
    c1 = [int(v) for v in c1.split()]
    c2 = [int(v) for v in c2.split()]
    L = len(c1)
    num, den = map(int, cr.split('/'))
    assert (sum(c1) + sum(c2)) * num == L * den  # скорость
    vyk[cr] = {'C1': c1, 'C2': c2}
assert set(vyk) == {'2/3', '3/4', '5/6', '7/8'}
itog['svertochnyi'] = {'G1': '171 (1111001)', 'G2': '133 (1011011)', 'K': 7, 'инверсия': 'выход G2 инвертирован (только базовый 1/2, не у выколотых)',
                       'порядок': 'C1(1), ~C2(1), C1(2), ~C2(2)…', 'выкалывание': vyk,
                       'источник': '131x0b6ec1.pdf стр. 32 (п. 4.3), стр. 34 (табл. 4-1)',
                       'проверка': 'двоичная запись = восьмеричной; скорость каждого шаблона выкалывания = заявленной (assert); libfec V27POLYA=0x4F/V27POLYB=0x6D — те же многочлены в обратном порядке бит'}

# ---------------------------------------------------------------- 5. Турбокод (стр. 45-52)
PQ = [31, 37, 43, 47, 53, 59, 61, 67]
assert all(str(p) in stranica(46) for p in PQ)


def pi_ccsds(k, k1=8):
    k2 = k // k1
    out = []
    for s in range(1, k + 1):
        m = (s - 1) % 2
        i = (s - 1) // (2 * k2)
        j = (s - 1) // 2 - i * k2
        t = (19 * i + 1) % (k1 // 2)
        q = t % 8 + 1
        c = (PQ[q - 1] * j + 21 * m) % k2
        out.append(2 * (t + c * (k1 // 2) + 1) - m)
    return out


turbo = {}
for k in (1784, 3568, 7136, 8920):
    p = pi_ccsds(k)
    assert sorted(p) == list(range(1, k + 1)), k
    # сверка с AFF3CT (Interleaver_core_CCSDS.cpp): lut[i] = 2*(t+c*k1/2+1)-m-1 (с нуля)
    turbo[k] = p
    json.dump(p, open(os.path.join(VYH, 'turbo_perm_%d.json' % k), 'w'))
aff = os.path.join(REPOS, 'aff3ct', 'src', 'Tools', 'Interleaver', 'CCSDS', 'Interleaver_core_CCSDS.cpp')
src = open(aff).read()
assert 'constexpr int p[8] = { 31, 37, 43, 47, 53, 59, 61, 67 }' in src and 'return 2 * (t + c * (k_1 / 2) + 1) - m - 1;' in src


def pi_aff3ct(k):  # дословный перенос формулы AFF3CT, индексы с нуля
    k1, k2 = 8, k // 8
    res = []
    for index in range(k):
        m = index % 2
        i = index // (2 * k2)
        j = (index // 2) - i * k2
        t = (19 * i + 1) % (k1 // 2)
        q = t % 8
        c = (PQ[q] * j + 21 * m) % k2
        res.append(2 * (t + c * (k1 // 2) + 1) - m - 1)
    return res


for k in turbo:
    assert [v + 1 for v in pi_aff3ct(k)] == turbo[k]
# длины кодовых слов (табл. 7-2) и перемежитель канала (табл. 7-4)
t72 = re.findall(r'(1784|3568|7136|8920)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', ' '.join(stranica(45).split()))
for row in t72:
    k = int(row[0])
    for r, n in zip((2, 3, 4, 6), map(int, row[1:])):
        assert n == (k + 4) * r
t74 = re.findall(r'(1784|3568|7136|8920)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)', ' '.join(stranica(52).split()))
assert len(t74) == 4
for row in t74:
    k = int(row[0])
    vals = list(map(int, row[1:]))
    for idx, r in enumerate((2, 3, 4, 6)):
        n, Nr = vals[2 * idx], vals[2 * idx + 1]
        assert n == (k + 4) * r and Nr * (4 * r) == n
itog['turbo'] = {
    'k': [1784, 3568, 7136, 8920], 'k1': 8, 'k2': '223·I', 'простые': PQ,
    'G0_обратная': '10011', 'G1': '11011', 'G2': '10101', 'G3': '11111', 'состояний': 16,
    'выходы': {'1/2': '(0a,1a,0a,1b) повтор (k+4)/2 раз', '1/3': '(0a,1a,1b)', '1/4': '(0a,2a,3a,1b)', '1/6': '(0a,1a,2a,3a,1b,3b)'},
    'хвост': '4 такта, регистры в нуль, систематический выход тоже даёт 4 бита', 'n': '(k+4)/r',
    'перемежитель_канала': 'строка-столбец глубины 4: запись строками по Nc=4/r (8/12/16/24), чтение столбцами, Nr=n/Nc (447/893/1785/2231), B-6 новинка',
    'файлы': ['tablicy/ccsds/turbo_perm_%d.json (π(s), s=1..k, с единицы)' % k for k in turbo],
    'источник': '131x0b6ec1.pdf стр. 44-53 (разд. 7, табл. 7-1…7-4)',
    'проверка': 'π — перестановка для всех 4 k; совпадает поэлементно с AFF3CT Interleaver_core_CCSDS.cpp (MIT); n=(k+4)/r и Nr·Nc=n по табл. 7-2/7-4 (assert)'}

# ---------------------------------------------------------------- 6. LDPC C2 (8160,7136) (стр. 55-60, 86-87)
t81 = re.findall(r'A(\d),(\d+)\s+(\d+), (\d+)\s+(\d+), (\d+)', ' '.join(stranica(57).split()))
assert len(t81) == 32
N = 511
circ = {}
for r, c, a, b, abs1, abs2 in t81:
    r, c, a, b, abs1, abs2 = map(int, (r, c, a, b, abs1, abs2))
    assert abs1 == (c - 1) * N + a and abs2 == (c - 1) * N + b
    circ[(r - 1, c - 1)] = (a, b)
# сверка с ldpc-toolbox (C2_CIRCULANTS)
lt = open(os.path.join(REPOS, 'ldpc-toolbox', 'src', 'codes', 'ccsds.rs')).read()
blk = lt.split('static C2_CIRCULANTS')[1].split('];\n\n')[0]
pairs = [tuple(map(int, p)) for p in re.findall(r'\[(\d+), (\d+)\]', blk)]
assert pairs == [circ[(r, c)] for r in range(2) for c in range(16)]
H = np.zeros((2 * N, 16 * N), dtype=np.uint8)
for (r, c), pos in circ.items():
    for j in range(N):
        for p in pos:
            H[r * N + j, c * N + (j + p) % N] ^= 1
# генератор из прил. C (стр. 86-87): 128 hex-цифр, первая — восьмеричная (3 бита)
tc = (stranica(86) + stranica(87))
bl = re.findall(r'b(\d+),(\d)\s*\n?\s*([0-9A-F]{60,70})\s*\n\s*([0-9A-F]{60,70})', tc)
assert len(bl) == 28, len(bl)
G = np.zeros((14 * N, 16 * N), dtype=np.uint8)
for i in range(14):
    G[i * N:(i + 1) * N, i * N:(i + 1) * N] = np.eye(N, dtype=np.uint8)
bstrok = {}
for i, j, h1, h2 in bl:
    hx = h1 + h2
    assert len(hx) == 128
    bits = [int(b) for b in format(int(hx[0], 16), '03b')] + bity_hex(hx[1:])
    assert len(bits) == N
    bstrok[(int(i), int(j))] = hx
    row0 = np.array(bits, dtype=np.uint8)
    for s in range(N):
        G[(int(i) - 1) * N + s, (14 + int(j) - 1) * N:(15 + int(j) - 1) * N] = np.roll(row0, s)
prod = (G.astype(np.float32) @ H.T.astype(np.float32)) % 2
assert not prod.any(), 'G·H^T = 0'
json.dump({'Z': N, 'H_единицы_первой_строки_циркулянтов': {'A%d,%d' % (r + 1, c + 1): list(v) for (r, c), v in circ.items()},
           'G_первые_строки_B_hex': {'b%d,%d' % k: v for k, v in bstrok.items()},
           'укорочение': '18 нулей спереди (не передаются) + 7136 инф. + 1022 проверочных + 2 нуля в конце = 8160',
           'H_строки': [np.nonzero(H[r])[0].tolist() for r in range(2 * N)]},
          open(os.path.join(VYH, 'ldpc_c2_8176_7154.json'), 'w'))
itog['ldpc_c2'] = {'n,k': '(8160,7136) из базового (8176,7156) → подкод (8176,7154)', 'H': '2×16 циркулянтов 511×511, вес 2', 'файл': 'tablicy/ccsds/ldpc_c2_8176_7154.json',
                   'источник': '131x0b6ec1.pdf стр. 55-60 (п. 8.3, табл. 8-1), стр. 86-87 (прил. C, табл. C-1)',
                   'проверка': 'абсолютные позиции табл. 8-1 = (столбец-1)·511 + позиция; циркулянты совпали с ldpc-toolbox (MIT/Apache); G(прил. C)·Hᵀ = 0 над GF(2) для всех 7154 строк (assert)'}

# ---------------------------------------------------------------- 7. AR4JA (стр. 60-64)
tt = ' '.join((stranica(62)).split())
tt4 = ' '.join((stranica(63)).split())
theta = {}
phi = {0: {}, 1: {}, 2: {}, 3: {}}
body = tt.split('M = 27 … 213 M = 27 … 213')[1]
nums = [int(v) for v in re.findall(r'\d+', body)]
assert len(nums) == 26 * 16, len(nums)
for r in range(26):
    row = nums[r * 16:(r + 1) * 16]
    assert row[0] == r + 1
    theta[r + 1] = row[1]
    phi[0][r + 1] = row[2:9]
    phi[1][r + 1] = row[9:16]
body4 = tt4.split('M = 27 … 213 M = 27 … 213')[1].split('8.4.3')[0]
nums4 = [int(v) for v in re.findall(r'\d+', body4)]
assert len(nums4) == 26 * 16, len(nums4)
for r in range(26):
    row = nums4[r * 16:(r + 1) * 16]
    assert row[0] == r + 1 and row[1] == theta[r + 1]
    phi[2][r + 1] = row[2:9]
    phi[3][r + 1] = row[9:16]
# сверка с ldpc-toolbox THETA_K, PHI_K
th_lt = [int(v) for v in re.findall(r'\d+', lt.split('static THETA_K: [u8; 26] = [')[1].split('];')[0])]
assert th_lt == [theta[k] for k in range(1, 27)]
phi_blk = lt.split('static PHI_K')[1].split('];\n\n')[0].split('=', 1)[1]
phi_nums = [int(v) for v in re.findall(r'\d+', re.sub(r'//[^\n]*', '', phi_blk))]
assert phi_nums == [v for j in range(4) for k in range(1, 27) for v in phi[j][k]], 'PHI_K'
MTAB = {(1024, '1/2'): 512, (1024, '2/3'): 256, (1024, '4/5'): 128, (4096, '1/2'): 2048, (4096, '2/3'): 1024, (4096, '4/5'): 512,
        (16384, '1/2'): 8192, (16384, '2/3'): 4096, (16384, '4/5'): 2048}


def pi_k(k, i, M):
    lg = M.bit_length() - 1
    j = 4 * i // M
    return (M // 4) * ((theta[k] + j) % 4) + (phi[j][k][lg - 7] + i) % (M // 4)


def ar4ja_H(kinf, rate):
    """Структура H_1/2, H_2/3, H_4/5 — п. 8.4.2.2-8.4.2.3 (рисунки-формулы; раскладка блоков по ldpc-toolbox ccsds.rs)."""
    M = MTAB[(kinf, rate)]
    extra = {'1/2': 0, '2/3': 2, '4/5': 6}[rate] * M
    rows = [set() for _ in range(3 * M)]

    def tog(r, c):
        rows[r] ^= {c}

    for i in range(M):
        tog(i, extra + 2 * M + i)
        tog(i, extra + 4 * M + i); tog(i, extra + 4 * M + pi_k(1, i, M))
        tog(M + i, extra + i); tog(M + i, extra + M + i); tog(M + i, extra + 3 * M + i)
        for kk in (2, 3, 4):
            tog(M + i, extra + 4 * M + pi_k(kk, i, M))
        tog(2 * M + i, extra + i)
        for kk in (5, 6):
            tog(2 * M + i, extra + M + pi_k(kk, i, M))
        for kk in (7, 8):
            tog(2 * M + i, extra + 3 * M + pi_k(kk, i, M))
        tog(2 * M + i, extra + 4 * M + i)
    if rate != '1/2':
        e2 = 0 if rate == '2/3' else 4 * M
        for i in range(M):
            for kk in (9, 10, 11):
                tog(M + i, e2 + pi_k(kk, i, M))
            tog(M + i, e2 + M + i)
            tog(2 * M + i, e2 + i)
            for kk in (12, 13, 14):
                tog(2 * M + i, e2 + M + pi_k(kk, i, M))
    if rate == '4/5':
        for i in range(M):
            for kk in (21, 22, 23):
                tog(M + i, pi_k(kk, i, M))
            tog(M + i, M + i)
            for kk in (15, 16, 17):
                tog(M + i, 2 * M + pi_k(kk, i, M))
            tog(M + i, 3 * M + i)
            tog(2 * M + i, i)
            for kk in (24, 25, 26):
                tog(2 * M + i, M + pi_k(kk, i, M))
            tog(2 * M + i, 2 * M + i)
            for kk in (18, 19, 20):
                tog(2 * M + i, 3 * M + pi_k(kk, i, M))
    n_full = extra + 5 * M
    return M, n_full, [sorted(r) for r in rows]


def rank_gf2(rows, n):
    import numpy as _np
    W = (n + 63) // 64
    A = _np.zeros((len(rows), W), dtype=_np.uint64)
    for r, cols in enumerate(rows):
        for c in cols:
            A[r, c >> 6] |= _np.uint64(1) << _np.uint64(c & 63)
    rk = 0
    for c in range(n):
        w, b = c >> 6, _np.uint64(1) << _np.uint64(c & 63)
        col = (A[rk:, w] & b) != 0
        idx = _np.nonzero(col)[0]
        if len(idx) == 0:
            continue
        p = rk + idx[0]
        A[[rk, p]] = A[[p, rk]]
        others = _np.nonzero((A[:, w] & b) != 0)[0]
        others = others[others != rk]
        A[others] ^= A[rk]
        rk += 1
        if rk == len(rows):
            break
    return rk


proekt = json.load(open('/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_prochie.json'))
LAB = open(os.path.join(REPOS, 'labrador-ldpc', 'src', 'codes', 'compact_parity_checks.rs')).read()


def lab_proto(name):
    blk = LAB.split('pub static %s: [[[u8; 11]; 4]; 3] = [' % name)[1].split('\n];')[0]
    blk = re.sub(r'//[^\n]*', '', blk)
    cells = re.findall(r'(HZ|HI|HP|0)(?:\s*\|\s*(\d+))?\s*[,\]]', blk)
    assert len(cells) == 3 * 4 * 11, (name, len(cells))
    return [[[cells[(p * 4 + r) * 11 + c] for c in range(11)] for r in range(4)] for p in range(3)]


def lab_phi(M):
    blk = LAB.split('pub static PHI_J_K_M%d: [[u16; 26]; 4] = [' % M)[1].split('];')[0]
    v = [int(x) for x in re.findall(r'\d+', blk)]
    return [v[j * 26:(j + 1) * 26] for j in range(4)]


lab_theta = [int(x) for x in re.findall(r'\d+', LAB.split('pub static THETA_K: [u8; 26] = [')[1].split('];')[0])]


def lab_expand(name, M, n_block_rows, phi_tab=None):
    proto = lab_proto(name)
    rows = [set() for _ in range(n_block_rows * M)]
    for part in proto:
        for br in range(n_block_rows):
            for bc in range(11):
                kind, val = part[br][bc]
                if kind in ('0', 'HZ'):
                    continue
                for i in range(M):
                    if kind == 'HI':
                        c = (i + int(val)) % M if val else i
                    else:  # HP|k-1 — перестановка π_k
                        k = int(val) + 1
                        j = 4 * i // M
                        c = (M // 4) * ((lab_theta[k - 1] + j) % 4) + (phi_tab[j][k - 1] + i) % (M // 4)
                    rows[br * M + i] ^= {bc * M + c}
    return [sorted(r) for r in rows]

ar = {}
for (kinf, rate), M in MTAB.items():
    M_, n_full, rows = ar4ja_H(kinf, rate)
    n_tx = n_full - M
    num, den = map(int, rate.split('/'))
    assert n_tx * num == kinf * den
    zap = {'k': kinf, 'скорость': rate, 'M': M, 'n_передаётся': n_tx, 'n_с_выколотыми': n_full,
           'выколоты': '%d-%d (последние M столбцов)' % (n_tx, n_full - 1), 'строк_H': 3 * M}
    if M <= 1024:
        rk = rank_gf2(rows, n_full)
        assert rk == 3 * M, (kinf, rate, rk)
        zap['ранг_H'] = rk
    ar['ar4ja-%d-%d' % (n_full, kinf)] = zap
    json.dump({'семейство': 'AR4JA', 'n': str(n_full), 'k': str(kinf), 'строки': rows, 'скорость': rate,
               'выколоты': '%d-%d' % (n_tx, n_full - 1),
               'откуда': 'CCSDS 131.0-B-6 п. 8.4, табл. 8-2…8-4 (стр. 60-63), собрано ccsds_tm.py'},
              open(os.path.join(VYH, 'ldpc_ar4ja_%d_%s.json' % (kinf, rate.replace('/', '_'))), 'w'), ensure_ascii=False)
    lab = lab_expand({'1/2': 'TM_R12_H', '2/3': 'TM_R23_H', '4/5': 'TM_R45_H'}[rate], M, 3, lab_phi(M))
    assert lab == rows, ('labrador', kinf, rate)
    zap['сверка_labrador'] = 'H совпала построчно с развёрткой labrador-ldpc (MIT) compact_parity_checks.rs'
    if (kinf, rate) == (4096, '1/2'):
        pr = proekt['ar4ja-10240-4096']['строки']
        sovp = len(set(map(tuple, pr)) & set(map(tuple, rows)))
        zap['сверка_с_проектом'] = ('H совпала с ar4ja-10240-4096 проекта' if sovp == len(rows) else
            'НЕ совпала с ar4ja-10240-4096 проекта (AFF3CT AR4JA_4096_8192.qc, база 12×20, Z=512): общих строк %d из %d; '
            'протограф (веса столбцов по блокам) тот же, подъём другой — в проекте не код CCSDS побитно' % (sovp, len(rows)))
itog['ldpc_ar4ja'] = {'коды': ar, 'формула_π': 'π_k(i) = M/4·((θ_k + ⌊4i/M⌋) mod 4) + (φ_k(⌊4i/M⌋, M) + i) mod (M/4)',
                      'файлы': 'tablicy/ccsds/ldpc_ar4ja_<k>_<r>.json (формат ldpc_prochie.json проекта)',
                      'источник': '131x0b6ec1.pdf стр. 60-64 (п. 8.4, табл. 8-2…8-5)',
                      'проверка': 'θ_k и φ_k(j,M) из табл. 8-3/8-4 совпали со всеми 26×4×7 числами ldpc-toolbox (MIT/Apache); все 9 матриц H построчно совпали с независимой развёрткой labrador-ldpc (MIT); ранг H = 3M для M≤1024. ВНИМАНИЕ: ar4ja-10240-4096 проекта (из AFF3CT) с CCSDS не совпадает'}
json.dump(itog, open(os.path.join(VYH, '_itog_ccsds_tm.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps({k: v.get('проверка', '') for k, v in itog.items()}, ensure_ascii=False, indent=1))
