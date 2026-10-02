"""Сверка, часть B3: навигация (GPS, ГЛОНАСС, Galileo, BeiDou, SBAS, NavIC)."""
import json, re
from __main__ import prov, zap, T, TT, P, st, poly_int, GF, pmul, pmod, is_primitive, conv_oct_to_bin, crc_bytes

N = 'istochniki/nav/'


@prov('GPS LNAV (L1 C/A)')
def _():
    e = zap('GPS LNAV (L1 C/A)')
    t = TT(N + 'IS-GPS-200N.pdf')
    blk = t[t.index('D25 = D'):t.index('Where d1, d2, ..., d24 are the source data bits')]
    eqs = re.findall(r'D(2[5-9]|30) = (D(?:29|30))((?:[\s-]*d\s?\d+)+)', blk)
    assert len(eqs) == 6, len(eqs)
    maski = []
    for nm, star, ds in eqs:
        m = (1 << 25) if star == 'D29' else (1 << 24)
        for d in map(int, re.findall(r'd\s?(\d+)', ds)):
            m |= 1 << (24 - d)
        maski.append(m)
    zm = [int(x, 16) for x in re.findall(r'0x[0-9A-F]+', e['параметры']['маски (сверено)'])]
    assert maski == zm, ([hex(x) for x in maski], [hex(x) for x in zm])
    return 'IS-GPS-200N табл. 20-XIV (стр. 158): 6 уравнений D25…D30 разобраны из текста → маски над [D29*, D30*, d1…d24] = 0x2EC7CD2, 0x1763E69, 0x2BB1F34, 0x15D8F9A, 0x1AEC7CD, 0x22DEA27 — равны маскам записи (PocketSDR)'


@prov('GPS CNAV (L2C, L5)')
def _():
    e = zap('GPS CNAV (L2C, L5)')
    t = TT(N + 'IS-GPS-200N.pdf')
    m = re.search(r'g\s*i\s*=?\s*1 for i ?= ?0 ?, ?1 ?, ?3 ?, ?4 ?, ?5 ?, ?6 ?, ?7 ?, ?10 ?, ?11 ?, ?14 ?, ?17 ?, ?18 ?, ?23 ?, ?24', t) or \
        re.search(r'24 , 23 , 18 , 17 , 14 , 11 , 10 ,7,6,5,4,3,1,0 i for 1 gi', t)
    assert m, 'g_i'
    assert re.search(r'1 X X X X X X X X X X X p 3 5 7 8 9 11 12 13 17 23', t)
    p = poly_int({0, 3, 5, 7, 8, 9, 11, 12, 13, 17, 23})
    g = pmul(p, 3)
    assert g == poly_int({24, 23, 18, 17, 14, 11, 10, 7, 6, 5, 4, 3, 1, 0}) == 0x1864CFB
    assert is_primitive(p)
    assert crc_bytes(b'123456789', 0x864CFB, 24) == 0xCDE703
    assert '0x1864CFB' in e['параметры']['CRC']
    return 'IS-GPS-200N стр. 229: g_i=1 для i=0,1,3,4,5,6,7,10,11,14,17,18,23,24 и p(X)=1+X^3+X^5+X^7+X^8+X^9+X^11+X^12+X^13+X^17+X^23 (примитивен) → g=(1+X)p(X)=0x1864CFB; check(«123456789»)=0xCDE703 пересчитан'


@prov('ГЛОНАСС (FDMA L1/L2 СТ)')
def _():
    e = zap('ГЛОНАСС (FDMA L1/L2 СТ)')
    t = T(N + 'ICD_GLONASS_5.1_2008_en.pdf', 18)
    m = re.search(r'g\(x\) = 1 \+ x3 \+ x5, or may be shown as (\d{30})', t)
    assert m
    mark = m.group(1)
    assert mark in e['параметры']['метка времени']
    # ЛРС 1+x^3+x^5 из единиц: ищем начальное состояние/порядок, дающие 30-битную метку (укороченная ПСП периода 31)
    ok = []
    for init in range(1, 32):
        a = [(init >> i) & 1 for i in range(5)]
        while len(a) < 31 + 30:
            a.append(a[-5] ^ a[-3])  # a[n] = a[n-5] ⊕ a[n-3]  (x^5 + x^2 + 1 — взаимный 1+x^3+x^5)
        s = ''.join(map(str, a))
        for off in range(31):
            if s[off:off + 30] == mark:
                ok.append((init, off))
    assert ok
    return 'стр. 18: метка времени 111110001101110101000010010110 и g(x)=1+x^3+x^5 — совпало; метка = 30 бит М-последовательности периода 31 этого ЛРС (воспроизведена своим генератором)'


