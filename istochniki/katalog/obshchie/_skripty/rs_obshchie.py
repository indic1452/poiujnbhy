"""Коды РС из собственных первоисточников области (штрихкоды ZXing, PDF417, CD/CD-ROM ECMA-130, DVD ECMA-267, JT65,
CCSDS в libfec, перечень кодеков lib/reed_solomon Linux) → tablicy/rs/rs_obshchie.json, zapisi/rs.json; плюс сводная
таблица РС всех областей tablicy/rs/svodka_oblastey.json.
Проверки (assert): тестовые векторы ZXing (ReedSolomonTestCase.java) — кодирование своим кодером (rs_gf.py) даёт те же
проверочные символы; таблица коэффициентов PDF417 = ∏(x − 3^i) mod 929; CCSDS (fcr=112, prim=11) — порождающий
многочлен симметричен; JT65 — параметры поля/корней подобраны перебором так, чтобы три примера статьи (рис. 2)
кодировались точно; CRC-32 CD-ROM EDC = произведение двух многочленов ECMA-130 и = poly модели RevEng."""
import json, os, re, sys, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rs_gf import GF, genpoly, encode
from gf2 import mul as pmul, primitive
from zap import KOR, I, Z, sohranit, stroka, gde
OUT = os.path.join(KOR, 'tablicy', 'rs'); os.makedirs(OUT, exist_ok=True)
res = {}; zap = []
# --- ZXing ---
FZ = 'istochniki/rs/zxing/'
gsrc = open(os.path.join(KOR, FZ + 'GenericGF.java')).read()
polya = {}
for name, poly, size, base in re.findall(r'public static final GenericGF (\w+) = new GenericGF\(0b([01]+), (\d+), (\d)\)', gsrc):
    polya[name] = (int(poly, 2), int(size), int(base))
polya['AZTEC_DATA_8'] = polya['DATA_MATRIX_FIELD_256']; polya['MAXICODE_FIELD_64'] = polya['AZTEC_DATA_6']
tsrc = open(os.path.join(KOR, FZ + 'ReedSolomonTestCase.java')).read()
proveril = {}
for m in re.finditer(r'testEncodeDecode\(GenericGF\.(\w+),\s*new int\[\]\s*\{([^}]*)\},\s*new int\[\]\s*\{([^}]*)\}\)', tsrc):
    f = m.group(1); dat = [int(x, 0) for x in re.findall(r'0x[0-9A-Fa-f]+|\d+', m.group(2))]; ec = [int(x, 0) for x in re.findall(r'0x[0-9A-Fa-f]+|\d+', m.group(3))]
    poly, size, base = polya[f]
    gf = GF(size.bit_length() - 1, poly)
    assert encode(gf, dat, genpoly(gf, len(ec), base)) == ec, f
    proveril[f] = proveril.get(f, 0) + 1
print('ZXing: векторов проверено', proveril)
res['zxing'] = {k: dict(poly=hex(v[0]), поле=v[1], fcr=v[2], векторов=proveril.get(k, 0)) for k, v in polya.items()}
# PDF417
p4 = open(os.path.join(KOR, FZ + 'ErrorCorrection_pdf417.java')).read()
body = p4[p4.find('EC_COEFFICIENTS = {'):p4.find('};', p4.find('EC_COEFFICIENTS = {'))]
rows = [[int(x) for x in re.findall(r'\d+', r)] for r in re.findall(r'\{([^{}]*)\}', body)]
for L, row in enumerate(rows):
    k = 2 ** (L + 1)
    g = [1]
    for i in range(1, k + 1):
        r = pow(3, i, 929); ng = g + [0]
        for j in range(len(g)): ng[j + 1] = (ng[j + 1] - g[j] * r) % 929
        g = ng
    assert len(row) == k and row == g[1:][::-1], L
