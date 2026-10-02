"""cdma2000 1x (3GPP2 C.S0002-E, в т.ч. конфигурации RC1/RC2 = IS-95) и 1xEV-DO (C.S0024-A): свёрточные K=9,
турбокод и его перемежитель, CRC качества кадра, перемежитель с обращением бит, длинный код и короткие ПСП, скремблер EV-DO.
Сверка: 1xbts (Apache-2.0, chrismoos/1xbts), каталог CRC проекта (RevEng), вычисленное d_free."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/cdma2000/ARIB_STD-T64-C.S0002-Ev2.0.pdf")
E = Tekst("istochniki/cdma2000/ARIB_STD-T64-C.S0024-Av3.0.pdf")
ZAPISI = []
B = "1xbts"
conv = kod(B, "crates/cdma-bts/src/lib/phy/coding/convolutional.rs")
turbo = kod(B, "crates/cdma-bts/src/lib/phy/hrpd/turbo.rs")
lfsr = kod(B, "crates/cdma-bts/src/lib/phy/lfsr.rs")
lc = kod(B, "crates/cdma-common/src/phy/long_code.rs")
bil = kod(B, "crates/cdma-bts/src/lib/phy/coding/block_interleaver.rs")
scr = kod(B, "crates/cdma-bts/src/lib/phy/hrpd/scrambler.rs")
hcrc = kod(B, "crates/cdma-bts/src/lib/phy/hrpd/crc.rs")
tx = lambda p: open(os.path.join(KOREN, p)).read()

# --- 1. свёрточные: «g0 equals 765 (octal), g1 equals …» ------------------------------------------------
SV = {}
VES1 = " ".join(T.ves.split())
for m in re.finditer(r"generator functions for (?:the rate (1/\d) code|this code) shall be (.{0,260}?)This code", VES1):
    g = [int(x, 8) for _, x in re.findall(r"g(\d) equals (?:\d{1,2} )?(\d{3})", m.group(2))]
    assert [int(k) for k in re.findall(r"g(\d) equals", m.group(2))] == list(range(len(g)))
    r = m.group(1) or f"1/{len(g)}"
    assert r == f"1/{len(g)}", (r, g)
    SV.setdefault(r, g); assert SV[r] == g, r
assert set(SV) == {"1/2", "1/3", "1/4", "1/6"}, SV
s_sv = {r: T.odna(r"g0 equals " + format(SV[r][0], "o"))[0] for r in SV}
DF = {r: dfree(g, 9) for r, g in SV.items()}
assert DF == {"1/2": 12, "1/3": 18, "1/4": 24, "1/6": DF["1/6"]} and DF["1/6"] >= 30, DF
tc = tx(conv)
assert "Encoder::new([0x1eb, 0x171])" in tc and 0x1eb == SV["1/2"][0] and 0x171 == SV["1/2"][1]
assert "Encoder::new([0o557, 0o663, 0o711])" in tc and "Encoder::new([0o765, 0o671, 0o513, 0o473])" in tc
assert [0o557, 0o663, 0o711] == SV["1/3"] and [0o765, 0o671, 0o513, 0o473] == SV["1/4"]

# --- 2. турбокод: многочлены и таблица перемежителя ----------------------------------------------------------
s_tp = T.odna(r"where d\(D\) = 1 \+ D2 \+ D3, n0\(D\) = 1 \+ D \+ D3, and n1\(D\) = 1 \+ D \+ D2 \+ D3")[0]
s_tpe = E.odna(r"where d\(D\) = 1 \+ D2 \+ D3, n0\(D\) = 1 \+ D \+ D3, and n1\(D\) = 1 \+ D \+ D2 \+ D3")[0]
tt = tx(turbo)
for s in ("d(D)  = 1 + D^2 + D^3", "n0(D) = 1 + D + D^3", "n1(D) = 1 + D + D^2 + D^3"): assert s in tt, s
kus, s_lut = T.kusok(r"Table 2\.1\.3\.1\.5\.2\.3-2\. Turbo Interleaver Lookup Table Definition\s*\n\s*1\s*\n", r"=====СТР|Note:|\n\s*2\.1\.3\.1\.5\.3")
ch = [int(x) for x in re.findall(r"\d+", kus)]
ch = ch[ch.index(10) + 1:] if 10 in ch[:20] else ch        # после заголовка «n = 3 … n = 10 Entries»
LUT = {}; i = 0
while i < len(ch) and len(LUT) < 32:
    if ch[i] == len(LUT): LUT[ch[i]] = ch[i + 1:i + 9]; i += 9
    else: i += 1
assert len(LUT) == 32 and all(len(v) == 8 for v in LUT.values()), len(LUT)
for n in range(3, 11):
    assert all(LUT[r][n - 3] % 2 == 1 and LUT[r][n - 3] < (1 << n) for r in range(32)), n   # нечётные множители < 2^n
L1x = [[int(x) for x in re.findall(r"\d+", r)] for r in re.findall(r"\[([\d,\s]+)\]", tt.split("const TURBO_INTERLEAVER_LUT: [[u16; 8]; 32] = [")[1].split("];")[0])]
assert len(L1x) == 32 and all(L1x[r][1:8] == LUT[r][0:7] for r in range(32))       # столбцы n = 3…9 (у 1xbts — n = 2…9)
kus, s_par = T.kusok(r"Table 2\.1\.3\.1\.5\.2\.3-1\. Turbo Interleaver Parameter", r"Table 2\.1\.3\.1\.5\.2\.3-2", posl=True)
chp = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", re.sub(r"=====СТР \d+=====", " ", kus))]
PAR = {chp[j]: chp[j + 1] for j in range(len(chp) - 1) if chp[j] > 100 and 3 <= chp[j + 1] <= 10}
assert len(PAR) == 23 and all((1 << (n + 4)) < N <= (1 << (n + 5)) for N, n in PAR.items()), PAR
def perm_turbo(N):
    n = next(n for n in range(3, 11) if N <= (1 << (n + 5))); out = []
    for c in range(1 << (n + 5)):
        hi = ((c >> 5) + 1) & ((1 << n) - 1)
        lo5 = c & 31; mul = (hi * LUT[lo5][n - 3]) & ((1 << n) - 1)
        a = (int(format(lo5, "05b")[::-1], 2) << n) | mul
        if a < N: out.append(a)
    return out
for N in PAR:
    p = perm_turbo(N); assert sorted(p) == list(range(N)), N
kus_e, s_lut_e = E.kusok(r"Table 12\.2\.1\.3\.4\.2\.3-2\. Turbo Interleaver Lookup Table Definition\s*\n\s*1\s*\n", r"=====СТР|Note")
che = [int(x) for x in re.findall(r"\d+", kus_e)]
ev = {}; i = che.index(7) + 1 if 7 in che[:12] else 0
while i < len(che) and len(ev) < 32:
    if che[i] == len(ev) and i + 5 < len(che): ev[che[i]] = che[i + 1:i + 6]; i += 6
    else: i += 1
assert len(ev) == 32 and all(ev[r] == LUT[r][:5] for r in range(32)), "EV-DO таблица ≠ 1x"
tablica("cdma2000_turbo_perem", {"LUT_n3_n10": [LUT[r] for r in range(32)], "N_turbo_n": PAR,
                                 "primer_N378_pervye_32": perm_turbo(378)[:32]},
        "cdma2000/EV-DO: таблица перемежителя турбокода (32 строки × n = 3…10), параметры n для размеров блока, пример перестановки",
        [T.ist(s_lut, "Table 2.1.3.1.5.2.3-2"), T.ist(s_par, "Table 2.1.3.1.5.2.3-1"), E.ist(s_lut_e, "Table 12.2.1.3.4.2.3-2"), ist_kod(turbo, "TURBO_INTERLEAVER_LUT")],
        "таблица разобрана из текста; все значения нечётны и < 2^n; столбцы n = 3…9 совпали с 1xbts TURBO_INTERLEAVER_LUT (HRPD); столбцы n = 3…7 EV-DO C.S0024-A — те же; "
        "перестановка по 9 шагам 2.1.3.1.5.2.3 биективна для всех 23 размеров N_turbo")

# --- 3. CRC качества кадра -------------------------------------------------------------------------------------
CRC = {}
for m in re.finditer(r"generator polynomial for the (\d+)-bit frame quality indicator(?: for (all reverse link channels\s*\S*\s*except the Reverse Fundamental\s*\S*\s*Channel with Radio Configuration 2|the Reverse Fundamental\s*\S*\s*Channel with Radio Configuration 2))?\s*(?:\S+\s*)?shall be\s*(?:\S+\s*)?(?:=====СТР \d+=====.*?\n\s*\d-\d+\s*)?g\(x\) = ([x\d\s+]+?)\.", T.ves, re.S):
    w = int(m.group(1)); st = [0 if t == "1" else 1 if t == "x" else int(t[1:]) for t in re.findall(r"x\d*|\b1\b", m.group(3))]
    key = f"{w}" + ("-RC2" if m.group(2) and m.group(2).startswith("the") else "")
    CRC[key] = (st, T.stranica_pozicii(m.start()))
assert set(CRC) == {"16", "12", "10", "8", "6", "6-RC2"}, CRC.keys()
rv = crc_reveng(r"CRC-[0-9]+/CDMA2000[^\"]*")
imena = {"16": "CRC-16/CDMA2000", "12": "CRC-12/CDMA2000", "10": "CRC-10/CDMA2000", "8": "CRC-8/CDMA2000", "6": "CRC-6/CDMA2000-A", "6-RC2": "CRC-6/CDMA2000-B"}
for k, (st, _) in CRC.items():
    w, p, init, *_ = rv[imena[k]]
    assert max(st) == w and mnogochlen(st) & ((1 << w) - 1) == p and init == (1 << w) - 1, k
s_init = T.odna(r"shift register\s*$|elements shall be set to logical one")[0]
s_init0 = T.odna(r"5 ms frame on the Forward Packet Data Control Channel, in which case the shift|in which case the shift")[0]   # исключение (добавлено при проверке)
assert "set to logical zero" in " ".join(T.stroki[i] for i in range(len(T.stroki)) if T.str_[i] == s_init0)
s_fcs = E.odna(r"g\(x\) = x24 \+ x23 \+ x6 \+ x5 \+ x \+ 1")[0]
assert "0x1021u16" in tx(hcrc)

# --- 4. перемежитель с обращением бит, длинный код, короткие ПСП, скремблер EV-DO -----------------------------------
s_bro = T.odna(r"BROm\(y\) indicates the bit-reversed m-bit value of y")[0]
assert "2u32.pow(self.params.m as u32) as usize * (i % self.params.j)" in tx(bil) and "bro(self.params.m, i / self.params.j)" in tx(bil)
mlc = re.search(r"p\(x\) = (x42 [^.]*?)\.", " ".join(T.ves.split())); s_lc = T.odna(r"p\(x\) = x42 \+ x35")[0]
st_lc = [0 if t == "1" else int(t[1:]) for t in re.findall(r"x\d+|\b1\b", mlc.group(1))]
assert len(st_lc) == 21 and max(st_lc) == 42
st1x = [0 if t == "1" else int(t.split("^")[1]) for t in re.findall(r"x\^\d+|\b1\b", tx(lc).split("p(x) =")[1].split("\n")[0])]
assert sorted(st1x) == sorted(st_lc)
PI = [15, 13, 9, 8, 7, 5, 0]; PQ = [15, 12, 11, 10, 6, 5, 4, 3, 0]
s_pi = T.odna(r"PI\(x\) = x15 \+ x13 \+ x9 \+ x8 \+ x7 \+ x5 \+ 1")[0]; T.odna(r"PQ\(x\) = x15 \+ x12 \+ x11\s*\+ x10 \+ x6 \+ x5 \+ x4 \+ x3 \+ 1")
tl = tx(lfsr)
taps = lambda name: int(re.search(name + r": u64 = 0b([01]+);", tl).group(1), 2)
assert taps("PN_I_TAPS") == sum(1 << (14 - e) for e in PI if e < 15) and taps("PN_Q_TAPS") == sum(1 << (14 - e) for e in PQ if e < 15)
s_scr = E.odna(r"h\(D\) = D17 \+ D14 \+ 1")[0]
assert "h(D) = D^17 + D^14 + 1" in tx(scr)

def rec(imya, vid, par, gde, ist, prov, sl, sem="cdma2000 / IS-95 (3GPP2 C.S0002)"):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST_SV = "есть частично: svyortka/kod.py — свёрточные 1/n любого K с витерби (многочлены 753/561 и т.д. задаются); разметки каналов cdma2000 нет"
rec("cdma2000/IS-95 свёрточные K=9: 1/2 (753,561), 1/3 (557,663,711), 1/4 (765,671,513,473), 1/6 (457,755,551,637,625,727)", "свёрточный",
    {"K": 9, "многочлены (восьм.)": {r: [format(x, "o") for x in g] for r, g in SV.items()}, "d_free": DF,
     "хвост": "8 нулевых хвостовых бит (Encoder Tail Bits), кодер обнуляется в начале кадра", "порядок выхода": "g0 первым, затем g1 …",
     "IS-95": "RC1/RC2: прямой канал 1/2 (753,561), обратный 1/3 (557,663,711) + 64-ичная ортогональная модуляция Уолша (обратный) и повторение символов",
     "выкалывание": "RC2/RC5 прямого канала: выкалывание 2 из 6 после 1/2 → 3/4 и др. (2.1.3.1.6)"},
    "cdma2000 1x: Sync/Paging/Traffic/Access (все RC), IS-95A/B", [T.ist(s, f"2.1.3.1.5.1 / 3.1.3.1.5.1 rate {r}") for r, s in s_sv.items()] + [ist_kod(conv, "0x1eb, 0x171")],
    f"многочлены разобраны из текста; 1/2, 1/3, 1/4 совпали с 1xbts convolutional.rs; вычислены d_free = {DF} (для 1/2, 1/3, 1/4 — известные оптимальные значения 12/18/24)", EST_SV)
rec("cdma2000/EV-DO турбокод PCCC (8 состояний, 1/2…1/5) и перемежитель с таблицей", "турбо PCCC",
    {"составляющий": "RSC: d(D) = 1+D²+D³, n0(D) = 1+D+D³, n1(D) = 1+D+D²+D³ (как UMTS/LTE по d, n0; добавлен n1)",
     "скорости": "1/2, 1/3, 1/4, 1/5 — выкалывание X/Y0/Y1/X'/Y'0/Y'1 по табл. 2.1.3.1.5.2.2-1; хвост 6 периодов (по 3 на кодер)",
     "перемежитель": "счётчик n+5 бит: старшие n бит +1, умножение на LUT[5 младших][n] mod 2^n, 5 младших — обращение бит → адрес; адреса ≥ N отбрасываются",
     "таблица": "tablicy/cdma2000_turbo_perem.json (32 × 8)", "N_turbo": "186…20730 (23 размера табл. 2.1.3.1.5.2.3-1, 1x), 250…4090 и далее (EV-DO)"},
    "cdma2000 1x Supplemental Channel (RC3–RC9), 1xEV-DO (все пакеты трафика и управления), также C.S0024 Rev A/B", [T.ist(s_tp, "2.1.3.1.5.2.1"), T.ist(s_lut, "Table 2.1.3.1.5.2.3-2"), E.ist(s_tpe, "C.S0024-A 12.2.1.3.4.2"), ist_kod(turbo, "TURBO_INTERLEAVER_LUT")],
    "многочлены = комментарий и код 1xbts; LUT столбцы n = 3…9 = 1xbts; EV-DO LUT = 1x; перестановка биективна для всех 23 размеров", "есть общий PCCC (turbo.py: систематическая ветвь, RSC, признаки перемежителя); перемежителя cdma2000 нет — средняя (таблица готова)")
rec("cdma2000 CRC качества кадра (FQI) 16/12/10/8/6 бит и FCS EV-DO 16/24", "CRC-подобный",
    {"многочлены": {k: D_zapis(v[0]).replace("D", "x") for k, v in CRC.items()}, "начальное": "все единицы (2.1.3.1.4.1); исключение — внутренний FQI 5-мс кадра прямого F-PDCCH: нули (3.1.3.1.4.1)", "выход": "без инверсии, старший первым",
     "RevEng": imena, "EV-DO FCS": "16: x16+x12+x5+1; 24: x24+x23+x6+x5+x+1 (C.S0024-A 13.1.4)"},
    "cdma2000 кадры трафика/управления всех RC, EV-DO физические пакеты", [T.ist(v[1], f"2.1.3.1.4.1 ({k})") for k, v in CRC.items()] + [T.ist(s_init, "начальное состояние"), T.ist(s_init0, "исключение: F-PDCCH 5 мс — нули"), E.ist(s_fcs, "FCS 24")],
    "все 6 многочленов и начальное состояние = CRC-*/CDMA2000 RevEng в crc_katalog.py проекта; FCS-16 EV-DO = 1xbts hrpd/crc.rs (0x1021)",
    "ЕСТЬ в проекте: crc_katalog.py (CRC-16/12/10/8/6-A/6-B CDMA2000); CRC-24 EV-DO — добавить")
rec("cdma2000 перемежитель с обращением бит (Ai = 2^m·(i mod J) + BRO_m(⌊i/J⌋))", "перемежитель (блочный, обращение бит)",
    {"формула": "A_i = 2^m (i mod J) + BRO_m(⌊i/J⌋), BRO — обращение m-битового числа", "параметры": "m, J из табл. 2.1.3.1.8-1 (N = 2^m·J)",
     "RC1/RC2": "IS-95: блочные 32×18 / 24×16 и т.п. по табл. (прямой) — те же формулы для N = 384/768", "подблочный": "EV-DO/1x турбо: подблочный перемежитель T_k = 2^m (k mod J) + BRO_m(⌊k/J⌋) после разделения потоков"},
    "cdma2000 все каналы RC3+; EV-DO", [T.ist(s_bro, "2.1.3.1.8")], "формула = 1xbts block_interleaver.rs (BitReversalInterleaver)", "нет; простая (формула)")
rec("cdma2000/IS-95 длинный код (x^42…) и короткие ПСП I/Q (x^15…)", "скремблер (ПСП m-последовательности)",
    {"длинный код": "p(x) = " + " + ".join(("1" if s == 0 else f"x{s}") for s in sorted(st_lc, reverse=True)) + ", период 2^42−1, маска 42 бита (ESN/IMSI, public/private)",
     "PI(x)": "x15+x13+x9+x8+x7+x5+1", "PQ(x)": "x15+x12+x11+x10+x6+x5+x4+x3+1", "период": "2^15 (вставлен лишний 0 после 14 нулей)",
     "кадр": "20 мс; скорость 1.2288 Мчип/с (SR1)"},
    "IS-95/cdma2000: скремблирование и расширение; для анализа — снятие скремблирования длинным кодом", [T.ist(s_lc, "2.1.3.1.16 long code"), T.ist(s_pi, "PI/PQ")],
    "длинный код = 1xbts long_code.rs; отводы PN_I_TAPS/PN_Q_TAPS 1xbts = обратная запись PI/PQ", "есть общий аддитивный скремблер (skrembler.py) — многочлены задаются; разметки нет")
rec("1xEV-DO (IS-856) скремблер h(D) = D^17 + D^14 + 1", "скремблер",
    {"многочлен": "D17+D14+1", "начальное состояние": "по MACIndex и скорости (DRC) — 17 бит: 7 единиц + MACIndex + код скорости (прямой канал)"},
    "EV-DO прямой и обратный трафик", [E.ist(s_scr, "12.2.1.3.5 Scrambling")], "многочлен = 1xbts hrpd/scrambler.rs", "есть аддитивный скремблер (многочлен задаётся)", sem="1xEV-DO (3GPP2 C.S0024)")

if __name__ == "__main__":
    print(len(ZAPISI), "записей", DF, {k: v[0] for k, v in CRC.items()})
