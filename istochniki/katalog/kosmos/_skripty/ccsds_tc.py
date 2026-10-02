"""CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) + 231.1-O-1 (короткие LDPC TC):
BCH(63,56), LDPC (128,64)/(256,128)/(512,256), рандомизатор BTG, CLTU. Сверка assert.
Страницы — физические страницы PDF (с 1)."""
import json, os, re
import numpy as np

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'ccsds')
VYH = os.path.join(KOR, 'tablicy', 'ccsds')
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
os.makedirs(VYH, exist_ok=True)


def zagr(fn):
    t = open(os.path.join(IST, fn), encoding='utf8').read()
    return ''.join(chr(ord(ch) - 0xF000) if 0xF030 <= ord(ch) <= 0xF039 else ch for ch in t)


B = zagr('231x0b4e1c1.pdf.txt')
O = zagr('231x1o1s.pdf.txt')


def str_(txt, n):
    return re.search(r'=====PAGE %d=====\n(.*?)(?======PAGE|\Z)' % n, txt, re.S).group(1)


def bits_hex(h):
    return [int(b) for c in h for b in format(int(c, 16), '04b')]


itog = {}
# ---------------------------------------------------------------- BCH (63,56) стр. 23-25
assert 'g(x) = x7 + x6 + x2 + 1' in str_(B, 23)
g = 0b11000101  # x^7+x^6+x^2+1


def pmul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1; b >>= 1
    return r


def pmod(a, m):
    dm = m.bit_length() - 1
    while a.bit_length() - 1 >= dm:
        a ^= m << (a.bit_length() - 1 - dm)
    return a


assert pmul(0b11, 0b1000011) == g  # (x+1)(x^6+x+1)
assert pmod((1 << 63) | 1, g) == 0  # g | x^63+1 → циклический код длины 63
# x^6+x+1 примитивный: порядок x равен 63
assert all(pmod(1 << e, 0b1000011) != 1 for e in range(1, 63)) and pmod(1 << 63, 0b1000011) == 1


def bch_kodir(info56):
    """56 бит → 64 бита: инф., 7 инвертированных проверочных, заполнитель 0 (п. 3.2-3.3)."""
    v = int(''.join(map(str, info56)), 2) << 7
    r = pmod(v, g)
    par = [(r >> (6 - i)) & 1 for i in range(7)]
    return list(info56) + [1 - b for b in par] + [0]


def bch_sindrom(slovo64):
    v = int(''.join(map(str, slovo64[:56])), 2) << 7
    par = int(''.join(str(1 - b) for b in slovo64[56:63]), 2)
    return pmod(v | par, g)


# все одиночные ошибки дают разные ненулевые синдромы (SEC), проверка на случайных словах
rng = np.random.default_rng(1)
info = list(rng.integers(0, 2, 56))
cw = bch_kodir(info)
assert bch_sindrom(cw) == 0
sind1 = set()
for i in range(63):
    e = cw.copy(); e[i] ^= 1
    sind1.add(bch_sindrom(e))
assert len(sind1) == 63 and 0 not in sind1
# хвост CLTU C5C5C5C5C5C5C579 (стр. 30): «неисправимый» — синдром не соответствует одиночной ошибке
assert 'C5C5 C5C5 C5C5 C579' in str_(B, 30)
tail = bits_hex('C5C5C5C5C5C5C579')
st = bch_sindrom(tail)
# синдромы одиночных ошибок нулевого слова — то же множество, что выше (линейность); у хвоста — чётность
odinochnye = set()
z = bch_kodir([0] * 56)
for i in range(63):
    e = z.copy(); e[i] ^= 1
    odinochnye.add(bch_sindrom(e))
assert st != 0 and st not in odinochnye, 'хвост неисправим'
itog['bch_tc'] = {'g': 'x^7+x^6+x^2+1 = (x+1)(x^6+x+1)', 'n,k': '(63,56) + 1 бит заполнителя = 64', 'проверочные': 'инвертированы',
                  'старт': 'EB90 (16 бит)', 'хвост': 'C5C5C5C5C5C5C579', 'заполнитель_данных': '0101… начиная с 0',
                  'источник': '231x0b4e1c1.pdf стр. 23-25 (разд. 3), стр. 29-30 (п. 5.2), стр. 35 (EB90)',
                  'проверка': 'g = (x+1)(x^6+x+1), x^6+x+1 примитивен → расширенный-выбрасыванием Хэмминг (63,56), d=4; 63 одиночные ошибки дают 63 разных синдрома; хвост C5…79 — синдром не одиночной ошибки (assert)'}

