"""Помощники независимой проверки каталога provod: текст страниц PDF (pymupdf, не tekst/ сборщика),
поиск значений записи на указанной странице, журнал результатов."""
import json, os, re, functools, hashlib
import pymupdf
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# сверяется снимок каталога сборщика (выход skripty/sobrat.py до правок проверяющего)
KAT = json.load(open(os.path.join(KOREN, "proverka", "katalog_do_proverki.json")))
IMENA = {z["имя"]: z for z in KAT}
ZHURNAL = []   # (имя записи, что проверено, итог, подробности)

@functools.lru_cache(None)
def doc(pdf):
    return pymupdf.open(os.path.join(KOREN, pdf))

@functools.lru_cache(None)
def stranica(pdf, p):
    """Текст страницы p (нумерация PDF с 1)."""
    return doc(pdf)[p - 1].get_text()

def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("−", "-").replace("–", "-").replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", "", s)

def na_str(pdf, p, *obrazcy, sosedi=0):
    """Все образцы (строки, сравнение без пробелов) есть на странице p (или ±sosedi). Возвращает список отсутствующих."""
    t = "".join(norm(stranica(pdf, q)) for q in range(max(1, p - sosedi), min(len(doc(pdf)), p + sosedi) + 1))
    return [o for o in obrazcy if norm(o) not in t]

def zapis(imya_chast):
    r = [z for z in KAT if z["имя"].startswith(imya_chast)] or [z for z in KAT if imya_chast in z["имя"]]
    assert len(r) == 1, (imya_chast, [z["имя"] for z in r])
    return r[0]

def ok(imya, chto, uslovie, podr=""):
    ZHURNAL.append({"запись": imya, "проверка": chto, "итог": "совпало" if uslovie else "РАСХОЖДЕНИЕ", "подробности": podr})
    return uslovie

def str_ok(z, pdf, p, chto, *obrazcy, sosedi=0):
    net = na_str(pdf, p, *obrazcy, sosedi=sosedi)
    return ok(z["имя"], f"{chto}: {pdf.split('/')[-1]} стр. {p}", not net, "нет на странице: " + "; ".join(net) if net else "найдено: " + "; ".join(obrazcy)[:300])

# --- GF(2) многочлены ---
def pmod(a, g):
    dg = g.bit_length() - 1
    while a and a.bit_length() - 1 >= dg:
        a ^= g << (a.bit_length() - 1 - dg)
    return a
def pmul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r
def st(*stepeni):
    return sum(1 << s for s in stepeni)
def oct_zapis(stepeni, K, starshiy_D0=False):
    v = sum(1 << ((K - 1 - s) if starshiy_D0 else s) for s in stepeni)
    return oct(v)[2:]

def crc_bits(bits, g, n, init=0, xorout=0):
    """CRC по битам старшим первым: регистр n бит, многочлен g без старшего члена."""
    r = init
    for b in bits:
        fb = ((r >> (n - 1)) & 1) ^ b
        r = ((r << 1) & ((1 << n) - 1)) ^ (g if fb else 0)
    return r ^ xorout

def crc_bytes(data, g, n, init=0, refin=False, refout=False, xorout=0):
    bits = []
    for B in data:
        bb = [(B >> i) & 1 for i in range(8)]
        bits += bb if refin else bb[::-1]
    r = crc_bits(bits, g, n, init)
    if refout: r = int(format(r, f"0{n}b")[::-1], 2)
    return r ^ xorout

def sohranit(put):
    json.dump(ZHURNAL, open(put, "w"), ensure_ascii=False, indent=1)

# --- конечные поля GF(2^m) и РС (своя реализация проверяющего) ---
@functools.lru_cache(None)
def gf(m, prim):
    """Таблицы exp/log поля GF(2^m) с многочленом prim (целое со старшим битом); α = x. Проверяет примитивность."""
    n = (1 << m) - 1
    exp = [0] * (2 * n); log = [None] * (n + 1); x = 1
    for i in range(n):
        assert log[x] is None, f"многочлен {prim:#x} не примитивен"
        exp[i] = x; log[x] = i
        x <<= 1
        if x >> m: x ^= prim
    assert x == 1
    for i in range(n, 2 * n): exp[i] = exp[i - n]
    return exp, log

def gmul(a, b, m, prim):
    if a == 0 or b == 0: return 0
    exp, log = gf(m, prim); return exp[log[a] + log[b]]

def rs_g(m, prim, korni):
    """g(x) = ∏ (x − α^i) по списку показателей; коэффициенты от старшего (1) к младшему."""
    exp, log = gf(m, prim); n = (1 << m) - 1
    g = [1]
    for i in korni:
        a = exp[i % n]
        r = g + [0]
        for j in range(len(g)):
            r[j + 1] ^= gmul(g[j], a, m, prim)
        g = r
    return g

def rs_proverochnye(dannye, g, m, prim):
    """Остаток x^(n−k)·d(x) mod g(x); данные — от старшего коэффициента; результат от старшего."""
    r = [0] * (len(g) - 1)
    for d in dannye:
        fb = d ^ r[0]
        r = r[1:] + [0]
        if fb:
            for j in range(len(r)):
                r[j] ^= gmul(fb, g[j + 1], m, prim)
    return r

def sindromy(slovo, m, prim, korni):
    exp, log = gf(m, prim); n = (1 << m) - 1
    out = []
    for i in korni:
        s = 0
        for c in slovo:
            s = gmul(s, exp[i % n], m, prim) ^ c
        out.append(s)
    return out

def min_mnogochlen(i, m, prim):
    """Минимальный многочлен α^i над GF(2) (целое: бит k = коэффициент x^k)."""
    exp, log = gf(m, prim); n = (1 << m) - 1
    klass = []; j = i % n
    while j not in klass:
        klass.append(j); j = (2 * j) % n
    p = [1]
    for j in klass:
        a = exp[j]; r = p + [0]
        for t in range(len(p)): r[t + 1] ^= gmul(p[t], a, m, prim)
        p = r
    assert all(c in (0, 1) for c in p)
    return sum(c << (len(p) - 1 - t) for t, c in enumerate(p))

def period(g):
    """Период многочлена над GF(2): наименьшее e, что g | x^e − 1."""
    d = g.bit_length() - 1; x = 2; e = 1
    while True:
        if pmod(x, g) == 1: return e
        x = pmod(x << 1, g); e += 1
        assert e < (1 << d) + 2

def primitiven(p):
    return period(p) == (1 << (p.bit_length() - 1)) - 1

def poly_str(p, x="x"):
    st_ = [i for i in range(p.bit_length() - 1, -1, -1) if p >> i & 1]
    return " + ".join("1" if s == 0 else x if s == 1 else f"{x}^{s}" for s in st_)

def bity_hex(h):
    """Шестнадцатеричная строка → биты, старший бит знака первым."""
    return [int(b) for c in h for b in format(int(c, 16), "04b")]

ISPR = []   # найденные ошибки каталога: что было, что стало, по какому источнику
def ispr(imya, chto, bylo, stalo, osnovanie):
    ISPR.append({"запись": imya, "что": chto, "было": bylo, "стало": stalo, "основание": osnovanie})
    ZHURNAL.append({"запись": imya, "проверка": chto, "итог": "ОШИБКА КАТАЛОГА → исправлено", "подробности": f"было: {bylo}; стало: {stalo}; основание: {osnovanie}"})
