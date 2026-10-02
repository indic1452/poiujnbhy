"""NB-IoT и LTE-M (3GPP TS 36.211/36.212 v19.3.0, ETSI TS 136 211/212): что отличает NB-IoT от LTE (общие коды LTE —
CRC24A/16, TBCC 1/3, турбокод QPP — описаны в области «mobilnaya»). Сверка — srsRAN_4G (AGPL-3.0): таблицы NSSS/NPSS,
маска CRC NPBCH, формулы c_init; ГПСП Голда — прогон скомпилированного sequence.c srsRAN (skripty/gold_srsran_test.c)."""
import re, sys, os, json, cmath
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

A = Tekst("istochniki/nbiot/ts_136211v190300p.pdf"); B = Tekst("istochniki/nbiot/ts_136212v190300p.pdf"); ZAPISI = []
sq = kod("srsRAN_4G", "lib/src/phy/common/sequence.c"); sqs = kod("srsRAN_4G", "lib/src/phy/phch/sequences.c")
npb = kod("srsRAN_4G", "lib/src/phy/phch/npbch.c"); nss = kod("srsRAN_4G", "lib/include/srsran/phy/sync/nsss.h"); nps = kod("srsRAN_4G", "lib/src/phy/sync/npss.c")
SL = "NB-IoT / LTE-M (3GPP LTE Rel-13+)"

# ГПСП Голда 7.2
s_g = A.odna(r"Pseudo-random sequences are defined by a length-31 Gold sequence")[0]
def gold(cinit, n):
    x1 = [1] + [0] * 30; x2 = [(cinit >> i) & 1 for i in range(31)]
    for k in range(1600 + n):
        x1.append(x1[k + 3] ^ x1[k]); x2.append(x2[k + 3] ^ x2[k + 2] ^ x2[k + 1] ^ x2[k])
    return "".join(str(x1[k + 1600] ^ x2[k + 1600]) for k in range(n))
vyv = open(os.path.join(KOREN, "skripty", "gold_srsran_vyvod.txt")).read().split("\n")
proveril = 0
for l in vyv:
    if l.strip():
        s, c = l.split(); assert gold(int(s), 64) == c, s; proveril += 1
assert proveril == 3 and "#define SEQUENCE_NC (1600)" in open(os.path.join(KOREN, sq)).read()
t_sq = open(os.path.join(KOREN, sqs)).read()
assert "(nslot / 2) * 512 + cell_id" in t_sq and "(rnti << 14) + ((nf % 2) << 13) + ((nslot / 2) << 9) + cell_id" in t_sq
assert "srsran_sequence_LTE_pr(seq, SRSRAN_NBIOT_NPBCH_NOF_TOTAL_BITS, cell_id)" in t_sq
s_pb = A.odna(r"equals 1600 for normal cyclic prefix\. The scrambling sequence shall be initialised with")[0]
s_pd = A.odna(r"Scrambling shall be done according to clause 6\.8\.2\. The scrambling sequence shall be initialised at the start of subframe")[0]
s_pu = A.odna(r"^10\.1\.3\.1\s*$|10\.1\.3\.1  Scrambling")[0]
ZAPISI.append(Z("3GPP ГПСП Голда c(n) длины 31 (x1: x^31 + x^3 + 1, x2: x^31 + x^3 + x^2 + x + 1, Nc = 1600) и её затравки c_init для каналов NB-IoT", SL, "скремблер (рандомизатор)",
  {"c(n)": "c(n) = (x1(n + Nc) + x2(n + Nc)) mod 2, Nc = 1600; x1(n+31) = x1(n+3) + x1(n); x2(n+31) = x2(n+3) + x2(n+2) + x2(n+1) + x2(n)",
   "начальное": "x1(0) = 1, x1(1…30) = 0; x2 — двоичная запись c_init (x2(i) = бит i)",
   "c_init NB-IoT": {"NPBCH": "N_ID^Ncell (на радиокадрах с nf mod 64 = 0)", "NPDCCH": "⌊ns/2⌋·2^9 + N_ID^Ncell", "NPDSCH/NPUSCH": "n_RNTI·2^14 + (nf mod 2)·2^13 + ⌊ns/2⌋·2^9 + N_ID^Ncell",
                     "NPDSCH с BCCH (Rel-14)": "по srsRAN: 0xFFFF·2^15 + (N_ID+1)·((nf mod 61)+1)"},
   "применение": "тот же генератор — LTE (PDSCH, PUSCH, PBCH…), LTE-M, опорные сигналы; в 5G NR — та же c(n)"},
  "LTE, LTE-M (eMTC), NB-IoT, 5G NR", [A.ist(s_g, "7.2"), A.ist(s_pb, "NPBCH"), A.ist(s_pd, "NPDCCH 10.2.5.2"), A.ist(s_pu, "NPUSCH 10.1.3.1"),
   ist_kod(sq, r"#define SEQUENCE_NC \(1600\)", "srsRAN Nc"), ist_kod(sqs, r"\(nslot / 2\) \* 512 \+ cell_id", "srsRAN NPDCCH"), ist_kod(sqs, r"\(rnti << 14\)", "srsRAN NPDSCH/NPUSCH")],
  "c(n) по формуле 7.2 совпала побитно (64 бита) с выводом скомпилированного sequence.c srsRAN для c_init = 0, 1, 0x12345 (skripty/gold_srsran_test.c, gold_srsran_vyvod.txt); c_init NPBCH/NPDCCH/NPDSCH — по srsRAN и тексту 36.211 (формулы в PDF извлекаются с искажениями)",
  "частично: skrembler.py — ГПСП Голда 3GPP как отдельный генератор не встроена (x^31 находится как многочлен аддитивного скремблера)"))