print('PDF417: уровней', len(rows), 'коэффициенты = ∏(x − 3^i) mod 929')
# CCSDS libfec
FL = 'istochniki/kod/libfec/gen_ccsds.c'
assert 'init_rs_char(8,0x187,112,11,32,0)' in open(os.path.join(KOR, FL)).read()
gfc = GF(8, 0x187); gc = genpoly(gfc, 32, 112, 11)
assert gc == gc[::-1]
# Linux rslib
FLX = 'istochniki/rs/linux/test_rslib.c'
tab = [tuple(int(x, 0) for x in r) for r in re.findall(r'\{(\d+),\s*(0x[0-9a-f]+),\s*(\d+),\s*(\d+),\s*(\d+),\s*\d+\s*\}', open(os.path.join(KOR, FLX)).read())]
for sym, gp, fcs, prim, nr in tab:
    GF(sym, gp)  # поле примитивно (assert внутри)
# JT65
FJ = 'istochniki/rs/jt65_qex2005_arrl.pdf.txt'
tj = open(os.path.join(KOR, FJ)).read()
prim = [(re.findall(r'Packed message, 6-bit symbols:\s*([\d ]+)\n', tj)), None]
msgs = [[int(x) for x in s.split()] for s in re.findall(r'Packed message, 6-bit symbols:\s*([\d ]+?)\s*\n', tj)]
chans = [[int(x) for x in s.split()] for s in re.findall(r'Channel symbols, including FEC:\s*\n([\d \n]+?)\n\s*\n', tj)]
assert len(msgs) == 3 and all(len(c) == 63 for c in chans), (len(msgs), [len(c) for c in chans])
def ig(g):
    b = g
    while g: g >>= 1; b ^= g
    return b
def deint(s):  # обратное к «запись по строкам 7×9, чтение по столбцам»
    out = [0] * 63; k = 0
    for j in range(7):
        for i in range(9): out[i * 7 + j] = s[k]; k += 1
    return out
cw = [deint([ig(x) for x in c]) for c in chans]
for c, mm in zip(cw, msgs): assert c[51:] == mm
naid = []
for poly in range(65, 128):
    if not primitive(poly): continue
    gf = GF(6, poly)
    for fcr in range(63):
        for pr in range(1, 63):
            from math import gcd
            if gcd(pr, 63) != 1: continue
            g = genpoly(gf, 51, fcr, pr)
            par = encode(gf, msgs[0], g)
            for rv in (False, True):
                if (par[::-1] if rv else par) == cw[0][:51]:
                    if all((encode(gf, mm, g)[::-1] if rv else encode(gf, mm, g)) == c[:51] for mm, c in zip(msgs, cw)):
                        naid.append(dict(poly=hex(poly), fcr=fcr, prim=pr, проверочные_в_обратном_порядке=rv))
