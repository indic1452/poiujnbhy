"""DECT (EN 300 175-2/-3 V2.9.1) и DECT-2020 NR (TS 103 636-3 V2.2.1): R-CRC, X-CRC (4/8/12/16), B-CRC-32, скремблер B-поля,
синхрослова S-поля; DECT-2020: CRC24A/24B/16, турбокод PCCC с QPP. Сверка: каталог CRC проекта (RevEng DECT-R/-X, CRC-12/DECT),
таблица QPP LTE (tablicy/lte_qpp_36212_t5.1.3-3.json)."""
import re, sys, json
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

M3 = Tekst("istochniki/dect/en_30017503v020901p.pdf")
M2 = Tekst("istochniki/dect/en_30017502v020901p.pdf")
N3 = Tekst("istochniki/dect/ts_10363603v020201p.pdf")
ZAPISI = []
okt = lambda s: int(s.replace(" ", ""), 8)
# R-CRC
s_r = M3.odna(r"g\(x\) = x16 \+ x10 \+ x8 \+ x7 \+ x3 \+ 1 = 202 611 \(oct\)")[0]
assert okt("202 611") == mnogochlen([16, 10, 8, 7, 3, 0])
s_rinv = M3.odna(r"After inverting the coefficient rm-1 of the x0 term the generator polynomial g\(x\) divides all valid codewords")[0]
rv = crc_reveng(r"CRC-1[26]/DECT[^\"]*")
assert rv["CRC-16/DECT-R"][1] == mnogochlen([16, 10, 8, 7, 3, 0]) & 0xFFFF and rv["CRC-16/DECT-R"][5] == 1       # инверсия младшего бита = xorout 0x0001
assert rv["CRC-16/DECT-X"][1] == 0x0589 and rv["CRC-16/DECT-X"][5] == 0
# X-CRC
XC = {"2": ("x4 + 1", "21", [4, 0]), "4": ("x8 + 1", "401", [8, 0]), "8": ("x12 + x11 + x3 + x2 + x + 1", "14 016", [12, 11, 3, 2, 1, 0]),
      "16/64": ("x16 + x10 + x8 + x7 + x3 + 1", "202 611", [16, 10, 8, 7, 3, 0])}
s_x = M3.odna(r"x4 \+ 1 = 21 \(oct\) for 2-level modulation")[0]
for k, (f, o, st) in XC.items():
    if k == "8": assert okt(o) == mnogochlen(st) - 1, k      # опечатка стандарта: 14 016 (восьм.) = x12+x11+x3+x2+x — без «+1»; многочлен с «+1» = CRC-12/DECT RevEng
    else: assert okt(o) == mnogochlen(st), k
    M3.odna(re.escape(f) + r" = " + re.escape(o) + r" \(oct\)")
assert rv["CRC-12/DECT"][1] == mnogochlen(XC["8"][2]) & 0xFFF
s_tm = M3.odna(r"Table 6\.38: Number of test bits \(m\)")[0]
# B-CRC-32
s_b = M3.odna(r"g\(x\) = x32 \+ x26 \+ x23 \+ x22 \+ x16 \+ x12 \+ x11 \+ x10 \+ x8 \+ x7 \+ x5 \+ x4 \+ x2 \+ x \+ 1")[0]
# скремблер B-поля
s_scr = M3.odna(r"The scrambling sequences are based on a pseudo random sequence of length 31")[0]
M3.odna(r"For the initial state of the shift register, Q3 and Q4 are set to 1")
# синхрослова
s_s = M2.odna(r"^1110 1001 1000 1010 \(binary\)")[0]; M2.odna(r"^0001 0110 0111 0101 \(binary\)")
assert int("1010101010101010" "1110100110001010", 2) == 0xAAAAE98A and int("0101010101010101" "0001011001110101", 2) == 0x55551675
assert 0xAAAAE98A ^ 0x55551675 == 0xFFFFFFFF                                         # PP = инверсия RFP
# DECT-2020 NR
s_n_crc = N3.odna(r"gCRC24A\(D\) = \[D24 \+ D23 \+ D18 \+ D17 \+ D14 \+ D11 \+ D10 \+ D7 \+ D6 \+ D5 \+ D4 \+ D3 \+ D \+ 1\]")[0]
N3.odna(r"gCRC24B\(D\) = \[D24 \+ D23 \+ D6 \+ D5 \+ D \+ 1\]"); N3.odna(r"gCRC16\(D\) = \[D16 \+ D12 \+ D5 \+ 1\]")
s_n_tc = N3.odna(r"g0\(D\) = 1 \+ D2 \+ D3,")[0]; N3.odna(r"g1\(D\) = 1 \+ D \+ D3\.")
kus, s_qpp = N3.kusok(r"Table 6\.1\.4\.2\.3-1: Turbo code internal interleaver parameters", r"\n6\.1\.4\.\d+\s*\n|\n6\.1\.5|Rate matching")
kus = re.sub(r"=====СТР \d+=====.*?Release \d+", " ", kus, flags=re.S)
kus = re.sub(r"(?<=\|)(\d) (\d{3})(?=\|)", r"\1\2", "|".join(x.strip() for x in kus.split("\n")))       # «1 120» → 1120
ch = [int(x) for x in re.findall(r"(?<=\|)\d+(?=\|)", "|" + kus + "|")]
lte = {z["i"]: (z["K"], z["f1"], z["f2"]) for z in json.load(open(os.path.join(KOREN, "tablicy/lte_qpp_36212_t5.1.3-3.json")))["данные"]}
QPP = {}
for j in range(len(ch) - 3):
    i, K, f1, f2 = ch[j:j + 4]
    if i in lte and lte[i] == (K, f1, f2): QPP[i] = (K, f1, f2)
