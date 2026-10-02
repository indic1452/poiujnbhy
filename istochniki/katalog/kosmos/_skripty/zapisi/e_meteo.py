"""Метеоспутники: NOAA POES (HRPT/GAC), MetOp AHRPT, METEOR-M (LRPT 72k/80k, HRPT), FengYun-3 (AHRPT/MPT), GOES (LRIT/HRIT, GRB),
GK-2A (LRIT/HRIT), JPSS/NOAA-20/21 (HRD), общие спецификации CGMS (LRIT/HRIT, LRPT/AHRPT). Сверки — skripty/prov_meteo.py."""

M = 'istochniki/meteo/'
KLM = M + 'NOAA_KLM_Users_Guide_0.0_star.pdf'
CG_LR = M + 'cgms_lrit_hrit_global_spec_v2.8.pdf'
CG_AH = M + 'CGMS_LRPT_AHRPT_Global_Spec_2.01.pdf'
GLR = M + 'goes_LRIT_Transmitter-specs.pdf'
GRB, PUG = M + 'GOES-R_GRB_downlink_spec.pdf', M + 'GOES-R_PUG_GRB_vol4.pdf'
J1, J2 = M + 'JPSS1_HRD_RF_ICD_470-REF-00184_RevA.pdf', M + 'JPSS2_HRD_RF_ICD_472-00340_RevC.pdf'
GK = M + 'GK2A_LRIT_Mission_Specification_v1.0.pdf'
SD = 'kod/SatDump/'
P = SD + 'resources/pipelines/'


