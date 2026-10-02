"""Проверки навигационных кодов по первоисточникам (assert).
1) GPS LNAV: уравнения чётности табл. 20-XIV IS-GPS-200N (стр. 158) == маски PocketSDR test_LNAV_parity.
2) GPS CNAV-2 (L1C): LDPC(1200,600) и (548,274) — подматрицы A,B,C,D,E,T из табл. 6.2-2..6.2-13 IS-GPS-800J
   (стр. 103-125) == массивы H_CNV2_* PocketSDR src/sdr_ldpc.c; T нижнетреугольная с единичной диагональю.
3) NavIC L1 SPS: те же проверки для табл. 30-41 (стр. 88-108) против H_IRNV1_*.
Выход: tablicy/nav/ldpc_gps_cnav2_sf2.json, ..._sf3.json, ldpc_navic_l1_sf2.json, ..._sf3.json, _itog_nav.json.
"""
import json, os, re
from collections import Counter

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'nav')
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
VYH = os.path.join(KOR, 'tablicy', 'nav')
os.makedirs(VYH, exist_ok=True)


def stranicy(fn):
    t = open(os.path.join(IST, fn), encoding='utf8').read()
    s = re.split(r'=====PAGE (\d+)=====\n', t)
    return {int(s[i]): ' '.join(s[i + 1].split()) for i in range(1, len(s), 2)}


itog = {}
# ---------------------------------------------------------------- 1) LNAV
p = stranicy('IS-GPS-200N.pdf.txt')[158]
assert 'Table 20-XIV. Parity Encoding Equations' in p
urav = {}
p = p.replace('\uf0c5', ' ^ ').replace('\uf0ab', '*')  # шрифт Symbol: ⊕ и звёздочка
for m in re.finditer(r'D(2[5-9]|30) = (D29|D30)\*((?:\s*\^\s*d\d+)+)', p):
    urav[int(m.group(1))] = (m.group(2), [int(x) for x in re.findall(r'd(\d+)', m.group(3))])
assert sorted(urav) == [25, 26, 27, 28, 29, 30], urav
psdr = open(os.path.join(REPOS, 'PocketSDR', 'python', 'sdr_nav.py')).read()
maski = [int(x, 16) for x in re.search(r'mask = \((.*?)\)', psdr).group(1).split(',')]
for j, D in enumerate(range(25, 31)):
    prev, dd = urav[D]
    # (buff>>6): бит 25 = D29*, бит 24 = D30*, биты 23..0 = d1..d24
    m = (1 << 25 if prev == 'D29' else 1 << 24)
    for d in dd:
        m |= 1 << (24 - d)
    assert m == maski[j], (D, hex(m), hex(maski[j]))
itog['gps_lnav_parity'] = 'табл. 20-XIV (стр. 158) — 6 уравнений D25..D30 == маски PocketSDR test_LNAV_parity (0x2EC7CD2...) — совпало'
json.dump({'уравнения': {'D%d' % k: {'D*': v[0], 'd': v[1]} for k, v in urav.items()},
           'маски_24бит_плюс_D29*_D30*': [hex(x) for x in maski],
           'откуда': 'IS-GPS-200N табл. 20-XIV, стр. 158 PDF'}, open(os.path.join(VYH, 'gps_lnav_chetnost.json'), 'w'),
          ensure_ascii=False, indent=1)

# ---------------------------------------------------------------- 2,3) LDPC
src = open(os.path.join(REPOS, 'PocketSDR', 'src', 'sdr_ldpc.c')).read()


def psdr_tab(imya):
    blk = re.search(r'static const uint16_t %s\s*\[\]\[2\] = \{(.*?)\};' % imya, src, re.S).group(1)
    blk = re.sub(r'//[^\n]*', '', blk)
    return [tuple(map(int, x)) for x in re.findall(r'\{\s*(\d+)\s*,\s*(\d+)\s*\}', blk)]


def icd_tab(fn, str_ot, str_do, shapka):
    st = stranicy(fn)
    tekst = ' '.join(st[i] for i in range(str_ot, str_do + 1))
    # NavIC стр. 109: продолжение табл. 41 без слова «Table 41:» в заголовке
    tekst = re.sub(r'(?<!: )(?<!\d )LDPC Submatrix T for Subframe 3 \* \(sheet 2 of 2\)', 'Table 41: LDPC Submatrix T for Subframe 3 * (sheet 2 of 2)', tekst) if 'NavIC' in fn else tekst
    tekst = re.split(r'Table (?:6\.2-14\.|42:) Number of', tekst)[0]
    chasti = re.split(shapka, tekst)
    tab = {}
    for i in range(1, len(chasti), 2):
        n = int(chasti[i])
        telo = chasti[i + 1]
        telo = re.sub(r'IS-GPS-800J 01-AUG-2022 \d+ IS-GPS-800', ' ', telo)
        # отрезаем сноску '* Coordinates ...' до конца страницы
        telo = re.sub(r'\* Coordinates.*?(?=R , C|$)', ' ', telo)
        tab.setdefault(n, []).extend((int(a), int(b)) for a, b in re.findall(r'(?<![\d.])(\d+) , (\d+)(?![\d.])', telo))
    return tab


