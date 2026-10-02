"""Интерфейсы: USB4 (RS(198,194) Gen2/3, RS(504,480) Gen4 — спецификация usb.org, бесплатная), CPRI 7.0 (RS(528,514) + PN-5280),
JESD204C (только руководство AMD PG242 — код FEC в нём не определён), Fibre Channel (по ссылке CPRI на FC-FS-4).
Проверки: порождающие многочлены USB4 (все 4 и 25 коэффициентов), примеры кодовых блоков приложения A (4 для Gen2/3, 3 для Gen4)."""
import os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *
from kody import *

ZAPISI = []
SEM = "Интерфейсы (USB4, CPRI, JESD204, FC)"


def rec(imya, vid, par, gde, ist, prov, sl, sem=SEM):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "provod", "вид": vid, "параметры": par, "где применяется": gde,
                   "источник": ist, "проверка": prov, "сложность внедрения": sl})


U = Tekst("istochniki/interfeisy/USB4_Specification_November_2025/USB4 Specification November 2025/USB4 Specification 2.0 November 2025 - CLEAN.pdf")
s1 = U.gde("The generating polynomial is g(x) = X^4 + 15X^3 + 54X^2 + 120X + 64")
assert rs_g(0x11D, 0, 4) == [1, 15, 54, 120, 64]
prim = []
for n in (5, 6, 7, 8):
    k, s = U.kusok(rf"Table A-{n}\.\s+Example \d – RS-FEC Block \(Gen 2/Gen 3\)\s*\n", rf"Table A-{n + 1}\.|A\.\d+\.\d+ ", posl=True)
    ch = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", k)]
    d = dict(zip(ch[0::2], ch[1::2])); assert sorted(d) == list(range(198))
    assert not any(rs_sindromy([d[i] for i in range(198)], 0x11D, 0, 4)); prim.append(s)
