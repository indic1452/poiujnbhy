"""Показ записи и текста цитируемых страниц — для построчной сверки."""
import json, re, sys, os
KOR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
d = json.load(open(os.path.join(KOR, 'katalog.json')))
def stranica(f, p):
    t = open(os.path.join(KOR, f + '.txt'), errors='replace').read()
    ch = re.split(r'=====PAGE (\d+)=====\n', t)
    for i in range(1, len(ch), 2):
        if int(ch[i]) == p: return ch[i + 1]
    return ''
if __name__ == '__main__':
    i = int(sys.argv[1]); polno = len(sys.argv) > 2
    e = d[i]
    print(json.dumps({k: e[k] for k in ('имя', 'параметры')}, ensure_ascii=False, indent=1))
    vid = set()
    for s in e['источник']:
        print('---', s['файл'], s['страница|строки'])
        if s['файл'].endswith('.pdf'):
            for p in map(int, re.findall(r'\d+', s['страница|строки'].split('(')[0])):
                if (s['файл'], p) in vid: continue
                vid.add((s['файл'], p))
                t = stranica(s['файл'], p)
                print(t if polno else t[:2500])
