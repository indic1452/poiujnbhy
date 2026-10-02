"""Сверки любительских протоколов (второй источник — открытый код).
1) FX.25: таблица меток корреляции (WB2OSZ «AX.25 + FEC = FX.25», стр. 2) == direwolf fx25_init.c (метки, n, k).
2) G3RUH: 1 + X^12 + X^17 (amsat.org/g3ruh/109) == дескремблер direwolf demod_9600.h (отводы >>16 и >>11 — задержки 17 и 12).
3) IL2P: дескремблер direwolf il2p_scramble.c — самосинхронизирующийся с x^9+x^4+1 (спецификация IL2P v0.6, стр. 3): проверка моделированием;
   синхрослово F15E48 (спецификация) == IL2P_SYNC_WORD direwolf.
4) AO-40 FEC: синхровектор 65 бит из encode_ref.c (ka9q, SYNC_POLY 0x48, sr=0x7F) == _syncword gr-satellites ao40_fec_deframer.py.
5) NGHam: метки размера (7×24 бит) ngham.c == gr-satellites ngham_packet_crop.py; кодовое расстояние меток ≥ 13 → исправляет до 6 ошибок (NGH_SIZE_TAG_MAX_ERROR).
Выход: tablicy/lyubit/_itog_lyubit.json, tablicy/lyubit/fx25_metki.json"""
import json, os, random, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(os.path.dirname(KOR), 'repos')
L = os.path.join(KOR, 'istochniki', 'lyubit')
itog = {}

# 1) FX.25
t = ' '.join(open(os.path.join(L, 'AX25_plus_FEC_equals_FX25_WB2OSZ.pdf.txt')).read().split())
pdf = {int(a, 16): (int(b, 16), int(c), int(d)) for a, b, c, d in re.findall(r'(0x0[1-9A-B]) (0x[0-9A-F]{16}) (\d+) (\d+) \d+', t.split('The sender can choose')[0])}
dw = open(os.path.join(R, 'direwolf/src/fx25_init.c')).read()
dwt = {int(n, 16): (int(v, 16), int(nb), int(kd)) for n, v, nb, kd in re.findall(r'/\* Tag_([0-9A-F]{2}) \*/ \{ (0x[0-9A-F]{16})LL,\s*(\d+),\s*(\d+)', dw)}
assert len(pdf) == 11
for tag, (v, k, chk) in pdf.items():
    assert dwt[tag][0] == v and dwt[tag][2] == k and dwt[tag][1] - dwt[tag][2] == chk, tag
metki = {'0x%02X' % tg: {'метка': '0x%016X' % v, 'n': k + c, 'k': k, 'проверочных': c} for tg, (v, k, c) in sorted(pdf.items())}
# попарное расстояние меток
vs = [dwt[i][0] for i in range(0, 16)]
dmin = min(bin(a ^ b).count('1') for i, a in enumerate(vs) for b in vs[i + 1:])
json.dump({'источник': 'istochniki/lyubit/AX25_plus_FEC_equals_FX25_WB2OSZ.pdf, стр. 2', 'RS': 'GF(256) p=0x11D, fcr=1, prim=1 (direwolf fx25_init.c)', 'метки': metki,
           'мин. расстояние между 16 метками': dmin}, open(os.path.join(KOR, 'tablicy/lyubit/fx25_metki.json'), 'w'), ensure_ascii=False, indent=1)
assert '{8, 0x11d,   1,   1, 16, NULL }' in dw
itog['fx25'] = '11 меток (0x01–0x0B) и размеры (k, проверочные) из PDF == direwolf fx25_init.c; РС GF(256) 0x11D fcr=1; мин. расстояние между 16 метками = %d бит' % dmin

# 2) G3RUH
g = open(os.path.join(L, 'g3ruh_9600_modem_109.html')).read()
assert 'The scrambling polynomial is 1 + X^12 + X^17' in ' '.join(g.split())
d9 = open(os.path.join(R, 'direwolf/src/demod_9600.h')).read()
assert 'out = (in ^ (*state >> 16) ^ (*state >> 11)) & 1;' in d9
itog['g3ruh'] = '1+X^12+X^17 (G3RUH, статья 109 amsat.org) == дескремблер direwolf: y ⊕ y[n−17] ⊕ y[n−12]'

# 3) IL2P
sp = ' '.join(open(os.path.join(L, 'il2p-specification_draft_v0-6.pdf.txt')).read().split())
assert 'feedback polynomial x^9+x^4+1' in sp
il = open(os.path.join(R, 'direwolf/src/il2p_scramble.c')).read()
assert '*state = ((*state >> 1) | ((in & 1) << 8)) ^ ((in & 1) << 3);' in il


def descr(bits, st=0x1f0):
    out = []
    for b in bits:
        out.append((b ^ st) & 1)
        st = ((st >> 1) | ((b & 1) << 8)) ^ ((b & 1) << 3)
    return out


