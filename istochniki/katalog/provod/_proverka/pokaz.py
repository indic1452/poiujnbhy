"""Показ текста страницы PDF (pymupdf) для ручной сверки: python3 pokaz.py <pdf> <стр> [<стр2>] [рег.выражение]"""
import sys, os, re, pymupdf
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pdf = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(KOREN, sys.argv[1])
d = pymupdf.open(pdf)
a = int(sys.argv[2]); b = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else a
pat = sys.argv[-1] if not sys.argv[-1].isdigit() and len(sys.argv) > 3 else None
for p in range(a, b + 1):
    t = d[p - 1].get_text()
    if pat:
        for i, l in enumerate(t.split("\n")):
            if re.search(pat, l, re.I): print(p, i, l)
    else:
        print(f"===== стр. {p} =====\n{t}")
