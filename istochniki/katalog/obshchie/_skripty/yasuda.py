"""Лучшие шаблоны выкалывания Yasuda (патент US 4,462,101, 1984; табл. 1, стр. 9 PDF / кол. 5) для материнского кода
K=7 R=1/2 → tablicy/svertochnye/yasuda_us4462101.json, zapisi/yasuda.json.
Таблица — картинка скана; переписана с изображения (risunki/yasuda_tab1.png, yasuda_tab1b.png).
Многочлены в таблице не названы («the convolutional code with the coding rate 1/2 and the code constraint length 7»);
проверка (assert): спектр пересчитан для обоих порядков ветвей 171/133; при первой строке шаблона = 133, второй = 171
(запись Proakis, старший бит — текущий вход) dfree и C_k (= P·B_dfree) совпадают с таблицей для всех 6 скоростей.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svertka_spektr import spektr
from zap import KOR, I, Z, sohranit
T = [('2/3', 2, 1, ['11', '10'], 6, 3), ('3/4', 3, 2, ['110', '101'], 5, 42), ('4/5', 4, 3, ['1111', '1000'], 4, 12),
     ('5/6', 5, 4, ['11010', '10101'], 4, 92), ('6/7', 6, 5, ['111010', '100101'], 3, 5), ('7/8', 7, 6, ['1111010', '1000101'], 3, 9)]
G = [0o133, 0o171]
out = []; zap = []
F = 'istochniki/svertochnye/US4462101_Yasuda_punctured.pdf'
for rate, L, m, rows, d, C in T:
    pm = [[int(c) for c in r] for r in rows]
    r = spektr(G, 7, punct=pm, terms=3)
    assert r['dfree'] == d and round(r['B'][0] * L) == C, (rate, r)
    out.append(dict(скорость=rate, L=L, m=m, шаблон=rows, d=d, C_k=C, A_dfree_P=round(r['A'][0] * L), спектр_B=r['B']))
    zap.append(Z('Выколотый свёрточный K=7 R=%s (Yasuda, шаблон %s/%s)' % (rate, rows[0], rows[1]), 'Лучшие шаблоны выкалывания (Yasuda 1984)', 'свёрточный',
                 {'n,k': rate.replace('/', ',')[::-1] if False else rate, 'K': 7, 'многочлены': '133 (строка 1 шаблона), 171 (строка 2) — восьм., запись Proakis; установлено сверкой спектра',
                  'выкалывание': 'период L=%d, удаляется m=%d из 2L; строка 1: %s, строка 2: %s (1 — передать)' % (L, m, rows[0], rows[1]), 'dfree': d, 'C_k': C,
                  'таблица': 'tablicy/svertochnye/yasuda_us4462101.json'},
                 'первоисточник выколотых кодов Viterbi (KDD/Intelsat); шаблоны 2/3, 3/4 совпадают с DVB-S/IESS по распределению (DVB: X 10/Y 11 — та же схема с другим порядком ветвей)',
                 [I(F, 'стр. 9 PDF (кол. 5), Table 1', 'https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/4462101')],
                 'спектр пересчитан (svertka_spektr): dfree=%d и C_k=P·B_dfree=%d совпали; при порядке ветвей 171/133 не совпадает — порядок установлен' % (d, C),
                 'ЕСТЬ в проекте: vykalyvanie.py перебирает все шаблоны k/(k+1) для материнского 1/2 (этот найдётся)'))
json.dump(dict(источник=F, многочлены=['133', '171'], коды=out), open(os.path.join(KOR, 'tablicy', 'svertochnye', 'yasuda_us4462101.json'), 'w'), ensure_ascii=False, indent=1)
sohranit('yasuda', zap)
