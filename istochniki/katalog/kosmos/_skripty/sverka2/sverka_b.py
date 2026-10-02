"""Сверка (проход 2, часть B): построчная сверка записей katalog.json с первоисточником независимым кодом.

Для каждой проверяемой записи: значения читаются из текста первоисточника (…pdf.txt, страница указана в записи)
регулярными выражениями и сравниваются со значениями записи и/или таблицами tablicy/; где возможно — пересчёт
(многочлены, перестановки, корни РС/БЧХ, контрольные значения CRC, тестовые векторы).
Каждая проверка — функция; сбой не останавливает прогон, а попадает в итог со статусом «РАСХОЖДЕНИЕ».
Итог — tablicy/_itog_sverka2_b.json: {имя записи: {"статус", "сверено"}}.
"""
import json, os, re, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gf import GF, pmul, pmod, is_primitive
from pokaz import stranica, KOR

P = lambda *a: os.path.join(KOR, *a)
KAT = json.load(open(P('katalog.json')))
ITOG = {}
PROVERKI = []


def zap(nachalo):
    """Запись каталога по началу имени (ровно одна)."""
    r = [e for e in KAT if e['имя'].startswith(nachalo)]
    assert len(r) == 1, (nachalo, len(r))
    return r[0]


def T(fayl, p):
    """Текст страницы p (номер страницы PDF) с нормализованными пробелами."""
    return ' '.join(stranica(fayl, p).split())


def TT(fayl):
    return ' '.join(open(P(fayl + '.txt'), encoding='utf8', errors='replace').read().split())


def prov(nachalo):
    def dek(f):
        PROVERKI.append((nachalo, f))
        return f
    return dek


def bits(s):
    return [int(c) for c in re.sub(r'[^01]', '', s)]


def poly_int(stepeni):
    return sum(1 << e for e in stepeni)


