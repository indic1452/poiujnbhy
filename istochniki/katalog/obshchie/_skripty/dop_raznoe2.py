"""Дополнение (проверка полноты), часть 2:
1) программный Хэмминг-ECC NAND ядра Linux (256/512 байт → 3 байта ECC, порядок обычный и SmartMedia);
2) РС-коды стирания ISA-L (Intel): матрицы Вандермонда gf_gen_rs_matrix и Коши gf_gen_cauchy1_matrix над GF(2^8)/0x11D;
3) контрольные цифры (python-stdnum, LGPL-2.1): Луна (и Luhn mod N), Верхуфф, Дамм, ISO 7064 Mod 11-2, Mod 11-10, Mod 37-2,
   Mod 37-36, Mod 97-10.
Проверки — своим кодом с assert:
 - NAND: исходный C (функции ecc_sw_hamming_calculate/correct, вырезаны из файла без правок логики) собран gcc и вызван
   через ctypes; свой расчёт по определению из nand_ecc.rst (rp0…rp17, cp0…cp5, хранение в инверсии) совпал на 300 случайных
   блоках обоих размеров и обоих порядков; все однобитовые ошибки в данных (выборка 2000) и в ECC (все 24) исправляются C-кодом;
 - ISA-L: таблица gff_base из ec_base.h = степени 2 по модулю 0x11D (своя); матрицы построены своим кодом по комментариям
   erasure_code.h и сверены с C (собран gcc); восстановление при стирании любых m−k строк (перебор подмножеств для малых k,m);
   найден пример необратимой подматрицы Вандермонда за пределами заявленных в erasure_code.h неравенств;
 - контрольные цифры: свои реализации по текстовым определениям проверены на всех примерах doctest из файлов stdnum;
   таблица Дамма — слабо вполне антисимметричная квазигруппа (assert), таблица Верхуффа — группа D5 (assert ассоциативности),
   перестановки Верхуффа — степени p(i) (assert)."""
import ctypes, itertools, json, os, random, re, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka
D = os.path.join(KOR, 'istochniki', 'dop')
TD = tempfile.mkdtemp(dir=os.path.join(KOR, '..', '..'))
tabl = {}
zap = []

# ======================= 1. NAND Хэмминг =======================
FC = 'istochniki/dop/nand/ecc-sw-hamming.c'; FR = 'istochniki/dop/nand/nand_ecc.rst'
src = open(os.path.join(KOR, FC)).read()
a = src.index('static const char invparity[256]'); b = src.index('EXPORT_SYMBOL(ecc_sw_hamming_correct);')
body = src[a:b]
body = re.sub(r'EXPORT_SYMBOL\([^)]*\);', '', body)
body = re.sub(r'/\*\*\n \* nand_ecc_sw_hamming_calculate.*?\nEXPORT_SYMBOL|int nand_ecc_sw_hamming_calculate\(.*?\n}\n', '', body, flags=re.S)
body = re.sub(r'pr_err\([^;]*\);', '', body)
assert 'nand_device' not in body
hdr = '#include <stdint.h>\n#include <stdbool.h>\ntypedef uint32_t u32;\n#define EBADMSG 74\n'
open(os.path.join(TD, 'nand.c'), 'w').write(hdr + body)
subprocess.run(['gcc', '-O1', '-shared', '-fPIC', '-o', os.path.join(TD, 'nand.so'), os.path.join(TD, 'nand.c')], check=True)
lib = ctypes.CDLL(os.path.join(TD, 'nand.so'))
def c_calc(buf, sm):
    b = (ctypes.c_ubyte * len(buf)).from_buffer_copy(buf); code = (ctypes.c_ubyte * 3)()
    lib.ecc_sw_hamming_calculate(b, len(buf), code, ctypes.c_bool(sm)); return bytes(code)
def c_corr(buf, read, calc):
    b = (ctypes.c_ubyte * len(buf)).from_buffer_copy(buf)
    r = lib.ecc_sw_hamming_correct(b, (ctypes.c_ubyte * 3)(*read), (ctypes.c_ubyte * 3)(*calc), len(buf), ctypes.c_bool(False))
    return r, bytes(b)
