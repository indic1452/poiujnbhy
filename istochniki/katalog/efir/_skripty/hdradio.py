"""HD Radio / IBOC (NRSC-5-D: SY_IDD_1011s FM L1, 1012s AM L1, 1017s аудиотранспорт): скремблер 1+x^2+x^11,
свёрточные коды FM (133,171,165[,165]) и AM (561,657,711 / 561,753,711), RS(96,88) заголовка PDU, CRC-8.
Сверка — nrsc5 (theori-io, GPL-3) и вычислением порождающего многочлена RS."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

F = Tekst("istochniki/hdradio/nrsc5d_1011s.pdf"); A = Tekst("istochniki/hdradio/nrsc5d_1012s.pdf"); T = Tekst("istochniki/hdradio/nrsc5d_1017s.pdf")
N = "nrsc5"; ZAPISI = []
n_dec = kod(N, "src/decode.c"); n_cd = kod(N, "src/conv_dec.c"); n_fr = kod(N, "src/frame.c"); n_pids = kod(N, "src/pids.c")
t_dec = open(os.path.join(KOREN, n_dec)).read()

# --- скремблер -------------------------------------------------------------------
s_s, _, _ = F.odna(r"scrambling sequence using a linear feedback shift register with the following primitive|length scrambling sequence using a linear feedback shift register")
s_r, _, _ = F.odna(r"For each logical channel, the scrambler is reset to state 0111 1111 111 upon receipt of a new transfer")
s_sa, _, _ = A.odna(r"scrambling sequence using a linear feedback shift register with the following primitive|length scrambling sequence using a linear feedback shift register")
assert "val = 0x3ff" in t_dec and "((val >> 9) ^ val) & 1" in t_dec
val = 0x3ff; seq = []
for _ in range(200):
    bit = ((val >> 9) ^ val) & 1; val |= bit << 11; val >>= 1; seq.append(bit)
rek = all(seq[n] == seq[n - 2] ^ seq[n - 11] for n in range(11, 200)) or all(seq[n] == seq[n - 9] ^ seq[n - 11] for n in range(11, 200))
assert rek
ZAPISI.append(Z("HD Radio скремблер логических каналов: P(x) = 1 + x^2 + x^11, сброс в 0111 1111 111 на кадр", "HD Radio / IBOC (NRSC-5-D)", "скремблер (рандомизатор)",
  {"многочлен": "1 + x^2 + x^11 (максимальной длины 2047)", "начало": "0111 1111 111 в начале каждого кадра переноса каждого логического канала (P1…P4, PIDS, S1…S5, SIDS)",
   "первые биты": "".join(map(str, seq[:24]))}, "HD Radio FM и AM (все логические каналы уровня 1)",
  [F.ist(s_s, "8.2 FM"), F.ist(s_r, "начальное состояние"), A.ist(s_sa, "8.1 AM"), ist_kod(n_dec, r"val = 0x3ff", "nrsc5 descramble")],
  "последовательность nrsc5 (регистр 0x3ff) удовлетворяет рекуррентному соотношению многочлена 1+x^2+x^11 на 200 битах", "частично: skrembler.py (аддитивный скремблер x^11 находится; сброс по кадру — через «ПСП блока»)"))

# --- свёрточные коды FM ------------------------------------------------------------
s_92, _, _ = F.odna(r"^Table 9-2: Convolutional Encoder Generator Polynomials – Rate 1/3 Mother Code")
s_93, _, _ = F.odna(r"^Table 9-3: Convolutional Encoder Generator Polynomials – Rate 1/4 Mother Code")
kus92 = F.kusok(r"Table 9-2: Convolutional Encoder Generator Polynomials – Rate 1/3 Mother Code", r"The rate 1/3 convolutional encoder is illustrated", posl=True)[0]
kus93 = F.kusok(r"Table 9-3: Convolutional Encoder Generator Polynomials – Rate 1/4 Mother Code", r"The rate 2/7 convolutional encoder", posl=True)[0]
assert ch(kus92)[-3:] == [133, 171, 165] and ch(kus93)[-4:] == [133, 171, 165, 165], (ch(kus92), ch(kus93))
assert ".gen = { 0133, 0171, 0165 }" in t_dec
kus91, s91 = F.kusok(r"Table 9-1: FM Convolutional Codes", r"9\.3\.4\.1", posl=True)
t91 = kus91.replace("⎥", "").replace("⎦", "").replace("⎤", "").replace("⎢", "").replace("⎣", "").replace("⎡", "")
bl = re.findall(r"(\d/\d)\s*\n((?:[01]\s*\n){6,8})", t91)
pm = {r: "".join(re.findall(r"[01]", b)) for r, b in bl}
n25 = [int(x) for x in re.search(r"const uint8_t puncture\[\] = \{1, 1, 1, 1, 1, 0\}", t_dec).group(0).split("{")[1].rstrip("}").split(",")]
assert pm["2/5"][::-1] == "".join(map(str, n25)) and pm["1/2"][::-1] == "101101", pm
ZAPISI.append(Z("HD Radio FM: свёрточный K=7, материнский 1/3 (133,171,165) и 1/4 (133,171,165,165), выкалывание 2/5, 1/2, 2/7; циклический хвост", "HD Radio / IBOC (NRSC-5-D)", "свёрточный с выкалыванием (циклический хвост)",
  {"материнские": {"1/3": "133, 171, 165", "1/4": "133, 171, 165, 165"}, "выкалывание (g1,g2,g3 по шагам)": {"1/3": "111 111", "2/5": "111 110", "1/2": "101 101 (g2 выкалывается)", "2/7": "1111 1110 (от 1/4)"},
   "хвост": "циклический (tail-biting) — nrsc5 CONV_TERM_TAIL_BITING", "каналы": "P1 (2/5), PIDS (2/5), P3/P4 (1/2), S1…S5, SIDS — по режимам MP1…MP11, MS1…MS4"},
  "HD Radio FM (гибрид/расширенный/полностью цифровой)", [F.ist(s91, "Table 9-1"), F.ist(s_92, "Table 9-2"), F.ist(s_93, "Table 9-3"), ist_kod(n_dec, r"\.gen = \{ 0133, 0171, 0165 \}", "nrsc5")],
  "многочлены из табл. 9-2/9-3 = nrsc5; матрицы выкалывания 2/5 и 1/2 из табл. 9-1 (прочитаны снизу вверх) = массивам puncture nrsc5", "частично: kod.py/svyortka (1/3 K=7 с многочленами 133/171/165 находится; хвост LTE-подобный есть)"))

s_a2, _, _ = A.odna(r"^Table 9-2: E1 Convolutional Encoder Generator Polynomials – Rate 1/3 Mother Code")
s_a3, _, _ = A.odna(r"^Table 9-3: E2 Convolutional Encoder Generator Polynomials")
s_a4, _, _ = A.odna(r"^Table 9-4: E3 Convolutional Encoder Generator Polynomials")
assert ".gen = { 0561, 0657, 0711 }" in t_dec and ".gen = { 0561, 0753, 0711 }" in t_dec
for pat in (r"Table 9-2: E1 Convolutional Encoder Generator Polynomials", r"Table 9-3: E2 Convolutional", r"Table 9-4: E3 Convolutional"):
    kk = A.kusok(pat, r"Puncture|Punctur", posl=True)[0]
    assert est_podposl(kk, [561, 657, 711]) or est_podposl(kk, [561, 753, 711]), ch(kk)
ZAPISI.append(Z("HD Radio AM: свёрточный K=9, E1 (561,657,711) → 5/12, E2/E3 (561,753,711) → 2/3 и др.; циклический хвост", "HD Radio / IBOC (NRSC-5-D)", "свёрточный с выкалыванием (циклический хвост)",
  {"E1": "561, 657, 711 (1/3, выкалывание до 5/12; nrsc5: шаблон 101101101111111 на 15 бит)", "E2": "561, 753, 711 (выкалывание до 2/3; nrsc5: 101100)", "E3": "561, 753, 711", "K": 9},
  "HD Radio AM (гибрид MA1, полностью цифровой MA3)", [A.ist(s_a2, "Table 9-2"), A.ist(s_a3, "Table 9-3"), A.ist(s_a4, "Table 9-4"), ist_kod(n_dec, r"0561, 0657, 0711", "nrsc5")],
  "многочлены из табл. 9-2…9-4 = nrsc5 conv_code_e1/e2_e3", "частично: kod.СВЁРТОЧНЫЕ знает пару 561/753 (K=9, IS-95, скорость 1/2); трёхветвевой код с 711/657 и шаблоны выкалывания AM — нет"))

# --- RS(96,88) и CRC-8 (аудиотранспорт) -----------------------------------------------
s_rs, _, _ = T.odna(r"The actual code word is shortened to a length of 96")
kus_g, _ = T.kusok(r"Generator polynomial is", r"where “a” is a root of the primitive polynomial")
exp = [1]
for _ in range(254):
    v = exp[-1] << 1
    if v & 0x100: v ^= 0x11D
    exp.append(v)
log = {v: i for i, v in enumerate(exp)}
def gm(a, b): return 0 if 0 in (a, b) else exp[(log[a] + log[b]) % 255]
g = [1]
for i in range(1, 9):
    ng = [0] * (len(g) + 1)
    for j, c in enumerate(g):
        ng[j] ^= c; ng[j + 1] ^= gm(c, exp[i])
    g = ng
glog = [log[c] for c in g[1:]]
assert glog == [176, 240, 211, 253, 220, 3, 203, 36], glog
assert est_podposl(kus_g, [7, 176, 6, 240, 5, 211, 4, 253, 3, 220, 2, 3, 203, 36])
t_fr = open(os.path.join(KOREN, n_fr)).read(); assert "init_rs_char(8, 0x11d, 1, 1, 8)" in t_fr
s_c8, _, _ = T.odna(r"This polynomial can be represented in binary form as 100110001 where the LSB is on the right")
ZAPISI.append(Z("HD Radio аудио-PDU: RS(96,88) над GF(256) для заголовка + CRC-8 x^8+x^5+x^4+1 на каждый пакет", "HD Radio / IBOC (NRSC-5-D)", "РС + CRC",
  {"РС": "(96,88) укороченный (255,247), p(x) = x^8+x^4+x^3+x^2+1, g(x) = ∏_{i=1}^{8}(x+a^i) = x^8 + a^176 x^7 + a^240 x^6 + a^211 x^5 + a^253 x^4 + a^220 x^3 + a^3 x^2 + a^203 x + a^36",
   "CRC-8": "g8(x) = x^8+x^5+x^4+1 (100110001, 0x31), младший бит остатка сразу после пакета"},
  "HD Radio аудиотранспорт (MPS/SPS PDU, HDC)", [T.ist(s_rs, "5.2.3.1"), T.ist(s_c8, "5.2.3.2 CRC-8"), ist_kod(n_fr, r"init_rs_char\(8, 0x11d, 1, 1, 8\)", "nrsc5")],
  "g(x) перемножен над GF(256): логарифмы коэффициентов 176,240,211,253,220,3,203,36 совпали с формулой в тексте и с nrsc5 (fcr=1, 8 корней)",
  "частично: rs_bch.py (RS над GF(256) вслепую), crc.py"))

t_p = open(os.path.join(KOREN, n_pids)).read(); assert "uint16_t poly = 0xD010" in t_p and "reg ^= 0x955" in t_p
ZAPISI.append(Z("HD Radio PIDS: CRC-12 (по nrsc5; в открытых документах NRSC-5-D не найдено)", "HD Radio / IBOC (NRSC-5-D)", "CRC-подобный",
  {"по nrsc5": "регистр 16 бит, poly 0xD010 (сдвиг вправо), начальное 0, результат XOR 0x955, 12 младших бит", "примечание": "первоисточник — SY_IDD_1020s (Station Information Service) — в открытом доступе не найден"},
  "HD Radio PIDS (SIS: позывной, координаты станции)", [ist_kod(n_pids, r"uint16_t poly = 0xD010", "nrsc5 crc12")],
  "только открытый код (обратная разработка nrsc5); стандартом не сверено", "нет"))
