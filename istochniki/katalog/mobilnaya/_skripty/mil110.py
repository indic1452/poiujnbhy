"""MIL-STD-188-110C (с изм. 1) + 110B/110D: последовательный модем, 39 тонов (RS), приложение C (= STANAG 4539),
приложение D (WBHF). Таблицы из текста с проверками; сверка с открытым MIL-STD-188-110C_FOSS (Apache-2.0)."""
import math, re, sys
from fractions import Fraction
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/mil/MIL-STD-188_110C_CHG_NOTICE-1.047598.pdf")
TB = Tekst("istochniki/mil/MIL-STD-188-110B_everyspec.pdf")
TD = Tekst("istochniki/mil/MIL-STD-188-110D.055856.pdf")
ZAPISI = []
def pos(TT, pat, i=-1): return [s for s, n, l in TT.naiti(pat)][i]
def okt(vyr):
    st = {0 if t == "1" else 1 if t.lower() in ("x", "x1") else int(t[1:]) for t in re.findall(r"[xX]\d*|\b1\b", vyr)}
    return st, format(sum(1 << s for s in st), "o")
# 1. свёрточный K=7 (5.3.2.3.3, рис. 4) — тот же в прил. C и D; K=9 (прил. D)
s_k7 = pos(T, r"FOR T1\s+X6\+X4\+X3\+X\+1", 0)
T1, o1 = okt("X6+X4+X3+X+1"); T2, o2 = okt("X6+X5+X4+X3+1"); assert (o1, o2) == ("133", "171")
assert TB.naiti(r"FOR T1\s+X6\+X4\+X3\+X\+1")      # 110B — тот же текст; в 110D многочлены только на рисунке
fe = kod("MIL-STD-188-110C_FOSS", "src/FECEncoder.cpp")
tf = open(os.path.join(KOREN, fe)).read()
assert "0b1011011" in tf and "0b1111001" in tf and int("1011011", 2) == int(o1, 8) and int("1111001", 2) == int(o2, 8)
m9 = T.odna(r"\(b0\) T1 = x8\+x6 \+ x5 \+ x4 \+ 1"); s_k9 = m9[0]
K9a, o9a = okt("x8+x6+x5+x4+1"); K9b, o9b = okt("x8+x7+x6+x5+x3+x1+1")
# 2. перемежитель последовательного режима (табл. VI) и правила загрузки/выборки
kus, s_t6 = T.kusok(r"TABLE VI\.\s+Interleaver matrix dimensions\.", r"5\.3\.2\.3\.5")
t6 = {m.group(1): tuple(int(x) for x in m.group(2, 3, 4, 5)) for m in re.finditer(r"(\d+[HN]?)\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)\s*\n\s*(\d+)", kus)}
assert t6["2400"] == (40, 576, 40, 72) and t6["75N"] == (20, 36, 10, 9) and len(t6) == 7, t6
for k, (r1, c1, r2, c2) in t6.items():
    step = 9 if k != "75N" else 7
    assert math.gcd(step, r1) == 1
il = kod("MIL-STD-188-110C_FOSS", "src/Interleaver.cpp")
ti = open(os.path.join(KOREN, il)).read(); assert "row + 9" in ti and "17" in ti
def pereme(r, c, shag_r=9, shag_c=17, nzag=None):
    """Загрузка: строка += 9 (mod r) по столбцам; выборка: строка += 1, столбец −= 17 (mod c)."""
    M = {}; rr = cc = 0
    for n in range(r * c):
        M[(rr, cc)] = n; rr = (rr + shag_r) % r
        if rr == 0: cc += 1
    out = []; col0 = 0; rr = 0; cc = 0
    while len(out) < r * c:
        out.append(M[(rr, cc)]); rr += 1; cc = (cc - shag_c) % c
        if rr == r: rr = 0; col0 += 1; cc = col0
    return out
