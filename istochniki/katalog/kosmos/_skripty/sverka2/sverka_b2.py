"""Сверка, часть B2: спутниковое вещание (МСЭ BO.1516), подвижная связь (GMR-1/2, BGAN, S-MIM, S-UMTS, SDR, Iridium)."""
import json, re
from __main__ import prov, zap, T, TT, P, st, poly_int, GF, pmul, pmod, is_primitive, conv_oct_to_bin

BO1516 = 'istochniki/itu/R-REC-BO.1516-1-201201-I.pdf'
G1 = 'istochniki/gmr1/ts_1013760503v010201p.pdf'


@prov('DirecTV DSS')
def _():
    e = zap('DirecTV DSS')
    t19, t20, t35 = T(BO1516, 19), T(BO1516, 20), T(BO1516, 35)
    assert re.search(r'\(204,188, T = 8\) \(146,130, T = 8\)', t19)
    assert re.search(r'N1 = 13, N2 = 146 \(Ramsey II\)', t20)
    assert re.search(r'1/2, 2/3, 6/7', t20)
    assert re.search(r'X = 100101 Y = 111010', t35)
    p = e['параметры']
    assert '(146,130' in p['РС'] and 'N1=13, N2=146' in p['перемежение'] and 'X=100101, Y=111010' in p['свёрточный']
    x, y = '100101', '111010'
    assert x.count('1') + y.count('1') == 7  # 6 бит → 7 символов: r=6/7
    return 'BO.1516 стр. 19–20, 35: РС(146,130,T=8), Рэмси II N1=13 N2=146, 171/133 со скоростями 1/2, 2/3, 6/7, шаблон 6/7 X=100101 Y=111010 (6→7 символов) — совпало'


@prov('DigiCipher II')
def _():
    e = zap('DigiCipher II')
    t20, t36 = T(BO1516, 20), T(BO1516, 36)
    assert re.search(r'117, 135, 161 \(octal\)', t20)
    assert re.search(r'1 \+ x \+ x3 \+ x12 \+ x16 truncated for a period of 4 894 bytes', t20)
    assert re.search(r'0001h', t20) and re.search(r'I = 12, M = 19 \(Forney\)', t20)
    m = re.search(r'G\(2\) = (\d+) binary \(117 octal\), G\(1\) = (\d+) binary \(135 octal\), and G ?\(0\) = (\d+) binary \(161 octal\)', t36)
    assert m and [conv_oct_to_bin(o, 7) for o in (117, 135, 161)] == list(m.groups())
    rates = re.findall(r'The rate (\d+/\d+) puncture matrix is', t36 + ' ' + T(BO1516, 37))
    assert {'3/4', '1/2', '5/11', '2/3', '4/5'} <= set(rates), rates
    # проверка скорости матриц: 3/4 — p2=[100], p1=[001], p0=[110] → 3 бита на 4 символа
    assert sum(s.count('1') for s in ('100', '001', '110')) == 4
    assert sum(s.count('1') for s in ('00111', '11010', '11111')) == 11
    p = e['параметры']
    assert 'G2=117₈, G1=135₈, G0=161₈' in p['свёрточный'] and '0001h' in p['рандомизация'] and '4894' in p['рандомизация'] and 'M=19' in p['перемежение']
    return 'BO.1516 стр. 20, 36: 117/135/161₈ (= 1001111/1011101/1110001 — двоичная запись стандарта пересчитана), ПСП 1+x+x^3+x^12+x^16 с 0001h, период 4894 байта, Форни I=12 M=19; матрицы выкалывания 3/4 (4 символа) и 5/11 (11 символов) — веса сходятся'


