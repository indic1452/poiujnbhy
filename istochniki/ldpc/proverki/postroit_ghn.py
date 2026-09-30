"""ITU-T G.9960 (G.hn) LDPC: материнские коды 1/2, 2/3, 5/6 (компактные матрицы 12×24, 8×24, 4×24),
расширение b = N_M/24, сдвиг â = ⌊a·b/96⌋ (a ≥ 0), выкалывание 16/18 и 20/21 (табл. 7-18), все коды табл. 7-19.

Источник — ITU-T G.9960 (10/2009, prepublished), 7.1.3.2, стр. 44–47 PDF: матрицы — текстом (стр. 46–47),
правило сдвига — стр. 46, шаблоны выкалывания — табл. 7-18 (стр. 48: pp16(1) = [1…1 0], pp1152(144) =
[1×240 0×48 1×720 0×96 1×48], pp5184(648) = [1×216 0×216 1×4320 0×432] — длины серий видны под скобками
таблицы; в тексте PDF они же, в обратном порядке, — проверяется), параметры — табл. 7-19 (стр. 49).
Второй источник матриц: материнские коды G.hn совпадают с базовыми матрицами IEEE 802.16e (WiMAX) 1/2, 2/3B и
5/6 из yaldpc (lib/IEEE80216_LDPC.m) — сверяется число в число (правило сдвига у них то же — ⌊s·z/96⌋); одно
расхождение — 5/6, блок (3, 0): в G.9960 50, в yaldpc 68 (в wimax_ldpc_lib и AFF3CT — 50), принято 50 (текст G.9960).
Сверка: ранг H каждого материнского кода = (1 − R_M)·N_M; длины после выкалывания = N_FEC табл. 7-19.
Пишет src/reportgen/potok/data/ldpc_ghn.json. Запуск из корня репозитория.
"""
import json
import os
import re
import sys

import numpy as np

ZDES = os.path.dirname(os.path.abspath(__file__))
K0 = os.path.normpath(os.path.join(ZDES, '..'))
KOREN = os.path.normpath(os.path.join(K0, '..', '..'))
sys.path.insert(0, os.path.join(KOREN, 'src'))
from reportgen.potok import gf2  # noqa: E402

tekst = open(os.path.join(K0, 'standarty', 'ITU-T_G.9960_2009.pdf.txt'), encoding='utf-8').read()
MATRICY = {}
for imya, zagolovok, strok, konec in (
        ('1/2', 'Hc with rate RM =1/2 (t = 24, c = 12) shall', 12, '=====PAGE 47====='),
        ('2/3', 'Hc with rate RM =2/3 (t = 24, c = 8) shall', 8, 'The compact form of parity-check matrix of mother code Hc with rate RM =5/6'),
        ('5/6', 'Hc with rate RM =5/6  (t = 24, c = 4) shall', 4, '=====PAGE 48=====')):
    kusok = tekst[tekst.index(zagolovok) + len(zagolovok):]
    kusok = kusok[:kusok.index(konec)]
    kusok = kusok[kusok.index('be:') + 3:]
    chisla = [int(x) for x in re.findall(r'-?\d+', kusok)]
    # За матрицей — колонтитул и начало следующего пункта (номера страниц, «7.1.3.2.1.1»): берём ровно
    # строк × 24 чисел; что это именно матрица, подтверждает сверка с WiMAX ниже.
    assert len(chisla) >= strok * 24, (imya, len(chisla))
    chisla = chisla[:strok * 24]
    MATRICY[imya] = [chisla[i * 24:(i + 1) * 24] for i in range(strok)]
    assert all(-1 <= x <= 95 for x in chisla), imya
print('G.9960: три материнские матрицы прочитаны из текста (12×24, 8×24, 4×24)')

# Второй источник — WiMAX (yaldpc): 1/2 = «12», 2/3 = «23B», 5/6 = «56».
m = open(os.path.join(K0, 'otkrytyj_kod', 'yaldpc', 'lib', 'IEEE80216_LDPC.m')).read()
for nashe, vimax in (('1/2', '12'), ('2/3', '23B'), ('5/6', '56')):
    kus = m[m.index('model_matrix_%s = ...' % vimax):]
    kus = kus[kus.index('[') + 1:]
    kus = kus[:re.search(r'\]\s*;\s*\n\s*\]\s*;', kus).end()]
    ryady = [[int(x) for x in re.findall(r'-?\d+', r_)] for r_ in re.findall(r'\[([^\[\]]+)\]', kus)]
    raznye = {(nashe, r, c): (MATRICY[nashe][r][c], ryady[r][c]) for r in range(len(ryady)) for c in range(24)
              if MATRICY[nashe][r][c] != ryady[r][c]}
    # Одно место: 5/6, блок (3, 0) — в G.9960 50, в yaldpc 68 (в wimax_ldpc_lib и AFF3CT — тоже 50; см. postroit_wimax.py).
    assert raznye == ({('5/6', 3, 0): (50, 68)} if nashe == '5/6' else {}), ('G.hn и WiMAX различаются', raznye)
