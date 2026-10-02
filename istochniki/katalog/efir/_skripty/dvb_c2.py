"""DVB-C2 (EN 302 769 V1.3.1): БЧХ, LDPC (Annex A/B), заголовок FECFrame и заголовок преамбулы — Рида — Маллера (32,16),
CRC. Сверка LDPC — с gr-dtv; RM(32,16) — вычислением (ранг и минимальное расстояние)."""
import re, sys, os, json, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

C = Tekst("istochniki/dvb_c2/en_302769v010301p.pdf"); KOL = r"ETSI"
GR = "gnuradio"; ZAPISI = []
g_ldpc = kod(GR, "gr-dtv/lib/dvb/dvb_ldpc_bb_impl.cc")

kus, s_b = C.kusok(r"Table 4\(a\): BCH polynomials \(for normal FECFrame", r"The bits of the baseband frame|6\.1\.2", posl=True)
a, b = kus.split("Table 4(b)")
pa = [stepeni(x.replace("+", " + ")) for x in re.findall(r"\n(1\+[x\d+]+)\s*\n", a)]
pb = [stepeni(x.replace("+", " + ")) for x in re.findall(r"\n(1\+[x\d+]+)\s*\n", b)]
atsc = json.load(open(os.path.join(KOREN, "tablicy", "atsc3_bch_mnogochleny.json")))["данные"]
zap = lambda st: " + ".join(("1" if s == 0 else "x" if s == 1 else f"x{s}") for s in sorted(st))
assert [zap(s) for s in pa] == atsc["64800"] and [zap(s) for s in pb] == atsc["16200"], (len(pa), len(pb))

SPIS = [("A.1", "2/3", 64800), ("A.2", "3/4", 64800), ("A.3", "4/5", 64800), ("A.4", "5/6", 64800), ("A.5", "9/10", 64800),
        ("B.1", "1/2", 16200), ("B.2", "2/3", 16200), ("B.3", "3/4", 16200), ("B.4", "4/5", 16200), ("B.5", "5/6", 16200), ("B.6", "8/9", 16200)]
IMENA = {(r, n): [f"ldpc_tab_{r.replace('/', '_')}{'N' if n == 64800 else 'S'}" + suf for suf in ("", "_DVBT2", "_DVBS2")] for _, r, n in SPIS}
t_ldpc = open(os.path.join(KOREN, g_ldpc)).read()
sovp = {}; tabl = {}
for tn, r, Nn in SPIS:
    kus, s = C.kusok(rf"Table {re.escape(tn)}: Rate {r} \(Nldpc = {Nn // 1000} {Nn % 1000:03d}\)[^\n]*", r"Table [AB]\.\d+:|Annex C \(normative\)", posl=True)
    rows = skleit_korotkie(stroki_chisel(bez_kolontitulov(kus, KOL)))
    kto = []
    for nm in IMENA[(r, Nn)]:
        if re.search(re.escape(nm) + r"\[", t_ldpc):
            gr = [x[1:1 + x[0]] for x in c_massiv_2d(g_ldpc, nm)]
            if [sorted(x) for x in gr] == [sorted(x) for x in rows]:
                kto.append(nm + (" (порядок адресов тот же)" if gr == rows else " (адреса в строке переставлены)"))
    assert kto, (tn, r, Nn, rows[0])
    sovp[f"{r} ({Nn})"] = kto
    tabl[f"{r} ({Nn})"] = tablica(f"dvbc2_ldpc_{r.replace('/', '_')}_{Nn}", {"строки": rows, "Nldpc": Nn, "Kldpc": len(rows) * 360},
        f"Адреса накопителей чётности LDPC DVB-C2, R = {r}, Nldpc = {Nn} (табл. {tn})", [C.ist(s, f"Table {tn}")], f"разобрано из текста; совпало с gr-dtv: {kto}")
