"""LunaNet LSIS-AFS Vol. A (NASA, 29.01.2025): LDPC r=1/2 подкадров SB2 (1200→2400) и SB3/SB4 (870→1740).
Источник подматриц — вложения PDF (Annex 2, CSV): индексы *_ind.csv и полные матрицы *_mat.csv.
Проверки (assert):
  1) индексная и полная формы каждой подматрицы совпадают (два независимых представления из стандарта);
  2) B·B⁻¹ = I над GF(2) (B⁻¹ дан в стандарте отдельно);
  3) кодер по тексту стандарта p1 = B⁻¹·A·s, p2 = C·s + D·p1 даёт слово c=(s,p1,p2) с H·c = 0, где H = [A B 0; C D I];
  4) число столбцов A = число бит подкадра + заполнитель (SB2: 1200+0, SB3/4: 870+10); z = 240 / 176 выкалываемых систем. бит;
  5) CRC-24: G(X)=(1+X)·P(X) из текста = 0x1864CFB (CRC-24Q, как GPS L1C / Galileo).
  6) H совпадает с базовым графом 2 5G NR (TS 38.212) при Z=120 и Z=88 (сдвиги — data/ldpc_nr.json проекта).
Выход: tablicy/lunanet/lunanet_afs_ldpc.json (индексы подматриц A, B, B⁻¹, C, D для SB2 и SB3)."""
import csv, json, os, random, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pokaz import KOR
from gf import pmul

V = os.path.join(KOR, 'istochniki/dop2/lsis_vlozheniya')
IM = {'sf2': {'a': '003a', 'b': '003b', 'b_inv': '003c', 'c': '003d', 'd': '003e'},
      'sf3': {'a': '003f', 'b': '003g', 'b_inv': '003h', 'c': '003i', 'd': '003j'}}
MM = {'sf2': {'a': '004h', 'b': '004j', 'b_inv': '004i', 'c': '004f', 'd': '004g'},
      'sf3': {'a': '004a', 'b': '004c', 'b_inv': '004b', 'c': '004d', 'd': '004e'}}


def fayl(pref):
    return next(os.path.join(V, f) for f in os.listdir(V) if f.startswith(pref))


def mat(pref):
    rows = [list(map(int, r)) for r in csv.reader(open(fayl(pref))) if r]
    return np.array(rows, dtype=np.uint8)


def ind(pref, shape):
    M = np.zeros(shape, dtype=np.uint8)
    pary = [tuple(map(int, r)) for r in csv.reader(open(fayl(pref))) if r]
    for i, j in pary:
        assert M[i, j] == 0
        M[i, j] = 1
    return M, pary


def g2(a, b):
    return (a.astype(np.int64) @ b.astype(np.int64)) % 2


