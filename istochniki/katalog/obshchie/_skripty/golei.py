"""Коды Голея: двоичные (23,12,7) и (24,12,8), троичные (11,6,5) и (12,6,6) → tablicy/golei/golei.json, zapisi/golei.json.

Источники: MIL-STD-188-141B (ALE), прил. A, A.5.2.2.2, рис. A-6 (G), A-8 (пример кодирования), A.5.2.2.3 (перемежение);
Sage (GPL) src/sage/coding/golay_code.py — G для n = 23, 24, 11, 12; LinuxALE golay.c (GPL-2, область mobilnaya) —
таблица кодера 4096 слов; gr-satellites golay24.c (GPL-3, область kosmos) — матрица H.
Проверки (assert): G MIL-STD = G Sage (24); вес. спектр 1+759x^8+2576x^12+759x^16+x^24; выкалывание последнего бита —
циклический код с g(x) = x^11+x^9+x^7+x^6+x^5+x+1 (MIL-STD) и Sage (23) — с g = 1+x^2+x^4+x^5+x^6+x^10+x^11 (взаимный);
g | x^23+1; пример рис. A-8 (данные 110100010101 → проверка 010101100110); таблица LinuxALE = P MIL-STD для всех 4096 слов;
код gr-satellites (по H) — тот же набор слов с точностью до перестановки? проверяется равенство набора; троичные — d.
"""
import itertools, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka, stranica
from gf2 import mod, deg, reverse
F_MIL = 'istochniki/golei/MIL-STD-188-141B_ALE.pdf.txt'
t = open(os.path.join(KOR, F_MIL)).read()
blk = t[t.find('=====PAGE 75====='):t.find('FIGURE A-6.  Generator matrix for (24, 12) extended Golay code.', t.find('=====PAGE 75====='))]
rows = re.findall(r'([01]{3})\s+([01]{3})\s+([01]{3})\s+([01]{3})\s+:\s+([01]{3})\s+([01]{3})\s+([01]{3})\s+([01]{3})', blk)
assert len(rows) == 12
G = [''.join(r) for r in rows]
for i, r in enumerate(G): assert r[:12] == ''.join('1' if j == i else '0' for j in range(12))
P = [int(r[12:], 2) for r in G]
# Sage
F_SA = 'istochniki/kod/sage/src/sage/coding/golay_code.py'
s = open(os.path.join(KOR, F_SA)).read()
def sage_mat(n, field):
    g0 = s.find('def generator_matrix')
    i = s.find('elif n == %d:' % n, g0) if n not in (23, 12) else s.find('if n == 23:' if n == 23 else 'else:', g0 if n == 23 else s.find('elif n == 11:', g0))
    body = s[i:s.find(']])', i) + 3]
    return [[int(x) for x in re.findall(r'\d', r)] for r in re.findall(r'\[([0-9, ]+)\]', body)]
S24 = sage_mat(24, 2); S23 = sage_mat(23, 2); S11 = sage_mat(11, 3); S12 = sage_mat(12, 3)
assert [''.join(map(str, r)) for r in S24] == G, 'G MIL-STD != Sage'
def words(Gr, q=2):
    n = len(Gr[0]); out = []
    for u in itertools.product(range(q), repeat=len(Gr)):
        w = [0] * n
        for ui, r in zip(u, Gr):
            if ui:
                for j in range(n): w[j] = (w[j] + ui * r[j]) % q
        out.append(tuple(w))
    return out
W24 = words([[int(c) for c in r] for r in G])
from collections import Counter
spek = Counter(sum(1 for x in w if x) for w in W24)
assert dict(spek) == {0: 1, 8: 759, 12: 2576, 16: 759, 24: 1}, spek
# (23,12): выкалываем последний бит → циклический?
g_mil = (1 << 11) | (1 << 9) | (1 << 7) | (1 << 6) | (1 << 5) | (1 << 1) | 1
assert mod((1 << 23) | 1, g_mil) == 0
def as_poly(bits):  # бит j слова — коэффициент при x^(22-j) (первый передаваемый — старший)
    v = 0
    for b in bits: v = (v << 1) | b
    return v
