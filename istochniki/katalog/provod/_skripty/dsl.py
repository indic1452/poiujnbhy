"""xDSL и G.fast/MGfast: ITU-T G.991.1 (HDSL), G.991.2 (SHDSL), G.992.1/.2/.3/.5 (ADSL/ADSL2/2+), G.993.1/.2 (VDSL/VDSL2),
G.998.4 (G.INP), G.9701 (G.fast), G.9711 (MGfast). Проверки: автомат Вэя сверен с правилом завершения решётки из текста,
многочлен Голея (24,12) — период 23, LDPC MGfast — таблицы 10-16/10-17 собраны программно и дают полный ранг для всех Mc."""
import os, re, sys, collections
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *
import pymupdf

ZAPISI = []
SEM = "xDSL / G.fast (ITU-T G.99x)"


def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": SEM, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


A1 = Tekst("istochniki/itu/T-REC-G.992.1-199907-I.pdf")
A2 = Tekst("istochniki/itu/T-REC-G.992.2-199907-I.pdf")
A3 = Tekst("istochniki/itu/T-REC-G.992.3-200904-I/G992-3_minusAnnexC_2009-04-final.pdf")
A5 = Tekst("istochniki/itu/T-REC-G.992.5-200901-I/G992-5_minusAnnexC_2009-01-final.pdf")
V1 = Tekst("istochniki/itu/T-REC-G.993.1-200406-I.pdf")
V2 = Tekst("istochniki/itu/T-REC-G.993.2-201902-I.pdf")
GF = Tekst("istochniki/itu/T-REC-G.9701-201903-I.pdf")
MG = Tekst("istochniki/itu/T-REC-G.9711-202104-I.pdf")
H1 = Tekst("istochniki/itu/T-REC-G.991.1-199810-I.pdf")
H2 = Tekst("istochniki/itu/T-REC-G.991.2-200312-I.pdf")
IN = Tekst("istochniki/itu/T-REC-G.998.4-201811-I.pdf")

# ---- RS DMT-семейства ----
sRS = A1.odna(r"^7\.6\.1\s+Reed-Solomon coding")[0] if A1.naiti(r"^7\.6\.1\s+Reed-Solomon coding") else A1.gde("7.6.1 Reed-Solomon coding")
sRSp = A1.gde("primitive binary polynomial x8 + x4 + x3 + x2 + 1")
sInt = A1.gde("byte Di (with index i) is delayed by (D–1) × i")
sV1 = V1.gde("The following codeword parameters specified as (N,K) shall be supported: (144,128)")
sV2 = V2.naiti(r"primitive binary polynomial")[0][0]
sGF = GF.gde("is the generator polynomial of the Reed-Solomon code, where the index of the product runs from i= 0 to RFEC-1")
sMG = MG.gde("A standard byte-oriented Reed-Solomon code shall be used for FEC")
assert primitivnyj(0x11D)
rec("DMT-DSL РС-код (ADSL/ADSL2/2+/VDSL/VDSL2/G.fast/MGfast): GF(256), G(D) = ∏_{i=0}^{R−1}(D + α^i), R = 0…16", "РС (переменные N,K) + свёрточное перемежение",
    {"поле": "x^8 + x^4 + x^3 + x^2 + 1, байт (d7…d0) ↔ d7α^7 + … + d0", "код": "C(D) = M(D)·D^R mod G(D), N = K + R ≤ 255, R чётное 0…16 (G.fast: RFEC 2…16)",
     "перемежение": "свёрточное: байт i слова задерживается на (D−1)·i; D — степень 2 (ADSL); VDSL: до 64 слов при N = 255",
     "ADSL": "быстрый и перемеженный буферы (KF/RF и KI/RI)", "VDSL1": "(144,128) и (240,224) обязательны",
     "G.fast/MGfast": "RS-слова внутри DTU (NFEC = KFEC + RFEC); RMC защищён отдельным RS"},
    "ADSL, ADSL-lite, ADSL2, ADSL2+, VDSL, VDSL2, G.fast, MGfast, 10PASS-TS",
    [A1.ist(sRS, "G.992.1 7.6.1"), A1.ist(sRSp), A1.ist(sInt, "7.6.3 перемежение"), A2.ist(A2.gde("Reed-Solomon coding"), "G.992.2"),
     V1.ist(sV1, "G.993.1 8.3"), V2.ist(sV2, "G.993.2"), GF.ist(sGF, "G.9701"), MG.ist(sMG, "G.9711")],
    "формула и многочлен поля одинаковы во всех 5 документах (сверено текстами); корни α^0…α^(R−1) — как G.975", "частично: RS над GF(256) и свёрточное перемежение DVB-типа есть (rs_bch.py, peremezhenie.py); DMT-кадрирования нет")

