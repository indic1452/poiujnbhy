#!/usr/bin/env python3
"""risunok.py <pdf> <страница с 1> <текст для поиска|-> <выше pt> <ниже pt> <имя.png> — снимок участка страницы (свидетельство)."""
import sys, pymupdf
pdf, s, txt, up, down, out = sys.argv[1:7]
d = pymupdf.open(pdf); p = d[int(s) - 1]
if txt == "-": y = 0
else:
    r = p.search_for(txt); assert r, "нет текста"; y = r[0].y0
clip = pymupdf.Rect(0, max(0, y - float(up)), p.rect.width, min(p.rect.height, y + float(down)))
p.get_pixmap(dpi=110, clip=clip).save("risunki/" + out); print("risunki/" + out)
