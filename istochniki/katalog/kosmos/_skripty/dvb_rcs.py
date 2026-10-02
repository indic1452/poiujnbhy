"""DVB-RCS (EN 301 790 V1.5.1) и DVB-RCS2 (EN 301 545-2 V1.5.1): дуобинарные турбокоды —
перемежители (табл. 9 / табл. A-1, A-2), таблицы циркуляции (табл. 10 / 7-13). Сверка с AFF3CT (MIT)."""
import json, os, re

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'dvb')
VYH = os.path.join(KOR, 'tablicy', 'dvb')
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
os.makedirs(VYH, exist_ok=True)


def txt(fn):
    return open(os.path.join(IST, fn), encoding='utf8').read()


def str_(T, n):
    return ' '.join(re.search(r'=====PAGE %d=====\n(.*?)(?======PAGE|\Z)' % n, T, re.S).group(1).split())


R1 = txt('en_301790v010501p.pdf.txt')
R2 = txt('en_30154502v010501p.pdf.txt')
AFF = open(os.path.join(REPOS, 'aff3ct', 'include', 'Tools', 'Interleaver', 'ARP', 'Interleaver_core_ARP_DVB_RCS1.hpp')).read()
AFF2 = open(os.path.join(REPOS, 'aff3ct', 'src', 'Tools', 'Interleaver', 'ARP', 'Interleaver_core_ARP_DVB_RCS2.cpp')).read()
ENC = open(os.path.join(REPOS, 'aff3ct', 'src', 'Module', 'Encoder', 'RSC_DB', 'Encoder_RSC_DB.cpp')).read()
itog = {}

# ---------------- RCS1: табл. 9 (стр. 31)
t9 = str_(R1, 31)
rows = re.findall(r'N = (\d+) \((\d+) bytes\) (\d+) \{(\d+),(\d+),(\d+)\}', t9)
assert len(rows) == 12
par1 = {}
for N, B, P0, P1, P2, P3 in rows:
    N, B, P0, P1, P2, P3 = map(int, (N, B, P0, P1, P2, P3))
    assert N == 4 * B
    par1[N] = (P0, P1, P2, P3)
aff_rows = [tuple(map(int, x)) for x in re.findall(r'\{ *(\d+), *(\d+), *(\d+), *(\d+) *\}', AFF.split('parameters =')[1].split(';')[0])]
assert sorted(aff_rows) == sorted(par1.values()), 'параметры AFF3CT = табл. 9'