print('JT65: подходящих параметров', naid)
assert naid
res['jt65'] = naid
# CD-ROM EDC
FE = 'istochniki/rs/ecma/ECMA-130_2nd_edition_june_1996.pdf.txt'
te = open(os.path.join(KOR, FE)).read()
assert 'P(x) = (x16 + x15 + x2 + 1) . (x16 + x2 + x + 1)' in te
edc = pmul((1 << 16) | (1 << 15) | 4 | 1, (1 << 16) | 4 | 2 | 1)
rv = json.load(open(os.path.join(KOR, 'tablicy', 'crc', 'reveng.json')))
cdm = next(m for m in rv if m['имя'] == 'CRC-32/CD-ROM-EDC')
assert int(cdm['poly'], 16) == edc & 0xFFFFFFFF
# DVD
FD = 'istochniki/rs/ecma/ECMA-267_3rd_edition_april_2001.pdf.txt'
td = open(os.path.join(KOR, FD)).read()
assert 'G(x) = x32 + x31 + x4 + 1' in td and 'RS (208,192,17)' in td and 'RS (182,172,11)' in td
dvd_edc = (1 << 32) | (1 << 31) | (1 << 4) | 1
dvd_scr = (1 << 15) | (1 << 4) | 1
res['dvd'] = dict(edc_hex=hex(dvd_edc), скремблер_примитивен=primitive(dvd_scr))
json.dump(res, open(os.path.join(OUT, 'rs_obshchie.json'), 'w'), ensure_ascii=False, indent=1)
U = 'https://github.com/zxing/zxing/blob/master/core/src/main/java/com/google/zxing/common/reedsolomon/'
def zxI(): return [I(FZ + 'GenericGF.java', 'строки 33–40'), I(FZ + 'ReedSolomonTestCase.java', 'тестовые векторы testQRCode/testDataMatrix/testAztec'), I(FZ + 'ReedSolomonEncoder.java', 'строки 39–46 (generatorBase)')]
SL_RS = 'ЕСТЬ в проекте: rs_bch.py (опознание поля, fcr и шага вслепую по корням; исправление с стираниями), dlinnye.рс_в_блоке'
zap.append(Z('РС QR Code: GF(256) x^8+x^4+x^3+x^2+1 (0x11D), fcr=0', 'РС штрихкодов (ISO/IEC 18004)', 'РС',
             {'поле': '0x11D, α=2', 'корни': 'α^0…α^(2t−1) (generatorBase = 0)', 'n,k': 'по версии/уровню QR (до 255 байт), блоки перемежаются', 'векторов ZXing': proveril.get('QR_CODE_FIELD_256', 0)},
             'QR Code (ISO/IEC 18004; сам стандарт платный — параметры из ZXing, Apache-2.0)', zxI(),
             'тестовые векторы ZXing перекодированы своим кодером (rs_gf.py) — проверочные байты совпали', SL_RS))
zap.append(Z('РС Data Matrix ECC 200 / Aztec 8-бит: GF(256) x^8+x^5+x^3+x^2+1 (0x12D), fcr=1', 'РС штрихкодов (ISO/IEC 16022, 24778)', 'РС',
             {'поле': '0x12D', 'корни': 'α^1…α^(2t)', 'векторов ZXing': proveril.get('DATA_MATRIX_FIELD_256', 0) + proveril.get('AZTEC_DATA_8', 0)},
             'Data Matrix ECC 200, Aztec (слова 8 бит)', zxI(), 'тестовые векторы ZXing (Data Matrix и Aztec-8) совпали', SL_RS))
for nm, opis in (('AZTEC_PARAM', 'GF(16) x^4+x+1 — служебное сообщение Aztec'), ('AZTEC_DATA_6', 'GF(64) x^6+x+1 — Aztec 6-бит, MaxiCode'),
                 ('AZTEC_DATA_10', 'GF(1024) x^10+x^3+1 — Aztec 10-бит'), ('AZTEC_DATA_12', 'GF(4096) x^12+x^6+x^5+x^3+1 — Aztec 12-бит')):
    p, s, b = polya[nm]
    zap.append(Z('РС Aztec/MaxiCode: %s (0x%X), fcr=%d' % (opis, p, b), 'РС штрихкодов (ISO/IEC 24778, 16023)', 'РС',
                 {'поле': '0x%X (%d элементов)' % (p, s), 'корни': 'α^1…α^(2t)', 'векторов ZXing': proveril.get(nm, 0)},
                 'Aztec Code' + (', MaxiCode' if nm == 'AZTEC_DATA_6' else ''), zxI(),
                 ('тестовые векторы ZXing совпали (%d)' % proveril.get(nm, 0)) if proveril.get(nm) else 'поле примитивно (расчёт); векторов нет',
                 SL_RS if s <= 256 else 'ЧАСТИЧНО: rs_bch.py — поля до GF(256)/GF(2^m) по символам; 10/12-бит символы штрихкода в потоке не встречаются'))