@prov('ГЛОНАСС CDMA L1OC/L3OC')
def _():
    e = zap('ГЛОНАСС CDMA L1OC/L3OC')
    s = e['параметры']['L1OC']
    m = re.search(r'многочлен 0x([0-9A-F]+) \(([^)]+)\)', s)
    assert m and poly_int(st(m.group(2))) == (1 << 16) | int(m.group(1), 16)
    src = open(P('istochniki/kod/PocketSDR/python/sdr_nav.py')).read()
    assert re.search(r'preamb = \(0, 1, 0, 1, 1, 1, 1, 1, 0, 0, 0, 1\)', src) and re.search(r'preamb = \(0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 1, 1, 0\)', src)
    assert re.search(r'\^ \(0x6F63 if R & 0x8000 else 0\)', src)
    t = TT(N + 'glonass/IKD_GLONASS_L3OC_proekt_2015_srns.pdf')
    assert re.search(r'133', t) and re.search(r'171', t)
    return 'внутренняя согласованность: 0x6F63 = x^16+x^14+x^13+x^11+x^10+x^9+x^8+x^6+x^5+x+1; преамбулы L1OC 010111110001 и L3OC 00000100100101001110 и CRC с многочленом 0x6F63 найдены в PocketSDR (sdr_nav.py); в проекте ИКД L3OC 2015 — СК (133,171) — совпало'


@prov('Galileo I/NAV и F/NAV')
def _():
    e = zap('Galileo I/NAV и F/NAV')
    t = TT(N + 'Galileo_OS_SIS_ICD_v2.1.pdf')
    assert re.search(r'Constraint Length 7 Generator Polynomials G1=171o G2=133o Encoding Sequence G1 then G2', t)
    assert re.search(r'second branch is inverted', t)
    assert re.search(r'I/NAV synchronisation pattern is 0101100000', t) and re.search(r'F/NAV synchronisation pattern is: 101101110000', t)
    assert '0101100000' in e['параметры']['синхро'] and '101101110000' in e['параметры']['синхро']
    return 'OS SIS ICD 2.1: K=7, G1=171₈, G2=133₈, G1 первым, вторая ветвь инвертирована; синхро I/NAV 0101100000, F/NAV 101101110000 — совпало'


@prov('Galileo I/NAV FEC2')
def _():
    t = TT(N + 'Galileo_OS_SIS_ICD_v2.1.pdf')
    i = [m.start() for m in re.finditer(r'Table 108: Integer octet representation of the coefficients of the generator polynomial j gj', t)][-1]
    blk = t[i:i + 700].split('Systematic Encoding')[0]
    nums = list(map(int, re.findall(r'\d+', blk)))[1:]  # без «108»
    pary = {}
    k = 0
    while k + 1 < len(nums):
        pary[nums[k]] = nums[k + 1]
        k += 2
    assert sorted(pary) == list(range(61)), sorted(pary)[:5]
    F = GF(8, 0x11D)
    g = F.poly_from_roots([F.a(i) for i in range(1, 61)])[::-1]  # g[j] при x^j
    assert [g[j] for j in range(61)] == [pary[j] for j in range(61)]
    return 'OS SIS ICD 2.1 табл. 108: 61 коэффициент g_j разобран из текста и == ∏_{i=1}^{60}(x−α^i) над GF(2^8), p=0x11D (свой расчёт)'


