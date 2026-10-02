"""Независимая сверка записей PON (G.984.3, G.987.3, G.9807.1, G.989.3, G.9804.x, G.9806)."""
import json, re
from pv import *
I = "istochniki/itu/"
G9843 = I + "T-REC-G.984.3-201401-I.pdf"; G9873 = I + "T-REC-G.987.3-202505-I.pdf"
gHEC = st(12, 10, 8, 5, 4, 3, 0)

z = zapis("GPON GEM заголовок")
str_ok(z, G9843, 47, "8.3.1: BCH(39,12,2) + чётность, g(x), нулевое начальное, маска", "BCH (39, 12, 2) code and a single parity bit", "x12 + x10 + x8 + x5 + x4 + x3 + 1", "initialization value of this register is all zeroes", "B6AB31E055")
ok(z["имя"], "период g(x) = 63 (свой расчёт) → БЧХ(63,51) укорочен до 39", period(gHEC) == 63)
# примеры заголовков GEM в приложении (стр. 151): проверка своим делителем
t = stranica(G9843, 151) + stranica(G9843, 152)
pr = re.findall(r"\b([0-9A-Fa-f]{10})\b", t)
good = [h for h in pr if pmod(int(h, 16) >> 1, gHEC) == 0 and bin(int(h, 16)).count("1") % 2 == 0]
ok(z["имя"], f"примеры заголовков GEM (прил., стр. 151): своя проверка БЧХ + чётность", len(pr) > 0 and len(good) == len(pr), f"{len(good)} из {len(pr)} годны: {pr[:4]}")

z = zapis("GPON FEC RS(255,239)")
str_ok(z, G9843, 37, "8.1.2: скремблер x^7 + x^6 + 1", "The polynomial used is x7 + x6 + 1")

z = zapis("XG-PON HEC")
t = stranica(G9873, 113) + stranica(G9873, 114)
str_ok(z, G9873, 113, "A.1: BCH(63,12,2), g(x), поля 51/19 бит", "BCH(63, 12, 2) code", "x12 + x10 + x8 + x5 + x4 + x3 + 1", "protected field of 19 bits", "51 bits")
k = t[t.index("Table A.1"):]
pary_ = re.findall(r"(?m)^\s*(\d{1,2})\s*\n\s*([0-9A-F]{3})\s*$", k)
tab = {int(p): int(s, 16) for p, s in pary_}
moi = {p: pmod(1 << (p - 1), gHEC) for p in range(1, 64)}
ok(z["имя"], "табл. A.1: синдромы позиции p = x^(p−1) mod g(x) (свой расчёт)", len(tab) == 63 and all(tab[p] == moi[p] for p in tab), f"разобрано {len(tab)}, совпало {sum(tab[p] == moi[p] for p in tab)}")
tj = json.load(open(os.path.join(KOREN, "tablicy/xgpon_hec_sindromy.json")))["данные"]
ok(z["имя"], "tablicy/xgpon_hec_sindromy.json = свой расчёт", all(int(tj[str(p)], 16) == moi[p] for p in range(1, 64)))

z = zapis("XG-PON/XGS-PON/HSP скремблер")
str_ok(z, G9873, 47, "10.4.1: x^58 + x^39 + 1, 51 бит счётчика + 7 единиц", "The polynomial used is x58 + x39 + 1", "The seven least significant bits of the preload are set to 1")
t = stranica(G9873, 116); t = t[t.index("Table A.5"):]
bits = "".join(re.findall(r"(?m)^\s*([01]{4})\s*$", t))
X = [None] + [1] * 7 + [0] * 51; out = []
for _ in range(len(bits)):
    out.append(X[58]); X = [None, X[39] ^ X[58]] + X[1:58]
ok(z["имя"], f"табл. A.5: свой регистр (X1…X7 = 1, счётчик 0, выход X58, обратная связь X39⊕X58) — {len(bits)} бит", "".join(map(str, out)) == bits and len(bits) == 256)

z = zapis("XG-PON/XGS-PON/NG-PON2: RS(248,216)")
str_ok(z, G9873, 117, "Annex B.1/B.2: поле x^8+x^4+x^3+x^2+1, G(z) = ∏_{i=0}^{15}, z^16·I(z) mod G", "Annex B", "Forward error correction using shortened Reed-Solomon codes", "Construction of RS(248, 232) codeword")
str_ok(z, G9873, 118, "B.3: RS(248,216)", "Construction of RS(248, 216) codeword")
str_ok(z, G9873, 44, "10.1.3: ссылка на Annex B", "RS(248, 216) and RS(248, 232) codes are formally described in Annex B")
ispr(z["имя"], "источник Annex B G.987.3", "стр. 6", "стр. 117–119", "стр. 6 PDF — оглавление; текст Annex B (B.1 поле, B.2/B.3 построение слов, рис. B.1/B.2) — стр. 117–119")

z = zapis("50G-PON (HSP, G.9804.2)")
G98042 = I + "T-REC-G.9804.2-202109-I.pdf"
str_ok(z, G98042, 208, "B.1.2: материнский код 12×69, Z = 256", "17280", "14592", sosedi=2)
Hc = json.load(open(os.path.join(KOREN, "tablicy/hsp_ldpc_17280_14592_Hc.json")))["данные"]
ok(z["имя"], "таблица Hc: 12 × 69, сдвиги −1…255", len(Hc) == 12 and all(len(r) == 69 and all(-1 <= v < 256 for v in r) for r in Hc))
# своя проверка ранга развёрнутой H над GF(2) (битовые строки как целые)
def rang_qc(Hc, Z):
    rows = []
    for r in Hc:
        for s in range(Z):
            v = 0
            for j, a in enumerate(r):
                if a >= 0: v |= 1 << (j * Z + (s + a) % Z)
            rows.append(v)
    piv = {}
    for v in rows:
        while v:
            h = v.bit_length() - 1
            if h in piv: v ^= piv[h]
            else: piv[h] = v; break
    return len(piv)
rk = rang_qc(Hc, 256)
ok(z["имя"], "свой ранг развёрнутой H (3072 × 17664) = 3072 → k материнского = 14592", rk == 3072, f"ранг {rk}")
# своя разборка печатной таблицы Hc (стр. 208–210: строки печатной таблицы = столбцы матрицы, C1…C12 = строки)
tok = []
for p in (208, 209, 210):
    L = [l.strip() for l in stranica(G98042, p).split("\n")]
    if p == 208: L = L[[i for i, l in enumerate(L) if "compact form of the parity-check matrix" in l][-1] + 1:]
    else: L = L[[i for i, l in enumerate(L) if l == "C12"][0] + 1:]
    if p == 210: L = L[:[i for i, l in enumerate(L) if l.startswith("B.1.3")][0]]
    tok += [int(l.replace("−", "-")) for l in L if re.fullmatch(r"[−-]?\d+", l)]
moi = [[tok[12 * j + i] for j in range(69)] for i in range(12)] if len(tok) == 828 else None
ok(z["имя"], "печатная таблица Hc разобрана заново (828 значений, транспонирование) = tablicy/hsp_ldpc_17280_14592_Hc.json", moi == Hc, f"{len(tok)} значений")
