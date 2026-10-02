"""Sigfox: закрытый протокол — по открытым реверсам: Coman/Kröger «Renard» (librenard — ЛИЦЕНЗИЯ В РЕПОЗИТОРИИ НЕ УКАЗАНА: используется только для сверки, не как первоисточник)
и статье Coman, Scholz et al. / IACR ePrint 2020/1575 «(In)security of the Radio Interface in Sigfox» (реальные кадры).
Проверка — CRC всех 14 кадров статьи вычислены по librenard; свойства кодов (dmin типов кадров, БЧХ(15,11)) — вычислением."""
import re, sys, os, itertools
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

P = Tekst("istochniki/sigfox/iacr_2020-1575_Sigfox_radio_interface.pdf"); ZAPISI = []
up = kod("librenard", "src/uplink.c"); crc = kod("librenard", "src/sigfox_crc.c"); dl = kod("librenard", "src/downlink.c"); bch = kod("librenard", "src/bch_15_11.c")
SL = "Sigfox (UNB, 868/902 МГц)"
t_up = open(os.path.join(KOREN, up)).read()
FT = c_massiv_2d(up, "frametypes")
assert FT == [[0x06b, 0x08d, 0x35f, 0x611, 0x94c], [0x6e0, 0x0d2, 0x598, 0x6bf, 0x971], [0x034, 0x302, 0x5a3, 0x72c, 0x997]]
vse = [v for r in FT for v in r]
dmin_ft = min(bin(a ^ b).count("1") for a, b in itertools.combinations(vse, 2))
assert dmin_ft >= 5
def crc16(d):
    r = 0
    for b in d:
        r ^= b << 8
        for _ in range(8): r = ((r << 1) ^ 0x1021) & 0xFFFF if r & 0x8000 else (r << 1) & 0xFFFF
    return r
kadry = [l.strip().strip("[]") for l in P.stroki if re.fullmatch(r"\[?[0-9a-f]{20,}\]?", l.strip())]
proveril = 0
for k in kadry:
    ft = int(k[:3], 16)
    if ft in FT[0]:
        assert (len(k) - 7) % 2 == 0 and (~crc16(bytes.fromhex(k[3:-4]))) & 0xFFFF == int(k[-4:], 16), k
        proveril += 1
assert proveril == 14, proveril
assert "convcode(encoded->frame[0], encoded->frame[1], encoded->framelen_nibbles * 4, SFX_UL_FTYPELEN_NIBBLES * 4, 07);" in t_up
assert "convcode(encoded->frame[0], encoded->frame[2], encoded->framelen_nibbles * 4, SFX_UL_FTYPELEN_NIBBLES * 4, 05);" in t_up
s_uf = P.odna(r"^ft \(13\)∥hdr \(48\)∥payload \(0-96\)∥mac \(16-40\)∥crc \(16\)")[0]
s_kd = P.odna(r"^06b0001895e4101adcf6d5f")[0]
ZAPISI.append(Z("Sigfox восходящий кадр: тип кадра (12 бит, 15 слов, dmin ≥ 5) + заголовок + данные + MAC + CRC-16 (0x1021, init 0, инверсия); повторы 2 и 3 — свёрточное «кодирование» G = 7 (1+D+D²) и 5 (1+D²)", SL, "CRC-подобный + свёрточный (повторы)",
  {"формат": "преамбула 0xAAAAA (19 бит + ft 13 бит по статье / 12 бит по librenard) ∥ li(2) bf(1) rep(1) cnt(12) devid(32, мл. байт первым) ∥ данные 0…12 байт ∥ MAC 2…5 байт (AES-128 CBC-MAC) ∥ CRC-16",
   "типы кадров [повтор][длина: 1 бит, 1, 4, 8, 12 байт]": [[f"0x{v:03x}" for v in r] for r in FT], "dmin типов кадров": dmin_ft,
   "CRC": "CRC-16/XMODEM-подобный: g = 0x1021, начальное 0, по байтам заголовка+данных+MAC; передаётся инвертированным (~crc)",
   "повторы": "кадр 2 — полиномиальное умножение (свёрточный 1/1) на G = 7₈ = 1+D+D², кадр 3 — на G = 5₈ = 1+D²; тип кадра не кодируется; обратное — деление (без исправления)",
   "модуляция": "DBPSK 100 бит/с (Европа) / 600 бит/с (США), три повтора на разных частотах",
   "оговорка": "официальная «Sigfox Connected Objects Radio Specifications» v1.7/1.7.1 (build.sigfox.com) выдаётся только после принятия лицензионного соглашения — не скачивалась; librenard без лицензии"},
  "Sigfox (IoT: счётчики, трекеры)", [P.ist(s_uf, "2.1.1 формат"), P.ist(s_kd, "реальные кадры"), ist_kod(up, r"uint16_t frametypes\[3\]\[5\]", "librenard типы"), ist_kod(crc, r"CRC16_POLYNOMIAL 0x1021", "librenard CRC"), ist_kod(up, r"SFX_UL_FTYPELEN_NIBBLES \* 4, 07\)", "librenard свёртка")],
  f"CRC всех {proveril} кадров статьи IACR 2020/1575 (8 исходных и подделанных) вычислены по librenard и совпали; dmin 15 типов кадров = {dmin_ft}",
  "частично: crc_katalog (CRC-16/XMODEM, инверсия — CRC-16/GSM-подобно); свёрточное умножение 1/1 — нет"))

