"""Дополнение каталога provod проверяющим: новые записи (пропущенные коды области) — каждая
сверена со страницей первоисточника (str_ok) и, где в источнике есть пример, — своим расчётом.
Пишет proverka/novye.json (новые записи), proverka/obnovleniya.json (дополнения к старым
записям) и таблицу tablicy/ieee8023cz_rs544_522_g.json. Журнал — в pv.ZHURNAL."""
import json, re, random
from pv import *

NOVYE = []      # новые записи каталога
OBNOV = {}      # имя старой записи → {"источник+": [...], "проверка+": str, "параметры+": {...}, ...}
PROV = {}       # имя новой записи → список итогов своих проверок (для поля «проверка»)

def ist(fail, str_, url, chto):
    return {"файл": fail, "страница|строки": str_, "url": url, "что": chto}

def url_iz_manifesta(put):
    for l in open(os.path.join(KOREN, "manifest.jsonl")):
        z = json.loads(l)
        if z["put"] == put: return z["url"]
    raise KeyError(put)

class Z:
    """Черновик записи: имя — ключ журнала; ok/str_ok пишут в общий журнал."""
    def __init__(self, imya): self.imya = imya; self.d = {"имя": imya}
    def s(self, pdf, p, chto, *obr, sosedi=0):
        r = str_ok(self.d, pdf, p, chto, *obr, sosedi=sosedi); assert r, (self.imya, chto); return r
    def o(self, chto, usl, podr=""):
        r = ok(self.imya, chto, usl, podr); assert r, (self.imya, chto, podr); PROV.setdefault(self.imya, []).append(chto); return r

def crc_msb(data_bits, poly, w, init=0):
    return crc_bits(data_bits, poly & ((1 << w) - 1), w, init)

def bity(bb):
    return [int(b) for B in bb for b in format(B, "08b")]

# ======================================================================================
# 1. GFP (ITU-T G.7041): заголовки cHEC/tHEC/eHEC
G7041 = "istochniki/itu/T-REC-G.7041-201608-I.pdf"; U7041 = url_iz_manifesta(G7041)
z = Z("GFP (G.7041): cHEC/tHEC/eHEC — CRC-16 x^16+x^12+x^5+1 с исправлением одиночной ошибки + маскирование ядра заголовка B6AB31E0")
z.s(G7041, 16, "6.1.1.2.1: G(x) = x^16+x^12+x^5+1, начальное 0, x^16 — старший; одиночное исправление", "G(x) = x16 + x12 + x5 + 1, with an initialization", "value of zero", "This single error correction shall be performed on the core header")
z.s(G7041, 16, "6.1.1.3: маскирование ядра заголовка B6AB31E0 (Баркер-подобная 32)", "exclusive-OR operation (modulo 2 addition) with", "the hexadecimal number B6AB31E0")
z.s(G7041, 19, "6.1.2.1.2: tHEC — те же шаги по полю типа, одиночное исправление", "The content of the tHEC field is generated using the same steps as the cHEC", "perform single-bit error correction on the type field")
z.s(G7041, 21, "6.1.2.1.4: eHEC — по расширенному заголовку без eHEC, исправление необязательно", "The content of the eHEC field is generated using the same steps as the cHEC", "Single error correction is optional for the extension header")
z.s(G7041, 62, "прил. III.1: PLI 0048 → cHEC C9 CC, TYPE 0101 → tHEC 23 10, CID/SPARE 8000 → eHEC 1B 98", "Appendix III", "GFP frame example illustrating transmission order and CRC calculation")
z.s(G7041, 64, "прил. III.1: ядро после маски B6 E3 F8 2C; пошаговый расчёт cHEC = 0xC9CC", "B6", "E3", "F8", "2C", "cHEC[15:0] = 0xC9CC")
z.s(G7041, 65, "прил. III.3: PLI 0024 → cHEC 64 E6; TYPE 100D → tHEC D2 DE", "III.3", "64", "E6", "D2", "DE")
P1021 = 0x11021
hec = lambda bb: crc_msb(bity(bb), P1021, 16)
vek = [(b"\x00\x48", 0xC9CC, "cHEC(PLI 0048)"), (b"\x01\x01", 0x2310, "tHEC(TYPE 0101)"), (b"\x80\x00", 0x1B98, "eHEC(CID 80, SPARE 00)"),
       (b"\x00\x24", 0x64E6, "cHEC(PLI 0024)"), (b"\x10\x0d", 0xD2DE, "tHEC(TYPE 100D)")]
z.o("прил. III.1/III.3: 5 значений HEC = свой CRC-16 (x^16+x^12+x^5+1, начальное 0, старший бит первым)", all(hec(m) == v for m, v, _ in vek), "; ".join(f"{n} = {hec(m):04X}" for m, v, n in vek))
mask = lambda pli, c: ((pli << 16) | c) ^ 0xB6AB31E0
z.o("прил. III.1/III.3: ядро после маски = B6E3F82C и B68F5506 (свой XOR)", mask(0x0048, 0xC9CC) == 0xB6E3F82C and mask(0x0024, 0x64E6) == 0xB68F5506)
# одиночное исправление: все 32 синдрома одиночных ошибок в 32-битном ядре различны и ненулевы
sind = {pmod(1 << i, P1021) for i in range(32)}
z.o("код (32,16) ядра заголовка: 32 синдрома одиночных ошибок различны и ненулевы — исправление возможно (свой расчёт)", len(sind) == 32 and 0 not in sind)
z.d.update({"семейство": "GFP / ATM / PDH (кадрирование и контроль заголовков)", "область": "provod", "вид": "CRC-подобный (циклический (32,16) с исправлением одиночной ошибки)",
    "параметры": {"многочлен": "G(x) = x^16 + x^12 + x^5 + 1 (0x1021), начальное 0, без выходной инверсии; x^16 — старший бит; первым передаётся коэффициент x^15",
        "cHEC": "по 2 байтам PLI (ядро заголовка 4 байта: PLI + cHEC); обязательное исправление одиночной ошибки",
        "tHEC": "по 2 байтам поля типа (PTI/PFI/EXI/UPI)", "eHEC": "по расширенному заголовку 0–58 байт (без eHEC); исправление необязательно",
        "маскирование": "ядро заголовка (PLI + cHEC) складывается с B6AB31E0 (только ядро; остальное — скремблер 1 + x^43, см. запись GFP-F)",
        "выделение кадров": "HUNT → PRESYNC → SYNC по cHEC (DELTA подряд верных), исправление в HUNT/PRESYNC отключено",
        "эквивалент RevEng": "CRC-16/XMODEM (0x1021, init 0, без отражений)"},
    "где применяется": "GFP-F/GFP-T поверх SDH/SONET (VC-n, VCAT), OTN (OPUk), Ethernet over SDH (EoS), G.7041/Y.1303",
    "источник": [ist(G7041, "стр. 16 (номер страницы PDF)", U7041, "6.1.1.2.1 HEC processing, 6.1.1.3 Core header scrambling"),
                 ist(G7041, "стр. 19 (номер страницы PDF)", U7041, "6.1.2.1.2 tHEC"), ist(G7041, "стр. 21 (номер страницы PDF)", U7041, "6.1.2.1.4 eHEC"),
                 ist(G7041, "стр. 62–65 (номер страницы PDF)", U7041, "Appendix III.1 и III.3 — примеры")],
    "сложность внедрения": "ЕСТЬ в проекте: src/reportgen/potok/gfp.py — hec16, выделение по cHEC с любой маской, tHEC/eHEC, исправление одиночной ошибки"})
NOVYE.append(z)

# 2. GFP-F: pFCS + скремблер нагрузки
z = Z("GFP-F (G.7041): pFCS CRC-32 (ISO/IEC 13239, предустановка единиц, инверсия) + самосинхронизирующийся скремблер нагрузки 1 + x^43")
z.s(G7041, 21, "6.1.2.2.1.1: CRC-32 G(x) = x^32+x^26+…+x+1, x^32 — старший", "The pFCS is generated using the CRC-32 generating polynomial", "x32 corresponds to the")
z.s(G7041, 22, "шаги 2–4: + x^8N·U(x) (≡ предустановка единиц), дополнение; остаток приёмника C704DD7B", "the all-1s polynomial U(x) = 1 +", "The complement of this 32-bit sequence is the CRC-32", "11000111_00000100_11011101_01111011")
z.s(G7041, 22, "6.1.2.3: скремблер 1 + x^43 на всю область нагрузки, состояние сохраняется между кадрами", "1 + x43 self-synchronous scrambler", "When the scrambler or", "descrambler is disabled, its state is retained")
z.s(G7041, 66, "прил. III.3: pFCS 87 64 8B BB по 28 байтам MPLS", "87", "64", "8B", "BB", "Covers only payload information field")