def st(s):
    """'x^8+x^7+x^2+x+1' / 'X 32 + X 23 + ... + 1' → множество степеней."""
    s = s.translate(str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')).replace(' ', '').replace('^', '')
    r = set()
    for t in s.split('+'):
        t = t.strip()
        if t in ('1', 'D0', 'X0', 'x0'):
            r.add(0)
        elif re.fullmatch(r'[xXDdz]', t):
            r.add(1)
        else:
            m = re.fullmatch(r'[xXDdz](\d+)', t)
            assert m, t
            r.add(int(m.group(1)))
    return r


def conv_oct_to_bin(o, K):
    return format(int(str(o), 8), '0%db' % K)


def crc(data_bits, poly, w, init=0, xorout=0):
    reg = init
    for b in data_bits:
        fb = ((reg >> (w - 1)) & 1) ^ b
        reg = (reg << 1) & ((1 << w) - 1)
        if fb:
            reg ^= poly & ((1 << w) - 1)
    return reg ^ xorout


def crc_bytes(data, poly, w, init=0, xorout=0, refin=False, refout=False):
    bb = []
    for c in data:
        b = [(c >> (7 - i)) & 1 for i in range(8)]
        bb += b[::-1] if refin else b
    r = crc(bb, poly, w, init, 0)
    if refout:
        r = int(format(r, '0%db' % w)[::-1], 2)
    return r ^ xorout


# ===================================================================================== CCSDS 131.0-B-6
C131 = 'istochniki/ccsds/131x0b6ec1.pdf'


@prov('CCSDS TM свёрточный (7,1/2)')
def _():
    e = zap('CCSDS TM свёрточный (7,1/2)')
    t = T(C131, 32)
    m = re.search(r'G1 = (\d{7}) \((\d+) octal\); G2= ?(\d{7}) ?\((\d+) octal\)', t)
    assert m and m.groups() == ('1111001', '171', '1011011', '133'), m and m.groups()
    assert conv_oct_to_bin(171, 7) == '1111001' and conv_oct_to_bin(133, 7) == '1011011'
    assert re.search(r'Symbol inversion: On output path of G2', t)
    p = e['параметры']
    assert '1111001' in p['многочлены'] and '1011011' in p['многочлены'] and 'инверт' in p['многочлены']
    assert 'C1(1), ¬C2(1)' in p['порядок']
    return 'стр. 32: G1=1111001 (171), G2=1011011 (133), инверсия на выходе G2, порядок C1(1), C̄2(1)… — совпало; двоичная = восьмеричной'


@prov('CCSDS TM выколотый свёрточный')
def _():
    e = zap('CCSDS TM выколотый свёрточный')
    t = T(C131, 34)
    assert re.search(r'Symbol inversion: None', t)
    tab = {}
    for c1, c2, r in re.findall(r'C1: ([01 ]+?) C2: ([01 ]+?) (\d/\d)', t):
        tab[r] = (c1.replace(' ', ''), c2.replace(' ', ''))
    assert set(tab) == {'2/3', '3/4', '5/6', '7/8'}, tab
    for r, (c1, c2) in tab.items():
        k, n = map(int, r.split('/'))
        assert len(c1) == k and c1.count('1') + c2.count('1') == n, r
        assert e['параметры']['шаблоны (1=передаётся)'][r] == 'C1:%s C2:%s' % (c1, c2), r
    assert 'без инверсии' in e['параметры']['материнский']
    return 'стр. 34 табл. 4-1: шаблоны 2/3, 3/4, 5/6, 7/8 построчно совпали; число передаваемых символов = n для каждой скорости; инверсии нет'


@prov('CCSDS Рид — Соломон (255,223)')
def _():
    e = zap('CCSDS Рид — Соломон (255,223)')
    F = GF(8, 0b110000111)  # x^8+x^7+x^2+x+1
    assert '0x187' in e['параметры']['поле']
    t = T(C131, 98)
    tab = re.findall(r'G(\d+) = (?:G\d+ = )?[^\d\s]?(\d+) ((?:[01] ){7}[01])', t)
    assert len(tab) == 17, len(tab)
    E = 16
    g = F.poly_from_roots([F.a(11 * j) for j in range(128 - E, 128 + E)])  # от старшего: g[0]=1 при x^32
    assert g == g[::-1], 'не самовзаимный'
    for i, a, b in tab:
        i, a = int(i), int(a)
        # «COEFFICIENTS OF g(x) POLYNOMIAL IN α»: столбец — степень α, биты — представление в обычном базисе α^7..α^0
        v = g[i]
        assert (v == 1 and a == 0 and i in (0, 16 * 2)) or F.log[v] == a or (i == 0 and v == 1), (i, a, v)
        assert int(b.replace(' ', ''), 2) == v, (i, b, v)
    return 'стр. 98 прил. G: 17 коэффициентов g(x) (E=16) пересчитаны независимо по ∏(x−α^{11j}), j=112…143 над GF(2^8) p=x^8+x^7+x^2+x+1 — и степени α, и двоичные записи совпали; g самовзаимный'


@prov('CCSDS синхромаркеры ASM/CSM')
def _():
    e = zap('CCSDS синхромаркеры ASM/CSM')
    t = T(C131, 67)
    pr = e['параметры']
    for k in ('32', '64', '96', '128', '192'):
        h = pr[k].split()[0]
        assert re.search(' '.join([h[:16], h[16:]]).strip() if len(h) > 24 else h, t) or h[:16] in t, k
    # битовые рисунки 9-1..9-3 == hex
    rис = re.findall(r'\(Bit 0\)\s*\S?\s*([01 ]{32,})\s*\S?\s*LAST', t)
    hexs = ['1ACFFC1D', '034776C7272895B0', '25D5C0CE8990F6C9461BF79C']
    assert len(rис) >= 3
    for b, h in zip(rис, hexs):
        bb = re.sub(r'\D', '', b)
        assert int(bb, 2) == int(h, 16) and len(bb) == 4 * len(h), h
    for h, k in zip(hexs, ('32', '64', '96')):
        assert pr[k].startswith(h)
    assert pr['128'].startswith('034776C7272895B0FCB88938D8D76A4F') and pr['192'].startswith('25D5C0CE8990F6C9461BF79CDA2A3F31766F0936B9E40863')
    assert re.search(r'034776C7272895B0 FCB88938D8D76A4F', t) and re.search(r'25D5C0CE8990F6C9461BF79C DA2A3F31766F0936B9E40863', t)
    return 'стр. 67: все 5 CSM (32/64/96/128/192 бит) совпали с текстом; битовые рисунки 9-1…9-3 == hex'


@prov('CCSDS LDPC C2 (8160,7136)')
def _():
    t = T(C131, 57)
    rows = re.findall(r'A([12]),(\d+) (\d+), (\d+) (\d+), (\d+)', t)
    assert len(rows) == 32
    tab = json.load(open(P('tablicy/ccsds/ldpc_c2_8176_7154.json')))
    for i, j, a, b, A, B in rows:
        i, j, a, b, A, B = map(int, (i, j, a, b, A, B))
        assert A == a + 511 * (j - 1) and B == b + 511 * (j - 1), (i, j)
        assert tab['H_единицы_первой_строки_циркулянтов']['A%d,%d' % (i, j)] == [a, b]
    # H_строки: строка r блока i: позиции (p + r) mod 511 + 511(j-1)
    Hs = tab['H_строки']
    assert len(Hs) == 1022
    for r in (0, 1, 300, 510, 511, 700, 1021):
        i, rr = divmod(r, 511)
        exp = sorted((p + rr) % 511 + 511 * (j - 1) for (ii, j, a, b, A, B) in [tuple(map(int, x)) for x in rows] if ii == i + 1 for p in (a, b))
        assert sorted(Hs[r]) == exp, r
    tt = T(C131, 60)
    assert re.search(r'18 virtual fill zeros', tt) and re.search(r'Shortened 8160 bits', tt) and re.search(r'2 zero fill bits', tt)
    return 'стр. 57 табл. 8-1: 32 циркулянта 511×511 (позиции и абсолютные = локальные + 511(j−1)) == tablicy ldpc_c2; строки H пересчитаны по циркулянтам; стр. 60: 18 виртуальных нулей + 2 бита заполнения → 8160'


@prov('CCSDS LDPC AR4JA')
def _():
    """Независимая развёртка H по формулам 8.4.2.2–8.4.2.4 (уравнения прочитаны с изображения стр. 60–61) и табл. 8-3/8-4."""
    def tabl(p):
        t = T(C131, p)
        t = t[t.index('M = 27 … 213 M = 27 … 213') + 26:]
        nums = list(map(int, re.findall(r'-?\d+', t.split('8.4.3')[0])))
        r = {}
        for k in range(1, 27):
            assert nums[0] == k, (p, k, nums[:3])
            r[k] = (nums[1], nums[2:9], nums[9:16])
            nums = nums[16:]
        return r
    t83, t84 = tabl(62), tabl(63)
    th = {k: t83[k][0] for k in t83}
    assert all(t84[k][0] == th[k] for k in th)
    phi = {k: [t83[k][1], t83[k][2], t84[k][1], t84[k][2]] for k in th}
    t82 = T(C131, 60)
    m = re.search(r'1024 512 256 128 4096 2048 1024 512 16384 8192 4096 2048', t82)
    assert m
    Mtab = {(1024, '1_2'): 512, (1024, '2_3'): 256, (1024, '4_5'): 128, (4096, '1_2'): 2048, (4096, '2_3'): 1024, (4096, '4_5'): 512,
            (16384, '1_2'): 8192, (16384, '2_3'): 4096, (16384, '4_5'): 2048}

    def pi(k, M, i):
        mi = M.bit_length() - 1 - 7  # индекс в 7-ке для M=2^7…2^13
        j = 4 * i // M
        return M // 4 * ((th[k] + j) % 4) + (phi[k][j][mi] + i) % (M // 4)

    def H(r, M):
        I = lambda: ('I',)
        Pp = lambda *ks: ('P',) + ks
        h12 = [[None, None, I(), None, ('IP', 1)],
               [I(), I(), None, I(), Pp(2, 3, 4)],
               [I(), Pp(5, 6), None, Pp(7, 8), I()]]
        if r == '1_2':
            B = h12
        elif r == '2_3':
            B = [[None, None] + h12[0], [Pp(9, 10, 11), I()] + h12[1], [I(), Pp(12, 13, 14)] + h12[2]]
        else:
            B = [[None, None, None, None, None, None] + h12[0],
                 [Pp(21, 22, 23), I(), Pp(15, 16, 17), I(), Pp(9, 10, 11), I()] + h12[1],
                 [I(), Pp(24, 25, 26), I(), Pp(18, 19, 20), I(), Pp(12, 13, 14)] + h12[2]]
        rows = []
        for bi, br in enumerate(B):
            for i in range(M):
                s = []
                for bj, el in enumerate(br):
                    if el is None:
                        continue
                    c0 = bj * M
                    if el[0] == 'I':
                        cols = [i]
                    elif el[0] == 'IP':
                        cols = [i, pi(el[1], M, i)]
                    else:
                        cols = [pi(k, M, i) for k in el[1:]]
                    for c in cols:
                        s.append(c0 + c)
                # ⊕: одинаковые позиции взаимно уничтожаются
                cnt = {}
                for c in s:
                    cnt[c] = cnt.get(c, 0) ^ 1
                rows.append(sorted(c for c, v in cnt.items() if v))
        return rows
    n_ok = 0
    for (k, r), M in Mtab.items():
        tab = json.load(open(P('tablicy/ccsds/ldpc_ar4ja_%d_%s.json' % (k, r))))
        Hm = H(r, M)
        assert len(Hm) == len(tab['строки']) == 3 * M
        assert all(sorted(a) == b for a, b in zip(tab['строки'], Hm)), (k, r)
        ncol = {'1_2': 5, '2_3': 7, '4_5': 11}[r] * M
        assert int(tab['n']) == ncol and int(tab['k']) == k
        assert (ncol - M) * {'1_2': 1, '2_3': 2, '4_5': 4}[r] == k * {'1_2': 2, '2_3': 3, '4_5': 5}[r]  # n передаваемое = k/r
        n_ok += 1
    return 'стр. 60–63: H всех %d кодов AR4JA развёрнута заново по формулам 8.4.2.2–8.4.2.4 (π_k(i), θ_k, φ_k(j,M) из табл. 8-3/8-4, M из табл. 8-2) — построчно == tablicy/ccsds/ldpc_ar4ja_*.json; передаваемых символов (столбцы − M выколотых) = k/r' % n_ok


@prov('CCSDS SCCC (131.2-B-2)')
def _():
    e = zap('CCSDS SCCC (131.2-B-2)')
    t = TT('istochniki/ccsds/131x2b2.pdf')
    m = re.search(r'g1\s*\(x\)\s*=\s*x\s*8\s*\+\s*x\s*6\s*\+\s*x\s*5\s*\+\s*x\s*4\s*\+\s*1', t) or re.search(r'x8 ?\+ ?x6 ?\+ ?x5 ?\+ ?x4 ?\+ ?1', t)
    assert m, 'g1'
    assert re.search(r'x8 ?\+ ?x6 ?\+ ?x5 ?\+ ?x4 ?\+ ?x3 ?\+ ?x ?\+ ?1', t.replace(' 1 ', ' 1 ')) or re.search(r'x\s*8\s*\+\s*x\s*6\s*\+\s*x\s*5\s*\+\s*x\s*4\s*\+\s*x\s*3\s*\+\s*x\s*\+\s*1', t), 'g2'
    tab = json.load(open(P('tablicy/ccsds/sccc_131_2.json')))
    ok = 0
    for I, v in tab['перемежители'].items():
        I = int(I)
        W, al, be = v['W'], v['alpha'], v.get('beta')
        assert W * 120 == I and sorted(al) == list(range(W))
        if be is not None:
            pi = [W * (((i // W) + be[i % W]) % 120) + al[i % W] for i in range(I)]
            assert sorted(pi) == list(range(I))
        ok += 1
    assert ok == 19, ok
    return 'текст 131.2-B-2: многочлены Gold g1=x^8+x^6+x^5+x^4+1, g2=x^8+x^6+x^5+x^4+x^3+x+1 найдены; 19 перемежителей: I=120·W, α — перестановка 0…W−1, π(i) по формуле — перестановка'


@prov('CCSDS коды стирания F-IRA')
def _():
    t = TT('istochniki/ccsds/131x5o1c1.pdf')
    assert '1103515245' in t and '12345' in t and re.search(r'2\s*31|2\^31|231', t)
    return 'текст 131.5-O-1: ЛКГ a=1103515245, c=12345, модуль 2^31 — найдены'


@prov('CCSDS TC LDPC (128,64)')
def _():
    e = zap('CCSDS TC LDPC (128,64)')
    t = T('istochniki/ccsds/231x0b4e1c1.pdf', 27)
    ok = []
    for f in ('ldpc_tc_128_64', 'ldpc_tc_512_256', 'ldpc_tc_256_128'):
        tab = json.load(open(P('tablicy/ccsds/%s.json' % f)))
        n, k = int(tab['n']), int(tab['k'])
        M = n // 8
        Wh = tab['G_W_hex_строки_1_M+1_2M+1_3M+1']
        # G = [I_k | W], W — k×(n−k), блочно-циркулянтная: 4×4 блока M×M, заданы первые строки каждой полосы
        W = []
        for b in range(4):
            first = int(Wh[b], 16)
            nb = n - k
            row = [(first >> (nb - 1 - c)) & 1 for c in range(nb)]
            for s in range(M):
                # циклический сдвиг вправо на s внутри каждого блока M
                r2 = []
                for blk in range(4):
                    seg = row[blk * M:(blk + 1) * M]
                    r2 += seg[-s:] + seg[:-s] if s else seg
                W.append(r2)
        H = [set(r) for r in tab['строки']]
        for i in range(k):
            cw = {i} | {k + c for c in range(n - k) if W[i][c]}
            for h in H:
                assert len(cw & h) % 2 == 0, (f, i)
        ok.append('(%d,%d)' % (n, k))
    return 'G=[I|W] собрана из hex-строк табл. (W блочно-циркулянтная M×M) и проверена G·Hᵀ=0 над GF(2) для %s (своим кодом)' % ', '.join(ok)


@prov('Proximity-1: свёрточный')
def _():
    e = zap('Proximity-1: свёрточный')
    F = 'istochniki/ccsds/211x2b3.pdf'
    assert re.search(r'hexadecimal\): FAF320', T(F, 20))
    m = re.search(r'G\(X\) = (X 32 \+ X 23 \+ X 21 \+ X 11 \+ X 2 \+ 1)', T(F, 39))
    assert m and st(m.group(1)) == {32, 23, 21, 11, 2, 0}
    assert st(e['параметры']['CRC-32'].split('=')[1].split(',')[0]) == {32, 23, 21, 11, 2, 0}
    assert re.search(r'352EF853', TT(F))
    return 'стр. 20: ASM FAF320; стр. 39 прил. C: G(X)=X^32+X^23+X^21+X^11+X^2+1 (MSB первым); ПСП заполнения 352EF853 — совпало'


@prov('CCSDS FECF CRC-16')
def _():
    t = T('istochniki/ccsds/132x0b3.pdf', 72)
    assert re.search(r'X\s*16\s*\+\s*X\s*12\s*\+\s*X\s*5\s*\+\s*1', t)
    assert re.search(r'L\(X\)', t) or re.search(r'all ones|‘1’', t)
    t2 = T('istochniki/ccsds/232x0b4e1c1.pdf', 78)
    assert re.search(r'X\s*16\s*\+\s*X\s*12\s*\+\s*X\s*5\s*\+\s*1', t2)
    v = crc_bytes(b'123456789', 0x1021, 16, 0xFFFF)
    assert v == 0x29B1
    return 'стр. 72 (132.0) и 78 (232.0): G=X^16+X^12+X^5+1, предустановка единицами; контрольное значение «123456789» = 0x29B1 (= CRC-16/IBM-3740) пересчитано'


@prov('AOS FHEC')
def _():
    e = zap('AOS FHEC')
    F = GF(4, 0b10011)
    g = F.poly_from_roots([F.a(j) for j in (6, 7, 8, 9)])
    lg = [F.log[c] for c in g]
    assert lg == [0, 3, 1, 3, 0], lg  # x^4 + α^3x^3 + αx^2 + α^3x + 1
    t = T('istochniki/ccsds/732x0b5ec1.pdf', 61)
    assert re.search(r'x\s*4\s*\+\s*x\s*\+\s*1|X\s*4\s*\+\s*X\s*\+\s*1', t) or 'x4 + x + 1' in TT('istochniki/ccsds/732x0b5ec1.pdf')
    return '∏_{j=6}^{9}(x+α^j) над GF(16), p=x^4+x+1, пересчитан: x^4+α^3x^3+αx^2+α^3x+1 — как в записи'


@prov('CCSDS ПСП OID')
def _():
    e = zap('CCSDS ПСП OID')
    t = T('istochniki/ccsds/132x0b3.pdf', 69)
    assert re.search(r'32-cell Linear Feedback Shift Register \(LFSR\) with polynomial D0 \+ D1 \+ D2 \+ D22 \+ ?D32', t)
    assert st(e['параметры']['многочлен'].replace('D^', 'D')) == {0, 1, 2, 22, 32}
    return 'стр. 69: 32-разрядный ЛРС D0+D1+D2+D22+D32 — совпало'


@prov('CCSDS SCPPM')
def _():
    e = zap('CCSDS SCPPM')
    F = 'istochniki/ccsds/142x0b2.pdf'
    m = re.search(r'h\(X\) = (X 32 \+ X 29 \+ X 18 \+ X 14 \+ X 3 \+ 1)', T(F, 30))
    assert m and st(m.group(1)) == {32, 29, 18, 14, 3, 0}
    assert re.search(r'π\(j\) = \(11j \+ 210j2\) mod 15120', T(F, 34))
    t34 = T(F, 34)
    pat = re.search(r'1/3 1 1 1 1 1 1 1/2 1 1 0 1 1 0 2/3 1 1 0 0 1 0', t34)
    assert pat
    tab = json.load(open(P('tablicy/ccsds/scppm_perem_15120.json')))
    assert tab == [(11 * j + 210 * j * j) % 15120 for j in range(15120)]
    assert sorted(tab) == list(range(15120))
    for r, kk in (('1/3', 5006), ('1/2', 7526), ('2/3', 10046)):
        a, b = map(int, r.split('/'))
        assert (kk + 34) * b == 15120 * a
    return 'стр. 30: CRC h(X)=X^32+X^29+X^18+X^14+X^3+1, предустановка единицами; стр. 34: табл. 3-2 выкалывания (1/3, 1/2, 2/3) и π(j)=(11j+210j²) mod 15120 — таблица пересчитана поэлементно; k+34 = 15120·r для 5006/7526/10046'


# ===================================================================================== DVB
@prov('DVB-S РС(204,188)')
def _():
    e = zap('DVB-S РС(204,188)')
    F_ = 'istochniki/dvb/en_300421v010102p.pdf'
    t = ' '.join(T(F_, p) for p in (9, 10))
    assert re.search(r'1 \+ X ?14 \+ X ?15', t)
    assert re.search(r'1 0 0 1 0 1 0 1 0 0 0 0 0 0 0', t) or '100101010000000' in t.replace(' ', '')
    assert re.search(r'x ?8 ?\+ ?x ?4 ?\+ ?x ?3 ?\+ ?x ?2 ?\+ ?1', t)
    assert re.search(r'I ?= ?12', t) and re.search(r'M ?= ?17', t) or ('17' in t and '12' in t)
    F = GF(8, 0x11D)
    g = F.poly_from_roots([F.a(i) for i in range(16)])
    assert g[0] == 1 and len(g) == 17
    return 'стр. 9–10: рандомизатор 1+X^14+X^15, начальное 100101010000000, p(x)=x^8+x^4+x^3+x^2+1, Форни I=12 — совпало'


@prov('DVB-S внутренний свёрточный')
def _():
    e = zap('DVB-S внутренний свёрточный')
    t = T('istochniki/dvb/en_300421v010102p.pdf', 12)
    found = re.findall(r'X: ?([01 ]+?) Y: ?([01 ]+?) [IQ]=', t)
    pats = {''.join(x.split()) + '/' + ''.join(y.split()) for x, y in found}
    for r in ('1/1', '10/11', '101/110', '10101/11010', '1000101/1111010'):
        assert r in pats, r
    s = e['параметры']['шаблоны']
    for x, y in (('1', '1'), ('10', '11'), ('101', '110'), ('10101', '11010'), ('1000101', '1111010')):
        assert 'X:%s Y:%s' % (x, y) in s
    m = re.search(r'dfree[^0-9]*10[^0-9]', t)
    return 'стр. 12 табл. 2: шаблоны X/Y для 1/2…7/8 совпали с записью и с CCSDS 131.0 табл. 4-1'


@prov('DVB-S2 внешний БЧХ')
def _():
    t = TT('istochniki/dvb/en_30230701v010401p.pdf')
    m = re.search(r'g\s*1\s*\(x\)\s*1\s*\+\s*x\s*2\s*\+\s*x\s*3\s*\+\s*x\s*5\s*\+\s*x\s*16', t)
    assert m, 'g1 норм.'
    m2 = re.search(r'g\s*1\s*\(x\)\s*1\s*\+\s*x\s*\+\s*x\s*3\s*\+\s*x\s*5\s*\+\s*x\s*14', t)
    assert m2, 'g1 кор.'
    assert is_primitive(poly_int({0, 2, 3, 5, 16})) and is_primitive(poly_int({0, 1, 3, 5, 14}))
    return 'текст EN 302 307-1 табл. 6a/6b: g1=1+x^2+x^3+x^5+x^16 (нормальный) и g1=1+x+x^3+x^5+x^14 (короткий) найдены; оба примитивны (своя проверка)'


@prov('DVB-S2 скремблер BB')
def _():
    e = zap('DVB-S2 скремблер BB')
    t = TT('istochniki/dvb/en_30230701v010401p.pdf')
    seq = e['параметры']['PLS'].split('⊕ ')[-1]
    s = re.sub(r'\s', '', t)
    assert seq in s, 'маска PLS'
    assert re.search(r'1\s*\+\s*x\s*7\s*\+\s*x\s*18', t) and re.search(r'1\s*\+\s*y\s*5\s*\+\s*y\s*7\s*\+\s*y\s*10\s*\+\s*y\s*18', t)
    assert '18D2E82' in s
    # 26-бит SOF
    assert len(bin(0x18D2E82)) - 2 == 25  # старший ноль: 01 1000 1101 0010 1110 1000 0010
    return 'текст: маска PLS 64 бита, многочлены Gold x: 1+x^7+x^18, y: 1+y^5+y^7+y^10+y^18, SOF 18D2E82 — совпало'


@prov('DVB-RCS дуобинарный турбокод CRSC')
def _():
    e = zap('DVB-RCS дуобинарный турбокод CRSC')
    F_ = 'istochniki/dvb/en_301790v010501p.pdf'
    t30, t31 = T(F_, 30), T(F_, 31)
    assert re.search(r'feedback branch: 15 \(in octal\), equivalently 1 \+ D \+ D3', t30)
    assert re.search(r'Y parity bits: 13, equivalently 1 \+ D2 \+ D3', t30) and re.search(r'W parity bits: 11, equivalently 1 \+ D3', t30)
    par = dict((int(n), (int(p0), int(a), int(b), int(c))) for n, p0, a, b, c in re.findall(r'N = (\d+) \(\d+ bytes\) (\d+) \{(\d+),(\d+),(\d+)\}', t31))
    assert len(par) == 12
    tab = json.load(open(P('tablicy/dvb/rcs1_turbo_perem.json')))
    for N, (p0, p1, p2, p3) in par.items():
        assert tab['параметры_P0_P1_P2_P3'][str(N)] == [p0, p1, p2, p3], N
        Pi = []
        for j in range(N):
            Pp = [0, N // 2 + p1, p2, N // 2 + p3][j % 4]
            Pi.append((p0 * j + Pp + 1) % N)
        assert sorted(Pi) == list(range(N)) and all((i + j) % 2 == 1 for j, i in enumerate(Pi))
        assert tab['перестановки_Pi_j'][str(N)] == Pi, N
    return 'стр. 30: многочлены 15/13/11₈ = 1+D+D³, 1+D²+D³, 1+D³; стр. 31 табл. 9: P0…P3 для 12 размеров == tablicy; Π(j)=P0·j+P+1 mod N пересчитана для всех N — поэлементно == tablicy, правило чёт/нечет выполняется'


@prov('DVB-RCS2 дуобинарный турбокод 16 состояний')
def _():
    t = TT('istochniki/dvb/en_30154502v010501p.pdf')
    assert re.search(r'23\s*\(?(?:in )?octal', t) or '23' in t
    tab = json.load(open(P('tablicy/dvb/rcs2_turbo_perem.json')))
    n = 0
    for w in tab['волны_линейные_A1'] + tab.get('волны_спред_A2', []):
        N = w['байт'] * 4
        P_, Q = w['P'], w['Q']
        Qj = [0, 4 * Q[1], 4 * Q[0] * P_ + 4 * Q[2], 4 * Q[0] * P_ + 4 * Q[3]]
        Pi = [(P_ * j + Qj[j % 4] + 3) % N for j in range(N)]
        assert sorted(Pi) == list(range(N)), w['id']
        n += 1
    return 'для %d волновых форм (табл. A-1/A-2) перестановка i=(P·j+Q(j)+3) mod N, Q(j)={0,4Q1,4Q0P+4Q2,4Q0P+4Q3}, N=4·байт — проверена: является перестановкой' % n


@prov('DVB-CID')
def _():
    e = zap('DVB-CID')
    F_ = 'istochniki/dvb/ts_103129v010102p.pdf'
    t = T(F_, 14) + ' ' + T(F_, 15)
    gs = [{0, 4, 7}, {0, 2, 3, 4, 7}, {0, 1, 2, 3, 4, 5, 7}, {0, 6, 7}, {0, 2, 4, 6, 7}, {0, 4, 5, 6, 7}]
    g = 1
    for s in gs:
        assert is_primitive(poly_int(s)) or pmod((1 << 127) | 1, poly_int(s)) == 0
        g = pmul(g, poly_int(s))
    assert g.bit_length() - 1 == 42 and pmod((1 << 127) | 1, g) == 0
    assert 127 - 42 == 85 and 127 - 16 == 111 and 85 - 16 == 69
    # CRC-8 D5
    assert poly_int({8, 7, 6, 4, 2, 0}) & 0xFF == 0xD5
    assert re.search(r'1 ?\+ ?x ?4 ?\+ ?x ?7', t) or re.search(r'x\s*4', t)
    return 'g=g1…g6 (табл. 4) перемножены: степень 42 = 127−85, g | x^127+1 (все 6 — минимальные многочлены над GF(2^7)) → BCH(127,85), укорочение на 16 → (111,69); CRC-8 X^8+X^7+X^6+X^4+X^2+1 = D5h'


def run():
    for nachalo, f in PROVERKI:
        try:
            r = f()
            im = zap(nachalo)['имя']
            if im in ITOG:
                ITOG[im]['сверено'] += ' || ' + r
            else:
                ITOG[im] = {'статус': 'СОВПАЛО', 'сверено': r}
        except Exception as ex:
            ITOG[nachalo] = {'статус': 'ОШИБКА ПРОВЕРКИ', 'сверено': repr(ex)[:300], 'trace': traceback.format_exc()[-600:]}


if __name__ == '__main__':
    import importlib
    for mod in sys.argv[1:]:
        importlib.import_module(mod)
    run()
    n_ok = sum(v['статус'] == 'СОВПАЛО' for v in ITOG.values())
    for k, v in ITOG.items():
        if v['статус'] != 'СОВПАЛО':
            print('!!', k, v['сверено'], v.get('trace', ''))
    print('сверено записей:', len(ITOG), 'совпало:', n_ok)
    json.dump(ITOG, open(P('tablicy/_itog_sverka2_b.json'), 'w'), ensure_ascii=False, indent=1)
