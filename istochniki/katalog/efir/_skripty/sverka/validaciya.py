"""Проверка формы katalog.json: валидный JSON, обязательные поля, у каждой записи источник с существующим файлом,
ссылки на таблицы (tablicy/…) существуют и валидны, sha256 первоисточников = manifest.jsonl."""
import json, os, re, hashlib, sys
sys.path.insert(0, os.path.dirname(__file__))
from obsh import KOREN
POLYA = ["имя", "семейство", "область", "вид", "параметры", "где применяется", "источник", "проверка", "сложность внедрения"]
def main():
    k = json.load(open(os.path.join(KOREN, "katalog.json")))
    man = {json.loads(l)["put"]: json.loads(l) for l in open(os.path.join(KOREN, "manifest.jsonl"))}
    osh = []
    imena = set()
    for z in k:
        for p in POLYA:
            if p not in z or z[p] in (None, "", [], {}): osh.append(f"{z.get('имя')}: пустое/нет поля {p}")
        if z["имя"] in imena: osh.append(f"дубль имени {z['имя']}")
        imena.add(z["имя"])
        if z.get("область") != "efir": osh.append(f"{z['имя']}: область {z.get('область')}")
        for i in z.get("источник", []):
            f = i.get("файл")
            if not f: osh.append(f"{z['имя']}: источник без файла"); continue
            if not os.path.exists(os.path.join(KOREN, f)): osh.append(f"{z['имя']}: нет файла {f}")
            if f.startswith("istochniki/") and f not in man: osh.append(f"{z['имя']}: {f} не в manifest")
            if not i.get("страница|строки"): osh.append(f"{z['имя']}: у источника {f} нет страницы/строк")
            if not i.get("url"): osh.append(f"{z['имя']}: у источника {f} нет url")
        for t in set(re.findall(r"tablicy/[\w\-]+\.json", json.dumps(z, ensure_ascii=False))):
            put = os.path.join(KOREN, t)
            if not os.path.exists(put): osh.append(f"{z['имя']}: нет таблицы {t}")
            else:
                d = json.load(open(put))
                for p in ("источник", "проверка", "данные"):
                    if not d.get(p): osh.append(f"{t}: пустое поле {p}")
    for put, z in man.items():
        polny = os.path.join(KOREN, put)
        if not os.path.exists(polny): osh.append(f"manifest: нет {put}"); continue
        if hashlib.sha256(open(polny, "rb").read()).hexdigest() != z["sha256"]: osh.append(f"sha256: {put}")
    return k, osh
if __name__ == "__main__":
    k, osh = main()
    print(len(k), "записей;", len(osh), "замечаний"); print("\n".join(osh[:80]))
