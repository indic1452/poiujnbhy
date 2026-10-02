"""Навигационные сообщения ГНСС: GPS (LNAV, CNAV L2C/L5, CNAV-2 L1C), ГЛОНАСС (FDMA и CDMA L1OC/L3OC), Galileo (I/NAV, F/NAV,
FEC2 RS, HAS RS), BeiDou (D1/D2 B1I/B3I, B-CNAV1/2/3, PPP-B2b, BDSBAS, SAR), QZSS (L1S, L6), NavIC/IRNSS (L5/S, L1), SBAS.
Сверки: skripty/nav_prov.py (_itog_nav.json) и skripty/nav_prov2.py (_itog_nav2.json)."""

N = 'istochniki/nav/'
GPS200, GPS705, GPS800 = N + 'IS-GPS-200N.pdf', N + 'IS-GPS-705J.pdf', N + 'IS-GPS-800J.pdf'
GAL, HAS = N + 'Galileo_OS_SIS_ICD_v2.1.pdf', N + 'Galileo_HAS_SIS_ICD_v1.0.pdf'
GLO = N + 'ICD_GLONASS_5.1_2008_en.pdf'
B1I2, B1I3, B3I = N + 'BDS-SIS-ICD-B1I-2.0_unb.pdf', N + 'bds/BDS_B1I_v3.0.pdf', N + 'bds/BDS_B3I_v1.0.pdf'
B1C, B2A, B2B, PPP = N + 'bds/BDS_B1C_v1.0.pdf', N + 'bds/BDS_B2a_v1.0.pdf', N + 'bds/BDS_B2b_v1.0.pdf', N + 'bds/BDS_PPP_B2b_v1.0.pdf'
BSBAS, BSAR = N + 'bds/BDS_BDSBAS_B1C_v1.0.pdf', N + 'bds/BDS_SAR_v1.0.pdf'
QL1S, QL6, QPNT = N + 'IS-QZSS-L1S.pdf', N + 'is-qzss-l6-008.pdf', N + 'is-qzss-pnt-006.pdf'
IRN, NVL1 = N + 'IRNSS_SPS_ICD_v1.1_2017.pdf', N + 'NavIC_SPS_ICD_L1_final.pdf'
PS = 'kod/PocketSDR/python/sdr_nav.py'
T = 'tablicy/nav/'


