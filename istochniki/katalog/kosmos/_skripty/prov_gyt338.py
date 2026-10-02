"""GY/T 338-2020 (КНР, «Спецификация канального кодирования и модуляции спутниковой цифровой ТВ-передачи»):
1) табл. 5/6 (параметры FECFRAME, стр. PDF 18) — сверены глазами по изображению страницы с EN 302 307-1 табл. 5a/5b (запись в katalog);
2) прил. D/E (адреса накопителей LDPC, стр. PDF 43–62) — OCR (ocr_gyt338.py) сравнивается с таблицами DVB-S2 проекта
   (src/reportgen/potok/data/ldpc_dvb.json — xdsopl/LDPC, сверенные с AFF3CT): доля совпавших чисел при выравнивании последовательностей.
Выход: tablicy/kitai/_itog_gyt338.json"""
import difflib, json, os, re
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = '/home/user/poiujnbhy/src/reportgen/potok/data/ldpc_dvb.json'
t = open(os.path.join(KOR, 'istochniki/kitai/GY_T_338-2020_prilDE_ocr.txt')).read()
st = {int(a): b for a, b in re.findall(r'=====PAGE (\d+)=====\n(.*?)(?=\n=====PAGE|\Z)', t, re.S)}
dvb = json.load(open(PROJ))
norm = ['dvb-s2-64800-%d' % k for k in (16200, 21600, 25920, 32400, 38880, 43200, 48600, 51840, 54000, 57600, 58320)]
kor = ['dvb-s2-16200-%d' % k for k in (3240, 5400, 6480, 7200, 9720, 10800, 11880, 12600, 13320, 14400)]


def posl(klyuchi):
    return [x for k in klyuchi for r in dvb[k]['строки'] for x in r]


def ocr_chisla(stranicy):
    out = []
    for p in stranicy:
        for l in st.get(p, '').split('\n'):
            ch = re.findall(r'\d+', l)
            # строки таблиц: ≥3 чисел, все < 64800
            if len(ch) >= 3 and all(int(x) < 64800 for x in ch):
                out += [int(x) for x in ch]
    return out


itog = {}
for imya, klyuchi, stranicy in (('прил. D (нормальный FECFRAME 64800, 11 скоростей)', norm, range(43, 55)), ('прил. E (короткий FECFRAME 16200, 10 скоростей)', kor, range(55, 63))):
    a, b = posl(klyuchi), ocr_chisla(stranicy)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    sovp = sum(bl.size for bl in sm.get_matching_blocks())
    itog[imya] = {'чисел в DVB-S2 (проект)': len(a), 'чисел распознано OCR': len(b), 'совпало при выравнивании': sovp, 'доля от DVB-S2': round(sovp / len(a), 4)}
os.makedirs(os.path.join(KOR, 'tablicy', 'kitai'), exist_ok=True)
json.dump(itog, open(os.path.join(KOR, 'tablicy', 'kitai', '_itog_gyt338.json'), 'w'), ensure_ascii=False, indent=1)
print(json.dumps(itog, ensure_ascii=False, indent=1))
