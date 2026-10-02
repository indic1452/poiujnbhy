"""Сборка области obshchie: katalog.json (все записи zapisi/*.json), spisok_faylov.json (путь, url, sha256, размер — istochniki и
tablicy), svodka.json (счёт по семействам/видам, не найдено, пересечения с проектом)."""
import collections, glob, hashlib, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR
zap = []
for f in sorted(glob.glob(os.path.join(KOR, 'skripty', 'zapisi', '*.json'))):
    zap += json.load(open(f))
# проверка: все файлы источников существуют
for z in zap:
    for s in z['источник']:
        assert os.path.exists(os.path.join(KOR, s['файл'])), (z['имя'], s['файл'])
json.dump(zap, open(os.path.join(KOR, 'katalog.json'), 'w'), ensure_ascii=False, indent=1)
# URL файлов
url = {}
for sp in glob.glob(os.path.join(KOR, 'istochniki', '*', '_spisok.txt')):
    for l in open(sp):
        p = l.split()
        if len(p) >= 2 and p[-1].startswith('http'): url['istochniki/' + p[0]] = p[-1]
kom = {}
kf = os.path.join(KOR, 'istochniki', 'kod', '_kommity.txt')
for l in open(kf):
    n, u, h = l.split(); kom[n] = (u[:-4] if u.endswith('.git') else u, h)
mk = {}
for l in open(os.path.join(KOR, 'istochniki', 'ldpc', 'mackay', '_urls.txt')):
    p, u = l.split(); mk['istochniki/ldpc/mackay/' + p] = u
def url_dlya(rel):
    if rel in url: return url[rel]
    if rel.endswith('.txt') and rel[:-4] in url: return 'текст, извлечённый из ' + url[rel[:-4]]
    if rel in mk: return mk[rel]
    m = re.match(r'istochniki/kod/([^/]+)/(.*)', rel)
    if m and m.group(1) in kom: u, h = kom[m.group(1)]; return '%s/blob/%s/%s' % (u, h, m.group(2))
    m = re.match(r'istochniki/primitivnye/koopman_lfsr/(.*)', rel)
    if m: return 'https://users.ece.cmu.edu/~koopman/lfsr/' + m.group(1)
    m = re.match(r'istochniki/crc/koopman_crc/(.*)', rel)
    if m: return 'https://users.ece.cmu.edu/~koopman/crc/' + m.group(1)
    m = re.match(r'istochniki/crc/reveng-3.0.6/(.*)', rel)
    if m: return 'https://downloads.sourceforge.net/project/reveng/3.0.6/reveng-3.0.6.tar.gz (файл архива %s)' % m.group(1)
    if rel.startswith('tablicy/'): return 'собрано скриптом skripty/ из istochniki/'
    return ''
spis = []
for kat in ('istochniki', 'tablicy'):
    for root, dirs, files in os.walk(os.path.join(KOR, kat)):
        for fn in sorted(files):
            p = os.path.join(root, fn); rel = os.path.relpath(p, KOR)
            if os.path.islink(p) or not os.path.isfile(p): continue
            h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
            spis.append(dict(путь=rel, url=url_dlya(rel), sha256=h, размер=os.path.getsize(p)))
spis.sort(key=lambda x: x['путь'])
json.dump(spis, open(os.path.join(KOR, 'spisok_faylov.json'), 'w'), ensure_ascii=False, indent=1)
bez = [s['путь'] for s in spis if not s['url'] and s['путь'].startswith('istochniki') and not s['путь'].endswith('_spisok.txt')]
# сводка
po_sem = collections.Counter(z['семейство'] for z in zap)
po_vid = collections.Counter(z['вид'] for z in zap)
est = collections.Counter(('ЕСТЬ' if z['сложность внедрения'].startswith('ЕСТЬ') else 'ЧАСТИЧНО' if z['сложность внедрения'].startswith('ЧАСТИЧНО') else 'НЕТ/справочно') for z in zap)
svodka = dict(записей=len(zap), по_семействам=dict(po_sem.most_common()), по_видам=dict(po_vid.most_common()), в_проекте=dict(est),
              файлов=len(spis), объём_байт=sum(s['размер'] for s in spis), файлы_без_url=bez,
              не_найдено=json.load(open(os.path.join(KOR, 'skripty', 'ne_naydeno.json'))))
_pr = os.path.join(KOR, 'skripty', 'proverka')
if os.path.exists(os.path.join(_pr, 'rezultaty.json')):
    svodka['независимая_проверка'] = {k: {x: (y if not isinstance(y, (list, dict)) or x != 'ошибок' else len(y)) for x, y in v['результат'].items() if x not in ('p_bch3', 'примеры', 'полей')}
                                      for k, v in json.load(open(os.path.join(_pr, 'rezultaty.json'))).items()}
if os.path.exists(os.path.join(_pr, 'ispravleniya.json')):
    svodka['исправления_проверки'] = json.load(open(os.path.join(_pr, 'ispravleniya.json')))
json.dump(svodka, open(os.path.join(KOR, 'svodka.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(dict(записей=len(zap), в_проекте=dict(est), файлов=len(spis), без_url=len(bez)), ensure_ascii=False))
print(bez[:30])