def proverka(fn, ot, do, shapka, nomera, imena, kod, n, k, doc):
    tab = icd_tab(fn, ot, do, shapka)
    rez = {}
    for nom, bukva in zip(nomera, 'ABCDET'):
        a = tab.get(nom, [])
        b = psdr_tab(imena % bukva)
        assert a and Counter(a) == Counter(b), (kod, nom, len(a), len(b), list((Counter(a) - Counter(b)).items())[:5],
                                                 list((Counter(b) - Counter(a)).items())[:5])
        assert len(set(a)) == len(a), (kod, nom, 'повторы')
        rez[bukva] = sorted(a)
    # T нижнетреугольная с единичной диагональю
    T = set(rez['T'])
    mT = max(r for r, c in T)
    assert all((i, i) in T for i in range(1, mT + 1)) and all(c <= r for r, c in T), kod
    # размеры: m = n-k проверок; T (m-g)x(m-g); A (m-g)x(n-m); E g x (m-g)
    m = n - k
    g = m - mT
    rA = max(r for r, c in rez['A']); cA = max(c for r, c in rez['A'])
    assert rA <= mT and cA <= n - m, (kod, rA, cA)
    assert max(c for r, c in rez['E']) <= mT and max(r for r, c in rez['E']) <= g, kod
    json.dump({'семейство': kod, 'n': n, 'k': k, 'g': g, 'подматрицы_R_C_с_1': {b: [list(x) for x in v] for b, v in rez.items()},
               'H': '[[A B T],[C D E]], (R,C) — координаты единиц, нумерация с 1',
               'откуда': doc}, open(os.path.join(VYH, 'ldpc_%s.json' % kod), 'w'), ensure_ascii=False)
    return {b: len(v) for b, v in rez.items()}, g


r2, g2 = proverka('IS-GPS-800J.pdf.txt', 103, 125, r'Table 6\.2-(\d+)\. LDPC Submatrix', range(2, 8), 'H_CNV2_SF2_%s', 'gps_cnav2_sf2',
                  1200, 600, 'IS-GPS-800J табл. 6.2-2..6.2-7, стр. 103-118 PDF')
r3, g3 = proverka('IS-GPS-800J.pdf.txt', 103, 125, r'Table 6\.2-(\d+)\. LDPC Submatrix', range(8, 14), 'H_CNV2_SF3_%s', 'gps_cnav2_sf3',
                  548, 274, 'IS-GPS-800J табл. 6.2-8..6.2-13, стр. 119-125 PDF')
itog['gps_cnav2_ldpc'] = 'табл. 6.2-2..6.2-13 IS-GPS-800J == PocketSDR H_CNV2_* (мультимножества координат); SF2 (1200,600) %s g=%d; SF3 (548,274) %s g=%d; T нижнетреугольная' % (r2, g2, r3, g3)
n2, h2 = proverka('NavIC_SPS_ICD_L1_final.pdf.txt', 88, 109, r'Table (\d+): LDPC Submatrix', range(30, 36), 'H_IRNV1_SF2_%s', 'navic_l1_sf2',
                  1200, 600, 'NavIC SPS ICD L1 табл. 30-35, стр. 88-102 PDF')
n3, h3 = proverka('NavIC_SPS_ICD_L1_final.pdf.txt', 88, 109, r'Table (\d+): LDPC Submatrix', range(36, 42), 'H_IRNV1_SF3_%s', 'navic_l1_sf3',
                  548, 274, 'NavIC SPS ICD L1 табл. 36-41, стр. 103-109 PDF')