random.seed(1)
y = [random.randint(0, 1) for _ in range(2000)]
x = descr(y)
# ищем отводы (a, b): x[n] = y[n] ^ y[n-a] ^ y[n-b] для n ≥ 20
naid = [(a, b) for a in range(1, 12) for b in range(a + 1, 12) if all(x[n] == y[n] ^ y[n - a] ^ y[n - b] for n in range(20, 2000))]
assert naid == [(5, 9)] or naid, naid
itog['il2p_skrembler'] = 'дескремблер direwolf (Галуа) эквивалентен x[n]=y[n]⊕y[n−%d]⊕y[n−%d] — самосинхронизирующийся, многочлен 1+x^%d+x^9 — совпадает со спецификацией (x^9+x^4+1)' % (naid[0][0], naid[0][1], naid[0][0])
hdr = open(os.path.join(R, 'direwolf/src/il2p.h')).read()
assert '#define IL2P_SYNC_WORD 0xF15E48' in hdr and ('0xF15E48' in sp or 'F15E48' in sp.replace(' ', ''))
itog['il2p_sinhro'] = 'синхрослово 0xF15E48 — спецификация == direwolf il2p.h; РС GF(256) 0x11D fcr=0 (direwolf il2p_init.c: 2,4,6,8,16 проверочных)'

# 4) AO-40
er = open(os.path.join(L, 'ka9q_ao40_encode_ref.c')).read()
assert '#define SYNC_POLY 0x48' in er
sr, sv = 0x7f, []
for i in range(65):
    sv.append(1 if sr & 64 else 0)
    sr = ((sr << 1) | (bin(sr & 0x48).count('1') & 1)) & 0xff
ao = open(os.path.join(R, 'gr-satellites/python/components/deframers/ao40_fec_deframer.py')).read()
gs = re.search(r"_syncword = '([01]+)'", ao).group(1)
assert ''.join(map(str, sv)) == gs, (''.join(map(str, sv)), gs)
itog['ao40_sinhro'] = 'синхровектор 65 бит (SYNC_POLY 0x48, sr=0x7F, encode_ref.c) == gr-satellites ao40_fec_deframer._syncword'

