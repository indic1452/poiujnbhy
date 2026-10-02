#!/usr/bin/env python3
"""naiti.py <регексп> <файл.txt> [контекст] — строки с номерами страниц PDF."""
import re, sys
pat = re.compile(sys.argv[1], re.I); f = sys.argv[2]; ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
lines = open(f, errors="replace").read().split("\n"); page = 0; pages = []
for l in lines:
    m = re.match(r"=====СТР (\d+)=====", l)
    if m: page = int(m.group(1))
    pages.append(page)
for i, l in enumerate(lines):
    if pat.search(l):
        for j in range(max(0, i - ctx), min(len(lines), i + ctx + 1)):
            print(f"[с.{pages[j]} стр.{j+1}] {lines[j]}")
        if ctx: print("--")