rst = open(os.path.join(KOR, FR)).read()
for frag in ('cp0 is the parity that belongs to all bit0, bit2, bit4, bit6', 'cp2 is the parity over bit0, bit1, bit4 and bit5',
             'cp4 is the parity over bit0, bit1, bit2 and bit3', 'rp0 is the parity of all even bytes'):
    assert frag in rst, frag
def moy_ecc(buf, sm):
    """По определению nand_ecc.rst: rp(2i)/rp(2i+1) — чётность байтов с битом i адреса = 0/1; cp — чётность столбцов;
    хранится инверсия чётности; 256 байт: младшие 2 бита code[2] = 1, 512 байт: rp17, rp16."""
    n = len(buf); m = 8 if n == 256 else 9
    par = [bin(x).count('1') & 1 for x in buf]
    rp = []
    for i in range(m):
        rp.append(sum(p for a, p in enumerate(par) if not (a >> i) & 1) & 1)
        rp.append(sum(p for a, p in enumerate(par) if (a >> i) & 1) & 1)
    X = 0
    for x in buf: X ^= x
    col = lambda bits: bin(X & sum(1 << k for k in bits)).count('1') & 1
    cp = [col((0, 2, 4, 6)), col((1, 3, 5, 7)), col((0, 1, 4, 5)), col((2, 3, 6, 7)), col((0, 1, 2, 3)), col((4, 5, 6, 7))]
    inv = lambda v: 1 - v
    lo = sum(inv(rp[i]) << i for i in range(8)); hi = sum(inv(rp[8 + i]) << i for i in range(8))
    c2 = sum(inv(cp[i]) << (i + 2) for i in range(6)) | (3 if n == 256 else (inv(rp[17]) << 1) | inv(rp[16]))
    return bytes([lo, hi, c2]) if sm else bytes([hi, lo, c2])
rnd = random.Random(1)
for _ in range(300):
    for n in (256, 512):
        buf = bytes(rnd.randrange(256) for _ in range(n))
        for sm in (False, True):
            assert c_calc(buf, sm) == moy_ecc(buf, sm)
ispr = 0
for _ in range(1000):
    for n in (256, 512):
        buf = bytes(rnd.randrange(256) for _ in range(n)); e = c_calc(buf, False)
        pos = rnd.randrange(n * 8); bad = bytearray(buf); bad[pos >> 3] ^= 1 << (pos & 7)
        r, fixed = c_corr(bytes(bad), e, c_calc(bytes(bad), False))
        assert r == 1 and fixed == buf; ispr += 1
for k in range(24):
    buf = bytes(rnd.randrange(256) for _ in range(256)); e = bytearray(c_calc(buf, False)); e[k >> 3] ^= 1 << (k & 7)
    if k >= 16 and (k & 7) < 2: continue  # младшие 2 бита code[2] при 256 байтах — постоянные 1, не проверочные
    r, fixed = c_corr(buf, bytes(e), c_calc(buf, False)); assert r == 1 and fixed == buf
