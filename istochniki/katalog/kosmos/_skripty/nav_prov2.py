"""Сверка кодов BeiDou (B1C/B2a/B2b/PPP-B2b): матрицы 64-ичных LDPC из ICD (текст PDF) == таблицы PocketSDR (src/sdr_ldpc.c, BSD-2).
Выход: tablicy/nav/ldpc_bds_*.json (индексы и элементы строк H, из ICD) и _itog_nav2.json."""
import json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'nav', 'bds')
PSDR = os.path.join(os.path.dirname(KOR), 'repos', 'PocketSDR', 'src', 'sdr_ldpc.c')
itog = {}


def txt(fn):
    t = open(os.path.join(IST, fn + '.txt'), encoding='utf8', errors='replace').read()
    return re.sub(r'=====PAGE \d+=====', ' ', t)


def icd_H(fn, m, n, vid):
    t = txt(fn)
    # колонтитулы страниц содержат цифры (год, номер страницы, версия) — вырезаем строки колонтитула
    L = t.split('\n'); ch = []; i = 0
    while i < len(L):
        l = L[i]
        if 'China Satellite Navigation Office' in l:
            i += 2 if (i + 1 < len(L) and re.fullmatch(r'\s*[\dIVX]+\s*', L[i + 1])) else 1; continue
        if re.match(r'\s*BDS-SIS-ICD-', l):
            i += 2 if (i + 1 < len(L) and re.fullmatch(r'\s*\d{4}-\d{2}\s*', L[i + 1])) else 1; continue
        if re.fullmatch(r'\s*\d+\s*', l) and i + 1 < len(L) and re.match(r'\s*BDS-SIS-ICD-', L[i + 1]):
            i += 1; continue
        ch.append(l); i += 1
    t = '\n'.join(ch)
    mo = re.search(r'H\s*%d\s*,\s*%d\s*,\s*%s\s*=\s*\[(.*?)\]' % (m, n, vid), t, re.S)
    assert mo, (fn, m, n, vid)
    ch = [int(x) for x in mo.group(1).split()]
    assert len(ch) == 4 * m, (fn, vid, len(ch), 4 * m)
    return [ch[i:i + 4] for i in range(0, len(ch), 4)]


def psdr(imya):
    t = open(PSDR).read()
    mo = re.search(r'static const uint8_t %s\[\]\[4\] = \{[^\n]*\n(.*?)\n\};' % imya, t, re.S)
    ch = [int(x) for x in re.findall(r'\d+', mo.group(1))]
    return [ch[i:i + 4] for i in range(0, len(ch), 4)]


KODY = [('BDS_B1C_v1.0.pdf', 100, 200, 'H_BCNV1_SF2', 'ldpc_bds_b1c_sf2_200_100'),
        ('BDS_B1C_v1.0.pdf', 44, 88, 'H_BCNV1_SF3', 'ldpc_bds_b1c_sf3_88_44'),
        ('BDS_B2a_v1.0.pdf', 48, 96, 'H_BCNV2', 'ldpc_bds_b2a_96_48'),
        ('BDS_B2b_v1.0.pdf', 81, 162, 'H_BCNV3', 'ldpc_bds_b2b_162_81'),
        ('BDS_PPP_B2b_v1.0.pdf', 81, 162, 'H_BCNV3', 'ldpc_bds_ppp_b2b_162_81')]
for fn, m, n, ps, vyh in KODY:
    idx, ele = icd_H(fn, m, n, 'index'), icd_H(fn, m, n, 'element')
    assert all(0 <= j < n for r in idx for j in r) and all(1 <= e < 64 for r in ele for e in r)
    # каждый столбец задействован (вес столбца ≥ 1), строки — 4 разных столбца
    assert all(len(set(r)) == 4 for r in idx)
    ves = [0] * n
    for r in idx:
        for j in r:
            ves[j] += 1
    pi, pe = psdr(ps + '_idx'), psdr(ps + '_ele')
    sovp = (pi == idx and pe == ele)
    assert sovp, (fn, ps)
    json.dump({'источник': 'istochniki/nav/bds/' + fn, 'n': n, 'k': n - m, 'поле': 'GF(2^6), p(x)=1+x+x^6, символ = 6 бит, старший первым',
               'H_index (по строкам, 4 ненулевых)': idx, 'H_element (показатель → вектор по табл. ICD)': ele,
               'веса столбцов': sorted(set(ves))}, open(os.path.join(KOR, 'tablicy', 'nav', vyh + '.json'), 'w'), ensure_ascii=False)
    itog[vyh] = 'H(%d×%d) из ICD == PocketSDR %s_idx/_ele — совпало (%d строк); веса столбцов %s' % (m, n, ps, m, sorted(set(ves)))

# PPP-B2b и B2b: одна и та же матрица?
a = json.load(open(os.path.join(KOR, 'tablicy', 'nav', 'ldpc_bds_b2b_162_81.json')))
b = json.load(open(os.path.join(KOR, 'tablicy', 'nav', 'ldpc_bds_ppp_b2b_162_81.json')))
itog['b2b_ravno_ppp_b2b'] = a['H_index (по строкам, 4 ненулевых)'] == b['H_index (по строкам, 4 ненулевых)'] and a['H_element (показатель → вектор по табл. ICD)'] == b['H_element (показатель → вектор по табл. ICD)']

