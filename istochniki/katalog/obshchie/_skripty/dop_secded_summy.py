"""Дополнение (проверка полноты): коды SEC-DED памяти (OpenTitan prim_secded, Hsiao и расширенный Хэмминг)
и арифметические контрольные суммы (RFC 1071, Fletcher RFC 1146 / ISO 8073 = RFC 905 прил. B, Adler-32 RFC 1950).
Таблицы собираются из исходников с assert; записи — skripty/zapisi/dop_secded.json, dop_summy.json."""
import itertools, json, os, re, random, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zap import KOR, I, Z, sohranit, stroka

OT = 'istochniki/dop/opentitan/'
os.makedirs(os.path.join(KOR, 'tablicy', 'dop'), exist_ok=True)


def maski(fn):
    """Маски чётности из *_enc.sv: data_o[k+i] = ^(data_o & MASK); инверсия data_o ^= C (если есть)."""
    t = open(os.path.join(KOR, OT + fn)).read()
    n = int(re.search(r'output logic \[(\d+):0\] data_o', t).group(1)) + 1
    k = int(re.search(r'input +\[(\d+):0\] data_i', t).group(1)) + 1
    m = [(int(a), int(b, 16)) for a, b in re.findall(r"data_o\[(\d+)\] = \^\(data_o & \d+'h([0-9A-F]+)\);", t)]
    inv = re.search(r"data_o \^= \d+'h([0-9A-F]+);", t)
    assert [a for a, _ in m] == list(range(k, n)), fn
    return n, k, [b for _, b in m], int(inv.group(1), 16) if inv else 0, t


def stolbcy(n, k, M):
    """Столбцы проверочной матрицы H (m×n): для бита данных j — набор масок, содержащих j; бит чётности i — e_i
    (для расширенного Хэмминга последняя маска охватывает и биты чётности — тогда H приводится к виду с общей чётностью)."""
    m = len(M); cols = []
    for j in range(n):
        c = 0
        for i, mask in enumerate(M):
            if j < k:
                if mask >> j & 1: c |= 1 << i
            else:
                if j - k == i: c |= 1 << i
                elif mask >> j & 1: c |= 1 << i  # маска захватывает предыдущие биты чётности (общая чётность)
        cols.append(c)
    return cols


def d_min_po_stolbcam(cols):
    """d ≥ 4 ⇔ все столбцы ненулевые, различны и никакие три не дают 0; d = 4, если найдутся четыре с суммой 0."""
    s = set(cols)
    assert 0 not in s and len(s) == len(cols)
    for a, b in itertools.combinations(cols, 2):
        if a ^ b in s: return 3
    for a, b, c in itertools.combinations(cols, 3):
        if a ^ b ^ c in s: return 4
    return 5


