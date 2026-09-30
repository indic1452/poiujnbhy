"""DVB-S2X целиком: VL-SNR (укорочение и выкалывание, табл. 19a) и перемежение бит S2X (табл. 9a, 9b).

Источник — ETSI EN 302 307-2 V1.4.1 (2024-08):
- 5.5.2.6, табл. 19a (стр. 51 PDF): для девяти MODCOD VL-SNR — Xs (первые Xs бит данных — нули, не
  передаются), P и Xp (не передаётся каждый P-й бит чётности, начиная с p0, пока не наберётся Xp);
  примечание к 5.3 (стр. 16): код «1/5» короткого кадра — это код 1/4 короткого кадра EN 302 307-1;
- 5.3.3, табл. 9a (нормальный и средний кадр) и 9b (короткий) (стр. 25–26): порядок чтения столбцов
  перемежителя для каждого MODCOD («102» — сперва средний столбец, затем левый, затем правый);
- прил. B, C: «LDPC code identifier» каждой таблицы адресов (B.1 … B.24, C.1 … C.10) — какой код взять;
  коды, которых в S2X нет (3/5, 2/3, 4/5, 5/6 и т. д.), — коды EN 302 307-1 (табл. 5a, 5b: k).

Второй источник — drmpeg/gr-dvbs2 (GPL-3.0): lib/ldpc_bb_impl.cc (get_nbch: Xs, P, Xp VL-SNR,
include/dvbs2/dvbs2_config.h: NORMAL_PUNCTURING и т. д.) и lib/interleaver_bb_impl.cc (get_rows:
rowaddr — порядок столбцов). Код get_rows переводится в Python и исполняется для каждого MODCOD
таблиц 9a/9b — сверка число в число. В gr-dvbs2 нет 64APSK, 128APSK и 256APSK — у них источник один
(текст стандарта). 128APSK (135/180, 140/180) не встраивается: после кодера 6 нулей, после
перемежителя 84 единицы (5.3.2, 5.3.3) — рамка длиннее слова, а в схеме передачи известны только нули.

Замечание о gr-dvbs2: выкалывание VL-SNR там делается до накопителя чётности (p[j] ^= p[j-1] после
удаления), а по тексту 5.5.2.6 выкалываются сами биты чётности p0, pP, … — места совпадают, значения
у gr-dvbs2 иные; принят текст стандарта (места выколотых — те же).

Пишет src/reportgen/potok/data/ldpc_dvbs2x.json. Запуск из корня репозитория.
"""
import json
import os
import re
from fractions import Fraction

ZDES = os.path.dirname(os.path.abspath(__file__))
K0 = os.path.normpath(os.path.join(ZDES, '..'))
KOREN = os.path.normpath(os.path.join(K0, '..', '..'))

S2X = open(os.path.join(K0, 'standarty', 'ETSI_EN_302_307-2_v1.4.1_DVB-S2X.pdf.txt'), encoding='utf-8',
           errors='replace').read()
S2 = open(os.path.join(K0, 'standarty', 'ETSI_EN_302_307-1_v1.4.1_DVB-S2.pdf.txt'), encoding='utf-8',
          errors='replace').read()
