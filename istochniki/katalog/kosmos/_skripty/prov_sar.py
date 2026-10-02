"""Cospas-Sarsat: проверка кодов маяков 406 МГц по примерам самих спецификаций (тестовые векторы).
T.001 (1-е поколение): BCH(82,61) — g = m1·m3·m5 (прил. B, стр. 82) и пример B1 (стр. 83–84); BCH(38,26) 12 бит — пример B2 (стр. 85–86).
T.018 (2-е поколение): BCH(250,202) — g = НОК(m1,m3,…,m11) (прил. B, стр. 61–62) и пример B.1 (стр. 63–64);
                       ПСП расширения X^23+X^18+1 — первые 64 чипа для 4 начальных состояний табл. 2.2 (стр. 15).
Выход: tablicy/sar/_itog_sar.json"""
import json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'sar')


def stranicy(fn):
    t = open(os.path.join(IST, fn + '.txt'), encoding='utf8').read()
    def it(c):
        o = ord(c)
        if 0x1D434 <= o <= 0x1D44D:
            return chr(ord('A') + o - 0x1D434)
        if 0x1D44E <= o <= 0x1D467:
            return chr(ord('a') + o - 0x1D44E)
        return c
    t = ''.join(it(c) for c in t)
    return {int(a): ' '.join(b.split()) for a, b in re.findall(r'=====PAGE (\d+)=====\n(.*?)(?=\n=====PAGE|\Z)', t, re.S)}


def pmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1; b >>= 1
    return r


def pmod(a, g):
    dg = g.bit_length()
    while a.bit_length() >= dg:
        a ^= g << (a.bit_length() - dg)
    return a


def poly(s):  # 'X21 + X18 + ... + X + 1' → int
    v = 0
    for ch in s.replace(' ', '').split('+'):
        if ch in ('1', 'x0', 'X0'):
            v |= 1
        elif ch in ('X', 'x'):
            v |= 2
        else:
            v |= 1 << int(ch[1:])
    return v


itog = {}
s1 = stranicy('CS_T001_2021.pdf')
p = s1[82]
m = [poly(re.search(r'm%d \(X\) = ([X\d +]+?) (?:m|from)' % k, p).group(1)) for k in (1, 3, 5)]
g1 = pmul(pmul(m[0], m[1]), m[2])
g1_txt = int(re.search(r'22-bit binary number: g\(X\) = ([01]{22})', p).group(1), 2)
assert g1 == g1_txt == poly(re.search(r'g\(X\) = m1 \(X\) m3 \(X\) m5 \(X\) = ([X\d +]+?) a determination', p).group(1))
b = ''.join(re.search(r'Bits 25-112: ((?:[01]{4} ){21}[01]{4})', s1[83]).group(1).split())
assert len(b) == 88
dan, bch_v = b[:61], b[61:82]
r = pmod(int(dan, 2) << 21, g1)
bch_txt = re.search(r'BCH Error-Correcting Code: ([01]{21})', s1[83]).group(1)
assert format(r, '021b') == bch_txt == bch_v
itog['t001_bch1'] = 'g=m1·m3·m5 = %s (X^21+X^18+…+1, стр. 82) — совпало; пример B1: 61 бит данных → BCH-1 %s == напечатанному и битам 86–106 сообщения 56E68 04002 20200 96552 50' % (format(g1, 'b'), bch_txt)
p = s1[85]
m6a, m6b = poly('1 + X + X6'), poly('1 + X + X2 + X4 + X6')
g2 = pmul(m6a, m6b)
assert g2 == poly('1 + X3 + X4 + X5 + X8 + X10 + X12')
d2 = ''.join(re.search(r'Placing the binary bits 107-132 in order gives: ([01 ]+?) and', p).group(1).split())
assert len(d2) == 26
r2 = format(pmod(int(d2, 2) << 12, g2), '012b')
ozh = ''.join(re.search(r'resultant 12-bit BCH code is: ([01 ]{14})', p).group(1).split())
assert r2 == ozh, (r2, ozh)
itog['t001_bch2'] = 'g=(1+x+x^6)(1+x+x^2+x^4+x^6)=1+x^3+x^4+x^5+x^8+x^10+x^12 (стр. 85); пример B2: 26 бит → %s == напечатанному' % r2

s2 = stranicy('CS_T018_2021.pdf')
p = s2[61]
ms = [poly(re.search(r'm%d\(X\) = ([X\d +]+?)(?: m\d|$| from)' % k, p).group(1)) for k in (1, 3, 5, 7, 9, 11)]
g3 = 1
for q in ms:
    g3 = pmul(g3, q)
g3_txt = int(re.search(r'49-bit binary number g\(X\) = ([01]{49})', s2[62]).group(1), 2)
assert g3 == g3_txt
d3 = ''.join(re.search(r'Bits 1-202 ((?:[01]{4} ){50}[01]{2})', s2[63]).group(1).split())
assert len(d3) == 202
r3 = format(pmod(int(d3, 2) << 48, g3), '048b')
ozh3 = re.search(r'([01]{48}) REFERENCE', s2[64]).group(1)
assert r3 == ozh3, (r3, ozh3)
itog['t018_bch'] = 'g=НОК(m1,m3,m5,m7,m9,m11) = произведение 6 многочленов степени 8 == 49-битному числу (стр. 62); пример B.1: 202 бита → BCH 48 бит %s == напечатанному (стр. 64)' % r3
# ПСП X^23+X^18+1 (рис. 2-2): выход X0; temp = X0 ⊕ X18; сдвиг Xn→Xn−1; temp → X22
t = s2[15]
stroki = re.findall(r'(Normal I|Normal Q|Self Test I|Self Test Q) ((?:[01] ){23})→ ((?:[0-9A-F]{4} ?){4})', t)
assert len(stroki) == 4
psp = {}
for imya, ini, hx in stroki:
    reg = [int(x) for x in ini.split()]  # регистры 22 … 0
    X = {22 - i: reg[i] for i in range(23)}
    out = []
    for _ in range(64):
        out.append(X[0])
        tmp = X[0] ^ X[18]
        X = {n - 1: X[n] for n in range(1, 23)}
        X[22] = tmp
    moi = '%016X' % int(''.join(map(str, out)), 2)
    assert moi == hx.replace(' ', ''), (imya, moi, hx)
    psp[imya] = moi
itog['t018_psp'] = 'ПСП X^23+X^18+1: первые 64 чипа для 4 начальных состояний табл. 2.2 (стр. 15) воспроизведены: %s' % psp
os.makedirs(os.path.join(KOR, 'tablicy', 'sar'), exist_ok=True)
json.dump(itog, open(os.path.join(KOR, 'tablicy', 'sar', '_itog_sar.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
