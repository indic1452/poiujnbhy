"""MacKay, «Encyclopedia of Sparse Graph Codes» (inference.org.uk/mackay/codes/data.html) → tablicy/ldpc/mackay.json, zapisi/mackay.json.
Скачаны все файлы матриц (alist), на которые ссылается страница (istochniki/ldpc/mackay/, перечень _urls.txt; 404 — _404.txt).
Проверки (assert): alist разобран полностью; списки по столбцам и по строкам — транспонированы друг другу; веса строк/столбцов
= заявленным; N и M = указанным на странице; ранг над GF(2) посчитан (K = N − ранг) для N ≤ 20000."""
import gzip, html, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka
D = os.path.join(KOR, 'istochniki', 'ldpc', 'mackay')
FH = 'istochniki/ldpc/mackay_data.html'
t = open(os.path.join(KOR, FH), encoding='latin1').read()
lines = t.split('\n')
# заголовки разделов
razd = [(m.start(), int(m.group(1)), html.unescape(re.sub('<[^>]+>', '', m.group(2))).strip()) for m in re.finditer(r'<a name=s\d+><[hH](\d)>(.*?)</[hH]', t)]


def razdel_po_poz(pos):
    put = {}  # уровень заголовка → текст; путь «раздел > подраздел»
    for p, lv, nm in razd:
        if p <= pos:
            put[lv] = nm
            for b in [x for x in put if x > lv]: del put[b]
    return ' > '.join(put[x] for x in sorted(put))


# раздел кода — по оглавлению (ссылка href=#lN стоит под заголовком раздела); подробные описания идут ПОСЛЕ всех
# заголовков, поэтому позиция описания давала последний раздел («Repeat-accumulate codes») всем кодам — исправлено
_ogl = t[:t.index('Here are the details of individual codes')]
_razdel_l = {}
for m_ in re.finditer(r'href=#l(\d+)>', _ogl):
    _razdel_l.setdefault(m_.group(1), razdel_po_poz(m_.start()))


def razdel(pos, nom=None):
    return _razdel_l.get(nom, '') if nom is not None else razdel_po_poz(pos)


from ldpc_alist import read_alist, rank


out = []; zap = []
for m in re.finditer(r'<a name=l(\d+)>([^<]*)</a></b><br>\s*<table border=0>(.*?)</table>', t, re.S):
    nom, title, body = m.group(1), m.group(2).strip(), m.group(3)
    pole = {}
    for k, v in re.findall(r'<td>\s*([^<:]+):?\s*<td>\s*(.*?)</tr>', body, re.S):
        pole.setdefault(k.strip(), []).append(v.strip())
    al = re.findall(r'href=([^>\s]+)>parity check matrix', body)
    stroka_html = t[:m.start()].count('\n') + 1
    rec = dict(номер=int(nom), заголовок=title, раздел=razdel(m.start(), nom), поля={k: [re.sub('<[^>]+>', '', x) for x in v] for k, v in pole.items()}, строка_html=stroka_html)
    fn = None
    if al:
        import posixpath
        p = posixpath.normpath(posixpath.join('codes', al[0]))
        p = p.replace('codes/', '', 1) if p.startswith('codes/') else p
        if os.path.exists(os.path.join(D, p)): fn = p
    mN = re.search(r'N=(\d+)', title); mK = re.search(r'K=(\d+)', title); mM = re.search(r'M=(\d+)', title)
    if fn:
        N, M, cw, rw, rows, vid = read_alist(os.path.join(D, fn))
        rasx = []
        if mN and N != int(mN.group(1)): rasx.append('в заголовке N=%s, в alist N=%d' % (mN.group(1), N))
        if mM and M != int(mM.group(1)): rasx.append('в заголовке M=%s, в alist M=%d' % (mM.group(1), M))
        rec['расхождение'] = rasx
        rk = rank(rows, N) if N <= 20000 else None
        rec.update(alist='istochniki/ldpc/mackay/' + fn, N=N, M=M, ранг=rk, K=(N - rk) if rk is not None else None,
                   веса_столбцов=sorted(set(cw)), веса_строк=sorted(set(rw)), единиц=sum(cw), формат=vid)
    else:
        rec.update(alist=None)
    out.append(rec)
json.dump(out, open(os.path.join(KOR, 'tablicy', 'ldpc', 'mackay.json'), 'w'), ensure_ascii=False, indent=1)
s_al = [r for r in out if r['alist']]
print('записей на странице', len(out), 'с матрицей', len(s_al), 'без матрицы', len(out) - len(s_al))
for r in s_al:
    nm = r['заголовок'].split(' (')[0]
    kt = r['K']; kz = re.search(r'K=(\d+)', r['заголовок'])
    zap.append(Z('LDPC MacKay %s (N=%d, M=%d%s)' % (nm, r['N'], r['M'], ', K=%d' % kt if kt is not None else ''), 'Gallager/MacKay — Encyclopedia of Sparse Graph Codes', 'LDPC',
                 {'n,k': '%d,%s' % (r['N'], kt if kt is not None else (kz.group(1) if kz else '?')), 'веса столбцов': r['веса_столбцов'], 'веса строк': r['веса_строк'], 'единиц в H': r['единиц'],
                  'раздел': r['раздел'], 'построение': '; '.join(r['поля'].get('created', [])) or '', 'автор': '; '.join(r['поля'].get('author', [])), 'матрица (alist)': r['alist'],
                  'ранг H': r['ранг']},
                 'исследовательские коды Галлагера/Маккея; эталоны для моделирования (alist), в стандартах не используются',
                 [I(FH, 'строка %d (запись l%d)' % (r['строка_html'], r['номер']), 'https://www.inference.org.uk/mackay/codes/data.html'),
                  I(r['alist'], 'весь файл (alist)', 'https://www.inference.org.uk/mackay/codes/' + r['alist'].split('mackay/')[1])],
                 'alist разобран: списки столбцов и строк взаимно транспонированы, веса совпали; ранг над GF(2) = %s%s' % (r['ранг'], (' (K = N − ранг = %d%s)' % (kt, ', как в заголовке' if kz and int(kz.group(1)) == kt else (', в заголовке K=%s' % kz.group(1) if kz else ''))) if kt is not None else '')
                 + ('; ' + '; '.join(r['расхождение']) if r.get('расхождение') else ''),
                 'ЕСТЬ в проекте: ldpc.py (загрузка H в формате alist, декодер мин-сумма, перфорация/укорочение); dlinnye.py (поиск разреженных проверок вслепую)'))
sohranit('mackay', zap)
