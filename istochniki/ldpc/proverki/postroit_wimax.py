"""IEEE 802.16e (WiMAX) LDPC: 6 базовых матриц × 19 длин (576 + 96·i) — все 114 кодов.

Базовые матрицы (z0 = 96) — yaldpc lib/IEEE80216_LDPC.m (по IEEE 802.16-2009; сам стандарт платный);
сдвиг для z = n/24: floor(s·z/96), у 2/3A — s mod z (yaldpc lib/loadWIMAX_LDPC.m, rescaleHbm).
Второй источник базовых матриц и правила сдвига — FEC (dshekhalev, rtl/ldpc/ldpc_parameters.svh, по
IEEE 802.16-2012): все шесть матриц совпали с yaldpc. Третий — готовые alist wimax_ldpc_lib: 95 из 114 кодов
совпали целиком, у всех 19 кодов 5/6 расходится один блок (строка 3, столбец 0): в yaldpc и FEC — 68, в
wimax_ldpc_lib — иной сдвиг; принято 68 (два источника против одного), расхождение записано в «откуда». Пишет src/reportgen/potok/data/ldpc_wimax.json. Запуск из корня репозитория.
"""
import json
import os
import re

ZDES = os.path.dirname(os.path.abspath(__file__))
K0 = os.path.normpath(os.path.join(ZDES, '..'))
KOREN = os.path.normpath(os.path.join(K0, '..', '..'))
import sys  # noqa: E402
sys.path.insert(0, ZDES)
from postroit_obshchee import alist_pary  # noqa: E402

m = open(os.path.join(K0, 'otkrytyj_kod', 'yaldpc', 'lib', 'IEEE80216_LDPC.m')).read()
MATRICY = {}
for imya in ('12', '23A', '23B', '34A', '34B', '56'):
    kusok = m[m.index('model_matrix_%s = ...' % imya):]
    kusok = kusok[kusok.index('[') + 1:]
    kusok = kusok[:re.search(r'\]\s*;\s*\n\s*\]\s*;', kusok).end()]
    ryady = [[int(x) for x in re.findall(r'-?\d+', r_)] for r_ in re.findall(r'\[([^\[\]]+)\]', kusok)]
    assert len({len(r_) for r_ in ryady}) == 1 and len(ryady[0]) == 24, imya
    MATRICY[imya] = ryady
fec = open(os.path.join(K0, 'otkrytyj_kod', 'FEC', 'rtl', 'ldpc', 'ldpc_parameters.svh')).read()
for imya, H in MATRICY.items():
    kus = fec[fec.index('Hc_%s = \'{' % imya if imya != '56' else 'Hc_56 = \'{'):]
    kus = kus[:kus.index('}};') + 3]
    ryady = [[int(x) for x in re.findall(r'-?\d+', r_)] for r_ in re.findall(r"'\{([-\d,\s]+)\}", kus)]
    assert ryady == H, ('yaldpc и FEC различаются', imya)
print('WiMAX: шесть базовых матриц yaldpc = FEC (dshekhalev)')
assert [len(MATRICY[k]) for k in ('12', '23A', '23B', '34A', '34B', '56')] == [12, 8, 8, 6, 6, 4]
IMYA_ALIST = {'12': '0_5', '23A': '0_66A', '23B': '0_66B', '34A': '0_75A', '34B': '0_75B', '56': '0_83'}


def sdvig(s, z, imya):
    if s <= 0:
        return s
    return s % z if imya == '23A' else s * z // 96


vsego = 0
rashozhdenie = 0
for n in range(576, 2304 + 1, 96):
    z = n // 24
    for imya, H in MATRICY.items():
        pary = {(r * z + i, c * z + (i + sdvig(s, z, imya)) % z)
                for r, ryad in enumerate(H) for c, s in enumerate(ryad) if s >= 0 for i in range(z)}
        na, ma, pa = alist_pary(os.path.join(K0, 'otkrytyj_kod', 'wimax_ldpc_lib', 'alist', f'wimax_{n}_{IMYA_ALIST[imya]}.alist'))
        assert (na, ma) == (n, len(H) * z), ('WiMAX: размеры', n, imya)
        raznye = {(a // z, b // z) for a, b in pa ^ pary}
        if imya == '56':
            assert raznye == {(3, 0)}, ('WiMAX 5/6: расхождение не там', n, raznye)
            rashozhdenie += 1
        else:
            assert not raznye, ('WiMAX не совпал', n, imya)
            vsego += 1
print(f'WiMAX: {vsego} кодов совпали с alist wimax_ldpc_lib; у {rashozhdenie} кодов 5/6 — расхождение в блоке (3, 0) (там 68 по yaldpc и FEC)')
put = os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_wimax.json')
json.dump({'откуда': 'IEEE 802.16e: базовые матрицы — yaldpc lib/IEEE80216_LDPC.m (IEEE 802.16-2009), сдвиг floor(s·z/96), '
                     'у 2/3A — s mod z (yaldpc rescaleHbm); матрицы и правило совпали с FEC (dshekhalev, IEEE 802.16-2012); с alist wimax_ldpc_lib '
                     'совпали 95 кодов, у 19 кодов 5/6 wimax_ldpc_lib расходится в блоке (3, 0) — принято 68 (yaldpc, FEC)',
           'матрицы': MATRICY}, open(put, 'w', encoding='utf-8'), ensure_ascii=False)
print('ВСЁ СОВПАЛО; записано', put)