zap = []; tabl = {}
konfig = open(os.path.join(KOR, OT + 'secded_cfg.hjson')).read()
for vid, pref in (('hsiao', 'prim_secded_'), ('hamming', 'prim_secded_hamming_')):
    for n, k in re.findall(r'(\d+)_(\d+)_enc\.sv', ' '.join(sorted(os.listdir(os.path.join(KOR, OT))))):
        fn = '%s%s_%s_enc.sv' % (pref, n, k)
        if not os.path.exists(os.path.join(KOR, OT + fn)): continue
        n, k, M, inv, txt = maski(fn)
        if (n, k) in [(x['n'], x['k']) for x in tabl.get(vid, [])]: continue
        n2, k2, M2, inv2, _ = maski(fn.replace('prim_secded_', 'prim_secded_inv_'))
        assert (n2, k2, M2) == (n, k, M) and inv2 and not inv  # inv-вариант: те же маски + инверсия
        cols = stolbcy(n, k, M)
        d = d_min_po_stolbcam(cols)
        assert d == 4, (fn, d)
        vesa = sorted(set(bin(c).count('1') for c in cols[:k]))
        if vid == 'hsiao':
            assert all(w % 2 for w in vesa), (fn, vesa)  # Hsiao: все столбцы нечётного веса
        else:
            assert M[-1] == (1 << (n - 1)) - 1  # последняя проверка — общая чётность всех предыдущих бит
        # декодер: маски синдрома = маска кодера | бит своей чётности
        dec = open(os.path.join(KOR, OT + fn.replace('_enc', '_dec'))).read()
        S = [int(x, 16) for x in re.findall(r"syndrome_o\[\d+\] = \^\(data_i & \d+'h([0-9A-Fa-f]+)\);", dec)]
        if vid == 'hsiao': assert S == [mm | (1 << (k + i)) for i, mm in enumerate(M)], fn
        else: assert S[:-1] == [mm | (1 << (k + i)) for i, mm in enumerate(M[:-1])] and S[-1] == (1 << n) - 1, fn
        # константы исправления декодера: data_o[j] исправляется при синдроме = столбцу j проверочной матрицы синдрома
        Hs = [sum(((s >> j) & 1) << i for i, s in enumerate(S)) for j in range(n)]
        kon = [int(x, 16) for x in re.findall(r"data_o\[\d+\] = \(syndrome_o == \d+'h([0-9a-fA-F]+)\)", dec)]
        assert kon == Hs[:k], fn
        # кодирование/синдром: случайные слова, одиночные ошибки различимы и исправимы, двойные — обнаруживаются
        rnd = random.Random(n * 1000 + k)
        for _ in range(50):
            u = rnd.getrandbits(k); c = u
            for i, mm in enumerate(M): c |= (bin(c & mm).count('1') & 1) << (k + i)
            syn = lambda w: sum((bin(w & s).count('1') & 1) << i for i, s in enumerate(S))
            assert syn(c) == 0
            e1 = rnd.randrange(n); assert syn(c ^ (1 << e1)) == Hs[e1]
            e2 = rnd.sample(range(n), 2); s2 = syn(c ^ (1 << e2[0]) ^ (1 << e2[1])); assert s2 and s2 not in Hs
        assert re.search(r'\{k: %d, +m: %d, +code_type: "%s"' % (k, n - k, vid), konfig)
        tabl.setdefault(vid, []).append(dict(n=n, k=k, m=n - k, маски_hex=['0x%X' % x for x in M], инверсия_inv_hex='0x%X' % inv2,
                                             веса_столбцов_данных=vesa, d=d, файл=OT + fn))
        nazv = 'Hsiao' if vid == 'hsiao' else 'Хэмминг расширенный'
        zap.append(Z('SEC-DED %s (%d,%d) — OpenTitan prim_secded%s' % (nazv, n, k, '' if vid == 'hsiao' else '_hamming'),
                     'Коды SEC-DED памяти (OpenTitan prim_secded)', 'Хэмминг' if vid == 'hamming' else 'Хэмминг (Hsiao SEC-DED)',
                     {'n,k': '%d,%d' % (n, k), 'd': d, 'проверочных бит': n - k,
                      'проверки': 'p_i = чётность(слово & маска_i), i = 0…%d; маски (бит j — j-й бит слова, данные — биты 0…%d, проверочные — %d…%d): %s'
                                  % (n - k - 1, k - 1, k, n - 1, ', '.join('0x%X' % x for x in M)),
                      'вид H': 'Hsiao: все столбцы данных нечётного веса %s, проверочные — единичные' % vesa if vid == 'hsiao'
                               else 'Хэмминг (n−1, k) + общая чётность в старшем бите (маска 0x%X)' % M[-1],
                      'инверсный вариант (prim_secded_inv_*)': 'те же маски, затем слово XOR 0x%X (часть проверочных бит инвертируется — ни нулевое, ни единичное слово не кодовые)' % inv2,
                      'синдром': 'syndrome_i = чётность(принятое & (маска_i | бит k+i)) (у расширенного Хэмминга последний — чётность всего слова); одиночная ошибка — синдром = столбец H; двойная — синдром ≠ 0 и не столбец',
                      'порядок бит': 'данные — младшие k бит (data_o[k-1:0] = data_i), проверочные — старшие',
                      'таблица': 'tablicy/dop/secded_opentitan.json'},
                     'память и шины СБИС (ECC SRAM/флеш, OpenTitan), общий класс кодов ECC памяти (72,64)/(39,32)/(22,16); у других СБИС маски свои — те же классы',
                     [I(OT + fn, 'строки %d–%d (маски кодера)' % (stroka(OT + fn, "data_o[%d]" % k), stroka(OT + fn, "data_o[%d]" % (n - 1)))),
                      I(OT + fn.replace('_enc', '_dec'), 'syndrome_o и константы исправления data_o'),
                      I(OT + fn.replace('prim_secded_', 'prim_secded_inv_'), 'строка %d (инверсия)' % stroka(OT + fn.replace('prim_secded_', 'prim_secded_inv_'), 'data_o ^=')),
                      I(OT + 'secded_cfg.hjson', 'строка %d (k, m, code_type)' % stroka(OT + 'secded_cfg.hjson', '{k: %d, ' % k)),
                      I(OT + 'OPENTITAN_LICENSE', 'Apache-2.0'),
                      I('istochniki/dop/arxiv_0803.1217_secded.pdf', 'стр. 1 PDF (определение проверочной матрицы Hsiao)')],
                     'маски разобраны из RTL; d=4 по столбцам H (нет нулевых/равных, ни одна тройка не даёт 0, есть четвёрка); %s; маски синдрома декодера = маски кодера | свой бит, константы исправления data_o[j] декодера = столбцы H; '
                     '50 случайных слов: синдром 0, одиночная ошибка → столбец H, двойная → обнаружена; инверсный вариант — те же маски' %
                     ('все столбцы данных нечётного веса (Hsiao)' if vid == 'hsiao' else 'последняя проверка — общая чётность'),
                     'ЧАСТИЧНО: в проекте есть Хэмминг/расширенный Хэмминг (kod.py) и блочные линейные коды; масок SEC-DED памяти нет — добавить таблицей'))