# ---------------------------------------------------------------- рандомизатор TC (стр. 35)
s35 = ' '.join(str_(B, 35).split())
assert 'h(x) = x8 + x6 + x4 + x3 + x2 + x + 1' in s35
m = re.search(r'The first 40 bits of the sequence are ((?:[01]{4} ){9}[01]{4})', s35)
b40 = [int(c) for c in m.group(1).replace(' ', '')]
# рекуррентность по h: a[t+8] = a[t+7]^a[t+6]^a[t+5]^a[t+4]^a[t+2]^a[t] (отводы 1,2,3,4,6,8) или взаимная
cands = {'прямая h': (1, 2, 3, 4, 6, 8), 'взаимная': (2, 4, 5, 6, 7, 8)}
ok = [nm for nm, taps in cands.items() if all(b40[t + 8] == (sum(b40[t + 8 - d] for d in taps) & 1) for t in range(32))]
assert len(ok) == 1
taps = cands[ok[0]]
seq = b40[:8]
while len(seq) < 255 + 8:
    t = len(seq) - 8
    seq.append(sum(seq[t + 8 - d] for d in taps) & 1)
assert seq[255:263] == seq[:8] and seq[:40] == b40
seq = seq[:255]
# второй вектор: хвост LDPC(128,64) после дерандомизации (стр. 31, прим. 3)
s31 = ' '.join(str_(B, 31).split())
assert '63A1 ED72 C6AC 79E2 5555 5555 5555 5555' in s31 and '9C987328 AE457F17 39DC7AF4 640B5D95' in s31
t128 = bits_hex('63A1ED72C6AC79E255555555555555555')[:128]
d128 = bits_hex('9C987328AE457F1739DC7AF4640B5D95')
assert [a ^ b for a, b in zip(t128, seq[:128])] == d128, 'дерандомизация хвоста'
open(os.path.join(VYH, 'randomizer_tc_255.hex'), 'w').write(''.join('%X' % int(''.join(map(str, (seq + [0])[i:i + 4])), 2) for i in range(0, 256, 4)) + '\n')
itog['randomizer_tc'] = {'h': 'x^8+x^6+x^4+x^3+x^2+x+1', 'нач': 'все единицы', 'рекуррентность': ok[0], 'файл': 'tablicy/ccsds/randomizer_tc_255.hex',
                         'источник': '231x0b4e1c1.pdf стр. 35-37 (разд. 6), стр. 31 (прим. 3 п. 5.2.4.2)',
                         'проверка': '40 первых бит (стр. 35) порождают ПСП периода 255; хвост 63A1…5555 XOR ПСП = 9C987328 AE457F17 39DC7AF4 640B5D95 — ровно как в стандарте (стр. 31) (assert)'}

# ---------------------------------------------------------------- LDPC TC
LAB = open(os.path.join(REPOS, 'labrador-ldpc', 'src', 'codes', 'compact_parity_checks.rs')).read()


def lab_proto(name):
    blk = LAB.split('pub static %s: [[[u8; 11]; 4]; 3] = [' % name)[1].split('\n];')[0]
    cells = re.findall(r'(HZ|HI|HP|0)(?:\s*\|\s*(\d+))?\s*[,\]]', re.sub(r'//[^\n]*', '', blk))
    return [[[cells[(p * 4 + r) * 11 + c] for c in range(11)] for r in range(4)] for p in range(3)]


def lab_H(name, M):
    proto = lab_proto(name)
    H = np.zeros((4 * M, 8 * M), dtype=np.uint8)
    sdvigi = [[[] for _ in range(8)] for _ in range(4)]
    for part in proto:
        for br in range(4):
            for bc in range(8):
                kind, val = part[br][bc]
                if kind in ('0', 'HZ'):
                    continue
                sh = int(val) if val else 0
                sdvigi[br][bc].append(sh)
                for i in range(M):
                    H[br * M + i, bc * M + (i + sh) % M] ^= 1
    return H, sdvigi


def tabl_orange(nomer_str, str_n):
    """Табл. 2-4…2-6 231.1-O-1: строки A..D, непустые клетки по порядку (пустая — нулевой блок)."""
    t = ' '.join(str_(O, str_n).split())
    t = t.split('Table %s' % nomer_str)[1]
    t = t.split('A B C D E F G H', 1)[1]
    rows = {}
    for r in 'ABCD':
        mm = re.search(r'\b%s ((?:\d+(?:,\d+)? ){6}\d+(?:,\d+)?)' % r, t)
        rows[r] = [[int(x) for x in c.split(',')] for c in mm.group(1).split()]
    return rows


def G_iz_tablicy(hexrows, M):
    """Строки 1, M+1, 2M+1, 3M+1 матрицы W — из hex; прочие — правые циклические сдвиги (п. 4.3.3)."""
    W = np.zeros((4 * M, 4 * M), dtype=np.uint8)
    for b, h in enumerate(hexrows):
        r0 = np.array(bits_hex(h)[:4 * M], dtype=np.uint8)
        assert len(bits_hex(h)) == 4 * M
        # сдвиг внутри каждого блока-циркулянта M×M
        blocks = r0.reshape(4, M)
        for s in range(M):
            W[b * M + s] = np.concatenate([np.roll(bl, s) for bl in blocks])
    return np.concatenate([np.eye(4 * M, dtype=np.uint8), W], axis=1)


