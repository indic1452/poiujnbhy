#!/usr/bin/env bash
# Установка на машине БЕЗ интернета из подготовленного комплекта (Linux).
set -euo pipefail
BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-/opt/reportgen}"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  OK  %s\033[0m\n' "$1"; }
fail() { printf '\033[31m  X   %s\033[0m\n' "$1"; exit 1; }

[ "${SKIP_VERIFY:-0}" = "1" ] || { step "Проверка комплекта"; "$BUNDLE/verify.sh"; }

step "Версия Python"
want="$(cat "$BUNDLE/wheels/PYTHON-VERSION.txt" 2>/dev/null || echo '')"
have="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[ -z "$want" ] || [ "$want" = "$have" ] || fail "колёса собраны для Python $want, установлен $have"
ok "Python $have"

step "Каталоги в $TARGET"
mkdir -p "$TARGET"/{app,models,llama,data/library/{literature,standards,datasheets,reports,regulations},logs}
cp -r "$BUNDLE/code/reportgen-src/." "$TARGET/app/"
ok "код развёрнут"

step "Зависимости из локальных колёс"
python3 -m venv "$TARGET/app/.venv"
"$TARGET/app/.venv/bin/python" -m pip install --no-index --find-links "$BUNDLE/wheels" \
    --upgrade pip setuptools wheel >/dev/null
"$TARGET/app/.venv/bin/python" -m pip install --no-index --find-links "$BUNDLE/wheels" \
    -r "$TARGET/app/requirements.txt"
[ -f "$TARGET/app/requirements-formats.txt" ] && "$TARGET/app/.venv/bin/python" -m pip install \
    --no-index --find-links "$BUNDLE/wheels" -r "$TARGET/app/requirements-formats.txt" || true
"$TARGET/app/.venv/bin/python" -c "import fastapi, uvicorn, docx, pymupdf, numpy; print('пакеты на месте')"
ok "зависимости установлены, сеть не использовалась"

step "Языковые файлы Tesseract"
# Пакет tesseract-ocr-rus есть не во всех дистрибутивах, а без русского
# сканы русских книг распознаются в бессмыслицу.
if [ -d "$BUNDLE/tessdata" ]; then
    flavour="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("install_from","best"))' \
        "$BUNDLE/tessdata/tessdata.json" 2>/dev/null || echo best)"
    dest="$(ls -d /usr/share/tesseract-ocr/*/tessdata /usr/share/tessdata 2>/dev/null | head -1 || true)"
    if [ -n "$dest" ]; then
        cp "$BUNDLE/tessdata/$flavour"/*.traineddata "$dest/" 2>/dev/null || true
        [ -f "$dest/osd.traineddata" ] || cp "$BUNDLE/tessdata/fast/osd.traineddata" "$dest/" 2>/dev/null || true
        ok "языки скопированы в $dest ($flavour)"
        tesseract --list-langs 2>&1 | grep -qx rus && ok "tesseract видит русский" \
            || printf '\033[33m  !   tesseract не видит русский — проверьте %s\033[0m\n' "$dest"
    else
        printf '\033[33m  !   Tesseract не установлен — языки не скопированы\033[0m\n'
    fi
fi

step "Модели"
if compgen -G "$BUNDLE/models/*.gguf" > /dev/null; then
    cp "$BUNDLE"/models/*.gguf "$TARGET/models/"
    ok "модели скопированы"
else
    printf '\033[33m  !   моделей в комплекте нет — положите .gguf в %s\033[0m\n' "$TARGET/models"
fi

step "Настройки"
# Без settings.json data_dir по умолчанию — ./var от текущего каталога, и
# созданное выше дерево $TARGET/data никто не использует.
CONFIG="$TARGET/settings.json"
if [ ! -f "$CONFIG" ]; then
    python3 - "$TARGET" "$CONFIG" <<'CONF'
import json, sys
from pathlib import Path
target, config = Path(sys.argv[1]), Path(sys.argv[2])
config.write_text(json.dumps({
    "data_dir": str(target / "data"),
    "library_dir": str(target / "data" / "library"),
    "llm_base_url": "http://127.0.0.1:8000/v1",
    "embed_base_url": "http://127.0.0.1:8001/v1",
    "rerank_base_url": "http://127.0.0.1:8002/v1",
}, ensure_ascii=False, indent=2), encoding="utf-8")
CONF
    ok "создан $CONFIG"
else
    ok "уже есть: $CONFIG"
fi

step "Проверка установки"
export PYTHONPATH="$TARGET/app/src"
export REPORTGEN_CONFIG="$CONFIG"
"$TARGET/app/.venv/bin/python" -m reportgen formats || \
    printf '\033[33m  !   reportgen formats завершился с ошибкой\033[0m\n'

echo
printf '\033[32mГотово.\033[0m Дальше: собрать llama.cpp (docs/10), запустить сервер модели,\n'
echo "затем создать администратора:"
echo "  cd $TARGET/app"
echo "  PYTHONPATH=src REPORTGEN_CONFIG=$CONFIG .venv/bin/python -m reportgen useradd --login admin --role admin"