json.dump(tabl, open(os.path.join(KOR, 'tablicy', 'dop', 'secded_opentitan.json'), 'w'), ensure_ascii=False, indent=1)
sohranit('dop_secded', zap)

# ---------- контрольные суммы ----------
R = 'istochniki/dop/rfc/'
def summa1071(b):
    if len(b) % 2: b += b'\0'
    s = 0
    for i in range(0, len(b), 2):
        s += b[i] << 8 | b[i + 1]; s = (s & 0xFFFF) + (s >> 16)
    return s
primer = bytes.fromhex('0001f203f4f5f6f7')
assert summa1071(primer) == 0xDDF2  # RFC 1071 разд. 3 (Sum ddf2)
assert (~0xDDF2) & 0xFFFF == 0x220D
# RFC 1624 разд. 4: пример инкрементного обновления (m = 0x5555 → m' = 0x3285, сумма прочих 0xCD7A)
def osum(*v):
    s = 0
    for x in v: s += x; s = (s & 0xFFFF) + (s >> 16)
    return s
ne = lambda x: (~x) & 0xFFFF
HC = ne(osum(0xCD7A, 0x5555)); assert HC == 0xDD2F
assert ne(osum(0xCD7A, 0x3285)) == 0x0000                      # пересчёт заново
assert ne(osum(ne(HC), ne(0x5555), 0x3285)) == 0x0000           # RFC 1624 Eqn. 3: верно (+0)
assert osum(HC, 0x5555, ne(0x3285)) == 0xFFFF                   # RFC 1141 Eqn. 2: даёт −0 (ошибка, описанная в RFC 1624)
_r = random.Random(1624)
for _ in range(500):
    w = [_r.randrange(65536) for _ in range(10)]; k = _r.randrange(10); m2 = _r.randrange(65536)
    hc = ne(osum(*w)); w2 = w[:]; w2[k] = m2
    assert ne(osum(ne(hc), ne(w[k]), m2)) == ne(osum(*w2)) or ne(osum(*w2)) == 0xFFFF
def fletcher8(b):  # RFC 1146 прил. I: A, B — сумматоры в обратном коде (mod 255)
    A = B = 0
    for x in b: A = (A + x) % 255; B = (B + A) % 255
    return A, B
