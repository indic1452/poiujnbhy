"""LoRa (Semtech CSS): официальной спецификации физуровня нет — по открытым реверсам: Tapparel и др. (EPFL, arXiv 2002.08208,
gr-lora_sdr, GPL-3.0) и LoRaPHY (jkadbear, MIT), патент Semtech US 9 252 834 B2 (Gray + перемежитель в общем виде).
Проверка — два независимых открытых кода сравниваются поэлементно/исчерпывающим перебором."""
import re, sys, os, random
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

P = Tekst("istochniki/lora/arxiv_2002.08208_Tapparel_open-source_LoRa_PHY.pdf"); PT = Tekst("istochniki/lora/US9252834B2.pdf"); ZAPISI = []
tb = kod("gr-lora_sdr", "lib/tables.h"); he = kod("gr-lora_sdr", "lib/hamming_enc_impl.cc"); il = kod("gr-lora_sdr", "lib/interleaver_impl.cc")
hd = kod("gr-lora_sdr", "lib/header_impl.cc"); cr = kod("gr-lora_sdr", "lib/add_crc_impl.cc"); gm = kod("gr-lora_sdr", "lib/gray_mapping_impl.cc")
ut = kod("gr-lora_sdr", "include/gnuradio/lora_sdr/utilities.h"); lp = kod("LoRaPHY", "LoRaPHY.m")
t_lp = open(os.path.join(KOREN, lp)).read()
SEM = "LoRa (Semtech SX127x/SX126x, LoRaWAN)"

# отбеливание
w1 = c_massiv(tb, "whitening_seq")
w2 = [int(v, 16) for v in re.findall(r"0x([0-9a-f]+)", t_lp.split("self.whitening_seq = uint8([")[1].split("]")[0])]
assert w1 == w2 and len(w1) == 255
for a, b in zip(w1, w1[1:]):          # w[n+1] = (w[n] << 1 | (b7 ⊕ b5 ⊕ b4 ⊕ b3)) mod 256  ⇔ x^8 + x^6 + x^5 + x^4 + 1
    assert b == ((a << 1) & 0xFF) | (((a >> 7) ^ (a >> 5) ^ (a >> 4) ^ (a >> 3)) & 1)
assert len(set(w1)) == 255
s_w = P.odna(r"1\) Whitening: Whitening is an XOR of the information bits")[0]
ZAPISI.append(Z("LoRa отбеливание: ГПСП 8 бит x^8 + x^6 + x^5 + x^4 + 1, начальное 0xFF, 255 байт, XOR с байтами полезной нагрузки", SEM, "скремблер (рандомизатор)",
  {"последовательность": "w[n+1] = (w[n]·2 mod 256) | (w7 ⊕ w5 ⊕ w4 ⊕ w3), w[0] = 0xFF: FF FE FC F8 F0 E1 C2 85 0B 17 …", "область": "только полезная нагрузка (не заголовок, не CRC); при CR 4/5 бит чётности считается от отбеленных бит",
   "период": 255},
  "LoRa / LoRaWAN (IoT, 433/868/915 МГц)", [P.ist(s_w, "whitening"), ist_kod(tb, r"whitening_seq\[\]", "gr-lora_sdr"), ist_kod(lp, r"self\.whitening_seq = uint8", "LoRaPHY")],
  "255 байт gr-lora_sdr и LoRaPHY совпали; каждый следующий байт получается сдвигом с битом обратной связи w7⊕w5⊕w4⊕w3 (все 254 перехода), период 255 (m-последовательность)",
  "частично: skrembler.py (аддитивный скремблер 8 бит находится); LoRa — на уровне символов CSS после снятия Грея/перемежения"))

# Хэмминг
t_he = open(os.path.join(KOREN, he)).read()
assert "p0 = data_bin[3] ^ data_bin[2] ^ data_bin[1];" in t_he and "p3 = data_bin[3] ^ data_bin[1] ^ data_bin[0];" in t_he
def gr_enc(nib, crr):          # gr-lora_sdr: data_bin = int2bool(nib, 4) — старший бит первым
    d = [(nib >> (3 - i)) & 1 for i in range(4)]
    if crr != 1:
        p0 = d[3] ^ d[2] ^ d[1]; p1 = d[2] ^ d[1] ^ d[0]; p2 = d[3] ^ d[2] ^ d[0]; p3 = d[3] ^ d[1] ^ d[0]
        return (d[3] << 7 | d[2] << 6 | d[1] << 5 | d[0] << 4 | p0 << 3 | p1 << 2 | p2 << 1 | p3) >> (4 - crr)
    return d[3] << 4 | d[2] << 3 | d[1] << 2 | d[0] << 1 | (d[0] ^ d[1] ^ d[2] ^ d[3])