zap.append(Z('РС PDF417 над простым полем GF(929), генератор ∏(x − 3^i), i=1…2^(L+1)', 'РС штрихкодов (ISO/IEC 15438)', 'РС (над простым полем)',
             {'поле': 'целые mod 929, примитивный элемент 3', 'уровни': '0…8: 2…512 проверочных слов', 'таблица': 'EC_COEFFICIENTS (ZXing) — пересчитана'},
             'PDF417', [I(FZ + 'ErrorCorrection_pdf417.java', 'строки %d–… (EC_COEFFICIENTS)' % stroka(FZ + 'ErrorCorrection_pdf417.java', 'EC_COEFFICIENTS = {')), I(FZ + 'ModulusGF.java', 'весь файл')],
             'все 9 уровней коэффициентов = ∏(x − 3^i) mod 929 (расчёт)', 'НЕТ: в проекте только поля GF(2^m)'))
zap.append(Z('РС CCSDS (255,223) в libfec: GF(256) 0x187, fcr=112, prim=11 (двойной базис)', 'РС CCSDS (реализация Karn)', 'РС',
             {'поле': 'x^8+x^7+x^2+x+1 (0x187)', 'корни': 'α^(11·(112+i)), i=0…31', 'двойной базис': 'Berlekamp (таблицы gen_ccsds_tal.c)', 'перемежение': 'I=1…8 (CCSDS 131.0)'},
             'телеметрия CCSDS (запись области kosmos — по стандарту); здесь — открытая реализация как второй источник',
             [I(FL, 'строка %d' % stroka(FL, 'init_rs_char(8,0x187,112,11,32,0)'), 'https://github.com/quiet/libfec/blob/master/gen_ccsds.c'), I(FLX, 'строка %d' % stroka(FLX, '0x187'), 'https://github.com/torvalds/linux/blob/master/lib/reed_solomon/test_rslib.c')],
             'порождающий многочлен симметричен (свойство fcr=112, prim=11, 2t=32 — расчёт); те же параметры в test_rslib.c ядра Linux',
             'ЕСТЬ в проекте: dlinnye.py (_TAL — двойной базис), rs_bch.py'))
for sym, gp, fcs, prim, nr in tab:
    zap.append(Z('РС тестовый кодек ядра Linux: символ %d бит, поле 0x%X, fcr=%d, prim=%d, 2t=%d' % (sym, gp, fcs, prim, nr), 'РС lib/reed_solomon (Linux)', 'РС',
                 {'n,k': '%d,%d' % ((1 << sym) - 1, (1 << sym) - 1 - nr), 'поле': '0x%X' % gp, 'fcr': fcs, 'prim': prim, 'проверочных': nr},
                 'набор проверки rslib (используется NAND, DM-verity FEC, rs-ecc драйверов)', [I(FLX, 'строка %d' % stroka(FLX, '{%d,\t0x%x,\t%d' % (sym, gp, fcs)), 'https://github.com/torvalds/linux/blob/master/lib/reed_solomon/test_rslib.c')],
                 'поле примитивно (таблица логарифмов строится полностью — rs_gf.GF)', SL_RS))
zap.append(Z('CIRC компакт-диска: C2 (28,24) и C1 (32,28) над GF(256) 0x11D, корни α^0…α^3', 'Оптические диски (ECMA-130 / IEC 60908)', 'каскадный: РС + перемежитель (CIRC)',
             {'поле': 'P(x) = x^8+x^4+x^3+x^2+1, α=(00000010)', 'C2': '(28,24), 4 байта Q', 'C1': '(32,28), 4 байта P', 'проверочные матрицы': 'H_P, H_Q: строки 1, α^i, α^2i, α^3i (корни α^0…α^3)',
              'перемежение': 'вторая секция задержек 0…27·D, D=4 F1-кадра; третья — задержка 1 кадр через байт; макс. задержка 108 кадров', 'инверсия': 'все биты P и Q инвертируются на выходе',
              'укорочение': 'из (255,251)'},
             'CD-DA, CD-ROM (уровень F1→F2 кадров)', [I(FE[:-4], gde(FE, 'The error correction encoder C1 generates')), I(FE[:-4], 'стр. 44 PDF (H_P, H_Q — изображение, risunki/ecma130_p44.png)')],
             'H_P/H_Q переписаны с изображения стр. 44; поле примитивно (rs_gf)', 'ЧАСТИЧНО: rs_bch.py найдёт C1/C2 при выровненных словах; перемежение CIRC (задержки D) — нет'))
