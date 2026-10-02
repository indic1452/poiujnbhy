"""Записи katalog.json для примитивных узкосмысловых БЧХ n = 2^m − 1, m = 3…10 (по tablicy/bch/bch_primitivnye_m3_10.json,
собранной bch_tablica.py с assert). Одна запись на код (n, k, t); для m = 8 — g при обоих p(x) (bch3.c и Linux lib/bch.c)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka

T = json.load(open(os.path.join(KOR, 'tablicy', 'bch', 'bch_primitivnye_m3_10.json')))
F_B3 = 'istochniki/bch/eccpage_bch3.c'
F_LX = 'istochniki/bch/linux_lib_bch.c'
F_HAN = 'istochniki/bch/ntpu_BCH_code.pdf'
osn = {}
for tb in T:
    osn.setdefault(tb['m'], []).append(tb)
zap = []
for m in sorted(osn):
    glav = osn[m][0]; alt = osn[m][1] if len(osn[m]) > 1 else None
    altk = {r['k']: r for r in alt['коды']} if alt else {}
    for r in glav['коды']:
        n, k, t = r['n'], r['k'], r['t']
        par = {'n,k': '%d,%d' % (n, k), 't': t, 'd конструктивное': r['d_конструктивное'], 'n−k': r['степень'],
               'поле': 'GF(2^%d), p(x) = %s (%s)' % (m, glav['p_запись'], glav['p_hex']),
               'g(x) восьмеричная': r['g_octal'], 'g(x) hex': r['g_hex'],
               'g(x) запись': r['g_запись'] or 'см. g(x) восьмеричная (степень %d)' % r['степень'],
               'минимальные многочлены (восьм.)': ', '.join(r['мин_многочлены']),
               'корни': 'α^1 … α^%d (узкосмысловой, fcr = 1) и их сопряжённые' % (2 * t),
               'порядок бит': 'восьмеричная/hex запись — старшая степень слева; систематический кодер: остаток от x^(n−k)·u(x) mod g(x)',
               'укорочение': 'стандарты часто укорачивают (n−s, k−s) — тот же g(x)',
               'таблица': 'tablicy/bch/bch_primitivnye_m3_10.json'}
        if alt and k in altk:
            a = altk[k]
            par['вариант при p(x) = %s (%s, Linux lib/bch.c)' % (alt['p_запись'], alt['p_hex'])] = 'g восьм. %s, t=%d' % (a['g_octal'], a['t'])
        ist = [I(F_B3, 'строки %d–%d (выбор p(x) для m=%d), %d–… (gen_poly: циклотомические классы, НОК)' % (
                    stroka(F_B3, 'if (m == 3)'), stroka(F_B3, 'if (m == 10)'), m, stroka(F_B3, 'gen_poly()', 170)))]
        if m >= 5:
            ist.append(I(F_LX, 'строка %d (prim_poly_tab)' % stroka(F_LX, 'prim_poly_tab[] = {')))
        if n == 63:
            ist.append(I(F_HAN, 'стр. 14 (разложение g(x) БЧХ n=63 на минимальные многочлены)'))
        prov = r['проверка'] + '; таблица собрана программно, все assert пройдены'
        zap.append(Z('БЧХ (%d,%d) t=%d, примитивный узкосмысловой' % (n, k, t), 'БЧХ примитивные (таблица n ≤ 1023)', 'БЧХ', par,
                     'общая таблица для вслепую распознавания; укороченные/расширенные варианты — DVB-S2/T2 (внешний БЧХ, m=14/16 — область efir/kosmos), POCSAG (31,21), P25 NID (63,16), Bluetooth (64,30), флеш-память (Linux lib/bch), CCSDS TC (63,56)',
                     ist, prov,
                     'ЕСТЬ в проекте: rs_bch.опознать (корни по примитивным p(x) степени m, n−k и t) и rs_bch.исправить (Берлекэмп — Мэсси); готовой таблицы g(x) нет — для подписи можно взять tablicy/bch'))
sohranit('bch', zap)
