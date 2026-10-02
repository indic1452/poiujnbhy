"""Gallager LDPC (MIT Press 1963 / докторская 1960): регулярный ансамбль (n, j, k) и пример рис. 2.1 (n=20, j=3, k=4)
→ tablicy/ldpc/gallager_fig21.alist, zapisi/gallager.json. Матрица переписана с изображения (risunki/gallager_fig21.png).
Проверка (assert): построение Галлагера — первая полоса из n/k строк с k подряд идущими единицами, остальные j−1 полос —
перестановки столбцов первой; веса строк k, столбцов j; каждая полоса покрывает каждый столбец ровно один раз; ранг GF(2)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldpc_alist import rank
from zap import KOR, I, Z, sohranit
H = ['11110000000000000000', '00001111000000000000', '00000000111100000000', '00000000000011110000', '00000000000000001111',
     '10001000100010000000', '01000100010000001000', '00100010000001000100', '00010000001000100010', '00000001000100010001',
     '10000100000100000100', '01000010001000010000', '00100001000010000010', '00010000100001001000', '00001000010000100001']
n, j, k = 20, 3, 4
assert all(len(r) == n for r in H) and len(H) == n * j // k
assert all(r.count('1') == k for r in H)
assert all(sum(int(r[c]) for r in H) == j for c in range(n))
for b in range(j):
    band = H[b * n // k:(b + 1) * n // k]
    assert all(sum(int(r[c]) for r in band) == 1 for c in range(n)), b
for i in range(n // k): assert H[i] == '0' * (i * k) + '1' * k + '0' * (n - (i + 1) * k)
rows = [[c + 1 for c in range(n) if r[c] == '1'] for r in H]
rk = rank(rows, n)
cols = [[i + 1 for i, r in enumerate(H) if r[c] == '1'] for c in range(n)]
with open(os.path.join(KOR, 'tablicy', 'ldpc', 'gallager_fig21.alist'), 'w') as f:
    f.write('%d %d\n%d %d\n' % (n, len(H), j, k) + ' '.join([str(j)] * n) + '\n' + ' '.join([str(k)] * len(H)) + '\n')
    for c in cols: f.write(' '.join(map(str, c)) + '\n')
    for r in rows: f.write(' '.join(map(str, r)) + '\n')
F = 'istochniki/ldpc/gallager1963_ldpc_book.pdf'
zap = [Z('LDPC Галлагера (n, j, k) — регулярный ансамбль; пример рис. 2.1: (20, j=3, k=4)', 'Gallager/MacKay', 'LDPC',
         {'построение': 'H из j полос по n/k строк; первая полоса — строки с k подряд идущими единицами; остальные — случайные перестановки столбцов первой (гл. 2.2)',
          'n,k': '%d,%d (ранг H = %d при %d строках — строки зависимы)' % (n, n - rk, rk, len(H)), 'веса': 'столбцы j=3, строки k=4', 'матрица': 'tablicy/ldpc/gallager_fig21.alist'},
         'теория LDPC; все регулярные (j,k) коды; декодирование — алгоритмы A/B Галлагера и вероятностный (BP)',
         [I(F, 'стр. 14 PDF (с. 13 книги), рис. 2.1; гл. 2.2 «Distance Properties of Low-Density Codes»', 'http://web.mit.edu/gallager/www/pages/ldpc.pdf')],
         'матрица переписана с изображения; веса, разбиение на полосы-перестановки и первая полоса — проверены; ранг GF(2) = %d' % rk,
         'ЕСТЬ в проекте: ldpc.py (alist), dlinnye.py (поиск разреженных проверок вслепую)')]
sohranit('gallager', zap)
