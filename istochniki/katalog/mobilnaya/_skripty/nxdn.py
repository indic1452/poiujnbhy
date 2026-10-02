"""NXDN (NXDN Forum TS 1-A v1.3 — Type-C/конвенциональный, TS 1-E v1.1 — Type-D): кодирование функциональных каналов
(CAC, Long/Short CAC, SACCH, FACCH1, UDCH/FACCH2; Type-D: SCCH, FACCH1, UDCH2/FACCH3), CRC-6/7/12/15/16/32, свёрточный K=5,
матрицы выкалывания, перемежение, LICH, скремблер. Сверка: MMDVMHost (GPL-2.0), dsd-fme (выкалывание), проект nxdn.py."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

A = Tekst("istochniki/nxdn/NXDN-TS-1-A_v0103.pdf")
D = Tekst("istochniki/nxdn/NXDN-TS-1-E_v0101.pdf")
ZAPISI = []

def razdely(T, nach=1000):
    """Разделы 4.5.x.y: (имя, номер строки, текст одной строкой)."""
    st = [i for i, l in enumerate(T.stroki) if re.fullmatch(r"4\.5\.\d\.\d\.\s*", l) and i > nach]
    kon = st[1:] + [next(i for i, l in enumerate(T.stroki) if i > st[-1] and re.fullmatch(r"4\.5\.\d\.\s*", l))]
    out = []
    for i, e in zip(st, kon):
        s = " ".join(" ".join(T.stroki[i:e]).split())
        s = re.sub(r"D (\d)\b", r"D\1", s).replace("＋", "+").replace("＝", "=").replace("Ｇ", "G")
        out.append((T.stroki[i + 1].strip(), i, s, T.stroki[i:e]))
    return out

def stepeni(v): return sorted({0 if t == "1" else 1 if t in ("D", "X") else int(t[1:]) for t in re.findall(r"[DX]\d*|\b1\b", v)}, reverse=True)

def matrica_vyk(T, stranica):
    """Матрица выкалывания по координатам слов «0»/«1» на странице PDF между «…matrix» и «(6)» (в текстовом слое строки
    матрицы Type-D склеены — порядок строк надёжен только по координатам)."""
    import pymupdf
    ws = pymupdf.open(os.path.join(KOREN, T.pdf))[stranica - 1].get_text("words")
    L = next(w for w in ws if w[4].lower().startswith("matrix"))
    ybot = min([w[1] for w in ws if w[1] > L[3] - 2 and w[4].startswith("(6)")], default=L[3] + 120)
    rows = {}
    for w in ws:
        if w[4] in ("0", "1") and L[1] - 3 < w[1] < ybot: rows.setdefault(round(w[1]), []).append((w[0], int(w[4])))
    rr = [[b for _, b in sorted(r)] for _, r in sorted(rows.items())]
    assert len(rr) == 2 and len(rr[0]) == len(rr[1]), rr
    return rr

def vykoloty(mat, n_vyh):
    """Номера выколотых бит в потоке G1,G2,G1,G2… длиной n_vyh."""
    P = len(mat[0])
    return [2 * t + r for t in range(n_vyh // 2) for r in (0, 1) if mat[r][t % P] == 0]

KAN = {}
for T, tip in ((A, "Type-C/конв."), (D, "Type-D")):
    for imya, i, s, stroki in razdely(T):
        z = {"тип": tip, "стр": T.str_[i], "T": T}
        m = re.search(r"(\d+) ?bits? CRC.*?Polynomial: ?(X[\dX +]+?\+ 1)\b", s)
        if m: z["crc"] = (int(m.group(1)), stepeni(m.group(2)))
        g = re.findall(r"G(\d) ?\(D\) ?= ?(1 ?\+[ D\d+]+?)(?= G\d| \(| [A-CE-Z]|$)", s)
        if g: z["G"] = [stepeni(x) for _, x in g[:2]]
        m = re.search(r"(?:number of information bits is|Number of Information bits is|are) (\d+) ?bits?,? (?:and )?(?:the interleaving depth is|depth of Interleave is) (\d+)", s)
        if m: z["il"] = (int(m.group(1)), int(m.group(2)))
        im = [k for k, l in enumerate(stroki) if re.search(r"[Pp]unctur\w* [Mm]atrix", l)]
        if im: z["vyk"] = matrica_vyk(T, T.str_[i + im[0]])
        z["hvost"] = 4 if re.search(r"Four tail bits|Fixed bit “0” of 4bit|4bit is added", s) else None
        KAN[f"{tip} {imya}"] = z
print({k: (v.get("crc"), v.get("G"), v.get("il"), v.get("vyk")) for k, v in KAN.items()}) if __name__ == "__main__" else None
G_NX = [[4, 3, 0], [4, 2, 1, 0]]
for k, v in KAN.items():
    if "crc" in v: assert v["G"] == G_NX, (k, v.get("G"))
assert dfree([mnogochlen(G_NX[0]), mnogochlen(G_NX[1])], 5) == 7

# --- сверка с MMDVMHost -----------------------------------------------------------------------------------------
MM = {"SACCH": kod("MMDVMHost", "NXDNSACCH.cpp"), "FACCH1": kod("MMDVMHost", "NXDNFACCH1.cpp"), "UDCH/ FACCH2": kod("MMDVMHost", "NXDNUDCH.cpp")}
crcf = kod("MMDVMHost", "NXDNCRC.cpp"); convf = kod("MMDVMHost", "NXDNConvolution.cpp"); lichf = kod("MMDVMHost", "NXDNLICH.cpp")
dsdf = kod("dsd-fme", "src/nxdn_deperm.c")
tc = open(os.path.join(KOREN, convf)).read()
assert "g1 = (d + d3 + d4) & 1" in tc and "g2 = (d + d1 + d2 + d4) & 1" in tc
DLINA = {"SACCH": 60, "FACCH1": 144, "UDCH/ FACCH2": 348}
IL = {}
for imya, put in MM.items():
    z = KAN["Type-C/конв. " + imya]; n = DLINA[imya]; glub = z["il"][1]
    tab = c_massiv(put, "INTERLEAVE_TABLE"); assert len(tab) == n, imya
    stolb = n // glub
    zakon = [(i % stolb) * glub + i // stolb for i in range(n)]   # запись строками D1…D_glub по n/glub, чтение столбцами
    assert tab == zakon, imya
    IL[imya] = (glub, stolb)
    pl = c_massiv(put, "PUNCTURE_LIST")
    n_do = n + len(pl)
    assert vykoloty(z["vyk"], n_do)[:len(pl)] == pl and len(vykoloty(z["vyk"], n_do)) == len(pl), (imya, z["vyk"])
td = open(os.path.join(KOREN, dsdf)).read()
dsd = {k: [int(x) for x in re.search(k + r"_puncture\[\d+\]\s*=\s*\{([\d,\s]+)\}", td).group(1).split(",")] for k in ("sacch", "facch1", "facch2", "cac")}
def verh_niz(mat): return [mat[r][t] for t in range(len(mat[0])) for r in (0, 1)]
assert verh_niz(KAN["Type-C/конв. SACCH"]["vyk"]) == dsd["sacch"] and verh_niz(KAN["Type-C/конв. FACCH1"]["vyk"]) == dsd["facch1"]
assert verh_niz(KAN["Type-C/конв. UDCH/ FACCH2"]["vyk"]) == dsd["facch2"] and verh_niz(KAN["Type-C/конв. CAC (Outbound)"]["vyk"]) == dsd["cac"]
tcr = open(os.path.join(KOREN, crcf)).read()
CRC_MM = {6: 0x27, 12: 0x80F, 15: 0x4CC5}
for w, p in CRC_MM.items():
    m = re.search(r"CNXDNCRC::createCRC%d\(.*?crc = (0x[0-9A-F]+)U;.*?crc \^= (0x[0-9A-F]+)U;" % w, tcr, re.S)
    assert int(m.group(1), 16) == (1 << w) - 1 and int(m.group(2), 16) == p, w
    zz = [v["crc"] for v in KAN.values() if v.get("crc", (0,))[0] == w]
    assert zz and all(mnogochlen(c[1]) & ((1 << w) - 1) == p for c in zz), w
s_crc_init = A.odna(r"Default values of the shift register shall be\s*$|shall be set to 1\.")[0]
s_crc32 = D.odna(r"X32\+X26\+X23\+X22\+X16\+X12\+X11\+X10\+X8\+X7\+X5\+X4\+X2\+X\+1")[0]
# проект
proj = open("/home/user/poiujnbhy/src/reportgen/potok/nxdn.py").read()
assert "ВЫКОЛОТЫ = [5, 11, 17, 23, 29, 35, 41, 47, 53, 59, 65, 71]" in proj and "МЕСТА_SACCH = [(i % 12) * 5 + i // 12 for i in range(60)]" in proj
# скремблер и LICH
s_scr = A.odna(r"Generator polynomial: X9 ＋ X4 ＋ 1|Generator polynomial: X9 \+ X4 \+ 1")[0]
kus, _ = A.kusok(r"Default value\s*\n", r"Table 4\.6-1")
nach = [int(x) for x in re.findall(r"\b[01]\b", kus)][:9]
assert nach == [0, 1, 1, 1, 0, 0, 1, 0, 0], nach                              # S8…S0
def pn9(n, s):
    out = []
    for _ in range(n):
        out.append(s & 1); fb = (s ^ (s >> 4)) & 1; s = (s >> 1) | (fb << 8)
    return out
pn = pn9(182, int("".join(map(str, nach)), 2))   # символы кадра после FSW (384 бита = 192 символа, FSW 10)
m = re.search(r"СКРЕМБЛЕР_БАЙТЫ = bytes\(\[(.*?)\]\)", proj, re.S)
sb = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", m.group(1))]
bity = [(b >> (7 - j)) & 1 for b in sb for j in range(8)]
assert len(bity) == 384 and bity[:20] == [0] * 20 and all(bity[20 + 2 * j] == pn[j] and bity[21 + 2 * j] == 0 for j in range(182)), "маска проекта ≠ ПСП"
mmc = c_massiv(kod("MMDVMHost", "NXDNControl.cpp"), "SCRAMBLER"); assert mmc == sb      # маска проекта = SCRAMBLER MMDVMHost
s_lich = A.odna(r"1 bit even parity")[0]
s_fsw = A.odna(r"Frame Sync Word shall be 10 symbols")[0]

tabl = {k: {"тип": v["тип"], "CRC": v.get("crc") and {"бит": v["crc"][0], "g": " + ".join(("1" if e == 0 else f"X{e}") for e in v["crc"][1])},
            "свёрточный": "K=5 1/2 G1=1+D3+D4, G2=1+D+D2+D4" if "G" in v else None, "хвост": v["hvost"], "перемежение_(бит,глубина)": v.get("il"),
            "выкалывание_G1_G2": v.get("vyk"), "страница": v["стр"]} for k, v in KAN.items()}
tablica("nxdn_kanaly", tabl, "NXDN: параметры кодирования функциональных каналов (TS 1-A, TS 1-E)",
        [A.ist(v["стр"], k) for k, v in KAN.items() if v["T"] is A] + [D.ist(v["стр"], k) for k, v in KAN.items() if v["T"] is D] + [ist_kod(MM["SACCH"], "INTERLEAVE_TABLE"), ist_kod(dsdf, "cac_puncture")],
        "перемежение SACCH/FACCH1/UDCH: запись строками D1…Dглубина, чтение столбцами — совпало с INTERLEAVE_TABLE MMDVMHost; выколотые позиции по матрицам = PUNCTURE_LIST MMDVMHost; "
        "матрицы SACCH/FACCH1/FACCH2/CAC = массивы *_puncture dsd-fme (чтение верх-низ); CRC-6/12/15 и начальное «все единицы» = MMDVMHost NXDNCRC.cpp")

def rec(imya, vid, par, gde, ist, prov, sl, sem="NXDN (NXDN Forum TS 1-A/1-E)"):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST = "ЕСТЬ частично в проекте: nxdn.py — FSW, скремблер, LICH, SACCH (CRC-6, K=5, выкалывание, перемежение 12×5), вызовы; FACCH1/UDCH/CAC — нет (те же элементы, другие размеры)"
for imya, z in KAN.items():
    if "crc" not in z: continue
    T = z["T"]
    par = {"CRC": tabl[imya]["CRC"], "свёрточный": tabl[imya]["свёрточный"], "хвост": "4 нулевых бита" if z["hvost"] else "—",
           "выкалывание (строки G1/G2)": z.get("vyk") or "нет («(5) Punctured Coding: None»)", "перемежение": (f"{z['il'][0]} инф. бит, глубина {z['il'][1]} — запись строками D1…D{z['il'][1]}, чтение столбцами" if z.get("il") else "—"),
           "порядок": "биты L3 со старшего (бит 7 октета 0), CRC — старший первым; выход G1, G2 попеременно", "начальное CRC": "все единицы (4.5.5)"}
    k = imya.split(" ", 1)[1]
    if k in IL: par["перемежение"] += f"; T[i] = (i mod {IL[k][1]})·{IL[k][0]} + ⌊i/{IL[k][1]}⌋ (номер в канале для i-го кодового бита)"
    prov = ("перемежение и выколотые позиции = MMDVMHost, матрица = dsd-fme; " if k in MM else "матрица выкалывания = dsd-fme cac_puncture; " if k.startswith("CAC") else "по тексту стандарта (второго открытого кода не найдено); ") + "CRC и свёрточный = MMDVMHost"
    rec(f"NXDN {imya}", f"каскадный: CRC-{z['crc'][0]} + свёрточный K=5 1/2 с выкалыванием + блочное перемежение", par,
        "NXDN 6.25/12.5 кГц: " + ("канал управления (RCCH)" if "CAC" in k else "SCCH (Type-D)" if "SCCH" in k else "каналы трафика RTCH/RDCH"),
        [T.ist(z["стр"], k), A.ist(s_crc_init, "4.5.5 CRC")], prov, EST)
rec("NXDN LICH (7 бит + чётность, повтор ×2 в дибите)", "код чётности", {"LICH": "RFCT 2, FCT 2, Option 2, направление 1 + 1 бит чётности по 4 старшим", "передача": "каждый бит LICH + единица (по символу) → 16 бит после FSW"},
    "NXDN все кадры", [A.ist(s_lich, "4.5.3 LICH"), ist_kod(lichf, "void CNXDNLICH::encode")], "= MMDVMHost NXDNLICH encode (бит, затем 1)", "ЕСТЬ в проекте: nxdn.py lich/чётность_lich")
rec("NXDN скремблер PN9 (X9 + X4 + 1, начальное 0x0E4)", "скремблер (аддитивный, по символам)", {"многочлен": "X9+X4+1", "начальное S8…S0": "0 1 1 1 0 0 1 0 0", "область": "всё, кроме FSW, защитного интервала, преамбулы и Post; сброс каждый кадр", "применение": "инверсия знака символа ±3/±1 при 1"},
    "NXDN (опционально по вызову)", [A.ist(s_scr, "4.6 Scrambler")], "ПСП из многочлена и начального состояния на 182 символа кадра = маска СКРЕМБЛЕР_БАЙТЫ проекта (инверсия старшего бита дибита) = SCRAMBLER MMDVMHost NXDNControl.cpp", "ЕСТЬ в проекте: nxdn.py СКРЕМБЛЕР")
rec("NXDN CRC-32 сообщений данных (Type-D)", "CRC-подобный", {"g": "X32+X26+X23+X22+X16+X12+X11+X10+X8+X7+X5+X4+X2+X+1 (= CRC-32 IEEE)", "начальное": "все единицы"},
    "NXDN пакетные данные", [D.ist(s_crc32, "9.3 Message CRC")], "многочлен совпадает с CRC-32/IEEE 802.3 (0x04C11DB7) каталога проекта", "есть CRC-32 в crc_katalog.py (параметры отражения — уточнить по примеру)")
rec("NXDN синхрослово FSW (20 бит = 10 символов)", "синхрослово", {"FSW": "0xCDF59 (символы −3 +1 −3 +3 −3 −3 +3 +3 −1 +3)", "преамбула": "см. 4.4.3"}, "поиск кадров NXDN",
    [A.ist(s_fsw, "4.4.4 FSW")], "FSW = NXDN_FSW MMDVMHost NXDNDefines.h и FSW проекта", "ЕСТЬ в проекте: nxdn.py FSW")
assert "0xCDF59" in proj and re.search(r"NXDN_FSW_BYTES\[\]\s*=\s*\{0xCDU, 0xF5U, 0x90U\}", open(os.path.join(KOREN, kod("MMDVMHost", "NXDNDefines.h"))).read())

if __name__ == "__main__":
    print(len(ZAPISI), "записей", IL)