def iso_proverka(b, n):  # RFC 905 (ISO 8073) прил. B.3: X, Y в октеты n, n+1 (нумерация с 1)
    L = len(b); C0 = C1 = 0
    for x in b: C0 = (C0 + x) % 255; C1 = (C1 + C0) % 255
    X = (-C1 + (L - n) * C0) % 255; Y = (C1 - (L - n + 1) * C0) % 255
    return X, Y
rnd = random.Random(1146)
for _ in range(200):
    L = rnd.randrange(4, 200); b = bytearray(rnd.randbytes(L)); n = rnd.randrange(1, L)
    b[n - 1] = b[n] = 0
    X, Y = iso_proverka(bytes(b), n); b[n - 1] = X % 255; b[n] = Y % 255
    # 6.17.3: Σ a[i] ≡ 0 и Σ i·a[i] ≡ 0 (mod 255)
    assert sum(b) % 255 == 0 and sum((i + 1) * x for i, x in enumerate(b)) % 255 == 0
    assert fletcher8(bytes(b))[0] == 0 and fletcher8(bytes(b))[1] == 0  # то же через A, B RFC 1146
def adler32(b):
    s1, s2 = 1, 0
    for x in b: s1 = (s1 + x) % 65521; s2 = (s2 + s1) % 65521
    return s2 << 16 | s1
for t in (b'', b'123456789', b'a', rnd.randbytes(5000)):
    assert adler32(t) == zlib.adler32(t), t[:10]
zs = []
zs.append(Z('Контрольная сумма Интернета (RFC 1071): дополнение до единицы суммы 16-битных слов', 'Арифметические контрольные суммы', 'CRC-подобный (контрольная сумма)',
            {'ширина': 16, 'алгоритм': 'сумма 16-битных слов (старший байт первым) в арифметике обратного кода с переносом из старшего разряда в младший; поле = инверсия суммы; нечётный хвост дополняется нулевым байтом',
             'проверка приёмником': 'сумма по всему сообщению вместе с полем = 0xFFFF',
             'свойства': 'не зависит от порядка байт при одинаковой перестановке (RFC 1071 §2 B); инкрементное обновление (RFC 1624)',
             'тестовый вектор': '00 01 f2 03 f4 f5 f6 f7 → сумма 0xDDF2, поле 0x220D'},
            'IPv4, ICMP, IGMP, UDP, TCP (с псевдозаголовком), OSPF, PIM, EIGRP, CDP, GRE (необяз.) и др.',
            [I(R + 'rfc1071.txt', 'строки %d–%d (разд. 3, Numerical Examples)' % (stroka(R + 'rfc1071.txt', '3. Numerical Examples', 200), stroka(R + 'rfc1071.txt', 'Final Swap:  dd'))),
             I(R + 'rfc1624.txt', 'строки %d–%d (разд. 3, Eqn. 3: HC\' = ~(~HC + ~m + m\')), разд. 4 (пример)' % (stroka(R + 'rfc1624.txt', '[Eqn. 3]'), stroka(R + 'rfc1624.txt', '4.  Examples'))),
             I(R + 'rfc1141.txt', 'весь текст (первоначальная формула HC\' = HC + m + ~m\' — даёт −0 вместо +0, исправлена RFC 1624)')],
            'своя реализация: пример RFC 1071 разд. 3 — сумма 0xDDF2 (assert), поле 0x220D; пример RFC 1624 разд. 4: Eqn. 3 даёт 0x0000 как пересчёт, формула RFC 1141 — 0xFFFF (assert); 500 случайных инкрементных обновлений = пересчёту',
            'ЕСТЬ в проекте: setevoy/pole.py, zahvat_seti/zapis.py (сумма RFC 1071)'))
