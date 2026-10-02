"""Дополнения по итогам проверки полноты области «mobilnaya» (второй проход по стандартам).

Каждая запись: значения сняты программно с текста первоисточника (номер страницы — первая страница с вхождением, страницы
оглавлений и перечней пропускаются) или с открытого кода с лицензией; затем проверены вычислением (assert):
тестовые векторы стандарта, второй независимый источник, структурные свойства (биективность, примитивность,
делимость, минимальное расстояние)."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})

def oglavlenie(t):
    return len(re.findall(r"\.{8,}", t)) >= 5 or len(re.findall(r"_{8,}", t)) >= 5

class TekstS(Tekst):
    """Tekst, у которого поиск пропускает страницы оглавлений/перечней (у сборщика первая находка попадала в оглавление)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.str_txt = {}
        for i, l in enumerate(self.stroki):
            self.str_txt.setdefault(self.str_[i], []).append(l)
        self.ogl = {s for s, ls in self.str_txt.items() if oglavlenie("\n".join(ls))}
    def odna(self, pat, flags=re.I):
        """Первая страница (не оглавление), в нормализованном тексте которой есть pat; возвращает (страница, None, фрагмент)."""
        for s_ in sorted(self.str_txt):
            if s_ in self.ogl or s_ == 0: continue
            m = re.search(pat, self.norm(s_), flags)
            if m: return (s_, None, m.group(0))
        raise AssertionError(f"не найдено вне оглавления в {self.pdf}: {pat}")
    def str_ves(self, s):
        return "\n".join(self.str_txt.get(s, []))
    def norm(self, s):
        return re.sub(r"\s+", " ", self.str_ves(s))

def pmod(a, g):
    dg = g.bit_length() - 1
    while a and a.bit_length() - 1 >= dg:
        a ^= g << (a.bit_length() - 1 - dg)
    return a
def pmul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r
def st(*s): return sum(1 << i for i in s)
def dmin(G):
    rows = [int("".join(map(str, r)), 2) for r in G]; best = len(G[0])
    for m in range(1, 1 << len(G)):
        w = 0
        for i in range(len(G)):
            if m >> i & 1: w ^= rows[i]
        best = min(best, bin(w).count("1"))
    return best
def lfsr_period(otvody, n, init=1):
    """Период аддитивной ПСП a(i+n) = Σ a(i+t), t ∈ otvody (0 ≤ t < n)."""
    s = init; seen = {}
    for k in range(1 << (n + 1)):
        if s in seen: return k - seen[s]
        seen[s] = k
        b = 0
        for t in otvody: b ^= (s >> t) & 1
        s = (s >> 1) | (b << (n - 1))
    return None

# =========================================================================================================== UMTS 25.212 HS/E
U = TekstS("istochniki/umts/ts_125212v190000p.pdf")
s_hs = U.odna(r"4\.6\.5 Channel coding for HS-SCCH")[0]
t = U.norm(s_hs) + " " + U.norm(s_hs - 1)
assert "z1,1, z1,2, …, z1,48" in t and "z2,1, z2,2, …, z2,111" in t
s_rm = U.odna(r"4\.6\.6 Rate matching for HS-SCCH")[0]
t = U.norm(s_rm)
m = re.search(r"From the input sequence z1,1, z1,2, …, z1,48 the bits (.*?) are punctured to obtain the output sequence r1,1", t)
P1 = [int(x) for x in re.findall(r"z1,(\d+)", m.group(1))]
m = re.search(r"From the input sequence z2,1, z2,2, …, z2,111 the bits (.*?) are punctured to obtain the output sequence r2,1", t)
P2 = [int(x) for x in re.findall(r"z2,(\d+)", m.group(1))]
assert P1 == [1, 2, 4, 8, 42, 45, 47, 48] and len(P2) == 31 and 48 - len(P1) == 40 and 111 - len(P2) == 80, (P1, len(P2))
assert (8 + 8) * 3 == 48 and (13 + 16 + 8) * 3 == 111     # K=9 1/3 с полным хвостом: (8+8)·3, (29+8)·3
s_ue = U.odna(r"4\.6\.7 UE specific masking for HS-SCCH")[0]
assert "b1, b2, b4, b8, b42, b45, b47, b48, are punctured" in U.norm(s_ue) and (16 + 8) * 2 == 48
s_crc = U.odna(r"4\.6\.4 CRC attachment for HS-SCCH")[0]
assert "masked with the UE Identity xue,1, xue,2, …, xue,16" in U.norm(s_crc)
tab = tablica("umts_hsscch_vykalyvanie", {"chast1_vykoloty_z1": P1, "chast2_vykoloty_z2": P2, "maska_UE_vykoloty_b": P1},
              "HS-SCCH тип 1 (TS 25.212 4.6.6–4.6.7): номера выкалываемых бит (с 1) части 1 (48→40), части 2 (111→80) и маски UE (48→40)",
              [U.ist(s_rm, "4.6.6"), U.ist(s_ue, "4.6.7")], "разобрано из текста; длины 48−8 = 40 и 111−31 = 80 — assert")
rec("UMTS HS-SCCH тип 1: свёрточный K=9 1/3 + выкалывание 48→40 / 111→80 + маска UE (свёрточный 1/2 от H-RNTI) + CRC-16 ⊕ H-RNTI",
    "UMTS/WCDMA", "каскадный: CRC-16 (маска RNTI) + свёрточный K=9 1/3 + выкалывание",
    {"часть 1": "xccs (7) + xms (1) = 8 бит → K=9 1/3 (557, 663, 711₈, 4.2.3.1) → 48 → выкалываются z1,1, z1,2, z1,4, z1,8, z1,42, z1,45, z1,47, z1,48 → 40 бит → ⊕ маска UE",
     "маска UE": "16 бит H-RNTI (xue,1 — старший) → K=9 1/2 (561, 753₈) → 48 → выколоты b1, b2, b4, b8, b42, b45, b47, b48 → c1…c40",
     "часть 2": "xtbs (6) + xhap (3) + xrv (3) + xnd (1) = 13 бит + CRC-16 (gCRC16 25.212, по 21 биту частей 1 и 2) ⊕ H-RNTI = 29 → K=9 1/3 → 111 → выколот 31 бит → 80",
     "выкалывание": tab, "кадр": "подкадр 2 мс: слот 1 — 40 бит части 1, слоты 2–3 — 80 бит части 2 (SF 128, QPSK)",
     "RV/модуляция": "табл. 12 (16QAM/64QAM) и 13 (QPSK) — Xrv из (s, r, b)"},
    "UMTS HSDPA (Rel-5+): канал управления HS-DSCH; типы 2/3/4 (HS-SCCH-less, MIMO) — 4.6A–4.6D",
    [U.ist(s_hs, "4.6.5"), U.ist(s_rm, "4.6.6"), U.ist(s_ue, "4.6.7"), U.ist(s_crc, "4.6.4")],
    "все позиции выкалывания разобраны из текста программно; длины согласованы: (8+8)·3 = 48 → 40, (29+8)·3 = 111 → 80, маска (16+8)·2 = 48 → 40 — assert",
    "частично: свёрточный K=9 1/2, 1/3 (kod.py/svyortka, многочлены 561/753, 557/663/711) и CRC-16 есть; выкалывание по таблице и маска RNTI — добавить (просто)")

s_ack = U.odna(r"4\.7\.2\.1 Channel coding for HS-DPCCH HARQ-ACK")[0]
t = U.norm(s_ack)
assert "ACK 1 1 1 1 1 1 1 1 1 1 NACK 0 0 0 0 0 0 0 0 0 0 PRE 0 0 1 0 0 1 0 0 1 0 POST 0 1 0 0 1 0 0 1 0 0" in t
s_20 = U.odna(r"Table 15A: Basis sequences for \(20,5\) code")[0]
t = U.norm(s_20)
nums = [int(x) for x in re.search(r"Mi,4 (.*?) The CQI values", t).group(1).split()]
M205 = [nums[6 * i + 1:6 * i + 6] for i in range(20)]
assert [nums[6 * i] for i in range(20)] == list(range(20)), nums[:20]
d205 = dmin([[M205[i][n] for i in range(20)] for n in range(5)])
assert d205 == 8, d205
tab205 = tablica("umts_hsdpcch_20_5", {"M_20x5": M205, "HARQ_ACK_10bit": {"ACK": [1] * 10, "NACK": [0] * 10, "PRE": [0, 0, 1, 0, 0, 1, 0, 0, 1, 0], "POST": [0, 1, 0, 0, 1, 0, 0, 1, 0, 0]}},
                 "HS-DPCCH (TS 25.212 4.7.2): базис кода CQI (20,5) табл. 15A и кодовые слова HARQ-ACK табл. 15", [U.ist(s_20, "Table 15A"), U.ist(s_ack, "Table 15")],
                 "20 строк разобраны с проверкой номеров; d_min = 8 перебором")
rec("UMTS HS-DPCCH: HARQ-ACK (повторение 10 бит, PRE/POST) и CQI — блочный код (20,5), d = 8",
    "UMTS/WCDMA", "блочный (подкод РМ 1-го порядка) + повторение",
    {"HARQ-ACK": "ACK = 1111111111, NACK = 0000000000, PRE = 0010010010, POST = 0100100100 (табл. 15)", "CQI": "a0…a4 (a0 — младший), b_i = Σ a_n·M_i,n mod 2, i = 0…19; базис табл. 15A → " + tab205,
     "d_min": d205, "CQI 0…30": "→ (1 0 0 0 0)…(1 1 1 1 1); 00000 не используется", "MIMO/многосотовые": "(20,7) PCI/CQI, (20,10) составной CQI — 4.7.3*"},
    "UMTS HSDPA, восходящий канал обратной связи (подкадр 2 мс: слот ACK + 2 слота CQI)",
    [U.ist(s_ack, "4.7.2.1, табл. 15"), U.ist(s_20, "табл. 15A")],
    "табл. 15A разобрана из текста с проверкой номеров строк; d_min (20,5) = 8 вычислен перебором 31 слова; строки табл. 15 найдены дословно — assert",
    "нет; простая (перебор 32 слов / быстрое преобразование Уолша — kod.уолш в проекте)")

s_ed = U.odna(r"4\.9\.4 Channel coding for E-DPCCH")[0]
t = U.norm(s_ed)
assert "sub-code of the second order Reed-Muller code" in t and "The basis sequences are as described in 4.3.3 for i=0, 1, ..., 29" in t
s_mux = U.odna(r"4\.9\.3 Multiplexing of E-DPCCH information")[0]
assert "xk = xh,1 k=1 xk = xrsn,4-k k=2,3 xk = xtfci,11-k k=4,5,…,10" in U.norm(s_mux)
TF = json.load(open(os.path.join(KOREN, "tablicy", "umts_25212.json")))["данные"]["TFCI_32x10_t8"]
G3010 = [[TF[i][n] for i in range(30)] for n in range(10)]
d3010 = dmin(G3010)
s_ag = U.odna(r"4\.10\.4 Rate matching for E-AGCH")[0]
t = U.norm(s_ag)
m = re.search(r"From the input sequence z1, z2, …, z90 the bits (.*?) are punctured to obtain the output sequence r1", t)
PAG = [int(x) for x in re.findall(r"z(\d+)", m.group(1))]
assert len(PAG) == 30 and 90 - 30 == 60 and (22 + 8) * 3 == 90, PAG
s_agc = U.odna(r"4\.10\.2 CRC attachment for E-AGCH")[0]
assert "yi=(ci-6 + xid,i-6) mod 2 i= 7, ..., 22" in U.norm(s_agc)
tabag = tablica("umts_eagch_vykalyvanie", {"vykoloty_z": PAG}, "E-AGCH (TS 25.212 4.10.4): номера выкалываемых бит 90→60", [U.ist(s_ag, "4.10.4")], "30 позиций, 90−30 = 60 — assert")
rec("UMTS E-DPCCH — (30,10) подкод РМ 2-го порядка (= TFCI 32,10 без двух последних строк), d = " + str(d3010) + "; E-AGCH — CRC-16 ⊕ E-RNTI + K=9 1/3 + выкалывание 90→60",
    "UMTS/WCDMA", "РМ (подкод) / каскадный: CRC-16 (маска) + свёрточный K=9 1/3",
    {"E-DPCCH": "x1 = happy, x2..x3 = RSN (старший — x3), x4..x10 = E-TFCI (старший — x10); z_i = Σ x_(n+1)·M_i,n mod 2, i = 0…29 — базис 4.3.3 (табл. 8 25.212)",
     "d_min (30,10)": d3010, "S-E-DPCCH": "так же (MIMO вверх)",
     "E-AGCH": "AG value 5 бит + scope 1 = 6 бит + CRC-16 ⊕ E-RNTI (y7…y22) = 22 → K=9 1/3 → 90 → выколото 30 → 60 → " + tabag, "E-ROCH": "та же цепочка, что E-AGCH"},
    "UMTS HSUPA (E-DCH, Rel-6+): восходящий канал управления и нисходящий абсолютный грант",
    [U.ist(s_mux, "4.9.3"), U.ist(s_ed, "4.9.4"), U.ist(s_agc, "4.10.2"), U.ist(s_ag, "4.10.4")],
    f"базис — строки 0…29 табл. 8 (сверена построчно с текстом в записи TFCI); d_min (30,10) = {d3010} перебором 1023 слов; 30 позиций выкалывания E-AGCH разобраны программно, длины (22+8)·3 = 90 → 60 — assert",
    "частично: базис TFCI (32,10) и свёрточный K=9 есть; разметка E-DPCCH/E-AGCH — добавить (просто)")

