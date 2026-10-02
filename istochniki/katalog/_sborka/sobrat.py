"""Сборка единого каталога кодов из пяти областей в репозиторий.

Запуск: python3 sobrat.py КАТАЛОГ_ОБЛАСТЕЙ РАБОЧАЯ_КОПИЯ [--polozhit]
  КАТАЛОГ_ОБЛАСТЕЙ — папка с подпапками kosmos/mobilnaya/efir/provod/obshchie
  (в каждой katalog.json, spisok_faylov.json, istochniki/, tablicy/, skripty/…);
  --polozhit — разложить файлы (жёсткими ссылками), иначе только посчитать.
Выбор первоисточников, которые кладутся в репозиторий (остальные — ссылкой), — chosen.json
(sha256), его строит vybor.py по бюджету места.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

ОБЛАСТИ = ["kosmos", "mobilnaya", "efir", "provod", "obshchie"]
НАЗВАНИЯ = {
    "kosmos": "Космическая и спутниковая связь, навигация, метеоспутники, любительские КА",
    "mobilnaya": "Подвижная радиосвязь: сотовая, ПМР и транкинг, военная, авиация, морская, пейджинг",
    "efir": "Эфирное вещание (ТВ, радио) и беспроводные сети (Wi-Fi, Bluetooth, IoT, WiMAX, UWB)",
    "provod": "Проводные и оптические линии, PON, xDSL, кабель, PLC, модемы V, носители, штрихкоды, интерфейсы",
    "obshchie": "Общие коды и таблицы: БЧХ, свёрточные, CRC, LDPC, РМ, Голей, примитивные многочлены, скремблеры",
}
ПРЕДЕЛ_БАЙТ = 40_000_000  # файлы больше 40 МБ — только ссылкой
ДАННЫЕ = "src/reportgen/potok/data"
ТАБЛИЦЫ = f"{ДАННЫЕ}/kody"
ИСТ = "istochniki/katalog"

K, W = sys.argv[1], sys.argv[2]
ПОЛОЖИТЬ = "--polozhit" in sys.argv
ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
chosen = set(json.load(open(os.path.join(ЗДЕСЬ, "chosen.json"))))
_кэш = {}
if os.path.exists(os.path.join(ЗДЕСЬ, "all_sha.json")):
    for _h, (_sz, _locs) in json.load(open(os.path.join(ЗДЕСЬ, "all_sha.json"))).items():
        for _a, _r in _locs:
            _кэш[f"{K}/{_a}/{_r}"] = (_h, _sz)


def sha(p: str) -> str:
    if p in _кэш and _кэш[p][1] == os.path.getsize(p):
        return _кэш[p][0]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---------- отображение путей области в пути репозитория ----------

def в_репо(обл: str, путь: str) -> str | None:
    """Путь внутри папки области → путь в репозитории (None — не наш путь)."""
    if путь.startswith("../"):
        хвост = путь[3:]
        if "/" not in хвост:
            return None
        обл2, путь = хвост.split("/", 1)
        if обл2 not in ОБЛАСТИ:
            return None
        обл = обл2
    корень, _, хвост = путь.partition("/")
    if корень == "istochniki":
        return f"{ИСТ}/{обл}/{хвост}"
    if корень == "tablicy":
        if os.path.basename(хвост).startswith("_"):
            return f"{ИСТ}/{обл}/_proverka/tablicy/{хвост}"
        return f"{ТАБЛИЦЫ}/{обл}/{хвост}"
    if корень in ("risunki", "tekst", "skripty", "proverka"):
        return f"{ИСТ}/{обл}/_{корень}/{хвост}"
    return None


def обл_и_путь(обл: str, путь: str) -> tuple[str, str]:
    if путь.startswith("../"):
        обл2, путь = путь[3:].split("/", 1)
        return обл2, путь
    return обл, путь


ПУТЬ_РЕ = re.compile(
    r"(?<![\w/.\-])((?:\.\./(?:kosmos|mobilnaya|efir|provod|obshchie)/)?"
    r"(?:istochniki|tablicy|risunki|tekst|skripty|proverka)/[^\s,;()«»\"'\[\]{}]+)")
нераспознанные: Counter = Counter()
упомянутые_тексты: set[tuple[str, str]] = set()


def заменить_в_строке(обл: str, s: str) -> str:
    def зам(m: re.Match) -> str:
        p = m.group(1)
        хвостик = ""
        for _ in range(3):
            o2, p2 = обл_и_путь(обл, p)
            if os.path.exists(f"{K}/{o2}/{p2}"):
                break
            if p and p[-1] in ".:…":
                хвостик = p[-1] + хвостик
                p = p[:-1]
        o2, p2 = обл_и_путь(обл, p)
        if not os.path.exists(f"{K}/{o2}/{p2}") and any(c in m.group(1) for c in "*<…") \
                and os.path.isdir(os.path.dirname(f"{K}/{o2}/{p2}")):
            return в_репо(обл, m.group(1))
        if not os.path.exists(f"{K}/{o2}/{p2}") and m.group(1).endswith("_") \
                and os.path.isdir(os.path.dirname(f"{K}/{o2}/{p2}")):
            return в_репо(обл, m.group(1))
        if not os.path.exists(f"{K}/{o2}/{p2}"):
            нераспознанные[(обл, m.group(1))] += 1
            return m.group(0)
        if p2.startswith(("tekst/", "skripty/", "proverka/", "risunki/")) and os.path.isfile(f"{K}/{o2}/{p2}"):
            упомянутые_тексты.add((o2, p2))
        return в_репо(обл, p) + хвостик
    return ПУТЬ_РЕ.sub(зам, s)


def заменить(обл: str, x):
    if isinstance(x, str):
        return заменить_в_строке(обл, x)
    if isinstance(x, list):
        return [заменить(обл, y) for y in x]
    if isinstance(x, dict):
        return {k: заменить(обл, v) for k, v in x.items()}
    return x


# ---------- классы и признак «в проекте» ----------

def класс(вид: str, имя: str) -> str:
    v = вид.lower().replace("ё", "е")
    if "не найдено" in v:
        return "не найдено"
    if v.startswith("таблица (примитив"):
        return "примитивный многочлен"
    if v.startswith("справочно") or v.startswith("таблица"):
        return "справочно"
    if "каскад" in v or "+" in v or "произведение" in v or "ofec" in v or "лестнич" in v:
        return "каскадный"
    if "дуобинар" in v:
        return "турбо дуобинарный"
    if "sccc" in v:
        return "турбо SCCC"
    if "турбо" in v or "pccc" in v:
        return "турбо PCCC"
    if "ткб" in v or "tpc" in v:
        return "ТКБ"
    if "ldpc" in v or re.search(r"\b(ira|ara|ra)\b", v) or "повторение–перемежение" in v:
        return "LDPC"
    if "raptor" in v or "фонтан" in v:
        return "фонтанный"
    if "tcm" in v or "ткм" in v or "решетчат" in v:
        return "ТКМ"
    if "сверт" in v or "рекурсивн" in v:
        return "свёрточный"
    if re.search(r"(^|[\s(])рс\b", v) or "рида — соломона" in v or "рида-соломона" in v:
        return "РС"
    if "бчх" in v or "bch" in v:
        return "БЧХ"
    if "голей" in v:
        return "Голей"
    if re.search(r"(^|[\s(])рм\b", v) or "маллер" in v:
        return "РМ"
    if "полярн" in v:
        return "полярный"
    if "хэмминг" in v or "хсяо" in v or "sec-ded" in v:
        return "Хэмминг"
    if "crc" in v or "контрольн" in v or "четност" in v or "файр" in v or "контрольная цифра" in v:
        return "CRC-подобный"
    if "скрембл" in v or "рандомиз" in v or "псп" in v or "отбелив" in v or "m-последоват" in v:
        return "скремблер/ПСП"
    if "синхро" in v or "преамбул" in v:
        return "синхрослово"
    if "перемеж" in v:
        return "перемежитель"
    if "примитивн" in v:
        return "примитивный многочлен"
    if "гопп" in v:
        return "Гоппа"
    if "стира" in v or "вандермонд" in v or "коши" in v or "raid" in v:
        return "код стирания"
    if "циклическ" in v or "qr" in v or "квадратично" in v:
        return "циклический"
    if "рс" in v.split():
        return "РС"
    if "случайный линейный" in v:
        return "код стирания"
    if "блочн" in v or "симплекс" in v or "повторение" in v:
        return "блочный линейный"
    return "прочее"


def в_проекте(сложн: str, кл: str) -> str:
    if кл == "не найдено":
        return "не найдено"
    s = сложн.strip().lower()
    if not s:
        return "нет"
    if s.startswith("не относится"):
        return "справочно"
    if "справочно" in s[:30] and s.startswith("нет"):
        return "справочно"
    if s.startswith("нет"):
        return "нет"
    if s.startswith("частично") or "частично" in s[:30]:
        return "частично"
    if s.startswith("есть"):
        return "есть"
    return "частично"


РАНГ = {"есть": 3, "частично": 2, "нет": 1, "справочно": 0, "не найдено": 0}

# ---------- дубли (одна и та же схема одного стандарта описана двумя областями) ----------
# (основная, присоединяемая) — по ручному разбору кандидатов (dubli.py и поиск по ключевым словам)
СЛИЯНИЯ = [
    (("provod", 82), ("obshchie", 934)),   # PDF417 РС над GF(929)
    (("efir", 15), ("provod", 52)),        # J.83 Annex B RS(128,122)
    (("mobilnaya", 171), ("efir", 106)),   # ПСП Голда длины 31 3GPP
    (("provod", 79), ("obshchie", 929)),   # Data Matrix ECC200 РС 0x12D
    (("provod", 70), ("obshchie", 947)),   # CIRC CD
    (("provod", 71), ("obshchie", 948)),   # RSPC CD-ROM
    (("provod", 71), ("obshchie", 949)),   # EDC CD-ROM
    (("provod", 71), ("obshchie", 950)),   # скремблер CD-ROM
    (("provod", 72), ("obshchie", 951)),   # RS-PC DVD
    (("provod", 3), ("obshchie", 0)),      # LDPC 10GBASE-T (матрица AFF3CT)
    (("mobilnaya", 177), ("obshchie", 954)),  # JT65 РС(63,12)
    (("mobilnaya", 25), ("obshchie", 804)),   # полярный 5G NR, последовательность Q
    (("mobilnaya", 102), ("obshchie", 793)),  # 3G-ALE BW1
    (("mobilnaya", 103), ("obshchie", 794)),  # 3G-ALE BW2
    (("mobilnaya", 104), ("obshchie", 795)),  # 3G-ALE BW3
    (("mobilnaya", 106), ("obshchie", 796)),  # 3G-ALE CRC-32
    (("efir", 0), ("kosmos", 27)),         # РС(204,188)+рандомизатор+Форни: DVB-T = DVB-S
    (("efir", 1), ("kosmos", 28)),         # 171/133 с выкалыванием 1/2…7/8: DVB-T = DVB-S
]
# связанные, но не тождественные записи
СМ_ТАКЖЕ = [
    (("efir", 1), ("efir", 42)),
    (("efir", 127), ("obshchie", 432)),
    (("obshchie", 494), ("mobilnaya", 100)),
    (("obshchie", 1), ("provod", 3)),
    (("provod", 80), ("obshchie", 930)), (("provod", 80), ("obshchie", 931)),
    (("provod", 80), ("obshchie", 932)), (("provod", 80), ("obshchie", 933)),
    (("provod", 81), ("obshchie", 931)),
    (("provod", 72), ("obshchie", 952)), (("provod", 72), ("obshchie", 953)),
    (("provod", 103), ("obshchie", 343)),
    (("efir", 127), ("provod", 101)),
]


def ид(обл: str, имя: str) -> str:
    return f"{обл}-{hashlib.sha1(имя.encode()).hexdigest()[:8]}"


ОБЯЗАТЕЛЬНЫЕ = ["имя", "семейство", "область", "вид", "параметры", "где применяется", "источник",
                "проверка", "сложность внедрения"]

# ---------- файлы областей ----------
файлы: dict[str, dict] = {}   # путь в репо → сведения
for обл in ОБЛАСТИ:
    спис = {x["путь"]: x for x in json.load(open(f"{K}/{обл}/spisok_faylov.json"))}
    for корень in ("istochniki", "risunki", "skripty", "proverka", "tablicy"):
        for r, _, fs in os.walk(f"{K}/{обл}/{корень}"):
            for x in fs:
                p = os.path.join(r, x)
                rel = os.path.relpath(p, f"{K}/{обл}")
                if os.path.islink(p) or "__pycache__" in rel or x.endswith(".pyc"):
                    continue
                рп = в_репо(обл, rel)
                св = спис.get(rel, {})
                файлы[рп] = {"область": обл, "исходный": rel, "абс": p, "url": св.get("url", ""),
                             "sha256": None, "размер": os.path.getsize(p), "вид": св.get("вид", "")}

# ---------- записи ----------
исходные = {обл: json.load(open(f"{K}/{обл}/katalog.json")) for обл in ОБЛАСТИ}
записи: dict[tuple[str, int], dict] = {}
for обл in ОБЛАСТИ:
    for i, z in enumerate(исходные[обл]):
        for поле in ОБЯЗАТЕЛЬНЫЕ:
            assert поле in z, (обл, i, поле)
        assert z["область"] == обл, (обл, i)
        н = {"ид": ид(обл, z["имя"])}
        for поле in ОБЯЗАТЕЛЬНЫЕ:
            н[поле] = copy.deepcopy(z[поле])
        if "сверка проверяющего" in z:
            н["сверка"] = z["сверка проверяющего"]
        лишние = set(z) - set(ОБЯЗАТЕЛЬНЫЕ) - {"сверка проверяющего"}
        assert not лишние, (обл, i, лишние)
        # источники
        ист = []
        for s in z["источник"]:
            s2 = {}
            f = s.get("файл", "")
            if f:
                рп = в_репо(обл, f)
                assert рп, (обл, f)
                o2, p2 = обл_и_путь(обл, f)
                assert os.path.exists(f"{K}/{o2}/{p2}"), (обл, f)
                s2["файл"] = рп
            s2["страница|строки"] = s.get("страница|строки", "")
            s2["url"] = s.get("url", "")
            if s.get("что"):
                s2["что"] = s["что"]
            if s.get("примечание"):
                s2["что"] = (s2.get("что", "") + " " + s["примечание"]).strip()
            for k in s:
                assert k in ("файл", "страница|строки", "url", "что", "примечание"), (обл, i, k)
            ист.append(s2)
        н["источник"] = ист
        for поле in ("параметры", "проверка", "сложность внедрения", "где применяется", "сверка"):
            if поле in н:
                н[поле] = заменить(обл, н[поле])
        for поле in ("где применяется", "проверка", "сложность внедрения"):
            if not str(н[поле]).strip():
                н[поле] = "—"
        н["класс"] = класс(z["вид"], z["имя"])
        н["в проекте"] = в_проекте(z["сложность внедрения"], н["класс"])
        записи[(обл, i)] = н

иды = Counter(z["ид"] for z in записи.values())
assert max(иды.values()) == 1, [k for k, v in иды.items() if v > 1]

# слияния
слито: set[tuple[str, int]] = set()
for осн, доп in СЛИЯНИЯ:
    a, b = записи[осн], записи[доп]
    исходн_a = исходные[осн[0]][осн[1]]["имя"]
    assert a["область"] == осн[0] and b["область"] == доп[0]
    ключи = {(s.get("файл"), s["страница|строки"]) for s in a["источник"]}
    for s in b["источник"]:
        if (s.get("файл"), s["страница|строки"]) not in ключи:
            a["источник"].append(s)
    for k, v in b["параметры"].items():
        if k not in a["параметры"]:
            a["параметры"][k] = v
        elif a["параметры"][k] != v:
            a["параметры"][f"{k} (по записи {b['область']})"] = v
    if b["где применяется"] not in a["где применяется"]:
        a["где применяется"] += f"; {b['где применяется']}"
    a["проверка"] += f" | [по записи области {b['область']} «{b['имя']}»] {b['проверка']}"
    if РАНГ[b["в проекте"]] > РАНГ[a["в проекте"]]:
        a["в проекте"] = b["в проекте"]
        a["сложность внедрения"] += f" | [{b['область']}] {b['сложность внедрения']}"
    a.setdefault("также в", [])
    if b["область"] not in a["также в"] and b["область"] != a["область"]:
        a["также в"].append(b["область"])
    a.setdefault("слиты", []).append({"ид": b["ид"], "имя": b["имя"], "область": b["область"]})
    слито.add(доп)
    assert исходн_a == a["имя"]

for x, y in СМ_ТАКЖЕ:
    assert x not in слито and y not in слито, (x, y)
    a, b = записи[x], записи[y]
    a.setdefault("см. также", []).append(b["ид"])
    b.setdefault("см. также", []).append(a["ид"])

итог = [z for k, z in записи.items() if k not in слито]

# ---------- что положено ----------
for рп, св in файлы.items():
    if св["sha256"] is None:
        св["sha256"] = sha(св["абс"])
    исх = св["исходный"]
    if св["размер"] > ПРЕДЕЛ_БАЙТ:
        св["положен"], св["причина"] = False, "больше 40 МБ — только ссылкой"
    elif исх.startswith("istochniki/"):
        if св["sha256"] in chosen or св["размер"] <= 100_000:
            св["положен"] = True
        else:
            св["положен"], св["причина"] = False, "не хватило места на машине сборки — ссылкой (sha256 для сверки при докачке)"
    else:
        св["положен"] = True
# тексты PDF, упомянутые в записях, — кладём (сжимаются в git хорошо)
for обл, p in sorted(упомянутые_тексты):
    рп = в_репо(обл, p)
    if рп not in файлы:
        абс = f"{K}/{обл}/{p}"
        файлы[рп] = {"область": обл, "исходный": p, "абс": абс, "url": "", "sha256": sha(абс),
                     "размер": os.path.getsize(абс), "вид": "текст PDF (производный)", "положен": True}

# что взято из файла
взято: dict[str, list[str]] = defaultdict(list)
for z in итог:
    for s in z["источник"]:
        f = s.get("файл")
        if not f:
            continue
        if f in файлы:
            s["положен"] = файлы[f]["положен"]
            взято[f].append(f"{s['страница|строки']}: {z['имя']}".strip(": "))
        else:   # папка
            под = [p for p in файлы if p.startswith(f.rstrip("/") + "/")]
            assert под, f
            s["положен"] = any(файлы[p]["положен"] for p in под)
            for p in под:
                взято[p].append(f"{s['страница|строки']}: {z['имя']} (папка)".strip(": "))
        if not s["положен"]:
            if not s["url"]:
                s["url"] = файлы.get(f, {}).get("url", "")

# порядок ключей
ПОРЯДОК = ["ид", "имя", "семейство", "область", "также в", "вид", "класс", "параметры", "где применяется",
           "источник", "проверка", "сверка", "сложность внедрения", "в проекте", "см. также", "слиты"]
итог = [{k: z[k] for k in ПОРЯДОК if k in z} for z in итог]
итог.sort(key=lambda z: (ОБЛАСТИ.index(z["область"]),))

сводка_обл = {}
for обл in ОБЛАСТИ:
    зз = [z for z in итог if z["область"] == обл]
    сводка_обл[обл] = {"название": НАЗВАНИЯ[обл], "записей": len(зз),
                       "исходных записей": len(исходные[обл]),
                       "в проекте": dict(Counter(z["в проекте"] for z in зз)),
                       "классы": dict(Counter(z["класс"] for z in зз).most_common())}
каталог = {
    "схема": {
        "версия": 1,
        "описание": "Единый каталог помехоустойчивых кодов, скремблеров и синхрокомбинаций из первоисточников; "
                    "пути файлов — относительно корня репозитория (istochniki/… — первоисточники, "
                    "src/reportgen/potok/data/kody/… — таблицы).",
        "поля": {
            "ид": "постоянный идентификатор: область-первые 8 знаков sha1(имени)",
            "имя": "название кода, как в первоисточнике",
            "семейство": "стандарт или семейство", "область": "область каталога (ключ)",
            "также в": "другие области, чьи записи о том же коде слиты в эту",
            "вид": "вид кода словами сборщика", "класс": "обобщённый вид для поиска",
            "параметры": "n,k, многочлены (запись как в стандарте), выкалывание, перемежитель, начальное состояние, "
                         "порядок бит, скремблер, кадр… — пути к таблицам в src/reportgen/potok/data/kody/",
            "где применяется": "системы и режимы",
            "источник": "список {файл, страница|строки, url, что, положен}; положен=false — файл не в репозитории, "
                        "только ссылкой (istochniki/katalog/spisok.json: url и sha256)",
            "проверка": "тестовый вектор или как сверено", "сверка": "сверка проверяющего (если была)",
            "сложность внедрения": "что есть в проекте (модули) и чего нет",
            "в проекте": "есть | частично | нет | справочно | не найдено",
            "см. также": "ид связанных записей", "слиты": "записи-дубли, присоединённые к этой",
        },
    },
    "собран": "2026-10-02",
    "области": сводка_обл,
    "записей": len(итог),
    "записи": итог,
}

# ---------- вывод ----------
print("записей", len(итог), "слито", len(слито), {o: v["записей"] for o, v in сводка_обл.items()})
print("классы", Counter(z["класс"] for z in итог).most_common())
print("в проекте", Counter(z["в проекте"] for z in итог))
print("нераспознанных путей в строках", len(нераспознанные), list(нераспознанные)[:15])
пол = [v for v in файлы.values() if v["положен"]]
print("файлов", len(файлы), "положено", len(пол), "МБ", sum(v["размер"] for v in пол) / 1e6,
      "ссылкой", len(файлы) - len(пол))

if ПОЛОЖИТЬ:
    for рп, св in файлы.items():
        if not св["положен"]:
            continue
        dst = os.path.join(W, рп)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            continue
        try:
            os.link(св["абс"], dst)
        except OSError:
            shutil.copy2(св["абс"], dst)
    with open(os.path.join(W, ДАННЫЕ, "kody_katalog.json"), "w", encoding="utf-8") as f:
        json.dump(каталог, f, ensure_ascii=False, indent=1)
        f.write("\n")
    спис = []
    for рп in sorted(файлы):
        св = файлы[рп]
        if рп.startswith(ТАБЛИЦЫ):
            continue
        э = {"путь": рп, "область": св["область"], "url": св["url"], "sha256": св["sha256"],
             "размер": св["размер"], "положен": св["положен"]}
        if not св["положен"]:
            э["причина"] = св["причина"]
        if св["вид"]:
            э["вид"] = св["вид"]
        if взято.get(рп):
            э["что взято"] = взято[рп][:12]
            if len(взято[рп]) > 12:
                э["что взято"].append(f"… и ещё {len(взято[рп]) - 12}")
        спис.append(э)
    with open(os.path.join(W, ИСТ, "spisok.json"), "w", encoding="utf-8") as f:
        json.dump({"собран": "2026-10-02", "файлов": len(спис), "файлы": спис}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    json.dump({рп: {k: v for k, v in св.items() if k != "абс"} for рп, св in файлы.items()},
              open(os.path.join(ЗДЕСЬ, "fayly_itog.json"), "w"), ensure_ascii=False)