# GF(64): p(x)=1+x+x^6 порождает таблицу степеней PocketSDR GF_VEC
t = open(os.path.join(os.path.dirname(PSDR), 'sdr_nb_ldpc.c')).read()
gv = [int(x) for x in re.findall(r'\d+', re.search(r'GF_VEC\[Q_GF\] = \{[^\n]*\n(.*?)\};', t, re.S).group(1))]
v, moi = 1, []
for i in range(63):
    moi.append(v)
    v <<= 1
    if v & 64:
        v ^= 0b1000011
itog['gf64'] = 'p(x)=1+x+x^6 (ICD B1C 6.2.2.2) порождает GF_VEC PocketSDR: %s' % (moi == gv[:63])
assert moi == gv[:63]
json.dump(itog, open(os.path.join(KOR, 'tablicy', 'nav', '_itog_nav2.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))

# ---------------------------------------------------------------- Galileo HAS: RS(255,32,224) над GF(256) (HAS SIS ICD 1.0, §6.2, табл. 42, прил. C)
HAS = os.path.join(KOR, 'istochniki', 'nav', 'Galileo_HAS_SIS_ICD_v1.0.pdf.txt')
th = open(HAS, encoding='utf8').read()
st = {int(a): ' '.join(b.split()) for a, b in re.findall(r'=====PAGE (\d+)=====\n(.*?)(?=\n=====PAGE|\Z)', th, re.S)}
t42 = st[35][st[35].index('j gj'):] + ' ' + st[36][:st[36].index('Table 42')]
t42 = re.sub(r'\d+ © European Union 2022 Galileo HAS SIS ICD, Issue 1.0, May 2022', ' ', t42).replace('j gj', ' ')
chisla = [int(x) for x in t42.split()]
assert len(chisla) == 2 * 224, len(chisla)
g_icd = {}
for i in range(0, len(chisla), 2):
    g_icd[chisla[i]] = chisla[i + 1]
assert sorted(g_icd) == list(range(224))
EXP, LOG = [0] * 512, [0] * 256
v = 1
for i in range(255):
    EXP[i] = v; LOG[v] = i
    v <<= 1
    if v & 256:
        v ^= 0x11D
for i in range(255, 512):
    EXP[i] = EXP[i - 255]
def gmul(a, b):
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]
g = [1]
for i in range(1, 224):
    a = EXP[i]
    ng = [0] * (len(g) + 1)
    for j, c in enumerate(g):
        ng[j + 1] ^= c           # c·x^(j+1)
        ng[j] ^= gmul(c, a)      # c·α^i ·x^j (минус = плюс)
    g = ng
assert g == [g_icd[j] for j in range(224)], 'g(x) не совпал с табл. 42'
itog['galileo_has_rs_g'] = 'g(x)=∏_{i=1}^{223}(x−α^i), p=x^8+x^4+x^3+x^2+1: все 224 коэффициента == табл. 42 (стр. 35–36)'
# систематическая G (255×32): строки Γ254..Γ0; столбец j ↔ c_{31−j}
_kesh = {}
def ostatok(stepen):  # R_g[x^stepen] — деление в столбик на монический g степени 223
    if stepen not in _kesh:
        r = [0] * (stepen + 1); r[stepen] = 1
        for d in range(stepen, 222, -1):
            c = r[d]
            if c:
                for q in range(224):
                    r[d - 223 + q] ^= gmul(c, g[q])
        _kesh[stepen] = r[:223]
    return _kesh[stepen]
G = []
for strk in range(255):
    Gi = strk  # индекс Γ = 254 − strk
    idx = 254 - Gi
    row = []
    for j in range(32):
        ci = 31 - j
        if idx >= 223:
            row.append(1 if idx - 223 == ci else 0)
        else:
            row.append(ostatok(ci + 223)[idx])
    G.append(row)
# прил. C: PID и матрица D
tc = ' '.join(st[p] for p in range(48, 52))
pids = [int(x) for x in re.findall(r'PID: (\d+)\. \[', tc)]
pids = list(dict.fromkeys(pids))[:15]
D_icd = [int(x) for x in re.search(r'D = \[(.*?)\]', tc).group(1).split()]
D_moi = [G[p - 1][j] for p in pids for j in range(15)]
assert D_moi == D_icd, 'матрица D прил. C не совпала'
w1 = [int(x) for x in re.search(r"w’1 = \[(.*?)\]", tc).group(1).split()]
m1 = [int(x) for x in re.search(r"m’1 = \[(.*?)\]", tc).group(1).split()]
# проверка: D·m1 == w1
Dm = [0] * 15
for r in range(15):
    s = 0
    for c in range(15):
        s ^= gmul(D_icd[r * 15 + c], m1[c])
    Dm[r] = s
assert Dm == w1
itog['galileo_has_rs_primer'] = 'прил. C (стр. 48–50): матрица D из PID %s, построенная по нашей G, == напечатанной (225 элементов); D·m’1 == w’1' % pids
json.dump({'источник': 'istochniki/nav/Galileo_HAS_SIS_ICD_v1.0.pdf, табл. 42', 'поле': 'GF(256), p=x^8+x^4+x^3+x^2+1 (0x11D)', 'g_j (j=0..223)': g},
          open(os.path.join(KOR, 'tablicy', 'nav', 'rs_galileo_has_255_32_g.json'), 'w'), ensure_ascii=False)
json.dump(itog, open(os.path.join(KOR, 'tablicy', 'nav', '_itog_nav2.json'), 'w'), ensure_ascii=False, indent=1)
print(itog['galileo_has_rs_g']); print(itog['galileo_has_rs_primer'])
