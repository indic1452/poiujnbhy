#!/usr/bin/env bash
# Сборка офлайн-комплекта на машине С ИНТЕРНЕТОМ (Linux).
# Windows-вариант — pack.ps1; для целевой Windows-машины собирать нужно
# на Windows: колёса зависят от платформы и версии Python.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="${1:-$PWD/reportgen-offline}"
BUNDLE_JSON="${BUNDLE_JSON:-$ROOT/scripts/offline/bundle.example.json}"
SKIP_MODELS="${SKIP_MODELS:-0}"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  OK  %s\033[0m\n' "$1"; }
warn() { printf '\033[33m  !   %s\033[0m\n' "$1"; }

mkdir -p "$DEST"/{wheels,llama,models,tools,tessdata,code,docs}

step "Код приложения"
if [ -d "$ROOT/.git" ]; then
    git -C "$ROOT" bundle create "$DEST/code/reportgen.bundle" --all >/dev/null
    ok "git-бандл создан"
fi
rm -rf "$DEST/code/reportgen-src"
mkdir -p "$DEST/code/reportgen-src"
# Если каталог назначения лежит внутри репозитория (а по умолчанию так и
# выходит при запуске из корня), tar начнёт паковать комплект сам в себя.
EXCLUDE_DEST=""
case "$DEST" in
    "$ROOT"/*) EXCLUDE_DEST="--exclude=./${DEST#$ROOT/}" ;;
esac
tar -C "$ROOT" \
    --exclude=.git --exclude=var --exclude=build --exclude=__pycache__ \
    --exclude=.venv --exclude=wheels --exclude=backups \
    --exclude=./reportgen-offline $EXCLUDE_DEST \
    -cf - . | tar -C "$DEST/code/reportgen-src" -xf -
ok "исходники скопированы"

cp "$ROOT"/docs/*.md "$ROOT/README.md" "$DEST/docs/" 2>/dev/null || true
ok "документация скопирована"

step "Колёса Python"
python3 -m pip download --quiet --dest "$DEST/wheels" --requirement "$ROOT/requirements.txt"
[ -f "$ROOT/requirements-formats.txt" ] && python3 -m pip download --quiet --dest "$DEST/wheels" \
    --requirement "$ROOT/requirements-formats.txt" || true
python3 -m pip download --quiet --dest "$DEST/wheels" pip setuptools wheel
python3 -c "import sys; print('%d.%d' % sys.version_info[:2])" > "$DEST/wheels/PYTHON-VERSION.txt"
ok "колёс: $(find "$DEST/wheels" -type f -name '*.whl' -o -name '*.tar.gz' | wc -l)"

step "llama.cpp"
warn "для Linux llama.cpp собирается из исходников (docs/10): кладу архив репозитория"
if command -v git >/dev/null; then
    git clone --depth 1 https://github.com/ggml-org/llama.cpp "$DEST/llama/llama.cpp" >/dev/null 2>&1 \
        && ok "исходники llama.cpp склонированы" \
        || warn "не удалось склонировать llama.cpp — перенесите вручную"
fi

if [ "$SKIP_MODELS" != "1" ]; then
    step "Модели GGUF (это надолго)"
    python3 - "$BUNDLE_JSON" "$DEST/models" <<'PY'
import json, subprocess, sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = Path(sys.argv[2])
for model in config["models"]:
    url = f"https://huggingface.co/{model['repo']}/resolve/main/{model['file']}?download=true"
    out = target / model["file"]
    print(f"  {model['role']}: {model['file']} (~{model.get('approx_gb', '?')} ГБ)")
    subprocess.run(["curl", "-L", "--fail", "--retry", "5", "-C", "-", "-o", str(out), url], check=True)
PY
    ok "модели скачаны"
else
    warn "модели пропущены (SKIP_MODELS=1)"
fi

step "Языковые файлы Tesseract"
python3 - "$BUNDLE_JSON" "$DEST/tessdata" <<'TESS'
import json, subprocess, sys
from pathlib import Path
config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = Path(sys.argv[2])
block = config.get("tessdata")
if not block:
    raise SystemExit(0)
for item in block["files"]:
    url = f"https://raw.githubusercontent.com/{item['repo']}/{item.get('ref', 'main')}/{item['path']}"
    out = target / item["as"]
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["curl", "-sSL", "--fail", "--retry", "5", "-C", "-", "-o", str(out), url], check=True)
    print(f"  {item['as']}")
(target / "tessdata.json").write_text(json.dumps(
    {"target": block.get("target"), "install_from": block.get("install_from")},
    ensure_ascii=False), encoding="utf-8")
TESS
ok "языковые файлы скачаны"

# Под Linux LibreOffice, Tesseract и DjVuLibre ставятся пакетами
# дистрибутива: универсальных установщиков, как под Windows, у них нет.
warn "внешние программы под Linux ставятся пакетами дистрибутива —"
warn "соберите их локальный репозиторий отдельно (см. docs/15-offline.md)"

step "Манифест и контрольные суммы"
python3 - "$DEST" <<'PY'
import hashlib, json, sys, time
from pathlib import Path
bundle = Path(sys.argv[1])
entries, total = [], 0
for path in sorted(bundle.rglob("*")):
    if not path.is_file() or path.name == "manifest.json":
        continue
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    size = path.stat().st_size
    total += size
    entries.append({"path": path.relative_to(bundle).as_posix(),
                    "bytes": size, "sha256": digest.hexdigest()})
manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python": sys.version.split()[0],
            "files": entries,
            "total_gb": round(total / 2**30, 2)}
(bundle / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"  файлов: {len(entries)}, объём: {manifest['total_gb']} ГБ")
PY

cp "$ROOT/scripts/offline/install-offline.sh" "$ROOT/scripts/offline/verify.sh" "$DEST/" 2>/dev/null || true
cp "$BUNDLE_JSON" "$DEST/bundle.json" 2>/dev/null || true
chmod +x "$DEST"/*.sh 2>/dev/null || true
cp "$ROOT/docs/15-offline.md" "$DEST/ЧИТАТЬ-ПЕРВЫМ.md" 2>/dev/null || true

echo
printf '\033[32mКомплект готов: %s\033[0m\n' "$DEST"
echo "Перенесите каталог целиком. На офлайн-машине: ./verify.sh, затем ./install-offline.sh"
