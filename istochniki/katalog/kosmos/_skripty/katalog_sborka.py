"""Сборка katalog.json области «kosmos».

Каждая запись собирается в skripty/zapisi/*.py (функция zapisi(S, Z)). Ссылки на источник строятся
функцией S(файл, шаблон): она САМА находит страницу PDF (по .txt с метками =====PAGE n=====) или строку
кода/текста, где встречается шаблон — если шаблон не найден, сборка падает (ссылка не может быть выдуманной).
Код из репозиториев копируется в istochniki/kod/<репозиторий>/ вместе с лицензией; URL — на GitHub с хэшем коммита.

Выход: katalog.json, spisok_faylov.json (путь, URL, sha256, размер), svodka.json.
"""
import glob, hashlib, importlib.util, json, os, re, shutil, sys

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki')
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
LIBFEC = os.path.join(os.path.dirname(os.path.dirname(KOR)), 'kody', 'istochniki', 'prochie', 'libfec')

# ---------------------------------------------------------------- URL из описей скачивания
URL = {}


def _opisi():
    for f in glob.glob(os.path.join(IST, '*', '_spisok.json')):
        d = os.path.basename(os.path.dirname(f))
        for e in json.load(open(f)):
            URL['istochniki/%s/%s' % (d, e['file'])] = e['url']
    for f in glob.glob(os.path.join(IST, '*', '_etsi.jsonl')) + glob.glob(os.path.join(IST, '*', '_itu.jsonl')):
        d = os.path.basename(os.path.dirname(f))
        for l in open(f):
            e = json.loads(l)
            URL['istochniki/%s/%s' % (d, e['file'])] = e['url']
    for f in glob.glob(os.path.join(IST, '*', '_spisok.txt')):
        d = os.path.basename(os.path.dirname(f))
        for l in open(f):
            p = l.split()
            if len(p) >= 2 and p[-1].startswith('http'):
                URL['istochniki/' + (p[0] if '/' in p[0] else d + '/' + p[0])] = p[-1]


REPO = {}


def _repo():
    for l in open(os.path.join(IST, 'kod', '_repozitorii.txt')):
        n, h, u = l.split()
        REPO[n] = (h, u[:-4] if u.endswith('.git') else u)


_opisi()
_repo()
LIC = {'aff3ct': 'LICENSE', 'aff3ct_conf': None, 'ldpc-toolbox': 'LICENSE-MIT', 'labrador-ldpc': 'LICENSE', 'PocketSDR': 'LICENSE.txt',
       'gr-satellites': 'LICENSE', 'JAERO': 'LICENSE', 'osmo-gmr': 'COPYING', 'iridium-toolkit': 'README.md', 'direwolf': 'LICENSE',
       'goestools': 'LICENSE', 'SatDump': 'LICENSE', 'meteor_decoder': None, 'gnss-sdr': 'COPYING', 'leansdr': 'LICENSE', 'gr-iridium': 'COPYING',
       'gr-dvbs2rx': 'LICENSE'}


def _kod(put):
    """kod/<repo>/<path> → копирование из repos (и лицензии)."""
    ch = put.split('/')
    repo, vnutr = ch[1], '/'.join(ch[2:])
    dst = os.path.join(IST, put)
    if repo == 'libfec':
        src = os.path.join(LIBFEC, vnutr)
        url = 'https://github.com/quiet/libfec/blob/9750ca0a6d0a786b506e44692776b541f90daa91/' + vnutr
        lic = 'lesser.txt'
        srcroot = LIBFEC
    else:
        src = os.path.join(REPOS, repo, vnutr)
        h, u = REPO[repo]
        url = '%s/blob/%s/%s' % (u, h, vnutr)
        lic = LIC.get(repo)
        srcroot = os.path.join(REPOS, repo)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    if lic:
        ld = os.path.join(IST, 'kod', repo, lic)
        if not os.path.exists(ld) and os.path.exists(os.path.join(srcroot, lic)):
            shutil.copy2(os.path.join(srcroot, lic), ld)
    return url


def _stranica(txt_path, shablon, n=1):
    t = open(txt_path, encoding='utf8', errors='replace').read()
    t = ''.join(chr(ord(ch) - 0xF000) if 0xF030 <= ord(ch) <= 0xF039 else ch for ch in t)
    pat = re.compile(shablon, re.I | re.S) if isinstance(shablon, str) else shablon
    # поиск по странице с нормализацией пробелов
    str_ = re.split(r'=====PAGE (\d+)=====\n', t)
    naid = []
    for i in range(1, len(str_), 2):
        norm = ' '.join(str_[i + 1].split())
        if pat.search(norm):
            naid.append(int(str_[i]))
    return naid