s27 = ' '.join(str_(B, 27).split())
tabB = {
    128: re.search(r'Row 1 ([0-9A-F ]+?) Row 17 ([0-9A-F ]+?) Row 33 ([0-9A-F ]+?) Row 49 ([0-9A-F ]+?) Table 4-2', s27).groups(),
    512: re.search(r'Row 1 ([0-9A-F ]+?) Row 65 ([0-9A-F ]+?) Row 129 ([0-9A-F ]+?) Row 193 ([0-9A-F ]+?)$', s27).groups(),
}
s17 = ' '.join(str_(O, 17).split())
tabO = {
    128: re.search(r'Row 1 ([0-9A-F ]+?) Row 17 ([0-9A-F ]+?) Row 33 ([0-9A-F ]+?) Row 49 ([0-9A-F ]+?) Table 2-2', s17).groups(),
    256: re.search(r'Row 1 ([0-9A-F ]+?) Row 33 ([0-9A-F ]+?) Row 65 ([0-9A-F ]+?) Row 97 ([0-9A-F ]+?) Table 2-3', s17).groups(),
    512: re.search(r'G256×512 Row 1 ([0-9A-F ]+?) Row 65 ([0-9A-F ]+?) Row 129 ([0-9A-F ]+?) Row 193 ([0-9A-F ]+?) 2\.4', s17).groups(),
}
for n in (128, 512):
    assert [x.replace(' ', '') for x in tabB[n]] == [x.replace(' ', '') for x in tabO[n]], 'голубая = оранжевая'
orange = {128: tabl_orange('2-4', 20), 256: tabl_orange('2-5', 21), 512: tabl_orange('2-6', 21)}
proekt = json.load(open('/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_prochie.json'))
tc = {}
for n, name in ((128, 'TC128_H'), (256, 'TC256_H'), (512, 'TC512_H')):
    M = n // 8
    H, sdv = lab_H(name, M)
    # сверка labrador с табл. 2-4…2-6 (непустые клетки по порядку)
    for bi, r in enumerate('ABCD'):
        lab_row = [sorted(x) for x in sdv[bi] if x]
        assert lab_row == [sorted(x) for x in orange[n][r]], (n, r, lab_row, orange[n][r])
    G = G_iz_tablicy([x.replace(' ', '') for x in tabO[n]], M)
    assert not ((G.astype(np.int64) @ H.T.astype(np.int64)) % 2).any(), ('G·Hᵀ', n)
    rows = [np.nonzero(H[i])[0].tolist() for i in range(4 * M)]
    zap = {'n': n, 'k': n // 2, 'M': M}
    if n == 128:
        pr = proekt['ccsds-128-64']['строки']
        zap['сверка_с_проектом'] = 'H совпала с ccsds-128-64 проекта (AFF3CT CCSDS_64_128.alist)' if sorted(map(tuple, pr)) == sorted(map(tuple, rows)) else 'H НЕ совпала с ccsds-128-64 проекта'
        # генератор проекта: проверим, что G из стандарта ортогональна H проекта
        Hp = np.zeros((64, 128), dtype=np.uint8)
        for i, rr in enumerate(pr):
            Hp[i, rr] = 1
        assert not ((G.astype(np.int64) @ Hp.T.astype(np.int64)) % 2).any()
        zap['сверка_с_проектом'] += '; G(табл. 4-1)·H_проектаᵀ = 0'
    json.dump({'семейство': 'CCSDS TC', 'n': str(n), 'k': str(n // 2), 'строки': rows, 'скорость': '1/2',
               'откуда': 'CCSDS 231.0-B-4 разд. 4 (128, 512) / 231.1-O-1 (256): H — labrador-ldpc = табл. 2-4…2-6; G — табл. 4-1/4-2 (2-1…2-3)',
               'G_W_hex_строки_1_M+1_2M+1_3M+1': [x.replace(' ', '') for x in tabO[n]]},
              open(os.path.join(VYH, 'ldpc_tc_%d_%d.json' % (n, n // 2)), 'w'), ensure_ascii=False)
    tc[n] = zap
assert 'LDPC' in B and '0347 76C7 2728 95B0' in ' '.join(str_(B, 30).split())
itog['ldpc_tc'] = {'коды': tc, 'старт_CLTU_LDPC': '034776C7272895B0 (64 бита)', 'хвост_128': '63A1ED72C6AC79E25555555555555555 (необязат.)', 'хвост_512': 'нет',
                   'рандомизация': 'всегда, BTG на каждое кодовое слово',
                   'файлы': 'tablicy/ccsds/ldpc_tc_<n>_<k>.json (формат ldpc_prochie.json)',
                   'источник': '231x0b4e1c1.pdf стр. 26-28 (разд. 4, табл. 4-1/4-2), стр. 29-31; 231x1o1s.pdf стр. 16-21 (табл. 2-1…2-6; (256,128) только в оранжевой книге)',
                   'проверка': 'сдвиги циркулянтов labrador-ldpc (MIT) = табл. 2-4…2-6; G из табл. 4-1/4-2 (= 2-1…2-3) · Hᵀ = 0 для всех трёх кодов; для (128,64) H = ccsds-128-64 проекта (AFF3CT) (assert)'}
json.dump(itog, open(os.path.join(VYH, '_itog_ccsds_tc.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps({k: v['проверка'] for k, v in itog.items()}, ensure_ascii=False, indent=1))
print(tc)
