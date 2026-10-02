"""«CASC» — значения аббревиатуры в области кодирования/спутниковой связи, с источниками → zapisi/casc.json."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka, gde
C = 'istochniki/casc/'
zap = [
 Z('CASC = China Aerospace Science and Technology Corporation (中国航天科技集团有限公司) — коды её стандартов', 'CASC (значения аббревиатуры)', 'справочно: организация и её стандарты кодирования',
   {'кто': 'госкорпорация КНР, головной разработчик ракет, спутников, КА; основана 1 июля 1999 г. (из 5-й академии МО 1956 г.); 8 НИИ-комплексов (в т. ч. CAST, CALT, SAST), 11 специализированных компаний (в т. ч. China Satellite Communications — «Чжунсин»), прямые подразделения (в т. ч. China Astronautics Standards Institute)',
    'стандарт кодирования с её участием': 'GB/T 39348-2020 «空间数据与信息传输系统 遥测同步与信道编码» (Space data and information transfer systems — TM synchronization and channel coding), действует с 2021-06-01; разработчики — 中国航天标准化研究所 (China Astronautics Standards Institute, подразделение CASC), 北京空间飞行器总体设计部, 北京遥测技术研究所, 西安空间无线电技术研究所, 航天东方红卫星有限公司 (CAST — CASC); по названию — китайская редакция CCSDS 131.0-B (коды: свёрточный 171/133, РС(255,223) с перемежением, турбо, LDPC C2/AR4JA — записи kosmos)',
    'в системе стандартов CNSA': 'CNSA-EE000017 «Telemetry tracking command and data handling for spacecraft — Part 3: TM channel coding»; отраслевые стандарты QJ (航天行业标准) — перечень в CNSA China Space Standard System v1.0',
    'вещание': 'ABS-S (спутниковое ТВ КНР на «Чжунсин-9»; оператор — China Satellite Communications, компания CASC): LDPC n=15360, Z=32 — в проекте (ldpc_kitay)',
    'текст стандарта': 'GB/T 39348-2020 открыт только для просмотра (openstd.samr.gov.cn), файлом не получен'},
   'спутники и КА КНР (CAST, ChinaSat), ТВ-вещание ABS-S',
   [I(C + 'spacechina_company_profile.html', 'раздел Company Profile (текст «CASC … was formally founded on July 1, 1999 … 8 large R&D and production complexes, 11 specialized companies»)'),
    I(C + 'wikipedia_CASC.html', 'разделы Subordinate entities (CAST, China Satellite Communications, China Astronautics Standards Institute)'),
    I(C + 'samr_search_GBT39348.json', 'весь файл (GB/T 39348-2020, 现行, дата введения 2021-06-01)'),
    I(C + 'samr_search_GBT39348_detail.html', 'блок «主要起草单位» (разработчики) и английское название'),
    I(C + 'cnsa_china_space_standard_system_2015.pdf', gde(C + 'cnsa_china_space_standard_system_2015.pdf.txt', 'Telemetry tracking command and data handling for spacecraft-Part 3:TM channel coding')),
    I(C + 'ycyk_20231031002.pdf', gde(C + 'ycyk_20231031002.pdf.txt', '在我国航天科技的发展规划中') + ' — «в планах развития космической отрасли КНР применение стандартов CCSDS стало неизбежной тенденцией»; ' + gde(C + 'ycyk_20231031002.pdf.txt', 'R-S 码、卷积码和R-S 码构成的级联码') + ' — свёрточный, РС, каскад РС+свёрточный, турбо, LDPC по CCSDS (журнал «遥测遥控», 2024, 45(1))')],
   'название и разработчики GB/T 39348-2020 — из карточки государственного реестра стандартов КНР (std.samr.gov.cn); принадлежность разработчиков к CASC — профиль CASC и раздел Subordinate entities; совпадение с CCSDS 131.0 — по названию (текст недоступен)',
   'ЕСТЬ в проекте: коды CCSDS 131.0 (ccsds.py, ldpc_std — C2/AR4JA, turbo_std — CCSDS), ABS-S (ldpc_kitay); вывод о «CASC LDPC» — istochniki/README.md проекта, docs/20-potok.md'),
 Z('CASIC ≠ CASC: China Aerospace Science and Industry Corporation (中国航天科工集团)', 'CASC (значения аббревиатуры)', 'справочно: смешиваемая аббревиатура',
   {'кто': 'другая госкорпорация КНР (ракеты, радары, спутниковая связь); Wikipedia прямо предупреждает: «Not to be confused with China Aerospace Science and Technology Corporation»'},
   'при поиске документов «CASC» — не путать с CASIC (CASIC — 中国航天科工集团)', [I(C + 'wikipedia_CASIC.html', 'вводная строка и раздел See also'), I(C + 'wikipedia_CASC.html', 'раздел See also')],
   'две статьи ссылаются друг на друга как на разные организации', 'не относится к кодам'),
 Z('CASC / «каскадные коды» = cascaded (concatenated) codes — каскадное (конкатенированное) кодирование', 'CASC (значения аббревиатуры)', 'каскадный (общее понятие)',
   {'определение': '«Concatenation is a method of building long codes out of shorter ones» (Forney 1965): внешний код (обычно РС над GF(2^m)) + внутренний (обычно свёрточный/блочный двоичный), между ними — перемежитель',
    'русская терминология': '«каскадные коды» (обобщённые каскадные коды Блоха–Зяблова, 1974) — статья Probl. Peredachi Inf. 10:3 файлом не получена (mathnet.ru не отдал)',
    'примеры в каталогах': 'CCSDS РС(255,223)+свёрточный 171/133, DVB-S РС(204,188)+Форни I=12+свёрточный, CD CIRC, DVD RS-PC, DVB-S2 БЧХ+LDPC — записи областей'},
   'повсеместно в спутниковой и космической связи; в модемах — «concatenated RS» (область кодов модемов)',
   [I(C + 'forney1965_concatenated_codes_ntrs.pdf', gde(C + 'forney1965_concatenated_codes_ntrs.pdf.txt', 'Concatenation is a method of building long codes out of shorter ones'), 'https://ntrs.nasa.gov/citations/19660011586'),
    I(C + 'scholarpedia_concatenated_codes.html', 'весь файл (обзор Scholarpedia)')],
   'определение — из первоисточника (Forney, MIT RLE TR 440, 1965, копия NTRS)', 'ЕСТЬ в проекте: каскад снимается по уровням (дерево разбора: свёрточный → Форни → РС)'),
 Z('Иные значения «CASC» в кодировании — в открытых источниках не найдены', 'CASC (значения аббревиатуры)', 'справочно',
   {'искали': 'поиск по «CASC» + FEC/modem/coding/LDPC/turbo/satellite; стандарты CCSDS/ETSI/IEEE 802.16 (вклады из kody2/ieee_all) — совпадений нет',
    'вывод': 'в контексте кодов «CASC» означает либо организацию CASC (и коды её стандартов — CCSDS-производные, ABS-S), либо «cascaded» = каскадный код'},
   'для аналитиков: уточнить у источника задания, какое значение имелось в виду', [I(C + 'scholarpedia_concatenated_codes.html', 'весь файл'), I(C + 'wikipedia_CASC.html', 'весь файл')],
   'поиск выполнен; отрицательный результат', '—'),
]
sohranit('casc', zap)
