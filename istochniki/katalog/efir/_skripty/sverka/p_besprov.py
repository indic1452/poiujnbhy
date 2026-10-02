"""Сверка: Wi-Fi, Bluetooth, 802.15.4, Z-Wave, LoRa, NB-IoT, WiMAX, ECMA-368 (+ CRC по каталогу RevEng области obshchie)."""
from obsh import *
import re, html, json, itertools
R = []
def rez(imya, chto, ist, metod, rezultat="совпало"):
    R.append({"запись": zapis(imya)["имя"], "что сверено": chto, "источник": ist, "метод": metod, "результат": rezultat})
W = "wifi/802.11-2020.pdf.txt"
def hext(nazv, do, stranicy):
    t = str_(W, *stranicy); i = t.find(nazv); j = t.find(do, i)
    return [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})\b", t[i:j])]
a = hext("Table I-13—The DATA bits before scrambling", "Table I-14—", [4161, 4162])
b = hext("Table I-15—The DATA bits after scrambling", "Table I-16—", [4163, 4164])
c = hext("Table I-16—The BCC encoded DATA bits", "Table I-17—", [4165, 4166, 4167])
assert (len(a), len(b), len(c)) == (108, 108, 144)
A = bajty_v_bity(a, False); B = bajty_v_bity(b, True); C = bajty_v_bity(c, True)   # I-13: B7…B0, I-15/I-16: B0…B7
k = [A[i] ^ B[i] for i in range(7)]
for n in range(7, len(A)): k.append(k[n - 4] ^ k[n - 7])
diff = [i for i in range(len(A)) if A[i] ^ k[i] != B[i]]
assert all(816 <= i < 822 and B[i] == 0 for i in diff)
sh = lfsr_lyuboy([7, 4, 0], bity("1011101"), k[:127]); assert sh
z = zapis("Wi-Fi OFDM"); assert lfsr_lyuboy([7, 4, 0], [1] * 7, bity(z["параметры"]["ПСП (начало 1111111)"]))
rez("Wi-Fi OFDM", "x^7+x^4+1, ПСП 127 бит, пример Annex I (табл. I-13 → I-15)", "802.11-2020 17.3.5.5 и Annex I, стр. 4161–4164",
    f"своя программа: гамма = I-13 ⊕ I-15 подчиняется k[n] = k[n−4] ⊕ k[n−7] на всех 864 битах и порождается затравкой 1011101; расходятся только биты {diff} — это 6 хвостовых бит (816–821), обнуляемых после скремблирования (17.3.5.3). Отсюда «107 из 108 байт» в записи — пояснение добавлено", "уточнено")
out = svyortka(B, [133, 171], 7); s = []
for i, (x, y) in enumerate(out):
    s += [x, y] if i % 3 == 0 else ([x] if i % 3 == 1 else [y])
assert s == C
rez("Wi-Fi BCC", "133/171 и выкалывание 3/4 (A1 B1 A2 B3)", "802.11-2020 Annex I табл. I-16, стр. 4165–4166", "свой кодер: 1152 из 1152 бит табл. I-16 совпали")
m = bity("1 0 0 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 1")
assert format(crc(m, 0x07, 8, 0xFF, 0xFF), "08b") == "00011100"
est(W, [3071], "{c7, … c0} are {0 0 0 1 1 1 0 0}")
rez("Wi-Fi CRC-8", "x^8+x^2+x+1, начальное 1…1, инверсия", "802.11-2020 21.3.10.3, стр. 3071", "свой CRC даёт 00011100 на примере VHT-SIG-B — как в тексте")
# Голей DMG
def golay(D, Wk):
    Aa = [1] + [0] * 127; Bb = Aa[:]
    n = 1 << len(D)
    Aa = [1 if i == 0 else 0 for i in range(n)]; Bb = Aa[:]
    for d, w in zip(D, Wk):
        Bs = [Bb[i - d] if i - d >= 0 else 0 for i in range(n)]
        Aa, Bb = [w * Aa[i] + Bs[i] for i in range(n)], [w * Aa[i] - Bs[i] for i in range(n)]
    return Aa[::-1], Bb[::-1]
