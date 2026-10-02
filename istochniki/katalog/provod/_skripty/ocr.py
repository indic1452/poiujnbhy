#!/usr/bin/env python3
"""OCR сканированных PDF (tesseract): tekst/<путь>.ocr.txt с метками страниц."""
import os, subprocess, sys, tempfile, pymupdf
from concurrent.futures import ThreadPoolExecutor
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def stranica(args):
    src, i = args
    d = pymupdf.open(src); pix = d[i].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    with tempfile.NamedTemporaryFile(suffix=".png") as t:
        pix.save(t.name)
        return subprocess.run(["tesseract", t.name, "-", "--psm", "6", "-l", "eng"], capture_output=True, text=True, env=dict(os.environ, OMP_THREAD_LIMIT="1"), timeout=300).stdout
for put in sys.argv[1:]:
    src = os.path.join(KOREN, put); n = len(pymupdf.open(src))
    with ThreadPoolExecutor(3) as ex: txt = list(ex.map(stranica, [(src, i) for i in range(n)]))
    dst = os.path.join(KOREN, "tekst", os.path.relpath(src, os.path.join(KOREN, "istochniki"))) + ".ocr.txt"
    with open(dst, "w") as o:
        for i, t in enumerate(txt): o.write(f"\n=====СТР {i+1}=====\n{t}")
    print("готово", dst)