# ---- Wei 16-state 4D ----
sW = A1.gde("Block processing of Wei's 16-state 4-dimensional trellis code is optional")
sWz = A1.gde("the 2 LSBs u1 and u2 of the final two 4-dimensional symbols in the DMT symbol are constrained to u1 = S1 ⊕ S3, and u2 = S2")
sWv = A1.gde("v1 = u1 ⊕ u3")
def wei(S, u1, u2):
    S0, S1, S2, S3 = S
    return S0, (S1 ^ S3 ^ u1, S2 ^ u2, S0, S1)        # выход u0 = S0; новые (S0, S1, S2, S3) по рис. 7-14
for s in range(16):
    S = tuple((s >> i) & 1 for i in range(4))
    for _ in range(2):
        _, S = wei(S, S[1] ^ S[3], S[2])
    assert S == (0, 0, 0, 0)                                  # правило завершения из текста приводит любое состояние в 0 за 2 символа
perehody = {s: [] for s in range(16)}
for s in range(16):
    S = tuple((s >> i) & 1 for i in range(4))
    for u in range(4):
        u0, N = wei(S, u & 1, u >> 1); perehody[s].append(sum(b << i for i, b in enumerate(N)))
assert all(len(set(v)) == 4 for v in perehody.values()) and collections.Counter(x for v in perehody.values() for x in v) == {i: 4 for i in range(16)}
tablica("wei_16_4d_perehody", {"состояние (S3S2S1S0 как число S0 + 2S1 + 4S2 + 8S3)": {str(s): {"u0": s & 1, "след. при (u2u1)=00,01,10,11": perehody[s]} for s in range(16)}},
        "Автомат 16-позиционного 4D кода Вэя (G.992.1 рис. 7-14): u0 = S0; S0' = S1⊕S3⊕u1; S1' = S2⊕u2; S2' = S0; S3' = S1; преобразование u→v,w рис. 7-13",
        [A1.ist(sW, "7.8"), A1.ist(sWz, "правило завершения")],
        "автомат прочитан по рисунку (снимок risunki/g9921_wei_koder.png) и проверен: правило завершения текста (u1 = S1⊕S3, u2 = S2) приводит любое из 16 состояний в нулевое за 2 символа; "
        "у каждого состояния 4 разных преемника и 4 предшественника")
rec("Решётчатый код Вэя: 16 состояний, 4D (ADSL/ADSL2/2+/VDSL2/G.fast/MGfast)", "свёрточный (ТКМ 4D, систематический рекурсивный)",
    {"кодер": "u1, u2 проходят без изменений; u0 = S0; S0' = S1⊕S3⊕u1, S1' = S2⊕u2, S2' = S0, S3' = S1 → tablicy/wei_16_4d_perehody.json",
     "отображение": "v1 = u1⊕u3, v0 = u3, w1 = u0⊕u1⊕u2⊕u3, w0 = u2⊕u3; 8 4D-смежных классов C4_i (u2u1u0), 2D-классы C2_0…C2_3",
     "начало/конец": "начальное состояние 0 в начале DMT-символа; два последних 4D-символа приводят в 0 (u1 = S1⊕S3, u2 = S2)",
     "избыточность": "1 бит на 4D-символ (пару тонов)"},
    "ADSL (опц.), ADSL2/2+, VDSL2, G.fast, MGfast (обязательно)",
    [A1.ist(sW, "G.992.1 7.8"), A1.ist(sWv, "рис. 7-13"), A3.ist(A3.gde("Wei's 16-state 4-dimensional trellis code shall be supported"), "G.992.3"),
     V2.ist(V2.gde("The trellis encoder shall use block processing of Wei's 16-state 4-dimensional trellis code"), "G.993.2"),
     GF.ist(GF.gde("The trellis encoder shall use block processing of Wei's 16-state 4-dimensional trellis code"), "G.9701")],
    "автомат согласован с правилом завершения (все 16 состояний → 0 за 2 шага); текст одинаков в 4 документах", "нет (ТКМ DMT не реализован)")

