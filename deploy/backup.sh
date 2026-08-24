#!/usr/bin/env bash
# Резервное копирование установки reportgen.
#
# Что копируется и почему (docs/07 р. 7.4):
#   * база SQLite      — кейсы, отчёты, правки, журнал. НЕВОССТАНОВИМО;
#   * библиотека       — исходные документы, из которых строится индекс;
#   * экспорты         — отправленные заказчикам DOCX/PDF;
#   * шаблоны-планы    — их пишет старший инженер, потерять обиднее всего.
# Индекс и векторы не копируются сознательно: они пересобираются командами
# `reportgen ingest` и `reportgen embed` из библиотеки.
#
# База копируется онлайн, через SQLite API `.backup`, — останавливать сервис не
# нужно. Простой `cp` базы в режиме WAL даёт битую копию: часть транзакций
# лежит в -wal и в файл ещё не перенесена.
#
# Запуск:
#   deploy/backup.sh                     # полный бэкап с проверкой восстановления
#   deploy/backup.sh --check <каталог>   # проверить уже сделанный бэкап
#   deploy/backup.sh --help
#
# По расписанию (ежедневно в 03:15), от root или от пользователя reportgen:
#   15 3 * * *  /opt/reportgen/deploy/backup.sh >> /var/log/reportgen/backup.log 2>&1
# либо systemd-таймером с той же командой в ExecStart.
#
# Настройка — переменными окружения (или строкой в /etc/reportgen/backup.env):
#   REPORTGEN_ENV_FILE   файл с REPORTGEN_* (по умолчанию /etc/reportgen/reportgen.env)
#   BACKUP_DIR           куда складывать копии (/var/backups/reportgen)
#   BACKUP_KEEP          сколько копий хранить (14)
#   BACKUP_TEMPLATES     каталог шаблонов (по умолчанию из REPORTGEN_TEMPLATES_DIR)
#   BACKUP_INCLUDE_ENV   1 — положить в копию и файлы настроек (в них секреты!)

set -euo pipefail

umask 0077   # в копии данные заказчиков: никаких прав для группы и остальных

# --------------------------------------------------------------- параметры --

# Корень установки: каталог на уровень выше deploy/ (обычно /opt/reportgen).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_FILE="${REPORTGEN_ENV_FILE:-/etc/reportgen/reportgen.env}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/reportgen}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
BACKUP_INCLUDE_ENV="${BACKUP_INCLUDE_ENV:-0}"

