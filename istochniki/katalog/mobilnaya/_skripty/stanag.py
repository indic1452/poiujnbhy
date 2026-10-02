"""STANAG (НАТО): 4285 (+4481 морское вещание) — 8PSK 2400 Бод, свёрточный K=7 прил. E; 5066 прил. G (высокоскоростной ВЧ модем 3200–9600:
K=7 1/2 → 3/4 выкалыванием 111001, свёрточный перемежитель 48 строк, скремблер x9+x4+1, преамбула); 5066 D_PDU (синхро Маури — Стайлза,
CRC-16 с тестовым вектором; CRC-16/32 открытой реализации); 5065 (НЧ MSK 300 бит/с, код Вагнера (13,12)).
STANAG 4539 = MIL-STD-188-110 прил. C, STANAG 4538 = MIL-STD-188-141 прил. C — см. записи mil110/mil141."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
S = Tekst("istochniki/mil/STANAG_5066_v2.pdf")
# --- 5066 прил. G ----------------------------------------------------------------------------------------------------------------
s_k7 = S.odna(r"The constraint length 7, rate 1/2 convolutional codec employed is the same as is used")[0]
kus, _ = S.kusok(r"The two generator polynomials used are", r"The two summing nodes")
kus = re.sub(r"\s+", " ", kus)
m1 = re.search(r"T1 = (.*?) T2 = (.*)$", kus.strip())
def stepeni(t):
    t = t.replace(" ", "")
    st = set(int(x) for x in re.findall(r"x(\d+)", t))
    if re.search(r"\+x\+|\+x$", t) or re.search(r"(?<!\d)x\+", t): st.add(1)
    if re.search(r"\+1$", t): st.add(0)
    return st
# в тексте степени разнесены по строкам: «x 6 + x 4 + x 3 + x + 1»
t1 = m1.group(1).replace("x ", "x"); t2 = m1.group(2).replace("x ", "x")
st1, st2 = stepeni(t1), stepeni(t2)
assert st1 == {6, 4, 3, 1, 0} and st2 == {6, 5, 4, 3, 0}, (st1, st2)
v8 = lambda st: oct(int("".join("1" if (6 - i) in st else "0" for i in range(7)), 2))
assert (v8(st1), v8(st2)) == ("0o133", "0o171")                                    # = NASA/CCSDS 133/171 (T1 первым)
s_pm = S.odna(r"puncturing mask of 1 1 1 0 0 1, applied to the bits output")[0]
MASKA = [1, 1, 1, 0, 0, 1]
kus, s_ci = S.kusok(r"The commutator row sequence for inputting bits to the interleaver is:", r"where the sequence has been generated")
VHOD = [int(x) for x in re.findall(r"\d+", kus)]
assert VHOD == [(19 * i) % 48 for i in range(48)]
kus, s_co = S.kusok(r"with\s+the\s+selected\s+puncturing\s+to\s+rate\s+3/4,\s+the\s+commutator\s+row\s+sequence\s+for\s+outputting\s+bits", r"The sequence above is repeated")
VYHOD = [int(x) for x in re.findall(r"\d+", kus.split("is:")[-1])]
assert VYHOD == [r for r in range(48) if MASKA[r % 6]] and len(VYHOD) == 32          # строки, сохранённые маской 111001
kus, s_j = S.kusok(r"Table G-6\. Delay increment .j. for each successive row\.", r"If the rate 1/2 code was used")
J = {int(a): (int(b), int(c)) for a, b, c in re.findall(r"(\d{4}) bps\s+(\d+)\s+(\d+)", kus)}
assert J == {3200: (2, 30), 4800: (3, 45), 6400: (4, 60), 9600: (6, 90)}, J
s_scr = S.odna(r"In all cases, the scrambling sequence generator polynomial is x")[0]
def pn9(n):                                                                           # x9+x4+1, начальное «1»
    r = [0] * 8 + [1]; out = []
    for _ in range(n):
        out.append(r[-1]); fb = r[-1] ^ r[3]; r = [fb] + r[:-1]
    return out
seq = pn9(1022); assert seq[:511] == seq[511:] and len(set(tuple(seq[i:i + 9]) for i in range(511))) == 511   # период 511 (m-последовательность)
kus, s_pr = S.kusok(r"using the symbol numbers given in Table\s*G-1, the synchronization preamble is:", r"where the data symbols D0, D1, and D2")
tok = re.findall(r"D[012]|\d", kus)
assert len(tok) == 200, len(tok)
s_d = S.odna(r"^Short \(0\.72 s\)")[0]; s_fr = S.odna(r"An initial 200 symbol preamble is followed by 72 blocks of alternating data and")[0]
pt = tablica("stanag5066G_peremezhitel_preambula", {"vhod_stroki_19i_mod48": VHOD, "vyhod_stroki_maska_111001": VYHOD, "j_kor_dlin": {str(k): v for k, v in J.items()},
             "preambula_200_8PSK": tok, "D0D1D2": {"net": [4, 0, 0], "korotkiy_0.72s": [2, 4, 2], "dlinnyy_10.8s": [6, 2, 4]}},
             "STANAG 5066 прил. G: порядок строк коммутаторов свёрточного перемежителя (48 строк), приращения задержки j, преамбула 200 символов 8PSK",
             [S.ist(s_ci, "вход"), S.ist(s_co, "выход"), S.ist(s_j, "Table G-6"), S.ist(s_pr, "G.4.1 преамбула")],
             "вход = 19·i mod 48 (как сказано в тексте); выход = строки, оставленные маской 111001; преамбула — 200 знаков, D0–D2 из табл. G-5")
rec("STANAG 5066 прил. G (ВЧ модем 3200–9600 бит/с): свёрточный K=7 → 3/4 + свёрточный перемежитель 48 строк + скремблер x9+x4+1", "НАТО: STANAG 5066 прил. G / 4285 прил. E", "каскадный: свёрточный с выкалыванием + свёрточный перемежитель + скремблер",
    {"свёрточный": "K=7 1/2: T1 = x6+x4+x3+x+1 (133₈), T2 = x6+x5+x4+x3+1 (171₈), T1 первым — «тот же, что в прил. E STANAG 4285»", "выкалывание": "маска 1 1 1 0 0 1 (T1k,T2k,T1k+1,T2k+2) → 3/4, на выходе перемежителя",
     "перемежитель": "свёрточный, 48 строк (используются 32), приращение j: 3200 — 2/30, 4800 — 3/45, 6400 — 4/60, 9600 — 6/90 (корот. 0,72 с / длин. 10,8 с); вход по строкам 19·i mod 48; таблица " + pt,
     "скремблер": "x9+x4+1, начальное 1 в начале каждого блока данных, период 511; 8PSK — сложение по мод 8 последних 3 бит, 16/64QAM — XOR 4/6 бит",
     "кадр": "преамбула 200 символов (97 синхро + 103 с признаком перемежителя D0–D2) + 72 × (256 данных + 31 зонд), затем 72 повторных символа преамбулы",
     "модуляция": "8PSK/16QAM/64QAM, 2400 Бод, 1800 Гц"},
    "ВЧ связь НАТО (морская), предшественник STANAG 4539", [S.ist(s_k7, "G.5.1"), S.ist(s_pm, "G.5.2 маска"), S.ist(s_ci, "G.5.3 вход"), S.ist(s_j, "Table G-6"), S.ist(s_scr, "G.2.3 скремблер"), S.ist(s_fr, "G.3.0 кадр"), S.ist(s_pr, "G.4.1 преамбула")],
    "степени T1/T2 из текста = 133/171₈; вход коммутатора = 19·i mod 48; выход = маска 111001; табл. G-6 разобрана; x9+x4+1 даёт m-последовательность периода 511",
    "частично: свёрточный K=7 133/171 и выкалывание есть (kod.py, vykalyvanie.py); свёрточный перемежитель — peremezhenie.py (перестановка коммутатора добавить); модем — нет")
F = Tekst("istochniki/mil/STANAG_4481_sigidwiki.pdf")
s_4481 = F.odna(r"Although Annex E of STANAG 4285 is marked .for information only")[0]
J85 = Tekst("istochniki/mil/HF_modems_explained_stanag_Jorgenson.pdf"); s_80 = J85.odna(r"ple, specifies an 80 symbol preamble")[0]
MT = Tekst("istochniki/mil/MT_2009_Demystifying_STANAG4285.pdf"); s_mt = MT.odna(r"1800-hertz \(Hz\) tone")[0]
rec("STANAG 4285 (и 4481 морское вещание): 8PSK 2400 Бод, кадр 256 символов с 80-символьной синхро, свёрточный K=7 (прил. E) + перемежитель", "НАТО: STANAG 4285 / 4481", "каскадный: свёрточный + перемежитель",
    {"свёрточный": "K=7, многочлены как в 5066 прил. G (133/171₈) — прил. E STANAG 4285 (для 4481 обязательна)", "кадр": "256 символов: 80 синхро + 4 × (32 данных + 16 зонд) (106,67 мс)",
     "скорости": "75–2400 бит/с кодированные, 1200–3600 некодированные; перемежитель короткий/длинный (0,853 / 10,24 с)", "модуляция": "2/4/8PSK на 1800 Гц, 2400 Бод"},
    "ВЧ связь НАТО, морское вещание (4481)", [F.ist(s_4481, "4481 п.4(b) — прил. E"), J85.ist(s_80, "80 символов"), MT.ist(s_mt, "8PSK 1800 Гц"), S.ist(s_k7, "«тот же, что прил. E 4285»")],
    "сам текст STANAG 4285 в открытом доступе не найден (только обзоры); многочлены — через 5066 прил. G; раскладка 32/16 — по обзорам (второй источник), таблица перемежителя прил. E неизвестна",
    "частично (свёрточный K=7 есть); кадр и перемежитель — нет (средне, нужен первоисточник)")
s_ms = S.odna(r"16 bit sequence shall be the 16-bit Maury-Styles sequence shown below")[0]
s_crc = S.odna(r"using the polynomial x16 \+ x12 \+ x5 \+ 1, in the manner described in CCITT V.41 part 2")[0]
s_vec = S.odna(r"^0x90, 0xEB, 0xF0, 0x00, 0x00, 0x47, 0x05, 0x64, 0x02, 0x5D, 0xCE")[0]
pdu = [0x90, 0xEB, 0xF0, 0x00, 0x00, 0x47, 0x05, 0x64, 0x02, 0x5D, 0xCE]
assert int("1110101110010000", 2) == 0xEB90 and pdu[:2] == [0x90, 0xEB]              # младший байт первым
def crc_r(d, p):
    r = 0
    for b in d:
        for i in range(8):
            bit = (r & 1) ^ ((b >> i) & 1); r >>= 1
            if bit: r ^= p
    return r
assert crc_r(pdu[2:9], 0x8408) == 0xCE5D and pdu[9:] == [0x5D, 0xCE]
o5 = kod("open5066", "dts.c")
t5 = open(os.path.join(KOREN, o5)).read(); assert int(format(0x9299, "016b")[::-1], 2) == 0x9949 == sum(1 << s for s in (15, 12, 11, 8, 6, 3, 0))
assert "CRC ^= 0x9299;" in t5 and "CRC ^= 0xf3a4e550;" in t5
rec("STANAG 5066 D_PDU: синхро Маури — Стайлза 0xEB90 + CRC-16 заголовка (+ CRC-32 данных)", "НАТО: STANAG 5066", "CRC-подобный + синхрослово",
    {"синхро": "16 бит 1110101110010000 (0xEB90), младшим битом вперёд → байты 90 EB", "CRC заголовка": "V1.0.2: x16+x12+x5+1 (отражённый 0x8408, начальное 0), без синхробайт; байты CRC младшим вперёд",
     "поздние редакции (open5066)": "CRC-16 отражённый 0x9299 = прямой 0x9949 (x16+x15+x12+x11+x8+x6+x3+1), CRC-32 данных отражённый 0xF3A4E550 — различие редакций",
     "порядок бит": "V.42 8.1.2.2 — младший первым"},
    "ВЧ передача данных НАТО (ARQ поверх STANAG 4285/4539/110)", [S.ist(s_ms, "C.3.1 синхро"), S.ist(s_crc, "C.3.1.5 CRC"), S.ist(s_vec, "пример D_PDU"), ist_kod(o5, r"CRC \^= 0x9299", "open5066 CRC-16"), ist_kod(o5, r"0xf3a4e550", "open5066 CRC-32")],
    "тестовый вектор V1.0.2: CRC(F0 00 00 47 05 64 02) = 0xCE5D — воспроизведён; open5066 (редакция 1.2+) использует другой многочлен — это же сообщение с 0x9299 даёт 0x" + format(crc_r(pdu[2:9], 0x9299), "04X") + " (отмечено как различие)",
    "нет разбора 5066; синхрослово и CRC — общими средствами (просто)")
N = Tekst("istochniki/mil/STANAG_5065_sigidwiki.pdf", ocr=True)
s_w = N.odna(r"transmit site shall encode encrypted data into a \(13,12\) Wagner code")[0]; s_w2 = N.odna(r"\(13,12\) Wagner odd parity code block")[0]; s_msk = N.odna(r"300 bps using MSK modulation")[0]
rec("STANAG 5065 (НЧ вещание 50–160 кГц): код Вагнера (13,12) с нечётной чётностью, MSK 300 бит/с", "НАТО: STANAG 5065", "код чётности (Вагнер, мягкое декодирование)",
    {"код": "(13,12): два знака ITA-2 (7-элементный старт-стоп, после KW-46: 6 шифрованных + бит Фибоначчи), каждый второй бит Фибоначчи заменён битом нечётной чётности",
     "декодирование": "при нарушении чётности инвертируется наименее надёжный бит, затем бит Фибоначчи восстанавливается", "модуляция": "MSK 300 бит/с (975 Гц основная полоса); FSK 75 бит/с — без кода"},
    "НЧ вещание на подводные лодки НАТО", [N.ist(s_w, "C.5"), N.ist(s_w2, "C.8"), N.ist(s_msk, "MSK 300")], "по тексту (OCR) STANAG 5065 изд. 1", "нет в проекте; тривиально (чётность)")
