"""DRM / DRM+ (ETSI ES 201 980 V4.3.1): многоуровневое кодирование (MLC) с материнским свёрточным кодом 1/6 K=7,
выкалывание (табл. 27, 28), энергодисперсия, битовые перемежители, CRC G1…G16, RS пакетного режима.
Сверка — drm-receiver (JvanKatwijk, GPL-2)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

D = Tekst("istochniki/drm/es_201980v040301p.pdf"); ZAPISI = []
DR = "drm-receiver"
d_pt = kod(DR, "the-decoder/parameters/puncture-tables.cpp"); d_vit = kod(DR, "the-decoder/support/viterbi-drm.cpp")
d_map = kod(DR, "the-decoder/support/mapper.cpp"); d_prbs = kod(DR, "the-decoder/support/prbs.cpp"); d_fac = kod(DR, "the-decoder/fac/fac-processor.cpp")
d_sdc = kod(DR, "the-decoder/sdc/sdc-processor.cpp")

s_g, _, _ = D.odna(r"The octal forms of the generator polynomials are 133, 171, 145, 133, 171 and 145, respectively")
t_vit = open(os.path.join(KOREN, d_vit)).read()
assert all(f"Poly{i}\t0{p}" in t_vit.replace(" ", "\t").replace("\t\t", "\t") or re.search(rf"Poly{i}\s+0{p}", t_vit) for i, p in enumerate(["133", "171", "145", "133", "171", "145"], 1))
kus, s_t = D.kusok(r"Table 27: Puncturing patterns", r"For the FAC, all bits are punctured according to table 27", posl=True)
kus = bez_kolontitulov(kus, r"ETSI")
pat = {}
for m in re.finditer(r"\n(\d+/\d+)\s*\n(?:\d+\s*\n\d+\s*\n)?((?:B\d: [01 ]+\s*\n){6})", kus):
    rows = [r.split(":")[1].split() for r in m.group(2).strip().split("\n")]
    pat[m.group(1)] = ["".join(r) for r in rows]
assert len(pat) == 15, list(pat)
t_pt = open(os.path.join(KOREN, d_pt)).read()
sovp = []; zamech = []
for r, rows in pat.items():
    k, n = map(int, r.split("/")); per = len(rows[0])
    assert per == k and sum(x.count("1") for x in rows) == n, r
    nm = f"table_{k}{n}"
    if re.search(rf"\b{nm}\s*\[\]", t_pt):
        g = c_massiv(d_pt, nm)
        # drm-receiver: для каждой позиции периода — 6 бит B0..B5
        exp = [int(rows[b][p]) for p in range(per) for b in range(6)]
        assert g[:len(exp)] == exp, (r, g, exp)
        if len(g) != len(exp): zamech.append(f"{nm} в drm-receiver длиннее периода стандарта ({len(g) // 6} позиций вместо {per}); первые {per} позиций совпадают")
        sovp.append(r)
s28, _, _ = D.odna(r"^Table 28: Puncturing patterns of the tailbits")
tab = tablica("drm_vykalyvanie", {"скорости": pat}, "Шаблоны выкалывания DRM (табл. 27): для каждой скорости Rp — 6 строк B0…B5 (выходы материнского кода 1/6), столбцы — позиции периода",
  [D.ist(s_t, "Table 27")], f"разобрано из текста (число единиц = RYp, период = RXp); совпало с drm-receiver для {len(sovp)} скоростей: {', '.join(sovp)}")
ZAPISI.append(Z("DRM MLC: материнский свёрточный 1/6 K=7 (133,171,145,133,171,145) с выкалыванием 1/6…8/9", "DRM / DRM+ (ES 201 980)", "свёрточный с выкалыванием (многоуровневое кодирование)",
  {"многочлены": "133, 171, 145, 133, 171, 145 (восьмерично), K = 7", "хвост": "6 бит → 36 бит, выкалываются по табл. 28 (индекс rp)", "выкалывание": tab,
   "MLC": "4-QAM — 1 уровень, 16-QAM — 2 уровня, 64-QAM — 3 уровня (SM/HMsym/HMmix); скорость каждого уровня — табл. 29–40",
   "перемежение бит": "уровни p≥1 (и FAC/SDC) — перестановка Π(i) = (t0·Π(i−1) + q) mod s, s = 2^⌈log2 N⌉, q = s/4 − 1, t0 = 13 или 21 (значения ≥ N пропускаются) — по drm-receiver (формула в PDF — рисунок)"},
  "DRM30 (ДВ/СВ/КВ) и DRM+ (режим E, УКВ)", [D.ist(s_g, "7.3.1 многочлены"), D.ist(s_t, "Table 27"), D.ist(s28, "Table 28"), ist_kod(d_vit, r"Poly1", "drm-receiver"), ist_kod(d_pt, r"table_47", "drm-receiver"), ist_kod(d_map, r"t0 \* mapperTable", "drm-receiver перемежитель")],
  f"15 шаблонов разобраны из текста с проверкой периода и числа единиц; {len(sovp)} из них побитно совпали с drm-receiver; многочлены = Poly1…Poly6 drm-receiver" + ("; замечание: " + "; ".join(zamech) if zamech else ""),
  "частично: svyortka.py (1/n до 4 — 1/6 нет), vykalyvanie.py (k/(k+1)); MLC DRM — нет"))

kus26, s26 = D.kusok(r"Table 26: First 16 bits of the PRBS", r"7\.2\.3|Table 27", posl=True)
perv = "".join(re.findall(r"\b[01]\b", kus26.split("bit value")[1]))[:16]
sr = [1] * 9; out = ""
for _ in range(16):
    b = sr[8] ^ sr[4]; sr = [b] + sr[:-1]; out += str(b)
assert perv == out == "0000011110111110"
ZAPISI.append(Z("DRM энергодисперсия: ПСП x^9 + x^5 + 1, все единицы", "DRM / DRM+ (ES 201 980)", "скремблер (рандомизатор)",
  {"многочлен": "x^9 + x^5 + 1", "начало": "все единицы, сброс для каждого кадра мультиплекса MSC, блока SDC и блока FAC", "первые 16 бит": perv},
  "DRM, DRM+", [D.ist(s26, "Table 26"), ist_kod(d_prbs, r".", "drm-receiver prbs")],
  "первые 16 бит табл. 26 воспроизведены (та же ПСП, что DAB, табл. 12 EN 300 401)", "частично: skrembler.py (x^9+x^5+1 находится вслепую)"))

kusD, sD = D.kusok(r"The CRC codes used in the DRM system are based on the following polynomials", r"Annex E \(informative\)", posl=True)
assert est_podposl(kusD, [1, 5, 12, 16]) and est_podposl(kusD, [1, 2, 3, 4, 8, 8]) and est_podposl(kusD, [1, 2, 3, 5, 6, 6]) and est_podposl(kusD, [1, 2, 4, 5, 5]) and est_podposl(kusD, [1, 3, 3]) and est_podposl(kusD, [1, 2, 2])
s8, _, _ = D.odna(r"polynomial G8\(x\) = x8 \+ x4 \+ x3 \+ x2 \+ 1\. See annex D")
s16, _, _ = D.odna(r"generator polynomial G16\(x\) = x16 \+ x12 \+ x5 \+ 1")
ZAPISI.append(Z("DRM CRC: G16 = x^16+x^12+x^5+1, G8 = x^8+x^4+x^3+x^2+1, G6, G5, G3, G2, G1 (Annex D)", "DRM / DRM+ (ES 201 980)", "CRC-подобный",
  {"многочлены": {"G16": "x^16+x^12+x^5+1 (SDC, пакеты, AFS, группы данных)", "G8": "x^8+x^4+x^3+x^2+1 (FAC, заголовки/AAC)", "G6": "x^6+x^5+x^3+x^2+x+1", "G5": "x^5+x^4+x^2+x+1", "G3": "x^3+x+1", "G2": "x^2+x+1", "G1": "x+1"},
   "начальное": "все единицы", "выход": "инверсия (дополнение до 1)", "порядок": "старший бит первым"},
  "DRM FAC, SDC, MSC (заголовки аудиокадров, пакетный режим)", [D.ist(sD, "Annex D"), D.ist(s8, "G8"), D.ist(s16, "G16")],
  "степени всех 7 многочленов найдены в тексте Annex D (формулы разобраны по цифрам); G8 и G16 — ещё и в тексте разделов", "частично: crc.py (CRC вслепую), crc_katalog (CRC-16/GENIBUS, CRC-8 варианты)"))

s_rs, _, _ = D.odna(r"RS \(255, 239, t = 8\) code or a shortened version of this mother code")
ZAPISI.append(Z("DRM пакетный режим: внешний RS(255,239) t=8 (укорачиваемый до (C+16,C)) с перемежением по таблице", "DRM / DRM+ (ES 201 980)", "РС",
  {"код": "RS(255,239), g(x) = ∏(x+λ^i) i=0…15, λ = 0x02, p(x) = x^8+x^4+x^3+x^2+1; при C < 239 — RS(C+16, C)", "таблица": "R строк × C столбцов данных (Application Data Table), RS по строкам, чётность распределяется по пакетам FEC"},
  "DRM пакетный режим (данные)", [D.ist(s_rs, "6.6.? FEC для пакетного режима")], "параметры из текста; поле и g(x) — как DVB", "есть: rs_bch.py (RS над GF(256) любой длины)"))
