"""Коды LDPC CCSDS — из текста стандартов, со сверкой с открытыми реализациями.

- AR4JA (131.0-B-5, 7.4): θ_k, φ_k(j, M) — таблицы 7-3, 7-4 (стр. PDF 56–57), разбор текста PDF;
  сверены с labrador-ldpc (compact_parity_checks.rs) и ldpc-toolbox (codes/ccsds.rs). Расположение
  блоков H_1/2, H_2/3, H_4/5 — 7.4.2.2–7.4.2.3 (стр. PDF 54–55, формулы — картинкой, переписаны
  в РАСПОЛОЖЕНИЕ ниже); π_k(i) — 7.4.2.4. Построенная H сверена с alist, выгруженными программой
  ldpc-toolbox (postroeno/alist_ldpc_toolbox): совпадает вся матрица.
- C2 (8176, 7156) (131.0-B-5, 7.3, табл. 7-1, стр. PDF 51): позиции единиц первых строк циркулянтов;
  сверены со столбцом «абсолютные позиции» той же таблицы и с alist ldpc-toolbox. Передача
  (8160, 7136) — 7.3.5 (стр. PDF 53–54): 18 ведущих нулей не передаются, в конце — два нуля.
- TC (128, 64), (512, 256) (231.0-B-4, 4.2–4.3, стр. PDF 27–29): H в стандарте — картинкой, в тексте —
  порождающая W (табл. 4-1, 4-2, шестнадцатерично); разреженная H — labrador-ldpc, сверена с W:
  G·Hᵀ = 0 и ранг H = n − k.
Пишет src/reportgen/potok/data/ldpc_ccsds.json. Запуск из корня репозитория.
"""
import json
import os
import re

import numpy as np

import sys  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from postroit_obshchee import alist_pary  # noqa: E402

ZDES = os.path.dirname(os.path.abspath(__file__))
K0 = os.path.normpath(os.path.join(ZDES, '..'))
KOREN = os.path.normpath(os.path.join(K0, '..', '..'))
TM = open(os.path.join(K0, 'standarty', 'CCSDS_131.0-B-5_TM_coding.pdf.txt'), encoding='utf-8').read()
TC = open(os.path.join(K0, 'standarty', 'CCSDS_231.0-B-4_TC_coding.pdf.txt'), encoding='utf-8').read()

# ---- AR4JA: θ и φ из таблиц 7-3 и 7-4 ----
def tablica_phi(zagolovok, konec):
    i = TM.index(zagolovok)
    j = TM.index(konec, i)
    kusok = TM[i:j]
    kusok = kusok[kusok.index('213', kusok.index('213') + 1) + 3:]          # после двух «M = 27 … 213»
    chisla = [int(x) for x in re.findall(r'\d+', kusok.split('=====PAGE')[0])]
    assert len(chisla) == 26 * 16, (zagolovok, len(chisla))
    theta, a, b = [], [], []
    for k in range(26):
        ryad = chisla[16 * k:16 * k + 16]
        assert ryad[0] == k + 1
        theta.append(ryad[1]); a.append(ryad[2:9]); b.append(ryad[9:16])
    return theta, a, b

th1, phi0, phi1 = tablica_phi('Table 7-3:  Description of ϕk(0,M) and ϕk(1,M) \nk', 'Table 7-4:  Description')
th2, phi2, phi3 = tablica_phi('Table 7-4:  Description of ϕk(2,M) and ϕk(3,M) \nk', '7.4.3 ENCODING')
assert th1 == th2
PHI = [phi0, phi1, phi2, phi3]                       # PHI[j][k-1][log2(M) - 7]

lab = open(os.path.join(K0, 'otkrytyj_kod', 'labrador-ldpc', 'src', 'codes', 'compact_parity_checks.rs')).read()
th_lab = [int(x) for x in re.search(r'THETA_K: \[u8; 26\] = \[([^\]]*)\]', lab).group(1).replace(',', ' ').split()]
assert th_lab == th1, 'θ: стандарт и labrador различаются'
for mi, M in enumerate((128, 256, 512, 1024, 2048, 4096, 8192)):
    m = re.search(r'PHI_J_K_M%d: \[\[u16; 26\]; 4\] = \[(.*?)\];' % M, lab, re.S)
    ryady = re.findall(r'\[([\d,\s]+)\]', m.group(1))
    assert len(ryady) == 4
    for j in range(4):
        assert [int(x) for x in ryady[j].replace(',', ' ').split()] == [PHI[j][k][mi] for k in range(26)], (M, j)
