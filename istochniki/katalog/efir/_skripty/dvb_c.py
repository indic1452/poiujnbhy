"""Кабельное цифровое ТВ: DVB-C (EN 300 429 = J.83 Annex A) и J.83 Annex B (SCTE 07, США): RS(128,122) над GF(128),
перемежитель (I,J), рандомизатор GF(128), TCM с BCC 25/37 и выкалыванием 4/5, синхрохвост кадра, контрольная сумма MPEG.
Сверка — gr-dtv catv (GNU Radio, GPL-3) и вычислением."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

C = Tekst("istochniki/dvb_c/en_300429v010201p.pdf"); J = Tekst("istochniki/itu/T-REC-J.83-200712-I.pdf")
GR = "gnuradio"; ZAPISI = []
g_rs = kod(GR, "gr-dtv/lib/catv/catv_reed_solomon_enc_bb_impl.cc"); g_rnd = kod(GR, "gr-dtv/lib/catv/catv_randomizer_bb_impl.cc")
g_fs = kod(GR, "gr-dtv/lib/catv/catv_frame_sync_enc_bb_impl.cc"); g_tr = kod(GR, "gr-dtv/lib/catv/catv_trellis_enc_bb_impl.cc")
g_tf = kod(GR, "gr-dtv/lib/catv/catv_transport_framing_enc_bb_impl.cc")

s_i, _, _ = C.odna(r"This unit shall perform a depth I = 12 convolutional interleaving")
s_d, _, _ = C.odna(r"In order to get a rotation-invariant constellation, this unit shall apply a differential encoding of the two Most Significant")
ZAPISI.append(Z("DVB-C (J.83 Annex A/C): RS(204,188) + Форни I=12 + дифференциальное кодирование 2 старших бит, 16…256-QAM", "DVB-C (EN 300 429)", "каскадный: РС + перемежитель (без внутреннего кода)",
  {"рандомизатор": "как DVB-T/S (1+X^14+X^15, 100101010000000, раз в 8 пакетов, 47→B8)", "РС": "(204,188) t=8, GF(256) 0x11D", "перемежитель": "Форни I = 12, M = 17",
   "модуляция": "16/32/64/128/256-QAM; 2 старших бита символа (Ak, Bk) — дифференциально (поворотная инвариантность)", "Annex C": "та же схема (Япония), другой скат фильтра"},
  "DVB-C (Европа, СНГ), J.83 Annex A/C, ISDB-C", [C.ist(s_i, "4.? перемежитель I=12"), C.ist(s_d, "дифференциальное кодирование")],
  "параметры из текста; внешний код совпадает с записью DVB-T (проверен там)", "есть: dvb.py (RS(204,188), Форни I=12, рандомизация); дифференциальное кодирование КАМ — moddekoder"))

# --- J.83B RS(128,122) над GF(128) ---------------------------------------------------------
s_rs, _, _ = J.odna(r"utilized to implement a t = 3, \(128,122\) extended Reed-Solomon code over GF\(128\)")
t_rs = open(os.path.join(KOREN, g_rs)).read()
assert "x = (x & 0x7F) ^ 0x09" in t_rs            # p(x) = x^7 + x^3 + 1
m = re.search(r"g\[\] = \{\s*1, gf_exp\[(\d+)\], gf_exp\[(\d+)\], gf_exp\[(\d+)\], gf_exp\[(\d+)\], gf_exp\[(\d+)\]", t_rs)
glog = [int(x) for x in m.groups()]
# вычислим (x+α)(x+α^2)…(x+α^5) над GF(128), p = x^7+x^3+1
exp = [1]
for _ in range(126):
    v = exp[-1] << 1
    if v & 0x80: v ^= 0x89
    exp.append(v)
log = {v: i for i, v in enumerate(exp)}
def gmul(a, b): return 0 if a == 0 or b == 0 else exp[(log[a] + log[b]) % 127]
g = [1]
for i in range(1, 6):
    ng = [0] * (len(g) + 1)
    for j, c in enumerate(g):
        ng[j] ^= c; ng[j + 1] ^= gmul(c, exp[i])
    g = ng
assert [log[c] for c in g[1:]] == glog == [52, 116, 119, 61, 15], ([log[c] for c in g[1:]], glog)
kus_eq, s_eq = J.kusok(r"The generator polynomial used by the encoder is:", r"B\.5\.2", posl=True)
assert est_podposl(kus_eq, [15, 61, 2, 119]) or all(str(x) in kus_eq for x in (15, 61, 119))
ZAPISI.append(Z("J.83 Annex B RS(128,122) t=3 расширенный над GF(128)", "J.83 Annex B / SCTE 07 (кабель США)", "РС",
  {"поле": "GF(128), p(x) = x^7 + x^3 + 1", "g(x)": "(x+α)(x+α^2)(x+α^3)(x+α^4)(x+α^5) = x^5 + α^52 x^4 + α^116 x^3 + α^119 x^2 + α^61 x + α^15",
   "расширение": "128-й символ — проверка c(α^6) (расширенный код)", "символ": "7 бит, старший первым"},
  "J.83B (64/256-QAM кабельное ТВ и DOCSIS 1.x/2.0/3.0 нисходящий)", [J.ist(s_rs, "B.5.1"), J.ist(s_eq, "g(x)"), ist_kod(g_rs, r"gf_exp\[52\]", "gr-dtv")],
  "g(x) перемножен над GF(128): логарифмы коэффициентов 52,116,119,61,15 совпали с gr-dtv; в тексте формулы видны степени 15, 61, 119", "частично: rs_bch.py (РС вслепую — нужен GF(128))"))

# --- перемежитель, синхрохвост, рандомизатор -------------------------------------------------------------
s_ij, _, _ = J.odna(r"\(I,J\) = \(128,1\), \(64,2\), \(32,4\), \(16,8\), \(8,16\)")
s_f64, _, _ = J.odna(r"symbols of the frame sync trailer contain the 28-bit \"unique\" synchronization pattern")
s_f256, _, _ = J.odna(r"trailer is divided as follows: 32 bits are the \"unique\" synchronization pattern")
t_fs = open(os.path.join(KOREN, g_fs)).read()
b = [int(x, 16) for x in re.findall(r"b = (0x[0-9a-f]{2});", t_fs)]
assert b == [0x75, 0x2C, 0x0D, 0x6C, 0x71, 0xE8, 0x4D, 0xD4], b
t64 = "1110101 0101100 0001101 1101100".split(); assert [int(x, 2) for x in t64] == b[:4]
assert int("01110001111010000100110111010100", 2) == int.from_bytes(bytes(b[4:]), "big")
s_rnd, _, _ = J.odna(r"Initialization is defined as pre-loading to the 'all ones' state for the randomizer")
t_rnd = open(os.path.join(KOREN, g_rnd)).read()
assert "c2 = 0x7F, c1 = 0x7F, c0 = 0x7F" in t_rnd
ZAPISI.append(Z("J.83 Annex B кадр FEC: синхрохвост (75 2C 0D 6C / 71 E8 4D D4), перемежитель (I,J), рандомизатор GF(128) x^3+x+α^3", "J.83 Annex B / SCTE 07 (кабель США)", "кадр/синхрослово + перемежитель + скремблер",
  {"кадр 64-QAM": "60 блоков RS × 128 символов + 42 бита хвоста: 28 бит 1110101 0101100 0001101 1101100 (75 2C 0D 6C), 4 бита режима перемежителя, 10 резерв",
   "кадр 256-QAM": "88 блоков RS + 40 бит: 32 бита 0111 0001 1110 1000 0100 1101 1101 0100 (71 E8 4D D4), 4 бита режима, 4 резерв",
   "перемежитель": "свёрточный по 7-битным символам: (I,J) = (128,1),(64,2),(32,4),(16,8),(8,16), расширенный I=128, J=1…8; коммутатор сбрасывается в начале кадра",
   "рандомизатор": "аддитивная ПСП 7-битных символов, f(x) = x^3 + x + α^3 над GF(128), предзагрузка «все единицы» в хвосте кадра, хвост не рандомизируется"},
  "J.83B, DOCSIS нисходящий", [J.ist(s_f64, "B.5.3 64-QAM"), J.ist(s_f256, "256-QAM"), J.ist(s_ij, "B.5.2 (I,J)"), J.ist(s_rnd, "B.5.4"), ist_kod(g_fs, r"b = 0x75", "gr-dtv"), ist_kod(g_rnd, r"0x7F", "gr-dtv")],
  "двоичные записи синхрослов в тексте = шестнадцатеричным и = байтам gr-dtv; начальное «все единицы» = 0x7F×3 в gr-dtv", "частично: sinhro.py находит синхрослово; рандомизатор над GF(128) — нет"))

# --- TCM ------------------------------------------------------------------------------------------------------------------
s_g, _, _ = J.odna(r"16-state non-systematic rate 1/2 encoder with the generator: G1 = 010 101, G2 = 011 111")
s_p, _, _ = J.odna(r"a puncture matrix: \[P1, P2\] = \[0001;1111\]")
s_o, _, _ = J.odna(r"3\) Generating code: G1 = \[010101\], G2 = \[011111\] \(25,37octal\)")
t_tr = open(os.path.join(KOREN, g_tr)).read()
assert "G1table[i] = (i >> 4) ^ ((i & 0x04) >> 2) ^ (i & 1);" in t_tr   # 1 0 1 0 1 = 25₈
assert re.search(r"G2table\[i\] = \(i >> 4\) \^ \(\(i & 0x08\) >> 3\) \^ \(\(i & 0x04\) >> 2\) \^\s*\(\(i & 0x02\) >> 1\) \^ \(i & 1\);", t_tr)  # 11111 = 37₈
assert dfree([0o25, 0o37], 5) == dfree_vyk([25, 37], 5) == 6 and dfree_vyk([25, 37], 5, ["0001", "1111"]) == 3
ZAPISI.append(Z("J.83 Annex B TCM: BCC 16 состояний 25/37 с выкалыванием 4/5, 64-QAM 14/15, 256-QAM 19/20", "J.83 Annex B / SCTE 07 (кабель США)", "свёрточный с выкалыванием (TCM)",
  {"BCC": "K=5, G1 = 25₈ (010101), G2 = 37₈ (011111), выкалывание [P1;P2] = [0001;1111] → 4 бита → 5 бит", "дифференциальный прекодер": "перед BCC (поворотная инвариантность 90°)",
   "64-QAM": "группа 28 бит (4 символа RS) → 5 символов КАМ, скорость 14/15", "256-QAM": "группа 38 бит → 5 символов, 19/20; 5 синхрогрупп в конце кадра",
   "dfree (двоичный код, своя программа)": {"материнский 1/2 (25,37)": dfree_vyk([25, 37], 5), "после выкалывания 4/5 [0001;1111]": dfree_vyk([25, 37], 5, ["0001", "1111"])}},
  "J.83B, DOCSIS нисходящий", [J.ist(s_g, "B.5.5 G1/G2"), J.ist(s_p, "выкалывание"), J.ist(s_o, "сводка"), ist_kod(g_tr, r"G1table\[i\] =", "gr-dtv")],
  "многочлены 25/37 из текста совпали с таблицами отводов G1/G2 gr-dtv; dfree посчитан двумя независимыми программами (6 для материнского, 3 после выкалывания 4/5; евклидово расстояние TCM обеспечивается ещё и разбиением КАМ)", "частично: kod.py/vykalyvanie.py — свёрточные с выкалыванием (многочлены 25/37 находятся вслепую); TCM J.83B — нет"))

# --- контрольная сумма MPEG --------------------------------------------------------------------------------------------------
s_cs, _, _ = J.odna(r"The syndrome is computed by passing the 1496 payload bits through a Linear Feedback Shift")
s_67, _, _ = J.odna(r"An offset of 67HEX is added to this checksum result")
t_tf = open(os.path.join(KOREN, g_tf)).read()
tg = int(re.search(r"tapsG = (0x[0-9A-F]+)", t_tf).group(1), 16); tb = int(re.search(r"tapsB = (0x[0-9A-F]+)", t_tf).group(1), 16)
gx = [0, 1, 5, 6, 8]; bx = [0, 1, 3, 7]
# gr-dtv хранит взаимные многочлены без старшего члена, бит (7−e) / (6−e)
assert sum(1 << (7 - (8 - e)) for e in gx if e != 0) == tg, hex(tg)
assert sum(1 << (6 - (7 - e)) for e in bx if e != 0) == tb, hex(tb)
assert "result = 0x67" in t_tf
ZAPISI.append(Z("J.83 Annex B контрольная сумма пакета MPEG вместо синхробайта (FIR-код, смещение 0x67)", "J.83 Annex B / SCTE 07 (кабель США)", "CRC-подобный (смежный класс линейного кода)",
  {"синдром": "f(x) = [1 + x^1497 b(x)] / g(x) по 1496 битам полезной нагрузки", "g(x)": "1 + x + x^5 + x^6 + x^8", "b(x)": "1 + x + x^3 + x^7",
   "смещение": "+0x67 → при верном слове синдромный декодер даёт 0x47", "место": "вместо синхробайта 0x47 предыдущего пакета"},
  "J.83B транспортный уровень", [J.ist(s_cs, "B.4 синдром"), J.ist(s_67, "смещение 67h"), ist_kod(g_tf, r"tapsG = 0xB1", "gr-dtv compute_sum")],
  "g(x), b(x) из текста совпали с отводами gr-dtv 0xB1/0x45 (взаимные многочлены); смещение 0x67 — в обоих", "нет"))