ga, gb = golay([1, 8, 2, 4, 16, 32, 64], [-1, -1, -1, -1, 1, -1, -1])
sga = "".join("+" if v > 0 else "-" for v in ga)
assert sga == zapis("WiGig 802.11ad: дополнительные")["параметры"]["Ga128"]
acf = [sum(ga[i] * ga[i + s] for i in range(128 - s)) + sum(gb[i] * gb[i + s] for i in range(128 - s)) for s in range(1, 128)]
assert all(v == 0 for v in acf)
rez("WiGig 802.11ad: дополнительные", "Ga128 по рекурсии Dk/Wk", "802.11-2020 20.?, стр. 3006",
    "своя рекурсия с D = [1 8 2 4 16 32 64], W = [−1 −1 −1 −1 +1 −1 −1] даёт Ga128 записи; сумма апериодических АКФ Ga128 и Gb128 = 0 при всех сдвигах (дополнительная пара)")
# Bluetooth BR/EDR
def bt_html(f):
    t = open(os.path.join(KOREN, "istochniki/bluetooth", f), errors="replace").read()
    t = re.sub(r"<[^>]+>", " ", t); t = html.unescape(t); return re.sub(r"[ \t]+", " ", t)
SD = bt_html("core60_br-edr_sample-data.html")
kus = SD[SD.find("3 . Access code sample data"):SD.find("4 . HEC and packet header sample data")]
rows = re.findall(r"([0-9a-f]{6}) \| ([5a]) \| ([0-9a-f]{8}) ([0-9a-f]{8}) \| ([5a])", kus)
assert len(rows) == 130
z = zapis("Bluetooth BR/EDR синхрослово")
g = 0x585713DA9; P = 0x83848D96BBCC54FC
assert poly_mod2_mul(0x37CD0EB67, 3) == g
def sync(lap):
    p = [(P >> k) & 1 for k in range(64)]   # p_k = разряд k (младший — p0)
    a_ = [(lap >> k) & 1 for k in range(24)]
    a_ += [0, 0, 1, 1, 0, 1] if a_[23] == 0 else [1, 1, 0, 0, 1, 0]
    x = [a_[k] ^ p[34 + k] for k in range(30)]
    r = poly_mod2_mod(sum(v << k for k, v in enumerate(x)) << 34, g)
    s_ = [((r >> k) & 1) ^ p[k] for k in range(34)] + [x[k] ^ p[34 + k] for k in range(30)]
    return int("".join(map(str, s_)), 2)
ok = sum(sync(int(l, 16)) == int(h1 + h2, 16) for l, _, h1, h2, _ in rows)
assert ok == 130
rez("Bluetooth BR/EDR синхрослово", "g(D) = (1+D)·0x37CD0EB67, ПСП 0x83848D96BBCC54FC, биты Баркера", "Core 6.0 Vol 2 Part B 6.3.3.1 + Sample Data §3",
    "своя реализация построения синхрослова из LAP воспроизвела все 130 синхрослов «Access code sample data» (GIAC, DIAC и др.)")
kus = SD[SD.find("4 . HEC and packet header sample data"):SD.find("5 . CRC sample data")]
hec = re.findall(r"\b([0-9a-f]{2}) ([0-9a-f]{3}) ([0-9a-f]{2}) ([0-7]{6}) ([0-7]{6}) ([0-7]{6})", kus)
def bt_hec(uap, data10):
    r = uap   # регистр загружается UAP (разряд 7 — старший)
    for i in range(10):
        bit = (data10 >> i) & 1
        f = ((r >> 7) & 1) ^ bit
        r = (r << 1) & 0xFF
        if f: r ^= 0xA7       # D^8+D^7+D^5+D^2+D+1 без старшего: D^7+D^5+D^2+D+1 = 0xA7
    return r
nok = 0
for u, d, h, o1, o2, o3 in hec:
    u, d, h = int(u, 16), int(d, 16), int(h, 16)
    v = int(format(bt_hec(u, d), "08b")[::-1], 2)
    oct_ = o1 + o2 + o3; bits = [1 if ch_ == "7" else 0 for ch_ in oct_]
    hb = bits[10:18]
    if v == h and bits[:10] == [(d >> i) & 1 for i in range(10)] and hb == [(h >> i) & 1 for i in range(8)]: nok += 1
assert nok == len(hec) and nok >= 20, (nok, len(hec))
rez("Bluetooth BR/EDR HEC", "HEC D^8+D^7+D^5+D^2+D+1, начальное UAP; FEC 1/3 заголовка", "Core 6.0 Sample Data §4",
    f"своя реализация воспроизвела все {nok} строк таблицы (HEC и 54-битный заголовок в восьмеричном виде: 10 бит данных + 8 бит HEC, каждый ×3 — проверено и повторение 1/3)")
