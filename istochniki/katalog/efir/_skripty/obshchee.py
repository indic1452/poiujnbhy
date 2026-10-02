"""Общее для скриптов каталога: страницы текста PDF, копии открытого кода с лицензией, запись таблиц."""
import hashlib, json, os, re, shutil, subprocess, time
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS = os.path.join(os.path.dirname(os.path.dirname(KOREN)), "repos_e")
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

# --- добавлено для области efir -------------------------------------------------
OBLAST = "efir"
def Z(imya, semeystvo, vid, parametry, gde, istochnik, proverka, slozhnost):
    """Запись каталога в принятом формате."""
    return {"имя": imya, "семейство": semeystvo, "область": OBLAST, "вид": vid, "параметры": parametry,
            "где применяется": gde, "источник": istochnik, "проверка": proverka, "сложность внедрения": slozhnost}

def ch(s):
    """Все целые числа строки."""
    return [int(x) for x in re.findall(r"\d+", s)]

def stepeni(s, x="[xXDd]"):
    """Степени многочлена из записи вида x^14 + x^9 + … + x + 1 (в тексте PDF — «x14 + x9 + x + 1»)."""
    s = s.replace("^", "")
    st = set()
    for t in re.split(r"\s*\+\s*", s.strip()):
        t = t.strip()
        m = re.fullmatch(x + r"(\d*)", t)
        if m: st.add(int(m.group(1)) if m.group(1) else 1)
        elif t == "1": st.add(0)
        else: raise AssertionError(f"не член многочлена: {t!r} в {s!r}")
    return sorted(st, reverse=True)

def mnogochlen(st):
    return sum(1 << e for e in st)

def html_tekst(put):
    """Текст HTML-источника (теги сняты) по строкам — для ссылок «строки N»."""
    import html as _h
    t = open(os.path.join(KOREN, put), errors="replace").read()
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</(p|div|tr|li|h\d|table|td|th)>", "\n", t, flags=re.I)
    t = _h.unescape(re.sub(r"<[^>]+>", " ", t))
    return [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in t.split("\n") if l.strip()]

def conv_kod(bity, mnogochleny, K):
    """Кодирование свёрточным кодом (многочлены восьмерично в записи «старший — текущий вход»)."""
    reg = 0; out = []
    for b in bity:
        reg = ((reg << 1) | b) & ((1 << K) - 1)
        for g in mnogochleny:
            out.append(bin(reg & g).count("1") & 1)
    return out

def prbs(otvody, dlina, nachalo):
    """Фибоначчиев ГПСП: otvody — степени обратной связи (напр. (14, 15)), регистр — список бит [1..n]."""
    reg = list(nachalo); out = []
    for _ in range(dlina):
        b = 0
        for o in otvody: b ^= reg[o - 1]
        out.append(b); reg = [b] + reg[:-1]
    return out

def prbs_vyhod(otvody, dlina, nachalo):
    """Как prbs, но выход — последний разряд регистра (регистр 1..n, сдвиг к n; вход = xor отводов)."""
    reg = list(nachalo); out = []
    for _ in range(dlina):
        out.append(reg[-1]); b = 0
        for o in otvody: b ^= reg[o - 1]
        reg = [b] + reg[:-1]
    return out

def c_massiv_2d(put, imya):
    """Строки C-массива `imya[..][..] = { {…}, {…} };` — список списков чисел."""
    t = open(os.path.join(KOREN, put), errors="replace").read()
    m = re.search(re.escape(imya) + r"\s*(?:\[[^\]]*\]\s*)+=\s*\{(.*?)\}\s*;", t, re.S)
    assert m, f"нет массива {imya} в {put}"
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", m.group(1), flags=re.S)
    return [[int(x, 0) for x in re.findall(r"-?(?:0[xX][0-9a-fA-F]+|\d+)", r)] for r in re.findall(r"\{([^{}]*)\}", body)]

def stroki_chisel(kus, kolontitul=None):
    """Строки таблицы из куска текста PDF: каждая строка текста, состоящая только из целых чисел (≥2 чисел),
    — строка таблицы. kolontitul — регексп строк колонтитула, которые выбрасываются вместе со следующим номером страницы."""
    out = []
    ls = kus.split("\n"); i = 0
    while i < len(ls):
        l = ls[i].strip()
        if l.startswith("=====СТР"):
            i += 1
            # колонтитул: до 6 строк текста + номер страницы
            j = i
            while j < len(ls) and j < i + 7 and not re.fullmatch(r"\d+", ls[j].strip()):
                j += 1
            if kolontitul and j < len(ls) and re.fullmatch(r"\d+", ls[j].strip()) and any(re.search(kolontitul, x) for x in ls[i:j]):
                i = j + 1
            continue
        if re.fullmatch(r"\d+(?:\s+\d+)*", l) and l:
            out.append([int(x) for x in l.split()])
        i += 1
    return out

def sravnit_tablicy(a, b, chto):
    assert len(a) == len(b), f"{chto}: строк {len(a)} ≠ {len(b)}"
    for i, (x, y) in enumerate(zip(a, b)):
        assert x == y, f"{chto}: строка {i}: {x[:8]} ≠ {y[:8]}"
    return True

