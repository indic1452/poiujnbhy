"""Перемежители общего вида: блочный, перекрёстный (свёрточный Рэмси–Форни), случайный, S-случайный (Dolinar–Divsalar)
→ zapisi/peremezhiteli.json; сводная таблица перемежителей всех областей → tablicy/peremezhiteli/svodka_oblastey.json.
Проверки (assert): блочный и перекрёстный перемежители IT++ реализованы по тексту itpp_interleave.h и проверены на
обратимость; перекрёстный IT++ порядка N = свёрточный Форни I=N, M=1 из проекта (reportgen.potok.forni.перемежить) —
одинаковый выход; S-случайная перестановка строится по правилу отчёта JPL 42-122 и проверяется на свойство разноса."""
import json, os, random, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/home/user/poiujnbhy/src')
from reportgen.potok import forni
from zap import KOR, I, Z, sohranit, stroka, gde
FH = 'istochniki/svertochnye/itpp/itpp_interleave.h'
def block_il(x, R, C):  # запись по строкам, чтение по столбцам (IT++ Block_Interleaver::interleave, обратная к deinterleave)
    out = np.empty_like(x)
    for r in range(R):
        for c in range(C): out[c * R + r] = x[r * C + c]
    return out
def block_deil(y, R, C):  # IT++ deinterleave: output(c*rows + r) = input(r*cols + c)
    out = np.empty_like(y)
    for r in range(R):
        for c in range(C): out[r * C + c] = y[c * R + r]
    return out