# BLE
LS = bt_html("core60_le_sample-data.html")
def crc24_ble(pdu, init):
    r = init   # загрузка как записано
    for byte in pdu:
        for i in range(8):
            bit = (byte >> i) & 1
            f = (r >> 23 & 1) ^ bit
            r = (r << 1) & 0xFFFFFF
            if f: r ^= 0x00065B
    return r
v = crc24_ble(bytes.fromhex("0003424C45"), 0x555555)
# передача: с позиции 23 (старший разряд регистра) первым → как байты младшим вперёд
bts = [(v >> (23 - i)) & 1 for i in range(24)]
by = [sum(bts[8 * j + i] << i for i in range(8)) for j in range(3)]
assert by == [0x29, 0x0A, 0xCE], [hex(x) for x in by]
rez("Bluetooth LE: CRC-24", "CRC-24 (0x00065B), начальное 0x555555, передача с позиции 23", "Core 6.0 LE Sample Data",
    "своя реализация: PDU 00 03 42 4C 45 → CRC в эфирном порядке 29 0A CE — как в записи (пример Sample Data)")
# 802.15.4 / Z-Wave / LoRa: CRC по RevEng (область obshchie)
OB = {z_["имя"]: z_ for z_ in json.load(open(os.path.join(os.path.dirname(KOREN), "obshchie", "katalog.json"))) if z_["семейство"].startswith("CRC каталог RevEng")}
def crc_rev(nm, data):
    p = OB[nm]["параметры"]; w = p["width"]; poly = int(p["poly"], 16); r = int(p["init"], 16)
    for byte in data:
        if p["refin"]: byte = int(format(byte, "08b")[::-1], 2)
        for i in range(8):
            f = ((r >> (w - 1)) & 1) ^ ((byte >> (7 - i)) & 1); r = (r << 1) & ((1 << w) - 1)
            if f: r ^= poly
    if p["refout"]: r = int(format(r, f"0{w}b")[::-1], 2)
    return r ^ int(p["xorout"], 16)
for nm in OB: assert crc_rev(nm, b"123456789") == int(OB[nm]["параметры"]["check"], 16) or OB[nm]["параметры"]["width"] > 64, nm
def svoy_crc(bits, poly, w, init=0, xorout=0): return crc(bits, poly, w, init, xorout)
chk = b"123456789"
# 802.15.4 FCS: b0 первого октета — старшая степень → refin; r0 первым → refout; init 0 → CRC-16/KERMIT
v = crc(bajty_v_bity(chk, False), 0x1021, 16, 0, 0); v = int(format(v, "016b")[::-1], 2)
assert v == crc_rev("CRC-16/KERMIT", chk)
rez("IEEE 802.15.4 FCS", "параметры FCS (x^16+x^12+x^5+1, начальное 0, младший бит первым)", "802.15.4-2006 7.2.1.9 + RevEng (obshchie: CRC-16/KERMIT)",
    "своя реализация по тексту стандарта на «123456789» = check RevEng CRC-16/KERMIT (0x2189); каталожное имя в записи верно")
v = crc(bajty_v_bity(chk), 0x1021, 16, 0x1D0F, 0)
assert v == crc_rev("CRC-16/SPI-FUJITSU", chk)
rez("Z-Wave", "CRC-16 R3: 0x1021, начальное 0x1D0F, без инверсии", "G.9959 + RevEng (obshchie: CRC-16/SPI-FUJITSU)",
    "параметры записи = CRC-16/SPI-FUJITSU (AUG-CCITT) RevEng, check 0xE5CC совпал со своей реализацией")
v = crc(bajty_v_bity(chk), 0x07, 8, 0, 0); assert v == crc_rev("CRC-8/SMBUS", chk)
rez("WiMAX 802.16 MAC", "HCS: CRC-8 0x07, начальное 0 (= CRC-8/SMBUS RevEng); пример [80 AA AA 0F 0F] → 0xD5", "802.16-2004 стр. 75 + RevEng",
    f"своя реализация: пример стандарта → {crc(bajty_v_bity(bytes.fromhex('80AAAA0F0F')), 0x07, 8):#04x}; check «123456789» = CRC-8/SMBUS")