# проверка: все слова (23 первых бита) делятся на g_mil (с одним из порядков бит)
ok_hi = all(mod(as_poly(w[:23]), g_mil) == 0 for w in W24)
ok_lo = all(mod(as_poly(w[:23][::-1]), g_mil) == 0 for w in W24)
assert ok_hi or ok_lo
g_sage = sum(1 << i for i, c in enumerate(S23[0]) if c)  # строка 1 Sage = коэффициенты g от x^0
assert g_sage == reverse(g_mil) and mod((1 << 23) | 1, g_sage) == 0
W23 = words(S23); assert min(sum(w) for w in W23 if any(w)) == 7
# пример рис. A-8
u = '110100010101'; p = 0
for i, c in enumerate(u):
    if c == '1': p ^= P[i]
assert format(p, '012b') == '010101100110', format(p, '012b')
# LinuxALE
F_LA = '../mobilnaya/istochniki/kod/LinuxALE/golay.c'
la = open(os.path.join(KOR, F_LA)).read()
tab = [int(x, 16) for x in re.findall(r'0x([0-9A-F]{3})', la[la.find('encode_table[]'):la.find('};', la.find('encode_table[]'))])]
assert len(tab) == 4096
for d in range(4096):
    q = 0
    for i in range(12):
        if (d >> (11 - i)) & 1: q ^= P[i]
    assert tab[d] == q, d
# gr-satellites H: H[i] = (1<<(23-i)) | P_i?  кодовые слова — r|s с проверками
F_GS = '../kosmos/istochniki/kod/gr-satellites/lib/golay24.c'
gs = open(os.path.join(KOR, F_GS)).read()
H = [int(x, 16) for x in re.findall(r'0x([0-9a-f]+)', gs[gs.find('static const uint32_t H'):gs.find('};', gs.find('static const uint32_t H'))])]
Bm = [h & 0xfff for h in H]
# у gr-satellites слово = (данные 12 бит)<<12 | проверка, проверка_i = parity(данные & B(i)) — сравним набор слов
gsw = set()
for d in range(4096):
    q = 0
    for i in range(12):
        q |= (bin(d & Bm[i]).count('1') & 1) << (11 - i)
    gsw.add((d << 12) | q)
milw = set((d << 12) | tab[d] for d in range(4096))
gs_same = gsw == milw
gs_spek = Counter(bin(w).count('1') for w in gsw)
assert dict(gs_spek) == {0: 1, 8: 759, 12: 2576, 16: 759, 24: 1}
# троичные
W11 = words(S11, 3); W12 = words(S12, 3)
d11 = min(sum(1 for x in w if x) for w in W11 if any(w)); d12 = min(sum(1 for x in w if x) for w in W12 if any(w))
assert d11 == 5 and d12 == 6
res = dict(G24_MIL=G, P_hex=['0x%03X' % x for x in P], g23_MIL=hex(g_mil), g23_sage=hex(g_sage), порядок_бит_циклич='первый бит — старший коэффициент' if ok_hi else 'первый бит — младший коэффициент',
           спектр24={str(k): v for k, v in sorted(spek.items())}, пример_A8=dict(данные=u, проверка=format(p, '012b')), gr_satellites_тот_же_набор=gs_same,
           G23_sage=[''.join(map(str, r)) for r in S23], G11_sage=[''.join(map(str, r)) for r in S11], G12_sage=[''.join(map(str, r)) for r in S12])
