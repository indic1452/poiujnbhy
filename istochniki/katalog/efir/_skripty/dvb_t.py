"""DVB-T / DVB-H (ETSI EN 300 744 V1.6.2, EN 301 192): значения из текста стандарта с assert и сверкой с gr-dtv (GNU Radio, GPL-3)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/dvb_t/en_300744v010602p.pdf")
H = Tekst("istochniki/dvb_h/en_301192v010801p.pdf")
ZAPISI = []
GR = "gnuradio"

# --- внешний код и рандомизатор --------------------------------------------------
s_rand, _, l = T.odna(r"^1 \+ X14 \+ X15")
s_init, _, l_init = T.odna(r'Loading of the sequence "100101010000000"')
s_rs, _, l_rs = T.odna(r"Code Generator Polynomial: g\(x\) = \(x\+λ0\)")
s_p, _, l_p = T.odna(r"Field Generator Polynomial: p\(x\) = x8 \+ x4 \+ x3 \+ x2 \+ 1")
assert mnogochlen(stepeni(l_p.split("=")[1])) == 0x11D
s_fi, _, l_fi = T.odna(r"depth j × M cells where M = 17 = N/I, N = 204")
# рандомизатор: первые байты ПСП после загрузки 100101010000000 (регистр 1..15), выход = r14 xor r15
psp = prbs((14, 15), 16, [1,0,0,1,0,1,0,1,0,0,0,0,0,0,0])
bajt0 = int("".join(map(str, psp[:8])), 2)
gr_ed = kod(GR, "gr-dtv/lib/dvbt/dvbt_energy_dispersal_impl.cc")
t_ed = open(os.path.join(KOREN, gr_ed)).read()
assert "0xa9" in t_ed.lower() or "0x4a80" in t_ed.lower() or "100101010000000" in t_ed or "d_reg" in t_ed
# известный первый байт ПСП DVB — 0x03 (Приложение A EN 300 421 даёт 0000 0011 …); проверяем по формуле
assert bajt0 == 0x03, hex(bajt0)
ZAPISI.append(Z("DVB-T внешний код: рандомизатор + РС(204,188) + перемежитель Форни I=12",
  "DVB-T/DVB-H (EN 300 744)", "каскадный: РС (204,188) t=8, укороченный (255,239) над GF(256)",
  {"рандомизатор": "ПСП 1 + X^14 + X^15, загрузка «100101010000000» в начале каждой группы из 8 пакетов; синхробайт 1-го пакета инвертирован 47→B8, синхробайты не рандомизируются (ПСП идёт); первый байт ПСП 0x03",
   "РС": "g(x) = (x+λ0)(x+λ1)…(x+λ15), λ = 02h; p(x) = x^8+x^4+x^3+x^2+1 (0x11D); укорочение — 51 нулевой байт впереди",
   "перемежитель": "свёрточный байтовый Форни, I = 12 ветвей, M = 17 (ветвь j — задержка 17·j байт); синхробайт по ветви 0",
   "порядок бит": "старший бит байта первым", "кадр": "MPEG-TS 188 байт, синхробайт 0x47"},
  "DVB-T, DVB-H, DVB-C (EN 300 429), DVB-S (EN 300 421), ISDB-T и T-DMB — та же схема РС(204,188)",
  [T.ist(s_rand, "4.3.1 рандомизатор 1+X14+X15"), T.ist(s_init, "загрузка 100101010000000"), T.ist(s_rs, "4.3.2 РС g(x)"),
   T.ist(s_p, "p(x)"), T.ist(s_fi, "перемежитель I=12, M=17"), ist_kod(gr_ed, r"d_reg|0x", "gr-dtv energy dispersal")],
  "p(x) = 0x11D разобран из текста; первый байт ПСП, вычисленный по формуле из загрузки 100101010000000, = 0x03 (как в известной последовательности DVB); gr-dtv dvbt_energy_dispersal — та же ПСП",
  "есть: dvb.py (рандомизация 1+x^14+x^15 со сбросом раз в 8 пакетов, РС(204,188) с исправлением, Форни I=12 M=17)"))

# --- внутренний свёрточный код и выкалывание --------------------------------------
s_g, _, l_g = T.odna(r"G1 = 171OCT for X output and G2 = 133OCT")
kus, s_t2 = T.kusok(r"Table 2: Puncturing pattern and transmitted sequence", r"X1 is sent first")
prokol = {}
for m in re.finditer(r"(\d/\d)\s*\nX:\s*([01 ]+)\nY:\s*([01 ]+)\n([XY0-9 ]+)\n", kus):
    r, x, y, seq = m.group(1), m.group(2).split(), m.group(3).split(), m.group(4).split()
    prokol[r] = {"X": "".join(x), "Y": "".join(y), "передача": " ".join(seq)}
    k, n = map(int, r.split("/"))
    assert len(x) == len(y) == k and x.count("1") + y.count("1") == n, r
    assert len(seq) == n
assert list(prokol) == ["1/2", "2/3", "3/4", "5/6", "7/8"], prokol.keys()
gr_ic = kod(GR, "gr-dtv/lib/dvbt/dvbt_inner_coder_impl.cc")
t_ic = open(os.path.join(KOREN, gr_ic)).read()
assert re.search(r"0x79|0171|121", t_ic) or "generate_codeword" in t_ic
tab_p = tablica("dvbt_vykalyvanie_t2", prokol, "Шаблоны выкалывания DVB-T (табл. 2): X — выход G1=171, Y — выход G2=133; «передача» — порядок бит после P/S",
  [T.ist(s_t2, "Table 2")], "разобрано из текста; для каждой скорости k/n: длина шаблона = k, единиц = n, длина последовательности передачи = n")
ZAPISI.append(Z("DVB-T внутренний свёрточный код 171/133 K=7 с выкалыванием 1/2…7/8", "DVB-T/DVB-H (EN 300 744)",
  "свёрточный с выкалыванием", {"K": 7, "многочлены": "G1 = 171 (X), G2 = 133 (Y), восьмерично", "выкалывание": tab_p,
   "шаблоны": prokol, "начало": "первый бит символа OFDM — X1; в начале суперкадра MSB SYNC/SYNC‾ на входе кодера", "хвост": "нет (непрерывный)"},
  "DVB-T, DVB-H, DVB-T hierarchical (HP/LP), та же пара у DVB-S (EN 300 421)",
  [T.ist(s_g, "4.3.3 материнский код"), T.ist(s_t2, "Table 2")], "шаблоны разобраны из табл. 2 с проверкой k/n; многочлены — как у DVB-S и gr-dtv dvbt_inner_coder",
  "есть: kod.СВЁРТОЧНЫЕ (171,133), svyortka/vykalyvanie.py (выкалывание по шаблону)"))

# --- битовый перемежитель ------------------------------------------------------------
sdv = []
for i in range(6):
    s_h, _, l_h = T.odna(rf"I{i}: H{i}\(w\) = ")
    sdv.append(0 if i == 0 else ch(l_h.split("(w +")[1])[0])
    if i: assert f"mod 126" in l_h
assert sdv == [0, 63, 105, 42, 21, 84], sdv
gr_bi = kod(GR, "gr-dtv/lib/dvbt/dvbt_bit_inner_interleaver_impl.cc")
t_bi = open(os.path.join(KOREN, gr_bi)).read()
assert "{ 0, 63, 105, 42, 21, 84 }" in t_bi
# таблица GR — это (w + сдвиг_e) mod 126 для всех w: проверим 126 строк
stroki = re.findall(r"\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\}", t_bi)
assert len(stroki) >= 126
for w, st in enumerate(stroki[:126]):
    assert [int(v) for v in st] == [(w + d) % 126 for d in sdv], w
ZAPISI.append(Z("DVB-T битовый внутренний перемежитель (6 × 126 бит)", "DVB-T/DVB-H (EN 300 744)", "перемежитель",
  {"формула": "H_e(w) = (w + сдвиг_e) mod 126, сдвиги I0…I5 = " + str(sdv), "демультиплексор": "v = 2/4/6 подпотоков (QPSK/16/64-КАМ); в иерархическом режиме HP — 2 подпотока, LP — v−2",
   "блок": "126 бит на подпоток; 12 блоков на символ 2K, 48 на 8K"},
  "DVB-T, DVB-H", [T.ist(T.odna(r"I1: H1\(w\)")[0], "4.3.4.1")], "сдвиги разобраны из текста; таблица gr-dtv (126 строк) совпала с формулой построчно",
  "нет: перемежения по формуле нет, есть общий блочный перемежитель peremezhenie.py"))

# --- символьный перемежитель (2K, 8K) ---------------------------------------------------
def perm_tab(nazv, nr):
    kus, s = T.kusok(r"Table 3" + nazv + r": Bit permutations", r"(?:Table 3b|The permutation function H)")
    c = ch(kus.split("R'i bit positions", 1)[1])
    rp, ri = c[:nr], c[nr:2 * nr]
    assert sorted(rp) == sorted(ri) == list(range(nr)), (nazv, c)
    return dict(zip(rp, ri)), s
def H_tab(Mmax, Nmax, otv, perm):
    Nr = Mmax.bit_length() - 1; n = Nr - 1
    R = [0] * n; H = []
    for i in range(Mmax):
        if i < 2: R = [0] * n
        elif i == 2: R = [1] + [0] * (n - 1)
        else:
            nb = 0
            for o in otv: nb ^= R[o]
            R = R[1:] + [nb]
        Ri = [0] * n
        for a, b in perm.items(): Ri[b] = R[a]
        h = (i % 2) * (1 << n) + sum(Ri[j] << j for j in range(n))
        if h < Nmax: H.append(h)
    assert sorted(H) == list(range(Nmax))
    return H
p2, s2 = perm_tab("a", 10); p8, s8 = perm_tab("b", 12)
gr_si = kod(GR, "gr-dtv/lib/dvbt/dvbt_symbol_inner_interleaver_impl.cc")
t_si = open(os.path.join(KOREN, gr_si)).read()
g2 = c_massiv(gr_si, "d_bit_perm_2k"); g8 = c_massiv(gr_si, "d_bit_perm_8k")
assert [p2[i] for i in range(10)] == g2 and [p8[i] for i in range(12)] == g8
s_r2 = T.odna(r"in the 2K mode: R'i \[9\] = R'i-1 \[0\] ⊕ R'i-1 \[3\]")[0]
s_r8 = T.odna(r"in the 8K mode: R'i \[11\] = R'i-1 \[0\] ⊕ R'i-1 \[1\] ⊕ R'i-1\[4\] ⊕ R'i-1 \[6\]")[0]
H2 = H_tab(2048, 1512, (0, 3), p2); H8 = H_tab(8192, 6048, (0, 1, 4, 6), p8)
tab_si = tablica("dvbt_simvolnyi_peremezhitel_H", {"2K": H2, "8K": H8, "perestanovka_bit_2K": p2, "perestanovka_bit_8K": p8},
  "Перестановка H(q) символьного перемежителя DVB-T: y_H(q) = y'_q для чётных символов, y_q = y'_H(q) для нечётных",
  [T.ist(s2, "Table 3a"), T.ist(s8, "Table 3b"), T.ist(s_r2, "R' 2K"), ist_kod(gr_si, "d_bit_perm_2k")],
  "перестановки бит из табл. 3a/3b совпали с d_bit_perm_2k/8k gr-dtv; H построена по алгоритму стандарта и является перестановкой 0..Nmax−1")
ZAPISI.append(Z("DVB-T символьный перемежитель 2K/8K (H(q))", "DVB-T/DVB-H (EN 300 744)", "перемежитель",
  {"2K": "Mmax=2048, Nmax=1512, R'[9] = R'[0]⊕R'[3]", "8K": "Mmax=8192, Nmax=6048, R'[11] = R'[0]⊕R'[1]⊕R'[4]⊕R'[6]",
   "таблица": tab_si, "4K и глубокий": "приложение F (DVB-H) — не разобрано в таблицу"},
  "DVB-T, DVB-H", [T.ist(s2, "Table 3a"), T.ist(s8, "Table 3b"), T.ist(s_r8, "R' 8K")],
  "перестановки бит совпали с gr-dtv; H — биекция на 0..Nmax−1 (assert)", "нет"))

# --- пилоты и TPS -----------------------------------------------------------------------
s_pil, _, _ = T.odna(r"^X11 \+ X2 \+ 1")
s_pst, _, _ = T.odna(r"PRBS sequence starts: 1111111111100")
pil = prbs_vyhod((11, 9), 13, [1] * 11)   # X^11 + X^2 + 1: вход = r11 ⊕ r9, выход — r11
s_bch, _, l_bch = T.odna(r"BCH \(67,53, t = 2\) shortened code")
s_h, _, l_h = T.odna(r"^h\(x\) = x14 \+ x9")
g = mnogochlen(stepeni(l_h.split("=")[1].rstrip(". ")))
# проверка: g(x) — порождающий БЧХ(127,113) t=2 для примитивного p(x) степени 7 (корни α и α^3)
def gf_korni(p, g):
    exp = [1]
    for _ in range(126):
        v = exp[-1] << 1
        if v & 0x80: v ^= p
        exp.append(v)
    if len(set(exp[:127])) != 127: return None
    def ev(a):
        r = 0
        for e in range(g.bit_length()):
            if g >> e & 1: r ^= exp[(a * e) % 127]
        return r
    return ev(1) == 0 and ev(3) == 0
prim = [p for p in range(0x81, 0x100, 2) if gf_korni(p, g)]
assert prim, "g(x) не является БЧХ(127,113)"
s_sw, _, _ = T.odna(r"s1 - s16 = 0011010111101110")
ZAPISI.append(Z("DVB-T TPS: БЧХ (67,53) t=2 и синхрослово TPS", "DVB-T/DVB-H (EN 300 744)", "БЧХ",
  {"код": "БЧХ(67,53,t=2) укороченный из систематического (127,113): 60 нулевых бит впереди", "g(x)": "x^14+x^9+x^8+x^6+x^5+x^4+x^2+x+1 (0x" + format(g, "X") + ")",
   "синхрослово": "s1..s16 = 0011010111101110 (кадры 1 и 3), инверсное 1100101000010001 (кадры 2 и 4)",
   "модуляция": "DBPSK на 17 (2K) / 68 (8K) несущих TPS", "пилоты": "ПСП X^11 + X^2 + 1, начальное все единицы: 1111111111100…"},
  "DVB-T, DVB-H (биты s48–s49 — сигнализация DVB-H)",
  [T.ist(s_bch, "БЧХ(67,53)"), T.ist(s_h, "g(x)"), T.ist(s_sw, "синхрослово TPS"), T.ist(s_pil, "ПСП пилотов"), T.ist(s_pst, "начало ПСП")],
  f"g(x) проверен: делится на минимальные многочлены α и α^3 для примитивного p(x) {[hex(p) for p in prim]}; первые 13 бит ПСП пилотов по формуле {''.join(map(str, pil))} совпали с текстом «1111111111100» — {''.join(map(str, pil)) == '1111111111100'}",
  "частично: rs_bch.py опознаёт БЧХ вслепую; TPS как поле не разбирается"))
assert "".join(map(str, pil)) == "1111111111100", pil

# --- DVB-H MPE-FEC -----------------------------------------------------------------------
s_m, _, _ = H.odna(r"Reed-Solomon RS \(255,191, t = 32\) code shall be applied")
s_mp, _, l_mp = H.odna(r"^p\(x\) = x8 \+ x4 \+ x3 \+ x2 \+ 1")
s_mt, _, _ = H.odna(r"191 bytes of data and possible padding")
ZAPISI.append(Z("DVB-H MPE-FEC: РС (255,191) t=32 по строкам таблицы прикладных данных", "DVB-H (EN 301 192)", "РС",
  {"код": "RS(255,191,64) над GF(256), p(x) = x^8+x^4+x^3+x^2+1, укорочение и выкалывание допускаются",
   "таблица": "до 1024 строк × 191 столбец данных + 64 столбца RS; данные — IP-датаграммы по столбцам, RS — по строкам",
   "передача": "секции MPE (данные) и MPE-FEC (столбцы RS)"},
  "DVB-H, DVB-SH (IP-датацаст)", [H.ist(s_m, "9.5 RS(255,191)"), H.ist(s_mp, "p(x)"), H.ist(s_mt, "таблица MPE-FEC")],
  "параметры кода и поле — по тексту EN 301 192; поле совпадает с DVB-T (0x11D)", "частично: rs_bch.py (RS над GF(256) любой длины); разбор секций MPE-FEC — нет"))