def pi_rcs1(N, P0, P1, P2, P3):
    out = []
    for j in range(N):
        P = [0, N // 2 + P1, P2, N // 2 + P3][j % 4]
        out.append((P0 * j + P + 1) % N)
    return out


perm1 = {}
for N, (P0, P1, P2, P3) in par1.items():
    p = pi_rcs1(N, P0, P1, P2, P3)
    assert sorted(p) == list(range(N)), N
    assert all((i % 2) != (j % 2) for j, i in enumerate(p)), 'правило чёт/нечет'
    perm1[N] = p
json.dump({'параметры_P0_P1_P2_P3': {str(k): v for k, v in par1.items()}, 'перестановки_Pi_j': {str(k): v for k, v in perm1.items()},
           'правило': 'j чётное — (A,B)→(B,A); i = P0·j + P + 1 mod N, P = 0, N/2+P1, P2, N/2+P3 по j mod 4'},
          open(os.path.join(VYH, 'rcs1_turbo_perem.json'), 'w'), ensure_ascii=False)
# табл. 10 (стр. 32)
t10 = str_(R1, 32)
c10 = []
for m in re.finditer(r'\b([1-6]) ((?:SC = \d ){7}SC = \d)', t10):
    c10.append([int(x) for x in re.findall(r'SC = (\d)', m.group(2))])
assert len(c10) == 6
aff_c1 = [list(map(int, re.findall(r'\d+', x))) for x in re.findall(r'\{ *(\d+(?:, *\d+){7}) *\}', ENC.split('circ_states = ')[1].split(';')[0])]
assert aff_c1 == c10, 'табл. 10 = AFF3CT circ_states RCS1'
itog['rcs1'] = {'N': sorted(par1), 'проверка': 'табл. 9: N=4·байт; 12 наборов P0..P3 = AFF3CT (MIT); все Π — перестановки с правилом чёт/нечет; табл. 10 (циркуляция, 6×8) = AFF3CT circ_states (assert)',
                'файл': 'tablicy/dvb/rcs1_turbo_perem.json'}

# ---------------- RCS2: табл. A-1 (стр. 240-243) и A-2 (стр. 244)
num = r'(\d{1,3}(?: \d{3})?)'
wf = []
TA1 = ' '.join(str_(R2, p) for p in range(240, 244))
pos = 0
for wid in range(1, 80):
    m = re.compile(r' %d (\d \d{3}|\d{3,4}) (\d \d{3}|\d{1,3}); (\d \d{3}|\d{2,3}) (QPSK|8-?PSK|16-?QAM|BPSK|16APSK) ?(\d/\d) (\S+) (\S+) (\S+) (\S+) (\S+) (\d+) (\d+) (\d+) (\d+) (\d+) ' % wid).search(TA1, pos)
    if not m:
        continue
    pos = m.end()
    g = m.groups()
    byt = int(g[1].replace(' ', ''))
    P, Q0, Q1, Q2, Q3 = map(int, g[10:15])
    wf.append({'id': wid, 'байт': byt, 'модуляция': g[3], 'скорость': g[4], 'P': P, 'Q': [Q0, Q1, Q2, Q3]})
ids = [w['id'] for w in wf]
assert ids == sorted(ids) and len(set(ids)) == len(ids), ids
t = str_(R2, 244)
wf2 = []
pos = 0
wid = 1
while True:
    m = re.compile(r' %d (\d \d{3}|\d{3}) (\d{1,2}) (\d{1,2} \d{3}|\d{3}) (\d{2,3}) (\d \d{3}|\d{3}) BPSK (\d+) (\d+) (\d+) (\d+) (\d{1,2} \d{3}|\d{1,3}) (\d+) (\d+) (\d+) (\d+) (\d+) ' % wid).search(t, pos)
    if not m:
        break
    g = m.groups()
    wf2.append({'id': wid, 'байт': int(g[3]), 'P': int(g[10]), 'Q': list(map(int, g[11:15]))})
    pos = m.end(); wid += 1
# сверка с AFF3CT RCS2 (размер N — пары)
aff2 = {}
for blk in re.findall(r'case (\d+):[^\n]*\n\s*p = (\d+);\s*q0 = (\d+);\s*q1 = (\d+);\s*q2 = (\d+);\s*q3 = (\d+);', AFF2):
    aff2[int(blk[0])] = tuple(map(int, blk[1:]))
nabor = {}
for w in wf + wf2:
    N = 4 * w['байт']
    key = (w['P'], *w['Q'])
    nabor.setdefault(N, set()).add(key)
sovp = 0
for N, keys in nabor.items():
    if N in aff2:
        assert aff2[N] in keys, (N, aff2[N], keys)
        sovp += 1


def pi_rcs2(N, P, Q0, Q1, Q2, Q3):
    out = []
    for j in range(N):
        Q = [0, 4 * Q1, 4 * Q0 * P + 4 * Q2, 4 * Q0 * P + 4 * Q3][j % 4]
        out.append((P * j + Q + 3) % N)
    return out


perm2 = {}
for N, keys in sorted(nabor.items()):
    for key in keys:
        p = pi_rcs2(N, *key)
        assert sorted(p) == list(range(N)), (N, key)
        perm2['%d:%s' % (N, ','.join(map(str, key)))] = p
t713 = str_(R2, 158)
body = t713.split('Last Encoder State')[1].split('7.3.5.1.3')[0]
nums = [int(x) for x in re.findall(r'\d+', body)]
assert nums[:16] == list(range(16))
c713 = []
rest = nums[16:]
for r in range(14):
    row = rest[r * 17:(r + 1) * 17]
    assert row[0] == r + 1
    c713.append(row[1:])
aff_c2 = [list(map(int, re.findall(r'\d+', x))) for x in re.findall(r'\{ *(\d+(?:, *\d+){15}) *\}', ENC.split('circ_states = ')[2].split(';')[0])]
assert aff_c2 == c713, 'табл. 7-13 = AFF3CT'
json.dump({'волны_линейные_A1': wf, 'волны_спред_A2': wf2, 'перестановки': perm2,
           'правило': 'j чётное — (A,B)→(B,A); Q(j)=0, 4Q1, 4Q0·P+4Q2, 4Q0·P+4Q3; i = (P·j + Q(j) + 3) mod N', 'циркуляция_7_13': c713},
          open(os.path.join(VYH, 'rcs2_turbo_perem.json'), 'w'), ensure_ascii=False)
itog['rcs2'] = {'волн_A1': len(wf), 'волн_A2': len(wf2), 'разных_N': len(nabor), 'сверено_с_AFF3CT_N': sovp,
                'проверка': 'табл. A-1/A-2 разобраны; все Π — перестановки; P,Q0..Q3 для %d размеров совпали с AFF3CT (MIT); табл. 7-13 (14×16) = AFF3CT circ_states (assert)' % sovp,
                'файл': 'tablicy/dvb/rcs2_turbo_perem.json'}
json.dump(itog, open(os.path.join(VYH, '_itog_dvb_rcs.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
