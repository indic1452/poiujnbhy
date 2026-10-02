"""Сверка, часть B5: AR4JA CCSDS — ТЕСТОВЫЕ ВЕКТОРЫ второго первоисточника (IRIG 106-24, гл. 2, прил. 2-D, D.4.g):
для 6 кодов (k=1024/4096; r=1/2, 2/3, 4/5) кодовое слово [u | переданная чётность] из IRIG + неизвестные выколотые M символов
должно удовлетворять H·c=0 с H из tablicy/ccsds/ldpc_ar4ja_*.json; выколотые символы находятся решением системы над GF(2),
система совместна ⇔ таблицы верны (переопределённая: 3M уравнений, M неизвестных)."""
import json, re
from __main__ import prov, zap, T, TT, P

IRIG = 'istochniki/dop3/IRIG106-24_chapter2.pdf'


def hexblok(t, start, stop):
    i = t.index(start) + len(start)
    j = t.index(stop, i)
    s = re.sub(r'=====PAGE \d+===== Telemetry Standards, RCC Standard 106-24 Chapter 2, October 2024 D-\d+ DISTRIBUTION A: APPROVED FOR PUBLIC RELEASE', ' ', t[i:j])
    return re.sub(r'[^0-9A-F]', '', s)


def bity(h):
    return [int(c) for x in h for c in format(int(x, 16), '04b')]


def reshit(rows, known, nk, npunct):
    """rows — списки позиций единиц; known — значения первых nk символов; неизвестны позиции nk…nk+npunct−1.
    Возвращает (совместна, решение)."""
    eqs = []
    for r in rows:
        rhs = 0
        mask = 0
        for c in r:
            if c < nk:
                rhs ^= known[c]
            else:
                mask |= 1 << (c - nk)
        eqs.append((mask, rhs))
    piv = {}
    for mask, rhs in eqs:
        while mask:
            b = mask.bit_length() - 1
            if b in piv:
                pm, pr = piv[b]
                mask ^= pm
                rhs ^= pr
            else:
                piv[b] = (mask, rhs)
                break
        else:
            if rhs:
                return False, None
    return True, len(piv)


@prov('CCSDS LDPC AR4JA k=1024')
def _():
    t = TT(IRIG)
    i0 = [m.start() for m in re.finditer(r'D\.4\.g\(1\) Data Block \(k = 1024 bits\)', t)][-1]
    t = t[i0:]
    u1 = hexblok(t, 'Example 1024-bit test pattern values in hexadecimal are as follows.', 'The following are')
    u4 = hexblok(t, 'Example 4096-bit test pattern values in hexadecimal are as follows:', 'The following are')
    assert len(u1) * 4 == 1024 and len(u4) * 4 == 4096, (len(u1), len(u4))
    assert u1.startswith('000000010002') and u1.endswith('FE6B2840')
    par = {}
    for k, r, M in ((1024, '1/2', 512), (1024, '2/3', 256), (1024, '4/5', 128), (4096, '1/2', 2048), (4096, '2/3', 1024), (4096, '4/5', 512)):
        start = 'Parity (Code Rate = %s, Information Block Size = %d, M = %d):' % (r, k, M)
        sub = t[t.index(start):]
        m = re.search(r'Parity \(Code Rate|D\.4\.g\(2\)|D\.5\. Synchronization', sub[len(start):])
        stop = sub[len(start):][m.start():m.start() + 20]
        par[(k, r)] = hexblok(sub, start, stop)
    rez = []
    for (k, r), ph in par.items():
        a, b = map(int, r.split('/'))
        n = k * b // a
        assert len(ph) * 4 == n - k, (k, r, len(ph) * 4, n - k)
        tab = json.load(open(P('tablicy/ccsds/ldpc_ar4ja_%d_%s.json' % (k, r.replace('/', '_')))))
        M = {(1024, '1/2'): 512, (1024, '2/3'): 256, (1024, '4/5'): 128, (4096, '1/2'): 2048, (4096, '2/3'): 1024, (4096, '4/5'): 512}[(k, r)]
        known = bity(u1 if k == 1024 else u4) + bity(ph)
        assert len(known) == n and int(tab['n']) == n + M
        ok, rang = reshit(tab['строки'], known, n, M)
        assert ok, (k, r, 'система несовместна — таблица или вектор расходятся')
        # контроль чувствительности: одна ошибка в переданной чётности → несовместна
        bad = known[:]
        bad[k + 5] ^= 1
        assert not reshit(tab['строки'], bad, n, M)[0]
        rez.append('k=%d r=%s' % (k, r))
    return ('ТЕСТОВЫЕ ВЕКТОРЫ IRIG 106-24 гл. 2 прил. D.4.g (второй первоисточник, тот же код AR4JA): для %s слово [u | чётность IRIG] дополняется M выколотыми символами — система H·c=0 '
            'с H из tablicy/ccsds/ldpc_ar4ja_*.json совместна для всех 6 кодов (переопределённая, 3M уравнений); при искажении 1 бита — несовместна' % ', '.join(rez))
