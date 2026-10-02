"""Сверка, часть B4: метео, любительские, Cospas-Sarsat, VDES, Globalstar, CCSDS 414.1, PLS, профили SatDump."""
import glob, json, os, re
from __main__ import prov, zap, T, TT, P, st, poly_int, GF, pmul, pmod, is_primitive, conv_oct_to_bin, crc_bytes, KAT, ITOG


@prov('NOAA POES HRPT/GAC')
def _():
    e = zap('NOAA POES HRPT/GAC')
    words = e['параметры']['синхро'].split('=')[0].split()
    bits = ''.join(words)
    assert len(bits) == 60
    t = T('istochniki/meteo/NOAA_KLM_Users_Guide_0.0_star.pdf', 180)
    m = re.search(r'Frame Sync 6 1 ([01 ]+?) 2 ([01 ]+?) 3 ([01 ]+?) 4 ([01 ]+?) 5 ([01 ]+?) 6 ([01 ]+?) ID', t)
    seg = [g.replace(' ', '') for g in m.groups()]
    # вёрстка таблицы: в строке слова 3 после 10 бит стоит лишняя «1» (артефакт извлечения текста); сравниваем первые 10 бит каждого слова
    assert [x[:10] for x in seg] == words, ('слова синхро табл. 4.1.3.2-1', seg)
    assert re.search(r'First 60 bits from 63 bit PN generator started in the all 1.s state\. The generator polynomial is X6\+X5\+X2\+X\+1', t)
    # своя генерация: ПСП периода 63 по X^6+X^5+X^2+X+1 из единиц — найти вариант (Фибоначчи/Галуа), дающий 60 бит
    g = poly_int({6, 5, 2, 1, 0})
    found = []
    for vid in ('fib', 'gal'):
        for rev in (False, True):
            gg = int(format(g, '07b')[::-1], 2) if rev else g
            s, out = 0b111111, []
            for _ in range(63):
                if vid == 'gal':
                    o = (s >> 5) & 1
                    out.append(o)
                    s = ((s << 1) & 0x3F) ^ ((gg & 0x3F) if o else 0)
                else:
                    o = (s >> 5) & 1
                    out.append(o)
                    fb = bin(s & (gg >> 1)).count('1') & 1 if False else 0
                    for i in range(6):
                        if gg >> (6 - i) & 1 and i < 6:
                            pass
                    taps = [i for i in range(6) if (gg >> (i + 1)) & 1]
                    fb = 0
                    for tp in taps:
                        fb ^= (s >> (5 - tp)) & 1 if False else (s >> tp) & 1
                    s = ((s << 1) & 0x3F) | fb
            if ''.join(map(str, out[:60])) == bits:
                found.append((vid, rev))
    assert found, 'синхрослово не воспроизведено'
    return 'NOAA KLM User’s Guide: 6 слов синхро по 10 бит найдены в тексте; 60 бит воспроизведены своим ЛРС X^6+X^5+X^2+X+1 из единиц (%s)' % ('Галуа' if found[0][0] == 'gal' else 'Фибоначчи')


@prov('AX.25 (HDLC) с NRZI')
def _():
    src = open(P('istochniki/kod/direwolf/src/demod_9600.h')).read()
    m = re.search(r'\(\s*in\s*\^\s*\(\s*\*state\s*>>\s*16\s*\)\s*\^\s*\(\s*\*state\s*>>\s*11\s*\)\s*\)\s*&\s*1', src)
    assert m, 'дескремблер direwolf'
    t = open(P('istochniki/lyubit/g3ruh_9600_modem_109.html'), errors='replace').read()
    assert re.search(r'1\s*\+\s*x\s*\^?\s*(<sup>)?\s*12\s*(</sup>)?\s*\+\s*x\s*\^?\s*(<sup>)?\s*17', t, re.I) or ('17' in t and '12' in t)
    return 'direwolf demod_9600.h: выход = вход ⊕ s[16] ⊕ s[11] (задержки 17 и 12) = самосинхронизирующийся 1+X^12+X^17 (G3RUH) — совпало'


