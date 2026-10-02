"""Общее для скриптов каталога: страницы текста PDF, копии открытого кода с лицензией, запись таблиц."""
import hashlib, json, os, re, shutil, subprocess, time
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS = os.path.join(os.path.dirname(os.path.dirname(KOREN)), "repos_p")
_manifest = None

def manifest():
    global _manifest
    if _manifest is None:
        _manifest = {}
        for l in open(os.path.join(KOREN, "manifest.jsonl")):
            z = json.loads(l); _manifest[z["put"]] = z
    return _manifest

def url(put):
    return manifest()[put]["url"]

class Tekst:
    """Текст PDF из tekst/ с метками страниц; поиск возвращает (страница, номер строки, строка)."""
    def __init__(self, put_pdf, ocr=False):
        self.pdf = put_pdf
        self.put = os.path.join("tekst", os.path.relpath(put_pdf, "istochniki")) + (".ocr.txt" if ocr else ".txt")
        self.stroki = open(os.path.join(KOREN, self.put), errors="replace").read().split("\n")
        self.str_ = []; s = 0
        for l in self.stroki:
            m = re.match(r"=====СТР (\d+)=====", l)
            if m: s = int(m.group(1))
            self.str_.append(s)
        self.ves = "\n".join(self.stroki)
    def stranica_pozicii(self, pos):
        return self.str_[self.ves.count("\n", 0, pos)]
    def naiti(self, pat, flags=re.I):
        return [(self.str_[i], i + 1, l) for i, l in enumerate(self.stroki) if re.search(pat, l, flags)]
    def odna(self, pat, flags=re.I):
        r = self.naiti(pat, flags)
        assert r, f"не найдено в {self.pdf}: {pat}"
        return r[0]
    def kusok(self, ot, do, flags=re.I | re.S, posl=False):
        """Текст между началом `ot` и первым `do` после него; posl — брать последнее вхождение `ot`."""
        starts = list(re.finditer("(?:" + ot + ")", self.ves, flags))
        assert starts, f"не найден кусок {ot} … {do} в {self.pdf}"
        order = starts[::-1] if posl else starts
        for st in order:
            m = re.compile("(?:" + do + ")", flags).search(self.ves, st.end())
            if m:
                return self.ves[st.end():m.start()], self.stranica_pozicii(st.start())
        raise AssertionError(f"нет конца куска {do} в {self.pdf}")
    def ist(self, stranica, primechanie=""):
        z = {"файл": self.pdf, "страница": stranica, "url": url(self.pdf)}
        if primechanie: z["что"] = primechanie
        return z

