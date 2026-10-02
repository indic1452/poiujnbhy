"""GOES DCPRS (CS2, NOAA/NESDIS, v2.0, 2009): решётчатый кодер 8PSK и скремблер — программно из PDF.
1) Рис. 1 (стр. 9) — векторный: вертикальные отводы от ячеек Q1…Q7 регистра вверх (к XOR E-1) и вниз (к XOR E-0)
   извлекаются из графики страницы (PyMuPDF get_drawings) и привязываются к ячейкам по координатам подписей Q1…Q7.
2) Таблица скремблера (40 байт, стр. 8) — из текста; линейная сложность (свой Берлекэмп — Мэсси) по 320 битам.
3) FSS 15 бит, таблица фаз (табл. 1, стр. 10).
Выход: tablicy/goes_dcs/dcprs_cs2.json."""
import json, os, re, sys
import pymupdf as fitz
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pokaz import KOR, stranica

F = os.path.join(KOR, 'istochniki/dop2/DCS_Certification_Standard_V2.pdf')
d = fitz.open(F)
p = d[8]
assert 'Trellis Encoder Functional Diagram' in p.get_text()
slova = {w[4]: w[:4] for w in p.get_text('words') if re.fullmatch(r'Q[1-7]|E-[012]', w[4])}
assert len([k for k in slova if k.startswith('Q')]) == 7
centry = {k: (v[0] + v[2]) / 2 for k, v in slova.items() if k.startswith('Q')}
reg_verh = min(v[1] for k, v in slova.items() if k.startswith('Q')) - 8   # верх рамки регистра ≈ 195
reg_niz = max(v[3] for k, v in slova.items() if k.startswith('Q')) + 8    # низ рамки ≈ 227
vert = set()
for g in p.get_drawings():
    for it in g['items']:
        if it[0] == 'l':
            a, b = it[1], it[2]
            if abs(a.x - b.x) < 0.6 and abs(a.y - b.y) > 20:
                vert.add((round((a.x + b.x) / 2, 1), round(min(a.y, b.y), 1), round(max(a.y, b.y), 1)))


def yacheika(x):
    k = min(centry, key=lambda q: abs(centry[q] - x))
    assert abs(centry[k] - x) < 6, x
    return int(k[1])


vverh = sorted({yacheika(x) for x, y0, y1 in vert if y1 <= reg_verh + 1 and y0 < reg_verh - 20})
vniz = sorted({yacheika(x) for x, y0, y1 in vert if y0 >= reg_niz - 1 and y1 > reg_niz + 20})
assert slova['E-1'][1] < slova['Q1'][1] < slova['E-0'][1]  # E-1 — над регистром, E-0 — под ним
assert vverh == [1, 3, 4, 6, 7], vverh
assert vniz == [1, 2, 3, 4, 7], vniz
# запись многочленов: Q1 — самая «свежая» ячейка (вход LSB слева) → бит задержки 0
oct_ = lambda s: oct(int(''.join('1' if i in s else '0' for i in range(1, 8)), 2))[2:]
assert oct_(vverh) == '133' and oct_(vniz) == '171'

t8 = ' '.join(stranica('istochniki/dop2/DCS_Certification_Standard_V2.pdf', 8).split())
m = re.search(r'convenience, and shall be used in a circular fashion throughout the message\. ((?:[0-9A-F]{2} ){40})', t8)
assert m
tab = m.group(1).split()
assert tab[0] == '53' and tab[-1] == '0A' and len(tab) == 40
assert re.search(r'CE \(Hex\), would be XOR with the first byte in the table, 53 \(Hex\).{0,200}1001 1101, or 9D', t8)
bits = [int(c) for h in tab for c in format(int(h, 16), '08b')]


def bm(s):
    """Берлекэмп — Мэсси над GF(2) (проверен на PN9: L=9, 1+x^5+x^9)."""
    n = len(s)
    C, B, L, m_ = [1] + [0] * n, [1] + [0] * n, 0, 1
    for i in range(n):
        dd = s[i]
        for j in range(1, L + 1):
            dd ^= C[j] & s[i - j]
        if dd == 0:
            m_ += 1
        elif 2 * L <= i:
            Tt = C[:]
            for j in range(n - m_ + 1):
                C[j + m_] ^= B[j]
            L, B, m_ = i + 1 - L, Tt, 1
        else:
            for j in range(n - m_ + 1):
                C[j + m_] ^= B[j]
            m_ += 1
    return L, C[:L + 1]


_t = [1] * 9
while len(_t) < 200:
    _t.append(_t[-9] ^ _t[-5])
assert bm(_t) == (9, [1, 0, 0, 0, 0, 1, 0, 0, 0, 1])
L, C = bm(bits)
bits_lsb = [int(c) for h in tab for c in format(int(h, 16), '08b')[::-1]]
L2, C2 = bm(bits_lsb)
assert 2 * L2 <= len(bits_lsb)
# проверка: рекуррентность с многочленом C2 воспроизводит все 320 бит (порядок «младший бит байта первым»)
for i in range(L2, len(bits_lsb)):
    v = 0
    for j in range(1, L2 + 1):
        v ^= C2[j] & bits_lsb[i - j]
    assert v == bits_lsb[i]
# FSS и фазы
t7 = ' '.join(stranica('istochniki/dop2/DCS_Certification_Standard_V2.pdf', 7).split())
assert re.search(r'\(MSB\) 001111100110101 \(LSB\) The left most bit is transmitted first', t7)
t10 = ' '.join(stranica('istochniki/dop2/DCS_Certification_Standard_V2.pdf', 10).split())
fazy = re.findall(r'([01]) ([01]) ([01]) (\d+)', t10.split('Phase Symbol Degrees')[1].split('Table 1')[0])
assert len(fazy) == 8
fz = {''.join(x[:3]): int(x[3]) for x in fazy}
assert fz == {'000': 0, '001': 45, '010': 135, '011': 90, '100': 180, '101': 225, '110': 315, '111': 270}
t9 = ' '.join(stranica('istochniki/dop2/DCS_Certification_Standard_V2.pdf', 9).split())
assert re.search(r'initial state of the encoder shall be all zeros', t9) and re.search(r'additional 32 zero \(0\) data bits', t9)
os.makedirs(os.path.join(KOR, 'tablicy/goes_dcs'), exist_ok=True)
out = {'источник': 'istochniki/dop2/DCS_Certification_Standard_V2.pdf (стр. PDF 7–10)',
       'кодер': {'E-2': 'старший бит пары без кодирования', 'E-1': 'Q%s' % '⊕Q'.join(map(str, vverh)), 'E-0': 'Q%s' % '⊕Q'.join(map(str, vniz)),
                 'запись_восьм': {'E-1': oct_(vverh), 'E-0': oct_(vniz)}, 'вход': 'младший бит пары в Q1, сдвиг на каждую пару; начальное состояние 0'},
       'фазы_E2E1E0': fz, 'FSS': '001111100110101', 'скремблер_40_байт': tab,
       'скремблер_линейная_сложность_старший_первым': L,
       'скремблер_линейная_сложность_младший_первым': L2,
       'скремблер_многочлен_связи_младший_первым': '+'.join(('x^%d' % i if i else '1') for i, c in enumerate(C2) if c)}
json.dump(out, open(os.path.join(KOR, 'tablicy/goes_dcs/dcprs_cs2.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
