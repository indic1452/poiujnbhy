"""IEEE 802.15.4-2006 (Zigbee, Thread, 6LoWPAN, WirelessHART, ISA100): DSSS O-QPSK 2,4 ГГц (16×32 чипа), BPSK 868/915 (15 чипов),
FCS CRC-16; Z-Wave (ITU-T G.9959): контрольная сумма XOR, CRC-16 (1D0F), Манчестер/NRZ, SOF.
Проверка — внутренние свойства таблиц стандарта и открытый код (gr-ieee802-15-4, waving-z)."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/ieee802154/802.15.4-2006.pdf"); Zw = Tekst("istochniki/itu/T-REC-G.9959-201501-I.pdf"); ZAPISI = []
kus, s24 = T.kusok(r"Table 24—Symbol-to-chip mapping", r"Figure 19—O-QPSK chip offsets", posl=True)
ch = {}
for m in re.finditer(r"\n(\d{1,2})\s*\n([01] [01] [01] [01])\s*\n((?:[01] ){31}[01])", kus):
    ch[int(m.group(1))] = m.group(3).replace(" ", "")
assert sorted(ch) == list(range(16)), sorted(ch)
c0 = ch[0]
# символы 1…7 — циклические сдвиги символа 0 вправо на 4k чипов; 8…15 — символы 0…7 с инверсией нечётных чипов
for k in range(1, 8):
    assert ch[k] == c0[-4 * k:] + c0[:-4 * k], k
for k in range(8):
    assert ch[k + 8] == "".join(c if i % 2 == 0 else str(1 - int(c)) for i, c in enumerate(ch[k])), k
dmin = min(sum(a != b for a, b in zip(ch[i], ch[j])) for i in range(16) for j in range(i + 1, 16))
s_sfd, _, _ = T.odna(r"^6\.3\.2 SFD field")
ZAPISI.append(Z("IEEE 802.15.4 O-QPSK 2450 МГц (Zigbee/Thread): 16 квазиортогональных последовательностей по 32 чипа", "IEEE 802.15.4 (Zigbee, Thread, 6LoWPAN)", "блочный код (DSSS, 4 бита → 32 чипа)",
  {"символ 0 (c0…c31)": c0, "структура": "символы 1–7 — циклический сдвиг символа 0 на 4k чипов; 8–15 — символы 0–7 с инверсией нечётных чипов", "минимальное расстояние": dmin,
   "модуляция": "O-QPSK с полусинусным формированием (эквивалент MSK), 2 Мчип/с, 250 кбит/с", "кадр": "преамбула 4 октета 0x00, SFD 0xA7, PHR (длина 7 бит), PSDU до 127 октетов"},
  "Zigbee, Thread, Matter (через Thread), 6LoWPAN, WirelessHART, ISA100.11a, RF4CE", [T.ist(s24, "Table 24"), T.ist(s_sfd, "6.3.2 SFD")],
  f"16 последовательностей разобраны из табл. 24; проверены сдвиговая и сопряжённая структура; минимальное расстояние Хэмминга {dmin}", "нет (слепое DSSS — вне битового уровня)"))

kus27, s27 = T.kusok(r"Table 27—Symbol-to-chip mapping", r"Table 28", posl=True)
b = re.findall(r"\n([01])\s*\n((?:[01] ){14}[01])", kus27)
m0 = b[0][1].replace(" ", ""); m1 = b[1][1].replace(" ", "")
assert m1 == "".join("1" if c == "0" else "0" for c in m0)
per = [int(c) for c in m0 * 3]
rek = any(all(per[n] == per[n - 4] ^ per[n - a] for n in range(4, 45)) for a in (1, 3))
assert rek
ZAPISI.append(Z("IEEE 802.15.4 BPSK 868/915 МГц: m-последовательность 15 чипов", "IEEE 802.15.4 (Zigbee, Thread, 6LoWPAN)", "расширение спектра (m-последовательность)",
  {"0": m0, "1": m1, "модуляция": "BPSK, 300/600 кчип/с, дифференциальное кодирование данных"}, "802.15.4 868/915 МГц (Zigbee Sub-GHz ранних версий)",
  [T.ist(s27, "Table 27")], "последовательность из табл. 27 удовлетворяет рекурренту m-последовательности степени 4 (период 15), бит 1 — инверсия", "нет"))

s_f, _, _ = T.odna(r"The FCS field is 2 octets in length and contains a 16-bit ITU-T CRC")
ZAPISI.append(Z("IEEE 802.15.4 FCS: CRC-16 ITU-T x^16+x^12+x^5+1, начальное 0, младший бит первым", "IEEE 802.15.4 (Zigbee, Thread, 6LoWPAN)", "CRC-подобный",
  {"многочлен": "G16(x) = x^16 + x^12 + x^5 + 1", "начальное": "0", "порядок": "b0 (младший бит первого октета) — старшая степень M(x); остаток дописывается r0 первым", "каталожное имя": "CRC-16/KERMIT"},
  "802.15.4 (все PHY до 2006), Zigbee, Thread", [T.ist(s_f, "7.2.1.9")], "параметры из текста (формула разобрана по элементам)", "есть: crc_katalog (CRC-16/KERMIT)"))

s_sof = Zw.odna(r"^Start of frame \(SOF\) field")[0]
s_man = Zw.odna(r"Manchester code shall be used for data symbol encoding at data rate R1 and non-return-to-zero \(NRZ\)")[0]
s_cs, _, _ = Zw.odna(r"The FCS shall be calculated as an odd \(XOR'ed\) checksum as shown in the following algorithm")
s_crc, _, _ = Zw.odna(r"The CRC-16 generator shall be initialized to 1D0Fh before applying the first byte of a frame")
kus_cs, _ = Zw.kusok(r"BYTE GenerateCheckSum", r"return CheckSum")
assert "CheckSum = 0xFF" in kus_cs and "CheckSum ^= *Data++" in kus_cs
wz = kod("waving-z", "wavingz.h")
ZAPISI.append(Z("Z-Wave (ITU-T G.9959): контрольная сумма XOR (0xFF) для R1/R2, CRC-16 CCITT (начальное 0x1D0F) для R3; SOF 0xF0", "Z-Wave (ITU-T G.9959)", "CRC-подобный + линейный код",
  {"R1 (9,6 кбит/с)": "Манчестер, FSK", "R2 (40 кбит/с)": "NRZ, FSK", "R3 (100 кбит/с)": "NRZ, GFSK", "контрольная сумма": "8 бит: 0xFF XOR все байты от HomeID до данных", "CRC-16": "x^16+x^12+x^5+1, начальное 0x1D0F, без инверсии (R3)",
   "SOF": "11110000 (0xF0)", "преамбула": "0x55… (R1/R2), длина по табл. 7-10"},
  "Z-Wave (умный дом), 868/908/916 МГц", [Zw.ist(s_sof, "7.1.3.3 SOF"), Zw.ist(s_man, "7.1.2 кодирование"), Zw.ist(s_cs, "8.1.3.8 контрольная сумма"), Zw.ist(s_crc, "8.1.3.9 CRC-16"), ist_kod(wz, r"checksum", "waving-z")],
  "алгоритм контрольной суммы и начальное значение CRC приведены в тексте (листинг); реализация checksum есть в waving-z", "частично: crc_katalog (CRC-16/SPI-FUJITSU = CCITT с 0x1D0F), контрольная сумма XOR — нет"))
