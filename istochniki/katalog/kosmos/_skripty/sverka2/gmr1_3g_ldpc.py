"""GMR-1 3G (ETSI TS 101 376-5-3 V3.3.1) LDPC: табл. 4.9–4.12 (k, n, XS, XR, XP, M, q) и прил. A табл. A.1–A.18
(адреса накопителей чётности) → tablicy/gmr1/gmr1_3g_ldpc.json.
Проверки (assert): число строк таблицы = k/M; q = (n−k)/M; все адреса < n−k; нет кратных рёбер при сдвигах t·q
(правило DVB-S2: бит t-й позиции группы → адрес (x + t·q) mod (n−k), чётности — накопитель);
эффективная скорость (k−XS)/(n−XS−XP+XR) совпадает со столбцом eff R табл. 4.9 и 4.11 для всех 18 кодов."""
import json, os, random, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pokaz import KOR

F = os.path.join(KOR, 'istochniki/gmr1/ts_1013760503v030301p.pdf.txt')
T = open(F, encoding='utf8', errors='replace').read()
lines = T.split('\n')


def chisla(s):
    return int(s.replace(' ', ''))


def blok(nachalo, dlina=140):
    i = max(k for k, l in enumerate(lines) if l.startswith(nachalo))
    return ' '.join(' '.join(lines[i:i + dlina]).split())


# ------------------------------------------------ табл. 4.9 и 4.11: код, модуляция, k, n, XS, XR, XP, eff R
NUM = r'(\d(?: ?\d{3})*|\d+)'
PAT = re.compile(r'(2/3 C1|2/3 C2|3/4|4/5 C1|4/5 C2|4/5 C3|9/10 C1|9/10 C2|1/2) (QPSK|16APSK|32APSK) ' + r'(\d{1,2} \d{3}|\d{3,4}) (\d{1,2} \d{3}|\d{3,4}) (\d+) (\d+) (0 or \d+|\d+) ([\d/,]+)')


def razmery(tabl, konec):
    b = blok(tabl)
    b = b[:b.index(konec)]
    r = PAT.findall(b)
    assert len(r) == 9, (tabl, len(r), r)
    return {c: {'модуляция': m, 'k': chisla(k), 'n': chisla(n), 'XS': int(xs), 'XR': int(xr), 'XP': xp, 'effR': e} for c, m, k, n, xs, xr, xp, e in r}


R12 = razmery('Table 4.9:', 'NOTE 1')
R3 = razmery('Table 4.11:', 'Table 4.12')


def mq(tabl, konec):
    b = blok(tabl, 60)
    b = b[b.index('Code M q') + 8:b.index(konec)]
    r = re.findall(r'(2/3 C1|2/3 C2|3/4|4/5 C1|4/5 C2|4/5 C3|9/10 C1|9/10 C2|1/2) (\d+) (\d+)', b)
    assert len(r) == 9, r
    return {c: (int(M), int(q)) for c, M, q in r}


MQ12 = mq('Table 4.10:', 'Parameters of the PNB2(5,3)')
MQ3 = mq('Table 4.12:', '4.10.3')

# ------------------------------------------------ прил. A: адреса
nachala = [k for k, l in enumerate(lines) if re.match(r'Table A\.\d+: Address of Parity Bit Accumulators', l)]
assert len(nachala) == 18
nachala.append(next(k for k in range(nachala[-1] + 1, len(lines)) if lines[k].startswith('Annex B') or lines[k].startswith('History')))
ADR = {}
for j in range(18):
    zag = lines[nachala[j]]
    m = re.search(r'\((.+?)( PNB2\(5,3\))?\) *$', zag)
    kod, pnb = m.group(1), ('PNB2(5,3)' if m.group(2) else 'PNB2(5,12)')
    stroki, v_kolont = [], False
    for l in lines[nachala[j] + 1:nachala[j + 1]]:
        s = l.strip()
        if s.startswith('=====PAGE'):
            v_kolont = True  # далее колонтитул: ETSI / ETSI TS … / номер страницы / GMR-1 3G 45.003
            continue
        if v_kolont:
            if s.startswith('GMR-1 3G'):
                v_kolont = False
            continue
        if re.fullmatch(r'\d+( \d+)+', s):
            stroki.append([int(x) for x in s.split()])
        elif s:
            raise AssertionError(('лишняя строка', zag, s))
    ADR[(pnb, kod)] = stroki


def H_i_kodirovanie(k, n, M, q, stroki, prob=3):
    m = n - k
    assert len(stroki) * M == k, ('строк', len(stroki), k / M)
    assert q * M == m
    assert all(0 <= a < m for r in stroki for a in r)
    for r in stroki:
        assert len(set(r)) == len(r), ('повтор адреса в строке', r)
        # после сдвигов t·q адреса строки не должны совпадать (иначе кратные рёбра)
        for t in range(M):
            assert len({(a + t * q) % m for a in r}) == len(r)
    stepeni = {}
    for r in stroki:
        stepeni[len(r)] = stepeni.get(len(r), 0) + M
    return stepeni


vyhod = {'источник': 'istochniki/gmr1/ts_1013760503v030301p.pdf (ETSI TS 101 376-5-3 V3.3.1, GMR-1 3G 45.003): п. 4.10, табл. 4.9–4.12, прил. A табл. A.1–A.18',
         'url': 'https://www.etsi.org/deliver/etsi_ts/101300_101399/1013760503/03.03.01_60/ts_1013760503v030301p.pdf',
         'правило': 'бит i_(g·M+t) накапливается в p[(x + t·q) mod (n−k)] для всех x строки g; затем p_j ^= p_(j−1) (как DVB-S2)', 'коды': []}
for pnb, R, MQ in (('PNB2(5,12)', R12, MQ12), ('PNB2(5,3)', R3, MQ3)):
    for kod, par in R.items():
        M, q = MQ[kod]
        st = H_i_kodirovanie(par['k'], par['n'], M, q, ADR[(pnb, kod)])
        xp = int(par['XP'].split()[0])  # для PNB2(5,12) «0 or N»: eff R в таблице дана при XP=0
        eff = (par['k'] - par['XS']) / (par['n'] - par['XS'] - xp + par['XR'])
        er = par['effR']
        er = float(er.replace(',', '.')) if '/' not in er else int(er.split('/')[0]) / int(er.split('/')[1])
        assert abs(eff - er) < 6e-4, (pnb, kod, eff, par)
        vyhod['коды'].append(dict(пакет=pnb, код=kod, M=M, q=q, степени_инф_бит=st, адреса=ADR[(pnb, kod)], **par))
os.makedirs(os.path.join(KOR, 'tablicy/gmr1'), exist_ok=True)
json.dump(vyhod, open(os.path.join(KOR, 'tablicy/gmr1/gmr1_3g_ldpc.json'), 'w'), ensure_ascii=False)
print('кодов:', len(vyhod['коды']))
for c in vyhod['коды']:
    print(c['пакет'], c['код'], c['модуляция'], 'k=%d n=%d M=%d q=%d' % (c['k'], c['n'], c['M'], c['q']), c['степени_инф_бит'])