Kmax = max(v[0] for v in QPP.values())
assert Kmax == 6144 and len(QPP) == 188 and sorted(QPP) == list(range(1, len(QPP) + 1)), (Kmax, len(QPP))
s_z = N3.odna(r"Z = 2 048\.")[0]

def rec(imya, vid, par, gde, ist, prov, sl, sem="DECT (ETSI EN 300 175)"):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
rec("DECT R-CRC (A-поле, защищённые B-подполя)", "CRC-подобный", {"g(x)": "x16+x10+x8+x7+x3+1 = 202611 (восьм.) = 0x0589", "особенность": "инверсия последнего бита (коэффициент при x^0) — RevEng CRC-16/DECT-R (xorout 0x0001)", "A-поле": "64 бита: 48 данных + 16 R-CRC"},
    "DECT MAC: A-поле каждой пачки, B-подполя защищённых форматов", [M3.ist(s_r, "6.2.5.2"), M3.ist(s_rinv, "инверсия r(m-1)")], "восьм. запись = многочлен; = CRC-16/DECT-R каталога проекта", "ЕСТЬ в проекте: crc_katalog.py CRC-16/DECT-R")
rec("DECT X-CRC (контроль B-поля: 4/8/12/16 бит по модуляции)", "CRC-подобный",
    {"2-уровневая": "x4+1 (21 восьм.) — так в V2.9.1", "4-уровневая": "x8+1 (401)", "8-уровневая": "x12+x11+x3+x2+x+1 = 0x80F = CRC-12/DECT (восьм. «14 016» в стандарте — опечатка, верно 14 017)", "16/64-уровневая": "x16+x10+x8+x7+x3+1 (как R-CRC, без инверсии = CRC-16/DECT-X)",
     "тестовые биты": "m по табл. 6.38 (84…1216), выбор бит B-поля по формулам 6.2.5.4, после скремблирования"},
    "DECT MAC: X-поле в конце B-поля", [M3.ist(s_x, "6.2.5.4"), M3.ist(s_tm, "Table 6.38")], "восьм. записи = многочлены, кроме 8-уровневой (опечатка 14016 вместо 14017 — многочлен текста = CRC-12/DECT RevEng 0x80F); 12 и 16 бит = CRC-12/DECT и CRC-16/DECT-X проекта", "есть CRC-12/16 DECT; выбор тестовых бит — нет")
rec("DECT B-CRC-32 (IPQ)", "CRC-подобный", {"g(x)": "CRC-32 IEEE (x32+x26+…+1)", "начальное": "все единицы, инверсия"}, "DECT IPQ-форматы B-поля",
    [M3.ist(s_b, "6.2.5.5")], "многочлен = CRC-32 IEEE", "есть CRC-32 (crc_katalog.py)")
rec("DECT скремблер B-поля (m-последовательность 31, 8 последовательностей s0…s7)", "скремблер",
    {"регистр": "5 разрядов Q0…Q4, Q3 = Q4 = 1, (Q2,Q1,Q0) = номер кадра f mod 8; выход — Q4 с инверсией, переключаемой после состояния «все единицы»", "отводы": "рисунок 6.14 (в текстовом слое нет)"},
    "DECT B-поле (всегда, даже при шифровании)", [M3.ist(s_scr, "6.2.4")], "по тексту; открытого второго источника не найдено", "нет")
rec("DECT синхрослова S-поля: RFP AAAA E98A, PP 5555 1675", "синхрослово", {"RFP": "1010…1010 1110 1001 1000 1010", "PP": "0101…0101 0001 0110 0111 0101 (инверсия RFP)", "модуляция": "GFSK 1152 кбит/с"},
    "поиск пачек DECT", [M2.ist(s_s, "4.6 S-field")], "из текста; PP = побитовая инверсия RFP", "есть поиск синхрослов (sinhro.py)", sem="DECT (ETSI EN 300 175)")
rec("DECT-2020 NR: CRC24A/24B/16 + турбокод PCCC 1/3 (8 состояний, QPP, сегменты ≤ 2048)", "турбо PCCC",
    {"составляющий": "g0 = 1+D²+D³, g1 = 1+D+D³ (как LTE)", "перемежитель": f"QPP Π(i) = (f1·i + f2·i²) mod K, табл. 6.1.4.2.3-1: все {len(QPP)} размеров K = 40…6144 — та же таблица, что LTE 36.212 табл. 5.1.3-3 (используются K ≤ Z = 2048)",
     "хвост": "по 3 хвостовых бита на кодер (как LTE)", "сегментация": "Z = 2048", "CRC": "24A/24B/16 = LTE"},
    "DECT-2020 NR (ETSI TS 103 636, «DECT NR+»)", [N3.ist(s_n_crc, "6.1.2 CRC"), N3.ist(s_n_tc, "6.1.4.2 Turbo"), N3.ist(s_qpp, "Table 6.1.4.2.3-1"), N3.ist(s_z, "Z = 2048")],
    f"все {len(QPP)} строк (i, K, f1, f2) найдены в тексте и совпали с таблицей QPP LTE (которая сверена с srsRAN)", "есть PCCC (turbo.py); QPP LTE — таблица готова", sem="DECT-2020 NR (ETSI TS 103 636)")

if __name__ == "__main__":
    print(len(ZAPISI), "записей", len(QPP), Kmax)
