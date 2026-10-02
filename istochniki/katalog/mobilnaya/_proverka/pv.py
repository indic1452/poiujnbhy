"""Помощники независимой проверки каталога mobilnaya: текст страниц PDF (pymupdf, не tekst/ сборщика),
поиск значений записи на указанной странице, журнал результатов."""
import json, os, re, functools, hashlib
import pymupdf
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KAT = json.load(open(os.path.join(KOREN, "katalog.json")))
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
    r = [z for z in KAT if imya_chast in z["имя"]]
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
