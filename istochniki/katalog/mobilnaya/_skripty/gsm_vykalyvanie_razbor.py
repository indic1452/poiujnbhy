"""Разбор списков выколотых бит из 3GPP TS 45.003 (текст PDF) — черновой просмотр."""
import re
T = open("tekst/gsm/ts_145003v190000p.pdf.txt").read()
stranicy = [(m.start(), int(m.group(1))) for m in re.finditer(r"=====СТР (\d+)=====", T)]
def stranica(pos):
    s = 0
    for p, n in stranicy:
        if p <= pos: s = n
    return s
for m in re.finditer(r"punctured(?: using [^,]*,)? (?:in )?such (?:a )?way that the following (\d+)?\s*(?:coded )?bits?:?(.*?)(?:are|is) not transmitted", T, re.S):
    n = m.group(1); body = m.group(2)
    nums = [int(x) for x in re.findall(r"C\((\d+)\)", body)]
    hdr = re.findall(r"\n\s*((?:\d+\.){1,4}\d+)\s*\n\s*([^\n]+)", T[max(0,m.start()-20000):m.start()])
    lab = re.findall(r"\n\s*((?:TCH|O-TCH|E-TCH|MCS|CS|PDTCH|SACCH|FACCH|RACH|SCH|PTCCH|EC-|TCH/)[^\n:]{0,30}):", T[max(0,m.start()-6000):m.start()])
    print(stranica(m.start()), n, len(nums), hdr[-1] if hdr else None, lab[-1] if lab else None, "|", re.sub(r"\s+"," ",body)[:80])
