"""Матрицы LDPC общего вида из AFF3CT configuration_files (dec/LDPC) → tablicy/ldpc/aff3ct.json, zapisi/aff3ct_ldpc.json.
Стандартные семейства модемов/вещания (DVB-S2, 5G, CCSDS AR4JA, Wi-Fi, WiMAX) здесь не повторяются — они у процесса kody2 и в проекте.
Проверки: alist разобран и согласован (ldpc_alist.read_alist), ранг GF(2); MACKAY_504_1008, MACKAY_4000_8000, PEG_Reg_1008x504 —
сверены с матрицами энциклопедии MacKay (второй источник): совпадение набора строк."""
import gzip, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldpc_alist import read_alist, rank
from zap import KOR, I, Z, sohranit
A = 'istochniki/ldpc/aff3ct/'
MK = 'istochniki/ldpc/mackay/'
SV = {'MACKAY_504_1008.alist': ['504.504.3.504', 'EN/C/PEGReg504x1008.gz'], 'MACKAY_4000_8000.alist': ['EN/C/8000.4000.3.483.gz'],
      'PEG_Reg_1008x504.alist': ['EN/C/PEGReg504x1008.gz', '504.504.3.504']}
OPIS = {'10GBPS-ETHERNET_1723_2048.alist': ('RS-LDPC (2048,1723) 10GBASE-T, IEEE 802.3an', 'Ethernet 10GBASE-T (cl. 55); стандарт — область provod'),
        '10GBPS-ETHERNET_ALT_1723_2048.alist': ('RS-LDPC (2048,1723) 10GBASE-T, альтернативная запись', 'Ethernet 10GBASE-T'),
        'GSM_2112_4224.alist': ('LDPC (4224,2112) «GSM» из набора AFF3CT', 'название набора AFF3CT; к стандарту 3GPP GSM не относится по нашим источникам — происхождение не указано'),
        'MACKAY_4000_8000.alist': ('Gallager/MacKay (8000,4000) j=3', 'исследовательский'),
        'MACKAY_504_1008.alist': ('Gallager/MacKay (1008,504) j=3', 'исследовательский'),
        'PEG_Reg_1008x504.alist': ('PEG регулярный (1008,504)', 'исследовательский (Hu–Eleftheriou–Arnold, PEG)'),
        'Peeling_PureIRA_2400_3000.alist': ('IRA (3000,2400) для декодирования «peeling»', 'исследовательский, IRA'),
        'PureIRA_R_4_5_N16k_H.alist': ('IRA R=4/5 (16640,13312)', 'исследовательский, IRA (DVB-подобная структура накопителя)'),
        'PureIRA_R_4_5_N20k_H.alist': ('IRA R=4/5 (20480,16384)', 'исследовательский, IRA'),
        'PureIRA_R_9_10_N16k_H.alist': ('IRA R=9/10 (16640,14976)', 'исследовательский, IRA'),
        'WRAN_360_480.alist': ('LDPC (480,360) IEEE 802.22 WRAN', 'IEEE 802.22 (когнитивное радио в ТВ-диапазоне)'),
        'DEBUG_6_3.alist': ('отладочный (6,3)', 'пример AFF3CT')}
out = []; zap = []
def rows_set(path):
    N, M, cw, rw, rows, vid = read_alist(path)
    return N, M, set(frozenset(r) for r in rows)
for f, (opis, gde) in OPIS.items():
    p = os.path.join(KOR, A + f)
    N, M, cw, rw, rows, vid = read_alist(p)
    rk = rank(rows, N) if N <= 25000 else None
    sv = []
    for other in SV.get(f, []):
        q = os.path.join(KOR, MK + other)
        if not os.path.exists(q): continue
        N2, M2, rs2 = rows_set(q)
        same = (N2 == N and M2 == M and rs2 == set(frozenset(r) for r in rows))
        sv.append('%s: %s' % (other, 'тот же набор строк' if same else 'другая матрица (N=%d, M=%d)' % (N2, M2)))
    out.append(dict(файл=A + f, N=N, M=M, ранг=rk, K=N - rk if rk is not None else None, веса_столбцов=sorted(set(cw)), веса_строк=sorted(set(rw)), сверка=sv))
    zap.append(Z('LDPC %s (N=%d, M=%d)' % (opis, N, M), 'LDPC общего вида (AFF3CT configuration_files)', 'LDPC',
                 {'n,k': '%d,%s' % (N, N - rk if rk is not None else '?'), 'веса столбцов': sorted(set(cw)), 'веса строк': sorted(set(rw)), 'матрица (alist)': A + f, 'ранг H': rk},
                 gde, [I(A + f, 'весь файл (alist)'), I(A + 'AFF3CT_LICENSE', 'MIT (AFF3CT); у репозитория configuration_files отдельной лицензии нет')],
                 'alist разобран и согласован; ранг GF(2) = %s%s' % (rk, ('; сверка с энциклопедией MacKay: ' + '; '.join(sv)) if sv else ''),
                 'ЕСТЬ в проекте: ldpc.py (загрузка alist, декодирование), dlinnye.py'))
json.dump(out, open(os.path.join(KOR, 'tablicy', 'ldpc', 'aff3ct.json'), 'w'), ensure_ascii=False, indent=1)
for o in out: print(o['файл'].split('/')[-1], o['N'], o['M'], o['K'], o['сверка'])
sohranit('aff3ct_ldpc', zap)
