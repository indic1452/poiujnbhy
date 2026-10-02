"""Дополнение: турбокод (параллельное каскадирование) по патенту C. Berrou US 5,446,747 (1995): структура FIG. 1–3 и
«псевдосистематический» (рекурсивный систематический) кодер FIG. 7. Текст — OCR встроенных изображений (ocr_vstroennye.py),
схема FIG. 7 прочитана по изображению страницы (risunki/berrou_US5446747_fig7.png): a_k = d_k ⊕ a_{k−2}, Y_k = a_k ⊕ a_{k−1} ⊕ a_{k−2}.
Проверка (assert): по OCR — «constraint length v=2», «interleaving matrix», «row-skip increment … relatively prime»,
«periodic switching between the outputs»; dfree RSC и d2 (вес слова при входе веса 2) — своим перебором по решётке."""
import heapq, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka
P = 'istochniki/dop/patenty/US5446747_Berrou_turbo.pdf'
t = open(os.path.join(KOR, P + '.txt')).read()
for frag in ('constraint length v=2', 'interleaving matrix', 'row-skip increment', 'relatively prime', 'periodic switching between the outputs',
             'parallel concatenation', 'shift register 82 contains not the previous source', 'exclusive OR gate 83'):
    assert frag in t, frag
# RSC FIG. 7: состояние (a_{k−1}, a_{k−2})
def step(s, d):
    a1, a2 = s; a = d ^ a2; y = a ^ a1 ^ a2
    return (a, a1), d + y
def dfree():
    h = [(step((0, 0), 1)[1], step((0, 0), 1)[0])]; seen = set()
    while h:
        w, s = heapq.heappop(h)
        if s == (0, 0): return w
        if s in seen: continue
        seen.add(s)
        for d in (0, 1):
            ns, bw = step(s, d); heapq.heappush(h, (w + bw, ns))
def d2():  # наименьший вес слова при входе веса 2, возвращающемся в 0
    best = None
    for gap in range(1, 40):
        s = (0, 0); w = 0
        for k in range(gap + 1 + 4):
            d = 1 if k in (0, gap) else 0
            s, bw = step(s, d); w += bw
        if s == (0, 0): best = w if best is None else min(best, w)
    return best
DF = dfree(); D2 = d2()
assert DF == 5 and D2 == 5, (DF, D2)  # 1+D² не примитивен (период 2): вход 1+D² → a = 1, Y = 1+D+D² → вес 2+3 = 5
zap = [Z('Турбокод Берру (параллельное каскадирование систематических свёрточных кодов, патент US 5,446,747)', 'Турбокоды PCCC (общие)', 'турбо PCCC',
         {'структура (FIG. 1–2)': 'X = d (систематический); Y1 — кодер 11 от d; Y2 — кодер 13 от перемеженных d (перемежитель 12 + сдвиговый регистр 14 для выравнивания задержки); модуль выбора 15 периодически переключает Y1/Y2 (выкалывание) — общая скорость R ≥ 1/2, тип. 1/2',
          'скорости составляющих': 'R2 = R·R1 / (R·R1 + R1 − R); пример R1 = 3/5, R2 = 4/5 → R = 1/2 (так в тексте; OCR дроби ненадёжен — сверять по PDF, стр. 11)',
          'составляющий кодер (FIG. 7)': 'рекурсивный («псевдосистематический»), v = 2: a_k = d_k ⊕ a_{k−2}; X_k = d_k; Y_k = a_k ⊕ a_{k−1} ⊕ a_{k−2} → G = [1, (1+D+D²)/(1+D²)] (восьм. 7/5, обратная связь 5)',
          'dfree составляющего (расчёт)': DF, 'd2 составляющего (расчёт)': D2, 'замечание': 'обратная связь 1+D² не примитивна (период 2), поэтому d2 = 5 меньше границы (n−1)(2^(m−1)+2)+2 = 6 для примитивной (Divsalar–Pollara, JPL 42-123)',
          'перемежитель': 'матрица n_e × n_e: запись по строкам, чтение по столбцам; шаги по строкам/столбцам > 1 и взаимно просты с n_e (Dunscombe–Piper 1989); улучшение — шаг пропуска строк зависит от номера столбца',
          'декодирование (FIG. 3–4)': 'модули итераций каскадом: вход X, Y и оценка Z_(p−1); первый модуль Z = 0; Витерби с мягким выходом (SOVA), деперемежение, логарифмическое сжатие'},
         'исходная схема турбокодов (Berrou, France Télécom/TDF, 1991–1993); основа турбокодов CCSDS, 3GPP, DVB-RCS (там свои составляющие и перемежители — другие области/модемные семейства)',
         [I(P, 'стр. 6 PDF (Sheet 5, FIG. 7 — схема RSC; изображение risunki/berrou_US5446747_fig7.png), стр. 2 PDF (Sheet 1, FIG. 1–3; risunki/berrou_US5446747_fig1_3.png)'),
          I(P + '.txt', 'строки %d–%d (FIG. 7 и свойства «псевдосистематического» кода), %d–%d (кодер FIG. 2, выбор Y1/Y2, перемежение)' % (
              stroka(P + '.txt', 'FIG. 7 shows an example of a'), stroka(P + '.txt', 'exclusive OR gate 83'), stroka(P + '.txt', 'FIG. 2 illustrates a particular embodiment of a coder'), stroka(P + '.txt', 'ters, Vol. 25, No. 22, October 1989.')))],
         'схема FIG. 7 прочитана по изображению; dfree = %d и d2 = %d RSC (1, 7/5) пересчитаны своим перебором по решётке; ключевые фразы описания найдены в OCR (assert)' % (DF, D2),
         'ЧАСТИЧНО: turbo.py — PCCC с RSC и признаки перемежителя; RSC 7/5 перебирается среди K=3')]
sohranit('dop_berrou', zap)
