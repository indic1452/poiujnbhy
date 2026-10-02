#!/usr/bin/env python3
"""Поиск через Bing/DDG html — выдаёт ссылки результатов."""
import re, subprocess, sys, urllib.parse, html
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def get(url):
    return subprocess.run(["curl", "-sSL", "-A", UA, "-m", "30", url], capture_output=True, text=True, errors="replace").stdout
def bing(q):
    t = get("https://www.bing.com/search?q=" + urllib.parse.quote(q) + "&count=30")
    out = []
    for m in re.finditer(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', t):
        out.append((html.unescape(m.group(1)), re.sub("<[^>]+>", "", html.unescape(m.group(2)))))
    return out
def ddg(q):
    t = get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q))
    out = []
    for m in re.finditer(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', t):
        u = html.unescape(m.group(1))
        mm = re.search(r"uddg=([^&]+)", u)
        if mm: u = urllib.parse.unquote(mm.group(1))
        out.append((u, re.sub("<[^>]+>", "", html.unescape(m.group(2)))))
    return out
if __name__ == "__main__":
    q = " ".join(sys.argv[1:])
    r = bing(q)
    if not r: r = ddg(q)
    for u, t in r: print(u, "|", t[:90])
