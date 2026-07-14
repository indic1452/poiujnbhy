#!/usr/bin/env bash
set -e

echo "Ожидание базы данных и применение миграций…"
for i in $(seq 1 30); do
  if alembic upgrade head 2>/dev/null; then
    echo "Миграции применены."
    break
  fi
  echo "  БД ещё не готова (попытка $i/30), жду 2с…"
  sleep 2
done

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
