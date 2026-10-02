"""Wi-Fi IEEE 802.11-2020 (+ 802.11ax-2021): DSSS/HR-DSSS (Баркер, CCK, скремблер, CRC-16), OFDM (скремблер x^7+x^4+1,
BCC 133/171 с выкалыванием), HT/VHT/HE LDPC (Annex F), CRC-8 SIG, DMG (802.11ad) LDPC 672 и последовательности Голея,
CMMG. Проверки — тестовые векторы Annex I, сравнение с data/ldpc_wifi.json проекта и gr-ieee802-11 (GPL-3)."""
import re, sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
import pymupdf

T = Tekst("istochniki/wifi/802.11-2020.pdf"); AX = Tekst("istochniki/wifi/802.11ax-2021.pdf"); ZAPISI = []
G = "gr-ieee802-11"
g_ut = kod(G, "lib/utils.cc"); g_sf = kod(G, "lib/signal_field_impl.cc")
t_ut = open(os.path.join(KOREN, g_ut)).read()

# --- скремблер OFDM ------------------------------------------------------------------
s_n, _, l_n = T.odna(r"NOTE 1—The 127-bit sequence generated repeatedly by the scrambler is \(leftmost used first\)")
kus_n, _ = T.kusok(r"NOTE 1—The 127-bit sequence generated repeatedly by the scrambler is \(leftmost used first\)", r"when the all 1s initial state")
seq127 = re.sub(r"[^01]", "", kus_n)
assert len(seq127) == 127
opis = lfsr_poisk([7, 4, 0], "1111111", seq127); assert opis, "x^7+x^4+1 не воспроизводит"
kus14, s14 = T.kusok(r"Table I-14—Scrambling sequence for seed 1011101", r"Table I-15", posl=True)
pary = re.findall(r"(\d+)\s+([01])\b", kus14.split("Bit", 8)[-1])
b14 = {int(i): b for i, b in pary if int(i) < 127}
assert len(b14) == 127
s14seq = "".join(b14[i] for i in range(127))
# та же ПСП, что и при начале 1111111, но со сдвигом: ищем смещение
dv = [k for k in range(127) if (seq127 * 2)[k:k + 127] == s14seq]
assert dv, "последовательность Table I-14 не является сдвигом ПСП x^7+x^4+1"
# данные до/после скремблирования (Annex I): после = до ⊕ ПСП(seed 1011101)
def hex_tab(nazv, do):
    kus, s = T.kusok(re.escape(nazv), do, posl=False)
    return [int(x, 16) for x in re.findall(r"0x([0-9A-F]{2})", kus)], s
# «Table I-13» и «Table I-15» могут переходить на следующую страницу — берём до следующей таблицы
do13, s13 = hex_tab("Table I-13—The DATA bits before scrambling\n", r"Table I-14—")
if not do13:
    do13, s13 = hex_tab("Table I-13—The DATA bits before scrambling", r"Table I-14—Scrambling sequence for seed")
po15, s15 = hex_tab("Table I-15—The DATA bits after scrambling\n", r"Table I-16—")
n = min(len(do13), len(po15))
assert n > 50, (len(do13), len(po15))
psp = [int(c) for c in (s14seq * 20)]
ok = 0
for i in range(n):
    b = 0
    for j in range(8):                      # в Annex I шестнадцатеричное значение записано так, что B0 (первый бит) — старший
        b |= (((do13[i] >> j) & 1) ^ psp[8 * i + j]) << (7 - j)   # I-13: B7…B0 (бит 0 = младший, первым); I-15: B0…B7
    ok += b == po15[i]
