"""Двумерные коды (для сверки РС-движка): QR, Data Matrix ECC200, Aztec, MaxiCode, PDF417 — по открытому коду zxing
(Apache-2.0, копии файлов с лицензией в istochniki/kod/zxing). Проверка: все тестовые векторы ReedSolomonTestCase zxing
(данные → проверочные символы) воспроизведены независимым кодером каталога (kody.rs_kodirovat); формат/версия QR —
таблица FORMAT_INFO_DECODE_LOOKUP пересчитана из BCH(15,5) и маски 0x5412."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "Двумерные штрихкоды (QR, Data Matrix, Aztec, MaxiCode, PDF417)"
R = "zxing"
f_gf = kod(R, "core/src/main/java/com/google/zxing/common/reedsolomon/GenericGF.java", "LICENSE")
f_enc = kod(R, "core/src/main/java/com/google/zxing/common/reedsolomon/ReedSolomonEncoder.java")
f_test = kod(R, "core/src/test/java/com/google/zxing/common/reedsolomon/ReedSolomonTestCase.java")
f_fmt = kod(R, "core/src/main/java/com/google/zxing/qrcode/decoder/FormatInformation.java")
f_mu = kod(R, "core/src/main/java/com/google/zxing/qrcode/encoder/MatrixUtil.java")
f_pdf = kod(R, "core/src/main/java/com/google/zxing/pdf417/decoder/ec/ModulusGF.java")
f_pdfc = kod(R, "core/src/main/java/com/google/zxing/pdf417/PDF417Common.java")
kod(R, "NOTICE", "-")

t = open(os.path.join(KOREN, f_gf)).read()
POLE = {m.group(1): (int(m.group(2), 2), int(m.group(3)), int(m.group(4))) for m in
        re.finditer(r"public static final GenericGF (\w+) = new GenericGF\(0b([01]+), (\d+), (\d+)\)", t)}
for m in re.finditer(r"public static final GenericGF (\w+) = (\w+);", t): POLE[m.group(1)] = POLE[m.group(2)]
for imya, (p, razm, fcr) in POLE.items():
    assert 1 << stepen(p) == razm and primitivnyj(p), imya

# тестовые векторы
tt = open(os.path.join(KOREN, f_test)).read()
chislo = lambda s: [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\d+", s)]
vektory = []
for m in re.finditer(r"testEncodeDecode\(GenericGF\.(\w+),\s*new int\[\]\s*\{([^}]*)\},\s*new int\[\]\s*\{([^}]*)\}\)", tt):
    imya = m.group(1); p, razm, fcr = POLE[imya]
    d, e = chislo(m.group(2)), chislo(m.group(3))
    assert rs_kodirovat(d, p, fcr, len(e)) == e, (imya, d[:4])
    vektory.append(imya)
assert len(vektory) >= 10
po_polyu = {k: vektory.count(k) for k in sorted(set(vektory))}

# формат QR: BCH(15,5) g = 0x537, маска 0x5412; версия BCH(18,6) g = 0x1F25
tf = open(os.path.join(KOREN, f_fmt)).read()
lookup = [(int(a, 16), int(b, 16)) for a, b in re.findall(r"\{0x([0-9A-F]+), 0x([0-9A-F]+)\}", tf)]
assert len(lookup) == 32
for kodovoe, dannye in lookup:
    assert ((dannye << 10) | poly_mod2(dannye << 10, 0x537)) ^ 0x5412 == kodovoe
assert poryadok_mnogochlena(0x537) == 15 and stepen(0x1F25) == 12

def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": SEM, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})

src_gf = lambda nm: ist_kod(f_gf, re.escape(nm) + r" = new GenericGF|" + re.escape(nm) + " = ")
SL = "частично: RS над GF(2^m) с любым fcr — rs_bch.py (слепо); для штрихкодов в проекте нет раскладки модулей"
rec("QR Code: RS над GF(256) x^8+x^4+x^3+x^2+1, корни α^0…; формат BCH(15,5) (g = 0x537, маска 0x5412), версия BCH(18,6) (g = 0x1F25)", "РС + БЧХ",
    {"поле": "0x11D, fcr = 0", "формат": "5 бит (уровень + маска) → 15 бит, XOR 0x5412; 32 кодовых слова — таблица FORMAT_INFO_DECODE_LOOKUP", "версия": "6 бит → 18 бит (версии ≥ 7)",
     "блоки": "по версии/уровню (Version.java)"}, "QR (ISO/IEC 18004)", [src_gf("QR_CODE_FIELD_256"), ist_kod(f_fmt, "FORMAT_INFO_MASK_QR = 0x5412"), ist_kod(f_mu, "VERSION_INFO_POLY = 0x1f25"), ist_kod(f_test, "public void testQRCode")],
    f"векторы zxing для QR ({po_polyu.get('QR_CODE_FIELD_256', 0)}) воспроизведены; все 32 слова формата пересчитаны из BCH(15,5) и маски", SL)
rec("Data Matrix ECC200: RS над GF(256) x^8+x^5+x^3+x^2+1 (0x12D), корни α^1…", "РС",
    {"поле": "0x12D, fcr = 1", "перемежение": "блоки по размеру символа (ISO/IEC 16022)"}, "Data Matrix", [src_gf("DATA_MATRIX_FIELD_256"), ist_kod(f_test, "public void testDataMatrix")],
    f"векторы zxing ({po_polyu.get('DATA_MATRIX_FIELD_256', 0)}) воспроизведены", SL)
rec("Aztec: RS над GF(16) x^4+x+1 (режим), GF(64) x^6+x+1, GF(256) 0x12D, GF(1024) x^10+x^3+1, GF(4096) x^12+x^6+x^5+x^3+1; корни α^1…", "РС (разные поля по размеру слова)",
    {"поля": "AZTEC_PARAM, AZTEC_DATA_6/8/10/12, fcr = 1"}, "Aztec (ISO/IEC 24778)",
    [src_gf("AZTEC_PARAM"), src_gf("AZTEC_DATA_12"), ist_kod(f_test, "public void testAztec")],
    "векторы zxing: " + ", ".join(f"{k} — {v}" for k, v in po_polyu.items() if k.startswith("AZTEC")), SL)
rec("MaxiCode: RS над GF(64) x^6+x+1, корни α^1…", "РС", {"поле": "= AZTEC_DATA_6 (0x43), fcr = 1"}, "MaxiCode (UPS)", [src_gf("MAXICODE_FIELD_64")],
    "поле то же, что Aztec 6 бит (векторы Aztec 6 воспроизведены)", SL)
rec("PDF417: РС над простым полем GF(929), генератор 3", "РС (простое поле)", {"поле": "Z/929, первообразный элемент 3", "проверочные": "2^(уровень+1) слов"}, "PDF417",
    [ist_kod(f_pdf, "PDF417_GF = new ModulusGF")], "3 — первообразный корень по модулю 929 (проверено)", "нет (в проекте только GF(2^m))")
assert len({pow(3, k, 929) for k in range(928)}) == 928

if __name__ == "__main__":
    print(len(ZAPISI), "записей", po_polyu)
