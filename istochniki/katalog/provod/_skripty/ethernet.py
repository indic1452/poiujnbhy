"""Ethernet IEEE 802.3 и консорциумы: FEC уровня PHY.
Первоисточники: IEEE Std 802.3-2012 (разделы 3–5, зеркало ebook.pldworld.com), открытые материалы рабочих групп
ieee802.org (базовые предложения, выдержки черновиков), спецификации Ethernet Technology Consortium (25G/50G, LL-FEC, 800G).
Проверки — тестовыми векторами приложений стандарта (74A, 76A, 114A, LL-FEC прил. B) и примерами из презентаций групп."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
S3 = Tekst("istochniki/ieee8023/standart/802.3-2012_section3.pdf")
S4 = Tekst("istochniki/ieee8023/standart/802.3-2012_section4.pdf")
S5 = Tekst("istochniki/ieee8023/standart/802.3-2012_section5.pdf")
PROJ = "/home/user/poiujnbhy/src/reportgen/potok"
hexbity = lambda h: [int(c) for x in h for c in format(int(x, 16), "04b")]


def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


SEM = "Ethernet IEEE 802.3"
SLEPOJ_RS = ("нет готового разборщика; слепой РС (rs_bch.py) находит поле и корни по словам известной длины, "
             "но нужна раскладка кадра (транскодирование, чередование символов) — её в проекте нет")

# ---------- Clause 74: FireCode (2112,2080) + PN-2112 ----------
g74 = iz_stepenej([32, 23, 21, 11, 2, 0])
s74 = S5.odna(r"The FEC code used is a shortened cyclic code \(2112, 2080\)")[0]
S5.odna(r"constructed by shortening the cyclic code \(42987, 42955\)")
assert poryadok_mnogochlena(g74) == 42987                    # период g(x) = длина исходного циклического кода
s74g = S5.odna(r"The generator polynomial g\(x\) for the \(2112, 2080\) parity-check bits")[0]
s74t = S5.odna(r"transcode bit is then XOR'ed with data bit 8")[0]
s74pn = S5.odna(r"PN-2112 is a pseudo-noise sequence of length 2112")[0]
k1, s74a = S5.kusok(r"Table 74A–1— 64B/66B block stream", r"=====СТР 826")
bl = re.findall(r"\n(10|01)\n([0-9a-f]{16})", k1)
k2, _ = S5.kusok(r"Table 74A–2— Transcoded FEC block", r"Table 74A–3")
tb = re.findall(r"\n([01])\n([0-9a-f]{16})", k2)
par74 = re.search(r"Parity hex \[0:31\] \n([0-9a-f]{8})", k2).group(1)
assert len(bl) == len(tb) == 32
assert all(int(t) == (int(sh[1]) ^ hexbity(p)[8]) for (sh, p), (t, _) in zip(bl, tb))      # T = SH.1 xor бит 8
blok = []
for t, p in tb: blok += [int(t)] + hexbity(p)
assert cikl_kodirovat(blok, g74) == hexbity(par74)
k4, _ = S5.kusok(r"Table 74A–4— PN-2112 sequence", r"Table 74A–5", posl=True)
pn_tab = hexbity("".join(re.findall(r"\n([0-9a-f]{16})", k4)))[:2112]
S = [1 - (i % 2) for i in range(58)][::-1]          # S57 = 1, S(i−1) = S(i) xor 1  → S[i] = Si
S = [(1 if (57 - i) % 2 == 0 else 0) for i in range(58)]
pn = []
for _ in range(2112):
    o = S[38] ^ S[57]; pn.append(o); S = [o] + S[:-1]
assert pn == pn_tab
k3, _ = S5.kusok(r"Table 74A–3— FEC block scrambled with PN-2112 sequence", r"=====СТР 827", posl=True)
skr = hexbity("".join(re.findall(r"\n([0-9a-f]{16})", k3)))[:2112]
assert skr == [a ^ b for a, b in zip(blok + hexbity(par74), pn)]
rec("Ethernet BASE-R FEC (Clause 74): укороченный циклический (2112,2080) «FireCode» + PN-2112", SEM, "циклический (укороченный, исправление пачек)",
    {"n,k": "2112, 2080 (32 строки по 65 бит + 32 проверочных бита)", "g(x)": "x^32 + x^23 + x^21 + x^11 + x^2 + 1 (ур. 74–1)",
     "исходный код": "циклический (42987, 42955) — период g(x) = 42987 (проверено вычислением)", "исправляет": "пачку до 11 бит на блок",
     "транскодирование": "66b → 65b: первый бит синхрозаголовка отбрасывается, T = SH.1 XOR бит 8 блока 64B/66B",
     "скремблер": "PN-2112: r(x) = 1 + x^39 + x^58 (как 49.2.6), в начале каждого блока S57 = 1, Si−1 = Si xor 1 (1010…); выход S38 xor S57 → S0",
     "порядок бит": "слева направо в строке, строки сверху вниз; проверочные в конце блока", "линии": "10GBASE-KR, 40GBASE-KR4/CR4, 100GBASE-CR10; 25GBASE-R (Cl.108) и FC 16GFC — та же FireCode"},
    "10G/40G/100G объединительные панели и медь (KR/CR), опционально 25GBASE-R; Fibre Channel 16GFC",
    [S5.ist(s74, "74.7.1 FEC code"), S5.ist(s74g, "74.7.4.4, ур. 74–1"), S5.ist(s74t, "74.7.4.2 транскодирование"), S5.ist(s74pn, "74.7.4.4.1 PN-2112"),
     S5.ist(s74a, "Annex 74A, табл. 74A–1…74A–4")],
    "тестовый вектор приложения 74A: из 32 блоков 64B/66B (табл. 74A–1) получены T-биты табл. 74A–2, 32 проверочных бита d96e7685, "
    "последовательность PN-2112 (табл. 74A–4, все 2112 бит) и скремблированный блок (табл. 74A–3) — всё совпало",
    "нет: 64b/66b и скремблер x^58 есть (pcs.py), FEC-кадр 2112 бит и PN-2112 — нет; просто добавить (циклический код + ПСП)")

# ---------- Clause 76: 10G-EPON RS(255,223) ----------
s76 = S5.odna(r"76\.3\.2\.4\.1 FEC Algorithm \[RS\(255,223\)\]")[0]
k, s76a = S5.kusok(r"Table 76A–4—223 octet input buffer", r"Table 76A–5—32", posl=True)
D = []
for m in re.finditer(r"n= (\d+)\n((?:[0-9A-F]{2}\n){1,16})", k): D += [int(x, 16) for x in m.group(2).split()]
k, _ = S5.kusok(r"Table 76A–5—32 parity octets computed by FEC encodera", r"Table 76A–6", posl=True)
P = []
for m in re.finditer(r"n= (\d+)\n((?:[0-9A-F]{2}\n){1,16})", k): P += [int(x, 16) for x in m.group(2).split()]
assert len(D) == 223 and len(P) == 32 and rs_kodirovat(D, 0x11D, 0, 32) == P
rec("10G-EPON RS(255,223) (Clause 76, 802.3av)", SEM, "РС",
    {"поле": "GF(2^8), x^8 + x^4 + x^3 + x^2 + 1, α = 0x02", "g(Z)": "∏_{i=0}^{31} (Z − α^i)", "n,k,t": "255, 223, 16",
     "укорочение": "кодовое слово: 27 блоков 65b (синхробит + 64) с 29 нулями спереди → 223 октета; 32 проверочных октета — 4 блока 66b",
     "порядок бит": "октеты данных LSB первым (3.1.1); D222 первый", "скремблер": "64B/66B x^58 + x^39 + 1 (49.2.6) до FEC"},
    "10GBASE-PR, 10/1GBASE-PRX (10G-EPON), нисходящий и восходящий",
    [S5.ist(s76, "76.3.2.4.1"), S5.ist(s76a, "Annex 76A, табл. 76A–4, 76A–5")],
    "тестовый вектор приложения 76A: 223 октета табл. 76A–4 → 32 проверочных октета табл. 76A–5 совпали (fcr = 0)",
    "частично: RS над GF(256) и исправление есть (rs_bch.py), раскладка 27×65b + паритет в 66b-блоках — нет")

# ---------- Clause 65: 1G-EPON RS(255,239) ----------
s65 = S5.odna(r"the Reed-Solomon code \(255,239,8\) over the Galois Field")[0]
s65b = S5.odna(r"The data is partitioned into 239-symbol frames")[0]
rec("1G-EPON FEC RS(255,239,8) (Clause 65, 802.3ah)", SEM, "РС",
    {"поле": "GF(2^8), x^8+x^4+x^3+x^2+1, α = 0x02", "g": "как ITU-T G.975 (корни α^0…α^15)", "n,k,t": "255, 239, 8",
     "раскладка": "кадр Ethernet после /S/ делится на блоки по 239 октетов (последний — укорочен), 16 проверочных после каждого; "
                  "кадр FEC ограничен последовательностями /S_FEC/ и /T_FEC/ (65.2.3.2)", "порядок бит": "d0 — LSB (3.1.1)"},
    "1000BASE-PX (EPON) с FEC", [S5.ist(s65, "65.2.3.1"), S5.ist(s65b, "65.2.3.2")],
    "многочлен и корни = G.975 (запись о G.975 в этом каталоге); примитивность x^8+x^4+x^3+x^2+1 проверена",
    "частично: RS(255,239) есть (otn.py/rs_bch.py), кадр EPON FEC (укорочение по длине кадра) — нет")

# ---------- Clause 55: 10GBASE-T LDPC(1723,2048) ----------
s55 = S4.odna(r"the remaining 1723 shall be encoded by the LDPC\(1723, 2048\) generator")[0]
s55a = S4.odna(r"55A\.1 The generator matrix")[-3] if False else S4.naiti(r"^55A\.1 The generator matrix")[-1][0]
s55c = S4.odna(r"Figure 55–11—CRC8")[0]
s55m = S4.odna(r"55\.3\.2\.2\.19 DSQ128 bit mapping")[0]
pr = json.load(open(os.path.join(PROJ, "data", "ldpc_prochie.json")))["10gbase-t-2048-1723"]
stroki = pr["строки"] if isinstance(pr["строки"], list) else json.loads(pr["строки"])
rows = [sum(1 << c for c in r) for r in stroki]
rang = 0; basis = {}
for r in rows:
    while r:
        h = r.bit_length() - 1
        if h in basis: r ^= basis[h]
        else: basis[h] = r; rang += 1; break
ves_str = sorted(set(len(r) for r in stroki)); ves_st = {}
for r in stroki:
    for c in r: ves_st[c] = ves_st.get(c, 0) + 1
assert len(stroki) == 384 and 2048 - rang == 1723 and ves_str == [32] and set(ves_st.values()) == {6}
rec("10GBASE-T LDPC(2048,1723) + CRC8 + DSQ128 (Clause 55, 802.3an)", SEM, "LDPC (с некодированными битами, ТКМ-подобная раскладка)",
    {"n,k": "2048, 1723 (325 проверочных)", "кадр": "50 блоков 64B/65B + CRC8 + 1 доп. бит = 3259 бит: 1536 некодированных (3 на символ DSQ128) + 1723 → LDPC",
     "CRC8": "C(x) = 1 + x + x^5 + x^6 + x^8 (рис. 55–11)", "G": "Annex 55A: G.txt (1723 строки) в matrices.zip — на сайте IEEE, в открытом доступе не найден",
     "H": "(6,32)-регулярная: 384 строки веса 32, столбцы веса 6 (по матрице AFF3CT проекта)",
     "модуляция": "DSQ128 (3 некодир. + 4 кодир. бита → 2×PAM16), 4D-PAM16, 800 Мсимв/с", "порядок": "x0 первым в кодер и в линию",
     "родственные": "2.5G/5GBASE-T (802.3bz) и 25G/40GBASE-T (802.3bq, некодированные биты защищены RS(140,136) GF(2^11)) — тот же LDPC"},
    "10GBASE-T (витая пара), 2.5G/5GBASE-T, 25G/40GBASE-T",
    [S4.ist(s55, "55.3.2.2.18 LDPC encoder"), S4.ist(s55c, "CRC8"), S4.ist(s55m, "DSQ128"), S4.ist(s55a, "Annex 55A (G.txt, H.txt — отдельным файлом)")],
    f"матрица проекта (AFF3CT 10GBPS-ETHERNET_1723_2048.alist): 384 строки веса 32, все столбцы веса 6, ранг {rang} → k = 2048 − {rang} = 1723, как в 55.3.2.2.18; "
    "сверка с G.txt IEEE невозможна (файл закрыт Cloudflare, копии в архиве нет)",
    "ЕСТЬ в проекте: ldpc_std (10gbase-t-2048-1723, источник один — AFF3CT); раскладки DSQ128/CRC8 нет")

# ---------- Clause 40: 1000BASE-T 4D-PAM5 решётчатый ----------
s40 = S3.odna(r"Transmit utilizes a three-state convolutional encoder")[0]
s40b = S3.odna(r"40\.3\.1\.3\.4 Generation of bits Sdn\[8:0\]")[0]
rec("1000BASE-T: 4D-PAM5 решётчатое кодирование (8 состояний) (Clause 40)", SEM, "свёрточный (ТКМ, 4D)",
    {"кодер": "систематический сверточный с 3 битами состояния csn[2:0]: csn[1] = Sdn[6] ^ csn−1[0], csn[2] = Sdn[7] ^ csn−1[1], csn[0] = csn−1[2]; Sdn[8] = csn[0]",
     "символ": "9 бит Sdn[8:0] → четырёхмерный символ PAM5 (4 пары), разбиение на подмножества (табл. 40–1/40–2)",
     "сброс": "csresetn = tx_enablen−2 AND NOT tx_enablen — обнуление в конце кадра", "скремблер": "побочный x^33 + x^13 + 1 (ведущий) / x^33 + x^20 + 1 (ведомый) — 40.3.1.3.1"},
    "Gigabit Ethernet по витой паре", [S3.ist(s40, "40.3.1.3"), S3.ist(s40b, "40.3.1.3.4")],
    "по тексту стандарта (единственный открытый источник); таблицы разбиения PAM5 — рисунки, в текстовом слое частично",
    "нет (ТКМ 4D-PAM5 не реализован)")

# ---------- Clause 91/108/119/134/161: RS(528,514) и RS(544,514) ----------
L = Tekst("istochniki/ieee8023/bj/langhammer_01_0512.pdf")
A = Tekst("istochniki/ieee8023/bj/anslow_01a_0712.pdf")
G = Tekst("istochniki/ieee8023/bj/gustlin_01_0312.pdf")
B = Tekst("istochniki/ieee8023/bj/brown_01a_0512.pdf")
BS = Tekst("istochniki/ieee8023/bs/gustlin_3bs_03_0317.pdf")
p10 = iz_stepenej([10, 3, 0]); assert primitivnyj(p10)
sL = L.odna(r"be x10\+ x3\+1\. The first root of the generator polynomial shall be 1")[0]
sA = A.odna(r"The resulting 14 10-bit parity symbols from RS\(528,514,7,10\) would be")[0]
chisla = [int(x) for x in A.odna(r"^1019, 521, 222")[2].replace(",", " ").replace("decimal", "").split()]
assert rs_kodirovat([0] * 513 + [0x100], p10, 0, 14) == chisla
gk = G.odna(r"^0x1b0 0x122 0x3b1")
g528 = rs_g(p10, 0, 14)
assert [int(x, 16) for x in gk[2].split()] == g528[1:][::-1] + [1] if False else [int(x, 16) for x in gk[2].split()] == g528[::-1][:-1] + [1]
cw = G.kusok(r"k = 514 symbol values from 1023 decremented to 510", r"=====СТР 21")[0]
chk = [int(x) for x in re.search(r"\n(451 952 674[\d ]+) decimal", cw).group(1).split()]
assert rs_kodirovat(list(range(1023, 509, -1)), p10, 0, 14) == chk
sB = B.odna(r"RS\(544,514,T=15,M=10\)")[0]
k, sT = BS.kusok(r"Table 119–3—Coefficients of the generator polynomial gi \(decimal\)", r"=====СТР")
chis = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", k)]
gi = dict(zip(chis[0::2], chis[1::2]))
g544 = rs_g(p10, 0, 30)[::-1]            # g0 … g30
assert sorted(gi) == list(range(31)) and all(gi[i] == g544[i] for i in gi), gi
sTi = BS.odna(r"The interleaving of two codewords for the 400GBASE-R PCS shall follow this procedure")[0]
sTe = BS.odna(r"The PCS shall implement an RS\(544,514\) based FEC encoder")[0]
tablica("ethernet_rs544_g", {"g_i (i=0..30)": g544, "RS(528,514) g_i (i=0..14)": g528[::-1]},
        "Коэффициенты порождающих многочленов RS(544,514) и RS(528,514), GF(2^10) по x^10+x^3+1, корни α^0…α^(2t−1)",
        [BS.ist(sT, "Table 119–3"), G.ist(gk[0], "коэффициенты g для RS(528,514)")],
        "вычислено по x^10+x^3+1 и корням α^0…; совпало со всеми 30 значениями табл. 119–3 и списком Gustlin_01_0312")
rec("RS(528,514) «KR4 FEC» (Clause 91, 802.3bj; также Cl.108 25GBASE-R)", SEM, "РС",
    {"поле": "GF(2^10), x^10 + x^3 + 1", "g(x)": "∏_{j=0}^{13} (x − α^j)", "n,k,t,m": "528, 514, 7, 10",
     "вход": "транскодирование 4×66b → 257b, 20 блоков 257b = 5140 бит = 514 символов", "порядок": "символы 10 бит, LSB первым (802.3)",
     "распределение": "100G: по 4 FEC-полосам символами по 10 бит; 25G (Cl.108): одна полоса", "маркеры": "AM — в начале слова; 25G: каждые 1024 слова (спец. консорциума 25G/50G)"},
    "100GBASE-KR4/CR4/SR4/…, 25GBASE-CR/KR/SR (Cl.108), CPRI 24G (опц.), Fibre Channel 32GFC",
    [L.ist(sL, "91.4.2.9 (предложение текста)"), G.ist(gk[0], "пример g и кодового слова"), A.ist(sA, "пример проверочных символов")],
    "два независимых примера: (1) Anslow_01a_0712 — слово 513×0x000 + 0x100 → 14 проверочных 3FB…30C совпали; (2) Gustlin_01_0312 — данные 1023…510 → 451 952 … 191 совпали; коэффициенты g — со списком",
    SLEPOJ_RS)
rec("RS(544,514) «KP4 FEC» (Cl.91 100GBASE-KP4; Cl.119 200/400G; Cl.134 50G; Cl.161/802.3ck 100G на полосу)", SEM, "РС",
    {"поле": "GF(2^10), x^10 + x^3 + 1", "g(x)": "∏_{j=0}^{29} (x − α^j), коэффициенты — табл. 119–3 → tablicy/ethernet_rs544_g.json", "n,k,t,m": "544, 514, 15, 10",
     "400G/200G": "два слова cA/cB чередуются по 10-битовым символам (119.2.4.6: tx_out<16k+2j> = cA<543−8k−j> …)",
     "вход": "транскодирование 256b/257b, 20 блоков → 514 символов", "800G (ETC)": "2×400G, 4 слова на интерфейсе"},
    "100GBASE-KP4, 50GBASE-R (Cl.134, 802.3cd), 200G/400GBASE-R (Cl.119, 802.3bs), 100G на полосу (802.3ck), 800G/1.6T (802.3df/dj), OTN FlexO (G.709.1)",
    [B.ist(sB, "выбор кода KP4"), BS.ist(sTe, "119.2.4.6 (черновик D3.1)"), BS.ist(sT, "Table 119–3"), BS.ist(sTi, "чередование двух слов")],
    "30 коэффициентов g_i табл. 119–3 (черновик P802.3bs/D3.1) совпали с вычисленными по x^10+x^3+1 и корням α^0…α^29",
    SLEPOJ_RS)

# 802.3cd, 802.3ck — отдельные ссылки на выбор RS(544,514)
CD = Tekst("istochniki/ieee8023/cd/nicholl_3cd_01a_0516.pdf"); sCD = CD.odna(r"FEC encoder is RS\(544,514\) in a 1x50G architecture")[0]
CK = Tekst("istochniki/ieee8023/ck/nicholl_3ck_adhoc_01b_042419.pdf"); sCK = CK.odna(r"The PCS shall implement an RS\(544,514\) based FEC encoder")[0]
ZAPISI[-1]["источник"] += [CD.ist(sCD, "50GBASE-R (802.3cd)"), CK.ist(sCK, "802.3ck, текст PCS")]

# ---------- 802.3df/dj: внутренний код (128,120) ----------
DJ = Tekst("istochniki/ieee8023/dj/patra_3dj_01b_2303.pdf"); sDJ = DJ.odna(r"RS\(544,514\) \+ Hamming \(128,120\) based")[0]
DF = Tekst("istochniki/ieee8023/df/bliss_3df_01_220929.pdf"); sDF = DF.odna(r"Proposal for a specific \(128,120\) extended inner")[0]
HE = Tekst("istochniki/ieee8023/dj/he_3dj_03b_2307.pdf"); sHE = HE.odna(r"Binary\(128,120\) code has been adopted for 200G/lane optical PMDs")[0]
HU = Tekst("istochniki/ieee8023/dj/huang_3dj_01a_2405.pdf"); sHU = HU.odna(r"Details for Hamming\(68,60\) Code")[0]
rec("802.3dj/df: каскад RS(544,514) (внешний) + внутренний расширенный Хэмминг (128,120) — 200G на полосу, оптика", SEM, "каскадный: РС + Хэмминг (мягкое решение)",
    {"внешний": "RS(544,514) Cl.119", "внутренний": "(128,120) расширенный код Хэмминга/БЧХ (укороченный), 8 проверочных бит; перемежение 8:1 / 16:1 перед внутренним кодером",
     "варианты": "Hamming(68,60) — предложение для 800G-LR1/ZR (huang_3dj_01a_2405)", "самосинхронизация": "по границам внутренних слов (he_3dj_03b_2307)"},
    "оптические PMD 200 Гбит/с на полосу (802.3dj/802.3df)",
    [DJ.ist(sDJ), DF.ist(sDF, "конкретный порождающий"), HE.ist(sHE), HU.ist(sHU)],
    "только по презентациям рабочей группы; утверждённый текст 802.3dj (Cl.177) в открытом доступе не найден — порождающую матрицу сверить не с чем",
    "нет")

# ---------- 802.3bp / ch / cy / bq / dg: автомобильные и витая пара ----------
BP = Tekst("istochniki/ieee8023/bp/Lo_3bp_02_0914.pdf"); sBP = BP.odna(r"Use RS\(450, 406, 29\) code for the FEC")[0]
rec("1000BASE-T1 RS(450,406) над GF(2^9) (802.3bp, Cl.97)", SEM, "РС",
    {"n,k,m": "450, 406, 9 (t = 22)", "данные": "405 символов = 45 блоков 80b/81b, 406-й символ — резерв 111 101 010", "порядок": "LSB символа первым",
     "модуляция": "PAM3, 3 бита → 2 троичных символа (табл. на стр. 3)", "многочлен поля": "в базовом предложении — «TBD»; утверждённый текст Cl.97 в открытом доступе не найден"},
    "автомобильный Ethernet 1 Гбит/с (одна пара)", [BP.ist(sBP, "базовое предложение FEC")],
    "по базовому предложению; многочлен поля и g(x) не опубликованы в открытых материалах — определять слепо (rs_bch.py перебирает примитивные многочлены степени 9)",
    "частично: слепой РС rs_bch.py (m = 9) найдёт поле и корни")
CH = Tekst("istochniki/ieee8023/ch/tu_3ch_01b_0718.pdf"); sCH = CH.odna(r"RS FEC Proposal for Multi-Gigabit")[0]
rec("2.5G/5G/10GBASE-T1: RS над GF(2^10) (802.3ch, Cl.149) — кандидаты RS(360,326) и др.", SEM, "РС",
    {"m": "10", "кандидаты": "таблица на стр. 5 (A1 RS(576,521), A2 RS(648,586), …); перемежение L = 1, 2, 4", "OAM": "1 символ резервируется под OAM",
     "итог": "в утверждённом 802.3ch — RS(360,326) (по ссылке в jonsson_tu_castrillon_3cy: «w.r.t RS(360,326)»); многочлен поля в открытых материалах не найден"},
    "автомобильный Ethernet 2.5–10 Гбит/с", [CH.ist(sCH, "кандидаты RS")],
    "по презентациям; сверить g(x) не с чем", "частично: слепой РС (m = 10)")
CY = Tekst("istochniki/ieee8023/cy/jonsson_tu_castrillon_3cy_01_01_11_22.pdf"); sCY = CY.odna(r"RS\(936,846\) with L=1,2,4,8 with editorial license")[0]
CY0 = Tekst("istochniki/ieee8023/cy/jonsson_tu_castrillon_3cy_01_12_21_21.pdf"); sCY0 = CY0.odna(r"^w\.r\.t$")[0]
rec("25GBASE-T1 RS(936,846) с перемежением L = 1, 2, 4, 8 (802.3cy)", SEM, "РС",
    {"n,k": "936, 846 (t = 45, m = 10)", "перемежение": "L = 1, 2, 4, 8", "сравнение": "RS(360,326) 802.3ch — табл. на стр. 4"},
    "автомобильный Ethernet 25 Гбит/с", [CY.ist(sCY, "принятое предложение"), CY0.ist(sCY0, "таблица вариантов")],
    "по презентациям (принятое движение); g(x) не опубликован в открытых материалах", "частично: слепой РС (m = 10)")
BQ = Tekst("istochniki/ieee8023/bq/wu_3bq_01a_1114.pdf"); sBQ = BQ.odna(r"Primitive generator polynomial: p\(x\)=x11\+x2\+1")[0]
assert primitivnyj(iz_stepenej([11, 2, 0]))
rec("25G/40GBASE-T: RS(140,136) над GF(2^11) для «некодированных» бит + LDPC(2048,1723) (802.3bq)", SEM, "каскадный: РС + LDPC",
    {"RS": "RS(140,136), m = 11, p(x) = x^11 + x^2 + 1, g(x) = (x − α^0)(x − α^1)(x − α^2)(x − α^3), t = 2", "LDPC": "как 10GBASE-T (2048,1723)",
     "транскодирование": "4×64/65 → 256/257; вместо CRC8 — 44 проверочных бита RS"},
    "25GBASE-T, 40GBASE-T", [BQ.ist(sBQ, "поле и g(x)")], "примитивность x^11+x^2+1 проверена; значения по презентации (текст Cl.113 закрыт)",
    "частично: LDPC есть, RS m = 11 — слепо")
DG = Tekst("istochniki/ieee8023/dg/Curran_3dg_01a_07152024.pdf"); sDG = DG.odna(r"Use a Reed Solomon FEC code with a Galois Field of 8 and RS\(128, 122, 3, 8\)")[0]
rec("100BASE-T1L (802.3dg): RS(128,122) над GF(2^8), t = 3", SEM, "РС",
    {"n,k,t,m": "128, 122, 3, 8", "модуляция": "PAM3, 8b6T, 80 Мсимв/с", "примечание": "режим защиты от пачек; предложение 2024"},
    "100BASE-T1L (предложение в 802.3dg), PAM-3 8b6T", [DG.ist(sDG)], "по презентации; g(x) не опубликован", "частично: слепой РС (m = 8)")

# ---------- 802.3bv 1000BASE-RH: BCH по тестовым векторам 114A ----------
BV = Tekst("istochniki/ieee8023/bv/IEEE_P802d3bv_114A_perezaranda_280616.pdf")
k, sBV = BV.kusok(r"Table 114A–1—BCH\(896, 720\) codeword", r"=====СТР 2")
inf = "".join(re.findall(r"\n([0-9a-f]{4,16})(?=\n)", k.split("Parity bits")[0])); par = "".join(re.findall(r"\n([0-9a-f]{4,16})(?=\n)", k.split("Parity bits")[1]))
p11 = iz_stepenej([11, 2, 0])
assert cikl_kodirovat(hexbity(inf)[:720], bch_g(p11, range(1, 32, 2))) == hexbity(par)[:176]
kandidaty = [P for P in range(1 << 11, 1 << 12) if primitivnyj(P) and stepen(bch_g(P, range(1, 32, 2))) == 176
             and cikl_kodirovat(hexbity(inf)[:720], bch_g(P, range(1, 32, 2))) == hexbity(par)[:176]]
assert kandidaty == [p11]
k2, sBV2 = BV.kusok(r"Table 114A–2—BCH\(1976, 1668\) codeword", r"$")
hx = lambda t: "".join(l.strip() for l in t.split("\n") if re.fullmatch(r"[0-9a-f]{1,16}", l.strip()))
inf2 = hx(k2.split("Parity bits")[0]); par2 = hx(k2.split("Parity bits")[1])
assert len(inf2) * 4 >= 1668 and len(par2) * 4 >= 308, (len(inf2), len(par2))
assert cikl_kodirovat(hexbity(inf2)[:1668], bch_g(p11, range(1, 56, 2))) == hexbity(par2)[:308]
rec("1000BASE-RH (POF, 802.3bv): БЧХ(896,720) заголовка и БЧХ(1976,1668) полезной нагрузки (MLCC)", SEM, "БЧХ (+ многоуровневое кодирование MLCC)",
    {"поле": "GF(2^11), x^11 + x^2 + 1 (восстановлено по векторам 114A: единственный из 176 примитивных многочленов степени 11)",
     "БЧХ(896,720)": "t = 16, корни α^1…α^32, укорочен из (2047,1871); физический заголовок + CRC16 после скремблера",
     "БЧХ(1976,1668)": "t = 28, корни α^1…α^56; первый уровень MLCC (16-PAM), остальные уровни некодированы",
     "порядок бит": "в таблицах: MSB каждого hex-знака первым"},
    "Gigabit Ethernet по пластиковому оптоволокну (1000BASE-RHA/RHB/RHC)",
    [BV.ist(sBV, "Annex 114A.1 (черновик D2.3)"), BV.ist(sBV2, "Annex 114A.2")],
    "оба примера приложения 114A (720+176 и 1668+308 бит) удовлетворяют БЧХ с x^11+x^2+1 и корнями α^1…α^2t; перебор всех примитивных многочленов степени 11 даёт ровно этот",
    "частично: слепой БЧХ (rs_bch.py) + многочлен известен; MLCC-раскладки нет")

# ---------- 802.3bn EPoC, 802.3ca 25G-EPON ----------
BN = Tekst("istochniki/ieee8023/bn/prodan_3bn_01_0313.pdf"); sBN = BN.odna(r"LDPC \(16200, 14400\)")[0]
BN2 = Tekst("istochniki/ieee8023/bn/shen_3bn_01_0913.pdf"); sBN2 = BN2.odna(r"Adopt the typo-corrected LDPC \(5940, 5040\) code")[0]
rec("EPoC (802.3bn, Cl.101): LDPC (16200,14400), (5940,5040), (1120,840) — общие с DOCSIS 3.1", SEM, "LDPC",
    {"коды": "нисходящий (16200,14400); восходящий (16200,14400), (5940,5040), (1120,840) — матрицы как DOCSIS 3.1 (см. семейство DOCSIS)",
     "исправление": "в (5940,5040) исправлена опечатка черновика (shen_3bn_01_0913)"},
    "Ethernet Passive Optical Network over Coax", [BN.ist(sBN), BN2.ist(sBN2)],
    "совпадение с DOCSIS 3.1 восходящими кодами — по тексту презентаций; матрицы DOCSIS 3.1 в проекте уже сверены с черновиком 802.3bn (docs/20-potok.md)",
    "ЕСТЬ в проекте: ldpc_std docsis31-* (те же коды)")
CA = Tekst("istochniki/ieee8023/ca/laubach_3ca_1b_0118.pdf"); sCA = CA.odna(r"The LDPC\(18493,15677\) \[13x75x256\] 0\.848 FEC code was adopted for the downstream")[0]
CA2 = Tekst("istochniki/ieee8023/ca/laubach_3ca_1_0518.pdf"); sCA2 = CA2.odna(r"LDPC \(16888,14328\)")[0]
rec("25G/50G-EPON (802.3ca): квазициклический LDPC, циркулянты 256×256 (промежуточные варианты (18493,15677), (16888,14328))", SEM, "LDPC (QC)",
    {"структура": "матрица-основа 13×75 (затем 12×69) циркулянтов 256×256, скорость ≈ 0.848", "итог": "в стандарте — LDPC(17280,14592) с укорочением/выкалыванием; тот же код — ITU-T G.9804.2 (запись в семействе PON)"},
    "25G/50G-EPON", [CA.ist(sCA, "принятие для нисходящего"), CA2.ist(sCA2)],
    "окончательная матрица — в ITU-T G.9804.2 (открыт), см. запись PON; презентации 802.3ca дают историю выбора",
    "нет (матрицу можно собрать из G.9804.2)")

# ---------- 802.3ct/cw ----------
CT = Tekst("istochniki/ieee8023/ct/bruckman_3ct_01_0120.pdf"); sCT = CT.odna(r"Comments and proposals regarding SC-FEC")[0]
CT2 = Tekst("istochniki/ieee8023/ct/chen_3ct_02_0719.pdf"); sCT2 = CT2.odna(r"Since IEEE 802.3ct has chosen CFEC as the selection of 400GbE 80km FEC")[0]
rec("100GBASE-ZR (802.3ct): SC-FEC (лестничный код G.709.2 OTU4-SC) и 400GBASE-ZR (802.3cw): CFEC", SEM, "каскадный (лестничный БЧХ + …)",
    {"100GBASE-ZR": "кадр SC-FEC на основе OTU4-SC ITU-T G.709.2 (лестничный код + внешний) — см. запись G.709.2", "400GBASE-ZR": "CFEC OIF 400ZR (лестничный + Хэмминг(128,119)) — см. запись OIF",
     "обратный RS": "опц. «Inverse RS-FEC» снимает Cl.91 перед ZR-FEC (nicholl_3ct_01a_0319)"},
    "DWDM 80 км", [CT.ist(sCT, "SC-FEC"), CT2.ist(sCT2, "CFEC для 400G ZR")], "по презентациям; определения — в G.709.2 и OIF-400ZR", "нет")

# ---------- 10PASS-TS / 2BASE-TL ----------
s62 = S5.odna(r"62\.2\.4\.3 Changes to 9\.3\.5, “Framing”")[0]
s63 = S5.naiti(r"^63\.[0-9.]+ .*(TC-PAM|TCPAM|Trellis)", re.I)
rec("10PASS-TS и 2BASE-TL (Ethernet по телефонной паре, 802.3ah Cl.61–63)", SEM, "каскадный (РС + ТКМ) — заимствован из DSL",
    {"10PASS-TS": "PMA/PMD = VDSL (ITU-T G.993.1 / ANSI T1.424): РС над GF(256) + перемежение; изменения — 62.2/62.3",
     "2BASE-TL": "PMA/PMD = SHDSL (ITU-T G.991.2): TC-PAM 16/32 с решётчатым кодом", "64/65-октетное кадрирование": "Cl.61 (TC-подуровень, CRC)"},
    "Ethernet в первой миле по меди", [S5.ist(s62, "Cl.62 изменения к VDSL")] + ([S5.ist(s63[0][0], "Cl.63")] if s63 else []),
    "коды — см. записи G.993.1 и G.991.2 семейства DSL", "нет")

# ---------- Консорциумы ----------
SEMK = "Ethernet Technology Consortium (25G/50G, LL-FEC, 800G)"
LL = Tekst("istochniki/etc/LL-FEC-Specification-1.0.pdf")
sLL = LL.odna(r"RS\(n = 272, k =")[0]; sLLg = LL.odna(r"The Generating polynomial used for all LL-FEC operational modes is identical")[0]
import pymupdf
doc = pymupdf.open(os.path.join(KOREN, "istochniki/etc/LL-FEC-Specification-1.0.pdf"))
proverki = []
for pg, tab in ((14, "Table 4"), (15, "Table 6")):
    ws = [w[4] for w in doc[pg].get_text("words") if re.fullmatch(r"[0-9a-f]{30,}", w[4])]
    rows = [w for w in ws if len(w) in (79, 80, 40)][:9]
    nedost = sum(1 for r in rows if len(r) == 79)
    rows = [r if len(r) != 79 else "0" + r for r in rows]              # в PDF потерян ведущий 0 строки
    b = "".join(format(int(c, 16), "04b") for c in "".join(rows)); sym = [int(b[i:i + 10], 2) for i in range(0, 2720, 10)]
    assert len(b) == 2720 and rs_kodirovat(sym[:258], p10, 0, 14) == sym[258:]
    proverki.append(f"{tab}: 258 символов → 14 проверочных совпали (в PDF у {nedost} строк потерян ведущий 0 — восстановлен)")
rec("LL-FEC RS(272,257+1) (Low Latency RS, 25/50G Ethernet Consortium)", SEMK, "РС (укороченный)",
    {"код": "RS(272,258), t = 7, m = 10 — укорочение того же кода, что RS(528,514) (g = ур. 91-1)", "данные": "10 блоков 257b = 2570 бит + 10-битовый «пад» (PRBS9 или константа) = 258 символов",
     "маркеры": "AM каждые 8192 слова", "назначение": "половина задержки RS(544) при той же скорости 272/257 = 544/514"},
    "50/100/200GbE с низкой задержкой (ETC)", [LL.ist(sLL, "3 LL-FEC"), LL.ist(sLLg, "3.1.1 g(x)"), LL.ist(15, "Annex B, табл. 4"), LL.ist(16, "табл. 6")],
    "; ".join(proverki), SLEPOJ_RS)
C25 = Tekst("istochniki/etc/25G-50G-Specification-FINAL.pdf"); s25 = C25.odna(r"3\.2\.3 RS-FEC \(Clause 91\) Sublayer")[0]
rec("25G/50G Ethernet Consortium: RS(528,514) 25G и RS(528,514)/RS(544,514) 50G по 2 FEC-полосам", SEMK, "РС",
    {"25G": "Cl.91 RS(528,514), одна полоса, AM каждые 1024 слова", "50G": "2 FEC-полосы, распределение 10-бит символов (рис. 9)", "FireCode": "Cl.74 тоже допускается"},
    "25GBASE-CR/KR (до 802.3by), 50G-CR2/KR2", [C25.ist(s25)], "код — тот же, что Cl.91 (проверен выше)", SLEPOJ_RS)
C800 = Tekst("istochniki/etc/800G-Specification_r1.1.pdf"); s800 = C800.odna(r"802\.3-2018 section 119\.2\.4\.6\. This means that an 800G stream will have 4 FEC codewords")[0]
rec("800G-ETC-R: 2×Cl.119 PCS, RS(544,514), 4 слова на интерфейсе", SEMK, "РС",
    {"код": "RS(544,514) Cl.119", "чередование": "10-бит, 2 × 400G потока, по 2 слова каждый"}, "800GBASE-ETC (консорциум, до 802.3df)", [C800.ist(s800)],
    "код тот же, что Cl.119 (проверен выше)", SLEPOJ_RS)

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
