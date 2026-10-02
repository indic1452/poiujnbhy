#!/usr/bin/env python3
"""Обход публичных страниц рабочих групп IEEE 802.3: сбор (url pdf, подпись) → poisk/ieee_<tf>.tsv."""
import re, subprocess, sys, os, html
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poisk")
def get(u):
    return subprocess.run(["curl", "-sSL", "-A", UA, "-m", "60", u], capture_output=True).stdout.decode("latin1")
def obhod(tf, glub=3):
    start = f"https://www.ieee802.org/3/{tf}/public/"
    seen, pdf = set(), {}
    front = [start, f"https://www.ieee802.org/3/{tf}/index.html", f"https://www.ieee802.org/3/{tf}/index.shtml", f"https://www.ieee802.org/3/{tf}/public/meeting_archive/"]
    for d in range(glub):
        nf = []
        with ThreadPoolExecutor(8) as ex:
            pages = list(ex.map(lambda u: (u, get(u)), [u for u in front if u not in seen]))
        for u, h in pages:
            seen.add(u)
            for m in re.finditer(r'<a[^>]+href\s*=\s*"([^"]+)"[^>]*>(.*?)</a>', h, re.I | re.S):
                href, txt = m.group(1), html.unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(2))).strip()
                a = urljoin(u, href)
                if f"/3/{tf}/" not in a: continue
                if a.lower().endswith(".pdf"):
                    # подпись: текст ссылки + окружающая строка таблицы
                    s = h.rfind("<tr", 0, m.start()); e = h.find("</tr", m.end())
                    row = html.unescape(re.sub(r"<[^>]+>|\s+", " ", h[s:e])) if s != -1 and e != -1 and e - s < 3000 else txt
                    pdf.setdefault(a, row.strip()[:300])
                elif (a.endswith("/") or a.endswith(".html") or a.endswith(".htm") or a.endswith(".shtml")) and a not in seen and ("/public/" in a or "index.shtml" in a):
                    nf.append(a)
        front = list(dict.fromkeys(nf))
    with open(os.path.join(OUT, f"ieee_{tf}.tsv"), "w") as f:
        for a, t in pdf.items(): f.write(f"{a}\t{t}\n")
    print(tf, len(seen), "стр.", len(pdf), "pdf")
if __name__ == "__main__":
    for tf in sys.argv[1:]: obhod(tf)
