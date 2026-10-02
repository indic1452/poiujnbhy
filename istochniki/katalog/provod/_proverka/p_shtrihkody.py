"""Независимая сверка записей штрихкодов по открытому коду zxing (Apache-2.0): свой кодер РС и БЧХ."""
import re
from pv import *
Z = os.path.join(KOREN, "istochniki/kod/zxing/core/src/")
gfj = open(Z + "main/java/com/google/zxing/common/reedsolomon/GenericGF.java").read()
polya = {m.group(1): (int(m.group(2), 2), int(m.group(3)), int(m.group(4))) for m in re.finditer(r"GenericGF (\w+) = new GenericGF\(0b([01]+), (\d+), (\d+)\)", gfj)}
polya["AZTEC_DATA_8"] = polya["DATA_MATRIX_FIELD_256"]; polya["MAXICODE_FIELD_64"] = polya["AZTEC_DATA_6"]
tc = open(Z + "test/java/com/google/zxing/common/reedsolomon/ReedSolomonTestCase.java").read()
vektory = re.findall(r"testEncodeDecode\(GenericGF\.(\w+),\s*new int\[\]\s*\{([^}]*)\},\s*new int\[\]\s*\{([^}]*)\}\)", tc)
def chisla(s): return [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\d+", re.sub(r"//[^\n]*", "", s))]
itog = {}
for pole, d, e in vektory:
    prim, size, fcr = polya[pole]; m = size.bit_length() - 1
    D, Ec = chisla(d), chisla(e)
    g = rs_g(m, prim, range(fcr, fcr + len(Ec)))
    itog.setdefault(pole, []).append(rs_proverochnye(D, g, m, prim) == Ec)
imena = {"QR_CODE_FIELD_256": "QR Code", "DATA_MATRIX_FIELD_256": "Data Matrix ECC200", "AZTEC_PARAM": "Aztec", "AZTEC_DATA_6": "Aztec", "AZTEC_DATA_8": "Aztec", "AZTEC_DATA_10": "Aztec", "AZTEC_DATA_12": "Aztec"}
for pole, r in itog.items():
    z = zapis(imena[pole])
    prim, size, fcr = polya[pole]
    ok(z["имя"], f"векторы zxing {pole} ({poly_str(prim)}, fcr = {fcr}): свой кодер РС", all(r), f"{sum(r)} из {len(r)}")
z = zapis("QR Code")
fi = open(Z + "main/java/com/google/zxing/qrcode/decoder/FormatInformation.java").read()
look = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{(0x[0-9A-Fa-f]+), (0x[0-9A-Fa-f]+)\}", fi)]
moi = [(((v << 10) | pmod(v << 10, 0x537)) ^ 0x5412, v) for v in range(32)]
ok(z["имя"], "32 слова формата: BCH(15,5) g = x^10+x^8+x^5+x^4+x^2+x+1 (0x537), маска 0x5412 — свой расчёт = FORMAT_INFO_DECODE_LOOKUP", sorted(look) == sorted(moi) and len(look) == 32)
# минимальное расстояние кода формата и версии
d15 = min(bin(a ^ b).count("1") for (a, _), (b, _) in __import__("itertools").combinations(moi, 2))
ver = [(v << 12) | pmod(v << 12, 0x1F25) for v in range(64)]
d18 = min(bin(a ^ b).count("1") for a, b in __import__("itertools").combinations(ver, 2))
ok(z["имя"], f"d_min: формат (15,5) = {d15}, версия (18,6) = {d18} (свой расчёт; ожидается 7 и 8)", d15 == 7 and d18 == 8)
z = zapis("PDF417")
ok(z["имя"], "3 — первообразный корень по модулю 929 (свой расчёт)", len({pow(3, i, 929) for i in range(928)}) == 928)
mg = open(Z + "main/java/com/google/zxing/pdf417/decoder/ec/ModulusGF.java").read()
ok(z["имя"], "ModulusGF.java: PDF417_GF = new ModulusGF(PDF417Common.NUMBER_OF_CODEWORDS, 3); NUMBER_OF_CODEWORDS = 929",
   bool(re.search(r"new ModulusGF\(PDF417Common\.NUMBER_OF_CODEWORDS, 3\)", mg)) and "NUMBER_OF_CODEWORDS = 929" in open(Z + "main/java/com/google/zxing/pdf417/PDF417Common.java").read())