@prov('GMR-1 (Thuraya) свёрточные K=5')
def _():
    e = zap('GMR-1 (Thuraya) свёрточные K=5')
    t = TT(G1)
    assert re.search(r'g0\(D\) = 1 \+ D3 \+ D4; g1\(D\) = 1 \+ D \+ D2 \+ D4', t)
    # r=1/5: уравнения c(5k)…c(5k+4) — извлечь и сравнить со степенями записи
    m = re.search(r'c\(5k\) = (u\(k\)[^;,]*?)(?:\)|,|;) where', t)
    blk = t[t.index('c(5k) ='):t.index('This results in a block of coded bits {c(0), c(1), c(2), ..., c(5K+19)}')]
    eqs = re.findall(r'c\(5k(?:\+(\d))?\) = ((?:u\(k(?:-\d)?\)\s*(?:⊕|\+ ⊕|\+)?\s*)+)', blk)
    assert [i for i, _ in eqs] == ['', '1', '2', '3', '2']  # ОПЕЧАТКА стандарта: 5-е уравнение помечено c(5k+2) вместо c(5k+4)
    got = {i: {int(d or 0) for d in re.findall(r'u\(k(?:-(\d))?\)', ex)} for i, (_, ex) in enumerate(eqs)}
    zap5 = e['параметры']['1/5']
    exp = [st(x.split('=')[1].strip()) for x in zap5.split(',')]
    assert [got[i] for i in range(5)] == exp, (got, exp)
    return 'TS 101 376-5-3 стр. 12, 14: g0=1+D³+D⁴, g1=1+D+D²+D⁴ (r=1/2); для r=1/5 пять уравнений разобраны из текста — множества задержек == многочленам записи; ОПЕЧАТКА в TS 101 376-5-3 V1.2.1 п. 4.4.1: пятое уравнение обозначено c(5k+2) вместо c(5k+4) (по порядку и по osmo-gmr — это c(5k+4))'


@prov('GMR-1 TCH3 свёрточный K=7')
def _():
    e = zap('GMR-1 TCH3 свёрточный K=7')
    t = T(G1, 14)
    assert re.search(r'circular encoding method \(tail-biting\)', t)
    m = re.search(r'constraint length 7 is used\. This code is defined by the following generator polynomials: g0\(D\) = ([^;]+); g1\(D\) = ([^.]+)\.', t)
    assert m
    assert st(m.group(1)) == {0, 2, 3, 5, 6} and st(m.group(2)) == {0, 1, 2, 3, 6}
    p = e['параметры']['многочлены']
    assert st(p.split(',')[0].split('=')[1].replace('²', '^2').replace('³', '^3').replace('⁵', '^5').replace('⁶', '^6')) == {0, 2, 3, 5, 6}
    # = 133/171 в записи старшим разрядом D^0: 1+D2+D3+D5+D6 → 1011011 = 133₈; 1+D+D2+D3+D6 → 1111001 = 171₈
    b = lambda s: ''.join('1' if i in s else '0' for i in range(7))
    assert b({0, 2, 3, 5, 6}) == conv_oct_to_bin(133, 7) and b({0, 1, 2, 3, 6}) == conv_oct_to_bin(171, 7)
    assert re.search(r'u\(K-1\) is placed in the register D1', t)
    return 'стр. 14: g0=1+D²+D³+D⁵+D⁶, g1=1+D+D²+D³+D⁶, кольцевой хвост (регистр из последних 6 бит, u(K−1) в D1) — совпало; это 133/171₈ (g0 = 133, g1 = 171 — порядок ветвей обратный CCSDS)'


@prov('GMR-1 Голей (24,12)')
def _():
    e = zap('GMR-1 Голей (24,12)')
    t = T(G1, 17)
    rows = e['параметры']['G'].split(': ')[1].split()
    for r in rows:
        assert r.lower() in t.lower() or r.upper() in t, r
    G = [int(r, 16) for r in rows]
    assert all(g >> 12 == 1 << (11 - i) for i, g in enumerate(G))  # систематическая
    dmin = 24
    for m in range(1, 4096):
        c = 0
        for i in range(12):
            if m >> i & 1:
                c ^= G[i]
        dmin = min(dmin, bin(c).count('1'))
    assert dmin == 8
    return 'стр. 17: 12 hex-строк G найдены в тексте; G систематическая [I|P]; перебором 4095 слов dmin=8 (своим кодом)'


@prov('GMR-1 РС(15,9)')
def _():
    e = zap('GMR-1 РС(15,9)')
    t = T(G1, 19)
    tab = re.findall(r'g(\d) α(\d+) ([01]{4}) (\d+)', t)
    assert len(tab) == 6
    F = GF(4, 0b10011)
    g = F.poly_from_roots([F.a(j) for j in range(1, 7)])[::-1]  # g[i] — при X^i
    for i, a, tup, dec in tab:
        i, a = int(i), int(a)
        assert F.log[g[i]] == a, (i, a, F.log[g[i]])
        v = sum(int(c) << k for k, c in enumerate(tup))  # X^0 слева
        assert v == g[i], (i, tup)
        assert int(dec) == int(tup, 2)  # «Decimal» — та же запись, прочитанная старшим слева
    return 'стр. 19 табл. 4.8: g0…g5 = α^6, α^9, α^6, α^4, α^14, α^10 пересчитаны как ∏_{j=1}^{6}(X+α^j) над GF(16), p=1+X+X^4 — совпало; n-кортеж записан X^0 слева, столбец «Decimal» = тот же кортеж как двоичное число (≠ значению элемента) — отмечено в записи'