# NPBCH / NPDSCH / NPDCCH / NPUSCH — таблицы 6.2-1, 6.2-2
s_62 = B.odna(r"Table 6\.2-1: Usage of channel coding scheme and coding rate for TrCHs")[0]
s_bch = B.odna(r"The size of the BCH transport block is set to 34 bits")[0]
s_msk = B.odna(r"^Table 5\.3\.1\.1-1: CRC mask for PBCH")[0]
kus_m, _ = B.kusok(r"Table 5\.3\.1\.1-1: CRC mask for PBCH", r"5\.3\.1\.2")
maski = {int(a): [int(v) for v in b.split(",")] for a, b in re.findall(r"\n\s*(\d)\s*\n<([01, ]+)>", kus_m)}
assert set(maski) == {1, 2, 4} and all(len(v) == 16 for v in maski.values())
t_npb = open(os.path.join(KOREN, npb)).read()
sr = [[int(v) for v in r.split(",")] for r in re.findall(r"\{([01, ]+)\}", t_npb.split("srsran_npbch_crc_mask[4][16] =")[1].split(";")[0])]
assert sr[0] == maski[1] and sr[1] == maski[2] and sr[3] == maski[4]
s_dl = B.odna(r"^6\.4\.2\s*$|Downlink shared channel and Paging channel")[0]
s_ack = B.odna(r"^Table 6\.3\.3-1: HARQ-ACK code words")[0]
kus_a, _ = B.kusok(r"Table 6\.3\.3-1: HARQ-ACK code words", r"6\.3\.4")
sl_a = re.findall(r"<([01,]+)>", kus_a.replace(" ", "").replace("\n", ""))
assert sl_a[-2:] == [",".join("0" * 16), ",".join("1" * 16)] or sl_a[-2:] == ["0," * 15 + "0", "1," * 15 + "1"], sl_a
s_ul = B.odna(r"The CRC attachment, channel coding, and rate matching are performed according to clauses 5\.2\.2\.1, 5\.2\.2\.3, and")[0]
s_dci = B.odna(r"^6\.4\.3\s*$|A DCI transports downlink or uplink scheduling information for one cell and one RNTI")[0]
ZAPISI.append(Z("NB-IoT канальное кодирование: NPBCH (34 + CRC16 с маской портов, TBCC 1/3, 1600 бит), NPDSCH (CRC24A + TBCC 1/3!), NPDCCH (DCI N0/N1/N2, CRC16 ⊕ RNTI, TBCC), NPUSCH ф.1 (турбо 1/3), ф.2 (ACK — повтор 16)", SL, "каскадный (CRC + свёрточный / турбо / повторение)",
  {"NPBCH": "MIB-NB 34 бита, CRC16 ⊕ маска: 1 порт — 0…0, 2 порта — 1…1 (4 порта — 0101…); TBCC 1/3 (133,171,165); согласование скорости до 1600 бит; TTI 640 мс, 8 блоков по 200 бит",
   "маски CRC": maski, "NPDSCH (DL-SCH, PCH)": "CRC24A, свёрточный 1/3 с циклическим хвостом (НЕ турбо, в отличие от LTE PDSCH); без сегментации кодовых блоков",
   "NPDCCH": "DCI N0 (UL-грант), N1 (DL), N2 (пейджинг); CRC16 скремблирован RNTI; TBCC 1/3", "NPUSCH формат 1 (UL-SCH)": "CRC24A + турбокод LTE 1/3 (QPP), π/4-QPSK/π/2-BPSK; перемежение по ресурсному блоку (время прежде частоты)",
   "NPUSCH формат 2 (UCI)": "1 бит HARQ-ACK → 16 одинаковых бит (код повторения 1/16)", "LTE-M": "каналы MPDCCH/PDSCH/PUSCH — коды LTE (турбо, TBCC, CRC) с повторениями"},
  "NB-IoT (Cat-NB1/NB2), LTE-M (Cat-M1), IoT в сетях LTE/5G", [B.ist(s_62, "табл. 6.2-1/6.2-2"), B.ist(s_bch, "6.4.1 BCH"), B.ist(s_msk, "маска CRC"), B.ist(s_dl, "6.4.2"), B.ist(s_ul, "6.3.2"), B.ist(s_ack, "6.3.3"), B.ist(s_dci, "6.4.3"),
   ist_kod(npb, r"srsran_npbch_crc_mask\[4\]\[16\]", "srsRAN маска")],
  "маски CRC табл. 5.3.1.1-1 совпали с srsRAN npbch.c; код HARQ-ACK — из табл. 6.3.3-1 (0 → 16 нулей, 1 → 16 единиц); размер MIB-NB 34 бита = SRSRAN_MIB_NB_LEN",
  "есть (как LTE): TBCC и турбокод LTE, CRC24A/CRC16 — область mobilnaya и проект (kod.py: циклический хвост LTE); NB-IoT-кадрирование — нет"))

