"""Поиск по всему PDF: python3 ishi.py <pdf> <рег.выражение> [контекст]"""
import sys, os, re, pymupdf
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pdf = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(KOREN, sys.argv[1])
k = int(sys.argv[3]) if len(sys.argv) > 3 else 0
d = pymupdf.open(pdf)
for p in range(len(d)):
    L = d[p].get_text().split("\n")
    for i, l in enumerate(L):
        if re.search(sys.argv[2], l, re.I):
            print(f"--- стр.{p+1} стр-ка {i}: " + " | ".join(L[max(0,i-k):i+k+1]))
