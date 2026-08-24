#!/usr/bin/env bash
# Проверка офлайн-комплекта по manifest.json (Linux).
set -euo pipefail
BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$BUNDLE" "${1:-full}" <<'PY'
import hashlib, json, sys
from pathlib import Path
bundle, mode = Path(sys.argv[1]), sys.argv[2]
manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
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
print("\nКомплект целый. Можно устанавливать: ./install-offline.sh")
PY
