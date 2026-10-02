"""ISDB-T / ISDB-Tb (ARIB STD-B31 v2.2): внешний RS(204,188), рандомизатор, байтовый перемежитель, свёрточный 171/133
с выкалыванием, временной перемежитель, TMCC/AC — укороченные коды разностных множеств (273,191). Сверка — gr-isdbt (GPL-3)."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

I = Tekst("istochniki/isdb/ARIB-STD-B31v2_2-E1.pdf"); ZAPISI = []
GI = "gr-isdbt"
g_tm = kod(GI, "lib/tmcc_encoder_impl.cc"); g_ed = kod(GI, "lib/energy_dispersal_impl.cc"); g_ti = kod(GI, "lib/time_interleaver_impl.cc")

s_p, _, _ = I.odna(r"polynomial p \(x\) is used to define GF \(28\)")
s_g, _, _ = I.odna(r"Note also that the following polynomial g \(x\) is used to generate \(204,188\) shortened")
s_e, _, l_e = I.odna(r"^g\(x\) = X15 \+ X14 \+ 1")
s_ei, _, _ = I.odna(r"The initial value of the register in the PRBS-generating circuit must be “100101010000000”")
s_b, _, _ = I.odna(r"energy-dispersed, undergoes convolutional byte interleaving\.\s+Interleaving must be 12 bytes")
ZAPISI.append(Z("ISDB-T внешний код: RS(204,188) + рандомизатор X^15+X^14+1 (сброс на кадр OFDM) + байтовый перемежитель 12", "ISDB-T (ARIB STD-B31)", "каскадный: РС + перемежитель",
  {"РС": "(204,188) t=8 укороченный, GF(2^8) p(x) = x^8+x^4+x^3+x^2+1 (как DVB)", "рандомизатор": "g(x) = X^15 + X^14 + 1, начальное 100101010000000, сброс каждый кадр OFDM (а не 8 пакетов, как DVB); синхробайт не скремблируется, но регистр сдвигается",
   "перемежитель": "свёрточный байтовый, 12 ветвей, 17·j байт (как DVB), плюс выравнивание задержек слоёв (табл. 3-7)"},
  "ISDB-T (Япония), ISDB-Tb/SBTVD (Бразилия, Латинская Америка), ISDB-Tsb", [I.ist(s_p, "p(x)"), I.ist(s_g, "g(x) RS"), I.ist(s_e, "рис. 3-8 ПСП"), I.ist(s_ei, "начальное"), I.ist(s_b, "3.? байтовое перемежение"), ist_kod(g_ed, r"0x|init|reg", "gr-isdbt energy_dispersal")],
  "параметры из текста; ПСП и загрузка совпадают с DVB (запись DVB-T), отличается только момент сброса", "есть: dvb.py (RS(204,188), Форни 12×17) — сброс рандомизатора по кадру OFDM не встроен"))

kus, s_t = I.kusok(r"Table 3-8: Inner-Code Coding Rates and Transmission-Signal Sequence", r"\(Ordinance Annex Table 12, Item 3", posl=True)
pat = {}
for m in re.finditer(r"(\d/\d)\s*\nX : ([01 ]+)\nY : ([01 ]+)\n", kus):
    pat[m.group(1)] = {"X": m.group(2).replace(" ", "").strip(), "Y": m.group(3).replace(" ", "").strip()}
dvb = json.load(open(os.path.join(KOREN, "tablicy", "dvbt_vykalyvanie_t2.json")))["данные"]
assert list(pat) == ["1/2", "2/3", "3/4", "5/6", "7/8"] and all(pat[r]["X"] == dvb[r]["X"] and pat[r]["Y"] == dvb[r]["Y"] for r in pat), (pat, dvb)
s_m, _, _ = I.odna(r"G1 = 171OCT and G2 = 133OCT")
ZAPISI.append(Z("ISDB-T внутренний свёрточный 171/133 K=7 с выкалыванием 1/2…7/8", "ISDB-T (ARIB STD-B31)", "свёрточный с выкалыванием",
  {"многочлены": "G1 = 171₈, G2 = 133₈", "шаблоны": pat, "сброс шаблона": "в начале кадра OFDM (синхронизация шаблона выкалывания)"},
  "ISDB-T, ISDB-Tb, ISDB-Tsb", [I.ist(s_m, "3.? материнский код"), I.ist(s_t, "Table 3-8")],
  "шаблоны табл. 3-8 разобраны и совпали побитно с табл. 2 EN 300 744 (DVB-T) — независимый документ", "есть: kod.СВЁРТОЧНЫЕ (171,133), vykalyvanie.py"))

s_ti, _, _ = I.odna(r"Provided that mi = \(i.{1,4}5\) mod 96")
# табл. 3-12: длины временного перемежения I и задержки по режимам (поправка второго прохода сверки: было «I = 0/1/2/4/8/16 (режим 1)»)
kus312, s312 = I.kusok(r"Table 3-12: Time Interleaving Lengths and Delay Adjustment Values", r"\(Notification No\. 303, Annexed Table 2")
ch312 = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus312)]
assert len(ch312) == 36, ch312
str312 = [ch312[k:k + 9] for k in range(0, 36, 9)]
dl_I = {m: [r[3 * m] for r in str312] for m in range(3)}; zd_s = {m: [r[3 * m + 1] for r in str312] for m in range(3)}; zd_k = {m: [r[3 * m + 2] for r in str312] for m in range(3)}
assert dl_I == {0: [0, 4, 8, 16], 1: [0, 2, 4, 8], 2: [0, 1, 2, 4]}, dl_I
assert zd_s == {0: [0, 28, 56, 112], 1: [0, 14, 28, 56], 2: [0, 109, 14, 28]} and zd_k == {0: [0, 2, 4, 8], 1: [0, 1, 2, 4], 2: [0, 1, 1, 2]}, (zd_s, zd_k)
ZAPISI.append(Z("ISDB-T перемежители: битовый (задержки 120 бит по разрядам), временной внутрисегментный mi = 5i mod 96, частотный", "ISDB-T (ARIB STD-B31)", "перемежитель",
  {"битовый": "после S/P разряды символа задерживаются на 0/40/80/120 бит (QPSK — 120, 16QAM — 40/80/120, 64QAM — 24…120; рис. 3-12…3-16)",
   "временной": "для несущей i задержка I·mi символов, mi = (5·i) mod 96; длина I по табл. 3-12: режим 1 — I = 0, 4, 8, 16; режим 2 — 0, 2, 4, 8; режим 3 — 0, 1, 2, 4 (nc = 96/192/384 несущих на сегмент)",
   "выравнивание задержки (табл. 3-12)": {f"режим {m + 1}": {f"I={i}": {"символов выравнивания": zs, "кадров задержки": zk} for i, zs, zk in zip(dl_I[m], zd_s[m], zd_k[m])} for m in range(3)},
   "частотный": "межсегментный + внутрисегментный: циклический сдвиг и случайная перестановка несущих (3.11.2)"},
  "ISDB-T, ISDB-Tb", [I.ist(s_ti, "рис. 3-23"), I.ist(s312, "Table 3-12 длины I и выравнивание задержки"), ist_kod(g_ti, r"mod|% 96|\* 5", "gr-isdbt time_interleaver")], "формула из текста; gr-isdbt использует ту же формулу; табл. 3-12 разобрана по числам (36 значений) с assert", "частично: peremezhenie.py (блочное); ISDB-перемежители — нет"))

s_c, _, _ = I.odna(r"\(184,102\) of the difference-set cyclic code \(273,191\)\.\s+The following shows the generating")
kus_g, _ = I.kusok(r"polynomial of the \(273,191\) code:", r"\(Ordinance Annexed Table 12, Item 2\)", posl=True)
st = stepeni(" + ".join(re.findall(r"x\d+|\b1\b", kus_g.replace("x82", "x82"))))
assert st == [82, 77, 76, 71, 67, 66, 56, 52, 48, 40, 36, 34, 24, 22, 18, 10, 4, 0], st
g = mnogochlen(st)
t_tm = open(os.path.join(KOREN, g_tm)).read()
gi = sorted([int(x) for x in re.findall(r"one << (\d+)", t_tm.split("g = ")[1].split(";")[0])] + [0], reverse=True)
assert gi == st, gi
# циклический код длины 273: g(x) делит x^273 + 1
def pmod(a, b):
    db = b.bit_length() - 1
    while a.bit_length() - 1 >= db:
        a ^= b << (a.bit_length() - 1 - db)
    return a
assert pmod((1 << 273) | 1, g) == 0
s_w, _, _ = I.odna(r"\(w0 = 0011010111101110, w1 = 1100101000010001\)")
s_ac, _, _ = I.odna(r"shortened \(187,105\) code of the \(273,191\) difference-set cyclic code")
ZAPISI.append(Z("ISDB-T TMCC и AC (сейсмическое предупреждение): код разностных множеств (273,191), укороченный (184,102)/(187,105)", "ISDB-T (ARIB STD-B31)", "циклический (разностных множеств, мажоритарно декодируемый)",
  {"g(x)": "x^82+x^77+x^76+x^71+x^67+x^66+x^56+x^52+x^48+x^40+x^36+x^34+x^24+x^22+x^18+x^10+x^4+1", "TMCC": "B20…B121 (102 бита) → (184,102), 204 бита на кадр, DBPSK, повтор на нескольких несущих",
   "AC": "биты B17…B121 → (187,105)", "синхрослово": "w0 = 0011010111101110, w1 = 1100101000010001 (чередуются по кадрам; то же, что синхрослово TPS DVB-T)"},
  "ISDB-T/Tb TMCC, AC (EWS/сейсмика)", [I.ist(s_c, "3.15.6.9"), I.ist(s_ac, "3.16.6.7 AC"), I.ist(s_w, "синхрослово"), ist_kod(g_tm, r"g = \(one << 82\)", "gr-isdbt")],
  "многочлен из текста совпал с gr-isdbt; проверено, что g(x) делит x^273 + 1 (циклический код длины 273)", "частично: kod.блочный (проверки вслепую); именованного кода нет"))
