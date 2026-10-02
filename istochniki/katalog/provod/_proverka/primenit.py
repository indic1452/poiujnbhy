"""Применение итогов проверки к каталогу provod.

Порядок: skripty/sobrat.py (сборщик) → копия katalog.json в proverka/katalog_do_proverki.json →
proverka/vse.py (сверки, журнал, новые записи) → proverka/primenit.py (этот файл).
Вход: proverka/katalog_do_proverki.json, proverka/zhurnal.json, proverka/novye.json,
proverka/obnovleniya.json, proverka/zameny_istochnikov.json.
Выход: katalog.json, spisok_faylov.json, svodka.json (с разделом «проверка»).
Каждая правка — точная замена с assert: если в исходной записи нет ожидаемого текста, сборка падает."""
import collections, copy, hashlib, json, os, sys
D = os.path.dirname(os.path.abspath(__file__)); KOREN = os.path.dirname(D)
sys.path.insert(0, os.path.join(KOREN, "skripty"))
from obshchee import manifest
POLYA = ["имя", "семейство", "область", "вид", "параметры", "где применяется", "источник", "проверка", "сложность внедрения"]
J = lambda f: json.load(open(os.path.join(D, f)))
K = copy.deepcopy(J("katalog_do_proverki.json")); ZH = J("zhurnal.json"); NOV = J("novye.json"); OBN = J("obnovleniya.json"); ZAM = J("zameny_istochnikov.json")
ISPR_PRIM = []

def odna(ch):
    r = [z for z in K if z["имя"].startswith(ch)] or [z for z in K if ch in z["имя"]]
    assert len(r) == 1, (ch, [z["имя"] for z in r]); return r[0]

def zamenit_v(z, put, bylo, stalo):
    """Замена подстроки в поле записи (путь — список ключей/индексов)."""
    o = z
    for k in put[:-1]: o = o[k]
    assert bylo in o[put[-1]], (z["имя"], put, bylo, o[put[-1]])
    o[put[-1]] = o[put[-1]].replace(bylo, stalo)
    ISPR_PRIM.append({"запись": z["имя"], "поле": "/".join(map(str, put)), "было": bylo, "стало": stalo})

def ist_str(z, fail_ok, bylo, stalo, chto=None):
    hit = [s for s in z["источник"] if s["файл"].endswith(fail_ok) and s["страница|строки"].startswith(bylo) and (chto is None or s.get("что", "") == chto)]
    assert hit, (z["имя"], fail_ok, bylo, chto)
    for s in hit:
        ISPR_PRIM.append({"запись": z["имя"], "поле": f"источник/{s['файл'].split('/')[-1]}/{s.get('что', '')}", "было": s["страница|строки"], "стало": stalo})
        s["страница|строки"] = stalo

