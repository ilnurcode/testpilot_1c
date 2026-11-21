#!/usr/bin/env bash
# Проверяемое восстановление в ПУСТУЮ одноразовую БД: sha256 манифеста, counts, hashes evidence, alembic.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DUMP=""; CONFIRMED=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="${2:-}"; shift 2 ;;
    --dump=*) DUMP="${1#*=}"; shift ;;
    --yes) CONFIRMED=1; shift ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DUMP" ] || { echo "Отказ: укажите --dump путь.sql" >&2; exit 2; }
[ "$CONFIRMED" = "1" ] || { echo "Отказ: повторите с --yes" >&2; exit 2; }
: "${TP_BACKUP_PROJECT:?задайте TP_BACKUP_PROJECT}"
: "${TP_BACKUP_COMPOSE_FILE:?задайте TP_BACKUP_COMPOSE_FILE}"
: "${TP_BACKUP_EXPECT_DB:?задайте TP_BACKUP_EXPECT_DB}"
: "${TP_BACKUP_INSTANCE_TOKEN:?задайте TP_BACKUP_INSTANCE_TOKEN}"
: "${TP_BACKUP_DB_USER:?задайте TP_BACKUP_DB_USER}"
PYTHON_BIN="${TP_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PYTHON_BIN="$ROOT/.venv/Scripts/python.exe"; else PYTHON_BIN="python3"; fi
fi
ARGS=(restore --dump "$DUMP" --yes --project "$TP_BACKUP_PROJECT" --compose-file "$TP_BACKUP_COMPOSE_FILE"
      --expect-db "$TP_BACKUP_EXPECT_DB" --instance-token "$TP_BACKUP_INSTANCE_TOKEN" --db-user "$TP_BACKUP_DB_USER")
if [ -n "${TP_BACKUP_ENV_FILE:-}" ]; then ARGS+=(--env-file "$TP_BACKUP_ENV_FILE"); fi
exec "$PYTHON_BIN" "$ROOT/scripts/pg_backup.py" "${ARGS[@]}"