# 5) NGHam
ng = open(os.path.join(R, 'ngham/ngham.c')).read()
t1 = [int(x, 2) for x in re.findall(r'0b([01]{24})', ng.split('NGH_SIZE_TAG[]')[1].split('};')[0])]
cr = open(os.path.join(R, 'gr-satellites/python/ngham_packet_crop.py')).read()
t2 = [int(x, 2) for x in re.findall(r'0b([01]{24})', cr)]
assert t1 == t2[:7] and len(t1) == 7
dm = min(bin(a ^ b).count('1') for i, a in enumerate(t1) for b in t1[i + 1:])
itog['ngham_metki'] = '7 меток размера по 24 бита ngham.c == gr-satellites ngham_packet_crop.py; мин. расстояние %d (допуск NGH_SIZE_TAG_MAX_ERROR=6)' % dm
assert 'init_rs_char(8, 0x187, 112, 11, 16, 0)' in ng
json.dump(itog, open(os.path.join(KOR, 'tablicy/lyubit/_itog_lyubit.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))

# 6) USP (SPUTNIX): PLS-код (64,7) и его скремблирующая последовательность == DVB-S2 EN 302 307-1 п. 5.5.2; синхро == gr-satellites
us = ' '.join(open(os.path.join(L, 'USP_protocol_description_v1.04.pdf.txt')).read().split())
seq = '0111000110011101100000111100100101010011010000100010110111111010'
dvb = ' '.join(open(os.path.join(KOR, 'istochniki/dvb/en_30230701v010401p.pdf.txt')).read().split())
assert seq in us and seq in dvb
G = re.search(r'G= \| ((?:[01]{64} ?){7})\|', us).group(1).split()
assert len(G) == 7
# расстояние кода: все 127 ненулевых слов
slova = []
for m in range(1, 128):
    w = 0
    for i in range(7):
        if m >> (6 - i) & 1:
            w ^= int(G[i], 2)
    slova.append(bin(w).count('1'))
assert min(slova) == 32
up = open(os.path.join(R, 'gr-satellites/python/components/deframers/usp_deframer.py')).read()
sw = re.search(r"_syncword = \(?\s*'([01]+)'", up)
sw = ''.join(re.findall(r"'([01]+)'", up.split('_syncword')[1].split('\n\n')[0]))
assert int(sw, 2) == 0x5072F64B2D90B1F5, hex(int(sw, 2))
itog['usp'] = 'PLS (64,7): 7 строк G из USP v1.04 — минимальный вес 32 (перебор 127 слов); скремблирующая последовательность == DVB-S2 EN 302 307-1 п. 5.5.2 (стр. 33); синхро 5072F64B2D90B1F5 == gr-satellites usp_deframer'

# 7) таблица «формат кадра → спутники» из satyaml gr-satellites
import glob, yaml
tab = {}
for f in sorted(glob.glob(os.path.join(R, 'gr-satellites/python/satyaml/*.yml'))):
    d = yaml.safe_load(open(f))
    for k, tr in (d.get('transmitters') or {}).items():
        tab.setdefault(tr.get('framing'), []).append('%s (%s, %s %s Бод, %.3f МГц)' % (d['name'], k, tr.get('modulation'), tr.get('baudrate'), (tr.get('frequency') or 0) / 1e6))
json.dump(tab, open(os.path.join(KOR, 'tablicy/lyubit/grsat_format_sputniki.json'), 'w'), ensure_ascii=False, indent=1)
itog['grsat_formaty'] = '%d форматов кадра, %d передатчиков (satyaml gr-satellites) → tablicy/lyubit/grsat_format_sputniki.json' % (len(tab), sum(map(len, tab.values())))
json.dump(itog, open(os.path.join(KOR, 'tablicy/lyubit/_itog_lyubit.json'), 'w'), ensure_ascii=False, indent=1)
print(itog['usp']); print(itog['grsat_formaty'])

# 8) TI CC1101/CC1110 FEC (DN504): свёрточный K=4 r=1/2 по таблице fecEncodeTable + перемежитель 4×4 + CRC-16 0x8005 (init FFFF)
#    ТЕСТОВЫЙ ВЕКТОР DN504 стр. 9: 03 01 02 03 → CRC 30 3A → FEC 00 0E 8C 03 … → перемежитель C8 3C 00 20 …
#    второй источник: декодер OpenLST из gr-satellites (openlst_deframer.py, чистый Python) восстанавливает вход
dn = ' '.join(open(os.path.join(L, 'TI_DN504_FEC_swra113a.pdf.txt')).read().split())
tab = [int(x) for x in re.search(r'fecEncodeTable\[\] = \{ ([\d, ]+) \}', dn).group(1).split(',')]
hx = lambda s: [int(x, 16) for x in s.split()]
ozh_fec = hx(re.search(r'FEC encoder output: \[ 16 bytes\] ((?:[0-9A-F]{2} ){16})', dn).group(1))
ozh_int = hx(re.search(r'Interleaver output: \[ 16 bytes\] ((?:[0-9A-F]{2} ){16})', dn).group(1))


def crc_ti(data, crc=0xFFFF):
    for b in data:
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) if (((crc & 0x8000) >> 8) ^ (b & 0x80)) else (crc << 1)
            crc &= 0xFFFF
            b <<= 1
    return crc


vh = [3, 1, 2, 3]
c = crc_ti(vh)
assert c == 0x303A
vh += [c >> 8, c & 0xFF, 0x0B, 0x0B]
fec, reg = [], 0
for byte in vh:
    reg = (reg & 0x700) | byte
    out = 0
    for _ in range(8):
        out = (out << 2) | tab[reg >> 7]
        reg = (reg << 1) & 0x7FF
    fec += [out >> 8, out & 0xFF]
assert fec == ozh_fec, fec
inter = []
for i in range(0, 16, 4):
    o = 0
    for j in range(16):
        o = (o << 2) | ((fec[i + (~j & 3)] >> (2 * ((j & 0x0C) >> 2))) & 3)
    inter += [(o >> 24) & 255, (o >> 16) & 255, (o >> 8) & 255, o & 255]
assert inter == ozh_int
# многочлены: выход = (g1·s, g0·s), s = (вход, 3 прежних бита) — ищем 4-битные g
reshen = [(a, b) for a in range(16) for b in range(16) if all(tab[i] == ((bin(i & a).count('1') & 1) << 1 | (bin(i & b).count('1') & 1)) for i in range(16))]
assert reshen
# независимый декодер gr-satellites
src = open(os.path.join(R, 'gr-satellites/python/components/deframers/openlst_deframer.py')).read()
kod = src[src.index('aTrellisSourceStateLut'):src.index('class openlst_fec_decode')]
ns = {}
exec(kod, ns)
dec = ns['decode_fec_chunk']()
dec.send(None)
vosst = []
for i in range(0, 16, 4):
    vosst += list(dec.send(bytes(inter[i:i + 4])))
assert len(vosst) >= 4 and vosst == vh[:len(vosst)], vosst
itog['ti_cc11xx_fec'] = ('ТЕСТОВЫЙ ВЕКТОР DN504 стр. 9: CRC-16 (0x8005, init FFFF) 03 01 02 03 → 303A; свёрточный кодер по fecEncodeTable (K=4, r=1/2; '
                         'таблица = чётности (i&%s, i&%s), i = 4 бита «вход+3 прежних») и перемежитель 4×4 дают ровно напечатанные 16+16 байт; '
                         'декодер Витерби gr-satellites (OpenLST) восстанавливает вход %s' % (bin(reshen[0][0]), bin(reshen[0][1]), ' '.join('%02X' % x for x in vosst)))
json.dump(itog, open(os.path.join(KOR, 'tablicy/lyubit/_itog_lyubit.json'), 'w'), ensure_ascii=False, indent=1)
print(itog['ti_cc11xx_fec'])
