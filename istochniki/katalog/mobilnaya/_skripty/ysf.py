"""Yaesu System Fusion / C4FM (Yaesu Amateur Radio Digital Specifications 1.02): FICH — CRC16 + Голей (24,12,8) + свёрточный K=5 +
перемежение 5×20; DCH — CRC16 + K=5 + перемежение 9×20 (5×20) + обеление PN(9,5); VCH/VeCH (V/D тип 2) — тройное повторение + обеление + 26×4;
синхрослово D471C9634D. Сверка: MMDVMHost (YSFFICH, YSFPayload, YSFConvolution, Golay24128), проект ysf.py."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *
import pymupdf

T = Tekst("istochniki/ysf/Yaesu_Amateur_Radio_Digital_Specs_1V02.pdf")
ZAPISI = []
fich = kod("MMDVMHost", "YSFFICH.cpp"); pay = kod("MMDVMHost", "YSFPayload.cpp"); conv = kod("MMDVMHost", "YSFConvolution.cpp")
gol = kod("MMDVMHost", "Golay24128.cpp"); ydef = kod("MMDVMHost", "YSFDefines.h")
tx = lambda p: open(os.path.join(KOREN, p)).read()
PDF = pymupdf.open(os.path.join(KOREN, T.pdf))

def slova_01(stranica, y_ot=0, y_do=1e9, shag=2):
    rows = {}
    for w in PDF[stranica - 1].get_text("words"):
        if w[4] in ("0", "1") and y_ot <= w[1] <= y_do: rows.setdefault(round(w[1] / shag), []).append((w[0], int(w[4])))
    return [[b for _, b in sorted(r)] for _, r in sorted(rows.items())]

# Голей (24,12,8), приложение A — матрица по координатам (в текстовом слое склеена)
s_gol = T.odna(r"The txd coded data is obtained by applying the Golay \(24, 12, 8\) generation matrix")[0]
G = [r for r in slova_01(s_gol) if len(r) == 24]
assert len(G) == 12 and all(G[i][:12] == [1 if j == i else 0 for j in range(12)] for i in range(12)), G
Gi = [int("".join(map(str, r)), 2) for r in G]
vesa = [bin(__import__("functools").reduce(lambda a, b: a ^ b, [Gi[i] for i in range(12) if (u >> i) & 1], 0)).count("1") for u in range(1, 4096)]
assert min(vesa) == 8
e24 = c_massiv(gol, "ENCODING_TABLE_24128")
for i in range(12):
    assert e24[1 << (11 - i)] == Gi[i], i                                    # строка = слово MMDVMHost для единичного входа
# свёрточный K=5: формула — картинка, в текстовом слое осколки «1 1 2 4 2 3 4 1 … x x x G x x G» = G2 = 1+x+x²+x⁴, G1 = 1+x³+x⁴
s_cc = T.odna(r"Constraint length K = 5")[0]
kus, _ = T.kusok(r"Constraint length K = 5\s*\nGenerator polynomial:", r"\(e\) Interleave")
oskolki = sorted(re.findall(r"[0-9xG]", kus))
assert oskolki == sorted(list("11242341") + list("xxxGxxG")), oskolki
assert "g1 = (d + d3 + d4) & 1" in tx(conv) and "g2 = (d + d1 + d2 + d4) & 1" in tx(conv)
# CRC16: начальное 0, инверсия в конце
s_crc = T.odna(r"The initial values of all the shift registers S15- S00 shall be zero and all bits must be inverted at the end|initial\s*$")[0]
T.odna(r"all bits must be inverted at the end")
# перемежение
s_il_f = T.odna(r"with block length M = 5 dibits and depth N = 10 must be carried out")[0]
s_il_d = T.odna(r"with block length M = 9 dibits and depth N = 20 must be carried out")[0]
s_il_v = T.odna(r"block length M = 26 bit and depth N = 4 is carried out")[0]
def dibit_il(M, N): return [2 * ((i % M) * N + i // M) for i in range(M * N)]      # запись строками по M дибит, чтение столбцами по N
IF = c_massiv(fich, "INTERLEAVE_TABLE"); assert IF == dibit_il(5, 20)              # FICH: 100 дибит → 5×20 (в тексте «N = 10» — опечатка, рис. 4-26: 20 строк)
assert c_massiv(pay, "INTERLEAVE_TABLE_9_20") == dibit_il(9, 20) and c_massiv(pay, "INTERLEAVE_TABLE_5_20") == dibit_il(5, 20)
assert c_massiv(pay, "INTERLEAVE_TABLE_26_4") == [(i % 26) * 4 + i // 26 for i in range(104)]
# обеление PN(9,5): S8…S0 = 1 1 1 0 0 1 0 0 1, выход S0, обратная связь S0⊕S4 → S8
s_wh = T.odna(r"\(9,5\) shown in Figure 4-28 as output")[0]
kus, _ = T.kusok(r"Register\|?\s*\n\s*Initial value", r"Figure 4-28")
nach = [int(x) for x in re.findall(r"\b[01]\b", kus)][:9]
assert nach == [1, 1, 1, 0, 0, 1, 0, 0, 1], nach
def pn(n):
    s = nach[::-1]; out = []                      # s[k] = Sk
    for _ in range(n): out.append(s[0]); fb = s[0] ^ s[4]; s = s[1:] + [fb]
    return out
wd = c_massiv(pay, "WHITENING_DATA"); assert [(b >> (7 - j)) & 1 for b in wd for j in range(8)] == pn(160)
# синхрослово
s_fs = T.odna(r"Synchronized signal \(40 bit\)\s+D471C9634D")[0]
assert re.search(r"YSF_SYNC_BYTES\[\]\s*=\s*\{0xD4U, 0x71U, 0xC9U, 0x63U, 0x4DU\}", tx(ydef))
proj = open("/home/user/poiujnbhy/src/reportgen/potok/ysf.py").read()
assert re.search(r"0xD471C9634D", proj, re.I)
tablica("ysf_c4fm", {"golay_24_12_G": G, "PN95_160": pn(160), "FICH_perem_bit": dibit_il(5, 20), "DCH_perem_bit": dibit_il(9, 20)},
        "YSF: матрица Голея (прил. A), обеление PN(9,5) 160 бит, перемежение FICH и DCH (номер бита в эфире для i-го кодового дибита)",
        [T.ist(s_gol, "Appendix A"), T.ist(s_wh, "Figure 4-28"), T.ist(s_il_f, "FICH interleave"), T.ist(s_il_d, "DCH interleave"), ist_kod(gol, "ENCODING_TABLE_24128"), ist_kod(pay, "WHITENING_DATA")],
        "Голей: 12 строк по координатам слов PDF, систематический, d_min = 8, строки = ENCODING_TABLE_24128 MMDVMHost; PN(9,5) из начального 111001001 = WHITENING_DATA MMDVMHost; "
        "перемежение = INTERLEAVE_TABLE (FICH) и INTERLEAVE_TABLE_9_20/5_20/26_4 MMDVMHost")

def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "Yaesu System Fusion (C4FM)", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
EST = "ЕСТЬ частично в проекте: ysf.py (синхрослово, FICH: Голей/свёртка/перемежение/CRC); DCH/VCH — проверить"
rec("YSF FICH: CRC16 + Голей (24,12,8) ×4 + свёрточный K=5 1/2 + перемежение 5×20", "каскадный: CRC + Голей + свёрточный",
    {"данные": "32 бита + CRC16 (x16+x12+x5+1, начальное 0, инверсия) = 48 → 4 × Голей (24,12,8) = 96 + 4 нулевых = 100 → K=5 1/2 (G1 = 1+x³+x⁴, G2 = 1+x+x²+x⁴) = 200",
     "перемежение": "100 дибит: запись строками по 5 дибит, 20 строк, чтение столбцами (в тексте «N = 10» — опечатка, рисунок и MMDVMHost: 20)",
     "Голей": "матрица прил. A = tablicy/ysf_c4fm.json"},
    "YSF все кадры (FICH после синхрослова)", [T.ist(s_crc, "4.5(1) CRC"), T.ist(s_gol, "Appendix A"), T.ist(s_cc, "K=5"), T.ist(s_il_f, "interleave FICH")],
    "Голей = MMDVMHost ENCODING_TABLE_24128; свёрточный = YSFConvolution (g1 = d+d3+d4, g2 = d+d1+d2+d4), осколки формулы в тексте совпадают; перемежение = YSFFICH INTERLEAVE_TABLE", EST)
rec("YSF DCH: CRC16 + свёрточный K=5 1/2 + перемежение 9×20 / 5×20 + обеление PN(9,5)", "каскадный: CRC + свёрточный",
    {"HC/TC, V/D тип 1, Data FR": "160 + CRC16 + 4 = 180 → 360 бит, перемежение 9 дибит × 20", "V/D тип 2": "80 + 16 + 4 = 100 → 200, перемежение 5 × 20",
     "обеление": "PN(9,5): x9+x5+1, S8…S0 = 111001001, сброс на каждый блок, XOR с начала блока", "CRC": "x16+x12+x5+1, начальное 0, инверсия"},
    "YSF заголовок/терминатор, данные, V/D", [T.ist(s_il_d, "DCH"), T.ist(s_wh, "whitening")], "обеление = WHITENING_DATA, перемежение = INTERLEAVE_TABLE_9_20/5_20 MMDVMHost", EST)
rec("YSF VCH/VeCH (V/D тип 2): мажоритарное тройное повторение 27 бит + обеление + перемежение 26×4", "повторение (3,1) + перемежитель",
    {"схема": "49 бит AMBE → 27 бит × 3 (81) + 22 = 103 + 1 ноль = 104, обеление PN(9,5), перемежение M = 26 бит × N = 4 → VCH 72 + VeCH 32"},
    "YSF речь V/D тип 2 (AMBE+2 3600)", [T.ist(s_il_v, "4.5(3)")], "перемежение = INTERLEAVE_TABLE_26_4 MMDVMHost", EST)
rec("YSF синхрослово D471C9634D (40 бит)", "синхрослово", {"FS": "0xD471C9634D, 20 символов 4FSK", "кадр": "960 бит (100 мс при 9600 бит/с): FS 40 + FICH 200 + полезная 720"},
    "поиск YSF", [T.ist(s_fs, "FS"), T.ist(T.odna(r"The various frames are all defined by 960 bit")[0], "кадр 960 бит")], "= YSF_SYNC_BYTES MMDVMHost = проект ysf.py", "ЕСТЬ в проекте: ysf.py")

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
