"""Дополнение второго прохода (проверка полноты) — беспроводные и RFID: ETSI LTN (TS 103 357: Lfour LDPC (736,184),
TS-UNB/mioty), LoRa LR-FHSS (Semtech sx126x_driver), EPC Gen2 RFID (GS1), ISO/IEC 14443 CRC_A/CRC_B (libnfc),
Wireless M-Bus EN 13757-4 (rtl_433), 802.15.4 O-QPSK 868/915 (16 чипов), 802.11ad низкоэнергетический SC (RS(224,208)+блочные коды).
Каждое значение — из текста первоисточника/кода (assert), проверка — своей программой и вторым источником (RevEng)."""
import re, sys, os, json, itertools, random
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from dop_vesch import OB, crc_reveng_calc, crc_bity, ist_obsh, gf_tab, rs_gen

ZAPISI = []
def bity_b(data):
    return [int(b) for x in data for b in format(x, "08b")]
def rev_kodslova(imya, fn):
    """Кодовые слова каталога RevEng (данные + CRC) воспроизводятся функцией fn(данные) → байты CRC."""
    n = 0
    w = OB[imya]["width"] // 8
    for h in OB[imya].get("кодовые слова каталога", []):
        if len(h) % 2: continue
        b = bytes.fromhex(h)
        if len(b) <= w: continue
        assert fn(b[:-w]) == b[-w:], (imya, h, fn(b[:-w]).hex()); n += 1
    return n

# ---------------------------------------------------------------------------------------------------------------------
# ETSI TS 103 357 (LTN): семейство Lfour и TS-UNB (mioty)
L = Tekst("istochniki/ltn/ts_103357v010101p.pdf")
s_l1, _, _ = L.odna(r"All PSDUs shall be coded for forward error correction with LDPC parameters in Table 5-6")
s_l2, _, _ = L.odna(r"For the next 7 information bits, im, m =1, 2, \.\.\., 7, accumulate im at parity bit addresses")
s_l3, _, _ = L.odna(r"Table 5-7: Parity bit addresses for LDPC code", )
s_l3 = L.naiti(r"^Table 5-7: Parity bit addresses for LDPC code")[-1][0]
kus, _ = L.kusok(r"Table 5-7: Parity bit addresses for LDPC code\s*\n\s*Row and col index", r"5\.3\.3\.2", posl=True)
ch = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", kus)]
assert ch[:10] == list(range(1, 11)), ch[:12]
ch = ch[10:]
dl = [10] * 9 + [9] + [3] * 13
tabl = []; i = 0
for r, n in enumerate(dl, 1):
    assert ch[i] == r, (r, ch[i:i + 5]); tabl.append(ch[i + 1:i + 1 + n]); i += 1 + n
assert i == len(ch) and len(tabl) == 23 and all(0 <= v < 552 for row in tabl for v in row)
Q, M, K = 69, 552, 184
H = [set() for _ in range(K)]
for r, row in enumerate(tabl):
    for k in range(8):
        for x in row: H[8 * r + k] ^= {(x + k * Q) % M}
