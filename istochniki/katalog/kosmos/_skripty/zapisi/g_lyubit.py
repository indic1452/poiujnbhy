"""Любительские и малые спутники: AX.25 (+G3RUH), FX.25, IL2P, AO-40 FEC, NGHam, USP (СПУТНИКС), GOMspace AX100/U482C, TI CC11xx FEC
(OpenLST и др.), Mobitex, SMOG-P RA, прочие форматы кадров gr-satellites. Сверки — skripty/prov_lyubit.py (_itog_lyubit.json)."""

L = 'istochniki/lyubit/'
GS = 'kod/gr-satellites/'
DF = GS + 'python/components/deframers/'
T = 'tablicy/lyubit/'


def zapisi(S, Z):
    z = []
    z.append(Z('AX.25 (HDLC) с NRZI и скремблером G3RUH 1+X^12+X^17 (9600 бод)', 'AX.25 v2.2 / G3RUH', 'CRC-подобный',
        {'кадр': 'флаг 7E, вставка нулей после пяти единиц, FCS 16 бит (CRC-16-CCITT/X.25, отражённый, init FFFF, xorout FFFF), октеты младшим битом вперёд', 'код в линии': 'NRZI (0 — смена уровня)',
         'скремблер': 'самосинхронизирующийся 1+X^12+X^17 (G3RUH/K9NG) на 9600 бод; 1200 бод AFSK — без скремблера', 'помехоустойчивость': 'нет ПКЗ (только обнаружение ошибок)'},
        'большинство любительских и учебных спутников (AX.25 G3RUH — 223 передатчика, AX.25 1200 — 68 по satyaml gr-satellites)',
        [S(L + 'AX25.2.2_ax25net.pdf', r'3.6 Bit Stuffing In order to ensure'), S(L + 'g3ruh_9600_modem_109.html', r'The scrambling polynomial is 1 \+ X\^12 \+ X\^17'), S('kod/direwolf/src/demod_9600.h', r'\*state >> 16\) \^ \(\*state >> 11'), S(DF + 'ax25_deframer.py', r'g3ruh')],
        'G3RUH 1+X^12+X^17 == дескремблер direwolf (задержки 17 и 12) (prov_lyubit.py)', 'ЕСТЬ (hdlc.py: флаги, вставка нулей, FCS; скремблер — skrembler.py находит 1+x^12+x^17)'))
    z.append(Z('FX.25: Рид — Соломон поверх AX.25, метки корреляции 64 бита (11 режимов RS(255,239)/(255,223)/(255,191) и укороченные)', 'FX.25 (Stensat / WB2OSZ)', 'РС',
        {'кадр': 'метка корреляции 64 бита (выбирает код) + блок РС: кадр AX.25 с флагами/вставкой нулей, дополненный 7E, + проверочные байты', 'РС': 'GF(256), p=0x11D, fcr=1, prim=1; 16/32/64 проверочных байта',
         'метки': T + 'fx25_metki.json (0x01 B74DB7DF8A532F3E — RS(255,239) … 0x0B 4A4ABEC4A724B796 — RS(128,64))', 'совместимость': 'приёмник без FX.25 видит обычный AX.25'},
        'APRS/пакетная связь, Astrocast (вариант FX.25 NRZ/NRZ-I), ряд кубсатов', [S(L + 'AX25_plus_FEC_equals_FX25_WB2OSZ.pdf', r'0x01 0xB74DB7DF8A532F3E'), S('kod/direwolf/src/fx25_init.c', r'Tag_01'), S(DF + 'astrocast_fx25_deframer.py', r'decode_rs\(True, 1\)')],
        'метки и (k, проверочные) PDF == direwolf fx25_init.c; мин. расстояние меток 32 бита (prov_lyubit.py)', 'ЧАСТИЧНО (РС GF(256) — rs_bch.py; поиск меток — sinhro.py)'))
    z.append(Z('IL2P: Рид — Соломон GF(256) (fcr=0) + самосинхронизирующийся скремблер x^9+x^4+1, синхрослово F15E48', 'IL2P (TARPN, v0.6)', 'РС',
        {'кадр': 'преамбула 55h, синхрослово F15E48 (24 бита), заголовок 13 байт + 2 проверочных РС, полезная нагрузка блоками ≤ 239 байт + 16 проверочных (или 2–8 в режиме малого FEC)',
         'РС': 'GF(256) x^8+x^4+x^3+x^2+1, первый корень α^0; 2, 4, 6, 8, 16 проверочных', 'скремблер': 'LFSR x^9+x^4+1, сброс в начале каждого блока РС, скремблируется внутри блока РС до кодирования'},
        'пакетная связь нового поколения (direwolf, TARPN), совместима с AX.25-сетями', [S(L + 'il2p-specification_draft_v0-6.pdf', r'feedback polynomial x\^9\+x\^4\+1'), S(L + 'il2p-specification_draft_v0-6.pdf', r'reducing polynomial x\^8\+x\^4\+x\^3\+x\^2\+1'), S('kod/direwolf/src/il2p.h', r'IL2P_SYNC_WORD 0xF15E48'), S('kod/direwolf/src/il2p_init.c', r'\{8, 0x11d,   0,   1, 16, NULL \}')],
        'дескремблер direwolf эквивалентен y⊕y[n−4]⊕y[n−9] (моделирование) = x^9+x^4+1; синхрослово == спецификации (prov_lyubit.py)', 'ЧАСТИЧНО (РС, скремблер — есть; сброс по блокам — НЕТ)'))
    z.append(Z('AO-40 FEC (KA9Q): 2× РС(160,128) укороченных CCSDS + скремблер + свёрточный K=7 r=1/2 + перемежитель 80×65 с синхровектором 65 бит', 'AMSAT AO-40 / FUNcube', 'каскадный',
        {'кадр': '256 байт → чёт/нечет в два RS(160,128) (укороченный CCSDS (255,223) — корни α^(11j), j=112..143, но в ОБЫЧНОМ (не двойном) базисе) → скремблер (SCRAMBLER_POLY 0x95, 320 байт) → свёрточный K=7 (CPOLYA 0x4F, CPOLYB 0x6D, вторая ветвь инвертирована) → перемежитель 5200 бит: 80 столбцов × 65 строк, в первом столбце синхровектор 65 бит (SYNC_POLY 0x48, sr=0x7F)',
         'варианты gr-satellites': 'AO-40 FEC, короткий (1 блок РС), с CRC-16-ARC; AO-40 uncoded (синхро 0x3915ED30)', 'GF': 'GF_POLY 0x187 (CCSDS)'},
        'AO-40, FUNcube-1 (AO-73), JY1Sat, Nayif-1 и др. (AO-40 FEC — 14+8+8 передатчиков satyaml)', [S(L + 'ka9q_ao40_encode_ref.c', r'#define SYNC_POLY 0x48'), S(L + 'ka9q_ao40tlm.html', r'\(160,128\) Reed-Solomon codewords over GF\(256\)'), S(L + 'ka9q_ao40tlm.html', r'polynomial basis is used'), S(L + 'ka9q_ao40_encode_ref.c', r'Second encoder symbol is inverted'), S(DF + 'ao40_fec_deframer.py', r"_syncword = '1111111000011101")],
        'синхровектор 65 бит по SYNC_POLY == gr-satellites; g(x) РС ka9q == CCSDS E=16 (prov_malye.py, ao40_rs)', 'ЧАСТИЧНО (РС CCSDS, свёрточный — есть; перемежитель 80×65 с синхростолбцом — НЕТ)'))
    z.append(Z('NGHam: РС(255,223)/(255,239) CCSDS-поле (обычный базис) 7 размеров + метки размера 24 бита + скремблер CCSDS', 'NGHam (LA3JPA)', 'РС',
        {'кадр': 'преамбула AA, синхро 5DE62A7E, метка размера 24 бита (7 меток, мин. расстояние 13, допуск 6 ошибок), блок РС', 'размеры': 'нагрузка 28/60/92/124/156/188/220 байт; РС-блоки 47/79/111/159/191/223/255 (16 или 32 проверочных)',
         'РС': 'init_rs_char(8, 0x187, 112, 11, 16|32) — параметры CCSDS, без двойного базиса; укорочение pad', 'проверка': 'CRC-16-CCITT (X.25) внутри'},
        'NGHam (кубсаты, в т. ч. в gr-satellites: «NGHam no Reed Solomon»)', [S('kod/ngham/ngham.c', r'NGH_SYNC\[\] = \{0x5D, 0xE6, 0x2A, 0x7E\}'), S('kod/ngham/ngham.c', r'init_rs_char\(8, 0x187, 112, 11, 32, 0\)'), S('kod/gr-satellites/python/ngham_packet_crop.py', r'0b001110110100100111001101')],
        'метки размера ngham.c == gr-satellites (prov_lyubit.py)', 'ЧАСТИЧНО (РС CCSDS есть; метки размера — НЕТ)'))
    z.append(Z('USP (Unified SPUTNIX Protocol): синхро 64 бита + PLS-код (64,7) как DVB-S2 + свёрточный K=7 + скремблер CCSDS + РС(255,223)', 'USP (СПУТНИКС, Россия)', 'каскадный',
        {'кадр': 'преамбула 55555555h, синхро 5072F64B2D90B1F5h (допуск 13 ошибок), PLS 64 бита (7 бит типа, код (64,7) d=32, XOR с последовательностью DVB-S2), тип 16 бит, данные',
         'ПКЗ': 'РС(255,223) CCSDS (двойной базис), скремблер CCSDS, свёрточный r=1/2 K=7 (79, −109 в записи GNU Radio — вторая ветвь инвертирована); PLS 0 — блок 223 байта, 1 — 48 байт с виртуальным дополнением',
         'модуляция': 'GMSK (рекомендовано), 1200–115200 бит/с'},
        'российские кубсаты на платформе СПУТНИКС/ОрбиКрафт — 119 передатчиков в satyaml: CubeSX-HSE, SiriusSat/CubeSX-Sirius-HSE, UmKA-1, Сколтех-Б1/Б2, Монитор-1/3/4, Кузбасс-300, ВИЗАРД, UTMN, ReshUCube, SakhaCube и др.', [S(L + 'USP_protocol_description_v1.04.pdf', r'64-bit sync sequence 5072F64B2D90B1F5h'), S(L + 'USP_protocol_description_v1.04.pdf', r'fully equivalent to that used in the standards DVB-S2'), S(DF + 'usp_deframer.py', r'\[79, -109\]')],
        'PLS: мин. вес 32 по 127 словам; скремблирующая последовательность == DVB-S2 (стр. 33 EN 302 307-1); синхро == gr-satellites (prov_lyubit.py)', 'ЧАСТИЧНО (свёрточный, РС CCSDS, PLS (64,7) DVB-S2 — dvbs2_pl.py; сборка USP — НЕТ)'))
    z.append(Z('GOMspace AX100 (режимы RS и ASM+Golay) и U482C: ASM 930B51DE, поле длины Голей (24,12), скремблер CCSDS, РС(255,223), свёрточный K=7', 'GOMspace NanoCom AX100/U482C', 'каскадный',
        {'AX100 RS': 'дескремблер G3RUH (маска 0x21, 16) → синхро 930B51DE → 256 байт: длина + РС(255,223) CCSDS (укороченный по длине) → CSP', 'AX100 ASM+Golay': 'синхро → 24 бита: длина 8 бит + флаги (бит 8 — Витерби, 9 — скремблер, 10 — РС), код Голея (24,12) → скремблер CCSDS → РС',
         'Голей (24,12)': 'матрица H 12 строк: 8008ED 4001DB 2003B5 100769 080ED1 040DA3 020B47 01068F 008D1D 004A3B 002477 001FFE (golay24.c)', 'U482C': 'то же поле длины Голей; опционально свёрточный K=7 r=1/2 (V27POLYA/B), скремблер, РС'},
        'кубсаты на радио GOMspace (AX100 ASM+Golay — 74 передатчика, AX100 RS — 6, U482C — 9 в satyaml)', [S(DF + 'ax100_deframer.py', r"_syncword = '10010011000010110101000111011110'"), S('kod/gr-satellites/lib/golay24.c', r'0x8008ed, 0x4001db'), S('kod/gr-satellites/lib/u482c_decode_impl.cc', r'viterbi_flag = length_field & 0x100'), S('kod/gr-satellites/lib/ax100_decode_impl.cc', r'decode_rs_8')],
        'только открытый код gr-satellites (GPL-3); документация GOMspace закрыта', 'ЧАСТИЧНО (Голей (24,12) в DMR — dmr.py, другая матрица; РС CCSDS есть)'))
    z.append(Z('TI CC1101/CC1110/CC2500 FEC: свёрточный K=4 r=1/2 (15/17₈) + перемежитель 4×4 + отбеливание PN9 (x^9+x^5+1) + CRC-16 0x8005', 'TI CC11xx (DN504, DN509, CC1101)', 'свёрточный',
        {'свёрточный': 'L=4 (M=3), невырожденный; таблица fecEncodeTable {0,3,1,2,3,0,2,1,3,0,2,1,0,3,1,2} = (g1, g0) = (1+D²+D³, 1+D+D²+D³) — 15₈/17₈; окончание решётки — байты 0B 0B', 'перемежитель': 'матрица 4×4 символов по 2 бита (32 бита = 4 байта)',
         'отбеливание': 'PN9 x^9+x^5+1, регистр из единиц, на данные после синхрослова (включая длину и CRC)', 'CRC': 'CRC-16 многочлен 0x8005, init FFFF (не отражённый)', 'синхрослово по умолчанию': 'D391 (часто D391D391)'},
        'OpenLST (DORA, Planet), GEOSCAN, Lucky-7, Grizu-263A, Reaktor Hello World, BINAR-2 и др. кубсаты на трансиверах TI', [S(L + 'TI_DN504_FEC_swra113a.pdf', r'fecEncodeTable\[\] = \{ 0, 3, 1, 2'), S(L + 'TI_DN504_FEC_swra113a.pdf', r'4x4 matrix interleaver'), S(L + 'TI_DN509_PN9_swra322.pdf', r'x9 \+ x5 \+ x0'), S(L + 'TI_CC1101_datasheet_swrs061i.pdf', r'PN9 sequence is initialized to all 1'), S(DF + 'openlst_deframer.py', r'aTrellisTransitionOutput')],
        'ТЕСТОВЫЙ ВЕКТОР DN504 (стр. 9): CRC 303A, выход кодера и перемежителя совпали байт в байт; декодер Витерби OpenLST (gr-satellites) восстанавливает вход (prov_lyubit.py)', 'ЧАСТИЧНО (свёрточный K=4 — svyortka.py найдёт; перемежитель 4×4 и PN9 — skrembler.py находит x^9+x^5+1)'))
    z.append(Z('Mobitex / Mobitex-NX (BEESAT, TechnoSat, TUBIN): блоки данных с кодом Хэмминга (12,8) + перемежение + CRC-16', 'Mobitex (TU Berlin)', 'Хэмминг',
        {'варианты': 'Mobitex-NX (с позывным и CRC позывного), BEESAT-1 (без позывного), BEESAT-9 (32 блока данных)', 'описание': 'по докстрингу и коду gr-satellites mobitex_deframer.py / mobitex_to_datablocks'},
        'BEESAT-1/2/4/9, TechnoSat, TUBIN (TU Berlin) — 8+8 передатчиков satyaml', [S(DF + 'mobitex_deframer.py', r'Hierarchical block to deframe Mobitex and Mobitex-NX')],
        'только открытый код gr-satellites (GPL-3); тест qa_mobitex_deframer с эталонным кадром', 'НЕТ'))
    z.append(Z('SMOG-P / SMOG-1 / MRC-100: коды повторения-накопления (RA) r=1/2, CRC-16-ARC', 'SMOG-P RA (BME, Венгрия)', 'LDPC',
        {'код': 'RA (repeat-accumulate), перфорация RA_PUNCTURE_RATE 3 → r=1/2, длины блоков по кадру; декодер ra_decoder_gen (Miklos Maroti)', 'CRC': 'CRC-16-ARC, у MRC-100 переставлен в конец'},
        'SMOG-P, ATL-1, SMOG-1, MRC-100 (16+8+8 передатчиков satyaml)', [S('kod/gr-satellites/lib/radecoder/ra_config.h', r'RA_PUNCTURE_RATE = 3'), S(DF + 'smogp_ra_deframer.py', r'decode_ra_code')],
        'только открытый код gr-satellites (GPL-3)', 'НЕТ'))
    z.append(Z('Прочие форматы кадров кубсатов (gr-satellites): синхрослова и ПКЗ', 'кубсаты (gr-satellites)', 'каскадный',
        {'AALTO-1': 'синхро 352E352E, PN9, CRC-16 X.25', 'AAUSAT-4': 'синхро 4F5A34435542, свёрточный + РС(255,223)', 'AISTECHSAT-2': 'РС(255,223) + скремблер CCSDS', 'ESEO': 'синхро 7E7E, РС(8,0x11d,fcr1,16 проверочных), CRC-16', 'TT-64 (QB50 AT04)': 'синхро 2DD4, РС 16 проверочных, CRC-16-ARC',
         '3CAT-1': 'синхро D391D391, PN9, РС 32 проверочных', 'Swiatowid': 'синхро 5B5BDDDD, РС 10 проверочных (fcr=0)', 'QUBIK': 'синхро 3C674952, РС(255,223), CRC-16/CRC-32C', 'OPS-SAT': 'AX.25+РС(255,223) (NRZI, G3RUH)',
         'LilacSat-1': 'свёрточный + скремблер, KISS + Codec2', 'K2SAT': 'свёрточный + дескремблер 308 бит', 'HSU-SAT1': 'свёрточный K=3, CRC-16 X.25', 'AX5043': 'синхро 8AE68AE6, свёрточный + перемежитель 4×4 (как CC11xx), HDLC, CRC-16-USB',
         'S-NET': 'синхро 04CF5FC8, собственный формат TU Berlin (опция buggy_crc)', 'NuSat': 'синхро 01E5AACC', 'SPINO': 'синхро 2EFC9827, CRC-16', 'FOSSASAT': 'синхро 5555 1212, PN9', 'BINAR-1/2': 'CRC-16 (CCITT-false / CC11xx)',
         'таблица «формат → спутники»': T + 'grsat_format_sputniki.json'},
        '≈680 передатчиков любительских и научных КА (satyaml gr-satellites)', [S(DF + 'aalto1_deframer.py', r'_syncword'), S(DF + 'eseo_deframer.py', r'decode_rs\(8, 0x11d, 1, 1, 16, 1\)'), S(DF + 'sat_3cat_1_deframer.py', r'decode_rs\(8, 0x11d, 1, 1, 32, 1\)'),
                                                                   S(DF + 'swiatowid_deframer.py', r'decode_rs\(8, 0x11d, 0, 1, 10, 1\)'), S(DF + 'tt64_deframer.py', r'decode_rs\(8, 0x11d, 1, 1, 16, 1\)'), S(DF + 'hsu_sat1_deframer.py', r'K = 3'), S(DF + 'ax5043_deframer.py', r'4x4 matrix deinterleaving')],
        'только открытый код gr-satellites (GPL-3); синхрослова — константы деформаторов', 'ЧАСТИЧНО (синхро, РС, CRC, PN9 — есть; собственные заголовки — НЕТ)'))
    return z