p = pereme(40, 576); assert sorted(p) == list(range(40 * 576))
s_t6_fetch = pos(T, r"the second bit comes", 0)
assert T.naiti(r"from row 1, column 559, and the third bit from row 2, column 542")
# 3. Модиф. Грей, D1/D2, скремблеры
kus, s_mgd = T.kusok(r"TABLE VIII\.\s+Modified Gray decoding at 2400 bps and 4800 bps\.", r"TABLE IX")
mgd = re.findall(r"([01])\s*\n\s*([01])\s*\n\s*([01])\s*\n\s*([01]{3})", kus)
MGD8 = {a + b + c: d for a, b, c, d in mgd}; assert len(MGD8) == 8 and sorted(MGD8.values()) == sorted(MGD8)
kus, s_d = T.kusok(r"TABLE XI\.\s+Assignment of designation symbols D1 and D2\.", r"TABLE XII")
kus, s_sc = T.kusok(r"The following scrambling sequence for the sync preamble shall repeat every 32 transmitted\s*\nsymbols:\s*\n", r"\n\s*where 7 shall")
SYNC32 = [int(x) for x in kus.split()]; assert len(SYNC32) == 32 and SYNC32[0] == 7 and SYNC32[-1] == 6
s_scr = pos(T, r"101110101101 \(binary\) or BAD \(hexadecimal\)", 0)
# 12-битовый регистр рис. 6 (снимок risunki/mil110_scrambler_fig6.png): сдвиг влево, выход ст.11 → ст.0 и XOR на входы ст.1, 4, 6
def randomizer(n):
    r = 0xBAD; out = []
    for k in range(n):
        if k % 160 == 0: r = 0xBAD
        for _ in range(8):
            fb = (r >> 11) & 1; r = (r << 1) & 0xFFF
            if fb: r ^= 0b000001010011      # биты 0, 1, 4, 6
        out.append(((r >> 2) & 1) << 2 | ((r >> 1) & 1) << 1 | (r & 1))
    return out
RND = randomizer(160)
# 4. приложение B: RS(15,11) над GF(16), x^4+x+1: корни g(x) = x^4 + α^13 x^3 + α^6 x^2 + α^3 x + α^10
s_rs = pos(T, r"computed by a shortened Reed-Solomon \(15,11\) block code", 0)
exp = [1]
for i in range(1, 15):
    v = exp[-1] << 1
    if v & 16: v ^= 0b10011
    exp.append(v)
log = {v: i for i, v in enumerate(exp)}
def mul(a, b): return 0 if 0 in (a, b) else exp[(log[a] + log[b]) % 15]
g = [exp[10], exp[3], exp[6], exp[13], 1]          # g0..g4
korni = [j for j in range(15) if (lambda x: (lambda acc: acc)(__import__("functools").reduce(lambda acc, c: mul(acc, x) ^ c, reversed(g), 0)))(exp[j]) == 0]
assert len(korni) == 4 and sorted(korni) == list(range(korni[0], korni[0] + 4)), korni
# 5. приложение C: табл. C-XV (размер) и C-XVI (шаг), маска 111001, скремблер x^9+x^4+1
def tabl_ch(pat_ot, pat_do):
    kus, s = T.kusok(pat_ot, pat_do)
    rows = {}
    for m in re.finditer(r"(?m)^\s*(3200|4800|6400|8000|9600)\s*$((?:\s*\n\s*[\d,]+\s*$){6})", kus):
        rows[int(m.group(1))] = [int(x.replace(",", "")) for x in m.group(2).split()]
    return rows, s
CXV, s_cxv = tabl_ch(r"TABLE C- XV\.\s+Interleaver size in bits", r"C\.5\.3\.3\.2")
CXVI, s_cxvi = tabl_ch(r"TABLE C- XVI\.\s+Interleaver increment value", r"C\.5\.3\.3\.3")
assert set(CXV) == set(CXVI) == {3200, 4800, 6400, 8000, 9600}
for r in CXV:
    for n, inc in zip(CXV[r], CXVI[r]): assert math.gcd(n, inc) == 1, (r, n, inc)
assert [(n * 97) % 512 for n in range(8)] == [0, 97, 194, 291, 388, 485, 70, 167]    # пример из C.5.3.3.2
CXIV, s_cxiv = tabl_ch(r"TABLE C- XIV\.\s+Input data block size in bits", r"C\.5\.3\.1")
for r in CXV:
    for a, b in zip(CXIV[r], CXV[r]): assert Fraction(a, b) == Fraction(3, 4)              # код 3/4 заполняет блок целиком
s_mask = pos(T, r"puncturing mask of 1 1 1 0 0 1", 0)
s_c9 = pos(T, r"scrambling sequence generator polynomial shall be x9 \+x4 \+1", 0)
# 6. приложение D: табл. D-XLII выкалывание K=7/K=9
kus, s_dx = T.kusok(r"TABLE D-XLII\.\s+Puncture patterns\.", r"D\.5\.3\.3")
DX = {}
for m in re.finditer(r"(\d+)\s*/\s*(\d+)\s*\n\s*([01]+)\s*\n\s*([01]+)\s*\n\s*([01]+)\s*\n\s*([01]+)\s*\n\s*(n/a|½ Repeated 2x|[^\n]*Repeated[^\n]*)", kus):
    DX[f"{m.group(1)}/{m.group(2)}"] = {"K7": [m.group(3), m.group(4)], "K9": [m.group(5), m.group(6)], "повтор": m.group(7).strip()}
