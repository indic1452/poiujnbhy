"""Проверка ссылок всех записей: обязательные поля, файл существует, «стр. N PDF» ≤ числа страниц PDF,
«строка N» ≤ числа строк текста (текст — сам файл, файл.txt или копия в ../<область>/tekst/…), url непустой."""
import json, os, re, sys
import pymupdf
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))
POLYA = ['имя', 'семейство', 'область', 'вид', 'параметры', 'где применяется', 'источник', 'проверка', 'сложность внедрения']
stranic = {}; strok = {}
def n_str(p):
    if p not in stranic:
        try: stranic[p] = len(pymupdf.open(p))
        except Exception: stranic[p] = None
    return stranic[p]
def tekst(f):
    cand = [f if not f.endswith('.pdf') else None, f + '.txt']
    m = re.match(r'(\.\./[^/]+)/istochniki/(.*)', f)
    if m: cand.append(os.path.join(m.group(1), 'tekst', m.group(2) + '.txt'))
    for c in cand:
        if c and os.path.exists(os.path.join(KOR, c)) and not c.endswith(('.pdf', '.gz', '.png')): return c
def n_strok(c):
    if c not in strok: strok[c] = sum(1 for _ in open(os.path.join(KOR, c), 'rb'))
    return strok[c]
osh = []; imena = set()
for r in kat:
    for p in POLYA:
        if p not in r or r[p] in (None, '', [], {}): osh.append((r.get('имя'), 'нет поля', p))
    if r['имя'] in imena: osh.append((r['имя'], 'повтор имени'))
    imena.add(r['имя'])
    if r['область'] != 'obshchie': osh.append((r['имя'], 'область', r['область']))
    for s in r['источник']:
        f = s['файл']; fp = os.path.join(KOR, f)
        if not os.path.exists(fp): osh.append((r['имя'], 'нет файла', f)); continue
        if not s.get('url'): osh.append((r['имя'], 'нет url', f))
        ss = s['страница|строки']
        m = re.search(r'стр\. (\d+) PDF', ss)
        if m and f.endswith('.pdf'):
            N = n_str(fp)
            if N and int(m.group(1)) > N: osh.append((r['имя'], 'страница > числа страниц', f, m.group(1), N))
        m = re.search(r'строк[аи] (\d+)', ss)
        if m:
            t = tekst(f)
            if t is None: osh.append((r['имя'], 'нет текста для номера строки', f))
            elif int(m.group(1)) > n_strok(t): osh.append((r['имя'], 'строка > числа строк', t, m.group(1)))
print(json.dumps(dict(записей=len(kat), ошибок=len(osh), примеры=osh[:60]), ensure_ascii=False, indent=0))
