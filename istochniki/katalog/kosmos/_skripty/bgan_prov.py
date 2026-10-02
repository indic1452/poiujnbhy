"""BGAN / Inmarsat Family SL (ETSI TS 102 744-2-1 V1.1.1, прил. C.1/C.2 — официальные вложения ts_1027440201v010101p0.zip):
проверка таблиц перемежителей турбокода (TCI) и таблиц канального перемежения/выкалывания/отображения (CIPM)."""
import glob, json, os, re

KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAZA = os.path.join(KOR, 'istochniki', 'inmarsat', 'bgan_prilozhenie')
VYH = os.path.join(KOR, 'tablicy', 'inmarsat')
os.makedirs(VYH, exist_ok=True)
tci = sorted(glob.glob(os.path.join(BAZA, '*AnnexC1*', '*AnnexC1*', '*_TCI.TXT')))
cipm = sorted(glob.glob(os.path.join(BAZA, '*AnnexC2*', '*AnnexC2*', '*_CIPM.TXT')))
assert len(tci) == 266 and len(cipm) == 266
itog = {'TCI': {}, 'CIPM': {}}
for f in tci:
    t = open(f, encoding='latin1').read()
    N = int(re.search(r'flush bits (?:N=|= )(\d+)', t).group(1))
    pary = [tuple(map(int, x)) for x in re.findall(r'^\s*(\d+)\s+(\d+)\s*$', t, re.M)]
    assert [q for q, _ in pary] == list(range(N)), (f, N, len(pary))
    d = [x for _, x in pary]
    assert sorted(d) == list(range(N)), ('не перестановка', f)
    itog['TCI'][os.path.basename(f)[:-8]] = N
plohie = []
for f in cipm:
    t = open(f, encoding='latin1').read()
    cs = int(re.search(r'Channel Symbols=\s*(\d+)', t).group(1)) if re.search(r'Channel Symbols=\s*(\d+)', t) else None
    cb = re.search(r'Channel Bits=\s*(\d+)', t)
    cb = int(cb.group(1)) if cb else None
    kol = re.search(r'Symbol\t(.*)\n', t)
    m = len(kol.group(1).split()) if kol else None
    stroki = []
    idx = []
    for l in t.split('\n'):
        mm = re.fullmatch(r'\s*(\d+)((?:\s+\d+)+)\s*([.+\-R]*)\s*', l)
        if not mm:
            continue
        v = list(map(int, mm.group(2).split()))
        stroki.append([int(mm.group(1))] + v)
        if mm.group(3).startswith('R'):  # BPSK-символ уникального слова: один бит на I и Q
            v = sorted(set(v))
        idx += v
    ok = (cs is None or len(stroki) == cs) and len(set(idx)) == len(idx) and [r[0] for r in stroki] == list(range(len(stroki)))
    if not ok:
        plohie.append(os.path.basename(f))
    itog['CIPM'][os.path.basename(f)[:-9]] = {'символов': len(stroki), 'бит_на_символ': m, 'бит': len(idx), 'уникальны': len(set(idx)) == len(idx)}
itog['CIPM_не_прошли'] = plohie
json.dump(itog, open(os.path.join(VYH, 'bgan_tablicy_svodka.json'), 'w'), ensure_ascii=False, indent=0)
print('TCI:', len(itog['TCI']), 'перестановок, N от', min(itog['TCI'].values()), 'до', max(itog['TCI'].values()))
print('CIPM:', len(itog['CIPM']), 'таблиц; бит/символ:', sorted({str(v['бит_на_символ']) for v in itog['CIPM'].values()}), 'не прошли:', plohie)
