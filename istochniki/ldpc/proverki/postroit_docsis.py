"""DOCSIS 3.1 (восходящий канал) LDPC: (16200, 14400) L = 360, (5940, 5040) L = 180, (1120, 840) L = 56.

Источник — CableLabs CM-SP-PHYv3.1-I15-180926, 7.4.3.2, стр. 59–60 PDF: «All three LDPC encoders are
systematic … c = (i0, …, iN−M−1, p0, …, pM−1)», базовые матрицы 5 × n, «−» — нулевой блок, число — единичная,
циклически сдвинутая вправо. Сами матрицы в PDF — картинки (стр. 60, три рисунка): они извлекаются из PDF
как есть, режутся по линиям сетки, «-» узнаётся по высоте чернил, цифры — сравнением с шаблонами шрифта
(средние картинки цифр; tesseract в этом шрифте путал 7 и 1, 9 и 8). Второй источник — текстовые таблицы 101–3…101–5 черновика IEEE 802.3bn
(EPoC, hajduczenia_3bn_01_0913, стр. 3–5; «matrices were extracted from prodan_3bn_01b_0713»): прочитанное
с картинок сверяется с ним число в число. Расхождение одно, и оно записано: у (1120, 840) в строке 5
столбце 20 в DOCSIS 3.1 — 1, в черновике 802.3bn — «−1» (черновик — «not yet adopted»; принят DOCSIS:
опубликованная спецификация). Сверка: ранг H = m·L (k = (n − m)·L по табл. стандарта), проверочная часть
(последние 5 блок-столбцов) — полного ранга, т. е. данные — первые k позиций, как в 7.4.3.2.
Пишет src/reportgen/potok/data/ldpc_docsis.json. Запуск из корня репозитория (нужны pymupdf и Pillow).
"""
import io
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

KODY = ((16200, 14400, 360, 45), (5940, 5040, 180, 33), (1120, 840, 56, 20))

# -- черновик IEEE 802.3bn: текстовые таблицы -------------------------------------------------------
tekst = open(os.path.join(K0, 'standarty', 'ieee802.3bn_hajduczenia_3bn_01_0913.pdf.txt'), encoding='utf-8').read()
# Номера строк на полях страниц (1…54 после «=====PAGE») — не данные: вырезаем их вместе с заголовком страницы.
tekst = re.sub(r'=====PAGE \d+=====\s*\n\d+\n(?:\d+\n){54}', '\n', tekst)
chernovik = {}
for n, k, L, stolbcov in KODY:
    H = [[None] * stolbcov for _ in range(5)]
    for m_ in re.finditer(r'LDPC \(%d, %d\) code matrix, columns (\d+)-(\d+)\s*\nRow\s*\nColumn\s*\n' % (n, k), tekst):
        ot, do = int(m_.group(1)), int(m_.group(2))
        shirina = do - ot + 1
        chisla = [int(x) for x in re.findall(r'-?\d+', tekst[m_.end():m_.end() + 4000])]
        assert chisla[:shirina] == list(range(ot, do + 1)), ('заголовок столбцов', n, ot, chisla[:shirina])
        chisla = chisla[shirina:]
        for r in range(5):
            assert chisla[0] == r + 1, ('номер строки', n, ot, r, chisla[:3])
            H[r][ot - 1:do] = chisla[1:1 + shirina]
            chisla = chisla[1 + shirina:]
    assert all(x is not None for ryad in H for x in ryad), ('не все столбцы', n)
    assert all(-1 <= x < L for ryad in H for x in ryad), ('сдвиг вне 0…L−1', n)
    chernovik[n] = H
print('802.3bn: три базовые матрицы прочитаны из текста (5×45, 5×33, 5×20)')

# -- DOCSIS 3.1: картинки стр. 60, шаблоны цифр --------------------------------------------------------
import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

d = pymupdf.open(os.path.join(K0, 'standarty', 'CableLabs_CM-SP-PHYv3.1-I15-180926_DOCSIS31_PHY.pdf'))
stranica = d[59]
assert 'Rate= 75% (1120, 840) code, m=5 rows x n=20 columns, L=56' in stranica.get_text()
kartinki = stranica.get_images()
assert len(kartinki) == 3
docsis = {}


def granicy(x):
    g = [int(x[0])]
    for v in x[1:]:
        if v - g[-1] > 3:
            g.append(int(v))
        else:
            g[-1] = int(v)
    return g


def kuski(kletka):
    """Куски чернил ячейки: (картинка строк с чернилами, [(от, до) столбцов]), разделены пустыми столбцами."""
    a = np.array(kletka) < 128
    stroki = np.flatnonzero(a.any(axis=1))
    a = a[stroki[0]:stroki[-1] + 1]
    est = a.any(axis=0)
    itog, c = [], 0
    while c < len(est):
        if not est[c]:
            c += 1
            continue
        d = c
        while d < len(est) and est[d]:
            d += 1
        itog.append((c, d))
        c = d
    return a, itog


def glify(a, kuski_, shirina):
    """Цифры ячейки в сетке 10 × 14; слипшиеся цифры (кусок шире полутора цифр) делятся поровну."""
    itog = []
    for c, d in kuski_:
        chast = max(1, round((d - c) / shirina))
        for q in range(chast):
            x0, x1 = c + (d - c) * q // chast, c + (d - c) * (q + 1) // chast
            g = Image.fromarray((~a[:, x0:x1]).astype(np.uint8) * 255).resize((10, 14), Image.BILINEAR)
            itog.append(np.array(g, dtype=float) / 255)
    return itog


