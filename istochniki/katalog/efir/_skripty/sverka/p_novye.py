"""Сверка новых записей второго прохода независимым кодом (не импортирует скрипты сборки)."""
from obsh import *
import re, json
R = []
def rez(imya, chto, ist, metod, rezultat="совпало"):
    R.append({"запись": zapis(imya)["имя"], "что сверено": chto, "источник": ist, "метод": metod, "результат": rezultat})
OB = {z["имя"]: z["параметры"] for z in json.load(open(os.path.join(os.path.dirname(KOREN), "obshchie", "katalog.json"))) if z["семейство"].startswith("CRC каталог RevEng")}
# NICAM: любая схема РСЛОС x^9+x^4+1 из единиц, дающая начало из текста
NT = "nicam/en_300163v010201p.pdf.txt"
est(NT, [8], "x9 + x4 + 1", "0000 0111 1011 1110 0010")
sh = lfsr_lyuboy([9, 4, 0], [1] * 9, bity("00000111101111100010"))
assert sh
rez("NICAM-728", "x^9+x^4+1, загрузка 1…1, начало ПСП", "EN 300 163 4.1.3, стр. 8", f"перебор схем РСЛОС: начало ПСП из текста даёт схема {sh[0]} — многочлен и загрузка записи согласованы")
# LR-FHSS: отбеливание = отбеливание LoRa (gr-lora_sdr tables.h)
lf = open(os.path.join(KOREN, "istochniki/kod/sx126x_driver/src/lr_fhss_mac.c")).read()
assert "( ( lfsr & 0x80 ) >> 7 ) ^" in lf and "( ( ( lfsr & 0x20 ) >> 5 ) ^ ( ( ( lfsr & 0x10 ) >> 4 ) ^ ( ( lfsr & 0x8 ) >> 3 ) ) )" in lf
lfsr = 0xFF; seq = []
for _ in range(255):
    seq.append(lfsr); lfsr = ((lfsr << 1) | (((lfsr >> 7) ^ (lfsr >> 5) ^ (lfsr >> 4) ^ (lfsr >> 3)) & 1)) & 0xFF
tab = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{2})", open(os.path.join(KOREN, "istochniki/kod/gr-lora_sdr/lib/tables.h")).read())][:255]
assert seq == tab
rez("LoRa LR-FHSS", "ГПСП отбеливания", "sx126x_driver lr_fhss_mac.c + gr-lora_sdr tables.h", "255 значений регистра отбеливания LR-FHSS (своя модель кода Semtech) равны таблице отбеливания LoRa gr-lora_sdr — тот же генератор (второй независимый источник)")
# Lfour CRC-24 = CRC-24/LTE-A
z = zapis("ETSI LTN семейство Lfour")
assert "0x864CFB" in z["параметры"]["CRC-24"] and OB["CRC-24/LTE-A"]["poly"] == "0x864CFB"
rez("ETSI LTN семейство Lfour", "многочлен CRC-24", "TS 103 357 5.2.3.3 + RevEng CRC-24/LTE-A", "многочлен записи = poly RevEng CRC-24/LTE-A (0x864CFB); начальное у Lfour другое (FFFFFF) — отмечено в записи")
# Gen2: своя побитная CRC-16 по примерам табл. F-2
G = "rfid/gs1_gen2_uhf_3.0.0.pdf.txt"
for cr, pc, n in ((0xE2F0, 0x0000, 0), (0xCCAE, 0x0800, 1), (0x968F, 0x1000, 2), (0x78F6, 0x1800, 3), (0xC241, 0x2000, 4), (0x2A91, 0x2800, 5), (0x1835, 0x3000, 6)):
    d = pc.to_bytes(2, "big") + b"".join(bytes([0x11 * (i + 1)] * 2) for i in range(n))
    assert crc(bajty_v_bity(d), 0x1021, 16, 0xFFFF, 0xFFFF) == cr
    assert poisk(G, f"{cr:04X}h")
rez("EPC Gen2 UHF RFID", "7 примеров StoredCRC (табл. F-2)", "GS1 Gen2 3.0 прил. F.3", "своя побитная CRC-16 (0x1021, FFFF, инверсия) по StoredPC + словам 1111h…6666h воспроизвела все 7 значений табл. F-2")
# AMSS: смещения и g(x) в тексте; синдромы смещений различны
z = zapis("AMSS")
est("amss/ts_102386v010201p.pdf.txt", [11], "multiply the 36 bit payload, m(x), by x11")
o1 = int(z["параметры"]["смещения d10…d0"]["блок 1"], 2); o2 = int(z["параметры"]["смещения d10…d0"]["блок 2"], 2)
g = stepeni_v_chislo("x^11+x^8+x^6+1")
assert o1 != o2 and poly_mod2_mod(o1 ^ o2, g) != 0
rez("AMSS", "смещения блоков 1/2 и g(x)", "TS 102 386 6.3, табл. 3", "смещения различаются и их разность не делится на g(x) — блоки 1 и 2 различимы по синдрому (как в RDS)")
# MPEG-2 CRC-32: check RevEng своей реализацией
assert crc(bajty_v_bity(b"123456789"), 0x04C11DB7, 32, 0xFFFFFFFF) == int(OB["CRC-32/MPEG-2"]["check"], 16)
rez("CRC-32 MPEG-2/DVB", "многочлен, начальное, без инверсии", "TS 101 191 прил. A + RevEng", "своя побитная реализация по параметрам записи дала check RevEng 0x0376E6E7")