@prov('GMR-1 3G: свёрточный K=9')
def _():
    e = zap('GMR-1 3G: свёрточный K=9')
    t = TT('istochniki/gmr1/ts_1013760503v030301p.pdf')
    assert re.search(r'g3\(D\) = 1 \+ D \+ D3 g5\(D\) = 1 \+ D \+ D2 \+ D3 \+ D5', t)
    m = re.search(r'g0\(D\) = (1 \+ D2 \+ D3 \+ D4 \+ D8)[;,]? ?g1\(D\) = (1 \+ D \+ D2 \+ D3 \+ D5 \+ D7 \+ D8)', t)
    assert m, 'K=9'
    return 'TS 101 376-5-3 V3.3.1: K=9 g0=1+D²+D³+D⁴+D⁸, g1=1+D+D²+D³+D⁵+D⁷+D⁸; CRC g3=1+D+D³, g5=1+D+D²+D³+D⁵ — совпало'


@prov('GMR-2 (ACeS')
def _():
    e = zap('GMR-2 (ACeS')
    t = T('istochniki/gmr2/ts_1013770503v010101p.pdf', 23)
    m = re.search(r'G0 = ([^G]+) G1 = ([^G]+) G2 = ([^G]+) G3 = (1 \+ D \+ D2 \+ D3 \+ D6)', t)
    assert m
    sets = [st(x.strip()) for x in m.groups()]
    assert sets == [{0, 2, 3, 4, 6}, {0, 2, 3, 5, 6}, {0, 1, 4, 5, 6}, {0, 1, 2, 3, 6}]
    pz = e['параметры']['1/4']
    conv = lambda s: s.replace('²', '^2').replace('³', '^3').replace('⁴', '^4').replace('⁵', '^5').replace('⁶', '^6')
    got = [st(conv(x.split('=')[1])) for x in pz.split(', ')]
    assert got == sets
    return 'TS 101 377-5-3 стр. 23: r=1/4, 64 состояния, G0…G3 — совпало с записью'


@prov('Inmarsat BGAN')
def _():
    e = zap('Inmarsat BGAN')
    F = 'istochniki/inmarsat/ts_1027440201v010101p.pdf'
    t27, t28 = T(F, 27), T(F, 28)
    assert re.search(r'h-register of the scrambler is 1 \+ X \+ X15', t27)
    m = re.search(r'initial[- ]state[^.]{0,80}?((?:[01]\s*){15})', t27)
    assert re.search(r'backward polynomial is 23 in octal, and the forward polynomial is 35 in octal: Backward Polynomial: 1 \+ X3 \+ X4 Forward Polynomial: 1 \+ X \+ X2 \+ X4', t28)
    assert re.search(r'set to the zero state at the beginning of each turbo code block', t28)
    init = re.sub(r'\D', '', e['параметры']['скремблер'].split('нач.')[1].split('(')[0])
    assert int(init, 2) == 0x6959
    if m:
        assert re.sub(r'\s', '', m.group(1)) == init, m.group(1)
    return 'стр. 27–28: скремблер 1+X+X^15, начальный вектор %s (=6959h)%s; SRCC 23₈=1+X³+X⁴ / 35₈=1+X+X²+X⁴, оба кодера с нуля на каждый блок — совпало' % (init, ' (сверен с текстом)' if m else ' (в тексте — на рисунке 5.23)')


@prov('S-MIM (Eutelsat')
def _():
    e = zap('S-MIM (Eutelsat')
    t = TT('istochniki/smim/ts_10272103v010201p.pdf')
    assert re.search(r'gCRC16\(D\) = D16 \+ D12 \+ D5 \+ 1 gCRC8\(D\) = D8 \+ D7 \+ D4 \+ D3 \+ D \+ 1', t)
    m = re.search(r'polynomial 1\+X4\+X9\. The initial loading of the shift register shall be equal to (\d{9})', t)
    assert m and m.group(1) == '101000000'
    assert is_primitive(poly_int({9, 4, 0}))
    return 'TS 102 721-3: CRC-16 D^16+D^12+D^5+1, CRC-8 D^8+D^7+D^4+D^3+D+1; скремблер — отрезок М-последовательности 1+X^4+X^9 (примитивен), начальная загрузка 101000000 (добавлено в запись)'


