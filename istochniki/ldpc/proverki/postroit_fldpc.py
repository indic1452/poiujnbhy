"""Таблицы кода F-LDPC (TrellisWare; основа Datum FlexLDPC) — из текста патента US 7,673,213 B2.

Строит src/reportgen/potok/data/ldpc_fldpc.json только из текста патента (patenty/US7673213.txt,
полный текст ppubs.uspto.gov), таблицы 38 (дизеры r, w), 39 (p, s), 40 (J), 42 (выкалывание), и сверяет
их с копиями в US 7,958,425 B2 (таблицы 21, 22). Номер строки файла — в поле «откуда».
Запуск: python3 istochniki/ldpc/proverki/postroit_fldpc.py (из корня репозитория).
"""
import json
import math
import os
import re

ZDES = os.path.dirname(os.path.abspath(__file__))
KOREN = os.path.normpath(os.path.join(ZDES, '..', '..', '..'))
P = os.path.join(ZDES, '..', 'patenty')


def stroka_tablicy(imya, nomer):
    tekst = open(os.path.join(P, imya), encoding='utf-8').read().split('\n')
    for i, s in enumerate(tekst):
        if 'TABLE-US-%05d' % nomer in s:
            return i + 1, s
    raise AssertionError((imya, nomer))


def dizery(s):
    rez = {}
    for m in re.finditer(r'(All|\d+)\s+([rw])\s+\(([\d,\s]+)\)', s):
        rez[(m.group(1), m.group(2))] = [int(x) for x in m.group(3).replace(',', ' ').split()]
    return rez


def ps(s):
    return {int(k): (int(p), int(s_)) for k, p, s_ in re.findall(r'(\d+)\t(\d+)\t(\d+)', s)}


n38, t38 = stroka_tablicy('US7673213.txt', 38)
n39, t39 = stroka_tablicy('US7673213.txt', 39)
n40, t40 = stroka_tablicy('US7673213.txt', 40)
n42, t42 = stroka_tablicy('US7673213.txt', 42)
d, pss = dizery(t38), ps(t39)
assert d == dizery(stroka_tablicy('US7958425.txt', 21)[1]), 'дизеры в двух патентах различаются'
assert pss == ps(stroka_tablicy('US7958425.txt', 22)[1]), 'p, s в двух патентах различаются'
assert sorted(pss) == [128, 256, 512, 1024, 2048, 4096, 8192, 16384]
for v in d.values():
    assert sorted(v) == list(range(64))
for K, (p, s) in pss.items():
    assert math.gcd(p, 2 * K) == 1
J = {a + '/' + b: int(j) for a, b, j in re.findall(r'(\d+)/(\d+)\t(\d+)', t40)}
assert J == {'1/2': 2, '2/3': 4, '4/5': 8, '8/9': 16, '16/17': 32}
vykal = {int(a): sh for a, sh in re.findall(r'(\d+)/16\t([01]{32})', t42)}
assert sorted(vykal) == list(range(8, 17)) and all(sh.count('1') == 2 * a for a, sh in vykal.items())
dannye = {
    'откуда': 'патент US 7,673,213 B2 (TrellisWare), кодер 1500: табл. 38 (строка %d файла patenty/US7673213.txt), '
              '39 (строка %d), 40 (строка %d), 42 (строка %d); дизеры и p, s совпали с табл. 21–22 US 7,958,425 B2' % (n38, n39, n40, n42),
    'r': d[('All', 'r')],
    'w': {K: d[(K, 'w')] for (K, vid) in d if vid == 'w'},
    'p_s': {str(K): list(v) for K, v in sorted(pss.items())},
    'J': J,
    'выкалывание': {str(a): vykal[a] for a in sorted(vykal)},
    'перемежение_канала': 1367,
}
put = os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_fldpc.json')
with open(put, 'w', encoding='utf-8') as f:
    json.dump(dannye, f, ensure_ascii=False)
print('записано', put)