zap.append(Z('RSPC CD-ROM: P (26,24) и Q (45,43) над GF(256) 0x11D, корни α^0, α^1', 'Оптические диски (ECMA-130)', 'произведение РС (RSPC)',
             {'поле': '0x11D', 'P': '43 столбца по 26 (24 + 2 P), H_P = [1…1; α^25…α^1 1]', 'Q': '26 диагоналей по 45 (43 + 2 Q)', 'вход': 'байты 12…2075 сектора, слова по 2 байта (MSB/LSB раздельно)'},
             'CD-ROM Mode 1 (сектор 2352)', [I(FE[:-4], gde(FE, 'The error correction encoding of the Sector is carried out by a Reed-Solomon Product-like Code')), I(FE[:-4], 'стр. 37 PDF (H_P, изображение risunki/ecma130_p37.png)')],
             'H_P переписана с изображения', 'ЧАСТИЧНО: rs_bch.py по словам; раскладка RSPC сектора — нет'))
zap.append(Z('EDC CD-ROM: CRC-32 (x^16+x^15+x^2+1)(x^16+x^2+x+1)', 'Оптические диски (ECMA-130)', 'CRC-подобный',
             {'многочлен': '0x8001801B (произведение)', 'порядок': 'младший бит байта первым; x^0 — в старшем бите байта 2067', 'охват': 'байты 0…2063'},
             'CD-ROM Mode 1', [I(FE[:-4], gde(FE, 'P(x) = (x16 + x15 + x2 + 1) . (x16 + x2 + x + 1)'))],
             'произведение многочленов = poly модели RevEng CRC-32/CD-ROM-EDC', 'ЕСТЬ в проекте: crc_katalog (CRC-32/CD-ROM-EDC), crc.py'))
zap.append(Z('Скремблер CD-ROM: x^15 + x + 1, предустановка 0x0001 после синхрослова сектора', 'Оптические диски (ECMA-130)', 'скремблер',
             {'тип': 'аддитивный, младший бит байта первым', 'охват': 'байты 12…2351 сектора', 'предустановка': '0000 0000 0000 0001'},
             'CD-ROM', [I(FE[:-4], gde(FE, 'fed back according to polynomial x15 + x + 1'))], 'x^15+x+1 примитивен' if primitive((1 << 15) | 3) else '?',
             'ЧАСТИЧНО: skrembler.найти_по_кадру находит кадровую ПСП; подписи CD-ROM нет'))
zap.append(Z('RS-PC DVD: внешний (208,192,17) и внутренний (182,172,11) над GF(256) 0x11D, корни α^0…α^15 / α^0…α^9', 'Оптические диски (ECMA-267)', 'произведение РС (RS-PC)',
             {'поле': '0x11D', 'PO': '16 байт на столбец (j=0…171)', 'PI': '10 байт на строку (i=0…207)', 'блок ECC': '16 скремблированных кадров, 192×172 байт → 208×182',
              'перемежение': '16 строк PO вставляются по одной после каждых 12 строк (кадры записи)'},
             'DVD-ROM (ECMA-267), DVD±R/RW', [I(FD[:-4], gde(FD, 'RS (208,192,17)')), I(FD[:-4], gde(FD, 'RS (182,172,11)'))],
             'формулы GPO = ∏(x+α^k), k=0…15; GPI — k=0…9 прочитаны из текста', 'ЧАСТИЧНО: rs_bch.py; раскладка RS-PC — нет'))