p11 = iz_stepenej([11, 2, 0])
k4, s4 = U.kusok(r"The RS generator polynomial \(t=12\)", r"The RS-FEC encoder generates 24 Symbols")
g_txt = [int(x) for x in re.search(r"𝑔= \[([\d,\s]+)\]", k4).group(1).replace(",", " ").split()]
assert g_txt == rs_g(p11, 0, 24)
prim4 = []
for n, dan in ((9, [i + 1 for i in range(480)]), (10, [0] * 479 + [1]), (11, [480 - i for i in range(480)])):
    k, s = U.kusok(rf"Table A-{n}\.\s+Example \d – RS-FEC Block \(Gen 4\)\s*\n", rf"Table A-{n + 1}\.|=====СТР 81[23]", posl=True)
    ch = [int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*$", k)]
    d = dict(zip(ch[0::2], ch[1::2]))
    assert [d[i] for i in range(480, 504)] == rs_kodirovat(dan, p11, 0, 24), n
    prim4.append(s)
rec("USB4 Gen 2/Gen 3: RS(198,194) над GF(256), g(x) = x^4 + 15x^3 + 54x^2 + 120x + 64 (корни α^0…α^3) + прекодер", "РС",
    {"поле": "x^8 + x^4 + x^3 + x^2 + 1", "блок": "192 байта (12 символов по 16 байт) + 2 байта Sync Bits + 4 проверочных", "прекодер": "превращает пачку ошибок в 2 ошибки на краях",
     "порядок": "байт 0 первым, старший бит первым", "скремблер": "см. 4.3.1.x (сид по SCR упорядоченного набора)"},
    "USB4 / Thunderbolt 3–4 (20/40 Гбит/с)", [U.ist(s1, "4.3.1.6"), U.ist(U.gde("The FEC scheme is based on Reed-Solomon RS(198,194) over GF(28) code with two correctable"), "3.1.1")] + [U.ist(s, f"прил. A, пример") for s in prim[:1]],
    "g(x) вычислен (fcr = 0) и совпал с текстом; все 4 примера табл. A-5…A-8 — нулевые синдромы", "частично: RS GF(256) (rs_bch.py); кадр USB4 — нет")
rec("USB4 Gen 4: RS(504,480) над GF(2^11) x^11 + x^2 + 1, t = 12 (корни α^0…α^23)", "РС",
    {"g": "25 коэффициентов [1, 1984, 701, …, 1455] (4.3.2.3)", "блок": "480 символов (660 байт) + 24 проверочных (33 байта), P23 — первый", "модуляция": "PAM3 (Gen 4)"},
    "USB4 v2 (80 Гбит/с)", [U.ist(s4, "4.3.2.3")] + [U.ist(prim4[0], "прил. A табл. A-9…A-11")],
    "все 25 коэффициентов g из текста совпали с вычисленными; примеры A-9, A-10, A-11 (1…480, единица в конце, 480…1) — проверочные символы совпали", "частично: слепой РС (m = 11)")

C = Tekst("istochniki/interfeisy/CPRI_v_7_0_2015-10-09.pdf")
sC = C.gde("The RS- FEC follows Fibre Channel FC-FS-4 [34], section 5.4")
sC2 = C.gde("scrambled with a fixed PN sequence of 5280 elements")
rec("CPRI 7.0 (опц. для 24G, 64B/66B): RS-FEC = RS(528,514) + транскодирование 256B/257B + ПСП PN-5280 (по FC-FS-4 5.4)", "РС",
    {"код": "RS(528,514) как IEEE 802.3 Cl.91", "транскодирование": "4 × 64B/66B → 257 бит, 20 блоков = 514 символов", "скремблер": "фиксированная ПСП 5280 бит после кодера",
     "пример": "раздел 6.10 (информативный) — полный пример гиперкадра (не сверялся)"},
    "CPRI (фронтхол сотовых БС), линия 10: 24.33 Гбит/с", [C.ist(sC, "6.9"), C.ist(sC2, "6.9.1"), C.ist(C.gde("RS-FEC Coding Example (Informative)"), "6.10")],
    "код — Cl.91 (проверен в семействе Ethernet); пример 6.10 не пересчитывался", "нет")
rec("Fibre Channel 32GFC/64GFC RS-FEC (FC-FS-4 5.4) и 16GFC FireCode", "РС / циклический",
    {"по ссылке": "CPRI 7.0 6.9: «RS-FEC follows Fibre Channel FC-FS-4 section 5.4» — RS(528,514) + PN-5280", "примечание": "сами FC-FS-4/FC-FS-5 (T11/INCITS) в открытом доступе не найдены (t11.org недоступен)"},
    "Fibre Channel", [C.ist(sC, "ссылка на FC-FS-4")], "косвенно — через CPRI; первичный текст FC не получен", "нет")
J = Tekst("istochniki/interfeisy/pg242-jesd204c_v4_2.pdf")
rec("JESD204C: FEC 64B/66B (укороченный циклический код) — определение в JEDEC JESD204C (закрыт)", "не найдено в открытом доступе",
    {"что есть": "руководство AMD PG242: режимы «CRC-12 / CMD / FEC» мультиблока 64B66B, счётчики исправленных/неисправленных; параметров кода нет"},
    "АЦП/ЦАП ↔ ПЛИС (JESD204C)", [J.ist(J.gde("Supports FEC Encoding (TX) and Decoding (RX) on the 64B66B link layer"), "PG242")],
    "код не определён в открытых источниках", "нет")

# ---- не найдено ----
NN = [("PCI Express 6.0/7.0 FEC (FLIT, 3-way interleaved RS + CRC)", "спецификация PCI-SIG — только для членов; открытых первоисточников с параметрами кода не найдено"),
      ("DisplayPort 2.0/2.1 (UHBR) RS-FEC", "спецификация VESA — только для членов"),
      ("HDMI 2.1 FRL RS-FEC", "спецификация HDMI Forum — по лицензии"),
      ("InfiniBand EDR/HDR/NDR FEC", "спецификация IBTA — для членов (коды совпадают с 802.3 KR4/KP4 по открытым презентациям, но первоисточника нет)"),
      ("SAS-4/SAS-5 RS-FEC, SATA", "T10 SAS-4 — черновики закрыты; SATA FEC не использует"),
      ("HDD (LDPC/RS в каналах чтения) и SSD NAND (BCH/LDPC)", "коды производителей (Marvell, Broadcom, Seagate, WD) не публикуются"),
      ("Blu-ray: многочлены LDC/BIS", "спецификация BDA закрыта; открыт только патент со структурой (запись Blu-ray)"),
      ("IEEE 802.3: утверждённые тексты Cl.91/108/119/134/161/97/149/165/177 и файл G.txt 10GBASE-T", "IEEE GET требует входа; копии 802.3-2012 есть (зеркало), новых редакций — нет; использованы презентации и черновые выдержки"),
      ("OpenROADM MSA W-port Digital Specification (oFEC)", "openroadm.org — форма регистрации; тот же OFEC есть в ITU-T G.709.3 (2024) — использован он"),
      ("IEEE 1901.2 / IEEE 1901 (HD-PLC wavelet)", "стандарты IEEE платные; G3-PLC (G.9903) и HomePlug AV (ETSI docbox) — открытые эквиваленты использованы"),
      ("MoCA 1.x/2.x", "спецификации MoCA — для членов"),
      ("G.975.1 I.6 LDPC — значения наклонов s1…s7", "в G.975.1 (2004) и поправках 1, 2 не приведены")]
for imya, pochemu in NN:
    ZAPISI.append({"имя": imya, "семейство": "Не найдено в открытом доступе", "область": "provod", "вид": "не найдено в открытом доступе",
                   "параметры": {"причина": pochemu}, "где применяется": "", "источник": [], "проверка": "—", "сложность внедрения": "—"})

if __name__ == "__main__":
    print(len(ZAPISI), "записей")
