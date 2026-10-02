"""Каталог CRC RevEng (all.htm, копия web.archive.org 2026-09-06) → tablicy/crc/reveng.json.

Для каждой модели: параметры (Williams/Rocksoft), класс, псевдонимы, ссылки на стандарты (текст пункта
каталога), кодовые слова. Проверки (assert): (1) CRC строки «123456789» = check; (2) кодовые слова
каталога (ширина кратна 8): CRC сообщения = хвост слова; (3) сверка со вторым источником — preset.c
RevEng 3.0.6 (GPL-3) и allcrcs.txt crcany (zlib) — одни и те же параметры.
"""
import html, json, os, re, sys
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = os.path.join(KOR, 'istochniki', 'crc')
t = open(os.path.join(IST, 'reveng_crc_catalogue_all_archive.htm'), encoding='utf-8').read()
stroki = t.split('\n')


def refl(x, w):
    r = 0
    for _ in range(w):
        r = (r << 1) | (x & 1); x >>= 1
    return r


def crc(data, w, poly, init, refin, refout, xorout):
    top = 1 << (w - 1); mask = (1 << w) - 1; reg = init
    for b in data:
        if refin: b = refl(b, 8)
        for i in range(7, -1, -1):
            bit = (b >> i) & 1
            fb = ((reg & top) != 0) ^ bit
            reg = (reg << 1) & mask
            if fb: reg ^= poly
    if refout: reg = refl(reg, w)
    return reg ^ xorout


def residue_ok(bits, w, poly, init, refout, residue):
    """Остаток RevEng: регистр после безошибочного кодового слова (с отражением при RefOut, без XorOut)."""
    top = 1 << (w - 1); mask = (1 << w) - 1; reg = init
    for bit in bits:
        fb = ((reg & top) != 0) ^ bit
        reg = (reg << 1) & mask
        if fb: reg ^= poly
    if refout: reg = refl(reg, w)
    return reg == residue


def txt(s):
    s = re.sub(r'<[^>]+>', '', s); s = html.unescape(s).replace('​', '')
    return re.sub(r'\s+', ' ', s).strip()


bloki = re.split(r'(?=<h3><a name="crc\.cat)', t)
mod = []
for b in bloki[1:]:
    m = re.search(r'<code>(width=.*?)</code>', b)
    if not m: continue
    p = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', m.group(1)))
    name = p['name'].strip('"')
    w = int(p['width']); P = int(p['poly'], 16); I = int(p['init'], 16); X = int(p['xorout'], 16)
    ri = p['refin'] == 'true'; ro = p['refout'] == 'true'; ch = int(p['check'], 16); res = int(p['residue'], 16)
    klass = re.search(r'Class: <span class="(\w+)"', b)
    alias = re.findall(r'<a name="crc\.cat\.[^"]*"><strong>([^<]+)</strong>', b)
    # ссылки: пункты первого уровня списка
    ul = b[b.find('<ul>') + 4:]
    punkty = []
    depth = 0; cur = ''
    for tok in re.split(r'(<li>|</li>|<ul>|</ul>)', ul):
        if tok == '<ul>': depth += 1; cur += tok if depth > 0 else ''
        elif tok == '</ul>':
            if depth == 0: break
            depth -= 1; cur += tok
        elif tok == '<li>':
            if depth == 0: cur = ''
            else: cur += tok
        elif tok == '</li>':
            if depth == 0: punkty.append(cur)
            else: cur += tok
        else: cur += tok
    ssylki = [txt(re.sub(r'<ul>.*', '', x, flags=re.S)) for x in punkty]
    ssylki = [s for s in ssylki if not s.startswith(('Class:', 'Alias:', 'Created:', 'Updated:'))]
    kodslova = [c.replace('&#8203;', '').replace('​', '') for c in re.findall(r'<li><code>([0-9A-Fa-f&#;​]+)</code></li>', b)]
    # строка в файле
    stroka = next(i + 1 for i, l in enumerate(stroki) if ('name="%s"' % name) in l and 'width=' in l)
    assert crc(b'123456789', w, P, I, ri, ro, X) == ch, name
    prov_ks = 0; ne_ks = []
    for k in kodslova:
        vars_ = []
        if set(k) <= set('01') and len(k) >= w + 1 and ('1' in k):
            bits = [int(c) for c in k]
            vars_.append(bits)
            if len(bits) % 8 == 0:
                vars_.append([bits[i + 7 - j] for i in range(0, len(bits), 8) for j in range(8)])
        if all(c in '0123456789abcdefABCDEF' for c in k) and len(k) % 2 == 0:
            d = bytes.fromhex(k)
            vars_.append([(b >> (i if ri else 7 - i)) & 1 for b in d for i in range(8)])
        ok = False
        for bits in vars_:
            if residue_ok(bits, w, P, I, ro, res):
                ok = True; break
        if ok: prov_ks += 1
        else: ne_ks.append(k)
    mod.append(dict(имя=name, width=w, poly='0x%0*X' % ((w + 3) // 4, P), init='0x%0*X' % ((w + 3) // 4, I), refin=ri, refout=ro,
                    xorout='0x%0*X' % ((w + 3) // 4, X), check='0x%0*X' % ((w + 3) // 4, ch), residue='0x%0*X' % ((w + 3) // 4, res),
                    класс=klass.group(1) if klass else '', псевдонимы=alias, ссылки=ssylki[:12], кодовые_слова=kodslova,
                    проверено_кодовых_слов=prov_ks, не_сошлось_кодовых_слов=len(ne_ks), строка_в_all_htm=stroka))
# сверка с preset.c RevEng 3.0.6
pre = open(os.path.join(IST, 'reveng-3.0.6', 'preset.c'), encoding='latin-1').read()
imena_pre = set(re.findall(r'"(CRC-[^"]+)"', pre))
# сверка с crcany allcrcs.txt
cany = {}
for l in open(os.path.join(IST, 'crcany_allcrcs.txt')):
    p = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', l))
    if 'name' in p: cany[p['name'].strip('"')] = p
sv_any = 0
for m in mod:
    c = cany.get(m['имя'])
    if c:
        assert int(c['poly'], 16) == int(m['poly'], 16) and int(c['init'], 16) == int(m['init'], 16) and int(c['xorout'], 16) == int(m['xorout'], 16) \
            and int(c['check'], 16) == int(m['check'], 16), m['имя']
        m['сверено_crcany'] = True; sv_any += 1
    m['есть_в_preset_c_3_0_6'] = m['имя'] in imena_pre
json.dump(mod, open(os.path.join(KOR, 'tablicy', 'crc', 'reveng.json'), 'w'), ensure_ascii=False, indent=1)
print('моделей', len(mod), 'кодовых слов проверено', sum(m['проверено_кодовых_слов'] for m in mod), 'не сошлось', sum(m['не_сошлось_кодовых_слов'] for m in mod), 'сверено crcany', sv_any,
      'в preset.c', sum(m['есть_в_preset_c_3_0_6'] for m in mod), 'псевдонимов', sum(len(m['псевдонимы']) for m in mod))
print('нет в preset.c:', [m['имя'] for m in mod if not m['есть_в_preset_c_3_0_6']])
print('нет в crcany:', [m['имя'] for m in mod if not m.get('сверено_crcany')])
