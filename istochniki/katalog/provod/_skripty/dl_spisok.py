#!/usr/bin/env python3
"""Скачать список URL ieee802.org → istochniki/ieee8023/<группа>/<имя> (путь из URL)."""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dl import dl, KOREN
papka = sys.argv[2] if len(sys.argv) > 2 else "ieee8023"
for u in open(sys.argv[1]):
    u = u.strip()
    if not u or u.startswith("#"): continue
    m = re.search(r"/3/([^/]+)/", u)
    grp = m.group(1) if m else "raznoe"
    put = os.path.join("istochniki", papka, grp, u.split("/")[-1].replace("%20", ""))
    if os.path.exists(os.path.join(KOREN, put)): continue
    dl(u, put)
