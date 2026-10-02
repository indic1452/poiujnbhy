"""Независимая сверка записей интерфейсов (USB4 Gen 2/3/4)."""
import re
from pv import *
U = "istochniki/interfeisy/USB4_Specification_November_2025/USB4 Specification November 2025/USB4 Specification 2.0 November 2025 - CLEAN.pdf"

def tablica_par(t, ot, do):
    k = t[t.index(ot):(t.index(do, t.index(ot)) if do and do in t[t.index(ot):] else len(t))]
    nums = [int(x) for x in re.findall(r"(?<![\w.-])(\d+)(?![\w.])", k.split("Value", 1)[1] if "Value" in k else k)]
    d = {}
    for i in range(0, len(nums) - 1):
        if nums[i] not in d and (not d or nums[i] == max(d) + 1): d[nums[i]] = nums[i + 1]
    return d

z = zapis("USB4 Gen 2/Gen 3")
str_ok(z, U, 255, "4.3.1.6: RS(198,194) над GF(2^8), p(x), g(x)", "RS(198,194) code over GF(28)", "p(x) = X^8 + X^4 + X^3 + X^2 + 1", "g(x) = X^4 + 15X^3 + 54X^2 + 120X + 64")
ok(z["имя"], "g(x) = (x+1)(x+α)(x+α^2)(x+α^3) над 0x11D = x^4 + 15x^3 + 54x^2 + 120x + 64 (свой расчёт)", rs_g(8, 0x11D, range(4)) == [1, 15, 54, 120, 64])
t = "".join(stranica(U, p) for p in range(805, 811))
rez = []
for a, b in (("Table A-5.", "Table A-6."), ("Table A-6.", "Table A-7."), ("Table A-7.", "Table A-8."), ("Table A-8.", "A.5.2")):
    k = t[t.index(a):t.index(b, t.index(a))]
    k = re.sub(r"Version 2\.0\s+- \d+ -\s+Universal Serial Bus 4\s+November 2025\s+Specification\s+Copyright © 2025 USB Promoter Group\.\s+All rights reserved\.", " ", k)
    nums = [int(x) for x in re.findall(r"\b\d+\b", k.split("Value", 1)[1])]
    d = {}; i = 0
    while i + 1 < len(nums) and len(d) < 198:
        if nums[i] == len(d): d[nums[i]] = nums[i + 1]; i += 2
        else: i += 1
    slovo = [d[j] for j in range(198)] if len(d) == 198 else None
    rez.append(slovo is not None and not any(sindromy(slovo, 8, 0x11D, range(4))))
ok(z["имя"], "прил. A, табл. A-5…A-8: все 4 блока по 198 байт — нулевые синдромы при корнях α^0…α^3 (свой расчёт)", all(rez), str(rez))

z = zapis("USB4 Gen 4")
t = " ".join(stranica(U, 261).split())
gt = [int(x) for x in re.search(r"𝑔= \[([\d, ]+)\]", t).group(1).replace(",", " ").split()]
moi = rs_g(11, st(11, 2, 0), range(24))
ok(z["имя"], "4.3.2.3: 25 коэффициентов g из текста = свой расчёт (x^11 + x^2 + 1, корни α^0…α^23)", gt == moi, f"{len(gt)} коэф.")
t = "".join(stranica(U, p) for p in (810, 811, 812))
rez = []
for a, b, dannye in (("Table A-9.", "Table A-10.", list(range(1, 481))), ("Table A-10.", "Table A-11.", [0] * 479 + [1]), ("Table A-11.", "Table A-12.", list(range(480, 0, -1)))):
    k = t[t.index(a):t.index(b, t.index(a) + 3)] if b in t[t.index(a) + 3:] else t[t.index(a):]
    k = re.sub(r"Version 2\.0\s+- \d+ -\s+Universal Serial Bus 4\s+November 2025\s+Specification\s+Copyright © 2025 USB Promoter Group\.\s+All rights reserved\.", " ", k)
    nums = [int(x) for x in re.findall(r"\b\d+\b", k.split("Value", 1)[1])]
    pr = []
    for i in range(len(nums) - 47):
        if all(nums[i + 2 * j] == 480 + j for j in range(24)):
            pr = [nums[i + 2 * j + 1] for j in range(24)]; break
    rez.append(rs_proverochnye(dannye, moi, 11, st(11, 2, 0)) == pr[:24] and len(pr) >= 24)
ok(z["имя"], "прил. A, табл. A-9…A-11: свой кодер → 24 проверочных символа P23…P0 совпали", all(rez), str(rez))

