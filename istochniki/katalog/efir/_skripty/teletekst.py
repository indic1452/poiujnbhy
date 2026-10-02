"""Телетекст (EN 300 706), PDC/VPS (EN 300 231), WSS (EN 300 294): Хэмминг 8/4, Хэмминг 24/18, нечётная чётность,
синхрослова; WSS — бифазный код и чётность. Проверка — построением кодов из формул стандарта и перебором минимального веса."""
import re, sys, os, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/teletekst/en_300706v010201p.pdf"); W = Tekst("istochniki/teletekst/en_300294v010401p.pdf"); P = Tekst("istochniki/teletekst/en_300231v010301p.pdf")
ZAPISI = []
def formula(l):
    return [t.strip().rstrip(".") for t in l.split("=", 1)[1].split("⊕")]
# --- Хэмминг 8/4 ---
f84 = {}
for p in ("P1", "P2", "P3", "P4"):
    s, _, l = T.odna(rf"^(For encoding: )?{p} = 1 ⊕")
    f84[p] = formula(l)
def kod84(d):
    D = {f"D{i+1}": d[i] for i in range(4)}; P_ = {}
    for p in ("P1", "P2", "P3", "P4"):
        v = 0
        for t in f84[p]:
            v ^= 1 if t == "1" else (D.get(t) if t in D else P_[t])
        P_[p] = v
    return [P_["P1"], D["D1"], P_["P2"], D["D2"], P_["P3"], D["D3"], P_["P4"], D["D4"]]
slova84 = [kod84([(n >> i) & 1 for i in range(4)]) for n in range(16)]
dmin84 = min(sum(a != b for a, b in zip(x, y)) for x, y in itertools.combinations(slova84, 2))
assert dmin84 == 4
bajty84 = [sum(b << i for i, b in enumerate(w)) for w in slova84]   # бит 1 передаётся первым = младший бит байта
s84, _, _ = T.odna(r"In a single 8-bit byte, bits 1, 3, 5 and 7 are the protection bits and bits 2, 4, 6 and 8 carry the data")
# --- Хэмминг 24/18 ---
f24 = {}
kus24, s24 = T.kusok(r"Over three consecutive 8-bit bytes, bits 1, 2, 4, 8, 16, 24 are the protection bits", r"8\.4|For decoding", posl=True)
k24 = re.sub(r"⊕\s*\n", "⊕ ", kus24)
for m in re.finditer(r"(P[1-6]) ?= ?(1 ⊕[^\n]*)", k24):
    f24[m.group(1)] = [x.strip().rstrip(".").strip() for x in m.group(2).split("⊕")]
assert set(f24) == {f"P{i}" for i in range(1, 7)}, f24.keys()
poz = "P1 P2 D1 P3 D2 D3 D4 P4 D5 D6 D7 D8 D9 D10 D11 P5 D12 D13 D14 D15 D16 D17 D18 P6".split()
def kod24(d):
    V = {f"D{i+1}": d[i] for i in range(18)}
    for p in ("P1", "P2", "P3", "P4", "P5", "P6"):
        v = 0
        for t in f24[p]:
            v ^= 1 if t == "1" else V[t]
        V[p] = v
    return int("".join(str(V[x]) for x in poz), 2)
