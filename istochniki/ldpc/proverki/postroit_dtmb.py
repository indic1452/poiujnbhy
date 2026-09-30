"""DTMB (GB 20600-2006) LDPC: три кода, n = 7488 передаваемых бит (+5 выколотых), k = 3048/4572/6096.

Источник один — dtmb-sdr (MIT): python/dtmb/data/dtmb_ldpc_rate{1,2,3}.alist; сам стандарт GB 20600
недоступен. Раскладка слова — core/cpp/src/fec.cpp (строки 535–559): в графе 7493 позиции, первые 5 не
передаются (LLR 0), проверочных — 35/23/11 блоков по 127, данные — после них. Сверка: ранг H = m
(k = n − m), веса блоков кратны 127 (квазициклическая структура). Пишет src/reportgen/potok/data/ldpc_dtmb.json.
"""
import json
import os
import sys

import numpy as np

ZDES = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ZDES)
from postroit_obshchee import alist_pary  # noqa: E402

K0 = os.path.normpath(os.path.join(ZDES, '..'))
KOREN = os.path.normpath(os.path.join(K0, '..', '..'))
sys.path.insert(0, os.path.join(KOREN, 'src'))
from reportgen.potok import gf2  # noqa: E402

kody = {}
for nomer, blokov, k in ((1, 35, 3048), (2, 23, 4572), (3, 11, 6096)):
    n, m, pary = alist_pary(os.path.join(K0, 'otkrytyj_kod', 'dtmb-sdr', 'python', 'dtmb', 'data', f'dtmb_ldpc_rate{nomer}.alist'))
    assert (n, m) == (7493, blokov * 127) and n - m == k
    H = np.zeros((m, n), dtype=np.uint8)
    for a, b in pary:
        H[a, b] = 1
    r = gf2.ранг(H)
    assert r == m, ('ранг', nomer, r)
    kody[f'dtmb-7488-{k}'] = {'семейство': 'DTMB (GB 20600)', 'n': n, 'k': k, 'скорость': f'{k}/7488',
                              'выколоты': '0-4', 'данные_с': m,
                              'строки': [sorted(b for a, b in pary if a == i) for i in range(m)],
                              'откуда': f'dtmb-sdr (MIT) dtmb_ldpc_rate{nomer}.alist; раскладка — core/cpp/src/fec.cpp; '
                                        'источник один (GB 20600 недоступен), ранг H проверен'}
    print(f'DTMB rate{nomer}: H {m}×{n}, ранг {r}, k = {k}')
json.dump(kody, open(os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_dtmb.json'), 'w', encoding='utf-8'),
          ensure_ascii=False)
print('ВСЁ СОВПАЛО')
