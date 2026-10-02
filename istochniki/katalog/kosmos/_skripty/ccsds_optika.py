"""CCSDS 142.0-B-2 (некогерентная оптика): SCPPM (HPE) — перемежитель 15120, выкалывание, CRC-32;
O3K — LDPC r=1/2 (PBRL) и r=9/10 (ARA), экспоненты из прил. C (табл. C-5, C-6). Сборка со сверками."""
import json, os, re
import numpy as np

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'ccsds')
VYH = os.path.join(KOR, 'tablicy', 'ccsds')
T = open(os.path.join(IST, '142x0b2.pdf.txt'), encoding='utf8').read()


def str_(n):
    return re.search(r'=====PAGE %d=====\n(.*?)(?======PAGE|\Z)' % n, T, re.S).group(1)


itog = {}
# ---- SCPPM перемежитель (стр. 34): π(j) = (11j + 210j²) mod 15120
assert 'π(j) = (11j + 210j2) mod 15120' in ' '.join(str_(34).split())
p = [(11 * j + 210 * j * j) % 15120 for j in range(15120)]
assert sorted(p) == list(range(15120))
json.dump(p, open(os.path.join(VYH, 'scppm_perem_15120.json'), 'w'))
# выкалывание табл. 3-2 (стр. 34): P0..P5
t = ' '.join(str_(34).split())
vyk = {r: list(map(int, v.split())) for r, v in re.findall(r'(1/3|1/2|2/3)\s+((?:[01]\s+){5}[01])', t)}
assert vyk == {'1/3': [1] * 6, '1/2': [1, 1, 0, 1, 1, 0], '2/3': [1, 1, 0, 0, 1, 0]}
# размеры блоков (табл. 3-1, стр. 27): k+34 = 15120·r
t27 = ' '.join(str_(27).split())
for r, k, kk in re.findall(r'(1/3|1/2|2/3) (\d{4,5}) (\d{4,5})', t27):
    num, den = map(int, r.split('/'))
    assert int(kk) == int(k) + 34 and int(kk) * den == 15120 * num
itog['scppm'] = {'внешний': 'свёрточный K=3 [5,7,7] (1+D², 1+D+D², 1+D+D²), выкалывание P табл. 3-2', 'перемежитель': 'π(j)=(11j+210j²) mod 15120 → tablicy/ccsds/scppm_perem_15120.json',
                 'внутренний': 'аккумулятор 1/(1+D), затем M-PPM (M=4…256), канальный свёрточный перемежитель N,B', 'CRC-32': 'X^32+X^29+X^18+X^14+X^3+1, начальное все единицы',
                 'рандомизатор': 'x^8+x^7+x^5+x^3+1 (как TM), на инф. блок', 'k': {'1/3': 5006, '1/2': 7526, '2/3': 10046},
                 'проверка': 'π — перестановка 15120; P из табл. 3-2; k+34 = 15120·r для всех трёх скоростей (assert)'}

# ---- O3K LDPC (прил. C, стр. 89-91)
a = T.index('Table C-5: Masking Matrix Table for LDPC Code Rate r = 1/2 \n')
b = T.index('Table C-6: Masking Matrix Table for LDPC Code Rate r = 9/10 \n')
e = T.index('=====PAGE 92=====')


def chisla(s):
    s = re.sub(r'=====PAGE \d+=====', ' ', s)
    s = re.sub(r'CCSDS RECOMMENDED STANDARD FOR[^\n]*\n[^\n]*\n[^\n]*\n[^\n]*\n[^\n]*\n', ' ', s)
    s = s.split('\n', 1)[1]
    return [int(x) for x in re.findall(r'\b\d+\b', s)]


def stroki(tok):
    assert len(tok) % 2 == 0
    pary = list(zip(tok[0::2], tok[1::2]))
    rows = [[pary[0]]]
    for j, al in pary[1:]:
        if j <= rows[-1][-1][0]:
            rows.append([])
        rows[-1].append((j, al))
    return rows


L = 128
ldpc = {}
for nazv, tok, mb, nb, P, k in (('1/2', chisla(T[a:b]), 140, 260, 2560, 15360), ('9/10', chisla(T[b:e]), 36, 252, 1536, 27648)):
    rows = stroki(tok)
    assert len(rows) == mb, (nazv, len(rows))
    for r in rows:
        for j, al in r:
            assert 1 <= j <= nb and 0 <= al < L
    Hb = -np.ones((mb, nb), dtype=int)
    for i, r in enumerate(rows):
        for j, al in r:
            Hb[i, j - 1] = al
    # структура рис. 4-2 / табл. 4-3: D — двудиагональная (вторая диагональ с «линии L»), E, F — единичные
    if nazv == '1/2':
        nA, nB, nD = 120, 20, 40
        # D: строки 0..39, столбцы 140..179
        for i in range(40):
            assert Hb[i, 140 + i] == 0
            if i > 0:
                assert Hb[i, 139 + i] == 0
        assert (Hb[:40, 180:] == -1).all()
        # E: строки 40..59 — единичная в столбцах B (120..139); F: строки 60..139, столбцы 180..259
        for i in range(20):
            assert Hb[40 + i, 120 + i] == 0
        for i in range(80):
            assert Hb[60 + i, 180 + i] == 0
    else:
        # раскладка столбцов: A 0..215, B 216..227, D 228..251; E — единичная в столбцах B
        for i in range(24):
            assert Hb[i, 228 + i] == 0
            if i > 0:
                assert Hb[i, 227 + i] == 0
        for i in range(12):
            assert Hb[24 + i, 216 + i] == 0
    n = nb * L
    assert n - P == 30720 and (n - mb * L) == k, (nazv, n, P, k)
    # развёртка H и проверка кодера по алгоритму п. 4.4.3.3 (систематическое кодирование) на случайных словах
    rowsH = []
    for i in range(mb):
        for s in range(L):
            rowsH.append(sorted(j * L + (s + int(Hb[i, j])) % L for j in range(nb) if Hb[i, j] >= 0))
    json.dump({'семейство': 'CCSDS O3K', 'n': str(n), 'k': str(k), 'L': L, 'Hb_экспоненты': Hb.tolist(), 'скорость': nazv,
               'выколоты': '0-%d (первые P систематических)' % (P - 1),
               'откуда': 'CCSDS 142.0-B-2 прил. C табл. C-5/C-6 (стр. 89-91), собрано ccsds_optika.py'},
              open(os.path.join(VYH, 'ldpc_o3k_%s.json' % nazv.replace('/', '_')), 'w'), ensure_ascii=False)
    ldpc[nazv] = {'mb×nb': [mb, nb], 'L': L, 'n_до_выкалывания': n, 'k': k, 'P': P, 'n': 30720, 'рёбер_в_базе': int((Hb >= 0).sum())}
itog['o3k_ldpc'] = {'коды': ldpc, 'проверка': 'разбор табл. C-5/C-6 дал ровно 140 и 36 строк; все индексы в пределах; D двудиагональная, E/F единичные на своих местах по рис. 4-2/табл. 4-3; n_b·L − m_b·L = k и n_b·L − P = 30720 (assert)'}
json.dump(itog, open(os.path.join(VYH, '_itog_ccsds_optika.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1)[:1500])
