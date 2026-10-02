"""Независимая сверка записей xDSL / G.fast (G.992.x, G.993.x, G.9701, G.998.4, G.991.x)."""
import json
from pv import *
I = "istochniki/itu/"
G9921 = I + "T-REC-G.992.1-199907-I.pdf"

z = zapis("DMT-DSL РС-код")
str_ok(z, G9921, 52, "7.6.1: C(D) = M(D)·D^R mod G(D), G(D) = ∏_{i=0}^{R−1}(D + α^i), поле, отображение байта", "C(D) = M(D) DR modulo G(D)", "index of the product runs from", "i = 0 to R–1", "primitive binary polynomial x8 + x4 + x3 + x2 + 1", "d7α7 + d6α6")
for f, p in (("T-REC-G.993.2-201902-I.pdf", 72), ("T-REC-G.9701-201903-I.pdf", 77)):
    str_ok(z, I + f, p, "то же поле и отображение байта", "primitive binary polynomial", "is identified with")

z = zapis("Решётчатый код Вэя")
str_ok(z, G9921, 57, "7.8.2: начальное состояние 0 и правило завершения u1 = S1 ⊕ S3, u2 = S2", "initialized to (0, 0, 0, 0)", "u1 = S1 ⊕ S3, and u2 = S2")
# свой автомат по рис. 7-14 (снимок proverka/snimki/g9921_p58.png): цепочка S2 → ⊕u2 → S1 → S3 → ⊕(S1, u1) → S0 → u0, обратная связь S0 → S2
def shag(S, u1, u2):
    S0, S1, S2, S3 = S
    return (S3 ^ S1 ^ u1, S2 ^ u2, S0, S1)
tab = json.load(open(os.path.join(KOREN, "tablicy/wei_16_4d_perehody.json")))["данные"]["состояние (S3S2S1S0 как число S0 + 2S1 + 4S2 + 8S3)"]
num = lambda S: S[0] + 2 * S[1] + 4 * S[2] + 8 * S[3]
vse = True
for n in range(16):
    S = (n & 1, n >> 1 & 1, n >> 2 & 1, n >> 3 & 1)
    sl = [num(shag(S, u & 1, u >> 1)) for u in range(4)]
    vse &= tab[str(n)]["u0"] == S[0] and tab[str(n)]["след. при (u2u1)=00,01,10,11"] == sl
ok(z["имя"], "свой автомат по рис. 7-14 (прочитан проверяющим заново) = tablicy/wei_16_4d_perehody.json (16 состояний × 4 входа)", vse)
zav = all(num(shag(shag(S, S[1] ^ S[3], S[2]), *(lambda T: (T[1] ^ T[3], T[2]))(shag(S, S[1] ^ S[3], S[2])))) == 0
          for S in [(n & 1, n >> 1 & 1, n >> 2 & 1, n >> 3 & 1) for n in range(16)])
ok(z["имя"], "правило завершения из текста приводит любое состояние в 0 за 2 символа (свой автомат)", zav)
str_ok(z, G9921, 58, "рис. 7-13: v1 = u1⊕u3, v0 = u3, w1 = u0⊕u1⊕u2⊕u3, w0 = u2⊕u3", "v1 = u1", "v0 = u3", "w0 = u2")

z = zapis("G.INP (G.998.4)")
G9984 = I + "T-REC-G.998.4-201811-I.pdf"
str_ok(z, G9984, 22, "8.4.2: C(D) = M(D)·D^11 mod G(D), G(D) = D^11+D^9+D^7+D^6+D^5+D+1", "D11 modulo G(D)", "G(D) = D11+ D9 + D7 + D6 + D5 + D + 1", "modified extended (24,12) Golay code")
g = st(11, 9, 7, 6, 5, 1, 0)
# минимальное расстояние циклического кода (23,12) с этим g: перебор всех 4096 слов
dmin = min(bin(pmul(m, g)).count("1") for m in range(1, 1 << 12))
ok(z["имя"], f"период g = 23, d_min циклического (23,12) = 7 (своё вычисление, полный перебор)", period(g) == 23 and dmin == 7, f"период {period(g)}, d_min {dmin}")

z = zapis("SHDSL (G.991.2)")
str_ok(z, I + "T-REC-G.991.2-200312-I.pdf", 17, "6.1.2.2: прямой несистематический кодер, X1(m) → Y1(m), Y0(m)", "feedforward non-systematic convolutional encoder", "X1(m) shall be applied to the")
str_ok(z, I + "T-REC-G.991.2-200312-I.pdf", 18, "коэффициенты a_i, b_i передаются приёмником при G.994.1", "The binary coefficients ai and bi shall be passed to the encoder from the receiver")
str_ok(z, I + "T-REC-G.991.2-200312-I.pdf", 37, "порядок передачи коэффициентов: a0 первым … b20 последним", "a0 is sent first in time", "b20 is sent last in time")
