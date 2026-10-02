"""RDS / RBDS (NRSC-4 1998 = EN 50067:1998 с дополнениями США; NRSC-4-B): укороченный циклический код (26,16),
смещения A/B/C/C'/D(/E), синдромы. Сверка — redsea (windytan, MIT) и вычислением по g(x)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

R = Tekst("istochniki/rds/nrsc-4-1998.pdf"); RB = Tekst("istochniki/rds/nrsc-4-b.pdf"); ZAPISI = []
rs = kod("redsea", "src/block_sync.cc")
s_g, _, l_g = R.odna(r"^g\(x\) = x10 \+ x8 \+ x7 \+ x5 \+ x4 \+ x3 \+ 1")
g = mnogochlen(stepeni(l_g.split("=")[1]))
sA = R.odna(r"^Table A\.1")[0]
kusB, sB = R.kusok(r"Table B\.1\s*\n", r"B\.2\.2", posl=True)
syn = {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(r"\n(A|B|C'|C|D)\s*\n([01]{10})\s*\n([01]{10})", kusB)}
assert set(syn) == {"A", "B", "C", "C'", "D"}, syn
_t = open(os.path.join(KOREN, rs)).read()
H = [int(x, 2) for x in re.findall(r"0b([01]{10})", _t.split("parity_check_matrix{")[1].split("};")[0])]
assert len(H) == 26
def xm(n):
    r = 1
    for _ in range(n):
        r <<= 1
        if r >> 10 & 1: r ^= g
    return r
assert all(xm((9 - k) % 1023) == H[k] for k in range(26))   # H — степени x по модулю g(x) из текста
def sindrom(v):
    r = 0
    for k in range(26):
        if v >> k & 1: r ^= H[25 - k]
    return r
for o, (d, s) in syn.items():
    assert sindrom(int(d, 2)) == int(s, 2), (o, d, s)
t_rs = open(os.path.join(KOREN, rs)).read()
for o, (d, s) in syn.items():
    assert f"0b{s}" in t_rs and f"0b{d}" in t_rs
s_d, _, _ = R.odna(r"The source data at the transmitter are differentially encoded according to the following rules")
s_E, _, _ = R.odna(r"For obtaining RDS information from an RDS/MMBS multiplex signal the E offset word must be recognized")
ZAPISI.append(Z("RDS/RBDS: укороченный циклический код (26,16) g(x)=x^10+x^8+x^7+x^5+x^4+x^3+1 со смещениями A, B, C, C', D (E — MMBS)", "RDS / RBDS (EN 50067, NRSC-4)", "CRC-подобный (укороченный циклический, исправляет пакеты ≤ 5 бит)",
  {"блок": "26 бит = 16 информационных + 10 проверочных (остаток x^10·m(x) mod g(x)) ⊕ смещение", "группа": "4 блока = 104 бита, смещения A, B, C (версия A) или C' (версия B), D",
   "смещения d9…d0": {o: d for o, (d, s) in syn.items()}, "синдромы смещений": {o: s for o, (d, s) in syn.items()}, "E": "0000000000 (RBDS/MMBS, в RDS не применяется)",
   "модуляция": "дифференциальное кодирование, бифазный (манчестерский) символ, 1187,5 бит/с, поднесущая 57 кГц (3×19 кГц), DSB-SC",
   "свойства": "обнаруживает одиночные пакеты ошибок ≤ 10 бит, исправляет пакеты ≤ 5 бит"},
  "FM-радиовещание: RDS (Европа, СНГ), RBDS (США), также RDS в DRM/DAB-сигнализации PI", [R.ist(s_g, "2.3 g(x)"), R.ist(sA, "Table A.1"), R.ist(sB, "Table B.1"), R.ist(s_d, "дифф. кодирование"), R.ist(s_E, "E offset"), ist_kod(rs, r"0b1111011000", "redsea синдромы"), ist_kod(rs, r"parity_check_matrix", "redsea H")],
  "g(x) из текста; все 26 строк проверочной матрицы redsea = x^((9−k) mod 1023) mod g(x); синдромы табл. B.1 вычислены из смещений табл. B.1 по этой матрице и совпали; двоичные значения смещений и синдромов есть в redsea",
  "частично: crc.py/kod.блочный (циклический код находится вслепую); смещения RDS не встроены"))