def bez_kolontitulov(kus, kolontitul):
    """Убирает из куска текста PDF блоки колонтитула: «=====СТР N=====», строки колонтитула (регексп) и номер страницы."""
    ls = kus.split("\n"); out = []; i = 0
    while i < len(ls):
        if ls[i].startswith("=====СТР"):
            j = i + 1
            while j < len(ls) and j < i + 8 and (re.search(kolontitul, ls[j]) or not ls[j].strip()):
                j += 1
            if j < len(ls) and re.fullmatch(r"\s*\d+\s*", ls[j]): j += 1
            while j < len(ls) and not ls[j].strip(): j += 1
            i = j; continue
        out.append(ls[i]); i += 1
    return "\n".join(out)

def lfsr_poisk(st, pre, t):
    """Ищет схему регистра (Фибоначчи/Галуа, порядок загрузки, направление, разряд выхода), при которой многочлен со
    степенями st и предзагрузка pre (строка бит) дают последовательность t. Возвращает описание или None."""
    m = len(pre); n = len(t)
    for order_name, order in (("как записано", pre), ("обратный", pre[::-1])):
        for tapmode in (0, 1, 2):
            for left in (0, 1):
                r = [int(c) for c in order]; out = []
                for _ in range(n):
                    fb = 0
                    for e in st:
                        if tapmode == 2:
                            if e == 0: continue
                            fb ^= r[e - 1]; continue
                        if e == m: continue
                        fb ^= r[(m - 1 - e) if tapmode == 0 else e]
                    out.append(fb); r = (r[1:] + [fb]) if left else ([fb] + r[:-1])
                if "".join(map(str, out)) == t:
                    return f"Фибоначчи, выход — бит обратной связи: загрузка {order_name}, сдвиг {'влево' if left else 'вправо'}, отводы {'m−1−e' if tapmode == 0 else 'e' if tapmode == 1 else 'e−1 (разряды x^1…x^m)'}"
        for outpos in range(m):
            for left in (0, 1):
                for tapmode in (0, 1):
                    r = [int(c) for c in order]; out = []
                    for _ in range(n):
                        out.append(r[outpos]); fb = 0
                        for e in st:
                            if e == m: continue
                            fb ^= r[(m - 1 - e) if tapmode == 0 else e]
                        r = (r[1:] + [fb]) if left else ([fb] + r[:-1])
                    if "".join(map(str, out)) == t:
                        return f"Фибоначчи: загрузка {order_name}, выход — разряд {outpos}, сдвиг {'влево' if left else 'вправо'}, отводы {'m−1−e' if tapmode == 0 else 'e'}"
                    # Галуа
                    r = [int(c) for c in order]; out = []
                    for _ in range(n):
                        b = r[outpos]; out.append(b)
                        if left:
                            top = r[0]; r = r[1:] + [0]
                            if top:
                                for e in st:
                                    if e < m: r[m - 1 - e] ^= 1
                        else:
                            low = r[-1]; r = [0] + r[:-1]
                            if low:
                                for e in st:
                                    if e < m: r[e] ^= 1
                    if "".join(map(str, out)) == t:
                        return f"Галуа: загрузка {order_name}, выход — разряд {outpos}, сдвиг {'влево' if left else 'вправо'}"
    return None

def est_podposl(kus, seq):
    """Встречается ли последовательность чисел seq подряд среди чисел куска текста."""
    t = [int(x) for x in re.findall(r"\d+", kus)]
    n = len(seq)
    return any(t[i:i + n] == list(seq) for i in range(len(t) - n + 1))

def skleit_korotkie(rows, min_dlina=3):
    """Строки таблицы, перенесённые в PDF: строка короче min_dlina чисел приклеивается к предыдущей."""
    out = []
    for r in rows:
        if out and len(r) < min_dlina: out[-1] = out[-1] + r
        else: out.append(list(r))
    return out

def rang_gf2(rows):
    """Ранг над GF(2) строк-целых."""
    basis = {}
    for r in rows:
        while r:
            h = r.bit_length() - 1
            if h in basis: r ^= basis[h]
            else: basis[h] = r; break
    return len(basis)

def qc_v_H(base, Z):
    """Квазициклическая базовая матрица (−1 = нулевой блок, i = единичная со сдвигом вправо на i) → строки H (целые)."""
    nb = len(base[0]); rows = []
    for br in base:
        for r in range(Z):
            v = 0
            for j, s in enumerate(br):
                if s is None or s < 0: continue
                c = (r + s) % Z
                v |= 1 << (nb * Z - 1 - (j * Z + c))
            rows.append(v)
    return rows


def dfree_vyk(polys_okt, K, shablon=None):
    """Свободное расстояние выколотого свёрточного кода 1/n (многочлены восьмерично, старший разряд — текущий бит).
    shablon — строки выкалывания по выходам одинаковой длины P («1» — бит передаётся); минимум по фазе начала шаблона.
    (Добавлено вторым проходом сверки.)"""
    import heapq
    P = len(shablon[0]) if shablon else 1
    gs = [int(str(p), 8) for p in polys_okt]; best = None
    def ves(s, b, f):
        reg = (b << (K - 1)) | s
        return sum(bin(reg & g).count("1") & 1 for i, g in enumerate(gs) if shablon is None or shablon[i][f] == "1"), reg >> 1
    for faza in range(P):
        dist = {}; h = []
        w0, s0 = ves(0, 1, faza); heapq.heappush(h, (w0, s0, (faza + 1) % P))
        while h:
            d, s_, f = heapq.heappop(h)
            if s_ == 0:
                best = d if best is None else min(best, d); break
            if dist.get((s_, f), 1 << 30) <= d: continue
            dist[(s_, f)] = d
            for b in (0, 1):
                w, ns = ves(s_, b, f); heapq.heappush(h, (d + w, ns, (f + 1) % P))
    return best