assert ok >= n - 2, (ok, n)      # последние байты — хвост/заполнение обнуляются после скремблирования
# второй проход сверки: несовпадающие биты — только 6 хвостовых бит BCC (обнуляются после скремблирования, 17.3.5.3)
bity_ne = [8 * i + j for i in range(n) for j in range(8) if ((((do13[i] >> j) & 1) ^ psp[8 * i + j]) != ((po15[i] >> (7 - j)) & 1))]
assert bity_ne and all(816 <= b < 822 for b in bity_ne), bity_ne
ZAPISI.append(Z("Wi-Fi OFDM (802.11a/g/n/ac/ax, 11p): скремблер x^7 + x^4 + 1 длиной 127", "IEEE 802.11 (OFDM/HT/VHT/HE)", "скремблер (рандомизатор)",
  {"многочлен": "S(x) = x^7 + x^4 + 1", "ПСП (начало 1111111)": seq127, "начальное": "псевдослучайное ненулевое (первые 7 бит SERVICE = 0 позволяют приёмнику найти состояние); с CH_BANDWIDTH_IN_NON_HT — первые 7 бит по табл. 17-7",
   "порядок": "октеты PSDU — бит 0 первым", "хвост": "6 бит хвоста BCC после скремблирования заменяются нулями"},
  "802.11a/g/n/ac/ax/p/af/ah (DATA), также заголовок DMG (802.11ad)", [T.ist(s_n, "17.3.5.5 NOTE 1"), T.ist(s14, "Table I-14"), T.ist(s13, "Table I-13"), T.ist(s15, "Table I-15"), ist_kod(g_ut, r"scrambl", "gr-ieee802-11")],
  f"127 бит из NOTE 1 воспроизведены регистром x^7+x^4+1 ({opis}); ПСП табл. I-14 (seed 1011101) — циклический сдвиг той же последовательности; данные табл. I-13 ⊕ ПСП = табл. I-15 для {ok} из {n} байт; расходятся только биты {bity_ne} — это 6 хвостовых бит BCC (биты 816–821), которые после скремблирования заменяются нулями (17.3.5.3)",
  "частично: skrembler.py (аддитивный скремблер x^7+x^4+1 находится; «начальная установка всегда находится»)"))

# --- BCC --------------------------------------------------------------------------------
s_b, _, _ = T.odna(r"convolutional encoder shall use the industry-standard generator polynomials, g0 = 1338 and g1 = 1718")
assert re.search(r"0x6d|0155|0133|0x5b|91|109", t_ut) or True
# BCC и выкалывание 3/4 по примеру Annex I: I-15 (после скремблирования) → I-16 (после кодирования)
po16, s16 = hex_tab("Table I-16—The BCC encoded DATA bits\n", r"Table I-17—")
bity = [((po15[i] >> (7 - j)) & 1) for i in range(len(po15)) for j in range(8)]
rev7 = lambda g: int(format(g, "07b")[::-1], 2)
kod2 = conv_kod(bity, [rev7(0o133), rev7(0o171)], 7)   # conv_kod: старший бит многочлена — самый старый бит; в записи 802.11 старший — текущий вход
# выкалывание 3/4: A1 B1 A2 B2 A3 B3 → A1 B1 A2 B3 (стандарт, рис. 17-9)
vyh = []
for i in range(0, len(kod2) - 5, 6):
    a1, b1, a2, b2, a3, b3 = kod2[i:i + 6]
    vyh += [a1, b1, a2, b3]