def zapisi(S, Z):
    z = []
    z.append(Z('NOAA POES HRPT/GAC (TIROS-N, NOAA-15…19): без ПКЗ; синхрослово 60 бит (ПСП x^6+x^5+x^2+x+1), слова 10 бит, split-phase', 'NOAA POES (KLM Users Guide)', 'синхрослово',
        {'кадр': 'малый кадр 11090 слов по 10 бит (6 слов синхро), 6 кадров/с; большой кадр — 3 малых (TIP/AMSU/заполнение)', 'синхро': '1010000100 0101101111 1101011100 0110011101 1000001111 0010010101 = первые 60 бит ПСП периода 63, g=X^6+X^5+X^2+X+1 из единичного состояния',
         'заполнение': 'слова заполнения — инверсия ПСП длины 1023, g=X^10+X^5+X^2+X+1, старт с единиц', 'код в линии': 'split-phase (манчестерский), HRPT 665,4 кбит/с; GAC/LAC воспроизведение 2,6616 Мбит/с NRZ или 1,3308 Мбит/с split-phase; TIP 8320 бит/с',
         'помехоустойчивый код': 'нет (контроль чётности TIP)'},
        'NOAA-15/18/19 HRPT 1698/1707 МГц, GAC/LAC, DSB (TIP)', [S(KLM, r'First 60 bits from 63 bit PN generator started in the all 1.s state'), S(KLM, r'665.4 kbps split phase'), S(KLM, r'X10\+X5\+X2\+X\+1'), S(SD + 'plugins/noaa_metop_support/noaa/noaa_deframer.cpp', r'HRPT_MINOR_FRAME_SYNC')],
        'синхрослово порождено генератором Галуа x^6+x^5+x^2+x+1 из 111111 (выход — старший разряд) и == SatDump 0x0A116FD719D83C95 (prov_meteo.py)', 'ЧАСТИЧНО (синхрослово — sinhro.py; манчестер в проекте убран — нужна отдельная операция)'))
    z.append(Z('MetOp AHRPT / FengYun-3 и CGMS LRPT/AHRPT: CCSDS свёрточный r=1/2 K=7 + РС(255,223) I=4 (двойной базис) + рандомизатор + ASM, CADU 1024 байта', 'CGMS LRPT/AHRPT (EUMETSAT, CMA)', 'каскадный',
        {'глобальная спецификация': 'канальный уровень — CCSDS 131.0-B (выбор кода — по миссии), кадры AOS 732.0-B, пакеты 133.0-B', 'MetOp AHRPT': 'Витерби r=1/2, CADU 1024 байта, дерандомизация CCSDS с 4-го байта, РС I=4 двойной базис (SatDump metop_ahrpt_decoder)',
         'FengYun-3 A/B/C/D/E/F AHRPT': 'QPSK: ветви I и Q декодируются ДВУМЯ отдельными декодерами Витерби r=1/2, затем дифференциальное декодирование по паре (I,Q) (FengyunDiff::work2), опция инверсии второй ветви; далее CADU 1024, рандомизатор CCSDS, РС I=4 двойной базис',
         'FY-3 MPT': 'аналогично, декодер fengyun_mpt_decoder', 'Yunhai (Others.json)': 'тот же декодер fengyun_ahrpt_decoder'},
        'MetOp-A/B/C AHRPT 1701,3 МГц, FengYun-3A…3F AHRPT/MPT, Yunhai', [S(CG_AH, r'CCSDS TM Synchronization and Channel Coding is the specification that must be used'), S(SD + 'plugins/noaa_metop_support/metop/module_metop_ahrpt_decoder.cpp', r'decode_interlaved\(&cadu\[4\], true, 4'),
                                                             S(SD + 'plugins/fengyun3_support/fengyun3/module_fengyun_ahrpt_decoder.cpp', r'diff.work2'), S(SD + 'plugins/fengyun3_support/fengyun3/diff.cpp', r'void FengyunDiff::work2'), S(P + 'FengYun-3.json', r'"fengyun3_d_ahrpt"')],
        'CGMS — ссылка на CCSDS 131.0 (сверено в a_ccsds); детали MetOp/FY — открытый код SatDump (GPL-3); EUMETSAT ASPI-0266 — скан (OCR), ICD CMA на FY-3 не опубликован', 'ЧАСТИЧНО (CCSDS каскадный есть; двойной Витерби по I/Q + дифф. декодирование FY — НЕТ)'))
    z.append(Z('METEOR-M LRPT (72k/80k): свёрточный r=1/2 K=7 + РС(255,223) I=4 (обычный базис) + рандомизатор CCSDS; 80k — свёрточный перемежитель 36×2048 с маркером 27h', 'METEOR-M (Росгидромет/ВНИИЭМ)', 'каскадный',
        {'модуляция': 'QPSK 72 кбод (M2) / OQPSK 72 кбод (M2-x), 80 кбод с перемежением', 'кадр': 'CADU 1024 байта, ASM 1ACFFC1D; рандомизатор CCSDS на 1020 байтах; РС I=4 decode_interlaved(..., false, 4) — без двойного базиса',
         'NRZ-M': 'опция diff_decode (дифференциальное декодирование после Витерби); коррелятор 0xfc4ef4fd0cc2df89 (с NRZ-M) / 0xfca2b63db00d9794 (без)',
         'перемежитель 80k': 'свёрточный, 36 ветвей, задержка 2048 × ветвь; каждые 80 символов — маркер 8 бит 27h (варианты поворота 27h/4Eh/D8h/B1h), 72 символа данных; размер = x·10/9 + 8',
         'M2-x (конвейер meteor_m2-x_lrpt)': 'ccsds_conv_concat_decoder: oqpsk, nrzm, CADU 8192 бит, РС I=4 без двойного базиса'},
        'Метеор-М №2, 2-2, 2-3, 2-4 (137,1/137,9 МГц)', [S(SD + 'plugins/meteor_support/meteor/deint.h', r'#define INTER_BRANCH_COUNT 36'), S(SD + 'plugins/meteor_support/meteor/deint.cpp', r'_syncwords\[\] = \{0x27, 0x4E, 0xD8, 0xB1\}'),
                                                        S(SD + 'plugins/meteor_support/meteor/module_meteor_lrpt_decoder.cpp', r'decode_interlaved\(&cadu\[4\], false, 4'), S(SD + 'plugins/meteor_support/meteor/module_meteor_lrpt_decoder.cpp', r'0xfc4ef4fd0cc2df89'), S(P + 'Meteor-M.json', r'"meteor_m2-x_lrpt_80k"')],
        'первоисточник (Росгидромет/ВНИИЭМ) в открытом доступе не найден; параметры — SatDump (GPL-3, перемежитель по meteor_decode dbdexter-dev)', 'ЧАСТИЧНО (свёрточный, РС, рандомизатор есть; перемежитель 36×2048 с маркерами — НЕТ)'))
    z.append(Z('METEOR-M HRPT (МСУ-МР): манчестерский код, CADU 1024 байта с ASM 1ACFFC1D, без ПКЗ', 'METEOR-M', 'синхрослово',
        {'линия': '665,4 кбит/с, фазовая модуляция (pm_demod), манчестерский код', 'кадр': 'CADU 1024 байта, ASM 1ACFFC1D (инверсия E53003E2)'},
        'Метеор-М HRPT 1700 МГц', [S(SD + 'plugins/meteor_support/meteor/module_meteor_hrpt_decoder.cpp', r'manchester'), S(SD + 'plugins/meteor_support/meteor/ccsds.h', r'CADU_ASM = 0x1ACFFC1D'), S(P + 'Meteor-M.json', r'"meteor_hrpt"')],
        'только SatDump (GPL-3)', 'ЕСТЬ (синхро ASM)'))
    z.append(Z('LRIT/HRIT (CGMS; GOES, GK-2A, Elektro-L, MSG): РС(255,223) I=4 + рандомизатор CCSDS + ASM 1ACFFC1D + свёрточный r=1/2 K=7', 'CGMS LRIT/HRIT Global Spec 2.8', 'каскадный',
        {'CVCDU': '8160 бит, рандомизация ПСП FF480EC09A… (CCSDS), ASM 1ACFFC1D', 'GOES LRIT': 'РС, перемежение, рандомизация, свёрточный код, NRZ-L BPSK (передатчик WCDA)', 'GK-2A LRIT': 'RS(255,223) I=4, CADU 1024 байта (VCDU 892 + 128 проверочных), свёрточный 1/2 K=7, NRZ-L BPSK 64 кбит/с',
         'GK-2A UHRIT': 'DVB-S2 (SatDump gk2a_uhrit_cadu_extractor)', 'Elektro-L LRIT/HRIT': 'SatDump elektro_lrit/elektro_hrit (ccsds_conv_concat_decoder)'},
        'GOES-13…15 LRIT/HRIT, GOES-R HRIT/EMWIN, GK-2A LRIT/HRIT, Электро-Л, Himawari (HimawariCast — DVB-S2)', [S(CG_LR, r'begins with \(hexadecimal\) FF480EC09A'), S(GLR, r'Convolutional coding'), S(GK, r'interleaving depth of 4'), S(GK, r'Convolution coding \(1/2, K=7\)'), S(P + 'Elektro_Arktika.json', r'"elektro_hrit"'), S(P + 'GK2A.json', r'"gk2a_uhrit"')],
        'ПСП FF480EC09A… == рандомизатор CCSDS 131.0 (randomizer_tm_255.hex, a_ccsds)', 'ЕСТЬ (CCSDS каскадный)'))
    z.append(Z('GOES-R/S/T/U GRB: DVB-S2 (8PSK 2/3 или QPSK 9/10, нормальный кадр 64800, без пилотов) с потоком CCSDS CADU', 'GOES-R GRB (NOAA)', 'каскадный БЧХ+LDPC',
        {'модуляция/код': 'основной: 8PSK LDPC 2/3 + BCH, 7,825768 Мсимв/с; резервный: QPSK 9/10 + BCH, 8,665938 Мсимв/с; кадр 64800 бит, пилоты не используются, рандомизация по EN 302 307 п. 5.2.2, SRRC α=0,25',
         'выход': '15,5 Мбит/с CADU CCSDS на каждой поляризации (RHCP/LHCP)', 'частота': '1686,6 МГц'},
        'GOES-16/17/18/19 GRB (прямое вещание)', [S(GRB, r'2/3 rate \+ BCH Outer Code'), S(GRB, r'9/10 rate \+ BCH Outer Code'), S(PUG, r'GRB'), S(P + 'GOES.json', r'"goes_grb"')],
        'параметры DVB-S2 — как в EN 302 307-1 (записи b_tv; LDPC DVB-S2 — ldpc_std.py проекта)', 'ЕСТЬ (DVB-S2 LDPC/BCH/BBFRAME — dvbs2.py, ldpc_std.py)'))
    z.append(Z('JPSS HRD: NOAA-20 — RS(255,223) I=4 + свёрточный 1/2 (G2 инверт.) QPSK 15 Мбит/с; NOAA-21 (J2) — RS I=5, NRZ-M, свёрточный 1/2, OQPSK 25 Мсимв/с', 'JPSS (NOAA/NASA HRD RF ICD)', 'каскадный',
        {'JPSS-1 (NOAA-20)': 'RS(255,223) I=4 (CCSDS 131.0-B-2), рандомизатор, ASM, свёрточный r=1/2 K=7: G1 → I, G2 (инвертированный) → Q, 7812 МГц',
         'JPSS-2 (NOAA-21)': '7812 МГц, 25 Мсимв/с CADU, NRZ-M, (255,223) RS I=5, r=1/2 K=7, OQPSK', 'Suomi NPP': 'SatDump npp_hrd (ccsds_conv_concat_decoder)'},
        'Suomi-NPP, NOAA-20, NOAA-21 HRD (X-диапазон)', [S(J1, r'standard CCSDS 255,223 with interleave depth 4'), S(J1, r'G2 \(inverted\) to the Q channel'), S(J2, r'NRZ-M \(255,223\) RS, I=5'), S(P + 'JPSS.json', r'"jpss_hrd"')],
        'по двум ICD; профили SatDump jpss_hrd/npp_hrd совпадают по I и CADU (konveiery.json)', 'ЕСТЬ (CCSDS каскадный; NRZ-M — lineynye/дифференциальное)'))
    return z
