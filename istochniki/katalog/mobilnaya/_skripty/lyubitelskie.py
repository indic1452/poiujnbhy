"""Любительские/коммерческие КВ протоколы: PACTOR-I (CRC-16, memory-ARQ, Хаффман), G-TOR (гибридный ARQ тип II на (24,12) Голее, CRC-16),
CLOVER (RS-блоки, 4 тона). PACTOR-II/III и CLOVER-2000 в части ITU-R M.1798 — см. записи morskaya."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def rec(imya, sem, vid, par, gde, ist, prov, sl):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})
P = Tekst("istochniki/archive_org/CNC1992-Pactor-W1BEL.pdf")
s1 = P.odna(r"Data blocks have CRC-16 checking as is")[0]; s2 = P.odna(r"8 bit ASCII and Huffman compressed 7 bit")[0]; s3 = P.odna(r"CRC-16 error checking and Memory-ARQ")[0]
rec("PACTOR-I (SCS): блоки с CRC-16 (CCITT, как AX.25), memory-ARQ, сжатие Хаффмана", "КВ ARQ: PACTOR (SCS)", "CRC-подобный",
    {"блок": "заголовок (00, 55, AA, FF — счётчик), данные 8 бит ASCII или Хаффман 7 бит, статусный байт, CRC-16", "скорость": "FSK 100/200 Бод, длительность цикла 1,25 с",
     "memory-ARQ": "накопление (суммирование) повторов с мягкими решениями до прохождения CRC"},
    "любительская и морская КВ связь (Winlink, ранний)", [P.ist(s1, "CRC-16"), P.ist(s2, "Хаффман"), P.ist(s3, "memory-ARQ")],
    "только обзор (QEX/CNC 1992, OCR); спецификация SCS PACTOR-I опубликована SCS, но не скачана — многочлен CRC-16 указан как в AX.25 (x16+x12+x5+1)", "CRC-16/X-25 есть (hdlc.py); кадра нет (просто при наличии спецификации)")
G = Tekst("istochniki/lyubitelskie/DCC1994_GTOR.pdf", ocr=True)
s4 = G.odna(r"properties of the \(24,12\) extended Golay forward")[0]; s5 = G.odna(r"invertible\. An invertible code is one in which the")[0]; s6 = G.odna(r"byte cyclic redundancy check \(CRC\) code\. The")[0]
s7 = G.odna(r"hybrid ARQ protocol employs forward error using interleaving")[0]
rec("G-TOR (Kantronics): гибридный ARQ тип II на расширенном Голее (24,12) + CRC-16 + перемежение, Хаффман", "КВ ARQ: G-TOR", "Голей",
    {"код": "расширенный Голей (24,12), обратимый (из проверочных восстанавливаются данные): 1-я передача — данные+CRC, по NACK — только проверочные",
     "CRC": "двухбайтный CRC кадра", "перемежение": "битовое по кадру (рандомизация пакетов ошибок)", "скорость": "FSK 100/200/300 Бод (адаптивно)", "сжатие": "Хаффман, RLE"},
    "любительская КВ связь (1994–2000-е)", [G.ist(s4, "Голей (24,12)"), G.ist(s5, "обратимость"), G.ist(s6, "CRC"), G.ist(s7, "перемежение")],
    "по статье ARRL DCC 1994 (OCR); многочлен Голея и схема перемежения не приведены", "Голей (24,12) есть (dmr/p25) — кадр G-TOR нет (средне)")
C = Tekst("istochniki/lyubitelskie/CNC1990_Cloverleaf_W7GHM.pdf", ocr=True)
s8 = C.odna(r"The Clover design uses Reed-Solomon error-control")[0]; s9 = C.odna(r"from two to sixteen distinct phase levels and up to four")[0]; s10 = C.odna(r"The data is encoded into Reed-Solomon error-correcting")[0]
rec("CLOVER (HAL, Rayfield Wickwire W7GHM): Рид — Соломон по блокам + 4 импульсных тона, ФМ/АФМ 1–6 бит на импульс", "КВ ARQ: CLOVER", "РС",
    {"код": "RS (байтовый) с адаптивной избыточностью по оценке канала; зазор в конце блока — оценка ОСШ", "модуляция": "4 последовательных тона-импульса (31,25 Бод на тон), BPSK…16PSK, 8P2A, 16P4A; ИФМ при плохом канале"},
    "любительская/коммерческая КВ связь (CLOVER-II, CLOVER-2000)", [C.ist(s8, "RS"), C.ist(s9, "модуляция"), C.ist(s10, "RS блоки")],
    "только обзор (ARRL CNC 1990, OCR); параметры RS (n,k, поле) в открытых источниках не найдены", "RS есть (rs_bch.py); параметры неизвестны — подбор по записи")
