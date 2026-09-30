# Сверка таблиц режимов LDPC модемов по двум (и более) первоисточникам.
#   VersaFEC: патент US 2010/0162078 A1 (Comtech EF Data), табл. 1 абз. [0053]
#             и руководство MN-CDM625A rev.4, табл. B-9 (стр. 553) и M-1 (стр. 691).
#   VersaFEC-2: MN-CDM625A табл. B-10/B-11 (стр. 557–558) и M-3/M-4 (стр. 697–698).
#   Datum FlexLDPC: руководство M7 (копия sky-brokers и M7_Main-Manual_0-12 с datumsystems.com)
#             и White Paper «Unlocking the Potential of LDPC» (2013, rev. B), стр. 3 и 7.
#   DOCSIS 3.1: CM-SP-PHYv3.1-I15, размеры кодов и подъём L.
import re, os
from fractions import Fraction as F

ZDES = os.path.dirname(os.path.abspath(__file__))
K0 = os.path.dirname(ZDES)
chit = lambda *p: open(os.path.join(K0, *p), encoding='utf8', errors='ignore').read()


def stranica(tekst, nomer):
    a = tekst.find('=====PAGE %d=====' % nomer)
    b = tekst.find('=====PAGE %d=====' % (nomer + 1))
    assert a >= 0
    return tekst[a:b]


def splosh(s):
    return re.sub(r'\s+', ' ', s)


# ---------- VersaFEC ----------
pat = chit('patenty', 'US20100162078.txt')
t1 = pat[pat.find('TABLE-US-00002'):]
t1 = t1[:t1.find('\n')]
vf_pat = [(int(m), mod, r, blk) for m, mod, r, blk in
          re.findall(r'(\d+) (BPSK|QPSK|8-QAM|16-QAM) (0\.\d{3}) \d\.\d\d\s+(\d+(?:\.\d)?k)', t1)]
assert len(vf_pat) == 12, vf_pat
man = chit('comtech', 'MN-CDM625A_rev4_skybrokers.pdf.txt')
b9 = splosh(stranica(man, 553))
vf_b9 = [(int(m), mod, r, blk + 'k') for m, mod, r, blk in
         re.findall(r'(\d\d) (BPSK|QPSK|8-QAM|16-QAM) (0\.\d{3}) \d\.\d\d (\d+(?:\.\d)?) ', b9)]
m1 = splosh(stranica(man, 691))
vf_m1 = [(mod, r, blk) for mod, r, blk in
         re.findall(r'(BPSK|QPSK|8-QAM|16-QAM) (0\.\d{3}) \d\.\d\d (\d+(?:\.\d)?k) ', m1)]
assert [x[1:] for x in vf_pat] == [x[1:] for x in vf_b9], (vf_pat, vf_b9)
assert [x[1:] for x in vf_pat] == vf_m1
print('VersaFEC: 12 ModCod совпали в патенте (табл. 1), MN-CDM625A табл. B-9 и M-1:')
for m, mod, r, blk in vf_pat:
    bps = {'BPSK': 1, 'QPSK': 2, '8-QAM': 3, '16-QAM': 4}[mod]
    print(f'   {m:2d} {mod:6s} {r}  блок {blk:5s}  (блок/бит на символ ≈ {float(blk[:-1]) * 1000 / bps:.0f} символов)')

# расширения CCM и ULL (только руководство, стр. 555; даташит ds-cdm625A — второй источник)
ext = splosh(stranica(man, 555))
assert '8-QAM 0.576' in ext and '16-QAM 0.644' in ext
ull = re.findall(r'(BPSK|QPSK) (0\.\d{3}) \d\.\d\d \d\.\d dB (\d+) ', ext)
assert [(a, b) for a, b, _ in ull] == [('BPSK', '0.493'), ('QPSK', '0.493'), ('QPSK', '0.654'), ('QPSK', '0.734')], ull
ds = splosh(chit('comtech', 'ds-cdm625A.pdf.txt'))
for s in ('0.488', '0.533', '0.631', '0.706', '0.803', '0.576', '0.642', '0.711', '0.780',
          '0.644', '0.731', '0.829', '0.853', '0.493', '0.654', '0.734'):
    assert s in ds, ('нет в даташите', s)
print('VersaFEC CCM 0.576/0.644 и ULL 0.493/0.493/0.654/0.734: руководство стр. 555 = даташит ds-cdm625A')

# ---------- VersaFEC-2 ----------
def vf2(stranicy):
    t = splosh(' '.join(stranica(man, s) for s in stranicy))
    return [(int(m), mod, F(r), int(k)) for m, mod, r, _, k in
            re.findall(r'(\d\d) (BPSK|QPSK|8-ARY|16-ARY|32-ARY) (0\.\d{3}) (\d\.\d{3}) (\d+) ', t)]