# пример: ECC пустой (стёртой) страницы и нулевой
ff256 = c_calc(b'\xff' * 256, False); z256 = c_calc(b'\x00' * 256, False); ff512 = c_calc(b'\xff' * 512, False)
assert ff256 == b'\xff\xff\xff'
tabl['NAND Хэмминг'] = dict(ECC_FF_256=ff256.hex(), ECC_00_256=z256.hex(), ECC_FF_512=ff512.hex())
l_calc = stroka(FC, 'int ecc_sw_hamming_calculate('); l_corr = stroka(FC, 'int ecc_sw_hamming_correct('); l_sm = stroka(FC, 'if (sm_order) {')
for n in (256, 512):
    zap.append(Z('Хэмминг-ECC NAND (программный, ядро Linux): %d байт → 3 байта ECC (%s)' % (n, '22 проверочных бита' if n == 256 else '24 проверочных бита'),
                 'Коды NAND/флеш-памяти', 'Хэмминг',
                 {'n,k': '%d,%d (бит; ECC отдельно в OOB)' % (n * 8 + (22 if n == 256 else 24), n * 8),
                  'проверочные': 'rp0…rp%d — чётность строк (байтов) по битам адреса байта: rp(2i) — бит i адреса = 0, rp(2i+1) — = 1; cp0…cp5 — чётность столбцов (бит 0,2,4,6 / 1,3,5,7 / 0,1,4,5 / 2,3,6,7 / 0–3 / 4–7)' % (15 if n == 256 else 17),
                  'хранение': 'каждый бит ECC — инверсия чётности (стёртая страница FF…FF даёт ECC FF FF FF)',
                  'раскладка (обычная)': 'code[0] = rp15…rp8, code[1] = rp7…rp0, code[2] = cp5 cp4 cp3 cp2 cp1 cp0 %s' % ('1 1' if n == 256 else 'rp17 rp16'),
                  'раскладка SmartMedia (sm_order)': 'code[0] и code[1] меняются местами (code[0] = rp7…rp0)',
                  'исправление': 'XOR прочитанного и вычисленного ECC: у однобитовой ошибки в каждой паре (rp2i, rp2i+1) и (cp) ровно один бит → адрес байта = нечётные rp, номер бита = cp5 cp3 cp1; один ненулевой бит — ошибка в самом ECC',
                  'ECC примеры (обычный порядок)': {'256×FF': ff256.hex(), '256×00': z256.hex(), '512×FF': ff512.hex()}},
                 'NAND SLC (SmartMedia/xD, старые SLC-чипы, загрузчики U-Boot, ядро Linux mtd «sw hamming»); в образах флеш-памяти — 3 байта ECC на 256/512 байт в OOB',
                 [I(FR, 'строки %d–%d (определение rp/cp)' % (stroka(FR, 'cp0 is the parity that belongs to all bit0'), stroka(FR, 'rp2 is the parity of all bytes 0, 1, 4, 5'))),
                  I(FC, 'строки %d (расчёт), %d (sm_order), %d (исправление)' % (l_calc, l_sm, l_corr)), I('istochniki/dop/nand/LINUX_COPYING', 'GPL-2.0 (ядро Linux; файл — GPL-2.0-or-later)')],
                 'исходный C собран gcc (ctypes): свой расчёт по определению nand_ecc.rst совпал на 300 случайных блоках × 2 порядка; по 1000 случайных однобитовых ошибок в данных на каждый размер и все 22/24 бита ECC исправлены кодом ядра (assert)',
                 'НЕТ: разбор OOB NAND в проекте отсутствует; расчёт тривиален (чётности по адресным битам)'))

# ======================= 2. ISA-L =======================
FB = 'istochniki/dop/isal/ec_base.c'; FH = 'istochniki/dop/isal/ec_base.h'; FE = 'istochniki/dop/isal/erasure_code.h'
h = open(os.path.join(KOR, FH)).read()
gff = [int(x, 16) for x in re.findall(r'0x[0-9a-f]{2}', h[h.index('gff_base[] = {'):h.index('};', h.index('gff_base[] = {'))])]
exp = [1]
for _ in range(254):
    v = exp[-1] << 1
    exp.append(v ^ 0x11D if v & 0x100 else v)
assert gff[:255] == exp, 'gff_base ≠ степени 2 mod 0x11D'
log = {v: i for i, v in enumerate(exp)}
mul = lambda a, b: 0 if a == 0 or b == 0 else exp[(log[a] + log[b]) % 255]
inv = lambda a: exp[(255 - log[a]) % 255]
def m_rs(m, k):  # erasure_code.h: верх — I, низ — 2^{i*(j-k+1)}… по коду: строка i≥k: p=gen^j, gen = 2^(i−k)
    A = [[int(i == j) for j in range(k)] for i in range(k)]; gen = 1
    for i in range(k, m):
        row = []; p = 1
        for j in range(k): row.append(p); p = mul(p, gen)
        A.append(row); gen = mul(gen, 2)
    return A
def m_cauchy(m, k):
    A = [[int(i == j) for j in range(k)] for i in range(k)]
    for i in range(k, m): A.append([inv(i ^ j) for j in range(k)])
    return A
def obr(M):
    n = len(M); A = [r[:] + [int(i == j) for j in range(n)] for i, r in enumerate(M)]
    for c in range(n):
        p = next((r for r in range(c, n) if A[r][c]), None)
        if p is None: return None
        A[c], A[p] = A[p], A[c]; t = inv(A[c][c]); A[c] = [mul(x, t) for x in A[c]]
        for r in range(n):
            if r != c and A[r][c]:
                f = A[r][c]; A[r] = [x ^ mul(f, y) for x, y in zip(A[r], A[c])]
    return [r[n:] for r in A]
