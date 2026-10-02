"""Скачать PDF ETSI: для каждого пути deliver берётся последняя версия в каждой главной ветке (vMM.*).
Использование: etsi_get.py <папка_назначения> <путь deliver> [all]"""
import re, subprocess, sys, os, json
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
def get(url):
    return subprocess.run(['curl', '-sL', '-A', UA, '-m', '120', url], capture_output=True).stdout.decode('latin1')
dst, path = sys.argv[1], sys.argv[2].strip('/')
allmaj = len(sys.argv) > 3
base = 'https://www.etsi.org/deliver/' + path + '/'
vers = sorted(set(re.findall(r'/(\d\d\.\d\d\.\d\d_60)/', get(base))))
if not vers:
    print('НЕТ ВЕРСИЙ', path); sys.exit()
pick = {}
for v in vers:
    pick[v[:2]] = v
sel = list(pick.values()) if allmaj else [vers[-1]]
os.makedirs(dst, exist_ok=True)
log = os.path.join(dst, '_etsi.jsonl')
for v in sel:
    lst = get(base + v + '/')
    pdfs = re.findall(r'HREF="([^"]+\.pdf)"', lst, re.I)
    for p in pdfs:
        url = 'https://www.etsi.org' + p
        fn = os.path.join(dst, p.split('/')[-1])
        if not os.path.exists(fn):
            subprocess.run(['curl', '-sL', '-A', UA, '-m', '600', '-o', fn, url])
        print(path, v, os.path.getsize(fn), fn)
        with open(log, 'a') as f:
            f.write(json.dumps({'path': path, 'ver': v, 'url': url, 'file': os.path.basename(fn)}) + '\n')