itog['navic_l1_ldpc'] = 'табл. 30-41 NavIC L1 == PocketSDR H_IRNV1_*; SF2 %s g=%d; SF3 %s g=%d' % (n2, h2, n3, h3)
# совпадают ли матрицы NavIC L1 и GPS L1C?
g_sf2 = json.load(open(os.path.join(VYH, 'ldpc_gps_cnav2_sf2.json')))['подматрицы_R_C_с_1']
n_sf2 = json.load(open(os.path.join(VYH, 'ldpc_navic_l1_sf2.json')))['подматрицы_R_C_с_1']
itog['navic_vs_gps_sf2_odinakovy'] = g_sf2 == n_sf2
json.dump(itog, open(os.path.join(VYH, '_itog_nav.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))

# ---------------------------------------------------------------- CRC-24Q (IS-GPS-200N стр. 229)
def pmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1; b >>= 1
    return r


p229 = stranicy('IS-GPS-200N.pdf.txt')[229]
assert 'This code is called CRC-24Q' in p229 and '24 , 23 , 18 , 17 , 14 , 11 , 10 ,7,6,5,4,3,1,0' in p229
assert '3 5 7 8 9 11 12 13 17 23' in p229
g_i = sum(1 << i for i in (24, 23, 18, 17, 14, 11, 10, 7, 6, 5, 4, 3, 1, 0))
p_x = sum(1 << i for i in (0, 3, 5, 7, 8, 9, 11, 12, 13, 17, 23))  # одно из 11 «X» в тексте — аргумент p(X)
assert pmul(0b11, p_x) == g_i and g_i == 0x1864CFB
# сверка с реализацией PocketSDR sdr_rtk.crc24q и с проектом (reveng CRC-24/LTE-A — тот же многочлен 0x864CFB, init 0)
ps_crc24q = None  # sdr_rtk PocketSDR требует собранную librtk.so — не используем


def crc_bits(bity, poly, w, init=0):
    r = init
    for b in bity:
        fb = ((r >> (w - 1)) & 1) ^ b
        r = (r << 1) & ((1 << w) - 1)
        if fb:
            r ^= poly & ((1 << w) - 1)
    return r


def crc24q_bytes(bb):
    return crc_bits([(x >> (7 - i)) & 1 for x in bb for i in range(8)], g_i, 24)


assert crc24q_bytes(b'123456789') == 0xCDE703  # check reveng CRC-24/LTE-A (есть в crc_katalog.py проекта)
if ps_crc24q:
    import numpy as np
    assert ps_crc24q(np.frombuffer(b'123456789', dtype=np.uint8), 9) == 0xCDE703
itog['crc24q'] = 'g(X)=(1+X)p(X) из стр. 229 = 0x1864CFB; check("123456789")=0xCDE703 = reveng CRC-24/LTE-A' + (' = PocketSDR crc24q' if ps_crc24q else '')

# ---------------------------------------------------------------- Galileo: свёртка + перемежитель (прил. D), RS FEC2 (табл. 108), ISM CRC (табл. 109/110)
st_g = stranicy('Galileo_OS_SIS_ICD_v2.1.pdf.txt')


def bity(s):
    return [int(c) for c in s.replace(' ', '')]


def konv(u, g1=0o171, g2=0o133, inv2=True, msb_novyi=True):
    reg = [0] * 7
    out = []
    for b in u:
        reg = [b] + reg[:6]
        for gi, g in enumerate((g1, g2)):
            v = 0
            for i in range(7):
                tap = (g >> (6 - i)) & 1 if msb_novyi else (g >> i) & 1
                v ^= tap & reg[i]
            out.append(v ^ (1 if (inv2 and gi == 1) else 0))
    return out


def peremezh(s, n_col, k_row, po_strokam):
    # запись по столбцам (n столбцов по k), чтение по строкам — или наоборот
    if po_strokam:
        return [s[c * k_row + r] for r in range(k_row) for c in range(n_col)]
    return [s[r * n_col + c] for c in range(n_col) for r in range(k_row)]


konv_sogl = per_sogl = None
for D, (dlina_vh, n_col) in ((106, (244, 61)), (107, (120, 30))):
    t = st_g[D]
    ch = re.findall(r'((?:[01]{8} )+[01]{1,8})', t)
    vh, kod_, pere = (bity(x) for x in ch[:3])
    assert len(vh) == dlina_vh and len(kod_) == 2 * dlina_vh and len(pere) == 2 * dlina_vh, (D, len(vh), len(kod_), len(pere))
    ok = [m for m in (True, False) if konv(vh, msb_novyi=m) == kod_]
    assert ok, 'свёртка Galileo не совпала с прил. D'
    konv_sogl = ok[0]
    pk = [m for m in (True, False) if peremezh(kod_, n_col, 8, m) == pere]
    assert pk, 'перемежитель Galileo не совпал'
    per_sogl = pk[0]
itog['galileo_konv_peremezh'] = ('прил. D (стр. 106-107): K=7, G1=171o, G2=133o с инверсией 2-й ветви, порядок G1,G2; F/NAV 244→488, I/NAV 120→240 — '
                                  'выход совпал бит в бит (старший бит многочлена — текущий вход: %s); перемежитель %s — совпал для 61×8 и 30×8'
                                  % (konv_sogl, 'запись по столбцам, чтение по строкам' if per_sogl else 'запись по строкам, чтение по столбцам'))
# RS FEC2: GF(256) p(x)=x^8+x^4+x^3+x^2+1, g(x)=∏_{i=1}^{60}(x-α^i), табл. 108
t111 = st_g[111]
tab108 = dict((int(a), int(b)) for a, b in re.findall(r'(\d+) (\d+)', t111.split('Table 108')[1].split('Systematic Encoding')[0].split('j gj j gj j gj j gj')[1]))
assert sorted(tab108) == list(range(61)), sorted(tab108)
exp = [0] * 510; log = [0] * 256; x = 1
for i in range(255):
    exp[i] = x; log[x] = i; x <<= 1
    if x & 0x100:
        x ^= 0x11D
for i in range(255, 510):
    exp[i] = exp[i - 255]
g = [1]
for i in range(1, 61):
    r = exp[i]; ng = [0] * (len(g) + 1)
    for j, c in enumerate(g):
        ng[j] ^= exp[log[c] + log[r]] if c else 0
        ng[j + 1] ^= c
    g = ng
assert g == [tab108[j] for j in range(61)], 'g(x) Galileo FEC2'
assert log[tab108[0]] == 45 and log[tab108[1]] == 92 and log[tab108[2]] == 65 and log[tab108[59]] == 108
itog['galileo_rs_fec2'] = 'RS(118,58,61) укороченный из (255,195) над GF(256), p=0x11D: ∏(x-α^i), i=1..60 == табл. 108 (61 коэффициент), стр. 111'
# ISM CRC-32: G = X32+X31+X24+X22+X16+X14+X8+X7+X5+X3+X+1
t115 = st_g[115]
vh = bity('010110' + '000' + re.search(r'Sequence = ((?:[01]{4} )+[01]{3})', t115).group(1))
assert len(vh) == 96
ozh = int(re.search(r'ISM CRC = ((?:[01]{4} ){7}[01]{4})', t115).group(1).replace(' ', ''), 2)
G = sum(1 << i for i in (32, 31, 24, 22, 16, 14, 8, 7, 5, 3, 1, 0))
assert 'X32 + X31 + X24 + X22 + X16 + X14 + X8 + X7 + X5 + X3 + X+ 1' in st_g[90].replace('𝑋', 'X')
assert crc_bits(vh, G, 32) == ozh, (hex(crc_bits(vh, G, 32)), hex(ozh))
assert G & 0xFFFFFFFF == 0x814141AB
itog['galileo_ism_crc32'] = 'G_ISM (стр. 90) = 0x814141AB (= reveng CRC-32/AIXM, есть в проекте); пример табл. 109→110 (стр. 115): CRC = 0x%08X — совпало' % ozh
json.dump(itog, open(os.path.join(VYH, '_itog_nav.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps({k: itog[k] for k in ('crc24q', 'galileo_konv_peremezh', 'galileo_rs_fec2', 'galileo_ism_crc32')}, ensure_ascii=False, indent=1))

# ---------------------------------------------------------------- ГЛОНАСС: код Хэмминга (табл. 4.13, стр. 42-43), метка времени (стр. 18)
st_r = stranicy('ICD_GLONASS_5.1_2008_en.pdf.txt')
t = st_r[42].split('C1 = ')[1] + ' ' + st_r[43].split(' 5 GLONASS SPACE SEGMENT')[0]


def razv(s):
    out = []
    for ch in s.replace(' ', '').rstrip('.').split(','):
        if '-' in ch:
            a, b = ch.split('-'); out += list(range(int(a), int(b) + 1))
        elif ch:
            out.append(int(ch))
    return out


nab = {}
for C, perem in (('1', 'i'), ('2', 'j'), ('3', 'k'), ('4', 'l'), ('5', 'm')):
    m = re.search(r'%s = ((?:\d+(?:-\d+)?,? ?)+)' % perem, t)
    nab[int(C)] = razv(m.group(1))
nab[6] = list(range(35, 66)); nab[7] = list(range(66, 86))  # C6: n=35..65, C7: p=66..85 (стр. 43)
assert 'n=35 p=66' in t and '65 85 C6' in t
gs = open(os.path.join(REPOS, 'gnss-sdr', 'src', 'core', 'system_parameters', 'GLONASS_L1_L2_CA.h')).read()
for C, imya in zip(range(1, 8), 'IJKLMNP'):
    v = [int(x) for x in re.search(r'GLONASS_GNAV_CRC_%s_INDEX\{(.*?)\}' % imya, gs).group(1).split(',')]
    assert v == nab[C], (C, imya)
# свойство: каждый информационный бит b9..b85 имеет ненулевой, различный синдром (C1..C7), и icor = синдром + 8 - K указывает на него
sind = {}
for b in range(9, 86):
    s = sum(1 << (C - 1) for C in range(1, 8) if b in nab[C])
    sind[b] = s
assert len(set(sind.values())) == 77 and 0 not in sind.values()
for b, s in sind.items():
    if bin(s).count('1') >= 2:
        K = s.bit_length()
        assert s + 8 - K == b, (b, s, K)
itog['glonass_hamming'] = 'табл. 4.13 (стр. 42-43) == gnss-sdr GLONASS_GNAV_CRC_{I..P}_INDEX; 77 бит данных имеют различные ненулевые синдромы, правило icor = C7..C1 + 8 - K указывает ровно на ошибочный бит (для всех синдромов веса ≥2)'
# метка времени: g(x)=1+x^3+x^5, 30 бит 111110001101110101000010010110
p18 = st_r[18]
assert 'g(x) = 1 + x3 + x5, or may be shown as 111110001101110101000010010110' in p18
mv = [int(c) for c in '111110001101110101000010010110']
ok = False
for obr in (False, True):
    r = [1] * 5; out = []
    for _ in range(30):
        out.append(r[-1])
        fb = r[4] ^ r[2] if not obr else r[4] ^ r[1]
        r = [fb] + r[:4]
    ok = ok or out == mv
assert ok, 'метка времени ГЛОНАСС не порождена 1+x^3+x^5'
gpre = [int(x) for x in re.search(r'#define GLONASS_GNAV_PREAMBLE \\\s*\{\s*\\\s*(.*?)\}', gs, re.S).group(1).replace('\\', '').split(',')]
assert gpre == mv
itog['glonass_metka'] = 'метка времени = 30 бит m-последовательности 1+x^3+x^5 (регистр из единиц) — порождена; == gnss-sdr GLONASS_GNAV_PREAMBLE'
# ---------------------------------------------------------------- BeiDou B1I: BCH(15,11) g=X^4+X+1, преамбула, NH
st_b = stranicy('BDS-SIS-ICD-B1I-2.0_unb.pdf.txt')
assert 'The generator polynomial is g(X)=X4+X+1' in st_b[16] and '“11100010010”' in st_b[27]
assert '(0, 0, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 1, 0)' in st_b[19]
bd = open(os.path.join(REPOS, 'gnss-sdr', 'src', 'core', 'system_parameters', 'Beidou_DNAV.h')).read()
assert 'BEIDOU_DNAV_PREAMBLE[12] = "11100010010"' in bd
# x^4+x+1 примитивен → синдромы 15 позиций различны (код Хэмминга, t=1)
sy = set()
for i in range(15):
    r = 1 << i
    for j in range(14, 3, -1):
        if r >> j & 1:
            r ^= 0b10011 << (j - 4)
    sy.add(r)
assert len(sy) == 15 and 0 not in sy
itog['beidou_b1i'] = 'BCH(15,11) g=X^4+X+1 (стр. 16): 15 различных ненулевых синдромов → исправляет 1 ошибку; преамбула 11100010010 (стр. 27) == gnss-sdr; NH-код 20 бит (стр. 19)'
# Galileo синхрошаблоны == gnss-sdr
assert 'pattern is 0101100000' in st_g[50] and 'pattern is: 101101110000' in st_g[43]
assert 'GALILEO_INAV_PREAMBLE[11] = "0101100000"' in open(os.path.join(REPOS, 'gnss-sdr', 'src', 'core', 'system_parameters', 'Galileo_INAV.h')).read()
assert 'GALILEO_FNAV_PREAMBLE[13] = "101101110000"' in open(os.path.join(REPOS, 'gnss-sdr', 'src', 'core', 'system_parameters', 'Galileo_E5a.h')).read()
itog['galileo_sinhro'] = 'I/NAV 0101100000 (стр. 50), F/NAV 101101110000 (стр. 43) == gnss-sdr'
json.dump(itog, open(os.path.join(VYH, '_itog_nav.json'), 'w'), ensure_ascii=False, indent=1)
for k in ('glonass_hamming', 'glonass_metka', 'beidou_b1i', 'galileo_sinhro'):
    print(k, itog[k])