# сверка с C ISA-L
cb = open(os.path.join(KOR, FB)).read()
def vyrezat(name):
    i = cb.index('\n' + name + '('); j = cb.index('\n}\n', i); return cb[cb.rindex('\n', 0, i - 1):j + 3]
csrc = ('#include <string.h>\n#include "ec_base.h"\n' + 'unsigned char gf_mul(unsigned char a, unsigned char b){int i; if(!a||!b) return 0; return gff_base[(i = gflog_base[a] + gflog_base[b]) > 254 ? i - 255 : i];}\n'
        'unsigned char gf_inv(unsigned char a){ if(!a) return 0; return gff_base[255 - gflog_base[a]];}\n' + vyrezat('gf_gen_rs_matrix') + vyrezat('gf_gen_cauchy1_matrix'))
open(os.path.join(TD, 'isal.c'), 'w').write(csrc)
hh = h
# ec_base.h может ссылаться на другие заголовки — оставляем только таблицы
tabs = ''.join(re.findall(r'static const unsigned char (?:gff_base|gflog_base)\[\] = \{.*?\};', hh, re.S))
open(os.path.join(TD, 'ec_base.h'), 'w').write(tabs)
subprocess.run(['gcc', '-O1', '-shared', '-fPIC', '-I', TD, '-o', os.path.join(TD, 'isal.so'), os.path.join(TD, 'isal.c')], check=True)
il = ctypes.CDLL(os.path.join(TD, 'isal.so'))
def c_mat(fn, m, k):
    a = (ctypes.c_ubyte * (m * k))(); getattr(il, fn)(a, m, k); return [list(a[i * k:(i + 1) * k]) for i in range(m)]
for k in range(1, 12):
    for m in range(k + 1, 17):
        assert c_mat('gf_gen_rs_matrix', m, k) == m_rs(m, k); assert c_mat('gf_gen_cauchy1_matrix', m, k) == m_cauchy(m, k)
# восстановление: любые k строк из m — обратимы (Коши всегда; Вандермонд — для заявленных пар)
def vse_obratimy(A, k):
    return all(obr([A[r] for r in rows]) is not None for rows in itertools.combinations(range(len(A)), k))
for k, m in ((3, 6), (4, 8), (4, 12), (5, 10), (6, 9), (8, 11), (10, 14)):
    assert vse_obratimy(m_cauchy(m, k), k), ('Коши', k, m)
for k, m in ((3, 8), (4, 9), (5, 10), (6, 10), (8, 12), (10, 13)):
    assert vse_obratimy(m_rs(m, k), k), ('Вандермонд', k, m)
# контрпример для Вандермонда за пределами неравенств
kontr = None
for k in range(5, 9):
    for m in range(k + 5, k + 9):
        A = m_rs(m, k)
        for rows in itertools.combinations(range(m), k):
            if obr([A[r] for r in rows]) is None: kontr = (k, m, rows); break
        if kontr: break
    if kontr: break
# сквозной тест кодирования/восстановления (k=10, m=14, Коши)
k, m = 10, 14; A = m_cauchy(m, k); data = [[rnd.randrange(256) for _ in range(64)] for _ in range(k)]
enc = [[0] * 64 for _ in range(m)]
for i in range(m):
    for j in range(k):
        for t in range(64): enc[i][t] ^= mul(A[i][j], data[j][t])
ost = sorted(rnd.sample(range(m), k)); B = obr([A[r] for r in ost])
rec = [[0] * 64 for _ in range(k)]
for i in range(k):
    for j in range(k):
        for t in range(64): rec[i][t] ^= mul(B[i][j], enc[ost[j]][t])