ZAPISI.append(Z("DVB-C2 LDPC 64800 (2/3…9/10) и 16200 (1/2…8/9) + внешний БЧХ", "DVB-C2 (EN 302 769)", "каскадный: БЧХ + LDPC",
  {"LDPC": tabl, "совпадения с gr-dtv": sovp, "БЧХ": "те же g1…g12, что DVB-T2/S2/ATSC 3.0 (t=10 для 2/3 64800, иначе 12)", "скремблер ББ": "1+X^14+X^15, 100101010000000"},
  "DVB-C2 (кабель второго поколения)", [C.ist(s_b, "Table 4(a)/(b)"), C.ist(C.odna(r"^Table A\.1: Rate 2/3")[0], "Annex A"), ist_kod(g_ldpc, r"ldpc_tab_9_10N\[", "gr-dtv")],
  "11 таблиц разобраны из текста; каждая совпала (как множество адресов по строкам) с таблицей gr-dtv DVB-S2/T2; БЧХ совпали с ATSC A/322",
  "есть: data/ldpc_dvb.json (DVB-S2/T2 — те же коды), dvbs2.py (БЧХ)"))

# --- RM(32,16) -------------------------------------------------------------------------------
kus_g, s_g = C.kusok(r"Table 16\(a\): Definition of the Reed-Muller encoder matrix", r"The 32 Reed-Muller encoded data bits vector", posl=True)
bits = [int(x) for x in re.findall(r"(?m)^\s*([01])\s*$", kus_g)]
assert len(bits) == 512, len(bits)
def rank(rows):
    rows = [int("".join(map(str, r)), 2) for r in rows]; rk = 0; basis = {}
    for r in rows:
        while r:
            h = r.bit_length() - 1
            if h in basis: r ^= basis[h]
            else: basis[h] = r; rk += 1; break
    return rk, [basis[k] for k in basis]
varianty = {"левые половины строк (256 бит), затем правые (256 бит)": [bits[16 * i:16 * i + 16] + bits[256 + 16 * i:256 + 16 * i + 16] for i in range(16)],
            "строки 16×32": [bits[i * 32:(i + 1) * 32] for i in range(16)], "столбцы (32×16 транспонированная)": [[bits[j * 16 + i] for j in range(32)] for i in range(16)]}
itog = None
for nm, G in varianty.items():
    rk, _ = rank(G)
    if rk != 16: continue
    gi = [int("".join(map(str, r)), 2) for r in G]
    dmin = 32
    for m in range(1, 1 << 16):
        c = 0
        for i in range(16):
            if m >> i & 1: c ^= gi[i]
        w = bin(c).count("1")
        if w < dmin: dmin = w
    itog = (nm, dmin, G); break
assert itog and itog[1] == 8, itog and itog[:2]
s_p, _, _ = C.odna(r"Reed-Muller \(32,16\) code and encoded by QPSK same as the QPSK based FECFrame header")
s_l, _, _ = C.odna(r"cyclically delayed by two values within each Reed-Muller codeword")
put_rm = tablica("dvbc2_rm_32_16_G", {"G": itog[2], "разбор": itog[0]}, "Порождающая матрица Рида — Маллера (32,16) DVB-C2 (табл. 16a)",
  [C.ist(s_g, "Table 16(a)")], f"512 бит разобраны из текста ({itog[0]}); ранг 16; минимальный вес ненулевого слова = {itog[1]} (как у RM(2,5)) — перебор всех 65535 слов")
ZAPISI.append(Z("DVB-C2 заголовок FECFrame и заголовок преамбулы: Рида — Маллера (32,16)", "DVB-C2 (EN 302 769)", "РМ",
  {"код": "RM(32,16), d = 8 (RM(2,5))", "G": put_rm, "FECFrame header": "16 бит (PLP_ID, PLP_FEC_TYPE, PLP_MOD, PLP_COD, …) → RM(32,16) → верхняя ветвь как есть, нижняя — циклический сдвиг на 2 и скремблирование ПСП (7.2.2.3) → QPSK или 16-QAM (устойчивый/обычный)",
   "заголовок преамбулы": "16 бит → RM(32,16) → QPSK (32 ячейки)"},
  "DVB-C2", [C.ist(s_g, "Table 16(a)"), C.ist(s_l, "7.2.2.2 циклическая задержка"), C.ist(s_p, "7.? преамбула")],
  "матрица разобрана из текста: ранг 16, минимальное расстояние 8 (полный перебор) — свойство RM(2,5)", "нет: РМ (32,16) не встроен (есть блочные коды вслепую kod.блочный)"))