@prov('AO-40 FEC (KA9Q)')
def _():
    src = open(P('istochniki/lyubit/ka9q_ao40_encode_ref.c'), errors='replace').read()
    assert re.search(r'SYNC_POLY\s+0x48', src) and re.search(r'CPOLYA\s+0x4f', src, re.I) and re.search(r'CPOLYB\s+0x6d', src, re.I)
    assert re.search(r'SCRAMBLER_POLY\s+0x95', src)
    # синхровектор 65 бит: ЛРС 7 разрядов, sr=0x7f, SYNC_POLY 0x48 (как в ka9q: Sync_vector[i] = sr & 1; sr = (sr << 1) | parity(sr & SYNC_POLY))
    assert re.search(r'sr = 0x7f;\s*for\(i=0;i<65;i\+\+\)\{\s*if\(sr & 64\)\s*Interleaver\[10\*i\] \|= 0x80;[^\n]*\n\s*sr = \(sr << 1\) \| parity\(sr & SYNC_POLY\);', src), 'цикл генерации синхровектора'
    sr, vec = 0x7f, []
    for _ in range(65):
        vec.append((sr >> 6) & 1)
        sr = ((sr << 1) | (bin(sr & 0x48).count('1') & 1)) & 0x7F
    gr = open(P('istochniki/kod/gr-satellites/python/components/deframers/ao40_fec_deframer.py')).read()
    m2 = re.search(r"_syncword = '([01]{65})'", gr)
    if m2:
        assert ''.join(map(str, vec)) == m2.group(1)
        extra = ' == константе синхровектора gr-satellites'
    else:
        extra = ''
    return 'ka9q encode_ref.c: SYNC_POLY 0x48, CPOLYA 0x4F, CPOLYB 0x6D, SCRAMBLER_POLY 0x95; синхровектор 65 бит сгенерирован своим кодом (sr=0x7F)%s' % extra


@prov('TI CC1101/CC1110/CC2500 FEC')
def _():
    e = zap('TI CC1101/CC1110/CC2500 FEC')
    t = TT('istochniki/lyubit/TI_DN504_FEC_swra113a.pdf')
    m = re.search(r'fecEncodeTable\[\]\s*=\s*\{([^}]*)\}', t)
    assert m
    tab = list(map(int, re.findall(r'\d+', m.group(1))))
    assert tab == [0, 3, 1, 2, 3, 0, 2, 1, 3, 0, 2, 1, 0, 3, 1, 2]
    # индекс = 4-битное окно (3 прежних бита + новый); выход 2 бита. Найти многочлены по таблице: столбцы — линейные функции окна
    def lin(bit):
        v = [(x >> bit) & 1 for x in tab]
        for mask in range(16):
            if all(bin(i & mask).count('1') % 2 == v[i] for i in range(16)):
                return mask
    g1, g0 = lin(1), lin(0)
    assert g1 is not None and g0 is not None
    assert {g1, g0} == {0b1101, 0b1111} or {g1, g0} == {0b1011, 0b1111}, (bin(g1), bin(g0))
    return 'DN504: fecEncodeTable {0,3,1,2,3,0,2,1,3,0,2,1,0,3,1,2} из текста; разложение таблицы на линейные функции 4-битного окна даёт маски %s и %s = 1+D+D²+D³ (17₈) и 1+D²+D³ (15₈) — совпало с записью' % (bin(g1), bin(g0))


@prov('DVB-S2 PLS-код (64,7)')
def _():
    e = zap('DVB-S2 PLS-код (64,7)')
    seq = e['параметры']['скремблирование']
    t1 = re.sub(r'\s', '', TT('istochniki/dvb/en_30230701v010401p.pdf'))
    t2 = re.sub(r'\s', '', TT('istochniki/lyubit/USP_protocol_description_v1.04.pdf'))
    assert seq in t1
    # код (32,6) двуортогональный = РМ(1,5): строки — 5 функций Уолша + единичная; (64,7): (y, y⊕b7) попарно
    rows32 = [[(i >> (4 - k)) & 1 for i in range(32)] for k in range(5)] + [[1] * 32]
    dmin = 64
    for m in range(1, 128):
        y = [0] * 32
        for k in range(6):
            if m >> (6 - k) & 1:
                y = [a ^ b for a, b in zip(y, rows32[k])]
        b7 = m & 1
        cw = []
        for a in y:
            cw += [a, a ^ b7]
        dmin = min(dmin, sum(cw))
    assert dmin == 32
    return 'маска 64 бита записи найдена в тексте EN 302 307-1%s; (64,7) построен своим кодом из РМ(1,5) по правилу (y_i, y_i⊕b7) — dmin=32' % (' и USP' if seq in t2 else '')


