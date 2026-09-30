"""ATSC 3.0 LDPC (A/322:2026-04): 24 кода (N = 64800 и 16200, скорости 2/15…13/15).

Числа таблиц адресов — приложение A (табл. A.1.1–A.1.12, A.2.1–A.2.12, стр. PDF 150–166), из текста
PDF; разбиение на строки (по группам из 360 бит) — по gr-atsc3 (lib/ldpc_bb_impl.cc): в PDF строки
таблиц свёрстаны сплошным текстом; числа PDF и gr-atsc3 совпали все (sverka_atsc.py). Тип кода (A/B) —
табл. 6.4; M1, M2, Q1, Q2 — табл. 6.5, 6.6; Qldpc — табл. 6.7 (стр. PDF 40–42), из текста PDF.
Пишет src/reportgen/potok/data/ldpc_atsc.json. Запуск из корня репозитория.
"""
import json
import os
import re
import sys

ZDES = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ZDES)
from sverka_atsc import GR, K0, tablicy_gr, tablicy_pdf  # noqa: E402

KOREN = os.path.normpath(os.path.join(K0, '..', '..'))
T = open(os.path.join(K0, 'standarty', 'ATSC_A322-2026-04_Physical_Layer.pdf.txt'), encoding='utf-8').read()


def chisla_mezhdu(nachalo, konec):
    i = T.index(nachalo, T.index('6.1.3.1 Type A LDPC Encoding \nType A'))
    return [x for x in re.findall(r'\d+/15|\d+', T[i + len(nachalo):T.index(konec, i)])]


# Табл. 6.4: тип для 64800 и 16200.
kus = T[T.index('Lengths \nCode  \nRate'):T.index('6.1.3.1 Type A LDPC Encoding \nType A')]
TIP = {}
for r, a, b in re.findall(r'(\d+)/15 \n([AB]) \n([AB])', kus):
    TIP[(64800, int(r))], TIP[(16200, int(r))] = a, b
assert len(TIP) == 24
PARAM = {}
for N, zag, kon in ((64800, 'Table 6.5 Coding Parameters for Type A: Ninner = 64800', 'Table 6.6'),
                    (16200, 'Table 6.6 Coding Parameters for Type A: Ninner = 16200', '6.1.3.2')):
    ch = chisla_mezhdu(zag, kon)
    ch = ch[ch.index('2/15'):] if '2/15' in ch else ch
    for j in range(0, len(ch), 5):
        r, M1, M2, Q1, Q2 = int(ch[j].split('/')[0]), *(int(x) for x in ch[j + 1:j + 5])
        assert Q1 * 360 == M1 and Q2 * 360 == M2 and TIP[(N, r)] == 'A'
        PARAM[(N, r)] = {'M1': M1, 'M2': M2}
ch = chisla_mezhdu('Table 6.7 Coding Parameters for Type B', '=====PAGE')
ch = ch[ch.index('6/15'):]
j = 0
while j < len(ch):
    r = int(ch[j].split('/')[0])
    if r == 7:                                    # 7/15: для 64800 — N/A (там тип A)
        PARAM[(16200, 7)] = {'Q': int(ch[j + 1])}
        j += 2
        continue
    PARAM[(64800, r)] = {'Q': int(ch[j + 1])}
    PARAM[(16200, r)] = {'Q': int(ch[j + 2])}
    j += 3
assert len(PARAM) == 24
P, stranicy = tablicy_pdf()
G = tablicy_gr()
t = open(GR).read()
kody = {}
for (N, r), p in sorted(PARAM.items()):
    k = N * r // 15
    assert P[(N, r)] == G[(N, r)]
    m = re.search(r'ldpc_tab_%d_15%s\[(\d+)\]\[(\d+)\] = \{(.*?)\n    \};' % (r, 'N' if N == 64800 else 'S'), t, re.S)
    ryady, mesto = [], 0
    for s in re.findall(r'\{([^{}]*)\}', m.group(3)):
        v = [int(x) for x in s.split(',') if x.strip()]
        ryady.append(P[(N, r)][mesto:mesto + v[0]])       # числа — из PDF, границы строк — по gr-atsc3
        assert ryady[-1] == v[1:1 + v[0]]
        mesto += v[0]
    assert mesto == len(P[(N, r)])
    if TIP[(N, r)] == 'A':
        assert len(ryady) == (k + p['M1']) // 360, (N, r, len(ryady))
        assert N - k == p['M1'] + p['M2']
        # строки групп чётности части 1 — только в часть 2 (x ≥ M1)
        assert all(x >= p['M1'] for ryad in ryady[k // 360:] for x in ryad)
    else:
        assert len(ryady) == k // 360 and p['Q'] * 360 == N - k
    kody[f'atsc3-{N}-{k}'] = {'семейство': 'ATSC 3.0', 'n': N, 'k': k, 'скорость': f'{r}/15', 'тип': TIP[(N, r)],
                              **p, 'строки': ryady,
                              'откуда': f'ATSC A/322:2026-04, прил. A, стр. PDF {stranicy[(N, r)]} (тип {TIP[(N, r)]}, '
                                        'табл. 6.4–6.7); сверено с gr-atsc3'}
    print(f'ATSC 3.0 N={N} {r}/15 тип {TIP[(N, r)]}: {len(ryady)} строк, {p}')
put = os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_atsc.json')
json.dump(kody, open(put, 'w', encoding='utf-8'), ensure_ascii=False)
print('ВСЁ СОВПАЛО; записано', put)
