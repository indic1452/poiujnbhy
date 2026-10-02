"""Рекомендации МСЭ-R (открытые): BS.1194 (FM-поднесущие: STIC), BT.2016/BT.1833 (AT-DMB), BT.1877 (DTMB-A, ISDB-T3 «Advanced ISDB-T»).
Для DTMB-A и ISDB-T3 МСЭ даёт только сводку — матрицы LDPC в открытом доступе не найдены."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

B = Tekst("istochniki/itu/R-REC-BS.1194-2-199812-I.pdf"); T2016 = Tekst("istochniki/itu/R-REC-BT.2016-4-202502-I.pdf")
T1833 = Tekst("istochniki/itu/R-REC-BT.1833-5-202305-I.pdf"); T1877 = Tekst("istochniki/itu/R-REC-BT.1877-4-202606-I.pdf"); ZAPISI = []

s_rs, _, _ = B.odna(r"The message is block encoded using a \(243, 228\) shortened Reed Solomon 256-ary code")
s_bi = B.odna(r"The Reed Solomon coded message is block interleaved by writing 8-bit bytes to a memory with 243 rows and 6")[0]
s_cc, _, l_cc = B.odna(r"polynomial coefficients 554 and 744 \(octal\)")
s_ci = B.odna(r"The encoded message is interleaved using a convolutional interleaver with 72 different paths")[0]
s_bch = B.odna(r"number using a Bose, Chaudhuri and Hocquenghem \(BCH\) \(15, 7\) code")[0]
s_pn = B.odna(r"The interleaved message is exclusive-OR'ed with a repeating Pseudo-Noise \(PN\) random pattern")[0]
s_t8 = B.odna(r"# stages in m-sequence")[0]
# 554 и 744 (восьм.) — 9-разрядная запись с выравниванием влево: сдвиг на 2 даёт 7-разрядные 133 и 171
assert 0o554 >> 2 == 0o133 and 0o744 >> 2 == 0o171 and (0o554 & 3) == 0 and (0o744 & 3) == 0
assert dfree([0o133, 0o171], 7) == 10
ZAPISI.append(Z("STIC (FM-поднесущая 72,2 кГц): RS(243,228) над GF(256) + блочный перемежитель 243×6 + свёрточный 1/2 K=7 «554, 744» (= 133, 171) + свёрточный перемежитель 72 ветви + ПСП-покрытие; номер кадра — БЧХ(15,7)", "FM-поднесущие данных (ITU-R BS.1194, System C STIC)", "каскадный (РС + свёрточный)",
  {"РС": "укороченный RS(243,228) над GF(256), исправляет 7, обнаруживает 8 символов (заменяет CRC); пакет 228 байт / 240 мс", "блочный перемежитель": "243 строки × 6 столбцов байтов, запись по столбцам, чтение по строкам",
   "свёрточный": "1/2, K = 7, «554 и 744 (восьм.)» — 9-разрядная запись с двумя нулями справа = 133/171; кодер без сброса", "свёрточный перемежитель": "72 ветви, ветвь i — (71−i)·J разрядов, J = 18/36/72/144 (табл. 8)",
   "покрытие": "XOR с m-последовательностью 17…20 разрядов (длина 93 312…746 496 бит), синхронной с суперкадром (многочлен в рекомендации не приведён)",
   "кадр": "синхроподкадр: 56-битное уникальное слово + БЧХ(15,7) номера кадра + 1 + 4 бита состояния канала; 72 бита данных в подкадре; D8PSK 9 025 симв/с"},
  "FM-радиовещание (США, 1990-е: данные на поднесущей)", [B.ist(s_rs, "RS(243,228)"), B.ist(s_bi, "блочный перемежитель"), B.ist(s_cc, "свёрточный"), B.ist(s_ci, "свёрточный перемежитель"), B.ist(s_pn, "покрытие"), B.ist(s_bch, "БЧХ(15,7)"), B.ist(s_t8, "табл. 8")],
  "554₈ >> 2 = 133₈, 744₈ >> 2 = 171₈ (запись с выравниванием влево), dfree = 10; остальное — по тексту рекомендации (второго источника нет)",
  "частично: rs_bch.py (RS над GF(256)), kod.СВЁРТОЧНЫЕ (133/171), forni.py (свёрточное перемежение)"))

s_at = T2016.odna(r"Convolutional code,\s*$|AT-DMB:\s*$")[0]
s_at2 = T2016.odna(r"enhancement layer, turbo code is applied|order to improve channel error correction capability in the enhancement layer, turbo code is applied")[0]
s_at3 = T1833.odna(r"AT-DMB system is an enhancement of T-DMB system to increase channel capacity of T-DMB and is completely backward")[0]
ZAPISI.append(Z("AT-DMB (Корея): иерархическая модуляция поверх T-DMB — базовый слой как T-DMB (свёрточный DAB), слой улучшения — турбокод 1/2, 2/5, 1/3, 1/4 (TTAK.KO-07.0070/R2)", "T-DMB / AT-DMB (ITU-R BS.1114 System A)", "турбо PCCC",
  {"базовый слой": "свёрточный DAB 1/4…3/4 + RS(204,188) + Форни 12 (как T-DMB)", "слой улучшения": "турбокод 1/2, 2/5, 1/3, 1/4; BPSK или QPSK поверх DQPSK, отношение созвездий 1,5…3,0",
   "оговорка": "многочлены и перемежитель турбокода — в TTAK.KO-07.0070/R2 (TTA, в открытом доступе не найден)"},
  "AT-DMB (Корея)", [T2016.ist(s_at, "табл. 1A"), T2016.ist(s_at2, "прил. 1 A.3"), T1833.ist(s_at3, "BT.1833")], "по двум рекомендациям МСЭ-R; детали турбокода не проверены — первоисточник TTA недоступен", "частично: turbo.py"))

s_da = T1877.odna(r"LDPC/BCH code with block size of 61 440 or 15 360 bits and code rates of")[0]
s_da2 = T1877.odna(r"coded with punctured\s*$|2/3 15360 LDPC for OFDM")[0]
ZAPISI.append(Z("DTMB-A (КНР, 2-е поколение): LDPC/BCH 61 440 и 15 360 бит, R = 1/2, 2/3, 5/6; сигнализация — выколотый 2/3 15 360; APSK до 256", "DTMB-A (ITU-R BT.1877 прил. 3)", "LDPC",
  {"LDPC": "n = 61 440 / 15 360, R = 1/2, 2/3, 5/6 + внешний BCH", "связь": "длина 15 360 совпадает с кодами ABS-S (КНР, в проекте: abs-s-15360-*) — тождество не проверено",
   "оговорка": "стандарт GB/T (DTMB-A) в открытом доступе не найден; МСЭ даёт только сводку"},
  "ТВ-вещание КНР (DTMB-A)", [T1877.ist(s_da, "табл. 3 п. 12"), T1877.ist(s_da2, "п. 19")], "только сводка МСЭ-R", "частично: ldpc_kitay.py (ABS-S 15360 — возможно тот же класс кодов)"))

s_i3 = T1877.odna(r"Inner code: LDPC code with block size of 69 120 \(Normal\) or 17 280 \(Short\)")[0]
ZAPISI.append(Z("ISDB-T3 / Advanced ISDB-T (Япония): LDPC 69 120 (Normal) / 17 280 (Short), R = 2/16…14/16 + BCH; TMCC — LDPC; NUC до 4096-QAM", "Advanced ISDB-T (ITU-R BT.1877)", "LDPC",
  {"LDPC": "n = 69 120 / 17 280, 13 скоростей 2/16…14/16, внешний BCH; групповое и блочное битовое перемежение", "оговорка": "спецификация ARIB (Advanced ISDB-T) в открытом доступе с таблицами не найдена"},
  "ТВ-вещание Японии нового поколения (испытания)", [T1877.ist(s_i3, "Advanced ISDB-T п. 16")], "только сводка МСЭ-R", "нет"))
