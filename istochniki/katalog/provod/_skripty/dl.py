#!/usr/bin/env python3
"""Скачивание первоисточника с записью в manifest.jsonl: путь, URL, sha256, размер."""
import hashlib, json, os, subprocess, sys, time
KOREN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTTP_OK = ("ebook.pldworld.com",)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
def dl(url, put, minsize=1000):
    if url.startswith("http://") and not any(h in url for h in HTTP_OK): url = "https://" + url[7:]
    polny = os.path.join(KOREN, put)
    os.makedirs(os.path.dirname(polny), exist_ok=True)
    r = subprocess.run(["curl", "-sSL", "-A", UA, "-m", "600", "--retry", "6", "--retry-all-errors", "--retry-delay", "3", "-o", polny, "-w", "%{http_code}", url],
                       capture_output=True, text=True)
    kod = r.stdout.strip()
    if kod != "200" or not os.path.exists(polny) or os.path.getsize(polny) < minsize:
        print(f"ОШИБКА {kod} {url} {r.stderr.strip()[:200]}", file=sys.stderr)
        if os.path.exists(polny) and kod != "200": os.remove(polny)
        return False
    if put.lower().endswith(".pdf") and open(polny, "rb").read(5) != b"%PDF-":
        print(f"ОШИБКА не PDF {url}", file=sys.stderr); os.remove(polny); return False
    zapis(put, url)
    return True
def zapis(put, url, primechanie=""):
    polny = os.path.join(KOREN, put)
    h = hashlib.sha256(open(polny, "rb").read()).hexdigest()
    z = {"put": put, "url": url, "sha256": h, "razmer": os.path.getsize(polny), "kogda": time.strftime("%Y-%m-%d")}
    if primechanie: z["primechanie"] = primechanie
    with open(os.path.join(KOREN, "manifest.jsonl"), "a") as f:
        f.write(json.dumps(z, ensure_ascii=False) + "\n")
    print(f"OK {z['razmer']:>10} {put}")
if __name__ == "__main__":
    args = sys.argv[1:]
    for i in range(0, len(args), 2):
        dl(args[i], args[i + 1])

def etsi(path, papka, vse=False):
    """Последняя версия документа ETSI по пути deliver (напр. etsi_en/301800_301899/30184101) → istochniki/<papka>/."""
    import re as _re
    get = lambda u: subprocess.run(["curl", "-sSL", "-A", UA, "-m", "120", u], capture_output=True).stdout.decode("latin1")
    base = "https://www.etsi.org/deliver/" + path.strip("/") + "/"
    vers = sorted(set(_re.findall(r"/(\d\d(?:\.\d\d\.\d\d)?_60)/", get(base))))
    if not vers:
        print("НЕТ ВЕРСИЙ", path, file=sys.stderr); return []
    out = []
    for v in (vers if vse else vers[-1:]):
        for p in _re.findall(r'HREF="([^"]+\.pdf)"', get(base + v + "/"), _re.I):
            put = os.path.join("istochniki", papka, p.split("/")[-1])
            if os.path.exists(os.path.join(KOREN, put)) or dl("https://www.etsi.org" + p, put): out.append(put)
    return out

def iz_zip(put_zip, papka=None, vnutri=None):
    """Распаковать архив-первоисточник: каждый файл → istochniki/.../<имя архива>/<путь в архиве>, в manifest с url архива#член."""
    import zipfile
    z = zipfile.ZipFile(os.path.join(KOREN, put_zip))
    papka = papka or put_zip.rsplit(".", 1)[0]
    out = []
    for n in z.namelist():
        if n.endswith("/") or (vnutri and not any(v in n for v in vnutri)): continue
        put = os.path.join(papka, n)
        polny = os.path.join(KOREN, put)
        if not os.path.exists(polny):
            os.makedirs(os.path.dirname(polny), exist_ok=True)
            open(polny, "wb").write(z.read(n))
            zapis(put, _url_iz_manifesta(put_zip) + "#" + n, f"извлечено из {put_zip}")
        out.append(put)
    return out

def _url_iz_manifesta(put):
    for l in open(os.path.join(KOREN, "manifest.jsonl")):
        z = json.loads(l)
        if z["put"] == put: return z["url"]
    return ""
