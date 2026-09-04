#!/usr/bin/env bash
# Проверка офлайн-комплекта по manifest.json (Linux).
set -euo pipefail
BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$BUNDLE" "${1:-full}" <<'PY'
import hashlib, json, sys
from pathlib import Path
bundle, mode = Path(sys.argv[1]), sys.argv[2]
# utf-8-sig: манифест, собранный на Windows, начинается с BOM, и
# обычный utf-8 на нём спотыкается.
manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8-sig"))
print(f"Комплект от {manifest['created']}, файлов: {len(manifest['files'])}, "
      f"объём: {manifest['total_gb']} ГБ")
missing = damaged = 0
for entry in manifest["files"]:
    path = bundle / entry["path"]
    if not path.is_file():
        print(f"  НЕТ ФАЙЛА  {entry['path']}"); missing += 1; continue
    if path.stat().st_size != entry["bytes"]:
        print(f"  РАЗМЕР     {entry['path']}"); damaged += 1; continue
    if mode != "quick":
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        if digest.hexdigest() != entry["sha256"]:
            print(f"  ПОВРЕЖДЁН  {entry['path']}"); damaged += 1
if missing or damaged:
    print(f"\nОтсутствует: {missing}, повреждено: {damaged}. Устанавливать нельзя.")
    sys.exit(1)

# Целые файлы — ещё не полный комплект: manifest.json перечисляет лишь то, что
# удалось скачать. Сверяем с составом, объявленным при сборке.
пусто = []
ожидается = manifest.get("expected") or {}
пути = [item["path"] for item in manifest["files"]]
def есть(начало):
    return any(p.startswith(начало) for p in пути)
for файл in ожидается.get("models", []):
    if f"models/{файл}" not in пути:
        пусто.append(f"модель {файл}")
for язык in ожидается.get("tessdata", []):
    if f"tessdata/{язык}" not in пути:
        пусто.append(f"язык Tesseract {язык}")
if ожидается.get("llama") and not есть("llama/"):
    пусто.append("сборка llama.cpp")
if пусто:
    print("\nФайлы целы, но КОМПЛЕКТ НЕПОЛНЫЙ:")
    for что in пусто:
        print(f"  * нет: {что}")
    print("\nСтавить можно, но система заработает не вся.")
    sys.exit(2)

print("\nКомплект целый и полный. Можно устанавливать: ./install-offline.sh")
PY