def lp_enc(nib, crr):          # LoRaPHY: bitget(w, 1) — младший бит
    b = lambda pos: [(nib >> (p - 1)) & 1 for p in pos]
    x = lambda l: sum(l) & 1
    p1, p2, p3, p4, p5 = x(b([1, 3, 4])), x(b([1, 2, 4])), x(b([1, 2, 3])), x(b([1, 2, 3, 4])), x(b([2, 3, 4]))
    return {1: p4 << 4 | nib, 2: p5 << 5 | p3 << 4 | nib, 3: p2 << 6 | p5 << 5 | p3 << 4 | nib, 4: p1 << 7 | p2 << 6 | p5 << 5 | p3 << 4 | nib}[crr]
def rev(v, n): return int(format(v, f"0{n}b")[::-1], 2)
KODY = {}
for crr in (1, 2, 3, 4):
    n = 4 + crr
    for nib in range(16):
        assert gr_enc(nib, crr) == rev(lp_enc(nib, crr), n), (crr, nib)      # порядок бит слова противоположный, код — один
    sl = [lp_enc(v, crr) for v in range(16)]
    KODY[f"4/{n}"] = {"слова (LoRaPHY, бит 1 = младший)": sl, "dmin": min(bin(a ^ b).count("1") for a in sl for b in sl if a != b)}
assert [KODY[k]["dmin"] for k in ("4/5", "4/6", "4/7", "4/8")] == [2, 2, 3, 4]
s_h = P.odna(r"rates CR ∈\{4/5, 4/6, 4/7, 4/8\}")[0]
ZAPISI.append(Z("LoRa помехоустойчивый код: 4/5 — чётность, 4/6 — укороченный Хэмминг, 4/7 — Хэмминг (7,4), 4/8 — расширенный Хэмминг (8,4); первые SF−2 полубайта — всегда 4/8", SEM, "Хэмминг",
  {"чётности (биты полубайта d0…d3, d0 — младший)": "p_a = d0⊕d1⊕d2 (5-й бит), p_b = d1⊕d2⊕d3 (6-й), p_c = d0⊕d1⊕d3 (7-й), p_d = d0⊕d2⊕d3 (8-й); 4/5: p = d0⊕d1⊕d2⊕d3",
   "слово": "d0 d1 d2 d3 затем p_a, p_b, p_c, p_d (сколько нужно по CR)", "кодовые слова и dmin": KODY,
   "заголовок": "первые SF−2 полубайт (заголовок + начало полезной нагрузки) — CR 4/8 и пониженная скорость (SF−2 бит на символ)"},
  "LoRa / LoRaWAN", [P.ist(s_h, "Hamming"), ist_kod(he, r"p0 = data_bin\[3\]", "gr-lora_sdr"), ist_kod(lp, r"p1 = LoRaPHY\.bit_reduce", "LoRaPHY")],
  "для всех 16 полубайт и 4 CR слова gr-lora_sdr и LoRaPHY совпали (с учётом противоположного порядка бит в целом); dmin = 2, 2, 3, 4",
  "частично: kod.блочный (короткие линейные коды вслепую); LoRa-цепочка — нет"))

# перемежитель
t_il = open(os.path.join(KOREN, il)).read(); assert "inter_bin[i][j] = cw_bin[mod((i - j - 1), sf_app)][i];" in t_il
assert "circshift(tmp(:,x), 1-x)" in t_lp
random.seed(1)
for sf in range(5, 13):
    for crr in (1, 2, 3, 4):
        cwl = 4 + crr; cw = [random.randrange(1 << cwl) for _ in range(sf)]
        cw_gr = [rev(c, cwl) for c in cw]                    # слово gr = LoRaPHY с обратным порядком
        gr = []
        for i in range(cwl):
            bits = [(cw_gr[(i - j - 1) % sf] >> (cwl - 1 - i)) & 1 for j in range(sf)]
            gr.append(int("".join(map(str, bits)), 2))
        lpo = []
        for x in range(cwl):
            col = [(cw[r] >> x) & 1 for r in range(sf)]
            col = col[x % sf:] + col[:x % sf]                      # circshift(·, 1−x) при x с единицы: сдвиг вверх на x−1
            lpo.append(sum(b << k for k, b in enumerate(col)))
        assert gr == lpo, (sf, crr)
