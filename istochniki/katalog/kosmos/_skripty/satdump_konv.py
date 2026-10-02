"""Сводка кодирования нисходящих линий спутников из конвейеров SatDump (resources/pipelines/*.json, GPL-3).
Каждый конвейер: демодулятор (модуляция, скорость) → декодер кадров (свёрточный/РС/LDPC/турбо, ASM, рандомизатор, NRZ-M).
Выход: tablicy/satdump/konveiery.json — по одному элементу на конвейер с файлом и строкой, где он описан."""
import glob, json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPE = os.path.join(os.path.dirname(KOR), 'repos', 'SatDump', 'resources', 'pipelines')


def chist(t):
    """Снять комментарии // и /* */ вне строк и висячие запятые (JSON с комментариями)."""
    out, i, n, v_str = [], 0, len(t), False
    while i < n:
        c = t[i]
        if v_str:
            out.append(c)
            if c == '\\':
                out.append(t[i + 1]); i += 2; continue
            if c == '"':
                v_str = False
            i += 1; continue
        if c == '"':
            v_str = True; out.append(c); i += 1; continue
        if t.startswith('//', i):
            j = t.find('\n', i); i = n if j < 0 else j; continue
        if t.startswith('/*', i):
            i = t.index('*/', i) + 2; continue
        out.append(c); i += 1
    return re.sub(r',(\s*[}\]])', r'\1', ''.join(out))


vse = []
for f in sorted(glob.glob(os.path.join(PIPE, '*.json'))):
    syr = open(f).read()
    d = json.loads(chist(syr))
    stroki = syr.split('\n')
    for k, v in d.items():
        nomer = next((i + 1 for i, l in enumerate(stroki) if l.strip().startswith('"%s"' % k)), next(i + 1 for i, l in enumerate(stroki) if ('"%s"' % k) in l))
        w = v.get('work', {})
        zap = {'конвейер': k, 'название': v.get('name'), 'файл': 'resources/pipelines/' + os.path.basename(f), 'строка': nomer,
               'частоты_МГц': [[a, b / 1e6] for a, b in v.get('frequencies', []) if isinstance(b, (int, float))], 'этапы': {}}
        for st, m in w.items():
            if isinstance(m, dict) and 'module' in m:
                zap['этапы'][st] = {'модуль': m['module'], 'параметры': m.get('parameters', {})}
        vse.append(zap)
assert len(vse) > 150
json.dump(vse, open(os.path.join(KOR, 'tablicy', 'satdump', 'konveiery.json'), 'w'), ensure_ascii=False, indent=1)
print(len(vse))
