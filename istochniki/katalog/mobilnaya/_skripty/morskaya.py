"""Морская подвижная служба (ITU-R): AIS (M.1371), ЦИВ/DSC (M.493), NAVTEX/SITOR (M.476, M.625, M.540), VDES (M.2092 — сквозной
тестовый вектор прил. A3-8), КВ-данные (M.1798: OFDM, PACTOR-III, широкополосная). Сверка: проект (ais.py, dsc.py, navtex.py), CCSDS 131.0-B (перемежитель)."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

AIS = Tekst("istochniki/morskaya/R-REC-M.1371-6-202602.pdf")
DSC = Tekst("istochniki/morskaya/R-REC-M.493-16.pdf")
S625 = Tekst("istochniki/morskaya/R-REC-M.625-4.pdf")
S476 = Tekst("istochniki/morskaya/R-REC-M.476-5.pdf")
S540 = Tekst("istochniki/morskaya/R-REC-M.540-2.pdf")
V = Tekst("istochniki/morskaya/R-REC-M.2092-2-202602.pdf")
H = Tekst("istochniki/morskaya/R-REC-M.1798-2-202102.pdf")
ZAPISI = []
PR = "/home/user/poiujnbhy/src/reportgen/potok/"

# --- AIS -------------------------------------------------------------------------------------------------------------
s_fcs = AIS.odna(r"The FCS uses the cyclic redundancy check \(CRC\) 16-bit polynomial to calculate the checksum as")[0]
s_pre = AIS.odna(r"The CRC bits should be pre-set to one \(1\) at the beginning of a CRC")[0]
s_tr = AIS.odna(r"The training sequence should be a bit pattern consisting of alternating 0’s and 1’s")[0]
s_flag = AIS.odna(r"The start flag consists of a bit pattern, 8 bits long: 01111110 \(7Eh\)")[0]
s_stuff = AIS.odna(r"subject to bit stuffing\. On the transmitting side, this means that if five \(5\) consecutive ones")[0]
s_nrzi = AIS.odna(r"The NRZI waveform is used for data encoding")[0]
ais_proj = open(PR + "ais.py").read()
assert "HDLC с NRZI и битстаффингом, FCS X.25" in ais_proj
# --- DSC: табл. A1-1 — 128 десятиэлементных знаков ------------------------------------------------------------------------
kus, s_dsc = DSC.kusok(r"Ten-bit error-detecting code\s*\n\s*Symbol", r"\n\s*TABLE A1-2|\n\s*Table A1-2|\n\s*2\s+Technical", posl=True)
kody = re.findall(r"\b[BY]{10}\b", kus)
assert len(kody) == 128, len(kody)
def dsc_znak(s):
    b = [1 if ch == "Y" else 0 for ch in kody[s]]
    info = sum(b[i] << i for i in range(7))                              # информационные — младшим вперёд
    chk = b[7] * 4 + b[8] * 2 + b[9]                                       # проверочные — старшим вперёд
    return info, chk
for s in range(128):
    info, chk = dsc_znak(s)
    assert info == s and chk == 7 - bin(s).count("1"), s                   # число B (нулей) в 7 инф. битах
s_dsc_r = DSC.odna(r"10 indicate, in the form of a binary number, the number of B elements that occur in the seven")[0]
s_dx = DSC.odna(r"the first transmission \(DX\) of a specific character is followed by the transmission of four other")[0]
s_ecc = DSC.odna(r"The seven information bits of the ECC shall be equal to the least significant bit of")[0]
dsc_proj = open(PR + "dsc.py").read()
assert "7 - bin(инфо).count(\"1\") == проверка" in dsc_proj and "СДВИГ = 5" in dsc_proj
# --- SITOR/NAVTEX: табл. 1 M.625 (и M.476): 7-элементный код 3Y/4B ----------------------------------------------------------
kus, s_625 = S625.kusok(r"TABLE 1\s*\n", r"\(1\)\s*\|?\s*\n?\s*A represents start polarity|\n\s*\(1\)\s*\n", posl=False)
k625 = re.findall(r"\b[BY]{7}\b", kus)
assert len(k625) == 32, len(k625)
bukvy = [chr(ord("A") + i) for i in range(26)]
assert all(k.count("Y") == 3 for k in k625), "не 3Y/4B"
navt = open(PR + "navtex.py").read()
m = re.search(r"КОДЫ = \{(.*?)\n\}", navt, re.S)
kody_pr = {int(a, 16): b for a, b in re.findall(r"(0x[0-9A-F]{2}): \(\"(.*?)\"", m.group(1))}
def v_chislo(k, B):                                                         # бит 1 передаётся первым → младший разряд
    return sum((1 if (ch == "B") == (B == 1) else 0) << i for i, ch in enumerate(k))
sovp_inv = sum(1 for i, L in enumerate(bukvy) if kody_pr.get(v_chislo(k625[i], 1)) == L)
sovp_pr = sum(1 for i, L in enumerate(bukvy) if kody_pr.get(v_chislo(k625[i], 0)) == L)
assert sovp_inv == 26 and sovp_pr == 0, (sovp_inv, sovp_pr)                # проект считает B = 1 (сноска (3) M.625: B = 0, Y = 1)
s_625n = S625.odna(r"The bit in bit position 1 is transmitted first; B = 0, Y = 1")[0]
s_476 = S476.odna(r"^BBBYYYB")[0]
s_modeb = S625.odna(r"FEC|forward error correction")[0]
# --- VDES M.2092: сквозной тестовый вектор A3-8 (LinkID 5): CRC-32 → турбокод 3/4 → скремблер ------------------------------------
def blok(T, ot, do):
    kus, s = T.kusok(ot, do)
    kus = re.sub(r"=====СТР \d+=====.*?M\.2092-2\s*\d*", " ", kus, flags=re.S)
    return [int(x) for x in re.findall(r"\b[01]\b", kus)], s
vb, s_vb = blok(V, r"\(b\)\s+Bit packed ASM acknowledgement message, using LinkID#5 \(256 bits\)", r"\(c\)\s+Input for turbo")
vc, _ = blok(V, r"\(c\)\s+Input for turbo encoder \(256 bits payload \+ 32 bits CRC = 288 bits\)", r"\(d\)\s+Turbo encoded")
vd, _ = blok(V, r"\(d\)\s+Turbo encoded data with flushing \(288 bits / ¾ FEC rate \+ 10 FEC tail bits = 394 bits\)", r"\(e\)\s+Scrambled")
ve, _ = blok(V, r"\(e\)\s+Scrambled data \(394 bits\)", r"\(f\)\s+Symbol mapped")
assert (len(vb), len(vc), len(vd), len(ve)) == (256, 288, 394, 394)
def crc32(bits, init=0xFFFFFFFF, poly=0x04C11DB7):
    r = init
    for x in bits: fb = ((r >> 31) & 1) ^ x; r = ((r << 1) & 0xFFFFFFFF) ^ (poly if fb else 0)
    return r
assert vc[:256] == vb and int("".join(map(str, vc[256:])), 2) == crc32(vb)          # CRC-32: начальное FFFFFFFF, без итоговой инверсии (= CRC-32/MPEG-2)
s_vcrc = V.odna(r"be set to the initial value 0xFFFF FFFF, and the 16 bits shift register for generating CRC-16 to the")[0]
kus, s_t4 = V.kusok(r"TABLE 4\s*\n\s*Interleaver and puncturing parameters", r"\(2\)\s+No tail bits|Table 4 will be extended")
m5 = re.search(r"\b5\s+3/4\s+288\s+(\d+)\|(\d+)\s+((?:\d+\|){7}\d+)\s+(\w+)\s+(\w+)", " ".join(kus.split()))
k1, k2 = int(m5.group(1)), int(m5.group(2)); PRM = [int(x) for x in m5.group(3).split("|")]; pid, tid = m5.group(4), m5.group(5)
assert (k1, k2, pid, tid) == (2, 144, "8", "8b"), m5.groups()
def pi_ccsds(k, k1, k2, P):
    out = []
    for s in range(1, k + 1):
        m_ = (s - 1) % 2; i = (s - 1) // (2 * k2); j = (s - 1) // 2 - i * k2; t = (19 * i + 1) % (k1 // 2); q = t % 8 + 1
        c = (P[q - 1] * j + 21 * m_) % k2; out.append(2 * (t + c * k1 // 2 + 1) - m_)
    return out
PI = pi_ccsds(288, k1, k2, PRM); assert sorted(PI) == list(range(1, 289))
s_pif = V.odna(r"The permutation numbers shall be interpreted such that the sth bit read out after interleaving is the")[0]
def tab_vyk(nomer, tablica_pat):
    kus, _ = V.kusok(tablica_pat, r"For each rate, the puncturing table shall be read first|Note: For each rate")
    t = " ".join(kus.split())
    mm = re.search(r"(?<![\w.])" + re.escape(nomer) + r"\s+\d/\d\s+((?:[0-3];){5}[0-3](?:\s*\|\s*(?:[0-3];){5}[0-3])*)", t)
    return [[int(x) for x in grp.split(";")] for grp in re.split(r"\s*\|\s*", mm.group(1))]
PAT = tab_vyk(pid, r"TABLE 5\s*\n\s*Puncturing patterns for data bit periods")
TP = tab_vyk(tid, r"TABLE 6\s*\n\s*Puncturing and repetition patterns for tail bit periods")
assert len(PAT) == 6 and len(TP) == 6
s_rsc = V.odna(r"𝑛0\(𝐷\) = 1 \+ 𝐷\+ 𝐷3|n0\(D\) = 1 \+ D \+ D3")[0]
def rsc(u):
    s = [0, 0, 0]; out = []
    for x in u:
        a = x ^ s[1] ^ s[2]; out.append((x, a ^ s[0] ^ s[2], a ^ s[0] ^ s[1] ^ s[2])); s = [a, s[0], s[1]]
    tail = []
    for _ in range(3):
        x = s[1] ^ s[2]; a = 0; tail.append((x, a ^ s[0] ^ s[2], a ^ s[0] ^ s[1] ^ s[2])); s = [a, s[0], s[1]]
    return out, tail
o1, t1 = rsc(vc); o2, t2 = rsc([vc[p - 1] for p in PI])
vyh = []
for n in range(288):
    v = list(o1[n]) + list(o2[n])
    for idx in range(6): vyh += [v[idx]] * PAT[n % len(PAT)][idx]
hv = [list(t1[i]) + [0, 0, 0] for i in range(3)] + [[0, 0, 0] + list(t2[i]) for i in range(3)]
for n in range(6):
    for idx in range(6): vyh += [hv[n][idx]] * TP[n][idx]
assert vyh == vd, "турбокодер ≠ тестовый вектор (d)"
skr = [a ^ b for a, b in zip(vd, ve)]
assert all(skr[n] == skr[n - 14] ^ skr[n - 15] for n in range(15, 394))           # F(x) = 1 + x^-14 + x^-15
s_vscr = V.odna(r"F\(x\)=1 \+ x-14 \+ x-15")[0]
s_vex = V.odna(r"Example of application specific message burst symbol generation\s*$")[0] if V.naiti(r"Example of application specific message burst symbol generation\s*$") else s_vb
s_rm = V.odna(r"\(32,6\) biorthogonal code \. The code is a first order Reed-Muller code with generator matrix")[0]
CC = Tekst("istochniki/ccsds/131x0b5s.pdf")
tablica("vdes_m2092_testvektor", {"LinkID5": {"k1|k2": [k1, k2], "p": PRM, "vykalyvanie_8": PAT, "hvost_8b": TP}, "pi_288": PI,
                                  "skrembler_nach_15": skr[:15], "crc32_hex": hex(crc32(vb))},
        "VDES (M.2092-2) LinkID 5: параметры перемежителя и выкалывания, перестановка π(288), начальные 15 бит скремблера по тестовому вектору",
        [V.ist(s_t4, "Table 4"), V.ist(s_vb, "A3-8 тестовый вектор (b)–(e)")],
        "из (b) вычислен CRC-32 = хвост (c); турбокодер (CCSDS-перемежитель + RSC n0/n1/d + выкалывание 8 + хвост 8b) из (c) дал ровно (d) — 394 бита; (d)⊕(e) удовлетворяет s(n) = s(n−14)⊕s(n−15) на всех 379 шагах")

def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
rec("AIS: HDLC-кадр с FCS CRC-16 (ISO/IEC 13239), NRZI, бит-стаффинг", "AIS (ITU-R M.1371)", "CRC-подобный + линейное кодирование",
    {"FCS": "CRC-16 ISO/IEC 13239 (x16+x12+x5+1), начальное — единицы, инверсия (= CRC-16/X-25 / HDLC FCS)", "кадр": "обучающая 24 бита 0101…, флаг 7E, данные (168 бит сообщения 1), FCS, флаг, буфер 24",
     "бит-стаффинг": "0 после пяти 1 (кроме флагов и обучающей)", "NRZI": "0 → смена уровня", "модуляция": "GMSK BT 0,4, 9600 бит/с, слоты 26,67 мс",
     "порядок": "байты младшим битом вперёд (как HDLC)"},
    "AIS класс A/B, базовые станции, SART, AtoN", [AIS.ist(s_fcs, "A2-3.2.2.6 FCS"), AIS.ist(s_pre, "начальное 1"), AIS.ist(s_tr, "обучающая"), AIS.ist(s_flag, "флаг"), AIS.ist(s_stuff, "стаффинг"), AIS.ist(s_nrzi, "NRZI")],
    "по тексту; FCS = CRC-16/X-25 каталога проекта (hdlc.fcs16), в проекте ais.py", "ЕСТЬ в проекте: ais.py (HDLC, NRZI, FCS, разбор сообщений), hdlc.py")
rec("ЦИВ (DSC): 10-элементный код обнаружения ошибок + временное разнесение DX/RX + ECC", "ЦИВ/DSC (ITU-R M.493)", "код обнаружения ошибок (счёт нулей) + повторение",
    {"знак": "7 инф. бит (младший первым, Y = 1) + 3 проверочных = число B (нулей) в инф. битах (старший первым); 128 знаков — tablicy не нужна (правило)",
     "разнесение": "каждый знак дважды: DX и RX через 4 других знака (400 мс КВ/ПВ, 33⅓ мс УКВ)", "ECC": "чётность по вертикали 7 инф. бит всех информационных знаков", "модуляция": "ЧМн 100 Бод (КВ/ПВ), 1200 Бод (УКВ, 1300/2100 Гц)"},
    "ГМССБ: ЦИВ на ПВ/КВ/УКВ (канал 70)", [DSC.ist(s_dsc, "Table A1-1"), DSC.ist(s_dsc_r, "1.1.1"), DSC.ist(s_dx, "1.2 DX/RX"), DSC.ist(s_ecc, "10.2 ECC")],
    "все 128 знаков табл. A1-1 разобраны из текста и удовлетворяют правилу (число B = проверочные); правило = dsc.py проекта (знак()), сдвиг DX/RX 5 = СДВИГ проекта", "ЕСТЬ в проекте: dsc.py")
rec("NAVTEX/SITOR (NBDP): 7-элементный код 3Y/4B (35 комбинаций) + режим B (FEC — временное разнесение) / режим A (ARQ)", "SITOR/NAVTEX (ITU-R M.476, M.625, M.540)", "код постоянного веса + повторение",
    {"код": "32 знака табл. 1 + служебные (α, β, RQ …): ровно 3 Y и 4 B", "режим B": "каждый знак дважды: DX и RX со сдвигом 5 знаковых мест (280 мс)", "полярность": "M.625 сноска (3): B = 0, Y = 1, бит 1 первым; B — верхняя частота",
     "расхождение с проектом": "navtex.py использует обратную полярность (B = 1, код веса 4): 26 букв совпадают при инверсии — зависит от знака сдвига частоты демодулятора"},
    "NAVTEX 518/490/4209,5 кГц, SITOR на КВ", [S625.ist(s_625, "Table 1"), S625.ist(s_625n, "сноска (3)"), S476.ist(s_476, "M.476 Table 1")],
    "32 кода табл. 1 разобраны, у всех 3 Y; буквы A–Z = КОДЫ navtex.py проекта при B = 1 (0 совпадений при B = 0) — проект инвертирован относительно сноски M.625", "ЕСТЬ в проекте: navtex.py (режим B, NAVTEX)")
rec("VDES (VDE-TER/SAT): CRC-32/16 + турбокод PCCC (CCSDS-перемежитель, DVB-SH выкалывание) + скремблер 1+x^-14+x^-15 + ID канала RM(32,6)", "VDES (ITU-R M.2092)", "турбо PCCC",
    {"RSC": "n0 = 1+D+D³, n1 = 1+D+D²+D³, d = 1+D²+D³ (как DVB-SH/cdma2000)", "перемежитель": "CCSDS 131.0-B: π(s) = 2(t + c·k1/2 + 1) − m (параметры k1|k2, p1…p8 по табл. 4)",
     "выкалывание": "табл. 5 (данные), табл. 6 (хвост, 6 тактов)", "CRC": "CRC-32 (x32+x26+…+1), начальное FFFFFFFF, без инверсии; CRC-16 x16+x15+x2+1 (LinkID 20)", "скремблер": "F(x) = 1 + x^-14 + x^-15, сброс на пакет",
     "ID канала": "(32,6) биортогональный (РМ 1-го порядка)", "модуляция": "π/4-QPSK, 8PSK, 16QAM; 25/50/100 кГц"},
    "VDES — развитие АИС (ASM, VDE-TER, VDE-SAT)", [V.ist(s_rsc, "A2-1.2.4.2"), V.ist(s_t4, "Table 4"), V.ist(s_vcrc, "A2-1.2.5 CRC"), V.ist(s_vscr, "A2-1.2.6"), V.ist(s_rm, "(32,6)"), V.ist(s_vb, "A3-8 тестовый вектор"),
     CC.ist(CC.odna(r"^\(19i \+ 1\) mod k1")[0], "CCSDS 131.0-B-5 6.3 — та же формула перемежителя")],
    "СКВОЗНОЙ ТЕСТОВЫЙ ВЕКТОР A3-8 (LinkID 5) воспроизведён побитно: CRC-32 → турбокод 3/4 (394 бита) → ПСП скремблера по рекурсии", "есть PCCC (turbo.py); перемежитель CCSDS — в области kosmos; средняя")
s_ofdm = H.odna(r"The scrambler is defined by the polynomial 1 \+ x14 \+ x17 or by the recursive equation")[0]
s_crc16 = H.odna(r"The 16-bit CRC at the end of all blocks is a standard ITU-T polynomial remainder calculated over")[0]
s_p3 = H.odna(r"code with a constraint length \(CL\) of 7 or 9 is used\. Similar to the PACTOR-II protocol, the codes")[0]
s_a5 = H.odna(r"The polynomial is represented as 𝑥32\+𝑥31\+𝑥27\+𝑥26\+1|x32\+x31\+x27\+x26\+1")[0]
s_a5f = H.odna(r"Forward error correction schemes")[0]
rec("КВ морские данные M.1798 прил. 2: OFDM 32 несущих — CRC-16 блоков + скремблер 1+x14+x17", "КВ-данные (ITU-R M.1798)", "CRC-подобный + скремблер",
    {"CRC": "CRC-16 ITU-T, XOR 0xFFFF, младший байт первым, проверка с 0xFFFF (= CRC-16/X-25)", "скремблер": "1+x^14+x^17 (17 разрядов), начальная фаза по номеру кадра", "блоки": "32 блока за 1520 мс"},
    "морская КВ-электронная почта (OFDM, DATAPLEX)", [H.ist(s_crc16, "Annex 2 CRC"), H.ist(s_ofdm, "Annex 2 scrambler")], "по тексту стандарта", "есть CRC-16/X-25 и аддитивный скремблер")
rec("КВ морские данные M.1798 прил. 5: RS(204,188) / свёрточный K=7 1/2 (выкалывание 2/3…7/8) / дуобинарный турбо; скремблер x32+x31+x27+x26+1", "КВ-данные (ITU-R M.1798)", "каскадный: РС + свёрточный / турбо",
    {"режимы": "1: RS(204,188); 2–6: K=7 1/2 выкалывание 1/2, 2/3, 3/4, 5/6, 7/8; 7–8: дуобинарный турбо 1/2, 3/4", "скремблер": "словный 32 бит, x32+x31+x27+x26+1", "модуляция": "OFDM 4/16/64-QAM, 10–20 кГц"},
    "широкополосная КВ точка–точка (судно–берег)", [H.ist(s_a5f, "Annex 5 Table 11"), H.ist(s_a5, "Annex 5 scrambler")],
    "по тексту (табл. 11, § 4.2.3); порождающие многочлены свёрточного кода, РС и турбокода в прил. 5 НЕ заданы (стр. 105–125 просмотрены поиском polynomial/generator/octal/DVB) — для реализации нужны данные производителя или запись сигнала", "частично: в проекте есть RS(204,188) и K=7 171/133 (DVB), но их совпадение с M.1798 прил. 5 источником не подтверждено; скремблер x32+x31+x27+x26+1 — есть общий аддитивный")
rec("PACTOR-III (M.1798 прил. 3): свёрточный K=7/9 1/2 с выкалыванием 3/4, 8/9 + перемежение + CRC-16 CCITT", "PACTOR (SCS)", "свёрточный с выкалыванием + CRC",
    {"уровни": "6 скоростей, 2–18 несущих DBPSK/DQPSK", "свёрточный": "1/2 K=7 или 9, выкалывание до 3/4 и 8/9 — многочлены и шаблоны НЕ опубликованы", "CRC": "CRC-16 CCITT", "управляющие сигналы": "6 × 20 бит на границе Плоткина (как PACTOR-II)",
     "сжатие": "Хаффман, RLE, PMC"},
    "морская/любительская КВ-почта (Winlink, Sailmail)", [H.ist(s_p3, "Annex 3 §1.3")], "описание по M.1798; параметры кода — собственность SCS (не открыты)", "нет; многочлены неизвестны")

if __name__ == "__main__":
    print(len(ZAPISI), "записей", sovp_inv, PRM)