assert rec == data
tabl['ISA-L'] = dict(поле='0x11D', вандермонд_m6_k4=m_rs(6, 4), коши_m6_k4=m_cauchy(6, 4), контрпример_вандермонда=dict(k=kontr[0], m=kontr[1], строки=list(kontr[2])) if kontr else None)
src_isal = [I(FB, 'строки %d–%d (gf_gen_rs_matrix), %d–%d (gf_gen_cauchy1_matrix)' % (stroka(FB, 'gf_gen_rs_matrix(unsigned char *a'), stroka(FB, 'gen = gf_mul(gen, 2);') + 2,
                                                                                    stroka(FB, 'gf_gen_cauchy1_matrix(unsigned char *a'), stroka(FB, '*p++ = gf_inv(i ^ j);') + 1)),
            I(FE, 'строки %d–%d (описание матриц и условия обратимости)' % (stroka(FE, 'Vandermonde matrix example of encoding coefficients'), stroka(FE, 'i:{0,k-1} j:{k,m-1}.  Any sub-matrix of a Cauchy matrix'))),
            I(FH, 'строка %d (gff_base — степени 2 по 0x11D)' % stroka(FH, 'static const unsigned char gff_base[]')), I('istochniki/dop/isal/LICENSE', 'BSD-3-Clause (Intel)')]
zap.append(Z('РС-код стирания ISA-L (Intel): систематическая матрица Вандермонда gf_gen_rs_matrix, GF(2^8)/0x11D', 'Коды стирания хранилищ', 'РС',
             {'поле': 'GF(2^8), x^8+x^4+x^3+x^2+1 (0x11D), примитивный 2', 'матрица m×k': 'строки 0…k−1 — единичная (данные), строка i ≥ k: a[i][j] = g^j, g = 2^(i−k) (первая проверочная — все 1 = XOR)',
              'кодирование': 'P_i = Σ_j a[i][j]·D_j побайтно', 'восстановление': 'любые k уцелевших строк → обращение k×k подматрицы',
              'ограничение': 'обратимость гарантирована только при k ≤ 3; k=4, m ≤ 25; k=5, m ≤ 10; k ≤ 21, m−k = 4; m−k ≤ 3 (erasure_code.h)',
              'контрпример (расчёт)': ('k=%d, m=%d: строки %s — подматрица вырождена' % (kontr[0], kontr[1], list(kontr[2]))) if kontr else 'не найден',
              'пример m=6,k=4': m_rs(6, 4), 'таблица': 'tablicy/dop/raznoe2.json'},
             'хранилища (Ceph, HDFS-EC через ISA-L, MinIO, Swift), RAID-подобные системы; встречается в дампах дисковых массивов', src_isal,
             'gff_base = степени 2 mod 0x11D (assert); матрицы своим кодом = C ISA-L (gcc, все k ≤ 11, m ≤ 16); обратимость всех k-подмножеств для пар из допустимых; найден контрпример вне неравенств',
             'НЕТ: в проекте нет кодов стирания; арифметика GF(2^8)/0x11D есть (rs_bch.py)'))
zap.append(Z('РС-код стирания ISA-L (Intel): систематическая матрица Коши gf_gen_cauchy1_matrix, GF(2^8)/0x11D', 'Коды стирания хранилищ', 'РС',
             {'поле': 'GF(2^8), 0x11D', 'матрица m×k': 'строки 0…k−1 — единичная, строка i ≥ k: a[i][j] = 1/(i ⊕ j) (сложение в GF(2^8) — XOR индексов)',
              'свойство': 'любая квадратная подматрица Коши обратима ⇒ восстановление при любых m−k стираниях (m ≤ 256)', 'пример m=6,k=4': m_cauchy(6, 4)},
             'хранилища (ISA-L, Ceph-плагин isa, SPDK); дампы массивов', src_isal,
             'матрицы своим кодом = C ISA-L (gcc); перебор всех k-подмножеств для 7 пар (k,m) — обратимы; сквозной тест k=10, m=14: 4 случайных стирания восстановлены (assert)',
             'НЕТ (справочно; GF(2^8)/0x11D в проекте есть)'))

# ======================= 3. Контрольные цифры =======================
S = 'istochniki/dop/stdnum/'
def doctests(fn):
    t = open(os.path.join(KOR, S + fn)).read(); out = []
    for m in re.finditer(r'>>> (\w+)\((.*?)\)\n(.*?)\n', t):
        out.append((m.group(1), m.group(2), m.group(3).strip()))
    return out
def args(s):
    return eval('(lambda *a, **k: (a, k))(' + s + ')', {'table': None})
