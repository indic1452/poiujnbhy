"""Дополнение (проверка полноты): коды прикладного уровня IETF (FEC Building Block / FECFRAME / RTP):
РС над GF(2^m) RFC 5510 (FEC ID 2, 5, 129) и RFC 6865, LDPC-Staircase/Triangle RFC 5170 и RFC 6816, RLC RFC 8681 (+TinyMT32
RFC 8682), XOR-чётность RTP RFC 5109 / 6015 / 8627, реестр FEC Encoding ID IANA. Таблицы с assert → tablicy/dop/."""
import itertools, json, os, re, sys
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka
from sympy import factorint
R = 'istochniki/dop/rfc/'
T = lambda n: R + 'rfc%d.txt' % n
os.makedirs(os.path.join(KOR, 'tablicy', 'dop'), exist_ok=True)

# ---- RFC 5510 разд. 8.1: примитивные многочлены m = 2…16 ----
txt = open(os.path.join(KOR, T(5510))).read()
poly = {}
for m, bits, expr in re.findall(r'm = (\d+), "([01]+)",? \(([^)]*)\)', txt):
    m = int(m); v = sum(1 << i for i, b in enumerate(bits) if b == '1')  # запись «младшая степень слева»
    e = 0
    for t in expr.replace(' ', '').split('+'):
        e |= 1 if t == '1' else 2 if t == 'x' else 1 << int(t.split('^^')[1])
    assert v == e and v.bit_length() - 1 == m, (m, bits, expr)
    poly[m] = v
assert sorted(poly) == list(range(2, 17))
def mulmod(a, b, p, m):
    r = 0
    while b:
        if b & 1: r ^= a
        b >>= 1; a <<= 1
        if a >> m: a ^= p
    return r