assert crc(bajty_v_bity(bytes.fromhex("80AAAA0F0F")), 0x07, 8) == 0xD5
v = crc(bajty_v_bity(chk), 0xD5, 8); assert v == crc_rev("CRC-8/DVB-S2", chk)
rez("DVB-T2 CRC-8", "CRC-8 0xD5 начальное 0 (CRC-8/DVB-S2), CRC-32 MPEG-2", "EN 302 755 + RevEng (obshchie: CRC-8/DVB-S2, CRC-32/MPEG-2)",
    "свои реализации совпали с check RevEng обоих CRC")
assert crc(bajty_v_bity(chk), 0x04C11DB7, 32, 0xFFFFFFFF, 0) == crc_rev("CRC-32/MPEG-2", chk)
v = crc(bajty_v_bity(chk), 0x1021, 16, 0xFFFF, 0xFFFF); assert v == crc_rev("CRC-16/GENIBUS", chk)
rez("DAB CRC-16", "0x1021, FFFF, инверсия, старший бит первым = CRC-16/GENIBUS", "EN 300 401 + RevEng", "check совпал; имя в записи верно")
# HD Radio CRC-8 = CRC-8/NRSC-5?
p = OB["CRC-8/NRSC-5"]["параметры"]
rez("HD Radio аудио-PDU", "CRC-8 x^8+x^5+x^4+1 ↔ RevEng CRC-8/NRSC-5", "NRSC-5-D 1017s + RevEng", f"RevEng CRC-8/NRSC-5: poly {p['poly']}, init {p['init']}, refin {p['refin']} — многочлен 0x31 = x^8+x^5+x^4+1 совпал")
assert int(p["poly"], 16) == 0x31
# LoRa отбеливание
z = zapis("LoRa отбеливание")
w0 = 0xFF; seq = [w0]
for _ in range(9):
    w = seq[-1]; fb = ((w >> 7) ^ (w >> 5) ^ (w >> 4) ^ (w >> 3)) & 1; seq.append(((w << 1) & 0xFF) | fb)
assert " ".join(f"{x:02X}" for x in seq) == z["параметры"]["последовательность"].split(": ")[1].split(" …")[0]
tab = open(os.path.join(KOREN, "istochniki/kod/gr-lora_sdr/lib/tables.h")).read()
vals = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", tab)]
cur = 0xFF; mine = []
for _ in range(255):
    mine.append(cur); fb = ((cur >> 7) ^ (cur >> 5) ^ (cur >> 4) ^ (cur >> 3)) & 1; cur = ((cur << 1) & 0xFF) | fb
assert vals[:255] == mine
rez("LoRa отбеливание", "255 байт отбеливания", "gr-lora_sdr tables.h (GPL-3)", "своя рекуррентная формула записи дала все 255 байт таблицы gr-lora_sdr")
# NB-IoT Голд
def gold(cinit, n, Nc=1600):
    x1 = [1] + [0] * 30; x2 = [(cinit >> i) & 1 for i in range(31)]
    for k in range(n + Nc):
        x1.append(x1[k + 3] ^ x1[k]); x2.append(x2[k + 3] ^ x2[k + 2] ^ x2[k + 1] ^ x2[k])
    return [x1[k + Nc] ^ x2[k + Nc] for k in range(n)]
vyv = open(os.path.join(KOREN, "skripty/gold_srsran_vyvod.txt")).read()
nsov = 0
for m_ in re.finditer(r"(?m)^(\d+) ([01]{32,})$", vyv):
    ci = int(m_.group(1), 0); bs = m_.group(2)
    assert "".join(map(str, gold(ci, len(bs)))) == bs, ci; nsov += 1
assert nsov >= 3, vyv[:300]
rez("3GPP ГПСП Голда", "c(n) = x1(n+1600) ⊕ x2(n+1600)", "TS 36.211 7.2 + вывод srsRAN (skripty/gold_srsran_vyvod.txt)",
    f"своя реализация по формулам записи совпала с выводом скомпилированного sequence.c srsRAN для {nsov} значений c_init")
# WiMAX RS-CC пример
X = "wimax/802.16-2004_usf.pdf.txt"
t = str_(X, 482)
def hx(nm, do):
    i = t.find(nm); j = t.find(do, i); return [int(x, 16) for x in re.findall(r"\b([0-9A-F]{2})\b", t[i + len(nm):j])]
