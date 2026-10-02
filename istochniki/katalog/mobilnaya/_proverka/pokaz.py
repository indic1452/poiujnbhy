import sys, re
sys.path.insert(0, __file__.rsplit('/',1)[0])
from pv import stranica
pdf = sys.argv[1]
for a in sys.argv[2:]:
    p, _, pat = a.partition(':')
    t = stranica(pdf, int(p))
    if pat:
        for m in list(re.finditer(pat, t, re.I))[:1]:
            print(f"--- стр {p} @{m.start()}:", re.sub(r'\s+', ' ', t[max(0, m.start()-300):m.start()+900]))
    else:
        print(f"=== стр {p}\n", re.sub(r'[ \t]+', ' ', t))