# Ячейки всех трёх таблиц: «-» — по высоте чернил (черта — пара пикселей), иначе — цифры.
kletki = {}
for (n, k, L, stolbcov), kartinka in zip(KODY, kartinki, strict=True):
    im = Image.open(io.BytesIO(d.extract_image(kartinka[0])['image'])).convert('L')
    # Сетка таблицы — по тёмным линиям: столбцы и строки режутся по ним.
    a = np.array(im) < 128
    gs, gr = granicy(np.flatnonzero(a.mean(axis=0) > 0.6)), granicy(np.flatnonzero(a.mean(axis=1) > 0.6))
    assert (len(gs) - 1, len(gr) - 1) == (stolbcov, 5), ('сетка', n, len(gs), len(gr))
    for r in range(5):
        for c in range(stolbcov):
            kl = im.crop((gs[c] + 3, gr[r] + 3, gs[c + 1] - 2, gr[r + 1] - 2))
            chernila = np.flatnonzero((np.array(kl) < 128).any(axis=1))
            assert len(chernila), ('пустая ячейка', n, r, c)
            kletki[(n, r, c)] = None if chernila[-1] - chernila[0] + 1 <= 4 else kuski(kl)
# Ширина одной цифры — самая частая ширина куска чернил (одиночные цифры встречаются чаще слипшихся).
shiriny = [d - c for v in kletki.values() if v is not None for c, d in v[1]]
shirina = max(set(shiriny), key=shiriny.count)
kletki = {kl: None if v is None else glify(v[0], v[1], shirina) for kl, v in kletki.items()}

# Шаблоны цифр — средние картинки цифр по всем ячейкам с подписями из 802.3bn (там, где число цифр
# совпало). Картинка каждой цифры потом сравнивается со всеми шаблонами и берётся ближайший: подпись
# 802.3bn лишь учит шрифт — ячейка, где на картинке другое число, прочитается как на картинке
# (так и нашлась ячейка (1120, 840) строки 5 столбца 20).
obrazcy = {str(z): [] for z in range(10)}
for (n, r, c), glifs in kletki.items():
    metka = chernovik[n][r][c]
    if glifs is not None and metka >= 0 and len(str(metka)) == len(glifs):
        for z, g in zip(str(metka), glifs, strict=True):
            obrazcy[z].append(g)
assert all(len(v) >= 5 for v in obrazcy.values()), {z: len(v) for z, v in obrazcy.items()}
shablony = {z: np.mean(v, axis=0) for z, v in obrazcy.items()}
for (n, k, L, stolbcov) in KODY:
    docsis[n] = [[-1 if kletki[(n, r, c)] is None else
                  int(''.join(min(shablony, key=lambda z: float(((g - shablony[z]) ** 2).sum()))
                              for g in kletki[(n, r, c)]))
                  for c in range(stolbcov)] for r in range(5)]
    print(f'DOCSIS 3.1 ({n}, {k}): картинка прочитана, {stolbcov} столбцов')

# -- сверка двух источников и ранг --------------------------------------------------------------------
IZVESTNO = {(1120, 4, 19): (1, -1)}          # (n, строка, столбец): (DOCSIS 3.1, 802.3bn)
kody = {}
for n, k, L, stolbcov in KODY:
    raznye = {(n, r, c): (docsis[n][r][c], chernovik[n][r][c]) for r in range(5) for c in range(stolbcov)
              if docsis[n][r][c] != chernovik[n][r][c]}
    assert raznye == {kl: v for kl, v in IZVESTNO.items() if kl[0] == n}, ('DOCSIS и 802.3bn различаются', raznye)
    H = np.zeros((5 * L, stolbcov * L), dtype=np.uint8)
    for r in range(5):
        for c in range(stolbcov):
            s = docsis[n][r][c]
            if s >= 0:
                H[r * L + np.arange(L), c * L + (np.arange(L) + s) % L] = 1
    rang = gf2.ранг(H)
    assert rang == 5 * L and stolbcov * L == n and n - rang == k, ('ранг', n, rang)
    assert gf2.ранг(H[:, k:]) == 5 * L, ('проверочная часть не полного ранга', n)
    kody[f'docsis31-{n}-{k}'] = {
        'семейство': 'DOCSIS 3.1 (восходящий)', 'n': n, 'k': k, 'L': L, 'скорость': f'{k}/{n}', 'база': docsis[n],
        'откуда': f'CableLabs DOCSIS 3.1 PHY I15, 7.4.3.2, стр. 60 (картинка, прочитана по шаблонам цифр), L = {L}; сверено с '
                  'текстом IEEE 802.3bn (hajduczenia_3bn_01_0913, табл. 101–3…101–5)'
                  + ('; в строке 5 столбце 20 у 802.3bn «−1», у DOCSIS 3.1 — 1 (принято DOCSIS)' if n == 1120 else '')
                  + f'; ранг H = {rang}'}
    print(f'DOCSIS 3.1 ({n}, {k}): совпало с 802.3bn' + (' (кроме известного (5, 20))' if n == 1120 else '')
          + f', ранг {rang}, данные — первые {k}')
json.dump(kody, open(os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_docsis.json'), 'w', encoding='utf-8'),
          ensure_ascii=False)
print('ВСЁ СОВПАЛО')
