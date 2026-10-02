"""CCSDS 414.1-B-3: составляющие ПСП дальномерного кода T4B/T2B извлекаются из векторного рисунка 3-1/3-2 (слова PDF по координатам),
строится код C = sign(ν·C1 + C2 − C3 − C4 + C5 − C6), L = 1 009 470; проверка — дробные корреляции ξ с каждой составляющей
равны табл. 2-5 (T2B) и 2-6 (T4B) зелёной книги CCSDS 414.0-G-2 (стр. 33).
Выход: tablicy/ccsds/pn_ranging_414_sostavlyayushchie.json, строка в tablicy/ccsds/_itog_ccsds_414.json"""
import collections, json, os, re
import numpy as np, pymupdf
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = pymupdf.open(os.path.join(KOR, 'istochniki/ccsds/414x1b3e1.pdf'))
w = d[14].get_text('words')
rows = collections.defaultdict(list)
for x0, y0, x1, y1, t, *_ in w:
    if t in ('+1', '–1', '-1', '−1'):
        rows[round((y0 + y1) / 2)].append((x0, 1 if t == '+1' else -1))
seqs = [[v for _, v in sorted(r)] for _, r in sorted(rows.items())]
dl = [len(s) for s in seqs]
assert dl == [2, 7, 11, 15, 19, 23], dl
C = {i + 1: np.array(s) for i, s in enumerate(seqs)}
L = 2 * 7 * 11 * 15 * 19 * 23
t = np.arange(L)
Ck = {k: C[k][t % len(C[k])] for k in C}
znak = {1: 1, 2: 1, 3: -1, 4: -1, 5: 1, 6: -1}
g = ' '.join(open(os.path.join(KOR, 'istochniki/ccsds/414x0g2.pdf.txt')).read().split())
itog = {}
for nu, tab in ((4, 'Table 2-6: v=4'), (2, 'Table 2-5: v=2')):
    S = nu * Ck[1] + Ck[2] - Ck[3] - Ck[4] + Ck[5] - Ck[6]
    code = np.sign(S)
    assert not (S == 0).any()
    xi = {k: float((code * znak[k] * Ck[k]).mean()) for k in C}
    frag = g[g.index(tab):g.index(tab) + 400]
    ozh = [float(x) for x in re.findall(r'(?:C1 \(range clock\)|C2|-C3|-C4|C5|-C6) (0\.\d{4})', frag)]
    assert len(ozh) == 6, frag
    for k in C:
        assert abs(xi[k] - ozh[k - 1]) < 6e-5, (nu, k, xi[k], ozh[k - 1])
    itog['T%dB' % nu] = 'ξ по составляющим C1..C6 (со знаками C2,−C3,−C4,C5,−C6) = %s == %s (414.0-G-2 %s, стр. 33)' % ([round(v, 4) for v in xi.values()], ozh, tab)
vyh = {'источник': 'istochniki/ccsds/414x1b3e1.pdf, рис. 3-1 (стр. 15)'}
vyh.update({'C%d' % k: seqs[k - 1] for k in range(1, 7)})
json.dump(dict(vyh, **{'комбинирование': 'C = sign(ν·C1 + C2 − C3 − C4 + C5 − C6), ν=4 (T4B), 2 (T2B)', 'L': L}),
          open(os.path.join(KOR, 'tablicy/ccsds/pn_ranging_414_sostavlyayushchie.json'), 'w'), ensure_ascii=False)
json.dump(itog, open(os.path.join(KOR, 'tablicy/ccsds/_itog_ccsds_414.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
