"""Подвижная спутниковая связь: Thuraya/GMR-1 (TS 101 376), ACeS/GMR-2 (TS 101 377), Inmarsat BGAN (TS 102 744), Aero, STD-C,
Iridium, Orbcomm, S-MIM (Eutelsat, TS 102 721), S-UMTS-G, RSM-A (Spaceway), IPoS, спутниковое цифровое радио (SDR, WorldSpace, S-DMB)."""

G1 = 'istochniki/gmr1/ts_1013760503v010201p.pdf'
G13 = 'istochniki/gmr1/ts_1013760503v030301p.pdf'
G2 = 'istochniki/gmr2/ts_1013770503v010101p.pdf'
BG = 'istochniki/inmarsat/ts_1027440201v010101p.pdf'


def zapisi(S, Z):
    z = []
    z.append(Z('GMR-1 (Thuraya) свёрточные K=5: 1/2, 1/3, 1/4, 1/5 с хвостом', 'GMR-1 (ETSI TS 101 376-5-3)', 'свёрточный',
        {'1/2': 'g0=1+D³+D⁴, g1=1+D+D²+D⁴', '1/4': 'g0=1+D³+D⁴, g1=1+D+D²+D⁴, g2=1+D²+D⁴, g3=1+D+D²+D³+D⁴', '1/3': 'g0=1+D²+D⁴, g1=1+D+D³+D⁴, g2=1+D+D²+D³+D⁴',
         '1/5': 'g0=1+D²+D⁴, g1=1+D+D³+D⁴, g2=1+D+D²+D³+D⁴, g3=1+D²+D³+D⁴, g4=1+D+D²+D⁴', 'хвост': '4 нуля', 'выкалывание': 'маски P(r;L) табл. 4.3–4.5 (напр. 1/2: P(2;3)=[011;101]→3/4)',
         'CRC': 'g8=D^8+D^7+D^4+D^3+D+1, g12=D^12+D^11+D^3+D^2+D+1, g16=D^16+D^12+D^5+1'},
        'Thuraya (GMR-1) каналы управления и данных', [S(G1, r'Rate 1/2 convolutional code of constraint length 5'), S('kod/osmo-gmr/src/l1/conv.c', r'gmr1_conv_k5_12_next_output'), S(G1, r'Table 4.3: GMR-1 puncturing masks'), S(G1, r'g12\(D\) = D12 \+ D11 \+ D3 \+ D2 \+ D \+ 1')],
        'таблицы next_output osmo-gmr (conv.c, GPL) для K=5 r=1/2,1/3,1/4,1/5 порождены ровно этими многочленами (prov_malye.py, assert)', 'ЧАСТИЧНО (свёрточные любых многочленов, выколотые)'))
    z.append(Z('GMR-1 TCH3 свёрточный K=7 r=1/2 с кольцевым хвостом', 'GMR-1 (ETSI TS 101 376-5-3)', 'свёрточный',
        {'многочлены': 'g0=1+D²+D³+D⁵+D⁶, g1=1+D+D²+D³+D⁶', 'хвост': 'tail-biting (регистр загружается последними 6 битами блока)', 'декодер': 'расширение решётки до 90 интервалов при 48 символах'},
        'речевой канал TCH3 Thuraya', [S(G1, r'circular encoding method \(tail-biting\)')], 'не проверялось вектором', 'ЕСТЬ (кольцевой хвост — svyortka.py кодировать_кольцо/витерби_кольцо)'))
    z.append(Z('GMR-1 Голей (24,12) систематический', 'GMR-1 (ETSI TS 101 376-5-3)', 'Голей',
        {'G': '12 строк hex: 800fa4 400cce 200a9d 1009f8 0806ba 0405e3 0203d6 010e69 008d55 00407f 002b33 00170f'},
        'служебные поля GMR-1', [S(G1, r'The \(24,12\) Golay code is used')], 'dmin=8 по перебору всех 4095 слов (prov_malye.py)', 'ЕСТЬ: Голей (24,12) в DMR/YSF (dmr.py, ysf.py) — матрица иная, нужно добавить'))
    z.append(Z('GMR-1 РС(15,9) над GF(16)', 'GMR-1 (ETSI TS 101 376-5-3)', 'РС',
        {'поле': 'p(X)=1+X+X^4', 'g': 'g0=α^6, g1=α^9, g2=α^6, g3=α^4, g4=α^14, g5=α^10 (табл. 4.8)', 'порядок бит': 'двоичная запись табл. 4.6 — X^0 слева'},
        'GMR-1 (данные)', [S(G1, r'systematic \(15,9\) Reed-Solomon'), S(G1, r'Table 4.8: Generator coefficients')],
        'g(X)=∏_{j=1}^{6}(X+α^j) — корни найдены (prov_malye.py)', 'ЧАСТИЧНО: rs_bch.py распознаёт РС над GF(2^m)'))
    z.append(Z('GMR-1 3G: свёрточный K=9 r=1/2, CRC-3/5 и турбокод PDCH3', 'GMR-1 3G (ETSI TS 101 376-5-3 V3)', 'свёрточный',
        {'K=9': 'g0=1+D²+D³+D⁴+D⁸, g1=1+D+D²+D³+D⁵+D⁷+D⁸', 'CRC': 'g3=1+D+D³, g5=1+D+D²+D³+D⁵', 'турбо': 'PDCH3 (CRC с начальными единицами)'},
        'Thuraya GmPRS/GMR-1 3G (IP-данные)', [S(G13, r'constraint length 9'), S(G13, r'g5\(D\) = 1 \+ D \+ D2 \+ D3 \+ D5')], 'не проверялось', 'ЧАСТИЧНО'))
    z.append(Z('GMR-2 (ACeS/Thuraya-2 ранние) свёрточные K=7 64 состояния, 1/2 и 1/4, диагональное перемежение 12×10', 'GMR-2 (ETSI TS 101 377-5-3)', 'свёрточный',
        {'1/4': 'G0=1+D²+D³+D⁴+D⁶, G1=1+D²+D³+D⁵+D⁶, G2=1+D+D⁴+D⁵+D⁶, G3=1+D+D²+D³+D⁶', '1/2 (SACCH и др.)': 'G0=1+D²+D³+D⁵+D⁶, G1=1+D+D²+D³+D⁶', 'перемежение': 'блочно-диагональное по 120-битным группам + блок 12×10'},
        'ACeS (Garuda), GMR-2', [S(G2, r'G2 = 1 \+ D \+ D4 \+ D5 \+ D6'), S(G2, r'block interleaver with 12 rows')], 'не проверялось', 'ЧАСТИЧНО'))
    z.append(Z('Inmarsat BGAN (Family SL): турбокод 16 состояний 23/35₈ с S-перемежителями по таблицам', 'Inmarsat BGAN (ETSI TS 102 744-2-1)', 'турбо PCCC',
        {'составляющие': 'SRCC обр. 23₈=1+X³+X⁴, пр. 35₈=1+X+X²+X⁴; оба с нуля; хвост 4 бита по табл. 5.11 (только в неперемеж. кодере)', 'перемежители': '266 таблиц TCI (N=156…5284) — прил. C.1 (официальное вложение)',
         'выкалывание/канальное перемежение/отображение': '266 таблиц CIPM (QPSK, 16/32/64QAM) — прил. C.2', 'скремблер': '1+X+X^15, нач. 110 1001 0101 1001 (6959h), на FEC-блок', 'внешнее': '80-мс внешний перемежитель для FR80T2.5/T5',
         'таблицы': 'istochniki/inmarsat/bgan_prilozhenie/…, сводка tablicy/inmarsat/bgan_tablicy_svodka.json'},
        'Inmarsat BGAN, FleetBroadband, SwiftBroadband (Inmarsat-4/5 L-диапазон)', [S(BG, r'The backward polynomial is 23 in octal'), S(BG, r'Table 5.11: Flush Bits'), S(BG, r'1 \+ X \+ X15')],
        'все 266 перемежителей — перестановки; все 266 таблиц CIPM — уникальные индексы, число символов = заголовку (bgan_prov.py); табл. 5.11 обнуляет регистр для всех 16 состояний (prov_malye.py)',
        'НЕТ (турбо 16 состояний и табличные перемежители)'))
    z.append(Z('Inmarsat Aero (AMS(R)S): свёрточный 171/133 K=7, перемежитель 64×N, скремблер 15 бит, UW E15AE893', 'Inmarsat Aero (ARINC 741 / ICAO Annex 10)', 'свёрточный',
        {'свёрточный': 'K=7 r=1/2, многочлены 109 и 79 (десятичные, запись libfec) = 171/133 CCSDS в обратном порядке бит', 'перемежитель': 'M=64 столбца, перестановка строк (i·27) mod 64, N строк (блоков)',
         'скремблер': '15-битный регистр, отводы 1 и 15, нач. 110100101011001 (= BGAN 6959h)', 'UW': '0xE15AE893 (32 бита) (600/1200 бит/с MSK), 52-бит пара для OQPSK 8400/10500'},
        'авиационная спутниковая связь Inmarsat Classic Aero (ACARS)', [S('kod/JAERO/JAERO/aerol.h', r'polys.push_back\(109\)'), S('kod/JAERO/JAERO/aerol.cpp', r'interleaverowpermute\[\(i\*27\)%M\]=i'), S('kod/JAERO/JAERO/aerol.h', r'int tmp\[\]=\{1,1,0,1,0,0,1,0,1,0,1,1,0,0,1,-1\}'), S('kod/JAERO/JAERO/aerol.cpp', r'3780831379LL')],
        'первоисточник (ARINC 741, ICAO SARPs AMS(R)S) закрыт/платный; скремблер JAERO = скремблер BGAN из ETSI (prov_malye.py)', 'ЧАСТИЧНО (свёрточный есть; перемежитель и скремблер — НЕТ)'))
    z.append(Z('Inmarsat-C (STD-C NCS/TDM): свёрточный K=7, перемежитель 64×162, UW 64 бита, скремблер 160 бит', 'Inmarsat-C', 'свёрточный',
        {'кадр': '64 строки × 162 символа (2 символа UW + 160 данных)', 'перестановка строк': '(i·23) mod 64', 'перемежение': 'блок 64×160', 'свёрточный': 'K=7 r=1/2 (109, 79 — как Aero)',
         'скремблер': 'таблица 160 бит на 4-байтные группы с переворотом бит (decode_utils.cpp)', 'UW': '64 бита 0000011111101010110011011101101001001110001011110010100011000010 (из Scytale-C)'},
        'Inmarsat-C, EGC SafetyNET (морская безопасность)', [S('kod/SatDump/plugins/inmarsat_support/stdc/decode_utils.cpp', r'const uint8_t syncword\[\]'), S('kod/SatDump/plugins/inmarsat_support/stdc/decode_utils.cpp', r'i \* 23\) % 64'), S('kod/SatDump/plugins/inmarsat_support/stdc/module_stdc_decoder.cpp', r'\{109, 79\}')],
        'первоисточник Inmarsat SDM закрыт; параметры — реконструкция (SatDump GPL-3, Scytale-C)', 'НЕТ'))
    z.append(Z('Iridium: BCH(31,21)/(31,20)/(7,3)/(26,21), перемежение, DQPSK, UW 0x789', 'Iridium', 'БЧХ',
        {'уникальное слово': '001100000011000011110011 (0x789 в BPSK)', 'BCH (десятичные многочлены)': 'сообщения 1897 (0x769), кольцевой вызов 1207 (0x4B7), ACCH 3545 (0xDD9), заголовок IBC 29, LCW: 29 (BCH(7,3)), 465, 41 (BCH(26,21))',
         'перемежение': 'чёт/нечет и на 3 (de_interleave/de_interleave3), таблица LCW 46 бит', 'CRC': 'IAQ CRC-16 0x15101 (>>2), IIP CRC-24', 'модуляция': 'DE-QPSK (imap [0,1,3,2])'},
        'Iridium (голос, SBD, пейджинг, ACARS)', [S('kod/iridium-toolkit/bitsparser.py', r'messaging_bch_poly=1897'), S('kod/iridium-toolkit/bitsparser.py', r'def de_interleave_lcw'), S('kod/iridium-toolkit/bitsparser.py', r'iridium_access=')],
        'первоисточник (спецификации Iridium/Motorola) закрыт; параметры — реконструкция CCC (iridium-toolkit, BSD-2)', 'НЕТ'))
    z.append(Z('Orbcomm (кадры STX): ASM A6159F 24 бита', 'Orbcomm', 'синхрослово',
        {'ASM': '0xA6159F (24 бита), допускается инверсия', 'примечание': 'проверка кадров — контрольные суммы протокола (SatDump orbcomm)'},
        'Orbcomm (низкоорбитальная передача данных)', [S('kod/SatDump/plugins/orbcomm_support/orbcomm/stx_deframer.h', r'FRM_ASM = 0xA6159F')],
        'первоисточник закрыт; по открытому коду SatDump (GPL-3)', 'НЕТ'))
    z.append(Z('S-MIM (Eutelsat, S-диапазон) обратный канал: турбо 3GPP r=1/3 + CRC-16/8 + скремблер 1+X^4+X^9', 'S-MIM (ETSI TS 102 721-3)', 'турбо PCCC',
        {'турбо': 'PCCC 3GPP (8 состояний, перемежитель 3GPP) с согласованием скорости', 'CRC': 'D^16+D^12+D^5+1 или D^8+D^7+D^4+D^3+D+1, нач. 0', 'скремблер': 'отрезок М-последовательности 1+X^4+X^9', 'расширение': 'E-SSA (асинхронный доступ с расширенным спектром)'},
        'Eutelsat S-MIM (W2A/EUTELSAT 10A S-диапазон)', [S('istochniki/smim/ts_10272103v010201p.pdf', r'1\+X4\+X9'), S('istochniki/smim/ts_10272103v010201p.pdf', r'Parallel Concatenated Convolutional Code \(PCCC\) with two 8-state')],
        'турбо = 3GPP TS 25.212 (сверено в проекте ldpc/turbo? — нет; QPP LTE есть)', 'ЧАСТИЧНО (турбо PCCC слепой, перемежитель 3GPP R99 — НЕТ)'))
    z.append(Z('S-UMTS семейство G (TS 101 851-2-1 = 25.212): CRC-24/16/12/8, свёрточный 1/2 и 1/3 K=9, турбо 3GPP', 'S-UMTS-G', 'турбо PCCC',
        {'CRC': 'gCRC24=D^24+D^23+D^6+D^5+D+1, gCRC16=D^16+D^12+D^5+1, gCRC12=D^12+D^11+D^3+D^2+D+1, gCRC8=D^8+D^7+D^4+D^3+D+1', 'коды': 'как 3GPP TS 25.212 (свёрточный K=9 r=1/2, 1/3; турбо 8 состояний)'},
        'спутниковый сегмент UMTS (Inmarsat IAI-2, S-DMB)', [S('istochniki/sumts/ts_1018510201v020101p.pdf', r'gCRC24\(D\) = D24 \+ D23 \+ D6 \+ D5 \+ D \+ 1')], 'не проверялось', 'ЧАСТИЧНО'))
    z.append(Z('RSM-A (Spaceway, Hughes): РС(244,220) t=12 + укороченный Хэмминг (12,8)', 'RSM-A (ETSI TS 102 188-3)', 'каскадный',
        {'внешний': 'RS(244,220) t=12 над GF(2^8), G(X) степени 24', 'внутренний': 'укороченный Хэмминг (12,8)', 'скремблер': 'ЛРС полезной нагрузки пакетов (байты 8…107 из 108)'},
        'Hughes Spaceway (регенеративная ячеистая сеть)', [S('istochniki/rsma/ts_10218803v010102p.pdf', r'\(244,220\) Reed-Solomon'), S('istochniki/rsma/ts_10218803v010102p.pdf', r'shortened Hamming \(12,8\)')],
        'не проверялось', 'ЧАСТИЧНО (РС распознаётся)'))
    z.append(Z('IPoS (Hughes HX/JUPITER, TIA-1008-B)', 'IPoS (ETSI TS 102 354)', 'справочно',
        {'статус': 'ETSI TS 102 354 лишь ссылается на TIA-1008-B; сам TIA-1008-B платный — коды не раскрыты'}, 'Hughes VSAT', [S('istochniki/ipos/ts_102354v010301p.pdf', r'TIA-1008-B')],
        'НЕ НАЙДЕНО открытого текста TIA-1008-B', 'НЕТ'))
    z.append(Z('SDR (спутниковое цифровое радио, ETSI TS 102 550): BCH(3057,3008) + турбо + CRC-8', 'SDR (ETSI TS 102 550/551)', 'БЧХ',
        {'BCH': 'укороченный из узкого BCH(4095,4047,9) → BCH(3056,3008,9) + общая чётность (3057,3056), dmin=10, на каждые 2 пакета TS', 'CRC-8': 'x^8+x^5+x^3+x^2+x+1 (заголовки)', 'скремблер (внутр. физ. уровень)': 'ПСП 2^11−1 (O.153), длина 2064 на CU'},
        'спутниковое радио (Ondas/Europe, проект SDR ETSI)', [S('istochniki/sdr/ts_102550v010301p.pdf', r'BCH\(4095,4047,9\)'), S('istochniki/sdr/ts_102550v010301p.pdf', r'x8 \+ x5 \+ x3 \+ x2 \+ x \+ 1'), S('istochniki/sdr/ts_10255101v010101p.pdf', r'2 047 \(211 - 1\) scrambler')],
        'не проверялось', 'ЧАСТИЧНО (БЧХ распознаётся rs_bch.py)'))
    z.append(Z('WorldSpace (Digital System DS, МСЭ-R BO.1130): РС(255,223) + блочный перемежитель + свёрточный 1/2', 'WorldSpace / BO.1130', 'каскадный',
        {'цепочка': 'канал вещания → RS(255,223) → блочный перемежитель → свёрточный r=1/2 (мобильный: 1/4 с выкалыванием, «ранний/поздний» каналы)', 'кадр': 'TDM, ПСП-слово в начале кадра перемежителя'},
        'WorldSpace (Африка/Азия), System DH (гибрид)', [S('istochniki/itu/R-REC-BO.1130-5-202602-I.pdf', r'RS \(255,223\) block coder'), S('istochniki/itu/R-REC-BO.1130-5-202602-I.pdf', r'early channel')],
        'не проверялось', 'ЧАСТИЧНО'))
    z.append(Z('S-DMB/MBSAT (Digital System E, BO.1130): РС(204,188) + байтовое свёрточное + битовое сегментированное перемежение', 'S-DMB / BO.1130', 'каскадный',
        {'внешний': 'RS(204,188), g=∏(x+λ^i) i=0..15, P(x)=x^8+x^4+x^3+x^2+1', 'перемежение': 'байтовое свёрточное + битовое свёрточное с тремя сегментами (защита от провалов >1 с)', 'модуляция': 'CDM на основе QPSK'},
        'MBSAT (Япония), S-DMB TU Media (Корея)', [S('istochniki/itu/R-REC-BO.1130-5-202602-I.pdf', r'bit-wise convolutional interleaving with three segmented')],
        'не проверялось', 'ЧАСТИЧНО (РС (204,188) есть)'))
    return z
