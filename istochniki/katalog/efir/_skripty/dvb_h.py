"""DVB-SH (ETSI EN 302 583 v1.2.1): турбокод 3GPP2 с доп. скоростями 1/5…2/3, CRC-16 пакетов, скремблер EFRAME; MPE-IFEC (TS 102 772).
MPE-FEC RS(255,191) DVB-H — в dvb_t.py. Сверка таблицы перемежителя 3GPP2 — с таблицей cdma2000 из области «mobilnaya»
(C.S0002, собрана независимо) и проверка биекции; ГПСП — пересчётом."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

S = Tekst("istochniki/dvb_h/en_302583v010201p.pdf"); IF = Tekst("istochniki/dvb_h/ts_102772v010101p.pdf"); SY = Tekst("istochniki/dvb_h/ts_102585v010201p.pdf"); ZAPISI = []
SL = "DVB-SH (спутник/наземное вещание на карманные терминалы, S-диапазон)"
kus, s_lut = S.kusok(r"Table 5\.5: Turbo interleaver look-up table definition\s*\nTable", r"5\.4\s*\nChannel interleaver", posl=True)
ch = [int(v) for v in re.findall(r"\d+", kus)]
# после индекса идут два числа: n=6 и n=9
LUT6, LUT9 = [], []
i = 0; t = ch[2:] if ch[:2] == [6, 9] else ch
for k in range(32):
    j = t.index(k, i) if k else t.index(0, i)
    LUT6.append(t[j + 1]); LUT9.append(t[j + 2]); i = j + 3
assert len(LUT6) == len(LUT9) == 32
MOB = json.load(open(os.path.join(os.path.dirname(os.path.dirname(KOREN)), "katalog", "mobilnaya", "tablicy", "cdma2000_turbo_perem.json")))["данные"]["LUT_n3_n10"]
assert [r[3] for r in MOB] == LUT6 and [r[6] for r in MOB] == LUT9          # столбцы n = 6 и n = 9 таблицы cdma2000
def perem(L, n, lut):
    out = []; c = 0
    while len(out) < L:
        msb = ((c >> 5) + 1) & ((1 << n) - 1); v = (msb * lut[c & 31]) & ((1 << n) - 1)
        adr = (int(format(c & 31, "05b")[::-1], 2) << n) | v
        if adr < L: out.append(adr)
        c += 1
    return out
P = {12282: perem(12282, 9, LUT9), 1146: perem(1146, 6, LUT6)}
for L, p in P.items(): assert sorted(p) == list(range(L)), L
kus_p, s_p = S.kusok(r"Table 5\.2: Puncturing patterns for the data bit periods", r"NOTE 1: For each rate", posl=True)
VYK = {}
for m in re.finditer(r"(\d+)\s+(\d/\d)\s+(Standard|Complementary)\s+((?:[0-9];[0-9];[0-9];[0-9];[0-9];[0-9];?\s*)+)", kus_p):
    VYK[int(m.group(1))] = {"скорость": m.group(2), "вид": m.group(3), "шаблон": re.findall(r"[01]", m.group(4))}
assert sorted(VYK) == list(range(12))
from fractions import Fraction
for pid, v in VYK.items():
    sh = [int(b) for b in v["шаблон"]]; per = len(sh) // 6
    assert Fraction(per, sum(sh)) == Fraction(v["скорость"]), (pid, v)
kus_t, s_tt = S.kusok(r"Table 5\.3: Puncturing and symbol repetition patterns for the tail bit periods", r"NOTE 1: For each rate", posl=True)
HVOST = {int(m.group(1)): re.findall(r"[0-3]", m.group(4)) for m in re.finditer(r"(\d+)\s+(\d/\d)\s+(Standard|Complementary)\s+((?:[0-3];\s*){35}[0-3])", kus_t)}
assert sorted(HVOST) == list(range(12)) and all(len(v) == 36 for v in HVOST.values())
js = tablica("dvbsh_turbo", {"LUT_n6": LUT6, "LUT_n9": LUT9, "выкалывание_данных": VYK, "выкалывание_хвоста": HVOST,
                            "перестановка_12282": P[12282], "перестановка_1146": P[1146]},
   "DVB-SH (3GPP2) турбокод: таблица перемежителя (n = 6, 9), шаблоны выкалывания данных и хвоста, готовые перестановки для L = 12 282 и 1 146",
   [S.ist(s_lut, "табл. 5.5"), S.ist(s_p, "табл. 5.2"), S.ist(s_tt, "табл. 5.3")],
   "столбцы n = 6 и n = 9 совпали с таблицей cdma2000 (C.S0002) области mobilnaya; перестановки — биекции; скорость каждого шаблона = период/число единиц")
s_cc = S.odna(r"With d\(D\) = 1 \+ D2 \+ D3, n0\(D\) = 1 \+ D \+ D3, and n1\(D\) = 1 \+ D \+ D2 \+ D3")[0]
s_il = S.odna(r"Determine the turbo interleaver parameter, n, where n is the smallest integer such that LTC-input")[0]
ZAPISI.append(Z("DVB-SH турбокод 3GPP2: RSC 8 состояний d = 1+D²+D³, n0 = 1+D+D³, n1 = 1+D+D²+D³; 12 шаблонов 1/5…2/3 (стандартные и дополнительные); L = 12 282 (данные) / 1 146 (сигнализация)", SL, "турбо PCCC",
  {"составной код": "G(D) = [1, n0(D)/d(D), n1(D)/d(D)]", "выход": "X, Y0, Y1, X', Y'0, Y'1", "хвост": "по 3 такта каждого кодера с обратной связью, выкалывание/повтор по табл. 5.3",
   "перемежитель": "3GPP2: счётчик n+5 бит; (старшие n бит + 1)·LUT[5 мл. бит] mod 2^n, старшая часть адреса — реверс 5 мл. бит; отбрасывание ≥ L; n = 9 для 12 282, n = 6 для 1 146",
   "скорости": {pid: v["скорость"] + " " + v["вид"] for pid, v in VYK.items()}, "таблица": js,
   "далее": "битовый блочный перемежитель + свёрточный временной перемежитель (5.4), OFDM (как DVB-H) или TDM (как DVB-S2)"},
  "DVB-SH (Европа, S-диапазон 2,2 ГГц; ограниченное внедрение)", [S.ist(s_cc, "5.3.1"), S.ist(s_p, "табл. 5.2"), S.ist(s_tt, "табл. 5.3"), S.ist(s_il, "5.3.3"), S.ist(s_lut, "табл. 5.5")],
  "LUT n = 6/9 = таблица cdma2000 (C.S0002, собрана в области mobilnaya из другого документа); перестановки L = 12 282 и 1 146 — биекции; скорости шаблонов проверены",
  "частично: turbo.py (PCCC вслепую); перемежитель 3GPP2 и шаблоны DVB-SH не встроены"))
s_crc = S.odna(r"generator polynomial shall be 0x1021")[0]
s_scr = S.odna(r"Loading of the sequence \(100101010000000\) into the PRBS register")[0]
s_eh = S.odna(r"A fixed length Encapsulation Frame Header \(EHEADER\) of 114 bits shall be inserted")[0]
ZAPISI.append(Z("DVB-SH адаптация: CRC-16 (0x1021, init FFFF) вместо синхробайта каждого пакета TS, заголовок EHEADER 114 бит с CRC-16, EFRAME 12 282 бит, скремблер 1+X^14+X^15 (100101010000000) на EFRAME", SL, "CRC-подобный + скремблер",
  {"CRC-16": "g = X^16 + X^12 + X^5 + 1, начальное FFFF, по 187 байтам пакета (без синхробайта); CRC ставится в конец пакета, синхробайт удаляется",
   "EHEADER": "TIS 2 | UPL 16 | DFL 16 | SYNC 8 | RFU 32 | CBCOUNTER 24 | CRC-16 по первым 98 битам", "EFRAME": "EHEADER + 8 пакетов (12 096 бит) + 72 нуля = 12 282 бита = вход турбокода",
   "скремблер": "как DVB (1+X^14+X^15, 100101010000000), сброс на каждый EFRAME"},
  SL, [S.ist(s_crc, "5.1.1"), S.ist(s_eh, "5.1.2"), S.ist(s_scr, "5.2.2")], "ГПСП и начальная загрузка совпадают с DVB-T/S2 (проверены в dvb_t.py/dvb_t2.py)",
  "есть: dvbs2.py (скремблер BBFRAME той же ГПСП), crc_katalog (CRC-16/IBM-3740 = 0x1021 init FFFF)"))
s_if = IF.odna(r"One based on MPE-FEC Reed Solomon code \(EN 301 192 \[1\], clause 9\.5\.1\) and called \"sliding RS")[0]
s_ra = IF.odna(r"Another based on Raptor code \(TS 102 472 \[2\]\) and called \"generalized encoding with Raptor code\" is")[0]
ZAPISI.append(Z("MPE-IFEC (DVB-SH/DVB-H): межпакетный FEC канального уровня — «скользящий» RS(255,191) MPE-FEC по нескольким пакетам (обязательный) или Raptor (информативно)", "DVB-H / DVB-SH (IP-вещание)", "РС (межпакетный) / фонтанный",
  {"RS": "тот же RS(255,191) MPE-FEC (EN 301 192 9.5.1), матрица заполняется B пакетами данных (ADST-отображение), чётность рассылается в S последующих пакетах",
   "Raptor": "TS 102 472 (= Raptor R10 3GPP MBMS / IETF RFC 5053)", "параметры": "EP, B, S, C, T — таблица ADT K = C·EP столбцов × T строк"},
  "DVB-SH, DVB-H IPDC", [IF.ist(s_if, "RS"), IF.ist(s_ra, "Raptor")], "по тексту TS 102 772; RS(255,191) проверен в записи DVB-H MPE-FEC (dvb_t.py)",
  "частично: rs_bch.py (RS(255,191)); межпакетная раскладка — нет"))
