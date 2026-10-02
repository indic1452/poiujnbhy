"""OCR сканированного PDF (tesseract, 300 dpi) → <pdf>.txt с метками '=====PAGE n=====' (номер страницы PDF)."""
import os, subprocess, sys, tempfile
import pymupdf
from concurrent.futures import ThreadPoolExecutor


def one(args):
    p, i, dpi, lang = args
    d = pymupdf.open(p)
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, 'p.png')
        d[i].get_pixmap(dpi=dpi).save(png)
        r = subprocess.run(['tesseract', png, '-', '-l', lang, '--psm', '6'], capture_output=True, text=True, env=dict(os.environ, OMP_THREAD_LIMIT='1'))
        return r.stdout


for p in sys.argv[1:]:
    n = len(pymupdf.open(p))
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(one, [(p, i, 300, 'eng') for i in range(n)]))
    with open(p + '.txt', 'w') as f:
        for i, t in enumerate(res):
            f.write('\n=====PAGE %d=====\n' % (i + 1)); f.write(t)
    print(p, n)