# ---- 1. исправления, найденные сверкой (proverka/ispravleniya.json) ----
z = odna("Ethernet BASE-R FEC (Clause 74)")
zamenit_v(z, ["параметры", "транскодирование"], "T = SH.1 XOR бит 8 блока 64B/66B", "T = SH.1 XOR S1.0 (бит 0 второго октета нагрузки = бит 8 нагрузки, бит 10 блока 66b; рис. 74–5)")
z = odna("RS(544,514) «KP4 FEC»")
zamenit_v(z, ["параметры", "400G/200G"], "(119.2.4.6: tx_out", "(119.2.4.8 Symbol distribution: tx_out")
zamenit_v(z, ["проверка"], "30 коэффициентов g_i табл. 119–3", "31 коэффициент g_0…g_30 табл. 119–3 (g_30 = 1)")
z = odna("G.975.1 I.3:")
ist_str(z, "T-REC-G.975.1-201307-I_Cor2.pdf", "стр. 3 ", "стр. 5 (номер страницы PDF)")
z = odna("XG-PON/XGS-PON/NG-PON2: RS(248,216)")
ist_str(z, "T-REC-G.987.3-202505-I.pdf", "стр. 6 ", "стр. 117–119 (номер страницы PDF)", "Annex B")
z = odna("LTO-1 Ultrium")
ist_str(z, "ECMA-319_1st_edition_june_2001.pdf", "стр. 75 ", "стр. 75–76 (номер страницы PDF)", "13.2")
ist_str(z, "ECMA-319_1st_edition_june_2001.pdf", "стр. 85 ", "стр. 85–86 (номер страницы PDF)", "13.6.2")
ist_str(z, "ECMA-319_1st_edition_june_2001.pdf", "стр. 86 ", "стр. 86–87 (номер страницы PDF)", "13.6.3")
z = odna("CPRI 7.0")
ist_str(z, "CPRI_v_7_0_2015-10-09.pdf", "стр. 3 ", "стр. 95–98 (номер страницы PDF)", "6.10")
zamenit_v(z, ["параметры", "пример"], "раздел 6.10 (информативный) — полный пример гиперкадра (не сверялся)", "раздел 6.10 (информативный), стр. 95–98 — пересчитан проверяющим: слово RS(528,514) и ПСП сошлись")
zamenit_v(z, ["параметры", "скремблер"], "фиксированная ПСП 5280 бит после кодера", "фиксированная ПСП 5280 бит после кодера (FC-FS-4 5.4.4); по примеру 6.10 ПСП удовлетворяет x^58 + x^39 + 1 на всех 5280 битах — сама последовательность: tablicy/cpri_pn5280.json")
zamenit_v(z, ["проверка"], "пример 6.10 не пересчитывался", "пример 6.10 пересчитан проверяющим: 20 блоков 257 бит → 14 нулевых синдромов RS(528,514) (бит 0 слова — младший бит символа M513), XOR выходов кодера и скремблера — ПСП с рекуррентой x^58 + x^39 + 1")
for zm in ZAM:
    ist_str(odna(zm["запись"][:40]), zm["файл"].split("/")[-1], zm["было"], zm["стало"])

# ---- 2. дополнения к записям (proverka/obnovleniya.json) ----
for ch, o in OBN.items():
    z = odna(ch)
    for k, v in o.items():
        if k == "источник+": z["источник"] += v
        elif k == "проверка+": z["проверка"] = (z["проверка"].rstrip(". ") + "; " if z["проверка"] not in ("", "—") else "") + v
        elif k == "параметры+": z["параметры"].update(v)
        else: z[k] = v
    ISPR_PRIM.append({"запись": z["имя"], "поле": ", ".join(o), "было": "—", "стало": "дополнено проверяющим"})

# ---- 3. итог сверки в каждую проверенную запись ----
po_zap = collections.defaultdict(list)
for r in ZH: po_zap[r["запись"]].append(r)
for z in K:
    r = po_zap.get(z["имя"])
    if not r: continue
    z["сверка проверяющего"] = {"проверок": len(r), "совпало": sum(x["итог"] == "совпало" for x in r),
        "исправлено": [x["проверка"] for x in r if x["итог"].startswith("ОШИБКА")], "расхождений": sum(x["итог"] == "РАСХОЖДЕНИЕ" for x in r),
        "что сверено": [x["проверка"] for x in r if x["итог"] == "совпало"], "журнал": "proverka/zhurnal.json"}

# ---- 4. новые записи ----
for n in NOV:
    r = po_zap.get(n["имя"], [])
    n["сверка проверяющего"] = {"проверок": len(r), "совпало": sum(x["итог"] == "совпало" for x in r), "исправлено": [], "расхождений": sum(x["итог"] == "РАСХОЖДЕНИЕ" for x in r),
                                "что сверено": [x["проверка"] for x in r], "журнал": "proverka/zhurnal.json", "добавлено": "проверяющим (proverka/dopolnenie.py)"}
    K.append({k: n[k] for k in POLYA} | {"сверка проверяющего": n["сверка проверяющего"]})

# ---- 5. проверки целостности ----
man = manifest(); osh = []
for z in K:
    for p in POLYA: assert p in z, (z["имя"], p)
    assert z["область"] == "provod"
    if not z["источник"] and z["вид"] != "не найдено в открытом доступе": osh.append(f"{z['имя']}: нет источника")
    for s in z["источник"]:
        assert set(s) >= {"файл", "страница|строки", "url"}, (z["имя"], s)
        if not os.path.exists(os.path.join(KOREN, s["файл"])): osh.append(f"{z['имя']}: нет файла {s['файл']}")
        if s["файл"].startswith("istochniki/") and s["файл"] not in man: osh.append(f"{z['имя']}: {s['файл']} не в manifest")
