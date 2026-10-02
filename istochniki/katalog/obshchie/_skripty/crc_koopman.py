"""CRC Polynomial Zoo (Ph. Koopman, CMU, CC BY 4.0) → tablicy/crc/koopman_zoo.json.

Запись в зоопарке: (0xea; 0x1d5) <=> (0xab; 0x157) {85,85,2,2} … | имена.
0xea — «неявная +1» (Koopman), 0x1d5 — явная запись, 0xab — зеркальный многочлен.
Проверки (assert): явная = (неявная << 1) | 1; зеркальная пара = reverse(явной); первое число профиля HD
(наибольшая длина данных при HD = 3) = порядок x по модулю g минус ширина — вычислено здесь.
"""
import glob, html, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf2 import order_x, reverse, deg
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'crc', 'koopman_crc')
out = []; n_hd3 = 0
for f in sorted(glob.glob(os.path.join(IST, 'crc*.html')), key=lambda s: int(re.findall(r'\d+', os.path.basename(s))[0])):
    w = int(re.findall(r'\d+', os.path.basename(f))[0])
    raw = open(f, encoding='latin-1').read()
    lines = raw.split('\n')
    t = html.unescape(re.sub(r'<[^>]+>', ' ', raw))
    t = re.sub(r'\s+', ' ', t)
    for m in re.finditer(r'\(\s*0x([0-9a-f]+)\s*;\s*0x([0-9a-f]+)\s*\)\s*<=>\s*\(\s*0x([0-9a-f]+)\s*;\s*0x([0-9a-f]+)\s*\)\s*\{([0-9, ]*)\}(.*?)(?=\(\s*0x[0-9a-f]+\s*;|HD profile|$)', t):
        k, e, rk, re_ = (int(m.group(i), 16) for i in range(1, 5))
        assert e == (k << 1) | 1 and re_ == (rk << 1) | 1 and deg(e) == w, (w, hex(k))
        assert reverse(e) == re_, (w, hex(k))
        prof = [int(x) for x in m.group(5).replace(' ', '').split(',') if x]
        hvost = m.group(6)
        imena = hvost.split('|')[-1].strip() if '|' in hvost else ''
        stroka = next((i + 1 for i, l in enumerate(lines) if ('0x%x' % k) in l), None)
        if prof and w <= 24:
            e_ord = order_x(e, 1 << 25)
            assert e_ord is not None and prof[0] == e_ord - w, (w, hex(k), prof[0], e_ord)
            n_hd3 += 1
        out.append(dict(ширина=w, koopman='0x%x' % k, явная='0x%x' % e, зеркальная_koopman='0x%x' % rk, зеркальная_явная='0x%x' % re_,
                        профиль_HD_от_3=prof, имена=imena, файл='istochniki/crc/koopman_crc/' + os.path.basename(f), строка=stroka))
json.dump(out, open(os.path.join(KOR, 'tablicy', 'crc', 'koopman_zoo.json'), 'w'), ensure_ascii=False, indent=1)
print('многочленов', len(out), 'проверено по порядку x (HD=3)', n_hd3)
