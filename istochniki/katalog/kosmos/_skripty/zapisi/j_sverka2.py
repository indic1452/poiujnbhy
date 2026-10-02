"""Сверка-2 (проверка полноты): записи, которых не было в каталоге.
LunaNet AFS (NASA LSIS 2025), GMR-1 3G LDPC (PNB2), GOES DCPRS (CS2), МСЭ-R BO.1130 системы B и DH, SDR многочастотный (TPS BCH),
IRIG 106-24 (телеметрия ракет/ЛА: LDPC AR4JA, STC). Проверки — skripty/sverka2/*.py, итоги — tablicy/_itog_sverka2_b.json."""
import json, os

KOR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LS = 'istochniki/dop2/LSIS_AFS_volA_2025.pdf'
VL = 'istochniki/dop2/lsis_vlozheniya/'
G3 = 'istochniki/gmr1/ts_1013760503v030301p.pdf'
DC = 'istochniki/dop2/DCS_Certification_Standard_V2.pdf'
BO = 'istochniki/itu/R-REC-BO.1130-5-202602-I.pdf'
SD2 = 'istochniki/sdr/ts_10255102v020101p.pdf'
IR = 'istochniki/dop3/IRIG106-24_chapter2.pdf'


def zapisi(S, Z):
    z = []
    ln = json.load(open(os.path.join(KOR, 'tablicy/lunanet/_itog_lunanet.json')))
    z.append(Z('LunaNet AFS (NASA LSIS Vol. A, 2025): кадр 12 с — SP 68 + BCH(51,8) + LDPC r=1/2 (= 5G NR базовый граф 2) + CRC-24Q, перемежитель 60×98',
               'LunaNet (NASA LSIS AFS)', 'каскадный',
               {'кадр': '6000 символов за 12 с (500 симв/с): синхрошаблон SP 68 символов CC63F74536F49E04A (не кодируется) + SB1 52 символа + 5880 символов SB2–SB4',
                'SB1': '9 бит (TOI 7 + FID): младшие 8 бит → BCH(51,8), многочлен 763₈ (8-разрядный ЛРС, загрузка «бит 1 первым»), старший бит SB1 складывается по модулю 2 с 51 символом и ставится первым → 52 символа (как GPS L1C)',
                'LDPC': 'r=1/2: SB2 1200→2400, SB3 и SB4 870→1740 (+10 бит заполнителя); H=[A B 0; C D I], кодер p1=B⁻¹·A·s, p2=C·s+D·p1; первые 2Z систематических выкалываются',
                '= 5G NR': 'H поэлементно = базовый граф 2 TS 38.212 при Z=120 (SB2) и Z=88 (SB3/SB4): ' + ln['sf2']['= 5G NR'] + '; ' + ln['sf3']['= 5G NR'],
                'CRC': 'CRC-24Q: g(X)=(1+X)·p(X)=0x1864CFB (в тексте стандарта перечень степеней содержит опечатку «34» вместо 24)',
                'перемежитель': 'блочный 60 строк × 98 столбцов (5880 символов SB2–SB4): запись по строкам слева направо (MSB первым), чтение по столбцам',
                'модуляция': 'AFS-I BPSK(1) с данными (Gold 2047, укороченный до 2046, без вторичного кода), AFS-Q BPSK(5) пилот (Weil 10230 + вторичный 4 + третичный 1500 символов на кадр 12 с)',
                'таблицы': 'tablicy/lunanet/lunanet_afs_ldpc.json (индексы A, B, B⁻¹, C, D для SB2/SB3), подматрицы CSV — istochniki/dop2/lsis_vlozheniya/'},
               'навигационный сигнал LunaNet (Луна, проекты NASA/ESA/JAXA LANS/Moonlight)',
               [S(LS, r'Bose–Chaudhuri–Hocquenghem \(BCH\) code \(51, 8\)'), S(LS, r'generator polynomial of 763 \(octal\)'), S(LS, r'Subframe 2 1200 LDPC 1/2 2400'),
                S(LS, r'60 rows and 98 columns'), S(LS, r'CC63F74536F49E04A'), S(LS, r'This code is called CRC-24Q'),
                S(VL + '003a_lunanet_sf2_ldpc_submatrix_a_ind.csv', r'^\d+,\d+'), S(VL + '002_LNIS-AD1-Vol-A-Annex2-LDPC-Submatrices-CSV-README.pdf', r'.')],
               'skripty/sverka2/lunanet_ldpc.py: индексная и полная формы подматриц совпали; B·B⁻¹=I; кодер по тексту даёт H·c=0; H = 5G NR BG2 (сдвиги из data/ldpc_nr.json проекта, сверенного с AFF3CT/srsRAN/Sionna) — ВТОРОЙ ИСТОЧНИК; CRC-24Q = 0x1864CFB',
               'ЧАСТИЧНО: LDPC 5G NR BG2 и CRC-24Q в проекте есть (data/ldpc_nr.json, crc); выкалывание 2Z, заполнитель и перемежитель 60×98 — добавить'))
    g3 = json.load(open(os.path.join(KOR, 'tablicy/gmr1/gmr1_3g_ldpc.json')))
    kody = ['%s %s %s (%d,%d)' % (c['пакет'], c['код'], c['модуляция'], c['n'], c['k']) for c in g3['коды']]
    z.append(Z('GMR-1 3G (Thuraya, SkyTerra/TerreStar): LDPC PNB2(5,12) и PNB2(5,3) — 18 кодов r=1/2…9/10, структура IRA как DVB-S2',
               'GMR-1 3G (ETSI TS 101 376-5-3 V3)', 'LDPC',
               {'коды': '; '.join(kody), 'построение': g3['правило'], 'параметры': 'k, n, XS (укорочение), XR, XP (выкалывание), M (размер группы), q=(n−k)/M — табл. 4.9–4.12',
                'адреса накопителей': 'прил. A табл. A.1–A.18', 'таблица': 'tablicy/gmr1/gmr1_3g_ldpc.json (собрана из текста с assert)',
                'применение': 'пакетные каналы PDTCH2 GMR-1 3G (LDPC coded PDTCH2(5,12)/(5,3) — МСЭ-R M.1850-2, SRI-H)'},
               'Thuraya IP/GmPRS-3G, SkyTerra/LightSquared, TerreStar (GMR-1 Release 3, МСЭ-R M.1850 SRI-H)',
               [S(G3, r'Table 4\.9'), S(G3, r'Address of Parity Bit Accumulators'), S(G3, r'Parameters of the PNB2\(5,3\)'), S('istochniki/itu/R-REC-M.1850-2-201409-I.pdf', r'LDPC coded PDTCH2 \(5,12\)')],
               'skripty/sverka2/gmr1_3g_ldpc.py: число строк табл. A = k/M, q=(n−k)/M, все адреса < n−k, нет кратных рёбер при сдвигах t·q; эффективная скорость (k−XS)/(n−XS−XP+XR) = столбцу eff R табл. 4.9/4.11 для всех 18 кодов',
               'ЧАСТИЧНО: механизм IRA/DVB-S2 (адреса накопителей) в проекте есть (ldpc_std.py); таблицы GMR-1 3G — добавить из tablicy/gmr1'))
    dc = json.load(open(os.path.join(KOR, 'tablicy/goes_dcs/dcprs_cs2.json')))
    z.append(Z('GOES DCS (DCPRS 300/1200 бит/с, NOAA CS2): прагматическая TCM 8PSK — свёрточный K=7 171/133 на младший бит пары, скремблер 40 байт, FSS 15 бит',
               'GOES DCS (NOAA/NESDIS DCPRS CS2)', 'TCM',
               {'кодер': 'пара бит (MSB, LSB) → 3 бита: E-2 = MSB без кодирования; LSB → регистр Q1…Q7: E-1 = %s (133₈), E-0 = %s (171₈); начальное состояние 0; байт делится на 4 пары, первой идёт пара бит 1–0' % (dc['кодер']['E-1'], dc['кодер']['E-0']),
                'фазы (E-2 E-1 E-0 → град.)': ', '.join('%s→%d' % kv for kv in dc['фазы_E2E1E0'].items()),
                'скремблер': 'XOR с циклической таблицей 40 байт (53 12 72 B2 … 3C 0A) с первого бита GOES ID; линейная сложность 320 бит: 72 в порядке передачи, 18 при чтении байтов младшим битом вперёд (многочлен связи %s) — вычислено при сверке' % dc['скремблер_многочлен_связи_младший_первым'],
                'кадр': 'несущая 0,5/0,25 с → 3 символа «тактов» 180°/0°/180° → FSS 001111100110101 (BPSK, не скремблируется) → GOES ID 32 бита (31-бит адрес БЧХ + 0) → флаги 8 бит → данные → EOT → 32 нуля сброса кодера',
                'скорости': '300 и 1200 бит/с, 8PSK (150/600 симв/с)', 'таблица': 'tablicy/goes_dcs/dcprs_cs2.json'},
               'платформы сбора данных (метео-, гидрологические станции) через GOES DCS',
               [S(DC, r'Figure 1\. Trellis Encoder Functional Diagram This logic'), S(DC, r'used in a circular fashion throughout the message'), S(DC, r'001111100110101'),
                S(DC, r'Phase Encoding Table'), S(DC, r'additional 32 zero')],
               'skripty/sverka2/prov_dcprs.py: отводы рис. 1 извлечены из векторной графики PDF и привязаны к подписям Q1…Q7 (assert = 1,3,4,6,7 и 1,2,3,4,7); таблица 40 байт и пример CE⊕53=9D из текста; табл. фаз 1; FSS; Берлекэмп — Мэсси проверен на PN9',
               'ЧАСТИЧНО: свёрточный 171/133 и 8PSK есть; прагматическая TCM (некодированный бит), таблица скремблера и кадр — добавить'))
    z.append(Z('МСЭ-R BO.1130 Digital System B (VOA/JPL, 1,4–2,7 ГГц): РС(160,140) + свёрточный r=1/2 K=7 + блочный перемежитель ~200 мс, QPSK',
               'BO.1130 System B', 'каскадный',
               {'внешний': 'РС (в тексте записано «RS (140,160)» — т. е. (160,140))', 'внутренний': 'свёрточный r=1/2, K=7',
                'перемежение': 'блочный, кадр перемежителя ≈200 мс при любой скорости', 'синхро': 'ПСП-слово в начале каждого кадра перемежителя',
                'прочее': 'обучающие символы через каждые n=2…4 символа данных (для ретрансляторов), QPSK; скорость 32 кбит/с … 1–10 Мбит/с'},
               'цифровое звуковое вещание (опытная система, прототип)',
               [S(BO, r'preceded by RS \(140,160\) encoding'), S(BO, r'interleaver frame time on the order of 200 ms'), S(BO, r'pseudo noise \(PN\) code word is inserted')],
               'текст BO.1130-5 п. 3.1.4–3.1.7 (skripty/sverka2/sverka_b6.py); образцов и второго источника нет',
               'ЧАСТИЧНО: РС, свёрточный и блочное перемежение в проекте есть; параметры системы — справочно'))
    z.append(Z('МСЭ-R BO.1130 Digital System DH (WorldSpace гибридная): РС(255,223) + свёрточный 1/4 → два дополняющих выколотых канала 1/2 «ранний/поздний» (разнос 4,32 с), перемежение 432 мс',
               'BO.1130 System DH', 'каскадный',
               {'спутниковая часть': 'TDM как System DS: кадр канала n×7136 бит (6912 + 224 заголовка) → РС(255,223) (в тексте «RS (223,255)») → n×8160 → свёрточный R 1/4 → разделение на два дополняющих выколотых канала R 1/2',
                'разнесение': 'ранний канал перемежается в кадре 432 мс, поздний задержан ≈4,32 с и не перемежается (совместим с обычными приёмниками DS); вариант с двумя КА — разнос в пространстве',
                'синхронизация': 'быстрое снятие фазовой неоднозначности QPSK каждые 1,4375 мс',
                'наземная часть': 'TDM-MCM (многочастотная, ретрансляторы ОЧС)'},
               'WorldSpace (AfriStar/AsiaStar) — подвижный приём', [S(BO, r'RS \(223,255\) block coder'), S(BO, r'R 1/4 convolution coder whose output is split'),
                                                                  S(BO, r'interleaved over a 432 ms'), S(BO, r'phase ambiguity recovery every 1\.4375 ms'), S(BO, r'transport is referred to as TDM-MCM')],
               'текст BO.1130-5 прил. 4 (skripty/sverka2/sverka_b6.py): 7136+1024=8160 = (255/223)·7136',
               'ЧАСТИЧНО: РС(255,223) и свёрточный K=7 есть; схема выкалывания 1/4→2×1/2 в BO.1130 не раскрыта'))
    z.append(Z('SDR многочастотный (ETSI TS 102 551-2): TPS — BCH(67,53) t=2 укороченный из BCH(127,113), скремблер X^11+X^9+1',
               'SDR (ETSI TS 102 550/551)', 'БЧХ',
               {'TPS': '53 бита (синхро + параметры) + 14 проверочных BCH(67,53,t=2), укорочение (127,113) на 60 нулей; h(x)=x^14+x^9+x^8+x^6+x^5+x^4+x^2+x+1; DBPSK на пилотных несущих TPS',
                'скремблер': 'X^11+X^9+1, начальное 11001110001, длина 2016 (режим 1) или 2064 (режимы 2–4), сброс на каждую CU'},
               'спутниковое цифровое радио ETSI SDR (OFDM-режимы)', [S(SD2, r'BCH \(67,53, t = 2\) shortened code'), S(SD2, r'h\(x\) = x14 \+ x9 \+ x8'), S(SD2, r'initial state is set to "11001110001"')],
               'skripty/sverka2/sverka_b6.py: h(x) = m1·m3 над GF(2^7) (своим перебором p(x)) → BCH(127,113) t=2',
               'ЧАСТИЧНО: БЧХ распознаётся rs_bch.py; параметры TPS — добавить'))
    z.append(Z('IRIG 106-24 гл. 2 (телеметрия ракет и ЛА): SOQPSK-TG + LDPC AR4JA k=1024/4096 r=1/2, 2/3, 4/5 (как CCSDS), ASM 64/256 бит, рандомизатор CCSDS; STC Аламоути',
               'IRIG 106 (RCC Telemetry Standards)', 'LDPC',
               {'LDPC': '6 кодов AR4JA CCSDS (131.1-O-2): k=1024 (M=512/256/128) и 4096 (M=2048/1024/512), r=1/2, 2/3, 4/5; G по первым строкам циркулянтов (табл. D-4…D-9)',
                'ТЕСТОВЫЕ ВЕКТОРЫ': 'D.4.g: счётчик 16-бит слов + синхро FE6B2840 (k=1024) и чётность для каждой скорости — использованы для сверки AR4JA CCSDS',
                'ASM': 'из 64-бит последовательностей A=FCB88938D8D76A4F и Ā=034776C7272895B0: 64 бита при k=1024, 256 бит (4 последовательности) при k=4096; ASM не рандомизируется',
                'рандомизатор': 'CCSDS (h(x)=x^8+x^7+x^5+x^3+1) на кадр кодовых блоков, сброс в единицы на каждом ASM; для некодированных линий — самосинхронизирующийся IRIG 15-бит (гл. 6, прил. A.2)',
                'STC': 'пространственно-временной код Аламоути для SOQPSK-TG (2 антенны), с пилотными последовательностями (прил. 2-E)'},
               'телеметрия ракет-носителей, ЛА и полигонов (США, RCC)',
               [S(IR, r'identical to'), S(IR, r'Table D-10\. ASM Definition 64-bit Sequence'), S(IR, r'intended to provide a means to verify parity generation'),
                S(IR, r'resets to the initial state of all 1s'), S(IR, r'based on the Alamouti STC')],
               'skripty/sverka2/sverka_b5.py: тестовые векторы D.4.g для всех 6 кодов удовлетворяют H·c=0 с H из tablicy/ccsds/ldpc_ar4ja_*.json (выколотые M символов найдены решением над GF(2))',
               'ЧАСТИЧНО: AR4JA CCSDS — tablicy/ccsds (в проекте ar4ja из AFF3CT НЕ совпал с CCSDS); STC и SOQPSK — нет'))
    return z