t = " ".join(stranica(G7041, 65).split()) + " " + " ".join(stranica(G7041, 66).split())
dannye = bytes(int(h, 16) for h in re.findall(r"\b\d{1,2} DATA ([0-9A-F]{2}) ; \d+d", t))
z.o(f"прил. III.3: из таблицы разобрано {len(dannye)} байт данных MPLS", len(dannye) == 28)
P32 = 0x104C11DB7
fcs = crc_msb(bity(dannye), P32, 32, init=0xFFFFFFFF) ^ 0xFFFFFFFF
z.o("прил. III.3: свой pFCS (предустановка 1…1, старший бит первым, дополнение) = 87648BBB", fcs == 0x87648BBB, f"{fcs:08X}")
ost = crc_msb(bity(dannye + fcs.to_bytes(4, "big")), P32, 32, init=0xFFFFFFFF)
z.o("остаток приёмника по данным + pFCS = 11000111 00000100 11011101 01111011 (C704DD7B), как в 6.1.2.2.1.1", ost == 0xC704DD7B, f"{ost:08X}")
m = re.search(r'"CRC-32/BZIP2", 32, (0x[0-9A-F]+), (0x[0-9A-F]+), (\w+), (\w+), (0x[0-9A-F]+), (0x[0-9A-F]+)', open("/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py").read())
z.o("модель совпадает с CRC-32/BZIP2 каталога RevEng (контрольное значение «123456789» = FC891918 своим расчётом)",
    m and int(m.group(1), 16) == 0x04C11DB7 and crc_bytes(b"123456789", 0x04C11DB7, 32, 0xFFFFFFFF, False, False, 0xFFFFFFFF) == int(m.group(6), 16) == 0xFC891918)
z.d.update({"семейство": "GFP / ATM / PDH (кадрирование и контроль заголовков)", "область": "provod", "вид": "CRC-подобный + скремблер",
    "параметры": {"pFCS": "CRC-32: G(x) = x^32+x^26+x^23+x^22+x^16+x^12+x^11+x^10+x^8+x^7+x^5+x^4+x^2+x+1, предустановка 1…1, старший бит первым, дополнение результата (= CRC-32/BZIP2); только по полю информации клиента; наличие — бит PFI поля типа",
        "остаток приёмника": "C704DD7B (11000111_00000100_11011101_01111011, x^31…x^0)",
        "скремблер": "самосинхронизирующийся 1 + x^43 на всю область нагрузки (после cHEC до конца кадра), в сетевом порядке бит; при отключении состояние сохраняется — в начале кадра это последние 43 бита предыдущего кадра",
        "дескремблер": "включён только в состоянии SYNC; двойная ошибка через 43 бита после дескремблера"},
    "где применяется": "GFP-F (Ethernet, PPP, MPLS, IP поверх SDH/OTN), G.7041",
    "источник": [ist(G7041, "стр. 21–22 (номер страницы PDF)", U7041, "6.1.2.2.1.1 pFCS, 6.1.2.3 Payload area scrambling"),
                 ist(G7041, "стр. 65–66 (номер страницы PDF)", U7041, "Appendix III.3 — пример GFP-F с MPLS и pFCS")],
    "сложность внедрения": "ЕСТЬ в проекте: src/reportgen/potok/gfp.py — crc32_bzip2 (pFCS), снятие скремблера x^43"})
NOVYE.append(z)

# 3. GFP-T суперблок
z = Z("GFP-T (G.7041): суперблок 8 × 64B/65B + CRC-16 x^16+x^15+x^12+x^10+x^4+x^3+x^2+x+1 (536 бит)")
z.s(G7041, 49, "8.1.1.3: 8 кодов 64B/65B → суперблок, флаги L1…L8 в замыкающем октете, CRC-16 в двух последних", "group eight 64B/65B codes into a superblock", "The 16 bits of the last two trailing octets are used for a CRC-16 error check")
z.s(G7041, 50, "8.1.2.1: G(x), начальное 0, 65 октетов → 520 бит, остаток 16 бит", "G(x) = x16 + x15 + x12 + x10 + x4 + x3 + x2 + x + 1 with an", "initialization value of zero", "the first 65 octets of the superblock are taken in network octet order")
z.s(G7041, 65, "III.2: октет (1,1) = 80h, остальные 0 → CRC 9AA2", "9AA2 hex")
sb = bytes([0x80]) + bytes(64)
c = crc_msb(bity(sb), 0x1941F, 16)
z.o("III.2: свой CRC-16 суперблока (0x941F, начальное 0, старший бит первым) = 9AA2", c == 0x9AA2, f"{c:04X}")
z.d.update({"семейство": "GFP / ATM / PDH (кадрирование и контроль заголовков)", "область": "provod", "вид": "CRC-подобный (с возможным исправлением одиночной ошибки)",
    "параметры": {"суперблок": "8 кодов 64B/65B: 64 октета данных/управления + октет флагов L1…L8 + 2 октета CRC = 536 бит",
        "CRC-16": "G(x) = x^16+x^15+x^12+x^10+x^4+x^3+x^2+x+1 (0x941F), начальное 0, по первым 65 октетам суперблока, старший бит первым; CRC-1 — старший",
        "кадр GFP-T": "N × 536 бит + заголовки (N — по табл. IV.1)", "клиенты": "8B/10B (Fibre Channel, ESCON, FICON, GbE, DVB ASI) → 64B/65B; 65B_PAD",
        "примечание": "одиночное исправление возможно, но после дескремблера x^43 ошибки сдвоены через 43 бита"},
    "где применяется": "прозрачный GFP-T (SAN-расширение FC/FICON/ESCON, GbE) поверх SDH/OTN",
    "источник": [ist(G7041, "стр. 49–50 (номер страницы PDF)", U7041, "8.1.1.3 Adaptation of 64B/65B code blocks into GFP, 8.1.2.1 Error control"),
                 ist(G7041, "стр. 65 (номер страницы PDF)", U7041, "Appendix III.2 — пример CRC суперблока")],
    "сложность внедрения": "частично: src/reportgen/potok/gfp.py — суперблоки 64B/65B выделяются, CRC-16 ищется вслепую (crc.найти); стандартный многочлен 0x941F можно задать явно"})
NOVYE.append(z)

