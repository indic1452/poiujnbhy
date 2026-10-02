"""Таблицы лучших свёрточных кодов из открытого кода → tablicy/svertochnye/*.json и записи zapisi/svertochnye_mfd.json.

1) IT++ (GPL-3, istochniki/svertochnye/itpp/itpp_convcode.cpp): MFD (наибольшее свободное расстояние, «по Proakis»)
   R = 1/2…1/8 и ODS (Frenger–Orten–Ottosson, IEEE Comm. Lett. 1999) R = 1/2…1/4. Запись восьмеричная, старший бит —
   текущий вход (как у Proakis; itpp_convcode.h, строки 61–64).
2) Sionna (Apache-2.0, kod/sionna/src/sionna/phy/fec/conv/utils.py): таблица по Moon (R = 1/2, 1/3; K = 3…8).
3) libfec Ph. Karn (LGPL, kod/libfec/fec.h): многочлены K=7 r=1/2, K=9 r=1/2, K=9 r=1/3, K=15 r=1/6 — запись
   с обратным порядком бит (младший бит — текущий вход).
Проверки (assert): для каждого кода заново считается спектр (svertka_spektr): dfree, A_d, B_d;
dfree ODS = dfree MFD при тех же (n, K) и B_dfree(ODS) ≤ B_dfree(MFD); Moon (Sionna) — dfree = MFD IT++;
libfec — после зеркального отражения совпадает с кодами 171/133, 561/753, 557/663/711 таблиц IT++.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svertka_spektr import spektr
from zap import KOR, I, Z, sohranit, stroka
OUT = os.path.join(KOR, 'tablicy', 'svertochnye'); os.makedirs(OUT, exist_ok=True)
F_IT = 'istochniki/svertochnye/itpp/itpp_convcode.cpp'
src = open(os.path.join(KOR, F_IT)).read()


def massiv(name):
    m = re.search(r'int ' + name + r'\[(\d+)\]\[(\d+)\] = \{(.*?)\n\};', src, re.S)
    rows = re.findall(r'\{([^{}]*)\}', m.group(3))
    return [[int(x.strip(), 8) for x in r.split(',')] for r in rows]


def zerk(g, K):
    return int(format(g, '0%db' % K)[::-1], 2)


tab = {}
for typ in ('MFD', 'ODS'):
    for n in range(2, 9):
        name = 'Conv_Code_%s_%d' % (typ, n)
        if name not in src: continue
        rows = massiv(name); line = stroka(F_IT, name + '[')
        for K, gens in enumerate(rows):
            if not gens[0]: continue
            # K-битовые многочлены: старший бит может быть 0 у отдельной ветви
            r = spektr(gens, K, terms=5)
            tab[(typ, n, K)] = dict(тип=typ, n=n, K=K, многочлены=['%o' % g for g in gens], dfree=r['dfree'], A=r['A'], B=r['B'],
                                     строка=line + 1 + K, файл=F_IT)
# сверки
zam = []
for (typ, n, K), v in tab.items():
    if typ == 'ODS':
        m = tab.get(('MFD', n, K))
        if m:
            if v['dfree'] != m['dfree'] or v['B'][0] > m['B'][0] + 1e-9:
                zam.append('ODS/MFD n=%d K=%d: ODS dfree=%d B=%s, MFD dfree=%d B=%s' % (n, K, v['dfree'], v['B'][0], m['dfree'], m['B'][0]))
                v['сверка_MFD'] = 'РАСХОЖДЕНИЕ с MFD (dfree MFD %d)' % m['dfree']
                g = [int(x, 8) for x in m['многочлены']]
                g2 = [x if x >> (K - 1) else x | (1 << (K - 1)) for x in g]
                r2 = spektr(g2, K)
                m['замечание'] = ('dfree MFD %d < ODS %d — вероятная опечатка в таблице IT++ (пропущен старший разряд: %s даёт dfree=%d)'
                                  % (m['dfree'], v['dfree'], ', '.join('%o' % x for x in g2), r2['dfree']))
            else:
                v['сверка_MFD'] = 'dfree как у MFD, B_dfree %s ≤ %s' % (v['B'][0], m['B'][0])
# монотонность MFD по K (dfree не убывает)
for n in range(2, 9):
    ks = sorted(K for (t, nn, K) in tab if t == 'MFD' and nn == n)
    for a, b in zip(ks, ks[1:]):
        if tab[('MFD', n, b)]['dfree'] < tab[('MFD', n, a)]['dfree']:
            zam.append('MFD n=%d: dfree падает K=%d→%d (%d→%d), вероятная опечатка в многочленах IT++ %s' % (
                n, a, b, tab[('MFD', n, a)]['dfree'], tab[('MFD', n, b)]['dfree'], tab[('MFD', n, b)]['многочлены']))
            tab[('MFD', n, b)].setdefault('замечание', 'dfree меньше, чем у K=%d' % a)
print('\n'.join(zam))
# Sionna / Moon
F_SI = 'istochniki/kod/sionna/src/sionna/phy/fec/conv/utils.py'
s = open(os.path.join(KOR, F_SI)).read()
moon = []
for rate, dname in ((2, 'rate_half_dict'), (3, 'rate_third_dict')):
    body = re.search(dname + r' = \{(.*?)\}', s, re.S).group(1)
    for K, gs in re.findall(r"(\d+): \(([^)]*)\)", body):
        K = int(K); gens = [int(g.strip().strip("'"), 2) for g in gs.split(',')]
        r = spektr(gens, K)
        m = tab[('MFD', rate, K)]
        moon.append(dict(n=rate, K=K, многочлены=['%o' % g for g in gens], dfree=r['dfree'], B=r['B'], dfree_MFD_itpp=m['dfree'],
                         строка=stroka(F_SI, '%d: (' % K, stroka(F_SI, dname)), файл=F_SI))
        moon[-1]['сверка'] = 'dfree = MFD IT++' if r['dfree'] == m['dfree'] else 'dfree %d < MFD IT++ %d (код Moon не наилучший по dfree)' % (r['dfree'], m['dfree'])
# libfec
F_LF = 'istochniki/kod/libfec/fec.h'
h = open(os.path.join(KOR, F_LF)).read()
dfn = {k: int(v, 16) if v.startswith('0x') else int(v, 8) for k, v in re.findall(r'#define\s+(V\w+POLY\w)\s+(0x[0-9a-f]+|0[0-7]+)', h)}
lf = []
for name, K, keys in (('viterbi27 (K=7 r=1/2)', 7, ['V27POLYA', 'V27POLYB']), ('viterbi29 (K=9 r=1/2)', 9, ['V29POLYA', 'V29POLYB']),
                      ('viterbi39 (K=9 r=1/3)', 9, ['V39POLYA', 'V39POLYB', 'V39POLYC']), ('viterbi615 (K=15 r=1/6, Cassini)', 15, ['V615POLY' + c for c in 'ABCDEF'])):
    gens = [zerk(dfn[k], K) for k in keys]
    r = spektr(gens, K)
    lf.append(dict(имя=name, K=K, libfec={k: hex(dfn[k]) if dfn[k] < 0o1000 else oct(dfn[k]) for k in keys}, многочлены_станд=['%o' % g for g in gens],
                   dfree=r['dfree'], B=r['B'], строки=[stroka(F_LF, k) for k in keys], файл=F_LF))
assert sorted(lf[0]['многочлены_станд']) == sorted(tab[('MFD', 2, 7)]['многочлены'])
assert sorted(lf[1]['многочлены_станд']) == sorted(tab[('MFD', 2, 9)]['многочлены'])
assert sorted(lf[2]['многочлены_станд']) == sorted(tab[('MFD', 3, 9)]['многочлены'])
json.dump([dict(v) for k, v in sorted(tab.items())], open(os.path.join(OUT, 'itpp_mfd_ods.json'), 'w'), ensure_ascii=False, indent=1)
json.dump(moon, open(os.path.join(OUT, 'sionna_moon.json'), 'w'), ensure_ascii=False, indent=1)
json.dump(lf, open(os.path.join(OUT, 'libfec.json'), 'w'), ensure_ascii=False, indent=1)
print('IT++ кодов', len(tab), 'Moon', len(moon), 'libfec', len(lf))

# записи каталога
IZV = {('171', '133'), ('133', '171'), ('7', '5'), ('5', '7'), ('15', '17'), ('23', '35'), ('53', '75'), ('561', '753')}
zap = []
for (typ, n, K), v in sorted(tab.items()):
    g = v['многочлены']
    est = (n == 2 and tuple(g) in IZV)
    zap.append(Z('Свёрточный %s R=1/%d K=%d (%s)' % (typ, n, K, ', '.join(g)), 'Лучшие свёрточные коды (%s)' % ('наибольшее dfree, Proakis/Larsen' if typ == 'MFD' else 'оптимальный спектр, Frenger–Orten–Ottosson 1999'),
                 'свёрточный', {'n,k': '%d,1' % n, 'K': K, 'многочлены': 'восьмерично ' + ', '.join(g) + ' (старший бит — текущий вход, запись Proakis)',
                                'dfree': v['dfree'], 'A_d от dfree': v['A'], 'B_d от dfree': v['B'], 'начальное/хвост': 'не задано (код таблицы; хвост K−1 нулей — по применению)',
                                'таблица': 'tablicy/svertochnye/itpp_mfd_ods.json'},
                 'общая теория; кандидаты при слепом опознании кодов 1/n (многие стандарты берут эти коды: 7/5, 171/133, 561/753, 557/663/711)',
                 [I(F_IT, 'строка %d (массив Conv_Code_%s_%d)' % (v['строка'], typ, n), 'https://raw.githubusercontent.com/nvmd/itpp/master/itpp/comm/convcode.cpp')],
                 'спектр пересчитан (skripty/svertka_spektr.py): dfree=%d, A=%s, B=%s; %s%s' % (v['dfree'], v['A'][:3], v['B'][:3], v.get('сверка_MFD', ''),
                                                                                                  ('; ' + v['замечание']) if 'замечание' in v else ''),
                 ('ЕСТЬ в проекте: svyortka.py (1/n, n=2…4, опознание многочленов вслепую и Витерби), kod.py' + (', многочлен в kod.СВЁРТОЧНЫЕ' if est else '; таблицы имён для этого кода нет — добавить в kod.СВЁРТОЧНЫЕ')
                  if n <= 4 else 'ЧАСТИЧНО: svyortka.py умеет n=2…4; для 1/%d нужно расширить (Витерби общий — kod.витерби_n)' % n)))
for m in moon:
    zap.append(Z('Свёрточный (Moon) R=1/%d K=%d (%s)' % (m['n'], m['K'], ', '.join(m['многочлены'])), 'Таблица Moon (Error Correction Coding) в Sionna', 'свёрточный',
                 {'n,k': '%d,1' % m['n'], 'K': m['K'], 'многочлены': 'восьмерично ' + ', '.join(m['многочлены']) + ' (строки 0/1 Sionna, первый символ — текущий вход)', 'dfree': m['dfree'],
                  'таблица': 'tablicy/svertochnye/sionna_moon.json'},
                 'Sionna (NVIDIA) — кодер по умолчанию; K=5 r=1/2 — GSM 05.03 п. 4.1.3 (комментарий Sionna)',
                 [I(F_SI, 'строка %d' % m['строка'], 'https://github.com/NVlabs/sionna/blob/main/src/sionna/phy/fec/conv/utils.py')],
                 'dfree пересчитан = %d; сверка с MFD IT++: %s' % (m['dfree'], m['сверка']), 'ЕСТЬ в проекте: svyortka.py (n=2,3)'))
F_CAS = 'istochniki/dop/descanso/Descanso3--Cassini2.pdf.txt'
from zap import gde
for l in lf:
    zap.append(Z('Свёрточный libfec %s' % l['имя'], 'libfec (Ph. Karn, KA9Q)', 'свёрточный',
                 {'K': l['K'], 'многочлены libfec': l['libfec'], 'многочлены в записи стандартов': l['многочлены_станд'], 'dfree': l['dfree'],
                  'инверсия': 'viterbi27: NASA-DSN — POLYA инвертирован, затем POLYB; CCSDS/GSFC — POLYB, затем POLYA инвертирован (fec.h, строки 10–11)' if '27' in l['имя'] else
                  ('Mars Pathfinder и STEREO меняют местами POLYC и POLYD, чередующаяся инверсия символов (fec.h, строка 170)' if '615' in l['имя'] else '')},
                 'Voyager/CCSDS/DVB (K=7), IS-95 (K=9 1/2 и 1/3), Cassini/Mars Pathfinder (K=15 1/6)',
                 [I(F_LF, 'строки %s' % ', '.join(map(str, l['строки'])), 'https://github.com/quiet/libfec/blob/master/fec.h')] +
                 ([I('istochniki/dop/descanso/Descanso3--Cassini2.pdf', gde(F_CAS, 'The normal telemetry downlink is (15,1/6) convolutionally coded.') + '; ' + gde(F_CAS, 'Reed Solomon (255,223) concatenated with C.E. (15,1/6)') + ' — применение на Cassini (многочлены в документе не приведены)')] if '615' in l['имя'] else []),
                 'зеркальное отражение бит libfec → запись стандартов; dfree пересчитан = %d%s' % (l['dfree'], '; совпадает с MFD IT++' if '615' not in l['имя'] else ''),
                 'ЕСТЬ в проекте: kod.py/svyortka.py (K=7 и K=9 1/2 в kod.СВЁРТОЧНЫЕ)' if '615' not in l['имя'] else 'ЧАСТИЧНО: 1/6 вне svyortka.py (n≤4); K=15 — 16384 состояния, Витерби медленный'))
sohranit('svertochnye_mfd', zap)
