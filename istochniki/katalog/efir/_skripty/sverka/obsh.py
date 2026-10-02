"""Общее для независимой сверки каталога «efir» (второй проход, отдельно от скриптов сборки).
Свои реализации РСЛОС/CRC/свёрточных кодов/полей Галуа — не импортируют код сборщика."""
import json, os, re
KOREN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_K = None
def katalog():
    global _K
    if _K is None:
        _K = {z["имя"]: z for z in json.load(open(os.path.join(KOREN, "katalog.json")))}
    return _K
def zapis(nachalo):
    r = [z for n, z in katalog().items() if n.startswith(nachalo)]
    assert len(r) == 1, (nachalo, len(r))
    return r[0]
_T = {}
def stranicy(pdf_txt):
    if pdf_txt not in _T:
        t = open(os.path.join(KOREN, "tekst", pdf_txt), errors="replace").read()
        ch = re.split(r"=====СТР (\d+)=====", t)
        _T[pdf_txt] = {int(ch[i]): ch[i + 1] for i in range(1, len(ch), 2)}
    return _T[pdf_txt]
def str_(pdf_txt, *nomera):
    s = stranicy(pdf_txt)
    return "\n".join(s.get(n, "") for n in nomera)
def szh(t):
    return re.sub(r"\s+", "", t)
def est(pdf_txt, nomera, *obrazcy):
    """Все образцы (без пробелов) есть на указанных страницах текста PDF (±1 страница: номер печатный/PDF)."""
    t = szh(str_(pdf_txt, *sorted({m for n in nomera for m in (n - 1, n, n + 1)})))
    net = [o for o in obrazcy if szh(o) not in t]
    assert not net, f"{pdf_txt} стр. {nomera}: нет {net}"
    return True
def poisk(pdf_txt, obrazec):
    """Номера страниц, где встречается образец (без пробелов)."""
    return [n for n, t in stranicy(pdf_txt).items() if szh(obrazec) in szh(t)]
def bity(s):
    return [int(c) for c in s if c in "01"]
def lfsr_fib(otvody, sost, n):
    """Фибоначчи: sost[0] = старший разряд (x^1 … x^m по порядку записи), выход и вход — xor отводов (номера степеней)."""
    s = list(sost); out = []
    for _ in range(n):
        b = 0
        for o in otvody: b ^= s[o - 1]
        out.append(b); s = [b] + s[:-1]
    return out
def crc(bits, poly, w, init=0, xorout=0):
    """CRC старшим битом вперёд: poly без старшей степени."""
    r = init
    for b in bits:
        f = ((r >> (w - 1)) & 1) ^ b
        r = (r << 1) & ((1 << w) - 1)
        if f: r ^= poly
    return r ^ xorout
def bajty_v_bity(bs, msb=True):
    o = []
    for x in bs:
        o += [(x >> (7 - i)) & 1 for i in range(8)] if msb else [(x >> i) & 1 for i in range(8)]
    return o
def svyortka(bits, polys, K, init=0):
    """Свёрточный кодер: polys восьмерично, старший разряд многочлена — текущий бит."""
    s = init; out = []
    for b in bits:
        s = ((s >> 1) | (b << (K - 1)))
        out.append([bin(s & int(str(p), 8)).count("1") & 1 for p in polys])
    return out
class GF:
    def __init__(self, m, prim):
        self.m = m; self.n = (1 << m) - 1; self.exp = [0] * (2 * self.n); self.log = [0] * (self.n + 1)
        x = 1
        for i in range(self.n):
            self.exp[i] = x; self.log[x] = i; x <<= 1
            if x >> m: x ^= prim
        for i in range(self.n, 2 * self.n): self.exp[i] = self.exp[i - self.n]
        assert len(set(self.exp[:self.n])) == self.n, "не примитивный"
    def mul(self, a, b):
        return 0 if a == 0 or b == 0 else self.exp[self.log[a] + self.log[b]]
    def gen(self, korni):
        g = [1]
        for k in korni:
            a = self.exp[k % self.n]; ng = [0] * (len(g) + 1)
            for i, c in enumerate(g):
                ng[i] ^= self.mul(c, a); ng[i + 1] ^= c
            g = ng
        return g  # g[0] — свободный член
