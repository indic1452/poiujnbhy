"""grep по .txt с разметкой страниц: печатает (стр. PDF) строку. gp.py шаблон файл [контекст]"""
import re, sys
pat = re.compile(sys.argv[1], re.I); fn = sys.argv[2]; ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
page = 0; lines = open(fn, encoding='utf8', errors='replace').read().split('\n')
pages = []
for l in lines:
    m = re.match(r'=====PAGE (\d+)=====', l)
    if m: page = int(m.group(1))
    pages.append(page)
for i, l in enumerate(lines):
    if pat.search(l):
        for j in range(max(0, i-ctx), min(len(lines), i+ctx+1)):
            print(f'[{pages[j]}:{j+1}] {lines[j]}')
        if ctx: print('--')