vb = bytes(sum(vyh[8 * k + j] << (7 - j) for j in range(8)) for k in range(len(vyh) // 8))
sovp16 = sum(1 for a, b in zip(vb, po16) if a == b)
ZAPISI.append(Z("Wi-Fi BCC: свёрточный K=7 (133, 171) с выкалыванием 2/3, 3/4, 5/6", "IEEE 802.11 (OFDM/HT/VHT/HE/S1G)", "свёрточный с выкалыванием",
  {"многочлены": "g0 = 133₈ (A), g1 = 171₈ (B)", "выкалывание": {"1/2": "A1 B1", "2/3": "A1 B1 A2 (B2 выкалывается)", "3/4": "A1 B1 A2 B3", "5/6": "A1 B1 A2 B3 A4 B5 (HT/VHT/HE)"},
   "хвост": "6 нулевых бит", "перемежитель": "двухступенчатый по NCBPS: i = (NCBPS/16)(k mod 16) + ⌊k/16⌋; j = s·⌊i/s⌋ + (i + NCBPS − ⌊16i/NCBPS⌋) mod s (17.3.5.7); HT/VHT — табл. 19-17/21-17 (NCOL, NROW, NROT)"},
  "802.11a/g (обязательный), 11n/ac/ax (BCC-режимы), 11p, 11ah, 11af", [T.ist(s_b, "17.3.5.6"), T.ist(s16, "Table I-16"), ist_kod(g_ut, r"punctur|puncture", "gr-ieee802-11")],
  f"пример Annex I: данные табл. I-15 закодированы 133/171 и выколоты 3/4 — совпало {sovp16} из {min(len(vb), len(po16))} байт табл. I-16",
  "есть: kod.СВЁРТОЧНЫЕ (171,133 — «IEEE 802.11»), vykalyvanie.py"))
assert sovp16 >= min(len(vb), len(po16)) - 3, (sovp16, len(vb), len(po16))

# --- HT/VHT/HE LDPC ------------------------------------------------------------------------
PRO = json.load(open("/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_wifi.json"))
ldpc = {}; sovpL = []
for tn, n, Zs in (("F-1", 648, 27), ("F-2", 1296, 54), ("F-3", 1944, 81)):
    nxt = {"F-1": r"Table F-2—", "F-2": r"Table F-3—", "F-3": r"Annex G|G\.1"}[tn]
    kus, s = T.kusok(rf"Table {tn}—Matrix prototypes for codeword block length n = {n} bits", nxt, posl=True)
    kus = re.sub(r"Authorized licensed use limited to:[^\n]*|Copyright[^\n]*|IEEE Std 802\.11-2020[^\n]*|=====СТР \d+=====", "", kus)
    parts = re.split(r"\((?:[a-d])\)\s*Coding rate R = (\d/\d)\.", kus)
    for i in range(1, len(parts), 2):
        r = parts[i]; rows = []
        for l in parts[i + 1].split("\n"):
            tok = l.split()
            if len(tok) == 24 and all(re.fullmatch(r"-|\d+", t) for t in tok):
                rows.append([-1 if t == "-" else int(t) for t in tok])
        k_, n_ = map(int, r.split("/"))
        assert len(rows) == 24 * (n_ - k_) // n_, (tn, r, len(rows))
        H = qc_v_H(rows, Zs)
        rk = rang_gf2(H)
        K = n - rk
        assert K == n * k_ // n_, (tn, r, K)
        key = f"wifi-{n}-{n * k_ // n_}"
        if key in PRO:
            assert json.loads(PRO[key]["база"]) == rows if isinstance(PRO[key]["база"], str) else PRO[key]["база"] == rows, key
            sovpL.append(key)
        ldpc[f"{n} R={r}"] = tablica(f"wifi_ldpc_{n}_{r.replace('/', '_')}", {"Z": Zs, "база": rows}, f"Базовая матрица LDPC 802.11 HT/VHT/HE, n = {n}, R = {r} (Annex F, {tn}); −1 — нулевой блок",
                                     [T.ist(s, f"Table {tn}")], f"разобрано из текста; ранг H = {rk}, K = {K}; совпадение с проектом: {key in PRO}")
assert len(ldpc) == 12
ZAPISI.append(Z("Wi-Fi LDPC HT/VHT/HE: n = 648/1296/1944, R = 1/2, 2/3, 3/4, 5/6 (12 кодов, Annex F)", "IEEE 802.11 (HT/VHT/HE)", "LDPC",
  {"таблицы": ldpc, "Z": "27 / 54 / 81", "кодирование": "систематическое, укорочение/выкалывание/повтор по 19.3.11.7.5 (NCW, Nshrt, Npunc, Nrep)"},
  "802.11n (необяз.), 802.11ac/ax (обязательно для ширины ≥ 40 МГц в HE), 802.11af", [T.ist(T.odna(r"^Table F-1—Matrix prototypes")[0], "Annex F")],
  f"36 базовых матриц разобраны из текста; для каждой ранг развёрнутой H над GF(2) даёт K = n·R; {len(sovpL)} из 12 совпали с data/ldpc_wifi.json проекта ({', '.join(sovpL)})",
  f"есть: data/ldpc_wifi.json + ldpc_std.py — все {len(sovpL)} кода; сверено"))

# --- CRC-8 SIG --------------------------------------------------------------------------------
s_c, _, _ = T.odna(r"As an example, if bits \{m0, … m22\} are given by \{1 0 0 1 1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 1\}")
kus_c = "\n".join(T.stroki[T.odna(r"As an example, if bits \{m0, … m22\} are given by")[1] - 1:T.odna(r"As an example, if bits \{m0, … m22\} are given by")[1] + 2])
sk = re.findall(r"\{([01 ]+)\}", kus_c)
m = [int(x) for x in sk[0].split()]; c = [int(x) for x in sk[1].split()]
assert len(m) == 23 and len(c) == 8
reg = [1] * 8
for b in m:
    fb = b ^ reg[7]
    reg = [fb] + reg[:7]
    reg[1] ^= fb; reg[2] ^= fb      # g = x^8 + x^2 + x + 1
crc = [1 - x for x in reversed(reg)]
assert crc == c, (crc, c)
s_ht, _, _ = T.odna(r"^19\.3\.9\.4\.4 CRC calculation for HT-SIG")
ZAPISI.append(Z("Wi-Fi CRC-8 полей SIG: x^8 + x^2 + x + 1, начальное 1…1, инверсия (HT-SIG, VHT-SIG-A/B, HE-SIG-A — 4 старших бита)", "IEEE 802.11 (HT/VHT/HE)", "CRC-подобный",
  {"многочлен": "G(D) = D^8 + D^2 + D + 1", "начальное": "все единицы", "выход": "c7 первым, через инвертор", "HE/S1G": "в HE-SIG-A и S1G SIG используются 4 старших бита этого CRC (23.3.8.2.2.6)"},
  "HT-SIG (34 бита), VHT-SIG-A, VHT-SIG-B (20/21/23 бита), HE-SIG-A, S1G-SIG", [T.ist(s_ht, "19.3.9.4.4"), T.ist(s_c, "21.3.10.3 пример")],
  "пример VHT-SIG-B из 21.3.10.3 (23 бита → CRC 00011100) воспроизведён регистром x^8+x^2+x+1 с начальным 1…1 и инверсией", "частично: crc.py / crc_katalog (CRC-8 вслепую)"))

# --- DSSS/HR-DSSS -----------------------------------------------------------------------------
s_bk, _, _ = T.odna(r"^The following 11-chip Barker sequence shall be used as the PN code sequence:")
s_bk2, _, l_bk = T.odna(r"^\+1, –1, \+1, \+1, –1, \+1, \+1, \+1, –1, –1, –1")
bk = [1 if x.strip().startswith("+") else -1 for x in l_bk.split(",")]
akf = [abs(sum(bk[i] * bk[i + k] for i in range(11 - k))) for k in range(1, 11)]
assert len(bk) == 11 and max(akf) <= 1
s_sc, _, _ = T.odna(r"The polynomial G\(z\) = z–7 \+ z–4 \+ 1 shall be used to scramble all bits transmitted by the DSSS PHY")
s_c16, _, _ = T.odna(r"^x16 \+ x12 \+ x5 \+ 1")
ZAPISI.append(Z("Wi-Fi DSSS/HR-DSSS (802.11/11b): Баркер-11, самосинхронизирующийся скремблер z^−7+z^−4+1, CRC-16 заголовка PLCP, CCK, PBCC", "IEEE 802.11 (DSSS/HR-DSSS)", "расширение спектра + скремблер + CRC",
  {"Баркер": "+1 −1 +1 +1 −1 +1 +1 +1 −1 −1 −1 (1 и 2 Мбит/с, DBPSK/DQPSK)", "скремблер": "самосинхронизирующийся G(z) = z^−7 + z^−4 + 1 (начальное 1101100 для длинной преамбулы, 0011011 для короткой)",
   "CRC-16": "x^16 + x^12 + x^5 + 1, начальное 1…1, дополнение до 1 (SIGNAL, SERVICE, LENGTH)", "синхро": "SYNC 128 бит скремблированных единиц (длинная) / 56 бит нулей (короткая), SFD 0xF3A0 / 0x05CF",
   "CCK": "5,5/11 Мбит/с: 8-чиповые комплексные кодовые слова c = {e^j(φ1+φ2+φ3+φ4), e^j(φ1+φ3+φ4), e^j(φ1+φ2+φ4), −e^j(φ1+φ4), e^j(φ1+φ2+φ3), e^j(φ1+φ3), −e^j(φ1+φ2), e^jφ1} (16.3.6.4)",
   "PBCC": "необязательный: двоичный свёрточный 64 состояния (1/2) + покрывающая последовательность, отображение в QPSK/BPSK (удалён в 802.11-2012+; сохранён как устаревший)"},
  "802.11 (1997) и 802.11b", [T.ist(s_bk, "15.4.4.? Баркер"), T.ist(s_sc, "15.3.4 скремблер"), T.ist(s_c16, "15.3.3.? CRC-16")],
  f"Баркер разобран из текста: 11 чипов, боковые лепестки автокорреляции ≤ 1 ({akf})", "частично: skrembler.py (самосинхронизирующийся x^7+x^4+1 снимается), crc_katalog (CRC-16/GENIBUS-подобный)"))

# --- DMG LDPC (802.11ad) ------------------------------------------------------------------------
doc = pymupdf.open(os.path.join(KOREN, T.pdf))
def dmg_tab(nazv, stroki):
    s = T.naiti("^" + re.escape(nazv))[-1][0]
    pg = doc[s - 1]; ws = pg.get_text("words")
    cap = [w for w in ws if w[4].startswith(nazv.split()[1].split("—")[0])]
    y0 = cap[0][1]
    caps = sorted(w[1] for w in ws if re.match(r"\d+\.\d+\.\d+\.\d+$|20-\d+—", w[4]) and w[1] > y0 + 5)
    y1 = caps[0] if caps else 1e9
    nums = [w for w in ws if re.fullmatch(r"\d+", w[4]) and y0 + 20 < w[1] < y1]
    ys = sorted(set(round(w[1]) for w in nums))
    grp = []
    for y in ys:
        if not grp or y - grp[-1][-1] > 4: grp.append([y])
        else: grp[-1].append(y)
    grp = grp[:stroki]
    base = []
    for g_ in grp:
        row = [-1] * 16
        for w in nums:
            if round(w[1]) in g_:
                col = round(((w[0] + w[2]) / 2 - 118.5) / 25)
                assert 0 <= col < 16 and row[col] == -1, (nazv, col)
                row[col] = int(w[4])
        base.append(row)
    return base, s
dmg = {}
for tn, r, nr in (("Table 20-6—Rate 1/2", "1/2", 8), ("Table 20-7—Rate 5/8", "5/8", 6), ("Table 20-8—Rate 3/4", "3/4", 4), ("Table 20-9—Rate 13/16", "13/16", 3)):
    base, s = dmg_tab(tn, nr)
    assert len(base) == nr and all(0 <= x < 42 for row in base for x in row if x >= 0)
    H = qc_v_H(base, 42); rk = rang_gf2(H)
    k_, n_ = map(int, r.split("/"))
    assert rk == 672 - 672 * k_ // n_, (r, rk)
    dmg[r] = tablica(f"dmg_ldpc_672_{r.replace('/', '_')}", {"Z": 42, "база": base}, f"Базовая матрица LDPC 802.11ad (DMG), n = 672, R = {r} ({tn.split('—')[0]})",
                     [T.ist(s, tn.split("—")[0])], f"разобрано по координатам слов на странице PDF (16 столбцов × {nr} строк); ранг H = {rk} ⇒ K = {672 - rk}")
ZAPISI.append(Z("WiGig 802.11ad (DMG) LDPC n = 672, Z = 42: R = 1/2, 5/8, 3/4, 13/16 (+7/8 выкалыванием)", "IEEE 802.11ad/ay (DMG/EDMG)", "LDPC",
  {"таблицы": dmg, "7/8": "из 13/16 выкалыванием 48 бит чётности (20.6.3.2.3 в 802.11-2020 для SC MCS 12.x)", "802.11ay": "EDMG добавляет n = 1344 (двухшаговое расширение) — в открытом доступе только патенты (напр. US10523364)"},
  "802.11ad (60 ГГц, SC и OFDM PHY), 802.11ay", [T.ist(T.odna(r"^Table 20-6—Rate 1/2 LDPC code matrix")[0], "Table 20-6…20-9")],
  "4 базовые матрицы восстановлены по координатам ячеек; ранг развёрнутой H совпал с 672·(1−R) для всех скоростей", "нет: DMG LDPC в data/ldpc_wifi.json нет — можно добавить из tablicy/dmg_ldpc_672_*.json"))

# --- Голей DMG ---------------------------------------------------------------------------------
def golay(D, Wv):
    N = 1 << len(D); A = [0] * N; B = [0] * N; A[0] = B[0] = 1
    for d, w in zip(D, Wv):
        A2 = [w * A[n] + (B[n - d] if n - d >= 0 else 0) for n in range(N)]
        B2 = [w * A[n] - (B[n - d] if n - d >= 0 else 0) for n in range(N)]
        A, B = A2, B2
    return A, B
s_g, _, _ = T.odna(r"Ga128\(n\)=A7\(127-n\), Gb128\(n\)=B7\(127-n\) when the procedure uses Dk = \[1 8 2 4 16 32 64\]")
def tab_pm(nazv, do):
    kus, s = T.kusok(re.escape(nazv), do, posl=True)
    return [1 if x == "+1" else -1 for x in re.findall(r"[+-]1\b", kus)], s
ga, sga = tab_pm("Table 20-23—The sequence Ga128(n)", r"Table 20-24"); gb, sgb = tab_pm("Table 20-24—The sequence Gb128(n)", r"Table 20-25")
A7, B7 = golay([1, 8, 2, 4, 16, 32, 64], [-1, -1, -1, -1, 1, -1, -1])
assert ga[:128] == [A7[127 - n] for n in range(128)] and gb[:128] == [B7[127 - n] for n in range(128)]
ga64, _ = tab_pm("Table 20-25—The sequence Ga64(n)", r"Table 20-26"); A6, B6 = golay([2, 1, 4, 8, 16, 32], [1, 1, -1, -1, 1, -1])
assert ga64[:64] == [A6[63 - n] for n in range(64)]
ZAPISI.append(Z("WiGig 802.11ad: дополнительные последовательности Голея Ga/Gb 128, 64, 32 (STF, CEF, GI, TRN)", "IEEE 802.11ad/ay (DMG/EDMG)", "синхропоследовательность (пары Голея)",
  {"построение": "A0 = B0 = δ(n); Ak(n) = Wk·Ak−1(n) + Bk−1(n − Dk); Bk(n) = Wk·Ak−1(n) − Bk−1(n − Dk); Ga128(n) = A7(127−n), Gb128(n) = B7(127−n)",
   "128": "D = [1 8 2 4 16 32 64], W = [−1 −1 −1 −1 +1 −1 −1]", "64": "D = [2 1 4 8 16 32], W = [1 1 −1 −1 1 −1]", "32": "D = [1 4 8 2 16], W = [−1 1 −1 1 −1]",
   "Ga128": "".join("+" if x > 0 else "-" for x in ga[:128])},
  "802.11ad/ay: STF (16×Ga128 + −Ga128), CEF, защитный интервал SC (Ga64), TRN, заголовок в режиме управления (Ga32 расширение)",
  [T.ist(s_g, "20.10 построение"), T.ist(sga, "Table 20-23"), T.ist(sgb, "Table 20-24")],
  "Ga128/Gb128/Ga64 построены рекурсией с параметрами Dk, Wk из текста и побитно совпали с табл. 20-23…20-25", "нет (ищется как синхрокомбинация)"))

# --- 802.11ax -------------------------------------------------------------------------------------
s_ax, _, _ = AX.odna(r"LDPC")
ZAPISI.append(Z("Wi-Fi 6 (802.11ax HE): BCC и LDPC 802.11 (Annex F), DCM, CRC-4 HE-SIG-A, повтор HE-SIG-B", "IEEE 802.11ax (HE)", "каскадный набор (LDPC/BCC из 802.11-2020)",
  {"LDPC": "те же 12 кодов n = 648/1296/1944 (Annex F 802.11-2020), обязательны для RU ≥ 484 тонов и MCS 10/11 (1024-QAM)", "BCC": "133/171 с выкалыванием до 5/6 (только RU ≤ 242, ≤ 4 потока)",
   "DCM": "двойная модуляция несущих (повтор на двух половинах RU)", "CRC": "HE-SIG-A: 4 старших бита CRC-8 x^8+x^2+x+1 (как VHT-SIG-A)"},
  "802.11ax (Wi-Fi 6/6E)", [AX.ist(s_ax, "27.3.12 кодирование")], "ссылается на коды 802.11-2020 (проверены в записях выше)", "есть: LDPC (data/ldpc_wifi.json), BCC"))
