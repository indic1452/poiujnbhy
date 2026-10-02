"""Малые проверки по первоисточникам (assert): AOS FHEC RS(10,6)/GF(16); GMR-1 Golay(24,12) и RS(15,9)/GF(16);
BGAN/Inmarsat: таблица хвостовых бит турбокода 23/35 и скремблер 1+X+X^15 (сравнение с JAERO); RS CCSDS в AO-40 (ka9q)."""
import json, os, re

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki')
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
VYH = os.path.join(KOR, 'tablicy')


def str_(fn, n):
    t = open(os.path.join(IST, fn), encoding='utf8').read()
    return ' '.join(re.search(r'=====PAGE %d=====\n(.*?)(?======PAGE|\Z)' % n, t, re.S).group(1).split())


def gf(prim, m):
    exp, log = [0] * (2 * (1 << m)), [0] * (1 << m)
    x = 1
    for i in range((1 << m) - 1):
        exp[i] = x; log[x] = i
        x <<= 1
        if x >> m:
            x ^= prim
    for i in range((1 << m) - 1, 2 * (1 << m)):
        exp[i] = exp[i - ((1 << m) - 1)]
    return exp, log


itog = {}
# ---- AOS FHEC (732.0-B-5 стр. 61): g(x)=(x+α^6)(x+α^7)(x+α^8)(x+α^9) = x^4+α^3x^3+αx^2+α^3x+1, F=x^4+x+1
t = str_('ccsds/732x0b5ec1.pdf.txt', 61)
assert 'F(X) = x4 + x + 1' in t and 'α6 = 1100, α7 = 1011 α8 = 0101, α9 = 1010' in t
exp, log = gf(0b10011, 4)
assert [exp[6], exp[7], exp[8], exp[9]] == [0b1100, 0b1011, 0b0101, 0b1010]
g = [1]
for j in (6, 7, 8, 9):
    r = exp[j]
    ng = [0] * (len(g) + 1)
    for i, c in enumerate(g):
        ng[i] ^= (0 if c == 0 else exp[log[c] + log[r]])
        ng[i + 1] ^= c
    g = ng
assert g == [1, exp[3], exp[1], exp[3], 1], g  # коэффициенты от x^0 до x^4
itog['aos_fhec'] = 'g(x) из корней α^6..α^9 = x^4+α^3x^3+αx^2+α^3x+1 (стр. 61) — совпало'

# ---- GMR-1 Golay (24,12) (TS 101 376-5-3 V1.2.1, стр. 17): G из hex, dmin=8
t = str_('gmr1/ts_1013760503v010201p.pdf.txt', 17)
G = [int(h, 16) for h in re.findall(r'\(0x([0-9a-f]{6})\)', t)]
assert len(G) == 12
wmin = 24
for u in range(1, 4096):
    c = 0
    for i in range(12):
        if u >> (11 - i) & 1:
            c ^= G[i]
    wmin = min(wmin, bin(c).count('1'))
assert wmin == 8, wmin
itog['gmr1_golay'] = 'G (12 строк hex, стр. 17) порождает код с dmin = 8 (перебор 4095 слов) — расширенный Голей (24,12)'
# ---- GMR-1 RS(15,9) над GF(16), p(X)=1+X+X^4; табл. 4.6 (порядок бит x^0..x^3 слева?), табл. 4.8 g0..g5
t18, t19 = str_('gmr1/ts_1013760503v010201p.pdf.txt', 18), str_('gmr1/ts_1013760503v010201p.pdf.txt', 19)
tab46 = re.findall(r'α(\d+)(?: = α15 = 1)? ([01]{4}) (\d+)', t18)
exp16, log16 = gf(0b10011, 4)
# в таблице 4.6 двоичная запись дана коэффициентами 1,X,X^2,X^3 слева направо: α1 = 0100 → X
for pw, b, dec in tab46:
    assert int(b, 2) == int(dec)
    v = int(b[::-1], 2)  # переворот: левый бит — x^0
    assert v == exp16[int(pw) % 15], (pw, b)
g_tab = {int(k): int(p) for k, p in re.findall(r'g(\d) α(\d+) [01]{4} \d+', t19)}
assert len(g_tab) == 6
naid = []
for b0 in range(15):
    g = [1]
    for j in range(b0, b0 + 6):
        r = exp16[j % 15]
        ng = [0] * (len(g) + 1)
        for i, c in enumerate(g):
            ng[i] ^= (0 if c == 0 else exp16[log16[c] + log16[r]])
            ng[i + 1] ^= c
        g = ng
    if all(g[i] == exp16[g_tab[i]] for i in range(6)) and g[6] == 1:
        naid.append(b0)
assert naid, 'корни порождающего многочлена не найдены'
itog['gmr1_rs15_9'] = 'табл. 4.6 GF(16) согласована с p(X)=1+X+X^4 (порядок бит: левый — X^0); g(X) табл. 4.8 = ∏(X+α^j), j=%d..%d' % (naid[0], naid[0] + 5)

# ---- BGAN (TS 102 744-2-1 стр. 28-29): RSC 23/35, таблица 5.11 хвостовых бит
t29 = str_('inmarsat/ts_1027440201v010101p.pdf.txt', 29)
tab = re.findall(r'([01]{4}) ([01]) ([01]) ([01]) ([01])', t29)
assert len(tab) == 16
# регистр (s1 s2 s3 s4) слева направо; обратная связь 1+X^3+X^4: a = u ^ s3 ^ s4; новое состояние (a, s1, s2, s3)
def shag(st, u):
    s = [int(c) for c in st]
    a = u ^ s[2] ^ s[3]
    return ''.join(map(str, [a, s[0], s[1], s[2]]))