def cross_il(x, N):  # IT++ Cross_Interleaver: столбцы сдвигаются, чтение по диагонали → y[iN+r] = x[(i−r)N + r]
    steps = -(-len(x) // N) + N; M = np.zeros((N, N), x.dtype); out = []
    for i in range(steps):
        M[:, 1:] = M[:, :-1].copy()
        col = np.zeros(N, x.dtype); seg = x[i * N:(i + 1) * N]; col[:len(seg)] = seg; M[:, 0] = col
        out += [M[r, r] for r in range(N)]
    return np.array(out)
x = np.arange(1, 1 + 48)
for R, C in ((4, 12), (6, 8), (3, 16)):
    assert (block_deil(block_il(x, R, C), R, C) == x).all()
for N in (2, 3, 4, 6, 12):
    y = cross_il(x, N)
    z = forni.перемежить(np.concatenate([x, np.zeros(N * N, x.dtype)]), N, 1, 0)[:len(y)]
    assert (y == z).all(), N
# S-случайная
def s_random(Nn, S, seed=1):
    rng = random.Random(seed)
    while True:
        rest = list(range(Nn)); rng.shuffle(rest); perm = []; ok = True
        for _ in range(Nn):
            for idx, v in enumerate(rest):
                if all(abs(v - perm[-j]) > S for j in range(1, min(S, len(perm)) + 1)):
                    perm.append(v); rest.pop(idx); break
            else: ok = False; break
        if ok: return perm
p = s_random(256, 8)
assert sorted(p) == list(range(256)) and all(abs(p[i] - p[j]) > 8 for i in range(256) for j in range(max(0, i - 8), i))
json.dump(dict(пример_S_random_N256_S8=p), open(os.path.join(KOR, 'tablicy', 'peremezhiteli', 's_random_primer.json'), 'w'))
FJ = 'istochniki/peremezhiteli/jpl_42-122B_turbo_permutations.pdf'
zap = [
 Z('Блочный перемежитель R×C (запись по строкам, чтение по столбцам)', 'Перемежители общего вида', 'перемежитель (блочный)',
   {'правило': 'y[c·R + r] = x[r·C + c], r = 0…R−1, c = 0…C−1; неполный последний блок дополняется нулями (IT++)', 'деперемежение': 'x[r·C + c] = y[c·R + r]'},
   'повсеместно: GSM/ТЕТРА (диагональные), 802.11 (двухступенчатый), JT65 (7×9), ALE и др. — конкретные таблицы в записях областей',
   [I(FH, 'строки %d–%d (Block_Interleaver), %d–… (deinterleave)' % (stroka(FH, '\\class Block_Interleaver'), stroka(FH, '\\class Block_Interleaver') + 15, stroka(FH, 'void Block_Interleaver<T>::deinterleave')), 'https://github.com/nvmd/itpp/blob/master/itpp/comm/interleave.h')],
   'реализация по тексту IT++; перемежение∘деперемежение = тождество для 4×12, 6×8, 3×16', 'ЕСТЬ в проекте: peremezhenie.py (глубина R вслепую по прореживанию, сборка строк)'),
 Z('Перекрёстный (свёрточный) перемежитель порядка N — IT++ Cross_Interleaver (Рэмси/Форни I=N, M=1)', 'Перемежители общего вида', 'перемежитель (свёрточный)',
   {'правило': 'y[i·N + r] = x[(i − r)·N + r] (ветвь r задержана на r блоков по N символов)', 'общий вид Форни': 'I ветвей, ветвь j — задержка j·M ячеек; символ k → ветвь k mod I (DVB: I=12, M=17 байт — записи efir/kosmos)',
    'ссылка IT++': 'S. B. Wicker, Error control systems…, 1995, p. 427'},
   'DVB-S/C/T (I=12, M=17), ATSC (I=52), DOCSIS/J.83, IESS — параметры в записях областей; модемы',
   [I(FH, 'строки %d–… (Cross_Interleaver), %d–… (interleave)' % (stroka(FH, '\\class Cross_Interleaver'), stroka(FH, 'void Cross_Interleaver<T>::interleave')), 'https://github.com/nvmd/itpp/blob/master/itpp/comm/interleave.h'),
    I('istochniki/peremezhiteli/US4547887_pseudorandom_conv_interleaving.pdf', 'патент (псевдослучайное свёрточное перемежение)'), I('istochniki/peremezhiteli/US6697975_generalized_conv_interleaver.pdf', 'патент (обобщённый свёрточный перемежитель)'),
    I('istochniki/peremezhiteli/US5764649_conv_interleaver_address.pdf', 'патент (адресация свёрточного перемежителя в одной памяти)')],
   'реализация по тексту IT++ даёт тот же выход, что forni.перемежить(I=N, M=1) проекта, для N = 2, 3, 4, 6, 12', 'ЕСТЬ в проекте: forni.py (I, M, фаза — вслепую)'),
 Z('Случайный (последовательностный) перемежитель — IT++ Sequence_Interleaver', 'Перемежители общего вида', 'перемежитель (перестановка)',
   {'правило': 'произвольная перестановка длины N (задаётся вектором); турбокоды — случайная/алгебраическая'},
   'турбокоды (исследовательские), LDPC-перемежение', [I(FH, 'строки %d–… (Sequence_Interleaver)' % stroka(FH, '\\class Sequence_Interleaver'), 'https://github.com/nvmd/itpp/blob/master/itpp/comm/interleave.h')],
   'определение из исходного текста', 'ЧАСТИЧНО: turbo.py восстанавливает перестановку турбокода по подписям позиций (нужны десятки блоков)'),
 Z('S-случайный (S-random) перемежитель турбокода (Dolinar–Divsalar)', 'Перемежители общего вида', 'перемежитель (полуслучайный)',
   {'правило': 'случайные целые 1…N выбираются по очереди; выбор отвергается, если он ближе ±S к любому из S предыдущих выбранных; повтор до заполнения всех N (обычно S < √(N/2))',
    'пример': 'tablicy/peremezhiteli/s_random_primer.json (N=256, S=8, seed=1)'},
   'турбокоды JPL/CCSDS-подобные, исследовательские', [I(FJ, gde(FJ + '.txt', 'We have designed one type of semirandom permutation'), 'https://ipnpr.jpl.nasa.gov/progress_report/42-122/122B.pdf')],
   'перестановка построена по правилу отчёта, свойство разноса > S проверено для всех пар в окне S', 'ЧАСТИЧНО: turbo.py восстанавливает любую перестановку по данным; QPP LTE — опознаётся'),
]
sv = []
for a in ('efir', 'kosmos', 'mobilnaya', 'provod'):
    for z in json.load(open(os.path.join(KOR, '..', a, 'katalog.json'))):
        v = str(z.get('вид', '')).lower(); pz = json.dumps(z.get('параметры', ''), ensure_ascii=False).lower()
        if 'перемеж' in v or 'перемежитель' in pz:
            sv.append(dict(область=a, имя=z['имя'], вид=z.get('вид'), перемежитель={k: val for k, val in (z.get('параметры') or {}).items() if 'перемеж' in k.lower() or 'interleav' in k.lower()} if isinstance(z.get('параметры'), dict) else None,
                           источник=[dict(s, файл='../%s/%s' % (a, s.get('файл', ''))) for s in z.get('источник', [])]))
json.dump(sv, open(os.path.join(KOR, 'tablicy', 'peremezhiteli', 'svodka_oblastey.json'), 'w'), ensure_ascii=False, indent=1)
print('перемежителей в других областях', len(sv))
sohranit('peremezhiteli', zap)
