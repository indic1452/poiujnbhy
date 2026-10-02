"""Globalstar Simplex (реконструкция C. Moore, Black Hat 2015 / PoC||GTFO 9:3):
1) ПСП расширения: m-последовательность 255 чипов, GLFSR степени 8, «маска 166», «затравка 59» — воспроизводим напечатанные 255 чипов;
2) CRC-24 пакета (код на Python из PoC||GTFO, многочлен 0114377431₈, init FFFFFF, инверсия) — проверяем на образце пакета 144 бита из статьи Black Hat.
Выход: tablicy/globalstar/_itog_globalstar.json"""
import json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'globalstar')
bh = ' '.join(open(os.path.join(IST, 'Moore_BH15_Globalstar_Simplex_wp.pdf.txt')).read().split())
pc = ' '.join(open(os.path.join(IST, 'PoCorGTFO_09-03_Breaking_Globalstar.pdf.txt')).read().split())
itog = {}
pn = ''.join(re.search(r'Actual PN Sequence: ([01 ]+?) Simplex', bh).group(1).split())
assert len(pn) == 255
assert 'Shift Register Mask 166 Shift Register Seed 59' in bh
najd = []
for maska in (166, 0o166):
    for seed in (59,):
        for vyh in ('lsb', 'msb'):
            for sdvig in ('prav', 'lev'):
                s, out = seed, []
                for _ in range(255):
                    if sdvig == 'prav':
                        b = s & 1
                        out.append(b if vyh == 'lsb' else (s >> 7) & 1)
                        s >>= 1
                        if b:
                            s ^= maska
                    else:
                        b = (s >> 7) & 1
                        out.append(b if vyh == 'msb' else s & 1)
                        s = (s << 1) & 0xFF
                        if b:
                            s ^= maska
                if ''.join(map(str, out)) == pn:
                    najd.append((maska, seed, vyh, sdvig))
# независимо: m-последовательность ли это, и её многочлен (Берлекэмп — Мэсси)
def bm(bits):
    n = len(bits); c = [1] + [0] * n; b = [1] + [0] * n; L, m = 0, -1
    for i in range(n):
        d = bits[i]
        for j in range(1, L + 1):
            d ^= c[j] & bits[i - j]
        if d:
            t = c[:]
            for j in range(n - i + m):
                if b[j]:
                    c[j + i - m] ^= 1
            if 2 * L <= i:
                L, m, b = i + 1 - L, i, t
    return L, c[:L + 1]
L, c = bm([int(x) for x in pn] * 2)
itog['psp'] = '255 чипов из статьи: линейная сложность %d, связующий многочлен %s (степень 8 → m-последовательность периода 255); генератор Галуа маска/затравка из статьи воспроизводит последовательность: %s' % (
    L, '+'.join('x^%d' % i for i, v in enumerate(c) if v), najd or 'нет (запись маски иная)')
# CRC-24
obr = ''.join(re.search(r'S a m p l e D a t a ([01 ]+)', bh).group(1).split())
assert '0114377431' in pc.replace(' ', '')
poly = 0o114377431 & 0xFFFFFF


def crc24(bits):
    crc = 0xFFFFFF
    for k in range(14):
        t = int(bits[k * 8 + 8:k * 8 + 16], 2)
        if k == 0:
            t &= 0x3F
        crc ^= t << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0o114377431
    return (~crc) & 0xFFFFFF


itog['obrazec_dlina'] = len(obr)
if len(obr) == 144:
    ok = crc24(obr) == int(obr[120:144], 2)
    itog['crc24'] = 'образец пакета 144 бита (Black Hat, стр. 3): CRC-24 по алгоритму PoC||GTFO (многочлен 0114377431₈ = 0x%06X с x^24) %s' % (poly, 'СОШЁЛСЯ' if ok else 'НЕ сошёлся')
os.makedirs(os.path.join(KOR, 'tablicy', 'globalstar'), exist_ok=True)
json.dump(itog, open(os.path.join(KOR, 'tablicy', 'globalstar', '_itog_globalstar.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
