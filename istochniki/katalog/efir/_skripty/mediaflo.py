"""MediaFLO / FLO (TIA-1099 — платный, в открытом доступе нет): по ETSI TS 102 589 (обзор) и главе книги Gao, Chari, Chen, Ling, Walker
(Qualcomm) «MediaFLO Technology: FLO Air Interface Overview». Значения — как в этих документах, сверки с кодом нет."""
import re, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

G = Tekst("istochniki/mediaflo/SFU_GCCLK09_FLO_air_interface_overview.pdf"); E = Tekst("istochniki/mediaflo/ts_102589v010101p.pdf"); ZAPISI = []
s_rs = E.odna(r"Reed-Solomon code over the Galois Field with 256 elements")[0]
s_k = E.odna(r"value of N is fixed at 16, while the value of K can be chosen from the set")[0]
s_tr = E.odna(r"for transmitting critical overhead information, and \{1/3, 1/2, 2/3\} for transmitting MLCs")[0]
s_plp = G.odna(r"The FCS \(Frame Check Sequence\) bits")[0]
s_tu = G.odna(r"The PLPs are encoded by Turbo code derived from the Turbo codes deﬁned in the")[0]
s_sc = G.odna(r"ear feedback shift register with the generator sequence h\(D\) = D20 \+D17 \+1 is")[0]
ZAPISI.append(Z("MediaFLO (FLO, TIA-1099): внешний RS(16,K) над GF(256) для стирания (K = 8, 12, 14, 16) по пакетам суперкадра + турбокод cdma2000/EV-DO (1/5 служебный, 1/3, 1/2, 2/3), PLP 1000 бит (976 + CRC-16 CCITT + 2 резерв + 6 хвост), скремблер h(D) = D^20 + D^17 + 1 с масками слотов",
  "MediaFLO (Qualcomm FLO)", "каскадный (РС + турбо)",
  {"внешний": "RS(16,K) по столбцам: K информационных пакетов MAC + 16−K пакетов чётности; блок разносится на 4 кадра суперкадра (1 с)",
   "PLP": "1000 бит: 976 полезных + FCS 16 (g = x^16 + x^12 + x^5 + 1) + 2 резерв + 6 хвост", "внутренний": "турбокод cdma2000/1xEV-DO (см. область mobilnaya), 1/3, 1/2, 2/3; OIS — 1/5",
   "скремблер": "20-разрядный РСЛОС h(D) = D^20 + D^17 + 1, загрузка по типу канала/номеру символа OFDM (WID/LID), выход — скалярное произведение состояния на 20-битную маску слота (8 масок)",
   "оговорка": "многочлены турбокода, g(x) RS и таблицы масок — в TIA-1099 (платный); в открытом доступе не найдено"},
  "MediaFLO (США 2007–2011, Verizon/AT&T; закрыт)", [E.ist(s_rs, "RS GF(256)"), E.ist(s_k, "N = 16, K"), E.ist(s_tr, "скорости"), G.ist(s_plp, "PLP и CRC"), G.ist(s_tu, "турбо"), G.ist(s_sc, "скремблер")],
  "только по тексту двух обзорных документов (ETSI TS 102 589 и глава Qualcomm) — взаимно согласованы по RS(16,K) и скоростям; детальная сверка невозможна без TIA-1099",
  "частично: rs_bch.py (RS над GF(256)), turbo.py; FLO-кадрирование — нет"))
