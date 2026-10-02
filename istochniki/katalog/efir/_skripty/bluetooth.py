"""Bluetooth Core 6.0 (bluetooth.com, HTML): BR/EDR — синхрослово (64,30), HEC, CRC-16, FEC 1/3 и (15,10), отбеливание;
LE — CRC-24, отбеливание, LE Coded (свёрточный K=4 + отображение S=2/S=8). Проверка — Sample Data того же стандарта."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

BB = "istochniki/bluetooth/core60_br-edr_baseband-specification.html"; BS = "istochniki/bluetooth/core60_br-edr_sample-data.html"
LL = "istochniki/bluetooth/core60_le_link-layer-specification.html"; LS = "istochniki/bluetooth/core60_le_sample-data.html"
Lb, Ls, Ll, Lls = html_tekst(BB), html_tekst(BS), html_tekst(LL), html_tekst(LS)
ZAPISI = []
def najti(L, pat):
    for i, l in enumerate(L):
        if re.search(pat, l): return i
    raise AssertionError(pat)
def ist(put, L, pat, chto=""):
    i = najti(L, pat)
    z = {"файл": put, "строки": f"{i + 1} (строка текста HTML без тегов)", "url": url(put)}
    if chto: z["что"] = chto
    return z
def rev(v, n): return int(format(v, f"0{n}b")[::-1], 2)
def crc_lsb(bits, poly, n, init):
    reg = init
    for b in bits:
        fb = ((reg >> (n - 1)) & 1) ^ b
        reg = (reg << 1) & ((1 << n) - 1)
        if fb: reg ^= poly & ((1 << n) - 1)
    return reg
def pmul(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r

# --- синхрослово ---------------------------------------------------------------------
i_g = najti(Lb, r"generator polynomial 0x37CD0EB67 of a primitive binary \(63,30\) BCH")
i_g2 = najti(Lb, r"g D = 0 x 585713 DA 9")
assert pmul(0x37CD0EB67, 0b11) == 0x585713DA9
i_pn = najti(Lb, r"The PN sequence generated \(including the extra terminating zero\) becomes 0x83848D96BBCC54FC")
pn = format(0x83848D96BBCC54FC, "064b")
p63 = [int(c) for c in pn[:63]]; h = [0, 1, 3, 4, 6]
rek = all(p63[n] == (p63[n - 6] ^ p63[n - 5] ^ p63[n - 3] ^ p63[n - 2]) for n in range(6, 63)) or all(p63[n] == (p63[n - 6] ^ p63[n - 4] ^ p63[n - 3] ^ p63[n - 1]) for n in range(6, 63))
assert rek and pn[63] == "0"
ZAPISI.append(Z("Bluetooth BR/EDR синхрослово кода доступа: расширенный (64,30) код БЧХ + ПСП-наложение + Баркер-7", "Bluetooth BR/EDR (Core 6.0)", "БЧХ (расширенный, с наложением ПСП)",
  {"код": "(64,30), g(D) = (1 + D)·g'(D), g' = 0x37CD0EB67 (примитивный БЧХ (63,30)), g = 0x585713DA9; dmin = 14", "информация": "LAP (24 бита) + 6 бит Баркера (001101 при LAP MSB = 0, иначе 110010)",
   "ПСП": "h(D) = 1 + D + D^3 + D^4 + D^6, p = 0x83848D96BBCC54FC (63 бита + 0)", "код доступа": "преамбула 4 бита (0101/1010) + синхрослово 64 бита + трейлер 4 бита"},
  "Bluetooth BR/EDR: CAC, DAC, GIAC/DIAC (запрос 0x9E8B33 → синхрослово)", [ist(BB, Lb, r"generator polynomial 0x37CD0EB67", "6.3.3.1"), ist(BB, Lb, r"g D = 0 x 585713 DA 9"), ist(BB, Lb, r"becomes 0x83848D96BBCC54FC", "ПСП")],
  "(1+D)·0x37CD0EB67 = 0x585713DA9 (перемножено); 63 бита ПСП удовлетворяют рекурренту h(D), 64-й бит = 0", "нет"))

# --- HEC ----------------------------------------------------------------------------------
i_h = najti(Lb, r"The HEC generating LFSR is depicted in Figure 7\.3")
i_hs = najti(Ls, r"^4 \. HEC and packet header sample data")
hp = mnogochlen([8, 7, 5, 2, 1, 0])
prim = []
k = i_hs
while k < i_hs + 60 and len(prim) < 6:
    if re.fullmatch(r"[0-9a-f]{2}", Ls[k]) and re.fullmatch(r"[0-9a-f]{3}", Ls[k + 1]) and re.fullmatch(r"[0-9a-f]{2}", Ls[k + 2]):
        prim.append((int(Ls[k], 16), int(Ls[k + 1], 16), int(Ls[k + 2], 16))); k += 3
    else: k += 1
assert len(prim) == 6
for uap, d, hx in prim:
    r = crc_lsb([(d >> i) & 1 for i in range(10)], hp, 8, uap)
    assert rev(r, 8) == hx, (uap, d, hx)
i_c = najti(Lb, r"CRC-CCITT generator polynomial g \( D \) = D 16 \+ D 12 \+ D 5 \+ 1")
ZAPISI.append(Z("Bluetooth BR/EDR HEC (8 бит) и CRC-16 пакетов, инициализация UAP", "Bluetooth BR/EDR (Core 6.0)", "CRC-подобный",
  {"HEC": "g(D) = (D+1)(D^7+D^4+D^3+D^2+1) = D^8+D^7+D^5+D^2+D+1 по 10 битам заголовка; начальное — UAP (DCI для FHS запроса)", "CRC-16": "CRC-CCITT D^16+D^12+D^5+1, начальное — UAP (в младших 8 разрядах)",
   "порядок": "биты заголовка — младший первым; значение HEC в примерах — обратный порядок бит регистра"},
  "Bluetooth BR/EDR: заголовок пакета (18 бит + HEC, затем FEC 1/3), полезная нагрузка ACL/eSCO", [ist(BB, Lb, r"The HEC generating LFSR is depicted", "7.1.1"), ist(BB, Lb, r"CRC-CCITT generator polynomial", "7.1.2"), ist(BS, Ls, r"^4 \. HEC and packet header sample data", "примеры HEC")],
  "все 6 примеров HEC из Sample Data (UAP 00/47, данные 123/124/125) воспроизведены", "частично: crc.py (CRC вслепую; инициализация UAP подбирается как начальное значение)"))

# --- FEC 2/3 (15,10) -----------------------------------------------------------------------
i_f = najti(Lb, r"The other FEC scheme is a \(15,10\) shortened Hamming code")
i_fs = najti(Ls, r"Rate 2/3 FEC -- \(15,10\) Shortened Hamming Code")
gh = mnogochlen([5, 4, 2, 0]); assert pmul(0b11, 0b10011) == gh
ex = []
for k in range(i_fs, i_fs + 40):
    if re.fullmatch(r"0x[0-9a-f]{3}", Ls[k]) and re.fullmatch(r"[01]{10} [01]{5}", Ls[k + 1]):
        ex.append((int(Ls[k], 16), Ls[k + 1]))
assert len(ex) == 10
for d, cw in ex:
    bits = [(d >> i) & 1 for i in range(10)]
    r = crc_lsb(bits, gh, 5, 0)
    par = format(r, "05b")
    assert cw == "".join(map(str, bits)) + " " + par, (d, cw, par)
ZAPISI.append(Z("Bluetooth BR/EDR FEC 1/3 (повторение) и FEC 2/3 — укороченный Хэмминг (15,10), g = (D+1)(D^4+D+1)", "Bluetooth BR/EDR (Core 6.0)", "Хэмминг (укороченный циклический)",
  {"1/3": "каждый бит повторяется 3 раза (заголовок, HV1)", "2/3": "(15,10), g(D) = D^5+D^4+D^2+1 (0x35); данные младшим битом вперёд, затем 5 проверочных; нагрузка дополняется нулями до кратного 10",
   "применение": "DM1/DM3/DM5, HV2, EV4, FHS"}, "Bluetooth BR/EDR",
  [ist(BB, Lb, r"The other FEC scheme is a \(15,10\) shortened Hamming code", "7.4"), ist(BS, Ls, r"Rate 2/3 FEC -- \(15,10\)", "примеры")],
  "10 примеров Sample Data (данные 0x001…0x200 → кодовые слова) воспроизведены делением на g(D)", "частично: kod.блочный (короткий циклический код вслепую)"))

# --- отбеливание BR ----------------------------------------------------------------------------
i_w = najti(Lb, r"The whitening word is generated with the polynomial g \( D \) = D 7 \+ D 4 \+ 1")
i_ws = najti(Ls, r"^Whitening LFSR D7\.\.\.\.\.D0")
seq = []; states = []
k = i_ws + 1
while k < len(Ls) and not re.fullmatch(r"[01]", Ls[k]): k += 1
while k + 1 < len(Ls) and re.fullmatch(r"[01]", Ls[k]) and re.fullmatch(r"[01]{7}", Ls[k + 1]):
    seq.append(int(Ls[k])); states.append(Ls[k + 1]); k += 2
assert len(seq) > 50, len(seq)
okw = sum(1 for i in range(len(seq)) if int(states[i][0]) == seq[i])   # выход = разряд D7 показанного состояния
assert okw == len(seq), (okw, len(seq))
rekw = all(seq[n] == seq[n - 7] ^ seq[n - 3] for n in range(7, len(seq))) or all(seq[n] == seq[n - 7] ^ seq[n - 4] for n in range(7, len(seq)))
assert rekw
ZAPISI.append(Z("Bluetooth BR/EDR отбеливание: D^7 + D^4 + 1, начальное из часов (CLK6…1, старший = 1)", "Bluetooth BR/EDR (Core 6.0)", "скремблер (рандомизатор)",
  {"многочлен": "g(D) = D^7 + D^4 + 1 (0x91)", "начальное": "биты CLK6…CLK1 и 1 в старшем разряде; заголовок и нагрузка без переинициализации; EDR — без отбеливания guard/sync/trailer"},
  "Bluetooth BR/EDR", [ist(BB, Lb, r"The whitening word is generated with the polynomial", "7.2"), ist(BS, Ls, r"^Whitening LFSR D7", "Sample Data")],
  f"последовательность Sample Data ({len(seq)} бит) = выход D7 указанных состояний и удовлетворяет рекурренту D^7+D^4+1", "частично: skrembler.py (x^7+x^4+1 находится)"))

# --- LE: CRC-24, отбеливание ----------------------------------------------------------------------
i_lc = najti(Ll, r"The polynomial has the form of x 24 \+ x 10 \+ x 9 \+ x 6 \+ x 4 \+ x 3 \+ x \+ 1")
i_ref = najti(Lls, r"^Access address: D6 BE 89 8E")
pdu = [int(x, 16) for x in Lls[i_ref + 1].split(":")[1].split()]; crc_t = [int(x, 16) for x in Lls[i_ref + 2].split(":")[1].split()]
p24 = mnogochlen([24, 10, 9, 6, 4, 3, 1, 0])
r = crc_lsb([(b >> i) & 1 for b in pdu for i in range(8)], p24, 24, 0x555555)
tx = [(r >> (23 - i)) & 1 for i in range(24)]
assert [sum(tx[8 * k + j] << j for j in range(8)) for k in range(3)] == crc_t
i_lw = najti(Lls, r"^Channel First 64 bits of the whitening sequence")
okc = 0
for k in range(i_lw + 1, i_lw + 41):
    m = re.fullmatch(r"(\d+) ((?:[01]{8} ?){8})", Lls[k])
    if not m: continue
    ch_ = int(m.group(1)); ref = m.group(2).replace(" ", "")
    # регистр 7 бит: позиция 0 = 1, позиции 1…6 = индекс канала (старший — в позиции 1)
    reg = [1] + [(ch_ >> (5 - j)) & 1 for j in range(6)]
    out = ""
    for _ in range(64):
        o = reg[6]; out += str(o)
        reg = [o] + reg[:6]; reg[4] ^= o
    okc += out == ref
assert okc >= 38, okc
ZAPISI.append(Z("Bluetooth LE: CRC-24 (x^24+x^10+x^9+x^6+x^4+x^3+x+1) и отбеливание x^7+x^4+1 от номера канала", "Bluetooth LE (Core 6.0)", "CRC-подобный + скремблер",
  {"CRC-24": "начальное 0x555555 (реклама) или CRCInit соединения; биты PDU младшим вперёд; CRC передаётся с позиции 23", "отбеливание": "x^7 + x^4 + 1, начальное: позиция 0 = 1, позиции 1–6 = номер канала (0…39); PDU + CRC",
   "адрес доступа рекламы": "0x8E89BED6", "преамбула": "0xAA/0x55 (1M), 2 октета (2M), 80 символов (Coded)"},
  "BLE 4.x–6.0 (1M, 2M, Coded), ISO-каналы", [ist(LL, Ll, r"x 24 \+ x 10 \+ x 9", "3.1.1"), ist(LS, Lls, r"^Access address: D6 BE 89 8E", "эталонный пакет"), ist(LS, Lls, r"^Channel First 64 bits", "отбеливание по каналам")],
  f"CRC эталонного пакета (PDU 00 03 42 4C 45 → 29 0A CE) воспроизведён; первые 64 бита отбеливания совпали для {okc} каналов из таблицы Sample Data", "частично: crc.py, skrembler.py"))

# --- LE Coded ------------------------------------------------------------------------------------
i_cc = najti(Ll, r"The convolutional FEC encoder uses a non-systematic, non-recursive rate ½ code with constraint length K=4")
i_fec = najti(Lls, r"^2\.2 \. Forward Error Correction encoder")
k = najti(Lls, r"^Access address$")
vx = (Lls[k + 1] + " " + Lls[k + 2]).replace("Input:", "").split()
vy = (Lls[k + 5] + " " + Lls[k + 6]).replace("Output:", "").split()
vin = [int(x) for x in vx]; vout = [int(x) for x in vy]
assert len(vin) == 32 and len(vout) == 64
kodv = conv_kod(vin, [0b1111, 0b1101], 4)   # G0 = 1+x+x²+x³, G1 = 1+x²+x³ (старший бит маски — самый старый)
assert kodv == vout, (kodv[:16], vout[:16])
k8 = najti(Lls, r"^2\.4 \. Transmitted symbols \(S=8\)")
ka = next(j for j in range(k8, k8 + 20) if Lls[j] == "Access Address")
aa8 = "".join(Lls[ka + 1:ka + 7]).replace(" ", "")
assert aa8.startswith("".join("0011" if b == 0 else "1100" for b in vout[:16]))
ZAPISI.append(Z("Bluetooth LE Coded PHY: свёрточный K=4 (G0 = 1+x+x²+x³, G1 = 1+x²+x³) + отображение S=2 / S=8", "Bluetooth LE (Core 6.0)", "свёрточный",
  {"код": "несистематический нерекурсивный 1/2, K = 4, начальное 0, хвост TERM 3 нуля", "S=8": "каждый кодовый бит → 4 символа: 0 → 0011, 1 → 1100 (125 кбит/с)", "S=2": "кодовый бит без расширения (500 кбит/с)",
   "кадр": "преамбула 80 символов (0011 1100 × 10); блок FEC 1: адрес доступа + CI (2 бита) + TERM1 — всегда S=8; блок FEC 2: PDU + CRC + TERM2 — S=2 или S=8"},
  "BLE 5.x+ дальний радиус (Coded PHY)", [ist(LL, Ll, r"constraint length K=4", "3.3.1"), ist(LS, Lls, r"^2\.2 \. Forward Error Correction encoder", "векторы FEC"), ist(LS, Lls, r"^2\.4 \. Transmitted symbols \(S=8\)", "символы S=8")],
  "32 бита адреса доступа закодированы и совпали с 64 выходными битами Sample Data; отображение S=8 совпало с переданными символами", "частично: svyortka.py (1/2 K=4 находится вслепую; отображение S=8 — через снятие повторения)"))
