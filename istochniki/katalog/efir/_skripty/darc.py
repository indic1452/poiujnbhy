"""DARC (EN 300 751 V1.2.1): код произведения (272,190) — укороченный код разностных множеств, CRC-14, CRC-6/CRC-8
заголовков, скремблер x^9+x^4+1, BIC. Проверка — примерами (тестовыми векторами) из самого стандарта."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

D = Tekst("istochniki/darc/en_300751v010201p.pdf"); ZAPISI = []
def ostatok(bits, g, sdvig=True):
    """остаток bits(x)·x^deg mod g(x); bits — строка, старший первым"""
    n = g.bit_length() - 1
    v = int(bits, 2) << (n if sdvig else 0)
    while v.bit_length() - 1 >= n:
        v ^= g << (v.bit_length() - 1 - n)
    return v

s14, _, _ = D.odna(r"The INFORMATION BLOCK CRC is calculated using the generator polynomial: g\(x\) = x14 \+ x11 \+ x2 \+1")
kus, s_ex = D.kusok(r"polynomial\. See example below:", r"NOTE:")
hx = re.findall(r"\b[0-9A-F]{2,6}\b", kus.split("CRC")[1])
data = "".join(h for h in hx[:-2]); crc = "".join(hx[-2:])
assert len(data) == 44, (data, len(data))
g14 = mnogochlen([14, 11, 2, 0])
bits = bin(int(data, 16))[2:].zfill(176)
r14 = ostatok(bits, g14)
assert r14 == int(crc, 16) >> 2, (hex(r14), crc)
s82, _, l82 = D.odna(r"^g\(x\) = x82 \+ x77 \+ x76")
g82 = mnogochlen(stepeni(l82.split("=")[1]))
kus_p, s_p = D.kusok(r"An example using data and CRC from above can be seen below:", r"NOTE:")
par = "".join(re.findall(r"\b[0-9A-F]{1,2}\b", kus_p.split("PARITY")[1].split("DC 10")[1]))
r82 = ostatok(bits + bin(r14)[2:].zfill(14), g82)
assert r82 == int(par, 16), (hex(r82), par)
# тот же g(x), что у кода разностных множеств (273,191) ISDB-T TMCC
isdb = [82, 77, 76, 71, 67, 66, 56, 52, 48, 40, 36, 34, 24, 22, 18, 10, 4, 0]
assert stepeni(l82.split("=")[1]) == isdb
# второй источник: ITU-R BS.1194-2 прил. 1 (System A, DARC)
BS = Tekst("istochniki/itu/R-REC-BS.1194-2-199812-I.pdf")
s_bs82, _, l_bs82 = BS.odna(r"^g\(x\) = x82 \+ x77\+ x76")
s_bs14, _, l_bs14 = BS.odna(r"^g\(x\)\s+=\s+x14\s+\+\s+x11\s+\+\s+x2\s+\+\s+1")
assert stepeni(l_bs82.split("=")[1].replace("+", " + ")) == isdb and stepeni(re.sub(r"\s+", " ", l_bs14.split("=")[1])) == [14, 11, 2, 0]
ZAPISI.append(Z("DARC: блок (272,190) — укороченный код разностных множеств (273,191) + CRC-14; кадр — код произведения 272×272", "DARC (EN 300 751)", "каскадный: код произведения (разностных множеств × разностных множеств) + CRC",
  {"блок": "BIC 16 бит + 176 бит данных + CRC-14 (g = x^14+x^11+x^2+1) + 82 бита чётности", "g(x) (272,190)": "x^82+x^77+x^76+x^71+x^67+x^66+x^56+x^52+x^48+x^40+x^36+x^34+x^24+x^22+x^18+x^10+x^4+1 (тот же, что (273,191) ISDB-T TMCC)",
   "кадр A": "190 информационных блоков (60 BIC3 + 70 BIC2 + 60 BIC1) + 82 блока чётности (BIC4): вертикальный (272,190) по столбцам → код произведения", "кадр B": "то же с блоками чётности, распределёнными", "декодирование": "мажоритарное"},
  "DARC (радиоданные на FM-поднесущей 76 кГц, Япония/Европа: SWIFT, пейджинг, ДГНСС-поправки)", [D.ist(s14, "11.1 CRC-14"), D.ist(s_ex, "пример CRC"), D.ist(s82, "g(x) (272,190)"), D.ist(s_p, "пример чётности"), BS.ist(s_bs82, "ITU-R BS.1194 g(x)"), BS.ist(s_bs14, "ITU-R BS.1194 CRC-14")],
  f"пример стандарта: CRC-14 от 22 байт данных = {crc} (14 бит влево) — воспроизведён; 82 бита чётности от данных+CRC = {par} — воспроизведены; g(x) совпадает с (273,191) ARIB STD-B31; g(x) и CRC-14 совпадают с ITU-R BS.1194-2 прил. 1",
  "частично: kod.блочный/rs_bch (циклический код вслепую), crc.py"))

kusB, sB = D.kusok(r"Table 2: BIC coding \(16 bits\)", r"7\.3\.2\.6", posl=True)
bic = {m.group(1): "".join(m.group(2).split()) for m in re.finditer(r"(BIC\d)\s*\n((?:[01]{4}\s*\n){4})", kusB)}
assert len(bic) == 4 and all(len(v) == 16 for v in bic.values()), bic
s_sc, _, _ = D.odna(r"The starting sequence for the scrambler is 101010101\. The scrambler is restarted for each block")
ZAPISI.append(Z("DARC: синхрослова блоков BIC1…BIC4 и скремблер x^9 + x^4 + 1 (101010101)", "DARC (EN 300 751)", "синхрослово + скремблер",
  {"BIC": bic, "скремблер": "g(x) = x^9 + x^4 + 1, начальное 101010101, перезапуск на каждый блок, BIC не скремблируется", "модуляция": "LMSK 16 кбит/с на поднесущей 76 кГц"},
  "DARC", [D.ist(sB, "Table 2"), D.ist(s_sc, "7.3.2.6")], "значения разобраны из текста", "частично: sinhro.py (синхрослово), skrembler.py"))

s6, _, _ = D.odna(r"The short message L3 header is protected by a 6 bits CRC")
s8, _, _ = D.odna(r"The short message L4 header is protected by a 8 bits CRC")
g6 = mnogochlen([6, 4, 3, 0]); g8 = mnogochlen([8, 5, 4, 3, 0])
prim = [("1001" + "0" + "1" + "0011", g6, "000100"), ("0101" + "0" + "0" + "1100", g6, "011101"), ("0" + "0" + "000001" + "0" + "0000011", g8, "11010111")]
for b, gg, c in prim:
    assert ostatok(b, gg) == int(c, 2), (b, c, bin(ostatok(b, gg)))
# второй проход сверки: 4-й пример (L4 длинный, 11.2.4) и CRC-16 группы данных L5 (11.2.5)
s6d, _, _ = D.odna(r"The long message L4 header is protected by a 6 bits CRC")
s16, _, _ = D.odna(r"The data group CRC is a 16-bit word based on division of the data group header and data field")
pr4 = "00" + "00" + "11" + "0" + "001000000" + "0" + "0" + "10000000"
assert ostatok(pr4, g6) == 0b101101, bin(ostatok(pr4, g6))
D.odna(r"^\s*101101\s*$"); D.odna(r"^\s*4021414243\s*$"); D.odna(r"^\s*87F5\s*$")
def crc16_l5(dannye):
    r = 0xFFFF                                   # «первые 16 бит инвертируются» ≡ начальное FFFF
    for x in dannye:
        for i in range(8):
            f = ((r >> 15) & 1) ^ ((x >> (7 - i)) & 1); r = (r << 1) & 0xFFFF
            if f: r ^= 0x1021
    return r ^ 0xFFFF
assert crc16_l5(bytes.fromhex("4021414243")) == 0x87F5
ZAPISI.append(Z("DARC CRC заголовков: CRC-6 x^6+x^4+x^3+1 (L3, L4 длинный), CRC-8 x^8+x^5+x^4+x^3+1 (L4 короткий)", "DARC (EN 300 751)", "CRC-подобный",
  {"CRC-6": "x^6 + x^4 + x^3 + 1, начальное 0, остаток от деления x^6·m(x) (L3 короткий и длинный, L4 длинный)", "CRC-8": "x^8 + x^5 + x^4 + x^3 + 1 (L4 короткий)",
   "CRC-16 L5 (группа данных)": "g(x) = x^16 + x^12 + x^5 + 1, первые 16 бит инвертируются (≡ начальное FFFF), остаток инвертируется; старший бит первым (= CRC-16/GENIBUS RevEng)"},
  "DARC уровни 3/4", [D.ist(s6, "11.2.1"), D.ist(s8, "11.2.3"), D.ist(s6d, "11.2.4 L4 длинный"), D.ist(s16, "11.2.5 CRC-16 L5")],
  "все 5 примеров стандарта воспроизведены: L3 короткий SC=12 → 000100, L3 длинный SC=3 → 011101, L4 короткий → 11010111, L4 длинный → 101101 (делением), L5: 40 21 41 42 43 → 87F5 (CRC-16 с инверсиями)", "частично: crc.py (CRC вслепую)"))