zs.append(Z('Контрольная сумма Флетчера 8-бит (RFC 1146 прил. I; ISO 8073 / RFC 905 прил. B — вариант с обнулением)', 'Арифметические контрольные суммы', 'CRC-подобный (контрольная сумма)',
            {'ширина': '16 (два октета A, B)', 'алгоритм RFC 1146': 'A := A + D[i]; B := B + A (сумматоры 8 бит в обратном коде, т. е. mod 255), A, B от нуля; A — первый октет, B — второй',
             'алгоритм ISO 8073 (RFC 905 прил. B)': 'C0 := C0 + a[i]; C1 := C1 + C0 по всему TPDU с нулевым полем; X = −C1 + (L−n)·C0, Y = C1 − (L−n+1)·C0 (mod 255) в октеты n, n+1 — тогда у принятого TPDU Σa[i] ≡ 0 и Σ i·a[i] ≡ 0 (mod 255)',
             'отличие': 'RFC 1146 передаёт A, B как есть; ISO/OSI (CLNP ISO 8473, IS-IS ISO 10589, TP4) подбирает X, Y так, чтобы сумма приёмника была 0',
             '16-битный вариант': 'RFC 1146 прил. II: то же с 16-битными A, B и словами D[i] (mod 65535)'},
            'TCP Alternate Checksum (опции 14/15, RFC 1146, исторический), ISO TP4 (класс 4), CLNP (ISO 8473), IS-IS (ISO 10589), OSPF LSA (Fletcher ISO 8473 — RFC 2328 §12.1.7)',
            [I(R + 'rfc1146.txt', 'строки %d–%d (прил. I), %d (прил. II)' % (stroka(R + 'rfc1146.txt', 'APPENDIX I:  The 8-bit', 100), stroka(R + 'rfc1146.txt', 'checksum does not adjust'), stroka(R + 'rfc1146.txt', 'APPENDIX II:  The 16-bit', 150))),
             I(R + 'rfc905.txt', 'строки %d–%d (6.17.3 формулы), %d–%d (прил. B, X и Y)' % (stroka(R + 'rfc905.txt', 'SUM(from i=1 to i=L) OF a[i]'), stroka(R + 'rfc905.txt', 'SUM(from i=1 to i=L) OF i*a[i]'), stroka(R + 'rfc905.txt', 'ANNEX B - CHECKSUM ALGORITHMS'), stroka(R + 'rfc905.txt', 'Y =  C1 - (L-n+1).C0')))],
            'сверка двух мест стандарта: 200 случайных TPDU — X, Y по прил. B → обе суммы 6.17.3 равны 0 (mod 255) и A = B = 0 по циклу RFC 1146 (assert)',
            'НЕТ: проект опознаёт CRC (crc.py, каталог RevEng), арифметических сумм Флетчера нет — добавить кандидатом в поиск полей'))
zs.append(Z('Adler-32 (RFC 1950, zlib)', 'Арифметические контрольные суммы', 'CRC-подобный (контрольная сумма)',
            {'ширина': 32, 'алгоритм': 's1 = 1 + Σ байт, s2 = Σ s1 после каждого байта, обе mod 65521 (наибольшее простое < 2^16); значение s2·65536 + s1, передаётся старшим байтом вперёд',
             'вектор': 'adler32("123456789") = 0x%08X' % adler32(b'123456789')},
            'поток zlib (RFC 1950: CMF, FLG, данные DEFLATE, ADLER32), PNG-фрагменты IDAT (внутри zlib), rsync (слабая сумма — родственная)',
            [I(R + 'rfc1950.txt', 'строки %d–%d (ADLER32), %d (разд. 8.2)' % (stroka(R + 'rfc1950.txt', 'ADLER32 (Adler-32 checksum)'), stroka(R + 'rfc1950.txt', 'Adler-32 checksum is stored as'), stroka(R + 'rfc1950.txt', '8.2. The Adler-32 algorithm', 400)))],
            'своя реализация = zlib.adler32 (эталонная библиотека авторов RFC 1950, лицензия zlib) на "", "a", "123456789" и 5000 случайных байтах (assert)',
            'НЕТ (сжатие zlib проект узнаёт по сигнатуре в karta.py; сумма Adler-32 как поле не проверяется)'))
sohranit('dop_summy', zs)
