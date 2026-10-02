#!/usr/bin/env python3
"""str.py <pdf> <с> <по> [рег] — текст страниц PDF в одну строку (для чтения)."""
import sys,re; sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import Tekst
T=Tekst(sys.argv[1], ocr=len(sys.argv)>4 and sys.argv[4]=="ocr"); a,b=int(sys.argv[2]),int(sys.argv[3])
print(" | ".join(l.strip() for i,l in enumerate(T.stroki) if a<=T.str_[i]<=b and l.strip() and not l.startswith("=====") and "everyspec" not in l))
