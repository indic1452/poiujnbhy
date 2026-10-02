"""Записи katalog.json: таблицы примитивных многочленов (Koopman CMU; Živković, Math. Comp. 1994) + по одной записи на степень
n = 2…64 (многочлен наименьшего веса из таблицы Živković: трёхчлен, иначе пятичлен) — tablicy/primitivnye/*.json (primitivnye.py, assert)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit
ziv = {z['n']: z for z in json.load(open(os.path.join(KOR, 'tablicy', 'primitivnye', 'zivkovic1994.json')))}
koop = json.load(open(os.path.join(KOR, 'tablicy', 'primitivnye', 'koopman_lfsr_pervye100.json')))
FZ = 'istochniki/primitivnye/zivkovic_primpol1.pdf'; FZ2 = 'istochniki/primitivnye/zivkovic1994_mcom62.pdf'
zap = [
 Z('Примитивные многочлены GF(2): Koopman «Maximal Length LFSR Feedback Terms», степени 4…64', 'Примитивные многочлены (таблицы)', 'таблица (примитивные многочлены)',
   {'объём': 'первые 100 для каждой степени 4…64; полные списки для 4…22 (число = φ(2^N−1)/N)', 'запись': 'Koopman — «неявная +1»: явная = (v<<1)|1', 'таблица': 'tablicy/primitivnye/koopman_lfsr_pervye100.json'},
   'генераторы ПСП/скремблеров, CRC, поля РС/БЧХ; перебор при слепом поиске', [I('istochniki/primitivnye/koopman_lfsr_index.html', 'весь файл', 'https://users.ece.cmu.edu/~koopman/lfsr/index.html'),
   I('istochniki/primitivnye/koopman_lfsr/64.txt', 'весь файл (пример)', 'https://users.ece.cmu.edu/~koopman/lfsr/64.txt')],
   'каждый из первых 100 (степени 4…64) — примитивен (порядок x = 2^N−1, разложение sympy); длина полных списков = φ(2^N−1)/N (primitivnye.py)',
   'ЕСТЬ в проекте: rs_bch.примитивные(m) порождает примитивные многочлены сам; skrembler.py — перебор отводов'),
 Z('Примитивные многочлены GF(2): Živković (Math. Comp. 62, 1994) — трёхчлен, 5- и 7-член для %d степеней n < 5000' % len(ziv), 'Примитивные многочлены (таблицы)', 'таблица (примитивные многочлены)',
   {'объём': 'n < 5000, для которых известно разложение 2^n−1 (%d степеней, от %d до %d)' % (len(ziv), min(ziv), max(ziv)), 'таблица': 'tablicy/primitivnye/zivkovic1994.json'},
   'длинные LFSR (криптография, ПСП), выбор малого веса', [I(FZ, 'табл. 1, стр. 3–21 PDF', 'https://poincare.matf.bg.ac.rs/~ezivkovm/publications/primpol1.pdf'), I(FZ2, 'аннотация (Math. Comp. 62 (1994) 385–386)', 'https://www.ams.org/journals/mcom/1994-62-205/S0025-5718-1994-1201073-4/S0025-5718-1994-1201073-4.pdf')],
   'n ≤ 22 — все есть в полных списках Koopman (второй источник); n ≤ 100 — примитивность рассчитана; n ≤ 400 — неприводимость рассчитана', 'ЕСТЬ в проекте (для m ≤ 16 порождаются кодом)'),
]
for n in range(2, 65):
    z = ziv.get(n)
    if not z: continue
    key = 'трёхчлен' if z.get('трёхчлен') else 'пятичлен'
    zap.append(Z('Примитивный многочлен степени %d: %s' % (n, z[key + '_запись']), 'Примитивные многочлены (таблицы)', 'примитивный многочлен (LFSR)',
                 {'многочлен': z[key + '_запись'], 'hex': z[key + '_hex'], 'вид': key, 'также': {k: z.get(k + '_запись') for k in ('трёхчлен', 'пятичлен', 'семичлен') if z.get(k) and k != key}},
                 'ПСП/скремблеры/поля GF(2^%d)' % n, [I(FZ, 'стр. %d PDF, строка n=%d табл. 1' % (z['стр_pdf'], n), 'https://poincare.matf.bg.ac.rs/~ezivkovm/publications/primpol1.pdf')],
                 '; '.join(z['проверка']), 'ЕСТЬ в проекте: rs_bch.примитивный (проверка), skrembler.период_лрп'))
sohranit('primitivnye', zap)
