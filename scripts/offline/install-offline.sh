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

step "Модели"
if compgen -G "$BUNDLE/models/*.gguf" > /dev/null; then
    cp "$BUNDLE"/models/*.gguf "$TARGET/models/"
    ok "модели скопированы"
else
    printf '\033[33m  !   моделей в комплекте нет — положите .gguf в %s\033[0m\n' "$TARGET/models"
fi

echo
printf '\033[32mГотово.\033[0m Дальше: собрать llama.cpp (docs/10), запустить сервер модели,\n'
echo "затем: cd $TARGET/app && PYTHONPATH=src .venv/bin/python -m reportgen useradd --login admin --role admin"