def commit(repo):
    return subprocess.run(["git", "-C", os.path.join(REPOS, repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

def remote(repo):
    return subprocess.run(["git", "-C", os.path.join(REPOS, repo), "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip().removesuffix(".git")

def kod(repo, rel, licenziya=None):
    """Копия файла открытого кода в istochniki/kod/<repo>/<rel> + лицензия; запись в manifest. Возвращает путь."""
    src = os.path.join(REPOS, repo, rel)
    put = os.path.join("istochniki", "kod", repo, rel)
    dst = os.path.join(KOREN, put)
    c = commit(repo); u = f"{remote(repo)}/blob/{c}/{rel}"
    if put not in manifest():
        os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy2(src, dst)
        h = hashlib.sha256(open(dst, "rb").read()).hexdigest()
        z = {"put": put, "url": u, "sha256": h, "razmer": os.path.getsize(dst), "kogda": time.strftime("%Y-%m-%d"), "commit": c}
        with open(os.path.join(KOREN, "manifest.jsonl"), "a") as f: f.write(json.dumps(z, ensure_ascii=False) + "\n")
        manifest()[put] = z
    for lic in ([licenziya] if licenziya else ["LICENSE", "LICENCE", "COPYING", "LICENSE.txt", "LICENSE.md", "COPYING.txt", "LICENSE-MIT", "UNLICENSE", "COPYRIGHT"]):
        if lic and os.path.exists(os.path.join(REPOS, repo, lic)) and os.path.join("istochniki", "kod", repo, lic) not in manifest():
            kod(repo, lic, licenziya="-")
    return put

def stroka_koda(put, pat):
    for i, l in enumerate(open(os.path.join(KOREN, put), errors="replace")):
        if re.search(pat, l): return i + 1
    raise AssertionError(f"нет {pat} в {put}")

def ist_kod(put, pat, primechanie=""):
    n = stroka_koda(put, pat)
    z = {"файл": put, "строки": str(n), "url": manifest()[put]["url"] + f"#L{n}"}
    if primechanie: z["что"] = primechanie
    return z

def c_massiv(put, imya):
    """Числа C-массива `imya[...] = { ... };` из файла."""
    t = open(os.path.join(KOREN, put), errors="replace").read()
    m = re.search(re.escape(imya) + r"\s*\[[^\]]*\](?:\s*\[[^\]]*\])*\s*=\s*\{(.*?)\};", t, re.S)
    assert m, f"нет массива {imya} в {put}"
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", m.group(1), flags=re.S)
    # в C «0NN» — восьмеричная запись (как ENCODE_MATRIX_* MMDVMHost)
    return [int(x, 8) if re.fullmatch(r"-?0[0-7]+", x) else int(x, 10) if re.fullmatch(r"-?0\d+", x) else int(x, 0) for x in re.findall(r"-?(?:0[xX][0-9a-fA-F]+|0[bB][01]+|\d+)", body)]

def tablica(imya, dannye, opisanie, istochniki, proverka):
    """tablicy/<imya>.json: данные + откуда + как проверено."""
    put = os.path.join("tablicy", imya + ".json")
    with open(os.path.join(KOREN, put), "w") as f:
        json.dump({"имя": imya, "описание": opisanie, "источник": istochniki, "проверка": proverka, "данные": dannye},
                  f, ensure_ascii=False, indent=1)
    return put

def mnogochlen(stepeni):
    return sum(1 << s for s in stepeni)

def D_zapis(stepeni):
    return " + ".join(("1" if s == 0 else "D" if s == 1 else f"D{s}") for s in sorted(stepeni))

def chisla_bez_kolontitulov(kus):
    """Числа, стоящие отдельной строкой, без колонтитулов ETSI (номер страницы, версия)."""
    kus = re.sub(r"=====СТР \d+=====.*?Release \d+", "", kus, flags=re.S)
    return [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]

def pary(chisla, klyuchi, zn_ot, zn_do):
    """Пары (ключ, значение) из потока чисел таблицы «ключ | значение | ключ | значение …»."""
    ost = set(klyuchi); d = {}; i = 0
    while i + 1 < len(chisla):
        k, v = chisla[i], chisla[i + 1]
        if k in ost and zn_ot <= v <= zn_do:
            d[k] = v; ost.discard(k); i += 2
        else:
            i += 1
    assert not ost, f"не хватает ключей: {sorted(ost)[:10]} … всего {len(ost)}"
    return d

def pary_po_poryadku(chisla, klyuchi):
    """Пары (ключ, значение): ключи ожидаются строго в данном порядке (строки таблицы слева направо),
    между ними допускается мусор колонтитулов. Возвращает {ключ: значение}."""
    d = {}; i = 0
    for k in klyuchi:
        while i < len(chisla) and chisla[i] != k: i += 1
        assert i + 1 < len(chisla), f"ключ {k} не найден"
        d[k] = chisla[i + 1]; i += 2
    return d

def klyuchi_strokami(n, stolbcov):
    """Порядок ключей таблицы из `stolbcov` пар в строке, столбец c содержит ключи c·h … c·h+h−1."""
    h = -(-n // stolbcov)
    return [r + c * h for r in range(h) for c in range(stolbcov) if r + c * h < n]

def dfree(gens, K):
    """Свободное расстояние несистематического свёрточного кода 1/n (многочлены — целые, K бит, любой порядок записи):
    Дейкстра по состояниям от первого единичного входа до возврата в нулевое состояние."""
    import heapq
    m = K - 1; mask = (1 << m) - 1
    def shag(s, b):
        reg = (b << m) | s                      # старший бит — текущий вход
        w = sum(bin(reg & g).count("1") & 1 for g in gens)
        return reg >> 1, w
    s0, w0 = shag(0, 1)
    best = {s0: w0}; q = [(w0, s0)]
    while q:
        w, s = heapq.heappop(q)
        if s == 0: return w
        if w > best.get(s, 1 << 30): continue
        for b in (0, 1):
            ns, dw = shag(s, b)
            if w + dw < best.get(ns, 1 << 30):
                best[ns] = w + dw; heapq.heappush(q, (w + dw, ns))
    return None

def okt_v_int(s): return int(str(s), 8)

def crc_reveng(imya_regex):
    """Записи каталога CRC проекта (RevEng): {имя: (ширина, poly, init, refin, refout, xorout)}."""
    t = open("/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py").read()
    return {m.group(1): (int(m.group(2)), int(m.group(3), 16), int(m.group(4), 16), m.group(5), m.group(6), int(m.group(7), 16))
            for m in re.finditer(r'\("(' + imya_regex + r')", (\d+), (0x[0-9A-Fa-f]+), (0x[0-9A-Fa-f]+), (\w+), (\w+), (0x[0-9A-Fa-f]+)', t)}

def _gde(self, fraza):
    """Страница, где встречается фраза (литерал; пробелы/переносы строк не важны)."""
    pat = r"\s+".join(re.escape(w) for w in fraza.split())
    m = re.search(pat, self.ves)
    assert m, f"не найдено в {self.pdf}: {fraza}"
    return self.stranica_pozicii(m.start())
Tekst.gde = _gde