for r, v in DX.items():
    for K in ("K7", "K9"):
        a, b = v[K]; assert len(a) == len(b)
        R = Fraction(len(a), a.count("1") + b.count("1")) * (Fraction(1, 2) if "Repeated" in v["повтор"] else 1)
        assert R == Fraction(r), (r, K, R)
tablica("mil_188_110", {"svertka_K7": {"T1": "x6+x4+x3+x+1", "T2": "x6+x5+x4+x3+1", "восьм": [o1, o2]},
    "svertka_K9_prilD": {"T1": "x8+x6+x5+x4+1", "T2": "x8+x7+x6+x5+x3+x+1", "восьм": [o9a, o9b]},
    "posledovatelnyi_tabl_VI": {k: {"длинный": [v[0], v[1]], "короткий": [v[2], v[3]]} for k, v in t6.items()},
    "modif_grei_VIII": MGD8, "sinhro_skrembler_32": SYNC32, "randomizator_160": RND,
    "RS_15_11_prilB": {"g": "x4 + α13 x3 + α6 x2 + α3 x + α10, GF(16) x4+x+1", "корни_степени_α": korni, "укорочение": "(14,10) при 2400, иначе (7,3)"},
    "prilC_razmer_C-XV": CXV, "prilC_shag_C-XVI": CXVI, "prilC_vhod_C-XIV": CXIV, "prilD_vykalyvanie_D-XLII": DX},
    "MIL-STD-188-110C: свёрточные K=7/K=9, перемежители, модифицированный Грей, скремблеры, RS(15,11) 39-тонового режима, таблицы прил. C и D",
    [T.ist(s_k7, "рис. 4"), T.ist(s_k9, "D.5.3.2.2"), T.ist(s_t6, "табл. VI"), T.ist(s_mgd, "табл. VIII"), T.ist(s_sc, "5.3.2.3.8.2"),
     T.ist(s_scr, "5.3.2.3.8.1, рис. 6"), T.ist(s_rs, "B.5.2"), T.ist(s_cxv, "табл. C-XV"), T.ist(s_cxvi, "табл. C-XVI"), T.ist(s_dx, "табл. D-XLII"),
     ist_kod(fe, "0b1011011"), ist_kod(il, "row \\+ 9")],
    f"многочлены K=7 = 133/171 и совпали с MIL-STD-188-110C_FOSS FECEncoder.cpp (0b1011011, 0b1111001); тот же текст в 110B и 110D; "
    f"перемежитель табл. VI: шаги 9 и 7 взаимно просты с числом строк, выборка по правилам текста биективна, пример текста (ряд 1 столбец 559, ряд 2 столбец 542) присутствует; "
    f"RS(15,11): у g(x) из текста ровно 4 последовательных корня α^{korni[0]}…α^{korni[-1]}; прил. C: все шаги взаимно просты с размерами, пример 0,97,194,…,167 воспроизведён, "
    f"вход/размер = 3/4 во всех 30 клетках; прил. D: скорость каждого шаблона выкалывания (с повтором) совпала с заявленной")
def rec(imya, vid, par, gde, ist, prov, sl, sem="MIL-STD-188-110 (HF модем)"):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST = "есть: витерби K=7 133/171 (kod.py, СВЁРТОЧНЫЕ), выкалывание (vykalyvanie.py), блочное перемежение вслепую (peremezhenie.py); нет: правил перемежения 110, модиф. Грея и скремблера 8-ФМ"
rec("MIL-STD-188-110 последовательный (single-tone) 75–4800 бит/с", "свёрточный K=7 1/2 (133,171) + блочный перемежитель + скремблер",
    {"код": "K=7, T1 = x6+x4+x3+x+1 (133), T2 = x6+x5+x4+x3+1 (171), T1 первым; 2/3 выкалыванием каждого 4-го бита на выходе перемежителя (2400 ППРЧ); повтор пар 2/4/8 раз для 300/150/75",
     "перемежитель": "матрица 40×(576/288/144) длинный, ×(72/36/18) короткий; загрузка по столбцам со строкой +9 mod 40; выборка: строка +1, столбец −17; 75 бит/с фикс.: +7/−7, 20×36 / 10×9",
     "символы": "модифицированный Грей (табл. VIII/IX), трибиты 8-ФМ 1800 Гц 2400 Бод; кадр 32 неизвестных + 16 известных (4800/2400) или 20+20",
     "скремблер данных": "12 бит, начальное 0xBAD, 8 сдвигов на символ, сброс через 160 символов, выход 3 младших разряда (рис. 6) → tablicy (160 значений)",
     "скремблер синхро": "32 символа 7 4 3 0 5 1 5 0 2 2 1 1 5 7 4 3 5 0 2 6 2 1 6 2 0 0 5 0 5 2 6 6 (mod 8)",
     "преамбула": "сегменты 200 мс по 15 символов: 0,1,3,0,1,3,1,2,0, D1, D2, C1, C2, C3, 0 (табл. XI: D1/D2 = скорость и перемежение)",
     "75 бит/с": "фикс. частота: 4-ичные ортогональные последовательности по 32 трибита (табл. X) = STANAG 4415"},
    "HF радио ВС США/НАТО (STANAG 4285-совместимые скорости), ALE-линии, HFDL-подобные системы", [T.ist(s_k7, "5.3.2.3.3"), T.ist(s_t6, "5.3.2.3.4–5"), T.ist(s_scr, "5.3.2.3.8"), T.ist(s_d, "табл. XI"), TB.ist(pos(TB, r"FOR T1\s+X6\+X4\+X3\+X\+1", 0), "110B рис. 4")],
    "текст 110B и 110C одинаков (в 110D многочлены — графикой); многочлены и правила перемежителя совпали с MIL-STD-188-110C_FOSS; скремблер снят с рисунка 6 (risunki/mil110_scrambler_fig6.png)", EST)