def primitiven(p):
    m = p.bit_length() - 1; N = (1 << m) - 1
    def pw(e):
        r, a = 1, 2
        while e:
            if e & 1: r = mulmod(r, a, p, m)
            a = mulmod(a, a, p, m); e >>= 1
        return r
    return pw(N) == 1 and all(pw(N // q) != 1 for q in factorint(N))
assert all(primitiven(p) for p in poly.values())
# порождающая матрица GM = V_kk^-1 · V_kn (разд. 8.2): систематичность и MDS для GF(16), k = 5, n = 15
def gf(m):
    p = poly[m]; N = (1 << m) - 1; exp = [0] * (2 * N); a = 1
    for i in range(N):
        exp[i] = exp[i + N] = a; a = mulmod(a, 2, p, m)
    log = {exp[i]: i for i in range(N)}
    mul = lambda x, y: 0 if not x or not y else exp[log[x] + log[y]]
    inv = lambda x: exp[(N - log[x]) % N]
    return N, exp, mul, inv
def gauss_inv(A, mul, inv):
    n = len(A); M = [row[:] + [int(i == j) for j in range(n)] for i, row in enumerate(A)]
    for c in range(n):
        r = next(r for r in range(c, n) if M[r][c]); M[c], M[r] = M[r], M[c]
        iv = inv(M[c][c]); M[c] = [mul(x, iv) for x in M[c]]
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c]; M[r] = [x ^ mul(f, y) for x, y in zip(M[r], M[c])]
    return [row[n:] for row in M]
def rang(A, mul, inv):
    A = [r[:] for r in A]; rk = 0; cols = len(A[0])
    for c in range(cols):
        piv = next((r for r in range(rk, len(A)) if A[r][c]), None)
        if piv is None: continue
        A[rk], A[piv] = A[piv], A[rk]; iv = inv(A[rk][c])
        for r in range(len(A)):
            if r != rk and A[r][c]:
                f = mul(A[r][c], iv); A[r] = [x ^ mul(f, y) for x, y in zip(A[r], A[rk])]
        rk += 1
    return rk
N, exp, mul, inv = gf(4); k = 5
V = [[exp[(i * j) % N] for j in range(N)] for i in range(k)]
Vi = gauss_inv([row[:k] for row in V], mul, inv)
GM = [[0] * N for _ in range(k)]
for i in range(k):
    for j in range(N):
        s = 0
        for t in range(k): s ^= mul(Vi[i][t], V[t][j])
        GM[i][j] = s
assert all(GM[i][j] == int(i == j) for i in range(k) for j in range(k))  # систематический
mds = all(rang([[GM[i][j] for j in cols] for i in range(k)], mul, inv) == k for cols in itertools.combinations(range(N), k))
assert mds
json.dump({'RFC 5510 разд. 8.1': {str(m): {'hex': '0x%X' % p, 'примитивен': True} for m, p in sorted(poly.items())},
           'пример GM, m=4, k=5, n=15 (разд. 8.2)': GM},
          open(os.path.join(KOR, 'tablicy', 'dop', 'rfc5510_rs.json'), 'w'), ensure_ascii=False, indent=1)

# ---- PRNG ----
def pmms(seed):  # RFC 5170 разд. 5.7: Park–Miller, A = 16807, M = 2^31 − 1
    x = seed
    while True:
        x = x * 16807 % 2147483647; yield x
g = pmms(1)
for _ in range(9999): next(g)
assert next(g) == 1043618065  # критерий RFC 5170: 10 000-е значение при seed = 1
M32 = 0xFFFFFFFF
class TinyMT32:  # RFC 8682 рис. 1
    def __init__(s, seed):
        s.mat1, s.mat2, s.tmat = 0x8f7011ee, 0xfc78ff1f, 0x3793fdff
        s.st = [seed & M32, s.mat1, s.mat2, s.tmat]
        for i in range(1, 8):
            p = s.st[(i - 1) & 3]
            s.st[i & 3] ^= (i + 1812433253 * (p ^ (p >> 30))) & M32
        for _ in range(8): s.nxt()
    def nxt(s):
        st = s.st; y = st[3]; x = (st[0] & 0x7fffffff) ^ st[1] ^ st[2]
        x ^= (x << 1) & M32; y ^= (y >> 1) ^ x
        st[0] = st[1]; st[1] = st[2]; st[2] = (x ^ (y << 10)) & M32; st[3] = y
        if y & 1: st[1] ^= s.mat1; st[2] ^= s.mat2
    def u32(s):
        s.nxt(); st = s.st
        t0 = st[3]; t1 = (st[0] + (st[2] >> 8)) & M32; t0 ^= t1
        if t1 & 1: t0 ^= s.tmat
        return t0
def chisla_posle(fn, metka, n=50):
    t = open(os.path.join(KOR, fn)).read(); i = t.index(metka)
    return [int(x) for x in re.findall(r'\b\d+\b', t[i + len(metka):])][:n]
ref32 = chisla_posle(T(8682), 'Figure 2, which are to be read\n   line by line.  Note that these values come from the tinymt/\n   check32.out.txt file provided by the PRNG authors to validate\n   implementations of TinyMT32 as part of the MersenneTwister-Lab/TinyMT\n   GitHub repository.')
s = TinyMT32(1); assert [s.u32() for _ in range(50)] == ref32
t81 = open(os.path.join(KOR, T(8681))).read()
def blok(t, nach, kon):
    i = t.index(nach); j = t.index(kon, i); return [int(x) for x in re.findall(r'\b\d+\b', t[i + len(nach):j])]
ref256 = blok(t81, 'provided in Figure 9, to be read line by line.', 'Figure 9: First 50')
ref16 = blok(t81, 'provided in Figure 10, to be read line by line.', 'Figure 10: First 50')
s = TinyMT32(1); assert [s.u32() & 0xFF for _ in range(50)] == ref256
s = TinyMT32(1); assert [s.u32() & 0xF for _ in range(50)] == ref16
assert 'x^(8) + x^(4) + x^(3) + x^(2) + 1' in t81 and poly[8] == 0x11D
def coeffs(repair_key, cc_nb, dt, m):  # RFC 8681 рис. 3
    if m == 1 and dt == 15: return [1] * cc_nb
    s = TinyMT32(repair_key); out = []
    for _ in range(cc_nb):
        if m == 1: out.append(1 if (s.u32() & 0xF) <= dt else 0)
        elif dt == 15:
            c = 0
            while c == 0: c = s.u32() & 0xFF
            out.append(c)
        else:
            if (s.u32() & 0xF) <= dt:
                c = 0
                while c == 0: c = s.u32() & 0xFF
                out.append(c)
            else: out.append(0)
    return out
json.dump({'TinyMT32 seed=1, первые 50 (RFC 8682 рис. 2)': ref32, 'rand256 (RFC 8681 рис. 9)': ref256, 'rand16 (RFC 8681 рис. 10)': ref16,
           'Park–Miller seed=1: 10000-е = 1043618065 (RFC 5170 разд. 5.7)': True,
           'пример коэффициентов RLC GF(2^8), repair_key=0, 10 шт., DT=15': coeffs(0, 10, 15, 8),
           'пример коэффициентов RLC GF(2), repair_key=0, 16 шт., DT=7': coeffs(0, 16, 7, 1)},
          open(os.path.join(KOR, 'tablicy', 'dop', 'ietf_prng.json'), 'w'), ensure_ascii=False, indent=1)

# ---- IANA ----
ns = '{http://www.iana.org/assignments}'
reg = {}
for r in ET.parse(os.path.join(KOR, 'istochniki/dop/iana/rmt-fec-parameters.xml')).getroot().iter(ns + 'registry'):
    tt = r.find(ns + 'title')
    rows = []
    for rec in r.findall(ns + 'record'):
        v = rec.find(ns + 'value').text; d = ' '.join((rec.find(ns + 'description').text or '').split())
        refs = [x.get('data') for x in rec.iter(ns + 'xref')]
        rows.append(dict(значение=v, описание=d, ссылки=refs))
    if rows: reg[(tt.text if tt is not None else r.get('id'))] = rows
assert any(x['значение'] == '2' and 'Reed-Solomon' in x['описание'] for x in reg['ietf:rmt:fec:encoding - Fully-Specified FEC schemes (0-127)'])
json.dump(reg, open(os.path.join(KOR, 'tablicy', 'dop', 'iana_fec_encoding_id.json'), 'w'), ensure_ascii=False, indent=1)

S = lambda n, obr, st=0: stroka(T(n), obr, st)
zap = []
SL = 'НЕТ: прикладные FEC IETF (над пакетами/символами, не над битами) проект не снимает; для опознания — поле FEC Encoding ID и FEC Payload ID в заголовках ALC/FLUTE/FECFRAME'
zap.append(Z('РС над GF(2^m) для стираний (RFC 5510, FEC Encoding ID 2; ID 5 — m = 8; ID 129 — Small Block Systematic)', 'Прикладные FEC IETF (стирания пакетов)', 'РС',
             {'поле': 'GF(2^m), m = 2…16, многочлены разд. 8.1 (m=8: 1+x^2+x^3+x^4+x^8 = 0x11D); таблица tablicy/dop/rfc5510_rs.json',
              'кодер': 'e = s·GM, GM = V_{k,k}^{-1}·V_{k,n}, V_{i,j} = α^(i·j), i < k, j < n = 2^m − 1 — систематический МДР-код (Вандермонд, как fec.c Риццо); укорочение — первые n′ столбцов',
              'параметры передачи': 'FEC OTI: m (8 бит, по умолч. 8), G (символов в пакете), E (длина символа), B (макс. длина блока), max_n; FEC Payload ID ID2: SBN (32−m бит) + ESI (m бит); ID5: SBN 24 + ESI 8',
              'декодер': 'любые k принятых символов → обращение k×k-подматрицы GM'},
             'FLUTE/ALC (файловое вещание: 3GPP MBMS, DVB-IPDC, ATSC 3.0 ROUTE — как альтернатива Raptor), NORM',
             [I(T(5510), 'строки %d–%d (разд. 8.1, многочлены), %d–%d (разд. 8.2, GM)' % (S(5510, 'm = 2, "111"'), S(5510, 'm = 16, "1101'), S(5510, '8.2.1.  Encoding Principles', 900), S(5510, 'GM = (V_{k,k}^^-1) * V_{k,n}'))),
              I(T(5510), 'строки %d (ID 2), %d (ID 5), %d (ID 129)' % (S(5510, '4.  Formats and Codes with FEC Encoding ID 2', 300), S(5510, '5.  Formats and Codes with FEC Encoding ID 5', 500), S(5510, '7.  Small Block Systematic FEC Scheme (FEC Encoding ID 129) and Reed-', 800)))],
             'все 15 многочленов разд. 8.1 примитивны (свой тест, assert), двоичная и формульная записи совпали; GM для m=4, k=5, n=15 систематична и МДР (все 3003 подматрицы 5×5 обратимы)', SL))
zap.append(Z('Simple Reed-Solomon FEC Scheme для FECFRAME (RFC 6865, FECFRAME FEC Encoding ID 8)', 'Прикладные FEC IETF (стирания пакетов)', 'РС',
             {'код': 'тот же РС RFC 5510 (поле и GM разд. 8), m = 8 по умолчанию', 'применение': 'FEC Framework RFC 6363: исходные пакеты потока (ADU) → блок символов, ремонтные пакеты отдельным потоком'},
             'FECFRAME (RTP/UDP-потоки реального времени)',
             [I(T(6865), 'весь текст (разд. 4–5)'), I(T(6363), 'архитектура FECFRAME')],
             'определение кода — ссылкой на RFC 5510 (проверен выше); номер ID — реестр IANA (tablicy/dop/iana_fec_encoding_id.json)', SL))
for nid, nazv in ((3, 'LDPC-Staircase'), (4, 'LDPC-Triangle')):
    zap.append(Z('%s (RFC 5170, FEC Encoding ID %d)' % (nazv, nid), 'Прикладные FEC IETF (стирания пакетов)', 'LDPC',
                 {'H': '(n−k)×n = [H1 | H2]; H1 — k столбцов по N1 единиц (N1 = 3…10, по умолч. 3), расставленных ГПСЧ (разд. 5.3), строки с <2 единиц дополняются; H2 — %s'
                       % ('«лестница»: диагональ и поддиагональ (двухдиагональная)' if nid == 3 else 'нижнетреугольная (right_matrix_staircase_init разд. 7): лестница + в строке i единицы в столбцах k+j, j выбирается pmms_rand убывающей цепочкой'),
                  'ГПСЧ': 'Park–Miller «minimal standard»: I(j+1) = 16807·I(j) mod (2^31 − 1); pmms_rand(maxv) — масштабирование в [0, maxv); seed из FEC OTI',
                  'параметры передачи': 'FEC OTI (EXT_FTI, HET=64): L 48 бит, E 16, N1m3 3 (N1 = N1m3 + 3), G 5, B 20, max_n 16, seed ГПСЧ 32 (1…2^31−2); FEC Payload ID: SBN 12 бит + ESI 20 бит',
                  'кодирование': 'ремонтные символы — XOR-суммы по строкам (лестница решается последовательно)', 'таблица': 'tablicy/dop/ietf_prng.json'},
                 'FLUTE/ALC, NORM (файловое многоадресное вещание)',
                 [I(T(5170), 'строки %d–%d (ГПСЧ и критерий), %d (построение H1)' % (S(5170, 'The Park-Miler "minimal standard" PRNG'), S(5170, 'equal to 1043618065.'), S(5170, 'Initialize the matrix with N1 "1s" per column'))),
                  I(T(5170), 'строки %d (ID 3/4), %d (Payload ID), %d (EXT_FTI)' % (S(5170, 'use the FEC Encoding ID 3 (Staircase) and 4'), S(5170, 'The Source Block Number (12-bit field)'), S(5170, 'Figure 2: EXT_FTI Header for FEC Encoding ID 3 and 4'))), I(T(5170), 'строка %d (треугольник)' % S(5170, 'void right_matrix_staircase_init (int k, int n)', 1200))],
                 'свой ГПСЧ Park–Miller: при seed = 1 10 000-е значение = 1043618065 — критерий RFC 5170 (assert)', SL))
zap.append(Z('Simple LDPC-Staircase для FECFRAME (RFC 6816, FECFRAME FEC Encoding ID 7)', 'Прикладные FEC IETF (стирания пакетов)', 'LDPC',
             {'код': 'LDPC-Staircase RFC 5170 (H и ГПСЧ те же)', 'отличие': 'параметры и идентификаторы для FECFRAME RFC 6363'},
             'FECFRAME', [I(T(6816), 'весь текст'), I(T(5170), 'определение кода (разд. 5–6)')],
             'ссылкой на RFC 5170 (ГПСЧ проверен выше)', SL))
for m, nid in ((1, 9), (8, 10)):
    zap.append(Z('RLC — случайный линейный код со скользящим окном над GF(%s) (RFC 8681, FECFRAME FEC Encoding ID %d)' % ('2' if m == 1 else '2^8', nid),
                 'Прикладные FEC IETF (стирания пакетов)', 'случайный линейный (скользящее окно)',
                 {'кодер': 'ремонтный символ = Σ c_i·S_i по символам окна кодирования (до ew_size); c_i — generate_coding_coefficients(repair_key, cc_nb, DT, m) (рис. 3)',
                  'ГПСЧ': 'TinyMT32 (RFC 8682: mat1 = 0x8f7011ee, mat2 = 0xfc78ff1f, tmat = 0x3793fdff), seed = repair_key; rand16 = выход & 0xF, rand256 = выход & 0xFF',
                  'плотность': 'DT 0…15: вероятность ненулевого коэффициента (DT+1)/16; DT = 15 — все ненулевые',
                  'поле': 'GF(2) — XOR' if m == 1 else 'GF(2^8) по x^8+x^4+x^3+x^2+1 (0x11D) — как RFC 5510',
                  'FEC Payload ID ремонтного пакета': 'repair_key 16, DT 4, NSS 12, ESI первого символа окна 32', 'таблица': 'tablicy/dop/ietf_prng.json'},
                 'FECFRAME для потоков реального времени (видео/аудио, малая задержка)',
                 [I(T(8681), 'строки %d (рис. 3), %d (поле), %d–%d (прил. A, векторы rand256/rand16)' % (S(8681, 'int generate_coding_coefficients (uint16_t  repair_key,'), S(8681, 'x^(8) + x^(4) + x^(3) + x^(2) + 1'), S(8681, 'Appendix A.  TinyMT32 Validation Criteria (Normative)', 1000), S(8681, 'Figure 10: First 50'))),
                  I(T(8682), 'строки %d–%d (рис. 1, TinyMT32), %d (рис. 2, 50 значений)' % (S(8682, 'const uint32_t  TINYMT32_MAT1_PARAM'), S(8682, 'Figure 1: TinyMT32 Reference Implementation'), S(8682, '2545341989')))],
                 'своя TinyMT32 по рис. 1 RFC 8682: первые 50 значений при seed=1 = рис. 2 RFC 8682; rand256 и rand16 = рис. 9 и 10 RFC 8681 (assert)', SL))
zap.append(Z('XOR-чётность RTP: ULPFEC (RFC 5109; заменяет RFC 2733)', 'Прикладные FEC IETF (стирания пакетов)', 'CRC-подобный (XOR-чётность пакетов)',
             {'код': 'FEC-пакет = XOR выбранных медиапакетов (по маске mask/offset mask), включая «восстановительные» поля заголовка RTP (P, X, CC, M, PT, TS, длина)',
              'уровни': 'неравная защита (ULP): уровень k защищает первые L_k байт полезной нагрузки, своя маска', 'вид': 'одиночная чётность — восстанавливает 1 потерянный пакет из группы'},
             'RTP (видеоконференции, SIP/WebRTC: формат ulpfec)',
             [I(T(5109), 'строки %d (разд. 4, Parity Codes), %d (offset mask)' % (S(5109, '4.  Parity Codes', 300), S(5109, 'each FEC packet contains an offset mask'))), I(T(2733), 'исходная версия (устарела)')],
             'определение прочитано из текста RFC (XOR — тождественно)', 'ЧАСТИЧНО: XOR-чётность тривиальна; разбор RTP есть в анализаторе пакетов, FEC-пакеты не восстанавливаются'))
zap.append(Z('Чередующаяся (1-D) чётность RTP (RFC 6015) и 2-D строки/столбцы FlexFEC (RFC 8627) — модель SMPTE 2022-1', 'Прикладные FEC IETF (стирания пакетов)', 'CRC-подобный (XOR-чётность пакетов)',
             {'1-D': 'блок L столбцов × D строк исходных пакетов; ремонтный пакет = XOR столбца (пакеты i, i+L, …, i+(D−1)L) — против пачек потерь до L',
              '2-D (FlexFEC)': 'XOR по строкам и по столбцам, итеративное восстановление; маски или (L, D) в заголовке FEC-пакета',
              'SMPTE 2022-1': 'та же схема строк/столбцов для MPEG-TS по IP (Pro-MPEG CoP3) — сам стандарт платный'},
             'IPTV/контрибуция видео (SMPTE 2022-1/-5), WebRTC (flexfec-03)',
             [I(T(6015), 'строки %d (рис. 3, столбцы), %d (L, D)' % (S(6015, 'Figure 3: Generating interleaved (column) FEC packets'), S(6015, '[SMPTE2022-1] introduces separate fields to convey the number of rows'))),
              I(T(8627), 'строки %d (1-D строки), %d (2-D)' % (S(8627, '1.1.1.  One-Dimensional (1-D) Non-interleaved (Row) FEC', 200), S(8627, '1.1.4.  Two-Dimensional (2-D)')))],
             'определение прочитано из текста RFC', 'ЧАСТИЧНО: перемежение L×D (peremezhenie.py) и XOR есть; восстановление RTP-пакетов — нет'))
zap.append(Z('Реестр IANA FEC Encoding ID (RMT и FECFRAME)', 'Прикладные FEC IETF (стирания пакетов)', 'справочно: реестр идентификаторов кодов',
             {'таблица': 'tablicy/dop/iana_fec_encoding_id.json',
              'RMT 0–127': '; '.join('%s — %s' % (x['значение'], x['описание']) for x in reg['ietf:rmt:fec:encoding - Fully-Specified FEC schemes (0-127)']),
              'RMT 128–255': '; '.join('%s — %s' % (x['значение'], x['описание']) for x in reg['ietf:rmt:fec:encoding - Under-Specified FEC schemes (128-255)'])},
             'опознание схемы FEC по полю FEC Encoding ID в LCT/ALC/FLUTE (EXT_FTI), FECFRAME (SDP fec-repair-flow)',
             [I('istochniki/dop/iana/rmt-fec-parameters.xml', 'весь файл (реестры rmt-fec-parameters-1…5, fecframe-fec-encoding-ids)'), I(T(5445), 'FEC Building Block: правила ID')],
             'XML реестра разобран; ID 2 = Reed-Solomon RFC 5510 (assert), ID 3/4 — RFC 5170, ID 1/6 — Raptor/RaptorQ (область efir)', 'НЕТ (справочно)'))
sohranit('dop_ietf_fec', zap)
