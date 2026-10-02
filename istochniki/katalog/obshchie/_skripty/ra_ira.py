"""RA / IRA / ARA: определения из первоисточников (Divsalar–Jin–McEliece 1998, Jin–Khandekar–McEliece 2000 (IRA),
Abbasfar–Divsalar–Yao (ARA, JPL 42-159 и arXiv cs/0509044)) + распределения степеней IRA из табл. 2–3 статьи Brest 2000
→ tablicy/ra/ira_brest2000.json, zapisi/ra.json.
Таблицы 2–3 — переписаны с изображения стр. 7 (risunki/ira_p7.png). Проверка (assert): Σλ_i = 1 (±2e-6) и скорость по формуле (8)
статьи, Rate = (1 + (Σρ_j/j)/(Σλ_j/j))^−1 при ρ(x) = x^(a−1), совпадает с колонкой «rate» до 1e-5."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, gde, stranica
T = [('Table 2', 2, {2: .139025, 3: .222155, 6: .638820}, .333364), ('Table 2', 3, {2: .078194, 3: .128085, 5: .160813, 6: .036178, 12: .108828, 13: .487902}, .333223),
     ('Table 2', 4, {2: .054485, 3: .104315, 6: .126755, 10: .229816, 11: .016484, 27: .450302, 28: .017842}, .333218),
     ('Table 3', 8, {3: .252744, 11: .081476, 12: .327162, 46: .184589, 48: .154029}, .50227),
     ('Table 3', 8, {2: .0577128, 3: .117057, 7: .2189922, 8: .0333844, 18: .2147221, 20: .0752259, 55: .0808676, 58: .202038}, .497946)]
F = 'istochniki/turbo_ra/mceliece_Brest00_IRA.pdf'
out = []; zap = []
for tab, a, lam, rate in T:
    s = sum(lam.values()); assert abs(s - 1) < 2e-6, (a, s)
    L = sum(v / i for i, v in lam.items()); R = 1 / (1 + (1 / a) / L)
    assert abs(R - rate) < 1e-5, (a, R, rate)
    out.append(dict(таблица=tab, a=a, lambda_=lam, rate=rate, rate_расчёт=round(R, 6)))
    zap.append(Z('IRA (систематический) a=%d, R≈%.4f, λ(x): %s' % (a, rate, ', '.join('λ%d=%g' % (i, v) for i, v in lam.items())), 'RA/IRA/ARA', 'IRA (повторение–перемежение–накопление, нерегулярный)',
                 {'n,k': 'R=%.6f' % rate, 'a': 'каждая проверка суммирует a бит после перемежителя, затем накопитель 1/(1+D)', 'распределение степеней (по рёбрам) λ': lam,
                  'перемежитель': 'случайный (в статье не фиксирован)', 'таблица': 'tablicy/ra/ira_brest2000.json'},
                 'теория IRA; прообраз DVB-S2/T2 LDPC (накопитель в проверочной части) и AR4JA', [I(F, 'стр. 7 PDF, %s (изображение risunki/ira_p7.png); формула (8) — стр. %d' % (tab, stranica(F + '.txt', 'The rate of the systematic IRA code')), 'http://www.mceliece.caltech.edu/publications/Brest00.pdf')],
                 'Σλ = %.6f; скорость по формуле (8) = %.6f — совпала с таблицей' % (s, R), 'ЧАСТИЧНО: ldpc.py декодирует IRA как LDPC по H (матрицы нет — ансамбль)'))
json.dump(out, open(os.path.join(KOR, 'tablicy', 'ra', 'ira_brest2000.json'), 'w'), ensure_ascii=False, indent=1)
FA = 'istochniki/turbo_ra/jpl_42-159K_ARA.pdf'; FX = 'istochniki/turbo_ra/arxiv_cs0509044_ARA_BEC.pdf'; FP = 'istochniki/turbo_ra/mceliece_Allerton98_coding_theorems_turbo_like.ps'
zap += [
 Z('RA (repeat–accumulate) q-кратный, R = 1/q', 'RA/IRA/ARA', 'RA (последовательный каскад: повторение → перемежитель → накопитель)',
   {'кодер': 'каждый бит повторяется q раз, блок qN перемешивается, затем накопитель 1/(1+D) (несистематический)', 'R': '1/q (в статье R = 1/q — п. 2)'},
   'теория турбо-подобных кодов (Divsalar–Jin–McEliece, Allerton 1998); основа IRA/ARA/AR4JA', [I(FP, 'весь файл (PostScript, раздел RA codes)', 'http://www.mceliece.caltech.edu/publications/Allerton98.ps'),
   I(F, gde(F + '.txt', 'case (2) simpliﬁes to R = 1/q'), 'http://www.mceliece.caltech.edu/publications/Brest00.pdf')],
   'определение сверено в двух работах (Allerton 1998 и Brest 2000, где RA — частный случай IRA)', 'ЧАСТИЧНО: проект опознаёт накопитель как RSC 1/(1+D) (svyortka) и повторение — как блочный код'),
 Z('ARA (accumulate–repeat–accumulate)', 'RA/IRA/ARA', 'ARA (предкодер-накопитель + RA)',
   {'кодер': 'накопитель (предкодер) на части бит → повторение → перемежитель → накопитель; вариант с выколотым накопителем — протографы ARA (JPL)', 'семейство': 'AR4JA CCSDS 131.0 — «accumulate–repeat-4–jagged-accumulate» (запись kosmos/kody2)'},
   'глубокий космос (AR4JA), исследования JPL', [I(FA, 'весь отчёт (табл. 1 — сравнение кодов n=8000, k=4000)', 'https://ipnpr.jpl.nasa.gov/progress_report/42-159/159K.pdf'), I(FX, 'весь файл', 'https://arxiv.org/abs/cs/0509044')],
   'два источника (JPL PR 42-159 и arXiv cs/0509044) описывают одну конструкцию', 'ЧАСТИЧНО: AR4JA — в проекте (ldpc_std, CCSDS); ARA общего вида — как LDPC по H'),
]
sohranit('ra', zap)
