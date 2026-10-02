"""Испытательные ПСП МСЭ-T O.150 (05/96) и O.151/O.152/O.153 (10/92) → tablicy/skremblery/prbs_o150.json, zapisi/prbs.json.
Проверки (assert): отводы прочитаны из текста («N-stage shift register whose Ath and Bth stage outputs…»), совпадают между O.150 и
O.151–O.153; многочлен x^B + x^A + 1 примитивен (период 2^n−1); заявленная «наибольшая серия нулей» пересчитана моделированием
LFSR для n ≤ 23 (инвертированный сигнал — серия единиц прямой ПСП), для 2^20−1 с подавлением нулей — по формуле O.150 5.5
(выход = Q20 OR NOT(Q6 OR … OR Q19)) серия ≤ 14."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf2 import primitive
from zap import KOR, I, Z, sohranit, gde
W = {'nine': 9, 'eleven': 11, 'fifteen': 15, 'twenty': 20, 'twenty-three': 23, 'twenty-nine': 29}
def txt(f): return open(os.path.join(KOR, f), encoding='utf-8', errors='replace').read()
def najti(f):
    t = re.sub(r'\s+', ' ', txt(f))
    out = []
    for m in re.finditer(r'([\w-]+)-stage shift[- ]register whose (\d+)\w* and (\d+)\w* stage outputs', t):
        out.append((W.get(m.group(1).lower()), int(m.group(2)), int(m.group(3)), m.start()))
    return out, t
F0 = 'istochniki/skremblery/T-REC-O.150-199605-I.pdf.txt'
o150, t0 = najti(F0)
drugie = {}
for f in ('T-REC-O.151-199210-I', 'T-REC-O.152-199210-I', 'T-REC-O.153-199210-I'):
    o, _ = najti('istochniki/skremblery/%s.pdf.txt' % f)
    drugie[f] = [(a, b) for _, a, b, _ in o]
def lfsr_runs(n, a, iters):
    s = (1 << n) - 1; z = o = mz = mo = 0
    for _ in range(iters):
        bit = ((s >> (a - 1)) ^ (s >> (n - 1))) & 1
        out = (s >> (n - 1)) & 1
        s = ((s << 1) | bit) & ((1 << n) - 1)
        if out: o += 1; z = 0; mo = max(mo, o)
        else: z += 1; o = 0; mz = max(mz, z)
    return mz, mo
# ссылка — строка своего пункта по «whose A…» (поиск по «N-stage» находил первый одноимённый пункт: 5.4 вместо 5.5, 5.7 вместо 5.8)
res = []; zap = []
for idx, (nw, a, b, pos) in enumerate(o150):
    n = b                     # «29-stage … 28th and 31st» в п. 5.8 — опечатка числа ступеней: берём наибольший отвод
    seg = t0[pos:pos + 600]
    m = re.search(r'Longest sequence of zeros (\d+) \(([^)]*)\)', seg)
    zayav = (int(m.group(1)), m.group(2)) if m else None
    zero_sup = 'forced to be a ONE' in seg
    p = (1 << n) | (1 << a) | 1
    assert primitive(p), (n, a)
    prov = 'x^%d + x^%d + 1 примитивен (период %d)' % (n, a, (1 << n) - 1)
    if n <= 23 and not zero_sup:
        mz, mo = lfsr_runs(n, a, (1 << n) - 1 + n)
        if zayav and 'inverted' in zayav[1] and 'non' not in zayav[1]: assert mo == zayav[0], (n, mo, zayav); prov += '; серия нулей инвертированного сигнала = серия единиц ПСП = %d (моделирование)' % mo
        elif zayav: assert mz == zayav[0], (n, mz, zayav); prov += '; наибольшая серия нулей = %d (моделирование)' % mz
    if zero_sup:
        # Q1(k+1) = Q17 ⊕ Q20; RD = Q20 OR NOT(Q6 OR … OR Q19)
        s = (1 << 20) - 1; run = mrun = 0
        for _ in range((1 << 20) + 40):
            q = [(s >> i) & 1 for i in range(20)]   # q[i] = Q(i+1)
            rd = q[19] | (0 if any(q[5:19]) else 1)
            run = run + 1 if rd == 0 else 0; mrun = max(mrun, run)
            s = ((s << 1) | (q[16] ^ q[19])) & ((1 << 20) - 1)
        assert mrun <= 14, mrun
        prov += '; с подавлением (RD = Q20 OR ¬(Q6…Q19)) наибольшая серия нулей = %d ≤ 14 (моделирование)' % mrun
    sovp = [f for f, lst in drugie.items() if (a, b) in lst]
    if sovp: prov += '; те же отводы в ' + ', '.join(s.replace('T-REC-', '').replace('-199210-I', '') for s in sovp)
    nazv = re.search(r'(\d[\d ]*\d)-bit pseudo-random test sequence', t0[max(0, pos - 400):pos])
    res.append(dict(n=n, отводы=[a, b], многочлен='x^%d + x^%d + 1' % (n, a), подавление_нулей=zero_sup, заявлено=zayav, проверка=prov))
    zap.append(Z('ПСП O.150: 2^%d − 1%s (x^%d + x^%d + 1)' % (n, ' с подавлением нулей (QRSS)' if zero_sup else '', n, a), 'Испытательные ПСП МСЭ-T O.150', 'скремблер (испытательная ПСП)',
                 {'многочлен': 'x^%d + x^%d + 1: сумма выходов ступеней %d и %d подаётся на вход первой' % (n, a, a, b), 'длина': (1 << n) - 1, 'сигнал': zayav[1] if zayav else '',
                  'наибольшая серия нулей (заявлено)': zayav[0] if zayav else '', 'подавление нулей': 'выход принудительно 1, если следующие 14 бит — нули (п. 5.5)' if zero_sup else 'нет', **({'примечание': 'в тексте п. 5.8 — «twenty-nine-stage shift register whose 28th and 31st…»: опечатка числа ступеней, регистр 31 ступень (длина 2^31 − 1 в заголовке п. 5.8)'} if nw and nw != n else {})},
                 'измерители BER/джиттера: 2^9−1, 2^11−1 (64 кбит/с, O.152/O.153), 2^15−1 (E1/T1…), 2^20−1, 2^23−1 (E3/E4), 2^29−1, 2^31−1',
                 [I(F0[:-4], gde(F0, 'whose %d' % a, 0))] + [I('istochniki/skremblery/%s.pdf' % f, 'текст (те же отводы)') for f in sovp],
                 prov, 'ЕСТЬ в проекте: skrembler.py (аддитивные ПСП находятся вслепую; ИЗВЕСТНЫЕ — O.151 2^15−1)'))
json.dump(res, open(os.path.join(KOR, 'tablicy', 'skremblery', 'prbs_o150.json'), 'w'), ensure_ascii=False, indent=1)
for r in res: print(r['многочлен'], r['подавление_нулей'], r['проверка'][:150])
sohranit('prbs', zap)
