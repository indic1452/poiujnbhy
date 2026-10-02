"""Полярные коды: последовательность надёжности 5G NR (N=1024) — сверка трёх источников; общее построение Арикана для BEC
(рекурсия (6) статьи arXiv:0807.3917) → tablicy/polyarnye/*.json, zapisi/polyarnye.json.
Проверки (assert): Q 5G — Sionna polar_5G.csv = AFF3CT 5G/N_1024.pc (в обратном порядке) = таблица 5.3.1.2-1 38.212 из записи
области mobilnaya (tablicy/nr_polar_38212.json); BEC-построение — рекурсия по I (статья, (6)) и по Z в логарифмах как в
AFF3CT Frozenbits_generator_BEC.cpp дают один порядок каналов (две независимые записи формулы)."""
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka, gde
OUT = os.path.join(KOR, 'tablicy', 'polyarnye')
FS = 'istochniki/kod/sionna/src/sionna/phy/fec/polar/codes/polar_5G.csv'
FA = 'istochniki/polyarnye/aff3ct/5G_N_1024.pc'
FM = '../mobilnaya/tablicy/nr_polar_38212.json'
qs = [None] * 1024
for l in open(os.path.join(KOR, FS)):
    i, v = l.strip().split(';'); qs[int(i)] = int(v)
a = open(os.path.join(KOR, FA)).read().split()
assert a[0] == '1024' and a[1] == 'awgn' and a[2] == '*'
qa = [int(x) for x in a[3:]][::-1]
qm = json.load(open(os.path.join(KOR, FM)))['данные']['Q_0_1023_t5.3.1.2-1']
assert qs == qa == qm and sorted(qs) == list(range(1024))
json.dump(dict(Q=qs, проверка='Sionna = AFF3CT (обратный порядок) = 38.212 табл. 5.3.1.2-1 (mobilnaya)'), open(os.path.join(OUT, 'q_5g_1024.json'), 'w'))
# BEC
def bec_I(n, eps):
    I_ = [1 - eps]
    for _ in range(n):
        nxt = []
        for x in I_: nxt += [x * x, 2 * x - x * x]
        I_ = nxt
    return I_
def bec_logz_aff3ct(n, eps):
    N = 1 << n; z = [0.0] * N; z[0] = math.log(eps)
    for l in range(1, n + 1):
        b = 1 << l; stride = N // b
        for j in range(b // 2):
            t = z[j * 2 * stride]
            z[j * 2 * stride] = math.log(2) + t + math.log1p(-math.exp(2 * t - (math.log(2) + t)))
            z[j * 2 * stride + stride] = 2 * t
    return z
bec = {}
for n in range(1, 11):
    I_ = bec_I(n, 0.5)
    # статья: индексы 1…N, порядок «естественный» (u_i); AFF3CT — тот же порядок индексов (j·2·stride — бит-реверсная раскладка внутри массива)
    order_I = sorted(range(1 << n), key=lambda i: (-I_[i], i))
    z = bec_logz_aff3ct(n, 0.5)
    # AFF3CT хранит z в бит-реверсной раскладке: позиция p ↔ канал bitrev(p)
    br = lambda p: int(format(p, '0%db' % n)[::-1], 2)
    pryamo = all(abs((1 - I_[i]) - math.exp(z[i])) < 1e-12 for i in range(1 << n))
    assert pryamo, n   # индексы AFF3CT совпадают с индексами рекурсии статьи (первое расщепление — старший бит)
    bec[1 << n] = dict(N=1 << n, надёжность_по_убыванию=order_I, I=[round(x, 12) for x in I_])
json.dump(dict(eps=0.5, построения=bec), open(os.path.join(OUT, 'arikan_bec_eps05.json'), 'w'))
print('Q 5G: три источника совпали; BEC: N=2…1024, две записи рекурсии совпали')
FAR = 'istochniki/polyarnye/arxiv_0807.3917_Arikan_polar.pdf'
zap = [
 Z('Полярный код 5G NR: последовательность надёжности Q (N_max = 1024)', 'Полярные коды', 'полярный',
   {'ядро': 'G_N = F^{⊗n}, F = [[1,0],[1,1]] (38.212 п. 5.3.1.2)', 'Q': 'tablicy/polyarnye/q_5g_1024.json (Q_0…Q_1023, по возрастанию надёжности)', 'N': '2^n, n ≤ 9 (DL) / 10 (UL)',
    'прочее': 'CRC-24C/11/6, перемежитель Π_IL (K_IL_max = 164), подблочный перемежитель (32 подблока), согласование скорости — запись mobilnaya'},
   '5G NR: PBCH, DCI (PDCCH), UCI (PUCCH/PUSCH)',
   [I(FS, 'строки 1–1024', 'https://github.com/NVlabs/sionna/blob/main/src/sionna/phy/fec/polar/codes/polar_5G.csv'), I(FA, 'весь файл (порядок от самого надёжного)'),
    I('../mobilnaya/istochniki/nr/ts_138212v190400p.pdf', 'стр. 19 PDF, табл. 5.3.1.2-1', 'https://www.etsi.org/deliver/etsi_ts/138200_138299/138212/19.04.00_60/ts_138212v190400p.pdf')],
   'три независимых источника (Sionna Apache-2.0, AFF3CT, текст 38.212 по разбору mobilnaya) — одинаковая перестановка 0…1023',
   'НЕТ в проекте: полярного декодера (SC/SCL) нет; запись mobilnaya описывает кадрирование 5G'),
 Z('Полярные коды Арикана (общее построение), BEC ε=0.5: порядок каналов для N = 2…1024', 'Полярные коды', 'полярный',
   {'ядро': 'F = [[1,0],[1,1]], G_N = B_N F^{⊗n} (B_N — бит-реверсная перестановка)', 'рекурсия (BEC)': 'I(W_N^(2i−1)) = I(W_{N/2}^(i))², I(W_N^(2i)) = 2I − I², I(W_1) = 1 − ε (формула (6) статьи)',
    'таблица': 'tablicy/polyarnye/arikan_bec_eps05.json', 'выбор': 'информационное множество A — K каналов с наибольшим I, остальные заморожены (обычно нулями)'},
   'общая теория; построения для AWGN — Тал–Варди/ГА (наборы AFF3CT TV), 5G — фиксированная Q',
   [I(FAR, gde(FAR + '.txt', 'have been computed using the recursive relations'), 'https://arxiv.org/abs/0807.3917'),
    I('istochniki/polyarnye/aff3ct/Frozenbits_generator_BEC.cpp', 'функция evaluate()', 'https://github.com/aff3ct/aff3ct/blob/master/src/Tools/Code/Polar/Frozenbits_generator/Frozenbits_generator_BEC.cpp')],
   'рекурсия статьи по I и реализация AFF3CT по log Z (индексы совпадают напрямую) дают одинаковые значения 1 − I = Z для всех каналов N ≤ 1024',
   'НЕТ: полярные коды в проекте не опознаются (признак — порядок F^{⊗n}; нужен SC-декодер)'),
]
sohranit('polyarnye', zap)