# ---- G.998.4 RRC расширенный Голей ----
sG = IN.gde("shall contain the check bits of the modified extended (24,12) Golay code")
gG = iz_stepenej([11, 9, 7, 6, 5, 1, 0])
assert poryadok_mnogochlena(gG) == 23
rec("G.INP (G.998.4) канал RRC: модифицированный расширенный код Голея (24,12)", "Голей",
    {"g(D)": "D^11 + D^9 + D^7 + D^6 + D^5 + D + 1", "кодирование": "C(D) = M(D)·D^11 mod G(D), M(D) = b0·D^11 + … + b11",
     "раскладка": "проверочные биты переставлены: C(D) = b17D^10 + b18D^9 + b22D^8 + b21D^7 + b14D^6 + b19D^5 + b23D^4 + b13D^3 + b20D^2 + b15D + b16; b12 — общая чётность"},
    "ADSL2/VDSL2 с повторной передачей (G.INP), G.fast RRC", [IN.ist(sG, "8.4.2")],
    "период g(D) = 23 (проверено) → циклический (23,12) Голей; снимки risunki/g9984_golay*.png", "частично: в проекте Голей (20,8) DMR (dmr.py); (24,12) G.INP с перестановкой проверочных — нет")

# ---- SHDSL / HDSL ----
sS = H2.gde("Figure 6-3 shows the feedforward non-systematic convolutional encoder")
sS2 = H2.gde("Convolutional Encoder Coefficients")
rec("SHDSL (G.991.2): TC-PAM с программируемым свёрточным кодером (до 20 задержек)", "свёрточный (ТКМ 1D, коэффициенты согласуются)",
    {"кодер": "несистематический прямой: Y1 = Σ b_i·X1(m−i), Y0 = Σ a_i·X1(m−i), i = 0…20 (рис. 6-3)", "коэффициенты": "a_i, b_i передаются приёмником при G.994.1 (поле «Convolutional Encoder Coefficients»)",
     "отображение": "K бит → K+1 → уровень PAM 2^(K+1)", "скремблер": "x^−23 ⊕ x^−18 ⊕ 1 / x^−23 ⊕ x^−5 ⊕ 1"},
    "SHDSL, 2BASE-TL (802.3ah)", [H2.ist(sS, "6.1.x"), H2.ist(sS2, "G.994.1 параметры")], "по тексту и рисунку (снимок risunki/g9912_svyortka.png); конкретные коэффициенты — из сеанса G.994.1", "частично: свёрточные коды любых многочленов (svyortka.py)")
rec("HDSL (G.991.1): 2B1Q без FEC; вариант CAP — 2D 8-позиционный решётчатый код + CRC-6", "свёрточный (ТКМ 2D)",
    {"CAP": "64-CAP/128-CAP с 2D 8-state (систематический сверточный, рис. B.2), прекодирование Томлинсона", "CRC-6": "X^6 + X + 1 по кадру", "скремблер": "x^−23 ⊕ x^−18 ⊕ 1 (NTU→LTU), x^−23 ⊕ x^−5 ⊕ 1 (LTU→NTU)"},
    "HDSL E1/T1", [H1.ist(H1.gde("The 2D (2 dimensional) 8-state trellis code"), "Annex B"), H1.ist(H1.gde("generator polynomial X6"), "CRC-6")],
    "по тексту; схема кодера — рисунок B.2", "частично: CRC-6 и скремблер — есть в общем виде")