vh = hx("Input Data (Hex)", "Randomized"); rnd = hx("Randomized Data (Hex)", "Reed"); rs = hx("Reed–Solomon encoded Data (Hex)", "Convolutionally")
cc = hx("Convolutionally Encoded Data (Hex)", "Interleaved")
gf = GF(8, 0x11D); gg = gf.gen(range(16))   # g[0] — свободный член
def rs_enc(msg):
    rem = [0] * 16
    for m_ in [0] * (239 - len(msg)) + msg:
        f = m_ ^ rem[-1]
        rem = [0] + rem[:-1]
        if f:
            for i in range(16): rem[i] ^= gf.mul(gg[i], f)
    return rem[::-1]     # старший первым
par = rs_enc(rnd + [0])   # хвостовой байт 0x00 входит в сообщение RS (K' = 36)
assert par[:4] == rs[:4] and rs[4:4 + 35] == rnd and rs[-1] == 0
bits = bajty_v_bity(rs); out = svyortka(bits, [171, 133], 7); s = []
pat = [("X", "Y"), ("Y",), ("X", "Y"), ("Y",), ("X",)]   # 5/6: X1 Y1 Y2 X3 Y4 X5
for i, (x, y) in enumerate(out):
    for q in pat[i % 5] if False else []: pass
seqv = []
for i in range(0, len(out), 5):
    b5 = out[i:i + 5]
    seqv += [b5[0][0], b5[0][1], b5[1][1], b5[2][0], b5[3][1], b5[4][0]]
mine = [sum(seqv[8 * j + q] << (7 - q) for q in range(8)) for j in range(len(seqv) // 8)]
assert mine == cc, (len(mine), len(cc))
rez("WiMAX 802.16 OFDM (256): RS-CC", "RS(40,36) T'=2 (первые 4 байта чётности перед данными), CC 171/133 5/6, хвост 0x00", "802.16-2004 8.3.3.5.1, стр. 482",
    "свой кодер RS(255,239) (корни λ^0…λ^15, p = 0x11D) с укорочением по сообщению «35 байт + хвостовой 0x00» дал 49 31 40 BF; свой свёрточный кодер 5/6 (X1Y1Y2X3Y4X5) дал все 48 байт «Convolutionally Encoded Data»")
# ECMA-368 скремблер
z = zapis("ECMA-368 скремблер")
for k_, (zat, perv) in z["параметры"]["затравки x[−1]…x[−15] → первые 16 бит"].items():
    xs = bity(zat)       # x[−1] … x[−15]
    hist = xs[::-1]      # x[−15] … x[−1]
    out = []
    for n in range(16):
        v = hist[-14] ^ hist[-15]; out.append(v); hist.append(v)
    assert out == bity(perv), k_
rez("ECMA-368 скремблер", "4 затравки и первые 16 бит (табл. 29)", "ECMA-368 стр. 68", "своя реализация x[n] = x[n−14] ⊕ x[n−15] от затравок записи даёт её первые 16 бит для всех 4 затравок")
gf = GF(8, 0x11D); assert gf.gen(range(1, 7)) == [117, 49, 58, 158, 4, 126, 1]
rez("ECMA-368 заголовок PLCP", "g(x) RS(23,17)", "ECMA-368 формула (7), стр. 63", "g(x) перемножен заново: 117, 49, 58, 158, 4, 126, 1 — как в записи")
# 802.15.4 чипы
z = zapis("IEEE 802.15.4 O-QPSK 2450"); c0 = bity(z["параметры"]["символ 0 (c0…c31)"])
est("ieee802154/802.15.4-2006.pdf.txt", [66], "11011001110000110101001000101110")
syms = [c0[-4 * k:] + c0[:-4 * k] if k else c0 for k in range(8)]
syms += [[b ^ (i & 1) for i, b in enumerate(s_)] for s_ in syms]
t = szh(str_("ieee802154/802.15.4-2006.pdf.txt", 66, 67))
nf = sum("".join(map(str, s_)) in t for s_ in syms)
dm = min(sum(x != y for x, y in zip(p_, q)) for i, p_ in enumerate(syms) for q in syms[i + 1:])
assert nf == 16 and dm == 12, (nf, dm)
rez("IEEE 802.15.4 O-QPSK 2450", "16 последовательностей по 32 чипа", "802.15.4-2006 табл. 24, стр. 66",
    "все 16 последовательностей, построенные по правилу записи (сдвиг на 4k, инверсия нечётных чипов), найдены в тексте табл. 24; dmin = 12")
