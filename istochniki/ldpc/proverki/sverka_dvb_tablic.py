# Сверка таблиц адресов аккумуляторов чётности DVB-S2 / DVB-S2X / DVB-T2
# по трём независимым источникам:
#   1) текст стандарта ETSI (извлечён из PDF: standarty/*.pdf.txt);
#   2) xdsopl/LDPC (dvb_s2_tables.hh, dvb_s2x_tables.hh, dvb_t2_tables.hh);
#   3) drmpeg/gr-dvbs2 (lib/ldpc_bb_impl.cc) — только S2 и S2X.
# Сравниваются плоские последовательности чисел таблицы (порядок строк и чисел).
import re, os, sys
from collections import OrderedDict

ZDES = os.path.dirname(os.path.abspath(__file__))
KOREN = os.path.dirname(ZDES)
XDS = os.path.join(KOREN, 'otkrytyj_kod', 'xdsopl-LDPC')
GRD = os.path.join(KOREN, 'otkrytyj_kod', 'gr-dvbs2', 'lib', 'ldpc_bb_impl.cc')


def tablicy_etsi(put, prefiksy):
    """Таблицы стандарта: заголовок 'Table X.n:' и следом строки из чисел."""
    tekst = open(put, encoding='utf8', errors='ignore').read().split('\n')
    tablicy = OrderedDict()
    tek = None
    for s in tekst:
        m = re.match(r'^Table ([A-C])\.(\d+):', s)
        if m and m.group(1) in prefiksy:
            tek = m.group(1) + m.group(2)
            if tek in tablicy:          # повтор заголовка в оглавлении/ссылке
                tek = None
                continue
            tablicy[tek] = []
            continue
        if tek is None:
            continue
        if s.startswith('Annex') or s.startswith('Table '):
            tek = None
            continue
        # строка таблицы: числа через пробел; одиночное число (перенос строки таблицы) — только
        # с пробелом в конце: номер страницы в тексте PDF стоит один и без пробела
        if re.match(r'^\d+( \d+)* ?\s*$', s) and (' ' in s.strip() or s.endswith(' ')):
            tablicy[tek].extend(int(x) for x in s.split())
    return {k: v for k, v in tablicy.items() if v}


def tablicy_xdsopl(put, imya):
    t = open(put).read()
    rez = {}
    for m in re.finditer(r'struct ' + imya + r'_TABLE_([A-C]\d+)\s*\{(.*?)\n\};', t, re.S):
        telo = m.group(2)
        N = int(re.search(r'int N = (\d+)', telo).group(1))
        K = int(re.search(r'int K = (\d+)', telo).group(1))
        pos = re.search(r'POS\[\] = \{(.*?)\}', telo, re.S).group(1)
        rez[m.group(1)] = (N, K, [int(x) for x in re.findall(r'\d+', pos)])
    return rez


def tablicy_grdvbs2(put):
    t = open(put).read()
    rez = {}
    for m in re.finditer(r'ldpc_bb_impl::ldpc_tab_(\w+?)([NSM])\[(\d+)\]\[(\d+)\]=\s*\{(.*?)\n    \};', t, re.S):
        N = {'N': 64800, 'S': 16200, 'M': 32400}[m.group(2)]
        chisla = []
        for stroka in re.findall(r'\{([^{}]*)\}', m.group(5)):
            v = [int(x) for x in stroka.split(',') if x.strip()]
            chisla.extend(v[1:1 + v[0]])      # первое число — количество
        K = int(m.group(3)) * 360
        rez[(N, K, m.group(1))] = chisla
    return rez


def sverit(etsi_put, prefiksy, imya_xds, fayl_xds, s_grd=True):
    E = tablicy_etsi(etsi_put, prefiksy)
    X = tablicy_xdsopl(os.path.join(XDS, fayl_xds), imya_xds)
    G = tablicy_grdvbs2(GRD) if s_grd else {}
    itog = []
    for k, (N, K, pos) in X.items():
        assert k in E, f'{imya_xds} {k}: таблицы нет в тексте ETSI'
        assert E[k] == pos, f'{imya_xds} {k}: ETSI и xdsopl различаются'
        gr = [v for (n, kk, _), v in G.items() if n == N and kk == K]
        if s_grd:
            assert len(gr) >= 1, f'{imya_xds} {k}: нет в gr-dvbs2 (N={N}, K={K})'
            assert any(g == pos for g in gr), f'{imya_xds} {k}: gr-dvbs2 отличается'
        itog.append((k, N, K, len(pos), len(gr)))
    return itog


if __name__ == '__main__':
    S = os.path.join(KOREN, 'standarty')
    r1 = sverit(os.path.join(S, 'ETSI_EN_302_307-1_v1.4.1_DVB-S2.pdf.txt'), 'BC', 'DVB_S2', 'dvb_s2_tables.hh')
    r2 = sverit(os.path.join(S, 'ETSI_EN_302_307-2_v1.4.1_DVB-S2X.pdf.txt'), 'BC', 'DVB_S2X', 'dvb_s2x_tables.hh')
    r3 = sverit(os.path.join(S, 'ETSI_EN_302_755_v1.4.1_DVB-T2.pdf.txt'), 'AB', 'DVB_T2', 'dvb_t2_tables.hh', s_grd=False)
    for imya, r in (('DVB-S2 (EN 302 307-1)', r1), ('DVB-S2X (EN 302 307-2)', r2), ('DVB-T2 (EN 302 755)', r3)):
        print(imya, 'таблиц совпало:', len(r))
        for k, N, K, n, g in r:
            print(f'   {k:4s} N={N} K={K} чисел={n} в_gr-dvbs2={g}')
    assert len(r1) == 21 and len(r3) >= 15
    print('ВСЁ СОВПАЛО')