# NPSS / NSSS
kus_s, s_ps = A.kusok(r"10\.2\.7\.1\.1\s*\nSequence generation", r"10\.2\.7\.1\.2", posl=True)
S_l = [int(v) for v in re.findall(r"-?1", kus_s.split("Normal")[1])][:11]
assert S_l == [1, 1, 1, 1, -1, -1, 1, 1, 1, -1, 1]
fl = [int(float(v)) for v in re.findall(r"-?\d+", open(os.path.join(KOREN, nps)).read().split("factor_lut[SRSRAN_NPSS_LEN] = {")[1].split("}")[0])]
assert fl == S_l
kus_b, s_ns = A.kusok(r"Table 10\.2\.7\.2\.1-1: Definition of", r"10\.2\.7\.2\.2", posl=True)
kus_b = re.sub(r"-\s+1", "-1", kus_b)
bq = []
for q in range(4):
    m = re.search(r"\n\s*" + str(q) + r"\s*\n\s*\[(.*?)\]", kus_b, re.S)
    bq.append([int(v) for v in re.findall(r"-?1", m.group(1))])
assert all(len(r) == 128 for r in bq)
t_nss = open(os.path.join(KOREN, nss)).read().split("b_q_m[SRSRAN_NSSS_NUM_SEQ][128] = {")[1].split("};")[0]
sr_bq = [[int(v) for v in re.findall(r"-?1", r)] for r in re.findall(r"\{([^{}]*)\}", t_nss)]
assert sr_bq == bq
def adamar(n):
    H = [[1]]
    while len(H) < n: H = [r + r for r in H] + [r + [-v for v in r] for r in H]
    return H
H128 = adamar(128); stroki = [H128.index(r) for r in bq]
json_n = tablica("nbiot_npss_nsss", {"S(l)": S_l, "b_q(m)": bq, "строки_Адамара_Сильвестра_128": stroki},
   "NB-IoT: покрывающий код NPSS S(3…13) и двоичные последовательности NSSS b_q(m), q = 0…3", [A.ist(s_ps, "10.2.7.1.1"), A.ist(s_ns, "табл. 10.2.7.2.1-1")],
   f"S(l) и b_q(m) совпали с srsRAN (npss.c factor_lut, nsss.h b_q_m); b_q — строки {stroki} матрицы Адамара — Сильвестра 128")
ZAPISI.append(Z("NB-IoT синхросигналы: NPSS — ЗЧ длины 11 (u = 5) с покрывающим кодом S(l); NSSS — ЗЧ длины 131 × строка Адамара b_q(m) × фазовый сдвиг θ_f", SL, "синхропоследовательность",
  {"NPSS": "d_l(n) = S(l)·exp(−jπun(n+1)/11), u = 5, n = 0…10, символы l = 3…13 подкадра 5", "S(l)": S_l,
   "NSSS": "d(n) = b_q(m)·e^{−j2πθ_f n}·e^{−jπun'(n'+1)/131}, u = N_ID mod 126 + 3, q = ⌊N_ID/126⌋, n' = n mod 131, m = n mod 128, θ_f = 33/132·(nf/2 mod 4); подкадр 9 чётных кадров",
   "b_q": f"строки {stroki} матрицы Адамара — Сильвестра порядка 128", "таблица": json_n},
  "NB-IoT (поиск соты)", [A.ist(s_ps, "NPSS"), A.ist(s_ns, "NSSS"), ist_kod(nps, r"factor_lut", "srsRAN NPSS"), ist_kod(nss, r"b_q_m\[SRSRAN_NSSS_NUM_SEQ\]", "srsRAN NSSS")],
  "S(l) и все 4×128 значений b_q(m) совпали с srsRAN; b_q — строки матрицы Адамара", "нет (уровень комплексной огибающей, не битовый)"))
