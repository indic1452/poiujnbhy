"""Сверка, часть B6: новые записи сверки-2 (LunaNet, GMR-1 3G LDPC, GOES DCPRS, BO.1130 B/DH, SDR TPS, IRIG 106)."""
import json, re, subprocess, sys, os
from __main__ import prov, zap, T, TT, P, st, poly_int, GF, pmul, pmod, is_primitive

HERE = os.path.dirname(os.path.abspath(__file__))


def zapusk(skript):
    r = subprocess.run([sys.executable, os.path.join(HERE, skript)], capture_output=True, text=True)
    assert r.returncode == 0, (skript, r.stderr[-500:])
    return r.stdout


@prov('LunaNet AFS')
def _():
    zapusk('lunanet_ldpc.py')
    it = json.load(open(P('tablicy/lunanet/_itog_lunanet.json')))
    assert 'совпала' in it['sf2']['= 5G NR'] and 'совпала' in it['sf3']['= 5G NR']
    t = TT('istochniki/dop2/LSIS_AFS_volA_2025.pdf')
    assert re.search(r'generator polynomial of 763 \(octal\)', t)
    # BCH(51,8) 763₈: 8 бит — начальное состояние ЛРС, 51 сдвиг; dmin перебором 255 слов (+ MSB поверх → 52 символа)
    g = int('763', 8)
    taps = [i for i in range(8) if g >> i & 1]
    dmin = 99
    for s0 in range(1, 256):
        a = [(s0 >> i) & 1 for i in range(8)]
        while len(a) < 51:
            v = 0
            for tp in taps:
                v ^= a[len(a) - 8 + tp]
            a.append(v)
        dmin = min(dmin, sum(a))
    assert 68 + 52 + 5880 == 6000 == 12 * 500
    assert re.search(r'60 rows and 98 columns', t) and 60 * 98 == 5880
    return 'lunanet_ldpc.py перезапущен: H(SB2/SB3) = 5G NR BG2 (Z=120/88); 763₈ из текста, BCH(51,8): dmin=%d по 255 словам (своим ЛРС); 68+52+5880 = 6000 символов = 12 с × 500; перемежитель 60×98 = 5880' % dmin


@prov('GMR-1 3G (Thuraya, SkyTerra')
def _():
    out = zapusk('gmr1_3g_ldpc.py')
    d = json.load(open(P('tablicy/gmr1/gmr1_3g_ldpc.json')))
    assert len(d['коды']) == 18
    return 'gmr1_3g_ldpc.py перезапущен: 18 кодов, все assert (k/M строк, q=(n−k)/M, адреса < n−k, нет кратных рёбер, eff R = табл. 4.9/4.11) выполнены'


@prov('GOES DCS (DCPRS')
def _():
    zapusk('prov_dcprs.py')
    d = json.load(open(P('tablicy/goes_dcs/dcprs_cs2.json')))
    assert d['кодер']['запись_восьм'] == {'E-1': '133', 'E-0': '171'}
    t = TT('istochniki/dop2/DCS_Certification_Standard_V2.pdf')
    assert re.search(r'300 bps certification the output symbol rate shall be 150 symbols per second', t) and re.search(r'1200 bps certification the output symbol rate shall be 600 symbols per second', t)
    return 'prov_dcprs.py перезапущен: отводы из векторного рис. 1 → E-1=Q1⊕Q3⊕Q4⊕Q6⊕Q7 (133₈), E-0=Q1⊕Q2⊕Q3⊕Q4⊕Q7 (171₈); таблица 40 байт, FSS, фазы; 150/600 симв/с — из текста п. 4.3'


@prov('МСЭ-R BO.1130 Digital System B')
def _():
    t = TT('istochniki/itu/R-REC-BO.1130-5-202602-I.pdf')
    assert re.search(r'rate 1/2, k = 7 convolutional encoding, preceded by RS \(140,160\) encoding', t)
    assert re.search(r'interleaver frame time on the order of 200 ms', t) and re.search(r'n can range from 2 to 4', t)
    return 'BO.1130-5 п. 3.1.4–3.1.7: «rate 1/2, k=7 … preceded by RS (140,160)», кадр перемежителя ≈200 мс, ПСП-слово, обучающие символы n=2…4 — совпало'


@prov('МСЭ-R BO.1130 Digital System DH')
def _():
    t = TT('istochniki/itu/R-REC-BO.1130-5-202602-I.pdf')
    assert re.search(r'RS \(223,255\) block coder to yield an output of n\s*\S?\s*8 160 bits', t) and re.search(r'n\s*\S?\s*7 136', t)
    assert 7136 * 255 // 223 == 8160
    assert re.search(r'R 1/4 convolution coder whose output is split into two R 1/2', t) and re.search(r'interleaved over a 432 ms frame', t)
    assert re.search(r'approximately 4\.32 s', t)
    return 'BO.1130-5 прил. 4: n×7136 → РС(255,223) → n×8160 (7136·255/223=8160), R 1/4 → два канала R 1/2, перемежение 432 мс, задержка ≈4,32 с — совпало'


@prov('SDR многочастотный')
def _():
    t = TT('istochniki/sdr/ts_10255102v020101p.pdf')
    m = re.search(r'h\(x\) = (x14 \+ x9 \+ x8 \+ x6 \+ x5 \+ x4 \+ x2 \+ x \+ 1)', t)
    h = poly_int(st(m.group(1)))
    ok = None
    for p in range(129, 256, 2):
        if is_primitive(p):
            F = GF(7, p)
            if pmul(F.minpoly(1), F.minpoly(3)) == h:
                ok = p
                break
    assert ok and pmod((1 << 127) | 1, h) == 0
    assert 127 - 60 == 67 and 113 - 60 == 53
    return 'TS 102 551-2 стр. 23: h(x)=x^14+x^9+x^8+x^6+x^5+x^4+x^2+x+1 = m1·m3 над GF(2^7) с p(x)=0x%X (своим перебором) → BCH(127,113) t=2, укорочение на 60 → (67,53)' % ok


@prov('IRIG 106-24 гл. 2')
def _():
    t = TT('istochniki/dop3/IRIG106-24_chapter2.pdf')
    assert re.search(r'A FCB88938D8D76A4F Ā 034776C7272895B0', t)
    assert int('FCB88938D8D76A4F', 16) ^ int('034776C7272895B0', 16) == (1 << 64) - 1  # Ā — инверсия A
    assert re.search(r'For k=1024 the ASM length will be 64 bits\. For k=4096 the ASM will be 256 bits', t)
    return 'IRIG 106-24 прил. D: A=FCB88938D8D76A4F и Ā=034776C7272895B0 (поразрядная инверсия, = ASM CCSDS для LDPC 1/2 и вторая половина ASM турбо 1/4), ASM 64/256 бит — совпало; тестовые векторы — sverka_b5'