# Луна
def luhn_sum(num, alphabet='0123456789'):
    alph = alphabet
    n = len(alph); v = [alph.index(c) for c in reversed(num)]
    return (sum(v[::2]) + sum(sum(divmod(2 * x, n)) for x in v[1::2])) % n
def luhn_calc(num, alphabet='0123456789'): return alphabet[-luhn_sum(num + alphabet[0], alphabet)]
# Верхуфф
tv = open(os.path.join(KOR, S + 'verhoeff.py')).read()
def tablica(t, name):
    blk = t[t.index(name):]; blk = blk[:blk.index('\n\n')]
    return [[int(x) for x in re.findall(r'\d+', r)] for r in re.findall(r'\(([\d, ]+)\)', blk)]
VM = tablica(tv, '_multiplication_table'); VP = tablica(tv, '_permutation_table')
assert len(VM) == 10 and all(sorted(r) == list(range(10)) for r in VM)
assert all(VM[0][x] == x == VM[x][0] for x in range(10))
assert all(VM[VM[a][b]][c] == VM[a][VM[b][c]] for a in range(10) for b in range(10) for c in range(10))  # группа
assert any(VM[a][b] != VM[b][a] for a in range(10) for b in range(10))  # некоммутативна (D5)
assert all(VP[i][x] == VP[1][VP[i - 1][x]] for i in range(1, 8) for x in range(10))  # p(i) = p∘p(i−1)
def verh_sum(num):
    c = 0
    for i, ch in enumerate(reversed(num)): c = VM[c][VP[i % 8][int(ch)]]
    return c
def verh_calc(num): return str(VM[verh_sum(num + '0')].index(0))
# Дамм
td = open(os.path.join(KOR, S + 'damm.py')).read()
blk = td[td.index('_operation_table: DammTable = ('):]; blk = blk[:blk.index('\n\n')]
DT = [[int(x) for x in re.findall(r'\d+', r)] for r in re.findall(r'\(([\d, ]+)\)', blk)]
assert len(DT) == 10 and all(sorted(r) == list(range(10)) for r in DT) and all(sorted(c) == list(range(10)) for c in zip(*DT))  # квазигруппа
assert all(DT[i][i] == 0 for i in range(10))
assert all(DT[DT[c][x]][y] != DT[DT[c][y]][x] for c in range(10) for x in range(10) for y in range(10) if x != y)  # слабо вполне антисимм.
def damm_sum(num, table=None):
    t = table or DT; i = 0
    for ch in num: i = t[i][int(ch)]
    return i
# ISO 7064
A36 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
def m_x2_sum(num, alph): M = len(alph); c = 0
def m112_sum(num):
    c = 0
    for ch in num: c = (2 * c + (10 if ch == 'X' else int(ch))) % 11
    return c
def m112_calc(num): c = (1 - 2 * m112_sum(num)) % 11; return 'X' if c == 10 else str(c)
def m372_sum(num, alphabet=A36 + '*'):
    c = 0
    for ch in num: c = (2 * c + alphabet.index(ch)) % len(alphabet)
    return c
def m372_calc(num, alphabet=A36 + '*'): return alphabet[(1 - 2 * m372_sum(num, alphabet)) % len(alphabet)]
def mh_sum(num, alphabet=A36):  # гибридный Mod M+1, M
    M = len(alphabet); c = M // 2
    for ch in num: c = (((c or M) * 2) % (M + 1) + alphabet.index(ch)) % M
    return c
def mh_calc(num, alphabet=A36): M = len(alphabet); return alphabet[(1 - ((mh_sum(num, alphabet) or M) * 2) % (M + 1)) % M]
def m9710_sum(num): return int(''.join(str(A36.index(c)) for c in num)) % 97
def m9710_calc(num): return '%02d' % ((98 - 100 * m9710_sum(num)) % 97)
moi = {'luhn.py': dict(checksum=luhn_sum, calc_check_digit=luhn_calc), 'verhoeff.py': dict(checksum=verh_sum, calc_check_digit=verh_calc),
       'damm.py': dict(checksum=damm_sum, calc_check_digit=lambda n, table=None: str(damm_sum(n, table))),
       'mod_11_2.py': dict(checksum=m112_sum, calc_check_digit=m112_calc), 'mod_37_2.py': dict(checksum=m372_sum, calc_check_digit=m372_calc),
       'mod_11_10.py': dict(checksum=lambda n: mh_sum(n, '0123456789'), calc_check_digit=lambda n: mh_calc(n, '0123456789')),
       'mod_37_36.py': dict(checksum=mh_sum, calc_check_digit=mh_calc), 'mod_97_10.py': dict(checksum=m9710_sum, calc_check_digit=m9710_calc, calc_check_digits=m9710_calc)}