for st, *fl in tab:
    x = st
    for b in map(int, fl):
        x = shag(x, b)
    assert x == '0000', (st, fl, x)
itog['bgan_flush'] = 'для всех 16 состояний хвостовые биты табл. 5.11 приводят RSC (обр. связь 23₈ = 1+X³+X⁴) в нуль'
# ---- Скремблер Inmarsat 1+X+X^15, начальное 110 1001 0101 1001 (6959h) = JAERO AeroLScrambler {1,1,0,1,0,0,1,0,1,0,1,1,0,0,1}
t27 = str_('inmarsat/ts_1027440201v010101p.pdf.txt', 27)
assert '1 + X + X15' in t27 and '110 1001 0101 1001 (6959h)' in t27
jaero = open(os.path.join(REPOS, 'JAERO', 'JAERO', 'aerol.h')).read()
assert 'int tmp[]={1,1,0,1,0,0,1,0,1,0,1,1,0,0,1,-1};' in jaero and 'state.at(0)^state.at(14)' in jaero
assert int('110100101011001', 2) == 0x6959
itog['inmarsat_scrambler'] = 'BGAN: 1+X+X^15, нач. 6959h; JAERO (Aero): тот же 15-битный регистр 110100101011001, отводы 1 и 15 — совпало'
# ---- AO-40 (ka9q encode_ref.c): RS_poly CCSDS = прил. G 131.0-B-6
src = open(os.path.join(IST, 'lyubit', 'ka9q_ao40_encode_ref.c')).read()
rsp = [int(x) for x in re.search(r'RS_poly\[\] = \{(.*?)\}', src, re.S).group(1).replace('\n', ' ').split(',')]
annex = json.load(open(os.path.join(VYH, 'ccsds', '_itog_ccsds_tm.json')))['rs']['коды']['16']['g_степени_alpha_от_x0']
assert rsp == annex[1:17], (rsp, annex[1:17])
itog['ao40_rs'] = 'RS_poly[] ka9q (C1..C16) = степени α коэффициентов g(x) CCSDS E=16 (прил. G 131.0-B-6)'
json.dump(itog, open(os.path.join(VYH, '_itog_malye.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))

# ---- GMR-1: таблицы next_output osmo-gmr (conv.c) против многочленов ETSI TS 101 376-5-3 (стр. 12-14)
osmo = open(os.path.join(REPOS, 'osmo-gmr', 'src', 'l1', 'conv.c')).read()
def osmo_tab(imya):
    blk = osmo.split('static const uint8_t %s_next_output[][2] = {' % imya)[1].split('};')[0]
    return [tuple(map(int, p)) for p in re.findall(r'\{\s*(\d+),\s*(\d+)\s*\}', blk)]
etsi = {  # из текста стандарта (стр. 12-14)
    'gmr1_conv_k5_12': ['1+D3+D4', '1+D+D2+D4'],
    'gmr1_conv_k5_13': ['1+D2+D4', '1+D+D3+D4', '1+D+D2+D3+D4'],
    'gmr1_conv_k5_14': ['1+D3+D4', '1+D+D2+D4', '1+D2+D4', '1+D+D2+D3+D4'],
    'gmr1_conv_k5_15': ['1+D2+D4', '1+D+D3+D4', '1+D+D2+D3+D4', '1+D2+D3+D4', '1+D+D2+D4'],
}
tt = ' '.join(''.join(str_('gmr1/ts_1013760503v010201p.pdf.txt', p) for p in (12, 13, 14)).split()).replace(' ', '')
for imya, mn in etsi.items():
    for m in mn:
        assert m.replace('+', '+') in tt or m in tt, (imya, m)
def stepeni(m):
    return [0 if t == '1' else (1 if t == 'D' else int(t[1:])) for t in m.split('+')]
sootv = None
for konv in ('новый_бит_старший', 'новый_бит_младший'):
    ok = True
    for imya, mn in etsi.items():
        tab = osmo_tab(imya)
        for s in range(16):
            for b in (0, 1):
                # история: u(k)=b, u(k-1..k-4) из состояния
                if konv == 'новый_бит_старший':
                    ist = [b] + [(s >> (3 - i)) & 1 for i in range(4)]
                else:
                    ist = [b] + [(s >> i) & 1 for i in range(4)]
                vyh = 0
                for m in mn:
                    vyh = (vyh << 1) | (sum(ist[d] for d in stepeni(m)) & 1)
                if tab[s][b] != vyh:
                    ok = False
    if ok:
        sootv = konv
assert sootv, 'таблицы osmo-gmr не совпали с многочленами ETSI ни при одном соглашении'
itog['gmr1_conv_osmo'] = 'таблицы next_output osmo-gmr (K=5, r=1/2, 1/3, 1/4, 1/5) порождены ровно многочленами ETSI TS 101 376-5-3 (соглашение: %s)' % sootv
json.dump(itog, open(os.path.join(VYH, '_itog_malye.json'), 'w'), ensure_ascii=False, indent=1)
print(itog['gmr1_conv_osmo'])