# 4. ATM HEC
I432 = "istochniki/itu/T-REC-I.432.1-199902-I.pdf"; U432 = url_iz_manifesta(I432)
z = Z("ATM HEC (I.432.1): CRC-8 x^8+x^2+x+1 по 4 байтам заголовка + смежный класс 01010101, исправление одиночной ошибки, выделение ячеек")
z.s(I432, 12, "7.3.2.2: x^8+x^2+x+1, предустановка 0, сложение с 0101 0101; пример «нули → 0101 0101»", "generator polynomial x8 + x2 + x + 1 of the product x8 multiplied by the content of the header", "the recommended pattern is \"0101 0101\"", "0000 0000 0000 0000 0000 0000 0000 0000 0101 0101")
z.s(I432, 14, "7.3.3.2: ALPHA = 7, DELTA = 6 (SDH) / 8 (на ячейках); скремблер x^43 + 1", "For an SDH-based Physical Layer, ALPHA = 7 and DELTA = 6", "For a cell-based Physical Layer, ALPHA = 7 and DELTA = 8", "self-synchronizing scrambler with polynomial x43 + 1")
h = crc_msb([0] * 32, 0x107, 8) ^ 0x55
z.o("пример 7.3.2.2: заголовок из нулей → HEC 0x55 (свой расчёт)", h == 0x55)
m = re.search(r'"CRC-8/I-432-1", 8, (0x[0-9A-F]+), (0x[0-9A-F]+), (\w+), (\w+), (0x[0-9A-F]+), (0x[0-9A-F]+)', open("/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py").read())
z.o("второй источник — каталог RevEng CRC-8/I-432-1 (0x07, xorout 0x55): «123456789» → A1 своим расчётом", m and int(m.group(1), 16) == 7 and int(m.group(5), 16) == 0x55 and crc_bytes(b"123456789", 0x07, 8, 0, False, False, 0x55) == int(m.group(6), 16) == 0xA1)
sind = {pmod(1 << i, 0x107) for i in range(40)}
z.o("(40,32): 40 синдромов одиночных ошибок различны и ненулевы — исправление возможно (свой расчёт)", len(sind) == 40 and 0 not in sind)
z.d.update({"семейство": "GFP / ATM / PDH (кадрирование и контроль заголовков)", "область": "provod", "вид": "CRC-подобный (укороченный циклический (40,32) с исправлением одиночной ошибки)",
    "параметры": {"многочлен": "x^8 + x^2 + x + 1, предустановка 0, первый бит заголовка — старший член", "смежный класс": "проверочные биты + 0101 0101 (0x55), приёмник вычитает",
        "режимы приёмника": "исправление одиночной ошибки / обнаружение (рис. 3)", "выделение ячеек": "HUNT (побитно) → PRESYNC (DELTA верных) → SYNCH; выход после ALPHA неверных; SDH: ALPHA 7, DELTA 6; на ячейках: ALPHA 7, DELTA 8",
        "скремблер": "самосинхронизирующийся x^43 + 1 на поле информации (SDH); на ячейках — распределённый выборочный x^31 + x^28 + 1 (см. область obshchie)",
        "эквивалент RevEng": "CRC-8/I-432-1"},
    "где применяется": "ATM UNI/NNI (SDH, PDH G.804, на ячейках), xDSL ATM-TC, DVB-RCC (ES 200 800), IMA",
    "источник": [ist(I432, "стр. 12–14 (номер страницы PDF)", U432, "7.3.2 HEC, 7.3.3 Cell delineation, 7.3.4 Scrambler")],
    "сложность внедрения": "ЕСТЬ в проекте: src/reportgen/potok/gfp.py — hec8 (CRC-8 + 0x55), выделение ячеек по HEC, скремблер x^43 (задача CCSDS/ATM)"})
NOVYE.append(z)

# 5. G.704 CRC-4/5/6
G704 = "istochniki/itu/T-REC-G.704-199810-I.pdf"; U704 = url_iz_manifesta(G704)
z = Z("PDH G.704: CRC-6 (1544, ESF), CRC-5 (6312) и CRC-4 (2048, сверхцикл 16 циклов) — положение в цикле и правила расчёта")
z.s(G704, 9, "2.1.3.1.2: CRC-6, CMB 4632 бит, F-биты → 1, x^6 + x + 1, e1 — старший", "CRC-6 Message Block (CMB) is a sequence of 4632 serial bits", "the F-bits are replaced by binary 1s", "generator polynomial x6 + x + 1")
z.s(G704, 14, "2.2.3.2: CRC-5, CMB 3151 бит; синхросигнал 110010100", "Cyclic Redundancy Check 5 (CRC-5) Message Block (CMB) is a sequence of 3151 serial bits", "The frame and multiframe alignment signal is 110010100")
z.s(G704, 15, "CRC-5: x^5 + x^4 + x^2 + 1", "generator polynomial x5 + x4 + x2 + 1")
z.s(G704, 17, "2.3.3.3: сверхцикл CRC-4 — 16 циклов, два подсверхцикла по 2048 бит, C1…C4 в бите 1 циклов с FAS", "two 8-frame Sub-Multiframes (SMF)", "(CRC-4) block size (i.e. 2048 bits)", "There are four CRC-4 bits, designated C1, C2, C3 and C4 in each SMF")
z.s(G704, 18, "2.3.3.5.2: x^4 + x + 1, биты CRC в SMF → 0, слово в SMF N по SMF N−1", "division (modulo 2) by the generator polynomial x4 + x + 1", "The CRC-4 bits in the SMF are replaced by binary 0s")
kat = open("/home/user/poiujnbhy/src/reportgen/potok/crc_katalog.py").read()
rev = {n: int(p, 16) for n, p in re.findall(r'"(CRC-[456]/G-704)", \d, (0x[0-9A-F]+)', kat)}
z.o("многочлены = RevEng CRC-4/G-704 (0x3), CRC-5/G-704 (0x15), CRC-6/G-704 (0x03)", rev == {"CRC-4/G-704": 0x3, "CRC-5/G-704": 0x15, "CRC-6/G-704": 0x03}, str(rev))
z.o("x^4+x+1 и x^6+x+1 примитивны; x^5+x^4+x^2+1 = (x+1)(x^4+x+1)… период 15 (свой расчёт)", primitiven(st(4, 1, 0)) and primitiven(st(6, 1, 0)) and pmod(st(5, 4, 2, 0), st(1, 0)) == 0 and period(st(5, 4, 2, 0)) == 15, f"период CRC-5 = {period(st(5, 4, 2, 0))}")
z.d.update({"семейство": "GFP / ATM / PDH (кадрирование и контроль заголовков)", "область": "provod", "вид": "CRC-подобный",
    "параметры": {"1544 кбит/с (ESF)": "CRC-6, x^6 + x + 1; CMB = сверхцикл 4632 бит (24 цикла), F-биты при расчёте = 1; e1…e6 в битах 194, 966, 1738, 2510, 3282, 4054 следующего сверхцикла; FPS 001011",
        "6312 кбит/с": "CRC-5, x^5 + x^4 + x^2 + 1; CMB 3151 бит (цикл 1 бит 1 … цикл 4 бит 784); e1…e5 — последние 5 бит сверхцикла; синхросигнал 110010100",
        "2048 кбит/с": "CRC-4, x^4 + x + 1; сверхцикл 16 циклов = 2 SMF по 2048 бит; C1…C4 в бите 1 циклов с FAS; слово SMF N считается по SMF N−1 с C-битами = 0; MFAS 001011, E-биты",
        "эквивалент RevEng": "CRC-4/G-704, CRC-5/G-704, CRC-6/G-704 (описаны также в области obshchie)"},
    "где применяется": "T1/DS1 ESF, J2 (6312), E1 с CRC-4 (G.704/G.706), DVB-RCC SL-ESF (CRC-6)",
    "источник": [ist(G704, "стр. 9 (номер страницы PDF)", U704, "2.1.3.1.2 CRC-6"), ist(G704, "стр. 14–15 (номер страницы PDF)", U704, "2.2.3.2 CRC-5"),
                 ist(G704, "стр. 17–18 (номер страницы PDF)", U704, "2.3.3.3–2.3.3.5 CRC-4"), ist(G704, "стр. 42–43 (номер страницы PDF)", U704, "Annex A — схема регистра CRC-4")],
    "сложность внедрения": "ЕСТЬ в проекте: src/reportgen/potok/cikl.py (сверхцикл CRC-4 E1), src/reportgen/potok/pdh_na.py (ESF CRC-6); CRC-5 J2 — через общий crc.py"})
NOVYE.append(z)