print('G.9960: материнские 1/2, 2/3, 5/6 = WiMAX 1/2, 2/3B, 5/6 (yaldpc) число в число, кроме 5/6 (3, 0): 50 против 68')

# Шаблоны выкалывания (табл. 7-18): серии «единицы/нули» — по скобкам таблицы; в тексте — те же числа.
SERII = {'1152-144': [(1, 240), (0, 48), (1, 720), (0, 96), (1, 48)],
         '5184-648': [(1, 216), (0, 216), (1, 4320), (0, 432)]}
nachalo = tekst.index('Table 7-18/G.9960')
tab = tekst[nachalo:tekst.index('7.1.3.2.2', nachalo)]
assert re.search(r'48\s+96\s+720\s+48\s+240', tab) and re.search(r'432\s+4320\s+216\s+216', tab), 'длины серий в тексте табл. 7-18'
for kl, serii in SERII.items():
    T, i = map(int, kl.split('-'))
    assert sum(d for _, d in serii) == T and sum(d for b, d in serii if b == 0) == i, kl


def shablon(kl):
    if kl == '16-1':
        return [1] * 15 + [0]
    return [b for b, d in SERII[kl] for _ in range(d)]


# Табл. 7-19: (скорость, K, шаблон, материнский, N_FEC).
TABL = [('1/2', 168, '16-0', '1/2', 336), ('1/2', 960, '16-0', '1/2', 1920), ('1/2', 4320, '16-0', '1/2', 8640),
        ('2/3', 960, '16-0', '2/3', 1440), ('2/3', 4320, '16-0', '2/3', 6480),
        ('5/6', 960, '16-0', '5/6', 1152), ('5/6', 4320, '16-0', '5/6', 5184),
        ('16/18', 960, '16-1', '5/6', 1080), ('16/18', 4320, '16-1', '5/6', 4860),
        ('20/21', 960, '1152-144', '5/6', 1008), ('20/21', 4320, '5184-648', '5/6', 4536)]
t19 = tekst[tekst.index('Table 7-19/G.9960 – FEC encoding parameters'):][:1500]
for sk, K, _, rm, NF in TABL:
    assert re.search(r'%s\s+(?:PHYH = )?%d\s' % (re.escape(sk), K), t19), ('табл. 7-19', sk, K)
    assert str(NF) in t19
kody = {}
for sk, K, pp, rm, NF in TABL:
    a, b_ = map(int, rm.split('/'))
    NM = K * b_ // a
    b = NM // 24
    H0 = MATRICY[rm]
    baza = [[-1 if s < 0 else s * b // 96 for s in ryad] for ryad in H0]
    H = np.zeros((len(baza) * b, NM), dtype=np.uint8)
    for r, ryad in enumerate(baza):
        for c, s in enumerate(ryad):
            if s >= 0:
                H[r * b + np.arange(b), c * b + (np.arange(b) + s) % b] = 1
    rang = gf2.ранг(H)
    assert rang == NM - K, ('ранг', sk, K, rang)
    assert gf2.ранг(H[:, K:]) == NM - K, ('данные — не первые K', sk, K)
    if pp == '16-0':
        vykol = []
    else:
        sh = shablon(pp)
        vykol = [t for t in range(NM) if sh[t % len(sh)] == 0]
    assert NM - len(vykol) == NF, ('N_FEC', sk, K)
    imya = f'ghn-{NF}-{K}'
    kody[imya] = {'семейство': 'ITU-T G.9960 (G.hn)', 'n': NM, 'k': K, 'b': b, 'скорость': sk, 'материнский': rm,
                  'база': baza, 'выколоты': vykol,
                  'откуда': f'ITU-T G.9960 (10/2009), 7.1.3.2: материнский {rm} (стр. 46–47), b = {b}, сдвиг ⌊a·b/96⌋'
                            + (f', выкалывание pp{pp.split("-")[0]}({pp.split("-")[1]}) табл. 7-18' if pp != '16-0' else '')
                            + f'; N_FEC = {NF} (табл. 7-19); матрица = WiMAX (yaldpc); ранг {rang}'}
    print(f'G.hn {sk} K = {K}: N_M = {NM}, b = {b}, ранг {rang}, в канале {NF}')
json.dump(kody, open(os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_ghn.json'), 'w', encoding='utf-8'),
          ensure_ascii=False)
print('ВСЁ СОВПАЛО')
