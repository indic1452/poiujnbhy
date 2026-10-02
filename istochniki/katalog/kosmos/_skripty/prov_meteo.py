"""Сверки метеоспутников.
1) NOAA POES HRPT: синхрослово малого кадра = первые 60 бит ПСП длины 63, g=X^6+X^5+X^2+X+1, регистр из единиц (KLM Users Guide, стр. 180/183)
   == константа SatDump HRPT_MINOR_FRAME_SYNC 0x0A116FD719D83C95 (младшие 60 бит).
Выход: tablicy/meteo/_itog_meteo.json"""
import json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS = os.path.join(os.path.dirname(KOR), 'repos')
itog = {}
t = open(os.path.join(KOR, 'istochniki/meteo/NOAA_KLM_Users_Guide_0.0_star.pdf.txt')).read()
assert 'First 60 bits from 63 bit PN generator started in the all 1\'s state. The generator polynomial is X6+X5+X2+X+1' in ' '.join(t.split())


def pn(otvody, n, dlina):
    r = [1] * n; out = []
    for _ in range(dlina):
        out.append(r[-1])
        fb = 0
        for o in otvody:
            fb ^= r[o - 1]
        r = [fb] + r[:-1]
    return out


sd = open(os.path.join(REPOS, 'SatDump/plugins/noaa_metop_support/noaa/noaa_deframer.cpp')).read()
k = int(re.search(r'HRPT_MINOR_FRAME_SYNC (0x[0-9A-Fa-f]+)LL', sd).group(1), 16)
sd_bity = [(k >> (59 - i)) & 1 for i in range(60)]
# генератор Галуа: сдвиг влево, при выходе за 6 разрядов — XOR с x^6+x^5+x^2+x+1; начальное состояние 111111; выход — старший разряд
P = (1 << 6) | (1 << 5) | (1 << 2) | (1 << 1) | 1
r, bity = 0b111111, []
for _ in range(63):
    bity.append((r >> 5) & 1)
    r <<= 1
    if r & 64:
        r ^= P
per = next(p for p in range(1, 64) if all(bity[i] == bity[(i + p) % 63] for i in range(63)))
assert per == 63 and bity[:60] == sd_bity
itog['noaa_hrpt_sync'] = ('синхрослово малого кадра 60 бит = %s = первые 60 бит m-последовательности (период 63) генератора Галуа '
                          'x^6+x^5+x^2+x+1 из состояния 111111, выход — старший разряд; == SatDump HRPT_MINOR_FRAME_SYNC 0x0A116FD719D83C95 (младшие 60 бит)'
                          % ' '.join(''.join(map(str, bity[i:i + 10])) for i in range(0, 60, 10)))
os.makedirs(os.path.join(KOR, 'tablicy', 'meteo'), exist_ok=True)
json.dump(itog, open(os.path.join(KOR, 'tablicy', 'meteo', '_itog_meteo.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