log()  { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
die()  { printf '%s  ОШИБКА: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; exit 1; }

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

# Подхватываем настройки установки, чтобы не дублировать пути.
load_env() {
    if [[ -r "$ENV_FILE" ]]; then
        set -a
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        set +a
        log "настройки прочитаны: $ENV_FILE"
    else
        log "файл настроек $ENV_FILE недоступен — работаем на значениях по умолчанию"
    fi

    DATA_DIR="${REPORTGEN_DATA_DIR:-/var/lib/reportgen}"
    DB_PATH="${REPORTGEN_DB_PATH:-$DATA_DIR/reportgen.db}"
    LIBRARY_DIR="${REPORTGEN_LIBRARY_DIR:-$DATA_DIR/library}"
    EXPORT_DIR="${REPORTGEN_EXPORT_DIR:-$DATA_DIR/exports}"
    TEMPLATES_DIR="${BACKUP_TEMPLATES:-${REPORTGEN_TEMPLATES_DIR:-/opt/reportgen/templates}}"
}

# ------------------------------------------------------------- copy базы ----

# Онлайн-копия SQLite. Предпочитаем sqlite3(1); в изолированном контуре его
# часто нет, тогда работаем тем же API через python3 (он есть всегда — на нём
# написано само приложение).
db_backup() {
    local src="$1" dst="$2"
    if command -v sqlite3 >/dev/null 2>&1; then
        sqlite3 "file:$src?mode=ro" ".timeout 15000" ".backup '$dst'"
    else
        python3 - "$src" "$dst" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
source = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=15)
target = sqlite3.connect(dst)
with target:
    source.backup(target)          # тот же механизм, что и .backup в sqlite3(1)
target.close()
source.close()
PY
    fi
    # Копия наследует journal_mode=WAL, и любое её открытие плодит рядом
    # -wal и -shm. Переводим копию в DELETE: один файл проще хранить,
    # считать по нему контрольную сумму и восстанавливать. Приложение всё
    # равно включает WAL само при первом подключении (store/db.py).
    python3 - "$dst" <<'PY'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=DELETE")
connection.close()
PY
    rm -f "$dst-wal" "$dst-shm"
}

# Проверка восстановления: копия открывается как обычная база, проходит
# integrity_check и отдаёт содержательные счётчики. Если тут тихо — копия
# рабочая, а не «файл нужного размера».
db_verify() {
    local db="$1"
    python3 - "$db" <<'PY' || die "копия базы не прошла проверку: $db"
import sqlite3, sys

db = sys.argv[1]
tables = ("users", "documents", "chunks", "cases", "reports", "edit_pairs", "audit")
try:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    status = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if status != "ok":
        print(f"  integrity_check: {status}", file=sys.stderr)
        raise SystemExit(1)
    counts = [f"{name}={connection.execute(f'SELECT count(*) FROM {name}').fetchone()[0]}"
              for name in tables]
except sqlite3.Error as error:
    # Сюда попадают битый файл, обрезанная копия и чужая схема.
    print(f"  база нечитаема: {error}", file=sys.stderr)
    raise SystemExit(1) from None
print("  integrity_check: ok")
print("  записи: " + ", ".join(counts))
PY
}

# --------------------------------------------------------------- проверка ---

# Полная проверка готовой копии: база восстанавливается во временный каталог,
# архивы разворачиваются на лету (tar -t), считаются контрольные суммы.
# Регламент из docs/07 р. 7.4 — раз в квартал; эта же функция гоняется после
# каждого бэкапа, поэтому «квартальная проверка» сводится к чтению журнала.
check_backup() {
    local dir="$1"
    [[ -d "$dir" ]] || die "каталог копии не найден: $dir"
    log "проверка копии: $dir"

    local tmp
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN

    [[ -f "$dir/reportgen.db" ]] || die "в копии нет файла базы reportgen.db"
    cp "$dir/reportgen.db" "$tmp/restored.db"
    db_verify "$tmp/restored.db"

    local archive
    for archive in "$dir"/*.tar.gz; do
        [[ -e "$archive" ]] || continue
        tar -tzf "$archive" >/dev/null || die "архив повреждён: $archive"
        log "  архив цел: $(basename "$archive") ($(du -h "$archive" | cut -f1))"
    done

    if [[ -f "$dir/SHA256SUMS" ]]; then
        ( cd "$dir" && sha256sum --quiet --check SHA256SUMS ) \
            || die "контрольные суммы не сошлись: $dir"
        log "  контрольные суммы совпали"
    fi

    log "проверка пройдена: копия $dir пригодна к восстановлению"
}

# ---------------------------------------------------------------- ротация ---

rotate() {
    local keep="$1" removed=0
    mapfile -t stale < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' \
                         | sort -r | tail -n "+$((keep + 1))")
    for dir in "${stale[@]:-}"; do
        [[ -n "$dir" ]] || continue
        rm -rf -- "$dir"
        removed=$((removed + 1))
        log "удалена старая копия: $(basename "$dir")"
    done
    log "ротация: оставлено копий $keep, удалено $removed"
}

# ------------------------------------------------------------------ бэкап ---

do_backup() {
    [[ -f "$DB_PATH" ]] || die "база не найдена: $DB_PATH (проверьте REPORTGEN_DATA_DIR)"
    mkdir -p "$BACKUP_DIR" || die "не создать каталог копий: $BACKUP_DIR"

    local stamp target partial
    stamp="$(date '+%Y%m%d-%H%M%S')"
    target="$BACKUP_DIR/$stamp"
    # Два запуска в одну секунду (ручной поверх cron) не должны класть копию
    # внутрь предыдущей — mv в существующий каталог делает именно это.
    if [[ -e "$target" ]]; then target="$target-$$"; fi
    partial="$target.partial"
    rm -rf "$partial"
    mkdir -p "$partial"
    # Незавершённая копия остаётся с суффиксом .partial и не попадает ни в
    # ротацию, ни под руку тому, кто будет восстанавливаться в спешке.
    trap 'rm -rf "$partial"' ERR

    log "бэкап начат: $target"

    log "база: $DB_PATH"
    db_backup "$DB_PATH" "$partial/reportgen.db"
    db_verify "$partial/reportgen.db"

    if [[ -d "$LIBRARY_DIR" ]]; then
        log "библиотека: $LIBRARY_DIR"
        tar -czf "$partial/library.tar.gz" -C "$(dirname "$LIBRARY_DIR")" "$(basename "$LIBRARY_DIR")"
    else
        log "библиотека не найдена ($LIBRARY_DIR) — пропускаем"
    fi

    if [[ -d "$EXPORT_DIR" ]]; then
        log "экспорты: $EXPORT_DIR"
        tar -czf "$partial/exports.tar.gz" -C "$(dirname "$EXPORT_DIR")" "$(basename "$EXPORT_DIR")"
    else
        log "экспорты не найдены ($EXPORT_DIR) — пропускаем"
    fi

    if [[ -d "$TEMPLATES_DIR" ]]; then
        log "шаблоны: $TEMPLATES_DIR"
        tar -czf "$partial/templates.tar.gz" -C "$(dirname "$TEMPLATES_DIR")" "$(basename "$TEMPLATES_DIR")"
    fi

    if [[ "$BACKUP_INCLUDE_ENV" == "1" && -r "$ENV_FILE" ]]; then
        # В файле REPORTGEN_SECRET_KEY: копия становится секретной целиком.
        log "настройки: $ENV_FILE (в копии есть секреты — храните соответственно)"
        install -m 0600 "$ENV_FILE" "$partial/$(basename "$ENV_FILE")"
    fi

    # Манифест: по нему через год понятно, что это за копия и чем восстанавливать.
    {
        echo "установка:   $(hostname)"
        echo "создано:     $(date --iso-8601=seconds)"
        echo "версия:      $(sed -n 's/^__version__ = "\(.*\)"/\1/p' \
                              "$REPO_ROOT/src/reportgen/__init__.py" 2>/dev/null | head -1)"
        echo "база:        $DB_PATH"
        echo "библиотека:  $LIBRARY_DIR"
        echo "экспорты:    $EXPORT_DIR"
        echo "шаблоны:     $TEMPLATES_DIR"
        echo
        echo "восстановление:"
        echo "  systemctl stop reportgen"
        echo "  cp reportgen.db $DB_PATH"
        echo "  rm -f $DB_PATH-wal $DB_PATH-shm"
        echo "  tar -xzf library.tar.gz -C $(dirname "$LIBRARY_DIR")"
        echo "  chown -R reportgen:reportgen $(dirname "$DB_PATH")"
        echo "  systemctl start reportgen"
        echo "  reportgen ingest    # пересобрать индекс из библиотеки"
    } > "$partial/MANIFEST.txt"

    ( cd "$partial" && sha256sum ./* > SHA256SUMS 2>/dev/null || true )

    mv "$partial" "$target"
    trap - ERR
    log "бэкап готов: $target ($(du -sh "$target" | cut -f1))"

    check_backup "$target"
    rotate "$BACKUP_KEEP"
}

# ------------------------------------------------------------------ запуск --

main() {
    case "${1:-}" in
        -h|--help) usage ;;
        --check)
            [[ $# -ge 2 ]] || die "укажите каталог копии: $0 --check /var/backups/reportgen/20250101-031500"
            load_env
            check_backup "$2"
            ;;
        "")
            load_env
            # Два бэкапа одновременно (cron наложился на ручной запуск) — лишняя
            # нагрузка на диск и путаница в ротации.
            exec 9>"${TMPDIR:-/tmp}/reportgen-backup.lock"
            flock -n 9 || die "бэкап уже выполняется"
            do_backup
            ;;
        *) die "неизвестный аргумент: $1 (см. $0 --help)" ;;
    esac
}

main "$@"
