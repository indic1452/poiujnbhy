#!/usr/bin/env bash
# Установка на машине БЕЗ интернета из подготовленного комплекта (Linux).
set -euo pipefail
BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-/opt/reportgen}"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m  OK  %s\033[0m\n' "$1"; }
fail() { printf '\033[31m  X   %s\033[0m\n' "$1"; exit 1; }
# Замечание, сказанное в середине установки, уезжает вверх за экран. Копим и
# повторяем в конце: «Готово» без списка недоделанного — неправда.
WARNINGS=()
later() { WARNINGS+=("$1"); printf '\033[33m  !   %s\033[0m\n' "$1"; }

if [ "${SKIP_VERIFY:-0}" != "1" ]; then
    step "Проверка комплекта"
    set +e; "$BUNDLE/verify.sh"; verify_code=$?; set -e
    [ "$verify_code" -eq 1 ] && fail "комплект повреждён — установка отменена"
    [ "$verify_code" -eq 2 ] && later "комплект неполный — часть возможностей работать не будет"
fi

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
if [ -f "$TARGET/app/requirements-formats.txt" ]; then
    "$TARGET/app/.venv/bin/python" -m pip install --no-index \
        --find-links "$BUNDLE/wheels" -r "$TARGET/app/requirements-formats.txt" >/dev/null 2>&1 \
        && ok "поддержка презентаций, Excel, RTF и сканов установлена" \
        || later "пакеты поддержки форматов не встали — презентации, Excel и RTF читаться не будут"
fi
# Набор проверок — единственный способ убедиться, что установка удалась, не
# имея ни сети, ни модели. Windows-установщик эти пакеты ставит, а этот не
# ставил: колёса в комплекте лежали, а pytest на машине отдела не было.
if [ -f "$TARGET/app/requirements-dev.txt" ]; then
    "$TARGET/app/.venv/bin/python" -m pip install --no-index \
        --find-links "$BUNDLE/wheels" -r "$TARGET/app/requirements-dev.txt" >/dev/null 2>&1 \
        && ok "пакеты для прогона проверок установлены" \
        || later "пакеты для прогона проверок не встали — проверить установку набором не выйдет"
fi
# Проверяем ВСЁ, без чего приложение не поднимется: половина списка тут
# отсутствовала, и установка рапортовала «пакеты на месте», а сервер потом
# падал на python-multipart.
"$TARGET/app/.venv/bin/python" -c "import fastapi, uvicorn, docx, pymupdf, numpy, multipart, itsdangerous, jinja2; print('пакеты на месте')"
PYTHONPATH="$TARGET/app/src" "$TARGET/app/.venv/bin/python" -c \
    "import reportgen.web.app as app; app.create_app; print('приложение собирается')"
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
        later "Tesseract не установлен — языки не скопированы"
    fi
fi

step "Модели"
if compgen -G "$BUNDLE/models/*.gguf" > /dev/null; then
    cp "$BUNDLE"/models/*.gguf "$TARGET/models/"
    ok "модели скопированы"
else
    later "моделей в комплекте нет — положите .gguf в $TARGET/models"
fi

step "Настройки"
# Без settings.json data_dir по умолчанию — ./var от текущего каталога, и
# созданное выше дерево $TARGET/data никто не использует.
CONFIG="$TARGET/settings.json"
if [ ! -f "$CONFIG" ]; then
    # Берём образец из комплекта и подставляем пути. Свой урезанный
    # settings.json оставлял embed_enabled и rerank_enabled выключенными —
    # смысловой поиск молча не работал, — а templates_dir и glossary_path
    # оставались относительными и искались от текущего каталога.
    SAMPLE="$BUNDLE/settings.example.json"
    [ -f "$SAMPLE" ] || SAMPLE="$TARGET/app/scripts/windows/settings.example.json"
    if [ -f "$SAMPLE" ]; then
        python3 - "$SAMPLE" "$TARGET" "$CONFIG" <<'CONF'
import json, sys
from pathlib import Path
образец, target, config = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
настройки = json.loads(образец.read_text(encoding="utf-8-sig"))
настройки.update({
    "data_dir": str(target / "data"),
    "library_dir": str(target / "data" / "library"),
    "templates_dir": str(target / "app" / "templates"),
    "glossary_path": str(target / "app" / "templates" / "glossary.json"),
    "domains_path": str(target / "app" / "templates" / "domains.json"),
    "terms_path": str(target / "app" / "templates" / "terms.json"),
})
config.write_text(json.dumps(настройки, ensure_ascii=False, indent=2), encoding="utf-8")
CONF
        ok "создан $CONFIG (по образцу из комплекта)"
    else
        later "образца настроек нет — создайте $CONFIG вручную по docs/11-windows.md"
    fi
else
    ok "уже есть: $CONFIG"
fi

step "Проверка установки"
export PYTHONPATH="$TARGET/app/src"
export REPORTGEN_CONFIG="$CONFIG"
"$TARGET/app/.venv/bin/python" -m reportgen formats || \
    printf '\033[33m  !   reportgen formats завершился с ошибкой\033[0m\n'

echo
if [ "${#WARNINGS[@]}" -gt 0 ]; then
    printf '\033[33mУстановка завершена, но с замечаниями:\033[0m\n'
    for note in "${WARNINGS[@]}"; do printf '  * %s\n' "$note"; done
    echo
fi
printf '\033[32mГотово.\033[0m Дальше: собрать llama.cpp (docs/10), запустить сервер модели,\n'
echo "затем создать администратора:"
echo "  cd $TARGET/app"
echo "  PYTHONPATH=src REPORTGEN_CONFIG=$CONFIG .venv/bin/python -m reportgen useradd --login admin --role owner"
