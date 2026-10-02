"""Докачка первоисточников каталога кодов, положенных в репозиторий только ссылкой.

На изолированной машине сети нет: запускать там, где она есть, и переносить папку istochniki/katalog
целиком. Опись — istochniki/katalog/spisok.json (путь, url, sha256, размер, положен).

  python3 istochniki/katalog/dokachat.py              # скачать по URL всё, чего нет, со сверкой sha256
  python3 istochniki/katalog/dokachat.py --iz ПАПКА   # взять из копии рабочей папки сборки (по sha256)
  python3 istochniki/katalog/dokachat.py --proverit   # только проверить, что есть и что sha256 совпадает

Файл с неверной суммой не сохраняется. URL, которые не ведут прямо на файл (страница архива, «из архива …»),
выводятся списком — их надо скачать руками. Только стандартная библиотека.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parents[2]
ОПИСЬ = Path(__file__).resolve().parent / "spisok.json"


def sha256(путь: Path) -> str:
    h = hashlib.sha256()
    with open(путь, "rb") as f:
        for кусок in iter(lambda: f.read(1 << 20), b""):
            h.update(кусок)
    return h.hexdigest()


def main() -> int:
    п = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    п.add_argument("--iz", help="папка с копией файлов (ищется по размеру и sha256)")
    п.add_argument("--proverit", action="store_true", help="только проверить наличие и sha256")
    п.add_argument("--vse", action="store_true", help="проверять и положенные в репозиторий")
    а = п.parse_args()
    опись = json.loads(ОПИСЬ.read_text(encoding="utf-8"))["файлы"]
    нужно = [x for x in опись if а.vse or not x["положен"]]
    по_размеру: dict[int, list[Path]] = {}
    if а.iz:
        for r, _, fs in os.walk(а.iz):
            for имя in fs:
                p = Path(r) / имя
                if p.is_file() and not p.is_symlink():
                    по_размеру.setdefault(p.stat().st_size, []).append(p)
    есть = скачано = руками = плохо = 0
    for x in нужно:
        цель = КОРЕНЬ / x["путь"]
        if цель.exists() and цель.stat().st_size == x["размер"] and sha256(цель) == x["sha256"]:
            есть += 1
            continue
        if а.proverit:
            print(f"нет или не совпадает: {x['путь']}")
            плохо += 1
            continue
        найден = None
        for кандидат in по_размеру.get(x["размер"], []):
            if sha256(кандидат) == x["sha256"]:
                найден = кандидат
                break
        цель.parent.mkdir(parents=True, exist_ok=True)
        if найден:
            shutil.copy2(найден, цель)
            скачано += 1
            continue
        url = x.get("url", "")
        if not url.startswith(("http://", "https://")) or " " in url:
            print(f"руками: {x['путь']} — {url or 'URL нет (распаковано из архива рядом)'}")
            руками += 1
            continue
        временный = цель.with_suffix(цель.suffix + ".part")
        try:
            запрос = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(запрос, timeout=120) as ответ, open(временный, "wb") as f:
                shutil.copyfileobj(ответ, f)
        except Exception as e:  # noqa: BLE001 — сеть: любая ошибка = «руками»
            print(f"руками: {x['путь']} — {url} ({e})")
            временный.unlink(missing_ok=True)
            руками += 1
            continue
        if sha256(временный) != x["sha256"]:
            print(f"sha256 не совпал (файл на сайте обновлён?): {x['путь']} — {url}")
            временный.unlink(missing_ok=True)
            плохо += 1
            continue
        временный.replace(цель)
        скачано += 1
    print(f"на месте {есть}, получено {скачано}, руками {руками}, не совпало/нет {плохо}, всего {len(нужно)}")
    return 1 if плохо else 0


if __name__ == "__main__":
    sys.exit(main())
