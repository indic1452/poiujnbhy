"""OCR PDF-сканов с одним встроенным изображением на страницу (патенты USPTO, CCITT): извлекается само изображение
(без пересчёта масштаба), tesseract → <pdf>.txt с метками '=====PAGE n====='."""
import os, subprocess, sys, tempfile
import pymupdf
from concurrent.futures import ThreadPoolExecutor
def one(args):
    p, i = args
    d = pymupdf.open(p); x = d[i].get_images()[0][0]
    pix = pymupdf.Pixmap(d, x)
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, 'p.png'); pix.save(png)
        r = subprocess.run(['tesseract', png, '-', '-l', 'eng', '--psm', '6'], capture_output=True, text=True, env=dict(os.environ, OMP_THREAD_LIMIT='1'))
        return r.stdout
for p in sys.argv[1:]:
    n = len(pymupdf.open(p))
    with ThreadPoolExecutor(4) as ex: res = list(ex.map(one, [(p, i) for i in range(n)]))
    with open(p + '.txt', 'w') as f:
        for i, t in enumerate(res): f.write('\n=====PAGE %d=====\n' % (i + 1)); f.write(t)
    print(p, n)
