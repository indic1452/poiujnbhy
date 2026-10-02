"""TETRAPOL (PAS 0001-2 v3.0.0 Radio Air Interface): речевые и информационные кадры — CRC-3/CRC-5, кольцевой свёрточный K=3 1/2,
перемежение VHF (формула) и UHF (векторы K), дифференциальное предкодирование UHF, скремблер 127, высокоскоростные данные — укороченный
БЧХ (76,48), RACH — CRC-10 + K=3 1/3 + перемежение 31k mod 75, синхропоследовательности. Сверка: tetrapol-kit (GPL-2.0)."""
import re, sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from obshchee import *

T = Tekst("istochniki/tetrapol/TETRAPOL_PAS0001-2_Radio_Air_Interface_v300.pdf")
ZAPISI = []
pc = kod("tetrapol-kit", "lib/phys_ch.c"); db = kod("tetrapol-kit", "lib/data_block.c")
def s_(pat, i=-1): return [s for s, n, l in T.naiti(pat)][i]
def pmod(a, b):
    while a and a.bit_length() >= b.bit_length(): a ^= b << (a.bit_length() - b.bit_length())
    return a
def umn(a, b):
    r = 0
    while b:
        if b & 1: r ^= a
        a <<= 1; b >>= 1
    return r

# --- перемежение VHF: k(j) = 19·p(j mod 8) + (3·⌊j/8⌋ mod 19), пример k(123) = 121 ------------------------------
s_vhf = T.odna(r"Example: k\(123\) = 19 p\(3\) \+ \( 3\*15 mod 19\) = 19\*6 \+ 7 = 121")[0]
p = [0, 4, 2, 6, 1, 5, 3, 7]
assert all(f"p({i}) = {p[i]}" in " ".join(T.ves.split()) for i in range(8))
kvhf = [19 * p[j % 8] + (3 * (j // 8)) % 19 for j in range(152)]
assert kvhf[123] == 121 and sorted(kvhf) == list(range(152))
assert c_massiv(pc, "interleave_voice_data_VHF") == kvhf
# --- UHF: векторы K (речь 6.1.4.1, данные 6.2.4.1) ----------------------------------------------------------------
def vektor_K(razdel_ot, razdel_do):
    kus, s = T.kusok(r"^" + razdel_ot + r"\.\s*\n.*?K =\s*\n?\s*\[", r"\]", flags=re.S | re.M, posl=True)
    v = [int(x) for x in re.findall(r"\d+", re.sub(r"=====СТР \d+=====.*?circulated without permission\.", " ", kus, flags=re.S))]
    return v, s
KV, s_kv = vektor_K(r"6\.1\.4\.1", r"6\.1\.4\.2"); KD, s_kd = vektor_K(r"6\.2\.4\.1", r"6\.2\.4\.2")
assert len(KV) == 152 and len(KD) == 152, (len(KV), len(KD))
k_osn = lambda K: sorted(K) == list(range(1, 153)) or sorted(K) == list(range(152))
assert k_osn(KV) and k_osn(KD)
sdv = 1 if min(KV) == 1 else 0                              # в тексте номера с 1 (MATLAB-стиль) — приводим к 0
assert c_massiv(pc, "interleave_voice_UHF") == [k - sdv for k in KV] and c_massiv(pc, "interleave_data_UHF") == [k - sdv for k in KD]
# --- дифференциальное предкодирование UHF (PRE_COD, 47 значений) ----------------------------------------------------------
kus, s_pre = T.kusok(r"PRE_COD = \[", r"\]")
PRE = [int(x) for x in re.findall(r"\d+", kus)]
assert len(PRE) == 47
assert c_massiv(pc, "diff_precod_UHF") == [2 if i in PRE else 1 for i in range(152)]
# --- скремблер: s(k) = 1 (k ≤ 6), s(k) = s(k−1) ⊕ s(k−7), период 127 -------------------------------------------------------
s_scr = T.odna(r"s\(k\) = \( s\(k-1\) \+ s\(k-7\) \) modulo 2 for k > 6")[0]
s = [1] * 7
while len(s) < 127 + 20: s.append(s[-1] ^ s[-7])
assert s[127:147] == s[:20] and c_massiv(pc, "scramb_table") == s[:127]
# --- CRC и свёрточный -------------------------------------------------------------------------------------------------
s_crc3 = T.odna(r"is a multiple of \(I \+ D \+ D3\)")[0]; s_crc5 = T.odna(r"is a multiple of \(I\+D2\+D5\)")[0]
s_cc = T.odna(r"C2j\s+= \(b\"j \+ b\"j-1 \+ b\"j-2\) modulo 2")[0]
s_rach = T.odna(r"is a multiple of I \+ D3 \+ D5 \+ D6 \+ D8 \+ D9 \+ D10")[0]
tdb = open(os.path.join(KOREN, db)).read()
assert re.search(r"mk_crc3", tdb) and re.search(r"mk_crc5", tdb)
assert dfree([0b111, 0b101], 3) == 5 and dfree([0b101, 0b111, 0b111], 3) == 8
# --- БЧХ высокоскоростных данных: g(D) степени 28 = произведение 4 минимальных многочленов GF(2^7) -------------------------
s_bch = T.odna(r"\(I\+D3\+D4\+D5\+D7\+D9\+D10\+D13\+D18\+D19\+D20\+D23\+D26\+D27\+D28\)\.")[0]
g28 = mnogochlen([0, 3, 4, 5, 7, 9, 10, 13, 18, 19, 20, 23, 26, 27, 28])
mnozh = [mnogochlen([0, 3, 7]), mnogochlen([0, 1, 2, 3, 7]), mnogochlen([0, 2, 3, 4, 7]), mnogochlen([0, 1, 2, 4, 5, 6, 7])]
T.odna(r"\(I\+D3\+D7\) \(I\+D\+D2\+ D3\+D7\)\(I\+D2\+D3\+D4\+D7\) \(I\+D\+D2\+D4\+ D5\+ D6\+D7\)")
pr = 1
for m in mnozh: pr = umn(pr, m)
assert pr == g28 and pmod((1 << 127) | 1, g28) == 0                               # делит x^127 + 1 → циклический код длины 127
# минимальное расстояние укороченного (76,48): полный перебор невозможен — проверяем, что корни α^1…α^8 (t = 4) лежат среди нулей:
exp = [1]
for _ in range(126):
    v = exp[-1] << 1
    if v & 0x80: v ^= mnogochlen([0, 3, 7])                    # нужен примитивный многочлен степени 7; найдём корни g
    exp.append(v)
def ocenka(poly, a):  # значение многочлена над GF(2) в точке a ∈ GF(2^7) (поле по x^7+x^3+1)
    r = 0; st = 1
    for i in range(poly.bit_length()):
        if (poly >> i) & 1: r ^= st
        # st *= a
        x, y, z = st, a, 0
        while y:
            if y & 1: z ^= x
            x <<= 1
            if x & 0x80: x ^= mnogochlen([0, 3, 7])
            y >>= 1
        st = z
    return r
korni = [i for i in range(127) if ocenka(g28, exp[i]) == 0]
assert len(korni) == 28
posl = max(L for L in range(1, 30) for b in range(127) if all(((b + t) % 127) in korni for t in range(L)))
assert posl >= 8                                                                     # 8 подряд идущих корней → d ≥ 9 (t = 4), граница БЧХ
s_hr = T.odna(r"truncated block code \(n=126,k=98,t=4\)")[0]
# --- RACH: перемежение j = 31k mod 75, синхро ------------------------------------------------------------------------------
s_ril = T.odna(r"with j = \(k x 31\) modulo 75")[0]
assert sorted((31 * k) % 75 for k in range(75)) == list(range(75))
s_fs = T.odna(r"\{f0 \.\.\. f7\} = \{0 1 1 0 0 0 1 0\}")[0]
fs_kit = re.search(r"frame_sync\[\]\s*=\s*\{([^}]*)\}|FRAME_SYNC", open(os.path.join(KOREN, pc)).read())
s_tr = T.odna(r"\{s0 \.\.\. s14\} = \{011010111100010\}")[0]
s_dme = T.odna(r"\{f0 \.\.\. f31\} = \{0101 1010 0000 1111 0101 1010 0000 1111\}")[0]
tablica("tetrapol_pas0001-2", {"perem_VHF_k": kvhf, "perem_UHF_rech_K": [k - sdv for k in KV], "perem_UHF_dannye_K": [k - sdv for k in KD],
                               "PRE_COD": PRE, "skrembler_127": s[:127], "BCH_g28_stepeni": [0, 3, 4, 5, 7, 9, 10, 13, 18, 19, 20, 23, 26, 27, 28], "RACH_perem": [(31 * k) % 75 for k in range(75)]},
        "TETRAPOL: перемежители VHF/UHF (номер позиции для кодового бита j, с 0), PRE_COD, скремблер, многочлен БЧХ, перемежение RACH",
        [T.ist(s_vhf, "6.1.3.1"), T.ist(s_kv, "6.1.4.1 K"), T.ist(s_kd, "6.2.4.1 K"), T.ist(s_pre, "6.1.4.2 PRE_COD"), T.ist(s_scr, "6.1.5.1"), T.ist(s_bch, "6.3.2"), T.ist(s_ril, "6.4.3"),
         ist_kod(pc, "interleave_voice_UHF"), ist_kod(pc, "scramb_table")],
        "VHF: формула с примером k(123) = 121, биекция, = interleave_voice_data_VHF; UHF: векторы K (152, в тексте с 1) = interleave_voice_UHF / interleave_data_UHF − 1; "
        "PRE_COD = diff_precod_UHF; скремблер по рекурсии = scramb_table; tetrapol-kit (aeburriel)")

def rec(imya, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": "TETRAPOL (PAS 0001-2)", "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
NET = "нет в проекте TETRAPOL; средняя: элементы простые (K=3, CRC, таблицы готовы в tablicy/tetrapol_pas0001-2.json)"
OBSH = {"перемежение VHF": "k(j) = 19·p(j mod 8) + (3·⌊j/8⌋ mod 19), p — обращение 3 бит", "перемежение UHF": "векторы K речи/данных (152)",
        "предкодирование UHF": "e'0 = e0 ⊕ f7; e'j = ej ⊕ e'(j−2) при j ∈ PRE_COD (47 позиций), иначе ej ⊕ e'(j−1); VHF — нет",
        "скремблер": "s(k) = 1 (k=0…6), s(k) = s(k−1) ⊕ s(k−7), период 127; S(SCR,k) = s(k+SCR), SCR ∈ [0,127], SCR = 0 — без скремблирования",
        "кадр": "160 бит / 20 мс (8 кбит/с GMSK): синхро 01100010 + 152"}
rec("TETRAPOL речевой кадр: CRC-3 + кольцевой свёрточный K=3 1/2 (26 бит) + 100 незащищённых", "каскадный: CRC-3 + свёрточный K=3 (tail-biting)",
    {"защищаемые": "D=0 + 20 бит речи + 2 ASB = 23 + CRC-3 (1+D+D³, инвертирован) = 26", "свёрточный": "C2j = b″j⊕b″j−1⊕b″j−2, C2j+1 = b″j⊕b″j−2 (7, 5 восьм.), кольцевой: b″−2 = b″24, b″−1 = b″25; d_free = 5",
     "незащищённые": "100 бит речи C52…C151", **OBSH},
    "TETRAPOL речь (RT ↔ BS, прямой режим)", [T.ist(s_crc3, "6.1.1"), T.ist(s_cc, "6.1.2"), T.ist(s_vhf, "6.1.3"), T.ist(s_pre, "6.1.4.2"), T.ist(s_scr, "6.1.5")],
    "перемежение, PRE_COD, скремблер = tetrapol-kit phys_ch.c; CRC-3 = mk_crc3 data_block.c", NET)
rec("TETRAPOL информационный кадр: CRC-5 + свёрточный K=3 1/2 (два блока 26 кольцевой + 50 с хвостом)", "каскадный: CRC-5 + свёрточный K=3",
    {"данные": "D=1 + 68 бит + CRC-5 (1+D²+D⁵) = 74", "блок 1": "d0…d25 — кольцевой (b'−2 = d24, b'−1 = d25) → 52", "блок 2": "d26…d73 + 2 нулевых хвоста → 100", **OBSH},
    "TETRAPOL сигнализация и данные (CCH, TCH-данные)", [T.ist(s_crc5, "6.2.1"), T.ist(s_kd, "6.2.4.1")], "= tetrapol-kit (mk_crc5, interleave_data_UHF)", NET)
rec("TETRAPOL высокоскоростные данные: укороченный БЧХ (76,48, t=4) ×2", "БЧХ (укороченный)",
    {"g(D)": "1+D³+D⁴+D⁵+D⁷+D⁹+D¹⁰+D¹³+D¹⁸+D¹⁹+D²⁰+D²³+D²⁶+D²⁷+D²⁸ = (1+D³+D⁷)(1+D+D²+D³+D⁷)(1+D²+D³+D⁴+D⁷)(1+D+D²+D⁴+D⁵+D⁶+D⁷)",
     "код": "из циклического (127,99) над GF(2^7), в тексте «(126,98)»; 96 бит (2 FN + 92 + 2 ASB) → 2 × 48 → 2 × 76", "перемежение": "k(n) = 2n, k(76+n) = 2n+1", "CRC": "нет (контроль — синдром БЧХ)"},
    "TETRAPOL High Rate Data", [T.ist(s_hr, "6.3"), T.ist(s_bch, "6.3.2")],
    f"произведение 4 множителей = g(D); g делит x^127+1; {posl} подряд идущих корней α^i → граница БЧХ d ≥ 9 (t = 4)", NET)
rec("TETRAPOL RACH: CRC-10 (9 передаются) + свёрточный K=3 1/3 + перемежение 31k mod 75", "каскадный: CRC + свёрточный K=3 1/3",
    {"данные": "14 бит + CRC-10 (1+D³+D⁵+D⁶+D⁸+D⁹+D¹⁰), бит b'14 удаляется, 9 инвертированы, 2 нуля хвост", "свёрточный": "C3j = b″j⊕b″j−2, C3j+1 = C3j+2 = b″j⊕b″j−1⊕b″j−2 (d_free = 8)",
     "перемежение": "e_k = C_(31k mod 75)", "формат": "синхро 10111110, 3 синхропоследовательности по 7+2 бита, блоки 37 и 38, защитное 10001"},
    "TETRAPOL случайный доступ", [T.ist(s_rach, "6.4.1"), T.ist(s_ril, "6.4.3")], "по тексту стандарта (в tetrapol-kit обработки RACH нет); d_free вычислено", NET)
rec("TETRAPOL синхропоследовательности: кадровая 01100010, тренировочная 15 бит, аварийный кадр, SCH/TI 64 бит", "синхрослово",
    {"кадровая": "01100010 (f0…f7 каждого кадра)", "тренировочная": "011010111100010 (повтор)", "аварийный прямой режим": "0101 1010 0000 1111 ×2 (повтор до 160)",
     "SCH/TI": "D(0) = 0110 0000 1111 1100 0011 1110 1001 1111 0110 1101 0010 1001 0000 0110 0111 0000, сдвиг 2·ID, интегрирование"},
    "поиск кадров TETRAPOL", [T.ist(s_fs, "6.1.5.2"), T.ist(s_tr, "6.5"), T.ist(s_dme, "6.6"), T.ist(s_("D\\(0\\) = \\{ 0110 0000"), "6.7.2")],
    "из текста стандарта; кадровая синхро используется tetrapol-kit (find_frame_sync)", NET)

if __name__ == "__main__":
    print(len(ZAPISI), "записей", sdv, posl)
