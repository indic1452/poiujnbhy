#!/usr/bin/env python3
"""ITU-T/R: скачать действующие (in force) части Рекомендации (основной текст, поправки, исправления) → istochniki/itu/."""
import os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dl import dl, KOREN, UA
def get(u):
    return subprocess.run(["curl", "-sSL", "-A", UA, "-m", "120", u], capture_output=True).stdout.decode("latin1")
def ids(rec, vse=False):
    ser = "T" if not rec.startswith("R-") else "R"
    name = rec if rec.startswith(("T-REC-", "R-REC-")) else f"T-REC-{rec}"
    h = get(f"https://www.itu.int/rec/{name}/en")
    if not vse:
        h = re.split(r"Superseded and Withdrawn|Superseded", h)[0]
    out = []
    for m in re.findall(r"parent=(T-REC-[^\"&']+|R-REC-[^\"&']+)", h):
        if m != name and re.search(r'-\d{6}-', m) and m not in out: out.append(m)
    return out
def one(i, papka="itu"):
    h = get(f"https://www.itu.int/rec/{i}/en")
    m = re.search(r"(dologin(?:_pub)?\.asp)\?lang=e&amp;id=([^&\"]+?)!PDF-E&amp;type=items", h)
    if not m:
        m2 = re.search(r"(/dms_pub[^\"']+?!!PDF-E\.pdf)", h)
        if m2: 
            u = "https://www.itu.int" + m2.group(1)
        else:
            print("НЕТ PDF", i, file=sys.stderr); return None
    else:
        u = f"https://www.itu.int/rec/{m.group(1)}?lang=e&id={m.group(2)}!PDF-E&type=items"
    put = os.path.join("istochniki", papka, i.replace("!", "_") + ".pdf")
    if os.path.exists(os.path.join(KOREN, put)): return put
    return put if dl(u, put) else None
if __name__ == "__main__":
    vse = "--vse" in sys.argv
    for rec in [a for a in sys.argv[1:] if not a.startswith("--")]:
        L = ids(rec, vse)
        print(rec, L)
        for i in L: one(i)
