"""ATSC 1.0 (A/53 Part 2:2011): рандомизатор, RS(207,187), перемежитель 52 сегмента, 12 решётчатых кодеров 2/3,
синхрополе PN511/PN63; ATSC-M/H (A/153 Part 2:2011): RS-CRC кадр, SCCC 1/2 и 1/4, RS(18,10) TPC, RS(51,37) FIC, PCCC 1/4.
Сверка — gr-dtv (GNU Radio, GPL-3)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

A = Tekst("istochniki/atsc/A53-Part-2-2011.pdf")
M = Tekst("istochniki/atsc/A153-Part-2-2011.pdf")
GR = "gnuradio"; ZAPISI = []
g_rnd = kod(GR, "gr-dtv/lib/atsc/atsc_randomize.cc"); g_rs = kod(GR, "gr-dtv/lib/atsc/atsc_rs_encoder_impl.cc")
g_pn = kod(GR, "gr-dtv/lib/atsc/atsc_pnXXX_impl.h"); g_tr = kod(GR, "gr-dtv/lib/atsc/atsc_basic_trellis_encoder.cc")
g_il = kod(GR, "gr-dtv/lib/atsc/atsc_interleaver_impl.cc")

# --- рандомизатор -------------------------------------------------------------
s_r, _, l_r = A.odna(r"G\(16\) = X16 \+ X13 \+ X12 \+ X11 \+ X7 \+ X6 \+ X3 \+ X \+ 1")
s_ri, _, _ = A.odna(r"The initialization \(pre-load\) to 0xF180")
st = stepeni(l_r.split("=")[1].replace("X", "x"))
assert st == [16, 13, 12, 11, 7, 6, 3, 1, 0]
t_rnd = open(os.path.join(KOREN, g_rnd)).read()
maska = [int(x, 16) for x in re.findall(r"if \(st & (0x[0-9a-fA-F]{4})\)", t_rnd)]
assert maska == [0x8000, 0x2000, 0x1000, 0x0200, 0x0020, 0x0010, 0x0008, 0x0004], maska
ZAPISI.append(Z("ATSC 1.0 рандомизатор данных (ГПСП 16 бит, 0xF180, сброс на поле)", "ATSC 1.0 (A/53)", "скремблер (рандомизатор)",
  {"многочлен": "G(16) = X^16 + X^13 + X^12 + X^11 + X^7 + X^6 + X^3 + X + 1", "начало": "предзагрузка 0xF180 во время сегмента синхронизации первого сегмента данных поля",
   "выход": "8 разрядов D0…D7 (отводы регистра) — байт, XOR с байтом данных; синхробайт MPEG не рандомизируется",
   "примечание": "тот же многочлен и та же предзагрузка — у скремблера ББ-пакетов ATSC 3.0 (см. запись ATSC 3.0) и у M/H-рандомизатора A/153"},
  "ATSC 1.0 8-VSB (эфир), ATSC-M/H (основной поток)", [A.ist(s_r, "5.2.1 G(16)"), A.ist(s_ri, "предзагрузка 0xF180"), ist_kod(g_rnd, r"if \(st & 0x8000\)", "gr-dtv выходные отводы")],
  "многочлен разобран из текста; 8 выходных отводов gr-dtv (0x8000,0x2000,0x1000,0x0200,0x0020,0x0010,0x0008,0x0004) совпадают с картой бит gr-atsc3, проверенной по первым байтам ATSC 3.0 (C0 6D 3F)",
  "частично: skrembler.py — аддитивный скремблер вслепую; сброс по полю ATSC не встроен"))

# --- RS(207,187) ------------------------------------------------------------------
s_rs, _, _ = A.odna(r"transmission subsystem shall be a t = l0 \(207,187\) code")
t_rs = open(os.path.join(KOREN, g_rs)).read()
assert "rs_init_gfpoly = 0x11d" in t_rs and "rs_init_fcr = 0" in t_rs and "rs_init_nroots = 20" in t_rs
ZAPISI.append(Z("ATSC 1.0 RS(207,187) t=10", "ATSC 1.0 (A/53)", "РС",
  {"код": "RS(207,187) — укороченный (255,235), 20 проверочных байт в конце сегмента", "поле": "GF(256), p(x) = x^8+x^4+x^3+x^2+1 (0x11D), первый корень α^0",
   "порядок": "старший бит байта первым"}, "ATSC 1.0 8-VSB, ATSC-M/H основной поток",
  [A.ist(s_rs, "5.2.2"), ist_kod(g_rs, r"rs_init_gfpoly", "gr-dtv: gfpoly 0x11d, fcr 0, nroots 20")],
  "t=10 и (207,187) — из текста; многочлены на рисунке 5.4 (в тексте PDF не читаются) — по gr-dtv: 0x11D, fcr=0, prim=1, 20 корней",
  "есть: rs_bch.py (RS над GF(256) вслепую, укороченные), dvb.py — RS(204,188) с тем же полем"))

# --- перемежитель ----------------------------------------------------------------------
s_il, _, _ = A.odna(r"The interleaver parameters shall be M = 4 and B = 52, resulting in N = 208")
t_il = open(os.path.join(KOREN, g_il)).read()
assert re.search(r"52", t_il) and re.search(r"\b4\b", t_il)
ZAPISI.append(Z("ATSC 1.0 свёрточный байтовый перемежитель B=52, M=4", "ATSC 1.0 (A/53)", "перемежитель",
  {"вид": "Форни, B = 52 ветви, M = 4 байта (ветвь j задерживает на 4·j байт), N = 208", "синхронизация": "к первому байту данных поля; синхробайт сегмента не перемежается",
   "глубина": "1/6 поля (~4 мс)"}, "ATSC 1.0 8-VSB",
  [A.ist(s_il, "5.2.3"), ist_kod(g_il, r"52", "gr-dtv atsc_interleaver")], "параметры из текста; gr-dtv использует те же 52/4",
  "есть: forni.py / peremezhenie (свёрточное перемежение любых параметров, B и M находятся)"))

# --- решётчатый код 2/3 ------------------------------------------------------------------
s_t, _, _ = A.odna(r"A two-thirds rate \(R=2/3\) trellis code is employed")
s_12, _, _ = A.odna(r"^Twelve identical trellis encoders shall be used")
kus52, s_52 = A.kusok(r"Table 5\.2 Byte to Symbol Conversion, Multiplexing of Trellis Encoders", r"=====СТР 1[89]=====", posl=True)
ns = c_massiv(g_tr, "next_state"); os_ = c_massiv(g_tr, "out_symbol")
assert len(ns) == 32 and len(os_) == 32
# проверка: next_state — 4 состояния свёрточного кода, вход x1 (2 бита вход: x2 x1)
ZAPISI.append(Z("ATSC 1.0 решётчатый код 2/3 (4 состояния, Унгербёк) ×12 с внутрисегментным перемежением, 8-VSB", "ATSC 1.0 (A/53)", "свёрточный (решётчатый, ТСМ)",
  {"кодер": "X2 — через прекодер (дифференциальный: Y2 = X2 ⊕ Y2 задержанный на 12 символов), X1 — в систематический 4-состояний кодер скорости 1/2 (Z1, Z0); Z2Z1Z0 → 8 уровней −7…+7",
   "перемежение": "12 одинаковых кодеров, байты по очереди в кодеры 0…11; пары бит (7,6),(5,4),(3,2),(1,0); таблица 5.2 — порядок байт/кодеров по сегментам (повтор через 12 сегментов)",
   "синхронизация": "сегментная синхронизация 4 символа (+5 −5 −5 +5), поле: PN511 + 3×PN63 (средний инвертируется через поле) + 24 символа режима VSB",
   "таблица состояний": {"next_state": ns, "out_symbol": os_}},
  "ATSC 1.0 8-VSB (США, Канада, Мексика, Корея)", [A.ist(s_t, "5.2.4"), A.ist(s_12, "12 кодеров"), A.ist(s_52, "Table 5.2"), ist_kod(g_tr, r"next_state\[32\]", "gr-dtv next_state/out_symbol")],
  "параметры из текста; таблица переходов 4×(2 бита) — gr-dtv", "частично: kod.py/svyortka — свёрточные 1/2; TCM 8-VSB и 12-кратное перемежение — нет"))

# --- PN511 / PN63 -------------------------------------------------------------------------
kus511, s511 = A.kusok(r"X6 \+ X4 \+ X3 \+ X \+ 1 with a pre-load value of ‘010000000’ \(see Figure 5\.10\)\. The resulting\s*sequence is:", r"’")
t511 = re.sub(r"[^01]", "", kus511); assert len(t511) == 511, len(t511)
kus63, s63 = A.kusok(r"pre-load value of ‘100111’ \(see Figure 5\.10\)\. The resulting PN63 sequence is:", r"’\s*\n")
t63 = re.sub(r"[^01]", "", kus63); assert len(t63) == 63, len(t63)
g511 = "".join(map(str, c_massiv(g_pn, "atsc_pn511"))); g63 = "".join(map(str, c_massiv(g_pn, "atsc_pn63")))
assert g511 == t511 and g63 == t63
ok511 = lfsr_poisk([9, 7, 6, 4, 3, 1, 0], "010000000", t511)
ok63 = lfsr_poisk([6, 1, 0], "100111", t63)
assert ok511 and ok63, (ok511, ok63)
ZAPISI.append(Z("ATSC 1.0 синхрополе: PN511 (X^9+X^7+X^6+X^4+X^3+X+1) и PN63 (X^6+X+1)", "ATSC 1.0 (A/53)", "синхропоследовательность (ПСП)",
  {"PN511": "X^9+X^7+X^6+X^4+X^3+X+1, предзагрузка 010000000", "PN63": "X^6+X+1, предзагрузка 100111; три PN63, средняя инвертируется через поле",
   "уровни": "1 → +5, 0 → −5", "последовательности": {"PN511": t511, "PN63": t63}},
  "ATSC 1.0 синхронизация поля (832 символа), эквалайзер",
  [A.ist(s511, "5.3.2.1 PN511"), A.ist(s63, "5.3.2.2 PN63"), ist_kod(g_pn, r"atsc_pn511\[511\]", "gr-dtv atsc_pn511/atsc_pn63")],
  f"последовательности из текста (511 и 63 бит) побитно совпали с gr-dtv; генератор по многочлену и предзагрузке воспроизводит текст (PN511: {ok511}; PN63: {ok63})",
  "нет (как синхрослово ищется общим поиском синхрокомбинаций sinhro.py)"))

# --- ATSC-M/H ----------------------------------------------------------------------------
kus55, s55 = M.kusok(r"Table 5\.5 RS Code Mode", r"Reserved", posl=True)
rsm = re.findall(r"\((\d+),187\)\s*\n(\d+)", kus55)
assert rsm == [("211", "24"), ("223", "36"), ("235", "48")], rsm
s_crc, _, _ = M.odna(r"The RS-CRC encoder then shall add CRC syndrome checksum bytes at the right end")
ZAPISI.append(Z("ATSC-M/H кадр RS-CRC: RS(211/223/235,187) по столбцам + CRC-16 по строкам", "ATSC-M/H (A/153)", "каскадный: РС × CRC (двумерный)",
  {"РС": "RS(211,187) P=24, RS(223,187) P=36, RS(235,187) P=48 над GF(256) — по N столбцам RS-кадра (187 строк данных)",
   "CRC": "2 байта CRC-16 в конце каждой строки (рис. 5.17, многочлен на рисунке — X^16, X^12, X^5, 1 видны в тексте рисунка)",
   "рандомизатор": "M/H: тот же G(16), предзагрузка 0xF180 (рис. 5.16)"},
  "ATSC-M/H (мобильное ТВ поверх 8-VSB)", [M.ist(s55, "Table 5.5"), M.ist(s_crc, "5.3.2.2.1.2 RS-CRC")],
  "коды разобраны из табл. 5.5 с проверкой P = N − 187", "частично: rs_bch.py (РС вслепую), crc.py (CRC вслепую); двумерный кадр M/H — нет"))
kus57, s57 = M.kusok(r"Table 5\.7 SCCC Outer Code Mode", r"Table 5\.8a", posl=True)
assert "1/2 rate" in kus57 and "1/4 rate" in kus57
s_cc, _, _ = M.odna(r"Outer convolutional coding for the SCCC shall be performed by a single 4-state convolutional")
kus511m, s_ex = M.kusok(r"Table 5\.11 Example: Block Length in Symbols = 2112, L = 4096", r"\}mod", posl=True)
pr = re.search(r"P’\(i\)\s+([\d\s]+)…", kus511m); pp = [int(x) for x in pr.group(1).split()]
formula = [(89 * i * (i + 1) // 2) % 4096 for i in range(len(pp))]
assert pp == formula, (pp, formula)
pi = [x for x in ((89 * i * (i + 1) // 2) % 4096 for i in range(4096)) if x < 2112]
pr2 = re.search(r"P\(i\)\s+([\d\s]+)…\s*([\d\s]+)", kus511m); p2 = [int(x) for x in pr2.group(1).split()]; hvost = [int(x) for x in pr2.group(2).split()]
assert pi[:len(p2)] == p2 and pi[-2:] == hvost[:2] and sorted(pi) == list(range(2112)), (pi[:16], p2, pi[-2:], hvost)
ZAPISI.append(Z("ATSC-M/H SCCC: внешний 4-состояний свёрточный 1/2 или 1/4 + символьный перемежитель + решётчатый кодер 8-VSB", "ATSC-M/H (A/153)", "турбо SCCC (последовательный каскад)",
  {"внешний код": "4 состояния, 1 вход → 5 выходов C0…C4; 1/2: (C0,C1); 1/4: (C0,C2),(C1,C4) (табл. 5.10); сброс в 0 перед каждым блоком SCCC",
   "перемежитель": "символьный, B = 4·SOBL; P'(i) = (89·i·(i+1)/2) mod L, L = 2^m ≥ B, значения ≥ B выбрасываются",
   "внутренний код": "решётчатый 2/3 ATSC 1.0 (12 кодеров)", "блоки": "SCB1…SCB10, SOBL/SIBL — табл. 5.8a/5.8b"},
  "ATSC-M/H", [M.ist(s57, "Table 5.7"), M.ist(s_cc, "5.3.2.3.3"), M.ist(s_ex, "Table 5.11 пример P(i)")],
  "формула перемежителя проверена на примере табл. 5.11 (B = 2112, L = 4096): все показанные P'(i) и P(i), включая последние P(2110), P(2111), совпали; результат — перестановка 0…2111",
  "нет"))
s_tpc, _, _ = M.odna(r"An \(N = 18, K = 10\) RS code having an error correction capability t = 4")
s_fic, _, _ = M.odna(r"An \(N = 51, K = 37\) RS code having an error correction capability t = 7")
s_pc, _, _ = M.odna(r"A 1/4 rate PCCC \(Parallel Concatenated Convolutional Code\) Encoder shall be employed")
s_bi, _, _ = M.odna(r"The bit interleaver Block length shall be 552 \(= 69 x 8\) bits")
ZAPISI.append(Z("ATSC-M/H сигнализация: RS(18,10) TPC + RS(51,37) FIC + блочный перемежитель 51×TNoG + PCCC 1/4", "ATSC-M/H (A/153)", "каскадный: РС + турбо PCCC",
  {"TPC": "RS(18,10) t=4 над GF(256) (поле как у RS-кадра)", "FIC": "RS(51,37) t=7, перемежитель 51 столбец × TNoG строк",
   "PCCC": "1/4: 6 чётных + 6 нечётных компонентных кодеров, битовый перемежитель 552 бит по той же формуле (89·i·(i+1)/2 mod 1024), сброс в каждом слоте"},
  "ATSC-M/H каналы сигнализации", [M.ist(s_tpc, "5.3.2.4.1"), M.ist(s_fic, "5.3.2.4.2"), M.ist(s_pc, "5.3.2.4.6"), M.ist(s_bi, "5.3.2.4.6.2")],
  "параметры из текста", "частично: rs_bch.py (РС); PCCC ATSC-M/H — нет"))
