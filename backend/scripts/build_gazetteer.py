#!/usr/bin/env python3
"""Собрать полный офлайн-газеттир из GeoNames (RU/UA/BY).

Запускать НА МАШИНЕ С ДОСТУПОМ В СЕТЬ (в облачном dev-окружении GeoNames
заблокирован egress-политикой). Данные GeoNames — лицензия CC BY 4.0.

Результат: backend/data/gazetteer_full.csv (в формате gazetteer_sample.csv).
Геокодер подхватит его автоматически при следующем старте.

Использование:
    python scripts/build_gazetteer.py                 # RU,UA,BY, все нас. пункты
    python scripts/build_gazetteer.py --min-pop 500   # только >=500 жителей
    python scripts/build_gazetteer.py --countries RU UA
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://download.geonames.org/export/dump/"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Колонки per-country dump (tab-separated), см. geonames readme
COLS = [
    "geonameid", "name", "asciiname", "alternatenames", "latitude", "longitude",
    "feature_class", "feature_code", "country_code", "cc2", "admin1_code",
    "admin2_code", "admin3_code", "admin4_code", "population", "elevation",
    "dem", "timezone", "mod_date",
]


def _download(url: str) -> bytes:
    print(f"  скачиваю {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "gazetteer-builder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
        return r.read()


def _load_admin_map(filename: str) -> dict[str, str]:
    """admin1CodesASCII.txt / admin2Codes.txt → {code: name}."""
    data = _download(BASE + filename).decode("utf-8", "replace")
    out: dict[str, str] = {}
    for line in data.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def build(countries: list[str], min_pop: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    admin1 = _load_admin_map("admin1CodesASCII.txt")
    admin2 = _load_admin_map("admin2Codes.txt")

    out_path = DATA_DIR / "gazetteer_full.csv"
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as out:
        w = csv.writer(out)
        w.writerow(
            ["name_ru", "name_uk", "name_en", "aliases", "lat", "lon",
             "admin1", "admin2", "country", "feature"]
        )
        for cc in countries:
            raw = _download(f"{BASE}{cc}.zip")
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                with z.open(f"{cc}.txt") as fh:
                    for line in io.TextIOWrapper(fh, encoding="utf-8"):
                        row = dict(zip(COLS, line.rstrip("\n").split("\t")))
                        if row.get("feature_class") != "P":  # только нас. пункты
                            continue
                        try:
                            pop = int(row.get("population") or 0)
                        except ValueError:
                            pop = 0
                        if pop < min_pop:
                            continue
                        a1 = admin1.get(f"{cc}.{row['admin1_code']}", "")
                        a2 = admin2.get(
                            f"{cc}.{row['admin1_code']}.{row['admin2_code']}", ""
                        )
                        w.writerow([
                            row["name"], "", row["asciiname"],
                            row["alternatenames"].replace(",", "|"),
                            row["latitude"], row["longitude"],
                            a1, a2, cc, row["feature_code"],
                        ])
                        n += 1
            print(f"  {cc}: готово")
    print(f"Готово: {n} записей → {out_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", nargs="+", default=["RU", "UA", "BY"])
    ap.add_argument("--min-pop", type=int, default=0)
    args = ap.parse_args()
    try:
        build([c.upper() for c in args.countries], args.min_pop)
    except Exception as exc:  # noqa: BLE001
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
