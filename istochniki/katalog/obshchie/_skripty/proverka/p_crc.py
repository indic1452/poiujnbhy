"""Независимая проверка записей CRC каталога RevEng: строки <code>width=… name="…"</code> из архивной копии all.htm
сопоставляются с записями (все 8 параметров + номер строки), check («123456789») и residue пересчитываются своим
побитовым CRC (прямая реализация по модели Rocksoft/Williams, без кода сборщика)."""
import json, os, re
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))
L = open(os.path.join(KOR, 'istochniki/crc/reveng_crc_catalogue_all_archive.htm'), encoding='utf-8', errors='replace').read().split('\n')
kat_crc = {}
for i, l in enumerate(L, 1):
    m = re.search(r'<code>(width=.*?name=&quot;(.*?)&quot;|width=.*?name="(.*?)")</code>', l)
    if not m: continue
    s = m.group(1).replace('&quot;', '"')
    d = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', s))
    d['name'] = d['name'].strip('"'); d['_line'] = i
    kat_crc.setdefault(d['name'], d)

def refl(x, w): return int(format(x, '0%db' % w)[::-1], 2)
def crc(data, w, poly, init, refin, refout, xorout):
    reg = init; top = 1 << (w - 1); mask = (1 << w) - 1
    for b in data:
        if refin: b = refl(b, 8)
        for i in range(7, -1, -1):
            bit = (b >> i) & 1
            fb = ((reg & top) >> (w - 1)) ^ bit
            reg = (reg << 1) & mask
            if fb: reg ^= poly
    if refout: reg = refl(reg, w)
    return reg ^ xorout

osh = []; ok = 0
recs = [r for r in kat if r['семейство'] == 'CRC каталог RevEng']
for r in recs:
    P = r['параметры']; d = kat_crc.get(r['имя'])
    if not d: osh.append((r['имя'], 'нет в all.htm')); continue
    for f in ('width', 'poly', 'init', 'refin', 'refout', 'xorout', 'check', 'residue'):
        if str(P[f]).lower() != d[f].lower(): osh.append((r['имя'], f, P[f], d[f]))
    ln = int(re.search(r'\d+', [s for s in r['источник'] if s['файл'].endswith('all_archive.htm')][0]['страница|строки']).group())
    if ln != d['_line']: osh.append((r['имя'], 'строка', ln, d['_line']))
    w = int(d['width']); poly = int(d['poly'], 16); init = int(d['init'], 16); xo = int(d['xorout'], 16)
    ri = d['refin'] == 'true'; ro = d['refout'] == 'true'
    c = crc(b'123456789', w, poly, init, ri, ro, xo)
    if c != int(d['check'], 16): osh.append((r['имя'], 'check', hex(c), d['check']))
    # residue: прогнать сообщение + его CRC (в порядке передачи) через регистр без xorout на выходе
    msg = b'123456789'
    if w % 8 == 0:
        nb = w // 8
        cw = c.to_bytes(nb, 'little' if ro else 'big')
        reg = crc(msg + cw, w, poly, init, ri, False, 0)
        if ri: reg = refl(reg, w)  # RevEng residue — в нормальной (неотражённой) форме регистра
        # регистр после кодового слова зависит только от xorout: = CRC_{init=0}(xorout-хвост)
        if reg != int(d['residue'], 16): osh.append((r['имя'], 'residue', hex(reg), d['residue']))
    ok += 1
lishnie = sorted(set(kat_crc) - {r['имя'] for r in recs})
print(json.dumps(dict(записей=len(recs), в_htm=len(kat_crc), проверено=ok, ошибок=osh, нет_в_каталоге=lishnie), ensure_ascii=False, indent=0))