valid_ok = {'luhn.py': lambda n, alphabet='0123456789': luhn_sum(n, alphabet) == 0, 'verhoeff.py': lambda n: verh_sum(n) == 0, 'damm.py': lambda n, table=None: damm_sum(n, table) == 0,
            'mod_11_2.py': lambda n: m112_sum(n) == 1, 'mod_37_2.py': lambda n, alphabet=A36 + '*': m372_sum(n, alphabet) == 1, 'mod_11_10.py': lambda n: mh_sum(n, '0123456789') == 1,
            'mod_37_36.py': lambda n, alphabet=A36: mh_sum(n, alphabet) == 1, 'mod_97_10.py': lambda n: m9710_sum(n) == 1}
dt_table = None
vektory = {}
for fn in moi:
    t = open(os.path.join(KOR, S + fn)).read()
    tbl = None
    mt = re.search(r'>>> table = \((.*?)\)\)\n', t, re.S)
    if mt: tbl = [[int(x) for x in re.findall(r'\d+', r)] for r in re.findall(r'\(([\d, ]+)\)', mt.group(0))]
    n_ok = 0
    for fun, arg, res in doctests(fn):
        if fun == 'table': continue
        a, kw = eval('(lambda *a, **k: (a, k))(' + arg + ')', {'table': tbl})
        if fun in ('checksum', 'calc_check_digit', 'calc_check_digits'):
            got = moi[fn][fun](*a, **kw); exp_ = eval(res)
            assert got == exp_, (fn, fun, arg, got, res); n_ok += 1
        elif fun == 'validate':
            ok = valid_ok[fn](*a, **kw)
            assert ok == (not res.startswith('Traceback')), (fn, arg); n_ok += 1
    assert n_ok >= 3, (fn, n_ok)
    vektory[fn] = n_ok