json.dump(res, open(os.path.join(KOR, 'tablicy', 'golei', 'golei.json'), 'w'), ensure_ascii=False, indent=1)
print('Голей: всё сошлось; gr-satellites тот же набор слов:', gs_same, 'порядок', res['порядок_бит_циклич'])
pg = stranica(F_MIL, 'A.5.2.2.2  Golay coding')
zap = [
 Z('Голей расширенный (24,12,8) — MIL-STD-188-141B ALE', 'Коды Голея', 'Голей',
   {'n,k': '24,12', 'd': 8, 'многочлен': 'g(x) = x^11 + x^9 + x^7 + x^6 + x^5 + x + 1 (0xAE3), систематический G = [I12 | P], P — tablicy/golei/golei.json',
    'применение в ALE': '24-бит слово ALE делится на две половины по 12 бит, каждая кодируется Голеем; проверочные G13…G24 инвертируются; 48 бит перемежаются по рис. A-10 + стаффинговый бит S49=0, передача A1,B1,…A24,B24,S49, тройное повторение (A.5.2.2.3)',
    'порядок бит': 'W1 — старший (MSB) бит слова'},
   'ALE 2G (MIL-STD-188-141, FED-STD-1045), КВ-радиосвязь; тот же G — у Sage (MacWilliams–Sloane)',
   [I(F_MIL[:-4], 'стр. %d PDF (A.5.2.2.2), стр. 75 PDF (рис. A-6), стр. 77 (рис. A-8)' % pg), I(F_SA, 'строки %d–%d' % (stroka(F_SA, 'elif n == 24:', 268), stroka(F_SA, 'elif n == 24:', 268) + 14), 'https://github.com/sagemath/sage/blob/develop/src/sage/coding/golay_code.py'),
    I(F_LA, 'строки 51–… (encode_table)', 'https://github.com/DigitalHERMES/LinuxALE/blob/d133636236855613615f0139366b0609918a3713/golay.c'), I(F_GS, 'строки 38–39 (H)', 'https://github.com/daniestevez/gr-satellites/blob/4210dc45c9725e30d8f612eeec614f3f353cd09c/lib/golay24.c')],
   'G MIL-STD = G Sage; весовой спектр 1+759+2576+759+1 (перебор 4096 слов); пример рис. A-8 пересчитан; таблица кодера LinuxALE (4096) = P MIL-STD; код gr-satellites (Morelos-Zaragoza) — тот же спектр%s' % (', тот же набор слов' if gs_same else ', другой (эквивалентный) набор слов'),
   'ЕСТЬ в проекте: kod.ИЗВЕСТНЫЕ (24,12) и ispravlenie.py (синдромное исправление t=3); ALE-кадр (перемежение рис. A-10, инверсия G13…G24) — НЕТ'),
 Z('Голей (23,12,7) — циклический, g = x^11+x^9+x^7+x^6+x^5+x+1 и взаимный x^11+x^10+x^6+x^5+x^4+x^2+1', 'Коды Голея', 'Голей',
   {'n,k': '23,12', 'd': 7, 'многочлены': '0xAE3 (MIL-STD-188-141B) и 0xC75 (Sage, строка 1 G: 1+x^2+x^4+x^5+x^6+x^10+x^11) — взаимные, оба делят x^23+1',
    'связь с (24,12)': '(24,12) = (23,12) + общий бит чётности; выкалывание последнего бита слова MIL-STD даёт циклический код с g=0xAE3 (%s)' % res['порядок_бит_циклич']},
   'P25 (TIA-102), ACCH/синхрослова, Voyager (исторически), DMR/NXDN/YSF (варианты 23,12 и 24,12 — в области mobilnaya)',
   [I(F_MIL[:-4], 'стр. %d PDF (A.5.2.2.2)' % pg), I(F_SA, 'строки %d–%d' % (stroka(F_SA, 'if n == 23:', 268), stroka(F_SA, 'if n == 23:', 268) + 14), 'https://github.com/sagemath/sage/blob/develop/src/sage/coding/golay_code.py')],
   'g | x^23+1 (расчёт); d=7 перебором 4096 слов Sage; reverse(0xAE3) = 0xC75; слова (24,12) MIL-STD без последнего бита делятся на 0xAE3',
   'ЕСТЬ в проекте: kod.ИЗВЕСТНЫЕ (23,12), rs_bch.py (корни циклического кода), ispravlenie.py'),
 Z('Троичный Голей (11,6,5) и расширенный (12,6,6)', 'Коды Голея', 'Голей',
   {'n,k': '11,6 / 12,6 над GF(3)', 'd': '5 / 6', 'G': 'tablicy/golei/golei.json (из Sage)'},
   'теория (совершенный троичный код); в связи не встречается',
   [I(F_SA, 'строки %d–%d' % (stroka(F_SA, 'elif n == 11:', 268), stroka(F_SA, 'elif n == 11:', 268) + 20), 'https://github.com/sagemath/sage/blob/develop/src/sage/coding/golay_code.py')],
   'd=5 и d=6 перебором 729 слов', 'НЕТ: проект работает с двоичными кодами (троичный — только справочно)'),
]
sohranit('golei', zap)
