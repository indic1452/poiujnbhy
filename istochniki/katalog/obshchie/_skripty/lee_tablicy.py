"""Таблицы P. J. Lee (JPL TDA Progress Reports): 42-77 (K=3…8? R=1/N, лучшие по требуемому Eb/N0), 42-80 (K=8…13, R=1/N),
42-82 (k0/(k0+1), m0 ≤ 8) → tablicy/svertochnye/lee_jpl.json, zapisi/lee.json.
Сканы → OCR (tesseract, ocr_pdf.py) → разбор строк; OCR-исправления только однозначные (§/$ → 5, l/]/I → 1) и
ручные правки по изображению страниц (словарь RUCHNYE, со ссылкой на снимок). Проверка (assert): dfree каждого кода
пересчитан (svertka_spektr для 1/N, dfree_kn для k0/n0) и равен d_f таблицы; строки, где не сошлось, не принимаются.
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svertka_spektr import spektr
from dfree_kn import dfree_kn
from zap import KOR, I, Z, sohranit, str_po_stroke
D = 'istochniki/svertochnye/'
FIX = str.maketrans({'§': '5', '$': '5', 'l': '1', ']': '1', 'I': '1', '|': '1', 'O': '0', 'o': '0', 't': '1'})
# (файл, номер строки) -> строка, переписанная с изображения страницы (risunki/lee77_p8_a.png, lee77_p8_b.png, lee77_p9_a.png, lee80_p4.png)
_F77 = 'istochniki/svertochnye/jpl_42-77F_Lee_rate1N.pdf.txt'; _F80 = 'istochniki/svertochnye/jpl_42-80K_Lee_rate1N_K9_13.pdf.txt'
RUCHNYE = {(_F77, 358): '3 7 7,7,7,7,5,5,5 18 6.664 4.035 3,a,b', (_F77, 396): '5 3 37, 33, 25 12 5.395 3.115 1,a,b',
           (_F77, 404): '5 5 37, 35, 33, 27, 25 20 5.270 2.923 3', (_F77, 412): '5 6 37, 35, 33, 27, 25, 23 23 5.211 2.880 4,a',
           (_F77, 418): '5 7 37, 35, 33, 31, 27, 25, 23 26 5.211 2.845 4,a', (_F77, 438): '6 2 75, 57 8 5.293 3.211 4,b',
           (_F77, 440): '6 3 75, 67, 41 12 5.034 2.854 4,b', (_F77, 448): '6 6 77, 73, 67, 55, 51, 45 26 4.694 2.591 4,a',
           (_F77, 450): '6 7 75, 75, 67, 65, 57, 53, 47 32 4.762 2.630 3', (_F77, 451): '6 7 77, 73, 67, 63, 55, 51, 45 30 4.696 2.565 4,a',
           (_F77, 467): '7 6 173, 163, 151, 137, 135, 135 30 4.394 2.499 3', (_F80, 169): '(9, 1/2) 5.573 4.129 12 9,O 753, 561', (_F80, 182): '(9, 1/7) 5.229 3.671 44 P 755, 751, 737, 673, 525, 463, 457'}
pr = {'1/N': [], 'k/n': []}; plohie = []


def chisla(s):
    return [x.translate(FIX) for x in re.findall(r"[0-9§$l\]I|Oot]+", s)]


def dfree1(gens, K):
    return spektr(gens, K, terms=2)['dfree']


# --- 42-77F ---
f = D + 'jpl_42-77F_Lee_rate1N.pdf.txt'
lines = open(os.path.join(KOR, f)).read().split('\n')
for i, l in enumerate(lines):
    l = RUCHNYE.get((f, i + 1), l)
    mf = re.search(r"\s([0-9§$][.,]\d{3})\s+([0-9§$][.,]\d{3})(\s|$)", l)
    if not mf: continue
    tok = [x for x in l[:mf.start()].replace(',', ' , ').split() if x not in ('-', '.', "'", '‘')]
    if len(tok) < 4 or not re.fullmatch(r"[0-9B)\]]+", tok[0]) or not re.fullmatch(r"[0-9\]]+", tok[1]): continue
    fl = [len(tok)]
    K, N = tok[0].translate(FIX), tok[1].translate(FIX)
    if not K.isdigit() or not N.isdigit(): plohie.append((f, i + 1, l, 'K/N нечитаемы')); continue
    K = int(K); N = int(N)
    dtok = tok[fl[0] - 1].translate(FIX)
    if not dtok.isdigit(): plohie.append((f, i + 1, l, 'd')); continue
    d = int(dtok)
    gens = [x.translate(FIX) for x in tok[2:fl[0] - 1] if x != ',']
    ok = len(gens) == N and all(re.fullmatch(r'[0-7]+', g) and int(g, 8) < (1 << K) for g in gens)
    if not ok: plohie.append((f, i + 1, l, 'генераторы')); continue
    g = [int(x, 8) for x in gens]
    dc = dfree1(g, K)
    if dc != d: plohie.append((f, i + 1, l, 'dfree %d ≠ %d' % (dc, d))); continue
    m = re.match(r'.*', l[mf.end():])
    pr['1/N'].append(dict(отчёт='42-77', K=K, N=N, G=gens, d=d, примечание=m.group(0).strip(), строка=i + 1, стр=str_po_stroke(f, i + 1), файл=f))
# --- 42-80K ---
f = D + 'jpl_42-80K_Lee_rate1N_K9_13.pdf.txt'
lines = open(os.path.join(KOR, f)).read().split('\n')
for i, l in enumerate(lines):
    l = RUCHNYE.get((f, i + 1), l)
    m = re.match(r"^\((\d+),\s*1/(\d)\)\s+[0-9§$.,]+\]?\s+[0-9§$.,]+\s+(\w+)\s+([0-9A-Z,]+)\s+(.*)$", l.strip())
    if not m: continue
    K = int(m.group(1)); N = int(m.group(2)); d = m.group(3).translate(FIX)
    if not d.isdigit(): plohie.append((f, i + 1, l, 'd')); continue
    d = int(d)
    gens = [g.translate(FIX) for g in re.split(r'[,\s]+', m.group(5).strip().rstrip(',')) if g]
    ok = len(gens) == N and all(re.fullmatch(r'[0-7]+', g) and int(g, 8) < (1 << K) for g in gens)
    if not ok: plohie.append((f, i + 1, l, 'генераторы')); continue
    g = [int(x, 8) for x in gens]
    dc = dfree1(g, K)
    if dc != d: plohie.append((f, i + 1, l, 'dfree %d ≠ %d' % (dc, d))); continue
    pr['1/N'].append(dict(отчёт='42-80', K=K, N=N, G=gens, d=d, примечание=m.group(4), строка=i + 1, стр=str_po_stroke(f, i + 1), файл=f))
# --- 42-82J ---
f = D + 'jpl_42-82J_Lee_highrate.pdf.txt'
lines = open(os.path.join(KOR, f)).read().split('\n')
cur = None
for i, l in enumerate(lines):
    l = RUCHNYE.get((f, i + 1), l)
    s = l.strip()
    m = re.match(r"^\((\d),?\s*(\d)/(\d)\)?\s+(.*)$", s)
    if m:
        cur = (int(m.group(1)), int(m.group(2)), int(m.group(3))); s = m.group(4)
    elif cur is None or not re.match(r"^[0-9s§$]", s): continue
    m2 = re.match(r"^(\w)\)?\s+([0-9§$.,]+)\s+([APD])\s+(.*)$", s)
    if not m2: continue
    m0, k0, n0 = cur
    d = m2.group(1).translate(FIX).replace('s', '5')
    gens = [g.translate(FIX) for g in re.findall(r"[0-9§$l\]I]+", m2.group(4))]
    K = m0 + k0
    if not d.isdigit() or len(gens) != n0 or not all(re.fullmatch(r'[0-7]+', g) and int(g, 8) < (1 << K) for g in gens):
        plohie.append((f, i + 1, l, 'разбор')); continue
    d = int(d); g = [int(x, 8) for x in gens]
    dl = dfree_kn(g, K, k0, 'left'); dr = dfree_kn(g, K, k0, 'right')
    if d not in (dl, dr): plohie.append((f, i + 1, l, 'dfree left %s right %s ≠ %d' % (dl, dr, d))); continue
    pr['k/n'].append(dict(отчёт='42-82', m0=m0, k0=k0, n0=n0, G=gens, d=d, примечание=m2.group(3), порядок='новые биты слева' if d == dl else 'новые биты справа',
                          строка=i + 1, стр=str_po_stroke(f, i + 1), файл=f))
print('принято 1/N', len(pr['1/N']), 'k/n', len(pr['k/n']), 'не принято', len(plohie))
for p in plohie: print(' ', p[0].split('/')[-1], p[1], p[3], '|', p[2][:110])
json.dump(dict(коды=pr, не_принято=[dict(файл=a, строка=b, текст=c, причина=e) for a, b, c, e in plohie]),
          open(os.path.join(KOR, 'tablicy', 'svertochnye', 'lee_jpl.json'), 'w'), ensure_ascii=False, indent=1)
assert not plohie
URL = {'42-77': 'https://ipnpr.jpl.nasa.gov/progress_report/42-77/77F.PDF', '42-80': 'https://ipnpr.jpl.nasa.gov/progress_report/42-80/80K.PDF',
       '42-82': 'https://ipnpr.jpl.nasa.gov/progress_report/42-82/82J.PDF'}
AVT = {'1': 'Odenwalder', '2': 'Larsen', '3': 'Daut et al.', '4': 'Lee', 'O': 'Odenwalder', 'L': 'Larsen', 'J': 'Johannesson–Paaske', 'D': 'Daut et al.',
       'P': 'Palazzo (1/N) / Paaske (k/n)', 'A': 'Lee'}
zap = []
for c in pr['1/N']:
    prim = c['примечание']
    kto = sorted(set(AVT[x] for x in re.findall(r'[1-4OLJDPA]', prim.split(',')[0] if c['отчёт'] == '42-77' else prim) if x in AVT))
    zap.append(Z('Свёрточный (Lee, JPL %s) K=%d R=1/%d (%s)' % (c['отчёт'], c['K'], c['N'], ', '.join(c['G'])), 'Лучшие свёрточные коды 1/N по требуемому Eb/N0 (P. J. Lee, JPL)', 'свёрточный',
                 {'n,k': '%d,1' % c['N'], 'K': c['K'], 'многочлены': 'восьм. ' + ', '.join(c['G']) + ' (обычное представление, как в таблице)', 'dfree': c['d'],
                  'примечание таблицы': prim, 'найден': ', '.join(kto), 'таблица': 'tablicy/svertochnye/lee_jpl.json'},
                 'глубокий космос, ШПС (низкоскоростные коды); кандидаты слепого опознания кодов 1/N',
                 [I(c['файл'][:-4], 'стр. %d PDF (Table %s), строка %d OCR' % (c['стр'], '2' if c['отчёт'] == '42-77' else '1', c['строка']), URL[c['отчёт']])],
                 'dfree пересчитан (svertka_spektr) = %d — совпадает с d_f таблицы' % c['d'],
                 'ЕСТЬ в проекте: svyortka.py (N ≤ 4), kod.витерби_n' if c['N'] <= 4 else 'ЧАСТИЧНО: svyortka.py ищет 1/n только до n=4; Витерби kod.витерби_n — общий'))
for c in pr['k/n']:
    zap.append(Z('Свёрточный (Lee, JPL 42-82) (m0=%d, %d/%d) G=(%s)' % (c['m0'], c['k0'], c['n0'], ', '.join(c['G'])), 'Лучшие свёрточные коды k/(k+1) (P. J. Lee, JPL 42-82)', 'свёрточный',
                 {'n,k': '%d,%d' % (c['n0'], c['k0']), 'K': c['m0'] + c['k0'], 'память m0': c['m0'], 'генераторы': 'G = (g(1)…g(%d)) восьм., g(j) — строка матрицы K бит: ' % c['n0'] + ', '.join(c['G']),
                  'структура': 'один регистр длины K = m0 + k0, за такт вдвигаются k0 бит (рис. 1 отчёта); %s' % c['порядок'], 'dfree': c['d'],
                  'найден': AVT.get(c['примечание'], c['примечание'])},
                 'высокоскоростные невыколотые коды (альтернатива выкалыванию)',
                 [I(c['файл'][:-4], 'стр. %d PDF (Table 1), строка %d OCR' % (c['стр'], c['строка']), URL['42-82'])],
                 'dfree пересчитан (dfree_kn.py, Дейкстра по решётке 2^m0 состояний) = %d — совпадает с таблицей' % c['d'],
                 'НЕТ: проект опознаёт k/n только как выколотые 1/2 (vykalyvanie.py); невыколотые k/(k+1) с общим регистром — не поддержаны'))
sohranit('lee', zap)