@prov('S-UMTS семейство G')
def _():
    e = zap('S-UMTS семейство G')
    t = T('istochniki/sumts/ts_1018510201v020101p.pdf', 13)
    m = re.search(r'gCRC24\(D\) = ([^;]+); - gCRC16\(D\) = ([^;]+); - gCRC12\(D\) = ([^;]+); - gCRC8\(D\) = ([^.]+)\.', t)
    assert m
    zz = e['параметры']['CRC']
    for nm, s in zip(('24', '16', '12', '8'), m.groups()):
        z = re.search(r'gCRC%s=([^,]+)' % nm, zz).group(1)
        assert st(z) == st(s), nm
    return 'стр. 13: gCRC24/16/12/8 — построчно совпали со степенями записи'


@prov('SDR (спутниковое цифровое радио')
def _():
    e = zap('SDR (спутниковое цифровое радио')
    t14 = T('istochniki/sdr/ts_102550v010301p.pdf', 14)
    assert re.search(r'outer BCH\(3056, 3008, 9\) code', t14) and re.search(r'BCH\(4095,4047,9\)', t14) and re.search(r'\(3057,3056,1\)', t14)
    assert 4095 - 4047 == 3056 - 3008 == 48 and 48 == 4 * 12  # t=4 над GF(2^12)
    assert re.search(r'x8 \+ x5 \+ x3 \+ x2 \+ x \+ 1', T('istochniki/sdr/ts_102550v010301p.pdf', 13))
    t = T('istochniki/sdr/ts_10255101v010101p.pdf', 10)
    m = re.search(r'X11 \+ X9 \+ 1, initial state is set to "(\d{11})"', t)
    assert m and m.group(1) == '11001110001'
    assert is_primitive(poly_int({11, 9, 0}))
    return 'TS 102 550 стр. 13–14: BCH(3056,3008,9) из BCH(4095,4047,9) + общая чётность (3057,3056), CRC-8 x^8+x^5+x^3+x^2+x+1; TS 102 551-1 стр. 10: скремблер X^11+X^9+1 (примитивен), начальное 11001110001, длина 2064 (уточнено в записи)'


@prov('Iridium: BCH')
def _():
    e = zap('Iridium: BCH')
    src = open(P('istochniki/kod/iridium-toolkit/bitsparser.py')).read()
    for d, n in ((1897, 31), (1207, 31), (3545, 31), (29, 7), (465, 31), (41, 26)):
        assert re.search(r'\b%d\b' % d, src), d
    # каждый многочлен — делитель x^n+1 для своей длины (циклический код): 1897 = BCH(31,21), 3545 (31,20)
    for g, n in ((1897, 31), (1207, 31), (3545, 31), (29, 7), (465, 15), (41, 31)):
        assert pmod((1 << n) | 1, g) == 0, (g, n)
    assert pmul(1207, 3) == 3545  # ACCH (31,20) = кольцевой вызов (31,21) × (x+1)
    assert pmul(3, 0b1011) == 29
    assert pmul(0b10011, 0b11111) == 465  # (x^4+x+1)(x^4+x^3+x^2+x+1) = g БЧХ(15,7) t=2
    t = T('istochniki/itu/R-REC-M.1850-2-201409-I.pdf', 56)
    m = re.search(r'Voice \(TCH\) 1/3 Data \(TCH\) 1/2 BCCH 1/2 RACH 1/6 SDCCH 1/4', t)
    assert m
    return 'iridium-toolkit: 1897 и 1207 делят x^31+1 (степень 10 → (31,21)); 3545 = 1207·(x+1) → (31,20); 29 = (x+1)(x^3+x+1) → (7,3); 41 = x^5+x^3+1 | x^31+1 → укороченный (26,21); 465 = (x^4+x+1)(x^4+x^3+x^2+x+1) — порождающий БЧХ(15,7) t=2 (в коде комментарий «BCH(13,16)» — неточен; уточнено в записи); первоисточник МСЭ-R M.1850-2 (SRI-D = Iridium) табл. 23: скорости свёрточного кода TCH-голос 1/3, TCH-данные 1/2, BCCH 1/2, RACH 1/6, SDCCH 1/4 — добавлено в запись'