# 6. ISDN BA (ETSI TS 102 080)
T102 = "istochniki/etsi/ts_102080v010401p.pdf"; U102 = url_iz_manifesta(T102)
z = Z("ISDN BA U-интерфейс (ETSI TS 102 080 / ITU-T G.961): 2B1Q — синхрослово 9 символов, сверхцикл 8, CRC-12; скремблеры 1⊕x^-5⊕x^-23 / 1⊕x^-18⊕x^-23 (2B1Q и MMS43)")
z.s(T102, 37, "A.3/A.4: цикл 120 символов, 12 слотов 2B+D по 18 бит; FW = +3 +3 −3 −3 −3 +3 −3 +3 +3, IFW — инверсия", "The number of 2B+D slots in a frame shall be 12", "FW = +3 +3 -3 -3 -3 +3 -3 +3 +3", "IFW = -3 -3 +3 +3 +3 -3 +3 -3 -3", sosedi=1)
z.s(T102, 41, "A.8.3.1.2: CRC-12 P(x) = x^12⊕x^11⊕x^3⊕x^2⊕x⊕1, сброс в начале сверхцикла, FW/IFW/M1–M3/M5/M6 не входят", "P(x) = x12 ⊕ x11 ⊕ x3 ⊕ x2 ⊕ x ⊕ 1", "At the beginning of a multiframe, all register cells are cleared", "(FW, IFW, M1, M2, M3, M5, M6)")
z.s(T102, 46, "A.11 (2B1Q): скремблер 23-го порядка, LT→NT1 1⊕x^-5⊕x^-23, NT1→LT 1⊕x^-18⊕x^-23", "scrambled with a 23rd order polynomial", "1 ⊕ x-5 ⊕ x-23", "1 ⊕ x-18 ⊕ x-23")
z.s(T102, 83, "B.9 (MMS43/4B3T): те же многочлены, только каналы 2B+D", "Scrambling shall be applied only to the 2B+D channels", "in direction LT to NT1: 1 ⊕ x-5 ⊕ x-23", "in direction NT1 to LT: 1 ⊕ x-18 ⊕ x-23")
z.o("1 + x^5 + x^23 и 1 + x^18 + x^23 — взаимные примитивные (период 2^23 − 1, свой расчёт)", primitiven(st(23, 5, 0)) and primitiven(st(23, 18, 0)))
z.o("CRC-12 0x80F = многочлен RevEng CRC-12/DECT и CRC-12/UMTS (каталог проекта)", "0x80F" in kat and re.search(r'"CRC-12/DECT", 12, 0x80F', kat) is not None)
z.o("цикл 2B1Q: 9 символов FW + 12 × 9 символов 2B+D + 3 символа M = 120 символов (свой счёт по A.3)", 9 + 12 * 18 // 2 + 6 // 2 == 120)
z.d.update({"семейство": "ISDN / цифровые абонентские линии", "область": "provod", "вид": "CRC-подобный + скремблер (без FEC)",
    "параметры": {"линейный код": "2B1Q (прил. A, 160 кбит/с, 80 кБод) или MMS43/4B3T (прил. B, 120 кБод)",
        "цикл 2B1Q": "120 символов за 1,5 мс: FW (9 символов) + 12 слотов 2B+D по 18 бит + 6 бит M (CL-канал); сверхцикл 8 циклов, первый — с IFW",
        "синхрослово": "FW = +3 +3 −3 −3 −3 +3 −3 +3 +3; IFW = −3 −3 +3 +3 +3 −3 +3 −3 −3",
        "CRC-12": "P(x) = x^12 + x^11 + x^3 + x^2 + x + 1; биты M5/M6 циклов 3–8; регистр сбрасывается в начале сверхцикла; FW/IFW/M1–M3/M5/M6 не охватываются; передаётся в следующем сверхцикле",
        "скремблер": "самосинхронизирующийся 23-го порядка: LT→NT1 1 ⊕ x^-5 ⊕ x^-23, NT1→LT 1 ⊕ x^-18 ⊕ x^-23 (2B1Q — все биты кроме FW; MMS43 — только 2B+D)"},
    "где применяется": "ISDN BRI U-интерфейс (Европа: 2B1Q и 4B3T/MMS43; ANSI T1.601 — 2B1Q), G.961",
    "источник": [ist(T102, "стр. 36–37 (номер страницы PDF)", U102, "A.3 Frame structure, A.4 Frame word"), ist(T102, "стр. 41 (номер страницы PDF)", U102, "A.8.3.1 CRC"),
                 ist(T102, "стр. 46–47 (номер страницы PDF)", U102, "A.11 Scrambling (2B1Q)"), ist(T102, "стр. 83 (номер страницы PDF)", U102, "B.9 Scrambling (MMS43)")],
    "сложность внедрения": "нет: синхрослово символьное (2B1Q), CRC-12 и многоотводный самосинхронизирующийся скремблер — общими средствами (crc.py, skrembler.py)"})
NOVYE.append(z)

# 7. DVB-RCC / DAVIC (ETSI ES 200 800)
E800 = "istochniki/etsi/es_200800v010301p.pdf"; U800 = url_iz_manifesta(E800)
z = Z("DVB-RCC / DAVIC (ETSI ES 200 800): RS(55,53) t=1 + свёрточное перемежение I=5 (нисходящий OOB), RS(59,53)/(118,106) и RS(16,14)/(11,9) (восходящий), рандомизатор 1+x^5+x^6, CRC-6 SL-ESF")
z.s(E800, 20, "нисходящий OOB: самосинхронизирующийся рандомизатор — частное от деления на 1 + x^5 + x^6", "quotient of the input data multiplied by x6 and then divided by the generator polynomial", "self-synchronizing de-randomizer")
z.s(E800, 25, "5.2.3.4 восходящий: аддитивный 1 + x^5 + x^6, затравка «все единицы», ПСП начинается 00000100", "with seed all ones", "starts with 00000100", "non self-synchronizing de-randomizer")
z.s(E800, 28, "уникальное слово CC CC CC 0D (QPSK) и F3 F3 F3 F3 F3 F3 33 F7 (16QAM)", "(CC CC CC 0D hex)", "(F3 F3 F3 F3 F3 F3 33 F7)")
z.s(E800, 31, "SL-ESF: CRC-6 x^6 + x + 1, CMB 4632 бит, служебные биты = 1, начальное 0", "CRC Message block [CMB] size = 4 632 bits", "The initial remainder value is preset to all zeros")
z.s(E800, 32, "HEC ATM с 01010101; RS t = 1 → (55,53)", "modulo-2 addition (XOR) of the pattern 01010101b to the HEC bits", "codeword of (55,53)")
z.s(E800, 33, "g(x) = (x+µ^0)(x+µ^1), µ = 02h, p(x) = x^8+x^4+x^3+x^2+1, 200 нулей перед (255,253); перемежение Форни I = 5", "g(x) = (x + µ0)(x + µ1), where µ = 02 hex", "p(x) = x8 + x4 + x3 + x2 + 1", "appending 200 bytes, all set to zero", "with I = 5")
z.s(E800, 42, "восходящий QPSK: T = 3, (59,53), 196 нулей перед (255,249)", "codeword of (59,53)", "appending 196 bytes, all set to zero")
z.s(E800, 43, "16QAM: T = 6, (118,106), g(x) = (x+µ^0)…(x+µ^5)", "codeword of (118,106)", "(x + µ5), where µ = 02 hex")
z.s(E800, 57, "минислоты: RS(16,14) QPSK и RS(11,9) 16QAM, укорочение 239/244 нулями", "(16,14) for", "QPSK modulation and (11,9) for 16QAM modulation", "appending 239 bytes for QPSK modulation (244 for 16QAM modulation)")
reg = [1] * 6; out = []
for _ in range(16):
    b = reg[4] ^ reg[5]; out.append(b); reg = [b] + reg[:5]
z.o("свой ЛРС 1 + x^5 + x^6 (затравка 111111, выход = r5 ⊕ r6) даёт 00000100… как в 5.2.3.4", "".join(map(str, out[:8])) == "00000100", "".join(map(str, out)))
z.o("1 + x^5 + x^6 примитивен (период 63); g(x) = x^2 + 03x + 02 для t = 1 (свой расчёт над 0x11D)", primitiven(st(6, 5, 0)) and rs_g(8, 0x11D, range(2)) == [1, 3, 2])
z.o("укорочения: 255 − 200 = 55, 255 − 196 = 59, 255 − 239 = 16, 255 − 244 = 11 (свой счёт)", (255 - 200, 255 - 196, 255 - 239, 255 - 244) == (55, 59, 16, 11))
z.d.update({"семейство": "Кабельное ТВ и DOCSIS (J.83, J.112/J.122, CableLabs)", "область": "provod", "вид": "РС (укороченный) + рандомизатор + CRC",
    "параметры": {"поле": "GF(256), p(x) = x^8 + x^4 + x^3 + x^2 + 1, µ = 02h",
        "нисходящий OOB (1,544/3,088 Мбит/с, QPSK)": "кадр SL-ESF (как T1 ESF, CRC-6 x^6 + x + 1, CMB 4632 бит); каждая ячейка ATM → RS(55,53), t = 1 (укороч. RS(255,253), g = (x+µ^0)(x+µ^1)); свёрточное перемежение Форни I = 5, M = 11; рандомизатор самосинхронизирующийся 1 + x^5 + x^6 (после FEC); дифференциальная QPSK",
        "восходящий (TDMA, QPSK/16QAM)": "уникальное слово CC CC CC 0D (QPSK) / F3 F3 F3 F3 F3 F3 33 F7 (16QAM) в открытом виде; ячейка ATM → RS(59,53) T = 3 (QPSK) или 2 ячейки → RS(118,106) T = 6 (16QAM), g = ∏_{i=0}^{2T−1}(x+µ^i); минислоты RS(16,14)/(11,9) T = 1; рандомизатор аддитивный 1 + x^5 + x^6, затравка 111111, начало 00000100…",
        "HEC": "ATM HEC с маской 01010101 (I.432)", "внутриполосный нисходящий": "DVB-C (ETS 300 429 = J.83 Annex A) — см. запись J.83 Annex A/C"},
    "где применяется": "интерактивный канал кабельного ТВ DVB-RCC / DAVIC 1.x (ETS 300 800, ES 200 800), SCTE 55-2",
    "источник": [ist(E800, "стр. 20–21 (номер страницы PDF)", U800, "рандомизатор нисходящего OOB"), ist(E800, "стр. 25 (номер страницы PDF)", U800, "5.2.3.4 Randomizer (Upstream)"),
                 ist(E800, "стр. 28 (номер страницы PDF)", U800, "уникальное слово"), ist(E800, "стр. 31–33 (номер страницы PDF)", U800, "SL-ESF CRC-6, RS(55,53), перемежение I = 5"),
                 ist(E800, "стр. 42–43 (номер страницы PDF)", U800, "восходящий RS(59,53)/(118,106)"), ist(E800, "стр. 57 (номер страницы PDF)", U800, "минислоты RS(16,14)/(11,9)")],
    "сложность внедрения": "частично: RS над GF(256) с корнями µ^0… и укорочением — rs_bch.py; свёрточное перемежение — peremezhenie.py/forni.py; кадр SL-ESF как ESF — pdh_na.py; разборщика DVB-RCC нет"})
NOVYE.append(z)

# 8. 802.3bz 2.5G/5GBASE-T
BZ = "istochniki/ieee8023/bz/Shirani_3bz_02_0515.pdf"; BZM = "istochniki/ieee8023/bz/IEEE_802bz_Motions_0515.pdf"; BZS = "istochniki/ieee8023/bz/Souvignier_3bz_01_0515.pdf"
z = Z("2.5GBASE-T/5GBASE-T (802.3bz, Cl.126): LDPC(2048,1723) 10GBASE-T, все биты под LDPC — 1 + 25×65 + 97 известных нулей + 325 проверочных, PAM16")
z.s(BZM, 2, "решение №4 (май 2015): базовое предложение PMA/PCS — Shirani_3bz_02_0515 стр. 3–5", "Move to adopt PMA/PCS Consensus Baseline Proposal as defined in", "Shirani_3bz_02_0515.pdf pages 3 to 5")
z.s(BZ, 3, "PAM16, 325 бит защищают ранее некодированные биты, 97 бит = 0", "Use PAM 16 signaling per symbol", "97 bits are set to zero", "All bits are protected by LDPC")
z.s(BZ, 4, "5G — 400 Мсимв/с, 2.5G — 200 Мсимв/с; кадр LDPC 320/640 нс", "5Gb/s via fully LDPC coded PAM 16 running at 400Ms/s", "2.5Gb/s via fully LDPC coded PAM 16 running at 200Ms/s")
z.s(BZ, 5, "кадр PCS: [1 бит Aux, 25 × 65 бит, 97 нулей] + 325 проверочных = 2048", "[1 Aux channel bit , 25 x 65 bit blocks, 97 zero bits ] + 325 parity bits = 2048 bits")
z.s(BZS, 5, "основа — DSQ-128 и LDPC (2048,1723) 10GBASE-T", "DSQ-128 and (2048,1723) LDPC")
z.o("1 + 25·65 + 97 = 1723 = k, 1723 + 325 = 2048 = n (свой счёт)", 1 + 25 * 65 + 97 == 1723 and 1723 + 325 == 2048)
z.d.update({"семейство": "Ethernet IEEE 802.3", "область": "provod", "вид": "LDPC (укорочение известными нулями)",
    "параметры": {"код": "тот же LDPC(2048,1723) RS-LDPC, что 10GBASE-T (Cl.55) — матрица H: см. запись 10GBASE-T и область obshchie",
        "кадр PCS": "[1 бит вспомогательного канала, 25 блоков 64b/65b, 97 бит = 0] + 325 проверочных = 2048; в отличие от 10GBASE-T (DSQ128, часть бит без кода) все биты под LDPC",
        "модуляция": "PAM16 (4 бита на символ, 8 бит на 2 символа, код Грея), THP; 400 Мсимв/с (5G), 200 Мсимв/с (2.5G); кадр LDPC 320 нс / 640 нс",
        "скремблер": "как 10GBASE-T (ведущий/ведомый x^58: 1 + x^39 + x^58 / 1 + x^19 + x^58)", "статус": "по принятому базовому предложению (решение №4 рабочей группы); утверждённый текст Cl.126 — IEEE GET (вход)"},
    "где применяется": "2.5GBASE-T, 5GBASE-T (NBASE-T) по Cat5e/Cat6",
    "источник": [ist(BZM, "стр. 2 (номер страницы PDF)", url_iz_manifesta(BZM), "решение №4: принятие базового предложения"), ist(BZ, "стр. 3–5 (номер страницы PDF)", url_iz_manifesta(BZ), "PCS/PMA Consensus Baseline"),
                 ist(BZS, "стр. 5, 18 (номер страницы PDF)", url_iz_manifesta(BZS), "10GBASE-T DSQ-128 + LDPC (2048,1723)")],
    "сложность внедрения": "частично: LDPC(2048,1723) есть (ldpc_std, 10gbase-t-2048-1723); раскладки кадра 2.5G/5G (97 известных нулей) нет"})
NOVYE.append(z)

# 9. 802.3cz (BASE-AU, автомобильная оптика)
CZ = "istochniki/ieee8023/cz/perezaranda_3cz_01a_1120_baseline.pdf"; CZ50 = "istochniki/ieee8023/cz/perezaranda_3cz_03_110521_50Gbps_pcs_pma.pdf"
z = Z("802.3cz 2.5G…50GBASE-AU (автомобильная оптика): RS(544,522) над GF(2^10), корни α^1…α^22, 36 слов в блоке, аддитивный скремблер x^25+x^22+1, CRC-16 PHD")
z.s(CZ, 4, "блок: 80 × 65B + 20 бит PHD → RS(544,522), 36 слов, аддитивный скремблер, PAM2", "Aggregate 80x65B blocks", "RS-FEC (544, 522) over GF(210)", "Aggregate 36 RS-FEC codewords", "Transmit block, 195840 symbols")
z.s(CZ, 7, "скремблер: регистр 25, затравка 0x02E57CA на блок, r = [r22 ⊕ r25, r1…r24]", "0x02E57CA", "r = [mod(r(22)+r(25),2) r(1:24)]")
z.s(CZ, 11, "CRC-16 PHD: (x+1)(x^15+x+1), начальное 0", "The generator polynomial is (x + 1)·(x15 + x + 1)", "initialized with the value of 0x0000")
z.s(CZ, 12, "RS: GF(2^10) x^10+x^3+1, n = 544, k = 522, t = 11, g = ∏_{j=1}^{2t}(x − α^j)", "n = 544, and k = 522", "primitive polynomial x10 + x3 + 1 (0x409)", "t = 11 symbols")
z.s(CZ50, 4, "50GBASE-AU (PAM4) — тот же RS(544,522) и 36 слов", "RS-FEC (544, 522) over GF(210)", "Aggregate 36 RS-FEC codewords")
t = " ".join(stranica(CZ, 14).split())
k = t[t.index("i gi i gi") + len("i gi i gi"):]
nums = [int(x) for x in re.findall(r"\b\d+\b", k)]
gi = {}
for j in range(0, len(nums) - 1, 2):
    if nums[j] <= 22 and nums[j] not in gi: gi[nums[j]] = nums[j + 1]
g = rs_g(10, 0x409, range(1, 23))[::-1]   # g0 … g22
z.o(f"табл. стр. 14: {len(gi)} коэффициентов g_i = свой расчёт ∏_{{j=1}}^{{22}}(x − α^j) над x^10+x^3+1", len(gi) == 23 and all(gi[i] == g[i] for i in range(23)), f"совпало {sum(gi.get(i) == g[i] for i in range(23))}/23")
z.o("отличие от KP4 RS(544,514): там корни α^0…α^29 (табл. 119–3), здесь α^1…α^22 — g различны (свой расчёт)", rs_g(10, 0x409, range(1, 23)) != rs_g(10, 0x409, range(0, 22)))
z.o("(x+1)(x^15+x+1) = x^16+x^15+x^2+1 (0x8005, многочлен CRC-16/ARC); x^25+x^22+1 примитивен (свой расчёт)", pmul(st(1, 0), st(15, 1, 0)) == st(16, 15, 2, 0) and primitiven(st(25, 22, 0)))
z.o("36 слов × 544 символа × 10 бит = 195840 бит блока (свой счёт)", 36 * 544 * 10 == 195840 and 80 * 65 + 20 == 522 * 10)
os.makedirs(os.path.join(KOREN, "tablicy"), exist_ok=True)
tab = {"описание": "коэффициенты g_0…g_22 порождающего многочлена RS(544,522) 802.3cz, g(x) = ∏_{j=1}^{22}(x − α^j) над GF(2^10), x^10 + x^3 + 1",
       "источник": {"файл": CZ, "страница": 14, "url": url_iz_manifesta(CZ)}, "сборка": "proverka/dopolnenie.py: разбор таблицы со стр. 14 + assert совпадения со своим расчётом",
       "данные": {str(i): gi[i] for i in range(23)}}
assert [tab["данные"][str(i)] for i in range(23)] == g
json.dump(tab, open(os.path.join(KOREN, "tablicy/ieee8023cz_rs544_522_g.json"), "w"), ensure_ascii=False, indent=1)
z.d.update({"семейство": "Ethernet IEEE 802.3", "область": "provod", "вид": "РС",
    "параметры": {"поле": "GF(2^10), x^10 + x^3 + 1 (0x409)", "n,k,t,m": "544, 522, 11, 10", "g(x)": "∏_{j=1}^{22}(x − α^j) (первый корень α^1!), коэффициенты → tablicy/ieee8023cz_rs544_522_g.json",
        "вход": "80 блоков 65B + 20-битный блок PHD (12 разных блоков PHD × 3 повтора на блок передачи); бит 0 блока 65B 0 → бит 0 m_{k−1}; символ передаётся с бита 0",
        "блок передачи": "36 слов RS = 195840 бит → аддитивный скремблер → PAM2 (2.5–25G) / PAM4 (50G)",
        "скремблер": "аддитивный, 25-битный регистр, обратная связь r[21] ⊕ r[24] (x^25 + x^22 + 1, как Cl.115 1000BASE-RH), затравка 0x02E57CA в начале каждого блока",
        "CRC PHD": "CRC-16 (x+1)(x^15+x+1) = x^16+x^15+x^2+1, начальное 0, по 224 битам PHD, передаётся с S15", "статус": "по базовому предложению рабочей группы (ноябрь 2020, май 2021)"},
    "где применяется": "2.5G/5G/10G/25G/50GBASE-AU — автомобильный Ethernet по многомодовому волокну OM3 (980 нм)",
    "источник": [ist(CZ, "стр. 4, 7, 11–14 (номер страницы PDF)", url_iz_manifesta(CZ), "802.3cz baseline proposal"), ist(CZ50, "стр. 4, 6 (номер страницы PDF)", url_iz_manifesta(CZ50), "50GBASE-AU baseline")],
    "сложность внедрения": "частично: слепой РС (m = 10, любой fcr) — rs_bch.py; кадр блока передачи и скремблер с затравкой — нет"})
NOVYE.append(z)

# 10. HDMI 1.4b острова данных
HD = "istochniki/interfeisy/HDMI-1.4b.pdf"; HDK = "istochniki/kod/hdl-util-hdmi/packet_assembler.sv"
z = Z("HDMI 1.0–1.4b острова данных: BCH(64,56) × 4 подпакета и BCH(32,24) заголовка, G(x) = 1 + x^6 + x^7 + x^8, кодирование TERC4")
z.s(HD, 97, "5.2.3.4: подпакет 56 бит + 8 бит чётности BCH, блок 0 — бит 0 каналов 1 и 2, 32 пикселя", "Each Subpacket includes 56 bits of data and is protected by an additional 8 bits of", "BCH ECC parity bits", "the 64 bits of BCH Block 0 are transferred over")
z.s(HD, 99, "5.2.3.5: BCH(64,56) и BCH(32,24), G(x) = 1+x^6+x^7+x^8", "BCH(64,56) and BCH(32,24) are generated by the polynomial G(x) shown in Figure 5-5", "G(x)=1+x6+x7+x8", "Packet Headers contain 24 data bits with an additional 8 bits of BCH(32,24) ECC")
z.s(HD, 96, "TERC4: 4 бита → 10-битовые символы, пакет 32 пикселя", "TMDS Error Reduction Coding (TERC4)", "Each packet is 32 pixels long and is protected by BCH ECC")
kod = open(os.path.join(KOREN, HDK)).read()
ok_kod = "8'b10000011" in kod and "next_ecc = (ecc >> 1) ^ ((ecc[0] ^ next_bch_bit) ? 8'b10000011 : 8'd0);" in kod
def ecc_hdl(bits):
    e = 0
    for b in bits: e = (e >> 1) ^ (0x83 if ((e & 1) ^ b) else 0)
    return e
G8 = st(8, 7, 6, 0)
def ecc_mn(bits):
    M = sum(b << (len(bits) - 1 - i) for i, b in enumerate(bits))   # первый бит — старший член
    r = pmod(M << 8, G8)
    return sum(((r >> (7 - k)) & 1) << k for k in range(8))         # бит k регистра = коэффициент x^(7−k)
random.seed(7)
sl = [[random.randint(0, 1) for _ in range(n)] for n in (56, 24) for _ in range(500)]
z.o("hdl-util/hdmi packet_assembler.sv (MIT OR Apache-2.0): next_ecc с 8'b10000011 — регистр младшим битом вперёд", ok_kod)
z.o("1000 случайных слов (56 и 24 бит): чётность hdl-util = остаток x^8·M(x) mod (1+x^6+x^7+x^8), бит k = коэф. x^(7−k) — открытый код соответствует многочлену спецификации", all(ecc_hdl(b) == ecc_mn(b) for b in sl))
z.o(f"период G(x) = {period(G8)} (свой расчёт; в спецификации «127 count repetition cycle»), G = (x+1)·(x^7+x^5+x^4+x^3+x^2+x+1), второй множитель примитивен", period(G8) == 127 and pmul(st(1, 0), st(7, 5, 4, 3, 2, 1, 0)) == G8 and primitiven(st(7, 5, 4, 3, 2, 1, 0)))
z.d.update({"семейство": "Интерфейсы (USB4, CPRI, JESD204, FC)", "область": "provod", "вид": "БЧХ (циклический, CRC-подобный с исправлением)",
    "параметры": {"многочлен": "G(x) = 1 + x^6 + x^7 + x^8 = (x+1)(x^7+x^5+x^4+x^3+x^2+x+1), период 127",
        "блоки": "заголовок пакета 24 бита + 8 = BCH(32,24) (бит 2 канала 0, 32 такта); 4 подпакета по 56 бит + 8 = BCH(64,56) (бит i каналов 1 и 2, по 2 бита за такт)",
        "порядок бит": "в открытой реализации регистр сдвигается младшим битом вперёд (константа 0x83 — отражение x^7+x^6+1)",
        "линейный код": "TERC4 (4 → 10 бит) в островах данных, охранные полосы 0b0100110011; видео — TMDS 8b/10b",
        "HDMI 2.1": "FRL — RS-FEC, спецификация закрыта (см. запись «HDMI 2.1 FRL RS-FEC»)"},
    "где применяется": "HDMI 1.x/2.0 TMDS: пакеты InfoFrame, аудио, ACR в периодах островов данных; DVI-совместимые источники HDMI",
    "источник": [ist(HD, "стр. 96–99 (номер страницы PDF)", url_iz_manifesta(HD), "5.2.3.4 Data Island Packet Construction, 5.2.3.5 Data Island Error Correction (копия спецификации HDMI Licensing в открытом репозитории)"),
                 ist(HDK, "строки 41–45 (next_ecc)", url_iz_manifesta(HDK), "открытая реализация ECC, лицензия MIT OR Apache-2.0 (istochniki/kod/hdl-util-hdmi/LICENSE-MIT, README.md)")],
    "сложность внедрения": "нет: в проекте нет разбора TMDS/TERC4; BCH как CRC-8 (0xC1, отражённый) считается общим crc.py"})
NOVYE.append(z)

# 11. DisplayPort 1.1a — RS(15,13) над GF(16)
DP = "istochniki/interfeisy/DP-1.1a.pdf"
z = Z("DisplayPort 1.x вторичные пакеты (SDP): RS(15,13) над GF(16) (x^4+x+1), g(x) = x^2 + α^4·x + α, перемежение полубайтов")
z.s(DP, 83, "2.2.6: заголовок 4 байта + 4 байта чётности, данные 16 байт + 4 байта чётности", "four byte header protected by four bytes of parity", "16 byte payload data protected by four bytes of parity")
z.s(DP, 83, "2.2.6.1: RS(15,13) с полубайтом, G(x) = (x − α^0)(x − α^1) = x^2 + g1·x + g0, g1 = α^4, g0 = α; α = 0010, α^4 = 0011", "RS(15,13) with a symbol size of one nibble", "where g1 = α4 and g0 = α", "gives α4 = (0, 0, 1, 1)")
z.s(DP, 84, "три примера сообщений с проверочными полубайтами", "Transmitted Message: f, e, d, c, b, a, 9, 8, 2, 2", "Transmitted Message: 9, 8, 3, 2, 1, 7, 5, 4, 8, f", "Transmitted Message: 7, 6, 5, 9, 8, 1, 3, 2, 7, 2")
pr = [[int(x, 16) for x in s.split(",")] for s in re.findall(r"Transmitted Message: ([0-9a-f](?:, [0-9a-f]){9})\b", " ".join(stranica(DP, 84).split()))]
g16 = rs_g(4, 0x13, range(2))
exp4, log4 = gf(4, 0x13)
z.o("g(x) = (x+1)(x+α) над x^4+x+1 = x^2 + α^4·x + α; α^4 = 0011 (свой расчёт)", g16 == [1, exp4[4], exp4[1]] and exp4[4] == 0b0011)
z.o(f"2.2.6.1: три примера ({len(pr)}) — свой кодер (укороченный RS, 8 информационных полубайтов) даёт те же 2 проверочных", len(pr) == 3 and all(rs_proverochnye(p[:8], g16, 4, 0x13) == p[8:] for p in pr), str([rs_proverochnye(p[:8], g16, 4, 0x13) for p in pr]))
eq_g1 = lambda c: ((c >> 3 ^ c >> 2) & 1) << 3 | ((c >> 2 ^ c >> 1) & 1) << 2 | ((c >> 3 ^ c >> 1 ^ c) & 1) << 1 | ((c >> 3 ^ c) & 1)
eq_g0 = lambda c: ((c >> 2) & 1) << 3 | ((c >> 1) & 1) << 2 | ((c >> 3 ^ c) & 1) << 1 | ((c >> 3) & 1)
z.o("логические уравнения g1·c[3:0] и g0·c[3:0] из 2.2.6.1 = умножение на α^4 и α в GF(16) для всех 16 c", all(eq_g1(c) == gmul(c, exp4[4], 4, 0x13) and eq_g0(c) == gmul(c, 2, 4, 0x13) for c in range(16)))
z.d.update({"семейство": "Интерфейсы (USB4, CPRI, JESD204, FC)", "область": "provod", "вид": "РС",
    "параметры": {"поле": "GF(16), x^4 + x + 1, α = 0010", "g(x)": "(x + α^0)(x + α^1) = x^2 + α^4·x + α (g1 = 0011, g0 = 0010)", "n,k": "15, 13 (укорочение: в примерах 8 + 2 полубайта)",
        "SDP": "заголовок HB0…HB3 + 4 байта чётности PB0…PB3; данные 16 байт + 4 байта чётности; полубайтовое перемежение по полосам (рис. 2-26…2-31)",
        "линейный код": "ANSI 8b/10b (DP 1.x); DP 2.x — 128b/132b и RS-FEC (закрыто, см. «DisplayPort 2.0/2.1 (UHBR) RS-FEC»)"},
    "где применяется": "DisplayPort 1.1–1.4: InfoFrame, аудио, служебные вторичные пакеты основного канала",
    "источник": [ist(DP, "стр. 83–87 (номер страницы PDF)", url_iz_manifesta(DP), "2.2.6 ECC for Secondary-data Packet")],
    "сложность внедрения": "частично: RS над GF(2^m) с любым fcr (m = 4) — rs_bch.py; разбора кадра DP нет"})
NOVYE.append(z)

# 12. OpenZR+ MSA
OZ = "istochniki/oif/openzrplus_rev3p0_final2.pdf"
z = Z("OpenZR+ MSA 3.0: ZR400/300/200/100-OFEC (16QAM/8QAM/QPSK) — скремблер x^16+x^12+x^3+x+1 + OFEC на расширенном БЧХ(256,239), два кодера по 3552/4096")
z.s(OZ, 38, "6.6: аддитивный скремблер 65535, x^16+x^12+x^3+x+1, сброс 0xFFFF в начале входного блока OFEC", "frame-synchronous additive scrambler of sequence 65535", "x16+ x12+ x3+ x + 1", "resets to 0xFFFF at the start of each new OFEC input block")
z.s(OZ, 39, "7: два кодера ENC0/ENC1 (чётные/нечётные биты), 3552 → 4096, N = 128, k = 239, расширенный БЧХ(256,239), d = 6", "two parallel 3552/4096 encoder engines", "extended BCH (256, 239) code", "minimum Hamming distance of 6")
z.s(OZ, 44, "7.4: формальное определение, g(t) = t^16+t^14+t^13+t^11+t^10+t^9+t^8+t^6+t^5+t+1, бит 0 — степень 254", "t16 + t14 + t13 + t11 + t10 + t9 + t8 + t6 +t5 + t +1", "with bit 0 of x being the coefficient of power 254")
gOZ = st(16, 14, 13, 11, 10, 9, 8, 6, 5, 1, 0)
gm = pmul(min_mnogochlen(1, 8, 0x11D), min_mnogochlen(3, 8, 0x11D))
z.o("g(t) OpenZR+ = m1·m3 над x^8+x^4+x^3+x^2+1 = g компонентного кода G.709.3 OFEC (свой расчёт)", gOZ == gm, poly_str(gm, "t"))
dmin = min(bin(pmul(m, gOZ)).count("1") + (bin(pmul(m, gOZ)).count("1") & 1) for m in range(1, 1 << 12))
z.o("вес ≥ 6 у расширенных слов, порождённых первыми 4095 информационными многочленами (частичная проверка d = 6)", dmin >= 6, f"мин. вес {dmin}")
z.o("скорость 111/128 = 3552/4096 (свой счёт)", 111 * 4096 == 128 * 3552)
z.d.update({"семейство": "OIF (400ZR/800ZR/800LR/1600ZR)", "область": "provod", "вид": "OFEC (пространственно-связанный блочно-свёрточный код на расширенном БЧХ)",
    "параметры": {"компонентный": "расширенный БЧХ(256,239), d = 6: первые 255 бит делятся на g(t) = t^16+t^14+t^13+t^11+t^10+t^9+t^8+t^6+t^5+t+1 (бит 0 — степень 254) + общая чётность",
        "структура": "N = 128, B = 16, G = 2 (защитные блоки); бит k слова (R, r): k < N → ((R^1) − 2G − 2N/B + 2⌊k/B⌋, ⌊k/B⌋, (k%B)^r, r), k ≥ N → (R, ⌊(k−N)/B⌋, r, (k%B)^r); вход 32 × 111 бит",
        "кодеры": "7104 бит после скремблера → ENC0 (чётные) и ENC1 (нечётные), 3552 → 4096 каждый; перемежитель OFEC 172032 бит",
        "скремблер": "аддитивный кадрово-синхронный, x^16 + x^12 + x^3 + x + 1, сброс 0xFFFF в начале входного блока OFEC (как OIF 400ZR)",
        "режимы": "ZR400-OFEC-16QAM, ZR400-OFEC-8QAM, ZR300-OFEC-8QAM, ZR200-OFEC-QPSK, ZR100-OFEC-QPSK (+ варианты HA/HB)",
        "связь": "тот же OFEC, что G.709.3 FlexO-x-DO и OpenROADM W-port"},
    "где применяется": "когерентные модули OpenZR+ QSFP-DD/OSFP 100–400G (метро/DCI), OpenROADM",
    "источник": [ist(OZ, "стр. 38 (номер страницы PDF)", url_iz_manifesta(OZ), "6.6 Frame Synchronous Scrambling"), ist(OZ, "стр. 39–44 (номер страницы PDF)", url_iz_manifesta(OZ), "7 Open Forward Error Correction (OFEC), 7.4 Formal encoder definition")],
    "сложность внедрения": "нет: OFEC (блочно-свёрточный с перемежителем) в проекте не реализован; скремблер x^16 — общими средствами"})
NOVYE.append(z)

# ======================================================================================
# Дополнения к существующим записям
OBNOV["G.709.3 FlexO-x-DO / OIF 800ZR, 1600ZR: OFEC"] = {
    "источник+": [ist(OZ, "стр. 44 (номер страницы PDF)", url_iz_manifesta(OZ), "второй источник: OpenZR+ MSA 3.0, 7.4 — тот же g(t) и структура слова")],
    "проверка+": "проверяющий: g(t) из OpenZR+ 7.4 = m1·m3 над 0x11D (свой расчёт) — второй независимый первоисточник подтвердил компонентный код"}
PC = "istochniki/interfeisy/206_DDasSharma_PCIe6.pdf"; PAT = "istochniki/interfeisy/US10997111B2_google.html"
zz = Z("PCI Express 6.0/7.0 FEC (FLIT, 3-way interleaved RS + CRC)")
zz.s(PC, 10, "презентация автора спецификации: флит 256 Б = 236 TLP + 6 DLP + 8 CRC + 6 FEC; FEC — 3 перемеженных кода с исправлением одного символа; CRC на основе РС", "236B TLP, 6B DLP, 8B CRC, 6B FEC", "FEC: 3-way interleaved, single symbol correct", "CRC: RS based")
pt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", open(os.path.join(KOREN, PAT), encoding="utf-8", errors="replace").read()))
zz.o("патент US10997111B2 (Intel, «Flit-based packetization»): три перемеженные группы, пары check/parity символов, 8 символов CRC на флит", all(s in pt for s in ("three-way interleaving", "8 flit-level CRC symbols", "check and parity symbols")))
OBNOV["PCI Express 6.0/7.0 FEC"] = {
    "семейство": "Интерфейсы (USB4, CPRI, JESD204, FC)", "вид": "РС (перемеженный) + CRC — частично (структура без многочленов)",
    "параметры": {"флит (Flit Mode, PCIe 6.0 64 GT/s PAM4)": "256 байт = 236 TLP + 6 DLP + 8 CRC + 6 FEC", "FEC": "3 перемеженных кода по 2 байта (check + parity), исправление одного символа — пачка до 3 байт; задержка < 2 нс",
        "CRC": "8 байт, «на основе РС», гарантированное обнаружение до 8 ошибочных байт, далее 2^−64", "ранний вариант (патент)": "флиты 324/648 символов с тройным перемежением и парами check/parity по полосам",
        "не найдено": "многочлены поля и порождающие многочлены FEC/CRC — только в спецификации PCI-SIG (для членов)"},
    "где применяется": "PCIe 6.0/7.0 (Flit Mode), CXL 3.x", "источник": [ist(PC, "стр. 10 (номер страницы PDF)", url_iz_manifesta(PC), "Das Sharma, PCIe 6.0 (OpenFabrics 2022): Flit Mode"),
        ist(PAT, "описание, фиг. 5–6", url_iz_manifesta(PAT), "US10997111B2 Flit-based packetization (Intel)"), ist("istochniki/interfeisy/US10997111B2.pdf", "весь документ (скан)", url_iz_manifesta("istochniki/interfeisy/US10997111B2.pdf"), "PDF патента USPTO")],
    "проверка": "структура флита — по презентации автора спецификации и патенту (две независимые формы); многочлены не опубликованы — проверка кода невозможна",
    "сложность внедрения": "нет: без многочленов — только слепой поиск РС по 2-байтовым проверкам (rs_bch.py) при известной раскладке флита"}
OBNOV["OpenROADM MSA W-port"] = {"параметры+": {"замена": "OFEC W-порта OpenROADM описан в открытой OpenZR+ MSA 3.0 (стр. 39–44) — см. запись OpenZR+"},
    "источник": [ist(OZ, "стр. 39–44 (номер страницы PDF)", url_iz_manifesta(OZ), "открытая замена: OFEC в OpenZR+ MSA 3.0")]}
for imya, f, u, chto in (("DisplayPort 2.0/2.1", "istochniki/ne_naydeno/vesa_dp.html", "https://vesa.org/displayport-developer/about-displayport/", "VESA: спецификации — для членов (Member Login)"),
                         ("HDMI 2.1 FRL", "istochniki/ne_naydeno/hdmi21.html", "https://www.hdmi.org/spec/hdmi2_1", "HDMI Forum: Adopter-Only Extranet"),
                         ("InfiniBand", "istochniki/ne_naydeno/ibta.html", "https://www.infinibandta.org/ibta-specification/", "IBTA: «MEMBERS … login required»"),
                         ("SAS-4/SAS-5", "istochniki/ne_naydeno/t10.html", "https://www.t10.org/drafts.htm", "T10: «Only T10 members are permitted to access this document»")):
    OBNOV[imya] = {"источник": [ist(f, "снимок страницы", u, chto)]}
for imya, f, obr in (("DisplayPort 2.0/2.1", "istochniki/ne_naydeno/vesa_dp.html", "Member Login"), ("HDMI 2.1 FRL", "istochniki/ne_naydeno/hdmi21.html", "Adopter-Only Extranet"),
                     ("InfiniBand", "istochniki/ne_naydeno/ibta.html", "login required"), ("SAS-4/SAS-5", "istochniki/ne_naydeno/t10.html", "Only T10 members are permitted to access this document")):
    ok(imya, f"снимок страницы {f.split('/')[-1]}: «{obr}»", obr in open(os.path.join(KOREN, f), encoding="utf-8", errors="replace").read())


# источники для записей «не найдено», где есть открытый документ, фиксирующий отсутствие значений
S4 = "istochniki/ieee8023/standart/802.3-2012_section4.pdf"; G9751 = "istochniki/itu/T-REC-G.975.1-200402-I.pdf"; BD = "istochniki/nositeli/US20060282614A1.pdf"
str_ok({"имя": "IEEE 802.3: утверждённые тексты"}, S4, 729, "Annex 55A: G.txt/H.txt — только в matrices.zip на standards.ieee.org/downloads/802.3/", "G.txt is available online in the file", "matrices.zip")
str_ok({"имя": "G.975.1 I.6 LDPC — значения наклонов"}, G9751, 31, "I.6.2: код задан выбором семи наклонов s1…s7, значения не приведены", "by selecting seven different slopes s1, ... , s7")
OBNOV["IEEE 802.3: утверждённые тексты"] = {"источник": [ist(S4, "стр. 729 (номер страницы PDF)", url_iz_manifesta(S4), "Annex 55A: матрицы G/H 10GBASE-T вынесены в matrices.zip (standards.ieee.org/downloads/802.3/) — архив недоступен"),
    ist("istochniki/ieee8023/bs/gustlin_3bs_03_0317.pdf", "стр. 15–17 (номер страницы PDF)", url_iz_manifesta("istochniki/ieee8023/bs/gustlin_3bs_03_0317.pdf"), "вместо утверждённого текста использованы черновики/презентации (здесь — P802.3bs D3.1, Cl.119)")]}
OBNOV["G.975.1 I.6 LDPC — значения наклонов"] = {"источник": [ist(G9751, "стр. 31 (номер страницы PDF)", url_iz_manifesta(G9751), "I.6.2: определение через 7 наклонов без их значений")]}
OBNOV["Blu-ray: многочлены LDC/BIS"] = {"источник": [ist(BD, "стр. 8 (номер страницы PDF)", url_iz_manifesta(BD), "патент: структура LDC/BIS без порождающих многочленов")]}

for z in NOVYE:
    z.d["проверка"] = "проверяющий (proverka/dopolnenie.py): " + "; ".join(PROV.get(z.imya, [])) + "; формулировки сверены со страницами источников (proverka/zhurnal.json)"
json.dump([z.d for z in NOVYE], open(os.path.join(KOREN, "proverka/novye.json"), "w"), ensure_ascii=False, indent=1)
json.dump(OBNOV, open(os.path.join(KOREN, "proverka/obnovleniya.json"), "w"), ensure_ascii=False, indent=1)
print("новых записей:", len(NOVYE), "обновлений:", len(OBNOV))