# минимальный вес ненулевой разности = минимальный вес линейной части (без «1 ⊕»): перебор 2^18
c0 = kod24([0] * 18)
dmin24 = min(bin(kod24([(n >> i) & 1 for i in range(18)]) ^ c0).count("1") for n in range(1, 1 << 18))
assert dmin24 == 4
s_fc, _, _ = T.odna(r"The bit pattern in transmission order is: 11100100")
s_cr, _, _ = T.odna(r"The bit pattern in transmission order is: 1010101010101010")
s_par, _, _ = T.odna(r"For encoding: P = 1 ⊕ D1 ⊕ D2 ⊕ D3 ⊕ D4 ⊕ D5 ⊕ D6 ⊕ D7")
ZAPISI.append(Z("Телетекст: Хэмминг 8/4 (адреса, управление), Хэмминг 24/18 (тройки X/26…X/29), нечётная чётность (текст), синхрослова", "Телетекст (EN 300 706)", "Хэмминг",
  {"8/4": "байт P1 D1 P2 D2 P3 D3 P4 D4 (передача с бита 1); P1=1⊕D1⊕D3⊕D4, P2=1⊕D1⊕D2⊕D4, P3=1⊕D1⊕D2⊕D3, P4=1⊕(все прочие); d=4 — исправление 1, обнаружение 2 ошибок",
   "кодовые байты 8/4 (данные 0…15, младший бит = первый переданный)": [f"{b:02X}" for b in bajty84],
   "24/18": "3 байта: P1 P2 D1 P3 D2 D3 D4 P4 D5…D11 P5 D12…D18 P6 (формулы 8.3); d=4", "чётность": "7 бит + нечётная чётность P = 1⊕D1⊕…⊕D7",
   "синхронизация": "clock run-in 1010101010101010, framing code 11100100 (обе чётной чётности)", "строка": "45 байт: 2 run-in + 1 FC + 2 адреса (8/4) + 40 данных; 6,9375 Мбит/с NRZ (система B)"},
  "Телетекст WST (система B, ТВ PAL/SECAM), пакеты 8/30 (PDC), X/26 (расширения)",
  [T.ist(s84, "8.2 Хэмминг 8/4"), T.ist(s24, "8.3 Хэмминг 24/18"), T.ist(s_par, "8.1 чётность"), T.ist(s_fc, "framing code"), T.ist(s_cr, "clock run-in")],
  f"коды построены из формул стандарта: 16 слов 8/4 и все 2^18 слов 24/18; минимальное расстояние 4 и 4 (полный перебор)",
  "частично: kod.блочный (короткие линейные коды вслепую); именованного телетекста нет"))
# --- WSS ---
s_w1, _, _ = W.odna(r"The data bits shall be inserted in bi-phase-L, in which one data bit period equals 2 × 3 clock periods")
s_w2, _, _ = W.odna(r"For error detection, an odd parity bit has been introduced\. The odd parity bit shall belong to the first 3 data bits only")
kus_w, s_w3 = W.kusok(r"The preamble contains a run-in and a start code", r"4\.1\.7")
s_sc, _, _ = W.odna(r"^0001 1110 0011 1100 0001 1111")
ZAPISI.append(Z("WSS (сигнал широкоэкранного формата, строка 23): бифазный код L, нечётная чётность по 3 битам, run-in + start code", "WSS (EN 300 294)", "линейный код (бифазный) + чётность",
  {"бифаза": "бит = 6 тактов 5 МГц (2×3)", "данные": "14 бит: группа 1 — 3 бита соотношения сторон + бит нечётной чётности; группы 2–4 — 10 бит", "start code": "0001 1110 0011 1100 0001 1111 (0x1E3C1F)", "run-in": "29 элементов 1 1111 0001 1100 0111 0001 1100 0111 (0x1F1C71C7)"},
  "PAL/SECAM ТВ (соотношение сторон, режим)", [W.ist(s_w1, "4.1.? бифаза"), W.ist(s_w2, "4.1.8 чётность"), W.ist(s_sc, "Table 1 start code")], "значения из текста", "частично: линейные коды (манчестер/бифаза убран из проекта по решению отдела)"))
s_p1, _, _ = P.odna(r"Each packet X/26 contains a clock run-in, framing code, magazine and packet address, followed by a designation code")
ZAPISI.append(Z("PDC/VPS: метки программ в телетексте (пакет 8/30 формат 2 — Хэмминг 8/4; X/26 — Хэмминг 24/18) и VPS (строка 16, бифаза)", "PDC/VPS (EN 300 231)", "Хэмминг (использует коды телетекста)",
  {"8/30 формат 2": "метка PIL/CNI в байтах, защищённых Хэммингом 8/4", "X/26": "13 троек Хэмминга 24/18", "VPS": "строка 16, бифазный код, 15 байт (без защиты кодом, кроме бифазы)"},
  "PAL-ТВ: управление видеомагнитофонами по программе", [P.ist(s_p1, "X/26")], "ссылается на коды EN 300 706 (проверены в записи телетекста)", "нет"))
