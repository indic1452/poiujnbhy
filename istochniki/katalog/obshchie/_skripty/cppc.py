"""Комплементарные выколотые коды (CPPC) iBiquity — патент US 6,768,778 B1 (IBOC/HD Radio): материнский K=7 R=1/3 (133, 171, 145),
полнополосные R=2/5 (Hagenauer, Kroeger) и пары R=4/5 (табл. 1–2, P=4) → zapisi/cppc.json.
Табл. 1 переписана с изображения (risunki/us6768778_t12.png), табл. 2 — из OCR. Проверка (assert): dfree и c_d/P (= B_dfree, среднее
по фазам) пересчитаны svertka_spektr для каждого шаблона и совпали с патентом (до 0,01)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svertka_spektr import spektr
from zap import KOR, I, Z, sohranit, gde
G = [0b1011011, 0b1111001, 0b1100101]
F = 'istochniki/svertochnye/US6768778_complementary_punctured.pdf'
def sp(pat, terms=2):
    pm = [[int(c) for c in r] for r in pat]
    r = spektr(G, 7, punct=pm, terms=terms)
    return r['dfree'], round(r['B'][0], 2)
assert sp(['1', '1', '1']) == (14, 1.0)
kody = [('R=2/5 Hagenauer', ['1111', '1111', '1100'], 11, 1.0), ('R=2/5 Kroeger', ['1111', '1111', '1010'], 11, 2.0)]
T1 = [(['1011', '0100', '1000'], 4, 8.00, ['0100', '1011', '0100'], 4, 2.75), (['1000', '0011', '1100'], 4, 9.50, ['0111', '1100', '0000'], 4, 6.25),
      (['1011', '0100', '0100'], 4, 21.25, ['0100', '1011', '1000'], 4, 9.75)]
T2 = [(['0110', '1001', '0010'], 4, 2.50, ['1001', '0110', '1000'], 4, 2.50), (['0110', '1001', '1000'], 4, 12.00, ['1001', '0110', '0010'], 4, 12.00)]
zap = []
def rec(nm, pat, d, c, gde_t, primech=''):
    dc, cc = sp(pat)
    assert (dc, cc) == (d, c), (nm, pat, dc, cc, d, c)
    zap.append(Z('CPPC iBiquity %s: шаблон (%s)' % (nm, ', '.join(pat)), 'Комплементарные выколотые коды (US 6,768,778)', 'свёрточный с выкалыванием',
                 {'материнский': 'K=7 R=1/3, g1=1011011 (133), g2=1111001 (171), g3=1100101 (145); первая позиция — новый бит', 'выкалывание': 'P=4, строки g1/g2/g3: ' + ', '.join(pat),
                  'dfree': d, 'c_d/P': c, 'примечание': primech},
                 'IBOC / HD Radio (NRSC-5): разнос кодовых бит по боковым полосам (половинные коды — дополняющие друг друга)', [I(F, gde_t, 'https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6768778')],
                 'dfree=%d и c_d/P=%.2f пересчитаны (svertka_spektr) — совпали с патентом' % (dc, cc), 'ЧАСТИЧНО: vykalyvanie.py перебирает шаблоны только для материнского 1/2; Витерби с выкалыванием — kod.витерби_n'))
rec('R=1/3 материнский', ['1', '1', '1'], 14, 1.0, 'стр. 11 PDF (кол. 5–6): «free Hamming distance … is 14, … c_df/P is one»')
for nm, pat, d, c in kody: rec(nm, pat, d, c, 'стр. 11 PDF (кол. 6): полнополосные коды')
for i, (p1, d1, c1, p2, d2, c2) in enumerate(T1):
    rec('R=4/5 (табл. 1, пара %d, к Hagenauer 2/5)' % (i + 1), p1, d1, c1, 'стр. 11 PDF, TABLE 1', 'дополняющий: (%s)' % ', '.join(p2))
    rec('R=4/5 (табл. 1, пара %d, дополняющий)' % (i + 1), p2, d2, c2, 'стр. 11 PDF, TABLE 1')
for i, (p1, d1, c1, p2, d2, c2) in enumerate(T2):
    rec('R=4/5 (табл. 2, пара %d, к Kroeger 2/5)' % (i + 1), p1, d1, c1, 'стр. 11 PDF, TABLE 2 (OCR)', 'дополняющий: (%s)' % ', '.join(p2))
    rec('R=4/5 (табл. 2, пара %d, дополняющий)' % (i + 1), p2, d2, c2, 'стр. 11 PDF, TABLE 2 (OCR)')
sohranit('cppc', zap)
