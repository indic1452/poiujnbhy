#!/usr/bin/env python3
"""Сборка каталога области «efir»: выполняет все скрипты семейств (каждый проверяет свои значения assert-ами),
собирает записи в katalog.json, список файлов с sha256 в spisok_faylov.json и сводку svodka.json.
Проверяет: у каждой записи есть источник с существующим файлом, sha256 файлов совпадает с manifest.jsonl."""
import hashlib, importlib, json, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obshchee import KOREN, manifest

SEMEYSTVA = ["dvb_t", "dvb_t2", "dvb_c", "dvb_c2", "dvb_h", "dvb_ngh", "atsc1", "atsc3", "isdb", "dtmb", "cmmb", "dab", "drm", "hdradio",
             "rds", "darc", "teletekst", "mediaflo", "itu", "raptor", "wifi", "bluetooth", "ieee802154", "lora", "sigfox", "nbiot", "wimax", "uwb", "dop_vesch", "dop_besprov"]
POLYA = ["имя", "семейство", "область", "вид", "параметры", "где применяется", "источник", "проверка", "сложность внедрения"]

def norm_ist(z):
    z = dict(z)
    if "страница" in z or "строки" in z:
        s = z.pop("страница", None); c = z.pop("строки", None)
        z["страница|строки"] = (f"стр. {s} (номер страницы PDF)" if isinstance(s, int) else str(s)) if s is not None else f"строки {c}"
    return {"файл": z.get("файл"), "страница|строки": z.get("страница|строки", ""), "url": z.get("url", ""), **({"что": z["что"]} if z.get("что") else {})}

def main():
    zapisi = []; oshibki = []
    for s in SEMEYSTVA:
        put = os.path.join(KOREN, "skripty", s + ".py")
        if not os.path.exists(put):
            print("нет скрипта", s); continue
        m = importlib.import_module(s)
        print(f"{s}: {len(m.ZAPISI)}")
        zapisi += m.ZAPISI
    man = manifest()
    for z in zapisi:
        for p in POLYA:
            assert p in z, (z.get("имя"), p)
        z["источник"] = [norm_ist(i) for i in z["источник"]]
        if z["вид"] != "не найдено в открытом доступе":
            assert z["источник"], z["имя"]
        for i in z["источник"]:
            f = i["файл"]
            if f and not os.path.exists(os.path.join(KOREN, f)):
                oshibki.append(f"{z['имя']}: нет файла {f}")
            if f and f.startswith("istochniki/") and f not in man:
                oshibki.append(f"{z['имя']}: файл {f} не в manifest")
    imena = collections.Counter(z["имя"] for z in zapisi)
    assert all(v == 1 for v in imena.values()), [k for k, v in imena.items() if v > 1]
    # файлы: первоисточники (manifest) + производные (tekst, tablicy, risunki, skripty)
    fayly = []
    for put, z in man.items():
        polny = os.path.join(KOREN, put)
        if not os.path.exists(polny):
            oshibki.append(f"manifest: нет файла {put}"); continue
        h = hashlib.sha256(open(polny, "rb").read()).hexdigest()
        if h != z["sha256"]:
            oshibki.append(f"sha256 изменился: {put}")
        fayly.append({"путь": put, "url": z["url"], "sha256": h, "размер": os.path.getsize(polny), "вид": "первоисточник",
                      **({"коммит": z["commit"]} if z.get("commit") else {}), **({"примечание": z["primechanie"]} if z.get("primechanie") else {})})
    for pap, vid in (("tablicy", "таблица (собрана скриптом из источника)"), ("risunki", "снимок страницы источника"),
                     ("skripty", "скрипт сборки и проверки"), ("tekst", "текст PDF (производный)")):
        for root, _, fs in os.walk(os.path.join(KOREN, pap)):
            if "__pycache__" in root: continue
            for f in sorted(fs):
                polny = os.path.join(root, f)
                fayly.append({"путь": os.path.relpath(polny, KOREN), "url": "", "sha256": hashlib.sha256(open(polny, "rb").read()).hexdigest(),
                              "размер": os.path.getsize(polny), "вид": vid})
    if oshibki:
        print("\n".join(oshibki)); raise SystemExit(1)
    json.dump(zapisi, open(os.path.join(KOREN, "katalog.json"), "w"), ensure_ascii=False, indent=1)
    json.dump(fayly, open(os.path.join(KOREN, "spisok_faylov.json"), "w"), ensure_ascii=False, indent=1)
    po_sem = collections.Counter(z["семейство"] for z in zapisi)
    po_vidu = collections.Counter(z["вид"].split(":")[0].split(" (")[0] for z in zapisi)
    est = [z["имя"] for z in zapisi if z["сложность внедрения"].lower().startswith("есть")]
    net = [z["имя"] for z in zapisi if z["вид"] == "не найдено в открытом доступе"]
    sv = {"записей": len(zapisi), "по_семействам": po_sem, "по_виду": po_vidu, "первоисточников": len(man),
          "файлов_всего": len(fayly), "объём_первоисточников_байт": sum(f["размер"] for f in fayly if f["вид"] == "первоисточник"),
          "есть_в_проекте": est, "не_найдено_в_открытом_доступе": net}
    json.dump(sv, open(os.path.join(KOREN, "svodka.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in sv.items() if k not in ("есть_в_проекте", "не_найдено_в_открытом_доступе")}, ensure_ascii=False))

if __name__ == "__main__":
    main()
