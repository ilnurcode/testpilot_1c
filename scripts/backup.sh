#!/usr/bin/env bash
# Дамп БД TestPilot из запущенного compose-стека.
# Пишет только файл дампа в .runtime/backups/ и ничего не удаляет.
# Использование: bash scripts/backup.sh
# Переменные: POSTGRES_USER/POSTGRES_DB (иначе берутся из контейнера db), BACKUP_DIR.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BACKUP_DIR="${BACKUP_DIR:-.runtime/backups}"
mkdir -p "$BACKUP_DIR"

DB_USER="${POSTGRES_USER:-$(docker compose exec -T db printenv POSTGRES_USER 2>/dev/null | tr -d '\r')}"
DB_NAME="${POSTGRES_DB:-$(docker compose exec -T db printenv POSTGRES_DB 2>/dev/null | tr -d '\r')}"
DB_USER="${DB_USER:-testpilot}"
DB_NAME="${DB_NAME:-testpilot}"

STAMP="$(date +%Y%m%d-%H%M%S)"
FILE="$BACKUP_DIR/testpilot-$STAMP.sql"
docker compose exec -T db pg_dump -U "$DB_USER" "$DB_NAME" > "$FILE"

SIZE="$(wc -c < "$FILE" | tr -d ' ')"
if [ "${SIZE:-0}" -le 0 ]; then
  echo "FAIL  дамп пуст: $FILE"
  exit 1
fi
echo "OK  дамп: $FILE ($SIZE байт)"
