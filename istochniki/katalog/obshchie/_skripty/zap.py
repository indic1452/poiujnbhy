"""Общие функции для записей katalog.json области obshchie."""
import json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZAP = os.path.join(KOR, 'skripty', 'zapisi')
os.makedirs(ZAP, exist_ok=True)
_URL = {}


def _urls():
    if _URL: return _URL
    ist = os.path.join(KOR, 'istochniki')
    for d in os.listdir(ist):
        f = os.path.join(ist, d, '_spisok.txt')
        if os.path.exists(f):
            for l in open(f):
                p = l.split()
                if len(p) >= 2 and p[-1].startswith('http'): _URL['istochniki/' + p[0]] = p[-1]
    return _URL


def I(fail, gde, url=None):
    """Ссылка на источник: файл (относительно папки области), страница|строки, url (из _spisok.txt, если не задан)."""
    if url is None:
        url = _urls().get(fail, '')
        if not url and fail.endswith('.txt'): url = _urls().get(fail[:-4], '')
    return {'файл': fail, 'страница|строки': gde, 'url': url}


def Z(имя, семейство, вид, параметры, где, источник, проверка, сложность):
    return {'имя': имя, 'семейство': семейство, 'область': 'obshchie', 'вид': вид, 'параметры': параметры,
            'где применяется': где, 'источник': источник, 'проверка': проверка, 'сложность внедрения': сложность}


def sohranit(imya, zapisi):
    for z in zapisi:
        assert z['источник'] and all('файл' in s for s in z['источник']), z['имя']
        for s in z['источник']:
            p = os.path.join(KOR, s['файл'])
            assert os.path.exists(p), (z['имя'], s['файл'])
    json.dump(zapisi, open(os.path.join(ZAP, imya + '.json'), 'w'), ensure_ascii=False, indent=1)
    print(imya, len(zapisi), 'записей')


def stroka(fail, obrazec, start=0):
    """Номер строки (с 1) первого вхождения образца в текстовом файле."""
    for i, l in enumerate(open(os.path.join(KOR, fail), encoding='utf-8', errors='replace')):
        if i + 1 >= start and obrazec in l: return i + 1
    raise AssertionError((fail, obrazec))


def stranica(fail_txt, obrazec):
    """Номер страницы PDF (метки =====PAGE n=====), где встречается образец (пробелы игнорируются)."""
    t = open(os.path.join(KOR, fail_txt), encoding='utf-8', errors='replace').read()
    norm = lambda s: re.sub(r'\s+', '', s)
    for blok in t.split('=====PAGE ')[1:]:
        n, _, body = blok.partition('=====')
        if norm(obrazec) in norm(body): return int(n)
    raise AssertionError((fail_txt, obrazec))


def str_po_stroke(fail_txt, nomer):
    """Страница PDF по номеру строки текстового файла (последняя метка =====PAGE n===== или =====СТР n===== выше строки)."""
    st = None
    for i, l in enumerate(open(os.path.join(KOR, fail_txt), encoding='utf-8', errors='replace')):
        if i + 1 > nomer: break
        m = re.match(r'=====(?:PAGE|СТР) (\d+)=====', l)
        if m: st = int(m.group(1))
    return st


def gde(fail_txt, obrazec, start=0):
    """'стр. N PDF, строка M текста' для первого вхождения образца."""
    n = stroka(fail_txt, obrazec, start)
    return 'стр. %s PDF (строка %d в %s)' % (str_po_stroke(fail_txt, n), n, os.path.basename(fail_txt))
