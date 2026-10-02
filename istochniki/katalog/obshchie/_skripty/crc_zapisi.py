"""Записи katalog.json: все модели каталога CRC RevEng (tablicy/crc/reveng.json, собран crc_reveng.py с assert) и
сводная запись зоопарка многочленов Koopman (tablicy/crc/koopman_zoo.json)."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka
mod = json.load(open(os.path.join(KOR, 'tablicy', 'crc', 'reveng.json')))
F_ALL = 'istochniki/crc/reveng_crc_catalogue_all_archive.htm'
F_PRE = 'istochniki/crc/reveng-3.0.6/preset.c'
F_ANY = 'istochniki/crc/crcany_allcrcs.txt'
proekt = open('/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py').read()
imena_pr = set(re.findall(r'\("(CRC-[^"]+)"', proekt))
zap = []
for m in mod:
    nm = m['имя']
    ist = [I(F_ALL, 'строка %d' % m['строка_в_all_htm'], 'https://reveng.sourceforge.io/crc-catalogue/all.htm (копия web.archive.org 2026-09-06)'),
           I(F_PRE, 'строка %d' % stroka(F_PRE, '{"%s"' % nm), 'https://downloads.sourceforge.net/project/reveng/3.0.6/reveng-3.0.6.tar.gz'),
           I(F_ANY, 'строка %d' % stroka(F_ANY, 'name="%s"' % nm))]
    if nm == 'CRC-32/ISCSI':  # первоисточник выбора многочлена (Castagnoli): RFC 3385 — многочлен 11EDC6F41 (= poly 0x1EDC6F41)
        R3385 = 'istochniki/dop/rfc/rfc3385.txt'
        assert '11EDC6F41' in open(os.path.join(KOR, R3385)).read() and m['poly'] == '0x1EDC6F41'
        ist.append(I(R3385, 'строка %d (CRC32C = 11EDC6F41), разд. 4.1' % stroka(R3385, 'memo) is 11EDC6F41')))
    par = {k: m[k] for k in ('width', 'poly', 'init', 'refin', 'refout', 'xorout', 'check', 'residue')}
    par['запись'] = 'Rocksoft/Williams: poly — без старшего члена x^width, нормальная (не отражённая) форма; check — CRC ASCII-строки «123456789»'
    par['класс RevEng'] = m['класс']
    if m['псевдонимы']: par['псевдонимы'] = m['псевдонимы']
    if m['кодовые_слова']: par['кодовые слова каталога'] = m['кодовые_слова'][:6]
    gde = '; '.join(m['ссылки'][:6]) or 'см. каталог'
    pr = 'check пересчитан (crc_reveng.py, assert); кодовых слов каталога проверено %d (остаток = residue), не сошлось %d; параметры = allcrcs.txt crcany (zlib) и preset.c RevEng 3.0.6 (GPL-3)' % (
        m['проверено_кодовых_слов'], m['не_сошлось_кодовых_слов'])
    sl = 'ЕСТЬ в проекте: crc_katalog.REVENG (имя по найденной вслепую CRC), crc.py — поиск многочлена/init/xorout/отражений вслепую' if nm in imena_pr else \
        'ЧАСТИЧНО: crc.py найдёт параметры вслепую, но в crc_katalog.REVENG этой модели нет — добавить строку'
    zap.append(Z(nm, 'CRC каталог RevEng', 'CRC-подобный', par, gde, ist, pr, sl))
# зоопарк Koopman
zoo = json.load(open(os.path.join(KOR, 'tablicy', 'crc', 'koopman_zoo.json')))
shir = sorted(set(z['ширина'] for z in zoo))
zap.append(Z('CRC Polynomial Zoo (Koopman): %d многочленов, ширины %d…%d' % (len(zoo), shir[0], shir[-1]), 'Лучшие многочлены CRC (Koopman, CMU)', 'CRC-подобный',
             {'таблица': 'tablicy/crc/koopman_zoo.json', 'запись': '(неявная +1; явная) <=> (зеркальная) {профиль HD: длина данных при HD=3,4,5…} | имена применений',
              'ширины': shir},
             'выбор многочлена по длине данных и требуемому расстоянию Хэмминга; опознание CRC по известным многочленам (имена применений в зоопарке)',
             [I('istochniki/crc/koopman_crc/index.html', 'весь файл (оглавление)', 'https://users.ece.cmu.edu/~koopman/crc/index.html'),
              I('istochniki/crc/koopman_crc/crc32.html', 'весь файл (пример страницы ширины 32)', 'https://users.ece.cmu.edu/~koopman/crc/crc32.html')],
             'crc_koopman.py (assert): явная = (неявная<<1)|1; зеркальная пара = reverse; для ширин ≤ 24 длина при HD=3 = порядок x mod g − ширина (%d многочленов пересчитано)' % sum(1 for z in zoo if z['профиль_HD_от_3'] and z['ширина'] <= 24),
             'ЧАСТИЧНО: crc.py ищет многочлен вслепую; таблицы зоопарка в проекте нет (полезна для ранжирования кандидатов)'))
sohranit('crc', zap)
