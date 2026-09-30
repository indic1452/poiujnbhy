# Сверка таблиц LDPC ATSC 3.0 (A/322:2026-04, приложение A, табл. A.1.1–A.1.12 и A.2.1–A.2.12)
# с открытым кодом drmpeg/gr-atsc3 (lib/ldpc_bb_impl.cc). Текст PDF читается по словам с координатами
# (PyMuPDF), в порядке блоков: у части таблиц хвосты строк свёрстаны отдельной правой колонкой.
import re, os
import pymupdf

ZDES = os.path.dirname(os.path.abspath(__file__))
K0 = os.path.dirname(ZDES)
PDF = os.path.join(K0, 'standarty', 'ATSC_A322-2026-04_Physical_Layer.pdf')
GR = os.path.join(K0, 'otkrytyj_kod', 'gr-atsc3', 'lib', 'ldpc_bb_impl.cc')


def stroki_stranicy(stranica):
    """Строки в порядке блоков PyMuPDF (блок, строка, слово): у широких таблиц A/322 хвосты
    строк стоят отдельной колонкой-блоком справа и по смыслу идут после левой колонки."""
    slova = sorted(stranica.get_text('words'), key=lambda w: (w[5], w[6], w[7]))
    stroki, tek, kl = [], [], None
    for w in slova:
        if kl is None or (w[5], w[6]) != kl:
            if tek:
                stroki.append(tek)
            tek, kl = [], (w[5], w[6])
        tek.append(w[4])
    if tek:
        stroki.append(tek)
    return stroki


def tablicy_pdf():
    d = pymupdf.open(PDF)
    rez, tek, stranicy = {}, None, {}
    for n, st in enumerate(d):
        for s in stroki_stranicy(st):
            tekst = ' '.join(s)
            m = re.match(r'Table A\.([12])\.(\d+) Rate = (\d+)/15 \(Ninner = (\d+)\)', tekst)
            if m and n > 30:                   # после оглавления
                tek = (int(m.group(4)), int(m.group(3)))
                rez[tek] = []
                stranicy[tek] = n + 1
                continue
            if tekst.startswith('Table ') or tekst.startswith('Annex') or tekst.startswith('A.'):
                tek = None if not tekst.startswith('Table A.') else tek
                continue
            if tek and all(x.isdigit() for x in s) and len(s) >= 2:
                rez[tek].extend(int(x) for x in s)
    return rez, stranicy


def tablicy_gr():
    t = open(GR).read()
    rez = {}
    for m in re.finditer(r'ldpc_tab_(\d+)_15([NS])\[(\d+)\]\[(\d+)\] = \{(.*?)\n    \};', t, re.S):
        N = 64800 if m.group(2) == 'N' else 16200
        chisla = []
        for s in re.findall(r'\{([^{}]*)\}', m.group(5)):
            v = [int(x) for x in s.split(',') if x.strip()]
            chisla.extend(v[1:1 + v[0]])
        rez[(N, int(m.group(1)))] = chisla
    return rez


if __name__ == '__main__':
    P, stranicy = tablicy_pdf()
    G = tablicy_gr()
    assert len(G) == 24, len(G)
    for k in sorted(G):
        assert k in P, ('нет в PDF', k)
        assert P[k] == G[k], ('различие', k, len(P[k]), len(G[k]))
        print(f'N={k[0]} R={k[1]}/15: {len(G[k])} чисел совпало (A/322, стр. PDF {stranicy[k]})')
    print('ВСЁ СОВПАЛО: 24 таблицы ATSC 3.0 = gr-atsc3')