t_b = open(os.path.join(KOREN, bch)).read()
G = c_massiv(bch, "bch_15_11_generatormatrix"); H = c_massiv(bch, "bch_15_11_paritycheckmatrix")
assert len(G) == 11 and len(H) == 4
assert all(bin(g & h).count("1") % 2 == 0 for g in G for h in H)          # G·Hᵀ = 0
kodsl = [0]
for g in G: kodsl += [c ^ g for c in kodsl]
dm = min(bin(c).count("1") for c in kodsl if c)
assert dm == 3 and rang_gf2(G) == 11 and rang_gf2(H) == 4
kolonki = sorted(sum(((h >> (14 - j)) & 1) << (3 - i) for i, h in enumerate(H)) for j in range(15))
assert kolonki == list(range(1, 16))                                     # все ненулевые синдромы — код Хэмминга
t_dl = open(os.path.join(KOREN, dl)).read(); assert "Polynomial: x^9 + x^5 + 1" in t_dl and "(common.seqnum * common.devid) & 0x1ff" in t_dl
assert "#define CRC8_POLYNOMIAL 0x2f" in open(os.path.join(KOREN, crc)).read()
s_df = P.odna(r"^ft \(13\)∥ecc \(32\)∥payload \(64\)∥mac \(16\)∥crc \(8\)")[0]
ZAPISI.append(Z("Sigfox нисходящий кадр: 8 перемеженных кодов БЧХ(15,11) (= Хэмминг) по разрядам байт + скремблер x^9 + x^5 + 1 (затравка cnt·devid mod 512) + CRC-8 0x2F", SL, "Хэмминг",
  {"формат": "ft(13) ∥ ecc(32) ∥ данные 8 байт ∥ MAC 2 байта ∥ CRC-8 (x^8+x^5+x^3+x^2+x+1 = 0x2F, init 0)", "БЧХ": "(15,11), dmin 3; n-й бит каждого байта кадра — слово n-го кода (8 кодов → 32 бита ecc)",
   "порождающая G (librenard)": [f"0x{g:04x}" for g in G], "проверочная H": [f"0x{h:04x}" for h in H],
   "скремблер": "9-битный РСЛОС x^9 + x^5 + 1, 8 тактов на байт, затравка (cnt·devid) & 0x1FF (0 → 0x1FF)", "модуляция": "GFSK 600 бит/с", "оговорка": "единственный открытый источник значений — librenard (лицензия не указана); статья IACR подтверждает только формат полей"},
  "Sigfox (ответ базовой станции)", [P.ist(s_df, "2.1.2"), ist_kod(bch, r"bch_15_11_generatormatrix", "librenard БЧХ"), ist_kod(dl, r"Polynomial: x\^9 \+ x\^5 \+ 1", "librenard скремблер"), ist_kod(crc, r"CRC8_POLYNOMIAL 0x2f", "librenard CRC-8")],
  "G·Hᵀ = 0, ранги 11 и 4, dmin = 3, столбцы H — все 15 ненулевых 4-битных синдромов (код Хэмминга (15,11)); скремблер и CRC-8 — по librenard (единственный открытый источник)",
  "частично: kod.блочный (Хэмминг вслепую), skrembler.py (x^9+x^5+1), crc_katalog (CRC-8/0x2F = CRC-8/AUTOSAR без init/xor)"))
