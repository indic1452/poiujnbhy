"""DTMB / GB 20600-2006 (стандарт в открытом доступе не найден): по описанию Keysight ADS DTMB Design Library (2011)
и открытому коду dtmb-sdr (MIT): скремблер 1+x^14+x^15, БЧХ(762,752), LDPC 7488 (0,4/0,6/0,8), свёрточный перемежитель
B=52, заголовки кадров PN420/PN595/PN945, системная информация (32,6)."""
import re, sys, os, json, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

K = Tekst("istochniki/dtmb/keysight-ads2011-dtmb.pdf"); ZAPISI = []
fec = kod("dtmb-sdr", "core/cpp/src/fec.cpp"); t_f = open(os.path.join(KOREN, fec)).read()
s_s, _, _ = K.odna(r"The polynomial is G\(x\) =1\+x14\+x15")
s_r, _, _ = K.odna(r"LFSR is reset at the beginning of each signal frame")
assert "kScramblerInitialState = 0x00A9U" in t_f and "((scrambler >> 13U) ^ (scrambler >> 14U)) & 1U" in t_f
assert format(0xA9, "015b")[::-1] == "100101010000000"
ZAPISI.append(Z("DTMB скремблер TS: 1 + x^14 + x^15, начальное 100101010000000, сброс на сигнальный кадр", "DTMB (GB 20600-2006)", "скремблер (рандомизатор)",
  {"многочлен": "G(x) = 1 + x^14 + x^15", "начальное": "100101010000000 (dtmb-sdr: 0x00A9 = та же загрузка в обратном порядке разрядов)", "сброс": "в начале каждого сигнального кадра"},
  "DTMB/CTTB (цифровое ТВ КНР, Гонконг, Макао, Куба, Лаос…)", [K.ist(s_s, "DTMB_Scrambler"), K.ist(s_r, "сброс"), ist_kod(fec, r"kScramblerInitialState", "dtmb-sdr")],
  "многочлен — Keysight (вторичный источник); начальное состояние — dtmb-sdr, совпадает с DVB (запись DVB-T); стандарт GB 20600 не найден", "есть: dvb.py (та же ПСП), сброс по кадру — через «ПСП блока»"))

s_b, _, _ = K.odna(r"BCH\(762, 752\) is obtained by shortening BCH\(1023, 1013\) system code\. 261 bits 0 are")
assert "(1U << kBchParityBits) | (1U << 3U) | 1U" in t_f
g = mnogochlen([10, 3, 0])
def por(p):
    m = p.bit_length() - 1; v = 2; k = 1
    while v != 1:
        v <<= 1
        if v >> m & 1: v ^= p
        k += 1
    return k
assert por(g) == 1023
ZAPISI.append(Z("DTMB внешний БЧХ(762,752): укороченный БЧХ(1023,1013), g(x) = x^10 + x^3 + 1", "DTMB (GB 20600-2006)", "БЧХ",
  {"код": "(762,752), укорочение на 261 нулевой бит впереди, исправление 1 ошибки", "g(x)": "x^10 + x^3 + 1 (по dtmb-sdr; в Keysight — рисунок)", "расположение": "4 блока БЧХ = 3008 бит → информационная часть LDPC 3048/4572/6096 бит (ставка 0,4: 4×752)"},
  "DTMB", [K.ist(s_b, "DTMB_BCH_Encoder"), ist_kod(fec, r"\(1U << kBchParityBits\) \| \(1U << 3U\) \| 1U", "dtmb-sdr BCH")],
  "g(x) из dtmb-sdr примитивен (порядок x = 1023) — корректен для БЧХ(1023,1013) t=1; сверка с первоисточником GB 20600 невозможна (недоступен)", "частично: rs_bch.py (БЧХ вслепую)"))

s_l, _, _ = K.odna(r"FEC code rate \(BCH: 752/762, LDPC Erased: 3048/7488, 4572/7488")
PRO = json.load(open("/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_dtmb.json"))
ZAPISI.append(Z("DTMB LDPC (7493 → 7488 после удаления 5 проверочных бит): 3048, 4572, 6096 информационных (0,4 / 0,6 / 0,8)", "DTMB (GB 20600-2006)", "LDPC",
  {"код": "квазициклический, Z = 127; из 7493 бит первые 5 проверочных не передаются", "в проекте": list(PRO)},
  "DTMB", [K.ist(s_l, "параметры FEC"), ist_kod(fec, r"LDPC", "dtmb-sdr")], "матрицы — alist dtmb-sdr (MIT), разобраны и проверены (ранг) в проекте; первоисточник GB 20600 недоступен",
  "есть: data/ldpc_dtmb.json (dtmb-7488-*), ldpc_kitay.py"))

s_i, _, _ = K.odna(r"interleave mode of symbol interleaving \(Mode1: B=52, M=240;")
ZAPISI.append(Z("DTMB свёрточный перемежитель символов: B = 52, M = 240 (режим 1) / 720 (режим 2); частотный — для C=3780", "DTMB (GB 20600-2006)", "перемежитель",
  {"временной": "Форни по символам КАМ, B = 52 ветви, M = 240 или 720", "частотный": "блочный внутри тела кадра 3780 (только режим C=3780)"},
  "DTMB", [K.ist(s_i, "параметры")], "по описанию Keysight", "есть: forni.py (свёрточное перемежение любых B, M — на уровне символов потребуется снятие отображения)"))

kus_si, s_si = K.kusok(r"index system information vector", r"The 4 bits system information is")
vek = re.findall(r"\b([01]{32})\b", kus_si)
assert len(vek) == 12
vsе = [int(v, 2) for v in vek] + [int(v, 2) ^ 0xFFFFFFFF for v in vek]
dist = min(bin(a ^ b).count("1") for a, b in itertools.combinations(vsе, 2))
assert dist >= 16
s_ph, _, _ = K.odna(r"Frame head mode 1 uses cyclic extended order 8 m sequence generator implemented by a")
s_p2, _, _ = K.odna(r"Frame head mode 2 uses the first 595 symbols of an order 10 pseudo random sequence")
s_p3, _, _ = K.odna(r"Frame head mode 3 uses cyclic extended order 9 m sequence generator implemented by a")
ZAPISI.append(Z("DTMB заголовки кадров PN420/PN595/PN945 и системная информация 36 бит (32 бита — биортогональный код, 4 бита — режим C)", "DTMB (GB 20600-2006)", "синхропоследовательность + блочный код",
  {"PN420": "m-последовательность 8-го порядка (PN255) + циклические префикс 82 и суффикс 83; 225 кадров в суперкадре с разными начальными фазами",
   "PN595": "первые 595 символов ПСП 10-го порядка, начальное 0000000001, сброс на кадр", "PN945": "PN511 (9-й порядок) + 217/217 циклического расширения; 200 кадров в суперкадре",
   "системная информация": {"векторы (12 из 24)": vek, "остальные": "дополнения (инверсия всех бит)", "4 бита": "0000 — C=1, 1111 — C=3780"}, "отображение": "0 → +1, 1 → −1; системная информация — 4QAM с I = Q"},
  "DTMB", [K.ist(s_ph, "PN420"), K.ist(s_p2, "PN595"), K.ist(s_p3, "PN945"), K.ist(s_si, "системная информация")],
  f"12 векторов системной информации и их дополнения попарно отстоят не менее чем на {dist} бит (биортогональный код (32,6) на базе Уолша)", "нет"))