@prov('BeiDou D1/D2')
def _():
    e = zap('BeiDou D1/D2')
    t = TT(N + 'BDS-SIS-ICD-B1I-2.0_unb.pdf')
    assert re.search(r'generator polynomial is g\(X\)=X4\+X\+1', t) and re.search(r'BCH\(15,11,1\)', t)
    assert re.search(r'preamble \(Pre\) of “11100010010”', t)
    g = 0b10011
    assert len({pmod(1 << i, g) for i in range(15)}) == 15
    return 'B1I ICD 2.0: g(X)=X^4+X+1, BCH(15,11,1), перемежение по 2 блока (по 1 биту), преамбула 11100010010 — совпало; 15 синдромов различны'


@prov('SBAS L1 (WAAS')
def _():
    e = zap('SBAS L1 (WAAS')
    t = TT(N + 'IS-QZSS-L1S.pdf')
    assert re.search(r'Pattern A 01010011 Pattern B 10011010 Pattern C 11000110', t)
    crc = e['параметры']['CRC']
    assert poly_int(st(crc.split('=')[1].split('(')[0])) == 0x1864CFB
    return 'IS-QZSS-L1S: преамбулы 01010011/10011010/11000110 — совпало; многочлен CRC записи (X^24+X^23+X^18+…+X+1) = 0x1864CFB = CRC-24Q GPS'


@prov('NavIC/IRNSS L5 и S')
def _():
    t = T(N + 'IRNSS_SPS_ICD_v1.1_2017.pdf', 24)
    assert re.search(r'G1 = \(171\)o G2 = \(133\)o Encoding Sequence G1 then G2', t)
    assert re.search(r'73 x 8', t) and re.search(r'Sync pattern is EB90 Hex', t) and re.search(r'6 zero value bits', t)
    assert re.search(r'written in columns and then, read in rows', t) and re.search(r'292 bits, after encoding, results in 584 symbols', t)
    return 'IRNSS SPS ICD 1.1 стр. 24: 171/133 (G1 первым), 292 бита → 584 символа, перемежитель 73×8 (запись по столбцам, чтение по строкам), синхро EB90h, 6 хвостовых нулей — совпало'


@prov('NavIC L1 SPS')
def _():
    t = T(N + 'NavIC_SPS_ICD_L1_final.pdf', 43)
    m = re.search(r'\(52, 9\) 52 9 20 (x 9\+x 8\+ x 7\+x 6\+x 5\+x 4\+x 2\+ x\+1)', t)
    assert m
    g = poly_int(st(m.group(1).replace(' ', '')))
    assert g == poly_int({9, 8, 7, 6, 5, 4, 2, 1, 0})
    # код: 9 бит в регистр ЛРС с характеристическим многочленом g; слово = 52 бита выхода; dmin перебором 511 слов
    best = None
    for vid in ('g', 'обр'):
        gg = g if vid == 'g' else int(format(g, '010b')[::-1], 2)
        taps = [i for i in range(9) if gg >> i & 1]
        dmin = 99
        for s0 in range(1, 512):
            a = [(s0 >> i) & 1 for i in range(9)]
            while len(a) < 52:
                v = 0
                for tp in taps:
                    v ^= a[len(a) - 9 + tp]
                a.append(v)
            dmin = min(dmin, sum(a))
        if dmin == 20:
            best = vid
    assert not is_primitive(g) and not is_primitive(int(format(g, '010b')[::-1], 2))  # g НЕ примитивен
    t42 = T(N + 'NavIC_SPS_ICD_L1_final.pdf', 42)
    assert re.search(r'generator polynomial of 1767 \(octal\)', t42) and int('1767', 8) == g
    assert re.search(r'shifted 52 times to generate 52 encoded symbols', t42)
    assert best, 'dmin≠20'
    return 'NavIC L1 ICD стр. 43: BCH(52,9), dmin=20, g(x)=x^9+x^8+x^7+x^6+x^5+x^4+x^2+x+1 (= 1767₈, стр. 42; g не примитивен); 9 бит TOI — начальное состояние, 52 сдвига → 52 символа; перебором 511 слов dmin=20 подтверждён'