# ---- MGfast LDPC ----
k, s16 = MG.kusok(r'Table 10-16 – Base graph matrix with "0" and "1" elements of dimension 7×25', r"Table 10-17")
b = [int(x) for x in re.findall(r"(?m)^\s*([01])\s*$", k)]
assert len(b) == 175
BG = [b[r * 25:(r + 1) * 25] for r in range(7)]
doc = pymupdf.open(os.path.join(KOREN, MG.pdf))
stranicy = [i for i, p in enumerate(doc) if "Number of circularly right shifted" in p.get_text() and "Table 10-17" in p.get_text()]
potoki = []
for pi in stranicy:
    p = doc[pi]; W = p.get_text("words"); mid = p.rect.width / 2
    y0 = max(w[1] for w in W if w[4] == "Row")
    for half in (0, 1):
        ws = [w for w in W if w[1] > y0 + 5 and re.fullmatch(r"\d+", w[4]) and ((w[0] < mid) if half == 0 else (w[0] >= mid))]
        lines = collections.defaultdict(list)
        for w in ws: lines[round(w[1] / 3)].append(w)
        potoki.append((half, [[int(w[4]) for w in sorted(lines[kk], key=lambda w: w[0])] for kk in sorted(lines) if len(lines[kk]) in (8, 9)]))
SD = {}; stroka = {0: None, 1: None}
for half, pot in potoki:
    for t in pot:
        if len(t) == 9: stroka[half] = t[0]; t = t[1:]
        SD[(stroka[half], t[0])] = t[1:]
MCS = (64, 96, 128, 192, 256, 384, 512)
assert set(SD) == {(r + 1, c + 1) for r in range(7) for c in range(25) if BG[r][c]}
assert all(0 <= v < mc for vs in SD.values() for v, mc in zip(vs, MCS))
for mi, Mc in enumerate(MCS):
    rows = []
    for r in range(7):
        for rr in range(Mc):
            v = 0
            for c in range(25):
                if BG[r][c]: v |= 1 << (25 * Mc - 1 - (c * Mc + (rr + SD[(r + 1, c + 1)][mi]) % Mc))
            rows.append(v)
    basis = {}; rk = 0
    for x in rows:
        while x:
            h = x.bit_length() - 1
            if h in basis: x ^= basis[h]
            else: basis[h] = x; rk += 1; break
    assert rk == 7 * Mc
s17 = stranicy[0] + 1
tablica("mgfast_ldpc", {"базовый граф 7×25": BG, "сдвиги (строка,столбец) → [Mc=64,96,128,192,256,384,512]": {f"{r},{c}": v for (r, c), v in sorted(SD.items())}},
        "LDPC MGfast (G.9711): базовый граф табл. 10-16 и сдвиги циркулянтов табл. 10-17 для всех 7 значений Mc",
        [MG.ist(s16, "Table 10-16"), MG.ist(s17, "Table 10-17")],
        "77 единиц графа = 77 строк таблицы сдвигов (по координатам слов PDF, две полутаблицы); все сдвиги < Mc; H (7Mc × 25Mc) имеет полный ранг 7Mc для всех 7 Mc → k = 18Mc")
rec("MGfast (G.9711): QC-LDPC 7×25 (скорость 18/25 материнская), Mc = 64…512, с выкалыванием и укорочением — альтернатива TCM+RS", "LDPC (QC)",
    {"H": "7Mc × 25Mc; базовый граф и сдвиги → tablicy/mgfast_ldpc.json", "N": "25Mc минус выколотые (табл. 10-18 — допустимые диапазоны)", "раскладка": "LDPC-экстрактор бит: амплитудные биты + информационные LDPC (10.2.1.3.3)"},
    "MGfast", [MG.ist(MG.gde("The LDPC encoder encodes its input bits by an LDPC code with a code rate rLDPC"), "10.2.1.3.5"), MG.ist(s16), MG.ist(s17)],
    "таблицы собраны программно, полный ранг для всех Mc", "частично: LDPC-движок проекта принимает QC-матрицы; добавить таблицу")

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
