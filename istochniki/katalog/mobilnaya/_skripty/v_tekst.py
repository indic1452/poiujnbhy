#!/usr/bin/env python3
"""PDF → текст с метками страниц «=====СТР N=====» в tekst/<тот же путь>.txt (производное, не первоисточник)."""
import os, sys, pymupdf
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for root, _, files in os.walk(os.path.join(KOREN, "istochniki")):
    for f in files:
        if not f.lower().endswith(".pdf"): continue
        src = os.path.join(root, f)
        dst = os.path.join(KOREN, "tekst", os.path.relpath(src, os.path.join(KOREN, "istochniki"))) + ".txt"
        if os.path.exists(dst) and os.path.getmtime(dst) > os.path.getmtime(src): continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            d = pymupdf.open(src)
            with open(dst, "w") as o:
                for i, p in enumerate(d):
                    o.write(f"\n=====СТР {i+1}=====\n"); o.write(p.get_text())
            print(len(d), dst)
        except Exception as e:
            print("ОШИБКА", src, e)