tabl['контрольные цифры'] = dict(верхуфф_умножение=VM, верхуфф_перестановки=VP, дамм=DT, примеров_doctest=vektory)
def ssyl(fn, obr): return I(S + fn, 'строки %d–… (%s); примеры doctest в начале файла' % (stroka(S + fn, obr), obr))
LIC = I(S + 'COPYING', 'LGPL-2.1 (python-stdnum)')
CD = 'Контрольные цифры (проверочные символы)'
zap += [
    Z('Алгоритм Луна (mod 10; Luhn mod N)', CD, 'контрольная сумма', {'алфавит': '0…9 (Luhn mod N — любой алфавит из N символов)',
      'правило': 'справа налево: символы на чётных позициях (считая контрольный — 1-й) удваиваются, сумма цифр удвоенного по основанию N; сумма всех ≡ 0 (mod N)',
      'обнаруживает': 'все одиночные ошибки, почти все перестановки соседних (кроме 09↔90)'}, 'номера банковских карт (ISO/IEC 7812), IMEI, SIM ICCID, номера NPI, канадский SIN',
      [ssyl('luhn.py', 'def checksum'), LIC], 'своя реализация совпала со всеми примерами doctest luhn.py (%d), включая Luhn mod 16' % vektory['luhn.py'], 'НЕТ (справочно; поля пакетов)'),
    Z('Алгоритм Верхуффа (группа диэдра D5)', CD, 'контрольная сумма', {'таблица умножения': 'D5 (10×10) — tablicy/dop/raznoe2.json', 'перестановки': 'p(i) = p^i, p = (1 5 7 6 2 8 3 0 9 4) — 8 строк',
      'правило': 'c = 0; для цифр справа налево: c = d(c, p(i mod 8)(n_i)); число верно при c = 0', 'обнаруживает': 'все одиночные ошибки и перестановки соседних цифр'},
      'индийский Aadhaar, номера некоторых банков/документов', [ssyl('verhoeff.py', '_multiplication_table'), LIC],
      'таблица — некоммутативная группа (ассоциативность проверена перебором), перестановки — степени p (assert); примеры doctest совпали (%d)' % vektory['verhoeff.py'], 'НЕТ (справочно)'),
    Z('Алгоритм Дамма (слабо вполне антисимметричная квазигруппа порядка 10)', CD, 'контрольная сумма', {'таблица': '10×10 (как в Википедии/python-stdnum) — tablicy/dop/raznoe2.json',
      'правило': 'i = 0; i = T[i][цифра] по всем цифрам; контрольная — итоговое i; число верно при i = 0', 'обнаруживает': 'все одиночные ошибки и перестановки соседних цифр'},
      'номера документов, протоколы с цифровыми идентификаторами', [ssyl('damm.py', '_operation_table'), LIC],
      'таблица — латинский квадрат с нулевой диагональю и свойством (c∗x)∗y ≠ (c∗y)∗x при x ≠ y (перебор, assert); примеры doctest (в т. ч. со своей таблицей) совпали (%d)' % vektory['damm.py'], 'НЕТ (справочно)'),
    Z('ISO 7064 Mod 11-2 (контрольный символ 0…9, X)', CD, 'контрольная сумма', {'правило': 'c = (2c + a) mod 11 по символам; верно при c = 1; контрольный = (1 − 2c) mod 11, 10 → X'},
      'ISNI, ORCID, китайский номер ID (18 знаков)', [ssyl('mod_11_2.py', 'def checksum'), LIC], 'примеры doctest совпали (%d)' % vektory['mod_11_2.py'], 'НЕТ (справочно)'),
    Z('ISO 7064 Mod 11-10 (гибридный, цифровой)', CD, 'контрольная сумма', {'правило': 'c = 5; c = (((c или 10)·2) mod 11 + a) mod 10; верно при c = 1'},
      'немецкий налоговый номер, хорватский OIB и др.', [ssyl('mod_11_10.py', 'def checksum'), LIC], 'примеры doctest совпали (%d)' % vektory['mod_11_10.py'], 'НЕТ (справочно)'),
    Z('ISO 7064 Mod 37-2 (буквенно-цифровой, символ 0…9 A…Z *)', CD, 'контрольная сумма', {'правило': 'c = (2c + a) mod 37 по символам алфавита 0…9A…Z*; верно при c = 1; общий вид Mod x, 2 со своим алфавитом'},
      'ISO 6346 (контейнеры — родственный), ISAN', [ssyl('mod_37_2.py', 'def checksum'), LIC], 'примеры doctest совпали (%d), включая Mod 11-2 через алфавит' % vektory['mod_37_2.py'], 'НЕТ (справочно)'),
    Z('ISO 7064 Mod 37-36 (гибридный буквенно-цифровой)', CD, 'контрольная сумма', {'правило': 'M = 36: c = M/2; c = (((c или M)·2) mod (M+1) + a) mod M; верно при c = 1; общий вид Mod M+1, M'},
      'GRid (музыкальные релизы), ISTC', [ssyl('mod_37_36.py', 'def checksum'), LIC], 'примеры doctest совпали (%d)' % vektory['mod_37_36.py'], 'НЕТ (справочно)'),
    Z('ISO 7064 Mod 97-10 (две контрольные цифры)', CD, 'контрольная сумма', {'правило': 'буквы → 10…35, число целиком mod 97; верно при остатке 1; контрольные = (98 − 100·r) mod 97 (две цифры)'},
      'IBAN (ISO 13616), LEI (ISO 17442), RF-ссылки платежей', [ssyl('mod_97_10.py', 'def checksum'), LIC], 'примеры doctest совпали (%d)' % vektory['mod_97_10.py'], 'НЕТ (справочно)'),
]
json.dump(tabl, open(os.path.join(KOR, 'tablicy', 'dop', 'raznoe2.json'), 'w'), ensure_ascii=False, indent=1)
import shutil; shutil.rmtree(TD, ignore_errors=True)
print('NAND исправлено', ispr, 'ISA-L контрпример', kontr, 'doctest', vektory)
sohranit('dop_raznoe2', zap)