# ================================================================================================= UMTS 25.213 скремблирование
S = TekstS("istochniki/umts/ts_125213v190000p.pdf")
s_ul = S.odna(r"The x sequence is constructed using the primitive \(over GF\(2\)\) polynomial X25\+X3\+1")[0]
t = S.norm(s_ul)
assert "The y sequence is constructed using the polynomial X25+X3+X2+X+1" in t and "16777232 chip shifted" in t
assert "xn(i+25) =xn(i+3) + xn(i) modulo 2" in t and "y(i+25) = y(i+3)+y(i+2) +y(i+1) +y(i) modulo 2" in t
s_dl = S.odna(r"polynomial 1\+X7\+X18")[0]
t = S.norm(s_dl)
assert "The y sequence is constructed using the polynomial 1+X5+X7+ X10+X18" in t and "x(i+18) =x(i+7) + x(i) modulo 2" in t and "y(i+18) = y(i+10)+y(i+7)+y(i+5)+y(i) modulo 2" in t
assert "Sdl,n(i) = Zn(i) + j Zn((i+131072) modulo (218-1)), i=0,1,…,38399" in t
s_sh = S.odna(r"g0\(x\)= x8\+3x5\+x3\+3x2\+2x\+3")[0]
t = S.norm(s_sh)
assert "g1(x)= x8+x7+x5+x+1" in t and "g2(x)= x8+x7+x5+x4+1" in t
# примитивность (период m-последовательности) — вычислено
assert lfsr_period((0, 7), 18) == (1 << 18) - 1 and lfsr_period((0, 5, 7, 10), 18, init=(1 << 18) - 1) == (1 << 18) - 1
assert lfsr_period((0, 3), 25) == (1 << 25) - 1
rec("UMTS скремблирующие коды: нисходящий Голд 2^18−1 (1+X7+X18, 1+X5+X7+X10+X18), восходящий длинный Голд 2^25−1 (X25+X3+1, X25+X3+X2+X+1) и короткий S(2) 256",
    "UMTS/WCDMA", "скремблер (ПСП Голда)",
    {"нисходящий": "x: x(i+18) = x(i+7)+x(i), x(0)=1, остальные 0; y: y(i+18) = y(i+10)+y(i+7)+y(i+5)+y(i), y = все 1; z_n(i) = x((i+n) mod (2^18−1)) ⊕ y(i); S_dl,n(i) = Z_n(i) + j·Z_n((i+131072) mod (2^18−1)), 38400 элементов на кадр 10 мс; n = 16·i (первичные 512) + k",
     "восходящий длинный": "x_n: x(i+25) = x(i+3)+x(i), начальное n0…n23, x(24)=1; y: y(i+25) = y(i+3)+y(i+2)+y(i+1)+y(i), все 1; c_long,2 — сдвиг на 16 777 232 элемента; C = c1·(1 + j·(−1)^i·c2(2⌊i/2⌋))",
     "восходящий короткий": "S(2) длины 255 (+1): a(i) по g0(x) = x8+3x5+x3+3x2+2x+3 над Z4, b(i) по g1 = x8+x7+x5+x+1, d(i) по g2 = x8+x7+x5+x4+1; z = a + 2b + 2d mod 4 → табл. 2",
     "модуляция": "QPSK, 3,84 Мэлем/с"},
    "UMTS FDD (WCDMA/HSPA): все физические каналы",
    [S.ist(s_ul, "4.3.2.2"), S.ist(s_sh, "4.3.2.3"), S.ist(s_dl, "5.2.2")],
    "многочлены и рекуррентности сняты с текста программно; периоды x (1+X7+X18), y (1+X5+X7+X10+X18) = 2^18−1 и x (X25+X3+1) = 2^25−1 вычислены — m-последовательности (assert)",
    "есть общий аддитивный скремблер (skrembler.py) — Голд-последовательности задаются двумя регистрами; генератор кода по номеру — добавить (просто)")

s_psc = S.odna(r"a = <x1, x2, x3, …, x16> = <1, 1, 1, 1, 1, 1, -1, -1, 1, -1, 1, -1, 1, -1, -1, 1>")[0]
t = S.norm(s_psc)
assert "Cpsc = (1 + j) × <a, a, a, -a, -a, a, -a, -a, a, a, a, -a, a, -a, a, a>" in t
assert "z = <b, b, b, -b, b, b, -b, -b, b, -b, b, -b, -b, -b, -b, -b>" in t and "m = 16×(k – 1)" in t
s_t4 = S.odna(r"Group 0 1 1 2 8 9 10 15 8 10 16 2 7 15 7 16", 0) if False else None
grp = {}
for s in sorted(set(S.str_))[1:]:
    tt = S.norm(s)
    for g, row in re.findall(r"Group (\d+) ((?:\d+ ){14}\d+)", tt):
        grp[int(g)] = [int(x) for x in row.split()]
assert sorted(grp) == list(range(64)) and all(len(r) == 15 and all(1 <= x <= 16 for x in r) for r in grp.values()), sorted(grp)[:5]
s_t4 = S.odna(r"Group 0 ")[0] if S.naiti(r"^Group 0 $") == [] else S.naiti(r"^Group 0\s*$")[0][0]
# свойство 5.2.3.2: циклические сдвиги всех 64 последовательностей различны (запятая-свободный код)
sdvigi = {}
for g, r in grp.items():
    for k in range(15):
        key = tuple(r[k:] + r[:k])
        assert key not in sdvigi, (g, k, sdvigi.get(key))
        sdvigi[key] = (g, k)
assert len(sdvigi) == 64 * 15
tssc = tablica("umts_ssc_gruppy_25213_t4", {str(g): grp[g] for g in range(64)}, "UMTS вторичный SCH (TS 25.213 табл. 4): номера SSC (1…16) по слотам 0…14 для 64 групп скремблирующих кодов",
               [S.ist(s_t4, "Table 4")], "64×15 разобраны из текста; проверено свойство 5.2.3.2 — все 960 циклических сдвигов различны (код без запятой)")
# PSC: апериодическая автокорреляция — вычисляем пик/боковые
a = [1, 1, 1, 1, 1, 1, -1, -1, 1, -1, 1, -1, 1, -1, -1, 1]
zn = [1, 1, 1, -1, -1, 1, -1, -1, 1, 1, 1, -1, 1, -1, 1, 1]
psc = [s_ * x for s_ in zn for x in a]
assert len(psc) == 256
rec("UMTS синхрокоды: PSC — обобщённая иерархическая последовательность Голея 256, SSC — Адамар H8 × z, 64 группы × 15 слотов (код без запятой)",
    "UMTS/WCDMA", "синхрослово (синхропоследовательности)",
    {"a": "<1,1,1,1,1,1,−1,−1,1,−1,1,−1,1,−1,−1,1>", "PSC": "(1+j)·<a,a,a,−a,−a,a,−a,−a,a,a,a,−a,a,−a,a,a> (256 элементов)",
     "SSC_k": "(1+j)·h_m(i)·z(i), m = 16(k−1), k = 1…16; z = <b,b,b,−b,b,b,−b,−b,b,−b,b,−b,−b,−b,−b,−b>, b = <x1…x8, −x9…−x16>",
     "группы": "табл. 4 → " + tssc + "; группа ↔ 8 первичных скремблирующих кодов"},
    "UMTS FDD: начальный поиск соты (слот 0,667 мс, кадр 10 мс)",
    [S.ist(s_psc, "5.2.3.1"), S.ist(s_t4, "Table 4 (стр. 45–47)")],
    "таблица 64×15 разобрана с текста; свойство «код без запятой» 5.2.3.2 проверено: 960 циклических сдвигов попарно различны — assert",
    "есть поиск синхрослов (sinhro.py) — для чипового уровня нужен демодулятор; таблица готова")

# ======================================================================================================= LTE / NR 36.211/38.211
L = TekstS("istochniki/lte/ts_136211v190300p.pdf"); N = TekstS("istochniki/nr/ts_138211v190500p.pdf")
s_g = L.odna(r"7\.2 Pseudo-random sequence generation")[0]
t = L.norm(s_g)
assert "where 1600 = C N" in t and "Pseudo-random sequences are defined by a length-31 Gold sequence" in t
assert "2 mod ) ( )1 ( ) 2 ( ) 3 ( ) 31 ( 2 mod ) ( ) 3 ( ) 31 (" in t      # x2(n+31) = x2(n+3)+x2(n+2)+x2(n+1)+x2(n); x1(n+31) = x1(n+3)+x1(n)
s_gn = N.odna(r"5\.2\.1 Pseudo-random sequence generation")[0]
assert "where C = 1600" in N.norm(s_gn) or "C = 1600" in N.norm(s_gn)
seq = kod("srsRAN_4G", "lib/src/phy/common/sequence.c")
src = open(os.path.join(KOREN, seq)).read()
assert "#define SEQUENCE_NC (1600)" in src
def gold31(cinit, n):
    x1 = [1] + [0] * 30; x2 = [(cinit >> i) & 1 for i in range(31)]
    for k in range(n + 1600):
        x1.append(x1[k + 3] ^ x1[k]); x2.append(x2[k + 3] ^ x2[k + 2] ^ x2[k + 1] ^ x2[k])
    return [x1[k + 1600] ^ x2[k + 1600] for k in range(n)]
# проверка: обе m-последовательности примитивны (период 2^31−1 по многочленам x^31+x^3+1 и x^31+x^3+x^2+x+1)
def prim31(g):
    # x^(2^31−1) ≡ 1 mod g и x^((2^31−1)/p) ≠ 1 для простых делителей 2^31−1 (оно простое)
    def pw(e):
        r, b = 1, 2
        while e:
            if e & 1: r = pmod(pmul(r, b), g)
            b = pmod(pmul(b, b), g); e >>= 1
        return r
    return pw((1 << 31) - 1) == 1
assert prim31(st(31, 3, 0)) and prim31(st(31, 3, 2, 1, 0))
c0 = gold31(0, 32)
rec("LTE/NR ПСП Голда длины 31: c(n) = x1(n+1600) ⊕ x2(n+1600), x1: x^31+x^3+1 (x1(0)=1), x2: x^31+x^3+x^2+x+1 (начальное c_init) — скремблирование всех каналов",
    "LTE", "скремблер (ПСП Голда)",
    {"x1": "x1(n+31) = (x1(n+3) + x1(n)) mod 2, x1(0) = 1, x1(1…30) = 0", "x2": "x2(n+31) = (x2(n+3)+x2(n+2)+x2(n+1)+x2(n)) mod 2, c_init = Σ x2(i)·2^i",
     "Nc": 1600, "c_init (примеры)": "PDSCH/PUSCH: n_RNTI·2^14 + q·2^13 + ⌊n_s/2⌋·2^9 + N_ID^cell (LTE 6.3.1); PBCH: N_ID^cell; NR — c_init по каналу (38.211 7.3.1.1 и др.)",
     "первые 32 бита при c_init = 0": "".join(map(str, c0))},
    "LTE (36.211 7.2), NR (38.211 5.2.1), NB-IoT, LTE-M, C-V2X: скремблирование, опорные сигналы (пересекается с записью области efir «3GPP ГПСП Голда c(n) длины 31…» — там затравки NB-IoT; здесь — LTE/NR)",
    [L.ist(s_g, "7.2"), N.ist(s_gn, "5.2.1"), ist_kod(seq, r"#define SEQUENCE_NC \(1600\)", "srsRAN_4G: Nc")],
    "рекуррентности и Nc = 1600 сняты с текста 36.211 и 38.211 (одинаковы); Nc = 1600 совпал с srsRAN_4G sequence.c; примитивность x^31+x^3+1 и x^31+x^3+x^2+x+1 подтверждена вычислением x^(2^31−1) ≡ 1 (2^31−1 — простое) — assert",
    "есть аддитивный скремблер (skrembler.py); генератор Голда-31 с c_init — ~15 строк")