def poly_mod2_mul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r
def poly_mod2_mod(a, m):
    dm = m.bit_length() - 1
    while a and a.bit_length() - 1 >= dm:
        a ^= m << (a.bit_length() - 1 - dm)
    return a
def stepeni_v_chislo(s):
    """'x^8 + x^4 + x^3 + 1' / 'X14' / 'D^5' → int."""
    v = 0
    for t in re.split(r"\s*\+\s*", s.strip()):
        t = t.strip()
        if t in ("1",): v |= 1; continue
        m = re.fullmatch(r"[xXDz]\^?(\d*)", t)
        assert m, t
        v |= 1 << (int(m.group(1)) if m.group(1) else 1)
    return v
def lfsr_vyh(otvody, sost, n):
    """Фибоначчи с выходом из последнего разряда (как рисунки DVB): вход = xor отводов."""
    s = list(sost); out = []
    for _ in range(n):
        out.append(s[-1]); b = 0
        for o in otvody: b ^= s[o - 1]
        s = [b] + s[:-1]
    return out
def lfsr_lyuboy(stepeni, zagr, cel, vozvrat_vse=False):
    """Перебор схем РСЛОС (Фибоначчи/Галуа, загрузка прямо/обратно, многочлен/взаимный, выход — бит ОС/крайний разряд)
    для многочлена со степенями `stepeni` (включая m и 0) и загрузки `zagr`; возвращает схемы, давшие `cel`."""
    m = max(stepeni); n = len(cel); ok = []
    for vzaim in (False, True):
        st = sorted({(m - e) if vzaim else e for e in stepeni})
        for obr in (False, True):
            pre = list(zagr[::-1] if obr else zagr)
            # Фибоначчи: s[i] = x^(i+1); новый бит = xor s[e-1] по e из st без 0
            for vyh in ("os", "krai"):
                s = pre[:]; out = []
                for _ in range(n):
                    b = 0
                    for e in st:
                        if e: b ^= s[e - 1]
                    out.append(b if vyh == "os" else s[-1]); s = [b] + s[:-1]
                if out == list(cel): ok.append(("Фибоначчи", "взаимный" if vzaim else "прямой", "загрузка обратно" if obr else "загрузка как записано", vyh))
            # Галуа: сдвиг к старшему, при выходе 1 — xor отводов
            s = pre[:]; out = []
            for _ in range(n):
                o = s[-1]; out.append(o); s = [0] + s[:-1]
                if o:
                    for e in st:
                        if 0 < e < m: s[e] ^= 1
                    s[0] ^= 1
            if out == list(cel): ok.append(("Галуа", "взаимный" if vzaim else "прямой", "загрузка обратно" if obr else "загрузка как записано", "старший"))
    return ok
def dfree(polys, K, shablon=None, glubina=200):
    """Свободное расстояние свёрточного кода (многочлены восьмерично, старший разряд — текущий бит).
    shablon — список строк выкалывания по выходам одинаковой длины P (1 — передаётся). Перебор по фазе начала."""
    import heapq
    P = len(shablon[0]) if shablon else 1
    gs = [int(str(p), 8) for p in polys]; S = 1 << (K - 1)
    best = None
    for faza in range(P):
        # Дейкстра по (состояние, фаза), старт: выход из нуля единицей
        dist = {}; h = []
        def ves(s, b, f):
            reg = (b << (K - 1)) | s
            w = 0
            for i, g in enumerate(gs):
                if shablon is None or shablon[i][f] == "1": w += bin(reg & g).count("1") & 1
            return w, reg >> 1
        w0, s0 = ves(0, 1, faza)
        heapq.heappush(h, (w0, s0, (faza + 1) % P))
        while h:
            d, s, f = heapq.heappop(h)
            if s == 0:
                best = d if best is None else min(best, d); break
            if dist.get((s, f), 1 << 30) <= d: continue
            dist[(s, f)] = d
            for b in (0, 1):
                w, ns = ves(s, b, f)
                heapq.heappush(h, (d + w, ns, (f + 1) % P))
    return best