# ---------- CPRI 7.0 RS-FEC: информативный пример 6.10 (стр. 95–98) ----------
import json
z = zapis("CPRI 7.0")
C = "istochniki/interfeisy/CPRI_v_7_0_2015-10-09.pdf"
str_ok(z, C, 95, "6.10: пример RS-FEC (информативный), начало кодового слова — первый базовый кадр гиперкадра", "6.10. RS-FEC Coding Example (Informative)")
ispr(z["имя"], "страница источника для 6.10", "стр. 3", "стр. 95–98", "стр. 3 PDF — оглавление; пример 6.10 — стр. 95 (вход 64B/66B), 96 (скремблер PCS), 97 (транскодер и выход кодера РС), 98 (выход скремблера PN-5280)")
t97 = stranica(C, 97); t98 = stranica(C, 98)
a = t97[t97.index("transcoder:"):t97.index("Output of RS-FEC encoder:")]
xc = re.findall(r"(?m)^\s*([01])\s*\n\s*([0-9A-F]{8}) ([0-9A-F]{8})\s*\n\s*((?:[0-9A-F]{8} ?){6})", a)
b = t97[t97.index("Output of RS-FEC encoder:"):]
enc = re.findall(r"(?m)^\s*([01])\s*\n\s*([0-9A-F]{64})\s*$", b)
encpar = "".join(re.findall(r"[0-9A-F]+", b[b.rindex(enc[-1][1]) + 64:]))
c = t98[t98.index("scrambler:"):]
scr = re.findall(r"(?m)^\s*([01])\s*\n\s*([0-9A-F]{64})\s*$", c)
scrpar = "".join(re.findall(r"[0-9A-F]+", c[c.rindex(scr[-1][1]) + 64:].split("CPRI")[0]))
X = []
for h, w1, w2, rest in xc: X += [int(h)] + bity_hex(w1 + w2 + rest.replace(" ", ""))
E_ = []
for h, w in enc: E_ += [int(h)] + bity_hex(w)
E_ += bity_hex(encpar)[:140]
S = []
for h, w in scr: S += [int(h)] + bity_hex(w)
S += bity_hex(scrpar)[:140]
ok(z["имя"], "6.10: 20 блоков 257 бит транскодера = информационная часть выхода кодера (систематичность), 5280 бит выхода кодера и скремблера", len(xc) == 20 and len(enc) == 20 and len(scr) == 20 and X == E_[:5140] and len(E_) == len(S) == 5280)
sym = [sum(bb << k for k, bb in enumerate(E_[10 * i:10 * i + 10])) for i in range(528)]
ok(z["имя"], "6.10: выход кодера — слово RS(528,514): 14 нулевых синдромов (x^10+x^3+1, корни α^0…α^13; бит 0 слова — младший бит символа M513)", not any(sindromy(sym, 10, 0x409, range(14))))
pn = [p ^ q for p, q in zip(E_, S)]
ok(z["имя"], "6.10: PN-5280 = выход кодера ⊕ выход скремблера; все 5280 бит удовлетворяют рекурренте x^58 + x^39 + 1 (многочлен восстановлен по примеру, FC-FS-4 5.4.4 недоступен)", all(pn[n] == pn[n - 39] ^ pn[n - 58] for n in range(58, 5280)))
tab = {"описание": "ПСП PN-5280 скремблера RS-FEC CPRI 7.0 / FC-FS-4 5.4.4 — восстановлена проверяющим как XOR выхода кодера РС (стр. 97) и выхода скремблера (стр. 98) примера 6.10; 5280 бит, бит 0 — первый передаваемый",
       "источник": {"файл": C, "страница": "97–98", "url": "https://www.cpri.info/downloads/CPRI_v_7_0_2015-10-09.pdf"},
       "рекуррента": "s[n] = s[n−39] ⊕ s[n−58] (x^58 + x^39 + 1) — проверена на всех 5280 битах",
       "сборка": "proverka/p_interfeisy.py (assert)", "данные": {"первые 58 бит": "".join(map(str, pn[:58])), "hex (5280 бит, старший бит знака — первый)": "%0*X" % (1320, int("".join(map(str, pn)), 2))}}
assert all(pn[n] == pn[n - 39] ^ pn[n - 58] for n in range(58, 5280))
json.dump(tab, open(os.path.join(KOREN, "tablicy/cpri_pn5280.json"), "w"), ensure_ascii=False, indent=1)