s_pss = L.odna(r"Table 6\.11\.1\.1-1: Root indices for the primary synchronization signal")[0]
assert "Root index u 0 25 1 29 2 34" in L.norm(s_pss)
s_sss = L.odna(r"6\.11\.2\.1 Sequence generation")[0]
t = L.norm(s_sss) + " " + L.norm(s_sss + 1)
assert "25 0 ,2 mod ) ( ) 2 ( ) 5 ( ≤ ≤ + + = + i i x i x i x" in t and "25 0 ,2 mod ) ( ) 3 ( ) 5 ( ≤ ≤ + + = + i i x i x i x" in t
assert "25 0 ,2 mod ) ( )1 ( ) 2 ( ) 4 ( ) 5 ( ≤ ≤ + + + + + + = + i i x i x i x i x i x" in t
# табл. 6.11.2.1-1 (m0, m1) против формулы 6.11.2.1
tm = L.norm(s_sss + 1) + " " + L.norm(s_sss + 2)
tm = tm[tm.find("Table 6.11.2.1-1"):]
nums = [int(x) for x in re.findall(r"\b\d+\b", tm[tm.find("1 m 0 0 1"):])]
def m01(N1):
    qq = N1 // 30; q = (N1 + qq * (qq + 1) // 2) // 30; mm = N1 + q * (q + 1) // 2
    m0 = mm % 31; return m0, (m0 + mm // 31 + 1) % 31
tab_m = {}
i = 0
while i + 2 < len(nums) and len(tab_m) < 168:
    n1, a_, b_ = nums[i:i + 3]
    if 0 <= n1 < 168 and n1 not in tab_m and (a_, b_) == m01(n1): tab_m[n1] = (a_, b_); i += 3
    else: i += 1
assert len(tab_m) == 168, len(tab_m)
def mseq5(taps, n=31):
    x = [0, 0, 0, 0, 1]
    for i in range(26): x.append(sum(x[i + t] for t in taps) % 2)
    return x
assert len(set(tuple(mseq5((0, 2))[k:] + mseq5((0, 2))[:k]) for k in range(31))) == 31
rec("LTE синхросигналы: PSS — Задов — Чу длины 63 (u = 25, 29, 34), SSS — m-последовательности длины 31 (x^5+x^2+1, x^5+x^3+1, x^5+x^4+x^2+x+1) с m0/m1 от N_ID^(1)",
    "LTE", "синхрослово (синхропоследовательности)",
    {"PSS": "d_u(n) = e^(−jπun(n+1)/63), n = 0…30; e^(−jπu(n+1)(n+2)/63), n = 31…61; u = 25/29/34 для N_ID^(2) = 0/1/2",
     "SSS": "s̃: x(i+5) = x(i+2)+x(i); c̃: x(i+5) = x(i+3)+x(i); z̃: x(i+5) = x(i+4)+x(i+2)+x(i+1)+x(i); начальное x(0…3) = 0, x(4) = 1; s̃/c̃/z̃ = 1 − 2x",
     "m0, m1": "q' = ⌊N1/30⌋, q = ⌊(N1 + q'(q'+1)/2)/30⌋, m' = N1 + q(q+1)/2, m0 = m' mod 31, m1 = (m0 + ⌊m'/31⌋ + 1) mod 31 (= табл. 6.11.2.1-1)",
     "ячейки": "504 = 168 групп × 3"},
    "LTE FDD/TDD (и LTE-M): поиск соты",
    [L.ist(s_pss, "6.11.1.1, табл. 6.11.1.1-1"), L.ist(s_sss, "6.11.2.1"), L.ist(s_sss + 1, "табл. 6.11.2.1-1")],
    "все 168 строк табл. 6.11.2.1-1 найдены в тексте и совпали с формулой m0/m1 — assert; рекуррентности s̃/c̃/z̃ и корни u сняты с текста; m-последовательность x^5+x^2+1 даёт 31 различный сдвиг",
    "есть поиск синхрослов; для чипового уровня — демодулятор OFDM (вне проекта)")

s_npss = N.odna(r"7\.4\.2\.2\.1 Sequence generation")[0]
t = N.norm(s_npss)
assert "( ) ( ) ( ) 127 0 127 mod 43 2 1 (2) ID PSS < ≤ + = − = n N n m m x n d" in t and "( ) ( ) ( ) ( ) 2 mod 4 7 i x i x i x + + = +" in t and "[ ] [ ] 0 1 1 0 1 1 1 0 1 2 3 4 5 6 = x x x x x x x" in t
s_nsss = N.odna(r"7\.4\.2\.3\.1 Sequence generation")[0]
t = N.norm(s_nsss + 1)
assert "112 mod 5 112 15 127 mod" in t and "2 mod 1 7 2 mod 4 7" in t
def nr_m(taps, init):
    x = init[:]
    for i in range(120): x.append(sum(x[i + t] for t in taps) % 2)
    return x
xp = nr_m((0, 4), [0, 1, 1, 0, 1, 1, 1])     # x(0..6) = 0,1,1,0,1,1,1 (запись [x6…x0] = [1 1 1 0 1 1 0])
assert len(set(tuple(xp[k:] + xp[:k]) for k in range(127))) == 127
rec("5G NR синхросигналы: PSS — m-последовательность 127 (x^7+x^4+1, сдвиг 43·N_ID^(2)), SSS — произведение двух m-последовательностей (x^7+x^4+1, x^7+x+1)",
    "5G NR", "синхрослово (синхропоследовательности)",
    {"PSS": "d(n) = 1 − 2x(m), m = (n + 43·N_ID^(2)) mod 127; x(i+7) = (x(i+4)+x(i)) mod 2; [x(6)…x(0)] = [1 1 1 0 1 1 0]",
     "SSS": "d(n) = [1 − 2x0((n+m0) mod 127)]·[1 − 2x1((n+m1) mod 127)]; m0 = 15⌊N1/112⌋ + 5·N2, m1 = N1 mod 112; x0(i+7) = x0(i+4)+x0(i), x1(i+7) = x1(i+1)+x1(i); [x(6)…x(0)] = [0 0 0 0 0 0 1]",
     "ячейки": "1008 = 336 × 3", "блок SS/PBCH": "4 символа × 240 поднесущих (PSS, PBCH, SSS+PBCH, PBCH)"},
    "5G NR (и NR-Light/RedCap): поиск соты",
    [N.ist(s_npss, "7.4.2.2.1"), N.ist(s_nsss, "7.4.2.3.1")],
    "рекуррентности, начальные значения и формулы m0/m1 сняты с текста программно; x^7+x^4+1 с начальным 1110110 даёт m-последовательность (127 различных сдвигов) — assert",
    "есть поиск синхрослов; полярный код PBCH — запись «5G NR полярный код»")

# ============================================================================================= любительские цифровые виды (WSJT-X)
W = "wsjtx"
P_ADOC = kod(W, "doc/user_guide/en/protocols.adoc", licenziya="COPYING")
ADOC = open(os.path.join(KOREN, P_ADOC)).read()
def ftn_hex_rows(put):
    return re.findall(r'"([0-9a-fA-F]+)"', open(os.path.join(KOREN, put)).read())
def ftn_data(put, imya):
    t = open(os.path.join(KOREN, put)).read()
    m = re.search(r"(?:\bdata\s+|/\s*,\s*)" + imya + r"\s*/(.*?)/", t, re.S | re.I)
    assert m, f"нет data {imya} в {put}"
    return [int(x) for x in re.findall(r"-?\d+", m.group(1))]
def gen_iz_hex(rows, k):
    return [[(int(r, 16) >> (len(r) * 4 - 1 - j)) & 1 for j in range(k)] for r in rows]
def proverit_ldpc(G, Mn, n, k):
    """G: M×k (проверочные = G·m), кодовое слово [m | p]; Mn: для каждого из n бит — номера проверок (с 1).
    Возвращает True, если H·c = 0 для базиса (k единичных сообщений)."""
    M = n - k
    H = [[0] * n for _ in range(M)]
    for col in range(n):
        for chk in Mn[3 * col:3 * col + 3]:
            H[chk - 1][col] ^= 1
    for j in range(k):
        c = [0] * n; c[j] = 1
        for i in range(M): c[k + i] = G[i][j]
        for row in H:
            if sum(row[t] & c[t] for t in range(n)) & 1: return False
    return True

g_w = kod(W, "lib/ft8/ldpc_174_91_c_generator.f90"); p_w = kod(W, "lib/ft8/ldpc_174_91_c_parity.f90")
G_w = gen_iz_hex(ftn_hex_rows(g_w), 91); Mn_w = ftn_data(p_w, "Mn")
assert len(G_w) == 83 and len(Mn_w) == 174 * 3
assert proverit_ldpc(G_w, Mn_w, 174, 91)
c_lib = kod("ft8_lib", "ft8/constants.c", licenziya="LICENSE")
gl = c_massiv(c_lib, "kFTX_LDPC_generator")
G_l = [[(gl[12 * i + j // 8] >> (7 - j % 8)) & 1 for j in range(91)] for i in range(83)]
assert G_l == G_w
assert c_massiv(c_lib, "kFT8_Costas_pattern") == [3, 1, 4, 0, 6, 5, 2] == ftn_data(kod(W, "lib/ft8/genft8.f90"), "icos7")
crc14 = kod(W, "lib/crc14.cpp"); assert "#define POLY 0x2757" in open(os.path.join(KOREN, crc14)).read()
hl = kod("ft8_lib", "ft8/constants.h"); assert "FT8_CRC_POLYNOMIAL ((uint16_t)0x2757u)" in open(os.path.join(KOREN, hl)).read()
gray8 = c_massiv(c_lib, "kFT8_Gray_map")
tft8 = tablica("ft8_ldpc_174_91_generator", {"G_83x91_hex_WSJTX": ftn_hex_rows(g_w), "Mn_174x3": [Mn_w[3 * i:3 * i + 3] for i in range(174)],
               "costas7": [3, 1, 4, 0, 6, 5, 2], "gray8": gray8}, "FT8/FT4: порождающая (проверочная часть 83×91) и проверочная (Mn) LDPC (174,91), Costas 7×7, Грей",
               [ist_kod(g_w, r"data g/"), ist_kod(p_w, r"data Mn/"), ist_kod(c_lib, r"kFTX_LDPC_generator")],
               "G из WSJT-X (hex Fortran) = G из ft8_lib (байты C) побитно; H·[m|G·m] = 0 для всех 91 базисных сообщений по Mn WSJT-X")
assert "FT8 uses the same LDPC (174,91) code as FT4" in ADOC and "174/3 + 21 = 79" in ADOC
rec("FT8 (WSJT-X): 77 бит + CRC-14 (0x2757) → LDPC (174,91) → 8-GFSK 6,25 Бод, 79 символов, 3 массива Костаса 7×7",
    "Любительские цифровые виды (WSJT-X)", "каскадный: CRC + LDPC (174,91)",
    {"сообщение": "77 бит (типы i3.n3) + 3 нулевых → CRC-14 по 82 битам, g = 0x2757 (x^14 без старшего)", "LDPC": "(174,91): кодовое слово = 91 бит (сообщение + CRC) + 83 проверочных = G·m; G → " + tft8,
     "символы": "174 бита по 3 → 58 символов данных (Грей " + str(gray8) + ") + Costas [3,1,4,0,6,5,2] в символах 0–6, 36–42, 72–78 = 79",
     "модуляция": "8-GFSK, 12000/1920 = 6,25 Бод, разнос 6,25 Гц, 12,64 с в 15-секундном цикле"},
    "любительская связь КВ/УКВ (WSJT-X, JTDX, MSHV), повсеместно с 2017 г.",
    [ist_kod(P_ADOC, r"==== FT8"), ist_kod(g_w, r"data g/"), ist_kod(p_w, r"data Mn/"), ist_kod(crc14, r"#define POLY 0x2757"), ist_kod(c_lib, r"kFTX_LDPC_generator"), ist_kod(hl, r"FT8_CRC_POLYNOMIAL")],
    "два независимых источника кода совпали: G WSJT-X (Fortran, hex) = G ft8_lib (C, байты) — все 83×91 бит; проверочная матрица WSJT-X (Mn) аннулирует все 91 базисных кодовых слова (H·cᵀ = 0); CRC-14 0x2757 и Costas одинаковы в обоих — assert",
    "есть общий LDPC (ldpc.py: H из alist/таблиц, BP-декодер) — загрузить H (таблица готова), CRC-14; демодуляция 8-GFSK — нет")
g4 = kod(W, "lib/ft4/genft4.f90")
rvec = ftn_data(g4, "rvec")
xl = c_massiv(c_lib, "kFT4_XOR_sequence")
rv_lib = [(xl[i // 8] >> (7 - i % 8)) & 1 for i in range(77)]
assert rvec == rv_lib and len(rvec) == 77
c4 = [ftn_data(g4, "icos4" + b) for b in "abcd"]
assert c4 == [c_massiv(c_lib, "kFT4_Costas_pattern")[4 * i:4 * i + 4] for i in range(4)]
assert "174/2 + 16\n+ 2 = 105" in ADOC or "174/2 + 16 + 2 = 105" in re.sub(r"\s+", " ", ADOC)
rec("FT4 (WSJT-X): 77 бит ⊕ rvec (77) + CRC-14 → тот же LDPC (174,91) → 4-GFSK 20,83 Бод, 105 символов (4 массива Костаса 4×4)",
    "Любительские цифровые виды (WSJT-X)", "каскадный: скремблер + CRC + LDPC (174,91)",
    {"скремблер": "msgbits ⊕ rvec = " + "".join(map(str, rvec)), "LDPC/CRC": "как FT8 (" + tft8 + ")",
     "синхро": f"Costas 4×4: {c4} в символах 1–4, 34–37, 67–70, 100–103 (+ символы нарастания/спада)", "модуляция": "4-GFSK 12000/576 = 20,833 Бод, 87 символов данных (Грей 0,1,3,2)"},
    "любительская связь (контесты) — WSJT-X 2.1+",
    [ist_kod(P_ADOC, r"==== FT4"), ist_kod(g4, r"data rvec/"), ist_kod(c_lib, r"kFT4_XOR_sequence")],
    "rvec WSJT-X (77 бит) = kFT4_XOR_sequence ft8_lib побитно; 4 массива Костаса совпали в обоих источниках — assert",
    "как FT8: LDPC (174,91) — таблица готова; скремблер — XOR с константой")

conv = kod(W, "lib/conv232.f90"); cv = ftn_data(conv, "npoly1") + ftn_data(conv, "npoly2")
cv = [x & 0xFFFFFFFF for x in cv]
assert cv == [0xF2D05351, 0xE4613C47] and "0xf2d05351 and 0xe4613c47" in ADOC
fano = kod(W, "lib/wsprd/fano.c"); assert "#define\tPOLY1\t0xf2d05351" in open(os.path.join(KOREN, fano)).read()
wsu = kod(W, "lib/wsprd/wsprsim_utils.c"); gw = kod(W, "lib/genwspr.f90")
pr3_c = c_massiv(wsu, "pr3"); pr3_f = ftn_data(gw, "npr3")
assert pr3_c == pr3_f and len(pr3_c) == 162
def brev8(i): return int(format(i, "08b")[::-1], 2)
perm = [j for j in (brev8(i) for i in range(256)) if j < 162]
assert sorted(perm) == list(range(162))
jt9s = kod(W, "lib/jt9sync.f90"); ii = ftn_data(jt9s, "ii")
assert ii == [1, 2, 5, 10, 16, 23, 33, 35, 51, 52, 55, 60, 66, 73, 83, 85]
assert "1, 2, 5, 10, 16, 23, 33, 35, 51, 52, 55,\n60, 66, 73, 83, and 85" in ADOC
il9 = kod(W, "lib/interleave9.f90")
perm9 = [j for j in (brev8(i) for i in range(256)) if j <= 205]
assert sorted(perm9) == list(range(206))
jt4sync = "".join(re.search(r"The pseudo-random sync vector is the\nfollowing sequence \(60 bits per line\):\n\n(.*?)\n\n", ADOC, re.S).group(1).split())
assert len(jt4sync) == 206
twk = tablica("wspr_jt4_jt9", {"poly1": "0xF2D05351", "poly2": "0xE4613C47", "WSPR_sync_162": pr3_c, "WSPR_perestanovka_bitreverse": perm,
              "JT9_sync_pozicii": ii, "JT9_perestanovka_206": perm9, "JT4_sync_206": jt4sync},
              "WSPR/JT4/JT9: многочлены K=32, синхровекторы, перестановки перемежения (обращение 8 бит с отбрасыванием ≥ n)",
              [ist_kod(conv, "npoly1"), ist_kod(wsu, r"pr3\[162\]"), ist_kod(jt9s, r"data ii/")], "pr3 C = npr3 Fortran; перестановки биективны")
rec("WSPR / JT4 / JT9: свёрточный K=32 r=1/2 (0xF2D05351, 0xE4613C47, Лейланд — Лушбо) с нулевым хвостом + перемежение обращением 8 бит + синхровектор",
    "Любительские цифровые виды (WSJT-X)", "свёрточный (K=32, последовательное декодирование Фано)",
    {"многочлены": "POLY1 = 0xF2D05351, POLY2 = 0xE4613C47 (регистр 32 бита, новый бит — младший; выход POLY1 первым)",
     "WSPR": "50 бит → (50+31)·2 = 162; перемежение j = bitrev8(i), j < 162; символ = 2·data + sync (pr3, 162 бита); 4-FSK 1,4648 Бод",
     "JT4": "72 бита → 206; 4-FSK 4,375 Бод; символ = data (старший) + sync (вектор 206 из протокола)",
     "JT9": "72 бита → 206 → 69 символов по 3 бита + 16 синхро в позициях 1,2,5,10,16,23,33,35,51,52,55,60,66,73,83,85; 9-FSK 1,736 Бод; перемежение bitrev8 ≤ 205",
     "таблицы": twk},
    "любительская связь КВ/НЧ (WSPR-маяки, JT9/JT4 — слабые сигналы, ЛЭС)",
    [ist_kod(P_ADOC, r"==== JT4"), ist_kod(P_ADOC, r"==== WSPR"), ist_kod(conv, "npoly1"), ist_kod(fano, r"#define\tPOLY1\t0xf2d05351"), ist_kod(wsu, r"pr3\[162\]"), ist_kod(gw, r"data npr3/"), ist_kod(jt9s, r"data ii/"), ist_kod(il9, r"subroutine interleave9")],
    "многочлены: описание протокола (protocols.adoc) = conv232.f90 (−221228207, −463389625 как беззнаковые 32 бит) = fano.c; синхровектор WSPR: C (wsprsim_utils.c) = Fortran (genwspr.f90) — все 162; позиции синхро JT9: протокол = jt9sync.f90; перестановки обращения бит биективны — assert",
    "есть общий свёрточный кодер (svyortka); K=32 витерби непригоден (2^31 состояний) — нужен Фано/стековый декодер (средне)")

wk = kod(W, "lib/wrapkarn.c"); assert "init_rs_int(6,0x43,3,1,51,0)" in open(os.path.join(KOREN, wk)).read()
il63 = kod(W, "lib/interleave63.f90"); g65 = kod(W, "lib/gen65.f90")
nprc = ftn_data(g65, "nprc")
ad65 = "".join(re.search(r"following pseudo-random sequence:\n\n(.*?)\n\n", ADOC, re.S).group(1).split())
assert "".join(map(str, nprc)) == ad65 and len(ad65) == 126 and ad65.count("1") == 63
# корни RS: fcr = 3, prim = 1, 51 корень → α^3…α^53 над GF(64)/x^6+x+1 (0x43)
E = [0] * 126; v = 1
for i in range(63):
    E[i] = E[i + 63] = v; v <<= 1
    if v & 64: v ^= 0x43
assert len(set(E[:63])) == 63
rec("JT65 (WSJT-X): РС (63,12) над GF(64) (p = 0x43, корни α^3…α^53) + перемежение 7×9 + Грей + синхровектор 126 → 65-FSK 2,69 Бод",
    "Любительские цифровые виды (WSJT-X)", "РС",
    {"РС": "init_rs_int(symsize 6, gfpoly 0x43 = x^6+x+1, fcr 3, prim 1, nroots 51) — Karn; 72 бита = 12 символов по 6 бит → 63", "перемежение": "матрица 7×9 транспонируется (interleave63)",
     "Грей": "символы в код Грея перед модуляцией", "синхро": ad65 + " (63 единицы — тон синхро)", "модуляция": "65-FSK, 11025/4096 = 2,692 Бод; JT65B/C — разнос ×2/×4"},
    "любительская связь ЛЭС (EME), КВ слабые сигналы",
    [ist_kod(P_ADOC, r"==== JT65"), ist_kod(wk, r"init_rs_int\(6,0x43,3,1,51,0\)"), ist_kod(il63, r"subroutine interleave63"), ist_kod(g65, r"data nprc/")],
    "синхровектор: описание протокола = gen65.f90 (126 бит, 63 единицы); параметры РС из кода Karn; поле 0x43 примитивно (63 различные степени α) — assert",
    "есть общий РС (rs_bch.py: любые GF(2^m), fcr, prim) — параметры заданы; демодуляция 65-FSK — нет")

g240 = kod(W, "lib/fst4/ldpc_240_101_generator.f90"); p240 = kod(W, "lib/fst4/ldpc_240_101_parity.f90")
G240 = gen_iz_hex(ftn_hex_rows(g240), 101); Mn240 = ftn_data(p240, "Mn")
assert len(G240) == 139 and proverit_ldpc(G240, Mn240, 240, 101)
g74 = kod(W, "lib/fst4/ldpc_240_74_generator.f90"); p74 = kod(W, "lib/fst4/ldpc_240_74_parity.f90")
G74 = gen_iz_hex(ftn_hex_rows(g74), 74); Mn74 = ftn_data(p74, "Mn")
assert len(G74) == 166 and proverit_ldpc(G74, Mn74, 240, 74)
c24 = kod(W, "lib/fst4/get_crc24.f90"); assert "0x100065b" in open(os.path.join(KOREN, c24)).read()
gf4 = kod(W, "lib/fst4/genfst4.f90"); sw1 = ftn_data(gf4, "isyncword1"); sw2 = ftn_data(gf4, "isyncword2")
rec("FST4 / FST4W (WSJT-X): CRC-24 (0x100065B) + LDPC (240,101) / (240,74) → 4-GFSK, 160 символов (5 × 8 синхро)",
    "Любительские цифровые виды (WSJT-X)", "каскадный: CRC + LDPC",
    {"FST4": "77 бит + CRC-24 = 101 → LDPC (240,101)", "FST4W": "50 бит + CRC-24 = 74 → LDPC (240,74)", "CRC-24": "многочлен 0x100065B (x^24 + x^10 + x^9 + x^6 + x^5 + x^4 + x^3 + x + 1)",
     "синхро": f"isyncword1 = {sw1}, isyncword2 = {sw2} (8 символов, 5 групп)", "модуляция": "4-GFSK, 120 символов данных, циклы 15…1800 с"},
    "любительская связь НЧ/СЧ/КВ (2020+), маяки FST4W",
    [ist_kod(P_ADOC, r"==== FST4$"), ist_kod(g240, r"data g/"), ist_kod(p240, r"data Mn/"), ist_kod(g74, r"data g/"), ist_kod(p74, r"data Mn/"), ist_kod(c24, "0x100065b"), ist_kod(gf4, "isyncword1/")],
    "для обоих кодов H (Mn WSJT-X) аннулирует все базисные кодовые слова из G (101 и 74) — assert; CRC-24 из кода, параметры (n,k) = описанию протокола",
    "есть общий LDPC — H готова (из Mn); демодулятора нет")
g128 = kod(W, "lib/ldpc_128_90_generator.f90"); bp128 = kod(W, "lib/ldpc_128_90_reordered_parity.f90"); bpd128 = kod(W, "lib/bpdecode128_90.f90")
assert 'include "ldpc_128_90_reordered_parity.f90"' in open(os.path.join(KOREN, bpd128)).read()
G128 = gen_iz_hex(ftn_hex_rows(g128), 90); Mn128 = ftn_data(bp128, "Mn")
assert len(G128) == 38 and proverit_ldpc(G128, Mn128, 128, 90)
c13 = kod(W, "lib/crc13.cpp"); assert "#define POLY 0x15D7" in open(os.path.join(KOREN, c13)).read()
gm = kod(W, "lib/genmsk_128_90.f90"); s8 = ftn_data(gm, "s8")
rec("MSK144 (WSJT-X, метеорное рассеяние): 77 бит + CRC-13 (0x15D7) → LDPC (128,90) + 2 × 8 бит синхро → OQPSK/MSK 2000 Бод, кадр 144 бит (72 мс)",
    "Любительские цифровые виды (WSJT-X)", "каскадный: CRC + LDPC (128,90)",
    {"LDPC": "(128,90): 38 проверочных = G·m", "CRC-13": "0x15D7", "синхро": f"s8 = {s8} (дважды в кадре)", "короткие": "MSK40: LDPC (32,16) + 8 бит синхро (обратный порядок)"},
    "любительская связь УКВ (метеорное рассеяние)",
    [ist_kod(P_ADOC, r"==== MSK144"), ist_kod(g128, r"data g/"), ist_kod(bp128, r"data Mn/"), ist_kod(c13, r"#define POLY 0x15D7"), ist_kod(gm, r"data s8/")],
    "H (Mn из ldpc_128_90_reordered_parity.f90, подключаемого в bpdecode128_90.f90) аннулирует все 90 базисных кодовых слов из G (ldpc_128_90_generator.f90) — assert",
    "есть общий LDPC — H готова; демодулятора MSK нет")
q65e = kod(W, "lib/qra/q65/q65_encoding_modules.f90") if os.path.exists(os.path.join(REPOS, W, "lib/qra/q65/q65_encoding_modules.f90")) else None
q65c = kod(W, "lib/qra/q65/qra15_65_64_irr_e23.c")
assert "Q65 is intended for scatter, EME" in ADOC and "(65,15) block code with six-bit symbols" in ADOC
qsrc = open(os.path.join(KOREN, q65c)).read()
rec("Q65 (WSJT-X): q-ичный код QRA (65,15) над GF(64), 2 выколотых символа = CRC-12 → эффективно (63,13), 22 символа синхро «тон 0», 65-FSK",
    "Любительские цифровые виды (WSJT-X)", "q-ичный RA (QRA, нерегулярный повторно-накопительный над GF(64))",
    {"код": "(65,15) над GF(64): 13 информационных + 2 символа CRC-12 (выколоты) → 63 передаваемых", "синхро": "22 символа тона 0 (псевдослучайные позиции)",
     "модуляция": "65-FSK; подвиды A–E: разнос ×1…×16; циклы 15–300 с", "описание кода": "lib/qra/q65/qra15_65_64_irr_e23.c (таблицы графа Таннера)"},
    "любительская связь ЛЭС, тропосферное и ионосферное рассеяние",
    [ist_kod(P_ADOC, r"==== Q65"), ist_kod(q65c, r"qra15_65_64_irr_e23")],
    "параметры (65,15)/(63,13), CRC-12, 85 символов — из описания протокола (protocols.adoc); таблицы кода — в открытом коде (GPL-3); 63 + 22 = 85 — согласовано",
    "нет: q-ичный BP над GF(64) — сложно; таблицы есть в открытом коде")

# ============================================================================================================ CT2 (I-ETS 300 131)
C2 = TekstS("istochniki/dect/iets_300131e02p.pdf")
s_cw = C2.odna(r"6\.3\.6 Check field encoding")[0]
t = C2.norm(s_cw)
assert "(63,48) cyclic code" in t and "x15 + x14 + x13 + x11 + x4 + x2 + 1" in t and "Bit 7 of octet 8 is inverted" in t and "whole 64-bit code word has even parity" in t
G_CT2 = st(15, 14, 13, 11, 4, 2, 0)
s_ex = C2.odna(r"Annex G \(informative\): Code word example")[0]
tex = C2.norm(s_ex)
def stroki_bit(tekst):
    """Строки таблиц-рамок вида «³ b8 … b1 ³ n» → [(биты 8…1, n)]."""
    r = []
    for l in tekst.split("\n"):
        m = re.fullmatch(r"\s*[^\s\d]?\s*((?:[01]\s+){7}[01])\s*[^\s\d]?\s*(\d)\s*", l)
        if m: r.append((m.group(1), m.group(2)))
    return r
okt = stroki_bit(C2.str_ves(s_ex))
oktety = {int(n): [int(b) for b in bits.split()] for bits, n in okt}
assert sorted(oktety) == list(range(1, 9)), oktety
def tx(o): return oktety[o][::-1]          # в таблице биты 8…1; передаётся бит 1 первым (6.3.1)
dannye = sum((tx(o) for o in range(1, 7)), [])
ostatok = pmod(int("".join(map(str, dannye)), 2) << 15, G_CT2)
assert format(ostatok, "015b") == "000010101111010" and "x10 + x8 + x6 + x5 + x4 + x3 + x1" in tex and "that is 000010101111010" in tex
proverka_slovo = format(ostatok ^ 1, "015b")
slovo = dannye + [int(c) for c in proverka_slovo]
slovo += [sum(slovo) % 2]
assert slovo[48:] == tx(7) + tx(8) and "".join(map(str, slovo[48:])) == "0000101011110111" and "0000101011110111" in tex
assert sum(slovo) % 2 == 0
s_chm = C2.odna(r"The bit patterns for SYNCP, SYNCF, CHMP and CHMF are given below")[0]
tch = C2.norm(s_chm)
SW = {}
for nm in ("CHMF", "CHMP", "SYNCF", "SYNCP"):
    m = re.search(nm + r" ((?:[01]{4} \. ){5}[01]{4}) \(([0-9A-F]{6})H\)", tch)
    SW[nm] = (m.group(1).replace(" . ", ""), m.group(2)); assert int(SW[nm][0], 2) == int(SW[nm][1], 16)
assert int(SW["CHMF"][0], 2) ^ int(SW["CHMP"][0], 2) == 0xFFFFFF and int(SW["SYNCF"][0], 2) ^ int(SW["SYNCP"][0], 2) == 0xFFFFFF
s_sd = C2.odna(r"6\.3\.1 Order of transmission and field mapping convention")[0]
sb = stroki_bit(C2.str_ves(s_sd)); lines = C2.str_ves(s_sd).split("\n")
i_sd = [i for i, l in enumerate(lines) if re.search(r"\bSYNCD\s*[^\s\d]?\s*$", l) and "IDLE_D" not in l][0]
pered = [re.fullmatch(r"\s*[^\s\d]?\s*((?:[01]\s+){7}[01])\s*[^\s\d]?\s*(\d)\s*", lines[j]) for j in range(i_sd - 3, i_sd + 3)]
pered = [(m.group(1), m.group(2)) for m in pered if m]
o1 = [b for b, n in pered if n == "1"][0]; o2 = [b for b, n in pered if n == "2"][0]
syncd = "".join(o1.split())[::-1] + "".join(o2.split())[::-1]
assert syncd == "1100010011010111"                                # = SYNC MPT1327 (0xC4D7) — второй источник
mpt = [z for z in json.load(open(os.path.join(KOREN, "katalog.json"))) if z["имя"].startswith("MPT1327")][0]
assert "0xC4D7" in mpt["параметры"]["синхро"] and mpt["параметры"]["g(X)"].replace("X", "x") == "x15+x14+x13+x11+x4+x2+1"
s_rs = C2.odna(r"R\.2\.2\.2 Forward error control \(FEC\)")[0]
trs = C2.norm(s_rs)
assert "g(x)=1+x2+x3+x4+x8" in trs and "parity symbols of a codeword shall be inverted" in trs and "In this case n=63 and i=192 symbols" in trs
s_fr = C2.odna(r"R\.2\.2 Framing and forward error control \(FFEC\)")[0]
assert "(01111110, the leftmost bit shall be transmitted first) followed by a 504-bit Reed-Solomon codeword" in C2.norm(s_fr)
s_rt = C2.odna(r"R\.2\.4 Synchronous Rate Adaptor")[0]
trt = " ".join(C2.norm(s) for s in (s_rt, s_rt + 1, s_rt + 2))
RT = [(int(a), int(k), int(mx), int(mn), int(rmx), int(rmn)) for a, k, mx, mn, rmx, rmn in re.findall(r"(\d+) 63,(\d+);3 (\d+) (\d+) (\d+) (\d+)", trt)]
assert [r[1] for r in RT] == [2, 4, 6, 11, 21, 30, 40]
for a, k, mx, mn, rmx, rmn in RT:
    assert mx == 8 * k - 8 and (mn == mx - 8 or (k == 2 and mn == 0)) and rmx == mx * 62.5 and rmn == mn * 62.5 and rmx >= a
s_lp = C2.odna(r"Table R\.1: Reed-Solomon Formats for LAPR")[0]
assert re.search(r"63,44;3 352 42 40 20000", C2.norm(s_lp) + " " + C2.norm(s_lp + 1)) and 8 * 44 == 352 and 44 - 2 == 42 and 8 * 62.5 * 40 == 20000
assert pmod(1 << 255, st(8, 4, 3, 2, 0)) == 1 and all(pmod(1 << (255 // p), st(8, 4, 3, 2, 0)) != 1 for p in (3, 5, 17))   # 0x11D примитивен
rec("CT2 / CAI (I-ETS 300 131): D-канал — циклический (63,48), g = x15+x14+x13+x11+x4+x2+1, инверсия последнего проверочного + общая чётность = слово 64 бита; синхрослова CHM/SYNC 24 бита, SYNCD 16 бит",
    "CT2 (I-ETS 300 131)", "БЧХ/циклический (64,48) + синхрослова",
    {"код": "48 информационных бит (октеты 1–6) × x^15 mod g(x), g = x15+x14+x13+x11+x4+x2+1; 15 проверочных → инвертировать коэффициент x^0 (бит 7 октета 8) → бит 8 октета 8 — чётность по 64 битам (6.3.6)",
     "порядок бит": "октеты по возрастанию, в октете бит 1 первым; поле проверки — старший разряд первым (бит 1 октета 7)",
     "синхрослова": {k: f"{v[0]} ({v[1]}h)" for k, v in SW.items()} | {"SYNCD (в порядке передачи)": syncd + " (0xC4D7 = SYNC MPT1327)", "IDLE_D": "1010… (последний бит перед SYNCD — 0)"},
     "кадр": "MUX1/MUX2/MUX3, 72 кбит/с GFSK, 2 мс (B-канал 32 кбит/с ADPCM G.721, D-канал 1–2 кбит/с)"},
    "цифровые беспроводные телефоны CT2 (Telepoint: Rabbit (Великобритания), Bi-Bop (Франция), Гонконг, Нидерланды, 864–868 МГц)",
    [C2.ist(s_cw, "6.3.6"), C2.ist(s_ex, "прил. G: пример кодового слова"), C2.ist(s_chm, "CHMF/CHMP/SYNCF/SYNCP"), C2.ist(s_sd, "6.3: SYNCD")],
    "тестовый вектор прил. G воспроизведён: остаток 000010101111010 (x10+x8+x6+x5+x4+x3+x), после инверсии и чётности — 0000101011110111 (= октеты 7–8 таблицы) — assert; CHMF ⊕ CHMP = SYNCF ⊕ SYNCP = FFFFFF; "
    "второй источник: тот же g(x) и SYNC 1100010011010111 у MPT1327 (запись «MPT1327», istochniki/trank/MPT1327.pdf стр. 28–29)",
    "есть: код (64,48) MPT1327 в проекте (БЧХ/циклический общего вида — rs_bch.py) — те же g(x) и правило инверсии; синхрослова — поиск синхрослов")
rec("CT2 / CAI прил. R (данные): укороченный РС (63,k) над GF(256), p(x) = 1+x2+x3+x4+x8, инверсия проверочных символов, кадр 8 бит 01111110 + 504 бита, LAPR / синхронный адаптер",
    "CT2 (I-ETS 300 131)", "РС (укороченный)",
    {"РС": "(255,K) → (63,k) укорочением на 192 символа; поле GF(256) p(x) = 1+x2+x3+x4+x8 (0x11D); проверочные символы инвертируются; корни порождающего многочлена в тексте НЕ указаны",
     "кадр FEC": "синхро 01111110 (левый бит первым) + 63 символа × 8 бит = 512 бит на 8 пачках B-канала, 16 мс",
     "форматы": {"синхронный адаптер": {str(a): f"(63,{k};3): {mx} бит/кадр, {rmx} бит/с" for a, k, mx, mn, rmx, rmn in RT}, "LAPR": "(63,44;3): Smax 352, Lmax 42, информация ≤ 40 октетов, 20000 бит/с"},
     "LAPR": "RFU 4 | Length 6 | User Defined 6 | адрес 8 | управление 8 | информация (LAPB без флагов и FCS)"},
    "CT2: службы передачи данных (прил. R)", [C2.ist(s_fr, "R.2.2: кадр FEC"), C2.ist(s_rs, "R.2.2.2"), C2.ist(s_rt, "R.2.4, табл. R.2"), C2.ist(s_lp, "табл. R.1")],
    "таблица форматов согласована: данные = 8k − 8 бит, скорость = бит/кадр × 62,5 кадр/с ≥ скорости линии; LAPR: 8·44 = 352, 44 − 2 = 42, 8·62,5·40 = 20000; 0x11D примитивен — assert",
    "частично: общий РС в проекте есть (rs_bch.py), но корни g(x) (fcr) стандарт не задаёт — подбирать слепым поиском корней (rs_bch умеет)")

# ===================================================================================== Eurobalise (ERTMS/ETCS, UNISIG SUBSET-036)
EB = TekstS("istochniki/zhd/sos3_index009_-_subset-036_v310.pdf")
s_fmt = EB.odna(r"4\.3\.1\.2 Telegram Format")[0]
tf = EB.norm(s_fmt) + " " + EB.norm(s_fmt + 1)
assert "nL = 1023 (= 93·11)" in tf and "nS = 341 (= 31·11)" in tf and "830 user bits" in tf and "210 user bits" in tf and "comprise 75 parity bits of the error detecting code and 10 bits for synchronisation" in tf
assert 83 * 11 + 3 + 12 + 10 + 85 == 1023 and 21 * 11 + 3 + 12 + 10 + 85 == 341
s_ck = EB.odna(r"4\.3\.2\.4 Computing the Check Bits")[0]
tck = EB.norm(s_ck)
def poli(imya, t):
    m = re.search(imya + r"\(x\) ?= ?(x\d+(?: ?\+ ?(?:x\d*|1))+)", t)
    s_ = m.group(1).replace(" ", "")
    return sum(1 << (int(a[1:]) if a.startswith("x") and len(a) > 1 else (1 if a == "x" else 0)) for a in s_.split("+")), s_
fL, fL_s = poli("fL", tck); gL, gL_s = poli("gL", tck); fS, fS_s = poli("fS", tck); gS, gS_s = poli("gS", tck)
assert fL.bit_length() == 11 and fS.bit_length() == 11 and gL.bit_length() == 76 and gS.bit_length() == 76
assert pmod(pmul(gS, st(682, 341, 0)), gL) == 0                     # (4): трёхкратный короткий удовлетворяет длинному
assert pmod((1 << 1023) | 1, gL) == 0 and pmod((1 << 341) | 1, gS) == 0   # циклические коды длины 1023 и 341
def poryadok(f, n):
    return min(d for d in range(1, n + 1) if n % d == 0 and pmod((1 << d) | 1, f) == 0)
assert poryadok(fL, 1023) == 1023 and poryadok(fS, 341) == 341      # R_f однозначно даёт сдвиг (начало телеграммы)
s_tab = EB.odna(r"B2 The 10-to-11 bit Transformation Substitution Words")[0]
tt = " ".join(EB.str_ves(s) for s in range(s_tab, s_tab + 3))
tt = tt[tt.find("The words are listed in octal."):tt.find("The 1024 words in this list")]
SL = [int(x, 8) for x in re.findall(r"\b([0-7]{5})\b", tt)]
assert len(SL) == 1024 and all(a < b for a, b in zip(SL, SL[1:])) and max(SL) < 2048
assert sum(SL[:512]) == 267528 and sum(SL) == 1048064 == 512 * 2047     # контрольные суммы из стандарта
assert "the sum of the first 512 words is 267528" in EB.norm(s_tab + 2)
assert all(SL[i] + SL[1023 - i] == 2047 for i in range(1024))           # симметрия: дополнение слова — тоже слово
import random as _r
_r.seed(36)
verh = _r.getrandbits(1023 - 85) << 85
tel = verh ^ pmod(verh, pmul(fL, gL)) ^ gL                            # формула (3)
assert pmod(tel, gL) == 0 and pmod(tel, fL) == pmod(gL, fL)
pod = int("".join(format(tel, "01023b")[1::2] + format(tel, "01023b")[0::2]), 2)   # «недодискретизация» bn-2, bn-4, …, b1, bn-1, …, b0
assert pmod(pod, gL) == 0                                             # 4.3.2.5.5: снова кодовое слово
s_sc = EB.odna(r"4\.3\.2\.2 Scrambling")[0]
tsc = EB.norm(s_sc) + " " + EB.norm(s_sc + 1)
assert "S = (2801775573 · B) mod 232" in tsc and "h(x) = x32 + x31 + x30 + x29 + x27 + x25 + 1" in tsc
assert "2801775573 = 690693 mod 232" in tsc and pow(69069, 3, 1 << 32) == 2801775573     # в PDF степени слиплись: 69069^3 mod 2^32 — проверено
teb = tablica("eurobalise_subset036", {"podstanovka_10_11_okt": [format(x, "05o") for x in SL], "fL": fL_s, "gL": gL_s, "fS": fS_s, "gS": gS_s,
               "h_skrembler": "x32+x31+x30+x29+x27+x25+1", "S": "(2801775573·B) mod 2^32, B = b106…b95"},
              "Eurobalise (SUBSET-036 3.1.0): 1024 слова подстановки 10→11 бит (восьмеричные, по возрастанию), многочлены f, g длинного и короткого формата, скремблер",
              [EB.ist(s_tab, "прил. B2"), EB.ist(s_ck, "4.3.2.4"), EB.ist(s_sc, "4.3.2.2")],
              "1024 слова строго возрастают; суммы первых 512 = 267528 и всех = 1048064 совпали с контрольными суммами стандарта; g_L | g_S(x^682+x^341+1); g_L | x^1023+1, g_S | x^341+1")
rec("Eurobalise (ERTMS/ETCS, SUBSET-036): телеграмма 1023/341 бит — скремблер 32 бит + подстановка 10→11 бит + циклический код (75 проверочных, g_L/g_S) × f(x) (10 бит синхро)",
    "Железнодорожная сигнализация: Eurobalise (UNISIG SUBSET-036)", "циклический код (обнаружение) + подстановочный код формирования + скремблер",
    {"формат": "длинный 1023 = 83×11 данных (830 бит пользователя) + cb 3 + sb 12 + esb 10 + 85 проверочных; короткий 341 = 21×11 (210 бит) + 3 + 12 + 10 + 85",
     "проверочные": "b84…b0 = R_{f·g}[b_{n−1}x^{n−1}+…+b85x^85] + g(x); fL = " + fL_s + "; gL = " + gL_s + "; fS = " + fS_s + "; gS = " + gS_s,
     "скремблер": "первые 10 бит ← сумма всех 10-битных блоков mod 2^10; S = (2801775573·B) mod 2^32 из 12 бит sb; регистр 32 бит σ ← R_h[x·σ + u·x^32], h = x32+x31+x30+x29+x27+x25+1",
     "подстановка": "10 → 11 бит по таблице 1024 допустимых слов → " + teb, "условия": "алфавит (каждое 11-битное слово допустимо), off-synch (≤ 2 / ≤ 10 / ≤ 6 подряд допустимых при сдвиге), апериодичность длинного (расстояние ≥ 3/2 на сдвиге 341±k), недодискретизация",
     "биты управления": "b109 = 0 (инверсия), b108 = 0, b107 = 1", "передача": "слева направо (с любого места — код циклический), DBPSK/FSK 564,48 кбит/с (интерфейс A1)"},
    "ERTMS/ETCS: путевые приёмоответчики (балисы) Eurobalise — передача на поезд (также Euroloop — тот же формат)",
    [EB.ist(s_fmt, "4.3.1.2"), EB.ist(s_sc, "4.3.2.2"), EB.ist(s_ck, "4.3.2.4"), EB.ist(s_tab, "прил. B2")],
    "многочлены разобраны из текста программно: g_L делит g_S·(x^682+x^341+1) (свойство (4) стандарта), g_L | x^1023+1 и g_S | x^341+1 (циклические коды), порядок x по модулю f_L = 1023 и f_S = 341 (однозначное нахождение начала); "
    "телеграмма по формуле (3) делится на g_L и после «недодискретизации на 2» остаётся кодовым словом (как утверждает 4.3.2.5.5); таблица подстановки: 1024 слова, контрольные суммы стандарта 267528 и 1048064 совпали, слова симметричны (w + w' = 2047); множитель скремблера 2801775573 = 69069^3 mod 2^32 (как в примечании стандарта) — assert",
    "нет в проекте: циклический код/CRC общего вида есть (проверка делимости — просто), подстановка 10→11 — таблица готова; скремблер — по формуле (средне, нет тестового вектора)")

# ========================================================================================== MELP / MELPe 2400 (MIL-STD-3005, STANAG 4591)
ML = TekstS("istochniki/mil/MIL-STD-3005.017601.PDF")
s_fec = ML.odna(r"5\.4 Error protection\. Forward Error Correction")[0]
tfe = ML.str_ves(s_fec)
k74 = tfe[tfe.find("Hamming (7,4) code is"):tfe.find("G 7,4")]; k84 = tfe[tfe.find("G 7,4"):tfe.find("G8,4")]
P74 = [[int(c) for c in r] for r in re.findall(r"^\s*([01]{4})\s*$", k74, re.M)]
P84 = [[int(c) for c in r] for r in re.findall(r"^\s*([01]{4})\s*$", k84, re.M)]
assert P74 == [[1, 1, 0, 1], [0, 1, 1, 1], [1, 0, 1, 1]] and P84 == [[1, 1, 1, 0], [0, 1, 1, 1], [1, 0, 1, 1], [1, 1, 0, 1]], (P74, P84)
G74 = [[int(i == j) for j in range(4)] + [P74[r][i] for r in range(3)] for i in range(4)]
G84 = [[int(i == j) for j in range(4)] + [P84[r][i] for r in range(4)] for i in range(4)]
assert dmin(G74) == 3 and dmin(G84) == 4
nf = ML.norm(s_fec)
assert "FEC replaces those 13 bits with the parity bits of three Hamming (7,4) codes and one Hamming (8,4) code" in nf and 3 * 3 + 4 == 13
s_b2 = ML.odna(r"TABLE II\. MELP bit allocation")[0]
assert "Total Bits / 22.5 ms Frame" in ML.str_ves(s_b2) and 25 + 8 + 8 + 7 + 4 + 1 + 1 == 54 == 25 + 8 + 7 + 13 + 1
s_cw = ML.odna(r"All 28 codes with Hamming weight of 1 or 2 are reserved for error")[0]
from math import comb
assert comb(7, 1) + comb(7, 2) == 28
rec("MELP / MELPe 2400 бит/с (MIL-STD-3005, STANAG 4591): в невокализованных кадрах — 3 × Хэмминг (7,4) + 1 × расширенный Хэмминг (8,4) вместо 13 бит; код высоты/вокализации 7 бит с резервом слов веса 1–2",
    "Вокодеры: MELP (MIL-STD-3005 / STANAG 4591)", "Хэмминг",
    {"(7,4)": "p = G·u, G(7,4) (3×4) строки: 1101, 0111, 1011 (d = 3)", "(8,4)": "G(8,4) (4×4) строки: 1110, 0111, 1011, 1101 (d = 4)",
     "что защищено": "4 старших бита 1-й ступени ЛСЧ — (8,4) → младшие 4 бита индекса полосовой вокализации; b2 b1 b0 + резервный 0 — (7,4) → старшие 3 бита индекса амплитуд Фурье; 4 старших бита 2-го усиления — (7,4) → следующие 3 бита; младший бит 2-го и 3 бита 1-го усиления — (7,4) → 2 младших бита амплитуд + апериодический флаг",
     "кадр": "54 бита / 22,5 мс = 2400 бит/с; бит синхро чередуется 0/1 от кадра к кадру; порядок передачи — табл. III",
     "высота/вокализация": "7 бит; 28 кодов с весом Хэмминга 1 или 2 зарезервированы для обнаружения ошибок (кадр невокализован — код 0x00)"},
    "военная и правительственная засекреченная связь (STANAG 4591, SCIP, ВЧ-модемы 2400 бит/с, 1200/600 бит/с MELPe)",
    [ML.ist(s_fec, "5.4"), ML.ist(s_b2, "табл. II"), ML.ist(s_cw, "кодирование высоты")],
    "матрицы разобраны из текста; кодовое расстояние перебором: (7,4) — 3, (8,4) — 4 (SEC-DED); 3·3 + 4 = 13 бит, распределение 54 бит в вокализованном и невокализованном кадрах сходится; C(7,1)+C(7,2) = 28 — assert",
    "нет вокодера; Хэмминг общего вида есть — проверка/исправление защищённых битов MELP-кадра просто (если известен порядок бит табл. III)")

# ============================================================================================ FreeDV (codec2): LDPC с накопителем (HRA)
CD = "codec2"
P_RF = kod(CD, "README_freedv.md", licenziya="COPYING")
RF = open(os.path.join(KOREN, P_RF)).read()
P_MPD = kod(CD, "src/mpdecode_core.c"); P_LC = kod(CD, "src/ldpc_codes.c")
mpd = open(os.path.join(KOREN, P_MPD)).read()
assert "if (ind) par = par + ibits[ind - 1];" in mpd and "tmp = par + prev;" in mpd     # чётность = Σ данных по H_rows ⊕ предыдущая (накопитель)
def c_float(put, imya):
    t = open(os.path.join(KOREN, put)).read(); m = re.search(re.escape(imya) + r"\[\]\s*=\s*\{(.*?)\};", t, re.S)
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", m.group(1))]
def c_def(put, imya):
    return int(re.search(r"#define " + imya + r"\s+(\d+)", open(os.path.join(KOREN, put)).read()).group(1))
FDV = {}
for c in ("HRA_112_112", "HRA_56_56", "HRAb_396_504"):
    ph = kod(CD, f"src/{c}.h"); pc = kod(CD, f"src/{c}.c")
    N = c_def(ph, c + "_CODELENGTH"); M = c_def(ph, c + "_NUMBERPARITYBITS"); Wr = c_def(ph, c + "_MAX_ROW_WEIGHT"); K = N - M
    rows = [int(x) for x in c_float(pc, c + "_H_rows")]; dd = [int(x) for x in c_float(pc, c + "_detected_data")]; inp = c_float(pc, c + "_input")
    assert len(rows) == M * Wr and len(dd) == N and len(inp) == N
    prev = 0; par = []
    for r in range(M):
        prev = (sum(dd[rows[r + i * M] - 1] for i in range(Wr) if rows[r + i * M]) + prev) & 1; par.append(prev)
    assert par == dd[K:]                                                 # тестовое кодовое слово codec2 воспроизведено кодером
    sovp = sum((x < 0) == bool(b) for x, b in zip(inp, dd)); assert sovp >= N - 8, (c, sovp)   # вход (мягкие отсчёты) согласуется с ним
    FDV[c] = (N, K, Wr, ph, pc, sovp)
assert "LDPC (224,112)" in RF and "LDPC (112,56)" in RF and "LDPC (504,396)" in RF
assert FDV["HRA_112_112"][:2] == (224, 112) and FDV["HRA_56_56"][:2] == (112, 56) and FDV["HRAb_396_504"][:2] == (504, 396)
rec("FreeDV (codec2): LDPC «HRA» (повторно-накопительный) — 700D (224,112), 700E/2020B (112,56), 2020 (504,396); чётность = Σ данных по H_rows ⊕ предыдущий проверочный бит",
    "Любительская цифровая речь: FreeDV (codec2)", "LDPC (RA, накопитель)",
    {"кодер": "p[r] = (Σ_i ibits[H_rows[r + i·M] − 1] + p[r−1]) mod 2 (H_rows с 1, 0 — пусто), кодовое слово [данные | p] (mpdecode_core.c encode)",
     "коды": {c: f"N = {v[0]}, K = {v[1]}, вес строки ≤ {v[2]}; таблица H_rows/H_cols — {v[4]}" for c, v in FDV.items()},
     "режимы": "700D: Codec2 700C + 17 несущих OFDM/QPSK, (224,112); 700E: 21 несущая, (112,56); 2020: LPCNet 1733 + 31 несущая, (504,396); 2020B: (112,56) с неравной защитой (11 бит из 52); 2020C: (212,158) — удалён",
     "прочее": "текст — варикод без FEC (или reliable_text: LDPC (112,56) с перемежением); FSK_LDPC для данных (README_data.md)"},
    "любительская цифровая речь на КВ (FreeDV 1.x, freedv-gui), также данные (Wenet, FSK_LDPC)",
    [ist_kod(P_RF, r"LDPC \(224,112\)"), ist_kod(P_MPD, r"tmp = par \+ prev;"), ist_kod(P_LC, r'"HRA_112_112"')] + [ist_kod(v[4], r"_H_rows\[\] = ") for v in FDV.values()],
    "кодер по mpdecode_core.c воспроизвёл тестовые кодовые слова codec2 (*_detected_data) для всех трёх кодов: проверочная часть совпала полностью; знаки мягкого входа (*_input) совпали с кодовым словом в " +
    ", ".join(f"{v[5]}/{v[0]}" for v in FDV.values()) + " позициях (остальное — шум теста); (N,K) = таблице README_freedv — assert",
    "есть общий LDPC (ldpc.py: H из таблиц, BP) — нужен импорт H_rows/H_cols (просто); OFDM-модем FreeDV — нет")

# ======================================================================================== NAVDAT (ITU-R M.2010-3, 2026): QC-LDPC + полярные
NV = TekstS("istochniki/morskaya/R-REC-M.2010-3-202602.pdf")
VS = NV.ves.replace("−", "-")
def gf2_rang(rows, ncol):
    rows = [r for r in rows if r]; rang = 0
    for c in range(ncol - 1, -1, -1):
        piv = next((i for i in range(rang, len(rows)) if rows[i] >> c & 1), None)
        if piv is None: continue
        rows[rang], rows[piv] = rows[piv], rows[rang]
        pr = rows[rang]
        for i in range(rang + 1, len(rows)):
            if rows[i] >> c & 1: rows[i] ^= pr
        rang += 1
    return rang
def razvernut(B, a, b, L, stolbcy):
    """Строки развёрнутой H (только столбцы-блоки из `stolbcy`) как целые: блок p — единичная матрица, сдвинутая вправо на p."""
    out = []
    for i in range(a):
        for r in range(L):
            v = 0
            for jj, j in enumerate(stolbcy):
                p = B[i * b + j]
                if p >= 0: v |= 1 << (jj * L + (r + p) % L)
            out.append(v)
    return out
NVK = []
NV_PROTIVORECHIYA = []
for m in re.finditer(r"\n(?:(6\.[23]\.\d+)\s*\n)?LDPC \((\d+),(\d+)\)\s*\nRate (\S+) LDPC \((\d+),(\d+)\) of mode ([AB]) in (\d+) kHz bandwidth with (\d+)×(\d+) parity-check matrix \(lifting\s+(?:factor|code)\s+L\s*=\s*(\d+)\),? see below:(.*?)\]\s*(.*?)(?=\n(?:6\.[23]\.\d+\s*\n)?LDPC \(\d+,\d+\)\s*\nRate|\n7\s*\nCyclic redundancy check)", VS, re.S):
    razd, n, k, r_, N0, K0, rezh, bw, a, b, L, mat, hvost = m.groups()
    n, k, N0, K0, a, b, L = map(int, (n, k, N0, K0, a, b, L))
    B = [int(x) for x in re.findall(r"-?\d+", mat.split("[", 1)[1])]
    hv = re.sub(r"\s+", " ", hvost)
    mp = re.search(r"shortened by deleting the (\d+) padded bits, and punctured by deleting the last (\d+) parity bits", hv)
    if mp: pad, punc = map(int, mp.groups())
    else: assert "No shortening or puncturing" in hv, (razd, hv[:200]); pad = punc = 0
    mb = re.search(r"Before encoding, (\d+) zero bits are padded to the ([\d ]+) information bits", hv)
    if mb: assert int(mb.group(1)) == pad and int(mb.group(2).replace(" ", "")) == k
    razd = razd or "6.3.5 (номер раздела не извлекается из PDF)"
    assert len(B) == a * b and b * L == N0 and (b - a) * L == K0, (razd, len(B), a, b, L, N0, K0)
    if not (K0 - pad == k and N0 - pad - punc == n):
        NV_PROTIVORECHIYA.append(f"{razd} LDPC ({n},{k}): мать ({N0},{K0}) {a}×{b} L={L}, в тексте «No shortening or puncturing», но N0 − n = {N0 - n}, K0 − k = {K0 - k} — по-видимому, выкалываются {N0 - n - (K0 - k)} проверочных бит (по аналогии с другими кодами), стандарт этого не говорит")
    assert all(-1 <= x < L for x in B)
    # структура как у 5G NR: ядро 4×4 «двойная диагональ» (1-й столбец чётности — 3 блока в строках 0–3, два одинаковых сдвига; далее лестница нулевых сдвигов),
    # строки 4…a−1 — расширение с единичной диагональю (по одному блоку 0 в своём столбце чётности)
    jb = b - a
    c0 = [B[i * b + jb] for i in range(4)]
    assert sum(x >= 0 for x in c0) == 3 and len(set(x for x in c0 if x >= 0)) == 2, (razd, c0)
    for j in range(1, 4):
        assert [i for i in range(4) if B[i * b + jb + j] >= 0] == [j - 1, j] if j > 1 else True, (razd, j)
        assert all(B[i * b + jb + j] >= 0 for i in (j - 1, j)) or j == 1
    for i in range(a):
        for j in range(4, a):
            assert B[i * b + jb + j] == (0 if i == j else -1), (razd, i, j)
    Hp = razvernut(B, a, b, L, list(range(b - a, b)))
    assert gf2_rang(Hp, a * L) == a * L                                   # часть чётности обратима → систематическое кодирование однозначно
    NVK.append({"раздел": razd, "(n,k)": [n, k], "мать (N0,K0)": [N0, K0], "скорость": r_, "режим": rezh, "полоса_кГц": int(bw), "базовая": [a, b], "L": L, "примечание": next((x for x in NV_PROTIVORECHIYA if x.startswith(razd)), "") + ("в тексте опечатка «lifting code L» вместо «lifting factor L»" if "lifting code" in m.group(0) else ""),
                "дополнение_нулями": pad, "выкалывание_последних_проверочных": punc, "B": [B[i * b:(i + 1) * b] for i in range(a)]})
assert NV_PROTIVORECHIYA == ["6.3.4 LDPC (2170,1628): мать (2220,1628) 8×30 L=74, в тексте «No shortening or puncturing», но N0 − n = 50, K0 − k = 0 — по-видимому, выкалываются 50 проверочных бит (по аналогии с другими кодами), стандарт этого не говорит"], NV_PROTIVORECHIYA
assert len(NVK) == 16 and sorted({(x["режим"], x["полоса_кГц"], x["скорость"]) for x in NVK}) == sorted((r, w, s) for r in "AB" for w in (1, 3, 5, 10) for s in ("1/2", "3/4"))
# таблицы 25/26: (n,k) кодов в таблицах = кодам разделов 6.2/6.3
_t25 = VS.find("TABLE 25 \nLDPC parameters of data stream for mode A"); _t6 = VS.find("Low-density parity-check codes", _t25)
assert 0 < _t25 < _t6
tab_nk = set(tuple(map(int, x)) for x in re.findall(r"LDPC\s*\n?\s*\((\d+),(\d+)\)", VS[_t25:_t6]))
assert {tuple(x["(n,k)"]) for x in NVK} == tab_nk, tab_nk
s_mis = NV.odna(r"The MIS is encoded using a \(16, 48\) polar code")[0]
vm = re.search(r"The MIS is encoded using a \(16, 48\) polar code.*?following vector:\s*([01\s]+)\.", VS, re.S).group(1).split()
vt = re.search(r"The TIS is encoded using a \(76, 152\) polar code.*?following vector:\s*([01\s]+)\.", VS, re.S).group(1).split()
assert len(vm) == 64 and vm.count("0") == 16 and len(vt) == 256 and vt.count("0") == 76
assert 64 - 16 == 48 and 112 + (168 - 129 + 1) == 152
s_tis = NV.odna(r"The TIS is encoded using a \(76, 152\) polar code")[0]
s_crc = NV.odna(r"For the bit error detection in DS, the 16-bit cyclic redundancy check")[0]
tcr = NV.norm(s_crc)
assert "𝐺16(𝑥) = 𝑥16 + 𝑥12 + 𝑥5 + 1" in tcr and "𝐺8(𝑥) = 𝑥8 + 𝑥4 + 𝑥3 + 𝑥2 + 1" in tcr
s_prbs = NV.odna(r"It should use a polynomial of degree 9")[0]
assert "𝑃(𝑋) = 𝑋9 + 𝑋5 + 1" in NV.norm(s_prbs) + NV.norm(s_prbs + 1) and lfsr_period([0, 5], 9) == 511
s_ldpc = NV.odna(r"6\s+Low-density parity-check codes|^Low-density parity-check codes")[0]
tnv = tablica("navdat_m2010_ldpc_polar", {"ldpc": NVK, "polar_MIS_64_zamorozhennye(1)": "".join(vm), "polar_TIS_256_zamorozhennye(1)": "".join(vt)},
              "NAVDAT (ITU-R M.2010-3): 16 базовых матриц QC-LDPC (режимы A/B × 1/3/5/10 кГц × 1/2, 3/4) со сдвигами, L, укорочением и выкалыванием; векторы замороженных позиций полярных кодов MIS (64) и TIS (256)",
              [NV.ist(s_ldpc, "§ 6, 6.2–6.3"), NV.ist(s_mis, "3.2 MIS"), NV.ist(s_tis, "4.2 TIS")],
              "все матрицы разобраны из текста: число элементов = a×b, b·L = N0, (b−a)·L = K0, K0 − дополнение = k, N0 − дополнение − выкалывание = n; часть чётности — ядро 4×4 с двойной диагональю + единичное расширение (как 5G NR) и обратима над GF(2) (ранг a·L); (n,k) = табл. 25/26")
rec("NAVDAT (ITU-R M.2010-3, 500 кГц / КВ): DS — QC-LDPC (16 кодов, 1/2 и 3/4, двойная диагональ, укорочение + выкалывание) + CRC-16; MIS/TIS — полярные (48,16) и (152,76) + CRC-8; ПСП рассеяния x9+x5+1",
    "Морская связь: NAVDAT (ITU-R M.2010)", "LDPC (QC) + полярный + CRC",
    {"DS LDPC": {f"{x['режим']} {x['полоса_кГц']} кГц {x['скорость']}": f"({x['(n,k)'][0]},{x['(n,k)'][1]}) из ({x['мать (N0,K0)'][0]},{x['мать (N0,K0)'][1]}), {x['базовая'][0]}×{x['базовая'][1]}, L={x['L']}, +{x['дополнение_нулями']} нулей, −{x['выкалывание_последних_проверочных']} проверочных" for x in NVK},
     "MIS": "16 бит (полоса 2, устойчивость 2, модуляция TIS 1, DS 2, скорость DS 1, CRC-8) → полярный N=64, информационные позиции = нули вектора, выкалываются биты 1–16 → 48",
     "TIS": "76 бит (4-QAM) / 2×76 (16-QAM) → полярный N=256, выбираются биты 1–112 и 129–168 → 152",
     "CRC": "DS: G16 = x16+x12+x5+1; MIS/TIS: G8 = x8+x4+x3+x2+1", "рассеяние": "ПСП P(X) = X9+X5+1 (до кодирования); тестовая ПСП X20+X17+1",
     "таблицы": tnv, "модуляция": "OFDM, 4/16/64-QAM, 1–10 кГц; режимы A/B (C/D — КВ)"},
    "морское цифровое вещание навигационной информации и информации по безопасности (замена NAVTEX), 500 кГц и КВ",
    [NV.ist(s_ldpc, "§ 6"), NV.ist(s_mis, "3.2"), NV.ist(s_tis, "4.2"), NV.ist(s_crc, "§ 7"), NV.ist(s_prbs, "рассеяние энергии")],
    "все 16 базовых матриц и параметры разобраны программно: размеры, L, (N0,K0), укорочение/выкалывание взаимно согласованы и совпали с (n,k) табл. 25/26; часть чётности — как у базовых графов 5G NR: ядро 4×4 с двойной диагональю (1-й столбец — 3 блока, два равных сдвига) + единичное расширение, развёрнутая H_p обратима (ранг полный) — assert; "
    "ПРОТИВОРЕЧИЕ В СТАНДАРТЕ: " + NV_PROTIVORECHIYA[0] + "; векторы полярных кодов: 64/16 и 256/76 нулей = числу информационных бит; X9+X5+1 даёт период 511 — assert. Второго источника (открытой реализации) нет — редакция 2026 г.",
    "есть общий LDPC (QC из базовых матриц, BP) и полярный (5G NR) — загрузить матрицы (таблица готова), полярный с произвольным вектором замороженных позиций — средне")

# ========================================================================================= 4G ALE / WALE (MIL-STD-188-141D прил. G)
AD = TekstS("istochniki/mil/MIL-STD-188-141D.055865.pdf")
s_g1 = AD.odna(r"G\.5\.1\.1 Error correction coding\.")[0]
tg = AD.norm(s_g1)
assert "constraint length 9, rate 1/2 convolutional code shall be applied to the 96 PDU bits, with full tail biting, producing a 192-bit coded block" in tg
assert "(b0) T1 = x8+x6 + x5 + x4 + 1" in tg and "(b1) T2 = x8+x7 + x6 + x5 + x3 + x1+1" in tg
T1 = st(8, 6, 5, 4, 0); T2 = st(8, 7, 6, 5, 3, 1, 0)
assert (oct(T1), oct(T2)) == ("0o561", "0o753")          # = UMTS K=9 1/2 (561, 753) — второй источник: запись «UMTS свёрточные K=9»
umts9 = [z for z in json.load(open(os.path.join(KOREN, "katalog.json"))) if z["имя"].startswith("UMTS свёрточные K=9")][0]
assert "561" in umts9["имя"] and "753" in umts9["имя"]
def tb_enc(u):                                          # «полный tail-biting» по тексту: предзагрузка 8 бит, затем те же 8 бит в конце
    reg = 0; out = []
    seq = u[8:] + u[:8]
    for b in u[:8]: reg = ((reg << 1) | b) & 0x1FF
    for b in seq:
        reg = ((reg << 1) | b) & 0x1FF
        out += [bin(reg & T1).count("1") & 1, bin(reg & T2).count("1") & 1]
    return out
_r.seed(141)
u96 = [_r.getrandbits(1) for _ in range(96)]
c = tb_enc(u96); assert len(c) == 192
cs = tb_enc(u96[1:] + u96[:1]); assert cs == c[2:] + c[:2]           # циклический сдвиг входа = сдвиг выхода (признак tail-biting)
s_dw = AD.odna(r"G\.5\.1\.7\.2 Deep WALE data modulation")[0]
tdw = AD.norm(s_dw) + " " + AD.norm(s_dw + 1)
bs = re.search(r"int bitshift\[159\] = \{(.*?)\};", tdw).group(1); bitshift = [int(x) for x in re.findall(r"[01]", bs)]
assert len(bitshift) == 159 and "bitout = bitshift[158];" in tdw and "bittap = bitshift[31];" in tdw and "return (bitshift[2]<<2)+(bitshift[1]<<1)+bitshift[0];" in tdw
assert "if the quad-bit is 0001, the sequence 0404040404040404 is repeated" in tdw
s_pre = AD.odna(r"G\.5\.1\.7\.1 Deep WALE preamble")[0]
s_crc = AD.odna(r"CRC_16_S5066\(unsigned char DATA, unsigned short CRC\)")[0]
tc = AD.norm(s_crc)
assert "if (bit) CRC^=0x9299;" in tc and "CRC_result = 0xffff;" in tc and "CRC_result ^= 0xffff;" in tc
assert int(format(0x9299, "016b")[::-1], 2) == 0x9949 == st(15, 12, 11, 8, 6, 3, 0)    # отражённый 0x9299 = x16+x15+x12+x11+x8+x6+x3+1
s5066 = [z for z in json.load(open(os.path.join(KOREN, "katalog.json"))) if "D_PDU" in z["имя"]][0]
assert "0x9299" in json.dumps(s5066["параметры"], ensure_ascii=False)
def crc_s5066(data):
    crc = 0xFFFF
    for d in data:
        i = 1
        while i < 256:
            bit = (crc & 1) ^ (1 if d & i else 0); crc >>= 1
            if bit: crc ^= 0x9299
            i <<= 1
    return crc ^ 0xFFFF
def _crc_ref(data):          # независимая запись: CRC-16, многочлен 0x9949, отражённый вход/выход, начальное 0xFFFF, XOR 0xFFFF
    r = 0xFFFF
    for d in data:
        for k in range(8):
            fb = ((r >> 15) & 1) ^ ((d >> k) & 1); r = ((r << 1) & 0xFFFF) ^ (0x9949 if fb else 0)
    return int(format(r, "016b")[::-1], 2) ^ 0xFFFF
vec = [bytes(_r.getrandbits(8) for _ in range(10)) for _ in range(20)]
assert all(crc_s5066(v) == _crc_ref(v) for v in vec)
CHK = crc_s5066(b"123456789")
rec("4G ALE / WALE (MIL-STD-188-141D прил. G): свёрточный K=9 1/2 (T1 = 561, T2 = 753) с полным tail-biting на 96 бит PDU → 192, CRC-16 STANAG 5066 (отражённый 0x9299), Deep WALE: Уолш 16×4 + скремблер 159 бит",
    "MIL-STD-188-141 (ALE)", "свёрточный (tail-biting) + CRC + Уолш",
    {"код": "K=9 1/2: T1 = x8+x6+x5+x4+1 (b0, первым), T2 = x8+x7+x6+x5+x3+x+1 (восьм. 561, 753 — как UMTS); полный tail-biting: регистр предзагружается первыми 8 битами PDU, в конце они кодируются повторно; 96 → 192 бит",
     "CRC": f"CRC_16_S5066: сдвиг вправо, XOR 0x9299 (= отражённый x16+x15+x12+x11+x8+x6+x3+1), начальное 0xFFFF, XOR на выходе 0xFFFF; CRC(\"123456789\") = 0x{CHK:04X}",
     "Deep WALE": "квадбит → последовательность Уолша 16 элементов (табл. G-IX), повтор ×4 = 64 элемента; скремблирование 8PSK-символами ПСП 159-битного регистра с отводом после бита 31 (обратная связь bit158 ⊕ bit31), 16 сдвигов на символ, символ = 3 младших бита; начальное состояние bitshift[159] — в тексте; преамбула 240 мс: 14 фиксированных дибитов {0,1,2,1,0,0,2,3,1,3,3,1,2,0} + 4 особых",
     "Fast WALE": "преамбула 120 мс, данные 8PSK с пробными символами (рис. G-10 — генератор скремблирования, 3 сдвига на символ)",
     "перемежитель": "описан на страницах-изображениях стр. 308–310 PDF (текстом не извлекается)", "символьная скорость": "2400 Бод, корень из приподнятого косинуса 35 %"},
    "ВЧ-радиосвязь США/НАТО: автоматическое установление связи 4-го поколения (широкополосное WBHF ALE)",
    [AD.ist(s_g1, "G.5.1.1"), AD.ist(s_pre, "G.5.1.7.1"), AD.ist(s_dw, "G.5.1.7.2"), AD.ist(s_crc, "CRC (G.5.2)")],
    "многочлены и код CRC разобраны из текста; T1/T2 = 561/753 = UMTS K=9 1/2 (вторая запись каталога); кодер tail-biting по тексту: сдвиг входа на 1 бит даёт сдвиг выхода на 2 бита — assert; "
    "CRC_16_S5066 из текста совпала с независимой записью (0x9949 отражённый, 0xFFFF/0xFFFF) на 20 случайных PDU; 0x9299 совпадает с open5066 (запись «STANAG 5066 D_PDU»); начальное состояние скремблера — 159 бит — assert",
    "есть свёрточный кодер/Витерби K=9 (UMTS) — нужен tail-biting (как LTE TBCC в проекте — просто); CRC — общий; демодулятор Уолша/8PSK — нет")
