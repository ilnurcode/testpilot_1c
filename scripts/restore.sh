#!/usr/bin/env bash
# ЧЕРНОВИК: не проверялся на реальном дампе. Проверять только на отдельном стеке
# (`-p testpilot-restore`), не на рабочем.
#
# Восстановление БД TestPilot из дампа. Пишет только в БД указанного compose-проекта:
# тома не удаляются, `down -v` не вызывается.
# Формат по расширению: .sql -> psql -v ON_ERROR_STOP=1, .dump/.backup -> pg_restore --clean --if-exists --no-owner.
# Плейн-дамп содержит CREATE TABLE: заливка в мигрированную БД ломается об существующие таблицы,
# поэтому непустая схема требует осознанного --force или чистого проекта.
# Использование: bash scripts/restore.sh --dump PATH [--yes] [--force] [--project NAME]
#   --yes     подтверждение: без него печатается только план (код 1)
#   --force   разрешает заливку в непустую схему/БД
#   --project compose-проект (по умолчанию testpilot или $COMPOSE_PROJECT_NAME)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DUMP=""
CONFIRMED=0
FORCE=0
PROJECT="${COMPOSE_PROJECT_NAME:-testpilot}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="${2:-}"; shift 2 ;;
    --dump=*) DUMP="${1#*=}"; shift ;;
    --yes) CONFIRMED=1; shift ;;
    --force) FORCE=1; shift ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --project=*) PROJECT="${1#*=}"; shift ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$DUMP" ]; then
  echo "Отказ: укажите дамп — bash scripts/restore.sh --dump путь/к/файлу.sql [--yes] [--force] [--project NAME]" >&2
  exit 2
fi
if [ ! -f "$DUMP" ]; then echo "Отказ: файл дампа не найден: $DUMP" >&2; exit 2; fi
SIZE="$(wc -c < "$DUMP" | tr -d ' ')"
if [ "${SIZE:-0}" -le 0 ]; then echo "Отказ: дамп пуст: $DUMP" >&2; exit 2; fi

case "$DUMP" in
  *.sql) METHOD="psql" ;;
  *.dump|*.backup) METHOD="pg_restore" ;;
  *) echo "Отказ: неизвестное расширение (ожидается .sql, .dump или .backup): $DUMP" >&2; exit 2 ;;
esac

dc() { docker compose -p "$PROJECT" "$@"; }
DB_USER="${POSTGRES_USER:-$(dc exec -T db printenv POSTGRES_USER 2>/dev/null | tr -d '\r')}"
DB_NAME="${POSTGRES_DB:-$(dc exec -T db printenv POSTGRES_DB 2>/dev/null | tr -d '\r')}"
DB_USER="${DB_USER:-testpilot}"
DB_NAME="${DB_NAME:-testpilot}"

STATE="$(dc exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -t -A -c "
SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='public')
    || ' ' ||
       CASE WHEN to_regclass('public.impact_projects') IS NULL THEN 0
            ELSE (SELECT count(*) FROM impact_projects) END" 2>/dev/null | tr -d '\r' || true)"
TABLES="${STATE%% *}"
PROJECTS="${STATE##* }"
if ! printf '%s' "${TABLES:-}" | grep -Eq '^[0-9]+$' || ! printf '%s' "${PROJECTS:-}" | grep -Eq '^[0-9]+$'; then
  echo "Отказ: не удалось прочитать БД проекта $PROJECT (стек не запущен или нет миграций)." >&2
  echo "       Для чистого восстановления: docker compose -p testpilot-restore up -d --wait, затем --project testpilot-restore" >&2
  exit 2
fi

echo "План восстановления:"
echo "  дамп: $DUMP ($SIZE байт, способ: $METHOD)"
echo "  проект compose: $PROJECT; база: $DB_USER@db/$DB_NAME"
echo "  текущая схема: $TABLES таблиц в public; проектов: $PROJECTS"
echo "  будут изменены только данные БД; тома, контейнеры и файлы не удаляются"

if [ "$CONFIRMED" -ne 1 ]; then
  echo "Отказ: повторите с --yes, чтобы выполнить восстановление." >&2
  exit 1
fi
if [ "$TABLES" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
  echo "Отказ: схема уже содержит $TABLES таблиц: восстановление плейн-дампа перезапишет/сломает её;" >&2
  echo "       используйте --force осознанно или чистый стек -p testpilot-restore." >&2
  exit 1
fi

set +e
if [ "$METHOD" = "psql" ]; then
  dc exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
else
  dc exec -T db pg_restore --clean --if-exists --no-owner -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
fi
RESTORE_RC=$?
set -e
if [ "$RESTORE_RC" -ne 0 ]; then
  echo "FAIL  восстановление завершилось с кодом $RESTORE_RC" >&2
  exit 1
fi

echo "Ревизия после восстановления: $(dc exec -T intelligence alembic current 2>/dev/null | tr -d '\r' | tail -1)"
echo "Счётчики после восстановления:"
dc exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -t -A \
  -c "SELECT 'impact_projects='||count(*) FROM impact_projects" \
  -c "SELECT 'impact_uploads='||count(*) FROM impact_uploads" \
  -c "SELECT 'ti_ai_suites='||count(*) FROM ti_ai_suites" \
  -c "SELECT 'code_audit='||count(*) FROM code_audit"
echo "Готово. Перезапустите сервисы: docker compose -p $PROJECT up -d --wait"