def S(fayl, shablon, url=None, vse=False, prim=None):
    """Ссылка на источник: fayl — путь от kosmos/ (istochniki/...), либо 'kod/<repo>/<path>'."""
    if fayl.startswith('kod/'):
        url = url or _kod(fayl)
        fayl = 'istochniki/' + fayl
    polnyi = os.path.join(KOR, fayl)
    assert os.path.exists(polnyi), fayl
    zap = {'файл': fayl}
    if fayl.lower().endswith('.pdf'):
        stranicy = _stranica(polnyi + '.txt', shablon)
        assert stranicy, ('шаблон не найден', fayl, shablon)
        zap['страница|строки'] = 'стр. ' + (', '.join(map(str, stranicy)) if vse else str(stranicy[0])) + ' (номер страницы PDF)'
    else:
        stroki = open(polnyi, encoding='utf8', errors='replace').read().split('\n')
        pat = re.compile(shablon, re.I)
        nomera = [i + 1 for i, l in enumerate(stroki) if pat.search(l)]
        assert nomera, ('шаблон не найден', fayl, shablon)
        zap['страница|строки'] = 'строки ' + (', '.join(map(str, nomera[:12])) if vse else str(nomera[0]))
        if url and 'github.com' in url and '#L' not in url:
            url = url + '#L%d' % nomera[0]
    zap['url'] = url or URL.get(fayl, '')
    if not zap['url'] and fayl.startswith('istochniki/') and not fayl.startswith('istochniki/kod/'):
        raise AssertionError('нет URL для ' + fayl)
    if prim:
        zap['примечание'] = prim
    return zap


POLYA = ['имя', 'семейство', 'область', 'вид', 'параметры', 'где применяется', 'источник', 'проверка', 'сложность внедрения']


def Z(imya, semeistvo, vid, parametry, gde, istochnik, proverka, vnedrenie, oblast='kosmos'):
    assert istochnik, imya
    return {'имя': imya, 'семейство': semeistvo, 'область': oblast, 'вид': vid, 'параметры': parametry, 'где применяется': gde,
            'источник': istochnik, 'проверка': proverka, 'сложность внедрения': vnedrenie}


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


if __name__ == '__main__':
    vse = []
    for f in sorted(glob.glob(os.path.join(KOR, 'skripty', 'zapisi', '*.py'))):
        spec = importlib.util.spec_from_file_location(os.path.basename(f)[:-3], f)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        z = m.zapisi(S, Z)
        for e in z:
            assert list(e) == POLYA, e.get('имя')
        print('%-14s %3d записей' % (os.path.basename(f)[:-3], len(z)))
        vse += z
    # поправки сверки-2 (исправления по первоисточникам, удаление повторов ссылок, итоги сверки в «проверка»)
    sys.path.insert(0, os.path.join(KOR, 'skripty', 'sverka2'))
    from popravki import popravki
    vse = popravki(vse, S)
    for e in vse:
        assert list(e) == POLYA and e['источник'] and all(s.get('файл') and s.get('страница|строки') for s in e['источник']), e['имя']
    imena = [e['имя'] for e in vse]
    dubli = {x for x in imena if imena.count(x) > 1}
    assert not dubli, dubli
    json.dump(vse, open(os.path.join(KOR, 'katalog.json'), 'w'), ensure_ascii=False, indent=1)
    # опись файлов
    fayly = []
    for kor in ('istochniki', 'tablicy', 'skripty'):
        for p in sorted(glob.glob(os.path.join(KOR, kor, '**', '*'), recursive=True)):
            if os.path.isfile(p) and not p.endswith('.pyc') and '__pycache__' not in p:
                rel = os.path.relpath(p, KOR)
                url = URL.get(rel, '')
                if not url and rel.startswith('istochniki/kod/'):
                    ch = rel.split('/')
                    if ch[2] in REPO:
                        url = '%s/blob/%s/%s' % (REPO[ch[2]][1], REPO[ch[2]][0], '/'.join(ch[3:]))
                    elif ch[2] == 'libfec':
                        url = 'https://github.com/quiet/libfec/blob/9750ca0a6d0a786b506e44692776b541f90daa91/' + '/'.join(ch[3:])
                fayly.append({'путь': rel, 'url': url, 'sha256': sha(p), 'размер': os.path.getsize(p)})
    json.dump(fayly, open(os.path.join(KOR, 'spisok_faylov.json'), 'w'), ensure_ascii=False, indent=1)
    sem = {}
    for e in vse:
        sem[e['семейство']] = sem.get(e['семейство'], 0) + 1
    json.dump({'записей': len(vse), 'по_семействам': sem, 'файлов': len(fayly)}, open(os.path.join(KOR, 'svodka.json'), 'w'), ensure_ascii=False, indent=1)
    print('ИТОГО записей:', len(vse), 'файлов:', len(fayly))