def zapisi(S, Z):
    z = []
    z.append(Z('GPS LNAV (L1 C/A): укороченный Хэмминг (32,26) с переносом D29*/D30*', 'GPS (IS-GPS-200)', 'Хэмминг',
        {'слово': '30 бит: 24 данных + 6 проверочных D25..D30; 10 слов в подкадре, 5 подкадров по 300 бит, 50 бит/с',
         'проверки': 'табл. 20-XIV: D25..D30 = суммы d1..d24 и D29*, D30* предыдущего слова; при D30*=1 биты данных передаются инвертированными',
         'преамбула TLM': '10001011 (8Bh)', 'маски (сверено)': 'PocketSDR test_LNAV_parity 0x2EC7CD2, 0x1763E69, 0x2BB1F34, 0x15D8F9A, 0x1AEC7CD, 0x22DEA27',
         'порядок бит': 'старший первым'},
        'GPS L1 C/A (и QZSS L1C/A)', [S(GPS200, r'Table 20-XIV'), S('kod/PocketSDR/python/sdr_nav.py', r'mask = \(0x2EC7CD2')],
        'tablicy/nav/_itog_nav.json: gps_lnav_parity — 6 уравнений табл. 20-XIV == маски PocketSDR (BSD-2)', 'ЧАСТИЧНО (kod.py находит блочный код (30,24) по рангу; перенос D30* — НЕТ)'))
    z.append(Z('GPS CNAV (L2C, L5) и LNAV-L2: свёрточный K=7 r=1/2 171/133 + CRC-24Q', 'GPS (IS-GPS-200/705)', 'свёрточный',
        {'свёрточный': 'K=7, G1=171₈, G2=133₈, G1 первым, кодирование непрерывное (регистр не обнуляется между сообщениями)', 'сообщение': '300 бит за 12 с (L2C 25 бит/с→50 сим/с) / 6 с (L5 50→100 сим/с), преамбула 8Bh',
         'CRC': 'CRC-24Q g(X)=(1+X)p(X) = 0x1864CFB, init 0 (reveng CRC-24/LTE-A)', 'L5': 'поверх символов — код Неймана — Хофмана NH10 = 0000110101 (I5), NH20 на Q5'},
        'GPS L2C (CM), L5 (I5); QZSS L2C/L5', [S(GPS200, r'G1 \(171 OCTAL\) G2 \(133 OCTAL\)'), S(GPS705, r'constraint length 7'), S(GPS705, r'Neuman-Hofman code is defined to be 0000110101'), S(GPS200, r'cyclic redundancy check \(CRC\)')],
        'CRC-24Q: check("123456789")=0xCDE703 (nav_prov.py, == reveng); 171/133 — тот же код, что в CCSDS/DVB-S (без инверсии)', 'ЕСТЬ (svyortka.py 171/133, crc_katalog CRC-24/LTE-A)'))
    z.append(Z('GPS CNAV-2 (L1C): BCH(51,8) TOI + CRC-24Q + LDPC (1200,600)/(548,274) + перемежитель 38×46', 'GPS (IS-GPS-800)', 'LDPC',
        {'подкадр 1': '9 бит TOI → 52 символа: BCH(51,8) (генератор — регистр 8 разрядов, рис. 3.2-4) + старший бит TOI добавляется поверх (XOR) ', 'подкадр 2': '576 бит + CRC-24Q = 600 → LDPC r=1/2 → 1200',
         'подкадр 3': '250 бит + CRC-24Q = 274 → LDPC r=1/2 → 548', 'LDPC': 'H = [A B T; C D E], подматрицы табл. 6.2-2…6.2-13; T нижнетреугольная (кодирование по методу Ричардсона — Урбанке)',
         'перемежитель': 'блочный 38 строк × 46 столбцов для 1748 символов: запись по строкам (слева направо, SF2 затем SF3), чтение по столбцам сверху вниз', 'кадр': '1800 символов за 18 с (100 сим/с)',
         'таблицы': T + 'ldpc_gps_cnav2_sf2.json, ' + T + 'ldpc_gps_cnav2_sf3.json'},
        'GPS L1C (Block III), QZSS L1C', [S(GPS800, r'9-bit TOI data shall be encoded into 52-symbol code'), S(GPS800, r'two-dimensional array of 38 rows and 46 columns'), S(GPS800, r'Parity Check Matrix H for LDPC Code')],
        'матрицы из ICD == PocketSDR H_CNV2_* (мультимножества координат), g=1 (nav_prov.py)', 'ЧАСТИЧНО (ldpc.py — загрузка H; готовой матрицы нет)'))
    z.append(Z('ГЛОНАСС (FDMA L1/L2 СТ): код Хэмминга (85,77) с дополнительной проверкой, относительный код, метка времени 30 бит', 'ГЛОНАСС (ИКД 5.1)', 'Хэмминг',
        {'строка': '2 с: 1,7 с данных (85 бит, биты 9..85 информация, 1..8 — проверочные C1..C8) + 0,3 с метка времени', 'проверки': 'C1..C7 по наборам i,j,k,l,m,n,p табл. 4.13; C8 — общая чётность; правила исправления одиночной ошибки (icor = C7..C1 + 8 − K)',
         'кодирование в линии': 'относительный (дифференциальный) код + меандр 100 Гц (bi-binary, 10 мс)', 'метка времени': '30 бит 111110001101110101000010010110 — укороченная ПСП 1+x^3+x^5', 'скорость': '50 бит/с'},
        'ГЛОНАСС L1OF/L2OF (СТ-код)', [S(GLO, r'Hamming code'), S(GLO, r'g\(x\) = 1 \+ x3 \+ x5, or may be shown as 111110001101110101000010010110'), S('kod/gnss-sdr/src/core/system_parameters/GLONASS_L1_L2_CA.h', r'GLONASS_GNAV_CRC_I_INDEX')],
        'табл. 4.13 == gnss-sdr GLONASS_GNAV_CRC_*_INDEX; синдромы 77 бит различны; метка времени порождена 1+x^3+x^5 (nav_prov.py)', 'НЕТ (нужна запись: код (85,77)+C8)'))
    z.append(Z('ГЛОНАСС CDMA L1OC/L3OC: свёрточный K=7 (0x4F/0x6D, ветви переставлены), CRC-16 0x6F63 (L1OC), CRC-24 (L3OC)', 'ГЛОНАСС CDMA (ИКД L1/L3 ред. 1.0 2016)', 'свёрточный',
        {'L1OC': 'строка 250 бит, преамбула 12 бит 010111110001, 552 символа на строку (262+8 после декодирования), CRC(250,234) многочлен 0x6F63 (x^16+x^14+x^13+x^11+x^10+x^9+x^8+x^6+x^5+x+1)',
         'L3OC': 'строка 300 бит, преамбула 20 бит 00000100100101001110, 668 символов, проверка CRC-24 по PocketSDR', 'свёрточный': 'K=7, r=1/2, многочлены 0x4F, 0x6D (запись PocketSDR; = 117₈/155₈ — зеркально 171/133), порядок ветвей G2,G1 (swap_syms)',
         'L3OC по ИКД (проект ред. 1.0 2015, фрагмент 13 стр.)': 'СК (133,171), кодовое ограничение 7, r=1/2 (первым идёт 133 — отсюда перестановка ветвей относительно GPS), ЦИ 100 бит/с → 200 симв/с; оверлейные коды: Баркер 5 символов 00010 на символах СК, Ньюман — Хофман 0000110101 на символах ЦИ; циклические коды (300,276), (200,176), (400,376) — разделы 4.4–4.6 (в доступном фрагменте отсутствуют)',
         'статус первоисточника': 'окончательные ИКД CDMA 2016 (russianspacesystems.ru) — сайт недоступен из сети анализа (ошибка TLS), архив web.archive.org отвечает 429; взят проект ИКД L3OC 2015 с srns.ru (13 стр.) + открытый код PocketSDR (BSD-2)'},
        'ГЛОНАСС-К/К2, ГЛОНАСС-М (L3OC)', [S(PS, r'preamb = \(0, 1, 0, 1, 1, 1, 1, 1, 0, 0, 0, 1\)'), S(PS, r'0x6F63'), S(PS, r'preamb = \(0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 1, 1, 0\)'), S('kod/PocketSDR/python/sdr_fec.py', r'POLY_CONV = \(0x4F, 0x6D\)'), S(N + 'glonass/IKD_GLONASS_L3OC_proekt_2015_srns.pdf', r'сверточное кодирование \(133,171\)'), S(N + 'glonass/IKD_GLONASS_L3OC_proekt_2015_srns.pdf', r'00010 \(код Баркера\)')],
        'свёрточный (133,171) K=7 по проекту ИКД L3OC == PocketSDR (0x4F/0x6D — зеркальная запись 171/133, ветви переставлены: 133 первым); CRC — только по PocketSDR', 'ЧАСТИЧНО (свёрточный K=7 есть)'))
    z.append(Z('Galileo I/NAV и F/NAV: свёрточный K=7 171/133 (инверсия G2) + блочный перемежитель + CRC-24Q, синхрошаблоны', 'Galileo (OS SIS ICD 2.1)', 'свёрточный',
        {'свёрточный': 'K=7, G1=171₈, G2=133₈, вторая ветвь инвертируется, порядок G1,G2, без хвоста внутри страницы (хвост 6 нулей в странице)', 'перемежитель': 'запись по столбцам, чтение по строкам: I/NAV 30 столбцов × 8 строк (240 символов), F/NAV 61×8 (488)',
         'синхро': 'I/NAV 0101100000 (10 бит), F/NAV 101101110000 (12 бит)', 'CRC': 'CRC-24Q на страницу', 'страница': 'I/NAV (E1-B, E5b-I) — 250 символов/1 с (чётная+нечётная половины), F/NAV (E5a-I) — 500 символов/10 с'},
        'Galileo E1-B, E5a-I, E5b-I', [S(GAL, r'pattern is 0101100000'), S(GAL, r'pattern is: 101101110000'), S(GAL, r'Convolutional'), S('kod/gnss-sdr/src/core/system_parameters/Galileo_INAV.h', r'GALILEO_INAV_PREAMBLE')],
        'прил. D: кодер воспроизводит пример бит в бит; перемежитель совпал для 61×8 и 30×8; синхро == gnss-sdr (nav_prov.py)', 'ЧАСТИЧНО (свёрточный с инверсией ветви — svyortka.py; перемежитель — peremezhenie.py)'))
    z.append(Z('Galileo I/NAV FEC2: Рид — Соломон (118,58) укороченный из (255,195), GF(256) p=0x11D', 'Galileo (OS SIS ICD 2.1)', 'РС',
        {'код': 'RS(118,58,61) — 60 проверочных октетов, корни α^1…α^60, p(x)=x^8+x^4+x^3+x^2+1', 'назначение': 'внешний код данных эфемерид/часов (слова 1–4 I/NAV), передаётся в новых словах 17–20',
         'g(x)': 'табл. 108 (61 коэффициент)', 'ISM CRC-32': 'G_ISM = 0x814141AB (CRC-32/AIXM) — пример табл. 109→110'},
        'Galileo E1-B I/NAV (улучшенный, с 2022+)', [S(GAL, r'Reed-Solomon'), S(GAL, r'Table 108')],
        'g(x)=∏(x−α^i), i=1..60 == табл. 108; ISM CRC пример 0xB2F4D6AF — совпал (nav_prov.py)', 'ЧАСТИЧНО (rs_bch.py находит РС над GF(256) вслепую)'))
    z.append(Z('Galileo HAS (E6-B): Рид — Соломон (255,32,224) по страницам (стирания), GF(256) p=0x11D', 'Galileo HAS (HAS SIS ICD 1.0)', 'РС',
        {'код': 'систематический RS(255,32) над GF(256), g(x)=∏_{i=1}^{223}(x−α^i), p(α)=α^8+α^4+α^3+α^2+1', 'схема': 'сообщение k=MS страниц × 53 октета; каждый столбец (k октетов, дополнен нулями до 32) кодируется вертикально → 255 страниц; PID — номер строки G',
         'декодирование': 'любые k страниц: m = D⁻¹·w (D — строки G по PID, первые k столбцов)', 'таблица': T + 'rs_galileo_has_255_32_g.json'},
        'Galileo HAS (высокоточное позиционирование, E6-B C/NAV)', [S(HAS, r'Octet Representation \(Integer\) of the Coefficients of the Generator Polynomial'), S(HAS, r'Reed-Solomon Decoding Example')],
        'ТЕСТОВЫЙ ВЕКТОР: табл. 42 (224 коэффициента) == ∏(x−α^i); матрица D прил. C (PID 55…253), построенная по нашей G, == напечатанной, D·m’1 == w’1 (nav_prov2.py)', 'НЕТ (декодер стираний по строкам G)'))
    z.append(Z('BeiDou D1/D2 (B1I, B3I, B2I): BCH(15,11) g=X^4+X+1 + перемежение по 2 слова, NH20, преамбула 11100010010', 'BeiDou (B1I/B3I ICD)', 'БЧХ',
        {'слово': '30 бит: первое слово — 15 бит без кодирования + 1 BCH(15,11); остальные — 2×BCH(15,11) с поразрядным чередованием (перемежение на 2)', 'g': 'g(X)=X^4+X+1 (исправляет 1 ошибку)',
         'преамбула': '11100010010 (11 бит)', 'NH': 'код Неймана — Хофмана 20 бит (00000100110101001110) на D1 (МЕО/IGSO), 50 бит/с', 'D2': '500 бит/с (GEO) без NH'},
        'BeiDou-2/3 B1I, B3I, B2I', [S(B1I2, r'g\(X\)=X4\+X\+1'), S(B1I3, r'BCH\(15,11,1\)'), S(B3I, r'BCH\(15,11,1\)'), S('kod/gnss-sdr/src/core/system_parameters/Beidou_DNAV.h', r'BEIDOU_DNAV_PREAMBLE')],
        '15 синдромов различны и ненулевые (исправление 1 ошибки); преамбула == gnss-sdr (nav_prov.py)', 'ЧАСТИЧНО (БЧХ вслепую — rs_bch.py; перемежение на 2 — peremezhenie.py)'))
    z.append(Z('BeiDou B-CNAV1 (B1C): 64-ичный LDPC (200,100) и (88,44) над GF(64) + BCH(21,6)/(51,8) + перемежитель 36×48', 'BeiDou (B1C ICD 1.0)', 'LDPC',
        {'подкадр 1': '14 бит (PRN 6 + SOH 8): BCH(21,6) — генератор 6 разрядов g=x^6+x^4+x^2+x+1, BCH(51,8) — 8 разрядов g=x^8+x^7+x^4+x^3+x^2+x+1 (отрезки m-последовательностей; декодирование корреляцией) → 72 символа',
         'подкадр 2': '600 бит (576 + CRC-24Q) → 64-ичный LDPC(200,100): 100 символов по 6 бит → 1200 символов', 'подкадр 3': '264 бита → LDPC(88,44) → 528 символов',
         'поле': 'GF(2^6), p(x)=1+x+x^6, символ = 6 бит старшим вперёд', 'H': 'по 4 ненулевых элемента в строке, веса столбцов 2; индексы и элементы — ' + T + 'ldpc_bds_b1c_sf2_200_100.json, ' + T + 'ldpc_bds_b1c_sf3_88_44.json',
         'перемежитель': 'блочный 36 строк × 48 столбцов для 1728 символов подкадров 2 и 3', 'кадр': '1800 символов / 18 с'},
        'BeiDou-3 B1C (данные B1C_data)', [S(B1C, r'Table 6-1 The generator polynomials of BCH encoders'), S(B1C, r'64-ary LDPC\(200, ?100\)'), S(B1C, r'block interleaver'), S('kod/PocketSDR/src/sdr_ldpc.c', r'H_BCNV1_SF2_idx')],
        'H (index и element) из текста ICD == PocketSDR H_BCNV1_SF2/SF3 (все строки); GF(64) p=1+x+x^6 порождает таблицу PocketSDR GF_VEC (nav_prov2.py)', 'НЕТ (недвоичный LDPC)'))
    z.append(Z('BeiDou B-CNAV2 (B2a): 64-ичный LDPC (96,48) + CRC-24Q, преамбула E24DE8h', 'BeiDou (B2a ICD 1.0)', 'LDPC',
        {'кадр': 'преамбула 24 символа 0xE24DE8 (старшим вперёд) + 288 бит (PRN 6 + тип 6 + SOW 18 + данные + CRC-24Q) → LDPC(96,48) над GF(64) → 576 символов; 600 символов / 3 с', 'H': T + 'ldpc_bds_b2a_96_48.json',
         'CRC': 'CRC-24Q'},
        'BeiDou-3 B2a', [S(B2A, r'64-ary LDPC\(96, ?48\)'), S(B2A, r'value of 0xE24DE8'), S('kod/PocketSDR/src/sdr_ldpc.c', r'H_BCNV2_idx')],
        'H(48×96) из ICD == PocketSDR H_BCNV2 (nav_prov2.py)', 'НЕТ'))
    z.append(Z('BeiDou B-CNAV3 (B2b) и PPP-B2b: 64-ичный LDPC (162,81) + CRC-24Q', 'BeiDou (B2b ICD 1.0, PPP-B2b ICD 1.0)', 'LDPC',
        {'кадр': 'преамбула 16 символов 0xEB90 + PRN 6 бит + служебные; 486 бит (тип 6 + SOW 20 + данные 436 + CRC-24Q 24) → LDPC(162,81) над GF(64) → 972 символа; кадр 1000 символов / 1 с', 'H': T + 'ldpc_bds_b2b_162_81.json (PPP-B2b — та же матрица)',
         'BDS SAR (обратный канал RLM)': 'тоже B-CNAV3 LDPC(162,81)'},
        'BeiDou-3 B2b (открытая служба), PPP-B2b (высокоточная коррекция), SAR RLM', [S(B2B, r'64-ary LDPC\(162, ?81\)'), S(B2B, r'value of 0xEB90'), S(PPP, r'64-ary LDPC\(162, ?81\)'), S(BSAR, r'64-ary LDPC \(162, 81\)'), S('kod/PocketSDR/src/sdr_ldpc.c', r'H_BCNV3_idx')],
        'H(81×162) B2b == PPP-B2b == PocketSDR H_BCNV3 (nav_prov2.py)', 'НЕТ'))
    z.append(Z('SBAS L1 (WAAS/EGNOS/MSAS/GAGAN, QZSS L1S, BDSBAS-B1C): свёрточный K=7 171/133 + CRC-24Q, преамбулы 53h/9Ah/C6h', 'SBAS (RTCA DO-229 через IS-QZSS-L1S, BDSBAS ICD)', 'свёрточный',
        {'сообщение': '250 бит/с: 8 преамбула + 6 тип + 212 данных + 24 CRC-24Q; свёрточный r=1/2 K=7 G1=171₈ G2=133₈ → 500 сим/с', 'преамбулы': 'по кругу 01010011, 10011010, 11000110 (53h, 9Ah, C6h)',
         'CRC': 'g(X)=X^24+X^23+X^18+X^17+X^14+X^11+X^10+X^7+X^6+X^5+X^4+X^3+X+1 (CRC-24Q)', 'L5 SBAS (DFMC)': 'преамбулы 4 бита 0101, 1100, 0110, 1001, 0011, 1010 (PocketSDR)'},
        'все SBAS L1, QZSS L1S (SLAS/DC Report), BDSBAS', [S(QL1S, r'constraint length 7'), S(QL1S, r'4.1.1.3. Cyclic Redundancy Check'), S(BSBAS, r'8-bit preamble starts at bit 0'), S(PS, r'preamb = \(\(0, 1, 0, 1, 0, 0, 1, 1\), \(1, 0, 0, 1, 1, 0, 1, 0\)')],
        'многочлен CRC-24Q QZSS == GPS (0x1864CFB); преамбулы == PocketSDR; RTCA DO-229 — платный, не скачан', 'ЕСТЬ (свёрточный 171/133, CRC-24Q)'))
    z.append(Z('QZSS L6 (CLAS/MADOCA): Рид — Соломон (255,223) CCSDS (двойной базис), укорочение 9 символов, CSK', 'QZSS (IS-QZSS-L6)', 'РС',
        {'сообщение': '2000 бит/с: 49 бит заголовок (преамбула 1ACFFC1D) + 1695 данных + 256 бит RS; 250 символов по 8 бит в 1 с', 'РС': 'RS(255,223) как CCSDS: базис l0=α^125, l1=α^88, …, l7=α^242; 9 нулевых символов в начале (укорочение до 250)',
         'модуляция': 'CSK — значение символа = сдвиг фазы ПСП', 'совпадение': 'РС и преамбула = CCSDS 131.0-B (ASM 1ACFFC1D)'},
        'QZSS L6D (CLAS), L6E (MADOCA-PPP)', [S(QL6, r'Reed-Solomon \(255, 223\) encoding is applied'), S(QL6, r'Change of Basis'), S(PS, r'preamb = np.array\(\[0x1A, 0xCF, 0xFC, 0x1D')],
        'базис l0..l7 = α^125, α^88, α^226, α^163, α^46, α^184, α^67, α^242 — тот же, что CCSDS (rs_dvoinoi_bazis.json CCSDS)', 'ЕСТЬ (РС CCSDS в проекте — ccsds/rs)'))
    z.append(Z('NavIC/IRNSS L5 и S: свёрточный K=7 171/133 + перемежитель 73×8 + CRC-24Q, синхрослово EB90h', 'NavIC (IRNSS SPS ICD 1.1)', 'свёрточный',
        {'подкадр': '600 символов: 16 бит синхро EB90h (не кодируется) + 584 символа (292 бита: данные + CRC-24Q + 6 хвостовых нулей) ', 'свёрточный': 'r=1/2, K=7, G1=171₈, G2=133₈, G1 первым',
         'перемежитель': 'блочный 73 столбца × 8 строк, запись по столбцам, чтение по строкам'},
        'NavIC L5, S (Индия)', [S(IRN, r'Constraint Length 7 Generator Polynomial G1 = \(171\)o'), S(IRN, r'73 x 8'), S(IRN, r'Sync pattern is EB90 Hex')],
        'по тексту ICD; перемежитель — тот же вид, что Galileo (сверен в nav_prov.py для 61×8 и 30×8)', 'ЕСТЬ (свёрточный, блочный перемежитель, CRC-24Q)'))
    z.append(Z('NavIC L1 SPS: BCH(52,9) TOI + LDPC r=1/2 (1200,600)/(548,274) + перемежитель 46×38', 'NavIC (L1 SPS ICD 1.0)', 'LDPC',
        {'TOI': 'BCH(52,9), dmin=20, g(x)=x^9+x^8+x^7+x^6+x^5+x^4+x^2+x+1', 'LDPC': 'квазициклические r=1/2 для подкадров 2 (600 бит) и 3 (274 бита); подматрицы табл. 30–41 — отличаются от GPS L1C',
         'перемежитель': '1748 символов, 46 столбцов × 38 строк', 'CRC': 'CRC-24Q на подкадр', 'таблицы': T + 'ldpc_navic_l1_sf2.json, ' + T + 'ldpc_navic_l1_sf3.json'},
        'NavIC L1 (IRNSS-1J/NVS)', [S(NVL1, r'Generator polynomials of BCH encoders BCH Code'), S(NVL1, r'46 x 38'), S('kod/PocketSDR/src/sdr_ldpc.c', r'IRNV1')],
        'табл. 30–41 == PocketSDR H_IRNV1_*; матрица SF2 NavIC ≠ GPS L1C (nav_prov.py)', 'ЧАСТИЧНО (ldpc.py — загрузка H)'))
    return z