dl_b, kor_b = vf2([557]), vf2([558])
dl_m, kor_m = vf2([697]), vf2([698])
assert len(dl_b) == 38 and len(kor_b) == 36, (len(dl_b), len(kor_b))
assert dl_b == dl_m and kor_b == kor_m, 'B-10/B-11 и M-3/M-4 различаются'
bps = {'BPSK': 1, 'QPSK': 2, '8-ARY': 3, '16-ARY': 4, '32-ARY': 5}
for (m, mod, r, Kd), (m2, mod2, r2, Kk) in zip(dl_b, kor_b):
    assert (m, mod, r) == (m2, mod2, r2)
    assert Kd == 6 * Kk, (m, Kd, Kk)                       # длинный блок = 6 коротких
    nom = F(Kk, 1600 * bps[mod])                           # номинальная скорость K/(1600·m)
    assert abs(float(r) / float(nom) - 0.9775) < 0.0015, (m, r, nom)
print('VersaFEC-2: 38 длинных и 36 коротких ModCod совпали в табл. B-10/B-11 и M-3/M-4; для всех общих 36:')
print('   K_длин = 6·K_кор; K_кор = 1600·m·r_ном; указанная скорость = r_ном·(0,9775±0,0015) — постоянная доля служебных символов [арифметика]')
print('   номинальные скорости:', sorted({str(F(Kk, 1600 * bps[mod])) for _, mod, _, Kk in kor_b}, key=lambda s: F(s)))

# ---------- Datum FlexLDPC ----------
m7a = splosh(chit('datum', 'M7_Operations_Manual_skybrokers.pdf.txt'))
m7b = splosh(chit('datum', 'M7_Main-Manual_0-12.pdf.txt'))
wp = chit('datum', 'Datum_FlexLDPC_White_Paper.pdf.txt')
blok = '0 = 256 Block, 1 = 512 Block, 2 = 1k Block, 3 = 2k Block, 4 = 4k Block, 5 = 8k Block, 6 = 16k Block'
skor = '0 = 1/2, 1 = 2/3, 2 = 3/4, 3 = 14/17, 4 = 7/8, 5 = 10/11, 6 = 16/17'
for t in (m7a, m7b):
    assert blok in t and skor in t
wp3 = splosh(stranica(wp, 3))
assert '7 code rates of 1/2, 2/3, 3/4, 14/17, 7/8, 10/11 & 16/17' in wp3
assert '7 block sizes of 256, 512, 1k, 2k, 4k, 8k & 16 k' in wp3
assert 'TrellisWare' in splosh(stranica(wp, 2))
# задержка в таблице стр. 7 растёт почти пропорционально размеру блока — блок задан в битах данных
wp7 = splosh(stranica(wp, 7))
zd = {int(b[:-1]) * 1024 if b.endswith('k') else int(b): float(ms) for b, ms in
      re.findall(r'LDPC, Rate 1/2 & (\d+k?) Block (\d+\.\d) ms', wp7)}
assert len(zd) == 7
print('Datum FlexLDPC: блоки и скорости совпали в двух руководствах M7 и White Paper;',
      'задержка 1/2 при 64 кбит/с, мс:', zd)
# 16/17: «Only 1 overhead bit is used for every 16 bits of real data» (стр. 9)
assert 'Only 1 overhead bit is used for every 16 bits of real data' in splosh(stranica(wp, 9))

# ---------- «Обычный» LDPC Comtech и Paradise: одинаковая задержка 395 мс ----------
assert re.search(r'Rate 3/4, 8-PSK, 8-QAM, 16-QAM 395', splosh(stranica(man, 552)))
par = splosh(chit('paradise', 'Quantum_Evolution_Handbook_3.0.17_skybrokers.pdf.txt'))
assert 'LDPC 16QAM ¾ at 64kbps has a latency of 395ms' in par
assert re.search(r'Traditional - 16QAM, LDPC, Rate 3/4', wp) and '395' in splosh(stranica(wp, 6))
assert 'blocks that are 16 kbits in length' in splosh(stranica(man, 553))
print('LDPC 3/4 16QAM при 64 кбит/с = 395 мс: Comtech (MN-CDM625A стр. 552), Paradise (Handbook стр. 238),',
      'Datum WP стр. 6 («Traditional LDPC»); у Comtech блок 16 кбит (стр. 553)')

# ---------- DOCSIS 3.1 ----------
d = splosh(chit('standarty', 'CableLabs_CM-SP-PHYv3.1-I15-180926_DOCSIS31_PHY.pdf.txt'))
kody = re.findall(r'\((\d+), (\d+)\) code, m=(\d+) rows x n=(\d+) columns, L=(\d+)', d)
assert len(kody) >= 3
for N, K, m, n, L in kody[:3]:
    N, K, m, n, L = map(int, (N, K, m, n, L))
    assert n * L == N and (n - m) * L == K, (N, K, m, n, L)
print('DOCSIS 3.1: коды', [(int(a), int(b), 'L=' + c) for a, b, _, _, c in kody[:3]], '— N = n·L, K = (n−m)·L')
print('ВСЁ СОВПАЛО')
