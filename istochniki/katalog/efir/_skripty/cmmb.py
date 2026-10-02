"""CMMB (GY/T 220.1-2006): RS(240,K) над GF(256) с байтовым перемежением, LDPC (9216; 1/2, 3/4), битовый перемежитель,
скремблер x^12+x^11+x^8+x^6+1, ПСП x^11+x^9+1. Проверка — вычисление порождающих многочленов RS по табл. B2–B4,
сравнение LDPC с data/ldpc_kitay.json проекта."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

C = Tekst("istochniki/cmmb/GYT_220.1-2006.pdf"); ZAPISI = []
s_rs, _, _ = C.odna(r"RS编码和字节交织按照按列输入和输出，按行编码的方式进行")
s_rs2, _, _ = C.odna(r"数，校验字节数为\(240－K\)。RS\(240,K\)码提供4种模式")
exp = [1]
for _ in range(254):
    v = exp[-1] << 1
    if v & 0x100: v ^= 0x11D
    exp.append(v)
log = {v: i for i, v in enumerate(exp)}
def gm(a, b): return 0 if 0 in (a, b) else exp[(log[a] + log[b]) % 255]
def gen(nr, fcr):
    g = [1]
    for i in range(fcr, fcr + nr):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c; ng[j + 1] ^= gm(c, exp[i % 255])
        g = ng
    return g   # от старшей степени
tabl = {}; opech = []
for tn, nr, nxt in (("表B2", 16, "表B3"), ("表B3", 48, "表B4"), ("表B4", 64, r"附\s*录\s*C|附录C")):
    kus, s = C.kusok(rf"{tn}\s*\nRS\(240,\d+\)生成多项式系数", nxt, posl=True)
    kus = bez_kolontitulov(kus, r"GY/T 220")
    nums = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]
    # пары «i gi» подряд (строки по 4 пары); собираем все пары и упорядочиваем по метке i
    pp = list(zip(nums[0::2], nums[1::2]))
    metki = [k for k, v in pp]
    assert len(pp) == nr + 1, (tn, len(pp))
    gi = [v for k, v in sorted(pp)]
    propusk = sorted(set(range(nr + 1)) - set(metki))
    if propusk: opech.append(f"{tn}: в нумерации i пропущено {propusk}, последняя метка {max(metki)} (опечатка; значения по порядку верны)")
    najd = [f for f in range(0, 3) if gen(nr, f)[::-1] == gi]
    assert najd, (tn, gi[:5], gen(nr, 0)[::-1][:5])
    tabl[f"RS(240,{240 - nr})"] = {"g_i": gi, "первый корень": f"α^{najd[0]}", "страница": s}
ZAPISI.append(Z("CMMB внешний код RS(240,K), K = 240/224/192/176, укороченный из RS(255,K+15), с байтовым блочным перемежителем", "CMMB (GY/T 220.1)", "РС",
  {"поле": "GF(256), p(x) = x^8+x^4+x^3+x^2+1", "g(x)": {k: f"∏(x+α^i), i = {v['первый корень'][2:]}…; коэффициенты g0…gN из табл. B: {v['g_i']}" for k, v in tabl.items()},
   "перемежитель": "блочный: 240 столбцов (длина RS) × MI строк (табл. 2), запись по столбцам, RS — по строкам", "RS(240,240)": "без кодирования (режим 0)"},
  "CMMB (мобильное ТВ КНР, 2/8 МГц, S-диапазон и УВЧ)", [C.ist(s_rs, "5.1"), C.ist(s_rs2, "4 режима K")] + [C.ist(v["страница"], k + " табл. B") for k, v in tabl.items()],
  "порождающие многочлены RS(240,224), (240,192), (240,176) перемножены над GF(256) и совпали с коэффициентами табл. B2–B4 (первый корень α^" + ",".join(v["первый корень"][2:] for v in tabl.values()) + ")" + ("; " + "; ".join(opech) if opech else ""),
  "частично: rs_bch.py (RS над GF(256) вслепую, укороченный)"))

PRO = json.load(open("/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_kitay.json"))
s_l, _, _ = C.odna(r"^5\.2 LDPC编码")
s_h, _, _ = C.odna(r"附\s*录\s*D|附录D.*LDPC码奇偶校验矩阵H")
ZAPISI.append(Z("CMMB LDPC (9216, 4608) и (9216, 6912): регулярные (3,6) и (3,12), H в прил. D, отображение бит — прил. C", "CMMB (GY/T 220.1)", "LDPC",
  {"R=1/2": "(9216, 4608), H 4608 × 9216", "R=3/4": "(9216, 6912), H 2304 × 9216", "отображение": "проверочные и информационные биты размещаются по вектору COL_ORDER (прил. C, формула 10)",
   "в проекте": [k for k in PRO if k.startswith("cmmb")]},
  "CMMB", [C.ist(s_l, "5.2"), C.ist(s_h, "прил. D")], "матрицы прил. D разобраны в проекте (data/ldpc_kitay.json, «откуда» — прил. D/C этого же документа); повторно не разбирались",
  "есть: data/ldpc_kitay.json (cmmb-9216-4608, cmmb-9216-6912), ldpc_kitay.py"))

kus8, s8 = C.kusok(r"表8 扰码移位寄存器初始值", r"图15", posl=True)
opc = re.findall(r"\n(\d)\s*\n([01]{4} [01]{4} [01]{4})", kus8)
assert len(opc) == 8
s_sc, _, _ = C.odna(r"^5\.6 扰码")
dubli = [(a, b) for i, (a, x) in enumerate(opc) for (b, y) in opc[i + 1:] if x == y]
ZAPISI.append(Z("CMMB скремблер: комплексная ПСП Si/Sq, x^12 + x^11 + x^8 + x^6 + 1, 8 начальных состояний", "CMMB (GY/T 220.1)", "скремблер (рандомизатор)",
  {"многочлен": "x^12 + x^11 + x^8 + x^6 + 1", "начальные": {a: b for a, b in opc}, "применение": "Pc(i) = ((1 − 2Si) + j(1 − 2Sq))/√2 — умножение всех поднесущих (данные и пилоты)",
   "замечание": f"в табл. 8 совпадают начальные значения вариантов {dubli}" if dubli else "", "ПСП маяка": "x^11 + x^9 + 1 (5.? синхро/маяк)"},
  "CMMB", [C.ist(s_sc, "5.6"), C.ist(s8, "Table 8")], "многочлен и 8 начальных значений разобраны из текста", "частично: skrembler.py (скремблер на уровне бит — здесь символьный)"))
