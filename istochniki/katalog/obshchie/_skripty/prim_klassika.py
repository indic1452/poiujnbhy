"""Классические таблицы примитивных многочленов: Watson (Math. Comp. 16, 1962; n ≤ 100, 118, 127) и Stahnke (Math. Comp. 27, 1973; n ≤ 168)
→ tablicy/primitivnye/watson_stahnke.json, zapisi/prim_klassika.json.
Скан → OCR (tesseract) → строки «n  показатели… 0». Принимаются только строки, многочлен которых проходит проверку:
неприводимость (Бен-Ор) для всех и примитивность (порядок x = 2^n−1) для n ≤ 100; непринятые — в списке «не_распознано»."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf2 import primitive, irreducible, poly_str
from zap import KOR, I, Z, sohranit
FIX = str.maketrans({'O': '0', 'o': '0', 'C': '0', 'l': '1', 'i': '1', 'I': '1', '|': '1', 'S': '5', 'B': '8', '#': ' ', '«': ' ', '=': ' ', '~': ' ', '—': ' ', '(': ' ', ')': ' ', ',': ' ', '.': ' ', "'": ' ', '°': ' ', '°': ' '})
def razbor(f, nmax):
    t = open(os.path.join(KOR, f)).read()
    prin = {}; plohie = []
    for line in t.split('\n'):
        s = line.translate(FIX)
        toks = [int(x) for x in re.findall(r'\d+', s)]
        # строка содержит 1–2 записи «n e1 … 0»: делим по нулям
        cur = []
        for x in toks:
            cur.append(x)
            if x == 0:
                if len(cur) >= 2:
                    n, ex = cur[0], cur[1:-1]
                    if 1 <= n <= nmax and all(e < n for e in ex) and len(set(ex)) == len(ex) and sorted(ex, reverse=True) == ex and n not in prin:
                        v = (1 << n) | 1
                        for e in ex: v |= 1 << e
                        ok = irreducible(v) and (n > 100 or primitive(v))
                        if ok: prin[n] = dict(n=n, показатели=ex, hex=hex(v), запись=poly_str(v), проверка='примитивен (расчёт)' if n <= 100 else 'неприводим (расчёт)')
                        else: plohie.append(line.strip())
                cur = []
    return prin, plohie
res = {}; zap = []
for f, nmax, name, url in (('istochniki/primitivnye/watson1962_mcom.pdf.txt', 127, 'Watson 1962', 'https://www.ams.org/journals/mcom/1962-16-079/S0025-5718-1962-0148256-1/S0025-5718-1962-0148256-1.pdf'),
                           ('istochniki/primitivnye/stahnke1973_mcom.pdf.txt', 168, 'Stahnke 1973', 'https://www.ams.org/journals/mcom/1973-27-124/S0025-5718-1973-0327722-7/S0025-5718-1973-0327722-7.pdf')):
    prin, plohie = razbor(f, nmax)
    res[name] = dict(принято=prin, не_распознано=plohie)
    print(name, 'принято степеней', len(prin), 'не распознано строк', len(plohie))
    zap.append(Z('Примитивные многочлены GF(2): %s — по одному на степень (принято из OCR %d степеней)' % (name, len(prin)), 'Примитивные многочлены (таблицы)', 'таблица (примитивные многочлены)',
                 {'объём': 'n = 1…%d' % nmax if name.startswith('Stahnke') else 'n = 1…100, 107, 127', 'принято': sorted(prin), 'таблица': 'tablicy/primitivnye/watson_stahnke.json',
                  'замечание': 'Stahnke — наименьшее число членов для каждой степени (аннотация статьи)' if name.startswith('Stahnke') else 'Watson — по одному примитивному многочлену'},
                 'классика LFSR/ПСП', [I(f[:-4], 'таблица (OCR: %s)' % os.path.basename(f), url)],
                 'принимались только строки OCR, чей многочлен неприводим (все) и примитивен (n ≤ 100) — расчёт; остальные строки сканa — не распознаны (%d)' % len(plohie),
                 'ЕСТЬ в проекте: rs_bch.примитивные (порождение и проверка)'))
json.dump(res, open(os.path.join(KOR, 'tablicy', 'primitivnye', 'watson_stahnke.json'), 'w'), ensure_ascii=False, indent=1)
sohranit('prim_klassika', zap)