GR = os.path.join(K0, 'otkrytyj_kod', 'gr-dvbs2')
DVB = json.load(open(os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_dvb.json'), encoding='utf-8'))

# -- какой код за «LDPC code identifier» -------------------------------------------------------------
KODY = {}          # (кадр, идентификатор) → имя встроенного кода
for bukva, kadr, n in (('B', 'normal', 64800), ('C', 'short', 16200), ('C', 'medium', 32400)):
    for nomer, ident, nn in re.findall(r'Table %s\.(\d+): LDPC code identifier: (\d+/\d+) \(nldpc ?= (\d+ \d+)\)'
                                       % bukva, S2X):
        if int(nn.replace(' ', '')) != n:
            continue
        imena = [i for i, k in DVB.items() if i.startswith('dvb-s2x-%d-' % n)
                 and re.search(r'таблица %s%s \(' % (bukva, nomer), k['откуда'])]
        assert len(imena) == 1, (bukva, nomer, imena)
        KODY[(kadr, ident)] = imena[0]
assert len(KODY) == 34, len(KODY)
# Коды EN 302 307-1: нормальный — k = r·64800 (табл. 5a), короткий — k из табл. 5b.
t5b = S2[S2.index('Table 5b: Coding parameters (for short FECFRAME'):]
t5b = t5b[:t5b.index('9/10')]
for ident, kbch, nbch in re.findall(r'\n(\d/\d{1,2}) *\n(\d+ \d+) \n(\d+ \d+) \n12 \n', t5b):
    k = int(nbch.replace(' ', ''))
    KODY.setdefault(('short', ident), 'dvb-s2-16200-%d' % k)
for ident in ('1/4', '1/3', '2/5', '1/2', '3/5', '2/3', '3/4', '4/5', '5/6', '8/9', '9/10'):
    KODY.setdefault(('normal', ident), 'dvb-s2-64800-%d' % (Fraction(ident) * 64800))
assert KODY[('short', '1/4')] == 'dvb-s2-16200-3240' and KODY[('short', '1/3')] == 'dvb-s2-16200-5400', KODY
for imya in KODY.values():
    assert imya in DVB, imya
print('Идентификаторы кодов: %d (S2X — прил. B, C; S2 — табл. 5a, 5b EN 302 307-1)' % len(KODY))

# -- VL-SNR: табл. 19a ------------------------------------------------------------------------------
kus = S2X[S2X.index('Table 19a: Shortening/Puncturing of VL-SNR FECFRAME'):]
kus = kus[:kus.index('Table 19b')]
stroki = re.findall(r'^(?:\S{1,3}/2 )?(BPSK|QPSK) (\d+/\d+) (normal|medium|short)( SF2)? *\n(\d+(?: \d+)?) *\n(\d+) *\n'
                    r'(\d+(?: \d+)?) *$', kus, re.M)
assert len(stroki) == 9, stroki
VLSNR = []
for mod, ident, kadr, sf2, xs, p, xp in stroki:
    xs, p, xp = (int(x.replace(' ', '')) for x in (xs, p, xp))
    # 5.3, примечание: «1/5» короткого кадра — код 1/4 короткого кадра EN 302 307-1.
    kod = KODY[(kadr, '1/4' if (kadr, ident) == ('short', '1/5') else ident)]
    n, k = DVB[kod]['n'], DVB[kod]['k']
    assert xp * p <= n - k, (ident, kadr)
    imya = 'dvb-s2x-vl-%d-%s%s' % (n, ident.replace('/', '-'), '-sf2' if sf2 else '')
    VLSNR.append({'имя': imya, 'модкод': '%s%s %s %s%s' % ('π/2 ' if mod == 'BPSK' else '', mod, ident, kadr, sf2),
                  'код': kod, 'Xs': xs, 'P': p, 'Xp': xp, 'длина': n - xs - xp})
print('VL-SNR: 9 MODCOD из табл. 19a:', ', '.join('%s (%d бит)' % (v['имя'], v['длина']) for v in VLSNR))

# Второй источник: gr-dvbs2 get_nbch.
cfg = open(os.path.join(GR, 'include', 'dvbs2', 'dvbs2_config.h')).read()
konst = {k: int(v) for k, v in re.findall(r'#define (\w+PUNCTURING\w*) (\d+)', cfg)}
ldpc_cc = open(os.path.join(GR, 'lib', 'ldpc_bb_impl.cc')).read()
gr_vl = {}
for enum, telo in re.findall(r'case (C\w+(?:_VLSNR\w*|_MEDIUM)):(.*?)break;', ldpc_cc, re.S):
    zn = {k: (int(v) if v.isdigit() else konst[v]) for k, v in re.findall(r'\*(Xs|P|Xp) = (\w+);', telo)}
    # У среднего кадра Xp по умолчанию — MEDIUM_PUNCTURING (*frame_size = FRAME_SIZE_MEDIUM - MEDIUM_PUNCTURING).
    gr_vl[enum] = (zn.get('Xs', 0), zn['P'], zn.get('Xp', konst['MEDIUM_PUNCTURING']))
SOOTV = {'dvb-s2x-vl-64800-2-9': 'C2_9_VLSNR', 'dvb-s2x-vl-32400-1-5': 'C1_5_MEDIUM',
         'dvb-s2x-vl-32400-11-45': 'C11_45_MEDIUM', 'dvb-s2x-vl-32400-1-3': 'C1_3_MEDIUM',
         'dvb-s2x-vl-16200-1-5-sf2': 'C1_5_VLSNR_SF2', 'dvb-s2x-vl-16200-11-45-sf2': 'C11_45_VLSNR_SF2',
         'dvb-s2x-vl-16200-1-5': 'C1_5_VLSNR', 'dvb-s2x-vl-16200-4-15': 'C4_15_VLSNR', 'dvb-s2x-vl-16200-1-3': 'C1_3_VLSNR'}
assert sorted(SOOTV) == sorted(v['имя'] for v in VLSNR), [v['имя'] for v in VLSNR]
for v in VLSNR:
    assert gr_vl[SOOTV[v['имя']]] == (v['Xs'], v['P'], v['Xp']), (v, gr_vl[SOOTV[v['имя']]])
print('VL-SNR: Xs, P, Xp всех 9 совпали с gr-dvbs2 (get_nbch)')

# -- перемежение: табл. 9a, 9b -----------------------------------------------------------------------
PEREM = []
for zagolovok, konec, kadr in (('Table 9a: Bit Interleaver Patterns', 'Table 9b', 'normal'),
                               ('Table 9b: Bit Interleaver Patterns', 'For 128APSK padding is introduced to have an integer '
                                'number of constellation points and slots in a FECFRAME. \n84', 'short')):
    kus = S2X[S2X.index(zagolovok):]
    kus = kus[:kus.index(konec)]
    for vid, ident, stolbcy in re.findall(r'^(8PSK|[\d+]+(?:rb)?APSK)(?: APSK)?,? (\d+/\d+) *\n(\d{3,8}) *$', kus, re.M):
        assert sorted(stolbcy) == [str(j) for j in range(len(stolbcy))], (vid, ident, stolbcy)
        PEREM.append({'вид': vid, 'модкод': '%s %s' % (vid, ident), 'кадр': kadr, 'идентификатор': ident,
                      'столбцы': stolbcy})
assert len(PEREM) == 35 + 11, len(PEREM)          # 9a: 20 + 15 (две страницы), 9b: 11
# Число столбцов = бит на символ: 8PSK — 3, a+b+…APSK — log2(a + b + …).
for p in PEREM:
    m = 8 if p['вид'] == '8PSK' else sum(int(x) for x in re.findall(r'\d+', p['вид'].replace('rb', '')))
    assert 1 << (len(p['столбцы']) - 1) == m // 2 and m & (m - 1) == 0, p
NE = [p for p in PEREM if p['вид'] == '128APSK']
assert [p['модкод'] for p in NE] == ['128APSK 135/180', '128APSK 140/180'], NE
PEREM = [p for p in PEREM if p['вид'] != '128APSK']
for p in PEREM:
    p['код'] = KODY[(p['кадр'], p['идентификатор'])]
print('Перемежение S2X: %d MODCOD из табл. 9a/9b (128APSK — 2 — не встраивается: добивка 6 нулей и 84 единиц)'
      % len(PEREM))

# Второй источник: get_rows gr-dvbs2 → Python.
cc = open(os.path.join(GR, 'lib', 'interleaver_bb_impl.cc')).read()
telo = cc[cc.index('switch (constellation) {'):cc.index('*mod_order = mod;')]
py, uroven = ['def get_rows(constellation, rate, frame_size):', '    r = {}'], 1
for s in telo.split('\n'):
    s = re.sub(r'/\*.*?\*/', '', s).strip()
    if not s or s.startswith('switch') or s in ('break;',) or s.startswith('mod =') or s.startswith('rows ='):
        continue
    m = re.match(r'case (\w+):', s)
    if m:
        uroven = 1
        py.append('    %sif constellation == "%s":' % ('el' if py[-1] != '    r = {}' else '', m.group(1)))
        uroven = 2
        py.append('        pass')
        continue
    if s == 'default:':
        py.append('    else:')
        py.append('        pass')
        uroven = 2
        continue
    m = re.match(r'(\} )?(else )?if \((.*)\) \{$', s)
    if m:
        uslovie = m.group(3).replace('||', ' or ').replace('*frame_size', 'frame_size')
        uslovie = re.sub(r'\b(C\w+|FRAME_SIZE_\w+)\b', r'"\1"', uslovie)
        if m.group(1):
            uroven -= 1
        py.append('    ' * uroven + ('elif ' if m.group(2) else 'if ') + uslovie + ':')
        uroven += 1
        py.append('    ' * uroven + 'pass')
        continue
    if s in ('} else {', 'else {'):
        if s.startswith('}'):
            uroven -= 1
        py.append('    ' * uroven + 'else:')
        uroven += 1
        py.append('    ' * uroven + 'pass')
        continue
    if s == '}':
        uroven -= 1
        continue
    m = re.match(r'rowaddr(\d) = (?:rows \* (\d)|(rows)|(0));$', s)
    assert m, ('не понял строку get_rows', s)
    mnozh = int(m.group(2)) if m.group(2) else (1 if m.group(3) else 0)
    py.append('    ' * uroven + 'r[%s] = %d' % (m.group(1), mnozh))
py.append('    return "".join(str(r[j]) for j in sorted(r))')
prostranstvo = {}
exec('\n'.join(py), prostranstvo)  # noqa: S102 — свой перевод get_rows gr-dvbs2, сверка
get_rows = prostranstvo['get_rows']
GR_MOD = {'8PSK': 'MOD_8PSK', '2+4+2APSK': 'MOD_8APSK', '4+12APSK': 'MOD_16APSK', '8+8APSK': 'MOD_8_8APSK',
          '4+12+16rbAPSK': 'MOD_4_12_16APSK', '4+8+4+16APSK': 'MOD_4_8_4_16APSK'}
sovpalo = 0
for p in PEREM:
    if p['вид'] not in GR_MOD:
        p['второй'] = ''
        continue
    rate = 'C' + p['идентификатор'].replace('/', '_')
    gr = get_rows(GR_MOD[p['вид']], rate, 'FRAME_SIZE_NORMAL' if p['кадр'] == 'normal' else 'FRAME_SIZE_SHORT')
    assert gr == p['столбцы'], (p, gr)
    p['второй'] = 'gr-dvbs2'
    sovpalo += 1
odin = sorted({p['вид'] for p in PEREM if not p['второй']})
print('Перемежение S2X: %d из %d совпали с gr-dvbs2 (get_rows); источник один (нет в gr-dvbs2): %s'
      % (sovpalo, len(PEREM), ', '.join(odin)))
assert sovpalo == 33 and odin == ['16+16+16+16APSK', '256APSK', '4+12+20+28APSK', '8+16+20+20APSK'], (sovpalo, odin)
# Проверка перевода: S2 (EN 302 307-1, 5.3.3) — 8PSK 3/5 «210», прочие «012».
assert get_rows('MOD_8PSK', 'C3_5', 'FRAME_SIZE_NORMAL') == '210'
assert get_rows('MOD_8PSK', 'C2_3', 'FRAME_SIZE_NORMAL') == '012'

itog = {'откуда': 'ETSI EN 302 307-2 V1.4.1: VL-SNR — 5.5.2.6, табл. 19a (стр. 51); перемежение — 5.3.3, '
                  'табл. 9a, 9b (стр. 25–26); сверено с gr-dvbs2 (get_nbch, get_rows)',
        'vlsnr': VLSNR,
        'перемежения': [{k: p[k] for k in ('код', 'вид', 'столбцы', 'модкод', 'кадр', 'второй')} for p in PEREM],
        'не_встроено': [p['модкод'] for p in NE]}
put = os.path.join(KOREN, 'src', 'reportgen', 'potok', 'data', 'ldpc_dvbs2x.json')
with open(put, 'w', encoding='utf-8') as f:
    json.dump(itog, f, ensure_ascii=False, indent=0)
print('Записано:', os.path.relpath(put, KOREN))