itog, vyhod = {}, {}
for sf, (m, n, z) in {'sf2': (1200, 2400, 240), 'sf3': (870, 1740, 176)}.items():
    P = {}
    for k in ('a', 'b', 'b_inv', 'c', 'd'):
        Mf = mat(MM[sf][k])
        Mi, pary = ind(IM[sf][k], Mf.shape)
        assert (Mf == Mi).all(), (sf, k)
        P[k] = Mf
        vyhod.setdefault(sf, {})[k] = {'размер': list(Mf.shape), 'единицы (строка, столбец), с 0': pary}
    A, B, Bi, C, D = P['a'], P['b'], P['b_inv'], P['c'], P['d']
    assert (g2(B, Bi) == np.eye(B.shape[0], dtype=np.uint8)).all(), sf
    kk = A.shape[1]
    m1 = A.shape[0]
    assert B.shape == (m1, m1) and C.shape[1] == kk and D.shape == (C.shape[0], m1)
    assert m1 + C.shape[0] == kk + m1 + C.shape[0] - kk  # тождество размеров
    # H = [A B 0; C D I]: строки m1 + mc, столбцы k + m1 + mc
    mc = C.shape[0]
    N = kk + m1 + mc
    H = np.zeros((m1 + mc, N), dtype=np.uint8)
    H[:m1, :kk] = A
    H[:m1, kk:kk + m1] = B
    H[m1:, :kk] = C
    H[m1:, kk:kk + m1] = D
    H[m1:, kk + m1:] = np.eye(mc, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for _ in range(5):
        s = rng.integers(0, 2, kk).astype(np.uint8)
        p1 = g2(Bi, g2(A, s))
        p2 = (g2(C, s) + g2(D, p1)) % 2
        c = np.concatenate([s, p1, p2])
        assert not g2(H, c).any(), sf
    fill = {'sf2': 0, 'sf3': 10}[sf]
    info = {'sf2': 1200, 'sf3': 870}[sf]
    assert kk == info + fill, (sf, kk, info, fill)  # s = данные (с CRC) + заполнитель; первые z бит s выкалываются
    nuzhno_p = 2 * info - (kk - z - fill)
    assert 0 < nuzhno_p <= m1 + mc
    itog[sf] = dict(k_матрицы=kk, m1=m1, mc=mc, N_материнский=N, подъём=z // 2, z=z, заполнитель=fill,
                    передаётся='%d симв. = (k−z−заполнитель) систем. + первые %d чётности' % (2 * info, 2 * info - (kk - z - fill)))
# 6) второй источник: H = [A B 0; C D I] совпадает поэлементно с базовым графом 2 5G NR (3GPP TS 38.212) при Z=120 (i_LS=7) и Z=88 (i_LS=5);
#    сдвиги V взяты из data/ldpc_nr.json проекта (сверены там по AFF3CT, srsRAN и Sionna)
NR = json.load(open('/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_nr.json'))['графы']['2']
for sf, Z, ils in (('sf2', 120, '7'), ('sf3', 88, '5')):
    Vm = NR[ils]['V']
    assert Z in NR[ils]['Z']
    Hn = np.zeros((len(Vm) * Z, len(Vm[0]) * Z), dtype=np.uint8)
    for i, r in enumerate(Vm):
        for j, v in enumerate(r):
            if v >= 0:
                for t in range(Z):
                    Hn[i * Z + t, j * Z + (t + v % Z) % Z] = 1
    A, B, C, D = (np.array(vyhod[sf][k]['единицы (строка, столбец), с 0']) for k in ('a', 'b', 'c', 'd'))
    kk, m1, mc = 10 * Z, 4 * Z, 38 * Z
    L = np.zeros_like(Hn)
    for M_, r0, c0 in ((A, 0, 0), (B, 0, kk), (C, m1, 0), (D, m1, kk)):
        L[M_[:, 0] + r0, M_[:, 1] + c0] = 1
    L[m1:, kk + m1:] = np.eye(mc, dtype=np.uint8)
    assert (L == Hn).all(), sf
    itog[sf]['= 5G NR'] = 'базовый граф 2 TS 38.212, Z=%d (i_LS=%s): H совпала поэлементно (%d единиц)' % (Z, ils, int(L.sum()))
# CRC-24 из текста
T = open(os.path.join(KOR, 'istochniki/dop2/LSIS_AFS_volA_2025.pdf.txt'), encoding='utf8').read()
t = ' '.join(T.split())
mP = re.search(r'primitive and irreducible polynomial with the following definition: \S+ = (\S*23 \+ \S*17 \+ \S*13 \+ \S*12 \+ \S*11 \+ \S*9 \+ \S*8 \+ \S*7 \+ \S*5 \+ \S*3 \+ 1)', t)
assert mP
Pp = sum(1 << e for e in (23, 17, 13, 12, 11, 9, 8, 7, 5, 3, 0))
G = pmul(Pp, 0b11)
assert G == 0x1864CFB
itog['crc24'] = 'G(X)=(1+X)·P(X) = 0x1864CFB = CRC-24Q'
os.makedirs(os.path.join(KOR, 'tablicy/lunanet'), exist_ok=True)
json.dump({'источник': 'istochniki/dop2/LSIS_AFS_volA_2025.pdf (вложения Annex 2: istochniki/dop2/lsis_vlozheniya/003*_ind.csv, 004*_mat.csv)',
           'url': 'https://www.nasa.gov/wp-content/uploads/2025/02/lunanet-signal-in-space-recommended-standard-augmented-forward-signal-vol-a.pdf',
           'кодер': 'p1 = B⁻¹·A·s; p2 = C·s + D·p1; H = [A B 0; C D I]', 'итог': itog, 'подматрицы': vyhod},
          open(os.path.join(KOR, 'tablicy/lunanet/lunanet_afs_ldpc.json'), 'w'), ensure_ascii=False)
json.dump(itog, open(os.path.join(KOR, 'tablicy/lunanet/_itog_lunanet.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
