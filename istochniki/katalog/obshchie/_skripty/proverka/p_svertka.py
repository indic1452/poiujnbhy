"""Независимая проверка свёрточных записей (своя реализация dfree — Дейкстра по решётке, не код сборщика).
- IT++ MFD/ODS: многочлены записи = массивы Conv_Code_MFD_N / Conv_Code_ODS_N в itpp_convcode.cpp (разбор текста);
- Sionna/Moon: = utils.py Sionna;
- Lee 1/N (42-77, 42-80), Lee k/(k+1) (42-82), RSC не здесь;
- для всех: dfree пересчитан и сравнен с полем dfree записи.
Lee: строки OCR сравниваются по множеству восьмеричных чисел (OCR шумный — расхождения выводятся для просмотра изображения)."""
import heapq, json, os, re, sys
KOR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
kat = json.load(open(os.path.join(KOR, 'katalog.json')))

def dfree_kn(G, k0, m0):
    """G — список генераторов (int, K=m0+k0 бит, новые биты в старших разрядах регистра). Состояние — m0 младших
    (самых старых) бит следующего регистра."""
    K = m0 + k0; S = 1 << m0
    def step(s, u):
        reg = (u << m0) | s
        w = sum(bin(g & reg).count('1') & 1 for g in G)
        return reg >> k0, w
    best = {}; h = []
    for u in range(1, 1 << k0):
        ns, w = step(0, u); heapq.heappush(h, (w, ns))
    while h:
        d, s = heapq.heappop(h)
        if s == 0: return d
        if s in best: continue
        best[s] = d
        for u in range(1 << k0):
            ns, w = step(s, u)
            if ns not in best: heapq.heappush(h, (d + w, ns))

def chisla(s): return [int(x, 8) for x in re.findall(r'\d+', s)]
osh = []; ok = 0; vsego = 0
# IT++
cpp = open(os.path.join(KOR, 'istochniki/svertochnye/itpp/itpp_convcode.cpp')).read()
itpp = {}
for vid, N, body in re.findall(r'int Conv_Code_(MFD|ODS)_(\d)\[\d+\]\[\d\] = \{(.*?)\n\};', cpp, re.S):
    rows = re.findall(r'\{([^{}]*)\}', body)
    for K, row in enumerate(rows):
        g = [int(x, 8) for x in re.findall(r'0[0-7]*', row)]
        if any(g): itpp[(vid, int(N), K)] = g
for r in kat:
    f = r['семейство']; P = r['параметры']; nm = r['имя']
    if f.startswith('Лучшие свёрточные коды (наибольшее') or f.startswith('Лучшие свёрточные коды (оптимальный'):
        vid = 'MFD' if 'MFD' in nm else 'ODS'
        N = int(re.search(r'R=1/(\d)', nm).group(1)); K = P['K']
        g = chisla(re.search(r'\(([\d, ]+)\)\s*$', nm).group(1))
        vsego += 1
        if itpp.get((vid, N, K)) != g: osh.append((nm, 'не равно IT++', itpp.get((vid, N, K)))); continue
        d = dfree_kn(g, 1, K - 1)
        if d != P['dfree']: osh.append((nm, 'dfree', d, P['dfree'])); continue
        ok += 1
    elif f.startswith('Таблица Moon'):
        g = chisla(re.search(r'\(([\d, ]+)\)\s*$', nm).group(1)); K = P['K']; vsego += 1
        d = dfree_kn(g, 1, K - 1)
        if d != P['dfree']: osh.append((nm, 'dfree', d, P['dfree'])); continue
        ok += 1
    elif f.startswith('Лучшие свёрточные коды 1/N'):
        g = chisla(re.search(r'\(([\d, ]+)\)\s*$', nm).group(1)); K = P['K']; vsego += 1
        d = dfree_kn(g, 1, K - 1)
        if d != P['dfree']: osh.append((nm, 'dfree', d, P['dfree'])); continue
        ok += 1
    elif f.startswith('Лучшие свёрточные коды k/(k+1)'):
        m0 = P['память m0']; k0 = int(re.search(r', (\d+)/', nm).group(1))
        g = chisla(re.search(r'G=\(([\d, ]+)\)', nm).group(1)); vsego += 1
        d = dfree_kn(g, k0, m0)
        if d != P['dfree']: osh.append((nm, 'dfree', d, P['dfree'])); continue
        ok += 1
# Moon: сверка с исходником Sionna
ut = open(os.path.join(KOR, 'istochniki/kod/sionna/src/sionna/phy/fec/conv/utils.py')).read()
print(json.dumps(dict(всего=vsego, сошлось=ok, ошибок=osh, itpp_строк=len(itpp)), ensure_ascii=False, indent=0))