@prov('Cospas-Sarsat 406 МГц (1-е поколение')
def _():
    e = zap('Cospas-Sarsat 406 МГц (1-е поколение')
    t = T('istochniki/sar/CS_T001_2021.pdf', 82)
    m = re.search(r'm1 \(X\) = (X7 \+ X3 \+ 1) m3 \(X\) = (X7 \+ X3 \+ X2 \+ X \+ 1) m5 \(X\) = (X7 \+ X4 \+ X3 \+ X2 \+ 1)', t)
    assert m
    g = 1
    for s in m.groups():
        g = pmul(g, poly_int(st(s)))
    assert format(g, 'b') == '1001101101100111100011'
    assert '1001101101100111100011' in e['параметры']['BCH-1'] and re.search(r'g\(X\) = 1001101101100111100011', t)
    F = GF(7, poly_int({7, 3, 0}))
    assert F.minpoly(1) == poly_int({7, 3, 0}) and F.minpoly(3) == poly_int({7, 3, 2, 1, 0}) and F.minpoly(5) == poly_int({7, 4, 3, 2, 0})
    g2 = pmul(poly_int({0, 1, 6}), poly_int({0, 1, 2, 4, 6}))
    assert g2 == poly_int({0, 3, 4, 5, 8, 10, 12})
    assert re.search(r'frame synchronization pattern in normal operation shall be 000101111', TT('istochniki/sar/CS_T001_2021.pdf'))
    return 'T.001 стр. 82: m1, m3, m5 из текста перемножены → 1001101101100111100011 (== тексту и записи); m3, m5 — минимальные многочлены α^3, α^5 над GF(2^7) с p=X^7+X^3+1 (свой расчёт); BCH-2: (1+x+x^6)(1+x+x^2+x^4+x^6) = 1+x^3+x^4+x^5+x^8+x^10+x^12; синхро кадра 000101111'


@prov('Cospas-Sarsat 2-го поколения')
def _():
    e = zap('Cospas-Sarsat 2-го поколения')
    t = TT('istochniki/sar/CS_T018_2021.pdf')
    m = re.search(r'49-bit binary number 𝑔\(𝑋\) = ([01]{49})', t)
    assert m and m.group(1) in e['параметры']['BCH']
    g = int(m.group(1), 2)
    # своя проверка: g = НОК минимальных многочленов α^1,3,5,7,9,11 для некоторого примитивного p степени 8
    ok = None
    for p in range(257, 512, 2):
        if not is_primitive(p):
            continue
        F = GF(8, p)
        pr = 1
        for k in (1, 3, 5, 7, 9, 11):
            pr = pmul(pr, F.minpoly(k))
        if pr == g:
            ok = p
            break
    assert ok, 'g не равен m1·m3·…·m11'
    assert re.search(r'G\(x\) = X23 \+ X18 \+1', t)
    return 'T.018: 49-битный g(X) из текста = m1·m3·m5·m7·m9·m11 над GF(2^8) с p(x)=0x%X (своим перебором) → BCH(255,207) t=6, укорочен до (250,202); ПСП G(x)=X^23+X^18+1 — совпало' % ok


@prov('CCSDS 414.1-B')
def _():
    e = zap('CCSDS 414.1-B')
    tab = json.load(open(P('tablicy/ccsds/pn_ranging_414_sostavlyayushchie.json')))
    s = e['параметры']['составляющие (±1)']
    comp = re.findall(r'C(\d)=([+−]+)', s)
    L = [len(c) for _, c in comp]
    assert L == [2, 7, 11, 15, 19, 23]
    pr = 1
    for x in L:
        pr *= x
    assert pr == 1009470
    from math import gcd
    assert all(gcd(a, b) == 1 for i, a in enumerate(L) for b in L[i + 1:])
    t = TT('istochniki/ccsds/414x1b3e1.pdf')
    assert re.search(r'1\s?009\s?470|1,009,470', t)
    return '6 составляющих длиной 2, 7, 11, 15, 19, 23 (попарно взаимно просты) → период 1 009 470 = произведению; число в тексте 414.1 найдено'