zap.append(Z('EDC DVD: CRC-32 x^32 + x^31 + x^4 + 1', 'Оптические диски (ECMA-267)', 'CRC-подобный', {'охват': 'ID + IED + CPR_MAI + данные 2048 (16 512 бит)'},
             'DVD-ROM', [I(FD[:-4], gde(FD, 'G(x) = x32 + x31 + x4 + 1'))], 'многочлен прочитан из текста', 'ЧАСТИЧНО: crc.py найдёт вслепую; в каталоге RevEng модели нет'))
zap.append(Z('Скремблер DVD: 15-бит регистр, r0 ← r14 ⊕ r10 (рекуррента x^15 + x^4 + 1), предустановка по табл. 3 (16 значений по ID)', 'Оптические диски (ECMA-267)', 'скремблер',
             {'тип': 'аддитивный байтовый (r7…r0 → байт S_k после каждого 8-битового сдвига)', 'предустановки': '0001, 5500, 0002, 2A00, 0004, 5400, 0008, 2800, 0010, 5000, 0020, 2001, 0040, 4002, 0080, 0005 (номер = биты b7…b4 ID)'},
             'DVD-ROM', [I(FD[:-4], gde(FD, 'Scrambled Frames', 1925)), I(FD[:-4], 'стр. 35 PDF (рис. 19, risunki/ecma267_fig19.png)')],
             'отводы r14, r10 — по рисунку 19; многочлен примитивен: %s' % primitive(dvd_scr), 'ЧАСТИЧНО: skrembler.найти_по_кадру'))
j = naid[0]
zap.append(Z('РС JT65 (63,12) над GF(64)', 'Любительская связь (WSJT, K1JT)', 'РС',
             {'n,k': '63,12', 'поле': '%s (x^6+x+1)' % j['poly'], 'корни (все подходящие наборы)': '; '.join('fcr=%d, prim=%d' % (x['fcr'], x['prim']) for x in naid) + ' — одно и то же множество корней', 'порядок слова': '51 проверочный символ%s, затем 12 информационных' % (' (в обратном порядке)' if j['проверочные_в_обратном_порядке'] else ''),
              'после кодера': 'перемежение 7×9 (запись по строкам, чтение по столбцам) и код Грея символов; 64-FSK', 'сообщение': '72 бита (12 × 6 бит)'},
             'JT65 (EME, КВ), WSJT/WSJT-X', [I(FJ[:-4], gde(FJ, 'Solomon code RS(63,12)')), I(FJ[:-4], gde(FJ, 'Interleaving and Gray Coding'))],
             'параметры поля и корней подобраны перебором (все примитивные многочлены 6-й степени, fcr, prim): три примера рис. 2 статьи кодируются точно — подходящих наборов %d' % len(naid),
             'ЧАСТИЧНО: rs_bch.py (GF(64) — да); перемежение и Грей JT65 — нет'))
# сводная РС по областям
sv = []
for a in ('efir', 'kosmos', 'mobilnaya', 'provod'):
    for z in json.load(open(os.path.join(KOR, '..', a, 'katalog.json'))):
        v = str(z.get('вид', ''))
        if 'РС' in v or 'Рида' in z.get('имя', '') or re.search(r'\bRS\b|РС\s*\(', z.get('имя', '')):
            sv.append(dict(область=a, имя=z['имя'], вид=v, параметры=z.get('параметры'), источник=[dict(s, файл='../%s/%s' % (a, s.get('файл', ''))) for s in z.get('источник', [])]))
json.dump(sv, open(os.path.join(OUT, 'svodka_oblastey.json'), 'w'), ensure_ascii=False, indent=1)
print('РС в других областях', len(sv))
sohranit('rs', zap)