s_i = P.odna(r"3\) Interleaving: LoRa uses a diagonal interleaver")[0]
s_ip = PT.odna(r"FIGS\. 3a, 3b, 3c illustrate three interleaving schemes")[0]
ZAPISI.append(Z("LoRa диагональный перемежитель: блок SF кодовых слов × (4+CR) бит → 4+CR символов по SF бит; символ i, бит j = слово[(i − j − 1) mod SF], бит i", SEM, "перемежитель",
  {"формула (gr-lora_sdr)": "inter[i][j] = cw[(i − j − 1) mod SF'][i], i — номер символа (0…3+CR), j — бит символа от старшего", "SF'": "SF − 2 для первого блока (заголовок) и при LDRO; SF — иначе",
   "первый блок": "к SF−2 битам добавляется бит чётности и 0 (пониженная скорость)", "Грей": "перед модуляцией — обратное Грея (TX: число ← «Грей→двоичное»), затем +1; для символов заголовка/LDRO ×4 (LoRaPHY)",
   "синхро": "преамбула из восходящих чирпов, 2 символа идентификатора сети (синхрослово, напр. 0x12 частная, 0x34 LoRaWAN), 2,25 нисходящих чирпа"},
  "LoRa / LoRaWAN", [P.ist(s_i, "Interleaving"), PT.ist(s_ip, "патент Semtech: схемы перемежения"), ist_kod(il, r"inter_bin\[i\]\[j\] = cw_bin", "gr-lora_sdr"), ist_kod(lp, r"circshift\(tmp\(:,x\), 1-x\)", "LoRaPHY"), ist_kod(gm, r"Gray Demap", "gr-lora_sdr Грей")],
  "формулы gr-lora_sdr и LoRaPHY дают одинаковые символы для SF = 5…12 и всех CR (случайные кодовые слова)", "нет"))

# контрольная сумма заголовка и CRC
t_hd = open(os.path.join(KOREN, hd)).read()
M = [[int(v) for v in r.split()] for r in t_lp.split("self.header_checksum_matrix = gf([")[1].split("]")[0].strip().split("\n")]
assert len(M) == 5 and all(len(r) == 12 for r in M)
def gr_chk(h):
    h0, h1, h2 = (h >> 8) & 15, (h >> 4) & 15, h & 15
    c4 = (h0 >> 3 ^ h0 >> 2 ^ h0 >> 1 ^ h0) & 1
    c3 = (h0 >> 3 ^ h1 >> 3 ^ h1 >> 2 ^ h1 >> 1 ^ h2) & 1
    c2 = (h0 >> 2 ^ h1 >> 3 ^ h1 ^ h2 >> 3 ^ h2 >> 1) & 1
    c1 = (h0 >> 1 ^ h1 >> 2 ^ h1 ^ h2 >> 2 ^ h2 >> 1 ^ h2) & 1
    c0 = (h0 ^ h1 >> 1 ^ h2 >> 3 ^ h2 >> 2 ^ h2 >> 1 ^ h2) & 1
    return [c4, c3, c2, c1, c0]
def lp_chk(h):
    v = [(h >> (11 - k)) & 1 for k in range(12)]              # de2bi(nibbles(1:3), 4, 'left-msb')
    return [sum(a * b for a, b in zip(r, v)) & 1 for r in M]
assert all(gr_chk(h) == lp_chk(h) for h in range(4096))
t_cr = open(os.path.join(KOREN, cr)).read(); assert "0x1021" in t_cr and "crc = crc ^ m_payload[m_payload_len - 1] ^ (m_payload[m_payload_len - 2] << 8);" in t_cr
s_hh = P.odna(r"check \(CRC\), and a checksum\. If the header is not present")[0]; s_pc = P.odna(r"5\) Payload and CRC: Finally, the last part of the packet")[0]
ZAPISI.append(Z("LoRa заголовок: 20 бит (длина 8, CR 3, флаг CRC 1, контрольная сумма 5 бит — линейный код по матрице 5×12) и CRC-16 полезной нагрузки (0x1021, init 0, ⊕ последние 2 байта)", SEM, "CRC-подобный",
  {"заголовок": "полубайты: длина (2), CR|CRC-флаг (1), контрольная сумма (1 бит + полубайт)", "матрица контрольной суммы (строки c4…c0, столбцы — 12 бит заголовка, старший первым)": M,
   "CRC полезной нагрузки": "CRC-16/XMODEM (0x1021, начальное 0, без инверсии) по байтам 0…L−3, затем XOR с байтом L−1 и (байтом L−2)<<8; передаётся младшим байтом вперёд (по gr-lora_sdr)",
   "неявный режим": "без заголовка — длина/CR/CRC заданы заранее"},
  "LoRa / LoRaWAN", [P.ist(s_hh, "header"), P.ist(s_pc, "payload CRC"), ist_kod(hd, r"bool c4 =", "gr-lora_sdr"), ist_kod(lp, r"self\.header_checksum_matrix = gf", "LoRaPHY"), ist_kod(cr, r"0x1021", "gr-lora_sdr CRC")],
  "формулы gr-lora_sdr и матрица LoRaPHY дают одинаковую контрольную сумму для всех 4096 заголовков; CRC — по коду gr-lora_sdr и LoRaPHY (calc_crc: XOR двух последних байт)",
  "частично: crc_katalog (CRC-16/XMODEM); особенность «XOR последних байт» — нет"))
