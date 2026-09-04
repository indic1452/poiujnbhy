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
# Набор проверок на офлайн-машине — единственный способ убедиться, что
# установка удалась, без модели и без сети. Windows-сборщик колёса для него
# кладёт, а этот не клал: на машине отдела проверять установку было нечем.
[ -f "$ROOT/requirements-dev.txt" ] && python3 -m pip download --quiet --dest "$DEST/wheels" \
    --requirement "$ROOT/requirements-dev.txt" || true
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

step "Скрипты установки"
# Кладём ДО манифеста: иначе сам установщик в него не попадает и подмену
# install-offline.sh проверка не заметит — а это единственный файл комплекта,
# который на офлайн-машине запускают с полными правами.
cp "$ROOT/scripts/offline/install-offline.sh" "$ROOT/scripts/offline/verify.sh" "$DEST/"
cp "$BUNDLE_JSON" "$DEST/bundle.json"
cp "$ROOT/scripts/windows/settings.example.json" "$DEST/settings.example.json"
chmod +x "$DEST"/*.sh
cp "$ROOT/docs/15-offline.md" "$DEST/ЧИТАТЬ-ПЕРВЫМ.md" 2>/dev/null || true
ok "install-offline.sh, verify.sh, settings.example.json"

step "Манифест и контрольные суммы"
python3 - "$DEST" "$BUNDLE_JSON" <<'PY'
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
# Из чего комплект ОБЯЗАН состоять: список одних лишь удавшихся файлов не
# отличает полный комплект от половины, и verify.sh рапортовал «целый».
import json as _json
план = _json.loads(Path(sys.argv[2]).read_text(encoding="utf-8-sig")) if len(sys.argv) > 2 else {}
manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python": sys.version.split()[0],
            "expected": {
                "models": [m["file"] for m in план.get("models", [])],
                "tessdata": [f["as"] for f in (план.get("tessdata") or {}).get("files", [])],
                "llama": [p["id"] for p in (план.get("llama_cpp") or {}).get("asset_patterns", [])],
            },
            "files": entries,
            "total_gb": round(total / 2**30, 2)}
(bundle / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"  файлов: {len(entries)}, объём: {manifest['total_gb']} ГБ")
PY

step "Полнота комплекта"
# Считать файлы бессмысленно: важно, встанет ли из них приложение. Спрашиваем
# у самого pip, ничего не устанавливая, — он же будет ставить их на машине
# отдела. Раньше одна сорвавшаяся закачка давала только предупреждение,
# уезжавшее вверх за экран, и выяснялось это уже на изолированной машине.
gaps=0
for set_name in requirements.txt requirements-formats.txt requirements-dev.txt; do
    [ -f "$ROOT/$set_name" ] || continue
    if python3 -m pip install --dry-run --ignore-installed --no-index \
           --find-links "$DEST/wheels" --requirement "$ROOT/$set_name" >/dev/null 2>&1; then
        ok "$set_name — из комплекта поставится"
    elif [ "$set_name" = requirements-dev.txt ]; then
        warn "$set_name — не поставится: на машине отдела не прогнать набор проверок"
    else
        printf '\033[31m  X   %s — из комплекта НЕ поставится\033[0m\n' "$set_name"
        gaps=$((gaps + 1))
    fi
done
if [ "$SKIP_MODELS" != "1" ]; then
    have_models=$(find "$DEST/models" -name '*.gguf' 2>/dev/null | wc -l)
    need_models=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['models']))" "$BUNDLE_JSON")
    if [ "$have_models" -lt "$need_models" ]; then
        printf '\033[31m  X   моделей %s из %s\033[0m\n' "$have_models" "$need_models"
        gaps=$((gaps + 1))
    else
        ok "моделей: $have_models"
    fi
fi
if ! find "$DEST/tessdata" -name 'rus.traineddata' 2>/dev/null | grep -q .; then
    warn "нет русского языка для Tesseract — сканы распознаются в бессмыслицу"
fi

echo
if [ "$gaps" -gt 0 ]; then
    printf '\033[31mКОМПЛЕКТ НЕПОЛНЫЙ.\033[0m Доберите недостающее и соберите заново.\n'
    echo "Везти такой комплект на изолированную машину нельзя: доложить там будет неоткуда."
    exit 1
fi
printf '\033[32mКомплект готов: %s\033[0m\n' "$DEST"
echo "Перенесите каталог целиком. На офлайн-машине: ./verify.sh, затем ./install-offline.sh"
