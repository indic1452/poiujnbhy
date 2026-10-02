"""MIL-STD-188-141B (ALE 2G/3G), прил. C: свёрточные коды BW1/BW2/BW3, CRC-12/16/32, ПСП 2^16−1 → zapisi/mil188141.json.
Проверки (assert): многочлены прочитаны из текста; dfree пересчитан и они совпадают (как множества) с MFD-кодами IT++ тех же (n, K);
CRC-32 = poly CRC-32/ISO-HDLC RevEng; ПСП: отводы рис. C-12 (с изображения risunki/mil141_figC12_zoom.png) дают период 2^16−1 (моделирование)."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from svertka_spektr import spektr
from zap import KOR, I, Z, sohranit, gde
F = 'istochniki/golei/MIL-STD-188-141B_ALE.pdf.txt'
t = re.sub(r'\s+', ' ', open(os.path.join(KOR, F)).read())
def poly(s):
    v = 0
    for term in s.replace(' ', '').split('+'):
        term = term.lower()
        if term == '1': v |= 1
        elif term == 'x': v |= 2
        else: v |= 1 << int(term[1:])
    return v
mfd = {(r['n'], r['K']): sorted(r['многочлены']) for r in json.load(open(os.path.join(KOR, 'tablicy', 'svertochnye', 'itpp_mfd_ods.json'))) if r['тип'] == 'MFD'}
kody = []
m1 = re.search(r'Bitout0 = (x8[^•]*?)•\s*Bitout1 = (x8[^•]*?)•\s*Bitout2 = (x8[^w]*?) where x8', t)
kody.append(('BW1', 3, 9, [m1.group(i).strip() for i in (1, 2, 3)], 'хвост кольцевой (tail-biting): регистр предзагружен последними 8 битами нагрузки p40…p47; 144 кодовых бита, без бит сброса', 'Bitout0 = x8+x7+x6+x3+1'))
m2 = re.search(r'1\. Bitout0: (X7[^B]*?) 2\. Bitout1: (X7[^B]*?) 3\. Bitout2: (X7[^B]*?) 4\. Bitout3: (X7[^,]*?),', t)
kody.append(('BW2', 4, 8, [m2.group(i).strip() for i in (1, 2, 3, 4)], '7 нулевых бит сброса в конце каждого EDataPkt (возврат в нулевое состояние)', 'Bitout0:  X7 + X4 + X3 + X2 + 1'))
m3 = re.search(r'Bitout0: (X6\+X4\+X3\+X\+1).*?Bitout1: (X6\+X5\+X4\+X3\+1)', t)
kody.append(('BW3', 2, 7, [m3.group(1), m3.group(2)], '7 бит сброса (6 нужно + 1 для кратности 4); в передаче № FTcount идут только биты EBlk(FTcount mod 2) — наращиваемая избыточность', 'Bitout0: X6+X4+X3+X+1'))
zap = []
for bw, n, K, ps, hvost, obr in kody:
    g = [poly(p) for p in ps]
    assert all(x >> (K - 1) for x in g)
    r = spektr(g, K, terms=3)
    sovp = sorted('%o' % x for x in g) == mfd.get((n, K))
    assert sovp, (bw, ['%o' % x for x in g], mfd.get((n, K)))
    zap.append(Z('MIL-STD-188-141B 3G-ALE %s: свёрточный R=1/%d K=%d (%s)' % (bw, n, K, ', '.join('%o' % x for x in g)), 'MIL-STD-188-141B (ALE)', 'свёрточный',
                 {'n,k': '%d,1' % n, 'K': K, 'многочлены': '; '.join(ps) + ' (старшая степень — самый новый бит; восьм. ' + ', '.join('%o' % x for x in g) + ')', 'хвост': hvost, 'dfree': r['dfree']},
                 'КВ-связь: 3G ALE / HDL / LDL (MIL-STD-188-141B прил. C, %s)' % bw, [I(F[:-4], gde(F, obr))],
                 'многочлены прочитаны из текста; dfree пересчитан = %d; набор многочленов = MFD IT++ для R=1/%d K=%d' % (r['dfree'], n, K),
                 'ЕСТЬ в проекте: svyortka.py (n ≤ 4), svyortka.кодировать_кольцо (tail-biting)' if n <= 4 else 'ЧАСТИЧНО'))
# CRC
rv = json.load(open(os.path.join(KOR, 'tablicy', 'crc', 'reveng.json')))
for w, s, obr in ((32, 'X32 + X26 + X23 + X22 + X16 + X12 + X11 + X10 + X8 + X7 + X5 + X4 + X2 + X1 + 1', 'X32 + X26 + X23 + X22 + X16 + X12'),
                  (12, 'X12 + X11 + X9 + X8 + X7 + X6 + X3 + X2 + X1 + 1', 'X12 + X11 + X9 + X8 + X7 + X6 + X3 + X2 + X1 + 1'),
                  (16, 'X16 + X15 + X11 + X8 + X6 + X5 + X4 + X3 + X1 + 1', 'X16 + X15 + X11 + X8 + X6 + X5 + X4 + X3 + X1 + 1')):
    assert s in t
    p = poly(s.replace('X1 +', 'X +')) & ((1 << w) - 1)
    imena = [m['имя'] for m in rv if m['width'] == w and int(m['poly'], 16) == p]
    zap.append(Z('MIL-STD-188-141B 3G-ALE CRC-%d (%s)' % (w, '0x%0*X' % (w // 4, p)), 'MIL-STD-188-141B (ALE)', 'CRC-подобный',
                 {'многочлен': s, 'poly': '0x%0*X' % (w // 4, p), 'процедура': 'п. C.4.1 стандарта', 'модели RevEng с тем же poly': imena or 'нет'},
                 'КВ 3G-ALE/HDL/LDL (CRC-32 — пакеты данных BW2/BW3; CRC-12 — TM PDU; CRC-16 — HDL_ACK)', [I(F[:-4], gde(F, obr))],
                 'многочлен прочитан из текста; сверка с каталогом RevEng по poly: %s' % (', '.join(imena) if imena else 'совпадений нет (нестандартная модель)'),
                 'ЕСТЬ в проекте: crc.py (поиск init/xorout/отражений вслепую по известному poly)'))
# ПСП 2^16-1, рис. C-12: 16 ячеек b15 (вход, слева) … b0 = B0; отводы — ячейки 1, 2, 11 (слева) и B0 → b15, b14, b5, b0; сдвиг вправо
def period(state0):
    s = state0; n = 0
    while True:
        fb = ((s >> 15) ^ (s >> 14) ^ (s >> 5) ^ s) & 1
        s = (s >> 1) | (fb << 15); n += 1
        if s == state0: return n
per = period(0xAB91)
zap.append(Z('MIL-STD-188-141B 3G-ALE BW2: ПСП 2^16 − 1 для расширения символов (PN spreading)', 'MIL-STD-188-141B (ALE)', 'скремблер (ПСП, по модулю 8)',
             {'регистр': '16 ячеек; обратная связь — сумма ячеек 1-й, 2-й, 11-й (считая от входа) и B0 подаётся на вход (рис. C-12)', 'начальное': '(0xAB91 + FTcount) mod 0x10000 в начале каждой передачи',
              'применение': 'на символ: 3 такта, B2B1B0 — 3-битовый символ, складывается по модулю 8 с символом 8-PSK'},
             'КВ 3G-ALE BW2 (HDL)', [I(F[:-4], gde(F, 'generator is initialized to (0xAB91 + FTcount)')), I(F[:-4], 'стр. 313 PDF, рис. C-12 (изображение risunki/mil141_figC12_zoom.png)')],
             ('отводы с изображения; моделирование: период = %d = 2^16 − 1 (как в названии рисунка)' % per) if per == 65535 else
             'РАСХОЖДЕНИЕ: отводы рис. C-12 (ячейки 1, 2, 11, 16 от входа; многочлен x^16+x^15+x^14+x^5+1) дают период %d, а не 2^16−1, как в названии рисунка — рисунок неточен или прочитан неверно; второго источника (MIL-STD-188-141C/D прил. C) нет — не использовать без проверки по записи сигнала' % per, 'ЧАСТИЧНО: skrembler.py (аддитивные ПСП); сложение по модулю 8 символов — нет'))
sohranit('mil188141', zap)