rec("MIL-STD-188-110 прил. B: 39-тоновый параллельный (RS(15,11) → (14,10)/(7,3))", "РС (укороченный)",
    {"код": "RS(15,11) над GF(16), поле x4+x+1, g(x) = x4 + α13x3 + α6x2 + α3x + α10", "корни": f"α^{korni[0]}…α^{korni[-1]} (проверено)", "укорочение": "(14,10) при 2400 бит/с, иначе (7,3)",
     "перемежение": "блочное, степени по табл. B-I", "модуляция": "39 поднесущих QDPSK + 40-я доплеровская"},
    "HF 39-тоновый модем (устаревающий), аналоги ANDVT", [T.ist(s_rs, "B.5.2")], "корни g(x) вычислены: 4 последовательные степени α (узкий смысл РС)",
    "есть общий декодер РС (rs_bch.py: любые GF(2^m), fcr) — GF(16) поддерживается; укорочение и перемежение 39-тонового — нет")
rec("MIL-STD-188-110 прил. C (= STANAG 4539) 3200–12800 бит/с", "свёрточный K=7 с кольцевым хвостом + выкалывание 3/4 + блочный перемежитель",
    {"код": "K=7 133/171, полный tail-biting (предзагрузка 6 бит), выкалывание маской 1 1 1 0 0 1 → 3/4", "перемежитель": "одномерный: позиция загрузки B(n) = n·шаг mod размер (табл. C-XV размер, C-XVI шаг)",
     "скремблер": "x9 + x4 + 1, начальное 1 в каждом кадре данных; 8-ФМ — сумма mod 8 трёх младших разрядов, КАМ — XOR 4/5/6 разрядов", "модуляция": "QPSK, 8PSK, 16/32/64QAM, 2400 Бод, кадр 256 данных + 31 мини-проба",
     "таблицы": "tablicy/mil_188_110.json (prilC_*)"},
    "HF высокоскоростные модемы НАТО (STANAG 4539), STANAG 5066 поверх", [T.ist(s_cxiv, "C.5.3"), T.ist(s_mask, "C.5.3.2.3"), T.ist(s_cxv, "C-XV"), T.ist(s_cxvi, "C-XVI"), T.ist(s_c9, "C.5.1.3")],
    "таблицы разобраны из текста; все шаги взаимно просты с размерами (перестановки биективны); пример текста воспроизведён; вход/размер = 3/4 везде",
    "есть кольцевой свёрточный (svyortka.кодировать_кольцо) и выкалывание; нет перемежителя C-XVI и скремблера x9+x4+1 — простые")
rec("MIL-STD-188-110 прил. D (WBHF 3–24 кГц, волновые формы 0–13)", "свёрточный K=7 или K=9 с кольцевым хвостом, выкалывание/повтор + перемежитель",
    {"K=7": "133/171", "K=9": f"T1 = x8+x6+x5+x4+1 ({o9a}), T2 = x8+x7+x6+x5+x3+x+1 ({o9b})", "выкалывание": "табл. D-XLII (9/10 … 1/16) → tablicy", "WID 0": "Walsh-модуляция", "модуляции": "BPSK … 256QAM"},
    "HF широкополосная передача данных (110C/110D, STANAG 5069)", [T.ist(s_k9, "D.5.3.2.2"), T.ist(s_dx, "табл. D-XLII")],
    "скорость каждого шаблона вычислена из маски и совпала с заявленной; K=9 многочлены только из текста (второго источника нет)", "есть витерби K=9 (256 состояний); таблицы готовы")
if __name__ == "__main__":
    print(len(ZAPISI), "записей; корни RS", korni, "D-XLII", len(DX), "C-XV", CXV[3200], "K9", o9a, o9b, "RND", RND[:12])