tb = open(os.path.join(K0, 'otkrytyj_kod', 'ldpc-toolbox', 'src', 'codes', 'ccsds.rs')).read()
th_tb = [int(x) for x in re.search(r'THETA_K: \[u8; 26\] = \[([^\]]*)\]', tb).group(1).replace(',', ' ').split()]
assert th_tb == th1, 'θ: стандарт и ldpc-toolbox различаются'
print('AR4JA: θ и φ из таблиц 7-3/7-4 совпали с labrador-ldpc (все 7 M × 4 j × 26 k) и θ — с ldpc-toolbox')

# Расположение блоков (7.4.2.2–7.4.2.3): (строка блока, столбец блока, слагаемые): «I» — единичная,
# число k — Π_k. Столбцы — в H_1/2 (5 блок-столбцов); у 2/3 слева ещё 2, у 4/5 — ещё 4 (перед H_2/3).
H12 = [(0, 2, ['I']), (0, 4, ['I', 1]),
       (1, 0, ['I']), (1, 1, ['I']), (1, 3, ['I']), (1, 4, [2, 3, 4]),
       (2, 0, ['I']), (2, 1, [5, 6]), (2, 3, [7, 8]), (2, 4, ['I'])]
H23_DOP = [(1, 0, [9, 10, 11]), (1, 1, ['I']), (2, 0, ['I']), (2, 1, [12, 13, 14])]
H45_DOP = [(1, 0, [21, 22, 23]), (1, 1, ['I']), (1, 2, [15, 16, 17]), (1, 3, ['I']),
           (2, 0, ['I']), (2, 1, [24, 25, 26]), (2, 2, ['I']), (2, 3, [18, 19, 20])]
RASPOLOZHENIE = {'1/2': H12,
                 '2/3': H23_DOP + [(r, c + 2, s) for r, c, s in H12],
                 '4/5': H45_DOP + [(r, c + 4, s) for r, c, s in H23_DOP] + [(r, c + 6, s) for r, c, s in H12]}
M_TAB = {(1024, '1/2'): 512, (1024, '2/3'): 256, (1024, '4/5'): 128, (4096, '1/2'): 2048, (4096, '2/3'): 1024,
         (4096, '4/5'): 512, (16384, '1/2'): 8192, (16384, '2/3'): 4096, (16384, '4/5'): 2048}   # табл. 7-2


def pi(k, i, M):
    j = 4 * i // M
    return M // 4 * ((th1[k - 1] + j) % 4) + (PHI[j][k - 1][M.bit_length() - 8] + i) % (M // 4)


def ar4ja(k, r):
    M = M_TAB[(k, r)]
    blokov = max(c for _, c, _ in RASPOLOZHENIE[r]) + 1
    H = {}
    i = np.arange(M)
    for rb, cb, slag in RASPOLOZHENIE[r]:
        for s in slag:
            stolb = i if s == 'I' else np.array([pi(s, x, M) for x in range(M)])
            for a, b in zip(rb * M + i, cb * M + stolb):
                H[(int(a), int(b))] = H.get((int(a), int(b)), 0) ^ 1
    return M, blokov * M, sorted(key for key, v in H.items() if v)


AL = os.path.join(K0, 'postroeno', 'alist_ldpc_toolbox')
dannye = {'откуда': {}, 'ar4ja': {'theta': th1, 'phi': PHI, 'расположение': RASPOLOZHENIE,
                                  'M': {f'{k}-{r}': M for (k, r), M in M_TAB.items()}}}
for (k, r), M in M_TAB.items():
    M_, n, pary = ar4ja(k, r)
    na, ma, pa = alist_pary(os.path.join(AL, f'ar4ja_k{k}_r{r.replace("/", "_")}.alist'))
    assert (n, 3 * M) == (na, ma) and set(pary) == pa, ('AR4JA не совпал с ldpc-toolbox', k, r)
    print(f'AR4JA k={k} r={r}: H {3 * M}×{n} совпала с alist ldpc-toolbox ({len(pary)} единиц)')
dannye['откуда']['ar4ja'] = ('CCSDS 131.0-B-5, 7.4: θ, φ — табл. 7-3, 7-4 (стр. PDF 56–57), расположение — 7.4.2.2–7.4.2.3, '
                             'π_k — 7.4.2.4; сверено с labrador-ldpc и alist ldpc-toolbox (вся H)')