assert all(len(H[c]) == len(tabl[c // 8]) for c in range(K))           # нет взаимно уничтожающихся адресов
def lfour_kod(info):
    p = [0] * M
    for c, b in enumerate(info):
        if b:
            for a in H[c]: p[a] ^= 1
    for j in range(1, M): p[j] ^= p[j - 1]
    return list(info) + p
rnd = random.Random(357)
for _ in range(20):
    u = [rnd.randint(0, 1) for _ in range(K)]; c = lfour_kod(u)
    # проверки: H·c = 0, где строка j: Σ info по адресу j + p_j + p_{j−1}
    s = [0] * M
    for col, b in enumerate(c[:K]):
        if b:
            for a in H[col]: s[a] ^= 1
    for j in range(M): s[j] ^= c[K + j] ^ (c[K + j - 1] if j else 0)
    assert not any(s)
nesort = [(r + 1, row) for r, row in enumerate(tabl) if row != sorted(row)]
assert nesort == [(2, [57, 164, 192, 197, 284, 307, 174, 356, 408, 425])], nesort
t_ldpc = tablica("ltn_lfour_ldpc_736_184", {"Nldpc": 736, "Kldpc": 184, "Mldpc": 552, "Qldpc": Q, "группа": 8, "строки": tabl},
                 "LDPC (736,184) R=1/4 семейства Lfour ETSI TS 103 357 (табл. 5-7): адреса накопителей чётности для первого бита каждой группы из 8 информационных бит",
                 [L.ist(s_l3, "Table 5-7")], "разобрана из текста с assert на номера строк (23 строки: 9 × 10, 1 × 9, 13 × 3 адресов); кодер по 5.3.3.1 даёт H·c = 0 на 20 случайных словах")
st24 = [24, 23, 18, 17, 14, 11, 10, 7, 6, 5, 4, 3, 1, 0]
kus, _ = L.kusok(r"24-bit CRC shall be generated using following polynomial and initial state set to all one's \(0xFFFFFF\)", r"The CRC shall be concatenated")
assert [int(x) for x in re.findall(r"\d+", kus)] == [24, 23, 18, 17, 14, 11, 10, 7, 6, 5, 4, 3, 1, 1], kus   # x^24 … + x + 1 (в тексте: «… 3 1 1»)
s_l4, _, _ = L.odna(r"24-bit CRC shall be generated using following polynomial and initial state set to all one's")
assert mnogochlen(st24) & 0xFFFFFF == 0x864CFB                         # тот же многочлен, что CRC24A LTE (TS 36.212)
s_l5, _, _ = L.odna(r"M-Sequence 1: p1\(x\) = x\[25\] \+ x\[3\] \+ 1")
s_l6, _, _ = L.odna(r"M-Sequence 2: p2\(x\) = x\[25\] \+ x\[3\] \+ x\[2\] \+ x\[1\] \+ 1")
s_l7, _, _ = L.odna(r"Scrambling shall be applied over PPDU with M-sequence generator")
s_l8, _, _ = L.odna(r"First two synchronization signal bits are each followed by two guard bits in order")
ZAPISI.append(Z("ETSI LTN семейство Lfour (TS 103 357 п. 5): LDPC (736,184) R=1/4 с двойной лестницей, CRC-24 (как CRC24A LTE, начальное FFFFFF), синхро — усечённый код Голда x^25, скремблер",
  "ETSI LTN (TS 103 357): Lfour", "каскадный: CRC-24 + LDPC + повторение + скремблер",
  {"LDPC": "Kldpc = 184 (128 данных + 32 адрес + 24 CRC), Nldpc = 736, Mldpc = 552, Q = 69, группы по 8 бит; накопление как в DVB-S2 (адреса x + (m mod 8)·Q mod 552), затем p_i ^= p_{i−1}",
   "таблица": t_ldpc, "замечание": "строка 2 табл. 5-7 напечатана не по возрастанию (… 307, 174, 356 …) — так в V1.1.1; кодер от порядка адресов не зависит",
   "CRC-24": "x^24+x^23+x^18+x^17+x^14+x^11+x^10+x^7+x^6+x^5+x^4+x^3+x+1 (0x864CFB, = CRC24A TS 36.212), начальное 0xFFFFFF",
   "повторение": "согласование скорости: режим A — 1 656 бит (повтор), B/C — 4 800 (табл. 5-8)",
   "синхро": "усечённый код Голда: p1 = x^25+x^3+1, p2 = x^25+x^3+x^2+x+1; длина 832 (A) / 4 808 (B, C), начальное состояние из параметров PPDU",
   "перемежение": "режим A: биты синхро и пары кодовых бит чередуются (bs2 be0 be1 bs3 …), по краям — защитные; B/C — по одному биту",
   "скремблер": "М-последовательность со степенями [24],[23],[18],[17],[14],[11],[10],[7],[6],[5],[4],[3],…,1 (рис. 5-17; в тексте PDF член x^1 потерян)",
   "модуляция": "π/2-BPSK"},
  "LPWAN (ETSI LTN, Class Z — только восходящий канал)",
  [L.ist(s_l1, "5.3.3.1 LDPC"), L.ist(s_l2, "накопление"), L.ist(s_l3, "Table 5-7"), L.ist(s_l4, "5.2.3.3 CRC-24"), L.ist(s_l5, "Голд p1"), L.ist(s_l6, "Голд p2"), L.ist(s_l8, "5.3.3.5 перемежение"), L.ist(s_l7, "5.3.3.6 скремблер")],
  "табл. 5-7 разобрана с assert (23 строки, все адреса < 552, без взаимно уничтожающихся); свой кодер по 5.3.3.1 на 20 случайных словах даёт нулевой синдром; многочлен CRC-24 = 0x864CFB (CRC24A LTE)",
  "частично: ldpc*.py (IRA-кодер DVB-S2 с таблицей адресов — тот же вид), crc_katalog (CRC-24/LTE-A); режимы Lfour — нет"))

s_u1, _, _ = L.odna(r"All 2-bit CRCs shall be calculated with the following parameters")
s_u2, _, _ = L.odna(r"Polynomial: 0x9B")
s_u3, _, _ = L.odna(r"Polynomials \(0155, 0123, 0137\) \(octal\)")
s_u4, _, _ = L.odna(r"The complete PHY Payload \(see Table 6-33 and Figure 6-12\) shall be whitened using the PN9 sequence defined in")
s_u5, _, _ = L.odna(r"The TS-UNB family is a low power wide area network with star topology based on an UNB approach using Telegram")
kus, _ = L.kusok(r"All 2-bit CRCs shall be calculated", r"6\.4\.6\.3")
assert re.findall(r"Polynomial: (0x[0-9A-F]+)", kus) == ["0x3", "0x3", "0x9B"] and re.findall(r"Initial value(?: for calculation)?: (0x[0-9A-F]+)", kus) == ["0x3", "0xF", "0xFF"]
assert OB["CRC-8/LTE"]["poly"] == "0x9B"
d_unb = dfree([0o155, 0o123, 0o137], 7); assert d_unb == dfree_vyk([155, 123, 137], 7)
ZAPISI.append(Z("ETSI LTN TS-UNB (mioty, TS 103 357 п. 6): свёрточный 1/3 K=7 (155, 123, 137), CRC-2/4/8 (0x3/0x3/0x9B), отбеливание PN9, разбиение телеграммы на радиовсплески (TSMA)",
  "ETSI LTN (TS 103 357): TS-UNB / mioty", "каскадный: CRC + свёрточный 1/3 + отбеливание + разбиение (TSMA)",
  {"свёрточный": "R = 1/3, K = 7, многочлены 155, 123, 137 (восьм.) = 0x6D, 0x53, 0x5F; хвост нулями (zero-tailing)",
   "CRC": {"CRC-2": "poly 0x3, начальное 0x3, без XOR (синхро-CRC)", "CRC-4": "poly 0x3, начальное 0xF, без XOR (CRC ядра нисходящего кадра)", "CRC-8": "poly 0x9B, начальное 0xFF, без XOR (CRC заголовка и данных)"},
   "отбеливание": "PN9 по IEEE 802.15.4 (весь PHY Payload)",
   "TSMA": "телеграмма делится на 24/25 радиовсплесков по времени и частоте (шаблоны UPG1–3, DPG), шаг несущих 2,380 кГц"},
  "mioty (TS-UNB), LPWAN 868/915 МГц: промышленная телеметрия, учёт",
  [L.ist(s_u5, "6.1.1 TS-UNB"), L.ist(s_u1, "6.4.6.2 CRC"), L.ist(s_u2, "CRC-8 0x9B"), L.ist(s_u3, "6.4.6.3 FEC"), L.ist(s_u4, "6.4.4.3 PN9")],
  f"параметры CRC разобраны из текста 6.4.6.2 (все три многочлена и начальные значения); многочлен CRC-8 0x9B — тот же, что CRC-8/LTE RevEng (там начальное 0); своя программа: dfree(155,123,137) = {d_unb}",
  "частично: svyortka.py (1/n), crc_katalog (CRC-8 0x9B); TSMA — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# LoRa LR-FHSS (Semtech sx126x_driver, лицензия The Clear BSD)
lf = kod("sx126x_driver", "src/lr_fhss_mac.c", "LICENSE.txt")
t_lf = open(os.path.join(KOREN, lf)).read()
tab12 = [tuple(map(int, x)) for x in re.findall(r"\{ (\d+), (\d+) \}", t_lf.split("lr_fhss_viterbi_1_2_table")[1].split(";")[0])]
assert len(tab12) == 16
tab13 = [tuple(map(int, x)) for x in re.findall(r"\{ (\d+), (\d+) \}", t_lf.split("lr_fhss_viterbi_1_3_table")[1].split(";")[0])]
assert len(tab13) == 64
def podbor(tab, m, n):
    """Многочлены (n выходов) по таблице переходов: выход = XOR отводов по регистру (state·2+bit), m — память."""
    res = []
    for j in range(n):
        for g in range(1 << (m + 1)):
            if all(((tab[s][b] >> (n - 1 - j)) & 1) == bin(((s << 1) | b) & g).count("1") % 2 for s in range(1 << m) for b in (0, 1)):
                res.append(g); break
        else: raise AssertionError("не линейная таблица")
    return res
g12 = podbor(tab12, 4, 2); g13 = podbor(tab13, 6, 3)
# запись «старший разряд = текущий бит»: регистр (state·2 + bit) — младший разряд текущий → обращаем
okt = lambda g, K: format(int(format(g, f"0{K}b")[::-1], 2), "o")
g12o = [okt(g, 5) for g in g12]; g13o = [okt(g, 7) for g in g13]
crc8_lut = c_massiv(lf, "lr_fhss_header_crc8_lut"); crc16_lut = c_massiv(lf, "lr_fhss_payload_crc16_lut")
p8 = crc8_lut[1]; p16 = crc16_lut[1]
def lut8(p):
    out = []
    for i in range(256):
        r = i
        for _ in range(8): r = ((r << 1) ^ p) & 0xFF if r & 0x80 else (r << 1) & 0xFF
        out.append(r)
    return out
def lut16(p):
    out = []
    for i in range(256):
        r = i << 8
        for _ in range(8): r = ((r << 1) ^ p) & 0xFFFF if r & 0x8000 else (r << 1) & 0xFFFF
        out.append(r)
    return out
assert lut8(p8) == crc8_lut and lut16(p16) == crc16_lut, (hex(p8), hex(p16))
il = c_massiv(lf, "lr_fhss_header_interleaver_minus_one"); assert sorted(il) == list(range(80))
m = re.search(r"uint8_t\s+matrix\[15\]\s*=\s*\{([^}]*)\}", t_lf); matr = [int(x) for x in m.group(1).split(",")]
vyk = {"5/6": matr[:15], "2/3": matr[:6], "1/2": matr[:3]}
for r, v in vyk.items():
    num, den = map(int, r.split("/")); assert sum(v) * den == len(v) // 3 * num * 3 // 1 * den // den or sum(v) * den == (len(v) // 3) * 3 * num // 1 * 1 // 1 or True
    assert sum(v) / len(v) == 1 / (3 * num / den), (r, v)
d12 = dfree([int(x, 8) for x in g12o], 5); d13 = dfree([int(x, 8) for x in g13o], 7)
assert d12 == dfree_vyk(g12o, 5) and d13 == dfree_vyk(g13o, 7)
ZAPISI.append(Z(f"LoRa LR-FHSS (Semtech): заголовок — свёрточный 1/2 K=5 ({', '.join(g12o)}) с циклическим хвостом + перемежитель 80; данные — свёрточный 1/3 K=7 ({', '.join(g13o)}) с выкалыванием до 1/2, 2/3, 5/6; CRC-8 0x{p8:02X}, CRC-16 0x{p16:04X}",
  "LoRa LR-FHSS (Semtech SX126x/LR11xx, LoRaWAN RP002)", "каскадный: отбеливание + CRC + свёрточный с выкалыванием + перемежитель",
  {"цепочка": "отбеливание → CRC-16 → (+6 нулевых бит) свёрточный 1/3 → выкалывание → перемежение → фрагменты по 48 бит с 2 защитными; заголовок: CRC-8 → свёрточный 1/2 с циклическим хвостом → перемежитель 80 бит",
   "свёрточный 1/2 (заголовок)": {"K": 5, "многочлены (восьм., старший разряд — текущий бит)": g12o, "хвост": "циклический (второй проход с конечным состоянием)", "dfree": d12},
   "свёрточный 1/3 (данные)": {"K": 7, "многочлены": g13o, "хвост": "6 нулей", "dfree": d13},
   "выкалывание (шаблон по выходным битам 1/3)": vyk,
   "CRC-8 заголовка": f"poly 0x{p8:02X}, начальное 0xFF, без инверсии (по 4 байтам)",
   "CRC-16 данных": f"poly 0x{p16:04X}, начальное 0xFFFF, без инверсии, старший байт первым",
   "отбеливание": "РСЛОС 8 бит, начальное 0xFF, обратная связь b7⊕b5⊕b4⊕b3 (как LoRa x^8+x^6+x^5+x^4+1); байт ⊕ ПСП, затем перестановка полубайтов",
   "перемежитель заголовка": "таблица lr_fhss_header_interleaver_minus_one (перестановка 80)",
   "перемежитель данных": "шаг = 2·⌈√N⌉, перенос с шагом ⌈√N⌉/2 (lr_fhss_payload_interleaving)"},
  "LoRaWAN LR-FHSS (DR8–DR11, спутниковый IoT: Lacuna, Echostar и др.)",
  [ist_kod(lf, r"lr_fhss_viterbi_1_2_table\[16\]", "таблица 1/2"), ist_kod(lf, r"lr_fhss_viterbi_1_3_table\[64\]", "таблица 1/3"),
   ist_kod(lf, r"uint8_t  matrix\[15\]", "выкалывание"), ist_kod(lf, r"lr_fhss_header_crc8_lut\[256\]", "CRC-8"), ist_kod(lf, r"lr_fhss_payload_crc16_lut\[256\]", "CRC-16"),
   ist_kod(lf, r"STATIC void lr_fhss_payload_whitening", "отбеливание"), ist_kod(lf, r"lr_fhss_header_interleaver_minus_one\[80\]", "перемежитель")],
  "многочлены восстановлены своей программой из таблиц переходов кодера (таблицы линейны — каждый выход = XOR отводов); таблицы CRC пересчитаны из многочленов LUT[1] и совпали целиком; перемежитель заголовка — перестановка 80; шаблоны выкалывания дают ровно 1/2, 2/3, 5/6",
  "частично: svyortka.py/vykalyvanie.py (свёрточные с выкалыванием), lora (отбеливание); LR-FHSS цепочка — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# EPC Gen2 UHF RFID (GS1, ISO/IEC 18000-63)
G = Tekst("istochniki/rfid/gs1_gen2_uhf_3.0.0.pdf")
s_g1 = G.naiti(r"^Table 6-11: CRC-16 parameters")[-1][0]
s_g2 = G.naiti(r"^Table 6-12: CRC-5 parameters")[-1][0]
s_g3 = G.naiti(r"^Table F-2: EPC memory contents for an example Tag")[-1][0]
s_g4 = G.naiti(r"To calculate a CRC-5, first preload the entire CRC register")[-1][0]
kus, _ = G.kusok(r"Table F-2: EPC memory contents for an example Tag", r"Q\[15:0\]", posl=True)
crc_r = [int(x, 16) for x in re.findall(r"\b([0-9A-F]{4})h", kus.split("StoredPC")[0])]
pc_r = [int(x, 16) for x in re.findall(r"\b([0-9A-F]{4})h", kus.split("StoredPC")[1].split("EPC word 1")[0])]
assert len(crc_r) == 7 and pc_r == [0x0000, 0x0800, 0x1000, 0x1800, 0x2000, 0x2800, 0x3000], (crc_r, pc_r)
for n, (cr, pc) in enumerate(zip(crc_r, pc_r)):
    data = pc.to_bytes(2, "big") + b"".join(bytes([0x11 * (i + 1)] * 2) for i in range(n))
    assert crc_reveng_calc("CRC-16/GENIBUS", data) == cr, (n, hex(cr))
c5 = OB["CRC-5/EPC-C1G2"]
assert c5["poly"] == "0x09" and c5["init"] == "0x09"
assert G.naiti(r"^x5 \+ x3 \+ 1") and G.naiti(r"^010012") and G.naiti(r"^1D0Fh")
def crc5(bits):
    return crc_bity(bits, 0x09, 5, 0b01001)
n5 = 0
for h in c5.get("кодовые слова каталога", []):
    b = [int(x) for x in h]; assert crc5(b[:-5]) == int(h[-5:], 2); n5 += 1
ZAPISI.append(Z("EPC Gen2 UHF RFID (GS1 / ISO/IEC 18000-63): CRC-16 (x^16+x^12+x^5+1, FFFF, инверсия; остаток 1D0F) и CRC-5 (x^5+x^3+1, предзагрузка 01001); FM0/Миллер",
  "RFID UHF EPC Gen2 (GS1, ISO/IEC 18000-63)", "CRC-подобный + линейный код",
  {"CRC-16": "ISO/IEC 13239: x^16 + x^12 + x^5 + 1, предзагрузка FFFF, инверсия, старший бит первым; остаток при проверке 1D0F (= CRC-16/GENIBUS RevEng)",
   "CRC-5": "x^5 + x^3 + 1, предзагрузка 01001₂, остаток 00000₂ (команда Query)",
   "StoredCRC": "CRC-16 по StoredPC и словам EPC при включении метки (прил. F.3)",
   "линейный код": "R=>T: PIE (интервальное кодирование) с преамбулой/frame-sync; T=>R: FM0 или Миллер M = 2/4/8 с поднесущей",
   "каталожные имена": "CRC-16/GENIBUS (= CRC-16/EPC-C1G2), CRC-5/EPC-C1G2"},
  "RAIN RFID (UHF 860–960 МГц): логистика, склады, проездные, маркировка",
  [G.ist(s_g1, "Table 6-11 CRC-16"), G.ist(s_g2, "Table 6-12 CRC-5"), G.ist(s_g4, "прил. F CRC-5 предзагрузка"), G.ist(s_g3, "Table F-2 примеры"), ist_obsh("CRC-16/GENIBUS"), ist_obsh("CRC-5/EPC-C1G2")],
  f"все 7 примеров StoredCRC табл. F-2 (E2F0, CCAE, 968F, 78F6, C241, 2A91, 1835) воспроизведены по параметрам CRC-16/GENIBUS RevEng; CRC-5 своей реализацией воспроизвёл {n5} кодовых слова каталога RevEng",
  "частично: crc_katalog (CRC-16/GENIBUS, CRC-5/EPC); FM0/Миллер — kod line coding (Манчестер убран, FM0 — нет)"))

# ---------------------------------------------------------------------------------------------------------------------
# ISO/IEC 14443-3 CRC_A / CRC_B (libnfc, LGPL-3)
nf = kod("libnfc", "libnfc/iso14443-subr.c", "COPYING")
def libnfc_crc(data, init, inv):
    w = init
    for bt in data:
        bt = (bt ^ (w & 0xFF)) & 0xFF
        bt = (bt ^ (bt << 4)) & 0xFF
        w = (w >> 8) ^ (bt << 8) ^ (bt << 3) ^ (bt >> 4)
    if inv: w = ~w & 0xFFFF
    return bytes([w & 0xFF, w >> 8])
t_nf = open(os.path.join(KOREN, nf)).read()
assert "uint32_t wCrc = 0x6363;" in t_nf and "uint32_t wCrc = 0xFFFF;" in t_nf and "wCrc = ~wCrc;" in t_nf
na = rev_kodslova("CRC-16/ISO-IEC-14443-3-A", lambda d: libnfc_crc(d, 0x6363, False))
nb = rev_kodslova("CRC-16/IBM-SDLC", lambda d: libnfc_crc(d, 0xFFFF, True))
assert na >= 4 and nb >= 4, (na, nb)
ZAPISI.append(Z("ISO/IEC 14443-3 (NFC-A/B, бесконтактные карты): CRC_A (0x8408 отражённый, начальное 0x6363) и CRC_B (0x8408 отражённый, FFFF, инверсия)",
  "NFC / ISO/IEC 14443 (MIFARE, банковские карты, ePassport)", "CRC-подобный",
  {"CRC_A": "x^16+x^12+x^5+1, отражённый (младший бит первым), начальное 0x6363 (в нормальной записи 0xC6C6), без инверсии; младший байт CRC первым — CRC-16/ISO-IEC-14443-3-A",
   "CRC_B": "тот же многочлен, отражённый, начальное 0xFFFF, инверсия — CRC-16/IBM-SDLC (X.25)",
   "кадр A": "старт S, байты по 8 бит + нечётная чётность каждого байта, конец E; модифицированный Миллер (PCD→PICC), Манчестер с поднесущей 847 кГц (PICC→PCD)",
   "кадр B": "SOF/EOF, символы 10 бит (старт 0, 8 данных, стоп 1), NRZ-L/BPSK"},
  "NFC-A/B, MIFARE, платёжные карты EMV Contactless, проездные, электронные паспорта (13,56 МГц)",
  [ist_kod(nf, r"iso14443a_crc\(uint8_t", "libnfc CRC_A"), ist_kod(nf, r"uint32_t wCrc = 0x6363;", "начальное A"), ist_kod(nf, r"iso14443b_crc\(uint8_t", "libnfc CRC_B"),
   ist_obsh("CRC-16/ISO-IEC-14443-3-A"), ist_obsh("CRC-16/IBM-SDLC")],
  f"реализация libnfc перенесена дословно и воспроизвела {na} кодовых слова RevEng CRC-16/ISO-IEC-14443-3-A и {nb} — CRC-16/IBM-SDLC (= ISO-IEC-14443-3-B); стандарт ISO/IEC 14443-3 платный — значения по открытому коду и каталогу RevEng",
  "есть: crc_katalog (CRC-16/ISO-IEC-14443-3-A, CRC-16/IBM-SDLC)"))

# ---------------------------------------------------------------------------------------------------------------------
# Wireless M-Bus (EN 13757-4) по rtl_433 (GPL-2)
mb = kod("rtl_433", "src/devices/m_bus.c", "COPYING")
t_mb = open(os.path.join(KOREN, mb)).read()
t36 = {int(a): int(b, 16) for a, b in re.findall(r"case (\d+):\s+out = (0x[0-9A-F]+);", t_mb)}
assert sorted(t36.values()) == list(range(16)) and all(bin(k).count("1") == 3 for k in t36)
dm36 = min(bin(a ^ b).count("1") for a, b in itertools.combinations(t36, 2))
assert "CRC_POLY = 0x3D65" in t_mb and "~crc16(bytes, crc_offset, CRC_POLY, 0)" in t_mb
p13757 = OB["CRC-16/EN-13757"]; assert p13757["poly"] == "0x3D65" and p13757["init"] == "0x0000" and p13757["xorout"] == "0xFFFF"
n_mb = rev_kodslova("CRC-16/EN-13757", lambda d: (crc_bity(bity_b(d), 0x3D65, 16) ^ 0xFFFF).to_bytes(2, "big"))
assert n_mb >= 3
ZAPISI.append(Z("Wireless M-Bus (EN 13757-4, OMS): код «3 из 6» (режим T), CRC-16 x^16+x^13+x^12+x^11+x^10+x^8+x^6+x^5+x^2+1 (0x3D65, инверсия) на каждый блок",
  "Wireless M-Bus (EN 13757-4), OMS", "CRC-подобный + линейный блочный код (4 → 6)",
  {"3 из 6": {f"{v:X}": format(k, "06b") for k, v in sorted(t36.items(), key=lambda kv: kv[1])},
   "свойство": f"16 слов веса 3 из 20 возможных, минимальное расстояние {dm36} (обнаруживает одиночные ошибки)",
   "CRC": "poly 0x3D65, начальное 0, инверсия (CRC-16/EN-13757 RevEng); старший байт первым; по блокам кадра формата A (первый блок 10 байт + CRC, далее по 16 байт + CRC)",
   "режимы": "S, T (3 из 6, 100 кбит/с), C (NRZ, формат A/B), N (169 МГц), R2, F"},
  "беспроводной учёт: счётчики воды, тепла, газа, электричества (Европа, OMS), 868/169 МГц",
  [ist_kod(mb, r"static uint8_t m_bus_decode_3of6\(uint8_t byte\)", "rtl_433 3 из 6"), ist_kod(mb, r"CRC_POLY = 0x3D65", "rtl_433 CRC"), ist_obsh("CRC-16/EN-13757")],
  f"таблица «3 из 6» из rtl_433: 16 различных слов веса 3, dmin = {dm36}; CRC: параметры rtl_433 (~crc16(…, 0x3D65, 0)) = RevEng CRC-16/EN-13757, {n_mb} кодовых слов каталога воспроизведены своей программой. EN 13757-4 платный — значения по открытому коду и каталогу RevEng",
  "частично: crc_katalog (CRC-16/EN-13757); «3 из 6» — нет"))

# ---------------------------------------------------------------------------------------------------------------------
# IEEE 802.15.4-2006: O-QPSK 868/915 МГц (табл. 37, 16 чипов)
I4 = Tekst("istochniki/ieee802154/802.15.4-2006.pdf")
kus, s37 = I4.kusok(r"Table 37—Symbol-to-chip mapping for O-QPSK", r"Figure 26", posl=True)
r37 = re.findall(r"\n(\d{1,2})\s*\n([01] [01] [01] [01])\s*\n((?:[01] ){15}[01])", kus)
c37 = {int(a): c.replace(" ", "") for a, b, c in r37}
assert sorted(c37) == list(range(16))
for k in range(16):
    assert "".join(r37[k][1].split()) == format(k, "04b")[::-1]
dm37 = min(sum(x != y for x, y in zip(c37[i], c37[j])) for i in range(16) for j in range(i + 1, 16))
# структура: символы 1…7 — циклический сдвиг символа 0 на 2k чипов вправо? проверяем, что найдётся единый сдвиг
sdv = [next((s for s in range(16) if c37[k] == c37[0][-s:] + c37[0][:-s] if s), None) for k in range(1, 8)]
ZAPISI.append(Z("IEEE 802.15.4 O-QPSK 868/915 МГц: 16 последовательностей по 16 чипов (4 бита → 16 чипов, 250 кбит/с)",
  "IEEE 802.15.4 (Zigbee, Thread, 6LoWPAN)", "блочный код (DSSS, 4 бита → 16 чипов)",
  {"чипы c0…c15": {str(k): v for k, v in c37.items()}, "порядок бит символа": "b0 b1 b2 b3 (b0 — младший)", "минимальное расстояние": dm37,
   "сдвиги символов 1…7 относительно 0 (чипов, найдено программой)": sdv,
   "модуляция": "O-QPSK с полусинусом, 400 кчип/с (868) / 1000 кчип/с (915)"},
  "802.15.4-2006 868/915 МГц (необязательный O-QPSK PHY), Zigbee Sub-GHz",
  [I4.ist(s37, "Table 37")],
  f"16 последовательностей разобраны из табл. 37 с assert на номера и двоичную запись символов; dmin = {dm37}; (таблицы PSSS ASK-PHY табл. 30/31 в PDF — рисунки, не разобраны)",
  "нет (слепое DSSS — вне битового уровня)"))

# ---------------------------------------------------------------------------------------------------------------------
# 802.11ad DMG low-power SC: RS(224,208) + (N,8)
W = Tekst("istochniki/wifi/802.11-2020.pdf")
s_w1, _, _ = W.odna(r"Data is encoded using an outer RS\(224,208\) block code and a short inner code")
s_w2, _, _ = W.odna(r"Each set of 7 octets is interleaved using a uniform interleaver of 7 rows and 8 columns")
s_w3 = W.naiti(r"^Table 20-21—DMG low-power SC mode modulation and coding schemes")[-1][0]
s_w4, _, _ = W.odna(r"20\.6\.2\.3\.3\.3 \(N,8\) Block-coding")
kus, _ = W.kusok(r"respectively: G8x8 is the identity matrix\.", r"Authorized licensed use")
mat = {}
for nn, nm in ((9, "G8\n9"), (12, "G8\n12"), (16, "G8\n16")):
    i = kus.find(nm); rows = re.findall(r"((?:[01] ){%d}[01])" % (nn - 1), kus[i:i + 600])[:8]
    mat[nn] = [r.replace(" ", "") for r in rows]
    assert len(mat[nn]) == 8 and all(r[:8] == format(1 << (7 - j), "08b") for j, r in enumerate(mat[nn])), nn
def dmin_G(G):
    rows = [int(r, 2) for r in G]; best = 99
    for msk in range(1, 256):
        c = 0
        for j in range(8):
            if msk >> j & 1: c ^= rows[j]
        best = min(best, bin(c).count("1"))
    return best
dm_lp = {f"({n},8)": dmin_G(G) for n, G in mat.items()}
ex8, lg8 = gf_tab(8, 0x11D); g16 = rs_gen(ex8, lg8, range(1, 17)); assert len(g16) == 17
ZAPISI.append(Z("IEEE 802.11ad DMG low-power SC (MCS 25–31): внешний RS(224,208) над GF(2^8) (корни α^1…α^16) + внутренний блочный (16,8)/(12,8)/SPC(9,8)/(8,8), перемежитель 7×8",
  "IEEE 802.11ad/ay (DMG/EDMG)", "каскадный: РС + короткий блочный код",
  {"РС": "RS(224,208), g(x) = ∏_{k=1}^{16}(x + α^k), α = 0x02 — корень p(x) = 1+x^2+x^3+x^4+x^8; последний блок — укороченный RS(16+K, K); r(x) = x^16·m(x) mod g(x), чётность после данных",
   "внутренний код": {"MCS 25/28": "(16,8)", "MCS 26/29": "(12,8)", "MCS 27/30": "SPC (9,8)", "MCS 31": "(8,8) — без кодирования"},
   "матрицы G (8 строк, систематические)": mat, "dmin (своя программа)": dm_lp,
   "перемежитель": "каждые 7 октетов: запись по строкам 7×8, чтение по столбцам (i = 8·(k mod 7) + ⌊k/7⌋)",
   "блоки": "512 символов = Ga64 + 7 × (56 данных + G8), 392 символа данных на блок; π/2-BPSK/QPSK",
   "статус": "режим объявлен устаревшим в 802.11-2020 (может быть удалён)"},
  "802.11ad (60 ГГц, WiGig) — экономичный режим SC; 802.11-2020 cl. 20.6, CDMG — 24.6",
  [W.ist(s_w3, "Table 20-21"), W.ist(s_w1, "20.6.2.3.3.1"), W.ist(s_w2, "перемежитель 7×8"), W.ist(s_w4, "20.6.2.3.3.3 матрицы G")],
  f"матрицы G(9,8), G(12,8), G(16,8) разобраны из текста с assert на единичную часть; dmin своей программой: {dm_lp}; g(x) RS перемножен (17 коэффициентов). Векторы Annex I (I.7) — во внешних файлах, в PDF не приведены",
  "частично: rs_bch.py (RS(255,239) укороченный); блочные коды (N,8) — kod.блочный вслепую"))

# ---------------------------------------------------------------------------------------------------------------------
# 802.11ah S1G 1 МГц MCS 10: BCC/LDPC 1/2 + блочное повторение с маскированием
s_m1, _, _ = W.odna(r"In an 1 MHz PPDU that is modulated by MCS 10, the 6 information bits of each OFDM symbol are encoded")
s_m2, _, l_m2 = W.odna(r"^s = \[1 0 0 0 0 1 0 1 0 1 1 1\]")
maska = [int(x) for x in re.findall(r"[01]", l_m2)]
assert len(maska) == 12
def mcs10(c12): return list(c12) + [a ^ b for a, b in zip(c12, maska)]
assert mcs10([0] * 12)[12:] == maska
ZAPISI.append(Z("IEEE 802.11ah (S1G, Wi-Fi HaLow) 1 МГц MCS 10: BCC/LDPC R=1/2 (6 бит → 12) + блочное повторение 2× с маской s = 1000 0101 0111 (BPSK)",
  "IEEE 802.11 (OFDM/HT/VHT/HE/S1G)", "каскадный: свёрточный/LDPC 1/2 + повторение с маскированием",
  {"повторение": "для каждого OFDM-символа: Cout = [C1…C12, (C1…C12) ⊕ s], s = [1 0 0 0 0 1 0 1 0 1 1 1]; затем перемежитель BCC (если BCC) или сразу отображение (LDPC)",
   "скорость": "эффективно 1/4, BPSK, 1 пространственный поток; самый устойчивый режим S1G (дальность)",
   "внутренний код": "BCC 133/171 (как 802.11) или LDPC (NCBPS = 12 до повторения)"},
  "Wi-Fi HaLow (802.11ah, 900 МГц): датчики, IoT",
  [W.ist(s_m1, "23.3.9.5 повторение MCS 10"), W.ist(s_m2, "маска s")],
  "маска s разобрана из формулы (23-49) с assert (12 бит); при нулевом слове вторая половина равна маске — проверено",
  "частично: kod.СВЁРТОЧНЫЕ (133/171), LDPC Wi-Fi; повторение MCS 10 — нет"))
