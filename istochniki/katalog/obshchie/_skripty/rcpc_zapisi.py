"""Записи katalog.json для RCPC Hagenauer (tablicy/svertochnye/rcpc_hagenauer1988.json, собран rcpc_hagenauer.py с assert)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit
t = json.load(open(os.path.join(KOR, 'tablicy', 'svertochnye', 'rcpc_hagenauer1988.json')))
zap = []
for c in t:
    n = len(c['многочлены_двоично'])
    zap.append(Z('RCPC Hagenauer M=%d R=%s (материнский 1/%d: %s)' % (c['M'], c['скорость'], n, ', '.join(c['многочлены_восьм'])), 'RCPC (Hagenauer 1988)', 'свёрточный',
                 {'n,k': c['скорость'], 'K': c['K'], 'многочлены': ', '.join(c['многочлены_двоично']) + ' (двоично, как в статье; восьм. ' + ', '.join(c['многочлены_восьм']) + ')',
                  'выкалывание': 'период P=8, матрица a(l) %d×8 (строки — ветви g1…g%d, 1 — передать): %s' % (n, n, ' / '.join(c['выкалывание'])),
                  'совместимость': 'единицы матрицы скорости R входят в матрицы всех меньших скоростей семейства (правило 7a)',
                  'dfree': c['dfree'], 'c_d': c['c_d'], 'a_d': c['a_d'], 'таблица': 'tablicy/svertochnye/rcpc_hagenauer1988.json'},
                 'неравная защита (UEP), гибридный ARQ; семейства RCPC применены в DAB (EN 300 401), DRM, GSM AMR, IS-136 и др. (там свои таблицы — области efir/mobilnaya)',
                 [I(c['источник'].split(', ')[0], ', '.join(c['источник'].split(', ')[1:]), 'https://doi.org/10.1109/26.2763 (копия: USPTO PTAB IPR2020-01099 Ex. 1013)')],
                 c['проверка'], 'ЧАСТИЧНО: kod.витерби_n/svyortka.py — Витерби 1/n с выкалыванием по шаблону; поиск по семейству RCPC (P=8, 1/3–1/4) вслепую — нет'))
# опечатки статьи (найдены пересчётом спектра; подтверждено независимой проверкой proverka/p_vykal.py): c_d как в статье + исправленные значения
import re
for z in zap:
    op = re.findall(r'c_(\d+): в статье (\d+), пересчёт (\d+)', z['проверка'])
    if op:
        cd = dict(z['параметры']['c_d']); 
        for d, st, pr in op:
            assert cd[d] == int(st); cd[d] = int(pr)
        z['параметры']['c_d (исправлено пересчётом)'] = cd
        z['параметры']['c_d'] = z['параметры'].pop('c_d')  # порядок полей
sohranit('rcpc', zap)
