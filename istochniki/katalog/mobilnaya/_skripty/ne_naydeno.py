"""Что искали и не нашли в открытом доступе (определения кодов не приводятся — по памяти нельзя). У каждой записи — где искали и что
найдено косвенно (документы, подтверждающие закрытость/платность или только описательный уровень)."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from obshchee import *

ZAPISI = []
def net(imya, sem, gde, ist, iskali, chastichno="нет"):
    ZAPISI.append({"имя": imya, "семейство": sem, "область": "mobilnaya", "вид": "не найдено в открытом доступе",
                   "параметры": {"статус": "определение кода в открытых первоисточниках не найдено", "где искали": iskali},
                   "где применяется": gde, "источник": ist, "проверка": "—", "сложность внедрения": chastichno})

FM = Tekst("istochniki/archive_org/fm-6-02.72-tactical-radios-2002.pdf")
C220 = Tekst("istochniki/mil/MIL-STD-188-220D.024816.pdf")
C110 = Tekst("istochniki/mil/MIL-STD-188_110C.037889.PDF")
JO = Tekst("istochniki/mil/HF_modems_explained_stanag_Jorgenson.pdf")
DA = Tekst("istochniki/archive_org/DTIC_ADA364390.pdf")
N184 = Tekst("istochniki/mil/MIL-STD-188_184_NOTICE-3.052208.pdf")
F85 = Tekst("istochniki/mil/udxf_Some-Notes-on-STANAG4285.pdf")

net("iDEN (Motorola): канальное кодирование M16QAM 25 кГц, 6 слотов", "iDEN (Motorola)", "транковые сети Nextel/Telus/Southern LINC (закрыты к 2013–2020)",
    [{"файл": None, "страница|строки": "", "url": "https://gophertrunk.org/reference/iden/", "что": "только описание (M16QAM, 6 слотов), кодов нет"}],
    "спецификации Motorola iDEN закрыты; поиск: manualslib, patents.google.com (адаптивное FEC по EVM US6611795 — не спецификация iDEN), sigidwiki, GitHub (открытых декодеров iDEN нет)")
net("Link-22 (NILE, STANAG 5522): ВЧ/УКВ волновые формы и FEC", "НАТО: Link-22", "тактический обмен данными ВМС НАТО",
    [JO.ist(JO.odna(r"oped for NILE/Link-22")[0], "упоминание новых волновых форм NILE/Link-22")],
    "STANAG 5522 — ограниченного распространения; обзоры (Йоргенсон) без параметров кода")
net("HAVE QUICK I/II (УВЧ ППРЧ авиации): ECCM, TOD/WOD, кодирование", "Военная УВЧ: HAVE QUICK", "авиационная УВЧ связь 225–400 МГц",
    [FM.ist(FM.odna(r"AFP also allows the entry of Have Quick")[0], "только упоминание загрузки данных HQ"), C220.ist(C220.odna(r"HAVEQUICK II R/T interface")[0], "188-220D: интерфейс данных поверх HQ II")],
    "параметры ППРЧ и WOD засекречены; открыт только стык данных MIL-STD-188-220 (см. запись 188-220D)", "частично: данные поверх HQ II — MIL-STD-188-220D (есть запись)")
net("SINCGARS: речь/ППРЧ ECCM (скачки, TRANSEC)", "Военная УКВ: SINCGARS", "армейская УКВ 30–88 МГц",
    [FM.ist(FM.odna(r"The SINCGARS can operate in either the SC or frequency hop \(FH\) mode")[0], "режимы SC/FH — описательно"), FM.ist(FM.odna(r"A TSK must be loaded into the SINCGARS radio prior to opening an FH net")[0], "TSK")],
    "ППРЧ/TRANSEC засекречены; открыт уровень данных — MIL-STD-188-220D (Голей (24,12) + TDC, есть запись)", "частично: данные SINCGARS — запись MIL-STD-188-220D")
net("MIL-STD-188-148 (ППРЧ ВЧ, волновая форма)", "MIL-STD-188-110 (HF модем)", "ВЧ ППРЧ",
    [C110.ist(C110.odna(r"See MIL-STD-188-148 \(S\)")[0], "110C 5.3.3: «(S)» — секретный")], "MIL-STD-188-148 имеет гриф (S), см. ссылку в 110C 5.3.3")
net("MIL-STD-188-182 (УВЧ DAMA 5 кГц) и MIL-STD-188-183 (УВЧ DAMA 25 кГц): волновые формы и FEC", "MIL-STD-188-181 (УВЧ SATCOM)", "УВЧ спутниковая связь DAMA (UFO/MUOS legacy)",
    [DA.ist(DA.odna(r"over UHF satellite channels are MIL-STD-188-182A")[0], "обзор DTIC: назначение 182A/183A, без параметров кода"),
     {"файл": None, "страница|строки": "", "url": "https://standards.globalspec.com/std/9872408/npfc-mil-std-188-183", "что": "188-183B «CONT. DIST.» — ограниченное распространение"}],
    "everyspec (нет), quicksearch.dla.mil (контролируемое распространение), DTIC через archive.org (ADA364390, ADA364468 — только протокольный уровень)",
    "частично: одиночный доступ 5/25 кГц — MIL-STD-188-181A (есть запись)")
net("MIL-STD-188-184 (волновая форма управления данными УВЧ SATCOM, адаптивное FEC)", "MIL-STD-188-181 (УВЧ SATCOM)", "УВЧ SATCOM передача длинных сообщений",
    [N184.ist(N184.odna(r"Interoperability and Performance Standard for the Data Control")[0], "только извещение о подтверждении (Notice 3, 2007), без текста стандарта")],
    "everyspec — только Notice 3; основной текст 2002 г. не найден")
net("STANAG 4285 прил. E: таблицы перемежителя (32 строки) и синхронизации перемежителя — полный текст", "НАТО: STANAG 4285 / 4481", "ВЧ связь НАТО",
    [F85.ist(1, "обзор UDXF — без полных таблиц")],
    "текст STANAG 4285 в открытом доступе не найден; кадр, синхро и скремблер восстановлены по STANAG 4529 (процедуры соответствия), многочлены K=7 — по 5066 прил. G (есть записи)",
    "частично: записи STANAG 4285/4481, 4529, 5066 прил. G")
PT = Tekst("istochniki/prochee/US6477680_patent.pdf", ocr=True)
net("D-AMPS (IS-54/IS-136, TIA/EIA-136): многочлены свёрточного кода, CRC-7, перемежение", "D-AMPS (TIA/EIA-136)", "сотовые сети TDMA США (закрыты к 2008)",
    [PT.ist(PT.odna(r"IS-136 \(TDMA\) and GSM standards are 6 and 5")[0], "патент US 6,477,680 (OCR): IS-136 — длина кодового ограничения 6, без многочленов"),
     {"файл": None, "страница|строки": "", "url": "https://global.ihs.com", "что": "TIA/EIA-136 — платный"}],
    "TIA/EIA-136 и IS-54 продаются TIA/IHS; archive.org, sigidwiki, GitHub — без текста; найдено только K = 6 (патент US 6,477,680)",
    "нет; известна только длина кодового ограничения K = 6")
net("NMT: полный текст NMT Doc. 450-1/900-1 (формат сигнализации, кадр 166 бит)", "Аналоговые сотовые (1G): NMT / Radiocom 2000", "сети NMT",
    [{"файл": "istochniki/analog/NMT450_Technical_Specification_BASE_1981.pdf", "страница|строки": "весь документ", "url": url("istochniki/analog/NMT450_Technical_Specification_BASE_1981.pdf"),
      "что": "найдена только спецификация БС (радиочасть, испытания), без формата сигнализации"}],
    "ETHW (найдена спецификация БС 1981), archive.org, sigidwiki; код Хагельбаргера и кадр — по osmocom-analog и ITU-R M.742-4 (есть запись)",
    "частично: запись «NMT-450/900 и Radiocom 2000: Хагельбаргер (6,19)»")
