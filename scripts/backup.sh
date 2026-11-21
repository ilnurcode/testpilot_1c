#!/usr/bin/env bash
# Атомарный backup одноразового стенда: dump + sha256/манифест, без рабочей БД.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${TP_BACKUP_PROJECT:?задайте TP_BACKUP_PROJECT (одноразовый compose project)}"
: "${TP_BACKUP_COMPOSE_FILE:?задайте TP_BACKUP_COMPOSE_FILE}"
: "${TP_BACKUP_EXPECT_DB:?задайте TP_BACKUP_EXPECT_DB}"
: "${TP_BACKUP_INSTANCE_TOKEN:?задайте TP_BACKUP_INSTANCE_TOKEN (маркер владельца БД)}"
: "${TP_BACKUP_DB_USER:?задайте TP_BACKUP_DB_USER}"
PYTHON_BIN="${TP_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PYTHON_BIN="$ROOT/.venv/Scripts/python.exe"; else PYTHON_BIN="python3"; fi
fi
ARGS=(backup --project "$TP_BACKUP_PROJECT" --compose-file "$TP_BACKUP_COMPOSE_FILE"
      --expect-db "$TP_BACKUP_EXPECT_DB" --instance-token "$TP_BACKUP_INSTANCE_TOKEN"
      --db-user "$TP_BACKUP_DB_USER" --dump-dir "${BACKUP_DIR:-$ROOT/.runtime/backups}")
if [ -n "${TP_BACKUP_ENV_FILE:-}" ]; then ARGS+=(--env-file "$TP_BACKUP_ENV_FILE"); fi
if [ "${TP_BACKUP_MANUAL:-0}" = "1" ]; then
  : "${TP_BACKUP_CONFIRM_PROJECT:?ручной backup требует TP_BACKUP_CONFIRM_PROJECT}"
  ARGS+=(--manual --confirm-project "$TP_BACKUP_CONFIRM_PROJECT")
fi
exec "$PYTHON_BIN" "$ROOT/scripts/pg_backup.py" "${ARGS[@]}"