imena = collections.Counter(z["имя"] for z in K)
osh += [f"повтор имени: {k}" for k, v in imena.items() if v > 1]
bez_ist = [z["имя"] for z in K if not z["источник"]]
for l in osh: print("!!", l)

# ---- 6. запись: каталог, список файлов, сводка ----
json.dump(K, open(os.path.join(KOREN, "katalog.json"), "w"), ensure_ascii=False, indent=1)
json.loads(open(os.path.join(KOREN, "katalog.json")).read())
json.dump(ISPR_PRIM, open(os.path.join(D, "primeneno.json"), "w"), ensure_ascii=False, indent=1)
fayly = []
for put, m in man.items():
    polny = os.path.join(KOREN, put)
    if not os.path.exists(polny): osh.append(f"manifest: нет файла {put}"); continue
    h = hashlib.sha256(open(polny, "rb").read()).hexdigest()
    if h != m["sha256"]: osh.append(f"sha256 изменился: {put}")
    fayly.append({"путь": put, "url": m["url"], "sha256": h, "размер": os.path.getsize(polny), "вид": "первоисточник",
                  **({"коммит": m["commit"]} if m.get("commit") else {}), **({"примечание": m["primechanie"]} if m.get("primechanie") else {})})
for pap, vid in (("tablicy", "таблица (собрана скриптом из источника)"), ("risunki", "снимок страницы источника"), ("skripty", "скрипт сборки и проверки"),
                 ("tekst", "текст PDF (производный)"), ("proverka", "проверка: скрипты, журнал, снимки")):
    for root, _, fs in os.walk(os.path.join(KOREN, pap)):
        if "__pycache__" in root: continue
        for f in sorted(fs):
            polny = os.path.join(root, f)
            fayly.append({"путь": os.path.relpath(polny, KOREN), "url": "", "sha256": hashlib.sha256(open(polny, "rb").read()).hexdigest(), "размер": os.path.getsize(polny), "вид": vid})
json.dump(fayly, open(os.path.join(KOREN, "spisok_faylov.json"), "w"), ensure_ascii=False, indent=1)
NE = "не найдено в открытом доступе"
ne_naid = [z["имя"] for z in K if z["вид"] == NE]
sv = {"записей": len(K), "определено_с_источниками": len(K) - len(ne_naid), "не_найдено": len(ne_naid),
      "по_семействам": collections.Counter(z["семейство"] for z in K), "по_виду": collections.Counter(z["вид"].split(":")[0].split(" (")[0] for z in K),
      "первоисточников": len(man), "файлов_всего": len(fayly), "объём_первоисточников_байт": sum(f["размер"] for f in fayly if f["вид"] == "первоисточник"),
      "есть_в_проекте": [z["имя"] for z in K if z["сложность внедрения"].lower().startswith("есть")], "не_найдено_в_открытом_доступе": ne_naid,
      "проверка": {"сверено_записей_сборщика": len([z for z in K if "сверка проверяющего" in z and "добавлено" not in z["сверка проверяющего"]]),
                   "определённых_записей_сборщика": len([z for z in J("katalog_do_proverki.json") if z["вид"] != NE]),
                   "добавлено_записей": len(NOV), "проверок_в_журнале": len(ZH), "совпало": sum(r["итог"] == "совпало" for r in ZH),
                   "исправлено_ошибок_каталога": sum(r["итог"].startswith("ОШИБКА") for r in ZH), "правок_применено": len(ISPR_PRIM),
                   "расхождений_неустранённых": sum(r["итог"] == "РАСХОЖДЕНИЕ" for r in ZH), "записей_без_источника": bez_ist,
                   "ошибок_целостности": osh}}
json.dump(sv, open(os.path.join(KOREN, "svodka.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: sv[k] for k in ("записей", "определено_с_источниками", "не_найдено", "первоисточников", "файлов_всего")} | {"проверка": {k: v for k, v in sv["проверка"].items()}}, ensure_ascii=False, indent=1))
assert not osh, osh