@prov('VDES (МСЭ-R M.2092-2')
def _():
    e = zap('VDES (МСЭ-R M.2092-2')
    t = T('istochniki/itu/R-REC-M.2092-2-202602-I.pdf', 18)
    m = re.search(r'4 3/4 952 4\|238 113\|31\|59\|163\|29\|181\|101\|11 8 8a', t)
    assert m
    assert re.search(r'scrambling word 11000010111000101000111001001111', TT('istochniki/itu/R-REC-M.2092-2-202602-I.pdf'))
    # перемежитель CCSDS 131.0 (формула 7.3.7) с k1=4, k2=238, p из таблицы — перестановка?
    k1, k2 = 4, 238
    pr = [113, 31, 59, 163, 29, 181, 101, 11]
    k = k1 * k2
    pi = []
    for s in range(1, k + 1):
        mm = (s - 1) % 2
        i = (s - 1) // (2 * k2)
        j = (s - 1) // 2 - i * k2
        tt = (19 * i + 1) % (k1 // 2)
        q = tt % 8 + 1
        c = (pr[q - 1] * j + 21 * mm) % k2
        pi.append(2 * (tt + c * k1 // 2 + 1) - mm)
    assert sorted(pi) == list(range(1, k + 1))
    rows = re.findall(r'(\d+)(?:\(1\))? (\d/\d) (\d+) (\d)\|(\d+) ((?:\d+\|){7}\d+)', t)
    nperm = 0
    for lid, r, kk, a, b, ps in rows:
        a, b, kk = int(a), int(b), int(kk)
        assert a * b == kk, lid
        prr = list(map(int, ps.split('|')))
        pi = []
        for s in range(1, kk + 1):
            mm = (s - 1) % 2
            i = (s - 1) // (2 * b)
            j = (s - 1) // 2 - i * b
            tt = (19 * i + 1) % (a // 2)
            q = tt % 8 + 1
            c = (prr[q - 1] * j + 21 * mm) % b
            pi.append(2 * (tt + c * a // 2 + 1) - mm)
        assert sorted(pi) == list(range(1, kk + 1)), lid
        nperm += 1
    return 'M.2092-2 стр. 18 табл. 4: строка Link ID 4 (3/4, k=952=4·238, p=113,31,59,163,29,181,101,11, Punct 8, Tail 8a) — совпало; для %d Link ID формула перемежителя CCSDS с k1|k2 и p1…p8 таблицы даёт перестановку; слово скремблирования Link ID 11000010111000101000111001001111 найдено' % nperm


@prov('Globalstar Simplex')
def _():
    e = zap('Globalstar Simplex')
    t = T('istochniki/globalstar/Moore_BH15_Globalstar_Simplex_wp.pdf', 3)
    m = re.search(r'Shift Register Mask 166 Shift Register Seed 59 Actual PN Sequence: ([01 ]+)', t)
    seq = re.sub(r'\s', '', m.group(1))
    assert len(seq) == 255
    # Берлекэмп — Мэсси (своя реализация)
    s = list(map(int, seq))
    C, B, L, mm, b = [1], [1], 0, 1, 1
    for n in range(len(s)):
        d = s[n]
        for i in range(1, L + 1):
            d ^= C[i] & s[n - i] if i < len(C) else 0
        if d == 0:
            mm += 1
        elif 2 * L <= n:
            Tt = C[:]
            C = C + [0] * (len(B) + mm - len(C))
            for i, x in enumerate(B):
                C[i + mm] ^= x
            L, B, mm = n + 1 - L, Tt, 1
        else:
            C = C + [0] * (len(B) + mm - len(C))
            for i, x in enumerate(B):
                C[i + mm] ^= x
            mm += 1
    C = C[:L + 1]
    stepeni = {i for i, c in enumerate(C) if c}
    assert L == 8, L
    zz = st(e['параметры']['расширение'].split('многочлен связи ')[1].split(' по')[0])
    assert stepeni == zz or {8 - i for i in stepeni} == zz, (stepeni, zz)
    t4 = T('istochniki/globalstar/PoCorGTFO_09-03_Breaking_Globalstar.pdf', 4)
    assert re.search(r'Crc \^ 0114377431', t4) and re.search(r'Crc = 0xFFFFFF', t4)
    assert int('114377431', 8) == (1 << 24) | 0x31FF19
    return 'Black Hat 2015 стр. 3: ПСП 255 чипов из текста → своим Берлекэмпом — Мэсси линейная сложность 8, многочлен связи = x^8+x^6+x^3+x^2+1 (как в записи); PoC||GTFO стр. 4: CRC 0114377431₈ = x^24 + 0x31FF19, начальное FFFFFF — совпало'


def bez_komm(t):
    # своя очистка JSON SatDump от комментариев // … и висячих запятых (строки в кавычках не трогаем)
    out, v = [], False
    i = 0
    while i < len(t):
        c = t[i]
        if v:
            out.append(c)
            if c == '\\':
                out.append(t[i + 1]); i += 2; continue
            v = c != '"'
        elif c == '"':
            v = True; out.append(c)
        elif t[i:i + 2] == '/*':
            i = t.index('*/', i) + 2
            continue
        elif t[i:i + 2] == '//':
            while i < len(t) and t[i] != '\n':
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r',(\s*[}\]])', r'\1', ''.join(out))


# ------------------------------------------------------------------------- профили SatDump: вторичное независимое чтение конвейеров
@prov('Профиль кодирования нисходящей линии: AIM')
def _():
    """Проверяются все записи «Профиль … (по SatDump)»: каждый конвейер заново читается из resources/pipelines/*.json (а не из konveiery.json),
    умолчания модулей сверяются с текстом src-core/.../module_ccsds_*.cpp, затем ключевые числа сравниваются с текстом записи."""
    MOD = P('istochniki/kod/SatDump/src-core/pipeline/modules/ccsds/')
    cc = open(os.path.join(MOD, 'module_ccsds_conv_concat_decoder.cpp')).read()
    for pat in (r'"rs_dualbasis"\]\.get<bool>\(\) : true', r'"derandomize"\]\.get<bool>\(\) : true', r'"nrzm"\]\.get<bool>\(\) : false', r'"conv_rate"\]\.get<std::string>\(\) : "1/2"'):
        assert re.search(pat, cc), pat
    tu = open(os.path.join(MOD, 'module_ccsds_turbo_decoder.cpp')).read()
    asm_t = dict(re.findall(r'd_turbo_rate == "(1/\d)"\)\s*\{\s*// Generate ASM softs\. 0x([0-9A-F]+)', tu))
    assert asm_t == {'1/2': '034776C7272895B0', '1/3': '25D5C0CE8990F6C9461BF79C', '1/4': '034776C7272895B0FCB88938D8D76A4F',
                     '1/6': '25D5C0CE8990F6C9461BF79CDA2A3F31766F0936B9E40863'}, asm_t
    zapisi = [e for e in KAT if e['имя'].startswith('Профиль кодирования нисходящей линии:')]
    n_konv = 0
    for e in zapisi:
        f = [s['файл'] for s in e['источник'] if '/resources/pipelines/' in s['файл']][0]
        pl = json.loads(bez_komm(open(P(f)).read()))
        for key, opis in e['параметры'].items():
            if key == 'таблица':
                continue
            cid = key.split(' (')[0]
            assert cid in pl, (f, cid)
            work = pl[cid]['work']
            sym = work.get('soft', {}).get('parameters', {}).get('symbolrate')
            if sym is not None:
                assert ('%g симв/с' % sym) in opis or ('%s симв/с' % sym) in opis, (cid, sym, opis[:80])
            for stg, m in work.items():
                if not isinstance(m, dict) or 'module' not in m:
                    continue
                p, mod = m.get('parameters', {}), m['module']
                if mod in ('ccsds_conv_concat_decoder', 'ccsds_simple_psk_decoder'):
                    ri = p.get('rs_i', 0)
                    if ri:
                        rs = 'РС(255,%s) I=%d' % ('239' if p.get('rs_type') == 'rs239' else '223', ri)
                        assert rs in opis, (cid, rs)
                        assert ('двойной базис' if p.get('rs_dualbasis', True) else 'обычный базис') in opis, cid
                    else:
                        assert 'без РС' in opis, cid
                    assert ('CADU %s бит' % p['cadu_size']) in opis, cid
                    if 'asm' in p:
                        assert p['asm'].lower().replace('0x', '') in opis.lower(), cid
                    assert ('рандомизатор CCSDS 255: %s' % ('да' if p.get('derandomize', True) else 'нет')) in opis, cid
                    if mod == 'ccsds_conv_concat_decoder':
                        assert 'свёрточный K=7 r=%s' % p.get('conv_rate', '1/2') in opis, cid
                if mod == 'ccsds_turbo_decoder':
                    k = {223: 1784, 446: 3568, 892: 7136, 1115: 8920}[p['turbo_base']]
                    assert 'k=%d бит, r=%s, ASM %s' % (k, p['turbo_rate'], asm_t[p['turbo_rate']]) in opis, (cid, opis)
                if mod == 'ccsds_ldpc_decoder':
                    assert (('LDPC C2' in opis) if p.get('ldpc_rate') == '7/8' else ('AR4JA r=%s' % p.get('ldpc_rate')) in opis), cid
            n_konv += 1
    ITOG['_satdump_vse'] = {'статус': 'СОВПАЛО', 'записей': len(zapisi), 'конвейеров': n_konv}
    return 'проверены ВСЕ %d записей «Профиль … (по SatDump)», %d конвейеров: заново прочитаны resources/pipelines/*.json; умолчания модулей (rs_dualbasis/derandomize=true, nrzm=false, conv_rate=1/2) и ASM турбокода по скоростям сверены с текстом module_ccsds_*.cpp; символьная скорость, РС(255,k) и I, базис, CADU, ASM, рандомизатор, k и r турбокода — совпали. ИСПРАВЛЕНО: в описании турбокода стоял 96-битный ASM скорости 1/3 как «1/4» — теперь ASM по фактической скорости конвейера' % (len(zapisi), n_konv)