# ---- C2: таблица 7-1 ----
i = TM.index('Table 7-1:  Specification of Circulants')
kusok = TM[i:TM.index('7.3.4 ENCODING', i)]
zap = re.findall(r'A([12]),(\d+)\s+(\d+), (\d+)\s+(\d+), (\d+)', kusok)
assert len(zap) == 32
C2 = [[None] * 16 for _ in range(2)]
for r_, c_, a, b, aa, bb in zap:
    r_, c_ = int(r_) - 1, int(c_) - 1
    a, b, aa, bb = int(a), int(b), int(aa), int(bb)
    assert (aa, bb) == (c_ * 511 + a, c_ * 511 + b), ('абсолютные позиции не сходятся', r_, c_)
    C2[r_][c_] = [a, b]
pary = {(r * 511 + j, c * 511 + (j + s) % 511) for r in range(2) for c in range(16) for s in C2[r][c] for j in range(511)}
na, ma, pa = alist_pary(os.path.join(AL, 'ccsds_c2_8176_7156.alist'))
assert (na, ma) == (8176, 1022) and pary == pa
print('C2 (8176,7156): табл. 7-1 сошлась со столбцом абсолютных позиций и с alist ldpc-toolbox')
dannye['c2'] = C2
dannye['откуда']['c2'] = ('CCSDS 131.0-B-5, 7.3.2, табл. 7-1 (стр. PDF 51); (8160,7136) — 7.3.5 (стр. PDF 53–54): '
                          '18 нулей не передаются, два нуля в конце; сверено с alist ldpc-toolbox')

# ---- TC: W из табл. 4-1, 4-2 и H labrador ----
def tc(n, zagolovok):
    k = n // 2
    M = k // 4
    i = TC.index(zagolovok)
    kusok = TC[i:i + 1200]
    ryady = re.findall(r'Row (\d+)\s*\n?\s*([0-9A-F ]+)', kusok)[:4]
    W = np.zeros((k, k), dtype=np.uint8)
    for b, (nomer, hexs) in enumerate(ryady):
        assert int(nomer) == b * M + 1
        bity = bin(int(hexs.replace(' ', ''), 16))[2:].zfill(k)
        v = np.array([int(x) for x in bity], dtype=np.uint8)
        for t in range(M):
            ryad = np.concatenate([np.roll(v[q * M:(q + 1) * M], t) for q in range(4)])
            W[b * M + t] = ryad
    G = np.concatenate([np.eye(k, dtype=np.uint8), W], axis=1)
    return G


def rang(A):
    A = A.copy() % 2
    r = 0
    for c in range(A.shape[1]):
        p = np.flatnonzero(A[r:, c])
        if not len(p):
            continue
        p = p[0] + r
        A[[r, p]] = A[[p, r]]
        mask = A[:, c].astype(bool); mask[r] = False
        A[mask] ^= A[r]
        r += 1
        if r == A.shape[0]:
            break
    return r


dannye['tc'] = {}
for n, zag in ((128, 'Table 4-1:  Generator Matrix for (n=128,k=64)'), (512, 'Table 4-2:  Generator Matrix for (n=512,k=256)')):
    G = tc(n, zag)
    na, ma, pa = alist_pary(os.path.join(K0, 'postroeno', 'alist_labrador', f'alist_labrador_TC{n}.alist'))
    H = np.zeros((ma, na), dtype=np.uint8)
    for a, b in pa:
        H[a, b] = 1
    assert na == n and not ((G.astype(int) @ H.T.astype(int)) % 2).any(), ('G·Hᵀ ≠ 0', n)
    assert rang(H) == n - n // 2 and rang(G) == n // 2
    print(f'TC ({n},{n // 2}): G = [I | W] по табл. стандарта, H labrador: G·Hᵀ = 0, ранг H = {n // 2}')
    dannye['tc'][str(n)] = [sorted(b for a, b in pa if a == r) for r in range(ma)]
dannye['откуда']['tc'] = ('CCSDS 231.0-B-4, 4.2–4.3 (стр. PDF 27–29): W — табл. 4-1, 4-2; разреженная H — labrador-ldpc '
                          '(compact_parity_checks.rs), сверена с W: G·Hᵀ = 0, ранг H = n − k')
put = os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_ccsds.json')
with open(put, 'w', encoding='utf-8') as f:
    json.dump(dannye, f, ensure_ascii=False)
print('ВСЁ СОВПАЛО; записано', put)
